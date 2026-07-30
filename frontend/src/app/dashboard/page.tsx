import Link from "next/link";
import { apiFetch } from "@/lib/api";
import { usd, pct, timeAgo } from "@/lib/format";
import type { DashboardData, ActivityItem, BannerItem } from "@/types/dashboard";

export const dynamic = "force-dynamic";

async function getData(): Promise<DashboardData> {
  try {
    return await apiFetch<DashboardData>("/api/dashboard");
  } catch (err) {
    return { error: err instanceof Error ? err.message : "Failed to load dashboard" };
  }
}

export default async function DashboardPage() {
  const d = await getData();

  if (d.error) {
    return (
      <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
        ⚠️ {d.error}
      </div>
    );
  }

  const roc = d.open_roc_annualized ?? 0;

  return (
    <div className="space-y-8">
      {d.banner_items && d.banner_items.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {d.banner_items.map((b: BannerItem, i) => (
            <div
              key={i}
              className="flex items-center gap-2 rounded-[var(--radius-pill)] border border-border bg-bg-card px-3 py-1.5 text-sm"
              title={[b.category, b.symbol].filter(Boolean).join(" · ")}
            >
              {b.emoji && <span>{b.emoji}</span>}
              {b.symbol && <span className="font-mono font-semibold">{b.symbol}</span>}
              {b.text && <span className="text-text-muted">{b.text}</span>}
            </div>
          ))}
        </div>
      )}

      {/* Summary cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Card label="Calls Exposure" value={`$${usd(d.total_call_exposure)}`} accent="text-accent-blue" />
        <Card label="Puts Committed" value={`$${usd(d.total_put_exposure)}`} accent="text-accent-blue" />
        <Card
          label="Avg RoC · annualized (open)"
          value={pct(roc)}
          accent={roc >= 0 ? "text-accent-green" : "text-accent-red"}
        />
      </div>

      {/* Secondary counters */}
      <div className="grid gap-4 sm:grid-cols-3">
        <Card label="Symbols" value={String(d.symbol_count ?? 0)} />
        <Card label="Active Positions" value={String(d.position_count ?? 0)} />
        <Card
          label="Market"
          value={d.market_open ? "Open" : "Closed"}
          accent={d.market_open ? "text-accent-green" : "text-text-muted"}
        />
      </div>

      {/* Recent activity */}
      <section>
        <h2 className="mb-3 text-lg font-medium">Recent Activity</h2>
        {d.activity && d.activity.length > 0 ? (
          <div className="divide-y divide-border overflow-hidden rounded-[var(--radius-card)] border border-border bg-bg-card">
            {d.activity.slice(0, 20).map((a: ActivityItem, i) => (
              <ActivityRow key={a.id ?? i} a={a} />
            ))}
          </div>
        ) : (
          <p className="text-sm text-text-muted">No recent activity.</p>
        )}
      </section>
    </div>
  );
}

function Card({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div className="rounded-[var(--radius-card)] border border-border bg-bg-card p-5">
      <div className={`font-mono text-3xl ${accent ?? "text-text"}`}>{value}</div>
      <div className="mt-1 text-sm text-text-muted">{label}</div>
    </div>
  );
}

function ActivityRow({ a }: { a: ActivityItem }) {
  const when = timeAgo(a.timestamp ?? a.created_at);
  return (
    <div className="flex items-center justify-between gap-3 px-4 py-3">
      <div className="flex min-w-0 items-center gap-3">
        {a.symbol && (
          <Link href={`/symbols/${a.symbol}`} className="font-mono font-semibold text-text no-underline hover:underline">
            {a.symbol}
          </Link>
        )}
        <span className="truncate text-sm text-text-muted">{a._agent_label}</span>
      </div>
      <div className="flex shrink-0 items-center gap-3 text-sm">
        {a.decision && <span className="text-text">{String(a.decision)}</span>}
        {when && <span className="text-text-muted">{when}</span>}
      </div>
    </div>
  );
}
