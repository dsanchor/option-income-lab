/**
 * Tests for caWizardRequestShape.ts — Amendment H corporate-action wizard.
 *
 * Run with: node --test frontend/tests/caWizardRequestShape.test.mjs
 *
 * Tests pure helpers that build the POST /api/portfolio/corporate-actions
 * and POST .../correct request bodies from wizard form state.
 *
 * Contract (Livingston final — 2026-09-06; extended 2026-09-08 for
 * SHARE_CONSOLIDATION per danny-share-consolidation-contract.md):
 *   POST /api/portfolio/corporate-actions          → 201
 *   POST /api/portfolio/corporate-actions/{id}/correct → 201
 *
 * Key rules:
 *   - event_type ∈ {CASH_DIVIDEND, DIVIDEND_WITH_SCRIP, SCRIP_DIVIDEND, RIGHTS_ISSUE,
 *     SHARE_CONSOLIDATION}
 *   - leg_type ∈ {CASH_DIVIDEND, RIGHTS_SOLD, SHARE_ACQUISITION, CASH_TOP_UP,
 *     CONSOLIDATION_OUT, CONSOLIDATION_IN, FRACTIONAL_CASH_OUT}
 *   - Required legs per event_type enforced before submit
 *   - withholding.source.amount_eur and .destination.amount_eur are primary inputs;
 *     rate_pct is server-derived and must NOT be sent (or ignored if sent)
 *   - gross, fees, withholding amount_eur: string numerics
 *   - correction_note: required non-empty for /correct
 *   - account_id: required
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";

// ---------------------------------------------------------------------------
// Inline helpers (mirrors caWizardRequestShape.ts — update both together)
// ---------------------------------------------------------------------------

const CA_EVENT_TYPES = [
  "CASH_DIVIDEND",
  "DIVIDEND_WITH_SCRIP",
  "SCRIP_DIVIDEND",
  "RIGHTS_ISSUE",
  "SHARE_CONSOLIDATION",
];

const CA_LEG_TYPES = [
  "CASH_DIVIDEND",
  "RIGHTS_SOLD",
  "SHARE_ACQUISITION",
  "CASH_TOP_UP",
  "CONSOLIDATION_OUT",
  "CONSOLIDATION_IN",
  "FRACTIONAL_CASH_OUT",
];

/** Required leg types per event_type. */
const CA_REQUIRED_LEGS = {
  CASH_DIVIDEND: ["CASH_DIVIDEND"],
  DIVIDEND_WITH_SCRIP: ["CASH_DIVIDEND", "SHARE_ACQUISITION"],
  SCRIP_DIVIDEND: ["SHARE_ACQUISITION"],
  RIGHTS_ISSUE: ["SHARE_ACQUISITION"],
  SHARE_CONSOLIDATION: ["CONSOLIDATION_OUT", "CONSOLIDATION_IN"],
};

/** Validate an event_type value. */
function isValidCaEventType(v) {
  return CA_EVENT_TYPES.includes(v);
}

/** Validate a leg_type value. */
function isValidCaLegType(v) {
  return CA_LEG_TYPES.includes(v);
}

/** Returns missing required leg types for the given event_type. */
function missingRequiredLegs(eventType, providedLegTypes) {
  const required = CA_REQUIRED_LEGS[eventType] || [];
  return required.filter((t) => !providedLegTypes.includes(t));
}

/**
 * Strip rate_pct from a withholding object before sending to the API.
 * The server derives rate_pct; client must not send user-typed values.
 */
function stripRatePctFromWithholding(wht) {
  if (!wht) return wht;
  const result = {};
  if (wht.source) {
    const { rate_pct: _drop, ...rest } = wht.source;
    result.source = rest;
  }
  if (wht.destination !== undefined) {
    if (wht.destination === null) {
      result.destination = null;
    } else {
      const { rate_pct: _drop, ...rest } = wht.destination;
      result.destination = rest;
    }
  }
  return result;
}

/**
 * Validate a group correction request shape before sending.
 * Returns array of error strings (empty = valid).
 */
function validateCaGroupCorrectionRequest(req) {
  const errors = [];
  if (!req.account_id) errors.push("account_id is required");
  if (!req.correction_note || !req.correction_note.trim()) {
    errors.push("correction_note is required and must be non-empty");
  }
  if (!isValidCaEventType(req.event_type)) {
    errors.push(`event_type '${req.event_type}' is not a valid CA event type`);
  }
  if (!Array.isArray(req.legs) || req.legs.length === 0) {
    errors.push("legs must be a non-empty array");
  } else {
    const legTypes = req.legs.map((l) => l.leg_type);
    const badTypes = legTypes.filter((t) => !isValidCaLegType(t));
    if (badTypes.length > 0) {
      errors.push(`Unknown leg_type(s): ${badTypes.join(", ")}`);
    }
    const missing = missingRequiredLegs(req.event_type, legTypes);
    if (missing.length > 0) {
      errors.push(`Missing required leg type(s) for ${req.event_type}: ${missing.join(", ")}`);
    }
  }
  return errors;
}

// ---------------------------------------------------------------------------
// Event type and leg type enum validation
// ---------------------------------------------------------------------------

describe("CA event type validation", () => {
  for (const t of CA_EVENT_TYPES) {
    it(`accepts valid event_type: ${t}`, () => {
      assert.equal(isValidCaEventType(t), true);
    });
  }

  it("rejects unknown event_type", () => {
    assert.equal(isValidCaEventType("MAGIC_DIVIDEND"), false);
  });

  it("rejects empty string event_type", () => {
    assert.equal(isValidCaEventType(""), false);
  });
});

describe("CA leg type validation", () => {
  for (const t of CA_LEG_TYPES) {
    it(`accepts valid leg_type: ${t}`, () => {
      assert.equal(isValidCaLegType(t), true);
    });
  }

  it("rejects unknown leg_type", () => {
    assert.equal(isValidCaLegType("MYSTERY_LEG"), false);
  });
});

// ---------------------------------------------------------------------------
// Required legs per event_type
// ---------------------------------------------------------------------------

describe("missingRequiredLegs", () => {
  it("CASH_DIVIDEND needs CASH_DIVIDEND leg", () => {
    assert.deepEqual(missingRequiredLegs("CASH_DIVIDEND", []), ["CASH_DIVIDEND"]);
  });

  it("CASH_DIVIDEND satisfied by CASH_DIVIDEND leg", () => {
    assert.deepEqual(missingRequiredLegs("CASH_DIVIDEND", ["CASH_DIVIDEND"]), []);
  });

  it("DIVIDEND_WITH_SCRIP needs both CASH_DIVIDEND and SHARE_ACQUISITION", () => {
    const missing = missingRequiredLegs("DIVIDEND_WITH_SCRIP", ["CASH_DIVIDEND"]);
    assert.deepEqual(missing, ["SHARE_ACQUISITION"]);
  });

  it("DIVIDEND_WITH_SCRIP satisfied by both legs", () => {
    const missing = missingRequiredLegs("DIVIDEND_WITH_SCRIP",
      ["CASH_DIVIDEND", "SHARE_ACQUISITION"]);
    assert.deepEqual(missing, []);
  });

  it("SCRIP_DIVIDEND needs SHARE_ACQUISITION only", () => {
    assert.deepEqual(missingRequiredLegs("SCRIP_DIVIDEND", []), ["SHARE_ACQUISITION"]);
  });

  it("RIGHTS_ISSUE needs SHARE_ACQUISITION only", () => {
    assert.deepEqual(missingRequiredLegs("RIGHTS_ISSUE", []), ["SHARE_ACQUISITION"]);
  });

  it("unknown event_type returns empty (no required legs)", () => {
    assert.deepEqual(missingRequiredLegs("MYSTERY", ["CASH_DIVIDEND"]), []);
  });
});

// ---------------------------------------------------------------------------
// FE-SC1: SHARE_CONSOLIDATION (danny-share-consolidation-contract.md §5.3)
// ---------------------------------------------------------------------------

describe("FE-SC1: SHARE_CONSOLIDATION wizard shape", () => {
  it("isValidCaEventType accepts SHARE_CONSOLIDATION", () => {
    assert.equal(isValidCaEventType("SHARE_CONSOLIDATION"), true);
  });

  it("isValidCaLegType accepts CONSOLIDATION_OUT", () => {
    assert.equal(isValidCaLegType("CONSOLIDATION_OUT"), true);
  });

  it("isValidCaLegType accepts CONSOLIDATION_IN", () => {
    assert.equal(isValidCaLegType("CONSOLIDATION_IN"), true);
  });

  it("isValidCaLegType accepts FRACTIONAL_CASH_OUT", () => {
    assert.equal(isValidCaLegType("FRACTIONAL_CASH_OUT"), true);
  });

  it("missingRequiredLegs('SHARE_CONSOLIDATION', []) returns both required legs", () => {
    assert.deepEqual(
      missingRequiredLegs("SHARE_CONSOLIDATION", []),
      ["CONSOLIDATION_OUT", "CONSOLIDATION_IN"],
    );
  });

  it("missingRequiredLegs satisfied by CONSOLIDATION_OUT + CONSOLIDATION_IN", () => {
    assert.deepEqual(
      missingRequiredLegs("SHARE_CONSOLIDATION", ["CONSOLIDATION_OUT", "CONSOLIDATION_IN"]),
      [],
    );
  });

  it("FRACTIONAL_CASH_OUT is NOT a required leg for SHARE_CONSOLIDATION", () => {
    assert.equal(CA_REQUIRED_LEGS.SHARE_CONSOLIDATION.includes("FRACTIONAL_CASH_OUT"), false);
  });
});

// ---------------------------------------------------------------------------
// stripRatePctFromWithholding — rate_pct must NOT be sent to server
// ---------------------------------------------------------------------------

describe("stripRatePctFromWithholding", () => {
  it("removes rate_pct from source", () => {
    const input = {
      source: { country: "GB", amount_eur: "0", rate_pct: "15" },
    };
    const out = stripRatePctFromWithholding(input);
    assert.ok(!("rate_pct" in out.source), "rate_pct must be stripped from source");
    assert.equal(out.source.country, "GB");
    assert.equal(out.source.amount_eur, "0");
  });

  it("removes rate_pct from destination", () => {
    const input = {
      destination: { country: "ES", amount_eur: "39.86", rate_pct: "19" },
    };
    const out = stripRatePctFromWithholding(input);
    assert.ok(!("rate_pct" in out.destination), "rate_pct must be stripped from destination");
    assert.equal(out.destination.amount_eur, "39.86");
  });

  it("preserves null destination (3-state: not-captured)", () => {
    const input = { source: { country: "GB", amount_eur: "0" }, destination: null };
    const out = stripRatePctFromWithholding(input);
    assert.equal(out.destination, null, "null destination must be preserved");
  });

  it("returns null for null input", () => {
    assert.equal(stripRatePctFromWithholding(null), null);
  });

  it("preserves amount_eur when stripping rate_pct", () => {
    const input = {
      source: { country: "GB", amount_eur: "15.00", rate_pct: "7.5" },
      destination: { country: "ES", amount_eur: "28.50", rate_pct: "19" },
    };
    const out = stripRatePctFromWithholding(input);
    assert.equal(out.source.amount_eur, "15.00");
    assert.equal(out.destination.amount_eur, "28.50");
  });
});

// ---------------------------------------------------------------------------
// validateCaGroupCorrectionRequest — wizard pre-submit validation
// ---------------------------------------------------------------------------

describe("validateCaGroupCorrectionRequest", () => {
  const VALID = {
    account_id: "heytrade_main",
    correction_note: "Fix gross amount",
    event_type: "DIVIDEND_WITH_SCRIP",
    legs: [
      {
        leg_type: "CASH_DIVIDEND",
        trade_date: "2024-03-28",
        gross: { amount: "200.00", currency: "GBP", eur_amount: "233.10" },
      },
      {
        leg_type: "SHARE_ACQUISITION",
        trade_date: "2024-03-28",
        quantity: "10",
        gross: { amount: "0", currency: "GBP", eur_amount: "0" },
        cost_basis_status: "INCOMPLETE",
      },
    ],
  };

  it("valid request returns no errors", () => {
    assert.deepEqual(validateCaGroupCorrectionRequest(VALID), []);
  });

  it("missing account_id returns error", () => {
    const req = { ...VALID, account_id: "" };
    const errs = validateCaGroupCorrectionRequest(req);
    assert.ok(errs.some((e) => e.includes("account_id")));
  });

  it("missing correction_note returns error", () => {
    const req = { ...VALID, correction_note: "" };
    const errs = validateCaGroupCorrectionRequest(req);
    assert.ok(errs.some((e) => e.includes("correction_note")));
  });

  it("whitespace-only correction_note returns error", () => {
    const req = { ...VALID, correction_note: "   " };
    const errs = validateCaGroupCorrectionRequest(req);
    assert.ok(errs.some((e) => e.includes("correction_note")));
  });

  it("invalid event_type returns error", () => {
    const req = { ...VALID, event_type: "SUPER_DIVIDEND" };
    const errs = validateCaGroupCorrectionRequest(req);
    assert.ok(errs.some((e) => e.includes("event_type")));
  });

  it("empty legs array returns error", () => {
    const req = { ...VALID, legs: [] };
    const errs = validateCaGroupCorrectionRequest(req);
    assert.ok(errs.some((e) => e.includes("legs")));
  });

  it("missing required leg returns error citing missing type", () => {
    const req = {
      ...VALID,
      legs: [VALID.legs[0]], // only CASH_DIVIDEND, missing SHARE_ACQUISITION
    };
    const errs = validateCaGroupCorrectionRequest(req);
    assert.ok(errs.some((e) => e.includes("SHARE_ACQUISITION")));
  });

  it("unknown leg_type returns error", () => {
    const req = {
      ...VALID,
      legs: [
        { ...VALID.legs[0], leg_type: "MYSTERY_LEG" },
        VALID.legs[1],
      ],
    };
    const errs = validateCaGroupCorrectionRequest(req);
    assert.ok(errs.some((e) => e.includes("MYSTERY_LEG")));
  });

  it("FE-SC3: SHARE_CONSOLIDATION with correct legs returns no errors", () => {
    const req = {
      account_id: "heytrade_main",
      correction_note: "Fix consolidation quantities",
      event_type: "SHARE_CONSOLIDATION",
      legs: [
        {
          leg_type: "CONSOLIDATION_OUT",
          trade_date: "2024-05-01",
          quantity: "100",
          gross: { amount: "0", currency: "EUR", eur_amount: "0" },
        },
        {
          leg_type: "CONSOLIDATION_IN",
          trade_date: "2024-05-01",
          quantity: "80",
          gross: { amount: "0", currency: "EUR", eur_amount: "0" },
          transfer_cost_basis_eur: "5000.00",
        },
      ],
    };
    assert.deepEqual(validateCaGroupCorrectionRequest(req), []);
  });

  it("FE-SC3: SHARE_CONSOLIDATION missing CONSOLIDATION_IN returns error", () => {
    const req = {
      account_id: "heytrade_main",
      correction_note: "Bad correction",
      event_type: "SHARE_CONSOLIDATION",
      legs: [
        {
          leg_type: "CONSOLIDATION_OUT",
          trade_date: "2024-05-01",
          quantity: "100",
          gross: { amount: "0", currency: "EUR", eur_amount: "0" },
        },
      ],
    };
    const errs = validateCaGroupCorrectionRequest(req);
    assert.ok(errs.some((e) => e.includes("CONSOLIDATION_IN")));
  });
});

// ---------------------------------------------------------------------------
// Inline mirrors of CorporateActionForm.tsx pure helpers
// These are tested independently; update both here and the component together.
// ---------------------------------------------------------------------------

function makeGross(amount, currency, eurAmount) {
  const eur = eurAmount || (currency === "EUR" ? amount : "0");
  return { amount: amount || "0", currency, eur_amount: eur || "0" };
}

function makeFees(total, currency) {
  if (!total || parseFloat(total) === 0) return null;
  const eur = currency === "EUR" ? total : "0";
  return { total, currency, total_eur: eur };
}

function derivedRate(amount, grossEur) {
  const a = parseFloat(amount) || 0;
  const g = parseFloat(grossEur) || 0;
  if (a > 0 && g > 0) return ((a / g) * 100).toFixed(4);
  return undefined;
}

function makeFx(fxRate, currency) {
  if (!fxRate || currency === "EUR") return undefined;
  return { rate: fxRate, rate_source: "ECB" };
}

// WhtDestState: "not_captured" | "zero" | "value"
function buildWithholding(srcCountry, srcAmount, grossEur, destState, destCountry, destAmount) {
  const hasSrc = !!(srcCountry || parseFloat(srcAmount) > 0);
  if (!hasSrc && destState === "not_captured") return undefined;

  const src = hasSrc
    ? {
        country: srcCountry || undefined,
        rate_pct: derivedRate(srcAmount, grossEur),
        amount_eur: srcAmount || "0",
      }
    : null;

  let dest;
  if (destState === "not_captured") {
    dest = null;
  } else if (destState === "zero") {
    dest = { country: destCountry || "ES", rate_pct: "0", amount_eur: "0" };
  } else {
    dest = {
      country: destCountry || undefined,
      rate_pct: derivedRate(destAmount, grossEur),
      amount_eur: destAmount || "0",
    };
  }
  return { source: src, destination: dest };
}

// Pre-fill helper (mirrors CorporateActionForm.buildCaInitialState)
function buildCaInitialState(legs, representative) {
  const cdLeg = legs.find((l) => l.ca_leg_type === "CASH_DIVIDEND");
  const saLeg = legs.find((l) => l.ca_leg_type === "SHARE_ACQUISITION");
  const rsLeg = legs.find((l) => l.ca_leg_type === "RIGHTS_SOLD");
  const ctuLeg = legs.find((l) => l.ca_leg_type === "CASH_TOP_UP");
  const baseLeg = cdLeg ?? saLeg ?? representative;
  const currency = baseLeg.gross?.currency ?? "EUR";
  const fxRate = baseLeg.fx?.rate ?? "";

  const state = {
    security_id: representative.security_id ?? "",
    account_id: representative.account_id ?? "_unassigned",
    event_type: representative.ca_event_type ?? "CASH_DIVIDEND",
    payment_date: representative.trade_date ?? "",
    currency,
    fx_rate: fxRate,
    notes: "",
  };

  if (cdLeg) {
    state.cd_gross = cdLeg.gross?.amount ?? "";
    state.cd_gross_eur = cdLeg.gross?.eur_amount ?? "";
    state.cd_fees = cdLeg.fees?.total ?? "";
    state.cd_wht_src_country = cdLeg.withholding?.source?.country ?? "";
    state.cd_wht_src_amount = cdLeg.withholding?.source?.amount_eur ?? "";
    const dest = cdLeg.withholding?.destination;
    if (!dest) {
      state.cd_wht_dest_state = "not_captured";
    } else if (dest.amount_eur === "0") {
      state.cd_wht_dest_state = "zero";
      state.cd_wht_dest_country = dest.country ?? "ES";
    } else {
      state.cd_wht_dest_state = "value";
      state.cd_wht_dest_country = dest.country ?? "";
      state.cd_wht_dest_amount = dest.amount_eur ?? "";
    }
  }

  if (saLeg) {
    state.sa_quantity = saLeg.quantity ?? "";
    state.sa_gross = saLeg.gross?.amount ?? "";
    state.sa_gross_eur = saLeg.gross?.eur_amount ?? "";
    state.sa_cost_basis = saLeg.cost_basis_status ?? "INCOMPLETE";
    state.sa_notes = "";
  }

  if (rsLeg) {
    state.rs_enabled = true;
    state.rs_quantity = rsLeg.quantity ?? "";
    state.rs_gross = rsLeg.gross?.amount ?? "";
    state.rs_gross_eur = rsLeg.gross?.eur_amount ?? "";
    state.rs_fees = rsLeg.fees?.total ?? "";
  }

  if (ctuLeg) {
    state.ctu_enabled = true;
    state.ctu_gross = ctuLeg.gross?.amount ?? "";
    state.ctu_gross_eur = ctuLeg.gross?.eur_amount ?? "";
  }

  return state;
}

// ---------------------------------------------------------------------------
// makeFees — omit when zero; required sub-fields when non-zero
// ---------------------------------------------------------------------------

describe("makeFees", () => {
  it("returns null for '0'", () => {
    assert.equal(makeFees("0", "EUR"), null);
  });

  it("returns null for empty string", () => {
    assert.equal(makeFees("", "EUR"), null);
  });

  it("returns null for undefined", () => {
    assert.equal(makeFees(undefined, "EUR"), null);
  });

  it("returns object for non-zero EUR fee", () => {
    const f = makeFees("7.50", "EUR");
    assert.ok(f !== null, "non-zero fee must not be null");
    assert.equal(f.total, "7.50");
    assert.equal(f.currency, "EUR");
    assert.equal(f.total_eur, "7.50");
  });

  it("returns object for non-EUR fee with total_eur='0'", () => {
    const f = makeFees("5.00", "GBP");
    assert.equal(f.currency, "GBP");
    assert.equal(f.total_eur, "0");  // EUR not computed client-side
  });
});

// ---------------------------------------------------------------------------
// makeFx — omit for EUR; include ECB source for non-EUR
// ---------------------------------------------------------------------------

describe("makeFx", () => {
  it("returns undefined for EUR currency (no FX needed)", () => {
    assert.equal(makeFx("1.165", "EUR"), undefined);
  });

  it("returns undefined when fxRate is empty", () => {
    assert.equal(makeFx("", "GBP"), undefined);
  });

  it("returns object with rate_source ECB for non-EUR", () => {
    const fx = makeFx("1.165500000", "GBP");
    assert.ok(fx !== null && fx !== undefined, "fx must not be falsy");
    assert.equal(fx.rate, "1.165500000");
    assert.equal(fx.rate_source, "ECB");
  });

  it("rate_source is always ECB (wizard uses ECB fetch)", () => {
    const fx = makeFx("1.09000", "USD");
    assert.equal(fx?.rate_source, "ECB");
  });
});

// ---------------------------------------------------------------------------
// buildWithholding — 3-state destination contract (not_captured / zero / value)
// ---------------------------------------------------------------------------

describe("buildWithholding — destination states", () => {
  const GROSS_EUR = "209.79";
  const SRC = { country: "GB", amount: "0", grossEur: GROSS_EUR };
  const DST_COUNTRY = "ES";

  it("no source, not_captured → withholding omitted (undefined)", () => {
    const wht = buildWithholding("", "0", GROSS_EUR, "not_captured", DST_COUNTRY, "");
    assert.equal(wht, undefined, "withholding must be omitted when no source and not_captured");
  });

  it("has source, not_captured → destination: null", () => {
    const wht = buildWithholding("GB", "0", GROSS_EUR, "not_captured", DST_COUNTRY, "");
    assert.ok(wht, "withholding must be present when source country provided");
    assert.equal(wht.destination, null, "not_captured destination must be null");
  });

  it("has source with amount, not_captured → source present, destination: null", () => {
    const wht = buildWithholding("GB", "31.47", GROSS_EUR, "not_captured", DST_COUNTRY, "");
    assert.ok(wht.source?.amount_eur === "31.47");
    assert.equal(wht.destination, null);
  });

  it("zero state → destination: {amount_eur: '0', rate_pct: '0', country: 'ES'}", () => {
    const wht = buildWithholding("GB", "0", GROSS_EUR, "zero", DST_COUNTRY, "");
    assert.ok(wht.destination, "destination must be present for zero state");
    assert.equal(wht.destination.amount_eur, "0");
    assert.equal(wht.destination.rate_pct, "0");
    assert.equal(wht.destination.country, "ES");
  });

  it("value state → destination carries amount_eur", () => {
    const wht = buildWithholding("GB", "0", GROSS_EUR, "value", DST_COUNTRY, "39.86");
    assert.ok(wht.destination, "destination must be present for value state");
    assert.equal(wht.destination.amount_eur, "39.86");
  });

  it("value state → destination rate_pct is client-computed (server will overwrite)", () => {
    const wht = buildWithholding("GB", "0", "233.10", "value", DST_COUNTRY, "44.29");
    const expectedRate = ((44.29 / 233.10) * 100).toFixed(4);
    assert.equal(wht.destination?.rate_pct, expectedRate,
      "client pre-computes rate_pct; server overwrites with authoritative value");
  });

  it("source amount_eur is always present in request", () => {
    const wht = buildWithholding("US", "45.00", "300.00", "not_captured", "", "");
    assert.equal(wht?.source?.amount_eur, "45.00");
  });

  it("no source, zero state → source: null, destination present", () => {
    const wht = buildWithholding("", "0", GROSS_EUR, "zero", DST_COUNTRY, "");
    assert.ok(wht, "withholding object returned when destState !== not_captured");
    assert.equal(wht.source, null);
    assert.equal(wht.destination?.amount_eur, "0");
  });
});

// ---------------------------------------------------------------------------
// Correction request shape — matches CorporateActionCorrectRequest
// ---------------------------------------------------------------------------

describe("CorporateActionCorrectRequest shape", () => {
  function buildCorrectionRequest(form, correctionNote, caGroupId) {
    return {
      account_id: form.account_id || "_unassigned",
      correction_note: correctionNote.trim(),
      event_type: form.event_type,
      security_id: form.security_id || undefined,
      payment_date: form.payment_date || undefined,
      notes: form.notes || undefined,
      legs: form.legs,
    };
  }

  it("required fields are present", () => {
    const form = {
      account_id: "heytrade_main",
      event_type: "CASH_DIVIDEND",
      security_id: "XLON:ULVR",
      payment_date: "2024-03-28",
      notes: "",
      legs: [{ leg_type: "CASH_DIVIDEND", trade_date: "2024-03-28",
                gross: { amount: "210.00", currency: "EUR", eur_amount: "210.00" } }],
    };
    const req = buildCorrectionRequest(form, "Fix gross amount", "cag_abc123");
    assert.equal(req.account_id, "heytrade_main");
    assert.equal(req.correction_note, "Fix gross amount");
    assert.equal(req.event_type, "CASH_DIVIDEND");
    assert.ok(Array.isArray(req.legs) && req.legs.length === 1);
  });

  it("empty security_id sent as undefined (optional field inferred from original)", () => {
    const form = {
      account_id: "heytrade_main",
      event_type: "CASH_DIVIDEND",
      security_id: "",
      payment_date: "",
      notes: "",
      legs: [{ leg_type: "CASH_DIVIDEND", trade_date: "2024-03-28",
                gross: { amount: "210.00", currency: "EUR", eur_amount: "210.00" } }],
    };
    const req = buildCorrectionRequest(form, "Fix gross", "cag_abc");
    assert.equal(req.security_id, undefined,
      "empty security_id must be sent as undefined (server infers from original)");
    assert.equal(req.payment_date, undefined);
  });

  it("empty notes sent as undefined (omitted from request)", () => {
    const form = {
      account_id: "x", event_type: "CASH_DIVIDEND", security_id: "", payment_date: "", notes: "",
      legs: [{ leg_type: "CASH_DIVIDEND", trade_date: "2024-03-28",
                gross: { amount: "1", currency: "EUR", eur_amount: "1" } }],
    };
    const req = buildCorrectionRequest(form, "fix", "cag_x");
    assert.equal(req.notes, undefined);
  });

  it("correction_note is trimmed before sending", () => {
    const form = {
      account_id: "x", event_type: "CASH_DIVIDEND", security_id: "S:X", payment_date: "2024-01-01",
      notes: "", legs: [{ leg_type: "CASH_DIVIDEND", trade_date: "2024-01-01",
                          gross: { amount: "100", currency: "EUR", eur_amount: "100" } }],
    };
    const req = buildCorrectionRequest(form, "  Fix error  ", "cag_y");
    assert.equal(req.correction_note, "Fix error");
  });
});

// ---------------------------------------------------------------------------
// buildCaInitialState — pre-fills correction form from existing legs
// ---------------------------------------------------------------------------

describe("buildCaInitialState — WHT destination pre-fill", () => {
  const BASE_LEG = {
    security_id: "XLON:ULVR",
    account_id: "heytrade_main",
    ca_event_type: "DIVIDEND_WITH_SCRIP",
    trade_date: "2024-03-28",
    gross: { amount: "180.00", currency: "GBP", eur_amount: "209.79" },
    fx: { rate: "1.165500000" },
  };

  it("null destination → cd_wht_dest_state = 'not_captured'", () => {
    const cdLeg = {
      ...BASE_LEG,
      ca_leg_type: "CASH_DIVIDEND",
      withholding: { source: { country: "GB", amount_eur: "0", rate_pct: "0" }, destination: null },
    };
    const state = buildCaInitialState([cdLeg], cdLeg);
    assert.equal(state.cd_wht_dest_state, "not_captured");
  });

  it("destination.amount_eur = '0' → cd_wht_dest_state = 'zero'", () => {
    const cdLeg = {
      ...BASE_LEG,
      ca_leg_type: "CASH_DIVIDEND",
      withholding: {
        source: { country: "GB", amount_eur: "0", rate_pct: "0" },
        destination: { country: "ES", amount_eur: "0", rate_pct: "0" },
      },
    };
    const state = buildCaInitialState([cdLeg], cdLeg);
    assert.equal(state.cd_wht_dest_state, "zero");
    assert.equal(state.cd_wht_dest_country, "ES");
  });

  it("destination with amount > '0' → cd_wht_dest_state = 'value'", () => {
    const cdLeg = {
      ...BASE_LEG,
      ca_leg_type: "CASH_DIVIDEND",
      withholding: {
        source: { country: "GB", amount_eur: "0", rate_pct: "0" },
        destination: { country: "ES", amount_eur: "39.86", rate_pct: "19.02" },
      },
    };
    const state = buildCaInitialState([cdLeg], cdLeg);
    assert.equal(state.cd_wht_dest_state, "value");
    assert.equal(state.cd_wht_dest_amount, "39.86");
    assert.equal(state.cd_wht_dest_country, "ES");
  });

  it("SHARE_ACQUISITION leg pre-fills quantity and cost_basis", () => {
    const saLeg = {
      ...BASE_LEG,
      ca_leg_type: "SHARE_ACQUISITION",
      quantity: "9",
      cost_basis_status: "INCOMPLETE",
      gross: { amount: "0", currency: "GBP", eur_amount: "0" },
    };
    const state = buildCaInitialState([saLeg], saLeg);
    assert.equal(state.sa_quantity, "9");
    assert.equal(state.sa_cost_basis, "INCOMPLETE");
  });

  it("RIGHTS_SOLD leg sets rs_enabled = true", () => {
    const rsLeg = {
      ...BASE_LEG,
      ca_leg_type: "RIGHTS_SOLD",
      quantity: "50",
      gross: { amount: "25.00", currency: "GBP", eur_amount: "29.14" },
      fees: { total: "0", currency: "GBP", total_eur: "0" },
    };
    const state = buildCaInitialState([rsLeg], rsLeg);
    assert.equal(state.rs_enabled, true);
    assert.equal(state.rs_quantity, "50");
  });

  it("CASH_TOP_UP leg sets ctu_enabled = true", () => {
    const ctuLeg = {
      ...BASE_LEG,
      ca_leg_type: "CASH_TOP_UP",
      gross: { amount: "5.00", currency: "EUR", eur_amount: "5.00" },
    };
    const state = buildCaInitialState([ctuLeg], ctuLeg);
    assert.equal(state.ctu_enabled, true);
    assert.equal(state.ctu_gross, "5.00");
  });
});
