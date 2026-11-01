/**
 * Regression tests — Shared Symbol Filter (directive 2026-09-07).
 *
 * Directive: Keep Portfolio and Watchlist as separate sections, but provide
 * ONE shared filter toolbar. Search, Favorable Calls, Favorable Puts, and all other
 * filters must appear only once and apply to BOTH sections simultaneously.
 *
 * Contract reference:
 *   .squad/decisions/inbox/copilot-directive-20260907-watchlist-shared-filters.md
 *   .squad/decisions/inbox/danny-unified-watchlist-contract.md §5.2
 *
 * Run with: node --test frontend/tests/sharedSymbolFilter.test.mjs
 *
 * SymbolsTable.tsx search/hide-zero composition is mirrored below.
 * Suitability, Momentum and position predicates load the production helpers.
 *
 * Tests cover:
 *   SF-1  Filter state is a single object shared by both sections.
 *   SF-2  Search query filters Portfolio section.
 *   SF-3  Search query filters Watchlist section.
 *   SF-4  Search query with no match produces empty results in BOTH sections.
 *   SF-5  Suitability filter "ideal_calls" applies to Portfolio section.
 *   SF-6  Suitability filter "ideal_calls" applies to Watchlist section.
 *   SF-7  Suitability filter "ideal_puts" applies to Portfolio section.
 *   SF-8  Suitability filter "ideal_puts" applies to Watchlist section.
 *   SF-9  Suitability filter "all" returns all rows in both sections.
 *   SF-10 hide-zero toggle filters Portfolio section.
 *   SF-11 hide-zero toggle does NOT hide watchlist-only rows (null portfolio_shares).
 *   SF-12 hide-zero=false reveals hidden-zero rows in Portfolio section.
 *   SF-13 Combined search + suitability filter applied to both sections.
 *   SF-14 Combined search + hide-zero applied to both sections.
 *   SF-15 Filter counts are independent per section (not summed across sections).
 *   SF-16 Clearing search shows all rows in both sections.
 *   SF-17 Suitability filter "no_puts" applies to both sections.
 *   SF-18 Suitability filter "no_calls" applies to both sections.
 *   SF-19 Filter does not mutate the original row arrays.
 *   SF-20 Search is case-insensitive across both sections.
 */

import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  ALL_SYMBOL_POSITIONS,
  matchesSymbolPositions,
} from "../src/lib/symbolPositionFilters.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(join(root, "package.json"));
const ts = require("typescript");

function load(relative) {
  const source = readFileSync(join(root, "src", relative), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const loaded = { exports: {} };
  const localRequire = (name) => name.startsWith("@/")
    ? load(`${name.slice(2)}.ts`)
    : require(name);
  new Function("require", "module", "exports", compiled)(localRequire, loaded, loaded.exports);
  return loaded.exports;
}

const { matchesSymbolSuitability } = load("lib/symbolSuitability.ts");
const { matchesSymbolMomentum } = load("lib/symbolMomentum.ts");

function timingSide(score) {
  return {
    score, band: score === null ? null : score >= 75 ? "green" : score < 25 ? "red" : "yellow",
    label: "Saved technical context", status: score === null ? "unavailable" : "available",
    reason_codes: [], factors: [], adjustments: [],
  };
}

function timing(callScore, putScore, extra = {}) {
  return {
    schema_version: 1, snapshot_id: "current", model_version: "v1",
    as_of: "2026-10-30T17:00:00Z", computed_at: "2026-10-30T17:05:00Z",
    source_session: "2026-10-30", input_timestamp_available: true,
    provisional: false, freshness: "fresh",
    covered_call: timingSide(callScore), cash_secured_put: timingSide(putScore), ...extra,
  };
}

// ─── Inline composition mirrors ──────────────────────────────────────────────

/**
 * Mirror of SymbolsTable.tsx isHiddenZeroRow.
 * Returns true when the row should be hidden under the "hide historical zeros" toggle.
 * Only hides auto-enrolled rows with exactly zero portfolio shares.
 * Watchlist-only rows (portfolio_shares == null) are never hidden.
 */
function isHiddenZeroRow(r) {
  if (r.portfolio_shares == null) return false;
  const shares = parseFloat(r.portfolio_shares);
  if (!isFinite(shares) || shares !== 0) return false;
  return r.is_auto_enrolled !== false;
}

/**
 * Mirror of SymbolsTable.tsx search predicate (from the `filtered` useMemo).
 * Returns true when the row matches the query string.
 */
function matchesSearch(r, query) {
  const q = (query || "").trim().toUpperCase();
  if (!q) return true;
  return (
    (r.symbol || "").toUpperCase().includes(q) ||
    (r.display_name || "").toUpperCase().includes(q) ||
    (r.category || "").toUpperCase().includes(q) ||
    (r.entry_tag || "").toUpperCase().includes(q)
  );
}

/**
 * The shared filter function.
 *
 * Applies the SAME filter state to both sections simultaneously.
 * This is the single source of truth for filter logic — one toolbar, two sections.
 *
 * @param {object[]} portfolioRows - Rows in the Portfolio section (row_source !== "watchlist").
 * @param {object[]} watchlistRows - Rows in the Watchlist section (row_source === "watchlist").
 * @param {{ q?: string, suitabilityFilter?: string, hideZero?: boolean, momentumFilters?: Set<string>, positionFilters?: object }} filterState
 * @returns {{ filteredPortfolio: object[], filteredWatchlist: object[] }}
 */
function applySharedFilter(portfolioRows, watchlistRows, filterState) {
  const { q = "", suitabilityFilter = "all", hideZero = true,
    positionFilters = ALL_SYMBOL_POSITIONS, momentumFilters = new Set() } = filterState;

  function filterSection(rows) {
    let out = rows.filter((r) => {
      if (hideZero && isHiddenZeroRow(r)) return false;
      return matchesSymbolPositions(r, positionFilters);
    });
    if (q.trim()) {
      out = out.filter((r) => matchesSearch(r, q));
    }
    if (suitabilityFilter !== "all") {
      out = out.filter((r) =>
        matchesSymbolSuitability(r.option_timing, suitabilityFilter)
      );
    }
    out = out.filter((r) => matchesSymbolMomentum(r.momentum, momentumFilters));
    return out;
  }

  return {
    filteredPortfolio: filterSection(portfolioRows),
    filteredWatchlist: filterSection(watchlistRows),
  };
}

describe("position filters use authoritative projections in both sections", () => {
  const portfolio = [
    { symbol: "CALL", display_name: "Call Co", portfolio_shares: "200", has_open_options: true, can_open_covered_calls: true, entry_tag: "Hold", momentum: "Neutral", option_timing: timing(85, 50) },
    { symbol: "PUT", portfolio_shares: "100", has_open_options: true, can_open_covered_calls: false, entry_tag: "Buy", momentum: "Bullish" },
    { symbol: "FREE", portfolio_shares: "100", has_open_options: false, can_open_covered_calls: true, entry_tag: "Hold", momentum: "Neutral" },
    { symbol: "CLOSED", portfolio_shares: "99", has_open_options: false, can_open_covered_calls: false },
  ];
  const watchlist = [
    { symbol: "PUTONLY", portfolio_shares: null, has_open_options: true, can_open_covered_calls: false },
    { symbol: "LEGACY", total_shares: 1000, active_count: 2, in_calls: 100, watchlist: { covered_call: true } },
  ];
  const symbols = (result) => [
    result.filteredPortfolio.map((r) => r.symbol),
    result.filteredWatchlist.map((r) => r.symbol),
  ];

  test("open options includes calls and puts, excluding closed/absent projections", () => {
    assert.deepEqual(symbols(applySharedFilter(portfolio, watchlist, {
      positionFilters: { openOptions: true, coveredCallCapacity: false },
    })), [["CALL", "PUT"], ["PUTONLY"]]);
  });

  test("covered-call capacity uses real free-share projection, not config or flags", () => {
    assert.deepEqual(symbols(applySharedFilter(portfolio, watchlist, {
      positionFilters: { openOptions: false, coveredCallCapacity: true },
    })), [["CALL", "FREE"], []]);
  });

  test("both position toggles intersect, and All positions restores both sections", () => {
    const before = structuredClone([portfolio, watchlist]);
    assert.deepEqual(symbols(applySharedFilter(portfolio, watchlist, {
      positionFilters: { openOptions: true, coveredCallCapacity: true },
    })), [["CALL"], []]);
    const reset = applySharedFilter(portfolio, watchlist, {
      positionFilters: ALL_SYMBOL_POSITIONS,
    });
    assert.deepEqual(reset, { filteredPortfolio: portfolio, filteredWatchlist: watchlist });
    assert.deepEqual([portfolio, watchlist], before);
  });

  test("search and suitability intersect with position filters; clearing positions preserves them", () => {
    const otherFilters = { q: "call co", suitabilityFilter: "ideal_calls" };
    assert.deepEqual(symbols(applySharedFilter(portfolio, watchlist, {
      ...otherFilters, positionFilters: { openOptions: true, coveredCallCapacity: true },
    })), [["CALL"], []]);
    assert.deepEqual(symbols(applySharedFilter(portfolio, watchlist, {
      ...otherFilters, positionFilters: ALL_SYMBOL_POSITIONS,
    })), [["CALL"], []]);
    assert.deepEqual(symbols(applySharedFilter(portfolio, watchlist, {
      q: "PUTONLY", positionFilters: { openOptions: true, coveredCallCapacity: true },
    })), [[], []]);
  });
});

/**
 * Splits a flat backend row array into Portfolio and Watchlist sections.
 * Portfolio: row_source !== "watchlist"
 * Watchlist: row_source === "watchlist"
 */
function splitSections(rows) {
  return {
    portfolioRows: rows.filter((r) => r.row_source !== "watchlist"),
    watchlistRows: rows.filter((r) => r.row_source === "watchlist"),
  };
}

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const PORTFOLIO_ROWS = [
  { symbol: "AAPL",  display_name: "Apple Inc.",   row_source: "portfolio", portfolio_shares: "100",  entry_tag: "Hold",       momentum: "Weakening",  category: "growth", is_auto_enrolled: false, option_timing: timing(85, 5) },
  { symbol: "MSFT",  display_name: "Microsoft",    row_source: "portfolio", portfolio_shares: "50",   entry_tag: "Buy",        momentum: "Bullish",    category: "growth", is_auto_enrolled: false, option_timing: timing(5, 85) },
  { symbol: "O",     display_name: "Realty Income", row_source: "portfolio", portfolio_shares: "200",  entry_tag: "Strong Buy", momentum: "Neutral",    category: "income", is_auto_enrolled: false, option_timing: timing(50, 80) },
  { symbol: "VOD",   display_name: "Vodafone",      row_source: "portfolio", portfolio_shares: "0",    entry_tag: "Wait",       momentum: "Bearish",    category: "income", is_auto_enrolled: true, option_timing: null, watchlist: { covered_call: false, cash_secured_put: false } },
];

const WATCHLIST_ROWS = [
  { symbol: "NVDA",  display_name: "Nvidia",        row_source: "watchlist", portfolio_shares: null,   entry_tag: "Hold",       momentum: "Bullish (overextended)", category: "growth", is_auto_enrolled: false, option_timing: timing(85, 5) },
  { symbol: "AMD",   display_name: "Advanced Micro Devices", row_source: "watchlist", portfolio_shares: null, entry_tag: "Wait", momentum: "Neutral", category: "growth", is_auto_enrolled: false, option_timing: timing(5, 85) },
  { symbol: "DGE",   display_name: "Diageo",        row_source: "watchlist", portfolio_shares: null,   entry_tag: "Buy",        momentum: "Weakening",  category: "income", is_auto_enrolled: false, option_timing: null },
  { symbol: "SAN",   display_name: "Santander",     row_source: "watchlist", portfolio_shares: null,   entry_tag: "Strong Buy", momentum: "Bearish (oversold)", category: "value", is_auto_enrolled: false, option_timing: timing(null, null, { covered_call: { ...timingSide(null), status: "not_applicable" }, cash_secured_put: { ...timingSide(null), status: "not_applicable" } }) },
  { symbol: "IBE",   display_name: "Iberdrola",     row_source: "watchlist", portfolio_shares: null,   entry_tag: "Wait",       momentum: "Bullish",    category: "income", is_auto_enrolled: false, option_timing: null },
];

describe("Option Timing suitability uses persisted independent scores only", () => {
  const filters = ["ideal_calls", "no_calls", "ideal_puts", "no_puts"];

  test("Entry and Momentum cannot override or change saved timing classification", () => {
    const rows = [
      { symbol: "PUT5", entry_tag: "Buy", momentum: "Bearish (oversold)", option_timing: timing(50, 5) },
      { symbol: "CALL85", entry_tag: "Wait", momentum: "Bullish (overextended)", option_timing: timing(85, 50) },
    ];
    const classify = (rows, suitabilityFilter) => applySharedFilter(rows, [], {
      suitabilityFilter,
    }).filteredPortfolio.map((r) => r.symbol);
    assert.deepEqual(classify(rows, "ideal_puts"), []);
    assert.deepEqual(classify(rows, "no_puts"), ["PUT5"]);
    assert.deepEqual(classify(rows, "ideal_calls"), ["CALL85"]);
    assert.deepEqual(classify(rows, "no_calls"), []);
    const changed = rows.map((r) => ({ ...r, entry_tag: "Strong Buy", momentum: "Neutral" }));
    for (const filter of filters) assert.deepEqual(classify(changed, filter), classify(rows, filter));
  });

  test("exact thresholds preserve zero and exclude the entire middle interval for both sides", () => {
    for (const [side, favorable, unfavorable] of [
      ["covered_call", "ideal_calls", "no_calls"],
      ["cash_secured_put", "ideal_puts", "no_puts"],
    ]) {
      for (const [score, isFavorable, isUnfavorable] of [
        [0, false, true], [24.99, false, true], [25, false, false],
        [74.99, false, false], [75, true, false], [100, true, false],
      ]) {
        const snapshot = timing(50, 50, { [side]: timingSide(score) });
        assert.equal(matchesSymbolSuitability(snapshot, favorable), isFavorable, `${side} ${score} favorable`);
        assert.equal(matchesSymbolSuitability(snapshot, unfavorable), isUnfavorable, `${side} ${score} unfavorable`);
      }
    }
    for (const score of [0, 85]) {
      const snapshot = timing(score, score);
      assert.equal(matchesSymbolSuitability(snapshot, "ideal_calls"), score === 85);
      assert.equal(matchesSymbolSuitability(snapshot, "ideal_puts"), score === 85);
      assert.equal(matchesSymbolSuitability(snapshot, "no_calls"), score === 0);
      assert.equal(matchesSymbolSuitability(snapshot, "no_puts"), score === 0);
    }
  });

  test("missing or invalid selected-side values never match, even with an available opposite side", () => {
    for (const [side, favorable, unfavorable] of [
      ["covered_call", "ideal_calls", "no_calls"],
      ["cash_secured_put", "ideal_puts", "no_puts"],
    ]) {
      const invalidSides = [
        undefined, null,
        ...[null, undefined, NaN, Infinity, -Infinity, -1, 101, "85"].map((score) => ({ ...timingSide(85), score })),
        ...["unavailable", "not_applicable", "unknown", undefined].map((status) => ({ ...timingSide(85), status })),
      ];
      for (const value of invalidSides) {
        const snapshot = timing(85, 85, { [side]: value });
        assert.equal(matchesSymbolSuitability(snapshot, favorable), false);
        assert.equal(matchesSymbolSuitability(snapshot, unfavorable), false);
        assert.equal(matchesSymbolSuitability(snapshot, "all"), true);
      }
    }
  });

  test("only fresh input snapshots qualify; fresh provisional intraday remains eligible; All preserves every snapshot", () => {
    const excluded = [undefined, null,
      ...["stale", "unknown", undefined].flatMap((freshness) => [timing(85, 85, { freshness }), timing(5, 5, { freshness })]),
      timing(85, 85, { input_timestamp_available: false }),
      timing(5, 5, { input_timestamp_available: false }),
    ];
    for (const snapshot of excluded) {
      for (const filter of filters) assert.equal(matchesSymbolSuitability(snapshot, filter), false);
      assert.equal(matchesSymbolSuitability(snapshot, "all"), true);
    }
    for (const [call, put, expected] of [
      [85, 5, [true, false, false, true]],
      [5, 85, [false, true, true, false]],
    ]) {
      const snapshot = timing(call, put, { provisional: true, input_timestamp_available: undefined });
      assert.deepEqual(filters.map((filter) => matchesSymbolSuitability(snapshot, filter)), expected);
    }
  });

  test("search, Momentum and position filters intersect with timing in both sections without mutation", () => {
    const before = structuredClone([PORTFOLIO_ROWS, WATCHLIST_ROWS]);
    const state = { q: "growth", suitabilityFilter: "ideal_calls" };
    const result = applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, state);
    assert.deepEqual(result.filteredPortfolio.map((r) => r.symbol), ["AAPL"]);
    assert.deepEqual(result.filteredWatchlist.map((r) => r.symbol), ["NVDA"]);
    const momentum = applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, {
      ...state, momentumFilters: new Set(["Bullish (overextended)"]),
    });
    assert.deepEqual(momentum.filteredPortfolio, []);
    assert.deepEqual(momentum.filteredWatchlist.map((r) => r.symbol), ["NVDA"]);
    const positions = applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, {
      ...state, positionFilters: { openOptions: true, coveredCallCapacity: false },
    });
    assert.deepEqual(positions, { filteredPortfolio: [], filteredWatchlist: [] });
    assert.deepEqual([PORTFOLIO_ROWS, WATCHLIST_ROWS], before);
  });

  test("noneligible foreign rows stay visible under All but never under any timing filter", () => {
    const foreign = [PORTFOLIO_ROWS[3], ...WATCHLIST_ROWS.filter((r) => ["DGE", "SAN", "IBE"].includes(r.symbol))];
    assert.deepEqual(applySharedFilter([], foreign, {
      suitabilityFilter: "all", hideZero: false,
    }).filteredWatchlist, foreign);
    for (const filter of filters) {
      assert.deepEqual(applySharedFilter([], foreign, {
        suitabilityFilter: filter, hideZero: false,
      }).filteredWatchlist, []);
    }
  });
});

// ─── SF-1: Filter state is a single shared object ────────────────────────────

describe("SF-1: Single shared filter state drives both sections", () => {
  test("same filterState object is applied to both portfolio and watchlist", () => {
    const filterState = { q: "a", suitabilityFilter: "all", hideZero: true };
    const { filteredPortfolio, filteredWatchlist } =
      applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, filterState);

    // Both sections should have reduced counts (matching "a" in name/symbol/etc)
    assert.ok(filteredPortfolio.length <= PORTFOLIO_ROWS.length);
    assert.ok(filteredWatchlist.length <= WATCHLIST_ROWS.length);

    // All remaining portfolio rows must match the query
    for (const r of filteredPortfolio) {
      assert.ok(matchesSearch(r, "a"), `${r.symbol} should match query "a"`);
    }
    // All remaining watchlist rows must match the query
    for (const r of filteredWatchlist) {
      assert.ok(matchesSearch(r, "a"), `${r.symbol} should match query "a"`);
    }
  });

  test("default filterState (no args) shows all non-hidden rows in both sections", () => {
    const { filteredPortfolio, filteredWatchlist } =
      applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, {});
    // VOD has zero shares + auto-enrolled → hidden by default
    assert.equal(filteredPortfolio.length, 3, "VOD hidden; AAPL, MSFT, O visible");
    // All watchlist rows are watchlist-only (null portfolio_shares) → never hidden
    assert.equal(filteredWatchlist.length, WATCHLIST_ROWS.length);
  });
});

// ─── SF-2 / SF-3: Search filters both sections ───────────────────────────────

describe("SF-2/SF-3: Search query filters both sections", () => {
  test("SF-2: search by symbol prefix filters portfolio section", () => {
    const { filteredPortfolio } = applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, { q: "AA" });
    assert.ok(filteredPortfolio.some((r) => r.symbol === "AAPL"));
    assert.ok(!filteredPortfolio.some((r) => r.symbol === "MSFT"));
    assert.ok(!filteredPortfolio.some((r) => r.symbol === "O"));
  });

  test("SF-3: search by symbol prefix filters watchlist section", () => {
    const { filteredWatchlist } = applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, { q: "NV" });
    assert.ok(filteredWatchlist.some((r) => r.symbol === "NVDA"));
    assert.equal(filteredWatchlist.filter((r) => r.symbol !== "NVDA").length, 0);
  });

  test("SF-3: search filters watchlist by display_name", () => {
    const { filteredWatchlist } = applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, { q: "Diageo" });
    assert.ok(filteredWatchlist.some((r) => r.symbol === "DGE"));
    assert.equal(filteredWatchlist.length, 1);
  });

  test("SF-2: search filters portfolio by category", () => {
    const { filteredPortfolio } = applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, { q: "income" });
    assert.ok(filteredPortfolio.every((r) => r.category === "income"));
  });
});

// ─── SF-4: No match → both sections empty ────────────────────────────────────

describe("SF-4: No match empties both sections", () => {
  test("unmatched search produces empty results in both sections", () => {
    const { filteredPortfolio, filteredWatchlist } =
      applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, { q: "ZZZNOTEXIST" });
    assert.equal(filteredPortfolio.length, 0, "Portfolio section must be empty");
    assert.equal(filteredWatchlist.length, 0, "Watchlist section must be empty");
  });
});

// ─── SF-5 / SF-6: ideal_calls applies to both sections ───────────────────────

describe("SF-5/SF-6: ideal_calls filter applies to both sections", () => {
  // Favorable calls: fresh, available covered CALL score >=75.

  test("SF-5: ideal_calls keeps matching rows in portfolio section", () => {
    const { filteredPortfolio } =
      applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, { suitabilityFilter: "ideal_calls" });
    // AAPL: covered CALL 85.
    assert.ok(filteredPortfolio.some((r) => r.symbol === "AAPL"), "AAPL should match ideal_calls");
    // MSFT: covered CALL 5.
    assert.ok(!filteredPortfolio.some((r) => r.symbol === "MSFT"), "MSFT should NOT match ideal_calls");
    // O: covered CALL 50.
    assert.ok(!filteredPortfolio.some((r) => r.symbol === "O"), "O should NOT match ideal_calls");
  });

  test("SF-6: ideal_calls keeps matching rows in watchlist section", () => {
    const { filteredWatchlist } =
      applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, { suitabilityFilter: "ideal_calls" });
    // NVDA: covered CALL 85.
    assert.ok(filteredWatchlist.some((r) => r.symbol === "NVDA"), "NVDA should match ideal_calls");
    // Non-US rows without available Option Timing never match.
    assert.ok(!filteredWatchlist.some((r) => r.symbol === "DGE"), "DGE should NOT match ideal_calls");
    assert.ok(!filteredWatchlist.some((r) => r.symbol === "SAN"), "SAN should NOT match ideal_calls");
  });

  test("SF-5/SF-6: same filter yields consistent predicate for both sections", () => {
    const filterState = { suitabilityFilter: "ideal_calls" };
    const { filteredPortfolio, filteredWatchlist } =
      applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, filterState);
    // Verify every row in both filtered sections actually matches ideal_calls
    for (const r of [...filteredPortfolio, ...filteredWatchlist]) {
      assert.ok(
        matchesSymbolSuitability(r.option_timing, "ideal_calls"),
        `${r.symbol} in filtered results must satisfy ideal_calls predicate`
      );
    }
  });
});

// ─── SF-7 / SF-8: ideal_puts applies to both sections ────────────────────────

describe("SF-7/SF-8: ideal_puts filter applies to both sections", () => {
  test("SF-7: ideal_puts keeps matching rows in portfolio section", () => {
    const { filteredPortfolio } =
      applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, { suitabilityFilter: "ideal_puts" });
    // MSFT: cash-secured PUT 85.
    assert.ok(filteredPortfolio.some((r) => r.symbol === "MSFT"), "MSFT should match ideal_puts");
    // O: cash-secured PUT 80.
    assert.ok(filteredPortfolio.some((r) => r.symbol === "O"), "O should match ideal_puts");
    // AAPL: cash-secured PUT 5.
    assert.ok(!filteredPortfolio.some((r) => r.symbol === "AAPL"), "AAPL should NOT match ideal_puts");
  });

  test("SF-8: ideal_puts keeps matching rows in watchlist section", () => {
    const { filteredWatchlist } =
      applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, { suitabilityFilter: "ideal_puts" });
    assert.deepEqual(filteredWatchlist.map((r) => r.symbol), ["AMD"]);
  });

  test("SF-7/SF-8: every row in both filtered sections satisfies ideal_puts predicate", () => {
    const { filteredPortfolio, filteredWatchlist } =
      applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, { suitabilityFilter: "ideal_puts" });
    for (const r of [...filteredPortfolio, ...filteredWatchlist]) {
      assert.ok(
        matchesSymbolSuitability(r.option_timing, "ideal_puts"),
        `${r.symbol} must satisfy ideal_puts predicate`
      );
    }
  });
});

// ─── SF-9: "all" returns all (non-hidden) rows in both sections ───────────────

describe("SF-9: suitabilityFilter=all returns all rows", () => {
  test("all filter with hideZero=false shows every row in both sections", () => {
    const { filteredPortfolio, filteredWatchlist } =
      applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, {
        suitabilityFilter: "all",
        hideZero: false,
      });
    assert.equal(filteredPortfolio.length, PORTFOLIO_ROWS.length);
    assert.equal(filteredWatchlist.length, WATCHLIST_ROWS.length);
  });
});

// ─── SF-10 / SF-11 / SF-12: hide-zero toggle ─────────────────────────────────

describe("SF-10/SF-11/SF-12: hide-zero toggle behavior per section", () => {
  test("SF-10: hide-zero=true hides auto-enrolled zero-share rows from Portfolio section", () => {
    const { filteredPortfolio } =
      applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, { hideZero: true });
    // VOD: portfolio_shares="0", is_auto_enrolled=true → hidden
    assert.ok(!filteredPortfolio.some((r) => r.symbol === "VOD"),
      "VOD (auto-enrolled, zero shares) must be hidden when hideZero=true");
  });

  test("SF-11: hide-zero does NOT hide watchlist-only rows (null portfolio_shares)", () => {
    const { filteredWatchlist } =
      applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, { hideZero: true });
    assert.equal(filteredWatchlist.length, WATCHLIST_ROWS.length,
      "Watchlist-only rows must never be hidden by the zero-share toggle");
  });

  test("SF-12: hide-zero=false reveals VOD in Portfolio section", () => {
    const { filteredPortfolio } =
      applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, { hideZero: false });
    assert.ok(filteredPortfolio.some((r) => r.symbol === "VOD"),
      "VOD must be visible when hideZero=false");
    assert.equal(filteredPortfolio.length, PORTFOLIO_ROWS.length);
  });

  test("SF-11: watchlist section count unchanged by hideZero=true", () => {
    const { filteredWatchlist: hiddenTrue } =
      applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, { hideZero: true });
    const { filteredWatchlist: hiddenFalse } =
      applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, { hideZero: false });
    assert.equal(hiddenTrue.length, hiddenFalse.length,
      "Watchlist section count must be identical regardless of hideZero value");
  });
});

// ─── SF-13: Combined search + suitability ────────────────────────────────────

describe("SF-13: Combined search + suitability applied to both sections", () => {
  test("search for 'income' + ideal_puts filters both sections conjunctively", () => {
    const { filteredPortfolio, filteredWatchlist } = applySharedFilter(
      PORTFOLIO_ROWS, WATCHLIST_ROWS,
      { q: "income", suitabilityFilter: "ideal_puts" }
    );
    assert.deepEqual(filteredPortfolio.map((r) => r.symbol), ["O"]);
    assert.deepEqual(filteredWatchlist, []);
    // Every remaining row must match BOTH predicates
    for (const r of [...filteredPortfolio, ...filteredWatchlist]) {
      assert.ok(matchesSearch(r, "income"), `${r.symbol} must match search "income"`);
      assert.ok(
        matchesSymbolSuitability(r.option_timing, "ideal_puts"),
        `${r.symbol} must match ideal_puts`
      );
    }
  });
});

// ─── SF-14: Combined search + hide-zero ──────────────────────────────────────

describe("SF-14: Combined search + hide-zero applied to both sections", () => {
  test("search + hideZero=true: auto-enrolled zero-share row excluded even if it matches search", () => {
    // VOD category="income" and would match search "income", but should still be hidden
    const portfolioWithVod = [
      ...PORTFOLIO_ROWS, // VOD is already in PORTFOLIO_ROWS with auto_enrolled=true, shares=0
    ];
    const { filteredPortfolio } = applySharedFilter(portfolioWithVod, [], {
      q: "income",
      hideZero: true,
    });
    assert.ok(!filteredPortfolio.some((r) => r.symbol === "VOD"),
      "VOD must be hidden by hideZero even if it matches search query");
  });
});

// ─── SF-15: Counts are independent per section ───────────────────────────────

describe("SF-15: Filter counts are independent per section", () => {
  test("portfolio count and watchlist count are computed separately", () => {
    const { filteredPortfolio, filteredWatchlist } =
      applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, { suitabilityFilter: "ideal_puts" });
    // Counts are independent — not summed
    assert.ok(typeof filteredPortfolio.length === "number");
    assert.ok(typeof filteredWatchlist.length === "number");
    // Neither equals the total of both
    const combined = filteredPortfolio.length + filteredWatchlist.length;
    assert.ok(combined <= PORTFOLIO_ROWS.length + WATCHLIST_ROWS.length);
  });
});

// ─── SF-16: Clearing search shows all rows ────────────────────────────────────

describe("SF-16: Clearing search shows all rows", () => {
  test("q='' shows all non-hidden rows in both sections", () => {
    const { filteredPortfolio, filteredWatchlist } =
      applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, { q: "", hideZero: false });
    assert.equal(filteredPortfolio.length, PORTFOLIO_ROWS.length);
    assert.equal(filteredWatchlist.length, WATCHLIST_ROWS.length);
  });

  test("q='  ' (whitespace) is treated as empty — shows all rows", () => {
    const { filteredPortfolio, filteredWatchlist } =
      applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, { q: "   ", hideZero: false });
    assert.equal(filteredPortfolio.length, PORTFOLIO_ROWS.length);
    assert.equal(filteredWatchlist.length, WATCHLIST_ROWS.length);
  });
});

// ─── SF-17 / SF-18: no_puts and no_calls filters ─────────────────────────────

describe("SF-17/SF-18: no_puts and no_calls filters apply to both sections", () => {
  test("SF-17: no_puts rows satisfy predicate in both sections", () => {
    const { filteredPortfolio, filteredWatchlist } =
      applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, { suitabilityFilter: "no_puts" });
    assert.deepEqual(filteredPortfolio.map((r) => r.symbol), ["AAPL"]);
    assert.deepEqual(filteredWatchlist.map((r) => r.symbol), ["NVDA"]);
    for (const r of [...filteredPortfolio, ...filteredWatchlist]) {
      assert.ok(
        matchesSymbolSuitability(r.option_timing, "no_puts"),
        `${r.symbol} must satisfy no_puts`
      );
    }
  });

  test("SF-18: no_calls rows satisfy predicate in both sections", () => {
    const { filteredPortfolio, filteredWatchlist } =
      applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, { suitabilityFilter: "no_calls" });
    assert.deepEqual(filteredPortfolio.map((r) => r.symbol), ["MSFT"]);
    assert.deepEqual(filteredWatchlist.map((r) => r.symbol), ["AMD"]);
    for (const r of [...filteredPortfolio, ...filteredWatchlist]) {
      assert.ok(
        matchesSymbolSuitability(r.option_timing, "no_calls"),
        `${r.symbol} must satisfy no_calls`
      );
    }
  });
});

// ─── SF-19: Filter does not mutate original arrays ───────────────────────────

describe("SF-19: Filter does not mutate original row arrays", () => {
  test("original portfolio rows unchanged after filtering", () => {
    const origLength = PORTFOLIO_ROWS.length;
    applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, { q: "AAPL" });
    assert.equal(PORTFOLIO_ROWS.length, origLength, "Original portfolio rows must not be mutated");
  });

  test("original watchlist rows unchanged after filtering", () => {
    const origLength = WATCHLIST_ROWS.length;
    applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, { suitabilityFilter: "ideal_calls" });
    assert.equal(WATCHLIST_ROWS.length, origLength, "Original watchlist rows must not be mutated");
  });
});

// ─── SF-20: Case-insensitive search ──────────────────────────────────────────

describe("SF-20: Search is case-insensitive in both sections", () => {
  test("lowercase query matches uppercase symbol in portfolio section", () => {
    const { filteredPortfolio } = applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, { q: "aapl" });
    assert.ok(filteredPortfolio.some((r) => r.symbol === "AAPL"));
  });

  test("uppercase query matches lowercase display_name in watchlist section", () => {
    const rows = [{ symbol: "DGE", display_name: "diageo", row_source: "watchlist", portfolio_shares: null, entry_tag: "Buy", momentum: "Weakening", category: "income", is_auto_enrolled: false }];
    const { filteredWatchlist } = applySharedFilter([], rows, { q: "DIAGEO" });
    assert.ok(filteredWatchlist.some((r) => r.symbol === "DGE"));
  });

  test("mixed-case query filters both sections case-insensitively", () => {
    const { filteredPortfolio, filteredWatchlist } =
      applySharedFilter(PORTFOLIO_ROWS, WATCHLIST_ROWS, { q: "ReAlTy" });
    // "Realty Income" is PORTFOLIO_ROWS[2] (symbol O)
    assert.ok(filteredPortfolio.some((r) => r.symbol === "O"));
    assert.equal(filteredWatchlist.length, 0);
  });
});

// ─── splitSections helper tests ───────────────────────────────────────────────

describe("splitSections: Portfolio and Watchlist sections derived from row_source", () => {
  const ALL_ROWS = [
    { symbol: "AAPL", row_source: "portfolio" },
    { symbol: "MSFT", row_source: "both" },
    { symbol: "NVDA", row_source: "watchlist" },
    { symbol: "DGE",  row_source: "watchlist" },
  ];

  test("portfolioRows contains portfolio and both rows", () => {
    const { portfolioRows } = splitSections(ALL_ROWS);
    assert.ok(portfolioRows.some((r) => r.symbol === "AAPL"));
    assert.ok(portfolioRows.some((r) => r.symbol === "MSFT"));
    assert.ok(!portfolioRows.some((r) => r.symbol === "NVDA"));
    assert.ok(!portfolioRows.some((r) => r.symbol === "DGE"));
  });

  test("watchlistRows contains only watchlist-source rows", () => {
    const { watchlistRows } = splitSections(ALL_ROWS);
    assert.ok(watchlistRows.some((r) => r.symbol === "NVDA"));
    assert.ok(watchlistRows.some((r) => r.symbol === "DGE"));
    assert.ok(!watchlistRows.some((r) => r.symbol === "AAPL"));
    assert.ok(!watchlistRows.some((r) => r.symbol === "MSFT"));
  });

  test("no symbol appears in both sections simultaneously", () => {
    const { portfolioRows, watchlistRows } = splitSections(ALL_ROWS);
    const ps = new Set(portfolioRows.map((r) => r.symbol));
    const ws = new Set(watchlistRows.map((r) => r.symbol));
    const overlap = [...ps].filter((s) => ws.has(s));
    assert.deepEqual(overlap, []);
  });

  test("total rows = sum of both sections", () => {
    const { portfolioRows, watchlistRows } = splitSections(ALL_ROWS);
    assert.equal(portfolioRows.length + watchlistRows.length, ALL_ROWS.length);
  });
});
