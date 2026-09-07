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

### 2026-09-07 — Symbols Overview: Authoritative Screener-Eligibility Booleans
- `_compute_symbols_overview` in `backend/web/app.py` previously emitted no
  `us_options_eligible` or `screener_eligible` field on any row, causing
  the Options Screener dropdown to run a client-side reimplementation that
  was both fail-open on MIC and divergent from the `is_watchlist_member`
  predicate.
- **Fix pattern:** compute `screener_universe` once before the row loop by
  calling `compute_options_screener_universe(symbols, portfolio_shares_by_ticker)`
  — the canonical single-source-of-truth from `options_screener_universe.py`.
  Per-row: `screener_eligible = sym in screener_universe` (set membership,
  zero logic duplication). `us_options_eligible` uses `_resolve_screener_mic`
  + `_is_us_options_eligible` (also re-exported from the shared modules).
- **Both booleans always present** on every row (non-US, zero-share,
  watchlist-only all get `False`/correct values) — no undefined in API contract.
- **Set membership is the pattern** for per-row predicates computed from a
  universe function: call the universe function once, then `sym in universe`
  per row. Avoids N calls to a function designed for batch iteration.
- 66/66 relevant backend tests pass (`test_symbol_configuration_section.py` +
  `test_options_screener_universe.py`). Pre-existing failure in
  `test_options_chain_persistence_integration.py` is unrelated.

### 2026-09-06 — Portfolio Summary: CMP Cost Basis (Danny's Contract)
- Replaced the ambiguous `purchases − sale_proceeds` formula for `current_invested_eur`
  with true remaining cost basis: **pool_cost residual** from the CMP algorithm.
- **CMP state per security:** `pool_shares` (shares with known cost) + `pool_cost` +
  `unpaid_shares` (INCOMPLETE BUY). BUY COMPLETE: pool grows. SELL ACCIONES: removes
  proportional cost (`sold_qty × pool_cost/pool_shares`). SELL DERECHOS: zero pool impact.
  TRANSFER_IN: adds carried cost to pool. TRANSFER_OUT: removes proportional cost.
- **New canonical fields** (both per-holding and summary): `total_purchase_outflow_eur`,
  `cost_basis_sold_eur`, `remaining_cost_basis_eur`, `total_sale_proceeds_eur`,
  `rights_proceeds_eur`, `realized_result_eur`, `has_incomplete_cost_basis`.
- **Backward-compat aliases** maintained with identical values: `total_purchases_eur`,
  `total_sales_eur`, `total_invested_eur`, `current_invested_eur` (now ≠ old formula).
- **Breaking change**: `current_invested_eur` changed from `purchases − sale_proceeds`
  (could go negative on profit) to `remaining_cost_basis_eur` (always ≥ 0).
  `avg_cost_basis_eur` now uses `pool_cost / pool_shares` (CMP-adjusted, stays constant
  through sells due to math), not `total_buy_cost / paid_buy_shares`.
- **Chronological sort** of movements (`trade_date` ASC, then `id` ASC) is mandatory
  before the CMP loop — without it, avg-cost at each sell is undefined.
- **3 existing tests** updated for new semantics: `test_purchases_and_sales` (665→707),
  `test_sales_exceed_purchases` (−300→0), `test_multi_security_aggregation` (721→824.10).
- **18 new `TestCMPAcceptance` tests** added (S1–S13, S15 + presence/flag tests). All 58
  holdings tests + 157 portfolio suite + 242 full portfolio pass.
- Formulas verified for S4: 60×(2000/150) = 800 exactly (no rounding error in Decimal).
- CMP avg is mathematically invariant through sells: `(cost−sold_qty×avg)/(shares−sold_qty)
  = avg`. The pre-existing test `test_avg_cost_basis_independent_of_sells` passes unchanged.
- Transfer global-cancel invariant: holds only when both TRANSFER_OUT and TRANSFER_IN carry
  matching `transfer_cost_basis_eur`. Unit tests that omit this field test share counts only.

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

### 2026-08-29 — Best Options scheduled precompute cache (Linus ownership slice)
- Implemented `backend/src/best_options_cache.py` (§13 of Danny's design) as a pure,
  thread-safe in-memory cache for precomputed Best Options envelopes with no Cosmos, no
  FastAPI, no scheduler imports, and no asyncio.Task objects stored. Cache key is the
  normalized symbol alone (one canonical `side="both"` envelope serves both Symbol Detail and
  Screener). Entry shape: `{symbol, status, envelope, generation, computed_at, chain_timestamp,
  chain_stale_at_compute, inputs, error, reason, refreshing, refresh_started_at,
  refresh_completed_at, refresh_error, chain_refresh_error}`. Snapshot shape: `{generation,
  entries, cycle_started_at, cycle_finished_at, cycle_duration_seconds, trigger, truncated,
  counts}`.
- **Atomic copy-on-write publish**: `publish_snapshot()` replaces the entire snapshot in a
  single guarded assignment (full-cycle path). `replace_symbol()` builds a new snapshot with
  `{**old.entries, symbol: new_entry}`, preserving every other entry's object identity and
  never advancing `generation` (single-symbol Refresh path, §9b of design). Readers take the
  snapshot reference exactly once per request and read everything from that one object —
  atomicity is what prevents mid-iteration generation jumps.
- **Carry-forward on failure** (§3 of design): a symbol that fails during cycle N+1 retains
  its cycle-N entry with `status` downgraded to `"stale"`, its original `generation` and
  `computed_at` intact, and `error`/`reason` populated. A transient provider hiccup never
  blanks a working page. A symbol that has never succeeded is `status="error"` (or `"warming"`
  when the cause is a not-yet-fetched chain) with `envelope=None`.
- **Module singleton** (mirrors `options_chain_cache.py`'s pattern verbatim):
  `get_best_options_cache()` / `set_best_options_cache(cache_or_None)`. Single module-level
  `threading.Lock` guards singleton access; each cache instance has its own `threading.RLock`
  guarding the `_snapshot` attribute. Test-only reset hook via `set_best_options_cache(None)`.
- **Immutability contract** (enforced via documented discipline, not runtime checks): all
  published envelopes, entries, and snapshots are read-only after publication. No handler may
  write into them (§7 of design). Per-request metadata is attached by constructing a new outer
  dict `{**envelope, "cache": {...}}`, never `envelope["cache"] = ...`. Per-row enrichment
  requires a shallow copy first. No deep copy anywhere (at 400 rows × 2 sides × N symbols it
  would cost more than the evaluation this design exists to eliminate).
- **Surgical options_screener.py update**: added `precomputed` parameter to
  `evaluate_options_screener()` and `_evaluate_symbol()`. When a `precomputed` envelope is
  present for a symbol, it is returned directly and `evaluate_best_options` is never called.
  A `status="ready"` entry with neither a `precomputed` envelope nor a usable `chain` is
  downgraded to `"error"` with a synthesised message — reusing the module's existing
  "ready but no usable chain" downgrade idiom rather than inventing a second failure
  convention. The direct-`chain` path stays for the module's own tests and independent reuse.
- **Tests**: `backend/tests/test_best_options_cache.py` (new, 30 tests): module singleton
  (get/set/reset), initial state (empty, generation 0), publish_snapshot (atomic replacement,
  generation advance, is_empty), replace_symbol (single-symbol update, object identity
  preservation, generation unchanged, trigger, counts recomputation, normalization), get_entry
  (present/absent, normalization), thread safety (snapshot read concurrent with publish,
  replace_symbol concurrent with reads, module singleton get), copy-on-write semantics (no
  mutation of old snapshot entries, no mutation of caller-supplied snapshot), status counts
  (reflect entry statuses, replace_symbol updates correctly), immutability contract (published
  entry not modified, snapshot entries map not mutated), carry-forward scenarios (stale entry
  preserves generation and computed_at). Added `TestPrecomputedParameter` class to
  `backend/tests/test_options_screener.py` (7 new tests): precomputed envelope used directly
  when present (call-counter via monkeypatch proves evaluate_best_options never called),
  byte-for-byte envelope returned, ready-without-precomputed-or-chain downgrades to error,
  ready-with-precomputed-but-no-chain succeeds, ready-with-chain-but-no-precomputed computes
  live, precomputed normalizes symbol keys (entry symbol normalized to match precomputed map
  keys), partial coverage only affects covered symbols. All existing screener tests (32 tests)
  pass unchanged — the `precomputed` parameter is strictly additive and backward-compatible.
- **Test runs**: `test_best_options_cache.py` — 30 passed. `test_options_screener.py` — 39
  passed (32 existing + 7 new). Combined — 69 passed, 0 regressions.
- **Pattern to reuse**: when adding a cache with atomic copy-on-write semantics, always
  provide both a full-cycle `publish_snapshot()` primitive (one atomic swap) and a
  single-entry `replace_symbol()` primitive (builds new snapshot with `{**old.entries, sym:
  new_entry}`). The latter's generation-unchanged behavior is load-bearing for distinguishing
  "completed full cycle" from "targeted refresh." Thread-safe read access is a single lock
  grab to copy a reference, never held across I/O or evaluation. Immutability is cheaper than
  deep copying when the data structure is large; enforce it via code review and grep gates,
  not runtime guards.

## 2026-08-29T18:27:00Z — Best Options Cache Implementation (Ownership Slice 1)

**Task:** Pure in-memory cache module + surgical options_screener update

**Scope:** Implement section 13 of Danny's accepted design — thread-safe in-memory cache for shared Best Options envelope

**Result:** ✅ COMPLETE — 69 tests passing (30 cache unit + 7 screener integration + 32 pre-existing), zero regressions

**Implementation:**
- `best_options_cache.py` (NEW): Entry/Snapshot shapes, atomic COW publish, module singleton, RLock per instance, zero external dependencies
- `options_screener.py` (SURGICAL): Added `precomputed` parameter, returns cached envelope directly when present
- Test coverage: Thread safety, COW semantics, immutability contract, carry-forward, symbol normalization

**Key design decisions:**
- Immutability enforced via discipline (not runtime guards) — documented, code-reviewed
- Per-symbol OS locks (non-reentrant) for scheduler thread blocking
- Zero Cosmos/FastAPI/asyncio coupling

**Handoff to Livingston:** Cache module ready for integration into precompute cycle; screener type changes ready for Rusty's frontend


## 2026-08-31 — Best Options Validation Audit (Context Parity Finding)

**Context:** Best Option contract validation runs Primary→Supervisor→Alpha pipeline but feeds minimal contract-only snapshot instead of full multi-page market data block that normal Following provides

**Owner:** Linus — Code inspection, audit findings

**Work Items:**

### Audit: Context Coverage Gap
- **Scope:** Line-by-line comparison of validation snapshot vs normal Following market data block
- **Finding:** Validation missing 6 canonical data elements:
  - OVERVIEW page (earnings, fundamentals)
  - TECHNICALS page (indicators, S/R)
  - FORECAST page (analyst consensus)
  - DIVIDENDS page (full history, ex-dates)
  - ENRICHMENT section (tech-timing, momentum, DGI)
  - VOLATILITY section (IV/HV, premium richness)
- **Impact:** Ex-dividend date present in Cosmos calendar was missed because agents only saw bare ISO date, not full dividend schedule
- **Root cause:** Validation never calls `fetch_all` or `_build_market_data_block` — uses only chain + contract snapshot
- **Recommendation:** Reuse existing `fetch_all` and `_build_market_data_block` — one canonical path, no parallel implementation

**Interdependencies:**
- Informs Danny's full-context-parity design
- Enables Livingston's initial implementation (later rejected for other reasons)
- Foundational for Rusty's calendar extractor revision

---

## 2026-08-31: Alpha Recommendation Badge Display — Frontend Fix

**Issue:** Dashboard `RecentCell` component showed primary WAIT activity instead of Alpha SELL recommendation when `recommendation_source = "alpha"`.

**Root Cause:** UI only rendered ALPHA badge when most recent primary activity was SELL. When primary activity was WAIT but Alpha provided SELL recommendation, ALPHA badge was suppressed.

**Solution:** Modified `RecentCell` component in `frontend/src/components/DashboardAgentTables.tsx` to check `recommendationSource === "alpha"`. When true, render hardcoded SELL + ALPHA badges instead of mapping over primary activity list.

**Implementation Details:**
- Component: `RecentCell` in `DashboardAgentTables.tsx`
- Logic: Check `recommendationSource === "alpha"` → render SELL + ALPHA
- Fallback: Primary-sourced recommendations render normal activity list
- Preservation: Click navigation and table styling unchanged

**Validation:**
- ✅ TypeScript: `npm run build` (noEmit) — clean compilation
- ✅ ESLint: Targeted checks passed
- ✅ Backend Tests: All 19 contract tests passed (`backend/tests/test_dashboard_alpha_fallback.py`)

**Impact:**
- Users now see correct Alpha recommendations (SELL + ALPHA) in dashboard
- No backend logic changes
- No performance impact
- No breaking changes

**Key Learning:** UI components must respect backend recommendation source flag as a data contract. Alpha fallback is not just a styling hint; it represents a backend decision that should drive UI rendering. The backend already correctly sets `recommendation_source = "alpha"` via `_build_dashboard_tables`; frontend just needed to honor that contract.

**Related Files:**
- Backend: `backend/web/app.py` (`_build_dashboard_tables`) — sets recommendation_source
- Tests: `backend/tests/test_dashboard_alpha_fallback.py` — validates backend contract
- Types: `frontend/src/types/dashboard.ts` — defines recommendationSource field
- Decision: Documented in `.squad/decisions/decisions.md` (merged from inbox)

---

## Learnings

### Buy Tracker Six-State Redesign (buy-tracker-state-redesign, 2025-07)

**Task:** Implement Danny's redesign of Buy Tracker from 3-state binary (BUY/STRONG_BUY/WAIT with 0/1 dimensions) to 6-state tri-state (AVOID/UNFAVORABLE/WAIT/ACCUMULATE/BUY/STRONG_BUY with -1/0/+1 dimensions) to fix ~95% BUY rate bias.

**Scope:**
- `backend/src/rule_evaluator.py`: Deterministic normalizer rewrite
- `backend/src/buy_tracker_instructions.py`: LLM system instructions rewrite
- `docs/screener.md`: User-facing documentation update

**Key Implementation Decisions:**

**1. Missing-data cap semantics (validation_flags vs evidence absence):**
The ≥3-missing dimension cap must use `validation_flags` (LLM omitted/invalidated breakdown keys), NOT absence of canonical evidence. A deliberate `0` (LLM explicitly sent neutral) with absent evidence is genuine neutrality. An absent breakdown key is a data gap. These two test requirements conflict if implemented naively — evidence-based counting fails when evidence is present but the LLM omits the key; flag-based counting correctly handles both. `_count_missing_data_dimensions` signature includes `validation_flags` parameter.

**2. Hard gate split (AVOID vs WAIT):**
- `dividend_cut_or_suspended` and `triple_bearish_breakdown` → Hard AVOID (AVOID state, never overridden by score)
- `earnings_within_days`, `rsi_overbought`, `price_extended` → Hard WAIT (WAIT state, only caps ≥ACCUMULATE)
- Hard WAIT does NOT affect UNFAVORABLE or AVOID (already negative signals)

**3. Signed score format:**
`_signed_score_text(n)` returns `"+n/5"` for positive, `"0/5"` for zero, `"-n/5"` for negative. Old `{0,1}` breakdowns remain backward-compatible (they sum 0–5, mapping into the positive tier of the new thresholds).

**4. Precedence order:** Hard AVOID → Hard WAIT (only when score ≥ ACCUMULATE) → Exceptional STRONG_BUY gate (only at +5 with no hard gates) → Score-based state.

**Validation Issue:**
After first implementation pass, one test failed: `test_three_missing_dims_caps_at_wait_with_insufficient_data`. Root cause: `_count_missing_data_dimensions` used evidence presence as proxy for data availability. Fix: use `validation_flags` exclusively — a dimension is "data-missing" only when `score_breakdown_{dim}_invalid` is present in the validation_flags set.

**Test File Hygiene:**
Stale `.pyc` cache files caused false test failure reports during CI iteration. Running `find . -name "*.pyc" -delete` before test runs is essential when test parametrize values have been recently updated. Always clear `__pycache__` dirs in `tests/` before reporting final pass/fail counts.

**Final Result:** 272 tests passing (all of `test_rule_evaluator.py` + `test_buy_tracker_normalization.py`), 0 failures.

### 2026-09-03 — Buy Tracker Six-State Implementation & Tri-State Scoring

**Role:** Quantitative strategy, prompt, deterministic normalization

Implemented Danny's six-state tri-state redesign across strategy, prompt, rule evaluation, and agent runner:

**Strategy & Prompt** (`buy_tracker_instructions.py`):
- Rewrote `DGI_ENTRY_RULES` with tri-state (-1/0/+1) per-dimension thresholds, replacing binary {0,1}
- Updated `BUY_TRACKER_INSTRUCTIONS` with six-state vocabulary, threshold examples, missing-data semantics
- Five dimensions: Value Entry (SMA50/SMA200 pullback/valuation), Trend (structural direction), Momentum (RSI/oscillator), Income (dividend/analyst), Calendar (earnings/gap-down)

**Normalization & Rule Evaluation** (`rule_evaluator.py`):
- `normalize_buy_tracker_activity()`: tri-state validation, hard-AVOID precedence (dividend_cut_or_suspended, triple_bearish_breakdown), hard-WAIT capping (earnings ≤2d, RSI>80, price_extended), exceptional gate (+5 + no hard gates + full evidence only)
- `_count_missing_data_dimensions()`: uses `score_breakdown_*_invalid` flags (not evidence absence); ≥3 missing → cap at WAIT with `insufficient_data` flag; deliberate LLM 0s are never miscounted
- Signed score format `"+3/5"` / `"-2/5"` / `"0/5"` with denominator 5 (number of dimensions)
- State thresholds: -5..−3 AVOID, -2..−1 UNFAVORABLE, 0..+1 WAIT, +2..+3 ACCUMULATE, +4 BUY, +5 BUY→STRONG_BUY via gate

**Agent Runner** (`agent_runner.py`):
- Updated `_NON_ALERT_ACTIVITIES` to include UNFAVORABLE and AVOID (only BUY, STRONG_BUY, ACCUMULATE alert now)
- Summary builder branches on alerting states; historical activity re-normalized on next run

**Frontend** (`badges.ts`, `ActivityDetailView.tsx`):
- Minimal changes: ACCUMULATE→blue, UNFAVORABLE→orange, AVOID→red

**Documentation** (`docs/screener.md`):
- Updated Buy Tracker section with six-state scale, tri-state thresholds, gate precedence, hard-AVOID/WAIT semantics
- Fixed stale documentation (was 4/5=STRONG_BUY, now correctly 5/5±gate=STRONG_BUY)

**Backward Compatibility:**
- Old {0,1} breakdowns remain valid (subset of {-1,0,+1}); historical data auto-re-normalized
- Unsigned score format "5/5" converted to "+5/5" on normalization
- No schema migration required

**Validation:** Basher, 272 focused tests passing. All acceptance criteria met.

**Decision record:** `.squad/decisions.md` — new entries "Buy Tracker Six-State Redesign (Danny)" and "Decision: Buy Tracker Implementation Details (Linus)"


### 2026-09-05 — Options Screener Share Availability: Backend Implementation & D2 Revision

**Role:** Backend implementation; fixed D2 frontend defect after Rusty lockout

Implemented backend share-availability calculation in `app.py` screener endpoint: `_build_share_availability_map` (per-symbol fields), per-row enrichment, `share_availability` query parameter, and filter logic (applied after aggregator, before pagination). Designed position-counting logic to filter active calls only (ignore closed/puts).

Initial feature rejected by Basher (D0) due to two contract gaps. After D1 fix, pulled forward to fix D2: TypeScript type declarations missing `committed_shares` and `free_shares` fields; tooltip recomputed value instead of consuming backend field (`OptionsScreenerView.tsx`). Rusty locked out as original implementer; Linus's fix updated both `screener.ts` and tooltip to read backend metadata, enabling 3 previously-failing tests to pass.

**Implementation:** `backend/web/app.py` (share availability calculation & enrichment)

**Revision:** `frontend/src/types/screener.ts`, `frontend/src/components/OptionsScreenerView.tsx` (D2 fix)

**Final Outcome:** All 73 gate tests pass (53 core + 20 extended); feature approved and production-ready.


### 2026-09-06 — Portfolio Ledger: Five Reviewer Findings Fixed (F1–F5)

**Role:** Revision owner after Livingston (backend) and Rusty (frontend) locked out post-rejection.

Five independent surgical fixes against Danny's frozen `danny-portfolio-rejection-resolution.md`:

**F1 — Commission preserved in fees:** `import_service._row_to_movement()` hardcoded `fees.total/total_eur = "0.00"`. Extracted `commission` variable in purchases/sales branches; dividends retain `Decimal("0")`. Used `f"{commission:.2f}"` formatting for consistent 2-decimal output. `holdings_service` already read `fees.total_eur` into cost basis — fix required no holdings-service changes. Cost basis now correctly includes commission.

**F2 — Spanish decimal dot-only strings:** `parse_spanish_decimal()` only stripped dots when a comma was present, so `"1.234"` was parsed as `1.234` instead of `1234`. Fix: always strip dots first (dots are ALWAYS thousands separators in this locale), then replace comma with dot. Existing comma-decimal tests (`"1.234,56"`, `"0,50"`) continue to pass.

**F3 — Preview company_name:** `_build_preview_response()` omitted `company_name`. Added `_resolve_preview_company_names()` helper (resolution_map + securities catalog lookup with empresa_raw fallback); call it in `generate_preview()` before serializing preview_movements; added `"company_name": m.get("company_name", "")` to the preview movement dict. No changes to the commit or holdings path.

**F4 — batch_value field rename (frontend):** Four touch-points: `ImportAnswer` type in `import.ts` (`value` → `batch_value`), `submit()` function signature in `ImportQuestionCard.tsx`, the actual `handleBatchSubmit` call, `answerSummary` reference, and `answerQuestion()` union type in `portfolio-api.ts`. Backend already read `batch_value` correctly.

**F5 — Dividend quantity null:** Changed `quantity = Decimal("0")` to `quantity = None` in the dividends branch of `_row_to_movement()`. Serialization: `str(quantity.normalize()) if quantity is not None else None`. Inventory tracking and `find_probable_duplicate` use `movement.get("quantity") or "0"/""`  (None-safe). `holdings_service._d(None)` already returned `Decimal("0")` and qty is never used for DIVIDEND type — no change needed. Frontend: `PreviewMovement.quantity: string | null`, `LedgerMovement.quantity: string | null`, `{m.quantity ?? "—"}` in ImportPreview.tsx, null-guard in PortfolioMovementsTable.tsx.

**Tests added:** 6 new tests in `test_portfolio_parsers.py` (F2), 9 in `test_portfolio_import_service.py` (F1/F3/F5), 4 in `test_portfolio_holdings.py` (F1/F5), 4 in `test_portfolio_endpoints.py` (F1/F3/F4/F5). All 151 targeted portfolio tests pass; 521-test options regression suite untouched. TypeScript `tsc --noEmit` clean.

**Key lesson:** When a module-level parsing function is for a single locale (Spanish historical schemas), document and enforce the assumption explicitly — a comment like "dots are ALWAYS thousands separators" is not enough without a code path that strips them unconditionally. The conditional `if "," in s:` pattern is an anti-pattern for locale-specific parsers: it invites the English fallback for a dataset where that interpretation is never correct.

---

## Portfolio Unified Implementation — First Review Fixes Complete (2026-09-06 00:13)

**Role:** First-round fix owner (F1–F5)
**Status:** ✅ COMPLETE

**Fixes Applied:**
- F1: Commission preserved in `fees.total_eur`
- F2: Spanish decimal parser — dots ALWAYS thousands separators
- F3: Preview company_name resolved from security master
- F4: batch_value field name (frontend) corrected
- F5: Dividend quantity = null (not Decimal("0"))

**Test Results:**
```
Backend: 151 tests (138 original + 13 new) — ALL PASS
Frontend: tsc --noEmit — 0 errors
```

**Archived to:** `.squad/decisions/archive/inbox-2026-09-06/` (audit trail preserved)

**Final Status:** ✅ All Round 1 findings resolved. Awaiting Round 2 validation.

---

## 2026-09-06 — Phase 2 Backend Revision (Rejection Recovery)

**Role:** Independent revision author — all Phase 2 backend defects
**Status:** ✅ COMPLETE

### Assignment
Livingston's Phase 2 backend implementation was rejected by Basher (4 confirmed defects) and Rusty (1 preview gap). Linus assigned as independent author with Livingston locked out.

### Defects Fixed

**D1 — `total_purchases_eur` included TRANSFER_IN carried basis (holdings_service.py)**
- Root cause: single `total_cost_eur` accumulator used for both BUY outflows and TRANSFER_IN carried basis; aliased to `total_purchases_eur` at output time.
- Fix: added `total_buy_cost_eur` accumulator, incremented only in the BUY branch. `total_purchases_eur` now uses `total_buy_cost_eur`; `total_invested_eur` keeps `total_cost_eur`. `current_invested_eur` changed to `total_invested - total_sales` (semantically correct; no test impact since no existing tests mix TRANSFER_IN with sales).

**D2 — Individual reassignment accepted blank reason (portfolio_routes.py)**
- Root cause: `reason=str(body.get("reason", ""))` passed empty string through without validation.
- Fix: `reason = str(body.get("reason", "")).strip(); if not reason: return _err("validation_error", ...)`.

**D3 — Batch reassignment accepted blank reason (portfolio_routes.py)**
- Same pattern as D2 in the batch handler. Same fix applied.

**D4 — Batch reassignment was non-atomic / silently partial (cosmos_portfolio.py)**
- Root cause: per-item `try/except` incremented `skipped_count` and continued on failures, producing silent partial application.
- Fix: Fail-fast with compensating rollback via `_rollback_batch_reassign`. On first failure: stop processing, delete each new doc from dest partition, restore each original to `correction_status = "ACTIVE"` in source partition. Best-effort per-step with MANUAL CLEANUP REQUIRED logging on rollback failures. `ValueError("batch_reassign_failed: ...")` raised; route handler catches it and returns 500.

**Gap — Preview endpoint**
- Livingston's partial implementation was reviewed and found correct: `preview_batch_reassign` uses `_fetch_reassign_candidates` (shared predicate), is read-only, and returns the specified shape. No changes needed.

### Invariants Preserved
- `total_invested_eur` remains correct for BUY-only scenarios (equal to `total_purchases_eur`, backward-compat alias test still passes).
- `avg_cost_basis_eur` unchanged — still uses `total_cost_eur / paid_buy_shares`.
- TRANSFER_IN adds to `total_cost_eur` (invested) but NOT `total_buy_cost_eur` (purchases).
- All Phase 1 and Phase 2 existing tests remain green.

### Test Results
```
Targeted (reassignment + transfers + holdings): 92 PASSED
Full portfolio suite: 505 PASSED, 0 FAILED
```

### xfail Markers Removed (all 4)
1. `test_portfolio_phase2_reassignment.py::TestIndividualReassignment::test_reassign_missing_reason_400`
2. `test_portfolio_phase2_reassignment.py::TestBatchReassignment::test_batch_missing_reason_400`
3. `test_portfolio_phase2_reassignment.py::TestBatchAtomicityDefect::test_batch_is_atomic_on_failure`
4. `test_portfolio_phase2_transfers.py::TestTransferHoldingsInvariants::test_transfer_excluded_from_purchases_eur`

### Files Changed
- `backend/src/portfolio/holdings_service.py` — D1
- `backend/web/portfolio_routes.py` — D2, D3, D4 (route error handling)
- `backend/src/portfolio/cosmos_portfolio.py` — D4 (atomicity + rollback)
- `backend/tests/conftest_portfolio_p2.py` — added `delete_item` to `FakePortfolioContainer`
- `backend/tests/test_portfolio_phase2_reassignment.py` — removed 3 xfail markers
- `backend/tests/test_portfolio_phase2_transfers.py` — removed 1 xfail marker

### Pattern Learnings
- Every design decision that introduces a numeric invariant must have a named accumulator. Aliasing two semantically different values to the same field at output time is a guaranteed divergence point.
- Route-level validation must be explicit (`.strip()` + non-empty check) — service-layer defaults don't enforce caller contracts.
- Cosmos cross-partition operations cannot be atomic; compensating rollback with per-step error logging is the correct approach for Phase 2 volumes.

---

## 2026-09-06 — Sales Reconciliation / write_ledger_txn Safety Guard

### Context
User reported: after re-importing production data, Portfolio showed total_sales_eur ≈ EUR 103,689
vs user's expected ≈ EUR 99,135 (difference ≈ EUR 4,554). User suspected rights (DERECHOS)
for ACS, Viscofán, Técnicas Reunidas were mis-classified or double-counted.

### Methodology
Full read-only inspection of all 38 active SELL ledger_txn documents in production Cosmos DB
(cosmos-stock-options / stock-options-manager / portfolio container). Cross-referenced all
three committed import sessions (format=sales), backup files, and the import_service/
holdings_service code paths.

### Reconciliation Findings

**Production DB state (post re-import):**
- Active SELL movements: 38 (all from session imp_91ff8bc47f3f4c18, batch batch_99d71bb8f774)
- No SUPERSEDED/VOIDED movements found
- No deleted movements found (deleted_at not set)
- No duplicate fingerprints (same security_id + trade_date + gross_eur)
- All movements in EUR (currency=EUR, fx.rate=1.0)

**Computed total (holdings_service formula = gross_eur − fees_eur per active SELL):**
- Total net: EUR 103,689.62
- ACCIONES net: EUR 103,632.90
- DERECHOS net: EUR 56.72 (5 movements: ACS×3, TRE×1, VIS×1)
- API total matches independent calculation exactly.

**DERECHOS movements are correctly handled:**
- All 5 DERECHOS movements are classified, do not decrement share counts,
  and contribute exactly once to total_sales_eur. Not the cause.

**Root cause of discrepancy:**
Primary: The ACS ACCIONES sale on 2026-07-14 (movement ID
`txn__unassigned_20260714_ACS_SELL_037`, gross=EUR 4,724.00, fees=EUR 8.94,
net=EUR 4,715.06) is the newest sale in the CSV (row 037, appended since the
user's reference calculation). Without it: total = EUR 98,974.56 ≈ "~EUR 99,135"
(within 0.16% of the user's stated expected).

Remaining ~EUR 160 gap between 98,974.56 and 99,135.00 is consistent with:
- The user's "approximately" qualifier
- DERECHOS fee corrections between old 6-column CSV (Session 1: all ACCIONES,
  higher fees) and new 7-column CSV (Session 3: DERECHOS properly classified,
  near-zero fees), net delta = EUR +11.88

**Import session history (all 2026-09-06):**
- 09:16 (Session 1): 38 rows committed, 6-col CSV (no Tipo), total EUR 103,677.74
- 10:28 (Session 2): 10 rows committed (28 skipped), 7-col CSV, total EUR 17,549.34
- 12:45: Actual deletion of all 1242 ledger_txn docs (365 BUY + 839 DIVIDEND + 38 SELL)
- 12:54 (Session 3): 38 rows committed, 7-col CSV, total EUR 103,689.62 (current)

### Code Bug Found and Fixed

**Bug:** `write_ledger_txn` used plain `upsert_item` which would silently overwrite
a VOIDED or SUPERSEDED movement document with a new document lacking `correction_status`.
The replacement would pass the `NOT IS_DEFINED(c.correction_status)` filter and be
treated as active again — effectively undoing a correction or void via re-import.

**Fix (surgical, Decimal-safe):**
- Added `CosmosPortfolioService.VoidedMovementError` exception class
- `write_ledger_txn` now reads the existing document (if any) before upsert;
  raises VoidedMovementError if correction_status ∈ {SUPERSEDED, VOIDED}
- Import commit loop catches VoidedMovementError separately, counts as `skipped_count`
  with explicit warning log — no silent data-integrity loss
- Guard skips cleanly if account_id is missing (new document path, no read needed)

### Files Changed
- `backend/src/portfolio/cosmos_portfolio.py` — added VoidedMovementError + guard in write_ledger_txn
- `backend/src/portfolio/import_service.py` — import VoidedMovementError alias, separate except clause
- `backend/tests/test_portfolio_phase2_corrections.py` — added TestWriteLedgerTxnSafetyGuard (5 tests)

### Test Results
- 21/21 correction tests pass (including 5 new safety guard tests)
- 527/527 portfolio tests pass

### Affected Production Records (data root cause — no code changes to data)
The offending movement that explains most of the discrepancy:
- **ID:** `txn__unassigned_20260714_ACS_SELL_037`
- **Security:** XMAD:ACS (ACS Actividades)
- **Type:** SELL ACCIONES
- **Date:** 2026-07-14
- **Gross:** EUR 4,724.00 | Fees: EUR 8.94 | Net: EUR 4,715.06
- **Recommendation:** User should verify this sale against broker statement.
  If the sale is correct, the expected total should be updated to EUR 103,689.62.
  If the sale was imported in error, it should be soft-deleted via the API
  (`DELETE /api/portfolio/movements/txn__unassigned_20260714_ACS_SELL_037?account_id=_unassigned`)
  — do NOT mutate or re-import without voiding first.

### Pattern Learnings
- Import upsert semantics must protect correction/voiding state. A re-import must
  never silently undo a correction. The safety guard (read-before-upsert) is the
  correct pattern even though it adds one extra read per movement per commit.
- When investigating discrepancies, enumerate ALL import sessions (including
  non-production ones) and match batch_id on persisted movements before suspecting
  code bugs. In this case the code was correct; the data (a new CSV row) was the cause.
- DERECHOS classification is not the cause of large discrepancies; their total
  cash proceeds are always small. Anchor suspicion on the most recent additions.
- "Approximately" in a financial reconciliation report means within ~1% — map
  to the nearest specific movement before concluding cause.

---

## 2026-09-06 — Portfolio CMP Cost-Basis Implementation & Safety Guard

**Role:** Backend Implementation, QA
**Status:** ✅ COMPLETE & RELEASED

### CMP Algorithm Implementation

**Task:** Implement per-security moving weighted average cost (CMP) algorithm per Danny's design; author 130 acceptance tests; implement write_ledger_txn safety guard.

**Backend Changes:**

1. **holdings_service.py — CMP Pool Logic**
   - Per-security pools: `pool_shares`, `pool_cost_eur`, `avg_cost_eur`
   - Chronological ordering: `movements.sort(key=(trade_date, id))`
   - Movement processing: BUY (cost += gross+commission), SELL ACCIONES (remove at CMP), SELL DERECHOS (proceeds only), TRANSFER (preserve global), DIVIDEND (no pool impact)
   - Incomplete handling: `has_incomplete_cost_basis` flag; zero-cost lots tracked separately
   - Negative inventory protection: decrement capped at current pool; excess cost 0

2. **models.py — New Summary Fields**
   - `remaining_cost_basis_eur` (replaces old `current_invested_eur` semantics)
   - `cost_basis_sold_eur` (cumulative CMP cost assigned to sales)
   - `total_purchase_outflow_eur` (gross + commission of all BUY COMPLETE)
   - `total_sale_proceeds_eur` (gross − commission of all SELL)
   - `rights_proceeds_eur` (SELL DERECHOS only)
   - `realized_result_eur` (proceeds − cost_sold)
   - `has_incomplete_cost_basis` (global warning flag)
   - Backward-compat aliases: `total_purchases_eur`, `total_sales_eur`, `total_invested_eur` (unchanged values)

3. **import_service.py & cosmos_portfolio.py — Write Guard**
   - `write_ledger_txn()` checks for existing doc with `correction_status ∈ {SUPERSEDED, VOIDED}`
   - Raises `VoidedMovementError(movement_id, status)` if restoration attempted
   - Import loop catches, increments `skipped_count`, logs warning
   - Prevents silent data corruption on re-import

### Test Suite

**16 Acceptance Scenarios (S1–S16):**
- S1: BUY only
- S2: BUY + partial SELL (FIFO consumes oldest)
- S3: BUY + full SELL
- S4: Two BUY at different prices + SELL
- S5: DERECHOS only (pool untouched)
- S6: ACCIONES + DERECHOS
- S7: BUY INCOMPLETE + BUY COMPLETE + SELL
- S8: TRANSFER_IN + TRANSFER_OUT (global preserved)
- S9: SELL before BUY (negative inventory)
- S10: Multi-security aggregation
- S11: Backward-compat aliases
- S12: Multi-lot FIFO consumption
- S13: Full exit clears pool, avg=null
- S14: Soft-deleted movement excluded
- S15: Correction (SUPERSEDED) excluded
- S16: Three buys with multi-lot SELL

**Voided-Movement Guard Tests (5 tests):**
- Guard rejects VOIDED movement restoration
- Guard rejects SUPERSEDED movement restoration
- Guard allows new movements
- Guard handles missing account_id (safe fallback)
- Import loop catches and logs appropriately

**Test Results:**
- 130 CMP acceptance tests — ALL PASS
- 79 holdings + corrections tests — ALL PASS
- Total portfolio suite: 209 tests — ALL PASS
- No regressions in pre-existing tests

### Code Quality

- **Algebraic safety:** Pool cost never negative (decrement capped at current pool)
- **Determinism:** Chronological sort ensures identical results across runs
- **Edge cases:** Incomplete, zero-cost, negative inventory all handled correctly
- **Backward compatibility:** Aliases preserved; only `current_invested_eur` semantics intentionally changed

### Deployed State

**Commit:** `ff087c3 fix: report remaining portfolio cost basis`
- All 130 acceptance tests pass
- All 79 holdings/corrections tests pass
- TypeScript clean
- No regressions

**Production:** API + Frontend deployed on sha-ff087c3, both healthy.

### Key Insights

1. **Pool-based cost allocation is transparent and algebraically safe.** Avoiding negative pools by construction (decrement capped) eliminates edge-case bugs.

2. **Chronological determinism via (trade_date, id) sort is essential.** Tie-break on id ensures stable output for same-day trades.

3. **DERECHOS sales as pool-independent events enable rights management without conflating share and rights accounting.**

4. **Write guards for import safety prevent subtle data corruption.** Silent restoration of voided movements is a real production risk; detecting it at write time is efficient and correct.


---

## 2026-09-07 — Portfolio Enrichment: Yahoo Symbol Resolution Fix (non-US securities)

**Role:** Implementation (danny-yahoo-symbol-resolution-contract.md)
**Status:** ✅ COMPLETE

### Problem

`run_portfolio_enrichment()` read only `sym_doc["symbol"]` (bare local
ticker — e.g. `ULVR`, `NESN`, `DGE`) and passed it straight through
`analyze_single_symbol()` → `YFinanceFetcher.get_ticker_data()` → `yf.Ticker()`.
The security's MIC (`symbol_config.exchange`) was never consulted, so every
non-US portfolio holding either failed enrichment or (worse) risked
resolving to the wrong Yahoo-listed instrument.

### Fix — single resolution point, no duplicated suffix table

1. **`resolve_yfinance_symbol(ticker, exchange_mic, security_master_doc=None)`**
   added to `backend/src/portfolio/provider_symbols.py` (same module that
   already owned `MIC_TO_YFINANCE_SUFFIX`/`suggest_yfinance_symbol`).
   Precedence: explicit `security_master.provider_symbols["yfinance"]`
   override → MIC suffix table → `None` (fail closed for unknown/missing MIC).
2. **Legacy exchange alias safety net.** Discovered mid-implementation: the
   pre-security_master `/api/symbols` add flow (DGI-screener-sourced, always
   US) stores `exchange` as a free-text display name (`"NASDAQ"`, `"NYSE"`,
   `"AMEX"`), never a MIC code. Without special-casing these, the new
   fail-closed behavior would have silently broken enrichment for every
   legacy US watchlist symbol added through that path (a real production
   regression, not just a test gap — no existing test covered this, so it
   would have shipped undetected). Added a tiny, well-documented
   `_LEGACY_US_EXCHANGE_ALIASES` set in `resolve_yfinance_symbol` that maps
   these three known-safe labels to bare-ticker (same as XNYS/XNAS) — not a
   second suffix table, since the resolved suffix is always empty.
3. **`portfolio_enrichment.run_portfolio_enrichment()`** now builds a
   `CosmosSecuritiesService(cosmos.container)` (symbols container — security
   master docs co-locate there) and, per symbol, resolves
   `security_id = sym_doc.get("security_id") or f"{exchange}:{symbol}"`,
   fetches the security_master doc, calls `resolve_yfinance_symbol`, and
   skips with a structured warning (counted in `errors`) on `None` — never
   falls back to the bare ticker for a non-US MIC.
4. **`enrich_symbol(symbol, yf_symbol=None)`** and
   **`dgi_screener.analyze_single_symbol(symbol, filters=None, yf_symbol=None)`**
   both gained an optional resolved-symbol parameter used only for the
   fetch call; `symbol` remains the storage/display/log key throughout.
   Defaulting `yf_symbol` to `symbol` preserves 100% backward compatibility
   for the US-only DGI screener universe (never called with the new param).

### Key Insight

**A "fail closed" contract for MIC resolution must be checked against every
document-creation path that populates the field it inspects, not just the
new one being fixed.** The bug being fixed only affected the newer
security_master-linked add path; the older DGI-screener add path uses a
different, non-MIC vocabulary for the same field name (`exchange`). Fixing
the newer path's ambiguity without accounting for the older path's
different-but-valid vocabulary would have introduced a regression more
severe than the one being fixed (breaking working US symbols instead of
just non-US ones). Always grep every writer of a field before tightening
how a reader interprets it.

### Verification

- `test_provider_symbols.py`: 41/41 pass (added 12 new cases for
  `resolve_yfinance_symbol` — override precedence, missing/empty override
  fallthrough, unknown/missing MIC → None, XNYS/XNAS bare, legacy US alias
  bare).
- `test_dgi_momentum.py`: 15/15 pass (confirms `analyze_single_symbol`
  signature change is backward compatible).
- Targeted sweep (dgi/provider_symbols/portfolio_enrichment/
  watchlist_symbols/us_options_eligibility/securities_catalog): 174 passed.
  4 pre-existing failures in `test_watchlist_symbols.py::TestWatchlistStrategyFilter`
  are unrelated — caused by another agent's in-progress, uncommitted
  `us_exchange_eligibility` 403-gate wiring already present in the shared
  working tree before this task started (confirmed via `git stash`: those
  4 tests still fail with my diff fully reverted). Not touched, per scope
  boundary ("Do not modify unrelated active-agent work").

### Follow-up (same day) — Added missing test_portfolio_enrichment.py

Verified my Yahoo resolution diff was still present in the shared working
tree (`git diff HEAD` non-empty for all 4 files) despite a report of it
appearing absent — likely a transient view during another agent's
concurrent write. Since Basher had not yet created the §7-required
`test_portfolio_enrichment.py`, added it myself: 10 focused tests (XMAD/
XLON/XETR/XSWX suffix routing, override precedence, unknown-MIC and
missing-exchange fail-closed skip with no crash, mixed-batch partial
success, XNYS bare, legacy free-text US exchange alias bare) using an
in-memory FakeContainer/FakeCosmos — no network, no real Cosmos. All 10
pass; full targeted sweep unchanged at 184 passed / 4 pre-existing
unrelated failures.

## Single canonical add-symbol contract (danny-single-add-symbol-contract.md)

Implemented the BACKEND portion: consolidated symbol creation into
`POST /api/symbols/add` (`backend/web/portfolio_routes.py`), removed the
legacy `POST /api/symbols` (`api_create_symbol` in `backend/web/app.py`)
and its backing `cosmos_db.create_symbol()` method (no other callers per
grep). Relocated warm-up (enrichment + forecast backfill) into a new
`_start_symbol_warmup()` helper reusing `resolve_yfinance_symbol` from the
Yahoo-resolution work (no duplicated suffix map/enrichment logic).

### Key bug found and fixed: `_auto_enrolled` is not a "just created" signal

`ensure_symbol_config()` (in `symbol_config_sync.py`) sets `_auto_enrolled:
True` permanently on the persisted doc — it is never cleared on later
reads. The pre-existing `add_symbol` code used
`config.get("_auto_enrolled")` to decide `config_created` (i.e. "should I
fire warm-up?"), which is broken: a *second* call for an already-enrolled
security would still see `_auto_enrolled=True` and re-fire warm-up,
violating the contract's "exactly once" gate. Fixed by pre-checking config
existence via `symbols_container.read_item(item=f"config_{ticker}",
partition_key=ticker)` (catching `CosmosResourceNotFoundError`) BEFORE
calling `ensure_symbol_config`, and basing `config_created`/
`config_existed` on that pre-call snapshot instead of the mutable field.
Lesson: never treat a field set once-and-never-cleared on a persisted doc
as a per-call "did I just do this" signal — snapshot state before the
mutating call.

### Hermetic-test trap: unstubbed `threading.Thread` + resolvable MICs

Once warm-up wiring landed, any existing test that triggers
`config_created=True` on an `XNYS`/`XNAS` security (the most common seed
data) would spawn REAL background threads hitting real yfinance/network
code, because `resolve_yfinance_symbol` returns a non-`None` bare ticker
for those MICs. Fixed by stubbing `threading.Thread` to a `_NoOpThread` by
default in the shared `client` fixture (`test_unified_add_symbol.py`),
with an opt-in `_SyncThread` (runs target synchronously) for tests that
need to deterministically observe warm-up side effects. Also:
`loop.create_task()` requires a genuine coroutine object — a custom
`__await__`-only stand-in fails; use a real `async def` replacement when
monkeypatching `backfill_symbol_forecasts` in tests.

### Verification

- Targeted sweep: `test_unified_add_symbol.py` (28, incl. new
  `TestAddSymbolWarmup` x5), `test_watchlist_symbols.py`,
  `test_provider_symbols.py`, `test_portfolio_enrichment.py`,
  `test_securities_catalog.py`, `test_us_options_eligibility.py`,
  `test_ensure_symbol_config.py`, `test_symbol_config_triggers.py`,
  `test_backfill_endpoints.py` — 260 passed, 0 failures.
- Independently re-verified against Basher's contract-compliance suite
  `test_add_symbol_contract.py` (not authored by me) — 30/30 pass,
  confirming the implementation satisfies the contract from an
  independent test author's perspective.
- Confirmed via `git diff --stat` that exactly the 5 intended backend
  files changed (`portfolio_routes.py`, `app.py`, `cosmos_db.py`,
  `test_watchlist_symbols.py`, `test_unified_add_symbol.py`); frontend
  files modified concurrently by another agent were left untouched.
- Note: the earlier Yahoo-resolution files (`provider_symbols.py`,
  `portfolio_enrichment.py`, `dgi_screener.py`,
  `test_provider_symbols.py`, `test_portfolio_enrichment.py`) now show an
  empty `git diff` because they were committed by a concurrent agent in
  `69e3635 "feat: consolidate symbols and portfolio workflows"` — not
  lost, just merged. Lesson: an empty diff on previously-verified files
  can simply mean "already committed" — check `git log -- <file>` before
  assuming loss.

## TradingView symbol resolution + Options Screener universe (two contracts, one pass)

Implemented `danny-tradingview-symbol-contract.md` and
`danny-options-screener-universe-contract.md` together to avoid overlapping
edits in `provider_symbols.py`/`app.py`.

### TradingView resolver

Added `MIC_TO_TRADINGVIEW_EXCHANGE` (6 approved MICs only) +
`resolve_tradingview_symbol()` to `provider_symbols.py`, mirroring
`resolve_yfinance_symbol`'s precedence shape exactly (override → MIC → legacy
alias → None). Wired into `_compute_symbol_detail()` using the *raw*
`security_doc` (not the cleaned `security_field` projection) — overrides live
in `provider_symbols` on the raw doc, and the cleaned projection strips
extra keys. Verified clean against Basher's independently-written
`TestResolveTradingviewSymbol` (20/20) and `test_tradingview_symbol_detail.py`
(10/10) with zero changes needed on my side — good sign the contract's
precedence spec was unambiguous enough for two independent implementations
to converge.

### Options Screener universe — contract text vs. reality mismatch

Danny's contract's evidence section names `_build_screener_symbol_inputs` as
the live "manual" screener code path. **It is dead code** — not called
anywhere in current `app.py` (confirmed via grep across the whole tree,
including bytecode caches). The endpoint was refactored to "precomputed-only"
(`copilot-options-screener-precomputed-only.md`) after the universe
contract's evidence was gathered, and now builds `docs`/`symbol_inputs`
inline inside `api_screener_options` itself, never calling the old helper.
Lesson: **when a contract's cited call site doesn't match `grep`-confirmed
reality, trust the grep, not the document** — architecture drifts between
design-review and implementation windows in a fast-moving shared repo.
Wired the universe filter into both (the live endpoint for correctness, the
dead helper for contract-letter completeness/future-proofing). Basher's own
new `TestUniverseFilterEnforcement` test class independently targets the
live endpoint, confirming this was the right call.

### Legacy NYSE/NASDAQ text needs its own narrow eligibility normalization

`is_us_options_eligible` (Amendment J, `us_exchange_eligibility.py`) only
recognizes MICs (`XNYS`/`XNAS`), not the legacy free-text exchange labels
(`"NYSE"`/`"NASDAQ"`/`"AMEX"`) that `provider_symbols.py`'s
`_LEGACY_US_EXCHANGE_ALIASES` already treats as bare-ticker-equivalent for
Yahoo/TradingView. Basher's independently-written unit tests
(`test_options_screener_universe.py`) required `"NYSE"`/`"NASDAQ"` text to be
eligible for the screener universe, but explicitly required `"AMEX"` to stay
excluded (Amendment J restricts options eligibility to XNYS/XNAS only, not a
third AMEX-equivalent). Added a small, scoped 2-entry alias map
(`_LEGACY_US_EXCHANGE_TO_MIC = {"NYSE": "XNYS", "NASDAQ": "XNAS"}`, no AMEX)
local to `options_screener_universe.py`, applied only before calling
`is_us_options_eligible` — kept that function itself untouched/imported
verbatim, per the contract's explicit "reused unchanged" instruction. Lesson:
different consumers of the "US-eligible" concept can have subtly different
legacy-alias scopes (Yahoo/TradingView resolution treats NYSE/NASDAQ/AMEX all
as bare-ticker-equivalent; options eligibility only recognizes NYSE/NASDAQ) —
don't assume one alias set fits every consumer; check the actual acceptance
test.

### Known collateral test breakage (flagged to Basher, not fixed by me)

`test_options_screener_share_availability.py` (30 tests) now fails because
its Cosmos fake never sets an `exchange` field on fixture docs — every
fixture symbol fails the new universe eligibility check closed. This is
correct-per-contract behavior (explicitly acknowledged as acceptable in the
contract's own evidence section) but breaks an orthogonal-feature test suite
(share-availability status calculation) that predates this contract. Left
untouched — flagged in my decision note for Basher to add `"exchange":
"XNYS"` to that file's fixture builder; not my scope to rewrite Basher's
adversarial suite. Separately, 6 pre-existing failures in
`test_options_screener_endpoint.py`/`test_options_screener_cache_concurrency.py`
(including 3 in Basher's brand-new `TestUniverseFilterEnforcement` class)
are unrelated to my change — all go through a `_warm_symbol` test helper
that the test file's own comment already documents as "incompatible with
the precomputed-only endpoint" (the live endpoint never touches the
per-symbol chain cache at all anymore). Confirmed via reading the helper's
own acknowledgment comment before assuming I'd caused a regression — always
check whether a failing test's own docstring/comments already disclose a
known, pre-existing gap before treating a failure as self-caused.

### Verification

- `test_provider_symbols.py`: 61/61 (41 existing + 20 new TradingView).
- `test_tradingview_symbol_detail.py`: 10/10.
- `test_options_screener_universe.py`: 25/25.
- Broad sweep (add-symbol/watchlist/symbol-detail/eligibility/ensure-config):
  418 passed, 0 failures.
- Scheduler-adjacent suite (force-alpha/precompute/production-bug/scheduler-
  startup regressions): 60/60 — confirms `main.py` wiring is safe.
- Confirmed via `git diff --stat`/`git status --short` that only the
  intended files changed (`provider_symbols.py`, `app.py`, `main.py`, plus
  new `options_screener_universe.py`/`watchlist_membership.py`); frontend
  and the migration script untouched; nothing committed/pushed.

## PEP repair currency-correction revision (reviewer-rejection fix)

Task: `.squad/decisions/inbox/danny-pep-repair-currency-correction.md` — the
previously-approved `_currency_verdict` mechanism in
`backend/scripts/repair_pep_security_id.py` (ledger `gross.currency`
unanimity → `listing_currency` correction) was rejected in review: a
portfolio's ledger booking/accounting currency is NOT evidence of a
security's listing currency, and the mechanism could silently corrupt
`listing_currency` from unrelated transaction data.

Corrected invariant implemented (currency-only changes; nothing else in the
file touched):
- `gross.currency` is now purely diagnostic (`_ledger_accounting_currency_note`
  → `"no_movements"` / `"unanimous_accounting_currency:<CUR>"` /
  `"mixed_accounting_currencies"`) — structurally incapable of producing a
  proposed currency (no "proposed" return value at all, unlike the old
  function).
- New optional `--listing-currency CUR` CLI flag. Omitted → zero currency
  change, existing source `listing_currency` preserved exactly as before
  this mechanism ever existed.
- If provided, `run_apply()` performs a live `YFinanceFetcher('PEP')` check
  (`_verify_listing_currency_with_provider`) requiring
  `info.currency == info.financialCurrency == requested CUR` AND the
  provider's exchange corroborates the target MIC — and this MUST happen
  and pass BEFORE Phase 2 (backup)/any mutation. Any failure/mismatch/
  unreachable → `RepairAbort(exit_code=2)` before backup.
- `--audit` runs the identical check via the same `discover()` code path
  but never aborts — reports `provider_currency_verdict` (`"not_requested"` /
  `"verified:<CUR>"` / `"unreachable"` / `"mismatch:<details>"`) for
  visibility only.

Reuse-don't-duplicate: composed two already-existing mapping tables instead
of inventing a new one for provider-exchange→MIC corroboration:
`src.dgi_screener.EXCHANGE_MAP` (yfinance code e.g. "NMS" → free-text alias
"NASDAQ"; this is the backend's existing twin of the frontend's
`toExchangeMic` in `DgiScreenerView.tsx`) composed with
`src.portfolio.provider_symbols.LEGACY_ALIAS_TO_MIC` (alias → MIC). This
`_resolve_provider_mic()` helper is the only new function; it deliberately
reuses both tables by reference/import rather than copying values.

Design note for future maintainers: keep provider verification logic living
inside `discover()` (best-effort, never raises) rather than duplicating the
live-check call site in both `run_audit()` and `run_apply()` — both paths
then share identical results and only differ in whether they gate/abort
based on the verdict. Also watch for parameter/local-variable shadowing
when adding a `listing_currency` parameter to a function whose body already
had a same-named local variable for a different, narrower purpose (renamed
to `resolved_listing_currency` inside the Phase 3a currency-selection block
in `run_apply()`).

Collaboration note: this revision explicitly locked out a peer agent
("Livingston") from consultation, and a second peer ("Reuben") had already
independently rewritten `TestCurrencyEvidence` + added
`TestProviderVerifiedListingCurrency` in the shared test file by the time
my implementation landed — my chosen function/field/CLI names
(`provider_currency_verdict`, `_verify_listing_currency_with_provider`,
`--listing-currency`) happened to align closely enough with the contract's
literal field-name requirements that all 13 of Reuben's new/rewritten
currency tests passed against my implementation with zero adjustment
needed on either side — evidence that hewing tightly to a contract's exact
field-name language (not just its intent) meaningfully de-risks concurrent,
non-consulting collaboration on the same feature.

### Verification
- `backend/tests/test_repair_pep_security_id.py`: 51/51 (full suite,
  including Reuben's 13 currency tests and all pre-existing MIC-derivation/
  collision/backup/checksum/holdings/restore/CAS/dry-run safety tests).
- `git diff --stat` confirms only `backend/scripts/repair_pep_security_id.py`
  was touched by me; the test file's diff is Reuben's, not mine. No
  commit/push/production run performed.

### 2026-09-07T13:43:00+02:00 — Symbol Onboarding Unification (Contracts A/B/C Implementation)

**Batch:** Unified symbol onboarding + market integrations (4 coordinated contracts)

**Scope — Backend Implementation:**
1. **Contract A (Single Add Symbol):** Canonical `POST /api/symbols/add` warm-up gate
   - Pre-check config existence; gate warm-up strictly on `config_created=True`
   - `_start_symbol_warmup()` helper (fire-and-forget, reusing `resolve_yfinance_symbol`)
   - Added `warmup_started` response field
   - Removed legacy `POST /api/symbols` + `cosmos_db.create_symbol()`

2. **Contract B (TradingView by MIC):** Centralized provider symbol mapping
   - `MIC_TO_TRADINGVIEW_EXCHANGE` (6 verified MICs) in `provider_symbols.py`
   - `resolve_tradingview_symbol()` resolver mirroring `resolve_yfinance_symbol()` precedence
   - Wired `tradingview_symbol` into both `_compute_symbol_detail()` branches (no N+1)

3. **Contract C (Options Screener Universe):** Universe predicate enforcement
   - `compute_options_screener_universe()` in new module (US-eligible + held/watched)
   - Relocated `_is_watchlist_member()` to `watchlist_membership.py` (reusable)
   - Enforced at manual endpoint + scheduled job; single `HoldingsService.compute_holdings()` call

**Test Coverage:**
- 260 targeted backend tests passed (own suite)
- 61 provider_symbols tests (41 existing + 20 TradingView)
- 10 TradingView detail tests
- 25 universe tests
- 60+ scheduler tests (no regression)
- Verified against Basher's independent contract test suites (passing)

**Verification:** All contracts production-ready; no concurrent edit conflicts with Rusty's frontend work.


## Provider-symbol corrections: ENAG/MICCT/ULVR (new artifact)

Task: `.squad/decisions/inbox/danny-provider-symbol-corrections-enag-micct-ulvr.md`
— pure `provider_symbols.yfinance`/`.tradingview` override corrections for
three securities whose canonical identity (security_id/MIC) is already
correct; Yahoo just doesn't mirror the local ticker after a spin-off/merger
(Magnum "MICCT"→"MICC", Unilever "ULVR"→"UNA" on Euronext Amsterdam;
Enagás "ENAG"→"ENG" on BME). No identity migration — narrower in scope than
the PEP/AD repairs (zero writes to config/ledger/portfolio, ever).

Filename conflict discovered and resolved: the contract's own "Authorized
paths" section (and the independently-authored test file,
`backend/tests/test_repair_provider_symbols.py`, written by Reuben)
name the implementation `scripts/repair_provider_symbols_enag_micct_ulvr.py`,
while this session's task instruction asked for the shorter
`scripts/repair_provider_symbols.py`. Resolved by keeping all real logic in
the contract/test-named file (so Reuben's independently-authored suite runs
unmodified) and adding `repair_provider_symbols.py` as a zero-logic
re-export shim (`from ... import *`) satisfying the shorter requested
filename without a second maintained copy. Lesson: when a contract document
and a live task instruction disagree on an artifact's exact filename, check
whether a concurrent peer has already authored tests against one specific
name before choosing — matching the tests unlocks independent verification
without asking anyone to adjust already-completed work.

Design choices of note:
- Live verification only checks `currency` (not the PEP repair's stricter
  `currency == financialCurrency` triple-check) — this contract's own §1
  text only requires `currency == listing_currency` corroboration for this
  narrower repair; over-porting the PEP task's stricter gate would have
  been scope creep not asked for here.
- No new general MIC↔exchange-name mapping table: since no "AD repair"
  hint table had landed yet at implementation time, used a small
  `_REPAIR_SCOPED_MIC_EXCHANGE_HINTS` dict explicitly scoped to only the
  two MICs this repair touches (XMAD, XAMS), documented as evidence-logging
  only, not a shared/reusable table — matches the contract's own explicit
  fallback instruction for this exact situation.
- Company-name corroboration (ENAG only, the one candidate flagged "must
  verify") uses a lightweight token-overlap heuristic after stripping
  corporate-suffix noise (SA/PLC/NV/Inc/etc.) — good enough to distinguish
  "Enagas SA" ≈ "Enagas Sociedad Anonima" from an unrelated issuer, without
  a fuzzy-matching dependency.
- Enrichment persistence sink (`cosmos`) is optional dependency-injected;
  when absent, the enrichment rerun (§4) is skipped entirely rather than
  erroring — production `main()` always supplies a real
  `CosmosDBService`, but this let Reuben's non-enrichment-focused test
  classes exercise the provider_symbols correction path in isolation
  without needing to fake/monkeypatch DGI analysis.
- Exit code 3 covers BOTH a failed post-write provider_symbols
  verification AND (per the contract's explicit "informationally, enrichment"
  clause) an enrichment verification failure on an otherwise-successfully-
  corrected security — confirmed via Reuben's
  `test_enrichment_failure_reported_non_fatally_without_rollback`, which
  asserts `exit_code == 3` while also asserting the provider_symbols
  correction itself is NOT rolled back. Non-fatal here means "doesn't abort
  the run or undo durable state," not "exit code stays 0."

### Verification
- `backend/tests/test_repair_provider_symbols.py` (Reuben, independent
  authorship): 31/31.
- `backend/tests/test_provider_symbols.py`: 72/72 (no regression — this
  repair only consumes `validate_provider_symbols`, never modifies it).
- `backend/tests/test_repair_pep_security_id.py`: 51/51 (unaffected,
  confirms no cross-script interference).
- `git status --porcelain` confirms only the two new script files were
  added by me; Reuben's test file, and unrelated concurrent AD-repair/
  frontend work already present in the tree, untouched.
- No production execution, no commit/push.

## 2026-09-07 — Movements Global Symbol Search Fix (post-Danny gate)

**Task:** Fix PortfolioMovementsTable.tsx per Danny's rejection gate. Rusty locked out; Linus owns this revision.

**Root cause confirmed:**
- Backend `get_movements()` exact-matches `security_id` (no substring/multi-field support); Python-side slices in memory — no real DB-level pagination limit.
- Old frontend sent `security_id` to backend + filtered the returned 50-row page client-side → partial matches invisible; pagination counts from server's unfiltered `total_count`.

**Strategy chosen:** Danny's mandated client-side full-fetch pattern.
- `FULL_FETCH_LIMIT = 10_000` — safe since backend already loads all matching rows into Python memory before slicing.
- When `securityId.trim()` is non-empty: fetch with `limit=FULL_FETCH_LIMIT, offset=0`, no `security_id` to backend → `allRows`. Apply `matchesMovementSymbol` predicate → `filteredAllRows`. Paginate locally (slice by `offset`/`PAGE_SIZE`). Prev/Next update only `offset` (no re-fetch).
- When `securityId.trim()` is empty: efficient server pagination as before (`limit=50, offset=N`), stored in `serverData`.
- `buildFilter` no longer emits `security_id` — symbol search is always client-side.
- `totalCount` and Prev/Next disabled predicates derive exclusively from `filteredAllRows.length` (client mode) or `serverData.total_count` (server mode) — single source of truth.
- Generation counter (`loadGenRef`) guards all async calls against races/stale responses.
- `onRefresh`, `onCreated`, `handleDelete` updated to pass current `securityId` to `load`.

**Files changed:** `frontend/src/components/PortfolioMovementsTable.tsx` only.
**Backend:** No changes required (Python already fetches all rows into memory; `FULL_FETCH_LIMIT` is safe).
**Validation:** `npx tsc --noEmit` passes with 0 errors.
**Committed:** No (per instructions). Basher to add adversarial multi-page pagination/count tests.

## 2026-09-07 — Large Release Session: Symbol Pricing & Portfolio Views (Orchestration)

**Session Role:** TradingView symbol resolution (international market support)

**Contributions:**
- Implemented `resolve_tradingview_symbol()` in `backend/src/portfolio/tradingview_symbol.py`
- MIC → suffix mapping for TradingView chart embeds (XLON→.L, XETR→.DE, XSWX→.SW, etc.)
- Wired resolution into TradingView embed generation
- Legacy US free-text exchange handling (NASDAQ/NYSE/AMEX fallback)
- Fallback for unknown MICs: bare ticker (fail-soft for embeds)

**Contract:** Danny's TradingView design approval (2026-09-07)

**Release Status:** ✅ Released in b4f8438 — GitHub Actions Run 34155988480 SUCCESS
- 137/137 backend tests passing
- 932/932 frontend tests passing
- Zero regressions (Basher validation)

**Key Design Properties:**
- No network calls from resolution function (table lookup only)
- Fail-soft fallback for unknown MICs (bare ticker, not error)
- Backward compatible (US securities unaffected)
