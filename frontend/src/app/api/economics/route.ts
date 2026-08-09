import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";

/**
 * BFF proxy for economics analytics: browser → this Next route → internal
 * Python API. Mirrors GET /api/economics and forwards all supported query
 * params (year, month, symbol, type, status).
 */
export async function GET(req: Request) {
  const incoming = new URL(req.url).searchParams;
  const forwarded = new URLSearchParams();
  for (const key of ["year", "month", "symbol", "type", "status"]) {
    const value = incoming.get(key);
    if (value) forwarded.set(key, value);
  }
  const qs = forwarded.toString();
  try {
    const data = await apiFetch<unknown>(
      `/api/economics${qs ? `?${qs}` : ""}`,
    );
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
