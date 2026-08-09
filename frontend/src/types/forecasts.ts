// Shapes returned by GET /api/symbols/{symbol}/forecasts

export const HORIZONS = ["1d", "1w", "2w", "4w"] as const;
export type Horizon = (typeof HORIZONS)[number];

export interface HorizonEndpoint {
  offset?: number | null;
  price?: number | null;
  date?: string | null;
  inside_1sigma?: boolean | null;
  inside_2sigma?: boolean | null;
  direction_correct?: boolean | null;
  is_endpoint?: boolean | null;
}

export interface HorizonSummary {
  path_count: number;
  path_pct_1sigma: number | null;
  path_pct_2sigma: number | null;
  endpoint: HorizonEndpoint | null;
  center: number | null;
  sigma: number | null;
  low1: number | null;
  high1: number | null;
  low2: number | null;
  high2: number | null;
  trend_end: number | null;
  trend_slope?: number | null;
  start_session?: number | null;
  end_session?: number | null;
  mean_dev: number | null;
  mean_dev_pct: number | null;
}

export interface ForecastSnapshot {
  offset: number | null;
  price: number | null;
  inside_1sigma: boolean | null;
  inside_2sigma: boolean | null;
  is_endpoint?: boolean | null;
}

/** Full single-forecast document returned by GET /forecasts/{id} — feeds the fan chart. */
export interface ForecastDetail {
  id: string;
  created_date: string | null;
  price_at_creation: number | null;
  hv: number | null;
  vol_source?: string | null;
  bias: number | null;
  confidence?: number | null;
  outer_confidence?: number | null;
  trend?: { slope?: number | null; r2?: number | null; quality?: string | null } | null;
  flags?: Record<string, unknown> | null;
  horizons?: Partial<Record<Horizon, HorizonSummary>>;
  snapshots?: ForecastSnapshot[];
  endpoints?: Record<string, HorizonEndpoint>;
  error?: string;
}

/** Trading-session offset at each horizon's END, for chart markers. */
export const HORIZON_END_SESSION: Record<Horizon, number> = { "1d": 1, "1w": 5, "2w": 10, "4w": 20 };

export interface ForecastRow {
  id: string;
  created_date: string;
  start_date: string | null;
  end_date: string | null;
  status: string | null;
  price_at_creation: number | null;
  hv: number | null;
  vol_source: string;
  confidence: number;
  outer_confidence: number;
  bias: number | null;
  trend: { slope?: number | null } & Record<string, unknown> | null;
  reading: string | null;
  flags: Record<string, unknown>;
  horizons: Partial<Record<Horizon, HorizonSummary>>;
}

export interface HitRateEntry {
  resolved: number;
  hit_pct_1sigma: number | null;
  hit_pct_2sigma: number | null;
  direction_pct: number | null;
  direction_n: number;
  mean_dev_pct: number | null;
  mean_dev_n: number;
}

export interface AverageEntry {
  n: number;
  lookback: number;
  anchor: number | null;
  mean: number | null;
  trimmed_mean: number | null;
  low: number | null;
  high: number | null;
}

export interface ForecastCalibration {
  k: number | null;
  k_target?: number | null;
  prev_k?: number | null;
  n?: number | null;
  target?: number | null;
  applied?: boolean | null;
  updated?: string | null;
}

export interface ForecastsResponse {
  symbol: string;
  range: { from: string | null; to: string | null };
  count: number;
  confidence: number;
  outer_confidence: number;
  calibration: ForecastCalibration | number | null;
  rows: ForecastRow[];
  hit_rate: Partial<Record<Horizon, HitRateEntry>>;
  averages: Partial<Record<Horizon, AverageEntry>>;
  error?: string;
}
