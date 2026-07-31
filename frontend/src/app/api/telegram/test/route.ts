import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";

/** BFF proxy: send a Telegram test message. Mirrors POST /api/telegram/test. */
export async function POST() {
  try {
    const res = await fetch(`${API_BASE_URL}/api/telegram/test`, {
      method: "POST",
      headers: { Accept: "application/json" },
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { ok: false, error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
