"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export default function AddSymbolForm() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [symbol, setSymbol] = useState("");
  const [exchange, setExchange] = useState("NASDAQ");
  const [coveredCall, setCoveredCall] = useState(false);
  const [csp, setCsp] = useState(false);
  const [buyTracker, setBuyTracker] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  function resetFields() {
    setSymbol("");
    setExchange("NASDAQ");
    setCoveredCall(false);
    setCsp(false);
    setBuyTracker(false);
    setError(null);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const sym = symbol.trim().toUpperCase();
    const exch = exchange.trim().toUpperCase();
    if (!sym || !exch) {
      setError("Symbol and exchange are required.");
      return;
    }
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      const res = await fetch("/api/symbols", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol: sym,
          exchange: exch,
          covered_call: coveredCall,
          cash_secured_put: csp,
          buy_tracker: buyTracker,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.status === 409) {
        setError(`${sym} already exists in your watchlist.`);
        return;
      }
      if (!res.ok) {
        setError((data as { error?: string }).error || `HTTP ${res.status}`);
        return;
      }
      setSuccess(`${sym} added!`);
      resetFields();
      router.refresh();
      setTimeout(() => {
        setSuccess(null);
        setOpen(false);
      }, 800);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Network error");
    } finally {
      setSaving(false);
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => { setOpen(true); resetFields(); setSuccess(null); }}
        className="inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] border border-border bg-bg-input px-3.5 py-1.5 text-sm text-text-muted transition-colors hover:border-accent-blue hover:text-accent-blue"
      >
        <span className="text-base leading-none">＋</span> Add Symbol
      </button>
    );
  }

  return (
    <form
      onSubmit={submit}
      className="rounded-[var(--radius)] border border-border bg-bg-card p-4"
    >
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-text">Add to Watchlist</h3>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="text-text-muted hover:text-text"
          aria-label="Cancel"
        >
          ✕
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <input
          type="text"
          required
          placeholder="Symbol (e.g. MSFT)"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value.toUpperCase())}
          className="w-32 rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 text-sm font-mono uppercase text-text placeholder:normal-case placeholder:text-text-muted focus:border-accent-blue focus:outline-none"
        />
        <input
          type="text"
          required
          placeholder="Exchange"
          value={exchange}
          onChange={(e) => setExchange(e.target.value.toUpperCase())}
          className="w-28 rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 text-sm font-mono uppercase text-text focus:border-accent-blue focus:outline-none"
        />

        <label className="flex cursor-pointer items-center gap-1.5 text-sm text-text-muted">
          <input type="checkbox" checked={coveredCall} onChange={(e) => setCoveredCall(e.target.checked)} className="accent-accent-blue" />
          📈 Calls
        </label>
        <label className="flex cursor-pointer items-center gap-1.5 text-sm text-text-muted">
          <input type="checkbox" checked={csp} onChange={(e) => setCsp(e.target.checked)} className="accent-accent-blue" />
          💵 Puts
        </label>
        <label className="flex cursor-pointer items-center gap-1.5 text-sm text-text-muted">
          <input type="checkbox" checked={buyTracker} onChange={(e) => setBuyTracker(e.target.checked)} className="accent-accent-blue" />
          🛒 Buy Tracker
        </label>

        <button
          type="submit"
          disabled={saving}
          className="rounded-[var(--radius)] border border-accent-blue bg-accent-blue/15 px-4 py-2 text-sm text-accent-blue transition-colors hover:bg-accent-blue/25 disabled:opacity-50"
        >
          {saving ? "Adding…" : "Add"}
        </button>
      </div>

      {error && (
        <p className="mt-2 text-xs text-accent-red">⚠️ {error}</p>
      )}
      {success && (
        <p className="mt-2 text-xs text-accent-green">✅ {success}</p>
      )}
    </form>
  );
}
