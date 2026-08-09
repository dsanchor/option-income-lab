export interface SymbolWatchlistFlags {
  covered_call: boolean;
  cash_secured_put: boolean;
  buy_tracker: boolean;
}

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
}

export interface SymbolsOverview {
  rows?: SymbolRow[];
  symbol_count?: number;
  total_call_exposure?: number;
  total_put_exposure?: number;
  last_update_ts?: string;
  error?: string;
}
