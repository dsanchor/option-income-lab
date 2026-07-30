export interface BannerItem {
  emoji?: string;
  symbol?: string;
  text?: string;
  priority?: number | string;
  category?: string;
}

export interface ActivityItem {
  id?: string;
  symbol?: string;
  _agent_label?: string;
  _agent_key?: string;
  decision?: string;
  confidence?: number | string;
  timestamp?: string;
  created_at?: string;
  [k: string]: unknown;
}

export interface GrandTotals {
  today: number;
  week: number;
  month: number;
  total: number;
}

export interface DashboardData {
  agent_tables?: unknown[];
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
