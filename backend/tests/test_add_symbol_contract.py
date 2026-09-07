"""Regression tests — Single Add Symbol contract.

Ref: .squad/decisions/inbox/danny-single-add-symbol-contract.md

Coverage:
  AS-1   POST /api/symbols/add creates security_master (MIC:TICKER identity).
  AS-2   POST /api/symbols/add with select returns existing security (200).
  AS-3   New symbol_config has all agent/alert/notification flags disabled.
  AS-4   Existing config unchanged on re-add; config_existed=True.
  AS-5   warmup_started field present in response.           [FAILS until Linus §2.4]
  AS-6   warmup_started=False when config_existed=True.      [FAILS until Linus §2.4]
  AS-7   warmup_started=True on config_created+resolvable.  [FAILS until Linus §2.4]
  AS-8   warmup_started=False when MIC resolves to None.    [FAILS until Linus §2.4]
  AS-9   POST /api/symbols returns 404 or 405.              [FAILS until Linus §2.7]
  AS-10  GET /api/symbols still works (list, untouched).
  AS-11  cosmos_db.create_symbol removed.                   [FAILS until Linus §2.7]
  AS-12  ensure_symbol_config failure surfaces as config_warning.
  AS-13  Missing security_id AND create → 400.
  AS-14  Unknown security_id → 404.
  AS-15  ISIN/ticker collision → 409 with existing_security.
  AS-16  navigate_to in response is /symbols/{security_id}.
  AS-17  Security_id in response is MIC:TICKER.
  AS-18  Warm-up NOT triggered when config_existed=True (idempotency). [FAILS §2.4]
  AS-19  config_warning=null on clean path (no error string polluting success).
  AS-20  Enrichment warm-up uses resolved Yahoo symbol (not bare ticker). [FAILS §2.4]
"""

from __future__ import annotations

import logging
import pytest
from unittest.mock import patch, MagicMock
from azure.cosmos.exceptions import CosmosResourceNotFoundError, CosmosHttpResponseError
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fake Cosmos container — supports both security_master and symbol_config docs
# ---------------------------------------------------------------------------

class FakeSymbolsContainer:
    def __init__(self):
        self._store: dict = {}  # (partition_key, id) → doc

    def read_item(self, item: str, partition_key: str):
        key = (partition_key, item)
        if key not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[key])

    def create_item(self, body: dict):
        ticker = body.get("symbol", body.get("ticker", ""))
        key = (ticker, body["id"])
        if key in self._store:
            raise CosmosHttpResponseError(
                message="409 Conflict", response=None, status_code=409,
            )
        self._store[key] = dict(body)
        return dict(body)

    def query_items(self, query="", parameters=None,
                    enable_cross_partition_query=False, partition_key=None):
        param_map = {p["name"]: p["value"] for p in (parameters or [])}
        results = [v for v in self._store.values() if v.get("doc_type") == "security_master"]
        if "@isin" in param_map:
            results = [r for r in results if r.get("isin") == param_map["@isin"]]
        return iter(results)

    def replace_item(self, item, body, **kw):
        for key in list(self._store):
            if self._store[key].get("id") == item:
                self._store[key] = dict(body)
                return dict(body)
        raise CosmosResourceNotFoundError(message="not found", response=None)

    def upsert_item(self, body):
        ticker = body.get("symbol", body.get("ticker", ""))
        key = (ticker, body["id"])
        self._store[key] = dict(body)
        return dict(body)

    def seed_security(self, security_id: str, company_name="Test Co.", **extra):
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
        self._store[(ticker, doc["id"])] = doc
        return doc

    def seed_config(self, ticker: str, extra: dict | None = None):
        doc = {
            "id": f"config_{ticker}",
            "symbol": ticker,
            "doc_type": "symbol_config",
            "watchlist": {
                "covered_call": False,
                "cash_secured_put": False,
                "buy_tracker": False,
            },
            "telegram_notifications_enabled": False,
            "_auto_enrolled": False,   # not auto-enrolled = manually added (config_existed)
        }
        if extra:
            doc.update(extra)
        self._store[(ticker, doc["id"])] = doc
        return doc


class FakeCosmos:
    def __init__(self):
        self.container = FakeSymbolsContainer()
        # portfolio_container stub (required by some middleware checks)
        self.portfolio_container = _FakePortfolioContainer()
        self.import_sessions_container = _FakeImportSessions()

    def list_symbols(self):
        return []

    def get_symbol(self, symbol):
        # Used by the legacy POST /api/symbols path only
        return None

    def update_symbol_enrichment(self, symbol, enrichment):
        return enrichment

    def record_enrichment_snapshot(self, symbol, score, momentum):
        pass


class _FakePortfolioContainer:
    def read(self): return {}
    def query_items(self, **kw): return iter([])
    def upsert_item(self, body): return body


class _FakeImportSessions:
    def read(self): return {}
    def query_items(self, **kw): return iter([])


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
# AS-1 / AS-17: Create new SecurityMaster — canonical MIC:TICKER identity
# ---------------------------------------------------------------------------

class TestAddSymbolCreateNew:
    def test_as1_create_returns_201(self, client):
        """AS-1: POST /api/symbols/add with create body → 201."""
        c, _ = client
        resp = c.post("/api/symbols/add", json={
            "create": {
                "ticker": "AAPL",
                "exchange_mic": "XNYS",
                "company_name": "Apple Inc.",
                "listing_currency": "USD",
            }
        })
        assert resp.status_code == 201, (
            f"AS-1: Expected 201 on new security creation, got {resp.status_code}: {resp.text}"
        )

    def test_as17_security_id_is_mic_colon_ticker(self, client):
        """AS-17: security_id in response is MIC:TICKER format."""
        c, _ = client
        resp = c.post("/api/symbols/add", json={
            "create": {
                "ticker": "MSFT",
                "exchange_mic": "XNAS",
                "company_name": "Microsoft",
                "listing_currency": "USD",
            }
        })
        assert resp.status_code == 201
        data = resp.json()
        security = data.get("security") or {}
        sid = security.get("security_id", "")
        assert ":" in sid, f"AS-17: security_id must be MIC:TICKER format, got {sid!r}"
        assert sid == "XNAS:MSFT", f"AS-17: Expected XNAS:MSFT, got {sid!r}"

    def test_as16_navigate_to_contains_security_id(self, client):
        """AS-16: navigate_to in response is /symbols/{security_id}."""
        c, _ = client
        resp = c.post("/api/symbols/add", json={
            "create": {
                "ticker": "O",
                "exchange_mic": "XNYS",
                "company_name": "Realty Income",
                "listing_currency": "USD",
            }
        })
        assert resp.status_code == 201
        data = resp.json()
        nav = data.get("navigate_to", "")
        assert "XNYS:O" in nav, (
            f"AS-16: navigate_to must contain security_id, got {nav!r}"
        )

    def test_as19_config_warning_null_on_clean_path(self, client):
        """AS-19: config_warning is null when no error occurs."""
        c, _ = client
        resp = c.post("/api/symbols/add", json={
            "create": {
                "ticker": "VZ",
                "exchange_mic": "XNYS",
                "company_name": "Verizon",
                "listing_currency": "USD",
            }
        })
        assert resp.status_code == 201
        assert resp.json().get("config_warning") is None, (
            "AS-19: config_warning must be null on success path"
        )


# ---------------------------------------------------------------------------
# AS-2: Select existing security → 200
# ---------------------------------------------------------------------------

class TestAddSymbolSelectExisting:
    def test_as2_select_existing_returns_200(self, client):
        """AS-2: POST /api/symbols/add with security_id returns 200."""
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        resp = c.post("/api/symbols/add", json={"security_id": "XNYS:AAPL"})
        assert resp.status_code == 200, (
            f"AS-2: Expected 200 on select-existing, got {resp.status_code}"
        )

    def test_as2_select_returns_security_in_body(self, client):
        """AS-2: Response body contains the security document."""
        c, fake = client
        fake.container.seed_security("XNAS:MSFT", "Microsoft")
        resp = c.post("/api/symbols/add", json={"security_id": "XNAS:MSFT"})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("security") is not None, "AS-2: 'security' field missing from response"
        assert data["security"]["security_id"] == "XNAS:MSFT"


# ---------------------------------------------------------------------------
# AS-3: New config has all flags disabled
# ---------------------------------------------------------------------------

class TestAddSymbolConfigDefaults:
    def test_as3_new_config_covered_call_false(self, client):
        """AS-3: covered_call defaults to False on new config."""
        c, fake = client
        resp = c.post("/api/symbols/add", json={
            "create": {
                "ticker": "ABBV",
                "exchange_mic": "XNYS",
                "company_name": "AbbVie",
                "listing_currency": "USD",
            }
        })
        assert resp.status_code == 201
        # Read the config that was created
        config = fake.container._store.get(("ABBV", "config_ABBV"))
        assert config is not None, "AS-3: config_ABBV must be created"
        assert config.get("watchlist", {}).get("covered_call") is False, (
            "AS-3: covered_call must default to False"
        )

    def test_as3_new_config_cash_secured_put_false(self, client):
        """AS-3: cash_secured_put defaults to False on new config."""
        c, fake = client
        resp = c.post("/api/symbols/add", json={
            "create": {
                "ticker": "JNJ",
                "exchange_mic": "XNYS",
                "company_name": "J&J",
                "listing_currency": "USD",
            }
        })
        assert resp.status_code == 201
        config = fake.container._store.get(("JNJ", "config_JNJ"))
        assert config is not None
        assert config.get("watchlist", {}).get("cash_secured_put") is False, (
            "AS-3: cash_secured_put must default to False"
        )

    def test_as3_new_config_buy_tracker_false(self, client):
        """AS-3: buy_tracker defaults to False on new config."""
        c, fake = client
        c.post("/api/symbols/add", json={
            "create": {
                "ticker": "T",
                "exchange_mic": "XNYS",
                "company_name": "AT&T",
                "listing_currency": "USD",
            }
        })
        config = fake.container._store.get(("T", "config_T"))
        assert config is not None
        assert config.get("watchlist", {}).get("buy_tracker") is False, (
            "AS-3: buy_tracker must default to False"
        )

    def test_as3_new_config_telegram_false(self, client):
        """AS-3: telegram_notifications_enabled defaults to False on new config."""
        c, fake = client
        c.post("/api/symbols/add", json={
            "create": {
                "ticker": "MO",
                "exchange_mic": "XNYS",
                "company_name": "Altria",
                "listing_currency": "USD",
            }
        })
        config = fake.container._store.get(("MO", "config_MO"))
        assert config is not None
        assert config.get("telegram_notifications_enabled") is False, (
            "AS-3: telegram_notifications_enabled must default to False"
        )


# ---------------------------------------------------------------------------
# AS-4: Re-add existing symbol → config unchanged, config_existed=True
# ---------------------------------------------------------------------------

class TestAddSymbolIdempotent:
    def test_as4_readd_returns_config_existed_true(self, client):
        """AS-4: Second add_symbol call returns config_existed=True."""
        c, fake = client
        # Seed a security_master + existing config
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        fake.container.seed_config("AAPL", extra={
            "security_id": "XNYS:AAPL",
            "watchlist": {"covered_call": True, "cash_secured_put": False, "buy_tracker": False},
            "telegram_notifications_enabled": False,
            "_auto_enrolled": False,
        })
        resp = c.post("/api/symbols/add", json={"security_id": "XNYS:AAPL"})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("config_existed") is True, (
            f"AS-4: Expected config_existed=True on re-add, got {data!r}"
        )
        assert data.get("config_created") is False, (
            "AS-4: config_created must be False when config already existed"
        )

    def test_as4_readd_does_not_overwrite_existing_flags(self, client):
        """AS-4: Re-adding an existing symbol must NOT reset covered_call to False."""
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        fake.container.seed_config("AAPL", extra={
            "security_id": "XNYS:AAPL",
            "watchlist": {"covered_call": True, "cash_secured_put": False, "buy_tracker": True},
            "telegram_notifications_enabled": True,
            "_auto_enrolled": False,
        })
        c.post("/api/symbols/add", json={"security_id": "XNYS:AAPL"})

        # Config must be byte-for-byte unchanged
        config_after = fake.container._store.get(("AAPL", "config_AAPL"))
        assert config_after["watchlist"]["covered_call"] is True, (
            "AS-4: covered_call must not be reset by re-add"
        )
        assert config_after["watchlist"]["buy_tracker"] is True, (
            "AS-4: buy_tracker must not be reset by re-add"
        )
        assert config_after["telegram_notifications_enabled"] is True, (
            "AS-4: telegram_notifications_enabled must not be reset by re-add"
        )


# ---------------------------------------------------------------------------
# AS-5 / AS-6 / AS-7 / AS-8 / AS-18 / AS-20: Warm-up contract
# These tests WILL FAIL until Linus implements §2.4 warm-up in add_symbol.
# ---------------------------------------------------------------------------

class TestAddSymbolWarmup:
    def test_as5_warmup_started_field_in_response(self, client):
        """AS-5: Response must contain warmup_started field.
        FAILS until Linus adds warm-up logic (§2.4).
        """
        c, _ = client
        resp = c.post("/api/symbols/add", json={
            "create": {
                "ticker": "WMT",
                "exchange_mic": "XNYS",
                "company_name": "Walmart",
                "listing_currency": "USD",
            }
        })
        assert resp.status_code == 201
        data = resp.json()
        assert "warmup_started" in data, (
            "AS-5: 'warmup_started' field missing from add_symbol response. "
            "Linus must add §2.4 warm-up and the warmup_started response field."
        )

    def test_as6_warmup_started_false_when_config_existed(self, client):
        """AS-6: warmup_started=False when config already existed.
        FAILS until Linus implements §2.4.
        """
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        fake.container.seed_config("AAPL", extra={
            "security_id": "XNYS:AAPL",
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
            "telegram_notifications_enabled": False,
            "_auto_enrolled": False,
        })
        resp = c.post("/api/symbols/add", json={"security_id": "XNYS:AAPL"})
        assert resp.status_code == 200
        data = resp.json()
        assert "warmup_started" in data, (
            "AS-6: warmup_started field missing from response"
        )
        assert data["warmup_started"] is False, (
            f"AS-6: warmup_started must be False when config already existed (re-add). "
            f"Got: {data.get('warmup_started')!r}"
        )

    def test_as7_warmup_started_true_on_new_us_symbol(self, client, monkeypatch):
        """AS-7: warmup_started=True when config_created=True and MIC resolves.
        FAILS until Linus implements §2.4.
        """
        c, _ = client
        # XNYS resolves to bare ticker (no suffix) — non-None return
        monkeypatch.setattr(
            "web.portfolio_routes.resolve_yfinance_symbol",
            lambda ticker, mic, doc=None: ticker.upper(),  # non-None
        )
        resp = c.post("/api/symbols/add", json={
            "create": {
                "ticker": "CVS",
                "exchange_mic": "XNYS",
                "company_name": "CVS Health",
                "listing_currency": "USD",
            }
        })
        assert resp.status_code == 201
        data = resp.json()
        assert "warmup_started" in data, "AS-7: warmup_started field missing"
        assert data["warmup_started"] is True, (
            f"AS-7: warmup_started must be True when config_created=True and MIC resolves. "
            f"Got: {data.get('warmup_started')!r}"
        )

    def test_as8_warmup_started_false_when_mic_unresolvable(self, client, monkeypatch):
        """AS-8: warmup_started=False when resolve_yfinance_symbol returns None.
        FAILS until Linus implements §2.4.
        """
        c, _ = client
        monkeypatch.setattr(
            "web.portfolio_routes.resolve_yfinance_symbol",
            lambda ticker, mic, doc=None: None,  # unknown MIC
        )
        resp = c.post("/api/symbols/add", json={
            "create": {
                "ticker": "FOO",
                "exchange_mic": "XZZZ",
                "company_name": "FooBar Exchange",
                "listing_currency": "EUR",
            }
        })
        assert resp.status_code == 201
        data = resp.json()
        assert "warmup_started" in data, "AS-8: warmup_started field missing"
        assert data["warmup_started"] is False, (
            f"AS-8: warmup_started must be False when MIC is unresolvable. "
            f"Got: {data.get('warmup_started')!r}"
        )

    def test_as18_no_warmup_on_readd(self, client, monkeypatch):
        """AS-18: Warm-up fires at most once per new config — never on re-add.
        FAILS until Linus implements §2.4.
        """
        c, fake = client
        warmup_calls = []

        def _fake_resolve(ticker, mic, doc=None):
            return ticker.upper()

        monkeypatch.setattr("web.portfolio_routes.resolve_yfinance_symbol", _fake_resolve)

        # Track whether any background thread is started
        original_thread = __import__("threading").Thread
        threads_started = []

        class _TrackingThread:
            def __init__(self, target=None, daemon=None, **kw):
                self._target = target
                threads_started.append(target)
            def start(self):
                pass  # Don't actually run — we just count

        monkeypatch.setattr("web.portfolio_routes.threading.Thread", _TrackingThread)

        # First add — new config, warm-up should fire
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        fake.container.seed_config("AAPL", extra={
            "security_id": "XNYS:AAPL",
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
            "telegram_notifications_enabled": False,
            "_auto_enrolled": False,
        })
        # Re-add (config already exists)
        threads_before = len(threads_started)
        resp = c.post("/api/symbols/add", json={"security_id": "XNYS:AAPL"})
        assert resp.status_code == 200
        threads_after = len(threads_started)
        assert threads_after == threads_before, (
            f"AS-18: Warm-up threads must NOT fire on re-add (config_existed). "
            f"Got {threads_after - threads_before} new thread(s) started."
        )

    def test_as20_enrichment_uses_resolved_yf_symbol(self, client, monkeypatch):
        """AS-20: Warm-up must fire with warmup_started=True for resolvable XETR symbol.
        When Linus implements §2.4, the enrichment thread must call enrich_symbol with
        the resolved yf_symbol (e.g. "SAP.DE"), NOT the bare ticker ("SAP").
        FAILS until Linus implements §2.4.
        """
        c, _ = client
        monkeypatch.setattr(
            "web.portfolio_routes.resolve_yfinance_symbol",
            lambda ticker, mic, doc=None: f"{ticker.upper()}.DE" if mic == "XETR" else ticker.upper(),
        )
        # enrich_symbol does not exist in portfolio_routes yet (Linus must import it in §2.4).
        # Once Linus adds warm-up, patch it to capture the yf_symbol argument.
        # For now we can only assert on the observable response field.

        resp = c.post("/api/symbols/add", json={
            "create": {
                "ticker": "SAP",
                "exchange_mic": "XETR",
                "company_name": "SAP SE",
                "listing_currency": "EUR",
            }
        })
        assert resp.status_code == 201

        data = resp.json()
        assert "warmup_started" in data, (
            "AS-20: warmup_started field missing — Linus must add §2.4 warm-up threads. "
            "When implemented, the enrichment thread must use SAP.DE (resolved yf_symbol), "
            "not bare SAP."
        )
        assert data.get("warmup_started") is True, (
            f"AS-20: warmup_started must be True for XETR (resolvable MIC via resolve_yfinance_symbol). "
            f"Got: {data.get('warmup_started')!r}"
        )


# ---------------------------------------------------------------------------
# AS-9: POST /api/symbols removed (legacy endpoint)
# WILL FAIL until Linus removes app.py api_create_symbol (§2.7).
# ---------------------------------------------------------------------------

class TestLegacyPostSymbolsRemoved:
    def test_as9_post_api_symbols_returns_404_or_405(self, client):
        """AS-9: POST /api/symbols must not exist after migration.
        FAILS until Linus removes api_create_symbol from app.py (§2.7).
        """
        c, _ = client
        resp = c.post("/api/symbols", json={
            "symbol": "TEST",
            "exchange": "NYSE",
            "display_name": "Test",
        })
        assert resp.status_code in (404, 405), (
            f"AS-9: POST /api/symbols must be removed (§2.7). "
            f"Currently returns {resp.status_code}. "
            f"Linus: delete api_create_symbol from app.py and cosmos_db.create_symbol()."
        )


# ---------------------------------------------------------------------------
# AS-10: GET /api/symbols still works
# ---------------------------------------------------------------------------

class TestGetSymbolsPreserved:
    def test_as10_get_api_symbols_returns_200_or_non_5xx(self, client):
        """AS-10: GET /api/symbols (list) must remain available."""
        c, _ = client
        resp = c.get("/api/symbols")
        assert resp.status_code < 500, (
            f"AS-10: GET /api/symbols must remain functional. Got {resp.status_code}."
        )
        # 200 expected; 404 would be wrong
        assert resp.status_code == 200, (
            f"AS-10: GET /api/symbols must return 200. Got {resp.status_code}."
        )


# ---------------------------------------------------------------------------
# AS-11: cosmos_db.create_symbol removed
# WILL FAIL until Linus removes it (§2.7).
# ---------------------------------------------------------------------------

class TestCreateSymbolRemovedFromCosmosDb:
    def test_as11_create_symbol_method_does_not_exist(self):
        """AS-11: cosmos_db.CosmosDBService.create_symbol must be removed.
        FAILS until Linus deletes it after removing its only caller (§2.7).
        """
        from src.cosmos_db import CosmosDBService
        assert not hasattr(CosmosDBService, "create_symbol"), (
            "AS-11: CosmosDBService.create_symbol still exists. "
            "Linus: remove it together with POST /api/symbols (§2.7). "
            "get_symbol, update_watchlist, replace_symbol must remain."
        )

    def test_as11_get_symbol_still_exists(self):
        """AS-11: get_symbol must remain on CosmosDBService."""
        from src.cosmos_db import CosmosDBService
        assert hasattr(CosmosDBService, "get_symbol"), (
            "AS-11: get_symbol must not be removed — it is used by PUT /api/symbols/{symbol}."
        )

    def test_as11_update_watchlist_still_exists(self):
        """AS-11: update_watchlist must remain on CosmosDBService."""
        from src.cosmos_db import CosmosDBService
        assert hasattr(CosmosDBService, "update_watchlist"), (
            "AS-11: update_watchlist must not be removed — it is used by PUT /api/symbols/{symbol}."
        )


# ---------------------------------------------------------------------------
# AS-12: ensure_symbol_config failure surfaces as config_warning
# ---------------------------------------------------------------------------

class TestAddSymbolConfigWarning:
    def test_as12_config_failure_surfaces_as_warning_not_500(self, client, monkeypatch):
        """AS-12: ensure_symbol_config failure → config_warning in 200/201, not 500."""
        # Must patch the name in portfolio_routes' namespace (it imports the function directly)
        monkeypatch.setattr(
            "web.portfolio_routes.ensure_symbol_config",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("Cosmos transient error")),
        )

        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        resp = c.post("/api/symbols/add", json={"security_id": "XNYS:AAPL"})
        assert resp.status_code in (200, 201), (
            f"AS-12: Config failure must yield 200/201 with config_warning, not {resp.status_code}"
        )
        data = resp.json()
        assert data.get("config_warning") is not None, (
            "AS-12: config_warning must contain the error message when ensure_symbol_config fails"
        )

    def test_as12_config_warning_does_not_prevent_security_response(self, client, monkeypatch):
        """AS-12: Security is returned even when config fails."""
        monkeypatch.setattr(
            "web.portfolio_routes.ensure_symbol_config",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("timeout")),
        )
        c, fake = client
        fake.container.seed_security("XNAS:MSFT", "Microsoft")
        resp = c.post("/api/symbols/add", json={"security_id": "XNAS:MSFT"})
        assert resp.status_code in (200, 201)
        assert resp.json().get("security") is not None, (
            "AS-12: security field must still be present even when config_warning is set"
        )


# ---------------------------------------------------------------------------
# AS-13: Validation — missing body keys → 400
# ---------------------------------------------------------------------------

class TestAddSymbolValidation:
    def test_as13_no_security_id_and_no_create_400(self, client):
        """AS-13: Empty body → 400 validation_error."""
        c, _ = client
        resp = c.post("/api/symbols/add", json={})
        assert resp.status_code == 400

    def test_as13_create_missing_ticker_400(self, client):
        """AS-13: create without ticker → 400."""
        c, _ = client
        resp = c.post("/api/symbols/add", json={
            "create": {"exchange_mic": "XNYS", "company_name": "Test", "listing_currency": "USD"}
        })
        assert resp.status_code == 400

    def test_as13_create_missing_exchange_mic_400(self, client):
        """AS-13: create without exchange_mic → 400."""
        c, _ = client
        resp = c.post("/api/symbols/add", json={
            "create": {"ticker": "TST", "company_name": "Test", "listing_currency": "USD"}
        })
        assert resp.status_code == 400

    def test_as14_unknown_security_id_404(self, client):
        """AS-14: security_id not found → 404."""
        c, _ = client
        resp = c.post("/api/symbols/add", json={"security_id": "XNYS:GHOST"})
        assert resp.status_code == 404, (
            f"AS-14: Unknown security_id must return 404, got {resp.status_code}"
        )

    def test_as15_ticker_collision_409(self, client):
        """AS-15: Duplicate ticker collision → 409 with existing_security."""
        c, fake = client
        # First create
        c.post("/api/symbols/add", json={
            "create": {
                "ticker": "DUP",
                "exchange_mic": "XNYS",
                "company_name": "Duplicate Corp",
                "listing_currency": "USD",
            }
        })
        # Second create with same ticker
        resp = c.post("/api/symbols/add", json={
            "create": {
                "ticker": "DUP",
                "exchange_mic": "XNYS",
                "company_name": "Another Corp",
                "listing_currency": "USD",
            }
        })
        assert resp.status_code == 409
        data = resp.json()
        assert "existing_security" in data or "existing" in data, (
            f"AS-15: 409 response must include existing_security. Got: {data!r}"
        )
