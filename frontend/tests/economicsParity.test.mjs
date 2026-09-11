/**
 * Visual/structural contract tests — Economics parity.
 *
 * INTENT
 * ======
 * Verify that Movements, Symbols toolbar, and Symbols KPI cards share the same
 * design-token vocabulary and structural invariants as the Economics surface.
 * This is NOT pixel-snapshot testing — it checks class-contract semantics via
 * source inspection, preferring component-import verification when component
 * reuse exists and falling back to structural invariant checks otherwise.
 *
 * INTERPRETATION OF COMPONENT REUSE
 * ==================================
 * • EconomicsView imports StatCard (shared KPI component) — tested directly.
 * • EconomicsView defines a local Pills sub-component — NOT exported; other
 *   surfaces must use either the same component or the same class contract.
 * • Symbols page defines a local KpiCard — acceptable light variant that MUST
 *   share the token vocabulary with StatCard (bg-bg-card, border-border, font-mono).
 * • SymbolsTable view-mode selector matches the Economics Pills container class
 *   CONTRACT exactly — tests verify this.
 * • Movements filter toolbar: container uses bg-bg-card/border-border tokens ✓;
 *   type filter currently uses <select> (not pills) — flagged as parity gap.
 *
 * CONTRACT GROUPS
 * ===============
 *   EP  Economics primitives baseline (ground truth — all should pass).
 *   SK  Symbols KPI card structural contract (token vocabulary parity).
 *   ST  SymbolsTable toolbar structural contract (pill class parity).
 *   MT  Movements filter toolbar structural contract (token + pill parity).
 *   MC  Movements summary cards (none currently; guard test for future).
 *   CR  Cross-surface design token consistency invariants.
 *   AC  Accessibility — focus/active states present on all filter controls.
 *   RG  Non-regression — key source invariants unchanged.
 *
 * Run: node --test frontend/tests/economicsParity.test.mjs
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join, dirname } from "node:path";

const __dir = dirname(fileURLToPath(import.meta.url));
const root  = join(__dir, "..");

function src(rel) { return readFileSync(join(root, rel), "utf8"); }

const economicsSrc  = src("src/components/EconomicsView.tsx");
const statCardSrc   = src("src/components/StatCard.tsx");
const symbolsPage   = src("src/app/symbols/page.tsx");
const symbolsTable  = src("src/components/SymbolsTable.tsx");
const movementsTbl  = src("src/components/PortfolioMovementsTable.tsx");
const addMovementSrc = src("src/components/AddMovementDialog.tsx");
const addPositionSrc = src("src/components/AddPositionForm.tsx");
const optionBadgesSrc = src("src/components/OptionLinkageBadges.tsx");
const movementDetailSrc = src("src/components/MovementDetailDialog.tsx");

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** True if `className` (or nearby text) contains `needle`. */
function hasClass(sourceTxt, needle) {
  return sourceTxt.includes(needle);
}

/**
 * True if there is NO raw Tailwind hex colour class (`bg-[#...]` / `text-[#...]`)
 * in the given source — except for classes that wrap a CSS custom-property
 * (`var(--...)`), which ARE tokens.
 */
function hasNoRawHex(sourceTxt) {
  // Match bg-[#xxx] or text-[#xxx] with literal hex (not var(...))
  const RAW_HEX = /\b(?:bg|text|border|fill|stroke)-\[#[0-9a-fA-F]{3,8}\]/g;
  return !RAW_HEX.test(sourceTxt);
}

/**
 * True if every `<select>` in `sourceTxt` has an `aria-label` attribute
 * on the SAME line or within 3 lines.
 * Heuristic: count select occurrences and aria-label occurrences; labels ≥ selects.
 */
function selectsHaveAriaLabels(sourceTxt) {
  const selectCount = (sourceTxt.match(/<select\b/g) || []).length;
  const ariaCount   = (sourceTxt.match(/aria-label=/g) || []).length;
  return ariaCount >= selectCount;
}

// ===========================================================================
// EP: Economics primitives baseline
// ===========================================================================

describe("EP: Economics primitives baseline (ground truth)", () => {
  it("EP-1: EconomicsView imports StatCard", () => {
    assert.ok(
      economicsSrc.includes("import StatCard"),
      "EP-1: EconomicsView must import StatCard — the shared KPI component."
    );
  });

  it("EP-2: StatCard carries the 'surface' utility class", () => {
    assert.ok(
      hasClass(statCardSrc, "surface"),
      "EP-2: StatCard must use the 'surface' utility class for background/border/radius."
    );
  });

  it("EP-3: StatCard carries 'card-hover' for elevation transition", () => {
    assert.ok(
      hasClass(statCardSrc, "card-hover"),
      "EP-3: StatCard must use 'card-hover' for smooth elevation on hover."
    );
  });

  it("EP-4: StatCard label uses 'text-xs' and 'text-text-muted'", () => {
    assert.ok(
      statCardSrc.includes("text-xs") && statCardSrc.includes("text-text-muted"),
      "EP-4: StatCard label must use text-xs and text-text-muted semantic tokens."
    );
  });

  it("EP-5: StatCard value uses 'font-mono'", () => {
    assert.ok(
      statCardSrc.includes("font-mono"),
      "EP-5: StatCard value must use font-mono for numeric display."
    );
  });

  it("EP-6: Economics Pills container uses 'rounded-[var(--radius-pill)] border border-border bg-bg-card p-1'", () => {
    assert.ok(
      economicsSrc.includes("rounded-[var(--radius-pill)] border border-border bg-bg-card p-1"),
      "EP-6: Economics Pills container must use the pill-radius/border/card token contract."
    );
  });

  it("EP-7: Economics Pills active button uses 'bg-accent-blue text-white'", () => {
    assert.ok(
      economicsSrc.includes("bg-accent-blue text-white"),
      "EP-7: Economics Pills active state must use bg-accent-blue text-white."
    );
  });

  it("EP-8: Economics Pills button uses 'rounded-[var(--radius-pill)] px-3 py-1 text-xs'", () => {
    assert.ok(
      economicsSrc.includes("rounded-[var(--radius-pill)] px-3 py-1"),
      "EP-8: Economics Pills button must use pill-radius px-3 py-1 text-xs compact sizing."
    );
  });

  it("EP-9: EconomicsView KPI grid uses CSS grid with gap-4", () => {
    assert.ok(
      economicsSrc.includes("grid") && economicsSrc.includes("gap-4"),
      "EP-9: Economics KPI section must use a CSS grid layout with gap-4."
    );
  });

  it("EP-10: StatCard gradient accent bar uses inline style (design token, not hardcoded)", () => {
    // The bar uses style={{ background: t.bar }} where t.bar is a var() reference
    assert.ok(
      statCardSrc.includes("t.bar") || statCardSrc.includes("style="),
      "EP-10: StatCard gradient bar must be applied via inline style using design token vars."
    );
  });
});

// ===========================================================================
// SK: Symbols KPI card structural contract
// ===========================================================================

describe("SK: Symbols KPI card — structural contract parity with Economics", () => {
  it("SK-1: Symbols page defines a local KpiCard component", () => {
    assert.ok(
      symbolsPage.includes("function KpiCard"),
      "SK-1: Symbols page must define a KpiCard component for portfolio summary metrics."
    );
  });

  it("SK-2: KpiCard uses 'bg-bg-card' design token (same as StatCard/surface)", () => {
    assert.ok(
      symbolsPage.includes("bg-bg-card"),
      "SK-2: KpiCard must use bg-bg-card token — same background token as Economics StatCard."
    );
  });

  it("SK-3: KpiCard uses 'border border-border' semantic token pair", () => {
    assert.ok(
      symbolsPage.includes("border border-border"),
      "SK-3: KpiCard must use border border-border — same border contract as Economics."
    );
  });

  it("SK-4: KpiCard uses 'rounded-[var(--radius)]' radius token", () => {
    assert.ok(
      symbolsPage.includes("rounded-[var(--radius)]"),
      "SK-4: KpiCard must use a var(--radius...) token — same radius-token family as Economics."
    );
  });

  it("SK-5: KpiCard label uses 'text-xs text-text-muted' (parity with StatCard label)", () => {
    assert.ok(
      symbolsPage.includes("text-xs text-text-muted"),
      "SK-5: KpiCard label must use text-xs text-text-muted — same label contract as StatCard."
    );
  });

  it("SK-6: KpiCard value uses 'font-mono' (parity with StatCard value)", () => {
    assert.ok(
      symbolsPage.includes("font-mono"),
      "SK-6: KpiCard value must use font-mono — same numeric display token as StatCard."
    );
  });

  it("SK-7: KpiCard row uses 'flex flex-wrap gap-3' responsive layout", () => {
    assert.ok(
      symbolsPage.includes("flex flex-wrap gap-3"),
      "SK-7: KpiCard row must use flex-wrap for responsive stacking (mobile → desktop)."
    );
  });

  it("SK-8: KpiCard tone-based text uses 'text-accent-*' tokens (no hardcoded hex)", () => {
    // Tone colours should be text-accent-green, text-accent-red, text-text — all semantic
    assert.ok(
      symbolsPage.includes("text-accent-green") &&
      symbolsPage.includes("text-accent-red") &&
      symbolsPage.includes("text-text"),
      "SK-8: KpiCard tone classes must use text-accent-* semantic tokens."
    );
  });

  it("SK-9: Symbols page does NOT import StatCard (local KpiCard is the correct lighter variant)", () => {
    // Symbols page intentionally uses a lighter local KpiCard, not the animated StatCard.
    // This is ACCEPTABLE — document it, don't require StatCard import.
    assert.ok(
      !symbolsPage.includes("import StatCard"),
      "SK-9: Symbols page should use local KpiCard, not StatCard " +
      "(KpiCard is a lighter variant; StatCard is for Economics-level animated display)."
    );
  });

  it("SK-10: KpiCard has 'flex-1 min-w-[160px]' for responsive equal-width columns", () => {
    assert.ok(
      symbolsPage.includes("flex-1") && symbolsPage.includes("min-w-"),
      "SK-10: KpiCard must use flex-1 and min-w-* to achieve responsive equal-width columns."
    );
  });
});

// ===========================================================================
// ST: SymbolsTable toolbar structural contract
// ===========================================================================

describe("ST: SymbolsTable toolbar — Economics Pills class-contract parity", () => {
  it("ST-1: View-mode selector container uses 'rounded-[var(--radius-pill)] border border-border bg-bg-card' (exact Economics Pills container contract)", () => {
    // Economics Pills: "rounded-[var(--radius-pill)] border border-border bg-bg-card p-1"
    // SymbolsTable view selector uses the same token set (p-0.5 vs p-1 is an acceptable variation)
    assert.ok(
      symbolsTable.includes("rounded-[var(--radius-pill)] border border-border bg-bg-card"),
      "ST-1: View-mode selector container must use rounded-[var(--radius-pill)] border border-border bg-bg-card — " +
      "exact Economics Pills container token contract."
    );
  });

  it("ST-2: View-mode selector active button uses 'bg-accent-blue text-white' (exact Economics Pills active state)", () => {
    assert.ok(
      symbolsTable.includes("bg-accent-blue text-white"),
      "ST-2: Active view-mode button must use bg-accent-blue text-white — " +
      "exact Economics Pills active state contract."
    );
  });

  it("ST-3: View-mode selector buttons use 'rounded-[var(--radius-pill)] px-3 py-1 text-xs' (Economics Pills button contract)", () => {
    assert.ok(
      symbolsTable.includes("rounded-[var(--radius-pill)] px-3 py-1"),
      "ST-3: View-mode buttons must use rounded-[var(--radius-pill)] px-3 py-1 text-xs — " +
      "Economics Pills button contract."
    );
  });

  it("ST-4: View-mode selector has role='radiogroup' (accessible segmented control)", () => {
    assert.ok(
      symbolsTable.includes('role="radiogroup"'),
      "ST-4: View-mode selector must use role='radiogroup' for screen-reader semantics."
    );
  });

  it("ST-5: Suitability filter buttons use 'rounded-[var(--radius-pill)] border px-3 py-1 text-xs'", () => {
    assert.ok(
      symbolsTable.includes("rounded-[var(--radius-pill)] border px-3 py-1 text-xs"),
      "ST-5: Suitability filter pills must use rounded-[var(--radius-pill)] border px-3 py-1 text-xs compact sizing."
    );
  });

  it("ST-6: Suitability active pill uses 'border-accent-blue' token (outline active variant)", () => {
    assert.ok(
      symbolsTable.includes("border-accent-blue"),
      "ST-6: Active suitability pill must use border-accent-blue for visible active state."
    );
  });

  it("ST-7: Search input uses 'rounded-[var(--radius-pill)] border border-border bg-bg-input' tokens", () => {
    assert.ok(
      symbolsTable.includes("rounded-[var(--radius-pill)] border border-border bg-bg-input"),
      "ST-7: Search input must use pill-radius, border, and bg-bg-input design tokens."
    );
  });

  it("ST-8: Toolbar uses 'flex flex-wrap items-center' responsive layout", () => {
    assert.ok(
      symbolsTable.includes("flex flex-wrap items-center"),
      "ST-8: SymbolsTable toolbar must use flex-wrap for responsive stacking on narrow viewports."
    );
  });

  it("ST-9: All filter/action buttons have type='button' (no implicit form submit)", () => {
    // Source has 5 distinct type="button" occurrences: Portfolio/Options view buttons,
    // suitability filter button template (map), delete row button, and dismiss error button.
    // The suitability map renders 5 buttons from 1 source occurrence — so source count ≥ 5.
    const count = (symbolsTable.match(/type="button"/g) || []).length;
    assert.ok(count >= 5, `ST-9: Expected ≥5 type="button" attributes in SymbolsTable; found ${count}.`);
  });
});

// ===========================================================================
// MT: Movements filter toolbar structural contract
// ===========================================================================

describe("MT: Movements filter toolbar — Economics token contract parity", () => {
  it("MT-1: Filter container uses 'bg-bg-card' design token (Economics surface token)", () => {
    // The filter wrapper div uses bg-bg-card (same background token as Economics surface)
    assert.ok(
      movementsTbl.includes("bg-bg-card"),
      "MT-1: Movements filter container must use bg-bg-card — the Economics surface background token."
    );
  });

  it("MT-2: Filter container uses 'border border-border' semantic token pair", () => {
    assert.ok(
      movementsTbl.includes("border border-border"),
      "MT-2: Movements filter container must use border border-border — same border contract."
    );
  });

  it("MT-3: Filter container uses 'rounded-[var(--radius...)]' radius token", () => {
    assert.ok(
      movementsTbl.includes("rounded-[var(--radius"),
      "MT-3: Movements filter container must use a var(--radius...) token family value."
    );
  });

  it("MT-4: Filter toolbar uses 'flex flex-wrap' responsive layout", () => {
    assert.ok(
      movementsTbl.includes("flex flex-wrap"),
      "MT-4: Movements filter toolbar must use flex-wrap for responsive stacking."
    );
  });

  it("MT-5: All filter inputs have 'aria-label' attributes", () => {
    assert.ok(
      selectsHaveAriaLabels(movementsTbl),
      "MT-5: All <select> filter controls in PortfolioMovementsTable must have aria-label attributes."
    );
  });

  it("MT-6: Filter inputs use 'bg-bg-input' token (not hardcoded background)", () => {
    assert.ok(
      movementsTbl.includes("bg-bg-input"),
      "MT-6: Filter inputs must use bg-bg-input design token for input backgrounds."
    );
  });

  it("MT-7: Apply/Reset action buttons have type='button'", () => {
    // These action buttons (Apply filter, Reset filter) must not trigger form submit
    const hasApply = movementsTbl.includes("applyFilter") || movementsTbl.includes("Apply");
    const hasReset = movementsTbl.includes("resetFilter") || movementsTbl.includes("Reset");
    const typeCount = (movementsTbl.match(/type="button"/g) || []).length;
    assert.ok(
      hasApply && hasReset && typeCount >= 2,
      "MT-7: Apply and Reset filter buttons must be type='button'."
    );
  });

  it("MT-8: No raw hex colour classes in filter toolbar (design token vocabulary only)", () => {
    assert.ok(
      hasNoRawHex(movementsTbl),
      "MT-8: PortfolioMovementsTable must not use raw hex colour classes — " +
      "use semantic tokens (text-accent-*, bg-bg-*, border-border) only."
    );
  });

  it("MT-9: Type filter uses pill-style buttons, not <select> dropdown (Economics parity gap — DEFECT)", () => {
    // Economics and StockTransactionsTable both use pill buttons for type filtering.
    // PortfolioMovementsTable currently uses a <select> dropdown for txn_type.
    // For visual parity with the Economics surface, the type filter should use
    // segmented pill controls matching the Economics Pills class contract.
    const usesSelect = movementsTbl.includes("<select") &&
      (movementsTbl.includes("txn_type") || movementsTbl.includes("BUY"));
    const usesPills =
      movementsTbl.includes("rounded-[var(--radius-pill)] px-3 py-1") ||
      movementsTbl.includes("TYPE_PILLS") ||
      (movementsTbl.includes("type=\"button\"") &&
       movementsTbl.includes("rounded-[var(--radius-pill)]") &&
       (movementsTbl.includes('"BUY"') || movementsTbl.includes("'BUY'")));
    assert.ok(
      usesPills && !usesSelect,
      "MT-9 DEFECT: Type filter uses <select> dropdown instead of pill-style buttons. " +
      "Rusty: replace the txn_type <select> with a TYPE_PILLS array of rounded-[var(--radius-pill)] " +
      "buttons matching the Economics Pills class contract and StockTransactionsTable precedent."
    );
  });

  it("MT-10: Type filter active state uses 'bg-accent-blue' token when pills are used", () => {
    // This test is conditional: only meaningful once MT-9 passes (pills implemented).
    // If pills are not yet present, test acts as documentation.
    const hasAccentBlueActive =
      movementsTbl.includes("bg-accent-blue") &&
      movementsTbl.includes("rounded-[var(--radius-pill)]") &&
      (movementsTbl.includes("BUY") || movementsTbl.includes("txn_type"));
    if (!movementsTbl.includes("rounded-[var(--radius-pill)]")) {
      // Pills not yet implemented — report as documentation (MT-9 already flags the gap)
      assert.ok(
        false,
        "MT-10 DEFECT: Cannot verify active-state token — type filter pills not yet implemented. " +
        "When pills are added (MT-9), the active button must use bg-accent-blue text-white."
      );
    } else {
      assert.ok(
        hasAccentBlueActive,
        "MT-10 DEFECT: Type filter pills active state must use bg-accent-blue token."
      );
    }
  });
});

// ===========================================================================
// MC: Movements summary cards (none currently — guard test)
// ===========================================================================

describe("MC: Movements summary cards — alignment guard", () => {
  it("MC-1: PortfolioMovementsTable does not currently use StatCard (verified state)", () => {
    // StatCard is Economics-level animated; Movements doesn't have summary KPIs yet.
    // This test documents the current state.
    assert.ok(
      !movementsTbl.includes("import StatCard") && !movementsTbl.includes("<StatCard"),
      "MC-1: PortfolioMovementsTable must not import StatCard — " +
      "if summary KPI cards are added, use a KpiCard-style light component or StatCard consistently."
    );
  });

  it("MC-2: If Movements gains summary cards, they must use bg-bg-card token (forward contract)", () => {
    // Guard: if summary cards ARE added, they must use the bg-bg-card token.
    // Currently no cards exist — test passes trivially.
    const hasSummaryCards =
      movementsTbl.includes("KpiCard") || movementsTbl.includes("summary-card");
    if (hasSummaryCards) {
      assert.ok(
        movementsTbl.includes("bg-bg-card"),
        "MC-2 DEFECT: Movements summary cards must use bg-bg-card design token for background."
      );
    } else {
      // No summary cards yet — pass trivially (forward contract documented)
      assert.ok(true, "MC-2: No summary cards in Movements yet — contract documented for future use.");
    }
  });

  it("MC-3: If Movements gains summary cards, they must use font-mono for values (forward contract)", () => {
    const hasSummaryCards =
      movementsTbl.includes("KpiCard") || movementsTbl.includes("StatCard");
    if (hasSummaryCards) {
      assert.ok(
        movementsTbl.includes("font-mono"),
        "MC-3 DEFECT: Movements summary card values must use font-mono token."
      );
    } else {
      assert.ok(true, "MC-3: No summary cards — forward contract documented.");
    }
  });
});

// ===========================================================================
// CR: Cross-surface design token consistency
// ===========================================================================

describe("CR: Cross-surface design token consistency", () => {
  it("CR-1: All three surfaces (Economics/Symbols/Movements) use 'border-border' token", () => {
    assert.ok(economicsSrc.includes("border-border"),  "CR-1a: EconomicsView must use border-border token.");
    assert.ok(symbolsTable.includes("border-border"),  "CR-1b: SymbolsTable must use border-border token.");
    assert.ok(movementsTbl.includes("border-border"),  "CR-1c: PortfolioMovementsTable must use border-border token.");
  });

  it("CR-2: All three surfaces use 'bg-bg-card' or 'surface' for elevated containers", () => {
    // Economics uses 'surface' (utility class) which encapsulates bg-bg-card
    // Symbols and Movements use explicit bg-bg-card Tailwind class
    assert.ok(
      economicsSrc.includes("surface") || economicsSrc.includes("bg-bg-card"),
      "CR-2a: Economics must use surface utility or bg-bg-card token."
    );
    assert.ok(symbolsPage.includes("bg-bg-card"),  "CR-2b: Symbols page must use bg-bg-card token.");
    assert.ok(movementsTbl.includes("bg-bg-card"), "CR-2c: Movements must use bg-bg-card token.");
  });

  it("CR-3: All pill/button active states use 'accent-blue' token family", () => {
    // Economics Pills: bg-accent-blue text-white
    // SymbolsTable view selector: bg-accent-blue text-white
    // SymbolsTable suitability pills: border-accent-blue bg-accent-blue/15 text-accent-blue
    assert.ok(economicsSrc.includes("accent-blue"), "CR-3a: Economics active state uses accent-blue.");
    assert.ok(symbolsTable.includes("accent-blue"), "CR-3b: SymbolsTable active state uses accent-blue.");
    assert.ok(movementsTbl.includes("accent-blue"), "CR-3c: Movements uses accent-blue for action/active elements.");
  });

  it("CR-4: All surfaces use 'var(--radius...)' token family for border-radius", () => {
    assert.ok(economicsSrc.includes("var(--radius"), "CR-4a: EconomicsView uses var(--radius...) token.");
    assert.ok(symbolsPage.includes("var(--radius"),   "CR-4b: Symbols page uses var(--radius...) token.");
    assert.ok(symbolsTable.includes("var(--radius"),  "CR-4c: SymbolsTable uses var(--radius...) token.");
    assert.ok(movementsTbl.includes("var(--radius"),  "CR-4d: Movements uses var(--radius...) token.");
  });

  it("CR-5: All label/helper text uses 'text-text-muted' semantic token", () => {
    assert.ok(economicsSrc.includes("text-text-muted"),  "CR-5a: EconomicsView uses text-text-muted.");
    assert.ok(symbolsPage.includes("text-text-muted"),   "CR-5b: Symbols page uses text-text-muted.");
    assert.ok(symbolsTable.includes("text-text-muted"),  "CR-5c: SymbolsTable uses text-text-muted.");
    assert.ok(movementsTbl.includes("text-text-muted"),  "CR-5d: Movements uses text-text-muted.");
  });

  it("CR-6: No raw hex colour classes in any surface (design token vocabulary only)", () => {
    assert.ok(hasNoRawHex(statCardSrc),  "CR-6a: StatCard must not use raw hex colours.");
    assert.ok(hasNoRawHex(symbolsPage),  "CR-6b: Symbols page must not use raw hex colours.");
    assert.ok(hasNoRawHex(symbolsTable), "CR-6c: SymbolsTable must not use raw hex colours.");
    assert.ok(hasNoRawHex(movementsTbl), "CR-6d: Movements must not use raw hex colours.");
  });

  it("CR-7: Economics StatCard and Symbols KpiCard share 'font-mono' + 'text-text-muted' label contract", () => {
    // Verify both surfaces use the same key token pair for value display + label
    assert.ok(statCardSrc.includes("font-mono") && statCardSrc.includes("text-text-muted"),
      "CR-7a: StatCard must have font-mono value and text-text-muted label.");
    assert.ok(symbolsPage.includes("font-mono") && symbolsPage.includes("text-text-muted"),
      "CR-7b: KpiCard must have font-mono value and text-text-muted label.");
  });
});

// ===========================================================================
// AC: Accessibility — focus/active states on all filter controls
// ===========================================================================

describe("AC: Accessibility — focus/active states present on all filter controls", () => {
  it("AC-1: SymbolsTable view-mode selector has aria-label on the radiogroup wrapper", () => {
    assert.ok(
      symbolsTable.includes('aria-label="Table view mode"') ||
      (symbolsTable.includes("role=\"radiogroup\"") && symbolsTable.includes("aria-label")),
      "AC-1: View-mode selector wrapper must have aria-label for screen-reader announcement."
    );
  });

  it("AC-2: SymbolsTable view-mode buttons have aria-checked for active state", () => {
    assert.ok(
      symbolsTable.includes("aria-checked"),
      "AC-2: View-mode radio buttons must use aria-checked={viewMode === mode} for active state announcement."
    );
  });

  it("AC-3: SymbolsTable search input has aria-label or accessible placeholder", () => {
    // Existing search uses placeholder="🔍 Filter symbols…" — acceptable
    const hasAccessibleLabel =
      symbolsTable.includes('aria-label=') ||
      symbolsTable.includes('placeholder=');
    assert.ok(hasAccessibleLabel, "AC-3: Search input must have aria-label or accessible placeholder.");
  });

  it("AC-4: SymbolsTable hide-zero checkbox has aria-label", () => {
    assert.ok(
      symbolsTable.includes('aria-label="Hide historical zero-share symbols"'),
      "AC-4: Hide-zero checkbox must have aria-label for screen readers."
    );
  });

  it("AC-5: Movements filter inputs all have aria-label (existing — must not regress)", () => {
    // From MT-5 — re-verified here in the accessibility group
    assert.ok(
      movementsTbl.includes('aria-label="Filter by account"') &&
      movementsTbl.includes('aria-label="Filter by transaction type"') &&
      movementsTbl.includes('aria-label="Filter by symbol"'),
      "AC-5: Movements filter inputs must all carry aria-label attributes."
    );
  });

  it("AC-6: Economics Pills buttons have type='button' (no implicit form submit)", () => {
    assert.ok(
      economicsSrc.includes('type="button"'),
      "AC-6: Economics Pills buttons must have type='button'."
    );
  });

  it("AC-7: SymbolsTable suitability buttons have type='button'", () => {
    // Verified indirectly: type-button count ≥6 in ST-9; re-confirm here for suitability area
    assert.ok(
      symbolsTable.includes('type="button"'),
      "AC-7: SymbolsTable suitability filter buttons must have type='button'."
    );
  });

  it("AC-8: SymbolsTable delete button has aria-label identifying the target symbol", () => {
    assert.ok(
      symbolsTable.includes("aria-label={`Delete") ||
      symbolsTable.includes('aria-label=`Delete'),
      "AC-8: Delete button must have a dynamic aria-label that names the symbol being deleted."
    );
  });
});

// ===========================================================================
// RG: Non-regression — key source invariants unchanged
// ===========================================================================

describe("RG: Non-regression — key invariants not broken by visual contract changes", () => {
  it("RG-1: EconomicsView still imports StatCard (not replaced with raw div)", () => {
    assert.ok(economicsSrc.includes("import StatCard"), "RG-1: StatCard import must remain in EconomicsView.");
  });

  it("RG-2: SymbolsTable view-mode state variable still present", () => {
    assert.ok(
      symbolsTable.includes("viewMode") || symbolsTable.includes("tableMode"),
      "RG-2: View-mode state must remain in SymbolsTable."
    );
  });

  it("RG-3: SymbolsTable SUITABILITY_FILTERS (All/Ideal Calls/Ideal Puts/No Puts/No Calls) still present", () => {
    // SymbolsTable has suitability filters, not transaction type filters.
    // Transaction type (Buy/Sell/Dividend) belongs to StockTransactionsTable.
    assert.ok(
      symbolsTable.includes("SUITABILITY_FILTERS") &&
      symbolsTable.includes("ideal_calls") &&
      symbolsTable.includes("ideal_puts"),
      "RG-3: SymbolsTable must retain SUITABILITY_FILTERS with ideal_calls/ideal_puts entries."
    );
  });

  it("RG-4: Symbols KpiCard 'flex-1 min-w-[160px]' flex layout not removed", () => {
    assert.ok(
      symbolsPage.includes("flex-1") && symbolsPage.includes("min-w-"),
      "RG-4: KpiCard flex-1 min-w layout must not be removed — cards collapse without it."
    );
  });

  it("RG-5: Movements per-movement account reassignment remains accessible via detail dialog", () => {
    // Individual movement reassignment is in MovementDetailDialog (ReassignmentDialog child).
    // PortfolioMovementsTable must import and render MovementDetailDialog for this to be accessible.
    assert.ok(
      movementsTbl.includes("MovementDetailDialog"),
      "RG-5: PortfolioMovementsTable must import/render MovementDetailDialog — " +
      "individual account reassignment ('Reassign account') is only accessible via that dialog."
    );
  });

  it("RG-6: PortfolioMovementsTable pagination buttons preserved", () => {
    assert.ok(
      movementsTbl.includes("Prev") || movementsTbl.includes("← Prev"),
      "RG-6: Prev pagination button must remain in Movements."
    );
  });

  it("RG-7: SymbolsTable 'surface table-modern' wrapper preserved (table contrast styling)", () => {
    assert.ok(
      symbolsTable.includes("surface") && symbolsTable.includes("table-modern"),
      "RG-7: SymbolsTable table wrapper must retain 'surface table-modern' classes."
    );
  });

  it("RG-8: StatCard font-mono text-3xl value sizing not downgraded", () => {
    assert.ok(
      statCardSrc.includes("text-3xl") && statCardSrc.includes("font-mono"),
      "RG-8: StatCard value must remain text-3xl font-mono — do not reduce Economics card size."
    );
  });
});

describe("PP: Paper positions and movement drilldown source contract", () => {
  it("PP-1: EconomicsView includes the showPaperPositions toggle and paper-exclusion copy", () => {
    assert.ok(
      economicsSrc.includes("showPaperPositions") &&
      economicsSrc.includes("Show paper positions") &&
      economicsSrc.includes("excluded_paper_positions"),
      "PP-1 DEFECT: EconomicsView must include the Show paper positions toggle and paper-exclusion coverage copy."
    );
  });

  it("PP-2: EconomicsView keeps MovementDetailDialog and Position Movements drilldown flow", () => {
    assert.ok(
      economicsSrc.includes("Position Movements") &&
      economicsSrc.includes("MovementDetailDialog") &&
      economicsSrc.includes("option_position_id"),
      "PP-2 DEFECT: EconomicsView must use the movements-by-position drilldown flow."
    );
  });

  it("PP-3: OptionLinkageBadges exports a reusable Paper badge", () => {
    assert.ok(
      optionBadgesSrc.includes("export function PaperBadge") &&
      optionBadgesSrc.includes("Paper"),
      "PP-3 DEFECT: OptionLinkageBadges must export a reusable PaperBadge."
    );
  });

  it("PP-4: AddPositionForm exposes Add Paper Position affordance", () => {
    assert.ok(
      addPositionSrc.includes("Add Paper Position") &&
      addPositionSrc.includes("is_paper"),
      "PP-4 DEFECT: AddPositionForm must send is_paper via Add Paper Position."
    );
  });

  it("PP-5: AddMovementDialog supports paper-position creation flow", () => {
    assert.ok(
      addMovementSrc.includes("Paper position") &&
      addMovementSrc.includes("is_paper") &&
      addMovementSrc.includes("/api/symbols/") &&
      addMovementSrc.includes("Failed to create paper position"),
      "PP-5 DEFECT: AddMovementDialog must support paper-position creation before manual option movement save."
    );
  });

  it("PP-6: MovementDetailDialog renders a Paper marker", () => {
    assert.ok(
      movementDetailSrc.includes("PaperBadge") &&
      movementDetailSrc.includes("m.is_paper"),
      "PP-6 DEFECT: MovementDetailDialog must render a Paper marker for paper movements."
    );
  });
});
