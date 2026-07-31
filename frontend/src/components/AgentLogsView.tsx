"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import type { AgentTracesResponse, AgentTraceRow } from "@/types/agent-traces";

const TIME_RANGES = [
  { days: 1, label: "1d" },
  { days: 7, label: "7d" },
  { days: 30, label: "30d" },
  { days: 90, label: "90d" },
];

const PURGE_OPTIONS = [
  { value: "90", label: "90 days" },
  { value: "30", label: "30 days" },
  { value: "7", label: "7 days" },
  { value: "all", label: "— all traces —" },
];

function activityClass(activity: string): string {
  const a = activity.toLowerCase();
  if (a.includes("sell") || a.includes("call") || a.includes("put")) {
    return "border-accent-green/40 bg-accent-green/10 text-accent-green";
  }
  if (a.includes("roll")) return "border-accent-orange/40 bg-accent-orange/10 text-accent-orange";
  if (a.includes("close") || a.includes("assign")) {
    return "border-accent-red/40 bg-accent-red/10 text-accent-red";
  }
  return "border-accent-blue/40 bg-accent-blue/10 text-accent-blue";
}

function Pill({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-[var(--radius-pill)] border px-3 py-1 text-xs transition-colors ${
        active
          ? "border-accent-blue/50 bg-accent-blue/15 text-accent-blue"
          : "border-border bg-bg-input text-text-muted hover:bg-hover"
      }`}
    >
      {children}
    </button>
  );
}

export default function AgentLogsView({ data }: { data: AgentTracesResponse }) {
  const agentTypes = data.agent_types ?? {};
  const agentKeys = Object.keys(agentTypes);

  const [rangeDays, setRangeDays] = useState(1);
  const [symbolFilter, setSymbolFilter] = useState("");
  const [agentFilter, setAgentFilter] = useState("");

  const [enabled, setEnabled] = useState<Record<string, boolean>>(
    () => ({ ...data.trace_enabled }),
  );
  const [savingConfig, setSavingConfig] = useState(false);
  const [configMsg, setConfigMsg] = useState<string | null>(null);

  const [purgeSel, setPurgeSel] = useState("90");
  const [purging, setPurging] = useState(false);
  const [purgeMsg, setPurgeMsg] = useState<string | null>(null);
  const [total, setTotal] = useState(data.total);

  const filtered = useMemo(() => {
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - rangeDays);
    return data.traces.filter((t) => {
      const tsStr = t.timestamp ? t.timestamp.slice(0, 19) : "";
      const ts = tsStr ? new Date(tsStr) : null;
      const timeOk = !ts || isNaN(ts.getTime()) ? true : ts >= cutoff;
      const symOk = !symbolFilter || (t.symbol ?? "") === symbolFilter;
      const agentOk = !agentFilter || (t.agent_type ?? "").trim() === agentFilter.trim();
      return timeOk && symOk && agentOk;
    });
  }, [data.traces, rangeDays, symbolFilter, agentFilter]);

  async function saveConfig() {
    setSavingConfig(true);
    setConfigMsg(null);
    try {
      const res = await fetch("/api/agent-traces/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled_types: enabled }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setConfigMsg("Saved ✓");
    } catch (e) {
      setConfigMsg(e instanceof Error ? e.message : "Failed to save");
    } finally {
      setSavingConfig(false);
      setTimeout(() => setConfigMsg(null), 3000);
    }
  }

  async function purge() {
    if (!confirm("Delete the selected traces? This cannot be undone.")) return;
    setPurging(true);
    setPurgeMsg(null);
    try {
      const res = await fetch("/api/agent-traces/purge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ older_than_days: purgeSel }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
      setPurgeMsg(`🧹 Purged ${body.deleted ?? 0} trace(s). Reload to refresh the list.`);
      setTotal((prev) => Math.max(0, prev - (body.deleted ?? 0)));
    } catch (e) {
      setPurgeMsg(e instanceof Error ? e.message : "Failed to purge");
    } finally {
      setPurging(false);
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-2xl font-semibold">🧾 Agent Logs</h1>
      </div>

      {!data.cosmos_available && (
        <div className="rounded-[var(--radius)] border border-accent-orange/40 bg-accent-orange/10 px-4 py-3 text-sm">
          CosmosDB is not available — traces cannot be shown.
        </div>
      )}

      {/* Trace capture toggles */}
      <section className="rounded-[var(--radius)] border border-border bg-bg-card">
        <div className="border-b border-border px-5 py-3">
          <h2 className="text-base font-semibold">⚙️ Trace capture</h2>
        </div>
        <div className="px-5 py-4">
          <div className="mb-4 flex flex-wrap gap-4">
            {agentKeys.map((key) => (
              <label key={key} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={!!enabled[key]}
                  onChange={(e) =>
                    setEnabled((prev) => ({ ...prev, [key]: e.target.checked }))
                  }
                  className="accent-[var(--color-accent-blue)]"
                />
                {agentTypes[key]?.label ?? key}
              </label>
            ))}
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={saveConfig}
              disabled={savingConfig}
              className="rounded-[var(--radius-pill)] border border-accent-blue/50 bg-accent-blue/15 px-4 py-1.5 text-sm text-accent-blue hover:bg-accent-blue/25 disabled:opacity-50"
            >
              {savingConfig ? "Saving…" : "Save capture settings"}
            </button>
            {configMsg && <span className="text-sm text-text-muted">{configMsg}</span>}
          </div>
        </div>
      </section>

      {/* Executions */}
      <section className="rounded-[var(--radius)] border border-border bg-bg-card">
        <div className="flex flex-col gap-3 border-b border-border px-5 py-3 md:flex-row md:items-center md:justify-between">
          <h2 className="text-base font-semibold">
            Executions{" "}
            <span className="ml-1 rounded-[var(--radius-pill)] border border-border bg-bg-input px-2 py-0.5 text-xs text-text-muted">
              {filtered.length}
            </span>{" "}
            <span className="text-sm font-normal text-text-muted">of {total} stored</span>
          </h2>
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex gap-1">
              {TIME_RANGES.map((r) => (
                <Pill
                  key={r.days}
                  active={rangeDays === r.days}
                  onClick={() => setRangeDays(r.days)}
                >
                  {r.label}
                </Pill>
              ))}
            </div>
            <select
              value={symbolFilter}
              onChange={(e) => setSymbolFilter(e.target.value)}
              className="rounded-[var(--radius)] border border-border bg-bg-input px-2 py-1 text-sm"
            >
              <option value="">All Symbols</option>
              {data.symbols.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <select
              value={agentFilter}
              onChange={(e) => setAgentFilter(e.target.value)}
              className="rounded-[var(--radius)] border border-border bg-bg-input px-2 py-1 text-sm"
            >
              <option value="">All Agents</option>
              {agentKeys.map((key) => (
                <option key={key} value={key}>
                  {agentTypes[key]?.label ?? key}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
                <th className="px-4 py-2 font-medium">Time (UTC)</th>
                <th className="px-4 py-2 font-medium">Symbol</th>
                <th className="px-4 py-2 font-medium">Agent</th>
                <th className="px-4 py-2 font-medium">Phase</th>
                <th className="px-4 py-2 font-medium">Decision</th>
                <th className="px-4 py-2 font-medium">Conf.</th>
                <th className="px-4 py-2 font-medium">Model</th>
                <th className="px-4 py-2 font-medium">Dur.</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr>
                  <td colSpan={9} className="px-4 py-6 text-center text-text-muted">
                    No traces recorded yet.
                  </td>
                </tr>
              ) : (
                filtered.map((t) => <TraceRow key={t.id} t={t} agentLabel={agentTypes[t.agent_type ?? ""]?.label ?? t.agent_type ?? ""} />)
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* Purge */}
      <section className="rounded-[var(--radius)] border border-border bg-bg-card">
        <div className="border-b border-border px-5 py-3">
          <h2 className="text-base font-semibold">🧹 Purge traces</h2>
        </div>
        <div className="flex flex-wrap items-center gap-3 px-5 py-4">
          <label className="flex items-center gap-2 text-sm">
            Delete traces older than
            <select
              value={purgeSel}
              onChange={(e) => setPurgeSel(e.target.value)}
              className="rounded-[var(--radius)] border border-border bg-bg-input px-2 py-1 text-sm"
            >
              {PURGE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={purge}
            disabled={purging}
            className="rounded-[var(--radius-pill)] border border-accent-red/50 bg-accent-red/15 px-4 py-1.5 text-sm text-accent-red hover:bg-accent-red/25 disabled:opacity-50"
          >
            {purging ? "Purging…" : "Purge"}
          </button>
          {purgeMsg && <span className="text-sm text-text-muted">{purgeMsg}</span>}
        </div>
      </section>
    </div>
  );
}

function TraceRow({ t, agentLabel }: { t: AgentTraceRow; agentLabel: string }) {
  const time = t.timestamp ? t.timestamp.slice(0, 19) : "—";
  const dur =
    t.duration_seconds !== null && t.duration_seconds !== undefined
      ? `${t.duration_seconds.toFixed(1)}s`
      : "";
  return (
    <tr className="border-b border-border/60 hover:bg-hover">
      <td className="px-4 py-2 font-mono text-xs">{time}</td>
      <td className="px-4 py-2 font-semibold">{t.symbol && t.symbol !== "_" ? t.symbol : "—"}</td>
      <td className="px-4 py-2">{agentLabel}</td>
      <td className="px-4 py-2 text-text-muted">{t.phase ?? ""}</td>
      <td className="px-4 py-2">
        <span className="flex flex-wrap items-center gap-1">
          {t.is_alert && <span title="Alert">📢</span>}
          {t.activity && (
            <span
              className={`inline-block rounded-[var(--radius-pill)] border px-2 py-0.5 text-xs ${activityClass(
                t.activity,
              )}`}
            >
              {t.activity}
            </span>
          )}
          {t.error && (
            <span
              title={t.error}
              className="inline-block rounded-[var(--radius-pill)] border border-accent-red/40 bg-accent-red/10 px-2 py-0.5 text-xs text-accent-red"
            >
              error
            </span>
          )}
        </span>
      </td>
      <td className="px-4 py-2">{t.confidence ?? ""}</td>
      <td className="px-4 py-2 font-mono text-xs">{t.model ?? ""}</td>
      <td className="px-4 py-2 font-mono text-xs">{dur}</td>
      <td className="px-4 py-2 text-right">
        <Link href={`/settings/logs/${encodeURIComponent(t.id)}`} className="text-accent-blue hover:underline">
          ›
        </Link>
      </td>
    </tr>
  );
}
