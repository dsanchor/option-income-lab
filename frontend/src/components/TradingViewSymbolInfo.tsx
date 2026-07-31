"use client";

import { useEffect, useRef } from "react";

/**
 * Always-visible TradingView Symbol Info widget (price, performance, key stats).
 * Mirrors the legacy header widget on the symbol detail page.
 */
export default function TradingViewSymbolInfo({
  symbol,
  exchange,
}: {
  symbol: string;
  exchange?: string;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const tvSymbol = exchange ? `${exchange}:${symbol}` : symbol;

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    host.innerHTML = "";

    const inner = document.createElement("div");
    inner.className = "tradingview-widget-container__widget";
    host.appendChild(inner);

    const script = document.createElement("script");
    script.type = "text/javascript";
    script.src = "https://s3.tradingview.com/external-embedding/embed-widget-symbol-info.js";
    script.async = true;
    script.innerHTML = JSON.stringify({
      symbol: tvSymbol,
      colorTheme: "dark",
      isTransparent: true,
      locale: "en",
      width: "100%",
    });
    host.appendChild(script);

    return () => {
      host.innerHTML = "";
    };
  }, [tvSymbol]);

  return (
    <div className="rounded-[var(--radius)] border border-border bg-bg-card px-2 py-1">
      <div ref={hostRef} className="tradingview-widget-container" />
    </div>
  );
}
