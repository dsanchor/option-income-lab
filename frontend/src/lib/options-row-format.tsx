"use client";

// Shared row-level presentation helpers for Best Options / Options Screener
// tables (both render the same per-contract row shape produced by
// `backend/src/best_options.py`). Extracted out of `BestOptionsView.tsx` so
// the Options Screener view reuses these instead of a second, drifting copy
// (per the 2026-08-29 visual-consistency directive: reuse shared
// components/tokens rather than duplicated ad-hoc colour/formatting logic).

import { CheckCircle2, AlertTriangle, XCircle } from "lucide-react";
import { preferenceStyle } from "@/lib/badges";
import type { BestOptionColor } from "@/types/best-options";

export const COLOR_ICON: Record<BestOptionColor, typeof CheckCircle2> = {
  green: CheckCircle2,
  yellow: AlertTriangle,
  red: XCircle,
};

// Human labels for backend flags (design §4/§5). Colour is never derived
// from these — they render as neutral badges alongside the backend's own
// `color`/`label`.
export const FLAG_LABELS: Record<string, string> = {
  earnings_date_unknown: "Earnings date unknown",
  stale_quote: "Stale quote",
  very_short_dte: "Very short DTE (<7d)",
  exceeds_system_dte_cap: "Beyond agents' 45d cap",
  below_category_floor: "Below category premium floor",
  ex_div_within_dte: "Ex-dividend within DTE",
  no_shares_held: "No shares held",
  below_support: "Strike at/below support",
  insufficient_data: "Insufficient data for a score",
};

export function flagLabel(flag: string): string {
  return FLAG_LABELS[flag] ?? flag.replace(/_/g, " ");
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  return typeof v === "number" && isFinite(v) ? v.toFixed(digits) : "—";
}
export function fmtPct(v: number | null | undefined, digits = 2): string {
  return typeof v === "number" && isFinite(v) ? `${v.toFixed(digits)}%` : "—";
}
export function fmtExpiration(v: string): string {
  return v && v.length === 8 ? `${v.slice(0, 4)}-${v.slice(4, 6)}-${v.slice(6, 8)}` : v;
}
export function fmtTime(v: string | null): string {
  if (!v) return "—";
  const d = new Date(v);
  return isNaN(d.getTime()) ? v : d.toLocaleString();
}

/** Colour + icon + text label, never colour alone (design §4.4). */
export function ColorBadge({ color, label }: { color: BestOptionColor; label: string }) {
  const Icon = COLOR_ICON[color] ?? AlertTriangle;
  const style = preferenceStyle(color);
  return (
    <span
      className="inline-flex items-center gap-1 whitespace-nowrap rounded-[var(--radius-pill)] border px-2 py-0.5 text-xs font-medium"
      style={{ color: style.color, background: style.bg, borderColor: style.border }}
    >
      <Icon aria-hidden="true" size={13} />
      {label}
    </span>
  );
}

export function GateBadge({ label, status }: { label: string; status: string }) {
  const tone = status === "pass" ? "green" : status === "unknown" ? "yellow" : "red";
  const style = preferenceStyle(tone);
  return (
    <span
      className="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide"
      style={{ color: style.color, background: style.bg, borderColor: style.border }}
    >
      {label}: {status}
    </span>
  );
}
