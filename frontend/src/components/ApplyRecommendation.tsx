"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { ActivityDoc } from "@/types/activity-detail";

type SourceAgent = "supervisor" | "alpha_advisor";

const ACTIONS = ["SELL", "ROLL_OUT", "ROLL_UP", "ROLL_DOWN", "ROLL_UP_AND_OUT", "ROLL_DOWN_AND_OUT"];

const inputCls =
  "w-full rounded-[var(--radius)] border border-border bg-bg-input px-2.5 py-1.5 text-sm text-text focus:border-accent-blue focus:outline-none";
const labelCls = "block text-xs uppercase tracking-wide text-text-muted mb-1";

function str(v: unknown): string {
  return v === null || v === undefined ? "" : String(v);
}

export default function ApplyRecommendation({
  activity,
  sourceAgent,
}: {
  activity: ActivityDoc;
  sourceAgent: SourceAgent;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  // Pre-fill values (mirrors legacy openRecommendationModal)
  const origStrike = str(activity.strike ?? activity.new_strike ?? activity.current_strike);
  const origExpiration = str(activity.expiration ?? activity.new_expiration ?? activity.current_expiration);
  const origPremium = str(activity.premium);

  let recStrike = origStrike;
  let recExpiration = origExpiration;
  let recPremium = origPremium;
  if (sourceAgent === "alpha_advisor" && activity.alpha_view?.alternative) {
    const alt = activity.alpha_view.alternative;
    if (alt.strike !== undefined && alt.strike !== null && alt.strike !== "") recStrike = str(alt.strike);
    if (alt.expiration) recExpiration = str(alt.expiration);
    if (alt.premium !== undefined && alt.premium !== null && alt.premium !== "") recPremium = str(alt.premium);
  }

  let defaultReason = "";
  if (sourceAgent === "alpha_advisor" && activity.alpha_view) {
    const av = activity.alpha_view;
    defaultReason = av.one_liner || av.alternative?.action || "";
  } else if (sourceAgent === "supervisor") {
    const sv = activity.supervisor_view ?? {};
    defaultReason = sv.one_liner || sv.net_assessment || "";
  }

  const otherAgentPresent =
    (sourceAgent === "supervisor" && !!activity.alpha_view) ||
    (sourceAgent === "alpha_advisor" && !!activity.supervisor_view);
  const otherAgentLabel = sourceAgent === "supervisor" ? "Alpha Advisor" : "Supervisor";
  const agentLabel = sourceAgent === "supervisor" ? "Supervisor" : "Alpha Advisor";

  // Controlled fields
  const [action, setAction] = useState(ACTIONS[0]);
  const [confidence, setConfidence] = useState(str(activity.confidence) || "medium");
  const [strike, setStrike] = useState(recStrike);
  const [expiration, setExpiration] = useState(recExpiration);
  const [premium, setPremium] = useState(recPremium);
  const [iv, setIv] = useState(str(activity.iv));
  const [riskRating, setRiskRating] = useState(str(activity.risk_rating));
  const [reason, setReason] = useState(defaultReason);
  const [includeOther, setIncludeOther] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    const payload = {
      source_activity_id: activity.id,
      source_agent: sourceAgent,
      activity_data: {
        activity: action,
        strike,
        expiration,
        premium,
        confidence,
        reason: reason || undefined,
        iv: iv || undefined,
        risk_rating: riskRating || undefined,
      },
      include_other_agent: includeOther,
    };
    try {
      const res = await fetch("/api/activities/from-recommendation", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setMsg({ kind: "error", text: (data as { error?: string }).error || "Failed to create activity." });
        setBusy(false);
        return;
      }
      setMsg({ kind: "ok", text: "✓ Activity created! Redirecting…" });
      const newId = (data as { id?: string }).id;
      setTimeout(() => {
        if (newId) {
          router.push(`/activities/${encodeURIComponent(newId)}`);
          router.refresh();
        } else {
          router.refresh();
        }
      }, 900);
    } catch (err) {
      setMsg({ kind: "error", text: err instanceof Error ? err.message : "Network error" });
      setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={() => {
          setMsg(null);
          setBusy(false);
          setOpen(true);
        }}
        className="rounded-[var(--radius-pill)] bg-accent-blue px-3 py-1.5 text-xs text-white hover:opacity-90"
      >
        📝 Apply Recommendation
      </button>

      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          onClick={(e) => {
            if (e.target === e.currentTarget) setOpen(false);
          }}
        >
          <div className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-[var(--radius)] border border-border bg-bg-card p-5 shadow-xl">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-lg font-semibold">Apply {agentLabel} Recommendation</h3>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="text-text-muted hover:text-text"
                aria-label="Close"
              >
                ✕
              </button>
            </div>

            <form onSubmit={submit} className="space-y-4">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                <div>
                  <label className={labelCls} htmlFor="recActivity">Action</label>
                  <select id="recActivity" className={inputCls} value={action} onChange={(e) => setAction(e.target.value)} required>
                    {ACTIONS.map((a) => (
                      <option key={a} value={a}>{a.replace(/_/g, " ")}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className={labelCls} htmlFor="recConfidence">Confidence</label>
                  <select id="recConfidence" className={inputCls} value={confidence} onChange={(e) => setConfidence(e.target.value)}>
                    <option value="high">High</option>
                    <option value="medium">Medium</option>
                    <option value="low">Low</option>
                  </select>
                </div>
                <div>
                  <label className={labelCls} htmlFor="recStrike">Strike ($)</label>
                  <input id="recStrike" type="number" step="0.5" min="0" className={inputCls} value={strike} onChange={(e) => setStrike(e.target.value)} required />
                </div>
                <div>
                  <label className={labelCls} htmlFor="recExpiration">Expiration</label>
                  <input id="recExpiration" type="date" className={inputCls} value={expiration} onChange={(e) => setExpiration(e.target.value)} required />
                </div>
                <div>
                  <label className={labelCls} htmlFor="recPremium">Premium ($)</label>
                  <input id="recPremium" type="number" step="0.01" min="0" className={inputCls} value={premium} onChange={(e) => setPremium(e.target.value)} required />
                </div>
                <div>
                  <label className={labelCls} htmlFor="recIV">IV (%)</label>
                  <input id="recIV" type="number" step="0.1" min="0" className={inputCls} value={iv} onChange={(e) => setIv(e.target.value)} />
                </div>
                <div>
                  <label className={labelCls} htmlFor="recRiskRating">Risk Rating (1-10)</label>
                  <input id="recRiskRating" type="number" step="1" min="1" max="10" className={inputCls} value={riskRating} onChange={(e) => setRiskRating(e.target.value)} />
                </div>
              </div>

              <div>
                <label className={labelCls} htmlFor="recReason">Reason</label>
                <textarea id="recReason" rows={3} className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why are you applying this recommendation?" />
              </div>

              {otherAgentPresent && (
                <label className="flex cursor-pointer items-center gap-2 text-sm text-text">
                  <input type="checkbox" checked={includeOther} onChange={(e) => setIncludeOther(e.target.checked)} />
                  <span>Include {otherAgentLabel} data in new activity</span>
                </label>
              )}

              <div className="rounded-[var(--radius)] bg-bg-input px-3 py-2 text-xs text-text-muted">
                📎 This activity will be linked to the original (<code className="font-mono">{activity.id}</code>) via {agentLabel}.
              </div>

              <div className="flex items-center justify-end gap-2">
                <button type="button" onClick={() => setOpen(false)} className="rounded-[var(--radius-pill)] border border-border bg-bg-input px-4 py-2 text-sm text-text-muted hover:bg-hover">
                  Cancel
                </button>
                <button type="submit" disabled={busy} className="rounded-[var(--radius-pill)] bg-accent-blue px-4 py-2 text-sm text-white disabled:opacity-50">
                  {busy ? "Creating…" : "Create Activity"}
                </button>
              </div>
              {msg && (
                <div
                  className={`rounded-[var(--radius)] border px-3 py-2 text-sm ${
                    msg.kind === "ok"
                      ? "border-accent-green/40 bg-accent-green/10 text-accent-green"
                      : "border-accent-red/40 bg-accent-red/10 text-accent-red"
                  }`}
                >
                  {msg.text}
                </div>
              )}
            </form>
          </div>
        </div>
      )}
    </>
  );
}
