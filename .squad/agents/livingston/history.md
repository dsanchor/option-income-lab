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

## 2026-08-19 — G3: persistence/serving portions of Danny's "Zero-Free
## Agent-Facing Option Chains" decision implemented

**Scope:** implemented §4 (persistence: retry/backoff, startup probe,
stale wiring, lazy migration + repair script, observability) plus applied
Linus's frozen `options_chain_view.to_agent_view()` at every serving/
agent-prompt seam within my exclusive write scope
(`options_chain_store.py`, `options_chain_cache.py`, `web/app.py`,
`agent_runner.py` serialization seam only, `yfinance_data_provider.py`
schema-description text only, `config.yaml`, new
`scripts/repair_options_chain_shards.py`). Did **not** touch
`options_chain_view.py`, `options_chain_merge.py`, `options_chain_filters.py`,
`roll_table.py`, `dps_scorer.py`, `options_math.py`, or `refresh_all` —
those remain Linus/frozen per the decision's ownership table (§5).

**§4.1 P0 fix — permanent negative memoization:** `get_options_chain_store()`
previously memoized a transient Cosmos construction failure forever (one
WARNING at process start, persistence dead for the process's whole life).
Rewrote as: only a successful/enabled store is memoized; a failure records
`(_last_failure_at, _last_error, _failure_count)` module globals and
returns an unmemoized disabled placeholder for that call only; every
subsequent call retries once `now - _last_failure_at >= backoff` (config
`persistence_retry_seconds`, default 300, capped-exponential to 1 hour);
`persistence_enabled: false` is still terminal/permanent/INFO-once. Time is
injected (`now=` param) in tests, never slept.

**§4.2 startup probe + observability:** eager `get_options_chain_store()`
call added to `web/app.py`'s `startup()` (ERROR log + retry interval on
failure, INFO with database/container on success); the scheduler-bootstrap
side (`src/main.py`/`run.py`) is out of my writable scope, documented as
relying on the store's own first-call construction/logging as a
substitute — flagged below as a residual gap for whoever owns that file.
`stats()` extended with `configured/enabled/last_error/last_error_at/
last_success_at/failure_count/retry_in_seconds/writes_ok/writes_failed`
plus per-symbol `quality` counters (`contracts_total/
contracts_no_usable_bid/contracts_greeks_invalid/contracts_stale`),
computed once per refresh cycle in `_refresh_locked`. New
`GET /api/health/options-chain` (always HTTP 200; `status: ok|degraded`)
surfaces both blocks.

**§4.3 stale wiring:** `stale_quote_warn_seconds` was a dead config key.
Added `get_stale_quote_warn_seconds()` (mirrors the existing
`_resolve_ttl_from_config()` pattern) and threaded it through as the
`stale_after_seconds` input to `to_agent_view`/quality-metric computation,
so `_meta.stale` and the `contracts_stale` counter are both finally driven
by the configured value instead of silently defaulting everywhere.

**§4.4 lazy migration + repair script:** `normalize_persisted_v1_to_v2()`
(pure, total, idempotent) nulls *only* the two defects the pre-fix
`recompute_derived` could fabricate — all five Greeks when not genuinely
valid, and `mid` when neither bid nor ask is usable — never touching any
observed field (bid/ask/lastPrice/iv/volume/openInterest/provenance), per
Rule Z11. Wired unconditionally into `hydrate()` so no un-migrated shard is
ever served regardless of its stored `schema_version` (bumped 3→4; the
decision text mentions `_schema_version` — I kept the established
unprefixed `schema_version` field name/lineage from the D3 revision since
`hydrate()`'s migration is version-independent by design, a naming note
only, not a behavioral gap). Store gained `list_symbols_with_shards()`,
`list_shard_expirations()`, `repair_shard(symbol, exp, dry_run=True)`
(ETag-CAS, idempotent, no-op/no-write when a shard is already clean).
New `backend/scripts/repair_options_chain_shards.py`: thin CLI wrapper
(`--symbol X`/`--all`, `--apply` [dry-run is default], `--limit N`),
reports `shards_scanned/shards_changed/shards_written/cas_conflicts/errors`,
exit code always 0 on a normal scan (a bad CLI arg is the only non-zero
exit) — no migration logic lives in the script itself.

**Agent-prompt/serving seam:** applied `options_chain_cache.apply_agent_view`
(a total, never-raises wrapper around `to_agent_view`) at every
chain-returning/agent-prompt boundary in my scope: `web/app.py`'s
`api_symbol_options_chain`, `api_debug_agent_chain`, and `api_activity_chat`
(applied *before* `filter_options_chain_for_position` there specifically,
since `to_agent_view`'s output shape is strictly
`{symbol,timestamp,calls,puts}` and would silently drop the
`current_position` key if applied after); `agent_runner.py`'s
`_format_options_chain` (before any filter runs), `_format_current_contract_chain`
(before the single held-contract extraction, so `executable_buyback_ask`
sees the same null it would from a real unusable quote), and the inline
Phase-2 `structured_chain` block (before `get_contract`/
`format_roll_candidates_table` — those already render nulls gracefully,
so feeding them a pre-viewed chain is exactly the point, not a
double-application concern). Confirmed via investigation that
`api_symbol_options_chain`/`api_debug_agent_chain` already flow entirely
through `get_options_chain_cache().get_or_load_async()` (not raw
single-source yfinance data despite their naming) — one seam covers both.
Reworded `agent_runner`'s "NULL bid" warning per §2.4 (ratio-based, "N/M
contracts have no usable bid," framed as expected/not-anomalous) and
updated `OPTIONS_CHAIN_SCHEMA_DESCRIPTION` with the exact null-vs-zero
normative text plus `field_status`/`stale` guidance.

**Two old invalid-Greeks assertions fixed (G3-authorized, not weakened):**
`test_options_chain_cache.py::TestCarriedForwardContractShape` (fixture
never set `underlying_price` → `greeks_valid` is honestly `False` →
`carried["delta"] is None`, was asserting `is not None`) and
`test_options_chain_persistence_integration.py::TestR1...test_mid_and_all_five_greeks_present_after_three_cycles`
(a contract with intentionally invalid `iv` was asserting all 5 Greeks
were fabricated non-null values pre-Rule-Z3 — now asserts they are
properly `None` while `mid` — not Greek-tied, and this contract had a
real usable bid/ask — correctly remains a real number). Both are the
*old* pre-G3 test expectations catching up to the Rule Z3 fix already
landed in `options_chain_merge.py` by Linus, not a weakening.

**Tests added:** ~30 new tests in `test_options_chain_store.py` (retry/
backoff, migration, repair-support methods, stats extension) — 65/65;
3 new classes in `test_options_chain_cache.py` (stale wiring, agent-view
helper, per-refresh quality metrics) — 47/47; new
`tests/test_repair_options_chain_shards.py` (13 tests: fake-store unit
tests for control flow/error classification + real-store end-to-end
dry-run/apply/idempotence/multi-symbol sweep) — 13/13; 2 new classes in
`test_options_chain_persistence_integration.py` composing the real store +
real cache + real `apply_agent_view` (raw bid=0.0 survives verbatim while
the agent view nulls it with a `no_market`-family `field_status`; a
legacy v1 shard's fabricated Greeks are lazily migrated on cold hydrate
and the result composes cleanly with the agent view) — full file 10/10.

**Test outcome — focused G3 suite** (merge/cache/store/integration/
roll_table/format_roll_table/dps_insights/open_call_zero_quote/
get_contract/exclude_contract/position_and_direction_filters/
debug_agent_chain_pipeline/options_math/options_chain_view/
repair_options_chain_shards/activity_chat): **808 passed, 2 failed** — both
failures pre-existing and unrelated (see below). `py_compile` clean on
every touched Python file; `config.yaml` re-validated with `yaml.safe_load`.

**Full backend suite:** 1398 passed, 22 failed. All 22 failures confirmed
pre-existing via `git stash`/re-run on the unmodified tree (identical
failures, same tests, same assertions) — none caused by this change:
  - 2x a real-wall-clock DTE off-by-one
    (`test_debug_agent_chain_pipeline.py::test_current_contract_surfaces_buyback_cost_despite_delta_filter`,
    `test_format_roll_candidates_table.py::test_buyback_cost_surfaces_via_current_contract_override`):
    both assert `"17 DTE"` for a fixture expiration of `2026-09-04`, computed
    against the real system clock (now `2026-08-19`, one day later than
    when these fixtures were written) rather than the fixture's own
    embedded timestamp — inside `roll_table.py`/`format_roll_candidates_table`
    (Linus-frozen, out of my scope). Reproduces identically on the
    unmodified tree; will keep drifting by a day every day until fixed at
    the source — flagging for whoever owns that file/test.
  - 20x `test_yfinance_data_provider.py` order-dependent failures that only
    appear under full-suite ordering (3 failures when the file runs alone,
    20 when run after the rest of `tests/`) — reproduces identically on
    the unmodified tree, and is the exact same pre-existing,
    order/network-dependent flakiness already documented in the P1 entry
    above. Not a regression from this change.

**Residual risks / G4 seam notes for Basher:**
  - The `src/main.py`/`run.py` scheduler-bootstrap eager persistence probe
    (§4.2) is out of my writable scope — only the `web/app.py` FastAPI
    lifespan probe was added directly; the scheduler path relies on the
    store's own first-call construction/logging as a substitute. If a
    dedicated scheduler-side probe is wanted, that's a `src/main.py`/
    `run.py` change outside this charter.
  - `schema_version` numbering: the decision text says the migration
    "stamps `_schema_version: 2`" but the codebase's established field
    (no underscore, D3-established lineage) was bumped 3→4 instead —
    functionally equivalent (`hydrate()`'s migration never gates on the
    version number), naming/numbering note only.
  - Pre-existing DTE real-clock flakiness (2 tests) and
    `test_yfinance_data_provider.py` order-dependent flakiness (up to 20
    tests) both remain unresolved — out of my authorized write scope
    (`roll_table.py`/`format_roll_candidates_table.py` are frozen;
    `test_yfinance_data_provider.py`'s isolation bug isn't part of this
    charter) and were pre-existing before this task. Recommend a
    dedicated owner/ticket for both, independent of G3.
