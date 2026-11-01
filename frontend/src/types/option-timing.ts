export type OptionTimingSideKey = "covered_call" | "cash_secured_put";
export type OptionTimingBand = "red" | "orange" | "yellow" | "green";

export interface OptionTimingFactor {
  key: string;
  label: string;
  score: number;
  weight: number;
  contribution: number;
  evidence: string[];
}

export interface OptionTimingAdjustment {
  key: string;
  label: string;
  reason: string;
  cap: number | null;
}

export interface OptionTimingSide {
  score: number | null;
  band: OptionTimingBand | null;
  label: string;
  status: "available" | "unavailable" | "not_applicable";
  reason_codes: string[];
  factors: OptionTimingFactor[];
  adjustments: OptionTimingAdjustment[];
}

export interface OptionTimingSnapshot {
  schema_version: 1;
  snapshot_id: string;
  model_version: string;
  as_of: string;
  computed_at: string;
  source_session: string;
  input_timestamp_available?: boolean;
  provisional: boolean;
  freshness: "fresh" | "stale" | "unknown";
  covered_call: OptionTimingSide;
  cash_secured_put: OptionTimingSide;
}

export type OptionTimingPoint = OptionTimingSnapshot & {
  history_kind: "observed" | "reconstructed";
};

export interface OptionTimingHistory {
  security_id: string;
  schema_version: 1;
  model_version: string;
  methodology: string;
  points: OptionTimingPoint[];
}

export interface OptionTimingExplainResponse {
  reply: string;
  side: OptionTimingSideKey;
  snapshot_id: string;
  score: number;
  as_of: string;
  provisional: boolean;
  freshness: "fresh" | "stale" | "unknown";
  generated_at: string;
  ai_function: "option_timing_explain";
  model: string;
  provider: string;
}
