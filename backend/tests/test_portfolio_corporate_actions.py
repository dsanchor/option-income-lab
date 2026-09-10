"""Amendment H — Corporate-Action Group tests.

Covers:
- H-T1: DIVIDEND_WITH_SCRIP with 4 legs → 4 ledger_txn docs with shared ca_group_id
- H-T2: CASH_DIVIDEND leg → txn_type=DIVIDEND
- H-T3: SHARE_ACQUISITION (COMPLETE) → txn_type=BUY, cost_basis_status=COMPLETE
- H-T4: SHARE_ACQUISITION (INCOMPLETE) → txn_type=BUY, cost_basis_status=INCOMPLETE
- H-T5: RIGHTS_SOLD → txn_type=SELL, sales_type=DERECHOS
- H-T6: CASH_TOP_UP → txn_type=BUY, quantity=0, cost_basis_status=INCOMPLETE
- H-T7: Correct one leg → replacement inherits ca_group_id
- H-T8: Void group → all legs VOIDED
- H-T9: Missing required leg → rejected
- H-T10: Individual leg failing validation → entire group rejected
- H-W1: WHT rate_pct derived server-side for create (amount-primary)
- H-W2: WHT rate_pct derived server-side for correct (amount-primary)
- H-W3: WHT rate_pct = 0 when gross_eur is zero
"""

from __future__ import annotations

import pytest
from decimal import Decimal
from fastapi.testclient import TestClient

from src.portfolio.cosmos_portfolio import CosmosPortfolioService
from src.portfolio.cosmos_securities import CosmosSecuritiesService
from tests.conftest_portfolio_p2 import FakeCosmos


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    from web.app import app
    return TestClient(app)


@pytest.fixture
def svc(monkeypatch):
    """CosmosPortfolioService backed by FakeCosmos (no network)."""
    fake = FakeCosmos()
    svc = CosmosPortfolioService(
        portfolio_container=fake.portfolio_container,
        import_sessions_container=fake.import_sessions_container,
        symbols_container=None,
    )
    monkeypatch.setattr(
        "src.portfolio.cosmos_portfolio.ensure_symbol_config",
        lambda *a, **kw: None,
    )
    return svc


_SECURITY_ID = "XLON:ULVR"
_ACCOUNT_ID = "heytrade_main"

_CA_4_LEGS = {
    "event_type": "DIVIDEND_WITH_SCRIP",
    "security_id": _SECURITY_ID,
    "account_id": _ACCOUNT_ID,
    "payment_date": "2024-03-28",
    "notes": "Unilever Q1 2024",
    "legs": [
        {
            "leg_type": "CASH_DIVIDEND",
            "trade_date": "2024-03-28",
            "gross": {"amount": "180.00", "currency": "GBP", "eur_amount": "209.79"},
            "withholding": {
                "source": {"country": "GB", "amount_eur": "0"},
                "destination": {"country": "ES", "amount_eur": "39.86"},
            },
            "fx": {"rate": "1.165500000", "rate_source": "ECB"},
        },
        {
            "leg_type": "SHARE_ACQUISITION",
            "trade_date": "2024-03-28",
            "quantity": "9",
            "gross": {"amount": "0", "currency": "GBP", "eur_amount": "0"},
            "cost_basis_status": "INCOMPLETE",
            "fx": {"rate": "1.165500000", "rate_source": "ECB"},
            "notes": "FMV: 24.75 GBP/share",
        },
        {
            "leg_type": "RIGHTS_SOLD",
            "trade_date": "2024-03-28",
            "quantity": "3",
            "gross": {"amount": "67.50", "currency": "GBP", "eur_amount": "78.67"},
            "fees": {"total": "2.00", "currency": "GBP", "total_eur": "2.33"},
            "fx": {"rate": "1.165500000", "rate_source": "ECB"},
        },
        {
            "leg_type": "CASH_TOP_UP",
            "trade_date": "2024-03-28",
            "gross": {"amount": "4.95", "currency": "GBP", "eur_amount": "5.77"},
            "fx": {"rate": "1.165500000", "rate_source": "ECB"},
        },
    ],
}


# ---------------------------------------------------------------------------
# H-T1: 4-leg group creation
# ---------------------------------------------------------------------------

class TestCorporateActionCreate:
    def test_ht1_four_legs_created(self, svc):
        """H-T1: 4 legs created with shared ca_group_id."""
        result = svc.create_corporate_action(_CA_4_LEGS)
        assert "ca_group_id" in result
        assert result["ca_group_id"].startswith("cag_")
        assert result["event_type"] == "DIVIDEND_WITH_SCRIP"
        assert len(result["movements"]) == 4

    def test_ht1_all_share_group_id(self, svc):
        """H-T1: All 4 movements share the same ca_group_id."""
        result = svc.create_corporate_action(_CA_4_LEGS)
        group_id = result["ca_group_id"]
        for mvt in result["movements"]:
            assert mvt["ca_group_id"] == group_id

    def test_ht1_seq_numbers(self, svc):
        """H-T1: ca_group_seq is 1-based and monotonically increasing."""
        result = svc.create_corporate_action(_CA_4_LEGS)
        seqs = sorted(m["ca_group_seq"] for m in result["movements"])
        assert seqs == [1, 2, 3, 4]

    def test_ht2_cash_dividend_txn_type(self, svc):
        """H-T2: CASH_DIVIDEND leg → txn_type=DIVIDEND."""
        result = svc.create_corporate_action(_CA_4_LEGS)
        div_leg = next(m for m in result["movements"] if m["ca_leg_type"] == "CASH_DIVIDEND")
        assert div_leg["txn_type"] == "DIVIDEND"

    def test_ht3_share_acquisition_complete(self, svc):
        """H-T3: SHARE_ACQUISITION(COMPLETE) → BUY, cost_basis_status=COMPLETE."""
        req = {
            "event_type": "DIVIDEND_WITH_SCRIP",
            "security_id": _SECURITY_ID,
            "account_id": _ACCOUNT_ID,
            "payment_date": "2024-03-28",
            "legs": [
                {
                    "leg_type": "CASH_DIVIDEND",
                    "trade_date": "2024-03-28",
                    "gross": {"amount": "180.00", "currency": "GBP", "eur_amount": "209.79"},
                },
                {
                    "leg_type": "SHARE_ACQUISITION",
                    "trade_date": "2024-03-28",
                    "quantity": "9",
                    "gross": {"amount": "222.75", "currency": "GBP", "eur_amount": "259.52"},
                    "cost_basis_status": "COMPLETE",
                },
            ],
        }
        result = svc.create_corporate_action(req)
        acq = next(m for m in result["movements"] if m["ca_leg_type"] == "SHARE_ACQUISITION")
        assert acq["txn_type"] == "BUY"
        assert acq["cost_basis_status"] == "COMPLETE"
        assert acq["quantity"] == "9"

    def test_ht4_share_acquisition_incomplete(self, svc):
        """H-T4: SHARE_ACQUISITION(INCOMPLETE) → BUY, cost_basis_status=INCOMPLETE."""
        result = svc.create_corporate_action(_CA_4_LEGS)
        acq = next(m for m in result["movements"] if m["ca_leg_type"] == "SHARE_ACQUISITION")
        assert acq["txn_type"] == "BUY"
        assert acq["cost_basis_status"] == "INCOMPLETE"

    def test_ht5_rights_sold_derechos(self, svc):
        """H-T5: RIGHTS_SOLD → txn_type=SELL, sales_type=DERECHOS."""
        result = svc.create_corporate_action(_CA_4_LEGS)
        rs = next(m for m in result["movements"] if m["ca_leg_type"] == "RIGHTS_SOLD")
        assert rs["txn_type"] == "SELL"
        assert rs["sales_type"] == "DERECHOS"

    def test_ht6_cash_top_up_qty_zero_incomplete(self, svc):
        """H-T6: CASH_TOP_UP → BUY, quantity=0, cost_basis_status=INCOMPLETE."""
        result = svc.create_corporate_action(_CA_4_LEGS)
        top = next(m for m in result["movements"] if m["ca_leg_type"] == "CASH_TOP_UP")
        assert top["txn_type"] == "BUY"
        assert top["quantity"] == "0"
        assert top["cost_basis_status"] == "INCOMPLETE"

    def test_ht9_missing_required_leg_rejected(self, svc):
        """H-T9: DIVIDEND_WITH_SCRIP without SHARE_ACQUISITION → ValueError."""
        req = {
            "event_type": "DIVIDEND_WITH_SCRIP",
            "security_id": _SECURITY_ID,
            "account_id": _ACCOUNT_ID,
            "payment_date": "2024-03-28",
            "legs": [
                {
                    "leg_type": "CASH_DIVIDEND",
                    "trade_date": "2024-03-28",
                    "gross": {"amount": "180.00", "currency": "GBP", "eur_amount": "209.79"},
                },
                # Missing SHARE_ACQUISITION
            ],
        }
        with pytest.raises(ValueError, match="requires leg types"):
            svc.create_corporate_action(req)

    def test_ht9_scrip_dividend_requires_share_acquisition(self, svc):
        """H-T9: SCRIP_DIVIDEND with no SHARE_ACQUISITION → rejected."""
        req = {
            "event_type": "SCRIP_DIVIDEND",
            "security_id": _SECURITY_ID,
            "account_id": _ACCOUNT_ID,
            "payment_date": "2024-03-28",
            "legs": [
                {
                    "leg_type": "CASH_TOP_UP",
                    "trade_date": "2024-03-28",
                    "gross": {"amount": "4.95", "currency": "GBP", "eur_amount": "5.77"},
                },
            ],
        }
        with pytest.raises(ValueError, match="requires leg types"):
            svc.create_corporate_action(req)

    def test_ht10_invalid_leg_type_rejected(self, svc):
        """H-T10: Unrecognized leg_type → entire group rejected."""
        req = {
            "event_type": "SCRIP_DIVIDEND",
            "security_id": _SECURITY_ID,
            "account_id": _ACCOUNT_ID,
            "payment_date": "2024-03-28",
            "legs": [
                {
                    "leg_type": "MAGIC_LEG",
                    "trade_date": "2024-03-28",
                    "gross": {"amount": "100.00", "currency": "GBP", "eur_amount": "116.55"},
                },
            ],
        }
        with pytest.raises(ValueError):
            svc.create_corporate_action(req)

    def test_all_or_nothing_no_partial_write(self, svc):
        """H-T10 extended: failed validation leaves no partial documents in store."""
        req = {
            "event_type": "DIVIDEND_WITH_SCRIP",
            "security_id": _SECURITY_ID,
            "account_id": _ACCOUNT_ID,
            "payment_date": "2024-03-28",
            "legs": [
                {
                    "leg_type": "CASH_DIVIDEND",
                    "trade_date": "2024-03-28",
                    "gross": {"amount": "180.00", "currency": "GBP", "eur_amount": "209.79"},
                },
                # SHARE_ACQUISITION missing — will fail before any write
            ],
        }
        initial_count = len(svc.portfolio_container._store)
        with pytest.raises(ValueError):
            svc.create_corporate_action(req)
        assert len(svc.portfolio_container._store) == initial_count

    def test_net_computed_per_leg(self, svc):
        """Each leg's net is computed server-side from gross - fees - wht."""
        result = svc.create_corporate_action(_CA_4_LEGS)
        div_leg = next(m for m in result["movements"] if m["ca_leg_type"] == "CASH_DIVIDEND")
        # gross_eur=209.79, wht_dest=39.86, wht_src=0 → net=209.79-39.86=169.93
        net_eur = Decimal(div_leg["net"]["eur_amount"])
        assert abs(net_eur - Decimal("169.930000")) < Decimal("0.0001")

    def test_ca_event_type_stored_on_each_leg(self, svc):
        """ca_event_type is stored on every leg doc."""
        result = svc.create_corporate_action(_CA_4_LEGS)
        for mvt in result["movements"]:
            assert mvt["ca_event_type"] == "DIVIDEND_WITH_SCRIP"


# ---------------------------------------------------------------------------
# H-T7: Correct one leg preserves ca_group_id
# ---------------------------------------------------------------------------

class TestCorporateActionLegCorrection:
    """H-T7: CA group leg correction semantics.

    Financial field changes (gross, fees, withholding, quantity, fx, sales_type,
    cost_basis_status) on a CA leg MUST go through the group correction endpoint
    (POST /corporate-actions/{ca_group_id}/correct). The individual correct_movement()
    endpoint rejects them with group_leg_correction_required.

    Non-financial corrections (trade_date, notes) ARE allowed via individual endpoint.
    """

    def test_ht7_financial_field_change_rejected_on_ca_leg(self, svc):
        """H-T7: correct_movement() with gross on a CA leg → group_leg_correction_required."""
        create_result = svc.create_corporate_action(_CA_4_LEGS)
        div_mvt = next(m for m in create_result["movements"] if m["ca_leg_type"] == "CASH_DIVIDEND")

        with pytest.raises(ValueError, match="group_leg_correction_required"):
            svc.correct_movement(
                div_mvt["id"],
                _ACCOUNT_ID,
                {
                    "account_id": _ACCOUNT_ID,
                    "correction_note": "Fix gross amount",
                    "gross": {"amount": "181.00", "currency": "GBP", "eur_amount": "210.95"},
                },
            )

    def test_ht7_withholding_change_rejected_on_ca_leg(self, svc):
        """H-T7: withholding is a financial field — must also be rejected individually."""
        create_result = svc.create_corporate_action(_CA_4_LEGS)
        div_mvt = next(m for m in create_result["movements"] if m["ca_leg_type"] == "CASH_DIVIDEND")

        with pytest.raises(ValueError, match="group_leg_correction_required"):
            svc.correct_movement(
                div_mvt["id"],
                _ACCOUNT_ID,
                {
                    "account_id": _ACCOUNT_ID,
                    "correction_note": "Fix withholding",
                    "withholding": {
                        "destination": {"country": "ES", "amount_eur": "45.00"},
                    },
                },
            )

    def test_ht7_error_cites_group_id_and_endpoint(self, svc):
        """H-T7: error message must cite the ca_group_id and the correction endpoint path."""
        create_result = svc.create_corporate_action(_CA_4_LEGS)
        group_id = create_result["ca_group_id"]
        div_mvt = next(m for m in create_result["movements"] if m["ca_leg_type"] == "CASH_DIVIDEND")

        with pytest.raises(ValueError) as exc_info:
            svc.correct_movement(
                div_mvt["id"],
                _ACCOUNT_ID,
                {
                    "account_id": _ACCOUNT_ID,
                    "correction_note": "test",
                    "fees": {"total": "5.00", "currency": "GBP", "total_eur": "5.83"},
                },
            )
        msg = str(exc_info.value)
        assert group_id in msg, "Error must cite the ca_group_id"
        assert "/correct" in msg, "Error must cite the group correction endpoint"

    def test_ht7_non_financial_correction_allowed_on_ca_leg(self, svc):
        """H-T7: trade_date and notes are not financial fields — individual correction OK."""
        create_result = svc.create_corporate_action(_CA_4_LEGS)
        div_mvt = next(m for m in create_result["movements"] if m["ca_leg_type"] == "CASH_DIVIDEND")

        result = svc.correct_movement(
            div_mvt["id"],
            _ACCOUNT_ID,
            {
                "account_id": _ACCOUNT_ID,
                "correction_note": "Fix trade date",
                "trade_date": "2024-03-29",
                "notes": "Updated settlement date",
            },
        )
        replacement = result["replacement"]
        assert replacement["trade_date"] == "2024-03-29"
        # CA group fields must be preserved on the replacement
        assert replacement["ca_group_id"] == create_result["ca_group_id"]
        assert replacement["ca_leg_type"] == "CASH_DIVIDEND"
        assert replacement["ca_event_type"] == "DIVIDEND_WITH_SCRIP"

    def test_ht7_non_financial_replacement_has_ca_group_seq(self, svc):
        """H-T7: ca_group_seq inherited when correcting a CA leg non-financially."""
        create_result = svc.create_corporate_action(_CA_4_LEGS)
        div_mvt = next(m for m in create_result["movements"] if m["ca_leg_type"] == "CASH_DIVIDEND")
        original_seq = div_mvt["ca_group_seq"]

        result = svc.correct_movement(
            div_mvt["id"],
            _ACCOUNT_ID,
            {
                "account_id": _ACCOUNT_ID,
                "correction_note": "Minor date fix",
                "trade_date": "2024-03-29",
            },
        )
        assert result["replacement"]["ca_group_seq"] == original_seq


# ---------------------------------------------------------------------------
# H-T8: Void entire group
# ---------------------------------------------------------------------------

class TestVoidCorporateActionGroup:
    def test_ht8_void_all_legs(self, svc):
        """H-T8: void_corporate_action_group voids all 4 active legs."""
        create_result = svc.create_corporate_action(_CA_4_LEGS)
        group_id = create_result["ca_group_id"]

        void_result = svc.void_corporate_action_group(group_id, _ACCOUNT_ID, "test void")
        assert void_result["ca_group_id"] == group_id
        assert void_result["voided_count"] == 4
        for doc in void_result["movements"]:
            assert doc["correction_status"] == "VOIDED"

    def test_ht8_void_with_reason(self, svc):
        """H-T8: void reason stored on voided docs."""
        create_result = svc.create_corporate_action(_CA_4_LEGS)
        group_id = create_result["ca_group_id"]
        void_result = svc.void_corporate_action_group(group_id, _ACCOUNT_ID, "data error")
        for doc in void_result["movements"]:
            assert doc.get("void_reason") == "data error"

    def test_ht8_void_nonexistent_group_raises(self, svc):
        """H-T8: void on unknown ca_group_id raises ValueError with no_active_legs."""
        with pytest.raises(ValueError, match="no_active_legs"):
            svc.void_corporate_action_group("cag_doesnotexist", _ACCOUNT_ID)

    def test_ht8_already_voided_legs_not_returned(self, svc):
        """H-T8: Voiding already-voided group returns no active legs → ValueError."""
        create_result = svc.create_corporate_action(_CA_4_LEGS)
        group_id = create_result["ca_group_id"]
        svc.void_corporate_action_group(group_id, _ACCOUNT_ID)
        # Second void should find no active legs
        with pytest.raises(ValueError, match="no_active_legs"):
            svc.void_corporate_action_group(group_id, _ACCOUNT_ID)


# ---------------------------------------------------------------------------
# H-W: WHT rate derivation (server-authoritative)
# ---------------------------------------------------------------------------

class TestWhtRateDerivation:
    def test_hw1_rate_pct_derived_on_create(self, svc):
        """H-W1: rate_pct is derived server-side (amount_eur/gross_eur*100) on create."""
        req = {
            "event_type": "DIVIDEND_WITH_SCRIP",
            "security_id": _SECURITY_ID,
            "account_id": _ACCOUNT_ID,
            "payment_date": "2024-03-28",
            "legs": [
                {
                    "leg_type": "CASH_DIVIDEND",
                    "trade_date": "2024-03-28",
                    "gross": {"amount": "200.00", "currency": "EUR", "eur_amount": "200.00"},
                    "withholding": {
                        "source": {"country": "GB", "amount_eur": "0", "rate_pct": "999"},
                        "destination": {"country": "ES", "amount_eur": "30.00", "rate_pct": "999"},
                    },
                },
                {
                    "leg_type": "SHARE_ACQUISITION",
                    "trade_date": "2024-03-28",
                    "quantity": "5",
                    "gross": {"amount": "100.00", "currency": "EUR", "eur_amount": "100.00"},
                    "cost_basis_status": "INCOMPLETE",
                },
            ],
        }
        result = svc.create_corporate_action(req)
        div = next(m for m in result["movements"] if m["ca_leg_type"] == "CASH_DIVIDEND")
        dest_wht = div["withholding"]["destination"]
        # 30/200*100 = 15.00 — client-sent "999" must be overwritten
        assert dest_wht["rate_pct"] == "15.00"

    def test_hw2_rate_pct_derived_on_correct(self, svc):
        """H-W2: rate_pct derived after withholding override in correction."""
        # Create standalone DIVIDEND first
        doc = svc.create_manual_movement({
            "txn_type": "DIVIDEND",
            "security_id": _SECURITY_ID,
            "trade_date": "2024-03-28",
            "account_id": _ACCOUNT_ID,
            "gross": {"amount": "200.00", "currency": "EUR", "eur_amount": "200.00"},
            "withholding": {
                "source": {"country": "GB", "amount_eur": "0"},
                "destination": {"country": "ES", "amount_eur": "20.00"},
            },
        })
        correction = svc.correct_movement(
            doc["id"],
            _ACCOUNT_ID,
            {
                "account_id": _ACCOUNT_ID,
                "correction_note": "Fix withholding",
                "withholding": {
                    "source": {"country": "GB", "amount_eur": "0", "rate_pct": "999"},
                    "destination": {"country": "ES", "amount_eur": "30.00", "rate_pct": "999"},
                },
            },
        )
        dest = correction["replacement"]["withholding"]["destination"]
        # 30/200*100 = 15.00
        assert dest["rate_pct"] == "15.00"

    def test_hw3_zero_gross_rate_is_zero(self, svc):
        """H-W3: When gross_eur=0, rate_pct=0 (no ZeroDivision)."""
        req = {
            "event_type": "DIVIDEND_WITH_SCRIP",
            "security_id": _SECURITY_ID,
            "account_id": _ACCOUNT_ID,
            "payment_date": "2024-03-28",
            "legs": [
                {
                    "leg_type": "CASH_DIVIDEND",
                    "trade_date": "2024-03-28",
                    "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
                    "withholding": {
                        "destination": {"country": "ES", "amount_eur": "0"},
                    },
                },
                {
                    "leg_type": "SHARE_ACQUISITION",
                    "trade_date": "2024-03-28",
                    "quantity": "5",
                    "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
                    "cost_basis_status": "INCOMPLETE",
                },
            ],
        }
        result = svc.create_corporate_action(req)
        div = next(m for m in result["movements"] if m["ca_leg_type"] == "CASH_DIVIDEND")
        dest_wht = div["withholding"]["destination"]
        assert dest_wht["rate_pct"] == "0"

    def test_hw1_create_manual_dividend_derives_rate(self, svc):
        """H-W1 extended: create_manual_movement also derives rate_pct."""
        doc = svc.create_manual_movement({
            "txn_type": "DIVIDEND",
            "security_id": _SECURITY_ID,
            "trade_date": "2024-03-28",
            "account_id": _ACCOUNT_ID,
            "gross": {"amount": "100.00", "currency": "EUR", "eur_amount": "100.00"},
            "withholding": {
                "source": {"country": "GB", "amount_eur": "0", "rate_pct": "0"},
                "destination": {"country": "ES", "amount_eur": "15.00", "rate_pct": "999"},
            },
        })
        dest = doc["withholding"]["destination"]
        assert dest["rate_pct"] == "15.00"


# ---------------------------------------------------------------------------
# H-21: Existing standalone movements unaffected
# ---------------------------------------------------------------------------

class TestStandaloneMovementsUnaffected:
    def test_h21_standalone_buy_has_no_ca_fields(self, svc):
        """H-21: Standalone BUY created without CA context has no ca_group_id."""
        doc = svc.create_manual_movement({
            "txn_type": "BUY",
            "security_id": _SECURITY_ID,
            "trade_date": "2024-01-10",
            "account_id": _ACCOUNT_ID,
            "quantity": "5",
            "gross": {"amount": "500.00", "currency": "EUR", "eur_amount": "500.00"},
        })
        assert "ca_group_id" not in doc
        assert "ca_leg_type" not in doc

    def test_h21_standalone_sell_correct_works(self, svc):
        """H-21: Correcting standalone SELL works as before (no CA fields involved)."""
        doc = svc.create_manual_movement({
            "txn_type": "SELL",
            "security_id": _SECURITY_ID,
            "trade_date": "2024-06-20",
            "account_id": _ACCOUNT_ID,
            "quantity": "3",
            "gross": {"amount": "600.00", "currency": "EUR", "eur_amount": "600.00"},
        })
        result = svc.correct_movement(
            doc["id"], _ACCOUNT_ID,
            {"account_id": _ACCOUNT_ID, "correction_note": "fix qty", "quantity": "4"},
        )
        assert result["replacement"]["quantity"] == "4"
        assert result["original"]["correction_status"] == "SUPERSEDED"


# ---------------------------------------------------------------------------
# H-T11 through H-T17: Group correction (POST .../correct)
# ---------------------------------------------------------------------------

# Note: this fixture uses DIVIDEND_WITH_SCRIP (2 legs), not the single-leg
# CASH_DIVIDEND event type, because CASH_DIVIDEND is never a "group" (see
# create_corporate_action: single-leg CASH_DIVIDEND gets no ca_group_id and
# is a plain movement). These tests exercise generic group-correction
# mechanics, so they need a genuinely multi-leg fixture.
_CA_SIMPLE = {
    "event_type": "DIVIDEND_WITH_SCRIP",
    "security_id": _SECURITY_ID,
    "account_id": _ACCOUNT_ID,
    "payment_date": "2024-03-28",
    "legs": [
        {
            "leg_type": "CASH_DIVIDEND",
            "trade_date": "2024-03-28",
            "gross": {"amount": "200.00", "currency": "EUR", "eur_amount": "200.00"},
            "withholding": {
                "destination": {"country": "ES", "amount_eur": "40.00", "rate_pct": "999"},
            },
        },
        {
            "leg_type": "SHARE_ACQUISITION",
            "trade_date": "2024-03-28",
            "quantity": "5",
            "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
            "cost_basis_status": "INCOMPLETE",
        },
    ],
}

_CA_SIMPLE_LEGS_FULL = [
    {
        "leg_type": "CASH_DIVIDEND",
        "trade_date": "2024-03-28",
        "gross": {"amount": "210.00", "currency": "EUR", "eur_amount": "210.00"},
    },
    {
        "leg_type": "SHARE_ACQUISITION",
        "trade_date": "2024-03-28",
        "quantity": "5",
        "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
        "cost_basis_status": "INCOMPLETE",
    },
]


class TestGroupCorrection:
    def _create_group(self, svc):
        return svc.create_corporate_action(_CA_SIMPLE)

    def test_ht11_new_group_created(self, svc):
        """H-T11: Correction creates a new ca_group_id distinct from original."""
        original = self._create_group(svc)
        orig_id = original["ca_group_id"]
        result = svc.correct_corporate_action_group(orig_id, {
            "account_id": _ACCOUNT_ID,
            "correction_note": "Fix gross amount",
            "event_type": "DIVIDEND_WITH_SCRIP",
            "legs": _CA_SIMPLE_LEGS_FULL,
        })
        assert result["ca_group_id"] != orig_id
        assert result["original_ca_group_id"] == orig_id
        assert result["ca_group_id"].startswith("cag_")

    def test_ht12_replacement_links_to_original(self, svc):
        """H-T12: New legs carry replaces_ca_group_id = original ca_group_id."""
        original = self._create_group(svc)
        orig_id = original["ca_group_id"]
        result = svc.correct_corporate_action_group(orig_id, {
            "account_id": _ACCOUNT_ID,
            "correction_note": "Fix amount",
            "event_type": "DIVIDEND_WITH_SCRIP",
            "legs": _CA_SIMPLE_LEGS_FULL,
        })
        for mvt in result["movements"]:
            assert mvt["replaces_ca_group_id"] == orig_id

    def test_ht13_originals_superseded(self, svc):
        """H-T13: All original legs have correction_status=SUPERSEDED."""
        original = self._create_group(svc)
        orig_id = original["ca_group_id"]
        new_group_id = svc.correct_corporate_action_group(orig_id, {
            "account_id": _ACCOUNT_ID,
            "correction_note": "Fix amount",
            "event_type": "DIVIDEND_WITH_SCRIP",
            "legs": _CA_SIMPLE_LEGS_FULL,
        })["ca_group_id"]

        # Query all docs in the fake store and check the originals
        orig_leg = original["movements"][0]
        stored = svc.portfolio_container.read_item(
            item=orig_leg["id"], partition_key=_ACCOUNT_ID
        )
        assert stored["correction_status"] == "SUPERSEDED"
        assert stored["superseded_by_ca_group_id"] == new_group_id

    def test_ht14_correction_note_required(self, svc):
        """H-T14: Empty correction_note raises ValueError."""
        original = self._create_group(svc)
        with pytest.raises(ValueError, match="correction_note is required"):
            svc.correct_corporate_action_group(original["ca_group_id"], {
                "account_id": _ACCOUNT_ID,
                "correction_note": "   ",
                "event_type": "DIVIDEND_WITH_SCRIP",
                "legs": _CA_SIMPLE_LEGS_FULL,
            })

    def test_ht15_no_active_legs_raises(self, svc):
        """H-T15: Non-existent ca_group_id raises no_active_legs."""
        with pytest.raises(ValueError, match="no_active_legs"):
            svc.correct_corporate_action_group("cag_nonexistent", {
                "account_id": _ACCOUNT_ID,
                "correction_note": "X",
                "event_type": "CASH_DIVIDEND",
                "legs": [
                    {
                        "leg_type": "CASH_DIVIDEND",
                        "trade_date": "2024-03-28",
                        "gross": {"amount": "1.00", "currency": "EUR", "eur_amount": "1.00"},
                    },
                ],
            })

    def test_ht16_invalid_event_type_rejected(self, svc):
        """H-T16: Unknown event_type rejected before any write."""
        original = self._create_group(svc)
        with pytest.raises(ValueError, match="event_type must be one of"):
            svc.correct_corporate_action_group(original["ca_group_id"], {
                "account_id": _ACCOUNT_ID,
                "correction_note": "Fix",
                "event_type": "TOTALLY_MADE_UP",
                "legs": [
                    {
                        "leg_type": "CASH_DIVIDEND",
                        "trade_date": "2024-03-28",
                        "gross": {"amount": "1.00", "currency": "EUR", "eur_amount": "1.00"},
                    },
                ],
            })

    def test_ht17_missing_required_leg_rejected(self, svc):
        """H-T17: DIVIDEND_WITH_SCRIP without SHARE_ACQUISITION leg is rejected."""
        # Create a 2-leg group first
        big_group = svc.create_corporate_action({
            "event_type": "DIVIDEND_WITH_SCRIP",
            "security_id": _SECURITY_ID,
            "account_id": _ACCOUNT_ID,
            "payment_date": "2024-03-28",
            "legs": [
                {
                    "leg_type": "CASH_DIVIDEND",
                    "trade_date": "2024-03-28",
                    "gross": {"amount": "200.00", "currency": "EUR", "eur_amount": "200.00"},
                },
                {
                    "leg_type": "SHARE_ACQUISITION",
                    "trade_date": "2024-03-28",
                    "quantity": "5",
                    "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
                    "cost_basis_status": "INCOMPLETE",
                },
            ],
        })
        with pytest.raises(ValueError, match="requires leg types"):
            svc.correct_corporate_action_group(big_group["ca_group_id"], {
                "account_id": _ACCOUNT_ID,
                "correction_note": "Fix",
                "event_type": "DIVIDEND_WITH_SCRIP",
                "legs": [
                    # Only CASH_DIVIDEND supplied — SHARE_ACQUISITION missing
                    {
                        "leg_type": "CASH_DIVIDEND",
                        "trade_date": "2024-03-28",
                        "gross": {"amount": "210.00", "currency": "EUR", "eur_amount": "210.00"},
                    },
                ],
            })

    def test_ht18_wht_rate_derived_in_correction(self, svc):
        """H-T18: rate_pct is server-derived in correction (client value ignored)."""
        original = self._create_group(svc)
        orig_id = original["ca_group_id"]
        result = svc.correct_corporate_action_group(orig_id, {
            "account_id": _ACCOUNT_ID,
            "correction_note": "Fix WHT",
            "event_type": "DIVIDEND_WITH_SCRIP",
            "legs": [
                {
                    "leg_type": "CASH_DIVIDEND",
                    "trade_date": "2024-03-28",
                    "gross": {"amount": "100.00", "currency": "EUR", "eur_amount": "100.00"},
                    "withholding": {
                        "destination": {"country": "ES", "amount_eur": "15.00", "rate_pct": "999"},
                    },
                },
                {
                    "leg_type": "SHARE_ACQUISITION",
                    "trade_date": "2024-03-28",
                    "quantity": "5",
                    "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
                    "cost_basis_status": "INCOMPLETE",
                },
            ],
        })
        div = next(m for m in result["movements"] if m["ca_leg_type"] == "CASH_DIVIDEND")
        assert div["withholding"]["destination"]["rate_pct"] == "15.00"


# ---------------------------------------------------------------------------
# H-G1: Guard — financial correction of a group leg rejected via correct_movement
# ---------------------------------------------------------------------------

class TestGroupLegFinancialGuard:
    def test_hg1_financial_field_blocked(self, svc):
        """H-G1: correct_movement on a group leg with financial field raises group_leg_correction_required."""
        group = svc.create_corporate_action(_CA_SIMPLE)
        leg = group["movements"][0]
        with pytest.raises(ValueError, match="group_leg_correction_required"):
            svc.correct_movement(
                leg["id"],
                _ACCOUNT_ID,
                {
                    "account_id": _ACCOUNT_ID,
                    "correction_note": "should be blocked",
                    "gross": {"amount": "999.00", "currency": "EUR", "eur_amount": "999.00"},
                },
            )

    def test_hg2_non_financial_field_allowed(self, svc):
        """H-G2: correct_movement on a group leg with only notes is allowed."""
        group = svc.create_corporate_action(_CA_SIMPLE)
        leg = group["movements"][0]
        result = svc.correct_movement(
            leg["id"],
            _ACCOUNT_ID,
            {
                "account_id": _ACCOUNT_ID,
                "correction_note": "Add note",
                "notes": "Updated annotation",
            },
        )
        assert result["replacement"]["notes"] == "Updated annotation"

    def test_hg3_trade_date_change_allowed(self, svc):
        """H-G3: correct_movement on a group leg with trade_date only is allowed."""
        group = svc.create_corporate_action(_CA_SIMPLE)
        leg = group["movements"][0]
        result = svc.correct_movement(
            leg["id"],
            _ACCOUNT_ID,
            {
                "account_id": _ACCOUNT_ID,
                "correction_note": "Fix date",
                "trade_date": "2024-04-01",
            },
        )
        assert result["replacement"]["trade_date"] == "2024-04-01"


# ---------------------------------------------------------------------------
# M-1: Model regression — CorporateActionCreateRequest and
#       CorporateActionCorrectRequest are distinct, properly validated classes.
# ---------------------------------------------------------------------------

class TestCorporateActionModels:
    """Regression: Danny flagged that CorporateActionCreateRequest was missing its
    class declaration, causing its fields to merge into CorporateActionCorrectRequest.
    These tests ensure both are distinct, importable, and enforce their own validators.
    """

    _LEG = {
        "leg_type": "CASH_DIVIDEND",
        "trade_date": "2024-03-28",
        "gross": {"amount": "100", "currency": "EUR", "eur_amount": "100"},
    }

    def test_m1_create_request_instantiates(self):
        """M-1a: CorporateActionCreateRequest accepts valid input."""
        from src.portfolio.models import CorporateActionCreateRequest, CorporateActionLegCreate
        req = CorporateActionCreateRequest(
            event_type="CASH_DIVIDEND",
            security_id="XLON:ULVR",
            account_id="acct_main",
            payment_date="2024-03-28",
            legs=[CorporateActionLegCreate(**self._LEG)],
        )
        assert req.event_type == "CASH_DIVIDEND"
        assert req.security_id == "XLON:ULVR"
        assert req.payment_date == "2024-03-28"
        assert len(req.legs) == 1

    def test_m1_create_has_no_correction_note_field(self):
        """M-1b: CorporateActionCreateRequest does NOT have correction_note."""
        from src.portfolio.models import CorporateActionCreateRequest
        import inspect
        fields = CorporateActionCreateRequest.model_fields
        assert "correction_note" not in fields, (
            "CorporateActionCreateRequest must not carry correction_note"
        )

    def test_m1_create_requires_security_id(self):
        """M-1c: security_id is required on Create (not Optional)."""
        from src.portfolio.models import CorporateActionCreateRequest, CorporateActionLegCreate
        import pytest as _pytest
        with _pytest.raises(Exception):
            CorporateActionCreateRequest(
                event_type="CASH_DIVIDEND",
                payment_date="2024-03-28",
                legs=[CorporateActionLegCreate(**self._LEG)],
                # security_id omitted — must fail
            )

    def test_m1_create_rejects_bad_event_type(self):
        """M-1d: CorporateActionCreateRequest validates event_type enum."""
        from src.portfolio.models import CorporateActionCreateRequest, CorporateActionLegCreate
        import pytest as _pytest
        with _pytest.raises(Exception, match="event_type must be one of"):
            CorporateActionCreateRequest(
                event_type="NOT_REAL",
                security_id="XLON:ULVR",
                payment_date="2024-03-28",
                legs=[CorporateActionLegCreate(**self._LEG)],
            )

    def test_m1_create_rejects_bad_payment_date(self):
        """M-1e: CorporateActionCreateRequest validates payment_date format."""
        from src.portfolio.models import CorporateActionCreateRequest, CorporateActionLegCreate
        import pytest as _pytest
        with _pytest.raises(Exception, match="payment_date must be YYYY-MM-DD"):
            CorporateActionCreateRequest(
                event_type="CASH_DIVIDEND",
                security_id="XLON:ULVR",
                payment_date="28-03-2024",
                legs=[CorporateActionLegCreate(**self._LEG)],
            )

    def test_m1_correct_request_instantiates(self):
        """M-1f: CorporateActionCorrectRequest accepts valid input."""
        from src.portfolio.models import CorporateActionCorrectRequest, CorporateActionLegCreate
        req = CorporateActionCorrectRequest(
            account_id="acct_main",
            correction_note="Fix gross",
            event_type="CASH_DIVIDEND",
            legs=[CorporateActionLegCreate(**self._LEG)],
        )
        assert req.correction_note == "Fix gross"
        assert req.security_id is None  # optional — inferred server-side

    def test_m1_correct_has_no_required_security_id(self):
        """M-1g: security_id is Optional on Correct (inferred from original)."""
        from src.portfolio.models import CorporateActionCorrectRequest
        field = CorporateActionCorrectRequest.model_fields["security_id"]
        assert not field.is_required(), "security_id must be optional on CorrectRequest"

    def test_m1_correct_rejects_blank_note(self):
        """M-1h: correction_note whitespace-only is rejected."""
        from src.portfolio.models import CorporateActionCorrectRequest, CorporateActionLegCreate
        import pytest as _pytest
        with _pytest.raises(Exception, match="correction_note must not be empty"):
            CorporateActionCorrectRequest(
                account_id="acct_main",
                correction_note="   ",
                event_type="CASH_DIVIDEND",
                legs=[CorporateActionLegCreate(**self._LEG)],
            )

    def test_m1_correct_rejects_empty_legs(self):
        """M-1i: empty legs list is rejected on CorrectRequest."""
        from src.portfolio.models import CorporateActionCorrectRequest
        import pytest as _pytest
        with _pytest.raises(Exception, match="legs must not be empty"):
            CorporateActionCorrectRequest(
                account_id="acct_main",
                correction_note="Fix",
                event_type="CASH_DIVIDEND",
                legs=[],
            )

    def test_m1_classes_are_distinct(self):
        """M-1j: The two model classes are genuinely separate (not the same object)."""
        from src.portfolio.models import (
            CorporateActionCreateRequest,
            CorporateActionCorrectRequest,
        )
        assert CorporateActionCreateRequest is not CorporateActionCorrectRequest
        assert CorporateActionCreateRequest.__name__ == "CorporateActionCreateRequest"
        assert CorporateActionCorrectRequest.__name__ == "CorporateActionCorrectRequest"


# ---------------------------------------------------------------------------
# HD-1: Hard delete — individual movements and CA groups
# ---------------------------------------------------------------------------

class TestHardDelete:
    """Hard-delete tests: DELETE /movements/{id} and DELETE /ca-groups/{id}."""

    def test_hd1_delete_active_movement(self, svc):
        """HD-1a: Hard-delete an ACTIVE movement removes it permanently."""
        doc = svc.create_manual_movement({
            "txn_type": "BUY", "security_id": _SECURITY_ID,
            "trade_date": "2024-01-10", "account_id": _ACCOUNT_ID,
            "quantity": "5",
            "gross": {"amount": "500.00", "currency": "EUR", "eur_amount": "500.00"},
        })
        result = svc.delete_movement(doc["id"], _ACCOUNT_ID)
        assert result == {"deleted": True, "id": doc["id"]}
        assert svc.get_movement(doc["id"], _ACCOUNT_ID) is None

    def test_hd2_delete_voided_movement(self, svc):
        """HD-2: Hard-delete also works on VOIDED movements."""
        doc = svc.create_manual_movement({
            "txn_type": "BUY", "security_id": _SECURITY_ID,
            "trade_date": "2024-01-10", "account_id": _ACCOUNT_ID,
            "quantity": "3",
            "gross": {"amount": "300.00", "currency": "EUR", "eur_amount": "300.00"},
        })
        svc.soft_delete_movement(doc["id"], _ACCOUNT_ID)
        result = svc.delete_movement(doc["id"], _ACCOUNT_ID)
        assert result["deleted"] is True

    def test_hd3_delete_not_found_raises(self, svc):
        """HD-3: Deleting a non-existent movement raises LookupError."""
        with pytest.raises(LookupError):
            svc.delete_movement("mvt_nonexistent", _ACCOUNT_ID)

    def test_hd4_group_leg_rejected(self, svc):
        """HD-4: Trying to hard-delete a CA group leg individually raises ValueError."""
        group = svc.create_corporate_action(_CA_SIMPLE)
        leg = group["movements"][0]
        with pytest.raises(ValueError, match="group_leg_hard_delete_required"):
            svc.delete_movement(leg["id"], _ACCOUNT_ID)

    def test_hd5_delete_ca_group(self, svc):
        """HD-5: Deleting a CA group removes all its legs."""
        group = svc.create_corporate_action(_CA_SIMPLE)
        ca_id = group["ca_group_id"]
        leg_ids = [m["id"] for m in group["movements"]]
        result = svc.delete_corporate_action_group(ca_id, _ACCOUNT_ID)
        assert result["deleted_count"] == len(leg_ids)
        assert set(result["ids"]) == set(leg_ids)
        for lid in leg_ids:
            assert svc.get_movement(lid, _ACCOUNT_ID) is None

    def test_hd6_delete_ca_group_all_statuses(self, svc):
        """HD-6: Group delete removes legs regardless of correction_status (ACTIVE+SUPERSEDED)."""
        group = svc.create_corporate_action(_CA_SIMPLE)
        ca_id = group["ca_group_id"]
        leg = group["movements"][0]
        # Make a non-financial correction so one leg becomes SUPERSEDED
        svc.correct_movement(leg["id"], _ACCOUNT_ID, {
            "account_id": _ACCOUNT_ID,
            "correction_note": "fix date",
            "trade_date": "2024-04-01",
        })
        # Group now has original (SUPERSEDED) + replacement (ACTIVE)
        result = svc.delete_corporate_action_group(ca_id, _ACCOUNT_ID)
        assert result["deleted_count"] >= 2

    def test_hd7_delete_ca_group_not_found(self, svc):
        """HD-7: Deleting a non-existent CA group raises ValueError with no_legs_found."""
        with pytest.raises(ValueError, match="no_legs_found"):
            svc.delete_corporate_action_group("cag_nonexistent", _ACCOUNT_ID)


# ---------------------------------------------------------------------------
# Single-leg CASH_DIVIDEND is never a group (no ca_group_id)
# ---------------------------------------------------------------------------

_CA_CASH_DIVIDEND_ONLY = {
    "event_type": "CASH_DIVIDEND",
    "security_id": _SECURITY_ID,
    "account_id": _ACCOUNT_ID,
    "payment_date": "2024-03-28",
    "legs": [
        {
            "leg_type": "CASH_DIVIDEND",
            "trade_date": "2024-03-28",
            "gross": {"amount": "200.00", "currency": "EUR", "eur_amount": "200.00"},
            "withholding": {
                "destination": {"country": "ES", "amount_eur": "40.00", "rate_pct": "999"},
            },
        },
    ],
}


class TestSingleLegCashDividendUngrouped:
    def test_no_ca_group_id_assigned(self, svc):
        """A single-leg CASH_DIVIDEND is created without ca_group_id/ca_leg_type/
        ca_event_type/ca_group_seq — it is a plain standalone movement."""
        result = svc.create_corporate_action(_CA_CASH_DIVIDEND_ONLY)
        assert result["ca_group_id"] is None
        mvt = result["movements"][0]
        assert mvt.get("ca_group_id") is None
        assert mvt.get("ca_leg_type") is None
        assert mvt.get("ca_event_type") is None
        assert mvt.get("ca_group_seq") is None

    def test_deletable_via_normal_delete_movement(self, svc):
        """A single-leg CASH_DIVIDEND is hard-deletable via delete_movement,
        without the group_leg_hard_delete_required guard."""
        result = svc.create_corporate_action(_CA_CASH_DIVIDEND_ONLY)
        mvt = result["movements"][0]
        delete_result = svc.delete_movement(mvt["id"], _ACCOUNT_ID)
        assert delete_result["deleted"] is True
        assert svc.get_movement(mvt["id"], _ACCOUNT_ID) is None

    def test_correctable_via_normal_correct_movement(self, svc):
        """A single-leg CASH_DIVIDEND is correctable via correct_movement (including
        financial fields), without the group_leg_correction_required guard."""
        result = svc.create_corporate_action(_CA_CASH_DIVIDEND_ONLY)
        mvt = result["movements"][0]
        corrected = svc.correct_movement(mvt["id"], _ACCOUNT_ID, {
            "account_id": _ACCOUNT_ID,
            "correction_note": "Fix gross amount",
            "gross": {"amount": "210.00", "currency": "EUR", "eur_amount": "210.00"},
        })
        assert corrected["replacement"]["gross"]["eur_amount"] == "210.00"
        assert corrected["original"]["correction_status"] == "SUPERSEDED"
