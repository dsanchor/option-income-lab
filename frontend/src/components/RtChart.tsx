"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Toggleable live TradingView advanced chart (daily, MACD/ADX/Divergence studies).
 * Mirrors the legacy "RT Chart" toolbar toggle.
 */
export default function RtChart({ symbol, exchange }: { symbol: string; exchange?: string }) {
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const hostRef = useRef<HTMLDivElement>(null);
  const tvSymbol = exchange ? `${exchange}:${symbol}` : symbol;

  useEffect(() => {
    if (!open || loaded || !hostRef.current) return;
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
      symbol: tvSymbol,
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
  }, [open, loaded, tvSymbol]);

  return (
    <div className="space-y-2">
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
      <div
        ref={hostRef}
        style={{ height: open ? 520 : 0 }}
        className={`overflow-hidden rounded-[var(--radius)] border transition-all ${
          open ? "border-border" : "border-transparent"
        }`}
      />
    </div>
  );
}
