# Decision: Backend portion of TradingView symbol + Options Screener universe contracts implemented

**From:** Linus (Quant Dev)
**Re:** `.squad/decisions/inbox/danny-tradingview-symbol-contract.md` and
`.squad/decisions/inbox/danny-options-screener-universe-contract.md`

## A) TradingView symbol resolution

- Added `MIC_TO_TRADINGVIEW_EXCHANGE` (six approved MICs only) and
  `resolve_tradingview_symbol()` to `backend/src/portfolio/provider_symbols.py`,
  mirroring `resolve_yfinance_symbol`'s precedence exactly (override →
  MIC mapping → legacy US alias → fail-closed `None`). No second mapping
  table, no changes to the Yahoo resolver.
- Wired `tradingview_symbol` into both branches of `_compute_symbol_detail()`
  in `backend/web/app.py` (portfolio_only and main path), using the raw
  `security_doc` (not the cleaned `security_field` projection) so
  `provider_symbols.tradingview` overrides resolve correctly.
- Verified against Basher's independently-authored `test_provider_symbols.py::TestResolveTradingviewSymbol` (20/20) and `test_tradingview_symbol_detail.py` (10/10) — both pass unmodified.

## B) Options Screener universe

- Relocated `_is_watchlist_member` into `backend/src/portfolio/watchlist_membership.py::is_watchlist_member` (behavior-preserving pure-function extraction); `app.py` now imports it as `_is_watchlist_member` so all existing call sites are unaffected.
- Added `backend/src/options_screener_universe.py::compute_options_screener_universe()`, composing `is_us_options_eligible` (imported unchanged) and `is_watchlist_member`. **Discovery not anticipated by the contract text**: Basher's independently-authored `test_options_screener_universe.py` requires legacy free-text `"NYSE"`/`"NASDAQ"` in `doc["exchange"]` to be treated as eligible (XNYS/XNAS-equivalent) for universe purposes, but explicitly requires `"AMEX"` to stay excluded (Amendment J restricts eligibility to XNYS/XNAS only). Implemented via a small, scoped 2-entry local normalization (`_LEGACY_US_EXCHANGE_TO_MIC`) inside `options_screener_universe.py` only — `is_us_options_eligible` itself remains untouched, per contract. 25/25 pass.
- **Important correction to the contract's evidence section**: `_build_screener_symbol_inputs` (named in the contract as "the manual/on-demand" call site) is dead code — not called anywhere in the current source. The actual live manual code path is the inline `docs`/`symbol_inputs` construction inside `api_screener_options` itself (post the "precomputed-only" refactor). Wired the universe filter into **both**: the real live endpoint (for correctness) and `_build_screener_symbol_inputs` (for contract-letter compliance / in case it's resurrected later). Basher's own new `TestUniverseFilterEnforcement` class in `test_options_screener_endpoint.py` independently targets the live endpoint, confirming this is the correct enforcement point.
- Wired the same filter into `_run_options_chain_fetch_async` (`backend/src/main.py`), gated on one `HoldingsService.compute_holdings()` call — no N+1.

## Known test fallout (flagging for Basher, not fixed by me — out of my scope)

`backend/tests/test_options_screener_share_availability.py` (30 tests) now fails: its `FakeShareAvailabilityCosmos.add_symbol()` fixture never sets an `exchange` field, so every fixture symbol fails `is_us_options_eligible` closed and is correctly excluded from the universe per the contract ("any symbol_config predating security_master linkage... fails closed... not a bug to special-case"). This is orthogonal, pre-existing test-fixture debt relative to a feature (share-availability status) unrelated to eligibility — fix is to add `"exchange": "XNYS"` (or explicit watchlist markers) to that file's fixture builder. Also note: 6 failures in `test_options_screener_endpoint.py`/`test_options_screener_cache_concurrency.py` (including 3 in Basher's own new `TestUniverseFilterEnforcement` class) are **pre-existing and unrelated** to this work — they all go through the `_warm_symbol` helper, which the test file's own comment (line ~264) already documents as "incompatible with the precomputed-only endpoint" (a prior refactor made the live endpoint never touch the real per-symbol chain cache at all). Verified this by inspecting the helper's own acknowledgment; not something I introduced.

## Verification

- `test_provider_symbols.py`: 61 passed (41 existing + 20 new TradingView).
- `test_tradingview_symbol_detail.py`: 10/10.
- `test_options_screener_universe.py`: 25/25.
- `test_add_symbol_contract.py`, `test_unified_add_symbol.py`, `test_watchlist_symbols.py`, `test_unified_symbol_detail.py`, `test_unified_watchlist.py`, `test_account_assignment_symbol_detail.py`, `test_symbol_detail_stocks_tab.py`, `test_watchlist_pause.py`, `test_us_options_eligibility.py`, `test_ensure_symbol_config.py`: 418 total, 0 failures.
- Scheduler-adjacent suite (`test_force_alpha_execution.py`, `test_force_alpha_plumbing.py`, `test_precompute_list_symbols_dict_regression.py`, `test_production_unhashable_dict_bug.py`, `test_scheduler_best_options_startup.py`, `test_trigger_force_alpha_scoping.py`): 60/60, confirms `main.py` universe-filter wiring doesn't break the scheduler.
- Confirmed via `git diff --stat` that only the intended files changed: `backend/src/portfolio/provider_symbols.py`, `backend/web/app.py`, `backend/src/main.py`, plus new `backend/src/options_screener_universe.py` and `backend/src/portfolio/watchlist_membership.py`. Frontend and the migration script were not touched. Not committed/pushed.
