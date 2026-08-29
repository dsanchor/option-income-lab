import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";

/**
 * BFF proxy for the Options Screener aggregation: browser -> this Next
 * route -> internal Python API. Mirrors GET /api/screener/options exactly
 * -- forwards every query param (side, symbols, preferences,
 * min_annualized_return_pct, min_abs_delta/max_abs_delta, dte_min/dte_max,
 * min_open_interest, sort/dir, offset/limit) and the upstream status code
 * verbatim, matching the existing `/api/symbols/[symbol]/best-options`
 * route's convention: a 400 validation error, a 500 evaluator error, and a
 * 200 result (which itself may report per-symbol warming/cold statuses) are
 * distinct states the client needs to tell apart, so this must not collapse
 * them into one generic thrown-on-!ok error.
 */
export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const qs = searchParams.toString();
  try {
    const res = await fetch(
      `${API_BASE_URL}/api/screener/options${qs ? `?${qs}` : ""}`,
      { headers: { Accept: "application/json" }, cache: "no-store" },
    );
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
