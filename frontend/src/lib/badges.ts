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
