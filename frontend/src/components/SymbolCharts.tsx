"use client";

import { useEffect, useRef, useState } from "react";
import type { Enrichment } from "@/types/symbol-detail";

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

/* ---------------- Radar chart (SVG) ---------------- */
interface QualityFactor {
  score?: number;
  max?: number;
  weight?: number;
}
function Radar({ qd }: { qd: Record<string, QualityFactor> }) {
  const labels: string[] = [];
  const values: number[] = [];
  const maxVals: number[] = [];
  for (const [k, v] of Object.entries(qd)) {
    if (v && typeof v === "object" && "score" in v) {
      labels.push(k.replace(/_/g, " "));
      values.push(v.score || 0);
      maxVals.push(v.max || v.weight || 25);
    }
  }
  const n = labels.length;
  if (n < 3) return null;

  const size = 320;
  const cx = size / 2;
  const cy = size / 2;
  const R = size / 2 - 54;
  const scaleMax = Math.max(100, ...maxVals, ...values);
  const angle = (i: number) => (Math.PI * 2 * i) / n - Math.PI / 2;
  const point = (i: number, val: number) => {
    const r = (Math.min(val, scaleMax) / scaleMax) * R;
    return [cx + r * Math.cos(angle(i)), cy + r * Math.sin(angle(i))];
  };
  const poly = (arr: number[]) => arr.map((v, i) => point(i, v).join(",")).join(" ");
  const rings = [0.25, 0.5, 0.75, 1];

  return (
    <div className="flex flex-col items-center">
      <h4 className="mb-2 self-start text-xs uppercase tracking-wide text-text-muted">Score Contribution</h4>
      <svg viewBox={`0 0 ${size} ${size}`} className="max-w-[320px]" role="img" aria-label="Quality score radar">
        {rings.map((rr, ri) => (
          <polygon
            key={ri}
            points={labels.map((_, i) => {
              const r = rr * R;
              return `${cx + r * Math.cos(angle(i))},${cy + r * Math.sin(angle(i))}`;
            }).join(" ")}
            fill="none"
            stroke="rgba(255,255,255,0.08)"
            strokeWidth={1}
          />
        ))}
        {labels.map((_, i) => {
          const [x, y] = point(i, scaleMax);
          return <line key={i} x1={cx} y1={cy} x2={x} y2={y} stroke="rgba(255,255,255,0.08)" strokeWidth={1} />;
        })}
        <polygon points={poly(maxVals)} fill="rgba(255,255,255,0.03)" stroke="rgba(255,255,255,0.18)" strokeWidth={1} strokeDasharray="4 4" />
        <polygon points={poly(values)} fill="rgba(99,102,241,0.25)" stroke="rgba(99,102,241,0.85)" strokeWidth={2} />
        {values.map((v, i) => {
          const [x, y] = point(i, v);
          return <circle key={i} cx={x} cy={y} r={3} fill="rgba(99,102,241,1)" />;
        })}
        {labels.map((lab, i) => {
          const [x, y] = point(i, scaleMax * 1.13);
          return (
            <text
              key={i}
              x={x}
              y={y}
              fill="#ccc"
              fontSize={10}
              textAnchor={Math.abs(x - cx) < 8 ? "middle" : x > cx ? "start" : "end"}
              dominantBaseline="middle"
            >
              {lab}
            </text>
          );
        })}
      </svg>
      <div className="mt-1 flex gap-4 text-xs text-text-muted">
        <span className="inline-flex items-center gap-1">
          <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: "rgba(99,102,241,0.85)" }} /> Score
        </span>
        <span className="inline-flex items-center gap-1">
          <span className="inline-block h-2.5 w-2.5 rounded-sm border border-dashed border-white/30" /> Max
        </span>
      </div>
    </div>
  );
}

/* ---------------- Timing history chart (SVG) ---------------- */
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

  const W = 640;
  const H = 200;
  const padL = 32;
  const padR = 12;
  const padT = 12;
  const padB = 24;
  const n = points.length;
  const x = (i: number) => padL + (i / (n - 1)) * (W - padL - padR);
  const y = (v: number) => padT + (1 - Math.max(0, Math.min(100, v)) / 100) * (H - padT - padB);
  const path = points
    .map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.tech_timing ?? 0).toFixed(1)}`)
    .join(" ");

  /* momentum bands as background rects per contiguous run */
  const bands: { x0: number; x1: number; color: string; m: string }[] = [];
  for (let i = 0; i < n; i++) {
    const m = points[i].momentum || "";
    const x0 = i === 0 ? padL : (x(i - 1) + x(i)) / 2;
    const x1 = i === n - 1 ? W - padR : (x(i) + x(i + 1)) / 2;
    bands.push({ x0, x1, color: momentumBand(m), m });
  }
  const usedMoms = Array.from(new Set(points.map((p) => p.momentum).filter(Boolean))) as string[];

  return (
    <div>
      <h4 className="mb-2 text-xs uppercase tracking-wide text-text-muted">Tech Timing — last {n} days</h4>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Tech timing history">
        {bands.map((b, i) => (
          <rect key={i} x={b.x0} y={padT} width={Math.max(0, b.x1 - b.x0)} height={H - padT - padB} fill={b.color} />
        ))}
        {[0, 25, 50, 75, 100].map((g) => (
          <g key={g}>
            <line x1={padL} y1={y(g)} x2={W - padR} y2={y(g)} stroke="rgba(148,163,184,0.12)" strokeWidth={1} />
            <text x={padL - 6} y={y(g)} fill="#9ca3af" fontSize={9} textAnchor="end" dominantBaseline="middle">{g}</text>
          </g>
        ))}
        <path d={path} fill="none" stroke="#e5e7eb" strokeWidth={2} />
        {points.map((p, i) => (
          <text
            key={i}
            x={x(i)}
            y={H - 8}
            fill="#9ca3af"
            fontSize={8}
            textAnchor="middle"
            style={{ display: n > 14 && i % Math.ceil(n / 12) !== 0 ? "none" : undefined }}
          >
            {p.date ? p.date.slice(5) : ""}
          </text>
        ))}
      </svg>
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

/* ---------------- Main collapsible section ---------------- */
export default function SymbolCharts({ symbol, enrichment }: { symbol: string; enrichment: Enrichment }) {
  const [open, setOpen] = useState(false);
  const enr = enrichment || {};
  const qd = (enr.quality_detail as Record<string, QualityFactor> | undefined) || undefined;
  const filterDetail = enr.filter_detail as { passes_all?: boolean; checks?: Record<string, FilterCheck> } | undefined;
  const metrics = enr.metrics as Record<string, unknown> | undefined;
  const technicals = enr.technicals as Record<string, unknown> | undefined;
  const hasData = enr.quality_score != null || (qd && Object.keys(qd).length > 0);

  return (
    <section className="rounded-[var(--radius)] border border-border bg-bg-card">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-4 py-3 text-left"
      >
        <span className="text-lg font-semibold">📊 Timing &amp; Score Detail</span>
        <span className="text-text-muted">{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div className="space-y-4 border-t border-border px-4 py-4">
          {!hasData ? (
            <div className="py-4 text-center text-sm text-text-muted">
              No enrichment data available yet. Run watchlist enrichment to populate.
            </div>
          ) : (
            <>
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
            </>
          )}
        </div>
      )}
    </section>
  );
}
