import Link from "next/link";
import { apiFetch } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import StatCard from "@/components/StatCard";
import Reveal from "@/components/Reveal";
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
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
          <p className="text-sm text-text-muted">Portfolio exposure &amp; latest agent activity</p>
        </div>
        <span
          className={`inline-flex items-center gap-2 rounded-[var(--radius-pill)] border px-3 py-1.5 text-sm ${
            d.market_open
              ? "border-accent-green/40 bg-accent-green/10 text-accent-green"
              : "border-border bg-bg-input text-text-muted"
          }`}
        >
          <span className={`h-2 w-2 rounded-full ${d.market_open ? "bg-accent-green animate-pulse" : "bg-text-muted"}`} />
          Market {d.market_open ? "Open" : "Closed"}
        </span>
      </div>

      {d.banner_items && d.banner_items.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {d.banner_items.map((b: BannerItem, i) => (
            <Reveal key={i} index={i}>
              <div
                className="flex items-center gap-2 rounded-[var(--radius-pill)] border border-border bg-bg-card/70 px-3 py-1.5 text-sm backdrop-blur card-hover"
                title={[b.category, b.symbol].filter(Boolean).join(" · ")}
              >
                {b.emoji && <span>{b.emoji}</span>}
                {b.symbol && <span className="font-mono font-semibold">{b.symbol}</span>}
                {b.text && <span className="text-text-muted">{b.text}</span>}
              </div>
            </Reveal>
          ))}
        </div>
      )}

      {/* Summary cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Reveal index={0}>
          <StatCard label="Calls Exposure" value={d.total_call_exposure ?? 0} prefix="$" tone="blue" icon="📞" />
        </Reveal>
        <Reveal index={1}>
          <StatCard label="Puts Committed" value={d.total_put_exposure ?? 0} prefix="$" tone="blue" icon="🛡️" />
        </Reveal>
        <Reveal index={2}>
          <StatCard
            label="Avg RoC · annualized (open)"
            value={roc}
            suffix="%"
            decimals={1}
            tone={roc >= 0 ? "green" : "red"}
            icon="📈"
          />
        </Reveal>
      </div>

      {/* Secondary counters */}
      <div className="grid gap-4 sm:grid-cols-3">
        <Reveal index={0}>
          <StatCard label="Symbols" value={d.symbol_count ?? 0} tone="purple" icon="📊" />
        </Reveal>
        <Reveal index={1}>
          <StatCard label="Active Positions" value={d.position_count ?? 0} tone="orange" icon="🎯" />
        </Reveal>
        <Reveal index={2}>
          <StatCard
            label="Market"
            display={d.market_open ? "Open" : "Closed"}
            tone={d.market_open ? "green" : "neutral"}
            icon={d.market_open ? "🟢" : "⚪"}
          />
        </Reveal>
      </div>

      {/* Recent activity */}
      <section>
        <h2 className="mb-3 text-lg font-semibold">Recent Activity</h2>
        {d.activity && d.activity.length > 0 ? (
          <div className="surface table-modern divide-y divide-border/70 overflow-hidden">
            {d.activity.slice(0, 20).map((a: ActivityItem, i) => (
              <ActivityRow key={a.id ?? i} a={a} />
            ))}
          </div>
        ) : (
          <div className="surface p-8 text-center text-sm text-text-muted">
            <div className="mb-1 text-2xl">💤</div>
            No recent activity yet.
          </div>
        )}
      </section>
    </div>
  );
}

function ActivityRow({ a }: { a: ActivityItem }) {
  const when = timeAgo(a.timestamp ?? a.created_at);
  return (
    <div className="flex items-center justify-between gap-3 px-4 py-3">
      <div className="flex min-w-0 items-center gap-3">
        {a.symbol && (
          <Link
            href={`/symbols/${a.symbol}`}
            className="rounded-md bg-bg-input px-2 py-0.5 font-mono text-sm font-semibold text-text no-underline transition-colors hover:bg-bg-hover"
          >
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
