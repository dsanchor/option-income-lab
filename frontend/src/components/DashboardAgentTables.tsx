"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { timeAgo } from "@/lib/format";
import {
  activityStyle,
  moneynessStyle,
  riskStyle,
  styleFor,
} from "@/lib/badges";
import TriggerButton from "@/components/TriggerButton";
import type { AgentTable, AgentRow, RecentActivityRef } from "@/types/dashboard";

function Badge({
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

function money(n?: number | null): string {
  return n != null ? `$${n.toFixed(2)}` : "—";
}

function GapCell({ row }: { row: AgentRow }) {
  if (row.paused || row.strike_pct == null) return <>—</>;
  const isCall = row.option_type === "call";
  const positive = row.strike_pct > 0;
  // For calls, above strike (positive) is bad (red); for puts it is good (green).
  const color =
    (isCall && positive) || (!isCall && !positive)
      ? "var(--accent-red)"
      : "var(--accent-green)";
  return <span style={{ color }}>{`${positive ? "+" : ""}${row.strike_pct.toFixed(1)}%`}</span>;
}

function RecentCell({
  items,
  paused,
  recommendationSource
}: {
  items?: RecentActivityRef[];
  paused?: boolean;
  recommendationSource?: "agent" | "alpha" | null;
}) {
  if (paused || !items || items.length === 0) return <>—</>;

  // When recommendation_source is "alpha", display SELL + ALPHA badges
  const isAlphaRec = recommendationSource === "alpha";

  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {isAlphaRec ? (
        <span className="inline-flex items-center gap-1">
          <Link
            href={`/activities/${items[0]?.id}`}
            onClick={(e) => e.stopPropagation()}
            title={items[0]?.reason || items[0]?.timestamp?.slice(0, 16)}
          >
            <Badge style={activityStyle("SELL")}>SELL</Badge>
          </Link>
          <Badge style={styleFor("purple")}>ALPHA</Badge>
        </span>
      ) : (
        items.map((act, i) => (
          <span key={i} className="inline-flex items-center gap-1">
            <Link
              href={`/activities/${act.id}`}
              onClick={(e) => e.stopPropagation()}
              title={act.reason || act.timestamp?.slice(0, 16)}
            >
              <Badge style={activityStyle(act.activity)}>{act.activity || "N/A"}</Badge>
            </Link>
          </span>
        ))
      )}
    </span>
  );
}

function DpsCell({ row }: { row: AgentRow }) {
  if (row.dps_score == null) return <>—</>;
  const color =
    row.dps_score >= 70
      ? "var(--accent-green)"
      : row.dps_score >= 50
        ? "var(--accent-orange)"
        : "var(--accent-red)";
  const delta = (v: number | null | undefined, title: string, prefix = "") =>
    v == null ? null : (
      <span
        title={title}
        style={{
          color: v > 0 ? "var(--accent-green)" : v < 0 ? "var(--accent-red)" : "var(--text-muted)",
        }}
      >
        {prefix}
        {v > 0 ? `+${v}` : v}
      </span>
    );
  return (
    <span className="whitespace-nowrap">
      <strong style={{ color }}>{row.dps_score}</strong>
      <span className="ml-1 text-[0.72rem] text-text-muted">
        {delta(row.dps_delta_1d, "1-day change")}
        {delta(row.dps_delta_7d, "7-day change", "/")}
      </span>
    </span>
  );
}

const TH = "px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-text-muted";
const THNUM = `${TH} text-right`;
const TD = "px-3 py-2 text-sm align-middle";
const TDNUM = `${TD} text-right font-mono`;

export default function DashboardAgentTables({ tables }: { tables: AgentTable[] }) {
  const router = useRouter();

  return (
    <div className="space-y-5">
      {tables.map((agent) => {
        const isPM = agent.is_position_monitor;
        const isBuy = agent.key === "buy_tracker";
        return (
          <section key={agent.key} className="surface overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/70 px-4 py-3">
              <h2 className="flex items-center gap-2 text-base font-semibold">
                {agent.label}
                {agent.last_update_ts && (
                  <span className="rounded-[var(--radius-pill)] bg-bg-input px-2 py-0.5 text-xs font-normal text-text-muted">
                    last update {timeAgo(agent.last_update_ts)}
                  </span>
                )}
              </h2>
              <TriggerButton agent={agent.key} />
            </div>

            {agent.rows.length === 0 ? (
              <p className="px-4 py-8 text-center text-sm text-text-muted">No alerts recorded yet</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[640px] border-collapse">
                  <thead>
                    <tr className="border-b border-border/70">
                      <th className={TH}>{isPM ? "Position" : "Symbol"}</th>
                      <th className={THNUM}>Price</th>
                      {!isBuy && <th className={THNUM}>Gap</th>}
                      <th className={TH}>Recent</th>
                      {isPM ? (
                        <>
                          <th className={THNUM}>DTE</th>
                          <th className={TH}>Moneyness</th>
                          <th className={TH}>Risk</th>
                          <th className={THNUM}>Delta</th>
                          <th className={THNUM}>P&amp;L</th>
                          <th className={THNUM}>DPS</th>
                        </>
                      ) : isBuy ? (
                        <>
                          <th className={TH}>Entry Zone</th>
                          <th className={TH}>Triggers</th>
                        </>
                      ) : (
                        <>
                          <th className={THNUM}>Strike</th>
                          <th className={TH}>Expiry</th>
                          <th className={THNUM}>Premium</th>
                        </>
                      )}
                      <th className={TH} />
                    </tr>
                  </thead>
                  <tbody>
                    {agent.rows.map((row) => (
                      <tr
                        key={row.key}
                        onClick={() => router.push(`/symbols/${row.symbol}`)}
                        className={`cursor-pointer border-b border-border/40 transition-colors hover:bg-bg-hover ${
                          row.paused ? "opacity-50" : ""
                        }`}
                      >
                        <td className={TD}>
                          <strong>{row.display}</strong>
                          {row.paused && (
                            <span className="ml-2 rounded-[var(--radius-pill)] bg-bg-input px-1.5 py-0.5 text-xs text-text-muted">
                              ⏸ {row.paused_until}
                            </span>
                          )}
                        </td>
                        <td className={TDNUM}>{row.paused ? "—" : money(row.underlying_price)}</td>
                        {!isBuy && (
                          <td className={TDNUM}>
                            <GapCell row={row} />
                          </td>
                        )}
                        <td className={TD}>
                          <RecentCell
                            items={row.recent_activities}
                            paused={row.paused}
                            recommendationSource={row.recommendation_source}
                          />
                        </td>
                        {isPM ? (
                          <>
                            <td className={TDNUM}>{row.dte ?? "—"}</td>
                            <td className={TD}>
                              {row.moneyness ? (
                                <Badge style={moneynessStyle(row.moneyness)}>{row.moneyness}</Badge>
                              ) : (
                                "—"
                              )}
                            </td>
                            <td className={TD}>
                              {row.assignment_risk ? (
                                <Badge
                                  style={riskStyle(row.assignment_risk)}
                                  title={(row.risk_flags || []).join(", ")}
                                >
                                  {row.assignment_risk}
                                </Badge>
                              ) : (
                                "—"
                              )}
                            </td>
                            <td className={TDNUM}>{row.delta != null ? row.delta.toFixed(2) : "—"}</td>
                            <td className={TDNUM}>
                              {row.pnl_pct != null ? (
                                <span
                                  style={{
                                    color: row.pnl_pct >= 0 ? "var(--accent-green)" : "var(--accent-red)",
                                  }}
                                >
                                  {row.pnl_pct >= 0 ? "+" : ""}
                                  {row.pnl_pct.toFixed(1)}%
                                </span>
                              ) : (
                                "—"
                              )}
                            </td>
                            <td className={TDNUM}>
                              <DpsCell row={row} />
                            </td>
                          </>
                        ) : isBuy ? (
                          <>
                            <td className={TD}>{row.paused ? "—" : row.entry_zone || "—"}</td>
                            <td className={TD}>
                              <span className="inline-flex flex-wrap gap-1">
                                {!row.paused &&
                                  (row.technical_triggers || []).slice(0, 3).map((t, i) => (
                                    <Badge key={i} style={styleFor("cyan")}>
                                      {t}
                                    </Badge>
                                  ))}
                              </span>
                            </td>
                          </>
                        ) : (
                          <>
                            <td className={TDNUM}>{row.paused ? "—" : row.strike ?? "—"}</td>
                            <td className={TD}>{row.paused ? "—" : row.expiration || "—"}</td>
                            <td className={TDNUM}>{row.paused ? "—" : money(row.premium)}</td>
                          </>
                        )}
                        <td className={TD}>
                          <TriggerButton agent={agent.key} symbol={row.symbol} compact />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
