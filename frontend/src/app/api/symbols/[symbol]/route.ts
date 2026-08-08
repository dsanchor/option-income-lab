import { NextResponse } from "next/server";
import { API_BASE_URL, apiFetch } from "@/lib/api";

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
    const body = await req.text();
    const res = await fetch(`${API_BASE_URL}/api/symbols/${encodeURIComponent(symbol)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body,
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
