"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { renderMarkdown } from "@/lib/markdown";
import { ROW_TINT_BG } from "@/lib/badges";
import type { Position } from "@/types/symbol-detail";

// ── Snapshot chart ────────────────────────────────────────────────────────
type Snapshot = Record<string, number | string | null | undefined>;

const SERIES: { key: string; field: string; label: string; color: string }[] = [
  { key: "gap", field: "gap_pct", label: "Gap %", color: "#58d68d" },
  { key: "rsi", field: "rsi_14", label: "RSI", color: "#5dade2" },
  { key: "macd", field: "macd_level", label: "MACD", color: "#af7ac5" },
  { key: "adx", field: "adx", label: "ADX", color: "#f5b041" },
  { key: "dps", field: "dps_score", label: "DPS", color: "#ec7063" },
  { key: "pnl", field: "pnl_pct", label: "P&L %", color: "#00bcd4" },
];

function toDate(ts: unknown): Date | null {
  if (typeof ts !== "string" || !ts) return null;
  const norm = ts.indexOf("T") === -1 && /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(ts)
    ? ts.replace(" ", "T") + "Z"
    : ts;
  const d = new Date(norm);
  return isFinite(d.getTime()) ? d : null;
}

function isWeekend(d: Date): boolean {
  const day = d.getUTCDay();
  return day === 0 || day === 6;
}

function numOrNull(v: unknown): number | null {
  const n = typeof v === "number" ? v : typeof v === "string" ? parseFloat(v) : NaN;
  return isFinite(n) ? n : null;
}

function SnapshotChart({ snapshots }: { snapshots: Snapshot[] }) {
  const [hideWeekends, setHideWeekends] = useState(true);
  const [visible, setVisible] = useState<Record<string, boolean>>(
    Object.fromEntries(SERIES.map((s) => [s.key, true])),
  );

  const rows = snapshots
    .map((s) => ({ s, d: toDate(s.timestamp) }))
    .filter((r) => r.d !== null && (!hideWeekends || !isWeekend(r.d!)));

  if (rows.length === 0) {
    return <p className="text-sm text-text-muted">No monitoring data yet.</p>;
  }

  const fmtLabel = (d: Date) =>
    d.toLocaleDateString(undefined, { month: "short", day: "numeric" });

  // Per-series min/max for 0..1 normalization (mixed scales → compare shape).
  const bounds: Record<string, { min: number; max: number }> = {};
  for (const s of SERIES) {
    const present = rows
      .map((r) => numOrNull(r.s[s.field]))
      .filter((v): v is number => v !== null);
    bounds[s.field] = present.length
      ? { min: Math.min(...present), max: Math.max(...present) }
      : { min: 0, max: 1 };
  }

  const data = rows.map((r) => {
    const row: Record<string, number | string | null> = { label: fmtLabel(r.d!) };
    for (const s of SERIES) {
      const v = numOrNull(r.s[s.field]);
      row[`${s.key}_raw`] = v;
      if (v === null) row[s.key] = null;
      else {
        const { min, max } = bounds[s.field];
        const span = max - min || 1;
        row[s.key] = (v - min) / span;
      }
    }
    return row;
  });

  const activeSeries = SERIES.filter((s) => visible[s.key]);

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex cursor-pointer items-center gap-1.5 text-xs text-text-muted">
          <input type="checkbox" checked={hideWeekends} onChange={(e) => setHideWeekends(e.target.checked)} />
          Hide weekends
        </label>
        <div className="flex flex-wrap gap-2.5">
          {SERIES.map((s) => (
            <label key={s.key} className="flex cursor-pointer items-center gap-1 text-xs" style={{ color: s.color }}>
              <input
                type="checkbox"
                checked={visible[s.key]}
                onChange={() => setVisible((v) => ({ ...v, [s.key]: !v[s.key] }))}
                style={{ accentColor: s.color }}
              />
              {s.label}
            </label>
          ))}
        </div>
      </div>
      <div className="rounded-[var(--radius)] border border-border/70 bg-bg-card p-3" style={{ height: 220 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 6, right: 10, bottom: 2, left: 2 }}>
            <CartesianGrid stroke="rgba(148,163,184,0.08)" strokeDasharray="3 3" />
            <XAxis
              dataKey="label"
              tick={{ fill: "#8d969e", fontSize: 10 }}
              tickLine={false}
              axisLine={{ stroke: "rgba(148,163,184,0.15)" }}
              minTickGap={24}
            />
            <YAxis
              domain={[0, 1]}
              ticks={[0, 0.25, 0.5, 0.75, 1]}
              tickFormatter={(v) => `${Math.round(Number(v) * 100)}%`}
              tick={{ fill: "#8d969e", fontSize: 10 }}
              tickLine={false}
              axisLine={{ stroke: "rgba(148,163,184,0.15)" }}
              width={38}
            />
            <Tooltip
              cursor={{ stroke: "rgba(148,163,184,0.35)", strokeWidth: 1 }}
              contentStyle={{
                background: "var(--bg-card)",
                border: "1px solid var(--border)",
                borderRadius: 10,
                fontSize: 12,
              }}
              labelStyle={{ color: "#8d969e" }}
              formatter={(_v, _n, item) => {
                const it = item as { dataKey?: unknown; payload?: Record<string, unknown> };
                const key = String(it?.dataKey ?? "").replace(/_norm$/, "");
                const meta = SERIES.find((s) => s.key === key);
                const raw = it?.payload?.[`${key}_raw`];
                return [raw != null ? Number(raw).toFixed(2) : "—", meta?.label ?? key];
              }}
            />
            {activeSeries.map((s) => (
              <Line
                key={s.key}
                type="linear"
                dataKey={s.key}
                name={s.label}
                stroke={s.color}
                strokeWidth={2}
                dot={{ r: 1.5, fill: s.color, strokeWidth: 0 }}
                activeDot={{ r: 3.5 }}
                connectNulls
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="flex justify-between text-xs text-text-muted">
        <span>{fmtLabel(rows[0].d!)}</span>
        <span>{rows.length} snapshots</span>
        <span>{fmtLabel(rows[rows.length - 1].d!)}</span>
      </div>
      <div className="flex flex-wrap gap-3 text-xs">
        {activeSeries.map((s) => {
          const last = [...rows].reverse().find((r) => numOrNull(r.s[s.field]) !== null);
          const v = last ? numOrNull(last.s[s.field]) : null;
          return (
            <span key={s.key} style={{ color: s.color }}>
              {s.label}: {v !== null ? v.toFixed(2) : "—"}
            </span>
          );
        })}
      </div>
    </div>
  );
}

// ── DPS analysis ──────────────────────────────────────────────────────────
// `status`/`risk_zone` may now legitimately be "NO_DATA"/"UNKNOWN" (the
// STATUS_COLORS/RISK_COLORS maps below already fall back to gray for any
// key they don't recognize, so those two need no code change). `inputs`'
// Greek/IV entries (delta/gamma/theta/iv) can now genuinely be `null` when
// the option chain has no usable quote for this contract this cycle — the
// backend never fabricates a 0/intrinsic-only substitute for them.
interface DpsResult {
  error?: string;
  status?: string;
  score?: number;
  risk_zone?: string;
  summary?: string;
  key_drivers?: string[];
  next_focus?: string;
  inputs?: Record<string, number | string | null>;
  score_breakdown?: { factor: string; points: number; reason: string }[];
  trend_analysis?: Record<string, unknown>;
  data_quality?: {
    missing_fields?: string[];
    confidence?: "full" | "partial" | "insufficient";
    quote_asof?: string | null;
    stale?: boolean | null;
  };
}

function fmtNullable(v: number | string | null | undefined): string {
  return v === null || v === undefined ? "N/A" : String(v);
}

const STATUS_COLORS: Record<string, string> = { HOLD: "#3fb950", WATCH: "#f0883e", ROLL: "#bc8cff" };
const RISK_COLORS: Record<string, string> = { SAFE: "#3fb950", MONITOR: "#f0883e", ATM_CRITICAL: "#f85149" };
const DATA_QUALITY_LABEL: Record<string, string> = { partial: "◐ Partial data", insufficient: "⚠ Insufficient data" };

function DpsAnalysis({ symbol, positionId }: { symbol: string; positionId: string }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<DpsResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(
        `/api/symbols/${encodeURIComponent(symbol)}/positions/${encodeURIComponent(positionId)}/dps-analysis`,
        { method: "POST", headers: { Accept: "application/json" } },
      );
      const data = (await res.json().catch(() => ({}))) as DpsResult;
      if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
      setResult(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  }

  const statusColor = result?.status ? STATUS_COLORS[result.status] ?? "#8d969e" : "#8d969e";
  const riskColor = result?.risk_zone ? RISK_COLORS[result.risk_zone] ?? "#8d969e" : "#8d969e";
  const inp = result?.inputs;

  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={run}
        disabled={busy}
        className="rounded-[var(--radius-pill)] bg-accent-blue px-3 py-1.5 text-xs text-white disabled:opacity-50"
      >
        {busy ? "⏳ Analyzing…" : "📊 DPS Analysis"}
      </button>
      {error && <div className="text-xs text-accent-red">⚠️ {error}</div>}
      {result && !error && (
        <div className="space-y-2 rounded-[var(--radius)] border border-border bg-bg-input p-3 text-sm">
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-xl font-bold" style={{ color: statusColor }}>{result.status}</span>
            <span className="font-semibold">Score: {result.score}/100</span>
            <span className="rounded border px-1.5 py-0.5 text-xs" style={{ color: riskColor, borderColor: `${riskColor}66`, background: `${riskColor}22` }}>
              {result.risk_zone}
            </span>
            {result.data_quality?.confidence && result.data_quality.confidence !== "full" && (
              <span
                className="rounded border px-1.5 py-0.5 text-xs text-text-muted"
                style={{ borderColor: "#8d969e66", background: "#8d969e22" }}
                title={
                  result.data_quality.missing_fields?.length
                    ? `Missing: ${result.data_quality.missing_fields.join(", ")}`
                    : undefined
                }
              >
                {DATA_QUALITY_LABEL[result.data_quality.confidence] ?? result.data_quality.confidence}
                {result.data_quality.missing_fields?.length
                  ? ` (${result.data_quality.missing_fields.join(", ")})`
                  : ""}
              </span>
            )}
          </div>
          {result.summary && <p className="text-xs italic text-text-muted">{result.summary}</p>}
          {result.key_drivers && result.key_drivers.length > 0 && (
            <div className="text-xs">
              <strong>Key Drivers:</strong>
              <ul className="ml-4 list-disc">
                {result.key_drivers.map((d, i) => <li key={i} className="text-text-muted">{d}</li>)}
              </ul>
            </div>
          )}
          {result.next_focus && <p className="text-xs text-text-muted">🎯 <strong>Next focus:</strong> {result.next_focus}</p>}
          {inp && (
            <details className="text-xs">
              <summary className="cursor-pointer text-text-muted">Input parameters</summary>
              <div className="mt-1 grid grid-cols-2 gap-1 text-text-muted sm:grid-cols-3">
                <span>Δ {fmtNullable(inp.delta)}</span>
                <span>Γ {fmtNullable(inp.gamma)}</span>
                <span>Θ {fmtNullable(inp.theta)}</span>
                <span>IV {typeof inp.iv === "number" ? `${(inp.iv * 100).toFixed(1)}%` : "N/A"}</span>
                <span>DTE {inp.dte}</span>
                <span>RSI {inp.rsi} ({inp.rsi_trend})</span>
                <span>MACD {inp.macd} ({inp.macd_trend})</span>
                <span>ADX {inp.adx} ({inp.adx_trend})</span>
                <span>Gap {inp.gap_percent}%</span>
              </div>
            </details>
          )}
          {result.score_breakdown && result.score_breakdown.length > 0 && (
            <details className="text-xs">
              <summary className="cursor-pointer text-text-muted">Score breakdown → {result.score}</summary>
              <table className="mt-1 w-full text-xs">
                <thead>
                  <tr className="border-b border-border text-left text-text-muted">
                    <th className="py-1 pr-2 font-medium">Factor</th>
                    <th className="py-1 pr-2 text-right font-medium">Points</th>
                    <th className="py-1 font-medium">Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {result.score_breakdown.filter((it) => it.factor !== "Base").map((it, i) => (
                    <tr key={i} className="border-b border-border/40">
                      <td className="py-1 pr-2">{it.factor}</td>
                      <td className="py-1 pr-2 text-right font-semibold" style={{ color: it.points > 0 ? "#3fb950" : it.points < 0 ? "#f85149" : "#8d969e" }}>
                        {it.points > 0 ? "+" : ""}{it.points}
                      </td>
                      <td className="py-1 text-text-muted">{it.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

function DpsInsights({ symbol, positionId }: { symbol: string; positionId: string }) {
  const [busy, setBusy] = useState(false);
  const [insights, setInsights] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(
        `/api/symbols/${encodeURIComponent(symbol)}/positions/${encodeURIComponent(positionId)}/dps-insights`,
        { method: "POST", headers: { Accept: "application/json" } },
      );
      const data = (await res.json().catch(() => ({}))) as { insights?: string; error?: string };
      if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
      setInsights(data.insights ?? "");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={run}
        disabled={busy}
        className="rounded-[var(--radius-pill)] bg-accent-blue px-3 py-1.5 text-xs text-white disabled:opacity-50"
      >
        {busy ? "⏳ Thinking…" : "🧠 DPS Insights"}
      </button>
      {error && <div className="text-xs text-accent-red">⚠️ {error}</div>}
      {insights && !error && (
        <div
          className="rounded-[var(--radius)] border border-border bg-bg-input p-3 text-sm leading-relaxed [&_strong]:text-text"
          dangerouslySetInnerHTML={{ __html: renderMarkdown(insights) }}
        />
      )}
    </div>
  );
}

// ── Roll table ────────────────────────────────────────────────────────────
// bid/ask/delta/net_credit are `null` (never a fabricated 0/negative value)
// when the candidate's quote is unusable this cycle (Z-R1); the render
// below already treats a "gray" cell (color === "gray") as unavailable and
// falls through to `fmt2`'s "—" for every numeric field, so widening these
// to accept `null` is a type-accuracy fix, not a behavior change.
interface RollCell {
  expiration?: string;
  strike?: number | null;
  bid?: number | null;
  ask?: number | null;
  delta?: number | null;
  net_credit?: number | null;
  color?: string;
}
interface RollRow {
  label?: string;
  strike?: number | null;
  offset?: number;
  cells?: RollCell[];
}
interface RollExp {
  date: string;
  dte?: number;
  is_current?: boolean;
  is_previous?: boolean;
}
interface RollTable {
  error?: string;
  current_position?: { strike?: number | null; expiration?: string; premium_received?: number | null; option_type?: string };
  underlying_price?: number;
  pct_captured?: number | null;
  buyback_cost?: number | null;
  buyback_per_share?: number | null;
  profit_target_reached?: boolean;
  chain_timestamp?: string;
  expirations?: RollExp[];
  rows?: RollRow[];
}

// Shared with Best Options (`lib/badges.ts`'s `ROW_TINT_BG`) so both tables
// paint the same semantic colour with the exact same background tint.
const CELL_BG = ROW_TINT_BG;

function fmt2(v: unknown): string {
  const n = numOrNull(v);
  return n !== null ? n.toFixed(2) : "—";
}

function RollTableView({ symbol, positionId }: { symbol: string; positionId: string }) {
  const [data, setData] = useState<RollTable | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(
          `/api/symbols/${encodeURIComponent(symbol)}/positions/${encodeURIComponent(positionId)}/roll-table`,
          { headers: { Accept: "application/json" } },
        );
        const d = (await res.json().catch(() => ({}))) as RollTable;
        if (cancelled) return;
        if (!res.ok || d.error) {
          setError(d.error || `HTTP ${res.status}`);
        } else {
          setData(d);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Network error");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [symbol, positionId]);

  if (loading) return <p className="text-sm text-text-muted">Calculating roll scenarios…</p>;
  if (error) return <p className="text-sm text-accent-red">⚠️ {error}</p>;
  if (!data) return null;

  const cp = data.current_position ?? {};
  const underlying = data.underlying_price ?? 0;
  const expirations = data.expirations ?? [];
  const rows = data.rows ?? [];
  const pct = typeof data.pct_captured === "number" ? (data.pct_captured * 100).toFixed(1) : null;
  const isPut = /put/i.test(cp.option_type ?? "call");

  const appliedPct = (strike?: number | null): string => {
    if (!underlying || strike == null) return "";
    const p = ((strike - underlying) / underlying) * 100;
    return `${p >= 0 ? "+" : ""}${p.toFixed(1)}%`;
  };

  // Moneyness of the current position's strike vs. the underlying price.
  const moneyness = (strike?: number | null): { label: string; pct: string; color: string } | null => {
    if (!underlying || strike == null) return null;
    const diff = ((strike - underlying) / underlying) * 100;
    const pctLabel = `${diff >= 0 ? "+" : ""}${diff.toFixed(1)}%`;
    if (Math.abs(diff) < 0.5) return { label: "ATM", pct: pctLabel, color: "var(--accent-orange)" };
    const above = diff > 0;
    const label = above ? (isPut ? "ITM" : "OTM") : isPut ? "OTM" : "ITM";
    const color = label === "ITM" ? "var(--accent-red)" : "var(--accent-green)";
    return { label, pct: pctLabel, color };
  };
  const cpMoneyness = moneyness(cp.strike);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        <span><span className="text-text-muted">Strike:</span> <span className="font-mono">${cp.strike ?? "—"}</span></span>
        {cpMoneyness && (
          <span
            className="rounded-[var(--radius-pill)] px-2 py-0.5 font-semibold"
            style={{ background: `${cpMoneyness.color}22`, color: cpMoneyness.color }}
            title="Your position's moneyness vs. current underlying price"
          >
            {cpMoneyness.label} · {cpMoneyness.pct}
          </span>
        )}
        {underlying ? (
          <span><span className="text-text-muted">Underlying:</span> <span className="font-mono">${fmt2(underlying)}</span></span>
        ) : null}
        <span><span className="text-text-muted">Exp:</span> <span className="font-mono">{cp.expiration ?? "—"}</span></span>
        {cp.premium_received ? (
          <span><span className="text-text-muted">Premium rcvd:</span> <span className="font-mono">${fmt2(cp.premium_received)}</span></span>
        ) : null}
        <span><span className="text-text-muted">Buyback:</span> <span className="font-mono">${fmt2(data.buyback_cost)} (${fmt2(data.buyback_per_share)}/sh)</span></span>
        {pct !== null && (
          <span>
            <span className="text-text-muted">Captured:</span>{" "}
            <span className="font-mono" style={{ color: parseFloat(pct) >= 70 ? "var(--accent-green)" : "var(--accent-orange)" }}>{pct}%</span>
          </span>
        )}
        {data.profit_target_reached && (
          <span className="rounded-[var(--radius-pill)] bg-accent-green/20 px-2 py-0.5 text-accent-green">✅ 70%+ captured — consider closing</span>
        )}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-xs">
          <thead>
            <tr>
              <th className="border-b border-border px-2 py-1 text-left">Target</th>
              {expirations.map((exp) => (
                <th
                  key={exp.date}
                  className="border-b border-border px-2 py-1 text-center"
                  style={exp.is_current ? { borderBottom: "2px solid var(--accent-green)", fontWeight: 700 } : undefined}
                >
                  {exp.date}
                  {exp.is_current && <span className="ml-1 text-accent-green">● open</span>}
                  {exp.is_previous && <span className="ml-1 text-text-muted">(prev)</span>}
                  <br />
                  <span className="font-normal text-text-muted">{exp.dte} DTE</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(() => {
              const curStrike = cp.strike != null ? Number(cp.strike) : null;
              const eqStrike = (a?: number | null, b: number | null = curStrike) =>
                a != null && b != null && Math.abs(Number(a) - b) < 1e-6;
              const refRow =
                curStrike != null ? (
                  <tr key="your-strike-ref">
                    <td
                      className="px-2 py-1 text-left font-bold"
                      style={{ color: "#d29922" }}
                    >
                      ◀ Your strike{" "}
                      <span className="font-semibold text-[0.73rem]">
                        ${fmt2(curStrike)}
                        {appliedPct(curStrike) ? ` (${appliedPct(curStrike)})` : ""}
                      </span>
                    </td>
                    <td
                      colSpan={expirations.length}
                      style={{
                        borderTop: "2px dashed #d29922",
                        borderBottom: "2px dashed #d29922",
                        background: "rgba(210,153,34,0.08)",
                      }}
                    />
                  </tr>
                ) : null;

              const out: ReactNode[] = [];
              let refInserted = false;
              rows.forEach((row, ri) => {
                if (
                  curStrike != null &&
                  !refInserted &&
                  row.strike != null &&
                  Number(row.strike) < curStrike
                ) {
                  out.push(refRow);
                  refInserted = true;
                }
                const cellsByExp: Record<string, RollCell> = {};
                (row.cells ?? []).forEach((c) => {
                  if (c.expiration) cellsByExp[c.expiration] = c;
                });
                let label = row.label ?? "";
                if (row.label === "ATM") {
                  label = underlying ? `ATM ($${fmt2(underlying)})` : "ATM";
                } else if (row.offset != null && row.offset !== 0) {
                  const off = Number(row.offset);
                  const pctLabel = `${off > 0 ? "+" : ""}${Math.round(off * 100)}%`;
                  const money = off > 0 ? (isPut ? "ITM" : "OTM") : isPut ? "OTM" : "ITM";
                  label = `${money} (${pctLabel})`;
                }
                out.push(
                  <tr key={ri} className="border-b border-border/40">
                    <td className="px-2 py-1 text-left font-medium">{label}</td>
                    {expirations.map((exp) => {
                      const cell = cellsByExp[exp.date];
                      if (!cell || cell.color === "gray") {
                        return <td key={exp.date} className="px-2 py-1 text-center text-text-muted">—</td>;
                      }
                      const isYou = !!exp.is_current && eqStrike(cell.strike);
                      return (
                        <td
                          key={exp.date}
                          className="px-2 py-1 text-center align-top"
                          style={{
                            background: CELL_BG[cell.color ?? ""] ?? "transparent",
                            ...(isYou ? { outline: "2px solid #d29922", outlineOffset: "-2px" } : {}),
                          }}
                        >
                          {isYou && (
                            <div className="text-[0.62rem] font-bold tracking-wide" style={{ color: "#d29922" }}>
                              ● YOUR POSITION
                            </div>
                          )}
                          <span className="font-semibold">${fmt2(cell.strike)}</span>{" "}
                          <span className="text-text-muted">({appliedPct(cell.strike)})</span>
                          <div className="text-text-muted">{fmt2(cell.bid)}/{fmt2(cell.ask)}</div>
                          <div className="text-text-muted">Δ {fmt2(cell.delta)}</div>
                          <div className="font-semibold">{cell.net_credit != null ? `$${fmt2(cell.net_credit)}` : "—"}</div>
                        </td>
                      );
                    })}
                  </tr>,
                );
              });
              if (curStrike != null && !refInserted) out.push(refRow);
              return out;
            })()}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Detail field helper ───────────────────────────────────────────────────
function DField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-0.5">
      <div className="text-xs uppercase tracking-wide text-text-muted">{label}</div>
      <div className="text-sm text-text">{children}</div>
    </div>
  );
}

function EditableFinancialField({
  symbol,
  positionId,
  label,
  field,
  value,
}: {
  symbol: string;
  positionId: string;
  label: string;
  field: "premium" | "buyback_cost";
  value: number | null | undefined;
}) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value != null ? String(value) : "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    const parsed = Number(draft);
    if (draft.trim() === "" || !Number.isFinite(parsed) || parsed < 0) {
      setError(`${label} must be a non-negative number.`);
      return;
    }

    setSaving(true);
    setError(null);
    try {
      const res = await fetch(
        `/api/symbols/${encodeURIComponent(symbol)}/positions/${encodeURIComponent(positionId)}/${field}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ [field]: parsed }),
        },
      );
      const data = (await res.json().catch(() => ({}))) as { error?: string };
      if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
      setEditing(false);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to update ${label.toLowerCase()}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-1" onClick={(event) => event.stopPropagation()}>
      <div className="text-xs uppercase tracking-wide text-text-muted">{label}</div>
      {editing ? (
        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5">
            <span className="text-sm text-text-muted">$</span>
            <input
              type="number"
              min="0"
              step="0.01"
              inputMode="decimal"
              aria-label={label}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                event.stopPropagation();
                if (event.key === "Enter") {
                  event.preventDefault();
                  void save();
                } else if (event.key === "Escape") {
                  setEditing(false);
                  setError(null);
                }
              }}
              disabled={saving}
              autoFocus
              className="w-24 rounded-[var(--radius)] border border-border bg-bg-input px-2 py-1 font-mono text-sm text-text outline-none focus:border-accent-blue disabled:opacity-60"
            />
            <button
              type="button"
              onClick={() => void save()}
              disabled={saving}
              className="rounded-[var(--radius-pill)] bg-accent-blue px-2.5 py-1 text-xs text-white disabled:opacity-60"
            >
              {saving ? "Saving…" : "Save"}
            </button>
            <button
              type="button"
              onClick={() => {
                setEditing(false);
                setError(null);
              }}
              disabled={saving}
              className="rounded-[var(--radius-pill)] border border-border px-2.5 py-1 text-xs text-text-muted hover:text-text disabled:opacity-60"
            >
              Cancel
            </button>
          </div>
          {error && <div className="text-xs text-accent-red">⚠️ {error}</div>}
        </div>
      ) : (
        <div className="flex items-center gap-2 text-sm text-text">
          <span className="font-mono">{value != null ? `$${fmt2(value)}` : "N/A"}</span>
          <button
            type="button"
            onClick={() => {
              setDraft(value != null ? String(value) : "");
              setError(null);
              setEditing(true);
            }}
            className="rounded-[var(--radius-pill)] border border-border px-2 py-0.5 text-xs text-text-muted hover:border-accent-blue/60 hover:text-accent-blue"
          >
            Edit
          </button>
        </div>
      )}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────
export default function PositionDetail({ symbol, position }: { symbol: string; position: Position }) {
  const posId = position.position_id ?? "";
  const isActive = position.status === "active";
  const source = (position.source ?? {}) as Record<string, unknown>;
  const router = useRouter();
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  async function deletePosition() {
    if (!posId) return;
    if (!confirm("Delete this position permanently?")) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      const res = await fetch(
        `/api/symbols/${encodeURIComponent(symbol)}/positions/${encodeURIComponent(posId)}`,
        { method: "DELETE", headers: { Accept: "application/json" } },
      );
      if (!res.ok) {
        const data = (await res.json().catch(() => ({}))) as { error?: string };
        throw new Error(data.error || `HTTP ${res.status}`);
      }
      router.refresh();
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : "Failed to delete");
      setDeleting(false);
    }
  }

  const [snapshots, setSnapshots] = useState<Snapshot[] | null>(null);
  const [snapError, setSnapError] = useState<string | null>(null);
  const [snapLoading, setSnapLoading] = useState(true);

  const loadSnapshots = useCallback(async () => {
    if (!posId) {
      setSnapLoading(false);
      return;
    }
    setSnapLoading(true);
    try {
      const res = await fetch(
        `/api/symbols/${encodeURIComponent(symbol)}/positions/${encodeURIComponent(posId)}/snapshots`,
        { headers: { Accept: "application/json" } },
      );
      const data = (await res.json().catch(() => ({}))) as { snapshots?: Snapshot[]; error?: string };
      if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
      setSnapshots(data.snapshots ?? []);
    } catch (e) {
      setSnapError(e instanceof Error ? e.message : "Failed to load snapshots");
    } finally {
      setSnapLoading(false);
    }
  }, [symbol, posId]);

  const loaded = useRef(false);
  useEffect(() => {
    if (loaded.current) return;
    loaded.current = true;
    loadSnapshots();
  }, [loadSnapshots]);

  const srcAgent = source.agent_type ? String(source.agent_type) : null;
  const srcReason = source.reason ? String(source.reason) : null;
  const srcTimestamp = source.timestamp ? String(source.timestamp).slice(0, 19) : null;

  return (
    <div className="space-y-4">
      {/* Detail grid */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        <DField label="Type">{(position.type ?? "—").toUpperCase()}</DField>
        <DField label="Strike"><span className="font-mono">${position.strike ?? "—"}</span></DField>
        <DField label="Expiration"><span className="font-mono">{position.expiration ?? "—"}</span></DField>
        <DField label="Status">{position.status ?? "—"}</DField>
        {typeof position.contracts === "number" && <DField label="Contracts">{position.contracts}</DField>}
        <DField label="Opened"><span className="font-mono">{position.opened_at ? String(position.opened_at).slice(0, 10) : "—"}</span></DField>
        {position.closed_at ? <DField label="Closed"><span className="font-mono">{String(position.closed_at).slice(0, 10)}</span></DField> : null}
        {posId ? (
          <EditableFinancialField
            symbol={symbol}
            positionId={posId}
            label="Premium"
            field="premium"
            value={position.display_premium}
          />
        ) : (
          <DField label="Premium"><span className="font-mono">{position.display_premium != null ? `$${fmt2(position.display_premium)}` : "N/A"}</span></DField>
        )}
        {posId ? (
          <EditableFinancialField
            symbol={symbol}
            positionId={posId}
            label="Buyback"
            field="buyback_cost"
            value={position.display_buyback}
          />
        ) : (
          <DField label="Buyback"><span className="font-mono">{position.display_buyback != null ? `$${fmt2(position.display_buyback)}` : "N/A"}</span></DField>
        )}
        {position.assignment_risk && <DField label="Assignment Risk">{position.assignment_risk}</DField>}
        {position.moneyness && <DField label="Moneyness">{position.moneyness}</DField>}
        {srcAgent && <DField label="Source Agent">{srcAgent}</DField>}
        {srcTimestamp && <DField label="Signal Time"><span className="font-mono">{srcTimestamp}</span></DField>}
      </div>

      {srcReason && (
        <div className="rounded-[var(--radius)] border-l-2 border-accent-blue bg-bg-input px-3 py-2">
          <div className="text-xs uppercase tracking-wide text-text-muted">Reason</div>
          <div className="whitespace-pre-wrap text-sm text-text">{srcReason}</div>
        </div>
      )}

      <div>
        <DField label="Notes">{position.notes || "—"}</DField>
      </div>

      {/* Monitoring history */}
      <div className="border-t border-dashed border-border pt-3">
        <h4 className="mb-2 text-sm font-semibold">📈 Monitoring History</h4>
        {snapLoading ? (
          <p className="text-sm text-text-muted">Loading monitoring data…</p>
        ) : snapError ? (
          <p className="text-sm text-accent-red">⚠️ {snapError}</p>
        ) : (
          <SnapshotChart snapshots={snapshots ?? []} />
        )}
      </div>

      {/* Active-only: DPS + roll scenarios */}
      {isActive && posId && (
        <>
          <div className="flex flex-wrap gap-3 border-t border-dashed border-border pt-3">
            <DpsAnalysis symbol={symbol} positionId={posId} />
            <DpsInsights symbol={symbol} positionId={posId} />
          </div>
          <div className="border-t border-dashed border-border pt-3">
            <h4 className="mb-2 text-sm font-semibold">🔄 Roll Scenarios</h4>
            <RollTableView symbol={symbol} positionId={posId} />
          </div>
        </>
      )}

      {/* Delete */}
      {posId && (
        <div className="flex items-center justify-end gap-3 border-t border-dashed border-border pt-3">
          {deleteError && <span className="text-xs text-accent-red">⚠️ {deleteError}</span>}
          <button
            type="button"
            onClick={deletePosition}
            disabled={deleting}
            className="rounded-[var(--radius-pill)] border border-accent-red/40 bg-accent-red/10 px-3 py-1.5 text-xs text-accent-red transition hover:bg-accent-red/20 disabled:opacity-50"
          >
            {deleting ? "Deleting…" : "🗑 Delete Position"}
          </button>
        </div>
      )}
    </div>
  );
}
