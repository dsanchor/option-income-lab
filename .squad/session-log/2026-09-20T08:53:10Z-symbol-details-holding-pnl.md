# Session Log: Symbol Details Holding P&L

**Timestamp:** 2026-09-20T08:53:10Z
**Requested by:** dsanchor

## Work consolidated

- Recorded Stocks → Options → Action Plans ordering, with Stocks as the default
  and recognized hashes retaining precedence.
- Recorded the additive Symbol Details portfolio fields
  `current_value_eur`, `unrealized_pnl_eur`, and `unrealized_pnl_pct`.
- Preserved the shared cached-EUR valuation and FIFO remaining-cost-basis
  authority, including null behavior for closed/unpriced holdings and
  non-positive percentage denominators.
- Recorded that the frontend displays backend-authored signed P&L with
  accessible gain/loss/neutral semantics and does not recompute quote-currency
  values.
- Merged and deduplicated the Linus, Livingston, and Basher inbox records, then
  removed the merged files.

## Validation evidence

Final gate: 276 backend tests, 382 frontend tests, 4 valuation edge probes,
TypeScript, changed-file ESLint, production build, Python compile, and
`git diff --check` passed. One existing generated-CSS parser warning remained
non-blocking.

No production or test files were modified by Scribe. No commit was created.
