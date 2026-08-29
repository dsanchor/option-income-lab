import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";
import type { ValidationStatusResponse } from "@/types/contract-validation";

/**
 * BFF GET proxy for contract validation status polling: browser → this Next route
 * → internal Python API. Returns in_progress, completed, or not_found.
 *
 * Backend: GET /api/best-options/validate/{run_id} (backend/web/app.py@3643)
 */
export async function GET(
  _req: Request,
  { params }: { params: Promise<{ run_id: string }> },
) {
  const { run_id } = await params;
  
  try {
    const data = await apiFetch<ValidationStatusResponse>(
      `/api/best-options/validate/${encodeURIComponent(run_id)}`,
      { method: "GET" },
    );
    
    if (data.status === "not_found") {
      return NextResponse.json(data, { status: 404 });
    }
    
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
