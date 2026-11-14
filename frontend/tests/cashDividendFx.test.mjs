import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { webcrypto } from "node:crypto";
import { test } from "node:test";

const require = createRequire(new URL("../package.json", import.meta.url));
const ts = require("typescript");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");

function compile(path, load = require, extra = "") {
  const source = readFileSync(new URL(`../src/${path}`, import.meta.url), "utf8");
  const code = ts.transpileModule(source + extra, {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const compiledModule = { exports: {} };
  new Function("require", "module", "exports", "crypto", code)(load, compiledModule, compiledModule.exports, webcrypto);
  return compiledModule.exports;
}

const fx = compile("lib/cashDividendFx.ts");
const shape = compile("lib/caWizardRequestShape.ts");
const base = (patch = {}) => ({
  security_id: "TEST", payment_date: "2024-06-15", currency: "USD",
  fx_rate: "0.9", fx_rate_source: "ECB", fx_preserve_initial: false,
  event_type: "CASH_DIVIDEND", cd_gross: "100", cd_gross_eur: "90",
  cd_wht_src_country: "US", cd_wht_src_usd: "15", cd_wht_src_amount: "13.5",
  cd_fees: "", cd_wht_dest_state: "not_captured", cd_wht_dest_country: "ES", cd_wht_dest_amount: "",
  ...patch,
});

// Exercise the real component's event handlers/effect cleanup without a new DOM dependency.
function hooks() {
  let cursor = 0;
  const slots = [];
  const pending = [];
  const equal = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
  const react = {
    ...React,
    useState(initial) {
      const i = cursor++;
      if (!slots[i]) slots[i] = { value: typeof initial === "function" ? initial() : initial };
      return [slots[i].value, (value) => {
        slots[i].value = typeof value === "function" ? value(slots[i].value) : value;
      }];
    },
    useRef(initial) {
      const i = cursor++;
      return (slots[i] ??= { current: initial });
    },
    useCallback(fn, deps) {
      const i = cursor++;
      if (!equal(slots[i]?.deps, deps)) slots[i] = { value: fn, deps };
      return slots[i].value;
    },
    useEffect(fn, deps) {
      const i = cursor++;
      if (!equal(slots[i]?.deps, deps)) {
        slots[i]?.cleanup?.();
        slots[i] = { deps };
        pending.push(() => { slots[i].cleanup = fn(); });
      }
    },
  };
  return {
    react,
    render(fn, props) { cursor = 0; return fn(props); },
    flush() { pending.splice(0).forEach((fn) => fn()); },
    unmount() { slots.forEach((slot) => slot.cleanup?.()); },
  };
}

function formModule(h = hooks(), api = {}) {
  return compile("components/CorporateActionForm.tsx", (id) => {
    if (id === "react") return h.react;
    if (id === "@/lib/cashDividendFx") return fx;
    if (id === "@/lib/caWizardRequestShape") return shape;
    if (id === "@/lib/portfolio-api") return api;
    if (id === "@/lib/accountDisplay") return { formatAccountName: () => "Test" };
    return require(id);
  }, "\nexport { buildLegs, validate, FxHelper };\n");
}

function nodes(tree) {
  if (Array.isArray(tree)) return tree.flatMap(nodes);
  if (!tree || typeof tree !== "object") return [];
  return [tree, ...nodes(tree.props?.children)];
}

function text(tree) {
  if (Array.isArray(tree)) return tree.map(text).join("");
  if (tree === null || tree === undefined || typeof tree === "boolean") return "";
  return typeof tree === "object" ? text(tree.props?.children) : String(tree);
}

function field(tree, label) {
  const group = nodes(tree).find((n) => n.type === "div" &&
    React.Children.toArray(n.props.children).some((c) => c.type === "label" && text(c) === label));
  return nodes(group).find((n) => n.type === "input");
}

test("gross and source USD convert atomically with exact six-place decimal rounding", () => {
  let state = fx.updateCashDividendFx(base(), { cd_gross: "123.45", cd_wht_src_usd: "18.5175" });
  assert.equal(state.cd_gross_eur, "111.105000");
  assert.equal(state.cd_wht_src_amount, "16.665750");
  state = fx.updateCashDividendFx(state, { fx_rate: "0.8" });
  assert.equal(state.cd_gross_eur, "98.760000");
  assert.equal(state.cd_wht_src_amount, "14.814000");
  assert.equal(fx.nativeToEur("1.000001", "0.5"), "0.500001");
  assert.equal(fx.nativeToEur("0", "0.9"), "0.000000");
});

test("invalid/cleared rate, native withholding and gross clear derived values and block submission", () => {
  const { validate } = formModule();
  for (const rate of ["", "0", "-1", "NaN", "Infinity", "junk"]) {
    const state = fx.updateCashDividendFx(base(), { fx_rate: rate });
    assert.equal(state.cd_gross_eur, "");
    assert.equal(state.cd_wht_src_amount, "");
    assert.match(validate(state), /FX rate/);
  }
  const cleared = fx.updateCashDividendFx(base(), { cd_wht_src_usd: "" });
  assert.equal(cleared.cd_wht_src_amount, "");
  assert.match(validate(cleared), /withholding in USD/);
  assert.equal(validate(fx.updateCashDividendFx(base(), { cd_wht_src_usd: "0" })), null);
  assert.equal(fx.updateCashDividendFx(base(), { cd_gross: "" }).cd_gross_eur, "");
});

test("USD/US only; EUR and other source countries retain primary EUR withholding", () => {
  for (const [currency, country] of [["EUR", "US"], ["USD", "CA"], ["GBP", "US"]]) {
    const state = fx.updateCashDividendFx(base({ currency, cd_wht_src_country: country }), { cd_wht_src_amount: "12.34" });
    assert.equal(state.cd_wht_src_amount, "12.34");
    assert.equal(state.cd_wht_src_usd, null);
  }
  const eur = fx.updateCashDividendFx(base({ currency: "EUR" }), { cd_gross: "150" });
  assert.equal(eur.cd_gross_eur, "150");
  assert.equal(fx.cashDividendFxError(eur), null);
  const left = fx.updateCashDividendFx(base(), { cd_wht_src_country: "CA" });
  assert.equal(left.cd_wht_src_amount, "13.5");
  const entered = fx.updateCashDividendFx(left, { cd_wht_src_country: "US" });
  assert.equal(entered.cd_wht_src_usd, "15.000000000000");
  assert.equal(entered.cd_wht_src_amount, "13.5");
});

test("cash payload uses converted EUR and derived source percentage, no native schema", () => {
  const { buildLegs } = formModule();
  const state = fx.updateCashDividendFx(base(), { fx_rate: "0.8" });
  const [leg] = buildLegs(state);
  assert.equal(leg.gross.eur_amount, "80.000000");
  assert.deepEqual(leg.withholding.source, { country: "US", rate_pct: "15.0000", amount_eur: "12.000000" });
  assert.equal(leg.fx.rate, "0.8");
  assert.deepEqual(Object.keys(leg.withholding.source).sort(), ["amount_eur", "country", "rate_pct"]);
});

test("correction reconstructs native withholding but preserves persisted EUR/FX exactly on mount", () => {
  const h = hooks();
  const mod = formModule(h);
  const leg = {
    ca_leg_type: "CASH_DIVIDEND", ca_event_type: "CASH_DIVIDEND", security_id: "TEST",
    trade_date: "2024-06-15", gross: { currency: "USD", amount: "100", eur_amount: "92.123457" },
    withholding: { source: { country: "US", amount_eur: "13.818518" } },
    fx: { rate: "0.921234567", rate_source: "BROKER" },
  };
  const initialState = mod.buildCaInitialState([leg], leg);
  assert.equal(initialState.cd_wht_src_usd, fx.eurToNative("13.818518", "0.921234567"));
  const props = { accounts: [], securities: [], mode: "correct", initialState, onSuccess() {} };
  let tree = h.render(mod.default, props);
  assert.equal(field(tree, "Origin withholding EUR (auto)").props.value, "13.818518");
  assert.equal(field(tree, "EUR equivalent (auto)").props.value, "92.123457");
  const helper = nodes(tree).find((n) => n.type === mod.FxHelper);
  assert.equal(helper.props.preserveInitial, true);
  assert.equal(helper.props.rateSource, "BROKER");
  field(tree, "Gross amount (USD) *").props.onChange({ target: { value: "200" } });
  tree = h.render(mod.default, props);
  assert.equal(field(tree, "EUR equivalent (auto)").props.value, "184.246913");
  assert.equal(field(tree, "Origin withholding EUR (auto)").props.value, "13.818518");
});

test("real form USD input drives read-only EUR, WHT percentage, destination basis and preview", () => {
  const h = hooks();
  const mod = formModule(h);
  const props = { accounts: [], securities: [], initialState: base(), onSuccess() {} };
  let tree = h.render(mod.default, props);
  field(tree, "Amount USD (primary)").props.onChange({ target: { value: "20" } });
  tree = h.render(mod.default, props);
  assert.equal(field(tree, "Origin withholding EUR (auto)").props.value, "18.000000");
  assert.equal(field(tree, "Origin withholding EUR (auto)").props.readOnly, true);
  assert.equal(field(tree, "EUR equivalent (auto)").props.readOnly, true);
  const sourceRate = nodes(tree).find((n) => n.type?.name === "WhtRateDisplay");
  assert.equal(sourceRate.props.amount, "18.000000");
  assert.match(renderToStaticMarkup(React.createElement(sourceRate.type, sourceRate.props)), /20.00%/);
  const destination = nodes(tree).find((n) => n.type?.name === "WhtDestFields");
  assert.equal(destination.props.grossEur, "72");
  const preview = nodes(tree).find((n) => n.type?.name === "CashDivNetPreview");
  assert.match(renderToStaticMarkup(React.createElement(preview.type, preview.props)), /€72.00/);
  field(tree, "Country").props.onChange({ target: { value: "CA" } });
  tree = h.render(mod.default, props);
  assert.equal(field(tree, "Amount USD (primary)"), undefined);
  assert.equal(field(tree, "Amount € (primary)").props.value, "18.000000");
});

test("manual current-context FX is preserved; changing date clears it and re-enables automatic lookup", () => {
  const h = hooks();
  const mod = formModule(h);
  const props = { accounts: [], securities: [], initialState: base(), onSuccess() {} };
  let tree = h.render(mod.default, props);
  field(tree, "USD/EUR rate").props.onChange({ target: { value: "0.8" } });
  tree = h.render(mod.default, props);
  let helper = nodes(tree).find((n) => n.type === mod.FxHelper);
  assert.equal(helper.props.canAutoFetch(), false);
  assert.equal(helper.props.currentRate, "0.8");
  assert.equal(field(tree, "EUR equivalent (auto)").props.value, "80.000000");
  field(tree, "Payment date *").props.onChange({ target: { value: "2024-06-18" } });
  tree = h.render(mod.default, props);
  helper = nodes(tree).find((n) => n.type === mod.FxHelper);
  assert.equal(helper.props.automatic, true);
  assert.equal(helper.props.canAutoFetch(), true);
  assert.equal(helper.props.currentRate, "");
  assert.equal(field(tree, "EUR equivalent (auto)").props.value, "");
  assert.equal(field(tree, "Origin withholding EUR (auto)").props.value, "");
});

test("create submits the UI's converted source EUR; clearing conversion prevents API calls", async () => {
  const h = hooks();
  const requests = [];
  const mod = formModule(h, { createCorporateAction: async (request) => {
    requests.push(request);
    return {};
  } });
  const props = { accounts: [], securities: [], initialState: base(), onSuccess() {} };
  let tree = h.render(mod.default, props);
  field(tree, "Amount USD (primary)").props.onChange({ target: { value: "20" } });
  tree = h.render(mod.default, props);
  await tree.props.onSubmit({ preventDefault() {} });
  assert.equal(requests.length, 1);
  assert.equal(requests[0].legs[0].withholding.source.amount_eur, "18.000000");
  assert.equal(requests[0].legs[0].withholding.source.rate_pct, "20.0000");
  field(tree, "USD/EUR rate").props.onChange({ target: { value: "" } });
  tree = h.render(mod.default, props);
  await tree.props.onSubmit({ preventDefault() {} });
  assert.equal(requests.length, 1);
  tree = h.render(mod.default, props);
  assert.match(text(tree), /valid payment-date FX rate/);
});

const tick = () => new Promise((resolve) => setTimeout(resolve, 5));
function lookupHarness(patch = {}) {
  const h = hooks();
  const requests = [];
  const mod = formModule(h, {
    getFxRate(currency, to, date) {
      return new Promise((resolve, reject) => requests.push({ currency, to, date, resolve, reject }));
    },
  });
  let revision = 0;
  let props = {
    currency: "USD", date: "2024-06-15", currentRate: "", rateSource: "ECB",
    automatic: true, preserveInitial: false, getRevision: () => revision,
    canAutoFetch: () => true,
    onRate(rate, currency, date, expected) {
      if (expected !== revision || currency !== props.currency || date !== props.date) return false;
      props.currentRate = rate;
      return true;
    },
    ...patch,
  };
  return {
    requests,
    render(patch = {}) {
      props = { ...props, ...patch };
      const tree = h.render(mod.FxHelper, props);
      h.flush();
      return tree;
    },
    edit(rate) { revision++; props.currentRate = rate; props.rateSource = "MANUAL"; },
    unmount: h.unmount,
    rate: () => props.currentRate,
  };
}
const observation = (request, rate = "0.9", date = request.date) => ({
  from_currency: request.currency, to_currency: "EUR", rate, date, rate_source: "ECB",
});

test("automatic lookup follows valid currency/payment date; delayed older requests cannot overwrite", async () => {
  const h = lookupHarness();
  h.render();
  await tick();
  assert.equal(h.requests.length, 1);
  h.render({ date: "2024-06-17" });
  await tick();
  assert.equal(h.requests.length, 2);
  h.requests[1].resolve(observation(h.requests[1], "0.8", "2024-06-14"));
  await tick();
  assert.equal(h.rate(), "0.8");
  assert.match(text(h.render()), /ECB publication: 2024-06-14/);
  h.requests[0].resolve(observation(h.requests[0], "0.7"));
  await tick();
  assert.equal(h.rate(), "0.8");
  h.render({ currency: "GBP", currentRate: "" });
  await tick();
  assert.equal(h.requests[2].currency, "GBP");
  h.unmount();
  h.requests[2].resolve(observation(h.requests[2]));
  await tick();
  assert.equal(h.rate(), "");
});

test("manual edit during lookup wins; failures clear prior fetched rate and expose retry", async () => {
  const h = lookupHarness();
  h.render();
  await tick();
  h.edit("0.85");
  h.requests[0].resolve(observation(h.requests[0]));
  await tick();
  assert.equal(h.rate(), "0.85");
  assert.match(text(h.render()), /manual/);
  let tree = h.render({ currentRate: "0.9", rateSource: "ECB" });
  const refresh = nodes(tree).find((n) => n.props?.["aria-label"] === "Refresh rate");
  void refresh.props.onClick();
  h.requests[1].reject(new Error("Mock ECB outage"));
  await tick();
  assert.equal(h.rate(), "");
  tree = h.render();
  assert.match(text(tree), /FX rate unavailable/);
  h.unmount();
});

test("invalid provider observations and delayed failures cannot supply a current conversion", async () => {
  const h = lookupHarness();
  h.render();
  await tick();
  h.render({ currency: "GBP" });
  await tick();
  h.requests[1].resolve(observation(h.requests[1], "0"));
  await tick();
  assert.equal(h.rate(), "");
  assert.match(text(h.render()), /FX rate unavailable/);
  h.requests[0].reject(new Error("Older USD request"));
  await tick();
  assert.equal(h.rate(), "");
  h.unmount();
});

test("correction stored rate is not fetched on mount; partial currency and invalid date never fetch", async () => {
  const h = lookupHarness({ preserveInitial: true, currentRate: "0.9" });
  h.render();
  await tick();
  assert.equal(h.requests.length, 0);
  assert.match(text(h.render()), /stored rate; publication date not recorded/);
  for (const patch of [
    { currency: "U", preserveInitial: false }, { currency: "US" },
    { currency: "USD", date: "2024-02-30" }, { date: "" },
  ]) {
    h.render(patch);
    await tick();
    assert.equal(h.requests.length, 0);
  }
  h.render({ date: "2024-06-18", currentRate: "" });
  await tick();
  assert.equal(h.requests.length, 1);
  h.unmount();
});

test("context changes clear FX and both conversions before asynchronous lookup can complete", () => {
  for (const patch of [{ payment_date: "2024-06-18" }, { currency: "CHF" }]) {
    const state = fx.updateCashDividendFx(base({ fx_preserve_initial: true }), patch);
    assert.equal(state.fx_rate, "");
    assert.equal(state.fx_preserve_initial, false);
    assert.equal(state.cd_gross_eur, "");
    assert.match(fx.cashDividendFxError(state), /FX rate/);
    if (patch.payment_date) assert.equal(state.cd_wht_src_amount, "");
  }
});
