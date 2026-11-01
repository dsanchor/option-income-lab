"""Atomic bounded timing publication in the existing /symbol Cosmos partition.

A single versioned document contains current plus the immutable explanation
points: no reader can observe a partially published history/current pair.
ETag compare-and-swap retries merge concurrent observed/backfill publications.
At most 90 calendar days, all distinct observed points per session, one reconstructed
close only when that session has no observed point. No new Cosmos container.
Serialized documents are capped below Cosmos's 2 MB limit, reserving room for
operational failure publication without dropping legitimate observations.
"""

from __future__ import annotations

import copy
import json
import logging
from datetime import datetime, timedelta, timezone

from azure.core import MatchConditions
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError

from src.option_timing import (
    METHODOLOGY,
    MODEL_VERSION,
    TimingInputError,
    empty_sides,
    snapshot,
)
from src.option_timing_sessions import freshness

logger = logging.getLogger(__name__)
DOC_TYPE = "option_timing_history"
MAX_DOCUMENT_BYTES = 1_900_000
ERROR_RESERVE_BYTES = 8_192


def document_id(symbol: str) -> str:
    return f"option_timing:{MODEL_VERSION}:{symbol}"


class OptionTimingStore:
    def __init__(self, container):
        self.container = container

    def read(self, symbol: str) -> dict | None:
        try:
            return self.container.read_item(document_id(symbol), partition_key=symbol)
        except CosmosResourceNotFoundError:
            return None

    def publish(self, symbol: str, security_id: str, points: list[dict],
                now: datetime, *, current: dict | None = None,
                error: str | None = None, backfilled: bool = False) -> dict:
        for _ in range(5):
            old = self.read(symbol)
            doc = copy.deepcopy(old) if old else {
                "id": document_id(symbol), "symbol": symbol, "doc_type": DOC_TYPE,
                "security_id": security_id, "schema_version": 1,
                "model_version": MODEL_VERSION, "points": [], "current": None,
            }
            if doc.get("security_id") != security_id:
                raise ValueError("option_timing_security_identity_mismatch")
            cutoff = (now - timedelta(days=90)).isoformat()
            existing = {p["snapshot_id"]: p for p in doc["points"]}
            for point in points:
                existing.setdefault(point["snapshot_id"], copy.deepcopy(point))
            kept = [p for p in existing.values() if p["as_of"] >= cutoff]
            observed_days = {p["source_session"] for p in kept if p["history_kind"] == "observed"}
            kept = [p for p in kept if p["history_kind"] == "observed" or
                    p["source_session"] not in observed_days]
            doc["points"] = sorted(kept, key=lambda p: (p["as_of"], p["snapshot_id"]))
            if current is not None:
                previous = doc.get("current")
                newer = previous is None or current["as_of"] > previous["as_of"] or (
                    current["as_of"] == previous["as_of"] and
                    not (previous["provisional"] is False and current["provisional"] is True)
                )
                if newer:
                    # Repeating the same observation preserves its original evidence.
                    doc["current"] = copy.deepcopy(existing[current["snapshot_id"]])
                last_error = doc.get("last_error")
                if newer and error is None and (
                    not last_error or current["computed_at"] >= last_error["at"]
                ):
                    doc["last_error"] = None
            if error is not None:
                doc["last_error"] = {"reason": error, "at": now.isoformat()}
            if backfilled:
                doc["backfilled"] = True
            etag = doc.pop("_etag", None)
            for key in ("_rid", "_self", "_attachments", "_ts"):
                doc.pop(key, None)
            try:
                payload_bytes = len(json.dumps(doc, allow_nan=False).encode("utf-8"))
            except (TypeError, ValueError) as exc:
                raise TimingInputError("option_timing_payload_not_json_serializable") from exc
            error_only = error is not None and not points and current is None and not backfilled
            limit = MAX_DOCUMENT_BYTES if error_only else MAX_DOCUMENT_BYTES - ERROR_RESERVE_BYTES
            if payload_bytes > limit:
                raise TimingInputError("option_timing_payload_too_large")
            try:
                if old is None:
                    return self.container.create_item(doc)
                return self.container.replace_item(
                    item=doc["id"], body=doc, etag=etag,
                    match_condition=MatchConditions.IfNotModified,
                )
            except CosmosHttpResponseError as exc:
                if exc.status_code not in (409, 412):
                    raise
        raise RuntimeError("option_timing_publish_conflict")

    def current_rows(self) -> dict[str, dict]:
        docs = self.container.query_items(
            query="SELECT c.symbol, c.security_id, c.current, c.last_error FROM c "
                  "WHERE c.doc_type = @type AND c.model_version = @version",
            parameters=[{"name": "@type", "value": DOC_TYPE},
                        {"name": "@version", "value": MODEL_VERSION}],
            enable_cross_partition_query=True,
        )
        return {d["symbol"]: d for d in docs if d.get("symbol")}

    def history(self, symbol: str, security_id: str, days: int, now: datetime) -> dict:
        doc = self.read(symbol)
        if doc and doc.get("security_id") != security_id:
            raise ValueError("option_timing_security_identity_mismatch")
        if not 1 <= days <= 90:
            raise ValueError("option_timing_range_requires_1_to_90_days")
        cutoff = (now - timedelta(days=days)).isoformat()
        points = [
            copy.deepcopy(p) for p in (doc or {}).get("points", [])
            if p["as_of"] >= cutoff
        ]
        return {
            "security_id": security_id, "schema_version": 1,
            "model_version": MODEL_VERSION, "methodology": METHODOLOGY,
            "points": points,
            "last_error": (doc or {}).get("last_error"),
            "backfill_complete": bool((doc or {}).get("backfilled")),
        }


def read_current(symbol: str, security_id: str | None, eligible: bool,
                 doc: dict | None, now: datetime | None = None,
                 read_error: str | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    if not eligible:
        reason = "not_us_options_eligible"
    elif read_error:
        reason = read_error
    elif doc and doc.get("security_id") != security_id:
        reason = "security_identity_mismatch"
    elif doc and doc.get("current"):
        result = copy.deepcopy(doc["current"])
        result["freshness"] = freshness(result, now)
        if doc.get("last_error"):
            result["freshness"] = "stale"
            result["last_error"] = doc["last_error"]
            for side in ("covered_call", "cash_secured_put"):
                result[side]["reason_codes"].append("latest_refresh_failed")
                result[side]["adjustments"].append({
                    "key": "latest_refresh_failed", "label": "Latest refresh failed",
                    "reason": (
                        f"Refresh failed at {doc['last_error']['at']}: "
                        f"{doc['last_error']['reason']}. Last valid score retained "
                        "and marked stale; this does not change its calculation."
                    ),
                    "cap": None,
                })
        return result
    else:
        reason = ((doc or {}).get("last_error") or {}).get("reason", "not_yet_observed")
    result = snapshot(
        symbol, empty_sides(reason, applicable=eligible), now, now,
        now.date().isoformat(), False,
    )
    result["input_timestamp_available"] = False
    result["freshness"] = "unknown"
    if eligible and doc and doc.get("last_error"):
        result["last_error"] = doc["last_error"]
    return result


def load_current(cosmos, symbol: str, security_id: str | None, eligible: bool) -> dict:
    if not eligible:
        return read_current(symbol, security_id, False, None)
    try:
        container = getattr(cosmos, "container", None)
        doc = OptionTimingStore(container).read(symbol) if container is not None else None
        return read_current(symbol, security_id, True, doc)
    except Exception as exc:  # noqa: BLE001 - fail closed for any storage/client failure
        logger.warning("Option timing read failed for %s: %s", symbol, exc)
        return read_current(symbol, security_id, True, None, read_error="storage_read_failed")


def load_current_rows(cosmos) -> tuple[dict[str, dict], str | None]:
    try:
        container = getattr(cosmos, "container", None)
        return (OptionTimingStore(container).current_rows(), None) if container is not None else ({}, None)
    except Exception as exc:  # noqa: BLE001 - fail closed for any storage/client failure
        logger.warning("Option timing overview read failed: %s", exc)
        return {}, "storage_read_failed"
