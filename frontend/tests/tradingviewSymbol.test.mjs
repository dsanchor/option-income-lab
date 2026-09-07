/**
 * Pure tests for TradingView symbol resolution helpers.
 *
 * Tests the hyphen→colon widget-format transform and the fail-closed
 * null-propagation semantics for both widget components.
 *
 * Mirrors the logic in TradingViewSymbolInfo.tsx and RtChart.tsx
 * (widgetSymbol = tvSymbol?.replace("-", ":") ?? null).
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";

// Mirror of the one-liner in both widget components — must stay in sync.
function toWidgetSymbol(tvSymbol) {
  return tvSymbol?.replace("-", ":") ?? null;
}

describe("toWidgetSymbol — hyphen → colon transform", () => {
  it("NYSE-ABBV → NYSE:ABBV", () => assert.equal(toWidgetSymbol("NYSE-ABBV"), "NYSE:ABBV"));
  it("NASDAQ-MSFT → NASDAQ:MSFT", () => assert.equal(toWidgetSymbol("NASDAQ-MSFT"), "NASDAQ:MSFT"));
  it("BME-ACS → BME:ACS (Madrid)", () => assert.equal(toWidgetSymbol("BME-ACS"), "BME:ACS"));
  it("EURONEXT-AD → EURONEXT:AD (Amsterdam)", () => assert.equal(toWidgetSymbol("EURONEXT-AD"), "EURONEXT:AD"));
  it("LSE-BATS → LSE:BATS (London)", () => assert.equal(toWidgetSymbol("LSE-BATS"), "LSE:BATS"));
  it("SIX-NESN → SIX:NESN (Swiss)", () => assert.equal(toWidgetSymbol("SIX-NESN"), "SIX:NESN"));
});

describe("toWidgetSymbol — fail-closed on null/undefined (no widget rendered)", () => {
  it("null → null", () => assert.equal(toWidgetSymbol(null), null));
  it("undefined → null", () => assert.equal(toWidgetSymbol(undefined), null));
});

describe("toWidgetSymbol — only first hyphen replaced (ticker hyphens preserved)", () => {
  // TradingView exchange codes never contain hyphens; tickers occasionally do.
  // JS String.replace(string, ...) replaces only the first occurrence — correct behavior.
  it("NYSE-BRK-B: only first hyphen replaced → NYSE:BRK-B", () =>
    assert.equal(toWidgetSymbol("NYSE-BRK-B"), "NYSE:BRK-B"));
});

describe("toWidgetSymbol — output is never a raw MIC or raw exchange name", () => {
  it("result contains a colon separator, not a hyphen one", () =>
    assert.ok(toWidgetSymbol("NYSE-ABBV")?.includes(":")));
  it("result does not contain raw 'XNYS'", () =>
    assert.ok(!toWidgetSymbol("NYSE-ABBV")?.includes("XNYS")));
  it("result does not contain raw 'XNAS'", () =>
    assert.ok(!toWidgetSymbol("NASDAQ-MSFT")?.includes("XNAS")));
});
