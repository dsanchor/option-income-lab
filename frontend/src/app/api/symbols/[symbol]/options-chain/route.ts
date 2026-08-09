import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";

/**
 * BFF proxy for the live options chain: browser → this Next route → internal
 * Python API. Mirrors GET /api/symbols/{symbol}/options-chain. The upstream does
 * a live yfinance fetch, so this can be slow (allow a generous timeout).
 */
export async function GET(
  _req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;
  try {
    const data = await apiFetch<unknown>(
      `/api/symbols/${encodeURIComponent(symbol)}/options-chain`,
    );
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
