import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";

/**
 * BFF proxy for the LLM technical-analysis generator: browser → this Next route
 * → internal Python API. Mirrors POST /api/symbols/{symbol}/technical-analysis
 * (slow, LLM-driven).
 */
export async function POST(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;
  try {
    const body = await req.json().catch(() => ({}));
    const data = await apiFetch<unknown>(
      `/api/symbols/${encodeURIComponent(symbol)}/technical-analysis`,
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
