# Danny — Final UI Point Gate: Movements Search / Summary Card / Chat Rename / Economics Styling

**Verdict: REJECTED (narrow)** — one high-confidence blocker in `PortfolioMovementsTable.tsx`.
Everything else in scope is **APPROVED**.

## Scope
- `frontend/src/components/StockTransactionsTable.tsx` — Symbol Details time filter
- `frontend/src/components/PortfolioMovementsTable.tsx` — global symbol search, type pills, Economics styling
- `frontend/src/components/ui/NativeSelect.tsx`
- `frontend/src/components/SymbolsTable.tsx`, `frontend/src/app/symbols/page.tsx` — Economics styling only
- `frontend/src/components/SymbolSummary.tsx` — double-card removal
- `frontend/src/components/GlobalChatView.tsx` — Options Chat rename
- Tests: `symbolDetailMovementsFilter`, `movementsSymbolFilter`, `economicsParity`

## APPROVED items

1. **`StockTransactionsTable.tsx` (contract #1)** — clean. Type filter + independent 1m/3m(default)/6m/1y/All time filter, calendar-inclusive lower bound (verified TB-1..TB-8: from-date and today both inclusive, calendar-month arithmetic with correct month-end clamping), `setPage(0)` on either filter change (PG-1..PG-6). Filters by a single known `security_id` prop (exact match is correct here — never user-typed free text). No client-side re-filtering on top of server pagination. **Sound.**

2. **`ui/NativeSelect.tsx`** — trivial, correct `forwardRef` pass-through wrapper. No concerns.

3. **`SymbolSummary.tsx` double-card fix (contract #4)** — confirmed by reading actual render tree in `frontend/src/app/symbols/[symbol]/page.tsx`: `SymbolSummary` is wrapped in exactly one `<DetailSection title="Summary">` container (the same shared card primitive used elsewhere, e.g. Action Plans), and `SymbolSummary.tsx` itself has no internal bordered/card wrapper of its own (plain `overflow-x-auto` div). Exactly one visual container. **Sound.**

4. **`GlobalChatView.tsx` rename (contract #5)** — verified: `type Mode = "portfolio" | "quick-analysis"` and `selectMode("portfolio")` internal key untouched; all three visible label sites (`modeLabel`, tab `title`, `<h2>`) now read "Options Chat". No unrelated Telegram-notification code touched in this file. **Sound.**

5. **Economics styling (contract #3)** — `SymbolsTable.tsx` filter/search bar and card surfaces now use the same design tokens as the Economics page (`bg-bg-card`, `border-border`, `rounded-[var(--radius-pill)]` pill toggles), explicitly commented `/* surface card matching Economics filter section */`. Purely visual token changes; no filter/row logic touched. `economicsParity.test.mjs` passes. **Sound.**

## REJECTED item — Movements global symbol search (contract #2)

`frontend/src/components/PortfolioMovementsTable.tsx` combines a **client-side substring/multi-field filter** (`matchesMovementSymbol`, matches `security_id`/`ticker`/`company_name`) with a **server-paginated, exact-match-only** data source, producing two independent correctness failures:

- **Incomplete matches.** `filteredMovements` (≈ line 97, `useMemo` keyed on `[data, securityId]`) filters only `data.movements` — the currently loaded `PAGE_SIZE = 50` server page. Any match beyond that page is invisible. Backend `get_movements()` (`backend/src/portfolio/cosmos_portfolio.py` ~line 1285) does `c.security_id = @security_id`, an **exact** match with no substring/company-name support — so typing a partial ticker or company name and clicking "Apply" sends a query that returns zero rows even when real matches exist.
- **Incoherent pagination.** The footer text (`Showing {offset+1}–{min(offset+PAGE_SIZE, total_count)} of {total_count}`) and the Prev/Next `disabled` predicates (`offset === 0`, `offset + PAGE_SIZE >= data.total_count`) unconditionally use the server's unfiltered `data.total_count`/`offset` — never `filteredMovements.length`. Whenever a symbol query narrows the visible rows below the page size, the counts and Next-button state are simply wrong.

This directly violates contract #2 ("Search... composes correctly and coherent pagination/counts").

**Test-quality gap confirms the defect was not caught by design.** `movementsSymbolFilter.test.mjs` only exercises an inline mirror of the match predicate and a `setOffset(0)`-on-change source-contract check (`SPR` group). No test in the file asserts pagination-footer text, `Next` disabled-state, or multi-page completeness against `total_count`. The suite gives false confidence that "parity" was achieved while never testing the one thing that breaks it.

### Required fix — direction mandated
Do **not** attempt to patch this by tuning the client-side filter. Replace server-side pagination for this view's symbol-search path with the same pattern the Symbols page itself already uses successfully: fetch the full type/date/account-filtered movement set for the active range (unpaginated, or paginated far beyond typical result sizes) and filter + paginate entirely client-side, so `filteredMovements.length` becomes the sole source of truth for the footer and Prev/Next state. Do not add a new backend free-text query capability — it duplicates logic the frontend already has and re-introduces the same class of drift Symbols/Movements just diverged on. If total data volume makes a full unpaginated fetch impractical, that must be raised back to Danny rather than silently reintroducing partial-page client filtering.

### Assignment (lockout enforced)
- **Rusty is locked out** (original author of `PortfolioMovementsTable.tsx` for this batch).
- **Product fix → Linus**: re-architect the Movements data-fetch/pagination strategy per the mandated direction above; keep type/time filters, account reassignment, and all other approved behavior byte-for-byte unchanged.
- **Test revision → Basher**: rewrite `movementsSymbolFilter.test.mjs` to assert against the real component behavior (not an inline predicate mirror) for multi-page completeness and pagination-footer/Next-button coherence once the fetch strategy changes; do not weaken existing SMF/SCI/SCR/SAC/STM/STT/NM coverage.

## Test evidence
- Ran `symbolDetailMovementsFilter.test.mjs` + `movementsSymbolFilter.test.mjs` + `economicsParity.test.mjs` together: **190/190 pass**, 0 fail — confirms all currently-written assertions pass, but per above this suite does not cover the actual defect.
- Source-inspected `GlobalChatView.tsx`, `SymbolSummary.tsx` + its usage in `[symbol]/page.tsx`, `SymbolsTable.tsx` styling diff, `ui/NativeSelect.tsx` directly (no test gaps found in these).

No product code modified by this review. No production calls made.
