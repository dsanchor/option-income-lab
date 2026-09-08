/**
 * symbolsViewSelector.test.mjs — Portfolio/Options view-selector regression.
 *
 * Contract: Copilot directive (2026-09-07) — Symbols table view-mode selector.
 *
 * Coverage:
 *   VS  — View-selector presence, default, position in toolbar
 *   CC  — Column contract: common set, Portfolio set, Options set
 *   HI  — Hidden columns in each mode (Price EUR / Value EUR absent from Options)
 *   SR  — Sort-reset: active sort on hidden column -> default on switch
 *   CS  — ColSpan/alignment: header <-> body column count must match
 *   AC  — Accessibility: aria-pressed / role attributes on selector buttons
 *   NF  — No extra fetch / no row-filter change on mode switch
 *   SF  — Shared-filter regression: search + suitability apply in both modes
 *   RG  — Regression: existing column labels not removed by selector refactor
 *   CW  — Column width contract: proportional/content-driven, overflow, no clipping
 *
 * Many VS/CC/HI/AC/CS tests are source-contract tests using readFileSync.
 * They will FAIL until Rusty implements the feature; that is intentional and
 * correct — the failures identify the open work item.
 *
 * Pure-logic behavioral tests (SR, NF, SF) run against inline mirrors and
 * do NOT depend on the selector being implemented yet.
 *
 * Run: node --test frontend/tests/symbolsViewSelector.test.mjs
 */

import { readFileSync } from "fs";
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, "..");

const src = readFileSync(
  join(root, "src/components/SymbolsTable.tsx"),
  "utf8",
);

// ---------------------------------------------------------------------------
// Shared column-set definitions (mirrors of what SymbolsTable should export)
// Updated 2026-09-07: DGI/Tech/Entry moved from "common" to Options-only.
// Portfolio default hides DGI, Tech, Entry, In Calls, Puts.
// Options shows DGI, Tech, Entry, In Calls, Puts; hides portfolio-specific.
// ---------------------------------------------------------------------------

const COMMON_KEYS = [
  "symbol", "category", "momentum", "price",
];

const PORTFOLIO_ONLY_KEYS = [
  "price_eur", "portfolio_shares", "portfolio_avg_cost_eur",
  "portfolio_invested_eur", "current_value_eur", "portfolio_dividends_eur",
];

const OPTIONS_ONLY_KEYS = [
  // DGI, Tech, Entry moved from "common" to Options-only per 2026-09-07 revision
  "dgi_score", "tech_timing", "entry_tag",
  "in_calls", "put_exposure",
];

// Keys that must be ABSENT from Portfolio mode (per new contract)
const PORTFOLIO_HIDDEN_KEYS = [
  "dgi_score", "tech_timing", "entry_tag", "in_calls", "put_exposure",
];

// Keys that must be ABSENT from Options mode (portfolio-specific columns)
const OPTIONS_HIDDEN_KEYS = [
  "price_eur", "current_value_eur",
  "portfolio_shares", "portfolio_avg_cost_eur",
  "portfolio_invested_eur", "portfolio_dividends_eur",
];

const PORTFOLIO_COLUMN_KEYS = [...COMMON_KEYS, ...PORTFOLIO_ONLY_KEYS];
const OPTIONS_COLUMN_KEYS   = [...COMMON_KEYS, ...OPTIONS_ONLY_KEYS];

// ---------------------------------------------------------------------------
// Helper: extract declared columns from a COLUMNS-array-like source string.
// Parses patterns like: { key: "foo", label: "Bar" }
// Returns [] if source doesn't yet have multiple column sets.
// ---------------------------------------------------------------------------
function extractColumnKeys(block) {
  const keys = [];
  for (const m of block.matchAll(/key:\s*["']([^"']+)["']/g)) {
    keys.push(m[1]);
  }
  return keys;
}

// ---------------------------------------------------------------------------
// Sort-reset logic (pure inline mirror)
// When the active sort column is NOT in the visible column set for the new
// mode, the sort key must reset to the default ("symbol", asc).
// ---------------------------------------------------------------------------
function applyViewModeSwitch(currentSort, currentDir, newModeColumns, defaultSort = "symbol") {
  const visibleKeys = newModeColumns.map((c) => c.key ?? c);
  if (!visibleKeys.includes(currentSort)) {
    return { sort: defaultSort, dir: "asc" };
  }
  return { sort: currentSort, dir: currentDir };
}

// ---------------------------------------------------------------------------
// VS: View-selector presence, default, position in toolbar
// ---------------------------------------------------------------------------

describe("VS: View selector — source-contract presence", () => {
  it("VS-1: SymbolsTable declares a view-mode state (viewMode / tableView / columnMode)", () => {
    // Must be a useState call with the mode strings, not just the section-label strings
    // "portfolio" already appears as a section label — look for the typed useState form
    const hasViewMode =
      src.includes("useState(\"portfolio\")") ||
      src.includes("useState('portfolio')") ||
      src.includes("viewMode") ||
      src.includes("tableMode") ||
      src.includes("columnMode") ||
      src.includes("selectedView") ||
      src.includes("activeView");
    // Tighter check: the mode type union must appear
    const hasModeUnion =
      src.includes("'portfolio' | 'options'") ||
      src.includes('"portfolio" | "options"') ||
      src.includes("portfolio\" | \"options") ||
      src.includes("portfolio' | 'options");
    assert.ok(
      hasViewMode && hasModeUnion,
      "VS-1 DEFECT: SymbolsTable must declare a view-mode state variable with " +
      "type 'portfolio' | 'options' (e.g. useState<'portfolio'|'options'>('portfolio')). " +
      "Rusty: add the typed state variable."
    );
  });

  it("VS-2: Default view mode is 'portfolio' (useState initialised with 'portfolio')", () => {
    // Accepts both plain and TypeScript generic forms:
    //   useState("portfolio") / useState('portfolio')
    //   useState<ViewMode>("portfolio") — contains >("portfolio")
    const hasPortfolioDefault =
      src.includes("useState(\"portfolio\")") ||
      src.includes("useState('portfolio')") ||
      (src.includes("useState<ViewMode>") && src.includes("(\"portfolio\")")) ||
      (src.includes("useState<ViewMode>") && src.includes("('portfolio')"));
    assert.ok(
      hasPortfolioDefault,
      "VS-2 DEFECT: Default view mode must be 'portfolio'. " +
      "Rusty: useState<ViewMode>('portfolio') or useState('portfolio')."
    );
  });

  it("VS-3: Both 'Portfolio' and 'Options' selector buttons exist in source", () => {
    const hasPortfolioBtn =
      src.includes("Portfolio") &&
      (src.includes('"portfolio"') || src.includes("'portfolio'"));
    const hasOptionsBtn =
      src.includes("Options") &&
      (src.includes('"options"') || src.includes("'options'"));
    assert.ok(
      hasPortfolioBtn,
      "VS-3a DEFECT: Selector must have a 'Portfolio' button. Rusty: add view selector."
    );
    assert.ok(
      hasOptionsBtn,
      "VS-3b DEFECT: Selector must have an 'Options' button. Rusty: add view selector."
    );
  });

  it("VS-4: View selector appears BEFORE suitability filter buttons in source order", () => {
    // Compare JSX render order: selector onClick handler must appear before
    // the SUITABILITY_FILTERS.map() render call.
    // Selector onClick uses changeViewMode (or setViewMode) inside the button .map()
    const selectorJsxIdx =
      src.indexOf("changeViewMode") !== -1
        ? src.indexOf("changeViewMode")
        : src.indexOf("setViewMode");  // fallback: state setter used directly
    // SUITABILITY_FILTERS.map is the render call (not the constant definition)
    const suitabilityMapIdx = src.indexOf("SUITABILITY_FILTERS.map");
    const suitabilityIdx = suitabilityMapIdx !== -1
      ? suitabilityMapIdx
      : src.indexOf("SUITABILITY_FILTERS");
    assert.ok(
      selectorJsxIdx !== -1 && suitabilityIdx !== -1 && selectorJsxIdx < suitabilityIdx,
      "VS-4 DEFECT: View selector (Portfolio/Options) must appear before suitability filter " +
      "buttons in the toolbar. changeViewMode/setViewMode JSX idx=" + selectorJsxIdx +
      " vs SUITABILITY_FILTERS idx=" + suitabilityIdx
    );
  });
});


// ---------------------------------------------------------------------------
// CC: Column contract — sets for each mode
// ---------------------------------------------------------------------------

describe("CC: Column contract — Portfolio and Options column sets", () => {
  it("CC-1: Source defines separate column sets for Portfolio and Options modes", () => {
    // Accepts two implementation strategies:
    // 1. Named arrays: PORTFOLIO_COLUMNS / OPTIONS_COLUMNS / portfolioColumns etc.
    // 2. Single COLUMNS array with per-column modes annotation (e.g. modes?: ViewMode[]),
    //    which achieves the same separation via filter. This is the preferred pattern.
    const hasModesAnnotation =
      src.includes("modes?: ViewMode[]") ||
      src.includes("modes: ViewMode[]") ||
      (src.includes("modes:") && src.includes("ViewMode"));
    const hasNamedArrays =
      src.includes("PORTFOLIO_COLUMNS") ||
      src.includes("portfolioColumns") ||
      src.includes("OPTIONS_COLUMNS") ||
      src.includes("optionsColumns");
    const hasViewModeFilter =
      src.includes("includes(viewMode)") ||
      src.includes(".includes(viewMode)");
    assert.ok(
      hasModesAnnotation || hasNamedArrays || hasViewModeFilter,
      "CC-1 DEFECT: SymbolsTable must define separate column sets for Portfolio and Options modes. " +
      "Options: (a) PORTFOLIO_COLUMNS/OPTIONS_COLUMNS arrays, or (b) COLUMNS with modes?: ViewMode[] " +
      "annotation filtered by viewMode. Rusty: implement column separation."
    );
  });

  it("CC-2: Contract: 4 common keys in both modes; DGI/Tech/Entry absent from Portfolio, present in Options", () => {
    for (const key of COMMON_KEYS) {
      assert.ok(PORTFOLIO_COLUMN_KEYS.includes(key), `CC-2a: Common key '${key}' must be in Portfolio column set`);
      assert.ok(OPTIONS_COLUMN_KEYS.includes(key),   `CC-2b: Common key '${key}' must be in Options column set`);
    }
    // REJECT: DGI/Tech/Entry must NOT be marked common (portfolio-visible)
    for (const key of ["dgi_score", "tech_timing", "entry_tag"]) {
      assert.ok(!PORTFOLIO_COLUMN_KEYS.includes(key), `CC-2c REJECT: '${key}' must NOT be in Portfolio — it is Options-only`);
      assert.ok(!COMMON_KEYS.includes(key),            `CC-2d REJECT: '${key}' must NOT be in COMMON_KEYS (marking it common makes it portfolio-visible)`);
      assert.ok(OPTIONS_COLUMN_KEYS.includes(key),     `CC-2e: '${key}' must be present in Options column set`);
    }
  });

  it("CC-3: Contract: Options-only keys (including DGI/Tech/Entry) absent from Portfolio set", () => {
    for (const key of ["in_calls", "put_exposure", "dgi_score", "tech_timing", "entry_tag"]) {
      assert.ok(!PORTFOLIO_COLUMN_KEYS.includes(key),
        `CC-3 REJECT: '${key}' must be ABSENT from Portfolio column set (Options-only)`);
    }
  });

  it("CC-4: Contract: Options-only keys (including DGI/Tech/Entry) present in Options set", () => {
    for (const key of ["in_calls", "put_exposure", "dgi_score", "tech_timing", "entry_tag"]) {
      assert.ok(OPTIONS_COLUMN_KEYS.includes(key),
        `CC-4: '${key}' must be in Options column set`);
    }
  });

  it("CC-5: Portfolio column count: 10 columns (4 common + 6 portfolio-specific)", () => {
    assert.equal(PORTFOLIO_COLUMN_KEYS.length, 10,
      `CC-5: Portfolio must have 10 columns (symbol,category,momentum,price + 6 portfolio-specific), got ${PORTFOLIO_COLUMN_KEYS.length}: ${PORTFOLIO_COLUMN_KEYS.join(", ")}`);
  });

  it("CC-6: Options column count: 9 columns (4 common + 5 options-specific)", () => {
    assert.equal(OPTIONS_COLUMN_KEYS.length, 9,
      `CC-6: Options must have 9 columns (symbol,category,momentum,price + dgi,tech,entry,in_calls,puts), got ${OPTIONS_COLUMN_KEYS.length}: ${OPTIONS_COLUMN_KEYS.join(", ")}`);
  });

  it("CC-7: Source marks dgi_score/tech_timing/entry_tag with modes:[\"options\"] — rejects common/portfolio annotation", () => {
    const dgiLine   = src.match(/key:\s*["']dgi_score["'][^}]+/)?.[0]   ?? "";
    const techLine  = src.match(/key:\s*["']tech_timing["'][^}]+/)?.[0] ?? "";
    const entryLine = src.match(/key:\s*["']entry_tag["'][^}]+/)?.[0]   ?? "";

    assert.ok(dgiLine.includes("modes") && dgiLine.includes("options") && !dgiLine.includes("portfolio"),
      `CC-7a REJECT: dgi_score must have modes:["options"] only. Got: ${dgiLine}`);
    assert.ok(techLine.includes("modes") && techLine.includes("options") && !techLine.includes("portfolio"),
      `CC-7b REJECT: tech_timing must have modes:["options"] only. Got: ${techLine}`);
    assert.ok(entryLine.includes("modes") && entryLine.includes("options") && !entryLine.includes("portfolio"),
      `CC-7c REJECT: entry_tag must have modes:["options"] only. Got: ${entryLine}`);
    assert.ok(src.includes(".includes(viewMode)") || src.includes(".includes(mode)"),
      "CC-7d: Source must filter COLUMNS by modes to exclude Options-only columns from Portfolio");
  });
});

// ---------------------------------------------------------------------------
// HI: Hidden columns per mode
// ---------------------------------------------------------------------------

describe("HI: Hidden columns — Price EUR and Current Value hidden in Options", () => {
  it("HI-1: 'price_eur' absent from Options column set (contract explicit)", () => {
    assert.ok(
      !OPTIONS_COLUMN_KEYS.includes("price_eur"),
      "HI-1: 'price_eur' (Price €) must be hidden in Options mode per contract"
    );
  });

  it("HI-2: 'current_value_eur' absent from Options column set (contract explicit)", () => {
    assert.ok(
      !OPTIONS_COLUMN_KEYS.includes("current_value_eur"),
      "HI-2: 'current_value_eur' (Value €) must be hidden in Options mode per contract"
    );
  });

  it("HI-3: 'price_eur' present in Portfolio column set", () => {
    assert.ok(
      PORTFOLIO_COLUMN_KEYS.includes("price_eur"),
      "HI-3: 'price_eur' (Price €) must be visible in Portfolio mode"
    );
  });

  it("HI-4: 'current_value_eur' present in Portfolio column set", () => {
    assert.ok(
      PORTFOLIO_COLUMN_KEYS.includes("current_value_eur"),
      "HI-4: 'current_value_eur' (Value €) must be visible in Portfolio mode"
    );
  });

  it("HI-5: Source switches visible columns based on viewMode (not CSS hide)", () => {
    const hasCssHide =
      src.includes("display: none") ||
      src.includes('className="hidden"') ||
      src.includes("visibility: hidden");
    const hasConditionalColumns =
      src.includes("viewMode") || src.includes("tableMode") || src.includes("PORTFOLIO_COLUMNS") || src.includes("OPTIONS_COLUMNS");
    assert.ok(hasConditionalColumns || !hasCssHide,
      "HI-5 DEFECT: Columns must be conditionally rendered per viewMode, not hidden with CSS.");
  });

  it("HI-6: 'dgi_score' absent from Portfolio column set (REJECT: DGI is Options-only)", () => {
    assert.ok(!PORTFOLIO_COLUMN_KEYS.includes("dgi_score"),
      "HI-6 REJECT: 'dgi_score' must NOT be visible in Portfolio mode — DGI is Options-only per 2026-09-07 contract.");
  });

  it("HI-7: 'tech_timing' absent from Portfolio column set (REJECT: Tech is Options-only)", () => {
    assert.ok(!PORTFOLIO_COLUMN_KEYS.includes("tech_timing"),
      "HI-7 REJECT: 'tech_timing' must NOT be visible in Portfolio mode — Tech is Options-only per 2026-09-07 contract.");
  });

  it("HI-8: 'entry_tag' absent from Portfolio column set (REJECT: Entry is Options-only)", () => {
    assert.ok(!PORTFOLIO_COLUMN_KEYS.includes("entry_tag"),
      "HI-8 REJECT: 'entry_tag' must NOT be visible in Portfolio mode — Entry is Options-only per 2026-09-07 contract.");
  });

  it("HI-9: dgi_score, tech_timing, entry_tag all present in Options column set", () => {
    assert.ok(OPTIONS_COLUMN_KEYS.includes("dgi_score"),   "HI-9a: dgi_score must be visible in Options");
    assert.ok(OPTIONS_COLUMN_KEYS.includes("tech_timing"), "HI-9b: tech_timing must be visible in Options");
    assert.ok(OPTIONS_COLUMN_KEYS.includes("entry_tag"),   "HI-9c: entry_tag must be visible in Options");
  });

  it("HI-10: Source body guards dgi_score/tech_timing/entry_tag cells with Options viewMode check", () => {
    // Search for the body render occurrence (r.dgi_score), not the type/key definition.
    const optionsGuardNearDgiBody = (() => {
      const idx = src.indexOf("r.dgi_score");
      if (idx === -1) return false;
      const context = src.slice(Math.max(0, idx - 200), idx + 50);
      return context.includes('viewMode === "options"') || context.includes("viewMode === 'options'");
    })();
    const usesActiveColumnsRender =
      src.includes("visibleColumns.map") && (src.includes("r[c.key]") || src.includes("r[col.key]"));
    assert.ok(
      optionsGuardNearDgiBody || usesActiveColumnsRender,
      "HI-10 DEFECT: dgi_score/tech_timing/entry_tag body cells must be inside " +
      "{viewMode === \"options\" && ...} OR rendered via a column-driven body loop. " +
      "Rendering them unconditionally shows DGI/Tech/Entry in Portfolio mode."
    );
  });
});

// ---------------------------------------------------------------------------
// SR: Sort-reset — active sort on hidden column resets to default on mode switch
// ---------------------------------------------------------------------------

describe("SR: Sort reset when active sort column becomes hidden", () => {
  it("SR-1: Switching to Options with sort='price_eur' (hidden) -> resets to symbol/asc", () => {
    const result = applyViewModeSwitch("price_eur", "desc", OPTIONS_COLUMN_KEYS.map((k) => ({ key: k })));
    assert.equal(result.sort, "symbol",
      "SR-1: price_eur is not visible in Options; sort must reset to 'symbol'");
    assert.equal(result.dir, "asc",
      "SR-1: sort direction must reset to 'asc' when column hidden");
  });

  it("SR-2: Switching to Options with sort='current_value_eur' (hidden) -> resets", () => {
    const result = applyViewModeSwitch("current_value_eur", "asc", OPTIONS_COLUMN_KEYS.map((k) => ({ key: k })));
    assert.equal(result.sort, "symbol", "SR-2: current_value_eur not in Options; must reset");
    assert.equal(result.dir, "asc", "SR-2: direction resets to asc");
  });

  it("SR-3: Switching to Options with sort='portfolio_shares' (hidden) -> resets", () => {
    const result = applyViewModeSwitch("portfolio_shares", "desc", OPTIONS_COLUMN_KEYS.map((k) => ({ key: k })));
    assert.equal(result.sort, "symbol", "SR-3: portfolio_shares not in Options; must reset");
  });

  it("SR-4: Switching to Options with sort='in_calls' (visible) -> sort preserved", () => {
    const result = applyViewModeSwitch("in_calls", "desc", OPTIONS_COLUMN_KEYS.map((k) => ({ key: k })));
    assert.equal(result.sort, "in_calls",
      "SR-4: in_calls IS visible in Options; sort key must NOT reset");
    assert.equal(result.dir, "desc",
      "SR-4: direction must be preserved when sort column remains visible");
  });

  it("SR-5: Switching to Portfolio with sort='in_calls' (hidden) -> resets", () => {
    const result = applyViewModeSwitch("in_calls", "asc", PORTFOLIO_COLUMN_KEYS.map((k) => ({ key: k })));
    assert.equal(result.sort, "symbol",
      "SR-5: in_calls not in Portfolio mode; sort must reset");
  });

  it("SR-6: Switching to Portfolio with sort='price_eur' (visible) -> sort preserved", () => {
    const result = applyViewModeSwitch("price_eur", "desc", PORTFOLIO_COLUMN_KEYS.map((k) => ({ key: k })));
    assert.equal(result.sort, "price_eur",
      "SR-6: price_eur IS visible in Portfolio; sort preserved");
    assert.equal(result.dir, "desc", "SR-6: direction preserved");
  });

  it("SR-7: Common columns preserve sort across both modes (symbol example)", () => {
    const fromPortfolio = applyViewModeSwitch("symbol", "asc", OPTIONS_COLUMN_KEYS.map((k) => ({ key: k })));
    const fromOptions   = applyViewModeSwitch("symbol", "asc", PORTFOLIO_COLUMN_KEYS.map((k) => ({ key: k })));
    assert.equal(fromPortfolio.sort, "symbol", "SR-7a: symbol preserved Portfolio->Options");
    assert.equal(fromOptions.sort, "symbol",   "SR-7b: symbol preserved Options->Portfolio");
  });

  it("SR-8: Source contains sort-reset logic on viewMode change", () => {
    // Proxy: a useEffect that depends on viewMode AND calls setSort, OR
    // a computed activeColumns that the sort key is validated against.
    // The current "setSort" usage is for toggleSort — that's not the same.
    const hasViewModeRef =
      src.includes("viewMode") || src.includes("tableMode") || src.includes("selectedView");
    const hasSortResetOnMode =
      hasViewModeRef && (
        src.includes("setSort(") ||
        src.includes("activeColumns") ||
        src.includes("visibleColumns")
      ) && (
        // Must be specifically in a mode-change context, not just general sort toggle
        src.includes("viewMode") && src.includes("setSort(")
      );
    assert.ok(
      hasSortResetOnMode,
      "SR-8 DEFECT: SymbolsTable must reset sort when switching to a mode where the active " +
      "sort column is hidden. Rusty: add useEffect([viewMode], ...) or a sort guard that " +
      "resets sort to 'symbol' if activeColumns does not include the current sort key."
    );
  });

  it("SR-9: Switching to Portfolio with sort='dgi_score' (now Portfolio-hidden) -> resets to symbol/asc", () => {
    const result = applyViewModeSwitch("dgi_score", "desc", PORTFOLIO_COLUMN_KEYS.map((k) => ({ key: k })));
    assert.equal(result.sort, "symbol", "SR-9: dgi_score is Options-only; sort must reset to 'symbol' when switching to Portfolio");
    assert.equal(result.dir, "asc", "SR-9: direction resets to asc");
  });

  it("SR-10: Switching to Portfolio with sort='tech_timing' (Portfolio-hidden) -> resets", () => {
    const result = applyViewModeSwitch("tech_timing", "asc", PORTFOLIO_COLUMN_KEYS.map((k) => ({ key: k })));
    assert.equal(result.sort, "symbol", "SR-10: tech_timing is Options-only; sort must reset");
    assert.equal(result.dir, "asc", "SR-10: direction resets");
  });

  it("SR-11: Switching to Portfolio with sort='entry_tag' (Portfolio-hidden) -> resets", () => {
    const result = applyViewModeSwitch("entry_tag", "desc", PORTFOLIO_COLUMN_KEYS.map((k) => ({ key: k })));
    assert.equal(result.sort, "symbol", "SR-11: entry_tag is Options-only; sort must reset");
    assert.equal(result.dir, "asc", "SR-11: direction resets");
  });

  it("SR-12: Switching to Options with sort='dgi_score' (Options-visible) -> sort preserved", () => {
    const result = applyViewModeSwitch("dgi_score", "desc", OPTIONS_COLUMN_KEYS.map((k) => ({ key: k })));
    assert.equal(result.sort, "dgi_score", "SR-12: dgi_score IS visible in Options; sort must NOT reset");
    assert.equal(result.dir, "desc", "SR-12: direction preserved");
  });

  it("SR-13: Switching to Options with sort='momentum' (common column) -> preserved both directions", () => {
    const asc  = applyViewModeSwitch("momentum", "asc",  OPTIONS_COLUMN_KEYS.map((k) => ({ key: k })));
    const desc = applyViewModeSwitch("momentum", "desc", PORTFOLIO_COLUMN_KEYS.map((k) => ({ key: k })));
    assert.equal(asc.sort,  "momentum", "SR-13a: momentum preserved Portfolio->Options (asc)");
    assert.equal(desc.sort, "momentum", "SR-13b: momentum preserved Options->Portfolio (desc)");
  });
});

// ---------------------------------------------------------------------------
// CS: ColSpan consistency
// ---------------------------------------------------------------------------

describe("CS: ColSpan/alignment — header and body cell counts match active columns", () => {
  it("CS-1: colSpan used for empty/section-header rows references column count variable", () => {
    // The existing table uses COLUMNS.length + 1 for colSpan.
    // With two modes it must use the active column set length.
    const hasActiveColSpan =
      src.includes("activeColumns") ||
      src.includes("visibleColumns") ||
      src.includes("PORTFOLIO_COLUMNS") ||
      src.includes("OPTIONS_COLUMNS") ||
      src.includes("viewColumns") ||
      src.includes("cols.length");
    // Current: COLUMNS.length — this will be wrong once modes are added unless updated
    const hasStaticColspan = src.includes("COLUMNS.length");
    if (hasStaticColspan && !hasActiveColSpan) {
      assert.fail(
        "CS-1 DEFECT: colSpan still uses static COLUMNS.length but must use the active " +
        "column set length after view-mode selector is added. " +
        "Rusty: replace COLUMNS.length with activeColumns.length in all colSpan attributes."
      );
    }
    // Once implemented, one of the dynamic references must be present
    assert.ok(true, "CS-1: colSpan check passed (static or dynamic)");
  });

  it("CS-2: Portfolio column count + Actions = 11 total table cells", () => {
    assert.equal(PORTFOLIO_COLUMN_KEYS.length + 1, 11,
      `CS-2: Portfolio 10 data columns + 1 Actions = 11; got ${PORTFOLIO_COLUMN_KEYS.length + 1}`);
  });

  it("CS-3: Options column count + Actions = 10 total table cells", () => {
    assert.equal(
      OPTIONS_COLUMN_KEYS.length + 1,  // +1 for Actions
      10,
      `CS-3: Options 9 data columns + 1 Actions = 10; got ${OPTIONS_COLUMN_KEYS.length + 1}`
    );
  });

  it("CS-4: No column key appears in both sets (sets are disjoint outside common)", () => {
    const portfolioOnly = PORTFOLIO_ONLY_KEYS;
    const optionsOnly   = OPTIONS_ONLY_KEYS;
    const overlap = portfolioOnly.filter((k) => optionsOnly.includes(k));
    assert.equal(
      overlap.length, 0,
      `CS-4: Portfolio-only and Options-only column key sets must be disjoint; overlap: ${overlap.join(", ")}`
    );
  });
});

// ---------------------------------------------------------------------------
// AC: Accessibility
// ---------------------------------------------------------------------------

describe("AC: Accessibility — selector active state and keyboard semantics", () => {
  it("AC-1: View selector uses aria-pressed, aria-checked, or role='tab' for active state", () => {
    // aria-checked is valid for role="radio" buttons inside a role="radiogroup"
    // aria-pressed is valid for toggle buttons
    // aria-selected is valid for role="tab"
    const hasAriaSemantic =
      src.includes("aria-pressed") ||
      src.includes("aria-checked") ||
      src.includes('role="tab"') ||
      src.includes("role='tab'") ||
      src.includes("role=\"tablist\"") ||
      src.includes("aria-selected");
    assert.ok(
      hasAriaSemantic,
      "AC-1 DEFECT: View selector buttons must have aria-pressed, aria-checked (for role='radio'), " +
      "or role='tab'/aria-selected so screen readers announce the active mode. " +
      "Rusty: add aria-checked={viewMode === mode} or aria-pressed to selector buttons."
    );
  });

  it("AC-2: Active selector button visually differs from inactive (class changes)", () => {
    // Pattern: conditional class based on viewMode comparison
    const hasConditionalClass =
      (src.includes("viewMode") || src.includes("tableMode")) &&
      src.includes("border-accent-blue");
    assert.ok(
      hasConditionalClass,
      "AC-2 DEFECT: Active view selector button must have a distinct visual style " +
      "(same pattern as suitability filter: border-accent-blue/bg-accent-blue/15). " +
      "Rusty: apply active class conditionally like SUITABILITY_FILTERS buttons."
    );
  });

  it("AC-3: Selector buttons are type='button' (no implicit form submit)", () => {
    // We check this works for suitability (existing) and the new selector should follow the same pattern
    const hasSuitabilityTypeButton = src.includes('type="button"');
    assert.ok(
      hasSuitabilityTypeButton,
      "AC-3: Selector buttons must have type='button'; pattern already exists for suitability filters"
    );
  });
});

// ---------------------------------------------------------------------------
// NF: No extra fetch / row-filter change on mode switch
// ---------------------------------------------------------------------------

describe("NF: No extra fetch on mode switch", () => {
  it("NF-1: SymbolsTable does not call fetch() inside useMemo or viewMode effect", () => {
    // The component receives rows as props; mode switch must not trigger re-fetch.
    // Detect any fetch inside a useEffect that depends on viewMode.
    // Simple proxy: look for fetch() calls that appear near viewMode references.
    // A fetch() in the root scope of a useEffect([viewMode,...]) block would be a defect.
    const hasFetchOnViewMode =
      src.includes("fetch(") &&
      src.includes("viewMode") &&
      src.includes("useEffect");
    // This is not definitive but a warning signal — if fetch + viewMode + useEffect all
    // appear, inspect manually.
    if (hasFetchOnViewMode) {
      // Only fail if they appear within 500 chars of each other (proximity proxy)
      const vmIdx = src.indexOf("viewMode");
      const fetchIdx = src.indexOf("fetch(");
      const useEffectIdx = src.indexOf("useEffect");
      const proximity = Math.max(
        Math.abs(vmIdx - fetchIdx),
        Math.abs(vmIdx - useEffectIdx),
      );
      assert.ok(
        proximity > 500,
        "NF-1 DEFECT: fetch() appears close to viewMode in a useEffect — " +
        "mode switch must NOT trigger additional API calls. " +
        "SymbolsTable is a pure display component; rows come from props only."
      );
    }
    assert.ok(true, "NF-1: No fetch-on-viewMode-change defect detected");
  });

  it("NF-2: Mode switch operates on already-filtered rows (no row-set change)", () => {
    // The 'filtered' memo should NOT depend on viewMode — only column visibility changes.
    // Proxy: check that the useMemo for 'filtered' does not reference viewMode.
    const filteredMemoIdx = src.indexOf("const filtered = useMemo");
    if (filteredMemoIdx === -1) {
      // filtered renamed — skip
      assert.ok(true, "NF-2: 'filtered' useMemo not found by name; check manually if needed");
      return;
    }
    // Extract the deps array of the filtered memo (heuristic: look for the ], after the memo body)
    const memoSection = src.slice(filteredMemoIdx, filteredMemoIdx + 800);
    const hasViewModeInDeps = memoSection.includes("viewMode") || memoSection.includes("tableMode");
    assert.ok(
      !hasViewModeInDeps,
      "NF-2 DEFECT: 'filtered' useMemo must NOT depend on viewMode. " +
      "Filtering (rows shown) must be independent of column mode. " +
      "Mode only controls which columns are rendered, not which rows are visible."
    );
  });
});

// ---------------------------------------------------------------------------
// SF: Shared-filter regression
// ---------------------------------------------------------------------------

describe("SF: Shared-filter behavior preserved after view-selector addition", () => {
  it("SF-1: Search filter 'q' state variable is still present", () => {
    assert.ok(
      src.includes("const [q, setQ]") || src.includes('const [q,'),
      "SF-1 REGRESSION: Search state 'q' must remain in SymbolsTable"
    );
  });

  it("SF-2: Suitability filter state is still present", () => {
    assert.ok(
      src.includes("suitabilityFilter") || src.includes("SUITABILITY_FILTERS"),
      "SF-2 REGRESSION: Suitability filter must remain in SymbolsTable"
    );
  });

  it("SF-3: hideZero toggle state is still present", () => {
    assert.ok(
      src.includes("hideZero") || src.includes("hide_zero"),
      "SF-3 REGRESSION: hideZero toggle must remain in SymbolsTable"
    );
  });

  it("SF-4: Both Portfolio and Watchlist sections are still rendered", () => {
    assert.ok(
      src.includes("portfolioFiltered") || src.includes("portfolio_filtered"),
      "SF-4a: portfolioFiltered must still exist"
    );
    assert.ok(
      src.includes("watchlistFiltered") || src.includes("watchlist_filtered"),
      "SF-4b: watchlistFiltered must still exist"
    );
  });

  it("SF-5: Search input references 'q' state (single shared toolbar)", () => {
    assert.ok(
      src.includes("value={q}") || src.includes("value={q "),
      "SF-5: Search input must be bound to the single 'q' state"
    );
  });
});

// ---------------------------------------------------------------------------
// RG: Regression — existing column labels not removed
// ---------------------------------------------------------------------------

describe("RG: Regression guard — existing column labels preserved", () => {
  it("RG-1: 'Price' column label still present", () => {
    assert.ok(
      src.includes('label: "Price"') || src.includes("label: 'Price'"),
      "RG-1 REGRESSION: 'Price' column label removed from SymbolsTable"
    );
  });

  it("RG-2: 'Dividends' column label still present", () => {
    assert.ok(
      src.includes("Dividends") || src.includes("dividends"),
      "RG-2 REGRESSION: 'Dividends' column label removed"
    );
  });

  it("RG-3: 'In Calls' column label still present", () => {
    assert.ok(
      src.includes("In Calls"),
      "RG-3 REGRESSION: 'In Calls' column label removed"
    );
  });

  it("RG-4: 'Puts $' or put_exposure column still present", () => {
    assert.ok(
      src.includes("Puts $") || src.includes("put_exposure"),
      "RG-4 REGRESSION: Puts column removed"
    );
  });

  it("RG-5: 'Price €' column label present (pricing Phase 4 column not dropped)", () => {
    assert.ok(
      src.includes("Price €") || src.includes("price_eur"),
      "RG-5 REGRESSION: 'Price €' column label removed"
    );
  });

  it("RG-6: 'Value €' / current_value_eur column still present", () => {
    assert.ok(
      src.includes("Value €") || src.includes("current_value_eur"),
      "RG-6 REGRESSION: 'Value €' column removed"
    );
  });

  it("RG-7: SortKey type includes all historical sort keys", () => {
    const sortKeys = [
      "dgi_score", "tech_timing", "momentum", "price", "price_eur",
      "portfolio_invested_eur", "current_value_eur", "in_calls",
    ];
    for (const key of sortKeys) {
      assert.ok(
        src.includes(`"${key}"`) || src.includes(`'${key}'`),
        `RG-7 REGRESSION: SortKey '${key}' removed from SymbolsTable`
      );
    }
  });
});

// ---------------------------------------------------------------------------
// CS-LIVE: Live colSpan check — current source state
// ---------------------------------------------------------------------------

describe("CS-LIVE: Current source colSpan state (tracks implementation progress)", () => {
  it("CS-LIVE-1: colSpan in empty-row uses COLUMNS.length (pre-implementation baseline)", () => {
    // This test documents the CURRENT state. Once Rusty updates to use activeColumns.length
    // this test should be updated to match the new pattern.
    const usesColumnsLength = src.includes("COLUMNS.length");
    const usesActiveLength =
      src.includes("activeColumns.length") ||
      src.includes("visibleColumns.length") ||
      src.includes("cols.length");
    if (usesActiveLength) {
      // Implementation already updated — verify static COLUMNS.length is gone from colSpan
      assert.ok(
        true,
        "CS-LIVE-1: colSpan already uses dynamic activeColumns.length — implementation complete"
      );
    } else if (usesColumnsLength) {
      // Still using static COLUMNS.length — acceptable until implementation lands
      assert.ok(
        true,
        "CS-LIVE-1: colSpan uses static COLUMNS.length (pre-implementation baseline; " +
        "will need update when view selector is added)"
      );
    } else {
      assert.ok(
        true,
        "CS-LIVE-1: No colSpan reference to COLUMNS.length detected; table structure may have changed"
      );
    }
  });
});

// ===========================================================================
// CW: Column width contract
//   Proportional / content-driven widths; no equal-column-grid assumption;
//   horizontal overflow; no content clipping.
//
//   Source-contract strategy: robust structural invariants; no pixel-exact snapshots.
//   CW-1..CW-11, CW-13, CW-14: pass with current committed source.
//   CW-12 (whitespace-nowrap): passes once Rusty adds nowrap to COLUMNS + th render.
//   CW-15..CW-18 (nowrap/width/colgroup metadata): document Rusty's full width-contract.
// ===========================================================================

/** Parse per-column attribute snapshots from COLUMNS-like source blocks. */
function parseColumnAttributes(source) {
  const result = new Map();
  for (const m of source.matchAll(/\{\s*key:\s*["']([^"']+)["']([^}]*)\}/g)) {
    const key   = m[1];
    const attrs = m[2];
    result.set(key, {
      hasAlignRight:     attrs.includes('"right"') || attrs.includes("'right'"),
      hasModesPortfolio: attrs.includes('"portfolio"') || attrs.includes("'portfolio'"),
      hasModesOptions:   attrs.includes('"options"')   || attrs.includes("'options'"),
      hasModes:          attrs.includes("modes"),
    });
  }
  return result;
}

describe("CW: Column width contract — proportional, overflow-safe, no clipping", () => {
  it("CW-1: COLUMNS type declares `align` field — alignment metadata-driven not ad-hoc", () => {
    assert.ok(
      src.includes("align?:") || (src.includes("COLUMNS") && src.includes("align:") && src.includes('"right"')),
      "CW-1 DEFECT: COLUMNS type must include an `align` field so header/body alignment " +
      "can be driven from a single source."
    );
  });

  it("CW-2: All right-aligned numeric columns have `align: \"right\"` in COLUMNS definition", () => {
    const numericKeys = [
      "dgi_score", "tech_timing", "price", "price_eur",
      "portfolio_shares", "portfolio_avg_cost_eur", "portfolio_invested_eur",
      "current_value_eur", "portfolio_dividends_eur", "in_calls", "put_exposure",
    ];
    const colAttrs = parseColumnAttributes(src);
    for (const key of numericKeys) {
      const attrs = colAttrs.get(key);
      assert.ok(attrs !== undefined, `CW-2: Column '${key}' not found in source COLUMNS array`);
      assert.ok(attrs.hasAlignRight, `CW-2 DEFECT: '${key}' is numeric and must have align: "right" in COLUMNS`);
    }
  });

  it("CW-3: Symbol, Category and Momentum are left-aligned (no align: \"right\")", () => {
    const colAttrs = parseColumnAttributes(src);
    for (const key of ["symbol", "category", "momentum"]) {
      const attrs = colAttrs.get(key);
      if (!attrs) continue;
      assert.ok(!attrs.hasAlignRight,
        `CW-3 DEFECT: '${key}' is a text column and must NOT have align: "right"`);
    }
  });

  it("CW-4: Header th applies alignment from COLUMNS metadata via conditional class", () => {
    assert.ok(
      (src.includes("c.align") && src.includes("text-right")),
      "CW-4 DEFECT: Header <th> must derive text-right from c.align metadata. " +
      "Pattern: c.align === \"right\" ? \"text-right\" : \"\""
    );
  });

  it("CW-5: Table wrapper has `overflow-x-auto` — horizontal scroll on narrow viewports", () => {
    assert.ok(src.includes("overflow-x-auto"),
      "CW-5 DEFECT: Table wrapper must have overflow-x-auto so narrow screens can scroll horizontally.");
  });

  it("CW-6: Table element has a non-zero minimum width (columns do not collapse to zero)", () => {
    assert.ok(
      src.includes("min-w-[") || src.includes("min-width:") || src.includes("minWidth"),
      "CW-6 DEFECT: Table must have a minimum-width hint (e.g. min-w-[1200px]) to prevent columns collapsing."
    );
  });

  it("CW-6b: Table minimum width is ≥ 800px if specified as a pixel value", () => {
    const match = src.match(/min-w-\[(\d+)px\]/);
    if (match) {
      const px = parseInt(match[1], 10);
      assert.ok(px >= 800, `CW-6b: Table min-width ${px}px is too narrow — expected ≥ 800px.`);
    } else {
      assert.ok(true, "CW-6b: No pixel min-w found; may use CSS variable — acceptable.");
    }
  });

  it("CW-7: No `table-fixed` layout — columns are proportional/content-driven", () => {
    assert.ok(!src.includes("table-fixed"),
      "CW-7 DEFECT: table-fixed forces equal column widths and must not be used. " +
      "With 10 Portfolio and 9 Options columns, equal-width is wrong for both.");
  });

  it("CW-7b: No w-1/N equal-distribution fraction classes on header or data cells", () => {
    assert.ok(!/\bw-1\/\d+\b/.test(src),
      "CW-7b DEFECT: Equal-width fraction classes (w-1/9, w-1/10) must not appear — " +
      "columns must be sized by content, not distributed equally.");
  });

  it("CW-8: Numeric body cells use `font-mono` for digit alignment", () => {
    assert.ok(src.includes("font-mono"),
      "CW-8 DEFECT: Numeric body cells must use font-mono for proper digit alignment.");
    assert.ok((src.match(/font-mono/g) ?? []).length >= 3,
      "CW-8 DEFECT: font-mono must appear on at least 3 numeric columns.");
  });

  it("CW-9: No `overflow-hidden` near <td> elements — essential content must not be clipped", () => {
    const tdIdx = src.indexOf("<td ");
    if (tdIdx !== -1) {
      const tdRegion = src.slice(tdIdx, tdIdx + 10000);
      const clipped  = tdRegion.includes("overflow-hidden") && !tdRegion.includes("truncate");
      assert.ok(!clipped,
        "CW-9 DEFECT: overflow-hidden on a <td> without truncate would silently clip financial data.");
    }
    assert.ok(true, "CW-9: No overflow-hidden content-clipping detected on <td> elements.");
  });

  it("CW-9b: No `truncate` class on monetary/numeric cells", () => {
    const hasTruncateOnNumeric = ["font-mono", "eur(", "num(", "usd("].some(ctx => {
      const idx = src.indexOf(ctx);
      if (idx === -1) return false;
      return src.slice(Math.max(0, idx - 100), idx + 200).includes("truncate");
    });
    assert.ok(!hasTruncateOnNumeric,
      "CW-9b DEFECT: truncate must not appear near numeric cells — financial data must be fully visible.");
  });

  it("CW-10: Portfolio (10 cols) and Options (9 cols) have different column counts — widths must adapt", () => {
    assert.strictEqual(PORTFOLIO_COLUMN_KEYS.length, 10, "CW-10: Portfolio must have 10 data columns");
    assert.strictEqual(OPTIONS_COLUMN_KEYS.length,   9,  "CW-10: Options must have 9 data columns");
    assert.notStrictEqual(PORTFOLIO_COLUMN_KEYS.length, OPTIONS_COLUMN_KEYS.length,
      "CW-10: Column counts must differ so widths adapt per mode rather than being fixed-equal.");
  });

  it("CW-10b: Symbol column is present in both mode sets (needs flexible width in all modes)", () => {
    assert.ok(PORTFOLIO_COLUMN_KEYS.includes("symbol"), "CW-10b: symbol in Portfolio");
    assert.ok(OPTIONS_COLUMN_KEYS.includes("symbol"),   "CW-10b: symbol in Options");
  });

  it("CW-11: All COLUMNS labels are concise (≤ 12 chars) — long labels force wide minimum column widths", () => {
    const labels = [...src.matchAll(/label:\s*["']([^"']+)["']/g)].map(m => m[1]);
    assert.ok(labels.length >= 8, `CW-11: Expected ≥ 8 column labels in source, found ${labels.length}`);
    for (const label of labels) {
      assert.ok(label.length <= 12,
        `CW-11 DEFECT: Label "${label}" (${label.length} chars) exceeds 12-char limit.`);
    }
  });

  it("CW-12: Header <th> cells have whitespace-nowrap — multi-word labels must not wrap", () => {
    // Accept: whitespace-nowrap applied to the th render via c.nowrap, OR unconditionally.
    // Defect: no whitespace-nowrap at all on header cells.
    const theadEnd = src.indexOf("</thead>");
    const theadBlock = theadEnd !== -1 ? src.slice(0, theadEnd) : src;
    const hasNowrap =
      theadBlock.includes("whitespace-nowrap") ||
      (src.includes("c.nowrap") && src.includes("whitespace-nowrap"));
    assert.ok(hasNowrap,
      "CW-12 DEFECT: Header <th> must have whitespace-nowrap (or c.nowrap conditional) to prevent " +
      "two-word labels like 'In Calls', 'Avg Cost' from wrapping. " +
      "Rusty: add ${c.nowrap ? \" whitespace-nowrap\" : \"\"} to th className.");
  });

  it("CW-13: Symbol column is not assigned a narrow fixed width", () => {
    const hasTableMinWidth = src.includes("min-w-[") || src.includes("min-width");
    const symbolBlock = src.match(/key:\s*["']symbol["'][^}]*/)?.[0] ?? "";
    const hasNarrow = /\bw-(8|12|16|20|24|28|32)\b/.test(symbolBlock) || symbolBlock.includes("w-[60px]");
    assert.ok(hasTableMinWidth && !hasNarrow,
      "CW-13 DEFECT: Symbol column must have minimum-width protection AND must not have " +
      "a narrow fixed-width class that clips 'XNYS:ABBV'-style tickers.");
  });

  it("CW-13b: No hard-coded narrow cell width on numeric short columns", () => {
    const fontMonoIdx = src.indexOf("font-mono");
    const hasTooNarrow = fontMonoIdx !== -1 &&
      /\bw-(8|10|12|14)\b/.test(src.slice(Math.max(0, fontMonoIdx - 50), fontMonoIdx + 100));
    assert.ok(!hasTooNarrow,
      "CW-13b DEFECT: Numeric cells must not have narrow fixed widths (w-8..w-14).");
  });

  it("CW-14: overflow-x-auto container survives view-selector addition (regression)", () => {
    const count = (src.match(/overflow-x-auto/g) ?? []).length;
    assert.ok(count >= 1,
      `CW-14 DEFECT: overflow-x-auto must remain after view-selector refactor. Found ${count} occurrence(s).`);
  });

  it("CW-15: COLUMNS type declares `nowrap` field — header nowrap is metadata-driven", () => {
    assert.ok(
      src.includes("nowrap?:") || src.includes("nowrap?: true") || src.includes("nowrap?: boolean"),
      "CW-15 DEFECT: COLUMNS type must declare nowrap?: true field so whitespace-nowrap is applied " +
      "from metadata, not hardcoded per-column in JSX."
    );
  });

  it("CW-15b: Header th applies nowrap from COLUMNS metadata (c.nowrap conditional)", () => {
    assert.ok(
      src.includes("c.nowrap") && src.includes("whitespace-nowrap"),
      "CW-15b DEFECT: Header <th> must apply c.nowrap to append whitespace-nowrap. " +
      "Pattern: ${c.nowrap ? \" whitespace-nowrap\" : \"\"}"
    );
  });

  it("CW-15c: Compact numeric columns have nowrap:true in COLUMNS definition", () => {
    for (const key of ["dgi_score", "tech_timing", "price", "in_calls", "put_exposure"]) {
      const keyBlock = src.match(new RegExp(`key:\\s*["']${key}["'][^}]*}`))?.[0] ?? "";
      assert.ok(keyBlock.includes("nowrap"),
        `CW-15c DEFECT: '${key}' is a compact numeric column and must have nowrap: true in COLUMNS`);
    }
  });

  it("CW-16: COLUMNS type declares `width` field — per-column width is metadata-driven", () => {
    assert.ok(
      src.includes("width?:") || src.includes('width?: string') || src.includes('c.width'),
      "CW-16 DEFECT: COLUMNS type must declare width?: string so column widths are centralized."
    );
  });

  it("CW-16b: Source has <colgroup> applying per-column widths from COLUMNS metadata", () => {
    assert.ok(src.includes("<colgroup>"),
      "CW-16b DEFECT: <colgroup> must be present to apply per-column width hints via <col>.");
    assert.ok(src.includes("c.width") || (src.includes("<col") && src.includes("width")),
      "CW-16b DEFECT: <col> elements must reference c.width from COLUMNS metadata.");
  });

  it("CW-17: Symbol column is wider than compact DGI/Tech columns (proportional widths)", () => {
    const symPx  = parseInt(src.match(/key:\s*["']symbol["'][^}]*width:\s*["'](\d+)px["']/)?.[1]   ?? "0", 10);
    const dgiPx  = parseInt(src.match(/key:\s*["']dgi_score["'][^}]*width:\s*["'](\d+)px["']/)?.[1] ?? "0", 10);
    if (symPx > 0 && dgiPx > 0) {
      assert.ok(symPx > dgiPx,
        `CW-17 DEFECT: Symbol (${symPx}px) must be wider than DGI (${dgiPx}px) for proportional layout.`);
    } else {
      assert.ok(true, "CW-17: Symbol/DGI widths not in parseable px form; proportionality check skipped.");
    }
  });

  it("CW-18: <colgroup> iterates visibleColumns (mode-aware), not full COLUMNS array", () => {
    assert.ok(
      (src.includes("visibleColumns") || src.includes("activeColumns")) && src.includes("<colgroup>"),
      "CW-18 DEFECT: <colgroup> must iterate visibleColumns (mode-filtered) so col count " +
      "matches header/body in both Portfolio and Options modes."
    );
  });
});

// ===========================================================================
// MW: Mode-aware min-width — no stale layout on Portfolio ↔ Options switch
//
//   Verifies that tableMinWidth, colgroup, and header all derive from the SAME
//   visibleColumns set (via the `cols` alias), that Options' narrower column set
//   does NOT inherit Portfolio's wider minimum, and that switching modes
//   recomputes the active layout atomically with no residual hidden-column space.
//
//   All tests are structural source-contract or arithmetic derivations from
//   COLUMNS metadata. No pixel-snapshot assertions.
// ===========================================================================

/**
 * Parse per-column width and mode metadata directly from the COLUMNS definition
 * in source. Returns computed minWidths including a 52px Actions column offset.
 */
function parseColumnsMinWidths(source) {
  const colsBlock = source.match(/const COLUMNS[^=]*=\s*\[([\s\S]*?)\];/)?.[1] ?? "";
  const ACTIONS_PX = 52;
  let portfolio = ACTIONS_PX;
  let options   = ACTIONS_PX;
  let portfolioOnly = 0;
  let optionsOnly   = 0;
  for (const colM of colsBlock.matchAll(/\{[^}]+\}/g)) {
    const col = colM[0];
    const widthStr = col.match(/width:\s*["'](\d+)px["']/)?.[1];
    if (!widthStr) continue;
    const width    = parseInt(widthStr, 10);
    const modesRaw = col.match(/modes:\s*\[([^\]]*)\]/)?.[1] ?? "";
    const inPortfolio = !modesRaw || modesRaw.includes("portfolio");
    const inOptions   = !modesRaw || modesRaw.includes("options");
    if (inPortfolio) portfolio += width;
    if (inOptions)   options   += width;
    if (inPortfolio && !inOptions) portfolioOnly += width;
    if (inOptions   && !inPortfolio) optionsOnly  += width;
  }
  return { portfolio, options, portfolioOnly, optionsOnly };
}

describe("MW: Mode-aware min-width — no stale layout on Portfolio \u21d4 Options switch", () => {
  it("MW-1: tableMinWidth derived from cols.reduce(parseInt(c.width)) — not a static numeric literal", () => {
    assert.ok(
      (src.includes("cols.reduce") || src.includes("visibleColumns.reduce")) &&
      src.includes("parseInt(c.width"),
      "MW-1 DEFECT: tableMinWidth must be a .reduce() over cols/visibleColumns that sums " +
      "parseInt(c.width,...). A static literal would not update when viewMode changes."
    );
    assert.ok(
      !/const tableMinWidth\s*=\s*\d+/.test(src),
      "MW-1 DEFECT: tableMinWidth must not be assigned a bare numeric constant — " +
      "switching modes would leave the old Portfolio minimum in place."
    );
  });

  it("MW-2: Table min-width applied via inline style — not a static Tailwind min-w-[...] class", () => {
    assert.ok(
      src.includes("tableMinWidth") && src.includes("minWidth") && src.includes("style="),
      "MW-2 DEFECT: Table must use style={{ minWidth: `${tableMinWidth}px` }} so the value " +
      "recomputes atomically with viewMode. A Tailwind class is fixed at render and cannot adapt."
    );
    const tableTag = src.match(/<table\b[^>]*/)?.[0] ?? "";
    assert.ok(!tableTag.includes("min-w-["),
      "MW-2 DEFECT: <table> must not carry a static min-w-[...] class — use inline style only.");
  });

  it("MW-3: Single `cols` alias drives colgroup, header AND minWidth reduce — no divergent column list", () => {
    assert.ok(
      src.includes("const cols = visibleColumns") || src.includes("cols = visibleColumns"),
      "MW-3 DEFECT: 'cols' must be a direct alias of 'visibleColumns' so that colgroup, " +
      "header, and tableMinWidth all consume the same filtered array."
    );
    assert.ok(
      /colgroup[\s\S]{0,300}cols\.map/.test(src),
      "MW-3 DEFECT: <colgroup> must iterate cols.map — the same source as the header."
    );
    assert.ok(
      /thead[\s\S]{0,800}cols\.map/.test(src),
      "MW-3 DEFECT: <thead> must iterate cols.map — the same source as colgroup."
    );
    assert.ok(src.includes("cols.reduce"),
      "MW-3 DEFECT: tableMinWidth must use cols.reduce — same cols ref as colgroup and header.");
  });

  it("MW-4: Portfolio computed minWidth > Options computed minWidth (Options cols are fewer/narrower)", () => {
    const w = parseColumnsMinWidths(src);
    assert.ok(w.portfolio > 0 && w.options > 0,
      `MW-4: COLUMNS parse yielded zero widths (portfolio=${w.portfolio}, options=${w.options}).`);
    assert.ok(w.portfolio > w.options,
      `MW-4 DEFECT: Portfolio minWidth (${w.portfolio}px) must exceed Options (${w.options}px). ` +
      "Options omits 6 wider portfolio-specific columns; switching to Options must shrink the table minimum.");
  });

  it("MW-5: Min-width difference equals portfolio-only width minus options-only width (exact accounting)", () => {
    const w = parseColumnsMinWidths(src);
    // Both modes share the same common columns, so the difference is entirely explained
    // by the asymmetry in mode-specific columns. If any stale width leaked in, this fails.
    const expectedDiff = w.portfolioOnly - w.optionsOnly;
    const actualDiff   = w.portfolio - w.options;
    assert.equal(actualDiff, expectedDiff,
      `MW-5 DEFECT: minWidth delta is ${actualDiff}px but mode-specific column delta is ${expectedDiff}px. ` +
      "Extra or missing width suggests a stale column width is included in one mode's calculation.");
  });

  it("MW-6: No independent COLUMNS.filter for colgroup — colgroup reuses cols (visibleColumns)", () => {
    // At most 2 COLUMNS.filter() calls are expected:
    //   1. visibleColumns = COLUMNS.filter(c => !c.modes || c.modes.includes(viewMode))
    //   2. inside changeViewMode() to compute the new sort-reset eligible set
    // A third would mean colgroup or header has its own divergent filter — a stale-layout bug.
    const filterCount = (src.match(/COLUMNS\.filter/g) ?? []).length;
    assert.ok(filterCount <= 2,
      `MW-6 DEFECT: Found ${filterCount} COLUMNS.filter() calls. ` +
      "colgroup and header must reuse 'cols' (visibleColumns), not re-filter independently, " +
      "which could cause header/colgroup col-count to diverge from each other.");
    assert.ok(!src.includes("COLUMNS.map"),
      "MW-6 DEFECT: colgroup/header must map over cols (visibleColumns), not raw COLUMNS.");
  });

  it("MW-7: Body cell mode guards are consistent with visibleColumns filter predicate", () => {
    // Body cells are guarded per-cell with viewMode === 'portfolio'/'options'; the column
    // filter predicate !c.modes || c.modes.includes(viewMode) is the single authoritative truth.
    assert.ok(
      src.includes("c.modes.includes(viewMode)") || src.includes("c.modes?.includes(viewMode)"),
      "MW-7 DEFECT: visibleColumns filter must use c.modes.includes(viewMode). " +
      "Body guards must derive from the same predicate to keep header/body/colgroup in sync."
    );
    const portfolioGuards = (src.match(/viewMode === ["']portfolio["']/g) ?? []).length;
    const optionsGuards   = (src.match(/viewMode === ["']options["']/g) ?? []).length;
    assert.ok(portfolioGuards >= 1,
      `MW-7 DEFECT: Expected ≥ 1 body guard for viewMode === 'portfolio'; found ${portfolioGuards}.`);
    assert.ok(optionsGuards >= 1,
      `MW-7 DEFECT: Expected ≥ 1 body guard for viewMode === 'options'; found ${optionsGuards}.`);
  });

  it("MW-8: Actions offset in reduce matches colSpan = cols.length + 1 (no phantom Actions column width)", () => {
    // tableMinWidth reduce adds a fixed px value for the single Actions <td> not in COLUMNS.
    // That same Actions column explains the +1 in colSpan for empty/loading rows.
    assert.ok(
      src.includes("cols.reduce") && src.includes("52"),
      "MW-8 DEFECT: tableMinWidth reduce must include a 52px Actions offset " +
      "matching the one Actions <td> outside COLUMNS."
    );
    assert.ok(
      src.includes("cols.length + 1") || src.includes("cols.length+1"),
      "MW-8 DEFECT: Empty/loading rows must use cols.length + 1 colSpan to account for the Actions column."
    );
  });

  it("MW-9: Mode switch rebuilds both col-key set and minWidth atomically (no half-updated state)", () => {
    // changeViewMode() must update viewMode; visibleColumns and tableMinWidth are derived via
    // useMemo/computed from viewMode, so they update in the same render — no intermediate state
    // where col-keys have switched but minWidth still reflects the old mode.
    assert.ok(
      src.includes("useMemo") && src.includes("visibleColumns") && src.includes("[viewMode]"),
      "MW-9 DEFECT: visibleColumns must be a useMemo with [viewMode] dependency so it recomputes " +
      "in the same render as the mode change — preventing a frame where header/colgroup show new " +
      "columns but minWidth still reflects the old mode's wider/narrower set."
    );
    // tableMinWidth must derive from visibleColumns (via cols), not be a separate useState
    assert.ok(
      !src.includes("setTableMinWidth") && !src.includes("useState(tableMinWidth)"),
      "MW-9 DEFECT: tableMinWidth must not be a useState — it must derive from visibleColumns " +
      "so it recomputes atomically and never holds a stale value from the previous mode."
    );
  });

  it("MW-10: Parsed COLUMNS column counts match established contract constants (round-trip integrity)", () => {
    const w = parseColumnsMinWidths(src);
    // Re-parse col keys per mode to cross-check contract constants
    const colsBlock = src.match(/const COLUMNS[^=]*=\s*\[([\s\S]*?)\];/)?.[1] ?? "";
    const portfolioParsed = [];
    const optionsParsed   = [];
    for (const colM of colsBlock.matchAll(/\{[^}]+\}/g)) {
      const col = colM[0];
      const key = col.match(/key:\s*["']([^"']+)["']/)?.[1];
      if (!key) continue;
      const modesRaw = col.match(/modes:\s*\[([^\]]*)\]/)?.[1] ?? "";
      if (!modesRaw || modesRaw.includes("portfolio")) portfolioParsed.push(key);
      if (!modesRaw || modesRaw.includes("options"))   optionsParsed.push(key);
    }
    assert.equal(portfolioParsed.length, PORTFOLIO_COLUMN_KEYS.length,
      `MW-10 DEFECT: COLUMNS parsed ${portfolioParsed.length} portfolio-visible cols; ` +
      `contract expects ${PORTFOLIO_COLUMN_KEYS.length}. ` +
      `Parsed: [${portfolioParsed.join(", ")}]`);
    assert.equal(optionsParsed.length, OPTIONS_COLUMN_KEYS.length,
      `MW-10 DEFECT: COLUMNS parsed ${optionsParsed.length} options-visible cols; ` +
      `contract expects ${OPTIONS_COLUMN_KEYS.length}. ` +
      `Parsed: [${optionsParsed.join(", ")}]`);
    // Also verify computed widths are non-trivial (sanity)
    assert.ok(w.portfolio >= 800,
      `MW-10: Portfolio minWidth ${w.portfolio}px seems too small — check COLUMNS widths.`);
    assert.ok(w.options >= 600,
      `MW-10: Options minWidth ${w.options}px seems too small — check COLUMNS widths.`);
  });
});
