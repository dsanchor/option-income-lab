import type { CachePeriodStats, TelemetryStats } from "@/types/settings";

const AGENT_ORDER = [
  "covered_call",
  "cash_secured_put",
  "buy_tracker",
  "open_call_monitor",
  "open_put_monitor",
];

function titleize(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function cell(stats: CachePeriodStats | undefined): string {
  if (!stats || (stats.count ?? 0) <= 0) return "—";
  return `${stats.avg_duration}s (${stats.count}×)`;
}

const thCls = "px-3 py-2 text-left text-xs font-semibold uppercase text-text-muted";
const tdCls = "px-3 py-2 text-sm";

/** Agent Run Stats card — per-agent run counts & average durations by period. */
export default function AgentRunStats({ agentRun }: { agentRun: TelemetryStats["agent_run"] }) {
  const runs = agentRun ?? {};
  return (
    <div className="rounded-[var(--radius)] border border-border bg-bg-card p-5">
      <h2 className="mb-4 text-lg font-semibold">🤖 Agent Run Stats</h2>
      {Object.keys(runs).length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-border">
                <th className={thCls}>Agent</th>
                <th className={thCls}>Today</th>
                <th className={thCls}>7 Days</th>
                <th className={thCls}>30 Days</th>
              </tr>
            </thead>
            <tbody>
              {AGENT_ORDER.filter((a) => a in runs).map((agent) => {
                const ag = runs[agent];
                return (
                  <tr key={agent} className="border-b border-border/50">
                    <td className={tdCls}>{titleize(agent)}</td>
                    <td className={`${tdCls} font-mono`}>{cell(ag.today)}</td>
                    <td className={`${tdCls} font-mono`}>{cell(ag["7d"])}</td>
                    <td className={`${tdCls} font-mono`}>{cell(ag["30d"])}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="text-sm text-text-muted">
          No agent run data yet. Stats will appear after agent runs.
        </div>
      )}
    </div>
  );
}
