# Basher — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Stack:** Python, Microsoft Agent Framework, Azure Foundry (gpt-5.4-mini)
- **MCP:** iflow-mcp_ferdousbhai_investor-agent 1.6.3
- **Description:** Two periodic trading agents for covered call and cash-secured put sell signals. Local runtime, configurable polling, stock symbols from files, decision logs, sell signal alerts.

## Core Context

### Phase 4a Deployment Automation (2026-03-28)
- Provisioning script (`provision_cosmosdb.sh`): idempotent CosmosDB setup with custom indexing policy
- Migration script (`migrate_to_cosmosdb.py`): Data import from local logs + txt files with integrity checks
- Dockerfile: Updated for cloud deployment (removed local volume mounts, added scripts)
- README: Architecture docs, env var table, Docker run examples, CosmosDB setup guide

### CosmosDB Unified Schema Migration (2026-04-01)
- 4-phase migration (`migrate_cosmos_events.py`): Export backup → Transform doc schema → Write unified events → Validate
- Features: `--dry-run` mode, `--restore` rollback capability, orphaned record handling, duplicate resolution
- Transformation: Merge alert docs into parent activities, strip prefixes, resolve timestamp collisions
- Pattern: Idempotent scripts with dry-run, backup capability, progressive validation

### Anti-403 Test Suite (2026-04-06)
- 28-pass test suite for per-symbol session isolation, exponential backoff recovery, warmup behavior
- Key patterns: Session scoping per symbol (not global), retry with backoff, config-driven settings
- Edge case: `tv_403` flag unreachable in some code paths (dead code in exception handler)

## Learnings

### Economics Contract Multiplier Test Expectations (2026-07-01)
- Economics report tests were stale after `web/app.py` added `CONTRACT_MULTIPLIER = 100` for option contract dollar amounts. Updated `tests/test_economics.py` expectations to ×100 dollar values while leaving ratios, per-share fields, counts, filters, and ordering semantics unmultiplied for dsanchor.

### Retired Obsolete yFinance Fetch DTE-Window Tests (2026-07-01)
- Retired obsolete DTE-window tests `test_only_7_to_90_dte_included`, `test_near_term_excluded`, and `test_custom_config_applied` after dsanchor confirmed the 7-90 fetch filter and `_min_dte`/`_max_dte` provider attributes were removed.
- Fetch-time options-chain behavior now only excludes expired contracts; roll-candidate DTE caps remain separate and out of scope for `tests/test_yfinance_data_provider.py`.

### Phase 4a — Provisioning, Dockerfile, README (2026-03-28)
- **Architecture:** CosmosDB single-container, partition by `/symbol`, three doc types: `symbol_config`, `decision`, `signal`
- **Indexing:** Custom policy indexes only query fields (`symbol`, `doc_type`, `timestamp`, `watchlist/*`, `agent_type`, `decision`); excludes large blobs (`reason`, `raw_response`, `analysis_context`)
- **Provisioning:** `scripts/provision_cosmosdb.sh` — idempotent, serverless default, customizable via env vars
- **Migration:** `scripts/migrate_to_cosmosdb.py` — idempotent (catches `CosmosResourceExistsError`), reads from `data/*.txt` + `logs/*.jsonl`, imports `src.cosmos_db.CosmosDBService`
- **Dockerfile:** Removed `data/` and `logs/` volume mounts, added `scripts/` copy — no persistent local storage needed
- **README:** Updated architecture description, env vars table, Docker run examples, added CosmosDB Setup + Migration + Environment Variables sections
- **Key file paths:** `scripts/provision_cosmosdb.sh`, `scripts/migrate_to_cosmosdb.py`, `Dockerfile`, `README.md`
- **Dependency:** Migration script imports `src.cosmos_db.CosmosDBService` (created by Rusty in Phase 1)

### CosmosDB Unified Container Migration (2026-04-01)
- **Migration script:** `scripts/migrate_cosmos_events.py` — 4-phase migration from dual doc_type (activity/alert) to unified is_alert model
- **Phase 1 (Export):** Queries all activities and alerts, writes timestamped JSON backup with integrity validation (count checks)
- **Phase 2 (Transform):** Merges alert docs into parent activities by activity_id, strips dec_/sig_ prefixes, handles orphaned alerts (converts to standalone), resolves duplicate timestamp collisions (appends sequence number)
- **Phase 3 (Write):** Deletes old documents, writes merged unified events to single container, validates write count
- **Phase 4 (Validate):** Count checks (activities + alerts before = events after), spot-checks merged records, verifies no doc_type='alert' or dec_/sig_ IDs remain
- **Script features:** `--dry-run` (phases 1-2 only, reports what would happen), `--restore BACKUP_FILE` (reads backup and restores), progress logging, defensive error handling with clear messages
- **Edge cases handled:** Orphaned alerts (activity_id points to missing activity) → convert to standalone activity with is_alert=true; duplicate timestamps → append _2, _3 sequence; activities already marked is_alert=true → preserve as-is
- **Key file paths:** `scripts/migrate_cosmos_events.py`, `scripts/MIGRATION_RUNBOOK.md`, `backups/*.json` (created on export)
- **Design source:** Danny's `.squad/decisions/inbox/danny-cosmosdb-migration.md` (9-section spec with transformation rules, edge cases, rollback procedure)
- **Testing patterns:** Dry-run first, backup-before-change, restore capability with confirmation, progressive validation, clear error messages with rollback instructions

## Cross-Agent Impact

### Phase 4a Integration with Phases 1–3 (2026-03-28)
- **Rusty (Agent Dev):** Phases 1–3 (service layer, scheduler, web dashboard) provide CosmosDBService API contract
- **Danny (Lead):** Architecture specification (8 sections) fully implemented: Rusty covered phases 1–3, Basher covered phases 4a provisioning/deployment
- **Orchestration log:** See `.squad/orchestration-log/2026-03-28T1350-basher-phase4a.md`

### CosmosDB Migration (2026-04-01)
- **Danny (Lead):** Authored migration design with 4-phase strategy, edge case handling, rollback procedures
- **Basher (Tester):** Implemented migration script per Danny's spec with dry-run, restore, and validation phases
- **Next steps:** Rusty must update `cosmos_db.py`, `agent_runner.py`, `web/app.py` to use new unified model (write_activity with is_alert flag, remove write_alert method, update queries from doc_type='alert' to is_alert=true)

## Orchestration Session (2026-04-01T21:39:57Z)

**Session:** CosmosDB Unified Schema — Decision Consolidation and Team Orchestration

**Status:** Migration script implemented and documented. Ready for dry-run and production execution.

**Team Coordination Update:**
- Danny: Migration design complete with 4-phase strategy, transformation rules, edge case handling
- Rusty: cosmos_db.py implementation complete with backwards compatibility
- Linus: agent_runner.py refactoring complete for unified write path
- Basher (this work): Migration script complete with defensive testing practices

**Pre-Production Execution Checklist:**
1. [Pending] Run `python scripts/migrate_cosmos_events.py --dry-run` against production database
2. [Pending] Review transformation summary for:
   - Unexpected orphaned alerts (should be rare)
   - ID collisions (should be zero)
   - Merge counts align with expectations
3. [Pending] Verify backup file integrity (count matches query results)
4. [Pending] Test `--restore BACKUP_FILE` in non-production environment
5. [Pending] Confirm all validation checks pass (Phase 4)
6. [Pending] Schedule downtime window (2-5 min)
7. [Pending] Execute: Stop app → run migration → validate → restart app
8. [Pending] Smoke test: Trigger one agent run, verify new ID format
9. [Pending] Delete backup after 7 days

**Migration Command Reference:**
```bash
# Dry-run (no database changes, shows transformation summary)
python scripts/migrate_cosmos_events.py --dry-run

# Actual migration (with backup created automatically)
python scripts/migrate_cosmos_events.py

# Rollback if needed (requires explicit 'YES' confirmation)
python scripts/migrate_cosmos_events.py --restore backups/YYYYMMDDTHHMM.json
```

**Session Log:** `.squad/log/2026-04-01T21-39-cosmosdb-unified-schema.md`  
**Orchestration Log:** `.squad/orchestration-log/2026-04-01T21-39-basher.md`

### Anti-403 Test Suite (2026-04-06)
- **Test file:** `tests/test_anti403.py` — 28 tests, all passing
- **Testing patterns:** `unittest.mock` for HTTP mocking (`_mock_response` helper), `pytest-asyncio` for async tests, `_noop_sleep` helper to avoid real delays in tests
- **Key file paths:** `tests/test_anti403.py`, `tests/__init__.py`, `src/tv_data_fetcher.py` (TradingViewFetcher class, `_handle_403`, `_refresh_session`, `_warmup`, `_with_retry`, `fetch_all`)
- **Architecture:** Rusty already landed Phases 1–4 of Danny's anti-403 spec: per-symbol session isolation (no global `has_403`), graduated 403 recovery (`_handle_403` with exponential backoff + session refresh), homepage warmup (`_warmup` gated by `_warmup_enabled`), and `fetch_all` returns `tv_403` key in result dict using local `_has_403` dict
- **Edge case discovered:** `tv_403` flag in `fetch_all` is currently unreachable — `_handle_403` raises `HTTPError` after retries exhausted, but individual fetch methods (e.g., `fetch_overview`) catch all exceptions in their own try/except and return JSON error strings. The `except HTTPError` in `_timed_fetch` (which sets `_has_403["blocked"]`) is dead code. Reported to Rusty.
- **Config properties:** `_max_403_retries`, `_403_retry_delays`, `_warmup_enabled` all passed from `create_fetcher()` via config. Defaults: retries=3, delays=[5,15,45], warmup=False
- **Run command:** `python -m pytest tests/test_anti403.py -v`


### Anti-403 Test Suite — 2026-04-06T14:10Z
**Status:** ✅ Completed  
**Timestamp:** 2026-04-06T14:10Z  
**Test File:** `tests/test_anti403.py` — 28 tests, all passing ✅

**Assignment**
Write comprehensive test suite validating all 4 phases of Rusty's anti-403 implementation, covering session isolation, 403 recovery with exponential backoff, no global state pollution, warmup behavior, symbol randomization, and config loading.

**Test Coverage (28 tests)**

**Session Isolation (6 tests)**
- Per-symbol session creation (fresh requests.Session for each symbol)
- Playwright browser lifecycle per-symbol
- Monitor agents scope fetcher per-symbol, not per-position
- Verify no global `has_403` flag exists
- Session isolation across concurrent symbol fetches
- No session state carries between symbols

**403 Recovery & Exponential Backoff (8 tests)**
- `_handle_403()` retries with backoff: 5s → 15s → 45s
- Between retries: old session closed, fresh headers generated
- Config properties `max_403_retries`, `_403_retry_delays` respected
- After max retries exhausted, HTTPError raised
- `fetch_all()` catches HTTPError and sets `tv_403=True`
- Non-403 transient errors handled separately in `_with_retry()`
- Backoff delays are cumulative (proper exponential timing)
- HTTPError propagates to caller after retries exhausted

**Global State Isolation (4 tests)**
- 403 in one symbol does not taint other symbols
- Result dict per-symbol; no shared `has_403` state
- `tv_403` flag correctly appears in data dict
- Backward compatibility: code checks `data.get("tv_403")`

**Homepage Warm-Up (3 tests)**
- `_warmup()` visits homepage when `warmup_enabled=True`
- Skips warmup when `warmup_enabled=False`
- Warm-up request includes organic headers (User-Agent, etc.)

**Symbol Randomization (4 tests)**
- `random.shuffle()` applied when processing all symbols
- Randomization skipped on single-symbol runs (preserves determinism)
- Config property `tradingview_randomize_symbols` controls behavior
- Randomization does not affect fetch correctness (order-independent)

**Config Loading (3 tests)**
- Config properties loaded from config.yaml
- Defaults applied: retries=3, delays=[5,15,45], warmup=False, randomize=True
- Config merged into `create_fetcher()` calls properly

**Testing Patterns**
- **HTTP Mocking:** `unittest.mock` to inject 403 responses without network
- **Async Testing:** `pytest-asyncio` for async methods (`_handle_403()`, `_warmup()`)
- **Delay Bypassing:** `_noop_sleep` helper to avoid real delays in test suite
- **Defensive State:** Fixtures for isolated test state, mock cleanup

**Run Instructions**
```bash
python -m pytest tests/test_anti403.py -v
```

**Result:** All 28 tests passing ✅

**Edge Case Discovered**
- `tv_403` flag in `fetch_all()` is currently unreachable — `_handle_403()` raises HTTPError after retries exhausted, but individual fetch methods (e.g., `fetch_overview()`) catch all exceptions in their own try/except and return JSON error strings. The `except HTTPError` in `_timed_fetch` (which sets `_has_403["blocked"]`) is dead code.
- Reported to Rusty; non-blocking, can be addressed in next iteration
- Recommendation: Consider whether `tv_403` should be set more granularly (per-fetch-method) or if HTTPError should be propagated to callers

**Quality Metrics**
- ✅ No global state pollution across tests
- ✅ All async operations awaited properly
- ✅ Config loading validated with env var substitution
- ✅ HTTP session refresh verified on 403 retry
- ✅ Exponential backoff delays validated
- ✅ Randomization only applies to full symbol runs
- ✅ Backward compatibility verified
- ✅ 28/28 tests passing

**Related Orchestration**
- `.squad/orchestration-log/2026-04-06T14-10-basher-anti403.md` (task deliverable)
- `.squad/orchestration-log/2026-04-06T14-10-rusty-anti403.md` (Rusty's deliverable)
- `.squad/log/2026-04-06T14-10-anti403-implementation.md` (session summary)
- `.squad/decisions/decisions.md` → "Anti-403 Implementation (4 Phases)"

### Contrarian Panel UI (2026-07-17)
- **Files changed:** `web/templates/activity_detail.html`, `web/static/style.css`, `web/static/app.js`
- **Feature:** Collapsible contrarian perspective panel on activity detail page
- **Placement:** After activity card, before Raw JSON card
- **Behavior:** Panel renders only when `activity.contrarian_view` exists in CosmosDB document. WEAK panels auto-collapse on load; MODERATE/STRONG expand by default. Color-coded badges (green/amber/red) match existing design system.
- **Backend:** No changes needed — `activity_detail_page()` already passes full activity document to template (line 1527 of `web/app.py`)
- **Jinja2 edge cases tested:** missing `contrarian_view` (hidden), empty `counter_arguments` (list hidden), missing `one_liner` (graceful), lowercase `challenge_strength` input (case-insensitive via `|upper`/`|lower` filters)
- **CSS:** Uses existing CSS variables (`--bg-card`, `--border`, `--accent-green`, `--accent-orange`, `--accent-red`, `--radius-card`, `--radius-pill`). Contrarian-specific classes follow existing badge/card patterns.
- **JS:** `toggleContrarian()` function + auto-collapse IIFE for WEAK panels. Added at end of `app.js` alongside existing DOMContentLoaded handlers.
- **Tests:** 6 Jinja2 rendering tests validated all states (no view, WEAK, MODERATE, STRONG, RECONSIDER, empty args, missing one_liner)

### yFinance Migration Phase 1 — Foundation Module Tests (2026-07-18)
- **Test files:** `tests/test_greeks_calculator.py` (29 tests), `tests/test_technicals_calculator.py` (43 tests), `tests/test_yfinance_data_provider.py` (24 tests) — **96 total, all passing ✅**
- **greeks_calculator.py API:** `GreeksCalculator` class, `.compute(flag, S, K, T, sigma)` where flag='c'/'p', `.compute_batch(options)`, `_fetch_risk_free_rate()` (module-level). Risk-free rate fetched lazily via `yf.Ticker("^TNX")` inside function (local import — mock via `@patch("yfinance.Ticker")`).
- **technicals_calculator.py API:** `TechnicalsCalculator` class, `.compute_all(history)` takes only DataFrame (no name/ticker/exchange). Signal functions are module-level: `_oscillator_signal(key, sym_dict)`, `_ma_signal(key, ma_val, close)`, `_tech_recommendation_label(value)`. Output uses dicts (not lists) for indicators, keyed by indicator code (e.g. "RSI", "SMA10"). Recommendation is `{"value": float, "label": str}`.
- **yfinance_data_provider.py API:** `YFinanceDataProvider(fetcher, config)` — takes a `YFinanceFetcher` + config dict. `fetch_all(symbol)` is **async**, returns dict of JSON strings. No public `fetch_options_chain()` etc — uses private `_build_*` methods. `create_provider(config)` factory creates provider with default fetcher.
- **Testing patterns:** `asyncio.get_event_loop().run_until_complete()` for async tests (no pytest-asyncio needed), `@patch("src.yfinance_data_provider.yf")` for yfinance mocking, synthetic OHLCV generators with known trend properties, edge cases for expired/zero-vol options via `_expired_greeks`.
- **Key insight:** `_ma_signal(key, ma_val, close)` — second arg is MA value, third is close price. `close > ma_val` → Buy. Easy to confuse parameter order.
- **Run command:** `python3 -m pytest tests/test_greeks_calculator.py tests/test_technicals_calculator.py tests/test_yfinance_data_provider.py -v`

### Cross-Agent Note: Roll DTE Target and Post-Earnings Block Changes (2026-07-01)

Linus updated roll agent instructions with new DTE targets and post-earnings windows. These changes affect test validation expectations if your test suite checks roll parameters.

**What Changed**
- Roll target: 30-45 DTE → 21-35 DTE primary (45 DTE fallback cap)
- Post-earnings: Hard block 0-13 days → 0-7 days hard block; 8-13 days caution zone; acceptable ≥8 days

**Impact on Testing**
- If test fixtures check `target_dte` or `fallback_dte`, update expectations to match new windows
- If test validation asserts on post-earnings thresholds, update to 0-7 hard block (was 0-13)
- Caution zone (8-13 days) is now part of validation logic

**Files Changed** (by Linus)
- `src/open_call_roll_instructions.py`
- `src/open_put_roll_instructions.py`
- `src/open_call_assessment_instructions.py`
- `src/open_put_assessment_instructions.py`

**Decision Record:** `.squad/decisions/decisions.md` → "Roll DTE Target and Post-Earnings Window Update"

### Web Endpoint Testing Pattern — Activity Chat Tests (2026-07-09)

**Test file:** `tests/test_activity_chat.py` — 13 hermetic tests for `POST /api/activities/{activity_id}/chat` endpoint (web/app.py:2817)

**Pattern established:** TestClient + monkeypatch + FakeCosmos for web endpoint tests with NO network, NO real LLM, NO real Cosmos

**Key components:**

1. **FakeCosmos class**: In-memory fake for CosmosDBService
   - Implements `get_activity_by_id(id)`, `get_symbol(symbol)`
   - Provides `.container` property with `query_items(query, parameters, partition_key)` method
   - Stores activities, symbols, and technical_docs dicts in memory

2. **FakeAgent class**: Captures LLM messages for contract assertions
   - Uses module-level `captured_messages` list to store messages across test invocations
   - Returns object with `.text = "MOCK ANSWER"` attribute
   - Enables assertions on message content (section headers, data inclusion)

3. **FakeConfig class**: Stubs config properties
   - Must include `activity_chat_model`, `model_deployment` (for AgentRunner fallback), and `llm_config()` method
   - Returns minimal valid values

4. **FakeOptionsChainCache class**: Returns JSON chain data or raises on demand
   - `get_or_load(symbol)` returns JSON string
   - `should_raise` flag for degradation testing

5. **test_app fixture**: Sets up TestClient with all mocks
   - **Critical**: Disable startup event with `app.router.on_startup = []` to prevent CosmosDB initialization
   - Set `app.state.cosmos` and `app.state.yf_provider` BEFORE creating TestClient
   - Use `TestClient(app, raise_server_exceptions=False)` to capture 4xx/5xx responses
   - Monkeypatch late-imported symbols: `agent_framework.Agent`, `src.llm.create_async_chat_client`, `src.config.Config`, `src.options_chain_cache.get_options_chain_cache`

**Testing patterns:**

- **Contract tests**: Assert captured LLM message contains all 5 required section headers, activity JSON, chain data, conversation history
- **Degradation tests**: Chain unavailable, technical analysis unavailable, missing position — all gracefully handled with 200 responses
- **Read-only validation**: Attach fake write methods to cosmos and assert they're never called
- **Edge cases**: Empty/blank message (400), unknown activity (404), missing position_id (graceful), history formatting

**Run command:**
```bash
source .venv/bin/activate 2>/dev/null
python3 -m pytest tests/test_activity_chat.py -q
```

**Key insight**: Late imports inside endpoint functions (e.g., `from agent_framework import Agent` inside `api_activity_chat`) require monkeypatching the SOURCE module attribute, NOT the function's local namespace. Use `monkeypatch.setattr("agent_framework.Agent", FakeAgent)` BEFORE the endpoint executes.

**Files changed:** `tests/test_activity_chat.py` (new file, 415 lines)

**Related endpoint:** `POST /api/activities/{activity_id}/chat` in `web/app.py:2817`

### Cross-Agent Note: Roll Candidate Ranking by Ann.Ret% (2026-07-01)

Linus changed roll candidate table sorting from **Net Credit descending** → **Ann.Ret% (annualized return) descending**. This affects test expectations for candidate ordering if your test suite validates roll candidate rankings.

**What Changed**
- Sort key: `net_credit` (longer DTE bias) → `ann_ret` (time-normalized return)
- Rationale: Ann.Ret% = Premium% × 365 / DTE normalizes premium by time, surfacing best return/day and aligning with 21-35 DTE target
- Net Credit column remains available for economics/threshold checks

**Impact on Testing**
- If test assertions check roll candidate table order or validate specific candidate rankings, update expectations
- Longer-dated contracts will rank lower now (lower annualized return per day)
- Shorter-DTE contracts with higher premium% will rank higher

**Pre-Existing Test Failures** (unrelated to this change)
- `test_economics`: contract-multiplier bug in test (premium × 100 discrepancy)
- `yfinance DTE-window filter test`: API window handling issue

**Files Changed** (by Linus)
- `src/options_chain_filters.py` (sort key + table label)
- `src/open_call_roll_instructions.py` (prose "sorted by Net Credit" → "sorted by Ann.Ret%")
- `src/open_put_roll_instructions.py` (prose "sorted by Net Credit" → "sorted by Ann.Ret%")

**Decision Record:** `.squad/decisions/decisions.md` → "Sort Roll Candidates by Ann.Ret%"
