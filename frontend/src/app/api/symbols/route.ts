import { NextResponse } from "next/server";
import { API_BASE_URL, apiFetch } from "@/lib/api";

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

/**
 * BFF proxy: create a symbol. Mirrors POST /api/symbols. Forwards the upstream
 * status so a 409 (already exists) reaches the client, which then falls back to
 * a PUT to enable the requested tracking flag.
 */
export async function POST(req: Request) {
  try {
    const body = await req.text();
    const res = await fetch(`${API_BASE_URL}/api/symbols`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body,
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
