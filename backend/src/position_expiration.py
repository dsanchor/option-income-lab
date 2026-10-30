"""Validation and monitoring boundaries for in-place contract corrections."""

import math
from datetime import date, datetime, timezone


def validate_position_strike(value: object) -> float:
    message = "strike must be a finite, positive number"
    if not isinstance(value, bool) and isinstance(value, (int, float, str)):
        try:
            strike = float(value)
            if math.isfinite(strike) and strike > 0:
                return strike
        except (ValueError, OverflowError):
            pass
    raise ValueError(message)


def validate_position_expiration(value: object) -> str:
    message = "expiration must be a real date in YYYY-MM-DD format"
    try:
        if isinstance(value, str) and date.fromisoformat(value).isoformat() == value:
            return value
    except ValueError:
        pass
    raise ValueError(message)


def monitoring_after_expiration_correction(position: dict, record: dict) -> bool:
    """Keep old observations as history, not as current-contract monitoring."""
    boundary = max(
        position.get("expiration_corrected_at") or "",
        position.get("strike_corrected_at") or "",
    )
    if not boundary:
        return True
    try:
        timestamp = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
        corrected_at = datetime.fromisoformat(boundary.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        if position.get("strike_corrected_at"):
            strike = record.get("current_strike", record.get("strike"))
            if validate_position_strike(strike) != validate_position_strike(position.get("strike")):
                return False
        return timestamp > corrected_at
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
