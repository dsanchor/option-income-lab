/**
 * Reusable per-row contract validation action button.
 * 
 * Used by BestOptionsView and OptionsScreenerView to trigger exact-contract
 * validation. Each row maintains independent state (validating/result/error).
 */

"use client";

import { useState } from "react";
import { useContractValidation } from "@/lib/useContractValidation";
import type { ValidateContractRequest } from "@/types/contract-validation";

interface ContractValidationActionProps {
  symbol: string;
  side: "call" | "put";
  strike: number;
  expiration: string;
  source: "best_options" | "options_screener";
  displayedSnapshot?: Record<string, unknown>;
  /** Accessible label for the validation action */
  label?: string;
  /** Compact mode (icon only) */
  compact?: boolean;
  /** Callback when validation completes */
  onComplete?: () => void;
}

export default function ContractValidationAction({
  symbol,
  side,
  strike,
  expiration,
  source,
  displayedSnapshot,
  label,
  compact = false,
  onComplete,
}: ContractValidationActionProps) {
  const [showResult, setShowResult] = useState(false);

  const { state, validate, reset } = useContractValidation({
    onComplete: (result) => {
      setShowResult(true);
      setTimeout(() => {
        setShowResult(false);
        reset();
      }, 5000);
      onComplete?.();
    },
    onError: () => {
      setShowResult(true);
      setTimeout(() => {
        setShowResult(false);
      }, 8000);
    },
  });

  const handleValidate = () => {
    const request: ValidateContractRequest = {
      symbol,
      side,
      strike,
      expiration,
      source,
      displayed_snapshot: displayedSnapshot || null,
    };
    validate(request);
  };

  const actionLabel = label || (side === "call" ? "Validate covered call" : "Validate cash-secured put");
  const isValidating = state.validating;
  const hasResult = state.result != null;
  const hasError = state.error != null;

  // Compact icon-only button
  if (compact) {
    return (
      <div className="relative">
        <button
          type="button"
          onClick={handleValidate}
          disabled={isValidating}
          aria-label={actionLabel}
          title={isValidating ? "Validating..." : actionLabel}
          className="rounded border border-border bg-bg-input px-1.5 py-0.5 text-[10px] text-text-muted hover:bg-bg-hover disabled:opacity-50"
        >
          {isValidating ? "⏳" : "🔍"}
        </button>
        {showResult && hasResult && state.result!.status === "completed" && (
          <div className="absolute right-0 top-full z-10 mt-1 whitespace-nowrap rounded border border-accent-green/40 bg-accent-green/10 px-2 py-1 text-[10px] text-accent-green shadow-lg">
            {state.result!.activity === "SELL" ? "✓ SELL" : state.result!.activity === "WAIT" ? "⏸ WAIT" : state.result!.activity}
          </div>
        )}
        {showResult && hasError && (
          <div className="absolute right-0 top-full z-10 mt-1 max-w-[200px] whitespace-normal rounded border border-accent-red/40 bg-accent-red/10 px-2 py-1 text-[10px] text-accent-red shadow-lg">
            {state.error}
          </div>
        )}
      </div>
    );
  }

  // Full button with text
  return (
    <div className="space-y-1">
      <button
        type="button"
        onClick={handleValidate}
        disabled={isValidating}
        aria-label={actionLabel}
        className="rounded-[var(--radius-pill)] border border-accent-blue/50 bg-accent-blue/10 px-2 py-1 text-xs text-accent-blue hover:bg-accent-blue/20 disabled:opacity-50"
      >
        {isValidating ? "Validating..." : "Validate"}
      </button>
      {showResult && hasResult && state.result!.status === "completed" && (
        <div className="rounded border border-accent-green/40 bg-accent-green/10 px-2 py-1 text-xs text-accent-green">
          {state.result!.activity === "SELL" && state.result!.validation_status === "approved" && "✓ SELL — check Recent Activities"}
          {state.result!.activity === "SELL" && state.result!.validation_status !== "approved" && `⏸ SELL (${state.result!.validation_status})`}
          {state.result!.activity === "WAIT" && `⏸ WAIT — ${state.result!.note?.slice(0, 40) || "see Recent Activities"}`}
          {state.result!.activity !== "SELL" && state.result!.activity !== "WAIT" && state.result!.activity}
        </div>
      )}
      {showResult && hasError && (
        <div className="max-w-[200px] rounded border border-accent-red/40 bg-accent-red/10 px-2 py-1 text-xs text-accent-red">
          {state.error}
        </div>
      )}
    </div>
  );
}
