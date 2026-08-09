"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import PlanModal from "@/components/PlanModal";
import type { Plan } from "@/types/plans";

function titleCase(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function priorityClass(p: string): string {
  if (p === "high") return "border-accent-red/40 bg-accent-red/10 text-accent-red";
  if (p === "low") return "border-border bg-bg-input text-text-muted";
  return "border-accent-orange/40 bg-accent-orange/10 text-accent-orange";
}

function statusClass(s: string): string {
  if (s === "completed") return "border-accent-green/40 bg-accent-green/10 text-accent-green";
  if (s === "cancelled") return "border-border bg-bg-input text-text-muted";
  if (s === "active") return "border-accent-blue/40 bg-accent-blue/10 text-accent-blue";
  return "border-accent-orange/40 bg-accent-orange/10 text-accent-orange";
}

function Badge({ text, className }: { text: string; className: string }) {
  return (
    <span className={`inline-block rounded-[var(--radius-pill)] border px-2 py-0.5 text-xs ${className}`}>
      {text}
    </span>
  );
}

export default function SymbolPlansTable({ plans }: { plans: Plan[] }) {
  const router = useRouter();
  const [modal, setModal] = useState<{ symbol: string; planId: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function setPlanStatus(plan: Plan, newStatus: string) {
    if (newStatus === "cancelled" && !window.confirm("Cancel this plan?")) return;
    setBusy(true);
    try {
      const res = await fetch(
        `/api/symbols/${encodeURIComponent(plan.symbol)}/plans/${encodeURIComponent(plan.id)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: newStatus }),
        },
      );
      if (res.ok) {
        router.refresh();
      } else {
        const body = await res.json().catch(() => ({}));
        window.alert(`Error: ${body.error || "Failed to update"}`);
      }
    } catch (e) {
      window.alert(`Network error: ${e instanceof Error ? e.message : ""}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="surface overflow-hidden">
      <div className="flex items-center justify-between px-5 pt-4">
        <h2 className="text-base font-semibold">Action Plans</h2>
        <span className="rounded-[var(--radius-pill)] bg-bg-input px-2 py-0.5 text-xs text-text-muted">
          {plans.length} {plans.length === 1 ? "plan" : "plans"}
        </span>
      </div>

      {plans.length === 0 ? (
        <p className="px-5 py-8 text-center text-sm text-text-muted">
          No action plans.{" "}
          <a href="/plans" className="text-accent-blue hover:underline">
            Create one →
          </a>
        </p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[720px] text-sm">
            <thead>
              <tr className="border-y border-border text-left text-xs uppercase tracking-wide text-text-muted">
                <th className="px-5 py-2 font-medium">Title</th>
                <th className="px-3 py-2 font-medium">Type</th>
                <th className="px-3 py-2 font-medium">Priority</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Last Note</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {plans.map((plan) => {
                const lastNote = plan.agent_notes?.length
                  ? plan.agent_notes[plan.agent_notes.length - 1]
                  : null;
                const noteText = lastNote?.note ?? "";
                return (
                  <tr key={plan.id} className="border-b border-border/60 last:border-0 hover:bg-bg-hover">
                    <td className="px-5 py-2">
                      <button
                        type="button"
                        onClick={() => setModal({ symbol: plan.symbol, planId: plan.id })}
                        className="text-left font-medium hover:text-accent-blue hover:underline"
                      >
                        {plan.title}
                      </button>
                    </td>
                    <td className="px-3 py-2">
                      <Badge text={titleCase(plan.plan_type || "")} className="border-border bg-bg-input text-text-muted" />
                    </td>
                    <td className="px-3 py-2">
                      <Badge text={titleCase(plan.priority || "")} className={priorityClass(plan.priority)} />
                    </td>
                    <td className="px-3 py-2">
                      <Badge text={titleCase(plan.status || "")} className={statusClass(plan.status)} />
                    </td>
                    <td className="max-w-[250px] px-3 py-2">
                      {noteText ? (
                        <span className="text-text-muted" title={noteText}>
                          {noteText.length > 80 ? `${noteText.slice(0, 80)}…` : noteText}
                        </span>
                      ) : (
                        <span className="text-text-muted">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right">
                      {plan.status === "planned" && (
                        <span className="inline-flex gap-1">
                          <button
                            type="button"
                            title="Complete"
                            disabled={busy}
                            onClick={() => setPlanStatus(plan, "completed")}
                            className="rounded border border-accent-green/40 bg-accent-green/10 px-2 py-0.5 text-accent-green hover:bg-accent-green/20 disabled:opacity-50"
                          >
                            ✓
                          </button>
                          <button
                            type="button"
                            title="Cancel"
                            disabled={busy}
                            onClick={() => setPlanStatus(plan, "cancelled")}
                            className="rounded border border-accent-red/40 bg-accent-red/10 px-2 py-0.5 text-accent-red hover:bg-accent-red/20 disabled:opacity-50"
                          >
                            ✕
                          </button>
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {modal && (
        <PlanModal symbol={modal.symbol} planId={modal.planId} onClose={() => setModal(null)} />
      )}
    </section>
  );
}
