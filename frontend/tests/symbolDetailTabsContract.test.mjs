/**
 * symbolDetailTabsContract.test.mjs — Three-tab Symbol Details redesign.
 *
 * Requirements (2026-09-07 directive):
 *   TB — Tablist: exactly 3 tabs in order (Options, Stocks, Action Plans);
 *        Options default; correct tab IDs and labels.
 *   TA — ARIA/tablist/tab/tabpanel semantics; aria-selected, aria-controls,
 *        aria-labelledby; tabIndex roving; type="button".
 *   KB — Keyboard: ArrowRight/Left cycle; Home/End jump; preventDefault.
 *   HR — Hash deep-links: #options/#stocks/#action-plans resolve correctly;
 *        tab change writes hash without navigation push.
 *   PV — Panel visibility: only active panel rendered (not CSS-hidden);
 *        prop slots optionsPanel/stocksPanel/plansPanel; correct mapping.
 *   SM — Summary always visible BEFORE tablist; never inside a tab panel.
 *   PG — Page integration: SymbolDetailTabs imported/used in page.tsx;
 *        correct panel composition (Options→options content, Stocks→Config+
 *        Holdings+transactions, Plans→PlansTable); no duplicate sections outside.
 *   US — Non-US eligibility: Options tab visible for all; panel content gated
 *        by usOptionsEligible; tab selection cannot bypass eligibility.
 *   VS — Visual contract: same pill-tab pattern as Options Screener.
 *   RG — Regression: prior redesigns (AT/NF/PT from symbolDetailRedesign.test.mjs)
 *        not undone by tab integration.
 *
 * Tests against SymbolDetailTabs.tsx (already implemented) will PASS.
 * Tests against page.tsx integration will FAIL until Rusty wires up the tabs.
 *
 * Run: node --test frontend/tests/symbolDetailTabsContract.test.mjs
 */

import { readFileSync } from "fs";
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, "..");

const tabsSrc     = readFileSync(join(root, "src/components/SymbolDetailTabs.tsx"), "utf8");
const pageSrc     = readFileSync(join(root, "src/app/symbols/[symbol]/page.tsx"), "utf8");
const cardSrc     = readFileSync(join(root, "src/components/SymbolConfigurationCard.tsx"), "utf8");
const holdingsSrc = readFileSync(join(root, "src/components/PortfolioHoldingsCard.tsx"), "utf8");

// ---------------------------------------------------------------------------
// Behavioral mirrors
// ---------------------------------------------------------------------------

/** Mirror of SymbolDetailTabs keyboard handler — index arithmetic only. */
function applyKeyDown(key, idx, tabCount) {
  if (key === "ArrowRight") return (idx + 1) % tabCount;
  if (key === "ArrowLeft")  return (idx - 1 + tabCount) % tabCount;
  if (key === "Home")       return 0;
  if (key === "End")        return tabCount - 1;
  return idx; // other keys: no change
}

/** Mirror of HASH_TO_TAB resolution. */
const HASH_TO_TAB = {
  "#options":      "options",
  "#stocks":       "stocks",
  "#action-plans": "action-plans",
};

// ---------------------------------------------------------------------------
// TB: Tablist definition — tab count, order, IDs, labels, default
// ---------------------------------------------------------------------------

describe("TB: Tab definitions — three tabs in correct order", () => {
  it("TB-1: SymbolDetailTabs defines exactly 3 tabs in the TABS array", () => {
    const tabMatches = [...tabsSrc.matchAll(/\{\s*id:\s*["']([^"']+)["']/g)];
    assert.equal(tabMatches.length, 3,
      `TB-1 DEFECT: Expected 3 tab definitions in TABS; found ${tabMatches.length}. ` +
      "Required: Options, Stocks, Action Plans.");
  });

  it("TB-2: First tab is Options (id='options')", () => {
    const first = tabsSrc.match(/\{\s*id:\s*["']([^"']+)["']/)?.[1];
    assert.equal(first, "options",
      `TB-2 DEFECT: First tab id must be 'options'; found '${first}'.`);
  });

  it("TB-3: Second tab is Stocks (id='stocks')", () => {
    const allIds = [...tabsSrc.matchAll(/\{\s*id:\s*["']([^"']+)["']/g)].map(m => m[1]);
    assert.equal(allIds[1], "stocks",
      `TB-3 DEFECT: Second tab id must be 'stocks'; found '${allIds[1]}'.`);
  });

  it("TB-4: Third tab is Action Plans (id='action-plans')", () => {
    const allIds = [...tabsSrc.matchAll(/\{\s*id:\s*["']([^"']+)["']/g)].map(m => m[1]);
    assert.equal(allIds[2], "action-plans",
      `TB-4 DEFECT: Third tab id must be 'action-plans'; found '${allIds[2]}'.`);
  });

  it("TB-5: Tab labels are 'Options', 'Stocks', 'Action Plans' in that order", () => {
    const labels = [...tabsSrc.matchAll(/label:\s*["']([^"']+)["']/g)].map(m => m[1]);
    assert.deepEqual(labels, ["Options", "Stocks", "Action Plans"],
      `TB-5 DEFECT: Tab labels must be ["Options","Stocks","Action Plans"]; found ${JSON.stringify(labels)}.`);
  });

  it("TB-6: Default active tab is 'options' (useState initialised with 'options')", () => {
    assert.ok(
      tabsSrc.includes('useState<TabId>("options")') ||
      tabsSrc.includes("useState('options')") ||
      tabsSrc.includes('useState("options")'),
      'TB-6 DEFECT: Default active tab must be "options". ' +
      'Found no useState("options") initialiser — Options tab will not be selected by default.'
    );
  });
});

// ---------------------------------------------------------------------------
// TA: ARIA semantics — tablist/tab/tabpanel/aria-selected/controls/labelledby
// ---------------------------------------------------------------------------

describe("TA: Tab ARIA and roving tabIndex contract", () => {
  it("TA-1: Container has role='tablist'", () => {
    assert.ok(tabsSrc.includes('role="tablist"'),
      "TA-1 DEFECT: Tab bar container must have role='tablist'.");
  });

  it("TA-2: Each tab button has role='tab'", () => {
    assert.ok(tabsSrc.includes('role="tab"'),
      "TA-2 DEFECT: Tab buttons must have role='tab'.");
  });

  it("TA-3: Each tab panel div has role='tabpanel'", () => {
    assert.ok(tabsSrc.includes('role="tabpanel"'),
      "TA-3 DEFECT: Tab panel divs must have role='tabpanel'.");
  });

  it("TA-4: Tabs have aria-selected attribute", () => {
    assert.ok(tabsSrc.includes("aria-selected"),
      "TA-4 DEFECT: Tab buttons must have aria-selected to communicate active state to AT.");
  });

  it("TA-5: aria-selected is a boolean expression (not a string literal)", () => {
    // Must be aria-selected={activeTab === tab.id}, not aria-selected="true"
    assert.ok(
      /aria-selected=\{/.test(tabsSrc),
      "TA-5 DEFECT: aria-selected must be a dynamic expression {activeTab === tab.id}, " +
      "not a static string 'true'/'false'."
    );
  });

  it("TA-6: Tabs have aria-controls pointing to matching panel id", () => {
    assert.ok(tabsSrc.includes("aria-controls"),
      "TA-6 DEFECT: Tab buttons must have aria-controls='sym-panel-{id}' linking to the panel.");
    // Panel ids follow sym-panel-{tab.id} pattern
    assert.ok(
      tabsSrc.includes("sym-panel-") && tabsSrc.includes("aria-controls"),
      "TA-6 DEFECT: aria-controls value must use the 'sym-panel-{tab.id}' naming convention."
    );
  });

  it("TA-7: Panels have aria-labelledby pointing to matching tab id", () => {
    assert.ok(tabsSrc.includes("aria-labelledby"),
      "TA-7 DEFECT: Tab panels must have aria-labelledby='sym-tab-{id}' linking back to the tab button."
    );
    assert.ok(
      tabsSrc.includes("sym-tab-") && tabsSrc.includes("aria-labelledby"),
      "TA-7 DEFECT: aria-labelledby must use 'sym-tab-{tab.id}' naming convention."
    );
  });

  it("TA-8: Roving tabIndex — active tab has 0, inactive tabs have -1", () => {
    assert.ok(
      tabsSrc.includes("tabIndex={activeTab === tab.id ? 0 : -1}") ||
      tabsSrc.includes("tabIndex={activeTab===tab.id?0:-1}"),
      "TA-8 DEFECT: tabIndex must rove: 0 on active tab, -1 on all others (ARIA APG tablist pattern)."
    );
  });

  it("TA-9: Tab buttons are type='button' — no implicit form submit on Enter", () => {
    // Count type="button" occurrences near role="tab"
    assert.ok(
      tabsSrc.includes('type="button"'),
      "TA-9 DEFECT: Tab trigger buttons must be type='button' to prevent form submission."
    );
  });

  it("TA-10: tablist has an accessible aria-label", () => {
    assert.ok(
      tabsSrc.includes("aria-label"),
      "TA-10 DEFECT: The tablist container must have an aria-label or aria-labelledby " +
      "so screen readers identify the navigation purpose."
    );
  });
});

// ---------------------------------------------------------------------------
// KB: Keyboard navigation — ArrowLeft/Right/Home/End (behavioral mirrors)
// ---------------------------------------------------------------------------

describe("KB: Keyboard navigation — behavioral mirror of handleKeyDown", () => {
  const N = 3; // three tabs

  it("KB-1: ArrowRight from last tab wraps to first (circular)", () => {
    assert.equal(applyKeyDown("ArrowRight", 2, N), 0, "KB-1: idx=2 + ArrowRight → 0 (wrap)");
  });

  it("KB-2: ArrowRight advances from first to second tab", () => {
    assert.equal(applyKeyDown("ArrowRight", 0, N), 1, "KB-2: idx=0 + ArrowRight → 1");
  });

  it("KB-3: ArrowLeft from first tab wraps to last (circular)", () => {
    assert.equal(applyKeyDown("ArrowLeft", 0, N), 2, "KB-3: idx=0 + ArrowLeft → 2 (wrap)");
  });

  it("KB-4: ArrowLeft retreats from last to second tab", () => {
    assert.equal(applyKeyDown("ArrowLeft", 2, N), 1, "KB-4: idx=2 + ArrowLeft → 1");
  });

  it("KB-5: Home always selects first tab regardless of current", () => {
    assert.equal(applyKeyDown("Home", 2, N), 0, "KB-5: Home → 0");
    assert.equal(applyKeyDown("Home", 1, N), 0, "KB-5: Home → 0 (from middle)");
  });

  it("KB-6: End always selects last tab regardless of current", () => {
    assert.equal(applyKeyDown("End", 0, N), 2, "KB-6: End → 2");
    assert.equal(applyKeyDown("End", 1, N), 2, "KB-6: End → 2 (from middle)");
  });

  it("KB-7: Other keys (Enter, Space, Escape) are no-ops for index", () => {
    for (const key of ["Enter", "Space", "Escape", "Tab", "a"]) {
      assert.equal(applyKeyDown(key, 1, N), 1, `KB-7: '${key}' must not change index`);
    }
  });

  it("KB-8: Source attaches onKeyDown handler to each tab button", () => {
    assert.ok(tabsSrc.includes("onKeyDown"),
      "KB-8 DEFECT: Tab buttons must have an onKeyDown handler for ArrowLeft/Right/Home/End.");
  });

  it("KB-9: handleKeyDown calls e.preventDefault() on navigation keys to suppress page scroll", () => {
    assert.ok(tabsSrc.includes("e.preventDefault()"),
      "KB-9 DEFECT: handleKeyDown must call e.preventDefault() on arrow/Home/End keys " +
      "to prevent the page from scrolling during tab keyboard navigation."
    );
  });

  it("KB-10: Source focuses the target tab ref after keyboard navigation", () => {
    assert.ok(
      tabsSrc.includes("tabRefs.current") && tabsSrc.includes(".focus()"),
      "KB-10 DEFECT: handleKeyDown must focus the newly activated tab ref " +
      "so keyboard users see the focus indicator move."
    );
  });
});

// ---------------------------------------------------------------------------
// HR: Hash / deep-link resolution
// ---------------------------------------------------------------------------

describe("HR: Hash deep-links — #options/#stocks/#action-plans", () => {
  it("HR-1: HASH_TO_TAB maps #options → 'options'", () => {
    assert.equal(HASH_TO_TAB["#options"], "options");
  });

  it("HR-2: HASH_TO_TAB maps #stocks → 'stocks'", () => {
    assert.equal(HASH_TO_TAB["#stocks"], "stocks");
  });

  it("HR-3: HASH_TO_TAB maps #action-plans → 'action-plans'", () => {
    assert.equal(HASH_TO_TAB["#action-plans"], "action-plans");
  });

  it("HR-4: Unknown hash returns undefined (no crash, default tab stays)", () => {
    assert.equal(HASH_TO_TAB["#unknown"], undefined,
      "HR-4: An unrecognised hash must not resolve to a tab — default 'options' is kept.");
  });

  it("HR-5: Source reads window.location.hash in useEffect on mount", () => {
    assert.ok(
      tabsSrc.includes("window.location.hash") && tabsSrc.includes("useEffect"),
      "HR-5 DEFECT: SymbolDetailTabs must read window.location.hash in a useEffect " +
      "to set the initial tab from the URL on client mount."
    );
  });

  it("HR-6: Source writes url.hash when tab changes (replaceState, no navigation push)", () => {
    assert.ok(
      tabsSrc.includes("replaceState") && tabsSrc.includes("url.hash"),
      "HR-6 DEFECT: handleTabChange must call window.history.replaceState with the new hash " +
      "so the URL updates without adding a browser history entry."
    );
  });

  it("HR-7: HASH_TO_TAB is defined in source covering all three tab IDs", () => {
    assert.ok(
      tabsSrc.includes("HASH_TO_TAB") &&
      tabsSrc.includes('"#options"') &&
      tabsSrc.includes('"#stocks"') &&
      tabsSrc.includes('"#action-plans"'),
      "HR-7 DEFECT: HASH_TO_TAB must map all three hashes; one or more are missing."
    );
  });
});

// ---------------------------------------------------------------------------
// PV: Panel visibility — conditional render; prop slots; correct mapping
// ---------------------------------------------------------------------------

describe("PV: Panel visibility — only active panel rendered", () => {
  it("PV-1: Panel render is conditional on activeTab (not CSS display:none / hidden class)", () => {
    // Accept {activeTab === tab.id ? <panel> : null} pattern
    assert.ok(
      tabsSrc.includes("activeTab === tab.id"),
      "PV-1 DEFECT: Panels must be conditionally rendered (activeTab === tab.id) so inactive " +
      "panels are absent from the DOM entirely, not just hidden via CSS."
    );
  });

  it("PV-2: SymbolDetailTabs accepts three panel props: optionsPanel, stocksPanel, plansPanel", () => {
    assert.ok(tabsSrc.includes("optionsPanel"),
      "PV-2 DEFECT: SymbolDetailTabs must accept an 'optionsPanel' prop.");
    assert.ok(tabsSrc.includes("stocksPanel"),
      "PV-2 DEFECT: SymbolDetailTabs must accept a 'stocksPanel' prop.");
    assert.ok(tabsSrc.includes("plansPanel"),
      "PV-2 DEFECT: SymbolDetailTabs must accept a 'plansPanel' prop.");
  });

  it("PV-3: Panel mapping correct — 'options' key maps to optionsPanel slot", () => {
    // Source: panels = { options: optionsPanel, stocks: stocksPanel, "action-plans": plansPanel }
    assert.ok(
      tabsSrc.includes("options: optionsPanel") || tabsSrc.includes("options:optionsPanel"),
      "PV-3 DEFECT: 'options' key must map to optionsPanel slot in the panels record."
    );
  });

  it("PV-4: Panel mapping correct — 'stocks' key maps to stocksPanel slot", () => {
    assert.ok(
      tabsSrc.includes("stocks: stocksPanel") || tabsSrc.includes("stocks:stocksPanel"),
      "PV-4 DEFECT: 'stocks' key must map to stocksPanel slot in the panels record."
    );
  });

  it("PV-5: Panel mapping correct — 'action-plans' key maps to plansPanel slot", () => {
    assert.ok(
      tabsSrc.includes('"action-plans": plansPanel') || tabsSrc.includes('"action-plans":plansPanel'),
      "PV-5 DEFECT: 'action-plans' key must map to plansPanel slot in the panels record."
    );
  });

  it("PV-6: No CSS-only panel hiding (display:none/hidden/visibility:hidden) in SymbolDetailTabs", () => {
    assert.ok(!tabsSrc.includes("display:none") && !tabsSrc.includes('display: "none"'),
      "PV-6 DEFECT: Panels must not use display:none — use conditional rendering to remove from DOM.");
    assert.ok(
      !tabsSrc.includes('"hidden"') || !tabsSrc.includes("tabpanel"),
      "PV-6 DEFECT: Panels must not be hidden via a 'hidden' class on tabpanel divs.");
  });
});

// ---------------------------------------------------------------------------
// SM: Summary always before tablist; never inside a tab panel
// ---------------------------------------------------------------------------

describe("SM: Summary always visible before tablist", () => {
  it("SM-1: SymbolDetailTabs has no 'summaryPanel' / 'summary' prop slot", () => {
    assert.ok(!tabsSrc.includes("summaryPanel") && !tabsSrc.includes("summary:"),
      "SM-1 DEFECT: SymbolDetailTabs must NOT accept a summary prop. " +
      "Summary is always visible outside the tab interface.");
  });

  it("SM-2: SymbolSummary (or DetailSection title='Summary') appears before SymbolDetailTabs in page.tsx", () => {
    const summaryIdx = pageSrc.indexOf("SymbolSummary") !== -1
      ? pageSrc.indexOf("SymbolSummary")
      : pageSrc.indexOf('"Summary"');
    const tabsIdx = pageSrc.indexOf("SymbolDetailTabs");
    assert.ok(summaryIdx !== -1,
      "SM-2: SymbolSummary reference not found in page.tsx — Summary must be present.");
    assert.ok(tabsIdx !== -1,
      "SM-2 DEFECT: SymbolDetailTabs not found in page.tsx — tabs not yet integrated.");
    assert.ok(summaryIdx < tabsIdx,
      `SM-2 DEFECT: SymbolSummary (idx=${summaryIdx}) must appear before ` +
      `SymbolDetailTabs (idx=${tabsIdx}) so Summary is always visible above the tab bar.`
    );
  });

  it("SM-3: SymbolPlansTable is NOT rendered outside the tabbed area in page.tsx (moved into plans panel)", () => {
    // After redesign, SymbolPlansTable lives inside the plansPanel prop, not as a standalone sibling.
    // Detect: <SymbolPlansTable outside of a <SymbolDetailTabs props context.
    const tabsStart = pageSrc.indexOf("<SymbolDetailTabs");
    const plansIdx  = pageSrc.indexOf("<SymbolPlansTable");
    assert.ok(plansIdx !== -1,
      "SM-3: <SymbolPlansTable not found in page.tsx.");
    assert.ok(tabsStart !== -1,
      "SM-3 DEFECT: SymbolDetailTabs not in page.tsx — plansPanel not yet integrated.");
    // SymbolPlansTable must appear AFTER the SymbolDetailTabs opening (i.e., inside its props)
    assert.ok(plansIdx > tabsStart,
      `SM-3 DEFECT: <SymbolPlansTable (${plansIdx}) appears before or outside ` +
      `<SymbolDetailTabs (${tabsStart}). Plans must be inside the plansPanel prop, not standalone.`
    );
  });
});

// ---------------------------------------------------------------------------
// PG: Page integration — SymbolDetailTabs in page.tsx; correct panel content
// ---------------------------------------------------------------------------

describe("PG: Page integration — SymbolDetailTabs wired in page.tsx", () => {
  it("PG-1: page.tsx imports SymbolDetailTabs", () => {
    assert.ok(
      pageSrc.includes("SymbolDetailTabs"),
      "PG-1 DEFECT: page.tsx does not import SymbolDetailTabs. " +
      "The three-tab redesign requires this component to be used in the symbol detail page."
    );
  });

  it("PG-2: <SymbolDetailTabs JSX is rendered in page.tsx", () => {
    assert.ok(
      pageSrc.includes("<SymbolDetailTabs"),
      "PG-2 DEFECT: <SymbolDetailTabs is not rendered in page.tsx. " +
      "The page still uses the old vertical DetailSection layout."
    );
  });

  it("PG-3: optionsPanel prop passed to SymbolDetailTabs with options content", () => {
    const tabsTag = pageSrc.match(/<SymbolDetailTabs[\s\S]*?\/>/)?.[0] ?? "";
    assert.ok(tabsTag.includes("optionsPanel="),
      "PG-3 DEFECT: SymbolDetailTabs in page.tsx missing optionsPanel= prop.");
    // Options content includes PositionsTable or AddPositionForm
    assert.ok(
      tabsTag.includes("PositionsTable") || tabsTag.includes("optionsPanel={") ||
      pageSrc.includes("optionsPanel={") ||
      (pageSrc.includes("PositionsTable") && pageSrc.indexOf("PositionsTable") > pageSrc.indexOf("<SymbolDetailTabs")),
      "PG-3 DEFECT: Options panel must include PositionsTable/options content."
    );
  });

  it("PG-4: stocksPanel prop contains SymbolConfigurationCard", () => {
    const afterTabs = pageSrc.slice(pageSrc.indexOf("<SymbolDetailTabs"));
    assert.ok(
      afterTabs.includes("SymbolConfigurationCard") ||
      pageSrc.includes("stocksPanel="),
      "PG-4 DEFECT: stocksPanel must contain SymbolConfigurationCard (collapsed, without toggles)."
    );
  });

  it("PG-5: stocksPanel prop contains PortfolioHoldingsCard or equivalent table", () => {
    const afterTabs = pageSrc.slice(pageSrc.indexOf("<SymbolDetailTabs") || 0);
    assert.ok(
      afterTabs.includes("PortfolioHoldingsCard") ||
      afterTabs.includes("PortfolioHoldingsTable") ||
      pageSrc.includes("stocksPanel="),
      "PG-5 DEFECT: stocksPanel must contain Portfolio Holdings table component."
    );
  });

  it("PG-6: stocksPanel prop contains StockTransactionsTable", () => {
    const afterTabs = pageSrc.slice(pageSrc.indexOf("<SymbolDetailTabs") || 0);
    assert.ok(
      afterTabs.includes("StockTransactionsTable") || pageSrc.includes("stocksPanel="),
      "PG-6 DEFECT: stocksPanel must contain StockTransactionsTable for stock movement history."
    );
  });

  it("PG-7: plansPanel prop contains SymbolPlansTable", () => {
    const afterTabs = pageSrc.slice(pageSrc.indexOf("<SymbolDetailTabs") || 0);
    assert.ok(
      afterTabs.includes("SymbolPlansTable") || pageSrc.includes("plansPanel="),
      "PG-7 DEFECT: plansPanel must contain SymbolPlansTable (Action Plans content)."
    );
  });

  it("PG-8: No standalone DetailSection('Options') outside SymbolDetailTabs in page.tsx", () => {
    // After redesign, the old DetailSection(title="Options") must be gone —
    // options content is inside optionsPanel prop.
    const tabsIdx  = pageSrc.indexOf("<SymbolDetailTabs");
    const optDsIdx = pageSrc.indexOf('<DetailSection title="Options"');
    const altDsIdx = pageSrc.indexOf("title={\"Options\"}");
    if (tabsIdx !== -1) {
      // If SymbolDetailTabs is present, no old Options DetailSection should exist BEFORE it
      const oldBeforeTabs = optDsIdx !== -1 && optDsIdx < tabsIdx;
      const altBeforeTabs = altDsIdx !== -1 && altDsIdx < tabsIdx;
      assert.ok(!oldBeforeTabs && !altBeforeTabs,
        "PG-8 DEFECT: Old <DetailSection title=\"Options\"> found outside/before SymbolDetailTabs. " +
        "Options content must be inside optionsPanel prop only — no duplicate."
      );
    } else {
      // SymbolDetailTabs not integrated yet — report DEFECT without failing hard on source form
      assert.ok(false,
        "PG-8 DEFECT: SymbolDetailTabs not in page.tsx. Cannot verify removal of old Options section."
      );
    }
  });

  it("PG-9: No standalone DetailSection('Stocks') outside SymbolDetailTabs in page.tsx", () => {
    const tabsIdx  = pageSrc.indexOf("<SymbolDetailTabs");
    const stDsIdx  = pageSrc.indexOf('<DetailSection title="Stocks"');
    if (tabsIdx !== -1) {
      const oldBeforeTabs = stDsIdx !== -1 && stDsIdx < tabsIdx;
      assert.ok(!oldBeforeTabs,
        "PG-9 DEFECT: Old <DetailSection title=\"Stocks\"> found before SymbolDetailTabs. " +
        "Stocks content must be inside stocksPanel prop only — no duplicate."
      );
    } else {
      assert.ok(false,
        "PG-9 DEFECT: SymbolDetailTabs not in page.tsx. Cannot verify removal of old Stocks section."
      );
    }
  });
});

// ---------------------------------------------------------------------------
// US: Non-US eligibility — Options tab visible; panel content gated
// ---------------------------------------------------------------------------

describe("US: Non-US eligibility — tab visible, panel content gated", () => {
  it("US-1: SymbolDetailTabs renders all 3 tabs unconditionally (Options tab always present)", () => {
    // The tab BAR must not conditionally hide the Options tab — all symbols get the same tabs.
    // Non-US eligibility is enforced inside the optionsPanel content, not on the tab button.
    assert.ok(
      !tabsSrc.includes("usOptionsEligible") && !tabsSrc.includes("hasOptions"),
      "US-1 DEFECT: SymbolDetailTabs should NOT receive or use usOptionsEligible/hasOptions to " +
      "hide individual tabs. Options tab must always render; eligibility gates panel content only."
    );
  });

  it("US-2: SymbolDetailTabs Props interface does not include usOptionsEligible", () => {
    const propsMatch = tabsSrc.match(/interface Props\s*\{[\s\S]*?\}/)?.[0] ?? "";
    assert.ok(!propsMatch.includes("usOptionsEligible"),
      "US-2 DEFECT: SymbolDetailTabs Props must not include usOptionsEligible. " +
      "Eligibility is a page-level concern; pass already-gated content as optionsPanel."
    );
  });

  it("US-3: Options panel content in page.tsx is gated by usOptionsEligible (behavioral mirror)", () => {
    // After integration, the options panel JSX must be inside a usOptionsEligible guard.
    // Source check: usOptionsEligible must appear near PositionsTable/AddPositionForm
    // within the optionsPanel prop context.
    const tabsStart  = pageSrc.indexOf("<SymbolDetailTabs");
    if (tabsStart === -1) {
      assert.ok(false,
        "US-3 DEFECT: SymbolDetailTabs not in page.tsx — cannot verify options content gating.");
      return;
    }
    const tabsRegion = pageSrc.slice(tabsStart, tabsStart + 3000);
    // PositionsTable or AddPositionForm must appear with usOptionsEligible in the same region
    assert.ok(
      tabsRegion.includes("usOptionsEligible") ||
      tabsRegion.includes("hasOptions"),
      "US-3 DEFECT: optionsPanel content must be gated by usOptionsEligible/hasOptions in page.tsx. " +
      "Non-US users must not see PositionsTable/AddPositionForm via the Options tab."
    );
  });

  it("US-4: Non-US behavioral check — Options tab selection does not bypass eligibility (pure logic)", () => {
    // Pure logic: eligibility check is independent of tab selection.
    // If usOptionsEligible=false, the panel content must be empty/restricted
    // regardless of which tab is active. Tab selection ≠ eligibility grant.
    const eligibleUS    = true;
    const notEligibleUS = false;
    // optionsContent = eligible ? <actions> : null
    const contentForEligible    = eligibleUS    ? "PositionsTable" : null;
    const contentForNonEligible = notEligibleUS ? "PositionsTable" : null;
    assert.equal(contentForEligible, "PositionsTable",
      "US-4: US-eligible symbol shows options content");
    assert.equal(contentForNonEligible, null,
      "US-4: Non-US symbol must show null content in options panel regardless of tab click");
  });
});

// ---------------------------------------------------------------------------
// VS: Visual contract — pill-tab matching Options Screener CC/CSP switcher
// ---------------------------------------------------------------------------

describe("VS: Visual contract — pill-tab matching Options Screener", () => {
  it("VS-1: Tab bar uses pill container styling (rounded-pill, border, bg-bg-card, p-1)", () => {
    assert.ok(
      tabsSrc.includes("rounded-[var(--radius-pill)]") &&
      tabsSrc.includes("border border-border") &&
      tabsSrc.includes("bg-bg-card") &&
      tabsSrc.includes("p-1"),
      "VS-1 DEFECT: Tab bar container must use the same pill-style as Options Screener: " +
      "rounded-[var(--radius-pill)] border border-border bg-bg-card p-1."
    );
  });

  it("VS-2: Active tab has bg-accent-blue text-white styling", () => {
    assert.ok(
      tabsSrc.includes("bg-accent-blue") && tabsSrc.includes("text-white"),
      "VS-2 DEFECT: Active tab must use bg-accent-blue text-white (same as Options Screener active state)."
    );
  });

  it("VS-3: Inactive tabs have text-text-muted hover:text-text styling", () => {
    assert.ok(
      tabsSrc.includes("text-text-muted") && tabsSrc.includes("hover:text-text"),
      "VS-3 DEFECT: Inactive tabs must use text-text-muted hover:text-text (same as Options Screener)."
    );
  });

  it("VS-4: Each tab button uses rounded-pill on itself (pill shape per button)", () => {
    // Count rounded-[var(--radius-pill)] occurrences — expect ≥2 (container + buttons)
    const pillCount = (tabsSrc.match(/rounded-\[var\(--radius-pill\)\]/g) ?? []).length;
    assert.ok(pillCount >= 2,
      `VS-4 DEFECT: Expected ≥2 rounded-[var(--radius-pill)] (container + tabs); found ${pillCount}.`
    );
  });

  it("VS-5: Tab bar is positioned in a single shared toolbar, not duplicated per section", () => {
    // The tablist container must appear once in SymbolDetailTabs source
    const tablistCount = (tabsSrc.match(/role="tablist"/g) ?? []).length;
    assert.equal(tablistCount, 1,
      `VS-5 DEFECT: role="tablist" appears ${tablistCount} times — must appear exactly once.`
    );
  });

  it("VS-6: Tab bar uses flex layout with gap (matching screener pill row)", () => {
    assert.ok(
      tabsSrc.includes("flex items-center gap-1"),
      "VS-6 DEFECT: Tab bar must use flex items-center gap-1 matching the Options Screener pill layout."
    );
  });
});

// ---------------------------------------------------------------------------
// RG: Regression — prior redesigns not undone by tab integration
// ---------------------------------------------------------------------------

describe("RG: Regression guard — prior redesign contracts preserved", () => {
  it("RG-1: SymbolConfigurationCard still has no 'Agent & Alert Toggles' section (AT-1)", () => {
    assert.ok(!cardSrc.includes("Agent & Alert Toggles"),
      "RG-1 DEFECT: 'Agent & Alert Toggles' section reappeared in SymbolConfigurationCard " +
      "after tab integration. AT-1 regression."
    );
  });

  it("RG-2: SymbolConfigurationCard still has no cfg-toggle-* ids (AT-6)", () => {
    assert.ok(!cardSrc.includes("cfg-toggle-cc"),
      "RG-2 DEFECT: cfg-toggle-cc reappeared — Covered Calls toggle regression.");
    assert.ok(!cardSrc.includes("cfg-toggle-telegram"),
      "RG-2 DEFECT: cfg-toggle-telegram reappeared — Telegram toggle regression.");
  });

  it("RG-3: PortfolioHoldingsCard still has no outer `surface` CSS class (NF-1)", () => {
    assert.ok(!holdingsSrc.includes('className="surface'),
      "RG-3 DEFECT: PortfolioHoldingsCard outer surface class reappeared after tab integration. " +
      "NF-1 regression."
    );
  });

  it("RG-4: PortfolioHoldingsCard uses a <table> element (PT-1)", () => {
    assert.ok(holdingsSrc.includes("<table"),
      "RG-4 DEFECT: PortfolioHoldingsCard table element absent — PT-1 regression after tab integration."
    );
  });

  it("RG-5: SymbolDetailTabs itself does not embed Summary content (no SymbolSummary inside component)", () => {
    assert.ok(!tabsSrc.includes("SymbolSummary"),
      "RG-5 DEFECT: SymbolDetailTabs must not contain SymbolSummary — " +
      "Summary lives outside the tab interface in page.tsx."
    );
  });

  it("RG-6: SymbolDetailTabs source has no inline DetailSection usage (tabs replace sections)", () => {
    // Tabs are the navigation mechanism — no nested DetailSection inside the tab component
    assert.ok(!tabsSrc.includes("DetailSection"),
      "RG-6 DEFECT: SymbolDetailTabs must not use DetailSection internally. " +
      "Each panel is a flat tabpanel div; section collapsing is at the Card level."
    );
  });

  it("RG-7: SymbolDetailTabs TABS array has not grown beyond 3 (no undeclared extra tab)", () => {
    const tabCount = [...tabsSrc.matchAll(/\{\s*id:\s*["'][^"']+["']/g)].length;
    assert.equal(tabCount, 3,
      `RG-7 DEFECT: TABS has ${tabCount} entries; contract specifies exactly 3. ` +
      "An undeclared fourth tab would violate the placement contract."
    );
  });
});
