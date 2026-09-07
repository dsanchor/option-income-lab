/**
 * Pure tests for the DGI exchange-to-MIC mapping logic.
 * Mirrors toExchangeMic() from DgiScreenerView.tsx (must stay in sync).
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";

// Mirror of toExchangeMic — keep in sync with DgiScreenerView.tsx
function toExchangeMic(exchange) {
  const ex = (exchange ?? "").toUpperCase().replace(/\s+/g, "");
  if (ex === "NYSE") return "XNYS";
  if (ex.startsWith("NASDAQ") || ex === "NMS" || ex === "NGM" || ex === "NCM") return "XNAS";
  return null;
}

describe("toExchangeMic — verified NYSE → XNYS", () => {
  it("NYSE", () => assert.equal(toExchangeMic("NYSE"), "XNYS"));
  it("nyse (lowercase)", () => assert.equal(toExchangeMic("nyse"), "XNYS"));
});

describe("toExchangeMic — verified Nasdaq → XNAS", () => {
  it("NASDAQ", () => assert.equal(toExchangeMic("NASDAQ"), "XNAS"));
  it("Nasdaq (mixed case)", () => assert.equal(toExchangeMic("Nasdaq"), "XNAS"));
  it("NASDAQ Global Select Market (spaces stripped)", () =>
    assert.equal(toExchangeMic("NASDAQ Global Select Market"), "XNAS"));
  it("NasdaqGS", () => assert.equal(toExchangeMic("NasdaqGS"), "XNAS"));
  it("NMS", () => assert.equal(toExchangeMic("NMS"), "XNAS"));
  it("NGM", () => assert.equal(toExchangeMic("NGM"), "XNAS"));
  it("NCM", () => assert.equal(toExchangeMic("NCM"), "XNAS"));
});

describe("toExchangeMic — unresolvable → null (must not proceed)", () => {
  it("undefined → null", () => assert.equal(toExchangeMic(undefined), null));
  it("empty string → null", () => assert.equal(toExchangeMic(""), null));
  it("AMEX → null (not XASE)", () => assert.equal(toExchangeMic("AMEX"), null));
  it("NYSE American → null", () => assert.equal(toExchangeMic("NYSE American"), null));
  it("ARCA → null", () => assert.equal(toExchangeMic("ARCA"), null));
  it("NYSEArca → null", () => assert.equal(toExchangeMic("NYSEArca"), null));
  it("OTC → null", () => assert.equal(toExchangeMic("OTC"), null));
  it("BATS → null", () => assert.equal(toExchangeMic("BATS"), null));
  it("completely unknown → null", () => assert.equal(toExchangeMic("FOOBAR"), null));
});

describe("toExchangeMic — no raw exchange string passes through", () => {
  it("NYSE does not return the string 'NYSE'", () =>
    assert.notEqual(toExchangeMic("NYSE"), "NYSE"));
  it("NASDAQ does not return the string 'NASDAQ'", () =>
    assert.notEqual(toExchangeMic("NASDAQ"), "NASDAQ"));
  it("unknown does not return the input", () =>
    assert.notEqual(toExchangeMic("FOOBAR"), "FOOBAR"));
});
