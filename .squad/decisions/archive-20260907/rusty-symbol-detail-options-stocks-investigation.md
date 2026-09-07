# Investigation: Symbol Detail — Options/Stocks Visibility Gap

**Date:** 2026-09-06  
**Author:** Rusty (frontend owner)  
**Status:** INVESTIGATION COMPLETE — implementation deferred until Danny's amended contract lands  
**Scope:** `symbols/[symbol]/page.tsx`, `SymbolMovementsTable.tsx`, `web/app.py (_compute_symbol_detail)`, `cosmos_portfolio.py (get_movements)`

---

## Requirement

Symbol Details must expose two clear sections/tabs — **Options** and **Stocks**:
- **Options**: existing positions behaviour (PositionsTable + AddPositionForm)
- **Stocks**: BUY/SELL/DIVIDEND history for the current security with date, type, quantity, and relevant financial fields

---

## Root Causes Found

### RC-1 — Holdings guard silences movements (Backend + Frontend)

`_compute_symbol_detail` only builds `portfolio_field` when `holding != None` (the security appears in `compute_holdings()`).

```python
# web/app.py ~L1211–1235
if holding:          # ← movements never fetched if holding is None
    movs, _ = holdings_svc.portfolio_svc.get_movements(
        security_id=security_id_from_config, limit=5
    )
```

If the security_id in the ledger doesn't exactly match `security_id_from_config` (e.g., symbol added to watchlist before security master was linked), `holding` is `None` → `portfolio_field = None` → `hasPortfolio = false` in frontend → `SymbolMovementsTable` never renders.

**Fix direction:** Frontend `SymbolStocksTab` should call `GET /api/portfolio/movements?security_id=X` directly instead of depending on `d.portfolio!.recent_movements`. This decouples movement display from the holdings computation path.

---

### RC-2 — `movement_count` capped at 5, total discarded (Backend bug)

Both code paths in `_compute_symbol_detail` use `movs, _ = get_movements(..., limit=5)` and set:

```python
"movement_count": len(recent_movs),  # ← always ≤ 5; total count discarded via _
```

The `get_movements` function returns `(items, total_count)` but total is thrown away (`_`). The "showing X of Y" UI text is therefore never accurate beyond 5.

**Fix direction (backend, Livingston):**
```python
movs, total_count = holdings_svc.portfolio_svc.get_movements(
    security_id=security_id, limit=5
)
"movement_count": total_count,   # ← use actual total
```
Or, for the new tab, the frontend calls the movements endpoint directly and handles pagination itself — making `movement_count` in the detail payload moot.

---

### RC-3 — No txn_type filter: TRANSFER appears in Recent Movements (Backend)

`get_movements` is called with no `txn_type` filter, so `TRANSFER_OUT` and `TRANSFER_IN` appear in `recent_movements`. The new Stocks tab should only display BUY/SELL/DIVIDEND.

**Fix direction:** Frontend component applies a client-side inclusion filter `["BUY", "SELL", "DIVIDEND"]` on rendered rows, OR passes `txn_type` per call (but single-type only — API doesn't currently support multi-value txn_type filter). Client-side display filter is the pragmatic approach.

---

### RC-4 — No Options/Stocks tab structure (Frontend)

The current page renders `PositionsTable` (options) and `SymbolMovementsTable` (movements) as flat, independent sections buried in a long scroll. There is no tab switcher. The movements card is positioned after enrichment summary, portfolio card, positions table, add-position form, plans, and activities — low visual prominence.

---

### RC-5 — `SymbolMovementsTable` shows max 5 rows, no inline pagination (Frontend)

The component always receives max 5 rows (from `recent_movements`). It has a "View all →" link to `/portfolio/movements?security_id=X` but no inline pagination or "Load more". The new Stocks tab needs a first-class paginated view.

---

### RC-6 — `watchlist_only` symbols show Options but not Stocks (Frontend conditional)

`PositionsTable` is rendered when `hasAgentContent || positions.length > 0` (`hasAgentContent` is true for all watchlist symbols). `SymbolMovementsTable` requires `hasPortfolio` which is false for `watchlist_only`. This is correct semantically (watchlist-only symbols have no ledger movements) but creates an asymmetry in the UI that reinforces the invisibility of the Stocks tab concept.

---

## Implementation Plan (deferred)

### Frontend — after contract amendment lands

**File: `frontend/src/app/symbols/[symbol]/page.tsx`**

1. Add tab state: `const [tab, setTab] = useState<"options" | "stocks">("options")`.
2. Render tab switcher (two buttons, same pattern as other tab UIs in the app).
3. `"options"` tab: existing `PositionsTable` + `AddPositionForm` — no changes.
4. `"stocks"` tab: render new `SymbolStocksTab` component.

**File: `frontend/src/components/SymbolStocksTab.tsx` (new)**

- Client component; takes `securityId: string | null`.
- If `securityId` is null: info notice "Symbol not linked to security master — cannot load stock movements."
- Calls `getMovements({ security_id: securityId, limit: 20, offset })` from `portfolio-api`.
- Renders rows for `txn_type` in `["BUY", "SELL", "DIVIDEND"]` only (exclude TRANSFER client-side).
- Columns: Date · Type badge · Qty · Gross (EUR) · Fees (EUR) · Net (EUR).
- Row click opens `MovementDetailDialog`.
- "Load more" pagination (offset-based, same pattern as `PortfolioMovementsTable`).
- Empty state: "No stock movements recorded for this security."
- Loading skeleton + error banner.

**Preserve:**
- Remove `SymbolMovementsTable` from the flat section (it's superseded by the Stocks tab).
- Keep `PortfolioHoldingsCard` outside the tabs — it's summary data, not movement history.
- Keep `RecentActivities`, `SymbolPlansTable`, `SymbolSummary` outside tabs.

### Backend — after amendment, no route changes needed

- Fix `movement_count` in `_compute_symbol_detail` to use `total_count` from `get_movements` return value (Livingston, non-breaking).
- Optional: add multi-value `txn_type` filter support to `get_movements` to allow server-side exclusion of TRANSFER.

### Tests — after amendment

| # | Test | Type |
|---|---|---|
| T-1 | SymbolStocksTab renders BUY/SELL/DIVIDEND rows | Frontend unit |
| T-2 | SymbolStocksTab excludes TRANSFER_IN/OUT from display | Frontend unit |
| T-3 | SymbolStocksTab shows empty state when no movements | Frontend unit |
| T-4 | SymbolStocksTab loads more when paginating | Frontend integration |
| T-5 | movement_count reflects total, not capped at limit | Backend unit |
| T-6 | Options tab preserves existing PositionsTable behavior | Frontend regression |

---

## What is safe to implement now (independent of amendment)

- Tab switcher shell (Options/Stocks toggle) — no DIVIDEND logic
- `SymbolStocksTab` display component — calls existing API, renders existing data
- `movement_count` backend fix — trivial, unrelated to DIVIDEND

However, per the explicit instruction this PR defers all implementation until the amended contract lands and reconciliation is complete.

---

## Files to be changed at implementation time

| File | Change | Owner |
|---|---|---|
| `frontend/src/app/symbols/[symbol]/page.tsx` | Add tab switcher; nest Options/Stocks | Rusty |
| `frontend/src/components/SymbolStocksTab.tsx` | New component (create) | Rusty |
| `frontend/src/components/SymbolMovementsTable.tsx` | Deprecate (remove from detail page) | Rusty |
| `backend/web/app.py` | Fix `movement_count` (2 lines, ~L1072 and ~L1232) | Livingston |
