# Rusty — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Stack:** Python, Microsoft Agent Framework, Azure Foundry (gpt-5.1)
- **MCP:** iflow-mcp_ferdousbhai_investor-agent 1.6.3
- **Description:** Two periodic trading agents for covered call and cash-secured put sell signals. Local runtime, configurable polling, stock symbols from files, decision logs, sell signal alerts.

## Core Context

**Consolidated work items from March–July 2026:**

**Phase 1–4a (March 2026):** CosmosDB foundation, scheduler refactor, web dashboard migration, REST API (symbol detail, position management)

**TradingView Data Layer (April 2026):** Pre-fetch architecture with Playwright for options, BS4+scanner API for overview/technicals/forecast/dividends (5 test scripts + tv_data_fetcher.py refactor)

**Agent Infrastructure (April–June 2026):** Telemetry, telegram notifications per-symbol, settings container, manual roll endpoint, context overflow handling, unified activities list, DPS snapshotting, PRN scheduler, anti-403 architecture

**Critical fixes & patterns:**
- Dict-spread protection (March 2026): Reassert `doc["timestamp"]` after `**spread` in write_activity/alert
- Config precedence (March 2026): Merge CosmosDB settings into Config at runtime
- Per-symbol notification toggles (March 2026): `telegram_notifications_enabled` field
- Market hours detection (May 2026): Live MSFT ATM call probe vs calendar rules; 5min cache, conservative fallback
- Options chain merge (May 2026): In-memory cache during market open; TV fallback merges with cache, preserving longer-dated expirations
- P&L robust mid calculation (June 2026): Handle illiquid one-sided quotes via `robust_mid(bid, ask)` helper
- Scheduler reliability (June 2026): Per-symbol timeout + worker max-duration guard to prevent hang cascades

**April–June tasks:** 40+ work items including quick analysis chat, TradingView widgets, DGI screener, contrarian agent, multi-provider MCP, phase 2 pipeline swap, comprehensive README updates. All documented in `/decisions.md` decision records.

## Recent Tasks

### July 2026 — Portfolio Chat Configuration Context (2026-07-14)
**Status:** ✅ Completed  
**Scope:** Intermediate configuration screen for Portfolio Chat with agent selection and activity limits

**Changes:**
- `web/templates/chat.html` — added intermediate Portfolio Chat configuration form with 5 agent checkboxes and activities limit input (default: 3)
- `web/app.py` — rewrote `mode == "portfolio"` context builder to accept `selected_agents` (list) and `activities_limit` (int)

**Context Contract:**
- Portfolio chat requests may include `selected_agents` and `activities_limit`
- Missing/empty `selected_agents` falls back to all `AGENT_TYPES` for backward compatibility
- `activities_limit` clamped server-side to 1..50
- Position monitors (`open_call_monitor`, `open_put_monitor`) seed context from active positions
- Following agents (`covered_call`, `cash_secured_put`, `buy_tracker`) seed context from watchlist membership
- Each row always includes open position/watchlist symbol plus up to N recent activities/alerts

**Validation:** `pytest tests/ -k chat` → 13 passed

**Decision Record:** `.squad/decisions/decisions.md` → "Portfolio Chat Context Contract"

### July 2026 — DAL Leak Refactoring (2026-07-09)
**Status:** ✅ Completed  
**Scope:** Migrate 5 direct Cosmos calls to data-access layer methods

**Changes:**
- Added 3 new CosmosDBService methods: `replace_symbol()`, `get_symbol_activities()`, `get_latest_technical_analysis()`
- Migrated 5 leak sites in `web/app.py` (watchlist update, activity accept, roll, activities list, activity-chat technicals)
- Updated FakeCosmos test double with new method stubs

**Validation:** AST parse OK; `pytest tests/test_activity_chat.py -q` → 13 passed; full suite 141 passed

### July 2026 — DPS Insights Endpoint (2026-07-09)
**Status:** ✅ Completed  
**Scope:** LLM narrative summary of position DPS health (historical snapshots only)

**Context:** Position + snapshot history (no live fetches, no technicals, no run_dps_analysis)
**Design:** One-shot feature (no history); uses exact section headers as contract with Linus's instruction module
**Model:** Configurable via `Config.dps_insights_model` (default: `gpt-5.4-mini`)

**Changes:**
- `web/app.py` line ~1286: Added `POST /api/symbols/{symbol}/positions/{position_id}/dps-insights`
- `src/config.py` line ~261: Added `dps_insights_model` property
- `web/templates/symbol_detail.html`: Added Insights button, result div, JS handler

**Validation:** Python AST parse OK

**Key Learnings:** Reused activity chat endpoint pattern for consistency. Position + snapshots only (lightweight design). Safe text rendering via `textContent` (avoid XSS).

### July 2026 — Activity Chat Endpoint (2026-07-09)
**Status:** ✅ Completed  
**Scope:** Read-only LLM advisory for specific agent decision analysis

**Context:** LIVE-fetch design — agent decision (historical) vs current market data (live chain + technical analysis)
**Design:** Multi-turn history kept in-memory on frontend; server does NOT persist chat history
**Model:** Configurable via `Config.activity_chat_model` (default: `gpt-5.4-mini`)

**Changes:**
- `web/app.py` line ~2815: Added `POST /api/activities/{activity_id}/chat` endpoint
- `src/config.py` line ~256: Added `activity_chat_model` property
- `web/templates/activity_detail.html`: Added Chat button, panel, JS handler

**Validation:** Python AST parse OK

**Key Learnings:** LIVE-fetch allows user to compare historical agent decision vs fresh market data. Exact section headers are contract with Linus's instructions. Ephemeral history (resets on reload) keeps design stateless.

### July 2026 — Scheduler Reliability Fixes (2026-06-30)
**Status:** ✅ Completed  
**Scope:** Prevent scheduler hang cascades via per-symbol timeout + worker max-duration guards

**Root Cause:** One hung options_chain job (yfinance stall) blocked entire single-threaded worker queue forever.

**Fix:**
- `src/options_chain_cache.py`: Rewrote `refresh_all()` with `ThreadPoolExecutor(max_workers=4)` + 90s per-symbol timeout
- `scheduler_registry.py`: Added worker max-duration check (prevents hung job from jamming queue)
- Fixed `web/app.py` line 2904 & 2954: `cosmos.get_all_symbols()` → `cosmos.list_symbols()`

**Validation:** ✅ No more "Skipped (still running)" cascades; scheduler advances consistently

**Key Learnings:** Bounded concurrency (4 workers) with hard timeout per symbol. Timeout → log warning → continue next symbol (don't abort). Prevents one source of latency from cascading to entire system.

### Earlier July Tasks
- Eligible Dividend Tracking (2026-07-08): Per-symbol ex-dividend awareness for CSP SELL timing
- Calendar Active-Per-Date (2026-07-08): Events calendar per-event-date position exposure logic
- Manual Close Buyback Cost (2026-07-02): Optional buyback cost tracking on position close
- Dead DTE Config Removal (2026-07-01): Removed stale `yfinance.options_chain` DTE window config
- Scheduler Toggle Persistence (2026-07-03): Fixed settings checkbox revert-on-reload via registry update

### April–June Tasks (40+ items)
Consolidated into `.squad/decisions/decisions.md` decision records including: Options Chain Scheduled Caching, Quick Analysis Chat Conversationalization, TradingView Data Layer, Anti-403 Architecture, DGI Screener, Contrarian Agent, Multi-Provider MCP, Phase 2 Pipeline Swap (TradingView → yfinance), Comprehensive README Updates.


### Unified Schema Query Pattern (2026-04-01)
Activities and alerts live in the same container with `doc_type='activity'`. Discriminate with `is_alert` boolean:
- **Alerts:** `WHERE c.doc_type = 'activity' AND c.is_alert = true`
- **Activities (excluding alerts):** `WHERE c.doc_type = 'activity' AND (c.is_alert = false OR NOT IS_DEFINED(c.is_alert))`

ID format: `{symbol}_{agent_type}[_{position_id}]_{ts_compact}` — no prefixes.

### Dict-Spread Protection Pattern
When using `**spread` in Python dict literals, reassert ALL routing/identity fields after spread (id, timestamp, doc_type, symbol, agent_type). LLM-generated dicts can contain arbitrary keys that silently overwrite critical fields. The `doc_type` field especially critical since it's used in every WHERE clause for document classification.

### Symbol Detail Page Layout
Alerts card appears BEFORE activities card in `web/templates/symbol_detail.html`. User preference: alerts are higher priority and should be seen first.

### Lazy Initialization of Expensive Resources
Playwright + Chromium are expensive. Initialize lazily via helper method (`_ensure_browser()`) rather than in `__init__`. Saves resources when only lightweight fetchers (BS4) run.

### Multi-Strategy Data Extraction
Implement 3-level fallback: (1) targeted HTML extraction, (2) embedded JSON parsing, (3) API fallback. Each strategy provides value-add error handling and graceful degradation.

### TradingView Scanner API for Validation
The unauthenticated `/america/scan` endpoint provides fundamentals, technicals, forecast, and dividends data without browser context. Returns "Unknown field" for invalid columns.

### Position Enrichment from Activities
When displaying open positions, enrich with data from latest monitor activity (assignment_risk, moneyness). Pattern: scan activities for monitor agents, build `position_id → latest activity` lookup, attach computed fields with `_` prefix (e.g., `_assignment_risk`, `_moneyness`) to avoid polluting persisted document.

### Settings Data Source Pattern (2026-07)
Any web route displaying user-configurable settings MUST read from CosmosDB first, falling back to `config.yaml` only if unavailable. Pattern: `cosmos_settings = _load_settings_from_cosmos(cosmos); config = cosmos_settings if cosmos_settings else _load_config()`. Only use `_load_config()` directly for connection credentials.

### Source Attach vs Pre-fill Pattern (2026-07)
Two distinct UX patterns for alert→position:
1. **From-activity route:** Full automation — creates position, disables watchlist, cascade-deletes activities/alerts
2. **Manual add with attach:** User fills fields manually; alert source metadata transparently attached. No side effects.

### Run Analysis Button on Symbol Detail
The positions card on symbol detail has "▶ Run Analysis" button that triggers open_call_monitor and/or open_put_monitor agents depending on active position types. Button only renders when active positions exist. Reuses `/api/trigger/{agent_type}` endpoint.

### Earnings Gate Schema (2026-07-09)
Mandatory earnings gate across all 4 instruction files. All agent responses now include `earnings_analysis` JSON object as first analytical step. Non-breaking addition (new field, existing fields unchanged).

### Summary Agent Categorization (2026-07-09)
Updated summary agent to organize daily reports into four sections: Current Calls, Current Puts, Watchlist Calls, Watchlist Puts. Empty sections show "No X" messages.

### Alert Link Bug Fix (2026-04-02)
**Issue:** Symbol detail page alert links generated 404s while activity links worked. Dashboard links worked for both.
**Root cause:** Alert row template used non-existent field `alt.activity_id` instead of `alt.id`.
**Fix:** Changed alert template from `data-href="/activities/{{ alt.activity_id }}"` to `data-href="/activities/{{ alt.id }}"` to match activities and dashboard patterns.
**Pattern:** Both activities and alerts are documents with an `id` field. Always use `{item}.id` for activity detail links, never invent intermediate field names.

### Dashboard Position DTE Sorting (2026-04-02)
**User preference:** Open calls and puts on dashboard should be ordered by DTE (days to expiration) in ascending order.
**Implementation:** Added sort in `_build_dashboard_tables()` after building rows for position monitors. Positions with lower DTE (expiring sooner) appear first.
**Location:** `web/app.py` line 797-799
**Sort key:** `lambda r: (r.get("dte") is None, r.get("dte") or 0)` — handles None values by pushing them to the end.
**Pattern:** Position monitor DTE is already populated from latest activity data. Sort is applied only for position monitor agents (open_call_monitor, open_put_monitor), not watchlist agents.

---

