"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw, Trophy } from "lucide-react";
import BestOptionsParams from "@/components/BestOptionsParams";
import ContractValidationAction from "@/components/ContractValidationAction";
import { preferenceRowTint } from "@/lib/badges";
import { ColorBadge, GateBadge, flagLabel, fmtNum, fmtPct, fmtExpiration, fmtTime, calcGap, fmtGap, fmtGapPct } from "@/lib/options-row-format";
import type {
  BestOptionRow,
  BestOptionsResponse,
  BestOptionsSide,
} from "@/types/best-options";

function RowDetails({ row }: { row: BestOptionRow }) {
  const compEntries = Object.entries(row.components ?? {});
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
      <strong>Nearest miss — why nothing (more) qualifies:</strong>{" "}
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
  underlyingPrice,
}: {
  symbol: string;
  side: "call" | "put";
  data: BestOptionsSide;
  underlyingPrice: number | null;
}) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const toggle = (i: number) =>
    setExpanded((s) => {
      const next = new Set(s);
      if (next.has(i)) next.delete(i);
      else next.add(i);
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
          <span className="font-mono">{data.rows.length} of {data.total}</span>
          {data.truncated ? <span className="text-text-muted"> (capped at 400)</span> : null}
        </span>
        <span>
          <span className="text-text-muted">Excluded by delta band:</span>{" "}
          <span className="font-mono">{data.excluded_by_delta_band}</span>
        </span>
      </div>

      {noSharesHeld && (
        <div role="note" className="rounded-[var(--radius)] border border-accent-orange/40 bg-accent-orange/10 px-3 py-2 text-xs text-accent-orange">
          0 shares held — no covered call is currently coverable. Every candidate contract is still shown below for
          reference.
        </div>
      )}

      <NearestMissPanel nearestMiss={data.nearest_miss} />

      {data.rows.length === 0 ? (
        <p className="text-sm text-text-muted">No contracts in the configured DTE window.</p>
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
                <th className="border-b border-border px-2 py-1 text-left">Exp</th>
                <th className="border-b border-border px-2 py-1 text-right">DTE</th>
                <th className="border-b border-border px-2 py-1 text-right">Strike</th>
                <th className="border-b border-border px-2 py-1 text-right" title="Gap from analyzed price">Gap $</th>
                <th className="border-b border-border px-2 py-1 text-right" title="Gap from analyzed price (%)">Gap %</th>
                <th className="border-b border-border px-2 py-1 text-right">Bid/Ask</th>
                <th className="border-b border-border px-2 py-1 text-right">Δ</th>
                <th className="border-b border-border px-2 py-1 text-right">Prem %</th>
                <th className="border-b border-border px-2 py-1 text-right">Ann. %</th>
                <th className="border-b border-border px-2 py-1 text-right">OI</th>
                <th className="border-b border-border px-2 py-1 text-right">Score</th>
                <th className="border-b border-border px-2 py-1 text-left">Flags</th>
                <th className="border-b border-border px-2 py-1 text-left" title="Validate contract — advisory only, no auto-order">Validate</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row, i) => {
                const open = expanded.has(i);
                const { gap, gapPct } = calcGap(row.strike, underlyingPrice);
                return (
                  <Fragment key={`${row.expiration}-${row.strike}-${i}`}>
                    <tr
                      className="border-b border-border/40"
                      style={{ background: preferenceRowTint(row.color) }}
                    >
                      <td className="px-2 py-1 text-left">
                        <button
                          type="button"
                          onClick={() => toggle(i)}
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
                      <td className="px-2 py-1 text-left font-mono">{fmtExpiration(row.expiration)}</td>
                      <td className="px-2 py-1 text-right font-mono">{row.dte}</td>
                      <td className="px-2 py-1 text-right font-mono font-semibold">{fmtNum(row.strike)}</td>
                      <td className="px-2 py-1 text-right font-mono text-text-muted" title={gap !== null && underlyingPrice !== null ? `Strike ${fmtNum(row.strike)} vs analyzed ${fmtNum(underlyingPrice)}` : undefined}>
                        {fmtGap(gap)}
                      </td>
                      <td className="px-2 py-1 text-right font-mono text-text-muted">
                        {fmtGapPct(gapPct)}
                      </td>
                      <td className="px-2 py-1 text-right font-mono text-text-muted">
                        {fmtNum(row.bid)} / {fmtNum(row.ask)}
                      </td>
                      <td className="px-2 py-1 text-right font-mono text-text-muted" title={`|Δ| ${fmtNum(row.abs_delta, 3)}`}>
                        {fmtNum(row.delta, 3)}
                      </td>
                      <td className="px-2 py-1 text-right font-mono font-semibold" title={`Floor ${fmtPct(row.effective_min_pct)}`}>
                        {fmtPct(row.premium_pct)}
                      </td>
                      <td className="px-2 py-1 text-right font-mono">{fmtPct(row.annualized_return_pct)}</td>
                      <td className="px-2 py-1 text-right font-mono text-text-muted">{row.open_interest ?? "—"}</td>
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
                          <RowDetails row={row} />
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
 */
export default function BestOptionsView({ symbol }: { symbol: string }) {
  const [state, setState] = useState<ViewState>({ kind: "loading" });
  const [refreshing, setRefreshing] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch(
        `/api/symbols/${encodeURIComponent(symbol)}/best-options?side=both`,
        { cache: "no-store" },
      );
      const body = await res.json().catch(() => ({}));
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
      setRefreshing(false);
    } catch (err) {
      setState({ kind: "error", message: err instanceof Error ? err.message : "Failed to load Best Options" });
      setRefreshing(false);
    }
  }, [symbol]);

  useEffect(() => {
    load();
  }, [load]);

  // Manual retry/refresh affordances (buttons) are event handlers, not
  // effects, so flipping to the "loading" state here directly is safe --
  // unlike the initial mount effect above and the warming auto-retry timer
  // below, which intentionally never set state synchronously themselves.
  const retry = useCallback(() => {
    setState({ kind: "loading" });
    load();
  }, [load]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const res = await fetch(`/api/symbols/${encodeURIComponent(symbol)}/best-options/refresh`, {
        method: "POST",
        cache: "no-store",
      });
      const data = await res.json().catch(() => ({}));

      if (res.status === 409) {
        // Already refreshing - just poll
        console.log("Refresh already in progress");
      } else if (!res.ok) {
        console.error("Refresh failed:", data.error || "Unknown error");
        setState({ kind: "error", message: data.error || `Refresh failed: HTTP ${res.status}` });
        setRefreshing(false);
        return;
      }

      // 202 Accepted or 409 Conflict - start polling
      // Poll more aggressively at first, then back off
      let pollAttempt = 0;
      const maxPolls = 30; // Max 30 polls over ~60 seconds

      const pollForResult = async () => {
        pollAttempt++;
        try {
          const pollRes = await fetch(
            `/api/symbols/${encodeURIComponent(symbol)}/best-options?side=both`,
            { cache: "no-store" }
          );
          const pollBody = await pollRes.json().catch(() => ({}));

          // Check if refresh completed
          if (pollBody?.status === "ok" || pollBody?.status === "stale") {
            // Success - entry now available
            setState({ kind: "ok", data: pollBody });
            setRefreshing(false);
          } else if (pollBody?.cache?.refresh_error || pollBody?.cache?.chain_refresh_error) {
            // Refresh failed with error
            const error = pollBody.cache.refresh_error || pollBody.cache.chain_refresh_error;
            setState({ kind: "error", message: `Refresh failed: ${error}` });
            setRefreshing(false);
          } else if (pollBody?.status === "warming" && pollAttempt < maxPolls) {
            // Still warming - keep polling with backoff
            const delay = pollAttempt < 5 ? 2000 : pollAttempt < 10 ? 3000 : 4000;
            setTimeout(pollForResult, delay);
          } else if (pollAttempt >= maxPolls) {
            // Timeout - stop polling but don't error (might still be processing)
            setState({ kind: "warming", retryAfter: 15, reason: "Refresh in progress - still processing" });
            setRefreshing(false);
          } else {
            // Unexpected state - keep polling
            setTimeout(pollForResult, 3000);
          }
        } catch (err) {
          console.error("Poll failed:", err);
          if (pollAttempt < maxPolls) {
            setTimeout(pollForResult, 3000);
          } else {
            setRefreshing(false);
          }
        }
      };

      // Start polling after 2 seconds
      setTimeout(pollForResult, 2000);
    } catch (err) {
      console.error("Refresh request failed:", err);
      setState({ kind: "error", message: err instanceof Error ? err.message : "Network error" });
      setRefreshing(false);
    }
  }, [symbol, load]);

  useEffect(() => {
    if (state.kind !== "warming") return;
    timerRef.current = setTimeout(load, Math.max(1, state.retryAfter) * 1000);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [state, load]);

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
          onClick={state.kind === "ok" && !refreshing ? refresh : retry}
          disabled={state.kind === "loading" || (refreshing && state.kind === "ok")}
          className="inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] border border-border bg-bg-input px-3 py-1.5 text-xs text-text-muted transition hover:bg-bg-hover disabled:opacity-50"
        >
          <RefreshCw aria-hidden="true" size={13} className={(state.kind === "loading" || refreshing) ? "animate-spin" : ""} />
          {state.kind === "ok" ? "Refresh" : "Retry"}
        </button>
      </div>

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
            {state.reason || `No data cached yet for ${symbol} — a background refresh has started.`} Retrying automatically in{" "}
            {state.retryAfter}s.
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
            className="inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] border border-accent-red/40 bg-accent-red/10 px-3 py-1.5 text-xs text-accent-red transition hover:bg-accent-red/20"
          >
            <RefreshCw aria-hidden="true" size={13} /> Retry
          </button>
        </div>
      )}

      {state.kind === "ok" && (
        <>
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
          <OptionsTable symbol={symbol} side="call" data={state.data.calls} underlyingPrice={state.data.parameters.underlying.price} />
          <OptionsTable symbol={symbol} side="put" data={state.data.puts} underlyingPrice={state.data.parameters.underlying.price} />
        </>
      )}
    </div>
  );
}
