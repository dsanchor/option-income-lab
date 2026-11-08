"use client";

import type { Horizon, SymbolForecastSummary, SymbolsForecastOverview } from "@/types/forecasts";

function percentage(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 100
    ? `${value.toFixed(1)}%` : "—";
}

export function ForecastBandCell({
  summary, horizon, range, error, formatPrice,
}: {
  summary?: SymbolForecastSummary;
  horizon: Horizon;
  range?: SymbolsForecastOverview["range"];
  error?: string;
  formatPrice: (price: number | null | undefined, currency: string | null | undefined) => string;
}) {
  if (summary?.status === "not_eligible") {
    return <td className="px-3 py-3 text-xs text-text-muted">Not elegible</td>;
  }
  if (!summary || summary.status !== "ok" || !summary.latest) {
    const label = error ? "Forecast error" : !summary ? "Loading…" :
      summary.status === "error" ? "Forecast error" : "Forecast unavailable";
    return <td className="px-3 py-3 text-xs text-text-muted" title={error ?? summary?.reason}>
      {label}
      {summary?.reason && <div>{summary.reason}</div>}
    </td>;
  }
  const latest = summary.latest;
  const band = latest.horizons[horizon];
  const hit = summary.hit_rate?.[horizon];
  const price = (value: number | null | undefined) => {
    if (!summary.currency) return "—";
    // Forecast values are raw source units, not the overview's major-unit prices.
    if (summary.currency === "GBp" || summary.currency === "GBX") {
      return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(2)} GBp` : "—";
    }
    return formatPrice(value, summary.currency);
  };
  const innerN = hit?.resolved_inner ?? hit?.resolved;
  const outerN = hit?.resolved_outer ?? hit?.resolved;
  const validCount = (n: number | undefined) => typeof n === "number" && Number.isInteger(n) && n > 0 ? n : 0;
  const innerCount = validCount(innerN);
  const outerCount = validCount(outerN);
  const samples = innerCount === outerCount ? `${innerCount} resolved endpoints` : `${innerCount} inner / ${outerCount} outer resolved endpoints`;
  const tooltip = `Forecast: ${latest.created_date}${summary.stale ? " · Stale: outside 90d window" : ""} · Anchor: ${price(latest.price_at_creation)} · ${range?.from ?? "—"} – ${range?.to ?? "—"} · ${samples}${summary.currency ? "" : " · Forecast currency unavailable"}`;
  return (
    <td className="px-3 py-3 whitespace-nowrap text-xs font-mono" title={tooltip}>
      {band ? <>
        {summary.stale && <div className="text-text-muted">Stale forecast: {latest.created_date}</div>}
        <div>Inner {percentage(latest.confidence * 100)}: {price(band.low1)}–{price(band.high1)}</div>
        <div className="text-text-muted">Outer {percentage(latest.outer_confidence * 100)}: {price(band.low2)}–{price(band.high2)}</div>
        <div className="text-accent-blue">Hit 90d: {innerCount ? percentage(hit?.hit_pct_1sigma) : "—"} / {outerCount ? percentage(hit?.hit_pct_2sigma) : "—"}</div>
      </> : <span className="text-text-muted">Forecast unavailable</span>}
    </td>
  );
}
