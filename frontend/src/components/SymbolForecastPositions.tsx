"use client";

import { useQuery } from "@tanstack/react-query";
import { ProjectionFanChart } from "@/components/ForecastCharts";
import { forecastOptionPositions } from "@/lib/forecastOptionPositions";
import { symbolDetailQueryOptions } from "@/lib/symbol-detail-query";
import type { ForecastRow, Horizon, HitRateEntry } from "@/types/forecasts";

export default function SymbolForecastPositions({
  symbol, snapshot, row, calibration,
}: {
  symbol: string;
  snapshot?: string;
  row: ForecastRow | null;
  calibration: { hitRate: Partial<Record<Horizon, HitRateEntry>>; windowLabel: string };
}) {
  const query = useQuery(symbolDetailQueryOptions(symbol, "options", snapshot));
  const { groups, unavailable } = forecastOptionPositions(query.data?.positions ?? []);
  return (
    <>
      <ProjectionFanChart row={row} calibration={calibration} positions={groups} />
      {row && groups.length > 0 && (
        <p className="mt-1 flex flex-wrap gap-x-3 text-xs text-text-muted">
          <span className="text-accent-purple">● Sold calls</span>
          <span className="text-accent-orange">● Sold puts</span>
          <span title="DTE is calendar days until expiration using the America/New_York date, displayed at DTE / 5 weeks.">
            Hover options for details · 5 DTE = 1w · &gt; beyond 20 DTE · Number = coincident options
          </span>
        </p>
      )}
      {query.isPending && <p role="status" className="text-xs text-text-muted">Loading open options…</p>}
      {query.error && (
        <p role="alert" className="text-xs text-accent-orange">
          Could not load open options. Use the Options tab to retry.
        </p>
      )}
      {unavailable > 0 && (
        <p className="text-xs text-accent-orange">{unavailable} open option(s) missing a valid strike or expiration.</p>
      )}
    </>
  );
}
