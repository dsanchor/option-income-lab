# Session Log: RKT Consolidation Analysis

**Date:** 2026-09-08  
**Time:** 11:29 UTC  
**Session ID:** 982fe3ee-631f-4684-a682-b5dc4ee47185  
**Agent:** Danny (Lead), Scribe (orchestration)

---

## Overview

Danny completed a comprehensive architecture analysis of the RKT (Reckitt Benckiser) 24/25 share consolidation accounting model. The analysis identified a material error in the current FIFO cost-basis treatment and proposed a corrected three-leg corporate-action structure.

---

## Key Finding

**Current state:** Ledger models the 72→69.12 consolidation as a SELL of 3 shares for EUR 8.58.

**Problem:** Under FIFO, this consumes the full acquisition cost of the 3 oldest lots, inflating realized P&L by the difference between 3 full old lots and 0.12 fractional new shares. The remaining 69 shares carry incorrect cost basis (total_cost minus cost_of_3_oldest_lots, not (69/69.12) x total_cost).

**Root cause:** No existing movement type can reduce share quantity while preserving aggregate FIFO cost.

---

## Proposed Solution

Introduce SHARE_CONSOLIDATION as a corporate-action group with three legs in strict sequence:

| Leg | Type | Qty | Purpose |
|---|---|---|---|
| 1 | CONSOLIDATION_OUT | 72 | Remove old shares; consume all FIFO lots; no P&L impact |
| 2 | CONSOLIDATION_IN | 69.12 | Recreate shares at adjusted unit cost preserving total cost |
| 3 | FRACTIONAL_CASH_OUT | 0.12 | Normal FIFO sale of fractional shares for EUR 8.58 |

### Why Holdings Service Handles This Without Changes

- **TRANSFER_OUT:** Calls `_consume_lots()` (depletes FIFO), does NOT increment cost_basis_sold_eur or sale_proceeds. Consolidation removal is P&L-neutral.
- **TRANSFER_IN:** Creates one new lot at `transfer_cost_basis_eur / qty` unit cost, does NOT increment purchase_outflow. No phantom investment.
- **SELL (ACCIONES):** Normal FIFO consumption and P&L booking. Fractional cost correctly reflects post-consolidation unit cost.

---

## Implementation Scope

**Minimal model extensions required:**
1. Add three enum values: CaLegType (CONSOLIDATION_OUT, CONSOLIDATION_IN, FRACTIONAL_CASH_OUT) and CaEventType (SHARE_CONSOLIDATION)
2. Add two mappings in cosmos_portfolio.py: leg-type-to-txn-type and required-legs-per-group
3. Wire transfer_cost_basis_eur and sales_type fields in CA leg-building loop

**No changes to holdings_service.py** — existing TRANSFER_OUT/TRANSFER_IN/SELL mechanics are already correct.

---

## Operator Prerequisite

Before implementation:
1. Void or delete the existing erroneous SELL of 3 shares x EUR 8.58
2. Capture remaining_cost_basis_eur for RKT from holdings page **after the void**
3. Once model is extended, create SHARE_CONSOLIDATION group with transfer_cost_basis_eur = captured value
4. Verify holdings show 69 shares with cost ≈ original_total × 0.998 (proportional fractional reduction)

---

## Decision Status

**PROPOSED** — awaiting user approval before implementation begins.

See: `.squad/decisions/inbox/danny-rkt-consolidation-model.md`

---

## Notes

- Dividend (separate DIVIDEND movement) explicitly out of scope; already recorded correctly.
- Fractional cash amount (EUR 8.58) verified as accurate per broker statement.
- Exact consolidation effective date to be confirmed (needed for FIFO lot ordering).
- No blocking dependencies on other FIFO or consolidation work in progress.

