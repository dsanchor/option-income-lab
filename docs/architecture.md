# Architecture

[← Back to README](../README.md)

Eight specialized agents handle options trading and stock screening:
- **Covered Call Agent**: Analyzes stocks for covered call writing opportunities
- **Cash Secured Put Agent**: Analyzes stocks for cash secured put opportunities
- **Open Call Monitor**: Monitors open covered call positions for assignment risk
- **Open Put Monitor**: Monitors open cash-secured put positions for assignment risk
- **Supervisor Agent (Quality Auditor)**: Validates trading decisions for data errors, blind spots, and unaddressed risks — acts as a quality gate ensuring the primary agents' work is accurate
- **Alpha Advisor Agent (Aggressive Perspective)**: Provides alternative, more aggressive viewpoints when technically justified — suggesting higher-premium strikes, shorter DTE, or bolder entries to complement the conservative primary agents
- **Report Agent**: Generates comprehensive per-symbol reports combining technical analysis, dividends, options chain, open position risk, and monitoring recommendations
- **DGI Screener**: Screens a configurable stock universe for top dividend growth investing candidates, ranking by composite quality score (70% fundamental + 30% technical timing) and selecting the Top 20
- **Buy Tracker Agent**: AI-powered DCA timing agent that evaluates 5 dimensions (value entry/pullback, trend, momentum, income/fundamentals, calendar/risk) and produces STRONG_BUY, BUY, or WAIT signals per symbol. Designed for patient accumulation timing — not momentum trading
- **Portfolio Enrichment**: Background process that enriches watchlist symbols with DGI quality scores, technical timing, momentum signals, and category classification. Results power the watchlist UI columns and signal filters

The first two agents (sell-side) decide whether to **open** new positions. The next two (position monitors) decide whether to **hold or adjust** existing positions. The Supervisor and Alpha Advisor run as Phase 3 in parallel — after the primary decision is written but before Telegram notifications, providing quality assurance and aggressive alternatives respectively. The report agent provides on-demand deep-dive analysis accessible from each symbol's detail page. Additionally, **per-symbol chat** is available directly from the symbol detail page, offering context-aware conversations with pre-loaded market data via the yfinance provider.

Both sell-side agents use the Microsoft Agent Framework (`agent-framework`) with Yahoo Finance (yfinance) as the data source. All market data — overview, technicals, forecast, dividends, and full options chains — is fetched via the `yfinance` Python library. No browser, no scraping, no authentication required. Data is pre-fetched deterministically and passed to the LLM for analysis. The LLM never makes HTTP requests directly.

**Storage backend:** Azure CosmosDB with five containers: `symbols` (watchlists, positions, activities, alerts, reports), `telemetry` (runtime performance stats with 30-day TTL), `settings` (application configuration persistence), `dgi_screener` (DGI screening results and daily snapshots), and `calendar` (cached earnings and ex-dividend dates from Yahoo Finance). Each symbol is a partition key in the symbols container containing four document types: `symbol_config` (watchlist flags + positions), `activity` (full audit trail), `alert` (actionable alerts), and `report` (generated symbol reports). The telemetry container tracks data fetch durations and agent run times, displayed on the Settings page. The settings container persists application configuration with partition key `/id`. The dgi_screener container stores current Top 20 entries and daily snapshots for historical tracking, partitioned by `/symbol`. The calendar container stores event data partitioned by `/symbol`. See the [Provisioning CosmosDB](deployment.md#3-provision-cosmosdb) section for details.

## How It Works

End-to-end flow for each scheduled run:

```
Scheduler (main.py)
  │
  ├─ Query CosmosDB for symbols with watchlist.covered_call = true
  │    for each symbol:
  │      1. Load per-symbol context (recent activities + alerts from CosmosDB)
  │      2. Pre-fetch market data via yfinance (overview, technicals, forecast, dividends, options chain)
  │      3. LLM analyzes pre-fetched data → structured JSON activity
  │      4. Write activity to CosmosDB; if SELL → also write alert document
  │      5. Phase 3 (Supervisor + Alpha): If alert or prolonged WAIT → quality audit + aggressive alternative (in parallel)
  │      6. Telegram notification includes supervisor/alpha one-liners (if MODERATE/STRONG)
  │
  ├─ Query CosmosDB for symbols with watchlist.cash_secured_put = true
  │    (same loop with supervisor + alpha phase, different agent instructions)
  │
  ├─ Query CosmosDB for symbols with active call positions
  │    for each position:
  │      1. Load position details from symbol_config
  │      2. Pre-fetch market data via yfinance
  │      3. Phase 1 (Assessment): LLM evaluates assignment risk → WAIT or handoff to Phase 2
  │      4. Phase 2 (Roll Management): Selects specific roll targets from filtered options chain, calculates economics
  │      5. Write activity to CosmosDB; if ROLL/CLOSE → also write alert
  │      6. Phase 3 (Supervisor + Alpha): If alert or prolonged WAIT → quality audit + aggressive alternative (in parallel)
  │      7. Telegram notification includes supervisor/alpha one-liners (if MODERATE/STRONG)
  │
  └─ Query CosmosDB for symbols with active put positions
       (same two-phase pipeline with supervisor + alpha phase, different agent instructions)
```

**Data gathering:** Python pre-fetches ALL market data deterministically via `YFinanceDataProvider` (`backend/src/yfinance_data_provider.py`). Five data types are fetched per symbol — overview, technicals, forecast, dividends, and options chain — all through the `yfinance` Python library. No browser, no scraping, no authentication required. The provider includes built-in rate limiting (2 calls/sec) and a TTL cache (5 min) to avoid redundant fetches. Options chains include 23+ expirations with computed Greeks (delta, gamma, theta, vega) via Black-Scholes (py-vollib). The LLM never makes HTTP requests — it receives pre-fetched data as text and only performs analysis. See [Pre-fetch Architecture](#pre-fetch-architecture-yfinance) below.

**Per-symbol context injection:** Before each symbol is analyzed, the runner reads that symbol's recent activities from CosmosDB and injects them into the prompt. Each activity includes whether it triggered an alert (via the `is_alert` field). The LLM sees only context for the symbol it's currently analyzing — not a mix of all symbols. Context depth is configurable in `config.yaml` (`context.max_activity_entries`, default 2, range 0–5).

**Output:** Every symbol produces an activity (SELL, WAIT, or HOLD) written to CosmosDB as a `activity` document. Only SELL activitys also produce a `alert` document — the actionable alerts that the dashboard and downstream systems watch. Position monitors produce WAIT or ROLL activities, with ROLL/CLOSE activities creating alert documents. If Telegram notifications are enabled, a message is sent for each alert — see [Output documentation](output.md) for details.

## Pre-fetch Architecture (yfinance)

LLMs don't reliably make multi-step HTTP tool calls. When given fetching tools directly, they skip steps, fabricate data, and ignore sequencing instructions.

The solution: `YFinanceDataProvider` (`backend/src/yfinance_data_provider.py`) fetches all market data via the `yfinance` Python library — a clean, zero-auth API wrapper over Yahoo Finance. No browser automation, no scraping, no HTML parsing. It gathers five data sets per symbol with built-in rate limiting (2 calls/sec) and a TTL cache (5 min default) to avoid redundant fetches when multiple agents or endpoints request the same symbol data:

| Data | Method | Content |
|------|--------|---------|
| Overview | `yfinance` Ticker.info | Market cap, P/E, EPS, dividend yield, sector, employees, company description |
| Technicals | `yfinance` price history + `pandas-ta` indicators | Oscillators (RSI, MACD, Stochastic), moving averages (EMA/SMA 10-200), summary recommendations |
| Forecast | `yfinance` analyst data | Analyst consensus, price targets (high/median/low), ratings distribution |
| Dividends | `yfinance` dividend history + info | Dividend yield, amount, ex-date, payment frequency, payout ratio, growth history |
| Options chain | `yfinance` options API + `py-vollib` Greeks | 23+ expirations (vs ~5 from old source), strikes, bids, asks, computed Greeks (delta, gamma, theta, vega via Black-Scholes), volume, OI |

The provider returns each data type as a JSON string, ready for injection into LLM prompts. The `fetch_all(symbol)` convenience method returns all 5 types in a single call.

**Key advantages over previous architecture:**
- No browser dependencies (no Playwright, no Chromium)
- No authentication required
- 23+ option expirations with full computed Greeks
- Built-in rate limiting and caching
- Single dependency (`yfinance`) instead of `requests` + `BeautifulSoup` + Playwright

The agent is created with **no tools** — it only analyzes the pre-fetched data included in its prompt. This is the key pattern: move deterministic multi-step workflows to the host language; let the LLM do what it's good at — analysis.

## Data Cache

The yfinance provider includes a built-in TTL cache that sits between consumers (chat, report, analysis endpoints) and Yahoo Finance, eliminating redundant fetches when multiple agents analyze the same symbol in a short time window.

**How it works:**
- Cache keys are per-symbol per-data-type: `(symbol, data_type)` where `data_type` is one of `overview`, `technicals`, `forecast`, `dividends`, or `options_chain`
- Each entry has a configurable TTL (default 5 minutes) — stale entries are evicted automatically
- Rate limiting (2 calls/sec) prevents Yahoo Finance throttling
- The cache is process-local (in-memory) — no external infrastructure required

**Consumers:** The cache is used by the `/chat` endpoint (Portfolio Chat and Quick Analysis), the Report Agent, and the per-symbol analysis runner. Any component that calls `YFinanceDataProvider` benefits from deduplication transparently.

## Options Chain Cache

A separate, centralized **Options Chain Cache** (`backend/src/options_chain_cache.py`) provides the single source of truth for options chain data across the entire application. This addresses gaps in yfinance data (missing strikes) by merging with TradingView.

**Load procedure (on miss or hourly cron refresh):**
1. Fetch from **yfinance** — all expirations with computed Greeks (delta, gamma, theta, vega)
2. Fetch from **TradingView** — overlay: overwrites matching strikes (only when TradingView has non-zero bid/ask), adds missing ones
3. **Normalize expiration keys** — TradingView returns Unix timestamps while yfinance uses YYYYMMDD format; keys are normalized to YYYYMMDD before merging
4. Store merged result in cache with **30-minute TTL**

**Design rationale:**
- yfinance occasionally misses strikes that TradingView has (observed with VZ $48.5 strike)
- TradingView data fills gaps and corrects stale entries
- No market-open detection needed — cache always contains the best available merge

**Consumers:** All agents (CSP, CC, monitors), DPS analysis, web endpoints, and the options chain trigger endpoint read from this cache. On miss, the load procedure runs automatically before returning data.

## Per-symbol Context Filtering

Each symbol's analysis sees its last N activities (default 2, configurable 0–5). Each activity includes whether it triggered an alert via the `is_alert` field — there is no separate alert configuration. The context provider queries CosmosDB within the symbol's partition, returning only matching entries up to the configured limit. This prevents cross-contamination between symbols and keeps context focused.

Configurable in `config.yaml`:
```yaml
context:
  max_activity_entries: 2   # Recent activities to inject as agent context (0=none, max 5). Each activity includes its alert status.
  activity_ttl_days: 90
```

## CosmosDB Document Model

All data is stored in Azure CosmosDB across four containers:

**`symbols` container** (partition key: `/symbol`) — four document types:

| Document Type | Purpose | Growth |
|---|---|---|
| `symbol_config` | One per symbol — watchlist flags, positions, metadata | Static (updated, not appended) |
| `activity` | One per symbol per agent run — full analysis output | ~20/day per symbol |
| `alert` | One per actionable activity (SELL, ROLL, CLOSE) | ~1-5/week per symbol |
| `report` | On-demand symbol report — technical analysis, dividends, options, risk | ~1-2/week per symbol |

**`telemetry` container** (partition key: `/metric_type`) — runtime performance stats with 30-day TTL:

| Metric Type | Purpose | Fields |
|---|---|---|
| `data_fetch` | Market data fetch timing | resource, duration_seconds, response_size_chars |
| `agent_run` | End-to-end agent execution timing | agent_type, duration_seconds |

**`settings` container** (partition key: `/id`) — application configuration persistence:

| Document ID | Purpose | Persisted Sections |
|---|---|---|
| `app_config` | Application settings synchronized across all components | `context`, `scheduler`, `web`, `telegram` |

**`dgi_screener` container** (partition key: `/symbol`) — DGI screening results:

| Document Type | Purpose | Growth |
|---|---|---|
| `dgi_top` | Current top DGI entries — composite score, category, metrics | Static (replaced each run) |
| `dgi_snapshot` | Daily snapshots for historical tracking of screener results | ~1/day per symbol |

On first run, configuration from `config.yaml` is seeded into the `settings` container (except `ai`, `azure`, `gemini`, and `cosmosdb` sections which remain file-only). On subsequent runs, new keys from `config.yaml` are added to CosmosDB, but existing values are never overwritten, allowing the Settings UI to persist changes. The Settings UI reads and writes directly to CosmosDB, making configuration changes immediately available to all components (scheduler, telegram notifier, web UI) without restart. If CosmosDB is unavailable, `config.yaml` serves as the fallback.

Telemetry stats are displayed on the Settings page and auto-expire after 30 days.

Activities older than 90 days can be configured for TTL-based cleanup. Alerts are kept indefinitely for audit.

## Project Structure

Monorepo with two deployable components — `backend/` (Python FastAPI JSON API +
in-process scheduler) and `frontend/` (Next.js App Router web app acting as a BFF).
Each has its own `Dockerfile` and env vars; both deploy to the same Azure Container
Apps environment and share the same CosmosDB.

```
stock-options-manager/
├── backend/          # Python FastAPI JSON API + scheduler (Docker: backend/Dockerfile)
├── frontend/         # Next.js App Router web app / BFF (Docker: frontend/Dockerfile)
├── docs/
├── DESIGN.md
└── README.md
```

### `backend/` — Python API + scheduler

```
backend/
├── config.yaml                           # Configuration (AI provider, CosmosDB, scheduling, context limits)
├── run.py                                # Entry point (web + scheduler; --api-only / --web-only / --scheduler-only)
├── src/
│   ├── __init__.py
│   ├── main.py                           # Scheduler with immediate + periodic runs
│   ├── config.py                         # YAML config loader with env var substitution and validation
│   ├── llm.py                            # LLM provider factory (Azure OpenAI / Google Gemini)
│   ├── cosmos_db.py                      # CosmosDB service layer — all database operations
│   ├── context.py                        # Context injection adapter — formats CosmosDB data for prompts
│   ├── agent_runner.py                   # Core execution engine — yfinance pre-fetch + SkillsProvider integration
│   ├── yfinance_data_provider.py         # Yahoo Finance data provider (overview, technicals, forecast, dividends, options chain)
│   ├── options_chain_cache.py            # Centralized options chain cache (yfinance + TradingView merge, 30-min TTL)
│   ├── options_chain_filters.py          # Options chain filter pipeline + roll candidates table
│   ├── covered_call_agent.py             # Covered call wrapper
│   ├── covered_call_instructions.py      # Covered call system prompt
│   ├── cash_secured_put_agent.py         # Cash secured put wrapper
│   ├── cash_secured_put_instructions.py  # Cash secured put system prompt
│   ├── open_call_monitor_agent.py        # Open call position monitor wrapper
│   ├── open_call_assessment_instructions.py  # Open call Phase 1 (assessment)
│   ├── open_call_roll_instructions.py        # Open call Phase 2 (roll management)
│   ├── open_call_chat_instructions.py        # Chat instructions for open call
│   ├── open_put_monitor_agent.py         # Open put position monitor wrapper
│   ├── open_put_assessment_instructions.py   # Open put Phase 1 (assessment)
│   ├── open_put_roll_instructions.py         # Open put Phase 2 (roll management)
│   ├── open_put_chat_instructions.py         # Chat instructions for open put
│   ├── buy_tracker_agent.py              # Buy tracker wrapper
│   ├── buy_tracker_instructions.py       # Buy tracker system prompt
│   ├── supervisor_instructions.py        # Supervisor agent (quality auditor)
│   ├── alpha_instructions.py             # Alpha Advisor agent (aggressive perspective)
│   ├── report_instructions.py            # Report agent system prompt
│   ├── summary_instructions.py           # Summary agent system prompt
│   ├── technical_analysis_instructions.py # Technical analysis system prompt
│   ├── banner_instructions.py            # Banner instructions
│   ├── skills/                           # Native agent-framework Skills (SKILL.md format)
│   │   ├── earnings-gate-monitor/SKILL.md   # Earnings gate for open position monitors
│   │   ├── earnings-gate-sell/SKILL.md      # Earnings gate for sell-side watchers
│   │   ├── roll-economics/SKILL.md          # Premium-First Roll Policy (3-tier hierarchy)
│   │   ├── data-source/SKILL.md             # Yahoo Finance data format guide
│   │   ├── risk-flags/SKILL.md              # Risk flag taxonomy
│   │   ├── activity-log/SKILL.md            # Previous activity log interpretation
│   │   ├── cc-aristocrat/SKILL.md           # Covered call params for Aristocrat stocks
│   │   ├── cc-compounder/SKILL.md           # Covered call params for Compounder stocks
│   │   ├── cc-rising-star/SKILL.md          # Covered call params for Rising Star stocks
│   │   ├── cc-high-yield/SKILL.md           # Covered call params for High Yield stocks
│   │   ├── cc-balanced/SKILL.md             # Covered call params for Balanced stocks
│   │   ├── csp-aristocrat/SKILL.md          # Cash-secured put params for Aristocrat stocks
│   │   ├── csp-compounder/SKILL.md          # Cash-secured put params for Compounder stocks
│   │   ├── csp-rising-star/SKILL.md         # Cash-secured put params for Rising Star stocks
│   │   ├── csp-high-yield/SKILL.md          # Cash-secured put params for High Yield stocks
│   │   └── csp-balanced/SKILL.md            # Cash-secured put params for Balanced stocks
│   ├── dgi_screener.py                   # DGI Screener pipeline
│   ├── dgi_metrics.py                    # DGI metric calculations (quality score, RSI, ADX, technical timing)
│   ├── portfolio_enrichment.py           # Watchlist enrichment (DGI scores, momentum, technicals → CosmosDB)
│   ├── yfinance_fetcher.py               # Yahoo Finance data fetcher for DGI Screener
│   ├── stockanalysis_fetcher.py          # StockAnalysis.com scraper
│   └── telegram_notifier.py             # Telegram notification service
├── scripts/
│   ├── provision_cosmosdb.sh             # Azure CosmosDB provisioning via az CLI
│   └── backfill_price_forecast.py        # Historical price-forecast rebuild (+ other maintenance scripts)
├── web/                                  # FastAPI app (JSON /api/* + legacy Jinja HTML routes)
│   ├── __init__.py
│   ├── app.py                            # FastAPI app — JSON API routes + CosmosDB queries (HTML routes legacy, superseded by frontend/)
│   ├── templates/                        # Legacy Jinja2 HTML templates (kept as parity reference; new UI lives in frontend/)
│   └── static/                           # Legacy CSS/JS for the Jinja templates
├── tests/
├── run_web.py                            # Web-only entry point (legacy convenience)
├── requirements.txt
└── Dockerfile                            # api image
```

### `frontend/` — Next.js web app (BFF)

```
frontend/
├── src/
│   ├── app/                              # App Router pages + BFF route handlers
│   │   ├── api/                          # Route handlers proxying to the internal api (BFF)
│   │   ├── dashboard/  symbols/  economics/  calendar/  plans/  dgi/  chat/  settings/
│   │   ├── layout.tsx  page.tsx  globals.css   # Root layout + dark theme tokens (from DESIGN.md)
│   ├── components/                       # Server + client React components (views, TopNav, charts)
│   ├── lib/                              # api.ts (server-only apiFetch, BFF-aware via API_BASE_URL)
│   └── types/                            # TypeScript interfaces mirroring backend JSON
├── public/
├── next.config.ts                        # output: 'standalone'
├── package.json
├── .env.example                          # API_BASE_URL, etc.
└── Dockerfile                            # web image
```
