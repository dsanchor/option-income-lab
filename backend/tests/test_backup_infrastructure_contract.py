from pathlib import Path

import yaml

from web.backup_routes import router as backup_router


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (ROOT / "backend/scripts/configure-backup.sh").read_text()
APP_CONFIG = (ROOT / "backend/config.yaml").read_text()
WORKFLOW_PATH = ROOT / ".github/workflows/docker-publish.yml"
WORKFLOW = WORKFLOW_PATH.read_text()


def test_provisioning_explicitly_selects_the_dedicated_identity():
    assert 'IDENTITY_CLIENT_ID="$(az identity show' in SCRIPT
    assert '"AZURE_CLIENT_ID=$IDENTITY_CLIENT_ID"' in SCRIPT
    assert "--mi-user-assigned" in SCRIPT
    assert 'Storage Blob Data Contributor' in SCRIPT
    assert 'CONTAINER_SCOPE="${STORAGE_ID}/blobServices/default/containers/${CONTAINER}"' in SCRIPT


def test_job_environment_and_azure_cron_are_the_only_automatic_config_surface():
    parsed = yaml.safe_load(APP_CONFIG)
    assert "automatic_backup" not in parsed
    assert 'readonly BACKUP_CRON="*/15 * * * *"' in SCRIPT
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
