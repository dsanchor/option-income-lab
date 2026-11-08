import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import test from "node:test";
import { symbolsViewQueryOptions } from "../src/lib/symbols-overview-query.ts";

const require = createRequire(new URL("../package.json", import.meta.url));
const ts = require("typescript");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const { QueryClient, QueryObserver } = require("@tanstack/react-query");
const source = readFileSync(new URL("../src/components/SymbolsForecastTable.tsx", import.meta.url), "utf8");
const table = readFileSync(new URL("../src/components/SymbolsTable.tsx", import.meta.url), "utf8");

function load(react = React) {
  const loadedModule = { exports: {} };
  const code = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  vm.runInNewContext(code, {
    exports: loadedModule.exports, module: loadedModule,
    require: (name) => name === "react" ? react : require(name),
    Date, Promise,
  });
  return loadedModule.exports;
}

const { ForecastBandCell } = load();
const formatPrice = (n, currency) => n == null ? "—" : `${n.toFixed(2)} ${currency}`;
const latest = {
  created_date: "2026-11-06", price_at_creation: 100,
  confidence: 0.5, outer_confidence: 0.9,
  horizons: Object.fromEntries(["1d", "1w", "2w", "4w"].map((h, i) => [
    h, { low1: 99 - i, high1: 101 + i, low2: 98 - i, high2: 102 + i },
  ])),
};
function cell(summary, horizon = "1d", error) {
  return renderToStaticMarkup(React.createElement(ForecastBandCell, {
    summary, horizon, error, formatPrice, range: { from: "2026-08-08", to: "2026-11-06" },
  }));
}

test("three views preserve native Price and forecast the shared historical-filtered Symbols", () => {
  assert.match(table, /\["portfolio", "options", "forecast"\]/);
  assert.doesNotMatch(table, /hideHistorical && viewMode !== "forecast"/);
  assert.match(table, /viewMode === "forecast" && visibleRows\.map\(renderRow\)/);
  assert.match(table, /viewMode === "forecast" \|\| !optionFiltersAvailable \|\| matchesSymbolPositions/);
  assert.match(table, /viewMode !== "forecast" && optionFiltersAvailable && suitabilityFilter/);
  assert.match(table, /formatPrice\(r\.price, r\.price_display_currency\)/);
  assert.match(table, /\/forecasts\?range=90d/);
  assert.match(table, /forecasts\.data\?\.symbols\[r\.security_id \?\? r\.symbol\]/);
});

function symbolsTableHarness(rows) {
  const state = [];
  const memos = [];
  let hook = 0, tree, props = { rows, optionFiltersAvailable: false };
  const react = {
    ...React,
    useState(initial) {
      const index = hook++;
      if (!(index in state)) state[index] = initial;
      return [state[index], (next) => {
        state[index] = typeof next === "function" ? next(state[index]) : next;
      }];
    },
    useMemo(fn, deps) {
      const index = hook++;
      const previous = memos[index];
      if (!previous || deps.some((dep, i) => !Object.is(dep, previous.deps[i]))) {
        memos[index] = { deps, value: fn() };
      }
      return memos[index].value;
    },
    useCallback: (fn) => fn,
  };
  function loadModule(source) {
    const loadedModule = { exports: {} };
    const code = ts.transpileModule(source, {
      compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
    }).outputText;
    vm.runInNewContext(code, {
      exports: loadedModule.exports, module: loadedModule,
      require(name) {
        if (name === "react") return react;
        if (name === "next/navigation") return { useRouter: () => ({ refresh() {} }) };
        if (name === "next/link") return { default: ({ children, href }) => React.createElement("a", { href }, children) };
        if (name === "sonner") return { toast: { error() {} } };
        if (name === "lucide-react") return { Trash2: () => null };
        if (name === "@/components/SymbolsForecastTable") return { ForecastBandCell };
        if (name.startsWith("@/components/")) return {
          default: () => null, OptionTimingValue: () => null, OptionTimingModal: () => null,
        };
        if (name.startsWith("@/")) {
          const base = `../src/${name.slice(2)}`;
          const url = [".ts", ".tsx"].map((ext) => new URL(base + ext, import.meta.url)).find((url) => existsSync(url));
          return loadModule(readFileSync(url, "utf8"));
        }
        return require(name);
      },
    });
    return loadedModule.exports;
  }
  const Component = loadModule(table).default;
  function find(predicate, node) {
    if (!node || typeof node !== "object") return;
    if (Array.isArray(node)) {
      for (const child of node) { const found = find(predicate, child); if (found) return found; }
      return;
    }
    return predicate(node) ? node : find(predicate, node.props?.children);
  }
  return {
    render(next = {}) {
      props = { ...props, ...next };
      hook = 0;
      tree = Component(props);
      return renderToStaticMarkup(tree);
    },
    switchView(mode) {
      find((node) => node.props?.role === "radio" && node.props.children === mode, tree).props.onClick();
      return this.render();
    },
    checkbox(label = "Hide historical zero-share symbols") {
      return find((node) => node.props?.["aria-label"] === label, tree)?.props;
    },
    check(checked, label) { this.checkbox(label).onChange({ target: { checked } }); return this.render(); },
    search(value) {
      find((node) => node.props?.placeholder === "🔍 Filter symbols…", tree).props.onChange({ target: { value } });
      return this.render();
    },
  };
}

const historicalRows = [
  { symbol: "SOLD", security_id: "sold", row_source: "portfolio", portfolio_shares: "0", is_historical: true },
  { symbol: "SHORT", security_id: "short", row_source: "both", portfolio_shares: "-2", is_historical: true },
  { symbol: "LIVE", security_id: "live", row_source: "portfolio", portfolio_shares: "10", is_historical: false },
  { symbol: "WATCH", security_id: "watch", row_source: "watchlist", total_shares: 0, is_historical: false },
  { symbol: "UNKNOWN", security_id: "unknown", total_shares: 0 },
].map((row) => ({
  category: "", momentum: "", entry_tag: "", in_calls: 0, put_exposure: 0,
  option_timing: {
    freshness: "fresh",
    covered_call: { status: "unavailable", score: null, reason_codes: [] },
    cash_secured_put: { status: "unavailable", score: null, reason_codes: [] },
  },
  ...row,
}));
const renderedSymbols = (html) => Array.from(html.matchAll(/aria-label="Delete (\w+) and all its data"/g), ([, symbol]) => symbol);

test("Forecast defaults to hiding only authoritative not eligible, updates metadata/counts, and preserves uncheck across views", () => {
  const rows = ["BLOCKED", "OK", "UNAVAILABLE", "ERROR", "LOADING", "FALLBACK"].map((symbol) => ({
    ...historicalRows[2], symbol, security_id: symbol === "FALLBACK" ? undefined : symbol.toLowerCase(),
    category: "Not eligible", mic: "XNAS",
  }));
  const table = symbolsTableHarness(rows);
  const overview = {
    symbols: {
      blocked: { status: "not_eligible" }, BLOCKED: { status: "ok" },
      ok: { status: "ok", currency: "USD", latest },
      unavailable: { status: "unavailable" }, error: { status: "error" },
      FALLBACK: { status: "not_eligible" },
    },
    range: {},
  };
  assert.equal(renderedSymbols(table.render({ forecastOverview: overview })).length, 6);
  assert.equal(table.checkbox("Hide not eligible"), undefined);
  const filtered = table.switchView("Forecast");
  assert.equal(table.checkbox("Hide not eligible").checked, true);
  assert.equal(table.checkbox("Hide unknown freshness"), undefined);
  assert.deepEqual(renderedSymbols(filtered), ["ERROR", "LOADING", "OK", "UNAVAILABLE"]);
  assert.match(filtered, /4 symbols/);

  const updated = { ...overview, symbols: { ...overview.symbols, blocked: { status: "unavailable" }, ok: { status: "not_eligible" } } };
  const reactive = table.render({ forecastOverview: updated });
  assert.deepEqual(renderedSymbols(reactive), ["BLOCKED", "ERROR", "LOADING", "UNAVAILABLE"]);
  assert.match(reactive, /4 symbols/);
  const fewer = table.render({
    forecastOverview: { ...updated, symbols: { ...updated.symbols, error: { status: "not_eligible" } } },
  });
  assert.deepEqual(renderedSymbols(fewer), ["BLOCKED", "LOADING", "UNAVAILABLE"]);
  assert.match(fewer, /3 symbols/);
  table.render({ forecastOverview: updated });
  assert.match(table.search("OK"), /0 symbols[\s\S]*No symbols match/);
  const restored = table.check(false, "Hide not eligible");
  assert.deepEqual(renderedSymbols(restored), ["OK"]);
  assert.match(restored, /Not elegible/);
  table.search("");
  for (const mode of ["Options", "Portfolio", "Forecast"]) {
    assert.equal(renderedSymbols(table.switchView(mode)).length, 6);
  }
  assert.equal(table.checkbox("Hide not eligible").checked, false);
  const changed = table.render({ forecastOverview: overview });
  assert.equal(renderedSymbols(changed).length, 6);
  assert.match(table.check(true, "Hide not eligible"), /4 symbols/);
});

test("Options hides unknown/missing freshness only, retains fresh/stale including unavailable scores, and preserves independent toggles", () => {
  const base = historicalRows[2];
  const rows = [
    ["FRESH", base.option_timing],
    ["STALE", { ...base.option_timing, freshness: "stale" }],
    ["UNKNOWN", { ...base.option_timing, freshness: "unknown" }],
    ["NULL", null],
    ["MISSING", undefined],
    ["NOFRESHNESS", { ...base.option_timing, freshness: undefined }],
    ["NULLFRESHNESS", { ...base.option_timing, freshness: null }],
  ].map(([symbol, option_timing], i) => ({
    ...base, symbol, security_id: symbol.toLowerCase(), option_timing,
    row_source: i % 2 ? "watchlist" : "portfolio", total_shares: 0, in_calls: 0,
  }));
  const table = symbolsTableHarness(rows);
  const portfolio = table.render();
  assert.equal(renderedSymbols(portfolio).length, 7);
  assert.equal(table.checkbox("Hide unknown freshness"), undefined);
  const options = table.switchView("Options");
  assert.equal(table.checkbox("Hide unknown freshness").checked, true);
  assert.equal(table.checkbox("Hide not eligible"), undefined);
  assert.deepEqual(renderedSymbols(options), ["FRESH", "STALE"]);
  assert.match(options, /1 portfolio · 1 watchlist/);
  assert.match(table.search("UNKNOWN"), /0 portfolio · 0 watchlist[\s\S]*No symbols match/);
  assert.deepEqual(renderedSymbols(table.check(false, "Hide unknown freshness")), ["UNKNOWN"]);
  table.search("");
  assert.equal(renderedSymbols(table.switchView("Forecast")).length, 7);
  assert.equal(table.checkbox("Hide not eligible").checked, true);
  table.check(false, "Hide not eligible");
  assert.equal(renderedSymbols(table.switchView("Portfolio")).length, 7);
  assert.equal(renderedSymbols(table.switchView("Options")).length, 7);
  assert.equal(table.checkbox("Hide unknown freshness").checked, false);
  assert.deepEqual(renderedSymbols(table.check(true, "Hide unknown freshness")), ["FRESH", "STALE"]);
  table.switchView("Forecast");
  assert.equal(table.checkbox("Hide not eligible").checked, false);
});

test("mode-specific defaults feed the shared historical count before hiding historical holdings", () => {
  const sold = historicalRows[0];
  const table = symbolsTableHarness([
    sold,
    { ...sold, symbol: "BLOCKED", security_id: "blocked", option_timing: null },
    { ...historicalRows[3], option_timing: null },
  ]);
  const portfolio = table.render({ forecastOverview: {
    symbols: { blocked: { status: "not_eligible" } }, range: {},
  } });
  assert.match(portfolio, /Hide historical \(2\)/);
  assert.match(portfolio, /0 portfolio · 1 watchlist/);
  const forecast = table.switchView("Forecast");
  assert.match(forecast, /Hide historical \(1\)/);
  assert.match(forecast, /1 symbols/);
  assert.match(table.check(false, "Hide not eligible"), /Hide historical \(2\)/);
  const options = table.switchView("Options");
  assert.match(options, /Hide historical \(1\)/);
  assert.match(options, /0 portfolio · 0 watchlist[\s\S]*No symbols match/);
  const restored = table.check(false, "Hide unknown freshness");
  assert.match(restored, /Hide historical \(2\)/);
  assert.deepEqual(renderedSymbols(restored), ["WATCH"]);
});

test("shared checkbox defaults checked and enabled in all tabs, hides real historical holdings only, and retains explicit uncheck", () => {
  const table = symbolsTableHarness(historicalRows);
  table.render();
  for (const mode of ["Portfolio", "Options", "Forecast"]) {
    const html = table.switchView(mode);
    assert.equal(table.checkbox().checked, true);
    assert.ok(!table.checkbox().disabled);
    assert.deepEqual(renderedSymbols(html).sort(), ["LIVE", "UNKNOWN", "WATCH"]);
    assert.match(html, /Hide historical \(2\)/);
    if (mode === "Forecast") {
      assert.match(html, /3 symbols/);
      assert.doesNotMatch(html, />Portfolio<\/td>|>Watchlist<\/td>/);
    }
  }
  assert.equal(renderedSymbols(table.check(false)).length, 5);
  for (const mode of ["Options", "Portfolio", "Forecast"]) {
    assert.equal(renderedSymbols(table.switchView(mode)).length, 5);
    assert.equal(table.checkbox().checked, false);
  }
  assert.equal(renderedSymbols(table.check(true)).length, 3);
});

test("Forecast historical-only search reports zero visible rows and no match, except while loading or failed", () => {
  const table = symbolsTableHarness(historicalRows);
  table.render();
  table.switchView("Forecast");
  const empty = table.search("SOLD");
  assert.match(empty, /0 symbols/);
  assert.match(empty, /No symbols match/);
  assert.deepEqual(renderedSymbols(empty), []);
  assert.doesNotMatch(table.render({ loading: true }), /No symbols match/);
  assert.doesNotMatch(table.render({ loading: false, loadError: "failed" }), /No symbols match/);
  table.render({ loadError: undefined });
  const revealed = table.check(false);
  assert.match(revealed, /1 symbols/);
  assert.doesNotMatch(revealed, /No symbols match/);
  assert.deepEqual(renderedSymbols(revealed), ["SOLD"]);
});

test("Forecast has only Symbol, native Price, four bands and Actions; other tabs retain Category/Momentum and cell alignment", () => {
  const table = symbolsTableHarness([{ ...historicalRows[2], category: "SEARCH_CATEGORY", momentum: "Bullish" }]);
  table.render({ forecastOverview: { symbols: {}, range: {} } });
  for (const mode of ["Portfolio", "Options", "Forecast"]) {
    const html = table.switchView(mode);
    const headers = Array.from(html.matchAll(/<th\b[^>]*>([\s\S]*?)<\/th>/g), ([, content]) => content.replace(/<[^>]*>/g, "").replace(/[▲▼]/g, "").trim());
    const row = html.match(/<tr\b[^>]*class="cursor-pointer[\s\S]*?<\/tr>/)[0];
    assert.equal((row.match(/<td\b/g) ?? []).length, headers.length);
    if (mode === "Forecast") {
      assert.deepEqual(headers, ["Symbol", "Price", "1d", "1w", "2w", "4w", "Actions"]);
      assert.doesNotMatch(row, /SEARCH_CATEGORY|Bullish/);
      assert.doesNotMatch(html, /width:300px/);
      const searched = table.search("SEARCH_CATEGORY");
      assert.deepEqual(renderedSymbols(searched), ["LIVE"]);
      const empty = table.search("absent");
      assert.match(empty, /colSpan="7"/);
      table.search("");
    } else {
      assert.ok(headers.includes("Category"));
      assert.ok(headers.includes("Momentum"));
      assert.match(row, /SEARCH_CATEGORY/);
      assert.match(row, /Bullish/);
    }
  }
});

test("all four horizons show nominal inner/outer bands, 90d hits and sample/date/anchor hover", () => {
  for (const [i, horizon] of ["1d", "1w", "2w", "4w"].entries()) {
    const html = cell({
      status: "ok", currency: "USD", latest,
      hit_rate: { [horizon]: { resolved: 8, hit_pct_1sigma: 0, hit_pct_2sigma: 87.5 } },
    }, horizon);
    assert.match(html, new RegExp(`${99 - i}\\.00 USD`));
    assert.match(html, new RegExp(`${102 + i}\\.00 USD`));
    assert.match(html, /Inner 50\.0%/);
    assert.match(html, /Outer 90\.0%/);
    assert.match(html, /Hit 90d: 0\.0% \/ 87\.5%/);
    assert.match(html, /8 resolved endpoints/);
    assert.match(html, /Forecast: 2026-11-06/);
    assert.match(html, /Anchor: 100\.00 USD/);
  }
});

test("zero or invalid sample counts and missing hits are dashes, never invented zero hits", () => {
  for (const resolved of [0, null, undefined, -1]) {
    const html = cell({ status: "ok", currency: "USD", latest,
      hit_rate: { "1d": { resolved, hit_pct_1sigma: 0, hit_pct_2sigma: null } } });
    assert.match(html, /Hit 90d: — \/ —/);
  }
  const html = cell({ status: "ok", currency: "USD", latest,
    hit_rate: { "1d": { resolved_inner: 0, resolved_outer: 8, hit_pct_1sigma: 0, hit_pct_2sigma: 87.5 } } });
  assert.match(html, /Hit 90d: — \/ 87\.5%/);
  assert.match(html, /0 inner \/ 8 outer resolved endpoints/);
});

test("forecast prices retain raw USD/EUR/GBP/GBp units independently of overview major units", () => {
  for (const currency of ["USD", "EUR", "GBP", "GBp", "GBX"]) {
    const html = cell({ status: "ok", currency, latest });
    assert.match(html, new RegExp(`99\\.00 ${currency === "GBX" ? "GBp" : currency}`));
    assert.doesNotMatch(html, /9,900/);
  }
  assert.match(cell({ status: "ok", latest }), /Forecast currency unavailable/);
  assert.match(cell({ status: "ok", latest }), /Inner 50\.0%: —–—/);
});

test("all unavailable/noneligible/loading/error states are explicit", () => {
  for (const [summary, label] of [
    [undefined, "Loading…"], [{ status: "not_eligible" }, "Not elegible"],
    [{ status: "unavailable" }, "Forecast unavailable"], [{ status: "error" }, "Forecast error"],
  ]) assert.ok(cell(summary).includes(label));
  assert.match(cell(undefined, "1d", "failed"), /Forecast error/);
});

test("not-eligible forecasts show only Not elegible, with no inline reason or hover text", () => {
  const html = cell({ status: "not_eligible", reason: "Verbose eligibility reason" });
  assert.equal(html.replace(/<[^>]*>/g, ""), "Not elegible");
  assert.doesNotMatch(html, /title=|Verbose eligibility reason/);
});

test("canonical ambiguity and corrupt bands surface per-Symbol reasons without rates", () => {
  for (const [status, reason] of [
    ["unavailable", "Legacy ticker-only forecast ownership is ambiguous"],
    ["error", "Forecast 1d.low1 must be finite numeric data"],
  ]) {
    const html = cell({ status, reason });
    assert.ok(html.includes(reason));
    assert.ok(html.includes(`title="${reason}"`));
    assert.doesNotMatch(html, /Hit 90d|Inner|Outer/);
  }
});

test("old latest shows stale creation date and bands with unavailable 90d rates", () => {
  const html = cell({
    status: "ok", stale: true, currency: "USD",
    latest: { ...latest, created_date: "2025-09-29" },
    hit_rate: { "1d": { resolved: 0, hit_pct_1sigma: null, hit_pct_2sigma: null } },
  });
  assert.match(html, /Stale forecast: 2025-09-29/);
  assert.match(html, /Stale: outside 90d window/);
  assert.match(html, /99\.00 USD/);
  assert.match(html, /Hit 90d: — \/ —/);
  assert.match(html, /0 resolved endpoints/);
});

const flush = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

test("cold page loads Portfolio only, visits defer Options/Forecast, caches reswitch and explicit refresh", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url) => {
    calls.push(url);
    return { ok: true, json: async () => ({ rows: [{ symbol: url }] }) };
  });
  const client = new QueryClient();
  const observer = new QueryObserver(client, symbolsViewQueryOptions("portfolio"));
  const unsubscribe = observer.subscribe(() => {});
  await flush();
  assert.deepEqual(calls, ["/api/symbols/overview?view=portfolio"]);
  observer.setOptions(symbolsViewQueryOptions("options"));
  await flush();
  observer.setOptions(symbolsViewQueryOptions("forecast"));
  await flush();
  assert.deepEqual(calls, [
    "/api/symbols/overview?view=portfolio",
    "/api/symbols/overview?view=options",
    "/api/symbols/overview?view=forecast",
  ]);
  observer.setOptions(symbolsViewQueryOptions("portfolio"));
  await flush();
  assert.equal(calls.length, 3);
  await observer.refetch();
  assert.equal(calls.length, 4);
  unsubscribe();
  client.clear();
});

test("cancellation prevents late data from crossing active tabs; failures surface and retry recovers", async (t) => {
  const first = deferred();
  let oldSignal;
  let calls = 0;
  t.mock.method(globalThis, "fetch", (url, { signal }) => {
    calls++;
    if (calls === 1) {
      oldSignal = signal;
      return first.promise;
    }
    return Promise.resolve({ ok: calls !== 2, json: async () => calls === 2 ? { error: "storage failure" } : { rows: [{ symbol: "NEW" }] } });
  });
  const client = new QueryClient();
  const observer = new QueryObserver(client, symbolsViewQueryOptions("portfolio"));
  const unsubscribe = observer.subscribe(() => {});
  assert.equal(observer.getCurrentResult().data, undefined);
  observer.setOptions(symbolsViewQueryOptions("options"));
  assert.equal(oldSignal.aborted, true);
  await flush();
  assert.match(observer.getCurrentResult().error.message, /storage failure/);
  await observer.refetch();
  first.resolve({ ok: true, json: async () => ({ rows: [{ symbol: "OLD" }] }) });
  await flush();
  assert.equal(observer.getCurrentResult().data.rows[0].symbol, "NEW");
  assert.equal(client.getQueryData(["symbols-overview", "portfolio"]), undefined);
  unsubscribe();
  client.clear();
});

test("Symbols page has no eager server data; persistent wrapper scopes KPIs and invalidates after mutations", () => {
  const page = readFileSync(new URL("../src/app/symbols/page.tsx", import.meta.url), "utf8");
  const view = readFileSync(new URL("../src/components/SymbolsView.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(page, /apiFetch|fetch\(|getData/);
  assert.match(view, /useQuery\(symbolsViewQueryOptions\(activeView\)\)/);
  assert.match(view, /activeView === "options" &&/);
  assert.match(view, /\{hasPortfolioSummary && \(/);
  assert.match(view, /onDataChanged=\{invalidateViews\}/);
  assert.match(view, /onAdded=\{invalidateViews\}/);
});

function symbolsViewHarness() {
  const client = new QueryClient();
  const observers = [];
  const unsubscribes = [];
  let mode = "portfolio", hook = 0, tableProps, addProps, refresh;
  const loadedModule = { exports: {} };
  const code = ts.transpileModule(
    readFileSync(new URL("../src/components/SymbolsView.tsx", import.meta.url), "utf8"),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } },
  ).outputText;
  vm.runInNewContext(code, {
    exports: loadedModule.exports, module: loadedModule,
    require: (name) => {
      if (name === "react") return { ...React, useState: () => [mode, (next) => { mode = next; }] };
      if (name === "@tanstack/react-query") return {
        useQueryClient: () => client,
        useQuery: (options) => {
          const index = hook++;
          if (!observers[index]) {
            observers[index] = new QueryObserver(client, options);
            unsubscribes.push(observers[index].subscribe(() => {}));
          } else observers[index].setOptions(options);
          return observers[index].getCurrentResult();
        },
      };
      if (name === "@/lib/symbols-overview-query") return { symbolsViewQueryOptions };
      if (name === "@/lib/format") return { usd: String, timeAgo: () => "now" };
      if (name === "@/components/SymbolsTable") return { default: (props) => { tableProps = props; return null; } };
      if (name === "@/components/AddSymbolForm") return { default: (props) => { addProps = props; return null; } };
      return require(name);
    },
  });
  const findRefresh = (node) => {
    if (!node || typeof node !== "object") return;
    if (Array.isArray(node)) { node.forEach(findRefresh); return; }
    if (node.type === "button" && node.props.children === "Refresh") refresh = node.props.onClick;
    findRefresh(node.props?.children);
  };
  return {
    client, observers,
    get rows() { return tableProps.rows; },
    render() {
      hook = 0;
      const tree = loadedModule.exports.default();
      findRefresh(tree);
      return renderToStaticMarkup(tree);
    },
    switchView(next) { tableProps.onViewModeChange(next); return this.render(); },
    refresh() { refresh(); },
    mutate() { tableProps.onDataChanged(); },
    add() { addProps.onAdded(); },
    close() {
      unsubscribes.forEach((unsubscribe) => unsubscribe());
      assert.equal(client.getQueryCache().find({ queryKey: ["symbols-overview", "portfolio"] }).getObserversCount(), 0);
      client.clear();
    },
  };
}

const portfolioSummary = {
  total_investment_eur: 1000, total_current_value_eur: 1200,
  net_gains_eur: 50, total_dividends_eur: 25,
};
const expectedCards = [
  ["Current Investment", "1.000,00\u00a0€"], ["Current Value", "1.200,00\u00a0€"],
  ["Unrealized P/L %", "+20.00%"], ["Realized Result", "50,00\u00a0€"],
  ["Net Dividends", "25,00\u00a0€"],
];
const kpiCards = (html) => Array.from(
  html.matchAll(/text-xs text-text-muted mb-1">([^<]+)<\/div><div class="[^"]+">([^<]+)<\/div>/g),
  ([, label, value]) => [label, value],
);

test("active Forecast mutations refresh native classification despite invalidated Portfolio/Options caches", async (t) => {
  const calls = [];
  let inventoryChanged = false;
  const forecastRows = () => historicalRows.map((row) => ({
    ...row,
    ...(inventoryChanged && row.security_id === "sold"
      ? { is_historical: false, portfolio_shares: "10" } : {}),
    ...(inventoryChanged && row.security_id === "live"
      ? { is_historical: true, portfolio_shares: "0" } : {}),
    category: "FORECAST",
  }));
  t.mock.method(globalThis, "fetch", async (url) => {
    calls.push(url);
    return { ok: true, json: async () => url.endsWith("forecast")
      ? { rows: forecastRows() } : { symbols: historicalRows } };
  });
  const view = symbolsViewHarness();
  t.after(() => view.close());
  view.render();
  await flush();
  view.switchView("options");
  await flush();
  view.render();
  assert.equal(view.rows[0].is_historical, true);
  view.switchView("forecast");
  await flush();
  view.render();
  assert.equal(view.rows.find((row) => row.security_id === "sold").is_historical, true);
  assert.equal(view.rows.find((row) => row.security_id === "short").row_source, "both");
  assert.equal(view.rows.find((row) => row.security_id === "watch").row_source, "watchlist");
  assert.equal(view.rows[0].category, "FORECAST");
  const table = symbolsTableHarness(view.rows);
  table.render();
  assert.deepEqual(renderedSymbols(table.switchView("Forecast")).sort(), ["LIVE", "UNKNOWN", "WATCH"]);

  inventoryChanged = true;
  view.mutate();
  await flush();
  view.render();
  for (const mode of ["portfolio", "options"]) {
    const cached = view.client.getQueryCache().find({ queryKey: ["symbols-overview", mode] });
    assert.equal(cached.state.isInvalidated, true);
    assert.equal(cached.isActive(), false);
    assert.equal(cached.state.data.symbols[0].is_historical, true);
    assert.equal(cached.state.data.symbols[2].is_historical, false);
  }
  assert.equal(view.rows.find((row) => row.security_id === "sold").is_historical, false);
  assert.equal(view.rows.find((row) => row.security_id === "live").is_historical, true);
  assert.equal(view.rows.find((row) => row.security_id === "short").row_source, "both");
  assert.equal(view.rows.find((row) => row.security_id === "watch").is_historical, false);
  const changed = table.render({ rows: view.rows });
  assert.deepEqual(renderedSymbols(changed).sort(), ["SOLD", "UNKNOWN", "WATCH"]);
  assert.match(changed, /3 symbols/);
  assert.match(changed, /Hide historical \(2\)/);
  assert.match(table.search("LIVE"), /0 symbols/);
  assert.match(table.render(), /No symbols match/);
  assert.deepEqual(renderedSymbols(table.search("SOLD")), ["SOLD"]);

  inventoryChanged = false;
  view.add();
  await flush();
  view.render();
  table.render({ rows: view.rows });
  assert.match(table.render(), /No symbols match/);
  assert.deepEqual(renderedSymbols(table.search("")), ["LIVE", "UNKNOWN", "WATCH"]);

  inventoryChanged = true;
  view.refresh();
  await flush();
  view.render();
  assert.equal(view.rows.find((row) => row.security_id === "sold").is_historical, false);
  assert.equal(view.rows.find((row) => row.security_id === "live").is_historical, true);
  assert.deepEqual(calls, [
    "/api/symbols/overview?view=portfolio", "/api/symbols/overview?view=options",
    "/api/symbols/overview?view=forecast", "/api/symbols/overview?view=forecast",
    "/api/symbols/overview?view=forecast", "/api/symbols/overview?view=forecast",
  ]);
});

test("a late initial Portfolio response cannot overwrite authoritative Forecast classification", async (t) => {
  const calls = [], portfolio = deferred();
  t.mock.method(globalThis, "fetch", (url) => {
    calls.push(url);
    return url.endsWith("portfolio") ? portfolio.promise : Promise.resolve({
      ok: true, json: async () => ({ rows: [
        { ...historicalRows[0], is_historical: false, portfolio_shares: "10" },
        { ...historicalRows[0], security_id: "another-exchange", row_source: "watchlist", is_historical: false },
        { ...historicalRows[0], security_id: null, row_source: undefined, is_historical: undefined, portfolio_shares: null },
      ] }),
    });
  });
  const view = symbolsViewHarness();
  t.after(() => view.close());
  view.render();
  view.switchView("forecast");
  await flush();
  portfolio.resolve({ ok: true, json: async () => ({ rows: historicalRows }) });
  await flush();
  view.render();
  assert.deepEqual(view.rows.map((row) => row.is_historical), [false, false, undefined]);
  assert.equal(view.rows[1].row_source, "watchlist");
  assert.deepEqual(calls, [
    "/api/symbols/overview?view=portfolio", "/api/symbols/overview?view=forecast",
  ]);
});

test("Forecast with no holdings-aware cache leaves unknown and zero-share watchlist rows visible", async (t) => {
  t.mock.method(globalThis, "fetch", async () => ({ ok: true, json: async () => ({ rows: [] }) }));
  const view = symbolsViewHarness();
  t.after(() => view.close());
  view.render();
  await flush();
  view.client.setQueryData(["symbols-overview", "forecast"], {
    rows: [historicalRows[3], historicalRows[4], { ...historicalRows[0], security_id: null, is_historical: false, row_source: "watchlist" }],
  });
  view.switchView("forecast");
  assert.deepEqual(view.rows.map((row) => row.is_historical), [false, undefined, false]);
});

test("shared KPI cards retain Portfolio values through lazy Options/Forecast loading and errors; Portfolio refresh updates them", async (t) => {
  const calls = [];
  const options = deferred(), forecast = deferred(), refreshed = deferred();
  t.mock.method(globalThis, "fetch", (url) => {
    calls.push(url);
    if (url.endsWith("options")) return options.promise;
    if (url.endsWith("forecast")) return forecast.promise;
    return calls.length === 1
      ? Promise.resolve({ ok: true, json: async () => ({ portfolio_summary: portfolioSummary }) })
      : refreshed.promise;
  });
  const view = symbolsViewHarness();
  t.after(() => view.close());
  assert.deepEqual(kpiCards(view.render()), []);
  await flush();
  assert.deepEqual(kpiCards(view.render()), expectedCards);
  assert.deepEqual(calls, ["/api/symbols/overview?view=portfolio"]);

  assert.deepEqual(kpiCards(view.switchView("options")), expectedCards);
  assert.match(view.render(), /Loading options/);
  options.resolve({ ok: true, json: async () => ({ portfolio_summary: { total_investment_eur: 0 } }) });
  await flush();
  assert.deepEqual(kpiCards(view.render()), expectedCards);

  assert.deepEqual(kpiCards(view.switchView("forecast")), expectedCards);
  assert.match(view.render(), /Loading forecast/);
  forecast.reject(new Error("Forecast unavailable"));
  await flush();
  const failed = view.render();
  assert.match(failed, /Could not load forecast/);
  assert.deepEqual(kpiCards(failed), expectedCards);
  assert.deepEqual(calls, [
    "/api/symbols/overview?view=portfolio", "/api/symbols/overview?view=options",
    "/api/symbols/overview?view=forecast",
  ]);

  assert.deepEqual(kpiCards(view.switchView("portfolio")), expectedCards);
  await flush();
  assert.equal(calls.length, 3);
  view.refresh();
  assert.deepEqual(kpiCards(view.render()), expectedCards);
  refreshed.resolve({ ok: true, json: async () => ({
    portfolio_summary: { ...portfolioSummary, total_current_value_eur: 1500 },
  }) });
  await flush();
  assert.deepEqual(kpiCards(view.render()), [
    expectedCards[0], ["Current Value", "1.500,00\u00a0€"],
    ["Unrealized P/L %", "+50.00%"], ...expectedCards.slice(3),
  ]);
  assert.equal(calls.length, 4);
});

test("cache subscription keeps KPIs current while mutations refetch only the active view and stale Portfolio refreshes on return", async (t) => {
  const calls = [];
  const refreshed = deferred();
  t.mock.method(globalThis, "fetch", (url) => {
    calls.push(url);
    if (url.endsWith("portfolio") && calls.length > 1) return refreshed.promise;
    return Promise.resolve({ ok: true, json: async () => url.endsWith("portfolio")
      ? { portfolio_summary: portfolioSummary } : { rows: [] } });
  });
  const view = symbolsViewHarness();
  t.after(() => view.close());
  view.render();
  await flush();
  view.switchView("options");
  await flush();
  assert.equal(view.client.getQueryCache().find({ queryKey: ["symbols-overview", "portfolio"] }).isActive(), false);
  const updated = { ...portfolioSummary, total_dividends_eur: 75 };
  view.client.setQueryData(["symbols-overview", "portfolio"], { portfolio_summary: updated });
  assert.equal(view.observers[1].getCurrentResult().data.portfolio_summary.total_dividends_eur, 75);
  const updatedCards = [...expectedCards.slice(0, 4), ["Net Dividends", "75,00\u00a0€"]];
  assert.deepEqual(kpiCards(view.render()), updatedCards);
  for (const mutate of [() => view.mutate(), () => view.add()]) {
    mutate();
    await flush();
    assert.deepEqual(kpiCards(view.render()), updatedCards);
  }
  assert.deepEqual(calls, [
    "/api/symbols/overview?view=portfolio", "/api/symbols/overview?view=options",
    "/api/symbols/overview?view=options", "/api/symbols/overview?view=options",
  ]);
  assert.deepEqual(kpiCards(view.switchView("portfolio")), updatedCards);
  assert.match(view.render(), /Loading portfolio/);
  refreshed.resolve({ ok: true, json: async () => ({ portfolio_summary: portfolioSummary }) });
  await flush();
  assert.deepEqual(kpiCards(view.render()), expectedCards);
  assert.equal(calls.length, 5);
});

test("missing or unavailable Portfolio summary never invents cards on any view", async (t) => {
  for (const portfolio of [{ rows: [] }, { portfolio_summary: null }, { portfolio_summary: {} }, { error: "Portfolio unavailable" }]) {
    const calls = [];
    t.mock.method(globalThis, "fetch", async (url) => {
      calls.push(url);
      return { ok: true, json: async () => url.endsWith("portfolio") ? portfolio : {
        portfolio_summary: portfolioSummary,
      } };
    });
    const view = symbolsViewHarness();
    try {
      for (const mode of ["portfolio", "options", "forecast"]) {
        if (mode === "portfolio") view.render();
        else view.switchView(mode);
        await flush();
        assert.deepEqual(kpiCards(view.render()), []);
      }
      assert.equal(calls.length, 3);
    } finally {
      view.close();
      t.mock.restoreAll();
    }
  }
});
