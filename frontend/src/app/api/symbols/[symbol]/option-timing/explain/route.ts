import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";
import { decodeSymbolParam } from "@/lib/symbolEncoding";

export async function POST(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol: rawSymbol } = await params;
  const symbol = decodeSymbolParam(rawSymbol);
  try {
    const res = await fetch(
      `${API_BASE_URL}/api/symbols/${encodeURIComponent(symbol)}/option-timing/explain`,
      {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: await req.text(),
        signal: req.signal,
        cache: "no-store",
      },
    );
    const body = await res.text();
    return new Response([204, 205, 304].includes(res.status) ? null : body, {
      status: res.status,
      headers: {
        "Content-Type": res.headers.get("Content-Type") ?? "application/json",
        "Cache-Control": "no-store",
      },
    });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502, headers: { "Cache-Control": "no-store" } },
    );
  }
}
