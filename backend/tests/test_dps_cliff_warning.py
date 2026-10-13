"""
Tests for src.dps_scorer.detect_dps_cliff_warning — the backtested DPS cliff
early-warning signal (see backend/scripts/validate_dps_cliff_rule.py for the
methodology this was validated against).

Hermetic: pure function, synthetic snapshot dicts, no Cosmos/network/LLM.
"""

from src.dps_scorer import detect_dps_cliff_warning


def _snap(ts, dps_score=None, gap_percent=None):
    return {"timestamp": ts, "dps_score": dps_score, "gap_percent": gap_percent}


def test_no_warning_with_insufficient_scored_history():
    snaps = [
        _snap("2026-01-01T00:00:00Z", dps_score=80, gap_percent=10.0),
        _snap("2026-01-02T00:00:00Z", dps_score=50, gap_percent=3.0),
    ]
    assert detect_dps_cliff_warning(snaps) is None


def test_no_warning_when_dps_stable():
    snaps = [
        _snap("2026-01-01T00:00:00Z", dps_score=80, gap_percent=3.0),
        _snap("2026-01-02T00:00:00Z", dps_score=75, gap_percent=3.0),
        _snap("2026-01-03T00:00:00Z", dps_score=70, gap_percent=3.0),
    ]
    assert detect_dps_cliff_warning(snaps) is None


def test_warning_fires_on_persistent_cliff():
    """Classic pattern: sharp drop within 1 day near the strike, and the
    drop persists on the next scored reading instead of bouncing back."""
    snaps = [
        _snap("2026-01-01T00:00:00Z", dps_score=65, gap_percent=3.0),
        _snap("2026-01-02T00:00:00Z", dps_score=30, gap_percent=2.5),  # cliff
        _snap("2026-01-03T00:00:00Z", dps_score=25, gap_percent=2.0),  # persists
    ]
    result = detect_dps_cliff_warning(snaps)
    assert result is not None
    assert result["dps_before"] == 65
    assert result["dps_after"] == 30
    assert result["drop"] == 35
    assert result["dps_latest"] == 25


def test_no_warning_when_cliff_bounces_back():
    """Single-cycle whipsaw: DPS crashes then rebounds above the persistence
    floor on the next reading — should NOT fire (this is the dominant noise
    pattern identified during backtesting)."""
    snaps = [
        _snap("2026-01-01T00:00:00Z", dps_score=65, gap_percent=3.0),
        _snap("2026-01-02T00:00:00Z", dps_score=30, gap_percent=2.5),  # cliff
        _snap("2026-01-03T00:00:00Z", dps_score=70, gap_percent=3.5),  # bounced back
    ]
    assert detect_dps_cliff_warning(snaps) is None


def test_no_warning_when_gap_percent_too_wide():
    """Cliff + persistence, but gap% was too far from the strike at the
    cliff moment — should not fire (position wasn't actually threatened)."""
    snaps = [
        _snap("2026-01-01T00:00:00Z", dps_score=65, gap_percent=15.0),
        _snap("2026-01-02T00:00:00Z", dps_score=30, gap_percent=12.0),  # cliff, gap too wide
        _snap("2026-01-03T00:00:00Z", dps_score=25, gap_percent=11.0),
    ]
    assert detect_dps_cliff_warning(snaps) is None


def test_no_warning_when_drop_spans_too_many_days():
    """Gradual multi-day decline (not a 'cliff') should not fire."""
    snaps = [
        _snap("2026-01-01T00:00:00Z", dps_score=65, gap_percent=3.0),
        _snap("2026-01-04T00:00:00Z", dps_score=30, gap_percent=2.5),  # 3 days apart
        _snap("2026-01-05T00:00:00Z", dps_score=25, gap_percent=2.0),
    ]
    assert detect_dps_cliff_warning(snaps) is None


def test_handles_unsorted_input_and_unscored_current_cycle():
    """Snapshots can arrive newest-first (as from get_position_snapshots) and
    may include a just-written current-cycle snapshot with no dps_score yet
    — the function must sort internally and ignore unscored entries."""
    snaps = [
        _snap("2026-01-03T00:00:00Z", dps_score=None, gap_percent=1.5),  # current cycle, no DPS yet
        _snap("2026-01-02T12:00:00Z", dps_score=28, gap_percent=2.0),  # persists
        _snap("2026-01-02T00:00:00Z", dps_score=32, gap_percent=2.5),  # cliff
        _snap("2026-01-01T00:00:00Z", dps_score=65, gap_percent=3.0),
    ]
    result = detect_dps_cliff_warning(snaps)
    assert result is not None
    assert result["dps_latest"] == 28
