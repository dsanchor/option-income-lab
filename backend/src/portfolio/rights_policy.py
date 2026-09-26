"""Compatibility policy for removed rights-associated portfolio movements."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

RIGHTS_UNSUPPORTED_MESSAGE = (
    "Rights-associated portfolio movements are no longer supported. "
    "Record converted shares through Dividend · Buy."
)

_RIGHTS_AMOUNT_FIELDS = frozenset({
    "source_derechos_amount",
    "source_derechos_eur",
    "derechos",
    "rights_amount",
    "rights_amount_eur",
    "source_rights_amount",
    "derechos_amount",
    "derechos_eur",
    "importe_en_derechos",
    "importe_derechos",
    "scrip_amount",
})
_RIGHTS_FLAG_FIELDS = frozenset({
    "is_rights_sale",
    "is_derechos_sale",
    "rights_sale",
})
_RIGHTS_FIELDS = _RIGHTS_AMOUNT_FIELDS | _RIGHTS_FLAG_FIELDS
_TYPE_FIELDS = frozenset({
    "sales_type",
    "sales_type_raw",
    "ca_leg_type",
    "ca_event_type",
    "event_type",
    "leg_type",
})
_RIGHTS_TYPE_TOKENS = frozenset({"DERECHO", "DERECHOS", "RIGHT", "RIGHTS"})
_ORDINARY_SALE_TYPES = frozenset({"ACCIONES", "STOCK", "STOCKS", "SHARE", "SHARES"})
_OBSOLETE_SALE_FIELDS = frozenset({"sales_type", "sales_type_raw", "is_rights_sale"})
_SOURCE_PAYLOAD_FIELDS = frozenset({
    "source_row",
    "source_payload",
    "source_data",
    "raw_source",
})


def _normalize_identifier(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    without_accents = "".join(
        character for character in text if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9]+", "_", without_accents.strip().lower()).strip("_")


def _normalize_marker(value: Any) -> str:
    return _normalize_identifier(value).upper()


def _is_rights_type(value: Any) -> bool:
    marker = _normalize_marker(value)
    if not marker:
        return False
    return any(token in marker.split("_") for token in _RIGHTS_TYPE_TOKENS)


def _type_field_is_associated(field: str, value: Any) -> bool:
    marker = _normalize_marker(value)
    if not marker:
        return False
    if _is_rights_type(value):
        return True
    if field in {"sales_type", "sales_type_raw"}:
        return marker not in _ORDINARY_SALE_TYPES
    return False


def _parse_legacy_amount(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise InvalidOperation
    if isinstance(value, Decimal):
        amount = value
    elif isinstance(value, (int, float)):
        amount = Decimal(str(value))
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if "," in raw:
            raw = raw.replace(".", "").replace(",", ".")
        try:
            amount = Decimal(raw)
        except (InvalidOperation, ValueError) as exc:
            raise InvalidOperation from exc
    if not amount.is_finite():
        raise InvalidOperation
    return amount


def _rights_amount_is_associated(value: Any) -> bool:
    try:
        amount = _parse_legacy_amount(value)
    except InvalidOperation:
        return True
    return amount is not None and amount != 0


def _rights_flag_is_associated(value: Any) -> bool:
    if value is None or value == "":
        return False
    if value is True:
        return True
    if value is False:
        return False
    normalized = _normalize_marker(value)
    if normalized in {"TRUE", "YES", "SI", "1"}:
        return True
    return normalized not in {"FALSE", "NO", "0"}


def _contains_rights_data(value: Any, *, in_source_payload: bool = False) -> bool:
    if isinstance(value, Mapping):
        for raw_key, nested_value in value.items():
            key = _normalize_identifier(raw_key)
            if key in _RIGHTS_AMOUNT_FIELDS and _rights_amount_is_associated(nested_value):
                return True
            if key in _RIGHTS_FLAG_FIELDS and _rights_flag_is_associated(nested_value):
                return True
            if key in _TYPE_FIELDS and _type_field_is_associated(key, nested_value):
                return True
            if (
                in_source_payload
                and ("derecho" in key or "rights" in key)
                and _rights_amount_is_associated(nested_value)
            ):
                return True
            if _contains_rights_data(
                nested_value,
                in_source_payload=in_source_payload or key in _SOURCE_PAYLOAD_FIELDS,
            ):
                return True
    elif isinstance(value, (list, tuple)):
        return any(
            _contains_rights_data(item, in_source_payload=in_source_payload)
            for item in value
        )
    return False


def is_legacy_rights_movement(movement: Mapping[str, Any]) -> bool:
    """Return whether a persisted ledger document represents a rights movement."""
    return _contains_rights_data(movement)


def payload_requests_rights(payload: Mapping[str, Any]) -> bool:
    """Detect rights-only fields/types in an inbound API or backup payload."""
    for raw_key, value in payload.items():
        key = _normalize_identifier(raw_key)
        if key in _RIGHTS_FIELDS:
            return True
        if key in _TYPE_FIELDS and _type_field_is_associated(key, value):
            return True
    return _contains_rights_data(payload)


def contains_legacy_rights_data(payload: Mapping[str, Any]) -> bool:
    """Detect a legacy record that carries an actual rights value or type."""
    return _contains_rights_data(payload)


def _strip_obsolete_rights_metadata(
    value: Any,
    *,
    in_source_payload: bool = False,
) -> Any:
    if isinstance(value, Mapping):
        cleaned: dict[Any, Any] = {}
        for raw_key, nested_value in value.items():
            key = _normalize_identifier(raw_key)
            if key in _RIGHTS_FIELDS or key in _OBSOLETE_SALE_FIELDS:
                continue
            if in_source_payload and ("derecho" in key or "rights" in key):
                continue
            cleaned[raw_key] = _strip_obsolete_rights_metadata(
                nested_value,
                in_source_payload=in_source_payload or key in _SOURCE_PAYLOAD_FIELDS,
            )
        return cleaned
    if isinstance(value, list):
        return [
            _strip_obsolete_rights_metadata(item, in_source_payload=in_source_payload)
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _strip_obsolete_rights_metadata(item, in_source_payload=in_source_payload)
            for item in value
        )
    return value


def sanitize_legacy_movement(movement: Mapping[str, Any]) -> dict[str, Any] | None:
    """Hide legacy rights records and remove obsolete metadata from safe records."""
    if contains_legacy_rights_data(movement):
        return None
    return _strip_obsolete_rights_metadata(movement)
