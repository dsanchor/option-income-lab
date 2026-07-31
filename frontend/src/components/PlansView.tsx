"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { PLAN_PRIORITIES, PLAN_TYPES, type Plan } from "@/types/plans";

const STATUS_FILTERS = [
  { value: "", label: "All" },
  { value: "planned", label: "Planned" },
  { value: "completed", label: "Completed" },
  { value: "cancelled", label: "Cancelled" },
];

function titleCase(s: string): string {
  return s
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
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

function PlanModal({
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

export default function PlansView({
  initialPlans,
  symbols,
}: {
  initialPlans: Plan[];
  symbols: string[];
}) {
  const [plans, setPlans] = useState<Plan[]>(initialPlans);
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [modal, setModal] = useState<{ symbol: string; planId: string } | null>(null);

  // New plan form
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState({
    symbol: "",
    plan_type: "sell_put",
    priority: "medium",
    title: "",
    objective: "",
    conditions: "",
  });
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [createOk, setCreateOk] = useState(false);

  async function refresh() {
    try {
      const res = await fetch("/api/plans");
      const body = await res.json().catch(() => []);
      if (res.ok && Array.isArray(body)) setPlans(body as Plan[]);
    } catch {
      /* keep current */
    }
  }

  async function createPlan() {
    setCreateError(null);
    setCreateOk(false);
    if (!form.symbol || !form.title.trim()) {
      setCreateError("Symbol and title are required.");
      return;
    }
    setCreating(true);
    try {
      const res = await fetch(`/api/symbols/${encodeURIComponent(form.symbol)}/plans`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: form.title.trim(),
          objective: form.objective.trim(),
          conditions: form.conditions.trim(),
          plan_type: form.plan_type,
          priority: form.priority,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setCreateError(body.error || "Failed to create plan.");
      } else {
        setCreateOk(true);
        setForm({ symbol: "", plan_type: "sell_put", priority: "medium", title: "", objective: "", conditions: "" });
        await refresh();
        setTimeout(() => setCreateOk(false), 2000);
      }
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : "Network error");
    } finally {
      setCreating(false);
    }
  }

  async function setPlanStatus(plan: Plan, newStatus: string) {
    if (newStatus === "cancelled" && !window.confirm("Cancel this plan?")) return;
    try {
      const res = await fetch(`/api/symbols/${encodeURIComponent(plan.symbol)}/plans/${encodeURIComponent(plan.id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: newStatus }),
      });
      if (res.ok) {
        await refresh();
      } else {
        const body = await res.json().catch(() => ({}));
        window.alert(`Error: ${body.error || "Failed to update"}`);
      }
    } catch (e) {
      window.alert(`Network error: ${e instanceof Error ? e.message : ""}`);
    }
  }

  const filtered = useMemo(() => {
    const term = search.trim().toUpperCase();
    return plans.filter((p) => {
      if (status && p.status !== status) return false;
      if (term && !p.symbol.toUpperCase().includes(term)) return false;
      return true;
    });
  }, [plans, status, search]);

  const field = "rounded-[var(--radius)] border border-border bg-bg-input px-3 py-1.5 text-sm text-text";

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">📋 Action Plans</h1>
        <p className="text-sm text-text-muted">
          Track your investment intentions and let the Plan Monitor agent keep you updated.
        </p>
      </div>

      {/* New plan */}
      <div className="rounded-[var(--radius)] border border-border bg-bg-card">
        <button
          type="button"
          onClick={() => setFormOpen((o) => !o)}
          className="flex w-full items-center px-4 py-3 text-left"
        >
          <h2 className="text-lg font-semibold">New Plan</h2>
          <span className="ml-auto text-xs text-text-muted">{formOpen ? "▼" : "▶"}</span>
        </button>
        {formOpen && (
          <div className="space-y-3 border-t border-border px-4 py-4">
            <div className="grid gap-3 sm:grid-cols-3">
              <label className="flex flex-col gap-1">
                <span className="text-xs text-text-muted">Symbol</span>
                <select
                  value={form.symbol}
                  onChange={(e) => setForm((f) => ({ ...f, symbol: e.target.value }))}
                  className={field}
                >
                  <option value="">Select…</option>
                  {symbols.map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs text-text-muted">Type</span>
                <select
                  value={form.plan_type}
                  onChange={(e) => setForm((f) => ({ ...f, plan_type: e.target.value }))}
                  className={field}
                >
                  {PLAN_TYPES.map((t) => (
                    <option key={t.value} value={t.value}>{t.label}</option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs text-text-muted">Priority</span>
                <select
                  value={form.priority}
                  onChange={(e) => setForm((f) => ({ ...f, priority: e.target.value }))}
                  className={field}
                >
                  {PLAN_PRIORITIES.map((p) => (
                    <option key={p.value} value={p.value}>{p.label}</option>
                  ))}
                </select>
              </label>
            </div>
            <label className="flex flex-col gap-1">
              <span className="text-xs text-text-muted">Title</span>
              <input
                type="text"
                value={form.title}
                onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
                placeholder="e.g. Sell CSP on VZ at $46 strike, Jul expiration"
                className={field}
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-xs text-text-muted">Objective</span>
              <textarea
                rows={2}
                value={form.objective}
                onChange={(e) => setForm((f) => ({ ...f, objective: e.target.value }))}
                placeholder="What you want to achieve and why…"
                className={`${field} resize-y`}
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-xs text-text-muted">
                Conditions <span className="text-text-muted">(when should the agent alert you?)</span>
              </span>
              <textarea
                rows={2}
                value={form.conditions}
                onChange={(e) => setForm((f) => ({ ...f, conditions: e.target.value }))}
                placeholder="e.g. Price below $48, RSI < 35, momentum bearish or neutral…"
                className={`${field} resize-y`}
              />
            </label>
            <button
              type="button"
              onClick={createPlan}
              disabled={creating}
              className="rounded-[var(--radius-pill)] bg-accent-blue px-4 py-1.5 text-sm text-white disabled:opacity-60"
            >
              {creating ? "Creating…" : "+ Create Plan"}
            </button>
            {createError && (
              <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-3 py-2 text-sm">
                {createError}
              </div>
            )}
            {createOk && (
              <div className="rounded-[var(--radius)] border border-accent-green/40 bg-accent-green/10 px-3 py-2 text-sm">
                Plan created successfully!
              </div>
            )}
          </div>
        )}
      </div>

      {/* Filters */}
      <div className="rounded-[var(--radius)] border border-border bg-bg-card p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-semibold">Filters</h2>
          <span className="rounded-[var(--radius-pill)] border border-border bg-bg-input px-2 py-0.5 text-xs text-text-muted">
            {status || search ? `Showing ${filtered.length} of ${plans.length}` : `${plans.length} plans`}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-1 rounded-[var(--radius-pill)] border border-border bg-bg-card p-1">
            {STATUS_FILTERS.map((s) => (
              <button
                key={s.value}
                type="button"
                onClick={() => setStatus(s.value)}
                className={`rounded-[var(--radius-pill)] px-3 py-1 text-xs transition ${
                  status === s.value ? "bg-accent-blue text-white" : "text-text-muted hover:text-text"
                }`}
              >
                {s.label}
              </button>
            ))}
          </div>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search symbol…"
            className={`${field} ml-auto w-40`}
          />
        </div>
      </div>

      {/* Plans list */}
      <div className="rounded-[var(--radius)] border border-border bg-bg-card">
        <h2 className="px-4 py-3 text-lg font-semibold">Plans</h2>
        <div className="overflow-x-auto border-t border-border">
          <table className="w-full min-w-[880px] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
                <th className="px-3 py-2 font-medium">Symbol</th>
                <th className="px-3 py-2 font-medium">Title</th>
                <th className="px-3 py-2 font-medium">Type</th>
                <th className="px-3 py-2 font-medium">Priority</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Last Note</th>
                <th className="px-3 py-2 font-medium">Updated</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-3 py-8 text-center text-text-muted">
                    {plans.length === 0
                      ? "No plans yet. Create your first action plan above."
                      : "No plans match the current filters."}
                  </td>
                </tr>
              )}
              {filtered.map((plan) => {
                const lastNote = plan.agent_notes?.length
                  ? plan.agent_notes[plan.agent_notes.length - 1]
                  : null;
                const noteText = lastNote?.note ?? "";
                return (
                  <tr key={plan.id} className="border-b border-border/60 last:border-0">
                    <td className="px-3 py-2">
                      <Link href={`/symbols/${plan.symbol}`} className="font-semibold text-accent-blue hover:underline">
                        {plan.symbol}
                      </Link>
                    </td>
                    <td className="px-3 py-2">
                      <button
                        type="button"
                        onClick={() => setModal({ symbol: plan.symbol, planId: plan.id })}
                        className="text-left hover:underline"
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
                    <td className="px-3 py-2 font-mono text-text-muted">
                      {plan.updated_at ? plan.updated_at.slice(0, 10) : "—"}
                    </td>
                    <td className="px-3 py-2 text-right">
                      {plan.status === "planned" && (
                        <span className="inline-flex gap-1">
                          <button
                            type="button"
                            title="Complete"
                            onClick={() => setPlanStatus(plan, "completed")}
                            className="rounded border border-accent-green/40 bg-accent-green/10 px-2 py-0.5 text-accent-green hover:bg-accent-green/20"
                          >
                            ✓
                          </button>
                          <button
                            type="button"
                            title="Cancel"
                            onClick={() => setPlanStatus(plan, "cancelled")}
                            className="rounded border border-accent-red/40 bg-accent-red/10 px-2 py-0.5 text-accent-red hover:bg-accent-red/20"
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
      </div>

      {modal && (
        <PlanModal symbol={modal.symbol} planId={modal.planId} onClose={() => setModal(null)} />
      )}
    </div>
  );
}
