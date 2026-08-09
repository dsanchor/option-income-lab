import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";

/** BFF proxy: full symbol detail. Mirrors GET /api/symbols/{symbol}/detail. */
export async function GET(
  _req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;
  try {
    const data = await apiFetch<unknown>(`/api/symbols/${encodeURIComponent(symbol)}/detail`);
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
