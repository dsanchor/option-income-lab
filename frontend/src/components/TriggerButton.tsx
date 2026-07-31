"use client";

import { useState } from "react";

type Status = "idle" | "running" | "done" | "error";

/**
 * Fire-and-forget agent trigger. POSTs to the BFF proxy `/api/trigger/{agent}`;
 * when `symbol` is provided it is sent in the JSON body (row-level trigger).
 * Shows transient running/done/error state, then resets after 3s.
 */
export default function TriggerButton({
  agent,
  symbol,
  compact = false,
  className = "",
}: {
  agent: string;
  symbol?: string;
  compact?: boolean;
  className?: string;
}) {
  const [status, setStatus] = useState<Status>("idle");

  async function run(e: React.MouseEvent) {
    e.stopPropagation();
    if (status === "running") return;
    setStatus("running");
    try {
      const res = await fetch(`/api/trigger/${agent}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: symbol ? JSON.stringify({ symbol }) : undefined,
      });
      const data = await res.json().catch(() => ({}));
      setStatus(data.status === "triggered" ? "done" : "error");
    } catch {
      setStatus("error");
    } finally {
      setTimeout(() => setStatus("idle"), 3000);
    }
  }

  const idleLabel = compact ? "▶" : "▶ Run Analysis";
  const label =
    status === "running"
      ? compact ? "⏳" : "⏳ Running…"
      : status === "done"
        ? compact ? "✓" : "✓ Triggered"
        : status === "error"
          ? compact ? "✗" : "✗ Error"
          : idleLabel;

  const tone =
    status === "done"
      ? "border-accent-green/40 text-accent-green"
      : status === "error"
        ? "border-accent-red/40 text-accent-red"
        : "border-border text-text-muted hover:border-accent-blue/50 hover:text-accent-blue";

  return (
    <button
      type="button"
      onClick={run}
      disabled={status === "running"}
      title={symbol ? `Run analysis for ${symbol}` : "Run this agent now"}
      className={`inline-flex items-center justify-center rounded-[var(--radius-pill)] border bg-bg-input font-medium transition-colors disabled:opacity-60 ${
        compact ? "h-7 w-7 text-xs" : "px-3 py-1.5 text-xs"
      } ${tone} ${className}`}
    >
      {label}
    </button>
  );
}
