import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";

/**
 * BFF POST proxy for targeted Best Options refresh: browser → this Next route →
 * internal Python API. Triggers a single-symbol chain fetch + Best Options
 * recompute, updating that symbol's shared precomputed entry atomically.
 * Returns immediately; the refresh runs in the background.
 */
export async function POST(
  _req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;
  try {
    const data = await apiFetch<unknown>(
      `/api/symbols/${encodeURIComponent(symbol)}/best-options/refresh`,
      { method: "POST" },
    );
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
