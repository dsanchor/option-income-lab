import { apiFetch } from "@/lib/api";
import StatCard from "@/components/StatCard";
import Reveal from "@/components/Reveal";
import DashboardBanner from "@/components/DashboardBanner";
import DashboardAgentTables from "@/components/DashboardAgentTables";
import DashboardActivity from "@/components/DashboardActivity";
import type { DashboardData } from "@/types/dashboard";

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
  const tables = d.agent_tables ?? [];
  const activity = d.activity ?? [];

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

      {/* Banner marquee */}
      {d.banner_items && d.banner_items.length > 0 && (
        <DashboardBanner items={d.banner_items} />
      )}

      {/* Summary cards (3, matching legacy) */}
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
            hint="Capital-weighted annualized return on your open positions"
          />
        </Reveal>
      </div>

      {/* Agent tables (open positions + trackings) */}
      {tables.length > 0 && <DashboardAgentTables tables={tables} />}

      {/* Recent activity with filters */}
      {activity.length > 0 && <DashboardActivity items={activity} />}
    </div>
  );
}
