"""
Integration test suite for the persistent options chain seam
(D5, Livingston 2026-08-18 revision directive).

Composes the **real** `OptionsChainStore` (src/options_chain_store.py) with
the **real**, frozen `src/options_chain_merge.py` pure functions through the
**real** `OptionsChainCache` (src/options_chain_cache.py) — the only fake in
this whole file is the CosmosDB container client (`FakeContainer`, an
in-memory stand-in with real ETag/CAS semantics) and the two network-facing
provider fetchers (`_fetch_yfinance`/`_fetch_tradingview`, which no
hermetic test anywhere in this suite is allowed to hit for real). Nothing
fakes the seam between the store and the merge module themselves — that is
precisely the composition the 2026-08-18 review found broken behind mutual
unit-test fakes (`test_options_chain_store.py`'s old `fake_merge_module` and
`test_options_chain_cache.py`'s `_FakeStore`, both of which still exist and
remain valid *unit* tests, but neither exercises this real seam end to end).

Covers Danny's required acceptance tests R1-R7 verbatim:
  R1 - >=3 real persist cycles -> hydrated chain has mid + all five greeks
       for every contract; `_meta.greeks_valid` matches reality (True only
       when iv/underlying_price/strike are all genuinely valid).
  R2 - Cold replica: hydrate -> `filter_options_chain_by_delta` yields the
       same contracts as the producing instance's in-memory chain.
  R3 - A carried, TradingView-sourced quote survives two persists with
       `quote_asof`/`quote_source`/`carried`/`first_seen` unchanged.
  R4 - Hydrated chain excludes expirations before today ET while the shard
       still exists inside the grace window; the hydrated entry is
       immediately stale-eligible and the very next read schedules a real
       background refresh.
  R5 - Hydrated payload carries top-level `symbol`, `timestamp`,
       `underlying_price`.
  R6 - Two `await cache.refresh(sym)` coroutines on one event loop cause
       exactly one fetch cycle; a refresh whose OS lock is held by another
       thread does not block this loop (proven via heartbeat ticks, not
       elapsed time alone).
  R7 - Unchanged market-observable data across cycles causes no shard
       rewrite (the `_content_hash` write-skip guard from D5 is effective).
"""

import asyncio
import copy
import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from azure.core import MatchConditions
from azure.cosmos.exceptions import (
    CosmosHttpResponseError,
    CosmosResourceNotFoundError,
)

from src.options_chain_cache import OptionsChainCache
from src.options_chain_store import OptionsChainStore
from src.options_chain_filters import filter_options_chain_by_delta


# ===========================================================================
# Fake Cosmos container (the ONLY fake in this file)
# ===========================================================================

class FakeContainer:
    """In-memory stand-in for a Cosmos container client — real ETag/CAS
    semantics, no real network/Cosmos connectivity. Mirrors
    `test_options_chain_store.py`'s `FakeContainer` (kept independent/
    duplicated here rather than imported, so this integration file has no
    dependency on another test module's internals)."""

    def __init__(self):
        self.store: dict[str, dict] = {}
        self._etag_counter = 0

    def _next_etag(self) -> str:
        self._etag_counter += 1
        return f"etag-{self._etag_counter}"

    def read_item(self, item, partition_key):
        doc = self.store.get(item)
        if doc is None:
            raise CosmosResourceNotFoundError()
        return copy.deepcopy(doc)

    def create_item(self, body):
        doc = copy.deepcopy(body)
        doc["_etag"] = self._next_etag()
        self.store[doc["id"]] = doc
        return copy.deepcopy(doc)

    def replace_item(self, item, body, etag=None, match_condition=None):
        current = self.store.get(item)
        if (
            match_condition == MatchConditions.IfNotModified
            and current is not None
            and current.get("_etag") != etag
        ):
            raise CosmosHttpResponseError(status_code=412, message="Precondition failed")
        doc = copy.deepcopy(body)
        doc["_etag"] = self._next_etag()
        self.store[item] = doc
        return copy.deepcopy(doc)

    def delete_item(self, item, partition_key):
        if item not in self.store:
            raise CosmosResourceNotFoundError()
        del self.store[item]

    def query_items(self, query, parameters=None, partition_key=None):
        params = {p["name"]: p["value"] for p in (parameters or [])}
        symbol = params.get("@s")
        return [
            copy.deepcopy(doc) for doc in self.store.values()
            if doc.get("symbol") == symbol
        ]


# ===========================================================================
# Fixtures / helpers
# ===========================================================================

def run_async(coro):
    """Drive a coroutine on an isolated event loop (matches the pattern
    used elsewhere in this suite; avoids `asyncio.run()`'s global policy
    side effects)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _future_exp_key(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y%m%d")


def _past_exp_key(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y%m%d")


def _source_contract(**overrides):
    """Raw single-source (yfinance/TradingView) contract shape — the input
    `merge_sources`/`merge_prior` actually consume, distinct from an
    already-merged persisted contract (see `_persisted_contract` below)."""
    base = {
        "contractSymbol": "AAPL260101C00100000",
        "strike": 100.0,
        "bid": 1.0,
        "ask": 1.2,
        "iv": 0.25,
        "lastPrice": 1.1,
        "lastTradeDate": "2026-01-01T00:00:00Z",
        "volume": 10,
        "openInterest": 100,
        "inTheMoney": False,
        "expiration": "20260821",
        "option_type": "call",
    }
    base.update(overrides)
    return base


def _persisted_contract(**overrides):
    """An already-merged, previously-recomputed contract, as it would
    exist in a shard written by a prior refresh cycle -- used only to seed
    the store directly (bypassing the cache) for tests that need
    pre-existing persisted history (R4)."""
    base = {
        "contractSymbol": "AAPL260101C00100000",
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
        "lastTradeDate": "2026-01-01T00:00:00Z",
        "inTheMoney": False,
        "expiration": "20260821",
        "option_type": "call",
        "_meta": {
            "quote_asof": "2026-08-10T00:00:00Z",
            "quote_source": "yfinance",
            "carried": False,
            "first_seen": "2026-08-01T00:00:00Z",
            "last_seen": "2026-08-10T00:00:00Z",
            "greeks_valid": True,
        },
    }
    base.update(overrides)
    return base


@pytest.fixture
def container():
    return FakeContainer()


@pytest.fixture
def store(container):
    return OptionsChainStore(container=container)


async def _empty_source(sym):
    return {"symbol": sym, "calls": {}, "puts": {}}


# ===========================================================================
# R1 — derived fields + greeks_valid survive >= 3 real persist cycles
# ===========================================================================

class TestR1DerivedFieldsSurviveMultiplePersistCycles:
    def test_mid_and_all_five_greeks_present_after_three_cycles(self, store, monkeypatch):
        cache = OptionsChainCache(ttl_seconds=1800, store=store)
        symbol = "AAPL"
        call_exp = _future_exp_key(10)
        put_exp = _future_exp_key(12)

        async def _fake_yf(sym):
            return {
                "symbol": symbol,
                "underlying_price": 150.0,
                "calls": {
                    call_exp: {
                        "100.0": _source_contract(expiration=call_exp, strike=100.0),
                        # Ask valid (gate passes -> quoted) but iv invalid:
                        # `greeks_valid` must reflect this specific
                        # contract's reality (False), proving the flag is
                        # not just trivially True across the board.
                        "110.0": _source_contract(
                            expiration=call_exp, strike=110.0, iv=0.0,
                            contractSymbol="AAPL260101C00110000",
                        ),
                    }
                },
                "puts": {
                    put_exp: {
                        "95.0": _source_contract(
                            expiration=put_exp, strike=95.0, option_type="put",
                            contractSymbol="AAPL260101P00095000",
                        )
                    }
                },
            }

        monkeypatch.setattr(cache, "_fetch_yfinance", _fake_yf)
        monkeypatch.setattr(cache, "_fetch_tradingview", _empty_source)

        for _ in range(3):
            run_async(cache.refresh(symbol))

        hydrated = store.hydrate(symbol)
        assert hydrated is not None

        call_100 = hydrated["calls"][call_exp]["100.0"]
        for field in ("mid", "delta", "gamma", "theta", "vega", "rho"):
            assert call_100.get(field) is not None, f"calls/100.0 missing {field} after 3 persist cycles"
        assert call_100["_meta"]["greeks_valid"] is True

        call_110 = hydrated["calls"][call_exp]["110.0"]
        # `mid` is still a real number -- bid/ask were both usable quotes,
        # and `mid` is only nulled when neither is (it is not Greek-tied).
        assert call_110.get("mid") is not None
        # iv was invalid every cycle for this contract, so `greeks_valid`
        # must honestly reflect that (False) -- and, per Rule Z3 (a
        # manufactured intrinsic-only Greek is no better than a
        # manufactured 0.0), the five Greeks themselves must be `null`,
        # never a fabricated value, when they are not genuinely valid.
        for field in ("delta", "gamma", "theta", "vega", "rho"):
            assert call_110.get(field) is None, (
                f"calls/110.0 {field} must be null (not fabricated) when greeks_valid is False"
            )
        assert call_110["_meta"]["greeks_valid"] is False

        put_95 = hydrated["puts"][put_exp]["95.0"]
        for field in ("mid", "delta", "gamma", "theta", "vega", "rho"):
            assert put_95.get(field) is not None
        assert put_95["_meta"]["greeks_valid"] is True


# ===========================================================================
# R2 — cold replica hydrate matches the producer's in-memory delta filter
# ===========================================================================

class TestR2ColdReplicaFilterParity:
    def test_cold_replica_delta_filter_matches_producer(self, store, monkeypatch):
        producer = OptionsChainCache(ttl_seconds=1800, store=store)
        symbol = "AAPL"
        exp = _future_exp_key(10)

        async def _fake_yf(sym):
            return {
                "symbol": symbol,
                "underlying_price": 100.0,
                "calls": {
                    exp: {
                        # Near-the-money -> mid-range delta, survives the
                        # default filter_options_chain_by_delta window.
                        "100.0": _source_contract(expiration=exp, strike=100.0),
                        # Deep OTM, short-dated, low iv -> delta near zero,
                        # filtered out by both producer and replica alike.
                        "300.0": _source_contract(
                            expiration=exp, strike=300.0, iv=0.15,
                            contractSymbol="AAPL260101C00300000",
                        ),
                    }
                },
                "puts": {},
            }

        monkeypatch.setattr(producer, "_fetch_yfinance", _fake_yf)
        monkeypatch.setattr(producer, "_fetch_tradingview", _empty_source)

        for _ in range(2):
            run_async(producer.refresh(symbol))

        producer_chain = json.loads(producer.get(symbol))
        producer_filtered = filter_options_chain_by_delta(producer_chain)
        assert producer_filtered["calls"], "producer's own filtered chain must be non-empty for this to be a real check"

        # A cold replica: a brand-new cache instance (empty in-memory
        # state, simulating a second pod/process), sharing only the real
        # persistence store/container.
        replica = OptionsChainCache(ttl_seconds=1800, store=store)

        async def _boom(sym):
            raise AssertionError("cold replica must not call a provider on a hydrate hit")

        monkeypatch.setattr(replica, "_fetch_yfinance", _boom)
        monkeypatch.setattr(replica, "_fetch_tradingview", _boom)

        replica_chain = json.loads(run_async(replica.get_or_load_async(symbol)))
        replica_filtered = filter_options_chain_by_delta(replica_chain)

        assert replica_filtered["calls"].keys() == producer_filtered["calls"].keys()
        for exp_key, strikes in producer_filtered["calls"].items():
            assert set(replica_filtered["calls"][exp_key].keys()) == set(strikes.keys())


# ===========================================================================
# R3 — carried, TV-sourced provenance survives two persists verbatim
# ===========================================================================

class TestR3ProvenanceSurvivesCarry:
    def test_carried_tv_sourced_quote_survives_two_persists(self, store, monkeypatch):
        cache = OptionsChainCache(ttl_seconds=1800, store=store)
        symbol = "AAPL"
        exp = _future_exp_key(20)

        # Cycle 1: TradingView is the only source that quotes this
        # contract at all (yfinance omits it entirely) -> quote_source
        # must end up "tradingview".
        async def _fake_yf_absent(sym):
            return {"symbol": symbol, "underlying_price": 150.0, "calls": {}, "puts": {}}

        async def _fake_tv_present(sym):
            return {"symbol": symbol, "calls": {exp: {"100.0": _source_contract(expiration=exp)}}, "puts": {}}

        monkeypatch.setattr(cache, "_fetch_yfinance", _fake_yf_absent)
        monkeypatch.setattr(cache, "_fetch_tradingview", _fake_tv_present)
        run_async(cache.refresh(symbol))

        first_meta = json.loads(cache.get(symbol))["calls"][exp]["100.0"]["_meta"]
        assert first_meta["quote_source"] == "tradingview"
        assert first_meta["carried"] is False

        # Cycles 2 and 3: the contract is entirely absent from BOTH fresh
        # sources -- it must be carried forward (not dropped), twice, and
        # persisted twice, per Danny's acceptance wording.
        monkeypatch.setattr(cache, "_fetch_yfinance", _empty_source)
        monkeypatch.setattr(cache, "_fetch_tradingview", _empty_source)
        run_async(cache.refresh(symbol))
        run_async(cache.refresh(symbol))

        in_memory_meta = json.loads(cache.get(symbol))["calls"][exp]["100.0"]["_meta"]
        assert in_memory_meta["carried"] is True
        assert in_memory_meta["quote_source"] == first_meta["quote_source"]
        assert in_memory_meta["quote_asof"] == first_meta["quote_asof"]
        assert in_memory_meta["first_seen"] == first_meta["first_seen"]

        # The round trip through persistence (D2's fix): the store must
        # transport `_meta` verbatim, never manufacture it.
        hydrated = store.hydrate(symbol)
        hydrated_meta = hydrated["calls"][exp]["100.0"]["_meta"]
        assert hydrated_meta == in_memory_meta


# ===========================================================================
# R4 — hydrate applies the same-day serving prune and is stale-eligible
# ===========================================================================

class TestR4HydratePrunesPastExpirationsAndIsStaleEligible:
    def test_hydrate_prunes_past_expiration_and_schedules_a_real_refresh(self, store, container, monkeypatch):
        symbol = "AAPL"
        past_exp = _past_exp_key(1)  # yesterday -- inside the 7-day persistence grace window
        future_exp = _future_exp_key(10)

        # Seed persisted history directly via the real store (simulating a
        # prior day's refresh cycle whose data has since aged past its
        # contract expiration but is still inside the persistence grace
        # window -- exactly the scenario D3 must handle at serving time).
        chain = {
            "calls": {
                past_exp: {"100.0": _persisted_contract(expiration=past_exp)},
                future_exp: {"100.0": _persisted_contract(expiration=future_exp)},
            },
            "puts": {},
            "underlying_price": 150.0,
        }
        store.persist(symbol, chain)
        assert f"optchain_{symbol}_{past_exp}" in container.store, (
            "the past-expiration shard must still exist in Cosmos (grace window) "
            "for this to be a meaningful serving-prune test"
        )

        cache = OptionsChainCache(ttl_seconds=1800, store=store)

        async def _boom(sym):
            raise AssertionError("must not call a provider on a hydrate hit")

        monkeypatch.setattr(cache, "_fetch_yfinance", _boom)
        monkeypatch.setattr(cache, "_fetch_tradingview", _boom)

        hydrated = json.loads(run_async(cache.get_or_load_async(symbol)))
        assert future_exp in hydrated["calls"]
        assert past_exp not in hydrated["calls"], "yesterday's expiration must be pruned from served data"

        # Immediately stale-eligible: the very next staleness check must be
        # True (not "fresh for a full TTL window").
        assert cache.is_stale(symbol) is True

        # And the NEXT read must actually schedule (and this test lets run)
        # a real background refresh -- not just flip an internal flag.
        fetch_calls = {"n": 0}

        async def _fake_yf(sym):
            fetch_calls["n"] += 1
            return {
                "symbol": symbol, "underlying_price": 150.0,
                "calls": {future_exp: {"100.0": _source_contract(expiration=future_exp)}},
                "puts": {},
            }

        monkeypatch.setattr(cache, "_fetch_yfinance", _fake_yf)
        monkeypatch.setattr(cache, "_fetch_tradingview", _empty_source)

        async def _second_read_and_settle():
            result = await cache.get_or_load_async(symbol)
            # Give the fire-and-forget background refresh task a moment to
            # actually run on this same loop before we tear it down.
            await asyncio.sleep(0.1)
            return result

        run_async(_second_read_and_settle())
        assert fetch_calls["n"] >= 1, "a stale hydrate hit must schedule a real background refresh on next read"


# ===========================================================================
# R5 — hydrated payload restores symbol/timestamp/underlying_price
# ===========================================================================

class TestR5HydratedPayloadTopLevelFields:
    def test_symbol_timestamp_underlying_price_restored(self, store, monkeypatch):
        cache = OptionsChainCache(ttl_seconds=1800, store=store)
        symbol = "AAPL"
        exp = _future_exp_key(10)

        async def _fake_yf(sym):
            return {
                "symbol": symbol, "underlying_price": 187.65,
                "calls": {exp: {"100.0": _source_contract(expiration=exp)}},
                "puts": {},
            }

        monkeypatch.setattr(cache, "_fetch_yfinance", _fake_yf)
        monkeypatch.setattr(cache, "_fetch_tradingview", _empty_source)
        run_async(cache.refresh(symbol))

        replica = OptionsChainCache(ttl_seconds=1800, store=store)

        async def _boom(sym):
            raise AssertionError("must not call a provider on a hydrate hit")

        monkeypatch.setattr(replica, "_fetch_yfinance", _boom)
        monkeypatch.setattr(replica, "_fetch_tradingview", _boom)

        hydrated = json.loads(run_async(replica.get_or_load_async(symbol)))
        assert hydrated["symbol"] == symbol
        assert hydrated.get("timestamp")
        assert hydrated["underlying_price"] == 187.65


# ===========================================================================
# R6 — concurrency correctness: same-loop dedup + cross-thread non-blocking
# ===========================================================================

class TestR6ConcurrencyCorrectness:
    def test_two_concurrent_same_loop_refreshes_cause_exactly_one_fetch(self, store, monkeypatch):
        cache = OptionsChainCache(ttl_seconds=1800, store=store)
        symbol = "AAPL"
        exp = _future_exp_key(10)
        fetch_count = {"n": 0}

        async def _fake_yf(sym):
            fetch_count["n"] += 1
            await asyncio.sleep(0.05)
            return {
                "symbol": symbol, "underlying_price": 150.0,
                "calls": {exp: {"100.0": _source_contract(expiration=exp)}},
                "puts": {},
            }

        monkeypatch.setattr(cache, "_fetch_yfinance", _fake_yf)
        monkeypatch.setattr(cache, "_fetch_tradingview", _empty_source)

        async def _run_both():
            return await asyncio.gather(cache.refresh(symbol), cache.refresh(symbol))

        result1, result2 = run_async(_run_both())
        assert fetch_count["n"] == 1, "two concurrent same-loop refresh() calls must fetch exactly once"
        assert result1 == result2, "both concurrent callers must see the identical merged result"

    def test_cross_thread_refresh_holding_the_lock_does_not_block_this_loop(self, store, monkeypatch):
        cache = OptionsChainCache(ttl_seconds=1800, store=store)
        symbol = "AAPL"
        hold_seconds = 0.5

        call_count = {"n": 0}
        call_count_lock = threading.Lock()

        async def _dispatch_yf(sym):
            with call_count_lock:
                call_count["n"] += 1
                this_call = call_count["n"]
            if this_call == 1:
                # Simulates a scheduler-thread refresh's synchronous
                # provider call (yfinance is a blocking library under the
                # hood) -- this sleep runs on the scheduler thread's own
                # event loop, never on the loop under test below.
                time.sleep(hold_seconds)
            return {"symbol": sym, "underlying_price": 150.0, "calls": {}, "puts": {}}

        monkeypatch.setattr(cache, "_fetch_yfinance", _dispatch_yf)
        monkeypatch.setattr(cache, "_fetch_tradingview", _empty_source)

        # A "scheduler thread" refresh (exactly refresh_all's _sync_refresh
        # shape: its own OS thread, its own event loop) holding this
        # symbol's cross-thread OS lock for `hold_seconds`.
        scheduler_thread = threading.Thread(target=cache._sync_refresh, args=(symbol,))
        scheduler_thread.start()
        time.sleep(0.1)  # let the scheduler thread actually acquire the lock first

        heartbeats = {"n": 0}

        async def _heartbeat():
            while True:
                heartbeats["n"] += 1
                await asyncio.sleep(0.02)

        async def _main_loop_scenario():
            hb_task = asyncio.create_task(_heartbeat())
            start = time.monotonic()
            # Contends for the SAME symbol's OS lock, currently held by the
            # scheduler thread above -- must genuinely wait, but must do so
            # without ever blocking this loop's own thread.
            await cache.refresh(symbol)
            elapsed = time.monotonic() - start
            hb_task.cancel()
            try:
                await hb_task
            except asyncio.CancelledError:
                pass
            return elapsed

        elapsed = run_async(_main_loop_scenario())
        scheduler_thread.join(timeout=5)
        assert not scheduler_thread.is_alive()

        # The wait was real -- this call could only proceed once the
        # scheduler thread released the lock, ~hold_seconds later.
        assert elapsed >= hold_seconds * 0.6, "refresh() must have genuinely waited for the cross-thread lock"
        # Yet the loop kept ticking throughout that wait instead of
        # freezing -- if the OS lock were acquired via a blocking call
        # directly on this loop's thread (the pre-fix D4 bug), heartbeats
        # would be ~0 for the whole hold_seconds window. This is the
        # "assert on elapsed loop responsiveness, not on sleep timing
        # alone" requirement (R6).
        assert heartbeats["n"] >= 10, "the event loop must remain responsive while waiting on a cross-thread lock"


# ===========================================================================
# R7 — write-skip guard is effective against market-observable content
# ===========================================================================

class TestR7WriteSkipGuardEffective:
    def test_identical_market_data_across_cycles_causes_no_shard_rewrite(self, store, container, monkeypatch):
        cache = OptionsChainCache(ttl_seconds=1800, store=store)
        symbol = "AAPL"
        exp = _future_exp_key(15)

        async def _fake_yf(sym):
            return {
                "symbol": symbol, "underlying_price": 150.0,
                "calls": {exp: {"100.0": _source_contract(expiration=exp)}},
                "puts": {},
            }

        monkeypatch.setattr(cache, "_fetch_yfinance", _fake_yf)
        monkeypatch.setattr(cache, "_fetch_tradingview", _empty_source)

        run_async(cache.refresh(symbol))
        etag_after_first = container.store[f"optchain_{symbol}_{exp}"]["_etag"]

        # Second cycle: byte-identical fresh fetch. The only thing that
        # legitimately changes inside a real merge_prior cycle for an
        # unchanged, still-quoted contract is provenance
        # (`_meta.last_seen`/`quote_asof`) -- never the market-observable
        # payload itself.
        run_async(cache.refresh(symbol))
        etag_after_second = container.store[f"optchain_{symbol}_{exp}"]["_etag"]

        assert etag_after_second == etag_after_first, (
            "unchanged market-observable data must not trigger a shard rewrite (D5/R7); "
            "a content hash keyed on the volatile _meta timestamps would fail this"
        )


# ===========================================================================
# G3 — Danny's "Zero-Free Agent-Facing Option Chains" decision, as amended
# by the 2026-08-19 "Zero-never-overwrites-prior" decision (Linus): the
# original G3 headline test in this class asserted the raw persisted layer
# stores an incoming provider zero *verbatim* (`bid: 0.0`) and only the
# agent-facing view (`to_agent_view`, via `apply_agent_view`) nulls it.
# That "raw layer stores 0.0 byte-for-byte" behavior is now explicitly
# superseded: `options_chain_merge.merge_prior` (Linus, frozen) treats an
# incoming exact zero for `bid`/`lastPrice`/`volume`/`openInterest` as "no
# opinion" during accumulation -- it never overwrites a genuinely valid
# non-zero prior, and when there is no such prior either the field is
# omitted entirely (never introduced as a literal `0.0`/`0`). Since
# `merge_prior` is the sole producer of the chain this store persists
# (confirmed: `OptionsChainCache.refresh()` always calls
# `merge_prior(prior_chain or {}, live, now=now)`), the *raw persisted
# shard itself* now never contains a zero for those four fields either --
# there is no longer a "raw zero survives, view nulls it" story to test;
# instead the invariant to prove here is "zero is absorbed before it ever
# reaches persistence, and a genuinely valid prior is never clobbered by a
# later glitch/closed-market zero." `options_chain_view.py` needed no
# change (it already required a positive/finite quote), so the
# agent-facing layer keeps behaving identically -- it just never sees a
# literal zero to null in the first place.
# ===========================================================================

class TestG3RawZeroSurvivesWhileAgentViewIsNull:
    def test_cold_start_bid_zero_is_never_persisted_as_zero(self, store, monkeypatch):
        """No prior exists yet (cold start): an incoming exact bid=0.0 is
        "no opinion" (Rule Z12 / 2026-08-19 decision) -- it must not be
        introduced into the accumulated/persisted chain as a literal
        `0.0`. The field is simply absent, and the agent-facing view
        (which already required a positive/finite quote) still nulls it,
        unchanged."""
        from src.options_chain_cache import apply_agent_view

        cache = OptionsChainCache(ttl_seconds=1800, store=store)
        symbol = "AAPL"
        exp = _future_exp_key(10)

        async def _fake_yf(sym):
            return {
                "symbol": symbol,
                "underlying_price": 150.0,
                "calls": {
                    exp: {
                        # A genuine "no bid quoted" observation (closed
                        # market / provider glitch) with no prior to fall
                        # back to.
                        "100.0": _source_contract(expiration=exp, strike=100.0, bid=0.0),
                    }
                },
                "puts": {},
            }

        monkeypatch.setattr(cache, "_fetch_yfinance", _fake_yf)
        monkeypatch.setattr(cache, "_fetch_tradingview", _empty_source)

        for _ in range(2):
            run_async(cache.refresh(symbol))

        # Raw/persisted layer: a meaningless zero (no valid prior to
        # protect) is never written as a literal 0.0 -- the field is
        # simply absent from the accumulated/persisted contract.
        hydrated = store.hydrate(symbol)
        assert hydrated is not None
        raw_contract = hydrated["calls"][exp]["100.0"]
        assert raw_contract.get("bid") is None, (
            "an incoming exact bid=0.0 with no genuinely valid prior must never be "
            "persisted as a literal 0.0 (2026-08-19 zero-never-overwrites-prior decision)"
        )
        assert "bid" not in raw_contract

        # Agent-facing layer: unchanged behavior -- still null, still the
        # same field_status family, now simply because the field was
        # never populated rather than because the view nulled a stored 0.0.
        agent_view = apply_agent_view(hydrated)
        view_contract = agent_view["calls"][exp]["100.0"]
        assert view_contract["bid"] is None, (
            "the agent-facing view must never surface a numeric zero for bid (Z1/§2.4)"
        )
        assert view_contract["_meta"]["field_status"]["bid"] in (
            "no_market", "no_trades", "unavailable",
        )

    def test_genuinely_valid_prior_bid_survives_a_later_closed_market_zero(self, store, monkeypatch):
        """The other half of the 2026-08-19 invariant, and the reason it
        exists: a genuinely valid, previously-persisted bid must survive a
        *later* cycle where the provider reports an exact zero (closed
        market / transient glitch) -- the zero must never overwrite the
        last-known-good value in the persisted chain. Composes the real
        store + real merge_prior + real cache across two distinct
        refresh cycles, proving the protection holds through an actual
        persist/hydrate round trip, not just in `options_chain_merge.py`'s
        own unit tests."""
        cache = OptionsChainCache(ttl_seconds=1800, store=store)
        symbol = "AAPL"
        exp = _future_exp_key(10)

        async def _valid_quote(sym):
            return {
                "symbol": symbol,
                "underlying_price": 150.0,
                "calls": {exp: {"100.0": _source_contract(expiration=exp, strike=100.0, bid=2.5)}},
                "puts": {},
            }

        monkeypatch.setattr(cache, "_fetch_yfinance", _valid_quote)
        monkeypatch.setattr(cache, "_fetch_tradingview", _empty_source)
        run_async(cache.refresh(symbol))

        hydrated_after_first = store.hydrate(symbol)
        assert hydrated_after_first["calls"][exp]["100.0"]["bid"] == 2.5

        async def _closed_market_zero(sym):
            return {
                "symbol": symbol,
                "underlying_price": 150.0,
                "calls": {exp: {"100.0": _source_contract(expiration=exp, strike=100.0, bid=0.0)}},
                "puts": {},
            }

        monkeypatch.setattr(cache, "_fetch_yfinance", _closed_market_zero)
        run_async(cache.refresh(symbol))

        hydrated_after_second = store.hydrate(symbol)
        assert hydrated_after_second["calls"][exp]["100.0"]["bid"] == 2.5, (
            "a genuinely valid, already-persisted bid must survive a later "
            "closed-market/glitch zero -- the zero is 'no opinion,' never an overwrite"
        )

    def test_legacy_shard_migration_composes_with_agent_view_on_cold_hydrate(self, store, container):
        """A pre-G3 (schema_version < 4) shard with a fabricated
        `mid`/Greeks (the old bug the migration exists to correct) is
        lazily normalized by `hydrate()`, and the resulting chain is then
        agent-view-safe too -- proving the migration and the serving seam
        compose correctly end to end on a real store, not just in
        isolated unit tests of each piece."""
        from src.options_chain_cache import apply_agent_view

        symbol = "MSFT"
        exp = _future_exp_key(10)
        # A legacy contract: invalid iv (0.0) yet a fabricated non-zero
        # delta and mid -- exactly the pre-fix bug shape, with no _meta at
        # all (the oldest possible shard shape).
        legacy_contract = _persisted_contract(
            expiration=exp, iv=0.0, delta=0.5, mid=1.1, strike=100.0,
        )
        legacy_contract.pop("_meta", None)
        container.store[f"optchain_{symbol}_{exp}"] = {
            "id": f"optchain_{symbol}_{exp}", "symbol": symbol, "doc_type": "options_chain",
            "expiration": exp, "schema_version": 2,
            "calls": {"100.0": legacy_contract}, "puts": {},
            "underlying_price": 150.0, "_etag": "e1",
        }

        hydrated = store.hydrate(symbol)
        assert hydrated is not None
        raw_contract = hydrated["calls"][exp]["100.0"]
        # Migration nulled the fabricated derived Greeks (invalid iv)...
        assert raw_contract["delta"] is None
        # ...but `mid` is only nulled when neither bid nor ask is usable;
        # this legacy contract has a real bid/ask (1.0/1.2), so its `mid`
        # is a genuine, computable value and must be left untouched.
        assert raw_contract["mid"] == legacy_contract["mid"]
        # The observed raw fields (bid/ask/iv) are exactly what was
        # actually recorded, per Rule Z11 -- never touched by migration.
        assert raw_contract["bid"] == legacy_contract["bid"]
        assert raw_contract["iv"] == 0.0

        # The already-migrated (null Greeks) chain remains agent-view-safe.
        agent_view = apply_agent_view(hydrated)
        view_contract = agent_view["calls"][exp]["100.0"]
        assert view_contract["delta"] is None
