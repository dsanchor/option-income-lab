# Livingston → Rusty: Unified Watchlist & US-Only Eligibility API Contract

**Date:** 2026-09-07  
**Author:** Livingston (Persistence & Integration Engineer)  
**For:** Rusty (Frontend Engineer)  
**Status:** SHIPPED — backend implemented, tests green, do not commit/deploy yet.

---

## 1. Overview

Two breaking-compatible changes land together. Backend is backward-compatible: every legacy field is preserved. Frontend should migrate at its own pace.

---

## 2. `GET /api/symbols/overview`

> **Updated 2026-09-07 (copilot-directive-20260907-watchlist-shared-filters):**
> Render TWO separate visible sections (Portfolio, Watchlist) using `portfolio_rows` and `watchlist_rows`. ONE shared filter/search toolbar applies to both sections simultaneously (client-side state). Do NOT collapse them into a single flat list in the UI.

### New query parameter

| Param | Type | Default | Meaning |
|---|---|---|---|
| `include_zero_portfolio` | bool | `false` | When true, includes auto-enrolled historical symbols with zero shares that are not explicit watchlist members. |

### Response shape

```jsonc
{
  // NEW — unified flat list (primary surface)
  "symbols": [
    {
      // Identity
      "symbol": "AAPL",
      "display_name": "Apple Inc.",
      "exchange": "NASDAQ",
      "exchange_mic": "XNAS",

      // Section routing (NEW)
      "row_source": "portfolio",        // "portfolio" | "watchlist"
      "list_section": "portfolio",      // same value, legacy alias
      "is_auto_enrolled": false,        // true = was created by ledger import, not manual add

      // Portfolio fields (null/0 for watchlist-only rows)
      "shares": 10.0,
      "avg_cost_basis_eur": 145.23,
      "portfolio_invested_eur": 1452.30,   // = remaining_cost_basis_eur (what you paid, adjusted)
      "portfolio_dividends_eur": 87.50,    // NEW — net dividends received for this holding
      "portfolio_realized_eur": 120.00,    // NEW — realized_result_eur (closed trades P&L only)
      "current_value_eur": 1720.00,
      "unrealized_gain_eur": 267.70,

      // Exposure / options (null for watchlist-only)
      "calls_exposure_eur": 500.00,
      "puts_committed_eur": 200.00,

      // CMP
      "cmp": 172.00,
      "cmp_source": "live",

      // Watchlist config (always present)
      "track_covered_calls": true,
      "track_cash_secured_puts": false,
      // ... other watchlist toggles

      "us_options_eligible": true       // SEE Section 4
    }
  ],

  // NEW — portfolio-wide KPI summary (null if no holdings exist)
  "portfolio_summary": {
    "total_investment_eur": 45000.00,   // sum of remaining_cost_basis_eur across all holdings
    "net_gains_eur": 3200.00,           // sum of realized_result_eur ONLY (honest label)
    "total_dividends_eur": 1800.00,     // sum of total_dividends_eur across all holdings
    "calls_exposure_eur": 4200.00,      // existing KPI, unchanged
    "puts_committed_eur": 1100.00,      // existing KPI, unchanged
    "has_incomplete_cost_basis": false  // advisory flag
  },

  // LEGACY — preserved for atomic rollout (do not remove until Rusty confirms migration done)
  "portfolio_rows": [ /* same objects filtered to list_section == "portfolio" */ ],
  "watchlist_rows": [ /* same objects filtered to list_section == "watchlist" */ ],
  "portfolio_count": 12,
  "watchlist_count": 5
}
```

### Visibility rules

- **Portfolio holdings** (shares ≠ 0, positive or negative): always included in `symbols`.
- **Explicit watchlist symbols** (manually added, or any watchlist toggle enabled): always included.
- **Auto-enrolled zero-share symbols**: hidden by default; appear only when `include_zero_portfolio=true`.
- **Deduplication**: if a symbol appears in both portfolio and watchlist, one row is emitted with `row_source = "portfolio"`. Portfolio data takes precedence.
- **`portfolio_summary` totals**: computed over ALL holdings regardless of the `include_zero_portfolio` filter — totals never change based on display visibility.

### Null / zero for watchlist-only rows

`portfolio_invested_eur`, `portfolio_dividends_eur`, `portfolio_realized_eur`, `shares`, `avg_cost_basis_eur`, `current_value_eur`, `unrealized_gain_eur`, `calls_exposure_eur`, `puts_committed_eur` are `null` or `0` for rows with `row_source = "watchlist"`.

---

## 3. Symbol Detail — `GET /api/symbols/{symbol}`

One new field added:

```jsonc
{
  // ... existing fields unchanged ...
  "us_options_eligible": true   // bool — see Section 4
}
```

---

## 4. `us_options_eligible` — Exchange Eligibility

| Value | Meaning |
|---|---|
| `true` | Exchange is XNYS (NYSE) or XNAS (NASDAQ). All option actions allowed. |
| `false` | All other exchanges. Option-related UI elements should be hidden/disabled. |

**Resolution priority** (fail-closed):
1. `security.exchange_mic` from the security record
2. `doc.exchange` field on symbol_config
3. MIC prefix from `security_id` (e.g. `"XMAD:REP"` → `"XMAD"`)
4. If none resolve → treated as ineligible (false)

---

## 5. Endpoints that return 403 for non-US symbols

When a symbol is not US-eligible, these endpoints return:

```json
HTTP 403
{
  "error": "options_not_eligible",
  "detail": "Symbol <TICKER> is not eligible for US options actions (exchange: <MIC>)"
}
```

**Guarded endpoints:**

| Method | Path | Action |
|---|---|---|
| POST | `/api/symbols/{symbol}/positions` | Add position |
| PUT | `/api/symbols/{symbol}/positions/{id}` | Update position |
| PATCH | `/api/symbols/{symbol}/positions/{id}` | Patch position |
| DELETE | `/api/symbols/{symbol}/positions/{id}` | Close/delete position |
| POST | `/api/symbols/{symbol}/positions/from-activity` | Add from activity |
| POST | `/api/symbols/{symbol}/positions/{id}/roll` | Roll position |
| POST | `/api/symbols/{symbol}/positions/{id}/snapshot` | Add snapshot |
| POST | `/api/symbols/{symbol}/best-options` | Analyze options |
| POST | `/api/symbols/{symbol}/best-options/refresh` | Refresh options |
| GET | `/api/symbols/{symbol}/options-chain` | Options chain |
| GET | `/api/symbols/{symbol}/report` | Report |
| POST | `/api/symbols/{symbol}/chat/context` | Chat context |
| POST | `/api/symbols/{symbol}/chat` | Agent chat |
| GET | `/api/symbols/{symbol}/forecasts` | Price forecasts |
| GET | `/api/symbols/{symbol}/forecasts/{id}` | Single forecast |
| POST | `/api/symbols/{symbol}/pause` | Pause watchlist |
| DELETE | `/api/symbols/{symbol}/pause` | Resume watchlist |
| POST | `/api/portfolio/holdings/from-activity` | Holdings from activity |
| POST | `/api/portfolio/holdings/{id}/roll` | Roll holding |
| POST | `/api/portfolio/holdings/{id}/snapshot` | Holding snapshot |
| PUT | `/api/symbols/{symbol}` | Symbol update (option toggles only — display/name/shares pass through) |

**Non-guarded endpoints** (allowed for all symbols including non-US):

- `GET /api/symbols/overview`
- `GET /api/symbols/{symbol}` (detail — returns `us_options_eligible` flag but is not blocked)
- `PUT /api/symbols/{symbol}` with non-option keys (`display_name`, `total_shares_override`, etc.)
- All account, ledger, activity, and identity endpoints

---

## 6. Frontend Migration Checklist (for Rusty)

> **UPDATED 2026-09-07:** Keep Portfolio and Watchlist as two distinct rendered sections. Use `portfolio_rows` for the Portfolio section and `watchlist_rows` for the Watchlist section. Provide a single shared filter/search toolbar whose state applies to both sections simultaneously (client-side filtering — no server-side filter param needed).

- [ ] Render Portfolio section from `portfolio_rows`, Watchlist section from `watchlist_rows` (not a single merged list)
- [ ] Implement shared filter/search toolbar — filter predicate applied client-side to both `portfolio_rows` and `watchlist_rows`
- [ ] Add second KPI row using `portfolio_summary.total_investment_eur`, `net_gains_eur`, `total_dividends_eur`
- [ ] Show per-row dividend badge using `portfolio_dividends_eur`
- [ ] Show per-row realized P&L using `portfolio_realized_eur`
- [ ] Hide / disable all option-action UI when `us_options_eligible === false`
- [ ] Use `include_zero_portfolio=true` for "show all history" toggle
- [ ] `symbols[]` unified list is available if ever needed (e.g. a combined search-all view), but the primary two-section rendering uses the legacy `portfolio_rows`/`watchlist_rows`
- [ ] Remove reads of the old sectioned client code after migration (backend will keep all fields until confirmed)

---

## 7. Performance Notes

- `portfolio_summary` is computed in a single pass over holdings — O(N) with no extra queries.
- Overview builds holdings map first (O(N)), then merges symbol configs (O(M)) — no N+1.
- Eligibility resolution is O(1) per row (field lookup, no extra DB calls).

---

## 8. Backward Compatibility Guarantees

- All pre-existing response fields on `GET /api/symbols/overview` are preserved.
- All pre-existing response fields on `GET /api/symbols/{symbol}` are preserved.
- No database schema migrations.
- Non-US symbols continue to work for all non-option endpoints as before.
