import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";

/** BFF proxy: pause watchlist until earnings. Mirrors POST /api/symbols/{symbol}/pause. */
export async function POST(
  _req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  return proxy(await params, "POST");
}

/** BFF proxy: resume watchlist. Mirrors DELETE /api/symbols/{symbol}/pause. */
export async function DELETE(
  _req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  return proxy(await params, "DELETE");
}

async function proxy({ symbol }: { symbol: string }, method: "POST" | "DELETE") {
  try {
    const res = await fetch(
      `${API_BASE_URL}/api/symbols/${encodeURIComponent(symbol)}/pause`,
      {
        method,
        headers: { "Content-Type": "application/json", Accept: "application/json" },
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
