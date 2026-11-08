import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";
import { runInNewContext } from "node:vm";
import React from "react";
import ts from "typescript";

// Exercise the production selector JSX, hooks and click handlers without a DOM dependency.
function loadSelector(view, selectedName, availableName, chartName) {
  const source = readFileSync(new URL(`../src/components/${view}.tsx`, import.meta.url), "utf8");
  const parsed = ts.createSourceFile(view, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const component = parsed.statements.find(
    (node) => ts.isFunctionDeclaration(node) && node.name?.text === view,
  );
  const nodes = [];
  function visit(node) {
    nodes.push(node);
    ts.forEachChild(node, visit);
  }
  visit(component);
  const state = nodes.find(
    (node) => ts.isVariableStatement(node) && node.getText(parsed).startsWith(`const [${selectedName},`),
  );
  const ref = nodes.find(
    (node) => ts.isVariableStatement(node) && node.getText(parsed).includes("useRef(false)"),
  );
  const effect = nodes.find((node) => ts.isExpressionStatement(node)
    && ts.isCallExpression(node.expression)
    && node.expression.expression.getText(parsed) === "useEffect"
    && node.expression.arguments[1]?.getText(parsed) === `[${availableName}]`);
  const toggle = nodes.find(
    (node) => ts.isFunctionDeclaration(node) && node.name?.text.startsWith("toggle"),
  );
  const selector = nodes.find((node) => ts.isJsxElement(node)
    && node.openingElement.tagName.getText(parsed) === "div"
    && node.children.some((child) => ts.isJsxExpression(child)
      && child.expression?.getText(parsed).startsWith(`${availableName}.map(`)));
  const chart = nodes.find(
    (node) => ts.isJsxSelfClosingElement(node) && node.tagName.getText(parsed) === chartName,
  );
  const chartFunction = parsed.statements.find(
    (node) => ts.isFunctionDeclaration(node) && node.name?.text === chartName,
  );
  const visibleYears = chartFunction.body.statements.find((node) =>
    ts.isVariableStatement(node) && node.getText(parsed).startsWith("const visibleYears ="));
  const compiled = ts.transpileModule(`
    export function renderSelector(${availableName}) {
      ${[state, ref, effect, toggle].map((node) => node.getText(parsed)).join("\n")}
      return { selector: ${selector.getText(parsed)}, chart: ${chart.getText(parsed)} };
    }
    export function chartYears(years, selectedYears) {
      ${visibleYears.getText(parsed)}
      return visibleYears;
    }
  `, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React },
  }).outputText;

  let selection;
  const noneRef = { current: false };
  let dependencies;
  let pendingEffect;
  const timers = new Map();
  const exports = {};
  runInNewContext(compiled, {
    exports, React, [chartName]: chartName, yoyRows: [], comparisonData: { monthly: [] },
    useState(initial) {
      selection ??= initial;
      return [selection, (update) => { selection = typeof update === "function" ? update(selection) : update; }];
    },
    useRef: () => noneRef,
    useEffect(effect, deps) {
      if (!dependencies || deps.some((value, index) => value !== dependencies[index])) {
        pendingEffect = effect;
        dependencies = deps;
      }
    },
    window: {
      setTimeout(callback) { timers.set(callback, callback); return callback; },
      clearTimeout(id) { timers.delete(id); },
    },
  });
  let years = [2024, 2025];
  let rendered;
  function render(nextYears = years) {
    years = nextYears;
    rendered = exports.renderSelector(years);
  }
  function flush() {
    pendingEffect?.();
    pendingEffect = undefined;
    for (const callback of timers.values()) callback();
    timers.clear();
    render();
  }
  function buttons() {
    return React.Children.toArray(rendered.selector.props.children).filter(React.isValidElement);
  }
  function button(label) {
    return buttons().find((element) => String(element.props.children).trim() === String(label)).props;
  }
  render();
  return {
    render, flush, button,
    click(label) { button(label).onClick(); render(); },
    visibleYears: () => Array.from(exports.chartYears(years, rendered.chart.props.selectedYears)),
    selectedYears: () => Array.from(rendered.chart.props.selectedYears),
  };
}

for (const [view, selected, available, chart] of [
  ["DividendsView", "selectedYoyYears", "availableYoyYears", "YearOverYearChart"],
  ["EconomicsOverviewView", "selectedPassiveIncomeYears", "availablePassiveIncomeYears", "PassiveIncomeYoyChart"],
]) {
  describe(`${view} YoY None selector`, () => {
    const harness = () => loadSelector(view, selected, available, chart);

    it("clears the actual chart selection, stays empty across data updates, and allows reselection", () => {
      const ui = harness();
      ui.flush();
      assert.deepEqual(ui.visibleYears(), [2024, 2025]);
      ui.click("None");
      assert.deepEqual(ui.selectedYears(), []);
      assert.deepEqual(ui.visibleYears(), []);
      ui.render([2024, 2025, 2026]);
      ui.click("None");
      ui.flush();
      assert.deepEqual(ui.selectedYears(), []);
      ui.click("None");
      ui.render([]);
      ui.flush();
      ui.render([2025, 2026]);
      ui.flush();
      assert.deepEqual(ui.visibleYears(), []);
      ui.click(2026);
      assert.deepEqual(ui.visibleYears(), [2026]);
      assert.equal(ui.button("None")["aria-pressed"], false);
      assert.equal(ui.button(2026)["aria-pressed"], true);
      ui.click(2026);
      assert.deepEqual(ui.selectedYears(), []);
      assert.equal(ui.button("None")["aria-pressed"], true);
    });

    it("preserves initial defaults and existing valid/invalid selection update behavior", () => {
      const ui = harness();
      ui.render([]);
      ui.flush();
      ui.render([2024, 2025]);
      ui.flush();
      ui.click(2024);
      ui.render([2024, 2025, 2026]);
      ui.flush();
      assert.deepEqual(ui.selectedYears(), [2025]);
      ui.render([2024, 2026]);
      ui.flush();
      assert.deepEqual(ui.selectedYears(), [2024, 2026]);
      ui.click(2024);
      ui.click(2026);
      ui.render([2024, 2025, 2026]);
      ui.flush();
      assert.deepEqual(ui.selectedYears(), [2024, 2025, 2026]);
      ui.click("None");
      ui.click(2025);
      ui.render([2024, 2026]);
      ui.flush();
      assert.deepEqual(ui.selectedYears(), [2024, 2026]);
    });

    it("matches year pill styling and exposes accessible selected states", () => {
      const ui = harness();
      ui.flush();
      const activeYearClass = ui.button(2024).className.trim();
      ui.click(2024);
      assert.equal(ui.button("None").className.trim(), ui.button(2024).className.trim());
      assert.equal(ui.button(2024)["aria-pressed"], false);
      ui.click("None");
      const none = ui.button("None");
      assert.equal(none.className.trim(), activeYearClass);
      assert.equal(none.type, "button");
      assert.equal(none["aria-pressed"], true);
      assert.match(none["aria-label"], /None.*deselect all comparison years/);
      assert.equal(ui.button(2025)["aria-pressed"], false);
    });
  });
}
