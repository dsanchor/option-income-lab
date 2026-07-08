def _has_position_active_on(active_positions, event_date_str: str) -> bool:
    """Mirrors sync_calendar's per-event active-position predicate."""
    for p in active_positions:
        try:
            exp_str = p["expiration"][:10]
            if exp_str >= event_date_str:
                return True
        except (TypeError, IndexError):
            continue
    return False


def test_calendar_active_position_requires_expiration_covering_event_date():
    active_positions = [{"status": "active", "expiration": "2026-08-21"}]

    assert _has_position_active_on(active_positions, "2026-08-10") is True
    assert _has_position_active_on(active_positions, "2026-08-21") is True
    assert _has_position_active_on(active_positions, "2026-09-23") is False
