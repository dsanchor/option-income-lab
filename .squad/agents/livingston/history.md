# Livingston — Project History

## Core Context

- **Project:** options-agent
- **User:** dsanchor
- **Stack:** Python, Microsoft Agent Framework, Azure Foundry, Cosmos DB
- **Focus:** Persistent option-chain round-trip fidelity, cache/store integration, and async/thread concurrency.
- **Joined:** 2026-08-18 after the persistent option-chain implementation failed the architecture gate at the store/merge seam.

## Learnings

### 2026-08-18 — D1-D5 revision implemented (post-REJECT bounded fix)

**Root cause (D1/D2):** `OptionsChainStore._write_shard` was calling
`options_chain_merge.merge_prior(prior_shard_chain, live_shard_chain)` to
reconcile CAS conflicts, but `merge_prior`'s real, frozen semantics are
"apply this cycle's *live source observations* onto a *prior accumulated
state*" — it manufactures fresh `_meta` via a `quote_updated` gate and its
per-contract merge only copies enumerated quote/observed fields, **never**
derived fields (mid/delta/gamma/theta/vega/rho). The store was calling this
on an **already-fully-merged** in-memory chain, treating it as if it were a
fresh live observation. That composition — not `merge_prior` itself — was
the defect. Fix: the store never imports/calls `options_chain_merge` at
all now. `_write_shard` reconciles CAS conflicts with a new, store-owned,
purely verbatim contract-level union (`_reconcile_bucket`): a contract on
only one side is kept as-is; a contract on both sides is kept *wholesale*
from whichever side has the more-recent `_meta.last_seen`/`quote_asof`
(ties favor `want`, i.e. this cycle's own result) — never blended
field-by-field, never re-derived, never touching `_meta`. Monotone (never
drops a contract) — safe under CAS retry.

**D5 (write-skip guard):** the old content hash included the *entire*
`_meta` blob, but `_merge_prior_contract` legitimately force-advances
`last_seen` (and, when the quote group is merely re-supplied unchanged,
`quote_asof`) on **every** cycle a contract is still listed — so the old
hash could never converge across cycles in production; the "unchanged →
skip write" optimization was dead code that only ever passed under a fake.
Fixed by hashing a `_hashable_contract` view that strips `last_seen`/
`quote_asof` before hashing (plus now includes `underlying_price`) — a
genuinely unchanged cycle now hashes identically; a real field change
(bid/iv/mid/greeks/underlying_price) still triggers a rewrite.
`_time_to_expiry_years` in the frozen merge module truncates to whole
*days* (`(exp_dt - now).days`), so recomputed greeks are bit-identical
across same-day cycles with identical inputs — this makes the write-skip
test deterministic without mocking `datetime.now`.

**Schema:** bumped `schema_version` 2→3, added `underlying_price` to the
shard body — both pre-approved by Danny's directive as the one shard-shape
change allowed without escalation. `hydrate()` reconstructs top-level
`timestamp`/`underlying_price` from the most-recently-`updated_at` shard;
legacy v2 shards without it hydrate fine, just without that field.

**D3 (cache.py hydrate):** `_hydrate_into_memory` now applies
`options_chain_merge.prune_by_expiration` (today's America/New_York date)
before ever serving hydrated data — `prune_by_expiration`'s result schema
is `{symbol, timestamp, calls, puts}` only, it does **not** carry
`underlying_price` forward, so that field must be captured before pruning
and re-applied after. The hydrated in-memory entry is stamped
`cached_at = time.monotonic() - self._ttl - 1` (immediately stale-eligible)
instead of "fresh" — the very next `is_stale()` check schedules a real
background refresh via the existing SWR path, no code duplication needed.

**D4 (locking):** replaced the single `threading.RLock` per symbol (which
was both reentrant — letting two same-loop `await refresh(sym)` calls both
run a full cycle — and blocking directly on the event-loop thread, freezing
*every* request on that loop, not just same-symbol ones, whenever a
scheduler-thread refresh held it) with two independent, purpose-built
mechanisms: (1) `_inflight_refresh: Dict[str, asyncio.Task]` — same-loop
task memoization inside `refresh()`, reused (via `asyncio.shield`) by a
second concurrent same-loop caller instead of starting a second fetch; (2)
`_symbol_os_locks: Dict[str, threading.Lock]` (plain, non-reentrant) whose
blocking `acquire()` is always offloaded via `loop.run_in_executor` inside
the new `_refresh_exclusive()` — so waiting on a cross-thread/cross-loop
hold never blocks the calling loop. `_schedule_background_refresh` (SWR)
keeps its non-blocking try-acquire on the *same* OS lock object.
`refresh_all` was not touched — it still funnels through `refresh()`, so
the new locking applies transitively.

**Validation:** ran the OLD (pre-edit) `test_options_chain_store.py`
against the NEW store.py first — 32/33 passed unchanged (only the hardcoded
`schema_version == 2` literal needed updating), strong evidence the
rewrite is behaviorally compatible with every previously-tested CAS/
conflict/pruning/size-valve scenario before any new tests were added.

**Files touched:** `backend/src/options_chain_store.py` (write path,
hydrate, schema — full rewrite of the reconcile/hash logic),
`backend/src/options_chain_cache.py` (hydrate path + locking only, merge
step logic in `_refresh_locked` untouched), `backend/tests/
test_options_chain_store.py` (removed the now-unnecessary
`fake_merge_module` fixture; added D1/D2/D5/underlying_price tests),
`backend/tests/test_options_chain_cache.py` (untouched — all 34 existing
tests still pass against the new locking design with no edits needed),
`backend/tests/test_options_chain_persistence_integration.py` (new — R1-R7,
real store + real merge, only the Cosmos container and the network-facing
fetch methods faked).

**Test outcome:** 546/546 in the focused options-chain suite (merge/store/
cache/integration/filters/tv-normalize), 1244/1244 across the rest of the
backend suite (excluding a pre-existing, order-dependent flakiness in
`test_yfinance_data_provider.py` — reproducible even with my changes fully
reverted, in a file I'm not authorized to touch; unrelated to this
revision, flagged as a residual risk for whoever owns that file next).

**Residual risk:** none identified within my authorized scope. The
pre-existing `test_yfinance_data_provider.py` flakiness (an
`asyncio.get_event_loop()` policy/ordering issue, not a regression I
introduced) should be escalated to whoever owns that file if it starts
blocking CI.


---

## 2026-08-18 (P1 follow-up) — get_or_load sync-in-async bridge deadlock

Danny approved D1-D5, then opened a separate P1 before production: the D4
per-symbol OS lock made `get_or_load`'s *pre-existing* sync-in-async bridge
able to self-deadlock under contention — reachable synchronously (not
`await`ed, not offloaded) from `web/app.py:3249` inside the async
`api_activity_chat` endpoint, especially on a true cold miss (hydrate
returns None).

**Root cause:** `get_or_load`'s old cold-miss fallback, when called from a
thread with a running event loop, did
`ThreadPoolExecutor().submit(self._sync_refresh, symbol).result(timeout=120)`
— a *blocking* wait executed **on the calling loop's own OS thread**,
freezing every other coroutine on that loop for up to 120s. `_sync_refresh`
spins up its own new loop and eventually awaits
`loop.run_in_executor(None, os_lock.acquire)` for this symbol's D4 lock. If
that lock happened to already be held by a task that itself needed the
ORIGINAL (now-frozen) loop to run in order to finish and release the lock
(e.g. a concurrent request's own in-flight `await cache.refresh(sym)` on
that same loop), the system self-deadlocked — resolved only by the 120s
timeout. Pre-D4, an uncontended `RLock.acquire()` was near-instant so this
was latent; D4's genuinely-contended, offloaded lock made it reachable.

**Fix (entirely within `options_chain_cache.py`):** `get_or_load` still
returns cached/hydrated last-known-good data immediately when available —
unchanged. On a true cold miss, it branches on whether a loop is running on
the calling thread:
  - No running loop (genuine sync caller — script, scheduler thread, etc.):
    unchanged — blocks that thread, runs a full refresh via a private
    `asyncio.new_event_loop()`, returns real data. "Sync callers preserve
    behavior" verified with a dedicated regression test.
  - A loop IS running: **zero blocking**, not even a short bounded wait —
    any synchronous wait on this thread is unsafe regardless of duration,
    since the lock-holder might be scheduled on this exact loop and can
    never resume while it's frozen. Instead: reuse the existing
    non-blocking try-acquire `_schedule_background_refresh` (already used
    by the SWR path) to kick off (or no-op skip if one is already in
    flight for the symbol — no cross-loop Task touching, dedup is purely
    via the loop-agnostic OS lock) a background refresh on the *current*
    running loop via `asyncio.create_task`, then immediately raises a new
    `OptionsChainNotReadyError(RuntimeError)` — explicit, fast failure
    instead of blocking/deadlocking. Confirmed compatible with
    `web/app.py`'s existing (untouched) `except Exception` around this
    exact call site (already degrades gracefully to an "unavailable"
    placeholder per `tests/test_activity_chat.py::test_chain_unavailable_degradation`).

**Files touched:** `backend/src/options_chain_cache.py` (new
`OptionsChainNotReadyError` class, rewritten `get_or_load` cold-miss
branch, module docstring updated with a new P1 section) — `web/app.py`,
`_refresh_locked`, `refresh_all`, `_sync_refresh`, `get_or_load_async`,
and all merge/store semantics untouched. `backend/tests/
test_options_chain_cache.py` — additive only: 5 new tests (all 34
pre-existing tests pass unmodified):
  - `TestGetOrLoadRunningLoopNeverBlocks` (3 tests): a deterministic
    same-loop lock-holder scenario (the literal self-deadlock shape) proves
    `get_or_load` fails fast (<1s, not 120s) while a heartbeat coroutine on
    the same loop keeps ticking throughout (loop never frozen); a scheduled
    background refresh actually populates the cache for a subsequent read;
    an already-in-flight background refresh for the symbol is not
    duplicated (fetch called exactly once).
  - `TestGetOrLoadSyncCallerBehaviorPreserved` (2 tests): a genuine
    no-running-loop caller still blocks and returns real fetched data
    unchanged, including while an unrelated symbol's lock is held elsewhere
    (different symbols independent, as before).

**Test outcome:** focused suite (merge/store/cache/integration/filters/
tv-normalize) 551/551 (546 prior + 5 new), `test_activity_chat.py` 13/13,
cache suite alone re-run 3x for determinism (39/39 each time, no
flakiness). Full backend suite: 1250 passed, 20 failed — all 20 in
`test_yfinance_data_provider.py`, confirmed the same pre-existing,
order/network-dependent flakiness already documented in the D1-D5 entry
above (reproducible in isolation, in a file untouched by this change;
count varies run-to-run, e.g. 3 failures in a clean isolated run vs 20 in
full-suite ordering — a real-network-call test file, not a regression from
this fix).

**Residual risk:** the new `OptionsChainNotReadyError` surfaces as a
generic "(option chain unavailable: ...)" chat message on a cold miss
reached synchronously from a running loop (vs the old behavior of
eventually blocking through to real data when uncontended) — a deliberate,
directive-mandated trade-off (fail-fast over block/deadlock). The
background refresh this schedules populates the cache for the *next* read
of that symbol, so the practical impact is limited to the very first
synchronous hit on a never-before-fetched symbol from an async caller.
`get_or_load_async` (already correct, `await`-based, untouched) remains the
recommended path for new async call sites. Pre-existing
`test_yfinance_data_provider.py` flakiness unrelated to this fix, flagged
again for its owner.
