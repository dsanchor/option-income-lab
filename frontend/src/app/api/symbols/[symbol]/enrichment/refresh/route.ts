import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";
import { decodeSymbolParam } from "@/lib/symbolEncoding";

/**
 * BFF proxy: trigger synchronous enrichment refresh.
 * Mirrors POST /api/symbols/{symbol}/enrichment/refresh (backend: Livingston).
 * Always returns 200 — even enrichment fetch failures come back as
 * {status:"error", detail:"..."} per the backend contract (§enrichment).
 */
export async function POST(
  _req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol: _rawSym } = await params;
  const symbol = decodeSymbolParam(_rawSym);
  try {
    const res = await fetch(
      `${API_BASE_URL}/api/symbols/${encodeURIComponent(symbol)}/enrichment/refresh`,
      {
        method: "POST",
        headers: { Accept: "application/json" },
      },
    );
    const data = await res.json().catch(() => ({ status: "error", detail: "Malformed response" }));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { status: "error", detail: err instanceof Error ? err.message : "Network error" },
      { status: 502 },
    );
  }
}
