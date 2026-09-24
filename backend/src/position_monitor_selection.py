"""Fail-closed position selection for monitor-agent executions."""

from __future__ import annotations

import math
from datetime import date
from typing import Any


class PositionSelectionError(ValueError):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


_ALIASES = {
    "account_id": ("account_id", "brokerage_account_id", "account"),
    "contract_id": (
        "contract_id", "option_contract_id", "contract_symbol",
        "occ_symbol", "osi_symbol",
    ),
    "instrument_id": ("instrument_id", "instrument_identifier", "security_id"),
}

POSITION_CONSTRAINT_FIELDS = (
    "option_type",
    "strike",
    "expiration",
    "account_id",
    "contract_id",
    "instrument_id",
    "is_paper",
)


def _first_identity(position: dict, field: str) -> Any:
    aliases = _ALIASES.get(field, (field,))
    source = position.get("source")
    for container in (position, source if isinstance(source, dict) else {}):
        for alias in aliases:
            value = container.get(alias)
            if value is not None and value != "":
                return value
    return None


def position_identity(position: dict) -> dict:
    return {
        "position_id": str(position.get("position_id") or "").strip(),
        "option_type": str(position.get("type") or "").strip().lower(),
        "strike": position.get("strike"),
        "expiration": str(position.get("expiration") or "").strip(),
        "account_id": _first_identity(position, "account_id"),
        "contract_id": _first_identity(position, "contract_id"),
        "instrument_id": _first_identity(position, "instrument_id"),
        "is_paper": position.get("is_paper") is True,
        "quantity": _first_identity(position, "quantity"),
    }


def _same(field: str, expected: Any, actual: Any) -> bool:
    if field == "strike":
        if isinstance(expected, bool) or isinstance(actual, bool):
            return False
        try:
            left = float(expected)
            right = float(actual)
        except (TypeError, ValueError):
            return False
        return math.isfinite(left) and math.isfinite(right) and left == right
    if field == "is_paper":
        return isinstance(expected, bool) and expected == bool(actual)
    return str(expected).strip().lower() == str(actual).strip().lower()


def _validate_constraint(field: str, value: Any) -> Any:
    if field not in POSITION_CONSTRAINT_FIELDS:
        raise PositionSelectionError(
            f"Unsupported position identity constraint: {field}", 400
        )
    if field == "strike":
        if isinstance(value, bool):
            raise PositionSelectionError("strike must be a finite number", 400)
        try:
            normalized = float(value)
        except (TypeError, ValueError):
            raise PositionSelectionError("strike must be a finite number", 400) from None
        if not math.isfinite(normalized):
            raise PositionSelectionError("strike must be a finite number", 400)
        return normalized
    if field == "is_paper":
        if not isinstance(value, bool):
            raise PositionSelectionError("is_paper must be a boolean", 400)
        return value
    if not isinstance(value, str) or not value.strip():
        raise PositionSelectionError(
            f"{field} must be a non-empty string", 400
        )
    normalized = value.strip()
    if field == "option_type":
        normalized = normalized.lower()
        if normalized not in {"call", "put"}:
            raise PositionSelectionError(
                "option_type must be either call or put", 400
            )
    elif field == "expiration":
        try:
            date.fromisoformat(normalized)
        except ValueError:
            raise PositionSelectionError(
                "expiration must be a valid ISO date", 400
            ) from None
    return normalized


def resolve_active_monitor_position(
    symbol_doc: dict | None,
    *,
    symbol: str,
    option_type: str,
    position_id: str | None = None,
    constraints: dict | None = None,
) -> dict:
    """Resolve one active position, never falling back after an explicit ID."""
    normalized_constraints = {
        field: _validate_constraint(field, expected)
        for field, expected in (constraints or {}).items()
    }
    if not symbol_doc:
        raise PositionSelectionError(f"Symbol {symbol} was not found", 404)

    candidates = [
        position for position in symbol_doc.get("positions", [])
        if str(position.get("type") or "").strip().lower() == option_type
        and str(position.get("status") or "").strip().lower() == "active"
    ]
    normalized_id = str(position_id or "").strip()
    if normalized_id:
        all_matches = [
            position for position in symbol_doc.get("positions", [])
            if str(position.get("position_id") or "").strip() == normalized_id
        ]
        if not all_matches:
            raise PositionSelectionError(
                f"Position {normalized_id} was not found for {symbol}", 404
            )
        selected = all_matches[0]
        if str(selected.get("status") or "").strip().lower() != "active":
            raise PositionSelectionError(
                f"Position {normalized_id} is not active", 409
            )
        if str(selected.get("type") or "").strip().lower() != option_type:
            raise PositionSelectionError(
                f"Position {normalized_id} is not an active {option_type} position",
                409,
            )
    else:
        if not candidates:
            raise PositionSelectionError(
                f"No active {option_type} positions were found for {symbol}", 404
            )
        if len(candidates) != 1:
            raise PositionSelectionError(
                f"Ambiguous {option_type} position for {symbol}: "
                f"{len(candidates)} active positions; position_id is required",
                409,
            )
        selected = candidates[0]

    identity = position_identity(selected)
    if not identity["position_id"]:
        raise PositionSelectionError(
            f"Active {option_type} position for {symbol} has no position_id", 409
        )

    for field, expected in normalized_constraints.items():
        actual = identity.get(field)
        if actual is None or not _same(field, expected, actual):
            raise PositionSelectionError(
                f"Position {identity['position_id']} {field} does not match "
                f"the requested position",
                409,
            )
    return selected
