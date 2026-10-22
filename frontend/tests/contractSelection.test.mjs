import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(new URL("../package.json", import.meta.url));
const ts = require("typescript");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const source = readFileSync(new URL("../src/components/ActivityDetailView.tsx", import.meta.url), "utf8");
const compiled = ts.transpileModule(`${source}\nexport { ContractSelectionPanel };`, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const componentModule = { exports: {} };
const load = (id) => id === "react/jsx-runtime" ? require(id) : () => null;
new Function("require", "module", "exports", compiled)(load, componentModule, componentModule.exports);

const requested = { strike: 85, expiration: "2026-11-13", premium: 0.67, delta: 0.285 };
const render = (overrides) => renderToStaticMarkup(React.createElement(
  componentModule.exports.ContractSelectionPanel,
  { activity: { requested_contract: requested, selected_contract: requested, ...overrides } },
));

test("same-contract relaxation is labeled explicitly, not as unrelaxed approval", () => {
  const html = render({
    activity: "SELL", validation_status: "approved", selection_source: "requested_relaxed",
    relaxed_parameter: "theta_below_minimum",
    comparison_rationale: "Theta 0.042 vs 0.050/day; accepting lower daily decay",
  });
  assert.match(html, /Requested contract · relaxed/);
  assert.match(html, /Theta Below Minimum/);
  assert.match(html, /0.042 vs 0.050/);
  assert.match(html, /85/);
  assert.doesNotMatch(html, /Validated ✓|passed all validation gates/);
});

test("WAIT and incomplete reviews never imply the same contract passed entry gates", () => {
  for (const overrides of [
    { activity: "WAIT", validation_status: "approved" },
    { activity: "SELL", validation_status: "review_incomplete" },
  ]) {
    const html = render(overrides);
    assert.match(html, /Not approved for entry/);
    assert.doesNotMatch(html, /Validated ✓|passed all validation gates/);
  }
});

test("unrelaxed requested contract and different-contract alternatives keep distinct labels", () => {
  assert.match(render({
    activity: "SELL", validation_status: "approved", selection_source: "requested_approved",
  }), /Validated ✓/);
  const html = render({
    activity: "SELL", validation_status: "approved", selection_source: "alpha_alternative",
    selected_contract: { ...requested, strike: 83 },
  });
  assert.match(html, /Alternative Selected/);
  assert.match(html, /Alpha Advisor alternative/);
});
