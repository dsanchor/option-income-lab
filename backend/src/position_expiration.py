"""Validation and monitoring boundaries for in-place expiration corrections."""

from datetime import date, datetime, timezone


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
    boundary = position.get("expiration_corrected_at")
    if not boundary:
        return True
    try:
        timestamp = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
        corrected_at = datetime.fromisoformat(boundary.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp > corrected_at
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
