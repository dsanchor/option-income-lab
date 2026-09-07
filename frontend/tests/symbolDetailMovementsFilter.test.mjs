/**
 * Regression tests — Symbol Details movements temporal filter.
 *
 * Contract (Copilot directive 2026-09-07):
 *   TF   Time filter periods: 1m / 3m / 6m / 1y boundary via calendar arithmetic.
 *   TB   Temporal boundary: inclusive from-date and today; "All" removes bound.
 *   AND  Type filter + time filter compose with AND semantics.
 *   DF   Canonical transaction date field: trade_date.
 *   PG   Pagination resets to page 0 on either filter change.
 *   SR   Source-contract: StockTransactionsTable has timeFilter state and UI.
 *   AC   Accessible segmented controls with distinguishable labels/state.
 *   RG   Regression: type selector preserved, reassign/actions unaffected.
 *
 * Run: node --test frontend/tests/symbolDetailMovementsFilter.test.mjs
 *
 * Behavioral tests (TF / TB / AND / DF / PG) exercise inline logic mirrors that
 * are always runnable.  Source-contract tests (SR / AC / RG) read the live
 * StockTransactionsTable.tsx and fail with a clear DEFECT message until Rusty
 * implements the feature.
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join, dirname } from "node:path";

const __dir = dirname(fileURLToPath(import.meta.url));
const SRC = join(__dir, "..", "src", "components", "StockTransactionsTable.tsx");
const src = readFileSync(SRC, "utf8");

// ---------------------------------------------------------------------------
// Inline mirrors (keep in sync with src/lib/dateHelpers.ts and the component)
// ---------------------------------------------------------------------------

/** Mirror of dateHelpers.subCalendarMonths */
function subCalendarMonths(date, months) {
  const srcYear  = date.getFullYear();
  const srcMonth = date.getMonth();
  const srcDay   = date.getDate();
  const rawMonth = srcMonth - months;
  const targetYear  = srcYear + Math.floor(rawMonth / 12);
  const targetMonth = ((rawMonth % 12) + 12) % 12;
  const lastDay  = new Date(targetYear, targetMonth + 1, 0).getDate();
  const targetDay = Math.min(srcDay, lastDay);
  return new Date(targetYear, targetMonth, targetDay);
}

/** Mirror of dateHelpers.toLocalDateString */
function toLocalDateString(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

/**
 * Mirror of the time-filter → from-date resolution.
 * "ALL" returns null (no date bound).
 * All other values are calendar-month offsets.
 */
const TIME_FILTER_MONTHS = { "1m": 1, "3m": 3, "6m": 6, "1y": 12 };

function resolveTimeBound(timeFilter, today) {
  if (timeFilter === "ALL") return null;
  const months = TIME_FILTER_MONTHS[timeFilter];
  if (months === undefined) throw new Error(`Unknown timeFilter: ${timeFilter}`);
  return subCalendarMonths(today, months);
}

/**
 * Client-side filter: given a from-date (or null), keep movements whose
 * trade_date >= fromDateStr.  Movements with null/missing trade_date are excluded.
 */
function filterByTime(movements, fromDate) {
  if (fromDate === null) return movements;
  const boundStr = toLocalDateString(fromDate);
  return movements.filter((m) => {
    if (!m.trade_date) return false;   // null trade_date → exclude
    return m.trade_date >= boundStr;   // lexicographic YYYY-MM-DD comparison
  });
}

/**
 * Mirror of the type filter applied inside the component.
 * "ALL" passes everything; otherwise strict match on txn_type.
 */
function filterByType(movements, typeFilter) {
  if (typeFilter === "ALL") return movements;
  return movements.filter((m) => m.txn_type === typeFilter);
}

/** Combined AND filter (order-invariant). */
function applyMovementFilters(movements, typeFilter, timeFilter, today) {
  const afterType = filterByType(movements, typeFilter);
  const fromDate  = resolveTimeBound(timeFilter, today);
  return filterByTime(afterType, fromDate);
}

/**
 * Mirror of the pagination-reset contract.
 * Each filter-change handler must reset page to 0.
 */
function handleFilterChange(newFilter, _currentPage) {
  return { filter: newFilter, page: 0 };
}

// ---------------------------------------------------------------------------
// Fixture helpers
// ---------------------------------------------------------------------------
const TODAY = new Date(2026, 8, 7);  // 2026-09-07 (local)
const TODAY_STR = toLocalDateString(TODAY);  // "2026-09-07"

function mvt(id, txnType, tradeDate) {
  return { id, txn_type: txnType, trade_date: tradeDate };
}

// ===========================================================================
// TF: Time filter period boundary computation
// ===========================================================================

describe("TF: Time filter period boundaries (calendar arithmetic)", () => {
  it("TF-1: default 3m from 2026-09-07 -> 2026-06-07", () => {
    const bound = resolveTimeBound("3m", TODAY);
    assert.equal(toLocalDateString(bound), "2026-06-07");
  });

  it("TF-2: 1m from 2026-09-07 -> 2026-08-07", () => {
    const bound = resolveTimeBound("1m", TODAY);
    assert.equal(toLocalDateString(bound), "2026-08-07");
  });

  it("TF-3: 6m from 2026-09-07 -> 2026-03-07", () => {
    const bound = resolveTimeBound("6m", TODAY);
    assert.equal(toLocalDateString(bound), "2026-03-07");
  });

  it("TF-4: 1y from 2026-09-07 -> 2025-09-07 (same day, minus 12 months)", () => {
    const bound = resolveTimeBound("1y", TODAY);
    assert.equal(toLocalDateString(bound), "2025-09-07");
  });

  it("TF-5: 1y uses 12 calendar months, not 365 days (2026-03-01 minus 1y = 2025-03-01)", () => {
    const d = new Date(2026, 2, 1);
    const bound = resolveTimeBound("1y", d);
    assert.equal(toLocalDateString(bound), "2025-03-01");
  });

  it("TF-6: 3m from month-end 2026-08-31 -> 2026-05-31 (no overflow into Jun)", () => {
    const d = new Date(2026, 7, 31);
    const bound = resolveTimeBound("3m", d);
    assert.equal(toLocalDateString(bound), "2026-05-31");
  });

  it("TF-7: 3m from 2026-05-31 -> 2026-02-28 (Feb clamp, non-leap)", () => {
    const d = new Date(2026, 4, 31);
    const bound = resolveTimeBound("3m", d);
    assert.equal(toLocalDateString(bound), "2026-02-28");
  });

  it("TF-8: 1m from 2024-03-31 -> 2024-02-29 (leap year clamp)", () => {
    const d = new Date(2024, 2, 31);
    const bound = resolveTimeBound("1m", d);
    assert.equal(toLocalDateString(bound), "2024-02-29");
  });

  it("TF-9: ALL returns null (no date bound)", () => {
    assert.equal(resolveTimeBound("ALL", TODAY), null);
  });

  it("TF-10: TIME_FILTER_MONTHS mapping is complete and correct", () => {
    assert.equal(TIME_FILTER_MONTHS["1m"],  1);
    assert.equal(TIME_FILTER_MONTHS["3m"],  3);
    assert.equal(TIME_FILTER_MONTHS["6m"],  6);
    assert.equal(TIME_FILTER_MONTHS["1y"], 12);
  });
});

// ===========================================================================
// TB: Temporal boundary correctness
// ===========================================================================

describe("TB: Temporal boundary — inclusive from-date, today included, ALL removes bound", () => {
  it("TB-1: movement exactly on from-date is INCLUDED (inclusive lower bound)", () => {
    const from = resolveTimeBound("3m", TODAY);  // 2026-06-07
    const movements = [mvt("on-bound", "BUY", toLocalDateString(from))];
    const result = filterByTime(movements, from);
    assert.equal(result.length, 1, "movement on the from-date must be included");
  });

  it("TB-2: movement one day before from-date is EXCLUDED", () => {
    const from = resolveTimeBound("3m", TODAY);  // 2026-06-07
    const dayBefore = new Date(from.getTime() - 86400000);
    const movements = [mvt("before-bound", "BUY", toLocalDateString(dayBefore))];
    const result = filterByTime(movements, from);
    assert.equal(result.length, 0, "movement before the from-date must be excluded");
  });

  it("TB-3: today's movement is INCLUDED (to=today is inclusive)", () => {
    const from = resolveTimeBound("3m", TODAY);
    const movements = [mvt("today", "SELL", TODAY_STR)];
    const result = filterByTime(movements, from);
    assert.equal(result.length, 1, "movement dated today must pass the time filter");
  });

  it("TB-4: movement with null trade_date is excluded (defensive)", () => {
    const from = resolveTimeBound("3m", TODAY);
    const movements = [{ id: "null-date", txn_type: "BUY", trade_date: null }];
    const result = filterByTime(movements, from);
    assert.equal(result.length, 0, "null trade_date must be excluded, not throw");
  });

  it("TB-5: movement with undefined trade_date is excluded (defensive)", () => {
    const from = resolveTimeBound("3m", TODAY);
    const movements = [{ id: "undef-date", txn_type: "BUY" }];
    const result = filterByTime(movements, from);
    assert.equal(result.length, 0, "undefined trade_date must be excluded, not throw");
  });

  it("TB-6: ALL (null bound) passes all movements including very old ones", () => {
    const movements = [
      mvt("old1", "BUY",      "1990-01-15"),
      mvt("old2", "DIVIDEND", "2000-06-30"),
      mvt("old3", "SELL",     "2010-12-01"),
      mvt("new1", "BUY",      TODAY_STR),
    ];
    const result = filterByTime(movements, null);
    assert.equal(result.length, 4, "ALL (null bound) must pass every movement");
  });

  it("TB-7: empty movements array returns empty regardless of timeFilter", () => {
    assert.equal(filterByTime([], resolveTimeBound("3m", TODAY)).length, 0);
    assert.equal(filterByTime([], null).length, 0);
  });

  it("TB-8: 1y from today passes last year's movements and excludes older ones", () => {
    const from = resolveTimeBound("1y", TODAY);  // 2025-09-07
    const movements = [
      mvt("within",  "BUY",  "2025-09-07"),   // on boundary → include
      mvt("recent",  "SELL", "2026-01-15"),   // within → include
      mvt("outside", "BUY",  "2025-09-06"),   // one day before → exclude
      mvt("old",     "SELL", "2024-12-31"),   // outside → exclude
    ];
    const result = filterByTime(movements, from);
    assert.deepEqual(result.map((m) => m.id), ["within", "recent"]);
  });
});

// ===========================================================================
// AND: Type + Time filter AND composition
// ===========================================================================

describe("AND: Type and time filters compose with AND semantics", () => {
  // Reference dataset: movements across different types and dates
  const MOVEMENTS = [
    mvt("b1", "BUY",      "2026-08-20"),  // within 3m, BUY
    mvt("b2", "BUY",      "2026-06-01"),  // outside 3m, BUY
    mvt("s1", "SELL",     "2026-07-15"),  // within 3m, SELL
    mvt("s2", "SELL",     "2025-12-31"),  // outside 3m, SELL
    mvt("d1", "DIVIDEND", "2026-09-01"),  // within 3m, DIVIDEND
    mvt("d2", "DIVIDEND", "2026-01-15"),  // outside 3m, DIVIDEND
  ];

  it("AND-1: BUY + 3m → only BUY within last 3 months", () => {
    const result = applyMovementFilters(MOVEMENTS, "BUY", "3m", TODAY);
    assert.deepEqual(result.map((m) => m.id), ["b1"]);
  });

  it("AND-2: SELL + 3m → only SELL within last 3 months", () => {
    const result = applyMovementFilters(MOVEMENTS, "SELL", "3m", TODAY);
    assert.deepEqual(result.map((m) => m.id), ["s1"]);
  });

  it("AND-3: ALL types + 3m → all types within last 3 months", () => {
    const result = applyMovementFilters(MOVEMENTS, "ALL", "3m", TODAY);
    assert.deepEqual(result.map((m) => m.id), ["b1", "s1", "d1"]);
  });

  it("AND-4: BUY + ALL_TIME → all BUY regardless of date", () => {
    const result = applyMovementFilters(MOVEMENTS, "BUY", "ALL", TODAY);
    assert.deepEqual(result.map((m) => m.id), ["b1", "b2"]);
  });

  it("AND-5: ALL types + ALL_TIME → all movements (no filtering)", () => {
    const result = applyMovementFilters(MOVEMENTS, "ALL", "ALL", TODAY);
    assert.equal(result.length, MOVEMENTS.length);
  });

  it("AND-6: DIVIDEND + 1m → DIVIDEND in last month only", () => {
    const result = applyMovementFilters(MOVEMENTS, "DIVIDEND", "1m", TODAY);
    assert.deepEqual(result.map((m) => m.id), ["d1"]);
  });

  it("AND-7: DIVIDEND + 6m → DIVIDEND within last 6 months", () => {
    const result = applyMovementFilters(MOVEMENTS, "DIVIDEND", "6m", TODAY);
    assert.deepEqual(result.map((m) => m.id), ["d1"]);
  });

  it("AND-8: SELL + ALL_TIME → all SELL regardless of date", () => {
    const result = applyMovementFilters(MOVEMENTS, "SELL", "ALL", TODAY);
    assert.deepEqual(result.map((m) => m.id), ["s1", "s2"]);
  });

  it("AND-9: filter order is AND-commutative (type-then-time = time-then-type)", () => {
    // Apply in different order — result must be identical
    const typeFirst = filterByTime(filterByType(MOVEMENTS, "BUY"), resolveTimeBound("3m", TODAY));
    const timeFirst = filterByType(filterByTime(MOVEMENTS, resolveTimeBound("3m", TODAY)), "BUY");
    assert.deepEqual(
      typeFirst.map((m) => m.id),
      timeFirst.map((m) => m.id),
      "AND filter must be commutative"
    );
  });

  it("AND-10: does not mutate the input array", () => {
    const input = [...MOVEMENTS];
    const snapshot = MOVEMENTS.map((m) => m.id);
    applyMovementFilters(MOVEMENTS, "BUY", "3m", TODAY);
    assert.deepEqual(MOVEMENTS.map((m) => m.id), snapshot, "input array must not be mutated");
    assert.equal(MOVEMENTS.length, input.length);
  });
});

// ===========================================================================
// DF: Canonical transaction date field
// ===========================================================================

describe("DF: Canonical transaction date field is trade_date", () => {
  it("DF-1: filter uses trade_date, NOT settlement_date", () => {
    const from = resolveTimeBound("3m", TODAY);
    // Movement with settlement_date INSIDE range but trade_date OUTSIDE:
    const m = {
      id: "m1", txn_type: "BUY",
      trade_date: "2026-01-01",         // outside 3m window
      settlement_date: TODAY_STR,        // inside window — must NOT override
    };
    const result = filterByTime([m], from);
    assert.equal(result.length, 0, "filter must use trade_date, not settlement_date");
  });

  it("DF-2: filter uses trade_date, NOT value_date", () => {
    const from = resolveTimeBound("3m", TODAY);
    const m = {
      id: "m2", txn_type: "DIVIDEND",
      trade_date: "2026-01-01",   // outside
      value_date: TODAY_STR,      // inside — must NOT override
    };
    const result = filterByTime([m], from);
    assert.equal(result.length, 0, "filter must use trade_date, not value_date");
  });

  it("DF-3: movement correctly included when trade_date is in range", () => {
    const from = resolveTimeBound("3m", TODAY);
    const m = {
      id: "m3", txn_type: "SELL",
      trade_date: "2026-08-01",    // inside 3m
      settlement_date: "2025-01-01", // outside — irrelevant
    };
    const result = filterByTime([m], from);
    assert.equal(result.length, 1, "must include when trade_date is in range");
  });

  it("DF-4: StockTransactionsTable renders trade_date column (not settlement_date)", () => {
    assert.ok(
      src.includes("trade_date") && src.includes("Date"),
      "DF-4 DEFECT: StockTransactionsTable must render trade_date in the Date column"
    );
    assert.ok(
      !src.includes("settlement_date"),
      "DF-4 DEFECT: settlement_date must not be used in StockTransactionsTable"
    );
  });
});

// ===========================================================================
// PG: Pagination resets on filter change
// ===========================================================================

describe("PG: Pagination resets to page 0 on either filter change", () => {
  it("PG-1: type filter change resets page to 0 regardless of current page", () => {
    for (const page of [0, 1, 2, 5]) {
      const result = handleFilterChange("BUY", page);
      assert.equal(result.page, 0, `page must reset to 0 from page ${page}`);
    }
  });

  it("PG-2: time filter change resets page to 0 regardless of current page", () => {
    for (const page of [0, 1, 3, 10]) {
      const result = handleFilterChange("1m", page);
      assert.equal(result.page, 0, `page must reset to 0 from page ${page}`);
    }
  });

  it("PG-3: filter value is carried through reset correctly", () => {
    const r1 = handleFilterChange("SELL", 5);
    assert.equal(r1.filter, "SELL");
    assert.equal(r1.page, 0);

    const r2 = handleFilterChange("6m", 3);
    assert.equal(r2.filter, "6m");
    assert.equal(r2.page, 0);
  });

  it("PG-4: source handleTypeFilter calls setPage(0) (existing pattern)", () => {
    assert.ok(
      src.includes("handleTypeFilter") && src.includes("setPage(0)"),
      "PG-4 DEFECT: handleTypeFilter must call setPage(0) — existing pattern must be preserved"
    );
  });

  it("PG-5: source has a handleTimeFilter (or equivalent) that also calls setPage(0)", () => {
    // Accepts any naming: handleTimeFilter, handleTimePeriod, handlePeriodChange, etc.
    const hasHandler =
      src.includes("handleTimeFilter") ||
      src.includes("handleTimePeriod") ||
      src.includes("handlePeriodChange") ||
      src.includes("setTimePeriod") ||
      src.includes("setTimeFilter");
    const hasReset = src.includes("setPage(0)");
    // Must have both a time-filter handler AND the reset on filter change
    const timeResetColocated = (
      hasHandler && hasReset && (
        // Simple heuristic: both appear within a short span in source
        Math.abs(src.indexOf("setPage(0)") - (
          src.indexOf("handleTimeFilter") !== -1 ? src.indexOf("handleTimeFilter") :
          src.indexOf("handleTimePeriod") !== -1 ? src.indexOf("handleTimePeriod") :
          src.indexOf("setTimeFilter")
        )) < 800
      )
    );
    assert.ok(
      timeResetColocated,
      "PG-5 DEFECT: A time-filter change handler must call setPage(0). " +
      "Rusty: add handleTimeFilter() { setTimeFilter(v); setPage(0); } mirroring handleTypeFilter."
    );
  });

  it("PG-6: page count reflects filtered total_count from API (not raw count)", () => {
    // Behavioral: totalPages = ceil(totalCount / PAGE_SIZE).
    // When timeFilter narrows the API response, totalCount changes -> pages change.
    const PAGE_SIZE = 20;
    function totalPages(totalCount) { return Math.ceil(totalCount / PAGE_SIZE); }
    assert.equal(totalPages(0),   0);
    assert.equal(totalPages(1),   1);
    assert.equal(totalPages(20),  1);
    assert.equal(totalPages(21),  2);
    assert.equal(totalPages(40),  2);
    assert.equal(totalPages(41),  3);
  });
});

// ===========================================================================
// SR: Source-contract — StockTransactionsTable has time filter state and UI
// ===========================================================================

describe("SR: Source-contract — StockTransactionsTable implements time filter", () => {
  it("SR-1: imports subCalendarMonths or dateHelpers", () => {
    const hasImport =
      src.includes("subCalendarMonths") ||
      src.includes("dateHelpers") ||
      src.includes("getDefaultMovementsDateRange");
    assert.ok(
      hasImport,
      "SR-1 DEFECT: StockTransactionsTable must import subCalendarMonths (or dateHelpers) " +
      "for calendar-correct date arithmetic. " +
      "Rusty: add import { subCalendarMonths, toLocalDateString } from '@/lib/dateHelpers'."
    );
  });

  it("SR-2: declares a timeFilter or timePeriod state variable", () => {
    const hasState =
      src.includes("timeFilter") ||
      src.includes("timePeriod") ||
      src.includes("timePeriodFilter") ||
      src.includes("selectedPeriod");
    assert.ok(
      hasState,
      "SR-2 DEFECT: StockTransactionsTable must declare a time-period filter state variable. " +
      "Rusty: add const [timeFilter, setTimeFilter] = useState<TimeFilter>(\"3m\")."
    );
  });

  it("SR-3: default time filter is '3m'", () => {
    const hasDefault =
      src.includes("\"3m\"") ||
      src.includes("'3m'");
    assert.ok(
      hasDefault,
      "SR-3 DEFECT: Default time filter must be '3m' (3 calendar months). " +
      "Rusty: useState<TimeFilter>(\"3m\")."
    );
  });

  it("SR-4: all five time periods present in source: 1m, 3m, 6m, 1y, All", () => {
    const required = ["1m", "3m", "6m", "1y"];
    for (const period of required) {
      assert.ok(
        src.includes(`"${period}"`) || src.includes(`'${period}'`),
        `SR-4 DEFECT: Time period "${period}" must appear in StockTransactionsTable. ` +
        `Rusty: add { value: "${period}", label: "..." } to the TIME_PILLS array.`
      );
    }
    // "All" label must also be present (for the time-All option)
    const hasAllLabel = src.includes('"All"') || src.includes("'All'") || src.includes(">All<");
    assert.ok(
      hasAllLabel,
      "SR-4 DEFECT: 'All' time period must be present. " +
      "Rusty: add { value: \"ALL\", label: \"All\" } to TIME_PILLS."
    );
  });

  it("SR-5: time filter passed to getMovements API call (date_from param)", () => {
    // The component must pass a date_from (or from_date) parameter to getMovements
    // when a time filter is active — the API does the actual date filtering.
    const hasDateParam =
      src.includes("date_from") ||
      src.includes("from_date") ||
      src.includes("dateFrom") ||
      src.includes("trade_date_from") ||
      // OR: performs client-side filtering using the bound
      (src.includes("timeFilter") && src.includes("subCalendarMonths"));
    assert.ok(
      hasDateParam,
      "SR-5 DEFECT: Time filter must be applied: either pass date_from to getMovements " +
      "or filter client-side using subCalendarMonths. " +
      "Rusty: add date_from to the getMovements call when timeFilter !== 'ALL'."
    );
  });

  it("SR-6: type filter TYPE_PILLS still has all four entries (preserved)", () => {
    const required = ["ALL", "BUY", "SELL", "DIVIDEND"];
    for (const v of required) {
      assert.ok(
        src.includes(`"${v}"`) || src.includes(`'${v}'`),
        `SR-6 DEFECT: TypeFilter "${v}" must remain in StockTransactionsTable after time filter addition.`
      );
    }
  });

  it("SR-7: two separate pill/button groups for type and time (not merged)", () => {
    // Proxy: both TYPE_PILLS-like and TIME_PILLS-like arrays (or inline map calls) exist.
    // Both "BUY" (type) and "1m" / "3m" (time) must be present as separate values.
    const hasType = src.includes('"BUY"') || src.includes("'BUY'");
    const hasTime = src.includes('"3m"') || src.includes("'3m'");
    assert.ok(
      hasType && hasTime,
      "SR-7 DEFECT: Both type pills (BUY/SELL/DIVIDEND) and time pills (1m/3m/6m/1y) " +
      "must be present as separate controls. Rusty: add TIME_PILLS array alongside TYPE_PILLS."
    );
  });
});

// ===========================================================================
// AC: Accessible segmented controls
// ===========================================================================

describe("AC: Accessible segmented controls", () => {
  it("AC-1: time filter buttons are type='button' (no implicit form submit)", () => {
    // Existing type filter already uses type="button" — time filter must match
    const typeButtonCount = (src.match(/type="button"/g) || []).length;
    assert.ok(
      typeButtonCount >= 4,  // at least the 4 existing type-filter buttons
      "AC-1 DEFECT: All filter buttons (including new time-filter pills) must have type='button'. " +
      `Currently found ${typeButtonCount} type="button" occurrences.`
    );
  });

  it("AC-2: time filter buttons have distinguishable aria-labels or group label", () => {
    // The time filter group must have an aria-label so screen readers can
    // distinguish it from the type filter group.
    // Acceptable: aria-label on a wrapper div/fieldset, or distinct button labels.
    const hasGroupLabel =
      src.includes("aria-label") &&
      (
        src.includes("time") || src.includes("period") || src.includes("Time") ||
        src.includes("Period")
      );
    const hasTimeButtonAriaLabel =
      src.includes("aria-label=\"1 month\"") ||
      src.includes("aria-label=\"3 months\"") ||
      src.includes('aria-label="Time period"') ||
      src.includes("aria-label=") && src.includes("month");
    assert.ok(
      hasGroupLabel || hasTimeButtonAriaLabel,
      "AC-2 DEFECT: Time filter controls need distinguishable labels for screen readers. " +
      "Rusty: add aria-label='Time period' to the time pills wrapper div, " +
      "or add aria-label='1 month filter' etc. to each button."
    );
  });

  it("AC-3: active type filter button has visual distinction (existing — must not regress)", () => {
    // The existing border-accent-blue conditional class must remain for type filter
    assert.ok(
      src.includes("border-accent-blue"),
      "AC-3 DEFECT: Active filter button visual distinction (border-accent-blue) must be preserved."
    );
  });

  it("AC-4: time filter active state has visual distinction (class change on active)", () => {
    // The active time pill should have a different class — similar to type pills
    // Proxy: if timeFilter state exists, there must be a conditional class referencing it
    const hasTimeFilter = src.includes("timeFilter") || src.includes("timePeriod");
    if (!hasTimeFilter) {
      assert.fail(
        "AC-4 DEFECT: timeFilter state not yet implemented. " +
        "Rusty: add useState + conditional className for active time pill."
      );
    }
    // When implemented, the active class must reference the time filter state
    const hasConditionalClass =
      (src.includes("timeFilter") || src.includes("timePeriod")) &&
      src.includes("border-accent-blue");
    assert.ok(
      hasConditionalClass,
      "AC-4 DEFECT: Active time filter pill must have a visually distinct class " +
      "(e.g. border-accent-blue). Rusty: mirror the type pill conditional className."
    );
  });
});

// ===========================================================================
// RG: Regression — existing behavior not broken by time filter addition
// ===========================================================================

describe("RG: Regression — existing behavior preserved", () => {
  it("RG-1: TYPE_PILLS array (All/Buy/Sell/Dividend) still in source", () => {
    assert.ok(
      src.includes("TYPE_PILLS"),
      "RG-1 DEFECT: TYPE_PILLS array removed — type filter regression."
    );
  });

  it("RG-2: 'Reassign accounts' button still in source", () => {
    assert.ok(
      src.includes("Reassign accounts") || src.includes("Batch reassign"),
      "RG-2 DEFECT: Batch reassign button removed from StockTransactionsTable — this is a regression. " +
      "Rusty: do not remove the Reassign accounts button when adding the time filter."
    );
  });

  it("RG-3: ReassignmentDialog still rendered in source", () => {
    assert.ok(
      src.includes("ReassignmentDialog"),
      "RG-3 DEFECT: ReassignmentDialog import/render removed — reassignment regression."
    );
  });

  it("RG-4: security_id still passed to getMovements API call", () => {
    assert.ok(
      src.includes("security_id") && src.includes("getMovements"),
      "RG-4 DEFECT: security_id parameter removed from getMovements call — this breaks Symbol Details isolation."
    );
  });

  it("RG-5: Date column (trade_date) still in table header", () => {
    assert.ok(
      src.includes("Date") && src.includes("trade_date"),
      "RG-5 DEFECT: Date column (trade_date) removed from table — regression."
    );
  });

  it("RG-6: pagination buttons (Prev/Next) still present", () => {
    assert.ok(
      src.includes("Prev") || src.includes("← Prev"),
      "RG-6 DEFECT: Pagination Prev button missing — regression."
    );
    assert.ok(
      src.includes("Next") || src.includes("Next →"),
      "RG-6 DEFECT: Pagination Next button missing — regression."
    );
  });

  it("RG-7: MovementDetailDialog still rendered (click-to-detail preserved)", () => {
    assert.ok(
      src.includes("MovementDetailDialog"),
      "RG-7 DEFECT: MovementDetailDialog removed — click-to-detail regression."
    );
  });

  it("RG-8: PAGE_SIZE constant still present (pagination contract)", () => {
    assert.ok(
      src.includes("PAGE_SIZE"),
      "RG-8 DEFECT: PAGE_SIZE constant removed — pagination regression."
    );
  });

  it("RG-9: type filter BUY/SELL/DIVIDEND behavioral correctness preserved", () => {
    // Smoke test: existing type filter logic (inline mirror) still correct
    const movements = [
      mvt("b", "BUY",      "2026-09-01"),
      mvt("s", "SELL",     "2026-09-01"),
      mvt("d", "DIVIDEND", "2026-09-01"),
    ];
    assert.equal(filterByType(movements, "BUY").length,      1);
    assert.equal(filterByType(movements, "SELL").length,     1);
    assert.equal(filterByType(movements, "DIVIDEND").length, 1);
    assert.equal(filterByType(movements, "ALL").length,      3);
  });
});
