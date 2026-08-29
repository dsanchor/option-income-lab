// Shapes returned by GET /api/symbols/{symbol}/best-options (backend/src/best_options.py,
// schema_version 1). Design: .squad/decisions/inbox/danny-best-options-design.md
// ("Best Options" Analyze Page, accepted 2026-08-29).
//
// Deterministic screen of the option chain — no LLM in this path. Every field here
// is produced by the pure backend/src/best_options.py evaluator; the UI never derives
// or re-computes thresholds, colours, or scores itself (design §6/§4.4).

export type BestOptionsSideParam = "call" | "put" | "both";
export type BestOptionColor = "green" | "yellow" | "red";

export interface BestOptionsCategory {
  value: string;
  label: string;
  raw: string | null;
  source: string;
  defaulted: boolean;
}

export interface BestOptionsThresholds {
  delta_lo: number;
  delta_hi: number;
  premium_min_pct: number;
  premium_wait_pct: number;
  iv_rank_min: number | null;
}

/** Thresholds genuinely differ per strategy (e.g. `premium_min_pct` 0.8 for
 * covered calls vs 1.0 for cash-secured puts in the same category) -- the
 * real backend (`best_options.py`) nests `thresholds`/`thresholds_source`/
 * `skill_reference` per side rather than picking one flat set, which is the
 * only coherent shape for the shared `side=both` parameters panel. */
export interface BestOptionsThresholdsBySide {
  call: BestOptionsThresholds;
  put: BestOptionsThresholds;
}

export interface BestOptionsSourceBySide {
  call: string;
  put: string;
}

export interface BestOptionsDte {
  min: number;
  max: number;
  source: string;
  system_cap: number;
  timezone: string;
}

export interface BestOptionsPremiumMeta {
  basis: BestOptionsSourceBySide;
  input_field: string;
  dte_scaling: string;
}

export interface BestOptionsLiquidityMeta {
  min_open_interest: number;
  max_spread_pct: number;
}

export interface BestOptionsUnderlying {
  price: number | null;
  source: string;
}

export interface BestOptionsEarnings {
  next_earnings_date: string | null;
  source: string;
  known: boolean;
}

export interface BestOptionsChainMeta {
  timestamp: string | null;
  quote_asof_min: string | null;
  quote_asof_max: string | null;
  stale_contracts: number;
  total_contracts: number;
}

export interface BestOptionsWeights {
  annualized_return: number;
  cushion: number;
  delta_fit: number;
  liquidity: number;
}

export interface BestOptionsColorThresholds {
  green: number;
  yellow: number;
}

/** The `parameters` block — the single source of truth for the panel; must be the
 * same object the scorer consumed, never re-derived by the frontend (design §6). */
export interface BestOptionsParameters {
  schema_version: number;
  evaluated_at: string;
  category: BestOptionsCategory;
  thresholds: BestOptionsThresholdsBySide;
  thresholds_source: BestOptionsSourceBySide;
  skill_reference: BestOptionsSourceBySide;
  iv_rank_enforced: boolean;
  iv_rank_note: string;
  dte: BestOptionsDte;
  premium: BestOptionsPremiumMeta;
  liquidity: BestOptionsLiquidityMeta;
  underlying: BestOptionsUnderlying;
  atm_iv: number | null;
  earnings: BestOptionsEarnings;
  chain: BestOptionsChainMeta;
  weights: BestOptionsWeights;
  color_thresholds: BestOptionsColorThresholds;
}

export interface BestOptionGates {
  tradability: "pass" | "fail" | string;
  delta_band: "pass" | "fail" | string;
  earnings_span: "pass" | "fail" | "unknown" | string;
}

/** One row per contract in the DTE window — every row supplied by the evaluator,
 * never filtered further by the UI (design §4.1: "row inclusion, not a gate"). */
export interface BestOptionRow {
  expiration: string;
  dte: number;
  strike: number;
  bid: number | null;
  ask: number | null;
  mid: number | null;
  iv: number | null;
  delta: number | null;
  abs_delta: number | null;
  open_interest: number | null;
  premium_pct: number | null;
  annualized_return_pct: number | null;
  effective_min_pct: number | null;
  effective_wait_pct: number | null;
  collateral: number | null;
  score: number | null;
  color: BestOptionColor;
  label: string;
  components: Record<string, number | null>;
  components_missing: string[];
  weight_basis: number;
  gates: BestOptionGates;
  flags: string[];
  quote_asof: string | null;
  stale: boolean;
}

/** Always populated, even on an all-red or empty table (design §4.6) — the direct
 * answer to "why am I not getting alerts." Shape is descriptive, not fully fixed by
 * the design doc, so unknown fields are tolerated rather than dropped. */
export interface BestOptionsNearestMiss {
  description?: string;
  expiration?: string | null;
  dte?: number | null;
  strike?: number | null;
  abs_delta?: number | null;
  missed_gate?: string | null;
  missed_threshold?: string | null;
  missed_by?: number | null;
  missed_by_pct?: number | null;
  [key: string]: unknown;
}

export interface BestOptionsSide {
  rows: BestOptionRow[];
  nearest_miss: BestOptionsNearestMiss | null;
  truncated: boolean;
  total: number;
  /** DTE-window contracts the category delta band excluded from `rows` --
   * additive with `total` to recover the full DTE-window contract count
   * (design §4.1/§7). Never silently dropped from the response, only from
   * `rows`: still the candidate pool for `nearest_miss`. */
  excluded_by_delta_band: number;
  /** Call-side only: true when the held share count is below one full lot
   * (100 shares) -- the page-level "0 shares held" banner condition
   * (design §5), never a per-row flag. `null` on the put side and on a
   * call section that wasn't requested. */
  no_shares_held?: boolean | null;
}

export interface BestOptionsResponse {
  symbol: string;
  status: "ok";
  schema_version: number;
  parameters: BestOptionsParameters;
  calls: BestOptionsSide;
  puts: BestOptionsSide;
  cache?: {
    used: boolean;
    generation?: number;
    computed_at?: string;
    chain_timestamp?: string | null;
    chain_stale?: boolean;
    inputs_drift?: string[];
    refreshing?: boolean;
    refresh_started_at?: string | null;
    refresh_completed_at?: string | null;
    refresh_error?: string | null;
    reason?: string;
  };
}

export interface BestOptionsUnavailableResponse {
  status: "unavailable";
  symbol: string;
  reason: string;
  next_run?: string | null;
}

export interface BestOptionsWarmingResponse {
  status: "warming";
  symbol: string;
  retry_after: number;
  reason?: string;
  next_run?: string | null;
}

export interface BestOptionsErrorResponse {
  error: string;
  symbol?: string;
}

export type BestOptionsApiResponse =
  | BestOptionsResponse
  | BestOptionsUnavailableResponse
  | BestOptionsWarmingResponse
  | BestOptionsErrorResponse;