import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";
import type { SymbolsOverview } from "@/types/symbols";

/**
 * BFF proxy: browser → this Next route → internal Python API.
 * Mirrors the backend's GET /api/symbols/overview endpoint (lightweight rows
 * used by the TopNav symbol search autocomplete and the Symbols list page).
 *
 * Forwards the incoming query string as-is. Rev 6: the backend always
 * returns every symbol (portfolio = has an active-holdings entry;
 * watchlist = no holdings entry, e.g. option-only symbols) — nothing is
 * ever hidden by the API, so no special query param is needed.
 */
export async function GET(request: Request) {
  try {
    const { search } = new URL(request.url);
    const data = await apiFetch<SymbolsOverview>(`/api/symbols/overview${search}`);
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
