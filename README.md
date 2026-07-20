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
- 📡 **Momentum & Signal Filters** → SMA50/200 + RSI + ADX momentum analysis with actionable signal categories (Ideal Puts, Ideal Calls, Accumulate, No Puts, No Calls)
- 🛒 **Buy Tracker** → AI-powered DCA timing agent scoring 5 dimensions (value entry, trend, momentum, income, calendar)

The result: a DGI portfolio that generates income from **dividends AND option premiums** — with an AI copilot keeping watch while you sleep, and a full economics dashboard tracking your P&L.

---

## Architecture

Eight specialized AI agents power the platform:

- **Covered Call & Cash-Secured Put Agents** — Analyze entry opportunities with category-specific parameters (Aristocrat, Compounder, Rising Star, High Yield, Balanced)
- **Position Monitors** — Two-phase pipeline watches open positions for assignment risk, suggests rolls with full economics
- **Supervisor Agent** — Quality auditor reviewing every decision for data errors, blind spots, and unaddressed risks
- **Alpha Advisor** — Aggressive alternative finder identifying the single blocking parameter and offering bolder trades
- **Report Agent** — On-demand deep-dive analysis combining technicals, dividends, options chain, and risk assessment
- **DGI Screener** — Ranks S&P 500 stocks by composite quality score (70% fundamental + 30% technical timing)
- **Buy Tracker** — AI-powered DCA timing agent evaluating 5 dimensions for patient accumulation
- **Portfolio Enrichment** — Background process updating watchlist with DGI scores, momentum signals, and categories. Also records a **daily tech-timing + momentum snapshot** per symbol (rolling 90-day history), shown as a chart in the symbol detail modal — the line is the tech-timing score (0–100) and the background band color reflects the momentum of each period.

**Data source:** Yahoo Finance via `yfinance` Python library — zero auth, no browser, 23+ option expirations with computed Greeks.

**Storage:** Azure CosmosDB with symbol-centric partitioning across 5 containers (symbols, telemetry, settings, dgi_screener, calendar).

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

### 📅 Events Calendar
Monthly calendar view of earnings and ex-dividend dates for tracked symbols. Color-coded badges show position exposure (earnings with active position = orange, ex-div with call = red). Daily sync from Yahoo Finance.
→ [Events Calendar](docs/concepts.md#events-calendar)

### ⏸ Pause Watchlist Until Earnings
Suspend a symbol's following agents (Covered Call, Cash-Secured Put, Buy Tracker) until its next earnings date to save LLM tokens. Toggles show shadowed on the symbol detail and dashboard; auto-resumes after earnings via a daily reactivation job.
→ [Pause Watchlist Until Earnings](docs/concepts.md#pause-watchlist-until-earnings)

### 💬 Chat & Reports
- **Portfolio Chat** — Analyze tracked symbols with CosmosDB context (positions, activities, alerts)
- **Quick Analysis** — Fetch live Yahoo data for any symbol without saving to database
- **Per-Activity Chat** — Ephemeral advisory on specific decisions with historical vs live data separation
- **Symbol Reports** — On-demand deep-dive combining technicals, dividends, options chain, risk assessment

→ [Chat](docs/chat.md) | [Reports](docs/agents.md#symbol-report)

---

## Quick Start

```bash
# 1. Install dependencies
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 2. Set environment variables
export COSMOSDB_ENDPOINT="https://your-account.documents.azure.com:443/"
export COSMOSDB_KEY="your-primary-key"

# Azure OpenAI
export AI_PROVIDER=azure
export AZURE_AI_PROJECT_ENDPOINT="https://your-project.services.ai.azure.com"
export AZURE_OPENAI_API_KEY="your-api-key"
export MODEL_DEPLOYMENT="gpt-5.1"

# OR Google Gemini
export AI_PROVIDER=gemini
export GOOGLE_API_KEY="your-google-api-key"
export MODEL_DEPLOYMENT="gemini-2.0-flash"

# 3. Run
python run.py  # Full app (web + scheduler)
```

Access the dashboard at http://localhost:8000

→ [Full setup guide](docs/local-setup.md) | [Azure deployment](docs/deployment.md)

---

## Acknowledgments

This project was built with [GitHub Copilot](https://github.com/features/copilot) and [Squad](https://github.com/bradygaster/squad) by [@bradygaster](https://github.com/bradygaster) — an AI team orchestration framework that runs inside Copilot CLI. Squad coordinated multiple specialized agents to develop, test, and iterate on this codebase.
