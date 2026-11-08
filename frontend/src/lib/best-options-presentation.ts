import { calcGap } from "@/lib/options-row-format";
import type { NumericDraft } from "@/components/OptionsPresentation";
import type { BestOptionRow, BestOptionsDte } from "@/types/best-options";
import type { ScreenerPreference, ScreenerSortDir } from "@/types/screener";

export type BestOptionsSort = "default" | "dte" | "abs_delta" | "premium_pct" |
  "annualized_return_pct" | "open_interest" | "expiration" | "strike" | "gap_pct" | "score";
export type NumericFilters = { [K in keyof NumericDraft]?: number };

export function initialBestOptionsDraft(window: BestOptionsDte): NumericDraft {
  return {
    minAnnualizedReturn: "", minAbsDelta: "", maxAbsDelta: "",
    minDte: String(window.min), maxDte: String(Math.max(window.min, Math.min(45, window.max))),
    minOi: "", minGapPct: "", maxGapPct: "",
  };
}

export function parseBestOptionsFilters(draft: NumericDraft, window: BestOptionsDte):
  { filters: NumericFilters; error: string | null } {
  const parsed = parseOptionsNumericFilters(draft, {
    min: window.min, max: Math.max(window.min, Math.min(45, window.max)),
  });
  if (parsed.error) return parsed;
  if (parsed.filters.minDte! < window.min || parsed.filters.maxDte! > window.max)
    return { filters: parsed.filters, error: `DTE filters must stay within this snapshot's configured ${window.min}–${window.max} day window.` };
  return parsed;
}

export function parseOptionsNumericFilters(draft: NumericDraft, dteDefaults: { min: number; max: number }):
  { filters: NumericFilters; error: string | null } {
  const filters: NumericFilters = {};
  for (const [key, text] of Object.entries(draft)) {
    const trimmed = text.trim();
    if (!trimmed) continue;
    if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(trimmed))
      return { filters, error: "Filters must contain finite numbers." };
    const value = Number(text);
    if (!Number.isFinite(value)) return { filters, error: "Filters must contain finite numbers." };
    filters[key as keyof NumericDraft] = value;
  }
  filters.minDte ??= dteDefaults.min;
  filters.maxDte ??= dteDefaults.max;
  for (const key of ["minAbsDelta", "maxAbsDelta"] as const) {
    if (filters[key] != null && (filters[key]! < 0 || filters[key]! > 1))
      return { filters, error: "|Δ| must be between 0 and 1." };
  }
  for (const key of ["minDte", "maxDte"] as const) {
    if (filters[key] != null && (filters[key]! < 0 || !Number.isInteger(filters[key])))
      return { filters, error: "DTE must be non-negative whole numbers." };
  }
  if (filters.minOi != null && filters.minOi < 0)
    return { filters, error: "Open interest must be non-negative." };
  for (const key of ["minGapPct", "maxGapPct"] as const) {
    if (filters[key] != null && (filters[key]! < -100 || filters[key]! > 200))
      return { filters, error: "Gap % must be between -100 and 200." };
  }
  for (const [lo, hi, label] of [
    ["minAbsDelta", "maxAbsDelta", "|Δ|"],
    ["minDte", "maxDte", "DTE"],
    ["minGapPct", "maxGapPct", "Gap %"],
  ] as const) {
    if (filters[lo] != null && filters[hi] != null && filters[lo]! > filters[hi]!)
      return { filters, error: `${label} minimum cannot exceed maximum.` };
  }
  return { filters, error: null };
}

function within(value: number | null, min?: number, max?: number): boolean {
  if (min == null && max == null) return true;
  return typeof value === "number" && Number.isFinite(value) &&
    (min == null || value >= min) && (max == null || value <= max);
}

export function filterBestOptionsRows(rows: BestOptionRow[], preferences: ScreenerPreference[],
  filters: NumericFilters, underlyingPrice: number | null): BestOptionRow[] {
  return rows.filter((row) =>
    (preferences.length === 0 || preferences.some((p) => p === row.label)) &&
    within(row.annualized_return_pct, filters.minAnnualizedReturn) &&
    within(row.abs_delta, filters.minAbsDelta, filters.maxAbsDelta) &&
    within(row.dte, filters.minDte, filters.maxDte) &&
    within(row.open_interest, filters.minOi) &&
    within(calcGap(row.strike, underlyingPrice).gapPct, filters.minGapPct, filters.maxGapPct));
}

export function sortBestOptionsRows(rows: BestOptionRow[], sort: BestOptionsSort,
  dir: ScreenerSortDir, underlyingPrice: number | null): BestOptionRow[] {
  // Default retains the evaluator's authoritative order, including its delta-fit tie-break.
  if (sort === "default") return rows;
  return [...rows].sort((a, b) => {
    if (sort === "expiration") {
      if (!a.expiration || !b.expiration) return !a.expiration === !b.expiration ? 0 : !a.expiration ? 1 : -1;
      const compared = a.expiration.replaceAll("-", "").localeCompare(b.expiration.replaceAll("-", ""));
      return dir === "asc" ? compared : -compared;
    }
    const value = (row: BestOptionRow) => sort === "gap_pct"
      ? calcGap(row.strike, underlyingPrice).gapPct : row[sort];
    const av = value(a), bv = value(b);
    const am = typeof av !== "number" || !Number.isFinite(av);
    const bm = typeof bv !== "number" || !Number.isFinite(bv);
    if (am || bm) return am === bm ? 0 : am ? 1 : -1;
    return dir === "asc" ? av! - bv! : bv! - av!;
  });
}

export function bestOptionIdentity(symbol: string, side: "call" | "put", row: BestOptionRow): string {
  return JSON.stringify([symbol, side, row.expiration.replaceAll("-", ""), row.strike]);
}

export function clampOptionsOffset(offset: number, limit: number, total: number): number {
  return Math.min(offset, Math.max(0, Math.ceil(total / limit) - 1) * limit);
}
