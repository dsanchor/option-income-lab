"use client";

import { useId, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CartesianGrid, ReferenceArea, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from "recharts";
import AccessibleDialog from "@/components/AccessibleDialog";
import { fmtTime } from "@/lib/options-row-format";
import { timeAgo } from "@/lib/format";
import { decodeSymbolParam } from "@/lib/symbolEncoding";
import { fetchOptionTimingHistory, OPTION_TIMING_SIDES, optionTimingPoints, optionTimingScore } from "@/lib/optionTiming";
import type { OptionTimingBand, OptionTimingPoint, OptionTimingSideKey, OptionTimingSnapshot } from "@/types/option-timing";

const BAND_CLASSES: Record<OptionTimingBand, string> = {
  red: "text-accent-red",
  orange: "text-accent-orange",
  yellow: "text-yellow-400",
  green: "text-accent-green",
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
  return (
    <span className="mt-3 block">
      <span className="relative block h-2 rounded bg-border"
        role={score === null ? undefined : "meter"}
        aria-label={`${label} timing`}
        aria-valuemin={score === null ? undefined : 0}
        aria-valuemax={score === null ? undefined : 100}
        aria-valuenow={score ?? undefined}
        aria-valuetext={score === null ? undefined : `${score} / 100${fresh ? "" : ` · ${snapshot?.freshness ?? "unknown"}`}`}
        style={score !== null && fresh ? { background: "linear-gradient(to right, var(--accent-red) 0% 25%, var(--accent-orange) 25% 50%, var(--color-yellow-400, #facc15) 50% 75%, var(--accent-green) 75% 100%)" } : undefined}>
        {score !== null && <span aria-hidden="true" className="absolute top-1/2 h-4 w-1 -translate-x-1/2 -translate-y-1/2 rounded bg-text ring-1 ring-bg-input" style={{ left: `${score}%` }} />}
      </span>
      <span className="mt-1 flex justify-between text-[10px] text-text-muted">
        <span>0</span><span>{label}: {score === null ? "Unavailable" : `${score} / 100`}</span><span>100</span>
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
    <div className="space-y-3 text-sm">
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
        <dl className="grid gap-1 text-xs text-text-muted">
          <div><dt className="inline">Source as of (local): </dt><dd className="inline">{snapshot.input_timestamp_available === false ? "Unavailable" : <><time dateTime={snapshot.as_of}>{fmtTime(snapshot.as_of)}</time> · {timeAgo(snapshot.as_of)}</>}</dd></div>
          <div><dt className="inline">Source session: </dt><dd className="inline">{snapshot.input_timestamp_available === false ? "Unavailable" : snapshot.source_session}</dd></div>
          <div><dt className="inline">Model: </dt><dd className="inline">{snapshot.model_version} · Schema {snapshot.schema_version}</dd></div>
          <div><dt className="inline">Snapshot: </dt><dd className="inline break-all">{snapshot.snapshot_id}</dd></div>
          <div><dt className="inline">Computed (local): </dt><dd className="inline">{fmtTime(snapshot.computed_at)} {snapshot.input_timestamp_available === false ? "(availability response, not an input observation)" : "(not source freshness)"}</dd></div>
        </dl>
      )}
      {!!value?.factors.length && (
        <div className="space-y-2">
          <h3 className="font-medium">Factors</h3>
          {value.factors.map((factor) => (
            <div key={factor.key} className="rounded border border-border p-3">
              <p className="font-medium">{factor.label}</p>
              <p className="text-xs text-text-muted">Score {factor.score} / 100 · Weight {factor.weight * 100}% · Contribution {factor.contribution}</p>
              <ul className="mt-1 list-inside list-disc text-xs">{factor.evidence.map((evidence, index) => <li key={index}>{evidence}</li>)}</ul>
            </div>
          ))}
        </div>
      )}
      {!!value?.adjustments.length && (
        <div className="space-y-2">
          <h3 className="font-medium">Guards and caps</h3>
          {value.adjustments.map((adjustment) => <p key={adjustment.key}>{adjustment.label}: {adjustment.reason} · {adjustment.cap === null ? "No numeric cap" : `Cap ${adjustment.cap}`}</p>)}
        </div>
      )}
    </div>
  );
}

export default function OptionTiming({ symbol, snapshot }: {
  symbol: string;
  snapshot?: OptionTimingSnapshot | null;
}) {
  const [side, setSide] = useState<OptionTimingSideKey | null>(null);
  return (
    <>
      <p className="mb-3 text-xs text-text-muted">Independent technical context (0–100), not contract profitability, probability, or a trade recommendation.</p>
      <div className="grid gap-3 md:grid-cols-2">
        {OPTION_TIMING_SIDES.map((item) => (
          <div key={item.key} className="rounded-[var(--radius)] border border-border bg-bg-input p-4 transition-colors hover:border-accent-blue">
            <button type="button" onClick={() => setSide(item.key)} className="block w-full rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue">
            <span className="mb-3 flex items-center justify-between gap-2"><span className="font-medium">{item.label}</span><span className="text-xs text-accent-blue">Info</span></span>
            <OptionTimingValue snapshot={snapshot} side={item.key} />
            {optionTimingScore(snapshot, item.key) !== null && snapshot?.[item.key].band && <span className="ml-2 text-xs text-text-muted">Band: {snapshot[item.key].band}</span>}
            <span className="mt-2 block text-xs text-text-muted">{snapshot?.[item.key].label || "No persisted score available"}</span>
            {optionTimingScore(snapshot, item.key) === null && <span className="mt-1 block text-xs text-text-muted">{snapshot?.[item.key].reason_codes.join(", ").replace(/_/g, " ") || "Unavailable: no persisted score available"}</span>}
            {snapshot && <span className="mt-2 block text-xs text-text-muted">Source (local): {snapshot.input_timestamp_available === false ? "Unavailable" : fmtTime(snapshot.as_of)}</span>}
            </button>
            <OptionTimingGauge snapshot={snapshot} side={item.key} />
          </div>
        ))}
      </div>
      <p className="mt-3 text-xs text-text-muted">Bands: red 0–&lt;25 · orange 25–&lt;50 · yellow 50–&lt;75 · green 75–100.</p>
      {side && <OptionTimingModal symbol={symbol} snapshot={snapshot} initialSide={side} onClose={() => setSide(null)} />}
    </>
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
      <div className="mb-4 flex items-center justify-between gap-3">
        <h2 id={titleId} className="text-lg font-semibold">{symbol} · Option Timing</h2>
        <button type="button" autoFocus onClick={onClose} aria-label="Close Option Timing" className="rounded border border-border px-3 py-1">Close</button>
      </div>
      <p className="mb-4 text-xs text-text-muted">Technical context only. Scores are persisted by the backend, not profitability or probability estimates. Session in progress is marked PROVISIONAL.</p>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <label className="text-sm">Side <select value={side} onChange={(event) => setSide(event.target.value as OptionTimingSideKey)} className="rounded border border-border bg-bg-input p-1">{OPTION_TIMING_SIDES.map((item) => <option key={item.key} value={item.key}>{item.short}</option>)}</select></label>
        <label className="text-sm">History range <select value={range} onChange={(event) => { setRange(event.target.value); setSelectedId(null); }} className="rounded border border-border bg-bg-input p-1">{["30d", "90d"].map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
      </div>
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
      <p className="mb-3 text-xs text-text-muted">Exact source timestamps; points are not interpolated or joined across missing data or model versions. Circles: observed; diamonds: reconstructed. Select a point to inspect its saved explanation.</p>
      {history.data?.points.length === 0 && <p className="mb-3 text-sm text-text-muted">No historical points available. Showing the current persisted snapshot when available.</p>}
      {points.length > 0 && (
        <label className="mb-4 block text-sm">Historical snapshot (local date and time)
          <select className="mt-1 block w-full rounded border border-border bg-bg-input p-2" value={selected?.snapshot_id ?? ""} onChange={(event) => setSelectedId(event.target.value)}>
            {points.map((point) => <option key={point.snapshot_id} value={point.snapshot_id}>{fmtTime(point.as_of)} · {optionTimingScore(point, side) ?? "Unavailable"} · {point.model_version} · {point.history_kind}{point.provisional ? " · PROVISIONAL" : ""}{point.freshness !== "fresh" ? ` · ${point.freshness}` : ""}</option>)}
          </select>
        </label>
      )}
      <div aria-live="polite">
        {selected && <p className="mb-2 text-xs text-text-muted">{selected.history_kind === "reconstructed" ? "Reconstructed historical snapshot (not an observed live score)." : "Observed snapshot."}</p>}
        <OptionTimingExplanation snapshot={selected ?? snapshot} side={side} />
      </div>
      <div className="mt-4 border-t border-border pt-3 text-xs text-text-muted">
        <h3 className="font-medium text-text">Methodology</h3>
        <p className="whitespace-pre-wrap">{history.data?.methodology || "Methodology unavailable until history is loaded."}</p>
        {history.data && <p>History model: {history.data.model_version} · Schema {history.data.schema_version}</p>}
        <p className="mt-1">Bands: red 0–&lt;25 · orange 25–&lt;50 · yellow 50–&lt;75 · green 75–100. Source freshness and availability are independent of the score.</p>
      </div>
    </AccessibleDialog>
  );
}
