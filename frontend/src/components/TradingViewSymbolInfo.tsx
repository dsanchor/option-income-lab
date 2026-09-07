"use client";

import { useEffect, useRef } from "react";

/**
 * Always-visible TradingView Symbol Info widget (price, performance, key stats).
 * Mirrors the legacy header widget on the symbol detail page.
 *
 * Accepts the backend-resolved `tvSymbol` in hyphen format (e.g. "NYSE-ABBV",
 * "BME-ACS"). Renders nothing when null — never falls back to a guessed symbol.
 */
export default function TradingViewSymbolInfo({ tvSymbol }: { tvSymbol: string | null }) {
  const hostRef = useRef<HTMLDivElement>(null);
  // Hyphen → colon: "BME-ACS" → "BME:ACS" (TradingView widget format)
  const widgetSymbol = tvSymbol?.replace("-", ":") ?? null;

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !widgetSymbol) return;
    host.innerHTML = "";

    const inner = document.createElement("div");
    inner.className = "tradingview-widget-container__widget";
    host.appendChild(inner);

    const script = document.createElement("script");
    script.type = "text/javascript";
    script.src = "https://s3.tradingview.com/external-embedding/embed-widget-symbol-info.js";
    script.async = true;
    script.innerHTML = JSON.stringify({
      symbol: widgetSymbol,
      colorTheme: "dark",
      isTransparent: true,
      locale: "en",
      width: "100%",
    });
    host.appendChild(script);

    return () => {
      host.innerHTML = "";
    };
  }, [widgetSymbol]);

  if (!widgetSymbol) return null;

  return (
    <div className="rounded-[var(--radius)] border border-border bg-bg-card px-2 py-1">
      <div ref={hostRef} className="tradingview-widget-container" />
    </div>
  );
}
