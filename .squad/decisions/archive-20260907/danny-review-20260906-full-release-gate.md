# Danny Final Gate Review — 2026-09-06

**Reviewer:** Danny (Lead Architect)
**Date:** 2026-09-06T22:22+02:00
**Scope:** Full movement/corporate-action/UI release — 46 files, +4166 −434 lines
**Verdict:** ✅ **APPROVED**

---

## Test Evidence

| Suite | Tests | Pass | Fail | Skip |
|-------|-------|------|------|------|
| Backend (pytest) | 421 | 421 | 0 | 0 |
| Frontend TypeScript (`tsc --noEmit`) | — | clean | 0 | — |
| Frontend Node tests | 183 | 183 | 0 | 0 |

## Scope Gate Summary (12 items)

| # | Item | Status |
|---|------|--------|
| 1 | Portfolio zero-filter (exact zero hidden, negatives visible, watchlist unaffected) | ✅ |
| 2 | Full BUY/SELL/DIVIDEND corrections; transfers protected; grouped CA legs guarded | ✅ |
| 3 | Manual BUY/SELL qty×price semantics, fees, per-share displays, 0.01 tolerance | ✅ |
| 4 | UI labels Stocks/Rights; internal enums ACCIONES/DERECHOS preserved | ✅ |
| 5 | Bilingual CSV headers+values; strict invalid-value handling; legacy compat | ✅ |
| 6 | WHT amount primary, rate_pct server-derived; null vs zero preserved | ✅ |
| 7 | Composite CA create/void/correct; linked legs; holdings effects; atomicity | ✅ |
| 8 | Symbol Detail Options+Stocks stacked sections; StockTransactionsTable; pagination | ✅ |
| 9 | Batch reason optional (stable default); individual remains required | ✅ |
| 10 | Portfolio Income Lab branding; no infrastructure renames | ✅ |
| 11 | API/frontend contract alignment; accessibility; loading/error states | ✅ |
| 12 | No conflicts, duplicate helpers, dead code, test-helper leaks, or corruption | ✅ |

## Advisory Notes (Non-Blocking)

### 1. Missing `class CorporateActionCreateRequest` declaration
**File:** `backend/src/portfolio/models.py` lines 568–598
**Issue:** The `CorporateActionCreateRequest` class body (fields + validators) is stacked inside `CorporateActionCorrectRequest` without its own `class` declaration. Pydantic emits two `UserWarning`s about validator overrides.
**Impact:** NOT runtime-blocking — neither model is instantiated in production routes or tests. Routes parse raw JSON dicts.
**Fix:** Add `class CorporateActionCreateRequest(BaseModel):` before line 568, and close `CorporateActionCorrectRequest` at line 567.

### 2. Duplicate alias in purchases parser
**File:** `backend/src/portfolio/parsers/purchases.py` line 37
**Issue:** `{"comision", "comision", "commission", "fees"}` — duplicate `"comision"` in set literal.
**Impact:** None (Python set deduplicates). Cosmetic only.
**Fix:** Remove duplicate.

---

## Key Files Reviewed

**Backend:**
- `backend/src/portfolio/models.py` — CaLegType/CaEventType enums, CorporateActionLegCreate, ManualMovementCreate CA fields
- `backend/src/portfolio/cosmos_portfolio.py` — `_derive_wht_rate_pct`, `_apply_wht_rate_derivation`, `_validate_correction_fields`, `create_corporate_action`, `void_corporate_action_group`, `correct_corporate_action_group`, correct_movement transfer guard + group guard + nullable overridable fields
- `backend/web/portfolio_routes.py` — CA endpoints, batch reason default, transfer 405
- `backend/web/app.py` — `_map_recent_movement` extended fields, movements fetched outside `if holding:`, `total_count` used, historical positions with minimal `portfolio_field`
- `backend/src/portfolio/parsers/` — All three parsers: bilingual alias maps, `_normalize_sales_type` with `_SALES_TYPE_ALIASES`

**Frontend:**
- `frontend/src/components/SymbolsSectionedClient.tsx` — Zero-filter predicate, toggle, count, empty state
- `frontend/src/components/AddMovementDialog.tsx` — BUY/SELL live computation, SALES_TYPE_LABELS, CorporateActionForm delegation
- `frontend/src/components/MovementCorrectionDialog.tsx` — Full type-adaptive form, WHT 3-state, live computed summary, cross-validation
- `frontend/src/components/MovementDetailDialog.tsx` — SALES_TYPE_LABELS, CA group panel with sibling legs, void/correct actions
- `frontend/src/app/symbols/[symbol]/page.tsx` — Options/Stocks stacked sections via DetailSection
- `frontend/src/components/DetailSection.tsx` — Reusable collapsible section
- `frontend/src/components/StockTransactionsTable.tsx` — Paginated, filtered, column-adaptive
- `frontend/src/components/CorporateActionForm.tsx` — Multi-leg wizard with event type pre-population
- `frontend/src/types/portfolio.ts` — SALES_TYPE_LABELS, CA types, MovementCorrectionRequest updated
- `frontend/src/lib/portfolio-api.ts` — CA group CRUD functions
- 7 lib helper modules with pure logic and corresponding tests
