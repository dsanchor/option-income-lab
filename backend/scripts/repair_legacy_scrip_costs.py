#!/usr/bin/env python3
"""Audit and repair deterministic legacy scrip SHARE_ACQUISITION costs.

Dry-run is the default. The tool uses only persisted movement cost fields and
movement FX; it never reads share_fmv as evidence and never fetches market data.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

SCRIPT_VERSION = "legacy-scrip-costs-v1"
APPLY_CONFIRMATION = "REPAIR_LEGACY_SCRIP_COSTS"
RESTORE_CONFIRMATION = "RESTORE_LEGACY_SCRIP_COSTS"
DEFAULT_BACKUP_DIR = Path(__file__).parent / "migration_backups"
MARKER_KEY = "_repair_legacy_scrip_costs_v1"
_EVENT_TYPES = frozenset({"SCRIP_DIVIDEND", "DIVIDEND_WITH_SCRIP"})
_SYSTEM_KEYS = frozenset({"_rid", "_self", "_etag", "_attachments", "_ts"})
_Q6 = Decimal("0.000001")
_ZERO = Decimal(0)


class RepairError(RuntimeError):
    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class TargetIdentity:
    endpoint: str
    database: str
    portfolio_container: str

    def as_dict(self) -> dict[str, str]:
        return {
            "endpoint": self.endpoint,
            "database": self.database,
            "portfolio_container": self.portfolio_container,
        }


def _clean(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(value) for key, value in doc.items() if key not in _SYSTEM_KEYS
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _safe_endpoint(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if not parsed.scheme or not parsed.hostname:
        raise RepairError("COSMOSDB_ENDPOINT is invalid", 2)
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname}{port}"


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite() or parsed < _ZERO:
        return None
    return parsed


def _present(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _over_precision(value: Decimal | None) -> bool:
    return value is not None and value.as_tuple().exponent < -6


def _q6(value: Decimal) -> Decimal:
    return value.quantize(_Q6, rounding=ROUND_HALF_UP)


def _fmt(value: Decimal) -> str:
    return f"{_q6(value):.6f}"


def _iso_date(value: Any) -> str | None:
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except (TypeError, ValueError):
        return None


def _valid_sha256(value: str | None) -> bool:
    return bool(
        value
        and len(value) == 64
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _is_scrip_share(doc: dict[str, Any]) -> bool:
    return bool(
        doc.get("doc_type") == "ledger_txn"
        and doc.get("txn_type") == "BUY"
        and doc.get("ca_leg_type") == "SHARE_ACQUISITION"
        and doc.get("ca_event_type") in _EVENT_TYPES
    )


def _is_active(doc: dict[str, Any]) -> bool:
    return bool(
        doc.get("correction_status") in (None, "", "ACTIVE")
        and doc.get("is_deleted") is not True
        and doc.get("deleted_at") is None
    )


def _matches_filters(doc: dict[str, Any], filters: dict[str, Any]) -> bool:
    for field in ("account_id", "security_id", "ca_group_id", "movement_id"):
        if (
            filters.get(field)
            and str(doc.get("id" if field == "movement_id" else field) or "")
            != filters[field]
        ):
            return False
    trade_date = _iso_date(doc.get("trade_date"))
    if filters.get("date_from") and (
        trade_date is None or trade_date < filters["date_from"]
    ):
        return False
    return not (
        filters.get("date_to")
        and (trade_date is None or trade_date > filters["date_to"])
    )


def _chain_context(
    doc: dict[str, Any], group_docs: list[dict[str, Any]]
) -> dict[str, Any]:
    links = {
        key: doc.get(key)
        for key in (
            "replaces_ca_group_id",
            "replacement_group_id",
            "replaced_by_ca_group_id",
            "superseded_by_ca_group_id",
        )
        if doc.get(key)
    }
    historical = sorted(
        (
            {
                "movement_id": str(item.get("id") or ""),
                "correction_status": item.get("correction_status") or "ACTIVE",
                "replaces_ca_group_id": item.get("replaces_ca_group_id"),
                "superseded_by_ca_group_id": item.get("superseded_by_ca_group_id"),
            }
            for item in group_docs
            if item.get("id") != doc.get("id")
        ),
        key=lambda item: item["movement_id"],
    )
    concerns: list[str] = []
    if _duplicate_active_evidence_ambiguous(group_docs):
        concerns.append("DUPLICATE_ACTIVE_SHARE_LEGS_INCONSISTENT_EVIDENCE")
    if (
        doc.get("superseded_by_ca_group_id")
        or doc.get("replaced_by_ca_group_id")
        or (
            doc.get("replaces_ca_group_id")
            and doc.get("replaces_ca_group_id") == doc.get("ca_group_id")
        )
    ):
        concerns.append("ACTIVE_LEG_HAS_CONFLICTING_CORRECTION_LINK")
    return {
        "links": links,
        "related_movements": historical,
        "concerns": concerns,
        "ambiguous": bool(concerns),
    }


def _snapshot(doc: dict[str, Any]) -> dict[str, Any]:
    gross = doc.get("gross") if isinstance(doc.get("gross"), dict) else {}
    fees = doc.get("fees") if isinstance(doc.get("fees"), dict) else {}
    net = doc.get("net") if isinstance(doc.get("net"), dict) else {}
    fx = doc.get("fx") if isinstance(doc.get("fx"), dict) else {}
    return {
        "cost_basis_status": doc.get("cost_basis_status"),
        "gross": {
            "amount": gross.get("amount"),
            "currency": gross.get("currency"),
            "eur_amount": gross.get("eur_amount"),
        },
        "fees": {
            "total": fees.get("total"),
            "currency": fees.get("currency"),
            "total_eur": fees.get("total_eur"),
        },
        "net": {
            "amount": net.get("amount"),
            "currency": net.get("currency"),
            "eur_amount": net.get("eur_amount"),
        },
        "fx": {"rate": fx.get("rate"), "rate_source": fx.get("rate_source")},
    }


def _duplicate_active_evidence_ambiguous(group_docs: list[dict[str, Any]]) -> bool:
    active_share_legs = [
        doc for doc in group_docs if _is_scrip_share(doc) and _is_active(doc)
    ]
    if len(active_share_legs) < 2:
        return False
    evidence_signatures = {
        _canonical_json(
            {
                "account_id": doc.get("account_id"),
                "security_id": doc.get("security_id"),
                "trade_date": doc.get("trade_date"),
                "quantity": doc.get("quantity"),
                "cost": _snapshot(doc),
            }
        )
        for doc in active_share_legs
    }
    return len(evidence_signatures) > 1


def _diff(current: dict[str, Any], proposed: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for field in ("gross", "fees", "net"):
        before = current.get(field) if isinstance(current.get(field), dict) else {}
        after = proposed.get(field) if isinstance(proposed.get(field), dict) else {}
        for key in sorted(set(before) | set(after)):
            if before.get(key) != after.get(key):
                changes[f"{field}.{key}"] = {
                    "from": before.get(key),
                    "to": after.get(key),
                }
    if current.get("cost_basis_status") != proposed.get("cost_basis_status"):
        changes["cost_basis_status"] = {
            "from": current.get("cost_basis_status"),
            "to": proposed.get("cost_basis_status"),
        }
    return changes


def classify_document(
    doc: dict[str, Any],
    *,
    chain_ambiguous: bool = False,
) -> dict[str, Any]:
    """Pure deterministic classification; share_fmv is intentionally unread."""
    current = _snapshot(doc)
    base = {
        "classification": "REVIEW_REQUIRED",
        "reason_code": "CONTRADICTORY_AMOUNTS",
        "evidence": [],
        "proposed": None,
        "proposed_diff": {},
        "resulting_fifo_lot_cost_eur": None,
    }
    if not str(doc.get("ca_group_id") or "").strip():
        return {**base, "reason_code": "MALFORMED_ACTIVE_GROUP"}
    if chain_ambiguous:
        return {**base, "reason_code": "CORRECTION_CHAIN_AMBIGUOUS"}

    gross, fees, net, fx = (
        current["gross"],
        current["fees"],
        current["net"],
        current["fx"],
    )
    numeric_fields = {
        "gross.amount": gross.get("amount"),
        "gross.eur_amount": gross.get("eur_amount"),
        "fees.total": fees.get("total"),
        "fees.total_eur": fees.get("total_eur"),
        "net.amount": net.get("amount"),
        "net.eur_amount": net.get("eur_amount"),
        "fx.rate": fx.get("rate"),
    }
    parsed = {key: _decimal(value) for key, value in numeric_fields.items()}
    invalid = [
        key
        for key, value in numeric_fields.items()
        if _present(value) and (parsed[key] is None or _over_precision(parsed[key]))
    ]
    if invalid:
        return {**base, "reason_code": "INVALID_NUMERIC_VALUE", "evidence": invalid}

    gross_amount = parsed["gross.amount"]
    gross_eur = parsed["gross.eur_amount"]
    fee_native = parsed["fees.total"]
    fee_eur = parsed["fees.total_eur"]
    net_native = parsed["net.amount"]
    net_eur = parsed["net.eur_amount"]
    fx_rate = parsed["fx.rate"]
    gross_currency = str(gross.get("currency") or "").strip().upper()
    fee_currency = str(fees.get("currency") or gross_currency).strip().upper()
    net_currency = str(net.get("currency") or gross_currency).strip().upper()
    fees_omitted = not _present(fees.get("total")) and not _present(
        fees.get("total_eur")
    )
    if fees_omitted:
        fee_native = fee_eur = _ZERO
    elif fee_eur is None and fee_native is None:
        return {**base, "reason_code": "INVALID_NUMERIC_VALUE", "evidence": ["fees"]}
    elif fee_eur is None and fee_native == _ZERO:
        fee_eur = _ZERO

    if fee_eur is None and fee_native is not None and fee_currency != gross_currency:
        return {**base, "reason_code": "MIXED_FEE_CURRENCY"}
    if (
        fee_native is not None
        and fee_currency
        and gross_currency
        and fee_currency != gross_currency
        and fee_eur is None
    ):
        return {**base, "reason_code": "MIXED_FEE_CURRENCY"}
    if net_native is not None and (
        not gross_currency or (net_currency and net_currency != gross_currency)
    ):
        return {**base, "reason_code": "CONTRADICTORY_AMOUNTS"}
    if (
        gross_amount is not None
        and fee_native is not None
        and net_native is not None
        and _q6(net_native) != _q6(gross_amount + fee_native)
    ):
        return {
            **base,
            "reason_code": "CONTRADICTORY_AMOUNTS",
            "evidence": ["net.amount!=gross.amount+fees.total"],
        }

    derived_gross_eur: Decimal | None = None
    reason: str | None = None
    evidence: list[str] = [
        "fees_omitted_default_zero" if fees_omitted else "stored_fees"
    ]
    if (
        fee_eur is None
        and fee_native is not None
        and fee_currency == gross_currency
        and fx_rate is not None
        and fx_rate > _ZERO
    ):
        fee_eur = _q6(fee_native * fx_rate)
        evidence.append("fees.total_converted_with_movement_fx")

    if gross_eur is not None:
        derived_gross_eur = gross_eur
        reason = "KNOWN_GROSS_EUR"
        evidence.append("gross.eur_amount")
    elif gross_amount == _ZERO:
        derived_gross_eur = _ZERO
        reason = "EXPLICIT_ZERO_NATIVE_CONTRIBUTION"
        evidence.append("gross.amount=0")
    elif gross_amount is None:
        return {**base, "reason_code": "MISSING_CONTRIBUTION"}
    elif (
        gross_currency
        and gross_currency != "EUR"
        and fx_rate is not None
        and fx_rate > _ZERO
    ):
        if fee_eur is None:
            if fee_native is None or fee_currency != gross_currency:
                return {**base, "reason_code": "MIXED_FEE_CURRENCY"}
            fee_eur = _q6(fee_native * fx_rate)
        derived_gross_eur = _q6(gross_amount * fx_rate)
        reason = "DERIVED_FROM_MOVEMENT_FX"
        evidence.extend(["gross.amount", "gross.currency", "fx.rate"])
    elif (
        net_eur is not None
        and fee_eur is not None
        and net_native is not None
        and fee_native is not None
        and gross_currency
        and net_currency == gross_currency
        and (not fee_currency or fee_currency == gross_currency)
        and _q6(net_native) == _q6(gross_amount + fee_native)
        and net_eur >= fee_eur
    ):
        derived_gross_eur = _q6(net_eur - fee_eur)
        reason = "DERIVED_FROM_CANONICAL_NET"
        evidence.extend(["net.amount=gross.amount+fees.total", "net.eur_amount"])
    elif net_eur is not None:
        return {**base, "reason_code": "AMBIGUOUS_LEGACY_NET"}
    elif gross_currency != "EUR" and (fx_rate is None or fx_rate <= _ZERO):
        return {**base, "reason_code": "MISSING_MOVEMENT_FX"}
    else:
        return {**base, "reason_code": "CONTRADICTORY_AMOUNTS"}

    if fee_eur is None:
        return {**base, "reason_code": "MIXED_FEE_CURRENCY"}
    desired_net_eur = _q6(derived_gross_eur + fee_eur)
    desired_status = "ZERO_COST" if desired_net_eur == _ZERO else "COMPLETE"
    proposed = _clean(doc)
    proposed.setdefault("gross", {})["eur_amount"] = _fmt(derived_gross_eur)
    if not fees_omitted:
        proposed.setdefault("fees", {})["total_eur"] = _fmt(fee_eur)
    proposed.setdefault("net", {})["eur_amount"] = _fmt(desired_net_eur)
    if (
        gross_amount is not None
        and fee_native is not None
        and gross_currency
        and (not fee_currency or fee_currency == gross_currency)
    ):
        proposed["net"]["amount"] = _fmt(gross_amount + fee_native)
        proposed["net"]["currency"] = gross_currency
    proposed["cost_basis_status"] = desired_status

    changes = _diff(_clean(doc), proposed)
    if not changes:
        classification = "ALREADY_CANONICAL"
    else:
        classification = "AUTO_REPAIRABLE"
        only_status = set(changes) == {"cost_basis_status"}
        if only_status:
            reason = "STATUS_ONLY_RECLASSIFICATION"
    return {
        **base,
        "classification": classification,
        "reason_code": reason,
        "evidence": evidence,
        "proposed": proposed,
        "proposed_diff": changes,
        "resulting_fifo_lot_cost_eur": _fmt(desired_net_eur),
    }


def _query_ledger(container: Any) -> list[dict[str, Any]]:
    query = (
        "SELECT * FROM c WHERE c.doc_type='ledger_txn' AND c.txn_type='BUY' "
        "AND c.ca_leg_type='SHARE_ACQUISITION' "
        "AND (c.ca_event_type='SCRIP_DIVIDEND' "
        "OR c.ca_event_type='DIVIDEND_WITH_SCRIP')"
    )
    return list(container.query_items(query=query, enable_cross_partition_query=True))


def build_plan(
    portfolio_container: Any,
    *,
    target: TargetIdentity,
    filters: dict[str, Any] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    filters = {key: value for key, value in (filters or {}).items() if value}
    queried = [
        doc for doc in _query_ledger(portfolio_container) if _is_scrip_share(doc)
    ]
    by_group: dict[str, list[dict[str, Any]]] = {}
    for doc in queried:
        by_group.setdefault(str(doc.get("ca_group_id") or ""), []).append(doc)
    documents = sorted(
        (doc for doc in queried if _is_active(doc) and _matches_filters(doc, filters)),
        key=lambda item: (
            str(item.get("account_id") or ""),
            str(item.get("trade_date") or ""),
            str(item.get("ca_group_id") or ""),
            str(item.get("id") or ""),
        ),
    )
    if limit is not None:
        documents = documents[:limit]

    rows: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    for doc in documents:
        chain = _chain_context(doc, by_group.get(str(doc.get("ca_group_id") or ""), []))
        analysis = classify_document(doc, chain_ambiguous=chain["ambiguous"])
        row = {
            "event_id": doc.get("ca_group_id"),
            "share_leg_id": doc.get("id"),
            "account_id": doc.get("account_id"),
            "security_id": doc.get("security_id"),
            "trade_date": doc.get("trade_date"),
            "correction_chain": chain,
            "current": _snapshot(doc),
            "classification": analysis["classification"],
            "reason_code": analysis["reason_code"],
            "evidence": analysis["evidence"],
            "proposed_diff": analysis["proposed_diff"],
            "resulting_fifo_lot_cost_eur": analysis["resulting_fifo_lot_cost_eur"],
            "currently_fails_economics": (
                doc.get("cost_basis_status") not in {"COMPLETE", "ZERO_COST"}
                or _decimal(
                    (
                        doc.get("gross") if isinstance(doc.get("gross"), dict) else {}
                    ).get("eur_amount")
                )
                is None
            ),
        }
        rows.append(row)
        if analysis["classification"] == "AUTO_REPAIRABLE":
            actions.append(
                {
                    "id": doc["id"],
                    "account_id": doc["account_id"],
                    "_etag": doc.get("_etag", ""),
                    "event_id": doc.get("ca_group_id"),
                    "reason_code": analysis["reason_code"],
                    "evidence": analysis["evidence"],
                    "prior": _snapshot(doc),
                    "proposed": analysis["proposed"],
                    "proposed_diff": analysis["proposed_diff"],
                }
            )
    counts = {
        classification: sum(row["classification"] == classification for row in rows)
        for classification in (
            "AUTO_REPAIRABLE",
            "ALREADY_CANONICAL",
            "REVIEW_REQUIRED",
        )
    }
    payload = {
        "schema_version": 1,
        "script_version": SCRIPT_VERSION,
        "target": target.as_dict(),
        "filters": filters,
        "limit": limit,
        "rows": rows,
        "actions": actions,
        "counts": counts,
    }
    return {**payload, "sha256": _sha256(payload)}


def _backup_checksum(payload: dict[str, Any]) -> str:
    return _sha256({key: value for key, value in payload.items() if key != "sha256"})


def write_backup(
    portfolio_container: Any,
    plan: dict[str, Any],
    backup_dir: Path = DEFAULT_BACKUP_DIR,
) -> tuple[Path, dict[str, Any]]:
    run_id = str(uuid5(NAMESPACE_URL, f"{SCRIPT_VERSION}:{plan['sha256']}"))
    documents = []
    for action in plan["actions"]:
        live = portfolio_container.read_item(
            item=action["id"], partition_key=action["account_id"]
        )
        if live.get("_etag", "") != action["_etag"]:
            raise RepairError("Plan changed before backup; no writes performed")
        documents.append(
            {
                "id": action["id"],
                "partition_key": action["account_id"],
                "_etag": live.get("_etag", ""),
                "body": _clean(live),
                "proposed_diff": action["proposed_diff"],
            }
        )
    payload = {
        "schema_version": 1,
        "script_version": SCRIPT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "plan_sha256": plan["sha256"],
        "target": plan["target"],
        "documents": documents,
    }
    payload["sha256"] = _backup_checksum(payload)
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / (
        f"legacy_scrip_costs_{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}_"
        f"{plan['sha256'][:12]}.json"
    )
    try:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    except Exception as exc:
        raise RepairError(
            "Backup could not be written; no writes performed", 2
        ) from exc
    return path, payload


def _etag_replace(container: Any, raw: dict[str, Any], body: dict[str, Any]) -> bool:
    from azure.core import MatchConditions
    from azure.cosmos.exceptions import CosmosHttpResponseError

    try:
        container.replace_item(
            item=raw["id"],
            body=_clean(body),
            etag=raw.get("_etag", ""),
            match_condition=MatchConditions.IfNotModified,
        )
        return True
    except CosmosHttpResponseError as exc:
        if exc.status_code in (409, 412):
            return False
        raise


def apply_plan(
    portfolio_container: Any,
    plan: dict[str, Any],
    *,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    now: Any | None = None,
) -> tuple[dict[str, int], Path | None]:
    results = {"updated": 0, "already_repaired": 0, "cas_conflict": 0, "failed": 0}
    if not plan["actions"]:
        return results, None
    backup_path, backup = write_backup(portfolio_container, plan, backup_dir)
    clock = now or (lambda: datetime.now(timezone.utc))
    for action in plan["actions"]:
        try:
            live = portfolio_container.read_item(
                item=action["id"], partition_key=action["account_id"]
            )
            marker = live.get(MARKER_KEY)
            if isinstance(marker, dict) and marker.get("plan_sha256") == plan["sha256"]:
                results["already_repaired"] += 1
                continue
            if live.get("_etag", "") != action["_etag"]:
                results["cas_conflict"] += 1
                continue
            if not _is_scrip_share(live) or not _is_active(live):
                results["failed"] += 1
                continue
            analysis = classify_document(live)
            if analysis["classification"] != "AUTO_REPAIRABLE":
                results["cas_conflict"] += 1
                continue
            body = analysis["proposed"]
            body[MARKER_KEY] = {
                "version": SCRIPT_VERSION,
                "timestamp": clock().isoformat(),
                "run_id": backup["run_id"],
                "plan_sha256": plan["sha256"],
                "reason_code": action["reason_code"],
                "prior": action["prior"],
                "evidence": action["evidence"],
                "calculation": action["proposed_diff"],
            }
            body["updated_at"] = clock().isoformat()
            if not _etag_replace(portfolio_container, live, body):
                results["cas_conflict"] += 1
                continue
            persisted = portfolio_container.read_item(
                item=action["id"], partition_key=action["account_id"]
            )
            if persisted.get(MARKER_KEY, {}).get("run_id") != backup["run_id"]:
                results["failed"] += 1
            else:
                results["updated"] += 1
        except Exception:  # noqa: BLE001
            results["failed"] += 1
    return results, backup_path


def read_backup(path: Path, target: TargetIdentity) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RepairError("Backup cannot be read", 2) from exc
    if payload.get("sha256") != _backup_checksum(payload):
        raise RepairError("Backup checksum mismatch", 2)
    if payload.get("target") != target.as_dict():
        raise RepairError("Backup target does not match configured Cosmos target", 2)
    if payload.get("script_version") != SCRIPT_VERSION:
        raise RepairError("Backup script version is unsupported", 2)
    return payload


def restore_backup(
    portfolio_container: Any, path: Path, target: TargetIdentity
) -> dict[str, int]:
    backup = read_backup(path, target)
    result = {"restored": 0, "already_restored": 0, "cas_conflict": 0, "failed": 0}
    for entry in backup["documents"]:
        try:
            live = portfolio_container.read_item(
                item=entry["id"], partition_key=entry["partition_key"]
            )
            original = entry["body"]
            if _clean(live) == original:
                result["already_restored"] += 1
                continue
            marker = live.get(MARKER_KEY)
            if not isinstance(marker, dict) or marker.get("run_id") != backup["run_id"]:
                result["cas_conflict"] += 1
                continue
            changed_paths = set(entry["proposed_diff"]) | {
                MARKER_KEY,
                "updated_at",
            }

            def without_changed(
                body: dict[str, Any],
                ignored_paths: set[str] = changed_paths,
            ) -> dict[str, Any]:
                value = _clean(body)
                value.pop(MARKER_KEY, None)
                value.pop("updated_at", None)
                for path_key in ignored_paths:
                    if "." not in path_key:
                        value.pop(path_key, None)
                        continue
                    parent, child = path_key.split(".", 1)
                    if isinstance(value.get(parent), dict):
                        value[parent].pop(child, None)
                return value

            if without_changed(live) != without_changed(original):
                result["cas_conflict"] += 1
                continue
            if _etag_replace(portfolio_container, live, original):
                result["restored"] += 1
            else:
                result["cas_conflict"] += 1
        except Exception:  # noqa: BLE001
            result["failed"] += 1
    return result


def _build_container(database: str, portfolio_name: str) -> tuple[Any, TargetIdentity]:
    from azure.cosmos import CosmosClient

    endpoint = os.environ.get("COSMOSDB_ENDPOINT", "")
    key = os.environ.get("COSMOSDB_KEY", "")
    if not endpoint or not key:
        raise RepairError("COSMOSDB_ENDPOINT and COSMOSDB_KEY are required", 2)
    client = CosmosClient(endpoint, credential=key)
    db = client.get_database_client(database)
    return (
        db.get_container_client(portfolio_name),
        TargetIdentity(_safe_endpoint(endpoint), database, portfolio_name),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dry-run audit and deterministic repair of legacy scrip costs."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--restore", metavar="BACKUP")
    parser.add_argument("--plan-sha256")
    parser.add_argument("--backup-sha256")
    parser.add_argument("--confirm")
    parser.add_argument("--all-active", action="store_true")
    parser.add_argument("--account-id")
    parser.add_argument("--security-id")
    parser.add_argument("--ca-group-id")
    parser.add_argument("--movement-id")
    parser.add_argument("--date-from")
    parser.add_argument("--date-to")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    parser.add_argument("--database", default="stock-options-manager")
    parser.add_argument("--portfolio-container", default="portfolio")
    parser.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR))
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    for field in ("date_from", "date_to"):
        value = getattr(args, field)
        if value and not _iso_date(value):
            raise RepairError(f"--{field.replace('_', '-')} must be YYYY-MM-DD")
    if args.date_from and args.date_to and args.date_from > args.date_to:
        raise RepairError("--date-from must not be after --date-to")
    if args.limit is not None and args.limit <= 0:
        raise RepairError("--limit must be positive")
    if args.plan_sha256 and not _valid_sha256(args.plan_sha256):
        raise RepairError("--plan-sha256 must be a 64-character hexadecimal SHA-256")
    if args.backup_sha256 and not _valid_sha256(args.backup_sha256):
        raise RepairError("--backup-sha256 must be a 64-character hexadecimal SHA-256")
    if args.restore:
        if args.confirm != RESTORE_CONFIRMATION or not args.backup_sha256:
            raise RepairError(
                f"--restore requires --backup-sha256 and --confirm {RESTORE_CONFIRMATION}"
            )
        if any(
            (
                args.apply,
                args.plan_sha256,
                args.all_active,
                args.account_id,
                args.security_id,
                args.ca_group_id,
                args.movement_id,
                args.date_from,
                args.date_to,
                args.limit,
            )
        ):
            raise RepairError("--restore cannot be combined with audit/apply filters")
        return
    if args.backup_sha256:
        raise RepairError("--backup-sha256 is valid only with --restore")
    if args.apply:
        if args.confirm != APPLY_CONFIRMATION or not args.plan_sha256:
            raise RepairError(
                f"--apply requires --plan-sha256 and --confirm {APPLY_CONFIRMATION}"
            )
        filtered = any(
            (
                args.account_id,
                args.security_id,
                args.ca_group_id,
                args.movement_id,
                args.date_from,
                args.date_to,
                args.limit,
            )
        )
        if not filtered and not args.all_active:
            raise RepairError("Unfiltered apply requires --all-active")
    elif args.plan_sha256 or args.confirm:
        raise RepairError("Confirmation flags are valid only with --apply/--restore")


def _print_plan(plan: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(plan, indent=2, sort_keys=True))
        return
    fields = (
        "event_id",
        "share_leg_id",
        "account_id",
        "security_id",
        "trade_date",
        "classification",
        "reason_code",
        "resulting_fifo_lot_cost_eur",
        "currently_fails_economics",
        "evidence",
        "proposed_diff",
        "correction_chain",
        "current",
    )
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in plan["rows"]:
        writer.writerow(
            {
                key: (
                    _canonical_json(row[key])
                    if isinstance(row.get(key), (dict, list))
                    else row.get(key)
                )
                for key in fields
            }
        )
    print(buffer.getvalue(), end="")
    print(f"# plan_sha256={plan['sha256']}", file=sys.stderr)


def main(argv: Iterable[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        _validate_args(args)
        portfolio, target = _build_container(args.database, args.portfolio_container)
        if args.restore:
            backup_path = Path(args.restore)
            backup = read_backup(backup_path, target)
            if backup["sha256"].lower() != args.backup_sha256.lower():
                raise RepairError("Backup SHA changed; restore refused")
            result = restore_backup(portfolio, backup_path, target)
            print(json.dumps({"mode": "restore", **result}, indent=2, sort_keys=True))
            return 3 if result["cas_conflict"] or result["failed"] else 0
        filters = {
            "account_id": args.account_id,
            "security_id": args.security_id,
            "ca_group_id": args.ca_group_id,
            "movement_id": args.movement_id,
            "date_from": args.date_from,
            "date_to": args.date_to,
        }
        plan = build_plan(portfolio, target=target, filters=filters, limit=args.limit)
        if not args.apply:
            _print_plan({"mode": "dry-run", **plan}, args.format)
            return 0
        if plan["sha256"].lower() != args.plan_sha256.lower():
            raise RepairError("Plan fingerprint changed; rerun dry-run")
        results, backup_path = apply_plan(
            portfolio, plan, backup_dir=Path(args.backup_dir)
        )
        print(
            json.dumps(
                {
                    "mode": "apply",
                    "plan_sha256": plan["sha256"],
                    "backup": str(backup_path) if backup_path else None,
                    "counts": results,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 3 if results["cas_conflict"] or results["failed"] else 0
    except RepairError as exc:
        print(
            json.dumps({"error": str(exc), "exit_code": exc.exit_code}, sort_keys=True)
        )
        return exc.exit_code
    except Exception:  # noqa: BLE001
        print(
            json.dumps(
                {"error": "Repair failed closed", "exit_code": 2}, sort_keys=True
            )
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
