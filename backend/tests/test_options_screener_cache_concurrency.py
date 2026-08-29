"""Livingston's cache/API-seam integration suite for `GET
/api/screener/options` (`backend/web/app.py`), covering the
concurrency/persistence concerns Basher's adversarial suite
(`test_options_screener_endpoint.py`) does not: whether the endpoint's
per-symbol Cosmos/persistence gather work actually stays off the request's
event loop (per the approved directive's "bounded/sequential in a worker
thread" requirement), whether cold-miss warming still really fires once
that gather work is offloaded to a thread with no event loop of its own,
and that the screener never becomes a second source of truth (no new
Cosmos writes for its own output).

Uses the REAL FastAPI app, REAL `OptionsChainCache`/`OptionsChainStore`,
and REAL `src.options_screener.evaluate_options_screener` /
`src.best_options.evaluate_best_options` -- only Cosmos and the
yfinance/TradingView network edges are faked, independently of
`test_options_screener_endpoint.py`'s own fakes (no shared/mutual fake
module between the two suites).

No pytest-asyncio dependency (matching `tests/test_options_chain_cache.py`'s
own convention): coroutines are driven via `run_async()`, an isolated
`asyncio.new_event_loop()` per test, rather than `asyncio.run()` or
`@pytest.mark.asyncio` (not installed in this project).

Hermetic: no network, no real Cosmos, no real LLM.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import httpx
import pytest

from src.options_chain_cache import (
    OptionsChainCache,
    set_options_chain_cache,
)
from src.options_chain_store import OptionsChainStore
from web.app import app


def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _CosmosDouble:
    """Records every write attempt (so a test can assert the screener
    endpoint never persists its own aggregated output -- no second source
    of truth) and lets `list_symbols`/`get_calendar_events` be given an
    artificial delay to simulate real cross-region Cosmos latency."""

    def __init__(self, symbols=None, *, list_symbols_delay=0.0):
        self._symbols = list(symbols or [])
        self._list_symbols_delay = list_symbols_delay
        self.write_calls = []

    def list_symbols(self):
        if self._list_symbols_delay:
            time.sleep(self._list_symbols_delay)
        return list(self._symbols)

    def get_calendar_events(self):
        return []

    def __getattr__(self, name):
        # Any write-ish method the endpoint might someday call (upsert_*,
        # save_*, write_*, persist_*) is recorded rather than silently
        # succeeding as a no-op -- a missing attribute would otherwise
        # raise, but a persistence defect here should be *visible* in a
        # test assertion, not a crash that looks unrelated to the point
        # being tested.
        if name.startswith(("write_", "upsert_", "save_", "persist_")):
            def _record(*args, **kwargs):
                self.write_calls.append((name, args, kwargs))
            return _record
        raise AttributeError(name)


def _fake_chain(symbol):
    return {
        "symbol": symbol,
        "timestamp": "2026-08-29T11:00:00Z",
        "underlying_price": 100.0,
        "calls": {},
        "puts": {},
    }


@pytest.fixture(autouse=True)
def _isolate_shared_cache_singleton():
    import src.options_chain_cache as occ_module
    saved = occ_module._shared_cache
    yield
    set_options_chain_cache(saved)


def _make_cache(monkeypatch, *, chain_factory=_fake_chain):
    cache = OptionsChainCache(ttl_seconds=1800, store=OptionsChainStore(enabled=False))

    async def _fetch(symbol):
        return chain_factory(symbol)

    monkeypatch.setattr(cache, "_fetch_yfinance", _fetch)
    monkeypatch.setattr(cache, "_fetch_tradingview", _fetch)
    return cache


class TestGatherWorkStaysOffTheEventLoop:
    """Reproduces the exact defect found while integrating this endpoint:
    `_build_screener_symbol_inputs` performs real, synchronous Cosmos I/O
    (`list_symbols`, `get_calendar_events`, and a persistence hydrate per
    cold symbol) -- called directly on the request's event loop, that I/O
    freezes every other coroutine sharing the loop (every other concurrent
    request) for its full duration. This asserts a concurrent, unrelated
    request (`/healthz`, zero dependencies) is served promptly *while* a
    slow screener request is still in flight, proving the gather work is
    genuinely off-loop, not just fast in practice."""

    def test_concurrent_healthz_request_is_not_delayed_by_a_slow_screener_gather(
        self, monkeypatch,
    ):
        cosmos = _CosmosDouble(symbols=[{"symbol": "SLOW", "enrichment": {}, "total_shares": 0}],
                                list_symbols_delay=0.6)
        cache = _make_cache(monkeypatch)
        set_options_chain_cache(cache)
        app.router.on_startup = []
        app.state.cosmos = cosmos

        async def _run():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                screener_task = asyncio.create_task(client.get("/api/screener/options"))
                # Give the screener request a head start into its slow,
                # offloaded Cosmos read before firing the canary request.
                await asyncio.sleep(0.15)
                t0 = time.monotonic()
                healthz_resp = await client.get("/healthz")
                healthz_elapsed = time.monotonic() - t0
                screener_resp = await screener_task
                return healthz_resp, healthz_elapsed, screener_resp

        healthz_resp, healthz_elapsed, screener_resp = run_async(_run())

        assert healthz_resp.status_code == 200
        assert screener_resp.status_code == 200
        # The slow Cosmos read alone takes 0.6s; a healthz reply arriving
        # in a small fraction of that proves the event loop kept serving
        # other requests while the screener's gather work ran elsewhere.
        assert healthz_elapsed < 0.3, (
            f"/healthz took {healthz_elapsed:.3f}s while a screener request was "
            "in flight -- the screener's Cosmos gather work is blocking the "
            "event loop instead of running in a worker thread"
        )


class TestDeferredWarmingStillFires:
    """The gather phase can no longer call `schedule_background_refresh`
    itself (it may run on a worker thread with no event loop of its own,
    where that call silently no-ops) -- the endpoint must apply the
    decision back on the request's own event-loop thread instead. This
    proves that hand-off actually happens: a cold symbol is genuinely
    warmed by one screener request, not silently dropped."""

    def test_cold_symbol_is_actually_warmed_after_one_screener_request(self, monkeypatch):
        cosmos = _CosmosDouble(symbols=[{"symbol": "WARM1", "enrichment": {}, "total_shares": 0}])
        cache = _make_cache(monkeypatch)
        set_options_chain_cache(cache)
        app.router.on_startup = []
        app.state.cosmos = cosmos

        assert cache.get("WARM1") is None, "fixture must start truly cold"

        async def _run():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/screener/options")
                assert resp.status_code == 200
                body = resp.json()
                assert body["symbols"]["counts"]["warming"] == 1

                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline and cache.get("WARM1") is None:
                    await asyncio.sleep(0.05)

        run_async(_run())

        assert cache.get("WARM1") is not None, (
            "cold symbol reported 'warming' by the endpoint but was never "
            "actually populated -- schedule_background_refresh was dropped "
            "somewhere between the worker thread and the event loop"
        )


class TestNoScreenerResultPersistence:
    """Approved directive: no persisted screener snapshots, no second
    source of truth. The screener must only ever read from the existing
    cache/Cosmos primitives -- never write its own aggregated output
    anywhere."""

    def test_screener_request_issues_zero_cosmos_writes(self, monkeypatch):
        cosmos = _CosmosDouble(symbols=[{"symbol": "READONLY", "enrichment": {}, "total_shares": 150}])
        cache = _make_cache(monkeypatch)
        set_options_chain_cache(cache)
        app.router.on_startup = []
        app.state.cosmos = cosmos

        async def _run():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.get("/api/screener/options")

        resp = run_async(_run())

        assert resp.status_code == 200
        assert cosmos.write_calls == []


class TestSharedRequestTimestamp:
    """A single `now` must be used for the whole aggregation -- the
    aggregator's own per-symbol memoization key deliberately excludes
    `now` (Linus's design), but the *value* it stamps into the response
    (`generated_at`) must be one consistent instant per request, not
    recomputed per symbol/section."""

    def test_generated_at_is_a_single_recent_timestamp(self, monkeypatch):
        cosmos = _CosmosDouble(symbols=[
            {"symbol": "TSA", "enrichment": {}, "total_shares": 0},
            {"symbol": "TSB", "enrichment": {}, "total_shares": 0},
        ])
        cache = _make_cache(monkeypatch)
        set_options_chain_cache(cache)
        app.router.on_startup = []
        app.state.cosmos = cosmos

        async def _run():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.get("/api/screener/options")

        before = datetime.now(timezone.utc)
        resp = run_async(_run())
        after = datetime.now(timezone.utc)

        assert resp.status_code == 200
        body = resp.json()
        generated_at = datetime.fromisoformat(body["generated_at"].replace("Z", "+00:00"))
        assert before <= generated_at <= after
