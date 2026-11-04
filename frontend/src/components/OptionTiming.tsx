"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CartesianGrid, ReferenceArea, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from "recharts";
import AccessibleDialog from "@/components/AccessibleDialog";
import { fmtTime } from "@/lib/options-row-format";
import { timeAgo } from "@/lib/format";
import { decodeSymbolParam } from "@/lib/symbolEncoding";
import { renderMarkdown } from "@/lib/markdown";
import { canExplainOptionTiming, fetchOptionTimingExplain, fetchOptionTimingHistory, OPTION_TIMING_SIDES, optionTimingPoints, optionTimingScore } from "@/lib/optionTiming";
import type { OptionTimingBand, OptionTimingExplainResponse, OptionTimingPoint, OptionTimingSideKey, OptionTimingSnapshot } from "@/types/option-timing";

const BAND_CLASSES: Record<OptionTimingBand, string> = {
  red: "text-accent-red",
  orange: "text-accent-orange",
  yellow: "text-yellow-400",
  green: "text-accent-green",
};

const BAND_GRADIENTS: Record<OptionTimingBand, string> = {
  red: "linear-gradient(135deg,#ff4d5e,#a3123a)",
  orange: "var(--grad-warm)",
  yellow: "linear-gradient(135deg,#facc15,#ff9416)",
  green: "var(--grad-green)",
};

export function OptionTimingValue({ snapshot, side }: {
  snapshot?: OptionTimingSnapshot | null;
  side: OptionTimingSideKey;
}) {
  const score = optionTimingScore(snapshot, side);
  const value = snapshot?.[side];
  const fresh = snapshot?.freshness === "fresh";
  return (
    <span className="inline-flex flex-wrap items-center justify-end gap-1.5">
      <span className={`font-mono ${fresh && score !== null && value?.band ? BAND_CLASSES[value.band] : "text-text-muted"}`}>{score === null ? "—" : score}</span>
      {snapshot?.provisional && <span className="rounded border border-accent-orange/40 px-1 text-[10px] text-accent-orange">PROVISIONAL</span>}
      {snapshot && !fresh && <span className="text-[10px] text-text-muted">{snapshot.freshness === "stale" ? "STALE" : "Freshness unknown"}</span>}
    </span>
  );
}

export function OptionTimingGauge({ snapshot, side }: {
  snapshot?: OptionTimingSnapshot | null;
  side: OptionTimingSideKey;
}) {
  const score = optionTimingScore(snapshot, side);
  const label = OPTION_TIMING_SIDES.find((item) => item.key === side)!.label;
  const fresh = snapshot?.freshness === "fresh";
  const band = snapshot?.[side].band;
  const gradient = fresh && band ? BAND_GRADIENTS[band] : null;
  return (
    <span className="mt-2 block">
      <span className="block h-1.5 overflow-hidden rounded-full bg-border"
        role={score === null ? undefined : "meter"}
        aria-label={`${label} timing`}
        aria-valuemin={score === null ? undefined : 0}
        aria-valuemax={score === null ? undefined : 100}
        aria-valuenow={score ?? undefined}
        aria-valuetext={score === null ? undefined : `${score} / 100${fresh ? "" : ` · ${snapshot?.freshness ?? "unknown"}`}`}>
        {score !== null && <span aria-hidden="true" className="block h-full rounded-full bg-text-muted/40" style={{
          width: `${score}%`,
          ...(gradient ? { background: gradient } : {}),
        }} />}
      </span>
    </span>
  );
}

export function OptionTimingExplanation({ snapshot, side }: {
  snapshot?: OptionTimingSnapshot | null;
  side: OptionTimingSideKey;
}) {
  const value = snapshot?.[side];
  const score = optionTimingScore(snapshot, side);
  return (
    <div className="space-y-5 text-sm leading-relaxed">
      <div className="flex flex-wrap items-center gap-2">
        <OptionTimingValue snapshot={snapshot} side={side} />
        <span>{value?.label || (score === null ? "Unavailable" : "Technical context")}</span>
        {score !== null && value?.band && <span className="text-text-muted">Band: {value.band}</span>}
      </div>
      {score === null && (
        <p className="text-text-muted">
          {value?.status === "not_applicable" ? "Not applicable" : "Unavailable"}
          {value?.reason_codes.length ? `: ${value.reason_codes.join(", ").replace(/_/g, " ")}` : ": No persisted score available."}
        </p>
      )}
      {score !== null && !!value?.reason_codes.length && <p>Context: {value.reason_codes.join(", ").replace(/_/g, " ")}</p>}
      {snapshot && (
        <dl className="grid gap-4 rounded-[var(--radius)] border border-border bg-bg-input p-4 text-xs sm:grid-cols-2">
          <div><dt className="mb-2 text-text-muted">Source as of (local):</dt><dd>{snapshot.input_timestamp_available === false ? "Unavailable" : <><time dateTime={snapshot.as_of}>{fmtTime(snapshot.as_of)}</time> · {timeAgo(snapshot.as_of)}</>}</dd></div>
          <div><dt className="mb-2 text-text-muted">Source session:</dt><dd>{snapshot.input_timestamp_available === false ? "Unavailable" : snapshot.source_session}</dd></div>
          <div><dt className="mb-2 text-text-muted">Model:</dt><dd className="break-words">{snapshot.model_version} · Schema {snapshot.schema_version}</dd></div>
          <div><dt className="mb-2 text-text-muted">Snapshot:</dt><dd className="break-all">{snapshot.snapshot_id}</dd></div>
          <div className="sm:col-span-2"><dt className="mb-2 text-text-muted">Computed (local):</dt><dd>{fmtTime(snapshot.computed_at)} <span className="text-text-muted">{snapshot.input_timestamp_available === false ? "(availability response, not an input observation)" : "(not source freshness)"}</span></dd></div>
        </dl>
      )}
      {!!value?.factors.length && (
        <section aria-label="Factors" className="space-y-3">
          <h3 className="mb-3 font-medium">Factors</h3>
          {value.factors.map((factor) => (
            <div key={factor.key} className="rounded-[var(--radius)] border border-border bg-bg-input p-4">
              <h4 className="mb-2 font-medium">{factor.label}</h4>
              <div className="mb-3 flex flex-wrap gap-2 text-xs text-text-muted">
                <span className="rounded border border-border bg-bg-card px-2 py-1">Score {factor.score} / 100</span>
                <span className="rounded border border-border bg-bg-card px-2 py-1">Weight {factor.weight * 100}%</span>
                <span className="rounded border border-border bg-bg-card px-2 py-1">Contribution {factor.contribution}</span>
              </div>
              <ul className="list-disc space-y-2 pl-4 text-xs leading-relaxed [overflow-wrap:anywhere]">{factor.evidence.map((evidence, index) => <li key={index}>{evidence}</li>)}</ul>
            </div>
          ))}
        </section>
      )}
      {!!value?.adjustments.length && (
        <section aria-label="Guards and caps">
          <h3 className="mb-3 font-medium">Guards and caps</h3>
          <div className="space-y-3">
            {value.adjustments.map((adjustment) => <div key={adjustment.key} className="rounded-[var(--radius)] border border-border bg-bg-input p-4">
              <h4 className="mb-2 font-medium">{adjustment.label}</h4>
              <p className="text-xs leading-relaxed text-text-muted [overflow-wrap:anywhere]">{adjustment.reason}</p>
              <p className="mt-2 text-xs">{adjustment.cap === null ? "No numeric cap" : `Cap ${adjustment.cap}`}</p>
            </div>)}
          </div>
        </section>
      )}
    </div>
  );
}

type ExplainState = {
  kind: "explain";
  symbol: string;
  side: OptionTimingSideKey;
  snapshot: OptionTimingSnapshot;
  loading: boolean;
  result?: OptionTimingExplainResponse;
  error?: string;
};

export default function OptionTiming({ symbol, snapshot }: {
  symbol: string;
  snapshot?: OptionTimingSnapshot | null;
}) {
  const [popup, setPopup] = useState<ExplainState | { kind: "info"; symbol: string; side: OptionTimingSideKey } | null>(null);
  const canonicalSymbol = decodeSymbolParam(symbol);
  const requestRef = useRef<{ key: string; controller: AbortController } | null>(null);
  const cacheRef = useRef(new Map<string, { result: OptionTimingExplainResponse; expires: number }>());
  useEffect(() => () => {
    requestRef.current?.controller.abort();
    requestRef.current = null;
  }, [canonicalSymbol]);

  function close() {
    requestRef.current?.controller.abort();
    requestRef.current = null;
    setPopup(null);
  }

  const explain = useCallback(async (side: OptionTimingSideKey, saved: OptionTimingSnapshot | null | undefined) => {
    if (!canExplainOptionTiming(saved, side)) return;
    const key = JSON.stringify([canonicalSymbol, side, saved.snapshot_id]);
    if (requestRef.current?.key === key) return;
    requestRef.current?.controller.abort();
    requestRef.current = null;
    const cached = cacheRef.current.get(key);
    const next: ExplainState = { kind: "explain", symbol: canonicalSymbol, side, snapshot: saved, loading: true };
    if (cached && cached.expires > Date.now()) {
      setPopup({ ...next, loading: false, result: cached.result });
      return;
    }
    const controller = new AbortController();
    requestRef.current = { key, controller };
    setPopup(next);
    try {
      const result = await fetchOptionTimingExplain(canonicalSymbol, side, saved.snapshot_id, controller.signal);
      if (controller.signal.aborted || requestRef.current?.controller !== controller) return;
      if (result.score !== optionTimingScore(saved, side) || Date.parse(result.as_of) !== Date.parse(saved.as_of)) {
        throw new Error("The explanation does not match the saved technical score.");
      }
      for (const [cacheKey, entry] of cacheRef.current) {
        if (entry.expires <= Date.now()) cacheRef.current.delete(cacheKey);
      }
      if (cacheRef.current.size >= 10) cacheRef.current.delete(cacheRef.current.keys().next().value!);
      cacheRef.current.set(key, { result, expires: Date.now() + 5 * 60_000 });
      setPopup({ ...next, loading: false, result });
    } catch (error) {
      if (controller.signal.aborted || requestRef.current?.controller !== controller) return;
      setPopup({ ...next, loading: false, error: error instanceof Error ? error.message : "Could not explain Option Timing." });
    } finally {
      if (requestRef.current?.controller === controller) requestRef.current = null;
    }
  }, [canonicalSymbol]);

  return (
    <>
      <section aria-label="Option Timing" className="grid gap-3 sm:grid-cols-2">
        {OPTION_TIMING_SIDES.map((item) => (
          <div key={item.key} className="rounded-[var(--radius)] border border-border bg-bg-input p-3">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <span className="text-sm font-medium">{item.label}</span>
              <div className="flex gap-2">
                <button type="button" onClick={() => { close(); setPopup({ kind: "info", symbol: canonicalSymbol, side: item.key }); }} aria-label={`View ${item.label} timing details`} className="rounded-[var(--radius)] border border-border bg-bg-card px-2 py-1 text-xs text-accent-blue transition-colors hover:bg-bg-hover focus-visible:ring-2 focus-visible:ring-accent-blue">Info</button>
                <button type="button" onClick={() => void explain(item.key, snapshot)} disabled={!canExplainOptionTiming(snapshot, item.key)} aria-label={`Explain ${item.label} timing`} className="rounded-[var(--radius)] border border-border bg-bg-card px-2 py-1 text-xs text-accent-blue transition-colors hover:bg-bg-hover focus-visible:ring-2 focus-visible:ring-accent-blue disabled:cursor-not-allowed disabled:opacity-50">Explain</button>
              </div>
            </div>
            <span className="flex flex-wrap items-baseline gap-2">
              <span className="text-xl"><OptionTimingValue snapshot={snapshot} side={item.key} /></span>
              <span className="text-xs text-text-muted">/ 100</span>
            </span>
            {optionTimingScore(snapshot, item.key) === null && <span className="mt-2 block text-xs text-text-muted">{snapshot?.[item.key].status === "not_applicable" ? "Not applicable" : "Unavailable"}: {snapshot?.[item.key].reason_codes.join(", ").replace(/_/g, " ") || "no persisted score available"}</span>}
            <OptionTimingGauge snapshot={snapshot} side={item.key} />
          </div>
        ))}
      </section>
      {popup?.symbol === canonicalSymbol && (popup.kind === "info"
        ? <OptionTimingModal symbol={symbol} snapshot={snapshot} initialSide={popup.side} onClose={close} />
        : <OptionTimingExplainModal state={popup} onClose={close} onRetry={() => void explain(popup.side, popup.snapshot)} />)}
    </>
  );
}

export function OptionTimingExplainModal({ state, onClose, onRetry }: {
  state: ExplainState;
  onClose: () => void;
  onRetry: () => void;
}) {
  const titleId = useId();
  const item = OPTION_TIMING_SIDES.find((item) => item.key === state.side)!;
  const result = state.result;
  const snapshot = state.snapshot;
  const freshness = snapshot.freshness === "stale" || result?.freshness === "stale"
    ? "stale" : snapshot.freshness === "unknown" || result?.freshness === "unknown" ? "unknown" : "fresh";
  return (
    <AccessibleDialog titleId={titleId} onClose={onClose}>
      <div className="mb-3 flex items-start justify-between gap-4 border-b border-border pb-4">
        <div>
          <h2 id={titleId} className="text-lg font-semibold">{state.symbol} · {item.short} Explain</h2>
          <p className="mt-2 text-xs text-text-muted">AI explanation of the saved technical score</p>
        </div>
        <button type="button" autoFocus onClick={onClose} aria-label="Close Option Timing explanation" className="rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 text-sm transition-colors hover:bg-bg-hover">Close</button>
      </div>
      <div className="space-y-4">
        <section aria-label="Saved technical score" className="rounded-[var(--radius)] border border-border bg-bg-input p-4">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <OptionTimingValue snapshot={{ ...snapshot, freshness, provisional: snapshot.provisional || !!result?.provisional }} side={state.side} />
            <span className="text-xs text-text-muted">/ 100</span>
          </div>
          <p className="text-xs text-text-muted">Source as of (local): <time dateTime={snapshot.as_of}>{fmtTime(snapshot.as_of)}</time></p>
          {freshness === "stale" && <p className="mt-3 text-xs text-accent-orange">Last valid saved score; source data is stale. This explanation is not a fresh score.</p>}
          {freshness === "unknown" && <p className="mt-3 text-xs text-text-muted">Source freshness is unknown.</p>}
        </section>
        <section aria-label="AI interpretation" aria-live="polite" className="rounded-[var(--radius)] border border-border bg-bg-card p-4">
          {state.loading && <p role="status" className="text-sm text-text-muted">Generating explanation…</p>}
          {state.error && <div role="alert" className="space-y-3 text-sm">
            <p className="text-accent-red">{state.error}</p>
            <button type="button" disabled={state.loading} onClick={onRetry} className="rounded-[var(--radius)] border border-border bg-bg-input px-3 py-1.5 text-xs hover:bg-bg-hover disabled:opacity-50">Retry</button>
          </div>}
          {result && <>
            <div className="text-sm leading-relaxed [&_a]:text-accent-blue [&_strong]:text-text" dangerouslySetInnerHTML={{ __html: renderMarkdown(result.reply) }} />
            <p className="mt-4 border-t border-border pt-3 text-xs text-text-muted">{result.model} · {result.provider} · AI generated (local): <time dateTime={result.generated_at}>{fmtTime(result.generated_at)}</time></p>
          </>}
        </section>
      </div>
    </AccessibleDialog>
  );
}

export function OptionTimingModal({ symbol, snapshot, initialSide, onClose }: {
  symbol: string;
  snapshot?: OptionTimingSnapshot | null;
  initialSide: OptionTimingSideKey;
  onClose: () => void;
}) {
  const titleId = useId();
  const [side, setSide] = useState(initialSide);
  const [range, setRange] = useState("90d");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const history = useQuery({
    queryKey: ["option-timing-history", decodeSymbolParam(symbol), range, snapshot?.model_version ?? "unknown", snapshot?.snapshot_id ?? "none"],
    queryFn: ({ signal }) => fetchOptionTimingHistory(symbol, range, signal),
    staleTime: 60_000,
    retry: false,
  });
  const points = useMemo(() => optionTimingPoints(history.data, snapshot), [history.data, snapshot]);
  const selected = points.find((point) => point.snapshot_id === selectedId)
    ?? points.find((point) => point.snapshot_id === snapshot?.snapshot_id)
    ?? points.at(-1);
  const chartData = points.map((point) => ({ timestamp: Date.parse(point.as_of), score: optionTimingScore(point, side), point }));
  const groups = [...new Set(points.map((point) => `${point.model_version}:${point.history_kind}`))];

  function selectChartPoint(entry: unknown) {
    if (typeof entry !== "object" || entry === null || !("point" in entry)) return;
    const point = entry.point as OptionTimingPoint;
    if (point?.snapshot_id) setSelectedId(point.snapshot_id);
  }

  return (
    <AccessibleDialog titleId={titleId} onClose={onClose}>
      <div className="mb-6 flex items-start justify-between gap-4 border-b border-border pb-5">
        <h2 id={titleId} className="text-lg font-semibold">{symbol} · Option Timing</h2>
        <button type="button" autoFocus onClick={onClose} aria-label="Close Option Timing" className="rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 text-sm transition-colors hover:bg-bg-hover">Close</button>
      </div>
      <div className="mb-6 grid gap-4 sm:grid-cols-2">
        <label className="block text-sm"><span className="mb-2 block text-text-muted">Side</span><select value={side} onChange={(event) => setSide(event.target.value as OptionTimingSideKey)} className="block w-full rounded-[var(--radius)] border border-border bg-bg-input p-3">{OPTION_TIMING_SIDES.map((item) => <option key={item.key} value={item.key}>{item.short}</option>)}</select></label>
        <label className="block text-sm"><span className="mb-2 block text-text-muted">History range</span><select value={range} onChange={(event) => { setRange(event.target.value); setSelectedId(null); }} className="block w-full rounded-[var(--radius)] border border-border bg-bg-input p-3">{["30d", "90d"].map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
      </div>
      <section aria-label="Evolution" className="mb-6 rounded-[var(--radius)] border border-border p-4 sm:p-5">
        <h3 className="mb-3 font-medium">Evolution</h3>
        {history.isPending && <div role="status" aria-label="Loading Option Timing history" className="mb-4 h-48 animate-pulse rounded bg-bg-input p-3 text-text-muted">Loading history…</div>}
        {history.isError && <div role="alert" className="mb-4 text-sm text-accent-red">{history.error.message} <button type="button" disabled={history.isFetching} onClick={() => void history.refetch()} className="underline">Retry</button></div>}
        {!history.isPending && points.length > 0 && (
          <div className="mb-4 h-64 w-full min-w-0" aria-label="Option Timing evolution, 0 to 100, local date and time">
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ top: 10, right: 15, bottom: 15, left: 0 }}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
                <XAxis dataKey="timestamp" type="number" domain={["dataMin", "dataMax"]} tickFormatter={(value) => fmtTime(new Date(value).toISOString())} tick={{ fontSize: 10 }} />
                <YAxis dataKey="score" type="number" domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} tick={{ fontSize: 11 }} />
                <ReferenceArea y1={0} y2={25} fill="var(--accent-red)" fillOpacity={0.06} />
                <ReferenceArea y1={25} y2={50} fill="var(--accent-orange)" fillOpacity={0.06} />
                <ReferenceArea y1={50} y2={75} fill="#facc15" fillOpacity={0.06} />
                <Tooltip cursor={{ strokeDasharray: "3 3" }} content={({ payload }) => {
                  const point = payload?.[0]?.payload?.point as OptionTimingPoint | undefined;
                  return point ? <div className="rounded border border-border bg-bg-card p-2 text-xs">{fmtTime(point.as_of)}<br />{optionTimingScore(point, side) ?? "—"} / 100 · {point.model_version} · {point.history_kind}{point.provisional ? " · PROVISIONAL" : ""}{point.freshness !== "fresh" ? ` · ${point.freshness}` : ""}</div> : null;
                }} />
                {groups.map((group, index) => <Scatter key={group} name={group} data={chartData.filter((entry) => entry.score !== null && `${entry.point.model_version}:${entry.point.history_kind}` === group)} fill={["#60a5fa", "#a78bfa", "#fb923c", "#22d3ee"][index % 4]} shape={group.endsWith(":reconstructed") ? "diamond" : "circle"} onClick={selectChartPoint} />)}
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        )}
        <p className="mb-3 text-xs leading-relaxed text-text-muted">Exact source timestamps; points are not interpolated or joined across missing data or model versions. Circles: observed; diamonds: reconstructed. Select a point to inspect its saved explanation.</p>
        {history.data?.points.length === 0 && <p className="mb-3 text-sm text-text-muted">No historical points available. Showing the current persisted snapshot when available.</p>}
        {points.length > 0 && (
          <label className="block text-sm"><span className="mb-2 block text-text-muted">Historical snapshot (local date and time)</span>
            <select className="block w-full rounded-[var(--radius)] border border-border bg-bg-input p-3" value={selected?.snapshot_id ?? ""} onChange={(event) => setSelectedId(event.target.value)}>
              {points.map((point) => <option key={point.snapshot_id} value={point.snapshot_id}>{fmtTime(point.as_of)} · {optionTimingScore(point, side) ?? "Unavailable"} · {point.model_version} · {point.history_kind}{point.provisional ? " · PROVISIONAL" : ""}{point.freshness !== "fresh" ? ` · ${point.freshness}` : ""}</option>)}
            </select>
          </label>
        )}
      </section>
      <section aria-label="Selected score" aria-live="polite" className="mb-6 rounded-[var(--radius)] border border-border p-4 sm:p-5">
        <h3 className="mb-3 font-medium">Selected score</h3>
        {selected && <p className="mb-4 text-xs leading-relaxed text-text-muted">{selected.history_kind === "reconstructed" ? "Reconstructed historical snapshot (not an observed live score)." : "Observed snapshot."}</p>}
        <OptionTimingExplanation snapshot={selected ?? snapshot} side={side} />
      </section>
      <section aria-label="Methodology" className="rounded-[var(--radius)] border border-border bg-bg-input p-4 text-xs leading-relaxed text-text-muted sm:p-5">
        <h3 className="mb-3 font-medium text-text">Methodology</h3>
        <div className="space-y-3">
          <p className="whitespace-pre-wrap [overflow-wrap:anywhere]">{history.data?.methodology || "Methodology unavailable until history is loaded."}</p>
          {history.data && <p className="break-words">History model: {history.data.model_version} · Schema {history.data.schema_version}</p>}
          <p>Session in progress is marked PROVISIONAL. Source freshness and availability are independent of the score.</p>
        </div>
      </section>
    </AccessibleDialog>
  );
}
