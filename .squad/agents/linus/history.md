# Linus — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Role:** quantitative strategy, provider contract, prompt, and financial-rules owner
- **Stack:** Python, provider adapters, options-chain math, portfolio accounting rules, React support contracts

## Core Context

- Linus owns deterministic strategy logic, provider evidence normalization, and contract-safe financial calculations.
- Durable themes: earnings gates, DTE/roll policy, options-chain validity rules, screening universe semantics, and holdings/cost-basis math.
- Core implementation style: fail closed on missing evidence, keep JSON/output contracts explicit, and separate derived metrics from raw observed fields.
- History before 2026-09 was condensed on 2026-09-09 into this core summary to control file growth.

## Recent Learnings

- **2026-09-27:** Repaired historical ECB lookup after an `XLON:RKT` dry run
  exposed nine `fx_unavailable` skips. The ECB full-history XML is single-line,
  so bounded parsing must stream XML structure rather than rely on line
  boundaries. The shared service now has hardened cache/timeouts, complete
  1999+ coverage, and actionable skip diagnostics.
- **2026-09-27:** UK Yahoo quotes denominated in pence now normalize `GBp` and
  `GBX` to GBP through the shared authoritative quote normalization used by
  both Dividend · Buy FMV backfill and Yahoo-on-save. This closes UK Partial
  coverage without adding divergent path-specific conversion logic.
- **2026-09-27:** Dividend Economics now aggregates at
  `ca_group_id`/movement-event grain after movement-ID deduplication. Scrip
  economic value is share FMV less share-leg contribution, cash top-ups, and
  attributable fees; cash fiscal totals and YoC remain cash-only. Coverage is
  explicit (`COMPLETE`/`PARTIAL`/`UNAVAILABLE`/`NOT_APPLICABLE`), combined
  totals preserve zero/negative values, and account/symbol/currency filters
  include matched corporate-action events atomically.
- **2026-09-27:** Dividend · Buy create/correction can now request server-only
  `YAHOO_OPEN` valuation by instruction. Endpoints and the backfill share one
  Security Master → provider-symbol → exact/next-session Open → historical ECB
  service, while client-authored Yahoo FMV remains reserved. Yahoo saves resolve
  before writes and commit legs, supersessions, and idempotency record in one
  account-partition transactional batch; failed lookup leaves no partial state.
- **2026-09-27:** Dividend · Buy Yahoo FMV backfill now resolves symbols only
  through the shared resolver, observes unadjusted daily Open on the exact/next
  session within seven calendar days, verifies listing currency, and converts
  with full-history ECB rates carrying the effective publication date. The
  migration is deterministic and dry-run-first, with confirmation-bound
  fingerprints, pre-write full-document backups, ETag CAS, force fencing,
  run-scoped restore, and complete YAHOO_OPEN provenance.
- **2026-09-27:** Symbol movement lot average price is backend-authored from
  strict Decimal `BUY net.eur_amount / positive quantity`, with ZERO_COST
  represented as `0.00`, invalid/incomplete/non-share rows failing closed to
  null, and two-decimal HALF_UP serialization. The same unrounded helper now
  supplies FIFO BUY lot unit cost, while the browser only formats the API field.
- **2026-09-27:** Proposed Dividend · Buy economic valuation at `ca_group_id`
  grain: cash net plus one provenance-qualified share FMV, less investor
  `CASH_TOP_UP` principal and attributable fees. Price/FX source, dates,
  confidence, correction state, and coverage remain explicit; incomplete
  annual values stay partial/unavailable rather than becoming zero. This is an
  analytical proposal only and does not alter FIFO or tax accounting.
- **2026-09-26:** Frontend legacy-rights exclusion now uses one fail-closed
  normalizer across movement lists, import preview, semantic filters, symbol
  and stock tables, detail/search reads, dividends, economics totals, counts,
  and charts. Marker casing/whitespace, supported aliases, nested source
  payloads, non-zero amounts, malformed/non-finite/object amounts, conflicting
  flags, and unknown explicit sales markers are excluded; explicit zero-only
  amounts remain ordinary unless another rights marker exists. Ordinary SELL,
  cash DIVIDEND, and Dividend · Buy semantics remain intact.
- **2026-09-24:** Historical-rights writes are fenced in the portfolio account
  partition: every ledger mutation transactionally CAS-replaces the lease
  document and performs exactly one create/replace/delete. Lease ID, monotonic
  fencing token, expiry renewal, and per-document fence ownership prevent stale
  writes and cross-fence compensation; prior-fence orphans are quarantined.
- **2026-09-24:** Rights previews canonicalize all financial leaves to bounded
  six-decimal non-negative values before hashing and recheck A/B/C equations.
  Backup journal schema v1 is recursively fail-closed, and discovery is indexed,
  horizon-bounded, candidate-capped, deterministic, and paginated.
- **2026-09-08:** Fixed the ECB FX parser bug where a `continue` skipped same-line `<Cube currency=...>` rates, emptying the non-EUR cache.
- **2026-09-07:** Re-established backend-authored `us_options_eligible` and `screener_eligible` booleans so the frontend no longer re-implements screener universe logic.
- **2026-09-06:** Locked portfolio summary cost basis to true CMP residual cost rather than `purchases - sales` arithmetic.
- **2026-09-03:** Continued the six-state Buy Tracker direction: deterministic evidence, explicit hard gates, and signed-score semantics.
- **2026-09-19:** Frontend linkable-position contexts now use a dedicated `LinkablePositionTxnType` so stock `BUY`/`SELL` can query assigned puts/calls without widening `OptionTxnType`; picker selections pass the full candidate so stock corrections synchronize `ASSIGNMENT_STOCK` and candidate option type while manual ID entry remains unchanged.
- **2026-09-19:** User-data backup frontend now keeps export downloads bound to the current server preview fingerprint, forwards ZIP bytes and backup headers unchanged, preserves multipart archive uploads, and exposes automatic “Run now” only when an explicit backend capability advertises an authenticated operator path.
- **2026-09-19:** Backup infrastructure now explicitly selects its dedicated UAMI through `AZURE_CLIENT_ID`, merges anchor-safe lifecycle rules without replacing unrelated policies, guards image-only workflow updates against identity/env drift, and distinguishes local provisioning contracts from live Azure smoke acceptance.
- **2026-09-19:** Backup import validation now enforces production transfer pair and corporate-action group invariants, including reciprocal peers, distinct source/destination accounts, required CA legs, and authoritative leg-to-transaction mappings; scheduled failures merge health state so prior success and latest changed archive remain durable.
- **2026-09-20:** Options Economics computes the last-12-month non-zero average once and shares that exact nullable value between the KPI card and monthly cash-flow chart; the chart omits the average line when the card has no value and preserves valid negative or exact-zero averages.
- **2026-09-20:** Symbol Details cannot safely derive holding unrealized P&L from `enrichment.metrics.current_price` because it is quote-currency data while FIFO residual cost is EUR; the detail portfolio contract must expose authoritative `current_value_eur` (or nullable unrealized EUR/percent fields) using the same pricing-cache semantics as Symbols Overview.
- **2026-09-20:** Symbol Details now defaults to Stocks while exact `#options`, `#stocks`, and `#action-plans` deep links override that default; listening to both `hashchange` and `popstate` keeps tab selection synchronized with browser navigation without discarding query parameters.
- **2026-09-20:** Symbol Details holding P&L consumes backend-authored `unrealized_pnl_eur` and `unrealized_pnl_pct` directly, preserving shared EUR pricing and FIFO cost semantics; frontend display suppresses closed holdings, treats invalid values as unavailable, and keeps absolute P&L visible when only the percentage is unavailable because cost basis is zero or unknown.
- **2026-09-22:** Scrip share FMV is the `SHARE_ACQUISITION.gross` amount and its EUR equivalent is the FIFO acquisition basis, not dividend income or `CASH_TOP_UP`; the wizard had forced `gross.eur_amount` to `"0"` for EUR share legs. Positive FMV now round-trips as `COMPLETE`, explicit zero as `ZERO_COST`, while blank/legacy unknown zero remains `INCOMPLETE`.
- **2026-09-23:** Movement type presentation must combine authoritative CA metadata with the stored transaction type: `BUY` + `SHARE_ACQUISITION` under `SCRIP_DIVIDEND` or `DIVIDEND_WITH_SCRIP` renders `Dividend · Buy`; ordinary buys, rights issues, cash-dividend legs, and records without CA metadata retain their base labels.
- **2026-09-24:** User-facing Dividend movement filters require semantic membership: dividend-derived `BUY` share-acquisition legs match both Buy and Dividend. Because the API `txn_type` filter is exact, Dividend-filtered surfaces fetch candidates, classify with `ca_leg_type` + `ca_event_type`, and paginate the filtered result client-side.
- **2026-09-24:** Scheduler status now preserves `last_run` as the latest
  attempt start for every legacy consumer while exposing explicit
  `last_attempt`, `last_success`, and `last_error` fields. Dashboard Banner
  Settings alone uses successful-generation time (`generated_at` or
  `last_success`), so failures remain visible without replacing a prior
  success or turning an initial `Never` into a false completion.
- **2026-09-24:** Dashboard monitor legacy identity matching now fails closed:
  paper/real accepts only booleans and normalized `true`/`false` strings,
  every non-empty top-level/source alias is normalized and conflict-checked,
  malformed values are incompatible, and an exact `position_id` must still
  agree with all explicit identity constraints. Incompatible records remain
  unassigned in monitor rows while staying visible in global Activities.
- **2026-09-24:** Manual position-monitor execution is now addressed by stable
  `position_id` from the clicked dashboard row through the BFF, API trigger,
  in-flight/run state, monitor wrapper, runner prompt, snapshots, activities,
  alerts, and refresh. The backend reloads and validates the exact active
  position (including account, contract, type, strike, expiry, and paper lane);
  stale or mismatched IDs and ambiguous symbol-only requests fail closed.
  Scheduled monitoring still iterates every active position independently.
- **2026-09-25:** Dashboard Banner technical recommendations now fail closed:
  recommendation labels of any polarity are eligible only with a finite
  normalized score, at least one non-zero calculated signal count, finite
  measured technical evidence, and an actual recent provider history
  timestamp. Moving-average recommendations additionally require finite
  moving-average indicator evidence. Case/nesting/alias normalization cannot
  turn bare `NEUTRAL`, `BUY`, `SELL`, or default text into a fact, count, or
  watermark.
- **2026-09-25:** Simulate a Roll is an exact-position, exact-contract,
  read-only calculation. Both close and open legs require positive,
  non-crossed two-sided markets and use the repository robust midpoint; the
  signed target-minus-current result scales by the explicit 100-share
  US-options multiplier and the full positive integer `position.contracts`.
  Missing quantity, missing exact contracts, invalid quotes, inactive
  positions, and non-US eligibility fail closed, while quote timestamps,
  source, stale/carried status, and field status remain visible.
