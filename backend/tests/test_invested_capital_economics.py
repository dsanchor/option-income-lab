"""Tests for the invested-capital (buys/sells) economics aggregation."""

from fastapi.testclient import TestClient

from tests.test_economics import ACCOUNT_1, ACCOUNT_2, _movement
from web.app import _build_invested_capital_report, app


def _sample_stock_movements():
    return [
        _movement("s1", "BUY", "XNAS:AAPL", None, "2024-03-01", ACCOUNT_1, "1000", "5", "1005"),
        _movement("s2", "BUY", "XNAS:AAPL", None, "2024-06-01", ACCOUNT_1, "500", "2", "502"),
        _movement("s3", "SELL", "XNAS:AAPL", None, "2024-09-01", ACCOUNT_1, "300", "1", "299"),
        _movement("s4", "BUY", "XNAS:GOOG", None, "2025-01-15", ACCOUNT_2, "2000", "10", "2010"),
        _movement("s5", "SELL", "XNAS:GOOG", None, "2025-02-15", ACCOUNT_2, "2200", "10", "2190"),
    ]


def test_build_invested_capital_report_aggregates_by_year():
    report = _build_invested_capital_report(_sample_stock_movements())

    assert [row["year"] for row in report["yearly"]] == [2024, 2025]
    year_2024 = report["yearly"][0]
    assert year_2024["buys_eur"] == 1507.0
    assert year_2024["sells_eur"] == 299.0
    assert year_2024["net_invested_eur"] == 1208.0
    assert year_2024["buy_count"] == 2
    assert year_2024["sell_count"] == 1

    year_2025 = report["yearly"][1]
    assert year_2025["buys_eur"] == 2010.0
    assert year_2025["sells_eur"] == 2190.0
    assert year_2025["net_invested_eur"] == -180.0

    cumulative = report["cumulative"]
    assert cumulative[0]["cumulative_net_invested_eur"] == 1208.0
    # Cumulative across both years = (1507 + 2010) - (299 + 2190) = 1028
    assert cumulative[1]["cumulative_net_invested_eur"] == 1028.0
    assert cumulative[1]["cumulative_buys_eur"] == 3517.0
    assert cumulative[1]["cumulative_sells_eur"] == 2489.0

    assert report["filters"]["symbols"] == ["AAPL", "GOOG"]
    assert report["filters"]["account_ids"] == [ACCOUNT_1, ACCOUNT_2]


def test_build_invested_capital_report_filters_by_symbol_and_account():
    report = _build_invested_capital_report(
        _sample_stock_movements(), symbol_filter=["AAPL"]
    )
    assert [row["year"] for row in report["yearly"]] == [2024]
    assert report["yearly"][0]["buys_eur"] == 1507.0

    report = _build_invested_capital_report(
        _sample_stock_movements(), account_filter=[ACCOUNT_2]
    )
    assert [row["year"] for row in report["yearly"]] == [2025]


def test_build_invested_capital_report_ignores_option_movements():
    movements = _sample_stock_movements() + [
        _movement("o1", "CALL_SELL", "XNAS:AAPL", "pos-1", "2024-04-01", ACCOUNT_1, "1.0", "0.1", "0.9"),
    ]
    report = _build_invested_capital_report(movements)
    # Options movements must not leak into buy/sell totals.
    assert report["yearly"][0]["buys_eur"] == 1507.0


class _FakeCapitalCosmos:
    def __init__(self, movements):
        self.container = None
        self.portfolio_container = _FakePortfolioContainer(movements)


class _FakePortfolioContainer:
    def __init__(self, movements):
        self._store = {m["id"]: dict(m) for m in movements}

    def query_items(self, query, enable_cross_partition_query=True):
        return list(self._store.values())


def test_api_economics_capital_endpoint_smoke():
    original_cosmos = getattr(app.state, "cosmos", None)
    original_error = getattr(app.state, "cosmos_error", None)
    fake = _FakeCapitalCosmos(_sample_stock_movements())
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            app.state.cosmos = fake
            app.state.cosmos_error = None
            response = client.get("/api/economics/capital")
    finally:
        app.state.cosmos = original_cosmos
        app.state.cosmos_error = original_error

    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {"yearly", "cumulative", "filters", "applied_filters"}
    assert len(body["yearly"]) == 2
