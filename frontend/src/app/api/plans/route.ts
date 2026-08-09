import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";

/** BFF proxy for the plans list. Mirrors GET /api/plans (status, symbol). */
export async function GET(req: Request) {
  const incoming = new URL(req.url).searchParams;
  const forwarded = new URLSearchParams();
  for (const key of ["status", "symbol"]) {
    const value = incoming.get(key);
    if (value) forwarded.set(key, value);
  }
  const qs = forwarded.toString();
  try {
    const data = await apiFetch<unknown>(`/api/plans${qs ? `?${qs}` : ""}`);
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
