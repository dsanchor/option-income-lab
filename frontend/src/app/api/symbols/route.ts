import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";

/**
 * BFF proxy: browser → this Next route → internal Python API.
 * Mirrors the backend's GET /api/symbols endpoint.
 */
export async function GET() {
  try {
    const data = await apiFetch<unknown>("/api/symbols");
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
