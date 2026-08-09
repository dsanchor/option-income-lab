"""Tests for technical indicators and signal generation module.

Validates technicals_calculator.py — computes RSI, Stochastic, CCI, MACD,
Williams %R, Moving Averages from OHLCV data, and generates Buy/Sell/Neutral
signals using TradingView-equivalent logic.

All tests use synthetic OHLCV DataFrames — no external API calls.
"""

import math

import numpy as np
import pandas as pd
import pytest

from src.technicals_calculator import (
    TechnicalsCalculator,
    _oscillator_signal,
    _ma_signal,
    _tech_recommendation_label,
)


# ---------------------------------------------------------------------------
# Helpers — synthetic OHLCV data generators
# ---------------------------------------------------------------------------

def _make_ohlcv(closes, *, length=None):
    """Build a DataFrame with OHLCV columns from a list of close prices.

    Open/High/Low are derived from close with small offsets.
    Volume is constant 1M.
    """
    if length and len(closes) < length:
        closes = list(closes) + [closes[-1]] * (length - len(closes))
    n = len(closes)
    df = pd.DataFrame({
        "Open": [c * 0.999 for c in closes],
        "High": [c * 1.005 for c in closes],
        "Low": [c * 0.995 for c in closes],
        "Close": closes,
        "Volume": [1_000_000] * n,
    })
    df.index = pd.date_range("2025-01-01", periods=n, freq="B")
    return df


def _uptrending_ohlcv(n=250, start=100.0, daily_gain=0.5):
    """Generate steadily rising OHLCV data."""
    closes = [start + i * daily_gain for i in range(n)]
    return _make_ohlcv(closes)


def _downtrending_ohlcv(n=250, start=200.0, daily_loss=0.5):
    """Generate steadily falling OHLCV data."""
    closes = [start - i * daily_loss for i in range(n)]
    return _make_ohlcv(closes)


def _flat_ohlcv(n=250, price=150.0):
    """Generate flat/sideways OHLCV data."""
    closes = [price] * n
    return _make_ohlcv(closes)


@pytest.fixture
def uptrending():
    return _uptrending_ohlcv()


@pytest.fixture
def downtrending():
    return _downtrending_ohlcv()


@pytest.fixture
def flat():
    return _flat_ohlcv()


@pytest.fixture
def calculator():
    return TechnicalsCalculator()


# ---------------------------------------------------------------------------
# 1. Signal logic — individual indicator signal mapping via _oscillator_signal
# ---------------------------------------------------------------------------

class TestRSISignal:
    """RSI < 30 and rising → Buy; RSI > 70 and falling → Sell; else Neutral."""

    def test_rsi_buy_signal(self):
        sym = {"RSI": 25.0, "RSI[1]": 22.0}
        assert _oscillator_signal("RSI", sym) == "Buy"

    def test_rsi_sell_signal(self):
        sym = {"RSI": 75.0, "RSI[1]": 78.0}
        assert _oscillator_signal("RSI", sym) == "Sell"

    def test_rsi_neutral_midrange(self):
        sym = {"RSI": 50.0, "RSI[1]": 48.0}
        assert _oscillator_signal("RSI", sym) == "Neutral"

    def test_rsi_oversold_but_falling_is_neutral(self):
        """RSI < 30 but still falling → Neutral (not yet reversing)."""
        sym = {"RSI": 25.0, "RSI[1]": 28.0}
        assert _oscillator_signal("RSI", sym) == "Neutral"

    def test_rsi_overbought_but_rising_is_neutral(self):
        """RSI > 70 but still rising → Neutral (momentum continues)."""
        sym = {"RSI": 75.0, "RSI[1]": 72.0}
        assert _oscillator_signal("RSI", sym) == "Neutral"

    def test_rsi_none_is_neutral(self):
        sym = {"RSI": None}
        assert _oscillator_signal("RSI", sym) == "Neutral"


class TestStochasticSignal:
    """K < 20, D < 20, K crosses above D → Buy; mirror for Sell."""

    def test_stochastic_buy(self):
        sym = {"Stoch.K": 18.0, "Stoch.D": 15.0, "Stoch.K[1]": 14.0, "Stoch.D[1]": 16.0}
        assert _oscillator_signal("Stoch.K", sym) == "Buy"

    def test_stochastic_sell(self):
        sym = {"Stoch.K": 85.0, "Stoch.D": 88.0, "Stoch.K[1]": 89.0, "Stoch.D[1]": 87.0}
        assert _oscillator_signal("Stoch.K", sym) == "Sell"

    def test_stochastic_neutral_midrange(self):
        sym = {"Stoch.K": 50.0, "Stoch.D": 50.0, "Stoch.K[1]": 49.0, "Stoch.D[1]": 51.0}
        assert _oscillator_signal("Stoch.K", sym) == "Neutral"

    def test_stochastic_missing_prev_neutral(self):
        sym = {"Stoch.K": 18.0, "Stoch.D": 15.0}
        assert _oscillator_signal("Stoch.K", sym) == "Neutral"


class TestCCISignal:
    """CCI < -100 and rising → Buy; CCI > 100 and falling → Sell."""

    def test_cci_buy(self):
        sym = {"CCI20": -120.0, "CCI20[1]": -130.0}
        assert _oscillator_signal("CCI20", sym) == "Buy"

    def test_cci_sell(self):
        sym = {"CCI20": 110.0, "CCI20[1]": 120.0}
        assert _oscillator_signal("CCI20", sym) == "Sell"

    def test_cci_neutral(self):
        sym = {"CCI20": 50.0, "CCI20[1]": 45.0}
        assert _oscillator_signal("CCI20", sym) == "Neutral"


class TestMACDSignal:
    """MACD > signal → Buy; MACD < signal → Sell."""

    def test_macd_buy(self):
        sym = {"MACD.macd": 1.5, "MACD.signal": 1.0}
        assert _oscillator_signal("MACD.macd", sym) == "Buy"

    def test_macd_sell(self):
        sym = {"MACD.macd": 0.5, "MACD.signal": 1.0}
        assert _oscillator_signal("MACD.macd", sym) == "Sell"

    def test_macd_neutral_equal(self):
        sym = {"MACD.macd": 1.0, "MACD.signal": 1.0}
        assert _oscillator_signal("MACD.macd", sym) == "Neutral"


class TestWilliamsRSignal:
    """Williams %R < -80 → Buy; > -20 → Sell."""

    def test_williams_buy(self):
        sym = {"W.R": -85.0}
        assert _oscillator_signal("W.R", sym) == "Buy"

    def test_williams_sell(self):
        sym = {"W.R": -15.0}
        assert _oscillator_signal("W.R", sym) == "Sell"

    def test_williams_neutral(self):
        sym = {"W.R": -50.0}
        assert _oscillator_signal("W.R", sym) == "Neutral"


class TestMASignalFunction:
    """close > MA → Buy; close < MA → Sell."""

    def test_ma_buy(self):
        # _ma_signal(key, ma_val, close) — close > ma → Buy
        assert _ma_signal("SMA10", 180.0, 185.0) == "Buy"

    def test_ma_sell(self):
        assert _ma_signal("SMA10", 180.0, 175.0) == "Sell"

    def test_ma_neutral_none(self):
        assert _ma_signal("SMA10", None, 180.0) == "Neutral"

    def test_ma_neutral_equal(self):
        # close == ma → both conditions fail → Neutral
        assert _ma_signal("SMA10", 180.0, 180.0) == "Neutral"


# ---------------------------------------------------------------------------
# 2. Recommendation label mapping
# ---------------------------------------------------------------------------

class TestRecommendationLabel:
    """Recommendation: >= 0.5 → Strong Buy; > 0.1 → Buy; etc."""

    def test_strong_buy(self):
        assert _tech_recommendation_label(0.7) == "Strong Buy"
        assert _tech_recommendation_label(0.5) == "Strong Buy"

    def test_buy(self):
        assert _tech_recommendation_label(0.3) == "Buy"
        assert _tech_recommendation_label(0.11) == "Buy"

    def test_neutral(self):
        assert _tech_recommendation_label(0.0) == "Neutral"
        assert _tech_recommendation_label(0.1) == "Neutral"
        assert _tech_recommendation_label(-0.1) == "Neutral"

    def test_sell(self):
        assert _tech_recommendation_label(-0.3) == "Sell"
        assert _tech_recommendation_label(-0.11) == "Sell"

    def test_strong_sell(self):
        assert _tech_recommendation_label(-0.5) == "Strong Sell"
        assert _tech_recommendation_label(-0.8) == "Strong Sell"

    def test_none_returns_na(self):
        assert _tech_recommendation_label(None) == "N/A"


# ---------------------------------------------------------------------------
# 3. Indicator values from synthetic data
# ---------------------------------------------------------------------------

class TestIndicatorValues:
    def test_uptrend_rsi_above_50(self, calculator, uptrending):
        result = calculator.compute_all(uptrending)
        rsi_val = result["oscillators"]["indicators"].get("RSI", {}).get("value")
        assert rsi_val is not None, "RSI not found in oscillators"
        assert rsi_val > 50, f"Uptrend RSI should be > 50, got {rsi_val}"

    def test_downtrend_rsi_below_50(self, calculator, downtrending):
        result = calculator.compute_all(downtrending)
        rsi_val = result["oscillators"]["indicators"].get("RSI", {}).get("value")
        assert rsi_val is not None
        assert rsi_val < 50, f"Downtrend RSI should be < 50, got {rsi_val}"

    def test_uptrend_smas_below_close(self, calculator, uptrending):
        result = calculator.compute_all(uptrending)
        close = uptrending["Close"].iloc[-1]
        for key, ind in result["moving_averages"]["indicators"].items():
            if key.startswith("SMA") and ind["value"] is not None:
                assert ind["value"] < close, (
                    f"In uptrend, {key}={ind['value']} should be below close={close}"
                )

    def test_downtrend_smas_above_close(self, calculator, downtrending):
        result = calculator.compute_all(downtrending)
        close = downtrending["Close"].iloc[-1]
        for key, ind in result["moving_averages"]["indicators"].items():
            if key.startswith("SMA") and ind["value"] is not None:
                assert ind["value"] > close, (
                    f"In downtrend, {key}={ind['value']} should be above close={close}"
                )


# ---------------------------------------------------------------------------
# 4. Output structure — compute_all()
# ---------------------------------------------------------------------------

class TestOutputStructure:
    def test_top_level_keys(self, calculator, uptrending):
        result = calculator.compute_all(uptrending)
        for key in ("price", "summary", "oscillators", "moving_averages"):
            assert key in result, f"Missing top-level key: {key}"

    def test_summary_structure(self, calculator, uptrending):
        result = calculator.compute_all(uptrending)
        summary = result["summary"]
        for key in ("recommendation", "buy", "sell", "neutral"):
            assert key in summary, f"Missing summary key: {key}"
        assert isinstance(summary["buy"], int)
        assert isinstance(summary["sell"], int)
        assert isinstance(summary["neutral"], int)
        # recommendation is a dict with value and label
        assert isinstance(summary["recommendation"], dict)
        assert "label" in summary["recommendation"]
        assert "value" in summary["recommendation"]

    def test_oscillators_structure(self, calculator, uptrending):
        result = calculator.compute_all(uptrending)
        osc = result["oscillators"]
        for key in ("recommendation", "buy", "sell", "neutral", "indicators"):
            assert key in osc, f"Missing oscillators key: {key}"
        assert isinstance(osc["indicators"], dict)
        assert len(osc["indicators"]) > 0

    def test_moving_averages_structure(self, calculator, uptrending):
        result = calculator.compute_all(uptrending)
        ma = result["moving_averages"]
        for key in ("recommendation", "buy", "sell", "neutral", "indicators"):
            assert key in ma, f"Missing moving_averages key: {key}"
        assert isinstance(ma["indicators"], dict)
        assert len(ma["indicators"]) > 0

    def test_indicator_entry_structure(self, calculator, uptrending):
        """Each indicator dict should have label, value, signal, formatted."""
        result = calculator.compute_all(uptrending)
        for key, ind in result["oscillators"]["indicators"].items():
            assert "label" in ind
            assert "value" in ind
            assert "signal" in ind
            assert ind["signal"] in ("Buy", "Sell", "Neutral")

    def test_price_matches_last_close(self, calculator, uptrending):
        result = calculator.compute_all(uptrending)
        expected = uptrending["Close"].iloc[-1]
        assert result["price"] == pytest.approx(expected, rel=1e-6)

    def test_signal_counts_sum(self, calculator, uptrending):
        """buy + sell + neutral should equal total indicator count."""
        result = calculator.compute_all(uptrending)
        for section in ("oscillators", "moving_averages"):
            s = result[section]
            total = len(s["indicators"])
            assert s["buy"] + s["sell"] + s["neutral"] == total, (
                f"{section}: counts don't sum to {total}"
            )


# ---------------------------------------------------------------------------
# 5. Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_dataframe(self, calculator):
        """Empty DataFrame → should return safe defaults, not crash."""
        empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        result = calculator.compute_all(empty)
        assert result is not None
        assert result["price"] is None
        assert result["summary"]["buy"] == 0
        assert result["summary"]["sell"] == 0

    def test_short_dataframe(self, calculator):
        """DataFrame shorter than minimum period (< 30 rows) → empty technicals."""
        short_data = _make_ohlcv([100 + i * 0.1 for i in range(20)])
        result = calculator.compute_all(short_data)
        assert result is not None
        assert result["price"] is None  # _empty_technicals

    def test_barely_sufficient_data(self, calculator):
        """DataFrame with exactly 30 rows — minimum for computation."""
        data = _make_ohlcv([100 + i * 0.5 for i in range(30)])
        result = calculator.compute_all(data)
        assert result is not None
        assert "summary" in result
        assert "oscillators" in result

    def test_none_history(self, calculator):
        """None history → should return empty technicals."""
        result = calculator.compute_all(None)
        assert result is not None
        assert result["price"] is None
