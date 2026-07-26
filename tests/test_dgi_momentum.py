"""Tests for the canonical DGI momentum classifier (single source of truth).

``classify_momentum`` is the one directional engine shared by the watchlist
enrichment (dgi_screener) and the price forecast (forecast_cron), so both
surfaces always agree.
"""

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
