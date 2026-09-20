from datetime import datetime, timezone

from src.backup.automatic_backup import AutomaticBackupService
from src.backup.blob_store import BlobStore
from src.backup.collectors import CosmosBackupCollector
from src.backup.export_service import ExportService

from .user_backup_fakes import FakeBlobContainer, populated_cosmos


def test_changed_upload_is_verified_before_latest_and_unchanged_is_skipped():
    container = FakeBlobContainer()
    blobs = BlobStore(container)
    service = AutomaticBackupService(
        ExportService(CosmosBackupCollector(populated_cosmos())), blobs
    )
    now = datetime(2026, 9, 19, 1, 0, tzinfo=timezone.utc)
    first = service.run(trigger="scheduled", now_utc=now)
    assert first.state == "UPLOADED"
    latest = blobs.read_json("v1/control/latest.json").value
    assert latest["archive_sha256"] == first.archive_sha256
    second = service.run(trigger="scheduled", now_utc=now)
    assert second.state == "UPLOADED"
    assert second.run_id == first.run_id
    zip_paths = [path for path in container.blobs if path.endswith(".zip")]
    assert len(zip_paths) == 1


def test_manual_only_if_changed_records_no_change_without_new_zip():
    container = FakeBlobContainer()
    service = AutomaticBackupService(
        ExportService(CosmosBackupCollector(populated_cosmos())),
        BlobStore(container),
    )
    now = datetime(2026, 9, 19, 1, 0, tzinfo=timezone.utc)
    assert service.run(trigger="manual", now_utc=now).state == "UPLOADED"
    assert service.run(
        trigger="manual", now_utc=now, only_if_changed=True
    ).state == "NO_CHANGE"
    assert len([path for path in container.blobs if path.endswith(".zip")]) == 1


def test_lease_blocks_concurrent_holder():
    import pytest

    from src.backup.blob_store import BlobLeaseBusyError

    blobs = BlobStore(FakeBlobContainer())
    with blobs.lease(), pytest.raises(BlobLeaseBusyError), blobs.lease():
        pass


def test_missing_latest_is_recovered_from_verified_run_and_blob():
    container = FakeBlobContainer()
    blobs = BlobStore(container)
    service = AutomaticBackupService(
        ExportService(CosmosBackupCollector(populated_cosmos())), blobs
    )
    now = datetime(2026, 9, 19, 1, 0, tzinfo=timezone.utc)
    first = service.run(trigger="manual", now_utc=now)
    del container.blobs["v1/control/latest.json"]
    recovered = blobs.recover_latest()
    assert recovered is not None
    assert recovered.value["run_id"] == first.run_id
    assert recovered.value["blob_path"] == first.blob_path


def test_stale_broken_latest_is_recovered_even_with_newer_fake_timestamp():
    container = FakeBlobContainer()
    blobs = BlobStore(container)
    service = AutomaticBackupService(
        ExportService(CosmosBackupCollector(populated_cosmos())), blobs
    )
    first = service.run(
        trigger="manual", now_utc=datetime(2026, 9, 19, 1, 0, tzinfo=timezone.utc)
    )
    blobs.write_json("v1/control/latest.json", {
        "run_id": "broken", "blob_path": "v1/daily/missing.zip",
        "archive_sha256": "0" * 64, "completed_at_utc": "2099-01-01T00:00:00Z",
    })
    recovered = blobs.recover_latest()
    assert recovered is not None
    assert recovered.value["run_id"] == first.run_id


def test_monthly_anchor_reconciliation_updates_retention_tags():
    cosmos = populated_cosmos()
    container = FakeBlobContainer()
    blobs = BlobStore(container)
    service = AutomaticBackupService(
        ExportService(CosmosBackupCollector(cosmos)), blobs
    )
    first = service.run(
        trigger="manual", now_utc=datetime(2026, 9, 19, 1, 0, tzinfo=timezone.utc)
    )
    cosmos.portfolio_container.store[("acct_demo", "mvt_1")]["notes"] = "changed once"
    second = service.run(
        trigger="manual", now_utc=datetime(2026, 9, 20, 1, 0, tzinfo=timezone.utc)
    )
    assert blobs.read_json("v1/monthly/2026-09.json").value["run_id"] == second.run_id
    assert container.blobs[first.blob_path]["tags"]["retentionClass"] == "daily"
    assert container.blobs[second.blob_path]["tags"]["retentionClass"] == "monthly"
    assert container.blobs[second.blob_path]["tags"]["yearMonth"] == "2026-09"


def test_monthly_anchor_reconciliation_keeps_twelve_live_anchors():
    container = FakeBlobContainer()
    blobs = BlobStore(container)
    service = AutomaticBackupService(
        ExportService(CosmosBackupCollector(populated_cosmos())), blobs
    )
    first = service.run(
        trigger="manual", now_utc=datetime(2025, 1, 15, 1, 0, tzinfo=timezone.utc)
    )
    latest = blobs.read_json("v1/control/latest.json").value
    for month in range(1, 14):
        year = 2025 + (month - 1) // 12
        month_number = (month - 1) % 12 + 1
        blobs.write_json(
            f"v1/monthly/{year:04d}-{month_number:02d}.json",
            {**latest, "local_date": f"{year:04d}-{month_number:02d}-15"},
        )
    anchors = blobs.reconcile_monthly_anchors(keep_months=12)
    assert len(anchors) == 12
    assert "2025-01" not in anchors
    assert blobs.read_json("v1/monthly/2025-01.json") is None
    assert container.blobs[first.blob_path]["tags"]["retentionClass"] == "monthly"
