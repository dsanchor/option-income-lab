from src.cosmos_db import is_watchlist_paused


def _should_reactivate(until: str, today: str) -> bool:
    return until < today


def test_watchlist_pause_missing_key_returns_false():
    assert is_watchlist_paused({}, today="2025-01-10") is False


def test_watchlist_pause_empty_or_missing_until_returns_false():
    assert is_watchlist_paused({"watchlist_pause": {}}, today="2025-01-10") is False
    assert is_watchlist_paused({"watchlist_pause": {"reason": "earnings"}}, today="2025-01-10") is False


def test_watchlist_pause_future_until_returns_true():
    sym_doc = {"watchlist_pause": {"until": "2025-02-01"}}

    assert is_watchlist_paused(sym_doc, today="2025-01-10") is True


def test_watchlist_pause_until_today_returns_true():
    sym_doc = {"watchlist_pause": {"until": "2025-01-10"}}

    assert is_watchlist_paused(sym_doc, today="2025-01-10") is True


def test_watchlist_pause_past_until_returns_false():
    sym_doc = {"watchlist_pause": {"until": "2025-01-05"}}

    assert is_watchlist_paused(sym_doc, today="2025-01-10") is False


def test_watchlist_reactivation_predicate_only_expired_pauses():
    assert _should_reactivate("2025-01-05", "2025-01-10") is True
    assert _should_reactivate("2025-02-01", "2025-01-10") is False
    assert _should_reactivate("2025-01-10", "2025-01-10") is False
