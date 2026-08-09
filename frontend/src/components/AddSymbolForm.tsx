"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  CircleAlert,
  CircleCheck,
  LoaderCircle,
  Plus,
  ShoppingCart,
  TrendingDown,
  TrendingUp,
  X,
  type LucideIcon,
} from "lucide-react";

export default function AddSymbolForm() {
  const router = useRouter();
  const formRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [symbol, setSymbol] = useState("");
  const [exchange, setExchange] = useState("NASDAQ");
  const [coveredCall, setCoveredCall] = useState(false);
  const [csp, setCsp] = useState(false);
  const [buyTracker, setBuyTracker] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;

    function onPointerDown(event: MouseEvent) {
      if (!saving && formRef.current && !formRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }

    function onKeyDown(event: KeyboardEvent) {
      if (!saving && event.key === "Escape") setOpen(false);
    }

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, saving]);

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

  function openForm() {
    setOpen(true);
    resetFields();
    setSuccess(null);
  }

  return (
    <div ref={formRef} className="relative inline-block">
      <button
        type="button"
        onClick={open ? () => setOpen(false) : openForm}
        disabled={saving}
        className="inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] bg-accent-blue px-4 py-2 text-sm font-medium text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue/60"
        aria-expanded={open}
        aria-haspopup="dialog"
      >
        <Plus size={15} aria-hidden />
        Add Symbol
      </button>

      {open && (
        <form
          onSubmit={submit}
          role="dialog"
          aria-labelledby="add-symbol-title"
          className="absolute left-0 z-30 mt-2 w-[min(34rem,calc(100vw-2rem))] rounded-[var(--radius)] border border-border bg-bg-card p-4 shadow-lg"
        >
          <div className="mb-4 flex items-center justify-between">
            <h3 id="add-symbol-title" className="text-sm font-semibold text-text">
              Add to Watchlist
            </h3>
            <button
              type="button"
              onClick={() => setOpen(false)}
              disabled={saving}
              className="grid h-8 w-8 place-items-center rounded-[var(--radius-pill)] text-text-muted transition-colors hover:bg-bg-hover hover:text-text disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue/60"
              aria-label="Close add symbol form"
            >
              <X size={16} aria-hidden />
            </button>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="grid gap-1.5 text-xs font-medium text-text-muted">
              Symbol
              <input
                type="text"
                required
                autoFocus
                autoComplete="off"
                placeholder="e.g. MSFT"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                className="h-10 rounded-[var(--radius)] border border-border bg-bg-input px-3 font-mono text-sm uppercase text-text placeholder:normal-case placeholder:text-text-muted focus:border-accent-blue focus:outline-none"
              />
            </label>
            <label className="grid gap-1.5 text-xs font-medium text-text-muted">
              Exchange
              <input
                type="text"
                required
                autoComplete="off"
                placeholder="e.g. NASDAQ"
                value={exchange}
                onChange={(e) => setExchange(e.target.value.toUpperCase())}
                className="h-10 rounded-[var(--radius)] border border-border bg-bg-input px-3 font-mono text-sm uppercase text-text placeholder:normal-case placeholder:text-text-muted focus:border-accent-blue focus:outline-none"
              />
            </label>
          </div>

          <fieldset className="mt-4">
            <legend className="mb-2 text-xs font-medium text-text-muted">Tracking</legend>
            <div className="flex flex-wrap gap-2">
              <StrategyToggle
                label="Calls"
                title="Covered Call tracking"
                icon={TrendingUp}
                checked={coveredCall}
                onChange={() => setCoveredCall((value) => !value)}
              />
              <StrategyToggle
                label="Puts"
                title="Cash-Secured Put tracking"
                icon={TrendingDown}
                checked={csp}
                onChange={() => setCsp((value) => !value)}
              />
              <StrategyToggle
                label="Buy Tracker"
                title="Buy Tracker"
                icon={ShoppingCart}
                checked={buyTracker}
                onChange={() => setBuyTracker((value) => !value)}
              />
            </div>
          </fieldset>

          <div className="mt-4 flex items-center justify-between gap-3">
            <div aria-live="polite">
              {error && (
                <p className="flex items-center gap-1.5 text-xs text-accent-red">
                  <CircleAlert size={14} className="shrink-0" aria-hidden />
                  {error}
                </p>
              )}
              {success && (
                <p className="flex items-center gap-1.5 text-xs text-accent-green">
                  <CircleCheck size={14} className="shrink-0" aria-hidden />
                  {success}
                </p>
              )}
            </div>
            <button
              type="submit"
              disabled={saving}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-[var(--radius-pill)] bg-accent-blue px-4 py-2 text-sm font-medium text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue/60"
            >
              {saving ? (
                <LoaderCircle size={15} className="animate-spin" aria-hidden />
              ) : (
                <Plus size={15} aria-hidden />
              )}
              {saving ? "Adding…" : "Add Symbol"}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

function StrategyToggle({
  label,
  title,
  icon: Icon,
  checked,
  onChange,
}: {
  label: string;
  title: string;
  icon: LucideIcon;
  checked: boolean;
  onChange: () => void;
}) {
  return (
    <button
      type="button"
      title={title}
      aria-pressed={checked}
      onClick={onChange}
      className={`inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] border px-3 py-1.5 text-xs transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue/60 ${
        checked
          ? "border-accent-green/40 bg-accent-green/10 text-accent-green"
          : "border-border bg-bg-input text-text-muted hover:bg-bg-hover hover:text-text"
      }`}
    >
      <Icon size={14} className="shrink-0" aria-hidden />
      {label}
    </button>
  );
}
