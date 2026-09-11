export interface EconomicsSummary {
  total_premium_usd: number;
  total_buyback_usd: number;
  net_option_usd_gross: number;
  net_income_eur: number;
  total_commission_eur: number;
  avg_roc_pct: number;
  avg_roc_annualized: number;
  win_rate: number;
  total_positions: number;
  coverage: EconomicsCoverage;
}

export interface EconomicsCoverage {
  linked_positions: number;
  total_positions: number;
  linked_ratio: number;
  positions_with_unresolved_security: number;
  positions_missing_opening_sell: number;
  positions_missing_closing_buy: number;
  positions_missing_assignment_stock: number;
  excluded_unlinked_positions: number;
  excluded_positions_linked_only_outside_account_filter: number;
  excluded_paper_positions: number;
}

export interface EconomicsMonthlyRow {
  month: number;
  year: number;
  label: string;
  total_premium_usd: number;
  total_buyback_usd: number;
  net_option_usd_gross: number;
  net_income_eur: number;
  total_commission_eur: number;
  calls_net_income_eur: number;
  puts_net_income_eur: number;
  positions_count: number;
  avg_roc_pct: number;
  avg_roc_annualized: number;
  calls_count: number;
  puts_count: number;
}

export interface EconomicsBySymbolRow {
  symbol: string;
  total_premium_usd: number;
  total_buyback_usd: number;
  net_option_usd_gross: number;
  net_income_eur: number;
  total_commission_eur: number;
  positions_count: number;
  avg_roc_pct: number;
  avg_roc_annualized: number;
}

export interface EconomicsTypeMetrics {
  total_premium_usd: number;
  total_buyback_usd: number;
  net_option_usd_gross: number;
  net_income_eur: number;
  total_commission_eur: number;
  count: number;
  avg_roc_pct: number;
  avg_roc_annualized: number;
}

export interface EconomicsPosition {
  symbol: string;
  position_id: string | null;
  type: string;
  is_paper: boolean;
  strike: number | null;
  expiration: string | null;
  premium_usd: number;
  buyback_usd: number;
  net_option_usd_gross: number;
  net_income_eur: number;
  total_commission_eur: number;
  roc_pct: number | null;
  roc_annualized: number | null;
  days_held: number | null;
  status: string;
  close_reason: string | null;
  opened_at: string | null;
  closed_at: string | null;
  rolled_from: string | null;
  rolled_to: string | null;
  linked_accounts: string[];
  linked_movement_count: number;
  coverage_status: "linked" | "unlinked" | "unresolved_security" | "account_filtered_out" | "paper";
  warnings: string[];
  resolved_security_id: string | null;
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
    months: number[] | null;
    symbols: string[] | null;
    type: string | null;
    status: string | null;
    account_ids: string[] | null;
  };
}

export type EconomicsSortKey =
  | "symbol"
  | "type"
  | "strike"
  | "expiration"
  | "premium_usd"
  | "buyback_usd"
  | "net_option_usd_gross"
  | "net_income_eur"
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
  cash_net?: number | null;
  derechos_net?: number | null;
  total_net?: number | null;
  effective_withholding_pct: number;
  total_dividends: number;
  total_accounts: number;
  portfolio_yoc_pct?: number | null;
}

export interface DividendsMonthlyRow {
  month: string;
  gross_eur: number;
  fees_eur: number;
  withholding_source_eur: number;
  withholding_destination_eur: number;
  withholding_total_eur: number;
  net_eur: number;
  cash_net?: number | null;
  derechos_net?: number | null;
  total_net?: number | null;
  dividend_count: number;
}

export interface DividendsBySymbolRow {
  symbol: string;
  gross_eur: number;
  withholding_total_eur: number;
  net_eur: number;
  cash_net?: number | null;
  derechos_net?: number | null;
  total_net?: number | null;
  dividend_count: number;
  yoc_pct?: number | null;
  yoc_basis?: "annualized" | "insufficient_history" | null;
  yoc_dividend_frequency?: number | null;
  yoc_trailing_annual_dividend_net_eur?: number | null;
  yoc_cost_basis_eur?: number | null;
}

export interface DividendsYearlyRow {
  year: number;
  gross_eur: number;
  withholding_eur: number;
  net_eur: number;
  cash_net?: number | null;
  derechos_net?: number | null;
  total_net?: number | null;
  dividend_count: number;
}

export interface DividendsCumulativeRow {
  month: string;
  cumulative_net_eur: number;
  cash_net?: number | null;
  derechos_net?: number | null;
  total_net?: number | null;
  cumulative_cash_net_eur?: number | null;
  cumulative_derechos_net_eur?: number | null;
  cumulative_total_net_eur?: number | null;
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
  cash_net?: number | null;
  derechos_net?: number | null;
  derechos_eur?: number | null;
  total_net?: number | null;
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
  options_net_eur: number;
  dividends_net_eur: number;
  dividends_cash_net_eur?: number;
  dividends_derechos_net_eur?: number;
  dividends_total_net_eur?: number;
  combined_net_eur: number;
  total_option_positions: number;
  options_coverage: EconomicsCoverage;
  total_dividend_events: number;
  total_symbols: number;
  portfolio_yoc_pct?: number | null;
}

export interface EconomicsAggregatedMonthlyRow {
  month: string;
  options_net_eur: number;
  dividends_net_eur: number;
  dividends_cash_net_eur?: number;
  dividends_derechos_net_eur?: number;
  dividends_total_net_eur?: number;
  combined_net_eur: number;
  option_positions: number;
  dividend_events: number;
}

export interface EconomicsAggregatedBySymbolRow {
  symbol: string;
  options_net_eur: number;
  dividends_net_eur: number;
  dividends_cash_net_eur?: number;
  dividends_derechos_net_eur?: number;
  dividends_total_net_eur?: number;
  combined_net_eur: number;
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
  account_ids: string[] | null;
  source: EconomicsAggregatedSource | null;
}

export interface EconomicsAggregatedMeta {
  options_bucket_field: string;
  dividends_bucket_field: string;
  options_currency: string;
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
