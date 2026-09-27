import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const source = (path) => readFileSync(join(here, "../src", path), "utf8");

describe("Dividend · Buy share FMV types and request semantics", () => {
  const types = source("types/portfolio.ts");
  const shape = source("lib/caWizardRequestShape.ts");
  const form = source("components/CorporateActionForm.tsx");

  it("models the complete persisted FMV and manual-only input", () => {
    for (const field of [
      "valuation_date", "amount", "currency", "eur_amount",
      "price_per_share", "price_per_share_eur", "source", "confidence",
      "fx", "provenance",
    ]) assert.match(types, new RegExp(`\\b${field}\\b`));
    assert.match(types, /share_fmv\?:\s*ShareFmvInput\s*\|\s*null/);
    assert.match(types, /"YAHOO_OPEN"/);
  });

  it("builds FMV independently and never derives it from gross, net, or notes", () => {
    const builder = shape.match(
      /export function buildManualShareFmv[\s\S]*?\n}\n/
    )?.[0] ?? "";
    assert.match(builder, /valuation_date/);
    assert.match(builder, /price_per_share/);
    assert.match(builder, /reference/);
    assert.doesNotMatch(builder, /\bgross\b|\bnet\b|\bnotes\b/);
    assert.match(builder, /source\s*===\s*"YAHOO_OPEN"\)\s*return undefined/);
  });

  it("sends explicit null on correction clear but omits disabled create FMV", () => {
    assert.match(form, /sa_fmv_initially_present/);
    assert.match(
      form,
      /share_fmv:\s*!form\.sa_fmv_enabled[\s\S]*sa_fmv_initially_present\s*\?\s*null\s*:\s*undefined[\s\S]*buildManualShareFmv/
    );
  });

  it("labels investor contribution and fees separately from fair value", () => {
    assert.match(form, /Personal contribution/i);
    assert.match(form, /Attributable fees/i);
    assert.match(form, /Add fair value of the shares/i);
    assert.doesNotMatch(form, /Gross\s*\/\s*FMV/i);
    assert.doesNotMatch(form, /Cost basis \(derived from FMV\)/i);
  });

  it("prefills correction FMV only from share_fmv and defaults valuation to payment date", () => {
    assert.match(form, /const fmv = saLeg\.share_fmv/);
    assert.match(form, /sa_fmv_valuation_date[\s\S]*(fmv\?\.valuation_date|payment_date)/);
    assert.doesNotMatch(
      form,
      /sa_fmv_(?:amount|price_per_share)\s*=[^;\n]*(?:saLeg\.gross|saLeg\.net|saLeg\.notes)/
    );
  });
});

describe("Movement detail keeps FIFO and FMV separate without Economics", () => {
  const detail = source("components/MovementDetailDialog.tsx");

  it("renders distinct FIFO and fair-value sections and complete provenance", () => {
    assert.match(detail, /FIFO Cost|Coste FIFO/i);
    assert.match(detail, /Fair Value of (?:the )?Shares|Valor razonable de las acciones/i);
    for (const field of [
      "share_fmv.amount", "share_fmv.eur_amount",
      "share_fmv.price_per_share", "share_fmv.price_per_share_eur",
      "share_fmv.valuation_date", "share_fmv.source",
      "share_fmv.confidence", "share_fmv.fx",
    ]) assert.ok(detail.includes(field), `missing detail field ${field}`);
    assert.match(detail, /market_session_date/);
    assert.match(detail, /provider_symbol/);
  });

  it("does not aggregate FMV into an event total or Economics metric", () => {
    assert.doesNotMatch(
      detail,
      /(?:economic|economics|event).{0,30}(?:total|value).{0,80}share_fmv/i
    );
  });
});
