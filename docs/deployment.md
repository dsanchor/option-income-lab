# Deployment

[← Back to README](../README.md)

## Azure Deployment

### Prerequisites

- [Azure CLI](https://docs.microsoft.com/en-us/cli/azure/install-azure-cli) installed and logged in (`az login`)
- LLM credentials configured (Azure AI Foundry **or** Google Gemini API key)
- Container image built (e.g., via GitHub Actions)

### 1. Set Variables

```bash
# ── Resource names ───────────────────────────────────────────────────────────
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-option-income-lab}"
LOCATION="${LOCATION:-eastus}"

# CosmosDB
COSMOSDB_ACCOUNT="${COSMOSDB_ACCOUNT:-cosmos-stock-options}"
DATABASE_NAME="${DATABASE_NAME:-stock-options-manager}"
CONTAINER_NAME="${CONTAINER_NAME:-symbols}"

# Container Apps
CONTAINER_ENV="${CONTAINER_ENV:-cae-option-income-lab}"
CONTAINER_APP="${CONTAINER_APP:-ca-option-income-lab}"
IMAGE="${IMAGE:-ghcr.io/dsanchor/option-income-lab:latest}"

# ── Credentials (fill these in) ─────────────────────────────────────────────
AI_PROVIDER="${AI_PROVIDER:-azure}"          # azure | gemini
MODEL_DEPLOYMENT="${MODEL_DEPLOYMENT:-gpt-5.1}"
AZURE_AI_PROJECT_ENDPOINT="${AZURE_AI_PROJECT_ENDPOINT:-your-project-endpoint}"
AZURE_OPENAI_API_KEY="${AZURE_OPENAI_API_KEY:-your-api-key-here}"
GOOGLE_API_KEY="${GOOGLE_API_KEY:-}"         # required when AI_PROVIDER=gemini
```

### 2. Create Resource Group

```bash
az group create \
  --name "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  -o none
```

### 3. Provision CosmosDB

Serverless is recommended — pay-per-request with no minimum cost.

```bash
# Create CosmosDB account (serverless)
az cosmosdb create \
  --name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --kind GlobalDocumentDB \
  --capacity-mode Serverless \
  --default-consistency-level Session \
  --locations regionName="$LOCATION" failoverPriority=0 isZoneRedundant=false \
  -o none

# Create database
az cosmosdb sql database create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --name "$DATABASE_NAME" \
  -o none

# Create container with partition key /symbol
az cosmosdb sql container create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "$CONTAINER_NAME" \
  --partition-key-path "/symbol" \
  --partition-key-version 2 \
  -o none

# Create telemetry container (partition key /metric_type, per-document TTL enabled)
az cosmosdb sql container create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "telemetry" \
  --partition-key-path "/metric_type" \
  --partition-key-version 2 \
  -o none

# Then update to enable TTL (30 days = 2592000 seconds)
az cosmosdb sql container update \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "telemetry" \
  --ttl 2592000 \
  -o none

# Create settings container (partition key /id, configuration persistence)
az cosmosdb sql container create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "settings" \
  --partition-key-path "/id" \
  --partition-key-version 2 \
  -o none

# Create dgi_screener container (partition key /symbol, DGI screening results)
az cosmosdb sql container create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "dgi_screener" \
  --partition-key-path "/symbol" \
  --partition-key-version 2 \
  -o none

# Create calendar container (partition key /symbol, earnings & ex-dividend dates)
az cosmosdb sql container create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "calendar" \
  --partition-key-path "/symbol" \
  --partition-key-version 2 \
  -o none

# Apply custom indexing policy
az cosmosdb sql container update \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "$CONTAINER_NAME" \
  --idx '{
    "indexingMode": "consistent",
    "automatic": true,
    "includedPaths": [
      {"path": "/symbol/?"},
      {"path": "/doc_type/?"},
      {"path": "/timestamp/?"},
      {"path": "/watchlist/covered_call/?"},
      {"path": "/watchlist/cash_secured_put/?"},
      {"path": "/agent_type/?"},
      {"path": "/activity/?"}
    ],
    "excludedPaths": [
      {"path": "/reason/*"},
      {"path": "/raw_response/*"},
      {"path": "/analysis_context/*"},
      {"path": "/*"}
    ]
  }' \
  -o none

# Retrieve endpoint and key
COSMOSDB_ENDPOINT=$(az cosmosdb show \
  --name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --query documentEndpoint \
  --output tsv)

COSMOSDB_KEY=$(az cosmosdb keys list \
  --name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --query primaryMasterKey \
  --output tsv)

echo "COSMOSDB_ENDPOINT=$COSMOSDB_ENDPOINT"
echo "COSMOSDB_KEY=$COSMOSDB_KEY"
```

> **Alternatively**, run `bash scripts/provision_cosmosdb.sh` which performs these same steps, or create the resources manually via the [Azure Portal](https://portal.azure.com) (CosmosDB → NoSQL → serverless capacity mode).

### 4. Deploy to Container Apps

```bash
# Create Container Apps environment
az containerapp env create \
  --name "$CONTAINER_ENV" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  -o none

# Deploy the container app
az containerapp create \
  --name "$CONTAINER_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --environment "$CONTAINER_ENV" \
  --image "$IMAGE" \
  --target-port 8000 \
  --ingress external \
  --min-replicas 1 \
  --max-replicas 1 \
  --cpu 1 \
  --memory 2Gi \
  --env-vars \
    AI_PROVIDER="$AI_PROVIDER" \
    MODEL_DEPLOYMENT="$MODEL_DEPLOYMENT" \
    AZURE_AI_PROJECT_ENDPOINT="$AZURE_AI_PROJECT_ENDPOINT" \
    AZURE_OPENAI_API_KEY="$AZURE_OPENAI_API_KEY" \
    GOOGLE_API_KEY="$GOOGLE_API_KEY" \
    COSMOSDB_ENDPOINT="$COSMOSDB_ENDPOINT" \
    COSMOSDB_KEY="$COSMOSDB_KEY" \
  -o none
```

> **Note:** If your GHCR package is private, add `--registry-username <github-username> --registry-password <github-pat>` with a PAT that has `read:packages` scope.

```bash
# Verify — get the app URL
APP_URL=$(az containerapp show \
  --name "$CONTAINER_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --query "properties.configuration.ingress.fqdn" -o tsv)

echo "Dashboard: https://$APP_URL"

# Check logs
az containerapp logs show \
  --name "$CONTAINER_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --follow
```

> **Security Tip:** Secure your Container App by configuring authentication with Entra ID or other identity providers. This ensures only authorized users can access your application. For setup instructions, see [Azure Container Apps authentication with Entra ID](https://learn.microsoft.com/en-us/azure/container-apps/authentication-entra).

### 5. Update Deployment

After pushing new code (triggers the GitHub Actions workflow to build a new image):

```bash
az containerapp update \
  --name "$CONTAINER_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --image "$IMAGE"
```

---


## Two-Container Deployment (`api` + `web`)

The app deploys as **two containers** in the same Container Apps environment, sharing the
same CosmosDB (see [Architecture → Deployment topology](architecture.md#project-structure)):

- **`api`** — image `ghcr.io/<owner>/<repo>-api:latest` (built from `backend/`). **Internal
  ingress only** (not reachable from the public internet), **no app-level auth**. Serves the
  JSON `/api/*` endpoints + runs the in-process scheduler.
- **`web`** — image `ghcr.io/<owner>/<repo>-front:latest` (built from `frontend/`). **External
  ingress** (this is the public entrypoint), auth delegated to Container Apps ingress. Acts as a
  BFF and proxies to `api` over the environment's internal DNS.

```bash
API_APP="${API_APP:-ca-option-income-lab-api}"
WEB_APP="${WEB_APP:-ca-option-income-lab-web}"
API_IMAGE="${API_IMAGE:-ghcr.io/dsanchor/stock-options-manager-api:latest}"
WEB_IMAGE="${WEB_IMAGE:-ghcr.io/dsanchor/stock-options-manager-front:latest}"

# 1. Deploy the api — INTERNAL ingress on port 8000 (no public exposure, no auth)
az containerapp create \
  --name "$API_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --environment "$CONTAINER_ENV" \
  --image "$API_IMAGE" \
  --target-port 8000 \
  --ingress internal \
  --min-replicas 1 --max-replicas 1 \
  --cpu 1 --memory 2Gi \
  --env-vars \
    AI_PROVIDER="$AI_PROVIDER" \
    MODEL_DEPLOYMENT="$MODEL_DEPLOYMENT" \
    AZURE_AI_PROJECT_ENDPOINT="$AZURE_AI_PROJECT_ENDPOINT" \
    AZURE_OPENAI_API_KEY="$AZURE_OPENAI_API_KEY" \
    GOOGLE_API_KEY="$GOOGLE_API_KEY" \
    COSMOSDB_ENDPOINT="$COSMOSDB_ENDPOINT" \
    COSMOSDB_KEY="$COSMOSDB_KEY" \
  -o none

# Grab the api's internal FQDN — the web app talks to it over internal DNS
API_FQDN=$(az containerapp show --name "$API_APP" --resource-group "$RESOURCE_GROUP" \
  --query "properties.configuration.ingress.fqdn" -o tsv)

# 2. Deploy the web — EXTERNAL ingress on port 3000, pointed at the api via API_BASE_URL
az containerapp create \
  --name "$WEB_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --environment "$CONTAINER_ENV" \
  --image "$WEB_IMAGE" \
  --target-port 3000 \
  --ingress external \
  --min-replicas 1 --max-replicas 1 \
  --cpu 0.5 --memory 1Gi \
  --env-vars \
    API_BASE_URL="https://$API_FQDN" \
  -o none

# Public URL of the web app
az containerapp show --name "$WEB_APP" --resource-group "$RESOURCE_GROUP" \
  --query "properties.configuration.ingress.fqdn" -o tsv
```

> **Note:** For private GHCR packages add `--registry-server ghcr.io --registry-username <user>
> --registry-password <pat>` (PAT with `read:packages`) to each `create`.

To update either component after a new image is pushed by CI:

```bash
az containerapp update --name "$API_APP" --resource-group "$RESOURCE_GROUP" --image "$API_IMAGE"
az containerapp update --name "$WEB_APP" --resource-group "$RESOURCE_GROUP" --image "$WEB_IMAGE"
```

## Scheduler

Both the `api` container and any additional instance you run include the **in-process
scheduler** (APScheduler). To avoid duplicate cron runs (double agent executions /
notifications), only **one** instance should run the scheduler at a time:

- Run the primary `api` normally (`python run.py`) — API + scheduler.
- Any extra API replica used purely to serve requests should start with `--web-only`
  (JSON API, no scheduler). Keep `--min-replicas`/`--max-replicas` at `1` on the
  scheduler-owning app so the cron never runs concurrently.

---


## Environment Variables

Env vars are **per component**. The `api` container takes the backend vars (CosmosDB, LLM,
Telegram, scheduler); the `web` container takes only `API_BASE_URL`.

**`api` (`backend/`):**

| Variable | Required when | Description |
|---|---|---|
| `COSMOSDB_ENDPOINT` | Always | CosmosDB account endpoint (e.g., `https://account.documents.azure.com:443/`) |
| `COSMOSDB_KEY` | Always | CosmosDB primary key |
| `AI_PROVIDER` | Optional | `azure` (default) or `gemini` |
| `MODEL_DEPLOYMENT` | Always | Default model for all agent roles (Azure deployment name or Gemini model ID) |
| `AZURE_AI_PROJECT_ENDPOINT` | Azure | Azure AI Foundry project endpoint |
| `AZURE_OPENAI_API_KEY` | Azure | Azure OpenAI API key |
| `GOOGLE_API_KEY` | Gemini | Google AI API key from [AI Studio](https://aistudio.google.com/apikey) |
| `TELEGRAM_BOT_TOKEN` | Optional | Telegram bot token (if notifications enabled) |
| `TELEGRAM_CHAT_ID` | Optional | Telegram chat ID (if notifications enabled) |

**`web` (`frontend/`):**

| Variable | Required when | Description |
|---|---|---|
| `API_BASE_URL` | Always | Base URL of the internal `api` (e.g., `https://<api-app>.internal.<env>.<region>.azurecontainerapps.io`). The Next.js server proxies browser requests here; the browser never calls `api` directly. Defaults to `http://localhost:8000` for local dev. |