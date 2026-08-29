"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  activityStyle,
  confidenceStyle,
  riskStyle,
  riskRatingStyle,
} from "@/lib/badges";
import type { ActivityItem } from "@/types/dashboard";
import { RuleStatusBadges } from "@/components/RuleEvaluationPanel";

const TIME_PILLS = [
  { label: "1d", days: 1 },
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
];

function parseTs(ts?: string): number {
  if (!ts) return 0;
  const t = new Date(ts.replace(" ", "T")).getTime();
  return isNaN(t) ? 0 : t;
}

function hasAlpha(item: ActivityItem): boolean {
  const s = item.alpha_view?.opportunity_strength;
  return s === "MODERATE" || s === "STRONG";
}

export default function DashboardActivity({ items }: { items: ActivityItem[] }) {
  const router = useRouter();
  const [now] = useState(() => Date.now());
  const [days, setDays] = useState(1);
  const [symbol, setSymbol] = useState("");
  const [agent, setAgent] = useState("");
  const [confidence, setConfidence] = useState("");
  const [decision, setDecision] = useState("");

  const symbols = useMemo(
    () => Array.from(new Set(items.map((i) => i.symbol).filter(Boolean))).sort() as string[],
    [items],
  );
  const agents = useMemo(() => {
    const map = new Map<string, string>();
    items.forEach((i) => {
      if (i._agent_key) map.set(i._agent_key, i._agent_label || i._agent_key);
    });
    return Array.from(map.entries()).sort((a, b) => a[1].localeCompare(b[1]));
  }, [items]);

  const filtered = useMemo(() => {
    const cutoff = now - days * 86400000;
    return items.filter((i) => {
      const ts = parseTs(i.timestamp || i.created_at);
      if (ts && ts < cutoff) return false;
      if (symbol && i.symbol !== symbol) return false;
      if (agent && i._agent_key !== agent) return false;
      if (confidence && String(i.confidence || "").toLowerCase() !== confidence) return false;
      if (decision) {
        const dec = (i.activity || "").toLowerCase();
        if (decision === "alpha" && !hasAlpha(i)) return false;
        if (decision === "sell" && dec !== "sell") return false;
        if (decision === "wait" && dec !== "wait") return false;
      }
      return true;
    });
  }, [items, days, now, symbol, agent, confidence, decision]);

  const selectCls =
    "rounded-[var(--radius)] border border-border bg-bg-input px-2.5 py-1.5 text-sm text-text focus:border-accent-blue/60 focus:outline-none";

  return (
    <section className="surface overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/70 px-4 py-3">
        <h2 className="text-base font-semibold">Recent Activity</h2>
        <div className="flex flex-wrap items-center gap-2">
          <div className="inline-flex rounded-[var(--radius-pill)] border border-border bg-bg-input p-0.5">
            {TIME_PILLS.map((p) => (
              <button
                key={p.days}
                onClick={() => setDays(p.days)}
                className={`rounded-[var(--radius-pill)] px-2.5 py-1 text-xs font-medium transition-colors ${
                  days === p.days ? "bg-accent-blue text-white" : "text-text-muted hover:text-text"
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
          <select className={selectCls} value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            <option value="">All Symbols</option>
            {symbols.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <select className={selectCls} value={agent} onChange={(e) => setAgent(e.target.value)}>
            <option value="">All Agents</option>
            {agents.map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
          <select className={selectCls} value={confidence} onChange={(e) => setConfidence(e.target.value)}>
            <option value="">All Confidence</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
          <select className={selectCls} value={decision} onChange={(e) => setDecision(e.target.value)}>
            <option value="">All Decisions</option>
            <option value="wait">Wait</option>
            <option value="sell">Sell</option>
            <option value="alpha">Alpha Executed</option>
          </select>
        </div>
      </div>

      {filtered.length === 0 ? (
        <div className="p-8 text-center text-sm text-text-muted">
          <div className="mb-1 text-2xl">💤</div>
          No activity matches the current filters.
        </div>
      ) : (
        <div className="divide-y divide-border/50">
          {filtered.map((item, idx) => (
            <ActivityRow key={item.id ?? idx} item={item} onClick={() => item.id && router.push(`/activities/${item.id}`)} />
          ))}
        </div>
      )}
    </section>
  );
}

function Pill({
  style,
  children,
  title,
}: {
  style: { color: string; bg: string; border: string };
  children: React.ReactNode;
  title?: string;
}) {
  return (
    <span
      title={title}
      className="inline-flex items-center rounded-[var(--radius-pill)] border px-2 py-0.5 text-xs font-medium"
      style={{ color: style.color, background: style.bg, borderColor: style.border }}
    >
      {children}
    </span>
  );
}

function ActivityRow({ item, onClick }: { item: ActivityItem; onClick: () => void }) {
  const act = (item.activity || "").toUpperCase();
  const sv = item.supervisor_view;
  const showSupervisor =
    act === "WAIT" && sv && (sv.challenge_strength === "MODERATE" || sv.challenge_strength === "STRONG");
  const showAlpha = act === "WAIT" && item.alpha_view;
  const isValidation = item.validation_source != null;
  const validationSourceLabel = item.validation_source === "best_options" ? "Best Options" : item.validation_source === "options_screener" ? "Screener" : null;

  return (
    <div
      onClick={onClick}
      className="flex cursor-pointer flex-wrap items-center gap-2 px-4 py-2.5 text-sm transition-colors hover:bg-bg-hover"
    >
      <span className="font-mono text-xs text-text-muted">{item.timestamp?.slice(0, 19)}</span>
      {item.is_alert && <span title="Alert">📢</span>}
      {item.data_error && <span title="Partial data — some data resources were unavailable">⚠️</span>}
      {showSupervisor && (
        <span title={`Supervisor ${sv!.challenge_strength}: ${sv!.one_liner ?? ""}`}>🤔</span>
      )}
      {showAlpha && <span title={`Alpha Advisor: ${item.alpha_view!.one_liner ?? ""}`}>🧠</span>}
      <Pill style={activityStyle(item.activity)}>{item.activity || "N/A"}</Pill>
      {validationSourceLabel && (
        <Pill
          style={{ color: "var(--accent-blue)", bg: "var(--accent-blue-bg)", border: "var(--accent-blue-border)" }}
          title={`Contract validated from ${validationSourceLabel}`}
        >
          {validationSourceLabel}
        </Pill>
      )}
      {isValidation && item.validation_status && (
        <Pill
          style={
            item.validation_status === "approved"
              ? { color: "var(--accent-green)", bg: "var(--accent-green-bg)", border: "var(--accent-green-border)" }
              : item.validation_status === "review_incomplete"
              ? { color: "var(--accent-orange)", bg: "var(--accent-orange-bg)", border: "var(--accent-orange-border)" }
              : { color: "var(--accent-red)", bg: "var(--accent-red-bg)", border: "var(--accent-red-border)" }
          }
          title={`Validation status: ${item.validation_status}`}
        >
          {item.validation_status === "approved" ? "✓ Approved" : item.validation_status === "review_incomplete" ? "⏸ Review incomplete" : "✗ Error"}
        </Pill>
      )}
      <span className="text-text-muted">{item._agent_label}</span>
      <strong>{item.symbol}</strong>
      {item.strike != null && item.strike !== "" && (
        <span className="font-mono text-text-muted">${item.strike}</span>
      )}
      {item.expiration && (
        <span className="font-mono text-xs text-text-muted">{item.expiration}</span>
      )}
      {item.confidence != null && item.confidence !== "" && (
        <Pill style={confidenceStyle(item.confidence)}>{String(item.confidence)}</Pill>
      )}
      {item.risk_rating != null && (
        <Pill style={riskRatingStyle(item.risk_rating)}>Risk {item.risk_rating}/10</Pill>
      )}
      {item.assignment_risk && (
        <Pill style={riskStyle(item.assignment_risk)}>{item.assignment_risk}</Pill>
      )}
      {item.waiting_for && (
        <Pill style={{ color: "var(--text-muted)", bg: "var(--bg-input)", border: "var(--border)" }}>
          ⏳ {item.waiting_for}
        </Pill>
      )}
      {item.rule_evaluation && <RuleStatusBadges evaluation={item.rule_evaluation} />}
    </div>
  );
}
