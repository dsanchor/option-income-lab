export interface EconomicsSummary {
  total_premium: number;
  total_buyback: number;
  net_income: number;
  avg_roc_pct: number;
  avg_roc_annualized: number;
  win_rate: number;
  total_positions: number;
}

export interface EconomicsMonthlyRow {
  month: number;
  year: number;
  label: string;
  premium: number;
  buyback: number;
  net: number;
  calls_net: number;
  puts_net: number;
  positions_count: number;
  avg_roc_pct: number;
  avg_roc_annualized: number;
  calls_count: number;
  puts_count: number;
}

export interface EconomicsBySymbolRow {
  symbol: string;
  premium: number;
  buyback: number;
  net: number;
  positions_count: number;
  avg_roc_pct: number;
  avg_roc_annualized: number;
}

export interface EconomicsTypeMetrics {
  premium: number;
  buyback: number;
  net: number;
  count: number;
  avg_roc_pct: number;
  avg_roc_annualized: number;
}

export interface EconomicsPosition {
  symbol: string;
  position_id: string | null;
  type: string;
  strike: number | null;
  expiration: string | null;
  premium: number;
  premium_per_share: number;
  buyback_cost: number | null;
  buyback_per_share: number | null;
  net: number;
  roc_pct: number | null;
  roc_annualized: number | null;
  days_held: number | null;
  status: string;
  opened_at: string | null;
}

export interface EconomicsFilters {
  years: number[];
  symbols: string[];
}

export interface EconomicsReport {
  summary: EconomicsSummary;
  monthly: EconomicsMonthlyRow[];
  by_symbol: EconomicsBySymbolRow[];
  by_type: { calls: EconomicsTypeMetrics; puts: EconomicsTypeMetrics };
  positions: EconomicsPosition[];
  filters: EconomicsFilters;
  applied_filters: {
    year: number | null;
    symbols: string[] | null;
    type: string | null;
    status: string | null;
  };
}

export type EconomicsSortKey =
  | "symbol"
  | "type"
  | "strike"
  | "expiration"
  | "premium"
  | "buyback_cost"
  | "net"
  | "roc_pct"
  | "roc_annualized"
  | "days_held"
  | "status"
  | "opened_at";
