#!/usr/bin/env python3
"""Migrate legacy symbol_config documents to canonical MIC + SecurityMaster linkage.

Implements danny-legacy-symbol-config-migration-contract.md §3 exactly.

Scope of writes (two structural fixes only):
  1. Normalize legacy free-text exchange aliases (NYSE/NASDAQ/AMEX) to their
     ISO 10383 MIC equivalents (XNYS/XNAS/XASE) in symbol_config.exchange.
  2. Link or safely create a security_master document for the resolved MIC,
     then set symbol_config.security_id = "{MIC}:{TICKER}".

Preserves all user settings, toggles, enrichment, shares, provider_symbols
unchanged. Never writes total_shares, watchlist.*, telegram_notifications_enabled,
display_name, positions, or any field on a pre-existing security_master.

Usage::

    # Audit (default — read-only, reports what WOULD change)
    python -m scripts.migrate_legacy_symbol_config

    # Backup only (no analysis writes beyond the backup file)
    python -m scripts.migrate_legacy_symbol_config --backup-only

    # Apply (backup is mandatory and automatic; then normalize + verify)
    python -m scripts.migrate_legacy_symbol_config --apply

    # Restore from a previous backup (mutually exclusive with all other modes)
    python -m scripts.migrate_legacy_symbol_config --restore migration_backups/symbol_config_migration_20260907T120000Z.json

Exit codes:
    0  Normal run (includes "nothing to do" and docs flagged for manual review)
    1  Bad CLI arguments / mutually-exclusive flags
    2  Backup step failed  (never proceeds to write)
    3  Post-apply verification failed

DO NOT RUN AGAINST PRODUCTION without explicit authorization.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Allow `python scripts/migrate_...` without package context.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.portfolio.provider_symbols import (  # noqa: E402
    LEGACY_ALIAS_TO_MIC,
    MIC_TO_YFINANCE_SUFFIX,
)
from src.portfolio.cosmos_securities import (  # noqa: E402
    make_security_id,
    security_id_to_doc_id,
    security_id_to_ticker,
)
from src.us_exchange_eligibility import US_OPTIONS_ELIGIBLE_MICS  # noqa: E402

logger = logging.getLogger("migrate_legacy_symbol_config")

# ---------------------------------------------------------------------------
# MIC universe — all values that are already valid MIC codes in this codebase.
# Includes MIC_TO_YFINANCE_SUFFIX (explicit suffix table), US_OPTIONS_ELIGIBLE_MICS
# (the Amendment J eligible set), and the target MICs produced by this tool so
# that idempotent re-runs correctly classify already-migrated docs as
# already_canonical rather than unresolved_exchange.
# ---------------------------------------------------------------------------
_KNOWN_MICS: frozenset = (
    frozenset(MIC_TO_YFINANCE_SUFFIX)
    | US_OPTIONS_ELIGIBLE_MICS
    | frozenset(LEGACY_ALIAS_TO_MIC.values())
)

_COSMOS_SYSTEM_KEYS = frozenset({"_rid", "_self", "_etag", "_attachments", "_ts"})

DEFAULT_BACKUP_DIR = Path(__file__).parent / "migration_backups"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class BackupEntry:
    id: str
    partition_key: str
    _etag: str
    body: Dict[str, Any]


@dataclass
class MigrationBackup:
    generated_at: str
    sha256: str
    documents: List[Dict[str, Any]]  # serialised BackupEntry dicts


@dataclass
class SymbolClassification:
    """Result of classifying one symbol_config document."""
    ticker: str
    status: str  # already_canonical | needs_write | collision_ambiguous | unresolved_exchange
    effective_mic: str = ""
    needs_exchange_write: bool = False
    needs_security_id_write: bool = False
    security_action: str = ""        # "link" | "create" | ""
    raw_exchange: str = ""           # for unresolved_exchange
    candidate_security_ids: List[str] = field(default_factory=list)  # for collision_ambiguous
    snapshot_etag: str = ""          # captured at classification time for CAS
    doc_id: str = ""                 # e.g. "config_AAPL"


@dataclass
class MigrationReport:
    """Aggregate counters; reconciliation: processed + already_canonical
    + collision_ambiguous_count + unresolved_exchange_count == total_configs_scanned."""
    total_configs_scanned: int = 0
    already_canonical: int = 0
    # "processed" bucket (reconciliation); informational sub-counts below
    normalized_exchange_count: int = 0  # also serves as reconciliation "processed" counter
    security_master_linked_count: int = 0
    security_master_created_count: int = 0
    collision_ambiguous: List[Dict] = field(default_factory=list)
    unresolved_exchange: List[Dict] = field(default_factory=list)
    cas_conflicts: int = 0
    errors: int = 0
    error_details: List[str] = field(default_factory=list)
    # CLI exit code: 0=normal, 2=backup failed, 3=verification failed
    exit_code: int = 0


@dataclass
class RestoreReport:
    reverted: int = 0
    deleted_migration_docs: int = 0
    cas_conflicts: int = 0
    errors: int = 0
    error_details: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_body(doc: dict) -> dict:
    """Strip all Cosmos system keys (including _etag) from a document body."""
    return {k: v for k, v in doc.items() if k not in _COSMOS_SYSTEM_KEYS}


def _is_valid_mic(value: str) -> bool:
    """Return True if value is already a known canonical MIC (case-insensitive)."""
    return value.strip().upper() in _KNOWN_MICS


def _compute_checksum(entries: List[Dict[str, Any]]) -> str:
    """SHA-256 over the sorted (by id) entry list.

    Uses json.dumps with sort_keys=True and default separators so that the
    stored checksum can be independently verified by reading the backup file
    and hashing json.dumps(sorted_documents, sort_keys=True).
    """
    sorted_entries = sorted(entries, key=lambda e: e["id"])
    serialised = json.dumps(sorted_entries, sort_keys=True).encode()
    return hashlib.sha256(serialised).hexdigest()


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

def build_backup(symbols_container, securities_container) -> MigrationBackup:
    """Snapshot every symbol_config and security_master document.

    Captures _etag per document (needed for ETag-gated restore and
    CAS-gated writes during apply).
    """
    entries = []

    # symbol_config documents
    symbol_configs = list(symbols_container.query_items(
        query="SELECT * FROM c WHERE c.doc_type = 'symbol_config'",
        enable_cross_partition_query=True,
    ))
    for doc in symbol_configs:
        entries.append({
            "id": doc["id"],
            "partition_key": doc.get("symbol", ""),
            "_etag": doc.get("_etag", ""),
            "body": _clean_body(doc),
        })

    # security_master documents (needed to restore the pre-migration graph)
    security_masters = list(securities_container.query_items(
        query="SELECT * FROM c WHERE c.doc_type = 'security_master'",
        enable_cross_partition_query=True,
    ))
    for doc in security_masters:
        entries.append({
            "id": doc["id"],
            "partition_key": doc.get("symbol", ""),
            "_etag": doc.get("_etag", ""),
            "body": _clean_body(doc),
        })

    checksum = _compute_checksum(entries)
    return MigrationBackup(
        generated_at=datetime.now(timezone.utc).isoformat(),
        sha256=checksum,
        documents=entries,
    )


def write_backup(backup: MigrationBackup, backup_dir: Path) -> Path:
    """Serialise backup to a timestamped JSON file; return the path."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = backup_dir / f"symbol_config_migration_{ts}.json"
    payload = {
        "generated_at": backup.generated_at,
        "sha256": backup.sha256,
        "documents": sorted(backup.documents, key=lambda e: e["id"]),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def read_backup(path: Path) -> MigrationBackup:
    """Read and checksum-verify a backup file.

    Raises ValueError if the checksum does not match (file was altered).
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    stored_checksum = payload.get("sha256", "")
    actual_checksum = _compute_checksum(payload.get("documents", []))
    if stored_checksum != actual_checksum:
        raise ValueError(
            f"Backup checksum mismatch — file may have been altered.\n"
            f"  stored:   {stored_checksum}\n"
            f"  computed: {actual_checksum}"
        )
    return MigrationBackup(
        generated_at=payload.get("generated_at", ""),
        sha256=stored_checksum,
        documents=payload.get("documents", []),
    )


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_one(
    doc: dict,
    securities_by_ticker: Dict[str, List[dict]],
) -> SymbolClassification:
    """Classify a single symbol_config document.

    Does NOT write anything; mutates nothing.
    """
    ticker = doc.get("symbol", "")
    exchange = doc.get("exchange", "")
    security_id = doc.get("security_id", "")
    exchange_upper = exchange.strip().upper()
    doc_id = doc.get("id", f"config_{ticker}")
    snapshot_etag = doc.get("_etag", "")

    base = dict(ticker=ticker, doc_id=doc_id, snapshot_etag=snapshot_etag)

    # --- resolve effective MIC ---
    if _is_valid_mic(exchange_upper) and exchange_upper not in frozenset(LEGACY_ALIAS_TO_MIC):
        # Exchange is already a valid canonical MIC.
        effective_mic = exchange_upper
        needs_exchange_write = False
    elif exchange_upper in LEGACY_ALIAS_TO_MIC:
        effective_mic = LEGACY_ALIAS_TO_MIC[exchange_upper]
        needs_exchange_write = True
    else:
        # Unknown / unresolvable exchange — report, skip all writes.
        return SymbolClassification(
            **base,
            status="unresolved_exchange",
            raw_exchange=exchange,
        )

    expected_sec_id = f"{effective_mic}:{ticker}"

    # --- check if already fully canonical ---
    if not needs_exchange_write and security_id == expected_sec_id:
        # Also verify the security_master actually exists (defensive check)
        secs = securities_by_ticker.get(ticker, [])
        if any(s.get("security_id") == expected_sec_id for s in secs):
            return SymbolClassification(**base, status="already_canonical")
        # security_id set but security_master missing — fall through to create

    # --- security_master resolution ---
    secs_for_ticker = securities_by_ticker.get(ticker, [])
    exact_match = next(
        (s for s in secs_for_ticker if s.get("security_id") == expected_sec_id),
        None,
    )
    if exact_match:
        needs_security_id_write = security_id != expected_sec_id
        return SymbolClassification(
            **base,
            status="needs_write",
            effective_mic=effective_mic,
            needs_exchange_write=needs_exchange_write,
            needs_security_id_write=needs_security_id_write,
            security_action="link",
        )

    # No exact match under effective_mic.  Are there security_masters for this
    # ticker under a DIFFERENT MIC?
    different_mic = [
        s for s in secs_for_ticker
        if s.get("security_id") != expected_sec_id
    ]
    if different_mic:
        return SymbolClassification(
            **base,
            status="collision_ambiguous",
            effective_mic=effective_mic,
            candidate_security_ids=[s["security_id"] for s in different_mic],
        )

    # No security_master for this ticker under any MIC → create.
    return SymbolClassification(
        **base,
        status="needs_write",
        effective_mic=effective_mic,
        needs_exchange_write=needs_exchange_write,
        needs_security_id_write=True,
        security_action="create",
    )


def classify_all(
    symbols_container,
    securities_container,
) -> List[SymbolClassification]:
    """Classify every symbol_config in the container."""
    symbol_configs = list(symbols_container.query_items(
        query="SELECT * FROM c WHERE c.doc_type = 'symbol_config'",
        enable_cross_partition_query=True,
    ))
    all_securities = list(securities_container.query_items(
        query="SELECT * FROM c WHERE c.doc_type = 'security_master'",
        enable_cross_partition_query=True,
    ))
    by_ticker: Dict[str, List[dict]] = {}
    for sec in all_securities:
        t = sec.get("symbol", "")
        by_ticker.setdefault(t, []).append(sec)

    return [classify_one(doc, by_ticker) for doc in symbol_configs]


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------

def _create_migration_security(
    symbols_container,
    ticker: str,
    mic: str,
    display_name: str,
    now: str,
) -> dict:
    """Create a new security_master doc with the migration marker.

    Builds the canonical document shape (mirroring CosmosSecuritiesService
    .create_security) and adds created_by_migration=True so --restore can
    identify and delete it.  CosmosDB raises 409 if the id already exists in
    the partition — this is the collision guard.
    """
    from azure.cosmos.exceptions import CosmosHttpResponseError

    security_id = make_security_id(mic, ticker)
    doc_id = security_id_to_doc_id(security_id)
    doc = {
        "id": doc_id,
        "symbol": ticker,
        "doc_type": "security_master",
        "security_id": security_id,
        "legacy_symbol": ticker,
        "ticker": ticker,
        "company_name": display_name or ticker,
        "exchange_mic": mic,
        "asset_class": "Equity",
        "listing_currency": "USD",
        "status": "ACTIVE",
        "aliases": [],
        "created_at": now,
        "updated_at": now,
        "created_by_migration": True,
    }
    try:
        result = symbols_container.create_item(doc)
    except CosmosHttpResponseError as exc:
        if exc.status_code == 409:
            raise ValueError(
                f"security_master {doc_id} already exists (concurrent creation race)"
            ) from exc
        raise
    return _clean_body(result)


def _etag_replace(symbols_container, doc: dict, snapshot_etag: str) -> bool:
    """ETag-gated replace_item.

    Returns True on success, False on CAS conflict (412/409).
    Propagates other errors.
    """
    from azure.core import MatchConditions
    from azure.cosmos.exceptions import CosmosHttpResponseError

    clean = _clean_body(doc)
    try:
        symbols_container.replace_item(
            item=doc["id"],
            body=clean,
            etag=snapshot_etag,
            match_condition=MatchConditions.IfNotModified,
        )
        return True
    except CosmosHttpResponseError as exc:
        if exc.status_code in (409, 412):
            return False
        raise


# ---------------------------------------------------------------------------
# Run modes
# ---------------------------------------------------------------------------

def _build_report_from_classifications(
    classifications: List[SymbolClassification],
) -> MigrationReport:
    """Populate a MigrationReport from the classification list (audit mode)."""
    report = MigrationReport()
    report.total_configs_scanned = len(classifications)
    for c in classifications:
        if c.status == "already_canonical":
            report.already_canonical += 1
        elif c.status == "needs_write":
            # Count as "normalized_exchange_count" (the reconciliation "processed" bucket)
            report.normalized_exchange_count += 1
        elif c.status == "collision_ambiguous":
            report.collision_ambiguous.append({
                "ticker": c.ticker,
                "candidate_security_ids": c.candidate_security_ids,
            })
        elif c.status == "unresolved_exchange":
            report.unresolved_exchange.append({
                "ticker": c.ticker,
                "raw_exchange": c.raw_exchange,
            })
    return report


def run_audit(symbols_container, securities_container) -> MigrationReport:
    """Read-only: classify all docs, return report, write nothing."""
    classifications = classify_all(symbols_container, securities_container)
    return _build_report_from_classifications(classifications)


def run_backup_only(
    symbols_container,
    securities_container,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
) -> Path:
    """Backup all docs to file; return the backup path."""
    backup = build_backup(symbols_container, securities_container)
    path = write_backup(backup, backup_dir)
    return path


def run_apply(
    symbols_container,
    securities_container,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
) -> MigrationReport:
    """Backup → classify → ETag-gated write → post-flight verify.

    The backup is mandatory and unconditional: there is no way to run a
    write pass without one.
    """
    # ── Phase 1: backup (mandatory) ───────────────────────────────────
    backup = build_backup(symbols_container, securities_container)
    backup_path = write_backup(backup, backup_dir)
    logger.info("Backup written to %s", backup_path)

    # ── Phase 2: classify ─────────────────────────────────────────────
    # Re-use the snapshot from the backup for ETag values.
    etag_by_id: Dict[str, str] = {
        e["id"]: e["_etag"] for e in backup.documents
    }
    # We re-classify live (to get the freshest state description) but use
    # the backup snapshot's ETags for CAS.
    classifications = classify_all(symbols_container, securities_container)

    report = MigrationReport()
    report.total_configs_scanned = len(classifications)
    now = datetime.now(timezone.utc).isoformat()

    # ── Phase 3: write ────────────────────────────────────────────────
    for c in classifications:
        if c.status == "already_canonical":
            report.already_canonical += 1
            continue
        if c.status == "collision_ambiguous":
            report.collision_ambiguous.append({
                "ticker": c.ticker,
                "candidate_security_ids": c.candidate_security_ids,
            })
            continue
        if c.status == "unresolved_exchange":
            report.unresolved_exchange.append({
                "ticker": c.ticker,
                "raw_exchange": c.raw_exchange,
            })
            continue

        # status == "needs_write"
        try:
            ticker = c.ticker
            snapshot_etag = etag_by_id.get(c.doc_id, "")

            # Step A: create security_master if needed (before touching
            # symbol_config, so if this fails we haven't half-written).
            if c.security_action == "create":
                # Retrieve display_name from the live doc for company_name.
                live_doc = symbols_container.query_items(
                    query=(
                        "SELECT * FROM c WHERE c.id = @id"
                    ),
                    parameters=[{"name": "@id", "value": c.doc_id}],
                    partition_key=ticker,
                )
                live_list = list(live_doc)
                display_name = (live_list[0].get("display_name", "") if live_list else "") or ticker
                _create_migration_security(
                    symbols_container, ticker, c.effective_mic, display_name, now
                )
                report.security_master_created_count += 1
                logger.info("Created security_master for %s:%s", c.effective_mic, ticker)

            elif c.security_action == "link":
                report.security_master_linked_count += 1
                logger.info("Will link security_id %s:%s to existing security_master", c.effective_mic, ticker)

            # Step B: update symbol_config (exchange + security_id) with
            # ETag-gated CAS.
            if c.needs_exchange_write or c.needs_security_id_write:
                # Read current doc (fresh) and patch only the two fields.
                current = symbols_container.query_items(
                    query="SELECT * FROM c WHERE c.id = @id",
                    parameters=[{"name": "@id", "value": c.doc_id}],
                    partition_key=ticker,
                )
                current_list = list(current)
                if not current_list:
                    raise ValueError(f"symbol_config {c.doc_id} not found during write pass")
                updated = dict(current_list[0])
                if c.needs_exchange_write:
                    updated["exchange"] = c.effective_mic
                updated["security_id"] = f"{c.effective_mic}:{ticker}"
                # Use the backup-snapshot ETag for CAS (not the re-read etag,
                # to detect any change since the backup was taken).
                if not _etag_replace(symbols_container, updated, snapshot_etag):
                    report.cas_conflicts += 1
                    logger.warning(
                        "CAS conflict for %s — doc changed since backup; skipping", c.doc_id
                    )
                    continue

            report.normalized_exchange_count += 1
            if c.security_action == "link":
                # Already counted above; adjust security_master counts if
                # both link AND exchange write happened.
                pass  # security_master_linked_count already incremented

        except Exception as exc:  # noqa: BLE001
            report.errors += 1
            report.error_details.append(f"{c.ticker}: {exc}")
            logger.error("Error processing %s: %s", c.ticker, exc)

    # ── Phase 4: post-flight verification ─────────────────────────────
    try:
        verification_errors = _verify_migration(
            symbols_container, securities_container, classifications, report
        )
    except VerificationError:
        raise
    except Exception as exc:
        raise VerificationError([str(exc)], report=report) from exc

    if verification_errors:
        for err in verification_errors:
            logger.error("VERIFICATION FAILED: %s", err)
        raise VerificationError(verification_errors, report=report)

    return report


class VerificationError(RuntimeError):
    def __init__(self, errors: List[str], report: Optional["MigrationReport"] = None) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors
        self.report = report  # partial report from the failed apply, if available


def _verify_migration(
    symbols_container,
    securities_container,
    classifications: List[SymbolClassification],
    report: MigrationReport,
) -> List[str]:
    """Post-flight checks (§3.6).  Returns a list of error strings (empty = pass)."""
    errors: List[str] = []

    processed = [c for c in classifications if c.status == "needs_write"]

    # (a) No processed symbol_config still has a non-MIC exchange value
    for c in processed:
        live = list(symbols_container.query_items(
            query="SELECT * FROM c WHERE c.id = @id",
            parameters=[{"name": "@id", "value": c.doc_id}],
            partition_key=c.ticker,
        ))
        if not live:
            errors.append(f"Verification: {c.doc_id} disappeared after write")
            continue
        live_doc = live[0]
        live_exchange = live_doc.get("exchange", "")
        if not _is_valid_mic(live_exchange.upper()):
            errors.append(
                f"Verification: {c.doc_id} exchange={live_exchange!r} "
                f"is still not a canonical MIC after migration"
            )
        # (b) security_id resolves to an existing security_master
        live_sec_id = live_doc.get("security_id", "")
        if not live_sec_id:
            errors.append(f"Verification: {c.doc_id} has no security_id after migration")
            continue
        expected_ticker = security_id_to_ticker(live_sec_id)
        expected_doc_id = security_id_to_doc_id(live_sec_id)
        sec_docs = list(securities_container.query_items(
            query="SELECT * FROM c WHERE c.id = @id",
            parameters=[{"name": "@id", "value": expected_doc_id}],
            partition_key=expected_ticker,
        ))
        if not sec_docs:
            errors.append(
                f"Verification: {c.doc_id} security_id={live_sec_id!r} "
                f"does not resolve to an existing security_master"
            )

    # (c) Reconciliation count check
    collision_count = len(report.collision_ambiguous)
    unresolved_count = len(report.unresolved_exchange)
    expected_total = (
        report.normalized_exchange_count
        + report.already_canonical
        + collision_count
        + unresolved_count
    )
    if expected_total != report.total_configs_scanned:
        errors.append(
            f"Reconciliation mismatch: "
            f"processed({report.normalized_exchange_count}) + "
            f"already_canonical({report.already_canonical}) + "
            f"collision({collision_count}) + "
            f"unresolved({unresolved_count}) = {expected_total}, "
            f"expected {report.total_configs_scanned}"
        )

    return errors


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def run_restore(
    symbols_container,
    securities_container,
    backup_path: Path,
) -> RestoreReport:
    """Restore Cosmos to the exact pre-migration state captured in backup_path.

    1. Verify checksum (abort on mismatch — file may have been altered).
    2. For each document in the backup:
       - security_master with created_by_migration=True: delete_item (it
         didn't exist before migration and must be removed).
       - All other docs: unconditional replace_item back to the pre-migration body
         (or create_item if somehow the doc was deleted rather than updated).
         The backup checksum already guards integrity; per-doc ETag checking
         in restore would fail because the apply pass intentionally changed etags.
    """
    from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError

    backup = read_backup(backup_path)  # raises ValueError on checksum mismatch
    report = RestoreReport()

    for entry in backup.documents:
        doc_id = entry["id"]
        partition_key = entry["partition_key"]
        body = entry["body"]

        # Migration-created security_master docs: delete, not restore.
        if body.get("created_by_migration") and body.get("doc_type") == "security_master":
            try:
                symbols_container.delete_item(item=doc_id, partition_key=partition_key)
                report.deleted_migration_docs += 1
                logger.info("Deleted migration-created security_master: %s", doc_id)
            except CosmosResourceNotFoundError:
                logger.info("Migration-created security_master %s already absent — ok", doc_id)
            except Exception as exc:  # noqa: BLE001
                report.errors += 1
                report.error_details.append(f"delete {doc_id}: {exc}")
            continue

        # All other docs: restore unconditionally to pre-migration body.
        # The backup checksum validates the backup's integrity; using the backup's
        # (now stale) ETag for CAS would cause 412 conflicts for every migration-
        # touched document, defeating the restore.
        clean = _clean_body(body)
        try:
            symbols_container.replace_item(item=doc_id, body=clean)
            report.reverted += 1
            logger.info("Restored: %s", doc_id)
        except (CosmosResourceNotFoundError, CosmosHttpResponseError) as exc:
            if hasattr(exc, "status_code") and exc.status_code == 404 or isinstance(
                exc, CosmosResourceNotFoundError
            ):
                try:
                    symbols_container.create_item(clean)
                    report.reverted += 1
                    logger.info("Re-created during restore: %s", doc_id)
                except Exception as create_exc:  # noqa: BLE001
                    report.errors += 1
                    report.error_details.append(f"create {doc_id}: {create_exc}")
            else:
                report.errors += 1
                report.error_details.append(f"restore {doc_id}: {exc}")
        except Exception as exc:  # noqa: BLE001
            report.errors += 1
            report.error_details.append(f"restore {doc_id}: {exc}")

    return report


# ---------------------------------------------------------------------------
# Public API aliases
# Tests and importers use these simpler single-container signatures.
# ---------------------------------------------------------------------------

def audit(container) -> MigrationReport:
    """Read-only audit using a single container for both doc types."""
    return run_audit(container, container)


def apply_migration(
    container,
    dry_run: bool = True,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
) -> MigrationReport:
    """Apply or audit depending on dry_run flag.

    Returns MigrationReport with exit_code set:
      0 — normal (includes flagged/skipped items)
      3 — post-apply verification failed
    """
    if dry_run:
        report = run_audit(container, container)
        report.exit_code = 0
        return report
    try:
        report = run_apply(container, container, backup_dir=backup_dir)
        report.exit_code = 0
        return report
    except VerificationError as exc:
        # Surface the partial report (with cas_conflicts, etc.) rather than
        # an empty placeholder so callers can inspect what was processed.
        report = exc.report if exc.report is not None else MigrationReport()
        report.exit_code = 3
        return report


def backup(container, backup_path: Path) -> None:
    """Build backup and write it to a specific file path (not a timestamped directory)."""
    b = build_backup(container, container)
    payload = {
        "generated_at": b.generated_at,
        "sha256": b.sha256,
        "documents": sorted(b.documents, key=lambda e: e["id"]),
    }
    Path(backup_path).write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )


def restore_migration(container, backup_path: Path) -> RestoreReport:
    """Restore from a backup file using a single container for both doc types."""
    return run_restore(container, container, backup_path)


# ---------------------------------------------------------------------------
# Report printing
# ---------------------------------------------------------------------------

def _print_report(report: MigrationReport, *, mode: str) -> None:
    print(f"\nmigrate_legacy_symbol_config — {mode}")
    print(f"  total_configs_scanned:         {report.total_configs_scanned}")
    print(f"  already_canonical:             {report.already_canonical}")
    print(f"  processed (normalized/linked): {report.normalized_exchange_count}")
    print(f"    security_master_linked:      {report.security_master_linked_count}")
    print(f"    security_master_created:     {report.security_master_created_count}")
    print(f"  collision_ambiguous:           {len(report.collision_ambiguous)}")
    print(f"  unresolved_exchange:           {len(report.unresolved_exchange)}")
    print(f"  cas_conflicts:                 {report.cas_conflicts}")
    print(f"  errors:                        {report.errors}")
    if report.collision_ambiguous:
        print("\n  MANUAL REVIEW REQUIRED — collision_ambiguous:")
        for item in report.collision_ambiguous:
            print(f"    {item['ticker']}: candidates={item['candidate_security_ids']}")
    if report.unresolved_exchange:
        print("\n  MANUAL REVIEW REQUIRED — unresolved_exchange:")
        for item in report.unresolved_exchange:
            print(f"    {item['ticker']}: exchange={item['raw_exchange']!r}")
    if report.error_details:
        print("\n  Errors:")
        for e in report.error_details:
            print(f"    {e}")
    print()


def _print_restore_report(report: RestoreReport) -> None:
    print("\nmigrate_legacy_symbol_config — RESTORE")
    print(f"  reverted:                 {report.reverted}")
    print(f"  deleted_migration_docs:   {report.deleted_migration_docs}")
    print(f"  cas_conflicts:            {report.cas_conflicts}")
    print(f"  errors:                   {report.errors}")
    if report.error_details:
        print("\n  Errors:")
        for e in report.error_details:
            print(f"    {e}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_cosmos(args):
    """Build real CosmosDB container objects from environment config.

    Import lazily so that tests that inject fakes never trigger this path.
    Only called from main() when running against a real Cosmos account.
    """
    import os

    endpoint = os.environ.get("COSMOSDB_ENDPOINT")
    key = os.environ.get("COSMOSDB_KEY")
    database = os.environ.get("COSMOSDB_DATABASE", "option-income-lab")
    symbols_cname = os.environ.get("COSMOSDB_SYMBOLS_CONTAINER", "symbols")
    if not endpoint or not key:
        logger.error(
            "COSMOSDB_ENDPOINT and COSMOSDB_KEY environment variables are required "
            "for --apply and --restore modes.  Set them before running."
        )
        sys.exit(2)
    from azure.cosmos import CosmosClient
    client = CosmosClient(endpoint, credential=key)
    db = client.get_database_client(database)
    container = db.get_container_client(symbols_cname)
    return container, container  # both symbol_config and security_master live in the same container


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        prog="migrate_legacy_symbol_config",
        description=(
            "Normalise legacy symbol_config exchange aliases to canonical MICs "
            "and link/create security_master documents.  Default: --audit (read-only)."
        ),
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--audit",
        action="store_true",
        help="(default) Read-only audit; report what would change, write nothing.",
    )
    mode_group.add_argument(
        "--backup-only",
        dest="backup_only",
        action="store_true",
        help="Backup docs to file without analysing or writing.",
    )
    mode_group.add_argument(
        "--apply",
        action="store_true",
        help="Backup (mandatory), then normalise + link, then verify.",
    )
    mode_group.add_argument(
        "--restore",
        metavar="BACKUP_FILE",
        default=None,
        help="Restore to pre-migration state from a backup file.",
    )
    parser.add_argument(
        "--backup-dir",
        default=str(DEFAULT_BACKUP_DIR),
        help=f"Directory for backup files (default: {DEFAULT_BACKUP_DIR})",
    )
    args = parser.parse_args()

    backup_dir = Path(args.backup_dir)

    # Default mode is --audit when no flag given.
    is_audit = args.audit or (
        not args.backup_only and not args.apply and args.restore is None
    )

    if is_audit:
        symbols_container, securities_container = _build_cosmos(args)
        report = run_audit(symbols_container, securities_container)
        _print_report(report, mode="AUDIT (dry-run)")
        return 0

    if args.backup_only:
        symbols_container, securities_container = _build_cosmos(args)
        try:
            path = run_backup_only(symbols_container, securities_container, backup_dir)
            print(f"Backup written: {path}")
        except Exception as exc:  # noqa: BLE001
            logger.error("Backup failed: %s", exc)
            return 2
        return 0

    if args.apply:
        symbols_container, securities_container = _build_cosmos(args)
        try:
            report = run_apply(symbols_container, securities_container, backup_dir)
        except VerificationError as exc:
            logger.error("Post-migration verification failed: %s", exc)
            return 3
        except Exception as exc:  # noqa: BLE001
            logger.error("Backup step failed: %s", exc)
            return 2
        _print_report(report, mode="APPLY (writes performed)")
        return 0

    if args.restore:
        symbols_container, securities_container = _build_cosmos(args)
        restore_path = Path(args.restore)
        if not restore_path.exists():
            logger.error("Backup file not found: %s", restore_path)
            return 1
        try:
            report = run_restore(symbols_container, securities_container, restore_path)
        except ValueError as exc:
            logger.error("Restore aborted: %s", exc)
            return 2
        _print_restore_report(report)
        return 0

    return 0  # unreachable but satisfies linters


if __name__ == "__main__":
    sys.exit(main())
