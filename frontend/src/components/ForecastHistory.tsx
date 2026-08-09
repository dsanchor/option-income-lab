"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { AnimatePresence, motion } from "motion/react";
import { toast } from "sonner";
import { HORIZONS, HORIZON_END_SESSION } from "@/types/forecasts";
import type {
  ForecastRow,
  ForecastDetail,
  HorizonSummary,
} from "@/types/forecasts";

const TRADING_DAYS = 252;
const CONF_Z: Record<string, number> = {
  "0.5": 0.6745, "0.68": 1.0, "0.8": 1.2816, "0.9": 1.6449, "0.95": 1.96, "0.99": 2.5758,
};

function zFor(conf: number | null | undefined, dflt: number): number {
  if (conf == null) return dflt;
  const z = CONF_Z[String(conf)];
  return z != null ? z : dflt;
}

function num(v: number | null | undefined, d = 2): string {
  return typeof v === "number" && isFinite(v) ? v.toFixed(d) : "—";
}
function pct(v: number | null | undefined, d = 1): string {
  return typeof v === "number" && isFinite(v) ? `${v.toFixed(d)}%` : "—";
}

type ReadingObj = {
  code?: string;
  label?: string;
  icon?: string;
  conviction?: string;
  csp?: string;
  cc?: string;
  reason?: string;
};

function readingCell(reading: unknown) {
  if (reading == null) return <span className="text-text-muted">—</span>;
  if (typeof reading === "string") return <span>{reading}</span>;
  const o = reading as ReadingObj;
  const col =
    o.code === "bull"
      ? "text-accent-green"
      : o.code === "bear"
        ? "text-accent-red"
        : o.code === "top" || o.code === "bottom"
          ? "text-accent-orange"
          : "text-text-muted";
  const sym: Record<string, string> = { favorable: "✅", caution: "⚠️", avoid: "❌", neutral: "·" };
  const guide = `CSP ${sym[o.csp ?? ""] ?? "·"} · CC ${sym[o.cc ?? ""] ?? "·"}`;
  return (
    <span title={o.reason ?? ""} className="cursor-help">
      <span className={`font-semibold ${col}`}>
        {o.icon ? `${o.icon} ` : ""}
        {o.label ?? o.code ?? ""}
      </span>
      {o.conviction && o.conviction !== "none" && (
        <span className="ml-1 text-[0.68rem] opacity-70">({o.conviction})</span>
      )}
      <span className="block text-[0.7rem] text-text-muted">{guide}</span>
    </span>
  );
}

function HorizonCell({
  h,
  confPct,
  outerPct,
  resolvedToday,
}: {
  h: HorizonSummary | undefined;
  confPct: number;
  outerPct: number;
  resolvedToday?: boolean;
}) {
  if (!h) return <span className="text-text-muted">—</span>;
  const ep = h.endpoint;
  const inside1 = ep?.inside_1sigma;
  const inside2 = ep?.inside_2sigma;
  const icon = ep ? (inside1 ? "✓" : inside2 ? "±" : "✗") : null;
  const iconCol = inside1 ? "text-accent-green" : inside2 ? "text-accent-orange" : "text-accent-red";
  const epTitle = ep
    ? `Endpoint close $${num(ep.price)}${ep.date ? ` (${ep.date})` : ""} — ${
        inside1
          ? `inside ${confPct}% band`
          : inside2
            ? `inside ${outerPct}% band only`
            : `outside ${outerPct}% band`
      }`
    : "";
  return (
    <div
      className={`leading-tight ${
        resolvedToday ? "-mx-1 rounded-md bg-accent-blue/10 px-1 ring-1 ring-accent-blue/40" : ""
      }`}
    >
      {h.low1 != null && h.high1 != null ? (
        <div
          className="font-semibold"
          title={`${confPct}% predicted range${
            h.low2 != null && h.high2 != null ? ` · ${outerPct}% $${num(h.low2)}–$${num(h.high2)}` : ""
          }`}
        >
          ${num(h.low1)}–${num(h.high1)}
        </div>
      ) : (
        <div className="text-text-muted">—</div>
      )}
      {h.trend_end != null && (
        <div className="text-[0.72rem] text-accent-blue" title="Linear-trend projection at the horizon end session">
          ↳ ${num(h.trend_end)}
          {h.mean_dev != null && (
            <span className="text-text-muted" title="Mean deviation of closes from the trend line">
              {" "}
              ±${num(h.mean_dev)}
              {h.mean_dev_pct != null ? ` / ${num(h.mean_dev_pct, 1)}%` : ""}
            </span>
          )}
        </div>
      )}
      <div className="text-[0.72rem] text-text-muted">
        {h.path_count > 0 && (
          <span title={`${h.path_count} snapshot(s); % inside the ${confPct}% band`}>{pct(h.path_pct_1sigma)}</span>
        )}
        {icon && (
          <strong className={`ml-1 ${iconCol}`} title={epTitle}>
            {icon}
          </strong>
        )}
        {resolvedToday && <span className="ml-1 text-accent-blue" title="Resolved on the latest close">•</span>}
        {h.path_count === 0 && !icon && "·"}
      </div>
    </div>
  );
}

// ---------- Fan chart ----------

const CENTER_COLOR = "rgba(148,163,184,0.7)";
const TREND_COLOR = "#38bdf8";
const BAND_COLOR = "#5b61ff";

type FanDatum = {
  session: number;
  center: number;
  band1: [number, number];
  band2: [number, number];
  trend: number | null;
  snap: number | null;
  snapInside1?: boolean | null;
  snapInside2?: boolean | null;
  ep: number | null;
};

function buildFanData(doc: ForecastDetail): FanDatum[] {
  const price = doc.price_at_creation;
  const hv = doc.hv;
  if (price == null || hv == null) return [];
  const z1 = zFor(doc.confidence, 1.0);
  const z2 = zFor(doc.outer_confidence, 1.96);

  const slopeForSession = (s: number): number | null => {
    const hs = doc.horizons ?? {};
    for (const name of HORIZONS) {
      const b = hs[name];
      if (
        b?.trend_slope != null &&
        b.start_session != null &&
        b.end_session != null &&
        s >= b.start_session &&
        s <= b.end_session
      ) {
        return b.trend_slope;
      }
    }
    if (hs["1d"]?.trend_slope != null) return hs["1d"].trend_slope;
    return doc.trend?.slope ?? null;
  };

  const snapByOffset = new Map<number, { price: number | null; i1: boolean | null; i2: boolean | null }>();
  for (const sn of doc.snapshots ?? []) {
    if (sn.offset != null) snapByOffset.set(sn.offset, { price: sn.price, i1: sn.inside_1sigma, i2: sn.inside_2sigma });
  }
  const epByOffset = new Map<number, number>();
  for (const h of HORIZONS) {
    const e = doc.endpoints?.[h];
    if (e?.price != null) epByOffset.set(HORIZON_END_SESSION[h], e.price);
  }

  const out: FanDatum[] = [];
  for (let s = 0; s <= 20; s++) {
    const sg = price * hv * Math.sqrt(s / TRADING_DAYS);
    const sl = slopeForSession(s);
    const snap = snapByOffset.get(s);
    out.push({
      session: s,
      center: price,
      band1: [price - z1 * sg, price + z1 * sg],
      band2: [price - z2 * sg, price + z2 * sg],
      trend: sl != null ? price + sl * s : null,
      snap: snap?.price ?? null,
      snapInside1: snap?.i1 ?? null,
      snapInside2: snap?.i2 ?? null,
      ep: epByOffset.get(s) ?? null,
    });
  }
  return out;
}

function SnapDot(props: { cx?: number; cy?: number; payload?: FanDatum }) {
  const { cx, cy, payload } = props;
  if (cx == null || cy == null || payload?.snap == null) return <g />;
  const fill = payload.snapInside1 ? "#00c493" : payload.snapInside2 ? "#f59e0b" : "#ef4444";
  return <circle cx={cx} cy={cy} r={4} fill={fill} stroke="rgba(0,0,0,0.35)" strokeWidth={1} />;
}

function EpDot(props: { cx?: number; cy?: number; payload?: FanDatum }) {
  const { cx, cy, payload } = props;
  if (cx == null || cy == null || payload?.ep == null) return <g />;
  return (
    <rect
      x={cx - 5}
      y={cy - 5}
      width={10}
      height={10}
      transform={`rotate(45 ${cx} ${cy})`}
      fill="#ffffff"
      stroke="rgba(15,23,42,0.8)"
      strokeWidth={1.5}
    />
  );
}

function FanChart({ doc }: { doc: ForecastDetail }) {
  const data = buildFanData(doc);
  if (!data.length) return <p className="text-sm text-text-muted">Not enough data to draw the cone.</p>;
  const anchor = doc.price_at_creation!;
  return (
    <div style={{ height: 380 }}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 16, bottom: 20, left: 6 }}>
          <CartesianGrid stroke="rgba(148,163,184,0.08)" strokeDasharray="3 3" />
          <XAxis
            dataKey="session"
            type="number"
            domain={[0, 20]}
            ticks={[0, 1, 5, 10, 20]}
            tick={{ fill: "#8d969e", fontSize: 10 }}
            tickLine={false}
            axisLine={{ stroke: "rgba(148,163,184,0.15)" }}
            label={{ value: "Trading sessions since creation", position: "insideBottom", offset: -10, fill: "#8d969e", fontSize: 10 }}
          />
          <YAxis
            tick={{ fill: "#8d969e", fontSize: 10 }}
            tickLine={false}
            axisLine={{ stroke: "rgba(148,163,184,0.15)" }}
            tickFormatter={(v) => `$${Number(v).toFixed(0)}`}
            width={52}
            domain={["auto", "auto"]}
          />
          <ReferenceLine y={anchor} stroke="rgba(148,163,184,0.3)" strokeDasharray="4 4" />
          <Tooltip
            cursor={{ stroke: "rgba(148,163,184,0.25)" }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const p = payload[0].payload as FanDatum;
              return (
                <div className="rounded-[10px] border border-border bg-bg-card px-3 py-2 text-xs shadow-lg">
                  <div className="mb-1 font-medium text-text">Session {p.session}</div>
                  <div className="flex items-center gap-2">
                    <span className="text-text-muted">±1σ</span>
                    <span className="ml-auto font-mono text-text-muted">${num(p.band1[0])} – ${num(p.band1[1])}</span>
                  </div>
                  {p.trend != null && (
                    <div className="flex items-center gap-2">
                      <span className="text-text-muted">Trend</span>
                      <span className="ml-auto font-mono" style={{ color: TREND_COLOR }}>${num(p.trend)}</span>
                    </div>
                  )}
                  {p.snap != null && (
                    <div className="flex items-center gap-2">
                      <span className="text-text-muted">Close</span>
                      <span className="ml-auto font-mono text-text">${num(p.snap)}</span>
                    </div>
                  )}
                  {p.ep != null && (
                    <div className="flex items-center gap-2">
                      <span className="text-text-muted">Endpoint</span>
                      <span className="ml-auto font-mono text-text">${num(p.ep)}</span>
                    </div>
                  )}
                </div>
              );
            }}
          />
          <Area dataKey="band2" name="±2σ" stroke="none" fill={BAND_COLOR} fillOpacity={0.07} isAnimationActive={false} />
          <Area dataKey="band1" name="±1σ" stroke="none" fill={BAND_COLOR} fillOpacity={0.16} isAnimationActive={false} />
          <Line dataKey="center" name="Center" stroke={CENTER_COLOR} strokeWidth={1} strokeDasharray="4 4" dot={false} isAnimationActive={false} />
          <Line dataKey="trend" name="Trend" stroke={TREND_COLOR} strokeWidth={2} strokeDasharray="6 3" dot={false} connectNulls isAnimationActive={false} />
          <Scatter dataKey="snap" name="Close" shape={<SnapDot />} isAnimationActive={false} />
          <Scatter dataKey="ep" name="Endpoint" shape={<EpDot />} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
      <div className="mt-1 flex flex-wrap justify-center gap-x-4 gap-y-1 text-[0.7rem] text-text-muted">
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-3 rounded-sm" style={{ background: BAND_COLOR, opacity: 0.3 }} /> ±1σ / ±2σ band
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-[2px] w-4" style={{ background: TREND_COLOR }} /> Trend
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-2 rounded-full" style={{ background: "#00c493" }} /> Close (green/amber/red)
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-2 rotate-45 bg-white" /> Endpoint ◆
        </span>
      </div>
    </div>
  );
}

// ---------- Modal ----------

function DetailModal({
  symbol,
  id,
  onClose,
}: {
  symbol: string;
  id: string;
  onClose: () => void;
}) {
  const [doc, setDoc] = useState<ForecastDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    fetch(`/api/symbols/${encodeURIComponent(symbol)}/forecasts/${encodeURIComponent(id)}`)
      .then((r) => r.json())
      .then((d: ForecastDetail) => {
        if (!alive) return;
        if (d.error) {
          setErr(d.error);
          toast.error(`Failed to load forecast: ${d.error}`);
        } else setDoc(d);
      })
      .catch((e) => {
        if (!alive) return;
        const msg = e instanceof Error ? e.message : "Failed to load";
        setErr(msg);
        toast.error(`Failed to load forecast: ${msg}`);
      });
    return () => {
      alive = false;
    };
  }, [symbol, id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const slope = doc?.trend?.slope;
  const r2 = doc?.trend?.r2;

  return (
    <motion.div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.18 }}
    >
      <motion.div
        className="max-h-[90vh] w-full max-w-[880px] overflow-y-auto rounded-[var(--radius)] border border-border bg-bg-card shadow-xl"
        onClick={(e) => e.stopPropagation()}
        initial={{ opacity: 0, scale: 0.96, y: 16 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.96, y: 16 }}
        transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
      >
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <h3 className="text-sm font-semibold">
            {symbol} — Forecast {doc?.created_date ?? ""}
          </h3>
          <button onClick={onClose} className="text-lg text-text-muted hover:text-text">
            &times;
          </button>
        </div>
        <div className="px-5 py-4">
          {err && (
            <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-3 py-2 text-sm">
              ⚠️ {err}
            </div>
          )}
          {!err && !doc && <p className="text-sm text-text-muted">Loading…</p>}
          {doc && (
            <>
              <div className="mb-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-text-muted">
                <span>
                  Price at creation:{" "}
                  <strong className="text-text">${num(doc.price_at_creation)}</strong>
                </span>
                <span>
                  HV: <strong className="text-text">{doc.hv != null ? `${(doc.hv * 100).toFixed(1)}%` : "—"}</strong>
                </span>
                <span>
                  Vol source: <strong className="text-text">{doc.vol_source ?? "hv"}</strong>
                </span>
                <span>
                  Bias: <strong className="text-text">{num(doc.bias)}</strong>
                </span>
                {slope != null && (
                  <span title="Robust (Theil-Sen) per-session drift, shrunk by fit quality R².">
                    Trend:{" "}
                    <strong className="text-text">
                      {slope >= 0 ? "+" : ""}
                      {num(slope)} $/session
                    </strong>
                    {r2 != null ? ` · R² ${r2.toFixed(2)}` : ""}
                  </span>
                )}
                {(doc.flags?.earnings_in_window as boolean) && <span>📅 earnings in window</span>}
                {(doc.flags?.exdiv_in_window as boolean) && <span>💰 ex-div in window</span>}
              </div>
              <FanChart doc={doc} />
              <p className="mt-2 text-[0.68rem] text-text-muted">
                X = trading sessions since creation (0→20). Shaded bands = inner/outer volatility cone.
                Cyan dashed = robust trend. Dots = daily closes (green inside ±1σ, amber ±2σ, red outside).
                ◆ = horizon endpoint. Dashed grey line = anchor price.
              </p>
            </>
          )}
        </div>
      </motion.div>
    </motion.div>
  );
}

// ---------- Table ----------

export default function ForecastHistory({
  symbol,
  rows,
  confidence,
  outerConfidence,
}: {
  symbol: string;
  rows: ForecastRow[];
  confidence: number;
  outerConfidence: number;
}) {
  const [openId, setOpenId] = useState<string | null>(null);
  const confPct = Math.round((confidence ?? 0.68) * 100);
  const outerPct = Math.round((outerConfidence ?? 0.95) * 100);
  const close = useCallback(() => setOpenId(null), []);

  // Latest resolved-close date across all rows = "today" (last market session).
  // Rows whose horizon endpoint resolved on this date get highlighted.
  let latestClose = "";
  for (const r of rows) {
    for (const h of HORIZONS) {
      const dt = r.horizons?.[h]?.endpoint?.date;
      if (dt && dt > latestClose) latestClose = dt;
    }
  }

  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold">Prediction history</h2>
      <div className="overflow-x-auto rounded-[var(--radius)] border border-border bg-bg-card">
        <table className="w-full min-w-[920px] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
              <th className="px-4 py-3 font-medium">Created</th>
              <th className="px-4 py-3 text-right font-medium">Anchor</th>
              <th className="px-4 py-3 text-right font-medium">Bias</th>
              <th className="px-4 py-3 font-medium">Reading</th>
              {HORIZONS.map((h) => (
                <th key={h} className="px-4 py-3 text-right font-medium uppercase">
                  {h}
                </th>
              ))}
              <th className="px-4 py-3 font-medium">Flags</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={5 + HORIZONS.length + 1} className="px-4 py-6 text-center text-text-muted">
                  No forecasts in this range.
                </td>
              </tr>
            )}
            {rows.map((r, i) => {
              const bias = r.bias ?? 0;
              const arrow = bias > 0.1 ? "↑" : bias < -0.1 ? "↓" : "→";
              const biasCol = bias > 0.1 ? "text-accent-green" : bias < -0.1 ? "text-accent-red" : "text-text-muted";
              const flags: string[] = [];
              const f = r.flags ?? {};
              if (f.earnings_in_window) flags.push("📅");
              if (f.exdiv_in_window) flags.push("💰");
              const rowResolvedToday =
                !!latestClose && HORIZONS.some((h) => r.horizons?.[h]?.endpoint?.date === latestClose);
              return (
                <tr
                  key={r.id ?? i}
                  onClick={() => r.id && setOpenId(r.id)}
                  className={`cursor-pointer border-b border-border/60 transition last:border-0 hover:bg-white/[0.03] ${
                    rowResolvedToday ? "bg-accent-blue/[0.06] shadow-[inset_3px_0_0_var(--color-accent-blue)]" : ""
                  }`}
                  title={rowResolvedToday ? `A horizon of this forecast resolved on the latest close (${latestClose})` : undefined}
                >
                  <td className="px-4 py-3 font-mono align-top">
                    {r.created_date ?? "—"}
                    {r.status === "closed" && <span className="ml-1 opacity-60">·closed</span>}
                  </td>
                  <td className="px-4 py-3 text-right font-mono align-top">
                    {r.price_at_creation != null ? `$${num(r.price_at_creation)}` : "—"}
                  </td>
                  <td className={`px-4 py-3 text-right font-mono align-top ${biasCol}`} title={`Directional bias ${num(bias)}`}>
                    {arrow} {num(bias)}
                  </td>
                  <td className="px-4 py-3 align-top">{readingCell(r.reading)}</td>
                  {HORIZONS.map((h) => (
                    <td key={h} className="px-4 py-3 text-right font-mono align-top">
                      <HorizonCell
                        h={r.horizons?.[h]}
                        confPct={confPct}
                        outerPct={outerPct}
                        resolvedToday={!!latestClose && r.horizons?.[h]?.endpoint?.date === latestClose}
                      />
                    </td>
                  ))}
                  <td className="px-4 py-3 align-top">{flags.join(" ")}</td>
                  <td className="px-4 py-3 align-top text-right">
                    <span className="rounded-[var(--radius-pill)] border border-border px-2 py-1 text-xs text-text-muted">
                      Chart
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-text-muted">
        Each horizon shows the <strong>{confPct}% predicted range</strong>, the <strong>trend</strong> projection at the
        horizon end (↳) with mean deviation, then <strong>% of snapshots inside</strong> the band and the resolved{" "}
        <strong>endpoint</strong> result (✓ inside ±1σ · ± inside ±2σ · ✗ outside). Click a row for the fan chart.
        {latestClose && (
          <>
            {" "}
            <span className="text-accent-blue">Highlighted rows</span> have a horizon that resolved on the latest close ({latestClose}).
          </>
        )}
      </p>
      <AnimatePresence>
        {openId && <DetailModal symbol={symbol} id={openId} onClose={close} />}
      </AnimatePresence>
    </section>
  );
}
