import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";

/** BFF proxy: get one plan. Mirrors GET /api/symbols/{symbol}/plans/{planId}. */
export async function GET(
  _req: Request,
  { params }: { params: Promise<{ symbol: string; planId: string }> },
) {
  const { symbol, planId } = await params;
  try {
    const res = await fetch(
      `${API_BASE_URL}/api/symbols/${encodeURIComponent(symbol)}/plans/${encodeURIComponent(planId)}`,
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

/** BFF proxy: update a plan. Mirrors PUT /api/symbols/{symbol}/plans/{planId}. */
export async function PUT(
  req: Request,
  { params }: { params: Promise<{ symbol: string; planId: string }> },
) {
  const { symbol, planId } = await params;
  try {
    const body = await req.text();
    const res = await fetch(
      `${API_BASE_URL}/api/symbols/${encodeURIComponent(symbol)}/plans/${encodeURIComponent(planId)}`,
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
