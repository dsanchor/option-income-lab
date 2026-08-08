import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";

/** BFF proxy: update a position's premium. Mirrors PATCH /api/symbols/{symbol}/positions/{positionId}/premium. */
export async function PATCH(
  req: Request,
  { params }: { params: Promise<{ symbol: string; positionId: string }> },
) {
  const { symbol, positionId } = await params;
  try {
    const body = await req.text();
    const res = await fetch(
      `${API_BASE_URL}/api/symbols/${encodeURIComponent(symbol)}/positions/${encodeURIComponent(positionId)}/premium`,
      {
        method: "PATCH",
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