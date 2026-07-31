import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";

/** BFF proxy for the agent traces list. Mirrors GET /api/agent-traces. */
export async function GET() {
  try {
    const data = await apiFetch<unknown>("/api/agent-traces");
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
