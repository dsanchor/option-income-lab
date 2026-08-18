"""Regression tests for a real Buy Tracker data-availability bug.

Bug report: Buy Tracker output showed `Score 0/5` with the reason "The
required score_breakdown was missing, so all canonical dimensions were
validated as 0. Canonical SMA50, SMA200, Stochastic confirmation, and
dividend-growth-years data are also unavailable" even though upstream
provider data existed.

Root cause (traced and reproduced against real yfinance data, see
`.squad/decisions/inbox/rusty-buy-tracker-canonical-availability-fix.md`):

1. `YFinanceDataProvider.fetch_all()`'s `ticker.history(period="1y")` can
   return a trailing row for the current session before its `Close` has
   actually been recorded (yfinance data lag / a request landing
   mid-session). That NaN close silently blanks the *last* value of any
   rolling-window indicator (`SMA*`, `Stoch.K`) computed over it, while
   EMA/RSI/MACD-style recursive indicators forward-fill through the same
   NaN and keep reporting the prior day's value instead — SMA50, SMA200,
   and Stochastic confirmation looked "unavailable" even though the
   underlying market data existed one row earlier.
2. `YFinanceDataProvider._build_dividends()`'s consecutive-dividend-growth
   computation compared the *current, still-in-progress* calendar year's
   partial dividend total against the prior *complete* year — which
   always looks like a cut (fewer payments so far this year), breaking
   the streak at `growth_years = 0` for effectively every dividend-paying
   stock evaluated before the current year fully completes.

These tests lock in the fix for both, and confirm the downstream Buy
Tracker evidence pipeline (`build_buy_tracker_evidence`) now sees these
canonical fields once the upstream provider data is no longer starved.

Independently cross-checked against live yfinance data for AAPL, MSFT, KO,
JNJ, and PG (Basher review, 2026-08-18): the fixed `_build_dividends`
produces `continuous_dividend_growth` == 23 for KO, 63 for JNJ, and 22 for
PG — matching Basher's expected values exactly. The multi-decade streak
test below mirrors the JNJ magnitude with synthetic, deterministic data.
"""

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from src.rule_evaluator import build_buy_tracker_evidence
from src.yfinance_data_provider import (
    YFinanceDataProvider,
    _drop_incomplete_trailing_bars,
)
from src.yfinance_fetcher import YFinanceFetcher


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int = 260, *, trailing_nan_close: bool = False) -> pd.DataFrame:
    """Deterministic upward-trending OHLCV history, optionally with a
    trailing row whose Close is NaN (simulating an incomplete session)."""
    closes = [100.0 + i * 0.15 + np.sin(i / 9) * 2.5 for i in range(n)]
    df = pd.DataFrame({
        "Open": [c - 0.4 for c in closes],
        "High": [c + 0.8 for c in closes],
        "Low": [c - 0.8 for c in closes],
        "Close": closes,
        "Volume": [1_000_000 + i * 500 for i in range(n)],
    })
    df.index = pd.date_range(end=datetime.now(), periods=n, freq="B")

    if trailing_nan_close:
        extra_date = df.index[-1] + pd.tseries.offsets.BDay(1)
        extra_row = pd.DataFrame(
            {
                "Open": [closes[-1] + 0.1],
                "High": [closes[-1] + 0.5],
                "Low": [closes[-1] - 0.5],
                "Close": [np.nan],
                "Volume": [900_000],
            },
            index=[extra_date],
        )
        df = pd.concat([df, extra_row])

    return df


@pytest.fixture
def provider():
    return YFinanceDataProvider(YFinanceFetcher())


def _dividend_series(*, completed_year_amounts, current_year_partial=None):
    """Build a quarterly dividend Series across the given completed years
    (each fully paid in 4 installments), optionally followed by a partial
    payment in the current, still-in-progress calendar year."""
    now = datetime.now(timezone.utc)
    current_year = now.year
    n_years = len(completed_year_amounts)
    start_year = current_year - n_years

    dates, values = [], []
    for offset, amount in enumerate(completed_year_amounts):
        year = start_year + offset
        quarterly = amount / 4
        for month in (3, 6, 9, 12):
            dates.append(pd.Timestamp(year=year, month=month, day=15, tz="America/New_York"))
            values.append(quarterly)

    if current_year_partial is not None:
        dates.append(pd.Timestamp(year=current_year, month=3, day=15, tz="America/New_York"))
        values.append(current_year_partial)

    return pd.Series(values, index=pd.DatetimeIndex(dates))


class _FakeTicker:
    """Minimal ticker stand-in exposing only what `_build_dividends` reads."""

    def __init__(self, dividends):
        self.dividends = dividends


# ---------------------------------------------------------------------------
# 1. `_drop_incomplete_trailing_bars` helper — pure unit tests
# ---------------------------------------------------------------------------

class TestDropIncompleteTrailingBars:
    def test_trailing_nan_close_row_is_dropped(self):
        history = _make_ohlcv(trailing_nan_close=True)
        assert pd.isna(history["Close"].iloc[-1])
        trimmed = _drop_incomplete_trailing_bars(history)
        assert not pd.isna(trimmed["Close"].iloc[-1])
        assert len(trimmed) == len(history) - 1

    def test_multiple_trailing_nan_rows_all_dropped(self):
        history = _make_ohlcv(trailing_nan_close=True)
        # Append a second incomplete row on top of the first.
        extra_date = history.index[-1] + pd.tseries.offsets.BDay(1)
        extra_row = pd.DataFrame(
            {"Open": [np.nan], "High": [np.nan], "Low": [np.nan],
             "Close": [np.nan], "Volume": [0]},
            index=[extra_date],
        )
        history = pd.concat([history, extra_row])
        trimmed = _drop_incomplete_trailing_bars(history)
        assert not pd.isna(trimmed["Close"].iloc[-1])
        assert len(trimmed) == len(history) - 2

    def test_no_trailing_nan_leaves_history_unchanged(self):
        history = _make_ohlcv(trailing_nan_close=False)
        trimmed = _drop_incomplete_trailing_bars(history)
        assert len(trimmed) == len(history)
        pd.testing.assert_frame_equal(trimmed, history)

    def test_empty_history_returns_unchanged(self):
        empty = pd.DataFrame()
        assert _drop_incomplete_trailing_bars(empty) is empty

    def test_interior_nan_close_is_preserved_only_trailing_is_trimmed(self):
        history = _make_ohlcv(trailing_nan_close=True)
        # An interior gap (e.g. a legitimate historical data hole) must
        # survive — only the *trailing* incomplete bar is dropped.
        history.iloc[10, history.columns.get_loc("Close")] = np.nan
        trimmed = _drop_incomplete_trailing_bars(history)
        assert pd.isna(trimmed["Close"].iloc[10])
        assert not pd.isna(trimmed["Close"].iloc[-1])


# ---------------------------------------------------------------------------
# 2. Technicals: SMA/Stochastic availability despite a trailing NaN bar
# ---------------------------------------------------------------------------

class TestTechnicalsSurvivesTrailingIncompleteBar:
    def test_sma_and_stochastic_present_with_trailing_nan_close(self, provider):
        history = _drop_incomplete_trailing_bars(_make_ohlcv(trailing_nan_close=True))
        tech = json.loads(provider._build_technicals(history, {}))
        ma_indicators = tech["moving_averages"]["indicators"]
        osc_indicators = tech["oscillators"]["indicators"]
        assert "SMA50" in ma_indicators
        assert "SMA200" in ma_indicators
        assert "Stoch.K" in osc_indicators
        assert ma_indicators["SMA50"]["value"] is not None
        assert ma_indicators["SMA200"]["value"] is not None

    def test_normal_history_without_trailing_nan_still_has_sma(self, provider):
        """Regression: the fix must not affect the ordinary, already-working case."""
        history = _make_ohlcv(trailing_nan_close=False)
        tech = json.loads(provider._build_technicals(history, {}))
        assert "SMA50" in tech["moving_averages"]["indicators"]
        assert "Stoch.K" in tech["oscillators"]["indicators"]


# ---------------------------------------------------------------------------
# 3. Dividends: growth-years availability despite an in-progress current year
# ---------------------------------------------------------------------------

class TestDividendGrowthYearsAvailability:
    def test_growth_years_available_despite_partial_current_year(self, provider):
        series = _dividend_series(
            completed_year_amounts=[1.00, 1.10, 1.20, 1.30, 1.40],
            current_year_partial=0.36,  # far below any full year — would look like a cut
        )
        dividends = json.loads(
            provider._build_dividends({}, _FakeTicker(series))
        )["dividends"]
        assert "continuous_dividend_growth" in dividends
        assert dividends["continuous_dividend_growth"]["value"] == 4

    def test_genuine_cut_in_a_completed_year_still_breaks_streak(self, provider):
        # 2 growth years, then a real cut, then more (unrelated) growth —
        # the streak must stop at the real cut, not be inflated by it.
        series = _dividend_series(
            completed_year_amounts=[1.00, 1.10, 1.20, 0.80, 0.90],
            current_year_partial=0.25,
        )
        dividends = json.loads(
            provider._build_dividends({}, _FakeTicker(series))
        )["dividends"]
        assert dividends.get("continuous_dividend_growth", {}).get("value", 0) == 1

    def test_no_dividend_history_leaves_field_absent(self, provider):
        dividends = json.loads(
            provider._build_dividends({}, _FakeTicker(pd.Series(dtype=float)))
        )["dividends"]
        assert "continuous_dividend_growth" not in dividends

    def test_long_multi_decade_streak_with_partial_current_year(self, provider):
        # Mirrors Basher's independent live cross-check (JNJ ~63 consecutive
        # growth years) — a long streak must not be truncated or overflow,
        # and the partial current year must still be excluded correctly.
        completed_year_amounts = [1.0 + i * 0.05 for i in range(63)]
        series = _dividend_series(
            completed_year_amounts=completed_year_amounts,
            current_year_partial=0.20,  # well below a quarter of the last full year
        )
        dividends = json.loads(
            provider._build_dividends({}, _FakeTicker(series))
        )["dividends"]
        assert dividends["continuous_dividend_growth"]["value"] == 62


# ---------------------------------------------------------------------------
# 4. Integration: canonical Buy Tracker evidence sees the fixed fields
# ---------------------------------------------------------------------------

class TestBuyTrackerEvidenceSeesFixedCanonicalFields:
    def test_sma_stochastic_and_dividend_growth_years_reach_canonical_evidence(self, provider):
        history = _drop_incomplete_trailing_bars(_make_ohlcv(trailing_nan_close=True))
        series = _dividend_series(
            completed_year_amounts=[1.00, 1.10, 1.20, 1.30, 1.40],
            current_year_partial=0.36,
        )
        fetch_data = {
            "overview": provider._build_overview({}, current_price=150.0),
            "technicals": provider._build_technicals(history, {}),
            "forecast": json.dumps({}),
            "dividends": provider._build_dividends({}, _FakeTicker(series)),
        }

        evidence = build_buy_tracker_evidence(fetch_data)

        assert evidence["sma50"] is not None
        assert evidence["sma200"] is not None
        assert evidence["stochastic_confirmation"] in {"BUY", "NEUTRAL", "SELL"}
        assert evidence["dividend_growth_years"] == 4
