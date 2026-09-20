from src.backup.archive import BackupArchive
from src.backup.collectors import CosmosBackupCollector
from src.backup.export_service import ExportService
from src.backup.models import ExportRequest
from src.backup.section_schemas import scan_for_secrets

from .user_backup_fakes import populated_cosmos


def test_export_has_fixed_sections_and_redacts_secrets():
    service = ExportService(CosmosBackupCollector(populated_cosmos()))
    request = ExportRequest()
    request.preview_fingerprint = service.preview(request).selection_fingerprint
    artifact = service.export(request)
    parsed = BackupArchive().read(artifact.archive)
    assert parsed.sections["ledger_movements"][0]["option_position_id"] == "pos_1"
    assert parsed.sections["option_positions"][0]["position_kind"] == "paper"
    assert parsed.sections["app_settings"][0]["path"] == "agent_trace"
    assert b"must-not-export" not in artifact.archive


def test_query_order_does_not_change_content_hash():
    cosmos = populated_cosmos()
    service = ExportService(CosmosBackupCollector(cosmos))
    request = ExportRequest()
    request.preview_fingerprint = service.preview(request).selection_fingerprint
    first = service.export(request)
    cosmos.container.store = dict(reversed(list(cosmos.container.store.items())))
    request.preview_fingerprint = service.preview(request).selection_fingerprint
    second = service.export(request)
    assert first.manifest["content_sha256"] == second.manifest["content_sha256"]


def test_recursive_secret_scanner_checks_arrays_and_serialized_json():
    findings = scan_for_secrets({
        "items": [{"safe": '{"nested_api_key":"redacted-canary"}'}],
    })
    assert findings


def test_secret_scanner_fails_closed_for_malformed_serialized_json():
    assert scan_for_secrets({"notes": '{"nested":"unterminated"'})


def test_secret_scanner_rejects_jwt_and_opaque_tokens_but_not_financial_ids():
    assert scan_for_secrets({
        "notes": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature123"
    })
    assert scan_for_secrets({"notes": "aB3dE5fG7hJ9kL2mN4pQ6rS8tU1vW3xY"})
    assert not scan_for_secrets({
        "security_id": "XNAS:AAPL",
        "isin": "US0378331005",
        "cusip": "037833100",
        "idempotency_hash": "aB3dE5fG7hJ9kL2mN4pQ6rS8tU1vW3xY",
    })
