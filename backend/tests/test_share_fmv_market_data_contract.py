from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.portfolio import fx_service
from src.portfolio.share_fmv_service import YahooFmvError, build_yahoo_share_fmv
from src.symbol_pricing import normalize_quote_price
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


@pytest.mark.parametrize("provider_currency", ["GBp", "GBX"])
def test_uk_pence_open_is_normalized_to_gbp_before_fx(provider_currency):
    result = build_yahoo_share_fmv(
        quantity="2",
        trade_date="2026-09-28",
        security={"listing_currency": "GBP"},
        provider_symbol="TEST.L",
        observation={
            "status": "ok",
            "open": "4500",
            "market_session_date": "2026-09-28",
            "currency": provider_currency,
        },
        fx_getter=lambda *args, **kwargs: ("1.2", "2026-09-28"),
    )
    assert result["price_per_share"] == "45.000000"
    assert result["amount"] == "90.000000"
    assert result["price_per_share_eur"] == "54.000000"
    assert result["price_per_share"] != "4500.000000"


def test_uk_native_gbp_open_is_not_divided_by_100():
    result = build_yahoo_share_fmv(
        quantity="1",
        trade_date="2026-09-28",
        security={"listing_currency": "GBP"},
        provider_symbol="TEST.L",
        observation={
            "status": "ok",
            "open": "45",
            "market_session_date": "2026-09-28",
            "currency": "GBP",
        },
        fx_getter=lambda *args, **kwargs: ("1", "2026-09-28"),
    )
    assert result["price_per_share"] == "45.000000"


@pytest.mark.parametrize("provider_currency", ["USD", "gbp", "GBx", "", None])
def test_uk_invalid_or_mismatched_quote_currency_fails_closed(provider_currency):
    with pytest.raises(YahooFmvError) as exc_info:
        build_yahoo_share_fmv(
            quantity="1",
            trade_date="2026-09-28",
            security={"listing_currency": "GBP"},
            provider_symbol="TEST.L",
            observation={
                "status": "ok",
                "open": "4500",
                "market_session_date": "2026-09-28",
                "currency": provider_currency,
            },
            fx_getter=lambda *args, **kwargs: ("1", "2026-09-28"),
        )
    assert exc_info.value.status_code == 422


def test_daily_open_preserves_yahoo_minor_unit_currency():
    ticker = MagicMock()
    ticker.info = {"currency": "GBp"}
    ticker.history.return_value = pd.DataFrame(
        {"Open": [4500]}, index=pd.to_datetime(["2026-09-28"])
    )
    with patch("src.yfinance_fetcher.yf.Ticker", return_value=ticker):
        result = YFinanceFetcher().get_daily_open("TEST.L", "2026-09-28")
    assert result["currency"] == "GBp"


@pytest.mark.parametrize(
    ("provider_currency", "raw_price", "expected_price", "expected_currency", "unit"),
    [
        ("GBp", "4500", Decimal(45), "GBP", "minor"),
        ("GBX", "4500", Decimal(45), "GBP", "minor"),
        ("ILA", "1234", Decimal("12.34"), "ILS", "minor"),
        ("ZAc", "9876", Decimal("98.76"), "ZAR", "minor"),
        ("GBP", "45", Decimal(45), "GBP", "major"),
        ("ILS", "12.34", Decimal("12.34"), "ILS", "major"),
        ("ZAR", "98.76", Decimal("98.76"), "ZAR", "major"),
    ],
)
def test_supported_provider_currency_units_are_normalized_exactly_once(
    provider_currency, raw_price, expected_price, expected_currency, unit
):
    assert normalize_quote_price(raw_price, provider_currency) == (
        expected_price,
        expected_currency,
        unit,
        provider_currency,
    )


def test_daily_open_no_session_includes_safe_request_window():
    ticker = MagicMock()
    ticker.history.return_value = pd.DataFrame()
    with patch("src.yfinance_fetcher.yf.Ticker", return_value=ticker):
        result = YFinanceFetcher().get_daily_open("ULVR.L", "2026-09-26")
    assert result == {
        "status": "no_market_session",
        "requested_date": "2026-09-26",
        "provider_symbol": "ULVR.L",
        "max_calendar_days": 7,
        "window_end_date": "2026-10-03",
    }


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
    response.content = (
        b'<Cube time="2026-09-25"><Cube currency="GBP" rate="0.875000"/></Cube>'
    )
    response.raise_for_status.return_value = None
    monkeypatch.setattr(fx_service.requests, "get", lambda *args, **kwargs: response)
    fx_service._fetch_and_cache()
    rate, effective = fx_service.get_historical_fx_rate(
        "GBP", rate_date="2026-09-25"
    )
    assert Decimal(rate) == Decimal("1.142857143")
    assert effective == "2026-09-25"


def test_ecb_compact_full_history_parses_realistic_old_gbp_dates(monkeypatch):
    response = MagicMock()
    response.content = (
        b'<?xml version="1.0"?><Envelope><Cube>'
        b'<Cube time="2026-09-25"><Cube currency="GBP" rate="0.86045"/></Cube>'
        b'<Cube time="2001-09-14"><Cube currency="GBP" rate="0.6247"/></Cube>'
        b'<Cube time="2001-09-13"><Cube currency="GBP" rate="0.6251"/></Cube>'
        b"</Cube></Envelope>"
    )
    response.raise_for_status.return_value = None
    calls = []

    def fetch(*args, **kwargs):
        calls.append((args, kwargs))
        return response

    monkeypatch.setattr(fx_service.requests, "get", fetch)

    rate, effective = fx_service.get_historical_fx_rate(
        "GBP", rate_date="2001-09-14"
    )
    cached = fx_service.get_historical_fx_rate("GBP", rate_date="2001-09-14")

    assert Decimal(rate) == Decimal("1.600768369")
    assert effective == "2001-09-14"
    assert cached == (rate, effective)
    assert len(calls) == 1
    assert calls[0][1]["timeout"] == (5, 20)
    assert calls[0][1]["headers"]["User-Agent"] == "option-income-lab/1.0"


def test_old_gbp_weekend_uses_last_prior_publication(monkeypatch):
    monkeypatch.setattr(fx_service, "_ensure_cache_fresh", lambda: None)
    fx_service._rate_cache[("2001-09-14", "GBP")] = "1.600768369"

    assert fx_service.get_historical_fx_rate(
        "GBP", rate_date="2001-09-16"
    ) == ("1.600768369", "2001-09-14")


def test_pre_ecb_and_future_dates_fail_closed(monkeypatch):
    monkeypatch.setattr(fx_service, "_ensure_cache_fresh", lambda: None)
    fx_service._rate_cache[("1999-01-04", "GBP")] = "1.406271973"

    with pytest.raises(fx_service.FxRateNotFoundError):
        fx_service.get_historical_fx_rate("GBP", rate_date="1998-12-31")
    with pytest.raises(fx_service.FxRateNotFoundError):
        fx_service.get_historical_fx_rate("GBP", rate_date="2099-01-01")
