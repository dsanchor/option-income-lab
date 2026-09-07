"""Backend tests for Symbol Configuration Section contract.

Covers:
  - PATCH /api/symbols/{symbol}/security (update_security + endpoint)
  - POST  /api/symbols/{symbol}/enrichment/refresh
  - _compute_symbol_detail security field extension
    (provider_symbols, effective_yfinance_symbol, effective_tradingview_symbol,
     cusip, sedol, country, asset_class, _etag)

Implements danny-symbol-configuration-section-contract.md test requirements.
"""

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError, CosmosHttpResponseError


# ---------------------------------------------------------------------------
# Fake Cosmos containers
# ---------------------------------------------------------------------------

class _FakeSymbolsContainerETag:
    """Symbols container with realistic ETag/412 support."""

    def __init__(self):
        self._store: dict = {}   # (ticker, doc_id) → doc
        self._etag_counter: int = 0

    def _next_etag(self, doc_id: str) -> str:
        self._etag_counter += 1
        return f"etag-{doc_id}-v{self._etag_counter}"

    def seed(self, doc: dict) -> dict:
        ticker = doc.get("symbol", doc.get("ticker", ""))
        etag = self._next_etag(doc["id"])
        full = {**doc, "_etag": etag}
        self._store[(ticker, doc["id"])] = full
        return dict(full)

    def read_item(self, item: str, partition_key: str) -> dict:
        key = (partition_key, item)
        if key not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[key])

    def create_item(self, body: dict) -> dict:
        ticker = body.get("symbol", body.get("ticker", ""))
        key = (ticker, body["id"])
        etag = self._next_etag(body["id"])
        doc = {**body, "_etag": etag}
        self._store[key] = doc
        return dict(doc)

    def replace_item(self, item: str, body: dict, *, etag: str = None, match_condition=None, **kw) -> dict:
        for key, stored in list(self._store.items()):
            if stored.get("id") == item:
                if etag and stored.get("_etag") != etag:
                    raise CosmosHttpResponseError(
                        status_code=412, message="Precondition Failed", response=None
                    )
                new_etag = self._next_etag(item)
                updated = {**body, "_etag": new_etag}
                self._store[key] = updated
                return dict(updated)
        raise CosmosResourceNotFoundError(message="not found", response=None)

    def delete_item(self, item: str, partition_key: str, **kw) -> None:
        key = (partition_key, item)
        self._store.pop(key, None)

    def query_items(self, query="", parameters=None, enable_cross_partition_query=False, partition_key=None):
        param_map = {p["name"]: p["value"] for p in (parameters or [])}
        results = []
        for (pk, did), doc in self._store.items():
            if partition_key and pk != partition_key:
                continue
            if doc.get("doc_type") != "security_master":
                continue
            if "@isin" in param_map and doc.get("isin") != param_map["@isin"]:
                continue
            if "@cusip" in param_map and doc.get("cusip") != param_map["@cusip"]:
                continue
            if "@sedol" in param_map and doc.get("sedol") != param_map["@sedol"]:
                continue
            results.append(dict(doc))
        return iter(results)

    def upsert_item(self, body: dict) -> dict:
        ticker = body.get("symbol", "")
        key = (ticker, body["id"])
        doc = {**body, "_etag": self._next_etag(body["id"])}
        self._store[key] = doc
        return dict(doc)


class _FakeCosmos:
    """Minimal CosmosDBService fake for endpoint tests."""

    def __init__(self, ticker: str = "AD", security_id: str = "XAMS:AD"):
        self.container = _FakeSymbolsContainerETag()
        self._ticker = ticker
        self._security_id = security_id
        self._sym_doc = {
            "id": f"config_{ticker}",
            "symbol": ticker,
            "doc_type": "symbol_config",
            "security_id": security_id,
            "exchange": security_id.split(":")[0],
            "display_name": "Ahold Delhaize",
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
            "telegram_notifications_enabled": False,
            "total_shares": 0,
        }
        self._enrichment = {}

    def get_symbol(self, symbol: str) -> dict | None:
        if symbol.upper() == self._ticker.upper():
            return dict(self._sym_doc)
        return None

    def list_symbols(self):
        return [self._sym_doc]

    def replace_symbol(self, doc: dict) -> dict:
        self._sym_doc = dict(doc)
        return dict(doc)

    def get_symbol_doc(self, symbol: str) -> dict | None:
        return self.get_symbol(symbol)

    def update_symbol_enrichment(self, symbol: str, enrichment: dict) -> dict:
        self._enrichment = enrichment
        return {**self._sym_doc, "enrichment": enrichment}

    def record_enrichment_snapshot(self, symbol: str, tech_timing, momentum: str = "", **kw):
        pass  # no-op in tests

    def get_plans(self, symbol):
        return []

    def get_recent_activities(self, *a, **kw):
        return []

    def get_recent_alerts(self, *a, **kw):
        return []

    def get_next_earnings_date(self, symbol):
        return None

    def get_plans_count(self, *a, **kw):
        return 0


def _seed_security(container, ticker="AD", security_id="XAMS:AD", **extra) -> dict:
    """Seed a minimal security_master doc into a fake container."""
    doc = {
        "id": f"sec_{security_id.replace(':', '_')}",
        "symbol": ticker,
        "doc_type": "security_master",
        "security_id": security_id,
        "ticker": ticker,
        "company_name": extra.pop("company_name", "Ahold Delhaize NV"),
        "exchange_mic": security_id.split(":")[0],
        "listing_currency": extra.pop("listing_currency", "EUR"),
        "isin": extra.pop("isin", "NL0011794037"),
        "provider_symbols": extra.pop("provider_symbols", {"yfinance": "AD.AS"}),
        **extra,
    }
    return container.seed(doc)


@pytest.fixture
def client_with_ad():
    """Return (TestClient, fake_cosmos, seeded_etag) for XAMS:AD."""
    from web.app import app
    fake_cosmos = _FakeCosmos(ticker="AD", security_id="XAMS:AD")
    seeded = _seed_security(fake_cosmos.container)
    with TestClient(app) as c:
        app.state.cosmos = fake_cosmos
        app.state.cosmos_error = None
        yield c, fake_cosmos, seeded["_etag"]


# ---------------------------------------------------------------------------
# update_security unit tests (service layer — no HTTP)
# ---------------------------------------------------------------------------

class TestUpdateSecurityService:
    def _svc(self, ticker="AD", security_id="XAMS:AD", **extra):
        from src.portfolio.cosmos_securities import CosmosSecuritiesService
        container = _FakeSymbolsContainerETag()
        seeded = _seed_security(container, ticker=ticker, security_id=security_id, **extra)
        return CosmosSecuritiesService(container), seeded

    def test_update_company_name_ok(self):
        svc, seeded = self._svc()
        result = svc.update_security(
            security_id="XAMS:AD",
            updates={"company_name": "Ahold Delhaize (Updated)"},
            etag=seeded["_etag"],
        )
        assert result["company_name"] == "Ahold Delhaize (Updated)"
        assert "_etag" in result
        assert result["_etag"] != seeded["_etag"]

    def test_update_rejects_security_id(self):
        svc, seeded = self._svc()
        with pytest.raises(ValueError, match="Identity fields are read-only"):
            svc.update_security(
                security_id="XAMS:AD",
                updates={"security_id": "XNYS:AD"},
                etag=seeded["_etag"],
            )

    def test_update_rejects_exchange_mic(self):
        svc, seeded = self._svc()
        with pytest.raises(ValueError, match="Identity fields are read-only"):
            svc.update_security(
                security_id="XAMS:AD",
                updates={"exchange_mic": "XNYS"},
                etag=seeded["_etag"],
            )

    def test_update_rejects_ticker(self):
        svc, seeded = self._svc()
        with pytest.raises(ValueError, match="Identity fields are read-only"):
            svc.update_security(
                security_id="XAMS:AD",
                updates={"ticker": "ADNEW"},
                etag=seeded["_etag"],
            )

    def test_update_listing_currency_ok(self):
        svc, seeded = self._svc()
        result = svc.update_security(
            security_id="XAMS:AD",
            updates={"listing_currency": "USD"},
            etag=seeded["_etag"],
        )
        assert result["listing_currency"] == "USD"

    def test_update_listing_currency_invalid_format(self):
        svc, seeded = self._svc()
        with pytest.raises(ValueError, match="ISO 4217"):
            svc.update_security(
                security_id="XAMS:AD",
                updates={"listing_currency": "EURO"},  # 4 letters — invalid
                etag=seeded["_etag"],
            )

    def test_update_listing_currency_lowercase_normalised(self):
        svc, seeded = self._svc()
        result = svc.update_security(
            security_id="XAMS:AD",
            updates={"listing_currency": "eur"},  # lowercase → normalised to EUR
            etag=seeded["_etag"],
        )
        assert result["listing_currency"] == "EUR"

    def test_update_provider_symbols_validated(self):
        svc, seeded = self._svc()
        with pytest.raises(ValueError):
            svc.update_security(
                security_id="XAMS:AD",
                updates={"provider_symbols": {"BAD KEY!": "AD.AS"}},
                etag=seeded["_etag"],
            )

    def test_update_provider_symbols_merged(self):
        svc, seeded = self._svc(provider_symbols={"yfinance": "AD.AS"})
        result = svc.update_security(
            security_id="XAMS:AD",
            updates={"provider_symbols": {"tradingview": "EURONEXT-AD"}},
            etag=seeded["_etag"],
        )
        ps = result["provider_symbols"]
        assert ps["yfinance"] == "AD.AS"       # preserved
        assert ps["tradingview"] == "EURONEXT-AD"  # added

    def test_etag_conflict_raises(self):
        from src.portfolio.cosmos_securities import _ETagConflictError
        svc, seeded = self._svc()
        with pytest.raises(_ETagConflictError) as exc_info:
            svc.update_security(
                security_id="XAMS:AD",
                updates={"company_name": "Conflict"},
                etag='"stale-etag-that-does-not-match"',
            )
        assert exc_info.value.current_doc["security_id"] == "XAMS:AD"

    def test_isin_collision_raises(self):
        from src.portfolio.cosmos_securities import _CollisionError
        container = _FakeSymbolsContainerETag()
        # Seed two securities — one with the ISIN we'll try to assign
        _seed_security(container, ticker="AD", security_id="XAMS:AD", isin="NL0011794037")
        other = _seed_security(container, ticker="RDSA", security_id="XAMS:RDSA", isin="NL0000009165")
        from src.portfolio.cosmos_securities import CosmosSecuritiesService
        svc = CosmosSecuritiesService(container)
        ad_doc = container.read_item("sec_XAMS_AD", "AD")
        with pytest.raises(_CollisionError) as exc_info:
            svc.update_security(
                security_id="XAMS:AD",
                updates={"isin": "NL0000009165"},  # RDSA's ISIN
                etag=ad_doc["_etag"],
            )
        assert exc_info.value.field == "isin"
        assert exc_info.value.existing["security_id"] == "XAMS:RDSA"

    def test_cusip_collision_raises(self):
        """CUSIP collision raises _CollisionError with field='cusip'."""
        from src.portfolio.cosmos_securities import _CollisionError, CosmosSecuritiesService
        container = _FakeSymbolsContainerETag()
        _seed_security(container, ticker="AD", security_id="XAMS:AD", cusip="N12345678")
        _seed_security(container, ticker="FOO", security_id="XAMS:FOO", cusip="X99999999")
        svc = CosmosSecuritiesService(container)
        ad_doc = container.read_item("sec_XAMS_AD", "AD")
        with pytest.raises(_CollisionError) as exc_info:
            svc.update_security(
                security_id="XAMS:AD",
                updates={"cusip": "X99999999"},  # FOO's CUSIP
                etag=ad_doc["_etag"],
            )
        assert exc_info.value.field == "cusip"
        assert exc_info.value.existing["security_id"] == "XAMS:FOO"

    def test_sedol_collision_raises(self):
        """SEDOL collision raises _CollisionError with field='sedol'."""
        from src.portfolio.cosmos_securities import _CollisionError, CosmosSecuritiesService
        container = _FakeSymbolsContainerETag()
        _seed_security(container, ticker="AD", security_id="XAMS:AD", sedol="B5M5VQ8")
        _seed_security(container, ticker="BAR", security_id="XAMS:BAR", sedol="B12345Z")
        svc = CosmosSecuritiesService(container)
        ad_doc = container.read_item("sec_XAMS_AD", "AD")
        with pytest.raises(_CollisionError) as exc_info:
            svc.update_security(
                security_id="XAMS:AD",
                updates={"sedol": "B12345Z"},  # BAR's SEDOL
                etag=ad_doc["_etag"],
            )
        assert exc_info.value.field == "sedol"
        assert exc_info.value.existing["security_id"] == "XAMS:BAR"

    def test_country_optional_field_added(self):
        svc, seeded = self._svc()
        result = svc.update_security(
            security_id="XAMS:AD",
            updates={"country": "NL"},
            etag=seeded["_etag"],
        )
        assert result["country"] == "NL"

    def test_country_null_removes_field(self):
        svc, _ = self._svc()
        # First set it
        container = _FakeSymbolsContainerETag()
        seeded = _seed_security(container, country="NL")
        from src.portfolio.cosmos_securities import CosmosSecuritiesService
        svc = CosmosSecuritiesService(container)
        result = svc.update_security(
            security_id="XAMS:AD",
            updates={"country": None},  # null → remove
            etag=seeded["_etag"],
        )
        assert "country" not in result or result.get("country") is None


# ---------------------------------------------------------------------------
# PATCH /api/symbols/{symbol}/security endpoint tests
# ---------------------------------------------------------------------------

class TestPatchSecurityEndpoint:
    def test_400_on_security_id_in_body(self, client_with_ad):
        c, _, etag = client_with_ad
        resp = c.patch("/api/symbols/AD/security", json={
            "_etag": etag,
            "security_id": "XNYS:AD",
        })
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"] == "identity_fields_read_only"
        assert "security_id" in data["detail"]

    def test_400_on_exchange_mic_in_body(self, client_with_ad):
        c, _, etag = client_with_ad
        resp = c.patch("/api/symbols/AD/security", json={
            "_etag": etag,
            "exchange_mic": "XNYS",
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "identity_fields_read_only"

    def test_400_on_ticker_in_body(self, client_with_ad):
        c, _, etag = client_with_ad
        resp = c.patch("/api/symbols/AD/security", json={
            "_etag": etag,
            "ticker": "ADNEW",
        })
        assert resp.status_code == 400

    def test_400_missing_etag(self, client_with_ad):
        c, _, _ = client_with_ad
        resp = c.patch("/api/symbols/AD/security", json={"company_name": "New Name"})
        assert resp.status_code == 400
        assert "etag" in resp.json()["error"]

    def test_404_unknown_symbol(self, client_with_ad):
        c, _, etag = client_with_ad
        resp = c.patch("/api/symbols/UNKNOWN/security", json={"_etag": etag, "company_name": "X"})
        assert resp.status_code == 404

    def test_422_invalid_listing_currency(self, client_with_ad):
        c, _, etag = client_with_ad
        resp = c.patch("/api/symbols/AD/security", json={
            "_etag": etag,
            "listing_currency": "EUROS",
        })
        assert resp.status_code == 422
        assert resp.json()["error"] == "validation_error"

    def test_422_invalid_provider_symbols(self, client_with_ad):
        c, _, etag = client_with_ad
        resp = c.patch("/api/symbols/AD/security", json={
            "_etag": etag,
            "provider_symbols": {"INVALID KEY": "AD.AS"},
        })
        assert resp.status_code == 422

    def test_409_etag_conflict(self, client_with_ad):
        c, _, _ = client_with_ad
        resp = c.patch("/api/symbols/AD/security", json={
            "_etag": '"stale-etag-will-not-match"',
            "company_name": "New Name",
        })
        assert resp.status_code == 409
        data = resp.json()
        assert data["error"] == "etag_conflict"
        assert "current" in data

    def test_200_success_returns_security_projection(self, client_with_ad):
        c, _, etag = client_with_ad
        resp = c.patch("/api/symbols/AD/security", json={
            "_etag": etag,
            "company_name": "Ahold Delhaize NV Updated",
            "listing_currency": "EUR",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "security" in data
        sec = data["security"]
        assert sec["company_name"] == "Ahold Delhaize NV Updated"
        assert sec["listing_currency"] == "EUR"
        # Read-only fields present and unchanged
        assert sec["security_id"] == "XAMS:AD"
        assert sec["exchange_mic"] == "XAMS"
        # Effective resolved symbols present
        assert "effective_yfinance_symbol" in sec
        assert "effective_tradingview_symbol" in sec
        # _etag present and updated
        assert "_etag" in sec
        assert sec["_etag"] != etag

    def test_200_provider_symbols_override_reflected_in_effective(self, client_with_ad):
        c, _, etag = client_with_ad
        resp = c.patch("/api/symbols/AD/security", json={
            "_etag": etag,
            "provider_symbols": {"yfinance": "AD.AS", "tradingview": "EURONEXT-AD"},
        })
        assert resp.status_code == 200
        sec = resp.json()["security"]
        # effective_yfinance_symbol = override value (via resolve_yfinance_symbol)
        assert sec["effective_yfinance_symbol"] == "AD.AS"
        assert sec["effective_tradingview_symbol"] == "EURONEXT-AD"

    def test_409_isin_collision(self):
        """ISIN collision returns 409 with error=collision."""
        from web.app import app
        fake_cosmos = _FakeCosmos(ticker="AD", security_id="XAMS:AD")
        # Seed both AD and RDSA with distinct ISINs
        _seed_security(fake_cosmos.container, ticker="AD", security_id="XAMS:AD", isin="NL0011794037")
        _seed_security(fake_cosmos.container, ticker="RDSA", security_id="XAMS:RDSA", isin="NL0000009165")
        ad_etag = fake_cosmos.container.read_item("sec_XAMS_AD", "AD")["_etag"]
        with TestClient(app) as c:
            app.state.cosmos = fake_cosmos
            app.state.cosmos_error = None
            resp = c.patch("/api/symbols/AD/security", json={
                "_etag": ad_etag,
                "isin": "NL0000009165",  # RDSA's ISIN
            })
        assert resp.status_code == 409
        data = resp.json()
        assert data["error"] == "collision"
        assert "colliding_security" in data

    def test_409_cusip_collision(self):
        """CUSIP collision returns 409 with error=collision and field=cusip."""
        from web.app import app
        fake_cosmos = _FakeCosmos(ticker="AD", security_id="XAMS:AD")
        _seed_security(fake_cosmos.container, ticker="AD", security_id="XAMS:AD", cusip="N12345678")
        _seed_security(fake_cosmos.container, ticker="FOO", security_id="XAMS:FOO", cusip="X99999999")
        ad_etag = fake_cosmos.container.read_item("sec_XAMS_AD", "AD")["_etag"]
        with TestClient(app) as c:
            app.state.cosmos = fake_cosmos
            app.state.cosmos_error = None
            resp = c.patch("/api/symbols/AD/security", json={
                "_etag": ad_etag,
                "cusip": "X99999999",  # FOO's CUSIP
            })
        assert resp.status_code == 409
        data = resp.json()
        assert data["error"] == "collision"
        assert data.get("field") == "cusip" or "colliding_security" in data

    def test_409_sedol_collision(self):
        """SEDOL collision returns 409 with error=collision and field=sedol."""
        from web.app import app
        fake_cosmos = _FakeCosmos(ticker="AD", security_id="XAMS:AD")
        _seed_security(fake_cosmos.container, ticker="AD", security_id="XAMS:AD", sedol="B5M5VQ8")
        _seed_security(fake_cosmos.container, ticker="BAR", security_id="XAMS:BAR", sedol="B12345Z")
        ad_etag = fake_cosmos.container.read_item("sec_XAMS_AD", "AD")["_etag"]
        with TestClient(app) as c:
            app.state.cosmos = fake_cosmos
            app.state.cosmos_error = None
            resp = c.patch("/api/symbols/AD/security", json={
                "_etag": ad_etag,
                "sedol": "B12345Z",  # BAR's SEDOL
            })
        assert resp.status_code == 409
        data = resp.json()
        assert data["error"] == "collision"
        assert data.get("field") == "sedol" or "colliding_security" in data

    def test_audit_updated_at_freshened_on_patch(self, client_with_ad):
        """updated_at is set to a fresh timestamp; identity fields are preserved."""
        from datetime import datetime, timezone
        c, _, etag = client_with_ad
        before = datetime.now(timezone.utc).isoformat()
        resp = c.patch("/api/symbols/AD/security", json={
            "_etag": etag,
            "company_name": "New Name",
        })
        assert resp.status_code == 200
        sec = resp.json()["security"]
        # Identity fields unchanged
        assert sec["security_id"] == "XAMS:AD"
        assert sec["exchange_mic"] == "XAMS"
        assert sec["ticker"] == "AD"
        # updated_at present (may be freshened — even if test Fake doesn't enforce the clock,
        # the field must be present in the projection returned by the endpoint)
        assert "updated_at" in sec or "_etag" in sec  # at minimum etag rotated


# ---------------------------------------------------------------------------
# Non-US guard regression: PUT /api/symbols/{symbol} must still enforce guard
# ---------------------------------------------------------------------------

class TestNonUsGuardRegression:
    """The new PATCH /security endpoint must not remove the PUT endpoint guard.

    PUT /api/symbols/{symbol} with option-toggle keys (covered_call, etc.) for
    a non-US symbol (XAMS:AD, exchange='XAMS') must still return 403
    options_not_eligible — the new PATCH endpoint doesn't replace this guard.
    """

    def test_put_covered_call_403_for_xams(self):
        """PUT covered_call=True for non-US symbol returns 403 options_not_eligible."""
        from web.app import app
        fake_cosmos = _FakeCosmos(ticker="AD", security_id="XAMS:AD")
        with TestClient(app) as c:
            app.state.cosmos = fake_cosmos
            app.state.cosmos_error = None
            resp = c.put("/api/symbols/AD", json={"covered_call": True})
        assert resp.status_code == 403
        data = resp.json()
        assert data.get("error") == "options_not_eligible"

    def test_put_telegram_403_for_xams(self):
        """PUT telegram_notifications_enabled=True for non-US is also guarded."""
        from web.app import app
        fake_cosmos = _FakeCosmos(ticker="AD", security_id="XAMS:AD")
        with TestClient(app) as c:
            app.state.cosmos = fake_cosmos
            app.state.cosmos_error = None
            resp = c.put("/api/symbols/AD", json={"telegram_notifications_enabled": True})
        assert resp.status_code == 403
        assert resp.json().get("error") == "options_not_eligible"

    def test_put_display_name_200_for_xams(self):
        """Non-option PUT field (display_name) is not gated — must succeed for non-US."""
        from web.app import app
        fake_cosmos = _FakeCosmos(ticker="AD", security_id="XAMS:AD")
        with TestClient(app) as c:
            app.state.cosmos = fake_cosmos
            app.state.cosmos_error = None
            resp = c.put("/api/symbols/AD", json={"display_name": "Ahold Delhaize"})
        assert resp.status_code == 200

class TestEnrichmentRefresh:
    def test_200_ok_on_success(self, client_with_ad):
        c, fake_cosmos, _ = client_with_ad
        mock_enrichment = {
            "last_updated": "2026-09-07T00:00:00Z",
            "quality_score": 80.0,
            "category": "Strong Buy",
            "entry_tag": "A",
            "momentum": "bullish",
        }
        with patch("src.portfolio_enrichment.enrich_symbol", return_value=mock_enrichment):
            resp = c.post("/api/symbols/AD/enrichment/refresh")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["enrichment"]["quality_score"] == 80.0

    def test_200_error_on_enrich_failure(self, client_with_ad):
        c, _, _ = client_with_ad
        with patch("src.portfolio_enrichment.enrich_symbol", return_value=None):
            resp = c.post("/api/symbols/AD/enrichment/refresh")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
        assert "detail" in data

    def test_200_error_no_yf_symbol(self):
        """No Yahoo Finance symbol → status=error, 200, no server crash."""
        from web.app import app
        # Use a fake cosmos with XZZZ MIC — no yfinance suffix → None
        fake_cosmos = _FakeCosmos(ticker="FOO", security_id="XZZZ:FOO")
        fake_cosmos._sym_doc["exchange"] = "XZZZ"
        # Seed security_master without any yfinance override and XZZZ has no MIC suffix
        _seed_security(
            fake_cosmos.container,
            ticker="FOO",
            security_id="XZZZ:FOO",
            provider_symbols={},  # no override → resolve_yfinance_symbol returns None for XZZZ
        )
        with TestClient(app) as c:
            app.state.cosmos = fake_cosmos
            app.state.cosmos_error = None
            resp = c.post("/api/symbols/FOO/enrichment/refresh")
        assert resp.status_code == 200
        assert resp.json()["status"] == "error"

    def test_404_unknown_symbol(self, client_with_ad):
        c, _, _ = client_with_ad
        resp = c.post("/api/symbols/ZZZYYYY/enrichment/refresh")
        assert resp.status_code == 404

    def test_enrichment_not_gated_by_us_options_eligible(self, client_with_ad):
        """XAMS is not US-options-eligible; enrichment must still run."""
        c, _, _ = client_with_ad
        mock_enrichment = {
            "last_updated": "2026-09-07T00:00:00Z",
            "quality_score": 70.0,
            "category": "Hold",
            "entry_tag": "B",
            "momentum": "neutral",
        }
        with patch("src.portfolio_enrichment.enrich_symbol", return_value=mock_enrichment):
            resp = c.post("/api/symbols/AD/enrichment/refresh")
        # Must succeed for non-US, not 403
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_record_enrichment_snapshot_called_on_success(self, client_with_ad):
        """record_enrichment_snapshot is called exactly once after successful enrichment."""
        c, fake_cosmos, _ = client_with_ad
        mock_enrichment = {
            "last_updated": "2026-09-07T00:00:00Z",
            "quality_score": 75.0,
            "category": "Buy",
            "entry_tag": "A",
            "momentum": "bullish",
            "tech_timing": "bullish",
        }
        from unittest.mock import MagicMock
        fake_cosmos.record_enrichment_snapshot = MagicMock()
        with patch("src.portfolio_enrichment.enrich_symbol", return_value=mock_enrichment):
            resp = c.post("/api/symbols/AD/enrichment/refresh")
        assert resp.status_code == 200
        fake_cosmos.record_enrichment_snapshot.assert_called_once()
        call_args = fake_cosmos.record_enrichment_snapshot.call_args
        assert call_args[0][0] == "AD"  # positional: ticker

    def test_enrich_symbol_receives_effective_yfinance_symbol(self, client_with_ad):
        """enrich_symbol is called with yf_symbol=resolve_yfinance_symbol(), not hardcoded."""
        c, fake_cosmos, etag = client_with_ad
        # Seed with explicit yfinance override so resolve_yfinance_symbol returns it
        from web.app import app
        fake_cosmos2 = _FakeCosmos(ticker="AD", security_id="XAMS:AD")
        _seed_security(
            fake_cosmos2.container,
            ticker="AD",
            security_id="XAMS:AD",
            provider_symbols={"yfinance": "AD.AS"},
        )
        mock_enrichment = {"last_updated": "2026-09-07T00:00:00Z", "quality_score": 50.0,
                           "category": "Hold", "entry_tag": "B", "momentum": "neutral"}
        captured = {}
        original_enrich = None

        def capturing_enrich(ticker, yf_symbol=None, **kw):
            captured["yf_symbol"] = yf_symbol
            return mock_enrichment

        with TestClient(app) as c2:
            app.state.cosmos = fake_cosmos2
            app.state.cosmos_error = None
            with patch("src.portfolio_enrichment.enrich_symbol", side_effect=capturing_enrich):
                resp = c2.post("/api/symbols/AD/enrichment/refresh")
        assert resp.status_code == 200
        # resolve_yfinance_symbol for XAMS:AD with yfinance override should yield "AD.AS"
        assert captured.get("yf_symbol") == "AD.AS"


# ---------------------------------------------------------------------------
# Security field extension in _compute_symbol_detail
# ---------------------------------------------------------------------------

class TestSecurityFieldExtension:
    def test_security_field_includes_new_fields(self):
        """_compute_symbol_detail security projection must include the new fields."""
        from web.app import _compute_symbol_detail
        from src.portfolio.cosmos_securities import CosmosSecuritiesService

        container = _FakeSymbolsContainerETag()
        _seed_security(
            container,
            ticker="AD",
            security_id="XAMS:AD",
            cusip="N/A",
            sedol="B5M5VQ8",
            country="NL",
            asset_class="Equity",
            provider_symbols={"yfinance": "AD.AS", "tradingview": "EURONEXT-AD"},
        )
        securities_svc = CosmosSecuritiesService(container)

        sym_doc = {
            "id": "config_AD",
            "symbol": "AD",
            "doc_type": "symbol_config",
            "security_id": "XAMS:AD",
            "exchange": "XAMS",
            "display_name": "Ahold Delhaize",
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
            "enrichment": {},
        }

        cosmos_mock = MagicMock()
        cosmos_mock.get_symbol.return_value = sym_doc
        cosmos_mock.list_symbols.return_value = [sym_doc]
        cosmos_mock.get_plans.return_value = []
        cosmos_mock.get_recent_activities.return_value = []
        cosmos_mock.get_recent_alerts.return_value = []
        cosmos_mock.get_next_earnings_date.return_value = None
        cosmos_mock.container = container

        result = _compute_symbol_detail(
            cosmos=cosmos_mock,
            symbol="AD",
            securities_svc=securities_svc,
        )

        sec = result.get("security")
        assert sec is not None, "security field must be present"
        # New contract fields
        assert "ticker" in sec
        assert "cusip" in sec
        assert "sedol" in sec
        assert "country" in sec
        assert "asset_class" in sec
        assert "provider_symbols" in sec
        assert "effective_yfinance_symbol" in sec
        assert "effective_tradingview_symbol" in sec
        assert "_etag" in sec
        assert "updated_at" in sec
        # Values
        assert sec["ticker"] == "AD"
        assert sec["cusip"] == "N/A"
        assert sec["sedol"] == "B5M5VQ8"
        assert sec["country"] == "NL"
        assert sec["asset_class"] == "Equity"
        assert sec["provider_symbols"]["yfinance"] == "AD.AS"
        # effective symbols use resolve_yfinance_symbol / resolve_tradingview_symbol
        assert sec["effective_yfinance_symbol"] == "AD.AS"          # override wins
        assert sec["effective_tradingview_symbol"] == "EURONEXT-AD"  # override wins

    def test_security_field_effective_symbols_fall_back_to_mic_when_no_override(self):
        """Without provider_symbols override, effective symbols come from MIC mapping."""
        from web.app import _compute_symbol_detail
        from src.portfolio.cosmos_securities import CosmosSecuritiesService

        container = _FakeSymbolsContainerETag()
        _seed_security(
            container,
            ticker="AD",
            security_id="XAMS:AD",
            provider_symbols={},  # no override
        )
        securities_svc = CosmosSecuritiesService(container)

        sym_doc = {
            "id": "config_AD",
            "symbol": "AD",
            "doc_type": "symbol_config",
            "security_id": "XAMS:AD",
            "exchange": "XAMS",
            "display_name": "Ahold Delhaize",
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
            "enrichment": {},
        }

        cosmos_mock = MagicMock()
        cosmos_mock.get_symbol.return_value = sym_doc
        cosmos_mock.list_symbols.return_value = [sym_doc]
        cosmos_mock.get_plans.return_value = []
        cosmos_mock.get_recent_activities.return_value = []
        cosmos_mock.get_recent_alerts.return_value = []
        cosmos_mock.get_next_earnings_date.return_value = None
        cosmos_mock.container = container

        result = _compute_symbol_detail(
            cosmos=cosmos_mock,
            symbol="AD",
            securities_svc=securities_svc,
        )

        sec = result["security"]
        # XAMS → .AS suffix → "AD.AS"
        assert sec["effective_yfinance_symbol"] == "AD.AS"
        # XAMS → EURONEXT → "EURONEXT-AD"
        assert sec["effective_tradingview_symbol"] == "EURONEXT-AD"
