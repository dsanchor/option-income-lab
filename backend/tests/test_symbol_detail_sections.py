from collections import Counter
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def detail_client(monkeypatch):
    from web.app import AGENT_TYPES, app

    calls = Counter()
    securities = {
        "XNAS:MSFT": {"security_id": "XNAS:MSFT", "ticker": "MSFT", "exchange_mic": "XNAS", "company_name": "Microsoft"},
        "XLON:MSFT": {"security_id": "XLON:MSFT", "ticker": "MSFT", "exchange_mic": "XLON", "company_name": "Other asset"},
    }

    class Cosmos:
        container = object()
        portfolio_container = object()
        doc = {
            "symbol": "MSFT", "security_id": "XNAS:MSFT", "exchange": "XNAS",
            "enrichment": {"category": "Growth", "metrics": {"current_price": 100}},
            "positions": [{"position_id": "p1", "type": "call", "strike": 100, "status": "active", "source": {"premium": "2"}}],
        }

        def get_symbol(self, symbol):
            calls["config"] += 1
            return dict(self.doc) if symbol == "MSFT" else None

        def get_plans(self, symbol):
            calls["plans"] += 1
            return [{"id": "plan", "updated_at": "2026-11-08"}]

        def get_recent_activities(self, symbol, agent_type, **kwargs):
            calls["activities"] += 1
            return [{"agent_type": agent_type, "position_id": "p1", "timestamp": "2026-11-08", "assignment_risk": "low"}] if agent_type == "open_call_monitor" else []

        def get_recent_alerts(self, *args, **kwargs):
            calls["alerts"] += 1
            return []

        def get_next_earnings_date(self, symbol):
            calls["earnings"] += 1
            return "2026-12-01"

    class Securities:
        def __init__(self, *args):
            pass

        def get_security(self, identity):
            calls["security"] += 1
            return securities.get(identity)

        def list_securities(self):
            return list(securities.values())

    class Portfolio:
        def __init__(self, *args):
            pass

        def get_movements(self, **kwargs):
            calls["movements"] += 1
            return [], 0

    class Holdings:
        def __init__(self, portfolio_svc, *args):
            calls["holdings_init"] += 1
            self.portfolio_svc = portfolio_svc

        def compute_holdings(self):
            calls["holdings"] += 1
            return {"holdings": [{"security_id": "XNAS:MSFT", "ticker": "MSFT", "total_shares": "12.5"}]}

    def linkage(*args, **kwargs):
        calls["linkage"] += 1
        return {"positions": [{"position_id": "p1", "coverage_status": "linked"}]}

    def timing(*args):
        calls["timing"] += 1
        return None

    monkeypatch.setattr("src.portfolio.cosmos_securities.CosmosSecuritiesService", Securities)
    monkeypatch.setattr("src.portfolio.cosmos_portfolio.CosmosPortfolioService", Portfolio)
    monkeypatch.setattr("src.portfolio.holdings_service.HoldingsService", Holdings)
    monkeypatch.setattr("src.option_timing_store.load_current", timing)
    monkeypatch.setattr("web.app.build_option_position_linkage", linkage)
    monkeypatch.setattr("web.app._holdings_by_account", lambda *args: [])
    monkeypatch.setattr(app.router, "on_startup", [])
    monkeypatch.setattr(app.state, "cosmos", Cosmos(), raising=False)
    return TestClient(app), calls, len(AGENT_TYPES), securities


def test_summary_is_lightweight_and_other_sections_load_independently(detail_client):
    client, calls, agent_count, _ = detail_client
    response = client.get("/api/symbols/XNAS:MSFT/detail?section=summary")
    assert response.status_code == 200
    summary = response.json()
    assert summary["security"]["security_id"] == "XNAS:MSFT"
    assert summary["tradingview_symbol"]
    assert summary["enrichment"]["category"] == "Growth"
    assert summary["summary"]["in_calls"] == 100
    assert summary["portfolio"] is None
    assert summary["symbol_state"] is None
    assert summary["has_symbol_config"] is True
    assert summary["detail_loaded_at"]
    assert all(calls[k] == 0 for k in ("holdings_init", "holdings", "movements", "plans", "activities", "alerts", "linkage"))

    options = client.get("/api/symbols/XNAS:MSFT/detail?section=options").json()
    assert options["positions"][0]["coverage_status"] == "linked"
    assert options["positions"][0]["assignment_risk"] == "low"
    assert calls["activities"] == calls["alerts"] == agent_count
    assert calls["linkage"] == 1
    assert calls["holdings"] == calls["movements"] == calls["plans"] == 0

    stocks = client.get("/api/symbols/XNAS:MSFT/detail?section=stocks").json()
    assert stocks["portfolio"]["current_shares"] == "12.5"
    assert stocks["symbol_state"] == "watchlist_and_portfolio"
    assert calls["holdings"] == 1
    assert calls["activities"] == agent_count
    assert calls["plans"] == 0

    plans = client.get("/api/symbols/XNAS:MSFT/detail?section=plans").json()
    assert plans["plans"][0]["id"] == "plan"
    assert calls["plans"] == 1
    assert calls["holdings"] == 1
    assert calls["activities"] == agent_count
    assert calls["timing"] == 1


def test_summary_refresh_changes_snapshot_and_canonical_collision_is_not_cross_asset(detail_client):
    client, calls, _, _ = detail_client
    first = client.get("/api/symbols/XNAS:MSFT/detail?section=summary").json()
    second = client.get("/api/symbols/XNAS:MSFT/detail?section=summary").json()
    assert first["detail_loaded_at"] != second["detail_loaded_at"]
    other = client.get("/api/symbols/XLON:MSFT/detail?section=summary").json()
    assert other["security"]["security_id"] == "XLON:MSFT"
    assert other["enrichment"] == {}
    assert other["has_symbol_config"] is False
    assert other["us_options_eligible"] is False
    assert calls["holdings"] == calls["activities"] == 0
    other_stocks = client.get("/api/symbols/XLON:MSFT/detail?section=stocks").json()
    assert other_stocks["portfolio"] is None
    client.app.state.cosmos.doc["security_id"] = None
    other = client.get("/api/symbols/XLON:MSFT/detail?section=summary").json()
    assert other["enrichment"] == {}
    assert other["has_symbol_config"] is False
    assert client.get("/api/symbols/MSFT/detail?section=summary").status_code == 300
    assert client.get("/api/symbols/MSFT/detail?section=invalid").status_code == 422


def test_deferred_holdings_failure_is_not_a_fake_empty_portfolio(detail_client, monkeypatch):
    client, _, _, _ = detail_client

    def fail(*args):
        raise RuntimeError("holdings unavailable")

    monkeypatch.setattr("src.portfolio.holdings_service.HoldingsService.compute_holdings", fail)
    assert client.get("/api/symbols/XNAS:MSFT/detail?section=summary").status_code == 200
    response = client.get("/api/symbols/XNAS:MSFT/detail?section=stocks")
    assert response.status_code == 503
    assert "holdings unavailable" in response.json()["error"]


def test_unscoped_detail_keeps_existing_full_contract(detail_client):
    client, calls, agent_count, securities = detail_client
    securities.pop("XLON:MSFT")
    response = client.get("/api/symbols/XNAS:MSFT/detail")
    assert response.status_code == 200
    data = response.json()
    assert all(k in data for k in ("summary", "portfolio", "positions", "activities", "plans", "security", "symbol_state"))
    assert "detail_loaded_at" not in data
    assert "has_symbol_config" not in data
    assert calls["holdings"] == calls["plans"] == calls["linkage"] == calls["timing"] == 1
    assert calls["activities"] == calls["alerts"] == agent_count
