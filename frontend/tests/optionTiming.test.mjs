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
const { renderToStaticMarkup } = require("react-dom/server");

function load(relative, overrides = {}) {
  const filename = join(root, "src", relative);
  const source = readFileSync(filename, "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const loaded = { exports: {} };
  const localRequire = (name) => {
    if (name in overrides) return overrides[name];
    if (name.startsWith("@/")) {
      const relativePath = name.slice(2);
      for (const extension of [".ts", ".tsx"]) {
        try { readFileSync(join(root, "src", relativePath + extension)); }
        catch { continue; }
        return load(relativePath + extension, overrides);
      }
    }
    return require(name);
  };
  new Function("require", "module", "exports", compiled)(localRequire, loaded, loaded.exports);
  return loaded.exports;
}

const helpers = load("lib/optionTiming.ts");
function side(score = 0, label = "Saved context") {
  return { score, band: score === null ? null : "red", label, status: score === null ? "unavailable" : "available", reason_codes: score === null ? ["missing_market_data"] : [], factors: [{ key: "trend", label: "Trend", score: 20, weight: 0.5, contribution: 10, evidence: [`Evidence for ${label}`] }], adjustments: [{ key: "guard", label: "Event guard", cap: 25, reason: `Reason for ${label}` }] };
}
function snapshot(id, time, score = 0, extra = {}) {
  return { schema_version: 1, snapshot_id: id, model_version: "v1", as_of: time, computed_at: "2026-10-30T21:00:00Z", source_session: "2026-10-30", provisional: false, freshness: "fresh", covered_call: side(score, id), cash_secured_put: side(80, `PUT ${id}`), ...extra };
}
const first = snapshot("first", "2026-10-30T14:00:00Z", 0);
const last = snapshot("last", "2026-10-30T20:00:00Z", 60);
const middle = snapshot("middle", "2026-10-30T17:00:00Z", null, { model_version: "v2", history_kind: "reconstructed" });

test("backend scores preserve zero, unavailable is not zero, and missing sorts last in both directions", () => {
  assert.equal(helpers.optionTimingScore(first, "covered_call"), 0);
  assert.equal(helpers.optionTimingScore(first, "cash_secured_put"), 80);
  for (const value of [undefined, null, snapshot("missing", first.as_of, null), snapshot("invalid", first.as_of, 101)]) {
    assert.equal(helpers.optionTimingScore(value, "covered_call"), null);
  }
  const unavailable = snapshot("unavailable", first.as_of, 80);
  unavailable.covered_call.status = "not_applicable";
  assert.equal(helpers.optionTimingScore(unavailable, "covered_call"), null);
  for (const direction of ["asc", "desc"]) {
    const sorted = [null, 75, 0, 100, null].sort((a, b) => helpers.compareOptionTiming(a, b, direction));
    assert.deepEqual(sorted, direction === "asc" ? [0, 75, 100, null, null] : [100, 75, 0, null, null]);
  }
});

test("chronological points retain exact intraday source timestamps, missing and reconstructed snapshots", () => {
  const history = { points: [{ ...last, history_kind: "observed" }, middle, { ...first, history_kind: "observed" }] };
  const points = helpers.optionTimingPoints(history, last);
  assert.deepEqual(points.map((point) => point.snapshot_id), ["first", "middle", "last"]);
  assert.equal(points[1].as_of, "2026-10-30T17:00:00Z");
  assert.equal(points[1].history_kind, "reconstructed");
  assert.equal(points[1].model_version, "v2");
  assert.equal(points[1].covered_call.score, null);
  assert.equal(helpers.optionTimingPoints({ points: [] }, first)[0].covered_call.score, 0);
});

test("synthetic unavailable timestamps are never appended as observations, real missing scores remain holes", () => {
  const synthetic = snapshot("synthetic", "2099-01-01T00:00:00Z", null, {
    source_session: "2099-01-01", input_timestamp_available: false, freshness: "unknown",
  });
  assert.deepEqual(helpers.optionTimingPoints({ points: [] }, synthetic), []);
  const observedMissing = { ...middle, history_kind: "observed" };
  const points = helpers.optionTimingPoints({ points: [observedMissing, { ...synthetic, history_kind: "observed" }] }, first);
  assert.deepEqual(points.map((point) => point.snapshot_id), ["first", "middle"]);
  assert.equal(points[0].covered_call.score, 0);
  assert.equal(points[1].covered_call.score, null);
  const { default: Cards, OptionTimingExplanation } = load("components/OptionTiming.tsx");
  for (const component of [Cards, OptionTimingExplanation]) {
    const html = renderToStaticMarkup(React.createElement(component, { snapshot: synthetic, symbol: "MSFT", side: "covered_call" }));
    assert.match(html, /Source (?:as of \(local\)|\(local\)):[\s\S]*?Unavailable/);
    assert.doesNotMatch(html, /2099|datetime=/i);
    if (component === OptionTimingExplanation) {
      assert.match(html, /Source session:[\s\S]*?Unavailable/);
      assert.match(html, /availability response, not an input observation/);
    }
  }
  const actual = renderToStaticMarkup(React.createElement(OptionTimingExplanation, { snapshot: first, side: "covered_call" }));
  assert.match(actual, /dateTime="2026-10-30T14:00:00Z"/i);
});

test("card gauges expose actual side values, preserve zero, and mute missing or non-fresh scores", () => {
  const { default: Cards, OptionTimingGauge } = load("components/OptionTiming.tsx");
  const render = (snapshot, side = "covered_call") => renderToStaticMarkup(React.createElement(OptionTimingGauge, { snapshot, side }));
  for (const score of [0, 25, 50, 75, 100]) {
    const html = render(snapshot(`score-${score}`, first.as_of, score));
    assert.match(html, /role="meter"/);
    assert.match(html, /aria-label="Covered CALL timing"/);
    assert.match(html, /aria-valuemin="0"/);
    assert.match(html, /aria-valuemax="100"/);
    assert.match(html, new RegExp(`aria-valuenow="${score}"`));
    assert.match(html, new RegExp(`left:${score}%`));
    assert.match(html, /linear-gradient[\s\S]*accent-red[\s\S]*accent-orange[\s\S]*yellow-400[\s\S]*accent-green/);
  }
  assert.match(render(first, "cash_secured_put"), /aria-label="Cash-secured PUT timing"[\s\S]*aria-valuenow="80"/);
  for (const freshness of ["stale", "unknown"]) {
    const html = render({ ...last, freshness });
    assert.match(html, /aria-valuenow="60"/);
    assert.match(html, new RegExp(`aria-valuetext="60 / 100 · ${freshness}"`));
    assert.doesNotMatch(html, /linear-gradient|accent-green/);
  }
  for (const value of [undefined, { ...middle, freshness: "fresh" }]) {
    const html = render(value);
    assert.match(html, /Covered CALL: Unavailable/);
    assert.doesNotMatch(html, /role="meter"|aria-valuenow|left:|linear-gradient/);
  }
  const cards = renderToStaticMarkup(React.createElement(Cards, { symbol: "MSFT", snapshot: first }));
  assert.equal((cards.match(/role="meter"/g) ?? []).length, 2);
  assert.doesNotMatch(cards, /<button\b[^>]*>(?:(?!<\/button>)[\s\S])*role="meter"/);
});

test("explanations render the selected snapshot's score, evidence, caps and model, not current data", () => {
  const { OptionTimingExplanation, OptionTimingValue } = load("components/OptionTiming.tsx");
  const render = (snapshot, component = OptionTimingExplanation) => renderToStaticMarkup(React.createElement(component, { snapshot, side: "covered_call" }));
  const html = render(first);
  assert.match(html, /Evidence for first/);
  assert.match(html, /Reason for first/);
  assert.match(html, /Contribution 10/);
  assert.match(html, /Weight 50%/);
  assert.match(html, /Cap 25/);
  assert.doesNotMatch(html, /Evidence for last/);
  assert.match(render(middle), /missing market data/);
  const stale = snapshot("stale", first.as_of, 80, { freshness: "stale", provisional: true });
  stale.covered_call.band = "green";
  const staleHtml = render(stale, OptionTimingValue);
  assert.match(staleHtml, /PROVISIONAL/);
  assert.match(staleHtml, /STALE/);
  assert.doesNotMatch(staleHtml, /text-accent-green/);
  assert.match(render({ ...stale, freshness: "unknown" }, OptionTimingValue), /Freshness unknown/);
});

test("history fetch canonicalizes provider ids, propagates abort and recovers on retry", async () => {
  const previous = globalThis.fetch;
  const signal = new AbortController().signal;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return calls.length === 1 ? { ok: false, status: 503 } : { ok: true, json: async () => ({ schema_version: 1, points: [] }) };
  };
  try {
    await assert.rejects(helpers.fetchOptionTimingHistory("XNYS%3AAAPL", "90d", signal), /503/);
    const result = await helpers.fetchOptionTimingHistory("XNYS%3AAAPL", "90d", signal);
    assert.deepEqual(result.points, []);
    assert.equal(calls[0].url, "/api/symbols/XNYS%3AAAPL/option-timing/history?range=90d");
    assert.equal(calls[0].options.signal, signal);
  } finally { globalThis.fetch = previous; }
});

test("history BFF awaits params, preserves canonical symbol/range, status and abort signal", async () => {
  const previous = globalThis.fetch;
  const signal = new AbortController().signal;
  let upstream;
  const { GET } = load("app/api/symbols/[symbol]/option-timing/history/route.ts", {
    "@/lib/api": { API_BASE_URL: "http://mock-backend" },
    "next/server": { NextResponse: { json: (data, options) => ({ data, ...options }) } },
  });
  globalThis.fetch = async (url, options) => {
    upstream = { url, options };
    return { status: 404, json: async () => ({ error: "No history yet" }) };
  };
  try {
    const response = await GET({ url: "http://frontend/api/symbols/XNYS%3AAAPL/option-timing/history?range=90d", signal }, { params: Promise.resolve({ symbol: "XNYS%3AAAPL" }) });
    assert.equal(upstream.url, "http://mock-backend/api/symbols/XNYS%3AAAPL/option-timing/history?range=90d");
    assert.equal(upstream.options.signal, signal);
    assert.equal(upstream.options.cache, "no-store");
    assert.equal(response.status, 404);
    assert.equal(response.data.error, "No history yet");
    globalThis.fetch = async () => { throw new Error("Backend unavailable"); };
    const failed = await GET({ url: "http://frontend/api/history", signal }, { params: Promise.resolve({ symbol: "XNYS:AAPL" }) });
    assert.equal(failed.status, 502);
  } finally { globalThis.fetch = previous; }
});

function elements(tree) {
  if (tree === null || tree === undefined || typeof tree !== "object") return [];
  if (Array.isArray(tree)) return tree.flatMap(elements);
  return [tree, ...elements(tree.props?.children)];
}

test("popup point selector switches explanations, side selector independent, error retry works", () => {
  const states = [];
  let cursor = 0;
  let retryCount = 0;
  let queryOptions;
  let query = { data: { schema_version: 1, methodology: "Saved methodology", points: [{ ...first, history_kind: "observed" }, { ...last, history_kind: "observed" }] }, isPending: false, isError: false, refetch: () => retryCount++ };
  const mockReact = { ...React, useId: () => "timing-title", useMemo: (fn) => fn(), useState: (initial) => {
    const index = cursor++;
    if (!(index in states)) states[index] = initial;
    return [states[index], (value) => { states[index] = value; }];
  } };
  const { OptionTimingModal, OptionTimingExplanation } = load("components/OptionTiming.tsx", {
    react: mockReact,
    "@tanstack/react-query": { useQuery: (options) => { queryOptions = options; return query; } },
  });
  const render = () => { cursor = 0; return elements(OptionTimingModal({ symbol: "XNYS:AAPL", snapshot: last, initialSide: "covered_call", onClose() {} })); };
  let tree = render();
  const range = tree.find((node) => node.type === "select" && node.props.value === "90d");
  assert.deepEqual(elements(range.props.children).filter((node) => node.type === "option").map((node) => node.props.value), ["30d", "90d"]);
  range.props.onChange({ target: { value: "30d" } });
  render();
  assert.equal(queryOptions.queryKey[2], "30d");
  range.props.onChange({ target: { value: "90d" } });
  tree = render();
  assert.equal(tree.find((node) => node.type === OptionTimingExplanation).props.snapshot.snapshot_id, "last");
  const selector = tree.find((node) => node.type === "select" && node.props.value === "last");
  selector.props.onChange({ target: { value: "first" } });
  tree = render();
  assert.equal(tree.find((node) => node.type === OptionTimingExplanation).props.snapshot.snapshot_id, "first");
  tree.find((node) => typeof node.props?.onClick === "function" && node.props?.name === "v1:observed").props.onClick({ point: last });
  assert.equal(render().find((node) => node.type === OptionTimingExplanation).props.snapshot.snapshot_id, "last");
  selector.props.onChange({ target: { value: "first" } });
  tree = render();
  tree.find((node) => node.type === "select" && node.props.value === "covered_call").props.onChange({ target: { value: "cash_secured_put" } });
  tree = render();
  assert.equal(tree.find((node) => node.type === OptionTimingExplanation).props.side, "cash_secured_put");
  assert.deepEqual(queryOptions.queryKey, ["option-timing-history", "XNYS:AAPL", "90d", "v1", "last"]);
  query = { ...query, isError: true, error: new Error("History failed") };
  tree = render();
  tree.find((node) => node.type === "button" && node.props.children === "Retry").props.onClick();
  assert.equal(retryCount, 1);
  query = { ...query, isError: false, data: { points: [] } };
  assert.equal(render().find((node) => node.type === OptionTimingExplanation).props.snapshot.snapshot_id, "last");
  query = { ...query, isPending: true };
  tree = render();
  assert.ok(tree.some((node) => node.props?.role === "status"));
  assert.equal(tree.find((node) => node.type === OptionTimingExplanation).props.snapshot.snapshot_id, "last");
});

test("accessible native modal activates focus containment, Escape, backdrop, scroll lock and restores focus", () => {
  let mount;
  let shown = 0;
  let closed = 0;
  let restored = 0;
  let requestedClose = 0;
  class Element { focus() { restored++; } }
  const priorDocument = globalThis.document;
  const priorHTMLElement = globalThis.HTMLElement;
  globalThis.HTMLElement = Element;
  globalThis.document = { activeElement: new Element(), body: { style: { overflow: "auto" } } };
  const dialog = { showModal: () => shown++, close: () => closed++ };
  try {
    const { default: Dialog } = load("components/AccessibleDialog.tsx", {
      react: { ...React, useRef: () => ({ current: dialog }), useEffect: (effect) => { mount = effect; } },
    });
    const tree = Dialog({ titleId: "title", onClose: () => requestedClose++, children: "content" });
    const cleanup = mount();
    assert.equal(shown, 1); // showModal provides native focus containment and inert background.
    assert.equal(tree.props["aria-modal"], "true");
    assert.equal(tree.props["aria-labelledby"], "title");
    assert.equal(document.body.style.overflow, "hidden");
    tree.props.onCancel({ preventDefault() {} });
    const target = { getBoundingClientRect: () => ({ left: 10, right: 90, top: 10, bottom: 90 }) };
    tree.props.onClick({ target, currentTarget: target, clientX: 0, clientY: 0 });
    assert.equal(requestedClose, 2);
    cleanup();
    assert.equal(closed, 1);
    assert.equal(restored, 1);
    assert.equal(document.body.style.overflow, "auto");
  } finally { globalThis.document = priorDocument; globalThis.HTMLElement = priorHTMLElement; }
});

test("overview wiring is options-only, after Tech, reuses snapshot and does not open legacy row popup", () => {
  const source = readFileSync(join(root, "src/components/SymbolsTable.tsx"), "utf8");
  assert.match(source, /key: "tech_timing"[\s\S]*?key: "call_timing"[\s\S]*?key: "put_timing"[\s\S]*?key: "entry_tag"/);
  for (const key of ["call_timing", "put_timing"]) assert.match(source, new RegExp(`key: "${key}"[^\\n]+modes: \\["options"\\]`));
  assert.match(source, /viewMode === "options" && OPTION_TIMING_SIDES\.map/);
  assert.match(source, /event\.stopPropagation\(\); setTimingModal/);
  assert.match(source, /snapshot=\{timingModal\.row\.option_timing\}/);
  assert.doesNotMatch(source, /fetchOptionTimingHistory/);
  assert.match(source, /matchesSymbolSuitability\(r\.entry_tag, r\.momentum, suitabilityFilter\)/);
});
