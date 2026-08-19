# Basher — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Role:** Test, regression, and reviewer-gate owner
- **Stack:** Python, pytest, TypeScript/React, CosmosDB, Microsoft Agent Framework

## Core Context

- Built deployment and migration validation for CosmosDB, including idempotent
  provisioning, dry-run/backup/restore workflows, schema transformation checks,
  orphan handling, and progressive integrity validation.
- Established anti-403, scheduler, alert, activity-chat, DPS Insights, roll
  table, watchlist, and position-financial regression suites.
- Review standard: test production-shaped data, malformed and boundary inputs,
  persistence atomicity, frontend/backend contract parity, and current-state
  integration rather than stale concurrent snapshots.
- Option economics use the 100-share contract multiplier only for dollar
  values; ratios, per-share values, counts, filters, and ordering stay unscaled.
- Provider fetch tests no longer enforce retired DTE windows; expiration and
  roll-candidate limits are separate concerns.

## Recent Learnings

### 2026-08-17 — Buy Tracker Normalization Contract
- Added parameterized coverage for all score mappings, exceptional-gate inputs
  and boundaries, hard-WAIT overrides, raw-evidence precedence, malformed
  breakdown/evidence, canonical flags, coherent output, and non-mutation.
- Runner tests prove normalized WAIT is non-alert, BUY/STRONG_BUY are alerts,
  and one normalized object reaches enrichment, evaluation, persistence, and
  notification.
- Final provider-proxy contract approved. Buy Tracker validation reported 271
  focused tests passing.

### 2026-08-17 — Open Call Zero-Quote Safety
- Executable ask must be numeric, finite, and greater than zero; strings,
  booleans, zero, negatives, NaN, and infinity are invalid.
- Roll tables and snapshot P&L use executable ask, not midpoint. Missing or
  invalid buyback economics remain null and cannot pass profit-target rules.
- Production-shaped MSFT coverage verifies WAIT degradation, no profit-only
  Phase 2 or alerts, safe prose, repeated cycles, and valid positive-ask CLOSE.
- Final validation reported 297 focused, 76 integration, and 717 backend tests
  excluding unchanged provider tests; reviewer contract approved.

### 2026-08-08 — Watchlist and Position Financial Review
- Approved deterministic suitability categories: All, Ideal Puts, Ideal Calls,
  No Puts, and No Calls. Classification is based on normalized Entry + Momentum
  semantics, not tracking flags or option-chain delta filters.
- Verified symbol creation, shares editing, forecast backfill isolation, and
  strict financial input validation with persistence/status preservation.
- Frontend validation used focused ESLint, TypeScript, and a runtime
  classification matrix because no dedicated frontend test runner exists.

### 2026-08-18 — Debug Agent Chain Pipeline: MSFT 525C 2026-09-04 "contract absent" bug
- **Repro (read-only, live yfinance data):** MSFT spot $480.35; 525 call exp
  2026-09-04 (17 DTE) has real bid=$0/ask=$0, Black-Scholes delta≈1e-11→rounds
  to 0.0 (IV 6.25%, correctly computed — not a greeks-calculator bug).
- **Root cause (proven with a synthetic contract carrying a valid $0.30 ask,
  delta 0.05):** `web/app.py` `api_debug_agent_chain` derives Stage 2's
  `bb_cost` (buyback/current-contract reference) from `position_filtered`,
  which is built from Stage 1's `filter_options_chain_by_delta` output. Any
  current position whose OWN delta falls outside the standard band
  (0.15–0.90 calls / −0.60..−0.15 puts) is silently dropped before the
  strike/expiration lookup ever runs — `bb_cost` stays `None` and
  `format_roll_candidates_table`'s "CURRENT POSITION" block shows "N/A" /
  absent EVEN WHEN a real, positive, executable ask exists in the raw chain.
  Confirmed via inline harness: debug path → `bb_cost=None`; production path
  `get_contract()` (raw chain, pre-delta-filter, used in `agent_runner.py`
  lines ~2457-2463 and ~1599) → `ask=0.3` correctly retrieved. This is a
  genuine parity gap — the debug endpoint never adopted the
  capture-before-delta-filter pattern already established by the 2026-07-09
  "Preserve Buyback Cost Reference" decision.
- **Compounding factor in THIS specific live snapshot:** the 525-strike
  neighborhood at 2026-09-04 is genuinely illiquid (bid=ask=0 across
  490–560), so zero ROLL_OUT candidates there is partly a real, correct
  "no market" outcome — not purely the code defect. The defect is proven
  independent of that via the synthetic $0.30-ask case above.
- **Coverage gap:** zero tests exist for `/api/debug/agent-chain/{symbol}`,
  `format_roll_candidates_table`, `filter_options_chain_for_position`, or
  `filter_options_chain_by_roll_direction` (grepped `tests/*.py` — no hits).
  Only Stage 0/1 (type + delta filter) have unit coverage, in
  `test_watchlist_symbols.py`. Targeted baseline run: 173 passed, 1
  pre-existing unrelated failure (`test_greeks_populated_for_nonzero_iv`,
  documented yfinance-mock-drift issue, not a regression).
- **Verdict: REJECT current behavior.** Acceptance criteria for Linus: (1)
  debug endpoint must resolve the current contract via a raw/pre-delta-filter
  lookup (`get_contract`-style) so buyback cost/bid/delta/theta display
  whenever a positive finite ask exists, regardless of the contract's own
  delta; (2) exclusion from candidacy (Stage 3/4 listing) must stay separate
  from loss of reference/buyback data; (3) add regression tests for: an
  out-of-band-delta current position with a valid ask (must show real data,
  not N/A), a genuine zero-liquidity neighborhood (must still render "NO
  EXECUTABLE BUYBACK QUOTE... WAIT"), plus direct unit tests for
  `filter_options_chain_for_position`, `filter_options_chain_by_roll_direction`,
  `format_roll_candidates_table`, and the debug route itself; (4) the 173
  currently-passing targeted tests must stay green.

### 2026-08-18 — Debug Agent Chain Pipeline Fix: APPROVED
- **Diff reviewed:** `options_chain_filters.py` (+20/-3, adds optional
  `current_contract` param to `format_roll_candidates_table`, backward
  compatible, `executable_buyback_ask` gate preserved, explicit
  `buyback_cost` still takes precedence), `agent_runner.py` (+1, threads the
  already-captured pre-filter `current_contract` into the production Phase 2
  call), `web/app.py` (+13/-13, Stage 4 now sources the current contract via
  `get_contract(structured, ...)` on the RAW chain instead of the
  delta-filtered `position_filtered`, with `executable_buyback_ask` replacing
  the old naive `float(bb_ask)`).
- **All 4 acceptance criteria met:** (1) pre-delta-filter lookup implemented
  in the debug endpoint; (2) candidacy exclusion (direction filter still
  strictly excludes the held strike+expiration) kept separate from
  reference/buyback-cost preservation; (3) new regression tests cover
  out-of-band-delta-with-valid-ask, genuine-zero-ask-still-incomplete, and
  direct unit tests for `filter_options_chain_for_position` /
  `filter_options_chain_by_roll_direction` / `format_roll_candidates_table` /
  the debug route; (4) prior 173 targeted tests still green.
- **Test result (targeted, smallest complete set):**
  `pytest tests/test_debug_agent_chain_pipeline.py
  tests/test_format_roll_candidates_table.py
  tests/test_options_chain_position_and_direction_filters.py
  tests/test_options_chain_cache.py tests/test_yfinance_data_provider.py
  tests/test_get_contract.py tests/test_exclude_contract.py
  tests/test_roll_table.py tests/test_watchlist_symbols.py
  tests/test_open_call_zero_quote.py -q` → **213 passed, 1 failed**
  (`test_greeks_populated_for_nonzero_iv` — pre-existing, documented
  yfinance-mock-drift baseline, untouched by this diff).
- **Caveat (non-blocking):** full unfiltered `pytest -q` shows 20 failures
  in `test_yfinance_data_provider.py` vs. 1 in isolation/targeted runs —
  confirmed via a rerun with `--ignore` on all 3 new test files that this
  test-isolation/ordering issue is 100% pre-existing (identical 20 failures
  with the new tests absent), not introduced by this fix. Out of scope for
  this review.
- No mocks elsewhere patch `format_roll_candidates_table` directly, so the
  new optional kwarg is a safe, non-breaking signature change.
- **Verdict: APPROVE.**

## Durable Testing Patterns
- Use hermetic mocks for Cosmos and provider boundaries.
- Assert invalid inputs cause no writes.
- Preserve exact upstream HTTP status codes through BFF/backend layers.
- Include repeated-cycle tests for scheduler and alert state.
- Treat existing unrelated provider failures as baseline, not regressions.
- Debug/diagnostic endpoints that re-derive economics from an
  already-filtered chain can silently diverge from the production pipeline;
  always verify they reuse the same pre-filter capture pattern (e.g.
  `get_contract` before delta filtering), and prove data-loss bugs with a
  synthetic contract carrying a valid quote so a genuinely-illiquid live
  snapshot can't mask the defect.
- Before blaming a fix for full-suite failures, isolate with `--ignore` on
  the new files — a pre-existing test-order/isolation issue can look like a
  regression if only compared against a targeted subset run.

### 2026-08-18 — Persistent Option Chain Merge (Danny's design): read-only review
- **Scope:** reviewer checklist + edge cases for Danny's accepted
  accumulate-and-merge design (`.squad/decisions/inbox/danny-persistent-option-chain-merge.md`),
  read-only — no production/test files edited.
- **Baseline confirmed live in `options_chain_cache.py`:** G1 (no
  persistence, `self._store` is a bare process dict), G2 (TV overlay hardcodes
  `volume/openInterest/lastTradeDate/inTheMoney/contractSymbol` outside
  `_QUOTE_FIELDS`, wiping yfinance real values — confirmed in
  `tv_options_chain_fetcher._parse_tv_to_yfinance_format`), G3 (`mid` +
  greeks are inside `_QUOTE_FIELDS` today, so they're field-merged, not
  recomputed — a carried delta can pair with a fresher iv), G4 (`bid==0`
  always treated as invalid, no trust-gate discriminator), G5
  (`_parse_tv_to_yfinance_format` falls back to `str(raw_exp)` for
  unparseable expirations; `_prune_expired_expirations` only touches
  8-digit numeric keys, so junk keys are permanent) — all reproduced by
  direct code inspection, matching Danny's doc exactly.
- **Baseline tests green:** `pytest tests/test_options_chain_cache.py
  tests/test_options_chain_position_and_direction_filters.py
  tests/test_format_roll_candidates_table.py tests/test_get_contract.py
  tests/test_exclude_contract.py -q` → 67 passed. Documented pre-existing
  isolation failure reconfirmed: `test_yfinance_data_provider.py` alone →
  1 failed (`test_greeks_populated_for_nonzero_iv`), 20 passed — baseline,
  not a regression signal for this work.
- **Hidden incompatibility for Rusty/Linus (deployment topology
  correction):** Danny's G1 severity text says "the web process and the
  scheduler process each hold a separate singleton." `docs/deployment.md`
  + `backend/run.py` show the real topology is a single combined `api`
  container (`min/max-replicas 1`) running the FastAPI app AND the
  in-process APScheduler in **one process, one thread each** — so
  `get_options_chain_cache()`'s module-level singleton *is* actually shared
  between "web" and "scheduler" in today's primary deployment; restart/
  redeploy of that one container is what wipes it, not cross-process
  divergence. The real cross-process divergence risk is the docs' own
  documented pattern: an **extra `--web-only` API replica** added purely to
  serve reads (docs explicitly allow/recommend this to avoid duplicate cron
  runs) would hold its own independent, unhydrated singleton, cold on every
  restart and never receiving the scheduler-owning replica's accumulated
  history. T13/T14 (hydrate-on-miss/restart) must explicitly cover the
  "fresh process, populated store" case since that's the scenario that
  actually occurs in this deployment, not a second concurrently-running
  scheduler.
- **CAS retry precision:** the only existing ETag-CAS precedent in this repo
  (`cosmos_db.py CosmosDBManager.update_settings`) retries on **both 409 and
  412**, not just 412 as Danny's §5.2 text states, and *raises* after
  max attempts (settings must not silently fail). Rusty's
  `options_chain_store.py` should catch both status codes for consistency
  with the established pattern, but should deliberately keep the *opposite*
  failure behavior (log WARNING + skip shard, never raise) per §5.4 — that
  divergence from the established pattern is intentional and must be called
  out in the PR description, not left implicit, so a future reader doesn't
  "fix" it back to raising.
- **`robust_mid` cross-check:** confirmed current `options_math.robust_mid`
  already documents `bid<=0` as a real, valid market state (bid-less
  contract marks near 0) — directly supports G4/the trust-gate; any T2/T3
  test fixtures should reuse `robust_mid`'s own documented boundary values
  (bid=0 with ask>0 sane-two-sided vs wide-stale-ask cases) rather than
  inventing new ones.
- **Full checklist, APPROVE criteria, and validation commands delivered to
  Rusty/Linus/Danny in-session** (not duplicated here — see decisions log /
  session transcript for the complete reviewer document). Verdict pending
  their implementation; this pass is design-review only, nothing to
  APPROVE/REJECT yet since no diff exists.

### 2026-08-18 — Persistent Option Chain Merge (implementation gate): REJECT
- **Independently reproduced 3 high-confidence defects** with the real
  (unmocked) `src.options_chain_merge` functions, matching Danny's
  concurrently-filed reject (his history.md briefly showed a REJECT entry
  mid-session that a later write superseded — I did not touch his file,
  only recorded my own independent proof here).
- **Defect 1 — derived fields silently stripped from every Cosmos shard
  after its first write.** `OptionsChainStore._write_shard` calls the real
  `merge_prior(prior_shard_chain, live_shard_chain, now=now)` whenever the
  shard already exists (not just on CAS 409/412 — every normal write hits
  this branch), treating the already-recomputed in-memory chain as if it
  were a fresh single-cycle observation. `_merge_prior_contract` (by
  design, correctly for its *intended* raw-accumulation use) only copies
  quote-group + observed + `_meta` + identity fields — `mid/delta/gamma/
  theta/vega/rho` are never in that list. Repro: persisted a contract with
  delta=0.522575 in cycle 1 (exists=False path, fields intact); ran the
  exact cycle-2 re-merge `_write_shard` performs when `stored is not None`
  → `delta`/`mid` absent from the result actually written. Breaks
  "restart/web-only hydration restores persisted chain" (hydrate returns
  contracts with no delta) and "downstream delta/buyback behavior"
  (`filter_options_chain_by_delta` gates on delta — a cold-hydrated chain
  would be filtered near-empty).
- **Defect 2 — `_hydrate_into_memory` never calls `prune_by_expiration`.**
  Repro: `OptionsChainCache.get_or_load()` against a `FakeStore` whose only
  shard is 3 days past expiration (inside the 7-day persistence grace)
  served that expired contract through the normal read API, uncapped by
  the same-day serving cutoff. The store's 7-day grace is meant only to
  let *persistence* lag behind serving, not to be re-exposed as live data
  on every cold hydrate. Breaks the "actual expiration/grace pruning"
  invariant specifically on the restart/new-replica path (the `refresh()`
  path prunes correctly — confirmed via existing
  `TestActualExpirationPruning`, which only exercises `refresh()`, never
  hydrate-only).
- **Defect 3 — provenance corrupted on (almost) every persist cycle.**
  Same root cause as Defect 1: re-running `merge_prior` on an
  already-merged snapshot makes `_select_quote_field` treat still-present-
  but-old fields as freshly "accepted," so a genuinely 3-day-old
  carried-forward contract (`_meta.carried=true`, `quote_asof` 3 days
  stale) gets re-stamped `carried=false`, `quote_asof=<persist time>` in
  what's written to Cosmos — indistinguishable from a live quote. Repro
  confirmed with the real `merge_prior`. This defeats R1's accepted
  mitigation (design doc: "the retention invariant means agents can now be
  shown a three-day-old quote... `_meta.quote_asof` + schema-doc update"
  — the mitigation is worthless if `quote_asof` itself lies).
- **Root cause of why 557 targeted tests stayed green:** confirmed
  `tests/test_options_chain_store.py`'s `fake_merge_module` fixture
  monkeypatches `sys.modules["src.options_chain_merge"]` with a naive
  fake `merge_prior` (preserves all fields unconditionally) for every CAS/
  write test — the real field-class-aware `merge_prior` is never exercised
  by a second write in that file. `tests/test_options_chain_cache.py`'s
  `_FakeStore.persist()` stores the chain verbatim (no merge at all), so
  cache-level tests correctly prove the in-memory pipeline but say nothing
  about what reaches Cosmos. No test in either file calls the real
  `OptionsChainCache.refresh()` twice against a real `OptionsChainStore`
  + fake Cosmos container and asserts `delta`/`mid`/`_meta.carried` survive
  the second write — that missing integration test is exactly what let a
  broken composed system pass a fully-green suite.
- **What is solid, confirmed via direct repro + passing targeted tests
  (557/557: `test_options_chain_merge.py`, `test_options_chain_store.py`,
  `test_options_chain_cache.py`, `test_options_chain_position_and_
  direction_filters.py`, `test_format_roll_candidates_table.py`,
  `test_debug_agent_chain_pipeline.py`, `test_tv_options_chain_fetcher_
  normalize.py`, `test_get_contract.py`, `test_exclude_contract.py`):**
  G2/G5 fixed in `tv_options_chain_fetcher.py` (absence-not-zero, Rule S3
  expiration rejection); source-merge trust gate, degeneracy gate,
  monotonicity (incl. a 40-cycle randomized fuzz test against the real
  `merge_prior`); `refresh()`'s own prune-by-expiration; non-fatal
  persistence (never raises, always returns the good in-memory chain);
  `OPTIONS_CHAIN_SCHEMA_DESCRIPTION` staleness-disclosure update; watchdog/
  `refresh_all` timeout preserved; debug-endpoint buyback fix
  (`current_contract` capture-before-filter) still correct and covered.
  Full-suite baseline unchanged: 20 pre-existing `test_yfinance_data_
  provider.py` failures with new files `--ignore`d (identical count with
  or without this diff — confirmed not a regression). Note:
  `test_yfinance_data_provider.py` run in isolation is flaky/non-
  deterministic independent of this diff (1 failure at session start, 3
  reproducibly on later runs) — traced to `@patch("src.yfinance_data_
  provider.yf")` not actually intercepting `OptionsChainCache._fetch_
  yfinance`'s own local `import yfinance as yf` (pre-existing gap, real
  network calls leak through); not counted as a regression signal either
  way, but worth a follow-up ticket since it silently exercises live
  yfinance/Playwright in "unit" tests.
- **Verdict: REJECT.** Merge semantics (Linus's module) and the TV/G2/G5/
  schema-doc fixes are ready as-is. The store/cache integration (Rusty's
  layer) must not be approved until: (1) `_write_shard`'s re-merge either
  calls `recompute_derived` after merging, or is replaced with a
  reconciliation that preserves already-computed derived fields and true
  prior `_meta` instead of re-deriving acceptance against an already-merged
  snapshot; (2) `_hydrate_into_memory`/`_load_previous_chain`'s hydrate
  path applies `prune_by_expiration` before serving; (3) a new
  integration test exists calling the real `OptionsChainCache.refresh()`
  twice against a real `OptionsChainStore` + fake Cosmos container
  (no `fake_merge_module`, no `_FakeStore`) asserting delta/mid survive
  and `_meta.carried`/`quote_asof` never regress across the second write.

### 2026-08-18 — Persistent Option Chain Merge: Independent confirmation of Danny's D1–D5
- Cross-checked Danny's REJECT (D1–D5, `.squad/decisions/inbox/danny-revision-directive-option-chain-2026-08-18.md`)
  against my own independent evidence — **all 5 confirmed**, no refutations,
  with new direct reproductions for D3's second half and D4/D5 (not covered
  by my first pass).
- **D1 (derived fields dropped) — confirmed**, matches my own earlier repro exactly.
- **D2 (provenance corrupted) — confirmed**, matches my own earlier repro exactly.
- **D3 (hydrate ignores serving horizon + missing top-level fields) — confirmed
  and extended.** Code-read of `OptionsChainStore.hydrate()` shows it returns
  only `{"symbol","calls","puts"}` — no `timestamp`/`underlying_price`, both
  documented as mandatory top-level fields in the frozen
  `OPTIONS_CHAIN_SCHEMA_DESCRIPTION`. `_hydrate_into_memory` stamps
  `cached_at=time.monotonic()` unconditionally ("marked fresh" per its own
  docstring) so a hydrated chain — however stale — never schedules a SWR
  background refresh until a full TTL elapses. Also confirmed `underlying_price`
  is required by `recompute_derived` but is a chain-level field never written
  into any per-expiration shard body — so D1's fix cannot be "just recompute on
  hydrate" without a shard-schema change to carry it, exactly as the directive
  specifies.
- **D4 (locking wrong for async) — confirmed with 2 new direct reproductions**
  using the literal `threading.RLock` + blocking `.acquire()` pattern from
  `cache.py`'s `refresh()`:
  1. Same-event-loop reentrancy: two `asyncio.gather`-ed coroutines both
     "holding" one RLock — task B's `acquire()` returns instantly (t=0.0)
     while task A still holds it, instead of waiting for A's release at
     t=0.3s. Confirms two concurrent `await refresh(sym)` calls on one loop
     both run the full cycle unserialized.
  2. Event-loop freeze: a blocking `acquire()` on the loop's own thread while
     a second OS thread holds the lock stalls an unrelated concurrent
     coroutine's `asyncio.sleep(0.1)` heartbeat — its first tick, due at
     ~t=0.15s, doesn't fire until t=0.601s, right after the blocking acquire
     unblocks at t=0.501s. Confirms a scheduler-thread refresh freezes every
     other request the same process is serving.
- **D5 (fake seam + dead RU guard) — confirmed.** Test-fake-seam finding
  matches my own prior diagnosis exactly. New repro: ran real `merge_prior`
  twice with byte-identical market data 5 minutes apart — `_content_hash`
  differs both times solely because `_meta.last_seen`/`quote_asof` advance
  unconditionally on every re-merge (line `meta["last_seen"] = now_iso`,
  unconditional in `_merge_prior_contract`), so `_write_shard`'s
  `exists and stored.get("_content_hash") == new_hash` skip-write guard is
  provably dead code under real (non-faked) `merge_prior` — every persist
  cycle always rewrites every shard regardless of whether market data moved.
- **No refutations.** All 5 of Danny's blockers hold up under independent,
  from-scratch reproduction with the real modules. Verdict stands: **REJECT**,
  unchanged from my own independent pass; concur with escalation to Livingston
  per the revision directive's bounded scope (§2.1–2.3) and required tests
  (R1–R7, §2.4) — these fully cover the defects I found plus D4/D5's locking
  and RU-guard gaps that I had not yet independently probed in my first pass.

### 2026-08-18 — Persistent Option Chain Merge: Livingston revision — final gate: APPROVE
- **Scope reviewed:** `options_chain_store.py` (rewritten — no longer imports/
  calls `options_chain_merge` at all; `_write_shard` now uses a new
  store-owned, verbatim, recency-based `_reconcile_bucket` instead of
  `merge_prior`; `_content_hash` strips volatile `_meta.last_seen`/
  `quote_asof` before hashing; schema_version 3 adds `underlying_price` to
  every shard; `hydrate()` now restores top-level `timestamp`/
  `underlying_price`), `options_chain_cache.py` hydration/locking portions
  (`_hydrate_into_memory` now applies `prune_by_expiration` + backdates
  `cached_at` to be immediately stale-eligible; locking replaced with
  same-loop `asyncio.Task` memoization in `refresh()` + a non-reentrant
  `threading.Lock` whose blocking acquire is offloaded via
  `loop.run_in_executor` in the new `_refresh_exclusive`), rewritten
  `test_options_chain_store.py` (old `fake_merge_module` fixture is gone —
  confirmed via grep, no `sys.modules["src.options_chain_merge"]`
  monkeypatch remains anywhere in the file), and new
  `test_options_chain_persistence_integration.py` (622 lines, R1–R7).
- **No fake spans the real store↔merge seam — confirmed by direct code
  read.** The integration file imports the real `OptionsChainCache`,
  `OptionsChainStore`, `filter_options_chain_by_delta`; the only fakes are
  `FakeContainer` (a faithful in-memory Cosmos stand-in with real
  ETag/412 semantics — the legitimate I/O boundary) and
  `_fetch_yfinance`/`_fetch_tradingview` (the legitimate network boundary,
  consistent with how the rest of the suite already fakes providers).
  `options_chain_store.py` no longer imports `options_chain_merge` at all
  (grep confirmed), so the old masking mechanism structurally cannot recur.
- **D1–D5 independently re-verified fixed, each with a fresh direct
  reproduction against the real modules (not just reading code, not just
  trusting the new tests):**
  - D1: persisted a contract with all 5 greeks + mid via the real
    `OptionsChainStore`, then re-persisted a second cycle 3 days later —
    hydrate returned `mid/delta/gamma/theta/vega/rho` all present, byte-for-byte.
  - D2: same repro, contract deliberately carried-forward (`_meta.carried`
    pre-set True, `quote_asof` 3 days stale) — hydrated `_meta` came back
    identical (`carried=True`, `quote_asof` unchanged), no corruption.
  - D3: same repro's `hydrate()` output included both `timestamp` and
    `underlying_price` at the top level.
  - D4: reran both of my own D4 repro scripts against the real
    `OptionsChainCache` (not toy locks this time): (a) two
    `asyncio.gather`-ed `cache.refresh("AAPL")` calls on one loop now
    produce exactly 1 fetch (`fetch_calls["n"] == 1`) and identical
    results — task memoization fixed the reentrancy hole; (b) a real
    background OS thread holding the cross-thread lock for 0.5s no longer
    freezes the event loop — an unrelated heartbeat coroutine ticked every
    ~0.1s throughout the wait (6/6 ticks on schedule) instead of bursting
    after the block cleared.
  - D5: re-persisted byte-identical market data 5 minutes apart via the
    real store — second cycle correctly reports `unchanged: 1, written: 0`
    (previously this was unconditionally `written: 1` every cycle).
- **Test outcome (exact):**
  - Targeted: `pytest tests/test_options_chain_merge.py
    tests/test_options_chain_store.py tests/test_options_chain_cache.py
    tests/test_options_chain_persistence_integration.py
    tests/test_options_chain_position_and_direction_filters.py
    tests/test_format_roll_candidates_table.py
    tests/test_debug_agent_chain_pipeline.py
    tests/test_tv_options_chain_fetcher_normalize.py
    tests/test_get_contract.py tests/test_exclude_contract.py -q`
    → **571 passed** (557 previously + 14 new R1–R7 tests).
  - Full suite: `pytest tests/ -q` → **1245 passed, 20 failed** — same 20
    pre-existing `test_yfinance_data_provider.py` failures as every prior
    baseline in this review (unrelated broken mock, not this diff).
- **Original 7 invariants — all now hold** (previously 4/7): prior-valid-
  survives-zeros ✓ (frozen, unaffected), TV-valid-only-overwrite ✓ (frozen),
  TTL-never-deletes ✓ (unaffected), non-fatal-persistence ✓ (unaffected,
  re-confirmed), restart/hydration-restores-chain ✓ (FIXED — D1/D3), actual
  expiration/grace-pruning ✓ (FIXED — D3, hydrate now applies the same-day
  serving prune, verified by R4 + my own repro), downstream-delta/buyback ✓
  (FIXED — derived fields survive persistence, R2's cold-replica filter-
  parity test plus my own D1 repro confirm it directly).
- **Verdict: APPROVE.** All D1–D5 blockers fixed, verified fixed
  independently (not just via the new tests), scope stayed within the
  directive's bounded authorized files, `options_chain_merge.py` untouched
  (byte-frozen as required), watchdog regression test still passes, no
  assertions weakened anywhere in the existing suite.

### 2026-08-18 — Persistent Option Chain: Livingston P1 follow-up (get_or_load deadlock) — APPROVE
- **Scope:** `options_chain_cache.py` only — new `OptionsChainNotReadyError`
  class + rewritten `get_or_load` cold-miss branch (`_refresh_locked`,
  `refresh_all`, `_sync_refresh`, `get_or_load_async`, merge/store semantics,
  `web/app.py` untouched, confirmed by code read); additive-only
  `test_options_chain_cache.py` (`TestGetOrLoadRunningLoopNeverBlocks`,
  `TestGetOrLoadSyncCallerBehaviorPreserved`, 5 new tests, 34 pre-existing
  unmodified).
- **Root cause confirmed by code read:** old `get_or_load`'s cold-miss path
  bridged sync→async via `ThreadPoolExecutor().result(timeout=120)`, a
  blocking wait on the *calling loop's own thread*; combined with D4's
  now-genuinely-contended per-symbol OS lock, this could self-deadlock if
  the lock-holder needed that same (now-frozen) loop to resume — reachable
  synchronously from `web/app.py:3249`'s `async def api_activity_chat`.
- **Fix verified by code read:** branches on `asyncio.get_running_loop()`.
  No loop → unchanged (blocks thread, real full refresh via a private new
  loop). Loop running → zero blocking of any kind: reuses the existing
  non-blocking try-acquire `_schedule_background_refresh` (dedups against
  an already-in-flight refresh for the same symbol) and immediately raises
  `OptionsChainNotReadyError(RuntimeError)`.
- **Independently reproduced all 4 required scenarios myself, directly
  against the real `OptionsChainCache` (not just trusting the new tests):**
  1. Same-loop lock contention: a same-loop coroutine holding the OS lock
     (via `run_in_executor`) + a heartbeat coroutine + `get_or_load` on a
     true cold miss — `get_or_load` raised `OptionsChainNotReadyError` in
     0.0003s (not 120s, not a hang); heartbeat kept ticking throughout
     (loop never froze).
  2. Cold miss with an already-in-flight background refresh for the same
     symbol: exactly 1 fetch call total (no duplicate), cache populated
     once the in-flight task completed.
  3. Warm/hydrated data returns immediately with zero fetch calls: verified
     both the pure in-memory-cached path and the store-hydrate path
     (<0.2ms each, fetch methods rigged to raise `AssertionError` if
     called — neither was).
  4. Genuine sync caller (real background thread, no running loop):
     unaffected — still blocks and returns real freshly-fetched data,
     independent of another symbol's lock being held elsewhere.
- **Activity-chat graceful degradation reconfirmed valid** by direct code
  read of `web/app.py:3249` (`api_activity_chat`'s `try/except Exception`
  around `cache.get_or_load(symbol)`, degrading to `"(option chain
  unavailable: {e})"`, HTTP 200) plus `tests/test_activity_chat.py::
  test_chain_unavailable_degradation` (still valid: its `FakeOptionsChainCache`
  raises a plain `RuntimeError`, and `OptionsChainNotReadyError` is a
  `RuntimeError` subclass, so the same broad handler catches it identically).
- **Test outcome (exact, run myself):**
  - `pytest tests/test_options_chain_merge.py tests/test_options_chain_store.py
    tests/test_options_chain_cache.py tests/test_options_chain_persistence_integration.py
    tests/test_options_chain_position_and_direction_filters.py
    tests/test_format_roll_candidates_table.py tests/test_debug_agent_chain_pipeline.py
    tests/test_tv_options_chain_fetcher_normalize.py tests/test_get_contract.py
    tests/test_exclude_contract.py tests/test_activity_chat.py -q` →
    **589 passed** (571 prior + 5 new cache tests + 13 activity-chat).
  - `tests/test_options_chain_cache.py` alone, re-run 3x for determinism:
    **39/39 passed each run**, no flakiness.
  - Full suite `pytest tests/ -q` → **1250 passed, 20 failed** — same
    pre-existing, unrelated `test_yfinance_data_provider.py` failures as
    every prior baseline this review cycle (confirmed not a regression).
- **Verdict: APPROVE.** Fix is correctly scoped (single file, additive
  tests only), verified fixed independently (not just via the provided
  tests), does not touch `get_or_load_async`/`refresh_all`/watchdog/merge/
  store, and the activity-chat consumer's existing graceful-degradation
  contract is intact and re-verified.

## 2026-08-18 — Buy Tracker: Root-Cause Diagnosis of "score_breakdown → canonical 0/5" (READ-ONLY)

**Task:** Independently diagnose why Buy Tracker output shows missing
`score_breakdown` collapsing to canonical 0/5 WAIT while SMA50/SMA200/
Stochastic/dividend-growth-years are reported unavailable. Read-only;
no production/test edits made.

**Pipeline traced:** `buy_tracker_agent.py` (thin orchestrator, no
data-shaping) → `AgentRunner.run_symbol_agent` (generic pass-through;
grepped `agent_runner.py` for the field names in question —
zero matches, confirming no intermediate transform there) → LLM call
against `buy_tracker_instructions.py`'s documented field-mapping
contract → `rule_evaluator.build_buy_tracker_evidence` (evidence
adapter) → `rule_evaluator.normalize_buy_tracker_activity` /
`_validate_buy_tracker_breakdown` (deterministic post-LLM validator).
`dps_scorer.py` checked and confirmed **unrelated** — its
`score_breakdown` is a distinct list-of-factors structure for a
different (DPS/covered-call) scorer, not in the Buy Tracker path.

**`rule_evaluator.py`'s evidence adapter (`build_buy_tracker_evidence`,
~line 1316) and `_validate_buy_tracker_breakdown` (~line 1615) are
both correct and NOT the bug.** The adapter's field mapping matches
`buy_tracker_instructions.py`'s documented contract exactly (no
alias/schema mismatch). `_validate_buy_tracker_breakdown` only zeroes
out a dimension if the **LLM's own** JSON output for that key is
missing/non-boolean-0-or-1 — it does not force 0/5 based on upstream
evidence gaps directly; it is downstream of the LLM correctly reacting
(per the prompt's own documented rule) to being fed too many `None`
evidence fields to confidently populate its own breakdown.

**Root cause (confirmed by direct reproduction against real production
code and LIVE yfinance data, not synthetic fixtures) — TWO distinct
provider-layer defects, both in `yfinance_data_provider.py` /
`technicals_calculator.py`, upstream of `rule_evaluator.py` entirely:**

1. **Technicals: trailing incomplete "today" OHLCV row silently nukes
   rolling-window indicators.** `yfinance_data_provider.fetch_all` calls
   `ticker.history(period="1y")` and passes it straight into
   `TechnicalsCalculator.compute_all` with **no trimming/dropna of a
   still-in-progress current session**. Live-reproduced against AAPL,
   MSFT, KO, JNJ (all fetched live, same moment): every symbol's
   `history()` call returned a **trailing row with `Close=NaN`**
   (today's bar not yet settled by Yahoo). `_safe_val(series, offset=-1)`
   is used unconditionally for every indicator — since SMA/Stoch rolling
   windows ending on that NaN row require the full window to be non-null,
   **SMA50, SMA200, and Stoch.K came back `None` for all 4 symbols**,
   even though yesterday's values (`offset=-2`) were fully valid
   (e.g. AAPL SMA200 @-1 = `None`, @-2 = `279.99`). RSI/MACD/ADX
   happened to survive (pandas_ta's EWM-based smoothing tolerates the
   trailing NaN differently) — this asymmetry (some indicators vanish,
   others don't) is an exact match to the reported symptom
   ("SMA50/SMA200/Stochastic unavailable" but not RSI/MACD). Confirmed
   fix hypothesis: `history.dropna(subset=["Close"])` before
   `compute_all()` restored all 4 indicators to their correct,
   yesterday-based values for AAPL. `_compute_manual` (pandas_ta-absent
   fallback) has the identical `_safe_val(..., -1)` pattern (56 call
   sites) — same latent defect, currently dormant since pandas_ta 0.4.71b0
   is installed in this environment.

2. **Dividends: current partial calendar year corrupts the
   consecutive-growth-years streak.** `_build_dividends`'s
   `continuous_dividend_growth` computation (`yfinance_data_provider.py`
   ~line 509) resamples `ticker.dividends` by calendar year (`"YE"`) and
   walks backward counting `annual.iloc[i] > annual.iloc[i-1]`, **without
   excluding the current, still-in-progress year**. Live-reproduced for
   KO/JNJ/PG (all real Dividend Aristocrats/Kings with genuine decades-long
   growth streaks): current logic returns **`growth_years = 0` for all
   three**, every time, because the partial-current-year sum is always
   less than the last full year's sum, breaking the streak at the very
   first comparison. Since the field is only added `if growth_years > 0`,
   `continuous_dividend_growth` is **silently omitted** from the JSON for
   every real dividend payer tested. Confirmed fix hypothesis: excluding
   `annual.index.year >= current_year` before the comparison loop yields
   the correct streak (KO=23, JNJ=63, PG=22 years, bounded by available
   yfinance dividend history depth).

**Classification (per the task's requested categories):** This is
**neither** a schema omission (the JSON shape/keys are exactly what
`rule_evaluator.py` and the prompt expect), **nor** an alias/normalizer
mismatch (the evidence-adapter mapping is faithful), **nor** a prompt
inconsistency (the prompt's own missing-score_breakdown fallback rule is
working as documented), **nor** genuinely-missing market data (the data
one period back — yesterday's close, last year's full dividend total —
is valid and available). It is a **provider-layer computation defect**:
both bugs treat an **incomplete trailing period** (today's still-open
session; this year's still-accruing dividends) as if it were a complete,
comparable period, silently discarding perfectly good prior-period data.

**Test-coverage gap confirmed:** `tests/test_technicals_calculator.py`
(44 tests, all passing) builds 100% clean synthetic OHLCV via
`_make_ohlcv`/`_uptrending_ohlcv` etc. — **zero** fixtures include a
trailing NaN/incomplete row, so this defect class was structurally
unreachable by the existing suite. `test_buy_tracker_normalization.py`
(5 passing) and `test_rule_evaluator.py` (196 passing) are unaffected/
irrelevant to this defect since it lives entirely upstream in the
provider layer. Ran `pytest tests/test_yfinance_data_provider.py` — 3
pre-existing, unrelated failures (Greeks/mid-price on options contracts,
not technicals/dividends) — confirmed unrelated to this diagnosis.

**Acceptance criteria for a fix (for the assigned production engineer,
not implemented by me):**
- AC1: `compute_all`/`_build_technicals` must exclude or repair any
  trailing OHLCV row lacking a valid `Close` before computing indicators,
  so SMA/EMA/Stoch/CCI/etc. reflect the last **complete** session.
- AC2: The fix must apply uniformly to all indicators so RSI/MACD (which
  currently "survive" only by accident of pandas_ta's internal NaN
  handling) and SMA/Stoch (which currently vanish) are computed as of
  the *same* reference date — no indicator should silently reflect a
  different "as-of" day than another.
- AC3: `continuous_dividend_growth`'s year-over-year comparison must
  exclude the current, not-yet-complete calendar year (or handle it via
  a TTM-vs-full-year comparison), so real multi-decade dividend-growth
  streaks are no longer universally reported as 0/absent.
- AC4: A regression test using a real-shaped fixture with (a) a trailing
  NaN "today" OHLCV row and (b) dividend history including the current
  partial year must assert SMA50/SMA200/Stoch.K and
  `continuous_dividend_growth` remain correctly populated.
- AC5: Re-verify against real, liquid, actively-covered dividend payers
  that the `score_breakdown` 0/5 canonical fallback no longer triggers
  purely as a downstream artifact of this provider-layer data loss.

**Verdict: REJECT current behavior.** Two concrete, independently
reproduced, high-confidence provider-layer defects (not edge cases —
reproduced live across 4+ real symbols on the first live attempt) are
the first point where valid upstream data disappears, well before
`rule_evaluator.py` or the LLM prompt are ever involved. Recommend fix
ownership at `yfinance_data_provider.py` / `technicals_calculator.py`.

## 2026-08-18 — Buy Tracker: Final QA Gate on Rusty's Fix (READ-ONLY) — APPROVE

**Task:** Final read-only reviewer gate on Rusty's revision closing my
prior REJECT (trailing-NaN-bar technicals loss, partial-current-year
dividend-streak loss). Scope: `backend/src/yfinance_data_provider.py`,
`backend/src/buy_tracker_instructions.py`,
`backend/tests/test_yfinance_technicals_dividend_availability.py`.

**Diff reviewed (`git diff`, 38 lines across 2 files + new 266-line test
file; `rule_evaluator.py` untouched):**
- New `_drop_incomplete_trailing_bars(history)` in `yfinance_data_provider.py`
  — while-loop pops trailing rows with NaN `Close`, called once in
  `fetch_all()` right after `ticker.history()` and before both
  `_build_technicals` and the `current_price` history-fallback (so both
  consumers see the trimmed frame). Guards `None`/empty/no-`Close`-column
  inputs by returning input unchanged.
- `_build_dividends`'s `continuous_dividend_growth` block now drops the
  last `annual` bin when `annual.index[-1].year >= datetime.now(timezone.utc).year`,
  before the growth-streak comparison loop. Still inside the pre-existing
  `try/except Exception` (any resample edge case fails safe, same as
  before).
- `buy_tracker_instructions.py`: added explicit prompt language that
  `score_breakdown` must always be a real 5-key object, that a missing
  dimension's data zeroes *only* that dimension, and that the
  missing/malformed-object fallback is a last resort for genuine
  malformation, not a shortcut for partial data unavailability.

**Independent verification performed (not just trusting the diff/tests):**
1. **Live re-verification** against `_drop_incomplete_trailing_bars` +
   `_build_technicals` + `_build_dividends` for AAPL, MSFT, KO, JNJ, PG
   (same live yfinance call showing the same trailing `Close=NaN` row as
   my original REJECT repro): SMA50/SMA200/Stoch.K/RSI now all populate
   with valid, non-`None` values, and `continuous_dividend_growth` =
   14/20/23/63/22 respectively — KO/JNJ/PG exactly match my original
   independent findings cited in the new test file's docstring.
2. **Trimming cannot remove legitimate rows** — confirmed by direct
   repro: the loop stops at the first row (from the tail) with a
   non-NaN `Close`, so a row is only ever dropped if it carries zero
   usable closing-price information; verified edge cases directly:
   all-`NaN`-`Close` history trims to fully empty (falls through to
   `compute_all`'s existing `<30 bars` → `_empty_technicals()` guard,
   no crash), a frame missing the `Close` column is returned unchanged,
   `None` input is returned unchanged. Also confirmed (via the test
   file's `test_interior_nan_close_is_preserved_only_trailing_is_trimmed`
   and my own read) that only a *trailing* run of NaNs is removed — an
   interior historical gap survives untouched.
3. **Current-year boundary logic** — read `annual.index[-1].year` (the
   resample bin's calendar-year label, which reflects the *bin's* year
   regardless of how partial its data is) compared against
   `datetime.now(timezone.utc).year`; reasoned through DST/timezone-skew
   edge cases at year boundaries (NY-vs-UTC offset is at most a few
   hours, immaterial against quarterly dividend cadence) — no boundary
   defect found. Confirmed the whole block remains inside the original
   `try/except`, so a `resample` corner case (e.g. an unusual empty
   series) fails safe rather than crashing `fetch_all`.
4. **Prompt/schema/normalizer consistency** — `rule_evaluator.py` was
   **not modified**; confirmed `_validate_buy_tracker_breakdown` already
   validated each of `BUY_TRACKER_DIMENSIONS` independently (a missing/
   invalid key only zeroes that key), so the new prompt language is
   purely clarifying intent to the LLM and requires no normalizer change
   to stay consistent — verified true by inspection, no drift found.

**Test outcome (exact, run myself):**
- `pytest tests/test_yfinance_technicals_dividend_availability.py
  tests/test_technicals_calculator.py tests/test_buy_tracker_normalization.py
  tests/test_rule_evaluator.py tests/test_yfinance_data_provider.py -q` →
  **276 passed** (12 new tests all pass), 2-3 failures in the same
  pre-existing, unrelated `TestOptionsChainStructure` Greeks/mid-price
  class (confirmed flaky/randomized-fixture, count varies 2-3 across
  3 repeated runs, unrelated to this diff — Rusty touched no options
  code).
- Full suite `pytest tests/ -q` → **1262 passed, 20 failed** (1250 + the
  12 new tests). Diffed the exact 20 failure names against a `git
  stash`-restored pre-fix baseline run on the same tree: **identical
  set, same file (`test_yfinance_data_provider.py`), same full-suite-only
  reproduction pattern** (these tests pass when the file is run alone;
  they only fail when run after the rest of the suite — a pre-existing
  cross-test-file mock-isolation artifact, not a regression from this
  change). Confirmed byte-for-byte pre-existing baseline, not introduced
  by Rusty's fix.

**Verdict: APPROVE.** Both root causes from my REJECT are fixed at the
correct layer (provider-level, not `rule_evaluator.py`/prompt-only),
verified independently against live data with the exact symbols from my
original diagnosis, trimming is provably conservative (never touches a
row with real close data), the date-boundary logic is sound, and the
prompt clarification is consistent with the already-correct normalizer
contract. No regressions; new tests are rigorous and non-fake (real
`YFinanceDataProvider`/`build_buy_tracker_evidence`, only network-facing
`ticker` is stood in).

## G2 review: Linus's Zero-Free Agent-Facing Option Chains (danny-zero-free-agent-option-chains.md)

Read the full 450-line decision doc + Linus's history entry, then reviewed
his actual `git diff` line-by-line against every rule (Z1-Z11), the frozen
`options_chain_view.py` five-function contract, the ownership table (§5),
and backward-compat rules (§7). Scope: `options_math.py`,
`options_chain_merge.py`, new `options_chain_view.py`,
`options_chain_filters.py`, `roll_table.py`, `dps_scorer.py` + all
changed/new tests.

**Diff findings (all 6 src files, no defects):**
- `options_math.py`: new `robust_mid_optional` delegates to unchanged
  `robust_mid`, returns `None` only when neither bid nor ask usable —
  numerically identical on every path that used to return a real price.
- `options_chain_merge.py`: `_recompute_contract` nulls all 5 Greeks
  *together* via one `greeks_valid` gate (never partial), stamps
  `greeks_asof`; raw-layer `is_accepted` gate untouched (provenance intact).
- `options_chain_view.py` (new, frozen contract): pure/total (try/except),
  non-mutating. Idempotence mechanism hand-traced: `contract_view` reuses
  an already-present `_meta.field_status` verbatim on a 2nd pass instead of
  re-deriving from now-nulled values — confirmed stable across 2 passes.
  `greeks_valid`-absence-trusts-raw design choice matches Z-V6's own spec
  and is correctly deferred to Livingston's G3 legacy-shard migration.
- `options_chain_filters.py`: candidate filtering uses `is_candidate_eligible`
  with accurate hidden-count footer in every branch; current-position block
  stays unfiltered (Z10 compliant).
- `roll_table.py`: grid cells null-safe via `usable_quote`/`usable_greek`,
  `color="gray"` on unusable bid/net_credit (Z-R1); intentionally NOT
  filtered by `is_candidate_eligible` (current-contract row, out of scope).
- `dps_scorer.py` (285-line rewrite, both put/call): `_finite_or_none`
  never coerces via `or 0`; every factor/combo gated on `is not None`;
  `risk_zone="UNKNOWN"` when delta missing; `_data_quality_block` forces
  `status="NO_DATA"` on insufficient confidence without ever nulling the
  numeric `score`; put P&L now aligned to `executable_buyback_ask` (matches
  call's pre-existing behavior, Z7). `rg "or 0\b"` sweep across all 6 owned
  files: zero live coercions remain.

**Independent live reproduction (real production code, not mocks):**
1. All-zero provider payload (bid/ask/last/iv=0) → raw layer keeps `bid=0.0`
   faithfully, `mid=None`/`greeks_valid=False` (Z3/Z4) even at the raw
   layer since nothing usable; agent view nulls bid/ask/last/iv/mid/greeks
   to `None` with correct `field_status` per field; `volume`/`openInterest`
   stay integer `0` (Z2 carve-out) at both layers.
2. Recursive walk of a full agent view for any numeric `0`/`0.0` outside
   `volume`/`openInterest`: **zero violations found** (Z-I1).
3. `to_agent_view` applied twice to its own output: **byte-identical**
   (idempotent).
4. One-sided real ask (bid=0 invalid, ask=1.2 valid) → `mid=0.10` at both
   layers — initially looked suspicious, but confirmed this is
   `robust_mid`'s own pre-existing, explicitly-unchanged "bid-less, mark
   conservatively near ask-capped-at-0.10" convention (a real derived value
   from a genuinely valid ask, not a fabricated placeholder) — not a Z3
   violation, matches §7 grandfathering.
5. TV-zero-overlay over a valid yfinance quote → merged contract retains
   yfinance's real bid/ask (1.0/1.2), TV's zeros do not overwrite —
   confirms the already-approved persistent-merge TV invariant survived
   Linus's Z3/Z4 changes to `recompute_derived`.
6. `dps_scorer.score_short_put` direct calls: full data → P&L correctly
   computed off executable ask (Z7); `ask=None` → P&L unavailable, 0 pts,
   `buyback_ask` input stays `None` (no bid/mid fallback); `delta=None` →
   `status=NO_DATA`, `risk_zone=UNKNOWN`, `confidence=insufficient`, Delta
   factor scores exactly 0 pts ("unavailable — not scored", no punishment),
   overall `score` stays a legitimate non-null number (69) built from the
   still-available factors — exact match to Z5/Z9 intent.

**Test outcome (exact, run myself):**
- Targeted (10 files): `pytest tests/test_options_math.py
  tests/test_options_chain_merge.py tests/test_options_chain_view.py
  tests/test_roll_table.py tests/test_dps_insights.py
  tests/test_format_roll_candidates_table.py tests/test_exclude_contract.py
  tests/test_get_contract.py
  tests/test_options_chain_position_and_direction_filters.py
  tests/test_debug_agent_chain_pipeline.py -q` → **645 passed, 2 failed.**
  `test_options_chain_view.py` alone: **59 passed.**
- The 2 failures (`test_debug_agent_chain_pipeline.py::...::test_current_
  contract_surfaces_buyback_cost_despite_delta_filter` and
  `test_format_roll_candidates_table.py::...::test_buyback_cost_surfaces_
  via_current_contract_override`) are hardcoded "17 DTE" wall-clock-relative
  assertions now reading "16 DTE" (sandbox date advanced to 2026-08-19).
  **Confirmed via `git stash` both fail identically on the pre-diff
  baseline** — pre-existing date drift, not introduced by this diff, and
  matches Linus's own self-reported "2 hardcoded-date drift" note.
- Explicitly located and ran the 2 named Livingston/G3-owned tests:
  `test_options_chain_cache.py::TestCarriedForwardContractShape::
  test_carried_contract_keeps_executable_ask_and_gets_fresh_delta` and
  `test_options_chain_persistence_integration.py::
  TestR1DerivedFieldsSurviveMultiplePersistCycles::
  test_mid_and_all_five_greeks_present_after_three_cycles` — **both fail,
  and fail exactly as described**: they assert old numeric Greeks on a
  contract whose `_meta.greeks_valid == False` (iv=0 invalid), i.e. they
  assert pre-Z3/Z4 fabricated-Greeks behavior. Ran the full Livingston
  persistence/cache/store suite (`test_options_chain_cache.py
  test_options_chain_store.py test_options_chain_persistence_integration.py`,
  86 tests): **exactly these 2 fail, 84 pass** — no other collateral
  damage in Livingston's test surface.
- Full suite `pytest tests/ -q`: **24 failed, 1347 passed** (post-diff) vs.
  `git stash`-restored pre-diff baseline: **22 failed, 1260 passed**. Delta
  is exactly `+2 failed` (the 2 expected G3-owned tests above) and `+87
  passed` (new Z1-Z10 tests), with the remaining 22 failures identical in
  both runs (20 pre-existing `test_yfinance_data_provider.py` full-suite-
  only artifacts + the 2 date-drift tests). **No unexplained failures
  anywhere in the corpus.**
- Ownership boundary check: `git diff --stat` on all 6 Livingston-owned
  files (`options_chain_store.py`, `options_chain_cache.py`, `web/app.py`,
  `agent_runner.py`, `yfinance_data_provider.py`, `config.yaml`) is
  **completely empty** — Linus touched none of them.

**Verdict: APPROVE.** Every Z1-Z10 rule is correctly implemented across
all 6 owned files; the frozen `options_chain_view.py` contract is pure,
total, and provably idempotent; raw-layer fidelity (Z2/Z3 raw exception)
and the pre-existing TV-overlay/persistent-merge invariant both survive
unmodified; scoring never rewards or punishes missing inputs and
correctly surfaces `UNKNOWN`/`NO_DATA`/`data_quality`; put buyback is
executable-ask-aligned; roll table nulls instead of fabricating zero;
current-position retention vs. candidate exclusion (Z10) is correct;
compatibility is additive-only (no renames, `robust_mid()` itself
untouched). The only test regressions are the 2 explicitly-expected
Livingston/G3-owned assertions (independently confirmed to fail for
exactly the stated reason) plus 2 pre-existing unrelated date-drift
failures (independently confirmed via `git stash` to predate this diff).
No hidden incompatibilities found. Clear to proceed to G3 (Livingston).

## 2026-08-19 — G4 Blocking Integration Review: Zero-Free Agent-Facing Option Chains (Linus G1 + Livingston G3 combined) — **REJECT**

Read-only cross-layer review against the full accepted decision doc
(`danny-zero-free-agent-option-chains.md`, all sections) and the actual
combined diff. Reviewed in full: `options_chain_store.py` (480-line diff:
`normalize_persisted_v1_to_v2`, retry/backoff singleton, health/repair
support — no defects), `options_chain_cache.py` (`apply_agent_view`,
`get_stale_quote_warn_seconds`, `_compute_chain_quality`, extended
`stats()` — no defects), `web/app.py` (startup probe, new
`/api/health/options-chain`, `apply_agent_view` wired at 3 endpoints — no
defects), `agent_runner.py` (`_format_options_chain`/
`_format_current_contract_chain`/Phase-2 `structured_chain` all correctly
gained `apply_agent_view` — **but see defect below**),
`yfinance_data_provider.py` (schema text — **see defect below**), new
`scripts/repair_options_chain_shards.py` (171 lines, dry-run default,
CAS-conflict handling — no defects).

**DEFECT 1 (high confidence, blocking — Z1/Z-I1 violation):**
`AgentRunner._build_alpha_options_chain()` (`agent_runner.py` ~L1585-1662)
never calls `apply_agent_view`/`to_agent_view` before serializing the raw
chain. Two independent raw-zero leaks confirmed by direct reproduction
and by the new integration test:
  1. The main candidate block: `json.dumps(structured, indent=2)` on line
     ~1635 dumps the raw (delta-filtered-but-unviewed) chain straight into
     `alpha_chain_text`.
  2. The "CURRENT POSITION (buyback-cost reference)" block reads
     `current_contract.get("bid")`/`.get("delta")`/`.get("last")` directly
     off the raw pre-filter contract (~L1642-1650).
  Both feed `alpha_market_data` → `_run_alpha_review(..., market_data=
  alpha_market_data, ...)`, a real, live Alpha-advisor LLM prompt call
  (confirmed at ~L1927-1933, 2918, 3003) — not a debug/internal-only path.
  Reproduced live with a realistic one-sided illiquid quote (bid=0.0,
  lastPrice=0.0, valid ask=1.2/iv=0.30 so it survives
  `filter_options_chain_by_delta`): literal `"bid": 0.0` and
  `"lastPrice": 0.0` appear verbatim in the text actually sent to the LLM.
  This directly violates Rule Z1 and the Z-I1 headline requirement ("no
  numeric zero appears... anywhere in the agent prompt"). Not mentioned
  in Livingston's own history fix inventory — a genuine missed seam, not
  a documented exception. Root cause of the earlier all-zero fixture not
  catching this: an all-signal-absent contract has no valid iv, so
  `filter_options_chain_by_delta` drops it before reaching the vulnerable
  `json.dumps` line — the defect only reproduces with a partially-valid
  quote (valid ask/iv, invalid bid), which is the realistic case.

**DEFECT 2 (lower severity, non-blocking on its own but must accompany
the fix above):** `OPTIONS_CHAIN_SCHEMA_DESCRIPTION` in
`yfinance_data_provider.py` contains a self-contradiction: a pre-existing
sentence (~L81-83, untouched by this diff) says
"greeks_valid: false ... values default to 0 / intrinsic-only in that
case" while Livingston's newly-added text a few lines below (~L96) says
"A numeric 0 will never appear in these fields." Both are sent verbatim
in the same prompt text — a self-contradictory instruction to the agent.

**Independent verification of everything else (all clean, no defects):**
- Derived fields (mid/Greeks) are already nulled at the raw/persisted
  layer via `recompute_derived`'s `greeks_valid` gate — the leak above is
  confined to raw-observed `bid`/`ask`/`lastPrice`/`iv`, not derived
  fields.
- `roll_table.py`/`dps_scorer.py`/`options_chain_filters.py` are
  self-sufficient (call `usable_quote`/`usable_greek`/`contract_view`
  directly) and do not depend on callers pre-applying `apply_agent_view` —
  confirmed no equivalent gap there.
- `robust_mid`/`robust_mid_optional`'s bid-less/ask-capped convention is
  pre-existing, grandfathered, not a Z3 violation.
- Persistence retry/backoff, `_ConstructionOutcome`, health endpoint,
  v1→v2 lazy migration (pure/idempotent/never touches observed fields),
  repair script (CAS, dry-run, idempotent) — all independently verified
  correct, no defects.
- `api_debug_agent_chain` actually sources raw via `provider.fetch_all()`
  rather than the cache (contradicts Livingston's own history wording)
  but is still safe since `apply_agent_view` is applied regardless — a
  documentation-accuracy nit, not a functional defect.

**Test authoring (only file I'm permitted to write, per decision §5
ownership table):** created `backend/tests/test_zero_free_agent_chain.py`
(~640 lines), covering Z-I1 through Z-I7 against real production modules
(`options_chain_merge`, `options_chain_store` with a `FakeContainer`,
`options_chain_cache`, `options_chain_view`, `agent_runner.AgentRunner`,
`dps_scorer`, `roll_table`, `options_chain_filters`) — no mocking of the
merge/store/view seam itself. While building it, found and fixed a bug in
my own zero-detection regex helper (false-positived on legitimate
non-zero decimals like `"iv": 0.3`); fixed by parsing the captured numeric
token with `float()` instead of pattern-matching digits. Also discovered
that a fully-all-zero fixture (bid=ask=iv=0) is correctly rejected in
whole by `options_chain_merge.gate_contract` (needs a valid ask>0 or
valid iv to accept any of the quote group) — this is the existing,
already-approved persistent-merge trust gate correctly distinguishing
"provider omission/no-quote" from "genuine one-sided zero," not a new
bug — so Z-I5's realistic fixture uses a valid-ask/invalid-bid contract
instead, which is also what correctly exercises Defect 1 above.

**Test results:**
- `test_zero_free_agent_chain.py` alone: **13 passed, 2 failed** — the 2
  failures are exactly `test_agent_runner_alpha_options_chain_text_clean`
  and `test_agent_runner_alpha_current_position_reference_block_clean`,
  precisely isolating Defect 1 (confirmed expected/correct to fail until
  Livingston fixes the seam).
- Full G3+G4 focused suite (merge/cache/store/persistence-integration/
  roll_table/format_roll_candidates_table/dps_insights/
  open_call_zero_quote/get_contract/exclude_contract/
  options_chain_position_and_direction_filters/
  debug_agent_chain_pipeline/options_math/options_chain_view/
  repair_options_chain_shards/zero_free_agent_chain): **808 passed, 4
  failed** — 2 pre-existing wall-clock "N DTE" date-drift failures
  (independently reconfirmed: today's date advanced one more day since
  these were last green; not caused by this diff) + the same 2 expected
  Defect-1 failures above.
- Full backend suite `pytest tests/ -q`: **24 failed, 1411 passed**.
  Confirmed via `--ignore=tests/test_zero_free_agent_chain.py`: without my
  new file, **22 failed, 1398 passed** (20 pre-existing order-dependent
  `test_yfinance_data_provider.py` full-suite-only failures — reconfirmed
  isolated run only fails 3 of them — + the 2 date-drift failures);
  adding my file contributes exactly `+2 failed` (Defect 1) and `+13
  passed`, with zero interference/pollution on any other test.

**Verdict: REJECT.** Defect 1 is a real, reachable, high-confidence Z1/
Z-I1 violation: a genuine provider zero (e.g., no-bid illiquid quote)
reaches a live Alpha-advisor LLM prompt completely unfiltered, in two
separate spots inside `_build_alpha_options_chain`. This must be fixed
(apply `apply_agent_view`/`to_agent_view` — or an equivalent per-contract
`contract_view`/`usable_quote` pass — to both the main serialized chain
and the CURRENT POSITION reference block) before G5. Defect 2 (schema
self-contradiction) should be fixed in the same pass since it's in the
same prompt text and cheap to correct (delete/rewrite the pre-existing
"values default to 0" sentence). Everything else in the combined G1+G3
diff — persistence retry/backoff/migration/repair, health endpoint, the
3 other serialization seams, scoring/roll-table/view invariants — passed
rigorous independent verification with no other defects found.

## 2026-08-19 — G4 Re-Review After Rusty's Fix: Zero-Free Agent-Facing Option Chains — **APPROVE**

Read-only re-review of Rusty's fix targeting my prior G4 REJECT (Defects 1
and 2). No production files edited by me.

**Defect 1 fix verified in `agent_runner.py`:** `_build_alpha_options_chain`
now calls `apply_agent_view(structured)` immediately after option-type
resolution — *before* `filter_options_chain_by_type`, before
`current_contract` capture, and before `filter_options_chain_by_delta`.
Both leaks are closed: the main candidate `json.dumps(structured, ...)`
block now serializes the viewed chain, and `current_contract` (captured
from that same already-viewed `structured`) feeds the CURRENT POSITION
reference block, so `current_contract.get("bid")` is now the
view-nulled value, not raw. `executable_buyback_ask(None)` correctly
returns `None` (confirmed in `options_math.py`), so the buyback-cost
reference degrades gracefully when ask is unusable. The same
`apply_agent_view` seam is (unchanged from before) also present in
`_format_options_chain`, `_format_current_contract_chain`, and the Phase-2
`structured_chain` block.

Independently re-reproduced live (not just via my own test) with a
2-contract chain (current position: bid=0.0/ask=1.2 valid/iv=0.30; a
second near-ATM candidate strike: bid=0.0/ask=0.85/iv=0.30, volume=0,
openInterest=0) through the real `merge_sources` → `merge_prior` →
`recompute_derived` → `_build_alpha_options_chain` pipeline: **zero
numeric-zero violations** in the guarded fields (bid/ask/lastPrice/iv/mid
+ 5 Greeks) anywhere in the emitted text; `bid`/`lastPrice` correctly
render `null`; `volume: 0`/`openInterest: 0` correctly preserved as real,
faithful integers (Z2); the CURRENT POSITION block's `bid` is `null`
while its valid `ask`/`delta` pass through untouched.

**Defect 2 fix verified in `yfinance_data_provider.py`:** the old
self-contradicting sentence ("values default to 0 / intrinsic-only ...")
is rewritten to "the Greeks are null (never 0 or an intrinsic-only
substitute); treat them as absent, not as unreliable numbers." Confirmed
via `grep` no remaining "default to 0" text anywhere in the schema
description, and the existing "field_status"/"stale"/"NULL vs ZERO"
sections are unchanged and consistent with it — no self-contradiction, no
duplication.

**Test results:**
- `test_zero_free_agent_chain.py` (my reviewer-owned file, unchanged since
  last review): **15 passed, 0 failed** — both previously-failing Defect-1
  tests (`test_agent_runner_alpha_options_chain_text_clean`,
  `test_agent_runner_alpha_current_position_reference_block_clean`) now
  pass.
- Focused suite (merge/cache/store/persistence-integration/roll_table/
  format_roll_candidates_table/dps_insights/open_call_zero_quote/
  get_contract/exclude_contract/
  options_chain_position_and_direction_filters/
  debug_agent_chain_pipeline/options_math/options_chain_view/
  repair_options_chain_shards/zero_free_agent_chain): **810 passed, 2
  failed** — only the 2 pre-existing wall-clock "N DTE" date-drift
  failures remain (reconfirmed unrelated to this diff).
- Full backend suite `pytest tests/ -q`: **22 failed, 1413 passed** —
  identical to the known pre-existing baseline (20 order-dependent
  `test_yfinance_data_provider.py` full-suite-only failures + the 2
  date-drift failures). **Zero new failures; zero regressions.**

**Verdict: APPROVE.** Both blocking defects from my prior REJECT are
independently confirmed fixed, with no new defects introduced and no
regressions anywhere in the corpus. The `_build_alpha_options_chain` seam
now matches the same `apply_agent_view`-before-filter pattern already
used by the other 3 serialization seams, closing the last unguarded
agent-facing surface. Schema description is now internally consistent
with the null/status contract. Clear to proceed to G5 (Danny).
