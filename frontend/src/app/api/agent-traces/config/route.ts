import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";

/** BFF proxy: save trace-capture toggles. Mirrors POST /api/agent-traces/config. */
export async function POST(req: Request) {
  try {
    const body = await req.text();
    const res = await fetch(`${API_BASE_URL}/api/agent-traces/config`, {
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
