# Danny — FINAL Reviewer Gate: Contracts A/B/C/D

## Verdict: REJECTED (single blocker, narrow scope)

Evaluated the actual current diff (not agent summaries) against all four
approved contracts:
- A) `danny-single-add-symbol-contract.md`
- B) `danny-tradingview-symbol-contract.md`
- C) `danny-options-screener-universe-contract.md`
- D) `danny-legacy-symbol-config-migration-contract.md`

## Confirmed correct (no blockers)

- **Contract A**: legacy `POST /api/symbols` + `cosmos_db.create_symbol()` +
  BFF POST route fully removed. `add_symbol`/`_start_symbol_warmup` in
  `portfolio_routes.py` gate warm-up strictly on `config_created` via a
  pre-check `read_item` (an improvement over my original sketch — avoids
  re-triggering warm-up on re-add of an existing auto-enrolled config).
  `warmup_started` present in response. `DgiScreenerView.tsx` `AddButton`
  correctly calls canonical `addSymbol()` + separate PUT toggle.
  **Basher's claim that `DgiScreenerView.tsx:257` is a stale `return null`
  defect is incorrect** — verified `toExchangeMic` (lines 251-258) is
  fully correct and fail-closed; no `AMEX`/`ARCA`/default-XNYS mapping bug
  exists in the product code.
- **Contract B**: `MIC_TO_TRADINGVIEW_EXCHANGE` (6 MICs) +
  `resolve_tradingview_symbol()` in `provider_symbols.py` match precedence
  exactly. Both `_compute_symbol_detail` branches in `app.py` wire
  `tradingview_symbol` with no N+1. Frontend `TradingViewSymbolInfo.tsx`/
  `RtChart.tsx` take one `tvSymbol` prop, render nothing on null.
- **Contract C**: `compute_options_screener_universe()` correctly applied
  at both the manual endpoint (`api_screener_options`) and the scheduled
  job (`_run_options_chain_fetch_async` in `main.py`), before any
  chain/cache work, using one batched `HoldingsService.compute_holdings()`
  call (no N+1). `_build_screener_symbol_inputs` is pre-existing dead code
  (zero live callers, confirmed via grep — the live endpoint uses its own
  inline "precomputed-only" path per the `copilot-options-screener-
  precomputed-only.md` directive merged 2026-08-29, over a week before
  this batch); Linus's edit to it is harmless and not a regression.
  `options_screener_universe.py`'s local `_LEGACY_US_EXCHANGE_TO_MIC`
  (2 entries) duplicates data already in `provider_symbols.LEGACY_ALIAS_TO_MIC`
  — a real but **non-blocking** DRY nit (no behavioral difference; AMEX
  still correctly excluded either way). Flagged as advisory, not a
  rejection reason.
- **Test-skip review** (`test_options_screener_endpoint.py`,
  `test_options_screener_cache_concurrency.py`): all 5 skips reviewed
  individually. Each documents a feature that is **genuinely gone**
  (concurrency cap / cold-bucket / on-request warming — the
  precomputed-only architecture predates this batch by over a week) and
  each cites replacement coverage that was verified to actually exist and
  be equal-or-broader (e.g. `test_options_screener_adversarial.py`'s
  full-JSON-dump `coverable_contracts` check is stronger than the retired
  per-row scan; `TestScheduledPathUniverseFilter` genuinely exercises the
  scheduler path). No concealment found — skips are legitimate.
- **Contract D**: `migrate_legacy_symbol_config.py` correctly reuses the
  canonical `LEGACY_ALIAS_TO_MIC` (no third duplicate table). `--audit`
  default, `--backup-only`, `--apply` (mandatory backup), `--restore` are
  mutually exclusive; exit codes 0/1/2/3 match the contract. Verified live:
  `--help` prints cleanly; default `--audit` with no `COSMOSDB_*` env vars
  fails closed with exit code 2 and **never attempts a real Cosmos
  connection**. `collision_ambiguous` fail-closed path present, never
  guesses across cross-exchange ticker collisions.

## Tests run

- Targeted: `test_migrate_legacy_symbol_config.py`,
  `test_options_screener_universe.py`, `test_tradingview_symbol_detail.py`,
  `test_add_symbol_contract.py`, `test_provider_symbols.py`,
  `test_watchlist_symbols.py`, `test_options_screener_share_availability.py`,
  `test_unified_add_symbol.py` → **303 passed**.
- `test_options_screener_endpoint.py` + `test_options_screener_cache_concurrency.py`
  → **18 passed, 5 skipped** (skips reviewed and confirmed legitimate above).
- Full backend suite → 20 failures, all in `test_yfinance_data_provider.py`
  (untouched by this diff). Verified against base via `git stash`: same
  file fails identically (2 failures) in isolation with or without this
  batch's diff; the extra 18 only appear under full-suite run ordering
  (event-loop test pollution), confirmed pre-existing and unrelated —
  **not counted as a blocker**.
- Frontend `tsc --noEmit` → clean.
- Frontend `next build` → succeeds, `.next/BUILD_ID` produced.
- Migration CLI `--help` and default `--audit` (no Cosmos env vars) → both
  behave safely as documented above.

## Blocker (must fix before APPROVED)

**`frontend/tests/tradingViewSourceContract.test.mjs`, test `DGI-4`
("toExchangeMic has no 'return XNYS' default fallback") is a false
positive.** It does a bare substring scan for `return "XNYS"` /
`return 'XNYS'` anywhere in the whole `DgiScreenerView.tsx` source, which
also matches the **correct, explicit** `if (ex === "NYSE") return "XNYS";`
mapping at line 255 — there is no unconditional default fallback in the
product code (verified: line 257 is `return null;`). The test's own intent
(reject an *unconditional* XNYS fallback) is not what its assertion
actually checks. This is a test-logic defect in Basher's new file, not a
product regression — confirmed the file is new/untracked to this batch via
`git status`.

**Required fix**: rewrite the `DGI-4` assertion to scope to the
unconditional-fallback pattern (e.g. assert the last `return` in
`toExchangeMic` is `return null;`, or require the `"XNYS"` return line to
be guarded by an `ex === "NYSE"` condition) rather than a bare
whole-file substring match. No product code change is needed.

## Assignment

- **Basher**: fix `DGI-4` in `frontend/tests/tradingViewSourceContract.test.mjs`
  only. No other file in scope.
- Reviewer lockout: Basher is locked out from touching any other
  contract's product code in this cycle; this fix is test-file-only and
  narrowly scoped.

Re-submit for a follow-up gate once `DGI-4` is corrected and rerun.
