from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .archive import BackupArchive, ParsedArchive
from .canonical import canonical_hash
from .controls import compute_controls
from .dependency_closure import validate_dependency_closure
from .journal_store import JournalStore
from .models import (
    SECTION_NAMES,
    DryRunPlan,
    ImportResult,
    RecordPlan,
    ValidationReport,
)
from .section_schemas import logical_key, partition_key

APPLY_ORDER = [
    "securities", "accounts", "symbol_configs", "option_positions",
    "action_plans", "ledger_movements", "app_settings",
]


class StaleDryRunError(ValueError):
    pass


class ImportValidationError(ValueError):
    pass


def _not_found(exc: Exception) -> bool:
    return "404" in str(exc) or "not found" in str(exc).lower()


class ImportService:
    def __init__(self, cosmos, archive: BackupArchive | None = None) -> None:
        self.cosmos = cosmos
        self.archive = archive or BackupArchive()
        self.symbols = getattr(cosmos, "container", None)
        self.portfolio = getattr(cosmos, "portfolio_container", None)
        self.settings = getattr(cosmos, "settings_container", None)
        self.journal_container = getattr(cosmos, "import_sessions_container", None)

    def _journals(self) -> JournalStore:
        return JournalStore(self.journal_container)

    def _read_item(self, section: str, record: dict[str, Any]) -> dict[str, Any] | None:
        if section == "app_settings":
            if self.settings is None:
                raise RuntimeError("Settings container is unavailable")
            try:
                doc = self.settings.read_item(item="app-config", partition_key="app-config")
            except Exception as exc:
                if _not_found(exc):
                    return None
                raise
            path = record["path"]
            if path not in doc:
                return None
            return {"path": path, "value": doc[path]}
        container = self.portfolio if section in {"accounts", "ledger_movements"} else self.symbols
        if container is None:
            raise RuntimeError(f"Container unavailable for {section}")
        if section == "option_positions":
            config_id = self._config_id(record)
            try:
                config = container.read_item(
                    item=config_id, partition_key=partition_key(section, record)
                )
            except Exception as exc:
                if _not_found(exc):
                    return None
                raise
            for item in config.get("positions") or []:
                if item.get("position_id") == record["position_id"]:
                    projected = {
                        "security_id": record.get("security_id"),
                        "symbol": record.get("symbol"),
                        **item,
                        "position_kind": "paper" if item.get("is_paper") else "real",
                    }
                    projected.pop("is_paper", None)
                    return projected
            return {"__missing_in_existing_config__": True}
        try:
            existing = container.read_item(
                item=record["id"], partition_key=partition_key(section, record)
            )
            if section == "symbol_configs":
                existing.pop("positions", None)
            return existing
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    @staticmethod
    def _config_id(record: dict[str, Any]) -> str:
        symbol = record.get("symbol") or str(record.get("security_id", "")).split(":")[-1]
        return f"config_{symbol}"

    def _destination_digest(self, parsed: ParsedArchive) -> str:
        states = []
        for section in APPLY_ORDER:
            for record in parsed.sections[section]:
                existing = self._read_item(section, record)
                states.append({
                    "section": section,
                    "key": logical_key(section, record),
                    "hash": canonical_hash(existing) if existing is not None else None,
                })
        return canonical_hash(states)

    def _security_identity_collision(self, record: dict[str, Any]) -> str | None:
        if self.symbols is None:
            raise RuntimeError("Symbols container is unavailable")
        identifiers = {
            field: str(record[field]).upper()
            for field in ("isin", "cusip", "sedol")
            if record.get(field)
        }
        if not identifiers:
            return None
        rows = self.symbols.query_items(
            query="SELECT * FROM c WHERE c.doc_type = 'security_master'",
            enable_cross_partition_query=True,
        )
        for existing in rows:
            if existing.get("security_id") == record.get("security_id"):
                continue
            for field, value in identifiers.items():
                if str(existing.get(field, "")).upper() == value:
                    return (
                        f"{field.upper()} already belongs to "
                        f"{existing.get('security_id', 'another security')}"
                    )
        return None

    def validate(self, payload: bytes) -> ValidationReport:
        try:
            parsed = self.archive.read(payload)
            dependency_errors = validate_dependency_closure(parsed.sections)
            control_errors = self._validate_controls(parsed)
            plan = self._plan(parsed)
            collisions = [
                {"section": item.section, "logical_key": item.logical_key, "detail": item.detail}
                for item in plan.records if item.status == "CONFLICT_REQUIRES_CHOICE"
            ]
            errors = list(dependency_errors) + control_errors
            return ValidationReport(
                valid=not errors and not collisions,
                archive_sha256=parsed.archive_sha256,
                manifest_summary={
                    "export_id": parsed.manifest.get("export_id"),
                    "exported_at": parsed.manifest.get("exported_at"),
                    "schema_version": parsed.manifest.get("schema_version"),
                    "content_sha256": parsed.manifest.get("content_sha256"),
                },
                section_counts={
                    section: len(parsed.sections[section]) for section in SECTION_NAMES
                },
                warnings=list(parsed.manifest.get("warnings") or []),
                dependency_errors=dependency_errors,
                collisions=collisions,
                errors=errors,
            )
        except Exception as exc:
            return ValidationReport(
                valid=False, archive_sha256=canonical_hash(payload.hex()),
                compatible=False, errors=[str(exc)],
            )

    def _plan(self, parsed: ParsedArchive) -> DryRunPlan:
        dependency_errors = validate_dependency_closure(parsed.sections)
        control_errors = self._validate_controls(parsed)
        records: list[RecordPlan] = []
        for section in APPLY_ORDER:
            for record in parsed.sections[section]:
                existing = self._read_item(section, record)
                key = logical_key(section, record)
                identity_collision = (
                    self._security_identity_collision(record)
                    if section == "securities" and existing is None else None
                )
                if identity_collision:
                    status = "CONFLICT_REQUIRES_CHOICE"
                    detail = identity_collision
                elif existing is None:
                    status = "CREATE"
                    detail = None
                elif canonical_hash(existing) == canonical_hash(record):
                    status = "SKIP_IDENTICAL"
                    detail = None
                else:
                    status = "CONFLICT_REQUIRES_CHOICE"
                    detail = "Destination logical key exists with different canonical content"
                records.append(RecordPlan(
                    section=section, logical_key=key, status=status, detail=detail
                ))
        for record_plan in records:
            if (
                record_plan.section == "app_settings"
                and record_plan.status == "CREATE"
                and self._settings_document_exists()
            ):
                record_plan.status = "CONFLICT_REQUIRES_CHOICE"
                record_plan.detail = (
                    "Create-only import cannot add a path to an existing settings document"
                )
        for error in dependency_errors:
            records.append(RecordPlan(
                section="ledger_movements", logical_key=error,
                status="BLOCKED_MISSING_REFERENCE", detail=error,
            ))
        for error in control_errors:
            records.append(RecordPlan(
                section="ledger_movements", logical_key="manifest-controls",
                status="BLOCKED_INVARIANT", detail=error,
            ))
        destination_digest = self._destination_digest(parsed)
        fingerprint = canonical_hash({
            "archive_sha256": parsed.archive_sha256,
            "destination_snapshot_digest": destination_digest,
            "records": [item.model_dump() for item in records],
        })
        blocking = [
            item for item in records
            if item.status not in {"CREATE", "SKIP_IDENTICAL", "REDACTED_IGNORED"}
        ]
        counts = Counter(item.status for item in records)
        return DryRunPlan(
            valid=not blocking,
            dry_run_fingerprint=fingerprint,
            destination_snapshot_digest=destination_digest,
            archive_sha256=parsed.archive_sha256,
            records=records,
            ordering=APPLY_ORDER,
            predicted_controls={
                "status_counts": dict(counts),
                "round_trip": compute_controls(parsed.sections),
            },
            summary=(
                f"{counts.get('CREATE', 0)} create, "
                f"{counts.get('SKIP_IDENTICAL', 0)} identical, "
                f"{len(blocking)} blocked"
            ),
            errors=[item.detail or item.logical_key for item in blocking],
        )

    def _settings_document_exists(self) -> bool:
        if self.settings is None:
            raise RuntimeError("Settings container is unavailable")
        try:
            self.settings.read_item(item="app-config", partition_key="app-config")
            return True
        except Exception as exc:
            if _not_found(exc):
                return False
            raise

    def dry_run(self, payload: bytes, mode: str = "create_only") -> DryRunPlan:
        if mode != "create_only":
            raise ImportValidationError("Only create_only mode is supported")
        parsed = self.archive.read(payload)
        return self._plan(parsed)

    @staticmethod
    def _validate_controls(parsed: ParsedArchive) -> list[str]:
        expected = parsed.manifest.get("controls")
        actual = compute_controls(parsed.sections)
        if expected != actual:
            return ["Manifest round-trip controls do not match archive contents"]
        return []

    def _build_create(
        self, section: str, record: dict[str, Any], parsed: ParsedArchive,
    ) -> tuple[Any, dict[str, Any], str, str]:
        if section == "app_settings":
            body = {"id": "app-config"}
            for item in parsed.sections["app_settings"]:
                body[item["path"]] = deepcopy(item["value"])
            return self.settings, body, "app-config", "app-config"
        container = self.portfolio if section in {"accounts", "ledger_movements"} else self.symbols
        body = deepcopy(record)
        if section == "symbol_configs":
            positions = []
            identity = record.get("security_id") or record.get("symbol")
            for position in parsed.sections["option_positions"]:
                if (position.get("security_id") or position.get("symbol")) != identity:
                    continue
                embedded = {
                    key: deepcopy(value) for key, value in position.items()
                    if key not in {"security_id", "symbol", "position_kind"}
                }
                if position.get("position_kind") == "paper":
                    embedded["is_paper"] = True
                positions.append(embedded)
            body["positions"] = positions
        return container, body, str(body["id"]), partition_key(section, record)

    def _planned_inventory(
        self, parsed: ParsedArchive, status_by_key: dict[tuple[str, str], str],
    ) -> list[dict[str, Any]]:
        planned = []
        settings_added = False
        for section in APPLY_ORDER:
            for record in parsed.sections[section]:
                key = logical_key(section, record)
                if status_by_key[(section, key)] != "CREATE" or section == "option_positions":
                    continue
                if section == "app_settings" and settings_added:
                    continue
                _, body, item_id, pk = self._build_create(section, record, parsed)
                planned.append({
                    "item_key": f"{section}:{item_id}:{pk}",
                    "section": section,
                    "id": item_id,
                    "partition_key": pk,
                    "canonical_sha256": canonical_hash(body),
                    "state": "PREPARED",
                })
                settings_added = settings_added or section == "app_settings"
        return planned

    def _delete_created(self, item: dict[str, Any]) -> None:
        section = item["section"]
        if section == "app_settings":
            self.settings.delete_item(item="app-config", partition_key="app-config")
            return
        container = self.portfolio if section in {"accounts", "ledger_movements"} else self.symbols
        current = container.read_item(item=item["id"], partition_key=item["partition_key"])
        if canonical_hash(current) != item["canonical_sha256"]:
            raise RuntimeError(f"Created item changed concurrently: {section}:{item['id']}")
        kwargs = {"item": item["id"], "partition_key": item["partition_key"]}
        if current.get("_etag"):
            try:
                from azure.core import MatchConditions
                kwargs.update(etag=current["_etag"], match_condition=MatchConditions.IfNotModified)
            except ImportError:
                pass
        container.delete_item(**kwargs)

    def _read_planned(self, item: dict[str, Any]) -> dict[str, Any] | None:
        section = item["section"]
        container = (
            self.settings if section == "app_settings"
            else self.portfolio if section in {"accounts", "ledger_movements"}
            else self.symbols
        )
        try:
            return container.read_item(
                item=item["id"], partition_key=item["partition_key"]
            )
        except Exception as exc:
            if _not_found(exc):
                return None
            raise

    def _compensate(
        self, journals: JournalStore, journal: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        errors: list[str] = []
        durable = journals.get(journal["id"]) or journal
        for item in reversed(durable.get("planned_items", [])):
            try:
                current = self._read_planned(item)
                if current is None:
                    continue
                if canonical_hash(current) != item["canonical_sha256"]:
                    raise RuntimeError(
                        f"Planned create has unexpected content: "
                        f"{item['section']}:{item['id']}"
                    )
                if item.get("state") == "PREPARED":
                    durable = journals.transition_item(
                        durable, item["item_key"], "AMBIGUOUS",
                        recovered_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    )
                    item = next(
                        entry for entry in durable["planned_items"]
                        if entry["item_key"] == item["item_key"]
                    )
                self._delete_created(item)
            except Exception as compensation_exc:
                errors.append(str(compensation_exc))
        refreshed = journals.get(journal["id"]) or durable
        for item in refreshed.get("planned_items", []):
            try:
                if self._read_planned(item) is not None:
                    errors.append(
                        f"Orphaned planned create remains: {item['section']}:{item['id']}"
                    )
            except Exception as verify_exc:
                errors.append(
                    f"Could not verify compensation for {item['section']}:{item['id']}: "
                    f"{verify_exc}"
                )
        return refreshed, sorted(set(errors))

    def apply(
        self, payload: bytes, *, dry_run_fingerprint: str,
        mode: str = "create_only", confirm: bool = False,
    ) -> ImportResult:
        if mode != "create_only" or not confirm:
            raise ImportValidationError("create_only mode and confirm=true are required")
        parsed = self.archive.read(payload)
        plan = self._plan(parsed)
        if plan.dry_run_fingerprint != dry_run_fingerprint or not plan.valid:
            raise StaleDryRunError("STALE_DRY_RUN")
        run_id = str(uuid4())
        journals = self._journals()
        journal = journals.create(
            run_id, parsed.archive_sha256, plan.dry_run_fingerprint
        )
        created_counts: Counter[str] = Counter()
        skipped_counts: Counter[str] = Counter()
        affected: list[str] = []
        lock_acquired = False
        try:
            journals.acquire_lock(run_id)
            lock_acquired = True
            rechecked = self._plan(parsed)
            if rechecked.dry_run_fingerprint != dry_run_fingerprint or not rechecked.valid:
                raise StaleDryRunError("STALE_DRY_RUN")
            journal = journals.set_state(journal, "APPLYING")
            status_by_key = {
                (item.section, item.logical_key): item.status for item in rechecked.records
            }
            planned_items = self._planned_inventory(parsed, status_by_key)
            journal = journals.prepare_inventory(journal, planned_items)
            settings_created = False
            for section in APPLY_ORDER:
                for record in parsed.sections[section]:
                    key = logical_key(section, record)
                    status = status_by_key[(section, key)]
                    if status == "SKIP_IDENTICAL":
                        skipped_counts[section] += 1
                        continue
                    if section == "option_positions":
                        created_counts[section] += 1
                        affected.append(f"{section}:{key}")
                        continue
                    if section == "app_settings" and settings_created:
                        created_counts[section] += 1
                        affected.append(f"{section}:{key}")
                        continue
                    container, body, item_id, pk = self._build_create(section, record, parsed)
                    created = container.create_item(body=body)
                    if section == "app_settings":
                        settings_created = True
                    journal = journals.transition_item(
                        journal, f"{section}:{item_id}:{pk}", "CREATED",
                        created_sha256=canonical_hash(created),
                    )
                    created_counts[section] += 1
                    affected.append(f"{section}:{key}")
            postflight = self._plan(parsed)
            if not postflight.valid or any(
                item.status != "SKIP_IDENTICAL" for item in postflight.records
            ):
                raise RuntimeError("Post-flight logical verification failed")
            actual_sections = {
                section: [
                    self._read_item(section, record)
                    for record in parsed.sections[section]
                ]
                for section in SECTION_NAMES
            }
            if compute_controls(actual_sections) != parsed.manifest.get("controls"):
                raise RuntimeError("Post-flight round-trip controls mismatch")
            journal = journals.set_state(
                journal, "COMPLETED", created_counts=dict(created_counts),
                skipped_counts=dict(skipped_counts),
            )
            return ImportResult(
                import_run_id=run_id, status="COMPLETED",
                archive_sha256=parsed.archive_sha256,
                created=dict(created_counts), skipped=dict(skipped_counts),
                affected_ids=affected,
                summary=f"Created {sum(created_counts.values())}; skipped {sum(skipped_counts.values())}",
            )
        except Exception as exc:
            journal, errors = self._compensate(journals, journal)
            state = "PARTIAL_REQUIRES_ATTENTION" if errors else "ROLLED_BACK"
            journal = journals.set_state(
                journal, state, failure_code=type(exc).__name__,
                compensation_errors=errors,
            )
            if isinstance(exc, StaleDryRunError) and not journal.get("planned_items"):
                raise
            return ImportResult(
                import_run_id=run_id, status=state,
                archive_sha256=parsed.archive_sha256,
                created=dict(created_counts), skipped=dict(skipped_counts),
                affected_ids=affected, compensation_errors=errors,
                summary=f"Import failed and compensation ended as {state}",
            )
        finally:
            if lock_acquired:
                try:
                    journals.release_lock(run_id)
                except Exception:
                    pass

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        journal = self._journals().get(run_id)
        if journal is None:
            return None
        return {
            "import_run_id": run_id,
            "state": journal.get("state"),
            "archive_sha256": journal.get("archive_sha256"),
            "created": dict(Counter(
                item.get("section") for item in journal.get("planned_items", [])
                if item.get("state") in {"CREATED", "AMBIGUOUS"}
            )),
            "compensation_errors": journal.get("compensation_errors", []),
            "updated_at": journal.get("updated_at"),
            "summary": f"Import run is {journal.get('state')}",
        }
