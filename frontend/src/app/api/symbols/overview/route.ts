import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";
import type { SymbolsOverview } from "@/types/symbols";

/**
 * BFF proxy: browser → this Next route → internal Python API.
 * Mirrors the backend's GET /api/symbols/overview endpoint (lightweight rows
 * used by the TopNav symbol search autocomplete and the Symbols list page).
 */
export async function GET() {
  try {
    const data = await apiFetch<SymbolsOverview>("/api/symbols/overview");
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
