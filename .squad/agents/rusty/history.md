# Rusty — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Role:** Backend, runner, scheduler, persistence, and frontend integration owner
- **Stack:** Python, Microsoft Agent Framework, CosmosDB, yfinance/TradingView,
  FastAPI/BFF, React

## Core Context

- Built the CosmosDB service layer, scheduler/task registry, dashboard APIs,
  symbol/position workflows, settings persistence, chat endpoints, and agent
  runner integration.
- Data-provider architecture prefetches and normalizes overview, technical,
  forecast, dividend, and options-chain data before agent execution.
- Scheduler work uses non-blocking queued jobs, overlap guards, per-symbol and
  worker timeouts, dynamic configuration, and persisted task state.
- Unified activity/alert records use `is_alert`; all downstream consumers should
  share one normalized activity object.
- Settings use CosmosDB as authoritative when configured, with ETag
  read-merge-replace, conflict retry, read-back verification, and scheduler
  reload only after durable success. YAML is authoritative only without Cosmos.
- UI/backend work includes symbol watchlists, pause-until-earnings, financial
  editing, roll tables, options-chain caching, provider/model settings, and
  portfolio chat context.

## Durable Implementation Patterns

- Normalize at read/input boundaries; malformed, non-finite, or unverified data
  remains unavailable.
- Reassert protected fields after dict spreading to avoid caller overwrite.
- Use lazy initialization/imports for expensive or provider-specific resources.
- Keep position monitors active when following-agent watchlists are paused.
- Preserve source intent: automated from-activity values and manual values have
  distinct contracts.
- Long-running scheduler jobs must not block heartbeat or next-run advancement.
- When Cosmos is configured, persistence failure is an error, never silent YAML
  fallback.

## Recent Learnings

### 2026-08-19 — Test update: "Zero-never-overwrites-prior" invariant (merge_prior reversal)
- Not my code change: Linus implemented the fix entirely inside
  `options_chain_merge.py`'s `merge_prior` selectors (`_select_quote_field`/
  `_select_observed_field`, new `_ZERO_SENSITIVE_FIELDS = ("bid",
  "lastPrice", "volume", "openInterest")` + `_is_meaningful_value()`) per
  a new user directive (`copilot-directive-2026-08-19T17-41-19.md`) that
  explicitly reversed the prior Zero-Free decision's "ruled out to
  change" stance on `is_accepted("bid", 0.0)`. New invariant: an incoming
  exact zero for those 4 fields during accumulation is never a meaningful
  update — it never overwrites a genuinely valid non-zero prior, and with
  no valid prior either the field is *omitted* (key absent) rather than
  stored as literal `0.0`/`0`. `ask`/`iv` unchanged (already `>0`-only).
  `is_accepted`/`gate_contract`/`gate_bucket`/`merge_sources` untouched;
  `options_chain_cache.py` needed zero changes (confirmed `refresh()`'s
  `merge_prior(prior_chain or {}, live, now=now)` call is the sole gate).
- My part: this broke 3 tests I own in `test_options_chain_cache.py`
  whose names/assertions encoded the now-superseded rule. Updated all
  three to assert the new behavior and renamed to stop describing the old
  rule: `test_yfinance_zero_beyond_tv_coverage_no_prior_data` →
  `..._omitted_no_prior_data` (far-expiration yfinance zeros beyond TV
  coverage, no prior, now assert `.get("bid") is None` instead of
  `== 0.0`); `test_first_fetch_zeros_preserved_as_is` →
  `test_first_fetch_zero_bid_omitted_no_prior` (bid now omitted like ask
  always was — "absence is not zero" now applies uniformly);
  `test_volume_and_open_interest_not_preserved_when_zero` →
  `..._zero_never_overwrites_valid_prior` (inverted: volume/openInterest
  now stay pinned at the valid prior, 500/1000, when the fresh fetch
  returns zero — the literal opposite of the old assertion). Added one
  new companion test, `test_volume_and_open_interest_zero_omitted_no_prior`,
  for the no-prior counterpart (not previously covered at the cache
  integration level; unit-level exhaustive coverage lives in Linus's own
  `test_options_chain_merge.py::TestMergePriorZeroNeverOverwrites`).
- Explicitly left alone (not my authorized artifacts): Basher's
  `test_zero_free_agent_chain.py` (`TestZI1.../test_to_agent_view_recursive_walk_clean`,
  `TestZI5.../test_persisted_bid_zero_survives_hydrate_untouched_but_view_nulls_it`
  — still red, reviewer-owned, I may not modify per the earlier G3
  revision instruction). Livingston had already fixed his own
  `test_options_chain_persistence_integration.py::TestG3RawZeroSurvivesWhileAgentViewIsNull`
  by the time I re-ran the suite — confirmed green, untouched by me.
- Validated: `test_options_chain_cache.py` 48/48 (was 44/47 w/ 3 known
  failures). Combined `test_options_chain_cache.py` +
  `test_options_chain_store.py` + `test_options_chain_merge.py`: 554/554.
  Full backend suite: confirmed via `git stash`/`git stash pop` A-B
  comparison that the ~20 `test_yfinance_data_provider.py` failures plus
  2 unrelated failures in `test_debug_agent_chain_pipeline.py`/
  `test_format_roll_candidates_table.py` seen under a full-suite run
  (`pytest tests/`) are **pre-existing test-order pollution**, present
  identically with my change stashed out — not caused by this edit, and
  outside my authorized artifacts to fix. `py_compile` clean. No
  decision file needed — implementing an already-fully-documented
  directive (`.squad/decisions.md` 2026-08-19 entry), no new ambiguity
  encountered.

### 2026-08-19 — Frontend null-safety sweep (backend zero-free G5 follow-up)
- Separate, frontend-only task: with backend now legitimately returning
  `null` for bid/ask/lastPrice/iv/mid/delta/gamma/theta/vega/rho (never a
  fabricated 0), audited every component reachable from
  `symbols/[symbol]/options-chain/page.tsx` and `symbols/[symbol]/page.tsx`
  for numeric assumptions on those fields (`toFixed`, arithmetic, `Intl`/
  formatter coercion-to-0).
- Findings: `options-chain/page.tsx` + `types/options-chain.ts` were
  already fully null-safe (`fmtPrice`/`fmtGreek`/`fmtIV` helpers, `number |
  null` types) — no changes needed. `RollTableView`/`fmt2`/`numOrNull` in
  `PositionDetail.tsx` were also already null-safe in *value* handling but
  the `RollCell`/`RollTable` TS interfaces still declared `bid?: number`
  etc. (optional, not nullable) — a type-accuracy gap, not a runtime bug.
  `lib/format.ts`'s `usd`/`pct` DO coerce null→0, but traced every call
  site reachable from these two pages and none apply them to a bid/ask/
  greek field (only portfolio-exposure totals already gated by an outer
  `!= null` check) — left untouched, out of blast-radius for this task.
  No action button in either page's component tree is gated on a live
  executable quote (roll/close/buyback-edit are all manual-entry forms
  independent of chain data) — nothing to disable.
- Real fix, `PositionDetail.tsx` only: the `DpsAnalysis` "Input parameters"
  panel rendered `inp.delta`/`gamma`/`theta` raw (blank, not "N/A", when
  null) and `inp.iv` could render a bare stray "%" with no number when
  null — replaced with a `fmtNullable()` helper → explicit "N/A". Widened
  `DpsResult.inputs` to `Record<string, number | string | null>` and
  `RollCell`/`RollRow`/`RollTable` (`bid`/`ask`/`delta`/`net_credit`/
  `strike`/`pct_captured`/`buyback_cost`/`buyback_per_share`/
  `premium_received`) to explicitly allow `null` (was `?number`, i.e.
  `number | undefined` — a real type-vs-runtime mismatch once the backend
  starts sending JSON `null` instead of omitting the key), which required
  widening `appliedPct`/`moneyness`/`eqStrike`'s parameter types to match
  (caught by `tsc`, not guessed). Added a new type-accurate `data_quality?:
  {missing_fields, confidence, quote_asof, stale}` field to `DpsResult`
  (Rule Z9's additive confidence block, previously undeclared/unused) and
  a small gray badge in the DPS Analysis header surfacing
  "partial"/"insufficient" confidence + missing fields — reuses the
  existing risk_zone-badge visual pattern, only renders when confidence
  isn't "full". `STATUS_COLORS`/`RISK_COLORS`'s existing `?? "#8d969e"`
  fallback already renders "NO_DATA"/"UNKNOWN" in gray with zero code
  changes needed.
- Validated: `tsc --noEmit` clean project-wide. `eslint` clean on
  `PositionDetail.tsx`, `options-chain/page.tsx`, `symbols/[symbol]/
  page.tsx`, `types/options-chain.ts`, `types/symbol-detail.ts` — one
  pre-existing, unrelated `react-hooks/set-state-in-effect` finding in
  `options-chain/page.tsx`'s data-fetch `useEffect` (calling `setLoading`/
  `setError` synchronously at the top of the effect body), confirmed via
  `git status`/`git log` to predate this session (file has zero
  uncommitted diff — last touched by an unrelated earlier commit) — left
  untouched as out-of-scope for a null-safety sweep. No frontend test
  runner exists in this repo (still true, re-confirmed) — nothing to run.
  Watchlist-deletion files (`SymbolsTable.tsx`, `api/symbols/[symbol]/
  route.ts`) confirmed untouched/still present via `git status`.

### 2026-08-19 — G3 revision (zero-free agent option chains): `_build_alpha_options_chain` raw-zero leak
- Reassigned to me as independent revision owner after reviewer REJECTed
  Livingston's G3 seam and locked him out of this artifact. Strict scope:
  `agent_runner.py`'s `_build_alpha_options_chain()` + `yfinance_data_provider.py`'s
  `OPTIONS_CHAIN_SCHEMA_DESCRIPTION` text only; `test_zero_free_agent_chain.py`
  is Basher's reviewer-owned acceptance test, read-only.
- Root cause (confirmed by Basher's Z-I1 tests, `test_agent_runner_alpha_options_chain_text_clean`
  / `test_agent_runner_alpha_current_position_reference_block_clean`): unlike
  its siblings `_format_options_chain`/`_format_current_contract_chain`
  (which already correctly called `options_chain_cache.apply_agent_view()`
  before filtering/serializing — that part of Livingston's G3 work was
  sound and untouched), `_build_alpha_options_chain()` filtered/serialized
  the **raw** chain straight into `json.dumps(structured)` for the
  Alpha-advisor LLM prompt, and separately read `current_contract.get("bid")`
  /`.get("ask")`/`.get("delta")` off the same raw (pre-view) contract for the
  "CURRENT POSITION" reference block — two independent raw-zero leaks into
  a live agent-facing surface, exactly as reported.
- Fix: one `structured = apply_agent_view(structured)` call inserted right
  after the option_type branch, before `filter_options_chain_by_type` — the
  same frozen `to_agent_view` boundary (via the existing `apply_agent_view`
  helper in `options_chain_cache.py`) the sibling functions already use.
  Since `current_contract` is captured (via `get_contract`) from this same
  now-normalized `structured` later in the function, both leak paths are
  closed by a single call — no changes needed to the ref_block construction
  itself (`executable_buyback_ask`, `contract.get("delta")` etc. now
  naturally read already-nulled values). `filter_options_chain_by_type`/
  `filter_options_chain_by_delta`/`get_contract`/`exclude_contract` are all
  purely structural (key/shape lookups, or already `delta is not None`
  guarded) — confirmed safe to run on a view-normalized chain, no special-
  casing required.
- `yfinance_data_provider.py`: found and fixed the actual contradiction —
  the `_meta.greeks_valid` doc bullet said computed-but-invalid Greeks
  "default to 0 / intrinsic-only," directly contradicting the adjacent
  "NULL vs ZERO" section's "a numeric 0 will never appear in these fields."
  Reworded to state Greeks are `null` (never 0/intrinsic) when invalid,
  consistent with Z3/Z4. No other contradictory sentences found elsewhere
  in the schema text.
- `apply_agent_view`/`contract_view` naming: task phrasing said
  "apply_agent_view/contract_view boundary"; design doc draft names the
  chain-level function `to_agent_view`. Checked actual source: both exist
  exactly as expected — `options_chain_cache.apply_agent_view()` (a thin,
  already-implemented config-wiring wrapper around
  `options_chain_view.to_agent_view()`) and `options_chain_view.contract_view()`
  are both real, frozen, already-authored functions I only needed to call,
  not define. No naming ambiguity in practice — no decision file needed.
- Validated: Basher's `test_zero_free_agent_chain.py` (15/15 passed, incl.
  both Z-I1 tests targeting this exact function), `test_open_call_zero_quote.py`
  (15/15), focused chain suite (`test_zero_free_agent_chain.py` +
  `test_open_call_zero_quote.py` + `test_options_chain_view.py` +
  `test_options_chain_merge.py` + `test_options_math.py` + `test_roll_table.py`
  + `test_options_chain_position_and_direction_filters.py` +
  `test_yfinance_data_provider.py` = 648 total, 645 passed / 3 pre-existing
  failures confirmed via `git stash` to predate this change — Linus's already-
  landed `_process_option_df` mid/greeks-removal made 3 assertions in
  `TestOptionsChainStructure` stale; out of my ownership/scope, not touched).
  `py_compile` clean on both files.

### 2026-08-19 — Watchlist "Delete Symbol" action
- New task, unrelated to option-chain work. Added a trash-icon delete
  action as the last cell in every `SymbolsTable` row, gated by a mandatory
  `window.confirm` naming the symbol (existing app convention, mirrored
  from `ActivityActions`/`PositionDetail`/`AgentLogsView`) — no new confirm-
  modal component introduced, kept consistent with the rest of the app.
- Key discovery: `DELETE /api/symbols/{symbol}` and
  `cosmos_db.delete_symbol()` **already existed**, fully implemented —
  deletes the `symbol_config` doc (which also holds embedded `positions`)
  plus every other doc in that symbol's partition (`activity`, `alert`,
  `report`, `technical_analysis`, `agent_trace`, `price_forecast`,
  `position_snapshot`, `action_plan`, `enrichment_history`, `action_plan`,
  etc. — anything with `doc_type != "symbol_config"`). This full-cascade
  behavior is the accepted, already-shipped "delete a symbol" product
  semantics referenced by the charter ("existing product semantics
  explicitly define that"), so `backend/web/app.py`/`cosmos_db.py` were
  **not touched** — only a `DELETE` proxy was added to the existing
  `app/api/symbols/[symbol]/route.ts` BFF route, mirroring the identical
  pattern already used for `positions/[positionId]/route.ts`.
- Delete failures surface via `toast.error` (sonner, already globally
  mounted in `layout.tsx`) — no optimistic hide before the request
  succeeds. On success the row is hidden immediately via local
  `removedSymbols` state (avoids a flash of stale data before
  `router.refresh()` lands) *and* `router.refresh()` reconciles from the
  server. Caught my own edge case before shipping: `removedSymbols` is
  local component state that survives `router.refresh()` (no remount), so
  re-adding the exact same ticker later without a full page reload would
  stay incorrectly hidden — fixed by pruning entries no longer present once
  a fresh `rows` prop arrives, using React's documented render-time
  "adjust state when a prop changes" pattern (compare-in-render), not a
  `useEffect`+`setState` (which this repo's eslint config explicitly flags
  as an error: `react-hooks/set-state-in-effect`).
- Delete button's `<td>` stops click propagation (matches the existing
  shares-edit cell) so it never triggers the row's `onClick`→
  `SymbolInfoModal` navigation; button has `aria-label`/`title` naming the
  symbol and is `disabled` while its own delete request is pending.
- Added 5 new backend tests (`TestDeleteSymbol` in
  `test_watchlist_symbols.py`, extending the existing `FakeCosmos` with a
  `delete_symbol` method) locking in the pre-existing endpoint's contract
  now that UI depends on it: happy path, case-insensitivity, 404 on unknown
  symbol (delete never called), 503 when Cosmos unavailable (no false
  success), and no cross-symbol bleed. 54/54 passed in that file. No
  frontend test runner exists in this repo (checked — no jest/vitest/
  playwright config anywhere), so frontend validation was ESLint +
  `tsc --noEmit`, both clean, consistent with prior frontend-change
  validation practice.
- **Danny quality follow-up (same day):** (1) aria-label/title now read
  "Delete {symbol} and all its data" (not "…from the watchlist" — avoids
  implying a lesser-scoped operation). (2) Confirm text now explicitly
  lists positions, activity history, plans, forecasts, and analysis. (3)
  Replaced the single `deletingSymbol: string | null` slot with a
  `deletingSymbols: Set<string>` — the old single-slot design meant
  starting a second symbol's delete while a first was still in flight
  silently re-enabled the first row's button (state moved to the new
  symbol), allowing a duplicate DELETE for it; the Set tracks each
  in-flight symbol independently, plus a defensive re-entrancy guard at
  the top of `deleteSymbol` itself. (4) `test_delete_symbol_when_cosmos_
  unavailable_returns_503` now uses `monkeypatch.setattr(app.state, ...,
  raising=False)` instead of a bare, permanent `app.state.cosmos = None`
  assignment — auto-restored after the test, verified order-independent by
  running it deliberately first. Re-ran ESLint/tsc (clean) and the full
  `test_watchlist_symbols.py` (54/54, including the reordered check).

### 2026-08-18 — Buy Tracker "Score 0/5, canonical fields unavailable" bug
- Unrelated new task (prior option-chain lockout explicitly lifted). Traced
  the full reported symptom end-to-end: schema/prompt
  (`buy_tracker_instructions.py`) → JSON extraction
  (`agent_runner._try_extract_json`) → breakdown validation
  (`rule_evaluator._validate_buy_tracker_breakdown`) → persistence
  (`cosmos_db.write_activity`) → canonical evidence mapping
  (`rule_evaluator.build_buy_tracker_evidence`). All confirmed correct and
  untouched — the strict breakdown type-checking (reject bool/string, only
  exact 0.0/1.0) is intentional and already tested; persistence does a full
  dict spread with no key stripping; evidence field mappings already matched
  `technicals_calculator`'s real output shape.
- Root cause was upstream, in `yfinance_data_provider.py`, reproduced live
  against real KO data: (1) `ticker.history(period="1y")` can return a
  trailing row (today's session) with `Close=NaN`; rolling-window indicators
  (`SMA*`, `Stoch.K`) correctly propagate that NaN into their last value
  (key omitted per "absence is not zero"), while recursive indicators
  (`EMA*`, `RSI`, `MACD`) silently forward-fill through it instead — an
  inconsistent mix that looked like SMA50/SMA200/Stochastic were simply
  "unavailable." (2) `_build_dividends`'s growth-streak loop compared the
  *current, still-in-progress* calendar year's partial dividend total
  against the prior complete year — always looks like a cut, breaking the
  streak at 0 and omitting `continuous_dividend_growth` for virtually every
  evaluation performed before year-end.
- Fixed at the data boundary (`yfinance_data_provider.py`), not in the
  shared `technicals_calculator.py` (garbage-in-garbage-out fix, keeps the
  shared calculator untouched for all other agent types): new
  `_drop_incomplete_trailing_bars()` trims trailing NaN-close rows before
  any indicator is computed (interior gaps untouched); `_build_dividends`
  now excludes the still-forming current year from the streak comparison
  (a genuine cut in a *completed* year still correctly breaks the streak).
  Added a defense-in-depth prompt clarification in
  `buy_tracker_instructions.py`: `score_breakdown` must always be a real
  5-key object; missing data for one dimension only zeroes that dimension,
  never the whole object.
- New `test_yfinance_technicals_dividend_availability.py` (11 tests, all
  deterministic/offline, no network) locks in both fixes plus a genuine-cut
  regression and an end-to-end `build_buy_tracker_evidence` integration
  check. Full relevant suite (`test_rule_evaluator` +
  `test_buy_tracker_normalization` + `test_agent_model_settings` + new
  file): 234/234 passed. Relevant offline subset of
  `test_yfinance_data_provider.py` (Technicals/Dividends/Overview): 5/5
  passed, no regressions.
- Decision recorded in
  `.squad/decisions/inbox/rusty-buy-tracker-canonical-availability-fix.md`.

### 2026-08-18 — Basher independent cross-check confirms the fix
- Basher independently reproduced the same two root causes (trailing NaN
  Close breaking rolling SMA/Stoch vs. surviving EWM indicators; partial
  current-year dividend bucket zeroing the growth streak) and supplied
  live expected values (KO≈23, JNJ≈63, PG≈22). Re-ran the already-applied
  fix live against AAPL/MSFT/KO/JNJ/PG: SMA50/SMA200/Stoch.K present for
  all five, `continuous_dividend_growth` == 23/63/22 for KO/JNJ/PG — exact
  match, no code change required. Added one more synthetic 63-year-streak
  deterministic test mirroring the JNJ magnitude for extra confidence.
  Basher continues investigating `score_breakdown` normalization
  separately — my fix explains the missing-evidence half, not necessarily
  the missing-score_breakdown half, which was already flagged as an open
  question in my decision doc.
- Linus's `options_chain_merge.py` is done/frozen (449 tests). He confirmed
  `options_chain_cache.py` is outside his authorized artifacts and asked me
  to personally mirror, on the yfinance side, the same normalizer fix he'd
  already applied to `tv_options_chain_fetcher.py` for TV.
- Rewrote `_process_option_df`: dropped the `current_price`/`T`/
  `greeks_calc` params entirely; stopped fabricating placeholder defaults
  for `bid`/`ask`/`iv`/`lastPrice`/`lastTradeDate`/`volume`/`openInterest`/
  `inTheMoney`/`contractSymbol` — a missing/NaN yfinance value is now
  omitted from the dict (never defaulted to 0/0.0/False/""); a real
  individually-valid zero (e.g. `bid=0.0`, `volume=0`) is still written
  through unchanged. Removed all `mid`/greeks computation from this
  function — that's now `recompute_derived`'s sole job, already wired into
  `_refresh_locked` from my earlier persistence work. No `_meta` written
  here either (unchanged: only `merge_prior`/`recompute_derived` write it).
  Removed now-dead `_get_greeks()`/`self._greeks` and the now-unused
  `robust_mid` import as a consequence.
- Verified with a throwaway script (deleted after use, never committed)
  exercising the real `_fetch_yfinance`/`_process_option_df` path against a
  mocked yfinance DataFrame with a fully-NaN row, a fully-observed row, and
  a real-zero row — confirmed NaN fields are omitted, zeros are written
  through, and no mid/greeks/`_meta` appear at this stage. Re-ran the full
  focused suite (merge + store + cache + filters + roll table): **581
  passed**, unaffected — none of my own tests assert on this function's raw
  pre-`recompute_derived` output shape (all monkeypatch `_fetch_yfinance`
  wholesale), so this was a pure, test-safe internal cleanup.
- Decision recorded in `.squad/decisions/inbox/rusty-persistent-option-chain-store-impl.md` §8.

### 2026-08-18 — Persistent Option Chain: Basher Review Hidden-Edge Follow-up
- Added 11 more deterministic tests (store + cache) for edges Basher flagged
  before gating the diff: a cold-singleton `--web-only` replica sharing a
  store with the combined scheduler+API instance (not just "a restart");
  Cosmos CAS retry parametrized over both 409 and 412 (store already treated
  them identically, now locked in by test); `schema_version`-absent legacy
  shard hydration; a fixed-seed 40-cycle property/fuzz test proving real
  `merge_prior` never loses a contract and never regresses `quote_asof`;
  `gate_bucket`'s exact 3-contract-all-failing vs 2-failing+1-passing
  boundary; TV supplying only IV or only ask (field-level, not per-source,
  precedence); and a carried-forward contract's exact handoff shape to
  `options_math.executable_buyback_ask`.
- Corrected two of my own assumptions while writing these: (1) Danny's G5
  ("unparseable expiration keys are immortal") is actually **self-healing**
  — `merge_prior` has its own defense-in-depth rejection of malformed keys,
  identical to `merge_sources`, so a legacy junk key from a hydrated prior
  chain is silently dropped on the very next refresh, not immortal.
  (2) `_meta.carried` means "absent from live this cycle", not "degenerate
  this cycle" — a contract a source DID report, just with junk bid/ask/iv,
  is `carried=False` even though its fields are effectively carried from
  prior. Both corrections recorded in my inbox file so no one repeats them.
- Full focused suite (merge + store + cache + filters + roll table):
  581 passed.

### 2026-08-18 — Persistent Option Chain: Persistence/Lifecycle/Concurrency
- New `options_chain_store.py` (Cosmos-backed, one shard per symbol+expiration,
  ETag/CAS retry re-applying `merge_prior`, graceful no-Cosmos/local
  degradation, grace-period pruning after real expiration) plugs into
  `options_chain_cache.py` behind a per-symbol `threading.RLock` covering the
  full hydrate → fetch → merge → persist cycle.
- Coded strictly against Linus's frozen `options_chain_merge.py` seven-function
  interface; never redefined trust-gate/merge semantics. His four merge
  functions only ever return `{symbol, timestamp, calls, puts}` — any other
  top-level key (e.g. `underlying_price`, needed for greek recompute) must be
  re-added by the caller after each call, not assumed to survive.
- `threading.RLock` reentrancy is per-OS-thread, not per-coroutine: concurrent
  `asyncio.create_task()`s on one event loop share a thread and would NOT
  serialize on the same lock. This matches Danny's explicit spec choice; real
  "no lost update" safety only holds across genuinely different OS threads
  (`refresh_all`'s thread pool, or a web request thread vs. the scheduler
  thread) — tests for this must use real `threading.Thread`s, not
  `asyncio.gather()`, to exercise true contention.
- Persistence failures are always non-fatal and logged only; in-memory
  assignment happens before the persist attempt so a Cosmos outage can never
  roll back a good in-memory result. TTL controls refresh timing only —
  contract retention/deletion is governed solely by real expiration date
  (America/New_York) plus an accepted grace period.
- `test_yfinance_data_provider.py` (unowned by me) has a pre-existing gap: its
  `@patch("...yf")` doesn't cover `options_chain_cache._fetch_yfinance`'s own
  local `import yfinance`, so its options-chain assertions exercise real
  network data. That, plus Linus's now-wired "absence is not zero" contract
  semantics, surfaces field-presence assumptions in that file that need an
  update by whoever owns it — flagged in my inbox decision, not fixed by me.

### 2026-08-17 — Buy Tracker Canonical Normalization
- Adapt raw provider output into a fixed ephemeral evidence object. Only the
  five binary score dimensions are accepted; score is always recomputed.
- Apply exceptional promotion and hard-WAIT predicates before alerting,
  evaluation, persistence, summaries, tracing, and notification.
- Exact canonical risk flags are conservative fallbacks only when raw evidence
  is unavailable. Raw safe evidence overrides stale flags; prose cannot create
  positive evidence.
- Provider prompt examples and evidence paths are shared and production-shaped.

### 2026-08-17 — OpenCallMonitor Zero-Quote Safety
- Added a shared positive-finite executable-ask contract for short-call P&L,
  buyback, roll tables, candidate tables, and DPS economics.
- Invalid asks yield null economics, skip profit-only Phase 2, and persist
  deterministic non-alert WAIT without prolonged-WAIT notifications.
- Independent risk rationale remains enforceable; valid positive asks preserve
  CLOSE and ROLL behavior.

### 2026-08-10 — AI Provider Cosmos Persistence
- Settings mutations read the authoritative document, merge only intended
  fields, conditionally replace by ETag, retry conflicts, and verify read-back.
- Configured Cosmos unavailability returns failure; unrelated document fields
  are preserved and live scheduler state updates only from verified data.

### 2026-08-08 — Watchlist and Position Financial Integration
- Symbol creation and inline shares editing validate normalized inputs and keep
  forecast backfill failure isolated from durable creation.
- Position premium and buyback updates use distinct routes and strict numeric
  validation.
- Suitability categories are owned by deterministic Entry + Momentum semantics,
  independent of watchlist flags and option-chain filters.

## Validation Practice
- Run targeted pytest suites, Python compilation, focused frontend lint/type
  checks, and scoped diffs.
- Preserve unrelated baseline provider failures in reports.
- Verify runner ordering and object identity at downstream boundaries.

## Alpha Fallback Recommendation in Dashboard FOLLOWING Tables (2026-08-21)

**Task:** Implement alpha fallback in `_build_dashboard_tables` for
`covered_call` / `cash_secured_put` rows; expose result in frontend.

**Key patterns:**
- `_is_complete_triplet(strike, expiration, premium)` — module-level helper
  before `_build_dashboard_tables`; float-casts with fallback to 0, asserts
  strike > 0, non-empty expiration, premium > 0.
- Alpha fallback activates only when: (1) main triplet is incomplete AND (2)
  `alpha_view.opportunity_strength in ("MODERATE", "STRONG")` AND (3) alpha
  `alternative` triplet is complete. Never partial — whole triplet from one
  source.
- `recommendation_source: "alpha" | "agent"` added to the row dict (backend)
  and to `AgentRow` type (frontend). String enum preferred over boolean for
  extensibility.
- Gap/strike_pct computed from the displayed strike (which may be alpha-
  sourced) — consistently uses `main_strike` variable after potential
  substitution.
- Frontend `Rec.` column added to FOLLOWING tables only (the `!isPM && !isBuy`
  branch). Renders `[SELL][ALPHA]` badges only when `recommendation_source ===
  "alpha"`. Main agent's `RecentCell` (WAIT) stays untouched.
- `buy_tracker` and position monitor branches have no `recommendation_source`
  field — confirmed by test.
- Pre-existing flaky test (`test_yfinance_data_provider`) fails when run with
  the full suite due to asyncio event loop interaction; passes in isolation;
  unrelated to this work.
