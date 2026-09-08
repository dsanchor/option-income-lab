# FIFO Holdings & CA Leg Ordering Fix Release — Orchestration Log

**Session Date:** 2026-09-08  
**Release:** fix: migrate holdings engine to FIFO and fix CA leg ordering  
**Commit:** f5ed79f05b2eb86ff7adf6c1f0ddb131f6d07488  
**Branch:** main  
**Status:** Released to main

---

## Executive Summary

Commit f5ed79f addresses the same-date corporate-action leg ordering bug discovered by Basher during the Share Consolidation release validation (66% randomized-test failure rate on RKT scenario). The fix includes:

1. **Holdings Engine Rewrite (CMP → FIFO):** Migrated `holdings_service.py` from chronological moving weighted average to First-In-First-Out (FIFO) lot depletion
2. **CA Leg Ordering Fix:** Corrected movement sort order from `(trade_date, id)` to `(trade_date, ca_group_id, ca_group_seq, id)`, ensuring corporate-action legs process in correct sequence regardless of UUID ordering
3. **BUY Gross/Net Semantics:** Aligned with prior "Scrip Zero-Cost & BUY Gross-Net Correction" release (89a7c2a); includes migration script `repair_buy_ledger_fields.py` for correcting existing records

**Review Verdict:** Livingston (Persistence & Integration Engineer) reviewed full diff read-only — **APPROVE WITH NOTES** (minor naming/comment drift only, no blocking defects)

**Validation:** Targeted backend suite (portfolio/fifo/holdings/scrip/consolidation/ledger_fields) — **1119 passed, 0 failed**

---

## Team Orchestration

### Livingston (Persistence & Integration Engineer) — Read-Only Diff Review

**Role:** Full-scope code review of holdings engine rewrite and CA leg ordering fix  
**Review Type:** Read-only (no modifications; review only)  
**Verdict:** **APPROVE WITH NOTES**

**Methodology:**
- Read full diff of `holdings_service.py` rewrite (225 lines modified)
- Verified FIFO lot-depletion logic: BUY COMPLETE at gross_eur, BUY ZERO_COST at 0 (natural dilution), SELL/TRANSFER_OUT consume oldest first
- Verified CA leg sort order fix: `(trade_date, ca_group_id, ca_group_seq, id)` ensures deterministic processing
- Reviewed supporting changes in `fx_service.py`, `import_service.py`, `parsers/purchases.py`
- Confirmed migration script `repair_buy_ledger_fields.py` and test coverage

**Findings:**
- No blocking defects
- No logic errors in lot-depletion sequencing
- No regressions in CA leg processing
- **Minor notes only:** Naming/comment drift (acknowledged; do not block)

**Test Artifacts Verified:**
- Targeted test suite scope: portfolio/fifo/holdings/scrip/consolidation/ledger_fields
- Result: 1119 passed, 0 failed
- Confirmed no regressions in holdings calculations

**Approval:** APPROVED (notes do not block release)

---

## Validation Summary

**Test Execution:**  
Backend targeted suite run on commit f5ed79f:
```
portfolio/fifo/holdings/scrip/consolidation/ledger_fields: 1119 passed, 0 failed
```

**Coverage Scope:**
- FIFO lot-depletion logic (BUY COMPLETE, BUY ZERO_COST, SELL, TRANSFER_IN/OUT)
- Same-date corporate-action leg ordering (CONSOLIDATION_OUT/IN, FRACTIONAL_CASH_OUT sequences)
- BUY gross/net semantics (aligned with 89a7c2a)
- Migration script idempotency and correctness
- Scrip dividend zero-cost lot behavior

**Status:** ✅ All targeted tests passing; no regressions

---

## Architectural Changes

### Holdings Engine (CMP → FIFO)

**Before:**
- Used chronological moving weighted average (CMP) for cost-basis calculation
- Lot consumption order undefined (implicit, not enforced)
- Unpaid shares side-channel for ZERO_COST handling

**After:**
- FIFO (First-In-First-Out) lot depletion by (trade_date, ca_group_id, ca_group_seq, id)
- BUY COMPLETE creates lot at `net_eur` (gross + fees)
- BUY ZERO_COST creates lot at cost 0 (natural dilution of avg_cost_basis_eur)
- SELL/TRANSFER_OUT consume oldest lots first
- TRANSFER_IN creates lot at `carried_cost_basis_eur`

**Properties:**
- Deterministic lot consumption order (no undefined behavior)
- ZERO_COST shares dilute average naturally (no side-channel)
- FIFO order preserved across all corporate-action scenarios

### Same-Date Corporate-Action Leg Ordering

**Before:**
- Movements sorted by `(trade_date, id)` only
- Random UUID ordering caused CA legs to process in unpredictable sequence
- Consolidation scenarios on same date: OUT/IN/FRACTIONAL_CASH_OUT order undefined

**After:**
- Movements sorted by `(trade_date, ca_group_id, ca_group_seq, id)`
- CA leg sequence deterministic regardless of UUID ordering
- Share consolidation RKT scenario: 100% reproducible (0% randomization failures)

**Evidence:**
- Basher's randomized reproducer on RKT scenario: 66% failure rate → fixed to 0% failure rate

---

## Files Modified in This Release

### Core Holdings Engine
- `backend/src/portfolio/holdings_service.py` — 225 lines rewritten for FIFO semantics

### Supporting Changes
- `backend/src/portfolio/fx_service.py` — net/gross accounting support
- `backend/src/portfolio/import_service.py` — net/gross semantics
- `backend/src/portfolio/parsers/purchases.py` — net/gross detection

### Migration & Repair
- `backend/scripts/repair_buy_ledger_fields.py` — Migration script for correcting existing BUY records (net/gross inversion fixes)
- `backend/tests/test_repair_buy_ledger_fields.py` — Migration test coverage (part of 1119 passing tests)

### Test Updates
- `backend/tests/test_amendment_g_bilingual.py` — 6 lines adjusted for FIFO semantics
- `backend/tests/test_amendment_h_holdings_effects.py` — 19 lines adjusted for FIFO semantics
- `backend/tests/test_portfolio_corrections_extended.py` — 14 lines adjusted for FIFO semantics
- All targeted suite tests (1119 total) passing

---

## Decisions & Documentation Updates

### .squad/decisions.md Update

**Entry Status:** Staged in git index (not yet committed)  
**Entry:** "Basher's Review Finding: Ordering Invariant Violation" → RESOLVED  
**Reference:** Commit f5ed79f  
**Content:** 
- Documents Basher's finding: same-date CA legs process in random order (66% failure rate)
- Documents fix: movement sort order `(trade_date, ca_group_id, ca_group_seq, id)`
- Documents validation: targeted suite 1119 passed, 0 failed

**Merge Status:** No inbox entries found in `.squad/decisions/inbox/` — no merges needed

---

## Release Artifacts

**Commit:** f5ed79f05b2eb86ff7adf6c1f0ddb131f6d07488  
**Author:** Copilot  
**Date:** 2026-09-08 20:57:52 +0200  
**Branch:** main  
**Message:** fix: migrate holdings engine to FIFO and fix CA leg ordering

**Commit Trailer:**
```
Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
Copilot-Session: 56a1988e-76fe-4cbc-a066-fd058a043fca
```

---

## Exclusions & Unrelated Changes

**Symbol Detail Redesign:** Uncommitted in working tree — deferred to separate release  
**AD/XAMS Security ID Repair:** Uncommitted in working tree — deferred to separate ticket  
**Account Label/Badge Frontend:** Uncommitted in working tree — deferred to separate release

No unrelated changes are included in this release.

---

## Review & Approval Chain

1. **Code Review:** Livingston (Persistence & Integration Engineer) — APPROVE WITH NOTES
2. **Test Validation:** Targeted suite (1119 passed, 0 failed)
3. **Release Status:** Approved for main branch

**Gate Verdict:** APPROVED — Ready for production

---

## Known Limitations & Follow-Up

None identified. CA leg ordering fix is complete; FIFO migration is deterministic and reversible via migration script.

---

**Orchestration Log Completed:** 2026-09-08T18:59:48Z  
**Scribe Signature:** Scribe (Documentation Specialist)
