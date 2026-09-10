from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import src.portfolio.cosmos_portfolio as cp
from web.app import app
from web.app import _build_economics_report


def _sample_symbol_docs():
    return [
        {
            "symbol": "AAPL",
            "positions": [
                {
                    "position_id": "aapl-call-1",
                    "type": "call",
                    "strike": 1000,
                    "expiration": "2026-01-31",
                    "opened_at": "2026-01-01T00:00:00Z",
                    "closed_at": "2026-01-20T00:00:00Z",
                    "status": "closed",
                    "source": {"premium": 100},
                },
                {
                    "position_id": "aapl-put-1",
                    "type": "put",
                    "strike": 800,
                    "expiration": "2026-02-14",
                    "opened_at": "2026-01-15T00:00:00Z",
                    "closed_at": "2026-02-01T00:00:00Z",
                    "status": "rolled",
                    "buyback_cost": 30,
                    "source": {"premium": 80},
                },
                {
                    "position_id": "aapl-call-skip",
                    "type": "call",
                    "strike": 900,
                    "expiration": "2026-03-01",
                    "opened_at": "2026-02-01T00:00:00Z",
                    "status": "active",
                    "source": {"premium": None},
                },
            ],
        },
        {
            "symbol": "MSFT",
            "positions": [
                {
                    "position_id": "msft-put-1",
                    "type": "put",
                    "strike": 500,
                    "expiration": "2025-12-31",
                    "opened_at": "2025-12-01T00:00:00Z",
                    "status": "active",
                    "source": {"premium": "50"},
                },
                {
                    "position_id": "msft-call-skip",
                    "type": "call",
                    "strike": 600,
                    "expiration": "2026-02-28",
                    "opened_at": "2026-01-29T00:00:00Z",
                    "status": "closed",
                    "source": {"premium": "N/A"},
                },
                {
                    "position_id": "msft-put-2",
                    "type": "put",
                    "strike": 700,
                    "expiration": "2026-03-03",
                    "opened_at": "2026-02-01T00:00:00Z",
                    "closed_at": "2026-02-20T00:00:00Z",
                    "status": "closed",
                    "buyback_cost": "N/A",
                    "source": {"premium": 70},
                },
            ],
        },
    ]


def test_build_economics_report_aggregates_only_valid_numeric_premiums():
    report = _build_economics_report(
        _sample_symbol_docs(),
        now=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )

    assert report["summary"] == {
        "total_premium": 30000.0,
        "total_buyback": 3000.0,
        "net_income": 27000.0,
        "avg_roc_pct": 9.0,
        "avg_roc_annualized": 109.5,
        "win_rate": 100.0,
        "total_positions": 4,
    }
    assert report["filters"] == {
        "years": [2026, 2025],
        "symbols": ["AAPL", "MSFT"],
    }
    assert [row["label"] for row in report["monthly"]] == [
        "Dec 2025",
        "Jan 2026",
        "Feb 2026",
    ]
    assert report["monthly"][1]["positions_count"] == 2
    assert report["monthly"][1]["calls_count"] == 1
    assert report["monthly"][1]["puts_count"] == 1
    assert report["by_symbol"] == [
        {
            "symbol": "AAPL",
            "premium": 18000.0,
            "buyback": 3000.0,
            "net": 15000.0,
            "positions_count": 2,
            "avg_roc_pct": 8.33,
            "avg_roc_annualized": 101.35,
        },
        {
            "symbol": "MSFT",
            "premium": 12000.0,
            "buyback": 0.0,
            "net": 12000.0,
            "positions_count": 2,
            "avg_roc_pct": 10.0,
            "avg_roc_annualized": 121.67,
        },
    ]
    assert report["by_type"]["calls"]["count"] == 1
    assert report["by_type"]["puts"]["count"] == 3
    assert [position["position_id"] for position in report["positions"]] == [
        "msft-put-2",
        "aapl-put-1",
        "aapl-call-1",
        "msft-put-1",
    ]


def test_build_economics_report_applies_filters():
    report = _build_economics_report(
        _sample_symbol_docs(),
        year=2026,
        symbol_filter="AAPL",
        option_type="put",
        status_filter="rolled",
        now=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )

    assert report["summary"]["total_positions"] == 1
    assert report["summary"]["total_premium"] == 8000.0
    assert report["summary"]["total_buyback"] == 3000.0
    assert report["summary"]["net_income"] == 5000.0
    assert report["summary"]["win_rate"] == 100.0
    assert report["positions"] == [
        {
            "symbol": "AAPL",
            "position_id": "aapl-put-1",
            "type": "put",
            "strike": 800.0,
            "expiration": "2026-02-14",
            "premium": 8000.0,
            "premium_per_share": 80.0,
            "buyback_cost": 3000.0,
            "buyback_per_share": 30.0,
            "net": 5000.0,
            "roc_pct": 6.25,
            "roc_annualized": 76.04,
            "days_held": 17,
            "status": "rolled",
            "opened_at": "2026-01-15T00:00:00Z",
        }
    ]


class _FakeEconomicsCosmos:
    def __init__(self, symbol_docs):
        self._symbol_docs = symbol_docs
        self.portfolio_container = object()

    def get_all_symbols(self):
        return self._symbol_docs


def _sample_dividend_movements():
    return [
        {
            "id": "div-aapl",
            "txn_type": "DIVIDEND",
            "trade_date": "2024-01-15",
            "ticker": "AAPL",
            "security_id": "XNAS:AAPL",
            "account_id": "acct-1",
            "correction_status": "ACTIVE",
            "gross": {"amount": "10", "currency": "USD", "eur_amount": "10"},
            "fees": {"total_eur": "0"},
            "withholding": {"source": {"amount_eur": "1"}},
            "net": {"eur_amount": "9"},
            "source_derechos_amount": "2",
        },
        {
            "id": "div-msft",
            "txn_type": "DIVIDEND",
            "trade_date": "2024-02-15",
            "ticker": "MSFT",
            "security_id": "XNAS:MSFT",
            "account_id": "acct-2",
            "correction_status": "ACTIVE",
            "gross": {"amount": "8", "currency": "USD", "eur_amount": "8"},
            "fees": {"total_eur": "0"},
            "withholding": {},
            "net": {"eur_amount": "8"},
        },
    ]


@pytest.fixture
def economics_client():
    original_cosmos = getattr(app.state, "cosmos", None)
    original_error = getattr(app.state, "cosmos_error", None)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            app.state.cosmos = _FakeEconomicsCosmos(_sample_symbol_docs())
            app.state.cosmos_error = None
            yield client
    finally:
        app.state.cosmos = original_cosmos
        app.state.cosmos_error = original_error


def test_api_dividends_economics_smoke(monkeypatch, economics_client):
    monkeypatch.setattr(
        cp.CosmosPortfolioService,
        "get_all_movements_for_holdings",
        lambda self: _sample_dividend_movements(),
    )

    response = economics_client.get("/api/economics/dividends")

    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {
        "summary",
        "monthly",
        "by_symbol",
        "yearly",
        "cumulative",
        "positions",
        "filters",
    }


def test_api_dividends_economics_exposes_cash_derechos_and_total_fields(monkeypatch, economics_client):
    monkeypatch.setattr(
        cp.CosmosPortfolioService,
        "get_all_movements_for_holdings",
        lambda self: _sample_dividend_movements(),
    )

    response = economics_client.get("/api/economics/dividends")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_net_eur"] == 17.0
    assert body["summary"]["cash_net"] == 17.0
    assert body["summary"]["derechos_net"] == 2.0
    assert body["summary"]["total_net"] == 19.0
    assert body["monthly"] == [
        {
            "month": "2024-01",
            "gross_eur": 10.0,
            "fees_eur": 0.0,
            "withholding_source_eur": 1.0,
            "withholding_destination_eur": 0.0,
            "withholding_total_eur": 1.0,
            "net_eur": 9.0,
            "cash_net": 9.0,
            "derechos_net": 2.0,
            "total_net": 11.0,
            "dividend_count": 1,
        },
        {
            "month": "2024-02",
            "gross_eur": 8.0,
            "fees_eur": 0.0,
            "withholding_source_eur": 0.0,
            "withholding_destination_eur": 0.0,
            "withholding_total_eur": 0.0,
            "net_eur": 8.0,
            "cash_net": 8.0,
            "derechos_net": 0.0,
            "total_net": 8.0,
            "dividend_count": 1,
        },
    ]
    assert body["yearly"] == [
        {
            "year": 2024,
            "gross_eur": 18.0,
            "withholding_eur": 1.0,
            "net_eur": 17.0,
            "cash_net": 17.0,
            "derechos_net": 2.0,
            "total_net": 19.0,
            "dividend_count": 2,
        }
    ]
    assert body["by_symbol"] == [
        {
            "symbol": "AAPL",
            "gross_eur": 10.0,
            "withholding_total_eur": 1.0,
            "net_eur": 9.0,
            "cash_net": 9.0,
            "derechos_net": 2.0,
            "total_net": 11.0,
            "dividend_count": 1,
        },
        {
            "symbol": "MSFT",
            "gross_eur": 8.0,
            "withholding_total_eur": 0.0,
            "net_eur": 8.0,
            "cash_net": 8.0,
            "derechos_net": 0.0,
            "total_net": 8.0,
            "dividend_count": 1,
        },
    ]
    assert body["cumulative"] == [
        {
            "month": "2024-01",
            "cumulative_net_eur": 9.0,
            "cumulative_cash_net_eur": 9.0,
            "cumulative_derechos_net_eur": 2.0,
            "cumulative_total_net_eur": 11.0,
            "cash_net": 9.0,
            "derechos_net": 2.0,
            "total_net": 11.0,
        },
        {
            "month": "2024-02",
            "cumulative_net_eur": 17.0,
            "cumulative_cash_net_eur": 17.0,
            "cumulative_derechos_net_eur": 2.0,
            "cumulative_total_net_eur": 19.0,
            "cash_net": 17.0,
            "derechos_net": 2.0,
            "total_net": 19.0,
        },
    ]
    positions = {position["id"]: position for position in body["positions"]}
    assert positions["div-aapl"]["net_eur"] == 9.0
    assert positions["div-aapl"]["cash_net"] == 9.0
    assert positions["div-aapl"]["derechos_eur"] == 2.0
    assert positions["div-aapl"]["derechos_net"] == 2.0
    assert positions["div-aapl"]["total_net"] == 11.0


def test_api_economics_overview_smoke(monkeypatch, economics_client):
    monkeypatch.setattr(
        cp.CosmosPortfolioService,
        "get_all_movements_for_holdings",
        lambda self: _sample_dividend_movements(),
    )

    response = economics_client.get("/api/economics/overview")

    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {
        "summary",
        "monthly",
        "by_symbol",
        "filters",
        "applied_filters",
        "meta",
    }


def test_api_economics_overview_exposes_dividend_breakdown_fields(monkeypatch, economics_client):
    monkeypatch.setattr(
        cp.CosmosPortfolioService,
        "get_all_movements_for_holdings",
        lambda self: _sample_dividend_movements(),
    )

    response = economics_client.get("/api/economics/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["dividends_net_eur"] == 17.0
    assert body["summary"]["dividends_cash_net_eur"] == 17.0
    assert body["summary"]["dividends_derechos_net_eur"] == 2.0
    assert body["summary"]["dividends_total_net_eur"] == 19.0
    assert body["summary"]["cash_net"] == 17.0
    assert body["summary"]["derechos_net"] == 2.0
    assert body["summary"]["total_net"] == 19.0

    january = next(row for row in body["monthly"] if row["month"] == "2024-01")
    assert january["dividends_net_eur"] == 9.0
    assert january["dividends_cash_net_eur"] == 9.0
    assert january["dividends_derechos_net_eur"] == 2.0
    assert january["dividends_total_net_eur"] == 11.0
    assert january["cash_net"] == 9.0
    assert january["derechos_net"] == 2.0
    assert january["total_net"] == 11.0

    aapl = next(row for row in body["by_symbol"] if row["symbol"] == "AAPL")
    assert aapl["dividends_net_eur"] == 9.0
    assert aapl["dividends_cash_net_eur"] == 9.0
    assert aapl["dividends_derechos_net_eur"] == 2.0
    assert aapl["dividends_total_net_eur"] == 11.0
    assert aapl["cash_net"] == 9.0
    assert aapl["derechos_net"] == 2.0
    assert aapl["total_net"] == 11.0
    assert body["meta"]["combined_total_available"] is True
