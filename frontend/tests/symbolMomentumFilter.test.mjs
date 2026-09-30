import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  matchesSymbolMomentum,
  SYMBOL_MOMENTUM_OPTIONS,
  symbolMomentumFilterValue,
} from "../src/lib/symbolMomentum.ts";

const component = readFileSync(
  new URL("../src/components/SymbolsTable.tsx", import.meta.url),
  "utf8",
);
const selected = (...values) => new Set(values);

test("momentum options preserve the backend classification order and labels", () => {
  assert.deepEqual(SYMBOL_MOMENTUM_OPTIONS, [
    "Bullish",
    "Bullish (overextended)",
    "Weakening",
    "Neutral",
    "Bearish",
    "Bearish (oversold)",
    "Unknown",
  ]);
});

test("empty selection is the All state and preserves every Symbol", () => {
  const all = selected();
  for (const momentum of ["Bullish", "Neutral", "Unknown", "", undefined]) {
    assert.equal(matchesSymbolMomentum(momentum, all), true);
  }
});

test("one or multiple selected momentums match exact backend-authored labels", () => {
  assert.equal(matchesSymbolMomentum("Bullish", selected("Bullish")), true);
  assert.equal(matchesSymbolMomentum("Bullish (overextended)", selected("Bullish")), false);

  const multiple = selected("Bullish", "Bearish (oversold)");
  assert.equal(matchesSymbolMomentum("Bullish", multiple), true);
  assert.equal(matchesSymbolMomentum("Bearish (oversold)", multiple), true);
  assert.equal(matchesSymbolMomentum("Neutral", multiple), false);
});

test("momentum combines with another Symbol filter using intersection semantics", () => {
  const rows = [
    { symbol: "AAA", category: "DGI", momentum: "Bullish" },
    { symbol: "BBB", category: "Growth", momentum: "Bullish" },
    { symbol: "CCC", category: "DGI", momentum: "Bearish" },
  ];
  const filtered = rows.filter(
    (row) =>
      row.category === "DGI" &&
      matchesSymbolMomentum(row.momentum, selected("Bullish")),
  );
  assert.deepEqual(filtered.map((row) => row.symbol), ["AAA"]);
});

test("Unknown explicitly includes missing, blank, canonical Unknown, and unrecognised values", () => {
  for (const momentum of [undefined, null, "", "  ", "Unknown", "Legacy signal"]) {
    assert.equal(symbolMomentumFilterValue(momentum), "Unknown");
    assert.equal(matchesSymbolMomentum(momentum, selected("Unknown")), true);
  }
  assert.equal(matchesSymbolMomentum("Neutral", selected("Unknown")), false);
});

test("Symbols toolbar exposes an accessible multi-select with an explicit clear state", () => {
  assert.match(component, /Filter Symbols by momentum/);
  assert.match(component, /type="checkbox"/);
  assert.match(component, /All \/ clear/);
  assert.match(component, /matchesSymbolMomentum\(r\.momentum, momentumFilters\)/);
  assert.match(component, /includes missing/);
});

test("expanded momentum selector joins the full filter row instead of escaping a trigger anchor", () => {
  assert.match(
    component,
    /<details className="group min-w-0 max-w-full open:basis-full">/,
  );
  assert.match(
    component,
    /<fieldset className="mt-2 w-full max-w-full rounded-/,
  );
  assert.doesNotMatch(component, /<fieldset className="[^"]*\babsolute\b/);
});
