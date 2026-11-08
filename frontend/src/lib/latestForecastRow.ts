import type { ForecastRow } from "@/types/forecasts";

/** Pick the most recent forecast row (max created_date). */
export function latestRow(rows: ForecastRow[]): ForecastRow | null {
  if (!rows?.length) return null;
  return rows.reduce((best, r) =>
    (r.created_date ?? "") > (best.created_date ?? "") ? r : best,
  rows[0]);
}
