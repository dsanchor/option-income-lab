# Scrip Zero-Cost & BUY Gross-Net Release — Session Log

**Date:** 2026-09-08 11:30+0200  
**Session:** Scribe documentation — release completion and production migration verification  
**Release:** fix: correct scrip cost basis and BUY totals  
**Commit:** 4ca553e7fd1ba7e4ac10751231540ca71ae5de1d  
**GitHub Actions:** Run 34197339468 — SUCCESS

---

## Executive Summary

Completed and documented the Scrip Zero-Cost & BUY Gross-Net Correction release. Coordinated with five team members (Reuben, Basher, Danny, Linus, Rusty) to:

1. **Implement** fix for zero-cost (scrip dividend) shares entering the CMP pool at cost 0 (not a side-channel unpaid_shares)
2. **Correct** BUY import semantics: `gross_eur = net_consideration + commission` (inverted-name fix)
3. **Execute** production migration: audit-default, apply-explicit, manual candidates require confirmation
4. **Verify** zero remaining candidates (idempotent repair), all 45 SELL records untouched, 339 BUY records repaired with 0 failures/collisions
5. **Test** at scale: 469 backend + 1223 frontend tests all passing, zero regressions

**Timeline:**
- 2026-09-07 23:00 — Contracts finalized (Danny, Reuben)
- 2026-09-08 00:30 — Backend implementation complete (Basher fixture updates)
- 2026-09-08 07:35 — Final contract gate APPROVED (Danny)
- 2026-09-08 07:58 — Code review gate APPROVED (Danny)
- 2026-09-08 09:32 — Clean release candidate built (Rusty)
- 2026-09-08 10:45 — Clean-diff release gate APPROVED (Danny)
- 2026-09-08 07:15–08:45 UTC — Production migration executed, verified
- 2026-09-08 11:30 — Session documentation complete

---

## Release Artifacts

| Artifact | Status |
|----------|--------|
| Code changes (21 files) | ✅ Committed to main (4ca553e7...) |
| Backend tests (469) | ✅ All passing |
| Frontend tests (1223) | ✅ All passing |
| Migration tests (55) | ✅ All passing |
| GitHub Actions run | ✅ Run 34197339468 — SUCCESS |
| Production deployment | ✅ Code deployed, migration executed |
| Backup + checksum | ✅ Verified, stored with SHA-256 |

---

## Key Technical Decisions

### 1. ZERO_COST Pool Entry (resolves arithmetic bug)
**Decision:** ZERO_COST shares enter the CMP pool at cost 0, not a side-channel unpaid_shares.

**Rationale:** 
- Zero-price imports are fully parsed and resolved; cost is known (it's zero)
- Entering pool at 0 naturally dilutes avg_cost_basis_eur to correct value
- No separate `effective_avg_cost_eur` field needed (user directive superseded earlier proposal)
- ACS example: 163 paid @ €26.34 + 60 ZERO_COST @ €0 → pool avg €20.33

**Impact:** avg_cost_basis_eur now correctly represents weighted average of *all* shares (including unpaid)

### 2. BUY Gross/Net Semantics (fixes inverted naming)
**Decision:** BUY CSV import column "Total (€)" is NET consideration; stored as `gross_eur = net + commission`.

**Rationale:**
- CSV total does not include commission (trader adds separately)
- Holdings engine uses `cost = gross_eur` → natural to include commission in the "gross" field
- SELL semantics unchanged: "Total Venta" is gross proceeds
- All existing code accidentally correct due to double naming confusion

**Impact:** Ledger field names now semantically aligned with their values

### 3. Migration Marker-First Idempotency
**Decision:** `_repair_buy_fields_v1` marker checked unconditionally first, before any repair logic.

**Rationale:**
- Marker presence = record already repaired (cannot fail from re-running)
- No tautological cost-detection logic
- Audit reports all candidates; apply is explicit; manual candidates require confirmation
- Second audit on repaired DB returns zero candidates

**Impact:** Safe, idempotent repair with zero risk of cascading edits

### 4. INCOMPLETE Classification Reservation (future-proofing)
**Decision:** INCOMPLETE now reserved for genuinely unknown costs; ZERO_COST for resolved-at-zero costs.

**Rationale:**
- Current zero-price imports are the only INCOMPLETE records
- Future import enhancements (missing column, user "cost unknown") will use true INCOMPLETE path
- Warning semantics: INCOMPLETE_COST_BASIS emitted only for true unknowns

**Impact:** Clear future path for handling truly missing costs

---

## Production Migration Results

**Audit (read-only scan):**
- Records scanned: 492 CSV BUY records
- Auto-repair candidates: 339
- Gross/net swap candidates: 280
- Status reclassifications (INCOMPLETE → ZERO_COST): 60
- Overlap candidates: 1
- Ambiguous/error records: 0

**Apply (execution):**
- Records patched: 339
- Failed/collisions: 0
- ETag/CAS write success rate: 100%

**Verification (second audit):**
- Remaining candidates: 0 (all repaired in first pass)

**Backup:**
- Location: Session workspace (safe from product commit)
- SHA-256 checksum verified
- Restore path documented for rollback (unused)

**Post-Migration Verification (ACS holding example):**
- Shares: 223 (163 paid + 60 ZERO_COST)
- Pool cost: EUR 4,534.62 (commissions corrected)
- Avg cost: EUR 20.33 (natural dilution from ZERO_COST pool entry)
- ZERO_COST shares in pool: 13
- INCOMPLETE records: 0 (all reclassified or confirmed)
- SELL records touched: 0 (all 45 untouched)
- Global ZERO_COST shares: 485
- Active INCOMPLETE records: 0

---

## Testing Coverage

### Backend (469 tests)
- Cost basis semantics: 8 test suites covering BUY/SELL/TRANSFER accounting
- Scrip-specific: 37 tests for ZERO_COST dilution, INCOMPLETE exclusion, partial sells
- Migration: 55 tests for marker-first, source validation, manual candidates, checksum verification
- Regression: 469 all passing (no P&L, SELL accounting, or multi-account surprises)

### Frontend (1223 tests)
- Type contracts: portfolio.ts, symbol-detail.ts type updates verified at compile time
- Display fields: PortfolioHoldingsTable remaining_cost_basis_eur field usage
- Warning cleanup: ZERO_COST_ACQUISITION references removed from 5 surfaces
- Regression: 1223 all passing (no UI, state management, or integration surprises)

---

## Cross-Agent Coordination Notes

| Challenge | Solution | Outcome |
|-----------|----------|---------|
| Early effective_avg proposal (danny-effective-average-cost-display-contract) vs. later pool-entry directive | Recognized user directive superseded earlier proposal; rewrote contract to simpler pool-entry fix | Single contract (danny-scrip-zero-cost-and-buy-import-contract.md) serves as canonical decision |
| Fixture updates blocking test pass (Basher) | Prioritized fixture corrections to reflect new pool-entry semantics before code review | All 469 tests passing with accurate semantics |
| Frontend type contract cleanup (Linus) | Removed effective_avg_cost_eur fields; kept avg_cost_basis_eur | TypeScript strict mode maintained; zero runtime surprises |
| Migration idempotency at scale (Reuben) | Implemented marker-first with source-row cross-validation | Second audit: 0 candidates (idempotent) |
| Release candidate assembly (Rusty) | Clean build from origin/main; verified diff at 21 files (no cruft) | GitHub Actions run 34197339468 SUCCESS |

---

## Decisions Merged into .squad/decisions.md

| Inbox File | Status | Action |
|-----------|--------|--------|
| danny-effective-average-cost-display-contract.md | SUPERSEDED | Marked as superseded; summary recorded |
| danny-scrip-zero-cost-and-buy-import-contract.md | APPROVED | Merged (canonical decision) |
| danny-final-scrip-buy-review.md | APPROVED | Merged (gate record) |
| danny-review-scrip-buy-import-implementation.md | APPROVED | Merged (implementation detail) |
| copilot-correction-20260908-scrip-cost-and-buy-import.md | USER DIRECTIVE | Referenced in merged decisions |
| copilot-directive-20260907-account-name-only.md | UNRELATED | Left in inbox (account UI, not in scope) |
| danny-post-release-ui-batch-final-gate.md | UNRELATED | Left in inbox (UI batch, not in scope) |

---

## Lessons Learned

### Architecture
- **Pool-first design:** Entering zero-cost shares at cost 0 (not a side-channel) naturally preserves arithmetic and dilution logic
- **Semantic naming matters:** Inverted field names (gross/net mislabeled) caused accidents-working-correctly scenario; correcting names improves future maintainability
- **Warning classification:** Separating resolved-but-zero from genuinely-unknown costs enables cleaner future extensions

### Testing
- **Fixture-first refactoring:** Updating test fixtures *before* code changes catches assumptions early
- **Domain-specific suites:** Dedicated 37 scrip tests isolated the feature logic and made regressions visible
- **Scale validation:** 469 + 1223 tests all passing proves no hidden assumptions in P&L, SELL, or multi-account logic

### Migration
- **Marker-first guard:** Unconditional marker check (not fallback) enables safe idempotence at scale
- **Audit-explicit-verify:** Three-step pattern (audit read-only, explicit apply, re-audit verify) caught all issues before second touch
- **Manual candidate flags:** Requiring explicit --apply-manual-ids for trader-discretionary BUYs prevented accidental bulk edits

### Release
- **Clean release candidate:** Building from origin/main with diff verification ensures no cruft
- **Cross-team gates:** Danny's three gates (contract, code, diff) created clear approval points
- **Deployed-first migration:** Code live in production before repair runs ensures new import/holdings semantics active

---

## Conclusion

Scrip Zero-Cost & BUY Gross-Net Correction release successfully completed:
- ✅ 21 files committed, pushed to main
- ✅ 469 backend + 1223 frontend tests all passing
- ✅ GitHub Actions run 34197339468 SUCCESS
- ✅ Production deployed and migration executed
- ✅ 339 BUY records repaired with 0 failures
- ✅ 0 remaining candidates (idempotent)
- ✅ All 45 SELL records untouched
- ✅ Backup verified and stored

**Status:** COMPLETE

