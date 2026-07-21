#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Provision Azure CosmosDB for Stock Options Manager
#
# Usage:
#   bash scripts/provision_cosmosdb.sh
#
# Prerequisites:
#   - Azure CLI installed and logged in (az login)
#   - Sufficient permissions to create resources in the target subscription
#
# What this script does:
#   1. Creates a resource group (if it doesn't exist)
#   2. Creates a CosmosDB account (serverless by default)
#   3. Creates the "stock-options-manager" database
#   4. Creates six containers: "symbols", "telemetry", "settings", "dgi_screener", "calendar", "agent_traces"
#   5. Applies custom indexing policy (index query fields, exclude large blobs)
#   6. Retrieves and prints the connection endpoint and primary key
#
# The script is idempotent — safe to re-run. Existing resources are not modified.
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Variables (customize these) ──────────────────────────────────────────────
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-stock-options-manager}"
LOCATION="${LOCATION:-eastus}"
COSMOSDB_ACCOUNT="${COSMOSDB_ACCOUNT:-cosmos-stock-options}"
DATABASE_NAME="${DATABASE_NAME:-stock-options-manager}"
CONTAINER_NAME="${CONTAINER_NAME:-symbols}"

echo "═══════════════════════════════════════════════════════════════"
echo "  CosmosDB Provisioning — Stock Options Manager"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "  Resource Group:   $RESOURCE_GROUP"
echo "  Location:         $LOCATION"
echo "  CosmosDB Account: $COSMOSDB_ACCOUNT"
echo "  Database:         $DATABASE_NAME"
echo "  Container:        $CONTAINER_NAME"
echo ""

# ── 1. Create Resource Group ─────────────────────────────────────────────────
echo "▶ Creating resource group '$RESOURCE_GROUP'..."
az group create \
  --name "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --only-show-errors \
  -o none
echo "  ✓ Resource group ready"

# ── 2. Create CosmosDB Account ───────────────────────────────────────────────
# Option A (default): Serverless — pay-per-request, best for dev/low-traffic
echo "▶ Creating CosmosDB account '$COSMOSDB_ACCOUNT' (serverless)..."
az cosmosdb create \
  --name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --kind GlobalDocumentDB \
  --capacity-mode Serverless \
  --default-consistency-level Session \
  --locations regionName="$LOCATION" failoverPriority=0 isZoneRedundant=false \
  --only-show-errors \
  -o none

# Option A-alt: Legacy serverless flag (uncomment if --capacity-mode is not supported)
# az cosmosdb create \
#   --name "$COSMOSDB_ACCOUNT" \
#   --resource-group "$RESOURCE_GROUP" \
#   --kind GlobalDocumentDB \
#   --capabilities EnableServerless \
#   --default-consistency-level Session \
#   --locations regionName="$LOCATION" failoverPriority=0 isZoneRedundant=false \
#   --only-show-errors \
#   -o none

# Option B: Provisioned throughput (uncomment below, comment out Option A above)
# echo "▶ Creating CosmosDB account '$COSMOSDB_ACCOUNT' (provisioned)..."
# az cosmosdb create \
#   --name "$COSMOSDB_ACCOUNT" \
#   --resource-group "$RESOURCE_GROUP" \
#   --kind GlobalDocumentDB \
#   --default-consistency-level Session \
#   --locations regionName="$LOCATION" failoverPriority=0 isZoneRedundant=false \
#   --enable-automatic-failover false \
#   --only-show-errors \
#   -o none

echo "  ✓ CosmosDB account ready"

# ── 3. Create Database ───────────────────────────────────────────────────────
echo "▶ Creating database '$DATABASE_NAME'..."
az cosmosdb sql database create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --name "$DATABASE_NAME" \
  --only-show-errors \
  -o none
echo "  ✓ Database ready"

# ── 4. Create Container ──────────────────────────────────────────────────────
echo "▶ Creating container '$CONTAINER_NAME' (partition key: /symbol)..."

# Serverless container (no throughput setting)
az cosmosdb sql container create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "$CONTAINER_NAME" \
  --partition-key-path "/symbol" \
  --partition-key-version 2 \
  --only-show-errors \
  -o none

# Provisioned container with autoscale (uncomment if using Option B above)
# az cosmosdb sql container create \
#   --account-name "$COSMOSDB_ACCOUNT" \
#   --resource-group "$RESOURCE_GROUP" \
#   --database-name "$DATABASE_NAME" \
#   --name "$CONTAINER_NAME" \
#   --partition-key-path "/symbol" \
#   --partition-key-version 2 \
#   --max-throughput 4000 \
#   --only-show-errors \
#   -o none

echo "  ✓ Container ready"

# ── 4b. Create Telemetry Container ───────────────────────────────────────────
TELEMETRY_CONTAINER="telemetry"
echo "▶ Creating container '$TELEMETRY_CONTAINER' (partition key: /metric_type)..."

# Serverless container
az cosmosdb sql container create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "$TELEMETRY_CONTAINER" \
  --partition-key-path "/metric_type" \
  --partition-key-version 2 \
  --only-show-errors \
  -o none

# Enable TTL (30 days = 2592000 seconds)
echo "▶ Enabling TTL on '$TELEMETRY_CONTAINER' (30 days)..."
az cosmosdb sql container update \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "$TELEMETRY_CONTAINER" \
  --ttl 2592000 \
  --only-show-errors \
  -o none

# Provisioned container with autoscale + TTL (uncomment if using Option B above)
# az cosmosdb sql container create \
#   --account-name "$COSMOSDB_ACCOUNT" \
#   --resource-group "$RESOURCE_GROUP" \
#   --database-name "$DATABASE_NAME" \
#   --name "$TELEMETRY_CONTAINER" \
#   --partition-key-path "/metric_type" \
#   --partition-key-version 2 \
#   --default-ttl -1 \
#   --max-throughput 4000 \
#   --only-show-errors \
#   -o none

echo "  ✓ Telemetry container ready"

# ── 4c. Create Settings Container ────────────────────────────────────────────
SETTINGS_CONTAINER="settings"
echo "▶ Creating container '$SETTINGS_CONTAINER' (partition key: /id)..."

# Serverless container
az cosmosdb sql container create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "$SETTINGS_CONTAINER" \
  --partition-key-path "/id" \
  --partition-key-version 2 \
  --only-show-errors \
  -o none

# Provisioned container with autoscale (uncomment if using Option B above)
# az cosmosdb sql container create \
#   --account-name "$COSMOSDB_ACCOUNT" \
#   --resource-group "$RESOURCE_GROUP" \
#   --database-name "$DATABASE_NAME" \
#   --name "$SETTINGS_CONTAINER" \
#   --partition-key-path "/id" \
#   --partition-key-version 2 \
#   --max-throughput 4000 \
#   --only-show-errors \
#   -o none

echo "  ✓ Settings container ready"

# ── 4d. Create DGI Screener Container ────────────────────────────────────────
DGI_CONTAINER="dgi_screener"
echo "▶ Creating container '$DGI_CONTAINER' (partition key: /symbol)..."

# Serverless container
az cosmosdb sql container create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "$DGI_CONTAINER" \
  --partition-key-path "/symbol" \
  --partition-key-version 2 \
  --only-show-errors \
  -o none

# Provisioned container with autoscale (uncomment if using Option B above)
# az cosmosdb sql container create \
#   --account-name "$COSMOSDB_ACCOUNT" \
#   --resource-group "$RESOURCE_GROUP" \
#   --database-name "$DATABASE_NAME" \
#   --name "$DGI_CONTAINER" \
#   --partition-key-path "/symbol" \
#   --partition-key-version 2 \
#   --max-throughput 4000 \
#   --only-show-errors \
#   -o none

echo "  ✓ DGI Screener container ready"

# ── 4e. Create Calendar Container ─────────────────────────────────────────────
CALENDAR_CONTAINER="calendar"
echo "▶ Creating container '$CALENDAR_CONTAINER' (partition key: /symbol)..."

az cosmosdb sql container create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "$CALENDAR_CONTAINER" \
  --partition-key-path "/symbol" \
  --partition-key-version 2 \
  --only-show-errors \
  -o none

echo "  ✓ Calendar container ready"

# ── 4f. Create Agent Traces Container ────────────────────────────────────────
TRACES_CONTAINER="agent_traces"
echo "▶ Creating container '$TRACES_CONTAINER' (partition key: /symbol)..."

az cosmosdb sql container create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "$TRACES_CONTAINER" \
  --partition-key-path "/symbol" \
  --partition-key-version 2 \
  --only-show-errors \
  -o none

# Enable TTL (90 days = 7776000 seconds) for automatic trace expiry
echo "▶ Enabling TTL on '$TRACES_CONTAINER' (90 days)..."
az cosmosdb sql container update \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "$TRACES_CONTAINER" \
  --ttl 7776000 \
  --only-show-errors \
  -o none

echo "  ✓ Agent Traces container ready"

# ── 5. Apply Custom Indexing Policy ──────────────────────────────────────────
echo "▶ Applying custom indexing policy..."
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
  --only-show-errors \
  -o none
echo "  ✓ Indexing policy applied"

# ── 6. Retrieve Connection Details ───────────────────────────────────────────
echo "▶ Retrieving connection details..."

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

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  ✅ Provisioning complete!"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "  Set these environment variables:"
echo ""
echo "    export COSMOSDB_ENDPOINT=\"$COSMOSDB_ENDPOINT\""
echo "    export COSMOSDB_KEY=\"$COSMOSDB_KEY\""
echo ""
echo "  Or add them to your .env file:"
echo ""
echo "    COSMOSDB_ENDPOINT=$COSMOSDB_ENDPOINT"
echo "    COSMOSDB_KEY=$COSMOSDB_KEY"
echo ""
