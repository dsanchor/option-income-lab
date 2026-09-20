from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

COSMOS_SYSTEM_KEYS = frozenset(
    {"_rid", "_self", "_etag", "_attachments", "_ts", "ttl"}
)
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T")


def _decimal_string(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("Non-finite decimal is not backup-safe")
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def _utc_string(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


_DECIMAL_FIELDS = frozenset({
    "amount", "eur_amount", "total", "total_eur", "quantity", "contracts",
    "strike", "option_strike", "rate", "rate_pct", "premium", "buyback_cost",
    "transfer_fee", "transfer_cost_basis_eur", "transfer_cost_basis_derived_eur",
    "gross", "net", "fees", "withholding",
})


def normalize(value: Any, field_name: str | None = None) -> Any:
    if isinstance(value, dict):
        return {
            str(key): normalize(item, str(key))
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if key not in COSMOS_SYSTEM_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [normalize(item, field_name) for item in value]
    if isinstance(value, datetime):
        return _utc_string(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return _decimal_string(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Non-finite number is not backup-safe")
        return _decimal_string(Decimal(str(value)))
    if isinstance(value, str):
        if _ISO_RE.match(value):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return _utc_string(parsed)
            except ValueError:
                pass
        try:
            if (
                field_name in _DECIMAL_FIELDS
                and re.fullmatch(r"-?(?:0|[1-9]\d*)(?:\.\d+)?", value)
            ):
                return _decimal_string(Decimal(value))
        except InvalidOperation:
            pass
    return value


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        normalize(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_hash(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def stable_records(records: Iterable[dict[str, Any]], key_func) -> list[dict[str, Any]]:
    return sorted((normalize(record) for record in records), key=key_func)


def section_hash(records: Iterable[dict[str, Any]], key_func) -> tuple[list[dict[str, Any]], str]:
    ordered = stable_records(records, key_func)
    return ordered, canonical_hash(ordered)


def content_hash(section_descriptors: Iterable[dict[str, Any]], scope: dict[str, Any]) -> str:
    material = {
        "schema_version": 1,
        "scope": normalize(scope),
        "sections": sorted(
            (
                {
                    "name": item["name"],
                    "schema_version": item.get("schema_version", 1),
                    "count": item["count"],
                    "sha256": item["sha256"],
                }
                for item in section_descriptors
            ),
            key=lambda item: item["name"],
        ),
    }
    return canonical_hash(material)
