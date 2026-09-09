# Design: Option Ledger Movements for Positions, Warnings, and Economics

**Date:** 2026-09-09  
**Author:** Danny  
**Requested by:** dsanchor  
**Status:** PROPOSED — design only, no implementation code in this task.

**Authoritative sources reviewed:**
- Position persistence lifecycle: `backend/src/cosmos_db.py:551-654`
- Frozen portfolio enums/models: `backend/src/portfolio/models.py:22-27`, `backend/src/portfolio/models.py:343-372`
- Manual movement/correction/reassignment services: `backend/src/portfolio/cosmos_portfolio.py:172-280`, `backend/src/portfolio/cosmos_portfolio.py:604-739`, `backend/src/portfolio/cosmos_portfolio.py:741-870`, `backend/src/portfolio/cosmos_portfolio.py:1366-1461`, `backend/src/portfolio/cosmos_portfolio.py:1683-1724`, `backend/src/portfolio/cosmos_portfolio.py:1763-1996`
- Holdings/FIFO rules: `backend/src/portfolio/holdings_service.py:104-258`
- Import preview movement handling: `backend/src/portfolio/import_service.py:565-579`, `backend/src/portfolio/import_service.py:602-720`
- Security Master / symbol-config prerequisites: `backend/src/portfolio/cosmos_securities.py:64-135`, `backend/src/portfolio/symbol_config_sync.py:35-110`
- Portfolio routes: `backend/web/portfolio_routes.py:533-564`, `backend/web/portfolio_routes.py:781-820`
- Current options economics builders/endpoints: `backend/web/app.py:233-449`, `backend/web/app.py:452-530`, `backend/web/app.py:1108-1285`, `backend/web/app.py:1558-1572`, `backend/web/app.py:3634-3638`
- Current options / overview frontend contracts: `frontend/src/types/economics.ts:1-82`, `frontend/src/components/EconomicsView.tsx:96-121`, `frontend/src/components/EconomicsView.tsx:377-659`, `frontend/src/components/EconomicsOverviewView.tsx:97-155`, `frontend/src/components/EconomicsOverviewView.tsx:240-279`, `frontend/src/components/EconomicsOverviewView.tsx:381-500`
- Current positions / movement frontend contracts: `frontend/src/types/symbol-detail.ts:22-38`, `frontend/src/types/portfolio.ts:5-166`, `frontend/src/types/portfolio.ts:293-306`, `frontend/src/components/PositionsTable.tsx:64-220`, `frontend/src/components/PositionDetail.tsx:853-873`, `frontend/src/components/PortfolioMovementsTable.tsx:18-48`, `frontend/src/components/AddMovementDialog.tsx:16-24`, `frontend/src/components/AddMovementDialog.tsx:512-602`, `frontend/src/components/MovementDetailDialog.tsx:14-20`, `frontend/src/components/MovementDetailDialog.tsx:253-360`, `frontend/src/components/EconomicsTabs.tsx:16-33`, `frontend/src/app/api/economics/route.ts:4-20`, `frontend/src/app/api/economics/overview/route.ts:4-14`

---

## Recommendation Summary

Introduce **four new flat `TxnType` values**:

- `CALL_SELL`
- `CALL_BUY`
- `PUT_SELL`
- `PUT_BUY`

These stay inside the existing `ledger_txn` document family, but are treated as **inventory-neutral cash-flow movements**:

- they never affect share lots,
- they never count toward stock purchase/sale totals,
- they always carry a required `option_position_id`,
- they store **real traded totals** in original currency plus EUR-converted amounts,
- they become the **only source** for Options/Economics aggregates.

For assignments, keep the stock leg as existing `BUY`/`SELL`, but allow an **optional informational `option_position_id` link** on that stock movement so warnings and traceability work without affecting FIFO.

I recommend **not** silently auto-creating a Security Master entry from the ledger write path. If a symbol has no resolvable `security_id`, option movement creation should fail with a structured precondition error and the UI should offer a **prefilled create-security flow**.

---

## 1. Data Model

### 1.1 New `TxnType` values

Current frozen enum only contains `BUY`, `SELL`, `DIVIDEND`, `TRANSFER_OUT`, `TRANSFER_IN` in `backend/src/portfolio/models.py:22-27` and the frontend mirror in `frontend/src/types/portfolio.ts:5-6`.

### Recommended enum extension

Add these flat values to the enum contract:

```text
CALL_SELL
CALL_BUY
PUT_SELL
PUT_BUY
```

### Why this naming

This matches the repo's existing flat-style enum contract better than a compound `{ option_type, side }` model:

- explicit and query-friendly,
- easy to whitelist/exclude everywhere `TxnType` is consumed,
- easy to mirror in frontend unions and filter pills,
- avoids partially-valid states like `type=call` + `side=unknown`.

### 1.2 Ledger document shape

Do **not** create a second movement schema. Reuse existing `ledger_txn` fields from `ManualMovementCreate` / `LedgerMovement` (`backend/src/portfolio/models.py:343-372`, `frontend/src/types/portfolio.ts:119-166`) and add a small option-link extension.

#### Required fields for option movements

| Field | Requirement | Notes |
|---|---|---|
| `txn_type` | required | One of `CALL_SELL`, `CALL_BUY`, `PUT_SELL`, `PUT_BUY` |
| `security_id` | required | Underlying security, same as stock ledger/security master identity |
| `trade_date` | required | ISO date |
| `account_id` | required | Enables account filtering for linked positions |
| `quantity` | persisted as `"0"` | Inventory-neutral by design; this remains a share-quantity field, not contracts |
| `gross.amount` | required | **Real traded total** in original trade currency, typically USD |
| `gross.currency` | required | Usually `USD` |
| `gross.eur_amount` | required | EUR-converted gross |
| `fees.total` / `fees.total_eur` | optional but strongly recommended | Commission in native + EUR |
| `net.amount` / `net.eur_amount` | server-derived | SELL-like for opens, BUY-like for buybacks |
| `fx.rate` / `fx.rate_source` | required | Same pattern as current ledger FX |
| `option_position_id` | required | Many-to-one link to symbols-container position |

#### Recommended new option metadata fields

| Field | Required? | Applies to | Purpose |
|---|---|---|---|
| `option_position_id` | yes for option txns; optional for stock BUY/SELL | all linked option-related txns | canonical cross-container link |
| `option_link_kind` | yes for option txns; optional for assignment stock txns | all linked option-related txns | `OPEN_SELL`, `CLOSE_BUY`, `ASSIGNMENT_STOCK` |
| `option_type` | yes for option txns; optional for assignment stock txns | linked txns | denormalized `call` / `put` |
| `option_strike` | optional snapshot | linked txns | informational only |
| `option_expiration` | optional snapshot | linked txns | informational only |
| `option_symbol` | optional snapshot | linked txns | denormalized ticker for debugging/export |

This mirrors the repo's established flat metadata style (`transfer_*`, `ca_*`) in `frontend/src/types/portfolio.ts:148-165` and `backend/src/portfolio/cosmos_portfolio.py:1644-1658`, `backend/src/portfolio/cosmos_portfolio.py:1248-1282`.

### 1.3 Semantics by movement type

| `txn_type` | Economic meaning | Net formula | Inventory effect | Notes |
|---|---|---|---|---|
| `CALL_SELL` | covered-call opening premium received | SELL-like: `gross - fees` | none | `option_link_kind=OPEN_SELL` |
| `PUT_SELL` | cash-secured-put opening premium received | SELL-like: `gross - fees` | none | `option_link_kind=OPEN_SELL` |
| `CALL_BUY` | call buyback paid to close | BUY-like: `gross + fees` | none | `option_link_kind=CLOSE_BUY` |
| `PUT_BUY` | put buyback paid to close | BUY-like: `gross + fees` | none | `option_link_kind=CLOSE_BUY` |
| `BUY` + `option_position_id` | stock assignment from put | existing BUY semantics | **normal stock FIFO applies** | link is informational only |
| `SELL` + `option_position_id` | stock assignment from call | existing SELL semantics | **normal stock FIFO applies** | link is informational only |

### 1.4 USD vs EUR fields

Constraint A is resolved by **reusing** existing gross/fees/net objects rather than inventing another parallel amount schema:

- **USD premium/buyback used for RoC and annualized return** comes from `gross.amount` when `gross.currency == "USD"`.
- **EUR cash-flow / income metrics** come from `net.eur_amount`.
- **Commission** comes from `fees.total` and `fees.total_eur`.
- **FX provenance** comes from `fx.rate` and `fx.rate_source`.

That means no extra top-level `premium_usd` field is required; the existing `MoneyAmount` + `fees` + `fx` pattern is already the right storage model.

### 1.5 Assignment traceability link

For assigned positions, use the same optional `option_position_id` on the manually-entered stock `BUY` or `SELL` movement. This gives one consistent link field across all option-related movements while leaving FIFO untouched because FIFO code only keys on `txn_type`, `quantity`, and money fields today (`backend/src/portfolio/holdings_service.py:165-252`).

### 1.6 Security resolution policy

**Recommendation:** `security_id` remains a precondition, not an auto-created side effect of posting a movement.

Reasoning:
- `ensure_symbol_config()` explicitly requires that the `security_master` already exist (`backend/src/portfolio/symbol_config_sync.py:83-94`).
- `create_security()` today requires authoritative `ticker`, `company_name`, and `exchange_mic` (`backend/src/portfolio/cosmos_securities.py:64-135`).
- Silently inventing MIC/company_name from a raw option position is too risky for a frozen identity model.

### Precondition behavior

If a position's symbol cannot be resolved to an existing `security_id`:

- movement creation fails with a structured error like `missing_security_master_for_symbol`,
- response includes the symbol and any available `display_name` / `exchange` from the symbol config,
- UI offers a **prefilled create-security** flow, then retries creation.

That keeps Security Master explicit while still making pure-options symbols workable.

---

## 2. Position → Movement Linkage / Lookup Design

### 2.1 Position lifecycle baseline

Positions are embedded under symbol docs in the `symbols` container (`backend/src/cosmos_db.py:551-654`). They have `position_id`, `type`, `strike`, `expiration`, `status`, optional `close_reason`, and roll links.

### 2.2 Cardinality

Linkage is **many movements → one `position_id`**.

Supported explicitly:
- open leg + close leg on the same position,
- roll close on old `position_id` and roll open on new `position_id`,
- multiple partial fills on one position,
- multiple assignment-related stock movements on one assigned position.

### 2.3 Resolution strategy

Use a **backend-side bulk join in memory**, never per-position round trips.

#### Step A — collect in-scope positions

From symbol docs, build rows containing at minimum:

- `symbol`
- `position_id`
- `type`
- `status`
- `close_reason`
- `strike`
- `expiration`
- `opened_at`
- `closed_at`
- `rolled_from` / `rolled_to`
- `legacy_premium_present` (`source.premium != null`)
- `legacy_buyback_present` (`buyback_cost != null`)

#### Step B — resolve `symbol -> security_id`

Resolution order:

1. `symbol_doc.security_id` when present.
2. Exact Security Master lookup by ticker when there is one unambiguous active match.
3. Otherwise mark the position as `security_unresolved`.

Any unresolved position:
- cannot join to ledger movements,
- is excluded from account-scoped economics,
- contributes to coverage-gap counts,
- gets a position warning/UI badge.

#### Step C — bulk query relevant movements

Use two cross-partition portfolio queries, batched if necessary.

##### Query 1: option ledger legs

Fetch all active linked option movements for the in-scope security set:

```sql
SELECT * FROM c
WHERE c.doc_type = 'ledger_txn'
  AND NOT IS_DEFINED(c.deleted_at)
  AND (NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE')
  AND ARRAY_CONTAINS(@option_txn_types, c.txn_type)
  AND IS_DEFINED(c.option_position_id)
  AND ARRAY_CONTAINS(@security_ids, c.security_id)
```

Optional account filter clause:

```sql
AND ARRAY_CONTAINS(@account_ids, c.account_id)
```

##### Query 2: assignment stock movements

Fetch only stock BUY/SELL movements that explicitly link back to an option position:

```sql
SELECT * FROM c
WHERE c.doc_type = 'ledger_txn'
  AND NOT IS_DEFINED(c.deleted_at)
  AND (NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE')
  AND ARRAY_CONTAINS(@stock_txn_types, c.txn_type)
  AND IS_DEFINED(c.option_position_id)
  AND ARRAY_CONTAINS(@security_ids, c.security_id)
```

Again, apply `account_id` filter only when supplied.

### 2.4 In-memory indexes

Build these maps:

- `option_moves_by_position_id[position_id] -> [ledger_txn...]`
- `assignment_stock_by_position_id[position_id] -> [ledger_txn...]`
- `accounts_by_position_id[position_id] -> set(account_id)`

Then derive per-position aggregates/warnings from the maps.

### 2.5 Why not query by `position_id` only?

Because the source-of-truth object still lives under the symbol doc, and we also need:
- symbol filters,
- position status/type/expiration fields,
- unresolved-security coverage tracking.

So the right flow is **positions first, movements second, join in memory**.

### 2.6 Account filter behavior

When `account_id` is supplied on Options or Overview:

- monetary aggregates only include linked movements in those accounts,
- a position counts as **linked in scope** only if at least one linked movement survives the account filter,
- unlinked positions are excluded from the money totals because they have no attributable account,
- positions linked only in other accounts are also excluded from filtered totals.

Response metadata must expose both exclusions separately:

- `excluded_unlinked_positions`
- `excluded_positions_linked_only_outside_account_filter`

---

## 3. Warning Rule Engine

### 3.1 Goal

Support a long manual backfill period without treating every legacy position as an error, while still surfacing the exact missing movement expected by business rule.

### 3.2 Warning types

Add option-specific warning codes to the position/report DTOs (not necessarily to ledger `WarningType`, which is currently portfolio-import specific in `backend/src/portfolio/models.py:47-55`):

- `OPTION_SECURITY_UNRESOLVED`
- `OPTION_OPENING_SELL_MISSING`
- `OPTION_MANUAL_CLOSE_BUY_MISSING`
- `OPTION_ASSIGNMENT_STOCK_MISSING`
- `OPTION_ACCOUNT_FILTER_EXCLUDED_UNLINKED` (report/banner level, not row-level)

### 3.3 Exact per-position predicates

Let:

- `expected_open_txn = CALL_SELL if position.type == 'call' else PUT_SELL`
- `expected_close_txn = CALL_BUY if position.type == 'call' else PUT_BUY`
- `expected_assignment_stock_txn = SELL if position.type == 'call' else BUY`
- `has_opening_sell = any(m.txn_type == expected_open_txn for m in option_moves_by_position_id[position_id])`
- `has_closing_buy = any(m.txn_type == expected_close_txn for m in option_moves_by_position_id[position_id])`
- `has_assignment_stock = any(m.txn_type == expected_assignment_stock_txn for m in assignment_stock_by_position_id[position_id])`
- `legacy_premium_present = position.source.premium is present and numeric`

#### Rules

| Position state | Warning condition | Emit |
|---|---|---|
| any state | security cannot resolve to `security_id` | `OPTION_SECURITY_UNRESOLVED` |
| active, closed, rolled, expired, assigned | `legacy_premium_present && !has_opening_sell` | `OPTION_OPENING_SELL_MISSING` |
| `status == 'rolled'` | `!has_closing_buy` | `OPTION_MANUAL_CLOSE_BUY_MISSING` |
| `close_reason == 'manual'` | `!has_closing_buy` | `OPTION_MANUAL_CLOSE_BUY_MISSING` |
| `close_reason == 'assigned'` | `!has_assignment_stock` | `OPTION_ASSIGNMENT_STOCK_MISSING` |
| `close_reason == 'expired'` | no extra close-leg rule | none |
| active with no legacy premium and no linked opening sell | no warning; count as uncovered | none |

### 3.4 Important roll rule

`roll_position()` marks the old position `status="rolled"` and creates a new active position with a new `position_id` (`backend/src/cosmos_db.py:577-625`). The old rolled position therefore **must** be treated like a manual close for warning purposes even though `close_reason` is not currently populated.

### 3.5 Explicit non-rules

Phase 1 should **not** warn on:
- movement count mismatch,
- premium/buyback amount mismatch vs legacy fields,
- missing assignment quantity match,
- contract-multiplier reconciliation.

Those are false-precision traps because historical positions never tracked contracts durably.

### 3.6 UI surfaces

#### A. Symbol detail positions table (`frontend/src/components/PositionsTable.tsx:64-220`)

Add:
- warning badge next to status,
- expanded-row warning list,
- quick actions:
  - `Create opening sell`
  - `Create closing buy`
  - `Link assignment stock movement`
- optional filter pills: `All`, `Linked`, `Needs backfill`, `Warnings only`.

This is the best place for row-level operational cleanup.

#### B. Position detail drawer (`frontend/src/components/PositionDetail.tsx:853-873`)

Keep current premium/buyback fields as **legacy informational/editable fields**, but add:
- linked movement summary,
- account(s) inferred from linked movements,
- movement links list,
- warning panel.

#### C. Economics / Options page

Add a top coverage banner plus per-row warning status in the positions detail table.

Example banner:

> Coverage: 14 / 83 positions linked for current filters. EUR totals reflect linked positions only. 9 positions are missing opening sells, 4 manual/rolled positions are missing buybacks, 2 assigned positions are missing stock assignment movements.

---

## 4. FIFO / Inventory / Totals Exclusion Audit

Confirmed decision #2 requires no silent fallthrough. The right implementation pattern is to centralize handled-type sets and make option types an **explicit inventory-neutral branch** everywhere.

### 4.1 `backend/src/portfolio/holdings_service.py`

| Function / lines | Current behavior | Required change |
|---|---|---|
| `compute_holdings()` `104-258` | Explicit branches for `BUY`, `SELL`, `DIVIDEND`, `TRANSFER_IN`, `TRANSFER_OUT`; unknown types silently do nothing | Add explicit `elif txn_type in OPTION_TXN_TYPES: pass` branch; move to handled-type allow-list; log or reject truly unknown types |

Why this matters: this function currently drives FIFO and total purchase/sale/dividend summaries (`backend/src/portfolio/holdings_service.py:150-154`, `174-252`). Option movements must be intentionally excluded from **all** of those share-derived totals.

### 4.2 `backend/src/portfolio/import_service.py`

| Function / lines | Current behavior | Required change |
|---|---|---|
| `_build_preview_movements()` `565-579` | Negative inventory preview only knows `BUY` and `SELL`; anything else falls through | Add explicit no-op handling for `CALL_SELL`, `CALL_BUY`, `PUT_SELL`, `PUT_BUY` so preview inventory remains intentional, not accidental |

**No phase-1 change recommended** for `_row_to_movement()` `602-720`: current import formats are dividends/purchases/sales only, so there is no options CSV importer in scope.

### 4.3 `backend/src/portfolio/cosmos_portfolio.py`

| Function / lines | Current behavior | Required change |
|---|---|---|
| `_validate_correction_fields()` `172-280` | Type-specific rules only for `BUY`, `SELL`, `DIVIDEND` | Add explicit rule buckets: stock-buy-like, stock-sell-like, option-buy-like, option-sell-like, dividend, transfer; disallow `sales_type` / `cost_basis_status` on option txns |
| `create_manual_movement()` `604-739` | Allows only `BUY`, `SELL`, `DIVIDEND`; net semantics keyed only on `BUY` vs others | Allow the four option txns; require `option_position_id`; persist `quantity="0"`; use BUY-like net for `*_BUY`, SELL-like net for `*_SELL`; preserve option metadata |
| `correct_movement()` `741-870` | Recompute net only with `txn_type == 'BUY'` special-case; transfer-only non-correctable guard | Extend classification so `CALL_BUY`/`PUT_BUY` follow BUY-like recompute and `CALL_SELL`/`PUT_SELL` follow SELL-like recompute; option txns remain correctable |
| `_compute_shares_at_date()` `1683-1698` | Explicitly handles `BUY`, `SELL`, `TRANSFER_IN`, `TRANSFER_OUT`; unknown types silently ignored | Add explicit option branch to keep transfer availability logic intentional |
| `_compute_cost_basis_at_date()` `1707-1724` | Explicitly handles `BUY` and `TRANSFER_IN`; unknown types silently ignored | Add explicit option branch to keep transfer carried-cost math intentional |
| `reassign_movement()` `1763-1832` | Type-agnostic copy preserves unknown fields, but preview/reporting does not expose option linkage | Preserve new option metadata explicitly in response contracts/tests; reassignment should be allowed for option movements |
| `_fetch_reassign_candidates()` / `preview_batch_reassign()` / `batch_reassign_movements()` `1834-1996` | Generic today | No exclusion needed, but preview sample should include `option_position_id` and option type so bulk account cleanup is auditable |

### 4.4 `backend/web/portfolio_routes.py`

| Route / lines | Current behavior | Required change |
|---|---|---|
| `GET /api/portfolio/movements` `533-564` | `_ALLOWED_TXN_TYPES` omits option types | Extend whitelist to include the four new values |
| `POST /api/portfolio/movements` `781-820` | Only allows `BUY`, `SELL`, `DIVIDEND` | Extend validation and docs to allow option txns; require option-link fields when one of the four new types is posted |

### 4.5 Frontend movement list / create / detail surfaces

| File / lines | Current behavior | Required change |
|---|---|---|
| `frontend/src/types/portfolio.ts:5-166`, `293-306` | `TxnType`, `LedgerMovement`, `ManualMovementRequest` omit option types/link fields | Extend unions and request/response shapes |
| `frontend/src/components/PortfolioMovementsTable.tsx:18-48` | badges + filter pills only know current five types | Add option badges and filter values |
| `frontend/src/components/AddMovementDialog.tsx:16-24`, `512-602` | create flow only supports BUY/SELL/DIVIDEND/TRANSFER | Add option movement presets and position-link fields; ideally launched from a position context to prefill symbol/position/type |
| `frontend/src/components/MovementDetailDialog.tsx:14-20`, `253-360` | detail dialog has no option-link presentation | Show option metadata and linked position info |

---

## 5. Economics / Options + Overview Rewrite

### 5.1 Current state to replace

Today `_build_economics_report()` reads `position.source.premium` and `position.buyback_cost`, multiplies by a hardcoded `CONTRACT_MULTIPLIER = 100`, and computes all options aggregates in USD (`backend/web/app.py:233-449`). `_build_economics_overview_report()` then publishes those options values as `options_net_native` beside dividend EUR values (`backend/web/app.py:452-530`).

This must be replaced entirely for options economics.

### 5.2 New aggregation inputs

Options reports must read from:
- symbol-container positions for structural fields and lifecycle state,
- portfolio-container movements for all economics amounts.

Legacy position fields remain display-only:
- `display_premium` / `display_buyback` are still set in symbol detail responses today (`backend/web/app.py:1567-1572`),
- `PositionDetail` edits them today (`frontend/src/components/PositionDetail.tsx:853-873`).

That is acceptable **only as informational/manual-entry legacy data**. They must no longer feed any economics aggregate.

### 5.3 Proposed backend report contract

#### Options report (`GET /api/economics`)

Keep existing filters and add `account_id`.

##### Request params

- `year`
- `month`
- `symbol`
- `type`
- `status`
- `account_id` (csv, optional)

##### Summary fields

| Field | Currency / source |
|---|---|
| `total_premium_usd` | sum of opening option `gross.amount` in USD |
| `total_buyback_usd` | sum of closing option `gross.amount` in USD |
| `net_option_usd_gross` | `premium_usd - buyback_usd`; used for RoC/win logic |
| `net_income_eur` | `sum(open SELL net.eur_amount) - sum(close BUY net.eur_amount)` |
| `total_commission_eur` | sum of `fees.total_eur` across linked option movements |
| `avg_roc_pct` | USD-based |
| `avg_roc_annualized` | USD-based |
| `win_rate` | USD-based, see below |
| `total_positions` | positions after structural filters |
| `coverage` | object described below |

##### Coverage block

```json
{
  "linked_positions": 0,
  "total_positions": 0,
  "linked_ratio": 0.0,
  "positions_with_unresolved_security": 0,
  "positions_missing_opening_sell": 0,
  "positions_missing_closing_buy": 0,
  "positions_missing_assignment_stock": 0,
  "excluded_unlinked_positions_for_account_filter": 0,
  "excluded_positions_linked_only_outside_account_filter": 0
}
```

##### Per-position fields

Each row should include at least:

- existing structural fields (`symbol`, `position_id`, `type`, `strike`, `expiration`, `status`, `opened_at`, `days_held`),
- `premium_usd`,
- `buyback_usd`,
- `net_income_eur`,
- `roc_pct`,
- `roc_annualized`,
- `linked_accounts: string[]`,
- `linked_movement_count`,
- `coverage_status: linked | unlinked | unresolved_security | account_filtered_out`,
- `warnings: [...]`.

### 5.4 Metric rules

#### RoC / annualized return (USD)

Per constraint A:
- `roc_pct = ((opening_sell_gross_usd - closing_buy_gross_usd) / strike) * 100`
- **no contract multiplier**,
- **no commission in the numerator**,
- still grouped/weighted using position strike and actual stored totals.

Because historical positions never tracked contracts, the report must treat the stored USD totals as authoritative cash amounts, not try to infer a count.

#### Net income / cash-flow (EUR)

Use ledger `net.eur_amount`:

- opening option sells contribute **positive EUR inflow**,
- buyback option buys contribute **negative EUR outflow** in the aggregate by subtracting their positive `net.eur_amount`,
- assigned stock BUY/SELL movements do **not** contribute to option cash-flow totals; they are only used for warnings/traceability.

#### Win-rate rule

To preserve current business meaning, classify wins from **USD gross option result**, not EUR FX noise:

- expired / assigned / active-with-only-opening-leg positions are not settled wins unless their current status logic says they are settled,
- manual/rolled settled positions: win when `opening_sell_usd - closing_buy_usd > 0`,
- assigned/expired settled positions: win when there is an opening sell and no close buy expected.

### 5.5 Endpoint changes

#### `GET /api/economics` (`backend/web/app.py:1108-1165`)

Add `account_id`, load both symbol docs and portfolio movements, and call a new ledger-based options builder instead of the current position-field builder.

#### `GET /api/economics/overview` (`backend/web/app.py:1220-1290`)

Add `account_id` and consume the new options report coverage + EUR cash-flow outputs.

#### Internal reuse point

`open_roc_annualized = _build_economics_report(...status_filter="active")` is reused in the dashboard today (`backend/web/app.py:3634-3638`). That call must continue to work against the new report builder, or be split into a narrower helper, so the dashboard does not regress.

### 5.6 New overview semantics

The old overview side-by-side USD-native options model should be retired. Under the new movement design:

- **Overview cash-flow cards/tables use EUR**,
- RoC remains an options-only USD metric and stays on the Options page.

#### Recommended overview cards

| Card | Currency |
|---|---|
| `Options Net Cash Flow` | EUR |
| `Dividends Net Cash Flow` | EUR |
| `Combined Cash Flow` | EUR |
| `Option Positions in Scope` | count |
| `Options Coverage` | linked / total |

#### Recommended overview monthly table

Replace `options_net_native` with `options_net_eur` in `backend/web/app.py:464-515` / `frontend/src/components/EconomicsOverviewView.tsx:240-279`.

### 5.7 Frontend changes — Options page

Current options summary and tables are fully USD-labeled (`frontend/src/components/EconomicsView.tsx:96-121`, `529-659`, `frontend/src/types/economics.ts:1-62`).

Recommended page changes:

- add `account_id` filter beside current `year/month/symbol/type/status`,
- rename cards to make mixed-currency semantics explicit:
  - `Premium Sold (USD)`
  - `Buybacks (USD)`
  - `Net Cash Flow (EUR)`
  - `Avg RoC% (USD basis)`
  - `Win Rate`
- add coverage banner above filters,
- add warning/coverage columns in the positions detail table,
- include linked-account badges in position rows.

Also update:
- `frontend/src/app/api/economics/route.ts:4-20` to forward `account_id`,
- `frontend/src/components/EconomicsTabs.tsx:16-19` so the Options tab preserves `account_id` when applicable.

### 5.8 Frontend changes — Overview page

Current overview cards and table still say `Options Net (USD native)` (`frontend/src/components/EconomicsOverviewView.tsx:97-155`, `240-279`).

Recommended changes:
- options card becomes EUR,
- add coverage chip directly under or inside the options card row,
- if `account_id` filter is active, show text such as:
  - `Unlinked positions excluded from account-scoped options totals`.

Also update:
- `frontend/src/app/api/economics/overview/route.ts:4-14` to forward `account_id`,
- `frontend/src/components/EconomicsTabs.tsx:16-19` to preserve `account_id` across overview/options/dividends where supported.

### 5.9 Coverage indicator placement (constraint E)

**Recommendation:** surface coverage in **both** places:

1. **Options detail page** — prominent banner above filters and repeated in positions table footer.
2. **Overview page** — compact KPI/chip in the summary row plus explanatory note near the options cash-flow card.

This prevents a near-zero EUR total during backfill from being mistaken for “no options income.”

---

## 6. Phasing Recommendation

### Phase 1 — must ship together

1. Extend enum + contracts with `CALL_SELL`, `CALL_BUY`, `PUT_SELL`, `PUT_BUY`.
2. Add option linkage fields (`option_position_id`, `option_link_kind`, snapshots).
3. Update manual create/list/detail/correct/reassign flows to understand option movements.
4. Make FIFO/transfer math explicitly exclude option movements.
5. Add warning engine and row-level surfacing on positions.
6. Rewrite Options + Overview reports to ledger-backed logic.
7. Add coverage indicators.
8. Add `account_id` filtering to options/overview.
9. Add **minimal assignment stock traceability** (`option_position_id` on stock BUY/SELL).

**Why assignment link is Phase 1, not Phase 2:** without it, confirmed decision #3 (“warn if no linked stock movement found for an assigned position”) cannot be implemented reliably.

### Phase 2 — high-value UX follow-up

1. Dedicated bulk backfill workspace for unlinked positions.
2. One-click “Create opening sell / closing buy” from PositionsTable rows.
3. One-click “Create linked stock BUY/SELL for assignment” from assigned rows.
4. Linked-movement drilldown from the economics positions table.
5. Better mismatch diagnostics (wrong linked type, duplicate link suspicion, conflicting accounts).

### Phase 3 — optional refinements

1. Import support for options CSVs, if ever needed.
2. More rigorous assignment validation (date/amount/quantity heuristics) once contract counts exist.
3. Optional commission-aware RoC variant if product wants it later.

---

## 7. Open Questions / Risks — RESOLVED (2026-09-09, by dsanchor)

### 7.1 Historical FX acquisition for manual backfill — RESOLVED

**Decision:** Not a problem. User will manually enter every option movement by hand: USD amount, EUR amount, and EUR commission, all supplied directly (no live FX lookup needed for backfill). Confirms the recommendation below as the shipped behavior, not just a fallback.

`fx_service.py` only has ~90 days of ECB history today (`backend/src/portfolio/fx_service.py:27-28`, `125-154`). That is **not** enough for historical option backfill autofill.

**Recommendation (confirmed):** reports read stored EUR values only; backfill UI must allow manual EUR/fx entry and treat live FX lookup as convenience, not requirement.

### 7.2 Ambiguous ticker → security resolution — RESOLVED

**Decision:** Not a concern for this user's portfolio. Assume no ticker renames/reuse — a ticker always maps to exactly one Security. No disambiguation UI needed for now (can be revisited if it ever occurs).

Legacy symbols may lack `security_id`, and Security Master may have multiple entries for the same ticker across exchanges.

**Risk:** an exact ticker fallback could attach the wrong security.

**Recommendation (superseded by decision above):** only auto-resolve ticker fallback when unambiguous; otherwise force explicit security creation/selection.

### 7.3 `quantity` meaning on option movements

Using `quantity="0"` is correct for share inventory, but it means the ledger still does not know contract counts.

**Trade-off accepted:** this is fine because reports now use real traded totals, and the user explicitly rejected reconciliation against the old `×100` model.

### 7.4 Warning strictness during long transition

If we warn on every unlinked active position with no legacy premium, the UI will be noisy. If we warn only when a legacy value or close-state expectation exists, some rows will be uncovered but not “warning red.”

**Recommendation:** separate **coverage** from **warning severity** exactly as designed above.

### 7.5 Cosmos query batching limits

Large arrays of `security_ids` / `account_ids` / `position_ids` may need batching.

**Implementation risk:** the join helper should chunk large parameter lists but keep the API contract stable.

### 7.6 Contract/versioning impact

This is a real ledger contract expansion, effectively portfolio contract **v1.2**.

Affected mirrors include:
- backend `TxnType` enum,
- frontend `TxnType` union,
- manual create/correction/detail payloads,
- economics response types.

### 7.7 Legacy premium/buyback edit UX — RESOLVED

**Decision:** Leave as-is for now. Do NOT relabel/mark `display_premium`/`display_buyback` as legacy in this release — purely cosmetic, deferred indefinitely until requested.

`PositionDetail` currently edits `display_premium` / `display_buyback` as if they are operational values (`frontend/src/components/PositionDetail.tsx:853-873`). Once economics stops reading them, that UI may confuse users.

**Recommendation (deferred):** mark them visibly as `Legacy / informational only` in the same release or immediately after.

---

## 8. Implementation Status

**As of 2026-09-09: design approved with the above resolutions. NOT YET IMPLEMENTED.** No code has been written for this feature. Implementation (backend `TxnType` extension, FIFO/holdings exclusion audit, position↔movement linking, warning engine, Economics/Options + Overview rewrite, frontend movement creation UI) is pending explicit user go-ahead.

---

## Final Recommendation

Ship this as a **ledger-first options accounting layer**:
- flat option movement types,
- explicit `option_position_id` linkage,
- inventory-neutral handling everywhere,
- EUR cash-flow + USD RoC split,
- coverage-aware reporting,
- assignment stock traceability in phase 1.

That gives you durable account-aware options economics without polluting FIFO, while tolerating a long manual backfill period.
