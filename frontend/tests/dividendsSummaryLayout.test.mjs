import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const source = fs.readFileSync(
  new URL("../src/components/DividendsView.tsx", import.meta.url),
  "utf8",
);
const summaryStart = source.indexOf("function SummaryRow");
const summaryEnd = source.indexOf("function MonthlySection", summaryStart);
const summary = source.slice(summaryStart, summaryEnd);
const primaryStart = summary.indexOf("<section");
const primaryEnd = summary.indexOf("</section>", primaryStart);
const primary = summary.slice(primaryStart, primaryEnd);
const siblingsStart = summary.indexOf('data-testid="dividends-sibling-cards"');
const siblings = summary.slice(siblingsStart);

test("groups the canonical dividend totals inside the accessible primary card", () => {
  assert.match(summary, /const netDividendsReceived = getNetDividendsReceived\(summary\)/);
  assert.match(summary, /totalGross = Number\.isFinite\(summary\.total_gross_eur\)/);
  assert.match(summary, /totalWithholding = Number\.isFinite\(summary\.total_withholding_eur\)/);
  assert.match(summary, /effectiveWithholding = Number\.isFinite\(summary\.effective_withholding_pct\)/);
  assert.match(primary, /aria-labelledby="total-dividends-label"/);
  assert.match(primary, /<dl /);

  for (const label of [
    "Total Dividends",
    "Total Gross",
    "Total Withholding",
    "Effective Withholding",
  ]) {
    assert.match(primary, new RegExp(label));
  }

  assert.match(primary, /totalGross == null \? "—" : eur\(totalGross\)/);
  assert.match(primary, /totalWithholding == null \? "—" : eur\(totalWithholding\)/);
  assert.match(
    primary,
    /effectiveWithholding == null \? "—" : `\$\{effectiveWithholding\.toFixed\(2\)\}%`/,
  );
  assert.doesNotMatch(primary, /<StatCard|cash_net|Cash Net|Rights/);
});

test("keeps exactly three existing metrics as sibling cards in their existing order", () => {
  const cardsStart = summary.indexOf("const cards = [");
  const cardsEnd = summary.indexOf("];", cardsStart);
  const cards = summary.slice(cardsStart, cardsEnd);

  assert.equal((siblings.match(/<StatCard/g) ?? []).length, 1);
  assert.equal((cards.match(/label:/g) ?? []).length, 3);

  const labels = [
    "Dividend Count",
    "Avg Monthly Net (last 12mo)",
    "Portfolio Yield on Cost",
  ];
  let previous = -1;
  for (const label of labels) {
    const index = cards.indexOf(`label: "${label}"`);
    assert.ok(index > previous, `Expected ${label} after the prior sibling metric`);
    previous = index;
  }
});

test("uses responsive equal-height grids without nested card components", () => {
  assert.match(
    summary,
    /grid items-stretch gap-4 lg:grid-cols-\[minmax\(0,1fr\)_minmax\(0,1\.5fr\)\]/,
  );
  assert.match(summary, /grid grid-cols-1 items-stretch gap-4 sm:grid-cols-3/);
  assert.match(primary, /h-full/);
  assert.match(primary, /grid grid-cols-1 divide-y[\s\S]*sm:grid-cols-3/);
  assert.equal((primary.match(/className="surface/g) ?? []).length, 1);
});
