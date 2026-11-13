import { Suspense } from "react";
import { API_BASE_URL } from "@/lib/api";
import { decodeSymbolParam } from "@/lib/symbolEncoding";
import { isUSOptionsEligible } from "@/lib/exchangeEligibility";
import SymbolActions from "@/components/SymbolActions";
import SymbolSummary, { SymbolMembershipBadge } from "@/components/DeferredSymbolSummary";
import SymbolForecastSummary from "@/components/SymbolForecastSummary";
import { SymbolOptionsPanel, SymbolStocksPanel, SymbolPlansPanel } from "@/components/SymbolDetailPanels";
import { RtChartProvider, RtChartButton, RtChartPanel } from "@/components/RtChart";
import TradingViewSymbolInfo from "@/components/TradingViewSymbolInfo";
import DetailSection from "@/components/DetailSection";
import SymbolDisambiguation from "@/components/SymbolDisambiguation";
import SymbolConfigurationCard from "@/components/SymbolConfigurationCard";
import SymbolDetailTabs from "@/components/SymbolDetailTabs";
import OptionTiming from "@/components/OptionTiming";
import type { SymbolDetail, SymbolDisambiguationResult } from "@/types/symbol-detail";

export const dynamic = "force-dynamic";

type DetailResult =
  | { kind: "detail"; data: SymbolDetail }
  | { kind: "disambiguation"; data: SymbolDisambiguationResult }
  | { kind: "error"; message: string };

async function getData(symbol: string): Promise<DetailResult> {
  const safeSymbol = decodeSymbolParam(symbol);
  try {
    const url = `${API_BASE_URL}/api/symbols/${encodeURIComponent(safeSymbol)}/detail?section=summary`;
    const res = await fetch(url, { headers: { Accept: "application/json" }, cache: "no-store" });

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
  const symbolState = d.symbol_state ?? null;

  const hasAgentContent =
    d.has_symbol_config ?? (symbolState === "watchlist_only" ||
    symbolState === "watchlist_and_portfolio" ||
    symbolState == null); // legacy — show if state unknown

  // Use backend-resolved flag when available (fail-closed); fall back to local helper.
  const usOptionsEligible =
    d.us_options_eligible != null
      ? d.us_options_eligible
      : isUSOptionsEligible(d.security?.exchange_mic);

  // security_id to query movements — prefer canonical form from security master
  const stocksSecurityId = d.security?.security_id ?? d.security_id ?? null;
  const detailSymbol = stocksSecurityId ?? symbol;

  return (
    <div className="space-y-6">
      {/* ── Shared Header ─────────────────────────────────────────────── */}

      {/* Canonical identity badge */}
      <RtChartProvider tvSymbol={d.tradingview_symbol ?? null}>
        <div className="flex flex-wrap items-center justify-between gap-3">
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
              <SymbolMembershipBadge detailSymbol={detailSymbol} snapshot={d.detail_loaded_at} fallback={symbolState} />
            </div>
          )}
          <RtChartButton />
        </div>

        {/* TradingView symbol info + RT chart panel */}
        <TradingViewSymbolInfo tvSymbol={d.tradingview_symbol ?? null} />
        <RtChartPanel />
      </RtChartProvider>

      {/* ── Summary Section ────────────────────────────────────────────── */}
      <DetailSection title="Summary">
        <SymbolSummary
          symbol={symbol}
          enrichment={enr}
          summary={d.summary}
          currentShares={d.portfolio?.current_shares}
          detailSymbol={detailSymbol}
          snapshot={d.detail_loaded_at}
        />
        <OptionTiming symbol={d.security?.security_id ?? d.security_id ?? symbol} snapshot={d.option_timing} />
        {usOptionsEligible && (
          <Suspense fallback={<div role="status" className="py-4 text-sm text-text-muted">Loading projection…</div>}>
            <SymbolForecastSummary symbol={d.security?.security_id ?? d.security_id ?? symbol} />
          </Suspense>
        )}
      </DetailSection>

      {/* Symbol configuration — always visible, below Summary */}
      {d.security && (
        <SymbolConfigurationCard
          symbol={symbol}
          security={d.security}
          enrichment={d.enrichment ?? {}}
          usOptionsEligible={usOptionsEligible}
        />
      )}

      {/* ── Three-tab navigation: Options / Stocks / Action Plans ─────── */}
      <SymbolDetailTabs
        optionsPanel={
          <div className="space-y-6">
            {usOptionsEligible ? (
              <SymbolOptionsPanel symbol={detailSymbol} snapshot={d.detail_loaded_at} hasAgentContent={hasAgentContent} />
            ) : (
              <p className="text-sm text-text-muted py-4">
                US options (XNYS/XNAS) are not available for this exchange.
              </p>
            )}
          </div>
        }
        stocksPanel={
          stocksSecurityId && (
            <SymbolStocksPanel symbol={detailSymbol} snapshot={d.detail_loaded_at} securityId={stocksSecurityId} />
          )
        }
        plansPanel={<SymbolPlansPanel symbol={detailSymbol} snapshot={d.detail_loaded_at} />}
        actions={
          usOptionsEligible && (
            <SymbolActions
              symbol={d.symbol}
              covered_call={d.watchlist?.covered_call ?? false}
              cash_secured_put={d.watchlist?.cash_secured_put ?? false}
              buy_tracker={d.watchlist?.buy_tracker ?? false}
              telegram_notifications_enabled={d.telegram_notifications_enabled ?? false}
              isPaused={d.is_paused ?? false}
              nextEarningsDate={d.next_earnings_date ?? null}
            />
          )
        }
      />
    </div>
  );
}
