"""Basher's adversarial seam suite for `GET /api/symbols/{symbol}/best-options`
(`backend/web/app.py`), exercising the REAL FastAPI endpoint together with a
REAL `OptionsChainCache` and the REAL `evaluate_best_options` evaluator --
per this task's instruction to use real modules across the evaluator/cache/
API seam rather than mocking the seam itself. Only the true edges are
faked: Cosmos (`FakeCosmos`, no real DB) and the options-chain data
providers (`_fetch_yfinance`/`_fetch_tradingview`, monkeypatched exactly
like `tests/test_options_chain_cache.py` already does -- no network calls).

Design reference: `.squad/decisions/inbox/danny-best-options-design.md`
(accepted 2026-08-29), especially finding F6 (a cold cache must never
block the event loop on a live provider fetch) and acceptance gate #1
(zero LLM calls reachable from this endpoint).

Hermetic: no network, no real Cosmos, no real LLM. The process-wide
options-chain-cache singleton is saved/restored around every test via an
autouse fixture, since `set_options_chain_cache`/`get_options_chain_cache`
share process-global state with every other test file that touches it.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from src.best_options import evaluate_best_options
from src.options_chain_cache import (
    OptionsChainCache,
    get_options_chain_cache,
    set_options_chain_cache,
)
from src.options_chain_store import OptionsChainStore
from web.app import app

# The endpoint calls `evaluate_best_options` with the REAL wall-clock time
# (`datetime.now(timezone.utc)`), not an injectable `now` -- unlike
# `test_best_options_adversarial.py`'s evaluator-level tests, fixture
# expirations here must be anchored to the actual current date, not a
# fixed constant, or every DTE-window-dependent assertion would silently
# depend on when this suite happens to be run relative to a hardcoded date.
def _real_now() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> date:
    return _real_now().date()


def _exp_key(days: int) -> str:
    return (_today() + timedelta(days=days)).strftime("%Y%m%d")


def _contract(bid=1.5, ask=1.6, strike=105.0, oi=500):
    # Deliberately no `delta`/other Greeks here: the real cache pipeline's
    # `recompute_derived` (Linus's frozen `options_chain_merge` interface)
    # is documented as the SOLE writer of mid/delta/gamma/theta/vega/rho --
    # any Greeks a raw provider payload carries are discarded and
    # recomputed from strike/iv/underlying_price/dte, so supplying a
    # target delta directly here would silently be thrown away. Strikes
    # below are empirically chosen (via the real pipeline, at
    # underlying=100.0, iv=0.30, ~20 DTE) so the RECOMPUTED delta lands
    # in-band for both `balanced` and `high_yield` category thresholds:
    # call strike 105.0 -> delta ~0.260 (in [0.20,0.30] and [0.25,0.35]);
    # put strike 96.0 -> delta ~-0.253 (abs in both same bands).
    mid = round((bid + ask) / 2, 4)
    return {
        "strike": strike, "bid": bid, "ask": ask, "mid": mid, "iv": 0.30,
        "lastPrice": bid, "openInterest": oi, "volume": 10, "inTheMoney": False,
    }


def _sample_chain(symbol="TEST"):
    return {
        "symbol": symbol,
        "timestamp": "2026-08-29T11:00:00Z",
        "underlying_price": 100.0,
        "calls": {_exp_key(20): {"105.0": _contract(bid=1.2, ask=1.3, strike=105.0)}},
        "puts": {_exp_key(20): {"96.0": _contract(bid=1.0, ask=1.05, strike=96.0)}},
    }


class FakeCosmos:
    """Independently-authored fake -- deliberately not imported from
    `test_activity_chat.py`'s `FakeCosmos` (this task's "avoid mutual
    fakes" instruction), and extended with the two Best-Options-specific
    calendar lookups the endpoint actually calls."""

    def __init__(self):
        self.symbols = {}
        self.earnings_dates = {}
        self.ex_dividend_dates = {}

    def get_symbol(self, symbol):
        return self.symbols.get(symbol)

    def get_next_earnings_date(self, symbol):
        return self.earnings_dates.get(symbol)

    def get_next_calendar_event_date(self, symbol, event_type):
        if event_type == "ex_dividend":
            return self.ex_dividend_dates.get(symbol)
        return None


def _make_cache(monkeypatch, *, yf_chain=None, tv_chain=None):
    """A real `OptionsChainCache`, hermetic persistence (disabled store,
    so no Cosmos/config side effects), with its two live-data seams
    (`_fetch_yfinance`/`_fetch_tradingview`) monkeypatched to fast in-memory
    fakes -- the exact pattern `tests/test_options_chain_cache.py` already
    establishes, reused here (not duplicated as a competing convention) so
    a real cache instance never reaches the network."""
    cache = OptionsChainCache(ttl_seconds=1800, store=OptionsChainStore(enabled=False))

    async def _fake_yf(symbol):
        return yf_chain if yf_chain is not None else {"symbol": symbol, "calls": {}, "puts": {}}

    async def _fake_tv(symbol):
        return tv_chain if tv_chain is not None else {"symbol": symbol, "calls": {}, "puts": {}}

    monkeypatch.setattr(cache, "_fetch_yfinance", _fake_yf)
    monkeypatch.setattr(cache, "_fetch_tradingview", _fake_tv)
    return cache


@pytest.fixture(autouse=True)
def _isolate_shared_cache_singleton():
    """`get_options_chain_cache()`/`set_options_chain_cache()` share
    process-global state with every other test file. Save/restore it
    around every test in this file so nothing here leaks into (or is
    leaked into by) test_options_chain_cache.py or a real server run."""
    import src.options_chain_cache as occ_module
    saved = occ_module._shared_cache
    yield
    set_options_chain_cache(saved)


@pytest.fixture
def client_and_cosmos(monkeypatch):
    fake_cosmos = FakeCosmos()
    app.router.on_startup = []  # skip real Cosmos/provider startup wiring
    app.state.cosmos = fake_cosmos
    client = TestClient(app, raise_server_exceptions=False)
    return client, fake_cosmos


class TestSymbolNotFound:
    def test_unknown_symbol_returns_404(self, client_and_cosmos):
        client, fake_cosmos = client_and_cosmos
        resp = client.get("/api/symbols/NOPE/best-options")
        assert resp.status_code == 404


class TestQueryParamValidation:
    def test_invalid_side_returns_400(self, client_and_cosmos):
        client, fake_cosmos = client_and_cosmos
        fake_cosmos.symbols["TEST"] = {"enrichment": {"category": "balanced"}, "total_shares": 0}
        resp = client.get("/api/symbols/TEST/best-options", params={"side": "bogus"})
        assert resp.status_code == 400

    def test_dte_min_greater_than_dte_max_returns_400(self, client_and_cosmos):
        client, fake_cosmos = client_and_cosmos
        fake_cosmos.symbols["TEST"] = {"enrichment": {"category": "balanced"}, "total_shares": 0}
        resp = client.get("/api/symbols/TEST/best-options", params={"dte_min": 40, "dte_max": 10})
        assert resp.status_code == 400

    def test_dte_max_beyond_endpoint_hard_cap_is_rejected_by_query_validation(self, client_and_cosmos):
        # `dte_max: int = Query(default=45, ge=0, le=60)` -- FastAPI's own
        # validation, not app code; 61 must never reach the handler.
        client, fake_cosmos = client_and_cosmos
        fake_cosmos.symbols["TEST"] = {"enrichment": {"category": "balanced"}, "total_shares": 0}
        resp = client.get("/api/symbols/TEST/best-options", params={"dte_max": 61})
        assert resp.status_code == 422

    def test_negative_dte_min_is_rejected_by_query_validation(self, client_and_cosmos):
        client, fake_cosmos = client_and_cosmos
        fake_cosmos.symbols["TEST"] = {"enrichment": {"category": "balanced"}, "total_shares": 0}
        resp = client.get("/api/symbols/TEST/best-options", params={"dte_min": -1})
        assert resp.status_code == 422


class TestColdCacheWarmingBehavior:
    """Design finding F6: a true cold miss must never block the event loop
    on a live provider fetch -- it schedules a background refresh and
    answers immediately with an explicit "warming" state."""

    def test_cold_cache_returns_200_warming_not_503_or_hang(self, client_and_cosmos, monkeypatch):
        client, fake_cosmos = client_and_cosmos
        fake_cosmos.symbols["TEST"] = {"enrichment": {"category": "balanced"}, "total_shares": 0}
        cache = _make_cache(monkeypatch, yf_chain=_sample_chain())
        set_options_chain_cache(cache)  # true cold miss: nothing in memory, nothing persisted

        started = time.monotonic()
        resp = client.get("/api/symbols/TEST/best-options?support_level=100.0")
        elapsed = time.monotonic() - started

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "warming"
        assert body["symbol"] == "TEST"
        assert body["retry_after"] == 15
        # May also include 'reason' and 'next_run' fields
        # Must answer immediately, never block on the (fake, but still
        # simulated-as-live) provider fetch this same request scheduled.
        assert elapsed < 2.0

    def test_cold_cache_warming_actually_populates_the_cache_in_the_background(self, client_and_cosmos, monkeypatch):
        # Beyond the immediate response shape: prove the scheduled refresh
        # this request kicked off actually completes and leaves the cache
        # warm, so a follow-up request would see real data rather than
        # "warming" forever.
        client, fake_cosmos = client_and_cosmos
        fake_cosmos.symbols["TEST"] = {"enrichment": {"category": "balanced"}, "total_shares": 0}
        cache = _make_cache(monkeypatch, yf_chain=_sample_chain())
        set_options_chain_cache(cache)

        resp = client.get("/api/symbols/TEST/best-options?support_level=100.0")
        assert resp.json()["status"] == "warming"

        deadline = time.monotonic() + 3.0
        while cache.get("TEST") is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert cache.get("TEST") is not None, "background refresh scheduled by the cold-cache request never completed"


class TestWarmCacheFullTable:
    def test_warm_cache_returns_full_populated_best_options_table(self, client_and_cosmos, monkeypatch):
        client, fake_cosmos = client_and_cosmos
        fake_cosmos.symbols["TEST"] = {"enrichment": {"category": "balanced"}, "total_shares": 300}
        chain = _sample_chain()
        cache = _make_cache(monkeypatch, yf_chain=chain)
        cache.get_or_load("TEST")  # warm it synchronously before any request (no running loop yet)
        set_options_chain_cache(cache)

        resp = client.get("/api/symbols/TEST/best-options?support_level=100.0")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["symbol"] == "TEST"
        assert len(body["calls"]["rows"]) == 1
        assert len(body["puts"]["rows"]) == 1

    def test_endpoint_parameters_match_a_direct_evaluate_best_options_call_on_same_chain(
        self, client_and_cosmos, monkeypatch
    ):
        # Seam-consistency check (Danny's acceptance gate #3): the endpoint
        # must not re-derive or diverge from what the evaluator itself
        # would produce for the identical chain/inputs.
        client, fake_cosmos = client_and_cosmos
        earn = (_today() + timedelta(days=40)).isoformat()
        ex_div = (_today() + timedelta(days=5)).isoformat()
        fake_cosmos.symbols["TEST"] = {"enrichment": {"category": "high_yield"}, "total_shares": 0}
        fake_cosmos.earnings_dates["TEST"] = earn
        fake_cosmos.ex_dividend_dates["TEST"] = ex_div
        chain = {
            "symbol": "TEST", "timestamp": "2026-08-29T11:00:00Z", "underlying_price": 100.0,
            "calls": {_exp_key(20): {"105.0": _contract(bid=1.2, ask=1.3, strike=105.0)}},
            "puts": {},
        }
        cache = _make_cache(monkeypatch, yf_chain=chain)
        cache.get_or_load("TEST")
        set_options_chain_cache(cache)

        resp = client.get("/api/symbols/TEST/best-options?support_level=100.0")
        endpoint_result = resp.json()

        stored_chain = json.loads(cache.get("TEST"))
        direct_result = evaluate_best_options(
            stored_chain, side="both", category="high_yield", total_shares=0,
            next_earnings_date=earn, ex_dividend_date=ex_div, support_level=None,
            dte_min=0, dte_max=45, now=_real_now(),
        )
        # `parameters.evaluated_at` legitimately differs (each call reads
        # wall-clock time independently, microseconds apart) -- every
        # other field must match exactly.
        endpoint_result["parameters"].pop("evaluated_at", None)
        direct_result["parameters"].pop("evaluated_at", None)
        assert endpoint_result["parameters"] == direct_result["parameters"]
        assert endpoint_result["calls"] == direct_result["calls"]
        assert endpoint_result["puts"] == direct_result["puts"]


class TestZeroLlmReachability:
    """Design acceptance gate #1: zero LLM calls reachable from this
    endpoint. `FakeCosmos`/the fake providers implement no LLM surface at
    all -- if the endpoint's code path needed one, this test would fail
    with an AttributeError/ImportError rather than a clean 200."""

    def test_full_request_cycle_completes_without_any_llm_dependency(self, client_and_cosmos, monkeypatch):
        client, fake_cosmos = client_and_cosmos
        fake_cosmos.symbols["TEST"] = {"enrichment": {"category": "balanced"}, "total_shares": 0}
        cache = _make_cache(monkeypatch, yf_chain=_sample_chain())
        cache.get_or_load("TEST")
        set_options_chain_cache(cache)

        started = time.monotonic()
        resp = client.get("/api/symbols/TEST/best-options?support_level=100.0")
        elapsed = time.monotonic() - started

        assert resp.status_code == 200
        # An LLM round-trip in this codebase is a genuine network call
        # (seconds, not milliseconds); a request this fast is strong
        # operational evidence none was made, on top of the static
        # source-scan already covering `best_options.py` itself in
        # `test_best_options_adversarial.py`.
        assert elapsed < 1.0


class TestBroadExceptionHandlingAroundEvaluator:
    """The endpoint wraps the `evaluate_best_options()` call in a broad
    `except Exception` (not a narrow `except RuntimeError`) -- Danny's
    design explicitly warns against the roll-table anti-pattern where a
    narrow `except RuntimeError: -> 503` silently swallows every OTHER
    exception type as an unhandled 500 with a useless client-facing error.
    A genuine bug inside the evaluator must surface as a 500 with the real
    error message, not a misleading 503 "service unavailable"."""

    def test_a_genuine_evaluator_exception_surfaces_as_500_with_real_message(
        self, client_and_cosmos, monkeypatch
    ):
        client, fake_cosmos = client_and_cosmos
        fake_cosmos.symbols["TEST"] = {"enrichment": {"category": "balanced"}, "total_shares": 0}
        cache = _make_cache(monkeypatch, yf_chain=_sample_chain())
        cache.get_or_load("TEST")
        set_options_chain_cache(cache)

        def _boom(*args, **kwargs):
            raise ValueError("simulated evaluator defect")

        monkeypatch.setattr("src.best_options.evaluate_best_options", _boom)
        resp = client.get("/api/symbols/TEST/best-options?support_level=100.0")
        assert resp.status_code == 500
        assert "simulated evaluator defect" in resp.json()["error"]
