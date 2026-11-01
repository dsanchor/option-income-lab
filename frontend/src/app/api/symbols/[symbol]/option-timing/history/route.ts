import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";
import { decodeSymbolParam } from "@/lib/symbolEncoding";

export async function GET(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol: rawSymbol } = await params;
  const symbol = decodeSymbolParam(rawSymbol);
  const range = new URL(req.url).searchParams.get("range") ?? "90d";
  try {
    const res = await fetch(
      `${API_BASE_URL}/api/symbols/${encodeURIComponent(symbol)}/option-timing/history?range=${encodeURIComponent(range)}`,
      { headers: { Accept: "application/json" }, signal: req.signal, cache: "no-store" },
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
