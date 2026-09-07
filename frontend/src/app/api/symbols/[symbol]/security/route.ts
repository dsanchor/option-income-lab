import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";
import { decodeSymbolParam } from "@/lib/symbolEncoding";

/**
 * BFF proxy: edit security metadata with optimistic concurrency.
 * Mirrors PATCH /api/symbols/{symbol}/security (backend assignment: Livingston).
 * Forwards upstream status verbatim so 409/422 conflict/validation errors
 * reach the client for inline UX handling.
 */
export async function PATCH(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol: _rawSym } = await params;
  const symbol = decodeSymbolParam(_rawSym);
  try {
    const body = await req.text();
    const res = await fetch(
      `${API_BASE_URL}/api/symbols/${encodeURIComponent(symbol)}/security`,
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
