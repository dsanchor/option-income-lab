"use client";

import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { HORIZONS } from "@/types/forecasts";
import type { ForecastRow, Horizon, HitRateEntry } from "@/types/forecasts";

const CENTER_COLOR = "#5b61ff";
const BAND1_COLOR = "#5b61ff";
const BAND2_COLOR = "#5b61ff";
const HIT1_COLOR = "#00c493";
const HIT2_COLOR = "#5b61ff";
const DIR_COLOR = "#b981ff";

const money = (v: number | null | undefined) =>
  typeof v === "number" && isFinite(v)
    ? new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(v)
    : "—";

const pct = (v: number | null | undefined) =>
  typeof v === "number" && isFinite(v) ? `${v.toFixed(1)}%` : "—";

/** Pick the most recent forecast row (max created_date). */
export function latestRow(rows: ForecastRow[]): ForecastRow | null {
  if (!rows?.length) return null;
  return rows.reduce((best, r) =>
    (r.created_date ?? "") > (best.created_date ?? "") ? r : best,
  rows[0]);
}

type FanPoint = {
  label: string;
  center: number | null;
  band1: [number, number] | null;
  band2: [number, number] | null;
};

function ProjectionFanChart({ row }: { row: ForecastRow | null }) {
  const anchor = row?.price_at_creation ?? null;
  if (!row || anchor == null) {
    return <p className="text-sm text-text-muted">No projection data for this range.</p>;
  }

  const data: FanPoint[] = [
    { label: "now", center: anchor, band1: [anchor, anchor], band2: [anchor, anchor] },
  ];
  for (const h of HORIZONS) {
    const hz = row.horizons?.[h];
    if (!hz || hz.center == null) continue;
    const band1: [number, number] | null =
      hz.low1 != null && hz.high1 != null ? [hz.low1, hz.high1] : null;
    const band2: [number, number] | null =
      hz.low2 != null && hz.high2 != null ? [hz.low2, hz.high2] : null;
    data.push({ label: h.toUpperCase(), center: hz.center, band1, band2 });
  }

  if (data.length < 2) {
    return <p className="text-sm text-text-muted">No projection data for this range.</p>;
  }

  return (
    <div style={{ height: 280 }}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
          <CartesianGrid stroke="rgba(148,163,184,0.08)" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fill: "#8d969e", fontSize: 10 }}
            tickLine={false}
            axisLine={{ stroke: "rgba(148,163,184,0.15)" }}
          />
          <YAxis
            tick={{ fill: "#8d969e", fontSize: 10 }}
            tickLine={false}
            axisLine={{ stroke: "rgba(148,163,184,0.15)" }}
            tickFormatter={(v) => money(Number(v))}
            width={62}
            domain={["auto", "auto"]}
          />
          <ReferenceLine y={anchor} stroke="rgba(148,163,184,0.35)" strokeDasharray="4 4" />
          <Tooltip
            cursor={{ stroke: "rgba(148,163,184,0.25)" }}
            content={({ active, payload, label }) => {
              if (!active || !payload?.length) return null;
              const p = payload[0].payload as FanPoint;
              return (
                <div className="rounded-[10px] border border-border bg-bg-card px-3 py-2 text-xs shadow-lg">
                  <div className="mb-1 font-medium text-text">{label}</div>
                  <div className="flex items-center gap-2">
                    <span className="text-text-muted">Center</span>
                    <span className="ml-auto font-mono text-text">{money(p.center)}</span>
                  </div>
                  {p.band1 && (
                    <div className="flex items-center gap-2">
                      <span className="text-text-muted">±1σ</span>
                      <span className="ml-auto font-mono text-text-muted">
                        {money(p.band1[0])} – {money(p.band1[1])}
                      </span>
                    </div>
                  )}
                  {p.band2 && (
                    <div className="flex items-center gap-2">
                      <span className="text-text-muted">±2σ</span>
                      <span className="ml-auto font-mono text-text-muted">
                        {money(p.band2[0])} – {money(p.band2[1])}
                      </span>
                    </div>
                  )}
                </div>
              );
            }}
          />
          <Area
            dataKey="band2"
            name="±2σ"
            stroke="none"
            fill={BAND2_COLOR}
            fillOpacity={0.08}
            isAnimationActive={false}
            connectNulls
          />
          <Area
            dataKey="band1"
            name="±1σ"
            stroke="none"
            fill={BAND1_COLOR}
            fillOpacity={0.18}
            isAnimationActive={false}
            connectNulls
          />
          <Line
            dataKey="center"
            name="Center"
            stroke={CENTER_COLOR}
            strokeWidth={2}
            dot={{ r: 3, fill: CENTER_COLOR, stroke: "none" }}
            isAnimationActive={false}
            connectNulls
          />
          <Legend wrapperStyle={{ fontSize: 11, color: "#8d969e" }} iconType="circle" iconSize={8} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

type HitPoint = { label: string; hit1: number | null; hit2: number | null; dir: number | null };

function HitRateChart({ hitRate }: { hitRate: Partial<Record<Horizon, HitRateEntry>> }) {
  const data: HitPoint[] = HORIZONS.map((h) => {
    const hr = hitRate?.[h];
    return {
      label: h.toUpperCase(),
      hit1: hr?.hit_pct_1sigma ?? null,
      hit2: hr?.hit_pct_2sigma ?? null,
      dir: hr?.direction_pct ?? null,
    };
  });

  const hasData = data.some((d) => d.hit1 != null || d.hit2 != null || d.dir != null);
  if (!hasData) {
    return <p className="text-sm text-text-muted">No resolved forecasts to calibrate yet.</p>;
  }

  return (
    <div style={{ height: 280 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 4 }} barGap={2}>
          <CartesianGrid stroke="rgba(148,163,184,0.08)" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fill: "#8d969e", fontSize: 10 }}
            tickLine={false}
            axisLine={{ stroke: "rgba(148,163,184,0.15)" }}
          />
          <YAxis
            tick={{ fill: "#8d969e", fontSize: 10 }}
            tickLine={false}
            axisLine={{ stroke: "rgba(148,163,184,0.15)" }}
            tickFormatter={(v) => `${v}%`}
            domain={[0, 100]}
            width={44}
          />
          <ReferenceLine y={68} stroke="rgba(0,196,147,0.35)" strokeDasharray="4 4" />
          <ReferenceLine y={95} stroke="rgba(91,97,255,0.35)" strokeDasharray="4 4" />
          <Tooltip
            cursor={{ fill: "rgba(148,163,184,0.08)" }}
            content={({ active, payload, label }) => {
              if (!active || !payload?.length) return null;
              return (
                <div className="rounded-[10px] border border-border bg-bg-card px-3 py-2 text-xs shadow-lg">
                  <div className="mb-1 font-medium text-text">{label}</div>
                  {payload.map((p, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <span className="inline-block h-2 w-2 rounded-sm" style={{ background: p.color }} />
                      <span className="text-text-muted">{p.name}</span>
                      <span className="ml-auto font-mono text-text">{pct(Number(p.value))}</span>
                    </div>
                  ))}
                </div>
              );
            }}
          />
          <Legend wrapperStyle={{ fontSize: 11, color: "#8d969e" }} iconType="circle" iconSize={8} />
          <Bar dataKey="hit1" name="Hit ±1σ" fill={HIT1_COLOR} radius={[3, 3, 0, 0]} maxBarSize={26} isAnimationActive={false} />
          <Bar dataKey="hit2" name="Hit ±2σ" fill={HIT2_COLOR} radius={[3, 3, 0, 0]} maxBarSize={26} isAnimationActive={false} />
          <Bar dataKey="dir" name="Direction" fill={DIR_COLOR} radius={[3, 3, 0, 0]} maxBarSize={26} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function ForecastCharts({
  rows,
  hitRate,
}: {
  rows: ForecastRow[];
  hitRate: Partial<Record<Horizon, HitRateEntry>>;
}) {
  const row = latestRow(rows);
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="rounded-[var(--radius)] border border-border bg-bg-card p-4">
        <div className="mb-1 flex items-baseline justify-between gap-2">
          <h3 className="text-sm font-semibold">Projection fan (latest)</h3>
          {row?.created_date && (
            <span className="font-mono text-xs text-text-muted">{row.created_date}</span>
          )}
        </div>
        <p className="mb-2 text-xs text-text-muted">
          Center price with ±1σ / ±2σ bands across horizons. Dashed line = anchor.
        </p>
        <ProjectionFanChart row={row} />
      </div>
      <div className="rounded-[var(--radius)] border border-border bg-bg-card p-4">
        <div className="mb-1 flex items-baseline justify-between gap-2">
          <h3 className="text-sm font-semibold">Calibration hit-rate</h3>
          <span className="font-mono text-xs text-text-muted">target 68% / 95%</span>
        </div>
        <p className="mb-2 text-xs text-text-muted">
          Share of resolved forecasts inside each band, plus direction accuracy.
        </p>
        <HitRateChart hitRate={hitRate} />
      </div>
    </div>
  );
}
