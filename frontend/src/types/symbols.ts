export interface SymbolWatchlistFlags {
  covered_call: boolean;
  cash_secured_put: boolean;
  buy_tracker: boolean;
}

export type SymbolListSection = "portfolio" | "watchlist";

export interface SymbolRow {
  symbol: string;
  display_name: string;
  category: string;
  dgi_score: number | null;
  tech_timing: number | null;
  entry_tag: string;
  momentum: string;
  price: number | null;
  total_shares: number;
  active_count: number;
  in_calls: number;
  put_exposure: number;
  call_exposure: number;
  watchlist?: SymbolWatchlistFlags;
  // Symbol Unification rev 3 — backfill fields
  list_section?: SymbolListSection;
  security_id?: string | null;
  portfolio_shares?: string | null;
  portfolio_avg_cost_eur?: string | null;
  portfolio_invested_eur?: string | null;
  // Unified Watchlist (Livingston contract)
  portfolio_dividends_eur?: string | null;
  portfolio_realized_eur?: string | null;
  row_source?: "portfolio" | "watchlist" | "both";
  is_auto_enrolled?: boolean;
  us_options_eligible?: boolean;
  screener_eligible?: boolean;
  // Pricing cache fields (Phase 4 — danny-symbol-pricing-cache-contract.md)
  price_display_currency?: string | null;  // quote currency as reported: "GBp", "USD", "EUR", "CHF"
  price_currency?: string | null;          // ISO 4217 major-unit code: "GBP", "USD", "EUR", "CHF"
  price_eur?: number | null;               // price_major × fx_rate, 2dp
  pricing_fetched_at?: string | null;      // ISO 8601 UTC timestamp of cache fetch
  pricing_status?: "ok" | "error" | "stale" | null;
  current_value_eur?: string | null;       // Decimal string — portfolio_shares × price_eur
}

export interface PortfolioSummary {
  // Livingston field names (shipped backend)
  total_investment_eur?: number | null;
  net_gains_eur?: number | null;
  total_dividends_eur?: number | null;
  has_incomplete_cost_basis?: boolean;
  calls_exposure_eur?: number | null;
  puts_committed_eur?: number | null;
  // Danny field names (contract aliases — accept both)
  remaining_cost_basis_eur?: string | null;
  realized_result_eur?: string | null;
  // Pricing Phase 4
  total_current_value_eur?: string | null;
}

export interface SymbolsOverview {
  // Livingston unified flat list (primary surface)
  symbols?: SymbolRow[];
  // Legacy sectioned arrays — kept for backward compat until migration confirmed
  rows?: SymbolRow[];
  portfolio_rows?: SymbolRow[];
  watchlist_rows?: SymbolRow[];
  symbol_count?: number;
  portfolio_count?: number;
  watchlist_count?: number;
  total_call_exposure?: number;
  total_put_exposure?: number;
  portfolio_summary?: PortfolioSummary;
  last_update_ts?: string;
  error?: string;
}
