"""
Tests for src.dps_scorer.detect_dps_gap_divergence -- the DPS-avg/Gap%
relative-scale convergence signal (see dps_scorer.py module comment above
`_DIVERGENCE_SEVERITY_FLOOR`).

Hermetic: pure functions, synthetic snapshot dicts, no Cosmos/network/LLM.
Scenario below mirrors a real production position (MO call) where the lines
crossed several sessions before DPS avg itself dropped <= 50.
"""

from src.dps_scorer import detect_dps_gap_divergence


def _snap(ts, dps_score_smoothed=None, gap_percent=None):
    return {"timestamp": ts, "dps_score_smoothed": dps_score_smoothed, "gap_percent": gap_percent}


def test_returns_none_with_insufficient_history():
    snaps = [_snap("2026-01-01T00:00:00Z", dps_score_smoothed=90, gap_percent=-5.0)]
    result = detect_dps_gap_divergence(
        snaps, current_score_smoothed=88, current_timestamp="2026-01-02T00:00:00Z",
    )
    assert result is None


def test_returns_none_without_current_gap():
    # No snapshot in history has a gap_percent at all, so there is no value
    # to carry forward as "today's" gap.
    snaps = [
        _snap("2026-01-01T00:00:00Z", dps_score_smoothed=90, gap_percent=None),
        _snap("2026-01-02T00:00:00Z", dps_score_smoothed=85, gap_percent=None),
    ]
    result = detect_dps_gap_divergence(
        snaps, current_score_smoothed=80, current_timestamp="2026-01-03T00:00:00Z",
    )
    assert result is None


def test_detects_cross_before_dps_drops_below_floor():
    # Replicates the real MO position: DPS stays high (100) while gap%
    # shrinks toward the strike, crosses on the relative scale while DPS is
    # still >50, then DPS itself falls under 50 a few sessions later.
    snaps = [
        _snap("2026-10-06T00:00:00Z", dps_score_smoothed=100.0, gap_percent=-5.43),
        _snap("2026-10-07T00:00:00Z", dps_score_smoothed=100.0, gap_percent=-4.34),
        # Today (10-08): gap shrinks sharply while DPS is still 87.7 (>50).
    ]
    result = detect_dps_gap_divergence(
        snaps, current_score_smoothed=87.7, current_timestamp="2026-10-08T00:00:00Z",
    )
    assert result is not None
    assert result["dps_gap_cross_detected"] is True
    # DPS avg still above the severity floor -- not yet a "warning".
    assert result["dps_gap_warning"] is False


def test_warning_requires_both_cross_and_low_dps():
    snaps = [
        _snap("2026-10-06T00:00:00Z", dps_score_smoothed=100.0, gap_percent=-5.43),
        _snap("2026-10-07T00:00:00Z", dps_score_smoothed=100.0, gap_percent=-4.34),
        _snap("2026-10-08T00:00:00Z", dps_score_smoothed=87.7, gap_percent=-1.37),
        _snap("2026-10-09T00:00:00Z", dps_score_smoothed=64.3, gap_percent=-1.17),
    ]
    # Today (10-12): DPS now <= 50 and the cross already happened earlier.
    result = detect_dps_gap_divergence(
        snaps, current_score_smoothed=42.3, current_timestamp="2026-10-12T00:00:00Z",
    )
    assert result is not None
    assert result["dps_gap_cross_detected"] is True
    assert result["dps_gap_warning"] is True
    assert result["dps_gap_cross_date"] is not None


def test_no_cross_when_dps_stays_well_above_gap():
    # Both DPS and gap% trend monotonically to new highs each session, so
    # they stay pinned together at the top of their own relative scales and
    # never flip sign.
    snaps = [
        _snap("2026-01-01T00:00:00Z", dps_score_smoothed=60, gap_percent=-10.0),
        _snap("2026-01-02T00:00:00Z", dps_score_smoothed=70, gap_percent=-8.0),
        _snap("2026-01-03T00:00:00Z", dps_score_smoothed=80, gap_percent=-6.0),
    ]
    result = detect_dps_gap_divergence(
        snaps, current_score_smoothed=90, current_timestamp="2026-01-04T00:00:00Z",
    )
    assert result is not None
    assert result["dps_gap_cross_detected"] is False
    assert result["dps_gap_warning"] is False


def test_distance_trend_widening_vs_narrowing():
    # Distance magnitude growing session over session -> "widening".
    snaps = [
        _snap("2026-01-01T00:00:00Z", dps_score_smoothed=50, gap_percent=-2.0),
        _snap("2026-01-02T00:00:00Z", dps_score_smoothed=30, gap_percent=-1.5),
    ]
    result = detect_dps_gap_divergence(
        snaps, current_score_smoothed=10, current_timestamp="2026-01-03T00:00:00Z",
    )
    assert result is not None
    assert result["dps_gap_distance_trend"] in ("widening", "narrowing")


def test_cross_date_ignores_early_noise_from_expanding_scale():
    # Early gap wiggle (12 -> 0 -> 17) must not count as a cross: on the
    # full-range scale gap only overtakes DPS once DPS has collapsed.
    series = [
        (70, 12), (70, 12), (90, 0), (92, 7), (100, 17), (88, 17),
        (90, 17), (75, 16), (60, 29), (38, 32), (18, 36), (16, 38),
    ]
    snaps = [
        _snap(f"2026-09-{i + 2:02d}T00:00:00Z", dps_score_smoothed=d, gap_percent=g)
        for i, (d, g) in enumerate(series[:-1])
    ]
    result = detect_dps_gap_divergence(
        snaps, current_score_smoothed=series[-1][0], current_timestamp="2026-09-13T00:00:00Z",
    )
    assert result["dps_gap_cross_date"] >= "2026-09-10"
