export const OPTIONAL_MONITOR_IDENTITY_FIELDS = [
  "option_type",
  "strike",
  "expiration",
  "account_id",
  "brokerage_account_id",
  "account",
  "contract_id",
  "option_contract_id",
  "contract_symbol",
  "occ_symbol",
  "osi_symbol",
  "instrument_id",
  "instrument_identifier",
  "security_id",
  "is_paper",
] as const;

type OptionalMonitorIdentityField =
  (typeof OPTIONAL_MONITOR_IDENTITY_FIELDS)[number];

type OptionalMonitorIdentity = Partial<
  Record<OptionalMonitorIdentityField, unknown>
>;

export type MonitorPositionIdentity = {
  position_id: string;
  option_type?: string | null;
  strike?: number | string | null;
  expiration?: string | null;
  account_id?: string | null;
  brokerage_account_id?: string | null;
  account?: string | null;
  contract_id?: string | null;
  option_contract_id?: string | null;
  contract_symbol?: string | null;
  occ_symbol?: string | null;
  osi_symbol?: string | null;
  instrument_id?: string | null;
  instrument_identifier?: string | null;
  security_id?: string | null;
  is_paper?: boolean | null;
  source?: OptionalMonitorIdentity | null;
};

function isAbsentConstraint(value: unknown): boolean {
  return value == null || (typeof value === "string" && value.trim() === "");
}

export function omitAbsentMonitorConstraints(
  payload: Record<string, unknown>,
): Record<string, unknown> {
  const cleaned = { ...payload };
  for (const field of OPTIONAL_MONITOR_IDENTITY_FIELDS) {
    if (field in cleaned && isAbsentConstraint(cleaned[field])) {
      delete cleaned[field];
    }
  }
  if (
    cleaned.source &&
    typeof cleaned.source === "object" &&
    !Array.isArray(cleaned.source)
  ) {
    const source = { ...(cleaned.source as Record<string, unknown>) };
    for (const field of OPTIONAL_MONITOR_IDENTITY_FIELDS) {
      if (field in source && isAbsentConstraint(source[field])) {
        delete source[field];
      }
    }
    cleaned.source = source;
  }
  return cleaned;
}

export function buildManualTriggerPayload(
  symbol?: string,
  position?: MonitorPositionIdentity,
): Record<string, unknown> {
  return omitAbsentMonitorConstraints({
    symbol,
    ...position,
    run_trigger: "manual",
    force_alpha: true,
  });
}
