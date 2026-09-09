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
