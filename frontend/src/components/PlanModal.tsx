"use client";

import { useEffect, useState } from "react";
import type { Plan } from "@/types/plans";

function titleCase(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <div className="text-xs text-text-muted">{label}</div>
      <div className={mono ? "font-mono text-sm" : "text-sm"}>{value}</div>
    </div>
  );
}

function Block({ title, text }: { title: string; text: string }) {
  return (
    <div className="mt-4">
      <h4 className="mb-1 text-xs font-medium uppercase tracking-wide text-text-muted">{title}</h4>
      <div className="whitespace-pre-wrap rounded-[var(--radius)] bg-bg-input px-4 py-3 text-sm">
        {text}
      </div>
    </div>
  );
}

export default function PlanModal({
  symbol,
  planId,
  onClose,
}: {
  symbol: string;
  planId: string;
  onClose: () => void;
}) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/symbols/${encodeURIComponent(symbol)}/plans/${encodeURIComponent(planId)}`)
      .then(async (res) => {
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
        return body as Plan;
      })
      .then((p) => !cancelled && setPlan(p))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : "Failed to load plan"));
    return () => {
      cancelled = true;
    };
  }, [symbol, planId]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const notes = plan?.agent_notes ?? [];

  return (
    <div
      className="fixed inset-0 z-[200] flex items-start justify-center overflow-auto bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        className="mt-16 w-full max-w-[700px] rounded-[var(--radius)] border border-border bg-bg-card"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <h3 className="text-lg font-semibold">
            {plan ? `${plan.symbol} — ${plan.title}` : "Plan Detail"}
          </h3>
          <button type="button" onClick={onClose} className="text-xl text-text-muted hover:text-text">
            &times;
          </button>
        </div>
        <div className="px-5 py-4">
          {error && <div className="text-sm text-accent-red">⚠️ {error}</div>}
          {!plan && !error && <div className="text-sm text-text-muted">Loading…</div>}
          {plan && (
            <>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Field label="Type" value={titleCase(plan.plan_type || "")} />
                <Field label="Priority" value={titleCase(plan.priority || "")} />
                <Field label="Status" value={titleCase(plan.status || "")} />
                <Field label="Created" value={(plan.created_at || "").slice(0, 16).replace("T", " ") || "—"} mono />
              </div>

              {plan.objective && <Block title="Objective" text={plan.objective} />}
              {plan.conditions && <Block title="Conditions" text={plan.conditions} />}

              <div className="mt-5">
                <h4 className="mb-2 text-xs font-medium uppercase tracking-wide text-text-muted">
                  Agent Notes
                </h4>
                {notes.length === 0 ? (
                  <p className="text-sm text-text-muted">
                    No agent notes yet. The Plan Monitor agent will add notes on its next run.
                  </p>
                ) : (
                  <div className="space-y-2">
                    {[...notes].reverse().map((n, i) => {
                      const icon =
                        n.alert_level === "action_recommended"
                          ? "⚡ "
                          : n.alert_level === "info"
                          ? "ℹ️ "
                          : "";
                      const borderColor =
                        n.alert_level === "action_recommended"
                          ? "var(--accent-orange)"
                          : n.alert_level === "info"
                          ? "var(--accent-blue)"
                          : "var(--border)";
                      return (
                        <div
                          key={i}
                          className="rounded-[var(--radius)] bg-bg-input px-4 py-2"
                          style={{ borderLeft: `3px solid ${borderColor}` }}
                        >
                          <div className="mb-1 text-xs text-text-muted">
                            {(n.timestamp || "").slice(0, 16).replace("T", " ")}
                          </div>
                          <div className="text-sm">
                            {icon}
                            {n.note || ""}
                          </div>
                          {(n.conditions_met || n.recommended_status_change) && (
                            <div className="mt-1 flex gap-3 text-xs">
                              {n.conditions_met && (
                                <span className="text-accent-green">✓ Conditions met</span>
                              )}
                              {n.recommended_status_change && (
                                <span className="text-accent-orange">
                                  → Recommends: {n.recommended_status_change}
                                </span>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
