// Shared badge/pill color helpers for dashboard + activity views.
// Returns inline style objects (no dependency on legacy global CSS classes).

type Style = { color: string; bg: string; border: string };

const C = {
  green: "var(--accent-green)",
  red: "var(--accent-red)",
  orange: "var(--accent-orange)",
  blue: "var(--accent-blue)",
  purple: "var(--accent-purple)",
  cyan: "var(--accent-cyan)",
  muted: "var(--text-muted)",
};

function tone(color: string): Style {
  return { color, bg: `color-mix(in srgb, ${color} 14%, transparent)`, border: `color-mix(in srgb, ${color} 35%, transparent)` };
}

export function styleFor(color: keyof typeof C): Style {
  return tone(C[color]);
}

/** Activity / decision badge (WAIT, SELL, HOLD, ALPHA_EXECUTED, …). */
export function activityStyle(activity?: string): Style {
  const a = (activity || "").toUpperCase();
  if (a === "SELL" || a === "ROLL") return tone(C.red);
  if (a === "WAIT" || a === "HOLD") return tone(C.orange);
  if (a.includes("ALPHA")) return tone(C.purple);
  if (a === "OPEN" || a === "BUY") return tone(C.green);
  return tone(C.blue);
}

/** Moneyness badge (ITM / ATM / OTM). */
export function moneynessStyle(m?: string): Style {
  const v = (m || "").toUpperCase();
  if (v === "ITM") return tone(C.red);
  if (v === "ATM") return tone(C.orange);
  if (v === "OTM") return tone(C.green);
  return tone(C.muted);
}

/** Assignment / generic risk badge (LOW / MEDIUM / HIGH / CRITICAL). */
export function riskStyle(r?: string): Style {
  const v = (r || "").toUpperCase();
  if (v === "LOW") return tone(C.green);
  if (v === "MEDIUM" || v === "MODERATE") return tone(C.orange);
  if (v === "HIGH" || v === "CRITICAL" || v === "VERY-HIGH") return tone(C.red);
  return tone(C.muted);
}

/** Best Options row preference (green/yellow/red from backend/src/best_options.py).
 * Colour is always paired with an icon + the backend's own text label
 * (Preferred/Acceptable/Avoid) by the consuming component — never colour alone. */
export function preferenceStyle(color?: string): Style {
  const v = (color || "").toLowerCase();
  if (v === "green") return tone(C.green);
  if (v === "yellow") return tone(C.orange);
  if (v === "red") return tone(C.red);
  return tone(C.muted);
}

/** Shared row/cell background tint palette. This is the exact same palette
 * the Roll Scenarios table (`PositionDetail.tsx`) paints its per-cell
 * backgrounds with — centralised here so every screen that needs a
 * "tinted row/cell by semantic colour" treatment (Roll Scenarios, Best
 * Options, and any future one) reads from one shared token instead of each
 * component re-declaring its own ad-hoc rgba map. */
export const ROW_TINT_BG: Record<string, string> = {
  green: "rgba(63,185,80,0.14)",
  orange: "rgba(240,136,62,0.14)",
  red: "rgba(248,81,73,0.14)",
  blue: "rgba(93,173,226,0.14)",
  purple: "rgba(188,140,255,0.14)",
};

/** Best Options row background tint — reuses `ROW_TINT_BG` (Roll Scenarios'
 * own palette) so a green/yellow/red Best Options row is painted with
 * exactly the same tint a green/orange/red Roll Scenarios cell already
 * uses. "yellow" maps to the shared "orange" bucket, matching how every
 * other WAIT/HOLD affordance in this app already renders backend "yellow"
 * (see `preferenceStyle` above; there is no `--accent-yellow` token). */
export function preferenceRowTint(color?: string): string {
  const v = (color || "").toLowerCase();
  if (v === "green") return ROW_TINT_BG.green;
  if (v === "yellow") return ROW_TINT_BG.orange;
  if (v === "red") return ROW_TINT_BG.red;
  return "transparent";
}

/** Confidence pill (high / medium / low). */
export function confidenceStyle(c?: string | number): Style {
  const v = String(c || "").toLowerCase();
  if (v === "high") return tone(C.green);
  if (v === "medium") return tone(C.orange);
  if (v === "low") return tone(C.muted);
  return tone(C.blue);
}

/** Risk rating 1-10 tier color. */
export function riskRatingStyle(n: number): Style {
  if (n <= 2) return tone(C.green);
  if (n <= 4) return tone(C.cyan);
  if (n <= 6) return tone(C.orange);
  if (n <= 8) return tone(C.red);
  return tone(C.red);
}

export function riskRatingLabel(n: number): string {
  if (n <= 2) return "low";
  if (n <= 4) return "moderate";
  if (n <= 6) return "elevated";
  if (n <= 8) return "high";
  return "very-high";
}

// ── Tailwind class-string helpers (shared by Symbols watchlist + DGI screener) ──

/** DGI dividend category pill classes (Aristocrat/Rising Star/Compounder/High Yield/Balanced). */
export function categoryClass(cat?: string): string {
  const c = (cat || "").toLowerCase();
  if (c === "aristocrat") return "text-accent-orange border-accent-orange/40 bg-accent-orange/10";
  if (c === "rising star") return "text-accent-cyan border-accent-cyan/40 bg-accent-cyan/10";
  if (c === "compounder") return "text-accent-purple border-accent-purple/40 bg-accent-purple/10";
  if (c === "high yield") return "text-accent-green border-accent-green/40 bg-accent-green/10";
  return "text-text-muted border-border bg-bg-input";
}

/** Entry-tag pill classes (Strong Buy/Buy/Accumulate/Wait). */
export function entryClass(tag?: string): string {
  const t = (tag || "").toLowerCase();
  if (t === "strong buy" || t === "buy") return "text-accent-green border-accent-green/40 bg-accent-green/10";
  if (t === "accumulate") return "text-accent-blue border-accent-blue/40 bg-accent-blue/10";
  if (t === "wait") return "text-accent-red border-accent-red/40 bg-accent-red/10";
  return "text-text-muted border-border bg-bg-input";
}
