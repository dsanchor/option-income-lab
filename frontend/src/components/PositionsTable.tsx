"use client";

import { useMemo, useState } from "react";
import type { Position } from "@/types/symbol-detail";

const STATUS_FILTERS = [
  { label: "All", value: "" },
  { label: "Active", value: "active" },
  { label: "Rolled", value: "rolled" },
  { label: "Closed", value: "closed" },
];

const DATE_FILTERS = [
  { label: "All", days: 0 },
  { label: "15d", days: 15 },
  { label: "1m", days: 30 },
  { label: "1y", days: 365 },
];

function num(v: number | null | undefined, digits = 2): string {
  return typeof v === "number" && isFinite(v) ? v.toFixed(digits) : "—";
}

function statusClass(status: string | undefined): string {
  if (status === "active") return "border-accent-green/40 bg-accent-green/10 text-accent-green";
  if (status === "rolled") return "border-accent-orange/40 bg-accent-orange/10 text-accent-orange";
  return "border-border bg-bg-input text-text-muted";
}

function riskClass(risk: string): string {
  const r = risk.toLowerCase();
  if (r.includes("high")) return "border-accent-red/40 bg-accent-red/10 text-accent-red";
  if (r.includes("medium") || r.includes("moderate")) return "border-accent-orange/40 bg-accent-orange/10 text-accent-orange";
  return "border-accent-green/40 bg-accent-green/10 text-accent-green";
}

function moneynessClass(m: string): string {
  const s = m.toLowerCase();
  if (s.includes("itm") || s.includes("in the money")) return "border-accent-red/40 bg-accent-red/10 text-accent-red";
  if (s.includes("atm") || s.includes("at the money")) return "border-accent-orange/40 bg-accent-orange/10 text-accent-orange";
  return "border-accent-green/40 bg-accent-green/10 text-accent-green";
}

function Badge({ text, className }: { text: string; className: string }) {
  return (
    <span className={`inline-block rounded-[var(--radius-pill)] border px-2 py-0.5 text-xs ${className}`}>
      {text}
    </span>
  );
}

export default function PositionsTable({ positions }: { positions: Position[] }) {
  const [status, setStatus] = useState("");
  const [days, setDays] = useState(0);

  // Newest first (mirrors the legacy `| reverse`).
  const ordered = useMemo(() => [...positions].reverse(), [positions]);

  const filtered = useMemo(() => {
    const now = Date.now();
    return ordered.filter((p) => {
      if (status && p.status !== status) return false;
      if (days > 0 && p.opened_at) {
        const ts = new Date(String(p.opened_at).slice(0, 10)).getTime();
        if (isFinite(ts) && (now - ts) / 86400000 > days) return false;
      }
      return true;
    });
  }, [ordered, status, days]);

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">
          Positions <span className="text-sm text-text-muted">({filtered.length} of {positions.length})</span>
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1 rounded-[var(--radius-pill)] border border-border bg-bg-card p-1">
            {STATUS_FILTERS.map((s) => (
              <button
                key={s.label}
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
          <div className="flex items-center gap-1 rounded-[var(--radius-pill)] border border-border bg-bg-card p-1">
            {DATE_FILTERS.map((dt) => (
              <button
                key={dt.label}
                type="button"
                onClick={() => setDays(dt.days)}
                className={`rounded-[var(--radius-pill)] px-3 py-1 text-xs transition ${
                  days === dt.days ? "bg-accent-blue text-white" : "text-text-muted hover:text-text"
                }`}
              >
                {dt.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="overflow-x-auto rounded-[var(--radius)] border border-border bg-bg-card">
        <table className="w-full min-w-[860px] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
              <th className="px-4 py-3 font-medium">Type</th>
              <th className="px-4 py-3 text-right font-medium">Strike</th>
              <th className="px-4 py-3 font-medium">Expiration</th>
              <th className="px-4 py-3 font-medium">Risk</th>
              <th className="px-4 py-3 font-medium">Moneyness</th>
              <th className="px-4 py-3 font-medium">Status</th>
              <th className="px-4 py-3 font-medium">Opened</th>
              <th className="px-4 py-3 text-right font-medium">Premium</th>
              <th className="px-4 py-3 font-medium">Notes</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr>
                <td colSpan={9} className="px-4 py-6 text-center text-text-muted">
                  No positions match the current filters.
                </td>
              </tr>
            )}
            {filtered.map((p, i) => (
              <tr key={p.position_id ?? i} className={`border-b border-border/60 last:border-0 ${p.status !== "active" ? "text-text-muted" : ""}`}>
                <td className="px-4 py-3">
                  <Badge
                    text={(p.type ?? "—").toUpperCase()}
                    className={p.type === "call" ? "border-accent-green/40 bg-accent-green/10 text-accent-green" : "border-accent-blue/40 bg-accent-blue/10 text-accent-blue"}
                  />
                </td>
                <td className="px-4 py-3 text-right font-mono">{p.strike != null ? `$${num(p.strike)}` : "—"}</td>
                <td className="px-4 py-3 font-mono">{p.expiration ?? "—"}</td>
                <td className="px-4 py-3">
                  {p.assignment_risk ? <Badge text={p.assignment_risk} className={riskClass(p.assignment_risk)} /> : "—"}
                </td>
                <td className="px-4 py-3">
                  {p.moneyness ? <Badge text={p.moneyness} className={moneynessClass(p.moneyness)} /> : "—"}
                </td>
                <td className="px-4 py-3">
                  <span className="flex flex-wrap items-center gap-1">
                    <Badge text={p.status ?? "—"} className={statusClass(p.status)} />
                    {p.status === "closed" && p.close_reason && (
                      <Badge
                        text={p.close_reason}
                        className={p.close_reason === "assigned" ? "border-accent-red/40 bg-accent-red/10 text-accent-red" : "border-border bg-bg-input text-text-muted"}
                      />
                    )}
                  </span>
                </td>
                <td className="px-4 py-3 font-mono">{p.opened_at ? String(p.opened_at).slice(0, 10) : "—"}</td>
                <td className="px-4 py-3 text-right font-mono">
                  {p.display_premium != null ? `$${num(p.display_premium)}` : "—"}
                </td>
                <td className="px-4 py-3 max-w-[200px] truncate" title={p.notes ?? ""}>{p.notes || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
