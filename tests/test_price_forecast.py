"""Tests for the deterministic price forecast module (src/price_forecast.py).

No LLM, no network. Validates the volatility-cone math, session-window bucketing,
endpoint detection, band membership, directional bias, and self-validation.
"""

import math

import pytest

from src.price_forecast import (
    HORIZONS,
    HORIZON_TREND_TIER,
    HV_WINDOW,
    LIFECYCLE_SESSIONS,
    MIN_HISTORY_BARS,
    DEFAULT_CONFIDENCE,
    OUTER_CONFIDENCE,
    compute_bias,
    compute_forecast,
    compute_forecast_from_closes,
    compute_reading,
    endpoint_direction_correct,
    evaluate_snapshot,
    has_enough_history,
    horizon_for_offset,
    is_endpoint,
    linear_trend,
    momentum_bias,
    graded_momentum_bias,
    price_inside,
    reading_from_momentum,
    summarize_prediction,
    aggregate_hit_rate,
    aggregate_forecast_averages,
    _trimmed_mean,
    TRIMMED_MEAN_MIN_N,
    trading_session_offset,
    z_for_confidence,
)
from src.volatility import ewma_volatility, historical_volatility


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
# Momentum-driven reading (DGI engine as single source of truth)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("momentum,expected_code,expected_bias", [
    ("Bullish", "bull", 1.0),
    ("Bullish (overextended)", "top", 0.6),
    ("Weakening", "top", -0.2),
    ("Neutral", "neutral", 0.0),
    ("Bearish", "bear", -1.0),
    ("Bearish (oversold)", "bottom", -0.6),
    ("Unknown", "neutral", 0.0),
])
def test_reading_and_bias_from_momentum(momentum, expected_code, expected_bias):
    rd = reading_from_momentum(momentum)
    assert rd["code"] == expected_code
    assert rd["label"] == momentum
    assert rd["momentum"] == momentum
    # Shape parity with compute_reading consumers.
    for key in ("code", "label", "icon", "conviction", "csp", "cc", "reason"):
        assert key in rd
    assert momentum_bias(momentum) == pytest.approx(expected_bias)


def test_reading_from_momentum_unknown_on_garbage():
    rd = reading_from_momentum("not-a-state")
    assert rd["code"] == "neutral"
    assert rd["label"] == "Unknown"
    assert momentum_bias("not-a-state") == 0.0
    assert momentum_bias(None) == 0.0


def test_compute_forecast_uses_momentum_over_technicals():
    # A strongly-bullish technicals aggregate would give bias +0.8, but an explicit
    # Bearish momentum must win — momentum is the single source of truth.
    tech = {"summary": {"recommendation": {"value": 0.8}}}
    fc = compute_forecast(100.0, 0.20, tech, momentum="Bearish")
    assert fc["momentum"] == "Bearish"
    assert fc["bias"] == pytest.approx(-1.0)
    assert fc["reading"]["code"] == "bear"
    assert fc["reading"]["label"] == "Bearish"
    # Bias still never shifts the band centre.
    assert fc["horizons"]["4w"]["center"] == pytest.approx(100.0)


def test_compute_forecast_without_momentum_falls_back_to_technicals():
    tech = {"summary": {"recommendation": {"value": 0.8}}}
    fc = compute_forecast(100.0, 0.20, tech)
    assert fc["momentum"] is None
    assert fc["bias"] == pytest.approx(0.8)


def test_from_closes_passes_momentum_through():
    closes = [100.0 + i * 0.1 for i in range(60)]
    fc = compute_forecast_from_closes(closes, momentum="Bullish")
    assert fc["momentum"] == "Bullish"
    assert fc["bias"] == pytest.approx(1.0)
    assert fc["reading"]["code"] == "bull"


# ---------------------------------------------------------------------------
# Graded momentum bias (sign from momentum, magnitude from ADX + SMA distance)
# ---------------------------------------------------------------------------

def test_graded_bias_falls_back_to_discrete_without_inputs():
    # No adx/sma → discrete regime bias.
    assert graded_momentum_bias("Bullish") == pytest.approx(1.0)
    assert graded_momentum_bias("Bearish") == pytest.approx(-1.0)
    assert graded_momentum_bias("Bullish (overextended)") == pytest.approx(0.6)


def test_graded_bias_non_directional_states_are_zero():
    assert graded_momentum_bias("Neutral", adx=40, price=110, sma_50=100, sma_200=90) == 0.0
    assert graded_momentum_bias("Unknown") == 0.0


def test_graded_bias_weakening_is_mildly_bearish_and_graded():
    # Weakening leans mildly bearish (cap 0.2), graded by ADX + SMA distance.
    b = graded_momentum_bias("Weakening", adx=40, price=110, sma_50=100, sma_200=90)
    assert -0.2 <= b < 0.0
    # adx_strength (40->0.667) blended with dist_strength (10%->1.0) => 0.833 * -0.2.
    assert b == pytest.approx(-0.1667, abs=1e-3)
    # Discrete fallback when inputs are missing.
    assert graded_momentum_bias("Weakening") == pytest.approx(-0.2)


def test_graded_bias_scales_with_adx_and_distance():
    # Strong trend (ADX 50 → adx_strength 1) and far from SMA50 (10% → dist 1) →
    # full magnitude at the regime cap.
    strong = graded_momentum_bias("Bullish", adx=50, price=110, sma_50=100, sma_200=90)
    assert strong == pytest.approx(1.0)

    # Weak trend (ADX just above floor) and price hugging SMA50 → near zero.
    weak = graded_momentum_bias("Bearish", adx=21, price=100.5, sma_50=100, sma_200=110)
    assert 0.0 < abs(weak) < 0.15
    assert weak < 0  # sign preserved


def test_graded_bias_sign_follows_momentum():
    assert graded_momentum_bias("Bullish", adx=35, price=108, sma_50=100, sma_200=90) > 0
    assert graded_momentum_bias("Bearish", adx=35, price=92, sma_50=100, sma_200=110) < 0


def test_graded_bias_respects_regime_cap():
    # Overextended cap is 0.6 even at maximum strength.
    v = graded_momentum_bias("Bullish (overextended)", adx=60, price=120, sma_50=100, sma_200=90)
    assert v == pytest.approx(0.6)


def test_compute_forecast_uses_graded_bias_with_momentum_technicals():
    mt = {"adx": 50.0, "sma_50": 100.0, "sma_200": 90.0}
    fc = compute_forecast(110.0, 0.20, momentum="Bullish", momentum_technicals=mt)
    assert fc["bias"] == pytest.approx(1.0)
    # Weaker structure → smaller magnitude.
    mt2 = {"adx": 22.0, "sma_50": 100.0, "sma_200": 95.0}
    fc2 = compute_forecast(100.5, 0.20, momentum="Bullish", momentum_technicals=mt2)
    assert 0.0 < fc2["bias"] < 1.0


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


# ---------------------------------------------------------------------------
# Confidence bands (configurable z-multiplier)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "confidence,expected_z",
    [
        (0.50, 0.6745),
        (0.68, 1.0),
        (0.80, 1.2816),
        (0.95, 1.96),
    ],
)
def test_z_for_confidence_known_levels(confidence, expected_z):
    assert z_for_confidence(confidence) == pytest.approx(expected_z, rel=1e-3)


def test_z_for_confidence_defaults_and_nearest():
    # None → default (0.68 → z=1.0).
    assert z_for_confidence(None) == pytest.approx(1.0)
    # Unknown value snaps to the nearest supported level.
    assert z_for_confidence(0.70) == pytest.approx(z_for_confidence(0.68))


def test_confidence_narrows_the_band():
    # A lower confidence must produce a tighter (narrower) primary band, but the
    # centre stays on spot and the raw unit sigma is unchanged.
    wide = compute_forecast(100.0, 0.20, confidence=0.95)["horizons"]["4w"]
    tight = compute_forecast(100.0, 0.20, confidence=0.50)["horizons"]["4w"]
    wide_width = wide["high1"] - wide["low1"]
    tight_width = tight["high1"] - tight["low1"]
    assert tight_width < wide_width
    assert tight["center"] == pytest.approx(100.0)
    # Raw unit sigma is the same regardless of the chosen confidence.
    assert tight["sigma"] == pytest.approx(wide["sigma"], rel=1e-6)


def test_compute_forecast_records_confidence_metadata():
    fc = compute_forecast(100.0, 0.20, confidence=0.50)
    assert fc["confidence"] == pytest.approx(0.50)
    assert fc["outer_confidence"] == pytest.approx(OUTER_CONFIDENCE)
    # z1 corresponds to the primary confidence; z2 to the outer one.
    band = fc["horizons"]["1w"]
    assert band["z1"] == pytest.approx(z_for_confidence(0.50), rel=1e-3)
    assert band["z2"] == pytest.approx(z_for_confidence(OUTER_CONFIDENCE), rel=1e-3)


# ---------------------------------------------------------------------------
# EWMA volatility
# ---------------------------------------------------------------------------

def test_ewma_volatility_needs_enough_history():
    assert ewma_volatility([100.0]) is None
    assert ewma_volatility(None) is None


def test_ewma_volatility_positive_and_annualized():
    closes = [100.0, 101.0, 99.5, 102.0, 100.5, 103.0, 101.0, 104.0, 102.5, 105.0]
    vol = ewma_volatility(closes, span=5)
    assert vol is not None and vol > 0


def test_ewma_reacts_faster_than_flat_hv():
    # Calm regime then a volatility spike at the end: EWMA should read higher
    # than the flat-window HV because it weights the recent spike more.
    calm = [100.0 + 0.1 * i for i in range(40)]
    spike = [calm[-1] + (5.0 if i % 2 == 0 else -5.0) for i in range(6)]
    closes = calm + spike
    hv = historical_volatility(closes)
    ewma = ewma_volatility(closes, span=10)
    assert hv is not None and ewma is not None
    assert ewma > hv


# ---------------------------------------------------------------------------
# Linear trend overlay
# ---------------------------------------------------------------------------

def test_linear_trend_none_on_short_series():
    assert linear_trend([100.0, 101.0]) is None
    assert linear_trend(None) is None


def test_linear_trend_recovers_known_slope():
    # Perfectly linear series: slope per session == 0.5, residuals ~0.
    closes = [100.0 + 0.5 * i for i in range(20)]
    tr = linear_trend(closes, window=20)
    assert tr is not None
    assert tr["slope"] == pytest.approx(0.5, rel=1e-6)
    assert tr["resid_std"] == pytest.approx(0.0, abs=1e-6)
    assert tr["window"] == 20


def test_linear_trend_respects_window():
    closes = [100.0 + 0.5 * i for i in range(60)]
    tr = linear_trend(closes, window=10)
    assert tr["window"] == 10


def test_linear_trend_perfect_line_high_r2_no_shrink():
    # Clean line: R²≈1 so the shrunk slope stays ≈ raw slope; quality "strong".
    closes = [100.0 + 0.5 * i for i in range(20)]
    tr = linear_trend(closes, window=20)
    assert tr["r2"] == pytest.approx(1.0, abs=1e-6)
    assert tr["slope"] == pytest.approx(tr["slope_raw"], rel=1e-6)
    assert tr["quality"] == "strong"


def test_linear_trend_noisy_series_shrinks_slope():
    # Zig-zag with no real drift: low R² → slope shrunk toward 0, quality "weak".
    closes = [100.0 + (2.0 if i % 2 else -2.0) for i in range(20)]
    tr = linear_trend(closes, window=20)
    assert tr["r2"] < 0.2
    assert abs(tr["slope"]) <= abs(tr["slope_raw"])
    assert tr["quality"] == "weak"


# ---------------------------------------------------------------------------
# Trade reading (bias + trend agreement matrix)
# ---------------------------------------------------------------------------

def test_compute_reading_bullish_aligned_favours_csp():
    rd = compute_reading(0.5, 0.8)
    assert rd["code"] == "bull"
    assert rd["agree"] is True
    assert rd["csp"] == "favorable"
    assert rd["cc"] == "avoid"


def test_compute_reading_bearish_aligned_favours_cc():
    rd = compute_reading(-0.5, -0.8)
    assert rd["code"] == "bear"
    assert rd["csp"] == "avoid"
    assert rd["cc"] == "favorable"


def test_compute_reading_topping_divergence():
    rd = compute_reading(-0.5, 0.8)  # uptrend, momentum fading
    assert rd["code"] == "top"
    assert rd["agree"] is False
    assert rd["cc"] == "favorable"


def test_compute_reading_bottoming_divergence():
    rd = compute_reading(0.5, -0.8)  # downtrend, momentum turning up
    assert rd["code"] == "bottom"
    assert rd["csp"] == "favorable"


def test_compute_reading_neutral_below_threshold():
    # |bias| under the conviction threshold and flat trend → neutral.
    rd = compute_reading(0.05, 0.0)
    assert rd["code"] == "neutral"
    assert rd["csp"] == "neutral" and rd["cc"] == "neutral"


# ---------------------------------------------------------------------------
# Direction scoring: conviction threshold + trend-agreement filter
# ---------------------------------------------------------------------------

def _dir_band(direction):
    # Minimal band: realized endpoint above/below the creation price (center).
    price = 100.0
    realized = 102.0 if direction > 0 else 98.0
    return {"center": price}, realized


def test_direction_scored_when_bias_and_trend_agree():
    band, realized = _dir_band(1)
    # Bullish: bias up, trend up, price rose → correct.
    assert endpoint_direction_correct(band, realized, 0.5, trend_slope=0.8) is True


def test_direction_none_when_bias_below_threshold():
    band, realized = _dir_band(1)
    assert endpoint_direction_correct(band, realized, 0.05, trend_slope=0.8) is None


def test_direction_none_when_bias_trend_diverge():
    band, realized = _dir_band(1)
    # High-conviction bias but trend disagrees → not scored.
    assert endpoint_direction_correct(band, realized, 0.5, trend_slope=-0.8) is None


def test_direction_old_doc_falls_back_to_bias_only():
    band, realized = _dir_band(1)
    # No trend slope (legacy doc): still scored on bias alone (gated by threshold).
    assert endpoint_direction_correct(band, realized, 0.5, trend_slope=None) is True


# ---------------------------------------------------------------------------
# Trend projection + mean deviation in summarize_prediction
# ---------------------------------------------------------------------------

def test_summarize_prediction_trend_end_and_mean_dev():
    # Doc carries a trend slope; snapshots deviate from the projected trend line.
    slope = 0.5
    price0 = 100.0
    pred = _pred(
        horizons=("1w",),
        snapshots=[
            # offset 3 → trend expects 100 + 0.5*3 = 101.5; actual 102.5 → dev 1.0
            {"horizon": "1w", "offset": 3, "price": 102.5,
             "inside_1sigma": True, "inside_2sigma": True},
            # offset 5 → trend expects 102.5; actual 101.5 → dev 1.0
            {"horizon": "1w", "offset": 5, "price": 101.5,
             "inside_1sigma": True, "inside_2sigma": True},
        ],
    )
    pred["price_at_creation"] = price0
    pred["trend"] = {"slope": slope, "resid_std": 0.0, "window": 20}
    # 1w ends at session 5 → projected trend end = 100 + 0.5*5 = 102.5
    for name, band in pred["horizons"].items():
        band["trend_end"] = round(price0 + slope * HORIZONS[name]["end_session"], 4)

    summary = summarize_prediction(pred)
    assert summary["1w"]["trend_end"] == pytest.approx(102.5)
    assert summary["1w"]["mean_dev"] == pytest.approx(1.0, rel=1e-3)
    assert summary["1w"]["mean_dev_pct"] is not None


# ---------------------------------------------------------------------------
# Trimmed mean + per-horizon projected-price averages
# ---------------------------------------------------------------------------

def test_trimmed_mean_none_below_min_samples():
    # Fewer than TRIMMED_MEAN_MIN_N samples → not meaningful → None.
    assert _trimmed_mean([1.0, 2.0, 3.0, 4.0]) is None
    assert TRIMMED_MEAN_MIN_N == 5


def test_trimmed_mean_drops_tails():
    # n=5, trim 20% → drop 1 from each tail. Outliers 1 and 100 removed → mean of 2,3,4.
    assert _trimmed_mean([1.0, 2.0, 3.0, 4.0, 100.0]) == pytest.approx(3.0)


def test_trimmed_mean_ignores_non_finite():
    vals = [1.0, 2.0, 3.0, 4.0, 5.0, None, float("nan")]
    assert _trimmed_mean(vals) == pytest.approx(3.0)


def _pred_with_slope(slopes, *, center=100.0, high1=105.0):
    """Build a prediction whose horizon bands carry the given ``trend_slope``.

    ``center``/``high1`` seed the anchor price and the volatility half-width
    (``high1 - center``) the aggregator uses to build the projected range.
    """
    pred = _pred(horizons=tuple(slopes.keys()))
    for name, sl in slopes.items():
        pred["horizons"][name]["trend_slope"] = sl
        pred["horizons"][name]["center"] = center
        pred["horizons"][name]["high1"] = high1
    return pred


def test_aggregate_forecast_averages_slope_projection_and_trim():
    # Option 2: average per-horizon slope, project once from the latest anchor
    # (price = anchor + mean_slope * sessions), range = mean ± (high1 - center).
    preds = [
        _pred_with_slope({"1d": 1.0, "1w": 0.0}),   # latest → anchor 100, half-width 5
        _pred_with_slope({"1d": 1.0, "1w": 0.2}),
        _pred_with_slope({"1d": 1.0, "1w": 0.4}),
        _pred_with_slope({"1d": 1.0, "1w": 0.6}),
        _pred_with_slope({"1d": 1.0, "1w": 2.0}),   # 1w slope outlier
    ]
    av = aggregate_forecast_averages(preds)

    # 1w: sessions=5, mean slope 0.64 → 100 + 0.64*5 = 103.2
    assert av["1w"]["lookback"] == 5 and av["1w"]["n"] == 5
    assert av["1w"]["anchor"] == pytest.approx(100.0)
    assert av["1w"]["mean"] == pytest.approx(103.2)
    # trimmed slope drops 0.0 and 2.0 → mean(0.2,0.4,0.6)=0.4 → 100 + 0.4*5 = 102.0
    assert av["1w"]["trimmed_mean"] == pytest.approx(102.0)
    # range = mean ± (high1 - center) = 103.2 ± 5
    assert av["1w"]["low"] == pytest.approx(98.2)
    assert av["1w"]["high"] == pytest.approx(108.2)

    # 1d: sessions=1, lookback=1, n=1, slope 1.0 → 100 + 1*1 = 101; no trim.
    assert av["1d"]["lookback"] == 1 and av["1d"]["n"] == 1
    assert av["1d"]["mean"] == pytest.approx(101.0)
    assert av["1d"]["trimmed_mean"] is None
    assert av["1d"]["low"] == pytest.approx(96.0)
    assert av["1d"]["high"] == pytest.approx(106.0)


def test_aggregate_forecast_averages_lookback_caps_per_horizon():
    # 25 predictions with distinct created_date; slope == index, newest is i=24.
    preds = []
    for i in range(25):
        p = _pred_with_slope({"1d": float(i), "1w": float(i),
                              "2w": float(i), "4w": float(i)})
        p["created_date"] = f"2026-01-{i + 1:02d}"  # ascending; i=24 is newest
        preds.append(p)
    av = aggregate_forecast_averages(preds)

    # Each horizon averages only its most-recent lookback slopes, projected
    # from anchor 100 over `sessions` sessions (sessions == lookback here).
    assert av["1d"]["lookback"] == 1 and av["1d"]["n"] == 1
    assert av["1d"]["mean"] == pytest.approx(100 + 24.0 * 1)              # newest slope
    assert av["1w"]["lookback"] == 5 and av["1w"]["n"] == 5
    assert av["1w"]["mean"] == pytest.approx(100 + (sum(range(20, 25)) / 5) * 5)
    assert av["2w"]["lookback"] == 10 and av["2w"]["n"] == 10
    assert av["2w"]["mean"] == pytest.approx(100 + (sum(range(15, 25)) / 10) * 10)
    assert av["4w"]["lookback"] == 20 and av["4w"]["n"] == 20
    assert av["4w"]["mean"] == pytest.approx(100 + (sum(range(5, 25)) / 20) * 20)


def test_aggregate_forecast_averages_trim_none_when_few_samples():
    preds = [_pred_with_slope({"1d": 1.0, "1w": 0.2}) for _ in range(3)]
    av = aggregate_forecast_averages(preds)
    assert av["1w"]["n"] == 3  # only 3 available (lookback 5)
    assert av["1w"]["mean"] == pytest.approx(100 + 0.2 * 5)  # 101.0
    assert av["1w"]["trimmed_mean"] is None


def test_aggregate_forecast_averages_empty_and_missing_slope():
    # No predictions → all horizons report n=0 with None projections.
    av = aggregate_forecast_averages([])
    for name in HORIZONS:
        assert av[name] == {
            "n": 0,
            "lookback": HORIZONS[name]["end_session"],
            "anchor": None,
            "mean": None,
            "trimmed_mean": None,
            "low": None,
            "high": None,
        }

    # A prediction with no slope (and no top-level trend) contributes nothing.
    pred = _pred(horizons=("1d",))  # bands lack trend_slope; no `trend` key
    av2 = aggregate_forecast_averages([pred])
    assert av2["1d"]["n"] == 0
    assert av2["1d"]["mean"] is None

    # A prediction without a trend_end contributes nothing.
    pred = _pred(horizons=("1d",))  # no trend_end set
    av2 = aggregate_forecast_averages([pred])
    assert av2["1d"]["n"] == 0
    assert av2["1d"]["mean"] is None


# ──────────────────────────────────────────────────────────────────────────────
# Horizon-aware trend windows (short 1d/1w, long 2w/4w)
# ──────────────────────────────────────────────────────────────────────────────

def test_horizon_aware_windows_use_matched_slope():
    # An accelerating series: the recent (short) slope is steeper than the long one.
    closes = [100.0] * 30 + [100 + i * 0.8 for i in range(1, 31)]
    fc = compute_forecast_from_closes(
        closes, trend_window=20, trend_window_long=40, vol_source="hv"
    )
    slope_short = linear_trend(closes, 20)["slope"]
    slope_long = linear_trend(closes, 40)["slope"]
    assert slope_short != pytest.approx(slope_long)  # the fixture must differentiate

    h = fc["horizons"]
    # Short horizons use the short-window slope; long horizons the long-window slope.
    assert h["1d"]["trend_slope"] == pytest.approx(slope_short)
    assert h["1w"]["trend_slope"] == pytest.approx(slope_short)
    assert h["2w"]["trend_slope"] == pytest.approx(slope_long)
    assert h["4w"]["trend_slope"] == pytest.approx(slope_long)
    # trend_end must be consistent with the per-horizon slope.
    assert h["4w"]["trend_end"] == pytest.approx(
        round(fc["price_at_creation"] + slope_long * h["4w"]["end_session"], 4)
    )
    # Top-level (headline) trend is the short window.
    assert fc["trend"]["slope"] == pytest.approx(slope_short)


def test_horizon_tier_map_covers_all_horizons():
    assert set(HORIZON_TREND_TIER) == set(HORIZONS)
    assert HORIZON_TREND_TIER == {"1d": "short", "1w": "short", "2w": "long", "4w": "long"}


def test_single_window_when_long_equals_short():
    closes = [100 + i * 0.5 for i in range(60)]
    fc = compute_forecast_from_closes(
        closes, trend_window=20, trend_window_long=20, vol_source="hv"
    )
    slopes = {fc["horizons"][h]["trend_slope"] for h in HORIZONS}
    assert len(slopes) == 1  # every horizon shares the single window's slope
