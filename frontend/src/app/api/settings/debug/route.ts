import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";

/** BFF proxy: debug diagnostics. Mirrors GET /api/settings/debug. */
export async function GET() {
  try {
    const data = await apiFetch<unknown>("/api/settings/debug");
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
