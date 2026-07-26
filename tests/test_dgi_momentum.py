"""Tests for the canonical DGI momentum classifier (single source of truth).

``classify_momentum`` is the one directional engine shared by the watchlist
enrichment (dgi_screener) and the price forecast (forecast_cron), so both
surfaces always agree.
"""

import math

import pytest

from src.dgi_metrics import classify_momentum


def _tech(sma_50, sma_200, rsi=50, adx=25):
    return {"sma_50": sma_50, "sma_200": sma_200, "rsi": rsi, "adx": adx}


def test_bullish_uptrend():
    assert classify_momentum(_tech(110, 100, rsi=55), 115) == "Bullish"


def test_bullish_overextended_when_overbought():
    assert classify_momentum(_tech(110, 100, rsi=75), 115) == "Bullish (overextended)"


def test_weakening_when_price_below_sma50_in_uptrend():
    assert classify_momentum(_tech(110, 100, rsi=55), 108) == "Weakening"


def test_bearish_downtrend():
    assert classify_momentum(_tech(90, 100, rsi=45), 85) == "Bearish"


def test_bearish_oversold_when_oversold():
    assert classify_momentum(_tech(90, 100, rsi=25), 85) == "Bearish (oversold)"


def test_neutral_when_below_sma200_but_above_sma50():
    assert classify_momentum(_tech(90, 100, rsi=50), 95) == "Neutral"


def test_low_adx_forces_neutral():
    # Would be Bullish on structure, but ADX < 20 = no real trend.
    assert classify_momentum(_tech(110, 100, rsi=55, adx=12), 115) == "Neutral"


def test_unknown_on_missing_smas():
    assert classify_momentum(_tech(0, 0), 100) == "Unknown"
    assert classify_momentum({}, 100) == "Unknown"
    assert classify_momentum(None, 100) == "Unknown"


def test_unknown_on_zero_price():
    assert classify_momentum(_tech(110, 100), 0) == "Unknown"


def test_nan_smas_return_unknown_not_bearish():
    # A NaN SMA is truthy in Python and used to fall through to "Bearish".
    nan = float("nan")
    assert classify_momentum(_tech(nan, nan, rsi=82, adx=27), 85) == "Unknown"
    assert classify_momentum(_tech(110, nan), 85) == "Unknown"


def test_timing_score_ignores_trailing_nan_row():
    import numpy as np
    from src.dgi_metrics import calculate_technical_timing_score

    rng = np.random.default_rng(0)
    close = 100.0 + np.cumsum(rng.normal(0, 0.5, 260))
    high = close * 1.01
    low = close * 0.99
    clean = calculate_technical_timing_score(close, high, low, float(close[-1]))

    # Append a trailing NaN row (as yfinance sometimes returns intraday/pre-market).
    close_n = np.append(close, np.nan)
    high_n = np.append(high, np.nan)
    low_n = np.append(low, np.nan)
    with_nan = calculate_technical_timing_score(close_n, high_n, low_n, float(close[-1]))

    # SMAs must stay finite and match the clean computation (NaN row dropped).
    assert math.isfinite(with_nan["sma_50"]) and math.isfinite(with_nan["sma_200"])
    assert with_nan["sma_50"] == pytest.approx(clean["sma_50"])
    assert with_nan["sma_200"] == pytest.approx(clean["sma_200"])
    assert with_nan["score"] == pytest.approx(clean["score"])
