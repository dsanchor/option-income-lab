# Implementation Contract: Portfolio Zero-Share Filter + Full Movement Correction

**Date:** 2026-09-06  
**Author:** Danny (Lead Architect)  
**Status:** PROPOSED — implementation contract pending approval  
**Impact:** Frontend `SymbolsSectionedClient.tsx`, `SymbolsTable.tsx`, backend `MovementCorrectionRequest` model, `cosmos_portfolio.py`, frontend `MovementCorrectionDialog.tsx`, TS types, tests

---

## Part A: Portfolio Section — Hide Zero-Share Rows

### A.1 Problem

The Symbols page's Portfolio section shows ALL portfolio rows including historical positions with zero current shares. The Portfolio Holdings page already has a `hideZeroShares` toggle (default ON). The Symbols page's Portfolio section should replicate this behavior for consistency, while **never** hiding Watchlist-only rows (which have no share concept in this context).

### A.2 Predicate

```
// Portfolio rows only — watchlist rows are NEVER filtered
hide when: portfolio_shares is not null AND parseFloat(portfolio_shares) === 0
show when: portfolio_shares is null OR parseFloat(portfolio_shares) !== 0
```

**Key semantics:**
- **Exact zero hidden** — `portfolio_shares === "0"` or `"0.000000"` → hidden when toggle ON
- **Negative stays visible** — `portfolio_shares === "-50"` → always visible (anomaly the user must see)
- **Null stays visible** — `portfolio_shares === null` (no holdings data computed yet) → visible (conservative)
- **Watchlist rows: NEVER filtered** — The toggle applies exclusively to `portfolioRows`; `watchlistRows` are passed through unfiltered regardless of toggle state

### A.3 State Management

| Property | Value |
|---|---|
| Default state | `true` (hide zero-share portfolio rows) |
| Persistence | React `useState` only — no localStorage, no URL param |
| Scope | `SymbolsSectionedClient` component; one toggle controls the portfolio section |

**Rationale:** Matches `PortfolioHoldingsTable` pattern (session-only `useState`, default ON). User resets on page navigation — acceptable for a filter preference.

### A.4 Count Semantics

The Portfolio section header badge count MUST reflect the **visible** (filtered) count when the toggle is ON:

```
// Current (backend-provided):
portfolioCount = portfolioRows.length  // total portfolio rows

// New:
visiblePortfolioCount = filteredPortfolioRows.length
```

The section header shows `Portfolio (N)` where N = visible rows after filter.

**Watchlist count:** Unchanged — always `watchlistRows.length`.

### A.5 Interaction with Search/Sort

- If the Symbols page adds per-section search in the future, the zero-share filter composes with search (AND): a row must pass BOTH the search predicate and the zero-share predicate to be visible.
- Sort order is unaffected — filtered rows maintain their existing DGI score sort.

### A.6 Implementation Plan

**File: `frontend/src/components/SymbolsSectionedClient.tsx`**

1. Add `const [hideZeroPortfolio, setHideZeroPortfolio] = useState(true);`
2. Compute `filteredPortfolioRows`:
   ```ts
   const filteredPortfolioRows = useMemo(() => {
     if (!hideZeroPortfolio) return portfolioRows;
     return portfolioRows.filter((r) => {
       const shares = r.portfolio_shares != null ? parseFloat(r.portfolio_shares) : null;
       if (shares !== null && shares < 0) return true; // negatives always visible
       return shares === null || shares !== 0;
     });
   }, [portfolioRows, hideZeroPortfolio]);
   ```
3. Render a checkbox toggle inside the Portfolio section (between header and table), matching `PortfolioHoldingsTable` pattern:
   ```tsx
   <label className="flex items-center gap-2 cursor-pointer select-none text-text-muted hover:text-text">
     <input type="checkbox" checked={hideZeroPortfolio}
            onChange={(e) => setHideZeroPortfolio(e.target.checked)}
            className="rounded border-border"
            aria-label="Hide zero-share portfolio symbols" />
     <span className="text-xs">Hide zero-share</span>
   </label>
   ```
4. Pass `filteredPortfolioRows` (not `portfolioRows`) to `<SymbolsTable>`.
5. Update `SectionHeader` count: `count={filteredPortfolioRows.length}`.
6. Add empty-state message when filter hides all rows:
   ```
   "All portfolio symbols have zero shares. Uncheck 'Hide zero-share' to see historical positions."
   ```

**No backend changes required.** The `portfolio_shares` field is already present on portfolio rows.

### A.7 Acceptance Criteria (Part A)

| # | Criterion |
|---|---|
| A-1 | Portfolio section defaults to hiding rows where `portfolio_shares` parses to exactly `0` |
| A-2 | Negative-share rows remain visible when toggle is ON |
| A-3 | Null `portfolio_shares` rows remain visible when toggle is ON |
| A-4 | Watchlist section rows are NEVER affected by the toggle, regardless of state |
| A-5 | Portfolio section count badge reflects visible (filtered) count |
| A-6 | Watchlist count badge never changes due to portfolio toggle |
| A-7 | Unchecking toggle shows all portfolio rows including zero-share |
| A-8 | Toggle state resets on navigation (session-only, no persistence) |
| A-9 | Empty state shown when filter hides all portfolio rows |
| A-10 | `tsc --noEmit` passes with zero errors |

---

## Part B: Full Movement Correction — Field Matrix

### B.0 Architectural Principle

Corrections create a **replacement** document; the original is marked `SUPERSEDED`. No in-place mutation. The replacement inherits all original fields, then overrides only what the user changed. Audit chain preserved via `corrects_movement_id` / `superseded_by` pointers.

**Scope of correction vs reassignment:** Account changes use the existing `POST /movements/{id}/reassign` endpoint (safe account-partition move). Security identity changes are NOT supported in corrections (would require relinking across partitions and cascading holdings recalculation). Corrections handle financial field adjustments within the same account+security scope.

### B.1 Common Fields (All Movement Types)

| Field | Correctable? | Notes |
|---|---|---|
| `trade_date` | ✅ YES | Date picker, pre-filled with original |
| `quantity` | ✅ YES (when applicable) | Null for DIVIDEND stays null; non-null pre-filled |
| `notes` | ✅ YES | Free text, additive to original |
| `account_id` | ❌ NO — use Reassign | Changing partition key requires reassignment workflow |
| `security_id` | ❌ NO | Immutable identity; delete and recreate if wrong security |
| `txn_type` | ❌ NO | Changing BUY→SELL is a different economic event; void + create new |
| `correction_note` | ✅ REQUIRED | Audit trail (existing; already enforced) |

### B.2 BUY Correction Fields

| Field | Correctable? | UI Input | Validation |
|---|---|---|---|
| `trade_date` | ✅ | Date picker | Valid ISO date |
| `quantity` | ✅ | Number input (step=any) | ≥ 0, Decimal string |
| `gross` | ✅ | `{amount, currency, eur_amount}` | All three required together; `amount` and `eur_amount` ≥ 0 |
| `fees` | ✅ | `{total, currency, total_eur}` | All three required together when provided |
| `fx` | ✅ | `{rate, rate_source}` | `rate` > 0 when currency ≠ EUR; rate_source ∈ {ECB, BROKER, MANUAL} |
| `cost_basis_status` | ✅ | Dropdown: COMPLETE / INCOMPLETE | Valid enum |
| `withholding` | ❌ N/A | Not applicable for BUY | BUY movements don't carry withholding |
| `sales_type` | ❌ N/A | Not applicable for BUY | |

**Zero/incomplete cost semantics:** A BUY with `gross.eur_amount = "0"` and `cost_basis_status = "INCOMPLETE"` is valid (zero-cost corporate action acquisition). The correction can change either field independently.

### B.3 SELL Correction Fields

| Field | Correctable? | UI Input | Validation |
|---|---|---|---|
| `trade_date` | ✅ | Date picker | Valid ISO date |
| `quantity` | ✅ | Number input | ≥ 0, Decimal string |
| `gross` | ✅ | `{amount, currency, eur_amount}` | All three required together |
| `fees` | ✅ | `{total, currency, total_eur}` | All three required together when provided |
| `fx` | ✅ | `{rate, rate_source}` | `rate` > 0 when currency ≠ EUR |
| `sales_type` | ✅ | Dropdown: ACCIONES / DERECHOS | Valid enum value |
| `withholding` | ✅ | See §B.5 below | Full withholding object (source + destination) |

**SELL + withholding:** Rare but supported. Some jurisdictions apply withholding on equity dispositions. The correction form shows withholding fields for SELL when the original has withholding data or when the user explicitly enables them.

**Effect on holdings/results:** The replacement SELL is processed by CMP holdings exactly as any SELL. The superseded original is excluded. No manual holdings recalc needed — holdings are derived on read.

### B.4 DIVIDEND Correction Fields

| Field | Correctable? | UI Input | Validation |
|---|---|---|---|
| `trade_date` | ✅ | Date picker (payment date) | Valid ISO date |
| `quantity` | ✅ | Number input (shares at ex-date) | Null allowed (remains null if not provided); ≥ 0 when provided |
| `gross` | ✅ | `{amount, currency, eur_amount}` | All three required together |
| `fees` | ✅ | `{total, currency, total_eur}` | All three required together when provided |
| `fx` | ✅ | `{rate, rate_source}` | `rate` > 0 when currency ≠ EUR |
| `withholding.source` | ✅ | `{country, rate_pct, amount_eur}` | See §B.5 |
| `withholding.destination` | ✅ | `{country, rate_pct, amount_eur}` OR explicit `null` | See §B.5 |

**Withholding null vs zero distinction (CRITICAL):**
- `withholding.destination: null` → "broker does not capture destination withholding" (⚠️ Pending in UI)
- `withholding.destination: {amount_eur: "0", ...}` → "confirmed zero destination withholding" (shows €0.00)
- The correction form MUST distinguish these states. Three-state control:
  - **Not captured** (sends `null`) — renders as ⚠️ Pending
  - **Zero** (sends `{amount_eur: "0", country: "ES", rate_pct: "0"}`) — renders as €0.00
  - **Value** (sends full object) — renders the amount

**Quantity null semantics:** DIVIDEND quantity is null when the source doesn't provide share count. A correction can set it to a value (e.g., "150.5") or keep it null. The form shows "Not specified" for null and allows toggling to a numeric input.

### B.5 Withholding Object Correction

When withholding is correctable (DIVIDEND always; SELL optionally), the form renders:

```
Withholding (origin)
  ├── Country:   [text] e.g. "US"
  ├── Rate %:    [number] e.g. "15"
  └── Amount €:  [number] e.g. "45.30"

Withholding (destination)
  ├── ○ Not captured (null)   ← default if original was null
  ├── ○ Zero (€0.00)
  ├── ○ Value:
  │     ├── Country:   [text] e.g. "ES"
  │     ├── Rate %:    [number] e.g. "19"
  │     └── Amount €:  [number] e.g. "57.00"
```

**Serialization:**
- "Not captured" → `withholding.destination = null` in request
- "Zero" → `withholding.destination = {country: "ES", rate_pct: "0", amount_eur: "0"}`
- "Value" → `withholding.destination = {country, rate_pct, amount_eur}`

### B.6 TRANSFER Correction Policy

**Transfers are NOT correctable via the standard correction endpoint.** Transfer movements are paired (TRANSFER_OUT + TRANSFER_IN) and linked by `transfer_group_id`. Correcting one half without the other would break the pair invariant.

**User workflow for transfer errors:**
1. Void the transfer pair (both OUT and IN via the existing void endpoint)
2. Create a new correct transfer

This is consistent with the user request explicitly naming "dividends, sales, purchases" as correction targets.

### B.7 Field Matrix Summary

| Field | BUY | SELL | DIVIDEND | TRANSFER |
|---|---|---|---|---|
| `trade_date` | ✅ | ✅ | ✅ | ❌ (void+recreate) |
| `quantity` | ✅ | ✅ | ✅ (null-preserving) | ❌ |
| `gross` | ✅ | ✅ | ✅ | ❌ |
| `fees` | ✅ | ✅ | ✅ | ❌ |
| `withholding.source` | ❌ N/A | ✅ | ✅ | ❌ |
| `withholding.destination` | ❌ N/A | ✅ | ✅ | ❌ |
| `fx` | ✅ | ✅ | ✅ | ❌ |
| `cost_basis_status` | ✅ | ❌ N/A | ❌ N/A | ❌ |
| `sales_type` | ❌ N/A | ✅ | ❌ N/A | ❌ |
| `notes` | ✅ | ✅ | ✅ | ❌ |

---

## Part C: Validation, Arithmetic, and Invariants

### C.1 Net Recomputation (Server-Side Source of Truth)

When **any** of `gross`, `fees`, or `withholding` is provided in the correction request, the server recomputes `net`:

```python
net_eur = gross_eur - fees_eur - wht_source_eur - wht_dest_eur
```

Where `wht_dest_eur = 0` when `withholding.destination` is `null`.

**The client MUST NOT send a `net` field.** Net is always server-derived. This eliminates contradictory totals.

### C.2 Partial Override Semantics

If a correction sends only `fees` but not `gross`, the server uses the **original's** `gross` value combined with the new `fees` to recompute `net`. This is already implemented in `cosmos_portfolio.py:correct_movement()` — the replacement starts as a copy of the original, then applies overrides.

### C.3 Decimal Precision

| Type | Precision | Example |
|---|---|---|
| Monetary amounts | 6 decimal places | `"18250.123456"` |
| FX rates | 9 decimal places | `"0.917431193"` |
| Quantities | 6 decimal places | `"100.500000"` |
| Withholding rates | String percentage | `"15"`, `"19.5"` |

All values are Decimal strings. The backend uses `Decimal` type, quantized with `ROUND_HALF_UP`.

### C.4 Immutable Audit Chain

```
Original (mvt_abc_001)
  correction_status: SUPERSEDED
  superseded_by: mvt_xyz_002
  
Replacement (mvt_xyz_002)
  correction_status: ACTIVE
  corrects_movement_id: mvt_abc_001
  correction_note: "Wrong withholding amount"
  import_source: "manual"
```

**Double-correction:** If the replacement itself needs correction, the same flow applies — the replacement becomes SUPERSEDED, and a new replacement is created. Chain: `A → B → C`. At any point, only the latest (`C`) is ACTIVE.

### C.5 Account/Security Immutability in Corrections

- `account_id` on replacement = original's `account_id` (same Cosmos partition key). Account changes use the reassignment endpoint.
- `security_id` on replacement = original's `security_id`. Security changes are not supported (void + recreate).
- `txn_type` on replacement = original's `txn_type`. Type changes are not supported (void + recreate).

### C.6 Correction of Imported Data

Imported movements (csv_import) are correctable. The replacement's `import_source` is set to `"manual"` to indicate it was manually corrected. The original's `import_source` remains `"csv_import"` for provenance.

### C.7 Holdings/CMP Recomputation

No explicit recomputation trigger needed. Holdings are derived on read. After a correction:
- The SUPERSEDED original is excluded from the holdings query (`correction_status = 'ACTIVE'` filter)
- The new replacement is included
- Next `compute_holdings()` call automatically reflects the correction

---

## Part D: API and Frontend Changes

### D.1 Backend API — No Route Changes

The existing endpoint `POST /api/portfolio/movements/{movement_id}/correct` already accepts all needed fields via `MovementCorrectionRequest`:

```python
class MovementCorrectionRequest(BaseModel):
    account_id: str
    correction_note: str
    trade_date: Optional[str] = None
    quantity: Optional[str] = None
    gross: Optional[MoneyAmountInput] = None      # ← already present
    fees: Optional[FeesInput] = None               # ← already present
    withholding: Optional[Any] = None              # ← already present (accepts full object)
    fx: Optional[Dict[str, str]] = None            # ← already present
    sales_type: Optional[str] = None               # ← already present
    cost_basis_status: Optional[str] = None        # ← already present
    notes: Optional[str] = None                    # ← already present
```

The backend `correct_movement()` already applies overrides and recomputes net. **No backend model or route changes required.**

### D.2 Backend — Validation Hardening (Minor)

Add explicit validation in `correct_movement()` for withholding structure when provided:

```python
if "withholding" in correction_data and correction_data["withholding"] is not None:
    wht = correction_data["withholding"]
    if not isinstance(wht, dict):
        raise ValueError("withholding must be an object or null")
    # source: must have amount_eur if present
    if "source" in wht and wht["source"] is not None:
        if "amount_eur" not in wht["source"]:
            raise ValueError("withholding.source must include amount_eur")
    # destination: null is valid (not captured); object must have amount_eur
    if "destination" in wht and wht["destination"] is not None:
        if "amount_eur" not in wht["destination"]:
            raise ValueError("withholding.destination must include amount_eur")
```

### D.3 Frontend TypeScript — `MovementCorrectionRequest` Update

Add `withholding` to the existing TS interface:

```typescript
export interface MovementCorrectionRequest {
  account_id: string;
  correction_note: string;
  trade_date?: string;
  quantity?: string;
  gross?: AmountInput;
  fees?: FeesInput;
  withholding?: {                    // ← ADD
    source?: WithholdingLeg | null;
    destination?: WithholdingLeg | null;
  } | null;
  fx?: { rate: string; rate_source: FxRateSource };
  sales_type?: "ACCIONES" | "DERECHOS";
  cost_basis_status?: CostBasisStatus;     // ← ADD
  notes?: string;
}
```

### D.4 Frontend — Adaptive Correction Form

**`MovementCorrectionDialog.tsx` rewrite:**

The current form only exposes `trade_date`, `quantity`, and a disabled gross field. The new form must be **type-adaptive** — it shows different fields based on `movement.txn_type`:

1. **Header:** Audit notice + original summary (keep existing)
2. **Correction reason** (keep existing, required)
3. **Type-adaptive field sections:**

| Section | BUY | SELL | DIVIDEND |
|---|---|---|---|
| Trade date | ✅ | ✅ | ✅ |
| Quantity | ✅ | ✅ | ✅ (null-aware toggle) |
| Gross (amount/currency/EUR) | ✅ | ✅ | ✅ |
| Fees (total/currency/EUR) | ✅ | ✅ | ✅ |
| FX (rate/source) | ✅ (when currency ≠ EUR) | ✅ | ✅ |
| Sales type | — | ✅ (ACCIONES/DERECHOS) | — |
| Cost basis status | ✅ (COMPLETE/INCOMPLETE) | — | — |
| Withholding source | — | ✅ (collapsible) | ✅ |
| Withholding destination | — | ✅ (collapsible, 3-state) | ✅ (3-state) |
| Notes | ✅ | ✅ | ✅ |

4. **Pre-fill behavior:** All fields pre-filled with original values. User modifies what they need.
5. **Change detection:** Form submits only changed fields (fields that differ from the original). Unchanged fields are not sent (existing pattern).
6. **FX helper:** Reuse `FxHelper` pattern from `AddMovementDialog.tsx` for rate fetching.

### D.5 Frontend — Transfer Correction Disabled

When `movement.txn_type` is `TRANSFER_OUT` or `TRANSFER_IN`, the "Correct movement" button in `MovementDetailDialog.tsx` should be **disabled** with tooltip: "Transfers cannot be corrected individually. Void the transfer pair and create a new one."

---

## Part E: Backward Compatibility and Migration

### E.1 No Migration Required

- Backend API: No route changes. `MovementCorrectionRequest` already accepts all fields.
- Backend logic: `correct_movement()` already handles `withholding` as an overridable field.
- Cosmos schema: No document structure changes.
- Frontend: Additive changes only (new UI fields in correction dialog, new filter in symbols page).

### E.2 Backward Compatibility

- Existing corrections (with only `trade_date`/`quantity`) continue to work unchanged.
- Frontend correction dialog gracefully handles movements that have no withholding data (fields show empty/null state).
- Portfolio zero-share filter is additive UI; backend returns same data.

---

## Part F: Test Matrix and Ownership

### F.1 Backend Tests — Livingston

**File: `backend/tests/test_portfolio_phase2_corrections.py`** (extend existing)

| # | Test | Assertion |
|---|---|---|
| C-1 | Correct BUY with gross+fees override | Replacement has new gross, new fees, recomputed net |
| C-2 | Correct BUY with cost_basis_status INCOMPLETE→COMPLETE | Replacement has `cost_basis_status: "COMPLETE"` |
| C-3 | Correct SELL with withholding source added | Replacement has `withholding.source` with amount_eur; net recomputed |
| C-4 | Correct SELL with sales_type ACCIONES→DERECHOS | Replacement `sales_type: "DERECHOS"` |
| C-5 | Correct DIVIDEND with withholding destination null→value | Replacement has `withholding.destination` with amount; net reduced |
| C-6 | Correct DIVIDEND with withholding destination value→null | Replacement `withholding.destination: null`; net increased |
| C-7 | Correct DIVIDEND with withholding destination zero | `withholding.destination.amount_eur = "0"`; net unchanged vs no-wht |
| C-8 | Correct with gross change triggers net recompute | `net_eur = new_gross - original_fees` |
| C-9 | Correct with fees change triggers net recompute | `net_eur = original_gross - new_fees` |
| C-10 | Correct with withholding change triggers net recompute | `net_eur = gross - fees - wht_source - wht_dest` |
| C-11 | Correct with fx override | Replacement `fx.rate` and `fx.rate_source` updated |
| C-12 | DIVIDEND quantity null preserved when not provided | Replacement `quantity: null` |
| C-13 | DIVIDEND quantity null→value via correction | Replacement `quantity: "150"` |
| C-14 | Invalid withholding structure → 400 | Missing `amount_eur` in source |
| C-15 | TRANSFER correction → 400 or 405 | Transfers blocked from correction |

### F.2 Frontend Tests — Rusty

**Components to test:**

| # | Test | Component | Assertion |
|---|---|---|---|
| F-1 | Portfolio zero-share filter default ON | `SymbolsSectionedClient` | Zero-share rows hidden, count updated |
| F-2 | Negative shares visible with filter ON | `SymbolsSectionedClient` | Negative-share row present in DOM |
| F-3 | Watchlist rows unaffected by filter | `SymbolsSectionedClient` | All watchlist rows visible regardless |
| F-4 | Filter toggle shows/hides rows | `SymbolsSectionedClient` | Uncheck → all rows visible |
| F-5 | Correction form shows gross/fees for BUY | `MovementCorrectionDialog` | Gross, fees, FX inputs rendered |
| F-6 | Correction form shows withholding for DIVIDEND | `MovementCorrectionDialog` | Source + destination withholding sections |
| F-7 | Withholding 3-state control (null/zero/value) | `MovementCorrectionDialog` | Radio group with correct serialization |
| F-8 | Transfer correction button disabled | `MovementDetailDialog` | Button disabled with tooltip |
| F-9 | Only changed fields sent in request | `MovementCorrectionDialog` | Unchanged fields omitted from API call |

### F.3 Acceptance Tests — Basher

**End-to-end integration tests:**

| # | Test | Scenario |
|---|---|---|
| E-1 | Create BUY → correct gross → verify holdings recalculated | Full cycle through API |
| E-2 | Create DIVIDEND with null dest WHT → correct to add dest WHT → verify net reduced | Withholding lifecycle |
| E-3 | Create SELL → correct with DERECHOS type → verify shares unchanged | Rights sale semantics preserved |
| E-4 | Double-correct same movement → second returns new replacement, first stays SUPERSEDED chain | Chain integrity |
| E-5 | Create TRANSFER → attempt correct → 400/405 | Transfer guard |
| E-6 | Symbols page renders with filter ON → zero-share hidden, negative visible | UI integration |

---

## Out of Scope (Explicit)

1. **Payment date / ex-dividend date corrections** — Not yet modeled as separate fields in `ManualMovementCreate`. Would require schema extension. Deferred.
2. **Dividend share leg (scrip/DRIP) corrections** — Complex atomic leg correction. Deferred.
3. **Batch corrections** — Not requested. Individual correction workflow suffices.
4. **Security/account change in correction** — Explicitly separate operations (reassign / void+recreate).
5. **URL-persisted or localStorage-persisted zero-share filter** — Session-only state is sufficient.

---

# Amendment G: Manual BUY/SELL Clarity, English Labels & Bilingual CSV Import

**Date:** 2026-09-06  
**Author:** Danny (Lead Architect)  
**Status:** PROPOSED — additive amendment to Parts B–F above  
**Trigger:** New user requirements for manual movement creation UX, English sale-type labels, and bilingual CSV import

---

## G.1 Glossary & Formulas: BUY and SELL

### G.1.1 BUY — Financial Definitions

| Concept | Backend field | Formula | Display label (EN) |
|---|---|---|---|
| Quantity | `quantity` | User input | Quantity |
| Unit price (without fees) | *UI-only* | User input or `trade_value / quantity` | Price per share (without fees) |
| Trade value / gross | `gross.amount` | `quantity × unit_price` | Trade value |
| Fees | `fees.total` | User input | Fees |
| **Total cost** | `net.eur_amount` (sign-inverted) | `gross + fees` | **Total cost (with fees)** |
| Effective cost per share | *UI-only, derived* | `(gross + fees) / quantity` | Cost per share (with fees) |

**Backend mapping:** `gross` = trade value (quantity × price, BEFORE fees). `net` is computed server-side as `gross − fees` for BUY (no withholding). The total cash outflow is `gross + fees`, which equals `−net` when net is negative, but since the system stores net as `gross − fees`, the UI must show **Total cost = gross + fees** as a display computation.

**Clarification:** The existing backend formula `net = gross − fees − wht` is preserved. For BUY (no withholding), `net = gross − fees`. The UI label "Total cost (with fees)" displays `gross + fees` which is the actual cash outflow. This is NOT the same as `net` — it is a UI-derived display value.

### G.1.2 SELL — Financial Definitions

| Concept | Backend field | Formula | Display label (EN) |
|---|---|---|---|
| Quantity | `quantity` | User input | Quantity |
| Unit price (without fees) | *UI-only* | User input or `trade_value / quantity` | Price per share (without fees) |
| Trade value / gross | `gross.amount` | `quantity × unit_price` | Trade value |
| Fees | `fees.total` | User input | Fees |
| Withholding | `withholding.source.amount_eur` etc. | User input (rare for SELL) | Withholding |
| **Net proceeds** | `net.eur_amount` | `gross − fees − wht` (server) | **Net proceeds (after fees)** |
| Effective proceeds per share | *UI-only, derived* | `net / quantity` | Proceeds per share (after fees) |

**Backend mapping:** Identical to existing — `gross` = trade value, `net = gross − fees − wht`. No backend changes.

### G.1.3 Source-of-Truth Policy: Quantity × Price vs Gross

When creating or correcting a BUY/SELL, the user may supply:
- **quantity** + **unit_price** → frontend computes `trade_value = quantity × unit_price` and sends it as `gross.amount`
- **gross** (trade_value) directly → user types total trade value

**Resolution rules (frontend, before API call):**

1. **quantity + unit_price filled, gross empty** → auto-compute `gross = quantity × unit_price`. Display in the trade value field (read-only computed).
2. **gross filled, unit_price empty** → auto-compute `unit_price = gross / quantity` when quantity > 0. Display in the price field (read-only computed).
3. **All three filled** → validate `|quantity × unit_price − gross| ≤ 0.01` (1-cent rounding tolerance in the transaction currency). If exceeded, show inline error: *"Trade value doesn't match quantity × price. Correct one of them."* Block submission.
4. **Only gross filled, quantity zero/empty** → valid (zero-cost corporate action for BUY; rights sale for SELL). Unit price shows "—".

**Server behavior:** The server receives `gross` only — it does NOT receive `unit_price`. The server never validates quantity × price = gross (it doesn't know unit_price). Unit price is a UI-only convenience field. The server's only job is `net = gross − fees − wht` as per §C.1.

**Correction behavior:** Same rules. The correction form pre-fills quantity and gross from the original. Unit price is derived as `gross / quantity`. User can edit any field; cross-validation fires live.

### G.1.4 Currency, FX, and EUR Calculation

No changes to existing FX model. Recap for completeness:

- If `currency ≠ EUR`, user must provide or fetch FX rate via `FxHelper`.
- `gross.eur_amount = gross.amount × fx.rate` (frontend computes before sending).
- `fees.total_eur = fees.total × fx.rate` (frontend computes before sending).
- Same for withholding amounts in EUR.
- All EUR amounts: 6 decimal places, `ROUND_HALF_UP`.
- FX rates: 9 decimal places.
- Quantities: 6 decimal places.

### G.1.5 Rounding and Decimal Rules

No changes to §C.3. All values remain Decimal strings. The 0.01 rounding tolerance in §G.1.3 is evaluated in the transaction currency (not EUR), using standard floating-point comparison after parsing.

---

## G.2 BUY/SELL Creation UI — Field Layout and Live Computations

### G.2.1 BUY Form (updated `AddMovementDialog.tsx → BuyForm`)

```
Security *             [dropdown]
Account                [dropdown]
Trade date *           [date picker]
──────────────────────────────────
Quantity *             [number]
Price per share        [number]          ← label: "Price per share (without fees)"
  (without fees)
Trade value            [number|computed]  ← label: "Trade value"
                                           auto = qty × price; editable override
Currency               [text, 3 chars]
  [FxHelper]
──────────────────────────────────
Fees                   [number]          ← label: "Fees"

── Live computed summary ──────────
Total cost (with fees)  = trade_value + fees       (read-only display)
Cost per share          = total_cost / quantity     (read-only display, when qty > 0)
  (with fees)
──────────────────────────────────
Notes                  [textarea]
```

**Interaction behavior:**
- Typing quantity + price → auto-fills trade value (live)
- Typing trade value → auto-fills price per share when quantity > 0 (live)
- Typing both → cross-validation (§G.1.3)
- "Total cost (with fees)" and "Cost per share (with fees)" are **read-only computed displays**, never editable

### G.2.2 SELL Form (updated `AddMovementDialog.tsx → SellForm`)

```
Security *             [dropdown]
Account                [dropdown]
Trade date *           [date picker]
──────────────────────────────────
Sale type *            (○ Stocks  ○ Rights)   ← ENGLISH labels
  [info banner when Rights selected]
Quantity *             [number]               ← required for Stocks; optional for Rights
Price per share        [number]               ← "Price per share (without fees)"
  (without fees)
Trade value *          [number|computed]       ← "Trade value"
Currency               [text, 3 chars]
  [FxHelper]
──────────────────────────────────
Fees                   [number]

── Live computed summary ──────────
Net proceeds            = trade_value − fees       (read-only display)
  (after fees)
Proceeds per share      = net / quantity            (read-only, when qty > 0)
  (after fees)
──────────────────────────────────
Notes                  [textarea]
```

**Sale type radio labels** render English text `Stocks` / `Rights`, but set `form.sales_type = "ACCIONES"` / `"DERECHOS"` as the value sent to the API. This is a **display-only change** — the internal enum is unchanged.

### G.2.3 Correction Form Additions

The correction dialog (`MovementCorrectionDialog.tsx`) must expose the same live-computed summary when correcting BUY or SELL:

- Pre-fill unit_price from `original.gross.amount / original.quantity` (when quantity > 0)
- Show "Total cost (with fees)" or "Net proceeds (after fees)" as read-only computed
- Apply same cross-validation (§G.1.3) when user edits quantity, price, or gross
- SELL correction: sale type radio shows `Stocks` / `Rights` labels (same as §G.2.2)

---

## G.3 Internal Enum Compatibility: Sales Type

### G.3.1 Canonical Values (UNCHANGED)

```python
# Backend — keep as-is
_VALID_SALES_TYPES = {"ACCIONES", "DERECHOS"}

# Cosmos documents — sales_type values remain "ACCIONES" or "DERECHOS"
# Frontend TS type — remains: "ACCIONES" | "DERECHOS"
```

**Rationale:** Changing the canonical enum would require a Cosmos migration of all existing documents, re-validation of holdings/CMP calculations, and parser output rewriting. Risk far exceeds benefit. The enum is an internal implementation detail; the user sees labels.

### G.3.2 UI Label Mapping

| Internal value | UI label (EN) | Used in |
|---|---|---|
| `ACCIONES` | Stocks | SELL form radio, Correction dialog radio, Movement detail display, Holdings table |
| `DERECHOS` | Rights | SELL form radio, Correction dialog radio, Movement detail display, Holdings table |

**Implementation:** A single mapping constant in the frontend:

```typescript
export const SALES_TYPE_LABELS: Record<"ACCIONES" | "DERECHOS", string> = {
  ACCIONES: "Stocks",
  DERECHOS: "Rights",
};
```

Used wherever `sales_type` is displayed. Everywhere that currently renders the raw string `"ACCIONES"` or `"DERECHOS"` must use this mapping.

### G.3.3 Affected Frontend Files

| File | Current behavior | Change |
|---|---|---|
| `AddMovementDialog.tsx` | Radio labels show `ACCIONES` / `DERECHOS` | Show `Stocks` / `Rights` via `SALES_TYPE_LABELS` |
| `MovementCorrectionDialog.tsx` | (New from Part D) Radio for sale type | Show `Stocks` / `Rights` via `SALES_TYPE_LABELS` |
| `MovementDetailDialog.tsx` | Displays `sales_type` raw value | Map through `SALES_TYPE_LABELS` |
| `PortfolioMovementsTable.tsx` | Displays `sales_type` in list | Map through `SALES_TYPE_LABELS` |
| `PortfolioHoldingsTable.tsx` | References sale types in tooltips/labels | Map through `SALES_TYPE_LABELS` |
| `ImportPreview.tsx` | Displays sale type in preview | Map through `SALES_TYPE_LABELS` |

---

## G.4 Bilingual CSV Import — Headers and Type Aliases

### G.4.1 Purchases Parser Header Aliases

Current headers (Spanish only):
```
Año | Empresa | Fecha compra | Valor compra | Acciones | Total (€) | Comisión
```

New: accept **either** Spanish or English headers. The parser normalizes headers (lowercase, strip accents/whitespace) and matches against an alias map:

```python
_PURCHASES_HEADER_ALIASES = {
    # Position 0: Year
    "ano": 0, "año": 0, "year": 0,
    # Position 1: Company
    "empresa": 1, "company": 1,
    # Position 2: Purchase date
    "fecha compra": 2, "fecha de compra": 2, "purchase date": 2, "buy date": 2, "date": 2,
    # Position 3: Price per share
    "valor compra": 3, "precio": 3, "price per share": 3, "price": 3, "unit price": 3,
    # Position 4: Shares
    "acciones": 4, "shares": 4, "quantity": 4,
    # Position 5: Total
    "total (€)": 5, "total (eur)": 5, "total": 5, "total cost": 5, "trade value": 5,
    # Position 6: Commission/Fees
    "comision": 6, "comisión": 6, "commission": 6, "fees": 6,
}
```

**Matching algorithm:**
1. Normalize each header cell: NFKD decompose → strip combining marks → lowercase → strip whitespace → collapse internal spaces.
2. Match normalized header against the alias map.
3. All 7 positions must be matched. If any position is unmatched, raise `ValueError` with the unrecognized header.
4. Headers may appear in any order within the alias map (but the current positional matching is kept for simplicity — aliases map to the expected position, and the parser validates position-by-position).

**Backward compatibility:** Existing Spanish-only files continue to work identically. The alias map is a superset of the current exact-match validation.

### G.4.2 Sales Parser Header Aliases

Current headers (6-column legacy):
```
Año | Empresa | Fecha venta | Acciones | Comisión | Total Venta
```

7-column variant A:
```
Año | Empresa | Fecha venta | Tipo | Acciones | Comisión | Total Venta
```

New: bilingual header aliases per position:

```python
_SALES_HEADER_ALIASES = {
    # Position 0
    "ano": 0, "año": 0, "year": 0,
    # Position 1
    "empresa": 1, "company": 1,
    # Position 2
    "fecha venta": 2, "fecha de venta": 2, "sale date": 2, "sell date": 2, "date": 2,
    # Position 3 (7-col variant A) or Position 6 (7-col variant B)
    "tipo": "tipo", "type": "tipo", "sale type": "tipo",
    # Shares position (3 in 6-col, 4 in 7A)
    "acciones": "shares", "shares": "shares", "quantity": "shares",
    # Commission
    "comision": "commission", "comisión": "commission", "commission": "commission", "fees": "commission",
    # Total proceeds
    "total venta": "total", "total": "total", "total proceeds": "total", "proceeds": "total",
}
```

**Variant detection** remains the same: if normalized header at position 3 matches a "tipo" alias → variant 7A; if position 6 matches → variant 7B; otherwise → 6-column legacy.

### G.4.3 Sales Type Value Aliases (Bilingual)

Current: only `ACCIONES` / `DERECHOS` (case-insensitive, accent-insensitive).

New `_normalize_sales_type()` accepts:

| Input (normalized) | Output |
|---|---|
| `ACCIONES` | `ACCIONES` |
| `DERECHOS` | `DERECHOS` |
| `STOCKS` | `ACCIONES` |
| `SHARES` | `ACCIONES` |
| `RIGHTS` | `DERECHOS` |
| *(empty/whitespace)* | `ACCIONES` (legacy default — 6-column files) |
| *(any other non-empty value)* | **ValueError** — invalid type, MUST NOT silently default |

```python
_SALES_TYPE_ALIASES = {
    "ACCIONES": "ACCIONES",
    "DERECHOS": "DERECHOS",
    "STOCKS": "ACCIONES",
    "SHARES": "ACCIONES",
    "RIGHTS": "DERECHOS",
}
```

Normalization: NFKD → strip combining marks → uppercase → strip whitespace. Lookup in `_SALES_TYPE_ALIASES`. If not found and non-empty, raise `ValueError(f"Invalid type value '{raw}'; must be one of: Acciones, Derechos, Stocks, Shares, Rights")`.

**Critical:** Only empty/whitespace values default to `ACCIONES` (preserving 6-column legacy behavior). A non-empty unrecognized value (e.g., "OPCIONES", "stock", with a typo) MUST error. This was already the behavior for `ACCIONES`/`DERECHOS`; we are extending, not relaxing.

### G.4.4 Dividends Parser Header Aliases

Current headers:
```
Año | Empresa | Fecha de cobro | Importe Bruto | Importe Neto | Importe en Derechos | Retención Origen | Retención Destino
```

New bilingual aliases:

```python
_DIVIDENDS_HEADER_ALIASES = {
    # Position 0
    "ano": 0, "año": 0, "year": 0,
    # Position 1
    "empresa": 1, "company": 1,
    # Position 2
    "fecha de cobro": 2, "fecha cobro": 2, "payment date": 2, "date": 2,
    # Position 3
    "importe bruto": 3, "gross amount": 3, "gross": 3,
    # Position 4
    "importe neto": 4, "net amount": 4, "net": 4,
    # Position 5
    "importe en derechos": 5, "rights amount": 5, "scrip amount": 5,
    # Position 6
    "retencion origen": 6, "retención origen": 6, "source withholding": 6, "withholding source": 6, "wht source": 6,
    # Position 7
    "retencion destino": 7, "retención destino": 7, "destination withholding": 7, "withholding destination": 7, "wht destination": 7, "wht dest": 7,
}
```

### G.4.5 Implementation Strategy for Parsers

**Approach:** Replace the current positional exact-match validation with an alias-based lookup while preserving the positional convention. Each parser:

1. Normalizes each header cell (NFKD, strip accents, lowercase, collapse spaces).
2. Looks up in the alias map to determine expected position.
3. If the normalized header at position N matches the alias for position N → match.
4. If a header doesn't match any alias → `ValueError` with the unrecognized header name.
5. All expected positions must be covered.

**Files to modify:**
- `backend/src/portfolio/parsers/purchases.py` — add `_PURCHASES_HEADER_ALIASES`, update header validation
- `backend/src/portfolio/parsers/sales.py` — add `_SALES_HEADER_ALIASES`, `_SALES_TYPE_ALIASES`, update `_normalize_sales_type()` and header validation
- `backend/src/portfolio/parsers/dividends.py` — add `_DIVIDENDS_HEADER_ALIASES`, update header validation

**No changes to `common.py`** — shared utilities are language-agnostic.

---

## G.5 Affected Files and Agent Assignments

### G.5.1 Backend Changes (Livingston)

| File | Change | Scope |
|---|---|---|
| `backend/src/portfolio/parsers/sales.py` | Add `_SALES_TYPE_ALIASES`, `_SALES_HEADER_ALIASES`; update `_normalize_sales_type()` and header validation | Moderate |
| `backend/src/portfolio/parsers/purchases.py` | Add `_PURCHASES_HEADER_ALIASES`; update header validation | Moderate |
| `backend/src/portfolio/parsers/dividends.py` | Add `_DIVIDENDS_HEADER_ALIASES`; update header validation | Moderate |
| `backend/src/portfolio/cosmos_portfolio.py` | No changes (gross/net logic already correct) | None |
| `backend/src/portfolio/models.py` | No changes (ManualMovementCreate, MovementCorrectionRequest already correct) | None |
| `backend/web/portfolio_routes.py` | No changes | None |

### G.5.2 Frontend Changes (Rusty)

| File | Change | Scope |
|---|---|---|
| `frontend/src/types/portfolio.ts` | Add `SALES_TYPE_LABELS` constant; add `withholding` to `MovementCorrectionRequest` (from Part D) | Small |
| `frontend/src/components/AddMovementDialog.tsx` | **BuyForm:** add live-computed trade value / total cost / cost-per-share; relabel fields per §G.2.1. **SellForm:** relabel radio to Stocks/Rights; add live-computed net proceeds / proceeds-per-share; relabel fields per §G.2.2 | Large |
| `frontend/src/components/MovementCorrectionDialog.tsx` | Add unit price derivation, live computed summary, cross-validation (§G.2.3); sale type uses English labels | Large |
| `frontend/src/components/MovementDetailDialog.tsx` | Map `sales_type` through `SALES_TYPE_LABELS` | Small |
| `frontend/src/components/PortfolioMovementsTable.tsx` | Map `sales_type` through `SALES_TYPE_LABELS` | Small |
| `frontend/src/components/PortfolioHoldingsTable.tsx` | Map `sales_type` references through `SALES_TYPE_LABELS` | Small |
| `frontend/src/components/ImportPreview.tsx` | Map `sales_type` through `SALES_TYPE_LABELS` | Small |

### G.5.3 Test Changes (Basher / Livingston)

| File | Change | Agent |
|---|---|---|
| `backend/tests/test_portfolio_parsers.py` | Add tests for English headers, bilingual type aliases, mixed-language files, invalid type error, empty-type default | Livingston |
| `backend/tests/test_portfolio_import_service.py` | Add test for English-header CSV end-to-end import | Livingston |
| Frontend component tests | Test English sale-type labels render, live computation, cross-validation | Rusty |

---

## G.6 Acceptance Criteria (Amendment G)

| # | Criterion |
|---|---|
| G-1 | BUY creation form shows "Price per share (without fees)", "Trade value", "Total cost (with fees)", "Cost per share (with fees)" with live computation |
| G-2 | SELL creation form shows equivalent fields with "Net proceeds (after fees)" and "Proceeds per share (after fees)" |
| G-3 | Typing quantity + price auto-computes trade value; typing trade value + quantity auto-computes price per share |
| G-4 | If all three (quantity, price, trade value) are filled and `|qty × price − trade_value| > 0.01`, an inline validation error blocks submission |
| G-5 | BUY/SELL correction dialog shows the same live-computed fields and cross-validation as creation |
| G-6 | SELL form and correction dialog show sale type radio labels `Stocks` / `Rights` (not `ACCIONES` / `DERECHOS`) |
| G-7 | The API payload still sends `sales_type: "ACCIONES"` or `"DERECHOS"` — internal enum unchanged |
| G-8 | `MovementDetailDialog`, `PortfolioMovementsTable`, `PortfolioHoldingsTable`, `ImportPreview` all display English labels via `SALES_TYPE_LABELS` |
| G-9 | Sales CSV parser accepts English headers (`Year`, `Company`, `Sale Date`, `Type`, `Shares`, `Commission`, `Total Proceeds`) and maps correctly |
| G-10 | Purchases CSV parser accepts English headers (`Year`, `Company`, `Purchase Date`, `Price per share`, `Shares`, `Total`, `Commission`) |
| G-11 | Dividends CSV parser accepts English headers (`Year`, `Company`, `Payment Date`, `Gross Amount`, `Net Amount`, `Rights Amount`, `Source Withholding`, `Destination Withholding`) |
| G-12 | Sales type values `Stocks`, `Shares`, `Rights` (case/accent-insensitive) are accepted and mapped to `ACCIONES` / `DERECHOS` |
| G-13 | A non-empty, unrecognized sale type value raises `ValueError` — no silent default |
| G-14 | Empty sale type in 6-column legacy files still defaults to `ACCIONES` (backward compatible) |
| G-15 | Existing Spanish-only CSV files continue to parse identically (no regression) |
| G-16 | Server gross/net computation unchanged: `net = gross − fees − wht` (§C.1 preserved) |
| G-17 | `tsc --noEmit` passes with zero errors after frontend changes |
| G-18 | All existing backend parser tests pass without modification |

---

# Amendment H: Dividend Withholding Derivation & Composite Corporate-Action Events

**Date:** 2026-09-06  
**Author:** Danny (Lead Architect)  
**Status:** PROPOSED — additive amendment  
**Trigger:** User requirements for derived WHT percentages, and composite dividend/corporate-action creation support  
**References:** `decisions.md §2.1` (ca_event + ca_leg model), Danny consolidation session 2026-09-05

---

## H.1 Withholding Percentage Derivation

### H.1.1 Current Problem

The dividend creation/correction forms treat `rate_pct` (withholding percentage) as a primary user input alongside `amount_eur`. This creates ambiguity: which is authoritative when they disagree?

### H.1.2 Design: Amount Is Primary, Rate Is Derived

**Withholding inputs (dividend creation and correction):**

| Field | Input type | Role |
|---|---|---|
| `country` | Text (2-char ISO) | User input |
| `amount_eur` | Number | **Primary** — user types the actual EUR amount withheld |
| `rate_pct` | Number (read-only computed) | **Derived** — `rate_pct = (amount_eur / gross.eur_amount) × 100` |

**Behavior:**
1. User enters gross amount and withholding amount_eur.
2. Frontend auto-computes `rate_pct = (amount_eur / gross_eur) × 100`, displayed as read-only.
3. Stored `rate_pct` is the computed value (string, 2 decimal places). If gross is zero, `rate_pct = "0"`.
4. User **cannot** type a percentage and have amount derived (this reversal is error-prone with rounding).

**Why amount is primary:** Broker statements show EUR amounts, not percentages. The percentage is a tax-treaty concept useful for display but not the authoritative figure. Storing both is fine; the amount is always the source of truth.

**Backend impact:** None. `WithholdingDetail.rate_pct` is already an optional string. The server stores whatever the client sends. No server-side derivation needed (client computes before sending).

### H.1.3 Applies To

- `AddMovementDialog.tsx` → DividendForm (creation): source + destination WHT
- `MovementCorrectionDialog.tsx` → DIVIDEND correction: source + destination WHT
- `MovementCorrectionDialog.tsx` → SELL correction: source + destination WHT (rare but supported per §B.3)

### H.1.4 Manual Override Escape Hatch

A small "Edit rate manually" toggle may be added in a future iteration if users need to specify an exact treaty rate and derive the amount. For now, amount-first is the only supported flow. This is a non-breaking extension if needed later.

---

## H.2 Composite Corporate-Action Events — Scope Gate Decision

### H.2.1 Background

The `ca_event` + `ca_leg` model was designed in `decisions.md §2.1` (Danny consolidation, 2026-09-05) to handle composite dividend/corporate-action events with up to four leg types:

| Leg type | Cash flow | Holdings impact | Example |
|---|---|---|---|
| `CASH_DIVIDEND` | Inflow | None | €225 gross, minus WHT |
| `RIGHTS_SOLD` | Inflow | None | Residual rights sold at market, €67 proceeds |
| `SHARE_ACQUISITION` | None | +shares | 9 shares received via scrip election |
| `CASH_TOP_UP` | Outflow | None | Investor pays €4.95 to round up to whole shares |

**Current implementation status:** The `ca_event`/`ca_leg` model exists **only as architecture** in `decisions.md`. Production code uses exclusively `ledger_txn` documents (flat, no parent-child linking). No `doc_type: "ca_event"` or `"ca_leg"` exists anywhere in codebase or Cosmos.

### H.2.2 Feasibility Assessment

Implementing full `ca_event`/`ca_leg` requires:

| Work item | Scope | Risk |
|---|---|---|
| New Cosmos doc types (`ca_event`, `ca_leg`) and CRUD operations | Large | Schema evolution; new query patterns |
| Holdings service: process `ca_leg` docs alongside `ledger_txn` | Large | Must not double-count; mixed-model queries |
| CMP cost-basis: `SHARE_ACQUISITION` legs with separate cost_basis model | Large | New cost-basis recording method enum, FMV separation |
| Frontend: multi-leg creation wizard (Steps: event type → legs → review) | Large | Complex UX; multi-step atomic submission |
| Correction: atomic multi-leg correction (replace entire event) | Large | Transactional consistency across legs |
| Import pipeline: existing dividend CSV → ca_event migration path | Medium | Backward compat with 8-column format |
| Test coverage: new doc types, holdings with mixed models | Large | Extensive matrix |

**Total estimate:** 3–4 sprints of focused work across all agents.

### H.2.3 Decision: Two-Phase Approach

**Phase H-α (IN SCOPE — this contract):** Linked `ledger_txn` documents as a lightweight corporate-action group, reusing the existing flat model with a new `ca_group_id` linking field. This gives the user creation support for composite events **now**, without requiring the full ca_event schema migration.

**Phase H-β (DEFERRED):** Full `ca_event` + `ca_leg` migration. When implemented, existing `ca_group_id`-linked `ledger_txn` documents are migrated to proper `ca_event` + `ca_leg` structure. The group_id becomes the navigation bridge.

**Rationale:** The user said "hablamos de poder" — they want the capability, not a theoretical model. Phase H-α gives them creation and correction of composite events using the proven `ledger_txn` infrastructure, with clean migration path to H-β.

---

## H.3 Phase H-α: Corporate-Action Groups via Linked `ledger_txn` Documents

### H.3.1 Core Concept

A corporate-action group is a set of `ledger_txn` documents sharing the same `ca_group_id`. Each document has a `ca_leg_type` discriminator. The group has no separate parent document — the first leg's metadata serves as the group anchor.

### H.3.2 New Fields on `ledger_txn`

```python
# Added to ledger_txn documents (optional — null for standalone movements)
{
    "ca_group_id": "cag_{uuid}",                   # links legs of same corporate action
    "ca_leg_type": "CASH_DIVIDEND",                # discriminator: see §H.3.3
    "ca_event_type": "DIVIDEND_WITH_SCRIP",        # group classification
    "ca_group_seq": 1,                             # ordering within group (1-based)
}
```

### H.3.3 Leg Types and Mapping to `ledger_txn` Fields

Each leg type reuses existing `ledger_txn` fields with specific semantics:

| `ca_leg_type` | `txn_type` | `quantity` | `gross` | `fees` | `withholding` | Holdings impact |
|---|---|---|---|---|---|---|
| `CASH_DIVIDEND` | `DIVIDEND` | null (shares at ex-date, optional) | Gross dividend | Fees if any | Source + destination | None (cash inflow) |
| `RIGHTS_SOLD` | `SELL` | Rights units (informational) | Sale proceeds | Sale fees | Own source + destination | **None** — `sales_type = "DERECHOS"` |
| `SHARE_ACQUISITION` | `BUY` | Shares received | FMV total (informational) | Zero typically | None | **+shares** — `cost_basis_status` per below |
| `CASH_TOP_UP` | `BUY` | `"0"` | Top-up amount | Fees if any | None | **None** (no shares; pure outflow) |

**Critical mappings:**

- **RIGHTS_SOLD** → `txn_type = "SELL"`, `sales_type = "DERECHOS"`. This reuses the existing DERECHOS pathway from holdings_service.py — no share count change, proceeds counted.

- **SHARE_ACQUISITION** → `txn_type = "BUY"`, `cost_basis_status` depends on whether cost basis is known:
  - Known cost basis → `"COMPLETE"`, `gross = cost_basis_total_eur` (the tax-relevant acquisition cost, NOT FMV)
  - Unknown cost basis → `"INCOMPLETE"`, `gross = "0"`. User fills in later via correction. Matches existing zero-cost corporate action pattern.
  - FMV stored in `notes` field as structured text (e.g., `"FMV: 24.75 GBP/share (broker statement)"`) until ca_leg model adds dedicated fields.

- **CASH_TOP_UP** → `txn_type = "BUY"`, `quantity = "0"`, `gross = top_up_amount`. This is a pure cash outflow that does NOT add shares. With `quantity = "0"`, holdings adds zero shares but records the cost. `cost_basis_status = "INCOMPLETE"` (cost attribution handled by user — top-up does NOT auto-determine share cost basis per Danny consolidation invariant).

### H.3.4 Arithmetic Invariants

For a corporate-action group with all four legs:

```
Total net cash flow = CASH_DIVIDEND.net + RIGHTS_SOLD.net − CASH_TOP_UP.gross
Total share change  = SHARE_ACQUISITION.quantity  (only this leg adds shares)
```

Each leg's internal arithmetic follows existing `net = gross − fees − wht` (§C.1). **No cross-leg arithmetic is enforced by the server** — each leg is a self-contained `ledger_txn`. The group_id is an audit/display link only.

**Why no cross-leg server validation:** Real corporate actions have complex economics (partial elections, fractional share rounding, multi-date settlements). Enforcing `cash_dividend.gross == shares_acquired × fmv` would reject valid real-world events. The user enters each leg as the broker reports it.

### H.3.5 Holdings and Cost-Basis Effects (Detailed)

Each leg is processed by `holdings_service.py` as a normal `ledger_txn`:

| Leg | Holdings effect | CMP pool effect |
|---|---|---|
| `CASH_DIVIDEND` (txn_type=DIVIDEND) | No share change | No pool change. Counted in `total_dividends_eur`. |
| `RIGHTS_SOLD` (txn_type=SELL, sales_type=DERECHOS) | No share change | No pool change. Counted in `rights_proceeds_eur` and `total_sale_proceeds_eur`. |
| `SHARE_ACQUISITION` (txn_type=BUY, COMPLETE) | `+quantity` shares | `pool_shares += quantity`, `pool_cost += gross_eur + fees_eur`. Counted in `total_purchase_outflow_eur`. |
| `SHARE_ACQUISITION` (txn_type=BUY, INCOMPLETE) | `+quantity` to `unpaid_shares` | No pool cost. Zero-cost badge displayed. |
| `CASH_TOP_UP` (txn_type=BUY, qty=0) | No share change (qty=0) | `pool_cost += gross_eur + fees_eur` (adds cost with zero shares — increases average cost of existing pool). Wait — this is wrong. |

**CASH_TOP_UP correction:** A BUY with `quantity = "0"` and `cost_basis_status = "INCOMPLETE"` adds zero shares and zero cost (existing INCOMPLETE logic skips pool cost). This is correct — the top-up cash should NOT auto-inflate the cost pool. When the user resolves cost basis for the SHARE_ACQUISITION leg, they may incorporate the top-up amount there manually. This preserves the Danny consolidation invariant: "top-up cash must not be conflated with cost basis."

**Revised CASH_TOP_UP mapping:**

| Field | Value | Rationale |
|---|---|---|
| `txn_type` | `BUY` | Cash outflow for acquisition-related purpose |
| `quantity` | `"0"` | No shares acquired by this leg |
| `cost_basis_status` | `INCOMPLETE` | Cost attribution pending user decision |
| `gross.eur_amount` | Top-up amount in EUR | Records the outflow |
| `notes` | `"Cash top-up for scrip rounding"` | Human-readable |

Holdings effect: adds to `unpaid_shares` by 0 (no-op), records outflow in purchase total but NOT in CMP pool cost. The amount appears in `total_purchase_outflow_eur` as a trackable outflow. User attributes cost basis to SHARE_ACQUISITION leg when resolving.

### H.3.6 Backend — Model and API Changes

#### New Backend Model

```python
# In models.py — new enum and fields
class CaLegType(str, Enum):
    CASH_DIVIDEND = "CASH_DIVIDEND"
    RIGHTS_SOLD = "RIGHTS_SOLD"
    SHARE_ACQUISITION = "SHARE_ACQUISITION"
    CASH_TOP_UP = "CASH_TOP_UP"

class CaEventType(str, Enum):
    CASH_DIVIDEND = "CASH_DIVIDEND"
    DIVIDEND_WITH_SCRIP = "DIVIDEND_WITH_SCRIP"
    SCRIP_DIVIDEND = "SCRIP_DIVIDEND"
    RIGHTS_ISSUE = "RIGHTS_ISSUE"

# ManualMovementCreate — add optional fields
class ManualMovementCreate(BaseModel):
    # ... existing fields ...
    ca_group_id: Optional[str] = None        # set by server for grouped creation
    ca_leg_type: Optional[str] = None        # CASH_DIVIDEND | RIGHTS_SOLD | etc.
    ca_event_type: Optional[str] = None      # DIVIDEND_WITH_SCRIP | etc.
    ca_group_seq: Optional[int] = None       # ordering within group
```

#### New API Endpoint

```
POST /api/portfolio/corporate-actions
Content-Type: application/json

{
  "event_type": "DIVIDEND_WITH_SCRIP",
  "security_id": "XLON:ULVR",
  "account_id": "heytrade_main",
  "ex_dividend_date": "2024-03-07",      // optional but recommended
  "payment_date": "2024-03-28",          // required
  "notes": "Unilever Q1 2024 scrip dividend",
  "legs": [
    {
      "leg_type": "CASH_DIVIDEND",
      "trade_date": "2024-03-28",
      "gross": { "amount": "180.00", "currency": "GBP", "eur_amount": "209.79" },
      "fees": null,
      "withholding": {
        "source": { "country": "GB", "amount_eur": "0" },
        "destination": { "country": "ES", "amount_eur": "39.86" }
      },
      "fx": { "rate": "1.1655", "rate_source": "ECB" },
      "notes": "Cash portion"
    },
    {
      "leg_type": "SHARE_ACQUISITION",
      "trade_date": "2024-03-28",
      "quantity": "9",
      "gross": { "amount": "0", "currency": "GBP", "eur_amount": "0" },
      "cost_basis_status": "INCOMPLETE",
      "fx": { "rate": "1.1655", "rate_source": "ECB" },
      "notes": "FMV: 24.75 GBP/share (broker statement). Cost basis pending."
    },
    {
      "leg_type": "RIGHTS_SOLD",
      "trade_date": "2024-03-28",
      "quantity": "3",
      "gross": { "amount": "67.50", "currency": "GBP", "eur_amount": "78.67" },
      "fees": { "total": "2.00", "currency": "GBP", "total_eur": "2.33" },
      "withholding": null,
      "fx": { "rate": "1.1655", "rate_source": "ECB" },
      "notes": "Residual rights sold"
    },
    {
      "leg_type": "CASH_TOP_UP",
      "trade_date": "2024-03-28",
      "quantity": "0",
      "gross": { "amount": "4.95", "currency": "GBP", "eur_amount": "5.77" },
      "cost_basis_status": "INCOMPLETE",
      "fx": { "rate": "1.1655", "rate_source": "ECB" },
      "notes": "Cash top-up for scrip rounding"
    }
  ]
}
```

**Response:**

```json
{
  "ca_group_id": "cag_abc123",
  "event_type": "DIVIDEND_WITH_SCRIP",
  "movements": [
    { "id": "mvt_leg1", "ca_leg_type": "CASH_DIVIDEND", ... },
    { "id": "mvt_leg2", "ca_leg_type": "SHARE_ACQUISITION", ... },
    { "id": "mvt_leg3", "ca_leg_type": "RIGHTS_SOLD", ... },
    { "id": "mvt_leg4", "ca_leg_type": "CASH_TOP_UP", ... }
  ]
}
```

#### Server Logic

1. Validate all legs individually (same validation as `create_manual_movement`).
2. Generate one `ca_group_id = f"cag_{uuid4().hex}"`.
3. Map each leg to `ledger_txn`:
   - `CASH_DIVIDEND` → `txn_type = "DIVIDEND"`
   - `RIGHTS_SOLD` → `txn_type = "SELL"`, `sales_type = "DERECHOS"`
   - `SHARE_ACQUISITION` → `txn_type = "BUY"`
   - `CASH_TOP_UP` → `txn_type = "BUY"`, `quantity = "0"`, `cost_basis_status = "INCOMPLETE"`
4. Set `ca_group_id`, `ca_leg_type`, `ca_event_type`, `ca_group_seq` on each.
5. Create all movements in sequence (same account_id → same partition → can use batch if available, otherwise sequential upserts).
6. If any leg fails validation, reject the entire request (all-or-nothing).

### H.3.7 Correction of Corporate-Action Groups

**Leg-level correction:** Individual legs within a group are correctable via the existing `POST /movements/{id}/correct` endpoint. The replacement inherits `ca_group_id`, `ca_leg_type`, `ca_group_seq`. The original is SUPERSEDED as usual.

**Event-level void:** A new endpoint to void the entire group:

```
POST /api/portfolio/corporate-actions/{ca_group_id}/void
{ "account_id": "...", "reason": "..." }
```

Voids ALL active legs in the group. This is equivalent to voiding each leg individually but enforces atomicity.

**No event-level correction:** Correcting the entire group at once is deferred to Phase H-β (requires the full ca_event model). Users correct individual legs.

### H.3.8 UI — Corporate-Action Creation Flow

**Entry point:** "Add Movement" dialog → new tab/type: `Corporate Action` alongside BUY, SELL, DIVIDEND, TRANSFER.

**Step 1: Event Type Selection**

```
What type of corporate action?
  ○ Cash Dividend (simple — single cash leg)         → redirects to existing DIVIDEND form
  ○ Dividend with Scrip Election (cash + shares)     → multi-leg wizard
  ○ Scrip Dividend (shares only, no cash)            → multi-leg wizard
  ○ Rights Issue                                     → multi-leg wizard
```

**Step 2: Common Fields**

```
Security *        [dropdown]
Account           [dropdown]
Payment date *    [date picker]
Ex-dividend date  [date picker, optional]
Notes             [textarea]
```

**Step 3: Leg Builder**

Dynamic section where user adds/removes legs. Pre-populated based on event type:

- **Dividend with Scrip:** Pre-adds CASH_DIVIDEND + SHARE_ACQUISITION. User may add RIGHTS_SOLD and/or CASH_TOP_UP.
- **Scrip Dividend:** Pre-adds SHARE_ACQUISITION. User may add CASH_TOP_UP.
- **Rights Issue:** Pre-adds SHARE_ACQUISITION. User may add RIGHTS_SOLD and/or CASH_TOP_UP.

Each leg is a collapsible card:

```
┌─ Leg 1: Cash Dividend ──────────────────────┐
│  Gross amount *     [number]                 │
│  Currency           [text]                   │
│  [FxHelper]                                  │
│  Withholding (source):                       │
│    Country [__]  Amount € [____]  Rate [derived] │
│  Withholding (destination):                  │
│    ○ Not captured  ○ Zero  ○ Value:          │
│    Country [__]  Amount € [____]  Rate [derived] │
│  Fees               [number]                 │
└──────────────────────────────────────────────┘

┌─ Leg 2: Share Acquisition ──────────────────┐
│  Shares received *  [number]                 │
│  Cost basis status  [COMPLETE / INCOMPLETE]  │
│  Cost basis (€)     [number, if COMPLETE]    │
│  FMV per share      [number, informational]  │
│  Currency           [text]                   │
│  [FxHelper]                                  │
└──────────────────────────────────────────────┘

┌─ Leg 3: Rights Sold (optional) ─────────────┐
│  [+ Add Rights Sold leg]                     │
│  Rights units       [number]                 │
│  Sale proceeds *    [number]                 │
│  Fees               [number]                 │
│  Withholding        [same as dividend]       │
│  Currency           [text]                   │
│  [FxHelper]                                  │
└──────────────────────────────────────────────┘

┌─ Leg 4: Cash Top-Up (optional) ─────────────┐
│  [+ Add Cash Top-Up leg]                     │
│  Amount paid *      [number]                 │
│  Currency           [text]                   │
│  [FxHelper]                                  │
│  ℹ Cost attribution is separate from share   │
│    cost basis. This records the outflow only. │
└──────────────────────────────────────────────┘
```

**Step 4: Summary & Confirm**

```
Corporate Action Summary
━━━━━━━━━━━━━━━━━━━━━━━
Security:  ULVR — Unilever PLC
Type:      Dividend with Scrip Election
Date:      2024-03-28

Legs:
  1. Cash Dividend:       +€209.79 gross, −€39.86 WHT = €169.93 net
  2. Share Acquisition:   +9 shares (cost basis: pending)
  3. Rights Sold:         +€78.67 gross, −€2.33 fees = €76.34 net
  4. Cash Top-Up:         −€5.77

Net cash flow: €169.93 + €76.34 − €5.77 = €240.50
Share change:  +9

[Cancel]  [Create Corporate Action]
```

### H.3.9 Displaying Corporate-Action Groups

**Movements table:** Legs of a group are displayed as linked rows. Visual indicator (group icon + `ca_group_id` badge). Clicking any leg shows the group context in `MovementDetailDialog`.

**Movement detail:** When viewing a leg that belongs to a group, show a "Corporate Action Group" section listing all sibling legs with their type, amount, and status.

**Holdings:** No special display. Each leg is processed individually per §H.3.5.

### H.3.10 Migration Path to Phase H-β

When the full `ca_event`/`ca_leg` model is implemented:

1. Query all `ledger_txn` documents where `ca_group_id IS NOT NULL`.
2. Group by `ca_group_id`.
3. For each group: create a `ca_event` parent document from the group metadata.
4. Convert each `ledger_txn` to a `ca_leg` document with the proper `leg_type`.
5. Mark original `ledger_txn` documents as migrated (or replace entirely).

The `ca_group_id` serves as the stable link between Phase H-α and H-β.

---

## H.4 Minimum Viable Legs — What's Required vs Optional

Not all corporate actions have all four legs. The minimum viable configurations:

| Event type | Required legs | Optional legs |
|---|---|---|
| `CASH_DIVIDEND` | 1× CASH_DIVIDEND | (none — use simple DIVIDEND form) |
| `DIVIDEND_WITH_SCRIP` | 1× CASH_DIVIDEND + 1× SHARE_ACQUISITION | RIGHTS_SOLD, CASH_TOP_UP |
| `SCRIP_DIVIDEND` | 1× SHARE_ACQUISITION | CASH_TOP_UP |
| `RIGHTS_ISSUE` | 1× SHARE_ACQUISITION | RIGHTS_SOLD, CASH_TOP_UP |

**Validation:** At least one required leg per event type. Each leg must pass individual validation (same as standalone movement creation).

---

## H.5 Affected Files and Agent Assignments (Amendment H)

### H.5.1 Backend Changes (Livingston)

| File | Change | Scope |
|---|---|---|
| `backend/src/portfolio/models.py` | Add `CaLegType`, `CaEventType` enums; add optional `ca_group_id`, `ca_leg_type`, `ca_event_type`, `ca_group_seq` to `ManualMovementCreate`; add `CorporateActionCreateRequest` model | Moderate |
| `backend/src/portfolio/cosmos_portfolio.py` | Add `create_corporate_action()` method (creates linked `ledger_txn` docs); add `void_corporate_action_group()` method | Large |
| `backend/web/portfolio_routes.py` | Add `POST /api/portfolio/corporate-actions` and `POST /api/portfolio/corporate-actions/{ca_group_id}/void` | Moderate |
| `backend/src/portfolio/holdings_service.py` | No changes (CASH_TOP_UP with qty=0 + INCOMPLETE already handled) | None |

### H.5.2 Frontend Changes (Rusty)

| File | Change | Scope |
|---|---|---|
| `frontend/src/components/AddMovementDialog.tsx` | Add `CORPORATE_ACTION` tab/type; multi-leg wizard (Steps 1–4 from §H.3.8) | Large |
| `frontend/src/types/portfolio.ts` | Add `CaLegType`, `CaEventType` types; add `ca_group_id`, `ca_leg_type` to `LedgerMovement`; add `CorporateActionRequest` type | Moderate |
| `frontend/src/lib/portfolio-api.ts` | Add `createCorporateAction()` and `voidCorporateAction()` API calls | Small |
| `frontend/src/components/MovementDetailDialog.tsx` | Show "Corporate Action Group" section for linked legs | Moderate |
| `frontend/src/components/PortfolioMovementsTable.tsx` | Group icon for movements with `ca_group_id`; collapsible group view | Moderate |
| `frontend/src/components/AddMovementDialog.tsx` | WHT derivation: `rate_pct` auto-computed from amount/gross (§H.1) in DividendForm | Small |
| `frontend/src/components/MovementCorrectionDialog.tsx` | WHT derivation in correction; group-aware correction (preserve `ca_group_id`) | Moderate |

### H.5.3 Test Matrix (Livingston / Basher)

| # | Test | Scope |
|---|---|---|
| H-T1 | Create DIVIDEND_WITH_SCRIP with 4 legs → all 4 `ledger_txn` docs created with shared `ca_group_id` | Backend |
| H-T2 | Holdings: CASH_DIVIDEND leg → no share change, counted in dividends | Backend |
| H-T3 | Holdings: SHARE_ACQUISITION leg (COMPLETE) → +shares, pool cost updated | Backend |
| H-T4 | Holdings: SHARE_ACQUISITION leg (INCOMPLETE) → +unpaid_shares, zero pool cost | Backend |
| H-T5 | Holdings: RIGHTS_SOLD leg → no share change, counted in rights_proceeds | Backend |
| H-T6 | Holdings: CASH_TOP_UP leg (qty=0, INCOMPLETE) → no share change, no pool cost | Backend |
| H-T7 | Correct one leg within group → replacement has same `ca_group_id` | Backend |
| H-T8 | Void entire group → all legs VOIDED | Backend |
| H-T9 | Reject group with missing required legs | Backend |
| H-T10 | Reject group where leg fails individual validation | Backend |
| H-T11 | WHT rate derivation: amount_eur=30, gross_eur=200 → rate_pct=15.00 | Frontend |
| H-T12 | WHT rate derivation: gross=0 → rate_pct=0 | Frontend |
| H-T13 | Multi-leg wizard UI renders correct pre-populated legs per event type | Frontend |
| H-T14 | Summary step shows correct net cash flow and share change | Frontend |

---

## H.6 Revised Acceptance Criteria (Full Contract — Parts A through H)

### Original Criteria (A-1 through A-10, C-1 through C-15, F-1 through F-9, E-1 through E-6, G-1 through G-18)

All remain unchanged.

### New Criteria (Amendment H)

| # | Criterion |
|---|---|
| H-1 | Dividend creation form: WHT `rate_pct` is auto-computed from `amount_eur / gross_eur × 100` and displayed read-only |
| H-2 | Dividend correction form: same WHT rate derivation behavior |
| H-3 | SELL correction form: same WHT rate derivation when withholding is present |
| H-4 | WHT rate shows `0` when gross is zero (no division error) |
| H-5 | `POST /api/portfolio/corporate-actions` creates N linked `ledger_txn` documents with shared `ca_group_id` |
| H-6 | Each leg maps to correct `txn_type`: CASH_DIVIDEND→DIVIDEND, RIGHTS_SOLD→SELL(DERECHOS), SHARE_ACQUISITION→BUY, CASH_TOP_UP→BUY(qty=0,INCOMPLETE) |
| H-7 | If any leg in the group fails validation, the entire group is rejected (no partial creation) |
| H-8 | Individual leg correction via existing `/movements/{id}/correct` preserves `ca_group_id` and `ca_leg_type` on replacement |
| H-9 | `POST /api/portfolio/corporate-actions/{ca_group_id}/void` voids all active legs in group |
| H-10 | Holdings service: CASH_DIVIDEND leg → no share change, counted in total_dividends_eur |
| H-11 | Holdings service: RIGHTS_SOLD leg → no share change, counted in rights_proceeds_eur (existing DERECHOS pathway) |
| H-12 | Holdings service: SHARE_ACQUISITION (COMPLETE) → +shares to pool, pool_cost increased |
| H-13 | Holdings service: SHARE_ACQUISITION (INCOMPLETE) → +unpaid_shares, zero pool cost (zero-cost badge) |
| H-14 | Holdings service: CASH_TOP_UP (qty=0, INCOMPLETE) → no share change, no pool cost change |
| H-15 | UI: "Corporate Action" type in Add Movement dialog with multi-leg wizard |
| H-16 | UI: Summary step shows computed net cash flow and net share change before confirmation |
| H-17 | UI: Movement table shows group indicator for legs belonging to a `ca_group_id` |
| H-18 | UI: Movement detail for a grouped leg shows sibling legs in a "Corporate Action Group" section |
| H-19 | DIVIDEND_WITH_SCRIP requires at minimum: 1× CASH_DIVIDEND + 1× SHARE_ACQUISITION |
| H-20 | SCRIP_DIVIDEND requires at minimum: 1× SHARE_ACQUISITION |
| H-21 | Existing standalone DIVIDEND/BUY/SELL movements (no `ca_group_id`) continue to work unchanged |
| H-22 | `tsc --noEmit` and all existing backend tests pass after changes |

---

# Amendment I: Symbol Detail — Options / Stocks Section Reorganization

**Date:** 2026-09-06  
**Author:** Danny (Lead Architect)  
**Status:** PROPOSED — additive amendment  
**Trigger:** User requirement: Symbol Detail must organize content into "Options" and "Stocks" sections. User explicitly prefers this information architecture.  
**References:** `.squad/decisions/inbox/copilot-directive-20260906-symbol-detail-options-stocks.md`

---

## I.1 Current State Analysis

### I.1.1 Current Symbol Detail Page Layout (`/symbols/[symbol]/page.tsx`)

The page currently renders a flat stack of sections (top to bottom):

```
1. Security badge (identity)
2. Toolbar (SymbolActions: CC/CSP/Buy toggles, pause)
3. TradingView symbol info
4. RT Chart
5. SymbolSummary (options summary: in_calls, put_exposure, etc.)
6. PortfolioHoldingsCard (shares, avg cost, invested, dividends — if has portfolio)
7. SymbolMovementsTable ("Recent Movements" — last 5, minimal columns: date/type/qty/amount)
8. PositionsTable (option positions with status filters)
9. AddPositionForm (add option position)
10. SymbolPlansTable (action plans)
11. RecentActivities (agent decision history)
```

### I.1.2 Problems

1. **Options and stocks content is interleaved** — PortfolioHoldingsCard (#6) and SymbolMovementsTable (#7) sit between the options-related SymbolSummary (#5) and PositionsTable (#8). No clear domain separation.
2. **Stock movements are underserved** — "Recent Movements" shows only 5 rows with 4 columns (date, type badge, qty, gross EUR). No fees, no withholding, no net, no sale type. The user cannot see the full transaction history.
3. **No organizational anchor** — there is no section header or container grouping related content. Options positions and agent activities are visually disconnected despite being part of the same domain.

---

## I.2 Design Decision: Stacked Sections (Not Tabs)

### I.2.1 Options Considered

| Approach | Pros | Cons |
|---|---|---|
| **Tabs** (Options / Stocks) | Compact; reduces scroll | Hides one domain entirely; breaks deep-link expectations; shared chart/summary ambiguous placement |
| **Stacked collapsible sections** | Both visible; scannable; matches existing page conventions | Longer page; more scroll |

### I.2.2 Decision: Stacked Sections

**Rationale:**
1. The existing Symbol Detail page is a single scrollable view. Every other section (Summary, Plans, Activities) uses the same stacked-card pattern. Tabs would be an inconsistent pattern requiring new navigation infrastructure.
2. Users often own both options AND stocks for the same symbol. Hiding one behind a tab means they can't see both at a glance.
3. The shared header (badge, toolbar, chart) applies to both domains — it would be awkward above tabs.
4. Collapsible sections give the user control over page length without hiding content by default.

**Layout:** Two titled section containers with clear visual boundaries. Both expanded by default. Collapsible via click on section header.

---

## I.3 New Page Layout

### I.3.1 Revised Section Order

```
── Shared Header ──────────────────────────────────────
1. Security badge (identity)
2. Toolbar (SymbolActions)
3. TradingView symbol info
4. RT Chart

── Options Section ────────────────────────────────────
5. Section header: "Options" with collapse toggle
   5a. SymbolSummary (in_calls, put_exposure, etc.)
   5b. PositionsTable (option positions)
   5c. AddPositionForm
   5d. RecentActivities (agent decisions)

── Stocks Section ─────────────────────────────────────
6. Section header: "Stocks" with collapse toggle
   6a. PortfolioHoldingsCard (shares, avg cost, invested, dividends)
   6b. StockTransactionsTable (full BUY/SELL/DIVIDEND history — NEW component)

── Other ──────────────────────────────────────────────
7. SymbolPlansTable (action plans — applies to both domains)
```

### I.3.2 Visibility Rules

| Section | Shown when |
|---|---|
| Options | `hasAgentContent` OR `positions.length > 0` OR `activities.length > 0` — same as current |
| Stocks | `hasPortfolio` (i.e., `d.portfolio != null`) |
| Plans | Always (existing behavior) |

When **neither** Options nor Stocks has content (e.g., a newly-added watchlist symbol with no positions and no portfolio), the page shows the shared header, chart, and plans only — no empty section containers.

### I.3.3 Section Container Component

A reusable `DetailSection` component:

```tsx
interface DetailSectionProps {
  title: string;           // "Options" or "Stocks"
  badge?: string;          // e.g., count
  defaultOpen?: boolean;   // true
  children: React.ReactNode;
}
```

Visual design:
- Full-width card with rounded border (`border border-border rounded-[var(--radius)]`)
- Header bar with section title (bold, slightly larger than subsection headers), optional badge, collapse chevron
- When collapsed: only header visible, no content rendered
- When expanded: content stacked inside with existing spacing

---

## I.4 Stocks Section — Enhanced Transaction History

### I.4.1 Problem with Current `SymbolMovementsTable`

The current component:
- Shows only 5 movements (hardcoded `limit=5` in backend query)
- Shows only 4 columns: Date, Type badge, Qty, Amount (EUR) — `gross_eur` only
- No fees, no withholding, no net proceeds, no sale type, no cost basis status
- No pagination, no type filter
- "View all →" links to `/portfolio/movements?security_id=...` (a separate page)

### I.4.2 New `StockTransactionsTable` Component

**Replaces** `SymbolMovementsTable` inside the Stocks section. Full-featured, client-side fetched.

#### Columns

| Column | BUY | SELL | DIVIDEND | Notes |
|---|---|---|---|---|
| Date | `trade_date` | `trade_date` | `trade_date` | YYYY-MM-DD |
| Type | Badge: Buy | Badge: Sell — Stocks/Rights | Badge: Dividend | Sale type via `SALES_TYPE_LABELS` (Amendment G) |
| Qty | `quantity` | `quantity` | `quantity` or "—" | Null for DIVIDEND if not captured |
| Gross (EUR) | `gross.eur_amount` | `gross.eur_amount` | `gross.eur_amount` | Trade value / gross dividend |
| Fees (EUR) | `fees.total_eur` | `fees.total_eur` | `fees.total_eur` | Shown when > 0 |
| WHT (EUR) | — | — or `wht.source.amount_eur` | `wht.source.amount_eur` + `wht.destination.amount_eur` | Withholding total |
| Net (EUR) | `net.eur_amount` | `net.eur_amount` | `net.eur_amount` | Server-computed total |
| Account | Account name | Account name | Account name | From account_id lookup |

**Column visibility:**
- Fees column: shown if ANY movement in the current page has non-zero fees
- WHT column: shown if ANY movement has non-zero withholding
- Account column: shown if movements span multiple accounts
- This keeps the table compact for simple portfolios while fully informative for complex ones

#### Features

| Feature | Behavior |
|---|---|
| **Pagination** | Client-side via `getMovements()` API with `security_id` filter, `limit=20`, `offset` |
| **Type filter** | Pill buttons: All / Buy / Sell / Dividend (filters `txn_type` param) |
| **Sort** | Default: newest first (`trade_date` DESC, same as current) |
| **Row click** | Opens `MovementDetailDialog` (existing component) |
| **Correction access** | Via MovementDetailDialog → "Correct movement" button (existing flow) |
| **Empty state** | "No stock transactions recorded for this security." |
| **Corporate action groups** | Grouped rows with visual indicator per Amendment H §H.3.9 |

#### Data Source

The component calls the **existing** `GET /api/portfolio/movements` endpoint with `security_id` filter:

```typescript
const data = await getMovements({
  security_id: securityId,
  txn_type: typeFilter || undefined,
  limit: 20,
  offset: page * 20,
});
```

This returns full `LedgerMovement` objects — all fields (gross, fees, withholding, net, sales_type, etc.) are already available. **No backend endpoint changes needed.**

### I.4.3 Backend — Enriched `RecentMovement` Wire Shape (Optional Optimization)

The current `RecentMovement` wire shape (used by `_map_recent_movement`) is minimal:

```python
# Current (keep for backward compat)
{"id", "txn_type", "trade_date", "quantity", "gross_eur"}
```

Since `StockTransactionsTable` calls the full movements endpoint directly, the minimal `RecentMovement` shape in the symbol-detail response is no longer the primary data source. We have two options:

**Option A (recommended):** Keep the current minimal `recent_movements` in the symbol-detail response (for the PortfolioHoldingsCard quick glance). The new `StockTransactionsTable` fetches full data independently via the movements API. No backend changes.

**Option B:** Enrich `RecentMovement` to include fees/net/withholding/sales_type. Adds payload weight to every symbol-detail call even for users who don't expand the Stocks section.

**Decision: Option A.** The `StockTransactionsTable` is a client component that fetches on mount. The symbol-detail SSR response stays lightweight. The `recent_movements` field may be removed from PortfolioSection in a future cleanup once StockTransactionsTable is the sole consumer.

---

## I.5 Section Component Hierarchy

### I.5.1 New Components

| Component | Type | Purpose |
|---|---|---|
| `DetailSection` | Client | Collapsible section container (reusable) |
| `StockTransactionsTable` | Client | Full BUY/SELL/DIVIDEND transaction history with pagination, filters |

### I.5.2 Modified Components

| Component | Change |
|---|---|
| `symbols/[symbol]/page.tsx` | Restructure layout into Options/Stocks sections using `DetailSection`; move children into correct sections |
| `SymbolMovementsTable` | **Deprecated** — replaced by `StockTransactionsTable` inside Stocks section. Keep file for backward compat but no longer rendered on symbol detail page. |

### I.5.3 Unchanged Components

| Component | Reason |
|---|---|
| `PortfolioHoldingsCard` | Moves into Stocks section but content unchanged |
| `PositionsTable` | Moves into Options section but content unchanged |
| `AddPositionForm` | Moves into Options section |
| `RecentActivities` | Moves into Options section |
| `SymbolSummary` | Moves into Options section |
| `SymbolPlansTable` | Stays below both sections |
| `TradingViewSymbolInfo`, `RtChart` | Stay in shared header |

---

## I.6 Affected Files and Agent Assignments (Amendment I)

### I.6.1 Frontend Changes (Rusty)

| File | Change | Scope |
|---|---|---|
| `frontend/src/app/symbols/[symbol]/page.tsx` | Restructure into Options/Stocks sections; wrap in `DetailSection` components | Large |
| `frontend/src/components/DetailSection.tsx` | **NEW** — reusable collapsible section container | Small |
| `frontend/src/components/StockTransactionsTable.tsx` | **NEW** — full BUY/SELL/DIVIDEND history with pagination, type filter, enhanced columns | Large |
| `frontend/src/components/SymbolMovementsTable.tsx` | Mark deprecated; no longer imported by symbol detail page | Trivial |

### I.6.2 Backend Changes

**None.** The full movements endpoint already exists and supports `security_id` filtering. No new endpoints or response changes required.

### I.6.3 Type Changes

| File | Change |
|---|---|
| `frontend/src/types/symbol-detail.ts` | No changes needed (StockTransactionsTable uses `LedgerMovement` from `portfolio.ts`) |

### I.6.4 Test Changes

| File | Change | Agent |
|---|---|---|
| Frontend tests | Test DetailSection collapse/expand; StockTransactionsTable renders columns, pagination, type filter; Options section visibility rules | Rusty |
| `backend/tests/test_unified_symbol_detail.py` | No changes (backend response unchanged) | — |

---

## I.7 Acceptance Criteria (Amendment I)

| # | Criterion |
|---|---|
| I-1 | Symbol Detail page has a titled **"Options"** section containing SymbolSummary, PositionsTable, AddPositionForm, and RecentActivities |
| I-2 | Symbol Detail page has a titled **"Stocks"** section containing PortfolioHoldingsCard and StockTransactionsTable |
| I-3 | Both sections are collapsible; both default to expanded |
| I-4 | Options section is hidden when no positions, no activities, and `hasAgentContent` is false |
| I-5 | Stocks section is hidden when `portfolio` is null (watchlist-only symbols) |
| I-6 | Plans section remains below both sections and is always visible |
| I-7 | Shared header (badge, toolbar, chart) remains above both sections |
| I-8 | `StockTransactionsTable` shows columns: Date, Type (with sale subtype), Qty, Gross EUR, Fees EUR, WHT EUR, Net EUR |
| I-9 | Fees column auto-hidden when all fees are zero; WHT column auto-hidden when no withholding exists |
| I-10 | Account column shown when movements span multiple accounts |
| I-11 | `StockTransactionsTable` has type-filter pills: All / Buy / Sell / Dividend |
| I-12 | `StockTransactionsTable` paginates via the existing `GET /api/portfolio/movements?security_id=...` endpoint (20 per page) |
| I-13 | Row click in `StockTransactionsTable` opens `MovementDetailDialog` (existing component) |
| I-14 | SELL movements display sale type as `Stocks` / `Rights` (using `SALES_TYPE_LABELS` from Amendment G) |
| I-15 | Corporate-action group legs (Amendment H) display with group indicator |
| I-16 | `DetailSection` component is reusable (not hardcoded to Options/Stocks) |
| I-17 | No backend endpoint changes; `test_unified_symbol_detail.py` passes unchanged |
| I-18 | `tsc --noEmit` passes with zero errors |
| I-19 | `SymbolMovementsTable` is no longer rendered on the symbol detail page (deprecated) |
