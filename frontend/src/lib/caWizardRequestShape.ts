/**
 * caWizardRequestShape.ts — Pure helpers for the corporate-action wizard.
 *
 * Builds and validates POST /api/portfolio/corporate-actions and
 * POST .../correct request bodies from wizard form state.
 *
 * Amendment H §H.3.1–§H.3.6 contract (Livingston final 2026-09-06, extended
 * 2026-09-08 for SHARE_CONSOLIDATION per danny-share-consolidation-contract.md):
 *   - event_type ∈ {CASH_DIVIDEND, DIVIDEND_WITH_SCRIP, SCRIP_DIVIDEND,
 *     SHARE_CONSOLIDATION}
 *   - leg_type ∈ {CASH_DIVIDEND, SHARE_ACQUISITION, CASH_TOP_UP,
 *     CONSOLIDATION_OUT, CONSOLIDATION_IN, FRACTIONAL_CASH_OUT}
 *   - Required legs per event_type validated before submit
 *   - withholding.*.rate_pct is server-derived — must NOT be sent or trusted
 *   - amount_eur is the primary input; rate_pct will be derived server-side
 */

import type {
  CaEventType,
  CaLegType,
  CostBasisStatus,
  ManualShareFmvSource,
  ShareFmvSource,
  ShareFmvInput,
  ShareFmvFxSource,
} from "@/types/portfolio";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

export const CA_EVENT_TYPES: readonly CaEventType[] = [
  "CASH_DIVIDEND",
  "DIVIDEND_WITH_SCRIP",
  "SCRIP_DIVIDEND",
  "SHARE_CONSOLIDATION",
];

export const CA_LEG_TYPES: readonly CaLegType[] = [
  "CASH_DIVIDEND",
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
 * Preview the server-derived share-acquisition basis state from the investor's
 * contribution and attributable fees. FMV is intentionally not an input.
 */
export function shareAcquisitionCostBasisStatus(
  grossAmount: string,
  grossEurAmount: string,
  currency: string,
  feesAmount = "",
  feesEurAmount = "",
): CostBasisStatus {
  const contribution = currency.trim().toUpperCase() === "EUR"
    ? grossAmount
    : grossEurAmount;
  const fees = currency.trim().toUpperCase() === "EUR"
    ? feesAmount
    : feesEurAmount;
  if (contribution.trim() === "" || fees.trim() === "") return "INCOMPLETE";

  const contributionValue = Number(contribution);
  const feesValue = Number(fees);
  const nativeContributionValue = Number(grossAmount);
  const nativeFeesValue = Number(feesAmount);
  if (
    !Number.isFinite(contributionValue)
    || contributionValue < 0
    || !Number.isFinite(feesValue)
    || feesValue < 0
    || !Number.isFinite(nativeContributionValue)
    || nativeContributionValue < 0
    || !Number.isFinite(nativeFeesValue)
    || nativeFeesValue < 0
  ) return "INCOMPLETE";
  if (
    currency.trim().toUpperCase() !== "EUR"
    && (
      (nativeContributionValue > 0 && contributionValue === 0)
      || (nativeFeesValue > 0 && feesValue === 0)
    )
  ) return "INCOMPLETE";
  return contributionValue + feesValue === 0 ? "ZERO_COST" : "COMPLETE";
}

export function shareAcquisitionCostValidationError(
  grossAmount: string,
  grossEurAmount: string,
  currency: string,
  feesAmount: string,
  feesEurAmount: string,
): string | null {
  const fields = [
    ["Personal contribution", grossAmount],
    ["Personal contribution (€)", grossEurAmount],
    ["Attributable fees", feesAmount],
    ["Attributable fees (€)", feesEurAmount],
  ] as const;
  for (const [label, raw] of fields) {
    if (raw.trim() === "") continue;
    const value = Number(raw);
    if (!Number.isFinite(value) || value < 0) {
      return `${label} must be a finite non-negative number.`;
    }
  }

  if (
    currency.trim().toUpperCase() === "EUR"
    && grossAmount.trim() !== ""
    && grossEurAmount.trim() !== ""
    && Number(grossAmount) !== Number(grossEurAmount)
  ) {
    return "Personal contribution and its EUR amount must match for EUR.";
  }
  if (
    currency.trim().toUpperCase() === "EUR"
    && feesAmount.trim() !== ""
    && feesEurAmount.trim() !== ""
    && Number(feesAmount) !== Number(feesEurAmount)
  ) {
    return "Attributable fees and their EUR amount must match for EUR.";
  }
  return null;
}

export interface ManualShareFmvFields {
  enabled: boolean;
  valuationDate: string;
  currency: string;
  amount: string;
  pricePerShare: string;
  source: ShareFmvSource;
  reference: string;
  fxRate: string;
  fxDate: string;
  fxSource: Exclude<ShareFmvFxSource, "IDENTITY">;
}

function positiveDecimal(raw: string): boolean {
  const value = Number(raw);
  return raw.trim() !== "" && Number.isFinite(value) && value > 0;
}

export function manualShareFmvValidationError(fields: ManualShareFmvFields): string | null {
  if (!fields.enabled) return null;
  if (fields.source === "YAHOO_OPEN") return null;
  if (!fields.valuationDate) return "Fair-value valuation date is required.";
  if (!/^[A-Z]{3}$/.test(fields.currency.trim().toUpperCase())) {
    return "Fair-value currency must be a three-letter ISO code.";
  }
  if (!positiveDecimal(fields.amount) && !positiveDecimal(fields.pricePerShare)) {
    return "Enter a positive fair-value total or price per share.";
  }
  for (const [label, raw] of [
    ["Fair-value total", fields.amount],
    ["Fair-value price per share", fields.pricePerShare],
  ] as const) {
    if (raw.trim() !== "" && !positiveDecimal(raw)) {
      return `${label} must be a finite positive number.`;
    }
  }
  if (fields.currency.trim().toUpperCase() !== "EUR") {
    if (!positiveDecimal(fields.fxRate)) return "A positive fair-value FX rate is required.";
    if (!fields.fxDate) return "Fair-value FX date is required.";
  }
  return null;
}

export function buildManualShareFmv(fields: ManualShareFmvFields): ShareFmvInput | undefined {
  if (!fields.enabled || fields.source === "YAHOO_OPEN") return undefined;
  const currency = fields.currency.trim().toUpperCase();
  return {
    valuation_date: fields.valuationDate,
    currency,
    source: fields.source as ManualShareFmvSource,
    amount: fields.amount.trim() || undefined,
    price_per_share: fields.pricePerShare.trim() || undefined,
    reference: fields.reference.trim() || undefined,
    fx: currency === "EUR"
      ? undefined
      : {
          rate: fields.fxRate.trim(),
          date: fields.fxDate,
          source: fields.fxSource,
        },
  };
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
