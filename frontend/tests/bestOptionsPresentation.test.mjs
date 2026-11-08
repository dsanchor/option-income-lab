import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(join(root, "package.json"));
const ts = require("typescript");
const React = require("react");

function loader(react = React, overrides = {}) {
  const cache = new Map();
  const load = (relative) => {
    if (cache.has(relative)) return cache.get(relative);
    const source = readFileSync(join(root, "src", relative), "utf8");
    const compiled = ts.transpileModule(source, {
      compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    const loadedModule = { exports: {} };
    const injected = relative === "components/BestOptionsView.tsx"
      ? "\nmodule.exports.testComponents = { SnapshotResults, OptionsTable, RowDetails, NearestMissPanel };"
      : relative === "components/OptionsScreenerView.tsx" ? "\nmodule.exports.testComponents = { ResultsTable };" : "";
    new Function("require", "module", "exports", compiled + injected)((name) => {
      if (name === "react") return react;
      if (name in overrides) return overrides[name];
      if (name.startsWith("@/")) {
        const path = name.slice(2);
        const suffix = ["lib/best-options-presentation", "lib/badges"].includes(path) ? ".ts" : ".tsx";
        return load(path + suffix);
      }
      return require(name);
    }, loadedModule, loadedModule.exports);
    cache.set(relative, loadedModule.exports);
    return loadedModule.exports;
  };
  return load;
}

const load = loader();
const helpers = load("lib/best-options-presentation.ts");
const presentation = load("components/OptionsPresentation.tsx");
const window = { min: 0, max: 45, system_cap: 45, source: "config", timezone: "UTC" };
const draft = (changes = {}) => ({ ...helpers.initialBestOptionsDraft(window), ...changes });
const row = (strike, changes = {}) => ({
  strike, expiration: "20261201", dte: 20, bid: 1, ask: 2, mid: 1.5, iv: 0.3,
  delta: -0.3, abs_delta: 0.3, open_interest: 100, premium_pct: 1.5, annualized_return_pct: 20,
  effective_min_pct: 1, effective_wait_pct: 0.5, collateral: 10000,
  score: 72.4, color: "green", label: "Preferred", components: { liquidity: 0.8 },
  components_missing: [], weight_basis: 1,
  gates: { tradability: "pass", delta_band: "pass", earnings_span: "unknown" },
  flags: ["earnings_date_unknown"], quote_asof: "2026-11-08T10:00:00Z", stale: false,
  ...changes,
});
const side = (rows, changes = {}) => ({
  rows, total: rows.length, truncated: false, excluded_by_delta_band: 2, nearest_miss: null, ...changes,
});
const response = (calls = [row(95)], puts = [row(105)]) => ({
  symbol: "XNYS:TEST", status: "ok",
  parameters: { dte: window, underlying: { price: 100, source: "chain" }, category: { label: "Balanced" } },
  calls: side(calls), puts: side(puts), cache: { generation: 1, chain_stale: false },
});
const strikes = (rows) => rows.map((r) => r.strike);

test("every numeric bound filters actual backend fields inclusively; zeros, signed gaps and absent metrics are not truthy shortcuts", () => {
  const rows = [
    row(90, { dte: 0, abs_delta: 0, delta: 0, open_interest: 0, annualized_return_pct: 0 }),
    row(100, { dte: 10, abs_delta: 0.2, delta: -0.2, open_interest: 10, annualized_return_pct: 10 }),
    row(110, { dte: 30, abs_delta: 0.5, delta: -0.5, open_interest: 500, annualized_return_pct: 30 }),
    row(120, { dte: null, abs_delta: null, delta: null, open_interest: null, annualized_return_pct: null }),
  ];
  for (const [filters, expected] of [
    [{ minAnnualizedReturn: 0 }, [90, 100, 110]],
    [{ minAnnualizedReturn: 10 }, [100, 110]],
    [{ minAbsDelta: 0 }, [90, 100, 110]],
    [{ minAbsDelta: 0.2 }, [100, 110]],
    [{ maxAbsDelta: 0 }, [90]],
    [{ minAbsDelta: 0.2, maxAbsDelta: 0.2 }, [100]],
    [{ minDte: 0, maxDte: 0 }, [90]],
    [{ minDte: 10 }, [100, 110]],
    [{ maxDte: 10 }, [90, 100]],
    [{ minOi: 0 }, [90, 100, 110]],
    [{ minOi: 500 }, [110]],
    [{ minGapPct: -10, maxGapPct: -10 }, [90]],
    [{ minGapPct: 0 }, [100, 110, 120]],
    [{ maxGapPct: 0 }, [90, 100]],
    [{ minGapPct: 10, maxGapPct: 20 }, [110, 120]],
  ]) {
    assert.deepEqual(strikes(helpers.filterBestOptionsRows(rows, [], filters, 100)), expected, JSON.stringify(filters));
  }
  for (const price of [null, 0, NaN, Infinity]) {
    assert.equal(helpers.filterBestOptionsRows(rows, [], { minGapPct: 0 }, price).length, 0);
    assert.equal(helpers.filterBestOptionsRows(rows, [], {}, price).length, 4);
  }
  const nonfinite = row(90, { annualized_return_pct: NaN, abs_delta: Infinity, open_interest: NaN });
  for (const filter of [{ minAnnualizedReturn: 0 }, { maxAbsDelta: 1 }, { minOi: 0 }])
    assert.equal(helpers.filterBestOptionsRows([nonfinite], [], filter, 100).length, 0);
});

test("profiles use the authoritative label (not score/color), default Preferred + Acceptable, and no selection means All", () => {
  const rows = [row(90, { color: "red", score: 0 }), row(100, { label: "Acceptable" }), row(110, { label: "Avoid", score: 100 })];
  assert.deepEqual(strikes(helpers.filterBestOptionsRows(rows, presentation.DEFAULT_PREFERENCES, {}, 100)), [90, 100]);
  assert.deepEqual(strikes(helpers.filterBestOptionsRows(rows, ["Avoid"], {}, 100)), [110]);
  assert.deepEqual(strikes(helpers.filterBestOptionsRows(rows, [], {}, 100)), [90, 100, 110]);
  assert.equal(helpers.filterBestOptionsRows(rows, [], {}, 100)[0], rows[0], "row truth is retained, not rebuilt");
});

test("empty filters clear optional bounds; DTE defaults honor the available snapshot; invalid inputs cannot silently broaden results", () => {
  const available = { ...window, min: 7, max: 30 };
  const empty = Object.fromEntries(Object.keys(draft()).map((k) => [k, ""]));
  assert.deepEqual(helpers.parseBestOptionsFilters(empty, available), { filters: { minDte: 7, maxDte: 30 }, error: null });
  assert.equal(helpers.initialBestOptionsDraft(available).maxDte, "30");
  assert.equal(helpers.initialBestOptionsDraft({ ...window, max: 60 }).maxDte, "45");
  for (const change of [
    { minAnnualizedReturn: "invalid" }, { minOi: "Infinity" }, { minOi: "-1" },
    { minAbsDelta: "-0.1" }, { maxAbsDelta: "1.1" }, { minAbsDelta: "0.5", maxAbsDelta: "0.3" },
    { minDte: "30", maxDte: "10" }, { minDte: "-1" }, { maxDte: "46" }, { minDte: "0.1" },
    { minGapPct: "-101" }, { maxGapPct: "201" }, { minGapPct: "10", maxGapPct: "-10" },
  ]) assert.ok(helpers.parseBestOptionsFilters(draft(change), window).error, JSON.stringify(change));
  assert.equal(helpers.parseBestOptionsFilters(draft({ minAnnualizedReturn: "-1", minAbsDelta: "0", minOi: "0.5", minGapPct: "-100", maxGapPct: "200" }), window).error, null);
});

test("both numeric parsers accept only finite decimal/scientific drafts and distinguish blank from invalid", () => {
  for (const parse of [
    (value) => helpers.parseBestOptionsFilters(value, window),
    (value) => helpers.parseOptionsNumericFilters(value, { min: 0, max: 45 }),
  ]) {
    for (const value of ["0x10", "0b10", "0o10", "1e", "1e+", "Infinity", "NaN", "1e309", ".", "+", "1 2"]) {
      assert.ok(parse(draft({ minAnnualizedReturn: value })).error, value);
    }
    for (const [value, expected] of [
      ["0", 0], ["-0", -0], [".5", 0.5], ["1.", 1], ["+1.5", 1.5],
      ["1e2", 100], ["-1.2e-3", -0.0012], ["  +1E+2  ", 100],
    ]) {
      const parsed = parse(draft({ minAnnualizedReturn: value }));
      assert.equal(parsed.error, null, value);
      assert.equal(parsed.filters.minAnnualizedReturn, expected, value);
    }
    for (const value of ["", " \t\n "]) {
      assert.deepEqual(parse(draft({ minAnnualizedReturn: value })), {
        filters: { minDte: 0, maxDte: 45 }, error: null,
      });
    }
  }
});

test("sorts are stable, non-mutating, missing last in both directions, and use |delta| and signed gap rather than formatted values", () => {
  const rows = [row(110, { abs_delta: 0.1 }), row(90, { abs_delta: 0.4 }), row(100, { abs_delta: 0.1 }), row(120, { abs_delta: null }), row(130, { abs_delta: NaN })];
  assert.deepEqual(strikes(helpers.sortBestOptionsRows(rows, "abs_delta", "asc", 100)), [110, 100, 90, 120, 130]);
  assert.deepEqual(strikes(helpers.sortBestOptionsRows(rows, "abs_delta", "desc", 100)), [90, 110, 100, 120, 130]);
  assert.deepEqual(strikes(helpers.sortBestOptionsRows(rows, "gap_pct", "asc", 100)), [90, 100, 110, 120, 130]);
  assert.deepEqual(strikes(helpers.sortBestOptionsRows(rows, "gap_pct", "desc", 100)), [130, 120, 110, 100, 90]);
  assert.deepEqual(strikes(helpers.sortBestOptionsRows(rows, "gap_pct", "desc", null)), strikes(rows));
  assert.equal(helpers.sortBestOptionsRows(rows, "default", "asc", 100), rows);
  for (const field of ["dte", "premium_pct", "annualized_return_pct", "open_interest", "score", "strike"]) {
    const values = [row(91, { [field]: 2 }), row(92, { [field]: null }), row(93, { [field]: 1 })];
    assert.deepEqual(helpers.sortBestOptionsRows(values, field, "asc", 100), [values[2], values[0], values[1]]);
    assert.deepEqual(helpers.sortBestOptionsRows(values, field, "desc", 100), [values[0], values[2], values[1]]);
  }
  const dates = [row(1, { expiration: "20261202" }), row(2, { expiration: "" }), row(3, { expiration: "2026-12-01" })];
  assert.deepEqual(strikes(helpers.sortBestOptionsRows(dates, "expiration", "asc", 100)), [3, 1, 2]);
  assert.deepEqual(strikes(helpers.sortBestOptionsRows(dates, "expiration", "desc", 100)), [1, 3, 2]);
  assert.deepEqual(strikes(rows), [110, 90, 100, 120, 130]);
});

function elements(node) {
  if (!node || typeof node !== "object") return [];
  if (Array.isArray(node)) return node.flatMap(elements);
  return [node, ...elements(node.props?.children)];
}
function text(node) {
  if (node == null || typeof node === "boolean") return "";
  if (Array.isArray(node)) return node.map(text).join("");
  return typeof node === "object" ? text(node.props?.children) : String(node);
}

function harness(componentName = "SnapshotResults") {
  let hooks = [], cursor = 0, pending = [], props;
  const timers = new Map();
  let timerId = 0;
  const previous = { fetch: globalThis.fetch, setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout };
  const requests = [];
  globalThis.fetch = async (...args) => {
    requests.push(args);
    if (componentName !== "OptionsScreenerView") throw new Error("Presentation must not fetch");
    const params = new URL(args[0], "http://frontend").searchParams;
    const body = args[0] === "/api/symbols/overview"
      ? { symbols: [{ symbol: "XNYS:TEST", screener_eligible: true }] }
      : {
        side: params.get("side"), rows: [], nearest_miss: [],
        pagination: { total_matching: 0, returned: 0, offset: Number(params.get("offset")), limit: Number(params.get("limit")), has_more: false },
        symbols: { total: 1, loaded: 1, loaded_fresh: 1, loaded_stale: 0, pending: 0, error: 0 },
      };
    return { ok: true, json: async () => body };
  };
  globalThis.setTimeout = (fn, delay) => { timers.set(++timerId, { fn, delay }); return timerId; };
  globalThis.clearTimeout = (id) => timers.delete(id);
  const same = (a, b) => a && a.length === b.length && a.every((v, i) => v === b[i]);
  const react = {
    ...React,
    useState(initial) {
      const i = cursor++;
      if (!(i in hooks)) hooks[i] = typeof initial === "function" ? initial() : initial;
      return [hooks[i], (value) => { hooks[i] = typeof value === "function" ? value(hooks[i]) : value; }];
    },
    useRef(initial) {
      const i = cursor++;
      if (!(i in hooks)) hooks[i] = { current: initial };
      return hooks[i];
    },
    useCallback(fn, deps) {
      const i = cursor++;
      if (!same(hooks[i]?.deps, deps)) hooks[i] = { deps, value: fn };
      return hooks[i].value;
    },
    useMemo(fn, deps) {
      const i = cursor++;
      if (!same(hooks[i]?.deps, deps)) hooks[i] = { deps, value: fn() };
      return hooks[i].value;
    },
    useEffect(effect, deps) {
      const i = cursor++;
      if (!same(hooks[i]?.deps, deps)) pending.push(() => {
        hooks[i]?.cleanup?.();
        hooks[i] = { deps, cleanup: effect() };
      });
    },
  };
  const localLoad = loader(react, {
    "@/components/BestOptionsParams": { default: () => null },
    "@/components/ContractValidationAction": { default: () => null },
    "@/components/MultiSelect": { default: () => null },
  });
  const loaded = localLoad(`components/${componentName === "OptionsScreenerView" ? "OptionsScreenerView" : "BestOptionsView"}.tsx`);
  const components = loaded.testComponents;
  const component = componentName === "OptionsScreenerView" ? loaded.default : components[componentName];
  const shared = localLoad("components/OptionsPresentation.tsx");
  const expand = (node) => {
    if (Array.isArray(node)) return node.flatMap(expand);
    if (!node || typeof node !== "object") return [];
    if (Object.values(shared).includes(node.type) && typeof node.type === "function")
      return [node, ...expand(node.type(node.props))];
    return [node, ...expand(node.props?.children)];
  };
  return {
    requests, components,
    render(next = props) {
      props = next;
      cursor = 0;
      const tree = expand(component(props));
      for (const effect of pending.splice(0)) effect();
      return tree;
    },
    apply() {
      for (const [id, timer] of [...timers]) {
        assert.equal(timer.delay, 400);
        timers.delete(id);
        timer.fn();
      }
      return this.render();
    },
    close() {
      for (const hook of hooks) hook?.cleanup?.();
      Object.assign(globalThis, previous);
      assert.equal(timers.size, 0);
      if (componentName !== "OptionsScreenerView") assert.deepEqual(requests, []);
    },
  };
}
const table = (h, tree) => tree.find((e) => e.type === h.components.OptionsTable);
const pager = (tree) => tree.find((e) => typeof e.type === "function" && "hasMore" in e.props);
const profile = (tree) => tree.find((e) => e.props?.allLabel === "All Profiles");
const input = (tree, label) => tree.find((e) => e.type === "input" && e.props["aria-label"] === label);
const button = (tree, label) => tree.find((e) => e.type === "button" && text(e).trim() === label);
const edit = (h, label, value) => {
  input(h.render(), label).props.onChange({ target: { value } });
  h.render();
  return h.apply();
};

test("runtime tabs show one side, controls omit Symbols/Share Availability, and every numeric control applies locally", () => {
  const h = harness();
  try {
    const data = response([
      row(90, { dte: 0, abs_delta: 0, open_interest: 0, annualized_return_pct: 0 }),
      row(100, { dte: 10, abs_delta: 0.2, open_interest: 10, annualized_return_pct: 10 }),
      row(110, { dte: 30, abs_delta: 0.5, open_interest: 500, annualized_return_pct: 30 }),
    ], [row(105, { label: "Avoid", color: "red" }), row(115)]);
    let tree = h.render({ symbol: data.symbol, response: data });
    assert.equal(tree.filter((e) => e.type === h.components.OptionsTable).length, 1);
    assert.equal(table(h, tree).props.side, "call");
    assert.equal(profile(tree).props.selected.join(","), "Preferred,Acceptable");
    assert.equal(tree.filter((e) => e.props?.allLabel).length, 1);
    assert.ok(!text(tree).includes("Share Availability"));
    for (const [label, value, expected] of [
      ["Minimum annualized return percentage", "10", [100, 110]],
      ["Minimum absolute delta", "0.2", [100, 110]],
      ["Maximum absolute delta", "0.2", [90, 100]],
      ["Minimum DTE", "10", [100, 110]],
      ["Maximum DTE", "10", [90, 100]],
      ["Minimum open interest", "500", [110]],
      ["Minimum gap percentage (-100 to 200)", "0", [100, 110]],
      ["Maximum gap percentage (-100 to 200)", "0", [90, 100]],
    ]) {
      tree = edit(h, label, value);
      assert.deepEqual(strikes(table(h, tree).props.rows), expected, label);
      edit(h, label, "");
    }
    button(h.render(), "Cash-Secured Puts").props.onClick();
    tree = h.render();
    assert.equal(table(h, tree).props.side, "put");
    assert.deepEqual(strikes(table(h, tree).props.rows), [115]);
    profile(tree).props.onChange([]);
    tree = h.render();
    assert.deepEqual(strikes(table(h, tree).props.rows), [105, 115]);
    assert.equal(tree.find((e) => e.type === "button" && e.props.role === "tab" && e.props["aria-selected"]).props.children, "Cash-Secured Puts");
    tree = edit(h, "Maximum gap percentage (-100 to 200)", "-1");
    assert.equal(table(h, tree).props.rows.length, 0);
    assert.equal(pager(tree).props.total, 0);
    tree = edit(h, "Minimum absolute delta", "2");
    assert.ok(tree.some((e) => e.props?.role === "alert"));
    assert.equal(table(h, tree).props.rows.length, 0);
  } finally { h.close(); }
});

test("runtime pagination resets on filter/tab/limit/sort and clamps immediately when a refreshed snapshot shrinks", () => {
  const h = harness();
  try {
    const data = response(Array.from({ length: 76 }, (_, i) => row(80 + i, { annualized_return_pct: i })));
    let tree = h.render({ symbol: data.symbol, response: data });
    tree.find((e) => e.type === "select").props.onChange({ target: { value: "25" } });
    tree = h.render();
    assert.equal(table(h, tree).props.rows.length, 25);
    const next = () => {
      button(h.render(), "Next").props.onClick();
      return h.render();
    };
    tree = next();
    assert.equal(pager(tree).props.offset, 25);
    table(h, tree).props.onSort("annualized_return_pct");
    tree = h.render();
    assert.equal(pager(tree).props.offset, 0);
    assert.equal(table(h, tree).props.rows[0].strike, 155);
    table(h, tree).props.onSort("annualized_return_pct");
    tree = h.render();
    assert.equal(table(h, tree).props.rows[0].strike, 80);
    next();
    tree = edit(h, "Minimum annualized return percentage", "75");
    assert.equal(pager(tree).props.offset, 0);
    assert.equal(pager(tree).props.total, 1);
    edit(h, "Minimum annualized return percentage", "");
    next();
    profile(h.render()).props.onChange([]);
    assert.equal(pager(h.render()).props.offset, 0);
    next();
    button(h.render(), "Cash-Secured Puts").props.onClick();
    assert.equal(pager(h.render()).props.offset, 0);
    button(h.render(), "Covered Calls").props.onClick();
    next();
    h.render().find((e) => e.type === "select").props.onChange({ target: { value: "50" } });
    assert.equal(pager(h.render()).props.offset, 0);
    next();
    const updated = { ...data, calls: side(data.calls.rows.slice(0, 3)), cache: { generation: 2 } };
    tree = h.render({ symbol: data.symbol, response: updated });
    assert.equal(pager(tree).props.offset, 0);
    assert.equal(table(h, tree).props.rows.length, 3);
    assert.equal(pager(tree).props.hasMore, false);
    assert.equal(table(h, tree).props.sort, "annualized_return_pct", "refresh retains presentation settings");
    h.render({ symbol: data.symbol, response: data });
    assert.equal(pager(h.render()).props.offset, 0, "later growth cannot resurrect the stale page");
    tree = h.render({ symbol: data.symbol, response: updated });
    button(tree, "Default order").props.onClick();
    assert.deepEqual(strikes(table(h, h.render()).props.rows), strikes(updated.calls.rows));
    button(h.render(), "Cash-Secured Puts").props.onClick();
    tree = h.render({ symbol: data.symbol, response: { ...updated, puts: side([row(200)]) } });
    assert.equal(table(h, tree).props.side, "put", "refresh retains the selected tab");
    assert.deepEqual(strikes(table(h, tree).props.rows), [200]);
  } finally { h.close(); }
});

test("Best Options text drafts retain applied rows and page on invalid numbers, and only real clears remove optional bounds", () => {
  const h = harness();
  try {
    const data = response(Array.from({ length: 60 }, (_, i) => row(80 + i, { annualized_return_pct: i })));
    let tree = h.render({ symbol: data.symbol, response: data });
    tree.find((e) => e.type === "select").props.onChange({ target: { value: "25" } });
    tree = edit(h, "Minimum annualized return percentage", "10");
    button(tree, "Next").props.onClick();
    tree = h.render();
    const appliedRows = table(h, tree).props.rows;
    for (const [label, value] of [
      ["Minimum annualized return percentage", "0x10"],
      ["Minimum annualized return percentage", "0b10"],
      ["Minimum annualized return percentage", "0o10"],
      ["Minimum annualized return percentage", "1e"],
      ["Minimum annualized return percentage", "-"],
      ["Minimum annualized return percentage", "Infinity"],
      ["Minimum annualized return percentage", "NaN"],
      ["Maximum absolute delta", "1.1"],
      ["Maximum DTE", "10"],
      ["Maximum gap percentage (-100 to 200)", "-11"],
    ]) {
      if (label === "Maximum DTE") edit(h, "Minimum DTE", "20");
      if (label.startsWith("Maximum gap")) edit(h, "Minimum gap percentage (-100 to 200)", "-10");
      const before = h.render();
      const rows = table(h, before).props.rows;
      const offset = pager(before).props.offset;
      tree = edit(h, label, value);
      assert.equal(input(tree, label).props.type, "text", "invalid drafts must not undergo native number sanitization");
      assert.equal(input(tree, label).props.value, value);
      assert.ok(tree.some((e) => e.props?.role === "alert"), `${label}: ${value}`);
      assert.deepEqual(table(h, tree).props.rows, rows);
      assert.equal(pager(tree).props.offset, offset);
      tree = edit(h, label, label === "Minimum annualized return percentage" ? "10" : "");
    }
    tree = edit(h, "Minimum DTE", "");
    tree = edit(h, "Minimum gap percentage (-100 to 200)", "");
    assert.equal(pager(tree).props.total, 50);
    tree = edit(h, "Minimum annualized return percentage", "");
    assert.ok(!tree.some((e) => e.props?.role === "alert"));
    assert.equal(pager(tree).props.total, 60);
    assert.equal(pager(tree).props.offset, 0);
    assert.notDeepEqual(table(h, tree).props.rows, appliedRows);
  } finally { h.close(); }
});

test("runtime table preserves exact validation snapshot, backend colors/details, honest cap/counts, and expansion identity through reordering/filtering/tabs", () => {
  const h = harness("OptionsTable");
  try {
    const rows = [row(90), row(110, { color: "red", label: "Avoid", score: 2 })];
    const props = {
      symbol: "XNYS:TEST", side: "call", data: side(rows, { total: 500, truncated: true, no_shares_held: true, nearest_miss: { description: "Backend reason" } }),
      rows, category: "Balanced", chainStale: true, underlyingPrice: 100, sort: "default", dir: "desc", onSort() {},
    };
    let tree = h.render(props);
    assert.match(text(tree), /Shown: 2 · available snapshot: 2 of 500/);
    assert.match(text(tree), /capped at 400/);
    assert.ok(tree.some((e) => e.props?.role === "note" && text(e).includes("0 shares held")));
    assert.equal(tree.find((e) => e.type === h.components.NearestMissPanel).props.nearestMiss.description, "Backend reason");
    const validations = tree.filter((e) => e.props?.source === "best_options");
    assert.equal(validations.length, 2);
    assert.deepEqual(validations[0].props.displayedSnapshot, {
      color: "green", score: 72.4, premium_pct: 1.5, annualized_return_pct: 20,
    });
    assert.equal(validations[0].props.symbol, props.symbol);
    assert.equal(validations[0].props.side, "call");
    assert.equal(validations[0].props.strike, 90);
    assert.equal(validations[0].props.expiration, rows[0].expiration);
    assert.ok(tree.some((e) => e.type === "td" && e.props.title === "Δ -0.300" && e.props.children === "0.300"));
    assert.ok(tree.some((e) => e.type === "td" && e.props.children === "-10.0%"));
    assert.ok(tree.some((e) => e.props?.color === "red" && e.props?.label === "Avoid"));
    tree.find((e) => e.type === "button" && e.props["aria-expanded"] === false).props.onClick();
    tree = h.render({ ...props, rows: [...rows].reverse() });
    assert.equal(tree.find((e) => e.type === h.components.RowDetails).props.row, rows[0]);
    assert.equal(tree.filter((e) => e.type === "button" && "aria-expanded" in e.props)[0].props["aria-expanded"], false);
    tree = h.render({ ...props, rows: [rows[1]] });
    assert.ok(!tree.some((e) => e.type === h.components.RowDetails));
    tree = h.render({ ...props, side: "put" });
    assert.ok(!tree.some((e) => e.type === h.components.RowDetails));
    assert.ok(!tree.some((e) => e.props?.role === "note"));
    tree = h.render(props);
    assert.equal(tree.find((e) => e.type === h.components.RowDetails).props.row, rows[0]);
    const detail = h.components.RowDetails({ row: rows[0], underlyingPrice: 100 });
    assert.match(text(detail), /Gap \$: -10.00/);
    assert.match(text(detail), /Score components/);
    assert.match(text(detail), /Quote as of:/);
    assert.equal(elements(detail).filter((e) => e.props?.status).length, 3);
    assert.equal(elements(detail).find((e) => e.props?.label === "Earnings").props.status, "unknown");
    const miss = h.components.NearestMissPanel({ nearestMiss: props.data.nearest_miss });
    assert.match(text(miss), /Backend reason/);
  } finally { h.close(); }
});

test("runtime stale/error/drift metadata remains visible, and configured generation bounds cannot promise unavailable contracts", () => {
  const h = harness();
  try {
    const data = response();
    data.parameters.dte = { ...window, min: 5, max: 30 };
    data.status = "stale";
    data.cache = { generation: 2, chain_stale: true, refreshing: true, inputs_drift: ["category"], chain_refresh_error: "Provider failed" };
    let tree = h.render({ symbol: data.symbol, response: data });
    assert.equal(input(tree, "Minimum DTE").props.value, "5");
    assert.equal(input(tree, "Maximum DTE").props.value, "30");
    assert.match(text(tree), /Snapshot \/ chain is stale/);
    assert.match(text(tree), /previous snapshot/);
    assert.match(text(tree), /category/);
    assert.match(text(tree), /Provider failed/);
    tree = edit(h, "Maximum DTE", "45");
    assert.match(text(tree), /snapshot's configured 5–30 day window/);
    assert.equal(table(h, tree).props.rows.length, 1, "invalid drafts retain the previous applied snapshot filters");
    tree = edit(h, "Maximum DTE", "");
    assert.equal(table(h, tree).props.rows.length, 1);
  } finally { h.close(); }
});

test("Screener still uses the shared controls with its original query defaults, debounce, All convention, tab/sort/page resets and symbol/share dropdowns", async () => {
  const h = harness("OptionsScreenerView");
  const settle = () => new Promise((resolve) => setImmediate(resolve));
  const query = () => new URL(h.requests.filter(([url]) => url.startsWith("/api/screener/options?")).at(-1)[0], "http://frontend").searchParams;
  try {
    h.render({});
    await settle();
    let tree = h.render();
    assert.equal(query().get("preferences"), "Preferred,Acceptable");
    assert.equal(query().get("dte_min"), "0");
    assert.equal(query().get("dte_max"), "45");
    assert.equal(query().get("limit"), "100");
    assert.deepEqual(tree.filter((e) => e.props?.allLabel).map((e) => e.props.allLabel), ["All Profiles", "All Symbols", "Show All"]);
    for (const [label, value] of [
      ["Minimum annualized return percentage", "0"], ["Minimum absolute delta", "0"], ["Maximum absolute delta", "0.5"],
      ["Minimum DTE", "5"], ["Maximum DTE", "30"], ["Minimum open interest", "0"],
      ["Minimum gap percentage (-100 to 200)", "-10"], ["Maximum gap percentage (-100 to 200)", "5"],
    ]) {
      input(h.render(), label).props.onChange({ target: { value } });
      h.render();
    }
    assert.equal(query().get("min_gap_pct"), null, "numeric edits remain drafts before debounce");
    h.apply();
    await settle();
    tree = h.render();
    for (const [key, value] of Object.entries({
      min_annualized_return_pct: "0", min_abs_delta: "0", max_abs_delta: "0.5",
      dte_min: "5", dte_max: "30", min_open_interest: "0", min_gap_pct: "-10", max_gap_pct: "5",
    })) assert.equal(query().get(key), value);
    profile(tree).props.onChange([]);
    h.render();
    await settle();
    assert.equal(query().get("preferences"), "Preferred,Acceptable,Avoid");
    let results = h.render().find((e) => e.type === h.components.ResultsTable);
    results.props.onPage(100);
    h.render();
    await settle();
    assert.equal(query().get("offset"), "100");
    results = h.render().find((e) => e.type === h.components.ResultsTable);
    results.props.onSort("abs_delta");
    h.render();
    await settle();
    assert.equal(query().get("offset"), "0");
    assert.equal(query().get("sort"), "abs_delta");
    assert.equal(query().get("dir"), "desc");
    h.render().find((e) => e.type === h.components.ResultsTable).props.onSort("abs_delta");
    h.render();
    await settle();
    assert.equal(query().get("dir"), "asc");
    button(h.render(), "Cash-Secured Puts").props.onClick();
    tree = h.render();
    await settle();
    assert.equal(query().get("side"), "put");
    assert.equal(query().get("share_availability"), null);
    assert.deepEqual(tree.filter((e) => e.props?.allLabel).map((e) => e.props.allLabel), ["All Profiles", "All Symbols"]);
    assert.ok(h.requests.every(([url, options]) =>
      (url === "/api/symbols/overview" || url.startsWith("/api/screener/options?")) && !options?.method));
  } finally { h.close(); }
});

test("Screener rejects invalid text, nonfinite numbers and crossed bounds without fetching or clearing applied filters", async () => {
  const h = harness("OptionsScreenerView");
  const settle = () => new Promise((resolve) => setImmediate(resolve));
  const requests = () => h.requests.filter(([url]) => url.startsWith("/api/screener/options?"));
  const query = () => new URL(requests().at(-1)[0], "http://frontend").searchParams;
  const bounds = [
    ["Minimum annualized return percentage", "10", "min_annualized_return_pct", ["0x10", "0b10", "0o10", "1e", "-", "NaN"]],
    ["Minimum absolute delta", "0.2", "min_abs_delta", ["1e", "-0.1", "0.6"]],
    ["Maximum absolute delta", "0.5", "max_abs_delta", ["Infinity", "1.1", "0.1"]],
    ["Minimum DTE", "5", "dte_min", ["1e", "-1", "0.5", "31"]],
    ["Maximum DTE", "30", "dte_max", ["1e", "4"]],
    ["Minimum open interest", "100", "min_open_interest", ["1e", "Infinity", "-1"]],
    ["Minimum gap percentage (-100 to 200)", "-10", "min_gap_pct", ["1e", "-101", "11"]],
    ["Maximum gap percentage (-100 to 200)", "10", "max_gap_pct", ["1e", "201", "-11"]],
  ];
  try {
    h.render({});
    await settle();
    h.render();
    for (const [label, valid] of bounds) {
      input(h.render(), label).props.onChange({ target: { value: valid } });
      h.render();
    }
    h.apply();
    await settle();
    h.render();
    for (const [label, valid, key, invalids] of bounds) {
      for (const invalid of invalids) {
        const before = requests().length;
        const appliedQuery = query().toString();
        const tree = edit(h, label, invalid);
        await settle();
        assert.equal(input(tree, label).props.type, "text");
        assert.equal(input(tree, label).props.value, invalid);
        assert.ok(tree.some((e) => e.props?.role === "alert"), `${label}: ${invalid}`);
        assert.equal(requests().length, before, "an invalid numeric draft must not trigger a broader request");
        assert.equal(query().toString(), appliedQuery);
        assert.equal(query().get(key), valid);
        edit(h, label, valid);
        await settle();
        h.render();
      }
    }
    for (const [label, , key] of bounds) {
      const tree = edit(h, label, "");
      await settle();
      h.render();
      assert.ok(!tree.some((e) => e.props?.role === "alert"));
      assert.equal(query().get(key), key === "dte_min" ? "0" : key === "dte_max" ? "45" : null);
    }
  } finally { h.close(); }
});
