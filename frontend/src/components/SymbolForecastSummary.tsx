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
      <p className="mb-2 text-xs text-text-muted">
        Latest price bands · Historical window: 90d.
        Hover a horizon for realized hit-rates and resolved sample counts.
        Dashed line = anchor. Open price forecasts →
      </p>
      <ProjectionFanChart
        row={row}
        calibration={{ hitRate: data.hit_rate ?? {}, windowLabel: "90d" }}
      />
    </Link>
  );
}
