import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { spawnSync } from "node:child_process";
import test from "node:test";

const require = createRequire(new URL("../package.json", import.meta.url));
const ts = require("typescript");
const { renderToStaticMarkup } = require("react-dom/server");

function load(relativePath, mocks = {}, clientProxy) {
  const source = readFileSync(new URL(relativePath, import.meta.url), "utf8");
  if (clientProxy && /^\s*["']use client["'];/.test(source)) {
    return clientProxy(relativePath);
  }
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2017, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  });
  const loaded = { exports: {} };
  new Function("require", "module", "exports", outputText)(
    (name) => mocks[name] ?? (name.startsWith("@/")
      ? load(`../src/${name.slice(2)}.${name.startsWith("@/components/") ? "tsx" : "ts"}`, mocks, clientProxy)
      : require(name)),
    loaded,
    loaded.exports,
  );
  return loaded.exports;
}

const chartNames = [
  "Area", "Bar", "BarChart", "CartesianGrid", "ComposedChart", "Legend", "Line",
  "ReferenceDot", "ReferenceLine", "ResponsiveContainer", "Tooltip", "XAxis", "YAxis",
];
const charts = load("../src/components/ForecastCharts.tsx", {
  recharts: Object.fromEntries(chartNames.map((name) => [name, name])),
  "@/types/forecasts": { HORIZONS: ["1d", "1w", "2w", "4w"] },
});
const encoding = load("../src/lib/symbolEncoding.ts");

function find(element, type) {
  if (!element || typeof element !== "object") return null;
  if (element.type === type) return element;
  for (const child of [element.props?.children].flat(Infinity)) {
    const match = find(child, type);
    if (match) return match;
  }
  return null;
}

const row = {
  created_date: "2026-11-08",
  price_at_creation: 100,
  confidence: 0.5,
  outer_confidence: 0.9,
  horizons: {
    "1d": { center: 101, low1: 95, high1: 105, low2: 90, high2: 110 },
    "1w": { center: 102, low1: 94, high1: 106, low2: 89, high2: 111 },
  },
};

function tooltip(tree, index) {
  const point = find(tree, "ComposedChart").props.data[index];
  return renderToStaticMarkup(find(tree, "Tooltip").props.content({
    active: true, label: point.label, payload: [{ payload: point }],
  }));
}

test("summary tooltip pairs actual band prices with per-horizon empirical coverage", () => {
  const tree = charts.ProjectionFanChart({
    row,
    calibration: {
      windowLabel: "90d",
      hitRate: {
        "1d": { resolved: 12, hit_pct_1sigma: 41.7, hit_pct_2sigma: 83.3 },
        "1w": { resolved: 4, hit_pct_1sigma: 0, hit_pct_2sigma: 100 },
      },
    },
  });
  const html = tooltip(tree, 1);
  assert.match(html, /<span class="text-text-muted">50\.0%<\/span>/);
  assert.match(html, /<span class="text-text-muted">90\.0%<\/span>/);
  assert.match(html, /<span class="ml-auto font-mono text-text">\$101\.00<\/span>/);
  for (const [low, high, hit] of [["95", "105", "41.7"], ["90", "110", "83.3"]]) {
    assert.ok(html.includes(
      `<span class="ml-auto font-mono text-text">$${low}.00 – $${high}.00<span class="block"><span class="text-text-muted">Hit-rate: </span>${hit}%</span></span>`,
    ));
  }
  assert.match(html, /<div class="mt-2 text-text-muted">Historical window: 90d.*Resolved samples: 12<\/div>/);
  assert.doesNotMatch(html, /σ|100\.0%|nominal confidence|Realized/);
  assert.match(tooltip(tree, 2), /Hit-rate: <\/span>0\.0%/);
  assert.match(tooltip(tree, 2), /Hit-rate: <\/span>100\.0%/);
  assert.match(tooltip(tree, 2), /Resolved samples: 4/);
  assert.doesNotMatch(tooltip(tree, 0), /Hit-rate|Resolved samples/);
});

test("unresolved, absent and invalid calibration stays unavailable, never fabricated zero", () => {
  for (const entry of [
    undefined,
    { resolved: 0, hit_pct_1sigma: 0, hit_pct_2sigma: 0 },
    { resolved: 5, hit_pct_1sigma: null, hit_pct_2sigma: NaN },
    { resolved: -1, hit_pct_1sigma: 50, hit_pct_2sigma: 90 },
  ]) {
    const tree = charts.ProjectionFanChart({
      row: { ...row, confidence: undefined, outer_confidence: null },
      calibration: { windowLabel: "90d", hitRate: { "1d": entry } },
    });
    const html = tooltip(tree, 1);
    assert.equal((html.match(/Hit-rate: <\/span>Unavailable/g) ?? []).length, 2);
    assert.match(html, /Inner band \(confidence unavailable\)/);
    assert.match(html, /Outer band \(confidence unavailable\)/);
    assert.doesNotMatch(html, /0\.0%|σ|nominal confidence|Realized/);
  }
});

test("existing projection chart retains its labels and tooltip without summary props", () => {
  const tree = charts.ProjectionFanChart({ row });
  assert.equal(find(tree, "Area").props.name, "±2σ");
  const html = tooltip(tree, 1);
  assert.match(html, /<span class="text-text-muted">±1σ<\/span>/);
  assert.match(html, /<span class="text-text-muted">±2σ<\/span>/);
  for (const [low, high] of [["95", "105"], ["90", "110"]]) {
    assert.ok(html.includes(
      `<span class="ml-auto font-mono text-text-muted">$${low}.00 – $${high}.00</span>`,
    ));
  }
  assert.doesNotMatch(html, /Hit-rate|Realized|Historical|nominal confidence/);
  assert.match(renderToStaticMarkup(charts.ProjectionFanChart({ row: null })), /No projection data/);
});

test("summary requests fixed 90d, selects latest row and links the entire card to canonical forecasts", async () => {
  const requests = [];
  const card = load("../src/components/SymbolForecastSummary.tsx", {
    "next/link": { default: "a" },
    "@/lib/api": { apiFetch: async (path) => {
      requests.push(path);
      return { rows: [row, { ...row, created_date: "2026-10-01" }], hit_rate: { "1d": { resolved: 3 } } };
    } },
    "@/lib/symbolEncoding": encoding,
    "@/components/SymbolForecastPositions": { default: "Fan" },
  }).default;
  const tree = await card({ symbol: "XNYS:AAD", snapshot: "detail-revision" });
  assert.deepEqual(requests, ["/api/symbols/XNYS%3AAAD/forecasts?range=90d"]);
  assert.equal(tree.type, "a");
  assert.equal(tree.props.href, "/symbols/XNYS:AAD/forecasts?range=90d");
  assert.match(tree.props["aria-label"], /price forecasts, 90-day range/);
  const fan = find(tree, "Fan");
  assert.equal(fan.props.symbol, "XNYS:AAD");
  assert.equal(fan.props.snapshot, "detail-revision");
  assert.equal(fan.props.row.created_date, "2026-11-08");
  assert.deepEqual(fan.props.calibration, { windowLabel: "90d", hitRate: { "1d": { resolved: 3 } } });
  assert.match(tree.props.className, /focus-visible/);
  assert.equal(find(tree, "BarChart"), null);
  const page = readFileSync(new URL("../src/app/symbols/[symbol]/page.tsx", import.meta.url), "utf8");
  assert.match(page, /<DetailSection title="Summary">[\s\S]*<SymbolForecastSummary symbol=\{d\.security\?\.security_id \?\? d\.security_id \?\? symbol\} snapshot=\{d\.detail_loaded_at\} \/>[\s\S]*<\/DetailSection>/);
});

test("summary exposes HTTP/transport and response errors rather than hiding failures", async () => {
  for (const apiFetch of [
    async () => { throw new Error("API 503 unavailable"); },
    async () => ({ error: "Forecast load failed" }),
  ]) {
    const card = load("../src/components/SymbolForecastSummary.tsx", {
      "next/link": { default: "a" },
      "@/lib/api": { apiFetch },
      "@/lib/symbolEncoding": encoding,
      "@/components/SymbolForecastPositions": { default: "Fan" },
    }).default;
    const tree = await card({ symbol: "AAD" });
    assert.equal(tree.props.role, "alert");
    assert.match(renderToStaticMarkup(tree), /API 503 unavailable|Forecast load failed/);
  }
});

async function summaryMarkup(rows) {
  const card = load("../src/components/SymbolForecastSummary.tsx", {
    "next/link": { default: "a" },
    "@/lib/api": { apiFetch: async () => ({ rows, hit_rate: {} }) },
    "@/components/SymbolForecastPositions": { default: () => null },
  }).default;
  return renderToStaticMarkup(await card({ symbol: "AAD" }));
}

test("summary metadata uses the actual latest forecast, with signed trend and detail tooltip", async () => {
  const older = {
    ...row, created_date: "2026-10-01", price_at_creation: 321.98,
    hv: 0.41, vol_source: "hv", bias: -0.75, trend: { slope: 1.23, r2: 0.92 },
    flags: { earnings_in_window: true, exdiv_in_window: false },
  };
  const latest = {
    ...row, price_at_creation: 617.43, hv: 0.287, vol_source: "iv", bias: 0.236,
    trend: { slope: -0.074, r2: 0.126 },
    flags: { earnings_in_window: false, exdiv_in_window: true },
  };
  for (const slope of [-0.074, 0.074, 0]) {
    const html = await summaryMarkup([older, { ...latest, trend: { ...latest.trend, slope } }]);
    const text = html.replace(/<[^>]*>/g, "");
    assert.match(text, /Price at creation: \$617\.43/);
    assert.match(text, /HV: 28\.7%/);
    assert.match(text, /Vol source: iv/);
    assert.match(text, /Bias: 0\.24/);
    assert.ok(text.includes(`Trend: ${slope >= 0 ? "+" : ""}${slope.toFixed(2)} $/session · R² 0.13`));
    assert.match(html, /title="Headline robust \(Theil-Sen\) trend in dollars per trading session:/);
    assert.match(html, /flex flex-wrap.*text-xs/);
    assert.match(text, /💰 ex-div in window/);
    assert.doesNotMatch(text, /earnings in window|321\.98|Latest price bands|Historical window|Hover a horizon|Dashed line = anchor|Open price forecasts/);
  }
});

const { forecastOptionPositions } = load("../src/lib/forecastOptionPositions.ts");
const now = new Date("2026-11-02T15:00:00Z");
const openCall = {
  position_id: "call", type: "call", status: "active", strike: 120,
  expiration: "2026-11-09", contracts: 2, display_premium: 3.25,
  account_name: "Retirement", account_id: "private-account-id",
  source: { dte: 999, account_id: "private-source-id" },
};

function findAll(element, type) {
  if (!element || typeof element !== "object") return [];
  return [
    ...(element.type === type ? [element] : []),
    ...[element.props?.children].flat(Infinity).flatMap((child) => findAll(child, type)),
  ];
}

test("open calls and puts use exact live DTE / 5; only >20 DTE overflows, and all positions survive grouping", () => {
  const positions = [
    openCall,
    { ...openCall, type: "put", strike: 80, expiration: "2026-11-07" },
    { ...openCall, expiration: "2026-11-22" },
    { ...openCall, expiration: "2026-11-23" },
    { ...openCall, type: "put", expiration: "2026-12-02", account_name: "Brokerage" },
    { ...openCall, position_id: "same-contract-other-account", account_name: "Joint" },
    { ...openCall, status: "closed" },
    { ...openCall, status: "rolled" },
    { ...openCall, type: "stock" },
    { ...openCall, contract_counts: { remaining: { status: "resolved", count: 0 } } },
    { ...openCall, open_contracts: 0 },
  ];
  const { groups, unavailable } = forecastOptionPositions(positions, now);
  assert.equal(unavailable, 0);
  assert.equal(groups.flatMap((group) => group.positions).length, 6);
  assert.deepEqual(groups.map((group) => [group.weeks, group.strike, group.positions.length]), [
    [1.4, 120, 2], [1, 80, 1], [4, 120, 3],
  ]);
  assert.equal(groups[2].overflow, true);
  assert.deepEqual(groups[2].positions.map((p) => [p.dte, p.expiration]), [
    [20, "2026-11-22"], [21, "2026-11-23"], [30, "2026-12-02"],
  ]);
  assert.equal(forecastOptionPositions([{ ...openCall, expiration: "2026-11-22" }], now).groups[0].overflow, false);
  assert.equal(forecastOptionPositions([{ ...openCall, expiration: "2026-11-02" }], now).groups[0].weeks, 0);
});

test("DTE follows New York date across UTC midnight and DST, not saved source DTE or browser timezone", () => {
  const position = { ...openCall, expiration: "2026-11-22" };
  assert.equal(forecastOptionPositions([position], new Date("2026-11-03T02:00:00Z")).groups[0].positions[0].dte, 20);
  assert.equal(forecastOptionPositions([position], new Date("2026-11-03T05:00:00Z")).groups[0].positions[0].dte, 19);
  const dst = { ...openCall, expiration: "2026-03-10" };
  assert.equal(forecastOptionPositions([dst], new Date("2026-03-08T06:00:00Z")).groups[0].positions[0].dte, 2);
  assert.equal(forecastOptionPositions([dst], new Date("2026-03-09T04:00:00Z")).groups[0].positions[0].dte, 1);
});

test("unplottable active options are explicit; quantity and premium never fabricate evidence", () => {
  const { groups, unavailable } = forecastOptionPositions([
    { ...openCall, strike: NaN },
    { ...openCall, strike: 0 },
    { ...openCall, expiration: "2026-02-30" },
    { ...openCall, expiration: undefined },
    { ...openCall, status: "closed", strike: NaN },
    { ...openCall, contracts: undefined, display_premium: undefined, account_name: undefined },
    { ...openCall, contracts: 10, open_contracts: -3, display_premium: 0 },
    { ...openCall, contract_counts: { remaining: { status: "error", count: null } } },
  ], now);
  assert.equal(unavailable, 4);
  assert.deepEqual(groups[0].positions.map((p) => p.contracts), [null, 3, null]);
  assert.deepEqual(groups[0].positions.map((p) => p.premium), [null, 0, 3.25]);
  assert.equal(groups[0].positions[0].accountName, null);
});

test("overlay extends strike domain, preserves fan data and provides mixed-color grouped hover details at the real coordinate", () => {
  const { groups } = forecastOptionPositions([
    { ...openCall, strike: 250, expiration: "2026-11-22" },
    { ...openCall, strike: 250, expiration: "2026-11-23", account_name: "Joint" },
    { ...openCall, type: "put", strike: 250, expiration: "2026-12-02", account_name: undefined, is_paper: true },
    { ...openCall, type: "put", strike: 25, expiration: "2026-11-09" },
  ], now);
  const tree = charts.ProjectionFanChart({ row, positions: groups });
  const x = find(tree, "XAxis").props;
  assert.equal(x.type, "number");
  assert.equal(x.dataKey, "weeks");
  assert.deepEqual(x.domain, [0, 4]);
  assert.deepEqual(find(tree, "ComposedChart").props.data.map((p) => p.weeks), [0, 0.2, 1]);
  assert.deepEqual(find(tree, "ComposedChart").props.data.map((p) => p.band1), [[100, 100], [95, 105], [94, 106]]);
  assert.ok(find(tree, "YAxis").props.padding.top >= 8);
  assert.ok(x.padding.right >= 20);
  const markers = findAll(tree, "ReferenceDot");
  assert.deepEqual(markers.map((p) => [p.props.x, p.props.y, p.props.ifOverflow]), [
    [4, 250, "extendDomain"], [1.4, 25, "extendDomain"],
  ]);
  const marker = markers[0].props.shape({ cx: 300, cy: 20 });
  const html = renderToStaticMarkup(marker);
  for (const detail of [
    /Sold CALL/, /Sold PUT \(paper\)/, /Strike \$250\.00/,
    /2026-11-22 · 20 DTE/, /2026-11-23 · 21 DTE/, /2026-12-02 · 30 DTE/,
    /Contracts: 2/, /Premium: \$3\.25/, /Account: Retirement/, /Account: Joint/,
    /var\(--accent-purple\)/, /var\(--accent-orange\)/,
  ]) assert.match(html, detail);
  assert.doesNotMatch(html, /private-account-id|private-source-id|999 DTE/);
  assert.match(html, /<title>[\s\S]*Sold CALL[\s\S]*Sold PUT[\s\S]*<\/title>/);
  assert.match(html, />3<\/text>/);
  assert.match(html, />&gt;<\/text>/);
  assert.match(renderToStaticMarkup(markers[1].props.shape({ cx: 80, cy: 200 })), /var\(--accent-orange\)/);
  assert.doesNotMatch(renderToStaticMarkup(markers[1].props.shape({ cx: 80, cy: 200 })), /&gt;<\/text>/);
  const boundary = forecastOptionPositions([{ ...openCall, expiration: "2026-11-22" }], now);
  const boundaryTree = charts.ProjectionFanChart({ row, positions: boundary.groups });
  assert.doesNotMatch(renderToStaticMarkup(find(boundaryTree, "ReferenceDot").props.shape({ cx: 300, cy: 20 })), /&gt;<\/text>|shown at limit/);
  assert.equal(find(charts.ProjectionFanChart({ row }), "XAxis").props.type, "category");
  assert.match(tooltip(tree, 1), />1D<\/div>/);
});

test("summary overlay shares the canonical Options query and leaves the fan usable during loading, errors, empty and refreshed data", () => {
  let state = { isPending: true };
  const options = [];
  const component = load("../src/components/SymbolForecastPositions.tsx", {
    "@tanstack/react-query": { queryOptions: (value) => value, useQuery: (value) => {
      options.push(value);
      return state;
    } },
    "@/components/ForecastCharts": { ProjectionFanChart: "Fan" },
  }).default;
  function render() {
    return component({ symbol: "XNAS:MSFT", snapshot: "revision-2", row, calibration: { hitRate: {}, windowLabel: "90d" } });
  }
  const pending = render();
  assert.match(renderToStaticMarkup(findAll(pending, "p")[0]), /Loading open options/);
  assert.deepEqual(find(pending, "Fan").props.positions, []);
  assert.deepEqual(options[0].queryKey, ["symbol-detail", "XNAS:MSFT", "options", "revision-2"]);
  state = { error: new Error("offline"), isPending: false };
  assert.equal(find(render(), "p").props.role, "alert");
  state = { data: { positions: [] }, isPending: false };
  assert.equal(find(render(), "p"), null);
  assert.equal(find(render(), "Fan").props.row, row);
  state = { data: { positions: [{ ...openCall, expiration: "2099-12-31" }] }, isPending: false };
  assert.equal(find(render(), "Fan").props.positions[0].positions[0].type, "call");
  state = { data: { positions: [{ ...openCall, expiration: undefined }] }, isPending: false };
  assert.match(renderToStaticMarkup(find(render(), "p")), /1 open option\(s\) missing/);
});

test("summary metadata renders explanatory native tooltips without adding visible copy", async () => {
  for (const source of ["iv", "hv", "ewma"]) {
    const html = await summaryMarkup([{
      ...row, hv: 0.287, vol_source: source, bias: -0.24,
      trend: { slope: -0.074, r2: 0.126 },
      flags: { earnings_in_window: true, exdiv_in_window: true },
    }]);
    const tooltips = [...html.matchAll(/<span class="cursor-help" title="([^"]+)">/g)]
      .map((match) => match[1]);
    assert.equal(tooltips.length, 8);
    assert.match(tooltips[0], /Price anchor.*created, not the live price/);
    assert.match(tooltips[1], /Annualized volatility actually used.*calibration scaling.*may come from IV, historical volatility or EWMA/);
    assert.match(tooltips[2], /iv = options-implied.*hv = historical.*ewma = exponentially weighted.*recent moves more weight/);
    assert.match(tooltips[3], /-1 to \+1.*positive is bullish, negative bearish.*absolute values.*stronger signal.*Not a probability or target return.*does not shift the band center/);
    assert.match(tooltips[4], /dollars per trading session.*positive slopes up, negative down.*shrunk toward zero.*overlay, not the band center.*longer horizons/);
    assert.match(tooltips[5], /fit quality from 0 to 1.*past price variation.*not a measure of future forecast accuracy/);
    assert.match(tooltips[6], /known earnings date.*after creation.*30 calendar days.*not the 90-day history range/);
    assert.match(tooltips[7], /known ex-dividend date.*after creation.*30 calendar days.*not the 90-day history range/);
    const text = html.replace(/<[^>]*>/g, "");
    assert.match(text, new RegExp(`Vol source: ${source}`));
    assert.doesNotMatch(text, /Annualized|estimator|probability|fit quality|calendar days|live price/);
  }
});

test("summary shows event flags only for true booleans and omits unavailable R²", async () => {
  for (const flag of [true, false, undefined, null, "true", "false", 1]) {
    for (const r2 of [undefined, null, NaN, Infinity, "0.5"]) {
      const html = await summaryMarkup([{
        ...row, trend: { slope: -0.1, r2 },
        flags: { earnings_in_window: flag, exdiv_in_window: flag },
      }]);
      const text = html.replace(/<[^>]*>/g, "");
      assert.match(text, /Trend: -0\.10 \$\/session/);
      assert.doesNotMatch(text, /R²|NaN|Infinity/);
      assert.equal(text.includes("📅 earnings in window"), flag === true);
      assert.equal(text.includes("💰 ex-div in window"), flag === true);
    }
  }
});

test("summary missing numeric metadata stays honest and empty responses omit metadata", async () => {
  for (const value of [undefined, null, NaN, Infinity, -Infinity]) {
    const html = await summaryMarkup([{
      ...row, price_at_creation: value, hv: value, bias: value,
      vol_source: null, trend: { slope: value, r2: 0.5 }, flags: null,
    }]);
    const text = html.replace(/<[^>]*>/g, "");
    assert.match(text, /Price at creation: —HV: —Vol source: —Bias: —/);
    assert.doesNotMatch(text, /Trend:|R²|NaN|Infinity|null|undefined|0\.00|in window/);
  }
  const zero = (await summaryMarkup([{
    ...row, price_at_creation: 0, hv: 0, bias: 0, vol_source: "hv",
  }])).replace(/<[^>]*>/g, "");
  assert.match(zero, /Price at creation: \$0\.00HV: 0\.0%Vol source: hvBias: 0\.00/);
  assert.doesNotMatch(await summaryMarkup([]), /Price at creation|HV:|Vol source|Bias:|Trend:|R²|in window|Latest price bands/);
});

test("summary executes successful and empty responses across the React server/client boundary", () => {
  const result = spawnSync(process.execPath, [
    "--conditions=react-server",
    "--input-type=module",
    "-e",
    `
      import assert from "node:assert/strict";
      import { readFileSync } from "node:fs";
      import { createRequire } from "node:module";
      const require = createRequire(${JSON.stringify(new URL("../package.json", import.meta.url).href)});
      const ts = require("typescript");
      const { createClientModuleProxy } = require("next/dist/compiled/react-server-dom-webpack/server.node");
      ${load.toString().replaceAll("import.meta.url", JSON.stringify(import.meta.url))}
      const fixture = ${JSON.stringify(row)};
      for (const rows of [
        [{ ...fixture, created_date: "2026-10-01" }, fixture],
        [],
      ]) {
        const card = load("../src/components/SymbolForecastSummary.tsx", {
          "next/link": { default: "a" },
          "@/lib/api": { apiFetch: async () => ({ rows, hit_rate: {} }) },
        }, createClientModuleProxy).default;
        const tree = await card({ symbol: "AAD" });
        assert.equal(tree.type, "a");
        const fan = tree.props.children.at(-1);
        assert.equal(fan.type.$$typeof, Symbol.for("react.client.reference"));
        assert.deepEqual(fan.props.row, rows.length ? fixture : null);
      }
    `,
  ], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr || result.stdout);
});
