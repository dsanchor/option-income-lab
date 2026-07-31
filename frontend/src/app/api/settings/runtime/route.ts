import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";

/** BFF proxy: runtime stats. Mirrors GET /api/settings/runtime. */
export async function GET() {
  try {
    const data = await apiFetch<unknown>("/api/settings/runtime");
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
