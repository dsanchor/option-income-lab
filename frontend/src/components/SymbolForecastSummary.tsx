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
          <span className="cursor-help" title="Price anchor when this forecast was created, not the live price. The fan bands stay centered here.">
            Price at creation:{" "}
            <strong className="text-text">
              {typeof row.price_at_creation === "number" && Number.isFinite(row.price_at_creation)
                ? `$${row.price_at_creation.toFixed(2)}` : "—"}
            </strong>
          </span>
          <span className="cursor-help" title="Annualized volatility actually used for the fan bands, including any applied calibration scaling. Despite the HV label, it may come from IV, historical volatility or EWMA; see Vol source.">
            HV:{" "}
            <strong className="text-text">
              {typeof row.hv === "number" && Number.isFinite(row.hv)
                ? `${(row.hv * 100).toFixed(1)}%` : "—"}
            </strong>
          </span>
          <span className="cursor-help" title="Volatility estimator used: iv = options-implied volatility; hv = historical volatility; ewma = exponentially weighted historical volatility, giving recent moves more weight.">
            Vol source: <strong className="text-text">{row.vol_source || "—"}</strong>
          </span>
          <span className="cursor-help" title="Signed directional signal from -1 to +1: positive is bullish, negative bearish; larger absolute values mean a stronger signal. Not a probability or target return; it does not shift the band center.">
            Bias:{" "}
            <strong className="text-text">
              {typeof row.bias === "number" && Number.isFinite(row.bias)
                ? row.bias.toFixed(2) : "—"}
            </strong>
          </span>
          {slope != null && (
            <span className="cursor-help" title="Headline robust (Theil-Sen) trend in dollars per trading session: positive slopes up, negative down, shrunk toward zero by fit quality R². Used for the trend overlay, not the band center; longer horizons may use their own fit.">
              Trend:{" "}
              <strong className="text-text">
                {slope >= 0 ? "+" : ""}{slope.toFixed(2)} $/session
              </strong>
              {r2 != null && (
                <>
                  {" · "}
                  <span className="cursor-help" title="Historical trend fit quality from 0 to 1: higher means the fitted line explains more of the past price variation. Used to shrink the trend slope, not a measure of future forecast accuracy.">
                    R² {r2.toFixed(2)}
                  </span>
                </>
              )}
            </span>
          )}
          {row.flags?.earnings_in_window === true && (
            <span className="cursor-help" title="At forecast creation, a known earnings date fell after creation and within the next 30 calendar days (not the 90-day history range). Earnings can cause price gaps.">
              📅 earnings in window
            </span>
          )}
          {row.flags?.exdiv_in_window === true && (
            <span className="cursor-help" title="At forecast creation, a known ex-dividend date fell after creation and within the next 30 calendar days (not the 90-day history range). The price may adjust as the stock trades without dividend entitlement.">
              💰 ex-div in window
            </span>
          )}
        </div>
      )}
      <ProjectionFanChart
        row={row}
        calibration={{ hitRate: data.hit_rate ?? {}, windowLabel: "90d" }}
      />
    </Link>
  );
}
