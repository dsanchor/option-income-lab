/**
 * Regression tests for MovementDetailDialog defensive guards on sparse
 * ledger movements.
 *
 * Run with: node --test frontend/tests/movementDetailDefensiveGuards.test.mjs
 *
 * Strategy:
 *   1. Behavioral mirror of the dialog's amount/WHT access pattern.
 *   2. Source assertions against the real .tsx file so optional chaining
 *      regressions fail even though Node cannot import JSX directly here.
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

function formatEurAmount(amount, currency) {
  if (!amount) return "—";
  const n = Number(amount);
  if (Number.isNaN(n)) return amount;
  const eur = `€${n.toLocaleString("es-ES", { minimumFractionDigits: 2 })}`;
  if (currency && currency !== "EUR") {
    return `${eur} (${currency})`;
  }
  return eur;
}

function readAmountFields(movement) {
  return {
    gross: formatEurAmount(movement.gross?.eur_amount, movement.gross?.currency),
    fees: formatEurAmount(movement.fees?.total_eur, movement.fees?.currency),
    net: formatEurAmount(movement.net?.eur_amount, movement.net?.currency),
    whtSource: movement.withholding?.source
      ? formatEurAmount(movement.withholding.source.amount_eur)
      : null,
    whtDestination: movement.withholding?.destination
      ? formatEurAmount(movement.withholding.destination.amount_eur)
      : null,
  };
}

describe("MovementDetailDialog defensive guards", () => {
  it("returns placeholders and omits WHT blocks when financial fields are missing", () => {
    const movement = {
      id: "m_transfer_manual_1",
      txn_type: "TRANSFER_OUT",
      trade_date: "2026-09-08",
      security_id: "XMAD:IBE",
      ticker: "IBE",
      company_name: "Iberdrola",
      quantity: "10",
      account_id: "acct_1",
      import_source: "manual",
      created_at: "2026-09-08T12:00:00Z",
      fx: { rate: "1", rate_source: "MANUAL" },
    };

    assert.doesNotThrow(() => readAmountFields(movement));
    assert.deepEqual(readAmountFields(movement), {
      gross: "—",
      fees: "—",
      net: "—",
      whtSource: null,
      whtDestination: null,
    });
  });

  it("still formats present values when only some nested blocks exist", () => {
    const movement = {
      gross: { eur_amount: "100", currency: "USD" },
      fees: undefined,
      net: { eur_amount: "90", currency: "EUR" },
      withholding: {
        source: { amount_eur: "15", country: "US" },
      },
    };

    assert.deepEqual(readAmountFields(movement), {
      gross: "€100,00 (USD)",
      fees: "—",
      net: "€90,00",
      whtSource: "€15,00",
      whtDestination: null,
    });
  });

  it("real source uses optional chaining on sparse ledger fields", () => {
    const dialogPath = fileURLToPath(new URL("../src/components/MovementDetailDialog.tsx", import.meta.url));
    const source = readFileSync(dialogPath, "utf8");

    assert.match(source, /m\.gross\?\.eur_amount/);
    assert.match(source, /m\.gross\?\.currency/);
    assert.match(source, /m\.fees\?\.total_eur/);
    assert.match(source, /m\.fees\?\.currency/);
    assert.match(source, /m\.net\?\.eur_amount/);
    assert.match(source, /m\.net\?\.currency/);
    assert.match(source, /m\.withholding\?\.source/);
    assert.match(source, /m\.withholding\?\.destination/);
  });
});
