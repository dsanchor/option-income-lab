import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
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
  const module = { exports: {} };
  const code = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  vm.runInNewContext(code, {
    exports: module.exports, module,
    require: (name) => name === "react" ? react : require(name),
    Date, Promise,
  });
  return module.exports;
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

test("three views preserve native Price and forecast all filtered Symbols without historical hiding", () => {
  assert.match(table, /\["portfolio", "options", "forecast"\]/);
  assert.match(table, /hideHistorical && viewMode !== "forecast"/);
  assert.match(table, /viewMode === "forecast" && filtered\.map\(renderRow\)/);
  assert.match(table, /viewMode === "forecast" \|\| !optionFiltersAvailable \|\| matchesSymbolPositions/);
  assert.match(table, /viewMode !== "forecast" && optionFiltersAvailable && suitabilityFilter/);
  assert.match(table, /formatPrice\(r\.price, r\.price_display_currency\)/);
  assert.match(table, /\/forecasts\?range=90d/);
  assert.match(table, /forecasts\.data\?\.symbols\[r\.security_id \?\? r\.symbol\]/);
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
    [undefined, "Loading…"], [{ status: "not_eligible" }, "Not eligible"],
    [{ status: "unavailable" }, "Forecast unavailable"], [{ status: "error" }, "Forecast error"],
  ]) assert.ok(cell(summary).includes(label));
  assert.match(cell(undefined, "1d", "failed"), /Forecast error/);
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
  assert.match(view, /activeView === "portfolio" && hasPortfolioSummary/);
  assert.match(view, /onDataChanged=\{invalidateViews\}/);
  assert.match(view, /onAdded=\{invalidateViews\}/);
});
