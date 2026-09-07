# Final Reviewer Gate — Symbol Configuration / Investments Menu / Options Screener Dropdown

**Reviewer:** Danny (Lead)
**Verdict: REJECTED** (narrow scope — Options Screener dropdown universe-parity only)

## APPROVED portions (no changes required)

1. **Symbol Configuration section** — `cosmos_securities.py::update_security`, `PATCH /api/symbols/{symbol}/security`,
   `POST /api/symbols/{symbol}/enrichment/refresh`, both new BFF routes, `SymbolConfigurationCard.tsx`, and
   `page.tsx` placement all match `danny-symbol-configuration-section-contract.md` exactly: ETag/If-Match
   correctness, identity-field rejection, collision checks, merge-not-replace semantics, fail-closed toggle
   disabling on non-US MIC, 502-on-network-failure BFF convention (verified against 5+ existing BFF routes),
   200/`status:"error"` handled-failure shape for enrichment refresh. `backend/tests/test_symbol_configuration_section.py`:
   **41/41 passed**. `frontend/tests/symbolConfigurationCard.test.mjs`: passing (part of the 48/48 combined run).

2. **Investments menu rename** — `TopNav.tsx` confirms label-only change (`"Investments"` dropdown), children
   unchanged: Symbols / Movements / Accounts / Calendar / Action Plans. Correct, no route restructuring.

## REJECTED: Options Screener dropdown universe parity

Two compounding, high-confidence defects in `frontend/src/components/OptionsScreenerView.tsx::isScreenerEligible`,
confirmed against the actual backend `/api/symbols/overview` implementation (`_compute_symbols_overview` in
`backend/web/app.py`), not just the isolated frontend file:

### Blocker 1 — MIC eligibility check is universally inert today (not a rare fallback)
`isScreenerEligible` only excludes when `r.us_options_eligible === false`, intentionally skipping the MIC gate
when the field is absent ("fail open... never silently empty"). This was framed as a defensive fallback, but
`_compute_symbols_overview`'s row-building loop (`backend/web/app.py` ~lines 795-824) **never sets
`us_options_eligible` on any row at all** — confirmed via full-function scan, zero references to
`us_options_eligible`, `exchange_mic`, or `is_us_options_eligible` anywhere inside that function. Every row
returned by `/api/symbols/overview` today has `us_options_eligible === undefined`, meaning the MIC/exchange gate
is **always skipped in production**, not occasionally. The dropdown currently admits non-US symbols (e.g.
XMAD/XLON/XETR) indiscriminately whenever they have shares>0 or a watchlist-shaped signal. This directly
violates the user's explicit requirement ("only exact eligible securities") and the codebase-wide fail-closed
convention (`is_us_options_eligible`, `enforce_us_options_eligible`, and this same batch's own
`SymbolConfigurationCard` toggle-disable logic all fail closed on missing/falsy eligibility).

### Blocker 2 — watchlist-membership proxy diverges from the backend's `is_watchlist_member` predicate
`isScreenerEligible`'s `is_auto_enrolled === false` / `row_source === "watchlist"` checks are a client-side
reimplementation, not the shared predicate (`not auto_enrolled OR any watchlist.{covered_call,cash_secured_put,
buy_tracker} toggle true OR telegram_notifications_enabled true`, per `watchlist_membership.py` and the
archived `danny-options-screener-universe-contract.md`). Concrete failing case: a portfolio-held symbol with
zero current shares but an active `covered_call`/`cash_secured_put` toggle gets `row_source: "portfolio"` or
`"both"` (never `"watchlist"`) and `is_auto_enrolled: true` — the backend's real `is_watchlist_member` returns
`true` for this security (qualifies for the screener universe per the approved contract), but
`isScreenerEligible` returns `false`, wrongly excluding it from the dropdown. `row_source`/`is_auto_enrolled`
are legacy list-section fields, not a faithful mirror of explicit-membership semantics.

### Test coverage masks both defects
`frontend/tests/optionsScreenerUniverse.test.mjs`'s "fallback when us_options_eligible absent" suite codifies
the fail-open behavior as intentional but never exercises the actual dangerous combination that occurs in
production today: a genuinely non-US MIC symbol with a watchlist-shaped signal (e.g. `row_source: "watchlist"`)
and **no** `us_options_eligible` field at all. Its one negative case
(`{symbol: "XMAD:ITX", portfolio_shares: "0"}`) only passes because of the *unrelated* shares/watchlist clause,
not because the MIC gate correctly excluded it — so the suite gives false confidence. `test_symbol_configuration_section.py`
has zero coverage of `_compute_symbols_overview`/`/api/symbols/overview` row shape (only 1 unrelated match on
`us_options_eligible` string, for a different endpoint), so the missing-field regression has no test anywhere
in the current diff.

## Required fix (architectural direction — do not reimplement predicates client-side)

Per this codebase's established single-source-of-truth pattern (`us_exchange_eligibility.py`,
`watchlist_membership.py`, `options_screener_universe.py` all exist specifically to prevent this kind of drift):

1. **Backend** (`_compute_symbols_overview` in `web/app.py`): add a `us_options_eligible` boolean to each row,
   computed via the existing `is_us_options_eligible(resolved MIC)` — reuse verbatim, do not re-derive MIC logic.
   Additionally expose a single `screener_eligible` boolean per row computed via the existing
   `is_watchlist_member`/screener-universe helpers, so the frontend consumes one authoritative boolean instead
   of reconstructing the AND/OR predicate from `row_source`/`is_auto_enrolled`/`portfolio_shares` client-side.
2. **Frontend** (`OptionsScreenerView.tsx`): replace `isScreenerEligible`'s heuristic body with a direct read of
   the new `screener_eligible` field (fail closed — treat missing/non-`true` as ineligible, no "backend not yet
   deployed" fallback once the backend ships the field in the same PR).
3. **Tests**: `optionsScreenerUniverse.test.mjs` must be rewritten to assert the new direct-boolean consumption
   (including a fail-closed case for missing/undefined `screener_eligible`) rather than testing the old
   reimplemented heuristic. `test_symbol_configuration_section.py` or a new backend test must cover
   `_compute_symbols_overview` emitting correct `us_options_eligible`/`screener_eligible` per row, including the
   zero-shares-but-toggle-active case.

## Ownership under lockout

- `frontend/src/components/OptionsScreenerView.tsx` — original author is Rusty (per the standing frontend
  assignment on this batch). Rusty is **locked out** from this revision cycle on this specific defect;
  assign the backend field addition + frontend consumption fix to **Linus** (backend field) and **Rusty is
  locked out only for `OptionsScreenerView.tsx`** — assign that frontend change to a different eligible agent,
  e.g. **Reuben**, since this is a fresh divergence finding, not a Reuben-authored artifact.
- `frontend/tests/optionsScreenerUniverse.test.mjs` — original author unclear from this batch's framing but
  functionally tied to the same defect; assign rewrite to **Basher** (independent of whoever revises the
  product code).
- Backend `_compute_symbols_overview` change — assign to **Linus**.

## Not blocking (informational only)
- `PortfolioHoldingsCard`/`page.tsx`/`SymbolConfigurationCard.tsx`/BFF routes/Investments rename: fully approved,
  no further action needed.
