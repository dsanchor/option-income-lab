# Livingston — Project History

## Core Context

- **Project:** options-agent
- **User:** dsanchor
- **Role:** backend persistence, aggregation, and integration owner
- **Focus areas:** portfolio ledger contracts, economics/report endpoints, FIFO/CMP correctness, and service seams that must stay pure-function testable
- **Durable pattern:** keep heavy business logic in pure Python modules first, then wire thin FastAPI/Cosmos adapters on top
- **Durable pattern:** ledger readers and writers must preserve complete financial field shapes (`gross`, `fees`, `net`, `withholding`) across manual, imported, and corporate-action flows
- **Durable pattern:** portfolio/economics APIs should avoid misleading blended totals when currencies or upstream contracts are not authoritative enough
- **History note:** pre-2026-09 detail was condensed on 2026-09-09 into this core summary to keep the file maintainable

## Recent Learnings

### 2026-09-09 — Dividends economics backend endpoints
- Added `backend/src/dividends_economics.py` as a pure aggregation module so dividend economics behavior can be unit-tested without Cosmos fakes.
- Added `GET /api/economics/dividends` and `GET /api/economics/overview` in `backend/web/app.py`.
- `yearly` and `cumulative` intentionally ignore `year` / `month` while still honoring `symbol` and `account_id`; the response declares that scope explicitly.

### 2026-09-08 — Manual ledger field normalization
- Normalized manual movement, transfer, correction, and corporate-action write paths so detail views always receive `gross`, `fees`, `net`, and `withholding` blocks.
- Reinforced the rule that round-trip-safe document shape matters as much as arithmetic correctness.

### 2026-09-08 — FIFO, cost-basis, and corporate-action semantics
- Confirmed/implemented the current contract that BUY cost uses net economics, zero-cost scrip shares enter holdings at zero cost, and grouped corporate actions must preserve event-level meaning.

## Learnings

### 2026-09-11 — Paper positions simplified to a position-only toggle
- Reverted movement-side `is_paper` plumbing from manual creation/correction/duplicate detection so paper status lives only on the symbol position document, per direct user direction.
- Added a dedicated toggle endpoint in `backend/web/app.py:3300` (`PATCH /api/symbols/{symbol}/positions/{position_id}/paper`) backed by `backend/src/cosmos_db.py:703`, instead of faking/linking paper movements.
- Added `coverage_status = "paper"` in `backend/src/portfolio/option_linkage_service.py:407` and suppressed linkage warnings for paper positions so they disappear from the unlinked-warning bucket without affecting real-economics totals.

### 2026-09-11 — Paper positions + economics movement drilldown
- Implemented paper-position persistence and movement parity across `backend/web/app.py`, `backend/src/cosmos_db.py`, `backend/web/portfolio_routes.py`, and `backend/src/portfolio/cosmos_portfolio.py`.
- Added `option_position_id` filtering to the movements API and wired the economics drilldown UI in `frontend/src/lib/portfolio-api.ts` and `frontend/src/components/EconomicsView.tsx`.
- Propagated `is_paper` through linkage/report layers in `backend/src/portfolio/option_linkage_service.py`, `backend/web/app.py`, `frontend/src/types/economics.ts`, and `frontend/src/types/portfolio.ts`.
- Added paper-aware UX in `frontend/src/components/OptionLinkageBadges.tsx`, `MovementDetailDialog.tsx`, `AddPositionForm.tsx`, `AddMovementDialog.tsx`, and `EconomicsOverviewView.tsx`.
- No intentional spec deviations in backend contract scope; frontend implementation detail choices are recorded in `.squad/decisions/inbox/livingston-paper-positions-impl.md`.
