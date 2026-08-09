import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";

/**
 * BFF proxy: DGI single-symbol analysis. Mirrors GET /api/dgi/analyze/{symbol}
 * (live yfinance fetch + scoring, ~2-5s). Forwards the upstream status so the
 * page can distinguish invalid-symbol (400) from analysis failures (500).
 */
export async function GET(
  _req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;
  try {
    const res = await fetch(
      `${API_BASE_URL}/api/dgi/analyze/${encodeURIComponent(symbol)}`,
      { headers: { Accept: "application/json" } },
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
