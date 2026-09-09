# Design: Economics Unified Dashboards (Overview, Options, Dividends)

**Date:** 2026-09-09  
**Author:** Danny  
**Requested by:** dsanchor  
**Authoritative sources:**
- Existing options Economics page: `frontend/src/app/economics/page.tsx`, `frontend/src/components/EconomicsView.tsx`, `frontend/src/types/economics.ts`
- Existing options economics API: `backend/web/app.py` (`GET /api/economics`, `_build_economics_report`)
- Portfolio dividend ledger model: `backend/src/portfolio/cosmos_portfolio.py`, `backend/src/portfolio/import_service.py`, `backend/src/portfolio/holdings_service.py`, `backend/web/portfolio_routes.py`
- FX capability reference: `backend/src/portfolio/fx_service.py`
- Team decisions baseline: `.squad/decisions.md`

**Status:** PROPOSED — design only, no code changes in this task.

---

## Recommendation Summary

Create **three routed views under Economics**: a new **global overview**, the existing **options detail**, and a new **dividends detail**. My recommendation is to ship the overview first as a **side-by-side cash-flow dashboard** (Options net in native USD, Dividends net in EUR) and **not** show a single blended money total until options economics gains durable FX conversion. Navigation should use **page-level routes with an in-page tab bar**; this keeps deep links, supports view-specific filters, and avoids overloading one large client-only page.

---

## Navigation & Information Architecture

### Options Considered

| Option | Shape | Pros | Cons | Verdict |
|--------|-------|------|------|---------|
| **A. Routed Economics views + in-page tabs** | `/economics`, `/economics/options`, `/economics/dividends` with a shared tab strip inside Economics | Best deep-linking, clear page ownership, page-specific data contracts, natural future growth | Requires legacy `/economics` migration handling | **Recommended** |
| **B. TopNav dropdown only** | Convert `Economics` into a dropdown like `Investments`, each item opens a separate page | Familiar existing pattern in this codebase; strong discoverability from any page | Once inside the page, sibling navigation is less visible; still needs routed pages underneath | **Runner-up** |
| **C. Single `/economics` page with client tabs only** | One page, all views mounted client-side | Minimal routing changes; easiest short-term migration | Weaker URL shareability, harder browser back/forward behavior, more state complexity, page grows too large | Not recommended |

### Recommended Navigation

**Recommendation:** use **routed views with an in-page tab bar**.

- `/economics` → **Global / Overview**
- `/economics/options` → **Options**
- `/economics/dividends` → **Dividends**

### Why this is the right trade-off

| Concern | Routed views + tabs outcome |
|--------|------------------------------|
| **URL shareability** | Strong: each view is a stable URL |
| **Shared filter carry-over** | Carry common params (`year`, `month`, `symbol`) when switching tabs; drop unsupported params (`type`, `status`, `account_id`) |
| **Discoverability** | Good inside Economics via visible tabs; acceptable in TopNav even if Economics remains a flat link |
| **Consistency with current TopNav** | Keeps current flat top-level link simple; optional future upgrade to dropdown remains available |
| **Complexity** | Lower than one mega-page with three datasets and divergent filters |

### Runner-up alternative

If product priority is global discoverability over migration simplicity, convert `Economics` into a **TopNav dropdown** with the same three routes underneath. I would keep that as phase 2 if users later need faster direct access to subviews from anywhere.

### Legacy URL / bookmark concern

Today `/economics` is the options detail page. If `/economics` becomes the overview, old bookmarks to `/economics?...` become ambiguous because `year`, `month`, and `symbol` are valid in both views.

**Mitigation plan:**
- Make `/economics/options` the canonical options detail URL immediately.
- Keep the overview on `/economics`.
- Add compatibility redirect only when clearly legacy-only params are present (for example `type`, `status`, or existing options-only sort keys).
- Accept that bare `/economics?year=...&month=...&symbol=...` links cannot be losslessly auto-classified; document this migration in release notes and keep those params functional on the overview.

---

## View 1 — Aggregated / Global Overview

### Objective

Provide a **single Economics landing page** that answers: “How much cash flow did options generate, how much did dividends generate, and how are both evolving by month and symbol?”

This view is **portfolio-level only**: no per-position or per-movement detail table.

### KPI Row

| KPI | Meaning | Notes |
|-----|---------|-------|
| **Options net** | Net options income in native options currency | Initially labelled explicitly as `USD (native)` |
| **Dividends net** | Net dividend income in EUR | Uses `net.eur_amount` |
| **Options positions** | Count of option positions included after filters | Mirrors existing options summary semantics |
| **Dividend events** | Count of dividend ledger rows included after filters | `txn_type == DIVIDEND`, active rows only |
| **Symbols with cash flow** | Distinct symbols appearing in either source after filters | Cross-source activity KPI |

**Important:** do **not** show a primary “Combined net total” KPI until both sources are expressed in one trusted currency.

### Monthly dashboards

| Dashboard | Purpose | Recommendation |
|-----------|---------|----------------|
| **Monthly options net** | Trend of options cash flow by `opened_at` month | Reuse current bar-chart pattern; label axis as USD native |
| **Monthly dividends net** | Trend of dividend cash flow by `trade_date` month | Same visual pattern; label axis as EUR |
| **Unified monthly table** | One row per month with both sources aligned | Columns: month, options_net_native, dividends_net_eur, option_positions, dividend_events |
| **By-symbol contribution table** | Portfolio-wide comparison by symbol | Side-by-side columns, not a blended money total |

**Preferred chart layout:** two aligned small multiples rather than one mixed-currency chart with a synthetic sum.

### Filters

| Filter | Scope | Notes |
|--------|-------|------|
| `year` | Both sources | Required parity with current view |
| `months[]` | Both sources | Same semantics as current options page |
| `symbols[]` | Both sources | Use ticker-level filtering; intersection with dividend `ticker` |
| `source` (optional) | Overview only | Toggle chart visibility: `options`, `dividends`, `both` |

### Should `type` still apply here?

**Recommendation: no.**

The overview should keep only **shared filters** plus an optional **source toggle**. `type` (`call`/`put`) and `status` are meaningful only for the options detail view; bringing them into the overview would make the filter bar asymmetrical and harder to explain.

### Currency mismatch — explicit decision

#### Option A — Convert options economics to EUR first

| Item | Assessment |
|------|------------|
| Capability baseline | `backend/src/portfolio/fx_service.py` already exists and can return ECB EUR rates |
| Limitation | Current service only fetches roughly **90 days** of history from ECB and caches daily; that is insufficient for older options positions |
| Data gap | Existing options positions carry **no explicit currency or stored FX rate**, only raw premium numbers |
| Implication | This is not a UI-only task; it becomes a data-foundation project touching storage assumptions and historical conversion strategy |

#### Option B — Side-by-side overview with no blended money total

| Item | Assessment |
|------|------------|
| Accuracy | Safe now; no synthetic EUR+USD headline |
| Scope | Fits the dashboard task without altering historical options storage |
| UX cost | User sees two monetary tracks rather than one total |
| Upgrade path | Can evolve later into a single EUR-based overview when options FX is added |

### Recommendation on currency handling

**Recommend Option B now.**

Reason: a naive blended total would be subtly wrong, and Option A is materially larger than a dashboard feature because the current FX helper is not enough for long historical coverage and options docs do not persist rate metadata. The overview should therefore present:

- `Options net (USD native)`
- `Dividends net (EUR)`
- aligned monthly rows and by-symbol rows
- **no single combined cash KPI or chart sum** until FX support is promoted to a dedicated backend scope

### Backend endpoint proposal

**Endpoint:** `GET /api/economics/overview`

**Query params:**
- `year`
- `month` (csv)
- `symbol` (csv)
- `source` (optional: `options|dividends|both`)

**Response shape sketch:**

```json
{
  "summary": {
    "options_net_native": 0,
    "options_currency": "USD",
    "dividends_net_eur": 0,
    "total_option_positions": 0,
    "total_dividend_events": 0,
    "total_symbols": 0,
    "fx_mode": "side_by_side"
  },
  "monthly": [
    {
      "month": "2026-01",
      "options_net_native": 0,
      "dividends_net_eur": 0,
      "option_positions": 0,
      "dividend_events": 0
    }
  ],
  "by_symbol": [
    {
      "symbol": "AAPL",
      "options_net_native": 0,
      "dividends_net_eur": 0,
      "option_positions": 0,
      "dividend_events": 0
    }
  ],
  "filters": {
    "years": [],
    "symbols": []
  },
  "applied_filters": {
    "year": 2026,
    "months": [1, 2],
    "symbols": ["AAPL"],
    "source": "both"
  },
  "meta": {
    "options_bucket_field": "opened_at",
    "dividends_bucket_field": "trade_date",
    "options_currency_native": "USD",
    "dividends_currency": "EUR",
    "combined_total_available": false
  }
}
```

### Backend aggregation rules

| Source | Include rule | Bucket field | Value field |
|--------|--------------|--------------|-------------|
| Options | Existing `_build_economics_report` semantics after filters | `opened_at` | `net_income` equivalent per row |
| Dividends | `ledger_txn`, `txn_type == DIVIDEND`, active/corrected-live rows only | `trade_date` | `net.eur_amount` |

---

## View 2 — Options Detail

### What stays as-is

The current options view remains the reference detail experience:

- KPI `SummaryRow`
- Filters: year, months, symbols, type pills, status pills
- URL-synced filter state
- Monthly table
- By-symbol table
- Charts panel (`MonthlyNetChart`, `TypeDoughnut`)
- Sortable per-position detail table
- Existing backend endpoint: `GET /api/economics`

### What changes

| Area | Change |
|------|--------|
| **Route** | Canonical path becomes `/economics/options` |
| **Page shell** | Add the shared Economics tab strip so users can jump to Overview / Options / Dividends |
| **Labels** | Rename page title contextually to “Economics · Options” or equivalent |
| **Linking** | All internal links should point to `/economics/options` rather than the legacy root |

### Migration concern

If the root route moves to Overview, bookmarked `/economics?...` links are the only real compatibility risk.

**Recommended handling:**
- Redirect only when legacy-only params prove options intent (`type`, `status`, legacy sort keys).
- Keep shared params working on both pages.
- Treat `/economics/options` as canonical in new navigation, copied links, and tests.

### No functional redesign needed

I do **not** recommend redesigning the current options dashboard beyond the route and shared tab shell. The existing structure already matches the user’s requested “detail” view.

---

## View 3 — Dividends Detail

### Objective

Create a dividends page that feels like the options page: same density, same dashboard posture, same shareable filter model — but grounded in **ledger dividend events** instead of options positions.

### KPI Row

| KPI | Meaning | Source field |
|-----|---------|-------------|
| **Total gross** | Gross dividend income in EUR | `gross.eur_amount` |
| **Total withholding** | Source + destination withholding in EUR | `withholding.source.amount_eur` + `withholding.destination.amount_eur` |
| **Total net** | Net dividend income in EUR | `net.eur_amount` |
| **Dividend count** | Count of dividend rows after filters | Number of matching `DIVIDEND` movements |
| **Effective withholding %** | `total_withholding_eur / total_gross_eur` | Portfolio-level rate |

Optional sixth KPI if needed later: **Accounts represented**.

### Filters

| Filter | Keep/Add | Notes |
|--------|----------|------|
| `year` | Keep | Mandatory parity with options |
| `months[]` | Keep | Mandatory parity with options |
| `symbols[]` | Keep | Mandatory parity with options |
| `account_id[]` | **Add** | Recommended because dividends are account-partitioned and users may want broker/account drill-down |

**Not recommended in v1:** `type` filter, because dividends have no equivalent of call/put. If later needed, add a dedicated dividend-specific facet such as `cash|mixed|shares` once product semantics are settled.

### Sections / dashboards

| Section | Description | Notes |
|---------|-------------|------|
| **Monthly table** | One row per month | Columns: month, gross_eur, withholding_eur, net_eur, dividend_count |
| **By-symbol table** | Aggregate by ticker | Columns: symbol, gross_eur, withholding_eur, net_eur, dividend_count |
| **Chart 1 — Monthly net** | Bar chart equivalent to options monthly net | Direct visual parity |
| **Chart 2 — Gross vs net / withholding impact** | Recommended replacement for calls-vs-puts doughnut | Tooltip can show withholding % and absolute withheld EUR |
| **Dividends detail table** | Sortable movement-level table | Mirrors options `PositionsDetail` pattern |

### Recommended charts

**Primary recommendation:**
1. **Monthly Net Dividends** bar chart
2. **Gross vs Net by Symbol** grouped/stacked chart

**Runner-up alternative:**
- Replace chart 2 with **Withholding % by Symbol** if users care more about tax drag than gross-vs-net shape.

### Detail table proposal

| Column | Purpose |
|--------|---------|
| `trade_date` | Payment-date chronology |
| `account_id` | Account/broker scoping |
| `symbol` | Ticker |
| `gross_amount` | Native transaction amount |
| `gross_currency` | Native currency label |
| `gross_eur` | Canonical comparable amount |
| `fees_eur` | Fees in EUR |
| `withholding_source_eur` | Origin withholding |
| `withholding_destination_eur` | Destination withholding |
| `withholding_total_eur` | Total tax drag |
| `net_eur` | Net received |
| `correction_status` | Active/corrected visibility |

**Sorting:** align with options detail-table behavior: sortable on date, symbol, account, gross_eur, withholding_total_eur, net_eur.

### Backend endpoint proposal

**Endpoint:** `GET /api/economics/dividends`

**Query params:**
- `year`
- `month` (csv)
- `symbol` (csv)
- `account_id` (csv, optional)

**Response shape sketch:**

```json
{
  "summary": {
    "total_gross_eur": 0,
    "total_fees_eur": 0,
    "total_withholding_eur": 0,
    "total_net_eur": 0,
    "effective_withholding_pct": 0,
    "total_dividends": 0,
    "total_accounts": 0
  },
  "monthly": [
    {
      "month": "2026-01",
      "gross_eur": 0,
      "fees_eur": 0,
      "withholding_source_eur": 0,
      "withholding_destination_eur": 0,
      "withholding_total_eur": 0,
      "net_eur": 0,
      "dividend_count": 0
    }
  ],
  "by_symbol": [
    {
      "symbol": "AAPL",
      "gross_eur": 0,
      "withholding_total_eur": 0,
      "net_eur": 0,
      "dividend_count": 0
    }
  ],
  "positions": [
    {
      "id": "txn_...",
      "account_id": "acct_1",
      "security_id": "XNAS:AAPL",
      "symbol": "AAPL",
      "trade_date": "2026-02-15",
      "gross_amount": 0,
      "gross_currency": "USD",
      "gross_eur": 0,
      "fees_eur": 0,
      "withholding_source_eur": 0,
      "withholding_destination_eur": 0,
      "withholding_total_eur": 0,
      "net_eur": 0,
      "correction_status": "ACTIVE"
    }
  ],
  "filters": {
    "years": [],
    "symbols": [],
    "account_ids": []
  },
  "applied_filters": {
    "year": 2026,
    "months": [1, 2],
    "symbols": ["AAPL"],
    "account_ids": ["acct_1"]
  },
  "meta": {
    "bucket_field": "trade_date",
    "value_field": "net.eur_amount"
  }
}
```

### Backend aggregation rules

| Rule | Recommendation |
|------|----------------|
| Included rows | `doc_type == ledger_txn`, `txn_type == DIVIDEND` |
| Status filter baseline | Only economically live rows (`correction_status == ACTIVE` or equivalent live-state rule) |
| Bucket field | `trade_date` |
| Money semantics | Net reporting uses `net.eur_amount`; gross uses `gross.eur_amount`; withholding sums source + destination EUR amounts |
| Symbol axis | Prefer `ticker` in UI; retain `security_id` in detail rows for stability |

---

## Frontend Data Model / Types Sketch

### Proposed types

| Type | Fields |
|------|--------|
| `DividendsSummary` | `total_gross_eur`, `total_fees_eur`, `total_withholding_eur`, `total_net_eur`, `effective_withholding_pct`, `total_dividends`, `total_accounts` |
| `DividendsMonthlyRow` | `month`, `gross_eur`, `fees_eur`, `withholding_source_eur`, `withholding_destination_eur`, `withholding_total_eur`, `net_eur`, `dividend_count` |
| `DividendsBySymbolRow` | `symbol`, `gross_eur`, `withholding_total_eur`, `net_eur`, `dividend_count` |
| `DividendPosition` | `id`, `account_id`, `security_id`, `symbol`, `trade_date`, `gross_amount`, `gross_currency`, `gross_eur`, `fees_eur`, `withholding_source_eur`, `withholding_destination_eur`, `withholding_total_eur`, `net_eur`, `correction_status` |
| `DividendsFilters` | `years`, `symbols`, `account_ids` |
| `DividendsAppliedFilters` | `year`, `months`, `symbols`, `account_ids` |
| `DividendsReport` | `summary`, `monthly`, `by_symbol`, `positions`, `filters`, `applied_filters`, `meta` |
| `EconomicsAggregatedSummary` | `options_net_native`, `options_currency`, `dividends_net_eur`, `total_option_positions`, `total_dividend_events`, `total_symbols`, `fx_mode` |
| `EconomicsAggregatedMonthlyRow` | `month`, `options_net_native`, `dividends_net_eur`, `option_positions`, `dividend_events` |
| `EconomicsAggregatedBySymbolRow` | `symbol`, `options_net_native`, `dividends_net_eur`, `option_positions`, `dividend_events` |
| `EconomicsAggregatedFilters` | `years`, `symbols` |
| `EconomicsAggregatedAppliedFilters` | `year`, `months`, `symbols`, `source` |
| `EconomicsAggregatedReport` | `summary`, `monthly`, `by_symbol`, `filters`, `applied_filters`, `meta` |

### Naming notes

- Keep `positions` in `DividendsReport` only if we want exact parity with existing `EconomicsReport`; otherwise `movements` is semantically cleaner. If parity is valued more highly than purity, keep `positions` and label the UI section “Dividend Detail”.
- Keep `months` and `symbols` naming identical to current `EconomicsReport` to simplify shared filter wiring.

---

## Open Questions / Follow-ups

| Topic | Open question | Why it matters |
|-------|---------------|----------------|
| **FX for overview** | Do we fund a follow-up project to convert historical options premiums into EUR with durable rate coverage beyond the current ECB ~90-day helper? | Needed before any trustworthy unified monetary total |
| **Options currency assumption** | Should options docs explicitly persist `currency` (likely USD) and, later, `fx_rate`/`eur_amount` at ingest time? | Avoids future inference and historical recomputation gaps |
| **Dividend scope** | Should the dividends view include only `txn_type == DIVIDEND`, or later also selected corporate-action cash distributions / rights-like events? | Prevents semantic drift between “dividends” and broader income events |
| **Mixed/scrip dividends** | If a dividend event has a share leg, do we show only cash net in Economics, or also expose a non-cash informational marker? | Affects user interpretation of dividend income vs value received |
| **Account filter on overview** | Should Overview remain source-symmetric (no account filter) or add an account filter that only affects dividends? | Symmetry vs practical broker drill-down |
| **Legacy route communication** | Is a release-note-only migration acceptable for ambiguous old `/economics?...` bookmarks? | Impacts user surprise on first navigation after rollout |
| **Detail-table naming** | `positions` vs `movements` for dividends response and component naming | Frontend parity vs semantic clarity |

---

## Delivery Sequence Recommendation

| Phase | Scope |
|-------|-------|
| **Phase 1** | Add routed Economics shell and tab navigation; keep options detail intact at `/economics/options` |
| **Phase 2** | Add Overview endpoint/page with side-by-side options/dividends monthly view and by-symbol table |
| **Phase 3** | Add Dividends endpoint/page mirroring options detail UX |
| **Phase 4** | Separate FX foundation project if a true combined EUR overview is desired |

**Implementation bias:** get the IA and page boundaries right first, then add dividends detail, then revisit FX only when the product explicitly needs a blended total.
