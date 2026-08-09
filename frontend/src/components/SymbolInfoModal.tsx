"use client";

import { useEffect, useState } from "react";
import { TimingScoreContent } from "@/components/SymbolCharts";
import type { Enrichment } from "@/types/symbol-detail";

/**
 * Modal that lazily loads a symbol's enrichment and shows its Timing & Score
 * detail. Used from the Symbols watchlist (row click).
 */
export default function SymbolInfoModal({
  symbol,
  onClose,
}: {
  symbol: string;
  onClose: () => void;
}) {
  const [enr, setEnr] = useState<Enrichment | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    fetch(`/api/symbols/${encodeURIComponent(symbol)}/detail`, { headers: { Accept: "application/json" } })
      .then(async (res) => {
        const body = await res.json().catch(() => ({}));
        if (!res.ok || body.error) throw new Error(body.error || `HTTP ${res.status}`);
        if (alive) setEnr((body.enrichment ?? {}) as Enrichment);
      })
      .catch((e) => alive && setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [symbol]);

  return (
    <div
      className="fixed inset-0 z-[200] flex items-start justify-center overflow-y-auto bg-black/60 p-4 backdrop-blur-sm animate-fade-in"
      onClick={onClose}
    >
      <div
        className="my-8 w-full max-w-3xl rounded-[var(--radius-card)] border border-border bg-bg-card shadow-[var(--shadow-lg)] animate-pop"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <h3 className="text-lg font-semibold">📊 {symbol} · Timing &amp; Score</h3>
          <button
            type="button"
            onClick={onClose}
            className="grid h-8 w-8 place-items-center rounded-[var(--radius-pill)] text-text-muted hover:bg-bg-hover hover:text-text"
            aria-label="Close"
          >
            ✕
          </button>
        </div>
        <div className="max-h-[75vh] overflow-y-auto px-5 py-4">
          {loading && <p className="py-8 text-center text-sm text-text-muted">Loading symbol data…</p>}
          {error && <p className="py-8 text-center text-sm text-accent-red">⚠️ {error}</p>}
          {!loading && !error && enr && <TimingScoreContent symbol={symbol} enrichment={enr} />}
        </div>
      </div>
    </div>
  );
}
