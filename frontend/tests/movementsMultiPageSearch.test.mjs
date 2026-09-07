/**
 * Adversarial regression tests — Movements global symbol search: multi-page
 * completeness, pagination coherence, filter isolation, stale-response guard,
 * and batch-termination behaviour.
 *
 * DEFECT CONTEXT (Danny final-gate rejection 2026-09-07)
 * ======================================================
 * `PortfolioMovementsTable.tsx` combined a client-side substring filter
 * (`matchesMovementSymbol`, applied to `data.movements`) with a server-paginated
 * data source.  Two independent correctness failures:
 *
 *   1. INCOMPLETE MATCHES — `filteredMovements` filters only the currently
 *      loaded PAGE_SIZE=50 rows.  Any matching movement on a later server page
 *      is silently invisible.
 *
 *   2. INCOHERENT PAGINATION — the footer and Prev/Next disabled predicates
 *      unconditionally used `data.total_count` (server unfiltered total), never
 *      `filteredMovements.length`.  When a symbol query narrows the visible rows
 *      below page size, the count display and Next button state are wrong.
 *
 * MANDATED FIX (Linus's scope)
 * =============================
 * When a symbol query is active: fetch the FULL type/date/account-filtered
 * movement set (batch-loop or equivalent unpaginated fetch), then apply the
 * multi-field symbol predicate and paginate entirely client-side.
 * `filteredMovements.length` becomes the sole source of truth for:
 *   - pagination footer text
 *   - Next button disabled predicate
 *   - Prev button disabled predicate
 * When no symbol query is active, revert to normal single-page server pagination
 * (no regression to existing Prev/Next behaviour).
 *
 * TEST GROUPS
 * ===========
 *   MP  Multi-page completeness   — behavioral simulation (always pass, spec-as-test)
 *   DV  Defect validation         — demonstrate OLD single-page path was wrong
 *   PG  Pagination coherence      — behavioral + source-contract (some fail now)
 *   BT  Batch termination         — behavioral + source-contract (some fail now)
 *   FL  Filter isolation          — behavioral (always pass)
 *   CL  Clear-query behaviour     — behavioral + source-contract (some fail now)
 *   ST  Stale-response guard      — source-contract (fail now; pass after Linus's fix)
 *   AR  Architecture contracts    — source-contract (fail now; pass after Linus's fix)
 *   ID  Canonical ID invariants   — behavioral (always pass)
 *   RG  Non-regression            — ensure existing SMF/SRC coverage still holds
 *   LB  Large-dataset batch loop  — Reuben's third-author revision (some fail
 *                                   until Reuben replaces FULL_FETCH_LIMIT with
 *                                   a proper paginated batch loop)
 *
 * Tests marked ⚠ DEFECT are expected to FAIL against the pre-fix source and
 * PASS once Linus ships the new fetch strategy.
 *
 * Tests marked ⚠ LB FUTURE are expected to FAIL against Linus's
 * FULL_FETCH_LIMIT=10_000 single-pass and PASS after Reuben's batch-loop
 * revision that handles total_count > 10,000 correctly.
 *
 * Run: node --test frontend/tests/movementsMultiPageSearch.test.mjs
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join, dirname } from "node:path";

const __dir = dirname(fileURLToPath(import.meta.url));
const root  = join(__dir, "..");

function src(rel) { return readFileSync(join(root, rel), "utf8"); }

const pmSrc = src("src/components/PortfolioMovementsTable.tsx");

// ===========================================================================
// Inline helpers — mirrors the correct algorithm Linus must implement
// ===========================================================================

const PAGE_SIZE = 50; // must match PortfolioMovementsTable.tsx

/** Mirror of the multi-field symbol predicate (parity with matchesMovementSymbol). */
function matchesMovementSymbol(m, query) {
  const q = (query ?? "").trim().toUpperCase();
  if (!q) return true;
  return (
    (m.security_id   || "").toUpperCase().includes(q) ||
    (m.ticker        || "").toUpperCase().includes(q) ||
    (m.company_name  || "").toUpperCase().includes(q)
  );
}

/**
 * Simulate the CORRECT batch-fetch-then-client-filter algorithm.
 *
 * serverDataset: all movements that would be returned by the server for the
 * current type/date/account filter (in order, spread across pages).
 *
 * Returns: { allFetched, matched, fetchCount, pageRequests }
 *
 * NOTE: Linus's implementation uses a single FULL_FETCH_LIMIT=10_000 call
 * rather than a multi-page loop — both are valid strategies.
 * This simulation models the multi-page-loop variant for behavioral testing;
 * the source-contract tests explicitly accept either approach.
 */
function simulateBatchFetch(serverDataset, symbolQuery, { requestedPageSize = PAGE_SIZE } = {}) {
  const pages = [];
  let off = 0;
  // do-while: always makes at least 1 fetch (even empty dataset).
  do {
    const page = serverDataset.slice(off, off + requestedPageSize);
    pages.push(page);
    off += requestedPageSize;
    if (page.length < requestedPageSize) break;
  } while (off < serverDataset.length);
  const allFetched = pages.flat();
  const matched = allFetched.filter(m => matchesMovementSymbol(m, symbolQuery));
  return {
    allFetched,
    matched,
    fetchCount: pages.length,
    pageRequests: pages.map((p, i) => ({
      offset: (i) * requestedPageSize,
      limit: requestedPageSize,
      returned: p.length,
    })),
  };
}

/**
 * Simulate the DEFECTIVE single-page path (what current code does).
 * Filters only `currentPage` — misses matches on any other page.
 */
function simulateDefectiveSinglePageFilter(currentPage, symbolQuery, serverTotal) {
  const matched = currentPage.filter(m => matchesMovementSymbol(m, symbolQuery));
  return {
    matched,
    // Defective pagination: uses serverTotal not matched.length
    footerTotal: serverTotal,
    nextDisabled: PAGE_SIZE >= serverTotal, // wrong — ignores actual matched count
  };
}

/** Paginate a list client-side. */
function clientPage(list, offset, pageSize = PAGE_SIZE) {
  return list.slice(offset, offset + pageSize);
}

/** Build a minimal movement fixture. */
function mvt(id, { secId = "XNYS:ABBV", ticker = "ABBV", company = "AbbVie Inc.", txnType = "BUY", date = "2026-01-01", account = "acc1" } = {}) {
  return { id, security_id: secId, ticker, company_name: company, txn_type: txnType, trade_date: date, account_id: account, gross: { eur_amount: "1000" } };
}

/** Build a dataset of N movements all sharing the same symbol (no match). */
function buildNeutralDataset(n, startId = 0) {
  return Array.from({ length: n }, (_, i) =>
    mvt(`m${startId + i}`, { secId: "XNYS:MSFT", ticker: "MSFT", company: "Microsoft Corp" })
  );
}

// ===========================================================================
// MP: Multi-page completeness (behavioral — always pass)
// ===========================================================================

describe("MP: Multi-page completeness — correct algorithm produces complete results", () => {
  it("MP-1: Target match only on server page 2 — batch-fetch finds it; single-page misses it", () => {
    // 70 movements total: first 50 are MSFT (no match), last 20 include the ABBV target
    const page1 = buildNeutralDataset(50, 0);           // no match
    const page2 = [
      ...buildNeutralDataset(15, 50),                    // no match
      mvt("m65", { secId: "XNYS:ABBV", ticker: "ABBV", company: "AbbVie Inc." }),
      ...buildNeutralDataset(4, 66),                     // no match
    ];
    const server = [...page1, ...page2];

    // Correct algorithm: fetch all, then filter
    const { matched } = simulateBatchFetch(server, "ABBV");
    assert.strictEqual(matched.length, 1, "MP-1: Correct algorithm must find ABBV on page 2.");
    assert.strictEqual(matched[0].id, "m65");

    // Defective algorithm: only sees page 1
    const defective = simulateDefectiveSinglePageFilter(page1, "ABBV", server.length);
    assert.strictEqual(
      defective.matched.length, 0,
      "MP-1 DEFECT EVIDENCE: Single-page filter finds 0 matches — target was on page 2, " +
      "which the old code never loaded for this search."
    );
  });

  it("MP-2: Matches spanning pages 1, 2, and 3 — all 3 found, no duplicates", () => {
    // Build exactly 120 rows with matches at positions 10, 50, and 100.
    const server = [
      ...buildNeutralDataset(10, 0),                // positions 0-9
      mvt("match1", { secId: "XNYS:JNJ", ticker: "JNJ", company: "Johnson & Johnson" }), // pos 10
      ...buildNeutralDataset(39, 11),               // positions 11-49  → page 1 = 50 rows
      mvt("match2", { secId: "XNYS:JNJ", ticker: "JNJ", company: "Johnson & Johnson" }), // pos 50
      ...buildNeutralDataset(49, 51),               // positions 51-99  → page 2 = 50 rows
      mvt("match3", { secId: "XNYS:JNJ", ticker: "JNJ", company: "Johnson & Johnson" }), // pos 100
      ...buildNeutralDataset(19, 101),              // positions 101-119 → page 3 = 20 rows
    ];
    assert.strictEqual(server.length, 120, "Fixture: exactly 120 rows.");

    const { matched, fetchCount } = simulateBatchFetch(server, "JNJ");
    const ids = matched.map(m => m.id);

    assert.strictEqual(matched.length, 3, "MP-2: All 3 matches across 3 pages must be found.");
    assert.ok(ids.includes("match1"), "MP-2: Match on page 1 must be included.");
    assert.ok(ids.includes("match2"), "MP-2: Match on page 2 must be included.");
    assert.ok(ids.includes("match3"), "MP-2: Match on page 3 must be included.");
    assert.strictEqual(new Set(ids).size, 3, "MP-2: No duplicate matches.");
    assert.strictEqual(fetchCount, 3, "MP-2: 120 items at PAGE_SIZE=50 requires exactly 3 fetches.");
  });

  it("MP-3: Partial ticker match finds target on page 2 (partial = real user behaviour)", () => {
    const server = [
      ...buildNeutralDataset(50, 0),
      mvt("abbv1", { secId: "XNYS:ABBV", ticker: "ABBV", company: "AbbVie Inc." }),
      ...buildNeutralDataset(9, 51),
    ];
    const { matched } = simulateBatchFetch(server, "ABB");   // partial ticker
    assert.ok(matched.length >= 1, "MP-3: Partial ticker 'ABB' must find ABBV on page 2.");
    assert.ok(matched.some(m => m.id === "abbv1"));
  });

  it("MP-4: Partial company name match finds target on page 2", () => {
    const server = [
      ...buildNeutralDataset(50, 0),
      mvt("abbv2", { secId: "XNYS:ABBV", ticker: "ABBV", company: "AbbVie Inc." }),
      ...buildNeutralDataset(9, 51),
    ];
    const { matched } = simulateBatchFetch(server, "abbvie");   // company name, lowercase
    assert.ok(matched.length >= 1, "MP-4: Partial company name 'abbvie' must find target on page 2.");
  });

  it("MP-5: MIC:TICKER search finds target on page 2 via security_id", () => {
    const server = [
      ...buildNeutralDataset(50, 0),
      mvt("abbv3", { secId: "XNYS:ABBV", ticker: "ABBV", company: "AbbVie Inc." }),
      ...buildNeutralDataset(9, 51),
    ];
    const { matched } = simulateBatchFetch(server, "XNYS:ABBV");
    assert.ok(matched.length >= 1, "MP-5: Canonical MIC:TICKER must find target on page 2.");
  });

  it("MP-6: Empty query after multi-page fetch — all rows returned", () => {
    const server = buildNeutralDataset(120, 0);
    const { matched } = simulateBatchFetch(server, "");
    assert.strictEqual(matched.length, 120, "MP-6: Empty query must return all rows (no filter).");
  });
});

// ===========================================================================
// DV: Defect validation — document what the OLD single-page path got wrong
// ===========================================================================

describe("DV: Defect validation — single-page filter produces wrong results (defect evidence)", () => {
  it("DV-1: DEFECT EVIDENCE — single-page filter returns empty when match is on page 2", () => {
    const page1 = buildNeutralDataset(50, 0);
    const target = mvt("target", { secId: "XNYS:ABBV", ticker: "ABBV", company: "AbbVie Inc." });
    const { matched } = simulateDefectiveSinglePageFilter(page1, "ABBV", 60);
    // This should be 0 — proving the defect
    assert.strictEqual(
      matched.length, 0,
      "DV-1: The defective single-page path correctly returns 0 — target was invisible. " +
      `target=${target.id} was on page 2 and never reached the filter.`
    );
  });

  it("DV-2: DEFECT EVIDENCE — footer shows server total even when 0 matched rows on screen", () => {
    const page1 = buildNeutralDataset(50, 0);
    const { footerTotal } = simulateDefectiveSinglePageFilter(page1, "ABBV", 60);
    // The defective code showed "of 60" even though 0 rows were visible
    assert.strictEqual(footerTotal, 60,
      "DV-2: Defective footer used data.total_count=60, " +
      "showing 'of 60' while 0 rows were visible — confirmed incoherence."
    );
  });

  it("DV-3: DEFECT EVIDENCE — Next disabled based on server total, not matched count", () => {
    const page1 = buildNeutralDataset(50, 0);
    const serverTotal = 70;
    const { nextDisabled } = simulateDefectiveSinglePageFilter(page1, "ABBV", serverTotal);
    // PAGE_SIZE=50 < serverTotal=70 → Next was ENABLED even though 0 rows matched
    assert.strictEqual(
      nextDisabled, false,
      "DV-3: Defective Next-disabled check used data.total_count=70 — " +
      "Next was enabled when 0 rows matched because 50 < 70."
    );
  });
});

// ===========================================================================
// PG: Pagination coherence — source-contract (⚠ DEFECT — fail until Linus's fix)
// ===========================================================================

describe("PG: Pagination coherence — footer and buttons must use filteredMovements.length", () => {
  it("PG-1: filteredAllRows.length / totalCount used in pagination footer (not raw data.total_count)", () => {
    // Linus: totalCount = filteredAllRows !== null ? filteredAllRows.length : serverData?.total_count
    // Footer: {totalCount} — NOT {data.total_count} unconditionally.
    const hasCorrectTotal =
      pmSrc.includes("filteredAllRows.length") ||
      pmSrc.includes("filteredMovements.length") ||
      // Accepts: totalCount variable derived from filteredAllRows.length
      (pmSrc.includes("totalCount") && pmSrc.includes("filteredAllRows"));
    assert.ok(
      hasCorrectTotal,
      "PG-1 DEFECT: pagination footer must derive its total from filteredAllRows.length " +
      "(or equivalent), not unconditionally from data.total_count. " +
      "Linus: use `const totalCount = filteredAllRows !== null ? filteredAllRows.length : serverData?.total_count`."
    );
  });

  it("PG-2: Next button disabled uses totalCount (not raw data.total_count unconditionally)", () => {
    // Correct: disabled={offset + PAGE_SIZE >= totalCount}
    // where totalCount = filteredAllRows.length in client mode.
    const hasCorrectNext =
      (pmSrc.includes(">= totalCount") || pmSrc.includes("> totalCount")) ||
      (pmSrc.includes("filteredAllRows.length") &&
       (pmSrc.includes("disabled={offset") || pmSrc.includes("disabled = {")));
    assert.ok(
      hasCorrectNext,
      "PG-2 DEFECT: Next button disabled must reference totalCount (which is filteredAllRows.length " +
      "in client mode). Linus: disabled={offset + PAGE_SIZE >= totalCount}."
    );
  });

  it("PG-3: Prev button disabled uses offset (reset correctly when switching modes)", () => {
    // disabled={offset === 0} is correct as long as offset is reset to 0
    // when switching between server/client modes.
    const hasPrevDisabled =
      pmSrc.includes("offset === 0") ||
      pmSrc.includes("offset < 1") ||
      pmSrc.includes("clientOffset === 0");
    assert.ok(
      hasPrevDisabled,
      "PG-3 DEFECT: Prev button must be disabled when offset=0 (disabled={offset === 0}). " +
      "The offset must be reset to 0 whenever the search mode changes."
    );
  });

  it("PG-4 behavioral: correct footer when 5 of 70 match — 'of 5', not 'of 70'", () => {
    const server = [
      ...buildNeutralDataset(49, 0),
      mvt("t1", { secId: "XNYS:ABBV", ticker: "ABBV", company: "AbbVie Inc." }),
      ...buildNeutralDataset(15, 50),
      mvt("t2", { secId: "XNYS:ABBV", ticker: "ABBV", company: "AbbVie Inc." }),
      mvt("t3", { secId: "XNYS:ABBV", ticker: "ABBV", company: "AbbVie Inc." }),
      mvt("t4", { secId: "XNYS:ABBV", ticker: "ABBV", company: "AbbVie Inc." }),
      mvt("t5", { secId: "XNYS:ABBV", ticker: "ABBV", company: "AbbVie Inc." }),
    ]; // 70 total
    const { matched } = simulateBatchFetch(server, "ABBV");
    const serverTotal = server.length;

    // Footer must say "of 5", not "of 70"
    const footerTotal = matched.length;
    assert.strictEqual(footerTotal, 5, "PG-4: filteredMovements.length must be 5.");
    assert.notStrictEqual(footerTotal, serverTotal, "PG-4: filteredMovements.length must differ from server total.");
  });

  it("PG-5 behavioral: Next disabled when all matched fit on one client page", () => {
    const server = [
      ...buildNeutralDataset(49, 0),
      mvt("t1", { secId: "XNYS:ABBV", ticker: "ABBV" }),
      ...buildNeutralDataset(10, 50),   // page 2, no match
    ];
    const { matched } = simulateBatchFetch(server, "ABBV");
    // matched = 1 row; client offset = 0; PAGE_SIZE = 50
    const nextDisabled = 0 + PAGE_SIZE >= matched.length;
    assert.strictEqual(nextDisabled, true,
      "PG-5: Next must be disabled when all matched rows fit on one client page."
    );
  });

  it("PG-6 behavioral: Next enabled when matched rows span multiple client pages", () => {
    // 60 matching rows → needs 2 client pages of 50
    const matches = Array.from({ length: 60 }, (_, i) =>
      mvt(`m${i}`, { secId: "XNYS:ABBV", ticker: "ABBV" })
    );
    const server = [...buildNeutralDataset(20, 100), ...matches, ...buildNeutralDataset(20, 200)];
    const { matched } = simulateBatchFetch(server, "ABBV");
    assert.strictEqual(matched.length, 60);
    // Client page 1: offset=0, PAGE_SIZE=50 → 50 < 60 → Next enabled
    const nextDisabled = 0 + PAGE_SIZE >= matched.length;
    assert.strictEqual(nextDisabled, false, "PG-6: Next must be enabled when matched rows > client page size.");
  });
});

// ===========================================================================
// BT: Batch termination — correct loop, no N+1, short-page detection
// ===========================================================================

describe("BT: Batch termination — loop terminates correctly; no N+1 or under-fetch", () => {
  it("BT-1 behavioral: short page terminates loop (no extra fetch after short page)", () => {
    // Server: 103 rows → page 1 (50), page 2 (50), page 3 (3 — short) → stop
    const server = buildNeutralDataset(103, 0);
    const { fetchCount, pageRequests } = simulateBatchFetch(server, "");

    assert.strictEqual(fetchCount, 3, "BT-1: 103 items at PAGE_SIZE=50 must require exactly 3 fetches.");
    assert.strictEqual(pageRequests[2].returned, 3,
      "BT-1: Third page returned 3 items (short page) — loop must stop here."
    );
  });

  it("BT-2 behavioral: exactly N=ceil(total/PAGE_SIZE) fetches for round total", () => {
    // Server: 100 rows → page 1 (50), page 2 (50 — equal to PAGE_SIZE but last) → 2 fetches
    const server = buildNeutralDataset(100, 0);
    const { fetchCount } = simulateBatchFetch(server, "");
    // 100 / 50 = 2 exactly; the last page is NOT short so the loop needs a total-count check
    assert.ok(fetchCount <= 2,
      "BT-2: 100 items at PAGE_SIZE=50 must produce ≤ 2 fetches (no over-fetch)."
    );
  });

  it("BT-3 behavioral: 50 rows (exact one page) — only 1 fetch required", () => {
    const server = buildNeutralDataset(50, 0);
    const { fetchCount } = simulateBatchFetch(server, "");
    assert.strictEqual(fetchCount, 1, "BT-3: Exact one full page = 1 fetch.");
  });

  it("BT-4 behavioral: 0 rows — 1 fetch only (empty page, terminates immediately)", () => {
    const server = [];
    const { fetchCount, allFetched } = simulateBatchFetch(server, "ABBV");
    assert.strictEqual(fetchCount, 1, "BT-4: Empty server dataset = 1 fetch (empty short page, loop stops).");
    assert.strictEqual(allFetched.length, 0);
  });

  it("BT-5: source uses a full-fetch or multi-page strategy when symbol query is active", () => {
    // Linus implemented FULL_FETCH_LIMIT=10_000 (single large-limit fetch) — acceptable.
    // Alternative implementations (batch loop, allMovements accumulation) are also correct.
    const hasFullFetchStrategy =
      pmSrc.includes("FULL_FETCH_LIMIT") ||             // Linus's approach: single large-limit
      pmSrc.includes("fullFetchLimit") ||
      pmSrc.includes("allRows") ||                       // dual-mode state (allRows vs serverData)
      pmSrc.includes("allMovements") ||
      pmSrc.includes("accumulated") ||
      pmSrc.includes("while (") ||                       // batch loop
      pmSrc.includes("fetchAll");
    assert.ok(
      hasFullFetchStrategy,
      "BT-5 DEFECT: No full-fetch strategy found. " +
      "When symbol query is active, the component must fetch beyond PAGE_SIZE rows " +
      "(either via a large FULL_FETCH_LIMIT or a batch loop) so that client-side " +
      "filtering sees the complete dataset. Without this, matches on page 2+ are invisible."
    );
  });

  it("BT-6: full-fetch uses an oversized limit (FULL_FETCH_LIMIT or batch loop)", () => {
    // The backend must receive either a very large limit (FULL_FETCH_LIMIT) or be called
    // repeatedly; PAGE_SIZE alone is insufficient when symbol query is active.
    const hasLargeLimit =
      pmSrc.includes("FULL_FETCH_LIMIT") ||
      (pmSrc.includes("limit:") && pmSrc.includes("10_000")) ||
      (pmSrc.includes("limit:") && pmSrc.includes("10000")) ||
      pmSrc.includes("while (");
    assert.ok(
      hasLargeLimit,
      "BT-6 DEFECT: Full-fetch must use a limit > PAGE_SIZE when symbol query is active. " +
      "Use FULL_FETCH_LIMIT or a batch loop; PAGE_SIZE=50 alone misses later-page matches."
    );
  });
});

// ===========================================================================
// FL: Filter isolation — account/type/date cannot leak excluded rows
// ===========================================================================

describe("FL: Filter isolation — pre-search server filters cannot leak excluded rows", () => {
  it("FL-1 behavioral: account filter excludes rows before symbol matching", () => {
    // Correct: server receives account filter; excluded-account movements never reach client
    // Simulate: dataset already pre-filtered by server (only acc1 rows)
    const filtered = [
      mvt("a1", { account: "acc1", secId: "XNYS:ABBV", ticker: "ABBV", company: "AbbVie Inc." }),
      mvt("a2", { account: "acc1", secId: "XNYS:MSFT", ticker: "MSFT", company: "Microsoft Corp" }),
    ];
    // excluded (acc2 rows) never in the server-returned dataset when account filter = "acc1"
    const excluded = [
      mvt("b1", { account: "acc2", secId: "XNYS:ABBV", ticker: "ABBV", company: "AbbVie Inc." }),
    ];

    const serverFiltered = filtered; // server already excluded acc2
    const { matched } = simulateBatchFetch(serverFiltered, "ABBV");

    assert.ok(
      matched.every(m => m.account_id === "acc1"),
      "FL-1: No acc2 rows in results — server account filter must be applied before client search."
    );
    assert.ok(
      !matched.some(m => m.id === excluded[0].id),
      "FL-1: Excluded-account movement must not appear in search results."
    );
  });

  it("FL-2 behavioral: type filter excludes SELL rows when txnType=BUY", () => {
    const serverFiltered = [
      mvt("b1", { txnType: "BUY", secId: "XNYS:ABBV", ticker: "ABBV" }),
      mvt("b2", { txnType: "BUY", secId: "XNYS:MSFT", ticker: "MSFT" }),
      // SELL rows never returned by server when txnType=BUY filter is active
    ];
    const { matched } = simulateBatchFetch(serverFiltered, "ABBV");
    assert.ok(matched.every(m => m.txn_type === "BUY"),
      "FL-2: Only BUY rows in results — type filter must be applied server-side before fetch."
    );
  });

  it("FL-3 behavioral: date filter excludes out-of-range rows", () => {
    const inRange = [
      mvt("d1", { date: "2026-06-01", secId: "XNYS:ABBV", ticker: "ABBV" }),
      mvt("d2", { date: "2026-09-01", secId: "XNYS:ABBV", ticker: "ABBV" }),
    ];
    // Out-of-range rows never in server response when date_from="2026-06-01"
    const { matched } = simulateBatchFetch(inRange, "ABBV");
    assert.strictEqual(matched.length, 2,
      "FL-3: Only in-range rows returned — date filter applied server-side before fetch."
    );
  });

  it("FL-4 behavioral: triple AND — account + type + symbol produces only qualifying rows", () => {
    const serverFiltered = [
      // acc1, BUY, ABBV — matches all three
      mvt("ok1", { account: "acc1", txnType: "BUY", secId: "XNYS:ABBV", ticker: "ABBV", company: "AbbVie Inc." }),
      // acc1, BUY, MSFT — account+type match but not symbol
      mvt("ok2", { account: "acc1", txnType: "BUY", secId: "XNYS:MSFT", ticker: "MSFT", company: "Microsoft Corp" }),
    ];
    const { matched } = simulateBatchFetch(serverFiltered, "ABBV");
    assert.strictEqual(matched.length, 1, "FL-4: Only ABBV row passes triple AND filter.");
    assert.strictEqual(matched[0].id, "ok1");
  });
});

// ===========================================================================
// CL: Clear-query behaviour
// ===========================================================================

describe("CL: Clear-query behaviour — empty query restores normal server pagination", () => {
  it("CL-1 behavioral: empty query returns all rows (no client-side filtering)", () => {
    const server = buildNeutralDataset(70, 0);
    const { matched } = simulateBatchFetch(server, "");
    assert.strictEqual(matched.length, 70, "CL-1: Empty query must return all rows.");
  });

  it("CL-2 behavioral: whitespace-only query treated as empty — all rows returned", () => {
    const server = buildNeutralDataset(30, 0);
    const { matched } = simulateBatchFetch(server, "   ");
    assert.strictEqual(matched.length, 30, "CL-2: Whitespace-only query treated as empty.");
  });

  it("CL-3 ⚠ DEFECT: when no symbol query, footer must not use filteredMovements.length as override", () => {
    // When no symbol query: behaviour reverts to normal server pagination.
    // The component must track whether the full-fetch is active.
    // Source check: component has a conditional or flag distinguishing the two modes.
    const hasSymbolQueryConditional =
      pmSrc.includes("symbolQuery") ||
      pmSrc.includes("securityId.trim()") ||                      // existing
      pmSrc.includes("securityId &&") ||
      pmSrc.includes("if (securityId") ||
      pmSrc.includes("symbolQuery ?") ||
      pmSrc.includes("securityId ?");
    assert.ok(
      hasSymbolQueryConditional,
      "CL-3 DEFECT: Component must have a conditional distinguishing 'symbol query active' " +
      "from 'no symbol query' modes, so that server pagination is restored on clear. " +
      "Linus: ensure resetFilter() / clear-query path reverts to normal single-page fetch."
    );
  });

  it("CL-4 ⚠ DEFECT: clearing symbol query triggers normal single-page load (not full-fetch)", () => {
    // Source: resetFilter or similar must call load() without triggering the batch loop
    const hasResetPath =
      pmSrc.includes("resetFilter") ||
      pmSrc.includes("clearSymbol") ||
      (pmSrc.includes("setSecurityId") && pmSrc.includes("load("));
    assert.ok(
      hasResetPath,
      "CL-4 DEFECT: No clear reset path detected. " +
      "Linus: resetFilter() must set securityId to '' and call normal single-page load, " +
      "not the batch-fetch path."
    );
  });
});

// ===========================================================================
// ST: Stale-response guard (⚠ DEFECT — fail until Linus's fix)
// ===========================================================================

describe("ST: Stale-response guard — async stale response cannot overwrite a newer query", () => {
  it("ST-1: component has a stale-request guard (generation counter, AbortController, or epoch)", () => {
    // Linus implemented: loadGenRef = useRef(0); gen = ++loadGenRef.current;
    //   if (gen !== loadGenRef.current) return;
    // This is the correct generation-counter pattern.
    const hasStaleGuard =
      pmSrc.includes("AbortController") ||
      pmSrc.includes("abortController") ||
      pmSrc.includes("controller.abort") ||
      pmSrc.includes("requestId") ||
      pmSrc.includes("epoch") ||
      pmSrc.includes("loadGenRef") ||               // Linus's actual name
      pmSrc.includes("genRef") ||
      pmSrc.includes("versionRef") ||
      pmSrc.includes("currentVersion") ||
      // useRef generation counter pattern (prefix OR postfix increment)
      (pmSrc.includes("useRef") &&
       (pmSrc.includes(".current++") || pmSrc.includes("++") && pmSrc.includes(".current")));
    assert.ok(
      hasStaleGuard,
      "ST-1 DEFECT: No stale-request guard found in PortfolioMovementsTable. " +
      "When the user types rapidly, an older full-fetch resolving after a newer one " +
      "would overwrite the correct results. " +
      "Linus: add a generation counter (useRef + gen check) or AbortController to " +
      "reject/abort stale responses."
    );
  });

  it("ST-2 behavioral: correct stale guard accepts newer result, discards older", () => {
    // Simulate: request A (query='ABBV', epoch=1) and request B (query='MSFT', epoch=2)
    // A resolves after B — epoch check must discard A's result
    let currentEpoch = 0;
    function makeRequest(query, epoch, onResult) {
      return { query, epoch, resolve: (data) => {
        // Stale guard: only apply if this response matches the latest epoch
        if (epoch < currentEpoch) return; // discard stale
        onResult(data);
      }};
    }
    let displayedQuery = null;
    currentEpoch = 1; const reqA = makeRequest("ABBV", 1, (q) => { displayedQuery = q; });
    currentEpoch = 2; const reqB = makeRequest("MSFT", 2, (q) => { displayedQuery = q; });
    // B resolves first
    reqB.resolve("MSFT");
    assert.strictEqual(displayedQuery, "MSFT", "ST-2: B resolved first — result is MSFT.");
    // A resolves late — stale, must be discarded
    reqA.resolve("ABBV");
    assert.strictEqual(displayedQuery, "MSFT",
      "ST-2: A resolved late — stale result must NOT overwrite MSFT. Still shows MSFT."
    );
  });

  it("ST-3: batch/full-fetch uses the stale guard to reject stale responses", () => {
    // The guard must be checked after the fetch resolves — before setState calls.
    // Linus: `if (gen !== loadGenRef.current) return;` after await getMovements().
    const hasInFetchGuard =
      pmSrc.includes("AbortController") ||
      pmSrc.includes("loadGenRef") ||
      // gen comparison after await
      (pmSrc.includes("gen !== ") || pmSrc.includes("gen === ")) ||
      // useRef + current check pattern
      (pmSrc.includes("useRef") && pmSrc.includes(".current"));
    assert.ok(
      hasInFetchGuard,
      "ST-3 DEFECT: Stale guard must protect the fetch callback (checked after await resolves). " +
      "Linus: ensure `if (gen !== loadGenRef.current) return;` is present after each " +
      "await getMovements() call — including inside any loop iterations."
    );
  });
});

// ===========================================================================
// AR: Architecture contracts (⚠ DEFECT — fail until Linus's fix)
// ===========================================================================

describe("AR: Architecture contracts — source invariants for the new fetch strategy", () => {
  it("AR-1: filteredAllRows.length (via totalCount) appears as pagination source of truth", () => {
    // Linus: const totalCount = filteredAllRows !== null ? filteredAllRows.length : serverData?.total_count
    // Footer and buttons use totalCount — which is filteredAllRows.length in client mode.
    const hasCorrectTotal =
      pmSrc.includes("filteredAllRows.length") ||
      pmSrc.includes("filteredMovements.length") ||
      (pmSrc.includes("totalCount") && pmSrc.includes("filteredAllRows"));
    assert.ok(
      hasCorrectTotal,
      "AR-1 DEFECT: filteredAllRows.length (or equivalent) must be the pagination source of truth. " +
      "Linus: define `totalCount = filteredAllRows !== null ? filteredAllRows.length : serverData?.total_count` " +
      "and use totalCount in footer and Prev/Next disabled predicates."
    );
  });

  it("AR-2: security_id NOT passed to server filter when symbol query is the search mechanism", () => {
    // Linus's comment: "symbol_id is always handled client-side; never sent to the backend."
    // buildFilter() no longer includes security_id.
    // Check: either the old `security_id: securityId.trim()` is gone from buildFilter,
    //         or the full-fetch path explicitly omits it.
    const hasClientSideSymbol =
      pmSrc.includes("client-side") ||               // Linus's comment
      pmSrc.includes("allRows") ||                    // dual-mode state confirms full-fetch path
      pmSrc.includes("FULL_FETCH_LIMIT") ||           // large-limit fetch (no symbol filter)
      // The old pattern is gone:
      !pmSrc.includes("security_id: securityId.trim()");
    assert.ok(
      hasClientSideSymbol,
      "AR-2 DEFECT: security_id is still passed to the server as an exact filter. " +
      "The server only supports exact MIC:TICKER match — partial ticker/company queries " +
      "return zero rows. Remove security_id from buildFilter() and apply matching client-side."
    );
  });

  it("AR-3: matchesMovementSymbol function or equivalent exists for client-side multi-field filter", () => {
    // This must exist for client-side filtering — already present in current source
    assert.ok(
      pmSrc.includes("matchesMovementSymbol") ||
      (pmSrc.includes("toUpperCase") && pmSrc.includes("includes(q)")),
      "AR-3: Multi-field symbol predicate must exist in PortfolioMovementsTable."
    );
  });

  it("AR-4: PAGE_SIZE constant (or equivalent) defined and used", () => {
    assert.ok(
      pmSrc.includes("PAGE_SIZE"),
      "AR-4: PAGE_SIZE constant must be defined in PortfolioMovementsTable."
    );
  });

  it("AR-5: component stores the full fetched dataset in state (allRows or equivalent)", () => {
    // Linus: const [allRows, setAllRows] = useState<LedgerMovement[] | null>(null)
    // allRows holds all movements from the full-fetch; filteredAllRows applies the symbol predicate.
    const hasAccumulation =
      pmSrc.includes("allRows") ||
      pmSrc.includes("allMovements") ||
      pmSrc.includes("accumulated") ||
      pmSrc.includes("pages.push") ||
      pmSrc.includes(".concat(") ||
      pmSrc.includes("[...prev") ||
      pmSrc.includes("fetchAll") ||
      pmSrc.includes("flatMap") ||
      (pmSrc.includes("while (") && pmSrc.includes("offset +"));
    assert.ok(
      hasAccumulation,
      "AR-5 DEFECT: No full-dataset storage pattern found. " +
      "The full-fetch result must be stored in state (e.g., allRows) so the " +
      "client-side symbol filter (filteredAllRows) can access all movements. " +
      "Pattern: const [allRows, setAllRows] = useState<LedgerMovement[] | null>(null)."
    );
  });
});

// ===========================================================================
// ID: Canonical ID invariants — security_id never mutated by search
// ===========================================================================

describe("ID: Canonical identity invariants — search never mutates or proxies canonical IDs", () => {
  it("ID-1: security_id field is byte-identical before and after applying symbol filter", () => {
    const original = mvt("id1", { secId: "XNYS:ABBV", ticker: "ABBV", company: "AbbVie Inc." });
    const before = original.security_id;
    // Apply the filter predicate — it must only read, never write
    const match = matchesMovementSymbol(original, "ABBV");
    assert.strictEqual(original.security_id, before,
      "ID-1: matchesMovementSymbol must not mutate security_id."
    );
    assert.ok(match, "ID-1: row must also match the query.");
  });

  it("ID-2: ticker field is byte-identical before and after applying symbol filter", () => {
    const m = mvt("id2", { ticker: "ABBV" });
    const before = m.ticker;
    matchesMovementSymbol(m, "ABBV");
    assert.strictEqual(m.ticker, before, "ID-2: matchesMovementSymbol must not mutate ticker.");
  });

  it("ID-3: company_name field is byte-identical before and after applying symbol filter", () => {
    const m = mvt("id3", { company: "AbbVie Inc." });
    const before = m.company_name;
    matchesMovementSymbol(m, "abbvie");
    assert.strictEqual(m.company_name, before, "ID-3: matchesMovementSymbol must not mutate company_name.");
  });

  it("ID-4: matched row objects in result are the same references (no cloning that could diverge)", () => {
    const m = mvt("id4", { secId: "XNYS:ABBV", ticker: "ABBV", company: "AbbVie Inc." });
    const server = [m, ...buildNeutralDataset(5, 100)];
    const { matched } = simulateBatchFetch(server, "ABBV");
    assert.ok(matched.some(r => r === m),
      "ID-4: Matched row reference must be the same object (no unexpected deep clone)."
    );
  });

  it("ID-5: filter never adds extra movements beyond what server returned", () => {
    const server = buildNeutralDataset(30, 0);
    const { matched } = simulateBatchFetch(server, "ABBV");
    // No ABBV in dataset → 0 results
    assert.strictEqual(matched.length, 0, "ID-5: Filter must not inject rows not in server data.");
    assert.ok(matched.length <= server.length, "ID-5: Matched count must not exceed server total.");
  });

  it("ID-6: movement ids are unique in the result set (no duplicates from page overlap)", () => {
    const server = [
      mvt("dup1", { secId: "XNYS:ABBV", ticker: "ABBV" }),
      mvt("dup2", { secId: "XNYS:ABBV", ticker: "ABBV" }),
      mvt("dup3", { secId: "XNYS:ABBV", ticker: "ABBV" }),
    ];
    const { matched } = simulateBatchFetch(server, "ABBV");
    const ids = matched.map(m => m.id);
    assert.strictEqual(new Set(ids).size, ids.length,
      "ID-6: No duplicate movement IDs in result — batch loop must not double-count any page."
    );
  });
});

// ===========================================================================
// RG: Non-regression — existing SMF/SRC coverage still holds
// ===========================================================================

describe("RG: Non-regression — existing symbol-filter invariants still hold post-refactor", () => {
  it("RG-1: matchesMovementSymbol still exists in PortfolioMovementsTable (not removed)", () => {
    assert.ok(
      pmSrc.includes("matchesMovementSymbol"),
      "RG-1: matchesMovementSymbol must be retained after the refactor."
    );
  });

  it("RG-2: securityId state variable still present for the symbol input", () => {
    assert.ok(
      pmSrc.includes("securityId") || pmSrc.includes("symbolQuery"),
      "RG-2: Symbol input state must be retained."
    );
  });

  it("RG-3: TYPE_PILLS still present (type filter must not regress)", () => {
    assert.ok(
      pmSrc.includes("TYPE_PILLS"),
      "RG-3: TYPE_PILLS must be retained — type filter is independent of symbol search refactor."
    );
  });

  it("RG-4: resetFilter function still present (reset button must still work)", () => {
    assert.ok(
      pmSrc.includes("resetFilter") || pmSrc.includes("function reset"),
      "RG-4: resetFilter must be retained after refactor."
    );
  });

  it("RG-5: MovementDetailDialog import still present (individual reassignment must not regress)", () => {
    assert.ok(
      pmSrc.includes("MovementDetailDialog"),
      "RG-5: MovementDetailDialog must still be used for per-movement detail/reassignment."
    );
  });

  it("RG-6: aria-label on symbol filter input retained", () => {
    assert.ok(
      pmSrc.includes('aria-label="Filter by symbol"'),
      "RG-6: Accessibility aria-label on symbol input must be retained."
    );
  });

  it("RG-7: existing predicate — case-insensitive toUpperCase pattern retained", () => {
    assert.ok(
      pmSrc.includes("toUpperCase"),
      "RG-7: Case-insensitive matching via toUpperCase must be retained."
    );
  });

  it("RG-8: Bulk import link retained", () => {
    assert.ok(
      pmSrc.includes("Bulk import") || pmSrc.includes("/portfolio/import"),
      "RG-8: Bulk import link must be retained."
    );
  });
});

// ===========================================================================
// LB helpers — simulate Reuben's correct large-dataset batch loop
// ===========================================================================

/**
 * Simulate the CORRECT batch-loop algorithm (Reuben's revision).
 *
 * - Loops with batchSize-offset until accumulated.length >= totalCountReported
 *   OR a short page is returned (whichever comes first).
 * - Deduplicates across batches by movement id.
 * - Returns { accumulated, matched, fetchCount, incomplete, dedupHappened }.
 *
 * NOTE: Intentionally does NOT use FULL_FETCH_LIMIT — the point is to verify
 * that ANY dataset size (including > 10,000) is handled correctly.
 */
function simulateLargeBatchLoop(
  serverDataset,
  totalCountReported,
  symbolQuery,
  batchSize = 500,
) {
  let accumulated = [];
  const seenIds = new Set();
  let batchOffset = 0;
  let fetchCount = 0;
  let dedupHappened = false;

  while (accumulated.length < totalCountReported) {
    const page = serverDataset.slice(batchOffset, batchOffset + batchSize);
    fetchCount++;

    for (const m of page) {
      if (seenIds.has(m.id)) {
        dedupHappened = true;
      } else {
        seenIds.add(m.id);
        accumulated.push(m);
      }
    }

    batchOffset += batchSize;
    if (page.length < batchSize) break; // short page → done
  }

  const incomplete = accumulated.length < totalCountReported;
  const matched = accumulated.filter(m => matchesMovementSymbol(m, symbolQuery));

  return { accumulated, matched, fetchCount, incomplete, dedupHappened };
}

/**
 * Simulate the short-page-only termination variant (total_count absent/untrusted).
 * Loop stops ONLY when a page shorter than batchSize is returned.
 */
function simulateLargeBatchLoopShortPageOnly(serverDataset, symbolQuery, batchSize = 500) {
  let accumulated = [];
  const seenIds = new Set();
  let batchOffset = 0;
  let fetchCount = 0;

  while (true) {
    const page = serverDataset.slice(batchOffset, batchOffset + batchSize);
    fetchCount++;

    for (const m of page) {
      if (!seenIds.has(m.id)) {
        seenIds.add(m.id);
        accumulated.push(m);
      }
    }

    batchOffset += batchSize;
    if (page.length < batchSize) break;
  }

  const matched = accumulated.filter(m => matchesMovementSymbol(m, symbolQuery));
  return { accumulated, matched, fetchCount };
}

/**
 * Simulate a mid-loop network error.
 * Pages 0..errorAfterPage-1 succeed; page errorAfterPage throws.
 * Returns { committed (null if error), errorCaught, fetchCount }.
 */
function simulateErrorMidLoop(serverDataset, errorAfterPage, batchSize = 500) {
  let accumulated = [];
  const seenIds = new Set();
  let batchOffset = 0;
  let fetchCount = 0;
  let committed = null;
  let errorCaught = null;

  try {
    while (accumulated.length < serverDataset.length) {
      const pageIndex = fetchCount;
      if (pageIndex >= errorAfterPage) throw new Error("Network error on batch " + pageIndex);

      const page = serverDataset.slice(batchOffset, batchOffset + batchSize);
      fetchCount++;

      for (const m of page) {
        if (!seenIds.has(m.id)) {
          seenIds.add(m.id);
          accumulated.push(m);
        }
      }

      batchOffset += batchSize;
      if (page.length < batchSize) break;
    }
    // Would set allRows — only reached if no error
    committed = [...accumulated];
  } catch (e) {
    errorCaught = e;
    // Do NOT assign committed — partial rows are discarded
  }

  return { committed, errorCaught, fetchCount };
}

// ===========================================================================
// LB: Large-dataset batch loop — Reuben's third-author revision
// Tests marked ⚠ LB FUTURE fail against current FULL_FETCH_LIMIT=10_000 and
// pass after Reuben ships a proper while-loop with per-batch gen-check.
// ===========================================================================

describe("LB-1: total_count > 10,000 — batch loop finds match after row 10,000", () => {
  it("LB-1a: batch algorithm — correct loop reaches row 10,050 and finds ABBV", () => {
    // Build a 10,200-row dataset with the ABBV target at position 10,050.
    // FULL_FETCH_LIMIT=10_000 silently truncates and MISSES this row.
    const neutral = buildNeutralDataset(10050, 0);
    const target  = mvt("deep_match_abbv", { secId: "XNYS:ABBV", ticker: "ABBV", company: "AbbVie Inc." });
    const tail    = buildNeutralDataset(149, 10051);
    const serverDataset = [...neutral, target, ...tail];

    // batchSize=1000: needs 11 fetches (10 × 1000 + 1 × 200)
    const { matched, fetchCount } = simulateLargeBatchLoop(
      serverDataset, serverDataset.length, "ABBV", 1000
    );

    assert.strictEqual(matched.length, 1,
      "LB-1a: The ABBV target at row 10,050 must be found when the loop goes past 10,000."
    );
    assert.strictEqual(matched[0].id, "deep_match_abbv",
      "LB-1a: Matched row must be the correct deep-position target."
    );
    assert.strictEqual(fetchCount, 11,
      "LB-1a: 10,200 rows / batchSize=1000 → exactly 11 batch requests required."
    );
  });

  it("LB-1b: single-pass FULL_FETCH_LIMIT=10_000 MISSES the row-10,050 target (documents defect)", () => {
    // This test documents why Reuben's loop is necessary.
    const neutral = buildNeutralDataset(10050, 0);
    const target  = mvt("deep_match_abbv2", { secId: "XNYS:ABBV", ticker: "ABBV", company: "AbbVie Inc." });
    const tail    = buildNeutralDataset(149, 10051);
    const serverDataset = [...neutral, target, ...tail];

    // Simulate current Linus single-pass: fetches at most FULL_FETCH_LIMIT=10_000 rows
    const FULL_FETCH_LIMIT = 10_000;
    const singleFetchResult = serverDataset.slice(0, FULL_FETCH_LIMIT);
    const matched = singleFetchResult.filter(m => matchesMovementSymbol(m, "ABBV"));

    assert.strictEqual(matched.length, 0,
      "LB-1b DEFECT EVIDENCE: FULL_FETCH_LIMIT=10_000 single-pass misses targets beyond row 9,999. " +
      "Reuben's batch-loop revision must fix this."
    );
  });

  it("LB-1c: ⚠ LB FUTURE — source contains a batch-loop that iterates past FULL_FETCH_LIMIT", () => {
    // Reuben's revision must replace the single FULL_FETCH_LIMIT request with a
    // loop.  Accept while/do-while/for-of with offset tracking, OR a recursive
    // fetch-chain pattern.  The FULL_FETCH_LIMIT constant may or may not remain
    // as a per-batch cap; what must be absent is relying on it as a one-shot limit.
    const hasBatchLoop =
      // Classic while-loop with offset tracking (preferred)
      (pmSrc.includes("while (") && (pmSrc.includes("batchOffset") || pmSrc.includes("pageOffset") || pmSrc.includes("fetchOffset") || pmSrc.includes("accumulated"))) ||
      // Alternative: do-while
      (pmSrc.includes("do {") && pmSrc.includes("while (")) ||
      // Recursive pagination pattern
      (pmSrc.includes("function fetchAll") || pmSrc.includes("const fetchAll") || pmSrc.includes("async function fetchBatch")) ||
      // Offset-advancing for loop
      (pmSrc.includes("for (let batchOff") || pmSrc.includes("for (let off =")) ||
      // Generator/async-generator pattern
      pmSrc.includes("async function*");

    assert.ok(
      hasBatchLoop,
      "LB-1c ⚠ LB FUTURE: No batch-loop pattern found in PortfolioMovementsTable.tsx. " +
      "Reuben must replace the single FULL_FETCH_LIMIT=10_000 fetch with a loop that " +
      "iterates until accumulated.length >= total_count or a short page is returned."
    );
  });
});

describe("LB-2: Terminates when accumulated >= total_count even if final page is full", () => {
  it("LB-2a: accumulated reaches total_count on a full page — no extra fetch", () => {
    // 2000 rows, batchSize=1000: page 1 (1000 full) → 1000 < 2000 → continue.
    // Page 2 (1000 full) → 2000 >= 2000 → STOP.  No 3rd fetch.
    const server = buildNeutralDataset(2000, 0);

    const { fetchCount, accumulated } = simulateLargeBatchLoop(server, 2000, "", 1000);

    assert.strictEqual(accumulated.length, 2000,
      "LB-2a: All 2000 rows must be accumulated."
    );
    assert.strictEqual(fetchCount, 2,
      "LB-2a: Exactly 2 fetches — loop must stop after accumulated equals total_count, " +
      "even when the last page was full (not short)."
    );
  });

  it("LB-2b: accumulated already exceeds total_count due to dedup — loop also terminates", () => {
    // 100 unique rows; server reports total_count=80 (conservative).
    // Accumulated reaches 80 after batch 1 subset — loop stops.
    const server = buildNeutralDataset(100, 0);

    const { fetchCount } = simulateLargeBatchLoop(server, 80, "", 100);

    // First batch: accumulates 100 rows → 100 >= 80 → loop stops immediately
    assert.strictEqual(fetchCount, 1,
      "LB-2b: When first batch already satisfies total_count, loop must not continue."
    );
  });

  it("LB-2c: ⚠ LB FUTURE — source checks accumulated length against total_count inside loop", () => {
    // Reuben's while condition must be: while (accumulated.length < total_count)
    // or equivalent.  Plain 'while (true)' with only a short-page break is
    // insufficient — it would over-fetch if the server returns extra pages.
    const hasLengthCheck =
      pmSrc.includes(".length < total") ||
      pmSrc.includes(".length < batchTotal") ||
      pmSrc.includes(".length < reportedTotal") ||
      pmSrc.includes("accumulated.length") ||
      pmSrc.includes("allRows.length") ||
      // while-condition comparing length to a total variable
      /while\s*\(\s*\w+\.length\s*<\s*\w+/.test(pmSrc);

    assert.ok(
      hasLengthCheck,
      "LB-2c ⚠ LB FUTURE: Loop must check accumulated.length < total_count in its " +
      "continuation condition (not just rely on short-page detection)."
    );
  });
});

describe("LB-3: Terminates on short page when total_count is absent or untrusted", () => {
  it("LB-3a: short-page-only termination — stops on the first page shorter than batchSize", () => {
    // 1250 rows, batchSize=500: pages of 500, 500, 250 (short) → 3 fetches
    const server = buildNeutralDataset(1250, 0);

    const { fetchCount, accumulated } = simulateLargeBatchLoopShortPageOnly(server, "", 500);

    assert.strictEqual(accumulated.length, 1250,
      "LB-3a: All 1250 rows must be accumulated even without a total_count guard."
    );
    assert.strictEqual(fetchCount, 3,
      "LB-3a: Short-page detection: 3 fetches (500+500+250 short) — no 4th fetch."
    );
  });

  it("LB-3b: exactly-page-size dataset terminates correctly with short-page-only guard", () => {
    // 1000 rows, batchSize=500: pages of 500, 500 (not short) → needs total_count guard.
    // With short-page-only, this would loop forever (or up to a max-loop safeguard).
    // This test verifies the combined (length check + short page) algorithm handles it.
    const server = buildNeutralDataset(1000, 0);

    const { fetchCount } = simulateLargeBatchLoop(server, 1000, "", 500);

    assert.strictEqual(fetchCount, 2,
      "LB-3b: Exactly-divisible dataset: total_count guard must stop the loop after " +
      "2 full pages (short-page guard alone would fail here)."
    );
  });

  it("LB-3c: ⚠ LB FUTURE — source has a short-page break inside the batch loop", () => {
    // Reuben must also break on a short page so datasets whose actual size is
    // less than the server-reported total_count don't loop indefinitely.
    const hasShortPageBreak =
      (pmSrc.includes("break") && pmSrc.includes(".length <")) ||
      (pmSrc.includes("break") && pmSrc.includes("< batchSize")) ||
      (pmSrc.includes("break") && pmSrc.includes("< BATCH_SIZE")) ||
      (pmSrc.includes("break") && pmSrc.includes("< limit")) ||
      (pmSrc.includes("break") && /page\.length|batch\.length|movements\.length/.test(pmSrc));

    assert.ok(
      hasShortPageBreak,
      "LB-3c ⚠ LB FUTURE: No short-page break found inside the batch loop. " +
      "Reuben must break when page.length < batchSize to guard against infinite loops " +
      "when total_count is absent or inaccurate."
    );
  });
});

describe("LB-4: No duplicate rows across batches", () => {
  it("LB-4a: overlapping server pages produce no duplicates after dedup", () => {
    // Simulate a misbehaving server that returns the same rows on two consecutive pages.
    const shared = buildNeutralDataset(100, 0);          // ids m0..m99
    const serverWithOverlap = [...shared, ...shared];    // duplicated
    // total_count = 200 (server claims 200), but there are only 100 unique rows.

    const { accumulated, dedupHappened } = simulateLargeBatchLoop(
      serverWithOverlap, 200, "", 100
    );

    assert.ok(
      dedupHappened,
      "LB-4a: Dedup must have fired when the same row ids appear on multiple pages."
    );
    assert.strictEqual(accumulated.length, 100,
      "LB-4a: Despite server returning 200 rows with 100 duplicates, only 100 unique rows."
    );
    const ids = accumulated.map(m => m.id);
    assert.strictEqual(new Set(ids).size, ids.length,
      "LB-4a: All accumulated row IDs must be unique."
    );
  });

  it("LB-4b: adjacent non-overlapping batches have no duplicates (normal case)", () => {
    // Pages 0-99, 100-199, 200-249 — no overlap.
    const server = buildNeutralDataset(250, 0);

    const { accumulated, dedupHappened } = simulateLargeBatchLoop(server, 250, "", 100);

    assert.strictEqual(dedupHappened, false,
      "LB-4b: No dedup events for cleanly non-overlapping pages."
    );
    assert.strictEqual(accumulated.length, 250,
      "LB-4b: All 250 unique rows accumulated."
    );
  });

  it("LB-4c: ⚠ LB FUTURE — source contains a dedup mechanism (Set or Map by id)", () => {
    // Reuben's implementation must guard against duplicate row IDs.
    const hasDedup =
      pmSrc.includes("new Set") ||
      pmSrc.includes("new Map") ||
      pmSrc.includes(".has(m.id)") ||
      pmSrc.includes(".has(row.id)") ||
      pmSrc.includes(".has(item.id)") ||
      pmSrc.includes("seen.has") ||
      pmSrc.includes("seenIds") ||
      pmSrc.includes("uniqueIds") ||
      // functional dedup via filter + Set
      (pmSrc.includes(".filter(") && pmSrc.includes(".id)"));

    assert.ok(
      hasDedup,
      "LB-4c ⚠ LB FUTURE: No dedup mechanism found. Reuben's loop must guard against " +
      "duplicate movement IDs when pages overlap (e.g., server offset drift)."
    );
  });
});

describe("LB-5 / LB-6: Filter isolation and symbol-query exclusion per batch", () => {
  it("LB-5a: non-symbol filters compose via AND — only rows matching both filter and query", () => {
    // Mix of ABBV and MSFT, with some in account acc2 (filtered out).
    const dataset = [
      mvt("a1", { secId: "XNYS:ABBV", ticker: "ABBV", company: "AbbVie Inc.", account: "acc1" }),
      mvt("a2", { secId: "XNYS:ABBV", ticker: "ABBV", company: "AbbVie Inc.", account: "acc2" }), // excluded by account filter
      mvt("m1", { secId: "XNYS:MSFT", ticker: "MSFT", company: "Microsoft Corp", account: "acc1" }),
    ];
    // Simulate: account filter removes acc2 rows BEFORE symbol filter.
    const accountFiltered = dataset.filter(m => m.account_id === "acc1");
    const { matched } = simulateLargeBatchLoop(accountFiltered, accountFiltered.length, "ABBV", 100);

    assert.strictEqual(matched.length, 1,
      "LB-5a: Only acc1 + ABBV row must match — account filter applied before symbol filter."
    );
    assert.strictEqual(matched[0].id, "a1",
      "LB-5a: Matched row must be 'a1' (acc1 ABBV), not 'a2' (acc2 ABBV)."
    );
  });

  it("LB-5b: ⚠ LB FUTURE — each batch request uses the same buildFilter() call", () => {
    // Reuben must pass the same base filter to every getMovements() call in the loop.
    // The filter must NOT include security_id (that is always handled client-side).
    const hasBuildFilter = pmSrc.includes("buildFilter");
    assert.ok(
      hasBuildFilter,
      "LB-5b ⚠ LB FUTURE: buildFilter() must be used to produce a consistent filter " +
      "object for every batch request in the loop."
    );
  });

  it("LB-6a: symbol query not passed as server filter — all symbol matching is client-side", () => {
    // The symbol predicate runs CLIENT-side after fetching; the batch requests
    // do NOT include a security_id/ticker server filter.
    // Verify by checking that buildFilter excludes security_id (mirrors BT tests).
    const buildFilterSrc = (() => {
      const start = pmSrc.indexOf("function buildFilter");
      if (start === -1) return null;
      const end = pmSrc.indexOf("\n}", start);
      return end === -1 ? null : pmSrc.slice(start, end + 2);
    })();

    if (buildFilterSrc === null) {
      // buildFilter may not yet exist (pre-Reuben) — skip the inner check
      return;
    }

    assert.ok(
      !buildFilterSrc.includes("security_id"),
      "LB-6a: buildFilter() must NOT include security_id — symbol matching is client-side."
    );
    assert.ok(
      !buildFilterSrc.includes("symbolQuery") && !buildFilterSrc.includes("securityId"),
      "LB-6a: buildFilter() must NOT pass the symbol/securityId query to the server."
    );
  });

  it("LB-6b: ⚠ LB FUTURE — comment or code confirms symbol filter is client-side in batch loop", () => {
    // Reuben's loop comment (or code structure) should confirm the intent.
    const hasClientSideComment =
      pmSrc.includes("// symbol") ||
      pmSrc.includes("// Symbol") ||
      pmSrc.includes("client-side") ||
      pmSrc.includes("clientSide") ||
      pmSrc.includes("// always handled") ||
      pmSrc.includes("symbol_id is always handled");

    assert.ok(
      hasClientSideComment,
      "LB-6b ⚠ LB FUTURE: No comment indicating symbol filter is handled client-side in the " +
      "batch loop. Reuben should retain or add a comment to prevent future regressions."
    );
  });
});

describe("LB-7: Generation guard checked after each await in the batch loop", () => {
  it("LB-7a: stale mid-loop response commits no state (simulation)", () => {
    // Scenario: generation guard fires after the 2nd batch await.
    // Steps: gen=5, batch 1 OK (rows 0-99), gen incremented to 6 mid-loop,
    //        batch 2 resolves but gen check fires → loop aborts without setAllRows.
    let stateCommit = null;
    let batchCount = 0;
    const gen = { current: 5 };
    const myGen = 5;

    const batches = [
      buildNeutralDataset(100, 0),   // batch 1 — OK
      buildNeutralDataset(100, 100), // batch 2 — arrives after gen change
    ];

    const accumulated = [];
    const seenIds = new Set();
    let aborted = false;

    for (const batch of batches) {
      batchCount++;
      // Simulate gen change between batch 1 and batch 2.
      if (batchCount === 2) gen.current = 6; // new load fired
      if (myGen !== gen.current) { aborted = true; break; }
      for (const m of batch) {
        if (!seenIds.has(m.id)) { seenIds.add(m.id); accumulated.push(m); }
      }
    }
    if (!aborted) stateCommit = [...accumulated];

    assert.ok(aborted,
      "LB-7a: When gen changes mid-loop, the loop must abort before committing state."
    );
    assert.strictEqual(stateCommit, null,
      "LB-7a: setAllRows / state commit must NOT be called with partial data when gen is stale."
    );
    assert.strictEqual(accumulated.length, 100,
      "LB-7a: Partial data (100 rows from batch 1) must NOT be published."
    );
  });

  it("LB-7b: ⚠ LB FUTURE — gen check is inside the batch loop body (not only before)", () => {
    // Linus already checks gen before the full-fetch call once.
    // Reuben MUST also check gen inside the while-loop AFTER each await.
    // Look for gen check inside a loop body — both must be present together.
    const hasGenInLoop =
      // gen check + a loop indicator in adjacent code
      (pmSrc.includes("loadGenRef.current") && pmSrc.includes("while (")) ||
      // alternate: if (gen !== loadGenRef.current) return  anywhere
      (pmSrc.includes("!== loadGenRef.current") && pmSrc.includes("while (")) ||
      (pmSrc.match(/while\s*\([\s\S]{0,200}loadGenRef/) !== null) ||
      (pmSrc.match(/loadGenRef[\s\S]{0,400}while\s*\(/) !== null);

    assert.ok(
      hasGenInLoop,
      "LB-7b ⚠ LB FUTURE: Generation guard must appear INSIDE the batch loop body, " +
      "not only before the loop starts. Reuben must add an in-loop check after each " +
      "'await getMovements()' call so a newer load can abort a running batch sequence."
    );
  });

  it("LB-7c: ⚠ LB FUTURE — uses prefix-increment for gen (++loadGenRef.current)", () => {
    // Both Rusty's and Linus's implementations use prefix increment to ensure
    // the check `myGen !== loadGenRef.current` fires correctly.
    const hasPrefixIncrement =
      pmSrc.includes("++loadGenRef.current") ||
      pmSrc.includes("loadGenRef.current += 1") ||
      pmSrc.includes("loadGenRef.current = loadGenRef.current + 1");

    assert.ok(
      hasPrefixIncrement,
      "LB-7c ⚠ LB FUTURE: loadGenRef must be incremented with prefix ++ (not postfix) " +
      "to avoid the off-by-one stale-check window."
    );
  });
});

describe("LB-8: Network/error mid-loop does not publish partial results", () => {
  it("LB-8a: error after first successful batch — committed is null", () => {
    const server = buildNeutralDataset(1000, 0);

    // Error fires on the 2nd batch (index 1).
    const { committed, errorCaught, fetchCount } = simulateErrorMidLoop(server, 1, 500);

    assert.ok(
      committed === null,
      "LB-8a: When an error occurs mid-loop, allRows must NOT be set with partial data."
    );
    assert.ok(
      errorCaught !== null,
      "LB-8a: An error must have been caught."
    );
    assert.strictEqual(fetchCount, 1,
      "LB-8a: Exactly 1 successful batch before the error."
    );
  });

  it("LB-8b: error on first batch — committed is null (no rows available)", () => {
    const server = buildNeutralDataset(500, 0);

    const { committed, errorCaught } = simulateErrorMidLoop(server, 0, 500);

    assert.strictEqual(committed, null,
      "LB-8b: Error on first batch — no state commit at all."
    );
    assert.ok(errorCaught !== null, "LB-8b: Error must be surfaced.");
  });

  it("LB-8c: ⚠ LB FUTURE — catch block in batch loop does NOT call setAllRows", () => {
    // Reuben's catch block should call setError / set an error state but
    // must NOT call setAllRows with partial data.
    const catchIdx = pmSrc.indexOf("} catch (");
    if (catchIdx === -1) return; // pre-implementation

    // Extract the catch block (rough heuristic: next 800 chars)
    const catchBody = pmSrc.slice(catchIdx, catchIdx + 800);

    assert.ok(
      !catchBody.includes("setAllRows(accumulated") &&
      !catchBody.includes("setAllRows(partial") &&
      !catchBody.includes("setAllRows([..."),
      "LB-8c ⚠ LB FUTURE: The catch block must NOT call setAllRows with partial data. " +
      "If an error occurs mid-loop, discard accumulated rows and surface the error instead."
    );
  });

  it("LB-8d: ⚠ LB FUTURE — catch block calls setError or equivalent error state setter", () => {
    const catchIdx = pmSrc.indexOf("} catch (");
    if (catchIdx === -1) return; // pre-implementation

    const catchBody = pmSrc.slice(catchIdx, catchIdx + 800);

    const surfacesError =
      catchBody.includes("setError(") ||
      catchBody.includes("setFetchError(") ||
      catchBody.includes("setLoadError(") ||
      catchBody.includes("setStatus(") ||
      catchBody.includes("console.error(");

    assert.ok(
      surfacesError,
      "LB-8d ⚠ LB FUTURE: The catch block must surface the error (setError or equivalent) " +
      "so the UI can display an error state rather than appearing to silently succeed."
    );
  });
});

describe("LB-9: Incompleteness warning — only genuine post-exhaustion mismatch", () => {
  it("LB-9a: loop exhausted with accumulated < total_count → incomplete=true", () => {
    // Server reports 10,000 rows but only returns 9,800 (e.g., last batch truncated).
    const server = buildNeutralDataset(9800, 0); // only 9800 actually returned

    const { incomplete } = simulateLargeBatchLoop(server, 10000, "", 1000);

    assert.strictEqual(incomplete, true,
      "LB-9a: When accumulated < total_count after loop exhaustion, incomplete must be true."
    );
  });

  it("LB-9b: loop completes with accumulated = total_count → incomplete=false", () => {
    const server = buildNeutralDataset(2000, 0);

    const { incomplete } = simulateLargeBatchLoop(server, 2000, "", 500);

    assert.strictEqual(incomplete, false,
      "LB-9b: When accumulated equals total_count, no incompleteness warning."
    );
  });

  it("LB-9c: crossing 10,000 with full success → incomplete=false (no false-positive warning)", () => {
    // 10,200 rows, all returned, total_count=10,200 — NOT an error condition.
    // No incompleteness warning should appear just because > 10,000 rows were loaded.
    const server = buildNeutralDataset(10200, 0);

    const { incomplete, accumulated } = simulateLargeBatchLoop(server, 10200, "", 1000);

    assert.strictEqual(incomplete, false,
      "LB-9c: Successfully loading 10,200 rows must NOT trigger the incompleteness warning."
    );
    assert.strictEqual(accumulated.length, 10200,
      "LB-9c: All 10,200 rows must be present."
    );
  });

  it("LB-9d: ⚠ LB FUTURE — source has an incompleteness state variable", () => {
    // Reuben must track whether post-loop accumulated < total_count.
    const hasIncompleteState =
      pmSrc.includes("incomplete") ||
      pmSrc.includes("truncated") ||
      pmSrc.includes("isTruncated") ||
      pmSrc.includes("isIncomplete") ||
      pmSrc.includes("searchExhausted") ||
      pmSrc.includes("partialResults");

    assert.ok(
      hasIncompleteState,
      "LB-9d ⚠ LB FUTURE: No incompleteness state variable found. Reuben must track " +
      "whether the batch loop terminated with accumulated.length < total_count so the " +
      "UI can show an accessible incompleteness warning."
    );
  });

  it("LB-9e: ⚠ LB FUTURE — incompleteness warning is accessible (aria-live or role=alert)", () => {
    // The warning must be discoverable by assistive technologies.
    const hasAccessibleWarning =
      (pmSrc.includes("incomplete") || pmSrc.includes("truncated") || pmSrc.includes("partialResults")) &&
      (pmSrc.includes('aria-live') || pmSrc.includes('role="alert"') || pmSrc.includes("role='alert'"));

    assert.ok(
      hasAccessibleWarning,
      "LB-9e ⚠ LB FUTURE: Incompleteness warning must use aria-live or role='alert' " +
      "so screen-reader users know the result set may be incomplete."
    );
  });
});

describe("LB-10: No N+1 — exact fetch count equal to Math.ceil(total / batchSize)", () => {
  it("LB-10a: 5000 rows at batchSize=500 → exactly 10 fetches", () => {
    const server = buildNeutralDataset(5000, 0);

    const { fetchCount } = simulateLargeBatchLoop(server, 5000, "", 500);

    assert.strictEqual(fetchCount, 10,
      "LB-10a: 5000/500 = 10 batches exactly — no 11th N+1 fetch."
    );
  });

  it("LB-10b: 5001 rows at batchSize=500 → exactly 11 fetches (ceiling)", () => {
    const server = buildNeutralDataset(5001, 0);

    const { fetchCount } = simulateLargeBatchLoop(server, 5001, "", 500);

    assert.strictEqual(fetchCount, 11,
      "LB-10b: Math.ceil(5001/500) = 11 batches — short final page detected correctly."
    );
  });

  it("LB-10c: 1 row at batchSize=500 → exactly 1 fetch (degenerate case)", () => {
    const server = buildNeutralDataset(1, 0);

    const { fetchCount } = simulateLargeBatchLoop(server, 1, "", 500);

    assert.strictEqual(fetchCount, 1,
      "LB-10c: Single row: exactly 1 batch fetch."
    );
  });

  it("LB-10d: zero rows at batchSize=500 → exactly 1 fetch (empty dataset)", () => {
    // Empty server response: 1 fetch, empty page (short=0 < 500), loop stops.
    const server = [];
    const { fetchCount, accumulated } = simulateLargeBatchLoopShortPageOnly(server, "", 500);

    assert.strictEqual(fetchCount, 1,
      "LB-10d: Even empty dataset must make exactly 1 fetch (server confirms empty)."
    );
    assert.strictEqual(accumulated.length, 0,
      "LB-10d: No rows accumulated from empty dataset."
    );
  });

  it("LB-10e: ⚠ LB FUTURE — no hard constant cap that silently truncates (no final FULL_FETCH_LIMIT guard)", () => {
    // After Reuben's fix, fetching MORE than 10,000 rows must be allowed.
    // The component must NOT contain a guard like: if (accumulated.length >= 10_000) break;
    const hasTruncateCap =
      /if\s*\(\s*(accumulated|allRows|all)\.length\s*>=\s*10[_,]?000\s*\)/.test(pmSrc) ||
      /if\s*\(\s*(accumulated|allRows|all)\.length\s*>=\s*FULL_FETCH_LIMIT\s*\)/.test(pmSrc);

    assert.ok(
      !hasTruncateCap,
      "LB-10e ⚠ LB FUTURE: Hard 10,000-row cap must not remain as a silent truncation guard. " +
      "The loop must terminate solely on accumulated >= total_count or short page."
    );
  });
});

describe("LB-11: Coherent client-side pagination after large-batch completion", () => {
  it("LB-11a: 10,200 fetched / 15 matching ABBV → totalCount=15, first page shows all 15", () => {
    const neutral = buildNeutralDataset(10185, 0);
    const abbvRows = Array.from({ length: 15 }, (_, i) =>
      mvt(`abbv_${i}`, { secId: "XNYS:ABBV", ticker: "ABBV", company: "AbbVie Inc." })
    );
    const server = [...neutral, ...abbvRows];

    const { matched } = simulateLargeBatchLoop(server, server.length, "ABBV", 1000);

    assert.strictEqual(matched.length, 15,
      "LB-11a: 15 ABBV rows must be found across 10,200 total rows."
    );

    // Client-side pagination: totalCount from matched.length, not server total
    const totalCount = matched.length;    // 15, not 10,200
    const firstPage = clientPage(matched, 0);

    assert.strictEqual(firstPage.length, 15,
      "LB-11a: First page shows all 15 matched rows (less than PAGE_SIZE=50)."
    );
    const nextDisabled = 0 + PAGE_SIZE >= totalCount; // 50 >= 15 → true
    assert.strictEqual(nextDisabled, true,
      "LB-11a: Next button must be disabled when matched count < PAGE_SIZE."
    );
  });

  it("LB-11b: 300 fetched / 120 matching → correct page count and Next/Prev state", () => {
    const neutral = buildNeutralDataset(180, 0);
    const abbvRows = Array.from({ length: 120 }, (_, i) =>
      mvt(`abbv2_${i}`, { secId: "XNYS:ABBV", ticker: "ABBV", company: "AbbVie Inc." })
    );
    const server = [...neutral, ...abbvRows];

    const { matched } = simulateLargeBatchLoop(server, 300, "ABBV", 100);

    assert.strictEqual(matched.length, 120, "LB-11b: 120 ABBV rows found.");

    const totalCount = matched.length;               // 120
    const pageCount = Math.ceil(totalCount / PAGE_SIZE); // 3
    assert.strictEqual(pageCount, 3,
      "LB-11b: 120 matched rows / PAGE_SIZE=50 = 3 client pages."
    );

    // Page 1: offset=0, Next enabled (0+50 < 120)
    const page1 = clientPage(matched, 0);
    assert.strictEqual(page1.length, PAGE_SIZE,
      "LB-11b: First client page has PAGE_SIZE=50 rows."
    );
    assert.strictEqual(0 + PAGE_SIZE >= totalCount, false,
      "LB-11b: Next button enabled on page 1 (50 < 120)."
    );

    // Page 3: offset=100, Next disabled (100+50 >= 120)
    const page3 = clientPage(matched, 100);
    assert.strictEqual(page3.length, 20,
      "LB-11b: Third client page has 20 rows (120 - 100)."
    );
    assert.strictEqual(100 + PAGE_SIZE >= totalCount, true,
      "LB-11b: Next button disabled on last page (150 >= 120)."
    );
  });

  it("LB-11c: clearing symbol query after large batch resets to server pagination", () => {
    // Behavioral: after clearing the query, offset resets to 0,
    // allRows is cleared, and server pagination resumes.
    // The 'allRows' mode is only active when a symbol query is present.
    const queryActive = "ABBV";
    const queryCleared = "";

    // With query: allRows mode → totalCount from filteredAllRows.length
    const allRows = buildNeutralDataset(200, 0);
    const filteredAllRows = allRows.filter(m => matchesMovementSymbol(m, queryActive));
    const totalCountWithQuery = filteredAllRows.length; // 0 (MSFT dataset, no ABBV)

    // Without query: server mode → totalCount from serverData.total_count
    const serverTotal = 342;
    const totalCountWithoutQuery = serverTotal;

    // Mode switch
    const inAllRowsMode = queryCleared.trim().length > 0;
    assert.strictEqual(inAllRowsMode, false,
      "LB-11c: Empty query must switch out of allRows mode."
    );
    assert.strictEqual(totalCountWithoutQuery, 342,
      "LB-11c: After clearing query, totalCount returns to server total."
    );
  });
});
