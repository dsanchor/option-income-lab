// Types for the Settings area (config / runtime / debug), mirroring the
// backend JSON endpoints under /api/settings/*.

/** One scheduler task's metadata from the registry. */
export interface SchedulerTask {
  name: string;
  display_name: string;
  config_key: string;
  enabled: boolean;
  cron: string;
  last_run?: string | null;
  next_run?: string | null;
  has_extra_config?: boolean;
}

/**
 * Flat config context returned by GET /api/settings/config. Field names match
 * the form/JSON keys accepted by POST /api/settings/config. Booleans are real
 * JSON booleans here (the backend converts them to "true"/"false" on save).
 */
export interface SettingsConfig {
  saved: string[];
  server_time: string;
  scheduler_tasks: SchedulerTask[];

  monitoring_enabled: boolean;
  cron_expr: string;
  monitoring_last_run: string;
  monitoring_next_run: string;
  monitoring_last_run_iso: string;
  monitoring_next_run_iso: string;

  summary_enabled: boolean;
  summary_cron: string;
  summary_activity_count: number;
  summary_last_run: string;
  summary_next_run: string;
  summary_last_run_iso: string;
  summary_next_run_iso: string;

  banner_enabled: boolean;
  banner_cron: string;
  banner_max_items: number;
  banner_last_run: string;
  banner_next_run: string;
  banner_last_run_iso: string;
  banner_next_run_iso: string;

  calendar_enabled: boolean;
  calendar_cron: string;
  calendar_last_run: string;
  calendar_next_run: string;
  calendar_last_run_iso: string;
  calendar_next_run_iso: string;

  options_chain_enabled: boolean;
  options_chain_cron: string;
  options_chain_last_run: string;
  options_chain_next_run: string;
  options_chain_last_run_iso: string;
  options_chain_next_run_iso: string;

  dgi_enabled: boolean;
  dgi_cron: string;
  dgi_top_n: number;
  dgi_symbols: string;
  dgi_last_run: string;
  dgi_next_run: string;
  dgi_last_run_iso: string;
  dgi_next_run_iso: string;

  pe_enabled: boolean;
  pe_cron: string;
  pe_last_run: string;
  pe_next_run: string;
  pe_last_run_iso: string;
  pe_next_run_iso: string;

  pf_enabled: boolean;
  pf_cron: string;
  pf_band_confidence: number;
  pf_vol_source: string;
  pf_trend_window: number;
  pf_trend_window_long: number;
  pf_last_run: string;
  pf_next_run: string;
  pf_last_run_iso: string;
  pf_next_run_iso: string;

  best_options_enabled: boolean;
  best_options_cron: string;
  best_options_run_on_startup: boolean;
  best_options_last_run: string;
  best_options_next_run: string;
  best_options_last_run_iso: string;
  best_options_next_run_iso: string;

  plan_monitor_enabled: boolean;
  plan_monitor_cron: string;
  plan_monitor_last_run: string;
  plan_monitor_next_run: string;
  plan_monitor_last_run_iso: string;
  plan_monitor_next_run_iso: string;

  telegram_enabled: boolean;
  telegram_bot_token: string;
  telegram_chat_id: string;
}

// -------- Runtime stats --------

export interface CachePeriodStats {
  count: number;
  avg_duration: number;
  error_count?: number;
}

export interface OptionsChainCacheEntry {
  age_seconds: number;
  expired: boolean;
}

export interface OptionsChainCacheStats {
  entries_count?: number;
  ttl_seconds?: number;
  entries?: Record<string, OptionsChainCacheEntry>;
}

export interface RecentFetchError {
  timestamp: string;
  symbol: string;
  resource: string;
  duration_seconds: number;
}

export interface TelemetryStats {
  agent_run?: Record<string, Record<string, CachePeriodStats>>;
  tv_fetch?: Record<string, Record<string, CachePeriodStats>>;
}

export interface SettingsRuntime {
  telemetry_stats: TelemetryStats;
  cache_stats: OptionsChainCacheStats;
  recent_errors: RecentFetchError[];
}

// -------- Debug --------

export interface DebugSymbol {
  symbol: string;
  display_name: string;
}

export interface SettingsDebug {
  cosmos_endpoint: string;
  cosmos_database: string;
  cosmos_status: string;
  cosmos_error: string | null;
  symbols: DebugSymbol[];
  cache_stats: {
    total_entries: number;
    symbols: string[];
  };
}

export interface FetchPreviewResource {
  text: string;
  size: number;
  duration_seconds: number | null;
}

export interface FetchPreview {
  resources: Record<string, FetchPreviewResource>;
}

export interface AgentChainStage {
  text: string;
  num_expirations?: number;
  num_contracts?: number;
}

export interface AgentChainPositionContext {
  strike?: number | string;
  expiration?: string;
  roll_type?: string;
  underlying_price?: number | string;
  underlying_price_source?: string;
}

export interface AgentChainResult {
  pipeline: {
    stage_1_delta_filtered: AgentChainStage;
    stage_2_position_filtered?: AgentChainStage;
    stage_3_direction_filtered?: AgentChainStage;
    stage_4_candidate_table?: AgentChainStage;
  };
  cache_age_seconds: number;
  position_context?: AgentChainPositionContext | null;
}
