import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
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

describe("Monthly net average reference line", () => {
  it("passes one shared last-12-month average to the card and chart", () => {
    assert.match(
      economicsView,
      /const avgLast12 = useMemo\(\s*\(\) => averageLastNExcludingZero\(data\?\.monthly\.map\(\(row\) => row\.net_income_eur\) \?\? \[\], 12\)/,
    );
    assert.match(economicsView, /<SummaryRow summary=\{data\.summary\} avgLast12=\{avgLast12\} \/>/);
    assert.match(economicsView, /<MonthlyNetChart rows=\{data\.monthly\} avgLast12=\{avgLast12\} \/>/);
    assert.equal(
      (economicsView.match(/averageLastNExcludingZero\(/g) ?? []).length,
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
