from copy import deepcopy
from decimal import Decimal

import pytest

from src.portfolio.cosmos_portfolio import CosmosPortfolioService
from src.portfolio.holdings_service import acquisition_lot_unit_cost
from src.portfolio.models import normalize_share_fmv
from tests.conftest_portfolio_p2 import FakeCosmos


ACCOUNT = "acct_fmv"
SECURITY = "XLON:ULVR"
DATE = "2026-09-25"


@pytest.fixture
def svc(monkeypatch):
    fake = FakeCosmos()
    monkeypatch.setattr(
        "src.portfolio.cosmos_portfolio.ensure_symbol_config",
        lambda *args, **kwargs: None,
    )
    return (
        CosmosPortfolioService(
            portfolio_container=fake.portfolio_container,
            import_sessions_container=fake.import_sessions_container,
            symbols_container=None,
        ),
        fake,
    )


def _share_leg(**overrides):
    leg = {
        "leg_type": "SHARE_ACQUISITION",
        "trade_date": DATE,
        "quantity": "3",
        "gross": {"amount": "10", "currency": "EUR", "eur_amount": "10"},
        "fees": {"total": "2", "currency": "EUR", "total_eur": "2"},
    }
    leg.update(overrides)
    return leg


def _request(share_leg=None):
    return {
        "event_type": "SCRIP_DIVIDEND",
        "security_id": SECURITY,
        "account_id": ACCOUNT,
        "payment_date": DATE,
        "legs": [share_leg or _share_leg()],
    }


def _eur_fmv(**overrides):
    value = {
        "valuation_date": DATE,
        "currency": "EUR",
        "amount": "37.02",
        "source": "OFFICIAL_NOTICE",
        "reference": "Issuer notice 42",
    }
    value.update(overrides)
    return value


def _gbp_fmv(**overrides):
    value = {
        "valuation_date": DATE,
        "currency": "GBP",
        "price_per_share": "12.345678",
        "source": "BROKER",
        "fx": {"rate": "1.16648", "date": DATE, "source": "BROKER"},
    }
    value.update(overrides)
    return value


def test_manual_eur_fmv_derives_identity_and_all_values_half_up():
    result = normalize_share_fmv(
        _eur_fmv(), quantity="3", trade_date=DATE
    )
    assert result == {
        "valuation_date": DATE,
        "amount": "37.020000",
        "currency": "EUR",
        "eur_amount": "37.020000",
        "price_per_share": "12.340000",
        "price_per_share_eur": "12.340000",
        "source": "OFFICIAL_NOTICE",
        "confidence": "AUTHORITATIVE",
        "fx": {"rate": "1.000000000", "date": DATE, "source": "IDENTITY"},
        "provenance": {"reference": "Issuer notice 42"},
    }


def test_manual_foreign_fmv_derives_total_and_eur_values():
    result = normalize_share_fmv(_gbp_fmv(), quantity="3", trade_date=DATE)
    assert result["amount"] == "37.037034"
    assert result["price_per_share"] == "12.345678"
    assert result["eur_amount"] == "43.202959"
    assert result["price_per_share_eur"] == "14.400986"
    assert result["confidence"] == "AUTHORITATIVE"
    assert result["fx"] == {
        "rate": "1.166480000",
        "date": DATE,
        "source": "BROKER",
    }


@pytest.mark.parametrize(
    "patch",
    [
        {"valuation_date": "2026-09-24"},
        {"currency": "EU"},
        {"amount": "0"},
        {"amount": "-1"},
        {"amount": "NaN"},
        {"amount": "Infinity"},
        {"source": "UNKNOWN"},
        {"source": "YAHOO_OPEN"},
        {"source": "MANUAL", "confidence": "AUTHORITATIVE"},
        {"amount": "37", "price_per_share": "12"},
        {"eur_amount": "999"},
        {"price_per_share_eur": "999"},
    ],
)
def test_manual_fmv_rejects_invalid_or_incompatible_values(patch):
    with pytest.raises(ValueError):
        normalize_share_fmv(
            _eur_fmv(**patch), quantity="3", trade_date=DATE
        )


@pytest.mark.parametrize(
    "fx",
    [
        None,
        {"rate": "0", "date": DATE, "source": "ECB"},
        {"rate": "1.2", "date": DATE, "source": "IDENTITY"},
        {"rate": "1.2", "date": "not-a-date", "source": "ECB"},
        {"rate": "1.2", "date": DATE, "source": "UNKNOWN"},
    ],
)
def test_non_eur_fmv_requires_complete_valid_non_identity_fx(fx):
    with pytest.raises(ValueError):
        normalize_share_fmv(
            _gbp_fmv(fx=fx), quantity="3", trade_date=DATE
        )


def test_manual_fmv_rejects_unknown_three_letter_currency():
    with pytest.raises(ValueError, match="ISO-4217"):
        normalize_share_fmv(
            _gbp_fmv(currency="ZZZ"),
            quantity="3",
            trade_date=DATE,
        )


def test_creation_keeps_contribution_separate_from_fmv_and_fifo_cost(svc):
    service, _ = svc
    result = service.create_corporate_action(
        _request(_share_leg(share_fmv=_eur_fmv()))
    )
    movement = result["movements"][0]
    assert movement["gross"]["eur_amount"] == "10"
    assert movement["fees"]["total_eur"] == "2"
    assert Decimal(movement["net"]["eur_amount"]) == Decimal("12")
    assert movement["cost_basis_status"] == "COMPLETE"
    assert movement["share_fmv"]["eur_amount"] == "37.020000"


def test_foreign_share_acquisition_net_preserves_native_gross_plus_fees(svc):
    service, _ = svc
    movement = service.create_corporate_action(
        _request(_share_leg(
            gross={"amount": "10", "currency": "GBP", "eur_amount": "11.60"},
            fees={"total": "1", "currency": "GBP", "total_eur": "1.16"},
        ))
    )["movements"][0]
    assert movement["net"] == {
        "amount": "11.000000",
        "currency": "GBP",
        "eur_amount": "12.760000",
    }


def test_true_zero_cost_requires_zero_contribution_and_zero_fees(svc):
    service, _ = svc
    zero = service.create_corporate_action(
        _request(_share_leg(
            gross={"amount": "0", "currency": "EUR", "eur_amount": "0"},
            fees={"total": "0", "currency": "EUR", "total_eur": "0"},
        ))
    )["movements"][0]
    fee_only = service.create_corporate_action(
        _request(_share_leg(
            gross={"amount": "0", "currency": "EUR", "eur_amount": "0"},
            fees={"total": "2", "currency": "EUR", "total_eur": "2"},
        ))
    )["movements"][0]
    assert zero["cost_basis_status"] == "ZERO_COST"
    assert Decimal(zero["net"]["eur_amount"]) == 0
    assert fee_only["cost_basis_status"] == "COMPLETE"
    assert Decimal(fee_only["net"]["eur_amount"]) == 2


def test_cash_top_up_contract_is_unchanged_and_never_gets_fmv(svc):
    service, _ = svc
    request = {
        "event_type": "DIVIDEND_WITH_SCRIP",
        "security_id": SECURITY,
        "account_id": ACCOUNT,
        "payment_date": DATE,
        "legs": [
            {
                "leg_type": "CASH_DIVIDEND",
                "trade_date": DATE,
                "gross": {"amount": "1", "currency": "EUR", "eur_amount": "1"},
            },
            _share_leg(),
            {
                "leg_type": "CASH_TOP_UP",
                "trade_date": DATE,
                "gross": {"amount": "4", "currency": "EUR", "eur_amount": "4"},
            },
        ],
    }
    top_up = next(
        item for item in service.create_corporate_action(request)["movements"]
        if item["ca_leg_type"] == "CASH_TOP_UP"
    )
    assert top_up["quantity"] == "0"
    assert top_up["cost_basis_status"] == "INCOMPLETE"
    assert "share_fmv" not in top_up


def test_share_fmv_is_not_inferred_from_historical_gross(svc):
    service, _ = svc
    movement = service.create_corporate_action(_request())["movements"][0]
    assert "share_fmv" not in movement
    assert movement["gross"]["eur_amount"] == "10"


def test_fmv_does_not_change_holdings_economics():
    base = {
        "id": "buy",
        "account_id": ACCOUNT,
        "doc_type": "ledger_txn",
        "txn_type": "BUY",
        "security_id": SECURITY,
        "trade_date": DATE,
        "quantity": "3",
        "gross": {"amount": "10", "currency": "EUR", "eur_amount": "10"},
        "fees": {"total": "2", "currency": "EUR", "total_eur": "2"},
        "net": {"amount": "12", "currency": "EUR", "eur_amount": "12"},
        "cost_basis_status": "COMPLETE",
        "correction_status": "ACTIVE",
        "ca_leg_type": "SHARE_ACQUISITION",
        "ca_event_type": "SCRIP_DIVIDEND",
    }
    with_fmv = deepcopy(base)
    with_fmv["share_fmv"] = normalize_share_fmv(
        _eur_fmv(), quantity="3", trade_date=DATE
    )
    assert acquisition_lot_unit_cost(base) == Decimal("4")
    assert acquisition_lot_unit_cost(with_fmv) == acquisition_lot_unit_cost(base)


def test_group_correction_inherits_clears_and_replaces_fmv(svc):
    service, fake = svc
    original = service.create_corporate_action(
        _request(_share_leg(share_fmv=_eur_fmv()))
    )
    original_id = original["movements"][0]["id"]

    inherited = service.correct_corporate_action_group(
        original["ca_group_id"],
        {
            **_request(),
            "correction_note": "same identity",
            "legs": [_share_leg(gross={"amount": "11", "currency": "EUR", "eur_amount": "11"})],
        },
    )
    inherited_share = inherited["movements"][0]
    assert inherited_share["share_fmv"]["amount"] == "37.020000"
    assert fake.portfolio_container._store[original_id]["share_fmv"]["amount"] == "37.020000"
    assert fake.portfolio_container._store[original_id]["correction_status"] == "SUPERSEDED"

    cleared = service.correct_corporate_action_group(
        inherited["ca_group_id"],
        {
            **_request(),
            "correction_note": "clear valuation",
            "legs": [_share_leg(share_fmv=None)],
        },
    )
    assert "share_fmv" not in cleared["movements"][0]

    replaced = service.correct_corporate_action_group(
        cleared["ca_group_id"],
        {
            **_request(),
            "correction_note": "new valuation",
            "legs": [_share_leg(share_fmv=_eur_fmv(amount="39"))],
        },
    )
    assert replaced["movements"][0]["share_fmv"]["amount"] == "39.000000"


@pytest.mark.parametrize(
    "changed",
    [
        {"quantity": "4"},
        {"trade_date": "2026-09-26"},
    ],
)
def test_correction_identity_change_requires_explicit_fmv_or_null(svc, changed):
    service, _ = svc
    original = service.create_corporate_action(
        _request(_share_leg(share_fmv=_eur_fmv()))
    )
    leg = _share_leg(**changed)
    with pytest.raises(ValueError, match="share_fmv must be supplied or null"):
        service.correct_corporate_action_group(
            original["ca_group_id"],
            {**_request(), "correction_note": "identity changed", "legs": [leg]},
        )


def test_correction_security_change_requires_explicit_fmv_or_null(svc):
    service, _ = svc
    original = service.create_corporate_action(
        _request(_share_leg(share_fmv=_eur_fmv()))
    )
    request = _request()
    request["security_id"] = "XLON:DGE"
    request["correction_note"] = "security changed"
    with pytest.raises(ValueError, match="share_fmv must be supplied or null"):
        service.correct_corporate_action_group(original["ca_group_id"], request)


def test_share_fmv_cannot_be_patched_on_an_individual_group_leg(svc):
    service, _ = svc
    original = service.create_corporate_action(_request())
    with pytest.raises(ValueError, match="group_leg_correction_required"):
        service.correct_movement(
            original["movements"][0]["id"],
            ACCOUNT,
            {"share_fmv": _eur_fmv(), "correction_note": "not atomic"},
        )
