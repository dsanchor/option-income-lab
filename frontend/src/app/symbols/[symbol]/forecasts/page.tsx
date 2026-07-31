import Link from "next/link";
import { apiFetch } from "@/lib/api";
import { HORIZONS } from "@/types/forecasts";
import type { ForecastsResponse, ForecastRow, Horizon } from "@/types/forecasts";

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

function readingClass(reading: unknown): string {
  const r = String(reading ?? "").toLowerCase();
  if (r.includes("bull") || r.includes("up")) return "text-accent-green";
  if (r.includes("bear") || r.includes("down")) return "text-accent-red";
  return "text-text-muted";
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div className="rounded-[var(--radius)] border border-border bg-bg-card px-4 py-3">
      <div className="text-xs uppercase tracking-wide text-text-muted">{label}</div>
      <div className={`mt-1 font-mono text-lg ${accent ?? ""}`}>{value}</div>
    </div>
  );
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
        <Stat label="Predictions" value={String(d.count ?? rows.length)} />
        <Stat label="Confidence" value={pctVal((d.confidence ?? 0) * 100)} accent="text-accent-blue" />
        <Stat label="Outer band" value={pctVal((d.outer_confidence ?? 0) * 100)} />
        <Stat
          label="Calibration k"
          value={d.calibration != null ? num(d.calibration, 2) : "—"}
          accent="text-accent-purple"
        />
      </div>

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

      {/* Prediction history */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Prediction history</h2>
        <div className="overflow-x-auto rounded-[var(--radius)] border border-border bg-bg-card">
          <table className="w-full min-w-[820px] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
                <th className="px-4 py-3 font-medium">Created</th>
                <th className="px-4 py-3 font-medium">Window</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 text-right font-medium">Anchor</th>
                <th className="px-4 py-3 text-right font-medium">HV</th>
                <th className="px-4 py-3 font-medium">Reading</th>
                {HORIZONS.map((h) => (
                  <th key={h} className="px-4 py-3 text-right font-medium uppercase">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr>
                  <td colSpan={6 + HORIZONS.length} className="px-4 py-6 text-center text-text-muted">
                    No forecasts in this range.
                  </td>
                </tr>
              )}
              {rows.map((r: ForecastRow, i) => (
                <tr key={r.id ?? i} className="border-b border-border/60 last:border-0">
                  <td className="px-4 py-3 font-mono">{r.created_date ?? "—"}</td>
                  <td className="px-4 py-3 font-mono text-text-muted">
                    {r.start_date ?? "?"} → {r.end_date ?? "?"}
                  </td>
                  <td className="px-4 py-3 capitalize">{r.status ?? "—"}</td>
                  <td className="px-4 py-3 text-right font-mono">
                    {r.price_at_creation != null ? `$${num(r.price_at_creation)}` : "—"}
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-text-muted">
                    {r.hv != null ? pctVal(r.hv * 100, 1) : "—"}
                  </td>
                  <td className={`px-4 py-3 ${readingClass(r.reading)}`}>{r.reading ?? "—"}</td>
                  {HORIZONS.map((h) => {
                    const hz = r.horizons?.[h];
                    return (
                      <td key={h} className="px-4 py-3 text-right font-mono">
                        {hz?.center != null ? `$${num(hz.center)}` : "—"}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-text-muted">
          Per-horizon columns show the predicted center price for each window (1d / 1w / 2w / 4w).
        </p>
      </section>
    </div>
  );
}
