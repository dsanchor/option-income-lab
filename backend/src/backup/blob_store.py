from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .archive import BackupArchive
from .canonical import canonical_json_bytes, sha256_bytes


class BlobDependencyError(RuntimeError):
    pass


class BlobAlreadyExistsError(RuntimeError):
    pass


class BlobLeaseBusyError(RuntimeError):
    pass


class BlobCasError(RuntimeError):
    pass


@dataclass
class BlobDocument:
    value: dict[str, Any]
    etag: str | None


class BlobStore:
    def __init__(self, container_client, archive: BackupArchive | None = None) -> None:
        self.container = container_client
        self.archive = archive or BackupArchive()

    @classmethod
    def from_environment(cls) -> BlobStore:
        account = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
        container = os.getenv("BACKUP_BLOB_CONTAINER", "user-data-backups")
        if not account:
            raise BlobDependencyError("AZURE_STORAGE_ACCOUNT_NAME is required")
        try:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient
        except ImportError as exc:
            raise BlobDependencyError(
                "Blob backup requires azure-identity and azure-storage-blob"
            ) from exc
        service = BlobServiceClient(
            account_url=f"https://{account}.blob.core.windows.net",
            credential=DefaultAzureCredential(),
        )
        return cls(service.get_container_client(container))

    def _client(self, path: str):
        return self.container.get_blob_client(path)

    def ensure_lock_blob(self, path: str = "v1/control/lock") -> None:
        client = self._client(path)
        try:
            client.upload_blob(b"", overwrite=False)
        except Exception as exc:
            if not self._is_conflict(exc):
                raise

    @contextmanager
    def lease(self, path: str = "v1/control/lock", duration: int = 60) -> Iterator[Any]:
        self.ensure_lock_blob(path)
        client = self._client(path)
        try:
            lease = client.acquire_lease(lease_duration=duration)
        except Exception as exc:
            if self._is_conflict(exc):
                raise BlobLeaseBusyError("Backup lease is already held") from exc
            raise
        stop = threading.Event()
        renewer = None
        if hasattr(lease, "renew"):
            def renew() -> None:
                while not stop.wait(max(15, duration // 2)):
                    lease.renew()
            renewer = threading.Thread(target=renew, daemon=True)
            renewer.start()
        try:
            yield lease
        finally:
            stop.set()
            if renewer is not None:
                renewer.join(timeout=2)
            try:
                lease.release()
            except Exception:
                pass

    @staticmethod
    def _is_not_found(exc: Exception) -> bool:
        return getattr(exc, "status_code", None) == 404 or "404" in str(exc)

    @staticmethod
    def _is_conflict(exc: Exception) -> bool:
        return getattr(exc, "status_code", None) in (409, 412) or any(
            marker in str(exc).lower() for marker in ("409", "412", "conflict", "conditionnotmet")
        )

    def read_json(self, path: str) -> BlobDocument | None:
        client = self._client(path)
        try:
            data = client.download_blob().readall()
            props = client.get_blob_properties()
            return BlobDocument(json.loads(data), getattr(props, "etag", None))
        except Exception as exc:
            if self._is_not_found(exc):
                return None
            raise

    def write_json(
        self, path: str, value: dict[str, Any], *,
        expected_etag: str | None = None, create_only: bool = False,
    ) -> str | None:
        client = self._client(path)
        kwargs: dict[str, Any] = {
            "data": canonical_json_bytes(value),
            "overwrite": not create_only,
        }
        if expected_etag is not None:
            try:
                from azure.core import MatchConditions
                kwargs.update(etag=expected_etag, match_condition=MatchConditions.IfNotModified)
            except ImportError:
                kwargs["etag"] = expected_etag
        try:
            response = client.upload_blob(**kwargs)
            return getattr(response, "etag", None)
        except Exception as exc:
            if self._is_conflict(exc):
                if create_only:
                    raise BlobAlreadyExistsError(path) from exc
                raise BlobCasError(path) from exc
            raise

    def upload_immutable(
        self, path: str, payload: bytes, metadata: dict[str, str] | None = None,
        tags: dict[str, str] | None = None,
    ) -> None:
        client = self._client(path)
        try:
            client.upload_blob(
                data=payload, overwrite=False, metadata=metadata or {}, tags=tags or {},
            )
        except Exception as exc:
            if self._is_conflict(exc):
                existing = client.download_blob().readall()
                if existing == payload:
                    return
                raise BlobAlreadyExistsError(path) from exc
            raise

    def verify_archive(
        self, path: str, archive_sha256: str, expected_size: int,
    ) -> dict[str, Any]:
        client = self._client(path)
        payload = client.download_blob().readall()
        props = client.get_blob_properties()
        size = getattr(props, "size", getattr(props, "content_length", len(payload)))
        if len(payload) != expected_size or size != expected_size:
            raise RuntimeError("Committed Blob length verification failed")
        if sha256_bytes(payload) != archive_sha256:
            raise RuntimeError("Committed Blob SHA-256 verification failed")
        parsed = self.archive.read(payload)
        if parsed.archive_sha256 != archive_sha256:
            raise RuntimeError("Committed Blob archive verification failed")
        return parsed.manifest

    def list_paths(self, prefix: str) -> list[str]:
        return sorted(
            str(getattr(item, "name", item))
            for item in self.container.list_blobs(name_starts_with=prefix)
        )

    def set_tags(self, path: str, tags: dict[str, str]) -> None:
        self._client(path).set_blob_tags(tags)

    def delete(self, path: str) -> None:
        self._client(path).delete_blob()

    def _verified_candidate(self, value: dict[str, Any]) -> dict[str, Any] | None:
        path = value.get("blob_path")
        archive_sha256 = value.get("archive_sha256")
        if not path or not archive_sha256 or not str(path).startswith("v1/daily/"):
            return None
        try:
            client = self._client(str(path))
            props = client.get_blob_properties()
            size = int(getattr(props, "size", getattr(props, "content_length", 0)))
            self.verify_archive(str(path), str(archive_sha256), size)
        except Exception:
            return None
        candidate = dict(value)
        candidate["size"] = size
        return candidate

    def recover_latest(self) -> BlobDocument | None:
        current = self.read_json("v1/control/latest.json")
        current_value = self._verified_candidate(current.value) if current else None
        candidates: list[dict[str, Any]] = []
        for path in self.list_paths("v1/runs/"):
            doc = self.read_json(path)
            if doc is None or doc.value.get("state") != "UPLOADED":
                continue
            verified = self._verified_candidate(doc.value)
            if verified:
                candidates.append(verified)
        if current_value:
            candidates.append(current_value)
        if not candidates:
            return None
        best = max(
            candidates,
            key=lambda item: (
                str(item.get("completed_at_utc", "")),
                str(item.get("run_id", "")),
            ),
        )
        if current_value is None or (
            str(best.get("completed_at_utc", ""))
            > str(current_value.get("completed_at_utc", ""))
        ):
            if current is not None and current_value is None:
                self.write_json(
                    "v1/control/latest.json", best, expected_etag=current.etag
                )
            else:
                self.update_latest(best, current.etag if current else None)
            return self.read_json("v1/control/latest.json")
        return BlobDocument(current_value, current.etag if current else None)

    def reconcile_monthly_anchors(self, keep_months: int = 12) -> dict[str, str]:
        candidates: dict[str, dict[str, Any]] = {}
        existing_anchor_paths = self.list_paths("v1/monthly/")
        for path in existing_anchor_paths:
            if not path.endswith(".json"):
                continue
            doc = self.read_json(path)
            value = self._verified_candidate(doc.value) if doc else None
            month = path.removeprefix("v1/monthly/").removesuffix(".json")
            if value is not None:
                candidates[month] = value
        for path in self.list_paths("v1/runs/"):
            doc = self.read_json(path)
            if doc is None or doc.value.get("state") not in {"UPLOADED", "NO_CHANGE"}:
                continue
            value = self._verified_candidate(doc.value)
            local_date = str(doc.value.get("local_date") or "")
            if value is None or len(local_date) < 7:
                continue
            month = local_date[:7]
            previous = candidates.get(month)
            if previous is None or str(value.get("completed_at_utc", "")) > str(
                previous.get("completed_at_utc", "")
            ):
                candidates[month] = value
        live_months = sorted(candidates)[-keep_months:]
        live_anchor_paths = {f"v1/monthly/{month}.json" for month in live_months}
        for path in existing_anchor_paths:
            if path.endswith(".json") and path not in live_anchor_paths:
                self.delete(path)
        anchors: dict[str, str] = {}
        for month in live_months:
            self.write_monthly_anchor(month, candidates[month])
            anchors[month] = str(candidates[month]["blob_path"])
        monthly_paths = set(anchors.values())
        for path in self.list_paths("v1/daily/"):
            if not path.endswith(".zip"):
                continue
            parts = path.split("/")
            year_month = f"{parts[2]}-{parts[3]}" if len(parts) > 4 else ""
            self.set_tags(path, {
                "backupType": "daily",
                "schemaVersion": "1",
                "yearMonth": year_month,
                "encrypted": "false",
                "retentionClass": "monthly" if path in monthly_paths else "daily",
            })
        return anchors

    def write_run(self, run_id: str, started_at: datetime, value: dict[str, Any]) -> str:
        path = f"v1/runs/{started_at:%Y/%m}/{run_id}.json"
        try:
            self.write_json(path, value, create_only=True)
        except BlobAlreadyExistsError:
            existing = self.read_json(path)
            if existing is None or existing.value != value:
                raise
        return path

    def update_latest(self, value: dict[str, Any], expected_etag: str | None) -> None:
        current = self.read_json("v1/control/latest.json")
        if current:
            current_time = str(current.value.get("completed_at_utc", ""))
            candidate_time = str(value.get("completed_at_utc", ""))
            if current_time > candidate_time:
                return
            if current.value.get("run_id") == value.get("run_id"):
                return
            expected_etag = current.etag
        try:
            self.write_json(
                "v1/control/latest.json", value,
                expected_etag=expected_etag, create_only=current is None,
            )
        except BlobAlreadyExistsError:
            refreshed = self.read_json("v1/control/latest.json")
            if refreshed and (
                refreshed.value.get("run_id") == value.get("run_id")
                or str(refreshed.value.get("completed_at_utc", "")) >= str(value.get("completed_at_utc", ""))
            ):
                return
            raise BlobCasError("latest.json changed concurrently")

    def write_health(self, value: dict[str, Any]) -> None:
        current = self.read_json("v1/control/health.json")
        self.write_json(
            "v1/control/health.json", value,
            expected_etag=current.etag if current else None,
            create_only=current is None,
        )

    def write_monthly_anchor(self, year_month: str, latest: dict[str, Any]) -> None:
        path = f"v1/monthly/{year_month}.json"
        current = self.read_json(path)
        if current and str(current.value.get("completed_at_utc", "")) >= str(
            latest.get("completed_at_utc", "")
        ):
            return
        self.write_json(
            path, latest, expected_etag=current.etag if current else None,
            create_only=current is None,
        )

    def status(self) -> dict[str, Any]:
        latest = self.read_json("v1/control/latest.json")
        health = self.read_json("v1/control/health.json")
        return {
            "latest": latest.value if latest else None,
            "health": health.value if health else None,
        }
