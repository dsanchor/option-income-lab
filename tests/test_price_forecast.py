"""Tests for the deterministic price forecast module (src/price_forecast.py).

No LLM, no network. Validates the volatility-cone math, session-window bucketing,
endpoint detection, band membership, directional bias, and self-validation.
"""

import math

import pytest

from src.price_forecast import (
    HORIZONS,
    HV_WINDOW,
    LIFECYCLE_SESSIONS,
    MIN_HISTORY_BARS,
    compute_bias,
    compute_forecast,
    compute_forecast_from_closes,
    endpoint_direction_correct,
    evaluate_snapshot,
    has_enough_history,
    horizon_for_offset,
    is_endpoint,
    price_inside,
    summarize_prediction,
    aggregate_hit_rate,
    trading_session_offset,
)


# ---------------------------------------------------------------------------
# Session-window helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "offset,expected",
    [
        (0, None),      # creation day belongs to no horizon
        (1, "1d"),
        (2, "1w"),
        (5, "1w"),
        (6, "2w"),
        (10, "2w"),
        (11, "4w"),
        (20, "4w"),
        (21, None),     # beyond lifecycle
        (99, None),
    ],
)
def test_horizon_for_offset(offset, expected):
    assert horizon_for_offset(offset) == expected


@pytest.mark.parametrize(
    "offset,expected",
    [(1, "1d"), (5, "1w"), (10, "2w"), (20, "4w"), (2, None), (11, None), (0, None)],
)
def test_is_endpoint(offset, expected):
    assert is_endpoint(offset) == expected


def test_lifecycle_is_20_sessions():
    assert LIFECYCLE_SESSIONS == 20


def test_has_enough_history():
    assert has_enough_history(list(range(1, MIN_HISTORY_BARS + 1)))
    assert not has_enough_history(list(range(1, MIN_HISTORY_BARS)))
    assert not has_enough_history([])
    assert not has_enough_history(None)
    # None values do not count toward the bar total.
    series = [None] * 10 + list(range(1, MIN_HISTORY_BARS))
    assert not has_enough_history(series)


# ---------------------------------------------------------------------------
# Bias
# ---------------------------------------------------------------------------

def test_compute_bias_reads_summary_recommendation():
    tech = {"summary": {"recommendation": {"value": 0.42, "label": "Buy"}}}
    assert compute_bias(tech) == pytest.approx(0.42)


def test_compute_bias_clamps_and_handles_missing():
    assert compute_bias({"summary": {"recommendation": {"value": 5.0}}}) == 1.0
    assert compute_bias({"summary": {"recommendation": {"value": -5.0}}}) == -1.0
    assert compute_bias({}) == 0.0
    assert compute_bias(None) == 0.0
    assert compute_bias({"summary": {"recommendation": {"value": None}}}) == 0.0
    assert compute_bias({"summary": {"recommendation": None}}) == 0.0


# ---------------------------------------------------------------------------
# Forecast band math
# ---------------------------------------------------------------------------

def test_compute_forecast_none_on_bad_inputs():
    assert compute_forecast(None, 0.2) is None
    assert compute_forecast(100.0, None) is None
    assert compute_forecast(0.0, 0.2) is None
    assert compute_forecast(-10.0, 0.2) is None
    assert compute_forecast(100.0, 0.0) is None
    assert compute_forecast(100.0, -0.2) is None


def test_compute_forecast_has_all_horizons_and_center():
    fc = compute_forecast(100.0, 0.20)
    assert set(fc["horizons"].keys()) == set(HORIZONS.keys())
    for band in fc["horizons"].values():
        # Band is centred on spot; bias never shifts it.
        assert band["center"] == pytest.approx(100.0)
        assert band["low2"] < band["low1"] < band["center"] < band["high1"] < band["high2"]
    assert fc["price_at_creation"] == pytest.approx(100.0)


def test_sigma_scales_with_sqrt_of_sessions():
    price, hv = 100.0, 0.20
    fc = compute_forecast(price, hv)
    sig_1d = fc["horizons"]["1d"]["sigma"]
    sig_4w = fc["horizons"]["4w"]["sigma"]

    # 1d ends at session 1, 4w ends at session 20 → ratio == sqrt(20).
    assert sig_4w / sig_1d == pytest.approx(math.sqrt(20), rel=1e-3)

    # Absolute check against the closed form for the 1d band.
    expected_1d = price * hv * math.sqrt(1 / 252)
    assert sig_1d == pytest.approx(expected_1d, rel=1e-3)


def test_bias_included_but_separate_from_band():
    tech = {"summary": {"recommendation": {"value": 0.8}}}
    fc = compute_forecast(100.0, 0.20, tech)
    assert fc["bias"] == pytest.approx(0.8)
    # Strongly bullish bias must NOT move the band centre off spot.
    assert fc["horizons"]["4w"]["center"] == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# compute_forecast_from_closes
# ---------------------------------------------------------------------------

def test_from_closes_needs_enough_history():
    assert compute_forecast_from_closes([100.0, 101.0]) is None


def test_from_closes_derives_hv_and_default_price():
    # Gently trending series with enough bars.
    closes = [100.0 + i * 0.1 for i in range(60)]
    fc = compute_forecast_from_closes(closes)
    assert fc is not None
    assert fc["hv"] > 0
    # Default current price is the last close.
    assert fc["price_at_creation"] == pytest.approx(closes[-1])


def test_from_closes_respects_explicit_current_price():
    closes = [100.0 + i * 0.1 for i in range(60)]
    fc = compute_forecast_from_closes(closes, current_price=123.45)
    assert fc["price_at_creation"] == pytest.approx(123.45)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _sample_horizons():
    return compute_forecast(100.0, 0.20)["horizons"]


def test_price_inside():
    band = {"low1": 95.0, "high1": 105.0, "low2": 90.0, "high2": 110.0}
    assert price_inside(band, 100.0, 1)
    assert price_inside(band, 95.0, 1)          # inclusive
    assert not price_inside(band, 94.9, 1)
    assert price_inside(band, 108.0, 2)         # outside 1σ, inside 2σ
    assert not price_inside(band, 120.0, 2)
    assert not price_inside(band, None, 1)


def test_evaluate_snapshot_buckets_and_flags():
    horizons = _sample_horizons()

    # Offset 0 (creation day) → no horizon.
    assert evaluate_snapshot(horizons, 100.0, 0) is None
    # Beyond lifecycle → None.
    assert evaluate_snapshot(horizons, 100.0, 21) is None

    # Offset 3 falls in the 1w window, not an endpoint.
    r = evaluate_snapshot(horizons, 100.0, 3)
    assert r["horizon"] == "1w"
    assert r["inside_1sigma"] and r["inside_2sigma"]
    assert r["is_endpoint"] is False

    # Offset 20 is the 4w endpoint.
    r = evaluate_snapshot(horizons, 100.0, 20)
    assert r["horizon"] == "4w"
    assert r["is_endpoint"] is True


def test_evaluate_snapshot_outside_band():
    horizons = _sample_horizons()
    # A price far above the 1d ±2σ band.
    r = evaluate_snapshot(horizons, 100.0 + 999.0, 1)
    assert r["horizon"] == "1d"
    assert not r["inside_1sigma"]
    assert not r["inside_2sigma"]


def test_endpoint_direction_correct():
    band = {"center": 100.0, "low1": 95.0, "high1": 105.0, "low2": 90.0, "high2": 110.0}
    # Bullish bias, price up → correct.
    assert endpoint_direction_correct(band, 103.0, 0.5) is True
    # Bullish bias, price down → incorrect.
    assert endpoint_direction_correct(band, 97.0, 0.5) is False
    # Bearish bias, price down → correct.
    assert endpoint_direction_correct(band, 97.0, -0.5) is True
    # Neutral bias makes no claim → None.
    assert endpoint_direction_correct(band, 103.0, 0.0) is None
    # No move → None.
    assert endpoint_direction_correct(band, 100.0, 0.5) is None


# ---------------------------------------------------------------------------
# Trading-session offset (calendar date → session offset, holiday-robust)
# ---------------------------------------------------------------------------

def test_trading_session_offset_counts_sessions_between_dates():
    # A week with a holiday gap: Wed missing (e.g. market closed).
    dates = ["2026-01-05", "2026-01-06", "2026-01-08", "2026-01-09", "2026-01-12"]
    # Created on the 5th; sessions after it up to the 9th are 6th, 8th, 9th = 3.
    assert trading_session_offset(dates, "2026-01-05", "2026-01-09") == 3
    # Same day → offset 0 (creation day belongs to no horizon).
    assert trading_session_offset(dates, "2026-01-05", "2026-01-05") == 0
    # Next session only.
    assert trading_session_offset(dates, "2026-01-05", "2026-01-06") == 1
    # Full span.
    assert trading_session_offset(dates, "2026-01-05", "2026-01-12") == 4


def test_trading_session_offset_edge_cases():
    assert trading_session_offset([], "2026-01-05", "2026-01-09") == 0
    assert trading_session_offset(["2026-01-06"], None, "2026-01-09") == 0
    assert trading_session_offset(["2026-01-06"], "2026-01-05", None) == 0


# ---------------------------------------------------------------------------
# Aggregation (table rows + rolling calibration)
# ---------------------------------------------------------------------------

def _pred(horizons=("1d", "1w"), snapshots=None, endpoints=None):
    hz = {name: {"center": 100.0, "low1": 95.0, "high1": 105.0,
                 "low2": 90.0, "high2": 110.0,
                 "start_session": HORIZONS[name]["start_session"],
                 "end_session": HORIZONS[name]["end_session"]}
          for name in horizons}
    return {
        "horizons": hz,
        "snapshots": snapshots or [],
        "endpoints": endpoints or {},
    }


def test_summarize_prediction_path_pct():
    pred = _pred(
        horizons=("1d", "1w"),
        snapshots=[
            {"horizon": "1w", "inside_1sigma": True, "inside_2sigma": True},
            {"horizon": "1w", "inside_1sigma": False, "inside_2sigma": True},
        ],
    )
    summary = summarize_prediction(pred)
    assert summary["1w"]["path_count"] == 2
    assert summary["1w"]["path_pct_1sigma"] == 50.0
    assert summary["1w"]["path_pct_2sigma"] == 100.0
    # 1d has no snapshots yet
    assert summary["1d"]["path_count"] == 0
    assert summary["1d"]["path_pct_1sigma"] is None


def test_summarize_prediction_endpoint_passthrough():
    ep = {"date": "2024-01-05", "price": 101.0, "inside_1sigma": True,
          "inside_2sigma": True, "direction_correct": True}
    pred = _pred(horizons=("1d",), endpoints={"1d": ep})
    summary = summarize_prediction(pred)
    assert summary["1d"]["endpoint"] == ep


def test_aggregate_hit_rate_calibration():
    preds = [
        _pred(horizons=("1d",), endpoints={"1d": {"inside_1sigma": True,
              "inside_2sigma": True, "direction_correct": True}}),
        _pred(horizons=("1d",), endpoints={"1d": {"inside_1sigma": False,
              "inside_2sigma": True, "direction_correct": False}}),
    ]
    agg = aggregate_hit_rate(preds)
    assert agg["1d"]["resolved"] == 2
    assert agg["1d"]["hit_pct_1sigma"] == 50.0
    assert agg["1d"]["hit_pct_2sigma"] == 100.0
    assert agg["1d"]["direction_pct"] == 50.0
    # Unresolved horizon → None
    assert agg["4w"]["resolved"] == 0
    assert agg["4w"]["hit_pct_1sigma"] is None


def test_aggregate_hit_rate_ignores_neutral_direction():
    preds = [
        _pred(horizons=("1d",), endpoints={"1d": {"inside_1sigma": True,
              "inside_2sigma": True, "direction_correct": None}}),
    ]
    agg = aggregate_hit_rate(preds)
    assert agg["1d"]["resolved"] == 1
    assert agg["1d"]["direction_pct"] is None
