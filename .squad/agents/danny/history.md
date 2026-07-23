# Danny — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Stack:** Python, Microsoft Agent Framework, Azure Foundry (gpt-5.1)
- **MCP:** iflow-mcp_ferdousbhai_investor-agent 1.6.3
- **Description:** Two periodic trading agents for covered call and cash-secured put sell signals. Local runtime, configurable polling, stock symbols from files, decision logs, sell signal alerts.

## Core Context

### DGI Screener & Timing Architecture (2026-05 to 2026-06)
- **Scope:** Top 20 DGI candidates with technical timing indicators (RSI, SMA, Bollinger Bands) + manual "Quick Analysis" / "Add to Watchlist" buttons
- **Infrastructure:** Daily scheduler, yfinance data source, CosmosDB storage, web dashboard UI, position snapshots container (180d TTL)
- **Design principle:** Screener is "opinionated" about timing; technical indicators are always programmatic (no LLM); LLM used only in contextual analysis
- **User directive:** David wants entry-point timing, not generic stock lists. Quality score: 70% fundamental + 30% technical.

### Contrarian Agent Architecture (2026-07)
- **Decision mapping:** 4 agent types × valid decisions = 16 decision-specific playbooks with parameterized instructions
- **Anti-noise rules:** WEAK self-assessment for solid decisions; forbid arguments against risk management
- **Validation:** Agent output schema rejects invalid combinations (e.g., open_put + ROLL_UP)
- **Guardrails:** Hard 45 DTE cap, near-ATM stability buffer (3% zone), ROLL_OUT restricted to near-ATM ≤5 DTE

### Scheduler & DPS Integration (2026-06)
- **Architecture:** TaskRegistry singleton at `src/scheduler_registry.py` (185 lines) manages all 8 scheduled tasks uniformly
- **DPS integration:** Real-time scoring (4x/day via monitoring agents) replaces nightly batch job; no impact on instruction logic
- **Code impact:** `src/main.py` reduced 1266 → 736 lines (41% reduction); web UI duplication reduced 150+ lines
- **Persistence:** Last-run timestamps persisted to CosmosDB, survive scheduler restarts; all tasks uniformly expose enabled checkbox + run_now button

### Telegram & Settings Infrastructure (2026-03 to 2026-06)
- CosmosDB Settings container with deep-merge behavior, initialization on first run, configuration API reference
- Telegram notification system for decision/alert delivery; BotFather setup, env var/config.yaml integration
- Real-time settings UI with enable/disable toggles and per-task run_now buttons


## Learnings

### README Modularization (2026-07-15)

- The project adopted a **docs/ structure** with the README as a lightweight index/overview (~135 lines, down from 1720)
- **Documentation split**: Content moved VERBATIM into 11 topic-specific files:
  - `docs/concepts.md` — Core system concepts (Activity vs Alert, DPS, Supervisor, Alpha Advisor, Position Lifecycle)
  - `docs/architecture.md` — System design, agent pipeline, data flow, pre-fetch architecture, CosmosDB model, project structure
  - `docs/chat.md` — Dual-mode chat (Portfolio Chat, Quick Analysis, Per-Activity Chat)
  - `docs/screener.md` — DGI Screener, Momentum Analysis, Buy Tracker, Category-Based Strategy Skills
  - `docs/agents.md` — Summarization Agent, Symbol Report, Action Plans
  - `docs/output.md` — Activity & alert documents, example JSON, Telegram notifications
  - `docs/web-dashboard.md` — Dashboard UI, pages, features
  - `docs/local-setup.md` — Local setup, prerequisites, Python venv, Docker
  - `docs/deployment.md` — Azure deployment, CosmosDB provisioning, environment variables
  - `docs/troubleshooting.md` — Common errors and fixes
  - `docs/development.md` — Skills architecture, instruction files, SDK information
- **README convention**: README must remain a lightweight index with:
  - Philosophy (the "why")
  - Architecture overview (1 paragraph + bullet list of agents)
  - Documentation table (links to all docs/*.md files)
  - Features highlights (2-3 lines per feature with links to details)
  - Quick Start (minimal commands to get running)
  - Acknowledgments
- **Breadcrumbs**: Every `docs/*.md` file starts with `[← Back to README](../README.md)` immediately under the H1 title
- **Maintenance**: When features change, update BOTH the relevant `docs/*.md` file AND the corresponding highlight in the README's Features section
- All code blocks, tables, JSON examples, and CLI snippets were moved VERBATIM — no content was lost or summarized

### Roll Table Feature — Architecture Consultation (2026-07-23)

- **Activity detail view**: `web/app.py:3096` (`GET /activities/{activity_id}`) → template `web/templates/activity_detail.html`. Handler fetches activity from CosmosDB, passes: `activity`, `symbol`, `display_name`, `agent_label`, `agent_type`, `is_alert`. No chain data today.
- **Options chain cache**: `src/options_chain_cache.py` — singleton, 30-min TTL, yfinance (ALL expirations) + TradingView overlay. Key format: `calls[YYYYMMDD][strike_str]` and `puts[...]`. `get_or_load_async` is the correct entry point for async endpoints.
- **Existing roll math**: `src/options_chain_filters.py:403` — `format_roll_candidates_table()` already computes buyback_cost, net_credit, premium_pct, ann_ret for each candidate. Returns plain-text markdown, not structured JSON. `get_contract()` and `exclude_contract()` helpers also exist.
- **Design decision**: New endpoint `GET /api/activities/{activity_id}/roll-table` (pure JSON). Uses cache, NOT `provider.fetch_all`. New module `src/roll_table.py` with `compute_roll_table()` returning structured dict for 4 expiries × 3 strikes (ATM, +3%, -3% relative to underlying price).
- **Template**: Add JS async fetch + green/red table to `activity_detail.html`, visible only for `open_call_monitor` / `open_put_monitor` agent types.
- **Scope boundary**: Linus owns `compute_roll_table()` math; Rusty owns endpoint + template rendering.
- **Critical**: Debug endpoint (`/api/debug/agent-chain/{symbol}`) uses `provider.fetch_all` directly (bypasses cache). Roll table endpoint must use cache for latency + consistency.
