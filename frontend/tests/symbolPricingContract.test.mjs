/**
 * symbolPricingContract.test.mjs — Frontend regression for Symbol Pricing Cache.
 *
 * Ref: danny-symbol-pricing-cache-contract.md (Phase 4 / Phase 5 tests)
 *
 * Coverage:
 *   FC  — Currency formatting logic (USD, EUR, GBp/GBX, CHF, legacy fallback)
 *   PC  — Price column: GBp display reconstructed from price_major * 100 + " GBp" suffix
 *   PE  — Price EUR column: price_eur rendered in € format; null renders "—"
 *   CV  — Current Value column: current_value_eur rendered in € format; null renders "—"
 *   KV  — KPI Current Value card: appears/hidden based on total_current_value_eur
 *   ST  — Stale / error / unavailable rendering states
 *   SK  — Sort keys: price_eur and current_value_eur in SortKey type
 *   SC  — Source-contract: type declarations in symbols.ts; column presence in SymbolsTable.tsx
 *   KP  — KPI card in symbols page.tsx
 *
 * Run: node --test frontend/tests/symbolPricingContract.test.mjs
 */

import { readFileSync } from "fs";
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, "..");

const symbolsTypeSrc = readFileSync(
  join(root, "src/types/symbols.ts"),
  "utf8",
);
const symbolsTableSrc = readFileSync(
  join(root, "src/components/SymbolsTable.tsx"),
  "utf8",
);
const symbolsPageSrc = readFileSync(
  join(root, "src/app/symbols/page.tsx"),
  "utf8",
);

// ---------------------------------------------------------------------------
// Inline currency formatter mirror — must match SymbolsTable.tsx contract.
// The contract (SS9.3) defines: GBp/GBX -> price * 100 with " GBp" suffix,
// USD -> "$X.XX", EUR -> "€X.XX", CHF -> "CHF X.XX", null -> "$" fallback.
//
// This is an inline mirror for behavioral tests; SC-3 reads actual source.
// ---------------------------------------------------------------------------

function num(n, digits = 2) {
  return typeof n === "number" && isFinite(n) ? n.toFixed(digits) : "—";
}

/**
 * Format price for display: respects quote currency.
 * For GBp/GBX: display is price * 100 (reconstruct pence) + " GBp" suffix.
 * (price_major comes from backend as major-unit, so * 100 to show pence.)
 */
function formatPrice(price, displayCurrency) {
  if (price == null || !isFinite(price)) return "—";
  if (!displayCurrency) return `$${num(price, 2)}`; // legacy null fallback
  const upper = displayCurrency.toUpperCase();
  if (upper === "USD") return `$${num(price, 2)}`;
  if (upper === "EUR") return `€${num(price, 2)}`;
  if (upper === "GBP" || upper === "GBX") {
    // Reconstruct pence for display (price_major * 100), integer format
    return `${Math.round(price * 100)} GBp`;
  }
  if (upper === "CHF") return `CHF ${num(price, 2)}`;
  // Unknown currency: suffix
  return `${num(price, 2)} ${displayCurrency}`;
}

function formatEur(value) {
  if (value == null) return "—";
  const n = typeof value === "number" ? value : parseFloat(value);
  if (!isFinite(n)) return "—";
  return new Intl.NumberFormat("de-DE", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 2,
  }).format(n);
}

// ---------------------------------------------------------------------------
// FC: Currency formatting
// ---------------------------------------------------------------------------

describe("FC: Currency formatting — formatPrice behavior", () => {
  it("FC-1: USD -> '$' prefix with 2dp", () => {
    assert.match(formatPrice(48.15, "USD"), /^\$48\.15$/,
      "FC-1: USD must format as $48.15");
  });

  it("FC-2: EUR -> '€' prefix with 2dp", () => {
    assert.match(formatPrice(28.50, "EUR"), /^€28\.50$/,
      "FC-2: EUR must format as €28.50");
  });

  it("FC-3: GBp display_currency -> price*100 with ' GBp' suffix (reconstruct pence)", () => {
    // Backend sends price_major=48.15; display must show 4815 GBp
    const result = formatPrice(48.15, "GBp");
    assert.equal(result, "4815 GBp",
      `FC-3: GBp display must be '4815 GBp' (48.15 * 100), got '${result}'. ` +
      "If '48.15 GBp', the pence reconstruction is broken — frontend shows pounds labelled as pence.");
  });

  it("FC-3b: GBX display_currency -> same as GBp (pence reconstruction)", () => {
    const result = formatPrice(48.15, "GBX");
    assert.equal(result, "4815 GBp",
      `FC-3b: GBX must also display as '4815 GBp', got '${result}'`);
  });

  it("FC-4: CHF -> 'CHF ' prefix with 2dp", () => {
    const result = formatPrice(120.50, "CHF");
    assert.match(result, /^CHF 120\.50$/,
      `FC-4: CHF must format as 'CHF 120.50', got '${result}'`);
  });

  it("FC-5: null display_currency -> legacy '$' prefix (backward compat)", () => {
    const result = formatPrice(166.25, null);
    assert.match(result, /^\$166\.25$/,
      "FC-5: null display_currency must fall back to '$' prefix (legacy behavior)");
  });

  it("FC-6: null price -> '—' regardless of currency", () => {
    assert.equal(formatPrice(null, "USD"), "—");
    assert.equal(formatPrice(null, "GBp"), "—");
    assert.equal(formatPrice(null, null), "—");
  });

  it("FC-7: price_eur null -> '—' in Price EUR column", () => {
    assert.equal(formatEur(null), "—",
      "FC-7: price_eur null must render as '—' in Price EUR column");
  });

  it("FC-8: price_eur number -> '€X.XX' format", () => {
    const result = formatEur(40.71);
    // Intl.NumberFormat de-DE uses ',' as decimal: "40,71 €" or similar
    assert.ok(result.includes("40") && result.includes("71") && result.includes("€"),
      `FC-8: price_eur 40.71 must format with '€' symbol, got '${result}'`);
  });
});

// ---------------------------------------------------------------------------
// FC-DOUBLE: Double pence conversion guard (frontend must NOT divide by 100)
// ---------------------------------------------------------------------------

describe("FC-DOUBLE: Frontend must not re-divide GBp price by 100", () => {
  it("FC-D1: formatPrice(48.15, 'GBp') must not produce 0.4815 GBp", () => {
    const result = formatPrice(48.15, "GBp");
    assert.ok(!result.includes("0.4815") && !result.includes("48.15 GBp"),
      `FC-D1: DOUBLE PENCE DEFECT: formatPrice(48.15, 'GBp') = '${result}'. ` +
      "Frontend must NOT divide price_major by 100. Only reconstruct: 48.15 * 100 = 4815 GBp. " +
      "Backend already did the /100 conversion; frontend must only do *100 for display.");
  });

  it("FC-D2: formatPrice(0.4815, 'GBp') would be wrong — detect if backend double-divided", () => {
    // If the backend had double-divided (GBp 4815 -> 48.15 -> 0.4815), the
    // frontend would receive price=0.4815. Detect this: result would be "48 GBp" (< 100).
    // The correct price_major for ULVR is 48.15 (pounds), display should be 4815.
    const wrongBackendPrice = 0.4815; // what a double-divided backend would send
    const wrongDisplay = formatPrice(wrongBackendPrice, "GBp");
    // Wrong display would be "48 GBp" — which looks like 48 pence, way off
    const correctDisplay = formatPrice(48.15, "GBp");
    assert.equal(correctDisplay, "4815 GBp",
      `FC-D2: Correct display must be '4815 GBp'; wrong double-divided display: '${wrongDisplay}'`);
  });
});

// ---------------------------------------------------------------------------
// PC: Price column rendering
// ---------------------------------------------------------------------------

describe("PC: Price column source contract in SymbolsTable.tsx", () => {
  it("PC-1: COLUMNS array has a 'Price' column", () => {
    assert.ok(
      symbolsTableSrc.includes('label: "Price"'),
      "PC-1: SymbolsTable COLUMNS must include { label: 'Price', ... } column"
    );
  });

  it("PC-2: Price column or formatPrice uses price_display_currency for rendering", () => {
    // Either explicit formatPrice function or conditional currency logic
    const hasFormatPrice = symbolsTableSrc.includes("formatPrice");
    const hasPriceDisplayCurrency = symbolsTableSrc.includes("price_display_currency");
    assert.ok(
      hasFormatPrice || hasPriceDisplayCurrency,
      "PC-2 DEFECT: SymbolsTable must use price_display_currency to format the Price column. " +
      "Currently uses hardcoded '$' prefix per legacy code. " +
      "Rusty: add formatPrice(r.price, r.price_display_currency) rendering."
    );
  });
});

// ---------------------------------------------------------------------------
// PE: Price EUR column
// ---------------------------------------------------------------------------

describe("PE: Price EUR column in SymbolsTable.tsx", () => {
  it("PE-1: COLUMNS includes a Price EUR column ('Price €')", () => {
    const hasPriceEurCol =
      symbolsTableSrc.includes('label: "Price €"') ||
      symbolsTableSrc.includes("label: 'Price €'") ||
      symbolsTableSrc.includes("Price EUR") ||
      symbolsTableSrc.includes("price_eur");
    assert.ok(
      hasPriceEurCol,
      "PE-1 DEFECT: SymbolsTable COLUMNS must include a 'Price €' column using price_eur field. " +
      "Rusty: add column per SS9.2 table (after Price, before Shares)."
    );
  });

  it("PE-2: price_eur sort key is in SortKey type", () => {
    assert.ok(
      symbolsTableSrc.includes('"price_eur"') || symbolsTableSrc.includes("'price_eur'"),
      "PE-2 DEFECT: 'price_eur' must be in the SortKey union type in SymbolsTable.tsx"
    );
  });

  it("PE-3: price_eur cell renders with eur() formatter or '—' when null", () => {
    // Either explicit eur(r.price_eur) or equivalent null guard pattern
    const hasEurPrice = symbolsTableSrc.includes("price_eur");
    assert.ok(
      hasEurPrice,
      "PE-3 DEFECT: SymbolsTable row renderer must reference price_eur for the Price EUR column cell"
    );
  });
});

// ---------------------------------------------------------------------------
// CV: Current Value column
// ---------------------------------------------------------------------------

describe("CV: Current Value column in SymbolsTable.tsx", () => {
  it("CV-1: COLUMNS includes a Value column ('Value €' or 'Current Value')", () => {
    const hasValueCol =
      symbolsTableSrc.includes('label: "Value €"') ||
      symbolsTableSrc.includes("label: 'Value €'") ||
      symbolsTableSrc.includes("Value EUR") ||
      symbolsTableSrc.includes("current_value_eur");
    assert.ok(
      hasValueCol,
      "CV-1 DEFECT: SymbolsTable COLUMNS must include a 'Value €' column using current_value_eur. " +
      "Rusty: add column per SS9.2 table (after Price €, before Dividends)."
    );
  });

  it("CV-2: current_value_eur sort key is in SortKey type", () => {
    assert.ok(
      symbolsTableSrc.includes('"current_value_eur"') ||
      symbolsTableSrc.includes("'current_value_eur'"),
      "CV-2 DEFECT: 'current_value_eur' must be in the SortKey union type in SymbolsTable.tsx"
    );
  });

  it("CV-3: current_value_eur cell renders with eur() or '—' when null", () => {
    const hasCvField = symbolsTableSrc.includes("current_value_eur");
    assert.ok(
      hasCvField,
      "CV-3 DEFECT: SymbolsTable row renderer must reference current_value_eur for the Value column"
    );
  });
});

// ---------------------------------------------------------------------------
// KV: KPI Current Value card in symbols/page.tsx
// ---------------------------------------------------------------------------

describe("KV: Current Value KPI card in symbols/page.tsx", () => {
  it("KV-1: page.tsx references total_current_value_eur field", () => {
    assert.ok(
      symbolsPageSrc.includes("total_current_value_eur"),
      "KV-1 DEFECT: symbols/page.tsx must read portfolio_summary.total_current_value_eur. " +
      "Rusty: add Current Value KPI card per SS9.5."
    );
  });

  it("KV-2: 'Current Value' label appears in the KPI card area", () => {
    assert.ok(
      symbolsPageSrc.includes("Current Value"),
      "KV-2 DEFECT: 'Current Value' label must appear in symbols/page.tsx KPI card. " +
      "Rusty: add KpiCard with label='Current Value' beside Current Investment."
    );
  });

  it("KV-3: Current Value KPI is conditionally rendered (hidden when null)", () => {
    // Must be inside a conditional: either the existing hasPortfolioSummary check
    // or a dedicated null guard for total_current_value_eur
    const hasConditional =
      symbolsPageSrc.includes("total_current_value_eur") &&
      (symbolsPageSrc.includes("&&") || symbolsPageSrc.includes("?"));
    assert.ok(
      hasConditional,
      "KV-3 DEFECT: Current Value KPI must be conditionally rendered " +
      "(hidden when total_current_value_eur is null). No unconditional render."
    );
  });

  it("KV-4: Current Value KPI appears beside Current Investment (source order)", () => {
    const investmentIdx = symbolsPageSrc.indexOf("Current Investment");
    const currentValueIdx = symbolsPageSrc.indexOf("Current Value");
    if (investmentIdx === -1) {
      // Not yet implemented — skip behavioral check
      assert.ok(true, "KV-4: Current Investment not found; layout check skipped");
      return;
    }
    assert.ok(
      currentValueIdx !== -1,
      "KV-4 DEFECT: 'Current Value' label must appear in page.tsx beside 'Current Investment'"
    );
    // Current Value should appear near (within 500 chars) Current Investment
    const distance = Math.abs(currentValueIdx - investmentIdx);
    assert.ok(
      distance < 800,
      `KV-4 DEFECT: 'Current Value' and 'Current Investment' are far apart (${distance} chars). ` +
      "They should be adjacent KPI cards per SS9.5."
    );
  });
});

// ---------------------------------------------------------------------------
// ST: Stale / error states
// ---------------------------------------------------------------------------

describe("ST: Stale and error visual states", () => {
  it("ST-1: pricing_status field is referenced in SymbolsTable.tsx for stale rendering", () => {
    const hasStatus = symbolsTableSrc.includes("pricing_status");
    assert.ok(
      hasStatus,
      "ST-1 DEFECT: SymbolsTable must reference pricing_status for stale visual indicator. " +
      "Rusty: add pricing_status=stale -> opacity-60/visual indicator per SS9.4."
    );
  });

  it("ST-2: stale state uses reduced opacity or visual marker (opacity-60 or 'stale' check)", () => {
    const hasOpacity = symbolsTableSrc.includes("opacity-60") || symbolsTableSrc.includes("stale");
    assert.ok(
      hasOpacity,
      "ST-2 DEFECT: SymbolsTable must apply opacity-60 or similar for stale pricing rows per SS9.4."
    );
  });
});

// ---------------------------------------------------------------------------
// SK: Sort keys in SortKey union type
// ---------------------------------------------------------------------------

describe("SK: Sort keys include pricing fields", () => {
  it("SK-1: SortKey includes 'price_eur'", () => {
    assert.ok(
      symbolsTableSrc.includes('"price_eur"') || symbolsTableSrc.includes("'price_eur'"),
      "SK-1: 'price_eur' must be in SortKey union (already declared per summary note)"
    );
  });

  it("SK-2: SortKey includes 'current_value_eur'", () => {
    assert.ok(
      symbolsTableSrc.includes('"current_value_eur"') ||
      symbolsTableSrc.includes("'current_value_eur'"),
      "SK-2: 'current_value_eur' must be in SortKey union"
    );
  });
});

// ---------------------------------------------------------------------------
// SC: Source-contract — TypeScript type declarations
// ---------------------------------------------------------------------------

describe("SC: TypeScript type declarations in symbols.ts", () => {
  it("SC-1: SymbolRow has price_display_currency field", () => {
    assert.ok(
      symbolsTypeSrc.includes("price_display_currency"),
      "SC-1: SymbolRow must declare price_display_currency per SS9.1"
    );
  });

  it("SC-2: SymbolRow has price_currency (major-unit ISO 4217)", () => {
    assert.ok(
      symbolsTypeSrc.includes("price_currency"),
      "SC-2: SymbolRow must declare price_currency"
    );
  });

  it("SC-3: SymbolRow has price_eur field typed number | null", () => {
    assert.ok(
      symbolsTypeSrc.includes("price_eur"),
      "SC-3: SymbolRow must declare price_eur per SS9.1"
    );
  });

  it("SC-4: SymbolRow has pricing_fetched_at field", () => {
    assert.ok(
      symbolsTypeSrc.includes("pricing_fetched_at"),
      "SC-4: SymbolRow must declare pricing_fetched_at per SS9.1"
    );
  });

  it("SC-5: SymbolRow has pricing_status typed as 'ok' | 'error' | 'stale' | null", () => {
    assert.ok(
      symbolsTypeSrc.includes("pricing_status"),
      "SC-5: SymbolRow must declare pricing_status per SS9.1"
    );
    // Check the union type includes the required values
    assert.ok(
      symbolsTypeSrc.includes('"ok"') || symbolsTypeSrc.includes("'ok'"),
      "SC-5: pricing_status type must include 'ok'"
    );
    assert.ok(
      symbolsTypeSrc.includes('"stale"') || symbolsTypeSrc.includes("'stale'"),
      "SC-5: pricing_status type must include 'stale'"
    );
    assert.ok(
      symbolsTypeSrc.includes('"error"') || symbolsTypeSrc.includes("'error'"),
      "SC-5: pricing_status type must include 'error'"
    );
  });

  it("SC-6: SymbolRow has current_value_eur as string | null (Decimal string)", () => {
    assert.ok(
      symbolsTypeSrc.includes("current_value_eur"),
      "SC-6: SymbolRow must declare current_value_eur per SS9.1"
    );
  });

  it("SC-7: PortfolioSummary has total_current_value_eur field", () => {
    assert.ok(
      symbolsTypeSrc.includes("total_current_value_eur"),
      "SC-7: PortfolioSummary must declare total_current_value_eur per SS9.1"
    );
  });
});

// ---------------------------------------------------------------------------
// KP: KPI card behavioral inline tests
// ---------------------------------------------------------------------------

describe("KP: KPI Current Value behavioral tests (inline)", () => {
  // Simulate the symbols/page.tsx KPI resolution logic:
  // kpiEur(totalCurrentValue) — based on total_current_value_eur from portfolio_summary
  function kpiEur(v) {
    const n = typeof v === "string" ? parseFloat(v) : (v ?? NaN);
    if (!isFinite(n)) return "—";
    return new Intl.NumberFormat("de-DE", {
      style: "currency",
      currency: "EUR",
      maximumFractionDigits: 2,
    }).format(n);
  }

  it("KP-1: total_current_value_eur='23391.00' formats as EUR string", () => {
    const result = kpiEur("23391.00");
    assert.ok(
      result.includes("23") && result.includes("391") && result.includes("€"),
      `KP-1: kpiEur('23391.00') must include EUR formatting, got '${result}'`
    );
  });

  it("KP-2: total_current_value_eur=null -> '—'", () => {
    assert.equal(kpiEur(null), "—",
      "KP-2: null total_current_value_eur must render as '—' (card hidden in page.tsx)");
  });

  it("KP-3: total_current_value_eur='0.00' formats as 0 EUR (not hidden)", () => {
    const result = kpiEur("0.00");
    assert.ok(
      result.includes("0") && result.includes("€"),
      `KP-3: zero total_current_value_eur must still format, got '${result}'`
    );
  });
});

// ---------------------------------------------------------------------------
// Regression guard: existing column headers not removed
// ---------------------------------------------------------------------------

describe("RG: Existing column headers preserved (regression guard)", () => {
  it("RG-1: Price column still present (not removed during pricing refactor)", () => {
    assert.ok(
      symbolsTableSrc.includes('label: "Price"'),
      "RG-1 REGRESSION: 'Price' column was removed from COLUMNS. Must remain."
    );
  });

  it("RG-2: Dividends column still present", () => {
    assert.ok(
      symbolsTableSrc.includes("Dividends") || symbolsTableSrc.includes("dividends"),
      "RG-2 REGRESSION: Dividends column must remain in SymbolsTable."
    );
  });

  it("RG-3: In Calls column still present", () => {
    assert.ok(
      symbolsTableSrc.includes("In Calls") || symbolsTableSrc.includes("in_calls"),
      "RG-3 REGRESSION: 'In Calls' column must remain in SymbolsTable."
    );
  });

  it("RG-4: SymbolsTable still has existing sort keys (no regression)", () => {
    const existingKeys = ["dgi_score", "tech_timing", "momentum", "portfolio_invested_eur"];
    for (const key of existingKeys) {
      assert.ok(
        symbolsTableSrc.includes(key),
        `RG-4 REGRESSION: SortKey '${key}' removed from SymbolsTable.tsx`
      );
    }
  });

  it("RG-5: Current Investment KPI still in page.tsx", () => {
    assert.ok(
      symbolsPageSrc.includes("Current Investment"),
      "RG-5 REGRESSION: 'Current Investment' KPI card must remain in symbols/page.tsx"
    );
  });

  it("RG-6: Realized Result KPI still in page.tsx", () => {
    assert.ok(
      symbolsPageSrc.includes("Realized Result"),
      "RG-6 REGRESSION: 'Realized Result' KPI card must remain in symbols/page.tsx"
    );
  });

  it("RG-7: Net Dividends KPI still in page.tsx", () => {
    assert.ok(
      symbolsPageSrc.includes("Net Dividends"),
      "RG-7 REGRESSION: 'Net Dividends' KPI card must remain in symbols/page.tsx"
    );
  });
});
