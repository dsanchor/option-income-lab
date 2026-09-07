# Danny — Point Gate: Movements Batch-Loop Dedup (Reuben's third-author revision)

**Verdict: REJECTED (narrow, single defect)**

## Scope
- `frontend/src/components/PortfolioMovementsTable.tsx` (Reuben revision)
- `frontend/tests/movementsMultiPageSearch.test.mjs` (Basher, 36 new adversarial tests)

## Confirmed correct (source-verified)
- `SEARCH_BATCH_SIZE = 500`; `load()`'s batch branch loops `getMovements({ ...filter, limit: SEARCH_BATCH_SIZE, offset: batchOffset })`, incrementing `batchOffset` by `SEARCH_BATCH_SIZE`, terminating on `accumulated.length >= reportedTotal` (first page's `total_count`) or a short page (`page.movements.length < SEARCH_BATCH_SIZE`).
- Generation guard (`gen !== loadGenRef.current`) is checked **before** each fetch, **after** each awaited response, and again immediately before the final `setState` commit — a new search correctly invalidates an in-flight batch at every step.
- `FULL_FETCH_LIMIT` hard cap removed entirely; no ceiling left in the completeness path (confirmed no remaining reference in source).
- Incompleteness detection (`searchIncomplete`) only fires on genuine post-exhaustion mismatch (`accumulated.length < reportedTotal` after the loop legitimately exits), never merely from crossing a size threshold — verified via LB-9a/b/c.
- Filter isolation, query-clear-reverts-to-server-pagination, and no-N+1 (`Math.ceil(total/batchSize)` exact fetch count) all hold — LB-5/6, LB-10, LB-11.
- Ran the full suite: `movementsMultiPageSearch.test.mjs` **86/87** (matches Basher's report of the sole failure). Frontend full suite reported 931/932 — consistent with the same single failure, not a second regression.

## Confirmed defect — no dedup across batch pages (LB-4c)

Read the loop body directly: `accumulated.push(...page.movements)` — no `Set`/`Map`/id-based guard of any kind before appending each page. This is a real production risk, not a hypothetical: the backend (`backend/src/portfolio/cosmos_portfolio.py::get_movements`) re-runs `SELECT * FROM c WHERE {where} ORDER BY c.trade_date DESC` fresh on every call and slices `all_items[offset:offset+limit]` in Python — there is no stable continuation token or secondary sort key. `trade_date` alone is not unique (multiple movements routinely share a trade date, e.g. a BUY and a same-day DIVIDEND, or bulk-imported CSV rows). A concurrent insert/edit/delete between batch calls, or simply multiple rows sharing a `trade_date` at a page boundary, can shift which rows fall into which `offset` window between calls — causing the same movement to appear in two consecutive pages (duplicate) or, in the reverse case, to be skipped. This directly violates the mandated "append stable order without duplicates" requirement from the prior gate. Test evidence: `LB-4c` fails against the current source, and no other test in the 36-test batch covers this path (86/87 is exactly the dedup gap, nothing else).

## Assignment (lockout enforced — fourth eligible agent required)
- **Rusty locked out** (original artifact, rejected in the first gate of this chain).
- **Linus locked out** (first revision, rejected in the second gate of this chain).
- **Reuben locked out** (current revision under review here, rejected).
- **Product fix → Livingston.** Livingston is the fourth eligible agent and owns this narrow revision.
- **Basher remains test owner** (his test artifact was not rejected — LB-4c correctly caught the defect it was written to catch).

## Exact required fix

Deduplicate by **`movement.id`** — the canonical Cosmos document identifier, which is required/non-null on `LedgerMovement` and is already relied upon elsewhere in this same component as a unique key (`key={m.id}` row rendering, `onDelete(m.id, ...)`, `MovementDetailDialog` keyed access). Do not use any composite/derived key built from `security_id`/`trade_date`/`gross` or similar — this codebase's own `PROBABLE_DUPLICATE` warning classification (seen in earlier reviews this session) exists specifically because distinct, legitimate movements can share those attributes; only `id` is schema-guaranteed unique per document.

Implementation: track a `Set<string>` of seen `id`s across the loop (or dedup the fully accumulated array once via a `Map` keyed by `id`, preserving first-seen order) before it is used for `filteredAllRows`/`displayedRows`/counts. Append order must remain stable (first occurrence wins; do not reorder by re-sorting after dedup).

### Termination and count-mismatch semantics — must change together with dedup
- **Termination condition is unaffected**: the loop must still stop on `accumulated.length >= reportedTotal` (pre-dedup accumulated length) or a short page — do **not** base loop continuation on the *deduplicated* count, or a batch containing only duplicates could cause an infinite/extra fetch loop chasing a `reportedTotal` it can structurally never reach post-dedup.
- **The incompleteness mismatch check must run on the deduplicated set**, not the raw accumulated count: compare `dedupedRows.length` against `reportedTotal` only after understanding that a `dedupedRows.length < reportedTotal` outcome caused purely by legitimate duplicate removal is **expected and not an error** — it must not trip `searchIncomplete`. Only trip the warning when the *raw* `accumulated.length < reportedTotal` after loop exhaustion (the existing check), independent of dedup. In other words: keep the existing `searchIncomplete` computation exactly as-is against the raw accumulated array; apply dedup as a separate, subsequent step purely to the array that feeds `allRows`/`filteredAllRows`, so the two concerns (genuine incompleteness vs. duplicate removal) never conflate.

### Test requirement (Basher — no lockout, extend in place)
Add a case where two batch pages return an overlapping `id` (simulating offset drift under concurrent write) and assert: (a) the final `allRows`/result set contains exactly one instance of that `id`; (b) `searchIncomplete` is **not** falsely triggered by the dedup removing rows; (c) row order remains stable (first-seen position retained, not re-sorted). Do not weaken or remove the existing 86 passing assertions.

No product code modified by this review. No production calls made.
