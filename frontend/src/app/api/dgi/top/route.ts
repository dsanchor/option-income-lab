import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";

/** BFF proxy for the DGI top list. Mirrors GET /api/dgi/top. */
export async function GET() {
  try {
    const data = await apiFetch<unknown>("/api/dgi/top");
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
