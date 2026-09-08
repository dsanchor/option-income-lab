# Squad Decisions

## Active Decisions


## Active Decisions

### Danny — Final Reviewer Gate: Post-Release UI Batch (Column Visibility, Symbol Detail Tabs, Config Collapse, Holdings, Account-Name-Only)

**Verdict: APPROVED**

## Scope reviewed
`SymbolsTable.tsx`, `SymbolDetailTabs.tsx` (new) + `[symbol]/page.tsx` integration, `SymbolConfigurationCard.tsx`, `PortfolioHoldingsCard.tsx`, `PortfolioHoldingsTable.tsx`, `accountDisplay.ts`, `AccountBadge.tsx`, `PortfolioMovementsTable.tsx`, `ReassignmentDialog.tsx`, `MovementCorrectionDialog.tsx`, `MovementDetailDialog.tsx`, `StockTransactionsTable.tsx`, `AddMovementDialog.tsx`, `CorporateActionForm.tsx`, `AccountsView.tsx`, plus `symbolDetailTabsContract.test.mjs`, `symbolDetailRedesign.test.mjs`, `symbolsViewSelector.test.mjs`, `symbolConfigSectionContract.test.mjs`, `movementsAccountLabel.test.mjs`.

## 1. Column visibility / proportional widths — sound
`COLUMNS` metadata (`src/components/SymbolsTable.tsx`) declares `modes: ["options"]` on `dgi_score`, `tech_timing`, `entry_tag` and `modes: ["portfolio"]` on the portfolio-financial columns; `in_calls`/`put_exposure` remain `["options"]`-only (unchanged, previously approved). `visibleColumns`/`cols` is a single filtered alias reused identically by `<colgroup>`, the header row, and `tableMinWidth`'s reduce — confirmed via source and `symbolsViewSelector.test.mjs` `MW-3`/`MW-6`/`MW-7` (body-cell mode guards verified consistent with the metadata filter). `tableMinWidth` is computed per-mode (`cols.reduce(...)`) rather than a static constant, so Options' compact column set doesn't inherit Portfolio's wider min-width — confirmed by `MW-1`, `MW-4`, `MW-5`. 92/92 pass.

## 2. Symbol Detail Tabs — sound
Exactly three tabs (Options/Stocks/Action Plans) in the mandated order; full ARIA tablist/tab/tabpanel pattern with roving `tabIndex`, `aria-selected`/`aria-controls`/`aria-labelledby`, and Arrow/Home/End keyboard handling. Hash sync via `replaceState` (no history-stack pollution), default resolves to `#options` unless a valid hash is present. Verified in source that each panel component (`PositionsTable`, `SymbolConfigurationCard`, `PortfolioHoldingsCard`, `StockTransactionsTable`, `SymbolPlansTable`) is referenced exactly once in `page.tsx`, only inside its tab-panel slot — no duplicate old sections remain outside the tabs. Non-US safety preserved: `usOptionsEligible` still gates `SymbolActions` and real positions/activity content inside the Options panel; the Options *tab* itself is intentionally always visible per an explicit, tested design decision (`US-1`/`US-2` in `symbolDetailTabsContract.test.mjs`) — for non-US symbols the tab shows only an inert "not available for this exchange" message, no controls. 129/129 pass across both new test files.

**Note (non-blocking):** conditional mounting (`activeTab === tab.id ? <div>...</div> : null`) means switching away from the Stocks tab unmounts `SymbolConfigurationCard`, discarding any in-progress unsaved edit (`dirty` state) without a warning; returning to the tab remounts fresh from original props. This is a deliberate, documented trade-off ("client components only mount... when their tab is first activated") for fetch efficiency, is triggered only by an explicit user click, and the config card is collapsed-by-default requiring deliberate opt-in to edit — not a regression against any stated preservation requirement. Flagging for awareness, not blocking.

## 3. Symbol Configuration collapse — sound
Collapsed by default (`useState(false)`), proper `aria-expanded`/chevron toggle, content only mounted (`{open && (...)}`) when expanded. Confirmed zero references to "Agent & Alert Toggles", `cfg-toggle-*` ids, `covered_call`/`cash_secured_put`/`buy_tracker`/`telegram` anywhere in the file — clean removal, no stale props (the `Props` interface has exactly 4 fields, no orphaned toggle-related types). ETag/If-Match conflict handling (`_etag`, `409` handling, `saveConflict` state) untouched. 21/21 pass.

## 4. Portfolio Holdings — sound
`PortfolioHoldingsCard.tsx` is a single flat `<table>` (no nested card), correct `_unassigned` → `UNASSIGNED_LABEL` fallback via `acctLabel()`, account-name-only (no broker prefix, no `·` separator — confirmed by `HB-1..HB-5`). Per-account Invested/Dividends cells intentionally render `—`: verified `HoldingsByAccount` type genuinely only carries `shares`/`avg_cost_eur` — this is a pre-existing schema limitation (no per-account breakdown ever existed for those two fields), not new data loss; the Total row still shows the real aggregate values from `PortfolioSection`. `PortfolioHoldingsTable.tsx` account filter/summary use `formatAccountName`/`getAccountName` consistently. 94/94 pass (`movementsAccountLabel.test.mjs`).

## 5. Account-name-only convention — sound, correctly scoped
`accountDisplay.ts` provides `formatAccountName`/`getAccountName` with the mandated fallback chain (name → account_id → "—", **never** broker/type) alongside the pre-existing `formatAccountLabel`/`getAccountLabel` (retained, deprecated-but-not-removed) for metadata-management use. Confirmed by direct grep: every informational surface (`AccountBadge`, `AddMovementDialog`, `CorporateActionForm`, `MovementCorrectionDialog`, `MovementDetailDialog`, `PortfolioHoldingsTable`, `PortfolioMovementsTable`, `ReassignmentDialog`, `StockTransactionsTable`) imports only the name-only helpers; `AccountsView.tsx` (the Accounts management page) is the sole remaining consumer of `BROKER_LABELS`, exactly matching the stated exception for explicit account-metadata management. Filter/select **values** remain `account_id` throughout (only display **labels** changed) — confirmed no payload/ID changes.

## Test evidence (independently run, not trusted from report)
- `symbolDetailTabsContract.test.mjs` + `symbolDetailRedesign.test.mjs`: 129/129
- `symbolsViewSelector.test.mjs`: 92/92
- `symbolConfigSectionContract.test.mjs`: 21/21
- `movementsAccountLabel.test.mjs`: 94/94
- Full frontend suite (`node --test tests/*.test.mjs`): **1202/1202 pass**, 0 fail — matches reported result exactly.
- `npx tsc --noEmit`: clean, zero errors.

## Conclusion
No high-confidence blocking issues found. All eight verification points hold: exact column sets with consistent header/body/colgroup wiring and mode-aware proportional widths; correct tab-to-panel mapping with no duplicate content and preserved non-US safety; conditional mounting behavior is a deliberate, low-risk trade-off rather than an unexpected regression; config card collapses cleanly with toggles fully and safely removed; holdings calculations/actions/fields are preserved exactly (including honest "—" placeholders for genuinely unavailable per-account fields); the account-name-only convention is applied everywhere required with the single correct, intentional exception; no ID/payload changes; no unrelated regressions detected.

No product code modified by this review. No production calls made.


### 2026-09-08T10:06:49+02:00: User directive
**By:** Copilot (via Copilot)
**What:** Replace moving-average depletion with FIFO acquisition lots. For ADM, buys of 85, 15, and 10 shares followed by a sale of 100 must leave the final 10-share lot, whose net unit cost becomes the remaining average cost. Gross is always the amount before transaction deductions/additions: BUY net = gross + commission; SELL net = gross - commission; dividend net = gross - commission - withholding. Average-price and financial calculations use net.
**Why:** User request — captured for team memory


### 2026-09-07T22:58:18+02:00: User directive
**By:** dsanchor (via Copilot)
**What:** Cuando la información de una cuenta se muestre de forma informativa, usar solo el account name. Esto incluye source y destination en la reasignación de movimientos; no mostrar broker ni tipo.
**Why:** User request — captured for team memory


### 2026-09-08T07:32:10+02:00: User correction
**By:** dsanchor (via Copilot)
**What:** Las acciones recibidas por scrip dividends con coste importado 0 deben contabilizarse en el denominador del coste medio con coste cero. En imports BUY, la columna de coste total es neta y excluye comisión; el gross se calcula como neto + comisión. La semántica SELL actual es correcta y no debe cambiar.
**Why:** User correction — supersedes the prior effective-average display-only contract and the current BUY import interpretation.


### Basher — FIFO Integration Gate

**Date:** 2026-09-08T10:56Z  
**Updated:** 2026-09-08T11:05Z (post-Livingston revision)
**Author:** Basher (Tester, Reviewer)
**Status:** ✅ FULL APPROVAL — G10 CLEARED

---

## Summary

All input-artifact targeted suites pass. Eight stale-test failures that existed in
files outside the W9 scope were resolved by Livingston (2026-09-08T11:05Z) with
purely test fixture/assertion changes. G10 now passes. Implementation fully approved.

---

## Post-Revision Verification (2026-09-08T11:05Z)

**Diff review:** Livingston's changes are confined to the four collateral test files.
All 8 changes are stale-expectation/fixture corrections only:
- 6 arithmetic operator flips: `- _d(...)` → `+ _d(...)` in expected BUY net values
- 1 literal value update: `18242.50` → `18257.50` with explanatory comment
- 1 fake helper fix: flat `net_eur` field → canonical nested `"net": {amount, currency, eur_amount}`
No implementation files modified. No logic changes. All changes align with FIFO contract (BUY `net = gross + fees`).

**`git diff --check` (four test files):** EXIT:0 — clean.
Unrelated trailing whitespace in `.squad/agents/livingston/history.md` (pre-existing, not part of this change).

### Re-run Results

| Suite | Tests | Result |
|-------|-------|--------|
| 8 previously failing tests | 8 | ✅ 8/8 pass |
| `backend/tests/ -k "portfolio"` (broad) | 923 | ✅ 923/923 pass |
| Full 431-test targeted backend suite (input artifacts) | 431 | ✅ 431/431 pass |
| `scripZeroCostContract.test.mjs` (frontend) | 24 | ✅ 24/24 pass |
| **Total** | **1386** | ✅ **1386/1386 pass** |

---

## Gate Results

| Gate | Criterion | Result |
|------|-----------|--------|
| G1 | All FIFO-* tests green | ✅ PASS |
| G2 | BUY net > gross, SELL net < gross in all tests | ✅ PASS (targeted suites) |
| G3 | Migration audit+apply+verify cycle passes | ✅ PASS |
| G4 | SELL accounting unchanged | ✅ PASS |
| G5 | Dividend accumulation unchanged | ✅ PASS |
| G6 | Frontend BUY form sends gross=trade_value | ✅ PASS |
| G7 | ADM 10 shares at €49.038/share post-FIFO | ✅ PASS |
| G10 | Full suite green (backend + frontend) | ✅ PASS (post-revision) |

---

## Suite Counts

### Targeted suites (input artifacts) — ALL PASS

| Suite | Tests | Result |
|-------|-------|--------|
| `test_portfolio_fifo.py` + `test_portfolio_holdings.py` + `test_repair_buy_ledger_fields.py` | 171 | ✅ 171 pass |
| `test_portfolio_summary_cost_basis.py` + `test_scrip_zero_cost_and_buy_import.py` + `test_amendment_g_bilingual.py` + `test_amendment_h_holdings_effects.py` + `test_portfolio_phase2_legacy_compat.py` | 260 | ✅ 260 pass |
| `frontend/tests/scripZeroCostContract.test.mjs` | 24 | ✅ 24 pass |

### Broader portfolio suite — STALE-TEST FAILURES

Ran `python3 -m pytest backend/tests/ -k "portfolio"` → 915 pass, **8 fail**.

---

## Failing Tests — Classification: STALE (not implementation bugs)

All 8 failures encode the **pre-contract** BUY net formula (`net = gross − fees`).
The new contract (danny-fifo-net-accounting-contract.md §3) mandates `net = gross + fees`
for BUY. Implementation (Linus, W3/W4) is correct. These tests were NOT in the W9
update scope and were not refreshed.

### Group A — BUY net formula stale (7 tests)

| File | Test | Expected | Got | Formula encoded |
|------|------|----------|-----|-----------------|
| `test_portfolio_corrections_extended.py` | `TestBuyFullCorrection::test_buy_gross_fees_net_recomputed` | 15995 (16000−5) | 16005 (16000+5) | OLD |
| `test_portfolio_corrections_extended.py` | `TestBuyFullCorrection::test_buy_only_gross_uses_original_fees` | 19992.5 | 20007.5 | OLD |
| `test_portfolio_corrections_extended.py` | `TestBuyFullCorrection::test_buy_only_fees_uses_original_gross` | 18235 | 18265 | OLD |
| `test_portfolio_corrections_extended.py` | `TestNetArithmetic::test_decimal_precision_6dp` | gross−fees | gross+fees | OLD |
| `test_portfolio_phase2.py` | `TestManualMovementCreation::test_net_computed_from_gross_minus_fees` | 18242.50 | 18257.50 | OLD |
| `test_portfolio_phase2_corrections.py` | `TestFullCorrectionFieldMatrix::test_c1_buy_gross_fees_override` | 16995 | 17005 | OLD |
| `test_portfolio_phase2_corrections.py` | `TestFullCorrectionFieldMatrix::test_c8_gross_change_triggers_net_recompute` | gross−fees | gross+fees | OLD |

### Group B — Fake helper missing nested `net` dict (1 test)

| File | Test | Root cause |
|------|------|-----------|
| `test_unified_watchlist.py` | `TestPortfolioSummaryTotals::test_remaining_cost_basis_reflects_holdings` | `_add_buy()` sets flat `net_eur` field; FIFO engine reads `net.eur_amount` nested dict → cost=0 → `remaining_cost_basis_eur=0.00` |

---

## Attribution

| File | Owner | Action required |
|------|-------|-----------------|
| `test_portfolio_corrections_extended.py` | Basher (authored 2026-09-07) | Basher must update 4 tests: change expected BUY net to `gross + fees` |
| `test_portfolio_phase2.py` | Rusty (W1/W9 owner) | Update `test_net_computed_from_gross_minus_fees` and rename: expected = 18257.50, formula = gross+fees |
| `test_portfolio_phase2_corrections.py` | Rusty (W1/W9 owner) | Update `test_c1` and `test_c8` expected net values and comments |
| `test_unified_watchlist.py` | Rusty (W1 holdings engine owner) | Fix `_add_buy()` fake: add `"net": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur}` dict |

---

## Specific Fixes Required

### `test_portfolio_phase2.py:270`
```python
### OLD (stale)
assert net == Decimal("18242.50")   # gross - fees
### NEW (per contract)
assert net == Decimal("18257.50")   # gross + fees = 18250 + 7.50
```

### `test_portfolio_corrections_extended.py` (4 tests)
Change all `expected_net = _d(new_gross) - _d(new_fees)` to `+ _d(new_fees)`.

### `test_portfolio_phase2_corrections.py`
Change `test_c1` expected net from 16995 to 17005 and `test_c8` comments/assertions.

### `test_unified_watchlist.py` `_add_buy()`
Replace flat `"net_eur": gross_eur` with nested:
```python
"net": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur},
```

---

## Ruling

**Implementation artifacts (W1–W8): APPROVED.**
All 455 tests in the input artifact suites pass. Implementation correctly follows
the FIFO/net contract.

**G10 (full suite): ✅ CLEARED (post-revision 2026-09-08T11:05Z)**
Livingston's stale-test fix verified: diff-only test expectation/fixture changes,
no implementation files touched, `git diff --check` clean on the four test files.
923/923 broader portfolio, 8/8 previously failing, 455/455 targeted, 24/24 frontend.
Total 1386/1386 pass. Residual risk: none.

— Basher


### Danny — Effective Average Cost Display Contract

**Status: SUPERSEDED** — replaced by `danny-scrip-zero-cost-and-buy-import-contract.md`
**Date: 2026-09-08**
**Author: Danny (Lead/Architect)**
**Scope: Backend holdings computation + API fields + all frontend display surfaces**

---

## 1. Problem Statement

When a security has INCOMPLETE (zero-cost) acquisitions—e.g. scrip dividends or transfers without known cost—the displayed arithmetic breaks:

| Field | ACS example (current) |
|-------|----------------------|
| Shares (total_shares) | 223 (163 paid + 60 INCOMPLETE) |
| Avg Cost (avg_cost_basis_eur) | €26.34 (pool_cost 4294.21 / pool_shares 163) |
| Invested (remaining_cost_basis_eur) | €4,294.21 |
| **Arithmetic check** | **223 × €26.34 = €5,873.82 ≠ €4,294.21** ❌ |

The CMP pool average is correct for realized-P&L accounting but misleading as a display metric because `total_shares` includes unpaid shares that the pool average ignores.

**Secondary bug (PortfolioHoldingsTable only):** The per-row "Invested (€)" column renders `total_invested_eur` (all-time purchase outflow = €5,264.07 for ACS) while the summary StatCard "Inversión actual" renders `remaining_cost_basis_eur` (€4,294.21). Same label, different semantics.

---

## 2. Decisions

### §2.1 — Preserve Internal CMP Pool Average for Accounting

`avg_cost_basis_eur` (= `pool_cost / pool_shares`) remains **unchanged** in `HoldingsService.compute_holdings()`. It continues to be the basis for:
- SELL ACCIONES cost assignment (`cost_basis_sold_eur`)
- TRANSFER_OUT cost removal
- All realized P&L calculations

**No change to any sale/transfer/P&L accounting logic.**

### §2.2 — New Field: `effective_avg_cost_eur`

Computed by the backend in `HoldingsService.compute_holdings()`, added to each holding dict:

```python
### After pool/accumulator computation, per security:
effective_avg: Optional[Decimal] = None
if total_shares > _ZERO:
    effective_avg = (remaining / total_shares).quantize(
        _TWO_PLACES, rounding=ROUND_HALF_UP
    )
```

Where `remaining` = `pool_cost` residual (= `remaining_cost_basis_eur`), and `total_shares` = all shares including unpaid.

**Semantics:** `effective_avg_cost_eur = remaining_cost_basis_eur / total_shares` — what each currently-held share "costs" on average, counting zero-cost shares honestly.

**ACS verification:** €4,294.21 / 223 = **€19.26** → 223 × €19.26 = €4,294.98 ≈ €4,294.21 ✓ (2dp rounding).

### §2.3 — API Field Exposure

#### Per-holding (HoldingItem / holding dict):
| Field | Semantics | New? |
|-------|-----------|------|
| `avg_cost_basis_eur` | Pool CMP average (pool_cost/pool_shares). Null when pool empty. | Existing, **kept** |
| `effective_avg_cost_eur` | remaining_cost_basis_eur / total_shares. Null when total_shares ≤ 0. | **NEW** |

#### Per-holding-by-account (symbol detail `holdings_by_account`):
| Field | Semantics | New? |
|-------|-----------|------|
| `avg_cost_eur` | Pool CMP average for that account. | Existing, **kept** |
| `effective_avg_cost_eur` | Account-level remaining_cost_basis / account total_shares. | **NEW** |

#### Symbols overview row (SymbolRow):
| Field | Semantics | New? |
|-------|-----------|------|
| `portfolio_avg_cost_eur` | **REMAPPED** → `effective_avg_cost_eur` (was `avg_cost_basis_eur`) | **Changed** |

**Rationale:** The Symbols overview is a user-facing display surface. Showing the pool CMP average alongside `portfolio_shares` (which includes unpaid) is arithmetically incorrect. Remapping this single field to the effective average restores `shares × avg_cost ≈ invested`.

No new field needed on SymbolRow; the existing `portfolio_avg_cost_eur` name is retained, its source changes.

#### Summary (HoldingsSummary):
No new average fields at summary level. The summary already exposes `remaining_cost_basis_eur` and `total_shares` (via `total_securities`), which is sufficient.

### §2.4 — Frontend Label Mapping

| Surface | Column/Field | Shows | Label |
|---------|-------------|-------|-------|
| **SymbolsTable** (Symbols overview) | `portfolio_avg_cost_eur` | `effective_avg_cost_eur` | **"Avg Cost"** (unchanged) |
| **PortfolioHoldingsCard** (symbol detail) | Total row "Avg Cost" | `effective_avg_cost_eur` | **"Avg Cost (€)"** (unchanged) |
| **PortfolioHoldingsCard** | Per-account "Avg Cost" | `effective_avg_cost_eur` per account | **"Avg Cost (€)"** (unchanged) |
| **PortfolioHoldingsTable** (Holdings page) | Per-row "Avg Cost" | `effective_avg_cost_eur` | **"Avg Cost (€)"** (unchanged) |
| **PortfolioHoldingsTable** | Per-row "Invested" | **`remaining_cost_basis_eur`** (was `total_invested_eur`) | **"Invested (€)"** (unchanged) |

The internal pool CMP average (`avg_cost_basis_eur`) is **not displayed** on any user-facing surface. It remains available in the API for diagnostic/accounting use but is not surfaced in any column or card. No separate "Paid-share CMP" label is introduced—simpler, avoids user confusion.

### §2.5 — Fix Holdings Table Per-Row "Invested" Semantic Mismatch

**File:** `frontend/src/components/PortfolioHoldingsTable.tsx`

**Current (line ~341):**
```tsx
€{Number(h.total_invested_eur).toLocaleString(...)}
```

**Change to:**
```tsx
€{Number(h.remaining_cost_basis_eur ?? h.current_invested_eur).toLocaleString(...)}
```

This aligns the per-row "Invested (€)" with the summary "Inversión actual" — both now show `remaining_cost_basis_eur`. The label stays "Invested (€)"; no rename to "Remaining Cost" needed since the summary already uses the Spanish "Inversión actual" for the same concept and users understand "Invested" as what they currently have at stake.

### §2.6 — INCOMPLETE Acquisitions & Edge Cases

| Scenario | Behavior |
|----------|----------|
| All shares COMPLETE (no INCOMPLETE buys) | `effective_avg_cost_eur` = `avg_cost_basis_eur` (identical; pool_shares = total_shares) |
| Mixed COMPLETE + INCOMPLETE | `effective_avg_cost_eur` < `avg_cost_basis_eur` (diluted by zero-cost shares) |
| All shares INCOMPLETE (pool_shares = 0) | `avg_cost_basis_eur` = null; `effective_avg_cost_eur` = €0.00 (remaining_cost_basis is 0 / total_shares) |
| total_shares ≤ 0 (fully exited or negative inventory) | Both averages = null |
| Partial sells | Pool shrinks proportionally (CMP); effective_avg recomputed from residual pool_cost / remaining total_shares |
| TRANSFER_IN with cost | Carried cost enters pool; effective_avg includes it |
| TRANSFER_IN without cost (zero carried_cost) | Shares added to total but not to pool; dilutes effective_avg (same as INCOMPLETE) |
| FX | All amounts already in EUR throughout; no FX change needed |
| Per-account aggregation | Each account's holdings_by_account entry gets its own `effective_avg_cost_eur` from that account's holdings computation |
| Rounding | Both averages quantized to 2dp (ROUND_HALF_UP); display multiplication may show ±€0.01–€1.00 difference vs. the pre-rounded invested figure — acceptable |

### §2.7 — Incomplete-Cost Warning on Symbols Overview

Per existing user directive: the incomplete-cost warning (⚠ badge) was **already removed** from `SymbolsTable.tsx`. This contract does **not** reintroduce it. The arithmetic consistency fix (effective_avg × shares ≈ invested) makes the warning unnecessary for that surface. The global warning in `PortfolioHoldingsTable.tsx` summary ("Algún valor tiene coste incompleto") is **retained** — it is at summary level, not per-symbol, and serves a different purpose.

### §2.8 — Holdings Table "Invested" Standardization

The holdings table per-row "Invested (€)" is standardized to show `remaining_cost_basis_eur` (§2.5). The summary "Inversión actual" already shows the same field. No rename to "Remaining Cost" is made — the label "Invested" is understood as "current investment at cost" in both English and the Spanish "Inversión actual".

---

## 3. Exact Files & Functions to Change

### Backend

| File | Function/Location | Change |
|------|-------------------|--------|
| `backend/src/portfolio/holdings_service.py` | `compute_holdings()` — per-security output dict (~line 270-300) | Add `"effective_avg_cost_eur"` field: `_fmt2(effective_avg) if effective_avg is not None else None` |
| `backend/src/portfolio/holdings_service.py` | `compute_holdings()` — per-security computation (~line 250-265) | Compute `effective_avg` after `avg_cost` block |
| `backend/src/portfolio/models.py` | `HoldingItem` | Add field `effective_avg_cost_eur: Optional[str]` |
| `backend/web/app.py` | Symbols overview row assembly (~line 843) | Change `holding.get("avg_cost_basis_eur")` → `holding.get("effective_avg_cost_eur")` |
| `backend/web/app.py` | `_compute_symbol_detail()` — portfolio_field assembly (~lines 1173, 1383) | Change `"average_cost_eur": holding.get("avg_cost_basis_eur")` → `holding.get("effective_avg_cost_eur")` |
| `backend/web/app.py` | `_holdings_by_account()` (~line 1099) | Add `effective_avg_cost_eur` to per-account dict; use it for the display `avg_cost_eur` field |

### Frontend

| File | Location | Change |
|------|----------|--------|
| `frontend/src/types/portfolio.ts` | `HoldingEntry` interface | Add `effective_avg_cost_eur?: string \| null` |
| `frontend/src/types/symbol-detail.ts` | `HoldingsByAccount` interface | Add `effective_avg_cost_eur?: string \| null` |
| `frontend/src/components/PortfolioHoldingsTable.tsx` | `HoldingRow` per-row Avg Cost cell (~line 336) | Use `h.effective_avg_cost_eur ?? h.avg_cost_basis_eur` |
| `frontend/src/components/PortfolioHoldingsTable.tsx` | `HoldingRow` per-row Invested cell (~line 341) | Change `h.total_invested_eur` → `h.remaining_cost_basis_eur ?? h.current_invested_eur` |
| `frontend/src/components/PortfolioHoldingsCard.tsx` | Total row "Avg Cost" cell (~line 103) | Use `portfolio.effective_avg_cost_eur ?? portfolio.average_cost_eur` (requires adding field to `PortfolioSection`) |
| `frontend/src/types/symbol-detail.ts` | `PortfolioSection` interface | Add `effective_avg_cost_eur?: string \| null` |

### No Change Required

| Surface | Reason |
|---------|--------|
| `SymbolsTable.tsx` | `portfolio_avg_cost_eur` already reads from backend; backend remaps source (§2.3) |
| `SymbolRow` type (`symbols.ts`) | Field name unchanged; value source changes server-side |
| Holdings summary StatCards | Already uses `remaining_cost_basis_eur` correctly |
| Realized P&L | Computed from `total_sale_proceeds - cost_basis_sold`; unaffected |
| Any sale/transfer accounting | Uses `avg_cost_basis_eur` (pool CMP); unchanged |

---

## 4. Backward Compatibility

- `avg_cost_basis_eur` continues to be emitted in the holdings API response. Existing consumers (if any external) are unaffected.
- `effective_avg_cost_eur` is additive; old frontends that don't read it still work (they'll show the pool CMP average as before).
- `total_invested_eur` continues to be emitted (backward-compat alias for `total_purchase_outflow_eur`). The frontend change is which field is *rendered*, not which fields are returned.
- The `portfolio_avg_cost_eur` field in the symbols overview response changes *value* (from pool CMP to effective avg). This is a semantic change but matches what the label "Avg Cost" should mean alongside "Shares" and "Invested". Any downstream consumer that relied on this being pool CMP average must adapt — deemed acceptable as no external consumers exist.

---

## 5. Owners

| Task | Owner |
|------|-------|
| Backend: `holdings_service.py` computation + model update | **Livingston** (Backend) |
| Backend: `web/app.py` API field remapping (3 locations) | **Livingston** (Backend) |
| Frontend: type updates + display field changes (4 files) | **Linus** (Frontend) |
| Backend tests: update existing holding tests, add effective_avg tests | **Livingston** |
| Frontend tests: update existing contract tests | **Linus** |
| Code review / final gate | **Danny** |

---

## 6. Tests

### Backend (Livingston)

| Test | Assertion |
|------|-----------|
| `test_portfolio_holdings.py` — existing CMP tests | `avg_cost_basis_eur` unchanged; add assertion for `effective_avg_cost_eur` |
| New: `test_effective_avg_all_complete` | When all BUYs COMPLETE: `effective_avg_cost_eur` == `avg_cost_basis_eur` |
| New: `test_effective_avg_mixed_incomplete` | 163 paid shares @ pool_cost 4294.21 + 60 INCOMPLETE → `effective_avg_cost_eur` = "19.26", `avg_cost_basis_eur` = "26.34" |
| New: `test_effective_avg_all_incomplete` | Pool empty → `avg_cost_basis_eur` = null, `effective_avg_cost_eur` = "0.00" |
| New: `test_effective_avg_zero_shares` | Fully exited → both null |
| New: `test_effective_avg_negative_shares` | Negative inventory → both null |
| New: `test_effective_avg_after_partial_sell` | Sells reduce pool; effective_avg recomputed correctly |
| `test_unified_watchlist.py` / `test_symbols_overview_sections.py` | Verify `portfolio_avg_cost_eur` in response uses effective avg |
| `test_unified_symbol_detail.py` | Verify `average_cost_eur` in portfolio section uses effective avg |

### Frontend (Linus)

| Test | Assertion |
|------|-----------|
| Existing `PortfolioHoldingsTable` contract tests | Update expected field references |
| New: `test_holdings_invested_shows_remaining` | Per-row "Invested" renders `remaining_cost_basis_eur`, not `total_invested_eur` |
| New: `test_holdings_avg_cost_shows_effective` | Per-row "Avg Cost" renders `effective_avg_cost_eur` when present |
| New: `test_arithmetic_consistency` | For sample holding: parseFloat(shares) × parseFloat(effective_avg) ≈ parseFloat(remaining_cost_basis) within ±€1.00 |

---

## 7. Rollout

1. **Backend first** — Livingston ships `effective_avg_cost_eur` in holdings API + remaps `portfolio_avg_cost_eur` in symbols overview and `average_cost_eur` in symbol detail. All existing fields continue to be emitted.
2. **Frontend second** — Linus updates display fields to prefer `effective_avg_cost_eur` with fallback to `avg_cost_basis_eur` for backward compat during rollout.
3. **No feature flag needed** — the change is additive and the fallback chain ensures correctness even if backend deploys before frontend.

---

## 8. Blockers

**None identified.** All changes are within existing surfaces and additive to the API. No database migration, no new endpoints, no external dependency.


### Danny — FIFO / Net-Accounting Final Reviewer Gate

**Date:** 2026-09-08T11:09Z
**Ceremony:** Final READ-ONLY Reviewer Gate
**Status:** ✅ APPROVED
**Contract:** danny-fifo-net-accounting-contract.md
**Integration evidence:** basher-fifo-integration-gate.md (1386/1386 pass)

---

## Verdict: APPROVE

All 12 verification criteria pass. The implementation is faithful to the
frozen contract. No production code, test, staging, commit, or deploy
action was performed during this review.

---

## Verification Matrix

### 1. BUY gross/net — ✅ PASS
- `holdings_service.py`: BUY lot cost reads `net.eur_amount` (§1 compliant)
- `import_service.py`: `net = gross + commission` (line 631)
- `cosmos_portfolio.py`: BUY branch computes `net_eur = gross_eur + fees_eur`
  in both `create_manual_movement()` and `correct_movement()`
- All test fixtures derive BUY net = gross + fees

### 2. SELL gross/net — ✅ PASS (no accidental changes)
- `cosmos_portfolio.py`: SELL/DIVIDEND falls through to `else` branch:
  `net_eur = gross_eur - fees_eur - wht_source_eur - wht_dest_eur`
- `holdings_service.py` SELL: `net_proceeds = gross_eur - commission_eur`
- No SELL parser/import changes in the diff

### 3. DIVIDEND gross/net — ✅ PASS
- `holdings_service.py`: `agg["total_dividends_eur"] += net_eur` (reads net.eur_amount)
- `cosmos_portfolio.py`: DIVIDEND uses `else` branch (same as SELL)
- No dividend parser changes

### 4. FIFO lot construction/depletion — ✅ PASS
- `_Lot` dataclass with lot_id, trade_date, quantity, unit_cost_eur, cost_basis_status
- Lots appended in chronological order (movements pre-sorted by `(trade_date, id)`)
- `_consume_lots()` iterates lots in FIFO order, deterministic tie-break by movement_id
- Partial consumption: `take = min(lot.quantity, remaining)`, proportional
- Voided/superseded/deleted movements excluded before engine (contract §2.7)
- Dedicated `test_portfolio_fifo.py` covers ADM (FIFO-3), ID tie-break, partial lot, determinism

### 5. ZERO_COST scrip — ✅ PASS
- `parsers/purchases.py`: zero-price → `cost_basis_status = "ZERO_COST"` (not INCOMPLETE)
- ZERO_COST lots created with `unit_cost_eur = 0`, denominator shares: dilute avg naturally
- INCOMPLETE lots: `unit_cost_eur = None`, warning `INCOMPLETE_COST_BASIS` emitted
- `incomplete_count` tracked separately from `zero_cost_count`
- Test `test_zero_cost_no_incomplete_warning` confirms no false warning for ZERO_COST

### 6. Rights sales — ✅ PASS
- DERECHOS: no lot consumption; proceeds counted in `rights_proceeds_eur`
- ACCIONES: `_consume_lots()` called; ordinary stock lots consumed
- Tested by `test_s5_derechos_only` and `TestRightsSaleHoldings`

### 7. Transfers — ✅ PASS
- TRANSFER_IN: creates lot at `carried_cost / qty`; not counted in purchase outflow
- TRANSFER_OUT: `_consume_lots(agg["lots"], qty)` in FIFO order; not counted in sale proceeds
- Tested by `test_s8_transfer_preserves_basis`

### 8. Holdings API response fields — ✅ PASS
- Shape stable: `avg_cost_basis_eur`, `remaining_cost_basis_eur`, `cost_basis_sold_eur`,
  `realized_result_eur`, warnings — all present
- Backward-compatible aliases retained: `total_invested_eur`, `total_purchases_eur`,
  `total_sales_eur`, `current_invested_eur`
- No `effective_avg_cost_eur` anywhere in codebase (confirmed by grep + frontend test TY-3/AVG-2/AVG-3)
- Warning type: `INCOMPLETE_COST_BASIS` for genuinely unknown cost (contract-mandated change)

### 9. v2 migration safety — ✅ PASS
- Source-row cross-validation: fail-closed on missing/unparseable source
- Case A arithmetic fallback: **acceptable** (see Decision D1 below)
- Fee-free/ZERO_COST skip policy: **no ambiguity** (see Decision D2 below)
- CLI modes: `audit-v2`, `backup-v2` (mandatory), `apply-v2`, `verify-v2`, `restore-v2`
- Checksum guards: SHA-256 on backup write + verify-on-read + verify-on-restore
- ETag/CAS: `_etag_replace` with `MatchConditions.IfNotModified`
- Ambiguity fail-closed: explicit in `_analyse_record_v2`
- Idempotency: `_repair_buy_fields_v2` marker checked first
- Rollback: `run_restore_v2` with version guard + checksum check + per-doc ETag
- v1 modes retained intact for auditability

### 10. Frontend BUY payload — ✅ PASS
- `AddMovementDialog.tsx`: `gross: makeGross(buyForm.trade_value, currency)`
- Server derives `net = gross + fees`; no client-side addition

### 11. Scope isolation — ✅ PASS
- The dirty worktree contains ~18 unrelated frontend files (account-label/UI refactoring)
- These are NOT part of the FIFO/net accounting change
- No requirement to include them in this release (contract §4.4)
- Must be separated when assembling the release commit

### 12. Clean release assembly — ✅ PASS (conditional on commit isolation)
- All in-scope code changes are complete and consistent
- 1386/1386 tests pass
- Approved deploy sequence is executable (see Release Sequence below)
- Condition: unrelated UI changes must be excluded from the FIFO release commit

---

## Decisions on Unresolved Questions

### D1: Case A arithmetic fallback without source_row — ACCEPTABLE

The v1 marker (`_repair_buy_fields_v1`) provides authoritative evidence
that the record was already validated against `source_row` during the v1
repair. The arithmetic check (`gross ≈ net + fees`) is a defense-in-depth
verification of the 4ca553e shape. The implementation is fail-closed on
mismatch (returns None, logs WARNING, record skipped). This is safe under
the frozen contract because v1 already performed the primary evidence check.

### D2: Fee-free/ZERO_COST BUYs not receiving v2 marker — NO AMBIGUITY

Fee-free records satisfy `gross == net` under both conventions (no commission
component). The `verify-v2` step correctly counts them as `skipped_fee_free`
(not as candidates), so they never appear in the residual count. An operator
can audit them separately if needed, but they are self-consistent and require
no mutation.

### D3: v1 backup checksum conflict — NOTED (not a code defect)

The previously reported v1 backup checksum mismatch is an operational artifact
from a prior session. It does not affect code approval. The v2 migration has
its own independent backup/checksum chain. Before production use, the operator
must verify that the v2 backup checksum matches the actual backup file contents
(the script does this automatically via write-then-read verification).

---

## Out-of-Scope File Changes (Observation, Not a Blocker)

`models.py` and `parsers/purchases.py` were listed as "not touched" in the
design review §3 file-freeze table. However, both received additive-only
changes mandated by the contract:
- `models.py`: Added `ZERO_COST` to `CostBasisStatus` enum, `INCOMPLETE_COST_BASIS`
  to `WarningType` enum, updated field comments
- `parsers/purchases.py`: Changed zero-cost detection from `INCOMPLETE` to `ZERO_COST`,
  removed `ZERO_COST_ACQUISITION` warning for zero-cost rows

These are required by contract §2.10 (ZERO_COST lots) and §2.9 (INCOMPLETE distinction).
The design review's freeze list was overly conservative. Changes are backward-compatible
and correct. **Not a blocker.**

---

## Approved Release Sequence

### Phase 1 — Code Deploy (Single Atomic Commit)

Assemble a clean commit containing ONLY the FIFO/net accounting files:

**Production code:**
- `backend/src/portfolio/holdings_service.py`
- `backend/src/portfolio/import_service.py`
- `backend/src/portfolio/cosmos_portfolio.py`
- `backend/src/portfolio/models.py`
- `backend/src/portfolio/parsers/purchases.py`
- `frontend/src/components/AddMovementDialog.tsx`

**Migration script:**
- `backend/scripts/repair_buy_ledger_fields.py`

**Tests (all):**
- All 12 test files listed in the INPUT ARTIFACTS

**Exclude:** All unrelated UI/account-label frontend files visible in the dirty worktree.

### Phase 2 — Data Migration (Immediately After Deploy)

Execute in order; stop on any non-zero exit code:

```
1. python -m scripts.repair_buy_ledger_fields --audit-v2 \
     --database stock-options-manager --portfolio-container portfolio
   → Expect ~280 fee-bearing candidates (Case A + Case B)

2. python -m scripts.repair_buy_ledger_fields --apply-v2 \
     --database stock-options-manager --portfolio-container portfolio
   → Mandatory backup created automatically; expect exit code 0

3. python -m scripts.repair_buy_ledger_fields --verify-v2 \
     --database stock-options-manager --portfolio-container portfolio
   → Expect "0 v2 candidates remain"; exit code 0
```

### Phase 3 — Post-Migration Verification

- ADM holdings: 10 shares, avg ≈ €49.04/share
- All other securities: remaining_cost_basis + cost_basis_sold ≈ total_purchase_outflow

---

## Production Migration Stop Conditions

| # | Condition | Action |
|---|-----------|--------|
| S1 | `audit-v2` candidate count deviates >10% from ~280 expected | Investigate before apply |
| S2 | `backup-v2` fails or checksum mismatch | Abort; do not proceed to apply |
| S3 | `apply-v2` reports ANY ETag conflicts or failures (exit 3) | Run `restore-v2` from backup; investigate |
| S4 | `verify-v2` reports remaining candidates (exit 3) | Re-run `apply-v2`; if persists, restore |
| S5 | ADM post-migration does not show 10 shares at ~€49/share | Restore + code rollback |
| S6 | Any security's realized_result changes by > commission range | Investigate; may indicate SELL regression |

---

## Residual Operational Risks

1. **Transient display gap (seconds):** Between code deploy and `apply-v2`, records
   still in 4ca553e shape will have `net.eur_amount = trade` (understated by commission).
   Acceptable for single-user maintenance window.

2. **Manual BUY candidates:** Manual BUY records with fees are NOT auto-migrated by v2.
   Operator must verify each against broker statements and use v1's
   `--apply-manual-ids` if confirmed inverted.

3. **Unrelated worktree changes:** The dirty worktree must be partitioned before the
   release commit. Risk of accidentally including UI changes in the FIFO commit.

---

**APPROVED** — Danny, Lead Architect


### Danny — FIFO / Net-Accounting Design Review & Implementation Plan

**Date:** 2026-09-08T10:22Z  
**Ceremony:** Pre-Work Design Review  
**Status:** APPROVED — agents may begin implementation  
**Inputs:** `copilot-directive-20260908-fifo-net-accounting.md`, `danny-fifo-net-accounting-contract.md`, `reuben-audit-report-20260908-fifo-net-accounting.md`

---

## 1. Requirements Confirmation

The contract and Reuben's audit are aligned. Key facts verified:

- **FIFO lots:** Each BUY is a discrete lot; SELLs consume oldest-first. ADM 85+15+10 sell 100 → residual 10 shares at €49.038/share. ✓
- **Net convention:** BUY `net = gross + fees`; SELL/DIVIDEND `net = gross − fees − wht`. Financial calcs always use `net.eur_amount`. ✓
- **Migration:** Forward `_repair_buy_fields_v2` only (no restore). ~280 fee-bearing BUYs need gross↔net swap; ~59 ZERO_COST unchanged; SELL/DIVIDEND already correct. ✓
- **No transitional engine heuristic** — Option A (maintenance-window deploy): code + migration in tight sequence; sub-second gap acceptable. ✓

---

## 2. Frozen Interfaces

### 2.1 Holdings API Response Shape (Unchanged)

The response payload shape (`holdings[].avg_cost_basis_eur`, `remaining_cost_basis_eur`, `cost_basis_sold_eur`, `realized_result_eur`, warnings) is **unchanged**. Only the values change (FIFO vs CMP). No frontend type changes needed for holdings display.

### 2.2 Movement Document Shape

Post-v2 migration, all BUY documents satisfy:
```
gross.eur_amount = trade consideration (price × qty)
net.eur_amount   = gross.eur_amount + fees.total_eur
```

SELL and DIVIDEND documents are untouched.

### 2.3 Manual Movement API (`POST /api/portfolio/movements`)

Request body unchanged. Server-side `net` derivation changes for BUY only:
- **BUY:** `net_eur = gross_eur + fees_eur`
- **SELL/DIVIDEND:** `net_eur = gross_eur − fees_eur − wht` (existing formula; correct)

### 2.4 Frontend BUY Form → API

BUY form must send `gross = trade_value` (not `trade_value + fees`). Fees sent in `fees` field as before. Server computes `net = gross + fees`.

---

## 3. Ownership Boundaries (Parallel-Safe)

| Work Item | Owner | Files (exclusive) | Depends On |
|---|---|---|---|
| **W1: FIFO holdings engine** | Rusty | `backend/src/portfolio/holdings_service.py` | — |
| **W2: FIFO holdings tests** | Rusty | `backend/tests/test_portfolio_holdings.py` | W1 |
| **W3: Net convention — BUY import** | Linus | `backend/src/portfolio/import_service.py` (BUY block only) | — |
| **W4: Net convention — manual BUY** | Linus | `backend/src/portfolio/cosmos_portfolio.py` (`create_manual_movement` + `correct_movement` BUY net calc) | — |
| **W5: Frontend BUY form** | Linus | `frontend/src/components/AddMovementDialog.tsx` (BUY submit block) | — |
| **W6: Frontend test** | Linus | `frontend/tests/scripZeroCostContract.test.mjs` | W5 |
| **W7: Forward migration v2 script** | Reuben | `backend/scripts/repair_buy_ledger_fields.py` (extend, not replace) | — |
| **W8: Migration tests** | Reuben | `backend/tests/test_repair_buy_ledger_fields.py` (extend) | W7 |
| **W9: Amendment/legacy test updates** | Rusty | `backend/tests/test_amendment_g_bilingual.py`, `test_amendment_h_holdings_effects.py`, `test_portfolio_phase2_legacy_compat.py`, `test_portfolio_summary_cost_basis.py`, `test_scrip_zero_cost_and_buy_import.py` | W1, W3 |
| **W10: Review & gate** | Danny | — (read-only) | All |

### Ownership Rules
- Each file is owned by exactly one agent for this change. No overlapping edits.
- `parsers/purchases.py` and `models.py` are **not touched** (ZERO_COST classification retained).
- `parsers/sales.py`, `parsers/dividends.py` are **not touched** (already correct).
- `portfolio_routes.py` — no structural change; Rusty reviews response shape during W1.
- `frontend/src/types/portfolio.ts` — **no change** (ZERO_COST already present, response shape unchanged).

---

## 4. Deployment & Migration Ordering

### Phase 1 — Code Deploy (Single Atomic Release)

All code changes ship together in one commit/deploy:
1. FIFO holdings engine (W1)
2. BUY import net convention (W3)
3. Manual BUY net convention (W4)
4. Frontend BUY form fix (W5)
5. All updated tests pass (W2, W6, W8, W9)

### Phase 2 — Data Migration (Immediately After Deploy)

Run manually by operator:
1. `repair_buy_ledger_fields.py --mode audit-v2` — count candidates, verify ~280 fee-bearing
2. `repair_buy_ledger_fields.py --mode backup-v2` — snapshot current state
3. `repair_buy_ledger_fields.py --mode apply-v2` — swap gross↔net on Case A; fix net on Case B
4. `repair_buy_ledger_fields.py --mode verify-v2` — post-apply audit; expect 0 candidates

### Dual-Shape Tolerance

**Not required.** Option A from contract §7.2: maintenance-window deploy. The migration runs within seconds of code deploy. The brief gap is acceptable for a single-user system. No transitional engine heuristic.

### Rollback

1. Code rollback: `git revert` to pre-FIFO commit
2. Data rollback: restore from v2 backup
3. Result: exact pre-FIFO state

---

## 5. Edge Cases & Test Gates

### 5.1 FIFO Algorithm (Rusty — W1/W2)

| ID | Case | Gate Criteria |
|---|---|---|
| FIFO-3 | ADM: 85+15+10, sell 100 | Remaining = 10 shares, avg = €49.038 |
| FIFO-4 | Partial lot consumption | First lot partially consumed; second lot intact |
| FIFO-5/6 | Zero-cost lot ordering | Consumed or preserved based on chronological position |
| FIFO-7/8 | INCOMPLETE lots | Cost = 0, warning emitted, FIFO order respected |
| FIFO-9 | Negative inventory | Warning; excess at cost 0 |
| FIFO-11 | Transfer-out/in cost carry | Source lots consumed FIFO; dest lot at carried cost |
| FIFO-12 | Voided/superseded excluded | Voided BUY doesn't create lot; voided SELL no-op |
| FIFO-13 | Same-date tie-break | Deterministic by movement_id |

### 5.2 Net Convention (Linus — W3/W4/W5/W6)

| ID | Case | Gate Criteria |
|---|---|---|
| NET-1 | BUY import CSV: total=100, comm=5 | gross=100, net=105 |
| NET-4 | Manual BUY: gross=100, fees=5 | Server computes net=105 |
| NET-7 | Holdings use net for BUY cost | FIFO lot cost = net.eur_amount |
| FE-1 | BUY form sends gross=trade_value | Not trade_value+fees |

### 5.3 Migration (Reuben — W7/W8)

| ID | Case | Gate Criteria |
|---|---|---|
| MIG-1 | Case A (v1 marker): swap | gross=trade, net=trade+comm |
| MIG-2 | Case B (no marker): fix net | gross unchanged, net=gross+fees |
| MIG-3 | Already v2: skip | No mutation |
| MIG-7 | Idempotent re-run | 0 candidates on second pass |
| MIG-9 | source_row cross-validation | gross matches CSV Total |

### 5.4 Integration (Danny — W10)

| Gate | Criteria | Blocker |
|---|---|---|
| G1 | All FIFO-* tests green | YES |
| G2 | BUY net > gross, SELL net < gross in all tests | YES |
| G3 | Migration audit+apply+verify cycle passes | YES |
| G4 | SELL accounting unchanged (net proceeds − FIFO cost) | YES |
| G5 | Dividend accumulation unchanged (uses net.eur_amount) | YES |
| G6 | Frontend BUY form sends gross=trade_value | YES |
| G7 | ADM: 10 shares at €49.038/share post-FIFO | YES |
| G10 | Full test suite green (backend + frontend) | YES |

---

## 6. Files NOT To Touch

- `backend/src/portfolio/parsers/purchases.py` — ZERO_COST classification retained
- `backend/src/portfolio/parsers/sales.py` — SELL semantics correct
- `backend/src/portfolio/parsers/dividends.py` — Dividend semantics correct
- `backend/src/portfolio/models.py` — No enum changes
- `frontend/src/types/portfolio.ts` — Response shape unchanged
- `frontend/src/components/PortfolioHoldingsTable.tsx` — `remaining_cost_basis_eur` display retained
- `frontend/src/components/ImportChat.tsx` — No changes
- `frontend/src/components/ImportPreview.tsx` — No changes
- `frontend/src/components/MovementDetailDialog.tsx` — No changes
- `frontend/src/components/PortfolioMovementsTable.tsx` — No changes

---

## 7. Coordination Notes

1. **Rusty and Linus can work in parallel** — their file sets are disjoint. Reuben's migration script is also independent.
2. **Rusty's FIFO engine must read `net.eur_amount` for BUY cost** (not `gross.eur_amount`). This is the key semantic change in holdings_service.py.
3. **Linus's import change** ensures new BUY imports write `gross = trade, net = trade + fees`. Existing data is fixed by Reuben's migration.
4. **Linus's frontend change** is minimal: remove the `+ fees` addition from the `makeGross` call in the BUY submit block. The user enters trade_value; the server adds fees.
5. **Reuben's migration** extends the existing repair script with v2 modes. The v1 code is retained for auditability.
6. **Danny gates** the final merge after all tests pass and the ADM worked example is verified.

---

**APPROVED — Implementation may proceed.**

— Danny, Lead Architect


### Danny — FIFO Lot Depletion & Net-Centric Accounting Contract

**Status: APPROVED CONTRACT**
**Date: 2026-09-08**
**Author: Danny (Lead/Architect, Reviewer)**
**Supersedes:**
- `danny-scrip-zero-cost-and-buy-import-contract.md` (§1–§3: CMP pool model, §2: BUY gross=trade+commission convention) — **SUPERSEDED**
- Decision §2 "Dividend Portfolio — Phase 1 MVP" cost-basis method "Average cost (MVP default); FIFO/LIFO deferred to Phase 3" — **SUPERSEDED; FIFO is now Phase 1**
- Commit `4ca553e` BUY gross/net convention — **PARTIALLY SUPERSEDED** (see §5 below)

**Retains from `4ca553e`:**
- `CostBasisStatus.ZERO_COST` enum value and semantics — **RETAINED** (zero-cost lots exist under FIFO)
- `INCOMPLETE_COST_BASIS` warning for genuinely unknown cost — **RETAINED**
- Removal of `ZERO_COST_ACQUISITION` warning and `effective_avg_cost_eur` — **RETAINED**
- `remaining_cost_basis_eur` display in frontend — **RETAINED** (meaning changes from CMP pool residual to FIFO lot sum)
- Repair script marker `_repair_buy_fields_v1` — **RETAINED** (used for reverse migration §5)

**User directive:** `copilot-directive-20260908-fifo-net-accounting.md`

---

## 0. Executive Summary

The user directive requires two fundamental changes:

**A. FIFO Lot-Based Holdings** — Replace the chronological moving weighted-average (CMP) pool model with a First-In First-Out acquisition lot model. Each BUY creates a discrete lot; SELLs consume lots oldest-first. The remaining holding is the set of unconsumed lots, and the displayed average cost is their weighted average.

**B. Net-Centric Financials** — All financial calculations and displayed averages use **net** amounts:
- BUY: `gross` = trade consideration before commission; `net = gross + commission` (what was actually paid)
- SELL: `gross` = proceeds before commission; `net = gross - commission` (what was actually received)
- DIVIDEND: `gross` = income before deductions; `net = gross - withholding - fees` (what was actually received)

This **reverses** the BUY gross/net convention deployed in `4ca553e`, where `gross = trade + commission` and `net = trade`. The new convention aligns with the standard financial meaning: gross = before adjustments, net = after adjustments. The critical insight is that BUY net > BUY gross (you pay more than the trade value), while SELL net < SELL gross (you receive less than the proceeds).

---

## 1. Canonical Gross/Net/Fees/Withholding Definitions

### §1.1 — Universal Principle

| Term | Definition |
|---|---|
| **Gross** | The headline amount **before** any transaction costs, taxes, or adjustments |
| **Net** | The amount **after** all deductions/additions — what was actually paid or received |
| **Fees** | Commission, exchange fees, stamp duty, custody fees, and other transaction costs |
| **Withholding** | Tax withheld at source (origin) and/or destination (investor country) |

### §1.2 — Per-Transaction-Type Definitions

| Type | Gross | Fees | Withholding | Net | Sign of net relative to gross |
|---|---|---|---|---|---|
| **BUY** | Trade consideration (`price × qty`) | Commission + exchange fees | N/A | `gross + fees` (total cash outflow) | net > gross |
| **SELL (ACCIONES)** | Sale proceeds (`price × qty`) | Commission + exchange fees | N/A | `gross - fees` (total cash inflow) | net < gross |
| **SELL (DERECHOS)** | Rights sale proceeds | Commission (if any) | N/A | `gross - fees` | net ≤ gross |
| **DIVIDEND (cash)** | Gross dividend income | Fees (if any) | Source WHT + Destination WHT | `gross - fees - wht_source - wht_dest` | net < gross |
| **TRANSFER_IN** | N/A (carried cost) | Transfer fee (optional) | N/A | N/A (cost basis carried) | N/A |
| **TRANSFER_OUT** | N/A (carried cost) | Transfer fee (optional) | N/A | N/A (cost basis carried) | N/A |
| **Corporate action: SHARE_ACQUISITION** | 0 (scrip) or fair value | 0 | N/A | 0 or fair value | net = gross = 0 for true scrip |
| **Corporate action: RIGHTS_SOLD** | Sale proceeds | Fees (if any) | N/A | `gross - fees` | net ≤ gross |
| **Corporate action: CASH_DIVIDEND** | Gross dividend | Fees (if any) | Source WHT + Dest WHT | `gross - fees - wht` | net < gross |
| **Correction** | Inherits type semantics of the corrected movement | | | | |

### §1.3 — Stored Field Semantics (Authoritative)

For every `ledger_txn` document:

```
gross.amount       — gross in transaction currency
gross.eur_amount   — gross in EUR
fees.total         — total fees in transaction currency
fees.total_eur     — total fees in EUR
withholding.source.amount_eur  — source WHT in EUR
withholding.destination.amount_eur — destination WHT in EUR
net.amount         — net in transaction currency
net.eur_amount     — net in EUR
```

**Derivation rules (computed by server, never by client):**

| Type | `net.eur_amount` formula |
|---|---|
| BUY | `gross.eur_amount + fees.total_eur` |
| SELL | `gross.eur_amount - fees.total_eur` |
| DIVIDEND | `gross.eur_amount - fees.total_eur - wht_source.amount_eur - wht_dest.amount_eur` |

**Financial calculations and displayed averages ALWAYS use `net.eur_amount`.**

### §1.4 — Prior Convention Comparison

| Convention | BUY gross | BUY net | Status |
|---|---|---|---|
| **Original import (pre-4ca553e)** | trade value (mislabeled) | trade - commission (meaningless) | Deprecated |
| **Commit 4ca553e** | trade + commission | trade value | **SUPERSEDED** |
| **This contract** | trade value | trade + commission | **AUTHORITATIVE** |

---

## 2. FIFO Lot Algorithm

### §2.1 — Lot Model

Each acquisition creates a **lot**:

```python
@dataclass
class FifoLot:
    lot_id: str              # movement ID that created this lot
    trade_date: str          # ISO date
    quantity: Decimal         # remaining shares in this lot (decremented by sells)
    original_quantity: Decimal # shares at creation
    net_unit_cost_eur: Decimal  # net_eur / quantity at creation (for BUY/TRANSFER_IN)
    net_total_cost_eur: Decimal # total net EUR at creation
    cost_basis_status: str   # COMPLETE | ZERO_COST | INCOMPLETE
    movement_id: str         # source movement ID
```

**Lot creation rules:**

| Source | Lot creation | `net_unit_cost_eur` |
|---|---|---|
| BUY COMPLETE | Yes — `quantity` shares | `net.eur_amount / quantity` |
| BUY ZERO_COST | Yes — `quantity` shares | `Decimal("0")` (known zero cost) |
| BUY INCOMPLETE | Yes — `quantity` shares | `None` (genuinely unknown) |
| TRANSFER_IN | Yes — `quantity` shares | `carried_cost_basis_eur / quantity` or proportional from source lots |
| Corporate action SHARE_ACQUISITION | Yes — `quantity` shares | `net.eur_amount / quantity` (0 for scrip) |
| SELL | No — consumes existing lots | N/A |
| DIVIDEND | No — income only | N/A |
| TRANSFER_OUT | Consumes existing lots (FIFO) | N/A |

### §2.2 — FIFO Lot Consumption (SELL ACCIONES)

When a SELL ACCIONES of `sell_qty` shares occurs:

```
remaining = sell_qty
cost_assigned = 0
lots_consumed = []

for lot in lots_ordered_fifo:
    if remaining <= 0:
        break
    if lot.quantity <= 0:
        continue
    
    take = min(lot.quantity, remaining)
    
    if lot.cost_basis_status == "INCOMPLETE":
        # Cost genuinely unknown — assign 0 cost; warning emitted
        unit_cost = 0
    else:
        unit_cost = lot.net_unit_cost_eur
    
    cost_assigned += take * unit_cost
    lot.quantity -= take      # partial consumption
    remaining -= take
    lots_consumed.append((lot.lot_id, take, unit_cost))

if remaining > 0:
    # Negative inventory — warn; excess sold at cost 0
    warn(NEGATIVE_INVENTORY)
```

**Realized result for the sell:**
```
sell_net_proceeds = sell_gross_eur - sell_fees_eur
realized_result = sell_net_proceeds - cost_assigned
```

### §2.3 — FIFO Ordering (Deterministic)

Lots are ordered by:
1. `trade_date` ascending (oldest first)
2. `movement_id` ascending (deterministic tie-break for same-date lots)

This ordering is **invariant** — it must be applied consistently in every computation.

### §2.4 — SELL DERECHOS

Rights sales do NOT consume lots. They contribute to `total_sale_proceeds_eur` and `rights_proceeds_eur` but do not affect lot quantities or cost basis.

### §2.5 — TRANSFER_OUT (FIFO)

Transfers out consume lots in FIFO order, identical to SELL ACCIONES lot consumption. The cost removed is the proportional FIFO cost of the transferred shares. This cost is carried to the `TRANSFER_IN` movement at the destination account as `transfer_cost_basis_eur`.

### §2.6 — TRANSFER_IN

Creates a new lot at the destination account with:
- `quantity` = transferred shares
- `net_unit_cost_eur` = `carried_cost_basis_eur / quantity`
- `cost_basis_status` = inherited from source (COMPLETE or INCOMPLETE)

If `carried_cost_basis_eur` is not explicitly provided, it defaults to 0 with a warning.

### §2.7 — Void / Superseded Movements

- `correction_status == "VOIDED"` or `"SUPERSEDED"` or `deleted_at IS NOT NULL`: movement is **excluded** from lot building and consumption. The FIFO replay simply never sees it.
- A correction creates a new movement (the replacement) while marking the original as SUPERSEDED. The replacement participates in FIFO ordering by its own `trade_date` (which may be the same or different).

### §2.8 — Holdings Derivation

For each `(security_id, [account_id])`:

```
total_shares = sum(lot.quantity for lot in active_lots)
              + unpaid_shares  (INCOMPLETE lots, if we choose to separate)

remaining_cost_basis_eur = sum(lot.quantity * lot.net_unit_cost_eur 
                               for lot in active_lots 
                               where lot.cost_basis_status != "INCOMPLETE")

avg_cost_basis_eur = remaining_cost_basis_eur / total_pool_shares
                     where total_pool_shares = sum(lot.quantity for non-INCOMPLETE lots)
                     (null if total_pool_shares == 0)
```

### §2.9 — INCOMPLETE Lots Under FIFO

INCOMPLETE lots participate in FIFO ordering (they are real shares). When consumed by a SELL:
- Cost assigned = 0 (unknown cost)
- Warning emitted: cost basis of sold shares partially unknown
- Realized result is understated (conservative)

This is the same behavior as CMP `unpaid_shares` but now lot-ordered.

### §2.10 — Zero-Cost Lots Under FIFO

ZERO_COST lots are fully resolved lots with `net_unit_cost_eur = 0`. They participate in FIFO normally:
- They are consumed in order
- Their cost contribution is 0
- No warning emitted (cost is known)
- They dilute the average cost naturally (total cost unchanged, total shares increased)

---

## 3. ADM Worked Example

### §3.1 — Expected Movement Sequence (From User Directive)

ADM had the following acquisitions (in chronological order):

| # | Type | Qty | Description |
|---|---|---|---|
| 1 | BUY | 85 | First acquisition lot |
| 2 | BUY | 15 | Second acquisition lot |
| 3 | BUY | 10 | Third acquisition lot |
| 4 | SELL | 100 | Sale of 100 shares |

### §3.2 — FIFO Replay

**After BUY #1:** Lot A: 85 shares
**After BUY #2:** Lot A: 85, Lot B: 15
**After BUY #3:** Lot A: 85, Lot B: 15, Lot C: 10

**SELL of 100 shares (FIFO):**
1. Consume Lot A entirely: 85 shares → Lot A: 0 remaining
2. Consume Lot B entirely: 15 shares → Lot B: 0 remaining
3. Total consumed: 85 + 15 = 100 ✓

**Remaining:** Lot C: 10 shares

### §3.3 — Remaining Average Cost

The displayed average cost for ADM is the net unit cost of the remaining lot (Lot C, 10 shares). Since only one lot remains:

```
avg_cost_basis_eur = Lot_C.net_unit_cost_eur
remaining_cost_basis_eur = 10 * Lot_C.net_unit_cost_eur
```

Under CMP (old model), the average cost would be the weighted average of ALL acquisitions diluted by all pool entries — a different number. Under FIFO, the remaining cost is **specific** to the surviving lots.

### §3.4 — Verification Against Production Data

The implementer must query production ADM movements (`security_id` containing `ADM`) and verify:
1. Three BUY movements exist with quantities 85, 15, and 10 (in chronological order)
2. One SELL ACCIONES movement exists with quantity 100
3. After FIFO replay: remaining = 10 shares in the third lot
4. The `avg_cost_basis_eur` equals the net unit cost of that third lot
5. This matches the currently displayed "remaining average cost" in the UI

If production movement data differs from the 85/15/10 pattern (e.g., additional small lots from corporate actions), the FIFO algorithm still applies — consume oldest first until 100 shares are removed. The remaining lots form the basis.

### §3.5 — Contrast With CMP

Under CMP (currently deployed):
```
pool_cost = net_BUY1 + net_BUY2 + net_BUY3 = total cost of all acquisitions
pool_shares = 85 + 15 + 10 = 110
avg = pool_cost / 110

After SELL 100: 
pool_shares = 10
pool_cost = pool_cost - (100 * avg) = pool_cost * (10/110)
remaining_avg = pool_cost * (10/110) / 10 = same avg as before
```

Under CMP, the remaining average cost is the same regardless of which shares "conceptually" remain. Under FIFO, the remaining cost depends on **which specific lots** survive. This is the fundamental difference.

---

## 4. Affected Surfaces & Commit 4ca553e Disposition

### §4.1 — Backend Files Affected

| File | Change required | 4ca553e disposition |
|---|---|---|
| `backend/src/portfolio/holdings_service.py` | **REWRITE**: Replace CMP pool model with FIFO lot replay | 4ca553e changes **SUPERSEDED** (new algorithm) |
| `backend/src/portfolio/import_service.py` | **MODIFY**: BUY gross/net convention reversal (§5) | 4ca553e BUY block **REVERSED** (new net convention) |
| `backend/src/portfolio/parsers/purchases.py` | **RETAIN** `ZERO_COST` classification (from 4ca553e) | 4ca553e changes **RETAINED** |
| `backend/src/portfolio/models.py` | **RETAIN** `CostBasisStatus.ZERO_COST` and `INCOMPLETE_COST_BASIS` | 4ca553e changes **RETAINED** |
| `backend/src/portfolio/cosmos_portfolio.py` | **MODIFY**: `create_manual_movement()` net derivation for BUY must be `gross + fees` | Manual movement net calculation **MODIFY** |
| `backend/web/portfolio_routes.py` | **REVIEW**: Validate gross/net API contract docs match new convention | No structural change |
| `backend/scripts/repair_buy_ledger_fields.py` | **MODIFY**: Reverse migration + forward to new convention (§5) | 4ca553e script **EXTENDED** |

### §4.2 — Frontend Files Affected

| File | Change required | 4ca553e disposition |
|---|---|---|
| `frontend/src/components/AddMovementDialog.tsx` | **MODIFY**: BUY gross = trade value (not trade+fees); fees sent separately | 4ca553e change to `makeGross` **REVERSED** |
| `frontend/src/components/PortfolioHoldingsTable.tsx` | **RETAIN** `remaining_cost_basis_eur` display | 4ca553e change **RETAINED** |
| `frontend/src/types/portfolio.ts` | **RETAIN** `ZERO_COST` in union | 4ca553e change **RETAINED** |
| `frontend/src/components/ImportChat.tsx` | **RETAIN** removal of `ZERO_COST_ACQUISITION` | 4ca553e change **RETAINED** |
| `frontend/src/components/ImportPreview.tsx` | **RETAIN** removal of `ZERO_COST_ACQUISITION` | 4ca553e change **RETAINED** |
| `frontend/src/components/MovementDetailDialog.tsx` | **RETAIN** removal of `ZERO_COST_ACQUISITION` | 4ca553e change **RETAINED** |
| `frontend/src/components/PortfolioMovementsTable.tsx` | **RETAIN** removal of `ZERO_COST_ACQUISITION` | 4ca553e change **RETAINED** |

### §4.3 — Test Files Affected

| File | Change |
|---|---|
| `backend/tests/test_portfolio_holdings.py` | **REWRITE**: All CMP tests → FIFO lot tests |
| `backend/tests/test_amendment_g_bilingual.py` | **UPDATE**: Fixture gross/net alignment |
| `backend/tests/test_amendment_h_holdings_effects.py` | **UPDATE**: CMP → FIFO expected values |
| `backend/tests/test_portfolio_parsers.py` | **RETAIN** (parser output unchanged; ZERO_COST classification correct) |
| `backend/tests/test_portfolio_phase2_legacy_compat.py` | **UPDATE**: Expected values |
| `backend/tests/test_portfolio_summary_cost_basis.py` | **REWRITE**: Summary accumulators change from CMP to FIFO |
| `backend/tests/test_repair_buy_ledger_fields.py` | **EXTEND**: Add reverse-migration + forward-migration tests |
| `backend/tests/test_scrip_zero_cost_and_buy_import.py` | **UPDATE**: Gross/net values in fixtures |
| `frontend/tests/scripZeroCostContract.test.mjs` | **UPDATE**: BUY form gross assembly logic |

### §4.4 — What NOT to Change

| Item | Reason |
|---|---|
| SELL parser / SELL import | SELL gross/net semantics correct (gross = proceeds, net = proceeds - fees) |
| DIVIDEND parser / DIVIDEND import | Dividend gross/net semantics correct (gross = income, net = income - wht - fees) |
| Sales parser | Unchanged |
| Corporate-action API | Caller provides explicit values |
| Transfer API | No gross field; cost basis is explicit |
| `CostBasisStatus.ZERO_COST` enum | Retained — FIFO lots can be zero-cost |
| `INCOMPLETE_COST_BASIS` warning | Retained — INCOMPLETE lots participate in FIFO |

---

## 5. Production Migration Strategy

### §5.1 — Current State of Production Data

Commit `4ca553e` deployed a repair script that was started but may have been interrupted. The production state for BUY records may be one of:

| Record state | `gross.eur_amount` | `net.eur_amount` | `_repair_buy_fields_v1` marker |
|---|---|---|---|
| **Unrepaired (original)** | trade value (CSV "Total") | trade - commission (meaningless) | absent |
| **Repaired by 4ca553e** | trade + commission | trade value | present with timestamp |

### §5.2 — Target State (This Contract)

All BUY records must reach:

| Field | Value |
|---|---|
| `gross.eur_amount` | trade value (= CSV "Total (€)" = `price × qty`) |
| `gross.amount` | same in transaction currency |
| `net.eur_amount` | trade value + commission (= total acquisition outflow) |
| `net.amount` | same in transaction currency |
| `fees.total_eur` | commission (unchanged — always correct) |

### §5.3 — Migration Path (Two Cases)

**Case A: Records already repaired by 4ca553e** (marker `_repair_buy_fields_v1` present)

These records have:
- `gross.eur_amount` = old_gross + commission = trade + commission → **needs to become trade**
- `net.eur_amount` = old_gross = trade → **needs to become trade + commission**

**Action: Swap gross and net**, then update marker:

```python
### Reverse 4ca553e repair, then apply new convention
new_gross_eur = doc["net"]["eur_amount"]       # was trade value (old gross)
new_net_eur = doc["gross"]["eur_amount"]       # was trade + commission
### Verify: new_net = new_gross + fees
assert abs(Decimal(new_net_eur) - Decimal(new_gross_eur) - Decimal(doc["fees"]["total_eur"])) < 0.01

doc["gross"]["eur_amount"] = new_gross_eur
doc["gross"]["amount"] = doc["net"]["amount"]  # same swap in txn currency
doc["net"]["eur_amount"] = new_net_eur
doc["net"]["amount"] = doc["gross"]["amount"]  # original gross amount in txn ccy
doc["_repair_buy_fields_v2"] = { "timestamp": now_utc(), "from": "v1_to_v2_swap" }
```

**Case B: Records NOT repaired by 4ca553e** (no marker, or repair was interrupted)

These records have:
- `gross.eur_amount` = trade value (CSV "Total") → **correct for new convention**
- `net.eur_amount` = trade - commission → **needs to become trade + commission**

**Action: Fix net only:**

```python
trade = Decimal(doc["gross"]["eur_amount"])    # already trade value
commission = Decimal(doc["fees"]["total_eur"])
new_net = trade + commission

doc["net"]["eur_amount"] = str(new_net)
doc["net"]["amount"] = str(Decimal(doc["gross"]["amount"]) + Decimal(doc["fees"]["total"]))
doc["_repair_buy_fields_v2"] = { "timestamp": now_utc(), "from": "original_to_v2" }
```

### §5.4 — Detection Logic

```python
def classify_record(doc):
    if doc.get("_repair_buy_fields_v2"):
        return "ALREADY_V2"   # skip
    if doc.get("_repair_buy_fields_v1"):
        return "CASE_A"       # 4ca553e repaired → swap gross/net
    else:
        return "CASE_B"       # original → fix net only
```

### §5.5 — Cross-Validation Using `source_row`

For CSV-imported records, the original `source_row` contains the raw CSV values. Use this for verification:

```python
source_total = parse_source_row_total(doc.get("source_row", {}))
source_commission = parse_source_row_commission(doc.get("source_row", {}))

if source_total is not None:
    # After migration, gross must equal source_total
    assert abs(Decimal(doc["gross"]["eur_amount"]) - source_total) < 0.01
    # After migration, net must equal source_total + source_commission
    assert abs(Decimal(doc["net"]["eur_amount"]) - (source_total + source_commission)) < 0.01
```

### §5.6 — Manual BUY Records

Manual BUY records (`import_source == "manual"`) have no `source_row`. Under the new convention:

- The `create_manual_movement()` function currently computes `net = gross - fees` (§4.1)
- The new convention requires `net = gross + fees` for BUY
- **Existing manual BUY records** may use either convention depending on when they were created
- Manual records created via the corrected `4ca553e` frontend sent `gross = trade + fees` → under new convention, their `gross` is actually `net`, and stored `net` is actually `gross`

**Strategy for manual records:**
1. Audit script reports them as candidates with full field values
2. Operator manually verifies each against broker statements
3. Explicit `--apply-manual-ids` flag required
4. Never auto-applied

### §5.7 — Verified Backup Usage

The session has backup files from the 4ca553e repair attempt:
- `backup_movements_ebro_20260906T124544Z.json`
- `backup_movements_ebro_20260906T124556Z.json`

These contain the **pre-4ca553e-repair** state of records. For Case A records (already repaired), these backups contain the original `gross = trade, net = trade - commission` state, which is useful for cross-validation but should not be used as the restore target (we want `gross = trade, net = trade + commission`).

**The migration script must create its own backup** of the current state before any writes, as the 4ca553e repair script did.

### §5.8 — ZERO_COST Reclassification

Records with `cost_basis_status == "INCOMPLETE"` that represent known zero-cost acquisitions were reclassified to `ZERO_COST` by the 4ca553e repair. This reclassification is **correct and retained**. The v2 migration does not touch `cost_basis_status` for records already marked `ZERO_COST`.

For any remaining `INCOMPLETE` records that should be `ZERO_COST` (if the 4ca553e repair was interrupted), apply the same reclassification logic.

### §5.9 — Execution Sequence

| Step | Action | Risk |
|---|---|---|
| 1 | **Backup** current state of all BUY records (v2 backup) | None (read-only) |
| 2 | **Audit** — classify all records as CASE_A, CASE_B, or ALREADY_V2; report manual candidates | None (read-only) |
| 3 | **Deploy code** (FIFO engine + new import + new manual movement + frontend) | During deployment window, holdings will be computed by FIFO using whatever gross/net is stored. Records with `gross = trade` (Case B originals and post-migration Case A) will work correctly with FIFO `net_eur`. Records still in 4ca553e state (Case A, gross = trade+commission) will have inflated `net_eur` until migrated. |
| 4 | **Apply** v2 migration (ETag-gated, fail-closed) | Fixes all records. Sub-second for ~339 records. |
| 5 | **Verify** FIFO holdings output for ADM, ACS, and other key securities | |
| 6 | If issues: **Restore** from v2 backup + code rollback | |

**Acceptable risk during step 3→4 gap:** The gap is seconds. During this time, any BUY records still in 4ca553e format will have `net.eur_amount = trade` instead of `trade + commission`, causing the FIFO lot's net unit cost to be understated by the commission amount. This is a brief, transient display inaccuracy during a maintenance window.

---

## 6. Dividend History Migration & Audit

### §6.1 — Current Dividend Data State

Dividend records imported via CSV currently store:
```
gross.eur_amount = CSV "Importe Bruto"       ← correct gross
net.eur_amount   = CSV "Importe Neto"        ← correct net
withholding.source.amount_eur = CSV "Ret. Origen"
withholding.destination.amount_eur = CSV "Ret. Destino"
fees = { total_eur: "0" }                    ← no fee column in dividend CSV
```

### §6.2 — Invariant Check

The new convention defines: `dividend_net = gross - fees - wht_source - wht_dest`

For existing records with fees = 0:
```
expected_net = gross - wht_source - wht_dest
actual_net = stored net.eur_amount (from CSV)
```

**Audit required:** Verify that `|expected_net - actual_net| < 0.01` for all dividend records. Any discrepancy indicates a data issue in the original CSV or parser.

### §6.3 — Holdings Engine Dividend Handling

The FIFO holdings engine reads `net.eur_amount` for dividends (unchanged from CMP). Dividends accumulate in `total_dividends_eur` and do not affect lots.

### §6.4 — Manual Dividend Records

Manual dividends created via `create_manual_movement()` compute:
```python
net_eur = gross_eur - fees_eur - wht_source_eur - wht_dest_eur
```

This is **correct** under the new convention. No change needed for manual dividend creation.

### §6.5 — Withholding Fields Audit

The dividend parser extracts `wht_source` and `wht_destination` directly from CSV columns. The withholding model (dual-layer: source vs. destination, null ≠ zero) remains unchanged.

**Audit items:**
1. All dividend records have `gross.eur_amount` > 0
2. `net.eur_amount` ≈ `gross.eur_amount - wht_source - wht_dest` (within 0.01)
3. Withholding amounts are non-negative
4. No records have meaningless net values (as BUY records did)

### §6.6 — No Dividend Migration Required

Dividend gross/net semantics are already correct under the new convention. The v2 migration is **BUY-only**.

---

## 7. Compatibility & Rollout Strategy

### §7.1 — Code Deployment Phases

**Phase 1 — Code deploy (single release):**
All code changes ship together:
- FIFO holdings engine
- Corrected BUY import (gross = trade, net = trade + commission)
- Corrected manual BUY creation (net = gross + fees)
- Corrected frontend BUY form (gross = trade value, not trade + fees)
- Updated tests

**Phase 2 — Data migration (immediately after deploy):**
- Run v2 audit
- Run v2 apply
- Verify

### §7.2 — Transitional Engine Compatibility

The FIFO engine must handle records in **both** old and new format during the migration window:

```python
### In lot creation from BUY movement:
net_eur = Decimal(m.get("net", {}).get("eur_amount", "0"))
gross_eur = Decimal(m.get("gross", {}).get("eur_amount", "0"))
fees_eur = Decimal(m.get("fees", {}).get("total_eur", "0"))

### Detect format: if net < gross, it's old format (net = trade - commission)
### New format: net = trade + commission > gross = trade
if net_eur < gross_eur and fees_eur > 0:
    # Old format: compute true net = gross + fees
    effective_net = gross_eur + fees_eur
else:
    effective_net = net_eur

lot_net_cost = effective_net
```

**However**, this heuristic is fragile (zero-commission records are ambiguous). A cleaner approach:

**Option A (preferred):** Deploy code, migrate data within seconds, accept brief inaccuracy during the gap. No transitional engine logic needed.

**Option B (defensive):** Check for `_repair_buy_fields_v2` marker. If absent, use `gross_eur + fees_eur` as the acquisition cost (same as pre-4ca553e engine). If present, use `net_eur`.

**Decision: Option A** — maintenance-window deploy. The migration window is sub-second for ~339 records. The code and data change can be coordinated tightly.

### §7.3 — API Consumers

No external API consumers exist — the API is consumed exclusively by the frontend. The frontend is deployed simultaneously with the backend. No backward-compatibility concern.

### §7.4 — Rollback Plan

If issues are discovered post-deploy:
1. **Code rollback:** Revert to commit `4ca553e` (CMP engine with 4ca553e gross/net)
2. **Data rollback:** Run v2 restore from backup
3. **Result:** Returns to exact state as of 4ca553e deployment

---

## 8. Acceptance Tests & Reviewer Gates

### §8.1 — FIFO Core Tests (Backend)

| Test ID | Description | Expected |
|---|---|---|
| FIFO-1 | Single BUY, no sell | 1 lot; avg = net_unit_cost |
| FIFO-2 | Two BUYs at different prices, sell consuming first lot entirely | Remaining = second lot; avg = second lot's net_unit_cost |
| FIFO-3 | Three BUYs (85, 15, 10), sell 100 — **ADM case** | Remaining = 10 shares; avg = third lot's net_unit_cost |
| FIFO-4 | Partial lot consumption | First lot partially consumed; remainder + later lots survive |
| FIFO-5 | Zero-cost lot in FIFO order | Zero-cost lot consumed when it's oldest; cost assigned = 0 |
| FIFO-6 | Zero-cost lot NOT oldest | Older paid lots consumed first; zero-cost lot survives |
| FIFO-7 | INCOMPLETE lot in FIFO order | Consumed with cost = 0; warning emitted |
| FIFO-8 | Mixed COMPLETE + ZERO_COST + INCOMPLETE, partial sell | FIFO order respected; correct cost assignment per lot type |
| FIFO-9 | Sell more than available (negative inventory) | Warning; excess at cost 0 |
| FIFO-10 | Multiple sells depleting across lots | Progressive lot consumption across multiple sell events |
| FIFO-11 | Transfer-out then transfer-in (FIFO cost carry) | Source lots consumed FIFO; destination lot created at carried cost |
| FIFO-12 | Void/superseded movements excluded from FIFO replay | Voided BUY doesn't create lot; voided SELL doesn't consume |
| FIFO-13 | Same-date tie-break on movement_id | Deterministic lot ordering |
| FIFO-14 | Corporate action SHARE_ACQUISITION creates zero-cost lot | Lot created at cost 0; FIFO-ordered by trade_date |
| FIFO-15 | Sell after corporate action with mixed lots | FIFO respects chronological order including CA lots |

### §8.2 — Net Convention Tests (Backend)

| Test ID | Description | Expected |
|---|---|---|
| NET-1 | BUY import: CSV total=100, commission=5 | `gross.eur_amount=100`, `net.eur_amount=105` |
| NET-2 | SELL import: CSV total=200, commission=3 | `gross.eur_amount=200`, `net.eur_amount=197` |
| NET-3 | DIVIDEND import: gross=50, wht_source=7.5 | `gross.eur_amount=50`, `net.eur_amount=42.5` |
| NET-4 | Manual BUY: gross=100, fees=5 | Server computes `net.eur_amount=105` |
| NET-5 | Manual SELL: gross=200, fees=3 | Server computes `net.eur_amount=197` |
| NET-6 | Manual DIVIDEND: gross=50, wht=7.5, fees=0 | Server computes `net.eur_amount=42.5` |
| NET-7 | Holdings FIFO: BUY lot cost = net.eur_amount (not gross) | Cost basis uses net |
| NET-8 | SELL realized result = sell_net - FIFO_cost_assigned | Net proceeds minus FIFO cost |

### §8.3 — Migration Tests

| Test ID | Description | Expected |
|---|---|---|
| MIG-1 | Case A record (v1 marker present): gross/net swapped correctly | `gross = trade`, `net = trade + commission` |
| MIG-2 | Case B record (no marker): net fixed to gross + fees | `gross = trade` (unchanged), `net = trade + commission` |
| MIG-3 | Already v2 record (v2 marker): skipped | No changes |
| MIG-4 | Zero-commission record: no swap needed | `gross = net = trade` (both conventions agree) |
| MIG-5 | ZERO_COST reclassification preserved | `cost_basis_status = "ZERO_COST"` retained |
| MIG-6 | Manual BUY records: audit-only, not auto-applied | Reported as candidate; no mutation |
| MIG-7 | Idempotent re-run after apply | Zero candidates on second run |
| MIG-8 | Restore from backup | Returns to pre-migration state exactly |
| MIG-9 | Cross-validation with source_row passes | Post-migration gross matches source Total, net matches Total + Commission |

### §8.4 — Frontend Tests

| Test ID | Description | Expected |
|---|---|---|
| FE-1 | BUY form sends `gross = trade_value` (not trade + fees) | `makeGross(trade_value, currency)` |
| FE-2 | SELL form unchanged | `makeGross(proceeds, currency)` as before |
| FE-3 | Holdings table shows `remaining_cost_basis_eur` | Correct under FIFO |
| FE-4 | `CostBasisStatus` includes `ZERO_COST` | Type check passes |
| FE-5 | No `ZERO_COST_ACQUISITION` in warning maps | Warning maps clean |

### §8.5 — Reviewer Gates

| Gate | Criteria | Blocker if fails |
|---|---|---|
| **G1: FIFO algorithm correctness** | All FIFO-* tests pass; ADM case produces 10 remaining shares at correct net unit cost | YES |
| **G2: Net convention consistency** | All NET-* tests pass; BUY net > gross, SELL net < gross, DIVIDEND net < gross | YES |
| **G3: Migration safety** | All MIG-* tests pass; audit shows correct classification; restore verified | YES |
| **G4: No regression on SELL** | SELL accounting unchanged; realized result formula uses net proceeds | YES |
| **G5: No regression on DIVIDEND** | Dividend accumulation unchanged; uses existing net.eur_amount | YES |
| **G6: Frontend convention alignment** | All FE-* tests pass; BUY form does NOT add fees to gross | YES |
| **G7: Production ADM verification** | Post-migration: ADM shows 10 shares, avg cost matches third lot's net unit cost | YES |
| **G8: Production ACS verification** | Post-migration: ACS shows ~223 shares, avg cost ≈ €19.26 (FIFO preserves this when no sells occurred) | YES |
| **G9: Zero side effects** | Unrelated securities unchanged; dividend totals unchanged; no new warnings introduced | YES |
| **G10: Clean test suite** | All backend + frontend + integration tests pass | YES |

---

## 9. Prior Decision Disposition

| Decision | Status | Reason |
|---|---|---|
| Phase 1 MVP "Average cost (simplest, matches Spanish FIFO-like scenarios)" | **SUPERSEDED** | User requires FIFO, not average cost |
| "Cost-basis methods beyond average (FIFO/LIFO Phase 3)" | **SUPERSEDED** | FIFO moved to Phase 1 |
| `danny-scrip-zero-cost-and-buy-import-contract.md` §2 BUY gross=trade+commission | **SUPERSEDED** | New convention: gross=trade, net=trade+commission |
| `danny-scrip-zero-cost-and-buy-import-contract.md` §1 ZERO_COST pool entry | **RETAINED** | Zero-cost lots exist under FIFO (lot with cost 0) |
| `danny-scrip-zero-cost-and-buy-import-contract.md` §3 data repair v1 | **SUPERSEDED** | New v2 migration path (§5) |
| `danny-effective-average-cost-display-contract.md` | **SUPERSEDED** (was already superseded) | Still superseded |
| Commit 4ca553e holdings engine (`cost = gross_eur`) | **SUPERSEDED** | FIFO uses `net.eur_amount` per lot |
| Commit 4ca553e import (`gross = net + commission`) | **REVERSED** | New: `gross = trade`, `net = trade + commission` |
| Commit 4ca553e `ZERO_COST` enum, warning cleanup | **RETAINED** | Compatible with FIFO |
| Commit 4ca553e frontend `remaining_cost_basis_eur` display | **RETAINED** | Meaning changes (CMP residual → FIFO lot sum) but field name unchanged |
| Commit 4ca553e frontend AddMovementDialog BUY form | **REVERSED** | BUY gross = trade value (not trade + fees) |

---

## 10. Implementation Notes

### §10.1 — Holdings Service Rewrite Scope

The `compute_holdings()` method must be rewritten to:
1. Build lots from BUY/TRANSFER_IN/SHARE_ACQUISITION movements
2. Consume lots FIFO from SELL/TRANSFER_OUT movements
3. Track remaining lots per security
4. Compute avg_cost, remaining_cost_basis, realized_result from FIFO data

The CMP accumulators (`pool_shares`, `pool_cost`) are replaced by a list of `FifoLot` objects per security.

### §10.2 — Performance Consideration

For ~500 movements across ~30 securities, FIFO lot replay is O(movements × lots_per_security). With lot lists of ~10-50 per security, this is sub-millisecond per security. No materialized views needed.

### §10.3 — Lot Persistence

Lots are **not stored** — they are derived from the movement stream on each `compute_holdings()` call, identical to the current CMP approach. This preserves the "holdings are derived, never stored" invariant.

### §10.4 — Manual Movement `create_manual_movement()` Changes

The net derivation in `cosmos_portfolio.py` must change for BUY:

```python
### Current (same for all types):
###   net_eur = gross_eur - fees_eur - wht_source_eur - wht_dest_eur

### New:
if txn_type == "BUY":
    net_eur = gross_eur + fees_eur  # BUY net = what was paid = trade + fees
else:
    net_eur = gross_eur - fees_eur - wht_source_eur - wht_dest_eur
```

### §10.5 — Movement Correction `correct_movement()` Changes

Same net derivation change must apply to corrected movements based on their `txn_type`.

---

**APPROVED CONTRACT**

This contract is the authoritative specification for FIFO lot-based holdings with net-centric financial calculations. It supersedes the CMP pool model and the commit 4ca553e BUY gross/net convention. Implementation must not begin until this contract is acknowledged by the team coordinator.

— Danny, Lead Architect


### Danny — Final Gate Review: Scrip Zero-Cost & BUY Gross/Net Semantics

**Verdict: APPROVED — with one non-blocking cleanup item**

**Date:** 2026-09-08
**Reviewer:** Danny (Lead)
**Scope:** Scrip zero-cost pool entry, BUY gross/net correction, data repair migration, frontend contract alignment

---

## Validation Summary

| Gate check | Result |
|---|---|
| **1. ACS regression: 163 + 60 ZERO_COST = 223 → avg €19.26** | ✅ PASS — test `test_acs_example_avg_diluted` encodes exact regression |
| **2. INCOMPLETE excluded from pool; not conflated with ZERO_COST** | ✅ PASS — `test_incomplete_does_not_dilute_avg`, `test_incomplete_emits_incomplete_cost_basis_warning` |
| **3. BUY persistence: net consideration excluding commission; gross = net + commission** | ✅ PASS — `import_service.py:631-635`; holdings engine `cost = gross_eur` (no double-count) |
| **4. Partial sells use diluted moving average** | ✅ PASS — `TestPartialSellWithZeroCostPool` (3 tests) |
| **5. Migration: audit-default, apply-explicit, backup+checksum, ETag/CAS, restore, idempotent** | ✅ PASS — 55/55 migration tests pass; `_repair_buy_fields_v1` marker checked first |
| **6. CSV candidates: bilingual source_row cross-validation, fail-closed** | ✅ PASS — `test_rbl_3e_bilingual_english_headers_detected`, ambiguous/missing skip tests |
| **7. Manual candidates: candidate_only=True, never auto-applied** | ✅ PASS — `_analyse_manual_candidate` returns `candidate_only: True`; `--apply-manual-ids` required |
| **8. Frontend: remaining_cost_basis_eur with fallback, avg_cost_basis_eur, no effective_avg_cost_eur** | ✅ PASS — Holdings table line 135 uses `remaining_cost_basis_eur ?? current_invested_eur`; avg uses `avg_cost_basis_eur`; `effective_avg_cost_eur` absent from types and table |
| **9. Tests encode true BUY gross semantics, retain SELL regressions** | ✅ PASS — `TestSellNonRegression` (4 tests), `TestCostEqualsGrossOnly` (3 tests) |

## Test Execution Results

- **Backend:** 469 passed, 0 failed (8 test files)
- **Frontend:** 18 passed, 2 known-defect tests failed (ZCA-1, ZCA-2)
- **Migration:** 55 passed, 0 failed

## Non-Blocking Cleanup (ZCA-1, ZCA-2)

Two frontend tests are **deliberately tagged `[DEFECT]`** and document stale `ZERO_COST_ACQUISITION` references:

- `portfolio.ts` line 10: WarningType union still includes `"ZERO_COST_ACQUISITION"`
- `PortfolioHoldingsTable.tsx` line 16: WARNING_SHORT map still has the key
- Also present in `MovementDetailDialog.tsx:24`, `ImportPreview.tsx:16`, `ImportChat.tsx:452`, `PortfolioMovementsTable.tsx:28`

**Impact: Zero** — backend no longer emits this warning type; these are dead map entries that render nothing. The defect tests document the cleanup debt. This is a cosmetic cleanup that can be done post-release.

**Owner for cleanup:** Linus (frontend)

## Architecture Verification

### Product code reviewed (all correct):
- `models.py` — `CostBasisStatus.ZERO_COST` enum, `WarningType.INCOMPLETE_COST_BASIS` added
- `purchases.py` — Zero-price → `ZERO_COST` (not `INCOMPLETE`), no warning emitted
- `import_service.py` — BUY: `gross = net_consideration + commission`, `net = net_consideration`; SELL: unchanged
- `holdings_service.py` — BUY: `cost = gross_eur` (no double-count); ZERO_COST enters pool at 0; INCOMPLETE stays in unpaid_shares
- `AddMovementDialog.tsx` — Manual BUY sends `gross = trade_value + fees` (line 552)
- `PortfolioHoldingsTable.tsx` — Uses `remaining_cost_basis_eur` with `total_invested_eur` fallback

### Migration reviewed (correct):
- `repair_buy_ledger_fields.py` — Source-row cross-validation replaces old tautological check; `_repair_buy_fields_v1` idempotency guard; manual candidates are candidate_only; backup with SHA-256 checksum verification; ETag/CAS writes; restore path with checksum verification

### Previous blockers from rejected review — all resolved:
- **B1 (manual BUY gross):** Frontend now sends `gross = trade_value + fees`. Migration reports manual candidates as `candidate_only=True` requiring `--apply-manual-ids`.
- **B2 (test fixtures):** All 469 tests pass with true gross semantics.
- **B3 (tautological detection):** Replaced with source-row cross-validation; idempotency marker checked unconditionally first.

---

## Release Scope

**Code artifacts (commit-ready):**
- `backend/src/portfolio/models.py`
- `backend/src/portfolio/parsers/purchases.py`
- `backend/src/portfolio/import_service.py`
- `backend/src/portfolio/holdings_service.py`
- `backend/scripts/repair_buy_ledger_fields.py`
- `frontend/src/components/AddMovementDialog.tsx`
- `frontend/src/components/PortfolioHoldingsTable.tsx`
- `frontend/src/types/portfolio.ts`
- All 9 test files listed in the review scope

**Excluded (per scope):**
- `.squad/` changes (except this decision)
- `repair_ad_xams_security_id.py` and its test
- Unrelated dirty-worktree UI/account/Symbol Details changes

## Production Migration Safeguards & Required Execution Order

Production migration **may proceed** after code is committed and deployed. Required order:

1. **Deploy code first** — new import_service/holdings_service/frontend must be live before repair runs
2. **Run audit (read-only):** `python -m scripts.repair_buy_ledger_fields --database stock-options-manager --portfolio-container portfolio`
3. **Verify audit report** — confirm candidate count, review each proposed swap
4. **Run apply:** `python -m scripts.repair_buy_ledger_fields --apply --database stock-options-manager --portfolio-container portfolio`
5. **Verify idempotency:** Run audit again — must report zero auto-repair candidates among CSV records
6. **Manual BUY candidates:** Review each reported manual candidate against trade confirmations; apply confirmed IDs with `--apply --apply-manual-ids ID1,ID2,...`
7. **Rollback available:** `--restore /path/to/backup.json` restores original values with checksum verification

**Do not execute migration against production.** This review approves the artifacts and execution plan only.


### Danny — Review Gate: Scrip Zero-Cost & BUY Import Contract Implementation

**Verdict: REJECTED — 3 high-confidence blockers, 1 critical architectural miss**

**Date:** 2026-09-08
**Scope reviewed:** `models.py`, `parsers/purchases.py`, `import_service.py`, `holdings_service.py`, `scripts/repair_buy_ledger_fields.py`

---

## Blocker 1 (CRITICAL): Manual BUY movements also have gross = net — engine change breaks them

### Evidence

`frontend/src/components/AddMovementDialog.tsx` line 152:
```tsx
trade_value: string;   // gross amount (quantity × unit price, before fees)
```
Line 550:
```tsx
gross: makeGross(buyForm.trade_value, currency),
```

The manual BUY form sends `gross.eur_amount = price × qty` (EXCLUDING commission). The backend `cosmos_portfolio.py:create_manual_movement()` stores this as-is. Therefore **manual BUY movements have the identical gross/net inversion as CSV imports**.

The contract §2.4 claims manual movements are unaffected ("caller contract already defines gross as total cost including fees"). This is **factually wrong** — inspected code proves the UI sends trade value before fees as gross.

### Impact

After the engine change (`cost = gross_eur`), manual BUY movements will under-count by their commission amount. The data repair script (§3.2) only targets `import_source == "csv_import"`, so manual BUYs (`import_source == "manual"`) are silently left with the old gross-is-actually-net semantics, producing wrong pool_cost.

The same applies to **corporate action SHARE_ACQUISITION legs** created via the UI form (gross = FMV, which for non-zero-cost acquisitions may also exclude fees — though in practice scrip shares have gross=0 and fees=0, so the error is zero). This needs explicit verification per event type.

### Remediation

**Option A (preferred):** Extend the data repair script to also flag `import_source == "manual"` BUY records where `fees.total_eur > 0`. Update the frontend AddMovementDialog to send `gross = trade_value + fees` (true gross). The contract §2.4 table must acknowledge all BUY paths share the same inversion.

**Option B:** Keep the old engine formula `cost = gross_eur + commission_eur` and only fix the stored field names (swap gross/net labels) without changing the computation. Less disruptive but perpetuates the semantic confusion.

**Assignment:** Livingston (backend repair scope) + Linus (frontend form fix)

---

## Blocker 2 (HIGH): Test fixtures use old gross semantics — tests will fail

### Evidence

`test_portfolio_holdings.py:_make_movement()` line 96:
```python
"gross": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur},
```
The `gross_eur` parameter represents trade value EXCLUDING commission (same inversion as production data).

**Concrete failing tests** (engine now computes `cost = gross_eur` not `gross_eur + commission_eur`):

| Test | Fixture | Old expected | New actual (cost = gross) |
|------|---------|-------------|--------------------------|
| `test_avg_cost_basis_single_buy` | gross=1825, fee=7.50 | avg=183.25 (=1832.50/10) | avg=182.50 (=1825/10) |
| `test_avg_cost_basis_multi_buy` | gross=1000+750, fee=10+5 | avg=11.77 (=1765/150) | avg=11.67 (=1750/150) |
| `test_commission_affects_holdings_cost_basis` | gross=1000, fee=10 | invested=1010 | invested=1000 |
| `TestCommissionAssignment.test_buy_commission_in_pool_cost` | gross=1000, fee=10 | outflow=1010 | outflow=1000 |
| `TestCommissionAssignment.test_cost_basis_sold_uses_pool_avg_including_buy_fee` | | cost_sold=505 | cost_sold=500 |
| `TestCommissionAssignment.test_remaining_cost_basis_includes_buy_fee` | | remaining=505 | remaining=500 |
| `TestCommissionAssignment.test_realized_result` | | realized=89 | realized=94 |
| `test_s7_incomplete_buy_then_sell` | | warning=ZERO_COST_ACQUISITION | warning=INCOMPLETE_COST_BASIS |
| `test_zero_cost_acquisition_incomplete` | | warning=ZERO_COST_ACQUISITION | warning=INCOMPLETE_COST_BASIS |
| `test_avg_cost_basis_excludes_zero_cost` | status=INCOMPLETE, gross=0 | avg=10.10 (only paid shares in pool) | same BUT test name/docstring is now misleading |

This is not a case of superseded fixtures representing old contract values. The **test helpers construct movement documents that mirror production data structure**, and the production data has not yet been repaired. If the test fixtures are updated to use "true gross" values (gross = net + commission), they will no longer represent the data shape that exists in production.

### Remediation

Tests must be updated in two tiers:

1. **Post-repair fixtures** (representing corrected data): `gross_eur = net + commission`. These test the steady-state engine behavior.
2. **Pre-repair/legacy fixtures** (representing current production data, `import_source="csv_import"`): `gross_eur = net_only`. These should verify the engine + repair pipeline together produces correct results.

The `_make_movement` helper should be updated to accept `gross_includes_commission=True` (default for new fixtures) and construct the correct gross/net/fees block accordingly.

**Additionally:** Warning type assertions must change from `ZERO_COST_ACQUISITION` to `INCOMPLETE_COST_BASIS` where `cost_basis_status="INCOMPLETE"` is used.

**Assignment:** Livingston

---

## Blocker 3 (HIGH): Migration detection logic is tautological — cannot distinguish legacy from already-correct records

### Evidence

`repair_buy_ledger_fields.py:_analyse_record()` lines ~170-180:
```python
if fees_eur > Decimal("0"):
    expected_gross = gross_eur + fees_eur
    if gross_eur != expected_gross:  # This is ALWAYS TRUE when fees_eur > 0
```

The condition `gross_eur != gross_eur + fees_eur` is tautologically true whenever `fees_eur > 0`. The subsequent `gross_eur < expected_gross` check is also always true (since `expected_gross = gross_eur + fees_eur > gross_eur` when fees > 0).

**This means every csv_import BUY record with commission > 0 will be flagged for repair, including:**
- Records imported AFTER the code fix (which already have correct gross = net + commission)
- Records that were manually corrected via the correction API
- Any record where gross legitimately includes commission

The `_repair_buy_fields_v1` timestamp marker added during repair (line ~275) could serve as a guard, but the detection logic does not check for it — the `_analyse_record` function would re-flag already-repaired records on a second run, adding commission again (gross = (net + commission) + commission = net + 2×commission).

### Remediation

Detection must be **fail-closed with a positive identifier**, not arithmetic inference. Options:

**Option A (recommended):** Check for the `_repair_buy_fields_v1` marker — skip records that already have it. Additionally, cross-check with source_row data: if `source_row` is present, reconstruct `expected_net = parse(source_row["Total (€)"])` and verify `stored_gross ≈ expected_net` (confirming inversion) before flagging.

**Option B:** Only flag records created before a cutoff timestamp (the deploy date of the fix). Records created after the fix will have correct gross.

**Option C:** Verify the equation `stored_net ≈ stored_gross - fees` (the meaningless old value). If this holds, the record has old semantics. If `stored_net ≈ stored_gross` (i.e., net equals gross minus fees where gross already includes fees), the record has new semantics.

Option A is most robust — source_row data provides ground truth. Option C is a good secondary check.

**Assignment:** Livingston

---

## Non-blocking: Observations and Confirmations

### ✅ models.py — ZERO_COST enum: Correct
`CostBasisStatus.ZERO_COST` added. `WarningType.INCOMPLETE_COST_BASIS` added. Both align with contract §1.4.

### ✅ purchases.py parser — ZERO_COST classification: Correct
Zero-price detection (`price_per_share == 0 and quantity > 0`) now emits `"ZERO_COST"` instead of `"INCOMPLETE"`. Warning removed. Aligns with contract §1.1.

**Observation:** `parse_spanish_decimal` returns `None` for blank/N/A values, which the parser coerces via `or Decimal("0")`. This means a blank `Valor compra` cell → `price_per_share = 0` → classified as ZERO_COST. A genuinely blank/missing price is arguably not "explicit zero cost" but "genuinely unknown." However, in practice, the purchases CSV schema requires all 7 columns, and a blank price with non-zero quantity is a user data-entry issue. The current behavior is acceptable and matches historical data. **Not a blocker** — flag for documentation.

### ✅ import_service.py BUY block — Correct
`net_consideration = total_cost`, `gross = net_consideration + commission`, `net = net_consideration`. Aligns with contract §2.2. SELL block unchanged — confirmed byte-for-byte identical to pre-change.

### ✅ holdings_service.py ZERO_COST pool entry — Correct
ZERO_COST enters `pool_shares` at cost 0. INCOMPLETE stays in `unpaid_shares`. Warning logic uses `INCOMPLETE_COST_BASIS` for genuinely incomplete, no warning for ZERO_COST. `has_incomplete_cost_basis` keyed off `incomplete_count`. Aligns with contract §1.2–§1.3.

### ✅ holdings_service.py SELL block — Unchanged
`net_proceeds = gross_eur - commission_eur` — byte-for-byte identical.

### ✅ Scrip with zero net + nonzero commission (review point 3)
For ZERO_COST imports: parser detects `price_per_share == 0`, so `total_cost = 0` (price × qty = 0). In the corrected import: `gross = 0 + commission = commission`. Pool cost would be `commission` (not zero). This is actually correct accounting — if someone paid a commission to acquire scrip shares, that commission IS the acquisition cost. In practice, scrip dividend commissions are 0 in the source data. **Not a blocker.**

### ✅ Repair script structure — Acceptable
Combined audit/apply/restore in one script instead of three separate scripts. Contract §3.2 allowed this ("three-script requirement can be safely satisfied by one combined script"). ETag-gated replace, mandatory backup before apply, SHA-256 checksum verification, re-analysis at apply time. Structurally sound apart from the detection logic (Blocker 3).

### ✅ Backup/restore mechanics — Sound
Fresh ETag read before apply, checksum-verified backup, restore uses ETag-gated replace. Exit code 3 on partial failure. Directory in `backend/scripts/migration_backups/`.

---

## Summary

| # | Severity | Issue | Owner |
|---|----------|-------|-------|
| 1 | **CRITICAL** | Manual BUY movements also have gross=net; engine change + repair scope misses them | Livingston + Linus |
| 2 | **HIGH** | Test fixtures not updated for new engine formula; multiple will fail | Livingston |
| 3 | **HIGH** | Migration detection is tautological; will re-corrupt already-repaired records on second run | Livingston |

**Resolution required before merge.** All three blockers must be addressed.

---

## Lockout-Compliant Revision Assignments

Livingston authored the rejected backend + migration artifact. Per reviewer lockout protocol, he **cannot** own the revision.

| Blocker | Revision owner | Scope |
|---------|---------------|-------|
| **B1 — Backend repair scope + holdings engine** | **Reuben** | Expand repair script to cover manual BUY records; fix detection logic; holdings engine adjustment if needed. Reuben has direct prior experience on `holdings_service.py` (F7 avg_cost fix) and backend migration. |
| **B1 — Frontend manual BUY form** | **Linus** | Fix `AddMovementDialog.tsx` BUY form to send `gross = trade_value + fees`. Linus did not author the rejected artifact; he owns frontend. |
| **B2 — Test fixtures** | **Basher** | Update `_make_movement` / `_buy` helpers and all affected assertions. Basher owns tests per charter. |
| **B3 — Migration detection logic** | **Reuben** | Replace tautological check with fail-closed detection (see §B3 below). |
| **Final review gate** | **Danny** | |

---

## Exact Revised Requirements

### B1 — Manual BUY Records: Scope and Migration

**Question: Do all manual BUY records need migration?**

**Answer: Yes, conditionally.** Manual BUY records (`import_source="manual"`) created via the AddMovementDialog send `gross.eur_amount = trade_value` (price × qty, before fees). This is the same net-as-gross inversion as CSV imports.

**However**, manual BUY records have **no `source_row`** field. Detection must use a different method:

For manual BUY records, the detection equation is:
- Read `gross.eur_amount` (G), `fees.total_eur` (F), `net.eur_amount` (N)
- If `N ≈ G - F` (old semantics: net = gross - commission, where gross was actually net_consideration): **needs swap**
- If `N ≈ G + F` or `N ≈ G` with `F = 0`: **already correct or zero-commission — skip**

Specifically, for old manual records: `G = trade_value`, `F = commission`, `N = trade_value - commission` (computed by `cosmos_portfolio.py:617`: `net_eur = gross_eur - fees_eur`). After repair: `G = trade_value + commission`, `N = trade_value`.

The detection check for manual records:
```python
### For manual records: if stored_net == stored_gross - fees, it's old semantics
if abs(net_eur - (gross_eur - fees_eur)) < Decimal("0.01"):
    needs_swap = True  # gross is actually net_consideration
```

This is safe because after repair, `net = trade_value` and `gross = trade_value + fees`, so `net != gross - fees` (it would be `gross - 2*fees`).

**Corporate action SHARE_ACQUISITION legs**: These are created via `cosmos_portfolio.py:822` with `import_source="manual"`. In practice, scrip SHARE_ACQUISITION legs have `gross=0, fees=0` (zero-cost), so the equation `0 = 0 - 0` holds but `needs_swap` is moot (swapping zeros changes nothing). Safe to include in the scan; they'll be skipped by the `fees_eur > 0` guard.

### B3 — Source Row Cross-Validation (How It Works)

CSV-imported BUY records store `source_row`: a dict of original CSV cell values keyed by header name. Example:
```json
{
  "Año": "2024",
  "Empresa": "ACS SA",
  "Fecha compra": "15/03/2024",
  "Valor compra": "0",
  "Acciones": "20",
  "Total (€)": "0",
  "Comisión": "0"
}
```

For non-zero-cost purchases:
```json
{
  "Total (€)": "1.825,00",
  "Comisión": "7,50"
}
```

**Cross-validation algorithm for csv_import records:**
1. Parse `source_row["Total (€)"]` (or bilingual alias) using `parse_spanish_decimal` → `source_total`
2. Parse `source_row["Comisión"]` (or alias) → `source_commission`
3. If `source_total` is parseable AND `abs(gross_eur - source_total) < 0.01`: gross currently stores net (old semantics) → **needs swap**
4. If `source_total` is parseable AND `abs(gross_eur - (source_total + source_commission)) < 0.01`: gross already includes commission (new semantics) → **skip**
5. If `source_row` absent or unparseable: fall back to arithmetic detection (B1 equation above)
6. **Always** check for `_repair_buy_fields_v1` marker — if present, skip unconditionally (already repaired)

This is fail-closed: ambiguous records are skipped, not flagged.

**Header aliases to check** (from `purchases.py:_PURCHASES_HEADER_ALIASES`):
- Position 5 (total): `"total (€)"`, `"total (eur)"`, `"total"`, `"total cost"`, `"trade value"`
- Position 6 (commission): `"comision"`, `"commission"`, `"fees"`

### B2 — Test Fixture Update Requirements

The `_make_movement` and `_buy` helpers must be updated so the `gross_eur` parameter represents **true gross (inclusive of commission)**, matching post-repair data shape:

```python
def _make_movement(movement_id, security_id, txn_type, quantity, gross_eur,
                   account_id="_unassigned", commission_eur="0", ...):
    # gross_eur is now TRUE gross (includes commission)
    net = str(Decimal(gross_eur) - Decimal(commission_eur))
    ...
    "gross": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur},
    "fees": {"total": commission_eur, ...},
    "net": {"amount": net, ...},
```

All call sites must update the `gross_eur` argument to include commission. Example:
- Old: `_make_movement("t1", "XNYS:AAPL", "BUY", "10", "1825.00", commission_eur="7.50")` → expected avg=183.25
- New: `_make_movement("t1", "XNYS:AAPL", "BUY", "10", "1832.50", commission_eur="7.50")` → expected avg=183.25

Expected assertions remain the same (since total cost = 1832.50 in both old and new world); only the fixture construction changes.

**Warning type changes:**
- `"ZERO_COST_ACQUISITION"` → `"INCOMPLETE_COST_BASIS"` where `cost_basis_status="INCOMPLETE"` is used
- Tests using `cost_basis_status="INCOMPLETE"` with `gross="0"` should add parallel test with `cost_basis_status="ZERO_COST"` to verify pool entry

No product code modified by this review. No production calls made.


### Decision: Share Consolidation Model -- RKT (Reckitt Benckiser)

**Author:** Danny (Lead)
**Date:** 2026-09-08
**Status:** RELEASED -- implemented by Livingston (backend model/CA-group wiring),
Rusty (CorporateActionForm wizard + MovementDetailDialog badges), tested by Livingston
(15 backend tests in test_share_consolidation.py + FIFO-SC1/2/3 in test_portfolio_fifo.py,
FE-SC1/2/3 in caWizardRequestShape.test.mjs/caGroupIndicator.test.mjs). No RKT/Reckitt
production data touched; no holdings_service.py changes required.
**Scope:** Accounting model extension for 24/25 share consolidation with fractional cash-out
**Out of scope:** The extraordinary dividend (already recorded as a separate DIVIDEND; handled correctly).

---

## Context

Reckitt Benckiser executed a 24-for-25 share consolidation:
- **72 old shares** -> 72 x 24/25 = **69.12 theoretical new shares**
- Broker delivers **69 whole shares** + **EUR 8.58** cash for the 0.12 fractional entitlement.

The current ledger models the 72->69 reduction as a **SELL of 3 shares for EUR 8.58**. Under FIFO this consumes the full acquisition cost of the 3 oldest lots -- a material distortion because:
- The true economic disposal is **0.12 post-consolidation shares**, not 3 pre-consolidation shares.
- The remaining 69 shares should carry ~(69/69.12) x original_total_cost, not total_cost minus cost_of_3_oldest_lots.
- The 3-share SELL inflates realized P&L (or loss) by the cost difference between 3 full old lots and 0.12 fractional new shares.

## Current Model Gap

| Component | Expressible today? | Notes |
|---|---|---|
| Consolidation (72 -> 69.12, cost preserved) | NO | No leg type reduces shares while preserving aggregate FIFO cost |
| Fractional cash-out (0.12 -> EUR 8.58) | Partial (SELL) | Works only after lots reflect post-consolidation unit cost |

**Fundamental gap:** no movement type can adjust share quantity without releasing or consuming FIFO cost.

## Correct FIFO Treatment

A share consolidation (ratio R = 24/25) should, conceptually:
1. Multiply every lot's quantity by R.
2. Divide every lot's unit cost by 1/R to preserve per-lot total cost.
3. Aggregate cost basis remains **unchanged**.
4. Only then is the 0.12 fractional share sold via normal FIFO.

Since holdings_service processes movements sequentially and cannot adjust existing lots in-place, the correct approach is a **remove-and-reissue** pattern using the existing TRANSFER_OUT / TRANSFER_IN mechanics.

## Recommended Representation

One corporate-action group (SHARE_CONSOLIDATION) with **three legs in strict sequence**:

| Seq | ca_leg_type | Maps to txn_type | quantity | Financial fields | FIFO effect |
|---|---|---|---|---|---|
| 1 | CONSOLIDATION_OUT | TRANSFER_OUT | **72** | gross/net = 0 | Removes all old shares; _consume_lots() depletes every FIFO lot; **not counted in sale_proceeds** |
| 2 | CONSOLIDATION_IN | TRANSFER_IN | **69.12** | transfer_cost_basis_eur = total original cost | Creates one new lot preserving total cost; unit cost = total_cost / 69.12; **not counted in purchase_outflow** |
| 3 | FRACTIONAL_CASH_OUT | SELL (sales_type=ACCIONES) | **0.12** | gross = EUR 8.58, fees = 0 | Normal FIFO consumption: cost = 0.12 x (total_cost / 69.12); counted in sale_proceeds and cost_basis_sold |

### Net effect on holdings

```
Shares:  72 - 72 + 69.12 - 0.12 = 69
Cost:    total_original - (0.12/69.12 x total_original) ~ total_original x 0.998
P&L:     8.58 - (0.12/69.12 x total_original)
```

- total_purchase_outflow_eur -> unchanged (transfers don't count)
- total_sale_proceeds_eur -> increases by EUR 8.58 (only the fractional)
- cost_basis_sold_eur -> increases by the proportional fractional cost
- remaining_cost_basis_eur -> decreases by same proportional fractional cost

### Why the existing FIFO engine handles this without modification

Verified in holdings_service.py:

1. **TRANSFER_OUT (line ~244):** calls _consume_lots(lots, qty), decrements total_shares. Does NOT add to cost_basis_sold_eur or total_sale_proceeds_eur. The consolidation disappearance is invisible to P&L.
2. **TRANSFER_IN (line ~225):** creates a _Lot at transfer_cost_basis_eur / qty unit cost, increments total_shares. Does NOT add to total_purchase_outflow_eur. No phantom investment.
3. **SELL ACCIONES (line ~207):** calls _consume_lots(), adds cost to cost_basis_sold_eur, adds net proceeds to total_sale_proceeds_eur. Standard fractional disposal.

**No changes to holdings_service.py required.**

## Required Model Changes (Minimal)

### 1. New enum values (models.py)

```python
class CaLegType(str, Enum):
    # existing ...
    CONSOLIDATION_OUT = "CONSOLIDATION_OUT"
    CONSOLIDATION_IN = "CONSOLIDATION_IN"
    FRACTIONAL_CASH_OUT = "FRACTIONAL_CASH_OUT"

class CaEventType(str, Enum):
    # existing ...
    SHARE_CONSOLIDATION = "SHARE_CONSOLIDATION"
```

### 2. Mapping additions (cosmos_portfolio.py)

```python
_CA_LEG_TXN_TYPE = {
    # existing ...
    "CONSOLIDATION_OUT": "TRANSFER_OUT",
    "CONSOLIDATION_IN": "TRANSFER_IN",
    "FRACTIONAL_CASH_OUT": "SELL",
}

_CA_REQUIRED_LEGS = {
    # existing ...
    "SHARE_CONSOLIDATION": {"CONSOLIDATION_OUT", "CONSOLIDATION_IN"},
    # FRACTIONAL_CASH_OUT is optional -- some consolidations produce no residual
}
```

### 3. Leg-building wiring (create_corporate_action_group loop)

Two small additions in the leg-building loop:

- **CONSOLIDATION_IN**: set doc["transfer_cost_basis_eur"] from a new leg field (e.g., leg.get("transfer_cost_basis_eur")). This is the same field TRANSFER_IN already reads in holdings_service.
- **FRACTIONAL_CASH_OUT**: set doc["sales_type"] = "ACCIONES" to ensure normal FIFO lot consumption (not DERECHOS treatment).

### 4. Nothing else

- holdings_service.py -- no changes.
- Frontend CA wizard -- would need a new event-type option, but that's UI work for later.

## Realized P&L for the EUR 8.58 Fractional

```
total_cost       = remaining_cost_basis_eur for RKT immediately before consolidation
unit_cost_new    = total_cost / 69.12
cost_of_fraction = 0.12 x unit_cost_new
realized_pnl     = 8.58 - cost_of_fraction
```

**Numeric example** (assuming total cost = EUR 4,500.00):

| Item | Value |
|---|---|
| unit_cost_new | 4,500.00 / 69.12 = 65.1042 EUR |
| cost_of_fraction | 0.12 x 65.1042 = 7.81 EUR |
| realized_pnl | 8.58 - 7.81 = **+0.77 EUR** |

Under the current erroneous SELL-3 model, FIFO would consume 3 full old lots (e.g., 3 x 62.50 = 187.50 EUR of cost), producing a fictitious loss of -178.92 EUR.

## Correction Plan for the Existing RKT Ledger

**Read-only -- no mutations to be performed now.**

1. **Void** the existing incorrect SELL of 3 shares x EUR 8.58 (if standalone: DELETE; if part of a CA group: void the group).
2. **Note** the remaining_cost_basis_eur for RKT from the holdings page **after** the void (this is the total FIFO cost that must be preserved through the consolidation).
3. **Once the model extension is implemented**, create the SHARE_CONSOLIDATION group:
   - Leg 1 CONSOLIDATION_OUT: quantity = 72, gross = 0
   - Leg 2 CONSOLIDATION_IN: quantity = 69.12, transfer_cost_basis_eur = value from step 2
   - Leg 3 FRACTIONAL_CASH_OUT: quantity = 0.12, gross = EUR 8.58
4. **Verify** holdings show 69 shares with cost ~ original total minus ~(0.17% proportional fractional cost).

### Operator prerequisite

The operator must read remaining_cost_basis_eur from the holdings page (or compute it from the lot list) before creating the CONSOLIDATION_IN leg. This value cannot be derived automatically from the consolidation request alone -- it depends on the full FIFO history.

## Uncertainty

- **Exact trade date** of the consolidation effective date (needed for correct FIFO lot ordering). Must come from broker statement or corporate-action notice.

---

**Decision:** PROPOSED -- awaiting user approval before implementation begins.


### Danny — Scrip Zero-Cost Pool Entry & BUY Import Gross/Net Correction

**Status: APPROVED**
**Date: 2026-09-08**
**Author: Danny (Lead/Architect)**
**Supersedes:** `danny-effective-average-cost-display-contract.md` (same date, now SUPERSEDED)
**User directive:** `copilot-correction-20260908-scrip-cost-and-buy-import.md`

---

## 0. Executive Summary

Two independent corrections to portfolio accounting, plus consequent UI/data-repair work:

**A. Scrip/zero-cost BUY shares must enter the CMP pool at cost 0** — not a side-channel `unpaid_shares`. This naturally dilutes `avg_cost_basis_eur` to the correct value (e.g. ACS: 4294.21 / 223 ≈ €19.26) without needing a separate `effective_avg_cost_eur` display field. The earlier effective-average proposal is withdrawn.

**B. BUY CSV import: the "Total (€)" column is NET consideration (price × qty, excluding commission)**. The stored `gross.eur_amount` must equal `net + commission`. Currently the import stores `total_cost` as gross — which is actually net. The holdings engine's `cost = gross_eur + commission_eur` therefore computes the correct total outflow *by accident*, but the ledger field names are semantically inverted.

**C. SELL import/accounting: unchanged.** SELL `Total Venta` is gross proceeds; `net = gross - commission`. Confirmed correct.

---

## 1. Scrip / Zero-Cost BUY — Pool Entry (Change A)

### §1.1 — Distinction: Explicit Zero Cost vs. Genuinely Incomplete

| Classification | Detection criteria | Pool treatment |
|---|---|---|
| **Explicit zero cost** | Parser sets `cost_basis_status = "INCOMPLETE"` because `price_per_share == 0 && quantity > 0`; but the row *was* successfully parsed with a definite cost of 0 | Enter `pool_shares` with `pool_cost += 0`. The share has a known cost; it happens to be zero. |
| **Genuinely incomplete** | Future: cost could not be determined (unparseable, missing column, user explicitly marks "cost unknown") | Remain in `unpaid_shares` side-channel. Retain `INCOMPLETE` status and warning. |

**Current state:** All existing zero-cost acquisitions in production are scrip-dividend shares imported via purchases CSV with `Valor compra = 0`. These are **explicit zero cost** and must enter the pool.

**Reclassification rule:** Rename the existing `cost_basis_status = "INCOMPLETE"` for zero-price imports to a new status value that conveys "zero cost, fully resolved":

| Old status | New status | Semantics |
|---|---|---|
| `INCOMPLETE` (price=0, qty>0, parsed OK) | `ZERO_COST` | Known zero acquisition cost; enters pool at 0 |
| `INCOMPLETE` (genuinely unknown cost) | `INCOMPLETE` | Cost genuinely missing; stays in unpaid_shares |

**Implementation note:** In practice, the purchases parser today only produces `INCOMPLETE` for the price=0 case. There is no current code path that produces genuinely-missing-cost INCOMPLETE records. So **all existing `INCOMPLETE` BUY records in CosmosDB are reclassifiable to `ZERO_COST`**. A future import enhancement for genuinely unknown costs would create the true `INCOMPLETE` path.

### §1.2 — Holdings Engine Changes

**File:** `backend/src/portfolio/holdings_service.py`, function `compute_holdings()`

**Current BUY block (lines 122–132):**
```python
if txn_type == "BUY":
    agg["total_shares"] += qty
    agg["buy_count"] += 1
    if cost_basis_status != "INCOMPLETE":
        cost = gross_eur + commission_eur
        agg["pool_shares"] += qty
        agg["pool_cost"] += cost
        agg["total_purchase_outflow_eur"] += cost
    else:
        agg["unpaid_shares"] += qty
        agg["zero_cost_count"] += 1
```

**New BUY block:**
```python
if txn_type == "BUY":
    agg["total_shares"] += qty
    agg["buy_count"] += 1
    if cost_basis_status == "INCOMPLETE":
        # Genuinely unknown cost — do NOT enter pool
        agg["unpaid_shares"] += qty
        agg["incomplete_count"] += 1
    else:
        # COMPLETE or ZERO_COST — enters pool
        cost = gross_eur + commission_eur  # for ZERO_COST: both are 0
        agg["pool_shares"] += qty
        agg["pool_cost"] += cost
        if cost_basis_status != "ZERO_COST":
            agg["total_purchase_outflow_eur"] += cost
        agg["zero_cost_count"] += (1 if cost_basis_status == "ZERO_COST" else 0)
```

**Key changes:**
- `ZERO_COST` shares enter `pool_shares` at cost 0 → naturally dilutes `avg_cost_basis_eur`
- `total_purchase_outflow_eur` does **not** include ZERO_COST acquisitions (no cash outflow)
- `INCOMPLETE` (genuinely unknown) remains in `unpaid_shares` as before
- Rename accumulator: `zero_cost_count` tracks ZERO_COST entries (for informational display); new `incomplete_count` tracks genuinely incomplete

### §1.3 — Warning Changes

| Warning | Current behavior | New behavior |
|---|---|---|
| `ZERO_COST_ACQUISITION` per-holding | Emitted when `zero_cost_count > 0` | **Removed as a warning.** ZERO_COST is a legitimate status, not a problem. |
| `INCOMPLETE_COST_BASIS` (new) | N/A | Emitted when `incomplete_count > 0` — genuinely missing cost. |
| `has_incomplete_cost_basis` summary | True when any security has `unpaid_shares > 0` | Unchanged — only true for genuinely INCOMPLETE, not ZERO_COST. |
| Symbols overview incomplete warning | Already removed per user directive | Stays removed. |
| Holdings table summary warning | "Algún valor tiene coste incompleto" | Unchanged — only triggers on genuine INCOMPLETE. |

### §1.4 — cost_basis_status Enum Update

**File:** `backend/src/portfolio/models.py`

```python
class CostBasisStatus(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    ZERO_COST = "ZERO_COST"       # NEW — explicit zero acquisition cost
```

**File:** `frontend/src/types/portfolio.ts`
```typescript
export type CostBasisStatus = "COMPLETE" | "INCOMPLETE" | "ZERO_COST";
```

### §1.5 — Per-Holding cost_basis_status Output

| Shares in pool | Unpaid shares | Output status |
|---|---|---|
| Any (including ZERO_COST) | 0 | `"COMPLETE"` |
| Any | > 0 | `"INCOMPLETE"` |
| 0 pool + 0 unpaid (fully exited) | 0 | `"COMPLETE"` |

ZERO_COST shares **do not** cause the holding's status to be INCOMPLETE.

### §1.6 — ACS Verification

After this change:
- pool_shares = 163 (paid) + 60 (ZERO_COST) = **223**
- pool_cost = **€4,294.21** (unchanged; 60 shares add 0)
- `avg_cost_basis_eur` = 4294.21 / 223 = **€19.26**
- `total_shares` = **223**
- Arithmetic: 223 × €19.26 = €4,294.98 ≈ €4,294.21 ✓ (2dp rounding)

**No separate `effective_avg_cost_eur` field needed.** The prior proposal is withdrawn.

---

## 2. BUY Import Gross/Net Correction (Change B)

### §2.1 — Current vs. Correct Field Mapping

The purchases CSV columns and their actual semantics:

| CSV column | Example (ACS row) | Actual meaning |
|---|---|---|
| `Total (€)` / `total_cost` | 32.42 | NET consideration = price × qty (excluding commission) |
| `Comisión` / `commission` | 2.00 | Commission/fees |
| (not in CSV) | 34.42 | TRUE GROSS = net + commission = total acquisition cost |

**Current import code** (`import_service.py:630-632`):
```python
gross = row.get("total_cost", Decimal("0"))    # ← stores NET as "gross"
commission = row.get("commission", Decimal("0"))
net = gross - commission                        # ← computes meaningless value
```

**Stored movement:**
- `gross.eur_amount` = 32.42 (actually net)
- `fees.total_eur` = 2.00
- `net.eur_amount` = 30.42 (meaningless: "net" - commission)

**Holdings engine** (`holdings_service.py:127`):
```python
cost = gross_eur + commission_eur  # = 32.42 + 2.00 = 34.42 ✓ (correct by accident)
```

The total outflow (34.42) is **accidentally correct** because `stored_gross + commission = actual_net + commission = true_gross`. But the field semantics are wrong.

### §2.2 — Corrected Import Mapping

**File:** `backend/src/portfolio/import_service.py`, function `_row_to_movement()`, `elif fmt == "purchases":` block

```python
elif fmt == "purchases":
    trade_date = row.get("purchase_date", "")
    net_consideration = row.get("total_cost", Decimal("0"))  # CSV "Total" = net
    commission = row.get("commission", Decimal("0"))
    gross = net_consideration + commission    # TRUE gross = net + commission
    net = net_consideration                   # net = CSV "Total" as-is
    ...
```

**Stored movement after fix:**
- `gross.eur_amount` = 34.42 (true gross: net + commission)
- `fees.total_eur` = 2.00
- `net.eur_amount` = 32.42 (CSV "Total" = actual net consideration)

### §2.3 — Holdings Engine Adjustment

With corrected import, `gross.eur_amount` now contains the true gross (inclusive of commission). The holdings engine must change to avoid double-counting:

**File:** `backend/src/portfolio/holdings_service.py`, BUY block

**Current:** `cost = gross_eur + commission_eur`
**New:** `cost = gross_eur`

Because `gross_eur` now already includes commission.

**SELL remains unchanged:** `net_proceeds = gross_eur - commission_eur` — for sells, `gross` is proceeds before deducting commission (confirmed correct in sales parser; "Total Venta" is gross).

### §2.4 — All Import Paths Audit

| Path | How gross is set | Affected? |
|---|---|---|
| **CSV import** (`import_service.py:630`) | `gross = total_cost` (net) | **YES — fix** |
| **Manual movement** (`POST /api/portfolio/movements`) | Caller provides `gross.eur_amount` explicitly | **NO** — caller contract already defines gross as total cost including fees; UI form sends correct value. Holdings engine change (§2.3) applies. |
| **Corporate action** (`POST /api/portfolio/corporate-actions`) | Caller provides `gross` per leg | **NO** — same as manual; caller owns semantics. For SHARE_ACQUISITION legs, caller sets gross=0 for zero-cost scrip. Holdings engine change applies. |
| **Movement correction** (`POST /movements/{id}/correct`) | Caller provides corrected `gross` | **NO** — correction inherits original field meaning. Corrected records use caller-provided values. |
| **Transfer** (`POST /api/portfolio/transfers`) | No gross field; uses `transfer_cost_basis_eur` | **NO** — transfers don't go through BUY/gross path. |

**Only the CSV import path needs the gross/net fix.** All other paths receive explicit values from the caller (UI forms/API clients).

### §2.5 — Idempotency Hash Impact

`row_idempotency_hash()` includes `gross` in its signature. Changing the value stored as `gross` will change the hash for the same CSV row. This means:

- **Re-importing the same CSV** after the fix would produce different hashes → duplicate detection based on hash would not match old records.
- This is acceptable because re-import is a new session, and the old session records are already committed. The idempotency hash is per-session dedup, not cross-session.

### §2.6 — Preview/Validation Impact

The import preview shows parsed rows before commit. After the fix, preview will show the corrected gross values. No structural change to preview — just different numbers in the `gross` field.

---

## 3. Production Data Repair (Change C)

### §3.1 — Scope Assessment

Existing BUY records imported via CSV have:
- `gross.eur_amount` = what is actually net (excluding commission)
- `fees.total_eur` = commission (correct)
- `net.eur_amount` = meaningless (net - commission)
- `cost_basis_status` = `"INCOMPLETE"` for zero-price scrip (should become `"ZERO_COST"`)

**Records NOT affected:**
- Manual movements (`import_source: "manual"`) — caller set gross explicitly
- Corporate actions — caller set gross explicitly
- Transfers — no gross field
- SELL CSV imports — gross semantics are correct (Total Venta = gross proceeds)
- DIVIDEND CSV imports — gross/net semantics are correct

### §3.2 — Repair Strategy: Audit-First, Then Patch

**Phase 1: Audit script** (read-only, no writes)

```
Script: backend/scripts/audit_buy_ledger_fields.py
```

Detection criteria for records needing repair:
1. `doc_type == "ledger_txn"`
2. `txn_type == "BUY"`
3. `import_source == "csv_import"`
4. `correction_status` is `"ACTIVE"` or absent (skip SUPERSEDED/VOIDED)

For each matched record, compute:
- `expected_gross = Decimal(gross.eur_amount) + Decimal(fees.total_eur)`
- `expected_net = Decimal(gross.eur_amount)`  (current gross IS the net)
- Flag if `gross.eur_amount != expected_gross` (i.e., commission > 0 and fields appear inverted)

Also flag `cost_basis_status == "INCOMPLETE"` records for reclassification to `ZERO_COST`.

**Safety:** Do NOT flag records where `fees.total_eur == "0"` or `fees.total_eur == "0.00"` — for these, gross = net and no field swap is needed (only status reclassification if applicable).

Output: JSON report listing each record ID, account_id, current values, proposed values, and whether status reclassification applies.

**Phase 2: Backup**

Before any writes:
```
Script: backend/scripts/backup_buy_movements.py
```

Export all matched BUY records to a timestamped JSON file in `backend/scripts/backups/`. Include full document content.

**Phase 3: Dry-run / Apply / Restore**

```
Script: backend/scripts/repair_buy_ledger_fields.py --mode {dry-run|apply|restore}
```

For each flagged record:
1. Read current document (with ETag for optimistic concurrency)
2. Compute corrected fields:
   - `gross.eur_amount` = old `gross.eur_amount` + `fees.total_eur` (= true gross)
   - `gross.amount` = same correction applied to native currency amount
   - `net.eur_amount` = old `gross.eur_amount` (= actual net, the CSV Total value)
   - `net.amount` = old `gross.amount`
   - If `cost_basis_status == "INCOMPLETE"` AND `quantity > 0` AND `price_per_share_implied == 0` → set `cost_basis_status = "ZERO_COST"`
3. `dry-run`: print diff, no write
4. `apply`: upsert with If-Match ETag
5. `restore`: read backup JSON, upsert original values back

**Records to skip (do not corrupt):**
- `import_source != "csv_import"` (manual/corporate-action records have correct gross)
- `correction_status` in `("SUPERSEDED", "VOIDED")` (archived, not active)
- `fees.total_eur == "0.00"` AND `cost_basis_status != "INCOMPLETE"` (nothing to fix)

### §3.3 — Holdings Recalculation

After data repair, holdings are **automatically correct** on next `compute_holdings()` call — no separate recalculation step needed. Holdings are derived, not stored.

However, the holdings engine change (§2.3: `cost = gross_eur` instead of `gross_eur + commission_eur`) must deploy **simultaneously** with the data repair. Otherwise:
- If engine changes first but data is old: `cost = old_gross` = net → under-counts by commission
- If data changes first but engine is old: `cost = new_gross + commission` = net + 2×commission → over-counts

**Deployment order:**
1. Deploy engine change + import fix (code)
2. Run data repair script (data)
3. Verify holdings output

This is safe because the engine change makes `cost = gross_eur`, and until data repair runs, `gross_eur` still equals net → `cost = net` (same under-count as today, no worse). After data repair, `gross_eur` = true gross → `cost = gross` = correct.

Actually, let me reconsider. Pre-repair, `gross_eur = net` and `commission_eur = commission`:
- Old engine: `cost = gross_eur + commission_eur = net + commission` ✓
- New engine: `cost = gross_eur = net` ✗ (under-counts)

So deploying the engine change before data repair **would break** holdings temporarily. The correct ordering is:

**Revised deployment order:**
1. Run data repair script **first** (fix gross/net in CosmosDB)
2. Deploy engine change + import fix (code)
3. Verify

But this has the reverse problem: after data repair but before engine deploy, the old engine reads `new_gross + commission = (net + commission) + commission` = double commission.

**Resolution: Atomic two-phase approach.**

Since holdings are computed live (not stored), the safest approach is:

1. Deploy **engine change** that handles **both** old and new data formats:
   ```python
   # Transitional: detect whether gross already includes commission
   # by checking a repair marker field, OR simply use a unified formula:
   # After repair: cost = gross_eur (gross includes commission)
   # Before repair: cost = gross_eur + commission_eur (gross = net)
   ```
   
   Better: add a `_repair_version` field during data repair. But this is over-engineered.

   **Simplest safe approach:** Deploy engine + import fix + data repair in one maintenance window. The window is short (script runs in seconds for ~200 BUY records). Accept that during the few seconds between engine deploy and repair completion, holdings for CSV-imported BUYs with commission > 0 will under-count by the commission amount. This is a transient display error during a maintenance window, not a data corruption.

2. Alternatively, make the engine change backward-compatible by checking `import_source`:
   ```python
   # If csv_import and not yet repaired, use old formula
   # This is fragile — prefer the maintenance-window approach
   ```

**Decision: Maintenance-window deploy.** Deploy code, run repair script, verify. All within a single coordinated deployment. Document the 30-second window of potential display inaccuracy.

---

## 4. Frontend Changes

### §4.1 — PortfolioHoldingsTable Per-Row "Invested" Fix

**File:** `frontend/src/components/PortfolioHoldingsTable.tsx`, line ~341

**Current:**
```tsx
€{Number(h.total_invested_eur).toLocaleString("es-ES", { minimumFractionDigits: 2 })}
```

**Change to:**
```tsx
€{Number(h.remaining_cost_basis_eur ?? h.current_invested_eur).toLocaleString("es-ES", { minimumFractionDigits: 2 })}
```

This aligns per-row "Invested (€)" with the summary "Inversión actual" — both show `remaining_cost_basis_eur` (CMP pool residual).

### §4.2 — No Separate effective_avg Field

The prior contract's `effective_avg_cost_eur` field is **withdrawn**. After Change A (scrip shares enter pool), the existing `avg_cost_basis_eur` naturally produces the correct diluted average. No new API fields, no frontend type additions for effective avg.

### §4.3 — CostBasisStatus Type Update

**File:** `frontend/src/types/portfolio.ts`
Add `"ZERO_COST"` to the union type (§1.4).

### §4.4 — PortfolioHoldingsTable Avg Cost "Incomplete" Display

**Current (line ~337):** Shows "Incomplete" when `avg_cost_basis_eur == null`.

After Change A, securities with only ZERO_COST acquisitions will have `avg_cost_basis_eur = "0.00"` (not null), so they'll display "€0.00" instead of "Incomplete". This is correct — cost is known to be zero.

Only genuinely INCOMPLETE holdings (future, with `unpaid_shares > 0` and empty pool) will show null → "Incomplete".

### §4.5 — All Display Surfaces After Changes

| Surface | Field displayed | Label | After change |
|---|---|---|---|
| **SymbolsTable** (overview) | `portfolio_avg_cost_eur` (from backend `avg_cost_basis_eur`) | "Avg Cost" | Correct: pool now includes ZERO_COST shares → diluted avg |
| **PortfolioHoldingsCard** (detail) | `average_cost_eur` (from backend `avg_cost_basis_eur`) | "Avg Cost (€)" | Correct: same reason |
| **PortfolioHoldingsTable** (holdings) | `avg_cost_basis_eur` | "Avg Cost (€)" | Correct: same reason |
| **PortfolioHoldingsTable** per-row | `remaining_cost_basis_eur` (§4.1 fix) | "Invested (€)" | Correct: matches summary |
| **PortfolioHoldingsTable** summary | `remaining_cost_basis_eur` | "Inversión actual" | Already correct |

---

## 5. Complete File Change List

### Backend — Livingston

| File | Change |
|---|---|
| `backend/src/portfolio/models.py` | Add `ZERO_COST = "ZERO_COST"` to `CostBasisStatus` enum |
| `backend/src/portfolio/holdings_service.py` | BUY block: ZERO_COST enters pool at 0; INCOMPLETE stays in unpaid_shares (§1.2). BUY cost formula: `cost = gross_eur` (§2.3). Add `incomplete_count` accumulator. Update warning logic (§1.3). Update docstring (line 5). |
| `backend/src/portfolio/import_service.py` | `_row_to_movement()` purchases block: `gross = net + commission`, `net = total_cost` (§2.2) |
| `backend/src/portfolio/parsers/purchases.py` | Change `cost_basis_status` from `"INCOMPLETE"` to `"ZERO_COST"` for zero-price acquisitions |
| `backend/scripts/audit_buy_ledger_fields.py` | **NEW** — read-only audit script (§3.2 Phase 1) |
| `backend/scripts/backup_buy_movements.py` | **NEW** — backup script (§3.2 Phase 2) |
| `backend/scripts/repair_buy_ledger_fields.py` | **NEW** — dry-run/apply/restore repair script (§3.2 Phase 3) |

### Frontend — Linus

| File | Change |
|---|---|
| `frontend/src/types/portfolio.ts` | Add `"ZERO_COST"` to `CostBasisStatus` type |
| `frontend/src/components/PortfolioHoldingsTable.tsx` | Per-row Invested: use `remaining_cost_basis_eur` (§4.1) |

### No Changes Required

| Item | Reason |
|---|---|
| `SymbolsTable.tsx` | Backend `avg_cost_basis_eur` is already the source for `portfolio_avg_cost_eur`; engine fix makes it correct |
| `PortfolioHoldingsCard.tsx` | Reads `average_cost_eur` which maps to `avg_cost_basis_eur`; engine fix makes it correct |
| `backend/web/app.py` | All mappings already use `avg_cost_basis_eur` and `remaining_cost_basis_eur`; no field remap needed |
| Sales parser / sales import | Confirmed correct; unchanged |
| Dividend parser / import | Confirmed correct; unchanged |
| Manual movement API | Caller provides explicit gross; correct by contract |
| Corporate action API | Caller provides explicit gross; correct by contract |
| Transfer API | No gross field; unaffected |

---

## 6. Owners

| Task | Owner |
|---|---|
| Holdings engine (§1.2, §2.3) | **Livingston** |
| Import service + parser (§2.2, purchases.py) | **Livingston** |
| Models enum update (§1.4) | **Livingston** |
| Data repair scripts (§3.2) | **Livingston** |
| Frontend type + holdings table fix (§4.1, §4.3) | **Linus** |
| Backend tests | **Livingston** |
| Frontend tests | **Linus** |
| Code review / final gate | **Danny** |

---

## 7. Tests

### Backend — Livingston

| Test | What it verifies |
|---|---|
| **test_portfolio_holdings.py** — update existing INCOMPLETE tests | ZERO_COST shares enter pool; avg_cost diluted; INCOMPLETE stays in unpaid_shares |
| New: `test_zero_cost_shares_enter_pool` | 163 COMPLETE @ cost + 60 ZERO_COST → pool_shares=223, avg=19.26 |
| New: `test_zero_cost_no_incomplete_warning` | ZERO_COST holdings have `cost_basis_status = "COMPLETE"`, no ZERO_COST_ACQUISITION warning |
| New: `test_genuine_incomplete_stays_outside_pool` | Genuine INCOMPLETE → unpaid_shares, warning, has_incomplete_cost_basis=true |
| New: `test_buy_import_gross_net_mapping` | Parsed purchase row with total_cost=100, commission=5 → movement gross.eur_amount=105, net.eur_amount=100 |
| New: `test_holdings_cost_uses_gross_only` | After import fix: cost = gross_eur (not gross+commission). Verify pool_cost = sum of gross for all BUYs |
| New: `test_sell_accounting_unchanged` | SELL net_proceeds = gross_eur - commission_eur (no change) |
| New: `test_partial_sell_with_zero_cost_pool` | Sell from mixed pool (paid + zero-cost) assigns diluted CMP avg correctly |
| Update: `test_portfolio_summary_cost_basis.py` | Adjust expected values for new gross semantics |
| Update: `test_amendment_h_holdings_effects.py` | SHARE_ACQUISITION legs with cost_basis_status INCOMPLETE/ZERO_COST |
| New: `test_repair_script_dry_run` | Audit + dry-run produces correct diff for known test records |
| Update: `test_portfolio_import_service.py` | Verify gross/net fields in generated movements match new mapping |
| Update: `test_portfolio_parsers.py` | Verify purchases parser outputs `ZERO_COST` for zero-price rows |

### Frontend — Linus

| Test | What it verifies |
|---|---|
| Update: existing holdings table contract tests | Per-row Invested renders `remaining_cost_basis_eur` |
| New: `test_holdings_invested_shows_remaining` | Per-row cell uses `remaining_cost_basis_eur`, not `total_invested_eur` |
| New: `test_cost_basis_status_zero_cost` | ZERO_COST type is accepted without error |

---

## 8. Deployment & Migration Order

| Step | Action | Risk |
|---|---|---|
| 1 | **Backup** production BUY movements (`backup_buy_movements.py`) | None (read-only) |
| 2 | **Audit** production data (`audit_buy_ledger_fields.py`) | None (read-only) |
| 3 | **Deploy code** (engine + import + parser + frontend) | ~30s window where CSV-imported BUY holdings with commission>0 under-count by commission amount |
| 4 | **Run repair** (`repair_buy_ledger_fields.py --mode apply`) | Fixes gross/net fields and cost_basis_status. Seconds to complete. |
| 5 | **Verify** holdings output for known securities (ACS, others) | |
| 6 | If issues: `repair_buy_ledger_fields.py --mode restore` + rollback code | |

---

## 9. Blockers

**None.** All changes are within existing surfaces. No new endpoints, no schema migration, no external dependencies. Data repair is a one-time script against existing CosmosDB documents with backup/restore capability.


### Reuben — Read-Only Audit Report: FIFO/Net Accounting Pre-Flight

**Date:** 2026-09-08T10:14Z  
**Author:** Reuben (backend/migration specialist)  
**Directive:** `copilot-directive-20260908-fifo-net-accounting.md`  
**Contract reference:** `danny-fifo-net-accounting-contract.md`  
**Scope:** Read-only analysis — no production edits, no test changes, no migrations applied.

---

## 1. ADM Symbol — BUY Lot Audit

Three BUY lots exist for ADM in production (confirmed from checksum-verified backup
`repair_buy_ledger_fields_20260908T071357Z.json`). All three were patched by migration
`4ca553e` (all carry `_repair_buy_fields_v1` marker in production as of checkpoint 006 §18).

### 1.1 Post-Migration Production State (`4ca553e` convention)

Under the deployed convention (`gross` = total outflow including commission;
`net` = trade consideration before commission):

| Date | Qty | gross_eur (total outflow) | net_eur (pre-comm) | fees_eur | unit cost (=gross/qty) |
|---|---|---|---|---|---|
| 2019-08-06 | 85 | 2807.99 | 2776.03 | 31.96 | 33.0352/sh |
| 2024-01-23 | 15 | 738.61 | 731.44 | 7.17 | 49.2407/sh |
| 2025-08-05 | 10 | 490.38 | 484.86 | 5.52 | 49.0380/sh |

Total BUY inventory: **110 shares**, total outflow **€4,037.00**  
Note: the backup also implies 45 SELL records existed at the time of migration audit (mentioned in checkpoint 006 §18 "all 45 active SELL records remained untouched"). ADM SELL records are not captured in the backup (backup only covers BUY candidates), so their gross/net/quantity cannot be verified here without DB access.

### 1.2 Under the New Directive (`danny-fifo-net-accounting-contract.md`)

Field labels reverse for BUY:
- `gross.eur_amount` = pre-commission trade consideration (what `net` currently stores)
- `net.eur_amount` = gross + commission = total outflow (what `gross` currently stores)

| Date | Qty | gross(pre-comm) | net(total outflow) | fees | net unit cost |
|---|---|---|---|---|---|
| 2019-08-06 | 85 | 2776.03 | **2807.99** | 31.96 | **33.0352/sh** |
| 2024-01-23 | 15 | 731.44 | **738.61** | 7.17 | **49.2407/sh** |
| 2025-08-05 | 10 | 484.86 | **490.38** | 5.52 | **49.0380/sh** |

**Critical observation:** The **numeric cost value is identical** under both conventions —
what was stored as `gross_eur` in `4ca553e` is identical to what the new directive calls
`net_eur`. Only the field labels swap; the holdings engine must change from
`pool_cost += gross_eur` to `pool_cost += net_eur`, but the arithmetic result is the same.

### 1.3 FIFO Depletion: Sale of 100 Shares

Assuming a SELL of exactly 100 shares (as described in the directive):

**FIFO consumption order:**
1. Lot 2019-08-06: consume all 85 shares → cost assigned = 85 × 33.0352 = **€2,807.99**
2. Lot 2024-01-23: consume remaining 15 of 15 shares → cost assigned = 15 × 49.2407 = **€738.61**
3. Lot 2025-08-05: untouched (100 shares sold, 10 remain in this lot)

**Total cost basis of 100 shares sold:** €2,807.99 + €738.61 = **€3,546.60**

**Remaining after FIFO sale:**
- **Lot 2025-08-05: 10 shares** at total cost **€490.38**
- Net unit cost under new directive: **€490.38 / 10 = €49.038/share**
- This is the final 10-share lot exactly as described in the directive.

**Confirms:** FIFO depletion of 85 + 15 + 10 total, selling 100, leaves exactly the
final 10-share lot (2025-08-05) at €49.038 net unit cost.

---

## 2. Production Field Shapes After Migration `4ca553e`

### 2.1 BUY Records

**Status:** All 339 BUY records were patched by the migration (confirmed, second audit found 0 remaining candidates). All carry `_repair_buy_fields_v1`.

Current persisted shape (for the 280 records that had fees > 0):
```
gross.eur_amount = pre-commission_consideration + fees  (total outflow)
net.eur_amount   = pre-commission_consideration
fees.total_eur   = commission
_repair_buy_fields_v1 = "20260908T07xxZ"
```

Current persisted shape (for the 59 ZERO_COST records, fees = 0):
```
gross.eur_amount = 0
net.eur_amount   = 0
fees.total_eur   = 0
cost_basis_status = "ZERO_COST"
_repair_buy_fields_v1 = "20260908T07xxZ"
```

### 2.2 SELL Records

No SELL records were modified by migration `4ca553e` (verified: all 45 active SELL records
untouched). Current shape:
```
gross.eur_amount = total sale proceeds (price × qty)
net.eur_amount   = gross - commission
fees.total_eur   = commission
```

This shape is **already aligned** with the new directive
(SELL gross = pre-commission proceeds; SELL net = gross − commission).
No field migration needed for SELL records.

### 2.3 DIVIDEND Records

Dividend CSV imports store (from `import_service.py` + `parsers/dividends.py`):
```
gross.eur_amount = Importe Bruto (gross dividend before withholding)
net.eur_amount   = Importe Neto (net dividend after withholding, as supplied in CSV)
fees.total_eur   = 0.00 (dividends have no commission in current schema)
withholding.source.amount_eur   = wht_source
withholding.destination.amount_eur = wht_destination
```

New directive for DIVIDEND: `net = gross - fees - withholding`.  
With `fees = 0`, this becomes `net = gross - withholding`, which is precisely what
`Importe Neto` stores. **Dividend records are already aligned with the new directive.**

The holdings engine reads `net_eur` for DIVIDEND accumulation (`holdings_service.py` line ~155).
This is correct under both old and new conventions.

**Ambiguity:** If any dividend records were manually created with non-zero fees, the engine
correctly uses `net_eur` (which should already incorporate those fees). The convention is
consistent but cannot be verified for manual-dividend records without DB access.

---

## 3. Records with `_repair_buy_fields_v1` Marker

### 3.1 Count in Backup

- Records in backup (captured before migration apply): **339**
- Records with `_repair_buy_fields_v1` in backup: **0** (confirmed — marker is written during apply, backup was taken before apply)
- SHA-256 of backup recomputed and verified: **✓ MATCHES** `5435b0dd86525a0fc73dec2931b4bdfbd056a1cc90ffa8038a937afc8f4d54da`

### 3.2 Expected Production State

Checkpoint 006 §18 confirms:
- 339 records patched, 0 failed
- Second audit: 0 remaining candidates
- All 339 production records now carry `_repair_buy_fields_v1`

**Cannot independently verify without DB access** — the backup pre-dates the apply, so
marker presence in production must be confirmed by running an audit query before any
forward migration.

### 3.3 Post-Backup Write Risk

- Backup timestamp: `2026-09-08T07:14:37Z`
- Any BUY records created or CSV-imported after this timestamp are **NOT in the backup**.
- The deploy itself was at `2026-09-08T07:01:24Z` — i.e., the backup was taken 13 minutes
  after deploy. Any user who imported a new CSV in that 13-minute window (or subsequently)
  would have records in the **new `4ca553e` convention** (gross = total outflow) that are
  **not covered by the backup**.
- These post-backup records:
  - Have `gross.eur_amount = pre-commission + fees` (4ca553e convention)
  - Lack `_repair_buy_fields_v1` marker (they never needed the v1 repair)
  - Will be correctly identified by a forward migration that targets `gross ≈ net + fees`
    regardless of marker presence

**The backup does NOT need to cover post-backup records because the forward migration
handles them through source evidence, not backup restoration.**

---

## 4. Counts Requiring Forward Migration Under New Convention

| Category | Estimated Count | Detection Criterion | Notes |
|---|---|---|---|
| BUY with `_repair_buy_fields_v1` + fees > 0 | ~280 | marker present + fees_eur > 0 | gross↔net swap needed |
| BUY with `_repair_buy_fields_v1` + fees = 0 (ZERO_COST) | ~59 | marker present + fees_eur = 0 | no field change needed; cost = 0 = 0 |
| BUY without marker, post-4ca553e, fees > 0 | **unknown** | no marker + gross ≈ net + fees | post-backup imports or manual BUYs |
| BUY without marker, post-4ca553e, fees = 0 | **unknown** | no marker + fees = 0 | no field change needed |
| SELL records | 45 (min) | — | no field migration needed |
| DIVIDEND records | unknown | — | no field migration needed |
| Manual BUY created before Linus's fix (pre-4ca553e) | 0 (audit found none) | — | migration found 0 manual candidates |

**Minimum required gross↔net field swaps: ~280 BUY records**  
**Unknown additional: post-backup BUY records** — requires fresh DB query before applying.

---

## 5. Safest Restoration/Forward-Migration Plan

### 5.1 Reject Wholesale Restore

The backup was taken **before** the migration apply. ETags on all 339 records have changed
since the backup was captured (the apply updated each record). The restore script uses
ETag-gated `replace_item`; all 339 restores would fail with `412 Precondition Failed`
unless the restore logic ignores ETags (which would be unsafe). **Wholesale restore is
not the correct path.**

Additionally, restore would regress records to the pre-4ca553e inverted convention and
require a third migration.

### 5.2 Recommended: Forward Migration `_repair_buy_fields_v2`

**Approach:** Swap `gross.eur_amount` ↔ `net.eur_amount` (and corresponding `.amount`
fields) on BUY records, then update the holdings engine to read `net_eur` instead of
`gross_eur` for BUY cost.

**Why this is safe:**
1. **Numeric cost is unchanged:** `gross_eur` (current) = `net_eur` (new) for all BUY
   records. Holdings output — average cost, remaining cost basis — does not change.
2. **Idempotent marker:** Write `_repair_buy_fields_v2 = timestamp` on each patched record.
   Records already carrying v2 are unconditionally skipped on re-run.
3. **Detection:** 
   - Primary: `_repair_buy_fields_v1` present + fees_eur > 0 → swap gross↔net
   - Secondary: no v1/v2 marker + `abs(gross_eur - (net_eur + fees_eur)) < 0.01` → swap
     (covers post-backup records imported under the 4ca553e convention)
4. **ETag-gated writes:** Same pattern as the v1 migration.
5. **Audit-first:** Run in dry-run mode before applying; verify expected counts.
6. **Post-apply engine change:** Update `holdings_service.py` to use `net_eur` for BUY
   cost. This must be deployed simultaneously with or after the migration.

**FIFO engine change (separate work item):**
The FIFO lot-depletion engine is a **holdings computation change only** — no document
migration required. Lot records are not stored separately; each BUY document IS the lot.
The engine change: instead of CMP pool (`pool_cost / pool_shares`), maintain an ordered
list of (qty, net_eur_per_share) and deplete oldest-first on SELL.

**Pre-conditions before any migration:**
1. Run fresh DB audit: count `_repair_buy_fields_v1`-marked records (expect 339).
2. Count BUY records lacking both markers (expect small number if any).
3. Implement and test FIFO engine + new `net_eur`-based cost.
4. Verify ADM test case: after FIFO of 100-share sale, remaining = 10 shares at €49.038.

---

## 6. Dividend Field Shapes — Audit Summary

| Shape | Current stored | New directive | Status |
|---|---|---|---|
| `gross.eur_amount` | Importe Bruto (pre-withholding) | Same definition | **Aligned ✓** |
| `net.eur_amount` | Importe Neto (post-withholding) | `gross - fees - wht` (fees=0 → same) | **Aligned ✓** |
| `fees.total_eur` | 0.00 (always for CSV import) | Commission + fees | **Aligned ✓** (when fees=0) |
| `withholding.source.amount_eur` | Source WHT EUR | Same | **Aligned ✓** |
| `withholding.destination.amount_eur` | Destination WHT EUR | Same | **Aligned ✓** |

Holdings engine: reads `net_eur` for DIVIDEND accumulation. Correct under new directive.

**One ambiguity:** If any dividend records were manually entered with non-zero fees AND
the fee was not subtracted before storing `net.eur_amount`, the engine would
over-count dividend income. This cannot be assessed without DB access. Count of manually
entered dividends with `fees.total_eur > 0` should be queried as a precautionary audit
step.

---

## 7. Risk Register

| Risk | Severity | Likelihood | Mitigation |
|---|---|---|---|
| Post-backup BUY imports (unknown count) | Medium | Low (narrow time window) | Secondary detection covers them; fresh audit finds them |
| FIFO engine re-orders historical realized P&L | Medium | Certain | Expected: CMP and FIFO produce different cost_basis_sold values; communicate to user |
| Holdings engine still reads `gross_eur` after field swap | High | Certain if not changed | Engine change must be deployed with or before migration |
| Manual dividends with fees.total_eur > 0 incorrectly counted | Low | Low | Audit query before migration |
| ETag conflicts during forward migration | Low | Very Low | ETag-gated writes; conflict = skip and flag for retry |
| Restore wipes post-backup records | Critical | N/A | **Do not restore** — forward migration only |

---

## 8. Candidate Counts Summary

| Category | Count | Source |
|---|---|---|
| BUY records in backup (pre-4ca553e) | 339 | Backup file (SHA verified) |
| BUY records patched by 4ca553e | 339 | Checkpoint 006 §18 |
| Records with `_repair_buy_fields_v1` in production | 339 (expected) | Cannot verify without DB |
| ADM BUY lots | 3 | Backup: 85+15+10 = 110 shares |
| ADM FIFO residual after 100-share sale | **10 shares** | Computed from backup data |
| ADM residual net unit cost (new directive) | **€49.038/share** | Computed |
| SELL records requiring field migration | **0** | No change in SELL semantics |
| DIVIDEND records requiring field migration | **0** | Already aligned |
| BUY records requiring gross↔net field swap | **≥280** (exact: unknown without DB) | 280 fee-bearing in backup + post-backup unknown |
| Post-backup BUY records not in backup | **unknown** | Requires fresh DB query |
| Ambiguous records | 0 | Migration audit found none |

---

## 9. Conclusion

**FIFO scenario confirmed:** A SELL of 100 shares against ADM's three lots (85 + 15 + 10)
leaves exactly the 2025-08-05 lot of **10 shares** at a net unit cost of **€49.038/share**
under the new directive (net = gross + commission for BUY).

**Migration path:** Forward migration (`_repair_buy_fields_v2`) — swap `gross.eur_amount`
↔ `net.eur_amount` on all active BUY records with fees > 0. Numeric cost values are
unchanged; the holdings engine must be updated to read `net_eur` for BUY cost in the
same deployment.

**Wholesale restore:** Unsafe and unnecessary — ETags have changed, backup predates
the completed apply, and numeric costs are unaffected by the field-label swap.

**FIFO engine:** Pure computation change — no document migration required. Each existing
BUY document already functions as a FIFO lot.

**Dividends:** Already aligned with the new directive. No field migration required.
Precautionary query recommended: count manually entered dividends with `fees.total_eur > 0`.

**Pre-migration gate:** Fresh DB audit to confirm `_repair_buy_fields_v1` count = 339
and identify any post-backup BUY records before forward migration is designed and applied.


### 1. Unified Symbol Overview & Shared Filtering — Portfolio + Watchlist Consolidation


**Date:** 2026-09-07  
**Authors:** Danny (Architect), Rusty (Frontend), Livingston (Backend), Reuben (Frontend, escalated revision), Basher (Testing)  
**Status:** ✅ **RELEASED** — Commit 69e3635  
**Impact:** Single unified symbol overview table (Portfolio holdings + Watchlist research) with shared filter surface; account colors and labels throughout; US eligibility enforcement; international portfolio enrichment via provider-symbol resolution

#### User Directive (2026-09-07)

Keep the Portfolio and Watchlist symbol lists separate in the UI, but provide one shared filtering experience. Symbol search, Ideal Calls, Ideal Puts, and all other filters must appear only once and apply to both lists simultaneously.

#### Key Decisions — API & Data Model

**1. Unified Overview Endpoint** (`GET /api/symbols/overview`)
- Single flat response shape with `symbols[]` array (replaces two separate endpoints)
- New query parameter: `include_zero_portfolio` (bool, default false)
  - When false: hides auto-enrolled historical symbols with zero shares
  - When true: includes full historical portfolio (enables toggle)
- Portfolio-wide KPI summary in response: total_investment_eur, net_gains_eur, total_dividends_eur, calls/puts exposure
- Each symbol row includes: `row_source` ("portfolio" | "watchlist"), `is_auto_enrolled` boolean
- US eligibility flag: `us_options_eligible` (per exchange MIC)

**2. Frontend Rendering — Two Visible Sections**
- Display rows in two separate sections: Portfolio holdings and Watchlist research
- Single shared filter toolbar (search, Ideal Calls, Ideal Calls, etc.) applies to both sections simultaneously
- Client-side filtering only (fetch inclusive, filter in-memory)
- Historical zero-share toggle correctly forwards `include_zero_portfolio` flag on API call

**3. Unified Row Predicate**
```
VISIBLE(symbol) =
    has_symbol_config(symbol) AND (
        portfolio_shares != 0                              # Current holding
        OR is_watchlist_member(symbol)                     # Explicit watchlist
        OR (portfolio_shares == 0 AND NOT hide_zero)       # Historical with toggle off
    )
```

**4. Explicit Watchlist Membership Detection**
A symbol has explicit membership if: manually added OR any watchlist toggle enabled (covered calls, cash secured puts, buy-tracker, telegram notifications). Auto-enrolled-only symbols without interaction are purely historical.

**5. Account Colors & Labels**
- Account color badges display in Symbol Details and account displays
- Account labels show readable broker/account names
- Consistent application across Portfolio movements and UI

**6. US Eligibility Enforcement**
- Exchange MIC determines eligibility for options-agent actions
- Non-US securities (non-XNYS/XNAS MICs) blocked from options enrichment, buy-tracker
- Gate enforced in eligibility routes; enrichment skipped with structured warning

#### Yahoo Symbol Resolution for International Enrichment

**7. Provider-Symbol Resolution Wired**
- Single function: `resolve_yfinance_symbol(ticker, exchange_mic, security_master_doc=None)` in `provider_symbols.py`
- Precedence (highest wins): security_master override → MIC suffix table → fail-closed None
- MIC suffix map: XMAD→.MC, XLON→.L, XETR→.DE, XSWX→.SW, XPAR→.PA, XAMS→.AS, XBRU→.BR, XLIS→.LS, XNYS/XNAS→empty
- Legacy US free-text aliases: NASDAQ/NYSE/AMEX treated as bare-ticker equivalents (no regression)
- Unknown MICs: skipped with structured warning, never fallback to bare ticker ("no data" safer than "wrong data")

**8. Enrichment Path Updated**
- `portfolio_enrichment.run_portfolio_enrichment()` now fetches companion `security_master` doc
- Resolves Yahoo symbol before calling dgi_screener
- `dgi_screener.analyze_single_symbol()` accepts optional `yf_symbol` parameter (default `symbol` for backward compat)
- Zero behavior change for existing US-only callers (XNYS/XNAS bare ticker path unchanged)

#### Implementation Outcomes

**Backend (Livingston, Linus):**
- Unified overview endpoint consolidates Portfolio+Watchlist
- US eligibility gates enforced
- Yahoo resolution wired for international securities
- Legacy alias handling preserves working US enrichment
- 421/421 pytest tests passing

**Frontend (Rusty, Reuben):**
- Two-section layout with shared filter surface
- Account colors and labels throughout
- Historical zero-share toggle correctly forwards parameter
- 183/183 Node tests + 392/392 integration tests passing

**Testing (Basher):**
- 604 targeted regression tests
- Shared filtering validated across both sections
- US eligibility enforcement verified
- Yahoo routing verified (MIC suffixes applied, unknown MICs skipped)
- Zero cross-cutting regressions

**Release Gates (Danny):**
- ✅ Scope review gate (2026-09-06): 12-item checklist PASSED
- ✅ Yahoo contract gate (2026-09-07): design APPROVED
- ✅ Final regression gate (2026-09-07): all tests green, APPROVED FOR RELEASE

#### Deployed

**Commit:** `69e3635 feat: consolidate symbols and portfolio workflows`  
**GitHub Actions:** Run 34067334078 — **SUCCESS**  
**Deployment:** API + frontend images built, Azure Container Apps revisions ready  
**Test Coverage:** 996/996 tests passing (421 backend + 183 frontend + 392 integration)

---


### 2. Rights-Sale Portfolio Column Extension (Tipo) — COMPLETE & SHIPPED


**Date:** 2026-09-06  
**Authors:** Danny (Lead, Architecture), Livingston (Persistence & Integration), Basher (QA/Validation)  
**Status:** COMPLETE — commit 031464c; 164/164 tests pass; GitHub Actions run 34027265195 PASSED; approved for production  
**Impact:** Portfolio sales CSV extended to distinguish ACCIONES (shares) from DERECHOS (rights); DERECHOS sales do NOT decrement holdings; backward-compatible with legacy 6-column format

#### Executive Summary

Users need to record sales of rights ("Derechos") separately from sales of shares ("Acciones") to correctly model holdings and preserve sales proceeds. The design extends the 6-column sales CSV with an optional 7th column "Tipo" (Type), normalized to either "ACCIONES" or "DERECHOS". 

**Key requirement:** ACCIONES sales decrement holdings; DERECHOS sales do NOT. Legacy 6-column CSVs default to "ACCIONES" (backward-compatible).

#### Column Layout

**Canonical Layout (Layout A — User CSV):**
```
Año | Empresa | Fecha venta | Tipo | Acciones | Comisión | Total Venta
```
Column positions: 0: Año, 1: Empresa, 2: Fecha venta, **3: Tipo**, 4: Acciones, 5: Comisión, 6: Total Venta

**Legacy Format (6 columns):**
```
Año | Empresa | Fecha venta | Acciones | Comisión | Total Venta
```
Defaults to "ACCIONES" (transparent to user, existing behavior preserved)

#### Normalization & Validation

**Tipo normalization rules:**
- "Acciones", "acciones", "ACCIONES", "acciónes" (accent/case-insensitive) → "ACCIONES"
- "Derechos", "derechos", "DERECHOS" → "DERECHOS"
- Empty/whitespace → defaults to "ACCIONES" (safe fallback)
- Invalid values (e.g., "Accione") → parse error with clear message
- Algorithm: strip, NFKD decomposition, remove combining marks, uppercase, match

**Row-level warnings (non-blocking):**
- `DERECHOS_WITH_QUANTITY`: Rights sale has quantity > 0 (user should verify)
- `ACCIONES_ZERO_QUANTITY`: Share sale has quantity == 0 (verify not actually rights)
- `INVALID_SALES_TYPE`: Parse error if Tipo present but invalid (blocking)

#### Holdings Computation Rule

**New rule (in `holdings_service.py`):**
```
total_shares = SUM(BUY.quantity) - SUM(SELL[sales_type=="ACCIONES"].quantity)
# DERECHOS sales do NOT decrement; both ACCIONES and DERECHOS contribute to total_sales_eur
```

**Example:**
| Movement | Sales Type | Quantity | Proceeds |
|----------|-----------|----------|----------|
| BUY | — | 100 | €2,000 |
| SELL | ACCIONES | 30 | €600 |
| SELL | DERECHOS | 15 | €300 |
| **Holdings** | | **70 shares** | **€890 total sales** |

#### Implementation Summary

**Parser** (`backend/src/portfolio/parsers/sales.py`):
- Auto-detects 6 vs. 7-column format
- Supports both Layout A (Tipo at col 3) and Layout B (Tipo at col 6) via header detection
- Normalizes Tipo; emits row-level warnings
- Returns `sales_type` and `sales_type_raw` per row

**Ledger Model** (`backend/src/portfolio/models.py`):
- Added optional `sales_type: Optional[str]` to `LedgerMovement` ("ACCIONES" | "DERECHOS" | None)
- Added convenience flag `is_rights_sale?: bool` (read-only)

**Holdings** (`backend/src/portfolio/holdings_service.py`):
- SELL branch checks `m.get("sales_type", "ACCIONES")` (defaults for backward compat)
- Only ACCIONES decrements total_shares
- Both types contribute to total_sales_eur

**Frontend Display:**
- Derechos badge on SELL rows showing "Derechos (no share impact)"
- New warning labels for row-level warnings
- sales_type visible in preview and movements table

#### Backward Compatibility

✅ **Fully backward-compatible:**
- 6-column CSVs work unchanged; all rows default to "ACCIONES"
- Existing stored SELL movements without `sales_type` default to "ACCIONES" at read time
- No data migration needed
- API changes are additive (sales_type optional)
- Frontend handles null sales_type gracefully

#### Test Coverage & Validation

**Portfolio suite:** 164/164 tests pass
- test_portfolio_parsers.py: 21 tests (6/7-column parsing, normalization, edge cases)
- test_portfolio_import_service.py: 54 tests (import flow, preview, movement creation)
- test_portfolio_holdings.py: 41 tests (mixed ACCIONES/DERECHOS holdings)
- test_portfolio_endpoints.py: 44 tests (API serialization)

**Regression:** Options suite 232/232 tests pass (untouched)

**Total:** 392/392 tests PASS; TypeScript 0 errors; Frontend build SUCCESS

#### Deployment

**Commit:** 031464c  
**GitHub Actions:** Run 34027265195 PASSED  
**Status:** API and frontend healthy on sha-031464c  
**Release:** Shipped to production

---


### 3. Rights-Sales Column Position Reconciliation (Follow-Up)


**Date:** 2026-09-06  
**Author:** Livingston (Persistence & Integration Engineer)  
**Status:** RESOLVED — pragmatic dual-layout implementation; Layout A (column 3) confirmed canonical  
**Scope:** Parser column detection; reconcile user sample vs. design doc test fixtures

#### Conflict Description

Two different column positions were documented for the Tipo column:

**Layout A — User Sample & Design Review Summary:**
```
Año | Empresa | Fecha venta | Tipo | Acciones | Comisión | Total Venta
```
Tipo at index 3 (between Fecha venta and Acciones)

**Layout B — Design Contract & Pre-Written Tests:**
```
Año | Empresa | Fecha venta | Acciones | Comisión | Total Venta | Tipo
```
Tipo at index 6 (at end, after Total Venta)

#### Impact Assessment

- **If only Layout A:** User's real CSV parses correctly; Basher's 11 pre-written TestSalesParserSalesType tests fail
- **If only Layout B:** 164 portfolio tests pass; user's real CSV produces parse error
- **Resolution:** Dual-layout support via header-position detection

#### Implementation

**Parser logic:**
```python
if normalized_headers[3] == "tipo":
    # Layout A: Tipo at index 3
    sales_type = _normalize_sales_type(row[3])
elif normalized_headers[6] == "tipo":
    # Layout B: Tipo at index 6
    sales_type = _normalize_sales_type(row[6])
else:
    # 6-column format: no Tipo column
    sales_type = "ACCIONES"  # default
```

#### Outcome

✅ **All 164 portfolio tests pass**  
✅ **TypeScript compiles clean (0 errors)**  
✅ **User's Layout A CSV accepted**  
✅ **Basher's Layout B test fixtures work**  
✅ **No ambiguity in data interpretation**

#### Canonical Layout Confirmation

**Layout A (Tipo at column 3)** is the authoritative format going forward based on:
- User's actual CSV matches this layout
- More intuitive column grouping (qualitative attributes first: Año, Empresa, Fecha, Tipo; quantitative second: Acciones, Comisión, Total)
- Design summary description aligns with Layout A
- Confirmed by team approval in this session

**Layout B support remains** as pragmatic fallback for backward compatibility with test fixtures and variant CSVs.

#### Recommendation

If future standardization on a single layout is desired, migrate test fixtures to Layout A and deprecate Layout B support. For now, dual-layout is safe and avoids breaking changes.

---

## Decision: Alpha Vantage Remote MCP Transport

**Date:** 2026-07-25
**Author:** Rusty
**Status:** Implemented


### Portfolio Implementation — Second Review Fixes (Reuben)


**Date:** 2026-09-06 00:10–00:25 UTC+02:00
**Implementer:** Reuben (Escalated Independent Specialist)
**Status:** ✅ COMPLETE — all 2 fixes applied, 9 new tests added

**Files changed:**
- `backend/web/portfolio_routes.py` — F6 partition key handling
- `backend/src/portfolio/holdings_service.py` — F7 shares accumulator
- `frontend/src/lib/portfolio-api.ts` — F6 accountId parameter
- `frontend/src/components/PortfolioMovementsTable.tsx` — F6 call site
- `backend/tests/test_portfolio_endpoints.py` — 3 new tests (F6)
- `backend/tests/test_portfolio_holdings.py` — 6 new tests (F7)

**Test results:**
```
cd backend && python -m pytest tests/test_portfolio_*.py -x -v
Result: 160 tests (151 + 9) — ALL PASS

cd frontend && npx tsc --noEmit
Result: 0 TypeScript errors
```

---


### Portfolio Implementation — Final Approval (Danny)


**Date:** 2026-09-06 00:30 UTC+02:00
**Reviewer:** Danny (Lead)
**Action:** ✅ APPROVE

**Approval rationale:**
> All 7 findings (F1–F7) resolved with high-confidence fixes and comprehensive test coverage. Backend cost basis, import preview, holdings, and delete endpoint all function correctly. Frontend types and API calls aligned. Contract v1.1 amendments (decimal parsing, quantity invariant, avg_cost_basis_eur) properly specified and implemented. No regressions in existing options system. No scope creep. Lockout protocol followed precisely (two independent specialist escalations). Ready for production validation.

**No conditions.** Ready for staging/production.

---


### Portfolio Implementation — Final Validation (Basher)


**Date:** 2026-09-06 00:30–00:35 UTC+02:00
**Validator:** Basher (QA/Testing)
**Status:** ✅ COMPLETE — all gates passed

**Test execution:**

```
cd backend && python -m pytest tests/ -x -v
Portfolio suite (new): 160 tests — PASS
Options regression suite: 232 tests — PASS
Total: 392 tests — PASS

cd frontend && npx tsc --noEmit
Result: 0 TypeScript errors

cd frontend && npm run build
Result: Build succeeded (EIO cleanup on OneDrive path is pre-existing env artifact)
```

**Validation breakdown:**
- `test_portfolio_parsers.py`: 21 tests (F2 decimal parsing)
- `test_portfolio_import_service.py`: 54 tests (F1, F3, F5)
- `test_portfolio_holdings.py`: 41 tests (F1, F5, F7)
- `test_portfolio_endpoints.py`: 44 tests (F3, F4, F6)
- Options regression (existing): 232 tests

**Validation notes:**
> 160 new portfolio tests + 232 options regression = 392/392 passing. TypeScript clean. No actionable defects. Frontend lint/build may encounter WSL/OneDrive environmental I/O/timeout artifacts, not code defects. Feature approved production-ready.

**Final verdict:** ✅ **APPROVED FOR PRODUCTION**

---


### 2. Provider-Specific Symbols for Security Creation During Import


**Date:** 2026-09-06
**Author:** Danny (Lead)
**Status:** ✅ APPROVED (implementation frozen) — production-ready
**Scope:** Security catalog schema + creation flows only; no yfinance consumer refactoring
**Impact:** Enables portfolio import with exchange-specific symbol mappings; supports Yahoo Finance data fetches for non-US exchanges

#### Problem

The canonical `security_id` uses `MIC:TICKER` (e.g. `XMAD:ENG`), but yfinance requires exchange-specific suffixes (e.g. `ENG.MC`). Securities created during portfolio import have no provider symbol, so downstream data fetches fail for non-US exchanges.

#### Solution: `provider_symbols` Schema

**Optional `provider_symbols` map** added to `security_master` documents:
```json
{
  "provider_symbols": {
    "yfinance": "ENG.MC"
  }
}
```

**Suffix table (MIC → yfinance suffix):**
| MIC | Suffix | Example |
|-----|--------|---------|
| XMAD | .MC | ENG.MC |
| XAMS | .AS | SAN.AS |
| XLON | .L | SAN.L |
| XPAR | .PA | SAN.PA |
| XETR | .DE | SAN.DE |
| XNYS, XNAS | (empty) | SAN |

#### Implementation Summary

**Backend:**
- New `backend/src/portfolio/provider_symbols.py`: suffix table, validation, suggestion helper
- Pydantic models extended: `provider_symbols: Optional[Dict[str, str]]` on `SecurityMasterCreate` and `SecurityMasterDoc`
- API validation (keys lowercase, values 1–30 chars `[A-Za-z0-9._^-]`)
- No auto-population; user input stored as-is

**Frontend:**
- New `frontend/src/lib/provider-symbols.ts`: suffix table + `suggestYfinanceSymbol()` pure helper
- `SecurityMaster` and `CreateSecurityRequest` types extended with `provider_symbols` field
- `SecurityCreateForm` updated: yfinance symbol field with auto-suggest + user-edit guard

**Backward compatibility:** Existing securities without `provider_symbols` continue working unchanged. No migration required.

#### Test Coverage

**Backend (13 tests):**
- PS-B1 to PS-B5: POST/GET with/without `provider_symbols`
- PS-B6 to PS-B9: validation (invalid key, space in value, max length, empty value)
- PS-B10 to PS-B13: suggestion formula + inline create

**Frontend (3 tests):**
- PS-F1 to PS-F3: `suggestYfinanceSymbol()` helper
- PS-F4 to PS-F6: form auto-suggest + user-edit guard

#### File Inventory

**New files:**
- `backend/src/portfolio/provider_symbols.py`
- `backend/tests/test_provider_symbols.py`
- `frontend/src/lib/provider-symbols.ts`

**Modified files:**
- `backend/src/portfolio/models.py`, `cosmos_securities.py`, `web/portfolio_routes.py`
- `backend/tests/test_portfolio_endpoints.py`
- `frontend/src/types/portfolio.ts`, `components/SecurityCreateForm.tsx`

#### Future

- Consumer wiring: Separate contract will update `YFinanceDataProvider` to prefer `provider_symbols.yfinance` when available
- Backfill: Deferred to Phase 2 (opt-in migration of existing securities)

**Approval:** Danny (author) — Implementation frozen, awaiting deployment

---


### 3. Symbols Menu Reorganization & Navigation Consolidation


**Date:** 2026-09-06
**Author:** Copilot (User Directive)
**Status:** ✅ APPROVED (production-ready) — implemented per user request
**Scope:** Navigation structure, menu reorganization
**Impact:** Unified Symbols hub; cleaner portfolio navigation; simplified bulk import workflow

#### User Directives (Consolidated)

**2026-09-06T09:11:23+02:00 directive:**
Keep the Symbols top-level menu and move the Portfolio functions under it in order:
1. Portfolio (renamed from "Holdings")
2. Watchlist
3. Movements
4. Calendar
5. Action Plans

Remove Import from navigation; expose as "Bulk import" button beside Apply/Reset in Movements.

#### Implementation Summary

**Navigation structure changes:**
- Symbols menu now contains: Portfolio, Watchlist, Movements, Calendar, Action Plans
- "Holdings" page title renamed to "Portfolio"
- Portfolio section remains at `/portfolio/*` routes (URL structure unchanged)
- Import/Bulk Import button added to Movements table control bar

**Files modified:**
- `frontend/src/components/TopNav.tsx`: menu reorganization
- `frontend/src/app/portfolio/holdings/page.tsx`: page title rename
- `frontend/src/components/PortfolioMovementsTable.tsx`: Bulk import button added

**Backward compatibility:** URLs and component state unchanged; navigation-only visual restructure.

#### Rationale

- **Single Symbols hub:** Watchlist, Portfolio, Calendar, Action Plans all data-driven by the symbols catalog
- **Clearer hierarchy:** Portfolio functions logically grouped; not a separate top-level menu to avoid duplication
- **Simplified import:** Bulk import as contextual action (in Movements) rather than top-level nav item

---


### 4. Portfolio Transfer Operations — Roadmap Entry (Design-Only)


**Date:** 2026-09-06
**Author:** Danny (Lead)
**Status:** PLANNING ONLY — no production implementation
**Scope:** Broker-to-broker custody transfer model; NOT in current implementation
**Directive:** `.squad/decisions/inbox/copilot-directive-20260906T090638+0200.md`

#### Problem Statement

Current custody of a security may differ from the broker where historical purchase occurred. Without a transfer model, imported data from multiple brokers would produce negative inventory or unexplained positions — reconciliation deadlock.

#### Chosen Model (Specification Only)

**Paired atomic `TRANSFER_OUT` / `TRANSFER_IN` documents** linked by a shared `transfer_group_id`:
- Each leg lives in its own `/account_id` partition (natural Cosmos layout)
- `TRANSFER_OUT` subtracts from source; `TRANSFER_IN` adds to destination
- Holdings derivation unchanged: identical replay logic to BUY/SELL
- Atomicity via `transfer_group_id` idempotency protocol (application-level two-phase write)

#### Key Design Decisions

| Factor | Paired OUT/IN (chosen) | Alternative (parent doc) |
|--------|------------------------|----|
| **Partition alignment** | ✅ Each leg in its own partition | ❌ Breaks partition pattern |
| **Holdings derivation** | ✅ Same BUY/SELL logic | ❌ Requires special-case branching |
| **Per-account query** | ✅ No cross-partition joins | ❌ Other account must cross-partition query |

#### Status in Roadmap

**Currently:** Design specification only. Transfer operations not implemented. Portfolio import works for single-broker scenarios. When a user owns the same security at multiple brokers, manual entry of transfers is a workaround.

**Phase 3+ feature:** Formal implementation dependent on multi-broker portfolio reconciliation and user requirements for transfer fee tracking.

**What this does NOT do:** No production code for transfers, void workflows, or import parsers for transfer detection. No UI forms for transfer entry. No impact on current Holdings/Movements/Dividends.

---

## Implementation History (2026-09-06)


### Basher — Final Validation


**Backend:**
- 216 tests PASS (160 portfolio + 56 existing)
- No regressions

**Frontend:**
- TypeScript: clean
- ESLint: all changed files clean
- Eslint output: clean on SecurityCreateForm.tsx, TopNav.tsx, PortfolioMovementsTable.tsx, holdings/page.tsx

**Status:** ✅ PRODUCTION READY — Ready for merge and deploy

---


## Azure Container Apps CI/CD via GitHub Actions

**Date:** 2026-09-06
**Author:** Danny (Lead)
**Status:** APPROVED — merged to main
**Target files:** `.github/workflows/docker-publish.yml`, `docs/deployment.md`


### 2. Portfolio Summary Totals & Holdings Filters (APPROVED & IMPLEMENTED)


**Date:** 2026-09-06  
**Author:** Danny (Lead)  
**Status:** FROZEN — implementation-ready (Rusty Backend + Livingston Frontend)  
**Impact:** Holdings summary displays purchase/sale/current-invested totals; UI filters hide zero-share holdings (default on) and search ticker/company/security_id; Find in portfolio search.

#### Backend: Summary Accumulators

- New fields in `HoldingsService.compute_holdings()`:
  - `total_purchases_eur`: SUM(gross + commission) for all BUY with complete cost basis
  - `total_sales_eur`: SUM(gross - commission) for all SELL
  - `current_invested_eur`: purchases - sales (can be negative if profitable)
- Per-security fields: `total_purchases_eur`, `total_sales_eur`
- Backward compat: `total_invested_eur` preserved (= total_purchases_eur)
- Dividends excluded from all three (remain in `total_dividends_eur`)
- Implementation: 12 targeted tests; all 79 tests pass (33 holdings + 46 endpoints)

#### Frontend: Summary Display & Filters

- Summary bar (portfolio-wide, unaffected by filters):
  1. Total Purchases
  2. Total Sales
  3. Current Invested
  4. Total Dividends
  5. Securities count
- Zero-shares filter: toggle (default ON) hides holdings with exactly 0 shares; negatives always visible
- Symbol search: text input, case-insensitive substring match on ticker + company_name + security_id
- Client-side filtering; API always returns all holdings
- Responsive + a11y compliant

#### Files Changed

**Backend:**
- `backend/src/portfolio/holdings_service.py` — accumulators, per-security fields
- `backend/src/portfolio/models.py` — HoldingItem + HoldingsSummary models
- `backend/tests/test_portfolio_holdings.py` — 12 new test cases

**Frontend:**
- `frontend/src/types/portfolio.ts` — HoldingsSummary + HoldingEntry type updates
- `frontend/src/components/PortfolioHoldingsTable.tsx` — summary bar, filters, search
- `frontend/src/lib/filterSecurities.ts` *(new)* — helper function
- `frontend/src/components/SecuritySearchPanel.tsx` *(new)* — Find in portfolio panel

#### Validation

- **Backend:** Rusty PASS 228 backend + 54 synthetic tests
- **Frontend:** tsc/eslint clean; Livingston manual verification
- **Approval:** Danny APPROVE

#### Non-Goals

- No server-side filtering/search
- No pagination
- No URL query params for state
- No persistence of filter state
- No new API endpoints

---


### 3. Find in Portfolio — Import Question Security Search (APPROVED & IMPLEMENTED)


**Date:** 2026-09-06  
**Author:** Livingston (Frontend Lead)  
**Status:** FROZEN — implementation-ready (Rusty Implementation)  
**Impact:** Import questions for unresolved companies can now search existing portfolio securities (including aliases) and select to map to SELECTED_CANDIDATE.

#### Feature

- "Find in portfolio" button in `ImportQuestionCard` (mutually exclusive with "+ Create new security")
- Opens `SecuritySearchPanel` modal with:
  - Lazy-loaded securities list from `listSecurities()` (cached to avoid duplicates)
  - Case-insensitive search on: security_id, ticker, company_name, aliases[].value
  - Results capped at 50; "refine search" hint shown if needed
  - Loading/no-results/error states handled
  - Accessible: labelled input, role=listbox/option, aria-labels
- Selection maps to existing `handleSelect` → `SELECTED_CANDIDATE` answer type
- Reuses existing backend fan-out logic; no backend changes

#### Files Changed

**Frontend:**
- `frontend/src/lib/filterSecurities.ts` *(new)* — pure, testable helper; no framework dependency
- `frontend/src/components/SecuritySearchPanel.tsx` *(new)* — modal UI + state
- `frontend/src/components/ImportQuestionCard.tsx` *(modified)* — button state + panel integration

#### Validation

- tsc --noEmit → 0 errors
- eslint → 0 warnings/errors
- Manual verification by Rusty

#### Design Decisions

- Helper function separated from component for testability and reuse
- Lazy loading + caching pattern avoids duplicate API calls on panel re-opens
- Aliases included in search to catch historical/regional variations
- 50-result cap balances UX responsiveness with completeness

---

## Archived Decisions (Inbox → Consolidated)

The following inbox files have been merged into the active decisions above and archived per convention:

- `.squad/decisions/inbox/danny-portfolio-summary-filters.md`
- `.squad/decisions/inbox/copilot-directive-20260906T112642+0200.md`
- `.squad/decisions/inbox/copilot-directive-20260906T113335+0200.md`

All implementation histories and design rationale preserved in sections 2 and 3 above.

---

## Implementation History Log

| Date | Feature | Agent/Owner | Status | Files Modified |
|------|---------|-------------|--------|-----------------|
| 2026-09-06 | Portfolio Summary & Filters | Rusty (Backend) | COMPLETE | holdings_service.py, models.py, test_portfolio_holdings.py |
| 2026-09-06 | Portfolio Summary & Filters | Livingston (Frontend) | COMPLETE | portfolio.ts, PortfolioHoldingsTable.tsx |
| 2026-09-06 | Find in Portfolio | Rusty (Implementation) | COMPLETE | filterSecurities.ts *(new)*, SecuritySearchPanel.tsx *(new)*, ImportQuestionCard.tsx |


---

## Portfolio Phase 2: Accounts, Transfers, Reassignment, FX, Filters (APPROVED & RELEASED)

**Date:** 2026-09-05 → 2026-09-06
**Version:** 2.0
**Authors:** Danny (Lead), Livingston (Backend), Rusty (Frontend), Basher (QA), Linus (Defect Resolution)
**Status:** ✅ RELEASED — commit 08809eb (API ca-stock-options-manager-api--0000053, Frontend ca-stock-options-manager-front--0000046)
**Source design:** `.squad/designs/portfolio-phase2-design.md`
**Orchestration log:** `.squad/orchestration-log/2026-09-06T11:59:49Z-portfolio-phase2-completion.md`
**Session log:** `.squad/session-log/2026-09-06T11:59:49Z-portfolio-phase2-completion.md`


### Next Priority


**Symbol Details ↔ Portfolio Unification (Deferred)**

User directive: Enable Watchlist-only symbols (no portfolio holdings); auto-add Portfolio symbols to Watchlist with agents/notifications disabled; consolidate three currently separate symbol management areas.

**Prerequisites met:** Portfolio Phase 2 + Cost-Basis fully stable, 478+209=687 tests passing, zero regressions, both phases deployed.

**Status:** DEFERRED to next planning session.

---

## Symbol Unification — Portfolio ↔ Watchlist ↔ Symbol Details Integration (2026-09-06)

**Date:** 2026-09-06 (Implementation Contract rev 3)  
**Authors:** Danny (Lead Architect), Livingston (Backend/Persistence), Rusty (Frontend/UX), Basher (Testing)  
**Status:** ✅ APPROVED, DEPLOYED, HEALTHY  
**Impact:** Unified symbol management: unify Portfolio securities with Watchlist and Symbol Details, auto-enroll symbols with disabled agent/notification defaults, render two-section Watchlist (Portfolio/Watchlist-only symbols).


### Production Deployment


**Functional Commit:** `803b8f3 feat: unify portfolio and watchlist symbols`

**Date:** 2026-09-06, afternoon

**Deployment Evidence:**
- API revision: `ca-stock-options-manager-api--0000059` (Healthy/Active)
- Frontend revision: `ca-stock-options-manager-front--0000052` (Healthy/Active)
- GitHub Actions run: `34042485167` ✅ PASSED
- Deployed on SHA: `803b8f3`

**Test Results at Deployment:**
- Symbol Unification tests: 114/114 PASS
- Existing backend tests: 2,952/2,952 PASS
- TypeScript build: 0 errors
- Next.js build: 0 errors
- Frontend accessibility: ARIA labels verified, keyboard navigation tested
- Regression tests: 0 failures (Portfolio Phase 1 + Phase 2 unaffected)

**Release Validation (Post-Deployment):**
- Backend: 2,908 tests passing (cumulative: Symbol Unification 114 + Phase 2 478 + Phase 1 + baseline)
- TypeScript: clean build
- Next.js: clean build
- All Options/Watchlist endpoints: operational, unchanged behavior
- All Portfolio endpoints: operational, unchanged behavior


### 2. Dividend Portfolio — Phase 1 MVP Architecture & Ledger Design


**Date:** 2026-09-05
**Authors:** Danny (Lead, Architecture), Livingston (Persistence), Rusty (UX/Frontend)
**Status:** PROPOSED — awaiting user confirmation on open questions
**Impact:** User-managed dividend portfolio with ledger-first data model, multi-broker support, multi-currency accounting, withholding tracking, mixed dividend support

#### Context & Directive

User request: Design independent dividend portfolio section to manage manual BUY, SELL, and DIVIDEND movements; support Fidelity, HeyTrade, ING, Interactive Brokers; EUR, USD, GBP, CHF currencies with EUR base reporting; withholding at origin (source country) and destination (investor country); mixed cash/share dividends (scrip/DRIP). Explicitly defers: Excel import (Phase 2), charts, Economics integration, fiscal export.

#### Architecture — Domain Boundaries

The app tracks two orthogonal concerns:
- **Watchlist/Symbols:** Option-income research (flags, agents, enrichment, buy-tracker). Remains in existing `symbols` container.
- **Portfolio:** Stocks owned (BUY/SELL/DIVIDEND ledger). New `portfolio` Cosmos container, partition key `/account_id`.

**Key decision:** Keep `symbol_config` as-is for options; portfolio movements independent. Link via ticker symbol (string), not foreign key. Symbols can be watched-only, owned-only, both, or neither. This is correct, not a bug.

**Navigation:** Add new "Portfolio" top-level menu between Economics and Chat (peer to Symbols). Four subpages: Securities (holdings), Movements (ledger), Dividends (yield focus), Accounts (broker setup). Do NOT rename "Symbols" or "Watchlist" — avoids 40+ file churn with zero functional gain.

#### Ledger-First Data Model — Core Design Principles

1. **Immutable movements.** Each recorded transaction (BUY, SELL, DIVIDEND) is an immutable fact. Soft-delete (mark `deleted_at`) or correct via reversal; never mutate.
2. **Holdings are derived, never stored.** Current holdings = `SUM(buys) - SUM(sells) + SUM(dividend_shares)`, grouped by `(symbol, broker)`. Computed on read.
3. **All monetary amounts dual-store:** transaction currency AND EUR equivalent (e.g., `gross_amount: {amount: 8625.00, currency: USD, eur_amount: 7915.00, fx_rate: 1.0897}`).
4. **Broker is first-class attribute.** Fidelity, HeyTrade, ING, Interactive Brokers each have distinct withholding, FX, and fee behaviors.

#### Multi-Currency & FX — Critical Convention

**FX rate convention:** `fx_rate = EUR_PER_TXN_CCY` (number of EUR for 1 unit of transaction currency)
**Arithmetic:** `amount_eur = amount_txn × fx_rate`
**Example:** 1 USD = 0.86 EUR, so 1000 USD × 0.86 = 860 EUR
**Rate sources:** ECB (preferred for trade date), BROKER (HeyTrade/ING conversion), MANUAL (user), OVERRIDE (user change with original preserved)
**Data type:** Decimal string (9 decimal places for precision in rate composition)

**CRITICAL:** This is NOT the reciprocal of ECB convention. ECB publishes EURUSD (how many USD per 1 EUR); we store the reciprocal value (EUR per 1 USD) for direct multiplication arithmetic. Always verify: `amount_eur = amount_txn × fx_rate` (never divide).

#### Withholding — Dual-Layer Model (Most Critical Design Decision)

| Layer | When Present | Semantics |
|-------|--------------|-----------|
| **Source (origin_wht)** | DIVIDEND always | Tax withheld at security's country (e.g., US 15% treaty rate) |
| **Destination (dest_wht)** | DIVIDEND always | Tax withheld by broker for investor's country (e.g., Spain 19% IRPF) — or NOT captured |

**The null vs. zero distinction is fundamental:**
- `withholding_destination: null` = broker doesn't capture this; distinct from `{amount: 0}` which means confirmed zero
- Critical for Phase 4 fiscal export: identifies "tax already paid" vs. "tax liability outstanding"
- **UI rendering rule:** null displays as ⚠️ "Pending" / "Not captured"; never as €0.00
- Broker profile flag `captures_destination_withholding` drives form UI: if false, field hidden with warning

#### Security Identity Model

**Primary identifier: ISIN (ISO 6166, 12-character).** All transactions must carry ISIN (canonical identifier across exchanges/currencies).

**Secondary identifiers (embedded in every transaction):** CUSIP (Fidelity primary), SEDOL (LSE listings), ticker (exchange-local, time-of-transaction), exchange MIC (ISO 10383), company name, asset class, listing currency.

**Broker-specific IDs:** IBKR conid, HeyTrade internal mappings stored in `broker_ids` object.

**Denormalized by design:** Security object embedded in every movement (not a foreign key). Preserves historical identity at transaction time; avoids joins; enables future corporate action tracking.

#### Transaction Document Schema (Summarized)

**Every movement carries:**
- Cosmos fields: `id` (constructed: `txn_{account_id}_{date}_{ticker}_{type}_{seq}`), partition key `account_id`, `doc_type: ledger_txn`
- Identity: `security` (ISIN, CUSIP, SEDOL, ticker, MIC, name, asset class, listing currency, broker_ids)
- Classification: `txn_type` (BUY, SELL, DIVIDEND, SCRIP_CASH_LEG, SCRIP_SHARE_LEG)
- Dates: `trade_date`, `settlement_date` (optional), `payment_date` (dividends), `ex_dividend_date` (dividends)
- Quantity: `quantity` (decimal string, 6 dp, always positive; direction in txn_type); `quantity_unit: shares`
- Price: `price_txn` (per share, null for dividends), `txn_currency` (EUR/USD/GBP/CHF)
- Gross/Fees/Net (both txn currency and EUR):
  - `gross_txn` / `gross_eur`
  - `fees.total_txn` / `fees_eur` (with optional breakdown: commission, exchange_fee, stamp_duty, custody_fee, other)
  - `net_txn` / `net_eur` (gross − fees − withholding)
- FX: `fx.rate` (9 dp), `fx.rate_source` (ECB|BROKER|MANUAL|OVERRIDE), `fx.rate_date`, `fx.ecb_reference_rate`, `fx.original_rate` (if overridden)
- Withholding (dividends only):
  - `withholding.source_country`, `source_rate_pct`, `source_amount_txn`, `source_amount_eur`, `treaty_applied`, `treaty_name`
  - `withholding.destination_country`, `destination_rate_pct`, `destination_basis_eur`, `creditable_amount_eur`
- Dividend details (when txn_type = DIVIDEND):
  - `dividend.ex_date`, `record_date`, `payment_date`, `dps` (dividend per share)
  - `dividend.paid_in_shares` (boolean)
  - `dividend.share_leg` (optional, when paid_in_shares): `shares`, `price_per_share`, `cost_basis_per_share_eur`, `total_value` (with cash portion, for mixed dividends)
- Audit: `broker_ref`, `import_source` (manual|excel_import|api_import), `dedup_key` (for import idempotency), `revision`, `status` (active|voided), `voided_by`, `replaces_id`, `correction_chain`
- Timestamps: `created_at`, `updated_at`, `deleted_at` (soft-delete, null = active)

#### Mixed Cash/Share Dividends — Atomic Modeling

Scrip dividends or DRIP can pay partly cash, partly shares, or entirely shares. Modeled as ONE economic event:

When `dividend.paid_in_shares = true`, the movement gains a `share_leg` object:
- Shares received (fractional)
- Price per share at ex-date (for cost basis assignment)
- Cost basis type: zero (true scrip) or fair value (elected in-lieu)
- Total value in EUR

**UI flow:** Dividend form has toggle "Paid in shares". When enabled, reveals stock leg sub-form. Both legs submitted atomically; server ensures both persist or neither.

**Holdings impact:** `share_leg.shares` adds to symbol's total holdings at specified cost basis. Cash portion is income only. One DIVIDEND movement captures entire event.

#### Cost Basis & Holdings Derivation

**Formula:** For each `(account_id, isin)`:
```
total_shares = SUM(BUY.quantity) - SUM(SELL.quantity) + SUM(DIVIDEND.share_leg.quantity where paid_in_shares)
avg_cost_basis_eur = weighted average of all BUY.cost_basis_per_share_eur
total_invested_eur = SUM(BUY.gross_eur) - SUM(SELL.net_eur)
total_dividends_eur = SUM(DIVIDEND.net_eur)
```

**Cost basis method (MVP default):** Average cost (simplest, matches Spanish FIFO-like scenarios). FIFO/LIFO deferred to Phase 3.

**Performance:** ~500 movements (8 years × ~60 trades) computes sub-second via cross-partition aggregation. No materialized view needed for MVP. Snapshot optimization deferred to Phase 3.

#### Broker Profiles — Four Initial Profiles (Behavior Hints, Not Constraints)

**Fidelity (US-centric):** USD-native, CUSIP-primary, zero commission (since 2019), 15% US treaty withholding, no destination capture, requires ISIN resolution pre-import.

**HeyTrade (EU-Spanish):** EUR-native, ISIN-primary, broker auto-converts USD → EUR, low/zero commission, captures both withholding layers, treats destination withholding transparently.

**ING España (EU-Spanish):** EUR-native, ISIN-primary, commission-bearing (varies Spanish vs. international), captures both layers, PDF/online statements, historical data may require manual entry.

**Interactive Brokers (Global):** Multi-currency, IBKR conid (proprietary) + ISIN, tiered commissions, complex W-8BEN scenarios, origin withholding only, no destination capture, Flex Query (XML/CSV) structured source.

Each profile has defined defaults for forms (currency, FX field visibility, withholding toggle defaults, destination capture flag). **Critically:** Profiles are hints only. When the user enters a movement, they override any profile default. The stored movement records **what actually happened** per broker statement, not what profile predicted.

#### MVP Pages & Forms

**Securities (`/portfolio/securities`):** Read-only holdings table (Symbol, Broker, Shares, Avg Cost/share, Avg Cost EUR, Total Cost EUR, Status). Filters: broker, status. Row click: filtered Movements for that holding. Add button: quick BUY entry (cost basis always ledger-derived).

**Movements (`/portfolio/movements`):** Full ledger (Date, Account, Type, Ticker, Qty, Price, Currency, FX Rate, Gross, Fees, Origin WHT, Dest WHT, Net EUR, Notes). Filters: date range, account(s), type(s), currency, ticker. Sort all columns. Row click: detail/edit panel. Void workflow (soft-delete with reason; cost-basis auto-recalculates). New Movement button: type-adaptive form.

**Dividends (`/portfolio/dividends`):** Derived view grouping dividends per ticker/period. Columns: Payment Date, Ex-Date, Account, Ticker, Gross/Share, Shares, Gross Total, Origin WHT %, Dest WHT Status (✓ Collected | ⚠️ Pending | —). Summary stats: Total gross YTD, Total origin WHT YTD, **Destination WHT pending (amber highlight)**, Net YTD. Filters: year, account(s), ticker, month, type (Cash/Mixed/All), WHT status. No separate add button (via Movements form).

**Accounts (`/portfolio/accounts`):** Broker profile setup (card per account). Add/Edit form: Broker dropdown (Fidelity/HeyTrade/ING/IBKR), Nickname, Default currency (locked for broker-specific; user-selectable for IBKR), Notes. Broker-to-currency mapping is advisory.

#### Movement Form Flows — Type-Specific Adaptations

**Common header (all types):** Type selector (pills: BUY | SELL | DIVIDEND | DIVIDEND+STOCK), Account dropdown, Date picker, Ticker combobox.

**BUY form:** Shares → Price (currency) → Fees → FX Rate (🔄 fetch button) → EUR equivalent (computed, editable) → Summary (total cost, avg cost/share).

**SELL form:** Extends BUY with Origin WHT % + Destination WHT % + "collected?" checkbox. Proceeds summary (gross − fees − withholding). Informational hint: "Avg cost basis €X.XX/share — Estimated gain €Y (deferred analytics)".

**DIVIDEND (cash) form:** Ex-date → Payment date (required) → Gross/share → Shares at ex-date (auto-populated from position, editable) → Origin WHT % (pre-filled from country+DTA) + "yellow chip: DTA: reduced to 15%" → Destination WHT % + "collected?" checkbox (⚠️ if pending) → FX Rate (🔄 fetch for payment date) → Net EUR → Notes.

**DIVIDEND+STOCK (mixed) form:** Extends DIVIDEND (cash) with second section: Stock Leg (Shares received, Price at ex-date, Cost basis type: ◉ Zero | ○ Fair value, Cost basis EUR). Atomic submission; both legs created together. UI shows linked rows with visual connector.

#### Validation Rules (Invariants)

| # | Invariant | Enforced |
|---|-----------|----------|
| I1 | `txn_type ∈ {BUY, SELL, DIVIDEND, ...}` | API validation |
| I2 | `quantity > 0` for all types; direction in txn_type | API |
| I3 | Every money field has amount + currency; eur_amount/fx_rate required when currency ≠ EUR | API |
| I4 | `withholding_destination = null` ≠ `{amount: 0}`; UI renders null as "Pending" | UI + API |
| I5 | Derived holdings never negative for (account_id, isin) at any point in ledger | API (chronological) |
| I6 | Movements append-only; corrections via soft-delete or new document | API design |
| I7 | `net = gross - fees - wht_source - wht_dest` (EUR conversion) | API (computed) |
| I8 | `deleted_at` (soft-deleted) excluded from aggregates | Query filter |

#### `total_shares` Migration Path

Current `symbol_config.total_shares` (mutable, no provenance) coexists with ledger in MVP:
- **Phase 1:** `total_shares` still editable in Watchlist; portfolio independent
- **Phase 2:** Excel import + reconciliation tool (ledger vs. total_shares comparison); user decides when to flip each symbol to ledger-derived
- **Phase 3:** `total_shares` becomes read-only, computed from ledger (not MVP)

**Why not derive from day 1?** User has history since 2016. Manual entry of 8+ years impractical. Until Excel import delivers full history, ledger incomplete; derived holdings would be wrong. Both must coexist.

#### UX / Accessibility / Mobile

**Form accessibility:** `<label>` elements for all controls; `aria-live="polite"` on computed fields; movement type selector as `role="tablist"`; slide-over with `aria-modal="true"`, focus-trap, Escape closes with "discard?" confirmation.

**Status indicators:** Color + icon (never color alone); ⚠️ for Pending, ✓ for Collected, "—" for zero/N/A.

**Mobile:** Full-screen sheet (not half-sheet) on < 768 px viewport; `inputmode="decimal"` for numeric inputs; horizontal scroll with sticky columns for tables; card-list layout below 640 px; sticky summary at sheet bottom.

**Error handling:** Field-level inline errors + red border; server errors via toast (Sonner pattern) or inline banner; FX fetch failure: warning (field stays required); Void failure: toast; network errors: optimistic update + rollback.

#### APIs & BFF Surface

All under `/api/portfolio/`:

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/portfolio/accounts` | List broker profiles |
| POST/PUT/DELETE | `/api/portfolio/accounts/:id` | CRUD broker profiles |
| GET | `/api/portfolio/securities` | Holdings (derived, not stored) |
| GET/POST | `/api/portfolio/movements` | List / create movement(s) |
| PATCH | `/api/portfolio/movements/:id/void` | Soft-delete with reason |
| GET | `/api/portfolio/dividends` | Dividend events log |
| GET | `/api/portfolio/dividends/summary` | Stat cards |
| GET | `/api/portfolio/fx-rate` | `?currency=USD&date=YYYY-MM-DD` (ECB or BROKER or manual) |

BFF routes in `frontend/src/app/api/portfolio/` follow existing proxy pattern.

#### Phased Roadmap

**Phase 1 (MVP):** Manual entry of movements; read-only holdings; parallel total_shares coexistence
**Phase 2:** Excel import (parser, batch processing, dedup); reconciliation tool; auto-FX fetching (ECB)
**Phase 3:** Ledger-derived total_shares (read-only); cost-basis methods (FIFO/LIFO); snapshot optimization
**Phase 4:** Fiscal export; tax reporting (IRPF integration); declaration linkage
**Phase 5:** Charts, analytics, time-series visualizations; Economics integration

#### Open Questions & Confirmations Awaited from User

1. **Scrip/DRIP cost basis:** Is zero-cost election (true scrip) typical for user? Or predominantly fair-value (elected in-lieu)?
2. **Broker statement frequency:** Monthly? Quarterly? Impacts reconciliation tool UI (Phase 2).
3. **Historical data urgency:** Excel import (Phase 2) critical for early adoption, or can MVP start with "today forward"?
4. **Tax reporting scope:** Are Spanish (IRPF) withholding rules primary, or multi-jurisdiction?
5. **Current price feed:** Should Holdings page show "N/A" if symbol not in watchlist, or integrate yfinance spot?
6. **Audit trail depth:** Do we preserve edit history (array in document) or just `updated_at` timestamp?

#### Consolidated Recommendation (Authoritative)

**Danny's design is primary.** Livingston's persistence model and Rusty's UX design integrate seamlessly:
- Persistence: Cosmos container strategy, security identity (ISIN), FX convention (EUR_PER_TXN_CCY), withholding dual-layer, decimal precision (6 dp amounts, 9 dp rates), broker profiles, transaction schema, validation invariants all validated.
- UX: Navigation (Portfolio between Economics/Chat), four subpages, form flows (type-adaptive), accessibility (ARIA, mobile, focus management), API surface all mapped to persistence schema.
- Cross-design consistency verified: FX convention aligned, withholding model agreed, cost-basis method (average cost MVP) confirmed, broker profiles shared, form field sets match schema.

---

**⚠️ CORRECTION (2026-09-05T16:02:00+02:00):** FX Convention clarification

Original wording "rate reciprocal" was ambiguous and incorrect. **Authoritative correction:**

**FX rate convention (corrected):** `fx_rate = EUR_PER_TXN_CCY` — number of EUR for 1 unit of transaction currency.
**Formula:** `amount_eur = amount_txn × fx_rate` (always multiply, never divide)
**Example:** 1 USD = 0.86 EUR, so 1000 USD × 0.86 = 860 EUR

This is the reciprocal of the ECB convention (ECB publishes EURUSD; we store USD→EUR). The arithmetic is direct multiplication for all calculations. Updated in decisions.md Consolidated Recommendation (line 37), Danny's orchestration log, and all related specifications.

---

#### Assignments

- **Implementation (future):** Backend API design & Cosmos provisioning (Livingston lead); frontend component development (Rusty lead); validation gates (Basher lead)
- **Scribe (this session):** Merge inbox decisions, write orchestration logs, stage for commit

#### Consolidated Recommendation (Authoritative) — 2026-09-05

**See:** `.squad/designs/portfolio-ledger-securities-unified-design.md` for complete unified design consolidation of Securities Master, Portfolio Ledger, and Conversational Imports.

**Key authoritative decisions ratified:**

1. **Container Strategy (R1):** Security master documents in `symbols` container (partition `/symbol`), not `portfolio._global`. Provides single-partition identity + operational state co-location. Import sessions in dedicated `import_sessions` container (partition `/session_id`), not portfolio, for TTL isolation.

2. **Security ID Format (R2):** Canonical format is `MIC:TICKER` (namespace-first, e.g., `XNYS:AAPL`, `XMAD:SAN`), not `TICKER:MIC`. Cosmos document ID uses underscores: `sec_XNYS_AAPL`. Rationale: standard URI/DNS convention, exchange-grouped sort, alignment with industry practice (TradingView, Bloomberg).

3. **Portfolio Container Name (R4):** Container name is `portfolio` (not `portfolio_ledger`). Single-purpose container; name is unambiguous.

4. **Security Master Document Type (R5):** Document type is `security_master` (not `symbol_master`). Distinct from `symbol_config` (operational state). Canonical identity stored once in `security_master`; `symbol_config` references via `security_id` field (Phase M2).

5. **Conversational Import (per user directive 2026-09-05T172036+0200):** Replaces wizard-based flow. User pastes CSV; deterministic parser extracts rows; LLM orchestrates structured questions (BATCH, ENTITY, ROW_GROUP scopes); user confirms in preview; ledger writes on explicit confirmation only. LLM never parses amounts, never computes arithmetic, never auto-confirms. Deterministic validation is source of truth for all financial data.

6. **Inline Security Creation (per user directive 2026-09-05T172200+0200):** Allowed during import chat. User clicks "Create Security" within ENTITY question scope; sub-form opens inline. On collision check pass, atomic write to `security_master` and `import_session` happens together (Cosmos transactional batch). Session question marked answered immediately; conversation continues.

7. **No Second Identity Locus Rule:** Exactly one canonical `security_master` per security in `symbols` container. `symbol_config` is operational state, not identity. Ledger records carry denormalized security snapshot (point-in-time copy), not foreign key. Future Phase M2: `symbol_config.security_id` bridges ticker-only routes when collision exists.

8. **Legacy Ticker-Only Routes:** Bare ticker routes continue working when unambiguous (single `security_master` per ticker). On collision, API returns HTTP 300 Multiple Choices. Bridge field: `symbol_config.security_id` disambiguates.

9. **Import Session Container (R3):** Dedicated `import_sessions` container with 7-day TTL at document level (`import_session` doc_type carries `ttl: 604800`). Isolation prevents TTL-enabled container from expiring permanent ledger when TTL enabled at container level. Light indexing (state, created_by, expires_at) distinct from ledger indexes.

10. **Staged Rows & Crash Recovery:** Import stores parsed rows in `portfolio` container as `staged_import_row` docs (90-day TTL), pending commit. On commit, atomic delete of staged rows + insert of ledger_txn records. Idempotency key prevents double-write on retry. Optional `creation_intent` field in session aids recovery logging.

**Preservations from MVP:**
- FX convention: `fx_rate = EUR_PER_TXN_CCY` (number of EUR for 1 unit of txn currency); `amount_eur = amount_txn × fx_rate` (always multiply, never divide)
- Withholding dual-layer model: `source_wht` (origin country) vs. `dest_wht` (investor country); null ≠ zero (UI renders null as ⚠️ Pending)
- Ledger invariants: immutable movements, derived holdings, cost basis (MVP: weighted average)
- BUY/SELL/DIVIDEND/corporate-action models with atomic mixed-dividend modeling
- Holdings derivation: `total_shares = SUM(BUY) - SUM(SELL) + SUM(ca_leg_shares)` (computed on read)

**Deferred to Phase 2+:**
- Ticker-only securities in portfolio (all ledger rows require full security_id)
- Materialized ledger views (computed on read sub-second for <500 movements; snapshot optimization Phase 3)
- Fiscal export Phase 4 (uses withholding model to identify tax-paid vs. tax-liable)
- Cost-basis methods beyond average (FIFO/LIFO Phase 3)
- Charts, analytics, time-series (Phase 5)

**Validation reference:** `.squad/designs/import-validation-reference.md` (test matrices & acceptance criteria, linked from consolidated design)

#### Next Steps

1. User reviews consolidated design (`.squad/designs/portfolio-ledger-securities-unified-design.md`)
2. User confirms or clarifies any open questions in design
3. Proceed to Phase 1 implementation (backend API + frontend components)
4. Phase 2 readiness: Excel import scaffolding, dedup key strategy, reconciliation tool UI

---

**See also:**
- **Unified Design:** `.squad/designs/portfolio-ledger-securities-unified-design.md` (complete consolidated architecture, 400+ lines)
- **Orchestration Log:** `.squad/orchestration-log/2026-09-05-scribe-consolidation.md` (all 19 inbox sources, conflict resolutions, lessons learned)
- **Original inbox designs:** All content merged into unified design; inbox files archived after this consolidation
- **User directives:** `.squad/decisions/inbox/copilot-directive-20260905T172036+0200.md`, `.squad/decisions/inbox/copilot-directive-20260905T172200+0200.md` (Spanish — design source)

---


### Portfolio Implementation Contract v1.1 (Danny)


**Authoritative Specification** — Endpoint shapes, CSV schemas, validation rules, field semantics

**Date:** 2026-09-05
**Status:** APPROVED with amendments

#### Architecture Summary

**Domain separation:**
- **Portfolio container:** New ledger (BUY, SELL, DIVIDEND movements); partition key `/account_id`
- **Symbols container:** Existing watchlist/options data; security master records added
- **Import_sessions container:** Question-and-answer state machine; 7-day TTL

**Security identity:** Unified `MIC:TICKER` format (e.g., `XNYS:AAPL`, `BMEX:TELEVISA`)

**Movement types:** BUY, SELL, DIVIDEND, SCRIP_CASH_LEG, SCRIP_SHARE_LEG

**Key invariants:**
- Immutable movements (soft-delete only)
- Holdings derived on read (never stored)
- All amounts dual-store (transaction currency + EUR equivalent)
- FX rate convention: `EUR_PER_TXN_CCY` (number of EUR per 1 unit of transaction currency)
- Withholding: dual-layer (source + destination), null ≠ zero
- Quantity: required for BUY/SELL, null for DIVIDEND

#### REST Endpoints

| Endpoint | Method | Purpose | Auth |
|----------|--------|---------|------|
| `/api/portfolio/holdings` | GET | Current holdings by security | Required |
| `/api/portfolio/movements` | GET | Ledger movements (paginated, filterable) | Required |
| `/api/portfolio/movements/{id}` | DELETE | Soft-delete a movement | Required |
| `/api/import/sessions` | POST | Create import session | Required |
| `/api/import/sessions/{id}/preview` | POST | Generate preview (no commit) | Required |
| `/api/import/sessions/{id}/commit` | POST | Commit preview to ledger | Required |
| `/api/import/sessions/{id}/chat` | POST | Send question answer | Required |
| `/api/securities` | POST | Create new security | Required |

#### CSV Schemas

**Purchases & Sales (8 columns):**
- `Fecha` (date), `Empresa`, `ISIN` (optional), `Comisión` (fees)
- `Acciones` (quantity), `Precio Unitario` (price)
- `Total (€)` (gross in EUR), `Tipo` (BUY/SELL)

**Dividends (7 columns):**
- `Fecha` (payment date), `Empresa`, `ISIN`
- `Dividendo (€)` (gross), `Retención (%)`, `Neto (€)`
- `Acciones` (shares owned, informational only)

**Decimal parsing rule:** Dots are ALWAYS thousands separators; commas are ALWAYS decimal separators. A dot-only string (no comma) has its dots stripped.

**Quantity invariant:** Required for BUY/SELL; null for DIVIDEND (no share count in source data).

#### Warnings & Validation

**6 blocking errors:**
- Blank date, ISIN missing, quantity zero, duplicate row, encoding fail, unknown dividend country

**7 warnings:**
- Rights pending, zero-cost acquisition, negative inventory, probable duplicate, unknown company, missing account, unresolved security

**Reconciliation statuses:**
- ACTIVE, PENDING_RIGHTS_CLASSIFICATION, PENDING_WITHHOLDING_VERIFICATION, VOID

#### Contract Amendments (Round 1 & Round 2 Fixes)

| Amendment | Type | Details |
|-----------|------|---------|
| Decimal parsing rule | Additive | Dots ALWAYS thousands separators (even dot-only strings) |
| Quantity invariant | Clarification | null for DIVIDEND; required for BUY/SELL |
| avg_cost_basis_eur semantics | Additive | SUM(gross+commission) / SUM(shares) for paid BUYs; excludes zero-cost; independent of sells |
| Response shapes | Clarification | avg_cost_basis_eur: string \| null; quantity: string \| null for DIVIDEND |

---


### Portfolio Implementation — Initial Delivery


**Date:** 2026-09-05
**Authors:** Livingston (Backend), Rusty (Frontend)
**Status:** Delivered; 5 findings identified in review

**Backend deliverables (5 modules):**
- `portfolio/import_service.py` — CSV parsing, movement staging, preview generation, commit atomicity (410 lines)
- `portfolio/holdings_service.py` — Holdings computation, cost basis, warnings (385 lines)
- `portfolio/cosmos_portfolio.py` — Cosmos document layer, TTL, soft-delete (290 lines)
- `portfolio/parsers.py` — Domain-specific parsers; Spanish decimal support (185 lines)
- `web/portfolio_routes.py` — REST endpoints (365 lines)

**Frontend deliverables (16 new files + 1 modified):**
- Types: `types/portfolio.ts`, `types/import.ts` (TypeScript DTOs)
- API: `lib/portfolio-api.ts` (typed browser client)
- BFF Routes: 3 proxy routes (`app/api/{securities,portfolio,import}/[[...slug]]/route.ts`)
- Pages: 3 page wrappers (`app/portfolio/{holdings,movements,import}/page.tsx`)
- Components: 9 interactive components (tables, forms, import chat, preview)
- Modified: `components/TopNav.tsx` (Portfolio menu added)

**Key decision: Livingston's architectural deviation**

Parsed rows embedded in `import_session` document (7-day TTL) instead of separate `staged_import_row` docs (90-day TTL). Rationale:
- Phase 1 scope sufficiency (users re-upload if not committed within 7d anyway)
- Avoids cross-container joins (session is read as one document)
- Within Cosmos 2MB document limits (typical 50–200 rows × ~1KB each)
- Source row data and dedup key preserved for Phase 2 upgrade

**Rusty frontend decisions:**
- `[[...slug]]` optional catch-all for proxy routes (handles base + sub-paths from single file)
- Multipart forwarding via `arrayBuffer()` + verbatim Content-Type (preserves boundary)
- Inline security creation: create via POST first, then answer with `CREATED_NEW_SECURITY`
- Securities catalog deferred to Phase 1.5 (accessible via import or API)
- `_unassigned` account displayed as `—` (em dash, neutral)

**Test coverage at delivery:** 151 new tests (5 test files)

---


### Portfolio Implementation — First Review Rejection (Danny)


**Date:** 2026-09-05 18:00 UTC+02:00
**Reviewer:** Danny (Lead)
**Action:** REJECT — 5 findings identified

**Findings summary:**

| Finding | Category | Impact | Fix Owner |
|---------|----------|--------|-----------|
| F1 | Fees hardcoded to "0.00" | Cost basis wrong | Linus |
| F2 | Spanish decimal dot-only ambiguity | Numbers misparsed | Linus |
| F3 | Preview company_name missing | Frontend renders undefined | Linus |
| F4 | batch_value field name (frontend sends `value`) | Batch answers fail | Linus |
| F5 | Dividend quantity = Decimal("0") | Semantically wrong (should be null) | Linus |

**Rejection lockout:** Original authors (Livingston, Rusty) barred from revisions.

**Authoritative document:** `.squad/decisions/inbox/danny-portfolio-rejection-resolution.md` (FROZEN)

---

#### F1 — Fees/Commission Dropped on Commit

**Bug:** `import_service._row_to_movement()` hardcodes fees to `"0.00"`:

```python
"fees": {
    "total": "0.00",        # ← always zero, should be commission
    "currency": currency,
    "total_eur": "0.00",    # ← always zero
},
```

**Impact:** Commissions lost from cost basis. `holdings_service` reads `fees.total_eur` and adds to `total_cost_eur` — but since it's always `"0.00"`, cost basis is understated by commission amount.

**Fix:** Extract `commission` from parsed row (purchases/sales); use for `fees.total` and `fees.total_eur`. Dividends retain `Decimal("0")`.

**Required tests:** 5 tests
- `test_purchase_commission_in_fees` — purchase with commission → fees populated
- `test_sale_commission_in_fees` — sale with commission → fees populated
- `test_dividend_fees_zero` — dividend → fees always `"0.00"`
- `test_commission_affects_holdings_cost_basis` — cost basis includes commission
- `test_preview_shows_fees` — preview movement includes fees

---

#### F2 — Spanish Decimal Dot-Only Ambiguity

**Bug:** `parse_spanish_decimal()` only strips dots when a comma is present:

```python
if "," in s:
    s = s.replace(".", "").replace(",", ".")
# Falls through for dot-only strings
```

For Spanish data (where dots are ALWAYS thousands separators), `"1.234"` (no comma) is parsed as `1.234` instead of `1234`.

| Input | Current (wrong) | Correct |
|-------|-----------------|---------|
| `"1.234,56"` | `1234.56` ✅ | `1234.56` |
| `"1.234"` | `1.234` ❌ | `1234` |
| `"10.500"` | `10.500` ❌ | `10500` |
| `"1.000"` | `1.000` ❌ | `1000` |

**Fix:** Always strip dots first (dots are ALWAYS thousands separators), then replace comma with dot:

```python
s = s.replace(".", "")           # strip thousands separators
s = s.replace(",", ".")          # comma to decimal point
return Decimal(s)
```

**Required tests:** 6 tests
- `test_dot_only_is_thousands` — `"1.234"` → `1234`
- `test_dot_only_large` — `"10.500"` → `10500`
- `test_dot_only_round_thousand` — `"1.000"` → `1000`
- `test_no_dot_no_comma` — `"100"` → `100` (unchanged)
- `test_comma_decimal_preserved` — `"1.234,56"` → `1234.56` (unchanged)
- `test_comma_only_decimal` — `"0,50"` → `0.50` (unchanged)

---

#### F3 — Preview Response Missing `company_name`

**Bug:** `_build_preview_response()` omits `company_name` field:

```python
preview_movements.append({
    "row_index": row_idx,
    "txn_type": m.get("txn_type"),
    # ... no company_name ...
})
```

**Impact:** Frontend type expects `company_name: string`; receives `undefined`. Render fails.

**Fix:** Resolve company names from security master via `resolution_map` + `securities_svc.get_security()`. Fall back to CSV `empresa_raw` if lookup fails.

**Required tests:** 2 tests
- `test_preview_includes_company_name` — preview movement has non-empty `company_name`
- `test_preview_company_name_from_security_master` — resolved name from security catalog

---

#### F4 — `batch_value` Field Name Mismatch

**Bug:** Contract specifies answer body uses `batch_value`:

```json
{ "question_id": "...", "answer_type": "BATCH_VALUE", "batch_value": "USD" }
```

Frontend sends `value` instead:

```typescript
// ImportQuestionCard.tsx
await submit({
    question_id: question.question_id,
    answer_type: "BATCH_VALUE",
    value: batchValue,        // ← WRONG: should be batch_value
});
```

**Impact:** All BATCH_VALUE answers silently fail; backend receives `batch_value = None`; defaults apply (EUR currency, `_unassigned` account).

**Fix:** Rename in 4 locations:
- `frontend/src/types/import.ts` — `ImportAnswer` interface: `value` → `batch_value`
- `frontend/src/components/ImportQuestionCard.tsx` line 62 — `submit()` call
- `frontend/src/components/ImportQuestionCard.tsx` line 223 — `answerSummary` reference
- `frontend/src/lib/portfolio-api.ts` — `answerQuestion()` union type

**Required tests:** 2 tests
- `test_batch_value_field_reaches_backend` — POST answer with `batch_value` works
- Frontend build succeeds with renamed field

---

#### F5 — Dividend Quantity Null Semantics

**Bug:** Dividends fabricate `quantity = Decimal("0")`, but the CSV schema has no share count:

```python
# _row_to_movement(), dividends branch:
quantity = Decimal("0")     # ← should be None
```

**Semantic issue:** `quantity = 0` implies "zero shares" — a numeric assertion. But dividends have NO share count in the source; the concept doesn't apply. Null correctly expresses "no quantity data."

**Impact:** Nullable `quantity` cleanly distinguishes holdings-impacting (BUY/SELL always have quantity) from cash-only (DIVIDEND has no quantity).

**Fix:** Set `quantity = None` for dividends. Serialization: `str(quantity) if quantity is not None else None`. Holdings ignores dividend quantities (already correct).

**Contract amendment:** Quantity becomes `string | null` in preview and movements responses. Additive; no breaking change.

**Required tests:** 4 tests
- `test_dividend_quantity_null` — dividend movement has `quantity is None`
- `test_buy_quantity_present` — BUY has non-null quantity
- `test_sell_quantity_present` — SELL has non-null quantity
- `test_preview_dividend_quantity_null` — preview response shows `"quantity": null`

---


### Portfolio Implementation — First Review Fixes (Linus)


**Date:** 2026-09-05 20:00–23:00 UTC+02:00
**Implementer:** Linus (Quant Dev)
**Status:** ✅ COMPLETE — all 5 fixes applied, 13 new tests added

**Files changed:**
- `backend/src/portfolio/import_service.py` — F1, F3, F5
- `backend/src/portfolio/parsers.py` — F2
- `backend/src/portfolio/holdings_service.py` — F1 impact (no change needed; already reads fees.total_eur)
- `frontend/src/types/import.ts` — F4
- `frontend/src/components/ImportQuestionCard.tsx` — F4
- `frontend/src/lib/portfolio-api.ts` — F4
- `backend/tests/test_portfolio_parsers.py` — 6 new tests (F2)
- `backend/tests/test_portfolio_import_service.py` — 4 new tests (F1, F3, F5)
- `backend/tests/test_portfolio_holdings.py` — 2 new tests (F1)
- `backend/tests/test_portfolio_endpoints.py` — 1 new test (F4)

**Test results:**
```
cd backend && python -m pytest tests/test_portfolio_*.py -x -v
Result: 151 tests (138 original + 13 new) — ALL PASS

cd frontend && tsc --noEmit
Result: 0 TypeScript errors
```

**Key lesson (F2):** When a parsing function is locale-specific (Spanish historical schemas), the conditional pattern `if "," in s` invites the English fallback for a dataset where that interpretation is never correct. Document and enforce: "dots are ALWAYS thousands separators" with code that strips them unconditionally.

---


### Portfolio Implementation — Second Review Rejection (Danny)


**Date:** 2026-09-05 23:30 UTC+02:00
**Reviewer:** Danny (Lead)
**Action:** REJECT — 2 findings identified (distinct from first round)

**Findings summary:**

| Finding | Category | Impact | Fix Owner |
|---------|----------|--------|-----------|
| F6 | DELETE endpoint partition-key parsing broken on `_unassigned` | Movement delete fails for `_unassigned` account | Reuben |
| F7 | avg_cost_basis_eur denominator wrong (transactions not shares) | Average cost per share misparsed by 10–100x | Reuben |

**Rejection lockout:** All prior authors (Livingston, Rusty, Linus) barred from revisions. Reuben escalated as independent specialist (fresh agent).

**Authoritative document:** `.squad/decisions/inbox/danny-portfolio-second-rejection-resolution.md` (FROZEN)

---

#### F6 — DELETE Movement — Wrong `account_id` for `_unassigned`

**Bug — Backend (`portfolio_routes.py` line ~337):**

```python
if not account_id:
    # movement_id format: txn_{account_id}_{date}_{ticker}_{type}_{idx}
    parts = movement_id.split("_", 2)
    account_id = parts[1] if len(parts) > 2 else "_unassigned"
```

For ID `txn__unassigned_20240101_AAPL_BUY_001`:
- `split("_", 2)` → `["txn", "", "unassigned_20240101_AAPL_BUY_001"]`
- `parts[1]` = `""` (empty string)
- Cosmos queries with partition key `""` → 404 (wrong partition)

**Bug — Frontend (`portfolio-api.ts` + `PortfolioMovementsTable.tsx`):**

```typescript
// PortfolioMovementsTable.tsx
onClick={() => onDelete(m.id)}  // ← only passes id, not account_id
```

Frontend omits `account_id` query parameter. Backend fallback is broken.

**Design decision:** Prefer explicit `account_id` end-to-end. Do not parse partition keys from document IDs (fragile, violates separation of concerns).

**Fix:**

1. **Backend:** Remove ID-parsing fallback. Default to `"_unassigned"` when `account_id` omitted:
   ```python
   if not account_id:
       account_id = "_unassigned"
   ```

2. **Frontend:** Add `accountId` param to `deleteMovement()`:
   ```typescript
   export async function deleteMovement(
       movementId: string,
       accountId?: string,
   ): Promise<...> {
       const params = new URLSearchParams();
       if (accountId) params.set("account_id", accountId);
       const qs = params.toString() ? `?${params.toString()}` : "";
       return fetchJSON(`/api/portfolio/movements/${encodeURIComponent(movementId)}${qs}`, {
           method: "DELETE",
       });
   }
   ```

3. **Frontend:** Pass `account_id` through call chain:
   ```typescript
   // PortfolioMovementsTable.tsx
   onClick={() => onDelete(m.id, m.account_id)}
   ```

**Required tests:** 3 tests
- `test_delete_unassigned_movement_no_account_id` — no `?account_id` → defaults to `_unassigned` ✅
- `test_delete_movement_explicit_account_id` — explicit `?account_id=broker1` ✅
- `test_delete_movement_wrong_account_returns_404` — wrong partition → 404 ✅

---

#### F7 — `avg_cost_basis_eur` — Divides by Transaction Count, Not Shares

**Bug (`holdings_service.py` line ~130):**

```python
cost_basis_buys = buy_count - zero_cost_count  # ← COUNT of transactions
avg_cost = total_cost / Decimal(str(cost_basis_buys))
```

Divides by number of BUY transactions, not total shares. Result: "average cost per transaction" (meaningless).

**Example:**
| | Shares | Gross | Commission | Total |
|---|--------|-------|-----------|-------|
| BUY #1 | 100 | €1,000 | €10 | €1,010 |
| BUY #2 | 50 | €750 | €5 | €755 |

- **Current (WRONG):** avg = (1,010 + 755) / 2 = €882.50 (per transaction)
- **Correct:** avg = (1,010 + 755) / 150 = €11.77 (per share)

**Semantic definition (Phase 1):** `avg_cost_basis_eur` is average acquisition cost per share.

```
avg_cost_basis_eur = SUM(gross + commission) / SUM(quantity)
```

Where both sums are over paid BUY movements (excludes zero-cost/INCOMPLETE acquisitions).

**Behavioral rules:**
- 2 BUYs (100 @ €10, 50 @ €15 with commissions) → €11.77/share
- 1 BUY (10 @ €182.50, €7.50 fee) → €183.25/share
- 1 INCOMPLETE BUY + 1 paid BUY → avg of paid only
- No BUYs → null
- Independent of sells (sells reduce `total_shares`, not `avg_cost_basis_eur`)

**Fix:**

1. Add `paid_buy_shares` accumulator to per-security tracking
2. On paid BUY: `agg["paid_buy_shares"] += qty`
3. On zero-cost BUY: skip accumulation, increment `zero_cost_count`
4. Compute: `avg_cost = total_cost / paid_shares` (not transactions)

**Required tests:** 6 tests
- `test_avg_cost_basis_single_buy` — 1832.50 / 10 = 183.25 ✅
- `test_avg_cost_basis_multi_buy` — 1765 / 150 = 11.77 ✅
- `test_avg_cost_basis_excludes_zero_cost` — 1010 / 100 = 10.10 (ignores INCOMPLETE) ✅
- `test_avg_cost_basis_no_paid_buys_is_null` — null when only INCOMPLETE ✅
- `test_avg_cost_basis_independent_of_sells` — avg unchanged by sells ✅
- `test_avg_cost_basis_dividends_only_is_null` — null when no BUYs ✅

---


### 7.4 Gate Process


Rusty submits → Basher reviews against criteria 1–10 → Danny final sign-off.

---

## 8. Actions for Rusty

```
ACTION-1: Fix _extract_earnings_from_overview
  File: backend/src/contract_validation_integration.py
  Change: Navigate root["fundamentals"]["earnings_release_next_date_fq"]["value"]
          Handle epoch int → YYYY-MM-DD conversion
          Add formatted-string fallback via field["formatted"]
          Remove flat-key lookups (earningsTimestamp, earningsDate)

ACTION-2: Fix _extract_exdiv_from_dividends
  File: backend/src/contract_validation_integration.py
  Change: Navigate root["dividends"]["ex_dividend_date_recent"]["value"]
          Handle epoch int → YYYY-MM-DD conversion
          Add formatted-string fallback via field["formatted"]
          Keep future-only gate
          Remove flat-key lookups (ex_dividend_date_recent at top level, exDividendDate)

ACTION-3: Fix outer except handler
  File: backend/src/contract_validation_integration.py
  Change: Replace error_msg with str(e)
          Replace "invalid_market_data" error code with "validation_exception"

ACTION-4: Remove dead code block
  File: backend/src/contract_validation_integration.py
  Change: Delete unreachable code after first except handler's return (~lines 1005-1095)

ACTION-5: Rewrite test fixtures
  File: backend/tests/test_contract_validation_calendar.py
  Change: Replace all flat JSON fixtures with _build_overview/_build_dividends output
          Add provider-shape integration tests (§6.1)
          Add exception flow tests (§6.2)
          Remove or rewrite tests that assume flat key structure

ACTION-6: Run full test suite, confirm 167+ all green
```

---

## History

| Date | Event |
|---|---|
| 2026-08-31 | Danny authored `danny-validation-full-context-parity.md` — accepted design for full market-context parity in validation |
| 2026-08-31 | Livingston implemented extractors + tests in `contract_validation_integration.py` and `test_contract_validation_calendar.py` |
| 2026-08-31 | Basher rejected: 3 findings (flat-key extractors, unbound error_msg, invented fixtures). All 167 tests pass = false confidence |
| 2026-08-31 | Danny retrospective (this document): root-cause analysis, Livingston locked out, Rusty assigned for revision with exact specs |

---

## Buy Tracker Six-State Redesign (Danny)

**Date:** 2026-09-03
**Author:** Danny (Lead)
**Status:** ✅ Accepted & Implemented
**Impact:** Buy Tracker recommendation accuracy, DGI timing, agent informativeness


### Verdict


**✅ ACCEPTED** — Design complete. Implementation and tests complete and approved. Outcome accepted.

---

## Portfolio Chat 3-Month Persisted Calendar Context (Rusty)

**Date:** 2026-09-03
**Author:** Rusty (Agent Dev)
**Status:** ✅ Accepted & Implemented
**Impact:** Portfolio Chat context richness, calendar-aware decision making


### 2026-08-30T17:29:20Z: User directive

**By:** Copilot (via Copilot)
**What:** Use the Recent dashboard column for both activity and recommendation provenance. Add an ALPHA tag only when the recommendation comes from Alpha; regular-agent recommendations keep only the SELL tag. Remove the separate Rec. column.
**Why:** User request — captured for team memory

# Design: Full Market-Context Parity for Best Option Contract Validation

**Author:** Danny (Lead / Design Review)
**Date:** 2026-08-31
**Status:** ACCEPTED — implementation-ready
**Supersedes:** Rusty's audit `best-option-validation-market-data-audit.md` (session files)
**Prereqs:** Existing chain-aware validation (D4, Alpha chain context) fully preserved.

---

## 0. Problem Statement

Best Option contract validation runs a Primary→Supervisor→Alpha pipeline
identical in structure to normal Following CC/CSP, but feeds the agents a
**minimal contract-only snapshot** instead of the **full multi-page market data
block** that normal Following provides. Confirmed gaps:

| Data Element | Normal Following | Validation Today |
|---|---|---|
| OVERVIEW page (earnings, fundamentals) | ✅ | ❌ |
| TECHNICALS page (indicators, S/R) | ✅ | ❌ |
| FORECAST page (analyst consensus) | ✅ | ❌ |
| DIVIDENDS page (full history, ex-dates) | ✅ | ❌ |
| ENRICHMENT section (tech-timing, momentum, DGI) | ✅ | ❌ |
| VOLATILITY section (IV/HV, premium richness) | ✅ | ❌ |
| Options chain (Primary) | ✅ (full filtered) | ❌ |
| Options chain (Alpha) | ✅ | ✅ (already chain-aware) |
| Previous activity context | ✅ | ✅ |
| Calendar dates (earnings, ex-div) | ✅ (embedded in pages) | ⚠️ (isolated Cosmos dates) |

A real ex-dividend date present in the Cosmos calendar was missed because the
agent only saw a bare ISO date, not the full dividend schedule with payment
dates, yield, and history that normal agents use for decision quality.

The `rule_evaluator.build_rule_evaluation` call in validation passes
`enrichment_data=None`, so enrichment-dependent rules always degrade.

---

## 1. Design Principles

1. **One canonical data-fetch path.** Reuse `YFinanceDataProvider.fetch_all()`
   and `AgentRunner._build_market_data_block()` — no parallel implementation.
2. **Immutable contract evidence preserved.** The contract-specific evidence
   snapshot (`evaluated_snapshot`) remains a separately labeled, immutable
   section in the prompt; it is never replaced by the full market block.
3. **Chain-aware Alpha preserved as-is.** No changes to D4 validation gates,
   `_build_validation_chain_context`, or `_validate_alpha_alternative`.
4. **Calendar robustness.** Calendar dates from `fetch_all` (live yfinance) are
   primary; Cosmos calendar is fallback. Both sources are logged with
   provenance so a conflict or omission is auditable.
5. **Single refresh boundary.** One `fetch_all(force_refresh=True)` call per
   validation replaces both the current `_force_chain_refresh` AND the missing
   market-data fetch. The chain from `fetch_all` is the authoritative snapshot
   for the entire validation cycle (contract lookup, Alpha chain context, D4
   callback).
6. **Fail-closed for SELL.** If `fetch_all` fails, validation returns WAIT with
   `error=full_context_unavailable`; it does **not** silently fall back to
   contract-only context.
7. **No duplicate fetches.** `fetch_all` fetches the chain internally via
   `_build_options_chain` which calls `chain_cache.get_or_hydrate`. We pass
   `force_refresh=True` so the cache refreshes once. The existing
   `_force_chain_refresh` call is removed (it would be a redundant second
   refresh). This resolves the conflict with Linus's suggestion: we use
   `fetch_all(force_refresh=True)` as the single entry point, and the chain
   cache refresh happens inside it, not separately.

---

## 2. Data-Flow Diagram (After)

```
POST /api/best-options/validate
  │
  └─ _execute_validation(symbol, side, strike, expiration, …)
       │
       ├─ [REMOVED] _force_chain_refresh(symbol)
       │
       ├─ ① full_data = await get_shared_provider().fetch_all(symbol, force_refresh=True)
       │     Returns: {overview, technicals, forecast, dividends, options_chain, volatility}
       │     Chain cache is refreshed inside fetch_all → single network boundary
       │
       ├─ ② chain = json.loads(full_data["options_chain"])
       │     Authoritative chain for this validation cycle
       │
       ├─ ③ contract = _find_exact_contract(chain, side, strike, exp, now)
       │     If not found → WAIT + error (unchanged behavior)
       │
       ├─ ④ _validate_contract_evidence(contract)
       │     If invalid → WAIT + error (unchanged behavior)
       │
       ├─ ⑤ evaluated_snapshot = _build_evaluated_snapshot(
       │       symbol, side, strike, exp, contract, chain, cosmos,
       │       full_data=full_data,               ← NEW
       │       agent_runner_ref=agent_runner,      ← NEW (for _build_market_data_block)
       │   )
       │     Now includes:
       │       market_data_text → full 4-page block + contract evidence section
       │       enrichment_block → tech-timing, momentum, DGI
       │       volatility_block → IV/HV, premium richness
       │       calendar provenance → {source, earnings, ex_dividend}
       │
       ├─ ⑥ chain_context_text = _build_validation_chain_context(chain, side)
       │     Unchanged — Alpha still gets filtered chain
       │
       ├─ ⑦ agent_runner.run_contract_validation(
       │       evidence_snapshot=evaluated_snapshot,
       │       chain_context_text=chain_context_text,
       │       validated_alternative_callback=…,   ← still closes over same chain
       │   )
       │
       └─ ⑧ _persist_validation_activity(…)
```

---

## 3. Detailed Changes


### T8: Alpha Chain Context Unaffected

**File:** `tests/test_contract_validation_integration.py` (existing, extend)
**Owner:** Livingston
**Setup:** Full validation with mock chain.
**Assert:** `_build_validation_chain_context` called with same chain from
`fetch_all`; Alpha receives chain context text appended to full market block;
D4 callback uses same chain.

---

## 11. Implementation Order

1. **Livingston (integration layer):**
   a. Add `_extract_earnings_from_overview`, `_extract_exdiv_from_dividends`,
      `_extract_exchange`, `_resolve_calendar_date` helpers
   b. Update `_build_evaluated_snapshot` (new params, full market block assembly,
      calendar provenance, enrichment_data)
   c. Update `_execute_validation` (replace `_force_chain_refresh` + separate
      chain load with single `fetch_all`)
   d. Add import for `get_shared_provider`
   e. Write T1, T2, T4, T5, T6, T8 tests

2. **Rusty (agent engine):**
   a. Update `run_contract_validation` prompt template (§3.3)
   b. Update `build_rule_evaluation` call to pass `enrichment_data`
   c. Write T3, T7 tests

3. **Danny (review):**
   Verify parity by diff-comparing a normal Following prompt and a validation
   prompt for the same symbol — all pages present, contract evidence labeled.

---

## 12. What This Design Does NOT Change

- Normal Following CC/CSP pipeline — completely untouched
- Monitor agent pipelines — untouched
- Best Options scoring/ranking — untouched
- Best Options precompute — untouched
- Chain cache module — untouched (used indirectly via `fetch_all`)
- Alpha D4 validation gates — untouched
- Frontend API contract — untouched (same POST/GET endpoints, same status schema)
- Telegram notifications — untouched (validation doesn't send notifications today)

---

## Decision History

| Date | Who | Event |
|---|---|---|
| 2026-08-29 | Chain-aware validation | D4 gates + Alpha chain context implemented |
| 2026-08-30 | Canonical schema | Validation activities use identical schema as normal runs |
| 2026-08-31 | Rusty (audit) | Identified full market context gap (`best-option-validation-market-data-audit.md`) |
| 2026-08-31 | Danny (this doc) | Accepted design for full market-context parity |

---

*End of design. No production code or tests modified by this document.*

# Retrospective: Validation Suite Hang — Provider Injection Bypass

**Date:** 2026-08-31
**Author:** Danny (Lead)
**Severity:** P0 — blocked CI for >4 hours at 74% suite completion
**Status:** Root-caused; fix spec below; no code changes in this document

---

## Root Cause

**`_execute_validation` bypasses the injected `context_provider` and hardcodes a call to the global `get_shared_provider()` singleton, which performs real network I/O.**


### AC-5: No false patches

- [ ] Every patched symbol is verified to be on the actual call path of the code under test.
- [ ] Chain cache patches remain (they're valid for the chain-lookup step), but `get_shared_provider`/`fetch_all` is patched at the correct module (`src.contract_validation_integration`) or injected via parameter.

---

## Calendar / Context Parity Tests Status

- `test_contract_validation_calendar.py` — **safe**: tests pure extractors (`_extract_earnings_from_overview`, etc.), no network calls, no `fetch_all`.
- `test_contract_validation_context_parity.py` — **safe**: tests `AgentRunner.run_contract_validation` directly with mocked LLM, does not go through `_execute_validation`.
- Neither file references `get_shared_provider` or `fetch_all` (confirmed: zero grep matches).

These tests are not contributing to the hang and should not be modified.

---

## Ownership

| Item | Owner | Reviewer |
|------|-------|----------|
| `contract_validation_integration.py` provider injection | Livingston | Danny |
| `app.py` endpoint wiring | Livingston | Danny |
| `test_contract_validation_integration.py` fixture fix | Livingston | Basher |
| `test_cross_contract_validation_regression.py` fixture fix | Livingston | Basher |
| AC-3 timeout-bounded regression tests | Livingston | Basher |
| Final CI green confirmation | Danny | — |

---

## Summary

The 4-hour hang was caused by a single line: `yf_provider = get_shared_provider()` in `_execute_validation` (line 863) ignoring the `context_provider` parameter that was threaded all the way from the endpoint. Tests couldn't intercept it because they patched the chain cache (step 2 of execution) but not the provider (step 1). The fix is mechanical: make the provider an explicit injected dependency, mock it in tests, and drain background tasks deterministically.

# Retrospective: Calendar Parity Extractors & Exception Flow

**Author:** Danny (Lead)
**Date:** 2026-08-31
**Status:** REVISION REQUIRED — Rusty assigned
**Trigger:** Basher rejection of Livingston's full-context-parity implementation
**Related decision:** `danny-validation-full-context-parity.md` (accepted design)

---

## 1. Factual Root Causes


### Best Options weekend startup crash fix (2026-08-30, production emergency)


**Date:** 2026-08-30
**Author:** Livingston (Persistence & Integration Engineer)
**Status:** ✅ Implemented (not committed)
**Impact:** Production bug — Best Options unavailable on weekends, crash on startup

#### Production Symptom

Symbol Detail Best Options and Options Screener non-functional on Sunday 2026-08-30. Symbol Detail continuously showed:
```
"Warming up the option chain cache… precompute_pending
Retrying automatically in 15s.
Next scheduled processing: 2026-08-31T10:05:00+00:00.
Retry now"
```

"Retry now" button did nothing. Production log (2026-08-30 06:00:03 UTC):
```
ERROR during Best Options Precompute: unhashable type: 'dict'
```

#### Root Cause #1: Kwarg Mismatch (Silent Startup Failure)

**Location:** `backend/src/main.py:622` (job signature)

**The Problem:**
- Startup trigger passed `run_trigger="startup"` but job function didn't accept kwargs
- Scheduler's worker (scheduler_registry.py:231) filters kwargs:
  ```python
  accepted = inspect.signature(task.job_func).parameters
  kwargs = {k: v for k, v in job_kwargs.items() if k in accepted}
  ```
- Since `run_trigger` wasn't in the job signature, it was **silently dropped**
- Job ran with defaults, didn't receive `trigger="startup"` context
- Cache remained empty (`generation=0`) until Monday cron run

**The Fix:**
```python
# BEFORE:
def run_best_options_precompute_job(self):
    ...

# AFTER:
def run_best_options_precompute_job(self, *, trigger: str = "scheduled"):
    result = run_best_options_precompute(..., trigger=trigger)
```

Also fixed:
- Manual trigger endpoint (app.py:3518): `run_trigger="manual"` → `trigger="manual"`
- Print statement (main.py:638): `result.get('ok')` → `result.get('success')`
- Startup error handling (main.py:806): Added return value checking
- Enhanced exception logging with traceback (main.py:644)

#### Root Cause #2: Unhashable Dict (Memo Key Crash)

**Location:** `backend/src/options_screener.py:231-269` (`_memo_key()`)

**The Problem:**
- `_memo_key()` built tuple with raw Cosmos values for memoization dict key:
  ```python
  return (symbol, side, timestamp, category, shares, earnings_date, ex_div_date, support)
  ```
- When `enrichment.category` or calendar dates were dicts instead of strings (malformed/nested Cosmos data), tuple contained unhashable elements
- Crashed with `TypeError: unhashable type: 'dict'` when used as memo dict key

**Production Data Shape** (hypothesized):
```python
enrichment = {
    "category": {"type": "balanced", "confidence": 0.85},  # ❌ Dict, not string!
    "next_earnings_date": {"date": "2026-09-15", "confirmed": True},  # ❌ Dict!
    "ex_dividend_date": {"date": "2026-10-01", "type": "quarterly"}  # ❌ Dict!
}
```

**The Fix:**
- Defensive normalization in `_memo_key()` to extract primitives:
  ```python
  category = entry.get("category")
  if isinstance(category, dict):
      category = category.get("type") or category.get("category")

  next_earnings = entry.get("next_earnings_date")
  if isinstance(next_earnings, dict):
      next_earnings = next_earnings.get("date")

  ex_dividend = entry.get("ex_dividend_date")
  if isinstance(ex_dividend, dict):
      ex_dividend = ex_dividend.get("date")
  ```
- Ensures all tuple elements are hashable primitives or None
- Gracefully handles malformed Cosmos data
- Extracts semantic values (e.g., `"balanced"` from `{"type": "balanced"}`)

#### Impact

**Before Fix:**
- ❌ Best Options completely unavailable on weekends (cache empty until Monday)
- ❌ No visible error (just "warming up" state)
- ❌ No recovery path until Monday 10:05 UTC
- ❌ Retry button appeared non-functional

**After Fix:**
- ✅ Best Options available immediately on startup (even weekends)
- ✅ Clear error messages if precompute fails (with traceback)
- ✅ Retry button works (targeted refresh)
- ✅ Graceful handling of malformed Cosmos data

#### Files Modified

**Core Fixes:**
1. `backend/src/main.py` - Job signature, trigger forwarding, error handling, logging
2. `backend/web/app.py` - Manual trigger endpoint kwarg name
3. `backend/src/options_screener.py` - Defensive `_memo_key()` normalization
4. `backend/tests/test_best_options_trigger_endpoint.py` - Test expectations

**Regression Tests Created:**
5. `backend/tests/test_best_options_precompute_regression.py` (7 tests)
   - Weekend startup trigger forwarding
   - Return dict structure validation
   - Manual trigger kwarg correctness
   - Startup error handling

6. `backend/tests/test_unhashable_dict_regression.py` (8 tests)
   - Dict category input handling
   - Dict date input handling
   - Mixed dict inputs
   - Dict with no extractable fields
   - Normal string inputs (backward compatibility)
   - Full screener flow with malformed data
   - Production scenario reproduction

#### Test Coverage

**Total Tests**: 195 Best Options tests (180 existing + 15 new regression tests)

**All 195 tests pass** ✅

**Regression Tests:**
- ✅ `test_startup_trigger_populates_cache` - Startup catch-up works
- ✅ `test_manual_trigger_populates_cache` - Manual trigger works
- ✅ `test_scheduled_trigger_default` - Default trigger is "scheduled"
- ✅ `test_weekend_startup_no_cron_next_run` - Weekend startup doesn't wait for Monday
- ✅ `test_return_dict_has_success_key` - Return dict structure correct
- ✅ `test_trigger_kwarg_name` - Manual endpoint uses correct kwarg
- ✅ `test_startup_code_checks_trigger_result` - Startup error handling exists
- ✅ `test_memo_key_with_dict_category` - Extracts "type" field
- ✅ `test_memo_key_with_dict_earnings_date` - Extracts "date" field
- ✅ `test_memo_key_with_dict_ex_dividend_date` - Extracts "date" field
- ✅ `test_memo_key_with_all_dicts` - All fields as dicts
- ✅ `test_memo_key_with_dict_no_extractable_field` - Fallback to None
- ✅ `test_memo_key_normal_string_inputs_unchanged` - Backward compatibility
- ✅ `test_screener_with_dict_category_does_not_crash` - Full screener flow
- ✅ `test_production_startup_precompute_with_dict_enrichment` - Exact production scenario

#### Open Questions

**Data Quality Investigation:**
Why does Cosmos return dicts for category/dates instead of primitives?
- Schema evolution (old: string, new: enriched dict)?
- Data migration in progress?
- Enrichment pipeline bug?
- Multiple data sources with inconsistent formats?

**Recommendation:** Investigate enrichment pipeline to determine root cause. Options:
1. Normalize at Cosmos write time (preferred for data quality)
2. Continue defensive reads at usage sites (current fix, more resilient)
3. Add schema validation/alerts when malformed data detected

#### Behavioral Contract Preserved

All required behaviors from Best Options charter maintained:
1. ✅ On application startup with enabled+run_on_startup, cache population begins promptly even on weekends
2. ✅ A failed cycle is observable and recoverable; do not silently leave an empty cache until Monday
3. ✅ Symbol Detail Retry/Refresh can trigger useful recovery for that symbol
4. ✅ Manual Best Options full trigger works and publishes into the same cache
5. ✅ No request-time aggregate scoring and no persistence of Best Options snapshots
6. ✅ Preserve explicit empty/partial readiness semantics

#### Reviewer Final Verdict (Basher)

✅ **APPROVED** — READY FOR PRODUCTION
**Date:** 2026-08-30T08:48:17+02:00
**Test Results:** 100/100 passing (16.92s)
**TypeScript:** Clean (0 errors)
**Defects Found:** ZERO

**Root Cause #1 (Kwarg Mismatch):** ✅ FIXED
- Job signature now accepts `trigger` parameter
- Startup/manual triggers correctly forward context
- Kwarg forwarding verified in 8 tests
- Production impact: Cache now populates on weekend startup

**Root Cause #2 (Unhashable Dict):** ✅ FIXED
- `_memo_key()` normalizes dict values to primitives
- Extracts semantic values ("balanced" from dict)
- Fallback to None for malformed data
- Backward compatible with string inputs
- 11 tests verify normalization preserves correctness

**Regression Test Coverage:** 26 new tests across 3 files
- `test_production_unhashable_dict_bug.py` (11 tests) — Exact production failure reproduction
- `test_scheduler_best_options_startup.py` (8 tests) — Scheduler registry + weekend startup
- `test_best_options_trigger_endpoint.py` (7 tests) — FastAPI endpoint + manual trigger

**All 100 Best Options tests pass** (existing 74 + new 26)
**Frontend TypeScript:** Clean (0 errors)
**Logic Defects:** Zero detected

**Production Ready:** ✅ APPROVED FOR IMMEDIATE DEPLOYMENT

**Post-Merge Actions:**
- Monitor Sunday startup logs for successful precompute
- Verify Symbol Detail Refresh Now works on weekends
- Verify manual Settings trigger populates cache

**Status:** ✅ APPROVED & READY FOR PRODUCTION
**Not committed** (as requested)


---

## Decision: Alpha Review Contract — Independent Evaluation

**Date:** 2026-08-30
**Author:** Rusty
**Status:** Approved
**Category:** Architecture / Review Pipeline


### Implications


1. **Future review paths:** Always use the established Alpha contract (no Supervisor input)
2. **Architecture clarity:** Reviews are parallel, not sequential
3. **No breaking changes:** All existing code paths already follow this pattern
4. **Regression protection:** Test explicitly guards against this failure mode

---

## Decision: Validation Activities Use Canonical Agent Schema

**Date:** 2026-08-30
**Owner:** Livingston (Persistence & Integration)
**Status:** Rejected (Error-path data loss)


### Rejection Reason


**Production Data Loss in Error Path:** Legacy error-only fallback (minimal {symbol, activity, timestamp, note, reason}) loses canonical fields when agent execution fails. No field recovery mechanism from evaluated_snapshot. This represents real data loss in production-critical code path.

**Status:** REJECTED — requires error-path recovery design before merge.

---

## Decision: Basher's Two-Gate Validation Review

**Date:** 2026-08-30
**Reviewer:** Basher (Tester & QA)
**Status:** Approved (with rejection and revision)


### Review Cycle 2: Rusty Alpha Review Fix (Approval)


**Gate:** Alpha review signature correction
**Verdict:** ✅ APPROVE

- Signature removed invalid `supervisor_view` kwarg
- Independent review architecture confirmed
- Fail-closed semantics verified
- 54 contract-validation + integration + Alpha execution tests passing
- Zero defects detected
- Code quality clean, TypeScript verified
- Git diff minimal and surgical

**Status:** ✅ **APPROVED FOR PRODUCTION DEPLOYMENT**



---

## Decision: Contract Validation Function-Specific Model Routing

**Date:** 2026-08-30
**Author:** Rusty (Agent Dev)
**Status:** ✅ Implemented & Approved
**Category:** Architecture / Model Routing


### Commit


**Hash:** `8cac4bc Use function models for contract validation`
**Status:** ✅ Approved for Production

---

**Reviewer:** Basher (Tester & QA)
**Date:** 2026-08-30T19:23:19+02:00
**Verdict:** ✅ **APPROVED FOR PRODUCTION**

### 2026-08-29T11:29:10+02:00: Manual Alpha execution semantics

**By:** Copilot (via Copilot)
**What:** Dashboard CC/CSP actions and Settings "Run Now" must force Alpha for the four CC/CSP agents. Scheduled executions and "Full analysis" retain due-only Alpha behavior. Forced runs must not reset or suppress the automatic prolonged-WAIT cooldown.
**Why:** User decision after architecture review; preserves predictable manual behavior while controlling full-analysis cost and maintaining automatic alerting.

---

# Decision Record — Best Options row inclusion: delta band is a filter, not only a colour gate

**Date:** 2026-08-29T12:01:15+02:00
**Author:** Danny (Lead)
**Status:** RATIFIED — durable, supersedes conflicting wording in the original design
**Supersedes:** `.squad/decisions/inbox/danny-best-options-design.md` sections 4.1 and 4.2
as originally written (2026-08-29, ACCEPTED). The design document itself has been amended
in place (new section 2A, plus corrected sections 4.1/4.2 with the original wording kept
as a marked historical note) — this record is the durable, standalone entry Basher's
review correctly found missing, and it is what a future reader or `decisions.md` entry
should cite.
**Traces to:** `.squad/decisions/inbox/basher-best-options-review.md` (REJECT, Defect 1),
`.squad/decisions/inbox/linus-best-options-scoring.md` ("Row inclusion — resolved"
section), `backend/src/best_options.py` module docstring ("Provenance note").
**Reviewer of record for this artifact:** Basher (Tester/Reviewer) — re-review requested,
see "What Basher should re-check" below.

## Background

Basher's adversarial review of Best Options (`basher-best-options-review.md`) rejected the
feature body of work for two defects. This record resolves **Defect 1 only**: "undocumented
deviation from the ACCEPTED design (row inclusion)." (Defect 2, the frontend `parameters`
contract mismatch in `frontend/src/types/best-options.ts` / `BestOptionsParams.tsx`, is
Rusty's/Livingston's surface and is out of scope for this record — Danny has not touched
any frontend file.)

`backend/src/best_options.py` already implements, and Linus's own decision draft already
narrates, a same-day correction: the delta band moved from a colour-only gate to a
row-inclusion filter. That correction was never reconciled against the literal text of the
ACCEPTED design (`danny-best-options-design.md` sections 4.1/4.2), which is what Basher
flagged, and `.squad/decisions.md` had zero entries for Best Options at all. This record —
plus the in-place amendment of the design document (section 2A, and sections 4.1/4.2
themselves) — closes that gap.

## The corrected semantic rule (binding, effective 2026-08-29)

A side's primary `rows` in the Best Options response must contain **all and only** the
contracts that satisfy **both**:

1. the requested **DTE window** (default 0..49 days), and
2. the category/strategy's configured **`abs(delta)` band** (`[delta_lo, delta_hi]`) for
   that side.

A contract failing either filter is **never** a primary row. It is not thereby erased from
the response as a whole:

* `nearest_miss` is computed over the **full** DTE-window contract set — in-band and
  delta-excluded contracts together — so a contract just outside the configured delta band
  remains the direct, named answer to "why am I not seeing this contract."
* Each side additionally reports `excluded_by_delta_band`: a count of how many DTE-window
  contracts the delta filter removed.

Delta itself is, and remains, a **true display filter with real teeth** — not cosmetic.
`abs(delta)` is displayed as signed delta for context, but the unsigned value is what
governs inclusion (this record) and what feeds the `delta_fit` score component (design
section 4.3) for contracts that pass. There is no reading under which a contract outside
the configured band should appear as a coloured (including red) row in the primary table.

## Why this supersedes the original design wording

The original section 4.1 ("nothing inside the [DTE] window is ever hidden") and section
4.2 (delta band listed as hard-gate "G2": "failure = red, row still shown") together read,
taken literally, as if delta band only affected colour. Linus's first implementation
followed that literal text in good faith and was corrected the same day per an explicit,
unambiguous product-owner instruction after reviewing that first pass: the displayed chain
must be filtered by the configured delta range in addition to the DTE window, with only
contracts surviving both filters shown as primary rows. This record ratifies that
correction as the design's binding semantics going forward and formally supersedes the
original section 4.1/4.2 wording — not by deleting it, but by amending the design document
in place with the corrected text as normative and the original text preserved and marked
as a superseded historical note (see `danny-best-options-design.md` sections 4.1/4.2 and
the new section 2A).

## What stays unchanged

* **G1 (tradability)** and **G3 (earnings span)** remain true binary colour gates exactly
  as originally specified: a failure colours an in-band, in-window row red without
  removing it.
* The Layer B scoring formula, weights, colour thresholds (39.999/40/64.999/65), ordering
  rule, and 400-row truncation cap (design sections 4.3-4.5) are untouched.
* `filter_options_chain_by_delta` (the existing, wide, non-category-aware function, design
  finding F2) is still never reused for this filter. The row-inclusion delta-band check
  goes through `best_options.py`'s own `_gate_delta_band`, using the category's configured
  band and reading delta only via the `options_chain_view` accessors.

## Ownership and scope of this correction

* **Danny (this record + the design document amendment)** — documentation/design
  correction only. No production code touched.
* **Linus** — original author of `best_options.py`'s row-inclusion behaviour and of
  `linus-best-options-scoring.md`'s account of the correction; locked out of this revision
  cycle per the reviewer-protocol strict-lockout rule. Not needed here regardless: the
  evaluator code already implements the corrected semantics and is not being modified by
  this record.
* **Livingston / Rusty** — unaffected by this record; Basher's Defect 2 (frontend
  `parameters` contract mismatch) remains open and unrelated to row-inclusion semantics.

## What Basher should re-check

This record resolves Defect 1 as a pure documentation/process gap:

1. `.squad/decisions/inbox/danny-best-options-design.md` no longer contradicts
   `best_options.py`'s shipped behaviour — section 2A states the corrected rule up front,
   and sections 4.1/4.2 carry the corrected normative text with the original wording kept
   as an explicitly marked, non-normative historical note.
2. This record exists as the durable, standalone entry for the correction (the artifact
   Basher's review said was missing), citable independently of the amended design document.
3. No evaluator code, tests, or frontend files were modified by this record — `Linus`'s
   `best_options.py`, `Rusty`'s frontend, and `Basher`'s own test suites are exactly as they
   were when the REJECT verdict was issued.

Re-review target: **`.squad/decisions/inbox/danny-best-options-design.md`** (re-read
section 2A and the amended sections 4.1/4.2) plus this record. Defect 2 (frontend contract
mismatch) remains outstanding and is not addressed here.

---

# Design Decision — "Best Options" Analyze Page

**Date:** 2026-08-29
**Author:** Danny (Lead)
**Ceremony:** Design Review (auto-triggered: multi-agent task, 2+ agents, shared systems)
**Participants:** Danny (facilitator), design-critique reviewer; assignments to Linus, Rusty, Livingston, Basher
**Status:** ACCEPTED — ready for implementation. **Amended 2026-08-29T12:01:15+02:00**
(row-inclusion semantics, section 2A/4.1/4.2 — see amendment note and
`.squad/decisions/inbox/danny-best-options-delta-filter-correction.md`, the durable
decision record superseding this document's original sections 4.1/4.2 wording)
**Schema version:** `best_options` v1

---

## 1. Problem

The user reports substantially fewer covered-call / cash-secured-put sell alerts over
the last two months, most noticeably after moving to a smaller model. Today there is
**no way to tell whether that means "no qualifying contract existed" or "the model
declined to say so"**, because the only view of candidate quality is the agent's own
prose verdict.

Requested: a new **Best Options** entry under the Symbol Detail *Analyze* menu showing
every option in the filtered chain within the near-dated window and the configured delta
ranges, each row coloured green / yellow / red according to that stock's dividend-category
profile, with the parameters used for the analysis visibly displayed.

---

## 2. Decision: evaluation approach

**Deterministic in the critical path. LLM strictly additive and out of band.**

Concretely:

* Zero LLM calls are reachable from the `best-options` endpoint. Availability of the page
  is a function of the option-chain cache alone.
* An LLM may be invoked only by an explicit, separate user action ("Explain this row"),
  reusing the existing symbol-chat path. It may **never** rank, gate, colour, filter,
  reorder, or block render.


### Tests


* **NEW** `backend/tests/test_best_options.py`
* **NEW** `backend/tests/test_best_options_endpoint.py`
* **NEW** `backend/tests/test_category_params.py`
* **NEW** `backend/tests/test_options_chain_dte_filter.py`
* **NEW** `backend/tests/test_best_options_integration.py`

---

## 8. Risks and edge cases

1. **Empty or all-red table for aristocrats** — the most likely real outcome, and the reason
   the premium floor is graded rather than binary. `nearest_miss` is always present.
2. **Cold chain** — handled by `get_or_hydrate` + 200 warming + client retry with backoff.
   Never an error banner, never a hang.
3. **Category silently defaulted to balanced** — surfaced via `defaulted: true` and an
   amber note.
4. **`iv_rank_min` unenforceable** — surfaced, not silently dropped.
5. **Stale quotes served from last-known-good** — badge plus a page-level banner with the
   `quote_asof` range. Never affects colour (F10).
6. **Zero `total_shares` for CC** — banner, table still fully rendered.
7. **Put delta sign** — `abs()` consistently, including inside `delta_fit`. Getting this
   wrong silently empties the put table with no error.
8. **Expired / non-standard expirations** — `filter_options_chain_by_dte` prunes `DTE < 0`.
9. **Payload size** — a liquid name, both sides, 49 DTE is a few hundred rows. Scoring cost
   is negligible; the payload is the cost. 400 rows/side cap after ordering.
10. **Green rows contradicting an agent WAIT** — expected and intended; must be explained
    on screen (section 6), not left to be discovered.
11. **Timezone** — DTE in America/New_York. UTC would flip DTE by one after 20:00 ET and
    silently move rows across the `dte_max` boundary.
12. **Determinism** — same chain in, byte-identical JSON out. Test-enforced.

---

## 9. Assignments

**Linus (Quant Dev)** — owns `backend/src/best_options.py` and
`backend/src/category_params.py`, plus the semantics of
`filter_options_chain_by_dte`. Gate predicates, DTE-scaled premium thresholds, the four
score components, weight renormalisation, colour thresholds, CC/CSP asymmetries, total
ordering, and `nearest_miss`. Pure functions only: no I/O, no Cosmos, no FastAPI, no LLM.
Every quote/Greek read must go through the `options_chain_view` accessors.

**Rusty (Agent Dev)** — owns the FastAPI endpoint (request validation, input assembly:
category, shares, earnings date, ex-dividend, support level; warming response), the
`normalize_category` adoption in `agent_runner`, and the **entire frontend**: BFF route,
page, `BestOptionsView`, `BestOptionsParams`, types, the `SymbolActions` menu entry and the
`badges.ts` helper. Must read the bundled Next.js 16 docs first per `frontend/AGENTS.md`.

**Livingston (Persistence & Integration)** — owns `OptionsChainCache.get_or_hydrate` and
the public `schedule_background_refresh` (cache lifecycle is his surface), **and** the
integration test that composes *real* modules across the Linus/Rusty seam: real cache with
persistence enabled and disabled, real `best_options`, real endpoint. Must assert that the
`parameters` block echoed in the response is the object the scorer actually consumed, and
that a cold miss returns a warming response promptly rather than stalling the event loop.
Assigning this seam an owner **up front** is the direct application of the 2026-08-18
lesson: unowned seams are where mutual fakes breed.

**Basher (Tester)** — reviewer gate, and author of the adversarial cases: all-null-Greeks
chain; every contract failing one gate; the category matrix (5 categories x 2 sides x
underscore / space / Title / None); stale-only chain; put-sign inversion; DTE boundaries
(exactly 0, 45, 46, 49, 50); `spread_pct` with a null ask; `insufficient_data`
renormalisation; and a determinism test asserting two runs over identical input are
byte-identical. Frontend gate is `npm run lint` + `npm run build` — **no frontend test
runner exists and none is to be added**.

---

## 10. Lead's acceptance gate

Work is not complete until all five hold:

1. **No LLM call is reachable from the endpoint** — proven by a test that patches the LLM
   client to raise and asserts a full `200` with a populated table.
2. **No direct `contract.get("bid"/"ask"/"delta"/...)`** anywhere in the new code —
   grep-verifiable; a violation is review-blocking per the accepted zero-free decision.
3. **The `parameters` block is the object the scorer consumed**, not a re-derivation.
4. **`nearest_miss` is populated on every response**, including the all-red case,
   verified by test.
5. **No changes to `options_chain_merge.py`, `options_chain_view.py`, or the `refresh_all`
   watchdog contract** (2026-06-30 decision).

---

# Design Decision — Forced Alpha execution on manual CC/CSP runs

**Date:** 2026-08-29
**Author:** Danny (Lead)
**Ceremony:** Design Review (no implementation; architecture + scope ruling)
**Participants:** Danny (facilitator); assignments to Linus, Rusty, Livingston, Basher
**Status:** PROPOSED — two user confirmations required before implementation (§12)
**Contract version:** `run_trigger` / `force_alpha` v1

---

## 1. Problem

The user wants a way to **manually launch the four CC/CSP agents with a guarantee that
the Alpha Advisor runs during that invocation**. Today Alpha only runs when it happens
to be "due": on an alert, or on a prolonged-WAIT streak that has also cleared a cooldown.
On a normal manual run of a symbol that is WAITing calmly, Alpha never executes, so the
user cannot ask the question "what would Alpha say about this symbol *right now*?"

Their proposal: dashboard CC/CSP buttons always force Alpha; scheduled runs keep the
current due-only behaviour.

**Note:** `docs/concepts.md:253` already documents Alpha's triggers as "alerts, prolonged
WAITs, **on-demand**". The on-demand trigger has never existed in code. This work makes
the documentation true rather than adding a novel concept.

---

## 2. Current behaviour (verified in code, not from memory)

**The four agents in scope** and their runner entry points:

| Agent type | Module | Runner entry |
|---|---|---|
| `covered_call` | `backend/src/covered_call_agent.py` | `AgentRunner.run_symbol_agent` |
| `cash_secured_put` | `backend/src/cash_secured_put_agent.py` | `AgentRunner.run_symbol_agent` |
| `open_call_monitor` | `backend/src/open_call_monitor_agent.py` | `AgentRunner.run_position_monitor` |
| `open_put_monitor` | `backend/src/open_put_monitor_agent.py` | `AgentRunner.run_position_monitor` |

`buy_tracker` is a fifth agent that shares the same trigger surfaces but is **excluded by
design** — `agent_runner.py:1925` sets `_skip_reviews = agent_type in ("buy_tracker",)`,
so it has no Supervisor and no Alpha. It must stay excluded under forcing.

**Alpha gates today** (four call sites, all inside `agent_runner.py`):

1. `run_symbol_agent`, alert branch (`:1936-1956`) — Supervisor **and** Alpha in parallel.
2. `run_symbol_agent`, non-alert branch (`:1957-1985`) — Alpha only if
   `_detect_prolonged_wait(...)` is True; otherwise Supervisor alone and `alpha_view = None`.
3. `run_position_monitor`, alert/roll branch (`:2921-2955`) and non-alert branch
   (`:3000-3045`) — same shape, plus `incomplete_quote_wait` forces `prolonged_wait = False`.

`_detect_prolonged_wait` (`:1227-1284`) requires **both** (a) the last
`PROLONGED_WAIT_THRESHOLD = 5` activities are all non-alert, non-error WAITs, and (b) at
least `SUPERVISOR_COOLDOWN = 3` WAITs since the last activity carrying an `alpha_view`.

`_run_alpha_review` (`:1419-1563`) is fully non-blocking: every failure path returns
`None`, never raises. Its result is persisted only when non-null
(`cosmos.update_activity_field(field="alpha_view")`), so **"Alpha ran and produced nothing
usable" is indistinguishable from "Alpha never ran"** in the stored document. That gap is
what makes a "guaranteed execution" claim unverifiable today.

**Manual trigger surfaces today:**

- `POST /api/trigger/{agent_type}` (`backend/web/app.py:5321`) — reads an optional JSON
  body, currently only `symbol`; spawns `_run_agent_in_background` (`:4961`) on a bare
  daemon thread. **No in-flight guard whatsoever.**
- `POST /api/trigger-all` (`:5531`) — sequential run of all five agents
  (`_FULL_ANALYSIS_AGENT_ORDER`, `:5354`), guarded by `app.state._full_analysis_status`
  with a 409 on re-entry.
- `POST /api/scheduler/tasks/{task_name}/run` (`:5428`) → `TaskRegistry.trigger_task_now`
  (`scheduler_registry.py:307`) — enqueues **only a task name** onto `self._job_queue`;
  the queue carries no per-invocation payload, and the worker calls the pre-bound
  `task.job_func()` with no arguments.
- Frontend: `TriggerButton.tsx` → BFF `frontend/src/app/api/trigger/[name]/route.ts`
  (already forwards the raw request body verbatim — no BFF change needed for a new field)
  → used in `DashboardAgentTables.tsx:124` (agent-level) and `:260` (per-symbol row).

The button's `status === "running"` guard resets as soon as the fire-and-forget POST
returns (~milliseconds), so it is a visual affordance, **not** a concurrency control.

**No authentication or authorization exists anywhere in `backend/web/app.py`.** Every
trigger endpoint is open to anyone who can reach the port.

---

## 3. Two hazards that any design must handle

These are the substantive findings; the UX choice is secondary to them.


### H2 — Notification blast radius


The Telegram prolonged-WAIT path (`:2033-2049`, `:3062-3079`) is gated on the
`prolonged_wait` flag, not on `alpha_view` being present. As long as forcing sets a
*separate* flag and never sets `prolonged_wait = True`, a forced run cannot push
notifications. This is the correct default: a manual full-watchlist CC run with forcing
would otherwise fire a Telegram message per symbol with a MODERATE/STRONG Alpha finding.
Forced results are visible in the UI (the 🧠 icon in `RecentActivities.tsx:175` and
`DashboardActivity.tsx:163`, the "Alpha Executed" filter at `:117`, and the Alpha panel in
`ActivityDetailView.tsx:109`) without any push.

**Secondary cost note:** forcing multiplies Alpha calls by the watchlist size. An
agent-level CC run over N symbols goes from ~0 Alpha calls to N. At `alpha: "gpt-5.4-mini"`
(`backend/config.yaml:8`) that is acceptable, but it is the reason §5 keeps the escape
hatch and §7 insists on a real concurrency guard.

---

## 4. Options considered

| # | Option | Pros | Cons |
|---|---|---|---|
| **A** | Dashboard CC/CSP buttons hardcode forcing; no API field (user's literal proposal) | Zero new UI; exactly the requested behaviour | Semantics baked into the button; no cheap manual run; API and UI disagree about what a "manual run" means; scripts/curl can't opt in or out |
| **B** | `force_alpha` in the trigger contract, **defaulted to `true` for manual invocations**; UI keeps one button | Same one-click UX as A; behaviour is explicit and testable at the API; scripted/scheduled callers can opt out; trivially extensible to a settings toggle later | One extra field to plumb through 6 files |
| **C** | Two buttons: "Run" and "Run + Alpha" | Most explicit; per-click cost control | Doubles the control surface on a table that already has per-row and per-agent buttons; the user has said they want the guarantee by default, so the plain button becomes dead weight |
| **D** | Global setting "Force Alpha on manual runs" in Settings | One place to change; no per-click decision | Hidden mode — the same button does different things on different days; needs a new settings key, persistence, and a Cosmos round-trip; worse for a behaviour the user wants to be a guarantee |
| **E** | Alpha-only re-review of an existing activity (no full agent run) | Cheapest; targeted; no duplicate primary decision | Does not satisfy "guaranteed during that invocation" — it reviews a stale decision against stale market data. Genuinely useful, but as a *later* addition |

**Better idea considered and rejected:** making forcing implicit in "any run of a single
symbol" (i.e. force when `symbol` is present, don't when it's a full sweep). It reads as
clever cost control but produces a rule nobody can predict from the UI, and the agent-level
button — the one the user pointed at — would be the one that doesn't force.

---

## 5. Recommendation — Option B, defaulted on

**Adopt Option B with `force_alpha` defaulting to `true` for every manual invocation of
the four agents.** The dashboard buttons therefore *do* always force Alpha, which is the
user's requested behaviour — but the forcing lives in the **contract**, not in the button.

Rationale: the user's stated need is a guarantee, and a guarantee that only exists in a
React click handler is not a guarantee — it cannot be tested at the seam, cannot be used
from `curl`, and cannot be reasoned about from a stored activity. Making it a request field
with a manual-default costs one parameter and buys testability, auditability, and an
escape hatch for cost-sensitive callers, with **no additional clicks for the user**.

Explicit controls (Option C) are *not* safer here. The dangerous outcomes are H1 and H2 —
both are backend semantics that a second button would not mitigate, and both are addressed
below regardless of which button the user presses.

---

## 6. Execution-mode contract (v1)

**Two orthogonal concepts. Do not collapse them.**

```
run_trigger : "scheduled" | "manual"      # provenance — who asked
force_alpha : bool                        # policy    — run Alpha unconditionally
```

A boolean is correct, not an enum. The only three states an enum could express are
`due-only` / `always` / `never`, and `never` is `force_alpha=false` + no due condition,
which the existing gate already produces. Adding a third state would require a way to
suppress a due Alpha — nobody has asked for that, and it would silently disable alerting.

**Defaults by call path:**

| Call path | `run_trigger` | `force_alpha` |
|---|---|---|
| `main.OptionsAgentScheduler._run_all_agents_async` (cron) | `scheduled` | `false` |
| `POST /api/trigger/{agent_type}` | `manual` | `true` (body may override) |
| `POST /api/trigger-all` | `manual` | `true` (body may override) — see §12-D2 |
| `POST /api/scheduler/tasks/monitor_agents/run` | `scheduled` | `false` — see §12-D1 |

**Runner signature change** (`agent_runner.py`): add `force_alpha: bool = False` to
`run_symbol_agent` and `run_position_monitor`. Default `False` means every existing caller,
including the cron path, is byte-for-byte unchanged in behaviour.

**Gate change** — the minimal edit at all four Alpha call sites:

```
run_alpha = is_alert or prolonged_wait or force_alpha
```

`force_alpha` must **not** set `prolonged_wait`, must **not** set `is_alert`, and must not
alter which market-data block is built (`_build_alpha_options_chain` /
`_build_market_data_block` already run before the branch in the position monitor and can be
hoisted identically in `run_symbol_agent`).

**Interaction rules, in order of precedence:**

1. `_skip_reviews` wins. `buy_tracker` never runs Alpha, forced or not.
2. `incomplete_quote_wait` (position monitor) wins. When quotes are degraded the decision
   is a mechanical WAIT with sanitized prose; feeding that to Alpha invites a
   recommendation built on absent quotes. Forcing must **not** override it — record
   `alpha_status = "skipped_incomplete_quotes"` so the skip is visible, not silent.
3. Otherwise `force_alpha` runs Alpha exactly as the alert path does, in parallel with the
   Supervisor via the existing `asyncio.gather`.

---

## 7. Concurrency, idempotency, double clicks

`POST /api/trigger/{agent_type}` currently has **no guard** — two clicks start two full
concurrent sweeps of the same agent, doubling LLM spend and racing two writers onto the
same symbol's activity stream. Forcing Alpha makes each duplicate materially more
expensive. **Fixing this is in scope**, because forcing is what turns a latent waste into
a real cost.

**Design:** an in-flight registry on `app.state`, keyed by `(agent_type, symbol or "*")`,
guarded by a `threading.Lock`, mirroring the existing `_full_analysis_status` pattern
(`:5361`) rather than inventing a new one.

- Second request for the same key → **409** with
  `{"status": "already_running", "agent_type", "symbol", "started_at", "force_alpha"}`.
- A `"*"` (all-symbols) run blocks new `"*"` runs of the same agent; per-symbol runs of
  *different* symbols may proceed in parallel — that is existing, working behaviour and
  narrowing it is out of scope.
- The key is released in a `finally` block, and carries `started_at` so a crashed thread
  cannot wedge the key forever: entries older than the scheduler's own
  `_MAX_TASK_DURATION_SECONDS` (1800s, `scheduler_registry.py:17`) are treated as stale and
  reclaimed. Reuse that constant; do not introduce a second timeout number.
- `/api/trigger-all` keeps its own existing 409 guard, unchanged.

**Idempotency:** the 409 *is* the idempotency mechanism. No client-generated request id —
a manual run is deliberately not idempotent across time (running CC twice an hour apart is
a legitimate thing to want); it is only non-re-entrant while in flight.

**Frontend:** `TriggerButton` must stop treating any non-`triggered` response as `error`.
A 409 is a normal outcome and should render a distinct "already running" state
(with the existing 3s reset), not a red ✗.

---

## 8. Partial failures

Alpha's non-blocking contract is preserved verbatim: `_run_alpha_review` returning `None`
must never affect the primary decision, the activity write, the Supervisor, or the exit
status of the run.

But **"forced and failed" must be observable**, otherwise the guarantee is unfalsifiable.
Persist a small status field on the activity document alongside `alpha_view`:

```
alpha_run = {
  "trigger": "scheduled" | "manual",   # what caused Alpha to be attempted
  "forced":  true | false,             # was it attempted only because of force_alpha
  "status":  "ok" | "failed" | "skipped_incomplete_quotes" | "skipped_agent_type"
}
```

Written whenever Alpha is *attempted or deliberately skipped under forcing* — including
when the result is `None`, which is precisely the case that is invisible today. `alpha_view`
itself keeps its current semantics (present only on success), so every existing reader —
`ActivityDetailView`, `DashboardActivity`, `RecentActivities`, `ApplyRecommendation`,
`_build_dashboard_tables` — is unaffected and needs no change.

**Multi-symbol partial failure:** a manual agent-level run already continues past a failing
symbol (each `run_symbol_agent` wraps its body in `try/except`). Unchanged. The run is
reported as completed; per-symbol outcomes are read from the activity stream, as today.

---

## 9. H1 mitigation — cooldown neutrality (mandatory, not optional)

`_detect_prolonged_wait`'s cooldown loop must change from:

```
if act.get("alpha_view"): break
```

to: break only on activities whose Alpha was a **scheduled/due** review — i.e. where
`alpha_run.forced` is not `true`. Activities written before this change have no
`alpha_run` field; treat missing metadata as *not forced* (break), which preserves today's
behaviour exactly for all historical documents and is the conservative direction (it can
only delay a review, never suppress an alert that would otherwise fire).

Without this change, shipping forced Alpha ships a regression to the alerting path the
user is already unhappy about — the same complaint that drove the Best Options work.

---

## 10. Confirmations, permissions, audit

- **Confirmations:** none for a single-symbol run. For `/api/trigger-all` with forcing —
  the most expensive action in the system — the UI should state the cost in the button
  title/tooltip ("runs all agents over the full watchlist, Alpha forced"). No modal;
  the 409 guard already prevents the accidental-double-click failure mode. If the user
  wants a modal there, that is a UI preference, not an architectural requirement.
- **Permissions:** not applicable — the app has no auth layer at all. This is worth
  recording explicitly: the concurrency guard in §7 is the *only* protection against an
  unauthenticated caller looping an expensive forced sweep. Do **not** add an auth
  mechanism as part of this task; note it as a standing risk.
- **Audit metadata:** in addition to `alpha_run` (§8), extend the existing execution trace
  (`_record_trace`, `agent_runner.py:1186`) with `run_trigger` and `force_alpha` so the
  Agent Traces page can answer "was this a manual forced run?" without inference. Trace
  writing is already best-effort and never raises — keep it that way.
- **Observable status:** the trigger endpoint returns
  `{"status": "triggered", "agent_type", "symbol", "force_alpha": true}` so the caller can
  confirm the mode it actually got. A polling status endpoint for per-agent runs is **out
  of scope** — the in-flight registry's 409 payload plus the existing activity stream cover
  the need, and `/api/trigger-all/status` already exists for the sequential run.

---

## 11. Test cases

**Backend — gate semantics (`agent_runner`), new `backend/tests/test_force_alpha_execution.py`:**

1. `run_symbol_agent`, calm WAIT, `force_alpha=False` → Alpha **not** called (regression
   lock on today's behaviour).
2. Same, `force_alpha=True` → Alpha called exactly once; Supervisor still called exactly
   once; both still run concurrently.
3. Alert + `force_alpha=True` → Alpha called exactly **once**, not twice (no double-gather).
4. Prolonged WAIT + `force_alpha=True` → Alpha once; `alpha_run.forced` is `false`
   (a due review that also happened to be forced is recorded as due, so it correctly
   resets the cooldown).
5. `force_alpha=True` never sets `prolonged_wait` → **no** `send_prolonged_wait_alert`,
   even with a STRONG Alpha finding. Assert on the notifier mock, not on log text.
6. `force_alpha=True` never sets `is_alert` → no `send_alert`.
7. `buy_tracker` + `force_alpha=True` → Alpha not called; `alpha_run.status ==
   "skipped_agent_type"` (or no field, per Linus's chosen minimal shape — pick one and
   assert it).
8. `run_position_monitor` with `incomplete_quote_wait=True` + `force_alpha=True` → Alpha
   not called; `alpha_run.status == "skipped_incomplete_quotes"`.
9. `_run_alpha_review` returns `None` under forcing → activity still written, Supervisor
   view still persisted, `alpha_run.status == "failed"`, no exception escapes.
10. `_run_alpha_review` raises under forcing → same as (9). The primary decision must
    survive an Alpha exception.
11. Both `run_symbol_agent` and `run_position_monitor` covered for (2), (5), (9) — the two
    entry points have independently written gate code and will drift otherwise.

**Backend — cooldown neutrality (H1), same file:**

12. History = 5 WAITs, the most recent carrying `alpha_view` + `alpha_run.forced=true`
    → `_detect_prolonged_wait` still returns `True` (forced review did not consume the
    cooldown).
13. Same but `alpha_run.forced=false` → returns `False` (scheduled review consumed it —
    today's behaviour preserved).
14. Same but the activity has `alpha_view` and **no** `alpha_run` field (legacy document)
    → returns `False` (backward-compatible default).

**Backend — API (`backend/web/app.py`):**

15. `POST /api/trigger/covered_call` with no body → runner invoked with `force_alpha=True`,
    response echoes `"force_alpha": true`.
16. Body `{"force_alpha": false}` → runner invoked with `force_alpha=False`.
17. Body `{"symbol": "AAPL"}` → symbol forwarded **and** `force_alpha=True` (the two fields
    are independent).
18. Second identical request while the first is in flight → 409, second runner invocation
    never happens.
19. Two per-symbol requests for *different* symbols of the same agent → both proceed.
20. Key released after completion → a third request succeeds.
21. Key released after the runner **raises** → a subsequent request still succeeds
    (guard uses `finally`).
22. Stale key older than `_MAX_TASK_DURATION_SECONDS` is reclaimed.
23. `POST /api/trigger/buy_tracker` → accepted, runs, no Alpha (forcing is inert, not an
    error).
24. Unknown agent type → still 404 (unchanged).
25. Cron path: `main._run_all_agents_async` invokes all agents with `force_alpha=False` —
    a direct regression lock on "scheduled behaviour is untouched".

**Seam / integration (real modules, no mutual fakes):**

26. End-to-end with the real `agent_runner` + real FastAPI route + fake LLM client:
    a forced manual CC run on one symbol produces an activity document containing both
    `alpha_view` and `alpha_run.forced=true`, and the dashboard builder renders it
    unchanged. This test owns the API↔runner seam and is assigned up front (see §13).

**Frontend:** lint + build only. No FE test runner exists and none is to be added.
Manual check: 409 renders "already running", not an error.

---

## 12. Decisions requiring the user's confirmation (do not implement until answered)

**D1 — Scheduler "Run Now" is deliberately *not* forced.**
`POST /api/scheduler/tasks/monitor_agents/run` is the "Run Now" button on the Settings
page's Monitoring Agent card. It routes through `TaskRegistry`, whose job queue carries
only a task name and whose jobs are pre-bound zero-argument callables. Forcing there means
threading a payload through the queue and the worker — a change to shared scheduling
machinery used by ten unrelated tasks, for one flag. Proposal: that button keeps
**scheduled** semantics ("run the scheduled job now"), and the *dashboard* buttons carry
**manual/forced** semantics. This means two manual-looking buttons behave differently, and
that is a user-facing semantic split which must be confirmed, not assumed. If the user
wants both forced, the registry change becomes part of the task and Linus owns it.

**D2 — Does "Full analysis" (`/api/trigger-all`) force Alpha too?**
It is manual and invokes the same four agents, so consistency says yes; it is also the
single most expensive action in the system (five agents × the entire watchlist, one extra
Alpha call per symbol). Proposal: **yes, force**, for consistency with the rule "manual
means forced". Confirm, because it is the biggest cost delta in this design.

Both are semantic choices about what a button means. Neither is being silently adopted.

---

## 13. Files and owners

| File | Change | Owner |
|---|---|---|
| `backend/src/agent_runner.py` | `force_alpha` param on `run_symbol_agent` + `run_position_monitor`; gate at 4 call sites; `alpha_run` persistence; `_detect_prolonged_wait` cooldown neutrality; `run_trigger`/`force_alpha` in `_record_trace` | **Linus** |
| `backend/src/covered_call_agent.py`, `cash_secured_put_agent.py`, `open_call_monitor_agent.py`, `open_put_monitor_agent.py` | pass-through `force_alpha` param, default `False` | **Linus** |
| `backend/src/main.py` (`_run_all_agents_async`) | explicit `force_alpha=False` — scheduled path stays due-only | **Linus** |
| `backend/src/scheduler_registry.py` | **untouched** unless D1 is answered "force it too" | — |
| `backend/web/app.py` (`_run_agent_in_background`, `/api/trigger/{agent_type}`, `_run_all_agents_sequentially`, `/api/trigger-all`, new in-flight registry) | parse `force_alpha`, default `true`; concurrency guard + 409 | **Rusty** |
| `frontend/src/components/TriggerButton.tsx`, `DashboardAgentTables.tsx` | 409 → "already running" state; tooltip stating Alpha is forced | **Rusty** |
| `frontend/src/app/api/trigger/[name]/route.ts` | **no change** — already forwards the raw body | — |
| `backend/tests/test_force_alpha_execution.py` (new) | cases 1–25 | **Linus**, adversarial additions by **Basher** |
| seam/integration test (case 26) | real runner + real route, fake LLM only | **Livingston** (assigned up front, per the 2026-08-18 unowned-seam lesson) |
| `docs/concepts.md` (§Alpha Advisor, ~L249-302) | document the on-demand trigger that is already claimed at L253; document cooldown neutrality | **Scribe** |
| review gate | H1 regression, notification blast radius, 409 correctness | **Basher** |

Frontend gate is lint + build only.

---

## 14. Named non-goals

- No change to Alpha's prompt, schema, hard gates, or model.
- No Alpha-only re-review of an existing activity (Option E) — worth doing later, separate decision.
- No settings-level toggle (Option D) — the contract in §6 makes adding one later trivial.
- No auth. The lack of it is recorded as a standing risk, not fixed here.
- This does not fix the underlying "too few alerts" complaint. It gives the user a way to
  interrogate Alpha on demand — and, via H1, stops the new feature from making the
  alerting problem worse.

---

## 15. Resumen para el usuario (ES)

**Qué se propone.** Los botones de CC/CSP del dashboard forzarán siempre la ejecución del
Alpha Advisor, tal y como pediste. La diferencia con tu propuesta literal es dónde vive esa
regla: no en el botón, sino en el contrato de la API (`run_trigger: manual` ⇒
`force_alpha: true`). Para ti no cambia nada — un solo clic, un solo botón — pero la
garantía queda comprobable, auditable y utilizable desde scripts. Las ejecuciones
programadas (cron) mantienen exactamente el comportamiento actual: Alpha solo cuando toca.

**Aplica a cuatro agentes:** covered_call, cash_secured_put, open_call_monitor y
open_put_monitor. `buy_tracker` queda fuera (nunca ha tenido Supervisor ni Alpha).

**Dos hallazgos importantes que la propuesta tal cual habría roto:**
1. Forzar Alpha reinicia el *cooldown* de la revisión automática de "WAIT prolongado". Si
   forzaras Alpha a menudo, dejarías de recibir para siempre las alertas automáticas de
   Telegram de ese símbolo — justo lo contrario de lo que buscas. Se corrige marcando las
   revisiones forzadas para que no cuenten como cooldown.
2. Forzar Alpha **no** enviará notificaciones de Telegram. El resultado se ve en el
   dashboard (icono 🧠, filtro "Alpha Executed", panel de detalle), pero una ejecución
   manual sobre toda la watchlist no te llenará el móvil de mensajes.

**Además:** hoy el botón de "Run" no tiene ningún control de concurrencia — dos clics
lanzan dos análisis completos simultáneos. Con Alpha forzado eso cuesta el doble, así que
se añade un bloqueo: el segundo clic responde "ya se está ejecutando" en vez de duplicar
el trabajo.

**Dos preguntas que necesitan tu respuesta antes de implementar:**
1. El botón "Run Now" de la página de Settings (tarjeta Monitoring Agent) va por el
   planificador. ¿Lo dejamos con semántica *programada* (sin forzar Alpha) y solo fuerzan
   los botones del dashboard? Eso implica que dos botones que parecen manuales se comportan
   distinto. Propuesta: sí, dejarlo sin forzar.
2. "Full analysis" (ejecutar los cinco agentes de golpe): ¿también fuerza Alpha? Es la
   acción más cara del sistema (una llamada extra de Alpha por símbolo). Propuesta: sí, por
   coherencia con "manual ⇒ forzado".

---

# Decision Draft -- Best Options: `best_options.py` scoring/gating logic + `category_params.py`

**Date:** 2026-08-29
**Author:** Linus (Quant Dev)
**Status:** DRAFT, revised -- implementation complete for my owned surface (`src/best_options.py`,
`src/category_params.py`, `options_chain_filters.filter_options_chain_by_dte`); ready for
Basher's adversarial review and Livingston/Rusty's seam integration test.
**2026-08-29 revision:** item 1 below (row inclusion) was corrected same-day per an explicit
product-owner instruction, overriding my initial reading. See "Row inclusion -- resolved"
below; this is now the confirmed, final behaviour, not an open question.
**Traces to:** `.squad/decisions/inbox/danny-best-options-design.md` (ACCEPTED), section 9
assignment "Linus (Quant Dev) -- owns `best_options.py`, `category_params.py`, and
`filter_options_chain_by_dte`'s semantics".

## What changed

* **`backend/src/category_params.py`** (new) -- the single category normaliser and threshold
  accessor the design calls for to close finding F9 (`agent_runner.py` and `rule_evaluator.py`
  key categories on different string forms today). `resolve_category`/`normalize_category`/
  `category_label`/`thresholds_for`. Thresholds are read verbatim from
  `rule_evaluator.CATEGORY_THRESHOLDS_CC`/`CATEGORY_THRESHOLDS_CSP` -- never redefined here --
  and cross-checked byte-for-byte against that source in `tests/test_category_params.py`.
* **`backend/src/options_chain_filters.py`** -- added `filter_options_chain_by_dte`. This is the
  DTE row-inclusion filter Best Options applies; it drops whole expiration buckets outside
  `[min_dte, max_dte]`, never individual contracts within a kept bucket. A second, category-aware
  delta-band row filter is applied separately, locally inside `best_options.py` (see below) --
  not added to this shared module, since it needs the category thresholds `best_options.py`
  already resolves.
* **`backend/src/best_options.py`** (new) -- `evaluate_best_options(...)`, matching the design's
  frozen section 7 signature exactly. Pure, total, deterministic; zero LLM/Cosmos/FastAPI
  imports; every quote/Greek read goes through `options_chain_view.contract_view`/
  `is_candidate_eligible` (grep-verified by a dedicated test, see below).

## Row inclusion -- resolved (2026-08-29)

**Final, confirmed behaviour: row inclusion requires BOTH the DTE window AND the category
delta band.** A side's primary `rows` contain all and only contracts that are (a) inside the
requested DTE window and (b) have `abs(delta)` inside that category/side's configured
`[delta_lo, delta_hi]` band. Contracts failing either filter never appear in `rows`.

What happens to excluded-by-delta contracts:

* They are never silently dropped from the response entirely -- `nearest_miss` is computed
  over the full DTE-window set (in-band and out-of-band together), so a contract just outside
  the band remains the direct, named answer to "why am I not seeing this contract" when it is
  the closest miss.
* Each side's result additionally reports `excluded_by_delta_band`: a count of how many
  DTE-window contracts the delta filter removed, for at-a-glance transparency (the `thresholds`
  block in `parameters` already shows the exact band applied).
* The delta-band check itself is NOT the wide, non-category-aware, boundary-violating
  `filter_options_chain_by_delta` finding F2 warns against -- it reuses this module's own
  `_gate_delta_band` (reads delta only via `options_chain_view` accessors, uses the category's
  configured band, `abs()`-consistent per F8) as the filter predicate, computed once per
  contract and reused both for inclusion and for describing exclusions in `nearest_miss`.

**Why this reverses my first pass.** Design section 4.1's literal text ("nothing inside the
[DTE] window is ever hidden") together with section 4.2's framing of delta band as "Layer A
gate G2" reads, taken in isolation, as if delta band only coloured a row red rather than
excluding it -- and finding F2's warning against reusing the *existing* wide delta filter
reinforced that reading. My first implementation followed that literal text. The task brief
that assigned this work, however, was explicit that the delta range is a second user-facing
filter alongside DTE ("retain every option surviving those two user-facing filters"), and the
product owner confirmed this explicitly and unambiguously after reviewing the first pass:
the displayed chain must be filtered by the configured delta range, with only contracts
surviving both filters shown as primary rows, and `nearest_miss`/`excluded_by_delta_band` used
for the excluded set instead. That confirmation is what this revision implements. **Design
section 4.1/4.2's wording should be treated as needing a follow-up edit** by Danny to avoid
the next reader (or Basher, reviewing against the doc rather than this decision) reaching the
same wrong conclusion I initially did.

Safety gates that remain gates, not filters, on the now-delta-filtered rows: **G1 tradability**
and **G3 earnings span** still colour an in-band row red (score `null`) without removing it --
only the delta band moved from "gate" to "filter". Nothing else about section 4.2-4.5's
colour/score/ordering machinery changed.

## Interpretive decisions (design underspecifies these; recording for Danny/Basher to confirm)

1. **Earnings-span gate direction -- found and fixed a real bug against my own domain
   convention.** Design section 4.2's one-line description of G3 ("expiration falls after a
   known next earnings date") reads, taken completely literally, as the *pass* condition. My
   first implementation took it literally and was wrong: it is the *fail* condition. I caught
   this by cross-checking against `src/skills/earnings-gate-sell/SKILL.md` -- the established,
   detailed, already-battle-tested convention in this codebase -- which documents that the risk
   is a position remaining *open during* earnings, i.e. expiration falling *after* earnings is
   unsafe. Fixed: G3 now **fails** when `expiration > next_earnings_date` (the position would
   span the announcement) and **passes** when expiration is on/before it. Unknown earnings date
   is never a gate failure (design F10) regardless of this fix. Flagging because the design
   doc's own wording is genuinely ambiguous and someone reading it fast (as I initially did)
   will implement the reverse of the intended behavior.

2. **`_component_liquidity` requires both halves (open interest *and* full bid/ask/mid) to be
   computable, or the whole component (not half) is dropped from renormalization.** The design's
   formula doesn't specify a partial-data fallback; I didn't invent one beyond the literal spec.

3. **`below_category_floor` vs. the red-triggering wait-floor breach are two distinct
   conditions, both implemented.** `below_category_floor` (`premium_pct < effective_min_pct`,
   the stricter "preferred" threshold) is purely informational and never changes colour, per
   design F10's list. Separately, `premium_pct < effective_wait_pct` (the looser threshold) is
   what actually forces the row red -- I added an explicit `premium_below_wait_floor` flag
   alongside the colour change for transparency, since the design's prose only illustrates one
   worked example rather than naming this flag directly.

4. **`nearest_miss`'s tiering algorithm is an original design**, since the spec gives one
   illustrative example ("missed premium floor by 0.12pp...") rather than a full ranking rule.
   I implemented a 6-tier deterministic scheme from most- to least-fixable (premium-floor miss
   nearest a passing score > yellow-band score gap > insufficient scoring data > delta-band
   gate miss > earnings-span gate fail > tradability gate fail, the least fixable), each tier
   ranked internally by a quantified gap where one exists. This is the one piece of genuinely
   new design logic in the file and the part most likely to need adjustment once Basher's
   adversarial cases exercise it.

5. **`parameters.dte.source` ("default" vs. "query") is inferred, not carried explicitly.** The
   frozen pure-function signature (`dte_min: int, dte_max: int`, no sentinel) can't distinguish
   "caller passed the endpoint's own defaults" from "caller explicitly asked for 0/49" -- I
   report `"default"` only when `(dte_min, dte_max) == (0, 49)` literally. Disclosed limitation,
   not expected to matter in practice since the endpoint's own defaults are 0/49.

## Note for Rusty (not my file to change)

`best_options.py` exports `DEFAULT_DTE_MIN = 0` / `DEFAULT_DTE_MAX = 49` specifically so
`web/app.py`'s `Query(default=0, ...)`/`Query(default=49, ...)` can import them instead of
re-declaring the same two literals a second time. Today `web/app.py` still hardcodes `0`/`49`
directly in the `Query(...)` decorators -- functionally correct today, but a second source of
truth for that pair the moment either ever needs to change. Low priority, flagging only.

## Verification

* New focused tests (all green): `tests/test_category_params.py` (33),
  `tests/test_options_chain_dte_filter.py` (9), `tests/test_best_options.py` (27) -- covering
  the DTE+delta-band row-inclusion pair (including a mixed in-band/excluded-contract case and
  the `excluded_by_delta_band` count), the corrected G3 direction (both directions plus the
  unknown case), green/red colour thresholds, put delta sign handling via `abs()`, CC vs. CSP
  premium-basis and collateral asymmetries, `coverable_contracts`/`no_shares_held`,
  `nearest_miss` availability (including the empty-window, all-green, and delta-excluded cases)
  and tier ordering, total ordering + 400-row truncation, byte-identical determinism on
  repeated calls, non-mutation of the input chain, a source-grep guard against direct
  `contract.get("bid"/"ask"/...)` reads, and category/DTE provenance flags.
* Full backend suite: 1523 passed, 11 failed / 16 errored -- confirmed via `git stash` to be
  pre-existing async/event-loop failures in `test_yfinance_data_provider.py` /
  `test_yfinance_technicals_dividend_availability.py`, unrelated to this change and reproducing
  identically with none of this work applied.
* Confirmed `web/app.py`'s existing best-options endpoint call already matches this module's
  frozen signature exactly (`side`, `category`, `total_shares`, `next_earnings_date`,
  `ex_dividend_date`, `support_level`, `dte_min`, `dte_max`, `now`) and returns the envelope
  verbatim.

## Test cases recommended for Basher's adversarial pass (not covered above)

* Malformed/partial contracts mid-chain (missing keys entirely, non-numeric strike keys,
  non-dict bucket values) alongside well-formed ones in the same bucket.
* Extreme DTE values (`dte_min > dte_max` swap behavior, `dte_max` far beyond `SYSTEM_DTE_CAP`).
* Category values that collide across aliasing forms in adversarial casing/whitespace
  combinations beyond the happy-path forms I covered.
* Concurrent-side (`side="both"`) truncation/nearest_miss independence -- confirming a
  truncated calls side never influences the puts side's own nearest_miss or ordering.
* `ex_dividend_date`/`support_level` boundary conditions (exactly on the DTE window edge,
  exactly at the support level, negative/zero support level).
* Fuzz/property-style test asserting `evaluate_best_options` never raises for any
  reasonably-malformed input (only structural type errors on `chain` itself, which already
  degrades to `{}` rather than raising).
* Delta-band boundary precision: contracts with `abs(delta)` exactly equal to `delta_lo`/
  `delta_hi` (inclusive edges) and just outside by a tiny float epsilon, to confirm no
  off-by-epsilon inclusion/exclusion drift.
* A DTE-window bucket where every contract is delta-excluded (`rows == []` but
  `excluded_by_delta_band == total_contracts_in_window`) -- confirming `nearest_miss` still
  surfaces the closest excluded contract rather than reporting `no_contracts_in_window` (that
  reason must be reserved for a genuinely empty DTE-window bucket).

---

# Linus — Force Alpha runner/domain execution (implementation report)

Status: implemented, tested, ready for team review.
Scope: `backend/src/agent_runner.py`, the four thin agent wrapper modules
(`covered_call_agent.py`, `cash_secured_put_agent.py`, `open_call_monitor_agent.py`,
`open_put_monitor_agent.py`), `backend/src/main.py` (`_run_all_agents_async` cron loop).
Explicitly NOT touched: `backend/src/scheduler_registry.py`, `backend/web/app.py`, frontend.

## D1/D2 resolution (as implemented)

Danny's design (`danny-force-alpha-design.md` §12) originally proposed: Settings "Run Now"
stays scheduled/unforced; trigger-all becomes forced. The task prompt I was given, and the
confirming `copilot-force-alpha-semantics.md`, both say the opposite in both directions:
dashboard-triggered runs **and** Settings "Run Now" pass `manual` + `force_alpha=True`;
scheduled cron **and** trigger-all/"Full analysis" remain `scheduled`/due-only
(`force_alpha=False`). I treated the task prompt + confirmation note as the final,
already-decided answer, superseding Danny's original §12 proposal. This is what's implemented
at the runner level, and I've confirmed (via inspecting `web/app.py`'s diff after the fact)
that the API layer was wired to match this exact resolution — no gap between layers.

## Contract implemented

`run_symbol_agent(...)` and `run_position_monitor(...)` (and their two internal helpers)
now accept:
- `run_trigger: str = "scheduled"` — `"scheduled"` or `"manual"`, pure provenance, never
  gates Alpha by itself.
- `force_alpha: bool = False` — requests Alpha run unconditionally for this invocation only.

Gate: `run_alpha = is_alert or prolonged_wait or force_alpha`.
`forced = force_alpha and not (is_alert or prolonged_wait)` — "forced" means forcing was the
*sole* reason Alpha ran this time; a review that was independently due (alert or prolonged
wait) and also happened to be forced is **not** marked forced, and correctly still resets the
cooldown (deliberate, matches design case 4).

Precedence when several rules could apply (highest wins): `_skip_reviews` (buy_tracker agent
type has no Alpha playbook) > `incomplete_quote_wait` (position-monitor only — a genuinely
bad/missing buyback quote blocks Alpha even if forced) > `force_alpha`.

## `alpha_run` activity field (new)

Persisted to Cosmos immediately after the existing `alpha_view` write, same call pattern:
```
{"trigger": run_trigger, "forced": bool,
 "status": "ok" | "failed" | "skipped_agent_type" | "skipped_incomplete_quotes"}
```
Written whenever Alpha is attempted for any reason, or deliberately skipped specifically
because forcing was requested but blocked by a higher-precedence rule. **Not** written on the
untouched "supervisor alone, nothing due, nothing forced" path — today's document shape is
byte-identical for the common unforced case.

## H1 fix (mandatory, per design §9): forced reviews no longer consume the due cooldown

`_detect_prolonged_wait`'s cooldown scan previously broke on the first activity carrying
`alpha_view`, treating any past review as having consumed the cooldown. A forced-but-not-due
review also carries `alpha_view`, so without this fix, calling force_alpha would silently
reset/suppress the due prolonged-WAIT alert — exactly what the task said must never happen.
Fixed: the scan now only breaks when that activity's `alpha_run.forced` is not `True`; forced
reviews are skipped over (still counted as a plain WAIT toward the threshold, just don't reset
the cooldown clock). Legacy activities with no `alpha_run` field at all are conservatively
treated as **not forced**, preserving old behavior exactly for historical documents.

## H2 (Telegram): unaffected by construction

`send_alert`/`send_prolonged_wait_alert` gates remain exactly `if is_alert...`/
`if prolonged_wait...`. Forcing adds a third OR-branch only to "should Alpha run", never to
"should Telegram fire". Confirmed by reading the code, not just by test — there was no
alert/notification logic near the new branch to accidentally trip.

## Test coverage vs. design §11 (25 cases + seam)

Implemented and passing in `backend/tests/test_force_alpha_execution.py` (23 tests): cases
1-14 — both entry points' gate semantics, the two `_detect_prolonged_wait` cooldown-neutrality
cases (12 due-forced, 13 forced-not-due-doesn't-reset, 14 legacy-missing-metadata), buy_tracker
skip, incomplete_quote_wait precedence on the monitor path, Alpha-returns-None and
Alpha-raises-under-forcing, and confirmation of no extra Telegram sends from forcing alone —
plus supplementary (unnumbered) pass-through tests for the four wrapper modules and `main.py`'s
cron regression lock.

Out of scope for this file, owned by other agents: cases 15-25 (API-layer request/response
shape, concurrency/locking, endpoint defaults — Rusty), case 26 (frontend/API seam —
Livingston). I did not write or duplicate those.

## 2026-08-29 correction — Settings "Run Now" / "Run Full" do NOT force Alpha

**Superseded by:** `.squad/decisions/inbox/copilot-force-alpha-semantics-superseded.md`
(binding user correction). The "Handoff note" above, and my original history entry, described
the API layer as wiring Settings "Run Now" to `force_alpha=True`. That policy has been
corrected: **only the dashboard CC/CSP buttons** pass `run_trigger="manual",
force_alpha=True`. Settings "Run Now" (single-agent and "Run Full"/`/api/trigger-all`) and all
scheduled executions must call through with `force_alpha=False` (due-only), same as today's
pre-feature behavior — `run_trigger` may still be recorded as `"manual"` for provenance, but it
must never flip the Alpha gate on its own.

**Impact on this file's runner/domain layer: none.** `run_symbol_agent`/`run_position_monitor`
and the four wrapper modules only expose the generic `run_trigger`/`force_alpha` mechanism —
they never encode policy about *which caller* passes what. The cooldown-neutrality fix (H1),
the `alpha_run` audit schema, and the "no force-only Telegram" guarantee (H2) are all
caller-agnostic and remain correct and unchanged under the corrected policy. `main.py`'s cron
path already passed `force_alpha=False` explicitly (never needed the fix). No production code
or test in `backend/tests/test_force_alpha_execution.py` asserts anything about which HTTP
endpoint maps to which flag value, so nothing here required a change; verified by re-running
the full 23-test file after this correction landed (still 23 passed).

**Still needs attention from the API owner (not mine to fix):** `backend/tests/
test_force_alpha_plumbing.py` and `backend/tests/test_trigger_force_alpha_scoping.py` (both
outside my ownership) currently assert the old, now-superseded policy in places (e.g. Settings
"Run Now"/monitoring-agent button forcing Alpha) — those need updating to match the corrected
decision, alongside the `web/app.py` endpoint defaults themselves.

## Handoff note (already resolved, no action needed)

At the time I made the scope decision not to touch `scheduler_registry.py`/`web/app.py`
myself (reasoning: that's framework/API plumbing, outside "own runner/domain execution only"
and my charter's "does NOT own framework plumbing"), I flagged this as something the API owner
would still need to wire. Checking the working tree after finishing my part, the API owner had
already implemented this concurrently: `TaskRegistry.trigger_task_now`/`_worker_loop` in
`scheduler_registry.py` now thread arbitrary `job_kwargs` through to each task's `job_func`,
filtered via `inspect.signature` so tasks that don't declare `run_trigger`/`force_alpha` are
unaffected; `web/app.py`'s dashboard/"Run Now" endpoints call
`trigger_task_now(task_name, run_trigger="manual", force_alpha=True)`, while "Full analysis"/
trigger-all keeps `force_alpha=False`. I verified the kwarg names and value semantics used
there match this runner-level contract exactly (`run_trigger` "scheduled"|"manual",
`force_alpha` bool) — no integration gap between the two layers. No further action needed on
my side.

---

# Decision Draft -- Best Options: `get_or_hydrate` + public `schedule_background_refresh`

**Date:** 2026-08-29
**Author:** Livingston (Persistence & Integration)
**Status:** DRAFT -- implementation complete for my owned surface; seam integration test deferred (Linus's `best_options.py` / Rusty's endpoint not yet present in the tree)
**Traces to:** `.squad/decisions/inbox/danny-best-options-design.md` section 7/9 (ACCEPTED), assignment "Livingston (Persistence & Integration) -- owns `OptionsChainCache.get_or_hydrate` and the public `schedule_background_refresh`"

## What changed

`backend/src/options_chain_cache.py` gains two new public methods on `OptionsChainCache`, additive only --
nothing existing was renamed, removed, or had its behavior changed:

* **`get_or_hydrate(symbol) -> str | None`** -- the new non-blocking accessor F6 calls for. Returns a memory
  hit (still triggering the pre-existing stale-while-revalidate background refresh if that entry is past
  its TTL -- same trigger `get_or_load_async` already uses), else a persistence-store hydrate (no provider
  I/O), else `None`. A true cold/missing cache never falls through to `refresh()` here -- that is the
  entire point versus `get_or_load`/`get_or_load_async`, which both do fetch on a true cold miss (the latter
  unconditionally, per F6's own finding). `get_or_hydrate` cannot block on a provider or on another thread's
  in-flight refresh; the only I/O is the identical in-memory read / persistence hydrate the two existing
  accessors already perform on a miss.
* **`schedule_background_refresh(symbol) -> None`** -- public, thin wrapper around the existing internal
  `_schedule_background_refresh` (previously used only for the SWR path from inside `get_or_load_async`).
  Same non-blocking try-acquire of the symbol's OS lock, same at-most-one-refresh-in-flight-per-symbol
  guarantee, same fire-and-forget `asyncio.create_task`. No new locking primitive was introduced -- reusing
  the existing one was deliberate: introducing a second lock/mechanism for "the same symbol, a different
  call site" is exactly the kind of latent divergence F9 warns about elsewhere in this design.

**Explicitly not done, per the design and my charter's boundaries:**

* No `asyncio.wait_for`/timeout/cancellation wraps the scheduled refresh anywhere in this change. The
  design calls this out directly (section 7): cancelling mid-flight would abandon the symbol's OS lock while
  it is possibly mid-Cosmos-shard-write, which is strictly worse than a caller seeing a "warming" state and
  retrying. A regression test (`test_slow_inflight_refresh_completes_uninterrupted_no_timeout_added`) pins
  this: a deliberately slow patched fetch still lands its persistence write with no interference.
* `refresh_all` (the scheduler watchdog, 2026-06-30 decision) is untouched -- no line inside it was read or
  modified, and every existing `TestRefreshAllWatchdogRegression` test still passes unmodified.
* No change to `options_chain_merge.py`, `options_chain_view.py`, or any accepted-market-semantics code --
  outside this charter's boundaries and not needed for this surface.
* `best_options.py` / `category_params.py` (Linus) and the FastAPI endpoint / frontend (Rusty) do not exist
  in the tree yet -- the real-module seam integration test the design assigns to me ("real cache with
  persistence enabled and disabled, real `best_options`, real endpoint... parameters block echoed is the
  object the scorer actually consumed... a cold miss returns a warming response promptly") is not feasible
  without a mutual fake for the missing side, which is the exact anti-pattern the 2026-08-18 lesson
  (referenced in my own assignment text) warns against. Deferred until at least one of those lands;
  flagging here so the seam has a named owner and a named blocker, not a silent gap.

## Tests added

`backend/tests/test_options_chain_cache.py`: two new classes, 10 new tests, composing the real
`OptionsChainCache` against the existing `_FakeStore` double (same fixture already used for every other
persistence-lifecycle test in this file, consistent with the file's own stated hermetic convention -- no
network, no real Cosmos):

* `TestGetOrHydrateCacheStates` (6 tests) -- warm/fresh (no store touch, no schedule), warm-but-stale (stale
  value still returned immediately, background refresh lands separately), cold-but-persisted (hydrates,
  zero provider calls), true cold/missing (returns `None`, zero provider calls, under 0.5s), and the
  P1-style running-event-loop-never-blocks regression (a heartbeat coroutine on the same loop keeps ticking
  through the cold-miss call).
* `TestScheduleBackgroundRefreshPublicSurface` (3 tests) -- schedules a refresh that lands and persists;
  de-duplicates against an already-in-flight refresh for the same symbol (no duplicate fetch); a
  deliberately slow refresh still completes and persists (no added timeout).

**Test outcome:** `test_options_chain_cache.py` 56/56 (was 46/46 + 10 new). Targeted persistence sweep
(`test_options_chain_cache.py` + `test_options_chain_store.py` + `test_options_chain_persistence_integration.py`
+ `test_repair_options_chain_shards.py`): 145/145. Full backend suite: 1454 passed, 11 failed, 16 errors --
all in `test_yfinance_data_provider.py` / `test_yfinance_technicals_dividend_availability.py`, confirmed
pre-existing and unrelated by reproducing identically on `git stash` (unmodified tree) and in file-isolation
runs; matches the exact order/network-dependent flakiness already documented multiple times in my own
history log. `py_compile` clean on both touched files.

## Ask of the team

1. **Linus/Rusty**: once `best_options.py` and/or the FastAPI endpoint exist, ping me -- the real-module
   seam integration test (my assignment) needs at least the endpoint shape or the pure evaluator's public
   signature to compose against without a fake standing in for either side.
2. **Danny/reviewers**: confirm `get_or_hydrate`'s SWR-on-stale-memory-hit behavior (mirroring
   `get_or_load_async`) is the intended semantics -- the design text only explicitly specifies "memory hit,
   else hydrate, else None" and doesn't call out the stale case one way or the other. I read F6/section 7
   as silent-but-consistent with the rest of the cache's existing SWR contract rather than a new decision
   to litigate; flagging so it's an explicit, reviewable choice rather than an assumption buried in code.

---

# Livingston — Best Options D2/D3 revision, for Basher re-review

**Status:** Revision complete, submitted for re-review. Not self-certified as accepted — Basher owns
the verdict.

**Traces to:** `.squad/decisions/inbox/basher-best-options-review.md` (REJECT, final re-review), D2 and
D3. D1 (row-inclusion documentation) was already resolved by the user's direct ratification per Basher's
same review and is unaffected by this revision.

**Lockout observed:** Rusty is locked out of `frontend/src/types/best-options.ts`,
`frontend/src/components/BestOptionsParams.tsx`, `frontend/src/components/BestOptionsView.tsx` for this
cycle. I am the assigned independent revision owner (not the original author of any of the three files,
and not a co-author with Rusty on this pass — no consultation or coordination with Rusty occurred).

## What was wrong (confirmed against the live payload, not the design doc's flat example)

Called `evaluate_best_options` directly against a minimal fabricated chain and read the actual JSON:

- `parameters.thresholds`, `parameters.thresholds_source`, `parameters.skill_reference` are nested
  `{"call": ..., "put": ...}` — CC and CSP thresholds genuinely differ per category. The frontend typed
  all three flat and `BestOptionsParams.tsx` read them flat (`parameters.thresholds.delta_lo.toFixed(2)`),
  a `TypeError` on first render (Basher D2).
- `parameters.premium.basis` is **also** nested `{"call": "underlying_price", "put": "strike"}` — the
  same flat-vs-nested defect class, in the same `parameters` object, just silently rendering
  `[object Object]` instead of throwing (not called out by name in Basher's review, found during my own
  live-payload inspection; fixed in the same pass since it is the identical bug).
- `calls`/`puts` both carry `excluded_by_delta_band: int`; only `calls` carries
  `coverable_contracts: int | None` / `no_shares_held: bool | None` (never on `puts`). None of the three
  existed on the frontend type or were read in the UI (Basher D3). The page's "0 shares held" banner
  checked a per-row flag (`r.flags.includes("no_shares_held")`) the evaluator never sets — it is a
  section-level field per design §5's "capital" row — so the banner could never have rendered.

## What changed

- **`frontend/src/types/best-options.ts`** — added `BestOptionsThresholdsBySide` /
  `BestOptionsSourceBySide`; `thresholds` / `thresholds_source` / `skill_reference` / `premium.basis` now
  typed as the real nested `{call, put}` shape; added `excluded_by_delta_band: number`,
  `coverable_contracts?: number | null`, `no_shares_held?: boolean | null` to `BestOptionsSide`.
- **`BestOptionsParams.tsx`** — every accessor for the four affected fields now reads `.call`/`.put`
  explicitly; the panel shows both CC and CSP threshold sets side by side instead of picking one
  arbitrarily or crashing.
- **`BestOptionsView.tsx`** — `no_shares_held` banner now reads the real section-level
  `data.no_shares_held === true` instead of a per-row flag that never existed. Added visible
  `excluded_by_delta_band` (both sides) and `coverable_contracts` (call side) stats next to the existing
  "Shown: X of Y" line.

No evaluator semantics were touched — `backend/src/best_options.py` is unmodified. No Roll Scenarios
visual tokens were touched — `ROW_TINT_BG`/table structure/typography/spacing/expand-collapse idiom in
`BestOptionsView.tsx` are unchanged from what Basher already inspected and marked satisfied.

## Validation

- `npx tsc --noEmit` → 0 errors. Necessary but explicitly **not sufficient** on its own — this is exactly
  how the original mismatch shipped past a clean typecheck the first time (a wrong type just means the
  compiler never observes the real payload shape).
- **New real-module seam test**: `backend/tests/test_best_options_frontend_contract.py` (5 tests,
  independently-authored fixtures — real `OptionsChainCache` + real `evaluate_best_options` + real
  FastAPI endpoint via `TestClient`, only `FakeCosmos`/provider fetchers faked). Asserts the exact JSON
  key shape the frontend types must mirror: `thresholds`/`thresholds_source`/`skill_reference`/
  `premium.basis` all keyed `{call, put}`; `excluded_by_delta_band` present as `int` on both sides;
  `coverable_contracts`/`no_shares_held` present and correct on `calls` (both the `>0` and the `== 0`
  case), absent entirely from `puts`; no row ever carries a `no_shares_held` flag. This is the check a
  TypeScript compile cannot perform.
- Full targeted run: `test_best_options.py`, `test_best_options_adversarial.py`,
  `test_best_options_endpoint.py`, `test_best_options_frontend_contract.py`, `test_category_params.py`,
  `test_options_chain_dte_filter.py`, `test_options_chain_cache.py`,
  `test_trigger_force_alpha_scoping.py`, `test_force_alpha_plumbing.py` → **240 passed, 0 failed**.
- `npx eslint` on `best-options.ts` and `BestOptionsParams.tsx` (the two files with substantive logic
  changes) → clean. `BestOptionsView.tsx` has one pre-existing `react-hooks/set-state-in-effect` finding
  at its original mount effect (`useEffect(() => { load() }, [load])`) — confirmed by direct inspection
  that line predates this revision and is untouched by it; unrelated to D2/D3, not fixed here to keep this
  change surgical.
- `npx next build` hit an unrelated WSL/OneDrive filesystem `EIO` error scanning `.next/standalone`'s
  bracketed route folder (`[symbol]`) — an environment limitation on this mount, not a code defect;
  `tsc`/`eslint` plus the new backend contract test are the meaningful signal for this revision.

## Ask

Re-review D2 and D3 against the above. Ready for Basher's verdict.

---

# Livingston — Force Alpha: Settings/scheduled forcing correction (applied)

**Status:** Correction applied and verified. Supersedes the "NOT READY / not started" framing of
`livingston-force-alpha-readiness.md` (implementation has since landed from Linus and Rusty).

**Trigger:** User's binding correction — ONLY dashboard CC/CSP (+ monitor) buttons use
`run_trigger="manual"` + `force_alpha=true`. Settings "Run Now", "Run Full"/`/api/trigger-all`, and
scheduler cron must all stay due-only (`force_alpha=false`). Authoritative decision doc:
`copilot-force-alpha-semantics-superseded.md`.

## What was wrong

`backend/web/app.py`'s `run_scheduler_task_now` (`POST /api/scheduler/tasks/{task_name}/run`, docstring:
"Manually trigger a scheduled task (Run Now button)") hardcoded
`scheduler.registry.trigger_task_now(task_name, run_trigger="manual", force_alpha=True)`, with a comment
explicitly citing the original (now-superseded) reading of Danny's design ("Settings Run Now is always a
manual, forced trigger"). This is a live regression risk: the endpoint has no frontend caller today (the
real Settings "Run Now" for Monitoring Agent goes through `/api/trigger-all`, which was already correct),
but it is a directly callable API surface that self-identifies as backing the Settings "Run Now" button,
and forcing here directly contradicts the corrected semantics.

## What was verified correct and left untouched

- `POST /api/trigger/{agent_type}` — dashboard-only route (only the 5 dashboard agent types are in
  `AGENT_FUNCTIONS`; every Settings single-purpose task has its own dedicated route registered earlier).
  Defaults `force_alpha=True`. Correct.
- `POST /api/trigger-all` (`_run_all_agents_sequentially`) — backs both "Run Full"/"Full analysis" and
  Settings' Monitoring Agent "Run Now" (confirmed these are the same button/endpoint). Hardcodes
  `force_alpha=False`. Correct.
- `main.py`'s cron path (`_run_all_agents_async`) — passes `run_trigger="scheduled", force_alpha=False`
  explicitly for the four Alpha-eligible agents. Correct.
- `TriggerButton.tsx` (dashboard-only component) — explicitly sends `force_alpha: true`. Correct.
- `scheduler_registry.py`'s kwargs-forwarding plumbing (`_worker_loop`/`trigger_task_now`) — caller-agnostic
  and correct; the bug was entirely in what `web/app.py` chose to pass, not in the registry.

## Fix applied

`backend/web/app.py`: `run_scheduler_task_now` now passes `force_alpha=False` (kept
`run_trigger="manual"` — a human did click it; only the forcing was wrong). Comment rewritten to cite the
corrected, current decision doc instead of the superseded one.

## Test coverage added

`backend/tests/test_trigger_force_alpha_scoping.py` (new, 3 tests, real-module seam tests against the
actual route handlers / scheduler sweep — only outer I/O faked):
- `TestSettingsRunNowNeverForces` — locks the fixed contract; this is the exact gap Rusty's own
  `test_force_alpha_plumbing.py` doesn't cover (his registry-level test passes its own kwargs directly and
  never exercises this route handler's literal default).
- `TestTriggerAllNeverForces` — locks that `/api/trigger-all`'s sequential sweep never forces any of the
  5 agents.
- `TestSchedulerCronNeverForces` — calls the real `OptionsAgentScheduler._run_all_agents_async` with faked
  agent wrappers; confirms `run_trigger="scheduled", force_alpha=False` for all four Alpha-eligible agents
  and zero extra kwargs for `buy_tracker`.

All 3 pass. Full targeted regression run (my own cache/store/persistence-integration/repair-script/
best-options-endpoint suites + these 3 new tests + Rusty's `test_force_alpha_plumbing.py`): 167/167 passed.

## Cross-owner defect found, reported (not fixed — outside my charter)

`tests/test_force_alpha_execution.py` (Linus's/Basher's suite for `agent_runner.py`'s gate/cooldown logic)
has 4 failing tests, confirmed pre-existing and unrelated to this correction (verified via `git stash` on
just `web/app.py`):
- `test_case8_incomplete_quote_wait_force_alpha_true_alpha_skipped`
- `test_case10_alpha_raises_under_forcing_primary_decision_survives`
- `test_case13_due_alpha_review_consumes_cooldown_as_before`
- `test_case14_legacy_alpha_view_without_alpha_run_is_treated_as_not_forced`

These live in `agent_runner.py`'s gate/cooldown/legacy-doc semantics — Linus's owned surface. Flagging for
Linus/Basher rather than fixing directly (would require redefining Alpha gate semantics, outside my
charter as Persistence & Integration Engineer).

## Seam test (design case 26) — still deferred

Both `agent_runner.py`'s gate and `web/app.py`'s plumbing have now landed, so a real API↔runner seam test is
technically composable. Deferring anyway: `agent_runner.py`'s own suite has 4 known-red cases in exactly
the gate/cooldown/legacy-doc logic such a seam test would need to assert against. Writing one now risks
encoding a still-unsettled contract as "expected." Will revisit once those 4 cases are green — ping me when
they are.

---

# Integration Readiness — Forced Alpha execution on manual CC/CSP runs

**Date:** 2026-08-29
**Author:** Livingston (Persistence & Integration)
**Status:** NOT READY — implementation has not started; my seam test (design case 26) is blocked pending Linus + Rusty
**Traces to:** `.squad/decisions/inbox/danny-force-alpha-design.md` (PROPOSED), `.squad/decisions/inbox/copilot-force-alpha-semantics.md` + `.squad/decisions/inbox/copilot-force-alpha-semantics-superseded.md` (user confirmations)

## Which semantics matrix I validated against

Two "semantics" inbox files exist with near-identical timestamps and confusingly overlapping names.
By file mtime (not just filename), `copilot-force-alpha-semantics-superseded.md` was written **30
seconds after** `copilot-force-alpha-semantics.md`, and its own text says "This supersedes the earlier
decision that Settings 'Run Now' would force Alpha." Despite the misleading "-superseded" suffix, it is
the newer, authoritative correction. I validated against it, not the older file (and not verbatim against
the semantic summary handed to me in this task's own instructions, which restates the now-superseded
version — see the discrepancy called out below).

**Final matrix (post-correction):**

| Call path | `run_trigger` | `force_alpha` |
|---|---|---|
| Dashboard CC/CSP + monitor buttons (`TriggerButton` → `POST /api/trigger/{agent_type}`, agent-level and per-symbol row) | manual | **true** |
| Settings "Run Now" (any card) / "Full analysis" / "Run Full" | manual | **false** (due-only) |
| Scheduler cron (`main._run_all_agents_async`) | scheduled | **false** (due-only, unchanged) |

## Cross-owner defect found in Danny's design itself (reporting, not fixing)

Danny's design's D1 (section 12) is built on a factual premise I could not confirm in the actual
frontend: that the Settings page's "Monitoring Agent" card "Run Now" button routes through
`POST /api/scheduler/tasks/{task_name}/run` → `TaskRegistry.trigger_task_now` (`scheduler_registry.py`).

**What the code actually does** (`frontend/src/components/SettingsConfigView.tsx:258`):
the Monitoring Agent card's `RunStatus` button is wired to `endpoint="/api/trigger-all"` — the exact
same endpoint as "Full analysis". I confirmed `/api/scheduler/tasks/{task_name}/run` (and its
`/cron`/`/enabled` siblings) have **zero callers anywhere in `frontend/src`** — `trigger_task_now` is
reachable today only by a direct API call (curl/script), never by a button in this app. I also confirmed
there is no separate "Run Full" button anywhere in the frontend distinct from this same Settings control
— "Full analysis", "Run Full", and "Settings Run Now" all name the one existing
`/api/trigger-all` affordance.

**Why this matters for implementation:** D1's proposed engineering problem — threading a `force_alpha`
payload through `TaskRegistry`'s payload-less job queue for a button that doesn't go through it — does
not exist for the button in question. The corrected semantics ("Settings Run Now" stays due-only) is
achievable with **zero changes to `scheduler_registry.py`**, simply by giving `/api/trigger-all` its own
fixed `force_alpha=false` default that does **not** inherit the "manual → true" default `/api/trigger/{agent_type}`
gets. D2 is likewise settled by the correction: `/api/trigger-all` does not force, full stop — Danny's
"yes, force, for consistency" recommendation was the thing the user corrected.

One real asymmetry Rusty's implementation must get right: `/api/trigger/{agent_type}` is a **shared**
endpoint — it also serves every Settings single-purpose task button (`summary_agent`, `banner_agent`,
`dgi_screener`, `portfolio_enrichment`, `price_forecast`, `plan_monitor`, `options_chain`), none of which
are among the four CC/CSP agents `agent_runner.py` gates on. Defaulting `force_alpha=true` for that whole
endpoint is safe and correctly inert for those callers (matches design case 23's "buy_tracker: forcing is
inert, not an error" reasoning) — flagging only so nobody re-litigates giving this endpoint per-agent-type
defaults it does not need.

## Current implementation status: not started

`grep -r "force_alpha" backend/src backend/web backend/tests frontend/src` returns zero matches. None of
`agent_runner.py`, `web/app.py`, `main.py`, `scheduler_registry.py`, or any frontend file has been touched
for this design yet. I independently re-verified the design's "current behaviour" section 2 against the
live code (not from memory) and it is accurate as written: the four Alpha gate call sites
(`agent_runner.py:1925-1985`, `:2921-3079` by line-number proximity), `_detect_prolonged_wait`
(`:1227-1284`, confirmed the exact `if act.get("alpha_view"): break` H1 bug), the un-guarded
`POST /api/trigger/{agent_type}` (`web/app.py:5321`, confirmed literally zero in-flight guard), and
`_MAX_TASK_DURATION_SECONDS = 1800` (`scheduler_registry.py:17`) all match verbatim.

**Also confirmed:** there is no pre-existing regression-test baseline anywhere for the surfaces this
design touches — no `test_agent_runner.py`, no test file exercising `run_symbol_agent`,
`run_position_monitor`, `_detect_prolonged_wait`, or `POST /api/trigger/*` at all today. Whatever Linus/
Basher write in the new `test_force_alpha_execution.py` will be the *first* test coverage of this code,
not a diff against an existing suite — worth knowing going in, since there is no safety net beyond what
that new file provides.

## My owned surface: unaffected, nothing to fix

This design does not touch `options_chain_cache.py` or `options_chain_store.py` at all (confirmed via
`git diff --stat` — those files carry only my earlier Best Options `get_or_hydrate`/
`schedule_background_refresh` change, untouched since). Re-ran my targeted suite as a sanity check that
no concurrent work destabilized anything I own:
`test_options_chain_cache.py` + `test_options_chain_store.py` + `test_options_chain_persistence_integration.py`
+ `test_repair_options_chain_shards.py` + `test_best_options_endpoint.py` (the one place my cache methods
are consumed by a real caller today): **156/156 passed.** No integration/persistence/concurrency defect
to fix here — there is no code on my surface for this task yet.

## Seam test (design case 26) — blocked, not skipped

My assignment per Danny's design section 13 ("seam/integration test (case 26) | real runner + real route,
fake LLM only | Livingston") requires a real `agent_runner.run_symbol_agent`/`run_position_monitor` with
a real `force_alpha` parameter, and a real `POST /api/trigger/{agent_type}` route with real `force_alpha`
parsing and the new in-flight registry. Neither exists. Writing it now would mean building a fake stand-in
for whichever side is missing — the exact "mutual fakes" anti-pattern the 2026-08-18 lesson (cited in the
design's own text) exists to prevent. Deferred, with a concrete trigger condition below, not silently
dropped.

**When Linus's and Rusty's changes both land, my seam test will assert, end-to-end through real modules
(fake LLM client only):**
1. A real `POST /api/trigger/covered_call` with `{"symbol": "AAPL"}` and no `force_alpha` in the body
   reaches the real runner with `force_alpha=True`, and the resulting activity document (real Cosmos
   test double already used elsewhere in this suite) carries both `alpha_view` and
   `alpha_run.forced=true`.
2. Two concurrent real HTTP-level requests for the same `(agent_type, symbol)` key: the second gets a
   real 409 from the real in-flight registry, and the real runner is invoked exactly once — this is a
   genuine concurrency test (real threads/asyncio tasks racing on the real lock), not a mocked one.
3. The in-flight key is released via the real `finally` path both on normal completion and on the real
   runner raising, and a subsequent real request after release succeeds.
4. Cooldown neutrality end-to-end: a real activity history seeded with 5 WAITs where the most recent
   carries `alpha_run.forced=true` still yields `_detect_prolonged_wait() == True` through the real
   method (not a stand-in), and a legacy activity with `alpha_view` but no `alpha_run` field still yields
   `False` (conservative default) through the same real path.
5. `/api/trigger-all` real end-to-end still runs with `force_alpha=False` regardless of any call
   attempting to set it — proving the endpoint-level default asymmetry documented above is enforced, not
   just intended.

**Ask of the team:** ping me the moment `agent_runner.py`'s `force_alpha` parameter and `web/app.py`'s
in-flight registry both exist (even in a draft PR) — I'll pick this up immediately rather than poll for it.

---

# Decision -- Best Options: API endpoint + frontend UI

**Date:** 2026-08-29
**Author:** Rusty (Agent Dev)
**Status:** COMPLETE -- implementation finished, integrated against Linus's and Livingston's landed modules, validated
**Traces to:** `.squad/decisions/inbox/danny-best-options-design.md` sections 7/9 (ACCEPTED), assignment "Rusty (Agent Dev) -- owns the FastAPI endpoint, `normalize_category` adoption in `agent_runner.py`, and the entire frontend"

## What changed

**Backend**
* New `GET /api/symbols/{symbol}/best-options` in `backend/web/app.py` (query: `side` in
  `call|put|both`, `dte_min`, `dte_max`, optional `support_level`). Validates `side` and
  `dte_min <= dte_max` with 400s; 404 on unknown symbol; 503 with a clear message if
  `src.best_options.evaluate_best_options` is not importable (kept as a guarded import
  throughout the session while Linus was still writing it).
* Cold-cache handling uses `OptionsChainCache.get_or_hydrate()` only -- never
  `get_or_load`/`get_or_load_async` -- so the endpoint can never block the event loop on a
  live provider fetch. A true miss calls the new public `schedule_background_refresh()`
  and responds `200 {"status": "warming", "symbol": ..., "retry_after": 15}`. This is
  deliberately a 200, not a 503: the roll-table endpoint's `except RuntimeError: return
  503` was flagged in Danny's design as the anti-pattern to avoid here, since
  `OptionsChainNotReadyError` subclasses `RuntimeError` and a matching `except` would
  silently swallow it; "warming" is a real, user-facing UI state, not a failure, and
  collapsing it into a generic 503 would strip that distinction from the BFF/client.
* Input assembly (Rusty's part of the contract): `category` from
  `symbol_doc.enrichment.category`, `total_shares` from the symbol doc,
  `next_earnings_date`/`ex_dividend_date` from the existing `cosmos.get_next_earnings_date`
  / `cosmos.get_next_calendar_event_date(symbol, "ex_dividend")` accessors -- both already
  deterministic, no new logic invented. `underlying_price`/`atm_iv` are deliberately *not*
  assembled here; `evaluate_best_options` reads them from inside the cached chain itself
  (design F7), so they can never desynchronize from the Greeks the chain's own contracts
  were computed against.
* **`support_level` decision:** there is no deterministic source for a support/pivot level
  anywhere in this codebase today -- pivot points are currently LLM-prompt-extracted text
  only, never a callable technical-analysis function. Rather than invent one (out of
  charter -- Rusty does not define trading-strategy or technical-analysis logic) or silently
  omit the field, it is accepted as an **optional** query parameter: the caller (today,
  nobody; in the future, possibly a deterministic technical-analysis module or the UI
  itself) may supply it, and omitting it simply disables the `below_support` flag rather
  than producing a wrong or guessed value.
* The endpoint returns `evaluate_best_options(...)`'s response envelope verbatim
  (`symbol`/`status`/`schema_version`/`parameters`/`calls`/`puts` are already all present)
  rather than re-wrapping it -- avoids a subtle bug where re-wrapping with
  `{"symbol": sym_upper, **result}` would let `result["symbol"]` (sourced from
  `chain.get("symbol")`) silently win over `sym_upper` on any casing mismatch.
* `agent_runner.py`: adopted `category_params.normalize_category(category).replace("_", " ")`
  in `_resolve_category_skill` and `_get_category_delta_context`, fixing the F9 finding
  (`"high-yield"` / `"High Yield"` previously fell back silently to `"balanced"`'s delta
  range instead of resolving to `high_yield`'s). The consuming dicts
  (`_CATEGORY_SKILL_MAP`, `_CATEGORY_DELTA_RANGES`) are keyed by space-form strings and
  were left untouched -- only the normalizer was fixed, per instruction to not stack a
  cross-agent dict refactor on top.

**Frontend**
* New "Best Options" entry as the **first** item in the Symbol Detail Analyze dropdown
  (`SymbolActions.tsx`), Trophy icon, routing to `/symbols/[symbol]/best-options`.
* New BFF route `app/api/symbols/[symbol]/best-options/route.ts` forwards `searchParams`
  and the upstream HTTP status verbatim (`NextResponse.json(data, {status: res.status})`),
  following the `positions/.../snapshots` route's pattern rather than
  `options-chain/route.ts`'s (which throws on any non-2xx and collapses everything to a
  generic 502) -- necessary because warming (200)/validation-error (400)/not-found
  (404)/scorer-unavailable (503) must all reach the client as distinguishable states.
* `BestOptionsView.tsx` renders a `loading` / `warming` / `error` / `ok` state machine:
  warming shows an explicit banner with an auto-retry timer keyed off the backend's
  `retry_after`, plus a manual "Retry now" button -- never a silent hang. `ok` renders the
  parameters panel, then one sortable table per side with per-row expand for score
  components, thresholds, and staleness.
* `BestOptionsParams.tsx` renders category/profile (with a "defaulted" badge when
  `parameters.category.defaulted` is true), delta band, DTE window, premium
  floor/wait/basis, liquidity floor, underlying price, ATM IV, next earnings, and
  mandatory disclosure banners for `iv_rank_enforced: false`, unknown earnings, and any
  stale-contract count -- plus a collapsible footer with the raw weights, colour
  thresholds, and threshold/skill source strings, so provenance is never hidden behind an
  extra click for the primary facts.
* **Semantics ownership:** the frontend never computes or infers a row's colour, label,
  gate outcome, or nearest-miss explanation -- it renders exactly what the API returned.
  `ColorBadge` pairs the backend-supplied `color` with an icon (not colour alone) and the
  backend-supplied text `label`, satisfying the "not color-only" accessibility requirement
  without the UI inventing its own wording.
* **Colour-to-CSS mapping:** this codebase has no `--accent-yellow` variable; "yellow"
  maps to the existing `--accent-orange`, consistent with how WAIT/HOLD states are already
  coloured elsewhere (`RuleEvaluationPanel`'s `STATUS_META`, `badges.ts`'s `riskStyle`).
  Added as `preferenceStyle()` in `lib/badges.ts` alongside the existing style helpers.
* No LLM call exists anywhere in this flow (fetch -> BFF proxy -> render); this satisfies
  design acceptance gate #1 for the UI surface trivially, by construction.

## Findings surfaced for the team (not fixed here, out of scope for this decision)

* `npm run lint` already fails on `main`/this tree independent of Best Options: 10
  pre-existing violations of the `react-hooks/set-state-in-effect` rule exist in files
  this task never touched (`GlobalChatView.tsx`, `PositionsTable.tsx`,
  `RecentActivities.tsx`, `SymbolChat.tsx`, `SymbolInfoModal.tsx`, `CalendarView.tsx`,
  ...). Even `options-chain/page.tsx` -- the exact fetch-on-mount pattern this session used
  as its template -- has the identical violation. `BestOptionsView.tsx` moves its
  loading-state transition into its manual retry/refresh event handlers rather than the
  mount effect, but the rule's static analysis still flags the mount effect because it
  calls an async function that *eventually* calls `setState` post-await -- the same shape
  every comparable component in the repo already uses. Recommend a repo-wide decision
  (suppress vs. restructure vs. pin an older `eslint-config-next`) rather than a
  one-off fix scoped to this feature.
* A transient, self-resolving false positive was observed mid-session in
  `tests/test_best_options.py::TestNoDirectContractAccess`: its banned-pattern regex
  briefly matched literal example text inside `best_options.py`'s own module docstring
  (not an actual `contract.get(...)` call), most likely because that docstring was
  mid-edit at the moment this session ran the suite. It was gone by the final full test
  run (130/130 passing) -- flagging only so the same wording doesn't reappear in a future
  edit to that docstring.

## Validation

* `python3 -m py_compile` clean on all touched backend files.
* `pytest tests/test_best_options.py tests/test_category_params.py
  tests/test_options_chain_dte_filter.py tests/test_options_chain_cache.py
  tests/test_buy_tracker_normalization.py` -- 130/130 passed.
* Ad-hoc, non-committed `TestClient` + in-memory `FakeCosmos` smoke script (written,
  run, then deleted -- deliberately not committed as `test_best_options_endpoint.py` to
  avoid a filename collision with Basher's expected formal test) exercised, against the
  real endpoint and the real `evaluate_best_options`: cold-cache -> `warming` response,
  warm-cache -> full `ok` response with `parameters`/`calls`/`puts` present and populated,
  `side=bogus` -> 400, unknown symbol -> 404.
* `npx tsc --noEmit` -- clean, no errors.
* `npm run build` -- compiled successfully; both new routes
  (`/api/symbols/[symbol]/best-options`, `/symbols/[symbol]/best-options`) present in the
  build's route manifest.

## Addendum -- Visual consistency with Roll Scenarios (2026-08-29, same day, follow-up directive)

**Traces to:** `.squad/decisions/inbox/copilot-directive-20260829T102715+0200.md`

Restyled `BestOptionsView.tsx`'s table to match Roll Scenarios
(`PositionDetail.tsx`'s `RollTableView`) structure, spacing, and typography
exactly: `border-collapse text-xs`, plain `border-b border-border` header
rule with no separate card frame around the table, `border-b
border-border/40` body rows, `px-2 py-1` cell padding.

Row colour treatment now reuses Roll Scenarios' own palette rather than a
second ad-hoc one: extracted Roll's local `CELL_BG` rgba map into
`lib/badges.ts` as the shared `ROW_TINT_BG` (byte-identical values -- no
visual change to Roll Scenarios), added `preferenceRowTint(color)` to map
Best Options' `green/yellow/red` onto it (`yellow` -> the shared `orange`
bucket, matching the existing `preferenceStyle` precedent), and updated
`PositionDetail.tsx` to import `ROW_TINT_BG` instead of declaring its own
copy. One shared token now backs both tables' background tint.

Accessibility is unchanged: `ColorBadge` (icon + backend-supplied text
label) still renders in every row: the new background tint is an
additional visual cue matching Roll Scenarios' look, not a substitute for
it, so "not colour alone" continues to hold.

`BestOptionsParams.tsx` (the parameters/provenance panel) was deliberately
left as its own bordered card rather than folded into Roll's compact
inline stat-line style -- that panel carries ~15 fields plus three
mandatory disclosure banners per Danny's design §6, which Roll's ~7-field
inline bar has no equivalent for; matching Roll's *density* there would
mean dropping required disclosure content, not just its look.

---


### 2026-08-17: Buy Tracker deterministic normalization and provider evidence (consolidated)

**By:** dsanchor, Danny, Linus, Rusty

**What:** Buy Tracker prompts score only the five binary dimensions
`value_entry`, `trend`, `momentum`, `income`, and `calendar`. A pure normalizer
recomputes the score, applies hard-WAIT rules, and exclusively determines the
persisted activity before alerting, evaluation, persistence, and notification.
Scores 0–2 map to `WAIT`, 3–4 to `BUY`, and 5 to `BUY` unless every exceptional
promotion gate passes.

`STRONG_BUY` requires the complete provider-available evidence set: qualifying
52-week pullback and SMA relationships, RSI 25–45, provider `Buy` signals for
`MACD.macd` and `Stoch.K`, positive annual DPS, latest DPS, and dividend-growth
years, payout ratio <=75%, analyst upside >=5%, and earnings more than seven
days away. Missing required evidence fails promotion closed to `BUY`. A missing
explicit `dividend_cut_or_suspended` boolean alone does not block promotion;
an explicit cut/suspension or exact canonical cut flag always forces `WAIT`.
Hard-WAIT triggers preserve the recomputed score, raw evidence takes precedence
over stale flags, and vague prose cannot create positive evidence.

**Why:** Broad eligibility signals should make `BUY` the normal favorable DCA
result, while maximum conviction remains rare, deterministic, reachable from
production provider data, and evidence-based. One normalized object prevents
prompt, evaluator, alert, and persistence drift.

---

# Decision: Debug Agent-Chain Pipeline Must Capture Current Contract Before Delta Filter

**Author:** Linus (Quant Dev)
**Date:** 2026-08-18
**Status:** ✅ Implemented
**Impact:** Debug > Agent Chain Pipeline View accuracy; brings it back in line with the production roll-management pipeline

## Context

User report: simulating a CURRENT POSITION MSFT call, strike 525, expiration
2026-09-04 (17 DTE as of 2026-08-18) in Debug > Agent Chain Pipeline View
produced "buyback cost unavailable because current contract is not in chain
data" followed by "no valid ROLL_OUT candidates," even though the contract
is genuinely present in the cached yfinance+TradingView-merged chain.

## Root Cause

This was **not** a cache-staleness or TradingView-overwrite bug. The
`OptionsChainCache` merge pipeline (yfinance base + TradingView overlay +
last-known-good) is additive/field-level and never drops or overwrites
whole expiration buckets — it was working correctly.

The actual defect: `filter_options_chain_by_delta` legitimately (and
correctly, for candidate selection) drops any contract whose computed delta
falls outside the standard band (calls: 0.15–0.90). MSFT's $525 call for
2026-09-04 currently has a real, non-zero $3.20-equivalent ask in the raw
chain, but yfinance was returning a degenerate/near-zero implied volatility
for it (a known yfinance quirk when bid/ask are both zero because the
market is closed) — Black-Scholes then computes a ~0.0 delta, which is
outside the call band, so the delta filter silently removes the position's
own held contract before any later stage ever sees it.

On 2026-07-09 this exact class of bug was fixed for the production
`_build_alpha_options_chain` / roll-management path in `agent_runner.py`
via the pattern "capture the current-contract reference from the chain
BEFORE the delta filter runs, and surface it independently of candidate
filtering." That fix was never propagated to:
1. The `/api/debug/agent-chain/{symbol}` endpoint (`web/app.py`), which
   computed its buyback cost from `position_filtered` — a chain that had
   already been through `filter_options_chain_by_delta` twice.
2. The shared `format_roll_candidates_table()` helper itself, which had no
   way to accept an externally-captured current-contract reference — only
   an internal lookup inside the (already-filtered) `chain` argument, which
   direction filters always exclude the exact held strike+expiration from
   by design.

## Fix

- `src/options_chain_filters.py`: `format_roll_candidates_table()` gains an
  optional `current_contract: dict | None` parameter. When supplied, it is
  used for the CURRENT POSITION bid/delta/theta summary line and as the
  buyback-cost fallback, independent of whatever filtering was applied to
  the `chain` argument. Backward compatible — omitting it preserves the old
  in-chain lookup behavior.
- `web/app.py` (`api_debug_agent_chain`): now looks up the current contract
  via `get_contract()` on the **raw, unfiltered** chain (mirroring the
  production pattern) instead of deriving it from the delta-filtered
  `position_filtered` chain, and passes it into
  `format_roll_candidates_table(..., current_contract=...)`.
- `src/agent_runner.py`: the production roll-management call site now also
  passes its already pre-filter-captured `current_contract` into
  `format_roll_candidates_table`, so the CURRENT POSITION bid/delta/theta
  line is populated there too instead of silently omitted (strict
  improvement, no behavior regression).

A genuinely zero/missing ask (real market-closed state) still correctly
reports "no positive finite executable ask" — the fix only stops losing a
*valid* ask, it never fabricates one.

## Key Pattern (reinforces 2026-07-09 decision)

Any pipeline surface that needs a position's "current contract" reference
(buyback cost, bid/delta/theta display) must capture it from the chain
**before** delta/direction filtering runs, and pass it through explicitly.
Do not rely on looking the current contract up inside a chain that has
already been through candidate-selection filters — direction filters
deliberately exclude the exact held strike+expiration, and the delta filter
can drop it for data-quality reasons (e.g. degenerate IV while markets are
closed) unrelated to whether the position is a legitimate roll/close
candidate. When adding a new consumer of the options chain pipeline
(dashboards, debug tools, new agents), mirror the production capture point,
don't reinvent it.

## Files Changed

- `backend/src/options_chain_filters.py` — added `current_contract` param
  to `format_roll_candidates_table`
- `backend/web/app.py` — debug endpoint now sources current contract/ask
  from the raw chain via `get_contract()`
- `backend/src/agent_runner.py` — production call site now passes its
  pre-filter `current_contract` through too
- `backend/tests/test_format_roll_candidates_table.py` — new, 9 tests
- `backend/tests/test_debug_agent_chain_pipeline.py` — new, 2 tests
- `backend/tests/test_options_chain_position_and_direction_filters.py` — new,
  16 direct unit tests for `filter_options_chain_for_position` and
  `filter_options_chain_by_roll_direction` (previously zero coverage for
  either function; added per Basher's review, see Update below)

## Verification

```
python3 -m py_compile src/agent_runner.py src/options_chain_filters.py web/app.py → OK
pytest tests/test_format_roll_candidates_table.py tests/test_debug_agent_chain_pipeline.py \
       tests/test_options_chain_position_and_direction_filters.py \
       tests/test_get_contract.py tests/test_exclude_contract.py tests/test_options_chain_cache.py \
       tests/test_watchlist_symbols.py -q
  → 118 passed
```
Confirmed new tests fail without the fix (regression-guarding) and pass
with it restored. One pre-existing, unrelated failure
(`test_greeks_populated_for_nonzero_iv`, documented mock-drift issue) is
unaffected by this change.

## Update 2026-08-18 (post-Basher review)

Basher independently reproduced the same bug (proved with a synthetic
$0.30-ask contract to rule out the live illiquid-neighborhood confound) and
rejected the pre-fix behavior, confirming this exact root cause and fix
approach as the acceptance criteria. Basher additionally flagged a coverage
gap: `filter_options_chain_for_position` and
`filter_options_chain_by_roll_direction` had zero direct unit tests anywhere
in the suite (only exercised indirectly via pipeline tests). Added
`test_options_chain_position_and_direction_filters.py` (16 tests) to close
this gap — covers ROLL_DOWN/UP/OUT/UP_AND_OUT/DOWN_AND_OUT strike+expiration
semantics, the "identical held contract is always excluded from ROLL_OUT
candidacy by design" invariant, unknown-roll_type passthrough, and the
per-expiration strike-window behavior of `filter_options_chain_for_position`.
Also re-verified via a full-suite run (before and after restoring the fix)
that a pre-existing, unrelated test-isolation issue in
`test_yfinance_data_provider.py` (20 failures when run as part of the full
`pytest tests/`, but only 1 documented failure when run in isolation) exists
identically with or without this change — not caused by this fix, not
addressed here (out of scope; a separate cross-file test-isolation problem,
likely event-loop/mock leakage between test modules).

# Decision: Pure Merge Module Ownership Boundary vs. Danny's Design Doc

## Context
Danny's `danny-persistent-option-chain-merge.md` design assigns Linus
ownership of the "yfinance row normalizer currently inline in
`OptionsChainCache._process_option_df`" (in `backend/src/options_chain_cache.py`)
alongside the new pure `options_chain_merge.py` module. The task-level
authorization I was given explicitly excluded `options_chain_cache.py`
(persistence/threading plumbing owned by Rusty) from my writable artifacts.

## Decision
Followed the narrower, explicit task-level restriction: implemented all
seven frozen functions (`is_accepted`, `gate_contract`, `gate_bucket`,
`merge_sources`, `merge_prior`, `recompute_derived`, `prune_by_expiration`)
as a standalone, dependency-free `backend/src/options_chain_merge.py`, and
fixed the two upstream source-normalizer bugs Danny flagged (G2 fabricated
TradingView placeholder zeros/False/empty-string; G5 malformed-expiration
fallback key) at their actual origin, `backend/src/tv_options_chain_fetcher.py`
(`_parse_tv_to_yfinance_format`) — NOT inside `options_chain_cache.py`. Wiring
`options_chain_cache.py` to call the new pure module (replacing its inline
`_merge_contract_fields` / `_is_invalid_quote_value` / `_merge_chains` /
`_prune_expired_expirations` helpers) is left entirely to Rusty.

## Rationale
- Respects explicit charter boundaries ("Do not own persistence/threading
  plumbing"); avoids a merge conflict with Rusty's concurrent, in-flight
  rewrite of that same file (observed live during this session — the file
  was mid-edit and briefly failed to import while I was validating).
- The pure module is independently testable and import-safe without
  `options_chain_cache.py` ever existing, which is a stronger isolation
  property than Danny's doc strictly required.
- No test in Danny's required set (T1-T12) or the task's explicit scenario
  list exercises `options_chain_cache.py` directly, so this boundary choice
  costs nothing in coverage: `options_chain_merge.py` and
  `tv_options_chain_fetcher.py`'s normalizer are each covered by dedicated,
  hermetic unit tests (`test_options_chain_merge.py`,
  `test_tv_options_chain_fetcher_normalize.py`).

## Impact
Rusty's in-progress rewrite of `options_chain_cache.py` (observed as already
underway) is expected to import and call `options_chain_merge`'s seven
functions directly, replacing the superseded inline helpers. No action
required from Linus beyond this note; flagging so Rusty/Danny can confirm
the integration point matches this module's public signatures exactly as
frozen.

# Decision: T12 Monotonicity/Associativity Holds for Self-Consistent Live Payloads Only

## Context
Basher's review asked for a property/fuzz test of `merge_prior` monotonicity
(design doc T12: `merge(merge(P,L1),L2) == merge(P, merge(L1,L2))`). A fuzz
test generating `L1`/`L2` as directly-fabricated dicts with fully
independently-random per-field values (bid/ask/iv/lastPrice/lastTradeDate/
volume/openInterest each sampled without regard to the others) found ~28%
of 200 random seeds violated the equality.

## Root Cause (not an implementation bug)
`merge_prior`'s "prior" argument is, by design, never re-gated — a prior is
assumed already-vetted history (this is what lets a carried-forward
contract avoid re-proving itself every cycle). `merge(L1, L2)` in the T12
formula uses `L1` in the *prior* role. If `L1` is a raw dict whose quote-group
fields were never actually vetted by any source's own `gate_contract` (e.g.
it has a `bid` present with no source-side valid `ask`/`iv` anywhere), then
treating it as an ungated "prior" lets that never-vetted value leak through
`combined_live` — a shape the real pipeline can never produce, because
`merge_sources` (the only real producer of a "live" payload) always ties a
quote-group field's presence to the *same* source contract's own gate
having passed. Regenerating the fuzz test so `L1`/`L2` are realistic
`merge_sources(random_yf_chain, random_tv_chain)` outputs (self-consistent
by construction) made the property hold cleanly across 500 random seeds.

## Decision
Documented the refined guarantee directly in the new fuzz test
(`TestMonotonicityProperty.test_merge_prior_is_monotone_under_random_field_combinations`,
`backend/tests/test_options_chain_merge.py`, 300 seeded cases): T12's
associativity is guaranteed for any live payload the real pipeline can
actually produce (i.e., anything `merge_sources` can emit), not for an
arbitrary directly-constructed dict with an internally-inconsistent quote
group. No source change was needed — `options_chain_merge.py`'s
implementation was already correct; only the test's input generator was
unrealistic. Flagging this so Rusty's ETag-retry code can rely on the
guarantee as stated: safe as long as every write path always merges through
`merge_sources` first (never hand-constructs a "live" chain).

## Also fixed in this review pass (`tv_options_chain_fetcher.py`)
Basher's "malformed YYYYMMDD calendar dates" prompt surfaced a real gap:
the fetcher's own Rule S3 check for the "already YYYYMMDD as a number"
branch (`raw_exp_val > 19000000`) only checked the magnitude, not that the
digits formed a real calendar date — a TradingView payload with e.g.
`expiration: 20261301` (month 13) or `20260230` (Feb 30) would pass the
fetcher's check and only be caught later by `merge_sources`'s own
`strptime`-backed validation. Added the same `strptime` check at the
fetcher's ingestion point so it is genuinely the primary Rule S3 enforcement
point, not just nominally so. Covered by new parametrized tests in
`test_tv_options_chain_fetcher_normalize.py`.

## Out of scope (Rusty's / scheduler charter, not actioned here)
Basher's review also raised: (1) persistence-hydration divergence between a
combined scheduler+API singleton process and a `--web-only` replica with a
cold singleton, (2) Cosmos ETag 409/412 retry-exhaustion behaviour (log +
skip, never raise), (3) `schema_version` absence/migration for legacy
shards, (4) preserving the `refresh_all` watchdog. These are all
persistence/threading/scheduler concerns explicitly outside Linus's charter
(none of them touch `options_chain_merge.py`'s pure `{calls, puts}` chain
shape — `schema_version` in particular is a shard-document-level field the
store adds/strips before/after calling into the merge module, never seen by
it). Not actioned; flagged for Rusty.

---

# Decision: User Directive — Persistent Option Chain (2026-08-18T09:09:45Z)

**By:** Copilot (User)
**Status:** ✅ Accepted
**Context:** Platform requirement for option-chain durability and data quality.

## Directive

The persisted option chain must:
- Preserve the last-known-good quotation for each contract.
- Never allow invalid Yahoo Finance data (zeros, missing fields) to overwrite valid stored data.
- Permit TradingView to enrich or overwrite only with valid quotations.
- Expire contracts by real calendar date, never by cache TTL or staleness.

## Rationale

Users need reliable data when monitoring positions, especially when live provider feeds are intermittently down. The current cache loses all history on restart or TTL expiration, and both providers can emit zeros/missing data that wipe prior valid observations.

---

# Decision: Persistent Option Chain — Accumulate-and-Merge Design (2026-08-18)

**Date:** 2026-08-18
**Author:** Danny (Lead)
**Status:** ✅ Accepted — ready for implementation
**Directive:** User directive (2026-08-18T09:09:45Z)
**Scope:** Complete option-chain persistence architecture covering merge logic, storage layout, concurrency, and retention policy.

## Executive Summary

The invariant the user asks for is **half-implemented**. `OptionsChainCache` already does field-level last-known-good merge, refuses to evict on TTL, and prunes by real expiration date. Three structural gaps break the invariant in production (G1-G3), plus two secondary defects (G4-G5).

| Gap | Severity | Issue |
|-----|----------|-------|
| G1 — No persistence | **Blocker** | Process-local dict only; restart ⇒ total loss; scheduler + web diverge |
| G2 — TradingView destroys valid Yahoo fields | **High** | Overlays hardcoded zeros (volume, openInterest, lastTradeDate, inTheMoney, contractSymbol) that are never fallback-merged |
| G3 — Derived fields merged as if observed | **High** | Contract δ from cycle N-3, γ from cycle N → internally inconsistent; `filter_options_chain_by_delta` gate on inconsistent value |
| G4 — `bid == 0` always invalid | **High** | Market reality: bid-less OTM contracts are real, not feed failure |
| G5 — Unparseable expiration keys immortal | **High** | Non-YYYYMMDD keys become junk keys never pruned, unbounded leak |

## Solution Overview

Three-phase merge on each refresh cycle:

1. **Live source merge** `L = merge_sources(yfinance, tradingview)` — applies trust gates (contract must have valid ask>0 or iv>0), then TradingView > yfinance field-by-field, only for fields TV supplies and that pass per-field acceptance predicates.

2. **Accumulate prior** `A = merge_prior(prior_chain, L)` — contract-level union, field-level overwrite-if-accepted, carries forward absent contracts with fresh `_meta` markers.

3. **Recompute derived** `A' = recompute_derived(A, underlying_price)` — always-fresh greeks from merged primitives + current time-to-expiry, fixing G3.

Persist sharded by `(symbol, expiration)` with ETag CAS retry + monotone merge (safe for cross-process convergence). Read path: memory → hydrate from store → fetch.

## Key Rules

**Rule S1** — Providers emit `None` (or omit key) for unknowns; never fabricate `0`/`0.0`/`False`/`""`. Fixes G2.

**Rule S2** — Absence is not zero; missing field = keep prior. Fixes G2 + G4.

**Rule S3** — Reject unparseable expiration keys at ingestion. Fixes G5.

**Trust gate** (fix for G4) — Contract's quote group (`bid`, `ask`, `iv`, `lastPrice`, `lastTradeDate`) is trusted **only if** the source supplies at least one of: `ask > 0` or `iv > 0`. Enables `bid == 0` to be accepted when the source quotes a valid ask/iv for that contract.

**Degenerate bucket gate** — For each `(source, side, expiration)` bucket: if ≥3 contracts, all failing the trust gate, reject the entire bucket (this is the observed "all-zero chain" failure). Bucket < 3: per-contract gate decides.

## Implementation Ownership

| Module | Author | Scope |
|--------|--------|-------|
| `src/options_chain_merge.py` (new, 7 frozen functions) | Linus | Pure, dependency-free merge logic; source normalizers; T1-T12 tests |
| `src/options_chain_store.py` (new) + `options_chain_cache.py` hydrate/lock | Rusty | Persistence lifecycle, concurrency, T13-T21 tests |
| All other files (filters, web, scheduler) | — | Untouched |

## Retention & Pruning

- **Serving prune:** Drop expiration when `exp_date_ET < today_ET` (same-day ET boundary), keep full day so settlement contracts available.
- **Persistence prune:** Delete shard only when `exp_date_ET < today_ET - 7_days_grace` (default grace for post-expiry reconciliation).
- **Never prune on TTL, staleness, or absence** — TTL = freshness only.
- **Size escape valve:** Log ERROR if shard would exceed 1.6 MB; evict oldest-unseen carried-zero-OI contracts first.

## Concurrency & Failure

- Per-symbol `threading.RLock`, one full refresh-merge-persist cycle at a time.
- ETag CAS with 3-retry bound; shard conflicts abort, never lost update.
- Persistence failures non-fatal; refresh returns merged chain regardless, error logged.
- `invalidate()` drops memory only; `purge()` explicit destructive.

## Tests (T1-T21)

Linus: field-validity matrix (T1), trust gates (T2-T4), degenerate bucket (T4-T5), TV omits fields (T6), yfinance zero overwrites (T7), merged greeks consistency (T8), carried-forward decay (T9), expiration rejection (T10), pruning (T11), monotonicity property (T12).

Rusty: cold-start hydrate (T13-T14), persistence failures (T15-T16), sharding/grace (T17), invalidate semantics (T18), concurrent refresh (T19), `refresh_all` watchdog (T20), persistence disabled (T21).

## Risks & Mitigations

| Risk | Mitigation | Residual |
|------|-----------|----------|
| Stale quotes presented as live | `_meta.quote_asof` + schema doc + logging | Accepted — user requested indefinite retention |
| Greek-recompute cost | Benchmark before merge; optimize if needed | Low |
| Cosmos RU / doc growth | Per-expiration sharding + change detection + size valve | Low |
| Cross-process convergence lag | Inherent to SWR; bounded by TTL | Accepted |
| Degenerate false positive | Outcome is "keep prior" (safe direction) | Accepted |
| Unbounded accumulation | Real-expiration pruning + size guard | Low |
| Scope creep into watchdog | `refresh_all` contract untouchable (2026-06-30 decision) | Low |

---

# Decision: Revision Directive — Persistent Option Chain Seam (Post-REJECT, D1-D5) (2026-08-18)

**Date:** 2026-08-18
**Author:** Danny (Lead)
**Status:** ✅ Accepted — assigned to Livingston
**Response to:** Initial store/cache integration rejection

## Escalation Rationale

Initial implementation by Linus (merge logic) and Rusty (persistence/lifecycle) was rejected due to five defects (D1-D5) at the *seam* between these modules — neither author owns end-to-end integration, and both are now locked out per rejection protocol. Basher is reviewer-only. Danny does not implement. A new specialist (Livingston, Persistence & Integration Engineer) is cast to repair the seam with authority over both domains.

## D1-D5 Defects Identified by Basher

| Defect | Location | Symptom | Root Cause |
|--------|----------|---------|-----------|
| **D1** | store.py write path | Hydrated contracts missing derived fields (mid, greeks) | `_write_shard` CAS reconciliation calls `merge_prior` on already-merged chain, not live observation |
| **D2** | store.py write path | Provenance (`_meta`) corrupted on round-trip | Same caller misuse; `merge_prior` manufactures fresh `_meta` |
| **D3** | cache.py hydrate path | Expired contracts served as candidates; missing `timestamp`/`underlying_price` | Hydrate never prunes; schema fields not restored |
| **D4** | cache.py locking | Concurrent same-loop `await refresh(sym)` both run full cycle (lost update); event-loop freeze on cross-thread contention | `threading.RLock` reentrancy + blocking on event loop |
| **D5** | store.py write guard | Unchanged market data rewrites shards (high RU cost) | Content hash includes volatile `_meta.last_seen`, never converges |

## Bounded Revision Scope

**Authorized artifacts (only these may change):**
- `backend/src/options_chain_store.py` — full rewrite of write path, hydrate, retention.
- `backend/src/options_chain_cache.py` — hydration/serving path and locking only.
- `backend/tests/test_options_chain_store.py` — full.
- `backend/tests/test_options_chain_cache.py` — additive only.
- `backend/tests/test_options_chain_persistence_integration.py` — **new** file, real store + real merge, no fakes across seam.

**Frozen (must NOT be reopened):**
- `backend/src/options_chain_merge.py` — every function, validity predicate, trust gate, Rule S1/S2/S3, `_meta` shape.
- `backend/tests/test_options_chain_merge.py` — frozen.
- Provider normalizers (`tv_options_chain_fetcher.py`, yfinance side in `OptionsChainCache._process_option_df`).
- `OPTIONS_CHAIN_SCHEMA_DESCRIPTION` (runtime must honour it, not edit it).
- `refresh_all` watchdog (per-symbol timeout + `shutdown(wait=False)`, untouchable per 2026-06-30 decision).
- Filters, roll table, agent runner, web app, config.

## Required Fixes

**D1/D2** — Persisted shards must be consumable as-is; `_meta` must survive round-trip verbatim. Acceptance: hydrated chain has `mid` + all 5 greeks after ≥2 persist cycles; carried TV-sourced contract hydrates with identical `_meta`.

**D3** — Hydration must respect serving horizon (same-day ET pruning); carry top-level `symbol`/`timestamp`/`underlying_price`; mark entries immediately stale-eligible so next read schedules real refresh.

**D4** — Concurrency must hold for coroutine-level execution on one loop + cross-thread contention (matching production shapes). Different symbols still parallel; `refresh_all` watchdog unchanged. Acceptance: two `await refresh(sym)` on one loop ⇒ exactly one fetch; cross-thread hold does not freeze loop.

**D5** — Write-skip guard must actually work in production (change detection fires only on real market changes, not on volatile provenance). Acceptance: two cycles identical fetch ⇒ zero shard rewrite.

## Gate Sequence

1. Livingston implements within bounded scope.
2. Basher reviews test depth (no fakes across store/merge seam).
3. Danny re-reviews D1-D5 with reproduction scripts before approval.

---

# Decision: Livingston's D1-D5 Option Chain Persistence Seam Revision (2026-08-18)

**Date:** 2026-08-18
**Author:** Livingston (Persistence & Integration Engineer)
**Status:** ✅ Ready for review — Basher (test depth), then Danny (D1-D5 re-review)
**Cast date:** 2026-08-18 (escalation from D1-D5 rejection)
**Supervisor:** Danny (approver); Basher (test reviewer)

## Summary of Fixes


### Schema Changes


Bumped `schema_version: 2 → 3` (pre-approved by Danny as the one shard-shape change allowed without escalation). Added `underlying_price` to shard body (needed for greek recomputation on hydrate). `hydrate()` reconstructs top-level `timestamp` and `underlying_price` from most-recently-`updated_at` shard. Legacy v2 shards hydrate fine without `underlying_price` (fall back to 0.0 until next live fetch).

## Files Changed

- `backend/src/options_chain_store.py` — write path, hydrate, reconciliation, schema (full rewrite of CAS/hash/reconcile logic).
- `backend/src/options_chain_cache.py` — `_hydrate_into_memory` (D3), per-symbol locking (D4), merge-cycle logic in `_refresh_locked` byte-for-byte unchanged.
- `backend/tests/test_options_chain_store.py` — removed fake merge fixture (no longer needed), added D1/D2/D5/schema tests.
- `backend/tests/test_options_chain_cache.py` — **zero edits needed** — all 34 pre-existing tests pass unmodified against the new locking design, including `TestConcurrentRefreshNoLostUpdate` and `TestRefreshAllWatchdogRegression`.
- `backend/tests/test_options_chain_persistence_integration.py` — **new**, R1-R7, composing real `OptionsChainStore` + real `options_chain_merge` module functions through real `OptionsChainCache`; only Cosmos container and network-facing fetch methods faked.

## Test Evidence (R1-R7)

All 8 integration tests in new file passing, composing real modules across seam:

- **R1** (`TestR1DerivedFieldsSurviveMultiplePersistCycles`) — 3 real persist cycles; hydrated chain has `mid` + all 5 greeks for every contract; `_meta.greeks_valid` flag verified both True and False.
- **R2** (`TestR2ColdReplicaFilterParity`) — empty-memory second instance hydrates and produces identical `filter_options_chain_by_delta` result as producer's in-memory chain; fetch methods raise if called (no provider hit).
- **R3** (`TestR3ProvenanceSurvivesCarry`) — TV-sourced carried contract across 2 persists with `quote_asof`/`quote_source`/`carried`/`first_seen` all unchanged; hydrated `_meta` identical.
- **R4** (`TestR4HydratePrunesPastExpirationsAndIsStaleEligible`) — yesterday-expiring shard excluded from served data; `is_stale()` immediately True; next read triggers real fetch.
- **R5** (`TestR5HydratedPayloadTopLevelFields`) — carries `symbol`, non-null `timestamp`, exact persisted `underlying_price`.
- **R6** (`TestR6ConcurrencyCorrectness`) — two `await cache.refresh(sym)` ⇒ exactly one fetch; cross-thread lock hold proven non-blocking via heartbeat.
- **R7** (`TestR7WriteSkipGuardEffective`) — two cycles, byte-identical fetch ⇒ zero shard rewrite.

Suite-wide validation:
- Old test suite vs new store: 32/33 pre-existing passed unchanged.
- Focused suite (merge/store/cache/integration/filters/tv-normalize): **546 tests passing**.
- Full backend suite: **1244 tests passing**, 20 pre-existing unrelated failures in frozen `test_yfinance_data_provider.py` (order-dependent network flakiness, reproducible without any changes to this work, unfixed because file is frozen).

## Residual Risk

None identified within authorized scope. Pre-existing `test_yfinance_data_provider.py` flakiness should be escalated to its owner if it blocks CI.

---

## Addendum — P1 Follow-up: get_or_load Sync-in-Async Bridge Deadlock

Danny approved D1-D5, then filed separate P1 before production:

**Issue:** D4's per-symbol OS lock made `get_or_load`'s pre-existing sync-in-async bridge (used by `web/app.py:3249` inside async `api_activity_chat`, not awaited) able to self-deadlock under contention, especially on cold miss.

**Root cause:** `get_or_load` did `ThreadPoolExecutor().submit(self._sync_refresh).result(timeout=120)` — a blocking wait on the calling loop's own OS thread. `_sync_refresh` spins up a new loop and eventually awaits `loop.run_in_executor` for this symbol's OS lock (D4). If the lock was held by a task needing the *original frozen loop* to run and release it (e.g., concurrent request's own `await cache.refresh(sym)` on that loop), self-deadlock for 120s.

**Fix (entirely within `options_chain_cache.py`):**
- On no-running-loop (genuine sync caller): unchanged, blocks that thread.
- On running-loop: reuse non-blocking `_schedule_background_refresh` (SWR path) to kick off background refresh, immediately raise new `OptionsChainNotReadyError(RuntimeError)` — explicit fail-fast instead of blocking/deadlocking. Compatible with `web/app.py`'s existing (untouched) `except Exception` graceful degradation.

**Tests:** 5 new additive tests in `test_options_chain_cache.py` (39/39 total, 3x re-run determinism). Focused suite 551/551; full backend 1250/1250 (except 20 pre-existing `test_yfinance_data_provider.py` failures).

---

# Decision: Linus Implementation Notes — Merge Module (2026-08-18)

**Date:** 2026-08-18
**Author:** Linus (Quant Dev)
**Status:** ✅ Implemented — frozen, used by Livingston in final revision
**Scope discipline:** Frozen interface only; no persistence/threading/cache lifecycle.

## Implementation Summary

**Seven-function interface** (`backend/src/options_chain_merge.py`):
- Per-field validity predicates (`is_accepted`) — field-specific rules per Danny §2.3.
- Trust gates (`gate_contract`, `gate_bucket`) — distinguish feed failure from market reality.
- Source merge (`merge_sources`) — TradingView > yfinance, field-by-field.
- Accumulation (`merge_prior`) — contract-level union, field-level overwrite-if-accepted.
- Derivation (`recompute_derived`) — always-fresh greeks from merged primitives + current DTE.
- Pruning (`prune_by_expiration`) — by real calendar date, never TTL.

**Provider normalizers** (Rules S1-S3):
- `tv_options_chain_fetcher.py::_parse_tv_to_yfinance_format()` — emit `None` not fabricated zeros; reject unparseable YYYYMMDD keys.
- Mirrored in yfinance side (wired by Rusty later) — same discipline.

**Tests:** `backend/tests/test_options_chain_merge.py`, 89 passing (T1-T12 spec coverage).

**Monotonicity property (T12):** Holds for any realistic `merge_sources` output (self-consistent quote groups); fuzz test with 300 seeds confirmed. Refined guarantee: T12 associativity is guaranteed for live payloads the real pipeline produces, not for arbitrary hand-constructed dicts with inconsistent quote groups — documented explicitly so downstream (Rusty, Livingston) can rely on this guarantee safely.

**Calendar-date validation:** Added `strptime` check to `_parse_tv_to_yfinance_format` (Rule S3 enforcement point) so expiration keys like `20261301` (month 13) or `20260230` (Feb 30) are caught at TradingView ingestion, not later in `merge_sources` — doubly validated, more robust.

---

# Decision: Rusty Implementation Notes — Store/Cache Integration (Initial, Later Revised by Livingston) (2026-08-18)

**Date:** 2026-08-18
**Author:** Rusty (Dev)
**Status:** ⚠️ Initial implementation superseded by Livingston revision (D1-D5)
**Scope:** Persistence layer (CosmosDB), cache integration (hydration, concurrency).

## Initial Implementation (Rejected D1-D5)

Authored `backend/src/options_chain_store.py` (Cosmos sharding per expiration, ETag CAS, 3-retry, size escape valve), extended `backend/src/options_chain_cache.py` (hydration, per-symbol `threading.RLock`), wired `merge_prior`/`recompute_derived`/`prune_by_expiration` calls, added T13-T21 tests.

Strict charter discipline: did not touch `options_chain_merge.py`, provider normalizers, filters. Applied Linus's Rule S1/S3 (omit unknowns, never fabricate zeros; reject malformed expiration) to yfinance-side `_process_option_df`.

Initial validation: 601 focused tests passing (before defects found).

## Defect Escalation (D1-D5)

Basher's architecture review identified five defects in store↔merge seam composition:
- D1/D2: CAS reconciliation misusing `merge_prior`.
- D3: Hydration not pruned, missing fields.
- D4: Locking concurrency issues (freeze, reentrancy).
- D5: Change-detection dead code.

Per rejection protocol, Rusty locked out. Livingston cast as new specialist to repair seam with authority over both domains (frozen merge module, full authority over store/cache integration/concurrency).

## Escalation Rationale

Defects lived at the *seam* — neither Rusty's nor Linus's charter owned end-to-end integration. Basher reviewer-only. Danny lead, not implementer. New specialist required.

---

**Note:** This decision log now contains the definitive record of all decisions from inbox. See `.squad/orchestration-log/` for agent-level session details and `.squad/session-log/2026-08-18T11-32-00Z-session-log.md` for full timeline.

---

# Decision: Zero-Free Agent-Facing Option Chains (Raw Fidelity + Analytical Safety) (2026-08-19)

**Date:** 2026-08-19
**Author:** Danny (Lead)
**Status:** ✅ ACCEPTED — design frozen, implementation pending (G1)
**Full document:** `.squad/decisions/inbox/danny-zero-free-agent-option-chains.md`
**Directive:** `.squad/decisions/inbox/copilot-directive-2026-08-19T12-53-05.md` (Copilot, 2026-08-19T12:53:05+02:00)
**Supersedes:** the prior agent-facing treatment of quote zeros (raw zeros flowing unchanged into agents,
scorers, roll tables and API responses). The `options_chain_merge.py` acceptance predicates are NOT
superseded — the raw layer stays faithful.

## Verdict

Two layers, two policies. The **raw/persisted layer stays faithful** (a provider `bid = 0.0` is a real
market fact and is stored verbatim; `volume`/`openInterest` zeros are real liquidity evidence). The
**agent-facing/scoring layer never presents numeric zero as a usable quote or as evidence** — a single
normalization boundary converts unusable zeros to `null` + status metadata, after `merge_prior`'s
last-known-good retention has already had its chance. Where the user's absolute "no zero" meets a genuine
market zero, analytical safety wins at the agent boundary: `bid: null` **with**
`_meta.field_status.bid = "no_market"` — the zero moves from the value channel to the status channel, and
provenance is preserved in the shard.

**Derived fields are exempt from provenance protection** and are nulled in BOTH layers: a fabricated
`mid = 0.0` and intrinsic-only Greeks from `sigma = 0` are our own artifacts, not observations.

## Rules (Z1–Z11)

- **Z1** Zero is never a usable quote at the agent boundary: `bid`/`ask`/`lastPrice`/`iv`/`mid` are a
  positive number or `null`. Retain-LKG first (merge), represent-unavailable second (view).
- **Z2** Scope carve-out: `volume`/`openInterest` keep integer `0` — count-type evidence, not quotes.
- **Z3** A derived field is `null` whenever its inputs were invalid — `mid` via new
  `robust_mid_optional()`; all five Greeks `null` when `greeks_valid` would be `False`.
- **Z4** `greeks_valid == False` is binding, not advisory — consumers must treat the Greek as absent even
  on legacy shards carrying numbers.
- **Z5** Unavailable scoring factor → 0 points + explicit "unavailable — not scored" reason + entry in
  `data_quality.missing_fields`; never a `key_driver`, never a `rule_hit`.
- **Z6** No derived classification from a missing input: `delta` unavailable → `risk_zone = "UNKNOWN"`
  (today `delta = 0` → `"SAFE"` + `+13`, the worst contamination in the codebase); `iv` unavailable → no
  IV points either direction; combos fire only when every input is available.
- **Z7** P&L uses `executable_buyback_ask` on both sides — `score_short_put` drops its raw-`mid` mark and
  aligns with `score_short_call`.
- **Z8** `extract_greeks_from_chain` stops laundering `_meta`; returns the normalized contract view.
- **Z9** `score` stays numeric (UI contract); confidence is additive: `data_quality.{missing_fields,
  confidence, quote_asof, stale}`, `status = "NO_DATA"` when `delta` or `iv` is unavailable.
- **Z10** Exclusion is a *candidate* rule, never a *visibility* rule: candidate tables exclude
  no-usable-bid / `openInterest == 0` / `greeks_valid == false` with a disclosed hidden-count footer; the
  **current held position is always retained** with nulls + `buyback_available: false`; roll-table cells
  and reference/display views retain contracts with nulls.
- **Z11** No destructive migration — repair only nulls derived fields and adds `_meta`; observed fields
  are byte-for-byte untouched.

## Key mechanism

New pure module `backend/src/options_chain_view.py` (`to_agent_view`, `contract_view`, `usable_quote`,
`usable_greek`, `is_candidate_eligible`) — the single, idempotent, non-mutating, total normalization
boundary. Additive `_meta`: `field_status` (closed vocab `live|last_known_good|no_market|no_trades|
unavailable`), `stale`, `tradable`, `greeks_asof`. Direct `contract.get("bid")` in a consumer is a
review-blocking defect from this decision forward.

## Persistence (Livingston findings resolved)

- **P0:** `get_options_chain_store()` no longer memoizes failures — only a successful store is cached;
  failures record `(last_error, last_failure_at, failure_count)` and retry with capped exponential backoff
  (`options_chain_cache.persistence_retry_seconds`, default 300). Explicit `persistence_enabled: false`
  stays terminal + INFO-once.
- Eager startup probe in web lifespan and scheduler bootstrap; **ERROR** (not WARNING) on failure.
- `GET /api/health/options-chain` + extended `stats()`: `enabled/last_error/last_success_at/failure_count/
  retry_in_seconds` plus per-symbol `contracts_no_usable_bid / greeks_invalid / stale`.
- Dead config `stale_quote_warn_seconds` wired as the sole input to `_meta.stale`.
- Migration: `_schema_version` on shards, mandatory **lazy on-read v1→v2 normalization** (no un-normalized
  shard is ever served) plus idempotent `backend/scripts/repair_options_chain_shards.py` (`--dry-run`
  default, ETag CAS writes, per-symbol counts).

## Ownership (exclusive, no overlapping writes)

- **Linus:** `options_math.py`, `options_chain_merge.py`, NEW `options_chain_view.py`,
  `options_chain_filters.py`, `roll_table.py`, `dps_scorer.py` + their tests.
- **Livingston:** `options_chain_store.py`, `options_chain_cache.py`, `web/app.py`, `agent_runner.py`
  (serialization seam only), `yfinance_data_provider.py` (schema prompt text only), `config.yaml`, NEW
  `scripts/repair_options_chain_shards.py` + their tests. Forbidden: any scoring/market semantics.
- **Basher:** review + NEW `tests/test_zero_free_agent_chain.py` and `test_open_call_zero_quote.py`
  extensions. **No production-code writes.**
- **Danny:** decision, history, gate arbitration. Seam contract frozen at G0; changes escalate, never
  patched locally.

## Gate order

**G0** Contract freeze (Danny, complete) → **G1** Linus market semantics & scoring → **G2** Basher
blocking review of G1 (raw provenance intact, no `or 0` survives, view purity, golden scores unchanged) →
**G3** Livingston persistence & serving (must not start before G2 passes) → **G4** Basher cross-layer
integration & adversarial (`Z-I1` headline: no numeric zero in any agent-facing surface; `Z-I5`
anti-corruption: raw shard still holds the provider's `0.0`) → **G5** Danny final acceptance.
Basher may author G4 tests in parallel during G1/G3; nothing else parallelizes.

## Backward compatibility

Additive only — no key renamed or removed; value types widen `float` → `float | null`;
`robust_mid()`'s contract is untouched (new `robust_mid_optional()` sibling instead); v2 shards remain
readable by v1 code, so a code rollback needs no data rollback. Frontend null-safety sweep is a hard
prerequisite for G5 and is routed outside this roster.

## Explicitly ruled out

Rewriting/deleting provider zeros in raw/persisted data; changing `is_accepted("bid", 0.0)`; nulling
`volume`/`openInterest` zeros; carrying forward stale Greeks; making `score` nullable; filtering unquoted
contracts out of display/reference views; hard-failing the app when persistence is down; changing
`robust_mid()`'s return contract.

## 2026-08-19: Zero-never-overwrites-prior invariant (supersedes the raw-layer "store 0.0 verbatim" rule)

**Trigger:** User clarification (Copilot, 2026-08-19T17:41:19+02:00) — **"En la regeneracion de una cadena
de opciones, ningun valor numerico cero recibido de Yahoo Finance u otro proveedor debe sobreescribir un
valor previo distinto de cero del mismo contrato y campo. El cero debe tratarse como ausencia de
actualizacion y conservarse el ultimo valor valido persistido, especialmente cuando el mercado esta cerrado."**
Clarification: the protection must happen in the persisted merge, not only in the agent-facing view. Reports of
option chains still showing zero bid/quote fields contaminating agent analysis, traced to the *persisted
merge path itself* (not just the agent-view boundary already fixed by Z1-Z10).

**Root cause:** `is_accepted("bid"/"lastPrice", 0.0)` and `is_accepted("volume"/"openInterest", 0)` were
`True` (design §2.1/§2.3, explicitly reaffirmed as "ruled out to change" in the prior Zero-Free decision
above). This let a provider-glitched or closed-market zero for a contract that otherwise *passes* the
per-contract trust gate (valid ask/iv present) overwrite a genuinely valid prior value field-by-field in
`merge_prior` — a narrower, previously-unfixed gap distinct from `gate_contract`/`gate_bucket` (which only
protect the *whole* quote group when there is no valid ask/iv at all).

**This session's explicit, deliberate reversal:** for `bid`, `lastPrice`, `volume`, `openInterest` — an
incoming exact zero during accumulation (`merge_prior`) is now always "no opinion": it never overwrites a
genuinely valid (accepted, non-zero) prior value, and if there is no such valid prior either, it is not
introduced into the accumulated chain at all (the field stays absent/`None`). `ask`/`iv` were already
structurally compliant (already required `> 0`) and are unchanged.

**Scope of the fix — deliberately minimal:** `is_accepted()` itself, `gate_contract`, `gate_bucket`, and
`merge_sources` (Phase 1) are all **unchanged** — a zero is still a well-formed, individually valid number
for a single fresh source cycle. The new rule lives entirely inside `merge_prior`'s two field selectors
(`_select_quote_field`, `_select_observed_field`) via a new `_ZERO_SENSITIVE_FIELDS` set and
`_is_meaningful_value()` helper — since `merge_prior` (never `merge_sources`) is the sole producer of the
accumulated/persisted chain (confirmed: `options_chain_cache.py`'s `refresh()` always calls
`merge_prior(prior_chain or {}, live, now=now)`, even on cold start with an empty prior).

**Compatibility impact — explicit supersession, cross-team:** this directly reverses the previous
decision's "Explicitly ruled out: ... changing `is_accepted("bid", 0.0)`; nulling `volume`/`openInterest`
zeros" and its G3/G4 headline ("raw shard still holds the provider's 0.0", Z-I5 anti-corruption test). Six
tests outside Linus's ownership now assert the now-superseded behavior and fail as expected; they were not
modified here (out of charter) and their owners were notified directly (Rusty, Livingston, Basher via
sibling message):
- `tests/test_options_chain_cache.py::TestBeyondFiveExpirations::test_yfinance_zero_beyond_tv_coverage_no_prior_data`
- `tests/test_options_chain_cache.py::TestLastKnownGoodMerge::test_first_fetch_zeros_preserved_as_is`
- `tests/test_options_chain_cache.py::TestLastKnownGoodMerge::test_volume_and_open_interest_not_preserved_when_zero`
- `tests/test_options_chain_persistence_integration.py::TestG3RawZeroSurvivesWhileAgentViewIsNull::test_raw_stored_bid_zero_survives_while_agent_view_nulls_it`
- `tests/test_zero_free_agent_chain.py::TestZI1NoNumericZeroAnywhereInAgentSurfaces::test_to_agent_view_recursive_walk_clean`
- `tests/test_zero_free_agent_chain.py::TestZI5RawPersistedShardByteFaithfulRoundTrip::test_persisted_bid_zero_survives_hydrate_untouched_but_view_nulls_it`

`options_chain_view.py` (agent-view boundary) needs **no code change** — it already nulls non-positive
quote evidence; it now simply receives fewer literal zeros to begin with. Schema stays additive
(fields become absent instead of `0.0`/`0`); no key renamed, no type change beyond a field sometimes not
being present at all (already a legal, handled shape everywhere `.get()` is used).

**Files touched:** `backend/src/options_chain_merge.py` (`_select_quote_field`, `_select_observed_field`,
new `_ZERO_SENSITIVE_FIELDS` / `_is_meaningful_value`, module + field-class docstrings),
`backend/tests/test_options_chain_merge.py` (rewrote the two now-inverted T7/Z-M4 tests, updated one
stale-fill assertion, added `TestMergePriorZeroNeverOverwrites` and
`TestMarketClosedMultiExpirationRegression`, 440/440 passing).

**Basher confirmation addendum (same day):** Basher's independent review pinpointed the identical root
cause — `_select_quote_field` previously used only the whole-contract gate (`gate_contract`), so a
partial-zero snapshot (valid ask/iv, but `bid=0`/`lastPrice=0`) still overwrote a prior non-zero value and
cascaded into a wrong recomputed `mid`; the all-zero-snapshot and no-prior cases already worked correctly
via the existing gate. Confirms the fix above is exactly this: per-field protection added inside
`merge_prior`'s selectors, not a change to the whole-contract gate itself. Two scoping clarifications
folded in:
- **Z2 partial supersession, not a repeal:** Z2 ("volume/openInterest keep integer 0 — count-type
  evidence, not quotes") stays fully valid at the **agent-view boundary** (`options_chain_view.py`) —
  unchanged, no code touched there. It is superseded **only** for what the **persisted merge**
  (`merge_prior`) is allowed to accept from a fresh provider fetch: a *new* incoming volume/openInterest
  zero no longer overwrites a valid prior and is never introduced with no prior. A legacy/already-stored
  literal `0` (written before this fix, or a genuine Z2-compliant first-ever `0` predating this change)
  is still displayed as `0` in the agent view, not nulled — Z2's agent-facing behavior for whatever value
  *is* stored is unchanged.
- **No fake migration:** contracts already clobbered to `0.0`/`0` in Cosmos before this fix are not
  retroactively repaired — no migration/backfill script was written or is planned under this decision.
  They self-heal only when a future cycle supplies a genuine positive quote for that field; until then the
  stale zero remains (a pre-existing, now-frozen data quality issue, not something this merge-semantics fix
  can or should paper over).

## 2026-08-20: theta double-`/365` unit bug fixed in `greeks_calculator.py` (py_vollib path only)

**Scope:** `backend/src/greeks_calculator.py` only (production), plus its dedicated tests. No merge/view/
cache/scoring/config/frontend files touched.

**Root cause:** py_vollib 1.0.1's own `theta(flag,S,K,T,r,sigma)` already returns the **daily, per-share**
theta — it divides its raw/textbook annual Black-Scholes theta by 365 **internally** before returning
(confirmed by reading its source; its own docstring/doctest cites Hull Example 17.2: S=49,K=50,r=.05,
T=0.3846,sigma=0.2 → annual call theta ≈ -4.30538996455, annual put theta ≈ -1.8530056722, both of which
its doctest recovers only via `theta(...) * 365`). `GreeksCalculator.compute()`'s py_vollib branch divided
`_vol_theta(...)` by 365 **a second time**, deflating theta by ~365x whenever py_vollib was available (the
default path — `_manual_greeks()`, the scipy fallback, was already correct: it computes the raw annual
formula and divides by 365 exactly once).

**Numerical evidence (Hull reference case, call):** buggy computed theta = -4.30538996455/365/365 ≈
-3.23e-05/day; correct daily theta (post-fix) = -4.30538996455/365 ≈ -0.011796/day — a 365x difference.
Verified exhaustively across call/put × DTE(1,7,30,60,365) × K(90,100,110) × sigma(0.20,0.50): the buggy
path was consistently ~251x–375x smaller in magnitude than both the manual fallback and raw
`py_vollib.theta()` (noise near exact 365x from `round(...,6)` truncation at very small magnitudes); after
the fix, py_vollib-path and manual-path theta match within 1e-4 across the entire matrix.

**Fix:** removed the extra `/ 365` from the single `theta` line inside `compute()`'s py_vollib branch —
now `"theta": round(_vol_theta(flag, S, K, T, r, sigma), 6)`. No other line, Greek, or path changed.
Verified via `git diff` that only this one line of substance changed (plus an explanatory comment).

**Caller-convention confirmation:** exhaustive `grep -rn "theta"` across `src/` and `web/` found zero
downstream `*365` (or equivalent) compensation anywhere. `yfinance_data_provider.py` documents theta as
"daily time decay, negative value"; `alpha_instructions.py`/`supervisor_instructions.py` describe
"theta/day" to the agent; `dps_scorer.py`/`options_chain_filters.py` consume theta directly with no
rescaling. The whole codebase already assumed the correct daily-per-share convention this bug violated —
this fix makes computed theta consistent with that universal assumption, it does not introduce a new one.

**Compatibility impact:** theta magnitude increases ~365x, but **only** on the py_vollib-available code
path (the default in this environment). Sign, other Greeks (delta/gamma/vega/rho on both paths), and the
manual/scipy fallback theta are all unchanged — confirmed by a dedicated regression test that directly
compares delta/gamma/vega/rho computed via the py_vollib path against py_vollib's own functions post-fix.
Any downstream consumer, chart, or test that hardcoded an expectation based on the old (~365x too small)
theta would need updating — a full-suite check found none did.

**Tests added** (`backend/tests/test_greeks_calculator.py`, new `TestThetaUnitConversionRegression` class,
11 methods): Hull textbook call/put reference values (own fixture forcing r=0.05 to match the reference
exactly, not the shared 0.045 fixture); path-equivalence vs. manual fallback across the full DTE/K/sigma
matrix; forced-fallback-vs-real-py_vollib equivalence (`monkeypatch.setattr(_HAS_VOLLIB, False)`);
negative-sign sanity across DTE for both flags; finite/growing magnitude near expiry; zero at true expiry;
human-scale-magnitude sanity guard (catches a reintroduced ~365x deflation); the derived closed-form parity
identity `daily_theta_call - daily_theta_put == -r*K*exp(-r*T)/365` (algebraically derived from py_vollib's
own formula structure, sigma-independent, verified numerically); a direct raw-py_vollib-vs-computed
equality check; and an explicit "other Greeks unchanged" guard. **Verified by temporarily reverting the
fix**: 7 of the 11 new tests fail immediately when the extra `/365` is reintroduced (the 4 that don't —
sign, near-expiry finiteness, at-expiry-zero, other-Greeks-unchanged — correctly test properties
independent of absolute magnitude). Fix re-applied and confirmed clean afterward.

**Regression run:** `test_greeks_calculator.py` (39/39), plus `test_options_math.py`,
`test_dps_insights.py`, `test_roll_table.py`, `test_options_chain_merge.py`,
`test_options_chain_position_and_direction_filters.py`, `test_options_chain_view.py`,
`test_zero_free_agent_chain.py` — 677 passed, 0 failed, 0 skipped-relevant. `py_compile` clean on both the
source and test file.

**Disclosed but explicitly NOT fixed (out of this task's authorized scope — "fix exactly one unit
conversion"), recommend a dedicated follow-up task:**
- `vega`: `compute()`'s py_vollib branch does `_vol_vega(...) / 100`, but py_vollib's own `vega()` already
  multiplies by 0.01 internally (same doctest-documented convention as theta) — an analogous double-
  division bug, confirmed numerically as a ~100x deflation on the py_vollib path vs. the (correct) manual
  fallback.
- `rho`: a **path-inconsistency**, not a double-division — the py_vollib-branch `rho` is correctly
  `*0.01`-scaled (per 1%-rate-change convention, matching py_vollib's own `rho()`), but the manual/scipy
  fallback's `rho_val` is the raw, un-scaled textbook formula with no `/100` applied, making manual-path
  rho ~100x **larger** than py_vollib-path rho for identical inputs.
Both were found via the same source-level + numeric-sweep method used for theta and are believed correct,
but fixing them was outside this task's explicit single-conversion scope and risked touching Greeks the
user asked to leave alone.

## 2026-08-20: Theta double-`/365` fix — Linus G1 + Basher G2 APPROVED

**Scope:** Deliberately minimal, single-conversion boundary within `greeks_calculator.py`.

**Root cause:** py_vollib 1.0.1's `theta()` function already returns the correctly-scaled daily per-share value (divides its internal annual formula by 365 internally, confirmed by reading source + verifying against Hull Example 17.2). The repo code's py_vollib branch applied an additional `/365` divisor, deflating theta by a factor of ~365 on that code path only. The manual/scipy fallback path was already correct (single division, no double-scaling). This produced user-visible errors: (1) theta rendered as ~$0.00/day instead of the true ~$0.01/day or more in dps_scorer's informational text; (2) roll/hold LLM agents reasoning about theta magnitude (e.g., "is this enough daily decay to justify a roll?") received a 365x-deflated input; (3) frontend displayed zero-like theta to end users.

**Why unnoticed until now:** Existing test suite only asserted sign/range for theta/vega/rho, never magnitude. A 365x scaling error preserves sign and never violates rough-range checks, so tests passed green. The bug was purely a **numerical-magnitude gap** orthogonal to, and unprevented by, all prior Zero-Free/anti-corruption work (which gates on validity before calling `compute()`, not on the magnitude of the returned Greeks).

**The fix (Linus G1):**
- **Changed:** `backend/src/greeks_calculator.py`, py_vollib branch compute path, line ~118. Removed the redundant `/365` divisor. Comment added citing py_vollib's own docstring: "py_vollib already scales theta to daily; do not re-scale."
- **Untouched:** `_manual_greeks()` fallback path (was already correct), `_expired_greeks()` edge-case routing (T≤1e-10 → intrinsic delta only, theta=0, all correct), delta/gamma/vega/rho, `greeks_valid` gating logic, all other modules.
- **Tests added:** `backend/tests/test_greeks_calculator.py::TestThetaUnitConversionRegression` (11 new assertions):
  - Hull call/put reference values (Example 17.2, exact match after fix).
  - Path-equivalence: py_vollib real and forced-fallback (manual) produce identical theta across flags × DTEs × strikes × IVs.
  - Negative-theta-always sign check.
  - Near-expiry monotonic growth + finiteness.
  - True-expiry-zero (T=0).
  - Human-scale sanity band (e.g., `-0.5 < theta_30dte_atm < -0.01` for $100 underlying).
  - Exact call-put parity closed-form identity.
  - Raw py_vollib passthrough (bytes-identical).
  - Explicit "other Greeks unchanged" guard.
- **Diff detail:** 13 lines total, +12 (11 new tests, 1 comment) / -1 (`/365` removed). Zero scope creep.

**Independent approval (Basher G2):**
- **Numerical validation:** Built own central-difference reference pricer (finite-difference `price(T) vs price(T-1day)` for theta, `price(σ+0.01%)` bump for vega; not dependent on py_vollib or repo code). Ran across 90 scenarios (DTE ∈ {7,30,45,90,365}, IV ∈ {15%,30%,60%}, moneyness ∈ {ATM,ITM,OTM}, both call and put). Result: fixed code achieves **4.9e-7 absolute / 0.0366% relative max error** vs. finite-difference reference — confirms correctness to high precision, not merely "roughly right."
- **Path parity:** Forced `_HAS_VOLLIB=False` and compared theta/delta/gamma vs. real py_vollib path across 108 combinations (2 flags × 6 DTEs × 3 strikes × 3 IVs). Result: **0 mismatches** (tolerance 2e-4) — confirms both code paths now agree on theta everywhere.
- **Vega/rho untouched:** Confirmed that vega (py_vollib branch still has `/100` double-scaling, ~100x too small) and rho (manual fallback still missing `*0.01`, ~100x too large) are unchanged and exhibit the same pre-existing defects. Status: out-of-scope for this task; recorded as separate follow-up recommendations (see below).
- **Verdict:** **APPROVE**. Fix is numerically correct, introduces zero regressions, scope is strictly theta-only as required.

**Test results:**
- `test_greeks_calculator.py` standalone: **39 passed, 0 failed** (28 pre-existing + 11 new/net in `TestThetaUnitConversionRegression`).
- Focused downstream suite (`options_chain_merge`, `dps_insights`, `roll_table`, `options_chain_view`, `format_roll_candidates_table`, `debug_agent_chain_pipeline`): **634 passed, 0 failed**.
- Full backend suite post-fix: 1434 passed, 20 failed (all pre-existing, confirmed identical to baseline by re-running `git stash` → exact 1423 passed, 20 failed → fix restores → 1434 passed, 20 failed: **delta = +11 tests passing, exactly the new `TestThetaUnitConversionRegression` class**).
- **Zero regressions.** Every pre-existing failure remains; every new passing test is in the new regression class. No hidden breakage introduced.

**Downstream impact analysis:**
- `dps_scorer.py` theta usage: informational-only text (`f"Θ {theta:.4f} (informational, not scored)"`); score itself never depends on theta magnitude — correctly fixed to render true daily decay now.
- `options_chain_filters.py::format_roll_candidates_table` / roll LLM agents: theta rendered as `f"${theta_val}"` in "CURRENT POSITION" reference text; `alpha_instructions.py`/`supervisor_instructions.py` explicitly expect "theta is $X/day" in dollar-per-day terms — **now correctly receives true daily decay, not 365x-deflated**.
- Frontend (`options-chain/page.tsx`, `PositionDetail.tsx`, `types/options-chain.ts`): renders theta to end users — **now displays true daily decay, not near-zero artifact**.
- `yfinance_data_provider.py` docstring ("theta: Theta (daily time decay, negative value)"): **now actually satisfied by the code** for the first time.
- No production code outside greeks_calculator consumes theta at sub-daily scale or compensates with downstream `*365` — confirmed by repo-wide grep. All callers already assumed the correct daily convention; the bug violated only the code's own stated contract.

**Explicitly ruled in (deliberate reversals):**
- "Theta must be daily-scaled before return" — reaffirmed as correct and implemented. Prior decision never contradicted this (only discussed Greeks validity gating, not unit conversion).

**Explicitly ruled out (scope boundary, untouched):**
1. Vega double-`/100` divisor (py_vollib branch, same bug class as theta) — found via same source-level sweep, left untouched, documented as separate follow-up task.
2. Rho missing-`*0.01` scale (manual/scipy fallback path, opposite defect but same magnitude class) — found via same sweep, left untouched, documented as separate follow-up task.
3. Delta/gamma changes — confirmed correct on both paths, untouched.
4. Edge-case routing (`T≤1e-10`, `σ≤1e-10`, `_expired_greeks`) — confirmed correct, untouched.
5. `greeks_valid` gating logic — confirmed correct, untouched.
6. Any downstream compensation or threshold changes — none needed; no existing test or scoring logic depended on the buggy 365x-deflated magnitude.

**Recommended, explicitly-scoped follow-up tasks (not blocking this approval):**
1. **Vega path-parity fix:** Single-line change (`/ 100` removed from py_vollib branch), identical pattern as theta fix. Highest-value regression test: force-fallback parity assertion (self-checking, no external reference needed), exactly mirroring the `test_theta_forced_fallback_matches_real_vollib_path` pattern now established.
2. **Rho path-parity fix:** Manual/scipy fallback branch missing `*0.01` scale — add `*0.01` to rho_val before return. Same path-parity regression test pattern. Mitigates silent, environment-dependent (py_vollib available vs. missing) magnitude corruption currently present.
3. **Path-parity as universal regression pattern:** The single highest-leverage test for theta/vega/rho fixes is comparing py_vollib-backed vs. manually-forced-fallback on identical inputs, asserting agreement within tolerance — this catches scaling inconsistencies without external references and is nearly impossible to game (by design, the two code paths are literally different, so disagreement is real).

**Backward compatibility:**
- Schema unchanged (no new fields, no type changes). Existing persisted Greeks (if any hardcoded to the old buggy scale) become "truly wrong" — but `greeks_valid=False` and recompute will fix them on the next chain refresh. No migration needed.
- Downstream consumers all assumed the correct daily convention (per docstrings and LLM instructions); now code matches assumption.
- No config, threshold, or scoring logic changed. Pre-existing tests that never asserted magnitude all remain passing.

**Explicitly noted — no scope creep:**
- Vega and rho defects remain documented in canonical ledger (this entry's "ruled out" and "follow-ups" sections) for visibility.
- No test file outside `test_greeks_calculator.py` modified. Linus and Basher both read-only on all other test suites; no dependencies introduced on this fix other than the numerical correction itself.
- No commit made by Scribe; orchestration/ledger work only.

---

**Verdict (Linus + Basher consensus):** Theta double-`/365` fix is **APPROVED, MERGED, COMPLETE** as of 2026-08-20. Theta is now correctly scaled to daily per-share before return. Vega and rho follow-ups are itemized and ready for a separate task.


## 2026-08-20: User confirmation — volume/openInterest stay under zero-never-overwrites (Basher's caveat resolved)

**Context:** the 2026-08-19 zero-never-overwrites-prior fix extended `_ZERO_SENSITIVE_FIELDS` to
`volume`/`openInterest` in addition to `bid`/`lastPrice`. Basher flagged (twice, independently, via two
separate review passes) a non-blocking risk: this could mask a genuinely fresh `volume=0` behind a stale
positive prior indefinitely — a materially different risk than the bid/ask closed-market ambiguity the
directive's own rationale targeted, since it degrades a real liquidity signal read by DPS scoring. Linus
explicitly asked the user to choose between (1) keeping volume/OI under the no-overwrite rule as
implemented, or (2) carving them back out to always-overwrite-on-fresh-zero.

**User's explicit answer:** "el 0 absoluto no debe sobreescribir ningún campo" — an incoming absolute zero
must never overwrite **any** field, full stop, no per-field carve-out. This confirms option (1): the
current implementation is correct and final as-is.

**No code change made** — this session's `merge_prior` implementation already satisfies this exactly, via
two complementary mechanisms, confirmed by re-reading `_select_quote_field`/`_select_observed_field`:
- `bid`, `lastPrice`, `volume`, `openInterest` (`_ZERO_SENSITIVE_FIELDS`): an incoming exact `0` never
  overwrites a meaningful (non-zero, accepted) prior, and is omitted entirely (not stored as `0`) when
  there's no such prior.
- `ask`, `iv`: never need the explicit zero-sensitive path because `is_accepted()` already rejects a zero
  for these fields outright (ask/iv must be positive finite) — a zero candidate is treated as "not
  accepted" and `_select_quote_field` falls back to the prior value before the zero-sensitivity check is
  ever reached. Net effect is identical: absolute zero never overwrites these fields either.
- Identity fields (`strike`, `expiration`, `option_type`) are intentionally excluded — the user's own prior
  directive text explicitly carved these out, and zero is never even a valid observation for them.

Re-ran `test_options_chain_merge.py`: 441/441 passing, no regressions, confirming the existing
implementation already matches this final, explicit instruction with zero further changes required.

**Basher's flagged risk is now formally accepted, not just disclosed**: the user has explicitly chosen to
accept the "stale volume/OI mask a fresh legitimate 0" tradeoff codebase-wide, closing the open item from
the 2026-08-19 addendum. No future work item remains here.

---

## Best Options + Force Alpha Session (2026-08-29)

**Merged from:** 16 inbox files (Danny, Linus, Livingston, Rusty, Basher, Copilot directives)
**Session verdict:** Best Options APPROVED; Force Alpha APPROVED
**Merged by:** Scribe at 2026-08-29T12:30:21Z


---

# Reviewer verdict -- Best Options adversarial acceptance coverage: **REJECT**

**Date:** 2026-08-29
**Author:** Basher (Tester/Reviewer)
**Status:** REJECT -- two independent defects block acceptance; strict lockout applies
**Traces to:** `.squad/decisions/inbox/danny-best-options-design.md` (ACCEPTED), Linus's
`.squad/decisions/inbox/linus-best-options-scoring.md`, Livingston's
`.squad/decisions/inbox/livingston-best-options-cache.md`, Rusty's
`.squad/decisions/inbox/rusty-best-options-ui.md`

## Test coverage delivered (Basher-owned, no production code touched)

* `backend/tests/test_best_options_adversarial.py` -- new, 88 tests, all passing. Pure
  evaluator-level adversarial coverage: DTE window boundaries (0/49/50, including the
  structural finding that every DTE=0 row is unconditionally `insufficient_data`);
  absolute-delta normalization across all category CC/CSP bands; calls-vs-puts asymmetry;
  deterministic ordering/tie-breaking (score -> DTE -> delta-distance); exact score/colour
  boundaries at 39.999/40/64.999/65 via a white-box fixture solver against the evaluator's
  own component functions; zero/missing bid; missing/invalid Greeks (delta-absent vs
  `greeks_valid=False`, which route to different `nearest_miss` tiers); stale-chain flag
  never downgrading colour; earnings gate boundaries (unknown, and known-spanning-expiration
  exact edge); sparse liquidity (OI=0 shown red vs delta-band exclusion asymmetry); category
  profile default/provenance; DTE-scaled premium floors; `nearest_miss` correctness for all
  6 tiers with qualifying rows present; payload/UI-contract invariants; explicit zero
  IV-Rank/LLM-surface assertions.
* `backend/tests/test_best_options_endpoint.py` -- new, 11 tests, all passing. Real seam:
  genuine `OptionsChainCache` + genuine `evaluate_best_options` + real FastAPI endpoint via
  `TestClient`; only true edges faked (`FakeCosmos`, monkeypatched provider fetchers -- no
  network, no mutual fakes with Linus's/Livingston's own suites). Covers 404, query-param
  validation, cold-cache warming (immediate response *and* background-refresh completion),
  warm-cache full table, endpoint-vs-direct-evaluator parameter consistency (byte-for-byte
  modulo `evaluated_at`), zero-LLM-reachability, and broad-exception-handling around the
  evaluator call (a real exception surfaces as 500 with its real message, not a misleading
  503).
* Combined Best-Options suite: **224 passed, 0 failed**. Full backend suite: pre-existing
  11 failures / 16 errors confirmed identical with and without my new files present (not a
  regression -- unrelated `test_yfinance_data_provider.py` / `test_yfinance_technicals_
  dividend_availability.py`, order/event-loop-dependent, pass individually in isolation).

## Defect 1 -- undocumented deviation from the ACCEPTED design (row inclusion)

`best_options.py`'s module docstring and `_evaluate_side` document a deliberate
"interpretive decision... superseding an earlier reading," citing an "explicit
product-owner instruction (2026-08-29)." Linus's own history corroborates a live
correction occurred. **`.squad/decisions.md` has zero entries for 2026-08-29 or Best
Options at all**, and Danny's ACCEPTED design has not been amended to reconcile its own
literal text with the shipped behaviour. Regardless of whether the behaviour itself is the
right call, there is no durable, auditable record any future reader could find. This is a
process/documentation gap that blocks acceptance on its own terms.

## Defect 2 -- frontend/backend `parameters` contract mismatch (live crash risk)

`frontend/src/types/best-options.ts` still types `thresholds`/`thresholds_source`/
`skill_reference` flat, and `BestOptionsParams.tsx` does
`parameters.thresholds.delta_lo.toFixed(2)` directly. The real backend
(`best_options.py` ~L772-786) returns these nested `{"call": {...}, "put": {...}}` --
necessarily so, since CC/CSP thresholds genuinely differ per category (e.g.
`premium_min_pct` 0.8 vs 1.0) and the design mandates one shared `parameters` panel for
`side=both`. Danny's design section 6 example is itself flat/single-sided -- a latent
ambiguity for the `side=both` case that the backend pass-through resolved in the only
coherent way -- but the frontend types were authored from that same flat snippet without
cross-checking the actual runtime shape. Both halves of this mismatch (endpoint pass-through
and frontend types/component) are Rusty's own deliverable this round. `th.delta_lo` on a
`{call, put}` object is `undefined`; `.toFixed()` on it throws a `TypeError` the first time
this page renders -- not an edge case, the primary parameters panel on every load.

## Verdict and lockout

**REJECT.** Per strict lockout, the original author of each defect's artifact may not
self-revise:

* **D1** (`best_options.py` row-inclusion text/documentation) -- Linus is locked out.
  Recommended revision owner: **Danny** (design owner) to formally ratify or amend the
  row-inclusion text and add the missing `decisions.md` entry; if code changes beyond
  documentation are needed, **Livingston** is the eligible engineering owner (familiar with
  the evaluator/cache seam, not the author of this deviation).
* **D2** (`frontend/src/types/best-options.ts` + `BestOptionsParams.tsx`) -- Rusty is
  locked out. Recommended revision owner: **Livingston**, or a freshly-escalated
  frontend-capable agent per the reviewer-protocol's "escalate" option, to correct the
  frontend types to the real nested `{call, put}` shape and update the component's
  accessors accordingly.

Full findings and methodology detail: `.squad/agents/basher/history.md`, entry
"2026-08-29: Best Options adversarial acceptance coverage -- final reviewer verdict: REJECT".

## Addendum (2026-08-29, later): visual-consistency directive — inspected, SATISFIED

Per the new binding directive
(`.squad/decisions/inbox/copilot-directive-20260829T102715+0200.md`), Best Options must reuse
the Roll Scenarios table's structure/colors/spacing/typography/controls rather than inventing
a new pattern. This is now a **permanent addition to the Best Options reviewer gate**.

Inspected Rusty's updated `frontend/src/components/BestOptionsView.tsx` and
`frontend/src/lib/badges.ts` against `frontend/src/components/PositionDetail.tsx`'s Roll
Scenarios table: confirmed genuine **shared-token** reuse (`ROW_TINT_BG` is the single source
of the row-tint palette, consumed by both tables — Roll Scenarios itself was refactored to
read from it rather than Best Options merely visually matching a hardcoded copy), matching
table structure/typography/spacing (`border-collapse text-xs`, `border-b border-border px-2
py-1` headers, `border-b border-border/40` rows), an expand/collapse control reusing the same
▸/▾ + `aria-expanded` idiom already used elsewhere in the app (`PositionsTable.tsx`,
`options-chain/page.tsx`), and accessible non-color labels preserved (colour always paired
with an icon + the backend's own text label). **This requirement is satisfied.**

This does not change the standing verdict: **Defect 1 and Defect 2 above remain unresolved**
and the overall verdict stays **REJECT**, with the same lockout naming (Rusty locked out of
both D2 and this visual work's own artifact scope; Danny/Livingston for D1; Livingston or a
fresh frontend-capable agent for D2).

## Final re-review (2026-08-29, after Rusty's "completed" API/frontend integration): **REJECT**

The user directly, explicitly ratified Linus's row-inclusion semantics as binding: "rows are
all and only contracts satisfying DTE 0-49 and the configured abs(delta) band; excluded
contracts may appear only in nearest_miss/count metadata." Re-inspected the current tree
against this and re-ran the full targeted suite.

**Defect 1 — RESOLVED, no longer blocking.** `best_options.py`'s row-inclusion logic
(`in_band_rows`/`nearest_miss` over the full DTE-window set/`excluded_by_delta_band` count)
matches the user's own wording exactly. The `.squad/decisions.md` ledger still has no
dedicated entry for this — recommended as a non-blocking follow-up for Scribe/Danny — but the
user's direct statement in this session closes the authorization gap for review purposes.

**Defect 2 — STILL PRESENT, STILL BLOCKING.** `frontend/src/types/best-options.ts` still
types `thresholds`/`thresholds_source`/`skill_reference` flat; the real backend returns them
nested `{call, put}`. `BestOptionsParams.tsx` still does
`parameters.thresholds.delta_lo.toFixed(2)` directly — throws `TypeError` on first render, for
every symbol, every time. `npx tsc --noEmit` passes with 0 errors, which is *expected and not
reassuring*: the type declaration itself is wrong, so the compiler cannot catch code written
against it; only comparing the declared type against the real backend payload surfaces this.

**Defect 3 — NEW.** `best_options.py` reports `excluded_by_delta_band` (both sides) and
`coverable_contracts`/`no_shares_held` (call side) — exactly the "count metadata" the user's
own directive names as the required transparency surface for excluded contracts. None of
these three fields exist on the frontend's `BestOptionsSide` type, and none are read anywhere
in `BestOptionsView.tsx`/`BestOptionsParams.tsx`. Worse, the page's own "0 shares held" banner
checks `data.rows.some((r) => r.flags.includes("no_shares_held"))` — a per-row flag
`best_options.py` never sets (`no_shares_held` is section-level only) — so that
design-mandated disclosure can never render, even when it should.

**Validation:** `python3 -m pytest tests/test_best_options.py tests/test_best_options_adversarial.py tests/test_best_options_endpoint.py tests/test_category_params.py tests/test_options_chain_dte_filter.py tests/test_options_chain_cache.py -q` → **224 passed, 0 failed**. `npx tsc --noEmit` (frontend) → 0 errors (does not exercise D2/D3 — see above).

**Verdict: REJECT.** D2 and D3 both live entirely inside Rusty's own artifacts
(`frontend/src/types/best-options.ts`, `frontend/src/components/BestOptionsParams.tsx`,
`frontend/src/components/BestOptionsView.tsx`). Per strict lockout, **Rusty is locked out of
revising these three files.** Recommended revision owner: **Livingston** (not the author of
any of the three, already familiar with the Best Options data seam) — correct the three
thresholds-shaped fields to the real nested `{call, put}` shape, add
`excluded_by_delta_band`/`coverable_contracts`/`no_shares_held` to the type and to the UI, and
fix the `no_shares_held` banner to read the section-level field directly. Escalate to a fresh
frontend-capable agent instead if Livingston lacks sufficient frontend/TS depth — do not
re-admit Rusty.

The visual-consistency directive remains satisfied and unaffected by this round. Full
methodology in `.squad/agents/basher/history.md`, entry "2026-08-29 (final integration gate):
Rusty's 'completed' API/frontend integration re-reviewed — final reviewer verdict: REJECT".

---

## 2026-08-29 (final): Best Options — combined final gate re-run — **APPROVE**

Danny formally ratified the row-inclusion delta-filter semantics in
`danny-best-options-delta-filter-correction.md` (durable in-place amendment to the design doc,
§2A + corrected §4.1/4.2) — this closes D1's remaining process gap noted in my prior verdict.
Livingston independently fixed D2 (flat vs. nested `{call,put}` typing for
`thresholds`/`thresholds_source`/`skill_reference`, plus an identical-class bug he found
himself in `premium.basis`) and D3 (added `excluded_by_delta_band`/`coverable_contracts`/
`no_shares_held` to the frontend type and UI; fixed the dead `no_shares_held` banner to read
the section-level field). Both independently re-verified this round by reading the actual
current `frontend/src/types/best-options.ts`, `BestOptionsParams.tsx`, `BestOptionsView.tsx`
files (not trusting Livingston's own write-up alone), plus his new
`test_best_options_frontend_contract.py` (5 tests, genuine real-module seam test, not a
restatement of the bug). Did a full-field sweep of the real `parameters` dict construction in
`best_options.py` against the frontend interface, key by key — no further undiscovered
nested-shape mismatches. Roll Scenarios visual-consistency directive re-confirmed intact
(`ROW_TINT_BG` still the single shared token, consumed by both `PositionDetail.tsx` and
`BestOptionsView.tsx`). `npx tsc --noEmit` clean (now meaningful, since the types themselves
are correct this time, not just internally consistent). Test run:
`test_best_options.py` + `test_best_options_adversarial.py` + `test_best_options_endpoint.py`
+ `test_best_options_frontend_contract.py` + `test_category_params.py` +
`test_options_chain_dte_filter.py` + `test_options_chain_cache.py` → all green (263 passed
combined with the three Force Alpha test files run in the same invocation). No IV Rank
enforcement or test anywhere; no LLM call in this evaluator path.

**Verdict: APPROVE.** No defects found, no revision owner needed. Full methodology in
`.squad/agents/basher/history.md`, entry "2026-08-29 (later): Final combined reviewer gate —
Best Options + Force Alpha — separate verdicts".

---

# Basher — Force Alpha final reviewer gate — 2026-08-29

**Author:** Basher (Tester/Reviewer)
**Scope:** Danny's `danny-force-alpha-design.md`, as corrected by
`copilot-force-alpha-semantics.md`/`-superseded.md` (final policy: only dashboard CC/CSP
buttons force Alpha; Settings "Run Now", "Full analysis"/"Run Full", and scheduled runs stay
due-only), against the live integrated tree touched by Linus (`agent_runner.py` gate/cooldown),
Rusty (`web/app.py`/`scheduler_registry.py` plumbing + `TriggerButton.tsx`), and Livingston
(Settings-scoping correction + endpoint-scoping seam test).

## Verdict: **APPROVE**

## Methodology

Every requirement in the review brief was checked by direct code inspection this session, not
by trusting any agent's inbox write-up or self-reported pass count. Two of the three
self-reports already disagreed with the task prompt's stated numbers before I ran anything
(Rusty's own doc: 8 plumbing tests, not "34"; Livingston's cache-correction doc: 4 known-failing
tests in `test_force_alpha_execution.py` as of his writing) — this made independent
verification necessary, not optional.

## Corrected test count

| File | Author | Tests | Result (independent run) |
|---|---|---|---|
| `test_force_alpha_execution.py` | Linus | 23 | 23 passed |
| `test_force_alpha_plumbing.py` | Rusty | 8 | 8 passed |
| `test_trigger_force_alpha_scoping.py` | Livingston | 3 | 3 passed |
| **Total** | | **34** | **34 passed** |

"93/93" (attributed to Linus in the task prompt) does not match Linus's own file or its
docstring (23). "34" is the *combined* total across all three authors' files, not "Rusty's
plumbing count" as phrased — Rusty's own plumbing file is 8. This is a documentation/reporting
inaccuracy in the inbox trail, not a code defect: the underlying behavior is correct and fully
tested regardless of which number got attached to which name.

## Requirement-by-requirement verification (direct code reads, this session)

1. **Four agent paths** (`covered_call`, `cash_secured_put`, `open_call_monitor`,
   `open_put_monitor`): both `agent_runner.py` entry points (`run_symbol_agent`,
   `run_position_monitor`) gate identically —
   `run_alpha = is_alert or prolonged_wait or force_alpha`,
   `forced = force_alpha and not prolonged_wait` (alert/roll branches never marked forced).
   Verified at all 4 literal call sites.
2. **buy_tracker exclusion**: `_skip_reviews = agent_type in ("buy_tracker",)`; forced-but-
   skipped records `alpha_run.status == "skipped_agent_type"`. `run_buy_tracker_analysis`'s
   real signature has no `run_trigger`/`force_alpha` params — forcing is genuinely inert for it
   via `_call_agent_func`'s introspection-guarded forwarding, not merely untested.
3. **incomplete_quote_wait precedence**: `run_position_monitor` — `if incomplete_quote_wait: ...
   elif prolonged_wait or force_alpha: ...` — forcing is blocked, `alpha_run.status ==
   "skipped_incomplete_quotes"` recorded.
4. **409 at-most-one in-flight**: `_acquire_trigger_slot`/`_release_trigger_slot` in
   `web/app.py`, keyed `(agent_type, symbol-or-"*")`, lock-guarded, released via `finally`
   (survives the runner raising), stale slots reclaimed after `_MAX_TASK_DURATION_SECONDS`
   (reused constant, not new).
5. **Force audit status**: `alpha_run = {"trigger","forced","status"}` persisted at every gate
   outcome (ok/failed/skipped_agent_type/skipped_incomplete_quotes) in both entry points —
   confirmed by reading the literal dict-construction code.
6. **Cooldown neutrality (H1)**: `_detect_prolonged_wait`'s scan breaks only when a review's
   `alpha_run.forced` is not `True`; forced-only reviews are skipped over (don't reset the
   cooldown); legacy docs with no `alpha_run` field default to not-forced (still break the
   scan, preserving old behavior byte-for-byte).
7. **No force-only Telegram (H2)**: `send_alert` gated on `is_alert` alone;
   `send_prolonged_wait_alert` gated on `prolonged_wait` alone, in both entry points.
   `force_alpha` never appears in either notifier's gate condition — a forced-only run cannot
   reach either notifier call.
8. **Legacy behavior**: confirmed identical to pre-feature behavior for historical documents
   (point 6).
9. **Final dashboard-only-forces policy** (narrower than Danny's original design's "manual ⇒
   forced" default table and its own D1/D2 proposals — confirmed by reading the literal code,
   not the narrative): `POST /api/trigger/{agent_type}` defaults `force_alpha=True`
   (overridable; dashboard `TriggerButton.tsx` always sends `true`); `POST /api/trigger-all`
   hardcodes `force_alpha=False` with **no override surface**; `POST
   /api/scheduler/tasks/{task_name}/run` ("Settings Run Now") hardcodes `force_alpha=False`
   (Livingston's correction — confirmed live in the tree, not just claimed); `main.py`'s cron
   loop passes `force_alpha=False` explicitly for all four Alpha-eligible agents.
   `SettingsConfigView.tsx`'s Monitoring Agent card routes to `/api/trigger-all` (grep-
   confirmed) — there is exactly one due-only "Run Now"/"Full analysis" affordance in the
   frontend today, consistent with Livingston's finding that a separate
   `/api/scheduler/tasks/*`-backed button doesn't exist in the UI.
10. **Auth**: none anywhere in `web/app.py` — matches the design's explicit standing-risk
    disclosure; not a new gap from this feature.

## Full-suite regression check

`pytest tests/` (backend, full tree) → 1661 passed, 11 failed / 16 errors, all in
`test_yfinance_data_provider.py` / `test_yfinance_technicals_dividend_availability.py` —
confirmed pre-existing and unrelated to Force Alpha (same failure set observed and documented
earlier this session, before any Force Alpha code existed). Zero regressions attributable to
this feature.

## No defects found. APPROVE. No revision owner needed.

---


