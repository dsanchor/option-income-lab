import json
from pathlib import Path
import subprocess
import sys

import yaml

from web.backup_routes import router as backup_router


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (ROOT / "backend/scripts/configure-backup.sh").read_text()
APP_CONFIG = (ROOT / "backend/config.yaml").read_text()
WORKFLOW_PATH = ROOT / ".github/workflows/docker-publish.yml"
WORKFLOW = WORKFLOW_PATH.read_text()
ROLE_VALIDATOR = SCRIPT.split(
    'if ! python3 - "$ROLE_BY_ID" "$TAG_ROLE_DEFINITION_ID" '
    '"$BLOB_TAG_ROLE_NAME" "$RESOURCE_GROUP_ID" "$BLOB_TAG_DATA_ACTION" <<\'PY\'',
    1,
)[1].split("\nPY\nthen", 1)[0]
ASSIGNMENT_VALIDATOR = SCRIPT.split(
    'if ! python3 - "$TAG_ASSIGNMENTS" "$TAG_ROLE_DEFINITION_ID" '
    '"$CONTAINER_SCOPE" "$IDENTITY_PRINCIPAL_ID" <<\'PY\'',
    1,
)[1].split("\nPY\nthen", 1)[0]


def _run_embedded_validator(code: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-", *args],
        input=code,
        text=True,
        capture_output=True,
        check=False,
    )


def test_provisioning_explicitly_selects_the_dedicated_identity():
    assert 'IDENTITY_CLIENT_ID="$(az identity show' in SCRIPT
    assert '"AZURE_CLIENT_ID=$IDENTITY_CLIENT_ID"' in SCRIPT
    assert "--mi-user-assigned" in SCRIPT
    assert 'Storage Blob Data Contributor' in SCRIPT
    assert 'readonly BLOB_TAG_ROLE_PREFIX="Option Income Lab Backup Blob Tags"' in SCRIPT
    assert "uuid.uuid5(uuid.NAMESPACE_URL, seed)" in SCRIPT
    assert 'BLOB_TAG_ROLE_NAME="${BLOB_TAG_ROLE_PREFIX} ${TAG_ROLE_DEFINITION_ID:0:12}"' in SCRIPT
    assert (
        "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/tags/write"
        in SCRIPT
    )
    assert '"AssignableScopes": ["$RESOURCE_GROUP_ID"]' in SCRIPT
    assert "--scope \"$CONTAINER_SCOPE\"" in SCRIPT
    assert '--role "$TAG_ROLE_DEFINITION_ID"' in SCRIPT
    assert 'CONTAINER_SCOPE="${STORAGE_ID}/blobServices/default/containers/${CONTAINER}"' in SCRIPT


def test_custom_tag_role_fails_closed_on_stale_or_broader_existing_definition():
    assert 'ROLE_BY_ID="$(az role definition list' in SCRIPT
    assert 'ROLE_BY_NAME="$(az role definition list' in SCRIPT
    assert "already belongs to another role ID" in SCRIPT
    assert 'role.get("roleType") != "CustomRole"' in SCRIPT
    assert "expected exactly one permission block" in SCRIPT
    assert 'expected = {' in SCRIPT
    assert '"actions": []' in SCRIPT
    assert '"notActions": []' in SCRIPT
    assert '"dataActions": [expected_action]' in SCRIPT
    assert '"notDataActions": []' in SCRIPT
    assert "assignableScopes=" in SCRIPT
    assert "stale or broader than required" in SCRIPT
    assert "role definition update" not in SCRIPT

    role_id = "11111111-2222-3333-4444-555555555555"
    role_name = "Option Income Lab Backup Blob Tags 11111111-222"
    scope = "/subscriptions/sub/resourceGroups/backup-rg"
    action = "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/tags/write"
    exact = [{
        "name": role_id,
        "roleName": role_name,
        "roleType": "CustomRole",
        "permissions": [{
            "actions": [],
            "notActions": [],
            "dataActions": [action],
            "notDataActions": [],
        }],
        "assignableScopes": [scope],
    }]
    assert _run_embedded_validator(
        ROLE_VALIDATOR, json.dumps(exact), role_id, role_name, scope, action
    ).returncode == 0

    stale = json.loads(json.dumps(exact))
    stale[0]["permissions"][0]["dataActions"].append(
        "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"
    )
    stale[0]["assignableScopes"] = ["/subscriptions/sub"]
    rejected = _run_embedded_validator(
        ROLE_VALIDATOR, json.dumps(stale), role_id, role_name, scope, action
    )
    assert rejected.returncode != 0
    assert "dataActions=" in rejected.stderr
    assert "assignableScopes=" in rejected.stderr


def test_custom_tag_role_rerun_and_assignment_are_exact_and_idempotent():
    create_block = SCRIPT.split('TAG_ROLE_DEFINITION="$(cat <<EOF', 1)[1].split(
        "ROLE_BY_ID=", 1
    )[0]
    assert '"Id": "$TAG_ROLE_DEFINITION_ID"' in create_block
    assert '"IsCustom": true' in create_block
    assert create_block.count("az role definition create") == 1
    assert SCRIPT.count('az role definition create') == 1
    assert SCRIPT.count('az role assignment create') == 2
    assert SCRIPT.count('--include-inherited') == 2
    assert SCRIPT.count('item.get("roleDefinitionId")') == 2
    assert SCRIPT.count('item.get("scope")') == 2
    assert SCRIPT.count('item.get("principalId")') == 2
    assert "expected one exact assignment" in SCRIPT

    role_id = "11111111-2222-3333-4444-555555555555"
    scope = (
        "/subscriptions/sub/resourceGroups/backup-rg/providers/Microsoft.Storage/"
        "storageAccounts/backups/blobServices/default/containers/user-data-backups"
    )
    principal_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    exact = [{
        "id": "/subscriptions/sub/providers/Microsoft.Authorization/roleAssignments/a1",
        "principalId": principal_id,
        "roleDefinitionId": (
            "/subscriptions/sub/providers/Microsoft.Authorization/roleDefinitions/"
            + role_id
        ),
        "scope": scope,
    }]
    assert _run_embedded_validator(
        ASSIGNMENT_VALIDATOR, json.dumps(exact), role_id, scope, principal_id
    ).returncode == 0

    wrong_scope = json.loads(json.dumps(exact))
    wrong_scope[0]["scope"] = scope.rsplit("/blobServices", 1)[0]
    assert _run_embedded_validator(
        ASSIGNMENT_VALIDATOR, json.dumps(wrong_scope), role_id, scope, principal_id
    ).returncode != 0


def test_job_environment_and_azure_cron_are_the_only_automatic_config_surface():
    parsed = yaml.safe_load(APP_CONFIG)
    assert "automatic_backup" not in parsed
    assert 'readonly BACKUP_CRON="15 23 * * *"' in SCRIPT
    assert "00:15 Europe/Madrid standard time; 01:15 daylight-saving time" in SCRIPT
    assert "safety guard; not a polling mechanism" in SCRIPT
    assert SCRIPT.count('--cron-expression "$BACKUP_CRON"') == 2
    help_block = SCRIPT.split("usage() {", 1)[1].split("die() {", 1)[0]
    dry_run_block = SCRIPT.split("if $DRY_RUN; then", 1)[1].split("fi", 1)[0]
    output_block = SCRIPT.split("Backup infrastructure configured.", 1)[1]
    assert "Azure triggers once daily at 23:15 UTC" in help_block
    assert "Schedule: $BACKUP_CRON UTC" in dry_run_block
    assert "Schedule: $BACKUP_CRON UTC" in output_block
    for variable in (
        "BACKUP_ENABLED",
        "BACKUP_BLOB_CONTAINER",
        "BACKUP_TIMEZONE",
        "BACKUP_LOCAL_TIME",
        "BACKUP_SCHEDULE_NAME",
    ):
        assert f'"{variable}=' in SCRIPT
    assert "--cron-expression \"$BACKUP_CRON\"" in SCRIPT


def test_api_has_no_automatic_backup_configuration_or_status_surface():
    paths = {route.path for route in backup_router.routes}
    assert "/api/backups/automatic" not in paths
    assert all(not path.startswith("/api/backups/automatic/") for path in paths)


def test_lifecycle_deletes_only_unanchored_daily_archives():
    assert '"name":"backup-daily-35-days"' in SCRIPT
    assert '"name":"retentionClass","op":"==","value":"daily"' in SCRIPT
    assert '"prefixMatch":["$CONTAINER/v1/daily/"]' in SCRIPT
    assert '"name":"backup-monthly-anchors-12-months"' in SCRIPT
    assert '"prefixMatch":["$CONTAINER/v1/monthly/"]' in SCRIPT
    assert '"value":"monthly"' not in SCRIPT


def test_provisioning_reconciles_managed_rules_without_replacing_unrelated_rules():
    assert 'managed_names = {rule["name"] for rule in desired["rules"]}' in SCRIPT
    assert 'if rule.get("name") not in managed_names' in SCRIPT
    assert "preserved + desired[\"rules\"]" in SCRIPT


def test_dry_run_and_help_do_not_read_or_print_secret_values():
    dry_run_block = SCRIPT.split("if $DRY_RUN; then", 1)[1].split("fi", 1)[0]
    assert "COSMOSDB_KEY" not in dry_run_block
    assert "GHCR_PAT" not in dry_run_block
    assert "set +x" in SCRIPT
    assert "no secret value is printed" in SCRIPT


def test_workflow_is_valid_yaml_and_image_only_update_guards_identity_and_env():
    parsed = yaml.safe_load(WORKFLOW_PATH.read_text())
    assert "deploy" in parsed["jobs"]
    assert "AZURE_CLIENT_ID" in WORKFLOW
    assert "ENV_BEFORE_HASH" in WORKFLOW
    assert "IDENTITIES_BEFORE_HASH" in WORKFLOW
    assert "--image \"$IMAGE\"" in WORKFLOW
    update_step = WORKFLOW.split("- name: Update backup Job image when configured", 1)[1]
    assert "job start" not in update_step
    assert "cron-expression" not in update_step


def test_existing_local_runtime_tests_cover_core_smoke_wiring():
    upload_tests = (ROOT / "backend/tests/test_user_backup_blob_upload.py").read_text()
    deployment_docs = (ROOT / "docs/deployment.md").read_text()
    assert "test_changed_upload_is_verified_before_latest_and_unchanged_is_skipped" in upload_tests
    assert "test_manual_only_if_changed_records_no_change_without_new_zip" in upload_tests
    assert "test_lease_blocks_concurrent_holder" in upload_tests
    assert "verified orphan upload is adopted after simulated pointer" in deployment_docs
    assert "Local syntax/static tests" in deployment_docs


def test_rbac_documentation_matches_the_exact_role_and_assignment_contract():
    docs = "\n".join(
        (ROOT / path).read_text()
        for path in (
            "docs/deployment.md",
            "docs/architecture.md",
            "docs/agents.md",
            ".squad/designs/user-data-backup-restore-design.md",
        )
    )
    assert docs.count("Storage Blob Data Contributor") >= 4
    assert docs.count("tags/write") >= 4
    assert docs.count("AssignableScopes") >= 4
    assert docs.count("roleDefinitions/write") >= 2
    assert docs.count("roleAssignments/write") >= 2
