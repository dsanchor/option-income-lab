import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";

/** BFF proxy: clear provider/options caches. Mirrors POST /api/debug/clear-cache. */
export async function POST() {
  try {
    const res = await fetch(`${API_BASE_URL}/api/debug/clear-cache`, {
      method: "POST",
      headers: { Accept: "application/json" },
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { success: false, error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
