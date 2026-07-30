# Running Locally

[← Back to README](../README.md)

### Prerequisites

1. **Python 3.12+** (matches the Docker image)
2. **LLM provider** — choose one:
   - **Azure** (default): Azure AI Foundry project + API key (e.g. `gpt-5.1`, `gpt-5.4-mini`)
   - **Gemini**: [Google AI API key](https://aistudio.google.com/apikey) (e.g. `gemini-2.0-flash`, `gemini-2.5-pro`)
3. **Azure CosmosDB Account** — See [Azure CosmosDB Setup](#azure-cosmosdb-setup) below

### Setup

#### 1. Create Virtual Environment and Install Dependencies

```bash
cd backend
python -m venv venv
source venv/bin/activate 
pip install -r requirements.txt
```

This installs:
- `agent-framework-core` + `agent-framework-openai` - Microsoft Agent Framework core SDK (Azure OpenAI and OpenAI-compatible APIs such as Gemini)
- `yfinance` - Yahoo Finance data provider (overview, technicals, forecast, dividends, options chains)
- `py-vollib` - Black-Scholes Greeks computation for options chain data
- `pandas-ta` - Technical analysis indicators (RSI, MACD, moving averages, etc.)
- `requests` - HTTP client for stockanalysis.com dividend scraping
- `beautifulsoup4` - HTML parsing for stockanalysis.com dividend data
- `numpy`, `pandas` - Numerical computation and data manipulation for DGI scoring pipeline
- `pyyaml`, `croniter`, `python-dotenv` - Configuration and scheduling

#### 2. Configure Environment Variables

Create a `.env` file in the project root (loaded automatically on startup) or export variables in your shell.

**CosmosDB** (required for all setups):

```bash
export COSMOSDB_ENDPOINT="https://your-account.documents.azure.com:443/"
export COSMOSDB_KEY="your-primary-key"
```

**Azure** (when `AI_PROVIDER` is unset or `azure`):

```bash
export AI_PROVIDER=azure
export AZURE_AI_PROJECT_ENDPOINT="https://your-project.services.ai.azure.com"
export MODEL_DEPLOYMENT="gpt-5.1"          # default model for all agent roles
export AZURE_OPENAI_API_KEY="your-api-key-here"
```

**Gemini** (when `AI_PROVIDER=gemini`):

```bash
export AI_PROVIDER=gemini
export GOOGLE_API_KEY="your-google-api-key"
export MODEL_DEPLOYMENT="gemini-2.0-flash"  # default model for all agent roles
```

Market data needs no API key — yfinance fetches overview, technicals, forecast, dividends, and options chains from Yahoo Finance.

#### 3. (Optional) Set Up Telegram Notifications

Receive alerts directly on Telegram. Skip this section if you don't need notifications.

**Create a Telegram bot:**
1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts (choose a name, then a username)
3. Copy the bot token (format: `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)

**Get your chat ID:**
1. Add the bot to a group or start a direct message with it
2. Send any message to the bot
3. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` (replace `<TOKEN>` with your bot token)
4. Look for `chat.id` in the JSON response — copy the ID (group IDs are negative)

**Set environment variables:**
```bash
export TELEGRAM_BOT_TOKEN="123456:ABC-DEF..."
export TELEGRAM_CHAT_ID="-1001234567890"  # Use negative for groups
```

**Enable in config.yaml** (see step 5) or toggle on the Settings page. Use the **Test** button to verify connectivity.

#### 4. Set Up Azure CosmosDB

See the [Azure CosmosDB Setup](#azure-cosmosdb-setup) section below for provisioning instructions.

#### 5. Configure Symbols

Symbols and positions are managed via the **web dashboard** or the CosmosDB API. Each symbol has:
- **Watchlist flags**: `covered_call` and `cash_secured_put` (true/false)
- **Positions**: Open call/put positions with strike, expiration, and status

The exchange prefix is stored for reference (e.g., `NYSE` + `MO`).

#### 6. Adjust Configuration (Optional)

Edit `config.yaml` to customize. Model names and per-role overrides live under `ai` and apply to **both** Azure and Gemini.

#### AI provider (Azure or Gemini)

| Setting | Purpose |
|---|---|
| `ai.provider` | `azure` or `gemini` (from `${AI_PROVIDER}`; empty = `azure`) |
| `ai.model_deployment` | Default model for all agents (from `${MODEL_DEPLOYMENT}`) |
| `ai.models` | Optional per-role overrides (`chat`, `symbol_chat`, `supervisor`, `monitor_assessment`, etc.) — each falls back to `model_deployment` |

Provider-specific credentials only:

| Provider | Config section | Env vars |
|---|---|---|
| Azure | `azure` | `AZURE_AI_PROJECT_ENDPOINT`, `AZURE_OPENAI_API_KEY` |
| Gemini | `gemini` | `GOOGLE_API_KEY` |

Example `config.yaml` (Gemini):

```yaml
ai:
  provider: "${AI_PROVIDER}"       # azure | gemini
  model_deployment: "${MODEL_DEPLOYMENT}"
  models:
    chat: "gemini-2.0-flash"
    symbol_chat: "gemini-2.0-flash"
    supervisor: "gemini-2.0-flash"

azure:
  project_endpoint: "${AZURE_AI_PROJECT_ENDPOINT}"
  api_key: "${AZURE_OPENAI_API_KEY}"

gemini:
  api_key: "${GOOGLE_API_KEY}"

cosmosdb:
  endpoint: "${COSMOSDB_ENDPOINT}"
  key: "${COSMOSDB_KEY}"
  database: "stock-options-manager"

context:
  max_activity_entries: 2               # Recent activities injected per symbol (0=none, max 5). Each includes alert status.
  activity_ttl_days: 90                 # Auto-cleanup old activities

scheduler:
  cron: "0 9-16/2 * * 1-5"               # Cron expression (e.g. every 2h, Mon-Fri 9am-4pm)

telegram:
  enabled: false                        # Toggle on/off (also controllable from Settings UI)
  bot_token: "${TELEGRAM_BOT_TOKEN}"    # Bot token from @BotFather
  chat_id: "${TELEGRAM_CHAT_ID}"        # Target chat/group/channel ID
```

### Running

All commands run from the `backend/` directory.

#### Full app (web dashboard + scheduler)

```bash
python run.py
```

Opens the dashboard at http://localhost:8000 and starts the agent scheduler in a background thread. Press `Ctrl+C` to stop both.

#### Web dashboard only

```bash
python run.py --web-only
```

#### Scheduler only (no web UI)

```bash
python run.py --scheduler-only
```

#### Options

| Flag | Description |
|------|-------------|
| `--web-only` | Start only the web dashboard (no scheduler) |
| `--scheduler-only` | Start only the scheduler (no web) |
| `--port PORT` | Override the web server port (default: from `config.yaml` or 8000) |

The dashboard runs on `http://localhost:8000` by default (configurable in `config.yaml` under `web:`).

### Running with Docker

Build the image:

```bash
docker build -t option-income-lab .
```

Run with CosmosDB + your LLM provider credentials.

**Azure example:**

```bash
docker run -d --name option-income-lab \
  -p 8000:8000 \
  -e AI_PROVIDER=azure \
  -e AZURE_AI_PROJECT_ENDPOINT="https://your-project.services.ai.azure.com" \
  -e MODEL_DEPLOYMENT="gpt-5.1" \
  -e AZURE_OPENAI_API_KEY="your-api-key-here" \
  -e COSMOSDB_ENDPOINT="https://your-account.documents.azure.com:443/" \
  -e COSMOSDB_KEY="your-primary-key" \
  option-income-lab
```

**Gemini example:**

```bash
docker run -d --name option-income-lab \
  -p 8000:8000 \
  -e AI_PROVIDER=gemini \
  -e GOOGLE_API_KEY="your-google-api-key" \
  -e MODEL_DEPLOYMENT="gemini-2.0-flash" \
  -e COSMOSDB_ENDPOINT="https://your-account.documents.azure.com:443/" \
  -e COSMOSDB_KEY="your-primary-key" \
  option-income-lab
```

| Variable | Required when | Purpose |
|---|---|---|
| `COSMOSDB_ENDPOINT` | Always | CosmosDB account endpoint |
| `COSMOSDB_KEY` | Always | CosmosDB primary key |
| `AI_PROVIDER` | Optional | `azure` (default) or `gemini` |
| `MODEL_DEPLOYMENT` | Always | Default model name for all agent roles |
| `AZURE_AI_PROJECT_ENDPOINT` | Azure | Azure AI Foundry project endpoint |
| `AZURE_OPENAI_API_KEY` | Azure | Azure OpenAI API key |
| `GOOGLE_API_KEY` | Gemini | Google AI API key |

View logs:

```bash
docker logs -f option-income-lab
```

Pass flags (e.g. web-only mode):

```bash
docker run -d --name option-income-lab-web \
  -p 8000:8000 \
  -e AI_PROVIDER=azure \
  -e AZURE_AI_PROJECT_ENDPOINT="..." \
  -e MODEL_DEPLOYMENT="gpt-5.1" \
  -e AZURE_OPENAI_API_KEY="your-api-key-here" \
  -e COSMOSDB_ENDPOINT="..." \
  -e COSMOSDB_KEY="..." \
  option-income-lab --web-only
```

---