import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";

/** BFF proxy: run all monitoring agents now. Mirrors POST /api/trigger-all. */
export async function POST() {
  try {
    const res = await fetch(`${API_BASE_URL}/api/trigger-all`, {
      method: "POST",
      headers: { Accept: "application/json" },
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
