import { apiFetch } from "@/lib/api";
import SymbolActions from "@/components/SymbolActions";
import RecentActivities from "@/components/RecentActivities";
import PositionsTable from "@/components/PositionsTable";
import SymbolSummary from "@/components/SymbolSummary";
import AddPositionForm from "@/components/AddPositionForm";
import SymbolPlansTable from "@/components/SymbolPlansTable";
import RtChart from "@/components/RtChart";
import TradingViewSymbolInfo from "@/components/TradingViewSymbolInfo";
import type { SymbolDetail } from "@/types/symbol-detail";
import type { Plan as PlanRow } from "@/types/plans";

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
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
          ⚠️ {d.error}
        </div>
      </div>
    );
  }

  const enr = d.enrichment ?? {};
  const positions = d.positions ?? [];
  const activities = d.activities ?? [];
  const plans = d.plans ?? [];

  return (
    <div className="space-y-6">
      {/* Toolbar: actions (symbol title/price/badges live in the widget + Summary) */}
      <div className="flex flex-wrap items-center justify-end gap-4">
        <SymbolActions
          symbol={d.symbol}
          covered_call={d.watchlist?.covered_call ?? false}
          cash_secured_put={d.watchlist?.cash_secured_put ?? false}
          buy_tracker={d.watchlist?.buy_tracker ?? false}
          telegram_notifications_enabled={d.telegram_notifications_enabled ?? false}
          isPaused={d.is_paused ?? false}
          nextEarningsDate={d.next_earnings_date ?? null}
        />
      </div>

      {/* TradingView symbol info + RT chart */}
      <TradingViewSymbolInfo symbol={d.symbol} exchange={d.exchange} />
      <RtChart symbol={d.symbol} exchange={d.exchange} />

      {/* Summary (click to open Timing & Score modal) */}
      <SymbolSummary
        symbol={symbol}
        enrichment={enr}
        summary={d.summary}
        totalShares={d.total_shares}
      />

      {/* Positions */}
      <PositionsTable symbol={symbol} positions={positions} />

      {/* Add position */}
      <AddPositionForm symbol={symbol} />

      {/* Plans */}
      <SymbolPlansTable plans={plans as unknown as PlanRow[]} />

      {/* Recent activity */}
      <RecentActivities activities={activities} agentTypes={d.agent_types ?? []} />
    </div>
  );
}
