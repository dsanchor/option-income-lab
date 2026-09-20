from __future__ import annotations

import io
import json
import posixpath
import stat
import zipfile
from dataclasses import dataclass
from typing import Any

from .canonical import canonical_hash, canonical_json_bytes, content_hash, sha256_bytes
from .models import SECTION_NAMES, BackupManifest
from .section_schemas import validate_record

SECTION_PATHS = {
    "accounts": "data/accounts.json",
    "securities": "data/securities.json",
    "symbol_configs": "data/symbol-configs.json",
    "option_positions": "data/option-positions.json",
    "ledger_movements": "data/ledger-movements.json",
    "action_plans": "data/action-plans.json",
    "app_settings": "data/app-settings.json",
}
FIXED_FILES = ("manifest.json", *SECTION_PATHS.values(), "checksums.sha256")


@dataclass(frozen=True)
class ArchiveLimits:
    max_archive_bytes: int = 50 * 1024 * 1024
    max_uncompressed_bytes: int = 200 * 1024 * 1024
    max_entry_bytes: int = 50 * 1024 * 1024
    max_files: int = len(FIXED_FILES)
    max_records: int = 200_000
    max_json_depth: int = 30
    max_expansion_ratio: float = 100.0


@dataclass
class ParsedArchive:
    manifest: dict[str, Any]
    sections: dict[str, list[dict[str, Any]]]
    archive_sha256: str
    raw_bytes: bytes


def _depth(value: Any, current: int = 0) -> int:
    if isinstance(value, dict):
        return max([current] + [_depth(item, current + 1) for item in value.values()])
    if isinstance(value, list):
        return max([current] + [_depth(item, current + 1) for item in value])
    return current


def _safe_name(name: str) -> bool:
    if not name or "\\" in name or name.startswith(("/", "\\")):
        return False
    normalized = posixpath.normpath(name)
    return normalized == name and normalized not in {".", ".."} and not normalized.startswith("../")


class BackupArchive:
    def __init__(self, limits: ArchiveLimits | None = None) -> None:
        self.limits = limits or ArchiveLimits()

    def build(
        self, manifest: BackupManifest | dict[str, Any],
        sections: dict[str, list[dict[str, Any]]],
    ) -> bytes:
        manifest_dict = (
            manifest.model_dump(mode="json") if isinstance(manifest, BackupManifest) else manifest
        )
        files: dict[str, bytes] = {}
        files["manifest.json"] = canonical_json_bytes(manifest_dict)
        for section in SECTION_NAMES:
            records = sections.get(section, [])
            files[SECTION_PATHS[section]] = canonical_json_bytes({
                "section": section,
                "schema_version": 1,
                "count": len(records),
                "records": records,
            })
        checksum_lines = [
            f"{sha256_bytes(files[path])}  {path}"
            for path in ("manifest.json", *SECTION_PATHS.values())
        ]
        files["checksums.sha256"] = ("\n".join(checksum_lines) + "\n").encode("ascii")

        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in FIXED_FILES:
                info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o600) << 16
                archive.writestr(info, files[path])
        result = output.getvalue()
        if len(result) > self.limits.max_archive_bytes:
            raise ValueError("Archive exceeds compressed size limit")
        return result

    def read(self, payload: bytes) -> ParsedArchive:
        if len(payload) > self.limits.max_archive_bytes:
            raise ValueError("Archive exceeds compressed size limit")
        try:
            archive = zipfile.ZipFile(io.BytesIO(payload), "r")
        except (zipfile.BadZipFile, OSError) as exc:
            raise ValueError("Invalid ZIP archive") from exc
        with archive:
            infos = archive.infolist()
            if len(infos) > self.limits.max_files:
                raise ValueError("Archive file count exceeds limit")
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ValueError("Duplicate ZIP entry")
            if len({name.casefold() for name in names}) != len(names):
                raise ValueError("Case-colliding ZIP entry")
            if tuple(names) != FIXED_FILES:
                raise ValueError("Archive file set or order is not canonical")
            total = 0
            for info in infos:
                if not _safe_name(info.filename):
                    raise ValueError("Unsafe ZIP path")
                mode = info.external_attr >> 16
                if not stat.S_ISREG(mode):
                    raise ValueError("ZIP symlinks and other non-regular entries are forbidden")
                if info.file_size > self.limits.max_entry_bytes:
                    raise ValueError("ZIP entry exceeds size limit")
                total += info.file_size
                compressed = max(1, info.compress_size)
                if info.file_size / compressed > self.limits.max_expansion_ratio:
                    raise ValueError("ZIP entry expansion ratio exceeds limit")
            if total > self.limits.max_uncompressed_bytes:
                raise ValueError("Archive uncompressed size exceeds limit")
            if total / max(1, len(payload)) > self.limits.max_expansion_ratio:
                raise ValueError("Archive expansion ratio exceeds limit")
            raw = {name: archive.read(name) for name in names}

        expected_lines = [
            f"{sha256_bytes(raw[path])}  {path}"
            for path in ("manifest.json", *SECTION_PATHS.values())
        ]
        try:
            actual_lines = raw["checksums.sha256"].decode("ascii").splitlines()
        except UnicodeDecodeError as exc:
            raise ValueError("Checksum file must be ASCII") from exc
        if actual_lines != expected_lines:
            raise ValueError("Checksum mismatch or noncanonical checksum ordering")
        try:
            manifest = json.loads(raw["manifest.json"])
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Invalid manifest JSON") from exc
        if _depth(manifest) > self.limits.max_json_depth:
            raise ValueError("Manifest JSON nesting exceeds limit")
        if manifest.get("format") != "option-income-lab-user-backup":
            raise ValueError("Unsupported backup format")
        if manifest.get("archive_version") != 1 or manifest.get("schema_version") != 1:
            raise ValueError("Unsupported archive or schema version")

        descriptors = manifest.get("sections")
        if not isinstance(descriptors, list) or len(descriptors) != len(SECTION_NAMES):
            raise ValueError("Manifest section declarations are invalid")
        declared = {item.get("name"): item for item in descriptors if isinstance(item, dict)}
        if set(declared) != set(SECTION_NAMES):
            raise ValueError("Manifest has missing or duplicate section declarations")
        sections: dict[str, list[dict[str, Any]]] = {}
        total_records = 0
        for section in SECTION_NAMES:
            path = SECTION_PATHS[section]
            descriptor = declared[section]
            if descriptor.get("path") != path:
                raise ValueError(f"Invalid manifest path for {section}")
            if descriptor.get("sha256") != sha256_bytes(raw[path]):
                raise ValueError(f"Manifest checksum mismatch for {section}")
            try:
                envelope = json.loads(raw[path])
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ValueError(f"Invalid JSON for {section}") from exc
            if _depth(envelope) > self.limits.max_json_depth:
                raise ValueError(f"{section} JSON nesting exceeds limit")
            if (
                envelope.get("section") != section
                or envelope.get("schema_version") != 1
                or not isinstance(envelope.get("records"), list)
                or envelope.get("count") != len(envelope["records"])
                or descriptor.get("count") != len(envelope["records"])
            ):
                raise ValueError(f"Invalid envelope for {section}")
            total_records += len(envelope["records"])
            if total_records > self.limits.max_records:
                raise ValueError("Archive record count exceeds limit")
            keys = set()
            for record in envelope["records"]:
                validate_record(section, record)
                from .section_schemas import logical_key
                key = logical_key(section, record)
                if key in keys:
                    raise ValueError(f"Duplicate logical key in {section}: {key}")
                keys.add(key)
            sections[section] = envelope["records"]
        descriptor_material = [
            {
                "name": section,
                "schema_version": declared[section].get("schema_version", 1),
                "count": declared[section]["count"],
                "sha256": declared[section]["sha256"],
            }
            for section in SECTION_NAMES
        ]
        if manifest.get("content_sha256") != content_hash(
            descriptor_material, manifest.get("scope") or {}
        ):
            raise ValueError("Manifest logical content hash mismatch")
        expected_payload_hash = canonical_hash({
            "sections": [
                {
                    **item,
                    "path": declared[item["name"]]["path"],
                }
                for item in descriptor_material
            ],
            "requested": manifest.get("requested_sections") or [],
            "effective": manifest.get("effective_sections") or [],
        })
        if manifest.get("archive_payload_sha256") != expected_payload_hash:
            raise ValueError("Manifest archive payload hash mismatch")
        return ParsedArchive(
            manifest=manifest,
            sections=sections,
            archive_sha256=sha256_bytes(payload),
            raw_bytes=payload,
        )
