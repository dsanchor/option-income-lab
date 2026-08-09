import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";

/**
 * BFF proxy: agent-chain pipeline viewer. Mirrors
 * GET /api/debug/agent-chain/{symbol} and forwards all query params
 * (option_type, strike, expiration, roll_type).
 */
export async function GET(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;
  const qs = new URL(req.url).search;
  try {
    const res = await fetch(
      `${API_BASE_URL}/api/debug/agent-chain/${encodeURIComponent(symbol)}${qs}`,
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
