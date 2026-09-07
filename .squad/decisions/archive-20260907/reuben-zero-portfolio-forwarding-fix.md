# Reuben → Team: Fix for Danny's `include_zero_portfolio` Rejection

**Date:** 2026-09-07
**Author:** Reuben (independent Frontend Specialist, escalated review — Rusty locked out of this revision)
**Status:** IMPLEMENTED — tests green, TypeScript clean, production build clean. Not committed/pushed.

---

## Blocker (per Danny)

Backend hides auto-enrolled zero-share rows unless `include_zero_portfolio=true` is
passed on `GET /api/symbols/overview` (see `livingston-unified-watchlist-api-contract.md`
§2). The frontend Symbols page and its Next.js BFF proxy never forwarded that
parameter, so the checked-by-default "Hide historical (0 shares)" toggle had nothing
to reveal when unchecked — the historical rows were never fetched at all.

## Fix

Chose the "fetch inclusive, filter client-side" design (no extra network round trip
when the user toggles the checkbox):

1. **`frontend/src/app/symbols/page.tsx`** — `getData()` now requests
   `"/api/symbols/overview?include_zero_portfolio=true"` instead of the bare path, so
   the server component always receives the full dataset including auto-enrolled
   zero-share rows.
2. **`frontend/src/app/api/symbols/overview/route.ts`** — the BFF proxy now forwards
   the incoming request's query string verbatim to the backend (`apiFetch(
   \`/api/symbols/overview${search}\`)`) instead of dropping it, so any caller
   (including future ones) can request the inclusive dataset through this route.
3. **`frontend/src/components/SymbolsTable.tsx`** — unchanged; its existing
   `isHiddenZeroRow` predicate and `hideZero` toggle already implement the correct
   client-side filtering — the rows simply weren't reaching it before.

## Tests added

`frontend/tests/symbolsOverviewIncludeZeroPortfolio.test.mjs` — unlike the existing
`sharedSymbolFilter.test.mjs` (pure synthetic predicate mirrors), this suite:
- Imports and executes the **real** `route.ts` `GET` handler (via a small custom ESM
  loader that resolves the `@/` alias and shims `next/server`), proving the query
  string is actually forwarded — not just a logic mirror.
- Asserts against `page.tsx`'s real source text that `getData()` requests
  `include_zero_portfolio=true` (JSX prevents direct `import()` of that file under
  Node's native TS type-stripping).
- Runs a full simulated round trip (`RT-1`) against a fake backend that honors
  `include_zero_portfolio`, proving the historical row is invisible without the flag
  (reproducing Danny's exact blocker) and correctly revealed/hidden by the toggle
  once the flag is forwarded.

Verified these tests fail against the pre-fix code (via `git stash` against the two
touched files) and pass after the fix — confirming they would have caught this
integration failure.

## Validation

- `node --test tests/*.mjs` → 392/392 passing (385 baseline + 7 new).
- `npx tsc --noEmit` → clean.
- `npm run build` → clean production build, `/symbols` and `/api/symbols/overview`
  both compile.
- No changes to option eligibility behavior, layout, or unrelated files.
