# Agents

[← Back to README](../README.md)

## Summarization Agent

An optional daily summary agent that sends a Telegram notification with a digest of your portfolio activities. Useful for staying informed without checking the dashboard daily.

### Features

- **Daily Summaries** — Automatically runs on a configurable schedule (default: 8 AM UTC)
- **Per-Symbol Activity Digest** — Summarizes the N most recent activities for each tracked symbol (configurable, default: 3)
- **Configurable Schedule** — Set the cron expression to match your timezone and preferences
- **Enable/Disable Toggle** — Turn on/off without restarting the application
- **Telegram Integration** — Requires Telegram notifications to be enabled; summaries are sent via Telegram

### Configuration

Configure the Summarization Agent in the **Settings** page (`/settings`):

1. **Enable/Disable** — Toggle the agent on/off
2. **Cron Expression** — Set the schedule (e.g., `0 8 * * *` for 8 AM daily)
3. **Activity Count** — Number of recent activities per symbol to include in the summary (1–5)
4. **Timezone** — Uses the container's system timezone (default: UTC)

Or configure in `config.yaml`:
```yaml
summary_agent:
  enabled: true
  cron: "0 8 * * *"        # 8 AM daily (UTC)
  activity_count: 3         # Latest N activities per symbol
```

### How It Works

1. The summarization agent runs on the configured schedule
2. It queries CosmosDB for all tracked symbols with recent activities
3. For each symbol, it retrieves the N most recent activities and any related alerts
4. The configured LLM provider (Azure OpenAI or Google Gemini) generates a concise summary of recent decisions and trends
5. A Telegram message is sent with the summary (if Telegram is enabled)
6. The message includes per-symbol activity digests and portfolio-wide insights

### Requirements

- **Telegram Notifications** must be enabled (see `/settings`)
- **LLM credentials** configured for your chosen provider (`azure` or `gemini` — see [AI provider](#ai-provider-azure-or-gemini))
- Valid **CosmosDB** connection

If Telegram is disabled, the summary is still generated but not sent.

## Symbol Report

The "Generate Report" button on each symbol's detail page triggers a comprehensive, on-demand analysis report via the **Report Agent** (`backend/src/report_agent.py`).

### What's Included

Each report covers:
- **Technical Analysis** — Current trend direction, price range, and key technical indicators
- **Earnings & Ex-Dividend** — Upcoming dates and their impact on options timing
- **Dividend Summary & Growth** — Yield, payment history, and growth trajectory
- **Options Chain** — Available calls then puts with strikes, premiums, and greeks
- **Open Position Risk Analysis** — Risk assessment for any active positions, including recent activity history
- **Monitoring Agent Recommendations** — Suggested actions based on current market conditions

### How It Works

1. User clicks "Generate Report" on the symbol detail page
2. The Report Agent uses the same `AgentRunner → Agent → OpenAIChatCompletionClient` pattern as other agents
3. Market data is loaded via the [Data Cache](#data-cache) for fast context assembly
4. The LLM generates a structured report from the system prompt (`backend/src/tv_report_instructions.py`)
5. The report is stored in CosmosDB as a `doc_type="report"` document and displayed on a dedicated page (`/symbols/{symbol}/report`)

## Action Plans

Action plans are user-created trading intentions tracked against live market data. Users create plans via the web UI (`/plans`) specifying a symbol, objective, conditions, and plan type. The "New Plan" form is **collapsed by default** and expandable on click, keeping the plans list clean.

### Plan Types

| Type | Description |
|------|-------------|
| `sell_put` | Plan to sell a cash-secured put |
| `sell_call` | Plan to sell a covered call |
| `buy_shares` | Plan to buy shares |
| `sell_shares` | Plan to sell shares |
| `roll` | Plan to roll an existing position |
| `close` | Plan to close a position |
| `other` | Custom plan type |

### Plan Monitor Agent

The Plan Monitor agent (`backend/src/plan_monitor_instructions.py`) evaluates active plans against current market data on a cron schedule (default: `0 4,16 * * 1-5` — twice daily on weekdays). It only analyzes plans with `"planned"` status.

**Input:** The plan (title, objective, conditions, type, status, notes), symbol enrichment data (price, momentum, entry tag, DGI score, technicals), active positions, and options chain data.

**Output:** A JSON object with:
- `note` — Brief analysis with specific data points
- `alert_level` — `none` / `info` / `action_recommended`
- `conditions_met` — Whether plan conditions match current data
- `recommended_status_change` — `null` or `"completed"`