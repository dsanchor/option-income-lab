"use client";

import { useMemo, useState } from "react";
import type { Activity, AgentType } from "@/types/symbol-detail";

const TIME_RANGES = [
  { label: "1d", days: 1 },
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
  { label: "All", days: 0 },
];

function num(v: unknown, digits = 2): string {
  const n = typeof v === "number" ? v : typeof v === "string" ? parseFloat(v) : NaN;
  return isFinite(n) ? n.toFixed(digits) : String(v ?? "");
}

function activityClass(activity: string | undefined): string {
  const a = (activity ?? "").toUpperCase();
  if (["SELL", "ROLL", "OPEN", "BUY"].includes(a)) return "border-accent-green/40 bg-accent-green/10 text-accent-green";
  if (["CLOSE", "ASSIGN", "EXIT", "ALERT"].includes(a)) return "border-accent-red/40 bg-accent-red/10 text-accent-red";
  if (["WAIT", "HOLD"].includes(a)) return "border-border bg-bg-input text-text-muted";
  return "border-accent-blue/40 bg-accent-blue/10 text-accent-blue";
}

function confidenceClass(c: string): string {
  if (c === "high") return "border-accent-green/40 bg-accent-green/10 text-accent-green";
  if (c === "low") return "border-accent-red/40 bg-accent-red/10 text-accent-red";
  return "border-accent-orange/40 bg-accent-orange/10 text-accent-orange";
}

function riskRatingClass(r: number): string {
  if (r <= 2) return "border-accent-green/40 bg-accent-green/10 text-accent-green";
  if (r <= 4) return "border-accent-green/40 bg-accent-green/10 text-accent-green";
  if (r <= 6) return "border-accent-orange/40 bg-accent-orange/10 text-accent-orange";
  if (r <= 8) return "border-accent-orange/40 bg-accent-orange/10 text-accent-orange";
  return "border-accent-red/40 bg-accent-red/10 text-accent-red";
}

function assignmentRiskClass(risk: string): string {
  const r = risk.toLowerCase();
  if (r.includes("high")) return "border-accent-red/40 bg-accent-red/10 text-accent-red";
  if (r.includes("medium") || r.includes("moderate")) return "border-accent-orange/40 bg-accent-orange/10 text-accent-orange";
  return "border-accent-green/40 bg-accent-green/10 text-accent-green";
}

function Badge({ text, className }: { text: string; className: string }) {
  return (
    <span className={`inline-block rounded-[var(--radius-pill)] border px-2 py-0.5 text-xs ${className}`}>
      {text}
    </span>
  );
}

export default function RecentActivities({
  activities,
  agentTypes,
}: {
  activities: Activity[];
  agentTypes: AgentType[];
}) {
  const [days, setDays] = useState(1);
  const [agent, setAgent] = useState("");
  const [confidence, setConfidence] = useState("");

  const filtered = useMemo(() => {
    const now = Date.now();
    return activities.filter((d) => {
      if (days > 0 && d.timestamp) {
        const ts = new Date(String(d.timestamp).replace(" ", "T").replace("Z", "") + "Z").getTime();
        if (isFinite(ts) && (now - ts) / 86400000 > days) return false;
      }
      if (agent && (d._agent_key ?? d.agent_type) !== agent) return false;
      if (confidence && String(d.confidence ?? "") !== confidence) return false;
      return true;
    });
  }, [activities, days, agent, confidence]);

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">
          Recent Activities <span className="text-sm text-text-muted">({filtered.length})</span>
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1 rounded-[var(--radius-pill)] border border-border bg-bg-card p-1">
            {TIME_RANGES.map((t) => (
              <button
                key={t.label}
                type="button"
                onClick={() => setDays(t.days)}
                className={`rounded-[var(--radius-pill)] px-3 py-1 text-xs transition ${
                  days === t.days ? "bg-accent-blue text-white" : "text-text-muted hover:text-text"
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
          <select
            value={agent}
            onChange={(e) => setAgent(e.target.value)}
            className="rounded-[var(--radius-pill)] border border-border bg-bg-input px-3 py-1.5 text-xs text-text"
          >
            <option value="">All Agents</option>
            {agentTypes.map((a) => (
              <option key={a.key} value={a.key}>{a.label}</option>
            ))}
          </select>
          <select
            value={confidence}
            onChange={(e) => setConfidence(e.target.value)}
            className="rounded-[var(--radius-pill)] border border-border bg-bg-input px-3 py-1.5 text-xs text-text"
          >
            <option value="">All Confidence</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </div>
      </div>

      <div className="overflow-x-auto rounded-[var(--radius)] border border-border bg-bg-card">
        <table className="w-full min-w-[900px] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
              <th className="px-4 py-3 font-medium">Timestamp</th>
              <th className="px-4 py-3 font-medium">Agent</th>
              <th className="px-4 py-3 font-medium">Activity</th>
              <th className="px-4 py-3 text-right font-medium">Strike</th>
              <th className="px-4 py-3 font-medium">Expiration</th>
              <th className="px-4 py-3 text-right font-medium">Underlying</th>
              <th className="px-4 py-3 font-medium">Confidence</th>
              <th className="px-4 py-3 font-medium">Risk</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-6 text-center text-text-muted">
                  No activities match the current filters.
                </td>
              </tr>
            )}
            {filtered.map((d, i) => {
              const act = d.activity;
              const isWait = (act ?? "").toUpperCase() === "WAIT";
              const sv = d.supervisor_view;
              const showSupervisor =
                isWait && sv && ["MODERATE", "STRONG"].includes(String(sv.challenge_strength));
              const conf = String(d.confidence ?? "");
              return (
                <tr key={d.id ?? d.activity_id ?? i} className="border-b border-border/60 last:border-0">
                  <td className="px-4 py-3 font-mono text-text-muted">
                    {d.timestamp ? String(d.timestamp).slice(0, 19) : "—"}
                  </td>
                  <td className="px-4 py-3">{d._agent_label ?? d.agent_type ?? "—"}</td>
                  <td className="px-4 py-3">
                    <span className="flex items-center gap-1.5">
                      {d.is_alert && <span title="Alert">📢</span>}
                      {d.data_error && <span title="Partial data — some resources were unavailable">⚠️</span>}
                      {showSupervisor && (
                        <span title={`Supervisor ${sv!.challenge_strength}: ${sv!.one_liner ?? ""}`}>🤔</span>
                      )}
                      {isWait && d.alpha_view && (
                        <span title={`Alpha Advisor: ${d.alpha_view.one_liner ?? ""}`}>🧠</span>
                      )}
                      <Badge text={act ?? "—"} className={activityClass(act)} />
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right font-mono">
                    {d.strike != null && d.strike !== "" ? (
                      <>
                        ${num(d.strike)}
                        {d.new_strike && d.current_strike && (
                          <span className="text-text-muted"> (from ${num(d.current_strike)})</span>
                        )}
                      </>
                    ) : "—"}
                  </td>
                  <td className="px-4 py-3 font-mono">
                    {d.expiration ? (
                      <>
                        {d.expiration}
                        {d.new_expiration && d.current_expiration && (
                          <span className="text-text-muted"> (from {d.current_expiration})</span>
                        )}
                      </>
                    ) : "—"}
                  </td>
                  <td className="px-4 py-3 text-right font-mono">
                    {d.underlying_price != null && d.underlying_price !== "" ? `$${num(d.underlying_price)}` : "—"}
                  </td>
                  <td className="px-4 py-3">
                    {conf ? <Badge text={conf} className={confidenceClass(conf)} /> : "—"}
                  </td>
                  <td className="px-4 py-3">
                    {typeof d.risk_rating === "number" ? (
                      <Badge text={`${d.risk_rating}/10`} className={riskRatingClass(d.risk_rating)} />
                    ) : d.assignment_risk ? (
                      <Badge text={d.assignment_risk} className={assignmentRiskClass(d.assignment_risk)} />
                    ) : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
