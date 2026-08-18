"""
Test suite for the centralized options chain cache (src/options_chain_cache.py).

Covers:
  - Source merge precedence between yfinance (base) and TradingView (overlay),
    delegated to Linus's frozen `src/options_chain_merge.py` pure functions
  - Field-level accumulate-against-prior merge: a fresh invalid/degenerate
    quote never overwrites a previously accepted value for the same contract
  - First-fetch invalid/degenerate values are preserved as-is (never
    fabricated into fake values)
  - Stale cache entries remain readable (never evicted purely by TTL)
  - Actual expired-contract (past expiration date) pruning
  - Persistence/lifecycle integration (Rusty's charter): hydrate-before-fetch
    on cold start/restart, per-symbol lock serialization with no lost
    update, non-fatal persistence failures, invalidate/purge semantics, and
    the 2026-06-30 `refresh_all` watchdog regression guard. CAS-retry/
    sharding/grace-pruning mechanics themselves are exercised exhaustively
    against a fake Cosmos container in `tests/test_options_chain_store.py`;
    here we only verify the cache correctly wires into that store.

Hermetic: no network calls, no real Cosmos. `_fetch_yfinance`/
`_fetch_tradingview` are monkeypatched per test so `refresh()` exercises
only the merge/cache logic; persistence uses either an explicitly disabled
`OptionsChainStore` or the lightweight `_FakeStore` double below.

No pytest-asyncio dependency: coroutines are driven with an isolated event
loop via `run_async()`, matching the pattern used elsewhere in this test
suite (see tests/test_summary_paused.py) rather than `asyncio.run()`, which
sets the global policy's "_set_called" flag and can interfere with other
tests relying on `asyncio.get_event_loop()`.
"""

import asyncio
import copy
import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from src.options_chain_cache import OptionsChainCache
from src.options_chain_store import OptionsChainStore


def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _contract(**overrides):
    base = {
        "contractSymbol": "AAPL240101C00100000",
        "strike": 100.0,
        "bid": 1.0,
        "ask": 1.2,
        "mid": 1.1,
        "iv": 0.25,
        "delta": 0.5,
        "gamma": 0.05,
        "theta": -0.02,
        "vega": 0.1,
        "rho": 0.01,
        "volume": 10,
        "openInterest": 100,
        "lastPrice": 1.1,
        "lastTradeDate": "2024-01-01T00:00:00Z",
        "inTheMoney": False,
        "expiration": "20240101",
        "option_type": "call",
    }
    base.update(overrides)
    return base


def _future_exp_key(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y%m%d")


def _past_exp_key(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y%m%d")


def _empty_chain(symbol="TEST"):
    return {"symbol": symbol, "timestamp": "2024-01-01T00:00:00Z", "calls": {}, "puts": {}}


def _patch_sources(monkeypatch, cache_obj, yf_chain, tv_chain):
    async def _fake_yf(symbol):
        return yf_chain

    async def _fake_tv(symbol):
        return tv_chain

    monkeypatch.setattr(cache_obj, "_fetch_yfinance", _fake_yf)
    monkeypatch.setattr(cache_obj, "_fetch_tradingview", _fake_tv)


# ===========================================================================
# OptionsChainCache.refresh() — end-to-end merge behavior
# ===========================================================================

@pytest.fixture
def cache():
    # Explicit disabled store keeps these tests hermetic (no Cosmos/config
    # side effects) and decoupled from the process-wide persistence
    # singleton — persistence lifecycle itself is covered separately in
    # TestPersistenceLifecycle below.
    return OptionsChainCache(ttl_seconds=1800, store=OptionsChainStore(enabled=False))


class TestSourceMergePrecedence:
    def test_tv_overwrites_when_valid(self, cache, monkeypatch):
        exp = _future_exp_key(5)
        yf_chain = {"symbol": "TEST", "calls": {exp: {"100.0": _contract(bid=1.0, ask=1.2)}}, "puts": {}}
        tv_chain = {"symbol": "TEST", "calls": {exp: {"100.0": _contract(bid=1.5, ask=1.7)}}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain, tv_chain)

        result = json.loads(run_async(cache.refresh("TEST")))
        assert result["calls"][exp]["100.0"]["bid"] == 1.5
        assert result["calls"][exp]["100.0"]["ask"] == 1.7

    def test_tv_zero_does_not_overwrite_valid_yfinance(self, cache, monkeypatch):
        exp = _future_exp_key(5)
        yf_chain = {"symbol": "TEST", "calls": {exp: {"100.0": _contract(bid=1.0, ask=1.2)}}, "puts": {}}
        # Under Linus's trust-gate rules (design §2.4), a quote is only
        # trusted when it supplies a valid ask>0 OR a valid iv — bid=0 alone
        # (with iv also invalid) represents a genuinely degenerate/failed TV
        # payload, not a real bid-less quote.
        tv_chain = {"symbol": "TEST", "calls": {exp: {"100.0": _contract(bid=0.0, ask=0.0, iv=0.0)}}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain, tv_chain)

        result = json.loads(run_async(cache.refresh("TEST")))
        assert result["calls"][exp]["100.0"]["bid"] == 1.0
        assert result["calls"][exp]["100.0"]["ask"] == 1.2

    def test_tv_adds_new_strikes_yfinance_is_missing(self, cache, monkeypatch):
        exp = _future_exp_key(5)
        yf_chain = {"symbol": "TEST", "calls": {}, "puts": {}}
        tv_chain = {"symbol": "TEST", "calls": {exp: {"105.0": _contract(strike=105.0, bid=2.0, ask=2.2)}}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain, tv_chain)

        result = json.loads(run_async(cache.refresh("TEST")))
        assert result["calls"][exp]["105.0"]["bid"] == 2.0


class TestBeyondFiveExpirations:
    def test_yfinance_zero_beyond_tv_coverage_no_prior_data(self, cache, monkeypatch):
        """TV (near-term only) covers the first few expirations; yfinance
        zeros for later expirations remain zero on a first fetch (no prior
        good data exists to fall back to) — matches observed production
        behavior beyond ~5 expirations before this fix."""
        near_exps = [_future_exp_key(d) for d in (5, 10, 15)]
        far_exps = [_future_exp_key(d) for d in (40, 70, 100)]

        yf_calls = {}
        for exp in near_exps + far_exps:
            yf_calls[exp] = {"100.0": _contract(bid=0.0, ask=0.0, expiration=exp)}

        tv_calls = {}
        for exp in near_exps:
            tv_calls[exp] = {"100.0": _contract(bid=1.0, ask=1.2, expiration=exp)}

        yf_chain = {"symbol": "TEST", "calls": yf_calls, "puts": {}}
        tv_chain = {"symbol": "TEST", "calls": tv_calls, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain, tv_chain)

        result = json.loads(run_async(cache.refresh("TEST")))
        for exp in near_exps:
            assert result["calls"][exp]["100.0"]["bid"] == 1.0
        for exp in far_exps:
            assert result["calls"][exp]["100.0"]["bid"] == 0.0

    def test_far_expiration_backfilled_from_last_known_good(self, cache, monkeypatch):
        """Once a valid value has been observed for a far expiration on a
        prior refresh, a later refresh returning zero for it must not wipe
        it out — this is the core regression this fix addresses."""
        far_exp = _future_exp_key(100)

        # First refresh: TV doesn't reach this far out, but yfinance had a
        # real quote that cycle.
        yf_chain_1 = {"symbol": "TEST", "calls": {far_exp: {"100.0": _contract(bid=3.0, ask=3.2, expiration=far_exp)}}, "puts": {}}
        tv_chain_1 = {"symbol": "TEST", "calls": {}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain_1, tv_chain_1)
        first = json.loads(run_async(cache.refresh("TEST")))
        assert first["calls"][far_exp]["100.0"]["bid"] == 3.0

        # Second refresh: yfinance now returns a genuinely degenerate quote
        # for the same contract (rate limit / thin quote / stale snapshot —
        # bid, ask, AND iv all invalid, failing the trust gate entirely per
        # design §2.4/T3), TV still doesn't cover it.
        yf_chain_2 = {"symbol": "TEST", "calls": {far_exp: {"100.0": _contract(bid=0.0, ask=0.0, iv=0.0, expiration=far_exp)}}, "puts": {}}
        tv_chain_2 = {"symbol": "TEST", "calls": {}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain_2, tv_chain_2)
        second = json.loads(run_async(cache.refresh("TEST")))

        assert second["calls"][far_exp]["100.0"]["bid"] == 3.0
        assert second["calls"][far_exp]["100.0"]["ask"] == 3.2


class TestLastKnownGoodMerge:
    def test_first_fetch_zeros_preserved_as_is(self, cache, monkeypatch):
        """No prior cache entry exists. A real bid=0 (valid on its own —
        design §2.1) is kept as a genuine bid-less observation. `ask`,
        which is only ever valid when >0 (design §2.1: ask==0 carries no
        information), is correctly represented as *absent* rather than a
        fabricated literal zero — "absence is not zero" (design §2.2)."""
        exp = _future_exp_key(5)
        yf_chain = {"symbol": "TEST", "calls": {exp: {"100.0": _contract(bid=0.0, ask=0.0)}}, "puts": {}}
        tv_chain = {"symbol": "TEST", "calls": {}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain, tv_chain)

        result = json.loads(run_async(cache.refresh("TEST")))
        assert result["calls"][exp]["100.0"]["bid"] == 0.0
        assert result["calls"][exp]["100.0"].get("ask") is None

    def test_contract_missing_from_fresh_fetch_carried_forward(self, cache, monkeypatch):
        """If a contract present in the previous cache is entirely absent
        from both fresh sources this cycle, the last-known-good contract is
        carried forward rather than disappearing."""
        exp = _future_exp_key(5)
        yf_chain_1 = {"symbol": "TEST", "calls": {exp: {"100.0": _contract(bid=1.0, ask=1.2)}}, "puts": {}}
        tv_chain_1 = {"symbol": "TEST", "calls": {}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain_1, tv_chain_1)
        run_async(cache.refresh("TEST"))

        # Next cycle: source omits this contract/expiration entirely.
        yf_chain_2 = {"symbol": "TEST", "calls": {}, "puts": {}}
        tv_chain_2 = {"symbol": "TEST", "calls": {}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain_2, tv_chain_2)
        result = json.loads(run_async(cache.refresh("TEST")))

        assert result["calls"][exp]["100.0"]["bid"] == 1.0

    def test_volume_and_open_interest_not_preserved_when_zero(self, cache, monkeypatch):
        """Volume/openInterest legitimately go to zero — they must always
        reflect the freshest fetch, never be pinned to a stale prior value."""
        exp = _future_exp_key(5)
        yf_chain_1 = {"symbol": "TEST", "calls": {exp: {"100.0": _contract(volume=500, openInterest=1000)}}, "puts": {}}
        tv_chain_1 = {"symbol": "TEST", "calls": {}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain_1, tv_chain_1)
        run_async(cache.refresh("TEST"))

        yf_chain_2 = {"symbol": "TEST", "calls": {exp: {"100.0": _contract(volume=0, openInterest=0)}}, "puts": {}}
        tv_chain_2 = {"symbol": "TEST", "calls": {}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain_2, tv_chain_2)
        result = json.loads(run_async(cache.refresh("TEST")))

        assert result["calls"][exp]["100.0"]["volume"] == 0
        assert result["calls"][exp]["100.0"]["openInterest"] == 0


class TestActualExpirationPruning:
    def test_past_expiration_dropped_after_merge(self, cache, monkeypatch):
        past_exp = _past_exp_key(3)
        future_exp = _future_exp_key(3)
        yf_chain = {
            "symbol": "TEST",
            "calls": {
                past_exp: {"100.0": _contract(expiration=past_exp)},
                future_exp: {"100.0": _contract(expiration=future_exp)},
            },
            "puts": {},
        }
        tv_chain = {"symbol": "TEST", "calls": {}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain, tv_chain)

        result = json.loads(run_async(cache.refresh("TEST")))
        assert past_exp not in result["calls"]
        assert future_exp in result["calls"]

    def test_previously_cached_past_expiration_pruned_on_next_refresh(self, cache, monkeypatch):
        """A contract that was valid and cached, but whose expiration date
        has since passed, must not be carried forward by the last-known-good
        merge — cache TTL/staleness and actual contract expiry are distinct."""
        soon_to_expire = _future_exp_key(1)
        yf_chain_1 = {"symbol": "TEST", "calls": {soon_to_expire: {"100.0": _contract(bid=1.0, expiration=soon_to_expire)}}, "puts": {}}
        tv_chain_1 = {"symbol": "TEST", "calls": {}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain_1, tv_chain_1)
        run_async(cache.refresh("TEST"))

        # Simulate time passing: manually rewrite the exp key to be in the past
        # in the stored cache entry, then refresh again with sources empty.
        with cache._lock:
            entry = cache._store["TEST"]
            chain = json.loads(entry["chain_json"])
            past_exp = _past_exp_key(1)
            chain["calls"][past_exp] = chain["calls"].pop(soon_to_expire)
            chain["calls"][past_exp]["100.0"]["expiration"] = past_exp
            entry["chain_json"] = json.dumps(chain)

        yf_chain_2 = {"symbol": "TEST", "calls": {}, "puts": {}}
        tv_chain_2 = {"symbol": "TEST", "calls": {}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain_2, tv_chain_2)
        result = json.loads(run_async(cache.refresh("TEST")))

        assert past_exp not in result["calls"]


class TestStaleCacheReadability:
    def test_get_returns_data_even_when_ttl_expired(self, cache):
        with cache._lock:
            cache._store["TEST"] = {
                "chain_json": json.dumps(_empty_chain()),
                "cached_at": -10_000.0,  # far in the past relative to monotonic()
            }
        assert cache.get("TEST") is not None

    def test_is_stale_true_after_ttl(self, cache):
        with cache._lock:
            cache._store["TEST"] = {
                "chain_json": json.dumps(_empty_chain()),
                "cached_at": -10_000.0,
            }
        assert cache.is_stale("TEST") is True

    def test_is_stale_true_for_missing_entry(self, cache):
        assert cache.is_stale("NEVER_FETCHED") is True

    def test_true_cache_miss_returns_none(self, cache):
        assert cache.get("NEVER_FETCHED") is None

    def test_get_or_load_async_returns_stale_data_immediately(self, cache, monkeypatch):
        exp = _future_exp_key(5)
        yf_chain = {"symbol": "TEST", "calls": {exp: {"100.0": _contract(bid=1.0)}}, "puts": {}}
        tv_chain = {"symbol": "TEST", "calls": {}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain, tv_chain)
        run_async(cache.refresh("TEST"))

        # Force staleness
        with cache._lock:
            cache._store["TEST"]["cached_at"] -= 10_000.0

        async def _scenario():
            # Background refresh should not block the read from returning
            # immediately with the last-known-good (stale) data.
            result_json = await cache.get_or_load_async("TEST")
            # Give the scheduled background refresh task a chance to run
            # to completion so it doesn't leak into other tests.
            pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            return result_json

        result = json.loads(run_async(_scenario()))
        assert result["calls"][exp]["100.0"]["bid"] == 1.0


# ===========================================================================
# Persistence / lifecycle integration (Rusty's charter)
#
# Covers Danny's design §7 required tests T13-T21:
#   T13 cold start hydrates before fetch, no provider call on a hydrate hit
#   T14 restart simulation: fresh instance + populated store serves prior
#   T15 store raises on persist -> refresh() still returns the merged chain
#   T16 CAS retry mechanics: exercised exhaustively in
#       tests/test_options_chain_store.py (fake Cosmos container); here we
#       only verify refresh() wires into store.persist()/prune_expired()
#   T17 persist/prune wiring: one call per refresh cycle, correct args
#   T18 invalidate() drops memory only; re-read re-hydrates identical data
#   T19 two concurrent refresh() calls for one symbol serialize (real OS
#       threads, matching refresh_all's ThreadPoolExecutor) -> union result
#   T20 refresh_all: one hanging symbol does not block others (2026-06-30
#       scheduler hang watchdog decision)
#   T21 persistence_enabled=false -> pure memory path (already exercised by
#       every test above using the disabled-store `cache` fixture; also
#       covered explicitly here against the real OptionsChainStore class)
# ===========================================================================

class _FakeStore:
    """Lightweight persistence-store test double for cache-level lifecycle
    tests. Mirrors the exact public surface `OptionsChainCache` depends on
    (`is_available`, `hydrate`, `persist`, `prune_expired`, `purge`,
    `stats`) without touching Cosmos — the CAS/sharding mechanics behind
    that surface are already exhaustively tested against a fake Cosmos
    container in tests/test_options_chain_store.py.
    """

    def __init__(self, *, enabled=True, hydrate_data=None, raise_on_persist=False):
        self._enabled = enabled
        self._hydrate_data = hydrate_data
        self._raise_on_persist = raise_on_persist
        self.persist_calls = []
        self.prune_calls = []
        self.purge_calls = []
        self._persist_errors = 0

    def is_available(self):
        return self._enabled

    def hydrate(self, symbol):
        if not self._enabled or self._hydrate_data is None:
            return None
        return copy.deepcopy(self._hydrate_data)

    def persist(self, symbol, chain, *, now=None):
        self.persist_calls.append((symbol, copy.deepcopy(chain)))
        if self._raise_on_persist:
            self._persist_errors += 1
            raise RuntimeError("simulated persistence failure")
        # Read-your-writes, like the real store: a later hydrate() sees
        # what was just persisted, unless a fixed override was supplied.
        self._hydrate_data = copy.deepcopy(chain)
        return {"written": 1, "unchanged": 0, "conflicts_skipped": 0, "errors": 0}

    def prune_expired(self, symbol, *, today_et, grace_days=None):
        self.prune_calls.append((symbol, today_et, grace_days))
        return 0

    def purge(self, symbol):
        self.purge_calls.append(symbol)
        self._hydrate_data = None
        return 1

    def stats(self):
        return {
            "available": self._enabled,
            "persist_errors": self._persist_errors,
            "last_persist_error_at": None,
            "last_persist_error": None,
            "expired_shard_grace_days": 7,
            "max_shard_bytes": 1_600_000,
        }


class TestHydrationOnMiss:
    """T13/T14 — a true in-memory miss hydrates from the persistence store
    before ever calling a live provider; a hydrate hit never calls a
    provider at all. Fixes cross-process/restart divergence (the scheduler
    or a fresh process may never have called get_or_load() for this symbol
    before)."""

    def test_cold_start_hydrates_before_fetch_no_provider_call(self, monkeypatch):
        exp = _future_exp_key(5)
        persisted = {
            "symbol": "TEST",
            "calls": {exp: {"100.0": _contract(bid=2.5, ask=2.7, expiration=exp)}},
            "puts": {},
        }
        store = _FakeStore(hydrate_data=persisted)
        cache = OptionsChainCache(ttl_seconds=1800, store=store)

        provider_called = {"yf": False, "tv": False}

        async def _fake_yf(symbol):
            provider_called["yf"] = True
            return _empty_chain(symbol)

        async def _fake_tv(symbol):
            provider_called["tv"] = True
            return _empty_chain(symbol)

        monkeypatch.setattr(cache, "_fetch_yfinance", _fake_yf)
        monkeypatch.setattr(cache, "_fetch_tradingview", _fake_tv)

        result = json.loads(cache.get_or_load("TEST"))
        assert result["calls"][exp]["100.0"]["bid"] == 2.5
        assert provider_called["yf"] is False
        assert provider_called["tv"] is False

    def test_restart_simulation_fresh_instance_serves_prior_quotes(self):
        """A brand-new OptionsChainCache instance (simulating a process
        restart with an empty in-memory dict) still serves previously
        persisted quotes without any provider call."""
        exp = _future_exp_key(5)
        persisted = {
            "symbol": "TEST",
            "calls": {exp: {"100.0": _contract(bid=4.0, ask=4.2, expiration=exp)}},
            "puts": {},
        }
        store = _FakeStore(hydrate_data=persisted)
        fresh_cache = OptionsChainCache(ttl_seconds=1800, store=store)

        assert fresh_cache.get("TEST") is None  # nothing in memory yet
        result = json.loads(fresh_cache.get_or_load("TEST"))
        assert result["calls"][exp]["100.0"]["bid"] == 4.0

    def test_true_cold_miss_falls_through_to_provider(self, monkeypatch):
        """No memory, no persisted data anywhere — must still fetch from
        providers; hydrate is a fallback, not a replacement for fetching."""
        store = _FakeStore(hydrate_data=None)
        cache = OptionsChainCache(ttl_seconds=1800, store=store)
        exp = _future_exp_key(5)
        yf_chain = {"symbol": "TEST", "calls": {exp: {"100.0": _contract(bid=9.0, ask=9.2)}}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain, _empty_chain())

        result = json.loads(cache.get_or_load("TEST"))
        assert result["calls"][exp]["100.0"]["bid"] == 9.0


class TestPersistenceFailureNonFatal:
    """T15 — persistence failures are visible (logged, counted) but must
    never turn a good refresh into a failed one."""

    def test_store_raises_on_persist_refresh_still_returns_chain(self, monkeypatch):
        store = _FakeStore(raise_on_persist=True)
        cache = OptionsChainCache(ttl_seconds=1800, store=store)
        exp = _future_exp_key(5)
        yf_chain = {"symbol": "TEST", "calls": {exp: {"100.0": _contract(bid=5.0, ask=5.2)}}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain, _empty_chain())

        result = json.loads(run_async(cache.refresh("TEST")))
        assert result["calls"][exp]["100.0"]["bid"] == 5.0
        assert len(store.persist_calls) == 1
        assert cache.stats()["persistence"]["persist_errors"] == 1

        # The good in-memory result must also still be readable afterwards.
        assert cache.get("TEST") is not None


class TestPersistPruneWiring:
    """T17 — refresh() wires into the persistence store's write and grace-
    pruning paths exactly once per cycle, with the correct symbol."""

    def test_refresh_calls_store_persist_and_prune_once(self, monkeypatch):
        store = _FakeStore()
        cache = OptionsChainCache(ttl_seconds=1800, store=store)
        exp = _future_exp_key(5)
        yf_chain = {"symbol": "TEST", "calls": {exp: {"100.0": _contract(bid=1.0, ask=1.2)}}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain, _empty_chain())

        run_async(cache.refresh("TEST"))

        assert len(store.persist_calls) == 1
        assert store.persist_calls[0][0] == "TEST"
        assert len(store.prune_calls) == 1
        assert store.prune_calls[0][0] == "TEST"

    def test_ttl_expiry_never_triggers_a_prune_call_on_its_own(self, monkeypatch):
        """Reading a stale-but-present entry via get()/is_stale() must not
        itself invoke any persistence prune — TTL controls freshness only,
        pruning is driven exclusively by actual contract expiration and
        only ever runs as part of a real refresh cycle."""
        store = _FakeStore()
        cache = OptionsChainCache(ttl_seconds=1800, store=store)
        with cache._lock:
            cache._store["TEST"] = {
                "chain_json": json.dumps(_empty_chain()),
                "cached_at": -10_000.0,
            }
        assert cache.is_stale("TEST") is True
        cache.get("TEST")
        assert store.prune_calls == []


class TestInvalidateAndPurge:
    """T18 — invalidate() drops the in-memory entry only; persisted data
    survives and a subsequent read re-hydrates identical data. purge() is
    the separate, explicit destructive operation."""

    def test_invalidate_then_read_rehydrates_identical_data(self, monkeypatch):
        store = _FakeStore()
        cache = OptionsChainCache(ttl_seconds=1800, store=store)
        exp = _future_exp_key(5)
        yf_chain = {"symbol": "TEST", "calls": {exp: {"100.0": _contract(bid=6.0, ask=6.2)}}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain, _empty_chain())
        first = json.loads(run_async(cache.refresh("TEST")))

        cache.invalidate("TEST")
        assert cache.get("TEST") is None  # memory dropped

        # Re-read must re-hydrate from the persistence store, not re-fetch.
        second = json.loads(cache.get_or_load("TEST"))
        assert second["calls"][exp]["100.0"]["bid"] == first["calls"][exp]["100.0"]["bid"] == 6.0

    def test_purge_deletes_persisted_data_and_memory(self):
        store = _FakeStore(hydrate_data={"symbol": "TEST", "calls": {}, "puts": {}})
        cache = OptionsChainCache(ttl_seconds=1800, store=store)
        with cache._lock:
            cache._store["TEST"] = {"chain_json": json.dumps(_empty_chain()), "cached_at": time.monotonic()}

        deleted = cache.purge("TEST")

        assert deleted == 1
        assert store.purge_calls == ["TEST"]
        assert cache.get("TEST") is None


class TestConcurrentRefreshNoLostUpdate:
    """T19 — two refresh() calls for the same symbol from different OS
    threads (the real production scenario: refresh_all's ThreadPoolExecutor
    workers, or a scheduler run overlapping a manual /api/trigger call)
    must serialize via the per-symbol lock and converge to the union of
    both cycles' contracts — the exact read-modify-write race the
    2026-08-18 design fixes."""

    def test_two_concurrent_refreshes_serialize_and_union_results(self, monkeypatch):
        store = _FakeStore()
        cache = OptionsChainCache(ttl_seconds=1800, store=store)
        exp = _future_exp_key(5)
        call_count = {"n": 0}
        call_count_lock = threading.Lock()

        async def _fake_yf(symbol):
            with call_count_lock:
                call_count["n"] += 1
                this_call = call_count["n"]
            if this_call == 1:
                # Widen the window so the second thread genuinely contends
                # for the per-symbol lock (blocking on lock.acquire()
                # inside refresh()) rather than the two runs happening to
                # execute back-to-back by scheduling luck alone.
                time.sleep(0.05)
                return {"symbol": symbol, "calls": {exp: {"100.0": _contract(bid=1.0, ask=1.2, strike=100.0, expiration=exp)}}, "puts": {}}
            return {"symbol": symbol, "calls": {exp: {"105.0": _contract(bid=2.0, ask=2.2, strike=105.0, expiration=exp)}}, "puts": {}}

        async def _fake_tv(symbol):
            return _empty_chain(symbol)

        monkeypatch.setattr(cache, "_fetch_yfinance", _fake_yf)
        monkeypatch.setattr(cache, "_fetch_tradingview", _fake_tv)

        threads = [threading.Thread(target=cache._sync_refresh, args=("TEST",)) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
            assert not t.is_alive()

        final = json.loads(cache.get("TEST"))
        strikes = final["calls"][exp]
        assert "100.0" in strikes, "first cycle's contract must not be lost"
        assert "105.0" in strikes, "second cycle's contract must be present"
        # Persisted exactly twice — once per refresh cycle, fully serialized.
        assert len(store.persist_calls) == 2


class TestRefreshAllWatchdogRegression:
    """T20 — preserves the 2026-06-30 scheduler hang watchdog decision:
    refresh_all must not block on one symbol whose refresh hangs past
    `_REFRESH_SYMBOL_TIMEOUT`; other symbols still complete promptly, and
    the hung worker is abandoned (not joined) rather than blocking
    shutdown."""

    def test_refresh_all_hanging_symbol_does_not_block_others(self, monkeypatch):
        store = _FakeStore()
        cache = OptionsChainCache(ttl_seconds=1800, store=store)
        hang_seconds = 0.8
        monkeypatch.setattr("src.options_chain_cache._REFRESH_SYMBOL_TIMEOUT", 0.3)

        async def _fake_tv(symbol):
            return _empty_chain(symbol)

        async def _maybe_hanging_yf(symbol):
            if symbol == "HANG":
                time.sleep(hang_seconds)  # longer than the patched per-symbol timeout
            return {"symbol": symbol, "calls": {}, "puts": {}}

        monkeypatch.setattr(cache, "_fetch_yfinance", _maybe_hanging_yf)
        monkeypatch.setattr(cache, "_fetch_tradingview", _fake_tv)

        start = time.monotonic()
        summary = run_async(cache.refresh_all(["HANG", "OK1", "OK2"]))
        elapsed = time.monotonic() - start

        # Must return promptly (bounded by the per-symbol timeout), not
        # block for anywhere near the hang duration.
        assert elapsed < 1.5
        assert summary["errors"] >= 1
        assert summary["success"] >= 2

        # The abandoned "HANG" worker thread (per the 2026-06-30 decision,
        # refresh_all deliberately does not join it) is still running past
        # this point. Give it time to finish *within* this test's own
        # monkeypatch scope so it exercises the fakes above rather than
        # leaking past teardown and hitting real network calls.
        time.sleep(hang_seconds)


class TestPersistenceDisabledPureMemoryPath:
    """T21 — `persistence_enabled: false` (modeled here by a disabled real
    OptionsChainStore, exactly what the config-driven factory constructs)
    yields precisely today's memory-only behaviour: refresh/get/invalidate
    all work, and there is nothing to hydrate from on a memory miss."""

    def test_disabled_real_store_full_memory_round_trip(self, monkeypatch):
        store = OptionsChainStore(enabled=False)
        assert store.is_available() is False
        cache = OptionsChainCache(ttl_seconds=1800, store=store)
        exp = _future_exp_key(5)
        yf_chain = {"symbol": "TEST", "calls": {exp: {"100.0": _contract(bid=1.0, ask=1.2)}}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain, _empty_chain())

        result = json.loads(run_async(cache.refresh("TEST")))
        assert result["calls"][exp]["100.0"]["bid"] == 1.0

        cache.invalidate("TEST")
        assert cache.get("TEST") is None
        assert cache._get_store().hydrate("TEST") is None


# ===========================================================================
# Basher review (2026-08-18) — hidden-edge coverage
# ===========================================================================

class TestWebOnlyReplicaColdSingleton:
    """The real deployment divergence this design must fix is NOT just 'a
    process restarted' — it is the *combined* scheduler+API singleton
    process (which actually calls `refresh()`/`_sync_refresh()` and
    persists) versus a *separate*, independently-deployed `--web-only`
    replica whose own in-process singleton has never refreshed this symbol
    at all. Two independently-constructed `OptionsChainCache` instances
    sharing one persistence store reproduce that topology precisely,
    without assuming the scheduler is a distinct OS process — a second
    web replica of the very same combined process exhibits the identical
    cold-singleton problem."""

    def test_second_replica_cold_singleton_serves_first_replicas_persisted_refresh(self, monkeypatch):
        shared_store = _FakeStore()

        # Replica 1: the "scheduler+API" instance that actually performs a
        # live refresh and persists the result.
        replica_1 = OptionsChainCache(ttl_seconds=1800, store=shared_store)
        exp = _future_exp_key(10)
        yf_chain = {
            "symbol": "AAPL",
            "calls": {exp: {"100.0": _contract(bid=3.0, ask=3.2, expiration=exp)}},
            "puts": {},
        }
        _patch_sources(monkeypatch, replica_1, yf_chain, _empty_chain("AAPL"))
        run_async(replica_1.refresh("AAPL"))
        assert shared_store.persist_calls, "replica 1 must have persisted its refresh"

        # Replica 2: an independently constructed cache instance (its own
        # empty `_store` dict — a brand-new `--web-only` process replica)
        # that has NEVER called refresh() for "AAPL". It must serve
        # replica 1's persisted data through the public API without ever
        # touching a live provider.
        provider_called = {"yf": False, "tv": False}

        async def _fail_yf(symbol):
            provider_called["yf"] = True
            return _empty_chain(symbol)

        async def _fail_tv(symbol):
            provider_called["tv"] = True
            return _empty_chain(symbol)

        replica_2 = OptionsChainCache(ttl_seconds=1800, store=shared_store)
        monkeypatch.setattr(replica_2, "_fetch_yfinance", _fail_yf)
        monkeypatch.setattr(replica_2, "_fetch_tradingview", _fail_tv)

        assert replica_2.get("AAPL") is None  # cold singleton, nothing in memory
        result = json.loads(run_async(replica_2.get_or_load_async("AAPL")))
        assert result["calls"][exp]["100.0"]["bid"] == 3.0
        assert provider_called["yf"] is False
        assert provider_called["tv"] is False


class TestMergePriorMonotonicityFuzz:
    """Property/fuzz test against Linus's real, frozen `merge_prior` —
    calling it as a black box (never redefining its semantics) across many
    randomized accumulation cycles to lock in the two invariants Rusty's
    persistence layer depends on: a contract observed once is never lost,
    and each contract's `_meta.quote_asof` never moves backward in time.
    Deterministic (fixed seed) so failures reproduce exactly."""

    def test_repeated_random_cycles_never_lose_a_contract_or_regress_quote_asof(self):
        import random

        from src.options_chain_merge import merge_prior

        rng = random.Random(20260818)
        strikes = [str(float(s)) for s in range(90, 111)]  # 21 possible strikes
        exp = _future_exp_key(30)
        base_time = datetime(2026, 8, 18, tzinfo=timezone.utc)

        accumulated: dict = {}
        ever_seen: set[str] = set()
        last_quote_asof: dict[str, str] = {}

        for cycle in range(40):
            now = base_time + timedelta(minutes=cycle)
            # Each cycle, a random subset of strikes gets a fresh (possibly
            # degenerate) live quote; the rest are simply absent this cycle.
            present_strikes = rng.sample(strikes, k=rng.randint(0, len(strikes)))
            live_bucket = {}
            for strike_key in present_strikes:
                valid = rng.random() > 0.3
                live_bucket[strike_key] = _contract(
                    strike=float(strike_key),
                    expiration=exp,
                    bid=round(rng.uniform(0, 5), 2) if valid else 0.0,
                    ask=round(rng.uniform(0.1, 5), 2) if valid else 0.0,
                    iv=round(rng.uniform(0.05, 1.0), 3) if valid else 0.0,
                )
                ever_seen.add(strike_key)
            live = {"symbol": "FUZZ", "calls": {exp: live_bucket}, "puts": {}}

            accumulated = merge_prior(accumulated or {}, live, now=now)

            merged_bucket = accumulated.get("calls", {}).get(exp, {})
            # Invariant 1: every strike ever observed remains present.
            missing = ever_seen - set(merged_bucket)
            assert not missing, f"cycle {cycle}: lost contracts {missing}"

            # Invariant 2: quote_asof never regresses for any contract that
            # already had one.
            for strike_key, contract in merged_bucket.items():
                asof = (contract.get("_meta") or {}).get("quote_asof")
                if asof is None:
                    continue
                prior_asof = last_quote_asof.get(strike_key)
                if prior_asof is not None:
                    assert asof >= prior_asof, (
                        f"cycle {cycle}: quote_asof regressed for {strike_key}: "
                        f"{prior_asof} -> {asof}"
                    )
                last_quote_asof[strike_key] = asof


class TestGateBucketDegeneracyBoundary:
    """Exact boundary of Linus's `gate_bucket` (design §2.4): a bucket with
    >= 3 contracts where *every single one* fails `gate_contract` is
    rejected wholesale (the "provider returned an all-zero chain" failure
    mode); a bucket at the same size where only some contracts fail is
    trusted, and each contract is still gated individually."""

    def test_three_contract_all_failing_bucket_yields_no_quote_fields(self):
        from src.options_chain_merge import merge_sources

        exp = _future_exp_key(5)
        dead = lambda strike: _contract(
            strike=strike, expiration=exp, bid=0.0, ask=0.0, iv=0.0,
        )
        yf_chain = {
            "symbol": "TEST",
            "calls": {exp: {
                "100.0": dead(100.0), "105.0": dead(105.0), "110.0": dead(110.0),
            }},
            "puts": {},
        }
        merged = merge_sources(yf_chain, _empty_chain("TEST"))
        bucket = merged["calls"][exp]
        assert set(bucket) == {"100.0", "105.0", "110.0"}
        for contract in bucket.values():
            # Whole-bucket degeneracy gate: no quote-group field is trusted
            # from any contract in this bucket, even though a lone zero bid
            # would otherwise be individually acceptable.
            assert "bid" not in contract
            assert "ask" not in contract
            assert "iv" not in contract

    def test_two_failing_one_passing_bucket_trusts_only_the_passing_contract(self):
        from src.options_chain_merge import merge_sources

        exp = _future_exp_key(5)
        dead = lambda strike: _contract(strike=strike, expiration=exp, bid=0.0, ask=0.0, iv=0.0)
        alive = _contract(strike=110.0, expiration=exp, bid=2.0, ask=2.2, iv=0.3)
        yf_chain = {
            "symbol": "TEST",
            "calls": {exp: {
                "100.0": dead(100.0), "105.0": dead(105.0), "110.0": alive,
            }},
            "puts": {},
        }
        merged = merge_sources(yf_chain, _empty_chain("TEST"))
        bucket = merged["calls"][exp]
        # Below the >=3-all-failing threshold at the bucket level (only 2 of
        # 3 fail), so gate_bucket trusts the bucket and per-contract gating
        # runs normally.
        assert "ask" not in bucket["100.0"]
        assert "ask" not in bucket["105.0"]
        assert bucket["110.0"]["ask"] == 2.2
        assert bucket["110.0"]["iv"] == 0.3


class TestSourcePartialFieldPrecedence:
    """TradingView supplying only one half of its quote group (only iv, or
    only ask) must still contribute exactly that field — field-level
    precedence, not all-or-nothing per source."""

    def test_tv_supplies_only_iv_yfinance_ask_still_wins(self):
        from src.options_chain_merge import merge_sources

        exp = _future_exp_key(5)
        yf_chain = {
            "symbol": "TEST",
            "calls": {exp: {"100.0": _contract(strike=100.0, expiration=exp, bid=4.0, ask=4.2, iv=0.20)}},
            "puts": {},
        }
        tv_chain = {
            "symbol": "TEST",
            # TV quotes only an implied vol this cycle — no bid/ask at all.
            "calls": {exp: {"100.0": _contract(strike=100.0, expiration=exp, bid=0.0, ask=0.0, iv=0.35)}},
            "puts": {},
        }
        merged = merge_sources(yf_chain, tv_chain)
        contract = merged["calls"][exp]["100.0"]
        assert contract["iv"] == 0.35  # TV's valid iv wins
        assert contract["ask"] == 4.2  # TV's ask was invalid; yfinance's stands

    def test_tv_supplies_only_ask_yfinance_iv_still_wins(self):
        from src.options_chain_merge import merge_sources

        exp = _future_exp_key(5)
        yf_chain = {
            "symbol": "TEST",
            "calls": {exp: {"100.0": _contract(strike=100.0, expiration=exp, bid=4.0, ask=4.2, iv=0.20)}},
            "puts": {},
        }
        tv_chain = {
            "symbol": "TEST",
            # TV quotes only an ask this cycle — no usable iv.
            "calls": {exp: {"100.0": _contract(strike=100.0, expiration=exp, bid=4.5, ask=4.6, iv=0.0)}},
            "puts": {},
        }
        merged = merge_sources(yf_chain, tv_chain)
        contract = merged["calls"][exp]["100.0"]
        assert contract["ask"] == 4.6  # TV's valid ask wins
        assert contract["iv"] == 0.20  # TV's iv was invalid; yfinance's stands


class TestMalformedExpirationKeyRobustness:
    """Danny's G5 ("unparseable expiration keys are immortal" in the old
    code): fresh fetches can never introduce a malformed key
    (`merge_sources` rejects them at ingestion — Rule S3), and a *hydrated*
    prior chain that somehow still carries one (legacy shard, hand-written
    data, or anything written before that rule existed) is defensively
    dropped by `merge_prior` itself ("never propagate a junk key that
    somehow got this far"). So the full refresh pipeline must not crash on
    this, and G5 is actually self-healing on the very next refresh rather
    than immortal — verified end-to-end here."""

    def test_malformed_prior_expiration_key_is_dropped_not_immortalized(self, monkeypatch):
        store = _FakeStore(hydrate_data={
            "symbol": "TEST",
            "calls": {"2026-08-21": {"100.0": _contract(strike=100.0, expiration="2026-08-21", bid=1.0)}},
            "puts": {},
        })
        cache = OptionsChainCache(ttl_seconds=1800, store=store)
        exp = _future_exp_key(5)
        yf_chain = {"symbol": "TEST", "calls": {exp: {"105.0": _contract(strike=105.0, expiration=exp)}}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain, _empty_chain("TEST"))

        result = json.loads(run_async(cache.refresh("TEST")))
        # No crash, and the malformed key is cleaned up (defense-in-depth
        # in merge_prior) rather than surviving forever; the well-formed
        # key from this cycle's live fetch is present as expected.
        assert "2026-08-21" not in result["calls"]
        assert exp in result["calls"]


class TestCarriedForwardContractShape:
    """The exact handoff shape a carried-forward contract presents to
    downstream consumers (`options_math.executable_buyback_ask`, used by
    `agent_runner.py`/`options_chain_filters.py`/`roll_table.py`/
    `dps_scorer.py`/`rule_evaluator.py`) — verified at the pure-function
    boundary, without asserting on any of those consumers' own logic
    (out of Rusty's charter). A contract whose live quote degenerates one
    cycle must still expose the *previously accepted* ask to
    `executable_buyback_ask`, and must always carry a freshly recomputed
    (non-stale) delta. Note: `_meta.carried` tracks whether a source
    reported the contract *at all* this cycle (True only when the contract
    is entirely absent from live data) — a degenerate-but-present quote is
    `carried=False` even though its field values are effectively carried
    from prior, which is exactly what this test locks in."""

    def test_carried_contract_keeps_executable_ask_and_gets_fresh_delta(self, monkeypatch):
        from src.options_math import executable_buyback_ask

        store = _FakeStore()
        cache = OptionsChainCache(ttl_seconds=1800, store=store)
        exp = _future_exp_key(20)

        # Cycle 1: a good, executable quote.
        cycle_1_yf = {
            "symbol": "TEST",
            "calls": {exp: {"100.0": _contract(strike=100.0, expiration=exp, bid=4.8, ask=5.0, iv=0.30)}},
            "puts": {},
        }
        _patch_sources(monkeypatch, cache, cycle_1_yf, _empty_chain("TEST"))
        first = json.loads(run_async(cache.refresh("TEST")))
        first_asof = first["calls"][exp]["100.0"]["_meta"]["quote_asof"]
        assert executable_buyback_ask(first["calls"][exp]["100.0"].get("ask")) == 5.0

        # Cycle 2: the feed goes degenerate for this exact contract (a
        # bad/failed quote reported by the source, not an omission),
        # everything else unchanged.
        cycle_2_yf = {
            "symbol": "TEST",
            "calls": {exp: {"100.0": _contract(strike=100.0, expiration=exp, bid=0.0, ask=0.0, iv=0.0)}},
            "puts": {},
        }
        _patch_sources(monkeypatch, cache, cycle_2_yf, _empty_chain("TEST"))
        second = json.loads(run_async(cache.refresh("TEST")))
        carried = second["calls"][exp]["100.0"]

        # The prior valid ask survives — downstream buyback math keeps
        # working off real, executable data, never a wiped-out zero.
        assert executable_buyback_ask(carried.get("ask")) == 5.0
        # Reported-but-degenerate is NOT the same as omitted: carried=False
        # here (the source DID speak this cycle, just with junk values).
        assert carried["_meta"]["carried"] is False
        # No accepted field this cycle => provenance timestamp does not
        # advance — this genuinely IS last-known-good, not freshly live.
        assert carried["_meta"]["quote_asof"] == first_asof
        # Greeks are never carried as observations — always freshly
        # recomputed this cycle (still a real number, not stale/None).
        assert carried["delta"] is not None
        assert isinstance(carried["delta"], float)


# ===========================================================================
# P1 follow-up (Livingston, 2026-08-18) — get_or_load sync-in-async bridge
# ===========================================================================
#
# Danny approved D1-D5, then opened this separate P1 before production: the
# per-symbol OS lock (D4) made get_or_load's pre-existing sync-in-async
# bridge (`ThreadPoolExecutor().submit(...).result(timeout=120)`) able to
# self-deadlock the calling event loop under contention — reachable from
# web/app.py:3249's synchronous `cache.get_or_load(symbol)` call inside the
# async `api_activity_chat` endpoint — especially when hydrate() returns
# None (a true cold miss). The tests below are additive only; every test
# above this point is unmodified and still exercises the exact same
# contract it always did (see 34/34 pass count in the task report).

from src.options_chain_cache import OptionsChainNotReadyError


class TestGetOrLoadRunningLoopNeverBlocks:
    """P1 regression: `get_or_load`, called synchronously from *within* a
    running event loop (the exact web/app.py:3249 shape — a plain function
    call from inside an `async def`, not `await`ed and not offloaded), must
    never block that loop on a true cold miss — not even when this exact
    symbol's OS lock is already held by another coroutine scheduled on that
    SAME loop, which is precisely the shape that could previously
    self-deadlock: the old code froze the loop waiting (via a thread-pool
    `.result(timeout=120)` bridge) for a refresh that itself needed that
    very (now-frozen) loop to run in order to release the lock."""

    def test_cold_miss_with_same_loop_lock_holder_fails_fast_without_blocking(self, monkeypatch):
        store = _FakeStore()  # hydrate_data=None by default -> true cold miss
        cache = OptionsChainCache(ttl_seconds=1800, store=store)
        symbol = "DEADLOCK"
        os_lock = cache._get_symbol_os_lock(symbol)

        async def main():
            heartbeat_ticks = {"n": 0}
            stop_heartbeat = asyncio.Event()
            holder_started = asyncio.Event()
            lock_released = asyncio.Event()

            async def heartbeat():
                # Proves the loop keeps making progress on OTHER
                # coroutines throughout this whole scenario -- the old,
                # buggy `get_or_load` would starve this entirely for up to
                # 120s (or forever, absent the timeout) under this exact
                # same-loop lock-holder contention.
                while not stop_heartbeat.is_set():
                    heartbeat_ticks["n"] += 1
                    await asyncio.sleep(0.005)

            async def lock_holder():
                # Simulates an in-flight, same-loop refresh already
                # holding this symbol's OS lock (e.g. get_or_load_async's
                # own SWR background task, mid-cycle) -- the scenario the
                # old blocking bridge could deadlock against.
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, os_lock.acquire)
                holder_started.set()
                try:
                    await lock_released.wait()
                finally:
                    os_lock.release()

            async def caller():
                await holder_started.wait()
                start = time.monotonic()
                with pytest.raises(OptionsChainNotReadyError):
                    cache.get_or_load(symbol)
                elapsed = time.monotonic() - start
                lock_released.set()
                stop_heartbeat.set()
                return elapsed

            results = await asyncio.gather(heartbeat(), lock_holder(), caller())
            return heartbeat_ticks["n"], results[2]

        ticks, elapsed = run_async(main())

        # Must fail fast -- nowhere near the old 120s bridge timeout, let
        # alone actually deadlock.
        assert elapsed < 1.0, f"get_or_load blocked the calling loop for {elapsed:.3f}s on a cold miss"
        # The heartbeat coroutine, scheduled on the SAME loop, must have
        # kept ticking the whole time -- proof the loop was never frozen.
        assert ticks > 0, "event loop heartbeat never advanced -- loop was blocked"

    def test_cold_miss_schedules_background_refresh_that_populates_cache(self, monkeypatch):
        """The explicit failure is not a dead end: get_or_load schedules a
        background refresh for the symbol (reusing the same non-blocking
        try-acquire `_schedule_background_refresh` already uses for SWR),
        so once that task lands, a subsequent read succeeds without ever
        having blocked the original caller's loop."""
        store = _FakeStore()
        cache = OptionsChainCache(ttl_seconds=1800, store=store)
        symbol = "TEST"
        exp = _future_exp_key(5)
        yf_chain = {"symbol": symbol, "calls": {exp: {"100.0": _contract(bid=1.0, ask=1.2, strike=100.0, expiration=exp)}}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain, _empty_chain(symbol))

        async def main():
            with pytest.raises(OptionsChainNotReadyError):
                cache.get_or_load(symbol)
            # Let the scheduled background task (asyncio.create_task,
            # already running on this same loop) actually run to
            # completion.
            for _ in range(50):
                await asyncio.sleep(0.01)
                if cache.get(symbol) is not None:
                    break
            return cache.get(symbol)

        populated = run_async(main())
        assert populated is not None
        assert json.loads(populated)["calls"][exp]["100.0"]["bid"] == 1.0

    def test_cold_miss_does_not_duplicate_an_already_inflight_background_refresh(self, monkeypatch):
        """'reused when possible without cross-loop Task misuse': if a
        background refresh for this symbol is already in flight (its OS
        lock already held) when a running-loop caller hits a cold miss,
        get_or_load must not start a second one -- it only needs to fail
        fast and let the existing one finish."""
        store = _FakeStore()
        cache = OptionsChainCache(ttl_seconds=1800, store=store)
        symbol = "TEST"
        fetch_calls = {"n": 0}

        async def _fake_yf(sym):
            fetch_calls["n"] += 1
            # A genuine suspension point so the background task is still
            # demonstrably in flight (holding the OS lock) at the moment
            # get_or_load's cold-miss branch runs below, rather than
            # racing to completion within a single scheduler tick.
            await asyncio.sleep(0.05)
            return _empty_chain(sym)

        async def _fake_tv(sym):
            return _empty_chain(sym)

        monkeypatch.setattr(cache, "_fetch_yfinance", _fake_yf)
        monkeypatch.setattr(cache, "_fetch_tradingview", _fake_tv)

        async def main():
            # Already-in-flight background refresh, exactly as
            # get_or_load_async's SWR path would schedule it.
            cache._schedule_background_refresh(symbol)
            await asyncio.sleep(0)  # let it start and acquire the OS lock

            with pytest.raises(OptionsChainNotReadyError):
                cache.get_or_load(symbol)

            for _ in range(50):
                await asyncio.sleep(0.01)
                if cache.get(symbol) is not None:
                    break

        run_async(main())
        assert fetch_calls["n"] == 1, "cold miss under a running loop must not duplicate an in-flight refresh"


class TestGetOrLoadSyncCallerBehaviorPreserved:
    """'sync callers preserve behavior': when there is NO event loop
    running on the calling thread (a script, a background worker thread,
    etc. -- the case this cache was already correctly handling), a true
    cold miss still blocks that thread and returns real, freshly-fetched
    data synchronously -- completely unchanged from before this P1 fix."""

    def test_true_sync_caller_cold_miss_still_blocks_and_returns_real_data(self, monkeypatch):
        store = _FakeStore()
        cache = OptionsChainCache(ttl_seconds=1800, store=store)
        exp = _future_exp_key(5)
        yf_chain = {"symbol": "TEST", "calls": {exp: {"100.0": _contract(bid=1.0, ask=1.2, strike=100.0, expiration=exp)}}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain, _empty_chain("TEST"))

        # This test function itself has no running event loop -- exactly
        # the pre-existing get_or_load() tests elsewhere in this file.
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()

        result = json.loads(cache.get_or_load("TEST"))
        assert result["calls"][exp]["100.0"]["bid"] == 1.0

    def test_true_sync_caller_from_worker_thread_cold_miss_unaffected_by_other_symbols_lock(self, monkeypatch):
        """A genuine background-thread caller (no loop running on that
        thread) must still get a real, fully-refreshed result even while
        an unrelated symbol's refresh is in flight elsewhere -- different
        symbols remain fully independent, unchanged by this fix."""
        store = _FakeStore()
        cache = OptionsChainCache(ttl_seconds=1800, store=store)
        exp = _future_exp_key(5)

        async def _fake_yf(symbol):
            return {"symbol": symbol, "calls": {exp: {"100.0": _contract(bid=1.0, ask=1.2, strike=100.0, expiration=exp)}}, "puts": {}}

        async def _fake_tv(symbol):
            return _empty_chain(symbol)

        monkeypatch.setattr(cache, "_fetch_yfinance", _fake_yf)
        monkeypatch.setattr(cache, "_fetch_tradingview", _fake_tv)

        other_lock = cache._get_symbol_os_lock("OTHER")
        other_lock.acquire()
        try:
            results = {}

            def worker():
                results["value"] = cache.get_or_load("TEST")

            t = threading.Thread(target=worker)
            t.start()
            t.join(timeout=10)
            assert not t.is_alive()
        finally:
            other_lock.release()

        assert json.loads(results["value"])["calls"][exp]["100.0"]["bid"] == 1.0
