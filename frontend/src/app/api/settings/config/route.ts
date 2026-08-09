import { NextResponse } from "next/server";
import { apiFetch, API_BASE_URL } from "@/lib/api";

/** BFF proxy: settings config context. Mirrors GET /api/settings/config. */
export async function GET() {
  try {
    const data = await apiFetch<unknown>("/api/settings/config");
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}

/** BFF proxy: save settings config. Mirrors POST /api/settings/config. */
export async function POST(req: Request) {
  try {
    const body = await req.text();
    const res = await fetch(`${API_BASE_URL}/api/settings/config`, {
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
