import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";

/**
 * BFF proxy: create a plan. Mirrors POST /api/symbols/{symbol}/plans.
 * Forwards the upstream status code so validation errors (400) reach the client.
 */
export async function POST(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;
  try {
    const body = await req.text();
    const res = await fetch(
      `${API_BASE_URL}/api/symbols/${encodeURIComponent(symbol)}/plans`,
      {
        method: "POST",
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
