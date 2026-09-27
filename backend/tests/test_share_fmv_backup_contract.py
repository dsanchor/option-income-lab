from copy import deepcopy

import pytest

from src.backup.archive import BackupArchive
from src.backup.collectors import CosmosBackupCollector
from src.backup.export_service import ExportService
from src.backup.import_service import ImportService
from src.backup.models import ExportRequest
from src.backup.section_schemas import SchemaError, validate_record
from tests.user_backup_fakes import FakeCosmos, populated_cosmos


FMV = {
    "valuation_date": "2026-09-25",
    "amount": "37.037034",
    "currency": "GBP",
    "eur_amount": "43.202959",
    "price_per_share": "12.345678",
    "price_per_share_eur": "14.400986",
    "source": "YAHOO_OPEN",
    "confidence": "MARKET_ESTIMATE",
    "fx": {"rate": "1.166480000", "date": "2026-09-25", "source": "ECB"},
    "provenance": {
        "provider": "yfinance",
        "provider_symbol": "ULVR.L",
        "price_field": "OPEN",
        "requested_date": "2026-09-25",
        "market_session_date": "2026-09-25",
        "fetched_at": "2026-09-27T07:00:00Z",
        "script_version": "dividend-buy-fmv-v1",
        "run_id": "c63f240e-15e1-4fc7-a372-645551d86534",
    },
}


def _export(cosmos):
    exporter = ExportService(CosmosBackupCollector(cosmos))
    request = ExportRequest()
    request.preview_fingerprint = exporter.preview(request).selection_fingerprint
    return exporter.export(request)


def _movement(identifier, status):
    return {
        "id": identifier,
        "account_id": "acct_demo",
        "doc_type": "ledger_txn",
        "txn_type": "BUY",
        "security_id": "XNAS:AAPL",
        "ticker": "AAPL",
        "trade_date": "2026-09-25",
        "quantity": "3",
        "gross": {"amount": "10", "currency": "EUR", "eur_amount": "10"},
        "fees": {"total": "2", "currency": "EUR", "total_eur": "2"},
        "net": {"amount": "12", "currency": "EUR", "eur_amount": "12"},
        "cost_basis_status": "COMPLETE",
        "correction_status": status,
        "ca_group_id": "cag_fmv",
        "ca_group_seq": 1,
        "ca_event_type": "SCRIP_DIVIDEND",
        "ca_leg_type": "SHARE_ACQUISITION",
        "share_fmv": deepcopy(FMV),
    }


def test_backup_round_trip_preserves_active_and_superseded_fmv_and_controls():
    source = populated_cosmos()
    source.portfolio_container.create_item(body=_movement("fmv-active", "ACTIVE"))
    superseded = _movement("fmv-old", "SUPERSEDED")
    superseded["superseded_by"] = "fmv-active"
    source.portfolio_container.create_item(body=superseded)

    exported = _export(source)
    parsed = BackupArchive().read(exported.archive)
    records = {
        row["id"]: row for row in parsed.sections["ledger_movements"]
    }
    assert records["fmv-active"]["share_fmv"] == FMV
    assert records["fmv-old"]["share_fmv"] == FMV

    target = FakeCosmos()
    importer = ImportService(target)
    plan = importer.dry_run(exported.archive)
    assert plan.valid, plan.errors
    result = importer.apply(
        exported.archive,
        dry_run_fingerprint=plan.dry_run_fingerprint,
        confirm=True,
    )
    assert result.status == "COMPLETED"
    restored = BackupArchive().read(_export(target).archive)
    assert restored.sections == parsed.sections
    assert _export(target).manifest["controls"] == exported.manifest["controls"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda row: row["share_fmv"].update(amount="0"),
        lambda row: row["share_fmv"].update(valuation_date="2026-09-24"),
        lambda row: row.update(ca_leg_type="CASH_TOP_UP"),
        lambda row: row.update(ca_event_type="SHARE_CONSOLIDATION"),
    ],
)
def test_backup_validation_rejects_invalid_or_ineligible_fmv(mutate):
    row = _movement("bad", "ACTIVE")
    mutate(row)
    with pytest.raises(SchemaError):
        validate_record("ledger_movements", row)


def test_backup_validation_accepts_explicit_null_as_absent_fmv():
    row = _movement("null-fmv", "ACTIVE")
    row["share_fmv"] = None
    validate_record("ledger_movements", row)
