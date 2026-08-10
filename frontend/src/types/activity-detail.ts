/**
 * Backend rule_evaluation schema (backend/src/rule_evaluator.py, schema_version 1).
 * Persisted verbatim on the activity document — never derived client-side.
 * Financial-viability rules (cash/buying-power/collateral/margin) are excluded
 * by the backend and must never be rendered even if present defensively.
 */
export type RuleStatus =
  | "blocked"
  | "fail"
  | "warning"
  | "unknown"
  | "pass"
  | "not_applicable"
  | "informational";

export interface RuleResult {
  rule_id: string;
  label: string;
  group?: string;
  status: RuleStatus;
  blocking?: boolean;
  expected?: string | null;
  observed?: string | null;
  source?: string;
  detail?: string | null;
  data_refs?: Record<string, unknown>;
  [k: string]: unknown;
}

export interface RuleEvaluationPhase {
  phase?: string | null;
  rules: RuleResult[];
}

export interface RuleEvaluation {
  schema_version?: number;
  agent_type?: string;
  phase?: string | null;
  rules?: RuleResult[];
  phases?: RuleEvaluationPhase[];
  generated_at?: string;
  first_blocker?: string | null;
  summary_counts?: Partial<Record<RuleStatus, number>>;
  [k: string]: unknown;
}

export interface CounterArgument {
  point?: string;
  data_support?: string;
}

export interface SupervisorView {
  challenge_strength?: string;
  counter_arguments?: CounterArgument[];
  net_assessment?: string;
  one_liner?: string;
  [k: string]: unknown;
}

export interface AlphaAlternative {
  action?: string;
  rationale?: string;
  premium_comparison?: string;
  strike?: number | string;
  expiration?: string;
  dte?: number | string;
  premium?: number | string;
  delta?: number | string;
  trade_off?: string;
  additional_risk?: string;
  [k: string]: unknown;
}

export interface AlphaView {
  opportunity_strength?: string;
  relaxed_parameter?: string;
  parameter_detail?: string;
  alternative?: AlphaAlternative | null;
  one_liner?: string;
  [k: string]: unknown;
}

export interface CreatedFrom {
  source_agent?: string;
  source_activity_id?: string;
  recommendation?: string;
  [k: string]: unknown;
}

export interface ActivityDoc {
  id?: string;
  symbol?: string;
  agent_type?: string;
  activity?: string;
  reason?: string;
  timestamp?: string;
  confidence?: number | string;
  is_alert?: boolean;
  data_error?: boolean;
  risk_rating?: number | null;
  assignment_risk?: string | null;
  underlying_price?: number | string | null;
  strike?: number | string | null;
  new_strike?: number | string | null;
  current_strike?: number | string | null;
  expiration?: string | null;
  new_expiration?: string | null;
  current_expiration?: string | null;
  premium?: number | string | null;
  iv?: number | string | null;
  risk_flags?: string[];
  position_id?: string | null;
  created_from?: CreatedFrom | null;
  supervisor_view?: SupervisorView | null;
  alpha_view?: AlphaView | null;
  rule_evaluation?: RuleEvaluation | null;
  [k: string]: unknown;
}

export interface ActivityDetail {
  activity: ActivityDoc;
  symbol: string;
  display_name: string;
  agent_type: string;
  agent_label: string;
  is_alert: boolean;
  error?: string;
}
