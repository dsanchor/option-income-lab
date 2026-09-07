/**
 * Pure tests for SymbolConfigurationCard helper logic.
 * Covers: ticker derivation, timeAgoShort, initForm field mapping,
 * and the PATCH body construction/ETag contract.
 *
 * All logic mirrored inline — no imports from .tsx (keeps tests pure JS).
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";

// ─── mirror: ticker derivation ────────────────────────────────────────────────
// Same as: security.ticker ?? security.security_id?.split(":")[1] ?? security.security_id

function deriveTicker(sec) {
  return sec.ticker ?? sec.security_id?.split(":")[1] ?? sec.security_id;
}

describe("deriveTicker", () => {
  it("uses explicit ticker when present", () =>
    assert.equal(deriveTicker({ ticker: "ACS", security_id: "XMAD:ACS" }), "ACS"));
  it("extracts after colon when ticker absent", () =>
    assert.equal(deriveTicker({ security_id: "XAMS:AD" }), "AD"));
  it("falls back to full security_id when no colon", () =>
    assert.equal(deriveTicker({ security_id: "LEGACY" }), "LEGACY"));
  it("null ticker falls through to split", () =>
    assert.equal(deriveTicker({ ticker: null, security_id: "XNYS:ABBV" }), "ABBV"));
  it("handles MIC:TICKER with multi-char ticker", () =>
    assert.equal(deriveTicker({ security_id: "XNAS:MSFT" }), "MSFT"));
});

// ─── mirror: initForm ─────────────────────────────────────────────────────────

function initForm(sec) {
  return {
    company_name: sec.company_name ?? "",
    isin: sec.isin ?? "",
    cusip: sec.cusip ?? "",
    sedol: sec.sedol ?? "",
    listing_currency: sec.listing_currency ?? "",
    country: sec.country ?? "",
    asset_class: sec.asset_class ?? "",
    yfinance: sec.provider_symbols?.yfinance ?? "",
    tradingview: sec.provider_symbols?.tradingview ?? "",
  };
}

describe("initForm — maps SecurityMasterInfo to editable form state", () => {
  const fullSec = {
    security_id: "XAMS:AD",
    exchange_mic: "XAMS",
    company_name: "Ahold Delhaize",
    isin: "NL0011794037",
    cusip: null,
    sedol: null,
    listing_currency: "EUR",
    country: "NL",
    asset_class: "Equity",
    provider_symbols: { yfinance: "AD.AS", tradingview: "EURONEXT-AD" },
  };

  it("maps company_name", () => assert.equal(initForm(fullSec).company_name, "Ahold Delhaize"));
  it("maps isin", () => assert.equal(initForm(fullSec).isin, "NL0011794037"));
  it("maps null cusip to empty string", () => assert.equal(initForm(fullSec).cusip, ""));
  it("maps null sedol to empty string", () => assert.equal(initForm(fullSec).sedol, ""));
  it("maps listing_currency", () => assert.equal(initForm(fullSec).listing_currency, "EUR"));
  it("maps country", () => assert.equal(initForm(fullSec).country, "NL"));
  it("maps asset_class", () => assert.equal(initForm(fullSec).asset_class, "Equity"));
  it("maps yfinance override", () => assert.equal(initForm(fullSec).yfinance, "AD.AS"));
  it("maps tradingview override", () => assert.equal(initForm(fullSec).tradingview, "EURONEXT-AD"));

  it("defaults all fields to empty string when security has minimal fields", () => {
    const minimal = { security_id: "XNYS:ABBV", exchange_mic: "XNYS", company_name: "AbbVie" };
    const f = initForm(minimal);
    assert.equal(f.isin, "");
    assert.equal(f.cusip, "");
    assert.equal(f.sedol, "");
    assert.equal(f.listing_currency, "");
    assert.equal(f.country, "");
    assert.equal(f.asset_class, "");
    assert.equal(f.yfinance, "");
    assert.equal(f.tradingview, "");
  });

  it("missing provider_symbols gives empty strings for both overrides", () => {
    const f = initForm({ security_id: "XNYS:ABBV", exchange_mic: "XNYS", company_name: "AbbVie" });
    assert.equal(f.yfinance, "");
    assert.equal(f.tradingview, "");
  });

  it("partial provider_symbols: only yfinance", () => {
    const f = initForm({
      security_id: "XNYS:ABBV", exchange_mic: "XNYS", company_name: "AbbVie",
      provider_symbols: { yfinance: "ABBV" },
    });
    assert.equal(f.yfinance, "ABBV");
    assert.equal(f.tradingview, "");
  });
});

// ─── mirror: PATCH body construction ─────────────────────────────────────────
// Logic from the save() callback — pure transformation, no fetch

function buildPatchBody(form, security) {
  const body = {
    _etag: security._etag,
    company_name: form.company_name || undefined,
    isin: form.isin || null,
    cusip: form.cusip || null,
    sedol: form.sedol || null,
    listing_currency: form.listing_currency || undefined,
    country: form.country || null,
    asset_class: form.asset_class || undefined,
  };
  const providerSymbols = {};
  if (form.yfinance?.trim()) providerSymbols.yfinance = form.yfinance.trim();
  if (form.tradingview?.trim()) providerSymbols.tradingview = form.tradingview.trim();
  if (Object.keys(providerSymbols).length > 0 || security.provider_symbols) {
    body.provider_symbols = Object.keys(providerSymbols).length > 0 ? providerSymbols : null;
  }
  return body;
}

describe("buildPatchBody — PATCH /security body construction", () => {
  const security = { _etag: '"abc123"', provider_symbols: null };

  it("includes _etag from current security", () => {
    const b = buildPatchBody({ company_name: "Test", isin: "", cusip: "", sedol: "",
      listing_currency: "USD", country: "", asset_class: "", yfinance: "", tradingview: "" }, security);
    assert.equal(b._etag, '"abc123"');
  });

  it("empty string isin becomes null (cleared)", () => {
    const b = buildPatchBody({ company_name: "x", isin: "", cusip: "", sedol: "",
      listing_currency: "", country: "", asset_class: "", yfinance: "", tradingview: "" }, security);
    assert.equal(b.isin, null);
  });

  it("non-empty isin is preserved", () => {
    const b = buildPatchBody({ company_name: "x", isin: "US0378331005", cusip: "", sedol: "",
      listing_currency: "", country: "", asset_class: "", yfinance: "", tradingview: "" }, security);
    assert.equal(b.isin, "US0378331005");
  });

  it("empty company_name becomes undefined (omitted from body)", () => {
    const b = buildPatchBody({ company_name: "", isin: "", cusip: "", sedol: "",
      listing_currency: "", country: "", asset_class: "", yfinance: "", tradingview: "" }, security);
    assert.equal(b.company_name, undefined);
  });

  it("provider_symbols included when yfinance set", () => {
    const b = buildPatchBody({ company_name: "", isin: "", cusip: "", sedol: "",
      listing_currency: "", country: "", asset_class: "", yfinance: "ABBV", tradingview: "" }, security);
    assert.deepEqual(b.provider_symbols, { yfinance: "ABBV" });
  });

  it("provider_symbols null when both overrides cleared but security had existing ones", () => {
    const secWithOverrides = { _etag: '"x"', provider_symbols: { yfinance: "OLD" } };
    const b = buildPatchBody({ company_name: "", isin: "", cusip: "", sedol: "",
      listing_currency: "", country: "", asset_class: "", yfinance: "", tradingview: "" }, secWithOverrides);
    assert.equal(b.provider_symbols, null);
  });

  it("provider_symbols omitted when no existing overrides and none entered", () => {
    const b = buildPatchBody({ company_name: "", isin: "", cusip: "", sedol: "",
      listing_currency: "", country: "", asset_class: "", yfinance: "", tradingview: "" }, security);
    assert.ok(!("provider_symbols" in b));
  });

  it("trimmed whitespace in provider overrides", () => {
    const b = buildPatchBody({ company_name: "", isin: "", cusip: "", sedol: "",
      listing_currency: "", country: "", asset_class: "", yfinance: "  ABBV  ", tradingview: " NASDAQ-ABBV " }, security);
    assert.equal(b.provider_symbols?.yfinance, "ABBV");
    assert.equal(b.provider_symbols?.tradingview, "NASDAQ-ABBV");
  });
});

// ─── ETag conflict discrimination ─────────────────────────────────────────────
// Mirror of the save() 409-handling logic — which condition triggers which UX

function classify409(data) {
  if (data.error === "collision") return "collision";
  return "etag_conflict";
}

describe("classify409 — ETag conflict vs identifier collision", () => {
  it("'collision' error string → collision branch", () =>
    assert.equal(classify409({ error: "collision", detail: "ISIN already used" }), "collision"));
  it("'etag_conflict' error → etag_conflict branch", () =>
    assert.equal(classify409({ error: "etag_conflict" }), "etag_conflict"));
  it("missing discriminator defaults to etag_conflict", () =>
    assert.equal(classify409({}), "etag_conflict"));
  it("unrecognised error value defaults to etag_conflict", () =>
    assert.equal(classify409({ error: "unknown" }), "etag_conflict"));
});
