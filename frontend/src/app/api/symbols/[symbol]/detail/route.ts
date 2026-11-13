import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";
import { decodeSymbolParam } from "@/lib/symbolEncoding";

/** Proxy legacy/full and scoped detail reads, preserving status and cancellation. */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol: _rawSym } = await params;
  const symbol = decodeSymbolParam(_rawSym);
  try {
    const { search } = new URL(request.url);
    const response = await fetch(`${API_BASE_URL}/api/symbols/${encodeURIComponent(symbol)}/detail${search}`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
      signal: request.signal,
    });
    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
