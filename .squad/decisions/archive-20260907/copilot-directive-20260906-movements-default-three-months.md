### 2026-09-06: Default Movements to the last three months
**By:** Copilot (via Copilot)
**What:** The Movements page must initially load movements from the current date back three calendar months. Users can change or clear the date range to view older history. The default limits only the initial query and never deletes or excludes stored data permanently.
**Why:** User requested a smaller, more relevant default movement window.

---
### 2026-09-06: Implementation learnings (Rusty)

#### TASK A — Symbol Route Double Encoding

**Root cause:** Next.js 16 App Router may deliver `params.symbol` as the raw percent-encoded URL segment (`XNYS%3AAAD`) rather than the decoded value (`XNYS:AAD`) when navigation occurs via a `<Link>` whose `href` already contains a percent-encoded colon. Every BFF route handler and the server-side page then called `encodeURIComponent('XNYS%3AAAD')` → `XNYS%253AAAD`, which the Python backend 404s.

**Files changed:**
- `src/lib/symbolEncoding.ts` *(new)* — `decodeSymbolParam(raw)` (safe single decode) and `symbolHref(id)` (raw colon, valid RFC 3986 pchar).
- `src/app/symbols/[symbol]/page.tsx` — decode param before `getData` and before passing to client children.
- All 28 `src/app/api/symbols/[symbol]/…/route.ts` handlers — normalize `params.symbol` with `decodeSymbolParam` before `encodeURIComponent`.
- `src/app/screener/dgi/analyze/[symbol]/page.tsx`, `src/app/api/debug/agent-chain/[symbol]/route.ts`, `src/app/api/dgi/analyze/[symbol]/route.ts` — same decode-once normalization.
- `src/app/symbols/[symbol]/best-options/page.tsx`, `chat/page.tsx`, `forecasts/page.tsx`, `options-chain/page.tsx`, `report/page.tsx`, `technical-analysis/page.tsx` — same.
- `src/components/SymbolsTable.tsx` — use `symbolHref(r.security_id ?? r.symbol)` (no `encodeURIComponent` in href); pass `r.security_id ?? r.symbol` to modal.
- `src/components/SymbolDisambiguation.tsx` — use `symbolHref(c.security_id)`.
- `tests/symbolEncoding.test.mjs` *(new)* — 14 tests: plain ticker, XNYS:AAD, XNYS%3AAAD, XBRU:COLR round-trips, `%253A` absence assertion.

**Encoding boundary contract:** params decoded ONCE via `decodeSymbolParam`, then encoded ONCE via `encodeURIComponent` for the backend. `AddSymbolForm` `navigate_to` already carries a raw colon from the backend — no change needed. Legacy ticker routes (`/symbols/AAPL`) unaffected.

#### TASK B — Movements Default Last Three Calendar Months

**Root cause:** `PortfolioMovementsTable` initialised `dateFrom`/`dateTo` as empty strings, loading all movements. Initial load called `load(0, {})` with no date filter.

**Files changed:**
- `src/lib/dateHelpers.ts` *(new)* — `subCalendarMonths(date, n)` (month-end clamp for leap/non-leap years), `toLocalDateString(date)` (local time, no UTC off-by-one), `getDefaultMovementsDateRange()`.
- `src/components/PortfolioMovementsTable.tsx` — state initialised with lazy `getDefaultMovementsDateRange()`; `useEffect` initial load passes default range; `resetFilter` restores 3-month range instead of clearing; empty-state message updated to include date filter in check.
- `tests/dateHelpers.test.mjs` *(new)* — 15 tests covering ordinary, month-end clamp, leap year (2024-02-29), non-leap year (2023-02-28), cross-year boundary.

**Build/test results:** `node --test tests/*.test.mjs` → 212/212 pass; `tsc --noEmit` → clean; `next build` → success (37 static + dynamic routes).
