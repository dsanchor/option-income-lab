"""Tests for unified yfinance data provider module.

Validates yfinance_data_provider.py — the Phase 1 module that replaces
TradingView scraping with direct yfinance API calls for options chains,
stock overview, dividends, analyst forecasts, and technical indicators.

All yfinance API calls are mocked — no real network I/O.
"""

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, PropertyMock, patch, call

import numpy as np
import pandas as pd
import pytest

from src.yfinance_data_provider import (
    YFinanceDataProvider,
    create_provider,
)
from src.yfinance_fetcher import YFinanceFetcher


# ---------------------------------------------------------------------------
# Fixtures — realistic mock data
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_ticker_info():
    """Realistic yfinance Ticker.info dict for AAPL."""
    return {
        "symbol": "AAPL",
        "shortName": "Apple Inc.",
        "longName": "Apple Inc.",
        "exchange": "NMS",
        "fullExchangeName": "NASDAQ",
        "currency": "USD",
        "regularMarketPrice": 185.50,
        "currentPrice": 185.50,
        "regularMarketOpen": 184.20,
        "regularMarketDayHigh": 186.10,
        "regularMarketDayLow": 183.80,
        "regularMarketVolume": 52_000_000,
        "marketCap": 2_900_000_000_000,
        "trailingPE": 30.5,
        "forwardPE": 28.2,
        "dividendYield": 0.0055,
        "dividendRate": 1.00,
        "exDividendDate": 1720000000,
        "beta": 1.25,
        "fiftyTwoWeekHigh": 199.62,
        "fiftyTwoWeekLow": 164.08,
        "fiftyDayAverage": 180.50,
        "twoHundredDayAverage": 178.30,
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "targetMeanPrice": 210.0,
        "targetHighPrice": 250.0,
        "targetLowPrice": 170.0,
        "targetMedianPrice": 208.0,
        "numberOfAnalystOpinions": 35,
        "recommendationKey": "buy",
        "recommendationMean": 1.8,
    }


@pytest.fixture
def mock_option_chain():
    """Realistic yfinance option chain for a single expiration."""
    calls = pd.DataFrame({
        "contractSymbol": ["AAPL260815C00180000", "AAPL260815C00185000", "AAPL260815C00190000"],
        "strike": [180.0, 185.0, 190.0],
        "lastPrice": [8.50, 5.20, 3.10],
        "bid": [8.30, 5.00, 2.90],
        "ask": [8.70, 5.40, 3.30],
        "volume": [1500, 3200, 2800],
        "openInterest": [12000, 18500, 15000],
        "impliedVolatility": [0.28, 0.25, 0.27],
        "inTheMoney": [True, True, False],
        "lastTradeDate": [pd.Timestamp("2026-07-10"), pd.Timestamp("2026-07-10"), pd.Timestamp("2026-07-10")],
    })
    puts = pd.DataFrame({
        "contractSymbol": ["AAPL260815P00180000", "AAPL260815P00185000", "AAPL260815P00190000"],
        "strike": [180.0, 185.0, 190.0],
        "lastPrice": [2.80, 4.70, 7.50],
        "bid": [2.60, 4.50, 7.30],
        "ask": [3.00, 4.90, 7.70],
        "volume": [800, 2100, 1900],
        "openInterest": [9000, 14000, 11000],
        "impliedVolatility": [0.26, 0.24, 0.28],
        "inTheMoney": [False, False, True],
        "lastTradeDate": [pd.Timestamp("2026-07-10"), pd.Timestamp("2026-07-10"), pd.Timestamp("2026-07-10")],
    })
    chain = MagicMock()
    chain.calls = calls
    chain.puts = puts
    return chain


@pytest.fixture
def mock_ohlcv():
    """250-day OHLCV DataFrame for technicals."""
    n = 250
    closes = [150.0 + i * 0.2 + np.sin(i / 10) * 3 for i in range(n)]
    df = pd.DataFrame({
        "Open": [c - 0.5 for c in closes],
        "High": [c + 1.0 for c in closes],
        "Low": [c - 1.0 for c in closes],
        "Close": closes,
        "Volume": [1_000_000 + i * 1000 for i in range(n)],
    })
    df.index = pd.date_range(end=datetime.now(), periods=n, freq="B")
    return df


def _make_expiration_dates(dte_list):
    """Create expiration date strings from DTE values."""
    today = datetime.now(timezone.utc)
    return [(today + timedelta(days=d)).strftime("%Y-%m-%d") for d in dte_list]


@pytest.fixture
def mock_yf_ticker(mock_ticker_info, mock_option_chain, mock_ohlcv):
    """Fully mocked yfinance Ticker object."""
    ticker = MagicMock()
    ticker.info = mock_ticker_info
    # Options expirations: mix within and outside 7-90 DTE range
    exps = _make_expiration_dates([5, 14, 30, 45, 60, 90, 120, 180])
    ticker.options = exps
    ticker.option_chain.return_value = mock_option_chain
    ticker.history.return_value = mock_ohlcv
    ticker.dividends = pd.Series(
        [0.25, 0.25, 0.25, 0.25],
        index=pd.date_range("2025-01-15", periods=4, freq="QS"),
    )
    return ticker


@pytest.fixture
def provider():
    """Provider with default config."""
    fetcher = YFinanceFetcher()
    return YFinanceDataProvider(fetcher)


def _run(coro):
    """Run async coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# 1. Options chain structure
# ---------------------------------------------------------------------------

class TestOptionsChainStructure:
    @patch("src.yfinance_data_provider.yf")
    def test_chain_has_required_top_keys(self, mock_yf, mock_yf_ticker):
        mock_yf.Ticker.return_value = mock_yf_ticker
        provider = create_provider()
        result = _run(provider.fetch_all("AAPL"))
        parsed = json.loads(result["options_chain"])
        for key in ("symbol", "timestamp", "calls", "puts"):
            assert key in parsed, f"Missing key: {key}"

    @patch("src.yfinance_data_provider.yf")
    def test_symbol_in_chain(self, mock_yf, mock_yf_ticker):
        mock_yf.Ticker.return_value = mock_yf_ticker
        provider = create_provider()
        result = _run(provider.fetch_all("AAPL"))
        parsed = json.loads(result["options_chain"])
        assert parsed["symbol"] == "AAPL"

    @patch("src.yfinance_data_provider.yf")
    def test_expiration_keys_yyyymmdd(self, mock_yf, mock_yf_ticker):
        mock_yf.Ticker.return_value = mock_yf_ticker
        provider = create_provider()
        result = _run(provider.fetch_all("AAPL"))
        parsed = json.loads(result["options_chain"])
        import re
        for exp_key in list(parsed.get("calls", {}).keys()) + list(parsed.get("puts", {}).keys()):
            assert re.match(r"^\d{8}$", exp_key), f"Expiration key not YYYYMMDD: {exp_key}"

    @patch("src.yfinance_data_provider.yf")
    def test_contract_has_required_fields(self, mock_yf, mock_yf_ticker):
        mock_yf.Ticker.return_value = mock_yf_ticker
        provider = create_provider()
        result = _run(provider.fetch_all("AAPL"))
        parsed = json.loads(result["options_chain"])
        required_fields = {
            "strike", "bid", "ask", "mid", "iv", "volume", "openInterest",
            "contractSymbol", "delta", "gamma", "theta", "vega", "rho",
        }
        for exp_key, strikes in parsed.get("calls", {}).items():
            for strike_key, contract in strikes.items():
                missing = required_fields - set(contract.keys())
                assert not missing, f"Missing fields: {missing}"
                return

    @patch("src.yfinance_data_provider.yf")
    def test_mid_price_calculation(self, mock_yf, mock_yf_ticker):
        """Mid price uses robust_mid logic (not naive average)."""
        from src.options_math import robust_mid
        mock_yf.Ticker.return_value = mock_yf_ticker
        provider = create_provider()
        result = _run(provider.fetch_all("AAPL"))
        parsed = json.loads(result["options_chain"])
        for exp_key, strikes in parsed.get("calls", {}).items():
            for strike_key, contract in strikes.items():
                expected_mid = robust_mid(contract["bid"], contract["ask"], contract["lastPrice"])
                assert contract["mid"] == pytest.approx(expected_mid, abs=0.01)

    @patch("src.yfinance_data_provider.yf")
    def test_contracts_keyed_by_strike_string(self, mock_yf, mock_yf_ticker):
        mock_yf.Ticker.return_value = mock_yf_ticker
        provider = create_provider()
        result = _run(provider.fetch_all("AAPL"))
        parsed = json.loads(result["options_chain"])
        for exp_key, strikes in parsed.get("calls", {}).items():
            for strike_key in strikes:
                try:
                    float(strike_key)
                except ValueError:
                    pytest.fail(f"Strike key '{strike_key}' is not a numeric string")

    @patch("src.yfinance_data_provider.yf")
    def test_greeks_populated_for_nonzero_iv(self, mock_yf, mock_yf_ticker):
        """Contracts with IV > 0 should have computed Greeks."""
        mock_yf.Ticker.return_value = mock_yf_ticker
        provider = create_provider()
        result = _run(provider.fetch_all("AAPL"))
        parsed = json.loads(result["options_chain"])
        for exp_key, strikes in parsed.get("calls", {}).items():
            for strike_key, contract in strikes.items():
                if contract.get("iv", 0) > 0:
                    assert contract["delta"] != 0 or contract["gamma"] != 0, (
                        f"Greeks should be non-zero for IV={contract['iv']}"
                    )


# ---------------------------------------------------------------------------
# 2. Overview structure
# ---------------------------------------------------------------------------

class TestOverviewStructure:
    @patch("src.yfinance_data_provider.yf")
    def test_overview_is_valid_json(self, mock_yf, mock_yf_ticker):
        mock_yf.Ticker.return_value = mock_yf_ticker
        provider = create_provider()
        result = _run(provider.fetch_all("AAPL"))
        parsed = json.loads(result["overview"])
        assert parsed is not None
        assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# 3. Dividends
# ---------------------------------------------------------------------------

class TestDividendsStructure:
    @patch("src.yfinance_data_provider.yf")
    def test_dividends_is_valid_json(self, mock_yf, mock_yf_ticker):
        mock_yf.Ticker.return_value = mock_yf_ticker
        provider = create_provider()
        result = _run(provider.fetch_all("AAPL"))
        parsed = json.loads(result["dividends"])
        assert parsed is not None


# ---------------------------------------------------------------------------
# 4. Forecast
# ---------------------------------------------------------------------------

class TestForecastStructure:
    @patch("src.yfinance_data_provider.yf")
    def test_forecast_is_valid_json(self, mock_yf, mock_yf_ticker):
        mock_yf.Ticker.return_value = mock_yf_ticker
        provider = create_provider()
        result = _run(provider.fetch_all("AAPL"))
        parsed = json.loads(result["forecast"])
        assert parsed is not None


# ---------------------------------------------------------------------------
# 5. Technicals — delegates to TechnicalsCalculator
# ---------------------------------------------------------------------------

class TestTechnicalsStructure:
    @patch("src.yfinance_data_provider.yf")
    def test_technicals_is_valid_json(self, mock_yf, mock_yf_ticker):
        mock_yf.Ticker.return_value = mock_yf_ticker
        provider = create_provider()
        result = _run(provider.fetch_all("AAPL"))
        parsed = json.loads(result["technicals"])
        assert parsed is not None

    @patch("src.yfinance_data_provider.yf")
    def test_technicals_has_summary(self, mock_yf, mock_yf_ticker):
        mock_yf.Ticker.return_value = mock_yf_ticker
        provider = create_provider()
        result = _run(provider.fetch_all("AAPL"))
        parsed = json.loads(result["technicals"])
        assert "summary" in parsed


# ---------------------------------------------------------------------------
# 6. fetch_all integration
# ---------------------------------------------------------------------------

class TestFetchAll:
    @patch("src.yfinance_data_provider.yf")
    def test_fetch_all_has_all_keys(self, mock_yf, mock_yf_ticker):
        mock_yf.Ticker.return_value = mock_yf_ticker
        provider = create_provider()
        result = _run(provider.fetch_all("AAPL"))
        expected_keys = {"overview", "technicals", "forecast", "dividends", "options_chain"}
        assert expected_keys.issubset(set(result.keys())), (
            f"Missing keys: {expected_keys - set(result.keys())}"
        )

    @patch("src.yfinance_data_provider.yf")
    def test_fetch_all_values_are_json_strings(self, mock_yf, mock_yf_ticker):
        mock_yf.Ticker.return_value = mock_yf_ticker
        provider = create_provider()
        result = _run(provider.fetch_all("AAPL"))
        for key, value in result.items():
            assert isinstance(value, str), f"fetch_all()['{key}'] should be str"
            assert len(value) > 0, f"fetch_all()['{key}'] is empty"
            # Each value should be valid JSON
            json.loads(value)  # Should not raise


# ---------------------------------------------------------------------------
# 7. Cache behavior
# ---------------------------------------------------------------------------

class TestCacheBehavior:
    @patch("src.yfinance_data_provider.yf")
    def test_second_call_uses_cache(self, mock_yf, mock_yf_ticker):
        """Two fetch_all calls → yfinance Ticker only created once."""
        mock_yf.Ticker.return_value = mock_yf_ticker
        provider = create_provider()
        _run(provider.fetch_all("AAPL"))
        _run(provider.fetch_all("AAPL"))
        # Should only call Ticker once due to cache
        assert mock_yf.Ticker.call_count == 1

    @patch("src.yfinance_data_provider.yf")
    def test_force_refresh_bypasses_cache(self, mock_yf, mock_yf_ticker):
        """force_refresh=True should fetch fresh data."""
        mock_yf.Ticker.return_value = mock_yf_ticker
        provider = create_provider()
        _run(provider.fetch_all("AAPL"))
        _run(provider.fetch_all("AAPL", force_refresh=True))
        assert mock_yf.Ticker.call_count == 2


# ---------------------------------------------------------------------------
# 8. DTE filtering
# ---------------------------------------------------------------------------

class TestDTEFiltering:
    @patch("src.yfinance_data_provider.yf")
    def test_only_7_to_90_dte_included(self, mock_yf, mock_yf_ticker):
        """Expirations outside 7-90 DTE should be excluded."""
        mock_yf.Ticker.return_value = mock_yf_ticker
        provider = create_provider()
        result = _run(provider.fetch_all("AAPL"))
        parsed = json.loads(result["options_chain"])

        today = datetime.now(timezone.utc)
        for exp_key in list(parsed.get("calls", {}).keys()) + list(parsed.get("puts", {}).keys()):
            exp_date = datetime.strptime(exp_key, "%Y%m%d").replace(tzinfo=timezone.utc)
            dte = (exp_date - today).days
            assert 6 <= dte <= 91, f"Expiration {exp_key} has DTE={dte}, outside 7-90 range"

    @patch("src.yfinance_data_provider.yf")
    def test_near_term_excluded(self, mock_yf):
        """Expirations < 7 DTE should be filtered out."""
        ticker = MagicMock()
        ticker.info = {"symbol": "TEST", "regularMarketPrice": 100}
        near_exps = _make_expiration_dates([1, 2, 3, 5, 6])
        ticker.options = near_exps
        ticker.option_chain.return_value = MagicMock(
            calls=pd.DataFrame(columns=["contractSymbol", "strike", "bid", "ask", "volume", "openInterest", "impliedVolatility"]),
            puts=pd.DataFrame(columns=["contractSymbol", "strike", "bid", "ask", "volume", "openInterest", "impliedVolatility"]),
        )
        ticker.history.return_value = pd.DataFrame()
        ticker.dividends = pd.Series()
        mock_yf.Ticker.return_value = ticker

        provider = create_provider()
        result = _run(provider.fetch_all("TEST"))
        parsed = json.loads(result["options_chain"])
        assert len(parsed.get("calls", {})) == 0, "Near-term expirations should be filtered"
        assert len(parsed.get("puts", {})) == 0


# ---------------------------------------------------------------------------
# 9. create_provider factory
# ---------------------------------------------------------------------------

class TestCreateProvider:
    def test_returns_yfinance_provider_instance(self):
        provider = create_provider()
        assert isinstance(provider, YFinanceDataProvider)

    def test_custom_config_applied(self):
        provider = create_provider({"min_dte": 10, "max_dte": 60})
        assert provider._min_dte == 10
        assert provider._max_dte == 60


# ---------------------------------------------------------------------------
# 10. Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    @patch("src.yfinance_data_provider.yf")
    def test_empty_info_returns_valid_result(self, mock_yf):
        """When yfinance returns empty info, should not crash."""
        ticker = MagicMock()
        ticker.info = {}
        ticker.options = []
        ticker.history.return_value = pd.DataFrame()
        ticker.dividends = pd.Series()
        mock_yf.Ticker.return_value = ticker

        provider = create_provider()
        result = _run(provider.fetch_all("INVALID"))
        assert result is not None
        assert "overview" in result

    @patch("src.yfinance_data_provider.yf")
    def test_empty_options_chain(self, mock_yf):
        """When no options data, should return empty chain, not crash."""
        ticker = MagicMock()
        ticker.info = {"symbol": "NOOPT", "regularMarketPrice": 50}
        ticker.options = []
        ticker.history.return_value = pd.DataFrame()
        ticker.dividends = pd.Series()
        mock_yf.Ticker.return_value = ticker

        provider = create_provider()
        result = _run(provider.fetch_all("NOOPT"))
        parsed = json.loads(result["options_chain"])
        assert parsed.get("calls") == {}
        assert parsed.get("puts") == {}

    @patch("src.yfinance_data_provider.yf")
    def test_info_exception_handled(self, mock_yf):
        """When ticker.info raises, provider handles gracefully."""
        ticker = MagicMock()
        type(ticker).info = PropertyMock(side_effect=Exception("Rate limited"))
        ticker.options = []
        ticker.history.return_value = pd.DataFrame()
        ticker.dividends = pd.Series()
        mock_yf.Ticker.return_value = ticker

        provider = create_provider()
        result = _run(provider.fetch_all("FAIL"))
        assert result is not None

    @patch("src.yfinance_data_provider.yf")
    def test_empty_history_for_technicals(self, mock_yf):
        """Empty price history → technicals should still return valid JSON."""
        ticker = MagicMock()
        ticker.info = {"symbol": "NOHIST", "regularMarketPrice": 100}
        ticker.options = []
        ticker.history.return_value = pd.DataFrame()
        ticker.dividends = pd.Series()
        mock_yf.Ticker.return_value = ticker

        provider = create_provider()
        result = _run(provider.fetch_all("NOHIST"))
        parsed = json.loads(result["technicals"])
        assert parsed is not None
