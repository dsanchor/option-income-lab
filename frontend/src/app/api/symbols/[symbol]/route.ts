import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";

/**
 * BFF proxy for a single symbol: browser → this Next route → internal Python API.
 * Mirrors the backend's GET/PUT /api/symbols/{symbol} endpoints.
 */
export async function GET(
  _req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;
  try {
    const data = await apiFetch<unknown>(`/api/symbols/${encodeURIComponent(symbol)}`);
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}

export async function PUT(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;
  try {
    const body = await req.json();
    const data = await apiFetch<unknown>(`/api/symbols/${encodeURIComponent(symbol)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
