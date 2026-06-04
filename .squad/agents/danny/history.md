# Danny — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Stack:** Python, Microsoft Agent Framework, Azure Foundry (gpt-5.1)
- **MCP:** iflow-mcp_ferdousbhai_investor-agent 1.6.3
- **Description:** Two periodic trading agents for covered call and cash-secured put sell signals. Local runtime, configurable polling, stock symbols from files, decision logs, sell signal alerts.

## Core Context

### DGI Screener Architecture & Scope (2026-05)
- Scope simplified: Removed CSP Recommender LLM agent; kept manual "Quick Analysis" + "Add to Watchlist" buttons
- Top 20 DGI candidates with technical timing indicators (RSI, SMA, Bollinger Bands)
- MVP: Daily scheduler, yfinance data source, CosmosDB storage, web dashboard UI
- Phases: Phase 1 (Screener MVP 3-4d) → Phase 2 (Dashboard + buttons 2-3d) → Phase 3 (Refinement 1-2d)

### Contrarian Agent Architecture (2026-07)
- 4 agent types × valid decisions: 16 decision-specific playbooks
- Anti-noise rules: WEAK self-assessment for solid decisions, forbid arguments against risk management
- Parameterized instruction functions enable context-specific customization while maintaining single source of truth
- Agent output schema validation rejects invalid combinations (e.g., open_put + ROLL_UP)

### Position Management Guardrails (2026-07)
- Hard 45 DTE cap for new positions (enforce in 6 places: Time Frame, Theta, Earnings gate, KEY PRINCIPLE, DTE Priority, WAIT triggers)
- Near-ATM stability buffer: 3% zone, WAIT on favorable technicals + delta < 0.60 to prevent oscillation
- ROLL_OUT guardrail: Restrict to near-ATM positions, ≤5 DTE, no directional signal (force ROLL_UP/DOWN_AND_OUT when strike needs change)
- Bare ROLL prevention: Enumerate valid actions explicitly, reject category names

## Learnings

### 2026-05-10: DGI Screener Scope Simplification — CSP Recommender Removed

**User Directive (David):** Simplificar el alcance eliminando el agente LLM dedicado al CSP Recommender. El usuario prefiere control manual sobre recomendaciones automáticas.

**Architecture Decision (v2.1):**
- ❌ **Eliminado:** CSP Recommender (agente LLM diario, cron job, doc_type `csp_recommendation`)
- ❌ **Eliminado:** Módulos `src/dgi_csp_recommender.py` y `src/dgi_csp_recommender_instructions.py`
- ✅ **Añadido:** Botón "Quick Analysis" en dashboard → reutiliza `/api/chat` con `mode: "quick-analysis"` (endpoint existente)
- ✅ **Añadido:** Botón "Add to Watchlist + CSP Watch" en dashboard → integra con sistema CSP existente
- ✅ **Beneficio:** Una sola fase de implementación (Screener + Web Dashboard con botones manuales)
- ✅ **Beneficio:** Costo LLM reducido (solo por análisis manual, no automático diario)

**Implementación:**
- Botón "Quick Analysis" dispara análisis LLM del símbolo en modal (reutiliza feature existente, sin agent nuevo)
- Botón "Add to Watchlist" integra el símbolo con el monitoring CSP existente (agente CSP existente lo monitorea automáticamente)
- No se requieren cambios a agentes existentes

**Key Files Updated:**
- Propuesta: `.squad/decisions/inbox/danny-dgi-screener.md` (v2.1 — secciones 1, 6, 7, 8, 9, 10, 11, 12, 13, 14 reescritas)
- Decisión formal: `.squad/decisions/inbox/danny-scope-simplification.md` (nueva)

**Fases Finales:**
- Fase 1 (3-4 días): DGI Screener MVP (diario, yfinance, Top 20, CosmosDB)
- Fase 2 (2-3 días): Web Dashboard + dos botones manuales
- Fase 3 (1-2 días): Refinamiento y robustez

### 2026-05-10: Technical Timing Indicators Added to DGI Screener Proposal

**User Directive (dsanchor/David):** El Top 20 DGI debe reflejar acciones con buenos puntos de entrada técnicos, no solo buenos fundamentales. "Las buenas DGI ya las conozco — quiero saber cuáles tienen buen entry point."

**Architecture Decision:**
- Quality score rebalanceado: 70% fundamental + 30% technical timing
- 4 indicadores técnicos programáticos: RSI(14), SMA(50/200) position, distancia 52w high, Bollinger Bands(20,2)
- Fuente datos: `yfinance` `ticker.history(period="1y")` → OHLCV diario
- TradingView como backup para datos técnicos
- Indicadores técnicos también se inyectan como contexto al CSP Recommender LLM (sección `== TECHNICAL INDICATORS ==`)
- Todos los parámetros configurables en `config.yaml` bajo `dgi_screener.technical_indicators` y `dgi_screener.score_weights`

**Key Files:**
- Propuesta: `.squad/decisions/inbox/danny-dgi-screener.md` (secciones 2, 4.2, 6.4, 6.5, 8.2, 11, 14 actualizadas)
- Decisión: `.squad/decisions/inbox/danny-technical-timing.md`

**User Preferences:**
- David quiere que el screener sea "opinionated" sobre timing — no una lista genérica
- Indicadores técnicos siempre programáticos (sin LLM) — el LLM solo en CSP Recommender
- yfinance es la fuente primaria para todo (fundamentals + technicals)

### CosmosDB Settings Container Documentation (2026-03-30)
- Updated `README.md` with comprehensive "Settings Container" section covering:
  - Feature overview with deep-merge behavior and use cases
  - Setup and initialization instructions (automatic on first run)
  - Configuration API reference with endpoint signatures
  - Example JSON payloads for nested config updates (e.g., telegram settings)
  - Troubleshooting guide for common configuration issues
  - Cross-reference to Rusty's implementation details
- Documentation included in commit fa64388 alongside implementation.
- Ensures users can independently manage runtime configuration via API or file.

### Telegram Documentation (2026-03-29)
- Updated `README.md` with:
  - **Telegram Alerts section** — Feature overview (real-time decision/alert notifications)
  - **Setup Instructions** — Step-by-step: BotFather, channel ID, env vars/config.yaml, test via /settings
  - **Configuration Reference** — Schema: `telegram.bot_token`, `telegram.channel_id`, `enabled`
  - **Project Structure** — Added `src/telegram_notifier.py`, `.squad/orchestration-log/`, `.squad/log/` references
- Commit: 4e1c16c.

### 2026-03-27: Model Configuration Updated to gpt-5.1

**User Directive (dsanchor):** Updated model from gpt-5.4-mini to gpt-5.1 in config/team.md

**Reason:** gpt-5.1 shows superior performance on multi-step TradingView Playwright workflows (navigate → click → snapshot sequences for options chain extraction). gpt-5.4-mini struggled with complex sequential browser instructions.

**Impact for Danny's Work:**
- Any downstream systems consuming agent outputs should verify compatibility with gpt-5.1 decision quality
- Model change applies to all providers (Massive.com, Alpha Vantage, TradingView) via team config inheritance
- Output format remains consistent (JSON+SUMMARY as per Rusty's 2026-03-27 update)
- No API contract changes, only model selection in config

**Status:** ✅ Updated in config/team.md
**Team:** User directive (dsanchor), Rusty (config implementation)

### 2026-03-28: CosmosDB-Centric Architecture Design (IMPLEMENTED)

**User Directive (dsanchor):** Full architectural refactor from file-based to CosmosDB-backed symbol-centric data model.

**Key Architecture Decisions:**

1. **Hybrid document model (NOT single-document-per-symbol):** A single document would exceed CosmosDB's 2MB limit within months for active symbols (~7MB/year in decisions alone). Instead: partition key = symbol ticker, with 3 document types in one container: `symbol_config`, `decision`, `signal`.

2. **Partition key = symbol ticker:** All queries for a symbol (config + decisions + signals) are single-partition and fast. Cross-partition queries only for dashboard aggregation (low QPS, acceptable).

3. **Positions embedded in symbol_config:** Positions per symbol are few (<20), so embedding avoids extra document lookups. Position lifecycle: `active` → `closed`.

4. **Serverless CosmosDB default:** Low traffic (50 operations/day). Pennies per month. Autoscale provisioned is the upgrade path.

5. **TTL on decision documents (90 days):** Prevents unbounded growth. Signals kept indefinitely for audit trail.

6. **Context injection adapter pattern:** `src/context.py` wraps CosmosDB reads and returns formatted strings identical to the old `logger.py` output. Agent instructions remain unchanged.

7. **Agent runner no longer owns discovery:** Scheduler queries CosmosDB for enabled symbols/positions and passes them individually to the runner. Runner only handles single-symbol execution.

**Implementation Status: ✅ COMPLETE (all 4 phases delivered as of 2026-03-28)**

**Implementation Timeline:**
- **Phase 1 (2026-03-28T13:50):** Rusty — CosmosDB service layer + config. Created `src/cosmos_db.py` (18 methods), `src/context.py`, updated config layer. Orchestration log: `2026-03-28T1350-rusty-phase1.md`
- **Phase 2 (2026-03-28T13:55):** Rusty — Scheduler + agent runner refactor. Refactored `main.py`, `agent_runner.py`, all 4 agent wrappers to use CosmosDB. Orchestration log: `2026-03-28T1355-rusty-phase2.md`
- **Phase 3 (2026-03-28T14:00):** Rusty — Web dashboard refactor. Rewrote `web/app.py` with REST API, created `symbols.html`, `symbol_detail.html`. Orchestration log: `2026-03-28T1400-rusty-phase3.md`
- **Phase 4a (2026-03-28T13:50):** Basher — Provisioning + deployment. Created `scripts/provision_cosmosdb.sh`, `scripts/migrate_to_cosmosdb.py`, updated Dockerfile, comprehensive README. Orchestration log: `2026-03-28T1350-basher-phase4a.md`

**Key Files Modified/Created:**
- Architecture doc: `.squad/decisions/inbox/danny-cosmosdb-refactor-architecture.md` (kept as reference, 1288 lines)
- Modules: `src/cosmos_db.py`, `src/context.py`, `scripts/migrate_to_cosmosdb.py`, `scripts/provision_cosmosdb.sh`
- Modified: `config.yaml`, `src/config.py`, `src/agent_runner.py`, `src/main.py`, `web/app.py`, all 4 agent modules, Dockerfile, README.md
- Deprecated: `src/logger.py`, `data/*.txt`, `logs/*.jsonl`
- New web templates: `web/templates/symbols.html`, `web/templates/symbol_detail.html`
- New dependency: `azure-cosmos>=4.7.0`

**User Preferences (dsanchor):**
- Prefers comprehensive design docs with actual schemas, code, and CLI commands ✅
- Wants everything symbol-centric (per-symbol settings, not global config files) ✅
- Only global setting = cron expression ✅
- Full CRUD through web dashboard ✅

**Team Cross-References:**
- **Rusty (Agent Dev):** Implemented all 3 phases (service layer, scheduler, web); architecture fully realized
- **Basher (Tester):** Implemented provisioning phase; tested phases 1–3 before handoff
- **Linus (Quant):** Agent instructions unaffected — context format identical; zero downstream impact
- **Session log:** `.squad/log/2026-03-28T1347-cosmosdb-refactor-implementation.md`

**Status:** ✅ Architecture delivered and fully implemented

## Learnings

**2025-03-29: Domain Entity Rename (decision→activity, signal→alert)**
- Completed exhaustive rename of two core domain concepts across agent instruction files and README
- Changed "decision" → "activity" to reflect that these are agent outputs/actions, not decisions
- Changed "signal" → "alert" to clarify these are actionable notifications, not trading signals
- Updated JSON schema fields in all 4 instruction files (tv_covered_call, tv_cash_secured_put, tv_open_call, tv_open_put)
- Updated all prose, examples, section headers, and documentation in README.md
- Preserved context-specific uses: "FDA decision", "regulatory decision", "technical signals" remain unchanged
- Used systematic sed replacements to ensure consistency across ~800+ lines of instruction text
- Verified zero remaining incorrect references in owned files

### 2026-03-31: Deep Feature Analysis — DGI + Options Strategy

**Context:** Full codebase audit to map current capabilities and propose DGI-specific features.

**Current Architecture (Key Files):**
- `src/agent_runner.py` (500+ lines) — ChatAgent execution, JSON/summary extraction, activity/alert persistence, telemetry
- `src/cosmos_db.py` (800+ lines) — CosmosDB service: symbols, positions, activities, alerts, telemetry, settings
- `src/tv_data_fetcher.py` (1130 lines) — Hybrid BS4 + Playwright: overview, technicals, forecast, dividends, options chain
- `src/context.py` — Activity history injection into agent prompts (last N activities per symbol)
- `src/main.py` — Cron-based scheduler, sequential agent execution (CC → CSP → OpenCall → OpenPut)
- `web/app.py` (1608 lines) — FastAPI: REST APIs, dashboard, symbol CRUD, positions, chat, settings, triggers
- 4 agent wrappers: covered_call, cash_secured_put, open_call_monitor, open_put_monitor
- 4 instruction files: ~12-18KB each, comprehensive analysis frameworks
- 14 HTML templates: dashboard, symbols, symbol_detail, activity/alert detail, chat, settings (3 tabs), fetch_preview

**Key Observations for Feature Planning:**
- Data model is symbol-centric with partition key = ticker; doc_types: symbol_config, activity, alert
- Positions embedded in symbol_config with lifecycle: active → closed; supports roll traceability
- No premium/income tracking — positions store strike/expiration but not premium collected
- No dividend tracking beyond what TradingView provides (ex-div dates, yield)
- No portfolio-level aggregation views (total premium, total dividend income, sector exposure)
- No historical P&L or position outcome tracking (did assignment happen? net result?)
- Chat exists (global + per-symbol) but has no persistent memory or strategy awareness
- Agents run sequentially per type — no cross-agent coordination
- TradingView fetcher gets 5 data types; options chain uses Playwright (slow ~15s per symbol)
- User preference: symbol-centric everything, only global = cron; prefers comprehensive docs

**Deliverable:** Feature proposal report (see task output below)

### TradingView Anti-Bot Monitoring and Configuration (2026-04-01)
- TradingView 403 bot detection blocking has been addressed with comprehensive anti-bot measures (Linus implementation)
- **Deployment consideration:** Rate limiting default (1-3s per request) means fetch times increase 5-15s per symbol
- **Current cron schedule (every 4h):** Should remain sufficient; adjust if batch fetch times exceed 15-20 minutes
- **Monitoring action items:**
  - Track 403 errors in logs to measure effectiveness of anti-bot implementation
  - If 403s persist, increase `tradingview.request_delay_max` to 5-10 seconds in config.yaml
  - Monitor successful fetch rates to ensure no performance degradation
- **Configuration reference:** `config.yaml` → `tradingview.request_delay_min/max` settings
- **Documentation:** See TRADINGVIEW_ANTI_BOT.md for full technical details and troubleshooting

### 2026-04-01: CosmosDB Unified Container Migration (PROPOSED)

**Architecture Decision:** Merge activity and alert documents into a single unified model.

**Key Changes:**
- **ID schema:** Drop `dec_` and `sig_` prefixes. New format: `{symbol}_{agent_type}[_{position_id}]_{ts_compact}`
- **Data model:** Eliminate separate alert documents. Alerts become activities with `is_alert=true`. The `write_alert()` method becomes `mark_as_alert()` (in-place update).
- **Query impact:** All `doc_type='alert'` queries change to `doc_type='activity' AND is_alert=true`
- **Migration:** Offline batch (~2-5min downtime). Merge alert docs into parent activities, strip ID prefixes, delete old alert docs.
- **Rollback:** JSON backup export before migration, 7-day retention.

**User Preferences (dsanchor):**
- Explicitly requested removal of `dec_`/`sig_` prefixes — these were from previous "decision/signal" naming
- Wants migration plan for existing CosmosDB data, not just forward-looking schema change
- Confirmed unified container approach (single doc type with boolean discriminator)

**Files Impacted:**
- `src/cosmos_db.py` — ID generation (line 429, 462), `write_alert()` → `mark_as_alert()`, query filters
- `src/agent_runner.py` — alert creation flow
- `web/app.py` — alert query endpoints, detail views
- `scripts/provision_cosmosdb.sh` — indexing policy update
- New: `scripts/migrate_unified_schema.py`

**Decision doc:** `.squad/decisions/inbox/danny-cosmosdb-migration.md`
**Status:** Proposed — awaiting user approval to implement

## Orchestration Session (2026-04-01T21:39:57Z)

**Session:** CosmosDB Unified Schema — Decision Consolidation and Team Orchestration

**Status:** All decisions merged from inbox into decisions.md. Orchestration logs created for all four team members (Danny, Rusty, Linus, Basher).

**Session Log:** `.squad/log/2026-04-01T21-39-cosmosdb-unified-schema.md`

**Team Coordination:**
- ✓ Danny: Design document complete, migration strategy finalized
- ✓ Rusty: cosmos_db.py implementation complete, awaiting migration script execution
- ✓ Linus: agent_runner.py refactor complete, awaiting Rusty's cosmos_db.py PR merge
- ✓ Basher: Migration script complete with dry-run, backup, restore, and validation phases

**Orchestration Logs:**
- `.squad/orchestration-log/2026-04-01T21-39-danny.md` — Design and strategy
- `.squad/orchestration-log/2026-04-01T21-39-rusty.md` — Implementation status and backwards compatibility
- `.squad/orchestration-log/2026-04-01T21-39-linus.md` — Refactoring and signal write path simplification
- `.squad/orchestration-log/2026-04-01T21-39-basher.md` — Migration script with defensive testing practices

**Next Steps:**
1. User approval to schedule migration downtime (2-5 min)
2. Dry-run migration script against production data
3. Review transformation summary for orphaned alerts, collisions
4. Execute: Stop app → run migration → validate → restart app
5. Smoke test: Trigger one agent run, verify new ID format
6. Delete backup after 7 days

### 2026-04-01: TradingView Anti-403 Architecture (PROPOSED)

**Context:** User (dsanchor) experiencing persistent 403 errors from TradingView, particularly on previously-banned symbols. Hypothesis: TradingView blacklists client config (cookies/session fingerprint) when accessing certain "hot" resources.

**Root Cause Analysis:**
- Single `requests.Session()` per agent type reused across all symbols (20-50 per agent)
- TradingView builds client fingerprint from cookie accumulation
- Global `has_403` flag taints entire agent run when one symbol fails
- Predictable symbol processing order (CosmosDB query result, deterministic)
- No session rotation or recovery after 403

**Architecture Decision: Per-Symbol Session Isolation + Graduated Recovery**

**Key Changes:**
1. **Per-symbol sessions** — Create fresh `requests.Session()` for each symbol, discard after use
2. **Remove global `has_403`** — Replace with per-symbol error tracking in `fetch_all()` return dict
3. **Graduated cooldown** — On 403: exponential backoff (5s → 15s → 45s) + session refresh, retry 3x
4. **Symbol randomization** — Shuffle symbol order before processing (configurable)
5. **Optional warm-up** — Visit TradingView homepage before fetching data (establishes "organic" cookies)

**Implementation Strategy:**
- **Phase 1 (High Priority):** Move `async with create_fetcher()` inside symbol loop (per-symbol sessions), remove `self.has_403`
- **Phase 2 (High Priority):** Replace `_check_403()` with `_fetch_with_403_recovery()` (exponential backoff + session refresh)
- **Phase 3 (Medium Priority):** Add `random.shuffle(symbols)` in agent wrappers
- **Phase 4 (Low Priority):** Add `_warmup()` method (homepage visit, configurable)

**Config Changes:**
```yaml
tradingview:
  warmup_enabled: false  # Visit homepage before data fetch
  max_403_retries: 3
  retry_delays: [5, 15, 45]  # Exponential backoff (seconds)
  randomize_symbols: true
```

**Files Impacted:**
- `src/tv_data_fetcher.py` — Remove `has_403`, add `_fetch_with_403_recovery()`, return error dict
- `src/covered_call_agent.py`, `cash_secured_put_agent.py`, `open_call_monitor_agent.py`, `open_put_monitor_agent.py` — Move fetcher creation inside symbol loop, add shuffle
- `src/agent_runner.py` — Check `data.get("error_403")` instead of `fetcher.has_403`
- `config.yaml` — Add new tradingview config section

**Success Metrics:**
- Before: 20-30% 403 rate, cascading failures across symbols
- Target: <5% 403 rate, isolated failures (one 403 doesn't taint others)

**Risk Assessment:**
- Session overhead: ~50ms × 50 symbols = 2.5s (negligible vs 5-15s network I/O per symbol)
- Retry overhead: 3 retries × 45s × 10% failure = ~11min for 50 symbols (acceptable for 4h cron)
- IP-based blocking: If problem persists, user needs proxy rotation (out of scope)

**Alternatives Rejected:**
- Proxy rotation (requires external infrastructure)
- Playwright for all resources (10x slower)
- Per-resource session rotation (too granular, triggers rate-limiting)

**Status:** Architecture proposed in `.squad/decisions/inbox/danny-anti403-strategy.md` — awaiting user (dsanchor) review and approval

**Team Assignment:**
- **Rusty:** Implementation (Phases 1–4)
- **Basher:** Testing (403 simulation, retry verification, symbol randomization check)
- **Linus:** No action required (agent instructions unchanged)

### 2026-04-22: Monitor Agent Split Assessment — Position Monitor + Roll Management

**Context:** User (dsanchor) reported monitor agents miscalculating credits / not reading option chain correctly. Proposed splitting each monitor agent into 2 sequential agents: (1) Position Monitor (WAIT vs action decision) and (2) Roll Management (chain reading + roll economics).

**Assessment Result: ✅ RECOMMENDED**

**Root Cause Analysis:**
- Current monitor instructions are ~590-600 lines each (call/put)
- Agent must process: 25-row earnings matrix + 8 analysis dimensions + profit optimization gate + premium-first roll policy + roll search algorithm — ALL in one context window
- The chain reading task (exact JSON key-path navigation + bid/ask arithmetic) is buried at line ~340 of 590, after heavy analysis work
- Context dilution is the likely cause: model attention degrades on precise arithmetic tasks when competing with 600 lines of multi-dimensional analysis

**Key Design Points:**
- Agent 1 (Position Monitor): ~350-400 lines. Gets overview/technicals/forecast + current contract delta/IV only. Handles earnings gate, 8 analysis dimensions, WAIT/ROLL decision, profit optimization gate.
- Agent 2 (Roll Management): ~200-250 lines. Gets full filtered chain + Agent 1's output + pivot points. Handles roll types, premium-first policy, roll search algorithm, verification.
- ~70-80% of runs are WAIT → Agent 2 never invoked → token savings
- Handoff via structured JSON intermediate format
- Runner becomes 2-phase: `_run_position_assessment()` → conditional `_run_roll_management()`
- Activity/alert persistence model unchanged

**Decision doc:** `.squad/decisions/inbox/danny-monitor-split.md`
**Status:** Recommended — awaiting user approval

### 2026-07-17: Contrarian / Devil's Advocate Agent Architecture (PROPOSED)

**Context:** User (dsanchor) wants the system to self-debate before presenting final recommendations. Currently dsanchor manually challenges every decision — he wants automation of the second-guessing process.

**Architecture Decision: Pipeline Selectivo + Vista Integrada (Opción D)**

**Key Design Points:**
- Contrarian runs as Phase 3 of the pipeline, ONLY when `is_alert=true` (SELL, ROLL_*, CLOSE)
- Output persisted as `contrarian_view` field inside the activity document (no separate doc)
- Challenge strength rating (STRONG/MODERATE/WEAK) prevents analysis paralysis
- Binary `net_assessment` (ORIGINAL_HOLDS / RECONSIDER) forces the contrarian to take a position
- Telegram integration: only shows contrarian line if `challenge_strength >= MODERATE`
- Zero incremental data cost — reuses market data already in memory
- On-demand "Challenge" button in dashboard for WAITs (deferred to Phase 2)

**Anti-Noise Mitigations:**
- Prompt explicitly instructs "if decision is obviously correct, rate WEAK — do NOT manufacture objections"
- Telegram filter: WEAK challenges not notified
- Max 1 contrarian per symbol per cycle
- Dashboard color coding: 🟢 WEAK / 🟡 MODERATE / 🔴 STRONG

**User Preferences (dsanchor):**
- Wants system to self-debate, not just advise
- Open on UX — accepted pipeline approach over chat-only
- Communicates in Spanish — proposal written in Spanish
- Values concrete proposals with actual file paths and code patterns

**Files Impacted (MVP):**
- New: `src/tv_contrarian_instructions.py`
- Modified: `src/agent_runner.py` (new `_run_contrarian_review()`, integration in `run_symbol_agent()` and `run_position_monitor()`)
- Modified: `src/cosmos_db.py` (new `update_activity_field()` method)
- Modified: `web/templates/activity_detail.html` (contrarian panel)
- Modified: `src/telegram_notifier.py` (contrarian line in alerts)

**Team Assignment:**
- Linus: Contrarian instructions (`tv_contrarian_instructions.py`)
- Rusty: Pipeline integration (`agent_runner.py`, `cosmos_db.py`)
- Basher: Frontend + Telegram + tests

**Decision doc:** `.squad/decisions/inbox/danny-contrarian-agent-architecture.md`
**Status:** Proposed — awaiting user (dsanchor) approval

- **Rusty:** Fetcher wrapper, screener agent, CSP recommender agent implementation
- **Linus:** Agent instructions (if LLM-driven), custom metrics formulas
- **Basher:** Cache implementation, monitoring, testing

**Decision doc:** `.squad/decisions/inbox/danny-dgi-screener.md`
**Status:** Proposed — comprehensive architecture with free data sources, awaiting user approval for Phase 1 implementation

**Alternatives Rejected:**
- Alpha Vantage Free Tier (500 req/day insufficient)
- Direct scraping of Dividend.com/Seeking Alpha (TOS violations, high block risk)
- IEX Cloud Free (dividend data requires paid tier)
- Massive.com MCP integration (complexity not justified, unknown endpoint availability for DGI metrics)

**Key Insight:** yfinance provides 90% of needed DGI metrics for free, with existing TradingView scraping covering the remaining 10% (options chain for CSP evaluation). The architecture leverages proven patterns from existing agents while adding minimal new complexity.

### 2026-05-10: DGI Screener Proposal v2 (Feedback Incorporated)

**Context:** User (dsanchor) reviewed v1 proposal and provided detailed feedback. Regenerated architecture proposal in Spanish, overwriting the previous version.

**Key Architecture Changes from v1 → v2:**

1. **Top 20 (not 50):** User wants a compact, high-signal list — exactly 20 entries always
2. **Categorización automática programática:** Stocks categorized as Compounder/High Yield/Aristocrat/Balanced/Rising Star based on metrics thresholds (yield, CAGR, payout, years). Priority order defined.
3. **Tracking de permanencia diario:** Each stock tracks `days_on_list` counter — increments daily if repeating, resets to 0 for new entries. Weekly snapshots stored for history.
4. **CSP Recommender usa LLM (gpt-5.1):** NOT programmatic — the LLM agent evaluates options chain and generates recommendations with reasoning, following the same AgentRunner pattern as existing CSP agent.
5. **DGI Screener es 100% programático:** No LLM involvement — yfinance data + custom calculations only.
6. **Sin Telegram:** No notifications for this feature — store only in CosmosDB.
7. **Dashboard separado:** New "DGI Screener" menu section, completely independent from main dashboard. "Añadir a seguimiento" button integrates with existing CSP watcher.
8. **Solo S&P 500:** No mid-caps expansion yet.

**CosmosDB Design:**
- New container `dgi_screener` (partition key: `/symbol`)
- Three doc_types: `dgi_top20` (active list), `csp_recommendation` (LLM output), `daily_snapshot` (historical)
- Separate from `symbols` container to avoid cross-partition contamination

**User Preferences (dsanchor):**
- Prefers Spanish for proposal documents
- Wants specific threshold values for categorization (not vague)
- Wants LLM-driven CSP analysis (not just calculations)
- No Telegram for screener features
- Compact list (20) over broad list (50)

**Decision doc:** `.squad/decisions/inbox/danny-dgi-screener.md` (v2, overwrites v1)
**Status:** Proposed — awaiting user approval to begin Phase 1 implementation

### 2026-05-14: yfinance Full Migration — Architecture Transition Plan

**Context:** Linus completed feasibility deep-dive confirming yfinance can replace TradingView + StockAnalysis.com for ~90% of data needs (remaining 10% is computable: Greeks via Black-Scholes, technicals from OHLCV). User directive: NO fallback, clean cut.

**Architecture Decisions Made:**

1. **New `yfinance_data_provider.py`** — High-level orchestrator (separate from existing `yfinance_fetcher.py` which stays as low-level wrapper). Clean separation of concerns: fetcher knows yfinance, provider knows agent data contracts.

2. **Options chain format preserved** — Keep existing expiration→strike→contract dict structure. Build from DataFrames instead of parsing raw scanner JSON. Add volume, openInterest, lastTradeDate (yfinance bonus data). Remove OPRA symbols, bid_iv/ask_iv.

3. **Greeks in dedicated `greeks_calculator.py`** — Black-Scholes via `py_vollib`. Separate module for testability. Risk-free rate from ^TNX with 4.5% fallback.

4. **Technicals in dedicated `technicals_calculator.py`** — Uses `pandas-ta` for 15+ indicators. Ports signal logic from `tv_data_fetcher.py`. Consolidates with `dgi_metrics.py` (DGI metrics delegates to this for RSI/SMA/Bollinger).

5. **Pivot points dropped** — Replaced with SMA-based strike selection guidance. Can add back if agents struggle.

6. **All `tv_*` instruction files renamed** (drop prefix) — 14 files. Content updated to remove TradingView references, add liquidity/staleness guidance.

7. **Caching simplified** — Replace `tv_cache.py` (async locks, per-resource TTLs) with simple TTL dict. yfinance is fast enough.

**File Impact:** 7 files deleted (4,227 lines), 3 new files created (~830 lines), 16 files modified, 12 instruction files renamed+updated. Net: ~3,400 lines removed, Dockerfile loses Playwright (~200MB image reduction).

**5 Phases:** Foundation (1d, Linus) → Pipeline Swap (1.5d, Rusty) → Instructions (1d, Linus) → Cleanup (0.5d, Rusty) → Optimization (1d, both). Total: 4-5 days.

**Key Risk:** Options chain format mismatch is highest severity. Mitigated by keeping exact same dict structure and testing end-to-end in Phase 2 before proceeding.

**Decision doc:** `.squad/decisions/inbox/danny-yfinance-transition-plan.md`
**Status:** Approved — ready for Phase 1 implementation

---

## 2026-05-14 — Architecture Transition Plan (yfinance Migration)

**Session:** 20260514T0539Z  
**Outcome:** 5-phase transition plan delivered to decisions.md

### Plan Summary
- **Duration:** 4-5 days estimated
- **Files to Delete:** 7 files (4,227 lines)
  - `tv_data_fetcher.py`, `tv_cache.py`, `stockanalysis_fetcher.py`, `options_chain_parser.py`
  - `TRADINGVIEW_ANTI_BOT.md`, legacy instruction files
- **Files to Create:** 3 files
  - `yfinance_data_provider.py` (unified provider)
  - `greeks_calculator.py` (Black-Scholes)
  - `technicals_calculator.py` (indicators)
- **Files to Modify:** 16 files (core agents, config, screeners)

### Phases
1. **Phase 1:** Create new provider modules
2. **Phase 2:** Update agents to use new provider
3. **Phase 3:** Delete legacy code
4. **Phase 4:** Config migration
5. **Phase 5:** Testing and validation

### Status
✅ Plan approved. Ready for implementation.

### 2026-06-04: Position Snapshot Time-Series Design (PROPOSED)

**Context:** Requested design for storing lightweight historical metrics on each open-position monitor run so the position detail page can render a compact evolution chart.

**Architecture Decisions Made:**
- Recommended a new CosmosDB container `position_snapshots` instead of reusing `symbols`, because this feature is position-scoped and needs partition key `/position_id`.
- Recommended container-level TTL of 180 days so retention stays cheap without adding prune jobs.
- Defined one snapshot document per monitor run with directional + absolute gap-to-strike fields, RSI (14), MACD level, and denormalized position metadata.
- Placed the write immediately after `fetch_all()` in `run_position_monitor()` so snapshots persist even if later agent phases fail.
- Required best-effort persistence only, following the existing telemetry pattern.

**Decision doc:** `.squad/decisions/inbox/danny-position-snapshots.md`
**Status:** Proposed — ready for implementation
