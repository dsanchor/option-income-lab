# Web Dashboard

[← Back to README](../README.md)

- **Dashboard** (`/`) — Alerts overview by agent type with rolling time-range counts (today, last 7 days, last 30 days), scheduler status, recent activity feed with alert indicators and clickable links, position summary. Activities can be filtered by **confidence level** (high/medium/low) and **agent type** for granular views. WAIT activities with MODERATE or STRONG supervisor opinions display a 🤔 indicator icon (STRONG gets a pulse animation).
- **Alert Details** (`/alerts/{agent}/{symbol}`) — All alerts for a specific symbol, newest first, with activity badges and risk flags.
- **Alert + Activities** (`/alerts/{agent}/{symbol}/{index}`) — Full alert JSON and backing activities from the same time window.
- **Symbol Detail** (`/symbols/{symbol}`) — Full detail page for a symbol: expandable positions with source traceability, editable notes field, Close/Roll/Delete actions, activities, alerts, and "Open Position from Alert" / "Roll Position from Alert" buttons on activity detail. Features a **play button** (▶) for running individual symbol analysis on demand. **Generate Report** and **Chat** buttons are aligned right; watchlist toggles are aligned left. A **Pause until earnings** button suspends the following agents (Covered Call, Cash-Secured Put, Buy Tracker) until the symbol's next stored earnings date to save LLM tokens; while paused the three toggles render shadowed/disabled with a `⏸ Paused until earnings · <date>` badge and a **Resume now** button, and the symbol's rows appear shadowed on the main dashboard. Pauses auto-clear the day after earnings (query-level auto-expiry plus a daily reactivation job). Activities support confidence and agent-type filtering. WAIT activities with MODERATE or STRONG supervisor opinions display a 🤔 indicator icon. Activity detail includes collapsible "Supervisor" and "Alpha Advisor" panels with color-coded badges showing audit findings and aggressive alternatives.
- **Symbols Watchlist** (`/symbols`) — Central watchlist management page showing all tracked symbols with enrichment data. Columns: Symbol, Category, DGI Score, Tech Timing, Entry (technical timing tag), Momentum (directional signal), Price, Shares (inline editable), In Calls, Put Exposure ($committed if assigned). Features **signal filter pills** for actionable categories:
  - **All** — Show everything
  - **Ideal Puts** — (SB/Buy + Bullish/Neutral/Weakening) or (any + Oversold)
  - **Ideal Calls** — (Hold/Wait + Weakening/Bearish/Neutral) or (any + Overextended)
  - **Accumulate** — Accumulate + Bullish/Neutral (small DCA)
  - **⚠️ No Puts** — SB/Buy + Bearish pure (falling knife)
  - **⚠️ No Calls** — Wait + Bullish pure (runaway)
- **Symbol Report** (`/symbols/{symbol}/report`) — Dedicated report display page showing the latest generated report for a symbol (technical analysis, dividends, options chain, risk assessment, and recommendations).
- **Symbol Chat** (`/symbols/{symbol}/chat`) — Per-symbol chat page with a context selection screen before starting the conversation. Pre-loads market data via the yfinance provider for faster responses. Supports open call and open put analysis contexts.
- **Fetch Preview** (`/symbols/{symbol}/fetch-preview`) — Debug page showing raw market data for each resource (overview, technicals, forecast, options chain) with fetch timing and size.
- **Chat** (`/chat`) — Dual-mode chat experience powered by your configured LLM provider (Azure or Gemini):
  - **Portfolio Chat** — Analyze tracked symbols using CosmosDB data (watchlists, positions, recent activities). Click "Portfolio Chat" to ask questions about your tracked symbols.
  - **Quick Analysis** — Analyze any symbol (tracked or not) by fetching live Yahoo Finance data without saving to the database. Click "Quick Analysis", select a market (NASDAQ/NYSE/AMEX/OTC), and get instant analysis without committing to tracking.
  - Mode selector on the chat page lets you switch between modes at any time.
- **Settings** (`/settings`) — Scheduler config, Telegram notifications toggle & test button, Summarization Agent config (cron schedule & activity count), runtime stats (today/7d/30d telemetry), a Debug Data Fetch tool for testing data fetching per symbol, and an **Agent Chain Pipeline** debug view (`/api/debug/agent-chain/{symbol}`) for inspecting the full two-phase monitor pipeline per symbol. Each scheduled agent (Monitoring, Calendar Sync, Watchlist Reactivation, Options Chain, DGI Screener, Summary, Portfolio Enrichment) has a **Run Now** button for manual triggering. Settings are persisted to CosmosDB and survive application restarts and deployments. Changes made in the Settings UI are immediately available to all components (scheduler, telegram notifier, summarization agent, etc.) without requiring a restart.
- **DGI Screener** (`/dgi`) — Top 20 dividend growth stock candidates ranked by composite quality score. Color-coded category badges, per-row Quick Analysis (▶) and Add to Symbols (➕) actions. Configurable stock universe and filter thresholds via Settings.
- **Economics** (`/economics`) — P&L analytics dashboard for options trading performance. Features:
  - **Summary cards** — Total premium collected, total buyback costs, net income, weighted average RoC% (annualized), and win rate
  - **Filters** — Year, months (multi-select dropdown with checkboxes), symbols (multi-select dropdown with checkboxes), type (call/put), status (closed/rolled). Defaults to current year on load.
  - **Monthly breakdown table** — Net income, premium, buyback, RoC%, and win rate per month
  - **By-symbol breakdown table** — Same metrics grouped by underlying symbol
  - **Charts** — Monthly Net Income stacked bar chart (calls vs puts breakdown) and Calls vs Puts donut chart
  - **Positions detail table** — All matching positions with per-share and dollar amounts, individual RoC%, days held, and status
  - **Weighted RoC%** — Calculated as `total_net_income / sum(strike × 100)` to avoid misleading simple averages. Annualized using average days to expiration.
  - **Contract multiplier** — Premium and buyback are stored per-share; Economics displays total dollar amounts (×100 for standard options contracts)
- **Events Calendar** (`/symbols/calendar`) — Monthly calendar view showing earnings and ex-dividend dates for all tracked symbols. Color-coded by event type and position exposure (purple/orange for earnings, red/yellow for ex-dividend). Data cached in CosmosDB with configurable daily sync from Yahoo Finance. Includes a manual Refresh button for on-demand updates.

---