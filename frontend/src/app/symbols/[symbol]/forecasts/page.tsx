import Link from "next/link";
import { apiFetch } from "@/lib/api";
import StatCard from "@/components/StatCard";
import ForecastCharts from "@/components/ForecastCharts";
import ForecastHistory from "@/components/ForecastHistory";
import { HORIZONS } from "@/types/forecasts";
import type { ForecastsResponse, ForecastCalibration, Horizon } from "@/types/forecasts";

export const dynamic = "force-dynamic";

const RANGES = ["1d", "7d", "30d", "90d"] as const;

async function getData(symbol: string, range: string): Promise<ForecastsResponse> {
  try {
    return await apiFetch<ForecastsResponse>(
      `/api/symbols/${encodeURIComponent(symbol)}/forecasts?range=${encodeURIComponent(range)}`,
    );
  } catch (err) {
    return { error: err instanceof Error ? err.message : "Failed to load forecasts" } as ForecastsResponse;
  }
}

function num(n: number | null | undefined, digits = 2): string {
  return typeof n === "number" && isFinite(n) ? n.toFixed(digits) : "—";
}

function pctVal(n: number | null | undefined, digits = 0): string {
  return typeof n === "number" && isFinite(n) ? `${n.toFixed(digits)}%` : "—";
}

export default async function ForecastsPage({
  params,
  searchParams,
}: {
  params: Promise<{ symbol: string }>;
  searchParams: Promise<{ range?: string }>;
}) {
  const { symbol } = await params;
  const sp = await searchParams;
  const range = RANGES.includes((sp.range ?? "") as (typeof RANGES)[number]) ? sp.range! : "30d";
  const d = await getData(symbol, range);

  if (d.error) {
    return (
      <div className="space-y-4">
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
          ⚠️ {d.error}
        </div>
      </div>
    );
  }

  const rows = d.rows ?? [];
  const cal: ForecastCalibration | null =
    d.calibration && typeof d.calibration === "object" ? d.calibration : null;
  const k = cal?.k ?? (typeof d.calibration === "number" ? d.calibration : null);
  const kHint =
    cal?.applied === false
      ? `warming up${cal?.n != null ? ` · n=${cal.n}` : ""}`
      : cal?.n != null
        ? `n=${cal.n}`
        : undefined;
  const confPct = Math.round((d.confidence ?? 0.68) * 100);
  const outerPct = Math.round((d.outer_confidence ?? 0.95) * 100);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">🎯 {d.symbol} Forecasts</h1>
          <p className="text-sm text-text-muted">
            Deterministic price-forecast history and rolling calibration.
          </p>
        </div>
        <div className="flex items-center gap-1 rounded-[var(--radius-pill)] border border-border bg-bg-card p-1">
          {RANGES.map((r) => (
            <Link
              key={r}
              href={`/symbols/${symbol}/forecasts?range=${r}`}
              className={`rounded-[var(--radius-pill)] px-3 py-1 text-xs transition ${
                r === range ? "bg-accent-blue text-white" : "text-text-muted hover:text-text"
              }`}
            >
              {r}
            </Link>
          ))}
        </div>
      </div>

      {/* Top-line stats */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Predictions"
          value={d.count ?? rows.length}
          tone="neutral"
          tooltip="Number of price forecasts generated in the selected date range."
        />
        <StatCard
          label="Confidence"
          value={confPct}
          suffix="%"
          tone="blue"
          tooltip="Central probability of the inner (±1σ) predicted price band. The band is sized so the actual close should land inside it about this often."
        />
        <StatCard
          label="Outer band"
          value={outerPct}
          suffix="%"
          tone="green"
          tooltip="Probability of the wider (±2σ) band. The close should almost always fall within it — breaches signal an unusually large move."
        />
        <StatCard
          label="Calibration k"
          value={k != null ? k : undefined}
          display={k != null ? undefined : "—"}
          prefix="×"
          decimals={2}
          tone="purple"
          hint={kHint}
          tooltip="Per-symbol volatility multiplier applied to the band width so this symbol's realized hit-rate drifts toward the confidence target. k<1 narrows an over-wide band, k>1 widens an over-narrow one. Auto-adjusts each run from recent resolved endpoints; shows “—” until enough have resolved (warming up)."
        />
      </div>

      {/* Charts: projection fan + calibration hit-rate */}
      <ForecastCharts rows={rows} hitRate={d.hit_rate ?? {}} />

      {/* Endpoint hit-rate + averages per horizon */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Per-horizon calibration</h2>
        <div className="overflow-x-auto rounded-[var(--radius)] border border-border bg-bg-card">
          <table className="w-full min-w-[720px] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
                <th className="px-4 py-3 font-medium">Horizon</th>
                <th className="px-4 py-3 text-right font-medium">Resolved</th>
                <th className="px-4 py-3 text-right font-medium">Hit ±1σ</th>
                <th className="px-4 py-3 text-right font-medium">Hit ±2σ</th>
                <th className="px-4 py-3 text-right font-medium">Direction</th>
                <th className="px-4 py-3 text-right font-medium">Mean dev</th>
                <th className="px-4 py-3 text-right font-medium">Proj. mean</th>
                <th className="px-4 py-3 text-right font-medium">±1σ range</th>
              </tr>
            </thead>
            <tbody>
              {HORIZONS.map((h: Horizon) => {
                const hr = d.hit_rate?.[h];
                const av = d.averages?.[h];
                return (
                  <tr key={h} className="border-b border-border/60 last:border-0">
                    <td className="px-4 py-3 font-mono font-semibold uppercase">{h}</td>
                    <td className="px-4 py-3 text-right font-mono">{hr?.resolved ?? 0}</td>
                    <td className="px-4 py-3 text-right font-mono">{pctVal(hr?.hit_pct_1sigma, 1)}</td>
                    <td className="px-4 py-3 text-right font-mono">{pctVal(hr?.hit_pct_2sigma, 1)}</td>
                    <td className="px-4 py-3 text-right font-mono">{pctVal(hr?.direction_pct, 1)}</td>
                    <td className="px-4 py-3 text-right font-mono">{pctVal(hr?.mean_dev_pct, 2)}</td>
                    <td className="px-4 py-3 text-right font-mono">
                      {av?.mean != null ? `$${num(av.mean)}` : "—"}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-text-muted">
                      {av?.low != null && av?.high != null ? `$${num(av.low)}–$${num(av.high)}` : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* Prediction history (rich cells + click-through fan chart) */}
      <ForecastHistory
        symbol={symbol}
        rows={rows}
        confidence={d.confidence ?? 0.68}
        outerConfidence={d.outer_confidence ?? 0.95}
      />
    </div>
  );
}
