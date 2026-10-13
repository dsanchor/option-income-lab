"""
Tests for src.dps_scorer.compute_dps_score_smoothed and
compute_dps_status_debounced -- the session-based score smoothing / status
debounce added to reduce single-session DPS noise (see dps_scorer.py module
comment above `_SMOOTHING_WINDOW`).

Hermetic: pure functions, synthetic snapshot dicts, no Cosmos/network/LLM.
"""

from src.dps_scorer import compute_dps_score_smoothed, compute_dps_status_debounced


def _snap(ts, dps_score=None, dps_status=None):
    return {"timestamp": ts, "dps_score": dps_score, "dps_status": dps_status}


def test_score_smoothed_averages_last_three_sessions():
    snaps = [
        _snap("2026-01-01T00:00:00Z", dps_score=90),
        _snap("2026-01-02T00:00:00Z", dps_score=80),
    ]
    # Today's raw score is 40 -- a single bad session.
    result = compute_dps_score_smoothed(
        snaps, current_score=40, current_timestamp="2026-01-03T00:00:00Z",
    )
    assert result == round((90 + 80 + 40) / 3, 1)


def test_score_smoothed_degrades_gracefully_with_short_history():
    # Only one prior session exists -- average over 2, not 3.
    snaps = [_snap("2026-01-01T00:00:00Z", dps_score=100)]
    result = compute_dps_score_smoothed(
        snaps, current_score=50, current_timestamp="2026-01-02T00:00:00Z",
    )
    assert result == round((100 + 50) / 2, 1)


def test_score_smoothed_collapses_multiple_runs_within_same_session():
    """A position monitored 3x in one session must count as ONE session in
    the window, not three -- otherwise a single noisy day could dominate."""
    snaps = [
        _snap("2026-01-01T00:00:00Z", dps_score=90),
        # Three runs on 2026-01-02, only the latest (70) should count.
        _snap("2026-01-02T09:00:00Z", dps_score=60),
        _snap("2026-01-02T13:00:00Z", dps_score=65),
        _snap("2026-01-02T17:00:00Z", dps_score=70),
    ]
    result = compute_dps_score_smoothed(
        snaps, current_score=40, current_timestamp="2026-01-03T00:00:00Z",
    )
    # Window=3 -> last 2 prior sessions (01-01=90, 01-02=70) + today (40).
    assert result == round((90 + 70 + 40) / 3, 1)


def test_score_smoothed_excludes_earlier_runs_from_todays_own_session():
    """An earlier monitor run from *today* must not be treated as a prior
    session -- it's superseded by today's current_score."""
    snaps = [
        _snap("2026-01-01T00:00:00Z", dps_score=90),
        _snap("2026-01-02T00:00:00Z", dps_score=80),
        _snap("2026-01-03T09:00:00Z", dps_score=20),  # earlier run, same day
    ]
    result = compute_dps_score_smoothed(
        snaps, current_score=40, current_timestamp="2026-01-03T17:00:00Z",
    )
    assert result == round((90 + 80 + 40) / 3, 1)


def test_status_debounced_requires_two_of_three_agreement():
    snaps = [
        _snap("2026-01-01T00:00:00Z", dps_status="HOLD"),
        _snap("2026-01-02T00:00:00Z", dps_status="HOLD"),
    ]
    # Single-session flip to ROLL -- not corroborated, stays HOLD-dominant
    # but since there's no majority yet (2 HOLD vs 1 ROLL), HOLD wins.
    assert compute_dps_status_debounced(
        snaps, current_status="ROLL", current_timestamp="2026-01-03T00:00:00Z",
    ) == "HOLD"


def test_status_debounced_confirms_change_after_second_session():
    snaps = [
        _snap("2026-01-01T00:00:00Z", dps_status="HOLD"),
        _snap("2026-01-02T00:00:00Z", dps_status="ROLL"),
    ]
    # Today also ROLL -- 2-of-3 agreement, status flips.
    assert compute_dps_status_debounced(
        snaps, current_status="ROLL", current_timestamp="2026-01-03T00:00:00Z",
    ) == "ROLL"


def test_status_debounced_falls_back_to_current_when_no_majority():
    snaps = [
        _snap("2026-01-01T00:00:00Z", dps_status="HOLD"),
        _snap("2026-01-02T00:00:00Z", dps_status="WATCH"),
    ]
    # Three different statuses across three sessions -- no 2-of-3 majority,
    # trust today's raw reading rather than hold a stale status forever.
    assert compute_dps_status_debounced(
        snaps, current_status="ROLL", current_timestamp="2026-01-03T00:00:00Z",
    ) == "ROLL"
