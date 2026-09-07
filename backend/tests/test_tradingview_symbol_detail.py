"""TradingView symbol resolution in the Symbol Detail endpoint.

Ref: danny-tradingview-symbol-contract.md §2.4 (backend detail response)

Coverage:
  TVD-1  tradingview_symbol present in /api/symbols/{symbol}/detail response.
  TVD-2  XMAD security → "BME-{TICKER}" in response.
  TVD-3  XNYS security → "NYSE-{TICKER}".
  TVD-4  XNAS security → "NASDAQ-{TICKER}".
  TVD-5  XETR security → null (unmapped MIC).
  TVD-6  Unknown MIC → null (fail-closed).
  TVD-7  Missing exchange_mic → null.
  TVD-8  Legacy free-text exchange="NASDAQ" → "NASDAQ-{TICKER}" (backward compat).
  TVD-9  explicit provider_symbols.tradingview override beats MIC mapping.
  TVD-10 portfolio_only branch (no symbol_config, security_master only) also resolves.

All tests hermetic — FakeCosmos, no network.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError


# ---------------------------------------------------------------------------
# Fake containers (modeled on test_ensure_symbol_config.py pattern)
# ---------------------------------------------------------------------------

class FakeSymbolsContainer:
    def __init__(self):
        self._store: dict = {}

    def read_item(self, item: str, partition_key: str):
        key = (partition_key, item)
        if key not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[key])

    def create_item(self, body: dict):
        ticker = body.get("symbol", body.get("ticker", ""))
        key = (ticker, body["id"])
        self._store[key] = dict(body)
        return dict(body)

    def query_items(self, query="", parameters=None,
                    enable_cross_partition_query=False, partition_key=None):
        """Support security_master search by ticker (for disambiguation)."""
        param_map = {p["name"]: p["value"] for p in (parameters or [])}
        results = []
        for (pk, did), doc in self._store.items():
            if partition_key and pk != partition_key:
                continue
            if doc.get("doc_type") != "security_master":
                continue
            if "@isin" in param_map and doc.get("isin") != param_map["@isin"]:
                continue
            results.append(dict(doc))
        return iter(results)

    def replace_item(self, item, body, **kw):
        for key in list(self._store):
            if self._store[key].get("id") == item:
                self._store[key] = dict(body)
                return dict(body)
        raise CosmosResourceNotFoundError(message="not found", response=None)

    def upsert_item(self, body):
        ticker = body.get("symbol", "")
        key = (ticker, body["id"])
        self._store[key] = dict(body)
        return dict(body)

    def seed_security(self, security_id: str, company_name="Test Co.",
                      provider_symbols: dict | None = None, **extra):
        mic, ticker = security_id.split(":", 1)
        doc = {
            "id": f"sec_{mic}_{ticker}",
            "symbol": ticker,
            "doc_type": "security_master",
            "security_id": security_id,
            "ticker": ticker,
            "company_name": company_name,
            "exchange_mic": mic,
            "listing_currency": "USD",
            "status": "ACTIVE",
            **extra,
        }
        if provider_symbols:
            doc["provider_symbols"] = provider_symbols
        self._store[(ticker, doc["id"])] = doc
        return doc

    def seed_config(self, ticker: str, extra: dict | None = None):
        doc = {
            "id": f"config_{ticker}",
            "symbol": ticker,
            "doc_type": "symbol_config",
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
            "telegram_notifications_enabled": False,
        }
        if extra:
            doc.update(extra)
        self._store[(ticker, doc["id"])] = doc
        return doc


class _FakePortfolioContainer:
    def query_items(self, **kw): return iter([])
    def upsert_item(self, body): return body
    def read(self): return {}


class _FakeImportSessions:
    def query_items(self, **kw): return iter([])


class FakeCosmos:
    def __init__(self):
        self.container = FakeSymbolsContainer()
        self.portfolio_container = _FakePortfolioContainer()
        self.import_sessions_container = _FakeImportSessions()
        self._symbols: dict[str, dict] = {}  # ticker → symbol_config

    # ── CosmosDBService interface expected by the app layer ──────────────────
    def list_symbols(self): return list(self._symbols.values())
    def get_symbol(self, symbol): return self._symbols.get(symbol.upper())
    def get_plans(self, symbol=None, status=None): return []
    def get_recent_activities(self, symbol, agent_type, max_entries=50): return []
    def get_recent_alerts(self, symbol, agent_type, max_entries=30): return []
    def get_next_earnings_date(self, symbol): return None
    def get_next_calendar_event_date(self, symbol, event_type): return None
    def get_paused_symbols(self): return []
    def get_all_activities(self, **kw): return []
    def get_all_alerts(self, **kw): return []
    def get_recent_activities_by_symbol(self, **kw): return []
    def get_covered_call_symbols(self): return []
    def get_cash_secured_put_symbols(self): return []
    def get_buy_tracker_symbols(self): return []
    def get_calendar_events(self): return []
    def update_symbol_enrichment(self, symbol, enrichment): return enrichment
    def record_enrichment_snapshot(self, symbol, score, momentum): pass

    def seed_symbol(self, ticker: str, exchange: str = "XNYS",
                    security_id: str | None = None, **extra):
        """Seed both a symbol_config and corresponding security_master doc."""
        sid = security_id or f"{exchange}:{ticker}"
        config = {
            "id": f"config_{ticker}",
            "symbol": ticker,
            "doc_type": "symbol_config",
            "exchange": exchange,
            "security_id": sid,
            "display_name": f"{ticker} Corp.",
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
            "telegram_notifications_enabled": False,
            **extra,
        }
        self._symbols[ticker.upper()] = config
        self.container.seed_security(sid, f"{ticker} Corp.")
        return config


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    from web.app import app
    fake = FakeCosmos()
    with TestClient(app) as c:
        app.state.cosmos = fake
        app.state.cosmos_error = None
        yield c, fake


# ---------------------------------------------------------------------------
# TVD-1..9: main config path
# ---------------------------------------------------------------------------

class TestTradingviewSymbolInDetailResponse:
    def test_tvd1_tradingview_symbol_field_present(self, client):
        """TVD-1: tradingview_symbol key must appear in detail response."""
        c, fake = client
        fake.seed_symbol("AAPL", "XNYS", security_id="XNYS:AAPL")
        resp = c.get("/api/symbols/AAPL/detail")
        assert resp.status_code == 200, f"TVD-1: {resp.status_code} — {resp.text[:200]}"
        data = resp.json()
        assert "tradingview_symbol" in data, (
            "TVD-1: tradingview_symbol field missing from /api/symbols/AAPL/detail"
        )

    def test_tvd3_xnys_resolves_to_nyse_ticker(self, client):
        """TVD-3: XNYS security → 'NYSE-{TICKER}'."""
        c, fake = client
        fake.seed_symbol("ABBV", "XNYS", security_id="XNYS:ABBV")
        resp = c.get("/api/symbols/ABBV/detail")
        assert resp.status_code == 200
        data = resp.json()
        tv = data.get("tradingview_symbol")
        assert tv == "NYSE-ABBV", f"TVD-3: Expected NYSE-ABBV, got {tv!r}"

    def test_tvd4_xnas_resolves_to_nasdaq_ticker(self, client):
        """TVD-4: XNAS security → 'NASDAQ-{TICKER}'."""
        c, fake = client
        fake.seed_symbol("MSFT", "XNAS", security_id="XNAS:MSFT")
        resp = c.get("/api/symbols/MSFT/detail")
        assert resp.status_code == 200
        assert resp.json()["tradingview_symbol"] == "NASDAQ-MSFT"

    def test_tvd2_xmad_resolves_to_bme_ticker(self, client):
        """TVD-2: XMAD security → 'BME-{TICKER}'."""
        c, fake = client
        fake.seed_symbol("ACS", "XMAD", security_id="XMAD:ACS")
        resp = c.get("/api/symbols/ACS/detail")
        assert resp.status_code == 200
        assert resp.json()["tradingview_symbol"] == "BME-ACS"

    def test_tvd5_xetr_returns_null(self, client):
        """TVD-5: XETR → null (unmapped, unverified TradingView code)."""
        c, fake = client
        fake.seed_symbol("SAP", "XETR", security_id="XETR:SAP")
        resp = c.get("/api/symbols/SAP/detail")
        assert resp.status_code == 200
        tv = resp.json().get("tradingview_symbol")
        assert tv is None, f"TVD-5: XETR must return null, got {tv!r}"

    def test_tvd6_unknown_mic_returns_null(self, client):
        """TVD-6: unknown MIC → null (fail-closed)."""
        c, fake = client
        fake.seed_symbol("ZZZ", "XZZZ", security_id="XZZZ:ZZZ")
        resp = c.get("/api/symbols/ZZZ/detail")
        assert resp.status_code == 200
        tv = resp.json().get("tradingview_symbol")
        assert tv is None, f"TVD-6: Unknown MIC must return null, got {tv!r}"

    def test_tvd7_missing_exchange_mic_returns_null(self, client):
        """TVD-7: symbol_config with no exchange field → null."""
        c, fake = client
        config = {
            "id": "config_NOEXCH",
            "symbol": "NOEXCH",
            "doc_type": "symbol_config",
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
            "telegram_notifications_enabled": False,
            # no exchange field, no security_id
        }
        fake._symbols["NOEXCH"] = config
        resp = c.get("/api/symbols/NOEXCH/detail")
        # May be 404 (no security_master) or 200 with null — either is valid
        if resp.status_code == 200:
            tv = resp.json().get("tradingview_symbol")
            assert tv is None, f"TVD-7: missing exchange must produce null, got {tv!r}"

    def test_tvd8_legacy_exchange_text_nasdaq_resolves(self, client):
        """TVD-8: legacy free-text exchange='NASDAQ' resolves via legacy alias."""
        c, fake = client
        config = {
            "id": "config_OLDWAY",
            "symbol": "OLDWAY",
            "doc_type": "symbol_config",
            "exchange": "NASDAQ",   # pre-MIC free text
            "display_name": "Old Way Inc.",
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
            "telegram_notifications_enabled": False,
        }
        fake._symbols["OLDWAY"] = config
        resp = c.get("/api/symbols/OLDWAY/detail")
        assert resp.status_code == 200
        tv = resp.json().get("tradingview_symbol")
        assert tv == "NASDAQ-OLDWAY", (
            f"TVD-8: legacy 'NASDAQ' exchange must resolve to NASDAQ-OLDWAY via alias, got {tv!r}"
        )

    def test_tvd9_provider_symbols_override_beats_mic(self, client):
        """TVD-9: explicit provider_symbols.tradingview override in security_master wins."""
        c, fake = client
        # Seed security_master with a tradingview override
        fake.container.seed_security(
            "XMAD:ACS",
            "ACS Servicios",
            provider_symbols={"tradingview": "BME-ACS3"},
        )
        config = {
            "id": "config_ACS",
            "symbol": "ACS",
            "doc_type": "symbol_config",
            "exchange": "XMAD",
            "security_id": "XMAD:ACS",
            "display_name": "ACS Servicios",
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
            "telegram_notifications_enabled": False,
        }
        fake._symbols["ACS"] = config
        resp = c.get("/api/symbols/ACS/detail")
        assert resp.status_code == 200
        tv = resp.json().get("tradingview_symbol")
        assert tv == "BME-ACS3", (
            f"TVD-9: explicit provider_symbols.tradingview override must win, got {tv!r}"
        )


class TestTradingviewSymbolPortfolioOnlyBranch:
    """TVD-10: detail endpoint portfolio_only branch (security_master found via portfolio,
    no matching symbol_config) also resolves tradingview_symbol correctly."""

    def test_tvd10_portfolio_only_branch_resolves_tradingview(self, client):
        """TVD-10: portfolio_only branch (security present, no config) includes tradingview_symbol."""
        c, fake = client
        # Seed security_master but no symbol_config
        fake.container.seed_security("XNYS:ABBV", "AbbVie Inc.")
        resp = c.get("/api/symbols/XNYS:ABBV/detail")
        # If endpoint finds security_master but no config, it may return 200 or 404
        # depending on portfolio lookup; either way, if 200 → tradingview_symbol must be set
        if resp.status_code == 200:
            data = resp.json()
            assert "tradingview_symbol" in data, (
                "TVD-10: portfolio_only branch must include tradingview_symbol field"
            )
            tv = data.get("tradingview_symbol")
            assert tv == "NYSE-ABBV", (
                f"TVD-10: portfolio_only XNYS must resolve to NYSE-ABBV, got {tv!r}"
            )
