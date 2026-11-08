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
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
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
  "ReferenceLine", "ResponsiveContainer", "Tooltip", "XAxis", "YAxis",
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
  assert.match(html, /50\.0% nominal confidence/);
  assert.match(html, /90\.0% nominal confidence/);
  assert.match(html, /\$95\.00.*\$105\.00/);
  assert.match(html, /\$90\.00.*\$110\.00/);
  assert.match(html, /Realized hit-rate: 41\.7%/);
  assert.match(html, /Realized hit-rate: 83\.3%/);
  assert.match(html, /Historical window: 90d.*Resolved samples: 12/);
  assert.doesNotMatch(html, /σ|100\.0%/);
  assert.match(tooltip(tree, 2), /Realized hit-rate: 0\.0%/);
  assert.match(tooltip(tree, 2), /Resolved samples: 4/);
  assert.doesNotMatch(tooltip(tree, 0), /Realized hit-rate|Resolved samples/);
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
    assert.equal((html.match(/Realized hit-rate: Unavailable/g) ?? []).length, 2);
    assert.match(html, /confidence unavailable/);
    assert.doesNotMatch(html, /0\.0%|σ/);
  }
});

test("existing projection chart retains its labels and tooltip without summary props", () => {
  const tree = charts.ProjectionFanChart({ row });
  assert.equal(find(tree, "Area").props.name, "±2σ");
  assert.match(tooltip(tree, 1), /±1σ|±2σ/);
  assert.doesNotMatch(tooltip(tree, 1), /Realized|Historical|nominal confidence/);
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
    "@/components/ForecastCharts": { ...charts, ProjectionFanChart: "Fan" },
  }).default;
  const tree = await card({ symbol: "XNYS:AAD" });
  assert.deepEqual(requests, ["/api/symbols/XNYS%3AAAD/forecasts?range=90d"]);
  assert.equal(tree.type, "a");
  assert.equal(tree.props.href, "/symbols/XNYS:AAD/forecasts?range=90d");
  assert.match(tree.props["aria-label"], /price forecasts, 90-day range/);
  const fan = find(tree, "Fan");
  assert.equal(fan.props.row.created_date, "2026-11-08");
  assert.deepEqual(fan.props.calibration, { windowLabel: "90d", hitRate: { "1d": { resolved: 3 } } });
  assert.match(tree.props.className, /focus-visible/);
  assert.equal(find(tree, "BarChart"), null);
  const page = readFileSync(new URL("../src/app/symbols/[symbol]/page.tsx", import.meta.url), "utf8");
  assert.match(page, /<DetailSection title="Summary">[\s\S]*<SymbolForecastSummary symbol=\{d\.security\?\.security_id \?\? d\.security_id \?\? symbol\} \/>[\s\S]*<\/DetailSection>/);
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
      "@/components/ForecastCharts": charts,
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
    "@/components/ForecastCharts": { ProjectionFanChart: () => null },
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
