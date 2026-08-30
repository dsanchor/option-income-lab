import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";
import type { ValidateContractRequest, ValidateContractResponse } from "@/types/contract-validation";

/**
 * BFF POST proxy for contract validation: browser → this Next route → internal
 * Python API. Triggers exact-contract validation (refresh chain, locate contract,
 * run agent). Returns immediately with run_id for status polling.
 *
 * Backend: POST /api/best-options/validate (backend/web/app.py@3568)
 */
export async function POST(req: Request) {
  try {
    const body: ValidateContractRequest = await req.json();
    
    const data = await apiFetch<ValidateContractResponse>(
      `/api/best-options/validate`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    
    // Map backend status codes to frontend responses
    if (data.status === "accepted") {
      return NextResponse.json(data, { status: 202 });
    } else if (data.status === "duplicate") {
      return NextResponse.json(data, { status: 409 });
    } else if (data.status === "max_concurrency") {
      return NextResponse.json(data, { status: 429 });
    } else {
      return NextResponse.json(data, { status: 400 });
    }
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
