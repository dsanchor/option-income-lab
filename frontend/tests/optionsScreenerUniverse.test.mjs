/**
 * Tests for the Options Screener symbol universe predicate.
 *
 * Ref: danny-symbol-config-investments-screener-dropdown-final-gate.md
 *
 * Mirrors `OptionsScreenerView.tsx::isScreenerEligible` — now a direct,
 * fail-closed read of the backend's authoritative `screener_eligible` boolean:
 *
 *   function isScreenerEligible(r) { return r.screener_eligible === true; }
 *
 * The backend emits screener_eligible per row in _compute_symbols_overview via
 * compute_options_screener_universe (XNYS/XNAS AND shares>0 OR is_watchlist_member).
 * No MIC table, share count, or watchlist signal is reimplemented on the frontend.
 *
 * Coverage:
 *   FC-1   screener_eligible === true → eligible.
 *   FC-2   screener_eligible === false → not eligible.
 *   FC-3   screener_eligible absent/undefined → not eligible (fail-closed).
 *   FC-4   screener_eligible === null → not eligible.
 *   FC-5   screener_eligible = "true" (string) → not eligible (strict ===).
 *   FC-6   screener_eligible = 1 (truthy int) → not eligible (strict ===).
 *   FC-7   screener_eligible = "1" (truthy string) → not eligible.
 *   NH-1   Row with us_options_eligible + portfolio_shares but no screener_eligible → false
 *          (heuristic passthrough is gone).
 *   NH-2   Row with row_source="watchlist" + us_options_eligible=true but no screener_eligible → false.
 *   NH-3   Row with is_auto_enrolled=false + us_options_eligible=true but no screener_eligible → false.
 *   NH-4   CRITICAL: Row with auto_enrolled=true + covered_call=true (old heuristic false,
 *          new screener_eligible=true) → eligible. This is the concrete divergence case
 *          from Danny's gate review.
 *   DS-1   XNYS + shares>0 → screener_eligible:true → eligible.
 *   DS-2   XNAS + covered_call toggle + zero shares → screener_eligible:true → eligible.
 *   DS-3   XAMS + shares → screener_eligible:false → not eligible (US gate).
 *   DS-4   XNYS + auto_enrolled=true + zero shares + no toggles → screener_eligible:false → not eligible.
 *   DS-5   XNYS + negative shares + is_watchlist_member (cc=True) → screener_eligible:true → eligible.
 *   DS-6   XNYS + negative shares + no watchlist → screener_eligible:false → not eligible.
 *   SC-1   Source contract: isScreenerEligible in OptionsScreenerView.tsx is
 *          exactly `return r.screener_eligible === true;`.
 *   ST-1   Stale selection cleanup: removes symbols no longer in eligible set.
 *   ST-2   Stale selection: valid selection is unchanged.
 *   ST-3   Stale selection: empty selection stays empty.
 */

import { readFileSync } from "fs";
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, "..");

// ---------------------------------------------------------------------------
// Inline mirror — MUST match OptionsScreenerView.tsx::isScreenerEligible exactly.
// Any divergence from `return r.screener_eligible === true;` is a test defect.
// ---------------------------------------------------------------------------

function isScreenerEligible(r) {
  return r.screener_eligible === true;
}

// ---------------------------------------------------------------------------
// FC-1..7: Fail-closed strict-equality checks
// ---------------------------------------------------------------------------

describe("FC: Fail-closed strict-equality on screener_eligible", () => {
  it("FC-1: screener_eligible === true → eligible", () => {
    assert.equal(isScreenerEligible({ symbol: "ABBV", screener_eligible: true }), true);
  });

  it("FC-2: screener_eligible === false → not eligible", () => {
    assert.equal(isScreenerEligible({ symbol: "AD", screener_eligible: false }), false);
  });

  it("FC-3: screener_eligible absent (undefined) → not eligible (fail-closed)", () => {
    assert.equal(isScreenerEligible({ symbol: "ABBV" }), false,
      "Missing screener_eligible must be treated as ineligible — no fail-open fallback");
  });

  it("FC-4: screener_eligible === null → not eligible", () => {
    assert.equal(isScreenerEligible({ symbol: "ABBV", screener_eligible: null }), false);
  });

  it('FC-5: screener_eligible = "true" (string, truthy) → not eligible (strict ===)', () => {
    assert.equal(isScreenerEligible({ symbol: "ABBV", screener_eligible: "true" }), false,
      'String "true" must not pass strict === true gate');
  });

  it("FC-6: screener_eligible = 1 (truthy number) → not eligible (strict ===)", () => {
    assert.equal(isScreenerEligible({ symbol: "ABBV", screener_eligible: 1 }), false,
      "Number 1 must not pass strict === true gate");
  });

  it('FC-7: screener_eligible = "1" (truthy string) → not eligible', () => {
    assert.equal(isScreenerEligible({ symbol: "ABBV", screener_eligible: "1" }), false);
  });
});

// ---------------------------------------------------------------------------
// NH-1..4: No heuristic fallback — old row_source/is_auto_enrolled/portfolio_shares ignored
// ---------------------------------------------------------------------------

describe("NH: No-heuristic — old proxy fields are inert without screener_eligible", () => {
  it("NH-1: us_options_eligible=true + portfolio_shares='100' but no screener_eligible → false", () => {
    assert.equal(
      isScreenerEligible({
        symbol: "ABBV",
        us_options_eligible: true,
        portfolio_shares: "100",
        // screener_eligible intentionally absent
      }),
      false,
      "NH-1: us_options_eligible+portfolio_shares without screener_eligible must fail-closed"
    );
  });

  it("NH-2: row_source='watchlist' + us_options_eligible=true but no screener_eligible → false", () => {
    assert.equal(
      isScreenerEligible({
        symbol: "JNJ",
        us_options_eligible: true,
        portfolio_shares: "0",
        row_source: "watchlist",
        // screener_eligible intentionally absent
      }),
      false,
      "NH-2: row_source='watchlist' without screener_eligible must fail-closed"
    );
  });

  it("NH-3: is_auto_enrolled=false + us_options_eligible=true but no screener_eligible → false", () => {
    assert.equal(
      isScreenerEligible({
        symbol: "KO",
        us_options_eligible: true,
        is_auto_enrolled: false,
        // screener_eligible intentionally absent
      }),
      false,
      "NH-3: is_auto_enrolled=false without screener_eligible must fail-closed"
    );
  });

  it("NH-4 CRITICAL: auto_enrolled=true + covered_call toggled + screener_eligible=true → eligible", () => {
    // This is the concrete case from Danny's gate review:
    // A portfolio-held symbol with zero current shares but an active covered_call toggle
    // → is_watchlist_member(doc)=True in backend → screener_eligible=True
    // → old heuristic (is_auto_enrolled=true) would return false — WRONG
    // → new predicate (screener_eligible===true) returns true — CORRECT
    assert.equal(
      isScreenerEligible({
        symbol: "ABBV",
        us_options_eligible: true,
        is_auto_enrolled: true,        // old heuristic would have excluded here
        row_source: "portfolio",       // never "watchlist" for this case → old heuristic false
        portfolio_shares: "0",
        watchlist: { covered_call: true, cash_secured_put: false, buy_tracker: false },
        screener_eligible: true,       // backend correctly emits true via is_watchlist_member
      }),
      true,
      "NH-4: auto_enrolled=true + cc=true with screener_eligible=true must be eligible. " +
        "Old row_source/is_auto_enrolled heuristic would have incorrectly excluded this symbol."
    );
  });

  it("NH-4b: same row with screener_eligible absent → false (fail-closed even if toggle is set)", () => {
    // Without the authoritative backend boolean, frontend cannot infer eligibility
    assert.equal(
      isScreenerEligible({
        symbol: "ABBV",
        us_options_eligible: true,
        is_auto_enrolled: true,
        row_source: "portfolio",
        portfolio_shares: "0",
        watchlist: { covered_call: true, cash_secured_put: false, buy_tracker: false },
        // screener_eligible intentionally absent — backend hasn't emitted it yet
      }),
      false,
      "NH-4b: without screener_eligible, frontend must fail-closed even when toggle signals are present"
    );
  });
});

// ---------------------------------------------------------------------------
// DS-1..6: Domain scenarios keyed on backend-emitted screener_eligible
// ---------------------------------------------------------------------------

describe("DS: Domain scenarios — screener_eligible as sole eligibility signal", () => {
  it("DS-1: XNYS + shares>0 → screener_eligible:true → eligible", () => {
    assert.equal(
      isScreenerEligible({ symbol: "ABBV", screener_eligible: true }),
      true
    );
  });

  it("DS-2: XNAS + covered_call toggle (zero shares) → screener_eligible:true → eligible", () => {
    assert.equal(
      isScreenerEligible({ symbol: "MSFT", screener_eligible: true }),
      true
    );
  });

  it("DS-3: XAMS + shares → screener_eligible:false → not eligible", () => {
    assert.equal(
      isScreenerEligible({ symbol: "AD", screener_eligible: false }),
      false
    );
  });

  it("DS-4: XNYS + auto-enrolled + zero shares + no toggles → screener_eligible:false → not eligible", () => {
    assert.equal(
      isScreenerEligible({ symbol: "HIST", screener_eligible: false }),
      false
    );
  });

  it("DS-5: XNYS + negative shares + is_watchlist_member (cc=True) → screener_eligible:true → eligible", () => {
    assert.equal(
      isScreenerEligible({ symbol: "ABBV", screener_eligible: true }),
      true
    );
  });

  it("DS-6: XNYS + negative shares + auto-enrolled only → screener_eligible:false → not eligible", () => {
    assert.equal(
      isScreenerEligible({ symbol: "HIST2", screener_eligible: false }),
      false
    );
  });
});

// ---------------------------------------------------------------------------
// SC-1: Source-contract — actual OptionsScreenerView.tsx function body
// ---------------------------------------------------------------------------

describe("SC-1: Source contract — isScreenerEligible body is direct screener_eligible read", () => {
  const viewSrc = readFileSync(
    join(root, "src/components/OptionsScreenerView.tsx"),
    "utf8",
  );

  it("SC-1: isScreenerEligible body is exactly `return r.screener_eligible === true;`", () => {
    const fnStart = viewSrc.indexOf("function isScreenerEligible");
    assert.ok(fnStart !== -1, "isScreenerEligible not found in OptionsScreenerView.tsx");
    // Extract the function body (balanced brace)
    let depth = 0;
    let bodyStart = viewSrc.indexOf("{", fnStart);
    let bodyEnd = bodyStart;
    for (let i = bodyStart; i < viewSrc.length; i++) {
      if (viewSrc[i] === "{") depth++;
      else if (viewSrc[i] === "}") {
        depth--;
        if (depth === 0) { bodyEnd = i; break; }
      }
    }
    const fnBody = viewSrc.slice(bodyStart + 1, bodyEnd).trim();
    assert.equal(
      fnBody,
      "return r.screener_eligible === true;",
      `SC-1 FAIL: isScreenerEligible body must be exactly ` +
        `\`return r.screener_eligible === true;\` — no reimplemented heuristics. ` +
        `Got: ${fnBody}`
    );
  });

  it("SC-1b: isScreenerEligible does NOT reference us_options_eligible", () => {
    const fnStart = viewSrc.indexOf("function isScreenerEligible");
    let depth = 0, bodyStart = viewSrc.indexOf("{", fnStart), bodyEnd = bodyStart;
    for (let i = bodyStart; i < viewSrc.length; i++) {
      if (viewSrc[i] === "{") depth++;
      else if (viewSrc[i] === "}") { depth--; if (depth === 0) { bodyEnd = i; break; } }
    }
    const fnBody = viewSrc.slice(bodyStart, bodyEnd + 1);
    assert.ok(!fnBody.includes("us_options_eligible"),
      "SC-1b FAIL: isScreenerEligible must not reference us_options_eligible (heuristic)");
  });

  it("SC-1c: isScreenerEligible does NOT reference portfolio_shares", () => {
    const fnStart = viewSrc.indexOf("function isScreenerEligible");
    let depth = 0, bodyStart = viewSrc.indexOf("{", fnStart), bodyEnd = bodyStart;
    for (let i = bodyStart; i < viewSrc.length; i++) {
      if (viewSrc[i] === "{") depth++;
      else if (viewSrc[i] === "}") { depth--; if (depth === 0) { bodyEnd = i; break; } }
    }
    const fnBody = viewSrc.slice(bodyStart, bodyEnd + 1);
    assert.ok(!fnBody.includes("portfolio_shares"),
      "SC-1c FAIL: isScreenerEligible must not reference portfolio_shares (heuristic)");
  });

  it("SC-1d: isScreenerEligible does NOT reference is_auto_enrolled", () => {
    const fnStart = viewSrc.indexOf("function isScreenerEligible");
    let depth = 0, bodyStart = viewSrc.indexOf("{", fnStart), bodyEnd = bodyStart;
    for (let i = bodyStart; i < viewSrc.length; i++) {
      if (viewSrc[i] === "{") depth++;
      else if (viewSrc[i] === "}") { depth--; if (depth === 0) { bodyEnd = i; break; } }
    }
    const fnBody = viewSrc.slice(bodyStart, bodyEnd + 1);
    assert.ok(!fnBody.includes("is_auto_enrolled"),
      "SC-1d FAIL: isScreenerEligible must not reference is_auto_enrolled (heuristic)");
  });

  it("SC-1e: isScreenerEligible does NOT reference row_source", () => {
    const fnStart = viewSrc.indexOf("function isScreenerEligible");
    let depth = 0, bodyStart = viewSrc.indexOf("{", fnStart), bodyEnd = bodyStart;
    for (let i = bodyStart; i < viewSrc.length; i++) {
      if (viewSrc[i] === "{") depth++;
      else if (viewSrc[i] === "}") { depth--; if (depth === 0) { bodyEnd = i; break; } }
    }
    const fnBody = viewSrc.slice(bodyStart, bodyEnd + 1);
    assert.ok(!fnBody.includes("row_source"),
      "SC-1e FAIL: isScreenerEligible must not reference row_source (heuristic)");
  });
});

// ---------------------------------------------------------------------------
// ST-1..3: Stale selection cleanup
// ---------------------------------------------------------------------------

describe("ST: Stale selection cleanup — works with screener_eligible predicate", () => {
  const universe = [
    { symbol: "ABBV", screener_eligible: true },
    { symbol: "MSFT", screener_eligible: true },
    { symbol: "AD",   screener_eligible: false },   // non-US excluded
    { symbol: "HIST", screener_eligible: false },   // zero-share auto-enrolled excluded
    { symbol: "JNJ",  screener_eligible: true },    // XNYS watchlist member
  ];

  it("ST-1: stale selection cleanup removes symbols no longer in eligible set", () => {
    const eligible = universe.filter(isScreenerEligible).map((r) => r.symbol);
    const validSet = new Set(eligible);
    const previousSelection = ["ABBV", "AD", "HIST"]; // AD and HIST are now ineligible
    const cleaned = previousSelection.filter((s) => validSet.has(s));
    assert.deepEqual(cleaned, ["ABBV"],
      "ST-1: Stale-selection guard must remove AD (non-US) and HIST (zero-share) from selection");
  });

  it("ST-2: valid selection is unchanged after stale-selection guard", () => {
    const eligible = universe.filter(isScreenerEligible).map((r) => r.symbol);
    const validSet = new Set(eligible);
    const selection = ["ABBV", "MSFT", "JNJ"];
    const cleaned = selection.filter((s) => validSet.has(s));
    assert.deepEqual(cleaned, selection);
  });

  it("ST-3: empty selection produces empty cleaned result", () => {
    const eligible = universe.filter(isScreenerEligible).map((r) => r.symbol);
    const validSet = new Set(eligible);
    const cleaned = [].filter((s) => validSet.has(s));
    assert.deepEqual(cleaned, []);
  });

  it("ST: eligible set from new predicate equals only screener_eligible:true rows", () => {
    const eligible = universe.filter(isScreenerEligible).map((r) => r.symbol).sort();
    assert.deepEqual(eligible, ["ABBV", "JNJ", "MSFT"].sort());
  });
});

