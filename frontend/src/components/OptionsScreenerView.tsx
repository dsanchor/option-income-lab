"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RefreshCw, ListFilter, ChevronLeft, ChevronRight, ArrowUp, ArrowDown } from "lucide-react";
import MultiSelect, { type MultiSelectOption } from "@/components/MultiSelect";
import ContractValidationAction from "@/components/ContractValidationAction";
import { preferenceRowTint } from "@/lib/badges";
import { ColorBadge, flagLabel, fmtNum, fmtPct, fmtExpiration, calcGap, fmtGapPct } from "@/lib/options-row-format";
import type {
  ScreenerOptionRow,
  ScreenerOptionsApiResponse,
  ScreenerOptionsResponse,
  ScreenerPreference,
  ScreenerSide,
  ScreenerSortDir,
  ScreenerSortField,
  ShareStatus,
} from "@/types/screener";

const PREFERENCE_OPTIONS: MultiSelectOption[] = [
  { value: "Preferred", label: "Preferred" },
  { value: "Acceptable", label: "Acceptable" },
  { value: "Avoid", label: "Avoid" },
];
const SHARE_AVAILABILITY_OPTIONS: MultiSelectOption[] = [
  { value: "available", label: "✅ 100+ shares free" },
  { value: "shares_committed", label: "🔒 Shares committed" },
  { value: "no_shares", label: "⚠️ <100 total shares" },
];
const DEFAULT_PREFERENCES: ScreenerPreference[] = ["Preferred", "Acceptable"];
const DEFAULT_DTE_MIN = 0;
const DEFAULT_DTE_MAX = 45;
const DEFAULT_LIMIT = 100;
const LIMIT_OPTIONS = [25, 50, 100, 250, 500];
const DEBOUNCE_MS = 400;

/** Numeric filter draft — free-text so a user can clear/retype a field
 * without fighting a coerced value; parsed to a number (or `undefined` for
 * "unset") only once debounced into `applied` below. */
interface NumericDraft {
  minAnnualizedReturn: string;
  minAbsDelta: string;
  maxAbsDelta: string;
  minDte: string;
  maxDte: string;
  minOi: string;
  minGapPct: string;
  maxGapPct: string;
}

const EMPTY_DRAFT: NumericDraft = {
  minAnnualizedReturn: "",
  minAbsDelta: "",
  maxAbsDelta: "",
  minDte: String(DEFAULT_DTE_MIN),
  maxDte: String(DEFAULT_DTE_MAX),
  minOi: "",
  minGapPct: "",
  maxGapPct: "",
};

interface AppliedFilters {
  side: ScreenerSide;
  preferences: ScreenerPreference[];
  symbols: string[];
  minAnnualizedReturn?: number;
  minAbsDelta?: number;
  maxAbsDelta?: number;
  minDte: number;
  maxDte: number;
  minOi?: number;
  minGapPct?: number;
  maxGapPct?: number;
  /** Call-side only: empty = show all (MultiSelect "0 selected == all" convention). */
  shareAvailability: ShareStatus[];
  sort: ScreenerSortField;
  dir: ScreenerSortDir;
  offset: number;
  limit: number;
}

const DEFAULT_APPLIED: AppliedFilters = {
  side: "call",
  preferences: DEFAULT_PREFERENCES,
  symbols: [],
  minDte: DEFAULT_DTE_MIN,
  maxDte: DEFAULT_DTE_MAX,
  shareAvailability: [],
  sort: "default",
  dir: "desc",
  offset: 0,
  limit: DEFAULT_LIMIT,
};

function parseNum(v: string): number | undefined {
  const t = v.trim();
  if (!t) return undefined;
  const n = Number(t);
  return isFinite(n) ? n : undefined;
}

function buildQuery(applied: AppliedFilters): string {
  const p = new URLSearchParams();
  p.set("side", applied.side);
  // The MultiSelect widget follows this app's usual "0 selected == all"
  // convention (matching the Symbols filter). The backend/aggregator's own
  // convention differs: an explicit *empty* `preferences` list is honoured
  // literally as "show nothing" (`options_screener._normalize_preferences`),
  // which would silently contradict the widget's "All Profiles" label. Map
  // "0 selected" to all three explicit values so the UI's own promise ("all
  // profiles") is what actually gets requested.
  const effectivePreferences = applied.preferences.length > 0 ? applied.preferences : PREFERENCE_OPTIONS.map((o) => o.value);
  p.set("preferences", effectivePreferences.join(","));
  if (applied.symbols.length) p.set("symbols", applied.symbols.join(","));
  if (applied.minAnnualizedReturn != null) p.set("min_annualized_return_pct", String(applied.minAnnualizedReturn));
  if (applied.minAbsDelta != null) p.set("min_abs_delta", String(applied.minAbsDelta));
  if (applied.maxAbsDelta != null) p.set("max_abs_delta", String(applied.maxAbsDelta));
  p.set("dte_min", String(applied.minDte));
  p.set("dte_max", String(applied.maxDte));
  if (applied.minOi != null) p.set("min_open_interest", String(applied.minOi));
  if (applied.minGapPct != null) p.set("min_gap_pct", String(applied.minGapPct));
  if (applied.maxGapPct != null) p.set("max_gap_pct", String(applied.maxGapPct));
  // share_availability is call-only; empty selection = show all (omit param).
  if (applied.side === "call" && applied.shareAvailability.length > 0) {
    p.set("share_availability", applied.shareAvailability.join(","));
  }
  p.set("sort", applied.sort);
  p.set("dir", applied.dir);
  p.set("offset", String(applied.offset));
  p.set("limit", String(applied.limit));
  return p.toString();
}

type ViewState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; data: ScreenerOptionsResponse };

const SORT_COLUMNS: { field: ScreenerSortField; label: string; align: "left" | "right" }[] = [
  { field: "symbol", label: "Symbol", align: "left" },
  { field: "dte", label: "DTE", align: "right" },
  { field: "abs_delta", label: "Δ", align: "right" },
  { field: "premium_pct", label: "Prem %", align: "right" },
  { field: "annualized_return_pct", label: "Ann. %", align: "right" },
  { field: "open_interest", label: "OI", align: "right" },
];

function SortableHeader({
  field,
  label,
  align,
  applied,
  onSort,
}: {
  field: ScreenerSortField;
  label: string;
  align: "left" | "right";
  applied: AppliedFilters;
  onSort: (field: ScreenerSortField) => void;
}) {
  const active = applied.sort === field;
  return (
    <th className={`border-b border-border px-2 py-1 text-${align}`}>
      <button
        type="button"
        onClick={() => onSort(field)}
        className={`inline-flex items-center gap-0.5 ${active ? "font-semibold text-text" : "text-text-muted hover:text-text"}`}
      >
        {label}
        {active && (applied.dir === "asc" ? <ArrowUp size={11} /> : <ArrowDown size={11} />)}
      </button>
    </th>
  );
}

/**
 * Client view for GET /api/screener/options (approved directive:
 * `.squad/decisions/inbox/copilot-options-screener-approved.md`). Deterministic
 * aggregation only — this component never calls an LLM and never derives
 * colour/score/admission itself; it renders exactly what the backend
 * returned, and every row links out to that symbol's own Best Options page
 * (Symbol Detail -> Analyze) for the full single-symbol drill-down rather
 * than duplicating it here.
 *
 * Table structure/typography/spacing/row-tint intentionally mirrors the Roll
 * Scenarios table (and `BestOptionsView.tsx`'s own reuse of it) per the
 * 2026-08-29 visual-consistency directive — shared `ColorBadge`/
 * `preferenceRowTint`/format helpers from `@/lib/options-row-format` and
 * `@/lib/badges`, not a second ad-hoc colour scheme.
 */
export default function OptionsScreenerView() {
  const [applied, setAppliedState] = useState<AppliedFilters>(DEFAULT_APPLIED);
  const [draft, setDraft] = useState<NumericDraft>(EMPTY_DRAFT);
  const [filterError, setFilterError] = useState<string | null>(null);
  const [symbolOptions, setSymbolOptions] = useState<MultiSelectOption[]>([]);
  const [state, setState] = useState<ViewState>({ kind: "loading" });
  const draftTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Symbol whitelist choices come from the full tracked-symbol universe
  // (independent of the screener's own response, which only lists symbols
  // matching the CURRENT filter — using that here would shrink the
  // available options as a user narrows down).
  useEffect(() => {
    let cancelled = false;
    fetch("/api/symbols/overview")
      .then((res) => res.json())
      .then((body) => {
        if (cancelled) return;
        const rows = (body?.rows ?? []) as { symbol: string }[];
        setSymbolOptions(rows.map((r) => ({ value: r.symbol, label: r.symbol })));
      })
      .catch(() => {
        if (!cancelled) setSymbolOptions([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Any filter change other than pagination itself resets to page 1 —
  // otherwise a narrower result set could leave `offset` pointing past the
  // end of the new page count.
  const setFilter = useCallback((partial: Partial<AppliedFilters>) => {
    setAppliedState((prev) => ({ ...prev, ...partial, offset: 0 }));
  }, []);
  const setPage = useCallback((offset: number) => {
    setAppliedState((prev) => ({ ...prev, offset }));
  }, []);

  // Free-text numeric fields debounce into `applied` — every other control
  // (tabs, sort, preferences/symbols, pagination) applies immediately.
  useEffect(() => {
    if (draftTimer.current) clearTimeout(draftTimer.current);
    draftTimer.current = setTimeout(() => {
      // Validate gap filters
      const minGap = parseNum(draft.minGapPct);
      const maxGap = parseNum(draft.maxGapPct);

      // Bounds check [-100, 200]
      if (minGap != null && (minGap < -100 || minGap > 200)) {
        setFilterError("Min gap % must be between -100 and 200");
        return;
      }
      if (maxGap != null && (maxGap < -100 || maxGap > 200)) {
        setFilterError("Max gap % must be between -100 and 200");
        return;
      }

      // Min <= Max check
      if (minGap != null && maxGap != null && minGap > maxGap) {
        setFilterError("Min gap % cannot exceed max gap %");
        return;
      }

      setFilterError(null);
      setFilter({
        minAnnualizedReturn: parseNum(draft.minAnnualizedReturn),
        minAbsDelta: parseNum(draft.minAbsDelta),
        maxAbsDelta: parseNum(draft.maxAbsDelta),
        minDte: parseNum(draft.minDte) ?? DEFAULT_DTE_MIN,
        maxDte: parseNum(draft.maxDte) ?? DEFAULT_DTE_MAX,
        minOi: parseNum(draft.minOi),
        minGapPct: minGap,
        maxGapPct: maxGap,
      });
    }, DEBOUNCE_MS);
    return () => {
      if (draftTimer.current) clearTimeout(draftTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft]);

  const load = useCallback(async () => {
    try {
      const qs = buildQuery(applied);
      const res = await fetch(`/api/screener/options?${qs}`, { cache: "no-store" });
      const body: ScreenerOptionsApiResponse = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
      if (!res.ok || "error" in body) {
        setState({ kind: "error", message: "error" in body ? body.error : `HTTP ${res.status}` });
        return;
      }
      setState({ kind: "ok", data: body });
    } catch (err) {
      setState({ kind: "error", message: err instanceof Error ? err.message : "Failed to load Options Screener" });
    }
  }, [applied]);

  useEffect(() => {
    load();
  }, [load]);

  const retry = useCallback(() => {
    setState({ kind: "loading" });
    load();
  }, [load]);

  const onSort = useCallback(
    (field: ScreenerSortField) => {
      setAppliedState((prev) => ({
        ...prev,
        offset: 0,
        sort: field,
        // Repeat-clicking the active column flips direction; picking a new
        // column starts descending (the conventional "biggest first" default
        // for every numeric column here — annualized return, OI, DTE, etc).
        dir: prev.sort === field && prev.dir === "desc" ? "asc" : "desc",
      }));
    },
    [],
  );

  const readinessStatus = useMemo(() => {
    if (state.kind !== "ok") return null;
    const s = state.data.symbols;
    const total = s.total;
    const loaded = s.loaded;
    const loadedFresh = s.loaded_fresh;
    const loadedStale = s.loaded_stale;
    const pending = s.pending;
    const error = s.error;
    const nextRun = state.data.cache?.next_run;

    if (total === 0) return { level: "info", message: "No symbols configured." };
    if (loaded === 0 && total > 0) {
      return {
        level: "warning",
        message: `0 of ${total} symbols loaded — No symbols precomputed yet. Results appear after the next scheduled processing cycle${nextRun ? ` (next run ${nextRun})` : ""}.`
      };
    }
    if (loaded < total) {
      return {
        level: "warning",
        message: `${loaded} of ${total} symbols loaded${loadedStale > 0 ? ` (${loadedStale} from an earlier cycle)` : ""} — Showing only precomputed symbols. The remaining ${total - loaded} will be included after the next scheduled processing cycle${nextRun ? ` (next run ${nextRun})` : ""}.`
      };
    }
    if (loaded === total && total > 0) {
      return {
        level: "success",
        message: `${total} of ${total} symbols loaded${loadedStale > 0 ? ` (${loadedStale} from an earlier cycle)` : ""}`
      };
    }
    return null;
  }, [state]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-semibold">
          <ListFilter size={22} className="text-accent-blue" aria-hidden /> Options Screener
        </h1>
        <p className="text-sm text-text-muted">
          Every option under 45 DTE and within category delta bands, across every tracked symbol.
        </p>
      </div>

      {/* Calls/Puts tabs — every symbol stays on the Calls tab regardless of
          share count (design: "keep all symbols on Calls"); share_status
          renders as a per-row badge for symbols that can't currently cover,
          and a Share Availability filter widget (Calls tab only) lets the
          user narrow by availability state. */}
      <div className="flex items-center gap-1 rounded-[var(--radius-pill)] border border-border bg-bg-card p-1">
        {(["call", "put"] as ScreenerSide[]).map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => setFilter({ side: s })}
            className={`rounded-[var(--radius-pill)] px-4 py-1.5 text-sm transition ${
              applied.side === s ? "bg-accent-blue text-white" : "text-text-muted hover:text-text"
            }`}
          >
            {s === "call" ? "Covered Calls" : "Cash-Secured Puts"}
          </button>
        ))}
      </div>

      <div className="surface p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-base font-semibold">Filters</h2>
          {state.kind === "ok" && (
            <span className="rounded-[var(--radius-pill)] bg-bg-input px-2 py-0.5 text-xs text-text-muted">
              {state.data.pagination.total_matching} matching
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-end gap-4">
          <div className="flex flex-col gap-1">
            <span className="text-xs text-text-muted">Category / Profile</span>
            <MultiSelect
              options={PREFERENCE_OPTIONS}
              selected={applied.preferences}
              onChange={(next) => setFilter({ preferences: next as ScreenerPreference[] })}
              allLabel="All Profiles"
            />
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs text-text-muted">Symbols</span>
            <MultiSelect
              options={symbolOptions}
              selected={applied.symbols}
              onChange={(next) => setFilter({ symbols: next })}
              allLabel="All Symbols"
            />
          </div>
          {applied.side === "call" && (
            <div className="flex flex-col gap-1">
              <span className="text-xs text-text-muted">Share Availability</span>
              <MultiSelect
                options={SHARE_AVAILABILITY_OPTIONS}
                selected={applied.shareAvailability}
                onChange={(next) => setFilter({ shareAvailability: next as ShareStatus[] })}
                allLabel="Show All"
              />
            </div>
          )}
          <div className="flex flex-col gap-1">
            <span className="text-xs text-text-muted">Min ann. return %</span>
            <input
              type="number"
              inputMode="decimal"
              value={draft.minAnnualizedReturn}
              onChange={(e) => setDraft((d) => ({ ...d, minAnnualizedReturn: e.target.value }))}
              placeholder="e.g. 15"
              className="w-28 rounded-[var(--radius)] border border-border bg-bg-input px-3 py-1.5 text-sm text-text"
            />
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs text-text-muted">|Δ| range</span>
            <div className="flex items-center gap-1">
              <input
                type="number"
                inputMode="decimal"
                step="0.01"
                value={draft.minAbsDelta}
                onChange={(e) => setDraft((d) => ({ ...d, minAbsDelta: e.target.value }))}
                placeholder="min"
                className="w-20 rounded-[var(--radius)] border border-border bg-bg-input px-2 py-1.5 text-sm text-text"
              />
              <span className="text-text-muted">–</span>
              <input
                type="number"
                inputMode="decimal"
                step="0.01"
                value={draft.maxAbsDelta}
                onChange={(e) => setDraft((d) => ({ ...d, maxAbsDelta: e.target.value }))}
                placeholder="max"
                className="w-20 rounded-[var(--radius)] border border-border bg-bg-input px-2 py-1.5 text-sm text-text"
              />
            </div>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs text-text-muted">DTE range</span>
            <div className="flex items-center gap-1">
              <input
                type="number"
                inputMode="numeric"
                value={draft.minDte}
                onChange={(e) => setDraft((d) => ({ ...d, minDte: e.target.value }))}
                className="w-16 rounded-[var(--radius)] border border-border bg-bg-input px-2 py-1.5 text-sm text-text"
              />
              <span className="text-text-muted">–</span>
              <input
                type="number"
                inputMode="numeric"
                value={draft.maxDte}
                onChange={(e) => setDraft((d) => ({ ...d, maxDte: e.target.value }))}
                className="w-16 rounded-[var(--radius)] border border-border bg-bg-input px-2 py-1.5 text-sm text-text"
              />
            </div>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs text-text-muted">Min open interest</span>
            <input
              type="number"
              inputMode="numeric"
              value={draft.minOi}
              onChange={(e) => setDraft((d) => ({ ...d, minOi: e.target.value }))}
              placeholder="e.g. 100"
              className="w-24 rounded-[var(--radius)] border border-border bg-bg-input px-2 py-1.5 text-sm text-text"
            />
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs text-text-muted">Min gap %</span>
            <input
              type="number"
              inputMode="decimal"
              value={draft.minGapPct}
              onChange={(e) => setDraft((d) => ({ ...d, minGapPct: e.target.value }))}
              placeholder="e.g. -10"
              className="w-24 rounded-[var(--radius)] border border-border bg-bg-input px-2 py-1.5 text-sm text-text"
              aria-label="Minimum gap percentage (-100 to 200)"
            />
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs text-text-muted">Max gap %</span>
            <input
              type="number"
              inputMode="decimal"
              value={draft.maxGapPct}
              onChange={(e) => setDraft((d) => ({ ...d, maxGapPct: e.target.value }))}
              placeholder="e.g. 10"
              className="w-24 rounded-[var(--radius)] border border-border bg-bg-input px-2 py-1.5 text-sm text-text"
              aria-label="Maximum gap percentage (-100 to 200)"
            />
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs text-text-muted">Rows per page</span>
            <select
              value={applied.limit}
              onChange={(e) => setFilter({ limit: Number(e.target.value) })}
              className="rounded-[var(--radius)] border border-border bg-bg-input px-3 py-1.5 text-sm text-text"
            >
              {LIMIT_OPTIONS.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {filterError && (
        <div role="alert" className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-3 py-2 text-xs text-accent-red">
          {filterError}
        </div>
      )}

      {readinessStatus && readinessStatus.level !== "success" && (
        <div role="status" className={`rounded-[var(--radius)] border px-3 py-2 text-xs ${
          readinessStatus.level === "warning" ? "border-accent-orange/40 bg-accent-orange/10 text-accent-orange" :
          readinessStatus.level === "info" ? "border-accent-blue/40 bg-accent-blue/10 text-accent-blue" :
          "border-border bg-bg-card text-text-muted"
        }`}>
          {readinessStatus.message}
        </div>
      )}
      {readinessStatus && readinessStatus.level === "success" && (
        <div className="rounded-[var(--radius)] border border-border bg-bg-card px-3 py-2 text-xs text-text-muted">
          {readinessStatus.message}
        </div>
      )}

      {state.kind === "loading" && (
        <div className="rounded-[var(--radius)] border border-border bg-bg-card px-4 py-12 text-center text-text-muted">
          <div className="mb-2 text-2xl">⏳</div>
          Loading…
        </div>
      )}

      {state.kind === "error" && (
        <div className="space-y-3 rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-4 text-sm">
          <p>⚠️ {state.message}</p>
          <button
            type="button"
            onClick={retry}
            className="inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] border border-accent-red/40 bg-accent-red/10 px-3 py-1.5 text-xs text-accent-red transition hover:bg-accent-red/20"
          >
            <RefreshCw aria-hidden="true" size={13} /> Retry
          </button>
        </div>
      )}

      {state.kind === "ok" && (
        <>
          <div className="rounded-[var(--radius)] border border-accent-blue/40 bg-accent-blue/10 px-3 py-2 text-xs text-accent-blue">
            🔍 <strong>Contract Validation</strong> — Click Validate to run an exact-contract agent review (Supervisor + Alpha). Advisory only; positions are never created automatically.
          </div>
          <ResultsTable applied={applied} data={state.data} onSort={onSort} onPage={setPage} />
        </>
      )}
    </div>
  );
}

function ResultsTable({
  applied,
  data,
  onSort,
  onPage,
}: {
  applied: AppliedFilters;
  data: ScreenerOptionsResponse;
  onSort: (field: ScreenerSortField) => void;
  onPage: (offset: number) => void;
}) {
  const { rows, nearest_miss: nearestMiss, pagination, side } = data;

  return (
    <section className="space-y-2">
      {rows.length === 0 ? (
        <p className="text-sm text-text-muted">
          No contracts match the current filters.
          {nearestMiss.length > 0 && " See nearest-miss detail below."}
        </p>
      ) : (
        // Table structure/typography/spacing/row-tint intentionally mirrors
        // the Roll Scenarios table (`PositionDetail.tsx`'s `RollTableView`)
        // and `BestOptionsView.tsx`'s own reuse of it, per the 2026-08-29
        // visual-consistency directive.
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr>
                <th className="border-b border-border px-2 py-1 text-left">Status</th>
                {SORT_COLUMNS.map((c) => (
                  <SortableHeader key={c.field} field={c.field} label={c.label} align={c.align} applied={applied} onSort={onSort} />
                ))}
                <th className="border-b border-border px-2 py-1 text-left">Exp</th>
                <th className="border-b border-border px-2 py-1 text-right">Strike</th>
                <th className="border-b border-border px-2 py-1 text-right" title="Gap from analyzed price">Gap</th>
                <th className="border-b border-border px-2 py-1 text-right">Bid/Ask</th>
                <th className="border-b border-border px-2 py-1 text-right">Score</th>
                <th className="border-b border-border px-2 py-1 text-left">Flags</th>
                <th className="border-b border-border px-2 py-1 text-left" title="Validate contract — advisory only, no auto-order">Validate</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row: ScreenerOptionRow, i) => {
                const { gap, gapPct } = calcGap(row.strike, row.underlying_price);
                const gapTooltip = gap !== null && row.underlying_price !== null
                  ? `Gap: ${fmtNum(gap, 2)} (${fmtGapPct(gapPct)}) | Strike ${fmtNum(row.strike)} vs analyzed ${fmtNum(row.underlying_price)}`
                  : undefined;
                return (
                <tr
                  key={`${row.symbol}-${row.expiration}-${row.strike}-${i}`}
                  className="border-b border-border/40"
                  style={{ background: preferenceRowTint(row.color) }}
                >
                  <td className="px-2 py-1 text-left">
                    <ColorBadge color={row.color} label={row.label} />
                  </td>
                  <td className="px-2 py-1 text-left">
                    <Link
                      href={`/symbols/${encodeURIComponent(row.symbol)}/best-options`}
                      className="font-mono font-semibold text-accent-blue no-underline hover:underline"
                    >
                      {row.symbol}
                    </Link>
                    {row.category && <span className="ml-1 text-text-muted">({row.category})</span>}
                    {row.chain_stale && (
                      <span
                        title="This symbol's cached option chain is due for a refresh"
                        className="ml-1 rounded border border-accent-orange/40 bg-accent-orange/10 px-1 py-0.5 text-[10px] text-accent-orange"
                      >
                        stale
                      </span>
                    )}
                  </td>
                  <td className="px-2 py-1 text-right font-mono">{row.dte}</td>
                  <td className="px-2 py-1 text-right font-mono text-text-muted" title={`Δ ${fmtNum(row.delta, 3)}`}>
                    {fmtNum(row.abs_delta, 3)}
                  </td>
                  <td className="px-2 py-1 text-right font-mono font-semibold" title={`Floor ${fmtPct(row.effective_min_pct)}`}>
                    {fmtPct(row.premium_pct)}
                  </td>
                  <td className="px-2 py-1 text-right font-mono">{fmtPct(row.annualized_return_pct)}</td>
                  <td className="px-2 py-1 text-right font-mono text-text-muted">{row.open_interest ?? "—"}</td>
                  <td className="px-2 py-1 text-left font-mono">{fmtExpiration(row.expiration)}</td>
                  <td className="px-2 py-1 text-right font-mono font-semibold">{fmtNum(row.strike)}</td>
                  <td className="px-2 py-1 text-right font-mono text-text-muted" title={gapTooltip}>
                    {fmtGapPct(gapPct)}
                  </td>
                  <td className="px-2 py-1 text-right font-mono text-text-muted">
                    {fmtNum(row.bid)} / {fmtNum(row.ask)}
                  </td>
                  <td className="px-2 py-1 text-right font-mono">{row.score != null ? Math.round(row.score) : "—"}</td>
                  <td className="px-2 py-1 text-left">
                    <div className="flex flex-wrap gap-1">
                      {row.share_status === "shares_committed" && (
                        <span
                          title={`${row.active_call_count ?? 0} active call(s) covering ${row.committed_shares ?? 0} shares — ${row.free_lots ?? 0} free lot(s)`}
                          className="rounded border border-accent-orange/40 bg-accent-orange/10 px-1 py-0.5 text-[10px] text-accent-orange"
                        >
                          🔒 Shares committed
                        </span>
                      )}
                      {row.share_status === "no_shares" && (
                        <span
                          title={`${row.total_shares ?? 0} shares held — need 100 for a covered call`}
                          className="rounded border border-accent-orange/40 bg-accent-orange/10 px-1 py-0.5 text-[10px] text-accent-orange"
                        >
                          ⚠️ No shares
                        </span>
                      )}
                      {row.flags.map((f) => (
                        <span
                          key={f}
                          title={flagLabel(f)}
                          className="rounded border border-border bg-bg-input px-1 py-0.5 text-[10px] text-text-muted"
                        >
                          {flagLabel(f)}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-2 py-1 text-left">
                    <ContractValidationAction
                      symbol={row.symbol}
                      side={side}
                      strike={row.strike}
                      expiration={row.expiration}
                      source="options_screener"
                      displayedSnapshot={{
                        color: row.color,
                        score: row.score,
                        premium_pct: row.premium_pct,
                        annualized_return_pct: row.annualized_return_pct,
                        category: row.category,
                      }}
                      compact
                    />
                  </td>
                </tr>
              );
              })}
            </tbody>
          </table>
        </div>
      )}

      {nearestMiss.length > 0 && (
        <details className="rounded-[var(--radius)] border border-accent-blue/40 bg-accent-blue/10 px-3 py-2 text-xs">
          <summary className="cursor-pointer select-none font-medium">
            Nearest miss — {nearestMiss.length} symbol(s) with zero qualifying rows
          </summary>
          <ul className="mt-2 space-y-1">
            {nearestMiss.map((m, i) => (
              <li key={`${m.symbol}-${i}`}>
                <span className="font-mono font-semibold">{m.symbol}</span>
                {m.category ? ` (${m.category})` : ""} —{" "}
                {typeof m.description === "string" && m.description.length > 0
                  ? m.description
                  : `${m.missed_gate ?? "unknown gate"}${m.missed_by != null ? `, missed by ${m.missed_by}` : ""}`}
              </li>
            ))}
          </ul>
        </details>
      )}

      <div className="flex items-center justify-between text-xs text-text-muted">
        <span>
          {pagination.total_matching === 0
            ? "0 results"
            : `${pagination.offset + 1}–${pagination.offset + pagination.returned} of ${pagination.total_matching}`}
        </span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => onPage(Math.max(0, pagination.offset - pagination.limit))}
            disabled={pagination.offset === 0}
            className="inline-flex items-center gap-1 rounded-[var(--radius-pill)] border border-border bg-bg-input px-2.5 py-1 disabled:opacity-40"
          >
            <ChevronLeft size={13} /> Prev
          </button>
          <button
            type="button"
            onClick={() => onPage(pagination.offset + pagination.limit)}
            disabled={!pagination.has_more}
            className="inline-flex items-center gap-1 rounded-[var(--radius-pill)] border border-border bg-bg-input px-2.5 py-1 disabled:opacity-40"
          >
            Next <ChevronRight size={13} />
          </button>
        </div>
      </div>
    </section>
  );
}
