"use client";

import { useEffect, useState } from "react";
import { usd } from "@/lib/format";
import { TimingScoreContent } from "@/components/SymbolCharts";
import type { Enrichment, SymbolSummary as Summary } from "@/types/symbol-detail";

function num(n: number | null | undefined, digits = 1): string {
  return typeof n === "number" && isFinite(n) ? n.toFixed(digits) : "—";
}

function scoreTone(score: number): string {
  if (score >= 70) return "text-accent-green";
  if (score >= 40) return "text-accent-orange";
  return "text-accent-red";
}

function scoreBar(score: number): string {
  if (score >= 70) return "var(--grad-green)";
  if (score >= 40) return "var(--grad-warm)";
  return "linear-gradient(135deg,#ff4d5e,#a3123a)";
}

function Badge({ text, className }: { text: string; className: string }) {
  return (
    <span className={`inline-block rounded-[var(--radius-pill)] border px-2 py-0.5 text-xs ${className}`}>
      {text}
    </span>
  );
}

function momentumClass(m: string): string {
  const base = m.toLowerCase();
  if (base.includes("bull")) return "border-accent-green/40 bg-accent-green/10 text-accent-green";
  if (base.includes("bear")) return "border-accent-red/40 bg-accent-red/10 text-accent-red";
  if (base.includes("weaken")) return "border-accent-orange/40 bg-accent-orange/10 text-accent-orange";
  return "border-border bg-bg-input text-text-muted";
}

function entryClass(t: string): string {
  const base = t.toLowerCase();
  if (base.includes("strong")) return "border-accent-green/40 bg-accent-green/10 text-accent-green";
  if (base === "buy" || base.includes("accumulate")) return "border-accent-blue/40 bg-accent-blue/10 text-accent-blue";
  return "border-border bg-bg-input text-text-muted";
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

  const score = enr.quality_score ?? 0;
  const techScore = enr.technicals?.score;
  const price = enr.metrics?.current_price;
  const momentum = enr.momentum ?? "";
  const momWarn = momentum.includes("overextended") || momentum.includes("oversold");

  const HEAD = ["Category", "DGI Score", "Tech Timing", "Entry", "Momentum", "Price", "Shares", "In Calls", "Puts $"];

  return (
    <>
      <section className="surface overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3">
          <h2 className="text-base font-semibold">Summary</h2>
          <span className="text-xs text-accent-blue">click for timing &amp; score →</span>
        </div>
        <div className="overflow-x-auto border-t border-border">
          <table className="w-full min-w-[820px] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
                {HEAD.map((h, i) => (
                  <th key={h} className={`px-3 py-2 font-medium ${i >= 5 ? "text-right" : ""}`}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr
                onClick={() => setOpen(true)}
                title="View timing & score detail"
                className="cursor-pointer transition-colors hover:bg-bg-hover/50"
              >
                <td className="px-3 py-3">
                  {enr.category ? (
                    <Badge text={enr.category} className="border-accent-purple/40 bg-accent-purple/10 text-accent-purple" />
                  ) : "—"}
                </td>
                <td className="px-3 py-3">
                  {score > 0 ? (
                    <div className="flex items-center gap-2">
                      <span className="h-1.5 w-16 overflow-hidden rounded-full bg-bg-input">
                        <span className="block h-full rounded-full" style={{ width: `${Math.min(score, 100)}%`, background: scoreBar(score) }} />
                      </span>
                      <span className={`font-mono font-semibold ${scoreTone(score)}`}>{num(score)}</span>
                    </div>
                  ) : "—"}
                </td>
                <td className="px-3 py-3 font-mono">{techScore != null ? num(techScore) : "—"}</td>
                <td className="px-3 py-3">
                  {enr.entry_tag ? <Badge text={enr.entry_tag} className={entryClass(enr.entry_tag)} /> : "—"}
                </td>
                <td className="px-3 py-3">
                  {momentum ? (
                    <Badge text={`${momWarn ? "⚠️ " : ""}${momentum}`} className={momentumClass(momentum)} />
                  ) : "—"}
                </td>
                <td className="px-3 py-3 text-right font-mono">{price != null ? `$${price.toFixed(2)}` : "—"}</td>
                <td className="px-3 py-3 text-right font-mono">{totalShares > 0 ? totalShares : "—"}</td>
                <td className="px-3 py-3 text-right font-mono">{summary?.in_calls ? summary.in_calls : "—"}</td>
                <td className="px-3 py-3 text-right font-mono">{summary?.put_exposure ? `$${usd(summary.put_exposure)}` : "—"}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

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
