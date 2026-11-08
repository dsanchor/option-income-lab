"use client";

import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight } from "lucide-react";
import type { ScreenerPreference, ScreenerSide, ScreenerSortDir } from "@/types/screener";

export const PREFERENCE_OPTIONS = [
  { value: "Preferred", label: "Preferred" },
  { value: "Acceptable", label: "Acceptable" },
  { value: "Avoid", label: "Avoid" },
];
export const DEFAULT_PREFERENCES: ScreenerPreference[] = ["Preferred", "Acceptable"];
export const LIMIT_OPTIONS = [25, 50, 100, 250, 500];

export interface NumericDraft {
  minAnnualizedReturn: string;
  minAbsDelta: string;
  maxAbsDelta: string;
  minDte: string;
  maxDte: string;
  minOi: string;
  minGapPct: string;
  maxGapPct: string;
}

export function OptionsSideTabs({ side, onChange }: {
  side: ScreenerSide;
  onChange: (side: ScreenerSide) => void;
}) {
  return (
    <div role="tablist" aria-label="Option strategy" className="flex items-center gap-1 rounded-[var(--radius-pill)] border border-border bg-bg-card p-1">
      {(["call", "put"] as const).map((s) => (
        <button key={s} type="button" role="tab" aria-selected={side === s} onClick={() => onChange(s)}
          className={`rounded-[var(--radius-pill)] px-4 py-1.5 text-sm transition ${side === s ? "bg-accent-blue text-white" : "text-text-muted hover:text-text"}`}>
          {s === "call" ? "Covered Calls" : "Cash-Secured Puts"}
        </button>
      ))}
    </div>
  );
}

export function OptionsSortHeader<T extends string>({ field, label, align, sort, dir, onSort }: {
  field: T;
  label: string;
  align: "left" | "right";
  sort: T;
  dir: ScreenerSortDir;
  onSort: (field: T) => void;
}) {
  const active = sort === field;
  return (
    <th className={`border-b border-border px-2 py-1 text-${align}`} aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"}>
      <button type="button" onClick={() => onSort(field)}
        className={`inline-flex items-center gap-0.5 ${active ? "font-semibold text-text" : "text-text-muted hover:text-text"}`}>
        {label}
        {active && (dir === "asc" ? <ArrowUp size={11} /> : <ArrowDown size={11} />)}
      </button>
    </th>
  );
}

export function OptionsPagination({ offset, limit, total, returned, hasMore, onPage }: {
  offset: number;
  limit: number;
  total: number;
  returned: number;
  hasMore: boolean;
  onPage: (offset: number) => void;
}) {
  return (
    <div className="flex items-center justify-between text-xs text-text-muted">
      <span>{total === 0 ? "0 results" : `${offset + 1}–${offset + returned} of ${total}`}</span>
      <div className="flex items-center gap-2">
        <button type="button" onClick={() => onPage(Math.max(0, offset - limit))} disabled={offset === 0}
          className="inline-flex items-center gap-1 rounded-[var(--radius-pill)] border border-border bg-bg-input px-2.5 py-1 disabled:opacity-40">
          <ChevronLeft size={13} /> Prev
        </button>
        <button type="button" onClick={() => onPage(offset + limit)} disabled={!hasMore}
          className="inline-flex items-center gap-1 rounded-[var(--radius-pill)] border border-border bg-bg-input px-2.5 py-1 disabled:opacity-40">
          Next <ChevronRight size={13} />
        </button>
      </div>
    </div>
  );
}

export function OptionsNumericFilters({ draft, onChange }: {
  draft: NumericDraft;
  onChange: (field: keyof NumericDraft, value: string) => void;
}) {
  // Native number inputs sanitize invalid drafts (such as "1e") to "", indistinguishable from a clear.
  const input = (field: keyof NumericDraft, label: string, width: string, placeholder?: string) => (
    <input type="text" inputMode={field === "minDte" || field === "maxDte" || field === "minOi" ? "numeric" : "decimal"}
      value={draft[field]} onChange={(e) => onChange(field, e.target.value)} aria-label={label}
      placeholder={placeholder}
      className={`${width} rounded-[var(--radius)] border border-border bg-bg-input ${field === "minAnnualizedReturn" ? "px-3" : "px-2"} py-1.5 text-sm text-text`} />
  );
  return (
    <>
      <div className="flex flex-col gap-1">
        <span className="text-xs text-text-muted">Min ann. return %</span>
        {input("minAnnualizedReturn", "Minimum annualized return percentage", "w-28", "e.g. 15")}
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-xs text-text-muted">|Δ| range</span>
        <div className="flex items-center gap-1">
          {input("minAbsDelta", "Minimum absolute delta", "w-20", "min")}
          <span className="text-text-muted">–</span>
          {input("maxAbsDelta", "Maximum absolute delta", "w-20", "max")}
        </div>
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-xs text-text-muted">DTE range</span>
        <div className="flex items-center gap-1">
          {input("minDte", "Minimum DTE", "w-16")}
          <span className="text-text-muted">–</span>
          {input("maxDte", "Maximum DTE", "w-16")}
        </div>
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-xs text-text-muted">Min open interest</span>
        {input("minOi", "Minimum open interest", "w-24", "e.g. 100")}
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-xs text-text-muted">Min gap %</span>
        {input("minGapPct", "Minimum gap percentage (-100 to 200)", "w-24", "e.g. -10")}
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-xs text-text-muted">Max gap %</span>
        {input("maxGapPct", "Maximum gap percentage (-100 to 200)", "w-24", "e.g. 10")}
      </div>
    </>
  );
}
