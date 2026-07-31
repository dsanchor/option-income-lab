# 🧪 Option Income Lab

> *Where boring dividends get interesting*

## Philosophy

The core strategy is **DGI (Dividend Growth Investing)** — building a portfolio of quality dividend stocks that pay you more every year. It's proven. It's reliable. It's also... boring.

Option Income Lab makes DGI *interesting* by layering options strategies on top:

- 🔍 **DGI Screener** → Find the best dividend growth stocks from the S&P 500
- 💰 **Cash-Secured Puts** → Get paid to wait for stocks you want at prices you choose
- 📈 **Covered Calls** → Squeeze extra income from stocks you already own
- 🤖 **AI-Powered Monitoring** → Agents watch your positions 24/7, suggest rolls, flag risks
- 📊 **Economics Dashboard** → Track P&L, premiums, buyback costs, RoC%, and win rate across all positions
- 📅 **Events Calendar** → Earnings and ex-dividend dates with position exposure warnings
- 🎯 **Profit Target Gate** → Auto-roll positions at 70% profit to lock in gains
- 🔮 **Price Forecast** → Deterministic volatility-cone price ranges (1d/1w/2w/4w) with a self-calibrating hit-rate — no LLM
- 📡 **Momentum & Signal Filters** → SMA50/200 + RSI + ADX momentum analysis with actionable signal categories (Ideal Puts, Ideal Calls, Accumulate, No Puts, No Calls)
- 🛒 **Buy Tracker** → AI-powered DCA timing agent scoring 5 dimensions (value entry, trend, momentum, income, calendar)

The result: a DGI portfolio that generates income from **dividends AND option premiums** — with an AI copilot keeping watch while you sleep, and a full economics dashboard tracking your P&L.

---

## Architecture

The platform runs as **two decoupled tiers** that ship as **two containers** in the same Azure
Container Apps environment and share a single CosmosDB:

```
                 ┌──────────────────────────────────────────────────────────┐
   Browser  ───► │  web  (frontend/)          Next.js 16 App Router          │  external ingress
   (HTTPS)       │  ─────────────────────     React 19 · Tailwind v4         │  :3000
                 │  Server-side BFF proxy      recharts · standalone build    │
                 └───────────────┬──────────────────────────────────────────┘
                                 │  internal DNS (API_BASE_URL)
                                 │  browser NEVER calls the api directly
                 ┌───────────────▼──────────────────────────────────────────┐
   Scheduler ◄── │  api  (backend/)           FastAPI (JSON-only)            │  internal ingress
   (in-proc)     │  ─────────────────────     APScheduler cron               │  :8000
                 │  8 AI agents · forecast     Playwright/Chromium fallback   │
                 └───────────────┬──────────────────────────────────────────┘
                                 │
                 ┌───────────────▼───────────┐   ┌──────────────────────────┐
                 │  Azure CosmosDB (NoSQL)    │   │  Yahoo Finance (yfinance) │
                 │  6 containers, /symbol PK  │   │  quotes · chains · Greeks │
                 └────────────────────────────┘   └──────────────────────────┘
```

- **`web` (`frontend/`)** — Next.js 16 App Router (React 19, Tailwind v4, recharts), built as a
  standalone Node server. It is the **public entrypoint** and acts as a **Backend-for-Frontend
  (BFF)**: every browser request for data hits a Next.js route handler that proxies to the internal
  `api` over the environment's private DNS (`API_BASE_URL`). The browser never talks to the API
  directly; authentication is delegated to Container Apps ingress.
- **`api` (`backend/`)** — FastAPI serving JSON-only `/api/*` endpoints plus an **in-process
  APScheduler** that runs the agent cron jobs. **Internal ingress only** (not publicly reachable),
  no app-level auth. Uses `yfinance` for market data and Playwright/Chromium only as a TradingView
  fallback.

### Tech stack

| Tier | Stack |
|------|-------|
| **Frontend (`web`)** | Next.js 16 (App Router, Turbopack), React 19, TypeScript, Tailwind CSS v4, recharts. `output: "standalone"`, Node 24 runtime, port **3000**. |
| **Backend (`api`)** | Python 3.12, FastAPI + Uvicorn, APScheduler, `yfinance`, Playwright (Chromium), Azure Cosmos SDK. Port **8000**. |
| **Data** | Azure CosmosDB (NoSQL, serverless) — 6 containers, symbol-centric partitioning. |
| **AI** | Azure AI Foundry (default) **or** Google Gemini, selected via `AI_PROVIDER`. |
| **CI/CD** | GitHub Actions matrix build → two GHCR images (`<repo>-api`, `<repo>-front`) → Azure Container Apps. |

### AI agents

Eight specialized AI agents power the backend:

- **Covered Call & Cash-Secured Put Agents** — Analyze entry opportunities with category-specific parameters (Aristocrat, Compounder, Rising Star, High Yield, Balanced)
- **Position Monitors** — Two-phase pipeline watches open positions for assignment risk, suggests rolls with full economics
- **Supervisor Agent** — Quality auditor reviewing every decision for data errors, blind spots, and unaddressed risks
- **Alpha Advisor** — Aggressive alternative finder identifying the single blocking parameter and offering bolder trades
- **Report Agent** — On-demand deep-dive analysis combining technicals, dividends, options chain, and risk assessment
- **DGI Screener** — Ranks S&P 500 stocks by composite quality score (70% fundamental + 30% technical timing)
- **Buy Tracker** — AI-powered DCA timing agent evaluating 5 dimensions for patient accumulation
- **Portfolio Enrichment** — Background process updating watchlist with DGI scores, momentum signals, and categories. Also records a **daily tech-timing + momentum snapshot** per symbol (rolling 90-day history), shown as a chart in the symbol detail modal — the line is the tech-timing score (0–100) and the background band color reflects the momentum of each period.
- **Price Forecast Engine** — Deterministic (no LLM) volatility-cone forecaster. A daily cron generates a probabilistic price *range* per symbol for four horizons (1d/1w/2w/4w) and, as each horizon resolves, records a self-calibrating band hit-rate and directional accuracy. New symbols get a 25-session backfill on creation. See `backend/src/price_forecast.py` + `backend/src/forecast_cron.py`.

**Data source:** Yahoo Finance via `yfinance` Python library — zero auth, no browser, 23+ option expirations with computed Greeks.

**Storage:** Azure CosmosDB with symbol-centric partitioning across 6 containers (symbols, telemetry, settings, dgi_screener, calendar, agent_traces).

**CI/CD:** On every push, a GitHub Actions matrix build publishes two images to GHCR —
`ghcr.io/<owner>/<repo>-api` (from `backend/`) and `ghcr.io/<owner>/<repo>-front` (from
`frontend/`) — tagged with the branch, commit SHA, and `latest` on the default branch. See
[Deployment](#deployment-azure-container-apps) below.

→ [Full architecture details](docs/architecture.md)

---

## Documentation

| Document | Description |
|----------|-------------|
| [**Key Concepts**](docs/concepts.md) | Activities vs Alerts, Position Monitors, DPS, Supervisor & Alpha Advisor, Position Lifecycle |
| [**Architecture**](docs/architecture.md) | System design, agent pipeline, data flow, pre-fetch architecture, CosmosDB model, project structure |
| [**Dual-Mode Chat**](docs/chat.md) | Portfolio Chat, Quick Analysis, Per-Activity Chat with historical vs live data separation |
| [**DGI Screener & Skills**](docs/screener.md) | Top 20 screener, momentum analysis, Buy Tracker, category-based strategy skills |
| [**Agents**](docs/agents.md) | Summarization Agent, Symbol Report, Action Plans monitor |
| [**Output**](docs/output.md) | Activity & alert documents, example JSON, Telegram notifications |
| [**Web Dashboard**](docs/web-dashboard.md) | Dashboard, alerts, symbols, chat, economics, calendar, settings |
| [**Running Locally**](docs/local-setup.md) | Prerequisites, setup, Python venv, Docker, configuration |
| [**Deployment**](docs/deployment.md) | Azure Container Apps, CosmosDB provisioning, environment variables |
| [**Troubleshooting**](docs/troubleshooting.md) | Common errors, connection issues, LLM auth, module imports |
| [**Development**](docs/development.md) | Skills architecture, instruction files, SDK information |

---

## Features

### 🔍 DGI Screener
Top 20 dividend growth stocks from S&P 500, ranked by composite quality score. Category classification (Aristocrat, Rising Star, Compounder, High Yield, Balanced) drives agent parameter selection. Technical timing overlay with momentum signals (Bullish, Bearish, Neutral, Overextended, Oversold) and actionable filter pills (Ideal Puts, Ideal Calls, Accumulate, No Puts, No Calls).
→ [Details](docs/screener.md)

### 💰 Cash-Secured Puts & 📈 Covered Calls
AI agents analyze entry opportunities with category-specific delta ranges, IV requirements, and premium thresholds. Earnings gates, risk rating (0-10 scale), and Supervisor/Alpha Advisor quality review on every alert.
→ [Concepts](docs/concepts.md#risk-rating-sell-side-agents) | [Category Skills](docs/screener.md#category-based-strategy-skills)

### 🤖 Position Monitors
Two-phase assessment + roll management pipeline. Watches assignment risk, suggests rolls (UP/DOWN/OUT combinations) with full economics (buyback cost, new premium, net credit/debit). Profit target gate at 70% P&L. DPS (Deterministic Position Scorer) runs automatically 4x daily during market hours.
→ [Open Position Monitors](docs/concepts.md#open-position-monitors) | [DPS](docs/concepts.md#deterministic-position-scorer-dps)

### 🛡️ Supervisor & 🔍 Alpha Advisor
Supervisor audits every SELL/ROLL decision for data errors and blind spots (9 playbooks × 4 contexts). Alpha Advisor identifies blocking parameters and offers aggressive alternatives with transparent trade-offs. Both run in parallel as Phase 3.
→ [Supervisor](docs/concepts.md#supervisor-agent-quality-auditor) | [Alpha Advisor](docs/concepts.md#alpha-advisor-agent-parameter-relaxation)

### 📊 Economics Dashboard
Track P&L across all positions with summary cards (total premium, buyback costs, net income, weighted RoC%, win rate). Filter by year/month/symbol/type/status. Monthly breakdown table, by-symbol analysis, charts (net income, calls vs puts donut), and positions detail table.
→ [Web Dashboard](docs/web-dashboard.md)

### 🔮 Price Forecast (Volatility Cone)
Deterministic, **no-LLM** price forecaster. For each symbol it projects a probabilistic price **range** (not a point prediction) over four horizons — **1 day, 1 week, 2 weeks, 4 weeks** — as a volatility cone centred on the current price and widened by realized volatility (HV) scaled by √(trading sessions). An inner band (default 68% ≈ ±1σ) and a wider reference band (95%) are both configurable.

- **Reading** — an actionable directional read (Bullish, Bearish, Overextended, Oversold, Weakening, Neutral) mapped to CSP/CC favorability, driven by the same DGI momentum engine (SMA50/200 + ADX + RSI) used across the platform, so the watchlist and the forecast never disagree.
- **Bias** — a directional value in `[-1, +1]` reported *separately* and **never folded into the band centre**. Its **sign** comes from the momentum regime; its **magnitude is graded continuously** by trend strength (ADX) and price extension from SMA50. Non-directional states (Neutral, Unknown) are `0`; Weakening carries a mild bearish lean.
- **Self-calibrating hit-rate** — as each horizon resolves, the engine records whether the close landed inside the 1σ/2σ band and whether the directional call was correct. The directional accuracy `dir NN% (c=N)` is a conditional rate over only the high-conviction claims (`|bias| ≥ 0.10`, trend-agreeing); range-bound forecasts are excluded, so `c` (claim count) may be lower than `n` (resolved endpoints). A trend-deviation metric shows how tightly price tracked the projected trend line.

Forecasts are generated by a **daily cron**; new symbols receive a **25-session backfill on creation**, and historical rebuilds are available via `backend/scripts/backfill_price_forecast.py`.
→ [Web Dashboard](docs/web-dashboard.md)

### 📅 Events Calendar
Monthly calendar view of earnings and ex-dividend dates for tracked symbols. Color-coded badges show position exposure (earnings with active position = orange, ex-div with call = red). Daily sync from Yahoo Finance.
→ [Events Calendar](docs/concepts.md#events-calendar)

### ⏸ Pause Watchlist Until Earnings
Suspend a symbol's following agents (Covered Call, Cash-Secured Put, Buy Tracker) until its next earnings date to save LLM tokens. Toggles show shadowed on the symbol detail and dashboard; auto-resumes after earnings via a daily reactivation job. Paused symbols are also excluded from the daily Telegram summary so their pre-pause analysis isn't repeated day after day (open-position monitors still report).
→ [Pause Watchlist Until Earnings](docs/concepts.md#pause-watchlist-until-earnings)

### 💬 Chat & Reports
- **Portfolio Chat** — Analyze tracked symbols with CosmosDB context (positions, activities, alerts)
- **Quick Analysis** — Fetch live Yahoo data for any symbol without saving to database
- **Per-Activity Chat** — Ephemeral advisory on specific decisions with historical vs live data separation
- **Symbol Reports** — On-demand deep-dive combining technicals, dividends, options chain, risk assessment

→ [Chat](docs/chat.md) | [Reports](docs/agents.md#symbol-report)

### 🎯 Enrichment & IV/HV Context for Agents
Sell-side agents (Cash-Secured Put, Covered Call), position monitors, and the plan monitor receive pre-computed **enrichment** (momentum, tech-timing score, entry tag, DGI category) plus a **90-day tech-timing trend** (improving / flat / deteriorating). Options agents also get a stateless **IV/HV richness** signal — current at-the-money implied volatility ÷ realized (historical) volatility — flagging premiums as rich (≥1.20), fair, or cheap (<0.90). Works from day one for any symbol, no stored IV history required. Buy Tracker gets enrichment only (no options).

### 🧾 Agent Logs
Full traceability of every agent execution under **Settings → Agent Logs**: system prompt, user message, assistant response, skills, and parsed result. Traces persist in a dedicated CosmosDB container for 90 days (auto-expiry via TTL) with a manual purge button (all / 90 / 30 / 7 days). Per-agent-type capture toggles let you enable/disable tracing individually. A filterable table (time pills 1d/7d/30d/90d, symbol, agent, confidence, decision — mirroring Recent Activity) drills into each execution's full detail.

---

## Quick Start (local)

Run the two tiers in **two terminals**: the `api` (FastAPI, port 8000) and the `web`
(Next.js BFF, port 3000). The browser only ever opens the `web` app.

### 1. Backend — `api`

```bash
cd backend
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt

# CosmosDB
export COSMOSDB_ENDPOINT="https://your-account.documents.azure.com:443/"
export COSMOSDB_KEY="your-primary-key"

# LLM — Azure AI Foundry (default)
export AI_PROVIDER=azure
export AZURE_AI_PROJECT_ENDPOINT="https://your-project.services.ai.azure.com"
export AZURE_OPENAI_API_KEY="your-api-key"
export MODEL_DEPLOYMENT="gpt-5.1"

# …OR Google Gemini
# export AI_PROVIDER=gemini
# export GOOGLE_API_KEY="your-google-api-key"
# export MODEL_DEPLOYMENT="gemini-2.0-flash"

python run.py                       # FastAPI + in-process scheduler on :8000
# python run.py --web-only          # JSON API without the scheduler
# python run.py --scheduler-only    # scheduler only, no API server
```

### 2. Frontend — `web`

```bash
cd frontend
npm install
export API_BASE_URL="http://localhost:8000"   # where the BFF proxies data requests
npm run dev                                    # Next.js dev server on :3000
```

Open the app at **http://localhost:3000** (not 8000 — that's the internal API).

> **Docker (either tier):** `docker build -t oil-api ./backend` and
> `docker build -t oil-web ./frontend`, then run `oil-web` with `-e API_BASE_URL=...` pointing at
> the `oil-api` container.

→ [Full setup guide](docs/local-setup.md)

---

## Deployment (Azure Container Apps)

The app deploys as **two container apps** in the same Container Apps environment, sharing one
CosmosDB. CI (GitHub Actions) publishes both images to GHCR on every push:
`ghcr.io/<owner>/<repo>-api` and `ghcr.io/<owner>/<repo>-front`.

| App | Image | Ingress | Port | Auth |
|-----|-------|---------|------|------|
| **`api`** | `…/<repo>-api:latest` (from `backend/`) | **internal** | 8000 | none (private) |
| **`web`** | `…/<repo>-front:latest` (from `frontend/`) | **external** | 3000 | Container Apps ingress (Entra ID) |

The `web` app reaches the `api` over the environment's internal DNS via `API_BASE_URL`; the browser
never hits the `api` directly. Concise flow:

```bash
# api — INTERNAL ingress, takes all backend env vars (CosmosDB + LLM)
az containerapp create --name ca-oil-api --resource-group $RG --environment $ENV \
  --image ghcr.io/<owner>/<repo>-api:latest \
  --target-port 8000 --ingress internal --cpu 1 --memory 2Gi \
  --env-vars COSMOSDB_ENDPOINT=... COSMOSDB_KEY=... AI_PROVIDER=azure \
             MODEL_DEPLOYMENT=... AZURE_AI_PROJECT_ENDPOINT=... AZURE_OPENAI_API_KEY=...

# grab the api's internal FQDN
API_FQDN=$(az containerapp show --name ca-oil-api --resource-group $RG \
  --query "properties.configuration.ingress.fqdn" -o tsv)

# web — EXTERNAL ingress, only needs API_BASE_URL pointing at the api
az containerapp create --name ca-oil-web --resource-group $RG --environment $ENV \
  --image ghcr.io/<owner>/<repo>-front:latest \
  --target-port 3000 --ingress external --cpu 0.5 --memory 1Gi \
  --env-vars API_BASE_URL="https://$API_FQDN"
```

### Environment variables

Env vars are **per component**. The `api` takes the backend vars; the `web` takes only
`API_BASE_URL`.

**`api` (`backend/`):**

| Variable | Required when | Description |
|---|---|---|
| `COSMOSDB_ENDPOINT` | Always | CosmosDB account endpoint (`https://<account>.documents.azure.com:443/`) |
| `COSMOSDB_KEY` | Always | CosmosDB primary key |
| `AI_PROVIDER` | Optional | `azure` (default) or `gemini` |
| `MODEL_DEPLOYMENT` | Always | Default model for all agents (Azure deployment name or Gemini model ID) |
| `AZURE_AI_PROJECT_ENDPOINT` | `AI_PROVIDER=azure` | Azure AI Foundry project endpoint |
| `AZURE_OPENAI_API_KEY` | `AI_PROVIDER=azure` | Azure OpenAI API key |
| `GOOGLE_API_KEY` | `AI_PROVIDER=gemini` | Google AI Studio API key |
| `TELEGRAM_BOT_TOKEN` | Optional | Telegram bot token (notifications) |
| `TELEGRAM_CHAT_ID` | Optional | Telegram chat ID (notifications) |

**`web` (`frontend/`):**

| Variable | Required when | Description |
|---|---|---|
| `API_BASE_URL` | Always | Base URL of the internal `api` (e.g. `https://<api-app>.internal.<env>.<region>.azurecontainerapps.io`). Defaults to `http://localhost:8000` for local dev. |

→ [Full deployment walkthrough (CosmosDB provisioning, scheduler notes, GHCR auth)](docs/deployment.md)

---

## Acknowledgments

This project was built with [GitHub Copilot](https://github.com/features/copilot) and [Squad](https://github.com/bradygaster/squad) by [@bradygaster](https://github.com/bradygaster) — an AI team orchestration framework that runs inside Copilot CLI. Squad coordinated multiple specialized agents to develop, test, and iterate on this codebase.
