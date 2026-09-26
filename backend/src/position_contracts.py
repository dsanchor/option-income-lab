"""Authoritative option-position open-contract quantity resolution."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


class PositionContractCountError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class OpenContractCount:
    contracts: int
    quantity_source: str
    warnings: tuple[str, ...] = ()


_REMAINING_FIELDS = ("open_contracts", "contracts_open")
_BASE_FIELDS = ("contracts", "quantity")
CURRENT_POSITION_SCHEMA_VERSION = 2

# Quantity persistence was introduced immediately after repository baseline
# 62f4ad5 (2026-09-26T10:44:42Z). Unversioned documents at or after this
# boundary are not legacy: they are incomplete current-schema records.
_LEGACY_QUANTITY_CUTOFF = datetime(2026, 9, 26, 10, 44, 42, tzinfo=UTC)
_POSITION_ID_TIMESTAMP_RE = re.compile(r"_(\d{8}_\d{6})$")


def _parse_contract_count(value: Any, *, field: str, allow_negative: bool) -> int:
    if isinstance(value, bool):
        raise PositionContractCountError(
            "position_quantity_invalid",
            f"Position field '{field}' must be a finite whole-number contract count, not a boolean.",
        )
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        raise PositionContractCountError(
            "position_quantity_invalid",
            f"Position field '{field}' must be a finite whole-number contract count.",
        )
    if not parsed.is_finite():
        raise PositionContractCountError(
            "position_quantity_invalid",
            f"Position field '{field}' must be finite.",
        )
    if parsed != parsed.to_integral_value():
        raise PositionContractCountError(
            "position_quantity_fractional",
            f"Position field '{field}' must be a whole number; fractional option contracts are not supported.",
        )
    if parsed == 0:
        raise PositionContractCountError(
            "position_quantity_zero",
            f"Position field '{field}' is zero, so this position has no open contracts to roll.",
        )
    if parsed < 0 and not allow_negative:
        raise PositionContractCountError(
            "position_quantity_invalid",
            f"Position field '{field}' is negative, but the position is not identified as a short CALL or PUT.",
        )
    return abs(int(parsed))


def _valid_legacy_position(position: Mapping[str, Any]) -> bool:
    """Recognize an unversioned position provably emitted before schema v2.

    The original writer encoded its UTC creation second in both ``position_id``
    and ``opened_at``. Requiring those independent persisted values to agree
    before the quantity-persistence boundary prevents a newly created
    quantity-less position from being inferred as legacy merely by omission.
    """
    if "position_schema_version" in position:
        return False
    position_id = position.get("position_id")
    if not isinstance(position_id, str) or not position_id.startswith("pos_"):
        return False
    timestamp_match = _POSITION_ID_TIMESTAMP_RE.search(position_id)
    if timestamp_match is None:
        return False
    if str(position.get("type") or "").strip().lower() not in {"call", "put"}:
        return False
    if position.get("status") != "active" or "notes" not in position:
        return False
    try:
        strike = Decimal(str(position.get("strike")))
        if not strike.is_finite() or strike <= 0:
            return False
        date.fromisoformat(str(position.get("expiration")))
        opened_at = datetime.fromisoformat(
            str(position.get("opened_at")).replace("Z", "+00:00")
        )
        if opened_at.tzinfo is None:
            return False
        opened_at = opened_at.astimezone(UTC)
        id_timestamp = datetime.strptime(
            timestamp_match.group(1), "%Y%m%d_%H%M%S"
        ).replace(tzinfo=UTC)
    except (InvalidOperation, TypeError, ValueError):
        return False
    return (
        opened_at < _LEGACY_QUANTITY_CUTOFF
        and id_timestamp < _LEGACY_QUANTITY_CUTOFF
        and abs((opened_at - id_timestamp).total_seconds()) < 1
    )


def resolve_open_contract_count(position: Mapping[str, Any]) -> OpenContractCount:
    """Resolve the exact current open contract count for one position.

    Remaining/open fields take precedence over original quantity fields. When
    aliases at the same precedence are both present, they must agree after
    short-position sign normalization.
    """
    if not isinstance(position, Mapping):
        raise PositionContractCountError(
            "position_quantity_invalid",
            "Position data is malformed; expected an object containing an open contract count.",
        )

    allow_negative = str(position.get("type") or position.get("option_type") or "").strip().lower() in {
        "call",
        "put",
    }
    for fields in (_REMAINING_FIELDS, _BASE_FIELDS):
        present = [(field, position[field]) for field in fields if field in position]
        if not present:
            continue
        parsed = [
            (field, _parse_contract_count(value, field=field, allow_negative=allow_negative))
            for field, value in present
        ]
        counts = {count for _, count in parsed}
        if len(counts) != 1:
            names = ", ".join(f"'{field}'" for field, _ in parsed)
            raise PositionContractCountError(
                "position_quantity_conflict",
                f"Position contract-count fields {names} disagree; correct the position before simulating a roll.",
            )
        source, count = parsed[0]
        return OpenContractCount(contracts=count, quantity_source=source)

    if _valid_legacy_position(position):
        return OpenContractCount(
            contracts=1,
            quantity_source="legacy_implicit_one",
            warnings=(
                "This legacy position predates stored contract quantities; the repository's historical one-contract default was used.",
            ),
        )

    raise PositionContractCountError(
        "position_quantity_missing",
        "Position is missing an open contract count. Set 'contracts' (or 'open_contracts' after a partial close) on this exact position before simulating a roll.",
    )
