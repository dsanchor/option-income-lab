export function usd(n: number | null | undefined): string {
  const v = typeof n === "number" && isFinite(n) ? n : 0;
  return v.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

export function pct(n: number | null | undefined, digits = 1): string {
  const v = typeof n === "number" && isFinite(n) ? n : 0;
  return `${v.toFixed(digits)}%`;
}

export function timeAgo(iso?: string | null): string {
  if (!iso) return "";
  const then = new Date(iso.replace("Z", "+00:00")).getTime();
  if (isNaN(then)) return "";
  const mins = Math.floor((Date.now() - then) / 60000);
  if (mins < 0) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  if (h < 24) return m ? `${h}h ${m}m ago` : `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}
