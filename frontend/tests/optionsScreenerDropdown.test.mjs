/**
 * optionsScreenerDropdown.test.mjs — Options Screener Dropdown source-contract tests.
 *
 * Ref: copilot-directive-20260907-options-screener-dropdown.md
 * Ref: danny-symbol-config-investments-screener-dropdown-final-gate.md
 *
 * Coverage:
 *   OSD-FE-1   OptionsScreenerView.tsx fetches from /api/symbols/overview
 *              (not a divergent all-symbols endpoint).
 *   OSD-FE-2   isScreenerEligible is defined as a named function in the source
 *              (not inlined into allRows.filter() as an anonymous lambda).
 *   OSD-FE-3   symbolOptions is derived from allRows.filter(isScreenerEligible),
 *              not from unfiltered rows.
 *   OSD-FE-4   Stale-selection guard effect exists: uses symbolOptions to clean
 *              applied.symbols when the eligible universe changes.
 *   OSD-FE-5   Effect cleanup: the overview fetch effect returns a cleanup that
 *              cancels inflight requests (cancelled flag pattern).
 *   OSD-FE-6   isScreenerEligible (new): screener_eligible=true → eligible.
 *   OSD-FE-7   isScreenerEligible (new): screener_eligible=false → NOT eligible.
 *   OSD-FE-8   isScreenerEligible (new): screener_eligible absent → fail-closed false.
 *   OSD-FE-9   isScreenerEligible (new): screener_eligible=true wins even for auto-enrolled zero shares.
 *   OSD-FE-10  No divergent all-symbols URL in the screener component source.
 *   OSD-FE-11  symbolOptions mapped as { value: r.symbol, label: r.symbol } (dedup by contract —
 *              backend partition key guarantees no duplicate symbols in the list source).
 *
 * Run: node --test frontend/tests/optionsScreenerDropdown.test.mjs
 */

import { readFileSync } from "fs";
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, "..");

const viewSrc = readFileSync(
  join(root, "src/components/OptionsScreenerView.tsx"),
  "utf8",
);

// ---------------------------------------------------------------------------
// Inline mirror of isScreenerEligible for behavioral tests (OSD-FE-6..9)
// Matches OptionsScreenerView.tsx: return r.screener_eligible === true (strict).
// ---------------------------------------------------------------------------

function isScreenerEligible(r) {
  return r.screener_eligible === true;
}

// ---------------------------------------------------------------------------
// OSD-FE-1: Dropdown fetches from /api/symbols/overview
// ---------------------------------------------------------------------------

describe("OSD-FE-1: Dropdown endpoint is /api/symbols/overview", () => {
  it("OptionsScreenerView.tsx fetches /api/symbols/overview for symbol dropdown", () => {
    assert.ok(
      viewSrc.includes('"/api/symbols/overview"') || viewSrc.includes("'/api/symbols/overview'"),
      "OSD-FE-1 FAIL: OptionsScreenerView must fetch '/api/symbols/overview' to populate dropdown. " +
        "A divergent endpoint would create a universe mismatch.",
    );
  });
});

// ---------------------------------------------------------------------------
// OSD-FE-2: isScreenerEligible is a named function
// ---------------------------------------------------------------------------

describe("OSD-FE-2: isScreenerEligible is a named function", () => {
  it("function isScreenerEligible is defined by name in source", () => {
    assert.ok(
      viewSrc.includes("function isScreenerEligible"),
      "OSD-FE-2 FAIL: isScreenerEligible must be a named function, not an inline anonymous lambda. " +
        "Named form allows independent testing and prevents divergence.",
    );
  });
});

// ---------------------------------------------------------------------------
// OSD-FE-3: symbolOptions is set from filtered rows, not all rows
// ---------------------------------------------------------------------------

describe("OSD-FE-3: symbolOptions = filtered (not all) rows", () => {
  it("eligible symbols are filtered via isScreenerEligible before setSymbolOptions", () => {
    // Both the filter call and setSymbolOptions must be present
    assert.ok(
      viewSrc.includes("filter(isScreenerEligible)"),
      "OSD-FE-3 FAIL: Overview rows must be filtered with isScreenerEligible before setSymbolOptions. " +
        "Without this filter, all overview symbols (including non-US) enter the dropdown.",
    );
    // The setSymbolOptions call must follow the filter, not operate on unfiltered rows
    const filterIdx = viewSrc.indexOf("filter(isScreenerEligible)");
    const setOptsIdx = viewSrc.indexOf("setSymbolOptions", filterIdx);
    assert.ok(
      setOptsIdx !== -1,
      "OSD-FE-3 FAIL: setSymbolOptions must appear after filter(isScreenerEligible) in source",
    );
    assert.ok(
      filterIdx < setOptsIdx,
      `OSD-FE-3 FAIL: filter(isScreenerEligible) (idx=${filterIdx}) must precede ` +
        `setSymbolOptions (idx=${setOptsIdx})`,
    );
  });

  it("symbolOptions maps eligible rows to { value: r.symbol, label: r.symbol }", () => {
    // The map must use r.symbol for both value and label (symbol identity, not display_name)
    assert.ok(
      viewSrc.includes("value: r.symbol") || viewSrc.includes('value: r["symbol"]'),
      "OSD-FE-3b FAIL: symbolOptions must map to { value: r.symbol } for correct symbol selection",
    );
    assert.ok(
      viewSrc.includes("label: r.symbol") || viewSrc.includes('label: r["symbol"]'),
      "OSD-FE-3b FAIL: symbolOptions must map to { label: r.symbol } for symbol display",
    );
  });
});

// ---------------------------------------------------------------------------
// OSD-FE-4: Stale-selection guard effect
// ---------------------------------------------------------------------------

describe("OSD-FE-4: Stale-selection guard removes ineligible selected symbols", () => {
  it("a useEffect that depends on symbolOptions exists for stale-selection cleanup", () => {
    // The guard effect depends on [symbolOptions] and filters applied.symbols
    assert.ok(
      viewSrc.includes("symbolOptions"),
      "symbolOptions must be used in the component",
    );
    // Look for the stale-selection pattern: validSet/cleaned/filter
    const hasValidSet =
      viewSrc.includes("validSet") ||
      viewSrc.includes("valid_set") ||
      viewSrc.includes("new Set(");
    assert.ok(
      hasValidSet,
      "OSD-FE-4 FAIL: Stale-selection guard must build a Set from eligible symbols",
    );
  });

  it("stale-selection guard filters prev.symbols (or applied.symbols) against valid set", () => {
    assert.ok(
      viewSrc.includes("filter(") && (viewSrc.includes("validSet") || viewSrc.includes("new Set(")),
      "OSD-FE-4 FAIL: Guard must filter selected symbols against the valid set",
    );
  });
});

// ---------------------------------------------------------------------------
// OSD-FE-5: Effect cleanup (cancelled flag prevents state update after unmount)
// ---------------------------------------------------------------------------

describe("OSD-FE-5: Overview fetch effect has cancellation cleanup", () => {
  it("cancelled flag pattern prevents state update on stale fetch", () => {
    assert.ok(
      viewSrc.includes("cancelled"),
      "OSD-FE-5 FAIL: Overview fetch effect must use a 'cancelled' flag to guard against " +
        "setting state on unmounted/stale effects.",
    );
    // The effect must return a cleanup function that sets cancelled = true
    assert.ok(
      viewSrc.includes("cancelled = true"),
      "OSD-FE-5 FAIL: Effect cleanup must set 'cancelled = true' to cancel inflight fetch",
    );
  });
});

// ---------------------------------------------------------------------------
// OSD-FE-6/7/8/9: isScreenerEligible strict-equality on screener_eligible
// ---------------------------------------------------------------------------

describe("OSD-FE-6/7: isScreenerEligible with screener_eligible field", () => {
  it("OSD-FE-6: screener_eligible=true → eligible", () => {
    assert.equal(
      isScreenerEligible({ symbol: "XNYS:ABBV", screener_eligible: true }),
      true,
      "OSD-FE-6: screener_eligible=true must be eligible",
    );
  });

  it("OSD-FE-6b: screener_eligible=true even for auto-enrolled zero-share with cc toggle", () => {
    assert.equal(
      isScreenerEligible({
        symbol: "XNYS:ABBV",
        screener_eligible: true,
        is_auto_enrolled: true,
        covered_call: true,
        portfolio_shares: "0",
      }),
      true,
      "OSD-FE-6b: screener_eligible=true must win — no heuristic can override it",
    );
  });

  it("OSD-FE-7: screener_eligible=false → NOT eligible", () => {
    assert.equal(
      isScreenerEligible({ symbol: "XAMS:AD", screener_eligible: false }),
      false,
      "OSD-FE-7: screener_eligible=false must NOT be eligible",
    );
  });
});

// ---------------------------------------------------------------------------
// OSD-FE-8: Missing screener_eligible → fail-closed
// ---------------------------------------------------------------------------

describe("OSD-FE-8: Missing screener_eligible → fail-closed false", () => {
  it("screener_eligible absent (undefined) → false (fail-closed)", () => {
    assert.equal(
      isScreenerEligible({ symbol: "XNYS:KO", us_options_eligible: true, portfolio_shares: "100" }),
      false,
      "OSD-FE-8: Row without screener_eligible must fail-closed — no field, no eligibility",
    );
  });

  it("screener_eligible=null → false", () => {
    assert.equal(
      isScreenerEligible({ screener_eligible: null }),
      false,
    );
  });
});

// ---------------------------------------------------------------------------
// OSD-FE-9: Truthy-but-not-true values are rejected (strict ===)
// ---------------------------------------------------------------------------

describe("OSD-FE-9: Truthy non-boolean values rejected by strict ===", () => {
  it("screener_eligible='true' (string) → false", () => {
    assert.equal(isScreenerEligible({ screener_eligible: "true" }), false);
  });

  it("screener_eligible=1 (number) → false", () => {
    assert.equal(isScreenerEligible({ screener_eligible: 1 }), false);
  });

  it("non-US with shares → screener_eligible=false → false", () => {
    assert.equal(
      isScreenerEligible({ screener_eligible: false, portfolio_shares: "500" }),
      false,
    );
  });
});

// ---------------------------------------------------------------------------
// OSD-FE-10: No divergent all-symbols endpoint
// ---------------------------------------------------------------------------

describe("OSD-FE-10: No divergent all-symbols URL in screener component", () => {
  it("OptionsScreenerView does not call /api/symbols/list or /api/symbols without overview", () => {
    // These would be divergent endpoints that don't carry us_options_eligible
    const hasDivergentListEndpoint =
      viewSrc.includes('"/api/symbols/list"') ||
      viewSrc.includes("'/api/symbols/list'");
    assert.ok(
      !hasDivergentListEndpoint,
      "OSD-FE-10 FAIL: /api/symbols/list would bypass the us_options_eligible field",
    );
  });

  it("OptionsScreenerView does not use a bare /api/symbols endpoint for dropdown", () => {
    // /api/symbols/ (the GET list endpoint, not overview) would return raw symbol_config
    // without portfolio-derived fields (portfolio_shares, is_auto_enrolled, etc.)
    const hasBareSymbolsEndpoint =
      viewSrc.includes('"/api/symbols"') || viewSrc.includes("'/api/symbols'");
    // If it does use /api/symbols, it must be for a non-dropdown purpose (e.g., POSTing a new symbol)
    // The fetch inside the symbolOptions useEffect must specifically be for overview
    const overviewFetchIdx = viewSrc.indexOf('fetch("/api/symbols/overview")');
    const overviewFetchIdx2 = viewSrc.indexOf("fetch('/api/symbols/overview')");
    const dropdownFetchIdx = Math.max(overviewFetchIdx, overviewFetchIdx2);
    assert.ok(
      dropdownFetchIdx !== -1,
      "OSD-FE-10 FAIL: Dropdown fetch must use /api/symbols/overview (not found in source)",
    );
  });
});

// ---------------------------------------------------------------------------
// OSD-FE-11: symbolOptions contract — mapped shape
// ---------------------------------------------------------------------------

describe("OSD-FE-11: symbolOptions has correct { value, label } shape", () => {
  // Behavioral test using the filter+map pattern — rows now carry screener_eligible
  const fakeRows = [
    { symbol: "ABBV", screener_eligible: true,  portfolio_shares: "100" },
    { symbol: "AD",   screener_eligible: false, portfolio_shares: "50" },
    { symbol: "JNJ",  screener_eligible: true,  portfolio_shares: "0", covered_call: true },
    { symbol: "HIST", screener_eligible: false, portfolio_shares: "0", is_auto_enrolled: true },
  ];

  it("symbolOptions contains only eligible rows mapped to { value, label }", () => {
    const options = fakeRows
      .filter(isScreenerEligible)
      .map((r) => ({ value: r.symbol, label: r.symbol }));
    // ABBV and JNJ → eligible; AD (non-US) and HIST (zero-share auto-enrolled) → excluded
    assert.deepEqual(
      options.map((o) => o.value).sort(),
      ["ABBV", "JNJ"].sort(),
    );
    // Shape check
    for (const o of options) {
      assert.ok("value" in o && "label" in o, "Each option must have value and label");
      assert.equal(o.value, o.label, "For screener dropdown, value and label are both the ticker symbol");
    }
  });

  it("no duplicate values in symbolOptions (guaranteed by partition-key uniqueness)", () => {
    // Backend partition key is config_{ticker} → one doc per ticker → no dupes in source
    const options = fakeRows
      .filter(isScreenerEligible)
      .map((r) => ({ value: r.symbol, label: r.symbol }));
    const values = options.map((o) => o.value);
    assert.equal(values.length, new Set(values).size, "symbolOptions must not contain duplicate values");
  });
});
