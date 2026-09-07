# Final Reviewer Gate — Pricing Cache + Symbols View Selector + Screener Dropdown (Revised)

**Reviewer:** Danny (Lead)
**Verdict: REJECTED** (narrow — test-coverage gap in `backend/tests/test_symbol_pricing.py` only)
**All product code (backend + frontend) is APPROVED.**

## Verified against contracts — APPROVED

### 1. Scheduler (`config.yaml`, `main.py`, `web/app.py`)
`symbol_pricing` registered via the exact `TaskRegistry.register()` pattern used by all other jobs; `reschedule_symbol_pricing()` mirrors `reschedule_portfolio_enrichment()`; settings-save route (`web/app.py` ~6058-6084) mirrors the `pe_*`/`sp_*` pattern exactly (croniter validation, Cosmos+file persistence, `update_task_enabled`). Cron `"0 9-23 * * 1-5"` matches contract. UTC semantics consistent with all 11 other jobs (`_now_local()` used only for print/log display; actual cron evaluation is the pre-existing `TaskRegistry`/`croniter` engine, unchanged).

### 2. FX outage / cache preservation / storage
`run_symbol_pricing()` correctly implements two-pass no-write-on-abort: Pass 1 (resolve + fetch, no Cosmos writes) → FX pre-fetch → **`FxUnavailableError` returns immediately, before Pass 2's write loop even starts** — existing cache entries are provably untouched (verified in source, no write call reachable on that path). Per-symbol failure paths (no Yahoo mapping, no quote currency, yfinance fetch failure) all `continue`/skip before any `update_symbol_pricing_cache()` call — prior cache preserved. `FxRateNotFoundError` is scoped per-currency (non-fatal), consistent with contract §5.3. `update_symbol_pricing_cache()` (`cosmos_db.py`) uses the same read-modify-write pattern as the adjacent pre-existing method (no ETag) — consistent with established convention, not a regression.

### 3. Price precedence / zero handling
All precedence checks use explicit `is not None` (`pc.get("price_major") is not None`, `row.get("price_eur") is not None`, etc.) — **no `or`-truthiness bugs**; a genuine `0.0` price or `price_eur` would be correctly honored, not silently dropped.

### 4. GBp/GBX/GBP consistency
Backend divides by 100 **exactly once** (`_apply_minor_unit`, case-sensitive on `"GBp"`/`"GBX"` only — plain `"GBP"` is correctly never divided). Frontend `formatPrice()` only **reconstructs** the pence display (`price_major × 100`) for the Price column label; `price_eur`/`current_value_eur` are consumed as-is from the backend with no independent re-conversion — no double-conversion path exists.

### 5. Staleness / Decimal / aggregate scope
2-hour staleness computed at read-time in `_compute_symbols_overview` (not baked into the stored doc), matching contract §3.4. `current_value_eur`/`total_current_value_eur` use `Decimal(...).quantize(Decimal("0.01"))`. Aggregate sum correctly iterates only rows with a non-null `current_value_eur` (i.e., portfolio rows with shares>0 and a resolved `price_eur`) — matches "total portfolio market value" semantics, not a raw row-count artifact.

### 6. Cold-cache fallback currency labeling
`price_display_currency: null` → frontend's legacy `$` fallback is contract-specified behavior (§9.3/9.4), not a new mislabeling regression — pre-existing legacy behavior is preserved verbatim, not worsened.

### 7. Screener dropdown — authoritative booleans (previously rejected, now fixed)
`_compute_symbols_overview` computes `screener_universe` **once** (outside the per-row loop, via the canonical `compute_options_screener_universe()` — the same function used by both the manual and scheduled screener endpoints, confirmed via grep at lines 4196/4398), then sets `us_options_eligible`/`screener_eligible` per row via `_is_us_options_eligible()`/set-membership — no N+1, no reimplementation. Frontend `isScreenerEligible` now reads `r.screener_eligible === true` directly — strict, fail-closed, no more `row_source`/`is_auto_enrolled` heuristic. Backend test `OSD-SE-9` explicitly regression-tests the exact bug previously found (auto-enrolled + active toggle + zero shares → correctly eligible now). 25 backend dropdown tests + 27/19 frontend tests pass.

### 8. View selector
Toolbar order confirmed: search → selector (`role="radiogroup"`, `aria-checked`) → suitability filters — selector precedes All/Ideal filters as required. `visibleColumns` correctly derived from `COLUMNS.filter(modes)`; Portfolio = all columns except In Calls/Puts $ (13 cols + actions = 14); Options = 7 common + In Calls + Puts $ (9 + actions = 10) — confirmed via `symbolsViewSelector.test.mjs` (CS-2/CS-3/CS-4, disjoint sets). `changeViewMode()` resets sort/dir when the active sort key becomes hidden. `filtered` (row-set) does not reference `viewMode` — confirmed no row filtering by view mode, matching "no row/filter/API changes." 135/135 frontend tests pass; `tsc --noEmit` clean.

### 9. Warning/Tracked removal, English KPI labels
Diff confirms `{totalCount} tracked` and the `⚠ incomplete cost basis` warning paragraph are removed; Spanish KPI labels (`Inversión actual`, `Resultado realizado`, `Dividendos netos`) replaced with English (`Current Investment`, `Realized Result`, `Net Dividends`, `Current Value`); the pre-existing English `Calls exposure`/`Puts committed` inline summary is untouched, as required.

## REJECTED — test-coverage gap: `backend/tests/test_symbol_pricing.py`

Two contract "Critical Rules" have **zero executing test coverage**, concealed by unconditional skips:

- **SP-4 (EUR identity, contract §4.3)** — `test_sp4_eur_identity_no_ecb_call` is gated solely on a `build_pricing_cache_entry` helper that doesn't exist in `symbol_pricing.py` (the logic is correctly inlined in `run_symbol_pricing`, not factored out) — the test has **no fallback path** exercising `run_symbol_pricing()` end-to-end, unlike its sibling tests. Result: `fx_rate == "1.000000000"` and `fx_pair == "EUR/EUR"` for a plain-EUR symbol are asserted **nowhere else** in the suite and never execute.
- **SP-5 (CHF conversion success, contract §4.2/§5)** — same gap: only a `build_pricing_cache_entry`-gated test exists, skipped. CHF appears elsewhere only in the FX-rate-**not-found** scenario (SP-8) and the provider-override-precedence test (SP-10, which doesn't assert `price_eur`) — a genuine CHF-rate-found success path computing `price_eur` is never executed.

By contrast, `TestGBpGBXConversion`/`TestCacheWriteShape`/`TestDoublePenceConversionGuard` (SP-1/2/11/21/25) correctly added an `else:` fallback exercising the real `run_symbol_pricing()` path when `build_pricing_cache_entry` is absent — proving the pattern was known and simply not applied consistently to SP-4/SP-5. This is not a hypothetical concern: I independently verified the production EUR/CHF logic in `symbol_pricing.py` is currently correct by direct source inspection, but the suite itself provides no regression protection for these two specific, contract-mandated invariants going forward.

This is a test-artifact-only rejection — no product code is implicated or requires changes.

## Required fix
Add a `run_symbol_pricing()`-based fallback (mirroring the SP-1/SP-25 pattern) to `test_sp4_eur_identity_no_ecb_call` and `test_sp5_chf_conversion`, asserting the written `pricing_cache` for a real EUR symbol has `fx_rate == "1.000000000"` / `fx_pair == "EUR/EUR"` / `price_eur == price_major`, and for a real CHF symbol with a mocked resolved rate has `price_eur == round(price_major * rate, 2)`. Remove the dead unconditional skip once real coverage exists (the `build_pricing_cache_entry`-only path may remain as a bonus check, not the sole one).

## Ownership
- `backend/tests/test_symbol_pricing.py` — original author **Basher** (Phase 5, contract §13). Not previously rejected on this file — **no lockout applies**; assign the SP-4/SP-5 rewrite to **Basher**.

## Validation run
- Backend: `test_options_screener_dropdown.py` + `test_symbol_pricing.py` — 59 passed, 3 skipped (see above), 0 failed.
- Backend full suite: 3816 passed, 20 failed, 8 skipped — all 20 failures isolated to `test_yfinance_data_provider.py`; **independently confirmed pre-existing** by running the identical file against the unmodified base via `git stash` (3 failures there too, different subset each run — order-dependent event-loop test pollution, unrelated to this diff's scope; no file in scope touches `YFinanceDataProvider` or asyncio lifecycle).
- Frontend: `symbolsViewSelector.test.mjs` + `symbolPricingContract.test.mjs` + `optionsScreenerDropdown.test.mjs` + `optionsScreenerUniverse.test.mjs` — 135 passed, 0 failed, 0 skipped.
- `npx tsc --noEmit` — clean, no errors.

## Not blocking (informational)
- `symbol_pricing.py` has a small dead helper (`_write_cache`, unused — logic is inlined in the main loop instead). Harmless, not required to fix.
