export interface AgentTraceRow {
  id: string;
  symbol?: string;
  agent_type?: string;
  model?: string;
  phase?: string;
  is_alert?: boolean;
  duration_seconds?: number | null;
  timestamp?: string;
  error?: string;
  activity_summary?: string;
  confidence?: string;
  activity?: string;
  /** Correlates every trace written during one decision cycle (analysis/
   * assessment/roll plus any supervisor/alpha reviews it spawned). */
  run_id?: string;
  /** Trace id of the phase that causally precedes this one within the
   * same run_id (e.g. a supervisor/alpha review's parent is the phase
   * whose decision it audited). `undefined` for a pipeline entry point. */
  parent_trace_id?: string;
}

export interface AgentTypeMeta {
  label: string;
}

export interface AgentTracesResponse {
  traces: AgentTraceRow[];
  total: number;
  symbols: string[];
  agent_types: Record<string, AgentTypeMeta>;
  trace_enabled: Record<string, boolean>;
  cosmos_available: boolean;
}

export interface AgentTraceDetail {
  id: string;
  symbol?: string;
  agent_type?: string;
  phase?: string;
  model?: string;
  duration_seconds?: number | null;
  confidence?: string;
  activity?: string;
  is_alert?: boolean;
  error?: string;
  skills?: string[];
  system_prompt?: string;
  user_message?: string;
  response_text?: string;
  parsed?: unknown;
  extra?: unknown;
  timestamp?: string;
  run_id?: string;
  parent_trace_id?: string;
  [key: string]: unknown;
}

export interface AgentTraceDetailResponse {
  trace: AgentTraceDetail;
  agent_label: string;
  error?: string;
}
