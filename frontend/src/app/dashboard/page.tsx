import { apiFetch } from "@/lib/api";
import StatCard from "@/components/StatCard";
import Reveal from "@/components/Reveal";
import DashboardBanner from "@/components/DashboardBanner";
import DashboardAgentTables from "@/components/DashboardAgentTables";
import DashboardActivity from "@/components/DashboardActivity";
import AutoRefresh from "@/components/AutoRefresh";
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
      <AutoRefresh />
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
        </div>
      </div>

      {/* Banner marquee */}
      {d.banner_items && d.banner_items.length > 0 && (
        <DashboardBanner items={d.banner_items} />
      )}

      {/* Summary cards (3, matching legacy) */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Reveal index={0} className="h-full">
          <StatCard label="Calls Exposure" value={d.total_call_exposure ?? 0} prefix="$" tone="blue" />
        </Reveal>
        <Reveal index={1} className="h-full">
          <StatCard label="Puts Committed" value={d.total_put_exposure ?? 0} prefix="$" tone="blue" />
        </Reveal>
        <Reveal index={2} className="h-full">
          <StatCard
            label="Avg RoC · annualized (open)"
            value={roc}
            suffix="%"
            decimals={1}
            tone={roc >= 0 ? "green" : "red"}
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
