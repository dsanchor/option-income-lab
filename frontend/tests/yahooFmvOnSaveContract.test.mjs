import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path) =>
  readFileSync(new URL(`../src/${path}`, import.meta.url), "utf8");

const types = read("types/portfolio.ts");
const shape = read("lib/caWizardRequestShape.ts");
const form = read("components/CorporateActionForm.tsx");

test("models Yahoo as a server instruction and requires a top-level idempotency UUID", () => {
  assert.match(
    types,
    /share_fmv_instruction\?:\s*(?:ShareFmvInstruction|\{\s*source:\s*"YAHOO_OPEN"\s*\})/,
  );
  const create = types.match(
    /interface CorporateActionCreateRequest[\s\S]*?\n}/,
  )?.[0] ?? "";
  const correct = types.match(
    /interface CorporateActionCorrectRequest[\s\S]*?\n}/,
  )?.[0] ?? "";
  assert.match(create, /client_request_id\?:\s*string/);
  assert.match(correct, /client_request_id\?:\s*string/);
});

test("builds exactly the Yahoo instruction with no client price, currency, FX, or provenance", () => {
  assert.match(
    form,
    /share_fmv_instruction:\s*requestYahooFmv\s*\?\s*\{\s*source:\s*"YAHOO_OPEN"\s*\}\s*:\s*undefined/,
  );
  const instructionLine = form
    .split("\n")
    .find((line) => line.includes("share_fmv_instruction"));
  assert.ok(instructionLine);
  assert.doesNotMatch(
    instructionLine,
    /price|valuation|currency|fx|provenance|amount/,
  );
});

test("offers the accepted Yahoo source and hides or disables every manual price and FX input", () => {
  assert.match(form, /Yahoo Finance\s*[—-]\s*Open on\/after payment date/);
  assert.match(form, /sa_fmv_source\s*===\s*"YAHOO_OPEN"/);
  for (const field of [
    "sa_fmv_amount",
    "sa_fmv_price_per_share",
    "sa_fmv_currency",
    "sa_fmv_fx_rate",
    "sa_fmv_fx_date",
    "sa_fmv_fx_source",
  ]) {
    const occurrences = form
      .split("\n")
      .filter((line) => line.includes(field))
      .join("\n");
    assert.ok(occurrences.length > 0, `Expected ${field} in form`);
  }
  assert.match(
    form,
    /sa_fmv_source\s*!==\s*"YAHOO_OPEN"[\s\S]*(?:sa_fmv_amount|sa_fmv_price_per_share)/,
  );
  assert.match(form, /Se resolverá al guardar/);
});

test("uses one stable client_request_id across retry and sends one request at a time", () => {
  assert.match(form, /crypto\.randomUUID\(\)/);
  assert.match(
    form,
    /(?:useRef|useState)[\s\S]{0,160}(?:clientRequestId|client_request_id)/,
  );
  assert.match(form, /client_request_id:\s*(?:clientRequestId|[^,\n]*\.current)/);
  assert.match(form, /if\s*\(saving\)\s*return|disabled=\{saving\}/);
  assert.doesNotMatch(
    form,
    /handleSubmit[\s\S]{0,400}client_request_id:\s*crypto\.randomUUID\(\)/,
  );
});

test("shows Yahoo loading state, locks the form, and preserves staged errors for retry", () => {
  assert.match(form, /Fetching Yahoo Open and historical FX…/);
  assert.match(form, /disabled=\{saving\}|<fieldset[^>]*disabled=\{saving\}/);
  assert.match(form, /stage/);
  assert.match(form, /retryable/);
  assert.match(form, />\s*Retry\s*</);
  assert.match(form, /setError/);
  assert.doesNotMatch(
    form,
    /catch\s*\([^)]*\)\s*\{[\s\S]{0,250}(?:onClose|onSuccess)\(/,
  );
});

test("does not call Yahoo or calculate FMV in the browser or preview", () => {
  assert.doesNotMatch(form, /fetch\([^)]*(?:yahoo|finance|valuation|preview)/i);
  assert.doesNotMatch(shape, /price_per_share\s*\*\s*quantity|quantity\s*\*\s*price/);
  assert.doesNotMatch(form, /share_fmv[^;\n]*\+|share_fmv[^;\n]*\*/);
});

test("uses returned movements share_fmv as the post-save authority", () => {
  assert.match(
    form,
    /response\s*=\s*await\s+(?:createCorporateAction|correctCorporateActionGroup)/,
  );
  assert.match(form, /(?:result|response)\.movements/);
  assert.match(form, /share_fmv/);
});

test("correction distinguishes inherited Yahoo, clear, manual replacement, and refresh", () => {
  assert.match(form, /Yahoo Open \(inherited\)|YAHOO_OPEN/);
  assert.match(form, /readOnly|disabled/);
  assert.match(form, /Refresh Yahoo|Resolve Yahoo again|refresh/i);
  assert.match(form, /sa_fmv_initially_present\s*\?\s*null/);
  assert.match(form, /share_fmv_instruction/);
  assert.match(
    form,
    /security|quantity|trade_date|payment_date/,
  );
});
