/**
 * Contract validation request/response types.
 *
 * Backend contracts: backend/src/contract_validation_integration.py,
 * backend/web/app.py@3568+
 */

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
  activity: string; // SELL, WAIT, etc.
  is_alert: boolean;
  validation_status: "approved" | "review_incomplete" | "error";
  note: string;
  symbol: string;
  side: "call" | "put";
  strike: number;
  expiration: string;
  timestamp: string;
}

/** Status polling response: not found (404) */
export interface ValidationStatusNotFound {
  status: "not_found";
}

export type ValidationStatusResponse =
  | ValidationStatusInProgress
  | ValidationStatusCompleted
  | ValidationStatusNotFound;
