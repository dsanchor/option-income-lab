from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .archive import SECTION_PATHS, BackupArchive
from .canonical import canonical_hash, canonical_json_bytes, content_hash, sha256_bytes
from .controls import compute_controls
from .dependency_closure import apply_filters, close_dependencies
from .models import (
    SECTION_NAMES,
    BackupManifest,
    ExportPreview,
    ExportRequest,
    SectionDescriptor,
)
from .section_schemas import (
    SchemaError,
    logical_key,
    project_account,
    project_action_plan,
    project_ledger,
    project_positions,
    project_security,
    project_settings,
    project_symbol_config,
)

PRESETS = {
    "recommended_full_user_backup": set(SECTION_NAMES),
    "portfolio_only": {
        "accounts", "securities", "symbol_configs", "option_positions", "ledger_movements",
    },
    "symbols_and_configuration": {
        "securities", "symbol_configs", "option_positions", "action_plans", "app_settings",
    },
}
PERMANENT_EXCLUSIONS = [
    "secrets and credentials", "Cosmos metadata and TTL", "activities and alerts",
    "telemetry and traces", "generated reports and forecasts", "calendar downloads",
    "pricing/enrichment caches", "hard-deleted records",
]


@dataclass
class ExportArtifact:
    archive: bytes
    manifest: dict[str, Any]
    preview: ExportPreview
    archive_sha256: str


class ExportService:
    def __init__(self, collector, archive: BackupArchive | None = None) -> None:
        self.collector = collector
        self.archive = archive or BackupArchive()

    @staticmethod
    def options() -> dict[str, Any]:
        return {
            "presets": [
                {"id": "recommended_full_user_backup", "label": "Recommended full backup"},
                {"id": "portfolio_only", "label": "Portfolio only"},
                {"id": "symbols_and_configuration", "label": "Symbols and configuration"},
                {"id": "custom", "label": "Custom"},
            ],
            "sections": list(SECTION_NAMES),
            "filters": ["account_ids", "security_ids", "symbols", "date_from", "date_to"],
            "permanent_exclusions": PERMANENT_EXCLUSIONS,
            "dependency_rules": [
                "movement -> account + security",
                "position/plan -> symbol config + security",
                "correction/reassignment/transfer/corporate-action chains remain complete",
            ],
            "source_row_sensitive": True,
        }

    @staticmethod
    def _project_records(
        section: str,
        items: list[dict[str, Any]],
        projector,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        records: list[dict[str, Any]] = []
        warnings: list[str] = []
        for item in items:
            try:
                projected = projector(item)
            except SchemaError as exc:
                warnings.append(f"SKIPPED_RECORD:{section}:{exc.safe_detail()}")
                continue
            if isinstance(projected, list):
                records.extend(projected)
            else:
                records.append(projected)
        return records, warnings

    def _project_all_with_warnings(
        self,
        request: ExportRequest,
    ) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
        source = self.collector.collect()
        configs = source.get("symbol_configs", [])
        projectors = {
            "accounts": (
                source.get("accounts", []),
                project_account,
            ),
            "securities": (
                source.get("securities", []),
                project_security,
            ),
            "symbol_configs": (
                configs,
                project_symbol_config,
            ),
            "option_positions": (
                configs,
                lambda item: project_positions(
                    item,
                    request.include_paper_positions,
                ),
            ),
            "ledger_movements": (
                source.get("ledger_movements", []),
                lambda item: project_ledger(item, request.include_source_row),
            ),
            "action_plans": (
                source.get("action_plans", []),
                project_action_plan,
            ),
            "app_settings": (
                source.get("app_settings_source") or [{}],
                project_settings,
            ),
        }
        records: dict[str, list[dict[str, Any]]] = {}
        warnings: list[str] = []
        for section, (items, projector) in projectors.items():
            records[section], section_warnings = self._project_records(
                section,
                items,
                projector,
            )
            warnings.extend(section_warnings)
        return records, warnings

    def _project_all(self, request: ExportRequest) -> dict[str, list[dict[str, Any]]]:
        records, _ = self._project_all_with_warnings(request)
        return records

    def _plan(self, request: ExportRequest):
        if request.preset == "custom":
            requested = set(request.sections or [])
            unknown = requested - set(SECTION_NAMES)
            if unknown:
                raise ValueError(f"Unknown sections: {sorted(unknown)}")
        else:
            requested = set(PRESETS[request.preset])
        all_records, projection_warnings = self._project_all_with_warnings(request)
        filtered = apply_filters(all_records, request.filters.model_dump())
        selected = {
            section: list(filtered[section]) if section in requested else []
            for section in SECTION_NAMES
        }
        closed, additions, dependency_warnings = close_dependencies(all_records, selected)
        warnings = sorted(set([*projection_warnings, *dependency_warnings]))
        effective = sorted(section for section, items in closed.items() if items or section in requested)
        for section in SECTION_NAMES:
            closed[section] = sorted(
                closed.get(section, []), key=lambda record: logical_key(section, record)
            )
        fingerprint_material = {
            "request": request.model_dump(exclude={"preview_fingerprint"}),
            "effective_sections": effective,
            "keys": {
                section: [logical_key(section, item) for item in closed[section]]
                for section in SECTION_NAMES
            },
            "warnings": warnings,
        }
        preview = ExportPreview(
            requested_sections=sorted(requested),
            effective_sections=effective,
            added_dependencies=additions,
            counts={section: len(closed[section]) for section in SECTION_NAMES},
            warnings=warnings,
            exclusions=PERMANENT_EXCLUSIONS,
            selection_fingerprint=canonical_hash(fingerprint_material),
        )
        return closed, preview

    def preview(self, request: ExportRequest) -> ExportPreview:
        return self._plan(request)[1]

    def export(self, request: ExportRequest, require_fingerprint: bool = True) -> ExportArtifact:
        sections, preview = self._plan(request)
        if require_fingerprint and request.preview_fingerprint != preview.selection_fingerprint:
            raise StalePreviewError("STALE_PREVIEW")
        descriptors = []
        section_bytes: dict[str, bytes] = {}
        for section in SECTION_NAMES:
            envelope = {
                "section": section, "schema_version": 1,
                "count": len(sections[section]), "records": sections[section],
            }
            data = canonical_json_bytes(envelope)
            section_bytes[section] = data
            descriptors.append({
                "name": section, "path": SECTION_PATHS[section], "schema_version": 1,
                "count": len(sections[section]), "sha256": sha256_bytes(data),
            })
        scope = {
            "preset": request.preset,
            "sections": preview.effective_sections,
            "filters": request.filters.model_dump(),
        }
        logical_hash = content_hash(descriptors, scope)
        payload_hash = canonical_hash({
            "sections": descriptors,
            "requested": preview.requested_sections,
            "effective": preview.effective_sections,
        })
        manifest = BackupManifest(
            app={"version": "0.1.0", "git_commit": os.getenv("BACKUP_BUILD_COMMIT")},
            scope=scope,
            sections=[SectionDescriptor(**item) for item in descriptors],
            redactions={
                "policy_version": 1, "secrets_included": False,
                "excluded_paths": [
                    "settings.telegram.bot_token", "settings.telegram.chat_id",
                    "settings.azure", "settings.gemini", "settings.cosmosdb",
                ],
            },
            content_sha256=logical_hash,
            archive_payload_sha256=payload_hash,
            requested_sections=preview.requested_sections,
            effective_sections=preview.effective_sections,
            dependency_additions=preview.added_dependencies,
            controls=compute_controls(sections),
            warnings=preview.warnings,
            include_paper_positions=request.include_paper_positions,
            include_source_row=request.include_source_row,
        )
        archive_bytes = self.archive.build(manifest, sections)
        parsed = self.archive.read(archive_bytes)
        return ExportArtifact(
            archive=archive_bytes,
            manifest=parsed.manifest,
            preview=preview,
            archive_sha256=parsed.archive_sha256,
        )


class StalePreviewError(ValueError):
    pass
