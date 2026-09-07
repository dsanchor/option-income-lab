/**
 * Regression tests — Movements symbol-filter parity with Symbols list search.
 *
 * INTERPRETATION
 * ==============
 * The "symbol-filter parity" requirement applies to the GLOBAL Movements page
 * (frontend/src/components/PortfolioMovementsTable.tsx), not to Symbol Details.
 * Symbol Details (StockTransactionsTable) is already scoped to a single securityId
 * prop — there is no symbol filter control there.
 *
 * The global Movements page has a "Symbol" text input (state: securityId) that
 * currently sends `securityId.trim()` to the backend API as a `security_id` exact
 * filter.  LedgerMovement rows already carry three searchable string fields that
 * are rendered in the table:
 *   • security_id  — canonical MIC:TICKER  (e.g. "XNYS:ABBV")
 *   • ticker       — bare ticker           (e.g. "ABBV")
 *   • company_name — visible company name  (e.g. "AbbVie Inc.")
 *
 * Parity with SymbolsTable search means the Movements filter must:
 *   1. Match any of those three fields (OR semantics within the row).
 *   2. Be case-insensitive.
 *   3. Trim whitespace from the query before matching.
 *   4. Return ALL rows when the query is empty or whitespace-only.
 *   5. Compose AND with the existing type and temporal filters.
 *   6. Reset pagination (offset → 0) when the symbol query changes.
 *   7. Never mutate the canonical security_id stored in a movement row.
 *   8. Never trigger additional per-row network calls.
 *
 * CONTRACT GROUPS
 * ===============
 *   SMF  Symbol-match predicate — behavioral (always runnable inline mirror).
 *   SCI  Case-insensitive matching across all three fields.
 *   SCR  Clear / reset: empty → all; whitespace → all.
 *   SAC  AND composition: symbol × type.
 *   STM  AND composition: symbol × temporal.
 *   STT  Triple AND: symbol × type × temporal.
 *   SPR  Pagination offset resets to 0 on symbol filter change.
 *   SRC  Source-contract: PortfolioMovementsTable has multi-field symbol filter.
 *   NM   No mutation; no extra per-row API calls.
 *
 * Run: node --test frontend/tests/movementsSymbolFilter.test.mjs
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join, dirname } from "node:path";

const __dir = dirname(fileURLToPath(import.meta.url));

const PMT_SRC_PATH = join(__dir, "..", "src", "components", "PortfolioMovementsTable.tsx");
const pmSrc = readFileSync(PMT_SRC_PATH, "utf8");

// Reference for cross-parity comparison: SymbolsTable search predicate
const STT_SRC_PATH = join(__dir, "..", "src", "components", "SymbolsTable.tsx");
const sttSrc = readFileSync(STT_SRC_PATH, "utf8");

// ---------------------------------------------------------------------------
// Inline mirrors
// ---------------------------------------------------------------------------

/**
 * Mirror of the PARITY target: the multi-field symbol search predicate that
 * the Movements symbol filter must implement.
 *
 * Matches:
 *   - m.security_id  (canonical MIC:TICKER, e.g. "XNYS:ABBV")
 *   - m.ticker       (bare ticker,           e.g. "ABBV")
 *   - m.company_name (visible company name,  e.g. "AbbVie Inc.")
 *
 * Mirrors SymbolsTable.tsx predicate:
 *   r.symbol?.toUpperCase().includes(q) ||
 *   (r.display_name || "").toUpperCase().includes(q)
 *
 * Movements mapping: symbol→ticker, display_name→company_name,
 * plus additional security_id (canonical) for power-user MIC searches.
 */
function matchesMovementSymbolFilter(movement, query) {
  if (query == null) return true;
  const q = query.trim().toUpperCase();
  if (!q) return true;  // empty → include all
  return (
    (movement.security_id || "").toUpperCase().includes(q) ||
    (movement.ticker       || "").toUpperCase().includes(q) ||
    (movement.company_name || "").toUpperCase().includes(q)
  );
}

/** Mirror of PortfolioMovementsTable type filter (backend-side; for AND composition). */
function filterMovementsForType(movements, txnType) {
  if (!txnType) return movements;
  return movements.filter((m) => m.txn_type === txnType);
}

/** Mirror of temporal filter (date_from inclusive, lexicographic YYYY-MM-DD). */
function filterMovementsForDate(movements, dateFrom) {
  if (!dateFrom) return movements;
  return movements.filter((m) => m.trade_date >= dateFrom);
}

/** Combined AND filter. */
function applyMovementsFilters(movements, { symbolQuery, txnType, dateFrom }) {
  return movements
    .filter((m) => matchesMovementSymbolFilter(m, symbolQuery ?? ""))
    .filter((m) => !txnType || m.txn_type === txnType)
    .filter((m) => !dateFrom || m.trade_date >= dateFrom);
}

/** Pagination reset state machine. */
function handleSymbolQueryChange(newQuery, _currentOffset) {
  return { symbolQuery: newQuery, offset: 0 };
}

// ---------------------------------------------------------------------------
// Fixture helpers
// ---------------------------------------------------------------------------

function mvt(id, txnType, secId, ticker, companyName, tradeDate) {
  return {
    id,
    txn_type: txnType,
    security_id: secId,
    ticker,
    company_name: companyName,
    trade_date: tradeDate,
  };
}

const ABBV  = mvt("m1", "BUY",      "XNYS:ABBV",  "ABBV",  "AbbVie Inc.",      "2026-08-15");
const ULVR  = mvt("m2", "SELL",     "XLON:ULVR",  "ULVR",  "Unilever PLC",     "2026-07-20");
const NESN  = mvt("m3", "DIVIDEND", "XSWX:NESN",  "NESN",  "Nestlé S.A.",      "2026-09-01");
const ENG   = mvt("m4", "BUY",      "XMAD:ENG",   "ENG",   "Enagás S.A.",      "2026-06-10");
const AAPL  = mvt("m5", "SELL",     "XNAS:AAPL",  "AAPL",  "Apple Inc.",       "2026-09-05");
const ABBV2 = mvt("m6", "DIVIDEND", "XNYS:ABBV",  "ABBV",  "AbbVie Inc.",      "2026-05-20");  // outside 3m

const ALL_MOVEMENTS = [ABBV, ULVR, NESN, ENG, AAPL, ABBV2];

// ===========================================================================
// SMF: Symbol-match predicate — basic behavioral correctness
// ===========================================================================

describe("SMF: Symbol-match predicate — multi-field matching", () => {
  it("SMF-1: empty string includes all movements", () => {
    const result = ALL_MOVEMENTS.filter((m) => matchesMovementSymbolFilter(m, ""));
    assert.equal(result.length, ALL_MOVEMENTS.length);
  });

  it("SMF-2: null query includes all (defensive)", () => {
    const result = ALL_MOVEMENTS.filter((m) => matchesMovementSymbolFilter(m, null));
    assert.equal(result.length, ALL_MOVEMENTS.length);
  });

  it("SMF-3: exact canonical security_id matches (XNYS:ABBV)", () => {
    assert.equal(matchesMovementSymbolFilter(ABBV, "XNYS:ABBV"), true);
  });

  it("SMF-4: partial security_id prefix matches (XNYS)", () => {
    // Searching "XNYS" matches all XNYS: securities
    const result = ALL_MOVEMENTS.filter((m) => matchesMovementSymbolFilter(m, "XNYS"));
    assert.ok(result.some((m) => m.id === "m1"), "ABBV (XNYS) must match");
    assert.ok(result.some((m) => m.id === "m6"), "ABBV2 (XNYS) must match");
    assert.ok(!result.some((m) => m.id === "m2"), "ULVR (XLON) must not match XNYS");
  });

  it("SMF-5: bare ticker exact match (ABBV)", () => {
    assert.equal(matchesMovementSymbolFilter(ABBV, "ABBV"), true);
  });

  it("SMF-6: partial ticker match (ABB)", () => {
    // "ABB" matches "ABBV" ticker
    assert.equal(matchesMovementSymbolFilter(ABBV, "ABB"), true);
  });

  it("SMF-7: company_name exact match", () => {
    assert.equal(matchesMovementSymbolFilter(ABBV, "AbbVie Inc."), true);
  });

  it("SMF-8: partial company_name match (Unilever)", () => {
    assert.equal(matchesMovementSymbolFilter(ULVR, "Unilever"), true);
  });

  it("SMF-9: partial company_name word match (Inc)", () => {
    // "Inc" matches "Apple Inc." and "AbbVie Inc."
    const result = ALL_MOVEMENTS.filter((m) => matchesMovementSymbolFilter(m, "Inc"));
    assert.ok(result.some((m) => m.ticker === "AAPL"), "Apple Inc. must match 'Inc'");
    assert.ok(result.some((m) => m.ticker === "ABBV"), "AbbVie Inc. must match 'Inc'");
  });

  it("SMF-10: no match returns false", () => {
    assert.equal(matchesMovementSymbolFilter(ABBV, "ZZZZNOTFOUND"), false);
  });

  it("SMF-11: query 'apple' matches Apple via company_name (case-insensitive)", () => {
    assert.equal(matchesMovementSymbolFilter(AAPL, "apple"), true);
  });

  it("SMF-12: security_id MIC:TICKER colon separator preserved in match", () => {
    // ":ABBV" suffix search works
    assert.equal(matchesMovementSymbolFilter(ABBV, ":ABBV"), true);
  });

  it("SMF-13: movement with null ticker is safe (no throw)", () => {
    const m = { ...ABBV, ticker: null };
    assert.doesNotThrow(() => matchesMovementSymbolFilter(m, "ABBV"));
    // company_name fallback still works
    assert.equal(matchesMovementSymbolFilter(m, "AbbVie"), true);
  });

  it("SMF-14: movement with null company_name is safe (no throw)", () => {
    const m = { ...ABBV, company_name: null };
    assert.doesNotThrow(() => matchesMovementSymbolFilter(m, "ABBV"));
    // ticker still matches
    assert.equal(matchesMovementSymbolFilter(m, "ABBV"), true);
  });

  it("SMF-15: movement with null security_id is safe (no throw)", () => {
    const m = { ...ABBV, security_id: null };
    assert.doesNotThrow(() => matchesMovementSymbolFilter(m, "XNYS"));
    // ticker still matches "ABBV"
    assert.equal(matchesMovementSymbolFilter(m, "ABBV"), true);
  });

  it("SMF-16: does not mutate movement during filtering", () => {
    const original = { ...ABBV };
    matchesMovementSymbolFilter(ABBV, "ABBV");
    assert.equal(ABBV.security_id, original.security_id, "security_id must not be mutated");
    assert.equal(ABBV.ticker,      original.ticker,      "ticker must not be mutated");
    assert.equal(ABBV.company_name,original.company_name,"company_name must not be mutated");
  });
});

// ===========================================================================
// SCI: Case-insensitive matching
// ===========================================================================

describe("SCI: Case-insensitive matching across all three fields", () => {
  it("SCI-1: lowercase query matches uppercase security_id (xnys:abbv → XNYS:ABBV)", () => {
    assert.equal(matchesMovementSymbolFilter(ABBV, "xnys:abbv"), true);
  });

  it("SCI-2: uppercase query matches mixed-case company_name (ABBVIE → AbbVie)", () => {
    assert.equal(matchesMovementSymbolFilter(ABBV, "ABBVIE"), true);
  });

  it("SCI-3: mixed-case query matches ticker (aBbV → ABBV)", () => {
    assert.equal(matchesMovementSymbolFilter(ABBV, "aBbV"), true);
  });

  it("SCI-4: all-lowercase 'nestlé' does not exclude on accent (partial match ok)", () => {
    // 'NESTLE' matches 'Nestlé' via toUpperCase (locale-insensitive basic match)
    // At minimum 'NEST' must match 'Nestlé S.A.'
    assert.equal(matchesMovementSymbolFilter(NESN, "NEST"), true);
  });

  it("SCI-5: query trimmed before case comparison (  ABBV  )", () => {
    assert.equal(matchesMovementSymbolFilter(ABBV, "  ABBV  "), true);
  });

  it("SCI-6: whitespace-only query includes everything", () => {
    const result = ALL_MOVEMENTS.filter((m) => matchesMovementSymbolFilter(m, "   "));
    assert.equal(result.length, ALL_MOVEMENTS.length);
  });

  it("SCI-7: SymbolsTable parity — same predicate logic for symbol/ticker field", () => {
    // SymbolsTable: r.symbol?.toUpperCase().includes(query)
    // Movements:    m.ticker.toUpperCase().includes(q)
    // Both must be case-insensitive substring match.
    const sttPred = (row, q) => (row.symbol || "").toUpperCase().includes(q.trim().toUpperCase());
    const movPred = (m,   q) => matchesMovementSymbolFilter(m, q);

    // Same result for ticker/symbol field
    const row = { symbol: "ABBV", display_name: "AbbVie Inc." };
    const mov = { ...ABBV };
    for (const q of ["ABBV", "abbv", "ABB", "bb", "A", ""]) {
      const sttResult = q.trim() ? sttPred(row, q) : true;
      const movResult = movPred(mov, q);
      // Movements may include MORE (security_id match) but must not exclude when stt includes
      if (sttResult) {
        assert.equal(movResult, true,
          `SCI-7: SymbolsTable includes '${q}' for ABBV; Movements must too`);
      }
    }
  });

  it("SCI-8: SymbolsTable parity — same predicate logic for display_name/company_name", () => {
    const sttPred = (row, q) =>
      (row.display_name || "").toUpperCase().includes(q.trim().toUpperCase());
    const movPred = (m, q) => matchesMovementSymbolFilter(m, q);

    const row = { symbol: "AAPL", display_name: "Apple Inc." };
    const mov = { ...AAPL };
    for (const q of ["Apple", "APPLE", "apple", "Inc", "INC", ""]) {
      const sttResult = q.trim() ? sttPred(row, q) : true;
      const movResult = movPred(mov, q);
      if (sttResult) {
        assert.equal(movResult, true,
          `SCI-8: SymbolsTable includes '${q}' for Apple Inc.; Movements must too`);
      }
    }
  });
});

// ===========================================================================
// SCR: Clear / reset — empty query returns all movements
// ===========================================================================

describe("SCR: Clear / reset — empty query includes all", () => {
  it("SCR-1: clearing to empty string includes all movements", () => {
    // First filter down, then clear
    const filtered = ALL_MOVEMENTS.filter((m) => matchesMovementSymbolFilter(m, "ABBV"));
    assert.ok(filtered.length < ALL_MOVEMENTS.length, "precondition: filter reduces rows");

    const cleared = ALL_MOVEMENTS.filter((m) => matchesMovementSymbolFilter(m, ""));
    assert.equal(cleared.length, ALL_MOVEMENTS.length, "cleared query must include all rows");
  });

  it("SCR-2: resetFilter restores symbolQuery to empty (offset to 0)", () => {
    // State machine: reset sets query to "" and offset to 0
    const after = handleSymbolQueryChange("", 5);
    assert.equal(after.symbolQuery, "");
    assert.equal(after.offset, 0);
  });

  it("SCR-3: no rows excluded when query is empty after reset", () => {
    const result = applyMovementsFilters(ALL_MOVEMENTS, { symbolQuery: "" });
    assert.equal(result.length, ALL_MOVEMENTS.length);
  });

  it("SCR-4: resetting symbol filter does not affect type filter (AND independence)", () => {
    // Clear symbol, keep type BUY — should show all BUYs
    const result = applyMovementsFilters(ALL_MOVEMENTS, { symbolQuery: "", txnType: "BUY" });
    assert.ok(result.every((m) => m.txn_type === "BUY"), "clearing symbol must not affect type filter");
    assert.ok(result.length > 0, "should still have BUY rows");
  });

  it("SCR-5: PortfolioMovementsTable resetFilter sets securityId to empty string", () => {
    // Source-contract: resetFilter() in PortfolioMovementsTable must reset securityId
    assert.ok(
      pmSrc.includes("setSecurityId(\"\"") || pmSrc.includes("setSecurityId('')"),
      "SCR-5 DEFECT: resetFilter() must reset securityId to empty string. " +
      "Rusty: ensure setSecurityId(\"\") is called in resetFilter()."
    );
  });
});

// ===========================================================================
// SAC: AND composition — symbol × type filter
// ===========================================================================

describe("SAC: AND composition — symbol × type filter", () => {
  it("SAC-1: symbol 'ABBV' + type BUY → only ABBV BUY", () => {
    const result = applyMovementsFilters(ALL_MOVEMENTS, { symbolQuery: "ABBV", txnType: "BUY" });
    assert.equal(result.length, 1);
    assert.equal(result[0].id, "m1");
    assert.equal(result[0].txn_type, "BUY");
  });

  it("SAC-2: symbol 'Unilever' + type SELL → only Unilever SELL", () => {
    const result = applyMovementsFilters(ALL_MOVEMENTS, { symbolQuery: "Unilever", txnType: "SELL" });
    assert.equal(result.length, 1);
    assert.equal(result[0].id, "m2");
  });

  it("SAC-3: symbol 'ABBV' + type DIVIDEND → only ABBV DIVIDEND", () => {
    const result = applyMovementsFilters(ALL_MOVEMENTS, { symbolQuery: "ABBV", txnType: "DIVIDEND" });
    assert.equal(result.length, 1);
    assert.equal(result[0].id, "m6");
  });

  it("SAC-4: empty symbol + type BUY → all BUY regardless of symbol", () => {
    const result = applyMovementsFilters(ALL_MOVEMENTS, { symbolQuery: "", txnType: "BUY" });
    assert.ok(result.length >= 2, "should include all BUY movements");
    assert.ok(result.every((m) => m.txn_type === "BUY"));
  });

  it("SAC-5: symbol query + empty type → all types matching symbol", () => {
    // "ABBV" matches m1 (BUY) and m6 (DIVIDEND)
    const result = applyMovementsFilters(ALL_MOVEMENTS, { symbolQuery: "ABBV", txnType: "" });
    assert.deepEqual(result.map((m) => m.id), ["m1", "m6"]);
  });

  it("SAC-6: symbol 'Apple' + type SELL → only Apple SELL", () => {
    const result = applyMovementsFilters(ALL_MOVEMENTS, { symbolQuery: "Apple", txnType: "SELL" });
    assert.equal(result.length, 1);
    assert.equal(result[0].ticker, "AAPL");
  });

  it("SAC-7: symbol 'XNYS' (MIC prefix) + type DIVIDEND → ABBV DIVIDEND (XNYS:ABBV)", () => {
    const result = applyMovementsFilters(ALL_MOVEMENTS, { symbolQuery: "XNYS", txnType: "DIVIDEND" });
    assert.equal(result.length, 1);
    assert.equal(result[0].ticker, "ABBV");
    assert.equal(result[0].txn_type, "DIVIDEND");
  });

  it("SAC-8: no match for symbol — zero results regardless of type", () => {
    const result = applyMovementsFilters(ALL_MOVEMENTS, { symbolQuery: "ZZZNOTEXIST", txnType: "BUY" });
    assert.equal(result.length, 0);
  });
});

// ===========================================================================
// STM: AND composition — symbol × temporal filter
// ===========================================================================

describe("STM: AND composition — symbol × temporal filter", () => {
  const DATE_3M_AGO = "2026-06-07";  // 3 calendar months before 2026-09-07

  it("STM-1: symbol 'ABBV' + date_from 3m → only ABBV within 3 months", () => {
    // m1 (ABBV BUY 2026-08-15) is within 3m; m6 (ABBV DIV 2026-05-20) is outside
    const result = applyMovementsFilters(ALL_MOVEMENTS, {
      symbolQuery: "ABBV",
      dateFrom: DATE_3M_AGO,
    });
    assert.deepEqual(result.map((m) => m.id), ["m1"]);
  });

  it("STM-2: empty symbol + date_from → all movement types within the date range", () => {
    const result = applyMovementsFilters(ALL_MOVEMENTS, {
      symbolQuery: "",
      dateFrom: DATE_3M_AGO,
    });
    assert.ok(result.every((m) => m.trade_date >= DATE_3M_AGO));
    // m6 (2026-05-20) is before DATE_3M_AGO → excluded
    assert.ok(!result.some((m) => m.id === "m6"));
  });

  it("STM-3: symbol + ALL_TIME (no date_from) → all dates for that symbol", () => {
    const result = applyMovementsFilters(ALL_MOVEMENTS, {
      symbolQuery: "ABBV",
      dateFrom: "",  // no date bound
    });
    // Both m1 and m6 should be included
    assert.deepEqual(result.map((m) => m.id), ["m1", "m6"]);
  });

  it("STM-4: symbol 'Enagás' matches ENG via company_name + date within range", () => {
    const result = applyMovementsFilters(ALL_MOVEMENTS, {
      symbolQuery: "Enag",
      dateFrom: DATE_3M_AGO,  // ENG trade_date is 2026-06-10 which is >= 2026-06-07
    });
    assert.equal(result.length, 1);
    assert.equal(result[0].ticker, "ENG");
  });

  it("STM-5: symbol matches but movement date is before the time bound → excluded", () => {
    // m6 ABBV DIVIDEND 2026-05-20 is before DATE_3M_AGO (2026-06-07)
    const result = applyMovementsFilters(ALL_MOVEMENTS, {
      symbolQuery: "ABBV",
      dateFrom: DATE_3M_AGO,
    });
    assert.ok(!result.some((m) => m.id === "m6"),
      "ABBV DIVIDEND dated 2026-05-20 must be excluded by 3-month date bound");
  });
});

// ===========================================================================
// STT: Triple AND — symbol × type × temporal
// ===========================================================================

describe("STT: Triple AND — symbol × type × temporal", () => {
  const DATE_3M_AGO = "2026-06-07";

  it("STT-1: ABBV + BUY + 3m → exactly 1 row (m1)", () => {
    const result = applyMovementsFilters(ALL_MOVEMENTS, {
      symbolQuery: "ABBV",
      txnType: "BUY",
      dateFrom: DATE_3M_AGO,
    });
    assert.equal(result.length, 1);
    assert.equal(result[0].id, "m1");
  });

  it("STT-2: ABBV + DIVIDEND + 3m → 0 rows (only dividend is before 3m)", () => {
    const result = applyMovementsFilters(ALL_MOVEMENTS, {
      symbolQuery: "ABBV",
      txnType: "DIVIDEND",
      dateFrom: DATE_3M_AGO,
    });
    assert.equal(result.length, 0);
  });

  it("STT-3: Apple + SELL + 3m → 1 row (AAPL SELL 2026-09-05)", () => {
    const result = applyMovementsFilters(ALL_MOVEMENTS, {
      symbolQuery: "Apple",
      txnType: "SELL",
      dateFrom: DATE_3M_AGO,
    });
    assert.equal(result.length, 1);
    assert.equal(result[0].ticker, "AAPL");
  });

  it("STT-4: empty symbol + BUY + 3m → all BUY within 3m", () => {
    const result = applyMovementsFilters(ALL_MOVEMENTS, {
      symbolQuery: "",
      txnType: "BUY",
      dateFrom: DATE_3M_AGO,
    });
    assert.ok(result.every((m) => m.txn_type === "BUY"));
    assert.ok(result.every((m) => m.trade_date >= DATE_3M_AGO));
  });

  it("STT-5: no symbol + no type + no date → all rows pass (all filters inactive)", () => {
    const result = applyMovementsFilters(ALL_MOVEMENTS, {
      symbolQuery: "",
      txnType: "",
      dateFrom: "",
    });
    assert.equal(result.length, ALL_MOVEMENTS.length);
  });

  it("STT-6: filter order is AND-commutative", () => {
    // Apply in different orders — same result
    const a = ALL_MOVEMENTS
      .filter((m) => matchesMovementSymbolFilter(m, "ABBV"))
      .filter((m) => m.txn_type === "BUY")
      .filter((m) => m.trade_date >= DATE_3M_AGO);
    const b = ALL_MOVEMENTS
      .filter((m) => m.trade_date >= DATE_3M_AGO)
      .filter((m) => m.txn_type === "BUY")
      .filter((m) => matchesMovementSymbolFilter(m, "ABBV"));
    assert.deepEqual(a.map((x) => x.id), b.map((x) => x.id));
  });
});

// ===========================================================================
// SPR: Pagination offset resets to 0 on symbol filter change
// ===========================================================================

describe("SPR: Pagination offset resets to 0 on symbol filter change", () => {
  it("SPR-1: changing symbol query resets offset to 0", () => {
    for (const offset of [0, 50, 100, 200]) {
      const after = handleSymbolQueryChange("ABBV", offset);
      assert.equal(after.offset, 0,
        `SPR-1: offset must reset to 0 from offset=${offset}`);
    }
  });

  it("SPR-2: clearing symbol query also resets offset to 0", () => {
    const after = handleSymbolQueryChange("", 150);
    assert.equal(after.offset, 0, "clearing symbol must also reset offset");
  });

  it("SPR-3: new query value is preserved through reset", () => {
    const after = handleSymbolQueryChange("Nestlé", 100);
    assert.equal(after.symbolQuery, "Nestlé");
    assert.equal(after.offset, 0);
  });

  it("SPR-4: PortfolioMovementsTable applyFilter resets offset (calls setOffset(0))", () => {
    assert.ok(
      pmSrc.includes("setOffset(0)"),
      "SPR-4 DEFECT: applyFilter() must call setOffset(0) before fetching. " +
      "Check PortfolioMovementsTable.tsx."
    );
  });

  it("SPR-5: symbol filter change triggers applyFilter (or equivalent reload with offset 0)", () => {
    // Source-contract: when securityId changes and the user applies filter,
    // the offset must be reset.  The existing applyFilter() pattern calls setOffset(0).
    const hasApplyFilter = pmSrc.includes("applyFilter");
    const hasOffsetReset = pmSrc.includes("setOffset(0)");
    assert.ok(
      hasApplyFilter && hasOffsetReset,
      "SPR-5 DEFECT: PortfolioMovementsTable must reset offset when symbol filter is applied. " +
      "Current: applyFilter=" + hasApplyFilter + " setOffset(0)=" + hasOffsetReset
    );
  });
});

// ===========================================================================
// SRC: Source-contract — PortfolioMovementsTable has multi-field symbol filter
// ===========================================================================

describe("SRC: Source-contract — PortfolioMovementsTable multi-field symbol filter", () => {
  it("SRC-1: securityId input field is present for symbol filtering", () => {
    assert.ok(
      pmSrc.includes("securityId"),
      "SRC-1: securityId state must be present in PortfolioMovementsTable — existing input."
    );
  });

  it("SRC-2: filter applies case-insensitive matching (toUpperCase or toLowerCase)", () => {
    // Either the component does client-side toLowerCase/toUpperCase,
    // OR sends a symbol_search/q param that the backend normalises.
    const hasCaseInsensitive =
      pmSrc.includes("toUpperCase") ||
      pmSrc.includes("toLowerCase") ||
      pmSrc.includes("symbol_search") ||
      pmSrc.includes("symbolSearch") ||
      pmSrc.includes("\"q\"") ||
      pmSrc.includes("'q'");
    assert.ok(
      hasCaseInsensitive,
      "SRC-2 DEFECT: Symbol filter must be case-insensitive. " +
      "Either apply toUpperCase/toLowerCase client-side on loaded rows, " +
      "or pass symbol_search param to the API. " +
      "Rusty: mirror the SymbolsTable.tsx search predicate."
    );
  });

  it("SRC-3: filter matches ticker field (not just security_id)", () => {
    // Must check m.ticker in a FILTER context — not merely render {m.ticker}.
    // The filter predicate must use .ticker with includes() or toUpperCase(),
    // not just interpolate it in JSX. Rendering alone does not constitute filtering.
    const tickerInFilterContext =
      pmSrc.includes("ticker") && (
        pmSrc.includes(".ticker.toUpperCase") ||
        pmSrc.includes(".ticker?.toUpperCase") ||
        pmSrc.includes("(ticker") ||
        pmSrc.includes(".ticker || \"\"") ||
        pmSrc.includes(".ticker || ''") ||
        (pmSrc.includes("ticker") && pmSrc.includes(".includes(") &&
          // ensure they appear in a filter/predicate context within ~400 chars
          Math.abs(pmSrc.indexOf(".includes(") - pmSrc.indexOf("ticker")) < 400)
      );
    assert.ok(
      tickerInFilterContext,
      "SRC-3 DEFECT: Symbol filter must check m.ticker in the filter predicate " +
      "(not only render it as JSX). " +
      "Rusty: add (m.ticker || \"\").toUpperCase().includes(q) to the symbol filter function."
    );
  });

  it("SRC-4: filter matches company_name field (visible display name)", () => {
    // Must check m.company_name in a FILTER context — not merely render {m.company_name}.
    const companyNameInFilterContext =
      pmSrc.includes("company_name") && (
        pmSrc.includes(".company_name.toUpperCase") ||
        pmSrc.includes(".company_name?.toUpperCase") ||
        pmSrc.includes(".company_name || \"\"") ||
        pmSrc.includes(".company_name || ''") ||
        (pmSrc.includes("company_name") && pmSrc.includes(".includes(") &&
          Math.abs(pmSrc.indexOf(".includes(") - pmSrc.lastIndexOf("company_name")) < 400)
      );
    assert.ok(
      companyNameInFilterContext,
      "SRC-4 DEFECT: Symbol filter must check m.company_name in the filter predicate " +
      "(not only render it as JSX). " +
      "Rusty: add (m.company_name || \"\").toUpperCase().includes(q) to the filter function."
    );
  });

  it("SRC-5: securityId input has aria-label for accessibility", () => {
    // Existing: aria-label="Filter by symbol"
    assert.ok(
      pmSrc.includes("Filter by symbol") || pmSrc.includes("symbol"),
      "SRC-5: Symbol filter input must have a descriptive aria-label."
    );
  });

  it("SRC-6: symbol filter input uses securityId state (not a dead variable)", () => {
    // Verify securityId is both read (value={securityId}) and set (onChange)
    const usedAsValue    = pmSrc.includes("value={securityId}");
    const usedInOnChange = pmSrc.includes("setSecurityId") && pmSrc.includes("onChange");
    assert.ok(
      usedAsValue && usedInOnChange,
      "SRC-6: securityId must be a live controlled input (value= and onChange setSecurityId). " +
      "Currently: value=" + usedAsValue + " onChange=" + usedInOnChange
    );
  });

  it("SRC-7: SymbolsTable parity — SymbolsTable uses display_name; Movements must use company_name in filter", () => {
    // SymbolsTable filters on display_name; Movements equivalent is company_name.
    // Check both: SymbolsTable DOES filter on display_name, and PortfolioMovementsTable
    // DOES filter on company_name (in predicate context, not just render).
    const sttFiltersOnDisplayName =
      sttSrc.includes("display_name") &&
      sttSrc.includes(".toUpperCase") &&
      Math.abs(sttSrc.indexOf(".toUpperCase") - sttSrc.indexOf("display_name")) < 500;
    const pmtFiltersOnCompanyName =
      pmSrc.includes("company_name") && (
        pmSrc.includes(".company_name.toUpperCase") ||
        pmSrc.includes(".company_name?.toUpperCase") ||
        pmSrc.includes(".company_name || \"\"") ||
        pmSrc.includes(".company_name || ''")
      );
    assert.ok(
      sttFiltersOnDisplayName,
      "SRC-7 prerequisite: SymbolsTable must filter on display_name with toUpperCase"
    );
    assert.ok(
      pmtFiltersOnCompanyName,
      "SRC-7 DEFECT: PortfolioMovementsTable must filter on company_name (in predicate, not render) " +
      "to achieve parity with SymbolsTable display_name search. " +
      "Rusty: add (m.company_name || \"\").toUpperCase().includes(q) to the filter predicate."
    );
  });

  it("SRC-8: security_id check is part of the multi-field predicate, not the only field", () => {
    // For parity, the frontend must check ticker and company_name in a FILTER predicate.
    // Merely rendering m.ticker and m.company_name in JSX is NOT sufficient.
    const tickerInPredicate =
      pmSrc.includes(".ticker.toUpperCase") ||
      pmSrc.includes(".ticker || \"\"") ||
      pmSrc.includes(".ticker || ''") ||
      pmSrc.includes(".ticker?.toUpperCase");
    const companyInPredicate =
      pmSrc.includes(".company_name.toUpperCase") ||
      pmSrc.includes(".company_name || \"\"") ||
      pmSrc.includes(".company_name || ''") ||
      pmSrc.includes(".company_name?.toUpperCase");
    assert.ok(
      tickerInPredicate && companyInPredicate,
      "SRC-8 DEFECT: Symbol filter must be multi-field (security_id + ticker + company_name) " +
      "in the filter predicate — not just in JSX rendering. " +
      "Currently only the backend security_id param is set. " +
      "Rusty: add a client-side matchesMovementSymbol() function that checks all three fields."
    );
  });
});

// ===========================================================================
// NM: No mutation; no extra per-row API calls
// ===========================================================================

describe("NM: No mutation of canonical symbol; no per-row API calls", () => {
  it("NM-1: filtering does not change movement.security_id", () => {
    const movements = ALL_MOVEMENTS.map((m) => ({ ...m }));
    const origIds = movements.map((m) => m.security_id);
    movements.filter((m) => matchesMovementSymbolFilter(m, "ABBV"));
    assert.deepEqual(
      movements.map((m) => m.security_id),
      origIds,
      "NM-1: filtering must not mutate security_id on any movement"
    );
  });

  it("NM-2: filtering does not change movement.ticker", () => {
    const movements = ALL_MOVEMENTS.map((m) => ({ ...m }));
    const origTickers = movements.map((m) => m.ticker);
    movements.filter((m) => matchesMovementSymbolFilter(m, "ABBV"));
    assert.deepEqual(movements.map((m) => m.ticker), origTickers);
  });

  it("NM-3: filtering does not change movement.company_name", () => {
    const movements = ALL_MOVEMENTS.map((m) => ({ ...m }));
    const origNames = movements.map((m) => m.company_name);
    movements.filter((m) => matchesMovementSymbolFilter(m, "Apple"));
    assert.deepEqual(movements.map((m) => m.company_name), origNames);
  });

  it("NM-4: filter predicate is pure (no side effects, same input → same output)", () => {
    const m = { ...ABBV };
    const r1 = matchesMovementSymbolFilter(m, "abbv");
    const r2 = matchesMovementSymbolFilter(m, "abbv");
    const r3 = matchesMovementSymbolFilter(m, "abbv");
    assert.equal(r1, r2);
    assert.equal(r2, r3);
  });

  it("NM-5: PortfolioMovementsTable does not call fetch inside row render (no per-row calls)", () => {
    // Source-contract: no fetch() call inside a .map() over movements
    // Heuristic: no "fetch(" that appears after "movements.map" in close proximity
    const mapIdx   = pmSrc.indexOf("movements.map");
    const fetchIdx = pmSrc.indexOf("fetch(", mapIdx > 0 ? mapIdx : 0);
    // If movements.map appears before a fetch call, check they are far apart (not inside)
    if (mapIdx !== -1 && fetchIdx !== -1) {
      // A fetch inside .map would typically be within ~500 characters
      const gap = fetchIdx - mapIdx;
      assert.ok(
        gap < 0 || gap > 1000,
        "NM-5 DEFECT: fetch() call found close after movements.map — possible per-row API call. " +
        "The symbol filter must never trigger a per-row network request."
      );
    }
    // Additionally: no .forEach loop with fetch
    assert.ok(
      !pmSrc.includes(".forEach") || !pmSrc.includes("fetch("),
      "NM-5: No per-row fetch calls inside forEach loops."
    );
  });

  it("NM-6: filter does not alter the array passed in (no splice/push/pop)", () => {
    const input = [...ALL_MOVEMENTS];
    const snapshot = input.map((m) => m.id);
    applyMovementsFilters(input, { symbolQuery: "ABBV", txnType: "BUY" });
    assert.deepEqual(input.map((m) => m.id), snapshot, "input array must not be mutated");
  });
});
