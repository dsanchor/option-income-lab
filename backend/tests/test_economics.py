from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from src.dividends_economics import build_dividends_economics_report
from tests.conftest_portfolio_p2 import FakeCosmos
from web.app import app
from web.app import _apply_dividends_yoc, _build_economics_report


ACCOUNT_1 = "acct-1"
ACCOUNT_2 = "acct-2"


def _sample_option_symbol_docs():
    return [
        {
            "symbol": "AAPL",
            "security_id": "XNAS:AAPL",
            "positions": [
                {
                    "position_id": "aapl-call-win",
                    "type": "call",
                    "strike": 100,
                    "expiration": "2026-01-31",
                    "opened_at": "2026-01-01T00:00:00Z",
                    "closed_at": "2026-01-20T00:00:00Z",
                    "status": "closed",
                    "close_reason": "manual",
                    "source": {"premium": 2.5},
                }
            ],
        },
        {
            "symbol": "GOOG",
            "security_id": "XNAS:GOOG",
            "positions": [
                {
                    "position_id": "goog-put-rolled",
                    "type": "put",
                    "strike": 80,
                    "expiration": "2026-02-09",
                    "opened_at": "2026-01-10T00:00:00Z",
                    "closed_at": "2026-01-25T00:00:00Z",
                    "status": "rolled",
                    "source": {"premium": 1.2},
                }
            ],
        },
        {
            "symbol": "IBM",
            "security_id": "XNYS:IBM",
            "positions": [
                {
                    "position_id": "ibm-put-assigned",
                    "type": "put",
                    "strike": 90,
                    "expiration": "2026-02-04",
                    "opened_at": "2026-01-05T00:00:00Z",
                    "closed_at": "2026-01-30T00:00:00Z",
                    "status": "closed",
                    "close_reason": "assigned",
                    "source": {"premium": 2.0},
                }
            ],
        },
        {
            "symbol": "TSLA",
            "security_id": "XNAS:TSLA",
            "positions": [
                {
                    "position_id": "tsla-call-open-missing",
                    "type": "call",
                    "strike": 120,
                    "expiration": "2026-02-05",
                    "opened_at": "2026-01-06T00:00:00Z",
                    "closed_at": "2026-01-26T00:00:00Z",
                    "status": "closed",
                    "close_reason": "manual",
                    "source": {"premium": 0.8},
                }
            ],
        },
        {
            "symbol": "AMZN",
            "positions": [
                {
                    "position_id": "amzn-call-filtered-out",
                    "type": "call",
                    "strike": 110,
                    "expiration": "2026-03-03",
                    "opened_at": "2026-02-01T00:00:00Z",
                    "status": "active",
                    "source": {"premium": 1.5},
                }
            ],
        },
        {
            "symbol": "META",
            "security_id": "XNAS:META",
            "positions": [
                {
                    "position_id": "meta-put-loss",
                    "type": "put",
                    "strike": 95,
                    "expiration": "2026-03-04",
                    "opened_at": "2026-02-02T00:00:00Z",
                    "closed_at": "2026-02-20T00:00:00Z",
                    "status": "closed",
                    "close_reason": "manual",
                    "source": {"premium": 1.0},
                }
            ],
        },
        {
            "symbol": "NFLX",
            "security_id": "XNAS:NFLX",
            "positions": [
                {
                    "position_id": "nflx-put-unlinked",
                    "type": "put",
                    "strike": 130,
                    "expiration": "2026-03-06",
                    "opened_at": "2026-02-04T00:00:00Z",
                    "status": "active",
                    "source": {"premium": None},
                }
            ],
        },
        {
            "symbol": "NIO",
            "positions": [
                {
                    "position_id": "nio-call-unresolved",
                    "type": "call",
                    "strike": 70,
                    "expiration": "2026-03-05",
                    "opened_at": "2026-02-03T00:00:00Z",
                    "status": "active",
                    "source": {"premium": 0.7},
                }
            ],
        },
    ]


def _movement(
    movement_id: str,
    txn_type: str,
    security_id: str,
    position_id: str,
    trade_date: str,
    account_id: str,
    gross_amount: str,
    fee_eur: str,
    net_eur: str,
):
    return {
        "id": movement_id,
        "doc_type": "ledger_txn",
        "txn_type": txn_type,
        "security_id": security_id,
        "ticker": security_id.split(":")[-1],
        "trade_date": trade_date,
        "quantity": "0",
        "gross": {"amount": gross_amount, "currency": "USD", "eur_amount": gross_amount},
        "fees": {"total": fee_eur, "currency": "EUR", "total_eur": fee_eur},
        "net": {"amount": net_eur, "currency": "EUR", "eur_amount": net_eur},
        "account_id": account_id,
        "correction_status": "ACTIVE",
        "import_source": "manual",
        "option_position_id": position_id,
    }


def _sample_option_movements():
    return [
        _movement("m1", "CALL_SELL", "XNAS:AAPL", "aapl-call-win", "2026-01-01", ACCOUNT_1, "2.5", "0.10", "2.40"),
        _movement("m2", "CALL_BUY", "XNAS:AAPL", "aapl-call-win", "2026-01-20", ACCOUNT_1, "1.0", "0.05", "0.85"),
        _movement("m3", "PUT_SELL", "XNAS:GOOG", "goog-put-rolled", "2026-01-10", ACCOUNT_1, "1.2", "0.10", "1.10"),
        _movement("m4", "PUT_SELL", "XNYS:IBM", "ibm-put-assigned", "2026-01-05", ACCOUNT_1, "2.0", "0.10", "1.90"),
        _movement("m5", "CALL_BUY", "XNAS:TSLA", "tsla-call-open-missing", "2026-01-26", ACCOUNT_1, "0.8", "0.05", "0.85"),
        _movement("m6", "CALL_SELL", "XNAS:AMZN", "amzn-call-filtered-out", "2026-02-01", ACCOUNT_2, "1.5", "0.10", "1.40"),
        _movement("m7", "PUT_SELL", "XNAS:META", "meta-put-loss", "2026-02-02", ACCOUNT_1, "1.0", "0.10", "0.90"),
        _movement("m8", "PUT_BUY", "XNAS:META", "meta-put-loss", "2026-02-20", ACCOUNT_1, "1.4", "0.05", "1.45"),
    ]


def _sample_option_movements_with_paper():
    return _sample_option_movements() + [
        _movement("m9", "CALL_SELL", "XNAS:SHOP", "shop-call-paper", "2026-02-05", ACCOUNT_1, "1.3", "0.10", "1.20")
    ]


def _sample_securities():
    return [
        {"security_id": "XNAS:AAPL", "ticker": "AAPL", "status": "ACTIVE"},
        {"security_id": "XNAS:GOOG", "ticker": "GOOG", "status": "ACTIVE"},
        {"security_id": "XNYS:IBM", "ticker": "IBM", "status": "ACTIVE"},
        {"security_id": "XNAS:TSLA", "ticker": "TSLA", "status": "ACTIVE"},
        {"security_id": "XNAS:AMZN", "ticker": "AMZN", "status": "ACTIVE"},
        {"security_id": "XNAS:META", "ticker": "META", "status": "ACTIVE"},
        {"security_id": "XNAS:NFLX", "ticker": "NFLX", "status": "ACTIVE"},
        {"security_id": "XNAS:SHOP", "ticker": "SHOP", "status": "ACTIVE"},
    ]


def _sample_option_symbol_docs_with_paper():
    docs = _sample_option_symbol_docs()
    docs.append(
        {
            "symbol": "SHOP",
            "security_id": "XNAS:SHOP",
            "positions": [
                {
                    "position_id": "shop-call-paper",
                    "type": "call",
                    "strike": 140,
                    "expiration": "2026-03-20",
                    "opened_at": "2026-02-05T00:00:00Z",
                    "status": "active",
                    "source": {"premium": 1.3},
                    "is_paper": True,
                }
            ],
        }
    )
    return docs


def _sample_dividend_movements():
    return [
        {
            "id": "div-aapl",
            "doc_type": "ledger_txn",
            "txn_type": "DIVIDEND",
            "trade_date": "2026-01-15",
            "ticker": "AAPL",
            "security_id": "XNAS:AAPL",
            "account_id": ACCOUNT_1,
            "correction_status": "ACTIVE",
            "gross": {"amount": "10", "currency": "USD", "eur_amount": "10"},
            "fees": {"total_eur": "0"},
            "withholding": {"source": {"amount_eur": "1"}},
            "net": {"eur_amount": "9"},
            "source_derechos_amount": "2",
        },
        {
            "id": "div-amzn",
            "doc_type": "ledger_txn",
            "txn_type": "DIVIDEND",
            "trade_date": "2026-02-15",
            "ticker": "AMZN",
            "security_id": "XNAS:AMZN",
            "account_id": ACCOUNT_2,
            "correction_status": "ACTIVE",
            "gross": {"amount": "8", "currency": "USD", "eur_amount": "8"},
            "fees": {"total_eur": "0"},
            "withholding": {},
            "net": {"eur_amount": "8"},
        },
    ]


def _dividend_history(
    *,
    ticker: str,
    start_date: str,
    count: int,
    gap_days: int,
    total_net_eur: float,
    account_id: str = ACCOUNT_1,
):
    start = datetime.strptime(start_date, "%Y-%m-%d")
    movements = []
    for index in range(count):
        trade_date = (start + timedelta(days=gap_days * index)).strftime("%Y-%m-%d")
        movements.append(
            {
                "id": f"{ticker.lower()}-div-{index + 1}",
                "doc_type": "ledger_txn",
                "txn_type": "DIVIDEND",
                "trade_date": trade_date,
                "ticker": ticker,
                "security_id": f"XNAS:{ticker}",
                "account_id": account_id,
                "correction_status": "ACTIVE",
                "gross": {
                    "amount": str(total_net_eur),
                    "currency": "USD",
                    "eur_amount": str(total_net_eur),
                },
                "fees": {"total_eur": "0"},
                "withholding": {},
                "net": {"eur_amount": str(total_net_eur)},
            }
        )
    return movements


@pytest.fixture
def option_report():
    return _build_economics_report(
        _sample_option_symbol_docs(),
        now=datetime(2026, 3, 1, tzinfo=timezone.utc),
        movements=_sample_option_movements(),
        securities=_sample_securities(),
    )


def test_build_economics_report_uses_linked_ledger_movements_and_new_summary_fields(option_report):
    assert option_report["summary"] == {
        "total_premium_usd": 8.2,
        "total_buyback_usd": 3.2,
        "net_option_usd_gross": 5.0,
        "net_income_eur": 4.55,
        "total_commission_eur": 0.65,
        "avg_roc_pct": 0.01,
        "avg_roc_annualized": 0.07,
        "win_rate": 66.67,
        "total_positions": 8,
        "coverage": {
            "linked_positions": 6,
            "total_positions": 8,
            "linked_ratio": 0.75,
            "positions_with_unresolved_security": 1,
            "positions_missing_opening_sell": 1,
            "positions_missing_closing_buy": 1,
            "positions_missing_assignment_stock": 1,
            "excluded_unlinked_positions": 1,
            "excluded_positions_linked_only_outside_account_filter": 0,
            "excluded_paper_positions": 0,
        },
    }
    assert option_report["filters"] == {
        "years": [2026],
        "symbols": ["AAPL", "AMZN", "GOOG", "IBM", "META", "NFLX", "NIO", "TSLA"],
    }
    assert option_report["monthly"] == [
        {
            "month": 1,
            "year": 2026,
            "label": "Jan 2026",
            "total_premium_usd": 5.7,
            "total_buyback_usd": 1.8,
            "net_option_usd_gross": 3.9,
            "net_income_eur": 3.7,
            "total_commission_eur": 0.4,
            "calls_net_income_eur": 0.7,
            "puts_net_income_eur": 3.0,
            "positions_count": 4,
            "avg_roc_pct": 0.01,
            "avg_roc_annualized": 0.07,
            "calls_count": 2,
            "puts_count": 2,
        },
        {
            "month": 2,
            "year": 2026,
            "label": "Feb 2026",
            "total_premium_usd": 2.5,
            "total_buyback_usd": 1.4,
            "net_option_usd_gross": 1.1,
            "net_income_eur": 0.85,
            "total_commission_eur": 0.25,
            "calls_net_income_eur": 1.4,
            "puts_net_income_eur": -0.55,
            "positions_count": 4,
            "avg_roc_pct": 0.01,
            "avg_roc_annualized": 0.06,
            "calls_count": 2,
            "puts_count": 2,
        },
    ]
    assert option_report["by_type"] == {
        "calls": {
            "total_premium_usd": 4.0,
            "total_buyback_usd": 1.8,
            "net_option_usd_gross": 2.2,
            "net_income_eur": 2.1,
            "total_commission_eur": 0.3,
            "count": 4,
            "avg_roc_pct": 0.01,
            "avg_roc_annualized": 0.03,
        },
        "puts": {
            "total_premium_usd": 4.2,
            "total_buyback_usd": 1.4,
            "net_option_usd_gross": 2.8,
            "net_income_eur": 2.45,
            "total_commission_eur": 0.35,
            "count": 4,
            "avg_roc_pct": 0.01,
            "avg_roc_annualized": 0.12,
        },
    }
    assert [position["position_id"] for position in option_report["positions"]] == [
        "nflx-put-unlinked",
        "nio-call-unresolved",
        "meta-put-loss",
        "amzn-call-filtered-out",
        "goog-put-rolled",
        "tsla-call-open-missing",
        "ibm-put-assigned",
        "aapl-call-win",
    ]


def test_build_economics_report_emits_row_warnings_and_linkage_fields(option_report):
    positions = {position["position_id"]: position for position in option_report["positions"]}

    assert positions["aapl-call-win"]["premium_usd"] == 2.5
    assert positions["aapl-call-win"]["buyback_usd"] == 1.0
    assert positions["aapl-call-win"]["net_income_eur"] == 1.55
    assert positions["aapl-call-win"]["linked_accounts"] == [ACCOUNT_1]
    assert positions["aapl-call-win"]["linked_movement_count"] == 2
    assert positions["aapl-call-win"]["coverage_status"] == "linked"
    assert positions["aapl-call-win"]["warnings"] == []

    assert positions["goog-put-rolled"]["warnings"] == ["OPTION_MANUAL_CLOSE_BUY_MISSING"]
    assert positions["ibm-put-assigned"]["warnings"] == ["OPTION_ASSIGNMENT_STOCK_MISSING"]
    assert positions["tsla-call-open-missing"]["warnings"] == ["OPTION_OPENING_SELL_MISSING"]
    assert positions["nio-call-unresolved"]["coverage_status"] == "unresolved_security"
    assert positions["nio-call-unresolved"]["warnings"] == ["OPTION_SECURITY_UNRESOLVED"]
    assert positions["nflx-put-unlinked"]["coverage_status"] == "unlinked"
    assert positions["nflx-put-unlinked"]["warnings"] == []


def test_build_economics_report_applies_filters_and_account_scope():
    report = _build_economics_report(
        _sample_option_symbol_docs(),
        year=2026,
        month_filter=[1],
        symbol_filter="AAPL,TSLA",
        option_type="call",
        status_filter="closed",
        account_filter=[ACCOUNT_1],
        now=datetime(2026, 3, 1, tzinfo=timezone.utc),
        movements=_sample_option_movements(),
        securities=_sample_securities(),
    )

    assert report["summary"] == {
        "total_premium_usd": 2.5,
        "total_buyback_usd": 1.8,
        "net_option_usd_gross": 0.7,
        "net_income_eur": 0.7,
        "total_commission_eur": 0.2,
        "avg_roc_pct": 0.0,
        "avg_roc_annualized": -0.01,
        "win_rate": 100.0,
        "total_positions": 2,
        "coverage": {
            "linked_positions": 2,
            "total_positions": 2,
            "linked_ratio": 1.0,
            "positions_with_unresolved_security": 0,
            "positions_missing_opening_sell": 1,
            "positions_missing_closing_buy": 0,
            "positions_missing_assignment_stock": 0,
            "excluded_unlinked_positions": 0,
            "excluded_positions_linked_only_outside_account_filter": 0,
            "excluded_paper_positions": 0,
        },
    }
    assert [row["position_id"] for row in report["positions"]] == [
        "tsla-call-open-missing",
        "aapl-call-win",
    ]


def test_build_economics_report_account_filter_tracks_both_exclusion_counters():
    report = _build_economics_report(
        _sample_option_symbol_docs(),
        account_filter=[ACCOUNT_1],
        now=datetime(2026, 3, 1, tzinfo=timezone.utc),
        movements=_sample_option_movements(),
        securities=_sample_securities(),
    )

    assert report["summary"] == {
        "total_premium_usd": 6.7,
        "total_buyback_usd": 3.2,
        "net_option_usd_gross": 3.5,
        "net_income_eur": 3.15,
        "total_commission_eur": 0.55,
        "avg_roc_pct": 0.01,
        "avg_roc_annualized": 0.06,
        "win_rate": 66.67,
        "total_positions": 8,
        "coverage": {
            "linked_positions": 5,
            "total_positions": 8,
            "linked_ratio": 0.625,
            "positions_with_unresolved_security": 1,
            "positions_missing_opening_sell": 1,
            "positions_missing_closing_buy": 1,
            "positions_missing_assignment_stock": 1,
            "excluded_unlinked_positions": 1,
            "excluded_positions_linked_only_outside_account_filter": 1,
            "excluded_paper_positions": 0,
        },
    }
    positions = {position["position_id"]: position for position in report["positions"]}
    assert positions["amzn-call-filtered-out"]["coverage_status"] == "account_filtered_out"
    assert positions["amzn-call-filtered-out"]["linked_accounts"] == [ACCOUNT_2]
    assert positions["amzn-call-filtered-out"]["premium_usd"] == 0.0
    assert positions["amzn-call-filtered-out"]["buyback_usd"] == 0.0


def test_build_economics_report_excludes_paper_positions_from_real_aggregates():
    report = _build_economics_report(
        _sample_option_symbol_docs_with_paper(),
        now=datetime(2026, 3, 1, tzinfo=timezone.utc),
        movements=_sample_option_movements_with_paper(),
        securities=_sample_securities(),
    )

    positions = {position["position_id"]: position for position in report["positions"]}
    assert positions["shop-call-paper"]["is_paper"] is True
    assert positions["shop-call-paper"]["coverage_status"] == "paper"
    assert positions["shop-call-paper"]["warnings"] == []
    assert report["summary"]["total_positions"] == 8
    assert report["summary"]["coverage"]["excluded_paper_positions"] == 1
    assert report["summary"]["total_premium_usd"] == 8.2
    assert all(row["symbol"] != "SHOP" for row in report["by_symbol"])
    assert all(row["positions_count"] == 4 for row in report["monthly"])
    assert report["by_type"]["calls"]["count"] == 4
    assert report["by_type"]["puts"]["count"] == 4


class _FakeEconomicsCosmos(FakeCosmos):
    def __init__(self, symbol_docs):
        super().__init__()
        self._symbol_docs = symbol_docs

    def get_all_symbols(self):
        return self._symbol_docs

    def list_symbols(self):
        return self._symbol_docs



def _seed_security(fake: FakeCosmos, security_id: str, ticker: str):
    fake.container.create_item(
        {
            "id": f"security_master::{security_id}",
            "doc_type": "security_master",
            "security_id": security_id,
            "ticker": ticker,
            "company_name": ticker,
            "exchange_mic": security_id.split(":")[0],
            "listing_currency": "USD",
            "status": "ACTIVE",
        }
    )


@pytest.fixture
def economics_client():
    original_cosmos = getattr(app.state, "cosmos", None)
    original_error = getattr(app.state, "cosmos_error", None)
    fake = _FakeEconomicsCosmos(_sample_option_symbol_docs())
    for security in _sample_securities():
        _seed_security(fake, security["security_id"], security["ticker"])
    for movement in _sample_option_movements() + _sample_dividend_movements():
        fake.portfolio_container._store[movement["id"]] = dict(movement)

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            app.state.cosmos = fake
            app.state.cosmos_error = None
            yield client
    finally:
        app.state.cosmos = original_cosmos
        app.state.cosmos_error = original_error



def test_api_dividends_economics_smoke(economics_client):
    response = economics_client.get("/api/economics/dividends")

    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {
        "summary",
        "monthly",
        "by_symbol",
        "yearly",
        "cumulative",
        "positions",
        "filters",
    }
    assert "portfolio_yoc_pct" in body["summary"]
    assert {"yoc_pct", "yoc_basis", "yoc_dividend_frequency"} <= set(body["by_symbol"][0])



def test_api_dividends_economics_exposes_cash_derechos_and_total_fields(economics_client):
    response = economics_client.get("/api/economics/dividends")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_net_eur"] == 17.0
    assert body["summary"]["cash_net"] == 17.0
    assert body["summary"]["derechos_net"] == 2.0
    assert body["summary"]["total_net"] == 19.0
    assert body["monthly"] == [
        {
            "month": "2026-01",
            "gross_eur": 10.0,
            "fees_eur": 0.0,
            "withholding_source_eur": 1.0,
            "withholding_destination_eur": 0.0,
            "withholding_total_eur": 1.0,
            "net_eur": 9.0,
            "cash_net": 9.0,
            "derechos_net": 2.0,
            "total_net": 11.0,
            "dividend_count": 1,
        },
        {
            "month": "2026-02",
            "gross_eur": 8.0,
            "fees_eur": 0.0,
            "withholding_source_eur": 0.0,
            "withholding_destination_eur": 0.0,
            "withholding_total_eur": 0.0,
            "net_eur": 8.0,
            "cash_net": 8.0,
            "derechos_net": 0.0,
            "total_net": 8.0,
            "dividend_count": 1,
        },
    ]



def test_api_economics_accepts_account_id_and_returns_ledger_backed_contract(economics_client):
    response = economics_client.get("/api/economics?account_id=acct-1")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {
        "total_premium_usd": 6.7,
        "total_buyback_usd": 3.2,
        "net_option_usd_gross": 3.5,
        "net_income_eur": 3.15,
        "total_commission_eur": 0.55,
        "avg_roc_pct": 0.01,
        "avg_roc_annualized": 0.06,
        "win_rate": 66.67,
        "total_positions": 8,
        "coverage": {
            "linked_positions": 5,
            "total_positions": 8,
            "linked_ratio": 0.625,
            "positions_with_unresolved_security": 1,
            "positions_missing_opening_sell": 1,
            "positions_missing_closing_buy": 1,
            "positions_missing_assignment_stock": 1,
            "excluded_unlinked_positions": 1,
            "excluded_positions_linked_only_outside_account_filter": 1,
            "excluded_paper_positions": 0,
        },
    }
    assert body["applied_filters"]["account_ids"] == [ACCOUNT_1]



def test_api_economics_overview_uses_eur_options_fields_and_coverage(economics_client):
    response = economics_client.get("/api/economics/overview?account_id=acct-1")

    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {
        "summary",
        "monthly",
        "by_symbol",
        "filters",
        "applied_filters",
        "meta",
    }
    assert body["summary"] == {
        "options_net_eur": 3.15,
        "dividends_net_eur": 9.0,
        "dividends_cash_net_eur": 9.0,
        "dividends_derechos_net_eur": 2.0,
        "dividends_total_net_eur": 11.0,
        "portfolio_yoc_pct": None,
        "combined_net_eur": 14.15,
        "total_option_positions": 8,
        "options_coverage": {
            "linked_positions": 5,
            "total_positions": 8,
            "linked_ratio": 0.625,
            "positions_with_unresolved_security": 1,
            "positions_missing_opening_sell": 1,
            "positions_missing_closing_buy": 1,
            "positions_missing_assignment_stock": 1,
            "excluded_unlinked_positions": 1,
            "excluded_positions_linked_only_outside_account_filter": 1,
            "excluded_paper_positions": 0,
        },
        "total_dividend_events": 1,
        "total_symbols": 8,
    }
    january = next(row for row in body["monthly"] if row["month"] == "2026-01")
    assert january == {
        "month": "2026-01",
        "options_net_eur": 3.7,
        "dividends_net_eur": 9.0,
        "dividends_cash_net_eur": 9.0,
        "dividends_derechos_net_eur": 2.0,
        "dividends_total_net_eur": 11.0,
        "combined_net_eur": 14.7,
        "option_positions": 4,
        "dividend_events": 1,
    }
    assert body["meta"] == {
        "options_bucket_field": "opened_at",
        "dividends_bucket_field": "trade_date",
        "options_currency": "EUR",
        "dividends_currency": "EUR",
        "combined_total_available": True,
    }


def test_api_economics_overview_reports_excluded_paper_positions():
    original_cosmos = getattr(app.state, "cosmos", None)
    original_error = getattr(app.state, "cosmos_error", None)
    fake = _FakeEconomicsCosmos(_sample_option_symbol_docs_with_paper())
    for security in _sample_securities():
        _seed_security(fake, security["security_id"], security["ticker"])
    for movement in _sample_option_movements_with_paper() + _sample_dividend_movements():
        fake.portfolio_container._store[movement["id"]] = dict(movement)

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            app.state.cosmos = fake
            app.state.cosmos_error = None
            response = client.get("/api/economics/overview?account_id=acct-1")
    finally:
        app.state.cosmos = original_cosmos
        app.state.cosmos_error = original_error

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_option_positions"] == 8
    assert body["summary"]["options_coverage"]["excluded_paper_positions"] == 1


def test_apply_dividends_yoc_uses_weighted_portfolio_cost_basis_and_guards_edge_cases():
    movements = (
        _dividend_history(
            ticker="AAPL",
            start_date="2025-01-15",
            count=12,
            gap_days=30,
            total_net_eur=10,
        )
        + _dividend_history(
            ticker="MSFT",
            start_date="2025-02-01",
            count=4,
            gap_days=91,
            total_net_eur=15,
        )
        + _dividend_history(
            ticker="SOLD",
            start_date="2025-03-01",
            count=4,
            gap_days=91,
            total_net_eur=5,
        )
        + _dividend_history(
            ticker="ZERO",
            start_date="2025-04-01",
            count=4,
            gap_days=91,
            total_net_eur=8,
        )
        + _dividend_history(
            ticker="NEW",
            start_date="2026-01-10",
            count=1,
            gap_days=30,
            total_net_eur=12,
        )
    )

    report = _apply_dividends_yoc(
        build_dividends_economics_report(movements),
        movements,
        {
            "AAPL": {"shares": "10", "remaining_cost_basis_eur": "600"},
            "MSFT": {"shares": "20", "remaining_cost_basis_eur": "1200"},
            "SOLD": {"shares": "0", "remaining_cost_basis_eur": "0"},
            "ZERO": {"shares": "5", "remaining_cost_basis_eur": "0"},
            "NEW": {"shares": "7", "remaining_cost_basis_eur": "350"},
        },
    )

    by_symbol = {row["symbol"]: row for row in report["by_symbol"]}

    assert by_symbol["AAPL"]["yoc_dividend_frequency"] == 12
    assert by_symbol["AAPL"]["yoc_trailing_annual_dividend_net_eur"] == 120.0
    assert by_symbol["AAPL"]["yoc_cost_basis_eur"] == 600.0
    assert by_symbol["AAPL"]["yoc_basis"] == "annualized"
    assert by_symbol["AAPL"]["yoc_pct"] == 20.0

    assert by_symbol["MSFT"]["yoc_dividend_frequency"] == 4
    assert by_symbol["MSFT"]["yoc_basis"] == "annualized"
    assert by_symbol["MSFT"]["yoc_pct"] == 5.0

    assert by_symbol["SOLD"]["yoc_dividend_frequency"] == 4
    assert by_symbol["SOLD"]["yoc_trailing_annual_dividend_net_eur"] == 20.0
    assert by_symbol["SOLD"]["yoc_pct"] is None
    assert by_symbol["SOLD"]["yoc_basis"] is None

    assert by_symbol["ZERO"]["yoc_dividend_frequency"] == 4
    assert by_symbol["ZERO"]["yoc_cost_basis_eur"] == 0.0
    assert by_symbol["ZERO"]["yoc_pct"] is None
    assert by_symbol["ZERO"]["yoc_basis"] is None

    assert by_symbol["NEW"]["yoc_dividend_frequency"] is None
    assert by_symbol["NEW"]["yoc_pct"] is None
    assert by_symbol["NEW"]["yoc_basis"] == "insufficient_history"

    assert report["summary"]["portfolio_yoc_pct"] == 10.0


def test_apply_dividends_yoc_ignores_year_month_filters_but_respects_visible_rows():
    movements = _dividend_history(
        ticker="AAPL",
        start_date="2025-01-15",
        count=12,
        gap_days=30,
        total_net_eur=10,
    )

    report = _apply_dividends_yoc(
        build_dividends_economics_report(
            movements,
            year=2025,
            month_filter=[12],
        ),
        movements,
        {"AAPL": {"shares": "10", "remaining_cost_basis_eur": "600"}},
    )

    assert report["summary"]["total_dividends"] == 1
    assert report["by_symbol"] == [
        {
            "symbol": "AAPL",
            "gross_eur": 10.0,
            "withholding_total_eur": 0.0,
            "net_eur": 10.0,
            "cash_net": 10.0,
            "derechos_net": 0.0,
            "total_net": 10.0,
            "dividend_count": 1,
            "yoc_pct": 20.0,
            "yoc_basis": "annualized",
            "yoc_dividend_frequency": 12,
            "yoc_trailing_annual_dividend_net_eur": 120.0,
            "yoc_cost_basis_eur": 600.0,
        }
    ]
    assert report["summary"]["portfolio_yoc_pct"] == 20.0
