import pytest

from src.backup.archive import BackupArchive
from src.backup.collectors import CosmosBackupCollector
from src.backup.export_service import ExportService
from src.backup.models import ExportRequest
from src.backup.section_schemas import (
    SchemaError,
    project_positions,
    scan_for_secrets,
)

from .user_backup_fakes import populated_cosmos


def test_export_has_fixed_sections_and_redacts_secrets():
    service = ExportService(CosmosBackupCollector(populated_cosmos()))
    request = ExportRequest()
    request.preview_fingerprint = service.preview(request).selection_fingerprint
    artifact = service.export(request)
    parsed = BackupArchive().read(artifact.archive)
    assert parsed.sections["ledger_movements"][0]["option_position_id"] == "pos_1"
    assert parsed.sections["option_positions"][0]["position_kind"] == "paper"
    assert parsed.sections["option_positions"][0]["source"]["activity_id"]
    assert parsed.sections["ledger_movements"][0]["company_name"] == "Apple"
    assert parsed.sections["ledger_movements"][0]["warnings"][0]["type"] == "PROBABLE_DUPLICATE"
    assert parsed.sections["securities"][0]["created_by_migration"] is True
    assert parsed.sections["securities"][0]["migrated_from"] == "LEGACY:AAPL"
    assert "migration_note" in parsed.sections["securities"][0]
    assert "pricing_cache" not in parsed.sections["symbol_configs"][0]
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


def _position_config(source):
    return {
        "security_id": "XNAS:AAPL",
        "symbol": "AAPL",
        "positions": [{
            "position_id": "pos-source",
            "type": "call",
            "source": source,
        }],
    }


def test_option_position_allows_opaque_activity_id_only_at_source_path():
    activity_id = "aB3dE5fG7hJ9kL2mN4pQ6rS8tU1vW3xY"

    projected = project_positions(
        _position_config({"activity_id": activity_id}),
        include_paper=True,
    )

    assert projected[0]["source"]["activity_id"] == activity_id


@pytest.mark.parametrize("payload", [
    {"metadata": {"activity_id": "aB3dE5fG7hJ9kL2mN4pQ6rS8tU1vW3xY"}},
    {"source_activity_id": "eyJhbGciOiJIUzI1NiJ9.abcdefgh12345678.signature123456"},
    {"source": {"source_activity_id": "aB3dE5fG7hJ9kL2mN4pQ6rS8tU1vW3xY"}},
    {"settings": {"activity_id": "Bearer abcDEFGH1234567890"}},
    {"ledger": {"source_activity_id": "zY9xW7vU5tS3rQ1pN8mL6kJ4hG2fE0dC"}},
    {"arbitrary": {"activity_id": "aB3dE5fG7hJ9kL2mN4pQ6rS8tU1vW3xY"}},
])
def test_activity_identifiers_outside_exact_provenance_path_are_rejected(payload):
    assert scan_for_secrets(payload)


@pytest.mark.parametrize("source", [
    {"activity_id": "eyJhbGciOiJIUzI1NiJ9.abcdefgh12345678.signature123456"},
    {
        "activity_id": "aB3dE5fG7hJ9kL2mN4pQ6rS8tU1vW3xY",
        "source_activity_id": "zY9xW7vU5tS3rQ1pN8mL6kJ4hG2fE0dC",
    },
    {
        "activity_id": "aB3dE5fG7hJ9kL2mN4pQ6rS8tU1vW3xY",
        "credential": "zY9xW7vU5tS3rQ1pN8mL6kJ4hG2fE0dC",
    },
])
def test_option_position_rejects_secret_shaped_adjacent_source_values(source):
    secret_value = next(reversed(source.values()))

    with pytest.raises(SchemaError) as exc_info:
        project_positions(_position_config(source), include_paper=True)

    error = exc_info.value
    assert error.issue == "secret_like_data"
    assert secret_value not in str(error)
    assert secret_value not in error.safe_detail()
    assert all(
        field.startswith("$.source.")
        for field in error.fields
    )
