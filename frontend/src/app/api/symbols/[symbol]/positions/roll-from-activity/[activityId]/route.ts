import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";

/** BFF proxy: roll a position from an alert activity.
 *  Mirrors POST /api/symbols/{symbol}/positions/roll-from-activity/{activityId}. */
export async function POST(
  _req: Request,
  { params }: { params: Promise<{ symbol: string; activityId: string }> },
) {
  const { symbol, activityId } = await params;
  try {
    const res = await fetch(
      `${API_BASE_URL}/api/symbols/${encodeURIComponent(symbol)}/positions/roll-from-activity/${encodeURIComponent(activityId)}`,
      { method: "POST", headers: { Accept: "application/json" } },
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
