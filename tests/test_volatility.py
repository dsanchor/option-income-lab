"""Tests for src/volatility.py — realized vol and IV/HV richness."""

import math
from datetime import date

import pytest

from src.volatility import (
    build_volatility_summary,
    classify_richness,
    extract_atm_iv,
    format_volatility_block,
    historical_volatility,
)


def test_historical_volatility_constant_prices_is_zero():
    assert historical_volatility([100.0] * 10) == pytest.approx(0.0, abs=1e-9)


def test_historical_volatility_positive_for_moving_prices():
    closes = [100, 102, 101, 104, 103, 106, 105, 108]
    hv = historical_volatility(closes, window=20)
    assert hv is not None
    assert hv > 0


def test_historical_volatility_insufficient_data():
    assert historical_volatility([100.0]) is None
    assert historical_volatility([]) is None
    assert historical_volatility(None) is None


def test_historical_volatility_ignores_bad_values():
    closes = [100, None, 102, 0, 101, -5, 104]
    hv = historical_volatility(closes)
    assert hv is not None and hv > 0


def test_historical_volatility_annualization():
    # Known daily std → annualized = daily_std * sqrt(252)
    closes = [100, 101, 100, 101, 100, 101, 100, 101]
    hv = historical_volatility(closes, window=20)
    assert hv is not None
    # Should be in a plausible range for ~1% daily oscillation.
    assert 0.05 < hv < 0.5


def _chain(today: date):
    # Two expirations: ~7d and ~30d. ATM strike near 100.
    exp_near = "20240108"   # 7 days from 2024-01-01
    exp_target = "20240131"  # 30 days
    return {
        "calls": {
            exp_near: {
                "100.0": {"strike": 100.0, "iv": 0.20},
            },
            exp_target: {
                "95.0": {"strike": 95.0, "iv": 0.40},
                "100.0": {"strike": 100.0, "iv": 0.30},
                "110.0": {"strike": 110.0, "iv": 0.55},
            },
        },
        "puts": {
            exp_target: {
                "100.0": {"strike": 100.0, "iv": 0.34},
                "90.0": {"strike": 90.0, "iv": 0.50},
            },
        },
    }


def test_extract_atm_iv_picks_target_dte_and_atm_strike():
    today = date(2024, 1, 1)
    result = extract_atm_iv(_chain(today), underlying_price=101.0,
                            target_dte=30, today=today)
    assert result is not None
    atm_iv, dte = result
    # ATM = average of call 0.30 and put 0.34 at the 100 strike, 30d expiry.
    assert atm_iv == pytest.approx((0.30 + 0.34) / 2)
    assert dte == 30


def test_extract_atm_iv_missing_inputs():
    assert extract_atm_iv({}, 100.0) is None
    assert extract_atm_iv(_chain(date(2024, 1, 1)), None) is None


def test_classify_richness_thresholds():
    assert classify_richness(1.5) == "rich"
    assert classify_richness(1.20) == "rich"
    assert classify_richness(1.0) == "fair"
    assert classify_richness(0.9) == "fair"
    assert classify_richness(0.5) == "cheap"
    assert classify_richness(None) is None


def test_build_volatility_summary_full():
    today = date(2024, 1, 1)
    closes = [100, 102, 99, 103, 98, 104, 97, 105, 96, 106]
    summary = build_volatility_summary(
        _chain(today), underlying_price=100.0, closes=closes,
        target_dte=30, hv_window=20, today=today,
    )
    assert summary["atm_iv"] == pytest.approx(0.32, abs=1e-6)
    assert summary["hv"] is not None and summary["hv"] > 0
    assert summary["iv_hv_ratio"] is not None
    assert summary["richness"] in {"rich", "fair", "cheap"}


def test_build_volatility_summary_missing_chain():
    summary = build_volatility_summary({}, 100.0, [100, 101, 102, 103])
    assert summary["atm_iv"] is None
    assert summary["iv_hv_ratio"] is None
    assert summary["richness"] is None
    # HV still computes from closes.
    assert summary["hv"] is not None


def test_format_volatility_block_empty_when_no_signal():
    assert format_volatility_block(None) == ""
    assert format_volatility_block({"atm_iv": None, "hv": None}) == ""


def test_format_volatility_block_renders_lines():
    summary = {
        "atm_iv": 0.32, "atm_dte": 30, "hv": 0.20, "hv_window": 20,
        "iv_hv_ratio": 1.60, "richness": "rich",
    }
    text = format_volatility_block(summary)
    assert "ATM implied volatility" in text
    assert "Realized" in text
    assert "IV/HV ratio: 1.60 (rich)" in text
    assert "favourable" in text
