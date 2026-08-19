# Linus — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Role:** Quantitative strategy, prompt, provider, and financial-contract owner
- **Stack:** Python, Microsoft Agent Framework, Azure/Gemini providers, yfinance,
  TradingView, Alpha Vantage, React

## Core Context

- Maintains strategy instruction parity across Massive, TradingView, Alpha
  Vantage, and yfinance while adapting only provider-specific data gathering.
- Prompt contracts must use deterministic evidence paths, explicit missing-data
  semantics, stable JSON output, and strategy-valid decisions.
- Major strategy work includes earnings gates, 21–35 DTE roll targets with a
  45 DTE cap, premium-first roll policy, near-ATM hysteresis, contrarian quality
  auditing, DGI screening, and Buy Tracker DGI alignment.
- Major data work includes provider migration, options-chain schema/filtering,
  last-known-good quote preservation, market-hours probing, dividend evidence,
  and position snapshots.
- Major UI work includes roll tables, activity/DPS chat prompts, timeline
  charts, settings, suitability display, and options-chain context.

## Durable Decisions and Patterns

- Earnings gates are mandatory and symmetric where applicable. Post-earnings
  0–7 days is blocked; 8–13 days is cautionary.
- Roll candidates prioritize annualized return while respecting target DTE,
  expiration, held-contract exclusion, and premium/quote verification.
- Position monitors use hysteresis near ATM to avoid flip-flopping on marginal
  price crossings.
- Alert/activity lookups must identify the event by a field unique to that
  event; generic fields create cooldown and history bugs.
- Third-party endpoint interception needs broad matching, field aliases,
  diagnostics, and graceful fallback because provider schemas drift.
- Background failures must retain tracebacks; silent container or persistence
  skips are data-loss risks.
- Percentage storage/display contracts must be explicit. Apply field-specific
  formatting before generic string conversion.

## Recent Learnings

### 2026-08-17 — Buy Tracker Prompt and Provider Contract
- Centralized the five-dimension DGI rules so Buy Tracker prompt surfaces cannot
  drift: 0–2 WAIT, 3–4 BUY, and gated 5/5 promotion.
- RSI is excluded from Value, permissive MA-summary scoring was removed from
  Trend, and earnings belongs only to Calendar.
- Production evidence uses provider `Buy` signals for `MACD.macd` and `Stoch.K`,
  plus positive annual DPS, latest DPS, and dividend-growth years.
- Payout eligibility is the exact finite `<=75%` rule. Missing required proxy
  evidence fails promotion closed; missing explicit cut state alone does not.

### 2026-08-17 — Open Call Executable Ask Safety
- Buyback P&L, profit CLOSE, and roll economics require a numeric, finite,
  positive current ask.
- Bid, midpoint, last/model price, and ask=0 are not executable substitutes.
  Bid=0 with ask>0 remains valid and P&L is ask-based.
- Incomplete quotes degrade to WAIT/incomplete data unless an independent risk
  path supports CLOSE; unavailable economics stay null and are disclosed.

### 2026-08-09 — Options Chain Last-Known-Good Cache
- Refresh merges fresh fields with prior valid contract fields instead of
  replacing the entire cache with provider zeros.
- Quote/Greek zeros may fall back to prior valid values; naturally changing
  fields such as volume and open interest remain fresh.
- Cache TTL controls staleness, not availability. Stale data is served while a
  deduplicated refresh runs; truly expired contract buckets are pruned.

### 2026-08-08 — Suitability Semantics
- Symbols-page suitability is deterministic Entry + Momentum classification,
  independent of watchlist membership and option-chain delta filters.
- Oversold and overextended modifiers route to Ideal Puts/Calls; No Puts/Calls
  require unmodified bearish/bullish momentum.

### 2026-08-18 — Debug Agent-Chain Pipeline: Current-Contract-Before-Delta-Filter
- Root cause of "current contract not in chain data" for a real, cached
  MSFT $525 call / 2026-09-04 position was NOT cache staleness or a
  TradingView overwrite — that additive merge pipeline was already correct.
- `filter_options_chain_by_delta` correctly drops candidates outside the
  standard band, but yfinance can return degenerate near-zero IV for a
  contract when bid/ask are both zero (market closed), which computes a
  ~0.0 delta for the position's OWN held contract and silently removes it.
- The 2026-07-09 "capture current contract before delta filter" pattern
  (built for `agent_runner.py`'s production roll pipeline) had never been
  propagated to the `/api/debug/agent-chain` endpoint or to the shared
  `format_roll_candidates_table()` helper itself — that endpoint derived
  buyback cost from an already delta-filtered chain.
- Fix: `format_roll_candidates_table` now accepts an optional
  `current_contract` override; both the debug endpoint and the production
  call site pass a reference captured from the RAW chain. A genuinely zero
  ask still correctly reports incomplete — the fix only stops losing a
  valid one.
- Pattern to reuse: any new consumer of the options-chain pipeline must
  mirror the production capture point for "current contract," never derive
  it from a chain that has already passed through delta/direction filters.
- Basher independently reproduced and rejected the pre-fix behavior
  (synthetic-ask proof ruling out the live illiquid-neighborhood confound),
  confirming this exact root cause/fix, and flagged a coverage gap: zero
  direct unit tests existed for `filter_options_chain_for_position` or
  `filter_options_chain_by_roll_direction`. Added
  `test_options_chain_position_and_direction_filters.py` (16 tests) to
  close it. Also confirmed via full-suite before/after runs that a
  pre-existing test-isolation issue in `test_yfinance_data_provider.py`
  (20 failures full-suite vs. 1 in isolation) is unrelated to this change.

### 2026-08-18 — Persistent Option Chain: Pure Merge Semantics (Danny's design)
- Implemented Danny's frozen seven-function interface as a new,
  dependency-free `backend/src/options_chain_merge.py`: `is_accepted`,
  `gate_contract`, `gate_bucket`, `merge_sources`, `merge_prior`,
  `recompute_derived`, `prune_by_expiration`. Absence is `None`/no-opinion
  throughout; zero is a valid observation for bid/last/volume/OI but never
  for ask/iv (must be finite and `>0`); derived fields (mid/greeks) are
  never merged/carried, only ever recomputed fresh from current primitives.
- Key interpretive call: the "trust gate" (bid/lastPrice must clear a
  quote-group sanity threshold before being trusted) applies to the WHOLE
  quote group (bid, ask, iv, lastPrice, lastTradeDate) per Danny's §2.4
  intro paragraph, not just the two fields his per-field table calls out —
  harmless for ask/iv since their own per-field acceptance already implies
  the gate passes. volume/openInterest/inTheMoney/contractSymbol are NEVER
  gated (independent observations per §2.4, verified with an explicit
  Yahoo-all-zero-bucket test that these still pass through).
- Fixed the two upstream TradingView normalizer bugs Danny flagged, at
  their actual origin (`tv_options_chain_fetcher.py`, not
  `options_chain_cache.py`): G2 — stopped fabricating
  `volume:0, openInterest:0, lastPrice:0.0, inTheMoney:False,
  contractSymbol:""` placeholders (now omitted so the merge gate can't
  mistake "TV didn't observe this" for "TV observed zero" and clobber a
  valid prior); G5 — malformed/unparseable expiration values are now
  rejected at ingestion (`continue`) instead of falling back to a junk
  `str(raw_exp)` key that could never merge with a real chain.
- `GreeksCalculator` lazily fetches `^TNX` via yfinance unless
  `risk_free_rate` is passed explicitly at construction — hardcoded the
  existing 0.045 fallback via a module-level singleton so
  `recompute_derived` stays genuinely pure/network-free. Reused
  `options_math.robust_mid` unchanged (bid-less-but-positive-ask caps mid
  at `min(ask, 0.10)`, not `ask/2` — mattered for test expectations).
- Scope boundary: Danny's doc nominally assigns Linus the inline yfinance
  normalizer in `OptionsChainCache._process_option_df`
  (`options_chain_cache.py`), but my task authorization explicitly excluded
  that file (persistence/threading, Rusty's charter). Followed the
  narrower restriction; recorded as a decision. Rusty was independently,
  concurrently rewriting that same file during this session (observed
  live — briefly non-importable mid-edit), confirming this was the correct
  boundary to hold.
- Added 117 tests in `test_options_chain_merge.py` (T1-T12 from Danny's
  doc plus every explicitly requested scenario: Yahoo all-zero bucket,
  bid-less contract with positive ask, TV partial overlay, stale prior
  fill, no-input-mutation across all four impure-looking functions,
  malformed expiration, expiration pruning, monotonicity, schema
  compatibility) and 11 tests in new
  `test_tv_options_chain_fetcher_normalize.py` (direct fetcher-level Rule
  S1/S3 coverage — placeholder fields absent not zero, unparseable
  expirations dropped, real chains unaffected). All pass; also re-ran
  `test_debug_agent_chain_pipeline.py` to confirm the already-applied
  current-contract-before-delta-filter fix is untouched and still green.

### 2026-08-18 — Basher review follow-up: fuzz testing found a real gate/associativity subtlety
- A naive property/fuzz test for `merge_prior` monotonicity (T12), built by
  feeding directly-fabricated random dicts as "live" payloads, found ~28%
  of seeds violated `merge(merge(P,L1),L2) == merge(P, merge(L1,L2))`.
  Root cause was the TEST, not the implementation: `merge_prior`'s "prior"
  side is deliberately never re-gated (that's what lets a carried-forward
  contract skip re-proving itself), so using a raw, never-vetted dict as
  the "prior" half of `merge(L1, L2)` lets an internally-inconsistent quote
  group leak through — a shape `merge_sources` (the only real producer of
  a "live" payload) can never actually generate, because it always ties a
  quote-group field's presence to that same source's own gate having
  passed. Regenerating `L1`/`L2` via real `merge_sources(random_yf,
  random_tv)` calls made the property hold cleanly across 500 seeds.
  **Lesson: when fuzz-testing a function whose contract depends on an
  upstream invariant, generate inputs through the real upstream producer,
  not by sampling the target function's own field space directly** —
  otherwise the fuzzer finds "bugs" that are actually just unreachable
  input shapes, and burns review time chasing them.
- Basher's "malformed YYYYMMDD calendar dates" prompt caught a genuine gap
  I'd missed: `tv_options_chain_fetcher.py`'s own Rule S3 check only
  validated the numeric *magnitude* of a YYYYMMDD-shaped expiration
  (`> 19000000`), not that the digits formed a real calendar date — a
  month-13 or Feb-30 value would slip past the fetcher and only get caught
  later by `options_chain_merge`'s `strptime`-backed check. Fixed by adding
  the same `strptime` validation at the fetcher's own ingestion point, so
  it's genuinely the primary enforcement point I'd claimed it was, not just
  nominally so. **Lesson: "defense in depth" claims need to be verified
  field-by-field at each claimed layer, not asserted from the existence of
  a downstream check that happens to catch the same class of bug.**
- Added 21 new tests (TV-single-quote-field overlay, expanded
  calendar-invalid YYYYMMDD matrix at both the merge and fetcher layers,
  300-seed realistic fuzz test, carried-forward-contract downstream
  `delta`/`executable_buyback_ask` consumption) plus a decision-log entry
  documenting the T12 scope refinement. Also confirmed several of Basher's
  requested edges (exact 3-contract degenerate-bucket boundary, mixed
  2-failing+1-passing bucket) were already covered from the first pass.
  Declined to action persistence/scheduler-layer items (hydration
  singleton divergence, ETag 409/412 retry exhaustion, `schema_version`
  migration, `refresh_all` watchdog) — outside charter, flagged for Rusty.

## Provider and Prompt Guardrails
- Keep strategy logic and output schemas provider-independent.
- Never infer positive evidence from prose or missing fields.
- Use canonical raw paths and validate finite numeric values.
- Preserve explicit risk precedence over favorable scoring.
- Document provider limitations instead of fabricating unavailable metrics.

## G1 — Zero-Free Agent-Facing Option Chains (Z1-Z10)
- Implemented Danny's accepted `danny-zero-free-agent-option-chains.md`
  design under exclusive ownership of `options_math.py`,
  `options_chain_merge.py` (Z3/Z4 only), **new** `options_chain_view.py`
  (the five frozen accessor/view functions), `options_chain_filters.py`
  (Z10), `roll_table.py` (Z1), and `dps_scorer.py` (Z5-Z9). No mutation of
  raw-layer semantics from the prior persistent-chain-merge task.
- `robust_mid_optional` delegates to the unchanged `robust_mid` whenever a
  side is usable, returning `None` only on the "nothing usable" path —
  byte-identical numerics everywhere except the fabricated-0.0 fallback.
- `_recompute_contract`/`recompute_derived` now null all five Greeks
  together (never partially) when `greeks_valid` is False, and stamp
  `_meta.greeks_asof` only when Greeks were actually recomputed that cycle
  — mirrors `quote_asof`'s provenance model instead of inventing a new one.
- **Idempotence bug + fix in `options_chain_view.py`**: a first pass nulls
  a genuine `bid=0.0` and an absent `bid` to the same `None`, so a second
  pass can no longer tell `no_market` from `unavailable` by re-deriving
  from the (now ambiguous) value alone. Fixed by having `contract_view`
  reuse an existing `_meta.field_status` verbatim whenever the input has
  already been through this boundary, rather than re-deriving it. **Lesson:
  any idempotent normalization that also *narrows* information (multiple
  raw states collapsing to one view state) must persist its own
  classification decision as data, not attempt to reconstruct it from the
  now-lossy output on a later pass.**
- **Deliberate interpretation, not a deviation — `greeks_valid` binding
  rule**: read literally, "an explicit `greeks_valid == False` nulls the
  Greeks" (Z4) is narrower than "only `greeks_valid is True` counts as
  valid." Chose the literal/narrower reading (`is False` blocks;
  absence trusts the raw numeric value) after the strict reading broke
  pre-existing hand-built test fixtures across the codebase that never
  modeled `_meta` at all — those aren't the contamination Z4 targets, and
  punishing them would have meant editing tests outside this task's
  charter. Documented in the module docstring itself so the choice is
  discoverable without archaeology.
- **Additive, not a deviation — `is_candidate_eligible`'s
  `min_open_interest` kwarg**: the decision doc's own open-question #1
  explicitly deferred this exact parameter to Linus at G1 with a
  documented default of `> 0`; adding it as a keyword-only default-1 param
  is executing an invited decision, not diverging from the frozen
  signature.
- Rewrote `score_short_put`/`score_short_call` end-to-end: null-safe
  extraction (`_finite_or_none`, never `or 0`), `risk_zone == "UNKNOWN"`
  when delta is absent, every scoring factor and combo-modifier skipped
  (0 points, explicit "unavailable — not scored" reason, tracked in
  `missing_fields`) rather than silently reading a missing input as its
  worst/best-case numeric extreme, put's P&L aligned to call's
  `executable_buyback_ask`-only rule (no more raw-`mid` fallback), and an
  additive `data_quality` block (Z9) that forces `status = "NO_DATA"` when
  `delta` or `iv` is missing without ever nulling the numeric `score` the
  UI depends on.
- Confirmed via a git-HEAD-vs-working-tree A/B harness (exec the
  pre-session `dps_scorer.py` from `git show HEAD:...` as an isolated
  module, run both against identical inputs) that the happy-path score and
  full `score_breakdown` are byte-identical to pre-Z1-Z10 behavior once
  the fixture is chosen so `mid` and `executable_buyback_ask(ask)` agree
  (isolating the golden-regression check from the *intentional* Z7 put
  P&L divergence) — locked in as `TestZS5HappyPathGoldenRegression`.
- Flagged, not fixed (outside charter — Rusty/Livingston-owned test
  files): `test_options_chain_cache.py::TestCarriedForwardContractShape::
  test_carried_contract_keeps_executable_ask_and_gets_fresh_delta` and
  `test_options_chain_persistence_integration.py::
  TestR1DerivedFieldsSurviveMultiplePersistCycles::
  test_mid_and_all_five_greeks_present_after_three_cycles` both assert the
  *old* behavior Z3/Z4 was written to eliminate (numeric Greeks fabricated
  even when the test's own fixture sets `iv=0.0`, i.e. `greeks_valid ==
  False`) — need a one-line "contaminated-by-zero expectation, corrected
  by Z3/Z4" update from their owner, per the decision's regression-baseline
  rule.
- Added 200+ new/updated assertions: `test_options_math.py`
  (`robust_mid_optional`), `test_options_chain_merge.py` (Z-M1-M4), **new**
  `test_options_chain_view.py` (Z-V1-V6 plus direct accessor/eligibility
  coverage, 59 tests), `test_roll_table.py` (Z-R1), `test_dps_insights.py`
  (Z-S1-S5, direct `score_short_put`/`score_short_call` unit tests),
  `test_format_roll_candidates_table.py` (Z-F1/Z-F2). Full targeted +
  whole-suite runs confirm exactly the pre-existing 22 unrelated failures
  (20 yfinance network/env, 2 hardcoded-date drift) plus the 2 flagged
  Rusty/Livingston failures above — nothing else regressed.
