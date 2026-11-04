import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";
import { decodeSymbolParam } from "@/lib/symbolEncoding";

/**
 * BFF POST proxy for targeted Best Options refresh: browser → this Next route →
 * internal Python API. Triggers a single-symbol chain fetch + Best Options
 * recompute, updating that symbol's shared precomputed entry atomically.
 * Preserves 202 (started), 409 (already running), and upstream failures so the
 * client can await completion via GET. The refresh runs in the background.
 */
export async function POST(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol: _rawSym } = await params;
  const symbol = decodeSymbolParam(_rawSym);
  try {
    const res = await fetch(
      `${API_BASE_URL}/api/symbols/${encodeURIComponent(symbol)}/best-options/refresh`,
      { method: "POST", cache: "no-store", headers: { Accept: "application/json" }, signal: req.signal },
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
