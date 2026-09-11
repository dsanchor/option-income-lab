export interface EnrichmentMetrics {
  current_price?: number | null;
  [k: string]: unknown;
}

export interface EnrichmentTechnicals {
  score?: number | null;
  [k: string]: unknown;
}

export interface Enrichment {
  category?: string;
  quality_score?: number | null;
  entry_tag?: string;
  momentum?: string;
  metrics?: EnrichmentMetrics;
  technicals?: EnrichmentTechnicals;
  last_updated?: string;
  [k: string]: unknown;
}

export interface Position {
  position_id?: string;
  type?: string;
  strike?: number;
  expiration?: string;
  status?: string;
  coverage_status?: "linked" | "unlinked" | "unresolved_security" | "account_filtered_out" | "paper" | null;
  contracts?: number;
  assignment_risk?: string | null;
  moneyness?: string | null;
  display_premium?: number | null;
  display_buyback?: number | null;
  opened_at?: string | null;
  close_reason?: string | null;
  warnings?: string[] | null;
  linked_accounts?: string[] | null;
  linked_movement_count?: number | null;
  notes?: string | null;
  source?: { agent_type?: string; premium?: unknown; [k: string]: unknown };
  [k: string]: unknown;
}

import type { RuleEvaluation } from "@/types/activity-detail";

export interface Activity {
  activity_id?: string;
  id?: string;
  agent_type?: string;
  _agent_key?: string;
  _agent_label?: string;
  decision?: string;
  note?: string;
  reason?: string;
  activity?: string;
  confidence?: number | string;
  is_alert?: boolean;
  data_error?: boolean;
  timestamp?: string;
  strike?: number | string | null;
  new_strike?: number | string | null;
  current_strike?: number | string | null;
  expiration?: string | null;
  new_expiration?: string | null;
  current_expiration?: string | null;
  underlying_price?: number | string | null;
  risk_rating?: number | null;
  assignment_risk?: string | null;
  supervisor_view?: { challenge_strength?: string; one_liner?: string; [k: string]: unknown } | null;
  alpha_view?: { one_liner?: string; [k: string]: unknown } | null;
  rule_evaluation?: RuleEvaluation | null;
  [k: string]: unknown;
}

export interface AgentType {
  key: string;
  label: string;
}

export interface Plan {
  id?: string;
  symbol?: string;
  title?: string;
  plan_type?: string;
  status?: string;
  objective?: string;
  notes?: string;
  updated_at?: string;
  [k: string]: unknown;
}

export interface WatchlistToggles {
  covered_call: boolean;
  cash_secured_put: boolean;
  buy_tracker: boolean;
}

export interface SymbolSummary {
  in_calls: number;
  put_exposure: number;
  call_exposure: number;
  active_count: number;
}

// Symbol Unification rev 3 — new types

export interface SecurityMasterInfo {
  security_id: string;
  company_name: string;
  exchange_mic: string;
  // ticker component extracted from security_id (returned by backend when available)
  ticker?: string | null;
  isin?: string | null;
  cusip?: string | null;
  sedol?: string | null;
  listing_currency?: string;
  country?: string | null;
  asset_class?: string | null;
  status?: string;
  // Provider symbol overrides (editable via PATCH /security)
  provider_symbols?: Record<string, string> | null;
  // Effective resolved values — computed backend-side, read-only in UI
  effective_yfinance_symbol?: string | null;
  effective_tradingview_symbol?: string | null;
  // Optimistic concurrency token for PATCH
  _etag?: string | null;
  updated_at?: string | null;
}

export interface HoldingsByAccount {
  account_id: string;
  account_name?: string | null;
  shares: string;
  avg_cost_eur?: string | null;
  current_invested_eur?: string | null;
  total_dividends_eur?: string | null;
}

export interface RecentMovement {
  id: string;
  txn_type: string;
  trade_date: string;
  quantity?: string | null;
  gross_eur?: string | null;
  // Extended fields for Stocks tab (added 2026-09-06)
  fees_eur?: string | null;
  net_eur?: string | null;
  currency?: string | null;
  account_id?: string | null;
  sales_type?: string | null;          // SELL only: ACCIONES | DERECHOS
  correction_status?: string | null;   // ACTIVE | SUPERSEDED | VOIDED
  import_source?: string | null;
  // NOTE: DIVIDEND withholding fields pending Danny's contract amendment
}

export interface PortfolioSection {
  current_shares: string;
  average_cost_eur?: string | null;
  current_invested_eur?: string | null;
  total_dividends_eur?: string | null;
  holdings_by_account?: HoldingsByAccount[];
  recent_movements?: RecentMovement[];
  movement_count?: number;
}

export type SymbolState =
  | "watchlist_only"
  | "portfolio_only"
  | "watchlist_and_portfolio"
  | "portfolio_historical";

export interface DisambiguationChoice {
  security_id: string;
  company_name: string;
  exchange_mic: string;
}

export interface SymbolDisambiguationResult {
  multiple_choices: DisambiguationChoice[];
  query: string;
}

export interface SymbolDetail {
  symbol: string;
  display_name: string;
  exchange: string;
  total_shares: number;
  watchlist: WatchlistToggles;
  telegram_notifications_enabled: boolean;
  enrichment: Enrichment;
  positions: Position[];
  activities: Activity[];
  agent_types?: AgentType[];
  plans: Plan[];
  summary: SymbolSummary;
  next_earnings_date?: string | null;
  is_paused: boolean;
  // Symbol Unification rev 3 — new fields (null when not yet deployed by backend)
  security_id?: string | null;
  security?: SecurityMasterInfo | null;
  portfolio?: PortfolioSection | null;
  symbol_state?: SymbolState | null;
  // Unified Watchlist — backend-resolved eligibility flag (Amendment J)
  us_options_eligible?: boolean | null;
  // TradingView — backend-resolved provider symbol (e.g. "BME-ACS", "NASDAQ-MSFT"); null when unresolved
  tradingview_symbol?: string | null;
  error?: string;
}
