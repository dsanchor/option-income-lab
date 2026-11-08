import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";
import ts from "typescript";

const readSource = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const formatSource = readSource("../src/lib/format.ts");

async function loadAverageCard(viewName) {
  const source = readSource(`../src/components/${viewName}.tsx`);
  const parsed = ts.createSourceFile(viewName, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const summary = parsed.statements.find((node) => ts.isFunctionDeclaration(node) && node.name?.text === "SummaryRow");
  const declarations = new Map();
  function visit(node) {
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name)) {
      declarations.set(node.name.text, node);
    }
    ts.forEachChild(node, visit);
  }
  visit(parsed);

  const cards = summary.body.statements
    .filter(ts.isVariableStatement)
    .flatMap((node) => [...node.declarationList.declarations])
    .find((node) => node.name.getText(parsed) === "cards").initializer;
  const card = cards.elements.find((node) => node.properties.some(
    (property) => property.name?.getText(parsed) === "label"
      && property.initializer.text.startsWith("Avg Monthly"),
  ));
  const helpers = [`const ${declarations.get("eur").getText(parsed)};`];
  if (viewName === "DividendsView") {
    helpers.push(`const ${declarations.get("amountOrZero").getText(parsed)};`);
    helpers.push(parsed.statements.find(
      (node) => ts.isFunctionDeclaration(node) && node.name?.text === "getTotalNet",
    ).getText(parsed));
  }
  const averages = ["avgLast12", "avgLast6", "avgLast3"]
    .map((name) => `const ${declarations.get(name).getText(parsed)};`).join("\n");
  const compiled = ts.transpileModule(`
    ${formatSource}
    ${helpers.join("\n")}
    const useMemo = (compute) => compute();
    export function averageCard(monthly) {
      const data = { monthly };
      ${averages}
      return ${card.getText(parsed)};
    }
  `, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const { averageCard } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
  return { source, averageCard };
}

const views = [
  ["EconomicsView", (value) => ({ net_income_eur: value })],
  ["EconomicsOverviewView", (value) => ({ combined_net_eur: value, options_net_eur: -999 })],
  ["DividendsView", (value, index) => value == null || !Number.isFinite(value)
    ? { net_eur: value }
    : index % 3 === 0
    ? { total_dividends_eur: value, total_net: -999, net_eur: -999 }
    : index % 3 === 1
      ? { total_net: value, net_eur: -999 }
      : { net_eur: value }],
];

for (const [viewName, rowFromValue] of views) {
  const { source, averageCard } = await loadAverageCard(viewName);
  describe(`${viewName} monthly average references`, () => {
    it("uses independent last-6 and last-3 windows from the original monthly series", () => {
      const values = [9999, ...Array(6).fill(100), 40, 40, 40, -60, 0, -120];
      const card = averageCard(values.map(rowFromValue));
      assert.equal(card.value, 540 / 11);
      assert.equal(card.hint, "6m: -€12.00 · 3m: -€90.00");
      assert.doesNotMatch(source, /Months with no activity are excluded from the average/);
    });

    it("shows dashes when there is no activity or no available average", () => {
      for (const values of [[], [0, 0, 0], [null, undefined, NaN, Infinity]]) {
        const card = averageCard(values.map(rowFromValue));
        assert.equal(card.value, undefined);
        assert.equal(card.hint, "6m: — · 3m: —");
      }
      const card = averageCard([100, 0, 0, 0].map(rowFromValue));
      assert.equal(card.hint, "6m: €100.00 · 3m: —");
    });

    it("keeps valid cancellation averages as zero and supports short negative datasets", () => {
      const card = averageCard([...Array(6).fill(100), 50, 50, 50, -25, 0, 25].map(rowFromValue));
      assert.equal(card.hint, "6m: €30.00 · 3m: €0.00");
      assert.equal(averageCard([-30].map(rowFromValue)).hint, "6m: -€30.00 · 3m: -€30.00");
    });

    if (viewName === "EconomicsView") {
      it("passes the computed short-window references to the summary card", () => {
        assert.match(source, /<SummaryRow[^>]*avgLast6=\{avgLast6\} avgLast3=\{avgLast3\}/);
      });
    }
  });
}
