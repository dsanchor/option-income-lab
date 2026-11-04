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

function load(relative, overrides) {
  const compiled = ts.transpileModule(readFileSync(join(root, "src", relative), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const loaded = { exports: {} };
  new Function("require", "module", "exports", compiled)((name) => {
    if (name in overrides) return overrides[name];
    if (name.startsWith("@/")) return {};
    return require(name);
  }, loaded, loaded.exports);
  return loaded.exports;
}

function elements(node) {
  if (!node || typeof node !== "object") return [];
  if (Array.isArray(node)) return node.flatMap(elements);
  return [node, ...elements(node.props?.children)];
}

const result = (generation = 1, cache = {}, status = "ok") => ({
  symbol: "AAPL", status,
  parameters: { underlying: { price: generation, source: "chain" } },
  calls: { rows: [{ strike: generation }] }, puts: { rows: [{ strike: generation + 10 }] },
  cache: { generation, refreshing: false, refresh_completed_at: `complete-${generation}`, ...cache },
});
const reply = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });
const settle = () => new Promise((resolve) => setImmediate(resolve));

// Same dependency-free hook harness as optionTiming.test.mjs, with effects
// committed after render and a controllable clock for the polling lifecycle.
function harness() {
  let hooks = [], cursor = 0, pending = [], currentSymbol;
  const timers = new Map(), requests = [], responses = [];
  const previous = { fetch: globalThis.fetch, setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout };
  let timerId = 0;
  globalThis.setTimeout = (fn, delay) => { timers.set(++timerId, { fn, delay }); return timerId; };
  globalThis.clearTimeout = (id) => timers.delete(id);
  globalThis.fetch = (url, options) => {
    requests.push({ url, options });
    const response = responses.shift();
    assert.ok(response, `Unexpected request ${url}`);
    return typeof response === "function" ? response(options) : Promise.resolve(response);
  };
  const same = (a, b) => a && a.length === b.length && a.every((v, i) => v === b[i]);
  const react = {
    ...React,
    useState(initial) {
      const i = cursor++;
      if (!(i in hooks)) hooks[i] = initial;
      return [hooks[i], (value) => { hooks[i] = typeof value === "function" ? value(hooks[i]) : value; }];
    },
    useRef(initial) {
      const i = cursor++;
      if (!(i in hooks)) hooks[i] = { current: initial };
      return hooks[i];
    },
    useCallback(fn, deps) {
      const i = cursor++;
      if (!same(hooks[i]?.deps, deps)) hooks[i] = { deps, fn };
      return hooks[i].fn;
    },
    useEffect(effect, deps) {
      const i = cursor++;
      if (!same(hooks[i]?.deps, deps)) pending.push(() => {
        hooks[i]?.cleanup?.();
        hooks[i] = { deps, cleanup: effect() };
      });
    },
  };
  const { default: View } = load("components/BestOptionsView.tsx", {
    react,
    "@/lib/options-row-format": { fmtNum: (value) => String(value) },
  });
  const unmount = () => { for (const hook of hooks) hook?.cleanup?.(); };
  return {
    requests, responses, timers,
    render(symbol = currentSymbol || "AAPL") {
      const wrapper = View({ symbol });
      if (currentSymbol !== wrapper.key) {
        unmount();
        hooks = [];
        currentSymbol = wrapper.key;
      }
      cursor = 0;
      const tree = elements(wrapper.type(wrapper.props));
      for (const effect of pending.splice(0)) effect();
      return tree;
    },
    async tick(delay) {
      const entry = [...timers].find(([, timer]) => delay === undefined ? timer.delay < 120_000 : timer.delay === delay);
      assert.ok(entry, `No timer for ${delay ?? "poll"}`);
      timers.delete(entry[0]);
      entry[1].fn();
      await settle();
    },
    close() {
      unmount();
      const remaining = timers.size;
      Object.assign(globalThis, previous);
      assert.equal(remaining, 0, "all timers cleaned up");
    },
  };
}
const tables = (tree) => tree.filter((node) => typeof node.type === "function" && node.props.side);
const busy = (tree) => tree.find((node) => "aria-busy" in (node.props || {}));
const refreshButton = (tree) => tree.find((node) => node.type === "button");
const alertText = (tree) => tree.find((node) => node.props?.role === "alert")?.props.children.flat(Infinity).join("");
async function ready(h, data = result()) {
  h.responses.push(reply(data));
  h.render();
  await settle();
  return h.render();
}

test("old ok/stale, including before refreshing is marked, cannot end refresh; completed replacement swaps both tables", async () => {
  const h = harness();
  try {
    let tree = await ready(h);
    const start = refreshButton(tree).props.onClick;
    h.responses.push(reply(result(4)), reply({}, 202));
    const running = start();
    start(); // Synchronous double click cannot issue another POST.
    await settle();
    for (const old of [result(4), result(4, { refreshing: true, refresh_error: "old failure" }, "stale"), result(5, { refreshing: true })]) {
      tree = h.render();
      assert.equal(refreshButton(tree).props.disabled, true);
      assert.equal(busy(tree).props["aria-busy"], true);
      assert.equal(busy(tree).props.inert, true);
      assert.match(busy(tree).props.className, /opacity-50/);
      assert.equal(tables(tree)[0].props.data.rows[0].strike, 1);
      h.responses.push(reply(old));
      await h.tick();
    }
    // A scheduled precompute generation alone is not targeted completion.
    h.responses.push(reply(result(6, { refresh_completed_at: "complete-4" })));
    await h.tick();
    assert.equal(busy(h.render()).props.inert, true);
    h.responses.push(reply(result(7)));
    await h.tick();
    await running;
    tree = h.render();
    assert.equal(busy(tree).props.inert, false);
    assert.deepEqual(tables(tree).map((node) => node.props.data.rows[0].strike), [7, 17]);
    assert.equal(alertText(tree), undefined);
    assert.equal(h.requests.filter((r) => r.options.method === "POST").length, 1);
  } finally { h.close(); }
});

test("409 joins the in-flight refresh and completed chain errors beat an ok response while keeping previous tables", async () => {
  const h = harness();
  try {
    await ready(h);
    h.responses.push(reply(result(1, { refreshing: true })), reply({}, 409));
    const running = refreshButton(h.render()).props.onClick();
    await settle();
    h.responses.push(reply(result(2, { chain_refresh_error: "Chain provider failed" })));
    await h.tick();
    await running;
    const tree = h.render();
    assert.match(alertText(tree), /Chain provider failed.*Previous results/);
    assert.equal(tables(tree)[0].props.data.rows[0].strike, 1);
    assert.equal(busy(tree).props.inert, false);
  } finally { h.close(); }
});

test("a failed recomputation with unchanged generation is surfaced, not accepted as old ok", async () => {
  const h = harness();
  try {
    await ready(h);
    h.responses.push(reply(result()), reply({}, 202));
    const running = refreshButton(h.render()).props.onClick();
    await settle();
    h.responses.push(reply(result(1, { refresh_completed_at: "new-failure", refresh_error: "Evaluation failed" })));
    await h.tick();
    await running;
    assert.match(alertText(h.render()), /Evaluation failed/);
    assert.equal(tables(h.render()).length, 2);
  } finally { h.close(); }
});

test("HTTP polling failures and unavailable error bodies retain last-known-good rows", async () => {
  for (const response of [reply({ error: "Backend unavailable" }, 503), reply({ status: "unavailable", error: "Evaluation failed" })]) {
    const h = harness();
    try {
      await ready(h);
      h.responses.push(reply(result()), reply({}, 202));
      const running = refreshButton(h.render()).props.onClick();
      await settle();
      h.responses.push(response);
      await h.tick();
      await running;
      assert.match(alertText(h.render()), /Backend unavailable|Evaluation failed/);
      assert.equal(tables(h.render()).length, 2);
      assert.equal(busy(h.render()).props.inert, false);
    } finally { h.close(); }
  }
});

test("unexpected and warming responses have bounded polling and timeout keeps existing tables", async () => {
  const h = harness();
  try {
    await ready(h);
    h.responses.push(reply(result()), reply({}, 202));
    const running = refreshButton(h.render()).props.onClick();
    await settle();
    for (let i = 0; i < 30; i++) {
      h.responses.push(reply({ status: i % 2 ? "warming" : "unexpected" }));
      await h.tick();
    }
    await running;
    assert.match(alertText(h.render()), /Timed out/);
    assert.equal(tables(h.render()).length, 2);
    assert.equal(h.requests.length, 33);
  } finally { h.close(); }
});

test("deadline aborts a hung request; unmount cancels fetches and polling timers", async () => {
  const h = harness();
  try {
    await ready(h);
    h.responses.push((options) => new Promise((resolve, reject) => {
      options.signal.addEventListener("abort", () => reject(options.signal.reason), { once: true });
    }));
    const running = refreshButton(h.render()).props.onClick();
    await h.tick(120_000);
    await running;
    assert.equal(h.requests.at(-1).options.signal.aborted, true);
    assert.match(alertText(h.render()), /timed out/);
    h.responses.push(reply(result()), reply({}, 202));
    refreshButton(h.render()).props.onClick();
    await settle();
    assert.equal(h.timers.size, 2);
  } finally {
    h.close();
    await settle();
    assert.equal(h.requests.at(-1).options.signal.aborted, true);
  }
});

test("symbol changes abort old work and late initial loads cannot overwrite another symbol", async () => {
  const h = harness();
  try {
    let late;
    h.responses.push(() => new Promise((resolve) => { late = resolve; }));
    h.render();
    await settle();
    h.responses.push(reply(result(10)));
    h.render("MSFT");
    assert.equal(h.requests[0].options.signal.aborted, true);
    await settle();
    late(reply(result(99)));
    await settle();
    assert.equal(tables(h.render())[0].props.data.rows[0].strike, 10);
    h.responses.push(reply(result(10)), reply({}, 202));
    refreshButton(h.render()).props.onClick();
    await settle();
    h.responses.push(reply(result(20)));
    h.render("AMD");
    await settle();
    assert.equal(tables(h.render())[0].props.data.rows[0].strike, 20);
    assert.equal(h.timers.size, 0);
    assert.equal(h.requests.at(-2).options.signal.aborted, true);
  } finally { h.close(); }
});

test("a late warming auto-load cannot overwrite a completed manual refresh", async () => {
  const h = harness();
  try {
    await ready(h, { status: "warming", retry_after: 15 });
    let late;
    h.responses.push(() => new Promise((resolve) => { late = resolve; }));
    await h.tick(15_000);
    const oldRequest = h.requests.at(-1);
    h.responses.push(reply({ status: "warming" }), reply({}, 202));
    const button = h.render().find((node) => node.type === "button" && node.props.children?.[1] === " Refresh Now");
    const running = button.props.onClick();
    assert.equal(oldRequest.options.signal.aborted, true);
    await settle();
    h.responses.push(reply(result(2)));
    await h.tick();
    await running;
    late(reply(result(99)));
    await settle();
    assert.equal(tables(h.render())[0].props.data.rows[0].strike, 2);
    assert.equal(busy(h.render()).props.inert, false);
  } finally { h.close(); }
});

test("a rejected POST keeps tables and an explicit retry can successfully join a 409 refresh", async () => {
  const h = harness();
  try {
    await ready(h);
    h.responses.push(reply(result()), reply({ error: "Refresh service unavailable" }, 503));
    await refreshButton(h.render()).props.onClick();
    assert.match(alertText(h.render()), /Refresh service unavailable/);
    assert.equal(tables(h.render())[0].props.data.rows[0].strike, 1);
    h.responses.push(reply(result()), reply({}, 409));
    const running = refreshButton(h.render()).props.onClick();
    assert.equal(alertText(h.render()), undefined);
    await settle();
    h.responses.push(reply(result(2)));
    await h.tick();
    await running;
    assert.equal(tables(h.render())[0].props.data.rows[0].strike, 2);
    assert.equal(refreshButton(h.render()).props.disabled, false);
  } finally { h.close(); }
});

test("warming auto-retry cannot clear manual refresh or restart after its failure", async () => {
  const h = harness();
  try {
    await ready(h, { status: "warming", retry_after: 15 });
    h.responses.push(reply({ status: "warming" }), reply({}, 202));
    const button = h.render().find((node) => node.type === "button" && node.props.children?.[1] === " Refresh Now");
    assert.ok(button);
    const running = button.props.onClick();
    await settle();
    assert.ok(![...h.timers.values()].some((timer) => timer.delay === 15_000));
    h.render();
    h.responses.push(reply({ error: "No chain" }, 503));
    await h.tick();
    await running;
    assert.match(alertText(h.render()), /No chain/);
    assert.equal(h.timers.size, 0);
  } finally { h.close(); }
});

test("refresh proxy preserves 202, 409 and upstream failures and propagates abort", async () => {
  const previous = globalThis.fetch;
  const { POST } = load("app/api/symbols/[symbol]/best-options/refresh/route.ts", {
    "next/server": { NextResponse: { json: (body, options) => ({ body, status: options?.status ?? 200 }) } },
    "@/lib/api": { API_BASE_URL: "http://backend" },
    "@/lib/symbolEncoding": { decodeSymbolParam: (value) => value },
  });
  try {
    for (const status of [202, 409, 503]) {
      const request = new Request("http://frontend/api/refresh", { method: "POST" });
      globalThis.fetch = async (url, options) => {
        assert.equal(url, "http://backend/api/symbols/AAPL/best-options/refresh");
        assert.equal(options.signal, request.signal);
        assert.equal(options.cache, "no-store");
        return reply({ message: "upstream" }, status);
      };
      const response = await POST(request, { params: Promise.resolve({ symbol: "AAPL" }) });
      assert.equal(response.status, status);
      assert.deepEqual(response.body, { message: "upstream" });
    }
  } finally { globalThis.fetch = previous; }
});
