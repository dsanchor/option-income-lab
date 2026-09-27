from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.portfolio import fx_service
from src.yfinance_fetcher import YFinanceFetcher


@pytest.fixture(autouse=True)
def clear_fx_cache():
    fx_service._rate_cache.clear()
    fx_service._cache_fetched_date = None
    yield
    fx_service._rate_cache.clear()
    fx_service._cache_fetched_date = None


def test_daily_open_uses_directed_unadjusted_history_and_next_session():
    ticker = MagicMock()
    ticker.info = {"currency": "GBP"}
    ticker.history.return_value = pd.DataFrame(
        {"Open": [12.5, 13.0], "Close": [99, 98]},
        index=pd.to_datetime(["2026-09-28", "2026-09-29"]),
    )
    with patch("src.yfinance_fetcher.yf.Ticker", return_value=ticker):
        result = YFinanceFetcher().get_daily_open("ULVR.L", "2026-09-26")
    assert result == {
        "status": "ok",
        "open": "12.5",
        "market_session_date": "2026-09-28",
        "currency": "GBP",
    }
    ticker.history.assert_called_once_with(
        start="2026-09-26",
        end="2026-10-04",
        auto_adjust=False,
        actions=False,
    )


@pytest.mark.parametrize("bad_open", [None, 0, -1, float("nan"), float("inf")])
def test_daily_open_fails_closed_without_close_fallback(bad_open):
    ticker = MagicMock()
    ticker.info = {"currency": "GBP"}
    ticker.history.return_value = pd.DataFrame(
        {"Open": [bad_open], "Close": [999]},
        index=pd.to_datetime(["2026-09-28"]),
    )
    with patch("src.yfinance_fetcher.yf.Ticker", return_value=ticker):
        result = YFinanceFetcher().get_daily_open("ULVR.L", "2026-09-28")
    assert result["status"] == "invalid_open"
    assert "open" not in result


def test_daily_open_fails_closed_when_currency_is_missing():
    ticker = MagicMock()
    ticker.info = {}
    ticker.history.return_value = pd.DataFrame(
        {"Open": [12.5]}, index=pd.to_datetime(["2026-09-28"])
    )
    with patch("src.yfinance_fetcher.yf.Ticker", return_value=ticker):
        result = YFinanceFetcher().get_daily_open("ULVR.L", "2026-09-28")
    assert result["status"] == "currency_unavailable"


def test_historical_fx_returns_effective_prior_ecb_date_with_five_day_limit(monkeypatch):
    monkeypatch.setattr(fx_service, "_ensure_cache_fresh", lambda: None)
    fx_service._rate_cache[("2026-09-25", "GBP")] = "1.166480000"
    assert fx_service.get_historical_fx_rate("GBP", rate_date="2026-09-27") == (
        "1.166480000",
        "2026-09-25",
    )
    fx_service._rate_cache.clear()
    fx_service._rate_cache[("2026-09-21", "GBP")] = "1.100000000"
    with pytest.raises(fx_service.FxRateNotFoundError):
        fx_service.get_historical_fx_rate("GBP", rate_date="2026-09-27")


def test_ecb_rate_is_inverted_to_eur_per_native_unit(monkeypatch):
    response = MagicMock()
    response.text = (
        '<Cube time="2026-09-25"><Cube currency="GBP" rate="0.875000"/></Cube>'
    )
    response.raise_for_status.return_value = None
    monkeypatch.setattr(fx_service.requests, "get", lambda *args, **kwargs: response)
    fx_service._fetch_and_cache()
    rate, effective = fx_service.get_historical_fx_rate(
        "GBP", rate_date="2026-09-25"
    )
    assert Decimal(rate) == Decimal("1.142857143")
    assert effective == "2026-09-25"
