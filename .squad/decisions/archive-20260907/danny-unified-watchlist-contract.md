# Implementation Contract: Unified Watchlist (Portfolio + Watchlist Merge)

**Date:** 2026-09-06  
**Author:** Danny (Lead Architect)  
**Status:** PROPOSED — amendment to Symbol Unification rev 3  
**Directives consumed:**
- `copilot-directive-20260906-merge-portfolio-into-watchlist.md`
- `copilot-directive-20260906-us-only-symbol-actions.md` ← **Amendment J**  
**Supersedes:** Two-section layout in `danny-symbol-unification-implementation-contract.md` §5  
**Preserves:** All prior Symbol Unification rev 3 decisions (ensure_symbol_config, security_id, backfill)  
**Concurrent directives (independent, no conflict):**
- `copilot-directive-20260906-account-colors.md` — account color badges
- `copilot-directive-20260906-account-display-name.md` — readable account labels
- `copilot-directive-20260906-movements-default-three-months.md` — movements date default

---

## 1. Unified-Row Predicate & Duplicate Precedence

### 1.1 Row Inclusion Predicate

A symbol appears in the unified table if **any** of the following is true:

```
VISIBLE(symbol) =
    has_symbol_config(symbol)                           # Watchlist membership (manual or auto-enrolled)
  AND (
    portfolio_shares(symbol) != 0                        # Current holding (positive or negative)
    OR is_watchlist_member(symbol)                        # Explicit watchlist (not auto-enrolled only)
    OR (portfolio_shares(symbol) == 0 AND NOT hide_zero) # Historical with toggle off
  )
```

**Concrete rules:**

| Scenario | Visible by default? | Reason |
|----------|---------------------|--------|
| Current holding (shares > 0) | ✅ Yes | Active position |
| Negative holding (shares < 0) | ✅ Yes | Anomaly — always visible |
| Watchlist-only (no ledger history) | ✅ Yes | Explicit watchlist membership |
| Manual watchlist + historical zero shares | ✅ Yes | Explicit membership overrides zero filter |
| Auto-enrolled only + exactly zero shares | ❌ Hidden | Historical, no explicit watchlist interest |
| Auto-enrolled only + zero shares + toggle OFF | ✅ Yes | User chose to see all |

### 1.2 Detecting Explicit Watchlist Membership

A symbol has explicit watchlist membership when:

```python
is_watchlist_member(config) = (
    not config.get("_auto_enrolled", False)     # manually added symbol
    or config.get("watchlist", {}).get("covered_call", False)
    or config.get("watchlist", {}).get("cash_secured_put", False)
    or config.get("watchlist", {}).get("buy_tracker", False)
    or config.get("telegram_notifications_enabled", False)
)
```

**Rationale:** If the user has interacted with a symbol's watchlist toggles or manually added it, it has explicit membership. An auto-enrolled symbol that was never configured is purely historical.

### 1.3 Duplicate Precedence

Each ticker appears **exactly once**. There are no duplicates because `symbol_config` is the single source of truth (one doc per ticker). Portfolio data enriches the row; it does not create separate rows.

### 1.4 Visual Indicators

| Row type | Badge |
|----------|-------|
| Current holding (shares ≠ 0) | `portfolio_shares` column shows count |
| Watchlist-only | Shares column shows "—" |
| Historical zero (visible via toggle) | Shares show "0", row receives `opacity-60` styling |

---

## 2. Overview API Shape (`GET /api/symbols/overview`)

### 2.1 Response Shape — Flat Union (replaces two-section)

```json
{
  "rows": [ ... ],                    // single unified array
  "symbol_count": 25,
  "total_call_exposure": 15000.0,     // Row 1, col 1
  "total_put_exposure": 22500.0,      // Row 1, col 2
  "portfolio_summary": {              // NEW — Row 2 metrics
    "remaining_cost_basis_eur": "48230.15",   // Total Investment
    "realized_result_eur": "3412.00",         // Net Gains (realized only)
    "total_dividends_eur": "1245.80"          // Total Dividends
  },
  "last_update_ts": "2026-09-06T..."
}
```

**Removed fields:** `portfolio_rows`, `watchlist_rows`, `portfolio_count`, `watchlist_count` — superseded by single `rows` array.

### 2.2 Per-Row Fields

Existing fields unchanged, plus:

| Field | Type | Source | Watchlist-only value |
|-------|------|--------|---------------------|
| `portfolio_shares` | `string \| null` | holdings_service | `null` |
| `portfolio_avg_cost_eur` | `string \| null` | holdings_service | `null` |
| `portfolio_invested_eur` | `string \| null` | holdings_service (remaining_cost_basis_eur) | `null` |
| `portfolio_dividends_eur` | `string \| null` | holdings_service (total_dividends_eur per holding) | `null` |
| `portfolio_realized_eur` | `string \| null` | holdings_service (realized_result_eur per holding) | `null` |
| `row_source` | `"portfolio" \| "watchlist" \| "both"` | computed | `"watchlist"` |
| `is_auto_enrolled` | `boolean` | symbol_config._auto_enrolled | `false` |

**`portfolio_invested_eur` semantics:** Maps to `remaining_cost_basis_eur` from CMP holdings (base de coste remanente de los lotes en cartera). NOT total purchase outflow.

### 2.3 Aggregation Fields — Formulas

#### Row 1: Options Exposure (unchanged)

| Field | Formula | Scope |
|-------|---------|-------|
| `total_call_exposure` | `Σ (strike × 100)` for all active call positions across all symbols | Portfolio-wide, unfiltered |
| `total_put_exposure` | `Σ (strike × 100)` for all active put positions across all symbols | Portfolio-wide, unfiltered |

#### Row 2: Portfolio Summary (NEW)

| API Field | UI Label (ES) | Formula | Definition |
|-----------|---------------|---------|------------|
| `remaining_cost_basis_eur` | **Inversión actual** | `Σ remaining_cost_basis_eur` over all holdings | Base de coste CMP de las acciones que aún se poseen |
| `realized_result_eur` | **Resultado realizado** | `Σ (total_sale_proceeds_eur − cost_basis_sold_eur)` over all holdings | Ganancia/pérdida cerrada por ventas de acciones y derechos |
| `total_dividends_eur` | **Dividendos netos** | `Σ net_eur` over all DIVIDEND movements | Dividendos netos recibidos (tras retenciones) |

**Decision: Net Gains = Realized Result Only.**  
Unrealized gains require current market prices (not always available for all symbols, depends on enrichment freshness). Including unrealized would mix reliable ledger data with potentially stale market data. Option P&L is tracked separately in the Economics section.

**Label honestly:** Tooltip reads "Ganancia o pérdida cerrada por ventas de acciones y derechos. No incluye ganancias no realizadas ni P&L de opciones."

### 2.4 Totals vs Filters

**Decision:** Summary totals in Row 1 and Row 2 are **portfolio-wide, unaffected by client-side search/filter/sort**. They reflect the true aggregate state. The "X of Y symbols" counter below the search bar shows filter impact.

---

## 3. Calls Exposure / Puts Committed + Second-Row Metrics

### 3.1 Layout

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Calls Exposure  $15,000          Puts Committed  $22,500                  │  ← Row 1 (options)
├─────────────────────────────────────────────────────────────────────────────┤
│  Inversión actual     Resultado realizado      Dividendos netos            │  ← Row 2 (portfolio)
│  €48,230.15           +€3,412.00  ▲            €1,245.80                   │
│                                                                            │
│  ⚠ 2 valores con coste incompleto                                          │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Conditional Visibility

- **Row 1:** Always visible (options exposure is always relevant).
- **Row 2:** Visible only when `portfolio_summary` is present and at least one holding exists. If portfolio storage is unavailable (503), Row 2 shows a muted "Portfolio data unavailable" message.
- **Warning badge:** Shown when `has_incomplete_cost_basis = true`.

---

## 4. Removal of `/portfolio/holdings`

### 4.1 Redirect Chain

| Old URL | Action | Target |
|---------|--------|--------|
| `/portfolio/holdings` | **HTTP 308 redirect** (Next.js `redirect()`) | `/symbols` |
| `/portfolio` | **HTTP 308 redirect** | `/symbols` |

Implementation: Replace content of `frontend/src/app/portfolio/holdings/page.tsx` and `frontend/src/app/portfolio/page.tsx` with `redirect("/symbols")`.

### 4.2 TopNav Changes

Current Symbols dropdown:
```
Portfolio      → /portfolio/holdings   ← REMOVE
Watchlist      → /symbols              ← RENAME to "Symbols"
Movements      → /portfolio/movements  ← KEEP
Accounts       → /portfolio/accounts   ← KEEP
Calendar       → /symbols/calendar     ← KEEP
Action Plans   → /plans                ← KEEP
```

New dropdown:
```
Symbols        → /symbols              ← was "Watchlist"
Movements      → /portfolio/movements
Accounts       → /portfolio/accounts
Calendar       → /symbols/calendar
Action Plans   → /plans
```

**Menu order preserved** minus the removed "Portfolio" entry.

### 4.3 BFF / Backend Backward Compatibility

- `GET /api/portfolio/holdings` backend route: **KEEP**. It is used by Symbol Details `PortfolioHoldingsCard` and is the authoritative holdings API. No change.
- `GET /api/symbols/overview` backend route: **MODIFY** per §2 to include `portfolio_summary` and extended per-row fields.
- Frontend BFF route `GET /api/symbols/overview` (Next.js proxy): No change needed (pass-through).

### 4.4 Deep Links

Any external links (bookmarks, Telegram notifications) pointing to `/portfolio/holdings` get the 308 redirect to `/symbols`. The symbol search on `/symbols` allows finding any symbol. Individual symbol detail links (`/symbols/AAPL`) are unaffected.

---

## 5. Watchlist Filters / Search / Sort / Zero-Toggle After Unification

### 5.1 Search

Existing search bar from `SymbolsTable` is preserved: filters by symbol ticker, category, and entry_tag. Extended to also match `display_name` (company name).

### 5.2 Zero-Share Toggle

Replaces the old two-checkbox system (portfolio section had its own, watchlist did not).

**New single toggle:** "Show historical (0 shares)" — default: OFF.  
When OFF: hides rows where `portfolio_shares == "0"` AND `is_auto_enrolled == true` AND no explicit watchlist membership.  
When ON: shows all rows.

### 5.3 Suitability Filters

Existing suitability filter pills (All, Ideal Puts, Ideal Calls, No Puts, No Calls) are preserved unchanged.

### 5.4 Sort

All existing sort columns preserved. New sort columns: `portfolio_shares`, `portfolio_dividends_eur`. Default sort: `dgi_score` descending (unchanged).

### 5.5 Combined Filter Result Count

Below search bar: "Showing X of Y symbols" where Y = total visible (after zero-toggle), X = after search + suitability filter.

---

## 6. Feature Migration from Portfolio Holdings

### 6.1 Features Moving to Symbols Page

| Feature | Current location | New location | Notes |
|---------|-----------------|--------------|-------|
| Summary KPIs (Inversión, Resultado, Dividendos) | `PortfolioHoldingsTable` | `/symbols` page Row 2 | Simplified: 3 primary KPIs only, no desglose row |
| Per-symbol dividends column | `PortfolioHoldingsTable` | New `Dividends (€)` column in unified table | §7 |
| Incomplete cost basis warning | `PortfolioHoldingsTable` | Row 2 warning badge | Same behavior |
| Search/filter | `PortfolioHoldingsTable` | Merged into `SymbolsTable` search | Already exists |
| Hide zero-share toggle | `PortfolioHoldingsTable` | Unified zero-toggle (§5.2) | Semantics refined |
| Refresh button | `PortfolioHoldingsTable` | Not needed (server component, full page refresh) | SSR handles freshness |

### 6.2 Features Remaining in Movements / Accounts

| Feature | Stays at |
|---------|----------|
| Batch reassignment | `/portfolio/movements` page (already accessible via Movements) |
| Account filter | `/portfolio/movements` page — filter movements by account |
| Per-account holdings breakdown | Symbol Detail `PortfolioHoldingsCard` (per-symbol view) |
| Import CSV | `/portfolio/import` (unchanged) |
| Account management (CRUD) | `/portfolio/accounts` (unchanged) |
| Transfer creation | `/portfolio/movements` manual movement dialog |
| Correction/void | `/portfolio/movements` → movement detail dialog |

### 6.3 Batch Reassignment Access

**Current:** "Batch reassign" button on `/portfolio/holdings`.  
**New:** Move "Batch reassign" button to `/portfolio/movements` page toolbar, next to the date range filter. This is the natural home — reassignment operates on movements, not on holdings rows.

### 6.4 No Features Silently Lost

| Feature from Holdings | Disposition | Verification |
|-----------------------|-------------|--------------|
| Summary KPIs | ✅ Moved to Symbols Row 2 | §3 |
| Holdings table | ✅ Merged into unified Symbols table | §2 |
| Account filter on holdings | ✅ Available on Movements page | §6.2 |
| Batch reassign | ✅ Moved to Movements toolbar | §6.3 |
| Per-holding warnings | ✅ Symbol Detail `PortfolioHoldingsCard` shows warnings | Existing |
| Search | ✅ Unified search | §5.1 |
| Zero toggle | ✅ Unified toggle | §5.2 |
| Refresh | ✅ SSR refresh / browser refresh | §6.1 |

---

## 7. Dividend Column Semantics

### 7.1 Definition

**Column: "Dividends (€)"**

```
portfolio_dividends_eur(symbol) = Σ net_eur for all DIVIDEND movements
                                   where security_id matches symbol
                                   AND deleted_at IS NULL
                                   AND correction_status != "SUPERSEDED"
```

**Semantics:** Total net dividends (after withholding taxes) ever received for this symbol across all accounts.

### 7.2 Formatting

| Value | Display |
|-------|---------|
| `null` (watchlist-only, no portfolio) | "—" |
| `"0.00"` (portfolio symbol, no dividends received) | "€0.00" in muted text |
| Positive value | "€1,245.80" in `accent-green` |
| Negative value (correction edge case) | "−€50.00" in `accent-red` |

Number formatting: `Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" })` — same as existing PortfolioHoldingsTable.

### 7.3 Column Position

After "Invested (€)" in the portfolio columns, before "In Calls":

```
Symbol | Category | DGI | Tech | Entry | Momentum | Price | Shares | Avg Cost | Invested | Dividends | In Calls | Puts $
```

For watchlist-only rows: Shares, Avg Cost, Invested, Dividends all show "—".

---

## 8. Implementation Plan

### 8.1 Backend Changes

| # | File | Change | Risk |
|---|------|--------|------|
| B1 | `backend/web/app.py` — `_compute_symbols_overview()` | Remove two-section split. Add `portfolio_summary` object with aggregated `remaining_cost_basis_eur`, `realized_result_eur`, `total_dividends_eur`. Add `portfolio_dividends_eur`, `portfolio_realized_eur`, `row_source`, `is_auto_enrolled` per row. Compute `is_watchlist_member` per §1.2. | Low — additive; existing fields preserved |
| B2 | `backend/web/app.py` — `_compute_symbols_overview()` | Remove `portfolio_rows`, `watchlist_rows`, `portfolio_count`, `watchlist_count` from response | Medium — frontend must update simultaneously |
| B3 | `backend/tests/test_symbols_overview_sections.py` | Update tests: no two-section assertions, add unified-row and portfolio_summary assertions | Low |

### 8.2 Frontend Changes

| # | File | Change | Risk |
|---|------|--------|------|
| F1 | `frontend/src/types/symbols.ts` | Update `SymbolRow`: add `portfolio_dividends_eur`, `portfolio_realized_eur`, `row_source`, `is_auto_enrolled`. Update `SymbolsOverview`: remove `portfolio_rows`/`watchlist_rows`/`portfolio_count`/`watchlist_count`, add `portfolio_summary`. | Low |
| F2 | `frontend/src/app/symbols/page.tsx` | Remove `SymbolsSectionedClient` usage. Render single `SymbolsTable` with all rows. Add Row 1 (options exposure — already exists) and Row 2 (`portfolio_summary` KPI cards). | Medium |
| F3 | `frontend/src/components/SymbolsSectionedClient.tsx` | **DELETE** file — no longer needed | Low |
| F4 | `frontend/src/components/SymbolsTable.tsx` | Unify columns: always show portfolio columns (Shares, Avg Cost, Invested, Dividends). Add `portfolio_dividends_eur` column. Integrate unified zero-toggle (§5.2). Remove `listSection` prop — always unified. | Medium |
| F5 | `frontend/src/components/TopNav.tsx` | Remove `{ href: "/portfolio/holdings", label: "Portfolio", icon: LayoutList }` from `DROPDOWNS.Symbols`. Rename "Watchlist" label to "Symbols". | Low |
| F6 | `frontend/src/app/portfolio/holdings/page.tsx` | Replace with `redirect("/symbols")` | Low |
| F7 | `frontend/src/app/portfolio/page.tsx` | Replace with `redirect("/symbols")` | Low |
| F8 | `frontend/src/components/PortfolioHoldingsTable.tsx` | **KEEP** file — still used if standalone access is needed; but primary usage removed. Consider deleting after migration confirmed. | Low |

### 8.3 Migration Impact

**Database migration: NONE required.**  
- No schema changes to Cosmos documents.
- No new containers.
- No field renames in stored data.
- `symbol_config._auto_enrolled` field already exists from Symbol Unification rev 3.

### 8.4 Rollout

1. **Backend + Frontend in single PR** — response shape change and frontend consumption must be atomic.
2. **Feature flag: NONE** — this is a UI consolidation, not a data change. Redirect ensures no broken links.
3. **Test: Full backend test suite + frontend TypeScript check + manual verification of redirects.**

---

## 9. Reconciliation with In-Progress Work

### 9.1 Account Labels/Colors (`copilot-directive-20260906-account-display-name.md`, `copilot-directive-20260906-account-colors.md`)

**No conflict.** Account labels and colors apply to:
- Movements table (unchanged location)
- Account badges in Symbol Detail `PortfolioHoldingsCard` (unchanged)
- Account filter dropdowns (unchanged location)

The unified Symbols table does NOT show account columns (too wide). Per-account breakdown remains in Symbol Detail. These directives proceed independently.

### 9.2 Movements Date Default (`copilot-directive-20260906-movements-default-three-months.md`)

**No conflict.** Movements page remains at `/portfolio/movements` with its own date range logic. Independent work.

### 9.3 Full Correction Contract (`danny-zero-filter-full-correction-contract.md`)

**No conflict.** Correction logic operates on movements and holdings computation. The unified Symbols table consumes holdings_service output, which already reflects corrections.

### 9.4 Cost Basis CMP Contract (`danny-portfolio-summary-cost-basis.md`)

**Dependency (already implemented).** The unified Symbols page Row 2 consumes `remaining_cost_basis_eur` and `realized_result_eur` from `holdings_service.compute_holdings()["summary"]`, which already uses CMP (media ponderada móvil) method. No additional computation needed.

---

## 10. Acceptance Criteria

### Functional (F)

| # | Criterion |
|---|-----------|
| F-1 | `/symbols` renders a single unified table with all symbols (portfolio + watchlist) |
| F-2 | Portfolio symbols show Shares, Avg Cost (€), Invested (€), Dividends (€) columns with data |
| F-3 | Watchlist-only symbols show "—" in Shares, Avg Cost, Invested, Dividends columns |
| F-4 | Auto-enrolled symbols with exactly 0 shares are hidden by default |
| F-5 | Manually watchlisted symbols with 0 shares remain visible (explicit membership) |
| F-6 | Negative-share symbols are always visible |
| F-7 | "Show historical (0 shares)" toggle reveals hidden auto-enrolled zero-share symbols |
| F-8 | Row 1 shows Calls Exposure and Puts Committed (portfolio-wide, unfiltered) |
| F-9 | Row 2 shows Inversión actual, Resultado realizado, Dividendos netos (portfolio-wide, unfiltered) |
| F-10 | Row 2 shows incomplete cost basis warning when applicable |
| F-11 | Row 2 hidden when no portfolio holdings exist |
| F-12 | Search filters by symbol, display_name, category, entry_tag |
| F-13 | Sort works on all columns including new portfolio columns |

### Navigation (N)

| # | Criterion |
|---|-----------|
| N-1 | `/portfolio/holdings` redirects to `/symbols` (HTTP 308) |
| N-2 | `/portfolio` redirects to `/symbols` (HTTP 308) |
| N-3 | TopNav Symbols dropdown: "Portfolio" entry removed, "Watchlist" renamed to "Symbols" |
| N-4 | Movements, Accounts, Calendar, Action Plans links unchanged |

### Data Integrity (D)

| # | Criterion |
|---|-----------|
| D-1 | `portfolio_summary.remaining_cost_basis_eur` matches `holdings_service.compute_holdings()["summary"]["remaining_cost_basis_eur"]` |
| D-2 | `portfolio_summary.realized_result_eur` matches holdings summary `realized_result_eur` |
| D-3 | `portfolio_summary.total_dividends_eur` matches holdings summary `total_dividends_eur` |
| D-4 | Per-row `portfolio_dividends_eur` matches per-holding `total_dividends_eur` from holdings_service |
| D-5 | Summary totals are unaffected by client-side filters |

### Feature Preservation (P)

| # | Criterion |
|---|-----------|
| P-1 | Batch reassignment accessible from Movements page |
| P-2 | Account filtering available on Movements page |
| P-3 | Import CSV accessible via `/portfolio/import` |
| P-4 | Symbol Detail `PortfolioHoldingsCard` unchanged |
| P-5 | All existing `/symbols/[symbol]` detail routes work |
| P-6 | `GET /api/portfolio/holdings` backend API unchanged |

### Non-Regression (NR)

| # | Criterion |
|---|-----------|
| NR-1 | All backend tests pass (421+ tests) |
| NR-2 | Frontend TypeScript compiles cleanly (`tsc --noEmit`) |
| NR-3 | TopNav symbol search autocomplete still works (uses `/api/symbols/overview` rows) |
| NR-4 | Dashboard and Economics pages unaffected |

---

## 11. Exact Formulas Reference

```
# ─── Per-symbol (row) ───────────────────────────────────────

portfolio_shares(s)       = holdings_service.holding[s].total_shares
                            (null if no portfolio history)

portfolio_avg_cost_eur(s) = holdings_service.holding[s].avg_cost_basis_eur
                            (null if no portfolio history or no lots)

portfolio_invested_eur(s) = holdings_service.holding[s].remaining_cost_basis_eur
                            (base de coste CMP lotes restantes)

portfolio_dividends_eur(s)= holdings_service.holding[s].total_dividends_eur
                            (Σ net_eur DIVIDEND movements)

portfolio_realized_eur(s) = holdings_service.holding[s].realized_result_eur
                            (sale_proceeds − cost_sold CMP)

# ─── Portfolio-wide (summary) ───────────────────────────────

Total Investment     = Σ remaining_cost_basis_eur  (all holdings)
Net Gains            = Σ (total_sale_proceeds_eur − cost_basis_sold_eur)  (all holdings)
                     = Ganancia cerrada. NO incluye unrealized ni option P&L.
Total Dividends      = Σ net_eur  (all DIVIDEND movements, active, non-superseded)

Calls Exposure       = Σ (strike × 100)  for active call positions
Puts Committed       = Σ (strike × 100)  for active put positions

# ─── Visibility ─────────────────────────────────────────────

VISIBLE_DEFAULT(s) = shares(s) != 0
                   OR is_watchlist_member(s)  # manual add OR any toggle ON
                   OR NOT has_ledger_history(s) AND has_config(s)  # pure watchlist

HIDDEN_DEFAULT(s)  = shares(s) == 0
                   AND is_auto_enrolled(s)
                   AND NOT is_watchlist_member(s)
```

---

## Appendix A: Files to Modify

```
# ── Unified Watchlist (§1–§10) ──
MODIFY  backend/web/app.py                          # _compute_symbols_overview + _compute_symbol_detail + 24 endpoint guards
MODIFY  backend/tests/test_symbols_overview_sections.py
MODIFY  frontend/src/types/symbols.ts
MODIFY  frontend/src/app/symbols/page.tsx
DELETE  frontend/src/components/SymbolsSectionedClient.tsx
MODIFY  frontend/src/components/SymbolsTable.tsx
MODIFY  frontend/src/components/TopNav.tsx
MODIFY  frontend/src/app/portfolio/holdings/page.tsx  # → redirect
MODIFY  frontend/src/app/portfolio/page.tsx           # → redirect

# ── Amendment J: US-Only Eligibility ──
CREATE  backend/src/us_exchange_eligibility.py       # Shared predicate + enforcement helper
CREATE  frontend/src/lib/us-options-eligible.ts      # Frontend mirror of predicate
MODIFY  frontend/src/types/symbol-detail.ts          # Add us_options_eligible
MODIFY  frontend/src/app/symbols/[symbol]/page.tsx   # Conditional SymbolActions + Options
CREATE  backend/tests/test_us_options_eligibility.py # Predicate + enforcement tests
```

**New files: 3 (2 backend, 1 frontend). No database migration. No new containers.**

---

## Amendment J — US-Only Symbol Actions Eligibility

**Directive:** `copilot-directive-20260906-us-only-symbol-actions.md`  
**Date:** 2026-09-06  
**Author:** Danny (Lead Architect)

---

### J.1 Eligibility Predicate — Single Source of Truth

A symbol is "US-options-eligible" when its exchange MIC matches a US options-capable exchange.

#### J.1.1 Shared Helper (Backend)

```python
# backend/src/us_exchange_eligibility.py  (NEW file — single source of truth)

US_OPTIONS_ELIGIBLE_MICS: frozenset[str] = frozenset({"XNYS", "XNAS"})

def is_us_options_eligible(exchange_mic: str | None) -> bool:
    """Return True if the exchange MIC supports US-listed options analysis.

    Used by BOTH the symbol detail API (to expose the flag) and all
    action endpoints (to enforce eligibility).  Keeping the predicate
    in ONE file prevents drift between UI-hiding and backend enforcement.
    """
    if not exchange_mic:
        return False
    return exchange_mic.strip().upper() in US_OPTIONS_ELIGIBLE_MICS
```

#### J.1.2 Shared Helper (Frontend)

```typescript
// frontend/src/lib/us-options-eligible.ts  (NEW file — mirrors backend)

export const US_OPTIONS_ELIGIBLE_MICS: ReadonlySet<string> = new Set(["XNYS", "XNAS"]);

export function isUsOptionsEligible(exchangeMic: string | null | undefined): boolean {
  if (!exchangeMic) return false;
  return US_OPTIONS_ELIGIBLE_MICS.has(exchangeMic.trim().toUpperCase());
}
```

**Invariant:** Both files define the identical set `{XNYS, XNAS}`. A future extension (e.g., BATS/XCBO) requires updating both files simultaneously — the set is small and explicit to prevent silent divergence.

#### J.1.3 MIC Resolution Order

For a given symbol, resolve `exchange_mic` using this precedence:

1. `security_master.exchange_mic` (canonical, from security catalog) — available in Symbol Detail as `d.security.exchange_mic`.
2. `symbol_config.exchange` (set by `ensure_symbol_config` from security_master, or by user on manual creation) — available as `d.exchange`.
3. If neither is set → **not eligible** (fail-closed).

---

### J.2 Symbol Detail API — Expose Eligibility Flag

Extend the `GET /api/symbols/{symbol}/detail` response with:

```json
{
  "us_options_eligible": true   // NEW boolean field
}
```

Computed by `_compute_symbol_detail()`:

```python
# In _compute_symbol_detail, after resolving security_field and clean["exchange"]:
from src.us_exchange_eligibility import is_us_options_eligible

# Prefer security_master.exchange_mic; fall back to symbol_config.exchange
effective_mic = (
    security_field.get("exchange_mic") if security_field else None
) or clean.get("exchange") or ""

us_options_eligible = is_us_options_eligible(effective_mic)

# Add to response dict:
# "us_options_eligible": us_options_eligible,
```

This field is included in **both** the full-config path and the `portfolio_only` minimal response.

---

### J.3 Frontend — UI Hiding Rules

#### J.3.1 SymbolDetail Page (`frontend/src/app/symbols/[symbol]/page.tsx`)

Read the new flag from the detail response:

```typescript
const eligible = d.us_options_eligible ?? false;
```

| Component / Section | Visible when `eligible = false`? | Notes |
|---------------------|----------------------------------|-------|
| **SymbolActions** (Analyze, CC, CSP, Buy, Alerts, Pause/Resume) | ❌ Hidden entirely | Do not render `<SymbolActions>` |
| **Options section** (PositionsTable, AddPositionForm, RecentActivities) | ❌ Hidden entirely | Do not render the `<DetailSection title="Options">` block |
| **TradingView chart** | ✅ Visible | Market data is exchange-agnostic |
| **RtChart** | ✅ Visible | Same |
| **Summary section** | ✅ Visible | DGI, technicals, enrichment |
| **Stocks section** (PortfolioHoldingsCard, StockTransactionsTable) | ✅ Visible | Ledger data is exchange-agnostic |
| **Plans section** | ✅ Visible | Plans may be non-option (buy_shares, sell_shares) |
| **Security identity badge** | ✅ Visible | Canonical ID, ISIN, currency |
| **Membership badge** | ✅ Visible | Watchlist/Portfolio state |

Implementation — wrap the two blocks:

```tsx
{/* Toolbar: actions — US-listed only */}
{eligible && (
  <div className="flex flex-wrap items-center justify-end gap-4">
    <SymbolActions ... />
  </div>
)}

{/* Options section — US-listed only */}
{eligible && hasOptions && (
  <DetailSection title="Options">
    ...
  </DetailSection>
)}
```

#### J.3.2 SymbolsTable — Unified Table

The "In Calls" and "Puts $" columns remain visible (they show aggregate data). Rows for non-US symbols will naturally show 0 / $0 since no positions exist. No column hiding needed.

#### J.3.3 Sub-pages (best-options, options-chain, chat, report, technical-analysis, forecasts)

These pages are accessed via `/symbols/{symbol}/...` URLs. For now, the Analyze dropdown is hidden for non-eligible symbols, which removes the primary navigation path. As defense-in-depth:

- Each sub-page should check eligibility server-side via the symbol detail API and display an "Options features are not available for non-US exchanges" message if `us_options_eligible === false`.
- **Phase 1:** Rely on UI hiding (Analyze dropdown removed). Backend enforcement (§J.4) prevents direct API calls.
- **Phase 2 (optional):** Add sub-page client guards if direct URL access becomes a concern.

---

### J.4 Backend — Endpoint Enforcement

Backend action endpoints **MUST NOT** rely solely on UI hiding. Each must independently check eligibility and return HTTP 403 with an explicit error.

#### J.4.1 Enforcement Helper

```python
# backend/src/us_exchange_eligibility.py (same file as §J.1.1)

from fastapi.responses import JSONResponse

def enforce_us_options_eligible(
    doc: dict,
    security_field: dict | None = None,
) -> JSONResponse | None:
    """Return a 403 JSONResponse if the symbol is not US-options-eligible.

    Returns None if eligible (caller proceeds normally).

    Args:
        doc: symbol_config document (has 'exchange' field)
        security_field: optional security_master info (has 'exchange_mic')
    """
    effective_mic = ""
    if security_field and security_field.get("exchange_mic"):
        effective_mic = security_field["exchange_mic"]
    elif doc and doc.get("exchange"):
        effective_mic = doc["exchange"]
    elif doc and doc.get("security_id") and ":" in doc["security_id"]:
        effective_mic = doc["security_id"].split(":")[0]

    if not is_us_options_eligible(effective_mic):
        return JSONResponse(
            {
                "error": "options_not_eligible",
                "detail": (
                    f"Options features are available only for US-listed securities "
                    f"(NYSE/NASDAQ). This symbol's exchange ({effective_mic or 'unknown'}) "
                    f"is not eligible."
                ),
            },
            status_code=403,
        )
    return None  # eligible — proceed
```

#### J.4.2 Endpoints to Guard

Every endpoint below must call `enforce_us_options_eligible()` early and return 403 if non-eligible:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/symbols/{symbol}/positions` | POST | Create position |
| `/api/symbols/{symbol}/positions/from-activity/{activity_id}` | POST | Position from activity |
| `/api/symbols/{symbol}/positions/roll-from-activity/{activity_id}` | POST | Roll from activity |
| `/api/symbols/{symbol}/positions/{position_id}/roll` | POST | Roll position |
| `/api/symbols/{symbol}/positions/{position_id}/close` | PUT | Close position |
| `/api/symbols/{symbol}/positions/{position_id}/notes` | PATCH | Update notes |
| `/api/symbols/{symbol}/positions/{position_id}/premium` | PATCH | Update premium |
| `/api/symbols/{symbol}/positions/{position_id}/buyback_cost` | PATCH | Update buyback |
| `/api/symbols/{symbol}/positions/{position_id}` | DELETE | Delete position |
| `/api/symbols/{symbol}/positions/{position_id}/dps-analysis` | POST | DPS analysis |
| `/api/symbols/{symbol}/positions/{position_id}/dps-insights` | POST | DPS insights |
| `/api/symbols/{symbol}/positions/{position_id}/roll-table` | GET | Roll table |
| `/api/symbols/{symbol}/positions/{position_id}/snapshots` | GET | Position snapshots |
| `/api/symbols/{symbol}/best-options` | GET | Best options screen |
| `/api/symbols/{symbol}/best-options/refresh` | POST | Refresh best options |
| `/api/symbols/{symbol}/options-chain` | GET | Options chain |
| `/api/symbols/{symbol}/report` | POST | Generate report |
| `/api/symbols/{symbol}/chat/context` | POST | Chat context |
| `/api/symbols/{symbol}/chat` | POST | Chat conversation |
| `/api/symbols/{symbol}/forecasts` | GET | Forecasts list |
| `/api/symbols/{symbol}/forecasts/{forecast_id}` | GET | Single forecast |
| `/api/symbols/{symbol}/pause` | POST | Pause tracking |
| `/api/symbols/{symbol}/pause` | DELETE | Resume tracking |

**Watchlist toggle update** (`PUT /api/symbols/{symbol}` with `covered_call`, `cash_secured_put`, `buy_tracker`, `telegram_notifications_enabled`):
- Guard applies only when the body contains an option-related toggle key (`covered_call`, `cash_secured_put`, `buy_tracker`).
- `display_name`, `exchange`, `total_shares` updates remain allowed for all MICs.
- `telegram_notifications_enabled` is guarded (alerts are option-activity-related).

#### J.4.3 Implementation Pattern

Each guarded endpoint adds 3 lines near the top:

```python
async def api_add_position(request: Request, symbol: str):
    try:
        cosmos = _get_cosmos(request)
        doc = cosmos.get_symbol(symbol.upper())
        if not doc:
            return JSONResponse({"error": f"Symbol {symbol} not found"}, status_code=404)

        # ── US-options eligibility guard ──
        from src.us_exchange_eligibility import enforce_us_options_eligible
        guard = enforce_us_options_eligible(doc)
        if guard:
            return guard

        # ... existing logic continues unchanged ...
```

For `api_update_symbol`, the guard is conditional:

```python
# Guard only option-related toggle updates
OPTION_TOGGLE_KEYS = {"covered_call", "cash_secured_put", "buy_tracker", "telegram_notifications_enabled"}
if any(k in body for k in OPTION_TOGGLE_KEYS):
    from src.us_exchange_eligibility import enforce_us_options_eligible
    guard = enforce_us_options_eligible(doc)
    if guard:
        return guard
```

---

### J.5 Unified Table — Options Exposure Columns

The existing "In Calls" and "Puts $" columns in the unified Symbols table (§7.3) continue to show values for all symbols. Non-US symbols will naturally have 0/0 since no positions can be created. **No column hiding** — the columns provide a consistent layout and the zero values are accurate.

---

### J.6 Symbol Detail Response — Type Update

```typescript
// frontend/src/types/symbol-detail.ts — add to SymbolDetail interface:
export interface SymbolDetail {
  // ... existing fields ...
  us_options_eligible?: boolean;  // NEW: true for XNYS/XNAS, false otherwise
}
```

---

### J.7 Acceptance Criteria

#### Functional (J-F)

| # | Criterion |
|---|-----------|
| J-F1 | Symbol Detail for XNYS-listed symbol shows Analyze dropdown, CC/CSP/Buy/Alerts toggles, Pause/Resume, and Options section |
| J-F2 | Symbol Detail for XNAS-listed symbol shows all option controls (same as XNYS) |
| J-F3 | Symbol Detail for XMAD-listed symbol hides Analyze dropdown, all tracking toggles, Pause/Resume, and Options section |
| J-F4 | Symbol Detail for XETR-listed symbol hides all option controls (same as XMAD) |
| J-F5 | Symbol Detail for symbol with no exchange_mic (null/empty) hides all option controls (fail-closed) |
| J-F6 | Summary section visible for all MICs |
| J-F7 | Stocks section (PortfolioHoldingsCard, StockTransactionsTable) visible for all MICs |
| J-F8 | Plans section visible for all MICs |
| J-F9 | TradingView chart and RT chart visible for all MICs |
| J-F10 | `us_options_eligible` field present in detail API response for all symbols |

#### Backend Enforcement (J-BE)

| # | Criterion |
|---|-----------|
| J-BE1 | `POST /api/symbols/{XMAD_SYMBOL}/positions` returns 403 with `error: "options_not_eligible"` |
| J-BE2 | `POST /api/symbols/{XNYS_SYMBOL}/positions` succeeds (200/201) when valid |
| J-BE3 | `GET /api/symbols/{XMAD_SYMBOL}/best-options` returns 403 |
| J-BE4 | `GET /api/symbols/{XMAD_SYMBOL}/options-chain` returns 403 |
| J-BE5 | `POST /api/symbols/{XMAD_SYMBOL}/report` returns 403 |
| J-BE6 | `POST /api/symbols/{XMAD_SYMBOL}/chat` returns 403 |
| J-BE7 | `POST /api/symbols/{XMAD_SYMBOL}/pause` returns 403 |
| J-BE8 | `PUT /api/symbols/{XMAD_SYMBOL}` with `{"covered_call": true}` returns 403 |
| J-BE9 | `PUT /api/symbols/{XMAD_SYMBOL}` with `{"display_name": "Foo"}` succeeds (non-option field allowed) |
| J-BE10 | `PUT /api/symbols/{XMAD_SYMBOL}` with `{"total_shares": 100}` succeeds |
| J-BE11 | All 24 guarded endpoints (§J.4.2 + conditional PUT) return 403 for non-eligible MICs |
| J-BE12 | `enforce_us_options_eligible` extracts MIC from security_id fallback when exchange field is empty |

#### Shared Predicate (J-SP)

| # | Criterion |
|---|-----------|
| J-SP1 | `is_us_options_eligible("XNYS")` returns `True` |
| J-SP2 | `is_us_options_eligible("XNAS")` returns `True` |
| J-SP3 | `is_us_options_eligible("XMAD")` returns `False` |
| J-SP4 | `is_us_options_eligible("")` returns `False` |
| J-SP5 | `is_us_options_eligible(None)` returns `False` |
| J-SP6 | Frontend `isUsOptionsEligible` returns identical results for the same 5 inputs |
| J-SP7 | Both files define exactly `{XNYS, XNAS}` — no drift |

---

### J.8 Files to Modify / Create

```
CREATE  backend/src/us_exchange_eligibility.py       # Shared predicate + enforcement helper
CREATE  frontend/src/lib/us-options-eligible.ts      # Frontend mirror of predicate
MODIFY  backend/web/app.py                           # _compute_symbol_detail: add us_options_eligible
                                                     # 24 endpoints: add enforce guard
MODIFY  frontend/src/types/symbol-detail.ts          # Add us_options_eligible to SymbolDetail
MODIFY  frontend/src/app/symbols/[symbol]/page.tsx   # Conditional render of SymbolActions + Options
CREATE  backend/tests/test_us_options_eligibility.py # Unit tests for predicate + enforcement
```

### J.9 Interaction with Unified Watchlist (§1–§10)

- **No conflict** with unified table (§2–§5). The eligibility flag is a Symbol Detail concern, not a list-page concern.
- **Options exposure summary** (Row 1 in unified page) remains unchanged — it aggregates positions which can only exist for eligible symbols.
- **Suitability filters** (Ideal Puts, Ideal Calls, etc.) in the Symbols table remain — they filter by enrichment data which is available for all symbols. Non-US symbols may show "—" for entry_tag/momentum (enrichment may not run), which is correct.
- **`row_source`** (§2.2) is unrelated to eligibility — a non-US symbol can be `"both"` (portfolio + watchlist) and still be non-eligible for options.
