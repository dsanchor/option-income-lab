# Livingston — Full Correction API Contract (for Rusty)

**Date:** 2026-09-06  
**Author:** Livingston (Persistence & Integration Engineer)  
**Status:** IMPLEMENTATION COMPLETE — backend hardened, tests passing  
**Audience:** Rusty (UI) — use this as the authoritative request/response spec

---

## ⚠️ DIVIDEND Correction — PENDING CONTRACT AMENDMENT

> **DO NOT IMPLEMENT DIVIDEND-SPECIFIC UI YET.**  
> Danny's in-flight amendment redefines the DIVIDEND model as follows (not yet frozen):
> - Withholding **amounts** are user-entered; `rate_pct` is **auto-calculated** by the server, not stored as input
> - A DIVIDEND may be a **linked composite corporate action** with distinct legs:  
>   residual cash · partial rights sale · shares from remaining rights · optional investor cash top-up
> - These legs must **not** be flattened into a single movement; quantity and cost must not be fabricated
>
> The sections below (§quantity-null semantics, §withholding 3-state, §DIVIDEND field matrix row)
> reflect contract v1 and may change materially. Implement BUY and SELL correction UI first.
> Reconcile DIVIDEND after the amendment is published.

---

## Endpoint

```
POST /api/portfolio/movements/{movement_id}/correct
```

### Path parameter
| Param | Type | Description |
|---|---|---|
| `movement_id` | string | ID of the ACTIVE movement to correct |

---

## Request Body

All fields except `account_id` and `correction_note` are optional. Only fields that are changed need to be sent (omit unchanged fields).

```json
{
  "account_id": "acct_fidelity_ira",         // REQUIRED — must match original's account
  "correction_note": "Wrong quantity",         // REQUIRED — non-empty
  "trade_date": "2024-03-15",                 // OPTIONAL — YYYY-MM-DD
  "quantity": "95.500000",                    // OPTIONAL — numeric string, ≥ 0; for DIVIDEND: null clears it
  "gross": {                                   // OPTIONAL — all 3 sub-fields required together
    "amount": "18250.000000",
    "currency": "EUR",
    "eur_amount": "18250.000000"
  },
  "fees": {                                    // OPTIONAL — all 3 sub-fields required together
    "total": "7.500000",
    "currency": "EUR",
    "total_eur": "7.500000"
  },
  "fx": {                                      // OPTIONAL — rate > 0 always
    "rate": "1.085000000",
    "rate_source": "ECB"                       // ECB | BROKER | MANUAL
  },
  "withholding": {                             // OPTIONAL — DIVIDEND/SELL only; null clears withholding
    "source": {                                // source is OPTIONAL within withholding
      "country": "US",
      "rate_pct": "15",
      "amount_eur": "45.300000"
    },
    "destination": null                        // null = "not captured" (⚠️ Pending); omit for no change
    // OR:
    // "destination": { "country": "ES", "rate_pct": "0", "amount_eur": "0.000000" }
    // OR:
    // "destination": { "country": "ES", "rate_pct": "19", "amount_eur": "57.000000" }
  },
  "sales_type": "DERECHOS",                   // OPTIONAL — SELL only; ACCIONES | DERECHOS
  "cost_basis_status": "COMPLETE",            // OPTIONAL — BUY only; COMPLETE | INCOMPLETE
  "notes": "Corrected per brokerage statement"
}
```

### Field matrix by movement type

| Field | BUY | SELL | DIVIDEND | Notes |
|---|---|---|---|---|
| `trade_date` | ✅ | ✅ | ⚠️ pending | |
| `quantity` | ✅ | ✅ | ⚠️ pending | BUY/SELL: must be string ≥ 0 |
| `gross` | ✅ | ✅ | ⚠️ pending | All 3 sub-fields required together |
| `fees` | ✅ | ✅ | ⚠️ pending | All 3 sub-fields required together |
| `fx` | ✅ | ✅ | ⚠️ pending | rate > 0; rate_source ∈ {ECB, BROKER, MANUAL} |
| `withholding` | ❌ → 400 | ✅ | ⚠️ pending | Full object or null; source.amount_eur required when source present |
| `sales_type` | ❌ → 400 | ✅ | ❌ → 400 | ACCIONES or DERECHOS |
| `cost_basis_status` | ✅ | ❌ → 400 | ❌ → 400 | COMPLETE or INCOMPLETE |
| `notes` | ✅ | ✅ | ⚠️ pending | |

> **DIVIDEND column marked ⚠️ pending** — all DIVIDEND correction fields are subject to Danny's
> in-flight amendment. Implement BUY and SELL correction only until amendment lands.

---

## Key Semantics

### Server-owned arithmetic

**Do NOT send `net` in the request.** The server recomputes it from source fields:

```
net_eur = gross_eur - fees_eur - wht_source_eur - wht_dest_eur
```

Where:
- `wht_dest_eur = 0` when `withholding.destination` is null (not captured)
- `wht_dest_eur = 0` when `withholding.destination.amount_eur = "0"` (confirmed zero)
- Gross, fees, and withholding values from original are preserved when not overridden

**Partial override example:** Send only `fees` → server uses original gross + new fees → recomputes net.

### Withholding — null vs zero distinction (BUY/SELL; DIVIDEND ⚠️ pending amendment)

| Request value | Meaning | Net effect |
|---|---|---|
| `"destination": null` | Not captured (⚠️ Pending) | `wht_dest = 0` in net formula |
| `"destination": {"amount_eur": "0", ...}` | Confirmed zero | `wht_dest = 0` in net formula |
| `"destination": {"amount_eur": "57.00", ...}` | Withheld amount | `wht_dest = 57.00` in net formula |

> **DIVIDEND withholding ⚠️ pending amendment**: Danny's amendment specifies amounts as user input
> and rate_pct as auto-calculated. The 3-state destination UI described below is provisional for
> DIVIDEND only; apply it to SELL now, hold DIVIDEND implementation.

### DIVIDEND quantity null — ⚠️ PENDING AMENDMENT

> DIVIDEND quantity semantics (null-aware toggle) and the composite corporate action model
> (residual cash + partial rights sale + share legs + cash top-up) are pending amendment.
> Do not implement DIVIDEND quantity correction UI until the amendment is frozen.

---

## Success Response — 200 OK

```json
{
  "original": {
    "id": "mvt_abc001",
    "correction_status": "SUPERSEDED",
    "superseded_by": "mvt_xyz002",
    "txn_type": "BUY",
    "quantity": "100.000000",
    "gross": { "amount": "18250.000000", "currency": "EUR", "eur_amount": "18250.000000" },
    "fees": { "total": "7.500000", "currency": "EUR", "total_eur": "7.500000" },
    "net": { "amount": "18242.500000", "currency": "EUR", "eur_amount": "18242.500000" },
    "trade_date": "2024-01-15",
    "account_id": "_unassigned",
    "security_id": "XNYS:AAPL"
  },
  "replacement": {
    "id": "mvt_xyz002",
    "correction_status": "ACTIVE",
    "corrects_movement_id": "mvt_abc001",
    "correction_note": "Wrong quantity",
    "import_source": "manual",
    "txn_type": "BUY",
    "quantity": "95.000000",
    "gross": { "amount": "18250.000000", "currency": "EUR", "eur_amount": "18250.000000" },
    "fees": { "total": "7.500000", "currency": "EUR", "total_eur": "7.500000" },
    "net": { "amount": "18242.500000", "currency": "EUR", "eur_amount": "18242.500000" },
    "trade_date": "2024-01-15",
    "account_id": "_unassigned",
    "security_id": "XNYS:AAPL"
  }
}
```

---

## Error Responses

| Status | `error` code | Condition |
|---|---|---|
| 400 | `validation_error` | `account_id` or `correction_note` missing/empty |
| 400 | `validation_error` | Invalid field value (bad enum, negative amount, missing sub-field, etc.) |
| 400 | `validation_error` | Field not applicable to this movement type (e.g., `withholding` on BUY) |
| 404 | `not_found` | Movement ID not found in the given account |
| 405 | `transfer_not_correctable` | Movement is TRANSFER_OUT or TRANSFER_IN |
| 409 | `already_superseded` | Movement already has `correction_status=SUPERSEDED` or `VOIDED` |
| 503 | `storage_unavailable` | Cosmos DB not reachable |

**Error shape:**
```json
{ "error": "validation_error", "detail": "withholding is not applicable to BUY movements" }
```

---

## Audit Chain

```
Original (mvt_abc001)
  correction_status: SUPERSEDED
  superseded_by: mvt_xyz002

Replacement (mvt_xyz002)
  correction_status: ACTIVE
  corrects_movement_id: mvt_abc001
  correction_note: "Wrong quantity"
  import_source: "manual"
```

Double-correction: correct the replacement in the same way (it becomes SUPERSEDED, a new one is created). Chain: `A → B → C`. Only `C` is ACTIVE at any time.

---

## Immutable fields (cannot be changed via correction)

- `account_id` — use `POST /movements/{id}/reassign`
- `security_id` — void and recreate
- `txn_type` — void and recreate (different economic event)

---

## Transfer correction policy

**Transfers (TRANSFER_OUT / TRANSFER_IN) return 405** with `"error": "transfer_not_correctable"`.

User workflow for transfer errors: void the pair (both legs), then create a new correct transfer.

---

## Arithmetic precision (Decimal strings)

| Field | Precision |
|---|---|
| Monetary amounts | 6 decimal places (`"18250.123456"`) |
| FX rates | 9 decimal places (`"0.917431193"`) |
| Quantities | 6 decimal places (`"100.500000"`) |
| Withholding rates | String percentage (`"15"`, `"19.5"`) |

---

## Change detection (UI pattern)

The form should only include fields that actually changed from the original. Unchanged fields should be omitted from the request body. The server applies partial overrides: only provided fields update the replacement; the rest inherit from original.

**Exception:** Withholding destination 3-state must always send the current state even if unchanged (because null vs absent is meaningful). When the user doesn't intend to change withholding, omit `withholding` from the request entirely.

---

## Addendum — Amendments G, H, I (2026-09-06)

### Status Update

Amendments G, H, I are now **IMPLEMENTED** on the backend. The ⚠️ DIVIDEND pending banners from
the original contract are **resolved**. Full details follow.

---

### Amendment G: Server-Owned Arithmetic Clarification + CSV Parser Bilingual Support

**BUY formula (no changes to backend):**
```
net_eur = gross_eur - fees_eur   (no withholding for BUY)
```
`unit_price` is a **UI-only** field — the server never receives it. Frontend computes
`gross = quantity × unit_price` before calling the API.

**SELL formula (unchanged):**
```
net_eur = gross_eur - fees_eur - wht_source_eur - wht_dest_eur
```

**Sales type internal enum unchanged:**
```
sales_type: "ACCIONES" | "DERECHOS"   // API contract unchanged
```
UI renders `Stocks` / `Rights` via `SALES_TYPE_LABELS` mapping — purely frontend concern.

**CSV Parsers now bilingual:** Spanish and English headers both accepted for all three parsers
(purchases, sales, dividends). English type aliases `Stocks`, `Shares`, `Rights` map to
`ACCIONES` / `DERECHOS`. Non-empty unrecognized type values raise `ValueError`.

---

### Amendment H: WHT Rate Derivation + Corporate-Action Groups

#### WHT Rate: Server-Authoritative (CRITICAL)

`rate_pct` in `WithholdingDetail` is **server-computed** and must **never be trusted from client**:

```
rate_pct = (amount_eur / gross_eur) × 100   (rounded to 2 decimal places)
rate_pct = "0"                                when gross_eur = 0 (no ZeroDivision)
```

- **`amount_eur` is the primary input** (what brokers report).
- If the client sends `rate_pct`, the server **ignores and overwrites it**.
- Applies to: `create_manual_movement()`, `correct_movement()` (when withholding is overridden),
  and all legs of `create_corporate_action()`.

**Frontend change:** Display `rate_pct` as read-only computed field after server response.
Do NOT send user-typed `rate_pct` as primary input.

#### New: Corporate-Action Groups

##### POST /api/portfolio/corporate-actions

Creates N linked `ledger_txn` docs sharing a `ca_group_id`.

**Request body:**
```json
{
  "event_type": "DIVIDEND_WITH_SCRIP",     // CASH_DIVIDEND | DIVIDEND_WITH_SCRIP | SCRIP_DIVIDEND | RIGHTS_ISSUE
  "security_id": "XLON:ULVR",
  "account_id": "heytrade_main",
  "payment_date": "2024-03-28",            // REQUIRED
  "ex_dividend_date": "2024-03-07",        // optional
  "notes": "Unilever Q1 2024",             // optional — applied to all legs unless leg.notes set
  "legs": [
    {
      "leg_type": "CASH_DIVIDEND",         // CASH_DIVIDEND | RIGHTS_SOLD | SHARE_ACQUISITION | CASH_TOP_UP
      "trade_date": "2024-03-28",          // REQUIRED per leg
      "gross": { "amount": "180.00", "currency": "GBP", "eur_amount": "209.79" },
      "withholding": {
        "source": { "country": "GB", "amount_eur": "0" },
        "destination": { "country": "ES", "amount_eur": "39.86" }
      },
      "fx": { "rate": "1.165500000", "rate_source": "ECB" },
      "fees": null,
      "notes": "Cash portion"
    },
    {
      "leg_type": "SHARE_ACQUISITION",
      "trade_date": "2024-03-28",
      "quantity": "9",
      "gross": { "amount": "0", "currency": "GBP", "eur_amount": "0" },
      "cost_basis_status": "INCOMPLETE",
      "fx": { "rate": "1.165500000", "rate_source": "ECB" },
      "notes": "FMV: 24.75 GBP/share"
    }
  ]
}
```

**Leg type → txn_type mapping:**
| `leg_type` | `txn_type` | `sales_type` | `cost_basis_status` |
|---|---|---|---|
| `CASH_DIVIDEND` | `DIVIDEND` | — | — |
| `RIGHTS_SOLD` | `SELL` | `DERECHOS` (forced) | — |
| `SHARE_ACQUISITION` | `BUY` | — | as provided, default `INCOMPLETE` |
| `CASH_TOP_UP` | `BUY` | — | `INCOMPLETE` (forced), `quantity="0"` (forced) |

**Required legs per event type:**
| `event_type` | Required leg types |
|---|---|
| `CASH_DIVIDEND` | `CASH_DIVIDEND` |
| `DIVIDEND_WITH_SCRIP` | `CASH_DIVIDEND` + `SHARE_ACQUISITION` |
| `SCRIP_DIVIDEND` | `SHARE_ACQUISITION` |
| `RIGHTS_ISSUE` | `SHARE_ACQUISITION` |

**Validation:** All-or-nothing. If any leg fails, the entire request is rejected before any write.

**Success Response — 201 Created:**
```json
{
  "ca_group_id": "cag_abc123",
  "event_type": "DIVIDEND_WITH_SCRIP",
  "movements": [
    { "id": "mvt_leg1", "ca_leg_type": "CASH_DIVIDEND", "ca_group_id": "cag_abc123", "ca_group_seq": 1, ... },
    { "id": "mvt_leg2", "ca_leg_type": "SHARE_ACQUISITION", "ca_group_id": "cag_abc123", "ca_group_seq": 2, ... }
  ]
}
```

**Error codes:**
| Status | `error` | Condition |
|---|---|---|
| 400 | `validation_error` | Missing required fields, invalid event_type/leg_type, missing required legs |
| 503 | `storage_unavailable` | Cosmos unreachable |

---

##### POST /api/portfolio/corporate-actions/{ca_group_id}/void

Voids all active legs in a corporate-action group.

**Request body:**
```json
{ "account_id": "heytrade_main", "reason": "Data entry error" }
```

**Success Response — 200 OK:**
```json
{
  "ca_group_id": "cag_abc123",
  "voided_count": 4,
  "movements": [/* all 4 leg docs with correction_status: VOIDED */]
}
```

**Error codes:**
| Status | `error` | Condition |
|---|---|---|
| 404 | `not_found` | No active legs found for this `ca_group_id` |
| 400 | `validation_error` | `account_id` missing |
| 503 | `storage_unavailable` | Cosmos unreachable |

---

##### Individual Leg Correction (unchanged endpoint)

Correct a single leg via `POST /movements/{id}/correct`. The replacement automatically inherits
`ca_group_id`, `ca_leg_type`, `ca_event_type`, and `ca_group_seq` from the original
(they are preserved in the `dict(original)` copy).

---

#### New fields on `ledger_txn` documents

These fields appear on group legs (null / absent on standalone movements):

| Field | Type | Description |
|---|---|---|
| `ca_group_id` | string | Links legs of same group. Format: `cag_{hex}`. |
| `ca_leg_type` | string | `CASH_DIVIDEND` \| `RIGHTS_SOLD` \| `SHARE_ACQUISITION` \| `CASH_TOP_UP` |
| `ca_event_type` | string | `CASH_DIVIDEND` \| `DIVIDEND_WITH_SCRIP` \| `SCRIP_DIVIDEND` \| `RIGHTS_ISSUE` |
| `ca_group_seq` | int | 1-based ordering within group |

**Frontend:** Display group indicator for movements with `ca_group_id ≠ null`.
Movement detail should show sibling legs (fetched by `ca_group_id` query).

---

### Amendment I: Symbol Detail — Stacked Sections (not Tabs)

**Final architecture decision:** Two **stacked collapsible sections** ("Options" and "Stocks"),
NOT tabs. See `livingston-symbol-detail-stocks-tab-findings.md` for updated Rusty spec.

**Backend is complete** — `GET /api/portfolio/movements?security_id=...` already serves the
`StockTransactionsTable` component with full `LedgerMovement` wire shapes including all financial
fields. No new backend endpoints needed.

**`StockTransactionsTable` data source:**
```typescript
GET /api/portfolio/movements?security_id={securityId}&txn_type={filter}&limit=20&offset={page*20}
```

Returns full `LedgerMovement` objects — all fields are available (gross, fees, withholding, net,
sales_type, ca_group_id, ca_leg_type, etc.). No backend enrichment needed.

---

*Contract updated for Amendments G, H, I — Livingston, 2026-09-06*

---

## Batch Account Reassignment — Reason Is Optional (2026-09-06)

**Directive:** `copilot-directive-20260906-optional-batch-reassignment-reason.md`

### POST /api/portfolio/movements/batch-reassign

`reason` is **optional** for batch reassignment. When omitted or blank, the server records
`"Batch account reassignment"` as the internal audit reason so the audit chain is never empty.

```json
// Both of these are valid:
{ "source_account_id": "acct_a", "dest_account_id": "acct_b" }
{ "source_account_id": "acct_a", "dest_account_id": "acct_b", "reason": "" }
{ "source_account_id": "acct_a", "dest_account_id": "acct_b", "reason": "Custom reason" }
```

**Individual reassignment** (`POST /movements/{id}/reassign`) retains the existing
**required** `reason` validation — empty/missing → 400 `validation_error`.

| Endpoint | `reason` | Behavior when blank/omitted |
|---|---|---|
| `POST /movements/{id}/reassign` | **Required** | 400 `validation_error` |
| `POST /movements/batch-reassign` | **Optional** | Defaults to `"Batch account reassignment"` |

---

## Amendment H — Group Correction Endpoint (2026-09-06)

### POST /api/portfolio/corporate-actions/{ca_group_id}/correct

Atomically replace an entire corporate-action group.

#### Request body

```json
{
  "account_id": "heytrade_main",           // required — same partition as original group
  "correction_note": "Fix gross amount",   // required, non-empty
  "event_type": "CASH_DIVIDEND",           // required — CaEventType value
  "security_id": "XLON:ULVR",             // optional — inferred from original legs if omitted
  "payment_date": "2024-03-28",           // optional — inferred from original legs if omitted
  "notes": "Group-level annotation",       // optional
  "legs": [                                // required, non-empty
    {
      "leg_type": "CASH_DIVIDEND",         // required — CaLegType value
      "trade_date": "2024-03-28",
      "quantity": "0",                     // omit or "0" for CASH_DIVIDEND / CASH_TOP_UP
      "gross": {
        "amount": "210.00",
        "currency": "EUR",
        "eur_amount": "210.00"
      },
      "fees": {                            // optional
        "total": "0",
        "currency": "EUR",
        "total_eur": "0"
      },
      "withholding": {                     // optional; rate_pct is ALWAYS server-derived
        "source":      {"country": "GB", "amount_eur": "0"},
        "destination": {"country": "ES", "amount_eur": "40.00"}
      },
      "fx": {"rate": "1.165500000", "rate_source": "ECB"},
      "cost_basis_status": "COMPLETE",     // only for SHARE_ACQUISITION legs
      "notes": "Leg-level annotation"      // optional
    }
  ]
}
```

#### Required legs per event_type

| `event_type`          | Required `leg_type`(s)                                |
|-----------------------|-------------------------------------------------------|
| `CASH_DIVIDEND`       | `CASH_DIVIDEND`                                       |
| `DIVIDEND_WITH_SCRIP` | `CASH_DIVIDEND`, `SHARE_ACQUISITION`                  |
| `SCRIP_DIVIDEND`      | `SHARE_ACQUISITION`                                   |
| `RIGHTS_ISSUE`        | `SHARE_ACQUISITION`                                   |

#### Response 201

```json
{
  "original_ca_group_id": "cag_abc123",
  "ca_group_id": "cag_def456",
  "event_type": "CASH_DIVIDEND",
  "correction_note": "Fix gross amount",
  "movements": [
    {
      "id": "mvt_xxx",
      "ca_group_id": "cag_def456",
      "replaces_ca_group_id": "cag_abc123",
      "ca_leg_type": "CASH_DIVIDEND",
      "ca_event_type": "CASH_DIVIDEND",
      "ca_group_seq": 1,
      "correction_note": "Fix gross amount",
      "txn_type": "DIVIDEND",
      ...
    }
  ]
}
```

#### Error codes

| HTTP | code | when |
|------|------|------|
| 400 | `validation_error` | missing/empty `correction_note`, invalid `event_type`, missing required leg type, unknown `leg_type`, empty `legs` |
| 400 | `ca_group_correction_failed` | phase-1 write failed; no partial state persisted |
| 404 | `not_found` | `ca_group_id` has no active legs |
| 409 | `integrity_error` | phase-2 supersession failed; new docs deleted; original group still intact |
| 503 | `storage_unavailable` | Cosmos unavailable |

#### Arithmetic rules

- `net_eur = gross_eur - fees_eur - withholding.source.amount_eur - withholding.destination.amount_eur`
- `withholding.*.rate_pct = round((amount_eur / gross_eur) × 100, 2)` — server derives, client value ignored
- `rate_pct = "0"` when `gross_eur == 0` (no ZeroDivision)

#### Financial-field guard on individual correct_movement

Group legs (`ca_group_id` set) **reject** financial field changes via the individual
`POST /movements/{id}/correct` endpoint. Financial fields include:
`gross`, `fees`, `withholding`, `quantity`, `fx`, `sales_type`, `cost_basis_status`.

Non-financial fields (`trade_date`, `notes`) are still patchable individually on group legs.

Error raised: `400 group_leg_correction_required` with a redirect message to the group endpoint.

