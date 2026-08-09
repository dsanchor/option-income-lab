import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";

/**
 * BFF proxy: fetch live market data for an untracked symbol (no DB write).
 * Mirrors POST /api/chat/fetch-symbol. Forwards the upstream status so
 * validation errors (400) and provider errors (500/503) reach the client.
 */
export async function POST(req: Request) {
  try {
    const body = await req.text();
    const res = await fetch(`${API_BASE_URL}/api/chat/fetch-symbol`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body,
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
