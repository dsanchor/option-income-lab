"""Acceptance coverage for Dividend · Buy economic value in Economics."""

from copy import deepcopy

import pytest

from src.dividends_economics import build_dividends_economics_report
from web.app import _build_economics_overview_report


def _cash(
    movement_id,
    *,
    date="2026-01-15",
    amount=20,
    currency="EUR",
    account="acct-1",
    security="XNAS:ACME",
    group=None,
    leg="CASH_DIVIDEND",
    status="ACTIVE",
):
    row = {
        "id": movement_id,
        "txn_type": "DIVIDEND",
        "trade_date": date,
        "ticker": security.split(":")[-1],
        "security_id": security,
        "account_id": account,
        "correction_status": status,
        "gross": {
            "amount": str(amount),
            "currency": currency,
            "eur_amount": str(amount),
        },
        "fees": {"total_eur": "0"},
        "withholding": {},
        "net": {"eur_amount": str(amount)},
    }
    if group is not None:
        row.update(
            ca_group_id=group,
            ca_leg_type=leg,
            ca_event_type="DIVIDEND_WITH_SCRIP",
        )
    return row


def _share(
    movement_id,
    *,
    group="scrip-1",
    date="2026-01-15",
    quantity="2",
    fmv="100",
    fmv_currency="EUR",
    gross="0",
    fees="0",
    account="acct-1",
    security="XNAS:ACME",
    status="ACTIVE",
    cost_basis_status="COMPLETE",
    event_type="SCRIP_DIVIDEND",
):
    row = {
        "id": movement_id,
        "txn_type": "BUY",
        "trade_date": date,
        "ticker": security.split(":")[-1],
        "security_id": security,
        "account_id": account,
        "correction_status": status,
        "ca_group_id": group,
        "ca_leg_type": "SHARE_ACQUISITION",
        "ca_event_type": event_type,
        "quantity": quantity,
        "cost_basis_status": cost_basis_status,
        "share_fmv": {
            "amount": str(fmv),
            "currency": fmv_currency,
            "eur_amount": str(fmv),
            "valuation_date": date,
        },
        "gross": {
            "amount": str(gross),
            "currency": "EUR",
            "eur_amount": str(gross),
        },
        "fees": {"total_eur": str(fees)},
        "net": {"eur_amount": str(float(gross) + float(fees))},
    }
    return row


def _top_up(
    movement_id,
    *,
    group="scrip-1",
    date="2026-01-15",
    gross="5",
    fees="3",
    account="acct-1",
    security="XNAS:ACME",
    status="ACTIVE",
):
    return {
        "id": movement_id,
        "txn_type": "BUY",
        "trade_date": date,
        "ticker": security.split(":")[-1],
        "security_id": security,
        "account_id": account,
        "correction_status": status,
        "ca_group_id": group,
        "ca_leg_type": "CASH_TOP_UP",
        "ca_event_type": "DIVIDEND_WITH_SCRIP",
        "quantity": "0",
        "gross": {
            "amount": str(gross),
            "currency": "EUR",
            "eur_amount": str(gross),
        },
        "fees": {"total_eur": str(fees)},
        # Deliberately unrelated: CASH_TOP_UP.net is not an economics input.
        "net": {"eur_amount": "9999"},
    }


def _empty_options_report():
    return {
        "summary": {
            "net_income_eur": 0.0,
            "total_positions": 0,
            "coverage": {},
        },
        "monthly": [],
        "by_symbol": [],
        "filters": {"years": [], "symbols": []},
    }


def test_pure_and_mixed_scrip_use_event_formula_and_preserve_cash_only_tax_fields():
    report = build_dividends_economics_report(
        [
            _share("pure-share", group="pure", fmv="40"),
            _cash("mixed-cash", group="mixed", amount=20),
            _share("mixed-share", group="mixed", fmv="100", gross="10", fees="2"),
            _top_up("mixed-top-up", group="mixed", gross="5", fees="3"),
        ]
    )

    summary = report["summary"]
    assert summary["total_net_eur"] == 20.0
    assert summary["cash_net"] == 20.0
    assert summary["scrip_fmv_eur"] == 140.0
    assert summary["scrip_personal_contribution_eur"] == 15.0
    assert summary["scrip_attributable_fees_eur"] == 5.0
    assert summary["scrip_dividends_eur"] == 120.0
    assert summary["total_dividends_eur"] == 140.0
    assert summary["total_net"] == 140.0
    assert summary["total_gross_eur"] == 20.0
    assert summary["total_fees_eur"] == 0.0
    assert summary["total_withholding_eur"] == 0.0
    assert summary["total_dividends"] == 2
    assert summary["scrip_events_total"] == 2
    assert summary["scrip_events_valued"] == 2
    assert summary["scrip_events_unvalued"] == 0
    assert summary["scrip_valuation_status"] == "COMPLETE"
    assert summary["total_dividends_is_partial"] is False


def test_multiple_share_legs_are_summed_once_after_movement_id_deduplication():
    first = _share("share-a", group="multi", fmv="30", gross="2", fees="1")
    report = build_dividends_economics_report(
        [
            first,
            deepcopy(first),
            _share("share-b", group="multi", fmv="70", gross="3", fees="2"),
            _top_up("top-up", group="multi", gross="4", fees="1"),
        ]
    )

    assert report["summary"]["scrip_fmv_eur"] == 100.0
    assert report["summary"]["scrip_personal_contribution_eur"] == 9.0
    assert report["summary"]["scrip_attributable_fees_eur"] == 4.0
    assert report["summary"]["scrip_dividends_eur"] == 87.0
    assert report["summary"]["total_dividends"] == 1
    assert report["summary"]["scrip_events_total"] == 1


def test_ineligible_buy_top_up_and_inactive_or_deleted_documents_never_participate():
    ordinary_buy = _share("ordinary", group="ignored", fmv="999")
    ordinary_buy.pop("ca_group_id")
    ordinary_buy.pop("ca_leg_type")
    ordinary_buy.pop("ca_event_type")
    isolated_top_up = _top_up("isolated", group="")
    superseded = _share("superseded", group="bad-1", status="SUPERSEDED")
    voided = _share("voided", group="bad-2", status="VOIDED")
    deleted = _share("deleted", group="bad-3")
    deleted["is_deleted"] = True

    report = build_dividends_economics_report(
        [ordinary_buy, isolated_top_up, superseded, voided, deleted]
    )

    assert report["summary"]["scrip_dividends_eur"] == 0.0
    assert report["summary"]["total_dividends_eur"] == 0.0
    assert report["summary"]["scrip_events_total"] == 0
    assert report["summary"]["scrip_valuation_status"] == "NOT_APPLICABLE"


@pytest.mark.parametrize(
    ("mutate", "expected_reason"),
    [
        (
            lambda rows: rows[0].update(cost_basis_status="INCOMPLETE"),
            "COST_BASIS_INCOMPLETE",
        ),
        (
            lambda rows: rows[0]["gross"].pop("eur_amount"),
            "MISSING_CONTRIBUTION_EUR",
        ),
        (
            lambda rows: rows[0]["share_fmv"].update(eur_amount="NaN"),
            "MISSING_OR_INVALID_SHARE_FMV_EUR",
        ),
        (
            lambda rows: rows[0].update(quantity="0"),
            "MISSING_OR_INVALID_QUANTITY",
        ),
        (
            lambda rows: rows[0]["fees"].update(total_eur="-1"),
            "INVALID_SHARE_FEES_EUR",
        ),
        (
            lambda rows: rows.append(_top_up("bad-top-up-gross", gross="not-a-number")),
            "INVALID_TOP_UP_CONTRIBUTION_EUR",
        ),
        (
            lambda rows: rows.append(_top_up("bad-top-up-fee", fees="-1")),
            "INVALID_TOP_UP_FEES_EUR",
        ),
        (
            lambda rows: rows.append(
                _cash("wrong-identity", group="scrip-1", account="acct-2")
            ),
            "INVALID_EVENT_IDENTITY",
        ),
        (
            lambda rows: rows[0].update(trade_date="not-a-date"),
            "INVALID_EVENT_DATE",
        ),
    ],
)
def test_unvalued_scrip_diagnostics_expose_each_stable_reason(mutate, expected_reason):
    rows = [_share("share-1")]
    mutate(rows)

    report = build_dividends_economics_report(rows)

    assert report["summary"]["scrip_events_unvalued"] == 1
    assert len(report["unvalued_scrip_events"]) == 1
    diagnostic = report["unvalued_scrip_events"][0]
    assert diagnostic["event_id"] == "scrip-1"
    assert expected_reason in diagnostic["reason_codes"]
    assert set(diagnostic) == {
        "event_id",
        "movement_ids",
        "share_leg_ids",
        "top_up_movement_ids",
        "account_id",
        "security_id",
        "symbol",
        "trade_date",
        "reason_codes",
    }
    assert "gross" not in diagnostic
    assert "fees" not in diagnostic
    assert "share_fmv" not in diagnostic


def test_unvalued_scrip_diagnostics_follow_filters_dedup_and_bounded_output():
    duplicate = _share(
        "duplicate",
        group="kept",
        account="acct-keep",
        security="XLON:KEEP",
        cost_basis_status="INCOMPLETE",
    )
    movements = [duplicate, deepcopy(duplicate)]
    movements.extend(
        _share(
            f"share-{index}",
            group=f"event-{index:03d}",
            account="acct-keep",
            security="XLON:KEEP",
            cost_basis_status="INCOMPLETE",
        )
        for index in range(105)
    )
    movements.append(
        _share(
            "excluded",
            group="excluded",
            account="acct-other",
            security="XLON:OTHER",
            cost_basis_status="INCOMPLETE",
        )
    )

    report = build_dividends_economics_report(
        movements,
        account_filter=["acct-keep"],
        symbol_filter=["KEEP"],
    )

    assert report["summary"]["scrip_events_total"] == 106
    assert report["meta"]["unvalued_scrip_events_total"] == 106
    assert report["meta"]["unvalued_scrip_events_limit"] == 100
    assert report["meta"]["unvalued_scrip_events_truncated"] is True
    assert len(report["unvalued_scrip_events"]) == 100
    assert all(
        row["account_id"] == "acct-keep"
        and row["symbol"] == "KEEP"
        and row["event_id"] != "excluded"
        for row in report["unvalued_scrip_events"]
    )


@pytest.mark.parametrize(
    ("mutation", "expected_status"),
    [
        (lambda row: row.pop("share_fmv"), "UNAVAILABLE"),
        (lambda row: row["share_fmv"].update(eur_amount="not-a-number"), "UNAVAILABLE"),
        (lambda row: row["share_fmv"].update(eur_amount="NaN"), "UNAVAILABLE"),
        (lambda row: row["share_fmv"].update(eur_amount="Infinity"), "UNAVAILABLE"),
        (lambda row: row.update(quantity="0"), "UNAVAILABLE"),
        (lambda row: row.update(quantity="-1"), "UNAVAILABLE"),
        (lambda row: row.update(cost_basis_status="INCOMPLETE"), "UNAVAILABLE"),
        (lambda row: row["gross"].update(eur_amount="-1"), "UNAVAILABLE"),
        (lambda row: row["fees"].update(total_eur="-1"), "UNAVAILABLE"),
    ],
)
def test_missing_malformed_or_noncanonical_required_values_fail_closed(
    mutation, expected_status
):
    movement = _share("invalid")
    mutation(movement)

    report = build_dividends_economics_report([movement])

    assert report["summary"]["scrip_dividends_eur"] is None
    assert report["summary"]["total_dividends_eur"] == 0.0
    assert report["summary"]["scrip_events_total"] == 1
    assert report["summary"]["scrip_events_valued"] == 0
    assert report["summary"]["scrip_events_unvalued"] == 1
    assert report["summary"]["scrip_valuation_status"] == expected_status
    assert report["summary"]["total_dividends_is_partial"] is True


def test_unvalued_diagnostics_publish_exact_ids_and_stable_reason_codes():
    movement = _share("share-invalid", group="event-invalid")
    movement["cost_basis_status"] = "INCOMPLETE"
    movement["gross"]["eur_amount"] = None
    movement["fees"]["total_eur"] = "-1"
    movement["share_fmv"]["eur_amount"] = "bad"
    movement["quantity"] = "0"
    top_up = _top_up("top-invalid", group="event-invalid")
    top_up["gross"]["eur_amount"] = None
    top_up["fees"]["total_eur"] = "NaN"

    report = build_dividends_economics_report([movement, top_up])

    assert report["summary"]["scrip_valuation_status"] == "UNAVAILABLE"
    assert report["unvalued_scrip_events"] == [
        {
            "event_id": "event-invalid",
            "movement_ids": ["share-invalid", "top-invalid"],
            "share_leg_ids": ["share-invalid"],
            "top_up_movement_ids": ["top-invalid"],
            "account_id": "acct-1",
            "security_id": "XNAS:ACME",
            "symbol": "ACME",
            "trade_date": "2026-01-15",
            "reason_codes": [
                "COST_BASIS_INCOMPLETE",
                "INVALID_SHARE_FEES_EUR",
                "INVALID_TOP_UP_CONTRIBUTION_EUR",
                "INVALID_TOP_UP_FEES_EUR",
                "MISSING_CONTRIBUTION_EUR",
                "MISSING_OR_INVALID_QUANTITY",
                "MISSING_OR_INVALID_SHARE_FMV_EUR",
            ],
        }
    ]
    assert report["meta"]["unvalued_scrip_events_total"] == 1
    assert report["meta"]["unvalued_scrip_events_truncated"] is False


def test_zero_cost_negative_and_legitimate_zero_results_are_preserved():
    report = build_dividends_economics_report(
        [
            _share(
                "zero-cost",
                group="zero-cost",
                fmv="25",
                cost_basis_status="ZERO_COST",
            ),
            _share(
                "negative",
                group="negative",
                fmv="5",
                gross="8",
                fees="1",
            ),
            _share(
                "zero",
                group="zero",
                fmv="10",
                gross="8",
                fees="2",
            ),
        ]
    )

    assert report["summary"]["scrip_dividends_eur"] == 21.0
    by_symbol = {row["symbol"]: row for row in report["by_symbol"]}
    # All three events intentionally use one symbol, so the aggregate proves
    # neither the negative nor zero event was discarded as falsy.
    assert by_symbol["ACME"]["scrip_events_total"] == 3
    assert by_symbol["ACME"]["scrip_events_valued"] == 3
    assert by_symbol["ACME"]["scrip_dividends_eur"] == 21.0


def test_partial_and_unavailable_coverage_publish_only_known_subtotals():
    valid = _share("valid", group="valid", fmv="50", gross="5", fees="1")
    invalid = _share("invalid", group="invalid")
    invalid.pop("share_fmv")

    partial = build_dividends_economics_report([valid, invalid])
    assert partial["summary"]["scrip_fmv_eur"] == 50.0
    assert partial["summary"]["scrip_personal_contribution_eur"] == 5.0
    assert partial["summary"]["scrip_attributable_fees_eur"] == 1.0
    assert partial["summary"]["scrip_dividends_eur"] == 44.0
    assert partial["summary"]["total_dividends_eur"] == 44.0
    assert partial["summary"]["scrip_valuation_status"] == "PARTIAL"
    assert partial["summary"]["total_dividends_is_partial"] is True
    assert partial["summary"]["scrip_events_total"] == 2
    assert partial["summary"]["scrip_events_valued"] == 1
    assert partial["summary"]["scrip_events_unvalued"] == 1

    unavailable = build_dividends_economics_report([invalid])
    assert unavailable["summary"]["scrip_fmv_eur"] is None
    assert unavailable["summary"]["scrip_personal_contribution_eur"] is None
    assert unavailable["summary"]["scrip_attributable_fees_eur"] is None
    assert unavailable["summary"]["scrip_dividends_eur"] is None
    assert unavailable["summary"]["scrip_valuation_status"] == "UNAVAILABLE"


def test_group_date_identity_and_filters_apply_to_the_whole_event():
    rows = [
        _cash("cash", group="usd-group", date="2025-12-31", currency="GBP"),
        _share(
            "share",
            group="usd-group",
            date="2026-02-10",
            fmv="60",
            fmv_currency="USD",
            account="acct-2",
            security="XNYS:USDCO",
        ),
        _top_up(
            "top",
            group="usd-group",
            date="2026-03-01",
            gross="5",
            account="acct-2",
            security="XNYS:USDCO",
        ),
        _share(
            "eur-share",
            group="eur-group",
            date="2026-04-10",
            fmv="30",
            fmv_currency="EUR",
            account="acct-1",
            security="XMAD:EURCO",
        ),
    ]
    # Keep group identity valid despite intentionally different leg dates.
    rows[0]["account_id"] = "acct-2"
    rows[0]["security_id"] = "XNYS:USDCO"
    rows[0]["ticker"] = "USDCO"

    report = build_dividends_economics_report(
        rows,
        year=2026,
        month_filter=[2],
        symbol_filter=["usdco"],
        account_filter=["acct-2"],
        currency_filter=["USD"],
    )

    assert report["summary"]["cash_net"] == 20.0
    assert report["summary"]["scrip_dividends_eur"] == 52.0
    assert report["summary"]["total_dividends_eur"] == 72.0
    assert [row["month"] for row in report["monthly"]] == ["2026-02"]
    assert report["by_symbol"][0]["symbol"] == "USDCO"
    assert report["filters"]["currencies"] == ["EUR", "GBP", "USD"]
    assert report["applied_filters"]["currencies"] == ["USD"]

    top_up_currency_only = build_dividends_economics_report(
        [
            _share("share-only", group="top-currency", fmv_currency="EUR"),
            _top_up("usd-top", group="top-currency"),
        ],
        currency_filter=["USD"],
    )
    assert top_up_currency_only["summary"]["total_dividends"] == 0


def test_conflicting_group_identity_or_share_dates_make_the_event_unavailable():
    mixed_account = [
        _share("a", group="identity", account="acct-1"),
        _share("b", group="identity", account="acct-2"),
    ]
    mixed_security = [
        _share("c", group="security", security="XNAS:ONE"),
        _share("d", group="security", security="XNYS:TWO"),
    ]
    mixed_dates = [
        _share("e", group="dates", date="2026-01-01"),
        _share("f", group="dates", date="2026-02-01"),
    ]

    report = build_dividends_economics_report(
        mixed_account + mixed_security + mixed_dates
    )

    assert report["summary"]["scrip_events_total"] == 3
    assert report["summary"]["scrip_events_valued"] == 0
    assert report["summary"]["scrip_events_unvalued"] == 3
    assert report["summary"]["scrip_dividends_eur"] is None
    assert report["summary"]["scrip_valuation_status"] == "UNAVAILABLE"


def test_combined_fields_flow_through_monthly_yearly_symbol_and_cumulative_rows():
    report = build_dividends_economics_report(
        [
            _cash("cash-2025", date="2025-01-10", amount=10, security="XNAS:ACME"),
            _share(
                "scrip-2025",
                group="scrip-2025",
                date="2025-01-15",
                fmv="40",
                security="XNAS:ACME",
            ),
            _cash("cash-2026", date="2026-02-10", amount=5, security="XNYS:BETA"),
            _share(
                "scrip-2026",
                group="scrip-2026",
                date="2026-02-15",
                fmv="20",
                security="XNYS:BETA",
            ),
        ]
    )

    assert report["summary"]["cash_net"] == 15.0
    assert report["summary"]["scrip_dividends_eur"] == 60.0
    assert report["summary"]["total_dividends_eur"] == 75.0
    assert report["summary"]["total_net"] == 75.0

    january, february = report["monthly"]
    assert january["cash_net"] == 10.0
    assert january["scrip_dividends_eur"] == 40.0
    assert january["total_dividends_eur"] == 50.0
    assert january["total_net"] == 50.0
    assert february["cash_net"] == 5.0
    assert february["scrip_dividends_eur"] == 20.0
    assert february["total_dividends_eur"] == 25.0

    yearly = {row["year"]: row for row in report["yearly"]}
    assert yearly[2025]["total_dividends_eur"] == 50.0
    assert yearly[2026]["total_dividends_eur"] == 25.0
    by_symbol = {row["symbol"]: row for row in report["by_symbol"]}
    assert by_symbol["ACME"]["total_dividends_eur"] == 50.0
    assert by_symbol["BETA"]["total_dividends_eur"] == 25.0
    assert report["cumulative"][-1]["cumulative_cash_net_eur"] == 15.0
    assert report["cumulative"][-1]["cumulative_total_net_eur"] == 75.0
    assert report["cumulative"][-1]["total_net"] == 75.0
    assert report["meta"]["event_granularity"] == "ca_group_id_or_movement_id"
    assert "share_fmv" in report["meta"]["value_field"]


def test_partial_status_is_present_on_every_aggregate_bucket_for_yoy_fail_closed():
    valid = _share("valid", group="valid", date="2025-01-10", fmv="20")
    invalid = _share("invalid", group="invalid", date="2026-01-10")
    invalid.pop("share_fmv")

    report = build_dividends_economics_report([valid, invalid])

    for collection in ("monthly", "yearly", "by_symbol"):
        assert all("scrip_valuation_status" in row for row in report[collection])
        assert all("total_dividends_is_partial" in row for row in report[collection])
        assert all("scrip_events_total" in row for row in report[collection])
        assert all("scrip_events_valued" in row for row in report[collection])
        assert all("scrip_events_unvalued" in row for row in report[collection])
    assert (
        next(row for row in report["yearly"] if row["year"] == 2026)[
            "scrip_valuation_status"
        ]
        == "UNAVAILABLE"
    )


@pytest.mark.parametrize(
    ("combined", "cash", "expected"),
    [(0.0, 99.0, 0.0), (-7.0, 99.0, -7.0)],
)
def test_overview_never_uses_or_fallback_for_zero_or_negative_combined_values(
    combined, cash, expected
):
    dividends_report = {
        "summary": {
            "total_net_eur": cash,
            "cash_net": cash,
            "scrip_dividends_eur": combined - cash,
            "total_dividends_eur": combined,
            "total_net": combined,
            "total_dividends_is_partial": False,
            "scrip_valuation_status": "COMPLETE",
            "scrip_events_total": 1,
            "scrip_events_valued": 1,
            "scrip_events_unvalued": 0,
            "total_dividends": 1,
        },
        "monthly": [
            {
                "month": "2026-01",
                "net_eur": cash,
                "cash_net": cash,
                "scrip_dividends_eur": combined - cash,
                "total_dividends_eur": combined,
                "total_net": combined,
                "total_dividends_is_partial": False,
                "scrip_valuation_status": "COMPLETE",
                "scrip_events_total": 1,
                "scrip_events_valued": 1,
                "scrip_events_unvalued": 0,
                "dividend_count": 1,
            }
        ],
        "by_symbol": [
            {
                "symbol": "ACME",
                "net_eur": cash,
                "cash_net": cash,
                "scrip_dividends_eur": combined - cash,
                "total_dividends_eur": combined,
                "total_net": combined,
                "total_dividends_is_partial": False,
                "scrip_valuation_status": "COMPLETE",
                "scrip_events_total": 1,
                "scrip_events_valued": 1,
                "scrip_events_unvalued": 0,
                "dividend_count": 1,
            }
        ],
        "filters": {"years": [2026], "symbols": ["ACME"]},
    }

    report = _build_economics_overview_report(_empty_options_report(), dividends_report)

    assert report["summary"]["dividends_net_eur"] == cash
    assert report["summary"]["dividends_cash_net_eur"] == cash
    assert report["summary"]["dividends_scrip_eur"] == combined - cash
    assert report["summary"]["dividends_total_net_eur"] == expected
    assert report["summary"]["combined_net_eur"] == expected
    assert report["monthly"][0]["dividends_total_net_eur"] == expected
    assert report["monthly"][0]["combined_net_eur"] == expected
    assert report["by_symbol"][0]["dividends_total_net_eur"] == expected
    assert report["by_symbol"][0]["combined_net_eur"] == expected


def test_overview_propagates_partial_coverage_without_recomputing_scrip():
    dividends_report = {
        "summary": {
            "total_net_eur": 10.0,
            "cash_net": 10.0,
            "scrip_dividends_eur": 20.0,
            "total_dividends_eur": 30.0,
            "total_net": 30.0,
            "total_dividends_is_partial": True,
            "scrip_valuation_status": "PARTIAL",
            "scrip_events_total": 2,
            "scrip_events_valued": 1,
            "scrip_events_unvalued": 1,
            "total_dividends": 3,
        },
        "monthly": [],
        "by_symbol": [],
        "filters": {"years": [], "symbols": []},
    }
    options = _empty_options_report()
    options["summary"]["net_income_eur"] = 5.0

    report = _build_economics_overview_report(options, dividends_report)

    assert report["summary"]["dividends_scrip_eur"] == 20.0
    assert report["summary"]["dividends_total_net_eur"] == 30.0
    assert report["summary"]["combined_net_eur"] == 35.0
    assert report["summary"]["total_dividends_is_partial"] is True
    assert report["summary"]["scrip_valuation_status"] == "PARTIAL"
    assert report["summary"]["scrip_events_total"] == 2
    assert report["summary"]["scrip_events_valued"] == 1
    assert report["summary"]["scrip_events_unvalued"] == 1
