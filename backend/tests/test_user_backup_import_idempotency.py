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
