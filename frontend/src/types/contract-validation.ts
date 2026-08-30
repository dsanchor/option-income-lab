/**
 * Contract validation request/response types.
 *
 * Backend contracts: backend/src/contract_validation_integration.py,
 * backend/web/app.py@3568+
 */

import type { ContractRef } from "@/types/activity-detail";

/** Request body for POST /api/best-options/validate */
export interface ValidateContractRequest {
  symbol: string;
  side: "call" | "put";
  strike: number;
  expiration: string; // ISO date (YYYY-MM-DD)
  source: "best_options" | "options_screener";
  displayed_snapshot?: Record<string, unknown> | null;
}

/** Success response (202 Accepted) */
export interface ValidateContractAccepted {
  status: "accepted";
  run_id: string;
  started_at: string;
  message: string;
  status_url: string;
}

/** Duplicate in-flight request (409 Conflict) */
export interface ValidateContractDuplicate {
  status: "duplicate";
  run_id: string;
  started_at: string;
}

/** Max concurrency reached (429 Too Many Requests) */
export interface ValidateContractRateLimited {
  status: "max_concurrency";
  retry_after: number;
}

/** Validation error (400 Bad Request) */
export interface ValidateContractError {
  status: "error";
  message: string;
}

export type ValidateContractResponse =
  | ValidateContractAccepted
  | ValidateContractDuplicate
  | ValidateContractRateLimited
  | ValidateContractError;

/** Status polling response: in-progress */
export interface ValidationStatusInProgress {
  status: "in_progress";
  run_id: string;
  started_at: string;
  symbol: string;
  side: "call" | "put";
  strike: number;
  expiration: string;
}

/** Status polling response: completed */
export interface ValidationStatusCompleted {
  status: "completed";
  run_id: string;
  activity_id: string;
  symbol: string;
  agent_type: string;
  activity: string; // SELL, WAIT, etc.
  is_alert: boolean;
  timestamp: string;
  // Canonical agent fields (same as normal agent runs)
  reason?: string | null;
  confidence?: string | null;
  underlying_price?: number | null;
  strike?: number | null;
  expiration?: string | null;
  premium?: number | null;
  iv?: number | null;
  risk_rating?: number | null;
  risk_flags?: string[] | null;
  assignment_risk?: string | null;
  // Validation metadata
  validation_status?: "approved" | "review_incomplete" | "error" | null;
  run_trigger?: string | null;
  // Trace/review outputs (same as normal runs)
  rule_evaluation?: Record<string, unknown> | null;
  primary_trace_id?: string | null;
  supervisor_view?: Record<string, unknown> | null;
  supervisor_trace_id?: string | null;
  alpha_view?: Record<string, unknown> | null;
  alpha_trace_id?: string | null;
  // Error if present
  error?: string | null;
  // Chain-aware validation fields (NEW)
  requested_contract?: ContractRef | null;
  selected_contract?: ContractRef | null;
  relaxed_parameter?: string | null;
  comparison_rationale?: string | null;
  selection_source?: "requested_approved" | "alpha_alternative" | null;
  // Backward compatibility
  note?: string | null;
}

/** Status polling response: not found (404) */
export interface ValidationStatusNotFound {
  status: "not_found";
}

export type ValidationStatusResponse =
  | ValidationStatusInProgress
  | ValidationStatusCompleted
  | ValidationStatusNotFound;
