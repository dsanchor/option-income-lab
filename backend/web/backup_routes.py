from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from src.backup.archive import ArchiveLimits, BackupArchive
from src.backup.collectors import CosmosBackupCollector
from src.backup.export_service import ExportService, StalePreviewError
from src.backup.import_service import (
    ImportService, ImportValidationError, StaleDryRunError,
)
from src.backup.models import ExportRequest

router = APIRouter(prefix="/api/backups", tags=["backups"])


def _limits() -> ArchiveLimits:
    def integer(name: str, default: int) -> int:
        return int(os.getenv(name, str(default)))

    return ArchiveLimits(
        max_archive_bytes=integer("BACKUP_MAX_ARCHIVE_BYTES", 50 * 1024 * 1024),
        max_uncompressed_bytes=integer("BACKUP_MAX_UNCOMPRESSED_BYTES", 200 * 1024 * 1024),
        max_entry_bytes=integer("BACKUP_MAX_ENTRY_BYTES", 50 * 1024 * 1024),
        max_files=integer("BACKUP_MAX_ZIP_FILES", 9),
        max_records=integer("BACKUP_MAX_RECORDS", 200_000),
        max_json_depth=integer("BACKUP_MAX_JSON_DEPTH", 30),
        max_expansion_ratio=float(os.getenv("BACKUP_MAX_EXPANSION_RATIO", "100")),
    )


def _cosmos(request: Request):
    cosmos = getattr(request.app.state, "cosmos", None)
    if cosmos is None:
        raise RuntimeError(
            f"CosmosDB not available: {getattr(request.app.state, 'cosmos_error', 'unknown')}"
        )
    return cosmos


def _archive() -> BackupArchive:
    return BackupArchive(_limits())


def _exporter(request: Request) -> ExportService:
    return ExportService(CosmosBackupCollector(_cosmos(request)), _archive())


def _importer(request: Request) -> ImportService:
    return ImportService(_cosmos(request), _archive())


async def _read_upload(file: UploadFile) -> bytes:
    maximum = _limits().max_archive_bytes
    payload = await file.read(maximum + 1)
    if len(payload) > maximum:
        raise ValueError("Archive exceeds compressed size limit")
    return payload


@router.get("/export/options")
async def export_options():
    return ExportService.options()


@router.post("/export/preview")
async def export_preview(body: ExportRequest, request: Request):
    try:
        return _exporter(request).preview(body).model_dump()
    except Exception as exc:
        return JSONResponse(
            {"error": "FAILED_EXPORT", "detail": str(exc)}, status_code=422
        )


@router.post("/export/run")
async def export_run(body: ExportRequest, request: Request):
    try:
        artifact = _exporter(request).export(body, require_fingerprint=True)
    except StalePreviewError:
        return JSONResponse(
            {"error": "STALE_PREVIEW", "detail": "Export selection changed; preview again"},
            status_code=409,
        )
    except Exception as exc:
        return JSONResponse(
            {"error": "FAILED_EXPORT", "detail": str(exc)}, status_code=422
        )
    export_id = artifact.manifest["export_id"]
    filename = (
        f"option-income-lab-backup-"
        f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.oil-backup.zip"
    )
    return Response(
        artifact.archive, media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Backup-Export-Id": export_id,
            "X-Backup-Content-SHA256": artifact.manifest["content_sha256"],
            "X-Backup-Archive-SHA256": artifact.archive_sha256,
        },
    )


@router.post("/import/validate")
async def import_validate(request: Request, file: UploadFile = File(...)):
    try:
        payload = await _read_upload(file)
        return _importer(request).validate(payload).model_dump()
    except Exception as exc:
        return JSONResponse(
            {"valid": False, "error": "INVALID_ARCHIVE", "detail": str(exc)},
            status_code=422,
        )


@router.post("/import/dry-run")
async def import_dry_run(
    request: Request, file: UploadFile = File(...),
    mode: str = Form("create_only"),
):
    try:
        payload = await _read_upload(file)
        plan = _importer(request).dry_run(payload, mode=mode)
        return plan.model_dump()
    except (ValueError, ImportValidationError) as exc:
        return JSONResponse(
            {"error": "INVALID_IMPORT", "detail": str(exc)}, status_code=422
        )


@router.post("/import/apply")
async def import_apply(
    request: Request, file: UploadFile = File(...),
    mode: str = Form("create_only"),
    dry_run_fingerprint: str = Form(...),
    confirm: bool = Form(False),
):
    try:
        payload = await _read_upload(file)
        result = _importer(request).apply(
            payload, mode=mode, dry_run_fingerprint=dry_run_fingerprint,
            confirm=confirm,
        )
        return result.model_dump()
    except StaleDryRunError:
        return JSONResponse(
            {"error": "STALE_DRY_RUN", "detail": "Archive or destination changed"},
            status_code=409,
        )
    except (ValueError, ImportValidationError) as exc:
        return JSONResponse(
            {"error": "INVALID_IMPORT", "detail": str(exc)}, status_code=422
        )


@router.get("/import/runs/{import_run_id}")
async def import_run_status(import_run_id: str, request: Request):
    try:
        result = _importer(request).get_run(import_run_id)
    except Exception as exc:
        return JSONResponse(
            {"error": "storage_unavailable", "detail": str(exc)}, status_code=503
        )
    if result is None:
        return JSONResponse(
            {"error": "not_found", "detail": "Import run not found"}, status_code=404
        )
    return result

