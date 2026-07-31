import type {
  SettingsRuntime,
  CachePeriodStats,
} from "@/types/settings";

const AGENT_ORDER = [
  "covered_call",
  "cash_secured_put",
  "buy_tracker",
  "open_call_monitor",
  "open_put_monitor",
];

const RESOURCE_ORDER = ["overview", "technicals", "forecast", "dividends", "options_chain"];

function titleize(s: string): string {
  return s
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function cell(stats: CachePeriodStats | undefined): string {
  if (!stats || (stats.count ?? 0) <= 0) return "—";
  return `${stats.avg_duration}s (${stats.count}×)`;
}

const thCls = "px-3 py-2 text-left text-xs font-semibold uppercase text-text-muted";
const tdCls = "px-3 py-2 text-sm";
const cardCls = "rounded-[var(--radius)] border border-border bg-bg-card p-5";

export default function SettingsRuntimeView({ data }: { data: SettingsRuntime }) {
  const cache = data.cache_stats ?? {};
  const cacheEntries = cache.entries ?? {};
  const telemetry = data.telemetry_stats ?? {};
  const agentRun = telemetry.agent_run ?? {};
  const tvFetch = telemetry.tv_fetch ?? {};
  const recentErrors = data.recent_errors ?? [];

  return (
    <div className="space-y-6">
      {/* Options Chain Cache */}
      <div className={cardCls}>
        <h2 className="mb-4 text-lg font-semibold">🗄️ Options Chain Cache</h2>
        {cache.entries_count !== undefined ? (
          <>
            <div className="mb-4 flex flex-wrap gap-8">
              <div>
                <span className="text-xs uppercase text-text-muted">TTL</span>
                <div className="font-mono text-lg">
                  {Math.round((cache.ttl_seconds ?? 0) / 60)}m
                </div>
              </div>
              <div>
                <span className="text-xs uppercase text-text-muted">Cached Symbols</span>
                <div className="font-mono text-lg">{cache.entries_count}</div>
              </div>
            </div>
            {Object.keys(cacheEntries).length > 0 && (
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-border">
                      <th className={thCls}>Symbol</th>
                      <th className={thCls}>Age</th>
                      <th className={thCls}>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(cacheEntries).map(([sym, info]) => (
                      <tr key={sym} className="border-b border-border/50">
                        <td className={`${tdCls} font-semibold`}>{sym}</td>
                        <td className={`${tdCls} font-mono`}>
                          {(info.age_seconds / 60).toFixed(1)}m
                        </td>
                        <td className={tdCls}>
                          <span
                            className={`rounded-[var(--radius-pill)] border px-2 py-0.5 text-xs ${
                              info.expired
                                ? "border-accent-red/40 bg-accent-red/10 text-accent-red"
                                : "border-accent-green/40 bg-accent-green/10 text-accent-green"
                            }`}
                          >
                            {info.expired ? "Expired" : "Fresh"}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        ) : (
          <div className="text-sm text-text-muted">
            Cache empty — entries will appear after first options chain request.
          </div>
        )}
      </div>

      {/* Agent Run Stats */}
      <div className={cardCls}>
        <h2 className="mb-4 text-lg font-semibold">🤖 Agent Run Stats</h2>
        {Object.keys(agentRun).length > 0 ? (
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
                {AGENT_ORDER.filter((a) => a in agentRun).map((agent) => {
                  const ag = agentRun[agent];
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

      {/* Data Fetch Stats */}
      <div className={cardCls}>
        <h2 className="mb-4 text-lg font-semibold">📡 Data Fetch Stats</h2>
        {Object.keys(tvFetch).length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-border">
                  <th className={thCls}>Resource</th>
                  <th className={thCls}>Today</th>
                  <th className={thCls}>7 Days</th>
                  <th className={thCls}>30 Days</th>
                  <th className={thCls}>Success Rate</th>
                </tr>
              </thead>
              <tbody>
                {RESOURCE_ORDER.filter((r) => r in tvFetch).map((resource) => {
                  const rs = tvFetch[resource];
                  const total = rs["30d"]?.count ?? 0;
                  const errors = rs["30d"]?.error_count ?? 0;
                  const rate = total > 0 ? ((total - errors) / total) * 100 : null;
                  const rateColor =
                    rate === null
                      ? ""
                      : rate >= 95
                        ? "text-accent-green"
                        : rate >= 80
                          ? "text-accent-orange"
                          : "text-accent-red";
                  return (
                    <tr key={resource} className="border-b border-border/50">
                      <td className={tdCls}>{titleize(resource)}</td>
                      <td className={`${tdCls} font-mono`}>{cell(rs.today)}</td>
                      <td className={`${tdCls} font-mono`}>{cell(rs["7d"])}</td>
                      <td className={`${tdCls} font-mono`}>{cell(rs["30d"])}</td>
                      <td className={`${tdCls} font-mono ${rateColor}`}>
                        {rate === null ? "—" : `${rate.toFixed(1)}%`}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="text-sm text-text-muted">
            No fetch data yet. Stats will appear after data fetches.
          </div>
        )}
      </div>

      {/* Recent Fetch Errors */}
      {recentErrors.length > 0 && (
        <div className={cardCls}>
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold">⚠️ Recent Fetch Errors</h2>
            <span className="rounded-[var(--radius-pill)] border border-border bg-bg-input px-3 py-1 text-xs text-text-muted">
              Last {recentErrors.length}
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-border">
                  <th className={thCls}>Time</th>
                  <th className={thCls}>Symbol</th>
                  <th className={thCls}>Resource</th>
                  <th className={thCls}>Duration</th>
                </tr>
              </thead>
              <tbody>
                {recentErrors.map((err, i) => (
                  <tr key={i} className="border-b border-border/50">
                    <td className={`${tdCls} font-mono`}>
                      {err.timestamp.slice(0, 16).replace("T", " ")}
                    </td>
                    <td className={`${tdCls} font-semibold`}>{err.symbol}</td>
                    <td className={tdCls}>{titleize(err.resource)}</td>
                    <td className={`${tdCls} font-mono`}>{err.duration_seconds.toFixed(1)}s</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
