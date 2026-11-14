import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import React from "react";
import ts from "typescript";

const __dir = dirname(fileURLToPath(import.meta.url));
const root = join(__dir, "..");
const economicsView = readFileSync(join(root, "src/components/EconomicsView.tsx"), "utf8");
const formatSource = readFileSync(join(root, "src/lib/format.ts"), "utf8");
const compiledFormat = ts.transpileModule(formatSource, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const formatModuleUrl = `data:text/javascript;base64,${Buffer.from(compiledFormat).toString("base64")}`;
const { averageLastNExcludingZero } = await import(formatModuleUrl);

function renderMonthlyChart(rows, avgLast12 = null) {
  const parsed = ts.createSourceFile("EconomicsView.tsx", economicsView, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const chart = parsed.statements.find((node) =>
    ts.isFunctionDeclaration(node) && node.name?.text === "MonthlyNetChart");
  const colors = parsed.statements.filter((node) =>
    ts.isVariableStatement(node) && /const (CALLS|PUTS|NET).*COLOR =/.test(node.getText(parsed)));
  const compiled = ts.transpileModule(
    `${colors.map((node) => node.getText(parsed)).join("\n")}\nexport ${chart.getText(parsed)}`,
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React } },
  ).outputText;
  const exports = {};
  const chartComponents = Object.fromEntries([
    "ResponsiveContainer", "BarChart", "CartesianGrid", "XAxis", "YAxis",
    "ReferenceLine", "Tooltip", "ChartTooltip", "Legend", "Bar",
  ].map((name) => [name, name]));
  runInNewContext(compiled, { exports, React, ...chartComponents, eur: (value) => `€${value}` });
  return exports.MonthlyNetChart({ rows, avgLast12 });
}

describe("Monthly options cash-flow series", () => {
  it("renders five distinct series with positive buyback costs and the authoritative signed net", () => {
    const rows = [
      { label: "Dec 2025", calls_premium_net_eur: 100, puts_premium_net_eur: 50,
        calls_buyback_net_eur: 0, puts_buyback_net_eur: 0, net_income_eur: 150,
        calls_net_income_eur: 100, puts_net_income_eur: 50 },
      { label: "Jan 2026", calls_premium_net_eur: 48, puts_premium_net_eur: 0,
        calls_buyback_net_eur: 30, puts_buyback_net_eur: 30, net_income_eur: -12,
        calls_net_income_eur: 18, puts_net_income_eur: -30 },
      { label: "Feb 2026", calls_premium_net_eur: 0, puts_premium_net_eur: 0,
        calls_buyback_net_eur: 20, puts_buyback_net_eur: 10, net_income_eur: -30,
        calls_net_income_eur: -20, puts_net_income_eur: -10 },
    ];
    const chart = renderMonthlyChart(rows, 36).props.children.props.children;
    const children = React.Children.toArray(chart.props.children);
    const bars = children.filter((child) => child.type === "Bar");
    assert.deepEqual(bars.map((bar) => bar.props.dataKey), [
      "calls_premium_net_eur", "puts_premium_net_eur",
      "calls_buyback_net_eur", "puts_buyback_net_eur", "net_income_eur",
    ]);
    assert.equal(new Set(bars.map((bar) => bar.props.fill)).size, 5);
    assert.match(bars[2].props.name, /Calls Buybacks.*cost/);
    assert.match(bars[3].props.name, /Puts Buybacks.*cost/);
    for (const [index, row] of chart.props.data.entries()) {
      for (const bar of bars) assert.equal(row[bar.props.dataKey], rows[index][bar.props.dataKey]);
      assert.ok(row.calls_buyback_net_eur >= 0 && row.puts_buyback_net_eur >= 0);
      assert.equal(
        row.calls_premium_net_eur + row.puts_premium_net_eur
          - row.calls_buyback_net_eur - row.puts_buyback_net_eur,
        row.net_income_eur,
      );
    }
    assert.ok(children.some((child) => child.type === "ReferenceLine" && child.props.y === 0));
    assert.ok(children.some((child) => child.type === "ReferenceLine" && child.props.y === 36));
    assert.ok(children.some((child) => child.type === "Tooltip" && child.props.content.type === "ChartTooltip"));
    assert.ok(children.some((child) => child.type === "Legend"));
    assert.match(economicsView, /Buyback costs are shown positive for comparison/);
  });
});

describe("Monthly net average reference line", () => {
  it("passes one shared last-12-month average to the card and chart", () => {
    assert.match(
      economicsView,
      /const avgLast12 = useMemo\(\s*\(\) => averageLastNExcludingZero\(data\?\.monthly\.map\(\(row\) => row\.net_income_eur\) \?\? \[\], 12\)/,
    );
    assert.match(economicsView, /<SummaryRow summary=\{data\.summary\} avgLast12=\{avgLast12\} avgLast6=\{avgLast6\} avgLast3=\{avgLast3\} \/>/);
    assert.match(economicsView, /<MonthlyNetChart rows=\{data\.monthly\} avgLast12=\{avgLast12\} \/>/);
    assert.equal(
      (economicsView.match(/averageLastNExcludingZero\([^\n]*, 12\)/g) ?? []).length,
      1,
      "The card and chart must not calculate separate averages.",
    );
  });

  it("renders the shared value as a labeled horizontal reference line", () => {
    assert.match(economicsView, /\{avgLast12 !== null && \(\s*<ReferenceLine/);
    assert.match(economicsView, /y=\{avgLast12\}/);
    assert.match(economicsView, /value: `12mo avg \$\{eur\(avgLast12\)\}`/);
    assert.match(economicsView, /stroke="var\(--accent-blue\)"/);
    assert.match(economicsView, /strokeDasharray="4 4"/);
  });

  it("preserves negative and exact-zero averages", () => {
    assert.equal(averageLastNExcludingZero([-30, -10], 12), -20);
    assert.equal(averageLastNExcludingZero([-25, 25], 12), 0);
  });

  it("matches card behavior for missing, zero-only, and short datasets", () => {
    assert.equal(averageLastNExcludingZero([], 12), null);
    assert.equal(averageLastNExcludingZero([null, undefined, 0], 12), null);
    assert.equal(averageLastNExcludingZero([120], 12), 120);
    assert.equal(averageLastNExcludingZero([120, 0, null, 60], 12), 90);
  });

  it("uses only the most recent 12 months", () => {
    assert.equal(averageLastNExcludingZero([1, ...Array(11).fill(12), 24], 12), 13);
  });
});
