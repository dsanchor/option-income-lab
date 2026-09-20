from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .blob_store import BlobLeaseBusyError, BlobStore
from .export_service import ExportService
from .models import AutomaticRunStatus, ExportRequest
from .section_schemas import SchemaError


@dataclass(frozen=True)
class AutomaticBackupConfig:
    enabled: bool = True
    timezone: str = "Europe/Madrid"
    local_time: str = "00:15"
    schedule_name: str = "daily-user-data"

    @classmethod
    def from_environment(cls) -> AutomaticBackupConfig:
        return cls(
            enabled=os.getenv("BACKUP_ENABLED", "true").lower() in {"1", "true", "yes"},
            timezone=os.getenv("BACKUP_TIMEZONE", "Europe/Madrid"),
            local_time=os.getenv("BACKUP_LOCAL_TIME", "00:15"),
            schedule_name=os.getenv("BACKUP_SCHEDULE_NAME", "daily-user-data"),
        )


def due_for_local_date(
    now_utc: datetime, config: AutomaticBackupConfig,
    completed_local_dates: set[str] | None = None,
) -> tuple[bool, str]:
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    try:
        zone = ZoneInfo(config.timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown IANA timezone: {config.timezone}") from exc
    local = now_utc.astimezone(zone)
    hour, minute = (int(part) for part in config.local_time.split(":", 1))
    due = datetime.combine(local.date(), time(hour, minute), tzinfo=zone)
    local_date = local.date().isoformat()
    return (
        config.enabled
        and local >= due
        and local_date not in (completed_local_dates or set()),
        local_date,
    )


class AutomaticBackupService:
    def __init__(
        self, exporter: ExportService, blobs: BlobStore,
        config: AutomaticBackupConfig | None = None,
    ) -> None:
        self.exporter = exporter
        self.blobs = blobs
        self.config = config or AutomaticBackupConfig.from_environment()

    def _run_path(self, local_date: str) -> str:
        return f"v1/control/scheduled-{self.config.schedule_name}-{local_date}.json"

    def run(
        self, *, trigger: str, now_utc: datetime | None = None,
        only_if_changed: bool | None = None,
    ) -> AutomaticRunStatus:
        now_utc = now_utc or datetime.now(timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)
        scheduled = trigger == "scheduled"
        due, local_date = due_for_local_date(now_utc, self.config)
        if scheduled and not due:
            return self._status("NOT_DUE", local_date=local_date)
        if scheduled:
            previous = self.blobs.read_json(self._run_path(local_date))
            if previous:
                return self._status(
                    previous.value.get("state", "NO_CHANGE"),
                    run_id=previous.value.get("run_id"), local_date=local_date,
                    detail="Scheduled local date already completed",
                )
        run_id = str(uuid4())
        started = now_utc.astimezone(timezone.utc)
        only_if_changed = scheduled if only_if_changed is None else only_if_changed
        try:
            with self.blobs.lease():
                latest_doc = self.blobs.recover_latest()
                latest = latest_doc.value if latest_doc else None
                request = ExportRequest(preset="recommended_full_user_backup")
                preview = self.exporter.preview(request)
                request.preview_fingerprint = preview.selection_fingerprint
                artifact = self.exporter.export(request)
                content_sha = artifact.manifest["content_sha256"]
                same = bool(latest and latest.get("content_sha256") == content_sha)
                completed = datetime.now(timezone.utc)
                if same and only_if_changed:
                    state = "NO_CHANGE"
                    blob_path = latest.get("blob_path") if latest else None
                    effective_archive_sha = (
                        latest.get("archive_sha256") if latest else artifact.archive_sha256
                    )
                else:
                    blob_path = (
                        f"v1/daily/{local_date.replace('-', '/')}/"
                        f"{completed:%Y%m%dT%H%M%SZ}_{run_id}_{content_sha[:12]}.oil-backup.zip"
                    )
                    self.blobs.upload_immutable(
                        blob_path, artifact.archive,
                        metadata={
                            "format": "option-income-lab-user-backup",
                            "schema_version": "1",
                            "content_sha256": content_sha,
                            "archive_sha256": artifact.archive_sha256,
                            "local_date": local_date,
                            "run_id": run_id,
                            "encrypted": "false",
                        },
                        tags={
                            "backupType": "daily",
                            "schemaVersion": "1",
                            "yearMonth": local_date[:7],
                            "encrypted": "false",
                            "retentionClass": "daily",
                        },
                    )
                    self.blobs.verify_archive(
                        blob_path, artifact.archive_sha256, len(artifact.archive)
                    )
                    latest_value = {
                        "run_id": run_id, "blob_path": blob_path,
                        "content_sha256": content_sha,
                        "archive_sha256": artifact.archive_sha256,
                        "size": len(artifact.archive), "schema_version": 1,
                        "counts": artifact.preview.counts, "local_date": local_date,
                        "completed_at_utc": completed.isoformat().replace("+00:00", "Z"),
                    }
                    run_value = {
                        **latest_value,
                        "trigger": trigger, "state": "UPLOADED",
                        "schedule_name": self.config.schedule_name,
                        "same_content_as_latest": same,
                        "started_at_utc": started.isoformat().replace("+00:00", "Z"),
                    }
                    self.blobs.write_run(run_id, started, run_value)
                    self.blobs.update_latest(
                        latest_value, latest_doc.etag if latest_doc else None
                    )
                    self.blobs.write_monthly_anchor(local_date[:7], latest_value)
                    state = "UPLOADED"
                    effective_archive_sha = artifact.archive_sha256
                if state != "UPLOADED":
                    run_value = {
                        "run_id": run_id, "trigger": trigger, "state": state,
                        "schedule_name": self.config.schedule_name,
                        "local_date": local_date, "content_sha256": content_sha,
                        "archive_sha256": effective_archive_sha,
                        "blob_path": blob_path, "same_content_as_latest": same,
                        "counts": artifact.preview.counts,
                        "size": latest.get("size") if latest else None,
                        "started_at_utc": started.isoformat().replace("+00:00", "Z"),
                        "completed_at_utc": completed.isoformat().replace("+00:00", "Z"),
                    }
                    self.blobs.write_run(run_id, started, run_value)
                self.blobs.reconcile_monthly_anchors()
                if scheduled:
                    self.blobs.write_json(self._run_path(local_date), run_value, create_only=True)
                previous_health = self.blobs.read_json("v1/control/health.json")
                self.blobs.write_health({
                    "last_attempt": run_value,
                    "last_scheduled_success": (
                        run_value if scheduled else
                        ((previous_health.value if previous_health else {}).get(
                            "last_scheduled_success"
                        ))
                    ),
                    "latest_changed_archive": (
                        run_value if state == "UPLOADED" else latest
                    ),
                    "health": "healthy",
                })
                return self._status(
                    state, run_id=run_id, local_date=local_date,
                    content_sha256=content_sha,
                    archive_sha256=effective_archive_sha,
                    blob_path=blob_path, same_content_as_latest=same,
                    counts=artifact.preview.counts,
                )
        except BlobLeaseBusyError:
            return self._status("ALREADY_RUNNING", run_id=run_id, local_date=local_date)
        except Exception as exc:
            detail = (
                exc.safe_detail()
                if isinstance(exc, SchemaError)
                else f"{type(exc).__name__}: backup operation failed"
            )
            failure = self._status(
                "FAILED", run_id=run_id, local_date=local_date,
                detail=detail,
            )
            try:
                previous_health = self.blobs.read_json("v1/control/health.json")
                health = dict(previous_health.value) if previous_health else {}
                health.update({
                    "last_attempt": failure.model_dump(),
                    "health": "unhealthy",
                    "error": failure.detail,
                })
                self.blobs.write_health(health)
            except Exception:
                pass
            return failure

    def _status(self, state: str, **kwargs: Any) -> AutomaticRunStatus:
        return AutomaticRunStatus(
            enabled=self.config.enabled, timezone=self.config.timezone,
            local_time=self.config.local_time, schedule_name=self.config.schedule_name,
            state=state, **kwargs,
        )
