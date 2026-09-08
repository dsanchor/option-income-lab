"use client";

import { useEffect, useState } from "react";
import { answerQuestion, listAccounts } from "@/lib/portfolio-api";
import { formatAccountName } from "@/lib/accountDisplay";
import type { ImportQuestion, ImportSession, SecurityMaster } from "@/types/import";
import type { BrokerAccount } from "@/types/portfolio";
import SecurityCreateForm from "./SecurityCreateForm";
import SecuritySearchPanel from "./SecuritySearchPanel";

interface Props {
  question: ImportQuestion;
  sessionId: string;
  onAnswered: (updated: ImportSession) => void;
}

const badgeCls: Record<string, string> = {
  BUY: "bg-accent-green/15 text-accent-green",
  SELL: "bg-accent-red/15 text-accent-red",
  DIVIDEND: "bg-accent-blue/15 text-accent-blue",
};

/** Renders a single import question card (BATCH or ENTITY scope). */
export default function ImportQuestionCard({ question, sessionId, onAnswered }: Props) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [showSearch, setShowSearch] = useState(false);
  // Cache fetched securities so repeated panel opens don't re-request.
  const [cachedSecurities, setCachedSecurities] = useState<SecurityMaster[] | null>(null);
  const [batchValue, setBatchValue] = useState(
    question.scope === "BATCH" ? question.current_value : "",
  );
  const isAccountBatch = question.scope === "BATCH" && question.batch_key === "account_id";
  const [accounts, setAccounts] = useState<BrokerAccount[]>([]);

  useEffect(() => {
    if (!isAccountBatch) return;
    let cancelled = false;
    listAccounts()
      .then((resp) => {
        if (!cancelled) setAccounts(resp.accounts ?? []);
      })
      .catch(() => {
        if (!cancelled) setAccounts([]);
      });
    return () => {
      cancelled = true;
    };
  }, [isAccountBatch]);

  // Already answered — show as a collapsed chip.
  if (question.answer !== null) {
    const summary = answerSummary(question);
    return (
      <div className="flex items-center gap-2 rounded-[var(--radius)] border border-border/50 bg-bg-card px-4 py-2 text-sm">
        <span className="text-accent-green">✓</span>
        <span className="text-text-muted truncate">{summary}</span>
      </div>
    );
  }

  async function submit(answer: {
    question_id: string;
    answer_type: string;
    selected_security_id?: string;
    batch_value?: string;
  }) {
    setError(null);
    setLoading(true);
    try {
      const updated = await answerQuestion(sessionId, answer);
      onAnswered(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to submit answer");
    } finally {
      setLoading(false);
    }
  }

  async function handleBatchSubmit(e: React.FormEvent) {
    e.preventDefault();
    await submit({
      question_id: question.question_id,
      answer_type: "BATCH_VALUE",
      batch_value: batchValue,
    });
  }

  async function handleSelect(securityId: string) {
    await submit({
      question_id: question.question_id,
      answer_type: "SELECTED_CANDIDATE",
      selected_security_id: securityId,
    });
  }

  async function handleSkip() {
    await submit({ question_id: question.question_id, answer_type: "SKIPPED_COMPANY" });
  }

  async function handleExclude() {
    await submit({ question_id: question.question_id, answer_type: "EXCLUDED_COMPANY" });
  }

  async function handleCreated(security: SecurityMaster) {
    setShowCreate(false);
    await submit({
      question_id: question.question_id,
      answer_type: "CREATED_NEW_SECURITY",
      selected_security_id: security.security_id,
    });
  }

  const inputCls =
    "rounded-[var(--radius)] border border-border bg-bg-input px-3 py-1.5 text-sm text-text placeholder:text-text-muted focus:border-accent-blue focus:outline-none";

  return (
    <div className="rounded-[var(--radius)] border border-border bg-bg-card p-4 space-y-3 animate-fade-up">
      {error && (
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-3 py-2 text-sm text-accent-red">
          {error}
        </div>
      )}

      {/* ─── BATCH question ─── */}
      {question.scope === "BATCH" && (
        <form onSubmit={handleBatchSubmit} className="space-y-3">
          <div className="text-sm font-medium text-text">
            {batchKeyLabel(question.batch_key)}
          </div>
          <div className="text-xs text-text-muted">{batchKeyHint(question.batch_key)}</div>
          <div className="flex gap-2 items-center">
            {isAccountBatch ? (
              <select
                value={batchValue}
                onChange={(e) => setBatchValue(e.target.value)}
                className={`${inputCls} w-56`}
              >
                <option value="">Sin asignar</option>
                {accounts.map((a) => (
                  <option key={a.account_id} value={a.account_id}>
                    {formatAccountName(a)}
                  </option>
                ))}
              </select>
            ) : (
              <input
                value={batchValue}
                onChange={(e) => setBatchValue(e.target.value)}
                className={`${inputCls} w-36`}
              />
            )}
            <button
              type="submit"
              disabled={loading}
              className="rounded-[var(--radius)] bg-[image:var(--grad-blue)] px-4 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              {loading ? "…" : "Confirm"}
            </button>
          </div>
        </form>
      )}

      {/* ─── ENTITY question ─── */}
      {question.scope === "ENTITY" && (
        <div className="space-y-3">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className="text-sm font-medium text-text">{question.company_name}</span>
            {question.row_count !== undefined && (
              <span className="text-xs text-text-muted">
                {question.row_count} row{question.row_count !== 1 ? "s" : ""}
              </span>
            )}
          </div>

          {/* Candidates */}
          {question.candidates.length > 0 ? (
            <div className="space-y-1.5">
              {question.candidates.map((c) => (
                <button
                  key={c.security_id}
                  type="button"
                  onClick={() => handleSelect(c.security_id)}
                  disabled={loading}
                  className="w-full flex items-center justify-between gap-3 rounded-[var(--radius)] border border-border/60 bg-bg px-4 py-2 text-left text-sm hover:bg-bg-hover hover:border-accent-blue/40 transition-colors disabled:opacity-50"
                >
                  <span>
                    <span className="font-mono font-semibold text-text mr-2">
                      {c.security_id}
                    </span>
                    <span className="text-text-muted">{c.company_name}</span>
                  </span>
                  <span
                    className={`rounded-full px-2 py-0.5 text-xs ${
                      c.score >= 0.9
                        ? "bg-accent-green/15 text-accent-green"
                        : "bg-accent-orange/15 text-accent-orange"
                    }`}
                  >
                    {(c.score * 100).toFixed(0)}%
                  </span>
                </button>
              ))}
            </div>
          ) : (
            <div className="text-sm text-text-muted">No matching securities found.</div>
          )}

          {/* Actions */}
          <div className="flex flex-wrap gap-2 pt-1">
            {!showSearch && !showCreate && (
              <button
                type="button"
                onClick={() => setShowSearch(true)}
                disabled={loading}
                className="rounded-[var(--radius)] border border-accent-blue/50 px-3 py-1.5 text-xs text-accent-blue hover:bg-accent-blue/10 disabled:opacity-50"
              >
                Find in portfolio
              </button>
            )}
            {!showCreate && !showSearch && (
              <button
                type="button"
                onClick={() => setShowCreate(true)}
                disabled={loading}
                className="rounded-[var(--radius)] border border-accent-blue/50 px-3 py-1.5 text-xs text-accent-blue hover:bg-accent-blue/10 disabled:opacity-50"
              >
                + Create new security
              </button>
            )}
            <button
              type="button"
              onClick={handleSkip}
              disabled={loading}
              className="rounded-[var(--radius)] border border-border px-3 py-1.5 text-xs text-text-muted hover:bg-bg-hover disabled:opacity-50"
            >
              Skip this company
            </button>
            <button
              type="button"
              onClick={handleExclude}
              disabled={loading}
              className="rounded-[var(--radius)] border border-accent-red/30 px-3 py-1.5 text-xs text-accent-red hover:bg-accent-red/10 disabled:opacity-50"
            >
              Exclude permanently
            </button>
          </div>

          {showSearch && (
            <SecuritySearchPanel
              prefillQuery={question.company_name}
              cachedSecurities={cachedSecurities}
              onCacheLoaded={setCachedSecurities}
              onSelect={(secId) => {
                setShowSearch(false);
                handleSelect(secId);
              }}
              onClose={() => setShowSearch(false)}
            />
          )}

          {showCreate && (
            <SecurityCreateForm
              prefillName={question.company_name}
              onCreated={handleCreated}
              onCancel={() => setShowCreate(false)}
            />
          )}
        </div>
      )}

      {/* Loading overlay */}
      {loading && (
        <div className="text-xs text-text-muted animate-pulse">Submitting…</div>
      )}
    </div>
  );
}

function answerSummary(q: ImportQuestion): string {
  if (!q.answer) return "Answered";
  const a = q.answer;
  if (a.answer_type === "BATCH_VALUE") {
    return `${q.scope === "BATCH" ? batchKeyLabel((q as { batch_key?: string }).batch_key ?? "") : "Value"}: ${a.batch_value}`;
  }
  if (a.answer_type === "SELECTED_CANDIDATE")
    return `Mapped to ${a.selected_security_id}`;
  if (a.answer_type === "CREATED_NEW_SECURITY")
    return `Created & mapped ${a.selected_security_id}`;
  if (a.answer_type === "SKIPPED_COMPANY") return "Skipped";
  if (a.answer_type === "EXCLUDED_COMPANY") return "Excluded";
  return "Answered";
}

function batchKeyLabel(key: string): string {
  const labels: Record<string, string> = {
    currency: "Batch currency",
    account_id: "Broker / account",
  };
  return labels[key] ?? key;
}

function batchKeyHint(key: string): string {
  const hints: Record<string, string> = {
    currency: "ISO 4217 currency code for amounts in this batch (default EUR).",
    account_id: "Leave blank to assign to '_unassigned'. You can reassign later.",
  };
  return hints[key] ?? "";
}

export { badgeCls };
