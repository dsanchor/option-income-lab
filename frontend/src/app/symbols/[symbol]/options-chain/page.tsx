"use client";

import { use, useEffect, useState } from "react";
import type { OptionBucket, OptionContract, OptionsChainResponse } from "@/types/options-chain";

function fmtDate(d: string): string {
  const s = String(d);
  return s.length === 8 ? `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}` : s;
}
function fmtPrice(v: number | null | undefined): string {
  return typeof v === "number" && isFinite(v) ? v.toFixed(2) : "—";
}
function fmtGreek(v: number | null | undefined): string {
  return typeof v === "number" && isFinite(v) ? v.toFixed(4) : "—";
}
function fmtIV(v: number | null | undefined): string {
  return typeof v === "number" && isFinite(v) ? `${(v * 100).toFixed(1)}%` : "—";
}

function toRows(raw: Record<string, OptionContract> | OptionContract[]): OptionContract[] {
  const rows = Array.isArray(raw) ? raw : Object.values(raw);
  return [...rows].sort((a, b) => (a.strike ?? 0) - (b.strike ?? 0));
}

const COLS = ["Strike", "Bid", "Ask", "Mid", "IV", "Delta", "Gamma", "Theta", "Vega"];

function ContractTable({ rows }: { rows: OptionContract[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-sm">
        <thead>
          <tr className="border-b border-border text-right text-xs uppercase tracking-wide text-text-muted">
            {COLS.map((c) => (
              <th key={c} className="px-3 py-2 font-medium">{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-border/50 text-right last:border-0">
              <td className="px-3 py-1.5 text-right font-mono font-semibold">{fmtPrice(r.strike)}</td>
              <td className="px-3 py-1.5 font-mono">{fmtPrice(r.bid)}</td>
              <td className="px-3 py-1.5 font-mono">{fmtPrice(r.ask)}</td>
              <td className="px-3 py-1.5 font-mono">{fmtPrice(r.mid)}</td>
              <td className="px-3 py-1.5 font-mono">{fmtIV(r.iv)}</td>
              <td className="px-3 py-1.5 font-mono">{fmtGreek(r.delta)}</td>
              <td className="px-3 py-1.5 font-mono">{fmtGreek(r.gamma)}</td>
              <td className="px-3 py-1.5 font-mono">{fmtGreek(r.theta)}</td>
              <td className="px-3 py-1.5 font-mono">{fmtGreek(r.vega)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ExpirationRow({ exp, rows }: { exp: string; rows: OptionContract[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-[var(--radius)] border border-border bg-bg-input/40">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-4 py-2.5 text-left"
      >
        <span className="w-4 text-xs text-text-muted">{open ? "▾" : "▸"}</span>
        <span className="font-medium">Exp: {fmtDate(exp)}</span>
        <span className="ml-auto text-xs text-text-muted">{rows.length} contracts</span>
      </button>
      {open && <div className="border-t border-border px-2 pb-2">{<ContractTable rows={rows} />}</div>}
    </div>
  );
}

function Section({ title, bucket }: { title: string; bucket: OptionBucket }) {
  const exps = Object.keys(bucket).sort();
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold">{title}</h2>
      {exps.length === 0 ? (
        <p className="text-sm text-text-muted">No data available.</p>
      ) : (
        <div className="space-y-2">
          {exps.map((exp) => (
            <ExpirationRow key={exp} exp={exp} rows={toRows(bucket[exp])} />
          ))}
        </div>
      )}
    </section>
  );
}

export default function OptionsChainPage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol } = use(params);
  const [data, setData] = useState<OptionsChainResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch(`/api/symbols/${encodeURIComponent(symbol)}/options-chain`)
      .then(async (res) => {
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
        return body as OptionsChainResponse;
      })
      .then((body) => {
        if (!cancelled) setData(body);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load option chain");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">📈 {symbol} Option Chain</h1>
        <p className="text-sm text-text-muted">
          Live option chain data{data?.timestamp ? ` · ${data.timestamp}` : ""}.
        </p>
      </div>

      {loading && (
        <div className="rounded-[var(--radius)] border border-border bg-bg-card px-4 py-12 text-center text-text-muted">
          <div className="mb-2 text-2xl">⏳</div>
          Fetching live chain… this can take a few seconds.
        </div>
      )}

      {!loading && error && (
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
          ⚠️ {error}
        </div>
      )}

      {!loading && !error && data && (
        <>
          <Section title="Calls" bucket={data.calls ?? {}} />
          <Section title="Puts" bucket={data.puts ?? {}} />
        </>
      )}
    </div>
  );
}
