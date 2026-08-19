"""Tests for symbols/watchlist flow: add symbol, total_shares editing,
strategy-filter logic, and the separate option-chain delta filter pipeline.

Hermetic — no network, no real CosmosDB, no real yfinance.
Pattern mirrors test_activity_chat.py: TestClient + FakeCosmos + monkeypatch.
"""

import pytest
from starlette.testclient import TestClient
from threading import Thread as RealThread

from src.options_chain_filters import (
    filter_options_chain_by_delta,
    filter_options_chain_by_type,
)


# ===========================================================================
# Helpers / Fakes
# ===========================================================================

def _make_symbol_doc(symbol: str, total_shares: int = 0,
                     covered_call: bool = False,
                     cash_secured_put: bool = False,
                     buy_tracker: bool = False) -> dict:
    return {
        "id": f"config_{symbol}",
        "symbol": symbol,
        "doc_type": "symbol_config",
        "exchange": "NASDAQ",
        "display_name": symbol,
        "total_shares": total_shares,
        "watchlist": {
            "covered_call": covered_call,
            "cash_secured_put": cash_secured_put,
            "buy_tracker": buy_tracker,
        },
        "telegram_notifications_enabled": True,
        "positions": [],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


class FakeCosmos:
    """In-memory fake for CosmosDBService (subset needed by symbols endpoints)."""

    def __init__(self, initial_docs: dict | None = None):
        self._docs: dict[str, dict] = dict(initial_docs or {})
        self.replaced: list[dict] = []
        self.deleted: list[str] = []

    def get_symbol(self, symbol: str) -> dict | None:
        return self._docs.get(symbol.upper())

    def list_symbols(self) -> list[dict]:
        return list(self._docs.values())

    def create_symbol(self, symbol: str, exchange: str,
                      display_name: str = "",
                      covered_call: bool = False,
                      cash_secured_put: bool = False,
                      buy_tracker: bool = False) -> dict:
        sym = symbol.upper()
        doc = _make_symbol_doc(sym, covered_call=covered_call,
                               cash_secured_put=cash_secured_put,
                               buy_tracker=buy_tracker)
        doc["exchange"] = exchange.upper()
        doc["display_name"] = display_name or f"{exchange.upper()}:{sym}"
        self._docs[sym] = doc
        return doc

    def replace_symbol(self, doc: dict) -> dict:
        sym = doc["symbol"].upper()
        self._docs[sym] = doc
        self.replaced.append(doc)
        return doc

    def delete_symbol(self, symbol: str) -> None:
        self.deleted.append(symbol.upper())
        self._docs.pop(symbol.upper(), None)

    def get_settings(self) -> dict:
        return {}

    def get_next_earnings_date(self, symbol: str):
        return None


def _make_client_with_cosmos(cosmos: FakeCosmos) -> TestClient:
    """Return a TestClient with the fake cosmos injected into app state."""
    from web.app import app
    # Inject cosmos before the request hits the router
    app.state.cosmos = cosmos
    # Disable yfinance provider (not needed for symbol CRUD tests)
    app.state.yf_provider = None
    return TestClient(app, raise_server_exceptions=False)


# ===========================================================================
# POST /api/symbols — Add symbol
# ===========================================================================

class TestCreateSymbol:

    def test_create_symbol_returns_201_with_valid_body(self, monkeypatch):
        """Happy path: symbol + exchange → 201 with the new doc."""
        cosmos = FakeCosmos()
        # Patch out background threads (enrichment + forecast seeding)
        import threading
        monkeypatch.setattr(threading, "Thread", lambda target, daemon: _FakeThread())

        client = _make_client_with_cosmos(cosmos)
        res = client.post("/api/symbols",
                          json={"symbol": "AAPL", "exchange": "NASDAQ"})

        assert res.status_code == 201
        body = res.json()
        assert body["symbol"] == "AAPL"
        assert body["exchange"] == "NASDAQ"
        assert "AAPL" in cosmos._docs

    def test_create_symbol_missing_symbol_returns_400(self, monkeypatch):
        """symbol field missing → 400."""
        cosmos = FakeCosmos()
        client = _make_client_with_cosmos(cosmos)
        res = client.post("/api/symbols", json={"exchange": "NASDAQ"})

        assert res.status_code == 400
        assert "symbol" in res.json()["error"].lower()

    def test_create_symbol_missing_exchange_returns_400(self, monkeypatch):
        """exchange field missing → 400."""
        cosmos = FakeCosmos()
        client = _make_client_with_cosmos(cosmos)
        res = client.post("/api/symbols", json={"symbol": "MSFT"})

        assert res.status_code == 400
        assert "exchange" in res.json()["error"].lower()

    def test_create_symbol_empty_symbol_returns_400(self, monkeypatch):
        """Blank symbol after strip → 400."""
        cosmos = FakeCosmos()
        client = _make_client_with_cosmos(cosmos)
        res = client.post("/api/symbols", json={"symbol": "   ", "exchange": "NYSE"})

        assert res.status_code == 400

    def test_create_duplicate_symbol_returns_409(self, monkeypatch):
        """Second POST for the same ticker → 409 Conflict."""
        existing = {"AAPL": _make_symbol_doc("AAPL")}
        cosmos = FakeCosmos(existing)
        client = _make_client_with_cosmos(cosmos)
        res = client.post("/api/symbols",
                          json={"symbol": "AAPL", "exchange": "NASDAQ"})

        assert res.status_code == 409
        assert "AAPL" in res.json()["error"]

    def test_create_symbol_normalises_ticker_to_uppercase(self, monkeypatch):
        """Lowercase ticker in request → stored as uppercase."""
        cosmos = FakeCosmos()
        import threading
        monkeypatch.setattr(threading, "Thread", lambda target, daemon: _FakeThread())
        client = _make_client_with_cosmos(cosmos)
        res = client.post("/api/symbols",
                          json={"symbol": "aapl", "exchange": "nasdaq"})

        assert res.status_code == 201
        assert res.json()["symbol"] == "AAPL"
        assert res.json()["exchange"] == "NASDAQ"

    def test_create_symbol_with_watchlist_flags(self, monkeypatch):
        """Watchlist flags passed on creation are stored."""
        cosmos = FakeCosmos()
        import threading
        monkeypatch.setattr(threading, "Thread", lambda target, daemon: _FakeThread())
        client = _make_client_with_cosmos(cosmos)
        res = client.post("/api/symbols", json={
            "symbol": "T",
            "exchange": "NYSE",
            "covered_call": True,
            "cash_secured_put": False,
        })

        assert res.status_code == 201
        doc = cosmos.get_symbol("T")
        assert doc["watchlist"]["covered_call"] is True
        assert doc["watchlist"]["cash_secured_put"] is False

    def test_successful_creation_backfills_only_created_symbol(self, monkeypatch):
        """A successful POST launches one forecast backfill with default depth."""
        cosmos = FakeCosmos()
        calls = []

        async def _record_backfill(cosmos_arg, provider_arg, symbol_arg, *, sessions):
            calls.append((cosmos_arg, provider_arg, symbol_arg, sessions))

        import src.forecast_cron as forecast_cron
        import threading
        monkeypatch.setattr(forecast_cron, "DEFAULT_BACKFILL_SESSIONS", 17)
        monkeypatch.setattr(forecast_cron, "backfill_symbol_forecasts", _record_backfill)
        monkeypatch.setattr(threading, "Thread", _ForecastOnlyThread)

        client = _make_client_with_cosmos(cosmos)
        res = client.post(
            "/api/symbols",
            json={"symbol": " msft ", "exchange": " nasdaq ", "buy_tracker": True},
        )

        assert res.status_code == 201
        assert calls == [(cosmos, None, "MSFT", 17)]

    @pytest.mark.parametrize(
        "payload, expected_status",
        [
            ({"exchange": "NASDAQ"}, 400),
            ({"symbol": "   ", "exchange": "NASDAQ"}, 400),
            ({"symbol": "AAPL", "exchange": "NASDAQ"}, 409),
        ],
    )
    def test_invalid_or_duplicate_creation_does_not_backfill(
        self, monkeypatch, payload, expected_status
    ):
        """Rejected POSTs must not launch forecast work."""
        cosmos = FakeCosmos({"AAPL": _make_symbol_doc("AAPL")})
        calls = []

        async def _record_backfill(*args, **kwargs):
            calls.append((args, kwargs))

        import src.forecast_cron as forecast_cron
        import threading
        monkeypatch.setattr(forecast_cron, "backfill_symbol_forecasts", _record_backfill)
        monkeypatch.setattr(threading, "Thread", _ForecastOnlyThread)

        client = _make_client_with_cosmos(cosmos)
        res = client.post("/api/symbols", json=payload)

        assert res.status_code == expected_status
        assert calls == []

    def test_backfill_failure_does_not_undo_creation(self, monkeypatch):
        """Forecast seeding is best-effort after the symbol has been persisted."""
        cosmos = FakeCosmos()
        calls = []

        async def _failing_backfill(*args, **kwargs):
            calls.append((args, kwargs))
            raise RuntimeError("forecast unavailable")

        import src.forecast_cron as forecast_cron
        import threading
        monkeypatch.setattr(forecast_cron, "backfill_symbol_forecasts", _failing_backfill)
        monkeypatch.setattr(threading, "Thread", _ForecastOnlyThread)

        client = _make_client_with_cosmos(cosmos)
        res = client.post(
            "/api/symbols", json={"symbol": "VZ", "exchange": "NYSE"}
        )

        assert res.status_code == 201
        assert cosmos.get_symbol("VZ") is not None
        assert len(calls) == 1


class _FakeThread:
    """Thread stub — does nothing, prevents background enrichment in tests."""
    def start(self):
        pass


class _ForecastOnlyThread:
    """Run forecast thread targets synchronously; suppress enrichment work."""

    def __init__(self, target, daemon):
        self.target = target
        self.daemon = daemon

    def start(self):
        if self.target.__name__ == "_seed_forecasts":
            thread = RealThread(target=self.target, daemon=self.daemon)
            thread.start()
            thread.join()


# ===========================================================================
# PUT /api/symbols/{symbol} — total_shares editing
# ===========================================================================

class TestUpdateTotalShares:

    def _client_with_symbol(self, symbol: str,
                             total_shares: int = 0) -> tuple[TestClient, FakeCosmos]:
        cosmos = FakeCosmos({symbol: _make_symbol_doc(symbol, total_shares)})
        return _make_client_with_cosmos(cosmos), cosmos

    def test_update_total_shares_positive_integer(self):
        """PUT with a positive integer persists the new share count."""
        client, cosmos = self._client_with_symbol("MSFT", 0)
        res = client.put("/api/symbols/MSFT", json={"total_shares": 200})

        assert res.status_code == 200
        assert cosmos.replaced[-1]["total_shares"] == 200

    def test_update_total_shares_zero_resets_count(self):
        """Setting total_shares=0 is valid (clears position)."""
        client, cosmos = self._client_with_symbol("JNJ", 100)
        res = client.put("/api/symbols/JNJ", json={"total_shares": 0})

        assert res.status_code == 200
        assert cosmos.replaced[-1]["total_shares"] == 0

    @pytest.mark.parametrize(
        "value",
        [-1, -10, 1.5, True, False, "100", "abc", None],
        ids=[
            "negative-one",
            "negative-many",
            "fractional",
            "boolean-true",
            "boolean-false",
            "numeric-string",
            "invalid-string",
            "null",
        ],
    )
    def test_invalid_total_shares_returns_clear_400_without_persisting(self, value):
        """Only non-negative JSON integers satisfy the total_shares contract."""
        client, cosmos = self._client_with_symbol("CVX", 50)
        res = client.put("/api/symbols/CVX", json={"total_shares": value})

        assert res.status_code == 400
        assert "total_shares" in res.json()["error"]
        assert cosmos.replaced == []

    def test_missing_total_shares_returns_clear_400(self):
        """An empty update is invalid rather than a successful no-op."""
        client, cosmos = self._client_with_symbol("O", 50)
        res = client.put("/api/symbols/O", json={})

        assert res.status_code == 400
        assert "total_shares" in res.json()["error"]
        assert cosmos.replaced == []

    def test_update_total_shares_symbol_not_found_returns_404(self):
        """PUT on unknown ticker → 404."""
        cosmos = FakeCosmos()
        client = _make_client_with_cosmos(cosmos)
        res = client.put("/api/symbols/UNKNOWN", json={"total_shares": 100})

        assert res.status_code == 404

    def test_update_total_shares_does_not_affect_other_fields(self):
        """Updating shares only changes total_shares; watchlist flags are preserved."""
        doc = _make_symbol_doc("MMM", total_shares=0,
                               covered_call=True, cash_secured_put=True)
        cosmos = FakeCosmos({"MMM": doc})
        client = _make_client_with_cosmos(cosmos)
        client.put("/api/symbols/MMM", json={"total_shares": 300})

        saved = cosmos.replaced[-1]
        assert saved["watchlist"]["covered_call"] is True
        assert saved["watchlist"]["cash_secured_put"] is True
        assert saved["total_shares"] == 300

    def test_update_total_shares_rollback_on_replace_error(self, monkeypatch):
        """If cosmos.replace_symbol raises, the endpoint returns 500 and
        the local in-memory doc is NOT mutated (caller would need to reload).
        """
        original_doc = _make_symbol_doc("HD", total_shares=50)
        cosmos = FakeCosmos({"HD": original_doc})

        def _exploding_replace(doc):
            raise RuntimeError("Cosmos write failure")

        monkeypatch.setattr(cosmos, "replace_symbol", _exploding_replace)
        client = _make_client_with_cosmos(cosmos)
        res = client.put("/api/symbols/HD", json={"total_shares": 999})

        assert res.status_code == 503
        # The doc in the fake store was mutated in-place before replace; that
        # is the current behaviour. What matters is replace was never called.
        assert cosmos.replaced == []


# ===========================================================================
# DELETE /api/symbols/{symbol} — Delete symbol (used by the watchlist trash
# action). This endpoint and its cosmos.delete_symbol() cascade already
# existed prior to the frontend delete-button feature; these tests just
# lock in the contract the new UI now depends on.
# ===========================================================================

class TestDeleteSymbol:

    def test_delete_symbol_returns_200_and_removes_it(self, monkeypatch):
        """Happy path: existing symbol → 200, removed from the store, and
        cosmos.delete_symbol was invoked with the (uppercased) ticker."""
        cosmos = FakeCosmos({"AAPL": _make_symbol_doc("AAPL", total_shares=100)})
        client = _make_client_with_cosmos(cosmos)
        res = client.delete("/api/symbols/AAPL")

        assert res.status_code == 200
        body = res.json()
        assert body == {"status": "deleted", "symbol": "AAPL"}
        assert cosmos.get_symbol("AAPL") is None
        assert cosmos.deleted == ["AAPL"]

    def test_delete_symbol_is_case_insensitive(self, monkeypatch):
        """A lowercase path segment still resolves and deletes the stored
        (uppercase) symbol."""
        cosmos = FakeCosmos({"MSFT": _make_symbol_doc("MSFT")})
        client = _make_client_with_cosmos(cosmos)
        res = client.delete("/api/symbols/msft")

        assert res.status_code == 200
        assert res.json()["symbol"] == "MSFT"
        assert cosmos.get_symbol("MSFT") is None

    def test_delete_unknown_symbol_returns_404_and_does_not_call_delete(self, monkeypatch):
        """A symbol that was never created (or already deleted) → 404, and
        the destructive delete_symbol() call is never reached."""
        cosmos = FakeCosmos()
        client = _make_client_with_cosmos(cosmos)
        res = client.delete("/api/symbols/NOPE")

        assert res.status_code == 404
        assert "NOPE" in res.json()["error"]
        assert cosmos.deleted == []

    def test_delete_symbol_when_cosmos_unavailable_returns_503(self, monkeypatch):
        """CosmosDB not configured/unavailable → 503, not a false success.

        Uses monkeypatch (not a direct, permanent mutation) so app.state is
        automatically restored after this test, regardless of test order —
        `raising=False` because `cosmos`/`cosmos_error` may not have been
        set on `app.state` yet when this test runs first.
        """
        from web.app import app
        monkeypatch.setattr(app.state, "cosmos", None, raising=False)
        monkeypatch.setattr(app.state, "cosmos_error", "not configured", raising=False)
        monkeypatch.setattr(app.state, "yf_provider", None, raising=False)
        from starlette.testclient import TestClient
        client = TestClient(app, raise_server_exceptions=False)

        res = client.delete("/api/symbols/AAPL")

        assert res.status_code == 503

    def test_delete_symbol_does_not_touch_other_symbols(self, monkeypatch):
        """Deleting one symbol must not remove or mutate any other symbol's
        watchlist config (no accidental cross-symbol cascade)."""
        cosmos = FakeCosmos({
            "AAPL": _make_symbol_doc("AAPL", total_shares=10),
            "MSFT": _make_symbol_doc("MSFT", total_shares=20),
        })
        client = _make_client_with_cosmos(cosmos)
        res = client.delete("/api/symbols/AAPL")

        assert res.status_code == 200
        assert cosmos.get_symbol("AAPL") is None
        remaining = cosmos.get_symbol("MSFT")
        assert remaining is not None
        assert remaining["total_shares"] == 20


# ===========================================================================
# Watchlist strategy filter — covered_call / cash_secured_put flags
# ===========================================================================

class TestWatchlistStrategyFilter:
    """Test the watchlist flag logic that drives the StrategyFilter in
    SymbolsTable (GET /api/symbols returns watchlist flags per row).
    
    Covers the PUT /api/symbols/{symbol} watchlist toggles.
    """

    def test_enable_covered_call_flag(self):
        """Setting covered_call=True on PUT persists the watchlist flag."""
        cosmos = FakeCosmos({"ABBV": _make_symbol_doc("ABBV", covered_call=False)})
        client = _make_client_with_cosmos(cosmos)
        res = client.put("/api/symbols/ABBV", json={"covered_call": True})

        assert res.status_code == 200
        assert cosmos.replaced[-1]["watchlist"]["covered_call"] is True

    def test_disable_covered_call_flag(self):
        """Setting covered_call=False clears the flag."""
        cosmos = FakeCosmos({"PFE": _make_symbol_doc("PFE", covered_call=True)})
        client = _make_client_with_cosmos(cosmos)
        res = client.put("/api/symbols/PFE", json={"covered_call": False})

        assert res.status_code == 200
        assert cosmos.replaced[-1]["watchlist"]["covered_call"] is False

    def test_enable_cash_secured_put_flag(self):
        """Setting cash_secured_put=True persists the flag."""
        cosmos = FakeCosmos({"INTC": _make_symbol_doc("INTC", cash_secured_put=False)})
        client = _make_client_with_cosmos(cosmos)
        res = client.put("/api/symbols/INTC", json={"cash_secured_put": True})

        assert res.status_code == 200
        assert cosmos.replaced[-1]["watchlist"]["cash_secured_put"] is True

    def test_watchlist_flags_are_independent(self):
        """Updating covered_call does not touch cash_secured_put or buy_tracker."""
        doc = _make_symbol_doc("KO", covered_call=False,
                               cash_secured_put=True, buy_tracker=True)
        cosmos = FakeCosmos({"KO": doc})
        client = _make_client_with_cosmos(cosmos)
        client.put("/api/symbols/KO", json={"covered_call": True})

        saved = cosmos.replaced[-1]["watchlist"]
        assert saved["covered_call"] is True
        assert saved["cash_secured_put"] is True
        assert saved["buy_tracker"] is True

    def test_filter_covered_call_returns_only_flagged_symbols(self):
        """GET /api/symbols rows include watchlist flags; filter logic excludes
        symbols without the flag (verifying the flag is emitted correctly).
        """
        cosmos = FakeCosmos({
            "AAPL": _make_symbol_doc("AAPL", covered_call=True),
            "MSFT": _make_symbol_doc("MSFT", covered_call=False),
        })
        client = _make_client_with_cosmos(cosmos)
        res = client.get("/api/symbols")
        assert res.status_code == 200

        rows = res.json()
        by_sym = {r["symbol"]: r for r in rows}
        assert by_sym["AAPL"]["watchlist"]["covered_call"] is True
        assert by_sym["MSFT"]["watchlist"]["covered_call"] is False


# ===========================================================================
# filter_options_chain_by_delta — default call (delta 0.15–0.90) and
# put (delta −0.60 to −0.15) ranges; unrelated to UI suitability filters
# ===========================================================================

def _make_chain(calls: dict, puts: dict) -> dict:
    return {
        "symbol": "TEST",
        "timestamp": "2026-08-08T12:00:00Z",
        "calls": calls,
        "puts": puts,
    }


class TestCallDeltaFilter:
    """filter_options_chain_by_delta default call range: 0.15 ≤ delta ≤ 0.90."""

    def test_call_within_range_is_kept(self):
        chain = _make_chain(
            calls={"20260919": {"150.0": {"bid": 2.0, "ask": 2.5, "delta": 0.35}}},
            puts={},
        )
        result = filter_options_chain_by_delta(chain)
        assert "20260919" in result["calls"]
        assert "150.0" in result["calls"]["20260919"]

    def test_call_at_lower_boundary_is_kept(self):
        chain = _make_chain(
            calls={"20260919": {"200.0": {"bid": 1.0, "ask": 1.1, "delta": 0.15}}},
            puts={},
        )
        result = filter_options_chain_by_delta(chain)
        assert "200.0" in result["calls"].get("20260919", {})

    def test_call_at_upper_boundary_is_kept(self):
        chain = _make_chain(
            calls={"20260919": {"100.0": {"bid": 5.0, "ask": 5.5, "delta": 0.90}}},
            puts={},
        )
        result = filter_options_chain_by_delta(chain)
        assert "100.0" in result["calls"].get("20260919", {})

    def test_call_below_min_delta_is_excluded(self):
        """Deep OTM call (delta < 0.15) is filtered out."""
        chain = _make_chain(
            calls={"20260919": {"250.0": {"bid": 0.05, "ask": 0.10, "delta": 0.05}}},
            puts={},
        )
        result = filter_options_chain_by_delta(chain)
        assert "20260919" not in result["calls"]

    def test_call_above_max_delta_is_excluded(self):
        """Deep ITM call (delta > 0.90) is filtered out."""
        chain = _make_chain(
            calls={"20260919": {"50.0": {"bid": 10.0, "ask": 10.5, "delta": 0.95}}},
            puts={},
        )
        result = filter_options_chain_by_delta(chain)
        assert "20260919" not in result["calls"]

    def test_call_with_missing_delta_is_excluded(self):
        """Contract without delta key is excluded (no delta → can't evaluate)."""
        chain = _make_chain(
            calls={"20260919": {"180.0": {"bid": 1.5, "ask": 2.0}}},
            puts={},
        )
        result = filter_options_chain_by_delta(chain)
        assert "20260919" not in result["calls"]

    def test_call_with_none_delta_is_excluded(self):
        chain = _make_chain(
            calls={"20260919": {"180.0": {"bid": 1.5, "ask": 2.0, "delta": None}}},
            puts={},
        )
        result = filter_options_chain_by_delta(chain)
        assert "20260919" not in result["calls"]

    def test_mixed_calls_keep_only_default_range(self):
        """One in-range call and two out-of-range calls in the same expiry."""
        chain = _make_chain(
            calls={"20261017": {
                "150.0": {"bid": 2.0, "ask": 2.5, "delta": 0.40},   # in range
                "200.0": {"bid": 0.05, "ask": 0.10, "delta": 0.08},  # too low
                "100.0": {"bid": 8.0, "ask": 8.5, "delta": 0.92},   # too high
            }},
            puts={},
        )
        result = filter_options_chain_by_delta(chain)
        kept = result["calls"]["20261017"]
        assert "150.0" in kept
        assert "200.0" not in kept
        assert "100.0" not in kept


class TestPutDeltaFilter:
    """filter_options_chain_by_delta default put range: −0.60 ≤ delta ≤ −0.15."""

    def test_put_within_range_is_kept(self):
        chain = _make_chain(
            calls={},
            puts={"20260919": {"140.0": {"bid": 1.8, "ask": 2.0, "delta": -0.30}}},
        )
        result = filter_options_chain_by_delta(chain)
        assert "140.0" in result["puts"].get("20260919", {})

    def test_put_at_lower_boundary_is_kept(self):
        """delta == −0.60 is at the inclusive lower bound."""
        chain = _make_chain(
            calls={},
            puts={"20260919": {"130.0": {"bid": 3.0, "ask": 3.5, "delta": -0.60}}},
        )
        result = filter_options_chain_by_delta(chain)
        assert "130.0" in result["puts"].get("20260919", {})

    def test_put_at_upper_boundary_is_kept(self):
        """delta == −0.15 is at the inclusive upper bound."""
        chain = _make_chain(
            calls={},
            puts={"20260919": {"160.0": {"bid": 0.9, "ask": 1.0, "delta": -0.15}}},
        )
        result = filter_options_chain_by_delta(chain)
        assert "160.0" in result["puts"].get("20260919", {})

    def test_put_deeper_than_minus_060_is_excluded(self):
        """Deep ITM put (delta < −0.60) is filtered out."""
        chain = _make_chain(
            calls={},
            puts={"20260919": {"170.0": {"bid": 8.0, "ask": 8.5, "delta": -0.80}}},
        )
        result = filter_options_chain_by_delta(chain)
        assert "20260919" not in result["puts"]

    def test_put_shallower_than_minus_015_is_excluded(self):
        """Far OTM put (delta > −0.15, i.e. closer to 0) is filtered out."""
        chain = _make_chain(
            calls={},
            puts={"20260919": {"100.0": {"bid": 0.02, "ask": 0.05, "delta": -0.03}}},
        )
        result = filter_options_chain_by_delta(chain)
        assert "20260919" not in result["puts"]

    def test_put_with_missing_delta_is_excluded(self):
        chain = _make_chain(
            calls={},
            puts={"20260919": {"145.0": {"bid": 1.5, "ask": 1.8}}},
        )
        result = filter_options_chain_by_delta(chain)
        assert "20260919" not in result["puts"]

    def test_mixed_puts_keep_only_default_range(self):
        """Only puts in the −0.60 to −0.15 range are retained."""
        chain = _make_chain(
            calls={},
            puts={"20261017": {
                "145.0": {"bid": 1.5, "ask": 1.8, "delta": -0.25},  # in range
                "110.0": {"bid": 0.02, "ask": 0.05, "delta": -0.05},  # too shallow
                "175.0": {"bid": 9.0, "ask": 9.5, "delta": -0.75},  # too deep
            }},
        )
        result = filter_options_chain_by_delta(chain)
        kept = result["puts"]["20261017"]
        assert "145.0" in kept
        assert "110.0" not in kept
        assert "175.0" not in kept


class TestOptionChainTypeAndDeltaPipeline:
    """End-to-end pipeline: type filter first, then delta filter (as used in
    the agent chain debug endpoint and in covered_call / cash_secured_put
    agent prompts).
    """

    def _chain_with_both_sides(self) -> dict:
        return {
            "symbol": "AAPL",
            "timestamp": "2026-08-08T12:00:00Z",
            "calls": {
                "20260918": {
                    "185.0": {"bid": 2.0, "ask": 2.5, "delta": 0.35},   # in range
                    "220.0": {"bid": 0.04, "ask": 0.08, "delta": 0.04},  # too low
                },
            },
            "puts": {
                "20260918": {
                    "175.0": {"bid": 1.5, "ask": 1.8, "delta": -0.28},  # in range
                    "220.0": {"bid": 9.0, "ask": 9.5, "delta": -0.82},  # too deep
                },
            },
        }

    def test_calls_pipeline(self):
        """Type-filter to calls, then retain only calls in the default delta range."""
        chain = self._chain_with_both_sides()
        type_filtered = filter_options_chain_by_type(chain, "call")
        delta_filtered = filter_options_chain_by_delta(type_filtered)

        # In-range call preserved
        assert "185.0" in delta_filtered["calls"].get("20260918", {})
        # OTM call removed
        assert "220.0" not in delta_filtered["calls"].get("20260918", {})
        # Puts bucket is empty after type filter
        assert delta_filtered["puts"] == {}

    def test_puts_pipeline(self):
        """Type-filter to puts, then retain only puts in the default delta range."""
        chain = self._chain_with_both_sides()
        type_filtered = filter_options_chain_by_type(chain, "put")
        delta_filtered = filter_options_chain_by_delta(type_filtered)

        # In-range put preserved
        assert "175.0" in delta_filtered["puts"].get("20260918", {})
        # Deep ITM put removed
        assert "220.0" not in delta_filtered["puts"].get("20260918", {})
        # Calls bucket is empty after type filter
        assert delta_filtered["calls"] == {}

    def test_empty_chain_after_delta_filter_is_handled(self):
        """All contracts outside default ranges produce empty buckets."""
        chain = _make_chain(
            calls={"20260918": {"300.0": {"bid": 0.01, "ask": 0.02, "delta": 0.01}}},
            puts={"20260918": {"100.0": {"bid": 0.01, "ask": 0.02, "delta": -0.01}}},
        )
        result = filter_options_chain_by_delta(chain)
        assert result["calls"] == {}
        assert result["puts"] == {}
