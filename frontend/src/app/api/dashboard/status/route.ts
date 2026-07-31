import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";

/** BFF proxy: lightweight dashboard change-signature.
 *  Mirrors GET /api/dashboard/status. Polled by the client to decide when
 *  to trigger a full dashboard refresh. */
export async function GET() {
  try {
    const res = await fetch(`${API_BASE_URL}/api/dashboard/status`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
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
