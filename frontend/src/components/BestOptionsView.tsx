"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw, Trophy } from "lucide-react";
import BestOptionsParams from "@/components/BestOptionsParams";
import ContractValidationAction from "@/components/ContractValidationAction";
import MultiSelect from "@/components/MultiSelect";
import { DEFAULT_PREFERENCES, LIMIT_OPTIONS, PREFERENCE_OPTIONS, OptionsNumericFilters, OptionsPagination, OptionsSideTabs, OptionsSortHeader, type NumericDraft } from "@/components/OptionsPresentation";
import { bestOptionIdentity, clampOptionsOffset, filterBestOptionsRows, initialBestOptionsDraft, parseBestOptionsFilters, sortBestOptionsRows, type BestOptionsSort } from "@/lib/best-options-presentation";
import type { ScreenerPreference, ScreenerSide, ScreenerSortDir } from "@/types/screener";
import { preferenceRowTint } from "@/lib/badges";
import { ColorBadge, GateBadge, flagLabel, fmtNum, fmtPct, fmtExpiration, fmtTime, calcGap, fmtGap, fmtGapPct } from "@/lib/options-row-format";
import type {
  BestOptionRow,
  BestOptionsResponse,
  BestOptionsSide,
} from "@/types/best-options";

function RowDetails({ row, underlyingPrice }: { row: BestOptionRow; underlyingPrice: number | null }) {
  const compEntries = Object.entries(row.components ?? {});
  const { gap } = calcGap(row.strike, underlyingPrice);
  return (
    <div className="space-y-2 border-t border-border/60 px-3 py-2 text-xs">
      <div className="flex flex-wrap gap-1.5">
        <GateBadge label="Tradability" status={row.gates.tradability} />
        <GateBadge label="Delta band" status={row.gates.delta_band} />
        <GateBadge label="Earnings" status={row.gates.earnings_span} />
      </div>
      {compEntries.length > 0 && (
        <div>
          <span className="font-medium text-text-muted">Score components</span>
          <div className="mt-1 grid grid-cols-2 gap-1 sm:grid-cols-4">
            {compEntries.map(([k, v]) => (
              <span
                key={k}
                className={row.components_missing?.includes(k) ? "text-text-muted line-through" : ""}
              >
                {k.replace(/_/g, " ")}: {typeof v === "number" ? v.toFixed(2) : "—"}
              </span>
            ))}
          </div>
          {row.weight_basis < 1 && (
            <div className="mt-1 text-text-muted">
              Weight basis: {(row.weight_basis * 100).toFixed(0)}% (renormalized —{" "}
              {row.components_missing.length} component(s) missing)
            </div>
          )}
        </div>
      )}
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-text-muted">
        <span>Gap $: {fmtGap(gap)}</span>
        <span>Signed Δ: {fmtNum(row.delta, 3)}</span>
        <span>
          Quote as of: {fmtTime(row.quote_asof)}
          {row.stale ? " (stale)" : ""}
        </span>
        {row.collateral != null && <span>Collateral: ${fmtNum(row.collateral, 0)}</span>}
        {row.effective_min_pct != null && <span>Effective floor: {fmtPct(row.effective_min_pct)}</span>}
        {row.effective_wait_pct != null && <span>Effective wait: {fmtPct(row.effective_wait_pct)}</span>}
      </div>
    </div>
  );
}

function NearestMissPanel({ nearestMiss }: { nearestMiss: BestOptionsSide["nearest_miss"] }) {
  if (!nearestMiss) return null;
  const { description, ...rest } = nearestMiss;
  const otherEntries = Object.entries(rest).filter(([, v]) => v !== null && v !== undefined);
  return (
    <div className="rounded-[var(--radius)] border border-accent-blue/40 bg-accent-blue/10 px-3 py-2 text-xs">
      <strong>Nearest miss — snapshot qualification detail:</strong>{" "}
      {typeof description === "string" && description.length > 0 ? (
        <span>{description}</span>
      ) : otherEntries.length > 0 ? (
        <span>
          {otherEntries.map(([k, v]) => `${k.replace(/_/g, " ")}: ${String(v)}`).join(" · ")}
        </span>
      ) : (
        <span className="text-text-muted">No detail supplied.</span>
      )}
    </div>
  );
}

const TABLE_COLS = 15;

function OptionsTable({
  symbol,
  side,
  data,
  rows,
  category,
  chainStale,
  underlyingPrice,
  sort,
  dir,
  onSort,
}: {
  symbol: string;
  side: "call" | "put";
  data: BestOptionsSide;
  rows: BestOptionRow[];
  category: string;
  chainStale: boolean;
  underlyingPrice: number | null;
  sort: BestOptionsSort;
  dir: ScreenerSortDir;
  onSort: (field: BestOptionsSort) => void;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const toggle = (identity: string) =>
    setExpanded((s) => {
      const next = new Set(s);
      if (next.has(identity)) next.delete(identity);
      else next.add(identity);
      return next;
    });

  // `no_shares_held` is a section-level field (`total_shares // 100 === 0`), never a
  // per-row flag -- design §5's "capital" row is explicit this is a page/section
  // banner, and `best_options.py` never sets it on any individual row.
  const noSharesHeld = side === "call" && data.no_shares_held === true;

  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        <h3 className="text-base font-semibold">{side === "call" ? "Covered Calls" : "Cash-Secured Puts"}</h3>
        <span>
          <span className="text-text-muted">Shown:</span>{" "}
          <span className="font-mono">{rows.length} · available snapshot: {data.rows.length} of {data.total}</span>
          {data.truncated ? <span className="text-text-muted"> (capped at 400)</span> : null}
        </span>
        <span>
          <span className="text-text-muted">Excluded by delta band:</span>{" "}
          <span className="font-mono">{data.excluded_by_delta_band}</span>
        </span>
      </div>

      {noSharesHeld && (
        <div role="note" className="rounded-[var(--radius)] border border-accent-orange/40 bg-accent-orange/10 px-3 py-2 text-xs text-accent-orange">
          0 shares held — no covered call is currently coverable. Candidate contracts remain available for
          reference, subject to the current filters.
        </div>
      )}

      <NearestMissPanel nearestMiss={data.nearest_miss} />

      {rows.length === 0 ? (
        <p className="text-sm text-text-muted">
          {data.rows.length === 0 ? "No contracts in the configured DTE window and delta bands." : "No contracts match the current filters."}
        </p>
      ) : (
        // Table structure/typography/spacing/row-tint intentionally mirrors
        // the Roll Scenarios table (`PositionDetail.tsx`'s `RollTableView`)
        // per the 2026-08-29 visual-consistency directive: `border-collapse
        // text-xs`, `border-b border-border` header rule (no separate card
        // frame around the table), `border-b border-border/40` body rows,
        // `px-2 py-1` cell padding, and the same shared `ROW_TINT_BG`
        // background-tint palette (`preferenceRowTint`) Roll Scenarios
        // paints its coloured cells with.
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr>
                <th className="border-b border-border px-2 py-1 text-left" aria-label="Expand" />
                <th className="border-b border-border px-2 py-1 text-left">Status</th>
                <th className="border-b border-border px-2 py-1 text-left">Symbol</th>
                {([
                  ["dte", "DTE"], ["abs_delta", "Δ"], ["premium_pct", "Prem %"],
                  ["annualized_return_pct", "Ann. %"], ["open_interest", "OI"],
                ] as const).map(([field, label]) => (
                  <OptionsSortHeader key={field} field={field} label={label} align="right" sort={sort} dir={dir} onSort={onSort} />
                ))}
                <OptionsSortHeader field="expiration" label="Exp" align="left" sort={sort} dir={dir} onSort={onSort} />
                <OptionsSortHeader field="strike" label="Strike" align="right" sort={sort} dir={dir} onSort={onSort} />
                <OptionsSortHeader field="gap_pct" label="Gap" align="right" sort={sort} dir={dir} onSort={onSort} />
                <th className="border-b border-border px-2 py-1 text-right">Bid/Ask</th>
                <OptionsSortHeader field="score" label="Score" align="right" sort={sort} dir={dir} onSort={onSort} />
                <th className="border-b border-border px-2 py-1 text-left">Flags</th>
                <th className="border-b border-border px-2 py-1 text-left" title="Validate contract — advisory only, no auto-order">Validate</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const identity = bestOptionIdentity(symbol, side, row);
                const open = expanded.has(identity);
                const { gap, gapPct } = calcGap(row.strike, underlyingPrice);
                return (
                  <Fragment key={identity}>
                    <tr
                      className="border-b border-border/40"
                      style={{ background: preferenceRowTint(row.color) }}
                    >
                      <td className="px-2 py-1 text-left">
                        <button
                          type="button"
                          onClick={() => toggle(identity)}
                          aria-expanded={open}
                          aria-label={open ? "Collapse row detail" : "Expand row detail"}
                          className="text-text-muted"
                        >
                          {open ? "▾" : "▸"}
                        </button>
                      </td>
                      <td className="px-2 py-1 text-left">
                        <ColorBadge color={row.color} label={row.label} />
                      </td>
                      <td className="px-2 py-1 text-left">
                        <span className="font-mono font-semibold text-accent-blue">{symbol}</span>
                        {category && <span className="ml-1 text-text-muted">({category})</span>}
                        {chainStale && (
                          <span title="This symbol's cached option chain is due for a refresh"
                            className="ml-1 rounded border border-accent-orange/40 bg-accent-orange/10 px-1 py-0.5 text-[10px] text-accent-orange">
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
                      <td className="px-2 py-1 text-right font-mono text-text-muted"
                        title={gap !== null && underlyingPrice !== null ? `Gap: ${fmtNum(gap, 2)} (${fmtGapPct(gapPct)}) | Strike ${fmtNum(row.strike)} vs analyzed ${fmtNum(underlyingPrice)}` : undefined}>
                        {fmtGapPct(gapPct)}
                      </td>
                      <td className="px-2 py-1 text-right font-mono text-text-muted">
                        {fmtNum(row.bid)} / {fmtNum(row.ask)}
                      </td>
                      <td className="px-2 py-1 text-right font-mono">
                        {row.score != null ? Math.round(row.score) : "—"}
                      </td>
                      <td className="px-2 py-1 text-left">
                        <div className="flex flex-wrap gap-1">
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
                          symbol={symbol}
                          side={side}
                          strike={row.strike}
                          expiration={row.expiration}
                          source="best_options"
                          displayedSnapshot={{
                            color: row.color,
                            score: row.score,
                            premium_pct: row.premium_pct,
                            annualized_return_pct: row.annualized_return_pct,
                          }}
                          compact
                        />
                      </td>
                    </tr>
                    {open && (
                      <tr className="border-b border-border/40">
                        <td colSpan={TABLE_COLS} className="p-0">
                          <RowDetails row={row} underlyingPrice={underlyingPrice} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function SnapshotResults({ symbol, response }: { symbol: string; response: BestOptionsResponse }) {
  const [side, setSide] = useState<ScreenerSide>("call");
  const [preferences, setPreferences] = useState<ScreenerPreference[]>(DEFAULT_PREFERENCES);
  const [draft, setDraft] = useState(() => initialBestOptionsDraft(response.parameters.dte));
  const [appliedDraft, setAppliedDraft] = useState(draft);
  const [limit, setLimit] = useState(100);
  const [offset, setOffset] = useState(0);
  const [sort, setSort] = useState<BestOptionsSort>("default");
  const [dir, setDir] = useState<ScreenerSortDir>("desc");
  const { error } = parseBestOptionsFilters(draft, response.parameters.dte);
  const applied = parseBestOptionsFilters(appliedDraft, response.parameters.dte);

  useEffect(() => {
    if (draft === appliedDraft || error) return;
    const timer = setTimeout(() => {
      setAppliedDraft(draft);
      setOffset(0);
    }, 400);
    return () => clearTimeout(timer);
  }, [draft, appliedDraft, error]);

  const data = side === "call" ? response.calls : response.puts;
  const price = response.parameters.underlying.price;
  const filtered = applied.error ? [] : filterBestOptionsRows(data.rows, preferences, applied.filters, price);
  const ordered = sortBestOptionsRows(filtered, sort, dir, price);
  const pageOffset = clampOptionsOffset(offset, limit, ordered.length);
  if (offset !== pageOffset) setOffset(pageOffset);
  const rows = ordered.slice(pageOffset, pageOffset + limit);
  const onSort = (field: BestOptionsSort) => {
    setDir(sort === field && dir === "desc" ? "asc" : "desc");
    setSort(field);
    setOffset(0);
  };

  return (
    <div className="space-y-6">
      <OptionsSideTabs side={side} onChange={(next) => { setSide(next); setOffset(0); }} />
      <div className="surface p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-base font-semibold">Filters</h2>
          <span className="rounded-[var(--radius-pill)] bg-bg-input px-2 py-0.5 text-xs text-text-muted">
            {filtered.length} matching · {data.rows.length} available in snapshot
          </span>
        </div>
        <div className="flex flex-wrap items-end gap-4">
          <div className="flex flex-col gap-1">
            <span className="text-xs text-text-muted">Category / Profile</span>
            <MultiSelect options={PREFERENCE_OPTIONS} selected={preferences} allLabel="All Profiles"
              onChange={(next) => { setPreferences(next as ScreenerPreference[]); setOffset(0); }} />
          </div>
          <OptionsNumericFilters draft={draft} onChange={(field: keyof NumericDraft, value) => {
            setDraft((prev) => ({ ...prev, [field]: value }));
          }} />
          <div className="flex flex-col gap-1">
            <span className="text-xs text-text-muted">Rows per page</span>
            <select aria-label="Rows per page" value={limit} onChange={(e) => { setLimit(Number(e.target.value)); setOffset(0); }}
              className="rounded-[var(--radius)] border border-border bg-bg-input px-3 py-1.5 text-sm text-text">
              {LIMIT_OPTIONS.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </div>
          <button type="button" onClick={() => { setSort("default"); setDir("desc"); setOffset(0); }}
            className="rounded-[var(--radius-pill)] border border-border bg-bg-input px-3 py-1.5 text-xs text-text-muted">
            Default order
          </button>
        </div>
        <p className="mt-3 text-xs text-text-muted">
          Filters narrow only {symbol}&apos;s available snapshot ({response.parameters.dte.min}–{response.parameters.dte.max} DTE and configured delta bands).
          They do not fetch more contracts or rerun generation. Refresh updates the snapshot.
        </p>
      </div>
      {(error || applied.error) && (
        <p role="alert" className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-3 py-2 text-xs text-accent-red">
          {error || applied.error}
        </p>
      )}
      {draft !== appliedDraft && !error && (
        <p role="status" className="text-xs text-text-muted">Applying filters…</p>
      )}
      {(response.status === "stale" || response.cache?.chain_stale || response.cache?.refreshing ||
        response.cache?.inputs_drift?.length || response.cache?.refresh_error || response.cache?.chain_refresh_error) && (
        <p role="status" className="rounded-[var(--radius)] border border-accent-orange/40 bg-accent-orange/10 px-3 py-2 text-xs text-accent-orange">
          {response.status === "stale" || response.cache?.chain_stale ? "Snapshot / chain is stale. " : ""}
          {response.cache?.refreshing ? "Server refresh is in progress; this is the previous snapshot. " : ""}
          {response.cache?.inputs_drift?.length ? `Inputs changed since generation: ${response.cache.inputs_drift.join(", ")}. ` : ""}
          {[response.cache?.refresh_error, response.cache?.chain_refresh_error].filter(Boolean).join("; ")}
        </p>
      )}
      <OptionsTable symbol={symbol} side={side} data={data} rows={rows}
        category={response.parameters.category.label} chainStale={response.cache?.chain_stale === true}
        underlyingPrice={price} sort={sort} dir={dir} onSort={onSort} />
      <OptionsPagination offset={pageOffset} limit={limit} total={filtered.length} returned={rows.length}
        hasMore={pageOffset + rows.length < filtered.length} onPage={setOffset} />
    </div>
  );
}

type ViewState =
  | { kind: "loading" }
  | { kind: "unavailable"; reason: string; nextRun?: string | null }
  | { kind: "warming"; retryAfter: number; reason?: string; nextRun?: string | null }
  | { kind: "error"; message: string }
  | { kind: "ok"; data: BestOptionsResponse };

/**
 * Client view for GET /api/symbols/{symbol}/best-options. Deterministic screen
 * only — this component never calls an LLM and never derives colour/score itself;
 * it renders exactly what the backend returned (design
 * .squad/decisions/inbox/danny-best-options-design.md).
 *
 * Cold-cache handling (design §7/F6): a `{status: "warming"}` 200 response renders
 * an explicit warming state with automatic retry (backend-supplied `retry_after`)
 * plus a manual "Retry now" affordance — never a hang, never treated as an error.
 *
 * Precomputed-only handling: `{status: "unavailable"}` renders the explicit
 * "not yet precomputed" state with next_run timestamp.
 *
 * Manual refresh waits for a new completed Best Options generation (not an
 * old "ok" envelope), retaining disabled tables until completion or failure.
 * It refreshes the options chain and screen, not Option Timing.
 */
export default function BestOptionsView({ symbol }: { symbol: string }) {
  return <BestOptionsContent key={symbol} symbol={symbol} />;
}

function BestOptionsContent({ symbol }: { symbol: string }) {
  const [state, setState] = useState<ViewState>({ kind: "loading" });
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const operationRef = useRef<AbortController | null>(null);
  const refreshingRef = useRef(false);

  const load = useCallback(async () => {
    if (refreshingRef.current) return;
    operationRef.current?.abort();
    const controller = new AbortController();
    operationRef.current = controller;
    try {
      const res = await fetch(
        `/api/symbols/${encodeURIComponent(symbol)}/best-options?side=both`,
        { cache: "no-store", signal: controller.signal },
      );
      const body = await res.json().catch(() => ({}));
      if (controller.signal.aborted) return;
      if (!res.ok) {
        setState({ kind: "error", message: body?.error || `HTTP ${res.status}` });
        return;
      }
      if (body?.status === "unavailable") {
        setState({ kind: "unavailable", reason: body.reason || "Not available", nextRun: body.next_run });
        return;
      }
      if (body?.status === "warming") {
        const retryAfter = typeof body.retry_after === "number" ? body.retry_after : 15;
        setState({ kind: "warming", retryAfter, reason: body.reason, nextRun: body.next_run });
        return;
      }
      if (body?.error) {
        setState({ kind: "error", message: body.error });
        return;
      }
      setState({ kind: "ok", data: body as BestOptionsResponse });
    } catch (err) {
      if (controller.signal.aborted) return;
      setState({ kind: "error", message: err instanceof Error ? err.message : "Failed to load Best Options" });
    }
  }, [symbol]);

  useEffect(() => {
    let active = true;
    void Promise.resolve().then(() => {
      if (active) void load();
    });
    return () => {
      active = false;
      operationRef.current?.abort();
      operationRef.current = null;
      refreshingRef.current = false;
    };
  }, [load]);

  // Manual retry/refresh affordances (buttons) are event handlers, not
  // effects, so flipping to the "loading" state here directly is safe --
  // unlike the initial mount effect above and the warming auto-retry timer
  // below, which intentionally never set state synchronously themselves.
  const retry = useCallback(() => {
    if (refreshingRef.current) return;
    setRefreshError(null);
    setState({ kind: "loading" });
    load();
  }, [load]);

  const refresh = useCallback(async () => {
    if (refreshingRef.current) return;
    refreshingRef.current = true;
    operationRef.current?.abort();
    if (timerRef.current) clearTimeout(timerRef.current);
    const controller = new AbortController();
    operationRef.current = controller;
    setRefreshing(true);
    setRefreshError(null);
    const deadline = setTimeout(() => controller.abort(), 120_000);
    controller.signal.addEventListener("abort", () => clearTimeout(deadline), { once: true });
    const url = `/api/symbols/${encodeURIComponent(symbol)}/best-options`;
    const read = async () => {
      const res = await fetch(`${url}?side=both`, {
        cache: "no-store", signal: controller.signal,
      });
      const body = await res.json();
      controller.signal.throwIfAborted();
      if (!res.ok) throw new Error(body?.error || `HTTP ${res.status}`);
      return body as BestOptionsResponse & { error?: string; reason?: string };
    };
    const pause = (ms: number) => new Promise<void>((resolve, reject) => {
      const onAbort = () => {
        clearTimeout(timer);
        reject(controller.signal.reason);
      };
      const timer = setTimeout(() => {
        controller.signal.removeEventListener("abort", onAbort);
        resolve();
      }, ms);
      controller.signal.addEventListener("abort", onAbort, { once: true });
      if (controller.signal.aborted) onAbort();
    });
    try {
      // Snapshot the server, not just displayed data: another refresh may have
      // completed since the table loaded. Old results can remain "ok" in flight.
      const baseline = (await read()).cache;
      const res = await fetch(`${url}/refresh`, {
        method: "POST",
        cache: "no-store",
        signal: controller.signal,
      });
      const data = await res.json().catch(() => ({}));
      controller.signal.throwIfAborted();
      if (res.status !== 409 && !res.ok) {
        throw new Error(data.error || `HTTP ${res.status}`);
      }
      for (let attempt = 0; attempt < 30; attempt++) {
        await pause(attempt < 5 ? 2000 : 3000);
        const body = await read();
        if (body.error) throw new Error(body.error);
        const cache = body.cache;
        const completed = cache?.refreshing === false &&
          !!cache.refresh_completed_at &&
          cache.refresh_completed_at !== baseline?.refresh_completed_at;
        if (!completed) continue;
        const errors = [...new Set([cache.refresh_error, cache.chain_refresh_error].filter(Boolean))];
        if (errors.length) throw new Error(errors.join("; "));
        if ((body.status === "ok" || body.status === "stale") &&
            (cache.generation ?? 0) > (baseline?.generation ?? 0)) {
          setState({ kind: "ok", data: body });
          return;
        }
      }
      throw new Error("Timed out waiting for fresh Best Options. The server may still be processing; try Refresh again.");
    } catch (err) {
      if (operationRef.current !== controller) return;
      setRefreshError(controller.signal.aborted
        ? "Refresh timed out. The server may still be processing; try Refresh again."
        : `Refresh failed: ${err instanceof Error ? err.message : "Network error"}`);
    } finally {
      clearTimeout(deadline);
      if (operationRef.current === controller) {
        refreshingRef.current = false;
        setRefreshing(false);
      }
    }
  }, [symbol]);

  useEffect(() => {
    if (state.kind !== "warming" || refreshing || refreshError) return;
    timerRef.current = setTimeout(load, Math.max(1, state.retryAfter) * 1000);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [state, load, refreshing, refreshError]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold">
            <Trophy size={22} className="text-accent-orange" aria-hidden /> {symbol} Best Options
          </h1>
          <p className="text-sm text-text-muted">
            Every option within the configured DTE window and delta bands.
          </p>
        </div>
        <button
          type="button"
          onClick={state.kind === "ok" ? refresh : retry}
          disabled={state.kind === "loading" || refreshing}
          className="inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] border border-border bg-bg-input px-3 py-1.5 text-xs text-text-muted transition hover:bg-bg-hover disabled:opacity-50"
        >
          <RefreshCw aria-hidden="true" size={13} className={(state.kind === "loading" || refreshing) ? "animate-spin" : ""} />
          {refreshing ? "Refreshing…" : state.kind === "ok" ? "Refresh" : "Retry"}
        </button>
      </div>

      {refreshing && (
        <p role="status" className="text-sm text-text-muted">
          Fetching the options chain and recalculating Best Options… Existing results are temporarily disabled.
        </p>
      )}
      {refreshError && (
        <p role="alert" className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-3 py-2 text-sm text-accent-red">
          {refreshError} {state.kind === "ok" && "Previous results are still shown; they have not been refreshed."}
        </p>
      )}

      {state.kind === "ok" && (
        <div className="rounded-[var(--radius)] border border-accent-blue/40 bg-accent-blue/10 px-3 py-2 text-xs text-accent-blue">
          🔍 <strong>Contract Validation</strong> — Click Validate to run an exact-contract agent review (Supervisor + Alpha). Advisory only; positions are never created automatically.
        </div>
      )}

      {state.kind === "loading" && (
        <div className="rounded-[var(--radius)] border border-border bg-bg-card px-4 py-12 text-center text-text-muted">
          <div className="mb-2 text-2xl">⏳</div>
          Loading…
        </div>
      )}

      {state.kind === "unavailable" && (
        <div role="status" className="space-y-3 rounded-[var(--radius)] border border-accent-blue/40 bg-accent-blue/10 px-4 py-8 text-center">
          <div className="text-2xl" aria-hidden>
            ⏸️
          </div>
          <p className="font-medium text-accent-blue">Best Options not yet precomputed</p>
          <p className="text-xs text-text-muted">
            {state.reason}
            {state.nextRun && ` Next scheduled processing: ${state.nextRun}.`}
          </p>
          <button
            type="button"
            onClick={refresh}
            disabled={refreshing}
            className="inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] bg-accent-blue px-3 py-1.5 text-xs font-medium text-white transition hover:opacity-90 disabled:opacity-50"
          >
            <RefreshCw aria-hidden="true" size={13} className={refreshing ? "animate-spin" : ""} /> Refresh Now
          </button>
        </div>
      )}

      {state.kind === "warming" && (
        <div role="status" className="space-y-3 rounded-[var(--radius)] border border-accent-orange/40 bg-accent-orange/10 px-4 py-8 text-center">
          <div className="text-2xl" aria-hidden>
            🔥
          </div>
          <p className="font-medium text-accent-orange">Warming up the option chain cache…</p>
          <p className="text-xs text-text-muted">
            {state.reason || `No data cached yet for ${symbol} — a background refresh has started.`}
            {!refreshing && !refreshError && ` Retrying automatically in ${state.retryAfter}s.`}
            {state.nextRun && ` Next scheduled processing: ${state.nextRun}.`}
          </p>
          <button
            type="button"
            onClick={refresh}
            disabled={refreshing}
            className="inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] bg-accent-orange px-3 py-1.5 text-xs font-medium text-white transition hover:opacity-90 disabled:opacity-50"
          >
            <RefreshCw aria-hidden="true" size={13} className={refreshing ? "animate-spin" : ""} /> Refresh Now
          </button>
        </div>
      )}

      {state.kind === "error" && (
        <div className="space-y-3 rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-4 text-sm">
          <p>⚠️ {state.message}</p>
          <button
            type="button"
            onClick={retry}
            disabled={refreshing}
            className="inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] border border-accent-red/40 bg-accent-red/10 px-3 py-1.5 text-xs text-accent-red transition hover:bg-accent-red/20"
          >
            <RefreshCw aria-hidden="true" size={13} /> Retry
          </button>
        </div>
      )}

      {state.kind === "ok" && (
        <div
          aria-busy={refreshing}
          inert={refreshing}
          className={`space-y-6 transition-opacity ${refreshing ? "pointer-events-none opacity-50" : ""}`}
        >
          <BestOptionsParams parameters={state.data.parameters} />
          <div className="rounded-[var(--radius)] border border-border bg-bg-card px-3 py-2 text-xs">
            <span className="font-medium text-text-muted">Analyzed underlying price:</span>{" "}
            <span className="font-mono font-semibold">
              ${fmtNum(state.data.parameters.underlying.price, 2)}
            </span>
            {state.data.parameters.underlying.source && (
              <span className="ml-2 text-text-muted">
                ({state.data.parameters.underlying.source})
              </span>
            )}
          </div>
          <SnapshotResults symbol={symbol} response={state.data} />
        </div>
      )}
    </div>
  );
}
