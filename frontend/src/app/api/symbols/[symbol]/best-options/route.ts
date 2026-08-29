import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";

/**
 * BFF proxy for the deterministic Best Options screen: browser → this Next route →
 * internal Python API. Mirrors GET /api/symbols/{symbol}/best-options. Forwards
 * query params (`side`, `dte_min`, `dte_max`, `support_level`) and the upstream
 * status code verbatim — a 200 "warming" body, a 4xx validation error, and a 200
 * "ok" result are all distinct states the client needs to tell apart, so this must
 * not collapse them into a single generic error the way a thrown-on-!ok helper would.
 */
export async function GET(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;
  const { searchParams } = new URL(req.url);
  const qs = searchParams.toString();
  try {
    const res = await fetch(
      `${API_BASE_URL}/api/symbols/${encodeURIComponent(symbol)}/best-options${qs ? `?${qs}` : ""}`,
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
