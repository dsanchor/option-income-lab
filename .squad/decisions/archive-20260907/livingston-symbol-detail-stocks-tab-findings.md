# Livingston → Rusty: Symbol Detail Options/Stocks — Investigation Findings & Contract

**Date:** 2026-09-06 (updated for Amendments G, H, I)
**Author:** Livingston  
**Audience:** Rusty (UI/Frontend)  
**Status:** Backend COMPLETE — Frontend stacked-sections spec READY for implementation

---

## ⚠️ AMENDMENT I DECISION: Stacked Sections, Not Tabs

Danny's Amendment I (2026-09-06) finalizes the UI architecture as **collapsible stacked sections**
instead of tabs. This supersedes the earlier "tabs" spec written before Amendment I landed.

**Authoritative spec:** Danny's contract §I.2.2–§I.7 (in `danny-zero-filter-full-correction-contract.md`).  
Summary of changes for Rusty:

1. **No tabs** — use two stacked `DetailSection` components (see §I.3.3)
2. **New components:** `DetailSection` (reusable collapsible container), `StockTransactionsTable`
   (replaces `SymbolMovementsTable` inside the Stocks section)
3. **`SymbolMovementsTable` is DEPRECATED** — no longer rendered on the symbol detail page.
   Keep the file for backward compat; it is no longer imported by `page.tsx`.
4. **Data source for `StockTransactionsTable`:** Call the existing movements API directly:
   ```typescript
   GET /api/portfolio/movements?security_id={securityId}&txn_type={filter}&limit=20&offset={page*20}
   ```
   Returns full `LedgerMovement` objects. No backend changes needed.

---

---

## Root Cause Summary

Four distinct bugs prevent BUY/SELL/DIVIDEND operations from being visibly shown in Symbol Details.

### Bug 1 — No tabs (CRITICAL, frontend)

The current `symbols/[symbol]/page.tsx` stacks everything vertically: `PortfolioHoldingsCard` → `SymbolMovementsTable` → `PositionsTable` → plans → activities. There are no `Options` / `Stocks` tabs. The user sees a long scroll with no clear delineation.

**Fix (yours):** Add client-side `Options` / `Stocks` tab switcher. See §UI Spec below.

### Bug 2 — `movement_count` always ≤ 10 (backend, FIXED)

`_compute_symbol_detail` called `get_movements(limit=5)` and set `"movement_count": len(recent_movements)`. Since `len` always equals `limit`, the "showing N of M" badge in `SymbolMovementsTable` never triggered — there was no visible "View all" count to inform the user more movements existed.

**Fix (mine, DONE):** Capture the real `total_count` from `get_movements` return value; `movement_count` now reflects the actual ledger count.  
**Also:** Raised default fetch limit from 5 → 10.

### Bug 3 — Movements gated on `holding is not None` (backend, FIXED)

`recent_movements` was only fetched inside `if holding:` — meaning a security with 0 current shares (fully exited, historical) had `portfolio_field = None`, so `hasPortfolio` was `false` and `SymbolMovementsTable` was never rendered.

**Fix (mine, DONE):** Movements are fetched independently of the holdings result. If there is no active holding but movements exist, a minimal `portfolio_field` with `current_shares: "0"` is constructed. `SymbolMovementsTable` is always rendered when movements exist.

### Bug 4 — `_map_recent_movement` too sparse (backend, FIXED)

The wire shape only exposed `txn_type`, `trade_date`, `quantity`, `gross_eur` — no `fees_eur`, `net_eur`, `currency`, `account_id`, or `sales_type`. A Stocks tab needs Gross + Net + sales sub-type at minimum.

**Fix (mine, DONE):** `_map_recent_movement` now also returns `fees_eur`, `net_eur`, `currency`, `account_id`, `sales_type`, `correction_status`, `import_source`.  
**TypeScript:** `RecentMovement` type extended with these optional fields.  
**Component:** `SymbolMovementsTable` now renders a Gross (EUR) + Net (EUR) two-column amount view, and shows "Rights" / "Shares" sub-label for SELL movements.

---

## What You Need to Build (Frontend)

### UI Spec: Options / Stocks tabs on Symbol Detail page

**File:** `frontend/src/app/symbols/[symbol]/page.tsx`

#### Tab architecture

Add a client-side tab switcher component (e.g. `SymbolDetailTabs`) with two tabs:

## New Layout: Stacked Sections (Amendment I)

**File:** `frontend/src/app/symbols/[symbol]/page.tsx`

### Section order (top to bottom)

```
── Shared Header (always visible) ─────────────────────
1. Security badge
2. Toolbar (SymbolActions)
3. TradingViewSymbolInfo
4. RtChart

── Options Section (collapsible) ──────────────────────
5. <DetailSection title="Options" defaultOpen>
   5a. SymbolSummary
   5b. PositionsTable
   5c. AddPositionForm
   5d. RecentActivities
   </DetailSection>

── Stocks Section (collapsible) ───────────────────────
6. <DetailSection title="Stocks" defaultOpen>
   6a. PortfolioHoldingsCard
   6b. StockTransactionsTable   ← NEW component
   </DetailSection>

── Plans (always visible) ─────────────────────────────
7. SymbolPlansTable
```

### Visibility rules (Amendment I §I.3.2)

| Section | Show when |
|---|---|
| Options | `hasAgentContent` OR `positions.length > 0` OR `activities.length > 0` |
| Stocks | `d.portfolio != null` |
| Plans | Always |

### New component: `DetailSection`

```tsx
// frontend/src/components/DetailSection.tsx
interface DetailSectionProps {
  title: string;
  badge?: string;
  defaultOpen?: boolean;   // default true
  children: React.ReactNode;
}
```
Full-width card with rounded border, section title, collapse chevron.
When collapsed: only header visible.

### New component: `StockTransactionsTable`

```tsx
// frontend/src/components/StockTransactionsTable.tsx
// Replaces SymbolMovementsTable inside Stocks section.
```

**Columns:**

| Column | Notes |
|---|---|
| Date | `trade_date` |
| Type | Badge (Buy/Sell/Dividend). SELL shows `SALES_TYPE_LABELS[sales_type]` sub-label |
| Qty | `quantity` or "—" for DIVIDEND |
| Gross (EUR) | `gross.eur_amount` |
| Fees (EUR) | `fees.total_eur` — auto-hidden if all zero in current page |
| WHT (EUR) | Sum of source+destination `amount_eur` — auto-hidden if no withholding |
| Net (EUR) | `net.eur_amount` |

**Features:**
- Type-filter pills: All / Buy / Sell / Dividend
- Pagination: 20 per page via `GET /api/portfolio/movements?security_id=...`
- Row click: opens `MovementDetailDialog`
- Corporate action indicator: icon badge for movements with `ca_group_id`
- Empty state: "No stock transactions recorded for this security."

**Data source:**
```typescript
const data = await getMovements({
  security_id: securityId,
  txn_type: typeFilter || undefined,
  limit: 20,
  offset: page * 20,
});
```
Returns full `LedgerMovement` objects (all fields available, no backend changes needed).

### `SALES_TYPE_LABELS` constant (Amendment G §G.3.2)

```typescript
// frontend/src/types/portfolio.ts
export const SALES_TYPE_LABELS: Record<"ACCIONES" | "DERECHOS", string> = {
  ACCIONES: "Stocks",
  DERECHOS: "Rights",
};
```

Use this everywhere `sales_type` is displayed (MovementDetailDialog, PortfolioMovementsTable,
PortfolioHoldingsTable, ImportPreview, StockTransactionsTable).

---

## API contract (what the backend returns for `portfolio.recent_movements`)

Used for `PortfolioHoldingsCard` quick-glance. `StockTransactionsTable` uses movements API directly.

```typescript
interface RecentMovement {
  id: string;
  txn_type: "BUY" | "SELL" | "DIVIDEND" | "TRANSFER_IN" | "TRANSFER_OUT";
  trade_date: string;          // YYYY-MM-DD
  quantity?: string | null;
  gross_eur?: string | null;   // gross.eur_amount
  fees_eur?: string | null;    // fees.total_eur
  net_eur?: string | null;     // net.eur_amount
  currency?: string | null;    // gross.currency
  account_id?: string | null;
  sales_type?: string | null;  // "ACCIONES" | "DERECHOS" — SELL only
  correction_status?: string | null;
  import_source?: string | null;
}
```

`portfolio.movement_count` is the real total ledger count (not capped at 10).

---

## `LedgerMovement` fields available for `StockTransactionsTable`

The existing `LedgerMovement` TS type from `portfolio.ts` already has all required fields.
New fields added by Amendment H:

```typescript
// Add to LedgerMovement in frontend/src/types/portfolio.ts:
ca_group_id?: string | null;
ca_leg_type?: "CASH_DIVIDEND" | "RIGHTS_SOLD" | "SHARE_ACQUISITION" | "CASH_TOP_UP" | null;
ca_event_type?: string | null;
ca_group_seq?: number | null;
```

---

## Files changed by Livingston (backend + types)

| File | Change |
|---|---|
| `backend/web/app.py` | `_map_recent_movement`: +7 fields. Both `_compute_symbol_detail` paths: movements fetch outside `if holding:`, real `total_count`, limit 5→10 |
| `frontend/src/types/symbol-detail.ts` | `RecentMovement` interface extended with 7 optional fields |
| `frontend/src/components/SymbolMovementsTable.tsx` | Added Gross+Net columns, sales_type sub-label (deprecated per Amendment I) |
| `backend/src/portfolio/parsers/purchases.py` | Bilingual header aliases (Amendment G) |
| `backend/src/portfolio/parsers/sales.py` | Bilingual headers + English type aliases (Amendment G) |
| `backend/src/portfolio/parsers/dividends.py` | Bilingual header aliases (Amendment G) |
| `backend/src/portfolio/models.py` | `CaLegType`, `CaEventType`, `CorporateActionCreateRequest` enums+models |
| `backend/src/portfolio/cosmos_portfolio.py` | `create_corporate_action()`, `void_corporate_action_group()`, WHT rate derivation |
| `backend/web/portfolio_routes.py` | `POST /api/portfolio/corporate-actions`, `POST .../void` |

## Files Rusty needs to create/modify (Amendment I)

| File | Change |
|---|---|
| `frontend/src/components/DetailSection.tsx` | NEW — reusable collapsible section container |
| `frontend/src/components/StockTransactionsTable.tsx` | NEW — full BUY/SELL/DIVIDEND history with pagination, filters |
| `frontend/src/app/symbols/[symbol]/page.tsx` | Restructure into Options/Stocks stacked sections |
| `frontend/src/components/SymbolMovementsTable.tsx` | Mark deprecated (no longer rendered on detail page) |

## Files Rusty needs to modify (Amendment G)

| File | Change |
|---|---|
| `frontend/src/types/portfolio.ts` | Add `SALES_TYPE_LABELS` constant; add CA group fields to `LedgerMovement` |
| `frontend/src/components/AddMovementDialog.tsx` | Live-computed trade value / cost-per-share (BuyForm); English Stocks/Rights radio labels (SellForm); unit_price cross-validation |
| `frontend/src/components/MovementCorrectionDialog.tsx` | Unit price derivation, live computed summary, cross-validation; WHT rate_pct as read-only computed |
| `frontend/src/components/MovementDetailDialog.tsx` | Map `sales_type` through `SALES_TYPE_LABELS`; show CA group siblings |
| `frontend/src/components/PortfolioMovementsTable.tsx` | Map `sales_type`; CA group indicator icon |
| `frontend/src/components/PortfolioHoldingsTable.tsx` | Map `sales_type` references |
| `frontend/src/components/ImportPreview.tsx` | Map `sales_type` |

