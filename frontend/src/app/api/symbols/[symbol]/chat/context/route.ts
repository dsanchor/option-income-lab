import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";

/**
 * BFF proxy: pre-fetch heavy chat context (Cosmos + market data) for a symbol.
 * Mirrors POST /api/symbols/{symbol}/chat/context.
 */
export async function POST(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;
  try {
    const body = await req.json().catch(() => ({}));
    const data = await apiFetch<unknown>(
      `/api/symbols/${encodeURIComponent(symbol)}/chat/context`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body ?? {}),
      },
    );
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
