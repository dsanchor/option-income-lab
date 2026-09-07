# Review: Portfolio CMP Cost-Basis + Movements Toolbar

**Date:** 2026-09-06
**Reviewer:** Danny (Lead Architect)
**Decision:** ✅ APPROVED
**Scope:** 13 files — backend CMP algorithm, Pydantic models, frontend summary bar, TS types, Movements toolbar, write_ledger_txn safety guard, 209 tests

---

## Summary

The CMP (moving weighted average) cost-basis implementation replaces the old `purchases − sale_proceeds` formula with true remaining pool cost. All 14 review requirements pass. No high-confidence blockers.

**UI Gate (added 15:57):** Portfolio summary already uses `StatCard` + `Reveal` matching Economics/Dashboard pattern. `tsc --noEmit` clean. Gate passed.

### Key findings

| # | Requirement | Status |
|---|---|---|
| 1 | CMP chronological & deterministic | ✅ sort by (trade_date, id) |
| 2 | BUY includes commission; SELL subtracts | ✅ |
| 3 | SELL ACCIONES removes correct CMP basis | ✅ |
| 4 | SELL DERECHOS leaves pool untouched | ✅ |
| 5 | Transfers preserve global cost basis | ✅ |
| 6 | Incomplete/zero-cost handled safely | ✅ |
| 7 | Negative inventory ≥ 0 remaining basis | ✅ |
| 8 | Full exit clears pool, avg=null | ✅ |
| 9 | Corrections/deleted/voided excluded | ✅ |
| 10 | API aliases backward compatible | ✅ |
| 11 | Summary portfolio-wide, filter-independent | ✅ |
| 12 | No tax/FIFO claims in UI | ✅ |
| 13 | Movements toolbar/filters correct | ✅ |
| 14 | Voided import guard: correct, tested, INCLUDE | ✅ |

### Non-blocking notes

- Contract says FIFO; team pivoted to CMP. Frontend tooltips accurate.
- Contract §3.1 realized formula has double-count bug; code correctly avoids it.
- `HoldingEntry` TS type omits `current_invested_eur` (unused per-holding in FE).

### Tests

209/209 pass (58 holdings + 21 corrections + 130 Basher acceptance).
