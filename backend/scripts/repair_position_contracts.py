#!/usr/bin/env python3
"""Audit missing canonical position quantities; explicit bounded CAS repair only.

No database connection occurs on import. See docs/web-dashboard.md for commands
and eligibility. Never run against production without separate authorization.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.position_contracts import (
    CURRENT_POSITION_SCHEMA_VERSION,
    PositionContractCountError,
    position_contract_counts,
    resolve_open_contract_count,
)

CONFIRMATION = "REPAIR_POSITION_CONTRACTS"
SCRIPT_VERSION = 2
_SYSTEM_KEYS = {"_rid", "_self", "_etag", "_attachments", "_ts"}
DEFAULT_BACKUP_DIR = Path(__file__).parent / "migration_backups"


class RepairError(ValueError):
    """Operator-safe validation failure containing no credential values."""


def _encoded(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_encoded(value)).hexdigest()


def _clean(doc: dict) -> dict:
    return {key: value for key, value in doc.items() if key not in _SYSTEM_KEYS}


def _classify(position: dict) -> tuple[dict | None, str]:
    if str(position.get("type") or "").strip().lower() not in {"call", "put"}:
        return None, "unsupported_position_type"
    if position.get("status") not in {"active", "closed", "rolled"}:
        return None, "unsupported_position_status"
    counts = position_contract_counts(position)
    total = counts["total"]
    if total["status"] != "resolved":
        return None, total["code"]
    if "contracts" in position:
        return None, "already_canonical"
    if "contract_count_repair" in position:
        return None, "existing_repair_provenance"
    if "position_schema_version" in position and (
        type(position["position_schema_version"]) is not int
        or position["position_schema_version"] != CURRENT_POSITION_SCHEMA_VERSION
    ):
        return None, "unsupported_schema"
    if counts["remaining"]["status"] != "resolved":
        return None, counts["remaining"]["code"]
    if any(field in position for field in ("open_contracts", "contracts_open")):
        try:
            resolve_open_contract_count(position)
        except PositionContractCountError as exc:
            return None, exc.code
    if counts["remaining"]["count"] > total["count"]:
        return None, "position_quantity_conflict"
    return {
        "contracts": total["count"],
        "source": total["source"],
        "warnings": total["warnings"],
    }, "candidate"


def _target_fingerprint(target: dict) -> dict:
    result = {
        key: target[key]
        for key in ("endpoint_sha256", "database", "container") if key in target
    }
    if "endpoint" in target:
        result["endpoint_sha256"] = _sha256(target["endpoint"].rstrip("/"))
    return result


def build_plan(
    container, *, target: dict, symbols: list[str] | None = None,
    all_symbols: bool = False, max_documents: int = 100, max_positions: int = 1000,
) -> dict:
    """Capture deterministic candidates and explicit skips within exact partitions."""
    if symbols is not None and (
        not isinstance(symbols, list)
        or any(not isinstance(symbol, str) or not symbol.strip() for symbol in symbols)
    ):
        raise RepairError("Exact nonempty symbol strings are required")
    symbols = sorted(set(symbols or []))
    if type(all_symbols) is not bool or all_symbols == bool(symbols):
        raise RepairError("Choose exactly one scope: exact symbols or all symbols")
    if (
        type(max_documents) is not int or not 1 <= max_documents <= 1000
        or type(max_positions) is not int or not 1 <= max_positions <= 10000
    ):
        raise RepairError("Positive bounded document/position limits are required")
    if len(symbols) > max_documents:
        raise RepairError("Symbol scope exceeds the document bound")
    documents = []
    discovered_positions = 0
    for symbol in [None] if all_symbols else symbols:
        query_args = (
            {
                "query": "SELECT * FROM c WHERE c.doc_type = 'symbol_config'",
                "enable_cross_partition_query": True,
            }
            if all_symbols else {
                "query": "SELECT * FROM c WHERE c.doc_type = 'symbol_config' AND c.symbol = @symbol",
                "parameters": [{"name": "@symbol", "value": symbol}],
                "partition_key": symbol,
            }
        )
        for doc in container.query_items(**query_args):
            if not isinstance(doc, dict):
                raise RepairError("Malformed symbol document")
            actual_symbol = doc.get("symbol")
            if (
                doc.get("doc_type") != "symbol_config"
                or not isinstance(actual_symbol, str) or not actual_symbol.strip()
                or not isinstance(doc.get("id"), str) or not doc["id"].strip()
                or (not all_symbols and actual_symbol != symbol)
            ):
                raise RepairError("Query returned a document outside the approved scope")
            documents.append(copy.deepcopy(doc))
            if len(documents) > max_documents:
                raise RepairError("Document bound exceeded; narrow --symbol scope")
            if isinstance(doc.get("positions"), list):
                discovered_positions += len(doc["positions"])
                if discovered_positions > max_positions:
                    raise RepairError("Position bound exceeded; narrow --symbol scope")
    if all_symbols:
        symbols = sorted({doc["symbol"] for doc in documents})

    actions, skips, snapshots = [], [], []
    scanned_positions = 0
    seen_documents = set()
    for doc in sorted(documents, key=lambda value: (value["symbol"], value["id"])):
        identity = {"symbol": doc["symbol"], "id": doc["id"]}
        key = (doc["symbol"], doc["id"])
        if key in seen_documents:
            raise RepairError("Duplicate symbol document identity")
        seen_documents.add(key)
        snapshots.append({**identity, "etag": doc.get("_etag"), "sha256": _sha256(doc)})
        positions = doc.get("positions", [])
        if not isinstance(positions, list):
            skips.append({**identity, "reason": "malformed_positions"})
            continue
        scanned_positions += len(positions)
        ids = Counter(
            p["position_id"] for p in positions
            if isinstance(p, dict) and isinstance(p.get("position_id"), str)
        )
        changes = []
        for index, position in enumerate(positions):
            row = {**identity, "index": index}
            if not isinstance(position, dict):
                skips.append({**row, "reason": "malformed_position"})
                continue
            pid = position.get("position_id")
            row["position_id"] = pid
            if not isinstance(pid, str) or not pid.strip() or ids[pid] != 1:
                skips.append({**row, "reason": "ambiguous_position_identity"})
                continue
            proposal, reason = _classify(position)
            if proposal is None:
                skips.append({**row, "reason": reason})
            elif not isinstance(doc.get("_etag"), str) or not doc["_etag"]:
                skips.append({**row, "reason": "missing_etag"})
            else:
                changes.append({"index": index, "position_id": pid, **proposal})
        if changes:
            actions.append({
                **identity, "etag": doc["_etag"], "snapshot_sha256": _sha256(doc),
                "changes": changes,
            })
    plan = {
        "script_version": SCRIPT_VERSION, "target": _target_fingerprint(target),
        "scope": {
            "all_symbols": all_symbols, "symbols": symbols,
            "max_documents": max_documents, "max_positions": max_positions,
        },
        "documents_scanned": len(documents), "positions_scanned": scanned_positions,
        "snapshots": snapshots, "actions": actions, "skips": skips,
    }
    return {**plan, "sha256": _sha256(plan)}


def _write_backup(backup_dir: Path, backup: dict) -> str:
    """Walk beneath cwd using pinned directories; never follow a symlink."""
    root = Path.cwd().resolve()
    directory = Path(backup_dir)
    if not directory.is_absolute():
        directory = root / directory
    try:
        parts = directory.relative_to(root).parts
    except ValueError:
        raise RepairError("Backup directory must be inside the current project directory") from None
    if ".." in parts:
        raise RepairError("Backup directory may not contain parent traversal")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_fd = os.open(root, flags)
    try:
        for part in parts:
            try:
                os.mkdir(part, mode=0o700, dir_fd=directory_fd)
                os.fsync(directory_fd)
            except FileExistsError:
                pass
            child_fd = os.open(part, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child_fd
        name = f"position_contracts_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.json"
        fd = os.open(
            name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600, dir_fd=directory_fd,
        )
        with os.fdopen(fd, "w+b") as handle:
            os.fchmod(handle.fileno(), 0o600)
            content = _encoded(backup)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            handle.seek(0)
            if handle.read() != content:
                raise RepairError("Backup verification failed; no writes performed")
        os.fsync(directory_fd)
        return str(directory / name)
    finally:
        os.close(directory_fd)


def apply_plan(
    container, plan: dict, *, expected_sha256: str, backup_dir: Path,
) -> dict:
    """Rediscover scope, preflight all originals, then backup before any CAS."""
    from azure.core import MatchConditions
    from azure.cosmos.exceptions import CosmosHttpResponseError

    payload = {key: value for key, value in plan.items() if key != "sha256"}
    if _sha256(payload) != expected_sha256 or plan["sha256"] != expected_sha256:
        raise RepairError("Plan fingerprint changed; rerun dry-run and review")
    scope = plan["scope"]
    current = build_plan(
        container, target=plan["target"],
        symbols=None if scope["all_symbols"] else scope["symbols"],
        all_symbols=scope["all_symbols"],
        max_documents=scope["max_documents"], max_positions=scope["max_positions"],
    )
    if current["sha256"] != expected_sha256:
        raise RepairError("Snapshot changed before backup; no writes performed; rerun dry-run")
    result = {"updated": 0, "cas_conflict": 0, "failed": 0, "outcomes": [], "backup": None}
    originals = []
    for action in plan["actions"]:
        raw = container.read_item(item=action["id"], partition_key=action["symbol"])
        if raw.get("_etag") != action["etag"] or _sha256(raw) != action["snapshot_sha256"]:
            raise RepairError("Snapshot changed before backup; no writes performed; rerun dry-run")
        originals.append(copy.deepcopy(raw))
    if not originals:
        return result
    backup = {"plan": plan, "documents": originals, "documents_sha256": _sha256(originals)}
    result["backup"] = _write_backup(backup_dir, backup)
    for action, raw in zip(plan["actions"], originals):
        body = copy.deepcopy(_clean(raw))
        for change in action["changes"]:
            position = body["positions"][change["index"]]
            position["contracts"] = change["contracts"]
            position.setdefault("position_schema_version", CURRENT_POSITION_SCHEMA_VERSION)
            position["contract_count_repair"] = {
                "script_version": SCRIPT_VERSION,
                "source": change["source"],
                "warnings": change["warnings"],
            }
        outcome = {"id": action["id"], "symbol": action["symbol"]}
        try:
            # Cosmos derives replace's partition from the unchanged body.symbol.
            container.replace_item(
                item=action["id"], body=body, etag=action["etag"],
                match_condition=MatchConditions.IfNotModified,
            )
            outcome["write_acknowledged"] = True
            live = container.read_item(item=action["id"], partition_key=action["symbol"])
            if _encoded(_clean(live)) != _encoded(body):
                raise RepairError("Post-write verification failed or a concurrent edit followed the write")
            result["updated"] += len(action["changes"])
            outcome["status"] = "updated"
        except CosmosHttpResponseError as exc:
            status = "cas_conflict" if exc.status_code in {404, 412} else "failed"
            result[status] += 1
            outcome.update(status=status, detail=f"Cosmos HTTP {exc.status_code}")
        except Exception as exc:  # noqa: BLE001 — report partial writes without exposing SDK request data
            result["failed"] += 1
            outcome.update(
                status="failed",
                detail=str(exc) if isinstance(exc, RepairError) else type(exc).__name__,
            )
        result["outcomes"].append(outcome)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Default; no writes or backup files")
    mode.add_argument("--apply", action="store_true")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--symbol", action="append", help="Exact symbol; repeat for a bounded batch")
    scope.add_argument("--all-symbols", action="store_true", help="Discover every symbol_config")
    parser.add_argument("--max-documents", type=int, default=100)
    parser.add_argument("--max-positions", type=int, default=1000)
    parser.add_argument("--database", default="stock-options-manager")
    parser.add_argument("--symbols-container", default="symbols")
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        if args.apply and (not args.plan_sha256 or args.confirm != CONFIRMATION):
            raise RepairError(f"--apply requires --plan-sha256 and --confirm {CONFIRMATION}")
        if not args.apply and (args.plan_sha256 or args.confirm):
            raise RepairError("Confirmation flags require --apply")
        symbols = args.symbol or []
        if (
            any(not value.strip() for value in symbols)
            or not 1 <= args.max_documents <= 1000
            or not 1 <= args.max_positions <= 10000
            or len(set(symbols)) > args.max_documents
        ):
            raise RepairError("Symbols must be nonempty and scope/bounds finite: 1000 documents / 10000 positions")
        endpoint = os.environ.get("COSMOSDB_ENDPOINT", "")
        key = os.environ.get("COSMOSDB_KEY", "")
        if not endpoint or not key:
            raise RepairError("COSMOSDB_ENDPOINT and COSMOSDB_KEY environment variables are required")
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment
        ):
            raise RepairError("A credential-free HTTPS COSMOSDB_ENDPOINT is required")
        from azure.cosmos import CosmosClient

        target = {
            "endpoint_sha256": _sha256(endpoint.rstrip("/")), "database": args.database,
            "container": args.symbols_container,
        }
        with CosmosClient(endpoint, credential=key, logging_enable=False) as client:
            container = client.get_database_client(args.database).get_container_client(args.symbols_container)
            if container.read().get("partitionKey", {}).get("paths") != ["/symbol"]:
                raise RepairError("Expected /symbol partition key")
            plan = build_plan(
                container, target=target, symbols=symbols, all_symbols=args.all_symbols,
                max_documents=args.max_documents, max_positions=args.max_positions,
            )
            if not args.apply:
                print(json.dumps({"mode": "dry-run", **plan}, indent=2, sort_keys=True))
                return 0
            result = apply_plan(
                container, plan, expected_sha256=args.plan_sha256, backup_dir=args.backup_dir,
            )
            print(json.dumps({"mode": "apply", "plan": plan, "result": result}, indent=2, sort_keys=True))
            return 3 if result["failed"] or result["cas_conflict"] else 0
    except RepairError as exc:
        print(f"Repair aborted: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — SDK failures must not expose request credentials
        print(f"Repair aborted: {type(exc).__name__}; check target configuration and connectivity", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
