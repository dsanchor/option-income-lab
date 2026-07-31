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
  [key: string]: unknown;
}

export interface AgentTraceDetailResponse {
  trace: AgentTraceDetail;
  agent_label: string;
  error?: string;
}
