import type { RuleEvaluation } from "@/types/activity-detail";

export interface BannerItem {
  emoji?: string;
  symbol?: string;
  text?: string;
  priority?: number | string;
  category?: string;
}

export interface RecentActivityRef {
  activity?: string;
  timestamp?: string;
  id?: string;
  reason?: string;
}

export interface AgentRow {
  key: string;
  symbol: string;
  display: string;
  underlying_price?: number | null;
  recent_activities?: RecentActivityRef[];
  risk_flags?: string[];
  paused?: boolean;
  paused_until?: string | null;
  // position monitor
  dte?: number | null;
  moneyness?: string | null;
  assignment_risk?: string | null;
  delta?: number | null;
  strike_pct?: number | null;
  option_type?: string;
  dps_score?: number | null;
  dps_delta_7d?: number | null;
  dps_delta_1d?: number | null;
  pnl_pct?: number | null;
  // buy tracker
  entry_zone?: string | null;
  technical_triggers?: string[];
  // watchlist agents
  strike?: number | string | null;
  expiration?: string | null;
  premium?: number | null;
  recommendation_source?: "agent" | "alpha" | null;
  [k: string]: unknown;
}

export interface AgentTable {
  key: string;
  label: string;
  rows: AgentRow[];
  totals?: { today: number; week: number; month: number; total: number };
  is_position_monitor: boolean;
  last_update_ts?: string;
}

export interface AgentTypeMeta {
  label: string;
  [k: string]: unknown;
}

export interface SupervisorView {
  challenge_strength?: string;
  one_liner?: string;
  [k: string]: unknown;
}

export interface AlphaView {
  opportunity_strength?: string;
  one_liner?: string;
  [k: string]: unknown;
}

export interface ActivityItem {
  id?: string;
  symbol?: string;
  _agent_label?: string;
  _agent_key?: string;
  activity?: string;
  decision?: string;
  confidence?: number | string;
  strike?: number | string;
  expiration?: string;
  risk_rating?: number | null;
  assignment_risk?: string | null;
  waiting_for?: string | null;
  is_alert?: boolean;
  data_error?: boolean;
  supervisor_view?: SupervisorView | null;
  alpha_view?: AlphaView | null;
  rule_evaluation?: RuleEvaluation | null;
  timestamp?: string;
  created_at?: string;
  // Contract validation fields
  run_id?: string | null;
  validation_status?: "approved" | "review_incomplete" | "error" | null;
  validation_source?: "best_options" | "options_screener" | null;
  [k: string]: unknown;
}

export interface GrandTotals {
  today: number;
  week: number;
  month: number;
  total: number;
}

export interface DashboardData {
  agent_tables?: AgentTable[];
  agent_types?: Record<string, AgentTypeMeta>;
  grand_totals?: GrandTotals;
  symbol_count?: number;
  position_count?: number;
  total_call_exposure?: number;
  total_put_exposure?: number;
  open_roc_annualized?: number;
  activity?: ActivityItem[];
  banner_items?: BannerItem[];
  market_open?: boolean;
  error?: string;
}
