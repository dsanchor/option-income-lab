import { NextResponse } from "next/server";
import { apiFetch, API_BASE_URL } from "@/lib/api";

export async function GET() {
  try {
    return NextResponse.json(
      await apiFetch<unknown>("/api/settings/ai-providers"),
    );
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}

export async function POST(req: Request) {
  try {
    const res = await fetch(`${API_BASE_URL}/api/settings/ai-providers`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: await req.text(),
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
