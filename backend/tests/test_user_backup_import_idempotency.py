from src.backup.collectors import CosmosBackupCollector
from src.backup.export_service import ExportService
from src.backup.import_service import ImportService
from src.backup.models import ExportRequest

from .user_backup_fakes import FakeCosmos, populated_cosmos


def _artifact():
    exporter = ExportService(CosmosBackupCollector(populated_cosmos()))
    request = ExportRequest()
    request.preview_fingerprint = exporter.preview(request).selection_fingerprint
    return exporter.export(request).archive


def test_create_only_import_is_idempotent():
    archive = _artifact()
    target = FakeCosmos()
    importer = ImportService(target)
    plan = importer.dry_run(archive)
    assert plan.valid
    result = importer.apply(
        archive, dry_run_fingerprint=plan.dry_run_fingerprint, confirm=True
    )
    assert result.status == "COMPLETED"
    second = importer.dry_run(archive)
    assert second.valid
    assert {item.status for item in second.records} == {"SKIP_IDENTICAL"}


def test_differing_existing_record_blocks_whole_apply():
    archive = _artifact()
    target = FakeCosmos()
    target.portfolio_container.create_item(body={
        "id": "acct_demo", "account_id": "acct_demo", "doc_type": "account",
        "broker": "Other", "name": "Conflict", "currency": "EUR",
    })
    plan = ImportService(target).dry_run(archive)
    assert not plan.valid
    assert "CONFLICT_REQUIRES_CHOICE" in {item.status for item in plan.records}


def test_skip_conflicts_preserves_destination_and_creates_missing_records():
    archive = _artifact()
    target = FakeCosmos()
    target.portfolio_container.create_item(body={
        "id": "acct_demo", "account_id": "acct_demo", "doc_type": "account",
        "broker": "Other", "name": "Destination account", "currency": "EUR",
    })
    importer = ImportService(target)

    plan = importer.dry_run(archive, skip_existing_conflicts=True)

    assert plan.valid
    account = next(
        item for item in plan.records
        if item.section == "accounts" and item.logical_key == "acct_demo"
    )
    assert account.status == "SKIP_CONFLICT"
    result = importer.apply(
        archive,
        dry_run_fingerprint=plan.dry_run_fingerprint,
        confirm=True,
        skip_existing_conflicts=True,
    )

    assert result.status == "COMPLETED"
    preserved = target.portfolio_container.read_item(
        item="acct_demo",
        partition_key="acct_demo",
    )
    assert preserved["name"] == "Destination account"
    assert result.skipped["accounts"] == 1
    assert target.portfolio_container.read_item(
        item="mvt_1",
        partition_key="acct_demo",
    )["txn_type"] == "CALL_SELL"


def test_skip_conflicts_does_not_bypass_cross_security_identity_collision():
    source = populated_cosmos()
    source_security = source.container.read_item(
        item="sec_XNAS_AAPL",
        partition_key="AAPL",
    )
    source_security["isin"] = "US0378331005"
    source.container.replace_item(
        item="sec_XNAS_AAPL",
        body=source_security,
    )
    exporter = ExportService(CosmosBackupCollector(source))
    request = ExportRequest()
    request.preview_fingerprint = exporter.preview(request).selection_fingerprint
    archive = exporter.export(request).archive
    target = FakeCosmos()
    target.container.create_item(body={
        "id": "sec_XNYS_OTHER",
        "doc_type": "security_master",
        "security_id": "XNYS:OTHER",
        "symbol": "OTHER",
        "ticker": "OTHER",
        "isin": "US0378331005",
    })

    plan = ImportService(target).dry_run(
        archive,
        skip_existing_conflicts=True,
    )

    assert not plan.valid
    security = next(
        item for item in plan.records
        if item.section == "securities" and item.logical_key == "XNAS:AAPL"
    )
    assert security.status == "CONFLICT_REQUIRES_CHOICE"
