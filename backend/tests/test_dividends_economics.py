from src.dividends_economics import build_dividends_economics_report


def _dividend_movement(
    *,
    movement_id: str,
    trade_date: str,
    ticker: str,
    account_id: str,
    gross_eur: float,
    fees_eur: float,
    source_withholding_eur: float = 0.0,
    destination_withholding_eur: float = 0.0,
    net_eur: float,
    source_derechos_eur: float = 0.0,
    correction_status=None,
    txn_type: str = "DIVIDEND",
):
    withholding = {}
    if source_withholding_eur:
        withholding["source"] = {"amount_eur": str(source_withholding_eur)}
    if destination_withholding_eur:
        withholding["destination"] = {"amount_eur": str(destination_withholding_eur)}

    movement = {
        "id": movement_id,
        "txn_type": txn_type,
        "trade_date": trade_date,
        "ticker": ticker,
        "security_id": f"XNAS:{ticker}",
        "account_id": account_id,
        "correction_status": correction_status,
        "gross": {
            "amount": str(gross_eur),
            "currency": "USD",
            "eur_amount": str(gross_eur),
        },
        "fees": {"total_eur": str(fees_eur)},
        "withholding": withholding,
        "net": {"eur_amount": str(net_eur)},
    }
    if source_derechos_eur:
        movement["source_derechos_amount"] = str(source_derechos_eur)
    return movement


def _sample_dividend_movements():
    return [
        _dividend_movement(
            movement_id="div-2023-12-aapl",
            trade_date="2023-12-15",
            ticker="AAPL",
            account_id="acct-1",
            gross_eur=100,
            fees_eur=1,
            source_withholding_eur=10,
            net_eur=89,
            correction_status="ACTIVE",
        ),
        _dividend_movement(
            movement_id="div-2024-01-aapl",
            trade_date="2024-01-20",
            ticker="AAPL",
            account_id="acct-1",
            gross_eur=50,
            fees_eur=0,
            destination_withholding_eur=5,
            net_eur=45,
            correction_status="",
        ),
        _dividend_movement(
            movement_id="div-2024-02-aapl",
            trade_date="2024-02-10",
            ticker="AAPL",
            account_id="acct-2",
            gross_eur=200,
            fees_eur=2,
            source_withholding_eur=10,
            destination_withholding_eur=10,
            net_eur=178,
            correction_status=None,
        ),
        _dividend_movement(
            movement_id="div-2024-02-msft",
            trade_date="2024-02-18",
            ticker="MSFT",
            account_id="acct-1",
            gross_eur=80,
            fees_eur=0,
            net_eur=80,
            correction_status="ACTIVE",
        ),
        _dividend_movement(
            movement_id="buy-ignored",
            trade_date="2024-02-19",
            ticker="AAPL",
            account_id="acct-1",
            gross_eur=999,
            fees_eur=0,
            net_eur=999,
            txn_type="BUY",
        ),
        _dividend_movement(
            movement_id="superseded-ignored",
            trade_date="2024-02-21",
            ticker="AAPL",
            account_id="acct-1",
            gross_eur=70,
            fees_eur=0,
            net_eur=70,
            correction_status="SUPERSEDED",
        ),
        _dividend_movement(
            movement_id="voided-ignored",
            trade_date="2024-02-22",
            ticker="MSFT",
            account_id="acct-2",
            gross_eur=60,
            fees_eur=0,
            net_eur=60,
            correction_status="VOIDED",
        ),
    ]


def test_build_dividends_economics_report_aggregates_active_dividends_only():
    report = build_dividends_economics_report(_sample_dividend_movements())

    assert report["summary"] == {
        "total_gross_eur": 430.0,
        "total_fees_eur": 3.0,
        "total_withholding_eur": 35.0,
        "total_net_eur": 392.0,
        "cash_net": 392.0,
        "derechos_net": 0.0,
        "total_net": 392.0,
        "effective_withholding_pct": 8.14,
        "total_dividends": 4,
        "total_accounts": 2,
    }
    assert report["monthly"] == [
        {
            "month": "2023-12",
            "gross_eur": 100.0,
            "fees_eur": 1.0,
            "withholding_source_eur": 10.0,
            "withholding_destination_eur": 0.0,
            "withholding_total_eur": 10.0,
            "net_eur": 89.0,
            "cash_net": 89.0,
            "derechos_net": 0.0,
            "total_net": 89.0,
            "dividend_count": 1,
        },
        {
            "month": "2024-01",
            "gross_eur": 50.0,
            "fees_eur": 0.0,
            "withholding_source_eur": 0.0,
            "withholding_destination_eur": 5.0,
            "withholding_total_eur": 5.0,
            "net_eur": 45.0,
            "cash_net": 45.0,
            "derechos_net": 0.0,
            "total_net": 45.0,
            "dividend_count": 1,
        },
        {
            "month": "2024-02",
            "gross_eur": 280.0,
            "fees_eur": 2.0,
            "withholding_source_eur": 10.0,
            "withholding_destination_eur": 10.0,
            "withholding_total_eur": 20.0,
            "net_eur": 258.0,
            "cash_net": 258.0,
            "derechos_net": 0.0,
            "total_net": 258.0,
            "dividend_count": 2,
        },
    ]
    assert report["by_symbol"] == [
        {
            "symbol": "AAPL",
            "gross_eur": 350.0,
            "withholding_total_eur": 35.0,
            "net_eur": 312.0,
            "cash_net": 312.0,
            "derechos_net": 0.0,
            "total_net": 312.0,
            "dividend_count": 3,
        },
        {
            "symbol": "MSFT",
            "gross_eur": 80.0,
            "withholding_total_eur": 0.0,
            "net_eur": 80.0,
            "cash_net": 80.0,
            "derechos_net": 0.0,
            "total_net": 80.0,
            "dividend_count": 1,
        },
    ]
    assert [position["id"] for position in report["positions"]] == [
        "div-2024-02-msft",
        "div-2024-02-aapl",
        "div-2024-01-aapl",
        "div-2023-12-aapl",
    ]
    assert report["filters"] == {
        "years": [2024, 2023],
        "symbols": ["AAPL", "MSFT"],
        "account_ids": ["acct-1", "acct-2"],
    }


def test_yearly_and_cumulative_ignore_year_month_filters_but_respect_symbol_and_account():
    movements = _sample_dividend_movements()

    aapl_report = build_dividends_economics_report(
        movements,
        year=2024,
        month_filter=[2],
        symbol_filter=["AAPL"],
    )

    assert aapl_report["summary"]["total_dividends"] == 1
    assert aapl_report["summary"]["total_net_eur"] == 178.0
    assert aapl_report["monthly"] == [
        {
            "month": "2024-02",
            "gross_eur": 200.0,
            "fees_eur": 2.0,
            "withholding_source_eur": 10.0,
            "withholding_destination_eur": 10.0,
            "withholding_total_eur": 20.0,
            "net_eur": 178.0,
            "cash_net": 178.0,
            "derechos_net": 0.0,
            "total_net": 178.0,
            "dividend_count": 1,
        }
    ]
    assert aapl_report["yearly"] == [
        {
            "year": 2023,
            "gross_eur": 100.0,
            "withholding_eur": 10.0,
            "net_eur": 89.0,
            "cash_net": 89.0,
            "derechos_net": 0.0,
            "total_net": 89.0,
            "dividend_count": 1,
        },
        {
            "year": 2024,
            "gross_eur": 250.0,
            "withholding_eur": 25.0,
            "net_eur": 223.0,
            "cash_net": 223.0,
            "derechos_net": 0.0,
            "total_net": 223.0,
            "dividend_count": 2,
        },
    ]
    assert aapl_report["cumulative"] == [
        {
            "month": "2023-12",
            "cumulative_net_eur": 89.0,
            "cumulative_cash_net_eur": 89.0,
            "cumulative_derechos_net_eur": 0.0,
            "cumulative_total_net_eur": 89.0,
            "cash_net": 89.0,
            "derechos_net": 0.0,
            "total_net": 89.0,
        },
        {
            "month": "2024-01",
            "cumulative_net_eur": 134.0,
            "cumulative_cash_net_eur": 134.0,
            "cumulative_derechos_net_eur": 0.0,
            "cumulative_total_net_eur": 134.0,
            "cash_net": 134.0,
            "derechos_net": 0.0,
            "total_net": 134.0,
        },
        {
            "month": "2024-02",
            "cumulative_net_eur": 312.0,
            "cumulative_cash_net_eur": 312.0,
            "cumulative_derechos_net_eur": 0.0,
            "cumulative_total_net_eur": 312.0,
            "cash_net": 312.0,
            "derechos_net": 0.0,
            "total_net": 312.0,
        },
    ]

    acct2_report = build_dividends_economics_report(
        movements,
        year=2024,
        month_filter=[2],
        account_filter=["acct-2"],
    )

    assert acct2_report["summary"]["total_dividends"] == 1
    assert acct2_report["summary"]["total_net_eur"] == 178.0
    assert acct2_report["yearly"] == [
        {
            "year": 2024,
            "gross_eur": 200.0,
            "withholding_eur": 20.0,
            "net_eur": 178.0,
            "cash_net": 178.0,
            "derechos_net": 0.0,
            "total_net": 178.0,
            "dividend_count": 1,
        }
    ]
    assert acct2_report["cumulative"] == [
        {
            "month": "2024-02",
            "cumulative_net_eur": 178.0,
            "cumulative_cash_net_eur": 178.0,
            "cumulative_derechos_net_eur": 0.0,
            "cumulative_total_net_eur": 178.0,
            "cash_net": 178.0,
            "derechos_net": 0.0,
            "total_net": 178.0,
        }
    ]


def test_withholding_taxonomy_fields_and_effective_withholding_pct_are_computed_per_shape():
    report = build_dividends_economics_report(
        [
            _dividend_movement(
                movement_id="source-only",
                trade_date="2024-01-05",
                ticker="AAPL",
                account_id="acct-1",
                gross_eur=100,
                fees_eur=0,
                source_withholding_eur=15,
                net_eur=85,
            ),
            _dividend_movement(
                movement_id="destination-only",
                trade_date="2024-01-06",
                ticker="AAPL",
                account_id="acct-1",
                gross_eur=100,
                fees_eur=0,
                destination_withholding_eur=5,
                net_eur=95,
            ),
            _dividend_movement(
                movement_id="both-withholding",
                trade_date="2024-01-07",
                ticker="AAPL",
                account_id="acct-1",
                gross_eur=100,
                fees_eur=0,
                source_withholding_eur=10,
                destination_withholding_eur=10,
                net_eur=80,
            ),
            _dividend_movement(
                movement_id="no-withholding",
                trade_date="2024-01-08",
                ticker="AAPL",
                account_id="acct-1",
                gross_eur=100,
                fees_eur=0,
                net_eur=100,
            ),
        ]
    )

    positions_by_id = {position["id"]: position for position in report["positions"]}

    assert positions_by_id["source-only"]["withholding_source_eur"] == 15.0
    assert positions_by_id["source-only"]["withholding_destination_eur"] == 0.0
    assert positions_by_id["source-only"]["withholding_total_eur"] == 15.0
    assert positions_by_id["destination-only"]["withholding_source_eur"] == 0.0
    assert positions_by_id["destination-only"]["withholding_destination_eur"] == 5.0
    assert positions_by_id["destination-only"]["withholding_total_eur"] == 5.0
    assert positions_by_id["both-withholding"]["withholding_source_eur"] == 10.0
    assert positions_by_id["both-withholding"]["withholding_destination_eur"] == 10.0
    assert positions_by_id["both-withholding"]["withholding_total_eur"] == 20.0
    assert positions_by_id["no-withholding"]["withholding_source_eur"] == 0.0
    assert positions_by_id["no-withholding"]["withholding_destination_eur"] == 0.0
    assert positions_by_id["no-withholding"]["withholding_total_eur"] == 0.0
    assert report["summary"]["effective_withholding_pct"] == 10.0


def test_derechos_amounts_flow_through_summary_monthly_yearly_cumulative_and_positions():
    report = build_dividends_economics_report(
        [
            _dividend_movement(
                movement_id="with-derechos-2023",
                trade_date="2023-12-05",
                ticker="IBE",
                account_id="acct-1",
                gross_eur=100,
                fees_eur=0,
                net_eur=80,
                source_derechos_eur=20,
            ),
            _dividend_movement(
                movement_id="cash-only-2024",
                trade_date="2024-01-06",
                ticker="IBE",
                account_id="acct-1",
                gross_eur=40,
                fees_eur=0,
                net_eur=40,
            ),
            _dividend_movement(
                movement_id="with-derechos-2024",
                trade_date="2024-02-07",
                ticker="SAN",
                account_id="acct-2",
                gross_eur=20,
                fees_eur=0,
                net_eur=10,
                source_derechos_eur=5,
            ),
        ]
    )

    assert report["summary"] == {
        "total_gross_eur": 160.0,
        "total_fees_eur": 0.0,
        "total_withholding_eur": 0.0,
        "total_net_eur": 130.0,
        "cash_net": 130.0,
        "derechos_net": 25.0,
        "total_net": 155.0,
        "effective_withholding_pct": 0.0,
        "total_dividends": 3,
        "total_accounts": 2,
    }
    assert report["monthly"] == [
        {
            "month": "2023-12",
            "gross_eur": 100.0,
            "fees_eur": 0.0,
            "withholding_source_eur": 0.0,
            "withholding_destination_eur": 0.0,
            "withholding_total_eur": 0.0,
            "net_eur": 80.0,
            "cash_net": 80.0,
            "derechos_net": 20.0,
            "total_net": 100.0,
            "dividend_count": 1,
        },
        {
            "month": "2024-01",
            "gross_eur": 40.0,
            "fees_eur": 0.0,
            "withholding_source_eur": 0.0,
            "withholding_destination_eur": 0.0,
            "withholding_total_eur": 0.0,
            "net_eur": 40.0,
            "cash_net": 40.0,
            "derechos_net": 0.0,
            "total_net": 40.0,
            "dividend_count": 1,
        },
        {
            "month": "2024-02",
            "gross_eur": 20.0,
            "fees_eur": 0.0,
            "withholding_source_eur": 0.0,
            "withholding_destination_eur": 0.0,
            "withholding_total_eur": 0.0,
            "net_eur": 10.0,
            "cash_net": 10.0,
            "derechos_net": 5.0,
            "total_net": 15.0,
            "dividend_count": 1,
        },
    ]
    assert report["yearly"] == [
        {
            "year": 2023,
            "gross_eur": 100.0,
            "withholding_eur": 0.0,
            "net_eur": 80.0,
            "cash_net": 80.0,
            "derechos_net": 20.0,
            "total_net": 100.0,
            "dividend_count": 1,
        },
        {
            "year": 2024,
            "gross_eur": 60.0,
            "withholding_eur": 0.0,
            "net_eur": 50.0,
            "cash_net": 50.0,
            "derechos_net": 5.0,
            "total_net": 55.0,
            "dividend_count": 2,
        },
    ]
    assert report["by_symbol"] == [
        {
            "symbol": "IBE",
            "gross_eur": 140.0,
            "withholding_total_eur": 0.0,
            "net_eur": 120.0,
            "cash_net": 120.0,
            "derechos_net": 20.0,
            "total_net": 140.0,
            "dividend_count": 2,
        },
        {
            "symbol": "SAN",
            "gross_eur": 20.0,
            "withholding_total_eur": 0.0,
            "net_eur": 10.0,
            "cash_net": 10.0,
            "derechos_net": 5.0,
            "total_net": 15.0,
            "dividend_count": 1,
        },
    ]
    assert report["cumulative"] == [
        {
            "month": "2023-12",
            "cumulative_net_eur": 80.0,
            "cumulative_cash_net_eur": 80.0,
            "cumulative_derechos_net_eur": 20.0,
            "cumulative_total_net_eur": 100.0,
            "cash_net": 80.0,
            "derechos_net": 20.0,
            "total_net": 100.0,
        },
        {
            "month": "2024-01",
            "cumulative_net_eur": 120.0,
            "cumulative_cash_net_eur": 120.0,
            "cumulative_derechos_net_eur": 20.0,
            "cumulative_total_net_eur": 140.0,
            "cash_net": 120.0,
            "derechos_net": 20.0,
            "total_net": 140.0,
        },
        {
            "month": "2024-02",
            "cumulative_net_eur": 130.0,
            "cumulative_cash_net_eur": 130.0,
            "cumulative_derechos_net_eur": 25.0,
            "cumulative_total_net_eur": 155.0,
            "cash_net": 130.0,
            "derechos_net": 25.0,
            "total_net": 155.0,
        },
    ]

    positions_by_id = {position["id"]: position for position in report["positions"]}
    assert positions_by_id["with-derechos-2023"]["net_eur"] == 80.0
    assert positions_by_id["with-derechos-2023"]["cash_net"] == 80.0
    assert positions_by_id["with-derechos-2023"]["derechos_eur"] == 20.0
    assert positions_by_id["with-derechos-2023"]["derechos_net"] == 20.0
    assert positions_by_id["with-derechos-2023"]["total_net"] == 100.0
    assert positions_by_id["cash-only-2024"]["derechos_eur"] == 0.0
    assert positions_by_id["cash-only-2024"]["total_net"] == 40.0


def test_zero_gross_dividend_keeps_effective_withholding_pct_at_zero():
    report = build_dividends_economics_report(
        [
            _dividend_movement(
                movement_id="zero-gross",
                trade_date="2024-03-01",
                ticker="AAPL",
                account_id="acct-1",
                gross_eur=0,
                fees_eur=0,
                source_withholding_eur=3,
                net_eur=-3,
            )
        ]
    )

    assert report["summary"] == {
        "total_gross_eur": 0.0,
        "total_fees_eur": 0.0,
        "total_withholding_eur": 3.0,
        "total_net_eur": -3.0,
        "cash_net": -3.0,
        "derechos_net": 0.0,
        "total_net": -3.0,
        "effective_withholding_pct": 0.0,
        "total_dividends": 1,
        "total_accounts": 1,
    }


def test_empty_movements_returns_empty_valid_report_shape():
    report = build_dividends_economics_report([])

    assert report["summary"] == {
        "total_gross_eur": 0.0,
        "total_fees_eur": 0.0,
        "total_withholding_eur": 0.0,
        "total_net_eur": 0.0,
        "cash_net": 0.0,
        "derechos_net": 0.0,
        "total_net": 0.0,
        "effective_withholding_pct": 0.0,
        "total_dividends": 0,
        "total_accounts": 0,
    }
    assert report["monthly"] == []
    assert report["by_symbol"] == []
    assert report["yearly"] == []
    assert report["cumulative"] == []
    assert report["positions"] == []
    assert report["filters"] == {
        "years": [],
        "symbols": [],
        "account_ids": [],
    }
    assert report["applied_filters"] == {
        "year": None,
        "months": None,
        "symbols": None,
        "account_ids": None,
    }
    assert report["meta"] == {
        "bucket_field": "trade_date",
        "value_field": "net.eur_amount",
        "yearly_cumulative_scope": "all_years_symbol_account_filtered",
    }
