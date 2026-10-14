"use client";

import { useEffect, useState } from "react";
import { SnapshotChart } from "@/components/PositionDetail";

type Snapshot = Record<string, number | string | boolean | null | undefined>;

export default function MonitoringHistoryDialog({
  symbol,
  positionId,
  title,
  onClose,
}: {
  symbol: string;
  positionId: string;
  title: string;
  onClose: () => void;
}) {
  const [snapshots, setSnapshots] = useState<Snapshot[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(
          `/api/symbols/${encodeURIComponent(symbol)}/positions/${encodeURIComponent(positionId)}/snapshots`,
          { headers: { Accept: "application/json" } },
        );
        const data = (await res.json().catch(() => ({}))) as { snapshots?: Snapshot[]; error?: string };
        if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
        if (!cancelled) setSnapshots(data.snapshots ?? []);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load snapshots");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [symbol, positionId]);

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

  return (
    <div
      className="fixed inset-0 z-[200] flex items-start justify-center overflow-y-auto bg-black/60 p-4 backdrop-blur-sm animate-fade-in"
      onClick={(e) => {
        e.stopPropagation();
        onClose();
      }}
    >
      <div
        role="dialog"
        aria-label="Monitoring History"
        className="my-8 w-full max-w-4xl rounded-[var(--radius-card)] border border-border bg-bg-card shadow-[var(--shadow-lg)] animate-pop"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <h3 className="text-lg font-semibold">📈 {title} · Monitoring History</h3>
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
          {error ? (
            <p className="py-8 text-center text-sm text-accent-red">⚠️ {error}</p>
          ) : snapshots === null ? (
            <p className="py-8 text-center text-sm text-text-muted">Loading monitoring data…</p>
          ) : (
            <SnapshotChart snapshots={snapshots} />
          )}
        </div>
      </div>
    </div>
  );
}
