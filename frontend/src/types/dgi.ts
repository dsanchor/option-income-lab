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

// ── Single-symbol analysis (GET /api/dgi/analyze/{symbol}) ─────────────

export interface DgiAnalysisMetrics {
  dividend_yield: number;
  dividend_cagr_5y: number;
  years_consecutive_increases: number;
  payout_ratio: number;
  pe_ratio: number;
  forward_pe: number;
  debt_to_equity: number;
  roe: number;
  market_cap: number;
  current_price: number;
  sector: string;
  exchange: string;
}

export interface DgiBollinger {
  position?: number;
  [key: string]: number | undefined;
}

export interface DgiAnalysisTechnicals {
  score?: number;
  rsi?: number;
  adx?: number;
  sma_50?: number;
  sma_200?: number;
  high_52w?: number;
  low_52w?: number;
  bb?: DgiBollinger;
  sub_scores?: {
    rsi_score?: number;
    sma_score?: number;
    high_dist_score?: number;
    bb_score?: number;
  };
}

export interface DgiAnalysisQualityDetail {
  total: number;
  sub_scores: Record<string, number>;
  weights: Record<string, number>;
  health_detail?: {
    debt_to_equity_score: number;
    roe_score: number;
  };
  minimum_thresholds: Record<string, number>;
  ideal_thresholds: Record<string, number>;
}

export interface DgiAnalysis {
  symbol: string;
  name: string;
  metrics: DgiAnalysisMetrics;
  technicals: DgiAnalysisTechnicals;
  quality_score: number;
  quality_detail: DgiAnalysisQualityDetail;
  entry_tag: string;
  momentum: string;
  category: string;
  has_dividends: boolean;
  passes_minimum_filters: boolean;
  filter_detail?: DgiFilterDetail;
  error?: string;
}
