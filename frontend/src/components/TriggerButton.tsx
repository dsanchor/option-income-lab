"use client";

import { useRef, useState } from "react";

type Status = "idle" | "running" | "done" | "error" | "already_running";

/**
 * Fire-and-forget agent trigger. POSTs to the BFF proxy `/api/trigger/{agent}`;
 * when `symbol` is provided it is sent in the JSON body (row-level trigger).
 * Manual triggers always request `run_trigger: "manual"` + `force_alpha:
 * true` (force-alpha design, danny-force-alpha-design.md §6) -- a
 * human-initiated click gets a fresh Alpha Advisor review unconditionally.
 * A 409 (another run already in flight for this agent/symbol) renders a
 * distinct "already running" state rather than an error. Shows transient
 * running/done/error/already-running state, then resets after 3s. A
 * synchronous ref guard (in addition to the server-side in-flight guard)
 * prevents a rapid double-click from firing a second request before React
 * has re-rendered the disabled button.
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
  const pendingRef = useRef(false);

  async function run(e: React.MouseEvent) {
    e.stopPropagation();
    if (pendingRef.current) return;
    pendingRef.current = true;
    setStatus("running");
    try {
      const res = await fetch(`/api/trigger/${agent}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol, run_trigger: "manual", force_alpha: true }),
      });
      if (res.status === 409) {
        setStatus("already_running");
        return;
      }
      const data = await res.json().catch(() => ({}));
      setStatus(data.status === "triggered" ? "done" : "error");
    } catch {
      setStatus("error");
    } finally {
      pendingRef.current = false;
      setTimeout(() => setStatus("idle"), 3000);
    }
  }

  const idleLabel = compact ? "▶" : "▶ Run Analysis";
  const label =
    status === "running"
      ? compact ? "⏳" : "⏳ Running…"
      : status === "already_running"
        ? compact ? "⏳" : "⏳ Already running…"
        : status === "done"
          ? compact ? "✓" : "✓ Triggered"
          : status === "error"
            ? compact ? "✗" : "✗ Error"
            : idleLabel;

  const tone =
    status === "done"
      ? "border-accent-green/40 text-accent-green"
      : status === "already_running"
        ? "border-accent-orange/40 text-accent-orange"
        : status === "error"
          ? "border-accent-red/40 text-accent-red"
          : "border-border text-text-muted hover:border-accent-blue/50 hover:text-accent-blue";

  const title =
    status === "already_running"
      ? "Already running for this agent" + (symbol ? ` (${symbol})` : "") + " — please wait"
      : symbol
        ? `Run analysis for ${symbol} (forces a fresh Alpha Advisor review)`
        : "Run this agent now (forces a fresh Alpha Advisor review)";

  return (
    <button
      type="button"
      onClick={run}
      disabled={status === "running" || status === "already_running"}
      title={title}
      className={`inline-flex items-center justify-center rounded-[var(--radius-pill)] border bg-bg-input font-medium transition-colors disabled:opacity-60 ${
        compact ? "h-7 w-7 text-xs" : "px-3 py-1.5 text-xs"
      } ${tone} ${className}`}
    >
      {label}
    </button>
  );
}
