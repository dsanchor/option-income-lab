# Reuben Agent History

## Portfolio Unified Implementation — Second Review Fixes (2026-09-06 00:10–00:25 UTC+02:00)

**Role:** Escalated independent specialist (second-round fix owner)  
**Status:** ✅ COMPLETE

**Context:**
Danny's second review identified 2 new findings (F6–F7) after Linus's first-round fixes. All prior authors (Livingston, Rusty, Linus) locked out per protocol. Reuben escalated as independent specialist with fresh perspective.

**Fixes Applied:**

### F6 — DELETE Movement Account ID Partition Key

**Bug:** Backend fallback parses account_id from movement ID, but splitting `_unassigned` produces empty string. Frontend omits `account_id` query parameter.

**Files Changed:**
- `backend/web/portfolio_routes.py` — Remove ID-parsing fallback; default to `"_unassigned"`
- `frontend/src/lib/portfolio-api.ts` — Add `accountId?: string` parameter; build query string
- `frontend/src/components/PortfolioMovementsTable.tsx` — Pass `m.account_id` through `onDelete` callback

**Tests Added:** 3 tests in `test_portfolio_endpoints.py`
- `test_delete_unassigned_movement_no_account_id` — PASS
- `test_delete_movement_explicit_account_id` — PASS
- `test_delete_movement_wrong_account_returns_404` — PASS

### F7 — `avg_cost_basis_eur` Wrong Denominator

**Bug:** Divides `total_cost` by `cost_basis_buys` (transaction count) instead of `paid_buy_shares` (share count). Results in "cost per transaction" instead of "cost per share."

**Files Changed:**
- `backend/src/portfolio/holdings_service.py` — Add `paid_buy_shares` accumulator; divide by shares

**Tests Added:** 6 tests in `test_portfolio_holdings.py`
- `test_avg_cost_basis_single_buy` — PASS
- `test_avg_cost_basis_multi_buy` — PASS
- `test_avg_cost_basis_excludes_zero_cost` — PASS
- `test_avg_cost_basis_no_paid_buys_is_null` — PASS
- `test_avg_cost_basis_independent_of_sells` — PASS
- `test_avg_cost_basis_dividends_only_is_null` — PASS

**Test Results:**
```
Backend: 160 tests (151 + 9) — ALL PASS
Frontend: npx tsc --noEmit — 0 errors
```

**Archived to:** `.squad/decisions/archive/inbox-2026-09-06/` (audit trail preserved)

**Final Status:** ✅ All Round 2 findings resolved. Feature ready for production.

### 2026-09-07T14:54:00+02:00 — Test Revisions Under Lockout (DGI-4 False Positive & PEP-12a Dead Closure)

**Batch:** Symbol onboarding & PEP repair (test-file-only revisions, independent from Basher)

**Lockout Assignment 1: DGI-4 False Positive Fix**
- **Original defect:** `frontend/tests/tradingViewSourceContract.test.mjs` whole-file substring scan matched legitimate `if (ex === "NYSE") return "XNYS";` guard
- **Scope:** Test-file-only; no product code changes
- **Revision strategy:** Real execution + structural assertions
  1. Extract `toExchangeMic()` function body via balanced-brace parsing; execute directly with `new Function`
  2. Call with guard-case inputs (`"OTC"`, `"PINK"`, `"FOREIGN"`, etc.); assert `null` for all
  3. Structurally assert function's final statement is unconditional `return null;`
  4. Textually assert every `"XNYS"`/`"XNAS"` return is guarded by `if (...)` on same line
  5. Independently verify source order: `toExchangeMic()` → `if (mic === null) return;` → `addSymbol()` → `fetch()` PUT
- **Result:** 23/23 TradingView contract tests pass; no product code regressions ✅

**Lockout Assignment 2: PEP-12a Backup-Ordering Test Rewrite**
- **Original defect:** Dead closure `_backup_then_apply()` never invoked; `backup_completed` flag logic unreachable
- **Scope:** `backend/tests/test_repair_pep_security_id.py` only; no product code changes
- **Revision strategy:** Real instrumentation
  1. Capture real `write_backup` call; monkeypatch to read back checksum immediately after write
  2. Instrument container mutation methods (`create_item`, `replace_item`, `delete_item`)
  3. Build single event trace across backup + mutations
  4. Assert `backup_indices[0] < first_mutation_idx` (real ordering proof, not dead flag)
  5. Fixture deliberately shaped to exercise all three mutation kinds across both containers (symbols + portfolio)
- **Result:** 42/42 PEP repair tests pass; all 3 mutation kinds + both containers verified ✅

**Secondary: Currency Test Revision (Reuben, coordinated with Linus)**
- Rewrite `TestCurrencyEvidence` class (5 tests): Ledger `gross.currency` never determines `listing_currency`, in either direction
- Add `TestProviderVerifiedListingCurrency` class (8 tests): Provider triple-check (currency/financialCurrency/exchange + MIC) mandatory before any currency change
- Add capability gate (`_CURRENCY_FLAG_AVAILABLE`): Runtime signature introspection confirms `apply_repair` accepts `listing_currency` kwarg; tests execute for real (not skipped)
- Fake provider injection: Real `_verify_listing_currency_with_provider()` logic exercised; only data source faked
- **Result:** 13/13 currency tests pass; 51/51 total PEP repair tests pass ✅

**Cross-Batch Impact:**
- All revisions are test-file-only; no product code modified by Reuben in this cycle
- Lockout protocol respected; Basher (original author) excluded from all revisions
- Independent verification: Reuben's real-execution assertions prove product code is sound

**Verification:** All product code (Linus, Livingston) remains unchanged in revision cycles; test defects were scaffolding issues (false patterns, dead code), not implementation bugs.

## Paper Position Roll Fix (2026-09-11 13:41–13:55 UTC+02:00)

- Fixed `backend/src/cosmos_db.py:613-624` in `roll_position()` so rolled positions inherit `old_pos["is_paper"]` only when it is explicitly `True`. This preserves paper status for paper rolls and avoids introducing `is_paper` on legacy real positions.
- Checked other position mutation helpers in the same file for the same pattern. `add_position()` already gates `is_paper` correctly; no other helper creates a derived replacement position from an existing one.
- Added regression tests in `backend/tests/test_cosmos_roll.py:33-55`:
  - `test_roll_position_preserves_is_paper_for_paper_positions`
  - `test_roll_position_does_not_introduce_is_paper_for_real_positions`
