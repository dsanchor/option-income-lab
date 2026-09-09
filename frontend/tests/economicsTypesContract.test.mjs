import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dir = dirname(fileURLToPath(import.meta.url));
const root = join(__dir, "..");

function src(rel) {
  return readFileSync(join(root, rel), "utf8");
}

const economicsTypes = src("src/types/economics.ts");
const dividendsView = src("src/components/DividendsView.tsx");
const overviewView = src("src/components/EconomicsOverviewView.tsx");

describe("Economics type contracts", () => {
  it("exports the new dividends and overview report types", () => {
    for (const name of [
      "DividendsSummary",
      "DividendsMonthlyRow",
      "DividendsBySymbolRow",
      "DividendsYearlyRow",
      "DividendsCumulativeRow",
      "DividendsReport",
      "EconomicsAggregatedSource",
      "EconomicsAggregatedSummary",
      "EconomicsAggregatedMonthlyRow",
      "EconomicsAggregatedBySymbolRow",
      "EconomicsAggregatedReport",
    ]) {
      assert.ok(
        economicsTypes.includes(`export interface ${name}`) ||
          economicsTypes.includes(`export type ${name}`),
        `Expected economics.ts to export ${name}.`
      );
    }
  });

  it("defines dividends report structure with yearly and cumulative sections", () => {
    for (const field of [
      "summary: DividendsSummary;",
      "monthly: DividendsMonthlyRow[];",
      "by_symbol: DividendsBySymbolRow[];",
      "yearly: DividendsYearlyRow[];",
      "cumulative: DividendsCumulativeRow[];",
      "positions: DividendPosition[];",
      "filters: DividendsFilters;",
      "applied_filters: DividendsAppliedFilters;",
      "meta: DividendsMeta;",
    ]) {
      assert.ok(
        economicsTypes.includes(field),
        `Expected DividendsReport to include "${field}".`
      );
    }
  });

  it("wires the new report contracts into the dedicated views", () => {
    assert.ok(
      dividendsView.includes("DividendsReport") &&
        dividendsView.includes("DividendsSummary"),
      "DividendsView should consume the typed dividends contract."
    );
    assert.ok(
      overviewView.includes("EconomicsAggregatedReport") &&
        overviewView.includes("EconomicsAggregatedSummary"),
      "EconomicsOverviewView should consume the typed overview contract."
    );
  });
});
