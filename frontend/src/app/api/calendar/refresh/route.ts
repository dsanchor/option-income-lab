import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";

/**
 * BFF proxy for calendar refresh. Mirrors POST /api/calendar/refresh — the
 * upstream fetches fresh earnings/ex-dividend dates from yfinance (slow).
 */
export async function POST() {
  try {
    const data = await apiFetch<unknown>("/api/calendar/refresh", {
      method: "POST",
    });
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
