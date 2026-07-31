"use client";

import { useEffect, useState } from "react";
import { usd, timeAgo } from "@/lib/format";
import { TimingScoreContent } from "@/components/SymbolCharts";
import type { Enrichment, SymbolSummary as Summary } from "@/types/symbol-detail";

function num(n: number | null | undefined, digits = 1): string {
  return typeof n === "number" && isFinite(n) ? n.toFixed(digits) : "—";
}

function Metric({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div className="surface card-hover px-4 py-3">
      <div className="text-xs uppercase tracking-wide text-text-muted">{label}</div>
      <div className={`mt-1 font-mono text-lg font-semibold ${accent ?? ""}`}>{value}</div>
    </div>
  );
}

export default function SymbolSummary({
  symbol,
  enrichment,
  summary,
  totalShares,
}: {
  symbol: string;
  enrichment: Enrichment;
  summary?: Summary;
  totalShares: number;
}) {
  const enr = enrichment || {};
  const [open, setOpen] = useState(false);

  // Close on Escape and lock body scroll while the modal is open.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open]);

  return (
    <>
      <div className="space-y-1.5">
        <button
          type="button"
          onClick={() => setOpen(true)}
          title="View timing & score detail"
          className="flex w-full items-center justify-between text-left"
        >
          <h2 className="text-sm font-semibold text-text-muted">
            Summary <span className="text-xs font-normal text-accent-blue">· click for timing &amp; score</span>
          </h2>
          <span className="text-text-muted">📊</span>
        </button>
        <div
          onClick={() => setOpen(true)}
          className="grid cursor-pointer gap-3 sm:grid-cols-2 lg:grid-cols-4"
        >
          <Metric label="DGI Score" value={num(enr.quality_score)} />
          <Metric label="Tech Timing" value={num(enr.technicals?.score)} />
          <Metric label="Shares" value={totalShares > 0 ? String(totalShares) : "—"} />
          <Metric label="Active Positions" value={String(summary?.active_count ?? 0)} />
          <Metric label="In Calls" value={summary?.in_calls ? String(summary.in_calls) : "—"} />
          <Metric
            label="Puts Committed"
            value={summary?.put_exposure ? `$${usd(summary.put_exposure)}` : "—"}
            accent="text-accent-blue"
          />
          <Metric
            label="Calls Exposure"
            value={summary?.call_exposure ? `$${usd(summary.call_exposure)}` : "—"}
            accent="text-accent-blue"
          />
          {enr.last_updated && <Metric label="Enriched" value={timeAgo(enr.last_updated)} />}
        </div>
      </div>

      {open && (
        <div
          className="fixed inset-0 z-[200] flex items-start justify-center overflow-y-auto bg-black/60 p-4 backdrop-blur-sm animate-fade-in"
          onClick={() => setOpen(false)}
        >
          <div
            className="my-8 w-full max-w-3xl rounded-[var(--radius-card)] border border-border bg-bg-card shadow-[var(--shadow-lg)] animate-pop"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-border px-5 py-3">
              <h3 className="text-lg font-semibold">
                📊 {symbol} · Timing &amp; Score
              </h3>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="grid h-8 w-8 place-items-center rounded-[var(--radius-pill)] text-text-muted hover:bg-bg-hover hover:text-text"
                aria-label="Close"
              >
                ✕
              </button>
            </div>
            <div className="max-h-[75vh] overflow-y-auto px-5 py-4">
              <TimingScoreContent symbol={symbol} enrichment={enr} />
            </div>
          </div>
        </div>
      )}
    </>
  );
}
