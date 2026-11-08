export function usd(n: number | null | undefined): string {
  const v = typeof n === "number" && isFinite(n) ? n : 0;
  return v.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

export function pct(n: number | null | undefined, digits = 1): string {
  const v = typeof n === "number" && isFinite(n) ? n : 0;
  return `${v.toFixed(digits)}%`;
}

/**
 * Average of the last `n` values, ignoring zero entries (e.g. months with no
 * activity yet). Returns null when there is nothing to average, so callers
 * can render a "—" instead of a misleading 0.00.
 */
export function averageLastNExcludingZero(values: (number | null | undefined)[], n = 12): number | null {
  const recent = values.slice(-n);
  const nonZero = recent.filter((v): v is number => typeof v === "number" && isFinite(v) && v !== 0);
  if (nonZero.length === 0) return null;
  return nonZero.reduce((sum, v) => sum + v, 0) / nonZero.length;
}

/**
 * Average available nonzero values in the last n completed local calendar
 * months. Missing months are not filled or replaced with older rows.
 */
export function averageCompletedMonthsExcludingZero<T extends { month: string | number; year?: number }>(
  rows: readonly T[],
  getValue: (row: T) => number | null | undefined,
  n: number,
  now = new Date(),
): number | null {
  if (!Number.isInteger(n) || n <= 0 || !Number.isFinite(now.getTime())) return null;
  const currentMonth = now.getFullYear() * 12 + now.getMonth();
  const values: number[] = [];
  for (const row of rows) {
    const match = typeof row.month === "string" ? /^(\d{4})-(\d{2})$/.exec(row.month) : null;
    const year = typeof row.month === "number" ? row.year : match ? Number(match[1]) : undefined;
    const month = typeof row.month === "number" ? row.month : match ? Number(match[2]) : NaN;
    if (typeof year !== "number" || !Number.isInteger(year) || year < 1 || year > 9999
      || !Number.isInteger(month) || month < 1 || month > 12) continue;
    const monthIndex = year * 12 + month - 1;
    if (monthIndex < currentMonth - n || monthIndex >= currentMonth) continue;
    const value = getValue(row);
    if (typeof value === "number" && Number.isFinite(value) && value !== 0) values.push(value);
  }
  return values.length === 0 ? null : values.reduce((sum, value) => sum + value, 0) / values.length;
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
