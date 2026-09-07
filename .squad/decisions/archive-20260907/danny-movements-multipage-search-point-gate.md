# Danny — Point Gate: Movements Multi-Page Symbol Search (revision of rejected artifact)

**Verdict: REJECTED (narrow, one architectural gap)**

## Scope
- `frontend/src/components/PortfolioMovementsTable.tsx` (Linus revision)
- `frontend/tests/movementsMultiPageSearch.test.mjs` (Basher, new)

## Confirmed correct

1. **Multi-page completeness / pagination coherence** — when a symbol query is active, `load()` issues one `getMovements({ ...filter, limit: FULL_FETCH_LIMIT, offset: 0 })` call (no `security_id` sent), stores the result in `allRows`, and `filteredAllRows` applies `matchesMovementSymbol` over the *complete* filtered-by-account/type/date set. `displayedRows` slices `filteredAllRows`, and `totalCount = filteredAllRows !== null ? filteredAllRows.length : serverData?.total_count`. Footer text and both Prev/Next `disabled` predicates now read from this single `totalCount` — verified directly in source (lines ~170, ~403-430). No path left using the stale unconditional `data.total_count`.
2. **Filter isolation** — `buildFilter()` still excludes `securityId` from the server request; account/type/date narrow the fetched universe before the client-side symbol predicate runs. Confirmed via source and `FL-1..FL-4`.
3. **Races** — `loadGenRef` generation counter is checked after every `await` in `load()` (both branches) before any `setState`, correctly discarding stale responses. Confirmed via source and `ST-1..ST-3`.
4. **Query clear / mode switch** — `resetFilter()` and an emptied `securityId` both route through the `q` (empty) branch of `load()`, reverting to normal `serverData`-driven single-page pagination; `filteredAllRows` becomes `null` again so `displayedRows` falls back to `serverData.movements`. Confirmed via source and `CL-1..CL-4`.
5. **No N+1** — exactly one network call per load, regardless of mode. Confirmed.
6. **No unrelated changes** — diff is scoped to the fetch/pagination/state logic; no Telegram, account-badge, or unrelated UI changes present.
7. **Endpoint does not silently cap below 10,000.** Read `backend/src/portfolio/cosmos_portfolio.py::get_movements` directly: the Cosmos `query_items(...)` call has no `max_item_count`/hard cap and is fully materialized via `list(...)` before any Python-side slicing — so the SDK itself will return every document matching the WHERE clause, not a partial page. The only place `limit` is applied is the in-memory slice `items = all_items[offset: offset + limit]`. So `FULL_FETCH_LIMIT=10_000` is not being silently reduced by the backend; it's a real, honest cap.

## Test evidence
- `movementsMultiPageSearch.test.mjs`: **51/51 pass** (matches Basher's report).
- `movementsSymbolFilter.test.mjs` + `symbolDetailMovementsFilter.test.mjs` re-run for regression: **125/125 pass**. Combined with the new suite: **176/176**, 0 fail.

## Blocking issue — undetected truncation above FULL_FETCH_LIMIT

The backend *does* honestly report the true total via the separate `count_query` → `total_count` in the response — but the frontend's full-fetch branch discards it entirely:

```
const d = await getMovements({ ...filter, limit: FULL_FETCH_LIMIT, offset: 0 });
...
setAllRows(d.movements);   // d.total_count is never read here
```

If the number of documents matching the *account/type/date* filter (before the symbol predicate is applied) legitimately exceeds `FULL_FETCH_LIMIT = 10_000` — e.g. a user clears the date range to "All" and searches with no account/type narrowing across many years of a multi-account, dividend-reinvestment-heavy portfolio — the backend's in-memory slice `all_items[0:10_000]` silently drops every document past the 10,000th (ordered by `trade_date DESC`, so the *oldest* matching records in the window are the ones dropped). `d.movements.length` would be 10,000, `d.total_count` would report the true (larger) count, and the frontend uses neither the real total nor any comparison between the two — it just proceeds as if `allRows` were complete. `totalCount` (the footer/Prev/Next source of truth) is computed from the truncated `filteredAllRows.length`, so the UI presents a small, internally-consistent, but factually incomplete result set with **zero signal** that anything was dropped. This is a silent, undetectable correctness failure — the same class of defect (misleadingly "coherent" but wrong) this entire gate exists to close, just relocated to a boundary condition instead of every load.

No test in `movementsMultiPageSearch.test.mjs` exercises this boundary; the suite explicitly accepts `FULL_FETCH_LIMIT` as a valid strategy without ever testing what happens when the true total exceeds it. Data volume in this app is presently modest (dozens of movements per symbol, per the identity-repair reviews this session), so today's risk is low, but the record volume only grows over time on a long-running local tool, and the gap is currently silent-by-design rather than merely low-probability.

### Required fix (narrow, small — reviewer-scale, not a re-architecture)
After the full fetch, compare `d.movements.length` against `d.total_count`. If `d.total_count > d.movements.length` (i.e. truncation occurred), surface it explicitly rather than silently proceeding — e.g. set a visible warning/error state ("Search results may be incomplete — narrow the date range or account filter and try again") and/or log it, instead of quietly rendering a false sense of completeness. A full batching loop is not required — detection-and-disclosure is sufficient and matches the size of the remaining gap.

### Assignment correction (lockout enforcement)

Original assignment to Rusty was invalid: Rusty authored the original client-search artifact rejected in the prior gate and remains locked out of this chain; Linus authored the revision rejected in this gate and is also locked out. Per strict lockout, the next revision owner must be a third agent who has not touched `PortfolioMovementsTable.tsx` in this rejection chain.

- **Linus locked out** (author of the revision rejected in this gate).
- **Rusty locked out** (author of the original artifact rejected in the prior gate; same file, same defect chain).
- **Product fix → Reuben.** Reuben owns the next revision of `PortfolioMovementsTable.tsx`.
- **Test → Basher** (unchanged — his `movementsMultiPageSearch.test.mjs` artifact was not rejected, so he remains eligible).

### Exact required behavior (superseding "warning-only is sufficient")

On reflection, a warning-only fix is **not** acceptable as the final answer — it would permanently accept silent data loss as a known, tolerated failure mode in a tool that accumulates ledger history indefinitely; "the risk is low today" is not a stable invariant to build on. The correct fix is to **guarantee completeness**, not merely disclose its absence:

1. **Batch-fetch until exhausted**, replacing the single capped `FULL_FETCH_LIMIT=10_000` call: loop `getMovements({ ...filter, limit: PAGE_SIZE_FETCH, offset })` (a reasonably large per-call page, e.g. reuse `FULL_FETCH_LIMIT` or a smaller batch such as 1,000 for lower peak memory) incrementing `offset` after each call, accumulating into `allRows`, until either (a) the returned page length is shorter than the requested batch size, or (b) `allRows.length >= d.total_count`. This guarantees the full account/type/date-filtered universe is retrieved regardless of its size — no silent truncation at any threshold.
2. **Defense in depth — keep the disclosure check too.** Even with batching, retain an explicit post-loop assertion comparing `allRows.length` to the final `total_count` observed across calls (they must match once the loop terminates by exhaustion). If they still don't match (e.g. concurrent writes during the batch shifted results), surface a non-silent, visible warning ("Results may be incomplete — data changed while loading; try again") rather than proceeding as if nothing happened. This check must never be satisfied merely by hitting a fetch limit — only by genuine exhaustion of the result set.
3. **No artificial cap left in the completeness path.** `FULL_FETCH_LIMIT` (or equivalent) may still bound the *size of each batch request*, but must never bound the *total* number of rows the search can see. Remove any code path where a single capped call is treated as "the full dataset" without a completeness guarantee.
4. Preserve every previously-approved behavior unchanged: filter isolation (`security_id` never sent), stale-response generation guard (must now also protect the batch loop itself — a new load must invalidate/abort an in-flight batch, not merely the final `setState`), query-clear reverting to normal server pagination, and no N+1 pattern beyond the necessary sequential batch calls for the active search only (never per-row).

### Test requirement (Basher, unchanged eligibility)
Extend `movementsMultiPageSearch.test.mjs` to assert the batch loop's termination condition (short page or `allRows.length >= total_count`), that it makes multiple calls when the dataset exceeds one batch, that a mid-batch new search invalidates the in-flight batch via the generation guard, and that a genuine mismatch after exhaustion (not merely hitting a limit) triggers the visible incompleteness warning. Do not accept a test that only proves `FULL_FETCH_LIMIT` was requested once — that no longer represents the required behavior.

No product code modified by this review. No production calls made.
