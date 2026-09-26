import pytest

from src.backup.archive import BackupArchive
from src.backup.collectors import CosmosBackupCollector
from src.backup.export_service import ExportService
from src.backup.import_service import ImportService
from src.backup.models import ExportRequest
from src.backup.section_schemas import SchemaError, project_ledger, validate_record

from .user_backup_fakes import FakeCosmos, populated_cosmos


def _export(cosmos):
    exporter = ExportService(CosmosBackupCollector(cosmos))
    request = ExportRequest()
    request.preview_fingerprint = exporter.preview(request).selection_fingerprint
    return exporter.export(request)


def test_full_export_import_reexport_preserves_canonical_content():
    original = _export(populated_cosmos())
    target = FakeCosmos()
    importer = ImportService(target)
    plan = importer.dry_run(original.archive)
    result = importer.apply(
        original.archive, dry_run_fingerprint=plan.dry_run_fingerprint, confirm=True
    )
    assert result.status == "COMPLETED"
    restored = _export(target)
    left = BackupArchive().read(original.archive).sections
    right = BackupArchive().read(restored.archive).sections
    assert left == right


def test_mid_import_failure_rolls_back_created_user_data():
    original = _export(populated_cosmos())
    target = FakeCosmos()
    target.portfolio_container.fail_create_id = "mvt_1"
    importer = ImportService(target)
    plan = importer.dry_run(original.archive)
    result = importer.apply(
        original.archive, dry_run_fingerprint=plan.dry_run_fingerprint, confirm=True
    )
    assert result.status == "ROLLED_BACK"
    assert target.portfolio_container.store == {}
    assert target.container.store == {}


def test_compensation_failure_is_partial_requires_attention():
    original = _export(populated_cosmos())
    target = FakeCosmos()
    target.portfolio_container.fail_create_id = "mvt_1"
    original_delete = target.container.delete_item

    def fail_delete(*args, **kwargs):
        if kwargs.get("item") == "config_AAPL":
            raise RuntimeError("injected compensation failure")
        return original_delete(*args, **kwargs)

    target.container.delete_item = fail_delete
    importer = ImportService(target)
    plan = importer.dry_run(original.archive)
    result = importer.apply(
        original.archive, dry_run_fingerprint=plan.dry_run_fingerprint, confirm=True
    )
    assert result.status == "PARTIAL_REQUIRES_ATTENTION"
    assert result.compensation_errors


def test_create_then_journal_transition_failure_recovers_ambiguous_window():
    original = _export(populated_cosmos())
    target = FakeCosmos()
    target.import_sessions_container.fail_replace_at = 3
    importer = ImportService(target)
    plan = importer.dry_run(original.archive)
    result = importer.apply(
        original.archive, dry_run_fingerprint=plan.dry_run_fingerprint, confirm=True
    )
    assert result.status == "ROLLED_BACK"
    assert target.portfolio_container.store == {}
    assert target.container.store == {}
    journal = target.import_sessions_container.read_item(
        item=result.import_run_id, partition_key=result.import_run_id
    )
    assert any(item["state"] == "AMBIGUOUS" for item in journal["planned_items"])


def test_planned_inventory_failure_happens_before_any_user_write():
    original = _export(populated_cosmos())
    target = FakeCosmos()
    target.import_sessions_container.fail_replace_at = 2
    importer = ImportService(target)
    plan = importer.dry_run(original.archive)
    result = importer.apply(
        original.archive, dry_run_fingerprint=plan.dry_run_fingerprint, confirm=True
    )
    assert result.status == "ROLLED_BACK"
    assert target.portfolio_container.store == {}
    assert target.container.store == {}


def test_round_trip_controls_cover_fifo_dividends_options_fx_links_and_history():
    source = populated_cosmos()
    for movement in [
        {
            "id": "buy", "account_id": "acct_demo", "doc_type": "ledger_txn",
            "txn_type": "BUY", "security_id": "XNAS:AAPL", "ticker": "AAPL",
            "trade_date": "2026-01-01", "quantity": "10",
            "gross": {"amount": "1000", "currency": "EUR", "eur_amount": "1000"},
            "fees": {"total": "10", "currency": "EUR", "total_eur": "10"},
            "net": {"amount": "1010", "currency": "EUR", "eur_amount": "1010"},
            "fx": {"rate": "1", "rate_source": "BROKER"},
            "cost_basis_status": "COMPLETE", "correction_status": "ACTIVE",
        },
        {
            "id": "sell", "account_id": "acct_demo", "doc_type": "ledger_txn",
            "txn_type": "SELL", "security_id": "XNAS:AAPL", "ticker": "AAPL",
            "trade_date": "2026-02-01", "quantity": "4",
            "gross": {"amount": "500", "currency": "EUR", "eur_amount": "500"},
            "fees": {"total": "5", "currency": "EUR", "total_eur": "5"},
            "net": {"amount": "495", "currency": "EUR", "eur_amount": "495"},
            "fx": {"rate": "1", "rate_source": "BROKER"},
            "correction_status": "ACTIVE", "paired_movement_id": "buy",
        },
        {
            "id": "div", "account_id": "acct_demo", "doc_type": "ledger_txn",
            "txn_type": "DIVIDEND", "security_id": "XNAS:AAPL", "ticker": "AAPL",
            "trade_date": "2026-03-01", "quantity": "0",
            "gross": {"amount": "50", "currency": "EUR", "eur_amount": "50"},
            "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
            "withholding": {
                "source": {"amount_eur": "10"}, "destination": None,
            },
            "net": {"amount": "40", "currency": "EUR", "eur_amount": "40"},
            "fx": {"rate": "1", "rate_source": "BROKER"},
            "correction_status": "ACTIVE",
        },
        {
            "id": "inactive", "account_id": "acct_demo", "doc_type": "ledger_txn",
            "txn_type": "DIVIDEND", "security_id": "XNAS:AAPL", "ticker": "AAPL",
            "trade_date": "2025-03-01", "quantity": "0",
            "gross": {"amount": "999", "currency": "EUR", "eur_amount": "999"},
            "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
            "net": {"amount": "999", "currency": "EUR", "eur_amount": "999"},
            "correction_status": "SUPERSEDED", "superseded_by": "div",
        },
    ]:
        source.portfolio_container.create_item(body=movement)
    original = _export(source)
    controls = original.manifest["controls"]
    holding = controls["holdings"]["holdings"][0]
    assert holding["total_shares"] == "6.000000"
    assert holding["remaining_cost_basis_eur"] == "606.00"
    assert holding["realized_result_eur"] == "91.00"
    assert controls["dividends"]["summary"]["total_net"] == "40"
    assert controls["option_economics"][0]["net_eur"] == "89.1"
    assert controls["audit_history"]["states"]["SUPERSEDED"] == 1
    assert any(item["withholding"] for item in controls["fx_withholding"])

    target = FakeCosmos()
    importer = ImportService(target)
    plan = importer.dry_run(original.archive)
    result = importer.apply(
        original.archive, dry_run_fingerprint=plan.dry_run_fingerprint, confirm=True
    )
    assert result.status == "COMPLETED"
    assert _export(target).manifest["controls"] == controls


def test_backup_export_rejects_legacy_rights_movement():
    movement = {
        "id": "legacy-rights",
        "account_id": "acct_demo",
        "doc_type": "ledger_txn",
        "txn_type": "SELL",
        "security_id": "XNAS:AAPL",
        "sales_type": "DERECHOS",
    }
    with pytest.raises(SchemaError, match="no longer supported"):
        project_ledger(movement, include_source_row=False)


def test_backup_restore_validation_rejects_legacy_rights_movement():
    movement = {
        "id": "legacy-rights",
        "account_id": "acct_demo",
        "doc_type": "ledger_txn",
        "txn_type": "SELL",
        "security_id": "XNAS:AAPL",
        "sales_type": "DERECHOS",
    }
    with pytest.raises(SchemaError, match="no longer supported"):
        validate_record("ledger_movements", movement)


@pytest.mark.parametrize(
    "rights_data",
    [
        {"SOURCE_DERECHOS_AMOUNT": "1"},
        {"source_derechos_amount": "not-a-number"},
        {"source_derechos_amount": "NaN"},
        {"source_payload": {"nested": {"Rights Amount": "2"}}},
        {"source_row": {"Importe en Derechos": "Infinity"}},
        {"sales_type": " derechos "},
        {"sales_type_raw": "rights sold"},
        {"sales_type": "unknown-legacy-value"},
    ],
)
def test_backup_restore_rejects_all_rights_aliases_and_malformed_values(rights_data):
    movement = {
        "id": "legacy-rights",
        "account_id": "acct_demo",
        "doc_type": "ledger_txn",
        "txn_type": "DIVIDEND",
        "security_id": "XNAS:AAPL",
        **rights_data,
    }
    with pytest.raises(SchemaError, match="no longer supported"):
        validate_record("ledger_movements", movement)


def test_backup_export_strips_safe_obsolete_stock_metadata():
    movement = {
        "id": "ordinary-sale",
        "account_id": "acct_demo",
        "doc_type": "ledger_txn",
        "txn_type": "SELL",
        "security_id": "XNAS:AAPL",
        "sales_type": "ACCIONES",
        "is_rights_sale": False,
        "source_derechos_amount": "0",
        "source_row": {
            "Rights Amount": "0.00",
            "Broker Reference": "abc",
        },
    }

    projected = project_ledger(movement, include_source_row=True)

    assert "sales_type" not in projected
    assert "is_rights_sale" not in projected
    assert "source_derechos_amount" not in projected
    assert projected["source_row"] == {"Broker Reference": "abc"}


def test_import_recomputes_and_rejects_tampered_manifest_controls():
    original = _export(populated_cosmos())
    parsed = BackupArchive().read(original.archive)
    manifest = dict(parsed.manifest)
    manifest["controls"] = dict(manifest["controls"])
    manifest["controls"]["audit_history"] = {"count": 999}
    tampered = BackupArchive().build(manifest, parsed.sections)
    plan = ImportService(FakeCosmos()).dry_run(tampered)
    assert not plan.valid
    assert any("round-trip controls" in error for error in plan.errors)
