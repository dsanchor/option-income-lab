"use client";

import { useEffect, useRef, useState } from "react";
import type { DashboardStatusPayload } from "@/types/dashboard";

type Status = "idle" | "running" | "done" | "error" | "already_running";
const POLL_INTERVAL_MS = 1000;
const RUN_TIMEOUT_MS = 30 * 60 * 1000;

/**
 * Starts an agent and follows its run ID through the dashboard status endpoint.
 * When `symbol` is provided it is sent in the JSON body (row-level trigger).
 * Manual triggers always request `run_trigger: "manual"` + `force_alpha:
 * true` (force-alpha design, danny-force-alpha-design.md §6) -- a
 * human-initiated click gets a fresh Alpha Advisor review unconditionally.
 * A 409 (another run already in flight for this agent/symbol) renders a
 * distinct "already running" state rather than an error. Terminal states reset
 * after 3s. A synchronous ref guard (in addition to the server-side in-flight guard)
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
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const pendingRef = useRef(false);
  const mountedRef = useRef(true);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const resetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => {
    mountedRef.current = false;
    abortRef.current?.abort();
    if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
    if (resetTimerRef.current) clearTimeout(resetTimerRef.current);
  }, []);

  function finish(nextStatus: Status, message: string | null = null) {
    if (!mountedRef.current) return;
    pendingRef.current = false;
    setStatus(nextStatus);
    setErrorMessage(message);
    resetTimerRef.current = setTimeout(() => {
      if (!mountedRef.current) return;
      setStatus("idle");
      setErrorMessage(null);
    }, 3000);
  }

  async function pollRun(runId: string, deadline: number): Promise<void> {
    if (Date.now() >= deadline) {
      finish("error", "Run status timed out");
      return;
    }
    abortRef.current = new AbortController();
    try {
      const res = await fetch("/api/dashboard/status", {
        cache: "no-store",
        signal: abortRef.current.signal,
      });
      if (!res.ok) throw new Error("Status request failed");
      const data = (await res.json()) as DashboardStatusPayload;
      const run = data.runs?.[runId];
      if (run?.status === "succeeded") {
        finish("done");
        return;
      }
      if (run?.status === "failed") {
        finish("error", run.error ?? "Run failed");
        return;
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
    }
    if (mountedRef.current && pendingRef.current) {
      pollTimerRef.current = setTimeout(
        () => void pollRun(runId, deadline),
        POLL_INTERVAL_MS,
      );
    }
  }

  async function run(e: React.MouseEvent) {
    e.stopPropagation();
    if (pendingRef.current) return;
    pendingRef.current = true;
    setStatus("running");
    setErrorMessage(null);
    try {
      const res = await fetch(`/api/trigger/${agent}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol, run_trigger: "manual", force_alpha: true }),
      });
      if (res.status === 409) {
        finish("already_running");
        return;
      }
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.status !== "triggered" || typeof data.run_id !== "string") {
        finish("error", typeof data.error === "string" ? data.error : "Trigger failed");
        return;
      }
      await pollRun(data.run_id, Date.now() + RUN_TIMEOUT_MS);
    } catch {
      finish("error", "Trigger request failed");
    }
  }

  const idleLabel = compact ? "▶" : "▶ Run Analysis";
  const label =
    status === "running"
      ? compact ? "⏳" : "⏳ Running…"
      : status === "already_running"
        ? compact ? "⏳" : "⏳ Already running…"
        : status === "done"
          ? compact ? "✓" : "✓ Completed"
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
      : status === "error" && errorMessage
        ? errorMessage
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
