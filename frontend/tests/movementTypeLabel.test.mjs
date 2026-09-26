import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { getMovementTypeLabel } from "../src/lib/movementTypeLabel.ts";

describe("movement type presentation", () => {
  it("ordinary BUY remains Buy only", () => {
    assert.equal(getMovementTypeLabel({ txn_type: "BUY" }), "Buy");
  });

  it("SCRIP_DIVIDEND share acquisition communicates Dividend and Buy", () => {
    assert.equal(getMovementTypeLabel({
      txn_type: "BUY",
      ca_leg_type: "SHARE_ACQUISITION",
      ca_event_type: "SCRIP_DIVIDEND",
    }), "Dividend · Buy");
  });

  it("DIVIDEND_WITH_SCRIP share acquisition communicates Dividend and Buy", () => {
    assert.equal(getMovementTypeLabel({
      txn_type: "BUY",
      ca_leg_type: "SHARE_ACQUISITION",
      ca_event_type: "DIVIDEND_WITH_SCRIP",
    }), "Dividend · Buy");
  });

  it("CASH_DIVIDEND leg remains Dividend only", () => {
    assert.equal(getMovementTypeLabel({
      txn_type: "DIVIDEND",
      ca_leg_type: "CASH_DIVIDEND",
      ca_event_type: "DIVIDEND_WITH_SCRIP",
    }), "Dividend");
  });

  it("missing optional corporate-action metadata preserves the base label", () => {
    assert.equal(getMovementTypeLabel({
      txn_type: "BUY",
      ca_leg_type: undefined,
      ca_event_type: undefined,
    }), "Buy");
  });
});

describe("active movement surfaces share the label helper", () => {
  const surfaces = [
    "PortfolioMovementsTable.tsx",
    "StockTransactionsTable.tsx",
    "MovementDetailDialog.tsx",
    "MovementCorrectionDialog.tsx",
    "EconomicsView.tsx",
    "PositionDetail.tsx",
  ];

  for (const surface of surfaces) {
    it(`${surface} uses getMovementTypeLabel`, () => {
      const source = fs.readFileSync(
        new URL(`../src/components/${surface}`, import.meta.url),
        "utf8",
      );
      assert.match(source, /import \{ getMovementTypeLabel \} from "@\/lib\/movementTypeLabel"/);
      assert.match(source, /getMovementTypeLabel\((?:m|movement)\)/);
    });
  }
});
