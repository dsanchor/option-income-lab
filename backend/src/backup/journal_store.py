from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class JournalStore:
    """Durable import journal stored in the existing import_sessions container."""

    def __init__(self, container, retention_seconds: int = 90 * 24 * 3600) -> None:
        if container is None:
            raise RuntimeError("import_sessions container is required for safe import")
        self.container = container
        self.retention_seconds = retention_seconds

    def create(
        self, run_id: str, archive_sha256: str, plan_digest: str,
        planned_items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        doc = {
            "id": run_id,
            "session_id": run_id,
            "doc_type": "user_backup_import_journal",
            "import_run_id": run_id,
            "archive_sha256": archive_sha256,
            "plan_digest": plan_digest,
            "state": "PREPARED",
            "planned_items": deepcopy(planned_items or []),
            "created_items": [],
            "before_images": [],
            "compensation_errors": [],
            "created_at": _now(),
            "updated_at": _now(),
            "ttl": self.retention_seconds,
        }
        return self.container.create_item(body=doc)

    def get(self, run_id: str) -> dict[str, Any] | None:
        try:
            return self.container.read_item(item=run_id, partition_key=run_id)
        except Exception as exc:
            if "404" in str(exc) or "not found" in str(exc).lower():
                return None
            raise

    def save(self, journal: dict[str, Any]) -> dict[str, Any]:
        body = deepcopy(journal)
        body["updated_at"] = _now()
        return self.container.replace_item(item=body["id"], body=body)

    def set_state(self, journal: dict[str, Any], state: str, **extra: Any) -> dict[str, Any]:
        journal = deepcopy(journal)
        journal["state"] = state
        journal.update(extra)
        return self.save(journal)

    def prepare_inventory(
        self, journal: dict[str, Any], planned_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        journal = deepcopy(journal)
        journal["planned_items"] = deepcopy(planned_items)
        return self.save(journal)

    def transition_item(
        self, journal: dict[str, Any], item_key: str, state: str,
        **extra: Any,
    ) -> dict[str, Any]:
        if state not in {"PREPARED", "CREATED", "AMBIGUOUS"}:
            raise ValueError(f"Invalid journal item state: {state}")
        journal = deepcopy(journal)
        matched = False
        for item in journal.get("planned_items", []):
            if item.get("item_key") == item_key:
                item["state"] = state
                item.update(extra)
                matched = True
                break
        if not matched:
            raise KeyError(f"Unknown planned journal item: {item_key}")
        journal["created_items"] = [
            deepcopy(item)
            for item in journal.get("planned_items", [])
            if item.get("state") in {"CREATED", "AMBIGUOUS"}
        ]
        return self.save(journal)

    def acquire_lock(self, run_id: str) -> dict[str, Any]:
        lock = {
            "id": "user-backup-import-lock",
            "session_id": "user-backup-import-lock",
            "doc_type": "user_backup_import_lock",
            "owner_run_id": run_id,
            "created_at": _now(),
            "ttl": 3600,
        }
        try:
            return self.container.create_item(body=lock)
        except Exception as exc:
            if "409" in str(exc) or "conflict" in str(exc).lower():
                raise ImportAlreadyRunningError("Another backup import is active") from exc
            raise

    def release_lock(self, run_id: str) -> None:
        try:
            current = self.container.read_item(
                item="user-backup-import-lock",
                partition_key="user-backup-import-lock",
            )
            if current.get("owner_run_id") == run_id:
                self.container.delete_item(
                    item="user-backup-import-lock",
                    partition_key="user-backup-import-lock",
                )
        except Exception as exc:
            if "404" not in str(exc) and "not found" not in str(exc).lower():
                raise


class ImportAlreadyRunningError(RuntimeError):
    pass
