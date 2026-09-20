#!/usr/bin/env bash
set -euo pipefail
set +x

readonly DEFAULT_CONTAINER="user-data-backups"
readonly DEFAULT_JOB_NAME="ca-stock-options-manager-backup"
readonly DEFAULT_IDENTITY="stock-options-manager-backup-mi"
readonly DEFAULT_SOURCE_APP="ca-stock-options-manager-api"
readonly DEFAULT_COSMOS_SECRET="cosmosdb-key"
readonly BLOB_TAG_ROLE_PREFIX="Option Income Lab Backup Blob Tags"
readonly BLOB_TAG_DATA_ACTION="Microsoft.Storage/storageAccounts/blobServices/containers/blobs/tags/write"
readonly BACKUP_CRON="15 23 * * *"
readonly BACKUP_TIMEZONE="Europe/Madrid"
readonly BACKUP_LOCAL_TIME="00:15"
readonly BACKUP_SCHEDULE_NAME="daily-user-data"
readonly DAILY_RETENTION_DAYS="35"
readonly MONTHLY_RETENTION_MONTHS="12"
readonly MONTHLY_ANCHOR_RETENTION_DAYS="370"
readonly RUN_RETENTION_DAYS="90"
readonly STAGING_RETENTION_DAYS="1"

RESOURCE_GROUP=""
LOCATION=""
STORAGE_ACCOUNT=""
IMAGE=""
SUBSCRIPTION=""
CONTAINER="$DEFAULT_CONTAINER"
JOB_NAME="$DEFAULT_JOB_NAME"
IDENTITY="$DEFAULT_IDENTITY"
CONTAINER_APP_ENVIRONMENT=""
COSMOS_ENDPOINT="${COSMOSDB_ENDPOINT:-}"
COSMOS_KEY_SECRET="$DEFAULT_COSMOS_SECRET"
SOURCE_CONTAINER_APP="$DEFAULT_SOURCE_APP"
DRY_RUN=false

usage() {
  cat <<'EOF'
Configure private Azure Blob storage and a scheduled Container Apps backup Job.
This script owns the Azure cron and the Job environment used at runtime.

Usage:
  backend/scripts/configure-backup.sh [options]

Required:
  --resource-group NAME              Existing Azure resource group
  --location REGION                 Azure region (for example, westeurope)
  --storage-account NAME            Globally unique GPv2 Storage account name
  --image IMAGE                     Immutable backend image (:sha-... or @sha256:...)

Optional:
  --subscription ID_OR_NAME         Azure subscription (defaults to current)
  --container NAME                  Blob container (default: user-data-backups)
  --job-name NAME                   Container Apps Job name
  --identity NAME                   User-assigned managed identity name
  --container-app-environment NAME  Existing Container Apps environment; when
                                    omitted, exactly one environment must exist
                                    in the resource group
  --cosmos-endpoint URL             Cosmos endpoint (or COSMOSDB_ENDPOINT)
  --cosmos-key-secret NAME          Job secret name (default: cosmosdb-key)
  --source-container-app NAME       Existing app from which the Cosmos secret
                                    may be copied (default: API app)
  --dry-run                         Authenticate, validate inputs, and print a
                                    non-secret plan without reading secrets or
                                    changing Azure resources
  -h, --help                        Show this help

Secret handling:
  For a new Job, set COSMOSDB_KEY in the invoking environment or ensure
  --source-container-app contains --cosmos-key-secret. The value is copied
  directly into the Job secret and is never printed. For a private GHCR image,
  set GHCR_USERNAME and GHCR_PAT when creating the Job.

Managed identity:
  The Job receives both the user-assigned identity resource and its client ID
  as AZURE_CLIENT_ID. DefaultAzureCredential uses that client ID to select the
  dedicated identity when multiple identities are available. Blob Data
  Contributor does not include Blob index-tag writes, so the same identity also
  receives a deployment-specific custom role containing only the Blob tags/write
  DataAction. Its AssignableScopes is the resource group (the narrowest scope
  Azure permits for a custom role); its assignment remains the exact container.
  The deploying principal needs Microsoft.Authorization/roleDefinitions/write
  and Microsoft.Authorization/roleAssignments/write.

Retention:
  Backup archives must be tagged retentionClass=daily when uploaded and changed
  to retentionClass=monthly while referenced by a live monthly anchor. Azure
  lifecycle deletes only retentionClass=daily archives after 35 days.

Configuration authority:
  Production enabled/timezone/local-time values come only from the Job
  environment set by this script. The application config.yaml and Settings UI
  do not configure automatic backups. Azure triggers once daily at 23:15 UTC,
  which is 00:15 Europe/Madrid in standard time and 01:15 during daylight-
  saving time. The local-date/due/idempotency gate is a safety guard, not a
  polling mechanism. Rerun this script after changing the constants above, or
  update the Job cron/environment directly.
EOF
}

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

require_value() {
  [[ $# -ge 2 && -n "$2" ]] || die "Missing value for $1"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --resource-group) require_value "$@"; RESOURCE_GROUP="$2"; shift 2 ;;
    --location) require_value "$@"; LOCATION="$2"; shift 2 ;;
    --storage-account) require_value "$@"; STORAGE_ACCOUNT="$2"; shift 2 ;;
    --image) require_value "$@"; IMAGE="$2"; shift 2 ;;
    --subscription) require_value "$@"; SUBSCRIPTION="$2"; shift 2 ;;
    --container) require_value "$@"; CONTAINER="$2"; shift 2 ;;
    --job-name) require_value "$@"; JOB_NAME="$2"; shift 2 ;;
    --identity) require_value "$@"; IDENTITY="$2"; shift 2 ;;
    --container-app-environment) require_value "$@"; CONTAINER_APP_ENVIRONMENT="$2"; shift 2 ;;
    --cosmos-endpoint) require_value "$@"; COSMOS_ENDPOINT="$2"; shift 2 ;;
    --cosmos-key-secret) require_value "$@"; COSMOS_KEY_SECRET="$2"; shift 2 ;;
    --source-container-app) require_value "$@"; SOURCE_CONTAINER_APP="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown argument: $1 (use --help)" ;;
  esac
done

for required in RESOURCE_GROUP LOCATION STORAGE_ACCOUNT IMAGE; do
  [[ -n "${!required}" ]] || die "--$(tr '[:upper:]_' '[:lower:]-' <<<"$required") is required"
done

command -v az >/dev/null 2>&1 || die "Azure CLI (az) is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required"
[[ "$STORAGE_ACCOUNT" =~ ^[a-z0-9]{3,24}$ ]] ||
  die "--storage-account must be 3-24 lowercase letters or digits"
[[ "$CONTAINER" =~ ^[a-z0-9]([a-z0-9-]{1,61}[a-z0-9])?$ ]] ||
  die "--container is not a valid Blob container name"
[[ "$JOB_NAME" =~ ^[a-z][a-z0-9-]{0,30}[a-z0-9]$ ]] ||
  die "--job-name must be lowercase, under 32 characters, and start with a letter"
[[ "$IMAGE" =~ @sha256:[0-9a-fA-F]{64}$ || "$IMAGE" =~ :sha-[0-9a-fA-F]{7,40}$ ]] ||
  die "--image must use an immutable sha-* tag or sha256 digest"

if [[ -n "$SUBSCRIPTION" ]]; then
  az account set --subscription "$SUBSCRIPTION" --only-show-errors
fi

ACCOUNT_ID="$(az account show --query id -o tsv --only-show-errors 2>/dev/null)" ||
  die "Azure CLI is not logged in; run 'az login'"
ACCOUNT_NAME="$(az account show --query name -o tsv --only-show-errors)"

if $DRY_RUN; then
  cat <<EOF
Dry run: no Azure resources will be changed.
Subscription: $ACCOUNT_NAME ($ACCOUNT_ID)
Resource group: $RESOURCE_GROUP
Location: $LOCATION
Storage account/container: $STORAGE_ACCOUNT/$CONTAINER
Managed identity: $IDENTITY
Container Apps environment: ${CONTAINER_APP_ENVIRONMENT:-<auto-discover-single-environment>}
Container Apps Job: $JOB_NAME
Image: $IMAGE
Schedule: $BACKUP_CRON UTC (00:15 Europe/Madrid standard time; 01:15 daylight-saving time)
Application gate: due/local-date/idempotency safety guard; not a polling mechanism
Retention: $DAILY_RETENTION_DAYS days daily; $MONTHLY_RETENTION_MONTHS monthly anchors
Authentication: dedicated UAMI selected through AZURE_CLIENT_ID
RBAC: Storage Blob Data Contributor plus an exact tag-write-only custom role;
  custom-role assignability is resource-group scoped, but access is assigned
  only at the exact Blob container. The deployer needs roleDefinitions/write
  and roleAssignments/write.

Planned idempotent operations:
  1. Verify the resource group and Container Apps environment.
  2. Create or harden a GPv2 Storage account (HTTPS/TLS 1.2, no public Blob access).
  3. Enable Blob versioning and 14-day Blob/container soft delete.
  4. Create the private '$CONTAINER' container and merge lifecycle retention.
  5. Create the dedicated user-assigned identity and container-scoped Blob RBAC:
     Storage Blob Data Contributor and an exact tag-write-only custom role.
     Existing custom-role definitions are verified and rejected if stale/broader.
  6. Create/update the scheduled Job, explicitly selecting that identity by client ID.
  7. Preserve the Cosmos key only as a Job secret reference; no secret value is printed.

Live Azure acceptance is separate and requires existing resources, credentials,
and an execution window. It must verify baseline upload, unchanged NO_CHANGE,
lease exclusion, pointer adoption, and downloaded archive integrity.
EOF
  exit 0
fi

az group show --name "$RESOURCE_GROUP" --only-show-errors -o none ||
  die "Resource group '$RESOURCE_GROUP' does not exist"

if [[ -z "$CONTAINER_APP_ENVIRONMENT" ]]; then
  mapfile -t ENVIRONMENTS < <(
    az containerapp env list \
      --resource-group "$RESOURCE_GROUP" \
      --query "[].name" -o tsv --only-show-errors
  )
  [[ ${#ENVIRONMENTS[@]} -eq 1 ]] ||
    die "Specify --container-app-environment (found ${#ENVIRONMENTS[@]} environments)"
  CONTAINER_APP_ENVIRONMENT="${ENVIRONMENTS[0]}"
fi
az containerapp env show \
  --name "$CONTAINER_APP_ENVIRONMENT" \
  --resource-group "$RESOURCE_GROUP" \
  --only-show-errors -o none ||
  die "Container Apps environment '$CONTAINER_APP_ENVIRONMENT' does not exist"

if az storage account show \
  --name "$STORAGE_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --only-show-errors -o none 2>/dev/null; then
  STORAGE_KIND="$(az storage account show \
    --name "$STORAGE_ACCOUNT" \
    --resource-group "$RESOURCE_GROUP" \
    --query kind -o tsv --only-show-errors)"
  [[ "$STORAGE_KIND" == "StorageV2" ]] ||
    die "Existing storage account '$STORAGE_ACCOUNT' is '$STORAGE_KIND', not GPv2"
  az storage account update \
    --name "$STORAGE_ACCOUNT" \
    --resource-group "$RESOURCE_GROUP" \
    --https-only true \
    --allow-blob-public-access false \
    --min-tls-version TLS1_2 \
    --only-show-errors -o none
else
  az storage account create \
    --name "$STORAGE_ACCOUNT" \
    --resource-group "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --kind StorageV2 \
    --sku Standard_LRS \
    --https-only true \
    --allow-blob-public-access false \
    --min-tls-version TLS1_2 \
    --public-network-access Enabled \
    --only-show-errors -o none
fi

az storage account blob-service-properties update \
  --account-name "$STORAGE_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --enable-versioning true \
  --enable-delete-retention true \
  --delete-retention-days 14 \
  --enable-container-delete-retention true \
  --container-delete-retention-days 14 \
  --only-show-errors -o none

az storage container-rm create \
  --storage-account "$STORAGE_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINER" \
  --public-access off \
  --only-show-errors -o none

LIFECYCLE_POLICY="$(cat <<EOF
{"rules":[
  {"enabled":true,"name":"backup-staging-1-day","type":"Lifecycle","definition":{"actions":{"baseBlob":{"delete":{"daysAfterModificationGreaterThan":$STAGING_RETENTION_DAYS}}},"filters":{"blobTypes":["blockBlob"],"prefixMatch":["$CONTAINER/v1/staging/"]}}},
  {"enabled":true,"name":"backup-daily-35-days","type":"Lifecycle","definition":{"actions":{"baseBlob":{"delete":{"daysAfterModificationGreaterThan":$DAILY_RETENTION_DAYS}}},"filters":{"blobIndexMatch":[{"name":"retentionClass","op":"==","value":"daily"}],"blobTypes":["blockBlob"],"prefixMatch":["$CONTAINER/v1/daily/"]}}},
  {"enabled":true,"name":"backup-monthly-anchors-12-months","type":"Lifecycle","definition":{"actions":{"baseBlob":{"delete":{"daysAfterModificationGreaterThan":$MONTHLY_ANCHOR_RETENTION_DAYS}}},"filters":{"blobTypes":["blockBlob"],"prefixMatch":["$CONTAINER/v1/monthly/"]}}},
  {"enabled":true,"name":"backup-run-records-90-days","type":"Lifecycle","definition":{"actions":{"baseBlob":{"delete":{"daysAfterModificationGreaterThan":$RUN_RETENTION_DAYS}}},"filters":{"blobTypes":["blockBlob"],"prefixMatch":["$CONTAINER/v1/runs/"]}}},
  {"enabled":true,"name":"backup-old-versions-14-days","type":"Lifecycle","definition":{"actions":{"version":{"delete":{"daysAfterCreationGreaterThan":14}}},"filters":{"blobTypes":["blockBlob"],"prefixMatch":["$CONTAINER/v1/"]}}}
]}
EOF
)"
EXISTING_POLICY="$(az storage account management-policy show \
  --account-name "$STORAGE_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --query policy -o json --only-show-errors 2>/dev/null || printf '{"rules":[]}')"
LIFECYCLE_POLICY="$(python3 - "$EXISTING_POLICY" "$LIFECYCLE_POLICY" <<'PY'
import json
import sys

existing = json.loads(sys.argv[1] or '{"rules":[]}')
desired = json.loads(sys.argv[2])
managed_names = {rule["name"] for rule in desired["rules"]}
preserved = [
    rule for rule in (existing.get("rules") or [])
    if rule.get("name") not in managed_names
]
print(json.dumps({"rules": preserved + desired["rules"]}, separators=(",", ":")))
PY
)"
az storage account management-policy create \
  --account-name "$STORAGE_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --policy "$LIFECYCLE_POLICY" \
  --only-show-errors -o none

if ! az identity show \
  --name "$IDENTITY" \
  --resource-group "$RESOURCE_GROUP" \
  --only-show-errors -o none 2>/dev/null; then
  az identity create \
    --name "$IDENTITY" \
    --resource-group "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --only-show-errors -o none
fi
IDENTITY_ID="$(az identity show --name "$IDENTITY" --resource-group "$RESOURCE_GROUP" --query id -o tsv --only-show-errors)"
IDENTITY_PRINCIPAL_ID="$(az identity show --name "$IDENTITY" --resource-group "$RESOURCE_GROUP" --query principalId -o tsv --only-show-errors)"
IDENTITY_CLIENT_ID="$(az identity show --name "$IDENTITY" --resource-group "$RESOURCE_GROUP" --query clientId -o tsv --only-show-errors)"
[[ -n "$IDENTITY_ID" && -n "$IDENTITY_PRINCIPAL_ID" && -n "$IDENTITY_CLIENT_ID" ]] ||
  die "Managed identity '$IDENTITY' is missing a resource, principal, or client ID"

STORAGE_ID="$(az storage account show \
  --name "$STORAGE_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --query id -o tsv --only-show-errors)"
CONTAINER_SCOPE="${STORAGE_ID}/blobServices/default/containers/${CONTAINER}"
RESOURCE_GROUP_ID="$(az group show \
  --name "$RESOURCE_GROUP" \
  --query id -o tsv --only-show-errors)"
TAG_ROLE_DEFINITION_ID="$(python3 - "$ACCOUNT_ID" "$RESOURCE_GROUP" "$STORAGE_ACCOUNT" "$CONTAINER" <<'PY'
import sys
import uuid

seed = "option-income-lab:backup-blob-tag-role:" + "|".join(
    value.strip().lower() for value in sys.argv[1:]
)
print(uuid.uuid5(uuid.NAMESPACE_URL, seed))
PY
)"
BLOB_TAG_ROLE_NAME="${BLOB_TAG_ROLE_PREFIX} ${TAG_ROLE_DEFINITION_ID:0:12}"

if ! az role assignment list \
  --assignee-object-id "$IDENTITY_PRINCIPAL_ID" \
  --scope "$CONTAINER_SCOPE" \
  --query "[?roleDefinitionName=='Storage Blob Data Contributor'] | [0].id" \
  -o tsv --only-show-errors | grep -q .; then
  az role assignment create \
    --assignee-object-id "$IDENTITY_PRINCIPAL_ID" \
    --assignee-principal-type ServicePrincipal \
    --role "Storage Blob Data Contributor" \
    --scope "$CONTAINER_SCOPE" \
    --only-show-errors -o none
fi

ROLE_BY_ID="$(az role definition list \
  --name "$TAG_ROLE_DEFINITION_ID" \
  --custom-role-only true \
  -o json --only-show-errors)"
ROLE_BY_NAME="$(az role definition list \
  --name "$BLOB_TAG_ROLE_NAME" \
  --custom-role-only true \
  -o json --only-show-errors)"

if [[ "$(python3 - "$ROLE_BY_ID" <<'PY'
import json
import sys
print(len(json.loads(sys.argv[1] or "[]")))
PY
)" == "0" ]]; then
  if [[ "$(python3 - "$ROLE_BY_NAME" <<'PY'
import json
import sys
print(len(json.loads(sys.argv[1] or "[]")))
PY
)" != "0" ]]; then
    die "Custom role name '$BLOB_TAG_ROLE_NAME' already belongs to another role ID. Remove or rename that conflicting administrator-managed role, then rerun."
  fi
  TAG_ROLE_DEFINITION="$(cat <<EOF
{
  "Name": "$BLOB_TAG_ROLE_NAME",
  "Id": "$TAG_ROLE_DEFINITION_ID",
  "IsCustom": true,
  "Description": "Write Blob index tags for Option Income Lab backup retention.",
  "Actions": [],
  "NotActions": [],
  "DataActions": [
    "$BLOB_TAG_DATA_ACTION"
  ],
  "NotDataActions": [],
  "AssignableScopes": ["$RESOURCE_GROUP_ID"]
}
EOF
)"
  az role definition create \
    --role-definition "$TAG_ROLE_DEFINITION" \
    --only-show-errors -o none
fi

ROLE_BY_ID="$(az role definition list \
  --name "$TAG_ROLE_DEFINITION_ID" \
  --custom-role-only true \
  -o json --only-show-errors)"
if ! python3 - "$ROLE_BY_ID" "$TAG_ROLE_DEFINITION_ID" "$BLOB_TAG_ROLE_NAME" "$RESOURCE_GROUP_ID" "$BLOB_TAG_DATA_ACTION" <<'PY'
import json
import sys

definitions = json.loads(sys.argv[1] or "[]")
expected_id, expected_name, expected_scope, expected_action = sys.argv[2:]

def norm_id(value):
    return str(value or "").rstrip("/").rsplit("/", 1)[-1].lower()

def norm_scope(value):
    return str(value or "").rstrip("/").lower()

errors = []
if len(definitions) != 1:
    errors.append(f"expected one definition by deterministic ID, found {len(definitions)}")
else:
    role = definitions[0]
    if norm_id(role.get("name") or role.get("id")) != expected_id.lower():
        errors.append("definition ID differs")
    if role.get("roleName") != expected_name:
        errors.append("role name differs")
    if role.get("roleType") != "CustomRole":
        errors.append("role type is not CustomRole")
    permissions = role.get("permissions") or []
    if len(permissions) != 1:
        errors.append(f"expected exactly one permission block, found {len(permissions)}")
    else:
        permission = permissions[0]
        expected = {
            "actions": [],
            "notActions": [],
            "dataActions": [expected_action],
            "notDataActions": [],
        }
        for key, values in expected.items():
            actual = sorted(set(permission.get(key) or []))
            if actual != sorted(values):
                errors.append(f"{key}={actual!r}, expected {sorted(values)!r}")
    scopes = sorted({norm_scope(scope) for scope in role.get("assignableScopes") or []})
    if scopes != [norm_scope(expected_scope)]:
        errors.append(f"assignableScopes={scopes!r}, expected {[norm_scope(expected_scope)]!r}")

if errors:
    print("; ".join(errors), file=sys.stderr)
    raise SystemExit(1)
PY
then
  die "Custom role '$BLOB_TAG_ROLE_NAME' ($TAG_ROLE_DEFINITION_ID) is stale or broader than required. It was not changed or assigned. An Azure RBAC administrator must remove the conflicting definition (after reviewing its assignments) so this script can recreate the exact tag-write-only role."
fi

TAG_ASSIGNMENTS="$(az role assignment list \
  --assignee-object-id "$IDENTITY_PRINCIPAL_ID" \
  --scope "$CONTAINER_SCOPE" \
  --include-inherited \
  -o json --only-show-errors)"
TAG_ASSIGNMENT_ID="$(python3 - "$TAG_ASSIGNMENTS" "$TAG_ROLE_DEFINITION_ID" "$CONTAINER_SCOPE" "$IDENTITY_PRINCIPAL_ID" <<'PY'
import json
import sys

assignments = json.loads(sys.argv[1] or "[]")
expected_role = sys.argv[2].lower()
expected_scope = sys.argv[3].rstrip("/").lower()
expected_principal = sys.argv[4].lower()
matches = [
    item for item in assignments
    if str(item.get("roleDefinitionId") or "").rstrip("/").rsplit("/", 1)[-1].lower() == expected_role
    and str(item.get("scope") or "").rstrip("/").lower() == expected_scope
    and str(item.get("principalId") or "").lower() == expected_principal
]
print(matches[0].get("id", "") if len(matches) == 1 else "")
PY
)"
if [[ -z "$TAG_ASSIGNMENT_ID" ]]; then
  az role assignment create \
    --assignee-object-id "$IDENTITY_PRINCIPAL_ID" \
    --assignee-principal-type ServicePrincipal \
    --role "$TAG_ROLE_DEFINITION_ID" \
    --scope "$CONTAINER_SCOPE" \
    --only-show-errors -o none
fi

TAG_ASSIGNMENTS="$(az role assignment list \
  --assignee-object-id "$IDENTITY_PRINCIPAL_ID" \
  --scope "$CONTAINER_SCOPE" \
  --include-inherited \
  -o json --only-show-errors)"
if ! python3 - "$TAG_ASSIGNMENTS" "$TAG_ROLE_DEFINITION_ID" "$CONTAINER_SCOPE" "$IDENTITY_PRINCIPAL_ID" <<'PY'
import json
import sys

assignments = json.loads(sys.argv[1] or "[]")
expected_role = sys.argv[2].lower()
expected_scope = sys.argv[3].rstrip("/").lower()
expected_principal = sys.argv[4].lower()
matches = [
    item for item in assignments
    if str(item.get("roleDefinitionId") or "").rstrip("/").rsplit("/", 1)[-1].lower() == expected_role
    and str(item.get("scope") or "").rstrip("/").lower() == expected_scope
    and str(item.get("principalId") or "").lower() == expected_principal
]
if len(matches) != 1:
    print(f"expected one exact assignment, found {len(matches)}", file=sys.stderr)
    raise SystemExit(1)
PY
then
  die "Tag-write role assignment verification failed: expected role definition $TAG_ROLE_DEFINITION_ID assigned exactly at $CONTAINER_SCOPE"
fi

[[ -n "$COSMOS_ENDPOINT" ]] ||
  die "Provide --cosmos-endpoint or set COSMOSDB_ENDPOINT"

JOB_EXISTS=false
if az containerapp job show \
  --name "$JOB_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --only-show-errors -o none 2>/dev/null; then
  JOB_EXISTS=true
fi

COSMOS_KEY_VALUE="${COSMOSDB_KEY:-}"
if [[ -z "$COSMOS_KEY_VALUE" ]] &&
   az containerapp show \
     --name "$SOURCE_CONTAINER_APP" \
     --resource-group "$RESOURCE_GROUP" \
     --only-show-errors -o none 2>/dev/null; then
  COSMOS_KEY_VALUE="$(az containerapp secret show \
    --name "$SOURCE_CONTAINER_APP" \
    --resource-group "$RESOURCE_GROUP" \
    --secret-name "$COSMOS_KEY_SECRET" \
    --query value -o tsv --only-show-errors 2>/dev/null || true)"
fi

BUILD_COMMIT="${IMAGE##*:sha-}"
if [[ "$BUILD_COMMIT" == "$IMAGE" ]]; then
  BUILD_COMMIT="${IMAGE##*@sha256:}"
fi

JOB_ENV=(
  "AZURE_CLIENT_ID=$IDENTITY_CLIENT_ID"
  "AZURE_STORAGE_ACCOUNT_NAME=$STORAGE_ACCOUNT"
  "BACKUP_ENABLED=true"
  "BACKUP_BLOB_CONTAINER=$CONTAINER"
  "BACKUP_TIMEZONE=$BACKUP_TIMEZONE"
  "BACKUP_LOCAL_TIME=$BACKUP_LOCAL_TIME"
  "BACKUP_SCHEDULE_NAME=$BACKUP_SCHEDULE_NAME"
  "BACKUP_BUILD_COMMIT=$BUILD_COMMIT"
  "BACKUP_MAX_ARCHIVE_BYTES=${BACKUP_MAX_ARCHIVE_BYTES:-52428800}"
  "BACKUP_MAX_UNCOMPRESSED_BYTES=${BACKUP_MAX_UNCOMPRESSED_BYTES:-209715200}"
  "BACKUP_MAX_ENTRY_BYTES=${BACKUP_MAX_ENTRY_BYTES:-52428800}"
  "BACKUP_MAX_ZIP_FILES=${BACKUP_MAX_ZIP_FILES:-9}"
  "BACKUP_MAX_RECORDS=${BACKUP_MAX_RECORDS:-200000}"
  "BACKUP_MAX_JSON_DEPTH=${BACKUP_MAX_JSON_DEPTH:-30}"
  "BACKUP_MAX_EXPANSION_RATIO=${BACKUP_MAX_EXPANSION_RATIO:-100}"
  "COSMOSDB_ENDPOINT=$COSMOS_ENDPOINT"
  "COSMOSDB_KEY=secretref:$COSMOS_KEY_SECRET"
)

if ! $JOB_EXISTS; then
  [[ -n "$COSMOS_KEY_VALUE" ]] ||
    die "New Job requires COSMOSDB_KEY or '$COSMOS_KEY_SECRET' on source app '$SOURCE_CONTAINER_APP'"
  CREATE_ARGS=(
    containerapp job create
    --name "$JOB_NAME"
    --resource-group "$RESOURCE_GROUP"
    --environment "$CONTAINER_APP_ENVIRONMENT"
    --trigger-type Schedule
    --cron-expression "$BACKUP_CRON"
    --parallelism 1
    --replica-completion-count 1
    --replica-retry-limit 2
    --replica-timeout 1800
    --image "$IMAGE"
    --container-name backup
    --cpu 0.5
    --memory 1Gi
    --mi-user-assigned "$IDENTITY_ID"
    --command python
    --args scripts/run_automatic_backup.py scheduled
    --secrets "$COSMOS_KEY_SECRET=$COSMOS_KEY_VALUE"
    --env-vars "${JOB_ENV[@]}"
    --only-show-errors
    -o none
  )
  if [[ -n "${GHCR_USERNAME:-}" && -n "${GHCR_PAT:-}" ]]; then
    CREATE_ARGS+=(
      --registry-server ghcr.io
      --registry-username "$GHCR_USERNAME"
      --registry-password "$GHCR_PAT"
    )
  fi
  az "${CREATE_ARGS[@]}"
else
  az containerapp job identity assign \
    --name "$JOB_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --user-assigned "$IDENTITY_ID" \
    --only-show-errors -o none
  if [[ -n "$COSMOS_KEY_VALUE" ]]; then
    az containerapp job secret set \
      --name "$JOB_NAME" \
      --resource-group "$RESOURCE_GROUP" \
      --secrets "$COSMOS_KEY_SECRET=$COSMOS_KEY_VALUE" \
      --only-show-errors -o none
  elif ! az containerapp job secret list \
    --name "$JOB_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query "[?name=='$COSMOS_KEY_SECRET'] | [0].name" \
    -o tsv --only-show-errors | grep -q .; then
    die "Job has no '$COSMOS_KEY_SECRET' secret; set COSMOSDB_KEY and rerun"
  fi
  az containerapp job update \
    --name "$JOB_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --image "$IMAGE" \
    --cron-expression "$BACKUP_CRON" \
    --parallelism 1 \
    --replica-completion-count 1 \
    --replica-retry-limit 2 \
    --replica-timeout 1800 \
    --container-name backup \
    --cpu 0.5 \
    --memory 1Gi \
    --command python \
    --args scripts/run_automatic_backup.py scheduled \
    --set-env-vars "${JOB_ENV[@]}" \
    --only-show-errors -o none
fi

JOB_ID="$(az containerapp job show \
  --name "$JOB_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query id -o tsv --only-show-errors)"

cat <<EOF
Backup infrastructure configured.
Subscription: $ACCOUNT_NAME ($ACCOUNT_ID)
Resource group: $RESOURCE_GROUP
Storage account: $STORAGE_ACCOUNT ($STORAGE_ID)
Blob container: $CONTAINER ($CONTAINER_SCOPE)
Managed identity: $IDENTITY
  Resource ID: $IDENTITY_ID
  Principal ID: $IDENTITY_PRINCIPAL_ID
  Client ID: $IDENTITY_CLIENT_ID
RBAC: Storage Blob Data Contributor plus tag-write-only custom role at container scope
  Custom role: $BLOB_TAG_ROLE_NAME ($TAG_ROLE_DEFINITION_ID)
  Assignable scope: $RESOURCE_GROUP_ID (does not grant access)
  Assignment scope: $CONTAINER_SCOPE
Container Apps Job: $JOB_NAME ($JOB_ID)
Image: $IMAGE
Schedule: $BACKUP_CRON UTC (00:15 Europe/Madrid standard time; 01:15 daylight-saving time)
Application gate: due/local-date/idempotency safety guard; not a polling mechanism
Protection: private container, public Blob access disabled, versioning enabled,
  Blob/container soft delete 14 days, tagged daily retention $DAILY_RETENTION_DAYS days,
  monthly anchors $MONTHLY_RETENTION_MONTHS months, run records $RUN_RETENTION_DAYS days,
  staging $STAGING_RETENTION_DAYS day.
Identity selection: AZURE_CLIENT_ID is configured with the dedicated UAMI client ID.

Verify:
  az storage account show --name $STORAGE_ACCOUNT --resource-group $RESOURCE_GROUP --query '{kind:kind,httpsOnly:enableHttpsTrafficOnly,publicBlobAccess:allowBlobPublicAccess,minTls:minimumTlsVersion}' -o table
  az storage account blob-service-properties show --account-name $STORAGE_ACCOUNT --resource-group $RESOURCE_GROUP -o table
  az role assignment list --assignee-object-id $IDENTITY_PRINCIPAL_ID --scope $CONTAINER_SCOPE -o table
  az containerapp job show --name $JOB_NAME --resource-group $RESOURCE_GROUP -o yaml
  az containerapp job execution list --name $JOB_NAME --resource-group $RESOURCE_GROUP -o table
EOF
