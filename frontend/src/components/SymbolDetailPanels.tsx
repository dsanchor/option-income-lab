"use client";

import { useQuery } from "@tanstack/react-query";
import PositionsTable from "@/components/PositionsTable";
import AddPositionForm from "@/components/AddPositionForm";
import RecentActivities from "@/components/RecentActivities";
import PortfolioHoldingsCard from "@/components/PortfolioHoldingsCard";
import StockTransactionsTable from "@/components/StockTransactionsTable";
import SymbolPlansTable from "@/components/SymbolPlansTable";
import { symbolDetailQueryOptions } from "@/lib/symbol-detail-query";
import type { Plan as PlanRow } from "@/types/plans";

type Props = { symbol: string; snapshot?: string };

function PanelStatus({ loading, error, refresh }: { loading: boolean; error: boolean; refresh: () => void }) {
  return (
    <div className="flex items-center gap-3 text-xs text-text-muted">
      <span role={error ? "alert" : "status"}>{error ? "Could not load this section." : loading ? "Loading section…" : ""}</span>
      <button type="button" disabled={loading} onClick={refresh} className="text-accent-blue hover:underline disabled:opacity-50">
        {error ? "Retry" : "Refresh"}
      </button>
    </div>
  );
}

export function SymbolOptionsPanel({ symbol, snapshot, hasAgentContent }: Props & { hasAgentContent: boolean }) {
  const query = useQuery(symbolDetailQueryOptions(symbol, "options", snapshot));
  const positions = query.data?.positions ?? [];
  const activities = query.data?.activities ?? [];
  const hasOptions = hasAgentContent || positions.length > 0 || activities.length > 0;
  return (
    <div className="space-y-6">
      <PanelStatus loading={query.isFetching} error={!!query.error} refresh={() => { void query.refetch(); }} />
      {query.data && (hasOptions ? <>
        <PositionsTable symbol={symbol} positions={positions} />
        <AddPositionForm symbol={symbol} />
        {(hasAgentContent || activities.length > 0) && (
          <RecentActivities activities={activities} agentTypes={query.data.agent_types ?? []} />
        )}
      </> : <p className="py-4 text-sm text-text-muted">No open positions or recent options activity.</p>)}
    </div>
  );
}

export function SymbolStocksPanel({ symbol, snapshot, securityId }: Props & { securityId: string }) {
  const query = useQuery(symbolDetailQueryOptions(symbol, "stocks", snapshot));
  const portfolio = query.data?.portfolio;
  return (
    <div className="space-y-6">
      <PanelStatus loading={query.isFetching} error={!!query.error} refresh={() => { void query.refetch(); }} />
      {portfolio && <PortfolioHoldingsCard portfolio={portfolio} symbolState={query.data?.symbol_state ?? null} />}
      <StockTransactionsTable securityId={securityId} />
    </div>
  );
}

export function SymbolPlansPanel({ symbol, snapshot }: Props) {
  const query = useQuery(symbolDetailQueryOptions(symbol, "plans", snapshot));
  return (
    <div className="space-y-4">
      <PanelStatus loading={query.isFetching} error={!!query.error} refresh={() => { void query.refetch(); }} />
      {query.data && <SymbolPlansTable plans={(query.data.plans ?? []) as unknown as PlanRow[]} />}
    </div>
  );
}
