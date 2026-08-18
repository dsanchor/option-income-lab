"""Centralized options chain cache with stale-while-revalidate semantics.

Single source of truth for options chain data across the application.
All consumers (agents, DPS, web endpoints) go through this cache.

Refresh procedure for one symbol, run entirely under that symbol's exclusion
(see ``refresh``/``_refresh_exclusive`` below — Livingston's 2026-08-18 D4
revision) so the hydrate → fetch → merge → assign → persist sequence can
never interleave with another refresh of the same symbol:

  0. Hydrate the "prior" chain: in-memory if present, else read the
     persistence store (``src/options_chain_store.py``) so a cold process
     (restart, redeploy, or a process — e.g. the scheduler — that has never
     refreshed this symbol before) never treats an empty memory cache as
     "no history".
  1. Fetch from yfinance (all expirations) and TradingView (overlay) fresh.
  2. Merge the two fresh sources field-by-field
     (``options_chain_merge.merge_sources`` — TV preferred, gated).
  3. Accumulate against the hydrated prior, monotonically
     (``options_chain_merge.merge_prior``): contracts are unioned, absent
     contracts are carried forward verbatim, and only accepted per-field
     observations advance state.
  4. Recompute derived fields (mid/greeks) from the merged, current
     primitives (``options_chain_merge.recompute_derived``) — these are
     never merged/carried themselves, always freshly computed.
  5. Drop expiration buckets whose actual contract expiration date (America/
     New_York) has passed (``options_chain_merge.prune_by_expiration`` —
     "serving" prune; same-day cutoff).
  6. Assign the merged result in-memory atomically.
  7. Persist the result, sharded one document per expiration, best-effort
     (``OptionsChainStore.persist``) — failures are logged and counted, never
     raised; the in-memory assignment from step 6 already happened and is
     never touched by a persistence failure. A second, longer-horizon prune
     (``OptionsChainStore.prune_expired``, default 7-day grace after actual
     expiration) deletes shards from persistence.

TTL controls *freshness* (whether a background refetch is warranted), not
*availability* — cached entries are never deleted purely because they aged
past the TTL, and persistence is never touched by TTL either. Last-known-good
data stays readable indefinitely so consumers never regress to zeros just
because a refresh cycle was missed or a source temporarily returned bad data.
This eliminates the need for market-open detection — data is always the best
available merge of both sources plus prior history (memory- and, when
enabled, Cosmos-backed).

Persistence is optional: with no reachable CosmosDB (or
``options_chain_cache.persistence_enabled: false``), the cache degrades to
exactly today's memory-only behavior — every persistence call becomes a
no-op via ``OptionsChainStore.is_available() == False``.

Concurrency (D4, Livingston 2026-08-18): production runs this cache from two
different execution shapes for the *same* symbol — (a) several coroutines on
one asyncio event loop (the FastAPI request loop: concurrent requests, plus
`get_or_load_async`'s own background stale-while-revalidate task), and (b) a
completely separate OS thread with its own event loop (`refresh_all`'s
`ThreadPoolExecutor` workers, each running `_sync_refresh` in a fresh loop
via `asyncio.new_event_loop()`). One `threading.RLock` per symbol used to
guard both shapes at once: reentrancy meant two same-loop `await
refresh(sym)` calls could *both* run the full cycle (the same OS thread
"already owns" the lock, so the second reentrant `acquire()` succeeded
instead of waiting), and a *blocking* `acquire()` called directly from a
coroutine could freeze the entire event loop — not just that request — if a
scheduler-thread refresh already held it. Fixed with two purpose-built,
independent mechanisms that together restore "one refresh per symbol at a
time, no lost update" without the two failure modes above:

  * Same-loop coroutine de-duplication (`refresh`): an `asyncio.Task` per
    symbol is memoized while in flight; a second `await refresh(sym)` on the
    *same* loop reuses (awaits) that exact task instead of starting another
    fetch cycle — exactly one fetch, both callers see the identical result.
  * Cross-thread/cross-loop mutual exclusion (`_refresh_exclusive`): a plain
    (non-reentrant) `threading.Lock` per symbol, whose acquisition is always
    offloaded to a worker thread via `loop.run_in_executor` — so a coroutine
    waiting for a lock held by another thread never blocks the loop it runs
    on; other requests on that loop keep being served meanwhile.
  * `_schedule_background_refresh` (the opportunistic SWR path) uses a
    non-blocking try-acquire of that same lock and simply skips (serves
    stale) when busy — unchanged in spirit from before, just now backed by a
    lock that cannot be defeated by same-thread reentrancy.

Different symbols use different lock/task objects and always refresh fully
in parallel; `refresh_all`'s per-symbol timeout and `shutdown(wait=False,
cancel_futures=True)` watchdog (2026-06-30 decision) are untouched.

P1 follow-up (Livingston, 2026-08-18): the D4 lock above is correct for
`refresh`/`get_or_load_async` (both are coroutines, so waiting on it never
blocks anything but the awaiting task itself), but `get_or_load` is a *sync*
method sometimes invoked directly — not `await`ed, not offloaded — from
inside an already-running event loop (e.g. an async FastAPI handler calling
it as a plain function). Its previous cold-miss fallback bridged this with
`ThreadPoolExecutor().submit(self._sync_refresh, symbol).result(timeout=120)`
— a *blocking* wait on the calling loop's own OS thread. Contended with the
new D4 lock, this could self-deadlock: if this exact symbol's lock happened
to be held by a task scheduled on that very (now frozen) loop, the loop
could never run that task to completion to release the lock, and the
`get_or_load` caller could never unblock the loop, resolved only by the
120s timeout. `get_or_load` no longer performs any blocking wait when a loop
is already running: on a true cold miss it schedules a background refresh
(reusing one already in flight for the symbol via the same non-blocking
try-acquire `_schedule_background_refresh` already used for SWR — never
touching a `Task` that belongs to a different loop) and raises
`OptionsChainNotReadyError` immediately instead. Genuine synchronous callers
(no loop running on that thread) are unaffected — full blocking refresh, as
before.
"""

import asyncio
import concurrent.futures
import json
import logging
import time
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Real contract expiration (not cache TTL) is evaluated in the exchange's
# local calendar — America/New_York — per Danny's accepted design. A same-day
# expiration is kept all day (contracts settle at 16:00 ET, but agents still
# need them that day to reason about assignment).
_ET_ZONE = ZoneInfo("America/New_York")

# Default TTL: 30 minutes. Controls staleness/refetch decisions only —
# an expired entry is NOT evicted, it just becomes eligible for background
# refresh on next access (stale-while-revalidate).
_DEFAULT_TTL_SECONDS = 1800

# Per-symbol refresh timeout to prevent hung jobs from blocking the queue
_REFRESH_SYMBOL_TIMEOUT = 90

# Sentinel distinguishing "caller didn't pass ttl_seconds" from "caller
# explicitly passed the default value" so the shared-cache factory can read
# options_chain_cache.ttl_seconds from config.yaml exactly once, without
# breaking any existing no-argument call site.
_TTL_UNSET = object()


class OptionsChainNotReadyError(RuntimeError):
    """Raised by ``get_or_load`` (P1, Livingston 2026-08-18) when called
    synchronously from within a running event loop and there is a true
    cold miss (nothing in memory, nothing persisted). The previous
    behavior blocked that loop's own thread for up to 120s waiting on a
    full refresh, which could self-deadlock if this exact symbol's OS
    lock was already held by a task that itself needed that same (now
    frozen) loop to resume in order to release it. A background refresh
    is scheduled (best-effort, subject to the same non-blocking, at-
    most-one-in-flight-per-symbol try-acquire ``get_or_load_async`` already
    uses) so a subsequent call is likely to succeed; this call fails fast
    and explicitly instead. A subclass of ``RuntimeError`` so any existing
    broad ``except RuntimeError`` handling around ``get_or_load`` keeps
    working unmodified."""


class OptionsChainCache:
    """In-memory options chain cache, backed by an optional CosmosDB
    persistence store, with TTL-driven (freshness-only) stale-while-
    revalidate semantics and yfinance+TradingView merge via
    ``src/options_chain_merge.py``."""

    def __init__(self, ttl_seconds: int = _DEFAULT_TTL_SECONDS, *,
                 store: Optional[Any] = None):
        self._ttl = ttl_seconds
        self._store: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        # Per-symbol, non-reentrant OS lock covering the entire hydrate →
        # fetch → merge → assign → persist sequence for that symbol,
        # closing the read-modify-write race between overlapping refreshes
        # (scheduler refresh_all + SWR background + /api/trigger) across
        # *any* thread/loop. Created lazily, guarded by self._lock. See
        # `_refresh_exclusive` for why acquiring it never blocks an event
        # loop, and the module docstring (D4) for the full rationale.
        self._symbol_os_locks: Dict[str, threading.Lock] = {}
        # Same-loop coroutine de-duplication: the in-flight `asyncio.Task`
        # running `_refresh_exclusive` for a symbol, keyed by symbol. Two
        # `await refresh(sym)` calls on the *same* running loop reuse this
        # exact task instead of each starting their own fetch cycle. A task
        # registered from a different loop (e.g. a scheduler-thread's own
        # loop) is never reused -- cross-thread exclusivity is instead
        # enforced by `_symbol_os_locks`. Guarded by self._lock.
        self._inflight_refresh: Dict[str, asyncio.Task] = {}
        # Persistence backend. `None` means "not yet resolved" — resolved
        # lazily (and cached) via `_get_store()` on first use so importing
        # this module never eagerly touches Cosmos/config. Tests may inject
        # an explicit store (including a disabled one) via the `store=`
        # keyword to stay fully hermetic.
        self._store_backend = store

    def _get_store(self):
        """Lazily resolve the persistence backend (production: the shared,
        config-driven singleton; tests: whatever was injected via
        `store=`, including a disabled store)."""
        if self._store_backend is None:
            from src.options_chain_store import get_options_chain_store
            self._store_backend = get_options_chain_store()
        return self._store_backend

    def _get_symbol_os_lock(self, symbol: str) -> threading.Lock:
        with self._lock:
            lock = self._symbol_os_locks.get(symbol)
            if lock is None:
                lock = threading.Lock()
                self._symbol_os_locks[symbol] = lock
            return lock

    def _is_stale(self, entry: Dict[str, Any]) -> bool:
        age = time.monotonic() - entry["cached_at"]
        return age >= self._ttl

    def get(self, symbol: str) -> Optional[str]:
        """Get cached options chain JSON string for a symbol.

        Returns None only on a true cache miss (never fetched before).
        Entries are never evicted purely due to TTL expiry — last-known-good
        data remains readable indefinitely. Use `is_stale()` to check
        whether a background refresh is warranted.
        """
        with self._lock:
            entry = self._store.get(symbol)
            if entry is None:
                return None
            if self._is_stale(entry):
                age = time.monotonic() - entry["cached_at"]
                logger.debug(
                    "%s: options chain cache stale (%.0fs old) — serving last-known-good",
                    symbol, age,
                )
            return entry["chain_json"]

    def is_stale(self, symbol: str) -> bool:
        """True if the cached entry is missing or past its freshness TTL."""
        with self._lock:
            entry = self._store.get(symbol)
            if entry is None:
                return True
            return self._is_stale(entry)

    def _hydrate_into_memory(self, symbol: str) -> Optional[str]:
        """On a true in-memory miss, attempt to hydrate the persisted chain
        before falling back to a live provider fetch (cold start / restart /
        cross-process divergence fix).

        D3 (Livingston, 2026-08-18): a cold hydrate is "prior" data, not a
        fresh live merge result, so it must go through the same same-day
        America/New_York *serving* prune a normal refresh cycle applies
        (`options_chain_merge.prune_by_expiration`) before ever being
        served — otherwise an expired-yesterday contract could survive
        indefinitely in a process that only ever hydrates and never
        refreshes. The top-level `symbol`/`timestamp`/`underlying_price`
        fields the schema documents are restored from the hydrated shard
        data (defended here with fallbacks for older/partial shards).
        The populated in-memory entry is marked immediately stale-eligible
        (backdated `cached_at`) rather than "fresh for a full TTL window":
        a hydrate hit serves last-known-good data on *this* read, but the
        very next `is_stale()` check (`get`/`get_or_load_async`) must see
        it as due for a background refresh, since hydrated data is by
        definition not this process's own live observation.

        Returns None when nothing is persisted (or persistence is
        unavailable) — never raises.
        """
        hydrated = self._get_store().hydrate(symbol)
        if hydrated is None:
            return None

        from src.options_chain_merge import prune_by_expiration

        underlying_price = hydrated.get("underlying_price")
        timestamp = hydrated.get("timestamp")
        today_et = datetime.now(timezone.utc).astimezone(_ET_ZONE).date()
        # prune_by_expiration's result schema is {symbol, timestamp, calls,
        # puts} only -- it does not carry `underlying_price` forward, so
        # restore all three top-level fields explicitly afterward.
        pruned = prune_by_expiration(hydrated, today_et=today_et)
        pruned["symbol"] = symbol
        pruned["timestamp"] = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if underlying_price is not None:
            pruned["underlying_price"] = underlying_price

        chain_json = json.dumps(pruned, default=str)
        with self._lock:
            self._store[symbol] = {
                "chain_json": chain_json,
                # Immediately stale-eligible (D3) -- see docstring above.
                "cached_at": time.monotonic() - self._ttl - 1,
            }
        logger.info(
            "%s: options chain cache miss — hydrated from persistence store "
            "(no provider call this read; immediately stale-eligible)", symbol,
        )
        return chain_json

    def get_or_load(self, symbol: str) -> str:
        """Get from cache or load synchronously on miss.

        On a true cache miss, first tries to hydrate from the persistence
        store (no provider call on a hydrate hit); returns last-known-good
        (cached or hydrated) data immediately whenever it exists — a
        stale-but-present entry is served as-is, refetching is driven by the
        scheduled refresh job, not this call.

        Only a true cold miss — nothing in memory and nothing persisted —
        needs to actually fetch. What happens then depends on the calling
        thread (P1, Livingston 2026-08-18):

          * No event loop running on this thread (genuine synchronous
            caller — a script, the scheduler's own worker thread, etc.):
            unchanged from before — safe to block this thread and run a
            full refresh to completion via a private event loop.
          * An event loop IS already running on this thread (e.g. an async
            FastAPI handler that calls this sync method directly instead of
            awaiting `get_or_load_async`): this method must never block that
            loop, not even briefly and not even bounded — any synchronous
            wait here (however implemented: a thread-pool `.result()`
            bridge, `time.sleep`, etc.) stalls every other coroutine on that
            loop, including — worst case — the very refresh this symbol's
            OS lock is waiting on if it happens to be scheduled on that same
            loop, which previously self-deadlocked until the 120s timeout.
            Instead: kick off a background refresh (reusing an
            already-in-flight one for this symbol when present, via the
            same non-blocking try-acquire `get_or_load_async` already uses
            — never touching a Task that belongs to a different loop) and
            raise `OptionsChainNotReadyError` immediately so the caller
            fails fast and explicitly rather than blocking or deadlocking. A
            following call (this one, or from another request) is likely to
            find the cache populated once that background refresh lands.
        """
        cached = self.get(symbol)
        if cached is not None:
            return cached

        hydrated = self._hydrate_into_memory(symbol)
        if hydrated is not None:
            return hydrated

        logger.info("%s: options chain cache miss — loading from sources", symbol)

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — safe to block this thread and refresh fully.
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self.refresh(symbol))
            finally:
                loop.close()

        # A loop IS running on this thread — never block it (see docstring).
        logger.warning(
            "%s: options chain cold miss reached synchronously from a "
            "running event loop — not blocking it for a fetch; scheduling "
            "a background refresh and failing this read explicitly instead",
            symbol,
        )
        self._schedule_background_refresh(symbol)
        raise OptionsChainNotReadyError(
            f"{symbol}: options chain not yet available (cold miss) — a "
            "background refresh has been scheduled; retry shortly"
        )

    def _sync_refresh(self, symbol: str) -> str:
        """Helper: run refresh() in a new event loop (for thread execution)."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.refresh(symbol))
        finally:
            loop.close()

    async def get_or_load_async(self, symbol: str) -> str:
        """Async version of get_or_load.

        Stale-while-revalidate: a true in-memory miss first tries to
        hydrate from the persistence store (no provider call on a hydrate
        hit); only a true cold miss blocks and fetches. A stale-but-present
        entry is returned immediately, with a background refresh kicked off
        (at most one in flight per symbol, via a non-blocking try-acquire of
        that symbol's lock) so future calls see fresher data without making
        this call pay the latency.
        """
        cached = self.get(symbol)
        if cached is not None:
            if self.is_stale(symbol):
                self._schedule_background_refresh(symbol)
            return cached

        hydrated = self._hydrate_into_memory(symbol)
        if hydrated is not None:
            return hydrated

        logger.info("%s: options chain cache miss — loading from sources", symbol)
        return await self.refresh(symbol)

    def _schedule_background_refresh(self, symbol: str) -> None:
        """Fire-and-forget refresh for stale entries.

        Uses a non-blocking try-acquire of the symbol's OS lock: if another
        refresh (foreground or background, on any thread/loop) already
        holds it, this call is a no-op — the caller keeps serving the stale
        in-memory chain instead of queueing duplicate work. Different
        symbols still refresh in parallel; this never affects
        `refresh_all`'s per-symbol timeout.
        """
        os_lock = self._get_symbol_os_lock(symbol)
        if not os_lock.acquire(blocking=False):
            return

        async def _run():
            try:
                await self._refresh_locked(symbol)
            except Exception as exc:
                logger.warning("%s: background stale-while-revalidate refresh failed: %s", symbol, exc)
            finally:
                os_lock.release()

        try:
            asyncio.create_task(_run())
        except RuntimeError:
            # No running loop to schedule on (shouldn't happen when called
            # from get_or_load_async, but fail safe rather than crash).
            os_lock.release()

    async def refresh(self, symbol: str) -> str:
        """Force-refresh the cache for a symbol.

        Same-loop de-duplication (D4, R6): if another `refresh()` call for
        this exact symbol is already in flight on the *current* event loop,
        this call reuses (awaits) that exact task instead of starting a
        second fetch cycle — two concurrent `await cache.refresh(sym)`
        calls on one loop always produce exactly one fetch, and both
        callers see the identical result. `asyncio.shield` protects the
        shared task from a *caller's* cancellation so one caller giving up
        never breaks the other awaiter. A refresh already in flight on a
        different loop/thread (e.g. one of `refresh_all`'s scheduler
        workers) is tracked as a separate task; cross-thread mutual
        exclusion for the *actual* fetch/persist work is instead enforced
        inside `_refresh_exclusive` without ever blocking this loop.
        Different symbols always refresh fully in parallel.

        Returns the merged options chain as a JSON string.
        """
        loop = asyncio.get_running_loop()
        with self._lock:
            task = self._inflight_refresh.get(symbol)
            if task is None or task.get_loop() is not loop or task.done():
                task = loop.create_task(self._refresh_exclusive(symbol))
                self._inflight_refresh[symbol] = task
        return await asyncio.shield(task)

    async def _refresh_exclusive(self, symbol: str) -> str:
        """Acquire this symbol's cross-thread/cross-loop OS lock, then run
        the refresh cycle.

        The blocking `Lock.acquire()` call is offloaded to a thread pool
        worker via `run_in_executor` — so if a scheduler-thread refresh
        (its own OS thread, its own event loop, via `refresh_all` →
        `_sync_refresh`) currently holds this symbol's lock, this coroutine
        merely awaits an executor future while its own event loop keeps
        serving every other request/coroutine in the meantime (D4: "a
        scheduler-thread refresh does not block the FastAPI loop").
        """
        os_lock = self._get_symbol_os_lock(symbol)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, os_lock.acquire)
        try:
            return await self._refresh_locked(symbol)
        finally:
            os_lock.release()

    async def _refresh_locked(self, symbol: str) -> str:
        """Runs the full hydrate → fetch → merge → assign → persist
        sequence for one symbol. Caller (`_refresh_exclusive` /
        `_schedule_background_refresh`) must already hold that symbol's
        OS lock — this method assumes exclusivity and performs no locking
        of its own beyond the brief `self._lock` used for the in-memory
        dict assignment.

        Delegates all merge-semantics decisions to
        `src.options_chain_merge` (Linus's frozen, pure functions) — this
        method owns only I/O, ordering, and failure isolation.
        """
        from src.options_chain_merge import (
            merge_sources, merge_prior, recompute_derived, prune_by_expiration,
        )

        # Step 0: hydrate the prior accumulated chain — in-memory if
        # present, else the persistence store — so a process that has never
        # refreshed this symbol before (fresh restart, or a scheduler
        # process distinct from whatever last served this symbol over the
        # web) still accumulates against real history instead of starting
        # from nothing.
        prior_chain = self._load_previous_chain(symbol)

        # Steps 1-2: fetch both sources fresh.
        yf_chain = await self._fetch_yfinance(symbol)
        tv_chain = await self._fetch_tradingview(symbol)

        # Step 3: live source merge (Linus) — TV preferred field-by-field
        # over yfinance, gated; contracts from either source are kept.
        live = merge_sources(yf_chain, tv_chain)

        # Step 4: accumulate against prior, monotonically (Linus) — never
        # drops a contract, only advances accepted fields forward.
        now = datetime.now(timezone.utc)
        accumulated = merge_prior(prior_chain or {}, live, now=now)

        # Step 5: recompute derived fields (mid/greeks) from the merged
        # primitives, current underlying price and current time-to-expiry
        # (Linus) — never carried/merged themselves, always fresh.
        underlying_price = self._extract_underlying_price(yf_chain, prior_chain)
        accumulated = recompute_derived(accumulated, underlying_price, now=now)

        # Step 6: serving prune — drop expiration buckets whose actual
        # contract expiration date (America/New_York) has passed. Distinct
        # from cache TTL/staleness; the whole expiration day is retained.
        today_et = now.astimezone(_ET_ZONE).date()
        merged = prune_by_expiration(accumulated, today_et=today_et)
        merged["symbol"] = symbol
        merged["timestamp"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        merged["underlying_price"] = underlying_price

        chain_json = json.dumps(merged, default=str)

        # Step 7: assign in-memory atomically. This happens before the
        # persistence attempt below so that a persistence failure can never
        # prevent (or roll back) a good in-memory result.
        with self._lock:
            self._store[symbol] = {
                "chain_json": chain_json,
                "cached_at": time.monotonic(),
            }

        call_count = sum(len(strikes) for strikes in merged.get("calls", {}).values())
        put_count = sum(len(strikes) for strikes in merged.get("puts", {}).values())
        logger.info(
            "%s: options chain cached — %d call exps, %d put exps, "
            "%d total call contracts, %d total put contracts",
            symbol,
            len(merged.get("calls", {})),
            len(merged.get("puts", {})),
            call_count,
            put_count,
        )

        # Step 8: best-effort persistence — never allowed to affect the
        # return value; failures are logged and counted only.
        self._persist_best_effort(symbol, merged, now=now)

        return chain_json

    @staticmethod
    def _extract_underlying_price(yf_chain: dict, prior_chain: Optional[dict]) -> float:
        """Prefer this cycle's live yfinance price. Fall back to the last
        persisted/cached price when yfinance failed to produce one this
        cycle, so a transient provider outage doesn't zero out every
        carried-forward contract's freshly recomputed greeks."""
        price = (yf_chain or {}).get("underlying_price")
        if price is None and prior_chain:
            price = prior_chain.get("underlying_price")
        try:
            return float(price) if price is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    def _persist_best_effort(self, symbol: str, chain: dict, *, now: datetime) -> None:
        """Persist the merged chain, best-effort. Never allowed to raise —
        `OptionsChainStore.persist`/`prune_expired` already never raise
        internally (every failure is caught, logged, and counted there);
        this wrapper is defense-in-depth so a bug in the store can never
        turn a successful refresh into a failed one.
        """
        store = self._get_store()
        try:
            store.persist(symbol, chain, now=now)
        except Exception as exc:
            logger.warning("%s: options chain persistence failed (non-fatal): %s", symbol, exc)
        try:
            today_et = now.astimezone(_ET_ZONE).date()
            store.prune_expired(symbol, today_et=today_et)
        except Exception as exc:
            logger.warning("%s: options chain persistence prune failed (non-fatal): %s", symbol, exc)

    async def refresh_all(self, symbols: list[str]) -> dict:
        """Refresh cache for all symbols with per-symbol timeout. Returns summary stats."""
        success_count = 0
        error_count = 0

        # Use ThreadPoolExecutor with bounded concurrency to handle symbols.
        # Each thread runs _sync_refresh which creates its own event loop.
        # NOTE: we deliberately do NOT use `with ... as executor` here, because
        # the context manager exit calls shutdown(wait=True), which would block
        # forever on a truly hung yfinance/requests call — reintroducing the very
        # deadlock this method guards against. Instead we enforce a per-symbol
        # timeout and then shutdown(wait=False, cancel_futures=True) so hung
        # threads are abandoned (they linger harmlessly) without blocking.
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        try:
            futures_map = {
                symbol: executor.submit(self._sync_refresh, symbol)
                for symbol in symbols
            }

            # Process each symbol with timeout enforcement
            for symbol, future in futures_map.items():
                try:
                    future.result(timeout=_REFRESH_SYMBOL_TIMEOUT)
                    success_count += 1
                except concurrent.futures.TimeoutError:
                    logger.warning(
                        "%s: options chain refresh timed out after %d seconds "
                        "(abandoning, will retry next run)",
                        symbol, _REFRESH_SYMBOL_TIMEOUT
                    )
                    error_count += 1
                except Exception as e:
                    logger.error("%s: options chain refresh failed: %s", symbol, e)
                    error_count += 1
        finally:
            # Never block on hung worker threads: don't wait, cancel pending.
            executor.shutdown(wait=False, cancel_futures=True)

        return {"success": success_count, "errors": error_count}

    def invalidate(self, symbol: str):
        """Remove a symbol from the in-memory cache only.

        Persisted data is never touched — the next read re-hydrates from
        the persistence store (see `_hydrate_into_memory`/
        `_load_previous_chain`), so this cannot cause data loss the way a
        destructive purge would. Use `purge()` for that.
        """
        with self._lock:
            self._store.pop(symbol, None)

    def invalidate_all(self):
        """Clear the entire in-memory cache. Persisted data is untouched —
        see `invalidate()`."""
        with self._lock:
            self._store.clear()

    def purge(self, symbol: str) -> int:
        """Explicit destructive admin operation: delete ALL persisted data
        for `symbol` immediately, ignoring the expiration grace period, and
        drop it from memory. Not wired to any scheduled path — callers must
        invoke this deliberately. Returns the number of shards deleted (0
        when persistence is unavailable)."""
        self.invalidate(symbol)
        return self._get_store().purge(symbol)

    def stats(self) -> dict:
        """Return cache statistics.

        Note: entries are never evicted for being past the TTL — `expired`
        here means "stale" (eligible for a background refetch), not "gone".
        """
        with self._lock:
            now = time.monotonic()
            entries = {}
            for sym, entry in self._store.items():
                age = now - entry["cached_at"]
                entries[sym] = {
                    "age_seconds": round(age, 1),
                    "expired": age >= self._ttl,
                }
            return {
                "ttl_seconds": self._ttl,
                "entries_count": len(self._store),
                "entries": entries,
                "persistence": self._get_store().stats(),
            }

    # ------------------------------------------------------------------
    # Internal: hydrate prior chain (memory, then persistence store)
    # ------------------------------------------------------------------

    def _load_previous_chain(self, symbol: str) -> Optional[dict]:
        """Return the prior accumulated chain (parsed dict) to merge
        against this cycle's fresh fetch: in-memory if this process has
        already loaded it, otherwise a hydrate read from the persistence
        store. Returns `None` only when there is truly nothing anywhere —
        this symbol has never been refreshed/persisted before.
        """
        with self._lock:
            entry = self._store.get(symbol)
        if entry is not None:
            try:
                return json.loads(entry["chain_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                logger.warning("%s: failed to parse previously cached chain — ignoring", symbol)
        return self._get_store().hydrate(symbol)

    # ------------------------------------------------------------------
    # Internal: yfinance fetch
    # ------------------------------------------------------------------

    async def _fetch_yfinance(self, symbol: str) -> dict:
        """Fetch options chain from yfinance. Returns parsed dict."""
        try:
            import yfinance as yf
        except ImportError:
            logger.error("yfinance not installed")
            return self._empty_chain(symbol)

        result = {
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "calls": {},
            "puts": {},
            "underlying_price": None,
        }

        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info or {}
            current_price = info.get("regularMarketPrice") or info.get("currentPrice")

            if current_price is None:
                try:
                    history = ticker.history(period="5d")
                    if not history.empty:
                        current_price = float(history["Close"].iloc[-1])
                except Exception:
                    pass

            if current_price is None:
                logger.warning("%s: no current price, yfinance chain will be empty", symbol)
                return result

            result["underlying_price"] = current_price

            try:
                expirations = ticker.options
            except Exception as exc:
                logger.error("%s: failed to fetch options expirations: %s", symbol, exc)
                return result

            if not expirations:
                logger.info("%s: no options expirations available from yfinance", symbol)
                return result

            now = datetime.now(timezone.utc)

            for exp_date_str in expirations:
                try:
                    exp_date = datetime.strptime(exp_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                except ValueError:
                    continue

                dte = (exp_date - now).days
                if dte < 0:
                    continue

                exp_key = exp_date.strftime("%Y%m%d")

                try:
                    chain = ticker.option_chain(exp_date_str)
                except Exception as exc:
                    logger.warning("%s: failed to fetch chain for %s: %s",
                                   symbol, exp_date_str, exc)
                    continue

                calls_dict = self._process_option_df(chain.calls, "call", exp_key)
                puts_dict = self._process_option_df(chain.puts, "put", exp_key)

                if calls_dict:
                    result["calls"][exp_key] = calls_dict
                if puts_dict:
                    result["puts"][exp_key] = puts_dict

            logger.info(
                "%s: yfinance chain fetched — %d call exps, %d put exps",
                symbol, len(result["calls"]), len(result["puts"]),
            )

        except Exception as exc:
            logger.exception("%s: yfinance options chain fetch failed: %s", symbol, exc)

        return result

    # ------------------------------------------------------------------
    # Internal: TradingView fetch
    # ------------------------------------------------------------------

    async def _fetch_tradingview(self, symbol: str) -> dict:
        """Fetch options chain from TradingView. Returns parsed dict."""
        try:
            from src.tv_options_chain_fetcher import fetch_tv_options_chain
            tv_result = await fetch_tv_options_chain(symbol)

            has_data = bool(tv_result.get("calls") or tv_result.get("puts"))
            if has_data:
                logger.info(
                    "%s: TradingView chain fetched — %d call exps, %d put exps",
                    symbol,
                    len(tv_result.get("calls", {})),
                    len(tv_result.get("puts", {})),
                )
                return tv_result
            else:
                logger.info("%s: TradingView returned empty chain", symbol)
                return self._empty_chain(symbol)

        except Exception as exc:
            logger.error("%s: TradingView fetch failed: %s", symbol, exc)
            return self._empty_chain(symbol)

    # ------------------------------------------------------------------
    # Internal: Process yfinance DataFrame
    # ------------------------------------------------------------------

    @staticmethod
    def _process_option_df(df, option_type: str, exp_key: str) -> dict:
        """Process a calls or puts DataFrame into strike-keyed dict.

        Emits only observed fields (Rule S1, "absence is not zero"): a
        missing/NaN yfinance value is omitted from the dict entirely,
        never fabricated as a placeholder (0 / 0.0 / False / "" / None-
        as-value). Zero and other individually-valid observed values
        (e.g. bid=0.0, volume=0) are still written through as real data —
        only fabricated defaults for genuinely *missing* data go away.

        Does not compute mid/greeks or write `_meta` — per Linus's frozen
        `src.options_chain_merge` interface, `recompute_derived` is the
        sole writer of mid/delta/gamma/theta/vega/rho, and `merge_prior`/
        `recompute_derived` are the sole writers of `_meta`. This function
        only surfaces this cycle's raw yfinance observations.
        """
        import math
        import pandas as pd

        contracts: Dict[str, Any] = {}
        if df is None or df.empty:
            return contracts

        def _is_nan(value):
            if value is None:
                return True
            try:
                return math.isnan(float(value))
            except (TypeError, ValueError):
                return False

        for _, row in df.iterrows():
            strike = row.get("strike")
            if strike is None or pd.isna(strike):
                continue

            strike_key = f"{strike:.1f}" if strike == int(strike) else str(strike)
            contract: Dict[str, Any] = {
                "strike": float(strike),
                "expiration": exp_key,
                "option_type": option_type,
            }

            contract_symbol = row.get("contractSymbol")
            if contract_symbol is not None and pd.notna(contract_symbol) and contract_symbol != "":
                contract["contractSymbol"] = contract_symbol

            bid = row.get("bid")
            if not _is_nan(bid):
                contract["bid"] = float(bid)

            ask = row.get("ask")
            if not _is_nan(ask):
                contract["ask"] = float(ask)

            iv = row.get("impliedVolatility")
            if not _is_nan(iv):
                contract["iv"] = round(float(iv), 6)

            last_price = row.get("lastPrice")
            if not _is_nan(last_price):
                contract["lastPrice"] = float(last_price)

            ltd = row.get("lastTradeDate")
            if ltd is not None and pd.notna(ltd):
                if hasattr(ltd, "strftime"):
                    contract["lastTradeDate"] = ltd.strftime("%Y-%m-%dT%H:%M:%SZ")
                else:
                    contract["lastTradeDate"] = str(ltd)

            volume = row.get("volume")
            if not _is_nan(volume):
                contract["volume"] = int(volume)

            open_interest = row.get("openInterest")
            if not _is_nan(open_interest):
                contract["openInterest"] = int(open_interest)

            in_the_money = row.get("inTheMoney")
            if in_the_money is not None and pd.notna(in_the_money):
                contract["inTheMoney"] = bool(in_the_money)

            contracts[strike_key] = contract

        return contracts

    # ------------------------------------------------------------------
    # Internal: helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _empty_chain(symbol: str) -> dict:
        return {
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "calls": {},
            "puts": {},
        }


# ======================================================================
# Module-level singleton
# ======================================================================

_shared_cache: Optional[OptionsChainCache] = None
_shared_cache_lock = threading.Lock()


def _resolve_ttl_from_config() -> int:
    """Read `options_chain_cache.ttl_seconds` from config.yaml. Falls back
    to `_DEFAULT_TTL_SECONDS` on any error (missing file, missing section,
    invalid config, etc.) — config-driven TTL is a convenience, never a
    hard requirement for the cache to function."""
    try:
        from src.config import Config
        cfg = Config()
        value = (cfg.config.get("options_chain_cache") or {}).get("ttl_seconds")
        return int(value) if value is not None else _DEFAULT_TTL_SECONDS
    except Exception as exc:
        logger.info("options_chain_cache.ttl_seconds not configured (%s) — using default %ds",
                    exc, _DEFAULT_TTL_SECONDS)
        return _DEFAULT_TTL_SECONDS


def get_options_chain_cache(ttl_seconds: int = _TTL_UNSET) -> OptionsChainCache:
    """Return the process-wide shared options chain cache instance.

    Every existing call site invokes this with no arguments; `ttl_seconds`
    then resolves from `config.yaml`'s `options_chain_cache.ttl_seconds`
    (falling back to `_DEFAULT_TTL_SECONDS`) exactly once, on first
    construction. Passing an explicit `ttl_seconds` (as tests do) bypasses
    config entirely, preserving today's behaviour for hermetic tests.
    """
    global _shared_cache
    if _shared_cache is None:
        with _shared_cache_lock:
            if _shared_cache is None:
                resolved_ttl = ttl_seconds if ttl_seconds is not _TTL_UNSET else _resolve_ttl_from_config()
                _shared_cache = OptionsChainCache(resolved_ttl)
    return _shared_cache


def set_options_chain_cache(cache: Optional[OptionsChainCache]) -> None:
    """Test-only hook: override or reset the process-wide shared cache
    instance. Passing `None` clears it so the next `get_options_chain_cache()`
    call rebuilds it from config."""
    global _shared_cache
    with _shared_cache_lock:
        _shared_cache = cache
