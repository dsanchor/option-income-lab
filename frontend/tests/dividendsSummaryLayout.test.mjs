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
const escapeRegex = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

test("groups the canonical dividend totals inside the accessible primary card", () => {
  assert.match(summary, /const netDividendsReceived = getNetDividendsReceived\(summary\)/);
  assert.match(summary, /summary\.cash_net/);
  assert.match(summary, /summary\.scrip_dividends_eur/);
  assert.match(summary, /totalGross = Number\.isFinite\(summary\.total_gross_eur\)/);
  assert.match(summary, /totalWithholding = Number\.isFinite\(summary\.total_withholding_eur\)/);
  assert.match(summary, /effectiveWithholding = Number\.isFinite\(summary\.effective_withholding_pct\)/);
  assert.match(primary, /aria-labelledby="total-dividends-label"/);
  assert.match(primary, /<dl /);

  for (const label of [
    "Total Dividends",
    "Cash Dividends (Net)",
    "Scrip Dividends (Economic Value)",
    "Total Gross",
    "Total Withholding",
    "Effective Withholding",
  ]) {
    assert.match(primary, new RegExp(escapeRegex(label)));
  }

  assert.match(primary, /totalGross == null \? "—" : eur\(totalGross\)/);
  assert.match(primary, /totalWithholding == null \? "—" : eur\(totalWithholding\)/);
  assert.match(
    primary,
    /effectiveWithholding == null \? "—" : `\$\{effectiveWithholding\.toFixed\(2\)\}%`/,
  );
  assert.match(primary, /Partial/);
  assert.doesNotMatch(primary, /<StatCard|Rights/);
});

test("shows exactly the total card and two intended secondary cards", () => {
  const cardsStart = summary.indexOf("const cards = [");
  const cardsEnd = summary.indexOf("];", cardsStart);
  const cards = summary.slice(cardsStart, cardsEnd);
  const secondaryCardCount = (cards.match(/label:/g) ?? []).length;

  assert.equal((summary.match(/data-testid="total-dividends-card"/g) ?? []).length, 1);
  assert.equal((siblings.match(/<StatCard/g) ?? []).length, 1);
  assert.equal(secondaryCardCount, 2);
  assert.equal(1 + secondaryCardCount, 3);

  const labels = [
    "Avg Monthly Total (last 12mo)",
    "Cash Yield on Cost",
  ];
  let previous = -1;
  for (const label of labels) {
    const index = cards.indexOf(`label: "${label}"`);
    assert.ok(index > previous, `Expected ${label} after the prior sibling metric`);
    previous = index;
  }
  assert.doesNotMatch(cards, /label: "Scrip Dividends"/);
  assert.doesNotMatch(cards, /Dividend Count|Avg Monthly Net|Portfolio Yield on Cost/);
});

test("uses responsive equal-height grids without nested card components", () => {
  assert.match(
    summary,
    /grid items-stretch gap-4 lg:grid-cols-\[minmax\(0,1\.5fr\)_minmax\(0,1fr\)\]/,
  );
  assert.match(summary, /grid grid-cols-1 items-stretch gap-4 sm:grid-cols-2/);
  assert.match(primary, /h-full/);
  assert.match(primary, /grid grid-cols-1 divide-y[\s\S]*sm:grid-cols-3/);
  assert.equal((primary.match(/className="surface/g) ?? []).length, 1);
});
