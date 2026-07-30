import Link from "next/link";
import { apiFetch } from "@/lib/api";
import { usd, timeAgo } from "@/lib/format";
import SymbolActions from "@/components/SymbolActions";
import type { SymbolDetail, Position, Activity, Plan } from "@/types/symbol-detail";

export const dynamic = "force-dynamic";

async function getData(symbol: string): Promise<SymbolDetail> {
  try {
    return await apiFetch<SymbolDetail>(`/api/symbols/${encodeURIComponent(symbol)}/detail`);
  } catch (err) {
    return {
      error: err instanceof Error ? err.message : "Failed to load symbol",
    } as SymbolDetail;
  }
}

function num(n: number | null | undefined, digits = 1): string {
  return typeof n === "number" && isFinite(n) ? n.toFixed(digits) : "—";
}

function Metric({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div className="rounded-[var(--radius)] border border-border bg-bg-card px-4 py-3">
      <div className="text-xs uppercase tracking-wide text-text-muted">{label}</div>
      <div className={`mt-1 font-mono text-lg ${accent ?? ""}`}>{value}</div>
    </div>
  );
}

export default async function SymbolDetailPage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol } = await params;
  const d = await getData(symbol);

  if (d.error) {
    return (
      <div className="space-y-4">
        <Link href="/symbols" className="text-sm text-text-muted hover:text-text">← Symbols</Link>
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
          ⚠️ {d.error}
        </div>
      </div>
    );
  }

  const enr = d.enrichment ?? {};
  const price = enr.metrics?.current_price ?? null;
  const positions = d.positions ?? [];
  const activePositions = positions.filter((p) => p.status === "active");
  const activities = d.activities ?? [];
  const plans = d.plans ?? [];

  return (
    <div className="space-y-6">
      <Link href="/symbols" className="text-sm text-text-muted hover:text-text">← Symbols</Link>

      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-semibold">{d.symbol}</h1>
            {price != null && <span className="font-mono text-xl text-text-muted">${num(price, 2)}</span>}
          </div>
          {d.display_name && <p className="text-sm text-text-muted">{d.display_name}</p>}
          <div className="mt-2 flex flex-wrap gap-2 text-xs">
            {enr.category && <Badge text={enr.category} className="border-border bg-bg-input text-text-muted" />}
            {enr.entry_tag && <Badge text={enr.entry_tag} className="border-accent-blue/40 bg-accent-blue/10 text-accent-blue" />}
            {enr.momentum && <Badge text={enr.momentum} className="border-border bg-bg-input text-text-muted" />}
            {d.next_earnings_date && (
              <Badge text={`Earnings ${d.next_earnings_date}`} className="border-accent-orange/40 bg-accent-orange/10 text-accent-orange" />
            )}
          </div>
        </div>
        <SymbolActions
          symbol={d.symbol}
          covered_call={d.watchlist?.covered_call ?? false}
          cash_secured_put={d.watchlist?.cash_secured_put ?? false}
          buy_tracker={d.watchlist?.buy_tracker ?? false}
          telegram_notifications_enabled={d.telegram_notifications_enabled ?? false}
          isPaused={d.is_paused ?? false}
        />
      </div>

      {/* Key metrics */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Metric label="DGI Score" value={num(enr.quality_score)} />
        <Metric label="Tech Timing" value={num(enr.technicals?.score)} />
        <Metric label="Shares" value={d.total_shares > 0 ? String(d.total_shares) : "—"} />
        <Metric label="Active Positions" value={String(d.summary?.active_count ?? 0)} />
        <Metric label="In Calls" value={d.summary?.in_calls ? String(d.summary.in_calls) : "—"} />
        <Metric
          label="Puts Committed"
          value={d.summary?.put_exposure ? `$${usd(d.summary.put_exposure)}` : "—"}
          accent="text-accent-blue"
        />
        <Metric
          label="Calls Exposure"
          value={d.summary?.call_exposure ? `$${usd(d.summary.call_exposure)}` : "—"}
          accent="text-accent-blue"
        />
        {enr.last_updated && <Metric label="Enriched" value={timeAgo(enr.last_updated)} />}
      </div>

      {/* Positions */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Positions</h2>
        <div className="overflow-x-auto rounded-[var(--radius)] border border-border bg-bg-card">
          <table className="w-full min-w-[720px] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
                <th className="px-4 py-3 font-medium">Type</th>
                <th className="px-4 py-3 text-right font-medium">Strike</th>
                <th className="px-4 py-3 font-medium">Expiration</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 text-right font-medium">Premium</th>
                <th className="px-4 py-3 font-medium">Assignment Risk</th>
              </tr>
            </thead>
            <tbody>
              {activePositions.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-6 text-center text-text-muted">No active positions.</td>
                </tr>
              )}
              {activePositions.map((p: Position, i) => (
                <tr key={p.position_id ?? i} className="border-b border-border/60 last:border-0">
                  <td className="px-4 py-3 capitalize">{p.type ?? "—"}</td>
                  <td className="px-4 py-3 text-right font-mono">{p.strike != null ? `$${num(p.strike, 2)}` : "—"}</td>
                  <td className="px-4 py-3 font-mono">{p.expiration ?? "—"}</td>
                  <td className="px-4 py-3 capitalize">{p.status ?? "—"}</td>
                  <td className="px-4 py-3 text-right font-mono">
                    {p.display_premium != null ? `$${num(p.display_premium, 2)}` : "—"}
                  </td>
                  <td className="px-4 py-3">
                    {p.assignment_risk ? (
                      <Badge text={p.assignment_risk} className={riskClass(p.assignment_risk)} />
                    ) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Plans */}
      {plans.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-lg font-semibold">Action Plans</h2>
          <div className="space-y-2">
            {plans.map((pl: Plan, i) => (
              <div key={pl.id ?? i} className="rounded-[var(--radius)] border border-border bg-bg-card px-4 py-3">
                <div className="flex items-center justify-between gap-3">
                  <span className="font-medium">{pl.title || pl.plan_type || "Plan"}</span>
                  {pl.status && <Badge text={pl.status} className="border-border bg-bg-input text-text-muted" />}
                </div>
                {pl.objective && <p className="mt-1 text-sm text-text-muted">{pl.objective}</p>}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Recent activity */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Recent Activity</h2>
        <div className="space-y-2">
          {activities.length === 0 && <p className="text-sm text-text-muted">No recent activity.</p>}
          {activities.slice(0, 30).map((a: Activity, i) => (
            <div key={a.activity_id ?? i} className="rounded-[var(--radius)] border border-border bg-bg-card px-4 py-3">
              <div className="flex items-center justify-between gap-3 text-xs text-text-muted">
                <span className="flex items-center gap-2">
                  {a.is_alert && <span className="text-accent-orange">⚠️</span>}
                  {a._agent_label ?? a.agent_type}
                </span>
                <span>{timeAgo(a.timestamp)}</span>
              </div>
              {(a.decision || a.note || a.reason) && (
                <p className="mt-1 text-sm">{a.decision || a.note || a.reason}</p>
              )}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function riskClass(risk: string): string {
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
