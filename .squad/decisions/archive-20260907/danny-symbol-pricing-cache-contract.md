# Symbol Pricing Cache — Architecture & Implementation Contract

**Date:** 2026-09-07  
**Author:** Danny (Lead/Architect)  
**Status:** PROPOSED — awaiting user review  
**Impact:** Scheduled symbol pricing cache with multi-currency support, FX conversion to EUR, Current Value computation, and Total Portfolio Value KPI card  

---

## 1. Context & User Requirement

The Symbols table currently displays `price` sourced from the enrichment pipeline's `metrics.current_price` field, which is a raw number from yfinance with **no currency metadata**. This is misleading for an international portfolio:

- **US (XNYS/XNAS):** Prices in USD ($)  
- **Spain (XMAD):** Prices in EUR (€)  
- **UK (XLON):** Yahoo returns prices in **GBp (pence)**, not GBP — e.g. Unilever ULVR.L at 4,815 means £48.15  
- **Amsterdam (XAMS):** Prices in EUR (€)  
- **Switzerland (XSWX):** Prices in CHF  

**Requirements:**
1. Display prices with correct quote currency/unit labels  
2. Add a `Price in EUR` column using FX conversion  
3. Add a scheduled job (hourly, 09:00–23:00 UTC Mon–Fri) that populates a pricing cache  
4. Cache becomes the authoritative source for the Symbols table  
5. Per-symbol `Current Value` = shares × EUR price  
6. New KPI card: `Current Value` (total portfolio value at market) beside `Current Investment`  

---

## 2. Scheduler — `symbol_pricing` Scheduled Task

### 2.1 Registration Pattern

Follow the existing `TaskRegistry.register()` convention used by all 11 current jobs in `backend/src/main.py`.

**Files to change:**
- `backend/config.yaml` — add `symbol_pricing` config block  
- `backend/src/main.py` — add `run_symbol_pricing_job()`, `_run_symbol_pricing_async()`, `reschedule_symbol_pricing()`, `registry.register(...)` call, setup logging block  
- `backend/web/app.py` — add `reschedule_symbol_pricing` route (same pattern as other reschedule endpoints)  

**Config block (`config.yaml`):**
```yaml
symbol_pricing:
  enabled: true
  cron: "0 9-23 * * 1-5"  # Hourly, 09:00–23:00 UTC, Mon–Fri
```

**Cron semantics:**
- `0 9-23 * * 1-5` — fires at minute 0 of hours 9, 10, 11, …, 23 UTC, Monday through Friday  
- **Inclusive:** Both 09:00 and 23:00 fire (cron range `9-23` is inclusive on both ends)  
- **Timezone:** UTC (container default, same as all other jobs)  
- **Weekdays only:** Mon–Fri (matching `portfolio_enrichment`)  

**Startup behavior:** No startup catch-up (unlike `best_options`). The cache warms on first scheduled tick. Cold cache = frontend falls back to enrichment price (see §9 Migration).

**Retries:** None at scheduler level. Individual symbol failures are logged and skipped (partial success). FX fetch failure is fatal for the run — logged, run aborted, existing cache untouched.

**Overlap guard:** Standard `task.running` guard in `TaskRegistry.execute_due_tasks()` — if previous run still in progress, skip and advance `next_run`.

**Idempotency:** Re-running the same hour overwrites cache with fresh data. No deduplication needed — latest-wins semantics.

### 2.2 Task Name & Display

```python
self.registry.register(
    "symbol_pricing",
    "Symbol Pricing",
    "symbol_pricing",
    "0 9-23 * * 1-5",
    self.run_symbol_pricing_job,
    has_extra_config=False,
)
```

---

## 3. Cache Storage Model

### 3.1 Storage Location — Existing `symbols` Container

**Decision:** Store pricing cache data on the existing `symbol_config` document, in a new top-level field `pricing_cache`. This reuses the same Cosmos container and partition key (`/symbol`) as enrichment data. **No new container or infrastructure.**

**Rationale:** The `symbol_config` document already stores `enrichment` (updated by `portfolio_enrichment` job). Adding a `pricing_cache` sibling field is the lowest-friction approach — same read path, same partition, single-document atomic update. The pricing cache job and enrichment job run independently; each updates its own field.

### 3.2 Document Shape — `pricing_cache` Field

```python
{
    # Existing symbol_config fields (id, symbol, doc_type, enrichment, etc.)
    # ...
    
    "pricing_cache": {
        # Quote from provider
        "raw_price": 4815.0,          # Exact value from yfinance (float)
        "quote_currency": "GBp",      # Currency code as reported by yfinance
        "quote_unit": "minor",        # "major" or "minor" (GBp = minor, everything else = major)
        
        # Normalized to major currency unit
        "price_major": 48.15,         # After pence→pounds conversion if applicable
        "price_currency": "GBP",      # ISO 4217 3-letter code (always major unit)
        
        # FX conversion to EUR
        "fx_rate": "0.845230000",     # EUR per 1 unit of price_currency (Decimal string, 9dp)
        "fx_pair": "GBP/EUR",         # Human-readable pair
        "fx_rate_date": "2026-09-05", # ECB rate date used (may be prior business day)
        "fx_source": "ECB",           # Always "ECB" in this phase
        "price_eur": 40.71,           # price_major × fx_rate (float, 2dp for display)
        
        # Metadata
        "fetched_at": "2026-09-07T14:00:03Z",  # ISO 8601 UTC
        "status": "ok",              # "ok" | "error" | "stale"
        "error_message": null,        # Non-null only when status = "error"
        "run_id": "20260907T140000Z"  # Identifies the pricing run (hour-level)
    }
}
```

### 3.3 Field Semantics

| Field | Type | Description |
|-------|------|-------------|
| `raw_price` | float | Exact `regularMarketPrice` or `currentPrice` from `yf.Ticker(symbol).info` |
| `quote_currency` | string | `info["currency"]` from yfinance. Yahoo returns `"GBp"` for LSE pence, `"USD"` for US, `"EUR"` for Eurozone, `"CHF"` for Swiss |
| `quote_unit` | string | `"minor"` if `quote_currency` matches a known minor-unit code (GBp, GBX, ILA, ZAc); `"major"` otherwise |
| `price_major` | float | After minor→major conversion: `raw_price / 100` for GBp/GBX; `raw_price` otherwise. **Pence conversion happens exactly once here.** |
| `price_currency` | string | ISO 4217 major-unit code derived from `quote_currency`: `GBp`/`GBX` → `GBP`; all others pass through unchanged |
| `fx_rate` | string | ECB rate: EUR per 1 unit of `price_currency`. Decimal string with 9dp (reuses `fx_service.get_fx_rate()`). `"1.000000000"` when `price_currency == "EUR"` |
| `fx_pair` | string | `"{price_currency}/EUR"` for display |
| `fx_rate_date` | string | ISO date of the ECB rate used (weekday fallback per existing `fx_service` logic) |
| `fx_source` | string | Always `"ECB"` (single source; expandable later) |
| `price_eur` | float | `price_major × Decimal(fx_rate)`, rounded to 2dp |
| `fetched_at` | string | UTC timestamp of this pricing fetch |
| `status` | string | `"ok"` = fresh valid data; `"error"` = fetch failed (stale data preserved); `"stale"` = data older than staleness threshold |
| `error_message` | string\|null | Diagnostic when `status == "error"` |
| `run_id` | string | Timestamp-based run identifier for the batch |

### 3.4 Staleness

A `pricing_cache` entry is considered **stale** when `fetched_at` is more than **2 hours** old (configurable future). The overview endpoint marks `status: "stale"` on read when this condition is met. The frontend renders stale prices with a visual indicator.

### 3.5 No Separate FX Document

**Decision:** FX rate snapshots are embedded per-symbol in `pricing_cache`, not stored in a separate Cosmos document. All symbols in a single pricing run use the same FX rates (fetched once at run start), so they're coherent by construction. This avoids cross-partition reads and additional document management.

The pricing job fetches all needed FX rates at the start of the run (one `get_fx_rate()` call per unique `price_currency`), then distributes the same rate to all symbols sharing that currency.

---

## 4. Currency Semantics — Critical Rules

### 4.1 Quote Currency Source

**Primary source:** `yf.Ticker(symbol).info["currency"]` — this is the provider's own metadata for the quote currency. Yahoo returns:
- `"USD"` for US equities  
- `"EUR"` for XMAD, XAMS equities  
- `"GBp"` for XLON equities (pence, NOT pounds)  
- `"CHF"` for XSWX equities  

**Validation against SecurityMaster/MIC:** After obtaining `info["currency"]`, validate it is plausible for the listing MIC. The validation table:

| MIC | Expected currencies |
|-----|-------------------|
| XNYS, XNAS | USD |
| XMAD | EUR |
| XAMS | EUR |
| XLON | GBp, GBX, GBP |
| XSWX | CHF |
| XETR | EUR |
| XPAR | EUR |

**On mismatch:** Log a warning but **trust the provider**. The provider knows the actual quote denomination. MIC-based expectation is a sanity check, not a gate. Example: some LSE-listed ETFs quote in USD, not GBp — the provider is correct.

**If `info["currency"]` is missing or empty:** Skip this symbol with `status: "error"`, `error_message: "no quote currency from provider"`. **Never infer currency from MIC alone** — that would silently apply the wrong FX rate.

### 4.2 Minor-Unit Handling (GBp/GBX)

**Known minor-unit codes and their major-unit mappings:**

| Minor code | Major code | Divisor |
|------------|-----------|---------|
| `GBp` | `GBP` | 100 |
| `GBX` | `GBP` | 100 |
| `ILA` | `ILS` | 100 |
| `ZAc` | `ZAR` | 100 |

**Conversion rule (executed exactly once in the pricing job):**
```python
if quote_currency.upper() in ("GBP", "GBX"):
    # Pence → pounds
    quote_currency_display = quote_currency  # preserve "GBp" for UI
    price_major = raw_price / 100
    price_currency = "GBP"
    quote_unit = "minor"
```

**Critical invariant:** Pence-to-pounds conversion happens **only** in the pricing cache builder. Downstream consumers (overview endpoint, frontend) never divide by 100 — they use `price_major` and `price_eur` directly.

### 4.3 EUR is Identity

When `price_currency == "EUR"`:
- `fx_rate = "1.000000000"`
- `fx_pair = "EUR/EUR"`
- `price_eur = price_major`
- No ECB call needed

### 4.4 Unknown/Unsupported Currencies

If `price_currency` is not in the ECB rate cache (exotic currency), log a warning and set:
- `price_eur = null`
- `fx_rate = null`
- `status = "ok"` (price itself is valid; just no EUR conversion)

Frontend renders `price_eur` as "—" when null.

---

## 5. FX Source — Reuse Existing `fx_service`

### 5.1 FX Provider

Reuse `backend/src/portfolio/fx_service.py` — the existing ECB-based FX service. **No new FX infrastructure.**

**Files involved:**
- `backend/src/portfolio/fx_service.py` — existing, no changes needed  
- New pricing module calls `get_fx_rate(from_currency="USD", to_currency="EUR")`

### 5.2 Rate Coherence

At the start of each pricing run, the job:
1. Collects all unique `price_currency` values across the universe  
2. Calls `get_fx_rate(currency)` once per unique currency  
3. Stores the same rate for all symbols sharing that currency  

This ensures all USD symbols get the same USD/EUR rate within a single run. The `fx_rate_date` on each symbol's cache entry records which ECB date was used.

### 5.3 FX Failure Handling

If `get_fx_rate()` raises `FxUnavailableError` (ECB API down): **abort the entire pricing run**. Do not overwrite existing cache entries with price-only data lacking EUR conversion — that would break the EUR price column. Log the failure; the next hourly tick retries.

If `get_fx_rate()` raises `FxRateNotFoundError` for a specific currency: mark that currency's symbols with `price_eur = null`, `fx_rate = null`, `status = "ok"` (price is valid, conversion unavailable). Continue with other currencies.

---

## 6. Universe — Which Symbols Get Priced

### 6.1 Universe Rule

**All symbols with a `symbol_config` document in the `symbols` container.** This is `cosmos.list_symbols()` — the same universe used by `portfolio_enrichment`.

**Rationale:** The pricing cache serves the Symbols table overview. Every symbol visible in the table should have pricing data. This includes:
- Portfolio symbols (shares > 0) — needed for Current Value  
- Watchlist-only symbols — needed for price display  
- Historical zero-share symbols — included when `include_zero_portfolio=true`  

### 6.2 Yahoo Symbol Resolution

Reuse the existing `resolve_yfinance_symbol(ticker, exchange_mic, security_master_doc)` from `backend/src/portfolio/provider_symbols.py`. This is the same resolution path used by `portfolio_enrichment`.

**Files involved:**
- `backend/src/portfolio/provider_symbols.py` — existing, no changes  
- `backend/src/portfolio/cosmos_securities.py` — existing, for SecurityMaster lookup  

If `resolve_yfinance_symbol()` returns `None` (unknown MIC, no override): skip symbol, set `pricing_cache.status = "error"`, `error_message = "no Yahoo symbol mapping"`.

### 6.3 Rate Limiting

Use existing `YFinanceFetcher` rate limiting or a simple `time.sleep(0.5)` between symbols (same pattern as `portfolio_enrichment`). The pricing job only needs `info["currency"]`, `info["regularMarketPrice"]`/`info["currentPrice"]` — a lightweight fetch.

---

## 7. New Module — `symbol_pricing.py`

### 7.1 Location

**New file:** `backend/src/symbol_pricing.py`

### 7.2 Public API

```python
async def run_symbol_pricing(cosmos) -> dict:
    """Fetch current prices for all symbols, convert to EUR, update cache.
    
    Returns summary: {"status", "total", "success", "errors", "fx_rates_used"}
    """
```

### 7.3 Internal Flow

```
1. symbols = cosmos.list_symbols()
2. For each symbol, resolve Yahoo symbol (reuse resolve_yfinance_symbol)
3. Collect unique currencies needed (first pass or during fetch)
4. Fetch FX rates for all unique currencies (single ECB call via get_fx_rate)
5. For each symbol:
   a. Fetch yf.Ticker(yf_symbol).info
   b. Extract raw_price = info["regularMarketPrice"] or info["currentPrice"]
   c. Extract quote_currency = info["currency"]
   d. Validate quote_currency against MIC expectation (warn on mismatch)
   e. Apply minor-unit conversion if needed (GBp → GBP, divide by 100)
   f. Look up fx_rate for price_currency
   g. Compute price_eur = price_major × fx_rate
   h. Build pricing_cache dict
   i. cosmos.update_symbol_pricing_cache(symbol, pricing_cache)
6. Return summary
```

### 7.4 CosmosDB Method

**New method on `CosmosDBService`:**

```python
def update_symbol_pricing_cache(self, symbol: str, pricing_cache: dict) -> dict:
    """Update the pricing_cache field on a symbol_config document."""
    doc = self.get_symbol(symbol)
    if doc is None:
        raise ValueError(f"Symbol {symbol} not found")
    doc["pricing_cache"] = pricing_cache
    doc["updated_at"] = datetime.utcnow().isoformat() + "Z"
    return self.container.replace_item(item=doc["id"], body=doc)
```

**File:** `backend/src/cosmos_db.py`

---

## 8. API & Overview Endpoint Changes

### 8.1 `_compute_symbols_overview` — Backend

**File:** `backend/web/app.py`, function `_compute_symbols_overview()`

Add new fields to each row dict sourced from `pricing_cache`:

```python
pc = s.get("pricing_cache") or {}

row = {
    # ... existing fields ...
    
    # Pricing cache fields (new)
    "price": pc.get("price_major") if pc.get("status") == "ok" else (metrics.get("current_price")),
    "price_display_currency": pc.get("quote_currency") if pc.get("status") == "ok" else None,
    "price_currency": pc.get("price_currency") if pc.get("status") == "ok" else None,
    "price_eur": pc.get("price_eur") if pc.get("status") == "ok" else None,
    "pricing_fetched_at": pc.get("fetched_at"),
    "pricing_status": pc.get("status"),  # "ok" | "error" | "stale" | null (no cache yet)
    
    # Current Value (new) — only for portfolio rows with shares
    "current_value_eur": None,  # computed below
}

# Compute current_value_eur for portfolio rows
if portfolio_shares_str is not None and pc.get("price_eur") is not None:
    try:
        shares_dec = Decimal(str(portfolio_shares_str))
        price_eur_dec = Decimal(str(pc["price_eur"]))
        if shares_dec > 0:
            row["current_value_eur"] = str(
                (shares_dec * price_eur_dec).quantize(Decimal("0.01"))
            )
    except Exception:
        pass
```

**Price field behavior:** When `pricing_cache` exists with `status == "ok"`, use `price_major` (correct currency-aware price). When cache is absent or errored, fall back to `enrichment.metrics.current_price` (legacy behavior — backwards compatible).

### 8.2 Portfolio Summary — Total Current Value

Add to the `portfolio_summary` dict:

```python
# After computing all rows, sum current_value_eur across portfolio rows
total_current_value = Decimal("0")
has_any_current_value = False
for r in all_rows:
    cv = r.get("current_value_eur")
    if cv is not None:
        try:
            total_current_value += Decimal(str(cv))
            has_any_current_value = True
        except Exception:
            pass

portfolio_summary = {
    # ... existing fields ...
    "total_current_value_eur": str(total_current_value.quantize(Decimal("0.01"))) if has_any_current_value else None,
}
```

### 8.3 Backward Compatibility

- The `price` field continues to exist and returns a number — now sourced from `pricing_cache.price_major` when available, else `enrichment.metrics.current_price`  
- New fields (`price_display_currency`, `price_currency`, `price_eur`, `current_value_eur`, `pricing_status`) are additive — no existing field is removed or renamed  
- Frontend that hasn't been updated continues to work (sees `price` as before)  

### 8.4 Decimal/Rounding Convention

| Field | Precision | Type |
|-------|-----------|------|
| `price` (row) | 2dp | float |
| `price_eur` | 2dp | float |
| `fx_rate` | 9dp | string (Decimal) |
| `current_value_eur` | 2dp | string (Decimal) |
| `total_current_value_eur` | 2dp | string (Decimal) |

### 8.5 Missing-Data Behavior

| Scenario | `price` | `price_display_currency` | `price_eur` | `current_value_eur` |
|----------|---------|------------------------|-------------|-------------------|
| Cache ok | price_major | quote_currency (e.g. "GBp") | computed | shares × price_eur |
| Cache error | enrichment fallback | null | null | null |
| Cache stale | price_major (last good) | quote_currency | last good | shares × last good |
| No cache yet | enrichment fallback | null | null | null |
| No enrichment | null | null | null | null |
| Shares = 0 | price_major | quote_currency | computed | null (skip) |
| FX unavailable for ccy | price_major | quote_currency | null | null |

---

## 9. Frontend / UI Contract

### 9.1 TypeScript Types — `types/symbols.ts`

Add to `SymbolRow`:
```typescript
// Pricing cache fields (new)
price_display_currency?: string | null;  // "GBp", "USD", "EUR", "CHF"
price_currency?: string | null;          // "GBP", "USD", "EUR", "CHF" (always major)
price_eur?: number | null;
pricing_fetched_at?: string | null;
pricing_status?: "ok" | "error" | "stale" | null;
current_value_eur?: string | null;       // Decimal string
```

Add to `PortfolioSummary`:
```typescript
total_current_value_eur?: string | null;
```

### 9.2 Symbols Table Columns — `SymbolsTable.tsx`

**Current Price column** — update rendering:
```
Before: "$48.15"  (hardcoded $ prefix)
After:  "4,815 GBp" or "$48.15" or "€48.15" or "CHF 48.15"
```

Display logic:
- If `price_display_currency` exists:
  - `"USD"` → `$` prefix  
  - `"EUR"` → `€` prefix  
  - `"GBp"` or `"GBX"` → number followed by ` GBp` suffix  
  - `"CHF"` → `CHF ` prefix  
  - Other → currency code suffix  
- If `price_display_currency` is null → `$` prefix (legacy behavior)  

**New columns to add (after Price):**

| Column | Header | Align | Format | Data |
|--------|--------|-------|--------|------|
| Price EUR | `Price €` | right | `€48.15` | `row.price_eur` |
| Current Value | `Value €` | right | `€1,234.56` | `row.current_value_eur` |

**Column order:**
```
Symbol | Category | DGI | Tech | Entry | Momentum | Price | Price € | Shares | Avg Cost | Invested | Value € | Dividends | In Calls | Puts $ | 🗑️
```

**Sort keys to add:** `price_eur`, `current_value_eur`

### 9.3 Currency Formatting

```typescript
function formatPrice(price: number | null, currency: string | null): string {
  if (price == null || !isFinite(price)) return "—";
  if (!currency) return `$${num(price, 2)}`;  // legacy fallback
  
  const upper = currency.toUpperCase();
  if (upper === "USD") return `$${num(price, 2)}`;
  if (upper === "EUR") return `€${num(price, 2)}`;
  if (upper === "GBP" || upper === "GBX") return `${num(price * 100, 0)} GBp`;  // NO — wrong
  // Actually display raw_price with quote_currency label:
  // For GBp, the backend sends price = price_major (48.15) but display_currency = "GBp"
  // The frontend should show the raw_price (4815) for GBp display — BUT we're sending price_major
  // 
  // RESOLUTION: The backend sends BOTH raw_price and price_major. The frontend uses:
  // - price (= price_major) for sorting and computation
  // - For GBp display: show raw_price with "GBp" label
  // - For everything else: show price with currency symbol
}
```

**Revised approach:** Backend row includes `price` (= `price_major`, always in major units for computation) and `price_display_currency`. For the Price column display:

- If `price_display_currency` is `"GBp"` or `"GBX"`: display as `"{price_major * 100} GBp"` — reconstruct the pence display from the major-unit price. This is safe because the conversion is deterministic (multiply by 100).
- For `"USD"`: `$X.XX`
- For `"EUR"`: `€X.XX`
- For `"CHF"`: `CHF X.XX`
- For anything else: `X.XX {currency}`

This keeps `price` as a single major-unit number for sorting, filtering, and computation, while the display respects the user's expectation of seeing pence for LSE stocks.

### 9.4 Stale/Unavailable State Rendering

- `pricing_status == "stale"` → price displayed with reduced opacity (class `opacity-60`) and tooltip "Price data is stale (>2h old)"
- `pricing_status == "error"` → price from enrichment fallback, no currency label, tooltip "Pricing unavailable — showing enrichment estimate"
- `pricing_status == null` (no cache) → same as error (enrichment fallback)
- `price_eur == null` → "—" in the Price EUR column
- `current_value_eur == null` → "—" in the Value column

### 9.5 KPI Cards — Symbols Page

**Current layout (Row 2):**
```
[ Current Investment ] [ Realized Result ] [ Net Dividends ]
```

**New layout (Row 2):**
```
[ Current Investment ] [ Current Value ] [ Realized Result ] [ Net Dividends ]
```

**Current Value KPI card:**
```typescript
<KpiCard
  label="Current Value"
  value={kpiEur(totalCurrentValue)}
  tone="neutral"
  tooltip="Valor actual de mercado del portfolio en EUR (acciones × precio actual en EUR)."
/>
```

- Source: `portfolio_summary.total_current_value_eur`
- Displayed only when `portfolio_summary` exists and `total_current_value_eur` is non-null
- When null: card hidden (not shown as "—"), since it means no pricing data is available yet

### 9.6 Labels

All KPI labels remain in **English** (matching the recently approved convention: `Current Investment`, `Realized Result`, `Net Dividends`, and now `Current Value`).

---

## 10. Safety, Observability & Error Handling

### 10.1 No Wrong-Currency Fallback

**Hard rule:** If the pricing job cannot determine `quote_currency` for a symbol, it does **not** fall back to assuming USD or any other currency. The symbol gets `status: "error"` and the frontend uses the enrichment price (without currency label) — which is the existing behavior and strictly better than showing a price with the wrong currency.

### 10.2 No Overwriting Good Cache with Bad Data

If a pricing fetch fails for a symbol (yfinance error, network timeout), the existing `pricing_cache` entry is **preserved**. The job only writes to `pricing_cache` when it has valid new data. On failure, the `status` field of the existing entry could be updated to `"stale"` if the `fetched_at` exceeds the staleness threshold, but the price data itself is never zeroed out.

### 10.3 Partial Success Metrics

The pricing job returns:
```python
{
    "status": "completed",
    "total": 42,
    "success": 38,
    "errors": 4,
    "error_symbols": ["FOO", "BAR", "BAZ", "QUX"],
    "fx_rates_used": {"USD": "0.917000000", "GBP": "0.845230000", "CHF": "0.940000000"},
    "run_id": "20260907T140000Z"
}
```

### 10.4 Logging

Standard `logger.info()` for each symbol success, `logger.warning()` for skips/errors. Run summary printed to stdout (same pattern as `portfolio_enrichment`).

---

## 11. Testing Requirements

### 11.1 Backend Tests (Basher)

**New test file:** `backend/tests/test_symbol_pricing.py`

| Test | Description |
|------|-------------|
| `test_gbp_pence_conversion` | GBp raw_price 4815 → price_major 48.15, price_currency GBP, price_eur = 48.15 × GBP rate |
| `test_gbx_pence_conversion` | GBX same as GBp (both divide by 100 to GBP) |
| `test_usd_no_conversion` | USD raw_price passes through, price_major = raw_price |
| `test_eur_identity` | EUR price_eur = price_major, fx_rate = 1.0 |
| `test_chf_conversion` | CHF → EUR via fx_rate |
| `test_missing_currency_field` | No `info["currency"]` → status "error", no price_eur |
| `test_fx_unavailable_aborts_run` | FxUnavailableError → entire run aborted, existing cache untouched |
| `test_fx_rate_not_found_single_currency` | FxRateNotFoundError for CHF → CHF symbols get null price_eur, others succeed |
| `test_no_yahoo_symbol_mapping` | Unknown MIC → status "error" |
| `test_current_value_computation` | shares × price_eur = current_value_eur |
| `test_total_current_value_aggregation` | Sum across portfolio rows |
| `test_stale_cache_detection` | fetched_at > 2h ago → status "stale" on read |
| `test_overview_uses_pricing_cache` | _compute_symbols_overview prefers pricing_cache over enrichment |
| `test_overview_fallback_no_cache` | No pricing_cache → uses enrichment.metrics.current_price |
| `test_partial_success` | Some symbols fail, others succeed; good data written, bad skipped |
| `test_scheduler_registration` | symbol_pricing task registered with correct cron/config_key |

### 11.2 Frontend Tests (Basher)

**File:** `frontend/tests/` (extend existing test files or new test file)

| Test | Description |
|------|-------------|
| `test_price_column_currency_label` | GBp shows "4,815 GBp", USD shows "$48.15", EUR shows "€48.15" |
| `test_price_eur_column_renders` | price_eur renders in € format |
| `test_current_value_column_renders` | current_value_eur renders in € format |
| `test_kpi_current_value_card` | KPI card appears when total_current_value_eur is present |
| `test_kpi_current_value_hidden_when_null` | KPI card hidden when total_current_value_eur is null |
| `test_stale_price_visual` | pricing_status "stale" → reduced opacity |
| `test_legacy_fallback_no_currency` | No price_display_currency → "$" prefix (legacy) |

---

## 12. Migration / Deployment Ordering

### 12.1 Phase Order

**Phase 1 (Backend — deploy first):**
1. Add `pricing_cache` field support to CosmosDB service  
2. Deploy `symbol_pricing.py` module and scheduler registration  
3. Deploy updated `_compute_symbols_overview` with pricing_cache fields  
4. All new API fields are **additive** — existing frontend continues to work  

**Phase 2 (Frontend — deploy after backend):**
1. Add new TypeScript types  
2. Update SymbolsTable with currency-aware price column, Price EUR column, Value EUR column  
3. Add Current Value KPI card  

### 12.2 Cold Cache Grace

On first deployment, `pricing_cache` will be `null` on all symbol_config documents. The overview endpoint falls back to `enrichment.metrics.current_price` (existing behavior). New columns (`Price EUR`, `Value EUR`) show "—" until the first pricing run completes. The KPI card is hidden.

After the first scheduled run (or a manual "Run Now" trigger from the Settings page), all symbols get populated. No backfill migration needed.

### 12.3 No Breaking Change

At no point does the frontend break:
- Before backend deploy: everything works as today  
- After backend deploy, before frontend deploy: new fields ignored by old frontend  
- After frontend deploy, before first pricing run: new columns show "—", old price column works via enrichment fallback  
- After first pricing run: full functionality  

---

## 13. Implementation Phasing & Ownership

### Phase 1 — Backend Pricing Module (Livingston)

**Owner:** Livingston (Persistence & Integration)  
**Reviewer:** Danny (architecture), Linus (currency/FX semantics)  

**Deliverables:**
1. `backend/src/symbol_pricing.py` — core pricing logic  
2. `backend/src/cosmos_db.py` — `update_symbol_pricing_cache()` method  
3. `backend/src/main.py` — scheduler registration, job method  
4. `backend/config.yaml` — `symbol_pricing` config block  
5. `backend/web/app.py` — reschedule route for symbol_pricing  

### Phase 2 — Currency/FX Semantics Review (Linus)

**Owner:** Linus (Quant Dev)  
**Reviewer:** Danny  

**Deliverables:**
1. Review and validate GBp/GBX/ILA/ZAc minor-unit handling in `symbol_pricing.py`  
2. Review FX rate coherence (all symbols in a run share same rates)  
3. Validate ECB rate fallback logic for weekends/holidays  
4. Confirm pence conversion happens exactly once  
5. Edge case: what if Yahoo returns `"GBP"` instead of `"GBp"` for some XLON securities? (Answer: treat as major-unit, no division — the provider is trusted)  

### Phase 3 — Overview API Integration (Livingston)

**Owner:** Livingston  
**Reviewer:** Danny  

**Deliverables:**
1. `_compute_symbols_overview()` changes — pricing_cache fields, current_value_eur computation, total_current_value_eur  
2. Backward-compatible `price` field behavior  

### Phase 4 — Frontend (Rusty)

**Owner:** Rusty (Frontend)  
**Reviewer:** Danny  

**Deliverables:**
1. `frontend/src/types/symbols.ts` — new fields  
2. `frontend/src/components/SymbolsTable.tsx` — currency-aware Price column, new Price EUR and Value EUR columns  
3. `frontend/src/app/symbols/page.tsx` — Current Value KPI card  
4. Currency formatting helpers  

### Phase 5 — Tests (Basher)

**Owner:** Basher (Testing)  
**Reviewer:** Danny  

**Deliverables:**
1. `backend/tests/test_symbol_pricing.py` — all 16 backend tests from §11.1  
2. Frontend tests from §11.2  
3. Integration test: end-to-end pricing run → overview API → correct values  

### Reviewer Gates

| Gate | Approver | Blocks |
|------|----------|--------|
| Architecture contract approval | User (dsanchor) | All phases |
| Phase 1 code review | Danny + Linus | Phase 3 |
| Phase 2 currency semantics sign-off | Danny | Phase 3 |
| Phase 3 API review | Danny | Phase 4 |
| Phase 4 frontend review | Danny | Phase 5 |
| Phase 5 test coverage review | Danny | Release |
| Final regression gate | Danny | Deploy |

---

## 14. Files Changed Summary

| File | Change Type | Owner |
|------|------------|-------|
| `backend/src/symbol_pricing.py` | **NEW** | Livingston |
| `backend/src/cosmos_db.py` | ADD method `update_symbol_pricing_cache()` | Livingston |
| `backend/src/main.py` | ADD job method, scheduler registration | Livingston |
| `backend/config.yaml` | ADD `symbol_pricing` config block | Livingston |
| `backend/web/app.py` | MODIFY `_compute_symbols_overview()`, ADD reschedule route | Livingston |
| `backend/src/portfolio/fx_service.py` | NO CHANGE (reused as-is) | — |
| `backend/src/portfolio/provider_symbols.py` | NO CHANGE (reused as-is) | — |
| `frontend/src/types/symbols.ts` | ADD new fields to `SymbolRow`, `PortfolioSummary` | Rusty |
| `frontend/src/components/SymbolsTable.tsx` | MODIFY Price column, ADD Price EUR + Value EUR columns | Rusty |
| `frontend/src/app/symbols/page.tsx` | ADD Current Value KPI card | Rusty |
| `backend/tests/test_symbol_pricing.py` | **NEW** | Basher |
| `frontend/tests/...` | ADD pricing/currency tests | Basher |

---

## 15. Open Questions / Blockers

**None blocking.** All architecture questions from the prompt have been resolved in this contract:

1. ✅ Scheduler: `0 9-23 * * 1-5`, inclusive, UTC, no startup catch-up, standard overlap guard  
2. ✅ Cache: `pricing_cache` field on existing `symbol_config` docs in `symbols` container  
3. ✅ Currency: trust provider `info["currency"]`, validate against MIC, GBp→GBP conversion exactly once  
4. ✅ FX: reuse `fx_service.get_fx_rate()`, rates embedded per-symbol, coherent within run  
5. ✅ Universe: all `symbol_config` docs (`cosmos.list_symbols()`)  
6. ✅ API: additive fields, backward-compatible, Decimal precision defined  
7. ✅ UI: currency-aware Price, Price EUR, Value EUR columns; Current Value KPI card  
8. ✅ Safety: no wrong-currency fallback, no overwriting good cache, partial success logged  
9. ✅ Migration: backend first, frontend second, cold cache graceful, no breaking changes  

**Potential future enhancement (not in scope):**
- Real-time price updates via WebSocket (currently batch/hourly)  
- Intraday price cache with sub-minute freshness  
- Additional FX sources beyond ECB  
