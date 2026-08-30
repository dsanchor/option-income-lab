/**
 * Shared hook for contract validation with bounded backoff polling.
 * 
 * Reused by BestOptionsView and OptionsScreenerView for per-row validate actions.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  ValidateContractRequest,
  ValidateContractResponse,
  ValidationStatusResponse,
} from "@/types/contract-validation";

export interface ValidationState {
  /** Validation in progress for this contract */
  validating: boolean;
  /** run_id from accepted/duplicate response */
  runId: string | null;
  /** Completed result (SELL/WAIT/error) */
  result: ValidationStatusResponse | null;
  /** Error message (network/API errors) */
  error: string | null;
}

interface UseContractValidationOptions {
  /** Called when validation completes successfully */
  onComplete?: (result: Extract<ValidationStatusResponse, { status: "completed" }>) => void;
  /** Called on error */
  onError?: (error: string) => void;
}

/** Contract identity for deduplication */
interface ContractKey {
  symbol: string;
  side: "call" | "put";
  strike: number;
  expiration: string;
}

function contractKey(req: ContractKey): string {
  return `${req.symbol}_${req.side}_${req.strike}_${req.expiration}`;
}

/**
 * Hook for validating a specific contract with status polling.
 * 
 * Returns:
 * - state: Current validation state
 * - validate: Function to start validation
 * - reset: Function to clear state
 */
export function useContractValidation(options?: UseContractValidationOptions) {
  const [state, setState] = useState<ValidationState>({
    validating: false,
    runId: null,
    result: null,
    error: null,
  });

  const pollTimerRef = useRef<NodeJS.Timeout | null>(null);
  const pollCountRef = useRef(0);
  const abortRef = useRef(false);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      abortRef.current = true;
      if (pollTimerRef.current) {
        clearTimeout(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, []);

  const pollStatus = useCallback(
    async (runId: string, attempt: number) => {
      if (abortRef.current) return;

      try {
        const res = await fetch(`/api/best-options/validate/${encodeURIComponent(runId)}`);
        
        if (abortRef.current) return;

        if (!res.ok) {
          if (res.status === 404) {
            setState((prev) => ({
              ...prev,
              validating: false,
              error: "Validation not found",
            }));
            return;
          }
          throw new Error(`Status check failed: ${res.status}`);
        }

        const data: ValidationStatusResponse = await res.json();

        if (data.status === "completed") {
          setState({
            validating: false,
            runId,
            result: data,
            error: null,
          });
          options?.onComplete?.(data);
        } else if (data.status === "in_progress") {
          // Bounded exponential backoff: 1s, 2s, 4s, 8s, max 10s
          const delay = Math.min(1000 * Math.pow(2, attempt), 10000);
          // Max 30 polls (~5 minutes total)
          if (attempt < 30) {
            pollTimerRef.current = setTimeout(() => {
              pollStatus(runId, attempt + 1);
            }, delay);
          } else {
            setState((prev) => ({
              ...prev,
              validating: false,
              error: "Validation timeout - please check Recent Activities",
            }));
            options?.onError?.("Validation timeout");
          }
        } else if (data.status === "not_found") {
          setState((prev) => ({
            ...prev,
            validating: false,
            error: "Validation not found",
          }));
        }
      } catch (err) {
        if (abortRef.current) return;
        const message = err instanceof Error ? err.message : "Status poll failed";
        setState((prev) => ({
          ...prev,
          validating: false,
          error: message,
        }));
        options?.onError?.(message);
      }
    },
    [options],
  );

  const validate = useCallback(
    async (request: ValidateContractRequest) => {
      // Clear any existing poll
      if (pollTimerRef.current) {
        clearTimeout(pollTimerRef.current);
        pollTimerRef.current = null;
      }
      
      abortRef.current = false;
      pollCountRef.current = 0;

      setState({
        validating: true,
        runId: null,
        result: null,
        error: null,
      });

      try {
        const res = await fetch("/api/best-options/validate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(request),
        });

        if (abortRef.current) return;

        // Handle 422 validation errors (FastAPI contract mismatch)
        if (res.status === 422) {
          const detail = await res.json().catch(() => ({ detail: "Validation error" }));
          const message = Array.isArray(detail.detail)
            ? detail.detail.map((e: any) => `${e.loc?.join(".")}: ${e.msg}`).join("; ")
            : detail.detail || "Request validation failed";
          setState({
            validating: false,
            runId: null,
            result: null,
            error: message,
          });
          options?.onError?.(message);
          return;
        }

        const data: ValidateContractResponse = await res.json();

        if (data.status === "accepted" || data.status === "duplicate") {
          const runId = data.run_id;
          setState((prev) => ({ ...prev, runId }));
          // Start polling immediately
          pollStatus(runId, 0);
        } else if (data.status === "max_concurrency") {
          setState({
            validating: false,
            runId: null,
            result: null,
            error: `Capacity limit reached. Please retry in ${data.retry_after}s.`,
          });
          options?.onError?.("max_concurrency");
        } else if (data.status === "error") {
          setState({
            validating: false,
            runId: null,
            result: null,
            error: data.message,
          });
          options?.onError?.(data.message);
        }
      } catch (err) {
        if (abortRef.current) return;
        const message = err instanceof Error ? err.message : "Validation request failed";
        setState({
          validating: false,
          runId: null,
          result: null,
          error: message,
        });
        options?.onError?.(message);
      }
    },
    [pollStatus, options],
  );

  const reset = useCallback(() => {
    abortRef.current = true;
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    setState({
      validating: false,
      runId: null,
      result: null,
      error: null,
    });
  }, []);

  return { state, validate, reset };
}
