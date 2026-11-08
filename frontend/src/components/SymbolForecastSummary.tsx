import Link from "next/link";
import { apiFetch } from "@/lib/api";
import { symbolHref } from "@/lib/symbolEncoding";
import { latestRow } from "@/lib/latestForecastRow";
import { ProjectionFanChart } from "@/components/ForecastCharts";
import type { ForecastsResponse } from "@/types/forecasts";

export default async function SymbolForecastSummary({ symbol }: { symbol: string }) {
  let data: ForecastsResponse;
  try {
    data = await apiFetch<ForecastsResponse>(
      `/api/symbols/${encodeURIComponent(symbol)}/forecasts?range=90d`,
    );
    if (data.error) throw new Error(data.error);
  } catch (err) {
    return (
      <div role="alert" className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
        ⚠️ {err instanceof Error ? err.message : "Failed to load forecasts"}
      </div>
    );
  }

  const row = latestRow(data.rows ?? []);
  const slope = typeof row?.trend?.slope === "number" && Number.isFinite(row.trend.slope)
    ? row.trend.slope : null;
  const r2 = typeof row?.trend?.r2 === "number" && Number.isFinite(row.trend.r2)
    ? row.trend.r2 : null;
  return (
    <Link
      href={`${symbolHref(symbol)}/forecasts?range=90d`}
      aria-label={`View ${symbol} price forecasts, 90-day range`}
      className="block rounded-[var(--radius)] border border-border bg-bg-card p-4 transition hover:border-accent-blue/40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-blue"
    >
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold">Projection fan (latest)</h3>
        {row?.created_date && (
          <span className="font-mono text-xs text-text-muted">{row.created_date}</span>
        )}
      </div>
      {row && (
        <div className="mb-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-text-muted">
          <span>
            Price at creation:{" "}
            <strong className="text-text">
              {typeof row.price_at_creation === "number" && Number.isFinite(row.price_at_creation)
                ? `$${row.price_at_creation.toFixed(2)}` : "—"}
            </strong>
          </span>
          <span>
            HV:{" "}
            <strong className="text-text">
              {typeof row.hv === "number" && Number.isFinite(row.hv)
                ? `${(row.hv * 100).toFixed(1)}%` : "—"}
            </strong>
          </span>
          <span>
            Vol source: <strong className="text-text">{row.vol_source || "—"}</strong>
          </span>
          <span>
            Bias:{" "}
            <strong className="text-text">
              {typeof row.bias === "number" && Number.isFinite(row.bias)
                ? row.bias.toFixed(2) : "—"}
            </strong>
          </span>
          {slope != null && (
            <span title="Robust (Theil-Sen) per-session drift, shrunk by fit quality R².">
              Trend:{" "}
              <strong className="text-text">
                {slope >= 0 ? "+" : ""}{slope.toFixed(2)} $/session
              </strong>
              {r2 != null ? ` · R² ${r2.toFixed(2)}` : ""}
            </span>
          )}
          {row.flags?.earnings_in_window === true && <span>📅 earnings in window</span>}
          {row.flags?.exdiv_in_window === true && <span>💰 ex-div in window</span>}
        </div>
      )}
      <ProjectionFanChart
        row={row}
        calibration={{ hitRate: data.hit_rate ?? {}, windowLabel: "90d" }}
      />
    </Link>
  );
}
