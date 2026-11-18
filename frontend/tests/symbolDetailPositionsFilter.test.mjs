import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const source = readFileSync(new URL("../src/components/PositionsTable.tsx", import.meta.url), "utf8");
const code = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    jsx: ts.JsxEmit.ReactJSX,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;

function elements(node) {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!node || typeof node !== "object") return [];
  return [node, ...elements(node.props?.children)];
}

function positionsHarness(positions) {
  const hooks = [];
  let cursor = 0;
  const react = {
    ...require("react"),
    useState(initial) {
      const i = cursor++;
      if (!(i in hooks)) hooks[i] = typeof initial === "function" ? initial() : initial;
      return [hooks[i], (value) => {
        hooks[i] = typeof value === "function" ? value(hooks[i]) : value;
      }];
    },
    useMemo: (fn) => fn(),
  };
  const overrides = {
    react,
    "next/navigation": { useRouter: () => ({ refresh() {} }) },
    "@/components/OptionLinkageBadges": {},
    "@/components/PositionDetail": {},
    "@/components/PositionContractCounts": {},
  };
  const loaded = { exports: {} };
  new Function("require", "module", "exports", code)(
    (name) => name in overrides ? overrides[name] : require(name),
    loaded, loaded.exports,
  );
  const h = {
    render() {
      cursor = 0;
      return elements(loaded.exports.default({ symbol: "XNAS:AAPL", positions }));
    },
    checkbox() {
      const label = h.render().find((node) =>
        node.type === "label" && node.props.children.includes("Only opened"));
      assert.ok(label, "The checkbox has a visible wrapping label");
      return elements(label).find((node) => node.type === "input" && node.props.type === "checkbox");
    },
    toggle(checked) {
      h.checkbox().props.onChange({ target: { checked } });
      return h.render();
    },
    click(label, group = "Position status filter") {
      const scope = elements(h.render().find((node) => node.props?.["aria-label"] === group));
      const button = scope.find((node) => node.type === "button" && node.props.children === label);
      assert.ok(button, `Missing ${label} filter`);
      button.props.onClick();
      return h.render();
    },
  };
  return h;
}

const position = (id, status, extra = {}) => ({
  position_id: id, status, type: "call", opened_at: new Date().toISOString(), ...extra,
});
const dataset = [
  position("active", "active"),
  position("rolled", "rolled"),
  position("closed-manual", "closed", { close_reason: "manual" }),
  position("closed-expired", "closed", { close_reason: "expired" }),
  position("closed-assigned", "closed", { close_reason: "assigned" }),
  position("expired", "expired"),
  position("missing-status", undefined),
  position("unknown-status", "unknown"),
  position("old-active", "active", { opened_at: "2000-01-01" }),
];
const renderedIds = (tree) => tree
  .filter((node) => typeof node.type === "symbol" && node.key !== null)
  .map((node) => node.key);

describe("Symbol Detail Option Positions filter", () => {
  it("defaults to checked and shows only canonical active positions, newest first", () => {
    const h = positionsHarness(dataset);
    assert.equal(h.checkbox().props.checked, true);
    assert.deepEqual(renderedIds(h.render()), ["old-active", "active"]);
  });

  it("restores every status when unchecked and reapplies the open filter when checked", () => {
    const h = positionsHarness(dataset);
    assert.deepEqual(renderedIds(h.toggle(false)), dataset.map((p) => p.position_id).reverse());
    assert.equal(h.checkbox().props.checked, false);
    assert.deepEqual(renderedIds(h.toggle(true)), ["old-active", "active"]);
  });

  it("keeps the existing status and date filters synchronized with the checkbox", () => {
    const h = positionsHarness(dataset);
    assert.deepEqual(renderedIds(h.click("Rolled")), ["rolled"]);
    assert.equal(h.checkbox().props.checked, false);
    assert.deepEqual(renderedIds(h.toggle(true)), ["old-active", "active"]);
    assert.deepEqual(renderedIds(h.click("15d", "Position time period")), ["active"]);
    assert.deepEqual(renderedIds(h.toggle(false)), dataset
      .filter((p) => p.position_id !== "old-active").map((p) => p.position_id).reverse());
    h.click("Active");
    assert.equal(h.checkbox().props.checked, true);
  });

  it("preserves the empty state and allows historical-only positions to be revealed", () => {
    const h = positionsHarness([position("rolled", "rolled")]);
    assert.deepEqual(renderedIds(h.render()), []);
    assert.ok(h.render().some((node) =>
      node.type === "td" && node.props.children === "No positions match the current filters."));
    assert.deepEqual(renderedIds(h.toggle(false)), ["rolled"]);
    const empty = positionsHarness([]);
    assert.deepEqual(renderedIds(empty.render()), []);
    assert.deepEqual(renderedIds(empty.toggle(false)), []);
  });
});
