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
