import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const dividends = fs.readFileSync(
  new URL("../src/components/DividendsView.tsx", import.meta.url),
  "utf8",
);
const overview = fs.readFileSync(
  new URL("../src/components/EconomicsOverviewView.tsx", import.meta.url),
  "utf8",
);

function functionSlice(source, name, nextName) {
  const start = source.indexOf(`function ${name}`);
  const end = nextName ? source.indexOf(`function ${nextName}`, start) : source.length;
  assert.ok(start >= 0, `Expected ${name} in source`);
  return source.slice(start, end > start ? end : source.length);
}

test("uses backend-authored combined totals everywhere without a client scrip formula", () => {
  const getTotal = functionSlice(dividends, "getTotalNet", "getCumulativeTotalNet");
  const summary = functionSlice(dividends, "SummaryRow", "MonthlySection");
  const monthlyChart = functionSlice(dividends, "MonthlyNetChart", "YearOverYearChart");
  const yoyChart = functionSlice(dividends, "YearOverYearChart", "CumulativeChart");
  const cumulative = functionSlice(dividends, "CumulativeChart", "DividendsDetail");

  assert.match(getTotal, /total_dividends_eur/);
  assert.match(summary, /getNetDividendsReceived\(summary\)/);
  assert.match(monthlyChart, /getTotalNet\(row\)|total_dividends_eur/);
  assert.match(yoyChart, /getTotalNet\(row\)|total_dividends_eur/);
  assert.match(cumulative, /getCumulativeTotalNet\(row\)/);

  for (const source of [dividends, overview]) {
    assert.doesNotMatch(
      source,
      /share_fmv(?:_eur)?\s*[-+]\s*(?:personal_contribution|scrip_personal_contribution)/,
    );
    assert.doesNotMatch(
      source,
      /scrip_dividends_eur\s*[:=]\s*[^,\n]*(?:cash_net|total_net_eur)/,
    );
  }
});

test("dividend tables expose separate scrip and authoritative total columns", () => {
  const monthly = functionSlice(dividends, "MonthlySection", "yocLabel");
  const bySymbol = functionSlice(dividends, "BySymbolSection", "ByYearSection");
  const byYear = functionSlice(dividends, "ByYearSection", "completeMonthsForYear");

  assert.ok((dividends.match(/label: "Scrip Dividends"|>Scrip Dividends</g) ?? []).length >= 3);
  assert.ok((dividends.match(/label: "Total Dividends"|>Total Dividends</g) ?? []).length >= 3);
  for (const section of [monthly, bySymbol, byYear]) {
    assert.match(section, /scrip_dividends_eur/);
    assert.match(section, /getTotalNet/);
  }
});

test("shows partial coverage as a badge and tooltip instead of coercing null to zero", () => {
  assert.match(dividends, /Partial/);
  assert.match(dividends, /scrip_events_valued/);
  assert.match(dividends, /scrip_events_total/);
  assert.match(dividends, /scrip_valuation_status/);
  assert.match(dividends, /title=.*(?:valued|coverage)|(?:valued|coverage).*title=/s);

  const scripRendering = dividends
    .split("\n")
    .filter((line) => line.includes("scrip_dividends_eur"))
    .join("\n");
  assert.doesNotMatch(scripRendering, /scrip_dividends_eur\s*(?:\?\?|\|\|)\s*0/);
});

test("removes the Dividend Count card and uses accepted labels and wider primary layout", () => {
  const summary = functionSlice(dividends, "SummaryRow", "MonthlySection");

  assert.doesNotMatch(summary, /label:\s*"Dividend Count"/);
  assert.match(summary, /Avg Monthly Total \(last 12mo\)/);
  assert.match(summary, /Cash Yield on Cost/);
  assert.match(summary, /Scrip Dividends/);
  assert.match(
    summary,
    /Fair value of shares less personal contributions and attributable fees/,
  );
  assert.match(
    summary,
    /lg:grid-cols-\[minmax\(0,1\.5fr\)_minmax\(0,1fr\)\]/,
  );
});

test("YoY percentage is unavailable whenever either compared year is partial or unavailable", () => {
  const yoy = functionSlice(dividends, "computeYoyGrowth", "YoyGrowthSection");

  assert.match(yoy, /total_dividends_is_partial/);
  assert.match(yoy, /thisYearIsPartial/);
  assert.match(yoy, /priorYearIsPartial/);
  assert.match(yoy, /pctChange[\s\S]*null|null[\s\S]*pctChange/);
});

test("overview consumes server cash, scrip, total, combined, and coverage fields directly", () => {
  assert.match(overview, /Dividends Total/);
  assert.match(overview, /dividends_cash_net_eur/);
  assert.match(overview, /dividends_scrip_eur/);
  assert.match(overview, /dividends_total_net_eur/);
  assert.match(overview, /combined_net_eur/);
  assert.match(overview, /scrip_valuation_status/);
  assert.match(overview, /total_dividends_is_partial/);

  assert.doesNotMatch(overview, /const withTotals/);
  assert.doesNotMatch(
    overview,
    /dividends_net_eur:\s*(?:report|row)\.[^\n]*dividends_total_net_eur/,
  );
});

test("overview fallback logic preserves legitimate zero and negative server values", () => {
  const forbiddenOrFallbacks = [
    /dividends_total_net_eur\s*\|\|/,
    /dividends_scrip_eur\s*\|\|/,
    /combined_net_eur\s*\|\|/,
    /total_dividends_eur\s*\|\|/,
  ];
  for (const pattern of forbiddenOrFallbacks) {
    assert.doesNotMatch(overview, pattern);
    assert.doesNotMatch(dividends, pattern);
  }
});
