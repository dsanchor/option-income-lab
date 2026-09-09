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

export interface DividendsSummary {
  total_gross_eur: number;
  total_fees_eur: number;
  total_withholding_eur: number;
  total_net_eur: number;
  effective_withholding_pct: number;
  total_dividends: number;
  total_accounts: number;
}

export interface DividendsMonthlyRow {
  month: string;
  gross_eur: number;
  fees_eur: number;
  withholding_source_eur: number;
  withholding_destination_eur: number;
  withholding_total_eur: number;
  net_eur: number;
  dividend_count: number;
}

export interface DividendsBySymbolRow {
  symbol: string;
  gross_eur: number;
  withholding_total_eur: number;
  net_eur: number;
  dividend_count: number;
}

export interface DividendsYearlyRow {
  year: number;
  gross_eur: number;
  withholding_eur: number;
  net_eur: number;
  dividend_count: number;
}

export interface DividendsCumulativeRow {
  month: string;
  cumulative_net_eur: number;
}

export interface DividendPosition {
  id: string;
  account_id: string | null;
  security_id: string | null;
  symbol: string;
  trade_date: string;
  gross_amount: number;
  gross_currency: string;
  gross_eur: number;
  fees_eur: number;
  withholding_source_eur: number;
  withholding_destination_eur: number;
  withholding_total_eur: number;
  net_eur: number;
  correction_status: string;
}

export interface DividendsFilters {
  years: number[];
  symbols: string[];
  account_ids: string[];
}

export interface DividendsAppliedFilters {
  year: number | null;
  months: number[] | null;
  symbols: string[] | null;
  account_ids: string[] | null;
}

export interface DividendsMeta {
  bucket_field: string;
  value_field: string;
}

export interface DividendsReport {
  summary: DividendsSummary;
  monthly: DividendsMonthlyRow[];
  by_symbol: DividendsBySymbolRow[];
  yearly: DividendsYearlyRow[];
  cumulative: DividendsCumulativeRow[];
  positions: DividendPosition[];
  filters: DividendsFilters;
  applied_filters: DividendsAppliedFilters;
  meta: DividendsMeta;
}

export type EconomicsAggregatedSource = "options" | "dividends" | "both";

export interface EconomicsAggregatedSummary {
  options_net_native: number;
  options_currency: string;
  dividends_net_eur: number;
  total_option_positions: number;
  total_dividend_events: number;
  total_symbols: number;
  fx_mode: string;
}

export interface EconomicsAggregatedMonthlyRow {
  month: string;
  options_net_native: number;
  dividends_net_eur: number;
  option_positions: number;
  dividend_events: number;
}

export interface EconomicsAggregatedBySymbolRow {
  symbol: string;
  options_net_native: number;
  dividends_net_eur: number;
  option_positions: number;
  dividend_events: number;
}

export interface EconomicsAggregatedFilters {
  years: number[];
  symbols: string[];
}

export interface EconomicsAggregatedAppliedFilters {
  year: number | null;
  months: number[] | null;
  symbols: string[] | null;
  source: EconomicsAggregatedSource | null;
}

export interface EconomicsAggregatedMeta {
  options_bucket_field: string;
  dividends_bucket_field: string;
  options_currency_native: string;
  dividends_currency: string;
  combined_total_available: boolean;
}

export interface EconomicsAggregatedReport {
  summary: EconomicsAggregatedSummary;
  monthly: EconomicsAggregatedMonthlyRow[];
  by_symbol: EconomicsAggregatedBySymbolRow[];
  filters: EconomicsAggregatedFilters;
  applied_filters: EconomicsAggregatedAppliedFilters;
  meta: EconomicsAggregatedMeta;
}
