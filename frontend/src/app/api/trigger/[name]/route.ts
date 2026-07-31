import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";

/**
 * BFF proxy: run a named scheduler task now. Mirrors POST /api/trigger/{name}
 * (summary_agent, banner_agent, options_chain, dgi_screener,
 * portfolio_enrichment, price_forecast, plan_monitor).
 */
export async function POST(
  _req: Request,
  { params }: { params: Promise<{ name: string }> },
) {
  const { name } = await params;
  try {
    const res = await fetch(
      `${API_BASE_URL}/api/trigger/${encodeURIComponent(name)}`,
      { method: "POST", headers: { Accept: "application/json" } },
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
