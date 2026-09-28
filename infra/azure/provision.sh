#!/usr/bin/env bash
set -euo pipefail
set +x

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INFRA_DIR="$ROOT/infra/azure"
VALIDATOR="$INFRA_DIR/validate_config.py"
MAIN_BICEP="$INFRA_DIR/main.bicep"
AUTH_BICEP="$INFRA_DIR/modules/frontend-auth.bicep"
LOCKS_BICEP="$INFRA_DIR/modules/locks.bicep"

CONFIG=""
MODE=""
APPROVE_PLAN=""
REGISTER_PROVIDERS=false
BOOTSTRAP_ENTRA=false
ROTATE_ENTRA_SECRET=false
BOOTSTRAP_GITHUB_OIDC=false
PRESERVED_LIFECYCLE_RULES='[]'

usage() {
  cat <<'EOF'
Provision the complete Option Income Lab Azure stack.

Usage:
  infra/azure/provision.sh --config FILE --mode dry-run|preflight|what-if|apply [options]

Options:
  --approve-plan SHA256       Required by apply; exact fingerprint from what-if
  --register-providers        Apply only: register missing required providers
  --bootstrap-entra           Apply only: create/reuse the owned Entra app/SP
  --rotate-entra-secret       Apply only: create a new Entra client secret
  --bootstrap-github-oidc     Apply only: create/reuse the image-deploy OIDC app
  -h, --help                  Show this help

Secrets are read from environment variables named by the config and are never
written to config, parameters files, logs, or command arguments. Cosmos and
Foundry keys are obtained by Bicep listKeys() and stored only as Container Apps
secrets consumed through secretRef.
EOF
}

die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
info() { printf '%s\n' "$*"; }
need_value() { [[ $# -ge 2 && -n "$2" ]] || die "Missing value for $1"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) need_value "$@"; CONFIG="$2"; shift 2 ;;
    --mode) need_value "$@"; MODE="$2"; shift 2 ;;
    --approve-plan) need_value "$@"; APPROVE_PLAN="$2"; shift 2 ;;
    --register-providers) REGISTER_PROVIDERS=true; shift ;;
    --bootstrap-entra) BOOTSTRAP_ENTRA=true; shift ;;
    --rotate-entra-secret) ROTATE_ENTRA_SECRET=true; shift ;;
    --bootstrap-github-oidc) BOOTSTRAP_GITHUB_OIDC=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

[[ -n "$CONFIG" ]] || die "--config is required"
[[ "$MODE" =~ ^(dry-run|preflight|what-if|apply)$ ]] || die "--mode must be dry-run, preflight, what-if, or apply"
[[ -f "$CONFIG" ]] || die "Config file does not exist: $CONFIG"
if [[ "$MODE" != "apply" ]]; then
  ! $REGISTER_PROVIDERS && ! $BOOTSTRAP_ENTRA && ! $ROTATE_ENTRA_SECRET && ! $BOOTSTRAP_GITHUB_OIDC ||
    die "Mutation flags are valid only with --mode apply"
  [[ -z "$APPROVE_PLAN" ]] || die "--approve-plan is valid only with --mode apply"
fi

for tool in python3 jq az curl sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || die "Required tool not found: $tool"
done

python3 "$VALIDATOR" "$CONFIG" >/dev/null
CANONICAL_CONFIG="$(python3 "$VALIDATOR" "$CONFIG" --canonical)"
cfg() { jq -er "$1" <<<"$CANONICAL_CONFIG"; }

SUBSCRIPTION_ID="$(cfg '.subscriptionId')"
TENANT_ID="$(cfg '.tenantId')"
LOCATION="$(cfg '.location')"
RESOURCE_GROUP="$(cfg '.resourceGroupName')"
API_APP="$(cfg '.names.apiApp')"
FRONTEND_APP="$(cfg '.names.frontendApp')"
BACKUP_JOB="$(cfg '.names.backupJob')"
BACKUP_IDENTITY="$(cfg '.names.backupIdentity')"
COSMOS_ACCOUNT="$(cfg '.names.cosmosAccount')"
FOUNDRY_ACCOUNT="$(cfg '.names.foundryAccount')"
STORAGE_ACCOUNT="$(cfg '.names.backupStorage')"
BACKUP_CONTAINER="$(cfg '.names.backupContainer')"
ENTRA_CLIENT_ID_ENV="$(cfg '.frontendAuth.clientIdEnvironmentVariable')"
ENTRA_CLIENT_SECRET_ENV="$(cfg '.frontendAuth.clientSecretEnvironmentVariable')"
TELEGRAM_TOKEN_ENV="$(cfg '.optionalSecrets.telegramBotTokenEnvironmentVariable')"
TELEGRAM_CHAT_ENV="$(cfg '.optionalSecrets.telegramChatIdEnvironmentVariable')"

print_plan() {
  jq -r '
    "Mode: offline dry-run\n" +
    "Subscription: " + .subscriptionId + "\n" +
    "Tenant: " + .tenantId + "\n" +
    "Resource group/location: " + .resourceGroupName + " / " + .location + "\n" +
    "Images: " + .images.api + ", " + .images.frontend + "\n" +
    "Models: " + ([.models.deployments[].deploymentName] | join(", ")) + "\n" +
    "API ingress: internal (external=false)\n" +
    "Frontend: internal until Microsoft Entra Easy Auth validates\n" +
    "Registry: public GHCR only; no ACR, registry credentials, or pull identity\n" +
    "Runtime auth: Cosmos/Foundry keys as ACA secretRef (keyless is a future TODO)\n" +
    "Runtime RBAC: backup UAMI only, exact Blob-container contributor + tags/write\n" +
    "Azure calls: none"
  ' <<<"$CANONICAL_CONFIG"
}

if [[ "$MODE" == "dry-run" ]]; then
  print_plan
  exit 0
fi

az_read() { az "$@" --only-show-errors; }

verify_account() {
  local account
  account="$(az_read account show -o json 2>/dev/null)" ||
    die "Azure CLI is not logged in; run az login"
  [[ "$(jq -r '.id' <<<"$account")" == "$SUBSCRIPTION_ID" ]] ||
    die "Active subscription does not match config.subscriptionId"
  [[ "$(jq -r '.tenantId' <<<"$account")" == "$TENANT_ID" ]] ||
    die "Active tenant does not match config.tenantId"
  [[ "$(jq -r '.state' <<<"$account")" == "Enabled" ]] ||
    die "Configured subscription is not enabled"
}

provider_state() {
  az_read provider show --namespace "$1" --query registrationState -o tsv 2>/dev/null || printf 'Unknown'
}

ensure_providers() {
  local missing=() provider state
  while IFS= read -r provider; do
    state="$(provider_state "$provider")"
    [[ "$state" == "Registered" ]] || missing+=("$provider")
  done < <(jq -r '.providers[]' <<<"$CANONICAL_CONFIG")
  if ((${#missing[@]} == 0)); then return; fi
  if [[ "$MODE" == "apply" ]] && $REGISTER_PROVIDERS; then
    info "Registering explicitly approved providers: ${missing[*]}"
    for provider in "${missing[@]}"; do az_read provider register --namespace "$provider" --wait -o none; done
    for provider in "${missing[@]}"; do
      [[ "$(provider_state "$provider")" == "Registered" ]] ||
        die "Provider failed to register: $provider"
    done
  else
    die "Required providers are not registered: ${missing[*]}; apply may opt in with --register-providers"
  fi
}

verify_operator_permissions() {
  local permissions
  permissions="$(az_read rest --method get \
    --url "https://management.azure.com/subscriptions/${SUBSCRIPTION_ID}/providers/Microsoft.Authorization/permissions?api-version=2022-04-01" \
    -o json)"
  python3 - "$permissions" <<'PY'
import fnmatch, json, sys
permissions = json.loads(sys.argv[1]).get("value", [])
required = [
    "Microsoft.Resources/subscriptions/resourceGroups/write",
    "Microsoft.App/managedEnvironments/write",
    "Microsoft.App/containerApps/write",
    "Microsoft.App/jobs/write",
    "Microsoft.DocumentDB/databaseAccounts/write",
    "Microsoft.CognitiveServices/accounts/write",
    "Microsoft.Storage/storageAccounts/write",
    "Microsoft.ManagedIdentity/userAssignedIdentities/write",
    "Microsoft.OperationalInsights/workspaces/write",
    "Microsoft.Insights/diagnosticSettings/write",
    "Microsoft.Authorization/roleAssignments/write",
    "Microsoft.Authorization/roleDefinitions/write",
    "Microsoft.Authorization/locks/write",
]
def granted(action):
    for permission in permissions:
        actions = permission.get("actions") or []
        denied = permission.get("notActions") or []
        if any(fnmatch.fnmatchcase(action.lower(), pattern.lower()) for pattern in actions):
            if not any(fnmatch.fnmatchcase(action.lower(), pattern.lower()) for pattern in denied):
                return True
    return False
missing = [action for action in required if not granted(action)]
if missing:
    raise SystemExit("Operator lacks required effective actions: " + ", ".join(missing))
print("Operator control-plane permissions validated.")
PY
}

verify_ghcr_image_once() {
  local image="$1" reference repo ref url
  local headers challenge realm service scope token digest
  reference="${image#ghcr.io/}"
  if [[ "$reference" == *@sha256:* ]]; then
    repo="${reference%@sha256:*}"
    ref="${reference##*@}"
  else
    repo="${reference%:sha-*}"
    ref="${reference##*:}"
  fi
  url="https://ghcr.io/v2/${repo}/manifests/${ref}"
  headers="$(curl -fsSIL -H 'Accept: application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json' "$url" 2>&1 || true)"
  if ! grep -qi '^docker-content-digest:' <<<"$headers"; then
    challenge="$(grep -i '^www-authenticate: Bearer ' <<<"$headers" | tr -d '\r' | tail -1)"
    [[ -n "$challenge" ]] || die "GHCR image is absent or not anonymously readable: $image"
    realm="$(sed -n 's/.*realm="\([^"]*\)".*/\1/p' <<<"$challenge")"
    service="$(sed -n 's/.*service="\([^"]*\)".*/\1/p' <<<"$challenge")"
    scope="$(sed -n 's/.*scope="\([^"]*\)".*/\1/p' <<<"$challenge")"
    token="$(curl -fsSLG --data-urlencode "service=$service" --data-urlencode "scope=$scope" "$realm" | jq -er '.token // .access_token')" ||
      die "GHCR package requires credentials and is not public: $image"
    headers="$(curl -fsSIL -H "Authorization: Bearer $token" -H 'Accept: application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json' "$url")" ||
      die "GHCR manifest cannot be read anonymously: $image"
  fi
  digest="$(awk -F': ' 'tolower($1)=="docker-content-digest" {gsub("\r","",$2); print $2}' <<<"$headers" | tail -1)"
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || die "GHCR did not return a content digest: $image"
  if [[ "$image" == *@sha256:* ]]; then
    [[ "${image##*@}" == "$digest" ]] || die "GHCR digest mismatch for $image"
  fi
  printf '%s' "$digest"
}

verify_images() {
  local image first second
  while IFS= read -r image; do
    first="$(verify_ghcr_image_once "$image")"
    second="$(verify_ghcr_image_once "$image")"
    [[ "$first" == "$second" ]] || die "GHCR tag changed during preflight: $image"
    info "Verified public immutable GHCR image: $image -> $first"
  done < <(jq -r '.images.api, .images.frontend' <<<"$CANONICAL_CONFIG")
}

verify_region_and_names() {
  az_read account list-locations --query "[?name=='$LOCATION'].name | [0]" -o tsv | grep -qx "$LOCATION" ||
    die "Azure location is unavailable: $LOCATION"
  local rg_json
  rg_json="$(az_read group show --name "$RESOURCE_GROUP" -o json 2>/dev/null || true)"
  if [[ -n "$rg_json" ]]; then
    [[ "$(jq -r '.location' <<<"$rg_json")" == "$LOCATION" ]] ||
      die "Existing resource group is in a different location"
    python3 - "$rg_json" "$(cfg '.tags')" <<'PY'
import json, sys
resource, expected = map(json.loads, sys.argv[1:])
if any((resource.get("tags") or {}).get(k) != v for k, v in expected.items()):
    raise SystemExit("Existing resource group ownership tags are incompatible")
PY
  fi
  local cosmos_available storage_available foundry_available
  if ! az_read cosmosdb show -g "$RESOURCE_GROUP" -n "$COSMOS_ACCOUNT" -o none 2>/dev/null; then
    cosmos_available="$(az_read cosmosdb check-name-exists --name "$COSMOS_ACCOUNT" -o tsv)"
    [[ "$cosmos_available" == "false" ]] || die "Cosmos account name is owned outside the target resource group"
  fi
  if ! az_read storage account show -g "$RESOURCE_GROUP" -n "$STORAGE_ACCOUNT" -o none 2>/dev/null; then
    storage_available="$(az_read storage account check-name --name "$STORAGE_ACCOUNT" --query nameAvailable -o tsv)"
    [[ "$storage_available" == "true" ]] || die "Storage account name is unavailable"
  fi
  if ! az_read cognitiveservices account show -g "$RESOURCE_GROUP" -n "$FOUNDRY_ACCOUNT" -o none 2>/dev/null; then
    local deleted_foundry
    deleted_foundry="$(az_read cognitiveservices account list-deleted -o json |
      jq -c --arg name "$FOUNDRY_ACCOUNT" --arg location "$LOCATION" \
        '[.[] | select(.name == $name and .location == $location)]')"
    [[ "$(jq 'length' <<<"$deleted_foundry")" == "0" ]] ||
      die "Foundry account '$FOUNDRY_ACCOUNT' is soft-deleted in $LOCATION; recover or purge it before retrying"
    foundry_available="$(az_read rest --method post \
      --url "https://management.azure.com/subscriptions/${SUBSCRIPTION_ID}/providers/Microsoft.CognitiveServices/checkDomainAvailability?api-version=2025-06-01" \
      --body "{\"subdomainName\":\"${FOUNDRY_ACCOUNT}\",\"type\":\"Microsoft.CognitiveServices/accounts\"}" \
      --query isSubdomainAvailable -o tsv)"
    [[ "$foundry_available" == "true" ]] || die "Foundry custom domain name is unavailable"
  fi
}

verify_existing_drift() {
  local value
  if value="$(az_read cosmosdb show -g "$RESOURCE_GROUP" -n "$COSMOS_ACCOUNT" -o json 2>/dev/null)"; then
    python3 - "$value" "$(cfg '.tags')" <<'PY'
import json, sys
resource, expected = map(json.loads, sys.argv[1:])
actual = resource.get("tags") or {}
if any(actual.get(k) != v for k, v in expected.items()):
    raise SystemExit("Existing Cosmos account ownership tags are incompatible")
PY
    [[ "$(jq -r '.kind' <<<"$value")" == "GlobalDocumentDB" ]] || die "Existing Cosmos account kind is incompatible"
    jq -e '[.capabilities[]?.name] | index("EnableServerless") != null' <<<"$value" >/dev/null ||
      die "Existing Cosmos account is not serverless"
    while IFS=$'\t' read -r name partition ttl indexing; do
      local existing
      existing="$(az_read cosmosdb sql container show -g "$RESOURCE_GROUP" -a "$COSMOS_ACCOUNT" -d "$(cfg '.cosmos.databaseName')" -n "$name" -o json 2>/dev/null || true)"
      [[ -z "$existing" ]] && continue
      [[ "$(jq -r '.resource.partitionKey.paths[0]' <<<"$existing")" == "$partition" ]] ||
        die "Partition-key drift for Cosmos container $name"
    done < <(jq -r '.cosmos.containers[] | [.name,.partitionKey,(.ttl|tostring),.indexing] | @tsv' <<<"$CANONICAL_CONFIG")
  fi
  if value="$(az_read cognitiveservices account show -g "$RESOURCE_GROUP" -n "$FOUNDRY_ACCOUNT" -o json 2>/dev/null)"; then
    python3 - "$value" "$(cfg '.tags')" <<'PY'
import json, sys
resource, expected = map(json.loads, sys.argv[1:])
if any((resource.get("tags") or {}).get(k) != v for k, v in expected.items()):
    raise SystemExit("Existing Foundry account ownership tags are incompatible")
PY
    [[ "$(jq -r '.kind' <<<"$value")" == "AIServices" ]] || die "Existing Foundry account kind is incompatible"
    [[ "$(jq -r '.location' <<<"$value")" == "$LOCATION" ]] || die "Existing Foundry account region is incompatible"
  fi
  if value="$(az_read storage account show -g "$RESOURCE_GROUP" -n "$STORAGE_ACCOUNT" -o json 2>/dev/null)"; then
    python3 - "$value" "$(cfg '.tags')" <<'PY'
import json, sys
resource, expected = map(json.loads, sys.argv[1:])
if any((resource.get("tags") or {}).get(k) != v for k, v in expected.items()):
    raise SystemExit("Existing backup Storage ownership tags are incompatible")
PY
    [[ "$(jq -r '.kind' <<<"$value")" == "StorageV2" ]] || die "Existing backup Storage account is not StorageV2"
    [[ "$(jq -r '.sku.name' <<<"$value")" == "Standard_LRS" ]] || die "Existing backup Storage SKU is incompatible"
  fi
  if value="$(az_read containerapp show -g "$RESOURCE_GROUP" -n "$API_APP" -o json 2>/dev/null)"; then
    [[ "$(jq -r '.properties.configuration.ingress.external' <<<"$value")" == "false" ]] ||
      die "Existing API ingress is public; refusing to continue"
    jq -e '(.properties.configuration.registries // []) | length == 0' <<<"$value" >/dev/null ||
      die "Existing API has forbidden registry credentials"
  fi
  value="$(az_read role definition list --name 'Option Income Lab Backup Blob Tag Writer v1' --custom-role-only true -o json)"
  if [[ "$(jq 'length' <<<"$value")" != "0" ]]; then
    jq -e --arg rg "/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}" '
      length == 1 and
      .[0].assignableScopes == [$rg] and
      .[0].permissions == [{
        actions: [],
        notActions: [],
        dataActions: ["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/tags/write"],
        notDataActions: []
      }]
    ' <<<"$value" >/dev/null ||
      die "Existing backup tag-writer custom role has incompatible permissions or scope"
  fi
}

load_preserved_lifecycle_rules() {
  local policy
  policy="$(az_read storage account management-policy show \
    -g "$RESOURCE_GROUP" --account-name "$STORAGE_ACCOUNT" -o json 2>/dev/null || printf '{}')"
  PRESERVED_LIFECYCLE_RULES="$(jq -c '
    (.policy.rules // .properties.policy.rules // [])
    | map(select(.name as $name | [
      "backup-staging-1-day",
      "backup-daily-retention",
      "backup-monthly-anchor-retention",
      "backup-run-retention",
      "backup-version-retention"
    ] | index($name) | not))
  ' <<<"$policy")"
}

verify_models_and_quota() {
  local catalog usage existing
  catalog="$(az_read cognitiveservices model list --location "$LOCATION" --subscription "$SUBSCRIPTION_ID" -o json)"
  usage="$(az_read cognitiveservices usage list --location "$LOCATION" --subscription "$SUBSCRIPTION_ID" -o json)"
  existing="$(az_read cognitiveservices account deployment list -g "$RESOURCE_GROUP" -n "$FOUNDRY_ACCOUNT" -o json 2>/dev/null || printf '[]')"
  printf '%s\0%s\0%s\0%s\0' \
    "$CANONICAL_CONFIG" "$catalog" "$usage" "$existing" |
    python3 /dev/fd/3 3<<'PY'
import json, sys
payloads = sys.stdin.buffer.read().split(b"\0")
if payloads[-1] == b"":
    payloads.pop()
if len(payloads) != 4:
    raise SystemExit("Expected config, catalog, usage, and existing deployments")
cfg, catalog, usage, existing = (json.loads(payload) for payload in payloads)
for wanted in cfg["models"]["deployments"]:
    matches = []
    for item in catalog:
        model = item.get("model") or item
        if model.get("name") != wanted["model"] or model.get("version") != wanted["version"]:
            continue
        skus = model.get("skus") or item.get("skus") or []
        if any((sku.get("name") if isinstance(sku, dict) else sku) == wanted["sku"] for sku in skus):
            matches.append(item)
    if not matches:
        raise SystemExit(f"Model catalog lacks exact {wanted['model']}@{wanted['version']} / {wanted['sku']}")
    adopted = next((d for d in existing if d.get("name") == wanted["deploymentName"]), None)
    if adopted:
        props = adopted.get("properties", {})
        model = props.get("model", {})
        sku = adopted.get("sku", {})
        actual = (model.get("name"), model.get("version"), sku.get("name"))
        expected = (wanted["model"], wanted["version"], wanted["sku"])
        if actual != expected:
            raise SystemExit(f"Existing model deployment {wanted['deploymentName']} is incompatible")
        required = max(0, wanted["capacity"] - int(sku.get("capacity") or 0))
    else:
        required = wanted["capacity"]
    candidates = []
    needle = wanted["model"].lower()
    sku_needle = wanted["sku"].lower()
    for entry in usage:
        name = str((entry.get("name") or {}).get("value") or entry.get("name") or "").lower()
        if needle in name and sku_needle in name:
            candidates.append(int(entry.get("limit") or 0) - int(entry.get("currentValue") or 0))
    if not candidates:
        raise SystemExit(f"Quota row not found for {wanted['sku']}/{wanted['model']}")
    if max(candidates) < required:
        raise SystemExit(
            f"Insufficient quota for {wanted['deploymentName']}: need {required}, available {max(candidates)}"
        )
print("Foundry catalog, existing deployments, and quota validated.")
PY
}

preflight() {
  verify_account
  verify_operator_permissions
  ensure_providers
  verify_region_and_names
  verify_existing_drift
  load_preserved_lifecycle_rules
  verify_models_and_quota
  verify_images
  info "Preflight passed: read-only checks completed."
}

build_templates() {
  az bicep build --file "$MAIN_BICEP" --stdout --only-show-errors >/dev/null
  az bicep build --file "$AUTH_BICEP" --stdout --only-show-errors >/dev/null
  az bicep build --file "$LOCKS_BICEP" --stdout --only-show-errors >/dev/null
}

parameters_json() {
  local external="$1" entra_secret="$2" telegram_token="$3" telegram_chat="$4"
  CONFIG_JSON="$CANONICAL_CONFIG" FRONTEND_EXTERNAL="$external" ENTRA_SECRET="$entra_secret" \
    TELEGRAM_TOKEN="$telegram_token" TELEGRAM_CHAT="$telegram_chat" \
    PRESERVED_LIFECYCLE_RULES="$PRESERVED_LIFECYCLE_RULES" python3 - <<'PY'
import json, os
print(json.dumps({
  "config": {"value": json.loads(os.environ["CONFIG_JSON"])},
  "frontendExternal": {"value": os.environ["FRONTEND_EXTERNAL"] == "true"},
  "entraClientSecret": {"value": os.environ["ENTRA_SECRET"]},
  "telegramBotToken": {"value": os.environ["TELEGRAM_TOKEN"]},
  "telegramChatId": {"value": os.environ["TELEGRAM_CHAT"]},
  "preservedLifecycleRules": {"value": json.loads(os.environ["PRESERVED_LIFECYCLE_RULES"])},
}, separators=(",", ":")))
PY
}

run_what_if() {
  local params result
  params="$(parameters_json true '__SECURE_VALUE__' '' '')"
  result="$(printf '%s' "$params" | az_read deployment sub what-if \
    --name option-income-lab-plan \
    --location "$LOCATION" \
    --template-file "$MAIN_BICEP" \
    --parameters @/dev/stdin \
    --result-format FullResourcePayloads \
    --no-pretty-print -o json)"
  python3 - "$result" <<'PY'
import json, sys
plan = json.loads(sys.argv[1])
deletes = []
def walk(value):
    if isinstance(value, dict):
        if value.get("changeType") == "Delete":
            deletes.append(value.get("resourceId") or value.get("path") or "<unknown>")
        for child in value.values():
            walk(child)
    elif isinstance(value, list):
        for child in value:
            walk(child)
walk(plan)
if deletes:
    raise SystemExit("What-if contains forbidden delete/replacement changes: " + ", ".join(deletes))
PY
  jq -cS 'del(.. | .timestamp?)' <<<"$result"
}

fingerprint() {
  local whatif="$1"
  {
    printf '%s' "$CANONICAL_CONFIG"
    az bicep build --file "$MAIN_BICEP" --stdout --only-show-errors
    az bicep build --file "$AUTH_BICEP" --stdout --only-show-errors
    az bicep build --file "$LOCKS_BICEP" --stdout --only-show-errors
    printf '%s%s%s%s' "$TENANT_ID" "$SUBSCRIPTION_ID" "$LOCATION" "$whatif"
  } | sha256sum | cut -d' ' -f1
}

preflight
build_templates

if [[ "$MODE" == "preflight" ]]; then exit 0; fi

WHAT_IF="$(run_what_if)"
FINGERPRINT="$(fingerprint "$WHAT_IF")"
info "Plan fingerprint: $FINGERPRINT"
if [[ "$MODE" == "what-if" ]]; then
  jq '.' <<<"$WHAT_IF"
  exit 0
fi

[[ -n "$APPROVE_PLAN" ]] || die "apply requires --approve-plan $FINGERPRINT"
[[ "$APPROVE_PLAN" == "$FINGERPRINT" ]] ||
  die "Approved fingerprint does not match the freshly computed plan"

read_named_environment_variable() {
  local name="$1" config_path="$2"
  [[ "$name" =~ ^[A-Z][A-Z0-9_]{1,63}$ ]] ||
    die "$config_path must contain an environment variable name, not its value"
  printf '%s' "${!name:-}"
}

ENTRA_CLIENT_ID="$(read_named_environment_variable \
  "$ENTRA_CLIENT_ID_ENV" "frontendAuth.clientIdEnvironmentVariable")"
ENTRA_CLIENT_SECRET="$(read_named_environment_variable \
  "$ENTRA_CLIENT_SECRET_ENV" "frontendAuth.clientSecretEnvironmentVariable")"
TELEGRAM_TOKEN="$(read_named_environment_variable \
  "$TELEGRAM_TOKEN_ENV" "optionalSecrets.telegramBotTokenEnvironmentVariable")"
TELEGRAM_CHAT="$(read_named_environment_variable \
  "$TELEGRAM_CHAT_ENV" "optionalSecrets.telegramChatIdEnvironmentVariable")"
[[ -z "$TELEGRAM_TOKEN" && -z "$TELEGRAM_CHAT" || -n "$TELEGRAM_TOKEN" && -n "$TELEGRAM_CHAT" ]] ||
  die "Telegram token and chat ID must either both be set or both be empty"
[[ -n "$ENTRA_CLIENT_ID" || "$BOOTSTRAP_ENTRA" == true ]] ||
  die "$ENTRA_CLIENT_ID_ENV is required unless --bootstrap-entra is used"
[[ -n "$ENTRA_CLIENT_SECRET" || "$BOOTSTRAP_ENTRA" == true || "$ROTATE_ENTRA_SECRET" == true ]] ||
  die "$ENTRA_CLIENT_SECRET_ENV is required unless Entra secret creation/rotation is explicitly requested"

deploy_main() {
  local external="$1" secret="$2" params
  params="$(parameters_json "$external" "$secret" "$TELEGRAM_TOKEN" "$TELEGRAM_CHAT")"
  printf '%s' "$params" | az_read deployment sub create \
    --name option-income-lab \
    --location "$LOCATION" \
    --template-file "$MAIN_BICEP" \
    --parameters @/dev/stdin \
    -o json
}

info "Applying phase 1: all Azure resources with frontend ingress internal."
PHASE1="$(deploy_main false '')"
FRONTEND_FQDN="$(jq -er '.properties.outputs.frontendFqdn.value' <<<"$PHASE1")"
API_FQDN="$(jq -er '.properties.outputs.apiFqdn.value' <<<"$PHASE1")"
REDIRECT_URI="https://${FRONTEND_FQDN}/.auth/login/aad/callback"

if [[ -z "$ENTRA_CLIENT_ID" ]]; then
  $BOOTSTRAP_ENTRA || die "$ENTRA_CLIENT_ID_ENV is required unless --bootstrap-entra is used; frontend remains internal"
  DISPLAY_NAME="option-income-lab-${RESOURCE_GROUP}"
  ENTRA_CLIENT_ID="$(az_read ad app list --display-name "$DISPLAY_NAME" --query "[?contains(tags, 'option-income-lab-provisioner-v1')].appId | [0]" -o tsv)"
  if [[ -z "$ENTRA_CLIENT_ID" ]]; then
    ENTRA_CLIENT_ID="$(az_read ad app create --display-name "$DISPLAY_NAME" --sign-in-audience AzureADMyOrg \
      --set tags='[\"option-income-lab-provisioner-v1\"]' --query appId -o tsv)"
  fi
  az_read ad sp show --id "$ENTRA_CLIENT_ID" -o none 2>/dev/null ||
    az_read ad sp create --id "$ENTRA_CLIENT_ID" -o none
fi

CURRENT_URIS="$(az_read ad app show --id "$ENTRA_CLIENT_ID" --query 'web.redirectUris' -o json)"
mapfile -t REDIRECT_URIS < <(jq -r --arg wanted "$REDIRECT_URI" '(. + [$wanted]) | unique[]' <<<"$CURRENT_URIS")
az_read ad app update --id "$ENTRA_CLIENT_ID" --web-redirect-uris "${REDIRECT_URIS[@]}" -o none
az_read ad app show --id "$ENTRA_CLIENT_ID" --query 'web.redirectUris' -o json |
  jq -e --arg callback "$REDIRECT_URI" 'index($callback) != null' >/dev/null ||
  die "Microsoft Entra callback reconciliation failed; frontend remains internal"

if [[ -z "$ENTRA_CLIENT_SECRET" || "$ROTATE_ENTRA_SECRET" == true ]]; then
  $BOOTSTRAP_ENTRA || $ROTATE_ENTRA_SECRET ||
    die "$ENTRA_CLIENT_SECRET_ENV is required; frontend remains internal"
  ENTRA_CLIENT_SECRET="$(az_read ad app credential reset --id "$ENTRA_CLIENT_ID" \
    --append --display-name option-income-lab-container-app --years 1 --query password -o tsv)"
fi
[[ -n "$ENTRA_CLIENT_SECRET" ]] || die "Microsoft Entra client secret is empty; frontend remains internal"

info "Applying phase 2: install the Entra secret while frontend remains internal."
deploy_main false "$ENTRA_CLIENT_SECRET" >/dev/null

AUTH_PARAMS="$(jq -cn \
  --arg frontend "$FRONTEND_APP" --arg tenant "$TENANT_ID" --arg client "$ENTRA_CLIENT_ID" \
  '{frontendName:{value:$frontend},tenantId:{value:$tenant},clientId:{value:$client},unauthenticatedClientAction:{value:"RedirectToLoginPage"}}')"
printf '%s' "$AUTH_PARAMS" | az_read deployment group create \
  --name option-income-lab-frontend-auth \
  --resource-group "$RESOURCE_GROUP" \
  --template-file "$AUTH_BICEP" \
  --parameters @/dev/stdin --mode Incremental -o none

AUTH_JSON="$(az_read containerapp auth show -g "$RESOURCE_GROUP" -n "$FRONTEND_APP" -o json)"
jq -e --arg tenant "$TENANT_ID" --arg client "$ENTRA_CLIENT_ID" '
  .platform.enabled == true and
  .globalValidation.requireAuthentication == true and
  .globalValidation.unauthenticatedClientAction == "RedirectToLoginPage" and
  .identityProviders.azureActiveDirectory.registration.clientId == $client and
  (.identityProviders.azureActiveDirectory.registration.openIdIssuer | contains($tenant)) and
  .identityProviders.azureActiveDirectory.registration.clientSecretSettingName == "entra-client-secret" and
  (.identityProviders.azureActiveDirectory.validation.allowedAudiences | index($client) != null)
' <<<"$AUTH_JSON" >/dev/null || die "Easy Auth validation failed; frontend remains internal"

info "Applying phase 3: expose the authenticated frontend."
FINAL="$(deploy_main true "$ENTRA_CLIENT_SECRET")"

LOCK_PARAMS="$(jq -cn --arg c "$COSMOS_ACCOUNT" --arg f "$FOUNDRY_ACCOUNT" --arg s "$STORAGE_ACCOUNT" \
  '{cosmosAccountName:{value:$c},foundryAccountName:{value:$f},storageAccountName:{value:$s}}')"
printf '%s' "$LOCK_PARAMS" | az_read deployment group create \
  --name option-income-lab-locks --resource-group "$RESOURCE_GROUP" \
  --template-file "$LOCKS_BICEP" --parameters @/dev/stdin --mode Incremental -o none

verify_revision() {
  local app="$1" revision state health active
  revision="$(az_read containerapp show -g "$RESOURCE_GROUP" -n "$app" --query properties.latestRevisionName -o tsv)"
  for delay in 15 30 60 120 120 120 120; do
    state="$(az_read containerapp revision show -g "$RESOURCE_GROUP" -n "$app" --revision "$revision" -o json)"
    health="$(jq -r '.properties.healthState // "Unknown"' <<<"$state")"
    active="$(jq -r '.properties.active // false' <<<"$state")"
    [[ "$health" == "Healthy" && "$active" == "true" ]] && return
    [[ "$health" == "Unhealthy" ]] && die "$app revision is unhealthy"
    sleep "$delay"
  done
  die "$app revision did not become healthy"
}
verify_revision "$API_APP"
verify_revision "$FRONTEND_APP"

[[ "$(az_read containerapp show -g "$RESOURCE_GROUP" -n "$API_APP" --query properties.configuration.ingress.external -o tsv)" == "false" ]] ||
  die "Post-deployment invariant failed: API is public"
[[ "$(az_read containerapp show -g "$RESOURCE_GROUP" -n "$FRONTEND_APP" --query properties.configuration.ingress.external -o tsv)" == "true" ]] ||
  die "Post-deployment invariant failed: authenticated frontend is not public"
for app in "$API_APP" "$FRONTEND_APP"; do
  APP_JSON="$(az_read containerapp show -g "$RESOURCE_GROUP" -n "$app" -o json)"
  jq -e '(.properties.configuration.registries // []) | length == 0' <<<"$APP_JSON" >/dev/null ||
    die "$app has forbidden registry configuration"
  jq -e '(.identity.type // "None") == "None"' <<<"$APP_JSON" >/dev/null ||
    die "$app has a forbidden runtime managed identity"
done
JOB_JSON="$(az_read containerapp job show -g "$RESOURCE_GROUP" -n "$BACKUP_JOB" -o json)"
jq -e '(.properties.configuration.registries // []) | length == 0' <<<"$JOB_JSON" >/dev/null ||
  die "Backup Job has forbidden registry configuration"
[[ "$(jq -r '.identity.type' <<<"$JOB_JSON")" == "UserAssigned" ]] ||
  die "Backup Job does not exclusively use its backup UAMI"

API_JSON="$(az_read containerapp show -g "$RESOURCE_GROUP" -n "$API_APP" -o json)"
jq -e '
  ([.properties.template.containers[].env[] |
    select(.name == "COSMOSDB_KEY" and .secretRef == "cosmosdb-key")] | length == 1)
  and
  ([.properties.template.containers[].env[] |
    select(.name == "AZURE_OPENAI_API_KEY" and .secretRef == "foundry-api-key")] | length == 1)
' <<<"$API_JSON" >/dev/null ||
  die "API Cosmos/Foundry credentials are not wired exclusively through secretRef"
FRONTEND_JSON="$(az_read containerapp show -g "$RESOURCE_GROUP" -n "$FRONTEND_APP" -o json)"
jq -e --arg expected "https://${API_FQDN}" '
  [.properties.template.containers[].env[] |
    select(.name == "API_BASE_URL" and .value == $expected)] | length == 1
' <<<"$FRONTEND_JSON" >/dev/null ||
  die "Frontend API_BASE_URL is not the actual internal API FQDN"

PUBLIC_API_STATUS="$(curl -skS --connect-timeout 8 --max-time 12 -o /dev/null -w '%{http_code}' "https://${API_FQDN}/healthz" || true)"
[[ "$PUBLIC_API_STATUS" == "000" ]] ||
  die "Public negative API probe unexpectedly received HTTP $PUBLIC_API_STATUS"
FRONTEND_HEADERS="$(curl -skSI --connect-timeout 10 --max-time 20 "https://${FRONTEND_FQDN}/")"
FRONTEND_STATUS="$(awk 'NR==1{print $2}' <<<"$FRONTEND_HEADERS")"
[[ "$FRONTEND_STATUS" =~ ^30[12378]$ ]] ||
  die "Anonymous frontend request did not redirect to Microsoft Entra"
grep -Eqi '^location: .*(login\.microsoftonline\.com|/\.auth/login/aad)' <<<"$FRONTEND_HEADERS" ||
  die "Anonymous frontend redirect did not target Microsoft Entra"

ROLE_SCOPE="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.Storage/storageAccounts/${STORAGE_ACCOUNT}/blobServices/default/containers/${BACKUP_CONTAINER}"
IDENTITY_PRINCIPAL_ID="$(az_read identity show -g "$RESOURCE_GROUP" -n "$BACKUP_IDENTITY" --query principalId -o tsv)"
ASSIGNMENTS='[]'
for delay in 15 30 60 120 120 120 120; do
  ASSIGNMENTS="$(az_read role assignment list --assignee-object-id "$IDENTITY_PRINCIPAL_ID" --all -o json)"
  CONTRIBUTOR_COUNT="$(jq --arg scope "$ROLE_SCOPE" '[.[] | select(.scope == $scope and .roleDefinitionName == "Storage Blob Data Contributor")] | length' <<<"$ASSIGNMENTS")"
  TAG_COUNT="$(jq --arg scope "$ROLE_SCOPE" '[.[] | select(.scope == $scope and .roleDefinitionName == "Option Income Lab Backup Blob Tag Writer v1")] | length' <<<"$ASSIGNMENTS")"
  [[ "$CONTRIBUTOR_COUNT" == "1" && "$TAG_COUNT" == "1" ]] && break
  sleep "$delay"
done
[[ "$CONTRIBUTOR_COUNT" == "1" && "$TAG_COUNT" == "1" ]] ||
  die "Backup RBAC assignments did not propagate at the exact Blob container scope"
jq -e --arg scope "$ROLE_SCOPE" '
  [.[] | select(.scope == $scope)] | length == 2 and
  [.[] | select(.scope != $scope)] | length == 0
' <<<"$ASSIGNMENTS" >/dev/null ||
  die "Backup UAMI has assignments outside the exact two-role matrix"
jq -e '[.[] | select((.roleDefinitionName == "Contributor" or .roleDefinitionName == "Owner"))] | length == 0' <<<"$ASSIGNMENTS" >/dev/null ||
  die "Backup UAMI has a forbidden elevated runtime role"

info "Starting real-identity Blob and internal API verification Job execution."
STATUS=''
for propagation_delay in 15 30 60 120; do
  EXECUTION="$(az_read containerapp job start -g "$RESOURCE_GROUP" -n "$BACKUP_JOB" \
    --container-name backup --command python \
    --args scripts/verify_azure_access.py --blob-rbac --cosmos-key --internal-api-url "https://${API_FQDN}" \
    --query name -o tsv)"
  [[ -n "$EXECUTION" ]] || die "Could not start runtime access verification"
  for poll_delay in 15 30 60 120; do
    STATUS="$(az_read containerapp job execution show -g "$RESOURCE_GROUP" -n "$BACKUP_JOB" --job-execution-name "$EXECUTION" --query properties.status -o tsv)"
    [[ "$STATUS" == "Succeeded" || "$STATUS" == "Failed" ]] && break
    sleep "$poll_delay"
  done
  [[ "$STATUS" == "Succeeded" ]] && break
  sleep "$propagation_delay"
done
[[ "$STATUS" == "Succeeded" ]] || die "Runtime access verification timed out"

if $BOOTSTRAP_GITHUB_OIDC; then
  command -v gh >/dev/null 2>&1 || die "gh is required for --bootstrap-github-oidc"
  [[ -n "${GITHUB_REPOSITORY:-}" ]] || die "GITHUB_REPOSITORY=owner/repo is required for OIDC bootstrap"
  REPO_JSON="$(gh api "repos/${GITHUB_REPOSITORY}")"
  OWNER_ID="$(jq -r '.owner.id' <<<"$REPO_JSON")"
  REPO_ID="$(jq -r '.id' <<<"$REPO_JSON")"
  OWNER_LOGIN="$(jq -r '.owner.login' <<<"$REPO_JSON")"
  REPO_NAME="$(jq -r '.name' <<<"$REPO_JSON")"
  SUBJECT="repo:${OWNER_LOGIN}@${OWNER_ID}/${REPO_NAME}@${REPO_ID}:environment:production"
  gh api -X PUT "repos/${GITHUB_REPOSITORY}/actions/oidc/customization/sub" \
    -F use_default=true -F use_immutable_subject=true >/dev/null
  OIDC_NAME="github-actions-option-income-lab"
  OIDC_APP_ID="$(az_read ad app list --display-name "$OIDC_NAME" --query '[0].appId' -o tsv)"
  [[ -n "$OIDC_APP_ID" ]] || OIDC_APP_ID="$(az_read ad app create --display-name "$OIDC_NAME" --query appId -o tsv)"
  OIDC_SP_ID="$(az_read ad sp show --id "$OIDC_APP_ID" --query id -o tsv 2>/dev/null || az_read ad sp create --id "$OIDC_APP_ID" --query id -o tsv)"
  FEDERATED="$(jq -cn --arg subject "$SUBJECT" '{name:"github-actions-production-env",issuer:"https://token.actions.githubusercontent.com",subject:$subject,audiences:["api://AzureADTokenExchange"],description:"Immutable repository/environment OIDC"}')"
  if ! az_read ad app federated-credential show --id "$OIDC_APP_ID" --federated-credential-id github-actions-production-env -o none 2>/dev/null; then
    printf '%s' "$FEDERATED" | az_read ad app federated-credential create --id "$OIDC_APP_ID" --parameters @/dev/stdin -o none
  fi
  for spec in \
    "Container Apps Contributor|/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.App/containerApps/${API_APP}" \
    "Container Apps Contributor|/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.App/containerApps/${FRONTEND_APP}" \
    "Container Apps Jobs Contributor|/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.App/jobs/${BACKUP_JOB}"; do
    role="${spec%%|*}"; scope="${spec#*|}"
    if ! az_read role assignment list --assignee-object-id "$OIDC_SP_ID" --scope "$scope" \
      --query "[?roleDefinitionName=='$role'] | [0].id" -o tsv | grep -q .; then
      az_read role assignment create --assignee-object-id "$OIDC_SP_ID" --assignee-principal-type ServicePrincipal \
        --role "$role" --scope "$scope" -o none
    fi
  done
  info "GitHub OIDC client ID: $OIDC_APP_ID"
fi

info "Provisioning succeeded."
info "Frontend: https://${FRONTEND_FQDN}"
info "API: internal-only (${API_FQDN})"
info "Future TODO: migrate Cosmos and Foundry runtime authentication to managed identity; no roles were pre-assigned."
