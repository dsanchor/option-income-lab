import { API_BASE_URL } from "@/lib/api";
import { decodeSymbolParam } from "@/lib/symbolEncoding";
import { isUSOptionsEligible } from "@/lib/exchangeEligibility";
import SymbolActions from "@/components/SymbolActions";
import RecentActivities from "@/components/RecentActivities";
import PositionsTable from "@/components/PositionsTable";
import SymbolSummary from "@/components/SymbolSummary";
import AddPositionForm from "@/components/AddPositionForm";
import SymbolPlansTable from "@/components/SymbolPlansTable";
import RtChart from "@/components/RtChart";
import TradingViewSymbolInfo from "@/components/TradingViewSymbolInfo";
import PortfolioHoldingsCard from "@/components/PortfolioHoldingsCard";
import DetailSection from "@/components/DetailSection";
import StockTransactionsTable from "@/components/StockTransactionsTable";
import SymbolDisambiguation from "@/components/SymbolDisambiguation";
import SymbolConfigurationCard from "@/components/SymbolConfigurationCard";
import type { SymbolDetail, SymbolDisambiguationResult } from "@/types/symbol-detail";
import type { Plan as PlanRow } from "@/types/plans";

export const dynamic = "force-dynamic";

type DetailResult =
  | { kind: "detail"; data: SymbolDetail }
  | { kind: "disambiguation"; data: SymbolDisambiguationResult }
  | { kind: "error"; message: string };

async function getData(symbol: string): Promise<DetailResult> {
  const safeSymbol = decodeSymbolParam(symbol);
  try {
    const url = `${API_BASE_URL}/api/symbols/${encodeURIComponent(safeSymbol)}/detail`;
    const res = await fetch(url, { headers: { Accept: "application/json" } });

    if (res.status === 300) {
      const body = await res.json() as SymbolDisambiguationResult;
      return { kind: "disambiguation", data: { ...body, query: body.query ?? symbol } };
    }

    if (!res.ok) {
      const body = await res.text().catch(() => "");
      return { kind: "error", message: `API ${res.status} for ${symbol}: ${body.slice(0, 200)}` };
    }

    const data = await res.json() as SymbolDetail;
    return { kind: "detail", data };
  } catch (err) {
    return {
      kind: "error",
      message: err instanceof Error ? err.message : "Failed to load symbol",
    };
  }
}

export default async function SymbolDetailPage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol: _rawSymbol } = await params;
  const symbol = decodeSymbolParam(_rawSymbol);
  const result = await getData(symbol);

  if (result.kind === "error") {
    return (
      <div className="space-y-4">
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
          ⚠️ {result.message}
        </div>
      </div>
    );
  }

  if (result.kind === "disambiguation") {
    return (
      <SymbolDisambiguation
        query={result.data.query}
        choices={result.data.multiple_choices}
      />
    );
  }

  const d = result.data;
  const enr = d.enrichment ?? {};
  const positions = d.positions ?? [];
  const activities = d.activities ?? [];
  const plans = d.plans ?? [];
  const symbolState = d.symbol_state ?? null;

  const hasPortfolio = d.portfolio != null;
  const hasAgentContent =
    symbolState === "watchlist_only" ||
    symbolState === "watchlist_and_portfolio" ||
    symbolState == null; // legacy — show if state unknown

  const hasOptions = hasAgentContent || positions.length > 0 || activities.length > 0;

  // Use backend-resolved flag when available (fail-closed); fall back to local helper.
  const usOptionsEligible =
    d.us_options_eligible != null
      ? d.us_options_eligible
      : isUSOptionsEligible(d.security?.exchange_mic);

  // security_id to query movements — prefer canonical form from security master
  const stocksSecurityId = d.security?.security_id ?? d.security_id ?? null;

  return (
    <div className="space-y-6">
      {/* ── Shared Header ─────────────────────────────────────────────── */}

      {/* Canonical identity badge */}
      {d.security && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-text-muted">
          <span className="font-mono font-semibold text-text">{d.security.security_id}</span>
          <span>·</span>
          <span>{d.security.company_name}</span>
          {d.security.isin && (
            <>
              <span>·</span>
              <span className="font-mono">ISIN {d.security.isin}</span>
            </>
          )}
          {d.security.listing_currency && (
            <>
              <span>·</span>
              <span>{d.security.listing_currency}</span>
            </>
          )}
          {symbolState && (
            <span className={`inline-block rounded-[var(--radius-pill)] border px-2 py-0.5 ${membershipBadgeClass(symbolState)}`}>
              {membershipBadgeLabel(symbolState)}
            </span>
          )}
        </div>
      )}

      {/* Toolbar: actions — US exchanges (XNYS/XNAS) only */}
      {usOptionsEligible && (
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
      )}

      {/* TradingView symbol info + RT chart */}
      <TradingViewSymbolInfo tvSymbol={d.tradingview_symbol ?? null} />
      <RtChart tvSymbol={d.tradingview_symbol ?? null} />

      {/* ── Summary Section ────────────────────────────────────────────── */}
      <DetailSection title="Summary">
        <SymbolSummary
          symbol={symbol}
          enrichment={enr}
          summary={d.summary}
          totalShares={d.total_shares}
        />
      </DetailSection>

      {/* ── Options Section — US exchanges (XNYS/XNAS) only ──────────── */}
      {hasOptions && usOptionsEligible && (
        <DetailSection title="Options">
          <PositionsTable symbol={symbol} positions={positions} />
          <AddPositionForm symbol={symbol} />
          {(hasAgentContent || activities.length > 0) && (
            <RecentActivities activities={activities} agentTypes={d.agent_types ?? []} />
          )}
        </DetailSection>
      )}

      {/* ── Stocks Section ─────────────────────────────────────────────── */}
      {stocksSecurityId && (
        <DetailSection title="Stocks">
          {/* Symbol Configuration — first, above Holdings, per contract */}
          <SymbolConfigurationCard
            symbol={symbol}
            security={d.security!}
            enrichment={d.enrichment ?? {}}
            watchlist={d.watchlist ?? { covered_call: false, cash_secured_put: false, buy_tracker: false }}
            telegramEnabled={d.telegram_notifications_enabled ?? false}
            usOptionsEligible={usOptionsEligible}
          />
          {hasPortfolio && (
            <PortfolioHoldingsCard portfolio={d.portfolio!} symbolState={symbolState} />
          )}
          <StockTransactionsTable securityId={stocksSecurityId} />
        </DetailSection>
      )}

      {/* ── Plans (always visible) ─────────────────────────────────────── */}
      <SymbolPlansTable plans={plans as unknown as PlanRow[]} />
    </div>
  );
}

function membershipBadgeLabel(state: string): string {
  switch (state) {
    case "watchlist_only": return "Watchlist";
    case "portfolio_only": return "Portfolio";
    case "watchlist_and_portfolio": return "Portfolio + Watchlist";
    case "portfolio_historical": return "Portfolio (historical)";
    default: return state;
  }
}

function membershipBadgeClass(state: string): string {
  switch (state) {
    case "watchlist_only": return "border-accent-blue/40 bg-accent-blue/10 text-accent-blue";
    case "portfolio_only":
    case "watchlist_and_portfolio": return "border-accent-green/40 bg-accent-green/10 text-accent-green";
    case "portfolio_historical": return "border-border bg-bg-input text-text-muted";
    default: return "border-border bg-bg-input text-text-muted";
  }
}
