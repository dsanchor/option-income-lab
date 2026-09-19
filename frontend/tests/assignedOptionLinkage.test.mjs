/**
 * Regression contract for assigned option -> stock movement linkage.
 *
 * Run: node --test frontend/tests/assignedOptionLinkage.test.mjs
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const readSource = (relativePath) =>
  readFileSync(fileURLToPath(new URL(`../${relativePath}`, import.meta.url)), "utf8");

const typesSource = readSource("src/types/portfolio.ts");
const apiSource = readSource("src/lib/portfolio-api.ts");
const pickerSource = readSource("src/components/OptionPositionLinkPicker.tsx");
const dialogSource = readSource("src/components/MovementCorrectionDialog.tsx");

function selectedMetadata(txnType, position) {
  const result = { option_position_id: position.position_id };
  if (txnType === "BUY" || txnType === "SELL") {
    result.option_link_kind = "ASSIGNMENT_STOCK";
    result.option_type = position.type;
  }
  return result;
}

describe("assigned option linkage contract", () => {
  it("keeps OptionTxnType narrow and adds BUY/SELL only to the linkable context", () => {
    assert.match(
      typesSource,
      /export type OptionTxnType = "CALL_SELL" \| "CALL_BUY" \| "PUT_SELL" \| "PUT_BUY";/,
    );
    assert.match(
      typesSource,
      /export type LinkablePositionTxnType = OptionTxnType \| "BUY" \| "SELL";/,
    );
    assert.match(apiSource, /txnType: LinkablePositionTxnType/);
    assert.match(pickerSource, /txnType: LinkablePositionTxnType/);
    assert.doesNotMatch(dialogSource, /txnType=\{m\.txn_type as OptionTxnType\}/);
  });

  it("synchronizes assigned put metadata when a BUY candidate is selected", () => {
    assert.deepEqual(
      selectedMetadata("BUY", { position_id: "put-1", type: "put" }),
      {
        option_position_id: "put-1",
        option_link_kind: "ASSIGNMENT_STOCK",
        option_type: "put",
      },
    );
    assert.match(dialogSource, /setOptionLinkKind\("ASSIGNMENT_STOCK"\)/);
    assert.match(dialogSource, /setOptionType\(position\.type\)/);
  });

  it("synchronizes assigned call metadata when a SELL candidate is selected", () => {
    assert.deepEqual(
      selectedMetadata("SELL", { position_id: "call-1", type: "call" }),
      {
        option_position_id: "call-1",
        option_link_kind: "ASSIGNMENT_STOCK",
        option_type: "call",
      },
    );
  });

  it("preserves option movement metadata behavior and manual ID entry", () => {
    assert.deepEqual(
      selectedMetadata("PUT_BUY", { position_id: "put-2", type: "put" }),
      { option_position_id: "put-2" },
    );
    assert.match(pickerSource, /onChange\(e\.target\.value\)/);
    assert.match(dialogSource, /OPTION_LINK_KIND_BY_TXN_TYPE\[optionTxnType\]/);
    assert.match(dialogSource, /OPTION_TYPE_BY_TXN_TYPE\[optionTxnType\]/);
  });
});
