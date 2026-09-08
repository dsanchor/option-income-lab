/**
 * Tests for caGroupIndicator.ts — Amendment H corporate-action group badge.
 *
 * Run with: node --test frontend/tests/caGroupIndicator.test.mjs
 *
 * Tests isCaGroupMovement() and getCaLegTypeLabel() pure helpers.
 * Corporate-action legs carry ca_group_id, ca_leg_type, ca_event_type, ca_group_seq.
 * Standalone movements have these fields absent or null.
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";

// ---------------------------------------------------------------------------
// Inline predicates (mirror caGroupIndicator.ts — update both together)
// ---------------------------------------------------------------------------

function isCaGroupMovement(movement) {
  return movement.ca_group_id != null && movement.ca_group_id !== "";
}

const CA_LEG_TYPE_LABELS = {
  CASH_DIVIDEND: "Cash Dividend",
  RIGHTS_SOLD: "Rights Sold",
  SHARE_ACQUISITION: "Share Acquisition",
  CASH_TOP_UP: "Cash Top-Up",
  CONSOLIDATION_OUT: "Consolidation Out",
  CONSOLIDATION_IN: "Consolidation In",
  FRACTIONAL_CASH_OUT: "Fractional Cash",
};

function getCaLegTypeLabel(legType) {
  if (!legType) return null;
  return CA_LEG_TYPE_LABELS[legType] ?? null;
}

// ---------------------------------------------------------------------------
// isCaGroupMovement
// ---------------------------------------------------------------------------

describe("isCaGroupMovement", () => {
  it("returns true when ca_group_id is a string", () => {
    assert.equal(isCaGroupMovement({ ca_group_id: "cag_abc123" }), true,
      "Movement with ca_group_id must be flagged as a CA group member");
  });

  it("returns true for any non-empty ca_group_id string", () => {
    assert.equal(isCaGroupMovement({ ca_group_id: "cag_xyz" }), true);
  });

  it("returns false when ca_group_id is null", () => {
    assert.equal(isCaGroupMovement({ ca_group_id: null }), false,
      "null ca_group_id = standalone movement, not a CA group member");
  });

  it("returns false when ca_group_id is undefined (absent field)", () => {
    assert.equal(isCaGroupMovement({ ca_group_id: undefined }), false,
      "Absent ca_group_id means standalone BUY/SELL");
  });

  it("returns false when ca_group_id is empty string", () => {
    assert.equal(isCaGroupMovement({ ca_group_id: "" }), false,
      "Empty string treated same as absent");
  });

  it("returns false when ca_group_id is completely absent from object", () => {
    assert.equal(isCaGroupMovement({}), false,
      "Standard BUY/SELL/DIVIDEND has no ca_group_id field");
  });

  it("BUY movement without ca_group_id → false", () => {
    const movement = { txn_type: "BUY", id: "mvt_001", quantity: "100" };
    assert.equal(isCaGroupMovement(movement), false);
  });

  it("CASH_DIVIDEND leg with ca_group_id → true", () => {
    const movement = {
      txn_type: "DIVIDEND",
      id: "mvt_div_001",
      ca_group_id: "cag_abc",
      ca_leg_type: "CASH_DIVIDEND",
      ca_event_type: "DIVIDEND_WITH_SCRIP",
      ca_group_seq: 1,
    };
    assert.equal(isCaGroupMovement(movement), true);
  });

  it("SHARE_ACQUISITION leg with ca_group_id → true", () => {
    const movement = {
      txn_type: "BUY",
      id: "mvt_acq_001",
      ca_group_id: "cag_abc",
      ca_leg_type: "SHARE_ACQUISITION",
      ca_event_type: "DIVIDEND_WITH_SCRIP",
      ca_group_seq: 2,
    };
    assert.equal(isCaGroupMovement(movement), true);
  });

  it("RIGHTS_SOLD leg with ca_group_id → true", () => {
    const movement = {
      txn_type: "SELL",
      id: "mvt_rights_001",
      ca_group_id: "cag_def",
      ca_leg_type: "RIGHTS_SOLD",
    };
    assert.equal(isCaGroupMovement(movement), true);
  });
});

// ---------------------------------------------------------------------------
// getCaLegTypeLabel
// ---------------------------------------------------------------------------

describe("getCaLegTypeLabel", () => {
  it("CASH_DIVIDEND → 'Cash Dividend'", () => {
    assert.equal(getCaLegTypeLabel("CASH_DIVIDEND"), "Cash Dividend");
  });

  it("RIGHTS_SOLD → 'Rights Sold'", () => {
    assert.equal(getCaLegTypeLabel("RIGHTS_SOLD"), "Rights Sold");
  });

  it("SHARE_ACQUISITION → 'Share Acquisition'", () => {
    assert.equal(getCaLegTypeLabel("SHARE_ACQUISITION"), "Share Acquisition");
  });

  it("CASH_TOP_UP → 'Cash Top-Up'", () => {
    assert.equal(getCaLegTypeLabel("CASH_TOP_UP"), "Cash Top-Up");
  });

  it("FE-SC2: CONSOLIDATION_OUT → 'Consolidation Out'", () => {
    assert.equal(getCaLegTypeLabel("CONSOLIDATION_OUT"), "Consolidation Out");
  });

  it("FE-SC2: CONSOLIDATION_IN → 'Consolidation In'", () => {
    assert.equal(getCaLegTypeLabel("CONSOLIDATION_IN"), "Consolidation In");
  });

  it("FE-SC2: FRACTIONAL_CASH_OUT → 'Fractional Cash'", () => {
    assert.equal(getCaLegTypeLabel("FRACTIONAL_CASH_OUT"), "Fractional Cash");
  });

  it("null → null", () => {
    assert.equal(getCaLegTypeLabel(null), null);
  });

  it("undefined → null", () => {
    assert.equal(getCaLegTypeLabel(undefined), null);
  });

  it("empty string → null", () => {
    assert.equal(getCaLegTypeLabel(""), null);
  });

  it("unknown leg type → null (no fallthrough)", () => {
    assert.equal(getCaLegTypeLabel("UNKNOWN_LEG"), null);
  });
});

// ---------------------------------------------------------------------------
// CA_LEG_TYPE_LABELS exhaustive coverage
// ---------------------------------------------------------------------------

describe("CA_LEG_TYPE_LABELS exhaustive coverage", () => {
  const ALL_LEG_TYPES = [
    "CASH_DIVIDEND",
    "RIGHTS_SOLD",
    "SHARE_ACQUISITION",
    "CASH_TOP_UP",
    "CONSOLIDATION_OUT",
    "CONSOLIDATION_IN",
    "FRACTIONAL_CASH_OUT",
  ];

  it("covers exactly 7 leg types from Amendment H §H.3.2 + SHARE_CONSOLIDATION", () => {
    assert.deepEqual(
      Object.keys(CA_LEG_TYPE_LABELS).sort(),
      ALL_LEG_TYPES.sort(),
    );
  });

  it("every leg type has a non-empty label", () => {
    for (const t of ALL_LEG_TYPES) {
      const label = getCaLegTypeLabel(t);
      assert.ok(label && label.length > 0,
        `getCaLegTypeLabel('${t}') must return non-empty string`);
    }
  });

  it("no label equals the internal enum value verbatim", () => {
    for (const t of ALL_LEG_TYPES) {
      assert.notEqual(getCaLegTypeLabel(t), t,
        `Label for '${t}' must not be the raw internal value`);
    }
  });
});
