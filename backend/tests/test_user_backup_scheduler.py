from datetime import datetime, timezone

from src.backup.automatic_backup import (
    AutomaticBackupConfig,
    AutomaticBackupService,
    due_for_local_date,
)
from src.backup.blob_store import BlobStore
from src.backup.collectors import CosmosBackupCollector
from src.backup.export_service import ExportService

from .user_backup_fakes import FakeBlobContainer, populated_cosmos

CFG = AutomaticBackupConfig(timezone="Europe/Madrid", local_time="00:15")


def test_scheduled_gate_handles_dst_and_same_day_catchup():
    due, local_date = due_for_local_date(
        datetime(2026, 3, 29, 1, 0, tzinfo=timezone.utc), CFG
    )
    assert due and local_date == "2026-03-29"
    due_again, _ = due_for_local_date(
        datetime(2026, 3, 29, 2, 0, tzinfo=timezone.utc), CFG, {"2026-03-29"}
    )
    assert not due_again


def test_before_local_due_is_not_due():
    due, _ = due_for_local_date(
        datetime(2026, 9, 18, 22, 10, tzinfo=timezone.utc), CFG
    )
    assert not due


def test_environment_is_the_authoritative_automatic_backup_configuration(monkeypatch):
    monkeypatch.setenv("BACKUP_ENABLED", "false")
    monkeypatch.setenv("BACKUP_TIMEZONE", "America/New_York")
    monkeypatch.setenv("BACKUP_LOCAL_TIME", "03:45")
    monkeypatch.setenv("BACKUP_SCHEDULE_NAME", "production-job")

    config = AutomaticBackupConfig.from_environment()

    assert config == AutomaticBackupConfig(
        enabled=False,
        timezone="America/New_York",
        local_time="03:45",
        schedule_name="production-job",
    )


def test_scheduled_failure_preserves_prior_success_and_latest_archive():
    blobs = BlobStore(FakeBlobContainer())
    service = AutomaticBackupService(
        ExportService(CosmosBackupCollector(populated_cosmos())),
        blobs,
        CFG,
    )
    success = service.run(
        trigger="scheduled",
        now_utc=datetime(2026, 9, 19, 1, 0, tzinfo=timezone.utc),
    )
    assert success.state == "UPLOADED"
    before = blobs.read_json("v1/control/health.json").value

    class FailingExporter:
        def preview(self, request):
            raise RuntimeError("injected export failure")

    service.exporter = FailingExporter()
    failure = service.run(
        trigger="scheduled",
        now_utc=datetime(2026, 9, 20, 1, 0, tzinfo=timezone.utc),
    )

    assert failure.state == "FAILED"
    health = blobs.read_json("v1/control/health.json").value
    assert health["health"] == "unhealthy"
    assert health["last_attempt"]["state"] == "FAILED"
    assert health["error"] == "RuntimeError: backup operation failed"
    assert health["last_scheduled_success"] == before["last_scheduled_success"]
    assert health["latest_changed_archive"] == before["latest_changed_archive"]
    assert health["latest_changed_archive"]["run_id"] == success.run_id


def test_scheduled_no_change_is_success_without_replacing_latest_archive():
    blobs = BlobStore(FakeBlobContainer())
    service = AutomaticBackupService(
        ExportService(CosmosBackupCollector(populated_cosmos())),
        blobs,
        CFG,
    )
    uploaded = service.run(
        trigger="scheduled",
        now_utc=datetime(2026, 9, 19, 1, 0, tzinfo=timezone.utc),
    )
    no_change = service.run(
        trigger="scheduled",
        now_utc=datetime(2026, 9, 20, 1, 0, tzinfo=timezone.utc),
    )

    assert no_change.state == "NO_CHANGE"
    health = blobs.read_json("v1/control/health.json").value
    assert health["health"] == "healthy"
    assert health["last_attempt"]["state"] == "NO_CHANGE"
    assert health["last_scheduled_success"]["state"] == "NO_CHANGE"
    assert health["latest_changed_archive"]["run_id"] == uploaded.run_id
