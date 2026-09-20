#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from src.backup.archive import ArchiveLimits, BackupArchive
from src.backup.automatic_backup import AutomaticBackupService
from src.backup.blob_store import BlobStore
from src.backup.collectors import CosmosBackupCollector
from src.backup.export_service import ExportService
from src.cosmos_db import CosmosDBService


def _parse_now(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _limits() -> ArchiveLimits:
    return ArchiveLimits(
        max_archive_bytes=int(os.getenv("BACKUP_MAX_ARCHIVE_BYTES", 50 * 1024 * 1024)),
        max_uncompressed_bytes=int(os.getenv("BACKUP_MAX_UNCOMPRESSED_BYTES", 200 * 1024 * 1024)),
        max_entry_bytes=int(os.getenv("BACKUP_MAX_ENTRY_BYTES", 50 * 1024 * 1024)),
        max_files=int(os.getenv("BACKUP_MAX_ZIP_FILES", "9")),
        max_records=int(os.getenv("BACKUP_MAX_RECORDS", "200000")),
        max_json_depth=int(os.getenv("BACKUP_MAX_JSON_DEPTH", "30")),
        max_expansion_ratio=float(os.getenv("BACKUP_MAX_EXPANSION_RATIO", "100")),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run authoritative user-data backup")
    parser.add_argument("mode", choices=("scheduled", "manual"))
    parser.add_argument("--now", help="Simulated UTC instant for scheduled gate")
    parser.add_argument("--only-if-changed", action="store_true")
    args = parser.parse_args()
    endpoint = os.getenv("COSMOSDB_ENDPOINT")
    key = os.getenv("COSMOSDB_KEY")
    if not endpoint or not key:
        parser.error("COSMOSDB_ENDPOINT and COSMOSDB_KEY are required")
    cosmos = CosmosDBService(
        endpoint=endpoint, key=key,
        database_name=os.getenv("COSMOSDB_DATABASE", "stock-options-manager"),
    )
    archive = BackupArchive(_limits())
    service = AutomaticBackupService(
        ExportService(CosmosBackupCollector(cosmos), archive),
        BlobStore.from_environment(),
    )
    result = service.run(
        trigger=args.mode, now_utc=_parse_now(args.now),
        only_if_changed=(args.only_if_changed if args.mode == "manual" else True),
    )
    print(json.dumps(result.model_dump(), sort_keys=True))
    return 0 if result.state in {"UPLOADED", "NO_CHANGE", "NOT_DUE", "ALREADY_RUNNING"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
