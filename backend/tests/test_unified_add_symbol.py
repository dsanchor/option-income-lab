"""Tests for unified Add Symbol endpoint — Symbol Unification rev 3.

Contract: Danny's §6 — Unified 'Add Symbol' Flow.

Coverage:
- Select existing security with no config → creates disabled config → 200
- Select existing security with existing config → no-op on config → 200
- Selecting existing security never resets existing agent flags
- Create new security → creates SecurityMaster + disabled config → 201
- ISIN collision on create → 409 with existing security detail
- MIC:TICKER collision on create → 409
- Missing both security_id and create → 400 validation error
- config_warning field present on partial failure
- config_created / config_existed flags in response
- navigate_to field is /symbols/{security_id}
- GET /api/securities/search returns candidates with has_config flag
- search returns no results for non-matching query

Uses FastAPI TestClient with fake Cosmos state.
"""

from __future__ import annotations

import pytest
from azure.cosmos.exceptions import CosmosResourceNotFoundError, CosmosHttpResponseError
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fake containers (same pattern as test_portfolio_endpoints)
# ---------------------------------------------------------------------------

class FakePortfolioContainer:
    def __init__(self):
        self._store: dict = {}

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
        return iter([])


class FakeSymbolsContainer:
    def __init__(self):
        self._store: dict = {}

    def read_item(self, item, partition_key):
        key = (partition_key, item)
        if key not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[key])

    def create_item(self, body):
        ticker = body["symbol"]
        key = (ticker, body["id"])
        if key in self._store:
            raise CosmosHttpResponseError(status_code=409, message="Conflict")
        self._store[key] = dict(body)
        return dict(body)

    def query_items(self, query="", parameters=None, enable_cross_partition_query=False,
                    partition_key=None):
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

    def replace_item(self, item, body):
        for key in self._store:
            if self._store[key].get("id") == item:
                self._store[key] = dict(body)
                return dict(body)
        raise CosmosResourceNotFoundError(message="not found", response=None)

    def upsert_item(self, body):
        ticker = body.get("symbol", "")
        key = (ticker, body["id"])
        self._store[key] = dict(body)
        return dict(body)

    def seed_security(self, security_id: str, company_name: str = "Test Co",
                      isin: str = None):
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
        }
        if isin:
            doc["isin"] = isin
        self._store[(ticker, doc["id"])] = doc

    def seed_config(self, ticker: str, extra: dict | None = None) -> dict:
        doc = {
            "id": f"config_{ticker}",
            "symbol": ticker,
            "doc_type": "symbol_config",
            "display_name": f"{ticker} Corp",
            "telegram_notifications_enabled": False,
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
            "positions": [],
        }
        if extra:
            doc.update(extra)
        self._store[(ticker, doc["id"])] = doc
        return doc

    def get_config(self, ticker: str):
        return self._store.get((ticker, f"config_{ticker}"))

    def count_configs(self):
        return sum(1 for doc in self._store.values() if doc.get("doc_type") == "symbol_config")


class FakeCosmos:
    def __init__(self):
        self.container = FakeSymbolsContainer()
        self.portfolio_container = FakePortfolioContainer()
        self.import_sessions_container = None
        self.enrichment_calls: list = []
        self.snapshot_calls: list = []

    def list_symbols(self):
        return []

    def get_symbol(self, symbol):
        return None

    def update_symbol_enrichment(self, symbol, enrichment):
        self.enrichment_calls.append((symbol, enrichment))
        return enrichment

    def record_enrichment_snapshot(self, symbol, tech_timing, momentum):
        self.snapshot_calls.append((symbol, tech_timing, momentum))
        return None


class _NoOpThread:
    """Thread stub — never actually runs the target (no network, no I/O).

    ``warmup_started`` is determined synchronously (before ``start()`` is
    called) by ``_start_symbol_warmup``'s ``resolve_yfinance_symbol`` check,
    so stubbing thread execution away does not affect that assertion — it
    only prevents the *body* of the background enrichment/backfill work
    (which would otherwise hit real yfinance/network) from ever running in
    tests that don't specifically want to exercise it.
    """
    def __init__(self, target=None, daemon=None):
        self.target = target
        self.daemon = daemon

    def start(self):
        pass


@pytest.fixture
def client(monkeypatch):
    from web.app import app
    import web.portfolio_routes as portfolio_routes

    # Default: warm-up threads are scheduled (so `warmup_started` reflects
    # real resolution outcomes) but never actually executed — keeps this
    # suite hermetic (no network) unless a test opts into running them.
    monkeypatch.setattr(portfolio_routes.threading, "Thread", _NoOpThread)

    fake_cosmos = FakeCosmos()
    with TestClient(app) as c:
        app.state.cosmos = fake_cosmos
        app.state.cosmos_error = None
        app.state.yf_provider = None
        yield c, fake_cosmos


# ---------------------------------------------------------------------------
# §6.2 — POST /api/symbols/add — select existing security
# ---------------------------------------------------------------------------

class TestAddSymbolSelectExisting:
    def test_select_existing_no_config_returns_200(self, client):
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")

        resp = c.post("/api/symbols/add", json={"security_id": "XNYS:AAPL"})
        assert resp.status_code == 200

    def test_select_existing_creates_disabled_config(self, client):
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")

        c.post("/api/symbols/add", json={"security_id": "XNYS:AAPL"})

        config = fake.container.get_config("AAPL")
        assert config is not None, "Config must be created after selecting existing security"
        assert config.get("telegram_notifications_enabled") is False
        wl = config.get("watchlist", {})
        assert wl.get("covered_call") is False
        assert wl.get("cash_secured_put") is False
        assert wl.get("buy_tracker") is False
        assert config.get("positions") == []

    def test_select_existing_config_existed_is_true_when_already_present(self, client):
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        fake.container.seed_config("AAPL", {"telegram_notifications_enabled": True})

        resp = c.post("/api/symbols/add", json={"security_id": "XNYS:AAPL"})
        data = resp.json()
        assert data.get("config_existed") is True, (
            "config_existed must be True when an existing config was found"
        )
        assert data.get("config_created") is False

    def test_select_existing_does_not_reset_agent_flags(self, client):
        """AC-8: selecting an existing security with existing config NEVER modifies the config."""
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        original_config = fake.container.seed_config("AAPL", {
            "telegram_notifications_enabled": True,
            "watchlist": {"covered_call": True, "cash_secured_put": True, "buy_tracker": True},
            "total_shares": 500,
            "custom_preserved_field": "must_remain",
        })

        c.post("/api/symbols/add", json={"security_id": "XNYS:AAPL"})

        config = fake.container.get_config("AAPL")
        assert config["telegram_notifications_enabled"] is True, (
            "AC-8: existing config telegram flag must not be reset"
        )
        assert config["watchlist"]["covered_call"] is True, (
            "AC-8: existing config watchlist flags must not be reset"
        )
        assert config["total_shares"] == 500
        assert config.get("custom_preserved_field") == "must_remain"

    def test_select_existing_navigate_to_field(self, client):
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")

        resp = c.post("/api/symbols/add", json={"security_id": "XNYS:AAPL"})
        data = resp.json()
        assert data.get("navigate_to") == "/symbols/XNYS:AAPL"

    def test_select_nonexistent_security_returns_404(self, client):
        c, fake = client
        resp = c.post("/api/symbols/add", json={"security_id": "XNYS:GHOST"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# §6.2 — POST /api/symbols/add — create new security
# ---------------------------------------------------------------------------

class TestAddSymbolCreateNew:
    def _create_payload(self, ticker="NEWCO", mic="XNYS", isin=None):
        data = {
            "create": {
                "ticker": ticker,
                "exchange_mic": mic,
                "company_name": f"{ticker} Corp",
                "listing_currency": "USD",
            }
        }
        if isin:
            data["create"]["isin"] = isin
        return data

    def test_create_new_returns_201(self, client):
        c, fake = client
        resp = c.post("/api/symbols/add", json=self._create_payload("NEWCO", "XNYS"))
        assert resp.status_code == 201

    def test_create_new_creates_security_master(self, client):
        c, fake = client
        c.post("/api/symbols/add", json=self._create_payload("NEWCO", "XNYS"))

        # Security must exist
        created_sec = next(
            (doc for (pk, did), doc in fake.container._store.items()
             if doc.get("doc_type") == "security_master" and doc.get("ticker") == "NEWCO"),
            None,
        )
        assert created_sec is not None, "SecurityMaster must be created"
        assert created_sec["security_id"] == "XNYS:NEWCO"

    def test_create_new_creates_disabled_config(self, client):
        c, fake = client
        c.post("/api/symbols/add", json=self._create_payload("NEWCO", "XNYS"))

        config = fake.container.get_config("NEWCO")
        assert config is not None, "Symbol config must be created with new security"
        assert config.get("telegram_notifications_enabled") is False
        wl = config.get("watchlist", {})
        assert wl.get("covered_call") is False
        assert wl.get("cash_secured_put") is False
        assert wl.get("buy_tracker") is False

    def test_create_new_config_created_is_true(self, client):
        c, fake = client
        resp = c.post("/api/symbols/add", json=self._create_payload("NEWCO", "XNYS"))
        data = resp.json()
        assert data.get("config_created") is True
        assert data.get("config_existed") is False

    def test_create_new_navigate_to_correct(self, client):
        c, fake = client
        resp = c.post("/api/symbols/add", json=self._create_payload("NEWCO", "XNYS"))
        data = resp.json()
        assert data.get("navigate_to") == "/symbols/XNYS:NEWCO"

    def test_create_new_isin_collision_returns_409(self, client):
        """AC-8b: ISIN collision on create → 409 with existing security details."""
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.", isin="US0378331005")

        resp = c.post("/api/symbols/add", json={
            "create": {
                "ticker": "AAPL2",
                "exchange_mic": "XNAS",
                "company_name": "Apple Inc. (duplicate)",
                "isin": "US0378331005",  # Same ISIN
            }
        })
        assert resp.status_code == 409

    def test_create_new_mic_ticker_collision_returns_409(self, client):
        """AC-8b: MIC:TICKER collision → 409."""
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")

        resp = c.post("/api/symbols/add", json=self._create_payload("AAPL", "XNYS"))
        assert resp.status_code == 409

    def test_collision_response_includes_existing_security(self, client):
        """409 collision response must carry both keys per final Livingston contract.

        Final contract (2026-09-06): both 'existing' and 'existing_security' are
        present and identical.  Frontend reads 'existing_security'.
        AddSymbolForm.tsx also reads e.data?.error (line ~112).
        SecurityCreateForm.tsx reads e.data?.detail (line ~66).
        """
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")

        resp = c.post("/api/symbols/add", json=self._create_payload("AAPL", "XNYS"))
        assert resp.status_code == 409
        data = resp.json()
        assert "error" in data, "409 response must include 'error' key"
        assert "detail" in data, "409 response must include 'detail' key"
        # Final contract: both 'existing' (compat) and 'existing_security' (Rusty primary)
        assert "existing_security" in data, (
            "409 collision response must include 'existing_security' key per final contract"
        )
        assert data["existing_security"].get("security_id") is not None, (
            "existing_security must include security_id of the colliding entry"
        )

    def test_same_ticker_different_mic_allowed(self, client):
        """Ticker collision on different MIC is not a collision — allowed."""
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")

        resp = c.post("/api/symbols/add", json={
            "create": {
                "ticker": "AAPL",
                "exchange_mic": "XNAS",
                "company_name": "Apple Inc.",
                "isin": "US0378331099",  # Different ISIN
            }
        })
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# §6.2 — Validation errors
# ---------------------------------------------------------------------------

class TestAddSymbolValidation:
    def test_missing_both_returns_400(self, client):
        c, fake = client
        resp = c.post("/api/symbols/add", json={})
        assert resp.status_code == 400

    def test_config_warning_on_ensure_failure(self, client):
        """If ensure_symbol_config fails, response includes config_warning; still 200."""
        from unittest.mock import patch
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")

        # Patch ensure at the portfolio_routes module boundary (where it is bound)
        with patch("web.portfolio_routes.ensure_symbol_config",
                   side_effect=Exception("Cosmos timeout")):
            resp = c.post("/api/symbols/add", json={"security_id": "XNYS:AAPL"})

        # Must succeed (security exists); config_warning non-null
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert data.get("config_warning") is not None


# ---------------------------------------------------------------------------
# §6.3 — GET /api/securities/search
# ---------------------------------------------------------------------------

class TestSecuritiesSearchEndpoint:
    """GET /api/securities/search — §6 securities catalog search.

    The search route is declared at line ~169 in portfolio_routes.py, explicitly
    before the /api/securities/{security_id:path} catch-all, so all queries to
    /api/securities/search are matched correctly.
    """

    def test_search_returns_200(self, client):
        c, fake = client
        resp = c.get("/api/securities/search?q=apple")
        assert resp.status_code == 200, f"Search endpoint returned {resp.status_code}"

    def test_search_returns_candidates_list(self, client):
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")

        resp = c.get("/api/securities/search?q=apple")
        assert resp.status_code == 200
        data = resp.json()
        assert "candidates" in data, "Search response must include 'candidates' list"

    def test_search_finds_by_company_name(self, client):
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")

        resp = c.get("/api/securities/search?q=apple")
        assert resp.status_code == 200
        data = resp.json()
        candidate_sids = [c_item["security_id"] for c_item in data.get("candidates", [])]
        assert "XNYS:AAPL" in candidate_sids

    def test_search_has_has_config_flag(self, client):
        """has_config field must be present on every candidate."""
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        fake.container.seed_security("XMAD:TEF", "Telefónica")
        fake.container.seed_config("TEF")  # TEF already enrolled

        resp = c.get("/api/securities/search?q=apple")
        assert resp.status_code == 200
        data = resp.json()
        for candidate in data.get("candidates", []):
            assert "has_config" in candidate, (
                f"Candidate {candidate.get('security_id')} missing has_config field"
            )

    def test_search_returns_empty_for_no_match(self, client):
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")

        resp = c.get("/api/securities/search?q=zzznomatch")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("candidates", []) == []

    def test_search_respects_limit_param(self, client):
        c, fake = client
        for i in range(15):
            fake.container.seed_security(f"XNYS:CO{i:02d}", f"Corp {i}")

        resp = c.get("/api/securities/search?q=corp&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data.get("candidates", [])) <= 5


# ---------------------------------------------------------------------------
# danny-single-add-symbol-contract.md §2.4/§2.3 — Relocated warm-up
# ---------------------------------------------------------------------------

class _SyncThread:
    """Runs the target synchronously on start() — for tests that need to
    observe warm-up side effects deterministically without real threading."""
    def __init__(self, target=None, daemon=None):
        self.target = target
        self.daemon = daemon

    def start(self):
        if self.target is not None:
            self.target()


class TestAddSymbolWarmup:
    def test_warmup_started_true_on_new_config_resolvable_mic(self, client, monkeypatch):
        """config_created=True + resolvable MIC (XNYS bare) → warmup_started."""
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")

        resp = c.post("/api/symbols/add", json={"security_id": "XNYS:AAPL"})
        data = resp.json()

        assert data.get("config_created") is True
        assert data.get("warmup_started") is True

    def test_warmup_started_false_when_mic_unresolvable(self, client):
        """Unknown MIC → resolve_yfinance_symbol fails closed → warmup skipped,
        not an error (response still 201/200, config still created)."""
        c, fake = client
        fake.container.seed_security("XZZZ:FOO", "Foo Corp")

        resp = c.post("/api/symbols/add", json={"security_id": "XZZZ:FOO"})
        data = resp.json()

        assert resp.status_code == 200
        assert data.get("config_created") is True
        assert data.get("warmup_started") is False

    def test_warmup_never_fires_on_config_existed(self, client, monkeypatch):
        """Second add_symbol call for the same security (config already
        exists) → config_created=False, config_existed=True,
        warmup_started=False — never re-triggers enrichment/backfill."""
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")

        first = c.post("/api/symbols/add", json={"security_id": "XNYS:AAPL"})
        assert first.json().get("config_created") is True

        enrich_calls = []
        monkeypatch.setattr(
            "src.portfolio_enrichment.enrich_symbol",
            lambda *a, **kw: enrich_calls.append((a, kw)),
        )

        second = c.post("/api/symbols/add", json={"security_id": "XNYS:AAPL"})
        data = second.json()

        assert data.get("config_created") is False
        assert data.get("config_existed") is True
        assert data.get("warmup_started") is False
        assert enrich_calls == []

    def test_warmup_calls_enrich_symbol_with_resolved_yf_symbol(self, client, monkeypatch):
        """Warm-up must call enrich_symbol with the resolved Yahoo symbol,
        not the bare local ticker, for a non-US MIC."""
        import web.portfolio_routes as portfolio_routes

        c, fake = client
        monkeypatch.setattr(portfolio_routes.threading, "Thread", _SyncThread)
        fake.container.seed_security("XMAD:ENG", "Enagas")

        calls = []

        def _fake_enrich(ticker, yf_symbol=None):
            calls.append((ticker, yf_symbol))
            return {
                "quality_score": 10, "quality_detail": {}, "category": "core",
                "entry_tag": "Hold", "momentum": "Neutral", "metrics": {},
                "technicals": {"score": 40}, "has_dividends": False,
                "filter_detail": None,
            }
        monkeypatch.setattr(portfolio_routes, "resolve_yfinance_symbol",
                           lambda ticker, mic, sec: "ENG.MC")
        monkeypatch.setattr("src.portfolio_enrichment.enrich_symbol", _fake_enrich)

        async def _fake_backfill(*a, **kw):
            return {"status": "ok"}
        monkeypatch.setattr(
            "src.forecast_cron.backfill_symbol_forecasts", _fake_backfill,
        )

        resp = c.post("/api/symbols/add", json={"security_id": "XMAD:ENG"})

        assert resp.json().get("warmup_started") is True
        assert calls == [("ENG", "ENG.MC")]
        assert fake.enrichment_calls and fake.enrichment_calls[0][0] == "ENG"

    def test_enrichment_exception_is_logged_not_swallowed(self, client, monkeypatch, caplog):
        """Enrichment failure during warm-up must be logged (not a silent
        `except Exception: pass`) and must never affect the already-returned
        response."""
        import logging
        import web.portfolio_routes as portfolio_routes

        c, fake = client
        monkeypatch.setattr(portfolio_routes.threading, "Thread", _SyncThread)
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")

        def _raise(*a, **kw):
            raise RuntimeError("yfinance boom")
        monkeypatch.setattr("src.portfolio_enrichment.enrich_symbol", _raise)

        with caplog.at_level(logging.WARNING, logger="web.portfolio_routes"):
            resp = c.post("/api/symbols/add", json={"security_id": "XNYS:AAPL"})

        assert resp.status_code == 200
        assert resp.json().get("config_created") is True
        assert any("yfinance boom" in rec.message or "enrichment failed" in rec.message
                  for rec in caplog.records)
