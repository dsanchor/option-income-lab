"use client";

import { useQuery } from "@tanstack/react-query";
import SymbolSummary from "@/components/SymbolSummary";
import { symbolDetailQueryOptions } from "@/lib/symbol-detail-query";
import type { ComponentProps } from "react";

export default function DeferredSymbolSummary({
  detailSymbol, snapshot, ...props
}: ComponentProps<typeof SymbolSummary> & { detailSymbol: string; snapshot?: string }) {
  const stocks = useQuery({ ...symbolDetailQueryOptions(detailSymbol, "stocks", snapshot), enabled: false });
  return <SymbolSummary {...props} currentShares={stocks.data ? stocks.data.portfolio?.current_shares : props.currentShares} />;
}

export function SymbolMembershipBadge({ detailSymbol, snapshot, fallback }: {
  detailSymbol: string; snapshot?: string; fallback?: string | null;
}) {
  // Observe the Stocks panel's cache without fetching holdings on the summary path.
  const stocks = useQuery({ ...symbolDetailQueryOptions(detailSymbol, "stocks", snapshot), enabled: false });
  const state = stocks.data?.symbol_state ?? fallback;
  if (!state) return null;
  const label = state === "watchlist_only" ? "Watchlist" : state === "portfolio_only" ? "Portfolio" :
    state === "watchlist_and_portfolio" ? "Portfolio + Watchlist" : state === "portfolio_historical" ? "Portfolio (historical)" : state;
  const tone = state === "watchlist_only" ? "border-accent-blue/40 bg-accent-blue/10 text-accent-blue" :
    state === "portfolio_only" || state === "watchlist_and_portfolio" ? "border-accent-green/40 bg-accent-green/10 text-accent-green" :
      "border-border bg-bg-input text-text-muted";
  return <span className={`inline-block rounded-[var(--radius-pill)] border px-2 py-0.5 ${tone}`}>{label}</span>;
}
