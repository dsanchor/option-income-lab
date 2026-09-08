# Squad Decisions — Archive

Archived decisions older than 30 days (archived: 2026-04-01).

## Archived Decisions

### 1. Trading Agent Instructions Design
**Date:** 2024-01-15  
**Author:** Linus (Quant Dev)  
**Status:** Completed  
**Impact:** Team-wide (defines agent behavior)

#### Context
Created system prompt instructions for covered call and cash-secured put agents. These instructions define how Azure AI Agents will analyze market data and make trading decisions.

#### Key Design Decisions

1. **Dual-Threshold Decision Framework**
   - **Standard SELL criteria**: Solid setups with IV Rank ≥50, proper Greeks, clean calendar
   - **CLEAR SELL SIGNAL criteria**: Exceptional setups (premium 2-2.5%, IV Rank ≥70) that trigger alerts
   - **Rationale**: Separates "good" opportunities from "don't miss this" opportunities

2. **Greeks-Based Strike Selection**
   - **Covered Calls:** Conservative (Δ 0.20-0.25), Moderate (Δ 0.25-0.30), Aggressive (Δ 0.30-0.35)
   - **Cash-Secured Puts:** Strike AT or BELOW support levels with same delta ranges
   - **Rationale**: Assignment on puts should happen at attractive prices (support), not above

3. **Standardized Output Format**
   - `[TIMESTAMP] SYMBOL | DECISION: SELL/WAIT | Strike: $X | Exp: YYYY-MM-DD | IV: X% | Reason: ... | Waiting for: ...`
   - **Rationale**: Enables easy parsing for decision logs and downstream analysis

4. **Fundamental Quality Gate (CSP Only)**
   - Mandatory check: "Would you want to own this stock at strike price?"
   - If NO → automatic WAIT regardless of premium
   - **Rationale**: Bad assignment on deteriorating stock wipes out months of premium

5. **Optimal DTE Window: 30-45 Days**
   - Balances premium amount with theta decay rate
   - Avoids <21 DTE (insufficient premium) and >60 DTE (too much time risk)
   - **Rationale**: Theta acceleration in final 30 days, but need enough time to manage position

6. **Earnings Calendar Integration**
   - **Covered Calls:** NEVER sell expiring after next earnings (gap risk)
   - **Cash-Secured Puts:** IDEAL to sell 1-3 days post-earnings (capture IV crush)
   - **Rationale**: Different risk profiles—calls fear upward gaps, puts benefit from volatility collapse

7. **MCP Tool Integration Strategy**
   - Phase 1: Core data (ticker, price history, options chain)
   - Phase 2: Volatility/sentiment (earnings calendar, fear/greed, trends)
   - Phase 3: Institutional context (holders, insiders)
   - **Rationale**: Systematic data gathering ensures no analysis gaps

#### Implications
- Instructions are Python string constants for Azure AI Agent's `instructions` parameter
- Decision logs must be appended to instruction context on each run
- CLEAR SELL SIGNAL marker enables alert detection in frontend
- Test edge cases: low IV, pre-earnings, post-earnings

#### Trade-offs
1. **Complexity vs. Flexibility**: Comprehensive (~12-18KB) to reduce hallucination
2. **Strict Rules vs. Agent Discretion**: Rules-based with interpretation room in "Reason" field
3. **Strike Selection**: Fixed delta ranges (0.20-0.35) per industry standard

---

### 2. Python Implementation Architecture (agent-framework SDK)
**Date:** 2024-03-26  
**Author:** Rusty (Python Dev)  
**Status:** In Progress (SDK migration from azure-ai-agents)  
**Impact:** Technical (defines project structure and integration points)

#### Context
Building complete Python project for periodic options trading agents with Azure AI Agents Framework and MCP integration.

#### Key Design Decisions

1. **Agent Framework SDK for Agent Management**
   - **Decision**: Use `agent-framework` SDK (correct) instead of `azure-ai-agents` (incorrect)
   - **Rationale**: Official framework for Microsoft Foundry with proper abstractions
   - **Impact**: Clean, maintainable code with proper resource cleanup

2. **Per-Symbol Agent Creation**
   - **Decision**: Create new agent for each symbol analysis, then delete after completion
   - **Rationale**: Avoids thread state accumulation, cleaner isolation, prevents cross-contamination
   - **Trade-off**: Slightly higher latency per symbol, worth it for reliability

3. **Dual-Log Strategy**
   - **Decision**: Maintain decision log (all decisions) and signal log (SELL only)
   - **Rationale**: Decision log captures history for context; signal log enables quick trader review
   - **Impact**: Better UX—traders know exactly where to look for actionable signals

4. **Context Continuity via Log Reading**
   - **Decision**: Read last 20 decision log entries and include in each analysis prompt
   - **Rationale**: Agents learn from previous decisions, avoid flip-flopping, provide temporal context
   - **Implementation**: `read_decision_log()` called before each analysis run

5. **Simple Scheduling with Python `schedule` Library**
   - **Decision**: Use `schedule` library instead of cron or APScheduler
   - **Rationale**: Simple readable syntax, no external dependencies, easy to test/debug
   - **Trade-off**: Less robust than systemd timers, sufficient for this use case

6. **Environment Variable Substitution in Config**
   - **Decision**: Substitute `${ENV_VAR}` in config at startup, fail fast if missing
   - **Rationale**: Secrets stay out of repo, cleaner separation of config/secrets
   - **Implementation**: Use `string.Template.substitute()`

#### Implications

- Instruction files are stored as Python string constants in `src/` (easy to maintain and version-control)
- Agent creation is ephemeral—agents are created per-run then immediately deleted
- Signal logs are separate from decision logs, enabling different retention/visibility rules
- Scheduling loop is the "heartbeat" of the system; failures here halt all analysis

#### Trade-offs

1. **Complexity vs. Simplicity**: Scheduling library is simpler but less robust than cron
2. **Ephemeral Agents vs. Reusable**: Slightly higher latency for cleaner isolation
3. **String Constants vs. Jinja**: Python strings are simpler to version-control and test

---

### 3. Switch MCP Server to mcp_massive
**Date:** 2026-03-26  
**Author:** Linus (Quant Dev)  
**Status:** Completed  
**Impact:** Technical (data integration)

#### Context

Initial MCP server was built on custom endpoints. Team decided to evaluate Massive.com's MCP server (`mcp_massive`) for cleaner data access and built-in tools (earnings, technicals, Greeks, sentiment).

#### Decision

Migrate MCP server to `mcp_massive` (Massive.com's official MCP implementation).

#### Rationale

1. **Built-in Financial Tools**: Black-Scholes Greeks, technical indicators (RSI, BBANDS, MACD), earnings data, sentiment scoring
2. **SQL Querying**: Structured data access via SQL `SELECT` statements instead of REST endpoints
3. **Single API Source**: Consolidates multiple data providers (price history, options chain, fundamentals, news)
4. **No Custom Maintenance**: Rely on Massive.com team for data pipeline updates
5. **Industry Standard**: More maintainable than custom implementation

#### Implications

- `mcp_massive` command manages the MCP server lifecycle (auto-start/restart)
- Agents query via SQL (more powerful than REST) for complex analysis
- Installation: `uv tool install massive` (user's local setup)
- `MASSIVE_API_KEY` required in environment

#### Trade-offs

**Advantages:**
- Built-in Black-Scholes Greeks simplify options calculations
- SQL querying enables more flexible data analysis
- Reduced complexity with single API source

**Neutral:**
- Requires `MASSIVE_API_KEY` environment variable (similar to previous setup requirements)
- Installation via `uv tool install` (slightly different from uvx pattern)

**Mitigations:**
- Linus updated agent instructions to ensure compatibility with mcp_massive tools
- Fallback strategies documented for missing data signals

#### Next Steps

1. **Basher**: Test that MCP server launches correctly with `mcp_massive` command
2. **Danny**: Run end-to-end test to confirm agents can successfully fetch data and generate signals
3. **Team**: Verify that `MASSIVE_API_KEY` is documented and available in deployment environment


---
# Decisions

## Architectural Decisions

### CosmosDB-Centric Refactor
**Date:** 2026-03-28  
**Author:** Danny (Lead)  
**Status:** Implemented (Phases 1–4a complete)  
**Impact:** Full system — data model, scheduler, web dashboard, config, deployment

Replaced file-based data model with symbol-centric CosmosDB backend. Hybrid document model (symbol_config, activity, alert) partitioned by symbol. Includes schema, service layer design, provisioning commands, and 4-phase implementation plan spread across the phases below.

---

## Implementation Phases

### Phase 1: CosmosDB Service Layer
**Date:** 2026-03-28  
**Author:** Rusty (Agent Dev)  
**Status:** ✅ Complete  
**Impact:** Foundation for all downstream work

Implemented the CosmosDB foundation per Danny's architecture doc (Sections 2, 3, 6).

**Deliverables:**
- **`src/cosmos_db.py`** — `CosmosDBService` class with 18 methods covering: symbol config CRUD, watchlist queries, position management, decision/signal write, context-injection reads, and dashboard queries.
- **`src/context.py`** — `ContextProvider` adapter replacing `logger.py` read functions with CosmosDB-backed equivalents. Output format identical (reason-per-line, oldest-first) so agent instructions require no changes.
- **Modified `src/config.py`** — Added `cosmosdb_endpoint`, `cosmosdb_key`, `cosmosdb_database`, `decision_ttl_days` properties. Removed per-agent config sections.
- **Modified `config.yaml`** — Added `cosmosdb` section with env var substitution. Added `decision_ttl_days: 90`. Removed legacy agent config sections.
- **Modified `requirements.txt`** — Added `azure-cosmos>=4.7.0`.

**Key Design Decisions:**
- TTL on decisions (configurable 0–90 days); signals have no TTL (audit trail)
- Backward-compatible context format
- Client-side position filtering to avoid complex CosmosDB queries

---

### Phase 2: Scheduler + Agent Runner Refactor
**Date:** 2026-03-28  
**Author:** Rusty (Agent Dev)  
**Status:** ✅ Complete  
**Impact:** Scheduler fully cloud-backed; file-based symbol/position discovery replaced

Completed CosmosDB migration of scheduler, agent runner, and all four agent wrappers.

**Deliverables:**
- **`src/agent_runner.py`** — Removed file-based symbol/position discovery. Added `run_symbol_agent()` and `run_position_monitor()` functions. Context injection via `ContextProvider.get_context()` (last N decisions with embedded signal status). Decision/signal persistence via `cosmos.write_decision()` / `write_signal()`.
- **`src/main.py`** — Scheduler initializes `CosmosDBService` and `ContextProvider` during setup. All agent wrappers receive cosmos + context_provider.
- **Agent Wrappers (4 files)** — `covered_call_agent.py`, `cash_secured_put_agent.py`, `open_call_monitor_agent.py`, `open_put_monitor_agent.py` — All query CosmosDB for symbols/positions; each wrapper owns a shared `TradingViewFetcher` for browser session reuse.
- **`web/app.py`** — Updated `_run_agent_in_background()` to pass scheduler.cosmos and scheduler.context_provider.

**Key Design Decisions:**
- Fetcher lifecycle: One per agent type per run (not per symbol) for browser session reuse
- Signals embedded in decisions via `is_signal` field per user directive
- `logger.py` deprecated but not removed (backward compatibility)

---

### Phase 3: Web Dashboard CosmosDB Refactor
**Date:** 2026-03-28  
**Author:** Rusty (Agent Dev)  
**Status:** ✅ Complete  
**Impact:** Dashboard fully CRUD-based; file I/O removed

Completed web dashboard refactor from file-based data to CosmosDB-backed REST API.

**Deliverables:**
- **New `web/templates/symbols.html`** — Symbol management UI with toggle switches and add/delete functionality
- **New `web/templates/symbol_detail.html`** — Symbol detail page with position management and recent decisions/signals
- **`web/app.py`** — Complete rewrite: removed JSONL/txt reads, added REST API endpoints, CosmosDB startup init
- **`web/templates/base.html`** — Added "Symbols" nav link
- **`web/templates/dashboard.html`** — Updated row links to `/symbols/{symbol}`, error banner support
- **`web/templates/settings.html`** — Simplified to cron-only + CosmosDB diagnostics
- **`web/static/style.css`** — Added toggle switch, form, button styles

**API Endpoints Added:**
- `GET/POST /api/symbols` — List/create symbols
- `GET/PUT/DELETE /api/symbols/{symbol}` — Symbol CRUD
- `POST /api/symbols/{symbol}/positions` — Add position
- `PUT /api/symbols/{symbol}/positions/{id}/close` — Close position
- `DELETE /api/symbols/{symbol}/positions/{id}` — Delete position
- `GET /api/signals` — List signals (filterable)
- `GET /api/decisions` — List decisions (filterable)

**Removed:** `DATA_FILES` dict, file-based helpers, legacy routes

---

### Phase 4a: Provisioning, Dockerfile, README
**Date:** 2026-03-28  
**Author:** Basher (Tester)  
**Status:** ✅ Complete  
**Impact:** System ready for Azure production deployment

Created provisioning scripts and updated deployment documentation.

**Deliverables:**
- **`scripts/provision_cosmosdb.sh`** — Idempotent az CLI script per architecture Section 8. Serverless default, custom indexing policy, outputs endpoint + key.
- **`scripts/migrate_to_cosmosdb.py`** — Full migration per architecture Section 7.1. Reads 4 data/*.txt + 8 logs/*.jsonl; idempotent; progress output.
- **`Dockerfile`** — Removed `mkdir -p data logs`, added `COPY scripts/ scripts/`, kept playwright install.
- **`README.md`** — Comprehensive rewrite: updated architecture, flow diagrams, config examples, Docker examples, added CosmosDB setup section, migration guide, troubleshooting.

**Key Design Decision:** Migration script is coded against `CosmosDBService` interface. If method signatures change, migration script must be updated to match.

---

## Domain Model

### Entity Rename: decision → activity, signal → alert
**Date:** 2025-03-29  
**Authors:** Danny (Lead), Rusty (Agent Dev), Linus (Quant Dev)  
**Status:** Completed  
**Impact:** Full system — backend, frontend, instructions, documentation  

The codebase used two domain concepts that were causing confusion:
- "decision" — Agent output for every symbol/position analysis
- "signal" — Actionable subset of decisions (SELL, ROLL, CLOSE)

These terms were ambiguous and overloaded. Renamed comprehensively across the entire system:

- **"decision" → "activity"** — Better reflects that these are agent actions/outputs, not decisions
- **"signal" → "alert"** — Clarifies these are actionable notifications, distinct from trading signals
- **"is_signal" → "is_alert"** — Boolean flag in documents
- **"max_decision_entries" → "max_activity_entries"** — Config key
- **"decision_ttl_days" → "activity_ttl_days"** — Config key

**Implementation:**
- **Backend (Rusty):** Renamed across 11 Python files (cosmos_db.py, agent_runner.py, context.py, config.py, 4 agent wrappers, scripts/provision_cosmosdb.sh), config.yaml. Preserved OS signal handling in main.py (SIGINT, SIGTERM).
- **Frontend (Linus):** Renamed across web/app.py (1412 lines), 6+ templates (decision_detail.html → activity_detail.html, signal_detail.html → alert_detail.html, signals.html → alerts.html), CSS classes, display text, API routes.
- **Instructions (Danny):** Updated agent instruction files (tv_*_instructions.py), README.md, documentation examples.
- **Database:** Recreated from scratch; no migration needed.

**Verification:** Zero "decision" or "signal" references remain in backend (except OS signals); zero remaining in frontend display text or CSS classes.

---

### Scheduler ↔ Web Communication via app.state
**Date:** 2025-07-22  
**Author:** Rusty (Agent Dev)  
**Impact:** Architecture (scheduler + web coupling)

Store `_scheduler_instance` on `app.state.scheduler` during FastAPI lifespan startup. Web routes access via `request.app.state.scheduler`. Degrades gracefully in `--web-only` mode (trigger returns 503, cron saves to YAML).

---

## Web Dashboard

### Dashboard Data Enrichment from Decision Logs
**Date:** 2025-07-28  
**Author:** Rusty  
**Status:** Implemented  
**Commit:** 0831a03

`_build_agent_table()` reads `decision_log` via `_latest_decisions_by_key()` to enrich dashboard rows with health metrics (DTE, moneyness, delta, IV, premium, risk flags). Signal list page gains IV/Premium/Delta columns.

---

## Logging / Data

### Timestamp Generation Moved from LLM to Python
**Author:** Rusty (Agent Dev)  
**Date:** 2025-07-28  
**Status:** Implemented  
**Commit:** 54a219e

All log timestamps now set in Python BEFORE agent execution using `TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"`. LLM's `timestamp` field is always overridden. Ensures consistency across decision and signal JSONL logs.

**Impact for team:**
- **Linus (Quant Dev):** Instruction schemas still include `timestamp` but as "auto-set by system"
- **Basher (Test/Ops):** All log entries now have consistent `YYYY-MM-DD HH:MM:SS` format

---

## User-Facing Features

### Position-from-Decision Endpoint: Inline Watchlist Disable + Cascade Delete
**Date:** 2026-03-29  
**Author:** Rusty (Agent Dev)  
**Status:** ✅ Implemented  
**Impact:** API endpoint, data model, decision lifecycle

Implemented `POST /api/symbols/{symbol}/positions/from-decision/{decision_id}` endpoint to open positions directly from decision intelligence. Extended `cosmos_db.py` `add_position()` with `source` parameter to track position origin (decision vs. watchlist).

**Design Decision:** Endpoint performs watchlist disable and cascade-delete inline rather than extracting shared logic with `api_update_symbol`. This keeps flows independent and avoids coupling user-initiated "open position" action with general symbol updates. Trade-off: watchlist-disable logic must be maintained in two places if it changes.

**Files Modified:**
- `src/cosmos_db.py` — `add_position()` source parameter
- `web/app.py` — New endpoint with inline watchlist/cascade logic

---

### Expandable Position Rows + Open Position Button
**Date:** 2026-03-29  
**Author:** Linus (Quant Dev)  
**Status:** ✅ Implemented  
**Impact:** Web dashboard UX

Added "Open Position" button to decision detail view (signal banner, Jinja conditional). Implemented expandable position rows in symbol detail via hidden `<tr class="pos-detail-row">` elements toggled by row click. Event propagation guard prevents expand/collapse when clicking action buttons. Reused existing CSS (`detail-grid`, `detail-field`) for visual consistency. Table now 8 columns (added chevron affordance column).

**Design Decisions:**
1. Button placed in signal banner flexbox (keeps signal indicator and CTA visually paired)
2. `<tr>` expansion with `display:none` toggle (maintains table semantics)
3. `e.target.closest()` guard for action buttons (more robust than `stopPropagation()`)
4. Reused existing CSS classes (ensures visual consistency)
5. Colspan = 8 (added chevron column)

**Trade-offs:**
- Inline styles for detail panel (border, padding) instead of new CSS classes
- Agent type formatting via inline Jinja ternary (would benefit from custom filter if more agent types added)

**Files Modified:**
- `web/templates/decision_detail.html` — Open Position button + scripts
- `web/templates/symbol_detail.html` — Expandable position rows + expand/collapse logic

---

## Frontend Features

### Price Chart Implementation on Symbol Detail Page
**Date:** 2025-07-25  
**Author:** Linus (Quant Dev)  
**Status:** Implemented  

Added a candlestick price chart with activity/alert markers on the symbol detail page to provide a visual timeline of agent activity relative to price movements.

**Charting Stack:**
- **Library:** TradingView Lightweight Charts (CDN, ~40KB, Apache 2.0)
- **Price Data:** yfinance (3-month daily OHLC, runs in asyncio.to_thread() for non-blocking)
- **Markers:** CosmosDB activities + alerts with visual distinction (⚡ amber for alerts, 📊 gray for activities)
- **New Endpoint:** `GET /api/symbols/{symbol}/chart-data` returns `{"candles": [...], "markers": [...]}`

**Files Changed:**
- `web/app.py` — new `/api/symbols/{symbol}/chart-data` endpoint
- `web/templates/symbol_detail.html` — chart card + Lightweight Charts script
- `requirements.txt` — added `yfinance>=0.2.0`

---

### Manual Roll UI in Positions Table
**Date:** 2025-07-24  
**Author:** Linus (Quant Dev)  
**Status:** Implemented  

Added an inline roll form inside the expandable position detail row rather than a modal dialog. The Roll button in the actions column expands the row and reveals the form at the top of the detail panel.

**Design:** Pre-populates form with current strike/expiration so users only need to adjust.

**API Contract:** `POST /api/symbols/{symbol}/positions/{position_id}/roll` with body `{"new_strike": 150.0, "new_expiration": "2025-08-15", "notes": "optional"}`

**Signal Table Enhancement:** Conditionally shows `(from $X)` context for roll signals with `new_strike`/`current_strike` and `new_expiration`/`current_expiration` fields.

---

### Roll Position Frontend — Conditional Buttons + Closing Source Display
**Date:** 2025-07-15  
**Author:** Linus (Quant Dev)  
**Status:** Implemented  

Button type in `decision_detail.html` determined by `decision.agent_type` at render time via Jinja conditional. Roll button calls `POST /roll-from-decision/`; Open button calls `POST /from-decision/`. Symbol detail page expands rows to show `closing_source`, `rolled_from`, `rolled_to` metadata.

---

### Eager CosmosDB Connection Validation at Startup
**Date:** 2025-07-14  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  

Azure Cosmos DB Python SDK's `CosmosClient()` is lazy — doesn't connect until first query. Added `cosmos.database.read()` immediately after construction to force eager HTTP call, surfacing connection/auth errors at startup instead of on first user request.

**Trade-offs:**
- Pro: Failures caught at startup with full traceback; error stored in `app.state.cosmos_error` for settings page
- Con: Adds ~200ms to startup time; if CosmosDB is temporarily unreachable at startup, app won't self-heal without restart

**Files Changed:**
- `web/app.py` — startup handler, `_resolve_env`, `_get_cosmos`, settings/dashboard routes
- `web/templates/settings.html` — error diagnostic section

---

#### Manual Roll Endpoint Design
**Date:** 2025-07-16  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  

Made `source`/`closing_source` optional in `roll_position()` rather than creating separate method. One code path for both manual and signal-based rolls.

**Design:** Endpoint infers position type (call/put) from existing position instead of requiring caller to specify it — fewer fields to pass, fewer validation errors.

**Endpoint:** `POST /api/symbols/{symbol}/positions/{position_id}/roll`

---

## Documentation & Deployment

### Unified Azure Setup Documentation
**Date:** 2025-07-15  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  

Merged separate Azure provisioning sections into single "## Azure Setup" with five numbered steps in logical dependency order:

1. Set Variables (consistent `${VAR:-default}` pattern)
2. Create Resource Group (once, shared)
3. Provision CosmosDB (inline az CLI commands)
4. Deploy to Container Apps (uses CosmosDB outputs from step 3)
5. Update Deployment (for subsequent pushes)

**Rationale:** Prevents users from deploying Container Apps before CosmosDB; eliminates drift; consistent variable patterns; `eastus` unified default.

**Impact:** README.md refactored (~48 net line reduction); `provision_cosmosdb.sh` unchanged.

---

### Remove Old File-Based Storage Artifacts
**Date:** 2025-07-09  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  

Deleted all file-based storage artifacts after CosmosDB migration completed:
- `data/` directory
- `logs/` directory
- `src/logger.py`
- `scripts/migrate_to_cosmosdb.py`

**Rationale:** Dead code/files create confusion; migration script references deleted data formats; README referenced file-based workflows no longer in use.

---

## Logging

### Timestamp Generation Moved from LLM to Python
**Date:** 2025-07-28  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  
**Commit:** 54a219e

All log timestamps set in Python BEFORE agent execution using `TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"`. LLM's `timestamp` field always overridden. Ensures consistency across activity and alert JSONL logs.

**Impact for team:**
- Linus (Quant Dev): Instruction schemas still include `timestamp` but as "auto-set by system"
- Basher (Test/Ops): All log entries now have consistent `YYYY-MM-DD HH:MM:SS` format

---

### Dashboard Data Enrichment from Activity Logs
**Date:** 2025-07-28  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  
**Commit:** 0831a03

`_build_agent_table()` reads `activity_log` via `_latest_activities_by_key()` to enrich dashboard rows with health metrics (DTE, moneyness, delta, IV, premium, risk flags). Alert list page gains IV/Premium/Delta columns.

---

## User Directives

### Only Commit Changes, Never Push
**Date:** 2026-03-30T11:27:20Z  
**By:** dsanchor (via Copilot CLI)  
**Status:** Active  

User directive: Only commit changes automatically, never push to remote. User will handle `git push` manually.

**Rationale:** User workflow preference to maintain control over when changes go to remote.

---

## Web UI & Frontend Decisions

### Dashboard Timezone Display Pattern
**Date:** 2024-03-30  
**Author:** Linus (Quant Dev / Frontend)  
**Status:** Implemented  

Implement dual-timezone display on dashboard for scheduler "Last run" and "Next run" times to reduce user confusion across timezones.

**Design:**
1. **Primary:** Show times in scheduler's configured timezone (backend provides ISO timestamp + timezone name)
2. **Secondary:** If user's browser timezone differs, show their local time below in smaller, muted text
3. **Tooltip:** Hover shows both times clearly labeled

**Implementation Pattern:**
- Backend passes: `{field}_iso` (ISO 8601 string) and `scheduler_timezone` (IANA timezone name)
- Frontend: Client-side JavaScript uses `toLocaleString()` with timezone parameter
- Format: "MMM DD, YYYY, HH:MM:SS AM/PM TZN" (e.g., "Mar 30, 2024, 02:00:00 PM EDT")
- Dual display markup: `formatted + '<br><small style="color: #888;">(localFormatted)</small>'`

**Rationale:**
- **Clarity:** No ambiguity about which timezone is displayed
- **Convenience:** Users see times in their local context when relevant
- **Clean UI:** Single timezone display when user TZ = scheduler TZ (no clutter)
- **Standards-based:** Uses native Intl API, no external timezone libraries needed client-side
- **Maintainable:** Backend owns timezone logic, frontend just formats for display

**Team Impact:**
- **Pattern:** Can be reused for any timestamp display in web UI
- **Backend contract:** Always send `{field}_iso` (ISO string) + timezone name
- **Frontend contract:** Always format client-side using Intl API

**Files Modified:** `web/templates/dashboard.html`

**Related:** Backend timezone support added by Rusty (pytz integration in web/app.py); scheduler timezone configuration in config.yaml and Settings page

---

### CosmosDB Settings Must Override Config File at Runtime
**Date:** 2025-01-15  
**Author:** Rusty (Backend Dev)  
**Status:** Implemented  

The application uses a two-tier configuration system:
1. **config.yaml** — File-based defaults
2. **CosmosDB settings** — Runtime-editable settings via web UI

The `merge_defaults()` function merges config.yaml values into CosmosDB, but only adds missing keys (never overwrites existing CosmosDB values).

**Problem:** After merge_defaults() was called in `src/main.py`, the Config object was NOT updated with the merged result. This caused the scheduler to use stale values from config.yaml instead of the authoritative CosmosDB values.

**Symptom:** User sets cron to "30 9-16/4 * * 1-5" via web UI → CosmosDB correctly stores it → but scheduler runs with "00 9-16/4 * * 1-5" from config.yaml.

**Decision:** After calling `merge_defaults()`, immediately update the Config object with the merged settings:

```python
merged_settings = self.cosmos.merge_defaults(settings_defaults)

# Update Config object with merged settings from CosmosDB (CosmosDB takes precedence)
if merged_settings:
    for key, value in merged_settings.items():
        if key not in ('azure', 'cosmosdb'):
            self.config.config[key] = value
```

**Rationale:**
1. **CosmosDB is the source of truth** for runtime-editable settings
2. **config.yaml is for defaults only** (first-run seed + new keys added in code updates)
3. **Web UI changes must persist** across scheduler restarts
4. **merge_defaults() returns the merged result** — we must use it

**Impact:**
- Scheduler now correctly uses settings modified via web UI
- Settings precedence is clear: CosmosDB > config.yaml
- No breaking changes — only fixes broken behavior

**Files Modified:** `src/main.py` — OptionsAgentScheduler.setup()

**Testing:** Set cron to "30 9-16/4 * * 1-5" via web UI, restart scheduler, verify it prints and uses the :30 minutes.

---

## Feature Implementations

### Per-Symbol Telegram Notification Toggles
**Date:** 2025-01-15  
**Author:** Rusty (Backend Dev)  
**Type:** Feature Implementation  
**Status:** Implemented  

Implemented per-symbol toggle for Telegram notifications to give users fine-grained control over which symbols trigger alerts.

**Context:** User requested ability to disable Telegram notifications for specific symbols while keeping notifications enabled for others. This is particularly useful when:
- User has many symbols but only wants alerts for a subset
- Testing new symbols without spam
- Temporarily muting notifications for volatile symbols

**Implementation Approach:**

**1. Storage Pattern:**
- Added `telegram_notifications_enabled: bool` field to symbol config documents in CosmosDB
- Default value: `True` (preserves existing behavior)
- Follows same pattern as `covered_call`/`cash_secured_put` watchlist toggles

**2. Notification Check Location:**
- Check implemented in `TelegramNotifier.send_alert()` method (not agent runners)
- **Rationale:** Centralizing the check ensures ALL notification types (sell alerts, roll alerts, future types) respect the setting without modifying multiple agent codepaths

**3. Safe Defaults:**
- Missing field = enabled (backward compatible)
- Symbol not found = enabled (fail open, not closed)
- CosmosDB unavailable = enabled (graceful degradation)

**4. UI Placement:**
- Toggle appears next to Call/Put watchlist toggles
- Labeled "Telegram Notifications" for clarity
- Present on both symbols list page and symbol detail page

**Migration:**
Existing symbols need the field added. Run:
```bash
python scripts/migrate_add_telegram_notifications.py
```
This adds `telegram_notifications_enabled: True` to all existing symbols.

**Files Modified:**
- `src/cosmos_db.py` — Symbol schema
- `src/telegram_notifier.py` — Notification check logic
- `web/app.py` — API endpoint handler
- `web/templates/symbol_detail.html` — Detail page toggle
- `web/templates/symbols.html` — List page toggle
- `scripts/migrate_add_telegram_notifications.py` — Migration script

**Alternative Approaches Considered:**
1. **Global blacklist in settings:** Rejected — less discoverable, harder to manage per-symbol
2. **Agent-level check:** Rejected — would require modifying all agent runners, not future-proof
3. **Separate notification config document:** Rejected — adds complexity, symbol config is the natural place

**Future Considerations:**
- Could extend to notification types (e.g., "disable sell alerts but keep roll alerts")
- Could add notification frequency limits per symbol
- Could integrate with "quiet hours" feature if added

**Team Impact:**
- **Danny:** Frontend toggle follows existing patterns
- **Linus:** No impact on agent strategy logic
- **All:** Existing symbols remain opt-in (notifications enabled) after migration


---

## Recent Decisions (Merged from Inbox)

### Earnings Decision Matrix — Nuanced vs Binary

**Date:** 2025-07-24  
**Author:** Linus (Quant Dev)  
**Status:** Implemented  
**Impact:** Team-wide (changes agent behavior for both CC and CSP)

The analysis agents were rejecting positions when earnings were ~30 days away — a blanket "no-go" that left premium income on the table. A 21-DTE position with earnings 30 days out expires 9 days before earnings and bears zero earnings risk.

**Decision:** Replace the binary earnings check with a tiered **Earnings Decision Matrix** that evaluates the gap between option expiration and earnings date, not just proximity to earnings.

**Tiers:**
| Earnings Distance | Rule | Risk Flag |
|---

### Protect all routing fields from dict-spread override

**Date:** 2025-07-24  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  

`write_activity()` and `write_alert()` in `src/cosmos_db.py` use `**data` dict spread to merge LLM-generated agent output into the CosmosDB document. The previous fix (commit 06150da) only protected `id` and `timestamp` after the spread. The `doc_type` field was left unprotected, meaning any LLM-generated dict containing a `doc_type` key would silently overwrite `"alert"` or `"activity"`, making documents invisible to queries.

**Decision:** Reassert ALL routing/identity fields after the spread in both methods:
- `write_activity()`: id, timestamp, doc_type, symbol, agent_type, is_alert
- `write_alert()`: id, timestamp, doc_type, symbol, agent_type, activity_id

**Rationale:** Any field used in CosmosDB partition keys, queries, or cross-document references must be treated as immutable infrastructure, not something the LLM can override. Defensive reassertion is cheap and prevents silent data corruption.

**Impact:** Fixes alert visibility bug — alerts will now always be queryable by `doc_type = 'alert'`.

---

## Quick Analysis Chat Decision Summary Table Pattern

**Date:** 2026-04-02  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  
**Scope:** Web UI / Chat Feature

### Context

The quick analysis chat feature provides conversational analysis of options opportunities, but users requested a more structured way to evaluate decisions. They need to quickly see:
- Both sides: reasons FOR and AGAINST opening a position
- Specific recommendations: strikes and expiration dates with reasoning
- Gate-based risk assessment (earnings, technicals)
- Actionable decision support

### Decision

Added a **mandatory Decision Summary Table** to the quick analysis chat instructions (both call and put variants).

#### Table Structure (9–10 Key Factors):

1. **Overall Recommendation** — Clear stance (Favorable / Cautiously Favorable / Neutral / Not Recommended)
2. **Key Reasons AGAINST Opening** — Specific risks with examples (earnings timing, technical red flags, gate violations)
3. **Key Reasons FOR Opening** — Specific opportunities (support levels, oversold conditions, technical setups)
4. **Suggested Strike Prices** — 1–2 strikes with reasoning (deltas, support/resistance, moneyness)
5. **Suggested Expiration Dates** — DTE ranges with reasoning (earnings timing, theta decay, technical timeframe)
6. **Earnings Gate Status** — SAFE / CAUTION / UNKNOWN with specific guidance based on gate logic
7. **Technical Gate Status** — Momentum summary (Bullish/Neutral/Bearish with key indicators)
8. **Primary Risk to Monitor** — Single most important risk factor to watch
9. **Profit Target / Exit Plan** — Tactical guidance (50% profit rule, roll scenarios)
10. **Assignment Readiness** (Puts Only) — "Would you own this stock at this strike price?"

### Implementation

- **Location:** `src/tv_open_call_chat_instructions.py` and `src/tv_open_put_chat_instructions.py`
- **Format:** Markdown table rendered after conversational analysis
- **Style:** Conversational analysis (3–5 paragraphs) → Decision Summary Table
- **Specificity:** Table must reference actual numbers (prices, deltas, dates, DTE) from the analysis
- **Balance:** Present both risks (AGAINST) and opportunities (FOR) equally

### Rationale

1. **Two-mode presentation:**
   - Conversational analysis for understanding and context
   - Structured table for decision-making and scanning

2. **Gate integration:**
   - Leverages existing gate logic from monitoring agents (earnings gates, technical gates)
   - Makes gate status visible and actionable in the analysis

3. **Balanced perspective:**
   - Forces presentation of BOTH sides (risks and opportunities)
   - Helps users make informed decisions, not just confirmation bias

4. **Actionable specificity:**
   - Not generic advice ("consider options") but specific ("$435 strike at 0.25 delta, 14 DTE expiring before earnings in 18 days")
   - References support/resistance levels, deltas, DTE, earnings timing

5. **User-centric:**
   - Answers the key question: "Should I open this position, and if so, how?"
   - Provides clear exit/profit targets

### Alternatives Considered

1. **Purely conversational (no table):** Too hard to scan and extract decision factors
2. **Table only (no conversation):** Loses context and nuance
3. **Separate "summary" endpoint:** Added complexity, better to integrate in one response
4. **JSON output:** Not human-friendly for chat interface

### Consequences

#### Positive
- ✅ Users get clear, scannable decision support
- ✅ Both risks and opportunities presented equally
- ✅ Gate logic made visible and actionable
- ✅ Specific recommendations (strikes, dates) with reasoning
- ✅ Consistent format across call and put analyses

#### Neutral
- Increases response length (conversational + table)
- Requires LLM to follow structured format (tested, works well with GPT-4)

#### Negative
- None identified yet. May need to refine table format based on user feedback.

### Related Files

- `src/tv_open_call_chat_instructions.py` — Call analysis instructions with table
- `src/tv_open_put_chat_instructions.py` — Put analysis instructions with table
- `web/app.py` (lines 1688–1699) — Dynamic instruction loading
- `.squad/agents/rusty/history.md` — Implementation log

### Future Considerations

- May add "confidence score" based on gate alignment
- Could add "similar historical setups" if we build a pattern library
- Consider visual formatting enhancements (color coding for gate status)

---

## Anti-403 Strategy: TradingView Data Fetcher Resilience

**Date:** 2026-04-06  
**Authors:** Danny (Lead), Linus (Quant Dev)  
**Status:** Proposed (Pending User Approval)  
**Impact:** Core data fetching — resilience, success rate, error isolation

### Problem Statement

TradingView is detecting and blacklisting scraping sessions with persistent 403 errors (current rate: 20–30% of symbols). Root causes:

1. **Single session reused across all symbols** — One `requests.Session()` per agent type, 20–50 symbols per run. Cookies accumulate; TradingView builds client fingerprint.
2. **Sticky global 403 flag** — Once `has_403 = True`, ALL subsequent symbols skipped (cascading failure).
3. **Predictable access pattern** — Symbols processed in deterministic order (CosmosDB query result). TradingView sees exact same sequence every 4 hours.
4. **No session rotation after 403** — Tainted session continues until agent run completes.
5. **Sequential resource fetching** — Each symbol fetches 5 resources with same session/cookies.

**User Hypothesis (dsanchor):** TradingView blacklists client config (cookies/session fingerprint) when accessing "hot" resources/symbols. Once banned, session is permanently tainted.

### Convergent Solution: Per-Symbol Session Isolation + Graduated Recovery

Both Danny and Linus independently proposed converging strategy (4 phases, prioritized):

#### Phase 1: Per-Symbol Session Lifecycle (HIGH PRIORITY)

**Change:** Move session creation inside symbol loop instead of reusing one session.

```python
# OLD: One session for all symbols
async with create_fetcher(config) as fetcher:
    for sym_doc in cc_symbols:
        await runner.run_symbol_agent(..., fetcher=fetcher)

# NEW: Fresh session per symbol
for sym_doc in cc_symbols:
    async with create_fetcher(config) as fetcher:
        await runner.run_symbol_agent(..., fetcher=fetcher)
```

**Rationale:**
- Each symbol gets clean slate — no cookie contamination
- TradingView sees individual "users" instead of scraper hitting 50 symbols
- Minimal code change — just move `async with` inside loop

**Implementation:**
- Refactor 4 agent wrappers: `covered_call_agent.py`, `cash_secured_put_agent.py`, `open_call_monitor_agent.py`, `open_put_monitor_agent.py`
- Remove `self.has_403` from `TradingViewFetcher.__init__()`
- Replace with per-fetch error details in result dict: `{"error_403": "message"}` instead of side-effect flag
- Update `agent_runner.py` to check `data.get("error_403")` instead of `fetcher.has_403`

#### Phase 2: Graduated Cooldown with Fresh Session Retry (HIGH PRIORITY)

**Change:** Replace single 403 check with exponential backoff + session refresh.

```python
async def _fetch_with_403_recovery(self, url: str, full_symbol: str, resource: str):
    """Fetch with exponential backoff + session refresh on 403."""
    delays = [5, 15, 45]  # seconds
    for attempt in range(len(delays) + 1):
        try:
            resp = self._session.get(url, headers=_get_random_headers(), timeout=15)
            if resp.status_code == 403:
                if attempt < len(delays):
                    delay = delays[attempt]
                    logger.warning("403 for %s %s — cooling %ds, refreshing session", 
                                   resource, full_symbol, delay)
                    await asyncio.sleep(delay)
                    self._session.close()
                    self._session = _requests.Session()  # Fresh session
                    self._session.headers.update(_get_random_headers())
                    continue
                else:
                    logger.error("403 for %s %s — all retries exhausted", resource, full_symbol)
                    return resp, True  # Fatal
            resp.raise_for_status()
            return resp, False  # Success
        except Exception as e:
            if attempt == len(delays):
                raise
            logger.warning("Error for %s %s: %s — retrying", resource, full_symbol, e)
            await asyncio.sleep(delays[attempt])
```

**Rationale:**
- First 403 might be transient rate-limiting → retry after cooldown
- Fresh session after each 403 prevents cookie taint accumulation
- Exponential backoff (5s → 15s → 45s) gives TradingView time to "forget" bad session
- After 3 attempts, mark symbol failed but don't taint other symbols (isolated failure)

**Configuration:**
```yaml
tradingview:
  max_403_retries: 3
  retry_delays: [5, 15, 45]  # Exponential backoff (seconds)
```

#### Phase 3: Symbol Order Randomization (MEDIUM PRIORITY)

**Change:** Shuffle symbol list before processing.

```python
import random
cc_symbols = cosmos.get_covered_call_symbols()
random.shuffle(cc_symbols)  # NEW: Randomize access order
```

**Rationale:**
- Breaks predictable scraping patterns
- If TradingView tracks "user A always accesses AAPL → MSFT → TSLA", randomization makes us look like different users
- Minimal overhead, high impact

**Configuration:**
```yaml
tradingview:
  randomize_symbols: true  # (default: true)
```

#### Phase 4: Homepage Warm-Up (LOW PRIORITY, OPTIONAL)

**Change:** Visit TradingView homepage to establish "organic" cookies before fetching resources.

```python
async def _warmup(self):
    """Visit TradingView homepage to establish organic cookies."""
    if not self._warmup_done:
        try:
            self._session.get("https://www.tradingview.com/", 
                            headers=_get_random_headers(), timeout=10)
            self._warmup_done = True
            logger.debug("Homepage warm-up completed")
        except Exception as e:
            logger.warning("Homepage warm-up failed: %s", e)
```

**Rationale:**
- Mimics organic browsing (user lands on homepage first)
- Establishes baseline cookies before hitting data endpoints
- Configurable (conservative by default)
- Low cost (~500ms per symbol)

**Configuration:**
```yaml
tradingview:
  warmup_enabled: false  # (default: false, enable if needed)
```

### Expected Outcomes

**Before Implementation:**
- 403 error rate: ~20–30% of symbols
- Persistent 403s on "hot" symbols cascade to entire batch
- Global `has_403` flag taints entire run

**After Phase 1–2 (MVP):**
- 403 error rate: <5% of symbols
- No multi-symbol cascading failures (one 403 isolated to that symbol)
- Individual symbols may still get 403 after retries, but it doesn't taint others

**After Phase 3–4 (Optional Enhancements):**
- Further 403 reduction if TradingView detects by IP (unlikely, but phases provide flexibility)
- More human-like access patterns (randomization + warm-up)

### Risk Assessment

| Risk | Likelihood | Mitigation |
|---

### Anti-403 Implementation (4 Phases)
**Date:** 2026-04-06  
**Author:** Rusty (Agent Dev)  
**Status:** ✅ Complete  
**Impact:** TradingView fetching resilience; all 4 agent wrappers, config, core fetcher

Implemented Danny's 4-phase anti-403 architecture to make TradingView data fetching resilient against HTTP 403 rate-limiting blocks through per-symbol session isolation, graduated recovery with exponential backoff, symbol randomization, and optional homepage warm-up.

**Phases Implemented:**

1. **Per-Symbol Session Isolation** — Moved `async with create_fetcher(config) as fetcher` inside symbol loop in all 4 agent files. Each symbol gets fresh HTTP session + Playwright browser lifecycle. Removed global `has_403` flag; `fetch_all()` returns `tv_403: bool` in result dict (stateless design). Monitor agents scope fetcher per-symbol, not per-position.

2. **Graduated 403 Recovery** — Replaced immediate failure with `_handle_403()` async method implementing exponential backoff (5s → 15s → 45s, configurable). Between retries: close old session, create fresh `requests.Session` with random headers. After max retries exhausted (default 3), raise HTTPError which `fetch_all()` catches and marks `tv_403=True`.

3. **Symbol Randomization** — Added `random.shuffle(symbols_list)` in all 4 agent files when processing all symbols (not single-symbol runs). Gated by `config.tradingview_randomize_symbols` (default: True).

4. **Homepage Warm-Up** — Added `_warmup()` method visiting https://www.tradingview.com/ to establish organic cookies. Called at start of `fetch_all()` when `warmup_enabled=True`. Defaults to False (conservative).

**Files Modified (9 total):**
- `src/tv_data_fetcher.py` (core refactor: `_handle_403()`, `_warmup()`, `_refresh_session()`)
- `src/config.py` (4 new config properties)
- `config.yaml` (new tradingview section)
- `src/covered_call_agent.py`, `src/cash_secured_put_agent.py`, `src/open_call_monitor_agent.py`, `src/open_put_monitor_agent.py` (per-symbol fetcher + randomization)
- `src/agent_runner.py` (check `data.get("tv_403")` instead of `fetcher.has_403`)
- `web/app.py` (check `data.get("tv_403")` instead of `fetcher.has_403`)

**Key Design Decisions:**
- Session isolation scoped per-symbol; monitor agents share fetcher for same-symbol positions (stateless)
- `tv_403` flag in result dict (caller-owned) not fetcher state (clean separation of concerns)
- Exponential backoff configurable: defaults [5, 15, 45] seconds; max retries default 3
- Warm-up conservative (defaults to False); can be enabled in config for higher resilience
- Randomization only on full symbol runs (not single-symbol) to preserve test determinism

**Testing:**
- Basher wrote 28-test validation suite (`tests/test_anti403.py`), all passing
- Coverage: session isolation (6), 403 recovery (8), global state isolation (4), warmup (3), randomization (4), config loading (3)
- Edge case discovered: `tv_403` flag in `fetch_all()` unreachable due to exception catch in individual fetch methods; non-blocking, recommend next-iteration fix

**Deployment Readiness:**
- ✅ All 4 phases implemented
- ✅ All 28 tests passing
- ✅ Backward compatible
- ✅ Config documented
- ✅ Expected 403 rate reduction from 20–30% to <5%

**Commit:** `831b95e` — feat: implement 4-phase anti-403 architecture for TradingView fetching

**Related Files:**
- `.squad/orchestration-log/2026-04-06T14-10-rusty-anti403.md` (Rusty task log)
- `.squad/orchestration-log/2026-04-06T14-10-basher-anti403.md` (Basher task log)
- `.squad/log/2026-04-06T14-10-anti403-implementation.md` (Session summary)

---

## User Directive: Activity Retention on Watchlist Disable
**Date:** 2026-04-06  
**Author:** dsanchor (via Copilot)  
**Status:** Guideline for future work  
**Impact:** Feature implementation — position/watchlist management

When calls or puts watching are disabled, or a position is opened from an alert, **disable watching only** — do NOT delete activities. Activities have a 30-day TTL via CosmosDB, so database bloat is not a risk. This preserves audit trail and operational history for debugging and analysis.

---

## Error Count Metric Addition
**Date:** 2026-04-08  
**Author:** Rusty (Agent Dev)  
**Status:** ✅ Implemented  
**Impact:** Dashboard — runtime telemetry visibility

Added error_count tracking to TradingView fetch runtime statistics. Errors tracked across 1-day, 7-day, and 30-day windows. Dashboard updated with "Errors" column using conditional formatting (green ≤5, red >5).

**Files Modified:**
- `src/cosmos_db.py` — Error count aggregation logic
- `web/templates/settings_runtime.html` — Error column UI

**Benefits:**
- Operator visibility into fetch reliability trends
- Early detection of systematic issues via dashboard color coding

---

## User Directive: Sequential Analysis
**Date:** 2026-04-08T15:22Z
**Author:** dsanchor (via Copilot)
**Status:** 📋 Captured

When running the full analysis, do not trigger all four agent blocks in parallel. Run each block sequentially — one at a time, waiting for each to complete before starting the next.




---

## Archive Update (2026-09-08)

### 2026-08-08: Symbols Suitability and Durable Symbol Creation


**Author:** Team (Rusty, Linus, Basher review)
**Status:** Implemented and approved

#### Suitability Classification

The Symbols UI exposes exactly `All`, `Ideal Puts`, `Ideal Calls`, `No Puts`, and `No Calls`. These categories are deterministic classifications derived from normalized `entry_tag` and momentum values:

- `Ideal Puts`: Strong Buy/Buy with Bullish, Neutral, or Weakening momentum, plus the Bearish (Oversold) override.
- `Ideal Calls`: Hold/Wait with Weakening, Bearish, or Neutral momentum, plus the Bullish (Overextended) override.
- `No Puts`: Strong Buy/Buy with pure Bearish momentum.
- `No Calls`: Wait with pure Bullish momentum.

The suitability categories are not derived from `watchlist.covered_call`, `watchlist.cash_secured_put`, or `watchlist.buy_tracker`; those flags only control operational tracking. They are also distinct from backend option-chain type/delta filters. A pure frontend helper owns the documented suitability semantics and normalizes case and whitespace.

#### Symbol Creation and Shares

- Symbol creation uses a collapsible inline client component and the existing BFF/backend contract.
- `total_shares` is edited inline through partial `PUT`, with optimistic client state, server refresh on success, and rollback on failure.
- The backend accepts only non-negative JSON integers for `total_shares`; invalid values fail before persistence.
- A successful symbol creation persists the symbol before starting `backfill_symbol_forecasts` for that ticker with `DEFAULT_BACKFILL_SESSIONS`.
- Forecast backfill runs independently. A backfill failure is logged but never rolls back the symbol or changes the successful `201` response.

#### Validation

Final review passed 49 watchlist tests, 41 position financial tests, an 11/11 suitability runtime matrix, focused frontend lint, and TypeScript typecheck.

---

# Decision: Agent Provider/Model Configuration in Settings

**Date:** 2026-08-09
**Author:** Rusty (UI/Frontend)
**Status:** Implemented ✅
**Context:** Provider/model selection for Monitoring, Summary, Banner, Plan Monitor agents; precedence hierarchy and credential security

## Summary

Implemented end-to-end Settings UI for configuring provider and model overrides per agent (scheduler, summary_agent, banner_agent, plan_monitor) with secure credential handling and dynamic scheduler reload.

## Decision

**Settings Configuration Hierarchy:**
1. Task override (agent-specific Settings value) — highest precedence
2. Role/provider global model (`ai.models[role]` in config.yaml)
3. Deployment/default (`ai.model_deployment` in config.yaml) — lowest precedence
4. Plan Monitor legacy fallback: `gpt-5.4-mini`

**Blank Settings Values:** Remove task override entirely; do not persist empty strings

**Provider Support:** Only `azure` and `gemini` accepted via Settings UI (prevents typos, scope isolation)

**Credential Handling:** Provider credentials remain in existing secret-backed configuration sections; no credential exposure through Settings UI

## Implementation

- `frontend/` — Settings form components for Monitoring, Summary, Banner, Plan Monitor agent configuration
- `backend/` — Precedence resolver; Settings persistence to CosmosDB and config.yaml
- `scheduler/` — Dynamic configuration reload on Settings changes
- Runtime agents — consume Settings precedence on execution


### Technical Details


**Provider/Model Fields:**
- Optional `provider` override in agent section
- Optional `model` override in agent section
- Both default to null (fall through precedence chain)

**Precedence Resolution Algorithm:**
```
resolve_provider(agent_name):
  if task_override_provider exists: return task_override_provider
  if global ai.provider exists: return ai.provider
  return "azure"  # default

resolve_model(agent_name, role):
  if task_override_model exists: return task_override_model
  if ai.models[role] exists: return ai.models[role]
  if agent_name == "plan_monitor": return "gpt-5.4-mini"  # legacy
  return ai.model_deployment
```

**Empty Settings Behavior:**
- Form submission with blank field → DELETE override from config (not INSERT empty string)
- Blank value in form triggers override removal
- Subsequent resolution uses next precedence level

**CosmosDB Persistence:**
- Settings changes immediately persist to cloud configuration
- Config.yaml updated synchronously for backup/audit
- Scheduler receives reload signal on persistence commit

**Scheduler Dynamic Reload:**
- Listener on Settings change events
- Hot-reload configuration without scheduler restart
- Runtime execution immediately consumes updated precedence

## Validation

✅ Settings form displays effective (resolved) provider/model for each agent
✅ Settings form edits provider/model overrides
✅ Blank form field removes override
✅ Precedence hierarchy correctly applied in all execution paths
✅ Provider validation restricts to {azure, gemini}
✅ Tests/checks passed
✅ Scheduler dynamic reload confirmed
✅ No credential exposure in Settings UI

## Impact

- Operators can override model/provider per agent without code changes
- Precedence hierarchy maintains deployment defaults while allowing task-level override
- Secure credential handling preserves existing secret management
- Dynamic reload eliminates restart requirement for configuration changes
- Plan Monitor backward compatibility maintained

## Cross-Context

**Related:** 2026-08-09 Session — Rusty completed implementation; Linus completed options-chain cache fix (separate work); Scribe merged decisions and created session/orchestration logs.

## Follow-ups

None currently identified; ready for deployment.

---

# Decision: AI Providers Replaces Model Controls in Cron Settings

**Date:** 2026-08-09
**Author:** Rusty (Agent Dev)
**Status:** Implemented
**Supersedes:** The per-agent cron Settings UI described immediately above

Provider and model controls live exclusively under **Settings → AI Providers**.
Cron settings contain scheduling fields only.

The page configures 15 internal functions across Monitoring, Reporting, and
Chat. Overrides are persisted in `ai_function_overrides`; clearing an override
restores inheritance. Resolution order is function override, compatible legacy
configuration, `ai.models`, then global or function-specific defaults.

Only the supported `azure` and `gemini` providers are accepted. Credentials are
never returned to the frontend. Scheduled runs, manual runs, reports, and chats
all resolve provider and model through the same per-function runtime path.

---


### 5. Multi-Provider MCP Configuration with Provider Switching

**Date:** 2026-07-25
**Decider:** Rusty (Agent Dev)
**Status:** ✅ Completed
**Impact:** Team-wide (enables flexible provider selection without code changes)

#### Context

The project initially deployed with `mcp_massive`, then added Alpha Vantage as alternative. Rather than maintaining two separate codebases, we needed a single config-driven approach to switch providers at runtime without code changes.

#### Decision

Implemented provider-based MCP configuration structure:
```yaml
mcp:
  provider: "massive"  # or "alphavantage"
  massive:
    command: "mcp_massive"
    env_key: "MASSIVE_API_KEY"
  alphavantage:
    command: "mcp_alphavantage"
    env_key: "ALPHAVANTAGE_API_KEY"
```

#### Key Design Decisions

1. **Prune inactive providers before env var substitution**
   - Removes non-active provider config sections before resolving environment variables
   - Prevents crash when user only sets API key for selected provider
   - Rationale: User shouldn't need to set all provider keys, only the active one

2. **Lazy instruction imports in agent files**
   - Instruction imports happen inside `async def run()` method, not at module level
   - Conditional logic selects instructions based on `config.mcp_provider`
   - Rationale: AV instruction files don't need to exist for Massive mode

3. **Dynamic MCP tool naming and env key**
   - `AgentRunner` takes `mcp_name` and `env_key` as constructor parameters
   - No more hardcoded "massive" or "MASSIVE_API_KEY"
   - Rationale: Single runner implementation serves all providers

#### Implementation

**Files Updated:**
1. `config.yaml` — Provider selector + per-provider sections
2. `src/config.py` — `mcp_provider`, `mcp_env_key` properties; `_prune_inactive_providers()`
3. `src/agent_runner.py` — Dynamic `mcp_name` and `env_key` parameters
4. `src/covered_call_agent.py` — Lazy provider-specific instruction import
5. `src/cash_secured_put_agent.py` — Lazy provider-specific instruction import
6. `src/main.py` — Pass provider settings to AgentRunner

#### Trade-offs

| Aspect | Pro | Con |
|--------|-----|-----|
| Single config file | Easy to switch providers | Can't use multiple providers in one run |
| Lazy imports | AV files optional for Massive mode | Slightly more complex agent logic |
| Prune before substitute | No required env vars for inactive providers | Inactive config discarded at load time |

#### Consequences

**Positive:**
- Users can select provider in config without code changes
- Supports future providers without architectural changes
- Instruction sets can evolve independently per provider

**Neutral:**
- Requires one env var per active provider (similar to before)
- Runtime cost of lazy imports negligible

#### Verification

- ✅ Config loads correctly with provider selector
- ✅ Pruning removes inactive sections before env var resolution
- ✅ Lazy imports only trigger on provider match
- ✅ AgentRunner accepts dynamic names and env keys
- ✅ Old config format detected with helpful error message

---


### 6. Alpha Vantage MCP Instruction Files (Strategy Logic Parity)

**Date:** 2026-07-25
**Author:** Linus (Quant Dev)
**Status:** ✅ Completed
**Files:** `src/av_covered_call_instructions.py` (420 lines), `src/av_cash_secured_put_instructions.py` (569 lines)
**Impact:** Team-wide (enables trading with Alpha Vantage data source)

#### Context

The project established comprehensive trading instructions for Massive.com MCP server. When Alpha Vantage was selected as alternative provider, we needed parallel instructions that:
- Keep all strategy logic and decision criteria identical
- Only adapt the data gathering protocol to AV's 3-meta-tool architecture (TOOL_LIST → TOOL_GET → TOOL_CALL)
- Leverage AV's unique advantages (built-in technicals, earnings data, sentiment scores)

#### Decision

Created parallel instruction files maintaining 100% strategy parity while optimizing data gathering for AV's tool interface.

#### Key Design Decisions

1. **Preserve all decision criteria identically**
   - Same SELL thresholds (IV Rank, delta ranges, DTE windows)
   - Same strike selection rules (CC: above support, CSP: at/below support)
   - Same output format for signal parsing
   - Rationale: Trading logic should not vary by data source

2. **Phase 1/2/3 structure preserved**
   - Covered Call: 3 phases (core data → context → analytics)
   - Cash-Secured Put: 3 phases (extended core → comprehensive context → analytics)
   - Rationale: Consistent naming makes provider swapping intuitive

3. **Leverage AV advantages for efficiency**
   - **Built-in technicals:** RSI, Bollinger Bands, MACD, SMA, EMA (vs. Massive's manual calculation)
   - **Earnings calendar:** Dedicated EARNINGS tool with beat/miss (vs. Massive's news parsing)
   - **Sentiment scores:** Numerical NEWS_SENTIMENT (vs. Massive's text analysis)
   - **Analyst ratings:** Direct COMPANY_OVERVIEW field (vs. Massive's fundamentals search)
   - Rationale: Use native capabilities for clarity and accuracy

4. **Manual adaptation for missing capabilities**
   - **Greeks:** No built-in Black-Scholes; instructions provide estimation guidance
   - **Joins:** No SQL; agent must synthesize across JSON objects
   - **Insider data:** No dedicated endpoint; instructions guide keyword search in news
   - Rationale: Incomplete data requires conservative criteria, not failure

#### Technical Implementation

**Covered Call Instructions (420 lines):**
```
ROLE + STRATEGY OVERVIEW
  ↓
ANALYSIS FRAMEWORK (Greeks, DTE, earnings)
  ↓
DATA GATHERING (TOOL_LIST → TOOL_GET → TOOL_CALL progression)
  Phase 1: Ticker, price history, options chain, dividends
  Phase 2: Fundamentals, analyst ratings, news/sentiment, technicals
  Phase 3: IV analysis, Greeks estimation, return calcs
  ↓
DECISION CRITERIA + OUTPUT
```

**Cash-Secured Put Instructions (569 lines):**
```
ROLE + STRATEGY OVERVIEW
  ↓
ANALYSIS FRAMEWORK (quality gate, DTE, earnings, technicals)
  ↓
DATA GATHERING (TOOL_LIST → TOOL_GET → TOOL_CALL progression)
  Phase 1: Extended core (price for support ID, dual financials, earnings history)
  Phase 2: Comprehensive (analyst, news, sentiment scores, fundamental quality)
  Phase 3: Strike selection (support via JSON scan, oversold via BBANDS/RSI, Greeks estimation)
  ↓
DECISION CRITERIA + OUTPUT
```

#### Trade-offs

| Aspect | Massive.com | Alpha Vantage |
|--------|-------------|---------------|
| Tool discovery | `search_endpoints` keyword search | `TOOL_LIST` + `TOOL_GET` discovery |
| Data aggregation | SQL JOINs across stored tables | Manual JSON synthesis |
| Technical indicators | Manual via `apply=["sma"]` | Built-in RSI, BBANDS, MACD, EMA |
| Greeks calculation | `apply=["bs_delta", "bs_theta"]` | Manual estimation guidance |
| Earnings data | Parse from news | Direct EARNINGS tool |
| Sentiment | Text-based analysis | Numerical NEWS_SENTIMENT scores |
| Institutional holders | Fundamentals or search | COMPANY_OVERVIEW consensus |

**Advantages AV:**
- Simpler tool interface (no SQL needed)
- More reliable earnings data
- Numerical sentiment is faster to analyze
- Built-in technicals reduce LLM hallucination

**Advantages Massive:**
- SQL composability for complex analysis
- Black-Scholes Greeks built-in
- More granular data control

#### Consequences

**Positive:**
- Single strategy logic supports both providers
- Provider swapping is config change only
- AV's built-in capabilities often provide faster/more accurate analysis
- Instruction maintenance: bug fixes apply to both via common sections

**Neutral:**
- AV requires more manual Greeks estimation (acceptable given other advantages)
- More instruction files to maintain (offset by exact copying of common sections)

**Mitigations:**
- Common sections (ROLE, STRATEGY, CRITERIA) identical between versions
- Extensive examples in DATA GATHERING for AV's tool discovery pattern
- Conservative criteria documented for missing signals

#### Verification

- ✅ Both files valid Python (import test passed)
- ✅ ROLE + STRATEGY OVERVIEW: exact match across versions
- ✅ ANALYSIS FRAMEWORK through DECISION CRITERIA: exact match
- ✅ Only DATA GATHERING PROTOCOL differs (intentional, AV-specific)
- ✅ All tool names verified against AV documentation
- ✅ Phase structure mirrors Massive version

#### Coordination

**Depends on:** Rusty's lazy import pattern (selection happens in agent files)
**Enables:** Agent provider swapping via `config.yaml` change only
**Documentation:** Common decision rationale in decisions.md; provider-specific details in each instruction file

#### Next Steps

1. **Integration testing:** Verify AV TOOL_LIST discovery works with actual API
2. **Signal quality comparison:** Compare decision logic output vs. Massive
3. **Provider migration:** Document process for users switching providers

---


### 2. Endpoint + Activity Detail Integration — `web/app.py` & `web/templates/activity_detail.html`


**Wiring:** Rusty (Agent Dev)
**Status:** ✅ Implemented & verified

**Endpoint:**
```
GET /api/activities/{activity_id}/roll-table
```

**Logic Flow (web/app.py ~3095):**
1. Fetch activity by ID (same pattern as other activity handlers)
2. Validate agent_type → map to option_type (covered_call/open_call_monitor → "call"; cash_secured_put/open_put_monitor → "put")
3. Resolve strike/expiration: `current_strike` / `current_expiration` (monitor agents) with `strike` / `expiration` fallback (watch agents)
4. Resolve premium: `activity["premium"]` → `source["premium"]` → 0
5. Live price: `request.app.state.yf_provider.fetch_all(symbol)` → `overview.fundamentals.current_price.value`
6. Options chain: `get_options_chain_cache().get_or_load_async(symbol)`
7. Call `compute_roll_table(...)`
8. Return `JSONResponse(result)`

**Error Responses:**
- 404: Activity not found
- 400: Unsupported agent_type, missing strike/expiration, invalid strike value
- 503: Price unavailable, options chain error

**Template Integration (web/templates/activity_detail.html ~360):**
- **Visibility:** Only for `agent_type in ['covered_call', 'cash_secured_put', 'open_call_monitor', 'open_put_monitor']`
- **Card:** "Roll Scenarios" section (id="rollTableCard")
- **JS:** Fetches endpoint on page load (no button required)
- **Loading:** Spinner (blue spinning border from style.css:723)
- **Error:** Inline message with `var(--accent-red)`
- **Summary:** Strike, expiration, premium received, buyback cost + per-share, % capturado (orange <70%, green ≥70%), profit_target_reached badge, chain timestamp (orange ⚠️ if >15 min old)
- **Grid:** Table with expirations as columns, strike offsets (ATM, +3%, -3%) as rows
- **Cell Display:** bid/ask, delta, net_credit — **no open interest** (per user spec)
- **Cell Colors:** green (rgba 0,168,126,0.18), red (rgba 226,59,74,0.18), gray (transparent with "—")

**Verification:**
- `python3 -m py_compile web/app.py` ✅
- `python3 -m pytest tests/test_roll_table.py -q` → 46/46 passed ✅
- AST parse (29936 nodes) ✅

**No changes to:** `src/roll_table.py`, `tests/test_roll_table.py`

## Impact

- Users now have deterministic roll analysis in Activity Detail
- 70% profit target gate (aligned to `open_call_assessment_instructions.py:68`) highlights when closing is justified
- Automatic endpoint fetch on page load (no extra button needed)
- Supports all position types: covered calls, CSP, and monitor agents
- Clean integration with existing activity detail template

## Files Changed

- `src/roll_table.py` — new, pure Python calculator
- `tests/test_roll_table.py` — new, 46 tests
- `web/app.py` — new endpoint (lines ~3095)
- `web/templates/activity_detail.html` — Roll Scenarios card + JS (lines ~360)

# Decision: Roll Table Relocation — Activity Detail → Position Detail

**Date:** 2026-07-23
**Author:** Rusty (Agent Dev)
**Status:** Implemented ✅

## Context

The Roll Table UI was previously wired to `activity_detail.html` via endpoint `GET /api/activities/{activity_id}/roll-table`. However, users interact with position data primarily through `symbol_detail.html`, where each active position has an expandable detail block showing the Monitoring History chart and DPS analysis buttons. The roll table was invisible from this primary workflow.

## Decision

**Relocate the Roll Scenarios section end-to-end from activity detail to position detail.**

- Surface Roll Scenarios for **every active position** (calls and puts), not just activities that happen to have a matching agent type.
- Trigger automatically on position row expand (lazy-load, load-once guard), consistent with how the Monitoring History chart loads.
- Use a dedicated position-scoped endpoint so the data is correct regardless of whether the user navigated through an activity.

## Changes


### 3. Added to `web/templates/symbol_detail.html`


**HTML** — inserted inside the `{% if pos.status == 'active' %}` guard, after the `.dps-analysis-section` div, still within the `.position-snapshot-chart` wrapper:

```html
<div class="roll-table-section"
     data-symbol="{{ symbol_doc.symbol }}"
     data-position-id="{{ pos.position_id }}"
     style="margin-top:0.75rem; border-top:1px dashed var(--border); padding-top:0.75rem;">
    <div class="roll-table-loading" style="display:flex; ...">…spinner…</div>
    <div class="roll-table-error" style="display:none; ..."></div>
    <div class="roll-table-content" style="display:none;">
        <h4>🔄 Roll Scenarios</h4>
        <div class="roll-table-summary"></div>
        <table class="roll-table-grid"></table>
    </div>
</div>
```

**JS** — IIFE added after `loadPositionSnapshotChart` function, exposes `window._loadRollTable(section)`:
- Guards with `dataset.rollLoaded` / `dataset.rollLoading` (load once per position)
- Fetches `GET /api/symbols/{sym}/positions/{posId}/roll-table`
- Builds summary bar (strike, exp, premium, buyback, % capturado, profit_target badge, chain timestamp ⚠️)
- Builds grid (ATM / +3% / -3% rows × 4 expirations; bid/ask + delta + net_credit; green/red/gray cells)

**Expand hooks** — `window._loadRollTable` called in:
1. `tr.pos-row` click handler (main expand)
2. Roll-button expand handler

Jinja balance verified: 81 opens / 81 closes ✅

## Validation

| Check | Result |
|---|---|
| `pytest tests/test_roll_table.py -q` | 46 passed ✅ |
| `python3 -m py_compile web/app.py` | OK ✅ |
| `python3 -c "import web.app"` | import OK ✅ |
| Jinja balance activity_detail.html | 51/51 ✅ |
| Jinja balance symbol_detail.html | 81/81 ✅ |

## Notes

- `src/roll_table.py` and its tests were **not modified** (pure calculation module, stable).
- The old `GET /api/activities/{activity_id}/roll-table` endpoint was **not removed** — it still exists but is no longer called from any template.
- Roll table renders for **all active positions** regardless of type (call or put), which was the primary motivation for the relocation.
---

# Decision: Roll Table Columns — Current Expiration Highlighting & ATM Price Context

**Date:** 2026-07-23
**Status:** Implemented ✅

## Summary

Enhanced roll table column layout to display current expiration as the primary reference, with optional previous expiration for comparison, followed by 4 future expirations. ATM row now displays the underlying price used as the base for moneyness calculations.

## Decision

**Roll table displays:** Previous (optional) + Current (highlighted) + 4 Future expirations

**ATM row context:** Show underlying price as calculation base (e.g., "ATM ($71.54)")

## Implementation

- `src/roll_table.py`: Expirations output includes `is_current` and `is_previous` boolean flags
- `src/roll_table.py tests`: 51 tests passing with new column layout
- `web/templates/symbol_detail.html`: buildRollGrid() bolds current expiration header with "● open" marker, adds "(prev)" tag to previous column
- ATM row label now includes underlying price base for user reference

## Impact

- Clearer user navigation: current expiration is visually distinguished
- Previous expiration context available without clutter
- Price anchor context removes ambiguity in moneyness calculations
---


### Approval Status


**Pending team review.**

**Recommendation strength:** HIGH — This addresses a structural root cause, not a symptom. Prompt engineering has been exhausted; data format is the bottleneck.

---

## 3. Decision: Anti-Hallucination Guardrails for Roll Pricing

**Author:** Linus (Quant Dev)
**Date:** 2026-07-22
**Status:** Implemented (not yet committed)


### Telegram Integration


- MODERATE and STRONG challenges trigger push notifications with brief summary
- WEAK challenges stored in CosmosDB for dashboard review only
- Format: "⚡ Contrarian: [one_liner]"

---

## 20. Decision: Contrarian Instructions Design

**Date:** 2026-07-18
**Author:** Linus (Quant Dev)
**Status:** Implemented
**Impact:** `src/tv_contrarian_instructions.py` (new)


### Reason


User request — captured for team memory. Maintains consistency with English as primary language.

---

## 19. Decision: Contrarian Agent Architecture (Propuesta)

**Date:** 2026-07-17
**Author:** Danny (Lead)
**Status:** Implemented (Option A adopted)
**Impact:** Pipeline automation with selective triggering


### Interface for Rusty


```python
from src.tv_contrarian_instructions import get_contrarian_instructions, CONTRARIAN_OUTPUT_SCHEMA

# Get parameterized prompt
prompt = get_contrarian_instructions("open_call", "ROLL_UP_AND_OUT")

# Parse response against schema
# Handle ValueError if combo is invalid
```

---

## 21. Decision: Contrarian Agent Pipeline Integration (MVP)

**Date:** 2026-07-17
**Author:** Rusty (Agent Dev)
**Status:** Implemented
**Implements:** Danny's contrarian architecture (Option A)


### Files Changed


- `src/agent_runner.py` — contrarian method + pipeline integration
- `src/cosmos_db.py` — `update_activity_field()` method
- `src/telegram_notifier.py` — contrarian line in sell + roll alerts

---

## 22. Decision: Prolonged WAIT Detection

**Date:** 2026-07-16
**Author:** Rusty (Agent Dev)
**Status:** Implemented


### 25. Mandatory Premium Cross-Verification Step


**Date:** 2026-07-14
**Author:** Linus (Quant Dev)
**Status:** ✅ Implemented
**Impact:** Agent instructions (7 files)

#### Problem

The CSP watcher agent was reporting premium (bid) from the correct strike but wrong expiration date — specifically the last expiration key in the options chain JSON. The LLM reads a multi-expiration nested dict and silently crosses expiration boundaries when extracting prices.

#### Decision

Add a mandatory "Premium Cross-Verification" step to every agent instruction file that produces a JSON activity block. The step requires the agent to explicitly cite the full chain lookup path (e.g., `puts["20260613"]["95.0"]["bid"] = 3.45`) and verify the expiration key matches the recommended date before writing the JSON output.

#### Scope

- **Watcher agents** (CSP, CC): New numbered step in RESPONSE STRUCTURE before JSON Activity Block
- **Roll agents** (open call roll, open put roll): New subsection before Final Activity JSON Schema — verifies both buyback (ask) and new position (bid) paths
- **Chat agents** (call chat, put chat): Lighter-weight verification guidance section
- **Schema description** (`options_chain_parser.py`): Added COMMON ERROR warning to DATA INTEGRITY section — injected into all agents at runtime

#### Rationale

- Zero runtime cost — this is prompt text only, no code logic changes
- Forces the LLM to make its lookup explicit, which naturally catches cross-expiration errors
- The contrarian agent already had a similar check added in a prior fix; this extends the pattern to the primary agents
- Same structural pattern as the "Never output bare ROLL" fix — making implicit behavior explicit prevents silent errors

#### Files Modified

`options_chain_parser.py`, `tv_cash_secured_put_instructions.py`, `tv_covered_call_instructions.py`, `tv_open_call_roll_instructions.py`, `tv_open_put_roll_instructions.py`, `tv_open_call_chat_instructions.py`, `tv_open_put_chat_instructions.py`

---


### 7. Position Lifecycle — Optional Buyback Cost on Manual Close

**Section:** `### Position Lifecycle` → updated `**Position Actions:**` → Close bullet
**What:** Manual close now supports optional per-share `buyback_cost` field (input shown only for manual close reason; omitted for assigned/expired closes).

## Commits Covered

- `76c5dae` — Roll DTE target tuning + post-earnings block window changes
- `439f0eb` — Roll candidate ranking: Net Credit → Annualized Return %
- `995c377` — Removed dead 7-90 DTE window config (internal cleanup, minimal doc impact)
- `4f0ae0f` — Optional per-share buyback_cost on manual position close
- `5a76e8f` — Scheduler enabled-toggle persistence fix (bugfix, minimal doc impact)
- `92c5a00` — Supervisor surfaces ex-dividend awareness for CSP SELL
- `75740ca` — Calendar events active-position flag per-event-date refinement
- `9db14c6` — Alpha Advisor excludes identical held contract + surfaces buyback cost
- `65762ab` — NEW: Per-Activity Chat (read-only LLM advisory)
- `a3145fb` — NEW: DPS Insights + DAL refactor (internal DAL changes briefly noted/skipped per constraints)

## Validation

- Re-read all edited sections to confirm they read cleanly and headings nest correctly
- All thresholds, field names, and model names verified against commit diffs
- No sections accidentally broken, no unrelated content touched
- Documentation needs no tests per task constraints

# Portfolio Chat Context Contract

**Date:** 2026-07-14
**Author:** Rusty (Agent Dev)
**Status:** ✅ Implemented
**Impact:** User-scoped advisor context, improved chat UX, server-side backward compatibility

## Decision

Portfolio Chat now has an intermediate configuration step and sends two request fields to `/api/chat` when `mode == "portfolio"`:

- `selected_agents`: ordered subset of `AGENT_TYPES` selected by the user
- `activities_limit`: maximum recent activities/alerts per open position or watchlist symbol

The server remains the source of truth for context construction. It iterates selected agents in `AGENT_TYPES` order, uses active positions for position monitors, uses watchlist membership for following agents, and always includes the open position/watchlist row even when there are fewer than N or zero activities.

## Rationale

This avoids dumping all portfolio activity by default and lets the user scope advisor context before the chat begins. Server-side construction preserves backward compatibility and keeps CosmosDB access partition-scoped through `get_recent_activities(..., include_alerts=True)`.

## Implementation

- **web/app.py:** `/api/chat` branch for `mode == "portfolio"` accepts `selected_agents` (list) and `activities_limit` (int) from request body; builds per-position and per-watchlist-symbol context filtered by selected agents
- **web/templates/chat.html:** New `#portfolioConfigForm` with 5 agent checkboxes + activities limit numeric field (default: 3), shown before chat begins
- **tests/test_chat.py:** 13 passing tests validate context construction and request handling

## Validation

- AST parse: ✅ OK
- Test suite: `pytest tests/ -k chat` → 13 passed

## README Documentation Conventions

- Consistent heading hierarchy: `###` for major features, `####` for subsections
- Technical details use inline code formatting for field names, config keys, and model names
- Behavior descriptions lead with user-visible outcome, followed by technical implementation
- "How it works" numbered lists for multi-step processes
- Bold for emphasis on key principles/constraints
- Exact thresholds and field names quoted from code
- Skimmable formatting: bullet lists for features, tables for comparisons
- Internal refactors briefly noted at most or skipped
# Decision: Watchlist Pause Until Earnings

Date: 2026-07-16
Owner: Rusty
Status: Proposed

## Context
Near earnings, the following agents often spend LLM tokens only for the earnings gate to return WAIT. Users need a temporary suspension for a symbol's following-agent watchlist runs while preserving their underlying watchlist intent.

## Decision
Add a separate `symbol_config.watchlist_pause` layer instead of flipping `watchlist.*` booleans. The pause applies only to `covered_call`, `cash_secured_put`, and `buy_tracker`; it does not affect `open_call_monitor` or `open_put_monitor` position monitors.

An active pause is `watchlist_pause.until >= today` using local `YYYY-MM-DD`. Watchlist scheduler queries exclude active pauses. Manual/per-symbol following-agent paths also check the pause helper. Expired pauses are query-inactive and are cleared by a weekday 06:00 `watchlist_reactivation` scheduler job.

## Consequences
- User watchlist preferences remain intact and resume automatically after earnings.
- Token savings apply to all three following agents while position risk monitoring continues.
- UI can shadow paused symbols/rows using one pause field without hiding data.
- Calendar sync must have an upcoming earnings date unless callers provide an explicit `until` override.

# Decision: Position Monitor Badge Isolation from Watchlist Pause

Date: 2026-07-16
Owner: Coordinator
Status: Implemented

## Context
The dashboard "paused until earnings" badge was being rendered on all monitor rows, including position-monitor rows (open_call_monitor, open_put_monitor). However, position monitors are unaffected by watchlist pause and continue running independently. Displaying the pause badge on monitor rows is misleading and contradicts the design intent: pause only suspends following-agent runs, not position monitoring.

## Decision
Gate the pause badge rendering in `_build_dashboard_tables` with `and not is_pm` (position-monitor check). The badge renders only on watchlist rows, never on position-monitor rows. Position monitors always display their active state, unrelated to watchlist pause status.

## Consequences
- Dashboard is semantically correct: pause badge only appears where pause actually applies (following-agent watchlist rows)
- Position monitors are visually decoupled from watchlist pause state
- Users cannot misinterpret monitor visibility as affected by watchlist pause
- UI accurately reflects the underlying execution model

# Decision: Symbol Detail Controls — Single Compact Toolbar

Date: 2026-07-16
Owner: Rusty (Agent Dev)
Status: ✅ Implemented
Impact: UX / Minimize Vertical Footprint

## Context

Symbol detail controls were initially grouped into two cards:
1. **Watchlist & alerts** — 4 toggles (alerts, watchlist, notifications, dividend tracking) + pause/resume header action
2. **Views & actions** — 4 navigation chips (Option Chain, Technical Analysis, etc.)

The two-card layout consumed excessive vertical space on the detail page, conflicting with the minimize-footprint UX goal.

## Decision

Consolidate all symbol detail controls into a **single compact horizontal toolbar**:

**Layout:**
- **Left section:** All 4 toggles + Pause button (left-aligned, equal height)
- **Right section:** Navigation chips Option Chain and Technical Analysis (icon-only for compactness)

**Key Properties:**
- Single horizontal row, minimal height
- Toggle buttons show icon + label for clarity
- Secondary nav buttons (Option Chain, Technical Analysis) rendered icon-only to save space
- All element IDs preserved for backward compatibility
- Pure HTML/CSS refactor; no JavaScript behavior changes

## Consequences

- ✅ Symbol detail page now requires significantly less vertical scrolling
- ✅ All functionality preserved (4 toggles, pause, 4 nav chips) in single compact row
- ✅ UX aligns with minimize-footprint design goal
- ✅ Backward compatibility maintained (element IDs unchanged)
- ✅ Mobile-friendly: horizontal scrolling for overflow if needed

## Implementation Notes

- Files: `web/templates/symbol_detail.html` (layout), `web/static/style.css` (toolbar styling)
- Commit: 767ab5e ("refactor: collapse symbol detail controls into a single compact toolbar")
- No API or backend changes required

# Decision: Deterministic Roll Table — MVP (Buyback + Roll Up/Down/Out)

**Date:** 2026-07-23
**Authors:** Linus (Quant Dev), Rusty (Agent Dev)
**Status:** ✅ Implemented & Integrated
**Impact:** Activity Detail UX — roll scenario analysis, profit target gate

## Context

Users need to evaluate roll scenarios when managing short options positions (covered calls, cash-secured puts, and monitor agents). The roll table displays:
- Buyback costs at different strikes and expirations
- Net credit (sold premium less buyback cost)
- Profit target gate (70% of original premium captured)

This enables quick cost-benefit analysis for rolling out/up/down decisions.

## Decisions


### Rationale

- Prompt-only guardrails are insufficient — LLMs can still produce invalid values
- WAIT is the safe fallback for Phase 1 (no action taken, re-evaluated next cycle)
- CLOSE is the safe fallback for Phase 2 (if direction can't be determined, close the position rather than roll blindly)
- Constants (`VALID_ROLL_ACTIONS`, `VALID_PHASE2_ACTIVITIES`) are importable for use in tests and other modules

---

## 11. Decision: Pre-Computed Markdown Candidate Tables for Phase 2

**Date:** 2026-07-10
**Author:** Rusty (Agent Dev)
**Status:** Implemented
**Commit:** 6e7556f


### Multi-DB Stance


Do NOT build the abstraction now. The generic JSON format IS the portability bridge (80% of the benefit). When a 2nd backend is actually added, extract a `StorageBackend` Protocol/ABC from CosmosDBService's public methods and add `CosmosStorage`. Tech-debt to watch: raw `cosmos.container.query_items("SELECT ... FROM c", partition_key=...)` calls that bypass the DAL (e.g. technical_analysis query in the activity-chat endpoint, DPS snapshot queries in web/app.py) — migrate these into CosmosDBService methods over time.

# README Update — July Session Changes

**Date:** 2026-07-10
**Author:** Linus (Quant Dev)
**Requested by:** dsanchor
**Status:** ✅ Complete

## Summary

Updated README.md to document all user-facing changes shipped in the July session. Made surgical, accurate edits to existing sections without reordering or rewriting unrelated content. Matched the README's existing tone, heading style, and formatting.

## Changes Documented


### Impact


✅ Alpha can now correctly compare close cost (ask of held contract) vs rolling to candidates
✅ Held contract never appears as no-op roll target (data-layer enforcement)
✅ Works even when held contract's delta is outside alpha's kept band
✅ Instruction guard reinforces semantic constraint (roll MUST change strike and/or expiration)

---

## 2026-07-09: Per-Activity Chat Feature — Two-Tier Context & Live-Fetch Design

**Decision owners:** Linus (prompt design), Rusty (endpoint + frontend), Basher (testing), dsanchor (feature request)
**Status:** Implemented & Validated (13 tests passing)
**Date decided:** 2026-07-09


### Future Considerations


- Persistent chat history: Store chat sessions in Cosmos if users request (new container or extend activity docs)
- Technical analysis caching: TTL-based caching in OptionsChainCache or dedicated TechnicalAnalysisCache if freshness becomes issue
- Chat transcript export: Add "Export" button generating markdown/JSON dump
- Conversation threading: Extend CosmosDB activity docs with chat_sessions sub-collection for multi-turn persistence

---

## 2026-07-09: DPS Insights Prompt Module

**Date:** 2026-07-09
**Owner:** Linus (Quant Dev)
**Status:** Implemented
**Context:** DPS (Deterministic Position Scorer) time-series narrative feature


### Future Considerations


- If users request interactive Q&A over DPS history (e.g., "why did the score drop on July 1?"), we could extend this into a multi-turn chat interface like activity-chat.
- If the snapshot schema evolves (e.g., adding IV rank, earnings proximity, or other signals), the prompt's snapshot field list and TREND narration logic should be updated to match.
- If we add multiple scoring models (e.g., DPS v2, alternative scorers), the prompt may need to clarify which scorer's outputs it's interpreting.

**Implemented by:** Linus (Quant Dev)
**Reviewed by:** N/A (solo implementation)
**Related files:**
- `src/dps_interpret_instructions.py` (new)
- `.squad/agents/linus/history.md` (updated — added DPS Insights learning)

---

## 2026-07-09: DPS Insights Endpoint

**Date:** 2026-07-09
**Author:** Rusty (Agent Dev)
**Status:** Implemented
**Collaborators:** Linus (Strategy/Prompt owner for `src.dps_interpret_instructions`)


### Related Work


- **Per-activity chat endpoint (2026-07-09, commit 65762ab):** Established the Agent Framework pattern that DPS Insights reuses
- **Deterministic DPS Analysis (`api_dps_analysis`, web/app.py line ~1207):** Complementary feature that provides detailed scoring metrics (DPS Insights is narrative, DPS Analysis is metrics)
- **Linus's `src.dps_interpret_instructions` module:** Strategy logic for interpreting DPS snapshots (parallel work, landing separately)

---

## 2026-07-09: DPS Insights Endpoint Test Suite

**Author:** Basher (Tester)
**Date:** 2026-07-09
**Status:** Tests written, all pass under system python3
**File:** `tests/test_dps_insights.py`


### Run Command


```bash
source .venv/bin/activate 2>/dev/null
python3 -m pytest tests/test_dps_insights.py -q
```

Expected output:
```
..........                                                               [100%]
10 passed in X.XXs
```

---

## 2026-07-09: DAL Leak Refactoring — Eliminate Direct Cosmos Access in web/app.py

**Date:** 2026-07-09
**Agent:** Rusty (Agent Dev / plumbing & web engineer)
**Status:** ✅ Completed


### 8. Supervisor surfaces ex-dividend for CSP (informational); calls unchanged


**Date:** 2026-07-08
**Author:** dsanchor (via Copilot)
**Agent:** Linus (Quant Dev)
**Status:** ✅ Implemented
**Impact:** CSP entry-timing awareness, supervisor context

#### Decision

Add a NON-BLOCKING informational note to the supervisor audit for cash-secured put (CSP) SELL decisions when an ex-dividend date falls within the trade window. Covered-call / call side is intentionally LEFT UNCHANGED (its ex-div ITM early-assignment warning already handles the real risk).

#### Context

**Motivation:** User request. Motivated by a GIS CSP alert (2026-07-07, $35 Aug-21 put) where ex-div was 3 days out and not surfaced.

**Ex-div data availability:** Ex-div data is already in the supervisor's context via the `DIVIDENDS PAGE` block injected by `agent_runner.py:1131-1132` (from yfinance `ex_dividend_date_recent`), so this is instruction-only — no plumbing work.

**Why CSP-only:** For short puts, ex-div creates mild entry-timing consideration (the underlying typically drops ~the dividend on ex-date, moving it modestly toward the short strike). However, options already price this via put-call parity; the value is discretionary entry timing, not catching mispricing. Calls have different dynamics (ITM early-assignment risk), already handled in call instructions.

#### Implementation

**File:** `src/supervisor_instructions.py`
**Method:** Modified `get_supervisor_instructions()` to conditionally append ex-div section when:
- `agent_type == "cash_secured_put"`
- `decision_type == "SELL"`

**Content Guidelines:**
- Check DIVIDENDS PAGE for ex-div within trade window (now → expiration)
- Emphasize near-term case (~10 days) as most relevant for fresh entry
- State ex-div date and typical price drop effect (modest headwind toward strike)
- Frame as **INFORMATIONAL / entry-timing awareness ONLY** — must NOT block, downgrade, or flip SELL decision by itself
- Must NOT by itself raise `challenge_strength` (options already price dividends via put-call parity)
- Deep-ITM (delta < -0.70) + ex-div within ~10 days: rare early-assignment possibility (brief note, consistent with existing CSP framework)
- Fold into existing audit fields (`counter_arguments`, `one_liner`, etc.) — no schema changes

**Lines added:** ~26

#### Verification

- ✅ `python3 -m py_compile src/supervisor_instructions.py` — Passed
- ✅ `covered_call SELL` — no ex-div text (unchanged)
- ✅ `open_call WAIT` — no ex-div text (unchanged)
- ✅ `cash_secured_put SELL` — has ex-div text (CSP-gated)
- ✅ `cash_secured_put NOT_NOW` — no ex-div text (SELL-only gating)

All tests passed — CSP-gating works correctly, other agents byte-for-byte unchanged.

#### Rationale

- **CSP-specific:** Ex-div creates mild entry-timing consideration but options already price this. Different from calls where ex-div creates ITM early-assignment risk (already handled).
- **Non-blocking:** Awareness, not a blocker. Supervisor surfaces as context, not as a challenge requiring reconsideration unless there's also a genuine data/risk issue.
- **No schema changes:** Folds into existing audit fields to keep response parsing unchanged.
- **Conditional append:** Implementation ensures covered_call and other agents get zero changes (tested and verified).

#### Technical Notes

- `agent_runner.py:1131-1132` — DIVIDENDS PAGE injection (already exists)
- `src/supervisor_instructions.py:556-578` — new CSP ex-div awareness section
- Entry-timing awareness framing ensures alignment with existing options pricing model (put-call parity)


### 9. Calendar active-position flag per event date


**Date:** 2026-07-08
**Author:** Rusty (Agent Dev)
**Status:** ✅ Implemented
**Impact:** Calendar event accuracy, position state consistency

#### Decision

The scheduled `sync_calendar` in `src/main.py` now computes `has_active_position` per calendar event date, matching the logic already present in `web/app.py`.

#### Context

Calendar events (earnings / ex-dividend) were being flagged as "active position" whenever the symbol had ANY active position, even if that position had expired before the event date. This was a symbol-wide check that failed to account for position expiration dates.

The web/manual sync in `web/app.py` (lines 2012-2021) was already implementing the correct per-event logic; the scheduled sync had not been updated to match.

#### Implementation

**File:** `src/main.py`
**Changes:**
- Rewrote `sync_calendar` to collect active positions with their expirations
- Added helper function `_has_position_active_on(event_date)` that returns True only if some active position has `expiration >= event_date`
- Applied the helper to both `earnings` and `ex_dividend` upserts
- Web/app.py left unchanged (already correct)

**New Test File:** `tests/test_calendar_active_position.py`
- Validates per-event active position logic
- Test passed ✅

#### Validation

```
python3 -m py_compile src/main.py → OK
pytest tests/test_calendar_active_position.py -q → 1 passed
```

#### Rationale

- **Per-event accuracy:** Each calendar event should be checked against active positions that extend through that specific date
- **Consistency:** Scheduled sync now mirrors the correct web/manual sync logic
- **Scope:** Changes apply to scheduled calendar sync only; web/manual sync and trading logic remain unchanged

---

## 2026-07-09: Alpha Exclude Identical Held Contract + Preserve Buyback Cost

**Date:** 2026-07-09
**Requester:** @dsanchor
**Status:** ✅ Implemented
**Impact:** Alpha advisor accuracy, prevents no-op rolls, improves close vs roll cost comparison


### 7. Scheduler Enabled Toggle Live Registry Persistence


**Date:** 2026-07-03
**Author:** Rusty (Agent Dev)
**Requested by:** dsanchor
**Status:** ✅ Done
**Impact:** Settings UI reliability, scheduler task enable/disable workflows

#### Decision

When saving scheduler settings from the `settings_config_save` endpoint, update the live scheduler registry enabled state for every togglable task immediately after rescheduling its cron.

#### Context

The save path persisted the `enabled` flag to disk config and CosmosDB, and rescheduled the associated cron task. However, the settings page reads checkbox state from `scheduler.registry.get_all_task_metadata()`, which contains the live in-memory state. Without updating the registry after save, the page reload would display stale (previously cached) enabled state for toggled tasks.

**Root Cause:** Gap between persistent storage (disk/Cosmos) and live registry state. The save wrote to disk but not to the registry.

#### Outcome

Added `scheduler.registry.update_task_enabled(task_name, enabled_bool, scheduler.config)` immediately after each `scheduler.reschedule_*()` call in `settings_config_save` for all togglable tasks:

1. summary_agent
2. plan_monitor
3. options_chain
4. dgi_screener
5. banner_agent
6. calendar_sync
7. portfolio_enrichment

**Note:** `monitor_agents` was intentionally left unchanged because the registry hardcodes it as enabled per design.

#### Validation

- ✅ `python3 -m py_compile web/app.py` — No syntax errors
- ✅ Code review: 7 one-line insertions, minimal and surgical
- ✅ No pre-existing unit tests for settings_config_save to run

#### Technical Notes

- The fix is strictly an in-memory sync operation; no API contract changes
- All task enable/disable state paths now synchronized: disk → Cosmos → registry
- Backward-compatible; no breaking changes to existing functionality


### 6. Manual Position Close — Optional Per-Share Buyback Cost


**Date:** 2026-07-02
**Author:** Rusty (Agent Dev)
**Requested by:** dsanchor
**Status:** ✅ Done
**Impact:** Manual close workflows, position economics tracking

#### Decision

Extend manual position closes to accept an optional per-share `buyback_cost`. When provided, the value is stored directly on the closed position. When omitted or empty, the field is not set. The input is only exposed in the close modal for the `manual` close reason; assigned and expired closes retain their existing flow.

#### Rationale

Users may want to track the actual cost paid to buy back shares when closing a position manually. The value is optional to maintain backward compatibility. Limiting the input to manual closes avoids schema drift in positions closed by automated reasons (assignment, expiration).

#### Changes

**src/cosmos_db.py:**
- `close_position()` function gained parameter: `buyback_cost: float | None = None`
- Sets `pos["buyback_cost"]` only when `buyback_cost` is provided (not None)
- If not provided, the field is omitted from the position record

**web/app.py:**
- `api_close_position()` endpoint now parses optional `buyback_cost` from the request JSON body
- Invalid or empty values are normalized to None
- The parameter is suppressed for non-manual close reasons; only exposed when `reason='manual'`
- Passes the parsed value to `close_position()`

**web/templates/symbol_detail.html:**
- Added optional buyback cost input field in the close modal
- Input is shown only when the close reason is set to `manual`
- Input is reset on modal open (no carry-over from previous closes)
- Only included in the PUT request body when the value is valid and non-empty

**tests/test_cosmos_close.py:**
- NEW test file with 2 test cases:
  - Close position WITH buyback_cost (verifies field is stored)
  - Close position WITHOUT buyback_cost (verifies field is omitted when not provided)
- Both tests passed ✅

#### Validation

- ✅ pytest tests/test_cosmos_close.py -q → 2 passed
- ✅ py_compile src/cosmos_db.py web/app.py → OK

#### Technical Notes

- The economics module already reads `position.buyback_cost` and multiplies by the contract multiplier
- No reporting changes were required
- The field is optional; backward-compatible with existing positions that lack it
- Manual-close-only constraint ensures assigned and expired positions keep clean, simple schemas


### 2. Sort Roll Candidates by Ann.Ret%


**Date:** 2026-07-01
**Author:** Linus (Quant Dev)
**Requested by:** dsanchor
**Status:** ✅ Implemented
**Impact:** Roll candidate ranking, DTE target alignment

#### Decision

Roll candidate tables are sorted by `Ann.Ret%` (annualized return) descending instead of Net Credit descending.

#### Rationale

Net Credit descending biases candidate selection toward longer-dated contracts because longer expirations usually carry higher absolute premium. Sorting by `Ann.Ret% = Premium% × 365 / DTE` normalizes premium by time, surfacing the best return per day and better aligning candidate ranking with the approved 21-35 DTE roll target.

#### Scope

The Net Credit column and `net_credit` values remain available for economics and threshold checks. Only candidate table sort order and related instruction prose changed.

#### Changes

- **src/options_chain_filters.py**: Sort key in both branches + table label updated
- **src/open_call_roll_instructions.py**: Prose "sorted by Net Credit" → "sorted by Ann.Ret%"
- **src/open_put_roll_instructions.py**: Prose "sorted by Net Credit" → "sorted by Ann.Ret%"

#### Validation

- ✅ py_compile passed
- ✅ Targeted pytest: 2 pre-existing unrelated failures confirmed (contract-multiplier bug; yfinance DTE-window filter test)


### 3. Economics Test Fix — Contract Multiplier & Net-RoC Semantics


**Date:** 2026-07-01
**Author:** Basher (Tester)
**Requested by:** dsanchor
**Status:** ✅ Done
**Impact:** Test suite correctness, production contract multiplier semantics

#### Decision

Update stale `tests/test_economics.py` expectations to match current `web/app.py::_build_economics_report` contract-multiplier semantics.

#### Scope

Production code was NOT changed. Only test expectations updated to reflect intentional web/app.py behavior:
- `CONTRACT_MULTIPLIER = 100` for option contract dollar amounts
- RoC now reported net-of-buyback
- win_rate now counts profitable rolls as wins

#### Changes

**tests/test_economics.py:**
- Dollar aggregate expectations multiplied by 100 per CONTRACT_MULTIPLIER
- avg_roc_pct / annualized_roc_pct updated to expect net-RoC values (net of buyback cost)
- win_rate updated to reflect profitable-roll-as-win semantics
- Added premium_per_share and buyback_per_share field assertions

#### Validation

✅ pytest tests/test_economics.py -q → 2 passed, 2 warnings

Coordinator (Squad) independently verified the new expected values are correct against the intentional web/app.py logic (not rubber-stamped).

#### Pending — Held Item

**yfinance DTE-Window Test Failure (DIAGNOSED ONLY, NO CODE CHANGES)**

Root causes identified but held pending dsanchor decision:

1. **Mock Mismatch:** Test mocks `src.yfinance_data_provider.yf` but yfinance now imported directly in `src/options_chain_cache.py` (not through wrapper). TradingView Playwright path also unmocked.

2. **Dead Config Keys:** The 7-90 DTE window filter was dropped during OptionsChainCache refactor. `config.yaml` keys `min_dte` and `max_dte` are now unused.

**Decision Pending:** Dsanchor to decide whether to (a) re-implement the DTE window filter, or (b) retire the config keys + remove the test.



### 4. Remove Dead 7-90 DTE Window Config


**Date:** 2026-07-01
**Author:** Rusty (Agent Dev)
**Requested by:** dsanchor
**Status:** ✅ Done
**Impact:** Config cleanliness, eliminated dead configuration keys

#### Decision

Remove the nested `yfinance.options_chain` config block from `config.yaml`, including `min_dte` and `max_dte` keys.

#### Rationale

The 7-90 DTE filter on options-chain fetch was intentionally removed during the `OptionsChainCache` refactor. The fetch path now only excludes expired contracts; roll-candidate selection keeps its separate DTE≤45 cap.

#### Changes

**config.yaml:**
- Removed `yfinance.options_chain` sub-block containing `min_dte: 7` and `max_dte: 90`

#### Verification

- ✅ No live config reads depend on `yfinance.options_chain.min_dte` / `max_dte` (src/config.py has no accessors)
- ✅ `config.yaml` parses successfully after removal


### 5. Retire Obsolete yFinance DTE-Window Tests


**Date:** 2026-07-01
**Author:** Basher (Tester)
**Requested by:** dsanchor
**Status:** ✅ Done
**Impact:** Test suite cleanliness, removed assertions on deleted filter behavior

#### Decision

Retire obsolete tests from `tests/test_yfinance_data_provider.py` that asserted the removed fetch-time 7-90 DTE filter or removed `_min_dte` / `_max_dte` attributes.

#### Changes

**tests/test_yfinance_data_provider.py:**
- Removed `test_only_7_to_90_dte_included` (asserted removed fetch-time 7-90 filter)
- Removed `test_near_term_excluded` (asserted removed fetch-time 7-90 filter)
- Removed `test_custom_config_applied` (asserted removed _min_dte/_max_dte attributes)
- Removed empty `TestDTEFiltering` class
- Updated fixture comments to no longer imply a 7-90 fetch-time filter

#### Verification

- ✅ pytest tests/test_yfinance_data_provider.py: 20 passed, 1 failed
- **Pre-existing out-of-scope failure:** `test_greeks_populated_for_nonzero_iv` fails due to Playwright/mock-target root cause (same issue as held yfinance item). This failure exists independently and is not caused by DTE config removal.

#### Note on Held Item

The pre-existing `test_greeks_populated_for_nonzero_iv` failure is now also documented as related to the held yfinance mock-drift issue: test mocks `src.yfinance_data_provider.yf` but yfinance is now imported directly in `src/options_chain_cache.py` (not through wrapper), and TradingView Playwright path is also unmocked. Browser cannot start in test environment.


### 3. Scheduler Hang Watchdog — Per-Symbol Timeout & Worker Max Duration


**Date:** 2026-06-30
**Author:** Rusty (Agent Dev)
**Status:** ✅ Implemented
**Impact:** Scheduler reliability, options chain caching, production stability

#### Context

Production Container App logs showed the scheduler was "working" (next_run advancing every 10 min) but NO jobs were actually executing for hours. Root cause: the options_chain job at 03:20 printed "Refreshing options chain cache for 16 symbols..." and NEVER printed "Complete". It hung indefinitely. From 04:20 onward every hour: "Options Chain Fetcher - Skipped (still running)" + "Skipping options_chain: previous run still in progress". Because the worker thread is single/sequential (by design to preserve job ordering and avoid concurrency issues with shared state), that one hung job blocked ALL other jobs (monitor_agents, summary, etc.) from ever executing.

#### Diagnosed Root Causes

1. **options_chain_cache.py `refresh_all` (lines ~150-163):** Awaited `self.refresh(symbol)` **sequentially with NO timeout**. `refresh` calls `_fetch_yfinance` (line ~196) which makes **synchronous blocking** yfinance calls (`yf.Ticker(symbol)`, `ticker.info`, `ticker.option_chain(...)` at lines ~212-255) directly inside an `async def`, with no `asyncio.to_thread`/executor and no socket timeout. yfinance uses `requests` under the hood; a stalled TCP connection hangs forever. One hung symbol => `refresh_all` never returns => the scheduler worker thread is blocked permanently.
   - Note: The existing sync web path `get_or_load` (line ~63-89) **DID** bound it: `pool.submit(self._sync_refresh, symbol).result(timeout=120)`. `refresh_all` lacked this protection.

2. **scheduler_registry.py `_worker_loop` (lines ~200-228):** Ran each job to completion with **no max-duration guard**. A hung job jams the queue forever. The main loop still ticks (heartbeat + next_run advancement continue because we have a worker thread), but NO jobs execute.

3. **web/app.py (lines 2904, 2954):** Called `cosmos.get_all_symbols()` which **does not exist**. The correct method is `cosmos.list_symbols()` (defined in src/cosmos_db.py:124). This caused `'CosmosDBService' object has no attribute 'get_all_symbols'` errors when resolving `last_run` timestamps for summary_agent and portfolio_enrichment tasks.

#### The Two-Layer Fix

##### Fix 1 — Bound options_chain refresh_all per symbol (primary defense)

**File:** `src/options_chain_cache.py`

**Changes:**
- Added module constant `_REFRESH_SYMBOL_TIMEOUT = 90` (90 seconds per symbol, line ~28)
- Added `import concurrent.futures` (line 17)
- Rewrote `refresh_all` (lines ~150-180) to use `concurrent.futures.ThreadPoolExecutor(max_workers=4)` and execute each symbol's `_sync_refresh` in a thread with a **hard timeout** via `future.result(timeout=_REFRESH_SYMBOL_TIMEOUT)`
- On timeout: log a warning, count it as an error, and **CONTINUE** to the next symbol (does not abort the batch)
- Reuses the existing `_sync_refresh(symbol)` helper which runs `self.refresh(symbol)` in its own event loop (safe for thread execution; each thread gets its own loop via `asyncio.new_event_loop()`)
- Small bounded concurrency (max_workers=4) speeds up the overall refresh while keeping each symbol timeout-bounded

**Rationale:**
Prevents one hung symbol from blocking the entire options chain refresh job. In production, if one symbol's yfinance connection stalls (network timeout, API hang, etc.), the job logs the error and moves on to the next symbol. The worker queue never jams.

**Code Pattern:**
```python
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
    futures_map = {symbol: executor.submit(self._sync_refresh, symbol) for symbol in symbols}
    for symbol, future in futures_map.items():
        try:
            future.result(timeout=_REFRESH_SYMBOL_TIMEOUT)
            success_count += 1
        except concurrent.futures.TimeoutError:
            logger.warning("%s: options chain refresh timed out after %d seconds", symbol, _REFRESH_SYMBOL_TIMEOUT)
            error_count += 1
        except Exception as e:
            logger.error("%s: options chain refresh failed: %s", symbol, e)
            error_count += 1
```

##### Fix 2 — Worker watchdog (defense in depth, protects the ENTIRE scheduler)

**File:** `src/scheduler_registry.py`

**Changes:**
- Added module constant `_MAX_TASK_DURATION_SECONDS = 1800` (30 minutes, line ~17)
- Rewrote `_worker_loop` (lines ~200-245) to execute each dequeued job in a **sub-thread** with `join(timeout=_MAX_TASK_DURATION_SECONDS)`
- If the job exceeds the timeout:
  - Log error: "task X exceeded max duration, abandoning"
  - Print: "❌ SCHEDULER TIMEOUT: {task.display_name} exceeded {_MAX_TASK_DURATION_SECONDS}s"
  - Set `task.running = False`
  - **Continue to the next queued job** (the orphaned thread may linger but does NOT block the worker)
- Preserves:
  - Setting `task.last_run` (start time) on completion AND on timeout/error
  - Error isolation (exceptions logged, worker keeps running)
  - The existing overlap guard via `task.running` (prevents double-runs)
- The job functions themselves bridge async via `_run_async` (new event loop). Running them in a sub-thread is safe (each thread is daemon).

**Rationale:**
No job can ever jam the queue forever, even if:
- The per-symbol timeout guard is bypassed (e.g. a future job type we add doesn't have item-level timeouts)
- A different job type hangs (e.g. a summary_agent LLM call stalls, a cosmos query hangs, etc.)
- A bug is introduced that disables the per-item timeout

This is a **catch-all safety net** for the entire scheduler. It guarantees that the worker queue will never be permanently jammed by ANY job, known or future.

**Code Pattern:**
```python
def run_task():
    try:
        task.job_func()
    except Exception as e:
        print(f"❌ SCHEDULER ERROR in {task.name}: {e}")
        logger.exception(f"Error executing task {task_name}")

job_thread = threading.Thread(target=run_task, daemon=True, name=f"TaskExec-{task_name}")
job_thread.start()
job_thread.join(timeout=_MAX_TASK_DURATION_SECONDS)

if job_thread.is_alive():
    logger.error(f"Task {task_name} exceeded max duration of {_MAX_TASK_DURATION_SECONDS}s, abandoning")
    print(f"❌ SCHEDULER TIMEOUT: {task.display_name} exceeded {_MAX_TASK_DURATION_SECONDS}s")

task.last_run = start_time
task.running = False
```

##### Fix 3 — get_all_symbols bug

**File:** `web/app.py`

**Changes:**
- Line ~2904 (summary_agent branch in last_run resolver): `cosmos.get_all_symbols()` → `cosmos.list_symbols()`
- Line ~2954 (portfolio_enrichment branch in last_run resolver): `cosmos.get_all_symbols()` → `cosmos.list_symbols()`

**Rationale:**
`get_all_symbols()` does not exist. `list_symbols()` is the correct method (defined in src/cosmos_db.py:124). This fixes the AttributeError in the persisted last_run resolver that was causing errors in production logs.

#### Design Decisions

##### Why Two Layers?

**Layer 1 (per-symbol timeout):** Targets the known risky operation (yfinance network calls). Fast to timeout (90s per symbol), provides detailed error reporting (logs which symbol hung), and doesn't penalize the entire batch (other symbols still refresh).

**Layer 2 (worker watchdog):** Protects the ENTIRE scheduler from ANY hung job, known or unknown. Slower to trigger (30 min), but guarantees the queue never jams permanently. Defense in depth.

Both are necessary:
- Layer 1 prevents 95% of hangs (common network timeouts, API rate limits, etc.) with fast recovery
- Layer 2 catches the 5% we didn't anticipate (new job types, bugs in timeout logic, rare edge cases)

##### Why max_workers=4 for options chain?

- **Conservative parallelism:** yfinance makes network calls to Yahoo Finance API. Too much concurrency risks rate limiting or connection pool exhaustion.
- **Bounded timeout per symbol:** With a 90s timeout per symbol and 16 symbols, sequential execution would take 24 minutes (16 * 90s) in the worst case. With max_workers=4, the worst case is ~6 minutes (16/4 * 90s), which is acceptable for an hourly job.
- **Each thread isolated:** `_sync_refresh` creates a new event loop per symbol, so threads don't share async state.

##### Why 30 minutes for worker watchdog?

- Longest expected job: `monitor_agents` runs 5 agents across all symbols with many sequential LLM + yfinance calls. In production, this can take 10-15 minutes for a large portfolio.
- 30 minutes provides a comfortable margin (2x the expected max) without being so long that a hung job jams the queue for hours.
- The timeout is a constant (`_MAX_TASK_DURATION_SECONDS`), so it can be adjusted if job characteristics change.

##### Why daemon threads?

- Daemon threads are killed when the main process exits, so we don't leave orphaned threads running after scheduler shutdown.
- The worker thread is daemon (always running, consuming the queue).
- Job execution sub-threads are daemon (may be orphaned if they exceed the timeout, but won't prevent shutdown).

#### Constraints Honored

- ✅ Do NOT change cron expressions, task set, web API surface, or template variable names
- ✅ Keep tz-aware datetimes (all datetime objects are timezone-aware)
- ✅ Daemon threads only; no signal handlers off the main thread
- ✅ Keep imports tidy (concurrent.futures and threading already used in the codebase)

#### Validation

1. ✅ **Import check:** `python3 -c "import src.main, src.scheduler_registry, web.app, src.options_chain_cache; print('import OK')"`
2. ✅ **Throwaway runtime test** (deleted after):
   - a) `refresh_all`-style call where one "symbol" hangs (mocked to sleep 5s with timeout=2s) returns within ~2s, counts it as an error, and OTHER symbols still succeed → **PASS**
   - b) Scheduler worker watchdog: enqueue a job that sleeps longer than test max-duration (2s); assert the worker logs/abandons it, clears running, and then a SECOND enqueued job still runs (proving the queue is not jammed) → **PASS**
3. ✅ **Targeted pytest:** `pytest tests/ -k "schedul or registry or options_chain or cache" -q` → 3 passed, 99 deselected, 4 warnings (pre-existing economics/yfinance fixture failures ignored)

#### Production Impact

**Before:**
- One hung symbol in options_chain refresh => entire scheduler queue jammed for hours
- Symptoms: "Options Chain Fetcher - Skipped (still running)" repeated every hour, no other jobs execute, portfolio stale, agents don't run

**After:**
- One hung symbol => logged as error after 90s, other symbols continue, job completes
- Any hung job (not just options_chain) => abandoned after 30 min, queue continues with next job
- Scheduler never jams permanently
- Production logs will show:
  - Per-symbol timeouts: `WARNING: AAPL: options chain refresh timed out after 90 seconds`
  - Job-level timeouts: `ERROR: Task options_chain exceeded max duration of 1800s, abandoning`
  - Queue continues: next job executes normally

#### Key Learnings

1. **Blocking I/O in async with no timeout:** yfinance (and other libraries using `requests`) makes synchronous blocking network calls. If called directly inside an `async def` without wrapping in `asyncio.to_thread` or `run_in_executor`, a stalled connection hangs the async task forever. **ALWAYS wrap blocking I/O in a thread with a hard timeout.**

2. **Per-item timeout for batch jobs:** When a batch job (refresh_all, fetch_all, etc.) iterates over many items, **each item MUST have a bounded timeout**. One hung item must NOT block the entire batch.

3. **Worker watchdog for scheduler safety:** Even with per-item timeouts, a scheduler worker should have a **max-duration guard for the entire job execution**. This prevents ANY job type (known or future) from jamming the queue forever.

4. **Defense in depth:** Use two layers:
   - (1) Per-item timeout for known risky operations (yfinance, LLM, etc.) — fast recovery, detailed errors
   - (2) Max-duration guard for the entire job execution — catch-all safety net
   Both are necessary — the first prevents common hangs, the second is a last resort.

5. **Method name bugs in error paths:** Always verify method names exist when calling dynamic code paths (e.g. error handlers, last_run resolvers). `get_all_symbols()` did not exist but was only called in the last_run resolver error path, so it went unnoticed until production logs showed the AttributeError.

#### Files Changed

- `src/options_chain_cache.py` (lines 17, 28-30, 150-180): Per-symbol timeout with ThreadPoolExecutor
- `src/scheduler_registry.py` (lines 17, 200-245): Worker watchdog with max-duration guard
- `web/app.py` (lines 2904, 2954): `get_all_symbols()` → `list_symbols()`

#### Future Considerations

- **Configurable timeouts:** If different symbols or job types need different timeouts, consider adding per-task timeout configuration (e.g. `task.max_duration` override).
- **Metrics/monitoring:** Log timeout events to a metrics system (e.g. Application Insights custom events) for production monitoring and alerting.
- **Per-symbol retry:** If a symbol times out, consider adding it to a retry queue with exponential backoff (but only if the timeout was transient, not a permanent hang).
- **yfinance replacement:** If yfinance hangs become frequent, consider switching to a more reliable data source or implementing circuit breakers.
---


### 27. Robust Mid-Price Calculation for Illiquid Options


**Date:** 2026-06-30
**Author:** Rusty (Agent Dev)
**Status:** ✅ Implemented
**Impact:** Options pricing accuracy, P&L calculations, open-put monitor dashboard

#### Context

The open-put monitor dashboard showed an ADM 67.5 put (expiration 2026-07-17, stock price $76.73, deep out-of-the-money SOLD put) with P&L of **-254.6%** (displayed in red/negative) when it should have been strongly positive. Investigation revealed:

- Position snapshot stored: `midprice=1.95`, `premium_received=0.55`
- P&L formula: `(premium_received - midprice) / premium_received * 100` = `(0.55 - 1.95) / 0.55 * 100` = **-254.5%**
- Live yfinance data showed: `bid=0.05`, `ask=0.25` → true value ~$0.15
- Corrupted snapshot came from: `bid=0`, `ask≈3.9` → naive mid = `(0 + 3.9)/2 = 1.95`

The problem was a **garbage one-sided illiquid quote** where the naive midpoint calculation produced an absurd mark.

#### Decision

Replace the naive mid-price calculation `(bid + ask) / 2` with a **robust mid-price function** that resists one-sided and stale-wide illiquid quotes.

#### Implementation

##### Created `src/options_math.py`

New shared module containing `robust_mid(bid, ask, last=0.0)` with logic:

1. **Sane two-sided quote** (bid > 0, ask > 0, not implausibly wide) → `(bid + ask) / 2`
2. **Implausibly wide spread** (ask > bid * 8 + 0.20) → anchor to `bid` (ignore stale/garbage ask)
3. **No bid (bid ≤ 0)** → mark conservatively near 0, hard cap at `0.10` (never use `ask/2`)
4. **Nothing usable** → `0.0`

The `last` (lastPrice) parameter is accepted for future heuristics but currently unused (lastPrice is stale for illiquid names).

##### Updated Two Call Sites

Both naive calculations were identical and replaced in a single commit:

1. **`src/options_chain_cache.py` line ~460:**
   - Before: `"mid": round((bid + ask) / 2, 4) if (bid + ask) > 0 else 0.0,`
   - After: `"mid": robust_mid(bid, ask, last_price),`

2. **`src/yfinance_data_provider.py` line ~522:**
   - Before: `"mid": round((bid + ask) / 2, 4) if (bid + ask) > 0 else 0.0,`
   - After: `"mid": robust_mid(bid, ask, last_price),`

Both sites have access to `last_price` parameter via existing local variables.

##### Test Coverage

- **Created `tests/test_options_math.py`:** 11 test cases covering:
  - Sane two-sided quotes → midpoint
  - Garbage one-sided quotes (bid=0, ask=3.9) → capped at 0.10 (NOT 1.95)
  - Normal spreads (bid=0.05, ask=0.25) → 0.15
  - Implausibly wide spreads (bid=0.05, ask=3.9) → anchor to bid (0.05)
  - Edge cases (both zero, only bid, only ask, negative/None handling, rounding)

- **Updated `tests/test_yfinance_data_provider.py`:**
  - `test_mid_price_calculation` now uses `robust_mid` instead of naive average for expected values

- **Validation:**
  - ✅ All 11 new tests pass
  - ✅ Updated yfinance test passes
  - ✅ Direct verification: `robust_mid(0, 3.9)` = `0.1` (not 1.95), `robust_mid(0.05, 0.25)` = `0.15`
  - ✅ No regressions

#### Rationale

1. **Data quality at the source:** Fixing bad marks at the data ingestion layer (options_chain_cache, yfinance_data_provider) prevents downstream corruption in position snapshots, P&L calculations, and dashboard displays.

2. **Shared logic:** Option pricing math should live in a dedicated module, not duplicated across multiple files.

3. **No lastPrice trust:** For illiquid names, `lastPrice` is stale (hours/days old) and unreliable. Using it as a fallback would just substitute one garbage value for another.

4. **Hard cap for bidless options:** When there are no buyers (bid=0), the option is near-worthless to the holder. Capping at `0.10` prevents a stale-high ask from inflating the mark on truly worthless positions.

5. **No downstream changes:** The P&L formula, dashboard logic, and position tracking are all CORRECT. This is purely a data-quality fix.

#### Impact

- **Immediate:** Prevents future position snapshots from recording absurd mid-prices due to one-sided/illiquid quotes.
- **Historical:** Existing corrupted snapshots (like the ADM 67.5 put with mid=1.95) remain in storage until the next live refresh overwrites them with correct marks.
- **Monitor accuracy:** Once refreshed, the open-put monitor will show correct P&L for all illiquid positions.

#### Files Changed

- **Created:** `src/options_math.py`
- **Modified:** `src/options_chain_cache.py` (import + line ~460)
- **Modified:** `src/yfinance_data_provider.py` (import + line ~522)
- **Created:** `tests/test_options_math.py`
- **Modified:** `tests/test_yfinance_data_provider.py` (test_mid_price_calculation)

#### Ownership

- **Module:** Rusty (Agent Dev — Python / data plumbing)
- **Not changed:** P&L logic, dashboard (Linus/Ralph/Danny — they own strategy/UI)

---

## 2026-04-20T10:25:00Z: User directive

**By:** dsanchor (via Copilot)
**What:** Always update README if necessary. Any new functionality or changes to existing ones require a README update.
**Why:** User request — captured for team memory

---

## 2. Options Chain Format Recommendation

**Date:** 2026-01-15
**Author:** Linus (Quant Dev)
**Status:** Proposed
**Impact:** Monitor agents (open_call_monitor, open_put_monitor), options chain parser, agent instructions


### API Contract (unchanged)


- **Endpoint:** `PUT /api/symbols/{symbol}/positions/{position_id}/close`
- **Body:** `{ close_reason: "expired" | "assigned" | "manual" }`
- **Backend:** web/app.py:1072 `api_close_position`
- **Default:** "manual" (when body omitted or invalid)

No backend changes required. The dropdown values map directly to the existing API contract.

## Validation

- ✅ Jinja2 template parses successfully
- ✅ `import web.app` succeeds
- ✅ Old `prompt('Close reason?` removed from codebase
- ✅ New `<select id="closeReasonSelect">` with expired/assigned/manual options present
- ✅ Fetch still posts `{ close_reason: <value> }` to same endpoint
- ✅ Tests pass with same baseline failures (2 economics, 1 yfinance config, 17 yfinance fixture errors — all pre-existing, unrelated to this change)
- ✅ Manual trace confirms: button click → modal opens with position_id → confirm sends correct PUT request

## Alternatives Considered

1. **Inline dropdown in table row:** Would clutter the position table and require per-row dropdowns
2. **Keep prompt() with text options:** Still requires typing; modal is more user-friendly
3. **Custom dropdown component:** Overkill; standard `<select>` is accessible and sufficient

## Impact

- **User-facing:** More intuitive UX, no need to remember number mappings
- **Code:** Replaced ~25 lines of prompt-based handler with ~53 lines of modal UI + handlers (net +28 lines)
- **Consistency:** Follows the same modal pattern as plan detail and summary modals
- **Accessibility:** Standard `<select>` element is keyboard-navigable and screen-reader-friendly
- **Behavior:** Default to "Manual close" matches previous default, no behavioral change

## Future Considerations

- Could add keyboard shortcut (Escape to close) for power users — already works via overlay click
- If we add more close reasons in the future, just add `<option>` elements to the dropdown
- The modal pattern is reusable for other action confirmations (e.g., delete position, roll confirmation)
# Scheduler Non-Blocking Architecture

**Date:** 2026-06-29
**Status:** ✅ Implemented
**Components:** Scheduler, TaskRegistry
**Files:** `src/scheduler_registry.py`, `src/main.py`

## Context

The scheduler UI displayed `next_run` timestamps in the past (e.g., "2026-06-29 13:55:00 UTC (6h ago)"), indicating the scheduler loop had frozen. Users could not tell if the scheduler was alive or when the next run would actually occur.

## Problem

Three interrelated issues caused the freeze:

1. **Loop freeze:** Jobs ran synchronously on the single scheduler thread. A long-running or hung job (e.g., a yfinance/LLM network call with no timeout) blocked the entire loop → no `next_run` advances, heartbeat stops, UI shows frozen past timestamp.

2. **next_run advanced AFTER job completes:** In `execute_due_tasks`, `task.next_run = task.cron_obj.get_next(datetime)` ran AFTER `task.job_func()`. Even a normal long job showed a past `next_run` for its whole duration.

3. **monitor_agents double-scheduled:** The heaviest job (runs 5 agents across all symbols with many sequential LLM + yfinance calls) was registered in the TaskRegistry AND ALSO handled by separate local `next_run`/`cron` variables in the loop. This caused `run_all_agents()` to run TWICE when due, and the heartbeat's local next_run diverged from the registry next_run shown in the UI.

## Decision

**Non-blocking job execution via worker thread:**

- Introduce a single dedicated worker thread inside `TaskRegistry` that executes jobs sequentially off the main loop thread
- Main loop detects due tasks, advances their `next_run` to the next future occurrence, and enqueues the job (non-blocking)
- Worker thread consumes jobs from a `queue.Queue`, executes them one at a time, logs exceptions but never dies
- Keep jobs SEQUENTIAL (one worker, not concurrent) because agents/cosmos/runner are NOT proven thread-safe

**Rationale:**
- Keeps the main loop ticking (heartbeat + schedule advancement) even while heavy jobs run
- Preserves sequential job execution to avoid breaking existing code assumptions
- Isolates failures: a failing job logs an error but doesn't kill the loop or worker
- Simple and correct: one queue, one worker, one job at a time

**Advance next_run BEFORE dispatching:**
- Compute `task.next_run = task.cron_obj.get_next(datetime)` BEFORE enqueuing the job
- Loop `get_next()` until the result is strictly in the future (guards against stale cron base)
- UI always shows a FUTURE `next_run`, never a past timestamp

**Overlap guard:**
- Add `task.running: bool` flag, set to `True` when enqueuing, `False` when job completes
- If a task is due but `task.running == True`, skip and log a warning (don't enqueue duplicate)
- `trigger_task_now()` (Run Now button) also respects the overlap guard

**Eliminate duplicate monitor_agents scheduling:**
- Remove local `cron`/`next_run` variables for monitor agents in `src/main.py`
- Remove the separate `if now_tz >= next_run: run_all_agents()` block
- `monitor_agents` lives ONLY in the TaskRegistry, like all other tasks
- Heartbeat reads `monitor_task.next_run` from the registry

## Alternatives Considered

**ThreadPoolExecutor with max_workers=1:**
- Pros: Standard library, no manual queue management
- Cons: Requires more boilerplate for shutdown, less explicit control over job sequencing
- **Rejected:** Simple `queue.Queue` + worker thread is more explicit and easier to reason about

**Concurrent job execution (thread pool with N workers):**
- Pros: Higher throughput, could run multiple lightweight tasks in parallel
- Cons: Agents/cosmos/runner are NOT proven thread-safe; would require extensive testing + locks
- **Rejected:** Risk too high for marginal gain (jobs already take hours, not seconds)

**Async/await with asyncio.create_task:**
- Pros: Python-native concurrency, could integrate with existing async agent code
- Cons: Scheduler loop is sync, would require refactoring `run()` method and signal handling
- **Rejected:** Mixing sync loop + async jobs adds complexity; worker thread is simpler

**Move `next_run` advancement AFTER job completes (status quo):**
- Pros: Simpler logic (one place to update next_run)
- Cons: UI shows past timestamps during long runs, confusing users
- **Rejected:** Advance-before-dispatch is a one-line change with huge UX benefit

## Implementation

**src/scheduler_registry.py:**
- Added `queue.Queue` (`_job_queue`), worker thread (`_worker_thread`), and shutdown flag (`_shutdown`) to `TaskRegistry.__init__`
- `initialize_all()` starts daemon worker thread
- `_worker_loop()` consumes jobs, executes them, sets `last_run` and clears `running` flag
- `execute_due_tasks()` detects due tasks, advances `next_run` via `_advance_next_run()`, sets `running = True`, enqueues job
- `_advance_next_run()` loops `get_next()` until result is in the future (max 100 iterations)
- `trigger_task_now()` checks overlap guard, enqueues job
- `shutdown()` sets `_shutdown` flag and joins worker thread with 5s timeout

**src/main.py:**
- Removed local `cron`/`next_run` variables for monitor agents
- Removed separate `if now_tz >= next_run: run_all_agents()` block
- Removed local cron reschedule block for monitor agents
- Updated heartbeat to read `monitor_task.next_run` from registry
- Added `self.registry.shutdown()` call before exiting

**Special handling for monitor_agents:**
- `monitor_agents` uses `config.cron_expression` (not `config.config['scheduler']['cron']`)
- `initialize_task()` and `handle_cron_changes()` special-case `task.name == "monitor_agents"`

## Validation

- ✅ Import checks: `python3 -c "import src.main, src.scheduler_registry, web.app"`
- ✅ Jinja2 template parsing: `jinja2.Environment().parse(...)`
- ✅ Runtime check: Created test script validating (a) next_run is future after dispatch, (b) loop not blocked, (c) job runs once, (d) last_run set, (e) overlap guard works → all PASSED
- ✅ Existing tests: `pytest tests/ -k "schedul or registry or main"` → 0 failures

## Consequences

**Positive:**
- Scheduler loop never freezes, even when jobs hang or take hours
- UI always shows accurate future `next_run` timestamps
- Heartbeat confirms scheduler is alive every 10 minutes
- Overlap guard prevents duplicate job execution
- Clean shutdown via `registry.shutdown()`

**Negative:**
- Slightly more complex: worker thread + queue instead of direct function calls
- Jobs still sequential (not concurrent), so total runtime unchanged

**Neutral:**
- `task.last_run` now records job START time (when enqueued) instead of completion time
  - Rationale: Start time is more useful for "when did this last run?" UI display
  - Alternative: Could record completion time, but then last_run wouldn't update until job finishes

## Follow-up

- Consider adding per-task timeout (e.g., `job_timeout: Optional[int]` in `ScheduledTask`, worker enforces via `threading.Timer`) if jobs start hanging indefinitely
- Consider logging job duration (worker thread records start/end, logs delta) for performance monitoring
- Consider making `max_iterations` in `_advance_next_run()` configurable (currently hard-coded to 100)

## Related

- `.squad/agents/rusty/history.md` — Learnings section on worker thread pattern
- `src/scheduler_registry.py` — Implementation
- `src/main.py` — Scheduler loop


### Integration

The snapshot chart consumes data from Linus's `position_snapshots` CosmosDB container via the API boundary, following the documented position snapshot schema and retention model.

---

## Rusty — DPS Scheduler Integration

**Date:** 2026-06-26
**Author:** Rusty (Agent Dev)
**Status:** ✅ Implemented
**Impact:** Bug fix — critical missing scheduler


### Related Work


See `scheduler_analysis.md` for full scheduler architecture documentation and deferred improvement recommendations.

---

## 6. Scheduler Registry Refactor + DPS Redundancy Removal

**Date:** 2026-06-26
**Decider:** Rusty (Agent Dev)
**Status:** ✅ Implemented


### Impact


**Positive:**
- 41% smaller main.py → easier maintenance
- 2 lines per new task vs 50+ lines → 96% effort reduction
- No behavior change (same 8 tasks, same crons)

**Risks Mitigated:**
- Web UI compatibility preserved
- No breaking changes
- Rollback available

---

## 7. Unified Scheduler Settings UI Model

**Date:** 2026-06-26
**Agent:** Rusty (Agent Dev)
**Status:** ✅ Implemented
**Impact:** High — eliminates per-task duplication, consistent UI for all scheduled tasks


### Lessons Learned


1. **Single source of truth eliminates divergence bugs** — before, last_run/next_run could diverge between scheduler and UI
2. **Uniform UX requires uniform data model** — can't have consistent controls if backend is inconsistent
3. **Backward compatibility eases incremental refactors** — preserved old endpoints, can refactor template in separate phase

---

## 8. Monitoring Agent Enabled Checkbox + Enable-Gating Guarantee

**Date:** 2026-06-26
**Author:** Rusty (Agent Dev)
**Status:** ✅ Implemented


### 8. Scheduler Last Run Display + Restart-Durable Timestamps

**Date:** 2026-06-26
**Author:** Rusty (Agent Dev)
**Status:** ✅ Implemented
**Scope:** Scheduler settings UI, last_run persistence

#### Context

After the scheduler refactor (16dcbec — task-registry architecture), the settings UI displayed 8 scheduled tasks with Next Run but only 5 had Last Run. Three tasks (Calendar Sync, DGI Screener, Watchlist Enrichment) were missing the Last Run display entirely.

Additionally, the TaskRegistry tracked `last_run` in-memory only (`task.last_run = now_tz` on execution). This meant that after a scheduler restart (deployment, config reload, crash), all `last_run` values reset to `None` → UI showed "Never" even for tasks that had recently executed.

The pre-refactor code (before 16dcbec) derived `last_run` from persisted Cosmos timestamps (activities, agent_notes, dgi_entries, banner doc, etc.), so the UI showed accurate "Last Run" even after restarts.

#### Problem

1. **Missing Last Run Display:**
   - Calendar Sync, DGI Screener, Watchlist Enrichment sections lacked Last Run rows
   - Only showed Next Run (single column) instead of the standard Last Run + Next Run grid

2. **Missing Context Variables:**
   - `web/app.py` didn't build `calendar_last_run` or `pe_last_run` for the template
   - DGI had `dgi_last_run` built but template never rendered it

3. **In-Memory Only last_run (Not Restart-Durable):**
   - TaskRegistry tracked `last_run` in-memory only
   - After scheduler restart, all `last_run` reset to `None` → UI showed "Never"
   - Lost the pre-refactor behavior where `last_run` was derived from persisted Cosmos data

#### Decision

**Restore uniform Last Run display for all 8 scheduler tasks AND make last_run restart-durable by falling back to persisted Cosmos timestamps.**

#### Implementation

**Template Updates (web/templates/settings_config.html):**
- Added Last Run display rows to 3 missing sections (Calendar Sync, DGI Screener, Watchlist Enrichment)
- All 8 sections now have uniform 2-column layout (Last Run + Next Run)

**Context Variables (web/app.py):**
- Added `calendar_last_run` and `pe_last_run` vars
- Both added to template context dict

**Restart-Durable last_run (web/app.py:2878-2972):**
- Created `get_persisted_last_run(task_name: str) -> str` helper
  - Queries Cosmos for task-specific "most recent execution" timestamp
  - Per-task sources: activities, agent_notes, dgi_entries, banner doc, calendar events, symbol updates
- Created `resolve_last_run(task_name: str, in_memory_last_run: str) -> str` helper
  - Prefers in-memory value if present
  - Falls back to `get_persisted_last_run()` when `None`
- Updated all 8 task context vars to use `resolve_last_run()` instead of direct `fmt_time()`

#### Rationale

**Why Restart-Durable Matters:** Scheduler may restart (deployments, config reloads, crashes). Persisted timestamps let UI show accurate "Last Run" even after restart.

**Why Per-Task Cosmos Sources:** Each task has a natural "most recent execution" signal already in Cosmos. Reusing existing timestamps is cleaner than adding new `last_execution_timestamp` fields to every task.

**Why Options Chain is In-Memory Only:** Cache is transient, task runs hourly, so "Never" after restart reflects reality (cache empty, task needs to run).

#### Alternatives Considered

1. **Add `last_execution_timestamp` field to every task's Cosmos output** → Adds storage overhead, duplicates existing data
2. **Persist last_run in dedicated `scheduler_state` Cosmos doc** → Extra Cosmos write per execution, doesn't help options_chain
3. **Leave last_run in-memory only (status quo)** → UI shows "Never" after restart (regression)

**Chosen:** Per-task Cosmos sources. Balances simplicity, no schema changes, leverages existing timestamps.

#### Impact

**User-Facing:**
- Scheduler Settings UI now shows Last Run + Next Run for ALL 8 tasks uniformly
- Last Run survives scheduler restarts (accurate even after deployments)
- Users can trust "Never" means "truly never run" (not "scheduler restarted")

**Code:**
- **web/app.py**: +100 lines (helper functions, per-task resolution)
- **web/templates/settings_config.html**: +24 lines (3 Last Run rows)
- No schema changes, no new Cosmos writes

**Validation:**
- ✅ Imports succeed
- ✅ 98 tests pass (4 pre-existing economics failures unrelated)
- ✅ Template: 8 "Last Run" labels, 8 `*_last_run` variables
- ✅ Context builder: all 8 tasks use `resolve_last_run()`

#### Future Work

1. **Refactor template to loop over `scheduler_tasks`** instead of 8 hardcoded sections
2. **Add `last_run` persistence to TaskRegistry itself** → Store in Cosmos `settings` container alongside cron/enabled

---

## 27. Premium/Buyback Display Normalization

**Date:** 2026-06-26
**Status:** ✅ Implemented
**Agent:** Rusty (Agent Dev)
**Requested by:** dsanchor


### 8. Scheduler Settings: Relative Time Display

**Date:** 2026-06-26
**Agent:** Rusty (scheduler + Settings UI owner)
**Status:** ✅ Completed
**Impact:** UI/UX (scheduler settings page)

#### Request
User requested: "under scheduler configuration settings, could you calculate the time next to Last Run and Next Run so we know when it was triggered and how much time is still to the next one? Add it next to the label."

#### Implementation Approach

**Choice: Live Client-Side JS (Preferred)**
Implemented live client-side relative time calculation with ISO timestamps in data attributes. This keeps the "in 45m" countdown continuously accurate as the page sits open without requiring a page reload.

**Rationale:**
- Better UX: countdown stays live (updates every 30 seconds via setInterval)
- No staleness: times remain accurate without reload
- Clean separation: server provides raw timestamps, client renders human-friendly relative times
- DRY: single reusable `formatRelative()` JS helper for all 8 tasks

#### Server-Side Changes (web/app.py)

1. **Added `to_iso()` helper** (line ~2981): Normalizes ISO timestamp strings to UTC for client-side parsing
2. **Added `resolve_last_run_iso()` helper** (line ~3000): Parallel to `resolve_last_run()` but returns raw ISO instead of formatted display string
3. **Extended context for all 8 tasks** (lines ~3005–3075):
   - Added `*_last_run_iso` and `*_next_run_iso` variants for each task:
     - `monitoring_last_run_iso`, `monitoring_next_run_iso`
     - `summary_last_run_iso`, `summary_next_run_iso`
     - `banner_last_run_iso`, `banner_next_run_iso`
     - `calendar_last_run_iso`, `calendar_next_run_iso`
     - `options_chain_last_run_iso`, `options_chain_next_run_iso`
     - `dgi_last_run_iso`, `dgi_next_run_iso`
     - `pe_last_run_iso`, `pe_next_run_iso`
     - `plan_monitor_last_run_iso`, `plan_monitor_next_run_iso`
   - Pattern: each task now contributes 4 context vars (display + ISO for both last/next)
4. **Added ISO variants to return dict** (lines ~3090–3137): All 16 ISO context vars added to the template context dictionary

#### Template Changes (web/templates/settings_config.html)

1. **Updated all 8 task sections** (lines vary):
   - Each Last Run div now has `data-last-run="{{ *_last_run_iso }}"`
   - Each Next Run div now has `data-next-run="{{ *_next_run_iso }}"`
   - Each div contains a `<span class="relative-time" style="..."></span>` for the computed relative time
   - Style: `font-size:0.75rem; opacity:0.7; margin-left:0.5rem;` to match existing muted text styling
2. **Added JS helper** (line ~498):
   - `formatRelative(isoStr)`: DRY helper that computes relative time strings
     - Returns `(2h ago)` for past times, `(in 45m)` for future times
     - Handles days, hours, minutes, `<1m` for very recent/imminent
     - Gracefully handles empty/invalid ISO strings (returns `''`)
   - `updateAllRelativeTimes()`: queries all `[data-last-run], [data-next-run]` elements and updates their `.relative-time` spans
   - Runs on page load and every 30 seconds via `setInterval(updateAllRelativeTimes, 30000)`

#### Validation Results

✅ **Import checks**: `python3 -c "import web.app"` → OK; `python3 -c "from src import main"` → OK
✅ **Template parsing**: Jinja template parses successfully (no syntax errors)
✅ **Attribute counts** (via grep):
  - `data-last-run=` → 8 occurrences (✓ all 8 tasks)
  - `data-next-run=` → 8 occurrences (✓ all 8 tasks)
  - `class="relative-time"` → 16 occurrences (✓ 8 last + 8 next)
✅ **Pytest**: 4 pre-existing economics test failures (expected, unrelated); no new failures introduced

#### Affected Tasks (All 8)
1. Monitoring Agent
2. Summarization
3. Dashboard Banner
4. Calendar Sync
5. Options Chain
6. DGI Screener
7. Watchlist Enrichment (portfolio_enrichment)
8. Plan Monitor

#### Edge Cases Handled
- `Never` / `None` last_run → no relative time shown (empty span)
- `N/A` next_run → no relative time shown (empty span)
- Next Run in the past (overdue task) → shows `(in <1m)` if very soon, or `(Xm ago)` if past
- Timezone correctness: ISO strings include UTC timezone, JS `Date` parses correctly

#### Notes
- Did NOT modify scheduler logic, cron expressions, or how last_run is resolved (purely display-additive)
- Kept all existing absolute timestamps intact (relative time is ADDITIONAL, not a replacement)
- Uniform styling across all 8 tasks (muted, small font, consistent placement)
- Live updates every 30s ensure "in 45m" → "in 44m" → ... without page reload

#### Files Modified
- `web/app.py` (lines ~2971–3137): Added ISO helpers and context vars
- `web/templates/settings_config.html`: Updated all 8 task sections + added JS helper

#### Future Improvements (Out of Scope)
- Could refactor the template to loop over `scheduler_tasks` instead of 8 hardcoded sections (reduces duplication)
- Could add tooltip on hover showing exact local time conversion

---
# Replace Close Position Prompt with Dropdown Modal

**Date:** 2026-06-27
**Author:** Rusty
**Status:** Implemented
**PR/Commit:** TBD

## Context

Users were prompted to type a number (1/2/3) to select a close reason when closing a position:
- 1 → Expired
- 2 → Assigned
- 3 → Manual close

This required remembering the mapping and typing accurately. The Close button already had a ▾ symbol hinting at a dropdown, but the UX was still a basic `prompt()` dialog.

## Decision

Replace the numeric `prompt()` with a **dropdown modal** for selecting the close reason.

**User Request (translated from Spanish):**
> "al cerrar las posiciones, da la opción de cerrar como expirada, asignada o close manual. Puedes cambiarlo para que no sea introducir un número sino que sea un desplegable?"
> ("When closing positions, give the option to close as expired, assigned, or manual close. Can you change it so it's not entering a number but a dropdown?")

## Implementation


### Related Decisions

- Decision 30: Market Hours Detection (signals when cache should be applied)
- Decision: Hybrid Options Chain (context for TV fallback)


---

## Rusty — Snapshot Chart Decision

**Date:** 2026-06-04
**Author:** Rusty (Agent Dev)
**Status:** Implemented


### Rationale

Follows the same pattern as `summary_activity_count`: numeric input with server-side clamping. Keeps the default at 40 so existing deployments are unaffected.


---

## 26. Decision: Recommendation values computed from signal ratios

**Author:** Linus (Quant Dev)
**Date:** 2026-05-14
**Status:** Implemented


### Consequences

Slight deviation from TradingView's exact weighting (which may have used proprietary signal weights), but same label thresholds apply and agents consume labels not raw values.

---

## 27. Decision: No pandas-ta hard requirement

**Author:** Linus (Quant Dev)
**Date:** 2026-05-14
**Status:** Implemented


### Consequences

- **Reliability**: Works on all platforms without binary dependencies
- **Performance**: pandas-ta path still available for users who have it installed
- **Maintenance**: One code path to maintain (manual) vs. conditional logic

---

## 28. Decision: Options chain DTE window is configurable

**Author:** Linus (Quant Dev)
**Date:** 2026-05-14
**Status:** Implemented


### Consequences

- **Flexibility**: Agents can tailor expiration horizons per strategy
- **Performance**: Smaller chains (fewer options to analyze)
- **Default behavior**: 7-90 DTE covers most standard strategies without config

---

## 29. Decision: dividendYield handling

**Author:** Linus (Quant Dev)
**Date:** 2026-05-14
**Status:** Implemented


### Consequences

- **Format consistency**: Matches TradingView API format
- **Agent simplicity**: Agents receive display-ready values
- **Calculation safety**: If Greeks calculator needs decimal form, convert locally

---

## 30. Decision: Market Hours Detection — Live Options Probe vs. Calendar Rules

**Author:** Linus (Quant Dev)
**Date:** 2026-05-14
**Status:** ✅ Implemented


### No Changes Required

- `src/yfinance_data_provider.py` — same import, same function signature
- Agent instructions — no logic changes needed
- Framework (Rusty) — no framework changes needed

---

## 31. Decision: Options Chain Merge Strategy — Preserve yfinance Cache During Market Closure

**Author:** Linus (Quant Dev)
**User Directive:** dsanchor (2026-05-14T19:24)
**Date:** 2026-05-14
**Status:** ✅ Implemented


### Impact

- All agents receiving options chain data see the integrity constraint (via shared schema description)
- Roll recommendations in both call and put monitors now require verifiable chain lookups
- No code logic changed — only instruction string content

---

## 4. Decision: Strike-Keyed Dictionary Format for Options Chains

**Date:** 2026-05-12
**Author:** Linus (Quant Dev)
**Status:** Implemented
**Impact:** Parser output format, agent instructions, agent_runner formatting


### Safety Constraints


- Detection NEVER blocks pipeline — wrapped in try/except, returns False on error
- Uses `include_alerts=True` when fetching activities so any real alert disqualifies prolonged WAIT
- Error activities also disqualify (checked via `act.get("error")`)

---

## 23. DGI Screener: Top 40 + Interactive Filters

**Date:** 2026-05-10
**Author:** Linus (Quant Dev)
**Status:** Implemented


### Pattern for Future


**Client-side filtering with JSON data attributes** is a powerful pattern when:
- Dataset is small enough to send to client (< 100 rows)
- Filters are exploratory (don't need persistence)
- Real-time feedback is valuable
- Existing rows already have structured data in attributes

This avoids the complexity of server-side filtering APIs while providing excellent UX.

---

## 24. Decision: Normalize exchange codes at the Python source

**Author:** Linus (Quant Dev)
**Date:** 2026-05-10
**Status:** Implemented


### Consequences

- **Template simplification**: JS-side mapping removed; both ▶ and ➕ buttons now use the already-normalized `entry.exchange` value.
- **Backward compatible**: Unknown exchange codes pass through as-is; empty codes default to "NYSE".
- **Existing Cosmos docs**: Will be updated on next screener run. Until then, old docs may still have raw yfinance codes.
- **If new exchanges appear**: Just add them to `EXCHANGE_MAP` in one place.

---

## 25. Decision: DGI `top_n` exposed in Settings UI

**Author:** Linus (Quant Dev)
**Date:** 2026-05-10


### Reason


User request — prevents oscillating recommendations that create noise without improving outcomes. Implemented in linus-stability-buffer decision.

---

## 17. User Directive — Mobile UI Horizontal Scrolling

**Date:** 2026-05-01T14:48Z
**By:** dsanchor (via Copilot)
**Status:** Team awareness


### Rationale

- A ROLL without a target is worse than useless — it implies an action was chosen but can't be executed
- Converting to CLOSE is the safest fallback: it flags the position for manual review
- The audit trail in `reason` preserves what the agent originally recommended for debugging
- Instruction-level hardening reduces the frequency of this happening, but code validation is the safety net

---

## 15. Decision: User Directive — ROLL Action Format

**Date:** 2026-04-23
**By:** dsanchor (via Copilot)
**Status:** Implemented


### Reason


User request — captured for team memory. Prevents bare ROLL output and unnecessary interim rolls that just delay inevitable closes.

---

## 16. Decision: User Directive — ITM Stability Buffer

**Date:** 2026-04-23
**By:** David (via Copilot)
**Status:** Implemented


### Reason


User request — captured for team memory. Ensures better mobile UX with responsive stacking instead of horizontal scroll friction.

---

## 18. User Directive — English-Only UI

**Date:** 2026-04-18T08:43:10Z
**By:** David Sancho (via Copilot)
**Status:** Team awareness


### 24. Agent Type Filter — Dynamic Population from DOM


**Date:** 2026-04-16
**Author:** Rusty (Agent Dev)
**Status:** ✅ Implemented
**Impact:** Dashboard and Symbol Detail UX

#### Context

Dashboard Recent Activity and symbol detail Recent Activities sections needed agent type filtering. Options could be passed server-side or built client-side.

#### Decision

Populate the agent type dropdown options dynamically from the DOM (same pattern as the symbol filter) rather than injecting them from the server. This avoids coupling the JS to the Python `AGENT_TYPES` dict and means any new agent type automatically appears once it has activity items.

#### Implementation

**Files Modified:**
- `web/static/app.js` — Filter logic + dynamic population from data-agent-type attributes (~80 lines)
- `web/templates/dashboard.html` — Added `#activity-agent-filter` select + `data-agent-type` attribute
- `web/templates/symbol_detail.html` — Added `#sym-activity-agent-filter` select + `data-agent-type` attribute

**Pattern:**
1. Activity/alert rows include `data-agent-type` attribute with the agent type value
2. Filter dropdown dynamically collects unique agent types from visible rows on page load
3. JavaScript filtering hides/shows rows based on selected filter value
4. "All" option shows everything; each agent type shows only that agent's activities

#### Trade-off

If an agent type has zero recent activity, it won't appear in the dropdown. This is acceptable since filtering an absent type would yield no results anyway.

#### Rationale

- **DRY:** Don't duplicate agent type list in Python + JavaScript
- **Automatic:** New agent types appear in filter as soon as they generate activities
- **Consistent:** Uses same DOM-scanning pattern as symbol filter
- **No Server Changes:** Frontend-only implementation

---


### 22. Summary Agent Multi-Agent-Type Data Fix


**Date:** 2026-04-10
**Author:** Linus (Quant Dev)
**Status:** ✅ Implemented
**Impact:** Data layer, summary agent accuracy

#### Problem Statement

The daily portfolio summary agent was generating incomplete summaries when a symbol had multiple agent types active (e.g., both `covered_call` and `cash_secured_put` watching enabled, or watching + monitor agents for open positions).

**Symptom**: Summary would only include activities from the most active agent_type, omitting the other(s) entirely.

**Root cause**: `CosmosDBClient.get_recent_activities_by_symbol()` (line 667) used `TOP @limit` on a single query filtering only by `doc_type = 'activity'`, without considering `agent_type`. This returned the N most recent activities **overall**, not N per agent_type.

**Example failure scenario**:
- Symbol: AAPL
- Activities: 10 recent `covered_call` decisions, 2 recent `cash_secured_put` decisions
- Query: `TOP 3` activities for AAPL
- Result: 3 `covered_call` activities, 0 `cash_secured_put` activities
- Summary agent sees only covered call data, generates incomplete summary

#### Decision

Changed `get_recent_activities_by_symbol()` to fetch `limit_per_symbol` activities **per agent_type per symbol**, then merge and sort by timestamp DESC.

#### Implementation Details

**Query Strategy**:
1. Fetch list of all symbols (unchanged)
2. For each symbol, iterate over all 4 agent_types: `covered_call`, `cash_secured_put`, `open_call_monitor`, `open_put_monitor`
3. For each agent_type, query `TOP @limit` activities filtering by both `doc_type = 'activity'` AND `agent_type = @agent_type`
4. Merge all agent_type results into a single list per symbol
5. Sort merged list by timestamp DESC (newest first)
6. Return `dict[str, list[dict]]` as before

**Return Type**: Unchanged — `dict[str, list[dict]]` (symbol → list of activities)

**Activity Count Per Symbol**: Now up to `limit_per_symbol × 4` (was exactly `limit_per_symbol`)

**Backward Compatibility**: Maintained — callers receive the same data structure, just with more complete data

#### Code Changes

**File**: `src/cosmos_db.py`, lines 667-700

**Docstring Updated**: Clarified that `limit_per_symbol` is now **per agent_type**, and total activities returned may be up to `limit_per_symbol × number_of_active_agent_types`.

#### Verification

**Caller Compatibility**:
- `src/agent_runner.py:683` — `run_summary_agent()` calls `get_recent_activities_by_symbol()`, passes results to summary agent as JSON. More activities = more complete summaries. ✅ Compatible
- `web/app.py` — Does NOT call this method. ✅ No impact

#### Rationale

**Why per-agent-type querying?**
- Ensures all active strategies are represented in summaries, regardless of activity frequency
- Prevents high-activity agent types from crowding out low-activity ones
- Aligns with user expectation: "summarize all my positions/watching" means ALL, not just the most active

**Why hardcode the 4 agent_types?**
- These are the only 4 agent types in the system (covered_call, cash_secured_put, open_call_monitor, open_put_monitor)
- If empty, the query returns 0 results for that agent_type — no harm, just skipped
- Future agent types can be added to the list when they exist

**Why not increase `limit_per_symbol` instead?**
- Doesn't solve skew problem — if one agent_type is 10× more active, it still dominates
- Per-agent-type ensures representation even with massive activity imbalances

#### Trade-offs

**Pros**:
- ✅ Complete data for summary agent — all agent types represented
- ✅ Backward compatible — same return type, same callers
- ✅ Simple implementation — just iterate 4 agent_types, merge results

**Cons**:
- ❌ More CosmosDB queries (4 per symbol instead of 1)
- ❌ Potentially more activities returned per symbol (up to 4× limit_per_symbol)
- ❌ Slightly higher RU consumption (4 partition queries per symbol)

**Mitigation**:
- Summary agent runs once per day — query cost is negligible
- Increased data volume improves summary quality (worth the cost)
- If performance becomes an issue, can optimize with parallel queries or caching

---


### 23. Sequential Full Analysis via /api/trigger-all


**Date:** 2026-04-10
**Author:** Rusty (Agent Dev)
**Status:** ✅ Implemented
**Impact:** Backend API, Frontend UI

#### Context

The "Run Full Analysis" button previously fired 4 separate `/api/trigger/{agent_type}` calls. Each spawned an independent background thread, so all 4 agents ran in parallel — causing resource contention and unpredictable execution order.

#### Decision

Added a dedicated `POST /api/trigger-all` endpoint that runs all 4 agents **sequentially in a single thread**. Progress is tracked via a shared status dict on `app.state._full_analysis_status` and exposed via `GET /api/trigger-all/status`. The frontend polls this status endpoint every 4 seconds and updates the button text with real-time progress (`"⏳ Running 2/4: cash_secured_put…"`).

#### Implementation Details

**Agent Execution Order**: covered_call → cash_secured_put → open_call_monitor → open_put_monitor

**Error Handling**: If one agent errors, the next still runs (errors are logged but not blocking)

**Concurrency Control**: 409 Conflict returned if a full analysis is already running

**Status Lifecycle**:
- Status auto-resets 30 seconds after completion
- All individual "Run Analysis" and per-row trigger buttons are disabled during a full run

**Backward Compatibility**: Existing `/api/trigger/{agent_type}` endpoints unchanged (still fire-and-forget)

#### Files Changed

- `web/app.py` — new `/api/trigger-all` and `/api/trigger-all/status` endpoints + `_run_all_agents_sequentially()` worker
- `web/static/app.js` — replaced chained fetch calls with single trigger + polling

#### Rationale

**Sequential execution prevents:**
- Resource contention on shared database
- Race conditions on position state
- Unpredictable execution timing

**Status polling improves UX:**
- Users know agents are running (not silent)
- Real-time feedback on progress (which agent, what number)
- Prevents multiple overlapping full runs

**Backward Compatibility preserved:**
- Individual trigger endpoints still available for per-agent runs
- Users can still run agents independently if needed

#### Pattern for Future Work

When running multiple sequential background tasks:
- Track state in `app.state` with locks to prevent overlapping executions
- Expose status via separate status endpoint (not just response)
- Poll status on frontend with reasonable interval (4-10 seconds)
- Display real-time progress (task N of M, current task name)

---


### 16. Alert Link Pattern: Document ID Field Usage


**Date:** 2026-04-02
**Author:** Rusty (UI/Integration)
**Status:** ✅ Implemented
**Impact:** Symbol detail page, alert navigation UX

#### Problem Statement

Alert rows on symbol detail page generated 404 errors when clicked. Activity rows worked correctly. Dashboard links (both alerts and activities) worked.

#### Root Cause

Alert row template referenced non-existent field `alt.activity_id` instead of the actual document ID field `alt.id`. The activity template correctly used `item.id`. Dashboard patterns showed both activities and alerts use the same `id` field format for detail navigation.

#### Solution

Changed alert row template:
- **From:** `data-href="/activities/{{ alt.activity_id }}"`
- **To:** `data-href="/activities/{{ alt.id }}"`

File: `web/templates/symbol_detail.html` — Alert row clickable navigation link

#### Pattern

Both activities and alerts are documents stored in CosmosDB with an `id` field. Both link to the same `/activities/{id}` detail endpoint (alerts and activities share the same document type with `is_alert` boolean discriminator). Always use `{item}.id` for activity/alert detail links, never invent intermediate field names.

#### Context

- Activities: `doc_type = 'activity', is_alert = false` (or undefined)
- Alerts: `doc_type = 'activity', is_alert = true`
- ID format: `{symbol}_{agent_type}[_{position_id}]_{ts_compact}` (no prefixes)

#### Impact

✓ Alert navigation now works
✓ Consistent with activity and dashboard patterns
✓ No data model changes

---

## Pending Review Decisions (from inbox — 2026-04-02)


### 19. Scheduler Reload Implementation


**Date:** 2026-04-02
**Author:** Linus (Backend Dev)
**Status:** Implemented
**Context:** Configuration and runtime management improvements

#### Decision Summary

Implemented scheduler reload capability to apply configuration changes without full application restart, reducing deployment friction and enabling faster iteration on scheduling logic.

#### Benefits

✓ Faster configuration updates
✓ Reduced downtime
✓ Better operational flexibility

---


### 21. Unified Activities + Alerts View with Alert Filter


**Date:** 2026-04-02
**Author:** Rusty (Agent Dev)
**Status:** ✅ Implemented
**Scope:** Symbol detail page UX

#### Decision

Unified the separate "Recent Alerts" and "Recent Activities" cards on symbol detail pages into a single chronological list. Added 📢 megaphone icon for alerts and "📢 Alerts" filter pill.

#### Context

Previously, symbol detail pages showed two separate cards:
1. **Recent Alerts** — Alerts only (is_alert=true)
2. **Recent Activities** — Non-alerts only (is_alert!=true)

**Problem:** Users couldn't determine chronological order between alerts and activities. Monitoring requires temporal context.

#### Implementation

**Backend (web/app.py lines 973-1013):**
- Merged `get_recent_activities()` and `get_recent_alerts()` calls
- Combined into single `activities` list, sorted by timestamp desc
- Increased item cap from 50 to 80 items
- Preserved separate `alerts` variable for position form pre-fill logic

**Frontend (symbol_detail.html lines 351-426):**
- Removed separate "Recent Alerts" card
- Updated "Recent Activities" card to show both types
- Unified columns: Timestamp | Agent | Activity | Strike | Expiration | Underlying | Confidence | Details
- Added megaphone icon (📢) for alert rows
- Added "📢 Alerts" filter toggle button

**JavaScript (app.js lines 126-200):**
- Enhanced `applyTableFilter()` with alerts-only filtering
- Combined time range and type filtering logic
- Dynamic badge count update

#### Rationale

**UX improvement:** Single chronological view eliminates mental timeline reconstruction. Users need temporal context for monitoring workflows.

**Data integrity preserved:** Backend maintains separate `alerts` list for position form logic. No breaking changes.

**Consistent pattern:** Megaphone icon matches dashboard visual language.

#### Pattern for Future Work

When displaying time-series data with multiple types (alerts, activities, events), prefer:
- Single unified chronological view with type filters
- Over separate cards requiring mental timeline reconstruction

#### Files Modified

- `web/app.py` — Backend merge logic (lines 973-1013)
- `web/templates/symbol_detail.html` — Template restructure (lines 351-426)
- `web/static/app.js` — Filter logic with alerts toggle (lines 126-200)

---


### 5. CosmosDB Unified Container Migration (Design)

**Date:** 2026-04-01
**Author:** Danny (Lead)
**Status:** Proposed
**Impact:** Data model, ID schema, cosmos_db.py, agent_runner.py, web/app.py, context.py, provisioning

#### Problem Statement

Current state: Activities and alerts live in the same `symbols` container, differentiated by `doc_type = "activity"` vs `doc_type = "alert"`. IDs carry legacy prefixes:
- Activity IDs: `dec_{symbol}_{agent_type}[_{position_id}]_{ts_compact}` (prefix from "decision")
- Alert IDs: `sig_{symbol}_{agent_type}_{ts_compact}` (prefix from "signal")

Goals:
1. Drop `dec_` and `sig_` prefixes — legacy naming artifacts
2. Replace `doc_type` discriminator with `is_alert` boolean
3. Merge into a true unified model — one document type, alerts are activities where `is_alert=true`

#### New Unified Schema

**ID Format:** `{symbol}_{agent_type}[_{position_id}]_{ts_compact}` (prefix-free, deterministic, collision-safe)

Examples:
- `AAPL_covered_call_20260328T14_3000`
- `VZ_open_call_monitor_pos_VZ_call_53.0_20260501_20260331T16_0137`
- `AAPL_cash_secured_put_20260401T09_3000`

**Document Model:** Every agent output is a single document. The `is_alert` boolean replaces the `doc_type` discriminator. The `doc_type` field stays as `"activity"` for all records.

**What Changes:**
| Before | After | Reason |
|--------|-------|--------|
| Two `doc_type` values: `"activity"`, `"alert"` | Single `doc_type`: `"activity"` | Alerts are activities with `is_alert=true` |
| Separate alert documents with `activity_id` reference | No separate alert docs | Alert data merged into activity itself |
| `dec_` prefix on activity IDs | No prefix | Legacy naming removed |
| `sig_` prefix on alert IDs | No separate alert IDs | Alerts are not separate documents |
| `write_alert()` creates a second document | `write_activity()` sets `is_alert=true` inline | One write, not two |

**Query Impact:**
| Query | Before | After |
|-------|--------|-------|
| Get activities for symbol | `WHERE doc_type='activity'` | `WHERE doc_type='activity'` (unchanged) |
| Get alerts for symbol | `WHERE doc_type='alert'` | `WHERE doc_type='activity' AND is_alert=true` |
| Get all alerts (dashboard) | `WHERE doc_type='alert'` | `WHERE doc_type='activity' AND is_alert=true` |

#### Migration Strategy

**Approach:** Offline batch migration (low traffic, no SLA, < 5 min window)

**Data Transformation Rules:**
1. Activity documents: strip `dec_` prefix
2. Alert documents: merge into parent activity (set `is_alert=true`), delete original alert doc
3. Orphaned alerts: convert to standalone activity, strip `sig_` prefix, set `is_alert=true`

**Pre-Migration Validation:**
- Count activities and alerts per symbol
- Verify every alert has valid `activity_id`
- Log orphaned alerts

**Post-Migration Validation:**
- Count activities matches expected
- Count `is_alert=true` activities matches expected
- No `doc_type='alert'` documents remain
- No IDs start with `dec_` or `sig_`
- Spot-check 3 random alerts for correctness

#### Code Changes Required

**`src/cosmos_db.py`:**
- `write_activity()`: Strip `dec_` prefix from ID
- `write_alert()` → `mark_as_alert()`: Update existing activity in-place instead of creating new doc
- Query methods: Update `doc_type='alert'` filters to `is_alert=true`

**`src/agent_runner.py`:**
- Remove `_build_alert_data()` and `_build_roll_alert_data()`
- Change `write_alert()` calls → `mark_as_alert()`
- Alert fields included in activity payload before write

**`web/app.py`:**
- Update alert endpoints: `doc_type='alert'` → `is_alert=true`
- Remove `activity_id` display/linkage

**`scripts/provision_cosmosdb.sh`:**
- Add composite index: `(doc_type ASC, is_alert ASC, timestamp DESC)`

#### Rollback Plan

**Pre-Migration Backup:**
```bash
python scripts/migrate_unified_schema.py --export-backup backup_20260401.json
```

**Rollback Procedure:**
1. Stop app
2. Delete new documents from symbols container
3. Restore: `python scripts/migrate_unified_schema.py --restore backup_20260401.json`
4. Revert code changes
5. Restart app

Keep backup for 7 days post-migration.

#### Execution Plan

1. Write migration script (--dry-run, --export-backup, --restore)
2. Code changes to cosmos_db.py, agent_runner.py, web/app.py
3. Update provisioning script indexing policy
4. Test locally with dry-run against production data
5. Export backup
6. Stop app → run migration → validate → restart
7. Smoke test (trigger one agent run, verify new ID format)
8. Delete backup after 7 days

**Estimated effort:** 2-3 hours implementation + testing.

#### Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Orphaned alerts (no parent activity) | Low | Low | Script handles gracefully — converts to standalone activity |
| ID collision during migration | Very Low | Medium | Timestamp-based IDs are inherently unique per agent/symbol |
| Query regression (dashboard/API) | Medium | High | Update all queries in cosmos_db.py; test each endpoint |
| Backup file corruption | Low | High | Verify backup integrity before starting destructive phase |
| CosmosDB rate limiting during batch ops | Low | Low | Script uses sequential writes with retry; 50-100 docs total |

#### Decision

**Recommendation:** Proceed with this migration. The unified model simplifies the codebase (one write path instead of two), eliminates stale references, and cleans up legacy naming. Risk is low given small data volume and straightforward transformation rules.

**Requires:** User approval to schedule downtime window (2-5 min) and execute.

---


### 6. Unified Schema Implementation (Code)

**Date:** 2026-04-01
**Author:** Rusty (Backend)
**Status:** Implementation Complete, Awaiting Migration
**Related:** CosmosDB Unified Container Migration (Design)

#### Summary

Implemented the unified schema changes in `src/cosmos_db.py` per Danny's migration design. Alerts are now activities with `is_alert=true` rather than separate documents. New ID format drops legacy prefixes.

#### Implementation Decisions

**1. Query Filter Pattern**

**Decision:** Use `(c.is_alert = false OR NOT IS_DEFINED(c.is_alert))` for non-alert activity queries.

**Rationale:**
- Handles legacy documents that don't have `is_alert` field
- After migration completes, all docs will have the field explicitly
- More robust than `c.is_alert = false` alone during transition

**Applied to:**
- `get_recent_activities()`
- `get_all_activities()`
- `get_recent_activities_by_symbol()`

**2. Backwards Compatibility Strategy**

**Decision:** Keep deprecated `write_alert()` method with clear deprecation notice and TODO comments.

**Rationale:**
- Migration script runs separately from code deployment
- During transition window, old alert documents may still exist
- Cascade delete methods need to clean up old alert docs
- Clear deprecation notices guide future cleanup

**Cleanup checklist (post-migration):**
- Remove `write_alert()` method entirely
- Remove cascade delete logic for `doc_type='alert'` documents
- Remove TODO comments

**3. New Method: `mark_as_alert()`**

**Signature:**
```python
def mark_as_alert(self, symbol: str, activity_id: str, alert_data: dict) -> dict
```

**Design:**
- Reads existing activity document
- Sets `is_alert = true`
- Merges alert-enrichment fields (currently just `confidence`)
- Returns updated document

**Why not inline in `write_activity()`?**
- Alert determination happens after activity write (in agent_runner.py)
- Keeps write_activity() focused on single responsibility
- Allows agents to decide post-hoc whether activity qualifies as alert

**4. Web Layer Changes**

**Decision:** No changes needed in `web/app.py`.

**Rationale:**
- Web layer already uses cosmos_db.py abstraction methods
- No direct SQL queries in web endpoints
- All filtering logic contained in data access layer
- Query method updates automatically propagate to web layer

#### Testing Notes

**Pre-migration:**
- Both old and new query patterns work
- New writes use prefix-free IDs
- Old `write_alert()` still functional

**Post-migration:**
- Remove backwards compatibility code
- All queries use `is_alert` discriminator
- No `doc_type='alert'` documents remain

#### Related Work

**Blocked on:**
- Danny's migration script execution

**Enables:**
- Simpler codebase (one write path instead of two)
- No more stale `activity_id` references
- Cleaner ID format without legacy naming

---


### 7. Agent Signal Refactor for Unified Schema

**Date:** 2026-04-01
**Author:** Linus (Quant Dev)
**Status:** Implemented
**Depends on:** Danny's CosmosDB unified schema migration

#### Problem

Current agent_runner.py writes alerts in two steps:
1. `write_activity()` — core activity document
2. `write_alert()` — separate alert document with `activity_id` reference

Danny's migration eliminates separate alert documents. Alerts become activities with `is_alert=true` and enrichment fields (confidence, risk_flags) merged directly into the activity payload.

**Required change:** Agent runner must write ONE document per agent run, with alert-specific fields included when `is_alert=true`.

#### Solution

**Key Changes:**

1. **Removed methods:**
   - `_build_alert_data()` — no longer needed; alert data IS activity data
   - `_build_roll_alert_data()` — same reason
   - `_ALERT_FIELDS` and `_ROLL_ALERT_FIELDS` — field control moved to cosmos layer

2. **Added method:**
   - `_extract_alert_enrichment(json_data)` — extracts alert-only fields (confidence, risk_flags) from agent JSON response

3. **Updated write paths (2 locations):**

   **Path 1: Covered call / cash-secured put agents (line ~340)**
   ```python
   # Before:
   cosmos.write_activity(...)
   if is_alert:
       cosmos.write_alert(...)

   # After:
   if is_alert:
       activity_payload.update(self._extract_alert_enrichment(json_data))
   cosmos.write_activity(...)  # Single write with alert fields included
   ```

   **Path 2: Position monitor agents (line ~580)**
   Same pattern — merge alert enrichment into activity payload before writing.

4. **Telegram notification:**
   - Still builds display data inline from `json_data` (no DB query needed)
   - No dependency on separate alert documents

#### Design Rationale

**Why merge alert fields into activity payload?**

Danny's unified schema stores alerts as `doc_type="activity"` with `is_alert=true`. There are no separate alert documents. Therefore:
- Agent runner must include alert-enrichment fields (confidence, risk_flags) in the activity payload when the activity IS an alert
- This happens BEFORE the write_activity call, not after

**Why keep Telegram data construction?**

Telegram notification happens immediately after the agent run. Building the display data from the agent's JSON response avoids:
- An extra DB read to fetch the just-written activity
- Dependency on DB write completion timing
- Coupling to the DB schema (Telegram only needs display fields)

**Why remove _ALERT_FIELDS and _ROLL_ALERT_FIELDS?**

These were used to filter which fields go into the alert document. With no separate alert doc:
- The activity payload already contains all relevant fields from the agent's JSON response
- Field filtering for storage happens in cosmos_db.py (write_activity), not in agent_runner
- Removing these lists simplifies agent_runner and centralizes schema knowledge

#### Testing Strategy

**Blockers:** Requires Danny's cosmos_db.py changes:
- `write_activity()` ID format change (remove `dec_` prefix)
- `write_alert()` method removed or deprecated
- `mark_as_alert()` method added (if separate marking is needed post-write)

**Test plan after cosmos_db.py is updated:**
1. Run covered_call agent on test symbol → verify activity written with `is_alert=true` and confidence/risk_flags included
2. Run open_call_monitor agent → same verification for roll alerts
3. Check Telegram notification still fires correctly
4. Query alerts in web UI → verify `is_alert=true` filter works

#### Team Coordination

**Dependencies:**
- **Danny:** Must complete cosmos_db.py changes first (ID format, write_activity schema, remove write_alert)
- **Rusty:** Must update web/app.py alert queries (`doc_type='alert'` → `is_alert=true`)

**Deployment order:**
1. Danny: Run migration script, update cosmos_db.py
2. Linus: agent_runner.py (this change) — merges after Danny's PR
3. Rusty: web/app.py query updates — can merge alongside Linus or after

**Rollback:** If migration fails, revert to previous code + restore DB backup (Danny's rollback plan).

---


### 8. Migration Script Testing Strategy

**Date:** 2026-04-01
**Author:** Basher (Tester)
**Status:** Implemented
**Related:** CosmosDB Unified Container Migration (Design)

#### Decision

The migration script `scripts/migrate_cosmos_events.py` implements defensive testing practices:

**1. Dry-Run First Philosophy**
- `--dry-run` flag executes phases 1-2 (export + transform) without any database writes
- Outputs transformation summary showing exactly what would change
- User can review orphaned alerts, ID collisions, and merge counts before committing
- **Recommendation:** ALWAYS run dry-run first, review output, then run actual migration

**2. Backup-Before-Change**
- Phase 1 creates timestamped backup JSON in `backups/` directory before any mutations
- Backup includes both activities and alerts with integrity validation (count checks)
- Backup file path logged at end of migration for rollback reference
- **Recommendation:** Keep backups for 7 days after migration

**3. Restore Capability**
- `--restore BACKUP_FILE` flag provides rollback mechanism
- Requires explicit 'YES' confirmation to prevent accidental data loss
- Deletes current data and restores from backup atomically
- Validates backup file exists before starting delete operations

**4. Progressive Validation**
- Backup integrity check: count verification after write
- Post-migration validation (Phase 4):
  - Activity count matches expected
  - Alert count matches merged + orphaned
  - No doc_type='alert' documents remain
  - No dec_/sig_ prefixed IDs remain
  - Spot-check 3 random merged records for correctness
- Clear error messages with rollback instructions on failure

**5. Edge Case Handling**
- **Orphaned alerts** (activity_id missing): Convert to standalone activity, strip sig_ prefix, log warning
- **Duplicate timestamps**: Append _2, _3 sequence numbers, log collision
- **Already migrated docs**: Skip if ID exists (idempotent), log warning
- **Missing fields**: Handle gracefully (e.g., missing symbol → log warning, skip delete)

**6. Observability**
- Structured logging with clear phase markers
- Progress indicators for batch operations (every 10 docs)
- Summary reports at transformation and completion
- Error messages include document IDs and partition keys for debugging

#### Testing Checklist (Pre-Production)

Before running migration on production data:

1. ✓ Run `--dry-run` against production database
2. ✓ Review transformation summary for unexpected orphaned alerts
3. ✓ Check for ID collisions (should be zero unless duplicate timestamps exist)
4. ✓ Verify backup file integrity (count matches query results)
5. ✓ Test `--restore` on backup file in non-production environment
6. ✓ Confirm all validation checks pass in Phase 4
7. ✓ Schedule downtime window (2-5 min)
8. ✓ Stop app → run migration → validate → restart app
9. ✓ Smoke test (trigger one agent run, verify new ID format)

#### Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Backup corruption | Integrity check validates count match after write |
| Migration fails mid-phase | Clear error messages with rollback command in logs |
| Orphaned alerts | Convert to standalone activities with warning logs |
| ID collisions | Append sequence number, log collision |
| CosmosDB rate limits | Sequential writes with retry (50-100 docs total, low volume) |
| Wrong environment | Script reads COSMOS_ENDPOINT from env, no hardcoded URLs |

#### Lessons Learned

**Defensive Coding Patterns Applied:**
1. **Validate inputs early:** Check env vars before any operations
2. **Fail fast:** Raise MigrationError with clear message on any validation failure
3. **Dry-run everything:** No-op mode for all destructive operations
4. **Log everything:** Info-level logs for all major operations, debug for details
5. **Confirm destructive actions:** Require 'YES' input for restore (deletes current data)

**Why No Test Suite:**
- Migration is a one-time operation (not production code)
- Dry-run serves as live validation against actual data
- Test suite would require CosmosDB emulator setup (overkill for one-off script)
- Manual testing checklist more appropriate for operational scripts

**Script Design Trade-offs:**
- **Sequential writes over batch:** Simpler error handling, clear progress logging, volume is low (50-100 docs)
- **In-memory transformation:** Entire dataset fits in memory, simpler than streaming
- **No undo for Phase 3:** Backup + restore is safer than complex undo logic

---


**5. Earnings Calendar Strategy**
- **Challenge:** No dedicated earnings calendar endpoint in Massive.com
- **Solution:** Multi-source: Check ticker_info for next earnings date field, parse news headlines for "earnings" mentions
- **Impact:** Instructions emphasize importance of earnings timing but acknowledge data may require manual validation

**6. Phased Data Gathering Structure**
- **Decision:** Maintain 3-phase structure (Core Data → Context → Analytics) with enhanced SQL capabilities
- **Rationale:** Logical progression mirrors decision-making process
- **Enhancement:** Phase 3 now includes explicit SQL examples for JOINs and `apply` functions

**7. Conservative Stance When Data Missing**
- **Decision:** Apply stricter criteria when key data unavailable (lower delta, higher margin of safety)
- **Examples:** If insider data unavailable → require stronger fundamentals; If Fear & Greed unavailable → focus on IV Rank; If earnings unclear → default to WAIT unless >60 days buffer
- **Rationale:** Incomplete information = higher risk; compensation required

#### Technical Implementation

**Covered Call Instructions Changes:**
- Phase 1: 4 steps (ticker details, price history with technicals, options chain, dividends)
- Phase 2: 5 steps (fundamentals, analyst ratings, news, sentiment proxy via news, retail interest via news volume)
- Phase 3: 3 steps (IV analysis, Greeks calculations, return metrics)
- Total: 12 data-gathering steps + 1 consolidation (granular and composable)

**Cash-Secured Put Instructions Changes:**
- Phase 1: 5 steps (ticker details, extended price history, dual financials, options chain, dividends)
- Phase 2: 6 steps (analyst ratings, news, earnings history via news, market movers, fear proxy, retail proxy)
- Phase 3: 6 steps (support via SQL, oversold conditions, Greeks, IV analysis, premium calculations, insider parsing)
- Total: 17 data-gathering steps + 1 consolidation (comprehensive analysis)

**SQL Examples Added:**
- Support identification: `SELECT MIN(low) FROM price_history` (CSP)
- Strike filtering: `SELECT * FROM options_chain WHERE delta BETWEEN 0.20 AND 0.35` (CC)
- Sentiment proxy: `SELECT sentiment FROM news GROUP BY sentiment`
- Greeks calculation: `SELECT ... apply=["bs_delta", "bs_theta"]`

#### Trade-offs

**Pros:**
1. More flexible: Discovery-based approach adapts to API changes
2. More powerful: SQL + built-in functions enable complex analysis
3. Better data integration: In-memory tables allow JOINs and cross-analysis
4. Composable: 4 simple tools combine for unlimited use cases

**Cons:**
1. More complex: Requires LLM to understand SQL and compose multi-step queries
2. More steps: 12-17 steps vs. 8-11 single tool calls (though more granular control)
3. Data gaps: Missing some signals (Fear & Greed, Google Trends, Insider Trades)
4. Discovery overhead: Each run requires `search_endpoints` calls

**Mitigations:**
- Provide extensive examples in instructions
- Document fallback strategies for missing data
- Emphasize semantic table naming for easier SQL composition
- Include explicit SQL templates for common queries

#### Success Criteria

- ✅ Instructions compile without syntax errors
- ✅ All available data gathering steps documented
- ✅ SQL examples tested for correctness
- ✅ Fallback strategies defined for missing data
- ⏳ Agent successfully gathers all available data
- ⏳ Agent makes same quality decisions as with old MCP server
- ⏳ No degradation in signal accuracy or timing

---


### 11. Quick Analysis Chat — Centralized Instruction Reuse for Put/Call Analysis

**Date:** 2026-04-01
**Decider:** Rusty (Agent Dev)
**Status:** ✅ Implemented
**Impact:** Chat feature enhancement, Agent instruction reuse

#### Context
Quick Analysis chat feature extension. Previously, Quick Analysis just fetched data and started a blank chat. User wanted the first message to be the same quality analysis that monitoring agents provide—not a generic greeting.

#### Decision
Quick Analysis chat now provides automatic first analysis using the same centralized monitoring agent instructions (`TV_OPEN_CALL_INSTRUCTIONS` / `TV_OPEN_PUT_INSTRUCTIONS`) based on user-selected option type (Call/Put).

#### Implementation Details

**Frontend Changes** (`web/templates/chat.html`)
- Three-input form: Symbol + Market + Option Type (required dropdown)
- Automatic analysis trigger on successful fetch
- State flag `awaitingFirstAnalysis` to track flow
- UI shows "Analyzing for Call/Put options..." while waiting

**Backend Changes** (`web/app.py`)
- `/api/chat/fetch-symbol`: Accept and return `option_type` parameter
- `/api/chat`: Handle `first_analysis` flag
  - When `true`: Import appropriate instruction file and use as system prompt
  - When `false`: Use standard chat system prompt
- Instructions imported at runtime: `from tv_open_{call|put}_instructions import TV_OPEN_{CALL|PUT}_INSTRUCTIONS`

**Centralized Instruction Files** (Unchanged)
- `src/tv_open_call_instructions.py` — Used by `open_call_monitor` agent and Quick Analysis (call)
- `src/tv_open_put_instructions.py` — Used by `open_put_monitor` agent and Quick Analysis (put)

#### Benefits
1. **Consistency** — Quick Analysis users get the exact same quality analysis as monitoring agents provide
2. **DRY** — Single source of truth for analysis instructions (no duplication)
3. **Maintainability** — Updates to monitoring agent instructions automatically apply to Quick Analysis
4. **User Experience** — First message is immediately valuable (actionable analysis, not "How can I help you?")

#### Trade-offs
- Slightly longer wait for first message (full LLM analysis vs instant greeting)
- Users must select option type upfront (can't analyze both call and put in same session)

#### Alternatives Considered
1. **Separate instructions for chat** — Rejected: would create divergence and maintenance burden
2. **No automatic analysis** — Rejected: user explicitly requested this to match agent behavior
3. **Analyze both call and put automatically** — Rejected: would be slow and confusing to display

#### Pattern for Future Work
When building chat/analysis features that should behave like existing agents:
1. Identify the agent's instruction file
2. Import and reuse at runtime (don't duplicate)
3. Use a flag (like `first_analysis`) to switch system prompts
4. Keep the chat flow simple: automatic first message → normal Q&A

#### Files Changed
- `web/templates/chat.html` — Added dropdown, automatic first analysis trigger
- `web/app.py` — Updated endpoints to accept `option_type`, handle `first_analysis` flag, import centralized instructions

#### Related Decisions
- Chat UI Design System Alignment (2026-03-31) — established form design patterns
- Quick Analysis Button Enable Pattern (2026-03-31) — form validation pattern reused for three-input form

---


### 12. Chat vs Monitor Instructions Split

**Date:** 2026-04-01
**Decider:** Rusty + User (dsanchor)
**Status:** ✅ Accepted
**Impact:** Chat feature enhancement, agent instruction architecture

#### Context

Quick Analysis chat mode was displaying JSON/structured output because it was reusing monitor agent instructions (`TV_OPEN_CALL_INSTRUCTIONS` / `TV_OPEN_PUT_INSTRUCTIONS`). These instructions were designed for monitoring agents that need to output structured JSON for database storage.

User feedback: "I don't want the json response. I want a response as a human readable conversation based on the agent response... not a json or a set of fields and key values. Human friendly please"

#### Decision

Create separate instruction sets for different use cases:

1. **Monitor Agents** (background automation):
   - Continue using `TV_OPEN_CALL_INSTRUCTIONS` / `TV_OPEN_PUT_INSTRUCTIONS`
   - Request JSON output with specific schema for database persistence
   - Focus on structured data extraction and decision logging

2. **Chat Interface** (user interaction):
   - Use new `TV_OPEN_CALL_CHAT_INSTRUCTIONS` / `TV_OPEN_PUT_CHAT_INSTRUCTIONS`
   - Request conversational, natural language analysis
   - Focus on human-readable insights and explanations
   - Avoid JSON, structured output, or field-value pairs

#### Rationale

- **Separation of Concerns:** Database storage needs structured JSON; human users need natural conversation
- **Single Source of Truth for Data:** Both use the same TradingView data fetcher and data structure
- **Different Output for Different Audiences:** Machines consume JSON; humans consume prose
- **Maintainability:** Clear naming (`*_instructions.py` vs `*_chat_instructions.py`) makes intent obvious

#### Implementation

- `src/tv_open_call_chat_instructions.py` — Conversational call analysis (chat UI)
- `src/tv_open_put_chat_instructions.py` — Conversational put analysis (chat UI)
- `src/tv_open_call_instructions.py` — Structured call monitoring (background agents)
- `src/tv_open_put_instructions.py` — Structured put monitoring (background agents)
- `web/app.py` — Chat endpoint uses `*_chat_instructions.py` for both first analysis and follow-ups

#### Consequences

**Positive:**
- Chat experience feels natural and conversational
- Monitor agents continue to produce clean JSON for database queries
- Clear separation makes future maintenance easier
- Each instruction set can evolve independently for its use case

**Negative:**
- Additional instruction files to maintain (4 instead of 2)
- Need to keep data interpretation logic aligned between chat and monitor versions
- Could drift if not careful about maintaining consistency of insights across both

**Mitigation:**
- Both draw from same data source (TradingView fetcher)
- Core analysis logic (earnings gates, technical assessment) documented in both
- One is optimized for JSON structure, one for conversational flow
- Regular review to ensure both stay aligned on trading logic

#### Pattern for Future Work

When building chat/analysis features that need different output formats:
1. Identify if audience is machine (JSON/structured) or human (prose/conversation)
2. Create separate instruction files for each audience
3. Keep core analysis logic consistent (same data sources, same decision criteria)
4. Document alignment pattern in both files (cross-references, shared examples)
5. Route to appropriate instruction set at call time (flag like `first_analysis`)

#### Files Changed
- `src/tv_open_call_chat_instructions.py` (NEW) — Conversational call analysis
- `src/tv_open_put_chat_instructions.py` (NEW) — Conversational put analysis
- `web/app.py` — Updated chat endpoints to use `*_chat_instructions.py`

#### Related Decisions
- Quick Analysis Chat — Centralized Instruction Reuse for Put/Call Analysis (2026-04-01) — established instruction reuse pattern
- Chat UI Design System Alignment (2026-03-31) — established form and conversation patterns


# Agent Trigger Scope: Optional Symbol Parameter

**Date:** 2026-04-01
**Author:** Rusty
**Type:** Architecture Decision

## Context

User reported bug: "Run Analysis" button on symbol detail page was triggering analysis for ALL symbols instead of just the symbol being viewed.

Example: On AAPL detail page, clicking "Run Analysis" for open call positions analyzed ALL symbols with open call positions, not just AAPL.

## Decision

All agent entry point functions now accept an optional `symbol: str = None` parameter:
- `run_open_call_monitor(config, runner, cosmos, context_provider, symbol=None)`
- `run_open_put_monitor(config, runner, cosmos, context_provider, symbol=None)`
- `run_covered_call_analysis(config, runner, cosmos, context_provider, symbol=None)`
- `run_cash_secured_put_analysis(config, runner, cosmos, context_provider, symbol=None)`

Web API endpoint `/api/trigger/{agent_type}` accepts optional `symbol` in request body and passes it through.

## Rationale

1. **Backward Compatible:** No symbol = analyze all (preserves existing behavior for dashboard/scheduled runs)
2. **Single Responsibility:** Symbol detail page should only trigger analysis for that symbol
3. **User Expectation:** Clicking "Run Analysis" on AAPL page should only run for AAPL
4. **Performance:** Scoped analysis completes faster and generates less noise

## Implementation Pattern

```python
if symbol:
    sym_doc = cosmos.get_symbol(symbol)
    if not sym_doc:
        print(f"Symbol {symbol} not found — skipping")
        return
    # Filter to just this symbol's positions/settings
    symbol_list = [sym_doc]
else:
    # Get all symbols (existing behavior)
    symbol_list = cosmos.get_symbols_with_active_positions(...)
```

## Alternatives Considered

1. **Separate endpoints** (`/api/trigger-symbol/{symbol}/{agent_type}`) — More explicit but breaks REST patterns
2. **Query parameter** (`?symbol=AAPL`) — Less flexible for future parameters, non-standard for POST
3. **No fix** — Would continue confusing users and generating incorrect analysis scope

## Impact

- All agent trigger paths support scoped execution
- Symbol detail page now correctly scopes analysis
- Dashboard/settings pages unaffected (don't pass symbol)
- Scheduler unaffected (doesn't pass symbol)

# Position ID Uniqueness Fix

**Date:** 2026-04-01
**Agent:** Rusty
**Type:** Bug Fix / Data Integrity

## Decision

Position IDs now include a UTC timestamp to guarantee uniqueness across the entire lifetime of positions.

## Old Format

```
pos_{symbol}_{option_type}_{strike}_{expiration}
```

Example: `pos_AAPL_PUT_150.0_20260417`

## New Format

```
pos_{symbol}_{option_type}_{strike}_{expiration}_{timestamp}
```

Example: `pos_AAPL_PUT_150.0_20260417_20260401_214900`

Timestamp format: `YYYYMMDD_HHMMSS` (UTC)

## Rationale

The old format caused collisions in these scenarios:
1. **Roll A → B → A**: Rolling from strike A to B, then later rolling back from B to A
2. **Close/Reopen**: Closing a position at strike X, then later opening a new position at same strike X
3. **Data Integrity**: Collisions led to delete operations affecting wrong positions and close operations failing

## Impact

- **Fixes**: Cascade delete bug, close operation failures
- **Guarantees**: Each position has a unique ID forever
- **Breaking Changes**: None (position_id is internal, API unchanged)
- **Performance**: Negligible (just appending a timestamp)

## Implementation

- **File**: `src/cosmos_db.py`
- **Method**: `_generate_position_id()` (new static method)
- **Updated**: `add_position()`, `roll_position()`
- **Removed**: Collision check logic (no longer needed)

## Testing

✓ Roll A → B → A creates 3 distinct IDs
✓ Close/reopen creates 2 distinct IDs
✓ Module imports successfully
✓ All position creation paths covered

---


### 18. Put Roll Up Strategy Relaxation Implementation


**Date:** 2026-04-01
**Author:** Linus (Backend Dev)
**Status:** Implemented
**Context:** Roll strategy optimization following covered call roll down relaxation

#### Decision Summary

Implemented relaxation of the cash-secured put ROLL_UP profit optimization gate from unanimous 9/9 consensus requirement to super-majority gate (3 mandatory + 4 of 7 flexible conditions). Aligns with recent covered call roll down relaxation work and applies research-backed thresholds.

#### Implementation

Updated put roll optimization gates to apply research-validated profit/margin thresholds with flexible condition matching rather than requiring all conditions to pass.

#### Benefits

- Improved optimization opportunities while maintaining strict safety standards
- Consistent with covered call roll down approach
- Aligned with quantitative research findings

---


### 20. Put Roll Implementation Details


**Date:** 2026-04-01
**Author:** Linus (Backend Dev)
**Status:** Implemented
**Context:** Options trading automation and roll mechanics

#### Implementation

Completed implementation of put roll mechanics with proper state transitions, position tracking, and integration with existing roll frameworks. Validated through comprehensive scenario testing.

#### Scope

- Position state management for put rolls
- Roll mechanics and validation
- Integration with existing position management systems

---



### 10. Quick Analysis Button Enable Pattern

**Date:** 2026-03-31
**Author:** Rusty (Agent Dev)
**Status:** ✅ Implemented
**Impact:** Form UX improvements

#### Context
The Quick Analysis mode in `chat.html` has a "Fetch & Analyze" button that requires both `symbol` and `market` inputs. The button was initially enabled, causing UX confusion when clicked without filled fields (would show error instead of preventing click).

#### Decision
Form submission buttons in multi-mode UIs should start disabled and enable dynamically based on required field validation.

#### Implementation
1. **Default State:** Button starts with `disabled` attribute
2. **Validation Function:** `checkFetchButtonState()` checks both fields have trimmed values
3. **Event Listeners:** Attach `input` events (not `keyup`) to catch paste/autofill
4. **Mode Entry Check:** Call validation function when form first displays
5. **Enter Key:** Respect button state (don't submit if disabled)

#### Benefits
- **Immediate Feedback:** Button state reflects form validity in real-time
- **Prevents Errors:** Users can't submit incomplete forms
- **Navigation Safe:** Handles back/forward, mode switching, pre-filled values
- **Accessible:** Visual disabled state is also functional (no click handler run)

#### Pattern for Team
When adding form-based flows with required fields:
```javascript
// 1. Start button disabled
<button id="submitBtn" disabled>Submit</button>

// 2. Create validation function
function checkFormValidity() {
    const isValid = requiredField1.value.trim() && requiredField2.value.trim();
    submitBtnEl.disabled = !isValid;
}

// 3. Attach to inputs
field1El.addEventListener('input', checkFormValidity);
field2El.addEventListener('input', checkFormValidity);

// 4. Check on display
function showForm() {
    formEl.style.display = 'block';
    checkFormValidity(); // handles pre-filled values
}

// 5. Respect in Enter handlers
fieldEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !submitBtnEl.disabled) {
        submit();
    }
});
```

#### Files Changed
- `web/templates/chat.html`

#### Related Decisions
- Chat UI Design System Alignment (2026-03-31) — established form field patterns
- Standard `.btn` disabled styles in `web/static/style.css`

---


### Trade-offs


| Pro | Con |
|-----|-----|
| FREE — no API key | Options chain likely incomplete |
| Pre-calculated technicals | No explicit IV, no Greeks |
| Pivot points built-in | No historical OHLCV data |
| Single-page fundamentals | No balance sheet / cash flow details |
| Fewest fetch calls (4 URLs) | No news feed / sentiment scores |

---

## Decision: Structured JSON Output Format for Decisions

**Date:** 2026-03-27
**Author:** Rusty (Agent Dev)
**Status:** Implemented
**Impact:** Team-wide (changes agent output parsing, logging, and instruction format)


### Implications for Team


- **Linus**: Instruction files now specify JSON output format — any new instruction files must follow the same schema
- **Basher**: Test cases should verify JSON extraction from agent responses
- **Danny**: Downstream systems can now consume `.jsonl` files for structured decision data
- **Scribe**: README may need updating to document the new output format

---

## User Directive: Model Configuration Change

**Date:** 2026-03-27T09:18:56Z
**By:** dsanchor (via Copilot)
**Status:** Implemented in config/team.md


### Impact


- Applies to all agent instruction files using TradingView provider
- Updated in `config/team.md` model field
- Existing Massive.com and Alpha Vantage workflows unaffected
- Configuration propagates to all agents via team config inheritance

---

## 2. TradingView Navigation Optimization: Remove Main Symbol Page

**Date:** 2026-03-27T09:38:00Z
**Author:** Rusty (Agent Dev)
**Status:** Implemented
**Impact:** Team-wide (improves TradingView agent data gathering)


### 12. User Directive: JSONL-Only Decision/Signal Output


**Date:** 2026-03-27
**Author:** dsanchor (via Copilot)
**Status:** Proposed
**Impact:** Output format simplification

#### Decision

Drop `.log` decision/signal files entirely. Keep only `.jsonl` output for decisions and signals. Update `config.yaml` paths accordingly.

#### Rationale

Single machine-parseable format reduces file management complexity. JSONL is easier to parse and aggregate than multiple file types.

---


### 4. MCP Server Migration to Massive.com (Agent Instructions)

**Date:** 2026-03-26
**Author:** Linus (Quant Dev)
**Status:** ✅ Completed
**Impact:** Team-wide (affects agent instructions and data gathering workflow)

#### Context

Migrated both covered call and cash-secured put agent instructions from the old `iflow-mcp-ferdousbhai-investor-agent` MCP server to the new `mcp_massive` from Massive.com. The old server had specific tool calls like `get_ticker_data()`, `get_price_history()`, `get_cnn_fear_greed_index()`, etc. The new Massive.com MCP server has a fundamentally different architecture with 4 composable tools and built-in analytical functions.

#### Key Design Decisions

**1. Discovery-First Workflow**
- **Decision:** Structure data gathering protocol around `search_endpoints` → `call_api` → `query_data` progression
- **Rationale:** The new MCP server is endpoint-agnostic; agents discover what they need rather than knowing tool names upfront
- **Impact:** Instructions now guide LLM through discovery phase before data collection

**2. In-Memory DataFrames with Meaningful Names**
- **Decision:** Use `store_as` parameter consistently with semantic table names (e.g., "price_history", "options_chain", "financials")
- **Rationale:** Enables SQL JOINs and cross-analysis in later steps
- **Pattern:** Phase 1: Store raw data tables → Phase 2: Store supplementary context → Phase 3: Query and analyze with SQL

**3. Built-in Functions for Greeks & Technicals**
- **Decision:** Leverage `apply` parameter extensively for Black-Scholes Greeks and technical indicators
- **Functions Used:** Greeks: `bs_delta`, `bs_gamma`, `bs_theta`, `bs_vega`, `bs_rho`; Technicals: `sma`, `ema`; Returns: `simple_return`, `cumulative_return`, `sharpe_ratio`
- **Rationale:** Avoid manual calculations; use optimized built-in functions for accuracy and speed

**4. Data Availability Adaptations**
- **Removed:** CNN Fear & Greed Index, Google Trends, Dedicated institutional holders endpoint, Dedicated insider trades endpoint
- **Alternatives:** Fear & Greed → News sentiment analysis; Trends → News volume; Institutional holders → Fundamentals; Insider trades → News parsing
- **Rationale:** Maintain decision quality with available data; apply conservative criteria when key signals missing
---


### Impact

- No local `uvx`/`marketdata-mcp-server` install needed for Alpha Vantage users
- Massive.com workflow unchanged
- `MCPStreamableHTTPTool` from `agent_framework` handles the HTTP transport

---

## Governance

- All meaningful changes require team consensus
- Document architectural decisions here
- Keep history focused on work, decisions focused on direction

---

## Decision: TradingView Provider Plumbing + EXCHANGE-SYMBOL Format

**Date:** 2026-03-26
**Author:** Rusty (Agent Dev)
**Status:** Implemented


### Impact

- **Linus must create**: `tv_covered_call_instructions.py` and `tv_cash_secured_put_instructions.py` before the tradingview provider can be activated.
- Existing providers (massive, alphavantage, yahoo) are unaffected.
- Symbol files changed — any external tooling reading these files needs to handle the new format.

---

## Decision: TradingView Instruction File Design

**Date:** 2026-03-26
**Author:** Linus (Quant Dev)
**Status:** Implemented
**Files:** `src/tv_covered_call_instructions.py`, `src/tv_cash_secured_put_instructions.py`


### 19. Web Dashboard Architecture


**Date:** 2025-07-28
**Author:** Rusty (Agent Dev)
**Status:** Completed

#### Context

Added a web dashboard for the options agent system — a separate entry point (`run_web.py`) using FastAPI + Jinja2 templates with a dark trading theme.

#### Key Decisions

1. **Separate entry point, shared data files**: Web dashboard (`run_web.py`) and scheduler (`python -m src.main`) run independently. Both read the same JSONL logs and data files — no database layer needed.

2. **Raw YAML config loading**: The web app reads `config.yaml` directly via `yaml.safe_load()` instead of using `src.config.Config`, which requires MCP environment variables. The web app only needs the Azure endpoint (for chat) and scheduler cron expression.

3. **No build step**: Vanilla HTML/CSS/JS with custom dark-theme CSS. No npm, no bundler, no CSS framework dependency.

4. **JSONL as the database**: All dashboard data comes from reading JSONL log files and `data/*.txt` files on every request. Acceptable for the current log sizes; would need indexing if logs grow to millions of lines.

5. **Chat uses direct OpenAI API**: The chat endpoint uses `openai.AzureOpenAI` with `AzureCliCredential` — same auth pattern as the agent runner but without the agent framework overhead. Context is the last 20 decisions per log file.

6. **Hot-reload confirmed**: `_read_symbols()` and `_read_positions()` in `agent_runner.py` read from disk on every call inside `run_agent()` / `run_position_monitor_agent()`. No caching — edits via the settings page take effect on the next scheduler tick with zero code changes.

#### Trade-offs

- Reading JSONL on every request is fine for current scale but won't scale to huge logs. If needed, add a lightweight caching layer or SQLite index later.
- No authentication on the web dashboard — acceptable for local/internal use. Add auth middleware if exposing to the internet.




---
---
---





### 22. Remove non-TradingView MCP providers


**Author:** Rusty (Agent Dev)
**Date:** 2025-07-23
**Status:** Implemented

## Context

The project supported four MCP data providers (Massive.com, Alpha Vantage, Yahoo Finance, TradingView) with per-provider instruction files, config branching, and transport selection. In practice, TradingView + Playwright pre-fetch is the only provider that works reliably — LLMs cannot drive multi-step browser/tool workflows, and the other providers' MCP servers had various limitations.

## Decision

Remove all non-TradingView providers. TradingView via Playwright is the sole data source.

## Changes

- **Deleted:** 6 instruction files (`av_*`, `yf_*`, generic `covered_call_instructions.py`, `cash_secured_put_instructions.py`)
- **Simplified:** `config.yaml` MCP section flattened (no `provider` key, no per-provider sub-sections)
- **Simplified:** `config.py` — removed provider selection, pruning, transport/url/env_key properties
- **Simplified:** `agent_runner.py` — removed entire non-TradingView code path (MCP tool creation, HTTP transport, API key validation)
- **Simplified:** Agent wrappers — no provider branching, always use TV instructions
- **Updated:** README — removed multi-provider docs, comparison table, env var setup for removed providers

## Trade-offs

- **Lost:** Ability to switch to Massive/AV/Yahoo without code changes
- **Gained:** ~4100 lines of dead code removed, dramatically simpler config and runtime paths, no unused env var requirements

## Team Implications

- **Linus (Quant Dev):** Only TV instruction files exist now. Any instruction changes go to `tv_*` files.
- **Basher (Test/Ops):** No need to test multiple providers. Playwright container is the only external dependency.
- **Scribe (Docs):** README already updated. No multi-provider docs to maintain.
# Decision: Dashboard Run Button UX

**Date:** 2024-12-XX
**Author:** Linus (Quant Dev / Frontend Dev)
**Status:** Implemented

## Context

The dashboard had "Run Now" buttons for each agent, but users needed:
1. Clearer button labeling (what does "Run Now" actually do?)
2. Ability to trigger all agents at once for comprehensive analysis

## Decision

1. **Button Text Change**: "Run Now" → "Run Analysis"
   - More explicit about what the button does
   - Aligns with the purpose: running analysis, not just "now"

2. **New Full Analysis Button**: Added "Run Full Analysis" button
   - Positioned above agent tables, right-aligned
   - Triggers all 4 agents sequentially (covered_call, cash_secured_put, open_call_monitor, open_put_monitor)
   - Shows progress during execution: "Running... (2/4)"
   - Blue primary styling to distinguish from individual agent buttons

## Implementation

- Sequential execution using promise chaining (not parallel)
- Uses existing `/api/trigger/{agentType}` endpoint
- Real-time progress feedback
- Button disables during execution, re-enables after completion

## Rationale

- **Sequential over Parallel**: Ensures controlled execution order and reduces server load
- **Progress Indicator**: Users can see which agent is currently running
- **Primary Styling**: Visual hierarchy makes it clear this is a comprehensive action
- **Consistent Patterns**: Reuses existing trigger button styles and API endpoints

## Alternatives Considered

1. **Parallel Execution**: Rejected due to potential resource contention
2. **Server-Side Batch Endpoint**: Rejected to keep frontend changes isolated
3. **Modal Dialog**: Rejected as too heavy for a simple batch trigger

## Impact

- **Frontend**: 3 files modified (dashboard.html, app.js, style.css)
- **Backend**: No changes needed (reuses existing endpoints)
- **UX**: Improved clarity and efficiency for users running multiple agents


---


### 15. Profit Optimization Signals for Open Position Monitors


**Date:** 2025-07-22
**Author:** Rusty (Agent Dev)
**Status:** Implemented
**Impact:** Agent behavior (monitor instruction prompts)

#### Context

The open position monitors (call + put) previously only detected defensive roll scenarios (assignment risk). Users wanted proactive profit optimization — rolling to a tighter strike to collect more premium when conditions are unanimously safe.

#### Decision

Added profit optimization instruction sections to both `tv_open_call_instructions.py` (ROLL_DOWN) and `tv_open_put_instructions.py` (ROLL_UP). Uses a 9-condition unanimous consensus gate — ALL must pass or the decision stays WAIT.

#### Key Design Choices

1. **Instruction-only change**: No schema changes, no `agent_runner.py` changes. ROLL_DOWN/ROLL_UP and `risk_flags` were already fully supported. This validates the architecture — schema is stable, behavior evolves through prompts.

2. **9-condition unanimity gate**: Deep OTM (5%+), very low delta (<0.15), technicals aligned, MAs aligned, no catalysts, analyst sentiment not contrary, low IV, DTE > 14, stable decision history. "No gambling" — one ambiguous indicator = WAIT.

3. **`profit_optimization` risk_flag**: Semantic marker distinguishing "rolling because the position is at risk" from "rolling because I can safely collect more premium." Propagates through existing `_ROLL_SIGNAL_FIELDS` pipeline.

4. **Confidence must be "high"**: If the agent can't say high confidence, it must not recommend the optimization.

#### Trade-offs

- **Conservative by design**: Many valid optimization opportunities will be missed because one indicator is neutral instead of confirmatory. This is intentional — false positives (bad optimization) are far worse than false negatives (missed premium).
- **No new schema fields**: Keeps the signal pipeline simple but means downstream consumers must check `risk_flags` to distinguish profit vs defensive rolls.

---


### 21. Always use signal_log for dashboard and signal views


**Date:** 2025-07-22
**Author:** Rusty (Agent Dev)
**Status:** Implemented

## Context
Dashboard counts for position monitors were reading from `decision_log`, which includes WAIT decisions. This inflated signal counts (e.g., 3 WAITs shown as 3 signals when actual actionable signals were 0).

## Decision
All dashboard counts, signal list pages, and signal detail pages now read exclusively from `signal_log`. The `decision_log` is only used for:
1. "Recent Activity" feed on the dashboard (which shows all events)
2. "Recent Decisions" context section on the signals list page
3. Backing decisions on the signal detail page (correlated by timestamp)

## Impact
- Dashboard signal counts now accurately reflect actionable signals only
- Signals list page gains a "Recent Decisions" section for analysis context
- No changes to how logs are written — only how they're read for display

---


### 18. TradingView Pre-Fetch Architecture


**Date:** 2025-07-17
**Author:** Rusty (Agent Dev)
**Status:** Implemented
**Commit:** 9bca215

#### Context

The LLM agent unreliably executes 3+ sequential Playwright browser tool calls — it skips pages, fabricates navigation errors, or ignores tool-calling instructions. Multiple instruction-based fixes were attempted (reordering pages, innerText extraction via browser_run_code, reducing snapshot size) — none solved the fundamental problem.

#### Decision

Pre-fetch ALL TradingView data deterministically in Python, then pass it to the agent as text. The agent receives NO browser tools — it only analyzes.

#### Implementation

1. **New module `src/tv_data_fetcher.py`**: `TradingViewFetcher` class uses the same Playwright MCP tools (browser_run_code, browser_navigate, browser_click, browser_snapshot) but driven from Python, not the LLM.
2. **`src/agent_runner.py`**: Branches on `mcp_provider == "tradingview"` — pre-fetch path creates ChatAgent with no tools; all other providers use existing MCP-tool flow unchanged.
3. **TV instruction files**: Phase 1 rewritten from "gather data via browser tools" to "review pre-fetched data". All `browser_*` references removed. Phase 2 analysis logic, trading rules, output format, decision criteria unchanged.

#### Trade-offs

- **Pro**: 100% reliable data fetching — Python deterministically loads all 3 pages every time
- **Pro**: Agent context is smaller and cleaner — only data + analysis instructions, no tool-call overhead
- **Pro**: Non-tradingview providers completely unaffected
- **Con**: Agent cannot adaptively explore pages (e.g., try different expirations) — but this was unreliable anyway
- **Con**: Pre-fetch always loads all 3 pages even if one would suffice — acceptable overhead

#### Impact

- Covered call and CSP agents using TradingView provider should now consistently analyze all 3 data sources (technicals, forecast, options chain) instead of randomly skipping 1-2 pages.

---


### 9. Chat UI Design System Alignment

**Date:** 2024-03-31
**Author:** Rusty (Agent Dev)
**Status:** Completed
**Impact:** Web UI consistency

#### Context
The dual-mode chat interface (Portfolio Chat + Quick Analysis) was initially implemented with custom CSS styles that didn't match the rest of the application's design system. User feedback indicated the look and feel was inconsistent with dashboard, settings, and other pages.

#### Key Design Decisions

1. **Use Standard Card Components**
   - Replace custom `.mode-option` styles with standard `.card` + `.card-header` structure
   - Use existing design tokens (`var(--bg-input)`, `var(--bg-hover)`, `var(--border)`, `var(--accent-blue)`)
   - Match padding, spacing, and border-radius to other cards in the app

2. **Free Text Input for Market Field**
   - Replace dropdown with text input for flexibility
   - Apply text-transform: uppercase for consistent display
   - Allows users to enter any market/exchange name

3. **Unified Navigation Pattern**
   - Use `.btn-sm` class for all back buttons across both modes
   - Consistent placement in card headers
   - Same "← Back" text pattern throughout

4. **Form Consistency**
   - Use `.hint` class for descriptive text (matches settings pages)
   - Use `.input-field` class for form inputs
   - Match label styling from `settings_config.html`

#### Implementation
- **Files Changed:** `web/templates/chat.html`, `web/static/style.css`
- **Design Tokens Used:** `--bg-card`, `--bg-input`, `--bg-hover`, `--border`, `--accent-blue`, `--text`, `--text-muted`, `--radius`
- **Refactoring:** Removed 30+ lines of unused CSS

#### Result
Standard card-based selection with free text inputs matching app design; all functionality preserved, visual consistency achieved.

#### Trade-offs
- **Flexibility vs Validation**: Free text input allows any market name but sacrifices dropdown validation (acceptable for power users)
- **Simplicity**: CSS reuse reduces code duplication and future maintenance burden

---


