# Danny — Final Point Gate: Movements Batch-Loop Dedup (Livingston's fourth-author revision)

**Verdict: APPROVED**

## Scope
- `frontend/src/components/PortfolioMovementsTable.tsx` (Livingston revision — sole file touched)
- `frontend/tests/movementsMultiPageSearch.test.mjs` (Basher's artifact, unchanged this turn)

## Lockout compliance
Confirmed `git status` shows `movementsMultiPageSearch.test.mjs` **not modified** in this turn — Basher's test file (already approved as test-owner artifact) was left untouched, consistent with "test unchanged" report. Only the product file carries new changes. Chain: Rusty (original) → Linus (first revision) → Reuben (second revision) all locked out and correctly not touched again; Livingston is the fourth-author and the only diff owner here.

## Verified fix
Read the actual `load()` batch branch directly:
- `seenIds: Set<string>` + `dedupedRows: LedgerMovement[]` added alongside the pre-existing `accumulated` array.
- Dedup key is `m.id` — the canonical, schema-required Cosmos document identifier, exactly as mandated (not a composite/derived key).
- First-seen order preserved: `dedupedRows.push(m)` only on first sighting of an `id`; subsequent occurrences of the same `id` are skipped with an explicit comment ("page-boundary duplicate — skip, first-seen row is canonical").
- Rows with a missing/falsy `id` are **retained, not collapsed** — pushed to `dedupedRows` unconditionally, exactly per the "missing-id rows retained" requirement, avoiding any risk of silently dropping legitimate distinct rows behind a falsy-id false match.
- **Count semantics correctly separated**: loop termination (`while (reportedTotal === null || accumulated.length < reportedTotal)`) and the defense-in-depth `incomplete` mismatch check both still key off the **raw** `accumulated.length`/`reportedTotal` — untouched by dedup. `setAllRows(dedupedRows)` is the only place the deduplicated set is used, feeding `filteredAllRows`/`displayedRows`/`totalCount` for display, symbol matching, and local pagination. This exactly matches the required split: termination/warning semantics immune to dedup, display/count semantics reflect the deduplicated set.
- Generation guard, error handling (`if (gen !== loadGenRef.current) return` before/after each await and before final commit), filter isolation (`security_id` still excluded from `buildFilter()`), and query-clear-reverts-to-server-pagination are all untouched — confirmed identical to the previously-approved logic.

## No unrelated changes
`git status` confirms `PortfolioMovementsTable.tsx` is the only file with new changes in this turn; all other modified/untracked files belong to earlier, already-approved batches (Symbol Configuration, pricing, screener dropdown, Economics styling, chat rename, etc.).

## Test evidence (independently run, not trusted from report)
- `movementsMultiPageSearch.test.mjs` + `movementsSymbolFilter.test.mjs` together: **154/154 pass**, 0 fail — includes `LB-4c` now passing (matches Livingston's 87/87 claim for the target file alone).
- `npx tsc --noEmit`: clean, zero errors — matches "tsc clean" report.

## Closing status
This closes the entire Movements global-symbol-search defect chain (client-side-filter-on-one-page → capped single fetch → uncontrolled batch loop → dedup gap → this fix) as **fully APPROVED**. Combined with the prior approvals in `danny-movements-search-summary-chat-final-gate.md` (StockTransactionsTable, NativeSelect, Summary single-card, Options Chat rename, Economics styling), the full final UI point-gate batch is now closed.

No product code modified by this review. No production calls made.
