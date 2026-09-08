/**
 * scripZeroCostContract.test.mjs — Frontend regression for scrip/zero-cost
 * and BUY import gross/net contract (danny-scrip-zero-cost-and-buy-import-contract.md).
 *
 * Requirements:
 *   TY  — CostBasisStatus TypeScript union includes "ZERO_COST" (§4.3).
 *   ZCA — WarningType no longer includes ZERO_COST_ACQUISITION; warning removed
 *          from WARNING_SHORT map (§1.3); stale UI entry is a defect.
 *   ICB — INCOMPLETE_COST_BASIS is the live warning type for genuinely unknown cost.
 *   INV — Per-row Invested cell uses remaining_cost_basis_eur ?? total_invested_eur
 *          (§4.1); NOT total_purchase_outflow_eur or a fabricated field.
 *   AVG — Avg Cost cell uses avg_cost_basis_eur only; no effective_avg_cost_eur field
 *          anywhere in types or source (§4, effective_avg withdrawn).
 *   GBL — Global holdings Invested stat uses remaining_cost_basis_eur (§4.2).
 *   WRN — Warning render path does not fabricate labels for unknown warning types
 *          (falls back to raw type string, not a crash).
 *
 * Run: node --test frontend/tests/scripZeroCostContract.test.mjs
 */

import { readFileSync } from "fs";
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const ROOT = join(__dirname, "../src");

function src(relPath) {
  return readFileSync(join(ROOT, relPath), "utf8");
}

// ---------------------------------------------------------------------------
// TY — CostBasisStatus type includes ZERO_COST
// ---------------------------------------------------------------------------

describe("TY — CostBasisStatus type", () => {
  const types = src("types/portfolio.ts");

  it("TY-1: CostBasisStatus includes ZERO_COST literal", () => {
    // Must include all three statuses (not just COMPLETE/INCOMPLETE)
    assert.match(
      types,
      /CostBasisStatus\s*=\s*[^;]*"ZERO_COST"/,
      "CostBasisStatus type must include \"ZERO_COST\""
    );
  });

  it("TY-2: CostBasisStatus includes COMPLETE and INCOMPLETE", () => {
    assert.match(types, /"COMPLETE"/, "CostBasisStatus must include \"COMPLETE\"");
    assert.match(types, /"INCOMPLETE"/, "CostBasisStatus must include \"INCOMPLETE\"");
  });

  it("TY-3: no effective_avg_cost_eur field in portfolio types (withdrawn field)", () => {
    assert.doesNotMatch(
      types,
      /effective_avg_cost_eur/,
      "effective_avg_cost_eur was withdrawn from contract — must not appear in types"
    );
  });
});

// ---------------------------------------------------------------------------
// ZCA — ZERO_COST_ACQUISITION warning entry should be removed (product defect)
// ---------------------------------------------------------------------------

describe("ZCA — ZERO_COST_ACQUISITION warning removal", () => {
  const holdingsTable = src("components/PortfolioHoldingsTable.tsx");
  const portfolioTypes = src("types/portfolio.ts");

  it("ZCA-1: [DEFECT] WARNING_SHORT must not contain ZERO_COST_ACQUISITION key", () => {
    // Backend no longer emits ZERO_COST_ACQUISITION; stale entry in WARNING_SHORT is dead code.
    // This test will FAIL until Linus removes the entry from the map.
    const hasStaleEntry = holdingsTable.includes("ZERO_COST_ACQUISITION");
    assert.equal(
      hasStaleEntry,
      false,
      "WARNING_SHORT still maps ZERO_COST_ACQUISITION — remove stale entry (§1.3)"
    );
  });

  it("ZCA-2: [DEFECT] WarningType union must not include ZERO_COST_ACQUISITION", () => {
    // Backend no longer emits this type; stale TypeScript union member should be removed.
    // This test will FAIL until Linus removes it from portfolio.ts.
    assert.doesNotMatch(
      portfolioTypes,
      /\|\s*"ZERO_COST_ACQUISITION"/,
      "WarningType still includes ZERO_COST_ACQUISITION — remove stale member (§4.2)"
    );
  });
});

// ---------------------------------------------------------------------------
// ICB — INCOMPLETE_COST_BASIS is the correct warning type
// ---------------------------------------------------------------------------

describe("ICB — INCOMPLETE_COST_BASIS warning support", () => {
  const holdingsTable = src("components/PortfolioHoldingsTable.tsx");

  it("ICB-1: WARNING_SHORT contains or warning render falls back for INCOMPLETE_COST_BASIS", () => {
    // Either the map explicitly handles INCOMPLETE_COST_BASIS, or the render
    // uses a fallback (w.type) so unknown keys degrade gracefully without crash.
    const hasExplicitEntry = holdingsTable.includes("INCOMPLETE_COST_BASIS");
    const hasFallback = holdingsTable.includes("?? w.type") ||
                        holdingsTable.includes("?? w.type") ||
                        /WARNING_SHORT\[.*\]\s*\?\?/.test(holdingsTable);
    assert.ok(
      hasExplicitEntry || hasFallback,
      "INCOMPLETE_COST_BASIS must either have an explicit WARNING_SHORT entry or be handled by fallback"
    );
  });
});

// ---------------------------------------------------------------------------
// INV — Per-row Invested uses remaining_cost_basis_eur
// ---------------------------------------------------------------------------

describe("INV — Per-row Invested cell source contract", () => {
  const holdingsTable = src("components/PortfolioHoldingsTable.tsx");

  it("INV-1: Invested cell uses remaining_cost_basis_eur as primary", () => {
    assert.match(
      holdingsTable,
      /remaining_cost_basis_eur/,
      "Invested cell must reference remaining_cost_basis_eur (§4.1)"
    );
  });

  it("INV-2: Invested cell uses total_invested_eur (not total_purchase_outflow_eur) as fallback", () => {
    // Per contract §4.1: remaining_cost_basis_eur ?? total_invested_eur
    // total_purchase_outflow_eur is the accumulator before sells and must not be displayed directly.
    const hasFallbackPattern =
      /remaining_cost_basis_eur\s*\?\?\s*\w*\.?total_invested_eur/.test(holdingsTable) ||
      holdingsTable.includes("remaining_cost_basis_eur ?? h.total_invested_eur") ||
      holdingsTable.includes("remaining_cost_basis_eur ?? summary.total_invested_eur");
    assert.ok(
      hasFallbackPattern,
      "Invested fallback must be total_invested_eur, not total_purchase_outflow_eur"
    );
  });

  it("INV-3: Invested cell does not use total_purchase_outflow_eur directly", () => {
    // total_purchase_outflow_eur is the technical outflow accumulator; never displayed as invested.
    assert.doesNotMatch(
      holdingsTable,
      /total_purchase_outflow_eur/,
      "Invested cell must not use total_purchase_outflow_eur directly in display"
    );
  });

  it("INV-4: no effective_avg_cost_eur referenced in PortfolioHoldingsTable", () => {
    assert.doesNotMatch(
      holdingsTable,
      /effective_avg_cost_eur/,
      "effective_avg_cost_eur was withdrawn — must not appear in PortfolioHoldingsTable"
    );
  });
});

// ---------------------------------------------------------------------------
// AVG — Avg Cost cell uses avg_cost_basis_eur only
// ---------------------------------------------------------------------------

describe("AVG — Avg Cost field contract", () => {
  const holdingsTable = src("components/PortfolioHoldingsTable.tsx");
  const portfolioTypes = src("types/portfolio.ts");

  it("AVG-1: Avg Cost cell references avg_cost_basis_eur", () => {
    assert.match(
      holdingsTable,
      /avg_cost_basis_eur/,
      "Avg Cost must reference avg_cost_basis_eur"
    );
  });

  it("AVG-2: no effective_avg_cost_eur in types (withdrawn)", () => {
    assert.doesNotMatch(
      portfolioTypes,
      /effective_avg_cost_eur/,
      "effective_avg_cost_eur was withdrawn from contract"
    );
  });

  it("AVG-3: HoldingEntry type does not have effective_avg_cost_eur", () => {
    assert.doesNotMatch(
      portfolioTypes,
      /effective_avg_cost_eur/,
      "effective_avg_cost_eur must not appear in portfolio.ts"
    );
  });
});

// ---------------------------------------------------------------------------
// GBL — Global holdings Invested stat
// ---------------------------------------------------------------------------

describe("GBL — Global holdings summary stat", () => {
  const holdingsTable = src("components/PortfolioHoldingsTable.tsx");

  it("GBL-1: summary Invested stat uses remaining_cost_basis_eur", () => {
    assert.match(
      holdingsTable,
      /summary\.remaining_cost_basis_eur/,
      "Summary Invested stat must use summary.remaining_cost_basis_eur (§4.2)"
    );
  });
});

// ---------------------------------------------------------------------------
// WRN — Warning render path degrades gracefully for unknown warning types
// ---------------------------------------------------------------------------

describe("WRN — Warning render fallback", () => {
  const holdingsTable = src("components/PortfolioHoldingsTable.tsx");

  it("WRN-1: warning render has fallback for unrecognised types", () => {
    // Pattern: WARNING_SHORT[w.type] ?? w.type  (or equivalent)
    const hasFallback =
      holdingsTable.includes("?? w.type") ||
      /WARNING_SHORT\[.*WarningType\)?\]\s*\?\?/.test(holdingsTable);
    assert.ok(
      hasFallback,
      "Warning render must use fallback (e.g. ?? w.type) to handle future/unknown types without crash"
    );
  });
});

// ---------------------------------------------------------------------------
// Migration scripts — source-contract audit (§3)
// ---------------------------------------------------------------------------

describe("MIG — Migration script source contract", () => {
  let repairSrc = null;
  try {
    repairSrc = readFileSync(
      join(__dirname, "../../backend/src/portfolio/scripts/repair_buy_ledger_fields.py"),
      "utf8"
    );
  } catch {
    // Fallback: check top-level scripts dir (Livingston may place it there)
    try {
      repairSrc = readFileSync(
        join(__dirname, "../../backend/scripts/repair_buy_ledger_fields.py"),
        "utf8"
      );
    } catch {
      repairSrc = null;
    }
  }

  it("MIG-1: repair script exists at expected path", () => {
    assert.notEqual(
      repairSrc,
      null,
      "repair_buy_ledger_fields.py must exist in backend scripts/ or src/portfolio/scripts/"
    );
  });

  if (repairSrc) {
    it("MIG-2: repair script supports dry-run or audit (read-only) mode", () => {
      // Contract §3 specifies dry-run; Livingston may implement as --audit (equivalent).
      const hasDryRun = /dry.?run/i.test(repairSrc);
      const hasAudit = /\-\-audit/.test(repairSrc);
      assert.ok(
        hasDryRun || hasAudit,
        "Repair script must support read-only mode (--dry-run or --audit equivalent)"
      );
    });

    it("MIG-3: repair script supports apply mode", () => {
      assert.match(
        repairSrc,
        /apply/i,
        "Repair script must support --mode apply for actual repair"
      );
    });

    it("MIG-4: repair script checks csv_import source before modifying", () => {
      assert.match(
        repairSrc,
        /csv_import/,
        "Repair script must filter by import_source=csv_import to avoid touching manual entries"
      );
    });

    it("MIG-5: repair script skips SUPERSEDED/VOIDED records", () => {
      const hasSuperseeded = /SUPERSEDED/.test(repairSrc);
      const hasVoided = /VOIDED/.test(repairSrc);
      assert.ok(
        hasSuperseeded || hasVoided,
        "Repair script must skip SUPERSEDED/VOIDED correction_status records"
      );
    });

    it("MIG-6: repair script is idempotent (skips already-corrected)", () => {
      const hasIdempotency =
        /correction_status/.test(repairSrc) ||
        /already.correct/i.test(repairSrc) ||
        /skip/i.test(repairSrc);
      assert.ok(
        hasIdempotency,
        "Repair script must be idempotent: skip records already marked correct"
      );
    });
  }
});

// ---------------------------------------------------------------------------
// FE — BUY form gross/net contract (danny-fifo-net-accounting-contract.md §1)
// Contract: FE-1 — BUY form sends gross = trade_value (NOT trade_value + fees).
//           Server derives net = gross + fees.
// ---------------------------------------------------------------------------

describe("FE — BUY form gross/net semantics", () => {
  const addMovement = src("components/AddMovementDialog.tsx");

  it("FE-1: BUY submit does not add fees into gross before sending", () => {
    // The old incorrect code added fees into gross before calling makeGross.
    // Pattern to reject: (parseFloat(buyForm.trade_value) ... + ... fees ...).toFixed
    // After the fix, gross must be makeGross(buyForm.trade_value, ...) — trade value only.
    const addsFeesToGross =
      /makeGross\s*\(\s*\(\s*\(parseFloat\(buyForm\.trade_value\)/.test(addMovement) ||
      /trade_value.*\+.*fees.*toFixed/.test(addMovement);
    assert.equal(
      addsFeesToGross,
      false,
      "BUY submit must NOT add fees into gross — send trade_value as gross; server computes net"
    );
  });

  it("FE-2: BUY submit sends gross = trade_value (bare field, no arithmetic)", () => {
    // After fix: gross: makeGross(buyForm.trade_value, currency)
    assert.match(
      addMovement,
      /makeGross\s*\(\s*buyForm\.trade_value\s*,\s*currency\s*\)/,
      "BUY gross must be makeGross(buyForm.trade_value, currency) — no fee addition"
    );
  });

  it("FE-3: BUY fees field is still forwarded separately", () => {
    // The fees field must still be sent in the request so the server can compute net.
    // Pattern: fees: buyForm.fees ? makeFeesInput(...)
    assert.match(
      addMovement,
      /fees.*buyForm\.fees.*makeFeesInput/s,
      "BUY fees must still be forwarded separately so the server can derive net = gross + fees"
    );
  });
});
