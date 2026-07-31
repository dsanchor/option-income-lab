import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";

/** BFF proxy: single activity detail. Mirrors GET /api/activities/{activityId}. */
export async function GET(
  _req: Request,
  { params }: { params: Promise<{ activityId: string }> },
) {
  const { activityId } = await params;
  try {
    const res = await fetch(
      `${API_BASE_URL}/api/activities/${encodeURIComponent(activityId)}`,
      { headers: { Accept: "application/json" } },
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

/** BFF proxy: delete an activity. Mirrors DELETE /api/activities/{activityId}. */
export async function DELETE(
  _req: Request,
  { params }: { params: Promise<{ activityId: string }> },
) {
  const { activityId } = await params;
  try {
    const res = await fetch(
      `${API_BASE_URL}/api/activities/${encodeURIComponent(activityId)}`,
      { method: "DELETE", headers: { Accept: "application/json" } },
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
