import { HORIZONS } from "@/types/forecasts";
import type { ForecastTrend, Horizon, HorizonSummary } from "@/types/forecasts";

/** Render the backend-authored session path; linear fallback reads legacy docs. */
export function projectForecastTrend(
  anchor: number,
  trend: ForecastTrend | null | undefined,
  horizons: Partial<Record<Horizon, HorizonSummary>> | undefined,
  session: number,
): number | null {
  const path = trend?.session_deltas;
  if (path && session >= 0 && session <= path.length - 1) {
    const lo = Math.floor(session), hi = Math.ceil(session);
    if (Number.isFinite(path[lo]) && Number.isFinite(path[hi])) {
      return anchor + path[lo] + (path[hi] - path[lo]) * (session - lo);
    }
  }
  if (trend?.model === "adaptive_damped_v1") return null;
  const band = HORIZONS.map((h) => horizons?.[h]).find((b) =>
    b?.start_session != null && b.end_session != null &&
    session >= b.start_session && session <= b.end_session,
  );
  const slope = band?.trend_slope ?? horizons?.["1d"]?.trend_slope ?? trend?.slope;
  return typeof slope === "number" && Number.isFinite(slope) ? anchor + slope * session : null;
}

export const ADAPTIVE_TREND_DESCRIPTION = "Adaptive recent/slow robust price trend, gated by causal path efficiency, with damped endpoints and per-horizon predictive shrinkage from matured non-overlapping past returns. First-session change is not constant drift; calibration is as of creation, not a probability or guaranteed accuracy. Momentum/bias and the price-centered volatility cone are separate.";

export function trendCalibrationLabel(trend: ForecastTrend): string {
  return HORIZONS.map((h) => {
    const c = trend.calibration?.[h];
    if (!c) return `${h}: unavailable`;
    return c.status === "available"
      ? `${h}: n=${c.n}, shrink ×${c.factor.toFixed(2)}`
      : `${h}: insufficient n=${c.n}/${c.min_n}, prior ×${c.factor.toFixed(2)}`;
  }).join(" · ");
}
