import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";
import { decodeSymbolParam } from "@/lib/symbolEncoding";

export async function POST(
  req: Request,
  { params }: { params: Promise<{ symbol: string; positionId: string }> },
) {
  const { symbol: rawSymbol, positionId } = await params;
  const symbol = decodeSymbolParam(rawSymbol);
  try {
    const body = await req.text();
    const res = await fetch(
      `${API_BASE_URL}/api/symbols/${encodeURIComponent(symbol)}/positions/${encodeURIComponent(positionId)}/roll-simulation`,
      {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
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
