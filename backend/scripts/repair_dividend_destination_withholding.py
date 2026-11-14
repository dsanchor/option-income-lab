#!/usr/bin/env python3
"""Remove destination WHT for exactly Fidelity NetBenefits and IBKR dividends.

From backend/: python3 -m scripts.repair_dividend_destination_withholding
Default is read-only; --apply requires a new durable --backup FILE.
--restore FILE uses FILE.applied.jsonl receipts and never overwrites later edits.
COSMOSDB_ENDPOINT and COSMOSDB_KEY are loaded only at runtime. No FX is fetched.
Exit 0: successful audit/apply/restore; exit 1: validation, storage or CAS failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from copy import deepcopy
from decimal import ROUND_HALF_UP, Decimal, DecimalException
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from azure.core import MatchConditions
from azure.core.exceptions import AzureError
from azure.cosmos import CosmosClient
from azure.cosmos.exceptions import CosmosHttpResponseError

from src.portfolio.cosmos_portfolio import _CA_REQUIRED_LEGS, _compute_manual_net

ACCOUNT_NAMES = ("Fidelity NetBenefits", "IBKR")
VERSION = "dividend-destination-withholding-v1"
SYSTEM_KEYS = {"_rid", "_self", "_etag", "_attachments", "_ts"}
Q6 = Decimal("0.000001")


class RepairError(RuntimeError):
    pass


def clean(doc: dict) -> dict:
    return {key: deepcopy(value) for key, value in doc.items() if key not in SYSTEM_KEYS}


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def active(doc: dict) -> bool:
    return (
        doc.get("correction_status") in (None, "", "ACTIVE")
        and doc.get("is_deleted") is not True
        and doc.get("deleted_at") is None
        and not doc.get("superseded_by")
        and not doc.get("superseded_by_ca_group_id")
        and not doc.get("replaced_by_ca_group_id")
    )


def number(value: Any, field: str, *, signed: bool = False) -> Decimal:
    if value is None or isinstance(value, bool):
        raise RepairError(f"{field}: missing/invalid money or FX")
    try:
        result = Decimal(str(value))
    except DecimalException as exc:
        raise RepairError(f"{field}: invalid decimal") from exc
    if not result.is_finite() or (result < 0 and not signed):
        raise RepairError(f"{field}: nonfinite/negative value")
    return result


def currency(value: Any, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch("[A-Z]{3}", value):
        raise RepairError(f"{field}: expected uppercase ISO currency")
    return value


def proposed(doc: dict) -> dict:
    """Validate before calling the permissive shared helper; adapt its native FX.

    The canonical helper determines EUR economics. Its legacy foreign-WHT
    branch returns EUR in net.amount; convert deductions using persisted
    EUR-per-native-unit FX instead, keeping original native consideration.
    """
    gross, fees, old_net = (doc.get(key) for key in ("gross", "fees", "net"))
    if not all(isinstance(block, dict) for block in (gross, fees, old_net)):
        raise RepairError("gross, fees and net must be objects")
    curr = currency(gross.get("currency"), "gross.currency")
    fee_curr = currency(fees.get("currency"), "fees.currency")
    currency(old_net.get("currency"), "net.currency")
    gross_amount = number(gross.get("amount"), "gross.amount")
    gross_eur = number(gross.get("eur_amount"), "gross.eur_amount")
    fee_amount = number(fees.get("total"), "fees.total")
    fee_eur = number(fees.get("total_eur"), "fees.total_eur")
    number(old_net.get("amount"), "net.amount", signed=True)
    number(old_net.get("eur_amount"), "net.eur_amount", signed=True)
    fx = doc.get("fx")
    if fx is not None and not isinstance(fx, dict):
        raise RepairError("fx must be an object")
    rate = (
        number(fx.get("rate"), "fx.rate") if fx is not None
        else Decimal(1) if curr == "EUR" else None
    )
    if rate is None or rate <= 0 or (curr == "EUR" and rate != 1):
        raise RepairError("positive persisted FX required; EUR FX must equal 1")
    # Rounded imported EUR amounts may differ by a cent; reject inverted,
    # placeholder or otherwise contradictory movement FX.
    tolerance = Decimal("0.01")
    if abs(gross_amount * rate - gross_eur) > tolerance:
        raise RepairError("gross and persisted FX disagree")
    fee_rate = Decimal(1) if fee_curr == "EUR" else rate if fee_curr == curr else None
    if fee_rate is None or abs(fee_amount * fee_rate - fee_eur) > tolerance:
        raise RepairError("fees currency/FX disagree")
    wht = doc.get("withholding")
    if wht is not None and not isinstance(wht, dict):
        raise RepairError("withholding must be an object or null")
    wht = deepcopy(wht) if wht is not None else {}
    for side in ("source", "destination"):
        leg = wht.get(side)
        if leg is not None:
            if not isinstance(leg, dict):
                raise RepairError(f"withholding.{side} must be an object or null")
            number(leg.get("amount_eur"), f"withholding.{side}.amount_eur")
            if leg.get("rate_pct") is not None:
                number(leg["rate_pct"], f"withholding.{side}.rate_pct")
    source_eur = number(
        (wht.get("source") or {}).get("amount_eur", "0"), "source.amount_eur"
    )
    wht["destination"] = None
    computed = _compute_manual_net("DIVIDEND", gross, fees, wht)
    if curr != "EUR":
        native_fees = fee_amount if fee_curr == curr else fee_eur / rate
        native = gross_amount - native_fees - source_eur / rate
        computed["amount"] = str(native.quantize(Q6, rounding=ROUND_HALF_UP))
    result = clean(doc)
    result["withholding"] = wht
    result["net"] = {**old_net, **computed}
    return result


def query(container: Any, sql: str, parameters: list | None = None) -> list[dict]:
    return list(container.query_items(
        query=sql, parameters=parameters or [], enable_cross_partition_query=True
    ))


def resolve_accounts(docs: list[dict]) -> list[dict]:
    accounts = []
    for name in ACCOUNT_NAMES:
        matches = [doc for doc in docs if doc.get("doc_type") == "account"
                   and doc.get("name") == name and active(doc)]
        if len(matches) != 1:
            raise RepairError(f"Account name {name!r}: expected one, found {len(matches)}")
        account = matches[0]
        if not account.get("id") or account.get("id") != account.get("account_id"):
            raise RepairError(f"Account {name!r}: invalid id/partition identity")
        accounts.append(account)
    return accounts


def validate_group(doc: dict, legs: list[dict]) -> None:
    legs = [leg for leg in legs if active(leg)]
    event = doc.get("ca_event_type")
    required = _CA_REQUIRED_LEGS.get(event)
    types = [leg.get("ca_leg_type") for leg in legs]
    identities = {(leg.get("account_id"), leg.get("security_id")) for leg in legs}
    if (
        doc.get("ca_leg_type") != "CASH_DIVIDEND"
        or required is None
        or not required.issubset(types)
        or len(types) != len(set(types))
        or len(identities) != 1
        or not doc.get("security_id")
        or any(leg.get("ca_event_type") != event for leg in legs)
        or any(leg.get("txn_type") != {
            "CASH_DIVIDEND": "DIVIDEND", "SHARE_ACQUISITION": "BUY",
            "CASH_TOP_UP": "BUY",
        }.get(leg.get("ca_leg_type")) for leg in legs)
    ):
        raise RepairError(f"{doc['id']}: inconsistent corporate-action group")


def plan(container: Any) -> dict:
    if container.read().get("partitionKey", {}).get("paths") != ["/account_id"]:
        raise RepairError("Expected portfolio partition key /account_id")
    accounts = resolve_accounts(query(container, "SELECT * FROM c WHERE c.doc_type = 'account'"))
    ids = [account["account_id"] for account in accounts]
    docs = query(
        container,
        "SELECT * FROM c WHERE c.doc_type = 'ledger_txn' "
        "AND ARRAY_CONTAINS(@accounts, c.account_id)",
        [{"name": "@accounts", "value": ids}],
    )
    entries, failures = [], []
    matched = unchanged = 0
    seen = set()
    for doc in docs:
        if (
            doc.get("doc_type") != "ledger_txn" or doc.get("account_id") not in ids
            or doc.get("txn_type") != "DIVIDEND" or not active(doc)
        ):
            continue
        matched += 1
        try:
            identity = (doc.get("account_id"), doc.get("id"))
            if not doc.get("id") or not doc.get("_etag") or identity in seen:
                raise RepairError("missing/duplicate identity or ETag")
            seen.add(identity)
            if doc.get("ca_group_id"):
                legs = query(
                    container,
                    "SELECT * FROM c WHERE c.doc_type = 'ledger_txn' AND c.ca_group_id = @group",
                    [{"name": "@group", "value": doc["ca_group_id"]}],
                )
                validate_group(doc, legs)
            after = proposed(doc)
            if after == clean(doc):
                unchanged += 1
                continue
            entries.append({
                "id": doc["id"], "partition_key": doc["account_id"], "etag": doc["_etag"],
                "before": clean(doc), "after": after,
            })
        except (RepairError, DecimalException) as exc:
            failures.append({"id": doc.get("id"), "error": str(exc)})
    return {
        "accounts": [{"id": a["id"], "name": a["name"]} for a in accounts],
        "matched": matched, "unchanged": unchanged, "entries": entries, "failures": failures,
    }


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durable_create(path: Path, text: str) -> None:
    # Exclusive creation refuses to overwrite an earlier recovery artifact.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


def journal_path(path: Path) -> Path:
    return Path(str(path) + ".applied.jsonl")


def patch_blocks(container: Any, live: dict, desired: dict) -> dict:
    operations = []
    for key in ("withholding", "net"):
        if key in desired:
            operations.append({"op": "add", "path": f"/{key}", "value": desired[key]})
        elif key in live:
            operations.append({"op": "remove", "path": f"/{key}"})
    return container.patch_item(
        item=live["id"], partition_key=live["account_id"],
        patch_operations=operations, etag=live["_etag"],
        match_condition=MatchConditions.IfNotModified,
        no_response=False,
    )


def read_entry(container: Any, entry: dict) -> dict:
    return container.read_item(item=entry["id"], partition_key=entry["partition_key"])


def apply_plan(container: Any, report: dict, target: dict, path: Path) -> None:
    if report["failures"]:
        raise RepairError("Validation failures: no writes attempted")
    if not report["entries"]:
        return
    # Re-read every candidate before backing up or writing; no partial apply on
    # already-stale audit data. Each actual patch is additionally ETag guarded.
    for entry in report["entries"]:
        live = read_entry(container, entry)
        if live.get("_etag") != entry["etag"] or clean(live) != entry["before"]:
            raise RepairError(f"{entry['id']}: changed since audit")
    payload = {"version": VERSION, "target": target, "entries": report["entries"]}
    backup_hash = digest(payload)
    if journal_path(path).exists():
        raise RepairError("Receipt journal already exists")
    durable_create(path, json.dumps({"payload": payload, "sha256": backup_hash}, indent=2))
    durable_create(journal_path(path), "")
    for entry in report["entries"]:
        live = {**entry["before"], "_etag": entry["etag"]}
        written = patch_blocks(container, live, entry["after"])
        if not written.get("_etag"):
            raise RepairError(f"{entry['id']}: patch returned no ETag; recovery needs review")
        # Receipt is persisted before readback, retaining safe recovery evidence
        # even if post-write verification fails.
        receipt = {"id": entry["id"], "partition_key": entry["partition_key"],
                   "etag": written["_etag"], "backup_sha256": backup_hash}
        with journal_path(path).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(receipt) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        verify = read_entry(container, entry)
        if verify.get("_etag") != written["_etag"] or clean(verify) != entry["after"]:
            raise RepairError(f"{entry['id']}: post-image verification failed")
        print(f"Verified applied {entry['partition_key']}/{entry['id']}")


def restore(container: Any, target: dict, path: Path) -> None:
    if container.read().get("partitionKey", {}).get("paths") != ["/account_id"]:
        raise RepairError("Expected portfolio partition key /account_id")
    backup = json.loads(path.read_text(encoding="utf-8"))
    payload = backup["payload"]
    if (
        backup["sha256"] != digest(payload) or payload["version"] != VERSION
        or payload["target"] != target
    ):
        raise RepairError("Backup integrity/version/target mismatch")
    receipts = {}
    for line in journal_path(path).read_text(encoding="utf-8").splitlines():
        receipt = json.loads(line)
        key = (receipt["partition_key"], receipt["id"])
        if receipt["backup_sha256"] != backup["sha256"] or key in receipts:
            raise RepairError("Invalid/duplicate applied receipt")
        receipts[key] = receipt
    pending = []
    for entry in payload["entries"]:
        before, after = entry["before"], entry["after"]
        if (
            before.get("id") != entry["id"]
            or before.get("account_id") != entry["partition_key"]
            or before.get("doc_type") != "ledger_txn"
            or before.get("txn_type") != "DIVIDEND" or not active(before)
            or proposed(before) != after
        ):
            raise RepairError("Invalid recovery entry")
        live = read_entry(container, entry)
        if clean(live) == before:
            continue  # Never-applied or already restored.
        receipt = receipts.get((entry["partition_key"], entry["id"]))
        if not receipt or live.get("_etag") != receipt["etag"] or clean(live) != after:
            raise RepairError(f"{entry['id']}: no matching receipt or concurrent edit; refusing restore")
        pending.append((entry, live))
    for entry, live in pending:
        written = patch_blocks(container, live, entry["before"])
        verify = read_entry(container, entry)
        if clean(verify) != entry["before"] or verify.get("_etag") != written.get("_etag"):
            raise RepairError(f"{entry['id']}: restore verification failed")
        print(f"Verified restored {entry['partition_key']}/{entry['id']}")


def build_container(database: str, portfolio_container: str) -> tuple[Any, dict]:
    endpoint = os.environ.get("COSMOSDB_ENDPOINT", "")
    key = os.environ.get("COSMOSDB_KEY", "")
    if not endpoint or not key:
        raise RepairError("COSMOSDB_ENDPOINT and COSMOSDB_KEY are required")
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise RepairError("Invalid Cosmos HTTPS endpoint")
    target = {"endpoint": f"https://{parsed.hostname}" + (f":{parsed.port}" if parsed.port else ""),
              "database": database, "portfolio_container": portfolio_container}
    client = CosmosClient(endpoint, credential=key)
    return client.get_database_client(database).get_container_client(portfolio_container), target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--restore", type=Path, metavar="FILE")
    modes.add_argument("--audit", "--dry-run", action="store_true")
    parser.add_argument("--backup", type=Path, help="New backup file; parent must exist")
    parser.add_argument("--database", default="stock-options-manager")
    parser.add_argument("--portfolio-container", default="portfolio")
    args = parser.parse_args(argv)
    if args.apply and not args.backup:
        parser.error("--apply requires --backup FILE")
    if args.backup and not args.apply:
        parser.error("--backup is only valid with --apply")
    try:
        container, target = build_container(args.database, args.portfolio_container)
        print(json.dumps({"target": target, "mode": "restore" if args.restore else
                          "apply" if args.apply else "audit"}))
        if args.restore:
            restore(container, target, args.restore)
            return 0
        report = plan(container)
        output = {key: value for key, value in report.items() if key != "entries"}
        output["changes"] = [{
            "id": e["id"], "account_id": e["partition_key"],
            "ca_group_id": e["before"].get("ca_group_id"),
            "before": {k: e["before"].get(k) for k in ("withholding", "net")},
            "after": {k: e["after"][k] for k in ("withholding", "net")},
        } for e in report["entries"]]
        output["change_count"] = len(report["entries"])
        print(json.dumps(output, indent=2, ensure_ascii=False))
        if report["failures"]:
            raise RepairError("Malformed target data: no writes attempted")
        if args.apply:
            apply_plan(container, report, target, args.backup)
        return 0
    except CosmosHttpResponseError as exc:
        print(f"Cosmos failure (HTTP {exc.status_code}); stopped, review backup/receipts",
              file=sys.stderr)
        return 1
    except AzureError as exc:
        print(f"Azure connection/storage failure ({type(exc).__name__}); stopped",
              file=sys.stderr)
        return 1
    except (RepairError, OSError, ValueError, KeyError, TypeError, DecimalException) as exc:
        print(f"Repair failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
