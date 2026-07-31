export interface DgiMetrics {
  dividend_yield?: number;
  dividend_cagr_5y?: number;
  years_consecutive_increases?: number;
  current_price?: number;
  [key: string]: unknown;
}

export interface DgiTechnicals {
  score?: number;
  [key: string]: unknown;
}

export interface DgiFilterCheck {
  passes: boolean;
  label: string;
  actual: number | string;
  threshold: number | string;
  op: string;
}

export interface DgiFilterDetail {
  passes_all?: boolean;
  checks?: Record<string, DgiFilterCheck>;
}

export interface DgiQualityDetail {
  sub_scores?: Record<string, number>;
  minimum_thresholds?: Record<string, number>;
  ideal_thresholds?: Record<string, number>;
}

export interface DgiScorePoint {
  date: string;
  score: number;
}

export interface DgiEntry {
  rank?: number;
  symbol: string;
  category?: string;
  quality_score?: number;
  entry_tag?: string;
  days_on_list?: number;
  first_appeared?: string;
  exchange?: string;
  last_updated?: string;
  metrics?: DgiMetrics;
  technicals?: DgiTechnicals;
  filter_detail?: DgiFilterDetail;
  quality_detail?: DgiQualityDetail;
  score_history?: DgiScorePoint[];
  [key: string]: unknown;
}

export interface DgiTopResponse {
  top?: DgiEntry[];
  error?: string;
}
