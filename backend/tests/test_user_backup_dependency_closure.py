from src.backup.archive import BackupArchive
from src.backup.collectors import CosmosBackupCollector
from src.backup.dependency_closure import (
    close_dependencies,
    validate_dependency_closure,
)
from src.backup.export_service import ExportService
from src.backup.import_service import ImportService
from src.backup.models import ExportRequest

from .user_backup_fakes import FakeCosmos, populated_cosmos


def _cosmos_with_account_reference(account_id):
    cosmos = populated_cosmos()
    cosmos.portfolio_container.store.pop(("acct_demo", "acct_demo"))
    movement = cosmos.portfolio_container.store.pop(("acct_demo", "mvt_1"))
    movement["account_id"] = account_id
    cosmos.portfolio_container.create_item(body=movement)
    return cosmos


def test_movement_closure_adds_account_security_config_and_position():
    service = ExportService(CosmosBackupCollector(populated_cosmos()))
    all_records = service._project_all(ExportRequest())
    selected = {name: [] for name in all_records}
    selected["ledger_movements"] = all_records["ledger_movements"]
    closed, additions, warnings = close_dependencies(all_records, selected)
    assert len(closed["accounts"]) == len(closed["securities"]) == 1
    assert len(closed["option_positions"]) == 1
    assert warnings == []
    assert additions["accounts"] == 1


def test_unassigned_movement_exports_and_imports_without_synthetic_account():
    service = ExportService(
        CosmosBackupCollector(_cosmos_with_account_reference("_unassigned"))
    )
    request = ExportRequest()
    preview = service.preview(request)

    assert not any(
        warning.startswith("BROKEN_ACCOUNT_REFERENCE:")
        for warning in preview.warnings
    )

    request.preview_fingerprint = preview.selection_fingerprint
    artifact = service.export(request)
    parsed = BackupArchive().read(artifact.archive)
    assert parsed.sections["accounts"] == []
    assert parsed.sections["ledger_movements"][0]["account_id"] == "_unassigned"

    importer = ImportService(FakeCosmos())
    validation = importer.validate(artifact.archive)
    plan = importer.dry_run(artifact.archive)
    assert validation.valid
    assert validation.dependency_errors == []
    assert plan.valid
    assert not any("missing_account" in error for error in plan.errors)

    result = importer.apply(
        artifact.archive,
        dry_run_fingerprint=plan.dry_run_fingerprint,
        confirm=True,
    )
    assert result.status == "COMPLETED"


def test_unknown_account_still_warns_and_blocks_import():
    service = ExportService(
        CosmosBackupCollector(_cosmos_with_account_reference("missing-account"))
    )
    request = ExportRequest()
    preview = service.preview(request)

    assert any(
        warning.endswith(":missing-account")
        and warning.startswith("BROKEN_ACCOUNT_REFERENCE:")
        for warning in preview.warnings
    )

    request.preview_fingerprint = preview.selection_fingerprint
    artifact = service.export(request)
    importer = ImportService(FakeCosmos())
    validation = importer.validate(artifact.archive)
    plan = importer.dry_run(artifact.archive)
    assert not validation.valid
    assert any("missing_account" in error for error in validation.dependency_errors)
    assert not plan.valid
    assert any("missing_account" in error for error in plan.errors)


def test_closure_follows_replacement_groups_and_reverse_correction_descendants():
    all_records = {
        "accounts": [
            {"id": "a", "account_id": "a", "doc_type": "account"},
        ],
        "securities": [
            {"id": "s", "security_id": "XNAS:A", "symbol": "A",
             "doc_type": "security_master"},
        ],
        "symbol_configs": [], "option_positions": [], "action_plans": [],
        "app_settings": [],
        "ledger_movements": [
            {"id": "old", "account_id": "a", "security_id": "XNAS:A",
             "doc_type": "ledger_txn", "ca_group_id": "ca-old",
             "replaced_by_ca_group_id": "ca-new"},
            {"id": "new", "account_id": "a", "security_id": "XNAS:A",
             "doc_type": "ledger_txn", "ca_group_id": "ca-new",
             "replaces_ca_group_id": "ca-old"},
            {"id": "correction", "account_id": "a", "security_id": "XNAS:A",
             "doc_type": "ledger_txn", "corrects_movement_id": "new"},
        ],
    }
    selected = {section: [] for section in all_records}
    selected["ledger_movements"] = [all_records["ledger_movements"][0]]
    closed, _, warnings = close_dependencies(all_records, selected)
    assert {item["id"] for item in closed["ledger_movements"]} == {
        "old", "new", "correction",
    }
    assert warnings == []


def test_import_validation_rejects_incomplete_transfer_and_missing_ca_replacement():
    records = {
        "accounts": [{"account_id": "a"}],
        "securities": [{"security_id": "XNAS:A"}],
        "symbol_configs": [], "option_positions": [], "action_plans": [],
        "app_settings": [],
        "ledger_movements": [{
            "id": "one", "account_id": "a", "security_id": "XNAS:A",
            "transfer_pair_id": "pair-1", "ca_group_id": "old",
            "superseded_by_ca_group_id": "missing",
        }],
    }
    errors = validate_dependency_closure(records)
    assert any("incomplete_group" in error for error in errors)
    assert any("missing_superseded_by_ca_group_id" in error for error in errors)


def _dependency_records(*movements):
    return {
        "accounts": [
            {"account_id": "source"},
            {"account_id": "destination"},
        ],
        "securities": [{"security_id": "XNAS:A"}],
        "symbol_configs": [], "option_positions": [], "action_plans": [],
        "app_settings": [], "ledger_movements": list(movements),
    }


def _transfer_leg(
    movement_id, txn_type, account_id, peer_id, *,
    source="source", destination="destination",
):
    return {
        "id": movement_id,
        "account_id": account_id,
        "security_id": "XNAS:A",
        "txn_type": txn_type,
        "transfer_group_id": "transfer-1",
        "transfer_peer_id": peer_id,
        "transfer_source_account_id": source,
        "transfer_dest_account_id": destination,
    }


def test_import_validation_rejects_two_transfer_in_legs():
    records = _dependency_records(
        _transfer_leg("in-1", "TRANSFER_IN", "destination", "in-2"),
        _transfer_leg("in-2", "TRANSFER_IN", "destination", "in-1"),
    )

    errors = validate_dependency_closure(records)

    assert "transfer_group_id:transfer-1:invalid_directions" in errors


def test_import_validation_rejects_nonreciprocal_or_same_account_transfer():
    records = _dependency_records(
        _transfer_leg(
            "out", "TRANSFER_OUT", "source", "missing",
            source="source", destination="source",
        ),
        _transfer_leg(
            "in", "TRANSFER_IN", "source", "out",
            source="source", destination="source",
        ),
    )

    errors = validate_dependency_closure(records)

    assert "transfer_group_id:transfer-1:nonreciprocal_peers" in errors
    assert "transfer_group_id:transfer-1:invalid_account_relationship" in errors


def test_import_validation_accepts_production_transfer_pair():
    records = _dependency_records(
        _transfer_leg("out", "TRANSFER_OUT", "source", "in"),
        _transfer_leg("in", "TRANSFER_IN", "destination", "out"),
    )

    assert validate_dependency_closure(records) == []


def test_import_validation_rejects_transfer_leg_without_group():
    movement = _transfer_leg("out", "TRANSFER_OUT", "source", "in")
    movement.pop("transfer_group_id")

    errors = validate_dependency_closure(_dependency_records(movement))

    assert any("missing_transfer_group" in error for error in errors)


def _consolidation_leg(movement_id, leg_type, txn_type):
    return {
        "id": movement_id,
        "account_id": "source",
        "security_id": "XNAS:A",
        "txn_type": txn_type,
        "ca_group_id": "ca-consolidation",
        "ca_event_type": "SHARE_CONSOLIDATION",
        "ca_leg_type": leg_type,
    }


def test_import_validation_accepts_production_share_consolidation_group():
    records = _dependency_records(
        _consolidation_leg(
            "consolidation-out", "CONSOLIDATION_OUT", "TRANSFER_OUT",
        ),
        _consolidation_leg(
            "consolidation-in", "CONSOLIDATION_IN", "TRANSFER_IN",
        ),
    )

    assert validate_dependency_closure(records) == []


def test_import_validation_rejects_malformed_share_consolidation_groups():
    missing_leg = _dependency_records(
        _consolidation_leg(
            "consolidation-out", "CONSOLIDATION_OUT", "TRANSFER_OUT",
        ),
    )
    wrong_mapping = _dependency_records(
        _consolidation_leg(
            "consolidation-out", "CONSOLIDATION_OUT", "TRANSFER_IN",
        ),
        _consolidation_leg(
            "consolidation-in", "CONSOLIDATION_IN", "TRANSFER_OUT",
        ),
    )

    missing_errors = validate_dependency_closure(missing_leg)
    mapping_errors = validate_dependency_closure(wrong_mapping)

    assert (
        "ca_group_id:ca-consolidation:missing_required_legs:CONSOLIDATION_IN"
        in missing_errors
    )
    assert any(
        "CONSOLIDATION_OUT:TRANSFER_IN:TRANSFER_OUT" in error
        for error in mapping_errors
    )
    assert any(
        "CONSOLIDATION_IN:TRANSFER_OUT:TRANSFER_IN" in error
        for error in mapping_errors
    )


def test_import_validation_rejects_scrip_group_missing_share_acquisition():
    records = _dependency_records({
        "id": "dividend",
        "account_id": "source",
        "security_id": "XNAS:A",
        "txn_type": "DIVIDEND",
        "ca_group_id": "ca-scrip",
        "ca_event_type": "DIVIDEND_WITH_SCRIP",
        "ca_leg_type": "CASH_DIVIDEND",
    })

    errors = validate_dependency_closure(records)

    assert (
        "ca_group_id:ca-scrip:missing_required_legs:SHARE_ACQUISITION"
        in errors
    )


def test_import_validation_rejects_wrong_transaction_type_for_ca_leg():
    records = _dependency_records(
        {
            "id": "dividend",
            "account_id": "source",
            "security_id": "XNAS:A",
            "txn_type": "BUY",
            "ca_group_id": "ca-scrip",
            "ca_event_type": "DIVIDEND_WITH_SCRIP",
            "ca_leg_type": "CASH_DIVIDEND",
        },
        {
            "id": "shares",
            "account_id": "source",
            "security_id": "XNAS:A",
            "txn_type": "DIVIDEND",
            "ca_group_id": "ca-scrip",
            "ca_event_type": "DIVIDEND_WITH_SCRIP",
            "ca_leg_type": "SHARE_ACQUISITION",
        },
    )

    errors = validate_dependency_closure(records)

    assert any("CASH_DIVIDEND:BUY:DIVIDEND" in error for error in errors)
    assert any("SHARE_ACQUISITION:DIVIDEND:BUY" in error for error in errors)
