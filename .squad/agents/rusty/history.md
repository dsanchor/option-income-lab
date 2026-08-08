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

**Portfolio Chat (July 2026):** Configuration screen with agent selection/activity limits (2026-07-14), Symbol Data toggle for enrichment display (2026-07-15), DAL leak migration (2026-07-09), Activity Chat and DPS Insights endpoints (2026-07-09)

**Scheduler & Data Layer (June-July 2026):** Reliability fixes (per-symbol timeout + worker max-duration guards), DAL leak refactoring (3 new CosmosDBService methods), 5-item Cosmos call migration

**Earlier July tasks:** Eligible Dividend Tracking (ex-dividend awareness for CSP SELL), Calendar Active-Per-Date (position exposure logic), Manual Close Buyback Cost (optional cost tracking), Dead DTE Config Removal, Scheduler Toggle Persistence

**April–June tasks:** 40+ work items including Options Chain Scheduled Caching, TradingView Data Layer, Quick Analysis Chat Conversationalization, Anti-403 Architecture, DGI Screener, Contrarian Agent, Multi-Provider MCP, Phase 2 Pipeline Swap (TradingView → yfinance), Comprehensive README Updates. All documented in `.squad/decisions/decisions.md` decision records.

**Critical fixes & patterns:**
- Dict-spread protection (March 2026): Reassert `doc["timestamp"]` after `**spread` in write_activity/alert
- Config precedence (March 2026): Merge CosmosDB settings into Config at runtime
- Per-symbol notification toggles (March 2026): `telegram_notifications_enabled` field
- Market hours detection (May 2026): Live MSFT ATM call probe vs calendar rules; 5min cache, conservative fallback
- Options chain merge (May 2026): In-memory cache during market open; TV fallback merges with cache, preserving longer-dated expirations
- P&L robust mid calculation (June 2026): Handle illiquid one-sided quotes via `robust_mid(bid, ask)` helper
- Scheduler reliability (June 2026): Per-symbol timeout + worker max-duration guard to prevent hang cascades
- Unified schema query pattern: Activities/alerts in same container; discriminate with `is_alert` boolean
- Symbol Detail Page: Alerts card before activities card (user priority)
- Lazy initialization of expensive resources (Playwright + Chromium)
- Multi-strategy data extraction (3-level fallback: HTML → JSON → API)
- TradingView Scanner API: Unauthenticated endpoint for fundamentals/technicals/forecast/dividends
- Position enrichment from activities (attach latest monitor activity data with `_` prefix)
- Settings data source pattern: CosmosDB first, fallback to config.yaml
- Source attach vs pre-fill pattern (from-activity automation vs manual with attachment)

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

### July 2026 — Portfolio Chat Symbol Data Toggle (2026-07-15)
**Status:** ✅ Completed  
**Scope:** Add "Include symbol data" checkbox to Portfolio Chat config screen with persisted enrichment

**Changes:**
- `web/templates/chat.html` — added checkbox (#includeSymbolDataInput), portfolioConfig.include_symbol_data binding, payload construction
- `web/app.py` — added `include_symbol_data` flag, context_symbols tracking, SYMBOL DATA section, system_prompt updates

**Feature Contract:**
- Frontend sends `include_symbol_data` from Portfolio Chat config checkbox
- Backend reads only existing `symbol_config.enrichment` from CosmosDB (no live fetches)
- Symbols de-duplicated across selected agents, sorted alphabetically in consolidated `=== SYMBOL DATA ===` section
- Missing/empty enrichment emits `No enrichment data available.` for that symbol

**Validation:** AST parse OK; `pytest tests/ -k chat` → 13 passed

**Decision Record:** `.squad/decisions/decisions.md` → "Portfolio Chat Symbol Data Toggle"

### July 2026 — DAL Leak Refactoring (2026-07-09)
The Portfolio Chat context contract now accepts `include_symbol_data` (default `false`) alongside `selected_agents` and `activities_limit`. When enabled, the backend appends one consolidated `=== SYMBOL DATA ===` section after agent context, using persisted `symbol_config.enrichment` only and de-duplicating symbols across selected position monitors and following agents before sorting alphabetically.

## Learnings

### July 2026 — Symbol Detail Controls Regrouping (2026-07-16)
- Symbol detail controls were regrouped into two cards: Watchlist & alerts contains the 4 toggles with the pause/resume control as a header action; Views & actions contains the 4 navigation chips.
- Notifications intentionally stays active during watchlist pause and is not shadowed or disabled, because position monitors still run and can still notify.

### July 2026 — Watchlist Pause Until Earnings (2026-07-16)
- `symbol_config.watchlist_pause` is an optional pause-layer object with `until` (`YYYY-MM-DD`), `reason: earnings`, `scope: [covered_call, cash_secured_put, buy_tracker]`, and `set_at` UTC ISO timestamp. It does not mutate `watchlist.*` flags, preserving user intent.
- Active pause semantics: `watchlist_pause.until >= today` (local `YYYY-MM-DD`). Expired pauses (`until < today`) are treated as inactive by scheduler queries and are cleared by a registered reactivation job.
- Gating is layered: Cosmos watchlist queries exclude active pauses, manual/per-symbol following-agent runs check `is_watchlist_paused(sym_doc)`, and the dashboard/detail UI shadows paused symbols/rows without affecting position monitors.
- API endpoints: `POST /api/symbols/{symbol}/pause` sets the pause using the next calendar earnings date (or optional `until` override); `DELETE /api/symbols/{symbol}/pause` resumes immediately.
- Scheduler registration: `watchlist_reactivation` / “Watchlist Reactivation” runs weekdays at `0 6 * * 1-5`, respects `watchlist_reactivation.enabled` defaulting true, and clears expired pauses.

### July 2026 — Symbol Detail Compact Toolbar (2026-07-16)
- Symbol detail controls were consolidated into a SINGLE compact horizontal toolbar (toggles | pause | nav chips) to minimize vertical space; the two-card layout was rejected for taking too much room.

### July 2026 — Roll Table Endpoint + Activity Detail UI (2026-07-23)
- New endpoint `GET /api/activities/{activity_id}/roll-table` wired in `web/app.py` (inserted between REST API Activity Chat section and Page Routes — Activity Detail). Uses `_get_cosmos(request)` + `cosmos.get_activity_by_id()` pattern identical to all other activity REST handlers.
- Strike/expiration resolution: `current_strike`/`current_expiration` (monitor agents) with `strike`/`expiration` as fallback (watch agents) — mirrors `api_roll_position_from_activity` pattern.
- Premium resolution: `activity.get("premium")` first, then `source.get("premium")`, then `source.get("new_premium")` — aligned with `api_dps_analysis` DPS scorer pattern.
- Price fetch: exact copy of `api_dps_analysis` yf_provider pattern (fetch_all → overview JSON → fundamentals → current_price.value).
- Chain fetch: exact copy of `api_dps_analysis` options_chain_cache pattern (`get_options_chain_cache().get_or_load_async(symbol)`).
- Graceful error returns: 404 (not found), 400 (unsupported agent_type, missing fields, invalid strike), 503 (price/chain unavailable, RuntimeError).
- Frontend: Roll Scenarios card added to `activity_detail.html` — visible only for `covered_call`, `cash_secured_put`, `open_call_monitor`, `open_put_monitor`. Lazy fetch on page load, spinner while loading. Summary bar shows strike/exp/premium, buyback cost+per-share, % capturado, profit_target badge, chain timestamp (orange ⚠️ if >15 min). Grid: rows = label+strike, columns = expiration+DTE, cells show bid/ask + delta + net_credit with green/red/gray background. No open interest shown (per user requirement).
- JS uses inline styles with CSS var() tokens (`--accent-green`, `--accent-red`, `--text-muted`, `--border`, `--font-mono`) — no new CSS classes added.

### July 2026 — Roll Table Relocation to Position Detail (2026-07-23)
- Roll Scenarios section **relocated** from `activity_detail.html` to `symbol_detail.html` per-position blocks.
- New endpoint `GET /api/symbols/{symbol}/positions/{position_id}/roll-table` added in `web/app.py` (after `api_dps_insights`, before Action Plans section). Mirrors `api_dps_analysis` exactly: same cosmos/position lookup, same yf_provider price fetch, same options_chain_cache call, 404/503/500 error handling. GET is appropriate (pure read).
- Old `GET /api/activities/{activity_id}/roll-table` endpoint remains for backward compatibility (activity detail page no longer uses it, but the endpoint itself was not removed since it does no harm).
- `activity_detail.html` cleanup: removed Roll Scenarios card HTML block (lines ~359-382) and the `{% if agent_type ... %}` script block (~789-900). Jinja balanced 51/51.
- `symbol_detail.html` addition: Roll table section inserted inside `{% if pos.status == 'active' %}` guard, after the `dps-analysis-section` div, inside `.position-snapshot-chart` wrapper. Triggers auto-on-expand via `window._loadRollTable(section)` hooked into both `tr.pos-row` click handler and the roll-button expand handler. Loads once per position (guarded by `dataset.rollLoaded`).
- JS scoped in IIFE, exposes only `window._loadRollTable`. Reuses exact same cell styles, formatters, summary builder, and grid builder from the activity detail implementation.
- Jinja balanced 81/81 for symbol_detail.html. `python3 -m pytest tests/test_roll_table.py -q` → 46 passed. `python3 -m py_compile web/app.py` → OK. `import web.app` → OK.


### 2026-08-08 — Watchlist UI Fix (shares inline edit + add symbol + strategy filters)
**Status:** ✅ Completed
**Scope:** frontend symbols/watchlist — SymbolsTable, AddSymbolForm, types, backend overview

**Changes:**
- `backend/web/app.py` — `_compute_symbols_overview` añade campo `watchlist` (covered_call, cash_secured_put, buy_tracker) en cada fila del overview.
- `frontend/src/types/symbols.ts` — Añadido `SymbolWatchlistFlags` + campo `watchlist?` en `SymbolRow`.
- `frontend/src/components/SymbolsTable.tsx` — Edición inline de shares (input controlado, actualización optimista, PUT BFF, router.refresh). Filtros de estrategia (pills: All / Calls / Puts / Buy Tracker). Error banner descartable.
- `frontend/src/components/AddSymbolForm.tsx` — Nuevo componente (botón colapsa/expande), campos: symbol, exchange, checkboxes de estrategia. Manejo 409, router.refresh en éxito.
- `frontend/src/app/symbols/page.tsx` — Importa y renderiza AddSymbolForm sobre la tabla.

**Key patterns:**
- Optimistic `localShares` local → revert en error; la fuente de verdad regresa via `router.refresh()`.
- `useCallback` en `saveShares`/`startEdit`/`cancelEdit` para estabilidad de deps.
- `total_shares` requiere entero JSON no negativo; el BFF conserva errores/status del backend y la celda editable detiene propagación hacia el modal.
- El alta dispara `backfill_symbol_forecasts` después de persistir; el fallo se registra pero nunca revierte la creación.
- `forecast_cron` trata `get_price_forecasts` como capacidad opcional para conservar compatibilidad con adaptadores mínimos y tests.
- Validación: `pytest tests/test_watchlist_symbols.py tests/test_forecast_cron.py -q` → 57 passed; ESLint focalizado + `tsc --noEmit` → OK.

### 2026-08-08 — Position Premium and Buyback Editing
- `PositionDetail` expone editores independientes para Premium y Buyback; Buyback se renderiza y puede editarse aunque el valor actual sea nulo.
- Los guardados no son optimistas: mantienen el valor confirmado ante fallos, muestran estado/error y ejecutan `router.refresh()` solo tras éxito.
- Los BFF PATCH de `premium` y `buyback_cost` replican el proxy de notas y conservan el status y cuerpo de error del backend.
- Ambos endpoints backend exigen valores finitos y no negativos, rechazan booleanos, valores malformados, NaN/Infinity y negativos con 400, y conservan 404/503 para errores de Cosmos.
- Validación: `test_position_financial_updates.py` → 30 passed; tests de posición relacionados → 3 passed; ESLint focalizado + `tsc --noEmit` → OK.

### 2026-08-08 — Cross-Agent Suitability Correction
- Linus replaced the temporary watchlist-flag filter pills with the documented suitability categories: Ideal Puts, Ideal Calls, No Puts, and No Calls.
- Suitability is derived from normalized `entry_tag` plus momentum. Watchlist flags remain separate operational tracking controls, and option-chain type/delta filters are a different backend concern.
- Basher's final current-state review approved the integrated implementation; earlier concurrent-snapshot findings are superseded.
