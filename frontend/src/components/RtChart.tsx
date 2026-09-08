"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";

/**
 * Toggleable live TradingView advanced chart (daily, MACD/ADX/Divergence studies).
 * Mirrors the legacy "RT Chart" toolbar toggle.
 *
 * Accepts the backend-resolved `tvSymbol` in hyphen format (e.g. "NYSE-ABBV",
 * "NASDAQ-MSFT"). Renders nothing (button and chart omitted) when null — never
 * falls back to a guessed symbol.
 *
 * Split into a Provider + Button + Panel so the toggle button can live on the
 * header info line while the chart expansion stays in its original position
 * on the page — both share the same open/closed state via context.
 */

interface RtChartContextValue {
  open: boolean;
  setOpen: (fn: (o: boolean) => boolean) => void;
  widgetSymbol: string | null;
}

const RtChartContext = createContext<RtChartContextValue | null>(null);

export function RtChartProvider({
  tvSymbol,
  children,
}: {
  tvSymbol: string | null;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  // Hyphen → colon: "NASDAQ-MSFT" → "NASDAQ:MSFT" (TradingView widget format)
  const widgetSymbol = tvSymbol?.replace("-", ":") ?? null;

  return (
    <RtChartContext.Provider value={{ open, setOpen, widgetSymbol }}>
      {children}
    </RtChartContext.Provider>
  );
}

export function RtChartButton() {
  const ctx = useContext(RtChartContext);
  if (!ctx || !ctx.widgetSymbol) return null;
  const { open, setOpen } = ctx;

  return (
    <button
      type="button"
      onClick={() => setOpen((o) => !o)}
      aria-pressed={open}
      className={`inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] border px-3 py-1.5 text-sm transition ${
        open
          ? "border-accent-blue/40 bg-accent-blue/10 text-accent-blue"
          : "border-border bg-bg-input text-text-muted hover:text-text"
      }`}
      title="Toggle a live TradingView chart (daily, with MACD, ADX and Divergence studies)."
    >
      <span>📉</span> RT Chart
    </button>
  );
}

export function RtChartPanel() {
  const ctx = useContext(RtChartContext);
  const [loaded, setLoaded] = useState(false);
  const hostRef = useRef<HTMLDivElement>(null);
  const open = ctx?.open ?? false;
  const widgetSymbol = ctx?.widgetSymbol ?? null;

  useEffect(() => {
    if (!open || loaded || !hostRef.current || !widgetSymbol) return;
    const host = hostRef.current;

    const widget = document.createElement("div");
    widget.className = "tradingview-widget-container";
    widget.style.height = "100%";
    widget.style.width = "100%";

    const inner = document.createElement("div");
    inner.className = "tradingview-widget-container__widget";
    inner.style.height = "calc(100% - 32px)";
    inner.style.width = "100%";
    widget.appendChild(inner);

    const config = {
      allow_symbol_change: true,
      calendar: false,
      details: false,
      hide_side_toolbar: true,
      hide_top_toolbar: false,
      hide_legend: false,
      hide_volume: false,
      hotlist: false,
      interval: "D",
      locale: "en",
      save_image: true,
      style: "1",
      symbol: widgetSymbol,
      theme: "dark",
      timezone: "Etc/UTC",
      backgroundColor: "#191c1f",
      gridColor: "rgba(242, 242, 242, 0.06)",
      watchlist: [],
      withdateranges: false,
      compareSymbols: [],
      studies: [
        "STD;Divergence%1Indicator",
        "STD;Average%1Directional%1Index",
        "STD;MACD",
      ],
      autosize: true,
    };

    const script = document.createElement("script");
    script.type = "text/javascript";
    script.src = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";
    script.async = true;
    script.innerHTML = JSON.stringify(config);
    widget.appendChild(script);

    host.appendChild(widget);
    setLoaded(true);
  }, [open, loaded, widgetSymbol]);

  if (!widgetSymbol) return null;

  return (
    <div
      ref={hostRef}
      style={{ height: open ? 520 : 0 }}
      className={`overflow-hidden rounded-[var(--radius)] border transition-all ${
        open ? "border-border" : "border-transparent"
      }`}
    />
  );
}

/**
 * Combined default export — button + panel stacked, for callers that don't
 * need to split them across the layout. Prefer RtChartProvider + RtChartButton
 * + RtChartPanel when the toggle must live elsewhere from the chart itself.
 */
export default function RtChart({ tvSymbol }: { tvSymbol: string | null }) {
  return (
    <RtChartProvider tvSymbol={tvSymbol}>
      <div className="space-y-2">
        <RtChartButton />
        <RtChartPanel />
      </div>
    </RtChartProvider>
  );
}
