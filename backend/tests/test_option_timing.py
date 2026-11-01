"""Behavioral tests for the independent, causal covered CALL/CSP rubric."""

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest
from src.option_timing import TimingInputError, band, calculate_sides, features
from src.option_timing_job import prepare_history, reconstruct
from src.option_timing_sessions import (
    freshness,
    is_session,
    latest_session,
    observation_cutoff,
    session_close,
    session_open,
)


def market_history(closes=None, end="2026-10-30", bars=320):
    dates = [d for d in pd.bdate_range(end=end, periods=bars + 30) if is_session(d.date())][-bars:]
    if closes is None:
        t = np.arange(len(dates))
        closes = 100 + .025 * t + .4 * np.sin(t / 3)
    closes = np.asarray(closes)
    opens = np.r_[closes[0], closes[:-1]]
    return pd.DataFrame({
        "Open": opens, "High": np.maximum(opens, closes) + .6,
        "Low": np.minimum(opens, closes) - .6, "Close": closes,
        "Volume": np.full(len(closes), 1e6),
    }, index=pd.DatetimeIndex(dates))


def utc(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


@pytest.mark.parametrize(("score", "expected"), [
    (None, None), (0, "red"), (5, "red"), (24.99, "red"),
    (25, "orange"), (50, "yellow"), (75, "green"), (100, "green"),
])
def test_band_boundaries(score, expected):
    assert band(score) == expected


def test_rising_recovery_not_falling_oversold():
    base = market_history()["Close"].to_numpy()
    falling = np.r_[base[:-20], np.linspace(base[-21], base[-21] - 12, 20)]
    recovery = np.r_[base[:-20], np.linspace(base[-21], base[-21] - 8, 14),
                     np.linspace(base[-21] - 8, base[-21] - 5, 6)]
    down = calculate_sides(market_history(falling))["cash_secured_put"]
    up = calculate_sides(market_history(recovery))["cash_secured_put"]
    assert down["score"] <= 20
    assert "falling_knife" in down["reason_codes"]
    assert up["score"] > down["score"] + 20
    assert "falling_knife" not in up["reason_codes"]
    assert features(market_history(recovery))["rsi14_change5"] > 0
    assert features(market_history(falling))["rsi14_change5"] < 0


def test_call_rejection_beats_accelerated_breakout():
    base = market_history()["Close"].to_numpy()
    rejection = market_history(base)
    rejection.iloc[-1, rejection.columns.get_loc("High")] += .8
    breakout = market_history(np.r_[base[:-4], base[-5] + np.array([1, 3, 5, 8])])
    rejected = calculate_sides(rejection)["covered_call"]
    broken = calculate_sides(breakout)["covered_call"]
    assert rejected["score"] > 50
    assert broken["score"] <= 20
    assert "accelerated_breakout" in broken["reason_codes"]
    assert broken["adjustments"][0]["cap"] == 20
    assert features(breakout)["resistance_breaks2"]


def test_support_loss_cap_and_prior_atr_not_event_inflated():
    base = market_history()["Close"].to_numpy()
    loss = market_history(np.r_[base[:-3], base[-4] - np.array([1, 3, 5])])
    score = calculate_sides(loss)["cash_secured_put"]
    assert score["score"] <= 20
    assert "confirmed_support_loss" in score["reason_codes"]
    f = features(loss)
    huge = loss.copy()
    huge.iloc[-1, huge.columns.get_loc("High")] += 20
    assert features(huge)["prior_atr14"] == f["prior_atr14"]
    assert features(huge)["atr14"] > f["atr14"]


def test_both_bad_structural_dislocation_not_complements():
    base = market_history()["Close"].to_numpy()
    dislocation = market_history(np.r_[base[:-1], base[-2] - 15])
    for side in calculate_sides(dislocation).values():
        assert side["score"] <= 15
        assert "severe_structural_deterioration" in side["reason_codes"]
    ordinary = calculate_sides(market_history())
    assert ordinary["covered_call"]["score"] > 60
    assert ordinary["cash_secured_put"]["score"] > 60
    assert sum(side["score"] for side in ordinary.values()) > 100


@pytest.mark.parametrize(("mutation", "reason"), [
    ("warmup", "warmup_requires_220_valid_bars"),
    ("missing", "missing_ohlcv"),
    ("nan", "non_finite_ohlcv"),
    ("bad_geometry", "invalid_ohlcv_geometry_or_volume"),
    ("zero_volume", "invalid_ohlcv_geometry_or_volume"),
    ("current_zero_volume", "missing_volume_context"),
    ("unordered", "unordered_or_duplicate_sessions"),
])
def test_required_inputs_fail_closed(mutation, reason):
    h = market_history()
    if mutation == "warmup":
        h = h.iloc[-219:]
    elif mutation == "missing":
        h = h.drop(columns="Volume")
    elif mutation == "nan":
        h.iloc[-1, h.columns.get_loc("Low")] = np.nan
    elif mutation == "bad_geometry":
        h.iloc[-1, h.columns.get_loc("High")] = 1
    elif mutation == "zero_volume":
        h["Volume"] = 0
    elif mutation == "current_zero_volume":
        h.iloc[-1, h.columns.get_loc("Volume")] = 0
    elif mutation == "unordered":
        h = h.iloc[::-1]
    with pytest.raises(TimingInputError, match=reason):
        calculate_sides(h)


def test_full_warmup_and_explained_weighted_transformation():
    score = calculate_sides(market_history(bars=220))
    for side in score.values():
        assert sum(f["weight"] for f in side["factors"]) == pytest.approx(1)
        assert side["base_score"] == pytest.approx(sum(f["contribution"] for f in side["factors"]))
        assert side["score"] == pytest.approx(side["base_score"], abs=.005)
        for factor in side["factors"]:
            assert 0 <= factor["score"] <= 100
            assert factor["contribution"] == pytest.approx(
                factor["score"] * factor["weight"], abs=.0001,
            )
            assert "Transformation:" in factor["evidence"][1]
            assert "=" in factor["evidence"][0]


def test_future_bars_do_not_change_past_reconstruction_or_explanation():
    h = market_history()
    initial = reconstruct(h.iloc[:-5], utc("2026-10-23T22:00:00"))
    later = reconstruct(h, utc("2026-10-30T22:00:00"))
    before = {p["source_session"]: p for p in initial}
    after = {p["source_session"]: p for p in later}
    assert before
    for day in before.keys() & after.keys():
        point = before[day]
        assert point["covered_call"] == after[day]["covered_call"]
        assert point["cash_secured_put"] == after[day]["cash_secured_put"]
        assert point["as_of"] == after[day]["as_of"]


def test_reconstruction_only_completed_daily_sessions_not_intraday():
    h = market_history(end="2026-10-30")
    now = utc("2026-10-30T17:00:00")
    points = reconstruct(h, now)
    assert points[-1]["source_session"] == "2026-10-29"
    assert len({p["source_session"] for p in points}) == len(points)
    assert all(p["history_kind"] == "reconstructed" and not p["provisional"] for p in points)
    assert all(datetime.fromisoformat(p["as_of"]) < now for p in points)
    assert len(points) <= 65


def test_current_session_live_bar_and_premarket_previous_close():
    h = market_history()
    premarket = utc("2026-10-30T12:00:00")
    selected = prepare_history(h, premarket)
    assert selected.index[-1].date().isoformat() == "2026-10-29"
    as_of, provisional = observation_cutoff(selected.index[-1].date(), premarket)
    assert as_of == utc("2026-10-29T20:00:00")
    assert provisional is False
    live = utc("2026-10-30T17:00:00")
    assert prepare_history(h, live).index[-1] == h.index[-1]
    assert observation_cutoff(h.index[-1].date(), live) == (live, True)
    with pytest.raises(TimingInputError, match="provider_latest_session_missing"):
        prepare_history(h.iloc[:-1], live)


def test_us_open_dst_and_early_close_holiday_calendar():
    assert session_open(utc("2026-07-22").date()).hour == 13
    assert session_open(utc("2026-11-02").date()).hour == 14
    assert latest_session(utc("2026-07-22T14:00:00")).isoformat() == "2026-07-22"
    assert latest_session(utc("2026-11-02T14:00:00")).isoformat() == "2026-10-30"
    assert session_close(utc("2026-11-27").date()).hour == 18
    assert not is_session(utc("2026-11-26").date())
    assert not is_session(utc("2025-01-09").date())


def test_friday_complete_snapshot_fresh_on_sunday_not_naive_24h():
    point = {"as_of": "2026-10-30T20:00:00+00:00", "source_session": "2026-10-30"}
    assert freshness(point, utc("2026-11-01T18:00:00")) == "fresh"
    assert freshness(point, utc("2026-11-02T14:00:00")) == "fresh"
    assert freshness(point, utc("2026-11-02T15:00:00")) == "stale"
    assert freshness({"as_of": "bad"}, utc("2026-11-01T18:00:00")) == "unknown"


def test_provisional_input_cutoff_freshness_not_computed_at():
    point = {"as_of": "2026-10-30T14:00:00+00:00", "source_session": "2026-10-30",
             "computed_at": "2026-10-30T19:00:00+00:00"}
    assert freshness(point, utc("2026-10-30T17:00:00")) == "fresh"
    assert freshness(point, utc("2026-10-30T19:00:00")) == "stale"
    assert freshness(point, utc("2026-10-31T14:00:00")) == "stale"
