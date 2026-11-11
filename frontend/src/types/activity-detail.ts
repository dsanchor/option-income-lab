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
  alternative_reviews?: Partial<Record<RollAlternativeId, ScopedSupervisorReview>>;
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
  preferred_alternative_id?: RollAlternativeId | null;
  comparison_rationale?: string;
  comparison_status?: "complete" | "incomplete";
  additional_proposal_status?: "available" | "rejected";
  additional_proposal_reason?: string;
  additional_alternative?: AlphaAlternative & {
    baseline_alternative_id: RollAlternativeId;
    advisory_only: true;
    roll_economics: { net_credit: number; net_credit_total: number };
  };
  [k: string]: unknown;
}

export type RollAlternativeId = "DEFENSIVE" | "CREDIT_EXTENDED";

export interface ScopedSupervisorReview {
  review_status: "complete" | "incomplete";
  challenge_strength?: string;
  net_assessment?: string;
  one_liner?: string;
  counter_arguments?: CounterArgument[];
}

export interface RollAlternative {
  alternative_id: RollAlternativeId;
  status: "available" | "no_candidate" | "incomplete_data" | "no_defensive";
  reason?: string;
  new_strike?: number | string;
  new_expiration?: string;
  dte?: number;
  advisory_only?: boolean;
  expiry_checks?: {
    expiration: string;
    status: "available" | "no_candidate" | "incomplete_data";
    reasons: string[];
    bid: number | null;
    ask: number | null;
    buyback_ask: number;
    quote_asof: string | null;
  }[];
  roll_economics?: {
    buyback_cost?: number;
    new_premium?: number;
    net_credit?: number;
    net_credit_per_contract?: number;
    net_credit_total?: number;
    quantity?: number;
    multiplier?: number;
  };
  rule_evaluation?: RuleEvaluation;
}

export interface RollAlternatives {
  schema_version: number;
  preferred_alternative_id: RollAlternativeId;
  preference_reason: string;
  action_target_id: "DEFENSIVE";
  alternatives: Record<RollAlternativeId, RollAlternative>;
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
  roll_alternatives?: RollAlternatives | null;
  // Contract validation fields (backend/src/contract_validation_integration.py)
  run_id?: string | null;
  validation_status?: "approved" | "review_incomplete" | "error" | null;
  validation_source?: "best_options" | "options_screener" | null;
  primary_trace_id?: string | null;
  supervisor_trace_id?: string | null;
  alpha_trace_id?: string | null;
  // Chain-aware validation fields (NEW)
  requested_contract?: ContractRef | null;
  selected_contract?: ContractRef | null;
  relaxed_parameter?: string | null;
  comparison_rationale?: string | null;
  selection_source?: "requested_approved" | "requested_relaxed" | "alpha_alternative" | null;
  [k: string]: unknown;
}

/** Contract reference (strike/expiration/premium/delta) */
export interface ContractRef {
  strike: number;
  expiration: string;
  premium: number | null;
  delta: number | null;
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
