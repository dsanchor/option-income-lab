# Design: Fiscal Reports

**Date:** 2026-09-09
**Author:** Danny
**Requested by:** dsanchor
**Status:** PROPOSED — design only, no application code changes in this task.

## Authoritative sources

- User request and placement target: `frontend/src/components/TopNav.tsx:26-31,67-71`
- Existing economics pure-module pattern: `backend/src/dividends_economics.py:1-173`
- Existing pure-predicate pattern: `backend/src/calendar_visibility.py:1-56`
- Existing economics thin endpoints: `backend/web/app.py:1108-1271`
- Ledger canonicalization and withholding normalization: `backend/src/portfolio/cosmos_portfolio.py:93-139,631-704,826-853,876-1048,1112-1113,1446-1458`
- BUY/SELL CSV parser shapes: `backend/src/portfolio/parsers/purchases.py:1-119`, `backend/src/portfolio/parsers/sales.py:1-254`
- Imported ledger field construction: `backend/src/portfolio/import_service.py:606-702`
- Sales-type and FIFO implications: `backend/src/portfolio/holdings_service.py:124-130,213-226`
- Existing dividend view filter/URL-sync behavior: `frontend/src/components/DividendsView.tsx:668-722`
- Existing frontend export/download evidence: `frontend/src/lib/portfolio-api.ts:146`; no `createObjectURL`, `download=`, or CSV download matches were found under `frontend/src`
- Prior team decisions: `.squad/decisions.md:75,908-910,932-939,2039-2047,3122-3200`

---

## 1. Overview & Goals

Fiscal Reports should be a new **tax/fiscal lens** over the same portfolio ledger that already powers holdings and Economics. Economics answers “how is the portfolio performing?”; Fiscal Reports should answer “what happened in a given tax period, what was withheld, and what may still require declaration?”

I recommend a new routed page at **`/portfolio/fiscal-reports`** with a new **“Fiscal Reports”** item inside `DROPDOWNS.Investments`. This route has two practical advantages:

1. It does **not** collide with any existing frontend page route (`frontend/src/app/**/page.tsx` currently has no fiscal-reports path).
2. It keeps the Investments dropdown highlighted automatically, because `TopNav` marks Investments active when the pathname starts with `/portfolio` (`frontend/src/components/TopNav.tsx:67-71`).

Primary goal hierarchy:

1. **Dividends first**: make dividend withholding clear and auditable.
2. **Also cover BUY/SELL**: year/month filtered fiscal rows with gross, fees, net, and sale subtype where relevant.
3. **Phase separation**: first visualize, then export exactly what is visualized.

---

## 2. Scope

### Core scope

#### A. Dividend fiscal view — the centerpiece

- Filter by **year**, **month(s)**, **symbol(s)**, **account(s)**
- Show one fiscal row per dividend event
- Show:
  - gross
  - fees
  - withholding at source
  - withholding at destination
  - total withholding
  - net
  - withholding taxonomy badge
  - derived “needs declaration” badge

#### B. BUY / SELL fiscal view

- Filter by **year**, **month(s)**, **symbol(s)**, **account(s)**
- Show:
  - gross
  - fees/commission
  - net
  - currency
  - EUR amounts when present in ledger
- SELL rows should additionally expose `sales_type` = `ACCIONES` vs `DERECHOS`, because holdings deliberately treat them differently (`backend/src/portfolio/holdings_service.py:213-226`; `.squad/decisions.md:3122-3200`).

### Explicitly out of core scope

- Legal/tax advice automation beyond deterministic data-derived flags
- XLSX generation in phase 1
- Full filing-form generation
- Automatic tax rules by country beyond the currently stored withholding data

---

## 3. Withholding Taxonomy (dividends)

### Recommended taxonomy

For each dividend fiscal row, derive:

- `SOURCE_ONLY`
- `DESTINATION_ONLY`
- `BOTH`
- `NEITHER`

and one extra boolean:

- `needs_manual_destination_declaration`

### Derivation logic

Using the same data shape already consumed by `build_dividends_economics_report`:

```text
source_eur = decimal((withholding.source or {}).amount_eur)
destination_eur = decimal((withholding.destination or {}).amount_eur)

if source_eur > 0 and destination_eur > 0:
  taxonomy = BOTH
elif source_eur > 0:
  taxonomy = SOURCE_ONLY
elif destination_eur > 0:
  taxonomy = DESTINATION_ONLY
else:
  taxonomy = NEITHER

needs_manual_destination_declaration = (source_eur > 0 and destination_eur == 0)
```

### Absent-vs-zero ambiguity: explicit ruling

**Ruling:** an absent withholding side and a present-but-zero withholding side should both collapse to **“no withholding on that side”** for Fiscal Reports.

Why this is safe in this codebase:

- `CosmosPortfolioService._ensure_ledger_detail_fields()` normalizes a missing withholding object to `{source: None, destination: None}` and strips each side down to either the side object or `None` (`backend/src/portfolio/cosmos_portfolio.py:121-139`).
- CSV imports only persist a withholding side when its amount is **strictly greater than zero**; otherwise the side is stored as `None` (`backend/src/portfolio/import_service.py:685-692`).
- The dividends aggregator already reads both shapes as numeric zero via `(withholding.get("source") or {})` and `(withholding.get("destination") or {})` before reading `amount_eur` (`backend/src/dividends_economics.py:92-104`).

So the taxonomy should depend on **amount > 0**, not on object presence.

### Important wording note

The user’s “debo declarar” intent is clear, but the app should label this as a **data-derived fiscal attention flag**, not definitive legal advice. Recommended wording:

- UI badge: **“Source withheld, no destination withholding recorded”**
- Secondary helper text: **“Likely requires manual review/declaration in your resident tax return.”**

That keeps the report useful without pretending to replace tax advice.

---

## 4. Composite dividend handling

### Decision: rows should be PER GROUP, not PER LEG, when a dividend belongs to a corporate-action group

**Recommendation:** if a dividend-related record has a `ca_group_id`, Fiscal Reports should render it at **event/group grain** rather than raw leg grain.

### Why

The ledger explicitly models composite corporate actions as multi-leg groups:

- all legs share one `ca_group_id` (`backend/src/portfolio/cosmos_portfolio.py:876-880`)
- `DIVIDEND_WITH_SCRIP` requires both `CASH_DIVIDEND` and `SHARE_ACQUISITION` legs (`backend/src/portfolio/cosmos_portfolio.py:146-162`)
- group correction/void flows are atomic at group level, not per leg (`backend/src/portfolio/cosmos_portfolio.py:1048-1360`)

If Fiscal Reports were per-leg:

- one economic dividend event could appear twice
- cash and stock legs could be mistaken for separate taxable events
- export totals would be harder to reconcile to what the user thinks of as “one dividend”

### Concrete row-grain rule

- **Ordinary cash dividend** (`txn_type == DIVIDEND`, no `ca_group_id`) → one row per movement
- **Composite dividend group** (`ca_event_type == DIVIDEND_WITH_SCRIP`) → one row per `ca_group_id`
  - cash columns come from the `CASH_DIVIDEND` / `txn_type == DIVIDEND` leg
  - companion stock columns come from the linked `SHARE_ACQUISITION` / `txn_type == BUY` leg
  - dividend withholding taxonomy is derived from the **cash dividend leg only**
- **Non-dividend corporate-action groups** such as share consolidations stay outside dividend taxonomy totals; they are not the centerpiece of this feature (`backend/src/portfolio/cosmos_portfolio.py:146-162`; `.squad/decisions.md:2039-2047`)

### Practical consequence

The dividend table becomes economically correct for tax review, while the raw ledger remains unchanged and still available elsewhere.

---

## 5. BUY/SELL fiscal fields

### Verified field-shape finding

The source CSV parsers for purchases and sales are **thin row parsers**:

- purchases: `price_per_share`, `quantity`, `total_cost`, `commission` (`backend/src/portfolio/parsers/purchases.py:45-119`)
- sales: `quantity`, `commission`, `total_proceeds`, `sales_type` (`backend/src/portfolio/parsers/sales.py:86-254`)

They do **not** themselves emit FX/EUR envelopes.

However, the ledger normalization layer does:

- imported BUY/SELL movements are persisted with `gross.amount/currency/eur_amount`, `fees.total/currency/total_eur`, `net.amount/currency/eur_amount`, and `fx` (`backend/src/portfolio/import_service.py:606-702`)
- manual BUY/SELL/DIVIDEND creation persists the same canonical money envelopes (`backend/src/portfolio/cosmos_portfolio.py:631-704`)

### Design implication

Fiscal Reports **can** show EUR columns for BUY and SELL because the **ledger** has them, even though the raw CSV parsers do not.

### Important caution

Historical import normalization currently sets imported `eur_amount` equal to the parsed numeric amount and `fx.rate = 1.000000000` (`backend/src/portfolio/import_service.py:677-700`). So BUY/SELL EUR columns are structurally available, but their trust level depends on how those rows were originally imported/captured.

Recommended UI behavior:

- Always show **native currency columns**
- Show **EUR columns** when available
- Add a small note: **“EUR values reflect ledger-normalized amounts; legacy imports may already be EUR-equivalent.”**

### SELL subtype treatment

- `ACCIONES` sales are ordinary stock disposals
- `DERECHOS` sales are rights disposals and do **not** reduce holdings shares (`backend/src/portfolio/holdings_service.py:213-226`; `.squad/decisions.md:932-933,3122-3200`)

Fiscal Reports should therefore:

- expose `sales_type`
- allow optional SELL subtype filtering
- clearly badge rights sales so they are not confused with normal share disposals

---

## 6. Data model / pure module sketch

### Module boundary

Follow the same pattern as `backend/src/dividends_economics.py` and `backend/src/calendar_visibility.py`: one **pure backend module** plus a **thin `app.py` endpoint wrapper**.

Recommended new module:

- `backend/src/fiscal_reports.py`

### Recommended entry point

```python
def build_fiscal_report(
    movements: list[dict],
    *,
    year: int | None = None,
    month_filter: list[int] | None = None,
    symbol_filter: list[str] | None = None,
    account_filter: list[str] | None = None,
    family: str = "all",                  # all | dividends | buys | sells
    sales_type_filter: list[str] | None = None,
) -> dict:
    ...
```

### Input contract

`movements` should be the same mixed active ledger list returned by `CosmosPortfolioService.get_all_movements_for_holdings()` (`backend/src/portfolio/cosmos_portfolio.py:1446-1458`), exactly like `build_dividends_economics_report()` already expects (`backend/src/dividends_economics.py:172-173`).

### Internal stages

1. **Extract active ledger rows** into canonical fiscal candidates
2. **Group dividend-related corporate-action events by `ca_group_id`**
3. **Derive taxonomy + declaration flags**
4. **Apply filters**
5. **Build summaries, monthly totals, detail rows, and available filter values**

### Suggested response shape

```json
{
  "summary": {
    "family": "dividends",
    "row_count": 0,
    "gross_eur": 0,
    "fees_eur": 0,
    "withholding_source_eur": 0,
    "withholding_destination_eur": 0,
    "withholding_total_eur": 0,
    "net_eur": 0,
    "buy_gross_eur": 0,
    "sell_gross_eur": 0,
    "dividend_taxonomy_counts": {
      "source_only": 0,
      "destination_only": 0,
      "both": 0,
      "neither": 0,
      "needs_manual_destination_declaration": 0
    }
  },
  "monthly": [
    {
      "month": "2026-03",
      "buy_gross_eur": 0,
      "sell_gross_eur": 0,
      "dividend_gross_eur": 0,
      "dividend_net_eur": 0,
      "row_count": 0
    }
  ],
  "rows": [
    {
      "row_id": "mvt_x or cag_x",
      "row_kind": "movement|ca_group",
      "family": "DIVIDEND|BUY|SELL",
      "trade_date": "2026-03-15",
      "symbol": "IBE",
      "account_id": "ibkr",
      "ca_group_id": "cag_x",
      "ca_event_type": "DIVIDEND_WITH_SCRIP",
      "sales_type": null,
      "gross_amount": 0,
      "gross_currency": "EUR",
      "gross_eur": 0,
      "fees_eur": 0,
      "withholding_source_eur": 0,
      "withholding_destination_eur": 0,
      "withholding_total_eur": 0,
      "withholding_taxonomy": "SOURCE_ONLY",
      "needs_manual_destination_declaration": true,
      "net_eur": 0,
      "share_leg_quantity": "0",
      "share_leg_cost_basis_status": "ZERO_COST"
    }
  ],
  "filters": {
    "years": [2026, 2025],
    "symbols": ["IBE", "MSFT"],
    "account_ids": ["ibkr", "ing"],
    "families": ["all", "dividends", "buys", "sells"],
    "sales_types": ["ACCIONES", "DERECHOS"]
  },
  "applied_filters": {
    "year": 2026,
    "months": [1, 2, 3],
    "symbols": ["IBE"],
    "account_ids": ["ing"],
    "family": "dividends",
    "sales_types": []
  },
  "meta": {
    "dividend_row_grain": "group_when_ca_group_id_present",
    "withholding_zero_rule": "amount_gt_zero_only",
    "source": "active_ledger_movements"
  }
}
```

---

## 7. API shape sketch

### Recommended backend endpoints

Phase 1:

- `GET /api/fiscal/reports`

Phase 2:

- `GET /api/fiscal/reports/export`

### Why this API namespace

- avoids collision with the existing `/api/economics*` family (`backend/web/app.py:1108-1271`)
- reflects that this is a **new fiscal lens**, not just another economics tab
- keeps the implementation aligned with the existing thin-`app.py` pattern requested for this feature

### Query parameters

For `GET /api/fiscal/reports`:

- `year=2026`
- `month=1,2,3`
- `symbol=IBE,MSFT`
- `account_id=ing,ibkr`
- `family=all|dividends|buys|sells`
- `sales_type=ACCIONES,DERECHOS` (optional; meaningful only for sells)

For `GET /api/fiscal/reports/export`:

- same filters as above
- `format=csv` initially
- optional later: `sort_by`, `sort_dir`

### Thin endpoint behavior

The wrapper in `backend/web/app.py` should mirror existing economics endpoints:

1. parse comma-separated filters
2. get portfolio container
3. load `movements = portfolio_svc.get_all_movements_for_holdings()`
4. call `build_fiscal_report(...)`
5. return JSON in phase 1, CSV stream in phase 2

---

## 8. UI sketch

### Navigation

Add one new Investments dropdown item:

- label: **Fiscal Reports**
- href: **`/portfolio/fiscal-reports`**

Recommended placement inside `DROPDOWNS.Investments`:

1. Symbols
2. Movements
3. **Fiscal Reports**
4. Accounts
5. Calendar
6. Action Plans

Reason: it sits naturally next to the movement ledger, which is its primary source.

### Page layout

#### Header

- Title: **Fiscal Reports**
- Subtitle: “Fiscal view of buys, sells, and especially dividends, with withholding clarity and export-ready filters.”

#### Filter row

Reuse the current dividends/economics interaction model (`frontend/src/components/DividendsView.tsx:668-722`):

- Year select
- Months multiselect
- Symbols multiselect
- Accounts multiselect
- Family pills: `All | Dividends | Buys | Sells`
- Conditional SELL subtype pills: `All | Stocks (ACCIONES) | Rights (DERECHOS)`

#### Summary cards

When `family = dividends`, emphasize:

- Gross dividends
- Source withholding
- Destination withholding
- Net dividends
- “Needs manual declaration” count

When `family = buys/sells/all`, show family-appropriate totals instead.

#### Detail table

Recommended columns:

| Column | Dividends | Buys | Sells |
|---|---|---|---|
| Date | ✓ | ✓ | ✓ |
| Symbol | ✓ | ✓ | ✓ |
| Account | ✓ | ✓ | ✓ |
| Family | ✓ | ✓ | ✓ |
| Gross | ✓ | ✓ | ✓ |
| Fees | ✓ | ✓ | ✓ |
| Source WHT | ✓ | — | — |
| Destination WHT | ✓ | — | — |
| Net | ✓ | ✓ | ✓ |
| Taxonomy badge | ✓ | — | — |
| Needs declaration badge | ✓ | — | — |
| Sales type | — | — | ✓ |

For composite dividend rows, add a compact secondary caption inside the row:

- “Includes scrip share leg: X shares, cost basis status ZERO_COST/COMPLETE”

### Monthly section

One monthly rollup table above the detail table:

- buys gross/net
- sells gross/net
- dividends gross/net/withholding

This keeps the page useful for tax-period review before export exists.

---

## 9. Export design

### Recommendation: backend-generated CSV, not client-generated CSV

I recommend **server-side CSV generation** in phase 2.

### Why

1. The exported rows must match the **server-derived** grouping and taxonomy rules exactly.
2. Composite dividend grouping is easier to flatten once, on the backend, than to duplicate in the browser.
3. There is no existing frontend download/export pattern to mirror. The only `Blob(...)` usage I found is for turning pasted CSV text into an upload file during import (`frontend/src/lib/portfolio-api.ts:146`), not for downloads.
4. The generic Next `/api/portfolio/*` proxy currently assumes JSON responses (`frontend/src/app/api/portfolio/[[...slug]]/route.ts:23-26`), so a CSV export needs a purpose-built pass-through anyway.

### “Export what I’m currently visualizing” — exact meaning

Export must respect:

- current **filters**
- current **family**
- current **SELL subtype filter**
- current **grouping rule** (grouped dividend events, not raw legs)

It should **not** silently export the full unfiltered ledger.

### Suggested CSV columns

Minimum CSV schema:

```text
date,symbol,account_id,family,row_kind,ca_group_id,ca_event_type,sales_type,
gross_currency,gross_amount,gross_eur,fees_eur,
withholding_source_eur,withholding_destination_eur,withholding_total_eur,
withholding_taxonomy,needs_manual_destination_declaration,
net_eur,share_leg_quantity,share_leg_cost_basis_status
```

### Future stretch

- `format=xlsx`
- Spanish-Excel-friendly dialect toggle (`delimiter=semicolon`, localized headers)

But CSV should be the only planned export format for the first export phase.

---

## 10. My proposed additions

These are useful, but should be clearly treated as **after-core** additions:

### A. Fiscal-year withholding breakdown by country

If/when country fields are consistently populated inside withholding legs, add:

- by-source-country totals
- by-destination-country totals
- country pairs (source + destination)

This would help with foreign tax credit review.

### B. “Manual review queue” preset

One-click preset filter:

- `family = dividends`
- only rows where `needs_manual_destination_declaration = true`

This directly serves the user’s most explicit dividend pain point.

### C. Annual tax summary export

A second export flavor, separate from raw row export:

- yearly totals
- taxonomy counts
- source/destination withholding totals
- rights-sale totals called out separately

This would be useful for filing prep, but only after the raw “export what I see” requirement ships.

### D. Reconciliation badge

For composite dividend groups:

- “cash only”
- “cash + scrip”
- “share-only corporate action”

That would help advanced users audit complex events.

---

## 11. Open Questions

1. **Resident tax jurisdiction fixed to Spain?**
   The user’s examples strongly imply Spanish reporting logic. The `needs_manual_destination_declaration` flag is sensible for Spain, but if this app may later support other resident-tax regimes, the wording should remain descriptive rather than legal.

2. **Should share-only scrip/rights-issue events appear in v1 Fiscal Reports?**
   My recommendation is no for the core dividend table unless they are attached to a cash dividend group; otherwise they risk broadening the feature away from the user’s stated priority.

3. **How much trust do we place in BUY/SELL EUR columns from historical imports?**
   The ledger has EUR envelopes, but imported rows currently normalize `eur_amount` equal to the parsed amount with `fx.rate = 1`. That is workable for display, but we should confirm whether legacy imported BUY/SELL data is always already EUR-equivalent.

4. **CSV dialect preference for export?**
   Spanish users often want semicolon-separated CSV with decimal comma for Excel. The default can still be standard CSV, but this is worth confirming before phase 2.

---

## 12. Phased delivery plan

### Phase 1 — Visualize only

Deliver:

- new pure module `backend/src/fiscal_reports.py`
- thin `GET /api/fiscal/reports` wrapper in `backend/web/app.py`
- new frontend page `/portfolio/fiscal-reports`
- TopNav Investments entry
- filters
- summary cards
- monthly rollup
- detail table
- dividend withholding taxonomy badges
- `needs_manual_destination_declaration` badge

Do **not** deliver export in this phase.

### Phase 2 — Export what is visualized

Deliver on top of phase 1:

- `GET /api/fiscal/reports/export?format=csv`
- frontend export button
- CSV file matching the currently filtered/visualized row set

No backend data-model change should be required if phase 1 ships with the recommended grouped-row contract.

---

## Final recommendation

Ship Fiscal Reports as a **portfolio-routed fiscal review page** centered on **dividend withholding clarity**, but still capable of showing BUY and SELL fiscal rows. Use **grouped dividend-event rows** for composite dividends, treat **absence and zero withholding as equivalent to “no withholding on that side,”** and keep export as a **strict phase 2** concern built on top of the same grouped JSON contract.
