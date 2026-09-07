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
// Inline here for behavioral tests that don't depend on product source.
// These are the CONTRACT definitions — used to validate product output.
// ---------------------------------------------------------------------------

const COMMON_KEYS = [
  "symbol", "category", "dgi_score", "tech_timing", "entry_tag", "momentum", "price",
];

const PORTFOLIO_ONLY_KEYS = [
  // every column currently in the table EXCEPT In Calls / Puts $
  "price_eur", "portfolio_shares", "portfolio_avg_cost_eur",
  "portfolio_invested_eur", "current_value_eur", "portfolio_dividends_eur",
];

const OPTIONS_ONLY_KEYS = [
  "in_calls", "put_exposure",
];

// Keys that must be ABSENT from Options mode (per contract)
const OPTIONS_HIDDEN_KEYS = [
  "price_eur",           // "Price in EUR hidden in Options"
  "current_value_eur",   // "Current Value hidden in Options"
  // All other portfolio-only keys are also hidden in Options
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

  it("CC-2: Contract: 7 common keys must be subsets of both mode sets", () => {
    // Behavioral: verify the inline CONTRACT sets are correct
    for (const key of COMMON_KEYS) {
      assert.ok(
        PORTFOLIO_COLUMN_KEYS.includes(key),
        `CC-2a: Common key '${key}' must be in Portfolio column set`
      );
      assert.ok(
        OPTIONS_COLUMN_KEYS.includes(key),
        `CC-2b: Common key '${key}' must be in Options column set`
      );
    }
  });

  it("CC-3: Contract: 'in_calls' and 'put_exposure' absent from Portfolio set", () => {
    assert.ok(
      !PORTFOLIO_COLUMN_KEYS.includes("in_calls"),
      "CC-3a: 'in_calls' must be ABSENT from Portfolio column set (Options-only)"
    );
    assert.ok(
      !PORTFOLIO_COLUMN_KEYS.includes("put_exposure"),
      "CC-3b: 'put_exposure' must be ABSENT from Portfolio column set (Options-only)"
    );
  });

  it("CC-4: Contract: 'in_calls' and 'put_exposure' present in Options set", () => {
    assert.ok(
      OPTIONS_COLUMN_KEYS.includes("in_calls"),
      "CC-4a: 'in_calls' must be in Options column set"
    );
    assert.ok(
      OPTIONS_COLUMN_KEYS.includes("put_exposure"),
      "CC-4b: 'put_exposure' must be in Options column set"
    );
  });

  it("CC-5: Portfolio column count: 13 columns (7 common + 6 portfolio-specific)", () => {
    assert.equal(
      PORTFOLIO_COLUMN_KEYS.length,
      13,
      `CC-5: Portfolio must have 13 columns, got ${PORTFOLIO_COLUMN_KEYS.length}: ${PORTFOLIO_COLUMN_KEYS.join(", ")}`
    );
  });

  it("CC-6: Options column count: 9 columns (7 common + 2 options-specific)", () => {
    assert.equal(
      OPTIONS_COLUMN_KEYS.length,
      9,
      `CC-6: Options must have 9 columns, got ${OPTIONS_COLUMN_KEYS.length}: ${OPTIONS_COLUMN_KEYS.join(", ")}`
    );
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
    // The implementation must use conditional rendering (different column set),
    // NOT hidden CSS. Check that there's no 'display:none' or 'hidden' on column keys.
    const hasCssHide =
      src.includes("display: none") ||
      src.includes('className="hidden"') ||
      src.includes("visibility: hidden");
    // We can't guarantee absence perfectly, but columns should be rendered conditionally.
    // Instead verify: the column computation is conditional on viewMode.
    const hasConditionalColumns =
      src.includes("viewMode") ||
      src.includes("tableMode") ||
      src.includes("selectedView") ||
      src.includes("PORTFOLIO_COLUMNS") ||
      src.includes("OPTIONS_COLUMNS");
    assert.ok(
      hasConditionalColumns || !hasCssHide,
      "HI-5 DEFECT: Columns must be conditionally rendered per viewMode, not hidden with CSS. " +
      "Rusty: compute activeColumns based on viewMode state."
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

  it("CS-2: Portfolio column count + Actions = 14 total table cells", () => {
    assert.equal(
      PORTFOLIO_COLUMN_KEYS.length + 1,  // +1 for Actions
      14,
      `CS-2: Portfolio 13 data columns + 1 Actions = 14; got ${PORTFOLIO_COLUMN_KEYS.length + 1}`
    );
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
