import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";
import { decodeSymbolParam } from "@/lib/symbolEncoding";

/** BFF proxy: option positions eligible to link to a given movement txn_type.
 *  Mirrors GET /api/symbols/{symbol}/positions/linkable?txn_type=CALL_SELL. */
export async function GET(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol: _rawSym } = await params;
  const symbol = decodeSymbolParam(_rawSym);
  const { searchParams } = new URL(req.url);
  const qs = searchParams.toString();
  try {
    const res = await fetch(
      `${API_BASE_URL}/api/symbols/${encodeURIComponent(symbol)}/positions/linkable${qs ? `?${qs}` : ""}`,
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
