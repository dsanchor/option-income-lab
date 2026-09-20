# Deployment

[← Back to README](../README.md)

## Automated CI/CD (GitHub Actions → Azure Container Apps)

The workflow at `.github/workflows/docker-publish.yml` runs two jobs:

| Job | Trigger | What it does |
|-----|---------|--------------|
| `build-and-push` | Every push (all branches) + `workflow_dispatch` | Builds API & frontend images, pushes to GHCR |
| `deploy` | Push to `main` + `workflow_dispatch` on `main` | Deploys both Container Apps with the immutable `sha-<7char>` tag |

`deploy` runs only after **both** matrix legs of `build-and-push` succeed (`needs: build-and-push`).
Concurrency group `deploy-production` (with `cancel-in-progress: false`) ensures deploys are queued, never skipped out of order.

### Required GitHub Secrets

> The `deploy` job runs with `environment: production`. Secrets may be stored at **repository scope** (Settings → Secrets and variables → Actions → Secrets) or at **environment scope** (Settings → Environments → production → Environment secrets). Either scope works for OIDC; environment secrets take precedence if both exist. The GitHub Environment named **`production`** must exist before the first deploy.

| Secret | Description |
|--------|-------------|
| `AZURE_CLIENT_ID` | App registration Application (client) ID |
| `AZURE_TENANT_ID` | Microsoft Entra tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID |

> **Never** store `AZURE_CREDENTIALS` (JSON blob) or `AZURE_CLIENT_SECRET`. The workflow uses passwordless OIDC — no long-lived credential is required.

### Optional GitHub Variables

The resource group and app names are hardcoded in the workflow (single-environment deployment). No GitHub Variables are required. If you later need multi-environment support, extract the following to repository Variables:

| Name | Default |
|------|---------|
| `AZURE_RESOURCE_GROUP` | `stock-options-manager-rg` |
| `AZURE_API_APP` | `ca-stock-options-manager-api` |
| `AZURE_FRONT_APP` | `ca-stock-options-manager-front` |

### One-Time Azure OIDC Setup

Run these commands once from a terminal with **Owner** or **User Access Administrator** privileges on the resource group.

#### 1. Create the App Registration

```bash
az ad app create --display-name "github-actions-option-income-lab"

APP_ID=$(az ad app list \
  --display-name "github-actions-option-income-lab" \
  --query "[0].appId" -o tsv)

az ad sp create --id "$APP_ID"
```

`APP_ID` is the value to store as the `AZURE_CLIENT_ID` secret.

#### 2. Enable Immutable-ID OIDC Subject on the Repository

> **Why this is required.** By default, GitHub's OIDC token subject uses the human-readable slug (`repo:owner/repo:environment:...`). When immutable subjects are enabled the token instead uses the repository's numeric owner/repo IDs, producing the form observed at runtime: `repo:dsanchor@18459098/option-income-lab@1194091654:environment:production`. Azure's federated credential performs an **exact-match** on the subject, so enabling this setting before creating the credential guarantees the tokens GitHub emits always match the credential that Azure trusts — even if the repository is renamed or transferred.

```bash
gh api -X PUT repos/dsanchor/option-income-lab/actions/oidc/customization/sub \
  -F use_default=true \
  -F use_immutable_subject=true
```

> **Note:** Both fields are required. Omitting `use_default` causes HTTP 422 `object is missing required key: use_default`.

Run this once with a token that has the `repo` scope and Owner/Admin access. Verify the change with:

```bash
gh api repos/dsanchor/option-income-lab/actions/oidc/customization/sub
# → {"use_default": true, "use_immutable_subject": true}
```

#### 3. Add the Federated Credential (OIDC)

> **Prerequisite:** A GitHub Environment named **`production`** must exist in the repository before this credential will match. Create it at **Settings → Environments → New environment → `production`**.

```bash
az ad app federated-credential create --id "$APP_ID" --parameters '{
  "name": "github-actions-production-env",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:dsanchor@18459098/option-income-lab@1194091654:environment:production",
  "audiences": ["api://AzureADTokenExchange"],
  "description": "GitHub Actions OIDC for option-income-lab production environment"
}'
```

**Federated subject:** `repo:dsanchor@18459098/option-income-lab@1194091654:environment:production`
Only the `deploy` job, which runs with `environment: production`, can authenticate. Azure AD performs an exact-match on this subject — a branch-ref subject (`ref:refs/heads/main`) would not match and would be rejected.

> **Note:** GitHub generates the OIDC subject using the repository's immutable numeric ID, not the display name. If the repository is renamed or transferred, the owner-slug portion changes but the numeric IDs remain stable. The subject above is the immutable-ID form that is registered as the federated credential.

#### 4. Assign the Least-Privilege RBAC Role

```bash
SP_OBJECT_ID=$(az ad sp list \
  --filter "appId eq '$APP_ID'" \
  --query "[0].id" -o tsv)

az role assignment create \
  --assignee-object-id "$SP_OBJECT_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "Container Apps Contributor" \
  --scope "/subscriptions/<SUBSCRIPTION_ID>/resourceGroups/stock-options-manager-rg"
```

**Role:** `Container Apps Contributor` (not `Contributor`) — sufficient for `az containerapp update` and `az containerapp revision list`. This is the minimum required scope: resource-group level on `stock-options-manager-rg`.

#### 5. Set the GitHub Secrets

Secrets can be set at **repository scope** (accessible to all workflows) or scoped to the **`production` environment** (Settings → Environments → production → Environment secrets). Either scope works; environment secrets take precedence if both exist.

```bash
# Repository-level (recommended for simplicity)
gh secret set AZURE_CLIENT_ID     --body "<APP_ID from step 1>"
gh secret set AZURE_TENANT_ID     --body "<your-tenant-id>"
gh secret set AZURE_SUBSCRIPTION_ID --body "<your-subscription-id>"

# Alternatively, environment-scoped (Settings → Environments → production → Secrets)
gh secret set AZURE_CLIENT_ID     --env production --body "<APP_ID from step 1>"
gh secret set AZURE_TENANT_ID     --env production --body "<your-tenant-id>"
gh secret set AZURE_SUBSCRIPTION_ID --env production --body "<your-subscription-id>"
```

### GHCR Pull Credentials Caveat

The deploy workflow does **not** configure GHCR registry credentials on the Container Apps. It assumes both `ca-stock-options-manager-api` and `ca-stock-options-manager-front` were already created with a GHCR pull credential set (e.g., a GitHub PAT with `read:packages`).

If a Container App is recreated or the GHCR credential expires, `az containerapp update` will succeed but the new revision will fail to pull the image. In that case, reconfigure the registry credential:

```bash
az containerapp registry set \
  --name <APP_NAME> \
  --resource-group stock-options-manager-rg \
  --server ghcr.io \
  --username <GITHUB_USERNAME> \
  --password <GHCR_PAT>
```

Use a PAT with `read:packages` scope. After reconfiguring, re-run the workflow.

### CI/CD Amendments

> **Amendment — 2026-09-06 (commit `eac9ce7` follow-up):**
>
> **Readiness check semantics corrected.** Previous workflow polled `properties.runningState == "Running"` which never matches when Azure scales the app to its configured replica count — the real healthy response is `runningState: "RunningAtMaxScale"` (or `RunningAtMinScale`). The readiness check now polls the *exact* revision name captured from `az containerapp update` and declares success when `properties.healthState == "Healthy"` **and** `properties.active == true`, regardless of `runningState` variant. It fails fast on `healthState == "Unhealthy"` or `runningState` in `{Failed, Degraded, ProvisionFailed, ActivationFailed}`.
>
> **Azure logout made best-effort.** `az logout || true` prevents a secondary workflow failure when OIDC login itself fails.
>
> **OIDC subject corrected to immutable-ID form.** GitHub issues tokens using the repository's numeric owner/repo IDs, not the display-name slug. The registered federated credential subject is `repo:dsanchor@18459098/option-income-lab@1194091654:environment:production`; the name-form subject used in earlier documentation would have caused an OIDC token mismatch.

---

## Automatic User-Data Backup

Automatic user-data backups run in a **scheduled Azure Container Apps Job**,
separate from the API scheduler. The Job uses the same immutable backend image
deployed to the API. GitHub Actions only updates that image reference when the
optional Job exists; it is not the scheduler.

The Azure cron is configured by `backend/scripts/configure-backup.sh` and
triggers every 15 minutes in UTC. The backup process reads its effective
enabled flag, timezone, and local due time only from the Job environment
(`BACKUP_ENABLED`, `BACKUP_TIMEZONE`, and `BACKUP_LOCAL_TIME`) and applies DST
handling, same-day catch-up, locking, changed-content detection, and upload
rules.

### Provision Storage, Identity, RBAC, and Job

Prerequisites:

- Azure CLI logged into the intended subscription.
- An existing resource group and Container Apps environment.
- Permission to create Storage accounts, managed identities, Container Apps
  Jobs, and role assignments.
- The immutable backend image already published (`:sha-<commit>` or digest).
- The API Container App contains the Cosmos secret named `cosmosdb-key`, or
  `COSMOSDB_KEY` is set only in the invoking shell.
- For a private GHCR package when creating the Job, set `GHCR_USERNAME` and
  `GHCR_PAT` in the invoking shell. Do not put either value in source or output.

Preview the plan:

```bash
backend/scripts/configure-backup.sh --dry-run \
  --resource-group stock-options-manager-rg \
  --location westeurope \
  --storage-account <globally-unique-storage-name> \
  --container-app-environment <existing-environment> \
  --image ghcr.io/dsanchor/option-income-lab-api:sha-<commit>
```

Apply the same command without `--dry-run`. The script is idempotent and:

1. creates or verifies a general-purpose v2 Storage account;
2. enforces HTTPS, TLS 1.2, and disabled public Blob access;
3. enables Blob versioning and 14-day Blob/container soft delete;
4. creates the private `user-data-backups` container;
5. installs lifecycle rules for one-day staging, 35-day unanchored daily
   backups, 12-month anchor documents, 90-day run records, and 14-day old
   versions while preserving unrelated account rules;
6. creates a dedicated user-assigned managed identity;
7. grants `Storage Blob Data Contributor` at the container scope only; and
8. creates or updates the scheduled Job with one completion, parallelism 1,
   bounded retries, and a 30-minute timeout.

Monthly-anchor safety is cooperative: the backup service must tag any daily
Blob `retentionClass=daily` on upload and change it to
`retentionClass=monthly` while referenced by a live monthly anchor.
Lifecycle deletion applies only to daily-path objects tagged
`retentionClass=daily`, so it cannot delete an archive protected by an anchor.
The application retention reconciler expires anchors after 12 months and only
returns an archive to `daily` after its final live reference is gone. A
370-day lifecycle rule removes expired anchor documents as a fail-safe. Do not
replace this contract with an unconditional age-based archive delete rule.

The Job invokes the frozen application entrypoint inside the backend image:

```text
python scripts/run_automatic_backup.py scheduled
```

This is the container-relative form of
`python backend/scripts/run_automatic_backup.py scheduled`; no backup business
rules are implemented in provisioning scripts or workflow YAML.

### Runtime Configuration

The **Azure Container Apps Job environment is the single production runtime
source of truth**. `AutomaticBackupConfig.from_environment()` reads these
values; `backend/config.yaml` has no automatic-backup section. The provisioning
script sets both the Azure cron and all Job variables listed below. The API
Container App does not have Azure control-plane access and therefore does not
expose automatic-backup configuration or status. Application Settings contains
only the manual export and import workflows.

| Variable | Required | Purpose |
|---|---:|---|
| `AZURE_CLIENT_ID` | Yes | Client ID of the dedicated user-assigned managed identity |
| `AZURE_STORAGE_ACCOUNT_NAME` | Yes | GPv2 account used by `DefaultAzureCredential` |
| `BACKUP_ENABLED` | Yes | Enables the automatic backup gate; default `true` |
| `BACKUP_BLOB_CONTAINER` | Yes | Private container; default `user-data-backups` |
| `BACKUP_TIMEZONE` | Yes | IANA zone; default `Europe/Madrid` |
| `BACKUP_LOCAL_TIME` | Yes | Daily due time; default `00:15` |
| `BACKUP_SCHEDULE_NAME` | Yes | Idempotency namespace; default `daily-user-data` |
| `BACKUP_BUILD_COMMIT` | Recommended | Immutable image commit/digest identifier |
| `BACKUP_MAX_ARCHIVE_BYTES` | Yes | Maximum compressed archive bytes |
| `BACKUP_MAX_UNCOMPRESSED_BYTES` | Yes | Maximum total extracted bytes |
| `BACKUP_MAX_ENTRY_BYTES` | Yes | Maximum single ZIP entry bytes |
| `BACKUP_MAX_ZIP_FILES` | Yes | Maximum ZIP member count |
| `BACKUP_MAX_RECORDS` | Yes | Maximum archive record count |
| `BACKUP_MAX_JSON_DEPTH` | Yes | Maximum JSON nesting depth |
| `BACKUP_MAX_EXPANSION_RATIO` | Yes | Maximum compressed-to-expanded ratio |
| `COSMOSDB_ENDPOINT` | Yes | Existing Cosmos account endpoint |
| `COSMOSDB_KEY` | Yes, secret ref | Existing Container Apps secret; never plain output |

Blob authentication uses the Job's dedicated managed identity and
`DefaultAzureCredential`. The provisioning script assigns that identity to the
Job and sets `AZURE_CLIENT_ID` to its client ID; this makes
`ManagedIdentityCredential` select the intended UAMI even if other identities
are available. Do not configure a Storage account key, connection string, or
persistent SAS. Cosmos key authentication is temporary technical debt; moving
Cosmos data-plane access to managed identity is a follow-up.

To change the production trigger or effective backup settings, change the
approved values in `configure-backup.sh` and rerun it, or update the Azure Job
cron/environment directly. Editing application Settings, `config.yaml`, or
`.env.example` does not change production. `.env.example` is documentation for
local CLI execution only. Monitor the automatic backup through Container Apps
Job executions and the private Blob run records, health document, immutable
archives, and monthly anchors using Azure Portal or CLI.

### Verify and Operate

```bash
az containerapp job show \
  --name ca-stock-options-manager-backup \
  --resource-group stock-options-manager-rg \
  -o yaml

az containerapp job start \
  --name ca-stock-options-manager-backup \
  --resource-group stock-options-manager-rg

az containerapp job execution list \
  --name ca-stock-options-manager-backup \
  --resource-group stock-options-manager-rg \
  -o table

az containerapp job logs show \
  --name ca-stock-options-manager-backup \
  --resource-group stock-options-manager-rg \
  --follow
```

Expected scheduled outcomes include `UPLOADED` after a changed upload and
`NO_CHANGE` when authoritative content is unchanged. `NO_CHANGE` is healthy.
Alert on failed runs, no scheduled success for more than 26 hours, stale
leases, integrity/pointer failures, and repeated RBAC or network errors.

Local syntax/static tests verify provisioning and runtime wiring but cannot
replace an Azure acceptance run. With existing test resources and credentials,
start the Job and verify: the first run uploads and byte-verifies a baseline;
an unchanged retry records `NO_CHANGE` without another ZIP; a concurrent start
is lease-excluded; a verified orphan upload is adopted after simulated pointer
failure; and downloaded bytes pass archive hash, manifest, and checksum
validation. Never run these destructive/failure-injection checks against the
sole production backup container.

### Restore Safety

Downloading a Blob is not a restore. Select an immutable archive from the run
catalog/monthly anchors (not only `latest.json`), verify its archive hash,
manifest, and per-file checksums, then use the application import flow:
validate, run the mandatory zero-write dry-run, review dependency/conflict
reports, and explicitly apply create-only/skip-identical import. V1 never
updates or deletes existing user records.

### Troubleshooting

- **403 from Blob:** confirm the Job identity has `Storage Blob Data
  Contributor` on the exact container resource ID, `AZURE_CLIENT_ID` equals
  that UAMI's client ID, and allow for RBAC propagation.
- **Image pull failure:** configure the Job's GHCR credentials or make the
  package readable. The deploy workflow intentionally does not create secrets.
- **Cosmos authentication failure:** verify the Job has the configured
  `cosmosdb-key` secret and `COSMOSDB_KEY=secretref:cosmosdb-key`.
- **Job runs but no archive appears:** inspect the sanitized run status.
  `NO_CHANGE`, an already-completed local date, or a not-yet-due gate is
  expected and must not be treated as upload failure.
- **Storage networking failure:** when Storage public network access is later
  disabled, integrate the Container Apps environment with the VNet, configure
  a Blob private endpoint/private DNS, and rerun verification.
- **Stale/missing `latest.json`:** recover from verified immutable Blobs and run
  records; never treat the mutable pointer as the sole authority.

---

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

# Create portfolio container (partition key /account_id, ledger transactions)
az cosmosdb sql container create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "portfolio" \
  --partition-key-path "/account_id" \
  --partition-key-version 2 \
  -o none

# Create import_sessions container (partition key /session_id, per-document 7-day TTL)
az cosmosdb sql container create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "import_sessions" \
  --partition-key-path "/session_id" \
  --partition-key-version 2 \
  -o none

# Enable per-document TTL on import_sessions (-1 = sessions carry ttl: 604800 in the doc)
az cosmosdb sql container update \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "import_sessions" \
  --ttl -1 \
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