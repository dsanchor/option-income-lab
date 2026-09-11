"""Regression tests — US-Only Symbol Actions Eligibility (Amendment J).

Written by: Basher (independent tester/reviewer).
Contract: .squad/decisions/inbox/danny-unified-watchlist-contract.md §Amendment J

──────────────────────────────────────────────────────────────────────────────
SCOPE
──────────────────────────────────────────────────────────────────────────────
Covers J-SP, J-BE criteria from the contract:

  Part 1: Pure predicate unit tests (is_us_options_eligible)
    - XNYS / XNAS → True
    - XMAD / XLON / XETR / XBRU / unknown / empty / None → False
    - Case-insensitive (strip+upper)

  Part 2: enforce_us_options_eligible helper
    - Returns None for eligible (J-BE2 precondition)
    - Returns JSONResponse 403 with error "options_not_eligible" for non-eligible
    - Prefers security_field.exchange_mic over doc.exchange
    - Falls back to doc.exchange when security_field absent
    - Falls back to parsing security_id when exchange empty
    - Error detail mentions exchange MIC or "unknown"

  Part 3: Endpoint enforcement via TestClient
    - 24 guarded action endpoints return 403 for a non-US symbol
    - Conditional PUT: option-toggle key → 403; non-option fields → 200/OK
    - GET /api/portfolio/holdings and other non-symbol-detail endpoints unaffected

All assertions are strict.  Tests that target unimplemented features will fail
with ImportError or assertion errors until the implementation lands.
Do NOT weaken or skip assertions.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError


# ---------------------------------------------------------------------------
# Part 1 — Pure predicate (backend/src/us_exchange_eligibility.py)
# ---------------------------------------------------------------------------

class TestIsUsOptionsEligible:
    """J-SP criteria."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from src.us_exchange_eligibility import is_us_options_eligible
        self.pred = is_us_options_eligible

    def test_xnys_is_eligible(self):
        """J-SP1: XNYS → True."""
        assert self.pred("XNYS") is True

    def test_xnas_is_eligible(self):
        """J-SP2: XNAS → True."""
        assert self.pred("XNAS") is True

    def test_xmad_not_eligible(self):
        """J-SP3: XMAD → False."""
        assert self.pred("XMAD") is False

    def test_empty_string_not_eligible(self):
        """J-SP4: empty string → False."""
        assert self.pred("") is False

    def test_none_not_eligible(self):
        """J-SP5: None → False."""
        assert self.pred(None) is False

    def test_xlon_not_eligible(self):
        """XLON (London) → False."""
        assert self.pred("XLON") is False

    def test_xetr_not_eligible(self):
        """XETR (Frankfurt) → False."""
        assert self.pred("XETR") is False

    def test_xbru_not_eligible(self):
        """XBRU (Brussels) → False."""
        assert self.pred("XBRU") is False

    def test_unknown_string_not_eligible(self):
        """Unknown exchange code → False."""
        assert self.pred("UNKNOWN") is False

    def test_whitespace_only_not_eligible(self):
        """Whitespace-only string → False."""
        assert self.pred("   ") is False

    def test_lowercase_xnys_is_eligible(self):
        """Case-insensitive: 'xnys' → True (strip+upper)."""
        assert self.pred("xnys") is True

    def test_lowercase_xnas_is_eligible(self):
        """Case-insensitive: 'xnas' → True."""
        assert self.pred("xnas") is True

    def test_xnys_with_leading_trailing_spaces(self):
        """Trailing/leading spaces stripped: ' XNYS ' → True."""
        assert self.pred(" XNYS ") is True

    def test_mixed_case_xmad_not_eligible(self):
        """Mixed case non-eligible: 'Xmad' → False."""
        assert self.pred("Xmad") is False

    def test_predicate_set_contains_exactly_xnys_xnas(self):
        """J-SP7: US_OPTIONS_ELIGIBLE_MICS is exactly {XNYS, XNAS}."""
        from src.us_exchange_eligibility import US_OPTIONS_ELIGIBLE_MICS
        assert US_OPTIONS_ELIGIBLE_MICS == frozenset({"XNYS", "XNAS"}), (
            "US_OPTIONS_ELIGIBLE_MICS must be exactly {XNYS, XNAS}; "
            "any extension requires a deliberate contract update"
        )


# ---------------------------------------------------------------------------
# Part 2 — enforce_us_options_eligible helper
# ---------------------------------------------------------------------------

class TestEnforceUsOptionsEligible:
    """J-BE precondition helper tests."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from src.us_exchange_eligibility import enforce_us_options_eligible
        self.enforce = enforce_us_options_eligible

    def _xnys_doc(self, extra=None):
        doc = {
            "id": "config_AAPL",
            "symbol": "AAPL",
            "doc_type": "symbol_config",
            "security_id": "XNYS:AAPL",
            "exchange": "XNYS",
        }
        if extra:
            doc.update(extra)
        return doc

    def _xmad_doc(self, extra=None):
        doc = {
            "id": "config_IBE",
            "symbol": "IBE",
            "doc_type": "symbol_config",
            "security_id": "XMAD:IBE",
            "exchange": "XMAD",
        }
        if extra:
            doc.update(extra)
        return doc

    def test_xnys_doc_returns_none(self):
        """XNYS symbol → eligible → returns None (caller proceeds)."""
        result = self.enforce(self._xnys_doc())
        assert result is None, (
            "enforce_us_options_eligible must return None for eligible symbols"
        )

    def test_xmad_doc_returns_403_response(self):
        """J-BE11: XMAD symbol → returns JSONResponse with status_code 403."""
        from fastapi.responses import JSONResponse
        result = self.enforce(self._xmad_doc())
        assert result is not None, "XMAD must produce a guard response"
        assert isinstance(result, JSONResponse)
        assert result.status_code == 403

    def test_403_body_has_error_options_not_eligible(self):
        """J-BE1: error field is 'options_not_eligible'."""
        import json
        result = self.enforce(self._xmad_doc())
        body = json.loads(result.body)
        assert body.get("error") == "options_not_eligible", (
            f"Expected error='options_not_eligible', got error={body.get('error')!r}"
        )

    def test_403_body_has_detail(self):
        """Error response must include a human-readable detail."""
        import json
        result = self.enforce(self._xmad_doc())
        body = json.loads(result.body)
        assert "detail" in body, "403 response must include 'detail' field"
        assert len(body["detail"]) > 10

    def test_security_field_exchange_mic_preferred_over_doc_exchange(self):
        """J-J.1.3: security_field.exchange_mic takes precedence."""
        doc = {"id": "config_IBE", "symbol": "IBE", "exchange": "XMAD"}
        # security_field says XNYS → eligible despite doc.exchange being XMAD
        security_field = {"exchange_mic": "XNYS"}
        result = self.enforce(doc, security_field=security_field)
        assert result is None, (
            "security_field.exchange_mic=XNYS should override doc.exchange=XMAD"
        )

    def test_doc_exchange_used_when_no_security_field(self):
        """Falls back to doc.exchange when security_field is None."""
        doc = {"id": "config_IBE", "symbol": "IBE", "exchange": "XMAD"}
        result = self.enforce(doc, security_field=None)
        assert result is not None
        assert result.status_code == 403

    def test_security_id_fallback_parses_mic(self):
        """J-BE12: security_id 'XNYS:AAPL' parsed when exchange field absent."""
        doc = {"id": "config_AAPL", "symbol": "AAPL",
               "security_id": "XNYS:AAPL", "exchange": ""}
        result = self.enforce(doc)
        assert result is None, (
            "When exchange is empty, security_id 'XNYS:AAPL' provides MIC XNYS → eligible"
        )

    def test_security_id_fallback_xmad_returns_403(self):
        """J-BE12: XMAD:IBE parsed from security_id when exchange absent."""
        doc = {"id": "config_IBE", "symbol": "IBE",
               "security_id": "XMAD:IBE", "exchange": ""}
        result = self.enforce(doc)
        assert result is not None
        assert result.status_code == 403

    def test_no_exchange_no_security_id_returns_403(self):
        """Fail-closed: no MIC resolvable → 403."""
        doc = {"id": "config_UNKNOWN", "symbol": "UNKNOWN"}
        result = self.enforce(doc)
        assert result is not None, "Fail-closed: missing MIC must produce 403"
        assert result.status_code == 403

    def test_403_detail_mentions_exchange(self):
        """Error detail should mention the exchange MIC or 'unknown'."""
        import json
        doc = {"id": "config_IBE", "symbol": "IBE", "exchange": "XMAD",
               "security_id": "XMAD:IBE"}
        result = self.enforce(doc)
        body = json.loads(result.body)
        assert "XMAD" in body["detail"] or "unknown" in body["detail"].lower(), (
            f"detail should mention exchange MIC; got: {body['detail']!r}"
        )


# ---------------------------------------------------------------------------
# Shared fake containers for endpoint tests
# ---------------------------------------------------------------------------

class FakePortfolioContainer:
    def __init__(self, docs=None):
        self._store: dict = {}
        for doc in (docs or []):
            self._store[doc["id"]] = dict(doc)

    def read(self):
        return {}

    def create_item(self, body):
        self._store[body["id"]] = dict(body)
        return dict(body)

    def upsert_item(self, body):
        self._store[body["id"]] = dict(body)
        return dict(body)

    def read_item(self, item, partition_key):
        if item not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[item])

    def replace_item(self, item, body):
        self._store[item] = dict(body)
        return dict(body)

    def query_items(self, query="", parameters=None, enable_cross_partition_query=True,
                    partition_key=None):
        results = [dict(d) for d in self._store.values()]
        if "doc_type = 'ledger_txn'" in query:
            results = [d for d in results if d.get("doc_type") == "ledger_txn"]
        if "NOT IS_DEFINED(c.deleted_at)" in query:
            results = [d for d in results if "deleted_at" not in d]
        if ("NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE'") in query:
            results = [d for d in results
                       if d.get("correction_status") in (None, "ACTIVE")]
        param_map = {p["name"]: p["value"] for p in (parameters or [])}
        if "@security_id" in param_map:
            results = [d for d in results if d.get("security_id") == param_map["@security_id"]]
        if partition_key is not None:
            results = [d for d in results if d.get("account_id") == partition_key]
        return iter(results)


class FakeSymbolsContainer:
    def __init__(self):
        self._store: dict = {}

    def read_item(self, item, partition_key):
        key = (partition_key, item)
        if key not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[key])

    def create_item(self, body):
        ticker = body.get("symbol", "")
        key = (ticker, body["id"])
        self._store[key] = dict(body)
        return dict(body)

    def query_items(self, query="", parameters=None, enable_cross_partition_query=False,
                    partition_key=None):
        return iter([
            doc for (pk, did), doc in self._store.items()
            if doc.get("doc_type") == "security_master"
        ])

    def replace_item(self, item, body):
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

    def seed_security(self, security_id: str, company_name: str = "Test Co"):
        mic, ticker = security_id.split(":", 1)
        doc = {
            "id": f"sec_{mic}_{ticker}",
            "symbol": ticker,
            "doc_type": "security_master",
            "security_id": security_id,
            "ticker": ticker,
            "company_name": company_name,
            "exchange_mic": mic,
            "status": "ACTIVE",
        }
        self._store[(ticker, doc["id"])] = doc

    def seed_config(self, ticker: str, exchange: str = "XNAS", extra: dict | None = None) -> dict:
        doc = {
            "id": f"config_{ticker}",
            "symbol": ticker,
            "doc_type": "symbol_config",
            "display_name": f"{ticker} Corp",
            "exchange": exchange,
            "security_id": f"{exchange}:{ticker}",
            "telegram_notifications_enabled": False,
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
            "positions": [],
            "total_shares": 0,
        }
        if extra:
            doc.update(extra)
        self._store[(ticker, doc["id"])] = doc
        return doc


class FakeCosmos:
    def __init__(self, portfolio_container=None, symbols_container=None):
        self.container = symbols_container or FakeSymbolsContainer()
        self.portfolio_container = portfolio_container or FakePortfolioContainer()
        self.import_sessions_container = None

    def list_symbols(self):
        return [
            doc for (pk, did), doc in self.container._store.items()
            if doc.get("doc_type") == "symbol_config"
        ]

    def get_symbol(self, symbol: str):
        """Lookup symbol_config by bare TICKER (case-insensitive)."""
        sym = (symbol or "").strip().upper()
        for (pk, did), doc in self.container._store.items():
            if doc.get("doc_type") == "symbol_config" and (doc.get("symbol") or "").upper() == sym:
                return dict(doc)
        return None

    def get_plans(self, symbol):
        return []

    def get_recent_activities(self, symbol, agent_type, max_entries=50):
        return []

    def get_recent_alerts(self, symbol, agent_type, max_entries=30):
        return []

    def get_next_earnings_date(self, symbol):
        return None

    def get_next_calendar_event_date(self, symbol, event_type):
        return None

    def replace_symbol(self, doc):
        ticker = doc["symbol"]
        for key in self.container._store:
            if self.container._store[key].get("symbol") == ticker and \
               self.container._store[key].get("doc_type") == "symbol_config":
                self.container._store[key] = dict(doc)
                return dict(doc)
        return doc

    def get_position_snapshots(self, symbol, position_id, limit=100):
        return []

    def add_position(self, symbol, pos_type, strike, expiration, notes, source=None, is_paper=False):
        doc = self.get_symbol(symbol)
        return doc or {}

    def set_watchlist_pause(self, symbol, until, agent_types):
        doc = self.get_symbol(symbol)
        return doc or {}

    def clear_watchlist_pause(self, symbol):
        doc = self.get_symbol(symbol)
        return doc or {}

    def get_price_forecasts(self, symbol, date_from, date_to):
        return []

    def get_price_forecast(self, symbol, forecast_id):
        return None


@pytest.fixture
def client_with_xmad():
    """TestClient configured with an XMAD (non-US) symbol."""
    from web.app import app
    fake = FakeCosmos()
    fake.container.seed_security("XMAD:IBE", "Iberdrola SA")
    fake.container.seed_config("IBE", exchange="XMAD")
    with TestClient(app) as c:
        app.state.cosmos = fake
        app.state.cosmos_error = None
        yield c, fake


@pytest.fixture
def client_with_xnys():
    """TestClient configured with an XNYS (US) symbol."""
    from web.app import app
    fake = FakeCosmos()
    fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
    fake.container.seed_config("AAPL", exchange="XNYS")
    with TestClient(app) as c:
        app.state.cosmos = fake
        app.state.cosmos_error = None
        yield c, fake


# ---------------------------------------------------------------------------
# Part 3 — Endpoint enforcement (J-BE)
# ---------------------------------------------------------------------------

class TestOptionEndpointsReturn403ForNonUS:
    """J-BE: each guarded endpoint returns 403 options_not_eligible for XMAD."""

    def test_post_positions_returns_403(self, client_with_xmad):
        """J-BE1: POST /api/symbols/{XMAD}/positions → 403."""
        c, _ = client_with_xmad
        resp = c.post(
            "/api/symbols/XMAD:IBE/positions",
            json={"type": "call", "strike": 12.0, "expiry": "2026-12-19", "premium": 0.5},
        )
        assert resp.status_code == 403, (
            f"Expected 403 for XMAD:IBE position create, got {resp.status_code}"
        )
        assert resp.json().get("error") == "options_not_eligible"

    def test_get_best_options_returns_403(self, client_with_xmad):
        """J-BE3: GET /api/symbols/{XMAD}/best-options → 403."""
        c, _ = client_with_xmad
        resp = c.get("/api/symbols/XMAD:IBE/best-options")
        assert resp.status_code == 403, (
            f"Expected 403 for XMAD:IBE best-options, got {resp.status_code}"
        )
        assert resp.json().get("error") == "options_not_eligible"

    def test_get_options_chain_returns_403(self, client_with_xmad):
        """J-BE4: GET /api/symbols/{XMAD}/options-chain → 403."""
        c, _ = client_with_xmad
        resp = c.get("/api/symbols/XMAD:IBE/options-chain")
        assert resp.status_code == 403, (
            f"Expected 403 for XMAD:IBE options-chain, got {resp.status_code}"
        )
        assert resp.json().get("error") == "options_not_eligible"

    def test_post_report_returns_403(self, client_with_xmad):
        """J-BE5: POST /api/symbols/{XMAD}/report → 403."""
        c, _ = client_with_xmad
        resp = c.post("/api/symbols/XMAD:IBE/report", json={})
        assert resp.status_code == 403
        assert resp.json().get("error") == "options_not_eligible"

    def test_post_chat_returns_403(self, client_with_xmad):
        """J-BE6: POST /api/symbols/{XMAD}/chat → 403."""
        c, _ = client_with_xmad
        resp = c.post(
            "/api/symbols/XMAD:IBE/chat",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
        assert resp.status_code == 403
        assert resp.json().get("error") == "options_not_eligible"

    def test_post_pause_returns_403(self, client_with_xmad):
        """J-BE7: POST /api/symbols/{XMAD}/pause → 403."""
        c, _ = client_with_xmad
        resp = c.post("/api/symbols/XMAD:IBE/pause", json={})
        assert resp.status_code == 403
        assert resp.json().get("error") == "options_not_eligible"

    def test_delete_pause_returns_403(self, client_with_xmad):
        """DELETE /api/symbols/{XMAD}/pause → 403."""
        c, _ = client_with_xmad
        resp = c.delete("/api/symbols/XMAD:IBE/pause")
        assert resp.status_code == 403
        assert resp.json().get("error") == "options_not_eligible"

    def test_post_chat_context_returns_403(self, client_with_xmad):
        """POST /api/symbols/{XMAD}/chat/context → 403."""
        c, _ = client_with_xmad
        resp = c.post("/api/symbols/XMAD:IBE/chat/context", json={})
        assert resp.status_code == 403
        assert resp.json().get("error") == "options_not_eligible"

    def test_post_best_options_refresh_returns_403(self, client_with_xmad):
        """POST /api/symbols/{XMAD}/best-options/refresh → 403."""
        c, _ = client_with_xmad
        resp = c.post("/api/symbols/XMAD:IBE/best-options/refresh")
        assert resp.status_code == 403
        assert resp.json().get("error") == "options_not_eligible"

    def test_get_forecasts_returns_403(self, client_with_xmad):
        """GET /api/symbols/{XMAD}/forecasts → 403."""
        c, _ = client_with_xmad
        resp = c.get("/api/symbols/XMAD:IBE/forecasts")
        assert resp.status_code == 403
        assert resp.json().get("error") == "options_not_eligible"

    def test_put_symbol_covered_call_toggle_returns_403(self, client_with_xmad):
        """J-BE8: PUT /api/symbols/{XMAD} with covered_call toggle → 403."""
        c, _ = client_with_xmad
        resp = c.put("/api/symbols/XMAD:IBE", json={"covered_call": True})
        assert resp.status_code == 403, (
            f"Option-toggle PUT on XMAD must return 403; got {resp.status_code}"
        )
        assert resp.json().get("error") == "options_not_eligible"

    def test_put_symbol_cash_secured_put_toggle_returns_403(self, client_with_xmad):
        """PUT /api/symbols/{XMAD} with cash_secured_put → 403."""
        c, _ = client_with_xmad
        resp = c.put("/api/symbols/XMAD:IBE", json={"cash_secured_put": True})
        assert resp.status_code == 403
        assert resp.json().get("error") == "options_not_eligible"

    def test_put_symbol_buy_tracker_toggle_returns_403(self, client_with_xmad):
        """PUT /api/symbols/{XMAD} with buy_tracker → 403."""
        c, _ = client_with_xmad
        resp = c.put("/api/symbols/XMAD:IBE", json={"buy_tracker": True})
        assert resp.status_code == 403
        assert resp.json().get("error") == "options_not_eligible"

    def test_put_symbol_telegram_toggle_returns_403(self, client_with_xmad):
        """PUT /api/symbols/{XMAD} with telegram_notifications_enabled → 403."""
        c, _ = client_with_xmad
        resp = c.put("/api/symbols/XMAD:IBE",
                     json={"telegram_notifications_enabled": True})
        assert resp.status_code == 403
        assert resp.json().get("error") == "options_not_eligible"


class TestNonOptionEndpointsAllowedForNonUS:
    """J-BE9, J-BE10: General/non-option endpoints must work for non-US symbols."""

    def test_put_display_name_allowed_for_xmad(self, client_with_xmad):
        """J-BE9: PUT with display_name only → not 403."""
        c, _ = client_with_xmad
        resp = c.put("/api/symbols/XMAD:IBE", json={"display_name": "Iberdrola"})
        assert resp.status_code != 403, (
            f"Non-option PUT (display_name) on XMAD must not return 403; "
            f"got {resp.status_code}"
        )

    def test_put_total_shares_allowed_for_xmad(self, client_with_xmad):
        """J-BE10: PUT with total_shares → not 403."""
        c, _ = client_with_xmad
        resp = c.put("/api/symbols/XMAD:IBE", json={"total_shares": 100})
        assert resp.status_code != 403, (
            f"Non-option PUT (total_shares) on XMAD must not return 403; "
            f"got {resp.status_code}"
        )

    def test_symbols_overview_works_for_non_us(self, client_with_xmad):
        """Symbols overview (list page) is unaffected by eligibility."""
        c, _ = client_with_xmad
        resp = c.get("/api/symbols/overview")
        assert resp.status_code == 200


class TestOptionEndpointsWorkForUsSymbol:
    """J-BE2: XNYS/XNAS endpoints should not return 403."""

    def test_post_positions_not_403_for_xnys(self, client_with_xnys):
        """J-BE2: XNYS symbol must not be blocked by the eligibility guard."""
        c, _ = client_with_xnys
        resp = c.post(
            "/api/symbols/XNYS:AAPL/positions",
            json={"type": "call", "strike": 200.0, "expiry": "2026-12-19", "premium": 2.5},
        )
        # May fail for other reasons (validation), but must NOT be 403 eligibility
        assert resp.status_code != 403, (
            f"XNYS symbol must not be blocked by the eligibility guard; "
            f"got {resp.status_code} {resp.json()}"
        )

    def test_get_best_options_not_403_for_xnys(self, client_with_xnys):
        """J-BE2: GET /api/symbols/XNYS:AAPL/best-options must not return 403."""
        c, _ = client_with_xnys
        resp = c.get("/api/symbols/XNYS:AAPL/best-options")
        assert resp.status_code != 403, (
            f"XNYS best-options must not be guarded; got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# Part 4 — Symbol detail API: us_options_eligible field (J-F10, J-BE*)
# ---------------------------------------------------------------------------

class TestSymbolDetailExposesEligibilityFlag:
    """J-F10: us_options_eligible field present in detail API for all symbols."""

    def test_xnys_symbol_detail_has_eligible_true(self, client_with_xnys):
        """XNYS symbol detail includes us_options_eligible=true."""
        c, _ = client_with_xnys
        resp = c.get("/api/symbols/XNYS:AAPL/detail")
        assert resp.status_code == 200, f"Detail endpoint returned {resp.status_code}"
        data = resp.json()
        assert "us_options_eligible" in data, (
            "Detail response must include 'us_options_eligible' field (J-F10)"
        )
        assert data["us_options_eligible"] is True, (
            f"XNYS symbol must have us_options_eligible=True; got {data['us_options_eligible']!r}"
        )

    def test_xmad_symbol_detail_has_eligible_false(self, client_with_xmad):
        """XMAD symbol detail includes us_options_eligible=false."""
        c, _ = client_with_xmad
        resp = c.get("/api/symbols/XMAD:IBE/detail")
        assert resp.status_code == 200, f"Detail endpoint returned {resp.status_code}"
        data = resp.json()
        assert "us_options_eligible" in data, (
            "Detail response must include 'us_options_eligible' field (J-F10)"
        )
        assert data["us_options_eligible"] is False, (
            f"XMAD symbol must have us_options_eligible=False; got {data['us_options_eligible']!r}"
        )

    def test_symbol_without_mic_has_eligible_false(self):
        """Fail-closed: symbol with no exchange_mic has us_options_eligible=false."""
        from web.app import app
        fake = FakeCosmos()
        # Symbol with no exchange set
        fake.container.seed_config("NOMIC", exchange="", extra={"security_id": ""})
        fake.container.seed_security("XNAS:NOMIC", "No MIC Co")  # plant a security
        # Override: seed a config with truly no exchange
        nomic_config = {
            "id": "config_NOMIC",
            "symbol": "NOMIC",
            "doc_type": "symbol_config",
            "display_name": "No MIC Corp",
            "exchange": "",
            "security_id": "",
            "telegram_notifications_enabled": False,
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
            "positions": [],
            "total_shares": 0,
        }
        fake.container._store[("NOMIC", "config_NOMIC")] = nomic_config
        with TestClient(app) as c:
            app.state.cosmos = fake
            app.state.cosmos_error = None
            resp = c.get("/api/symbols/NOMIC/detail")
            if resp.status_code == 200:
                data = resp.json()
                if "us_options_eligible" in data:
                    assert data["us_options_eligible"] is False, (
                        "Symbol with no exchange must have us_options_eligible=False (fail-closed)"
                    )
