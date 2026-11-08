import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";
import ts from "typescript";

const readSource = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const formatSource = readSource("../src/lib/format.ts");
const compiledFormat = ts.transpileModule(formatSource, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { averageCompletedMonthsExcludingZero, averageLastNExcludingZero } = await import(
  `data:text/javascript;base64,${Buffer.from(compiledFormat).toString("base64")}`
);
const november = new Date(2026, 10, 8);

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
    .map((name) => `const ${declarations.get(name).getText(parsed)};`).join("\n")
    .replaceAll("averageCompletedMonthsExcludingZero(", "averageCompletedMonthsAtReference(");
  const compiled = ts.transpileModule(`
    ${formatSource}
    ${helpers.join("\n")}
    const useMemo = (compute) => compute();
    export function averageCard(monthly, now = new Date(2026, 10, 8)) {
      const data = { monthly };
      const averageCompletedMonthsAtReference = (rows, getValue, n) =>
        averageCompletedMonthsExcludingZero(rows, getValue, n, now);
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
  ["EconomicsView", (value) => ({ net_income_eur: value }), (month) => {
    const [year, number] = month.split("-").map(Number);
    return { year, month: number };
  }],
  ["EconomicsOverviewView", (value) => ({ combined_net_eur: value, options_net_eur: -999 }), (month) => ({ month })],
  ["DividendsView", (value, index) => value == null || !Number.isFinite(value)
    ? { net_eur: value }
    : index % 3 === 0
    ? { total_dividends_eur: value, total_net: -999, net_eur: -999 }
    : index % 3 === 1
      ? { total_net: value, net_eur: -999 }
      : { net_eur: value }, (month) => ({ month })],
];

for (const [viewName, rowFromValue, rowFromMonth] of views) {
  const { source, averageCard } = await loadAverageCard(viewName);
  const rows = (entries) => entries.map(([month, value], index) => ({
    ...rowFromMonth(month), ...rowFromValue(value, index),
  }));
  describe(`${viewName} monthly average references`, () => {
    it("uses May–October and August–October regardless of input order, leaving 12m unchanged", () => {
      const entries = [
        ["2026-04", 900], ["2026-05", 10], ["2026-06", 20], ["2026-07", 30],
        ["2026-08", 40], ["2026-09", 50], ["2026-10", 60],
        ["2026-11", 999999], ["2026-12", 888888],
      ];
      for (const ordered of [entries, [...entries].reverse()]) {
        const card = averageCard(rows(ordered));
        assert.equal(card.value, averageLastNExcludingZero(ordered.map(([, value]) => value), 12));
        assert.equal(card.hint, "6m: €35.00 · 3m: €50.00");
      }
      assert.equal((source.match(/averageCompletedMonthsExcludingZero\(/g) ?? []).length, 2);
      assert.equal((source.match(/averageLastNExcludingZero\([^\n]*, 12\)/g) ?? []).length, 1);
      assert.doesNotMatch(source, /Months with no activity are excluded from the average/);
    });

    it("shows dashes when there is no activity or no available average", () => {
      for (const values of [[], [0, 0, 0], [null, undefined, NaN, Infinity]]) {
        const card = averageCard(rows(values.map((value, index) => [`2026-0${index + 6}`, value])));
        assert.equal(card.value, undefined);
        assert.equal(card.hint, "6m: — · 3m: —");
      }
      assert.equal(averageCard(rows([["2026-04", 100], ["2026-11", 900]])).hint, "6m: — · 3m: —");
    });

    it("does not replace missing months with older data", () => {
      const card = averageCard(rows([["2026-04", 9999], ["2026-06", 20], ["2026-10", -40]]));
      assert.equal(card.hint, "6m: -€10.00 · 3m: -€40.00");
    });

    it("keeps valid cancellation averages as zero and supports short negative datasets", () => {
      const card = averageCard(rows([["2026-06", 100], ["2026-08", 50], ["2026-09", 0], ["2026-10", -50]]));
      assert.equal(card.hint, "6m: €33.33 · 3m: €0.00");
      assert.equal(averageCard(rows([["2026-10", -30]])).hint, "6m: -€30.00 · 3m: -€30.00");
    });

    it("uses available previous-year months in January without pulling older months", () => {
      const card = averageCard(rows([
        ["2025-06", 9999], ["2025-07", 10], ["2025-10", 20],
        ["2025-11", 30], ["2025-12", 40], ["2026-01", 999999],
      ]), new Date(2026, 0, 1));
      assert.equal(card.hint, "6m: €25.00 · 3m: €30.00");
    });

    if (viewName === "EconomicsView") {
      it("passes the computed short-window references to the summary card", () => {
        assert.match(source, /<SummaryRow[^>]*avgLast6=\{avgLast6\} avgLast3=\{avgLast3\}/);
      });
    }
  });
}

describe("completed calendar-month average helper", () => {
  const average = (rows, n = 3, now = november) =>
    averageCompletedMonthsExcludingZero(rows, (row) => row.value, n, now);

  it("quietly skips malformed dates and nonfinite amounts", () => {
    const invalid = [
      { month: "2026-13", value: 900 }, { month: "2026-00", value: 900 },
      { month: "2026-8", value: 900 }, { month: "2026-10-01", value: 900 },
      { month: "bad", value: 900 }, { month: null, value: 900 },
      { year: 2026, month: 10.5, value: 900 }, { year: NaN, month: 10, value: 900 },
      { month: 10, value: 900 }, { year: 2026.5, month: 10, value: 900 },
      ...[0, null, undefined, NaN, Infinity].map((value) => ({ month: "2026-10", value })),
    ];
    assert.equal(average(invalid), null);
    assert.equal(average([...invalid, { year: 2026, month: 8, value: -20 }, { month: "2026-10", value: 60 }]), 20);
  });

  it("uses local calendar boundaries, including the first and last instant of a month", () => {
    const rows = [{ month: "2026-07", value: 10 }, { month: "2026-10", value: 1000 }];
    assert.equal(average(rows, 3, new Date(2026, 9, 31, 23, 59, 59)), 10);
    assert.equal(average(rows, 3, new Date(2026, 10, 1, 0, 0, 0)), 1000);
  });

  it("returns null for empty windows or invalid reference dates", () => {
    assert.equal(average([]), null);
    assert.equal(average([{ month: "2026-10", value: 100 }], 0), null);
    assert.equal(average([{ month: "2026-10", value: 100 }], 3, new Date(NaN)), null);
  });
});
