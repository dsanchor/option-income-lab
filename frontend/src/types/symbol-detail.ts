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
  contracts?: number;
  assignment_risk?: string | null;
  moneyness?: string | null;
  display_premium?: number | null;
  display_buyback?: number | null;
  source?: { agent_type?: string; premium?: unknown; [k: string]: unknown };
  [k: string]: unknown;
}

export interface Activity {
  activity_id?: string;
  agent_type?: string;
  _agent_key?: string;
  _agent_label?: string;
  decision?: string;
  note?: string;
  reason?: string;
  confidence?: number | string;
  is_alert?: boolean;
  timestamp?: string;
  [k: string]: unknown;
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
  plans: Plan[];
  summary: {
    in_calls: number;
    put_exposure: number;
    call_exposure: number;
    active_count: number;
  };
  next_earnings_date?: string | null;
  is_paused: boolean;
  error?: string;
}
