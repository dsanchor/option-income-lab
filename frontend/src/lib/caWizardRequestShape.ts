/**
 * caWizardRequestShape.ts — Pure helpers for the corporate-action wizard.
 *
 * Builds and validates POST /api/portfolio/corporate-actions and
 * POST .../correct request bodies from wizard form state.
 *
 * Amendment H §H.3.1–§H.3.6 contract (Livingston final 2026-09-06, extended
 * 2026-09-08 for SHARE_CONSOLIDATION per danny-share-consolidation-contract.md):
 *   - event_type ∈ {CASH_DIVIDEND, DIVIDEND_WITH_SCRIP, SCRIP_DIVIDEND, RIGHTS_ISSUE,
 *     SHARE_CONSOLIDATION}
 *   - leg_type ∈ {CASH_DIVIDEND, RIGHTS_SOLD, SHARE_ACQUISITION, CASH_TOP_UP,
 *     CONSOLIDATION_OUT, CONSOLIDATION_IN, FRACTIONAL_CASH_OUT}
 *   - Required legs per event_type validated before submit
 *   - withholding.*.rate_pct is server-derived — must NOT be sent or trusted
 *   - amount_eur is the primary input; rate_pct will be derived server-side
 */

import type { CaEventType, CaLegType } from "@/types/portfolio";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

export const CA_EVENT_TYPES: readonly CaEventType[] = [
  "CASH_DIVIDEND",
  "DIVIDEND_WITH_SCRIP",
  "SCRIP_DIVIDEND",
  "RIGHTS_ISSUE",
  "SHARE_CONSOLIDATION",
];

export const CA_LEG_TYPES: readonly CaLegType[] = [
  "CASH_DIVIDEND",
  "RIGHTS_SOLD",
  "SHARE_ACQUISITION",
  "CASH_TOP_UP",
  "CONSOLIDATION_OUT",
  "CONSOLIDATION_IN",
  "FRACTIONAL_CASH_OUT",
];

/** Required leg types per event_type. */
export const CA_REQUIRED_LEGS: Record<CaEventType, CaLegType[]> = {
  CASH_DIVIDEND: ["CASH_DIVIDEND"],
  DIVIDEND_WITH_SCRIP: ["CASH_DIVIDEND", "SHARE_ACQUISITION"],
  SCRIP_DIVIDEND: ["SHARE_ACQUISITION"],
  RIGHTS_ISSUE: ["SHARE_ACQUISITION"],
  SHARE_CONSOLIDATION: ["CONSOLIDATION_OUT", "CONSOLIDATION_IN"],
};

// ---------------------------------------------------------------------------
// Validation helpers
// ---------------------------------------------------------------------------

export function isValidCaEventType(v: string): v is CaEventType {
  return (CA_EVENT_TYPES as readonly string[]).includes(v);
}

export function isValidCaLegType(v: string): v is CaLegType {
  return (CA_LEG_TYPES as readonly string[]).includes(v);
}

/**
 * Returns the missing required leg types for a given event_type and
 * the already-provided leg types.
 */
export function missingRequiredLegs(
  eventType: string,
  providedLegTypes: string[],
): string[] {
  const required = (CA_REQUIRED_LEGS as Record<string, string[]>)[eventType] ?? [];
  return required.filter((t) => !providedLegTypes.includes(t));
}

// ---------------------------------------------------------------------------
// Rate stripping (server derives rate_pct; client must not send it)
// ---------------------------------------------------------------------------

type WithholdingDetail = {
  country?: string;
  amount_eur: string;
  rate_pct?: string;
};

type WithholdingInput = {
  source?: WithholdingDetail | null;
  destination?: WithholdingDetail | null;
};

/**
 * Strips rate_pct from a withholding object before sending to the API.
 * The server always derives and overwrites rate_pct from amount_eur/gross_eur.
 * Sending a client-typed rate_pct would either be ignored or cause confusion.
 */
export function stripRatePctFromWithholding(
  wht: WithholdingInput | null | undefined,
): WithholdingInput | null | undefined {
  if (!wht) return wht;
  const result: WithholdingInput = {};
  if (wht.source) {
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const { rate_pct: _drop, ...rest } = wht.source;
    result.source = rest;
  }
  if ("destination" in wht) {
    if (wht.destination === null) {
      result.destination = null;
    } else if (wht.destination) {
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      const { rate_pct: _drop, ...rest } = wht.destination;
      result.destination = rest;
    }
  }
  return result;
}

// ---------------------------------------------------------------------------
// Group correction request validation
// ---------------------------------------------------------------------------

export interface CaGroupCorrectionRequest {
  account_id: string;
  correction_note: string;
  event_type: string;
  legs: Array<{ leg_type: string; [key: string]: unknown }>;
  [key: string]: unknown;
}

/**
 * Client-side pre-submit validation for POST .../correct request.
 * Returns an array of error strings; empty = valid.
 *
 * Does NOT replace server-side validation — these are UX guards only.
 */
export function validateCaGroupCorrectionRequest(
  req: CaGroupCorrectionRequest,
): string[] {
  const errors: string[] = [];

  if (!req.account_id) errors.push("account_id is required");

  if (!req.correction_note?.trim()) {
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
      errors.push(
        `Missing required leg type(s) for ${req.event_type}: ${missing.join(", ")}`,
      );
    }
  }

  return errors;
}
