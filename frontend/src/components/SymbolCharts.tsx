"use client";

import { useEffect, useRef, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  RadarChart,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  Radar as RechartsRadar,
} from "recharts";
import type { Enrichment } from "@/types/symbol-detail";

const Recharts = { Radar: RechartsRadar };

/* ---- momentum band colors (mirror legacy MOMENTUM_BAND) ---- */
const MOMENTUM_BAND: Record<string, string> = {
  bullish: "rgba(34,197,94,0.16)",
  "bullish (overextended)": "rgba(234,179,8,0.17)",
  weakening: "rgba(249,115,22,0.16)",
  neutral: "rgba(148,163,184,0.13)",
  bearish: "rgba(239,68,68,0.16)",
  "bearish (oversold)": "rgba(59,130,246,0.17)",
};
function momentumBand(m: string | undefined): string {
  return MOMENTUM_BAND[(m || "").toLowerCase()] || "rgba(148,163,184,0.07)";
}

/* ---------------- formatting helpers ---------------- */
function fmtVal(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "number") {
    if (!isFinite(v)) return "—";
    return Number.isInteger(v) ? String(v) : v.toLocaleString(undefined, { maximumFractionDigits: 4 });
  }
  if (typeof v === "boolean") return v ? "Yes" : "No";
  return String(v);
}

function fmtField(k: string, v: unknown): string {
  if (typeof v === "number") {
    if (k === "payout_ratio") return (v * 100).toFixed(1) + "%";
    if (k === "dividend_yield" || k === "dividend_cagr_5y" || k === "roe") return (v * 100).toFixed(2) + "%";
    if (k === "debt_to_equity") return v.toFixed(2);
    if (k === "market_cap") {
      if (v >= 1e9) return "$" + (v / 1e9).toFixed(2) + "B";
      if (v >= 1e6) return "$" + (v / 1e6).toFixed(2) + "M";
    }
    if (k === "current_price") return "$" + v.toFixed(2);
  }
  return fmtVal(v);
}

/* ---------------- Info section (Overview / Fundamentals / Technical) ---------------- */
function Section({ title, obj }: { title: string; obj: Record<string, unknown> }) {
  const fields: [string, unknown][] = [];
  const nested: [string, Record<string, unknown>][] = [];
  for (const [k, v] of Object.entries(obj)) {
    if (v && typeof v === "object" && !Array.isArray(v)) nested.push([k, v as Record<string, unknown>]);
    else fields.push([k, v]);
  }
  return (
    <div className="mb-4">
      <h4 className="mb-2 text-xs uppercase tracking-wide text-text-muted">{title}</h4>
      {fields.length > 0 && (
        <div className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-[var(--radius)] bg-bg-input px-4 py-3 sm:grid-cols-3">
          {fields.map(([k, v]) => (
            <div key={k}>
              <div className="text-[0.7rem] uppercase tracking-wide text-text-muted">{k.replace(/_/g, " ")}</div>
              <div className="font-mono text-sm">{fmtField(k, v)}</div>
            </div>
          ))}
        </div>
      )}
      {nested.map(([k, v]) => (
        <Section key={k} title={`${title} › ${k.replace(/_/g, " ")}`} obj={v} />
      ))}
    </div>
  );
}

/* ---------------- Filter Status ---------------- */
interface FilterCheck {
  passes?: boolean;
  label?: string;
  actual?: number | string;
  threshold?: number | string;
  op?: string;
}
function FilterStatus({ detail }: { detail: { passes_all?: boolean; checks?: Record<string, FilterCheck> } }) {
  const checks = detail.checks || {};
  const keys = Object.keys(checks);
  if (keys.length === 0) return null;
  const fmtCheck = (fk: string, val: number | string | undefined): string => {
    if (val == null) return "—";
    if (typeof val === "number") {
      if (fk === "min_yield" || fk === "max_payout" || fk === "min_growth") return (val * 100).toFixed(2) + "%";
      if (fk === "min_market_cap") return "$" + (val / 1e9).toFixed(1) + "B";
      if (fk === "min_years") return Math.round(val) + " yrs";
      return val.toLocaleString(undefined, { maximumFractionDigits: 4 });
    }
    return String(val);
  };
  return (
    <div className="mb-4">
      <h4 className="mb-2 text-xs uppercase tracking-wide text-text-muted">
        {detail.passes_all ? "✅" : "⚠️"} Filter Status
      </h4>
      <div className="rounded-[var(--radius)] bg-bg-input px-4 py-2">
        {keys.map((fk) => {
          const fc = checks[fk];
          return (
            <div
              key={fk}
              className="flex items-center justify-between border-b border-border py-1.5 last:border-0"
              style={{ color: fc.passes ? "var(--text)" : "var(--accent-red, #ef4444)" }}
            >
              <span className="text-sm">{fc.passes ? "✅" : "❌"} {fc.label || fk}</span>
              <span className="font-mono text-xs">
                {fmtCheck(fk, fc.actual)}{" "}
                <span className="text-text-muted">{fc.op} {fmtCheck(fk, fc.threshold)}</span>
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ---------------- Radar chart (Recharts) ---------------- */
interface QualityFactor {
  score?: number;
  max?: number;
  weight?: number;
}
function Radar({ qd }: { qd: Record<string, QualityFactor> }) {
  const data: { label: string; score: number; max: number }[] = [];
  for (const [k, v] of Object.entries(qd)) {
    if (v && typeof v === "object" && "score" in v) {
      data.push({ label: k.replace(/_/g, " "), score: v.score || 0, max: v.max || v.weight || 25 });
    }
  }
  if (data.length < 3) return null;
  const scaleMax = Math.max(100, ...data.map((d) => Math.max(d.max, d.score)));

  return (
    <div className="flex flex-col items-center">
      <h4 className="mb-2 self-start text-xs uppercase tracking-wide text-text-muted">Score Contribution</h4>
      <div style={{ width: "100%", maxWidth: 360, height: 300 }}>
        <ResponsiveContainer width="100%" height="100%">
          <RadarChart data={data} outerRadius="72%">
            <PolarGrid stroke="rgba(255,255,255,0.10)" />
            <PolarAngleAxis dataKey="label" tick={{ fill: "#ccc", fontSize: 10 }} />
            <PolarRadiusAxis domain={[0, scaleMax]} tick={{ fill: "#777", fontSize: 9 }} axisLine={false} />
            <Recharts.Radar name="Max" dataKey="max" stroke="rgba(255,255,255,0.25)" strokeDasharray="4 4" fill="rgba(255,255,255,0.04)" fillOpacity={1} isAnimationActive={false} />
            <Recharts.Radar name="Score" dataKey="score" stroke="rgba(99,102,241,0.9)" fill="rgba(99,102,241,0.28)" fillOpacity={1} isAnimationActive={false} />
            <Tooltip
              contentStyle={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 10, fontSize: 12 }}
              labelStyle={{ color: "#8d969e" }}
            />
            <Legend wrapperStyle={{ fontSize: 11, color: "#ccc" }} />
          </RadarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/* ---------------- Timing history chart (Recharts) ---------------- */
interface TimingPoint {
  date?: string;
  tech_timing?: number;
  momentum?: string;
}
function TimingHistory({ symbol }: { symbol: string }) {
  const [points, setPoints] = useState<TimingPoint[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loaded = useRef(false);

  useEffect(() => {
    if (loaded.current) return;
    loaded.current = true;
    fetch(`/api/symbols/${encodeURIComponent(symbol)}/enrichment-history`)
      .then((r) => r.json())
      .then((data) => setPoints((data && data.points) || []))
      .catch(() => setError("Could not load timing history"));
  }, [symbol]);

  if (error) return null;
  if (points == null) return <div className="py-4 text-sm text-text-muted">Loading timing history…</div>;
  if (points.length < 2) return null;

  const n = points.length;
  const data = points.map((p) => ({
    label: p.date ? p.date.slice(5) : "",
    tech_timing: p.tech_timing ?? null,
    momentum: p.momentum ?? "",
  }));

  /* contiguous momentum runs → shaded reference areas */
  const runs: { x1: string; x2: string; color: string }[] = [];
  let start = 0;
  for (let i = 1; i <= n; i++) {
    if (i === n || data[i].momentum !== data[start].momentum) {
      const m = data[start].momentum;
      if (m) runs.push({ x1: data[start].label, x2: data[i - 1].label, color: momentumBand(m) });
      start = i;
    }
  }
  const usedMoms = Array.from(new Set(points.map((p) => p.momentum).filter(Boolean))) as string[];

  return (
    <div>
      <h4 className="mb-2 text-xs uppercase tracking-wide text-text-muted">Tech Timing — last {n} days</h4>
      <div style={{ width: "100%", height: 220 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
            {runs.map((r, i) => (
              <ReferenceArea key={i} x1={r.x1} x2={r.x2} fill={r.color} fillOpacity={1} ifOverflow="extendDomain" />
            ))}
            <CartesianGrid stroke="rgba(148,163,184,0.10)" vertical={false} />
            <XAxis dataKey="label" tick={{ fill: "#8d969e", fontSize: 10 }} tickLine={false} axisLine={{ stroke: "rgba(148,163,184,0.15)" }} minTickGap={24} />
            <YAxis domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} tick={{ fill: "#8d969e", fontSize: 10 }} tickLine={false} axisLine={false} width={28} />
            <Tooltip
              contentStyle={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 10, fontSize: 12 }}
              labelStyle={{ color: "#8d969e" }}
              formatter={(v, _n, item) => [v as number | string, (item as { payload?: { momentum?: string } })?.payload?.momentum ? `Timing · ${(item as { payload?: { momentum?: string } }).payload!.momentum}` : "Tech Timing"]}
            />
            <Line type="monotone" dataKey="tech_timing" stroke="#e5e7eb" strokeWidth={2} dot={n <= 40 ? { r: 2 } : false} activeDot={{ r: 4 }} connectNulls isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      {usedMoms.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-3 text-xs text-text-muted">
          {usedMoms.map((m) => (
            <span key={m} className="inline-flex items-center gap-1">
              <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: momentumBand(m).replace(/0\.\d+\)/, "0.9)") }} />
              {m}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/* ---------------- Timing & Score content (used inside the summary modal) ---------------- */
export function TimingScoreContent({ symbol, enrichment }: { symbol: string; enrichment: Enrichment }) {
  const enr = enrichment || {};
  const qd = (enr.quality_detail as Record<string, QualityFactor> | undefined) || undefined;
  const filterDetail = enr.filter_detail as { passes_all?: boolean; checks?: Record<string, FilterCheck> } | undefined;
  const metrics = enr.metrics as Record<string, unknown> | undefined;
  const technicals = enr.technicals as Record<string, unknown> | undefined;
  const hasData = enr.quality_score != null || (qd && Object.keys(qd).length > 0);

  if (!hasData) {
    return (
      <div className="py-6 text-center text-sm text-text-muted">
        No enrichment data available yet. Run watchlist enrichment to populate.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-6 lg:grid-cols-2">
        <div>
          <Section
            title="Overview"
            obj={{
              quality_score: enr.quality_score,
              category: enr.category,
              entry_tag: enr.entry_tag,
            }}
          />
          {filterDetail && <FilterStatus detail={filterDetail} />}
        </div>
        {qd && <Radar qd={qd} />}
      </div>

      <TimingHistory symbol={symbol} />

      {metrics && Object.keys(metrics).length > 0 && <Section title="Fundamentals" obj={metrics} />}
      {technicals && Object.keys(technicals).length > 0 && <Section title="Technical Timing" obj={technicals} />}
    </div>
  );
}
