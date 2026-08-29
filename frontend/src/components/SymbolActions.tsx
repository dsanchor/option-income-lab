"use client";

import Link from "next/link";
import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import {
  ChevronDown,
  CandlestickChart,
  MessageSquare,
  FileText,
  Microscope,
  Target,
  TrendingUp,
  TrendingDown,
  ShoppingCart,
  Bell,
  Play,
  Pause,
  Trophy,
  type LucideIcon,
} from "lucide-react";

type ToggleKey = "covered_call" | "cash_secured_put" | "buy_tracker" | "telegram_notifications_enabled";

interface Props {
  symbol: string;
  covered_call: boolean;
  cash_secured_put: boolean;
  buy_tracker: boolean;
  telegram_notifications_enabled: boolean;
  isPaused: boolean;
  nextEarningsDate?: string | null;
}

const ANALYZE: { href: string; icon: LucideIcon; label: string }[] = [
  { href: "best-options", icon: Trophy, label: "Best Options" },
  { href: "options-chain", icon: CandlestickChart, label: "Option Chain" },
  { href: "chat", icon: MessageSquare, label: "Chat" },
  { href: "report", icon: FileText, label: "Report" },
  { href: "technical-analysis", icon: Microscope, label: "Tech Analysis" },
  { href: "forecasts", icon: Target, label: "Forecasts" },
];

export default function SymbolActions(props: Props) {
  const [open, setOpen] = useState(false);
  const router = useRouter();
  const [state, setState] = useState({
    covered_call: props.covered_call,
    cash_secured_put: props.cash_secured_put,
    buy_tracker: props.buy_tracker,
    telegram_notifications_enabled: props.telegram_notifications_enabled,
  });
  const [saving, setSaving] = useState<ToggleKey | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pausing, setPausing] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  async function togglePause() {
    setPausing(true);
    setError(null);
    try {
      const res = await fetch(`/api/symbols/${encodeURIComponent(props.symbol)}/pause`, {
        method: props.isPaused ? "DELETE" : "POST",
        headers: { "Content-Type": "application/json" },
      });
      if (!res.ok) {
        const data = (await res.json().catch(() => ({}))) as { error?: string };
        throw new Error(data.error || `HTTP ${res.status}`);
      }
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
      setPausing(false);
    }
  }

  async function toggle(key: ToggleKey) {
    const next = !state[key];
    setState((s) => ({ ...s, [key]: next }));
    setSaving(key);
    setError(null);
    try {
      const res = await fetch(`/api/symbols/${encodeURIComponent(props.symbol)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [key]: next }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    } catch (err) {
      setState((s) => ({ ...s, [key]: !next })); // revert
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(null);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-3">
      {/* Analyze dropdown */}
      <div className="relative" ref={menuRef}>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] bg-accent-blue px-4 py-2 text-sm font-medium text-white transition hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue/60"
          aria-expanded={open}
          aria-haspopup="menu"
        >
          Analyze
          <ChevronDown size={14} className={`transition-transform ${open ? "rotate-180" : ""}`} aria-hidden />
        </button>
        {open && (
          <div
            role="menu"
            className="absolute left-0 z-20 mt-1 w-52 overflow-hidden rounded-[var(--radius)] border border-border bg-bg-card py-1 shadow-lg"
          >
            {ANALYZE.map((a) => {
              const Icon = a.icon;
              return (
                <Link
                  key={a.href}
                  href={`/symbols/${props.symbol}/${a.href}`}
                  role="menuitem"
                  className="flex items-center gap-2 px-4 py-2 text-sm text-text-muted no-underline transition-colors hover:bg-bg-hover hover:text-text"
                  onClick={() => setOpen(false)}
                >
                  <Icon size={15} className="shrink-0 opacity-80" /> {a.label}
                </Link>
              );
            })}
          </div>
        )}
      </div>

      {/* Tracking toggles */}
      <div className="flex flex-wrap items-center gap-2">
        <Toggle label="CC" icon={TrendingUp} title="Covered Call tracking" checked={state.covered_call}
          disabled={props.isPaused || saving === "covered_call"} onChange={() => toggle("covered_call")} />
        <Toggle label="CSP" icon={TrendingDown} title="Cash-Secured Put tracking" checked={state.cash_secured_put}
          disabled={props.isPaused || saving === "cash_secured_put"} onChange={() => toggle("cash_secured_put")} />
        <Toggle label="Buy" icon={ShoppingCart} title="Buy Tracker" checked={state.buy_tracker}
          disabled={props.isPaused || saving === "buy_tracker"} onChange={() => toggle("buy_tracker")} />
        <Toggle label="Alerts" icon={Bell} title="Telegram notifications" checked={state.telegram_notifications_enabled}
          disabled={saving === "telegram_notifications_enabled"} onChange={() => toggle("telegram_notifications_enabled")} />
      </div>

      {/* Pause / Resume watchlist */}
      {props.isPaused ? (
        <button
          type="button"
          onClick={togglePause}
          disabled={pausing}
          title="Resume covered call, cash-secured put, and buy tracker now"
          className="inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] border border-accent-green/40 bg-accent-green/10 px-3 py-1.5 text-xs text-accent-green transition hover:bg-accent-green/20 disabled:opacity-50"
        >
          <Play size={13} className="shrink-0" /> Resume
        </button>
      ) : (
        <button
          type="button"
          onClick={togglePause}
          disabled={pausing || !props.nextEarningsDate}
          title={
            props.nextEarningsDate
              ? `Pause covered call, cash-secured put, and buy tracker until ${props.nextEarningsDate}`
              : "No upcoming earnings date found. Sync the calendar first."
          }
          className="inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] border border-accent-orange/40 bg-accent-orange/10 px-3 py-1.5 text-xs text-accent-orange transition hover:bg-accent-orange/20 disabled:opacity-50"
        >
          <Pause size={13} className="shrink-0" /> Pause
        </button>
      )}
      {error && <span className="text-xs text-accent-red">{error}</span>}
    </div>
  );
}

function Toggle({
  label, icon: Icon, title, checked, disabled, onChange,
}: {
  label: string; icon: LucideIcon; title: string; checked: boolean; disabled?: boolean; onChange: () => void;
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onChange}
      disabled={disabled}
      aria-pressed={checked}
      className={`inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] border px-3 py-1.5 text-xs transition disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue/60 ${
        checked
          ? "border-accent-green/40 bg-accent-green/10 text-accent-green"
          : "border-border bg-bg-input text-text-muted hover:bg-bg-hover hover:text-text"
      }`}
    >
      <Icon size={14} className="shrink-0" />
      {label}
    </button>
  );
}
