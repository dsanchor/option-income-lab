import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";

/** BFF proxy for calendar events. Mirrors GET /api/calendar. */
export async function GET() {
  try {
    const data = await apiFetch<unknown>("/api/calendar");
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { events: [], error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
