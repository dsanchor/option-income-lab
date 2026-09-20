from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

SECTION_NAMES = (
    "accounts",
    "securities",
    "symbol_configs",
    "option_positions",
    "ledger_movements",
    "action_plans",
    "app_settings",
)


class ExportFilters(BaseModel):
    account_ids: list[str] = Field(default_factory=list)
    security_ids: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    date_from: str | None = None
    date_to: str | None = None


class ExportRequest(BaseModel):
    preset: Literal[
        "recommended_full_user_backup", "portfolio_only",
        "symbols_and_configuration", "custom",
    ] = "recommended_full_user_backup"
    sections: list[str] | None = None
    filters: ExportFilters = Field(default_factory=ExportFilters)
    include_paper_positions: bool = True
    include_source_row: bool = True
    preview_fingerprint: str | None = None


class SectionDescriptor(BaseModel):
    name: str
    path: str
    schema_version: int = 1
    count: int
    sha256: str


class BackupManifest(BaseModel):
    format: str = "option-income-lab-user-backup"
    archive_version: int = 1
    schema_version: int = 1
    export_id: str = Field(default_factory=lambda: str(uuid4()))
    exported_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    app: dict[str, Any] = Field(default_factory=dict)
    scope: dict[str, Any]
    sections: list[SectionDescriptor]
    relationships: dict[str, Any] = Field(
        default_factory=lambda: {"identity": "security_id", "preserve_ids": True}
    )
    redactions: dict[str, Any]
    content_sha256: str
    archive_payload_sha256: str
    requested_sections: list[str]
    effective_sections: list[str]
    dependency_additions: dict[str, int] = Field(default_factory=dict)
    controls: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    include_paper_positions: bool = True
    include_source_row: bool = True


class ExportPreview(BaseModel):
    requested_sections: list[str]
    effective_sections: list[str]
    added_dependencies: dict[str, int]
    counts: dict[str, int]
    warnings: list[str]
    exclusions: list[str]
    selection_fingerprint: str


class RecordPlan(BaseModel):
    section: str
    logical_key: str
    status: Literal[
        "CREATE", "SKIP_IDENTICAL", "CONFLICT_REQUIRES_CHOICE",
        "BLOCKED_MISSING_REFERENCE", "BLOCKED_INVARIANT", "REDACTED_IGNORED",
    ]
    detail: str | None = None


class ValidationReport(BaseModel):
    valid: bool
    archive_sha256: str
    manifest_summary: dict[str, Any] = Field(default_factory=dict)
    compatible: bool = True
    section_counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    dependency_errors: list[str] = Field(default_factory=list)
    collisions: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class DryRunPlan(BaseModel):
    valid: bool
    dry_run_fingerprint: str
    destination_snapshot_digest: str
    archive_sha256: str
    records: list[RecordPlan]
    ordering: list[str]
    predicted_controls: dict[str, Any] = Field(default_factory=dict)
    summary: str
    errors: list[str] = Field(default_factory=list)


class ImportResult(BaseModel):
    import_run_id: str
    status: Literal["COMPLETED", "ROLLED_BACK", "PARTIAL_REQUIRES_ATTENTION"]
    archive_sha256: str
    created: dict[str, int] = Field(default_factory=dict)
    skipped: dict[str, int] = Field(default_factory=dict)
    affected_ids: list[str] = Field(default_factory=list)
    compensation_errors: list[str] = Field(default_factory=list)
    summary: str


class AutomaticRunStatus(BaseModel):
    enabled: bool
    timezone: str
    local_time: str
    schedule_name: str
    state: str
    run_id: str | None = None
    local_date: str | None = None
    content_sha256: str | None = None
    archive_sha256: str | None = None
    blob_path: str | None = None
    same_content_as_latest: bool = False
    counts: dict[str, int] = Field(default_factory=dict)
    detail: str | None = None
