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

## 2026-08-19 (later): zero-never-overwrites-prior in the persisted merge (supersedes T7/Z-M4)

- New user directive (`copilot-directive-2026-08-19T17-41-19.md`) explicitly reversed a rule the prior
  Zero-Free decision had reaffirmed as "ruled out to change": `is_accepted("bid"/"lastPrice", 0.0)` and
  `is_accepted("volume"/"openInterest", 0)` being `True`. Root cause of the reported "chains still show
  zero bid" symptom: a contract that *passes* the per-contract trust gate (valid ask/iv present) could
  still have one field (typically bid) glitched to exactly `0` by a provider, and that zero would overwrite
  a genuinely good prior value inside `merge_prior` — a gap `gate_contract`/`gate_bucket` never covered
  (they only protect the whole quote group when there's no valid ask/iv at all).
- Deliberately minimal fix: left `is_accepted`, `gate_contract`, `gate_bucket`, and `merge_sources`
  (Phase 1) completely untouched — a zero is still a well-formed number for a single fresh source cycle.
  The new "no opinion" rule lives entirely inside `merge_prior`'s two field selectors
  (`_select_quote_field`/`_select_observed_field`) via a new `_ZERO_SENSITIVE_FIELDS` set (`bid`,
  `lastPrice`, `volume`, `openInterest` — `ask`/`iv` already required `>0` and needed no change) and an
  `_is_meaningful_value()` helper: an incoming exact zero never overwrites a genuinely valid non-zero
  prior, and if there's no such prior either, it's omitted (`None`) rather than introduced. Verified this
  is fully sufficient by re-reading `options_chain_cache.py`'s `refresh()` — it always routes through
  `merge_prior(prior_chain or {}, live, now=now)`, even cold-start, so `merge_prior` really is the sole
  gate to the accumulated/persisted chain; `options_chain_cache.py`/`tv_options_chain_fetcher.py` needed
  no changes at all.
- Rewrote the two now-inverted tests (`test_yfinance_observed_volume_zero_overwrites_prior_500` →
  `..._never_overwrites_prior_500`; `test_z_m4_live_bid_zero_..._overwrites_and_is_stored_as_zero` →
  `..._never_overwrites_prior`) plus one stale-fill assertion (`test_invalid_live_quote_group_falls_back_
  to_prior_field_by_field`'s volume expectation). Added `TestMergePriorZeroNeverOverwrites` (initial
  zero-only contract introduces no zero fields, mixed snapshot preserves-vs-updates per field, TV positive
  overlay still wins) and `TestMarketClosedMultiExpirationRegression` (multi-expiration/strike/side,
  all-zero Yahoo snapshot → byte-identical accumulated chain; mixed partial-zero snapshot updates only
  the genuinely-changed field, leaves every other contract untouched). Confirmed the existing 300-seed
  `merge_prior` associativity/monotonicity fuzz test still passes unmodified under the new rule (reasoned
  through why field-level associativity holds even with the new "omit if neither side is meaningful"
  branch, then empirically verified all 300 seeds green).
- Ran `test_options_chain_merge.py` (440/440) plus the broader focused suite (options_math, view,
  dps_insights, roll_table, format_roll_candidates_table, get_contract, exclude_contract, position/
  direction filters, debug_agent_chain_pipeline): only the 2 known pre-existing DTE-drift failures
  remain (confirmed present on a clean `git stash`, unrelated to this change).
- Flagged, not fixed (outside charter — Rusty/Livingston/Basher-owned test files, notified via
  sibling message): six tests across `test_options_chain_cache.py`, `test_options_chain_persistence_
  integration.py`, and `test_zero_free_agent_chain.py` assert the exact *superseded* "raw zero survives
  verbatim" behavior (including the prior decision's own Z-I5/G3 anti-corruption headline test) and will
  fail until their owners update them to match the new invariant.
- Recorded the reversal in `.squad/decisions.md` (2026-08-19 entry) since it explicitly contradicts a
  previously-accepted "explicitly ruled out" item and has cross-team test impact.

- Basher's parallel prep independently confirmed the identical root cause (whole-contract gate in
  `_select_quote_field` letting a partial-zero snapshot with valid ask/iv overwrite bid/lastPrice and
  cascade into a wrong `mid`) — no code change needed beyond what's above, since the fix already lives at
  the per-field level, not the whole-contract gate. Added one more explicit regression closing that loop:
  `test_z12_partial_zero_snapshot_does_not_cascade_a_wrong_mid` (merge_prior + recompute_derived together,
  asserts `mid` reflects the preserved real bid, not the spurious incoming zero). Folded two scoping
  clarifications into the decision doc: Z2 (volume/openInterest keep literal `0`) is superseded only for
  what the *persisted merge* accepts from a fresh fetch, not for the agent-view boundary or for values
  already stored; and no migration/backfill is planned for already-clobbered Cosmos values — they self-heal
  only on a future genuine positive quote. `test_options_chain_merge.py`: 441/441 passing.

- **2026-08-20 — theta double-`/365` fix in `greeks_calculator.py`** (focused, single-file task, scope
  strictly `greeks_calculator.py` + its dedicated tests only). Root cause: py_vollib 1.0.1's own `theta()`
  already returns the *daily* per-share value (divides its raw annual formula by 365 internally — confirmed
  by reading its source, not assuming; its own doctest cites Hull Example 17.2). `compute()`'s py_vollib
  branch divided by 365 again, deflating theta ~365x on that path only; the manual/scipy fallback was
  already correct (single division). Fixed by removing the redundant `/365` from one line; left
  delta/gamma/vega/rho and the fallback path untouched. Confirmed via `grep` that no caller anywhere
  compensates with a downstream `*365` — the whole codebase (yfinance docs, agent instructions, scorer,
  filters) already assumed the correct daily convention this bug violated.
  - Added `TestThetaUnitConversionRegression` (11 tests) to `test_greeks_calculator.py`: Hull reference
    values, full-matrix path-equivalence vs. manual fallback, forced-fallback-vs-real-py_vollib check,
    sign/finiteness/expiry-edge sanity, a derived closed-form call-put parity identity, direct raw-vs-
    computed equality, and an explicit other-Greeks-unchanged guard.
  - **Verified effectiveness directly, not just by inspection**: temporarily reverted the fix and reran —
    7/11 new tests failed immediately (the other 4 correctly test magnitude-independent properties). This
    is a good habit worth repeating for future "prove the regression test actually regresses" checks —
    passing tests alone don't prove they'd catch the bug; deliberately reintroducing it does.
  - Regression run (677 tests: greeks_calculator, options_math, dps_insights, roll_table,
    options_chain_merge, options_chain_position_and_direction_filters, options_chain_view,
    zero_free_agent_chain): 0 failures, 0 unexpected — no existing test had hardcoded the old buggy
    magnitude.
  - Disclosed but explicitly did not fix (out of the task's single-conversion scope) two sibling bugs found
    by the same source+numeric-sweep method: `vega` has an analogous double-`/100` division on the
    py_vollib path (~100x deflation); `rho` has a path-inconsistency where the manual fallback is un-scaled
    (~100x larger than the py_vollib path) rather than a double-division. Recorded both in
    `.squad/decisions.md` as a recommended, explicitly scoped follow-up task — did not touch either, per the
    user's "fix exactly one unit conversion" instruction.

- **2026-08-20 — user confirmed volume/OI scope for zero-never-overwrites**: asked explicitly "el 0
  absoluto no debe sobreescribir ningún campo" (absolute zero must never overwrite any field, no
  exceptions). This resolves Basher's twice-flagged, non-blocking caveat about volume/OI staleness masking
  a genuinely fresh 0 — user accepts that tradeoff codebase-wide. No code change needed: the existing
  `_ZERO_SENSITIVE_FIELDS` implementation (bid/lastPrice/volume/openInterest) plus `is_accepted`'s own
  positive-finite rejection of ask/iv zeros already satisfies this exactly. Re-ran
  `test_options_chain_merge.py`: 441/441 green. Recorded as a closing decision entry in
  `.squad/decisions.md` — this item is now closed, not just disclosed.

- **2026-08-29 — Best Options evaluator (`src/best_options.py` + `src/category_params.py` + `filter_options_chain_by_dte`)**,
  implementing Danny's accepted design (`.squad/decisions/inbox/danny-best-options-design.md`). Scope kept
  strictly to the pure domain layer per charter: no API/cache/frontend files touched.
  - `category_params.py` (new): single category normaliser/threshold accessor closing finding F9. Thresholds
    read verbatim from `rule_evaluator.CATEGORY_THRESHOLDS_CC`/`_CSP` — never redefined, cross-checked
    byte-for-byte in tests.
  - `options_chain_filters.py`: added `filter_options_chain_by_dte` — the sole row-inclusion filter for this
    feature (design F1's fix). Whole-expiration-bucket semantics only; never drops an individual contract
    within a kept bucket, unlike the pre-existing `filter_options_chain_by_delta` (explicitly not reused here
    per finding F2).
  - `best_options.py` (new, ~780 lines): `evaluate_best_options(...)` matching the design's frozen section 7
    signature. Two-layer design — three binary safety gates (tradability/delta-band/earnings-span) colour a
    row and null its score on failure but never remove it; a graded 0–100 quality score (annualized return
    0.45 / cushion 0.25 / delta fit 0.20 / liquidity 0.10) with green ≥65 / yellow ≥40 / red otherwise. DTE
    window is the *only* row-inclusion filter (resolved an apparent conflict between the task brief's
    paraphrase and design §4.1's explicit "nothing is ever hidden" language — recorded as an interpretive
    decision for Danny/Basher to confirm). Premium floor is DTE-scaled and graded, never a hard drop. IV Rank
    is never read or enforced anywhere (unobservable from yfinance per `volatility.py`); reported in
    `parameters` for display only. `nearest_miss` always populated via an original 6-tier deterministic
    algorithm (design only gives one worked example). Full parameter provenance block (category
    defaulted-flag, thresholds source, skill references, DTE source, weights, colour thresholds) built from
    the same values the scorer itself uses.
  - **Found and fixed a real bug in my own first pass**: design §4.2's one-line G3 description ("expiration
    falls after a known next earnings date") reads literally as the *pass* condition; my first
    implementation took it that way and was backwards. Caught via cross-check against this codebase's own
    established `src/skills/earnings-gate-sell/SKILL.md` convention (risk = a position remaining open
    *during* earnings) and fixed: G3 now fails when expiration falls after the next earnings date (position
    would span the announcement), passes on/before it. Unknown earnings date remains never a gate failure
    (design F10). Flagged prominently in the decision doc since the design wording invites the opposite,
    wrong reading.
  - Added `tests/test_category_params.py` (33 tests), `tests/test_options_chain_dte_filter.py` (9 tests),
    `tests/test_best_options.py` (25 tests, including a source-grep guard against direct
    `contract.get("bid"/"ask"/...)` reads — Danny's acceptance gate #2 — and a byte-identical-output
    determinism check). All 67 new tests green.
  - Ran the full backend suite (1521 passed) plus targeted reruns of `options_chain_view`, `rule_evaluator`
    (+ rejection regressions), `format_roll_candidates_table`, and
    `options_chain_position_and_direction_filters` — no regressions from the `options_chain_filters.py`
    addition. The 11 failed / 16 errored tests in `test_yfinance_data_provider.py` /
    `test_yfinance_technicals_dividend_availability.py` reproduce identically on a clean `git stash` — confirmed
    pre-existing and unrelated.
  - Confirmed Rusty's already-wired `web/app.py` endpoint call matches this module's frozen signature
    exactly; live-tested a direct call through it.
  - Recorded all interpretive decisions (DTE-only filter, liquidity missing-data handling, `below_category_floor`
    vs. the wait-floor red trigger, the original `nearest_miss` tiering, `dte.source` provenance limitation,
    the earnings-gate direction fix) plus a full list of adversarial test cases for Basher in
    `.squad/decisions/inbox/linus-best-options-scoring.md`.

- **2026-08-29 (same-day correction) — Best Options row inclusion reversed: delta band is a
    row FILTER, not just a colour gate.** My first pass took design §4.1's literal text
    ("nothing inside the [DTE] window is ever hidden") plus §4.2's framing of delta band as
    "Layer A gate G2" at face value and implemented delta band as colour-only — every
    out-of-band contract still showed up in `rows`, red. The product owner reviewed my summary
    and explicitly corrected this: the displayed chain must be filtered by BOTH the DTE window
    and the category delta range, with only contracts surviving both shown as primary rows.
    This matches what the original task brief actually said ("retain every option surviving
    those two user-facing filters") — I had overridden that plain-language instruction in favor
    of an ambiguous design-doc reading, which was the wrong call.
    - Fixed in `best_options.py`: `_evaluate_side` now builds every contract's row as before,
      computes `nearest_miss` over the FULL set (so an out-of-band contract remains describable
      as "closest to qualifying"), then filters to only `delta_band == "pass"` rows for the
      primary `rows`/`total`/`truncated` fields. Added `excluded_by_delta_band` (a count) to
      each side's result for transparency. G1 tradability and G3 earnings span remain colour-only
      gates on in-band rows — only delta band changed from gate to filter.
    - Did **not** reuse `filter_options_chain_by_delta` (design F2's documented anti-pattern:
      wide, non-category-aware, reads `contract.get("delta")` directly) — the new filter reuses
      this module's own accessor-respecting `_gate_delta_band` predicate as both the inclusion
      test and the `nearest_miss` classifier, so there is exactly one place that decision is made.
    - Updated module/function docstrings in `best_options.py` and the `filter_options_chain_by_dte`
      docstring in `options_chain_filters.py` to state the corrected two-filter semantics and
      stop claiming DTE is "the only content filter Best Options applies".
    - Rewrote/added tests in `test_best_options.py` for the new semantics (excluded contract
      absent from `rows` but present in `nearest_miss`/`excluded_by_delta_band`; an in-band and an
      excluded contract coexisting correctly; the placeholder schema for an unrequested side).
      67 -> 69 tests, all green.
    - Full backend suite re-run: 1523 passed; same pre-existing 11 failed / 16 errored
      `test_yfinance_data_provider.py`/`test_yfinance_technicals_dividend_availability.py`
      failures, confirmed via `git stash` to be unrelated.
    - Recorded the full before/after account, including a note that design §4.1/§4.2's own
      wording should get a follow-up edit from Danny so the next reader doesn't reach the same
      wrong conclusion, in `.squad/decisions/inbox/linus-best-options-scoring.md`.

## 2026-08-29 — Force Alpha (`run_trigger`/`force_alpha`) runner/domain execution

- Task: implement explicit `run_trigger` ("scheduled"|"manual") + `force_alpha` pass-through
  for the four applicable paths (covered_call, cash_secured_put, open_call_monitor,
  open_put_monitor), per `.squad/decisions/inbox/danny-force-alpha-design.md` and the
  confirming `.squad/decisions/inbox/copilot-force-alpha-semantics.md`. Scope: runner/domain
  only — no API/frontend/scheduler-queue files.
- **D1/D2 resolution**: Danny's design §12 originally proposed Settings "Run Now" stays
  scheduled/unforced and trigger-all becomes forced. The task prompt and the confirming note
  both say the opposite in both directions (dashboard + Settings Run Now = forced; scheduled +
  trigger-all/"Full analysis" = due-only). Treated the task prompt + confirmation note as the
  final, already-decided answer overriding Danny's original proposal — this is what got
  implemented.
- **`agent_runner.py` changes**: `run_symbol_agent`/`run_position_monitor` and their
  `_run_position_assessment`/`_run_roll_management` helpers gained `run_trigger`/`force_alpha`
  params. Alpha-gating logic: `run_alpha = is_alert or prolonged_wait or force_alpha`;
  `forced = force_alpha and not (is_alert or prolonged_wait)` — i.e. "forced" is only true when
  forcing was the *sole* reason Alpha ran; a review that's independently due and also forced
  still resets the cooldown. Precedence (highest to lowest): buy_tracker skip > (monitor-only)
  `incomplete_quote_wait` > `force_alpha`. `_record_trace` now also persists `run_trigger`/
  `force_alpha` on every trace.
- **`alpha_run` schema** (new Cosmos activity field, written right after the existing
  `alpha_view` write, same call pattern): `{"trigger": run_trigger, "forced": bool,
  "status": "ok"|"failed"|"skipped_agent_type"|"skipped_incomplete_quotes"}`. Written whenever
  Alpha is attempted for any reason, or deliberately skipped specifically because forcing was
  requested but blocked by a higher-precedence rule. Not written on the untouched "supervisor
  alone, nothing forced" path — byte-identical document shape preserved for the common case.
- **H1 fix (mandatory per design §9)**: `_detect_prolonged_wait`'s cooldown scan used to break
  on the first activity carrying `alpha_view`, treating it as "the cooldown was consumed by a
  real review". A forced-but-not-due review also carries `alpha_view`, so without a fix, forcing
  Alpha would silently reset/suppress the due prolonged-WAIT cooldown — the one thing the task
  explicitly required not to happen. Fixed: the scan only breaks when the activity's
  `alpha_run.forced` is not `True`; forced reviews are skipped over (still counted as a plain
  WAIT toward the threshold, just don't reset the cooldown). Legacy activities with no
  `alpha_run` field at all are conservatively treated as **not forced** (still break, preserving
  old behavior for historical documents with no knowledge of this feature).
- **H2 (Telegram blast radius)**: verified by construction, no code change needed — the
  `send_alert`/`send_prolonged_wait_alert` gates remain exactly `if is_alert...`/
  `if prolonged_wait...`; forcing only adds a third OR-branch to "should Alpha run", never to
  "should Telegram fire". `force_alpha=True` alone never sends a Telegram message.
- **`_run_alpha_review` never raises** — confirmed by reading its full body (catch-all at the
  tail always returns `None`). If a gathered Alpha call does raise, it's caught by a separate,
  pre-existing, unrelated `except Exception` in `run_symbol_agent`/`run_position_monitor` that
  writes a second "error" activity — this is old behavior, not something this feature changes;
  it just has to be modeled correctly in tests that exercise a forced-Alpha-raises scenario.
- **`incomplete_quote_wait` derivation quirk** (useful for anyone touching monitor tests): it is
  NOT settable via the fake Phase-1 assessment payload. `run_position_monitor` derives it
  independently from the real market-data executable buyback ask and overwrites any
  Phase-1-supplied `incomplete_data`/`buyback_available` fields via `_apply_buyback_quote_state`.
  To exercise this path in a test, supply genuinely bad ask market data (0.0/negative/missing),
  mirroring `test_open_call_zero_quote.py`'s fixture.
- **Wrapper modules** (`covered_call_agent.py`, `cash_secured_put_agent.py`,
  `open_call_monitor_agent.py`, `open_put_monitor_agent.py`): added `run_trigger`/`force_alpha`
  params with `scheduled`/`False` defaults, forwarded straight through to the runner. `main.py`'s
  `_run_all_agents_async` now passes them explicitly (`run_trigger="scheduled",
  force_alpha=False`) for the four in-scope agents as a regression lock, rather than relying on
  the wrapper defaults; `buy_tracker` is untouched (no Alpha playbook, doesn't accept the kwargs).
- **Explicit scope boundary held**: did NOT touch `scheduler_registry.py` or `web/app.py` myself
  — threading `force_alpha`/`run_trigger` through the "Settings Run Now" scheduler-queue call is
  framework plumbing, out of my charter and this task's own "Own runner/domain execution only"
  instruction. Confirmed after the fact (via `git diff`/`git status`) that another teammate
  (API owner) had concurrently implemented exactly this in `scheduler_registry.py`
  (`TaskRegistry.trigger_task_now`/`_worker_loop` now thread arbitrary `job_kwargs` through to
  `job_func`, filtered by `inspect.signature` so tasks that don't accept the new kwargs are
  unaffected) and `web/app.py` (dashboard/"Run Now" endpoints call `trigger_task_now(...,
  run_trigger="manual", force_alpha=True)`, "Full analysis"/trigger-all stays
  `force_alpha=False`). Verified the kwarg names (`run_trigger`, `force_alpha`) and value
  semantics used there match this runner-level contract exactly — no integration gap.
- **Tests**: new `backend/tests/test_force_alpha_execution.py` (~800 lines, 23 tests) covering
  design §11 cases 1-14 (runner/domain-owned: gate semantics for both entry points, H1
  cooldown-neutrality via direct `_detect_prolonged_wait` unit tests, buy_tracker skip,
  incomplete_quote_wait precedence, Alpha-returns-None and Alpha-raises under forcing, no extra
  Telegram sends from forcing alone) plus additional pass-through coverage for the four thin
  wrapper modules and `main.py`'s `_run_all_agents_async` regression lock (not separately
  numbered in the design's case list, but squarely in this file's ownership). Cases 15-25
  (API-layer semantics: HTTP request/response shapes, concurrency locks, endpoint defaults) and
  26 (frontend/API seam) are explicitly out of scope for this file — Rusty and Livingston's.
  Two debugging notes worth keeping: (1) `get_recent_activities`-style activity lists are
  most-recent-first — hand-built fixtures for `_detect_prolonged_wait` must respect that or the
  cooldown scan is tested backwards; (2) when a gathered Alpha call raises under forcing, expect
  TWO Cosmos activities (the original WAIT decision written before the gather, plus a separate
  "error" activity from the outer catch-all), not one.
- **Test runs**: `test_force_alpha_execution.py` — 23 passed. Combined with
  `test_open_call_zero_quote.py`, `test_buy_tracker_normalization.py`,
  `test_agent_model_settings.py`, `test_zero_free_agent_chain.py`, `test_summary_paused.py` —
  82 passed, 0 regressions.

## 2026-08-29 (follow-up) — binding correction: only dashboard CC/CSP buttons force Alpha

User correction (`.squad/decisions/inbox/copilot-force-alpha-semantics-superseded.md`)
supersedes the D1/D2 resolution above: **only the dashboard CC/CSP buttons** pass
`run_trigger="manual", force_alpha=True`. Settings "Run Now" (single-agent and "Run
Full"/`/api/trigger-all`) and all scheduled executions must stay due-only (`force_alpha=False`),
same as pre-feature behavior.
- Checked this file's runner/domain layer against the correction: no change needed.
  `run_symbol_agent`/`run_position_monitor`/the four wrapper modules only expose the generic
  `run_trigger`/`force_alpha` mechanism and never encode policy about which caller passes what
  value — that policy lives entirely in `web/app.py` (not mine). The H1 cooldown-neutrality fix,
  the `alpha_run` audit schema, and the no-force-only-Telegram guarantee are all caller-agnostic
  and remain correct under the corrected policy.
- Re-ran `backend/tests/test_force_alpha_execution.py` after the correction landed: still 23
  passed, 0 changes needed — none of my tests assert anything about which HTTP endpoint maps to
  which flag value.
- Updated `.squad/decisions/inbox/linus-force-alpha-execution.md` with an explicit correction
  section pointing at the superseding decision, and flagged that `test_force_alpha_plumbing.py`
  and `test_trigger_force_alpha_scoping.py` (both outside my ownership) still assert the old
  policy and need the API owner to update them alongside the `web/app.py` endpoint defaults.

## 2026-08-29 (follow-up 2) — Livingston's reported 4 failures: verified clean on the live tree

Livingston's integration pass flagged 4 failures in `backend/tests/test_force_alpha_execution.py`
(`test_case8_incomplete_quote_wait_force_alpha_true_alpha_skipped`,
`test_case10_alpha_raises_under_forcing_primary_decision_survives`,
`test_case13_due_alpha_review_consumes_cooldown_as_before`,
`test_case14_legacy_alpha_view_without_alpha_run_is_treated_as_not_forced`) after concurrent API
changes landed. Investigated on the live tree, not a cached snapshot:
- Cleared all `__pycache__`/`.pyc` and re-ran fresh: all 4 named tests pass individually
  (`-k "case8 or case10 or case13 or case14"`), the full file passes (23/23), and the combined
  suite with the two API-layer alpha files plus the existing regression set passes (93/93).
  `src/agent_runner.py`'s mtime predates this check and shows no edits since my own last
  verified-green run earlier in this session — no concurrent change landed in my owned file
  that could have reintroduced the bug.
- Conclusion: these 4 failures were real at some earlier point **within this same session**
  (I found and fixed all four myself before ever reporting completion: the
  `_five_wait_activities()` fixture ordering bug affecting case13/case14, the
  `incomplete_quote_wait` derivation quirk needing a real bad-ask market-data fixture for
  case8, and the case10 assertion expecting the wrong activity count). Livingston's
  integration check evidently captured the tree at a point before those fixes landed (or before
  they were confirmed), not a new regression. No code or test change was needed this round —
  reported the clean, reproducible result back rather than re-editing already-correct code.
- Confirmed alongside this that the final policy is all still intact and passing: only
  dashboard CC/CSP buttons force; forced runs don't consume the prolonged-WAIT cooldown; legacy
  missing `alpha_run` metadata stays conservative (not forced); incomplete-quote/buy_tracker
  skips remain safe and recorded; no force-only Telegram send.

## Options Screener aggregation module (backend/src/options_screener.py) — new pure module

Approved directive: `.squad/decisions/inbox/copilot-options-screener-approved.md` (top-level
Screener menu, Options tab aggregating Best Options across all symbols, server-side
filters/sort/pagination, default preference Preferred+Acceptable, no persisted snapshots,
explicit per-symbol warming/error/freshness states, capped cold-chain warming concurrency —
warming/concurrency is the cache/API layer's job, out of my surface). **No separate
"Danny/Linus proposal" design doc exists for this feature** — grepped `.squad/decisions.md`
and every agent `history.md` for "options screener"/"screener"; only unrelated pre-existing
DGI-screener hits. Implemented directly from the approved directive plus the task's own
detailed spec, and recorded that gap plus every non-obvious interpretive call in
`.squad/decisions/inbox/linus-options-screener-design.md` for team visibility, per this
agent's established pattern (same as the force-alpha D1/D2 "task prompt is authoritative"
precedent).

- **Reuse discipline**: `evaluate_options_screener` calls `best_options.evaluate_best_options`
  literally, once per ready symbol/side, always with that module's own default DTE window
  (`DEFAULT_DTE_MIN`/`DEFAULT_DTE_MAX`, currently 0/45) — the screener's own `min_dte`/
  `max_dte`/`min_abs_delta`/`max_abs_delta` are strictly post-filters on rows a symbol's own
  category rules already admitted; they are never passed into `evaluate_best_options` as a
  wider or narrower window, and can only narrow, never widen, what a symbol's own delta band
  already let through. `category_key` and each side's delta-band midpoint are pulled straight
  from that per-symbol result's own `parameters.category.value`/`parameters.thresholds`, not
  re-derived via `resolve_category`/`thresholds_for` — zero second source of truth.
- **Row filters implemented**: preference/label (default `{"Preferred","Acceptable"}`, reusing
  `best_options._COLOR_LABELS`'s vocabulary via each row's own already-computed `label`),
  symbol allowlist (case-insensitive `.strip().upper()`, matching the `dgi_screener.py`/
  `cosmos_db.py` convention), `min_annualized_return_pct`, `min_abs_delta`/`max_abs_delta`,
  `min_dte`/`max_dte`, `min_open_interest`. **Null-metric policy (deliberate, tested
  explicitly)**: a `None` value on a row's filtered metric fails any bound actually set on
  that axis (can't confirm a minimum is met -> excluded) but passes through untouched when no
  filter is set on that axis at all — symmetric across all four numeric filters, matching
  `best_options.py`'s own "absence is not zero" philosophy.
- **`nearest_miss` placement rule**: surfaced per side only for a symbol whose own
  `evaluate_best_options` call admitted **zero** rows (`section["total"] == 0`), tagged with
  that symbol (and its category). A symbol that *did* have admitted rows which the screener's
  own filters subsequently hid entirely is NOT reported via `nearest_miss` — that's "filtered
  to zero, as requested," a different fact from "your category rules found nothing," and
  conflating them would mislead a caller into thinking a widened filter might reveal a
  contract that was never admitted in the first place. Both branches have explicit tests.
- **Sorting**: mirrors `best_options._row_sort_key` exactly (score desc, DTE asc, then
  category-relative delta fit asc — `|abs_delta - midpoint|` using *that row's own symbol's
  own side's own category* delta-band midpoint via an external `symbol -> midpoint` lookup, not
  a field injected onto the row, keeping the row schema identical to `best_options.py`'s own
  contract) plus an explicit total tie-breaker (`symbol`, then `expiration`, then `strike`)
  that `best_options.py`'s own single-symbol sort key didn't need but this multi-symbol
  aggregation does, since Python's stable-sort guarantee alone would otherwise leave ordering
  dependent on `symbol_inputs`' input order. Verified with a same-score/DTE/delta fixture
  across two different symbols and a reversed-input-order fixture (`test_options_screener.py`
  `TestDeterministicOrdering`).
- **Memoization** (opt-in, caller-supplied `dict`, never module-global state — preserves
  purity and testability): key is `(symbol, side, chain["timestamp"], category, total_shares,
  next_earnings_date, ex_dividend_date, support_level)` — **deliberately excludes `now`**. The
  freshness signal is the chain's own `timestamp` (stamped by the cache layer whenever content
  actually changes), not wall-clock call time; the screener's own `generated_at` is always
  freshly stamped from the current call's `now` regardless of memo hit/miss. Accepted, bounded
  edge case documented in the module docstring: a memoized entry's nested `stale_quote` flag
  reflects the `now` in effect the first time that key was computed, not the current request —
  low-value given the 24h staleness threshold, and re-warming stale chains promptly is the
  cache layer's job, not this module's. Verified each key component (chain timestamp, category,
  total_shares, next_earnings_date, ex_dividend_date, support_level) independently busts the
  memo, and that `now` alone does NOT (via a monkeypatched call-counter on
  `evaluate_best_options`), while `generated_at` still changes on a memo hit.
- **Pagination**: independent `offset`/`limit`/`total_matching`/`returned`/`has_more` per side;
  `offset`/`limit` clamped to `>= 0` defensively rather than raising (total-function style,
  matching `best_options.py`'s own posture) — no hard cap on `limit` imposed here, since
  reasonable page-size ceilings are the caller/API layer's concern, not a pure domain
  constraint; `best_options.py`'s own `_MAX_ROWS_PER_SIDE = 400` already bounds how many rows
  a single symbol can ever contribute in the first place.
- **Symbol status handling** (total by construction): `"ready"`/`"warming"`/`"error"` are the
  only recognised statuses; anything else — or a `"ready"` entry with no usable `chain` dict —
  is downgraded to `"error"` with a synthesised message rather than raising or silently
  vanishing. `"warming"`/`"error"` symbols are recorded in the top-level `symbols` summary and
  skipped entirely for row computation (no `evaluate_best_options` call made for them).
- **Calls/Puts separation**: rows from the two sides are never merged; `side="call"`/`"put"`
  makes the unrequested side a cheap empty placeholder (mirroring
  `evaluate_best_options`'s own side-skipping), `side="both"` (default) populates both
  independently with their own filters/sort/pagination/`nearest_miss`.
- **Tests** (`tests/test_options_screener.py`, new file, 26 tests): filter intersection
  (combined filters only narrow, never widen; global delta window can't reach past a symbol's
  own category band), deterministic ordering (default sort, input-order independence, explicit
  tie-breaker), null-metric behavior (`open_interest`/`annualized_return_pct` both tested
  present-filter-set-excludes vs. filter-unset-passes-through), memoization (hit/miss call
  counting via monkeypatch, each key component's invalidation, `now`-does-not-invalidate),
  pagination (slicing, `has_more`, offset-beyond-total, negative-input clamping), nearest_miss
  placement (zero-row vs. filtered-to-zero), symbol status handling (warming/error/bogus
  status/missing chain), side handling (call-only placeholder, both-sides independence), and
  byte-stable results (`json.dumps(..., sort_keys=True)` equality across repeated calls).
- **Test runs**: `test_options_screener.py` alone — 32 passed. Combined with
  `test_best_options.py` — 59 passed. Full targeted sweep (`test_best_options.py` +
  `test_best_options_adversarial.py` + `test_category_params.py` +
  `test_options_screener.py`) — 180 passed, 0 regressions.
