import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";

/** BFF proxy: position monitoring snapshots.
 *  Mirrors GET /api/symbols/{symbol}/positions/{positionId}/snapshots. */
export async function GET(
  req: Request,
  { params }: { params: Promise<{ symbol: string; positionId: string }> },
) {
  const { symbol, positionId } = await params;
  const { searchParams } = new URL(req.url);
  const qs = searchParams.toString();
  try {
    const res = await fetch(
      `${API_BASE_URL}/api/symbols/${encodeURIComponent(symbol)}/positions/${encodeURIComponent(positionId)}/snapshots${qs ? `?${qs}` : ""}`,
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
