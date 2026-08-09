import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";

/** BFF proxy: close a position. Mirrors PUT /api/symbols/{symbol}/positions/{positionId}/close. */
export async function PUT(
  req: Request,
  { params }: { params: Promise<{ symbol: string; positionId: string }> },
) {
  const { symbol, positionId } = await params;
  try {
    const body = await req.text();
    const res = await fetch(
      `${API_BASE_URL}/api/symbols/${encodeURIComponent(symbol)}/positions/${encodeURIComponent(positionId)}/close`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body,
      },
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
