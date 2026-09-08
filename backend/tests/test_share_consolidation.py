"""SHARE_CONSOLIDATION corporate-action operation tests.

Contract: .squad/decisions/inbox/danny-share-consolidation-contract.md (Danny, 2026-09-08)

Covers §5.1 (SC-T1..SC-T10) service-layer tests for the reusable SHARE_CONSOLIDATION
event type:
- SC-T1: Basic consolidation without fractional (2 legs)
- SC-T2: Consolidation with fractional cash-out (3 legs)
- SC-T3: Missing CONSOLIDATION_OUT → rejected
- SC-T4: Missing CONSOLIDATION_IN → rejected
- SC-T5: CONSOLIDATION_IN without transfer_cost_basis_eur → rejected
- SC-T6: Void consolidation group
- SC-T7: Correct consolidation group
- SC-T8: FRACTIONAL_CASH_OUT sales_type is always ACCIONES
- SC-T9: ca_group_seq ordering
- SC-T10: Event type validation rejects SHARE_CONSOLIDATION with dividend legs

No RKT movements, no production data changes — all tests run against FakeCosmos.
"""

from __future__ import annotations

import pytest

from src.portfolio.cosmos_portfolio import CosmosPortfolioService
from tests.conftest_portfolio_p2 import FakeCosmos


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

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


_SECURITY_ID = "XLON:TESTCO"
_ACCOUNT_ID = "heytrade_main"

_SC_2_LEGS = {
    "event_type": "SHARE_CONSOLIDATION",
    "security_id": _SECURITY_ID,
    "account_id": _ACCOUNT_ID,
    "payment_date": "2024-05-01",
    "notes": "Test 1:2 consolidation (24/25 style ratio informational only)",
    "legs": [
        {
            "leg_type": "CONSOLIDATION_OUT",
            "trade_date": "2024-05-01",
            "quantity": "100",
            "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
        },
        {
            "leg_type": "CONSOLIDATION_IN",
            "trade_date": "2024-05-01",
            "quantity": "80",
            "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
            "transfer_cost_basis_eur": "5000.00",
        },
    ],
}

_SC_3_LEGS = {
    "event_type": "SHARE_CONSOLIDATION",
    "security_id": _SECURITY_ID,
    "account_id": _ACCOUNT_ID,
    "payment_date": "2024-05-01",
    "legs": [
        {
            "leg_type": "CONSOLIDATION_OUT",
            "trade_date": "2024-05-01",
            "quantity": "72",
            "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
        },
        {
            "leg_type": "CONSOLIDATION_IN",
            "trade_date": "2024-05-01",
            "quantity": "69.12",
            "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
            "transfer_cost_basis_eur": "4142.06",
        },
        {
            "leg_type": "FRACTIONAL_CASH_OUT",
            "trade_date": "2024-05-01",
            "quantity": "0.12",
            "gross": {"amount": "8.58", "currency": "EUR", "eur_amount": "8.58"},
        },
    ],
}


# ---------------------------------------------------------------------------
# SC-T1: Basic consolidation without fractional (2 legs)
# ---------------------------------------------------------------------------

class TestBasicConsolidation:
    def test_sct1_two_movements_shared_group_id(self, svc):
        result = svc.create_corporate_action(_SC_2_LEGS)
        assert "ca_group_id" in result
        assert result["ca_group_id"].startswith("cag_")
        assert result["event_type"] == "SHARE_CONSOLIDATION"
        assert len(result["movements"]) == 2
        group_id = result["ca_group_id"]
        for mvt in result["movements"]:
            assert mvt["ca_group_id"] == group_id

    def test_sct1_consolidation_out_shape(self, svc):
        result = svc.create_corporate_action(_SC_2_LEGS)
        out_leg = next(m for m in result["movements"] if m["ca_leg_type"] == "CONSOLIDATION_OUT")
        assert out_leg["txn_type"] == "TRANSFER_OUT"
        assert out_leg["quantity"] == "100"

    def test_sct1_consolidation_in_shape(self, svc):
        result = svc.create_corporate_action(_SC_2_LEGS)
        in_leg = next(m for m in result["movements"] if m["ca_leg_type"] == "CONSOLIDATION_IN")
        assert in_leg["txn_type"] == "TRANSFER_IN"
        assert in_leg["quantity"] == "80"
        assert in_leg["transfer_cost_basis_eur"] == "5000.00"


# ---------------------------------------------------------------------------
# SC-T2: Consolidation with fractional cash-out (3 legs)
# ---------------------------------------------------------------------------

class TestFractionalConsolidation:
    def test_sct2_three_movements_shared_group_id(self, svc):
        result = svc.create_corporate_action(_SC_3_LEGS)
        assert len(result["movements"]) == 3
        group_id = result["ca_group_id"]
        for mvt in result["movements"]:
            assert mvt["ca_group_id"] == group_id

    def test_sct2_fractional_cash_out_shape(self, svc):
        result = svc.create_corporate_action(_SC_3_LEGS)
        fco = next(m for m in result["movements"] if m["ca_leg_type"] == "FRACTIONAL_CASH_OUT")
        assert fco["txn_type"] == "SELL"
        assert fco["sales_type"] == "ACCIONES"
        assert fco["quantity"] == "0.12"


# ---------------------------------------------------------------------------
# SC-T3 / SC-T4: Missing required legs → rejected
# ---------------------------------------------------------------------------

class TestRequiredLegs:
    def test_sct3_missing_consolidation_out_rejected(self, svc):
        req = {
            "event_type": "SHARE_CONSOLIDATION",
            "security_id": _SECURITY_ID,
            "account_id": _ACCOUNT_ID,
            "payment_date": "2024-05-01",
            "legs": [
                {
                    "leg_type": "CONSOLIDATION_IN",
                    "trade_date": "2024-05-01",
                    "quantity": "80",
                    "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
                    "transfer_cost_basis_eur": "5000.00",
                },
            ],
        }
        with pytest.raises(ValueError, match="requires leg types"):
            svc.create_corporate_action(req)

    def test_sct4_missing_consolidation_in_rejected(self, svc):
        req = {
            "event_type": "SHARE_CONSOLIDATION",
            "security_id": _SECURITY_ID,
            "account_id": _ACCOUNT_ID,
            "payment_date": "2024-05-01",
            "legs": [
                {
                    "leg_type": "CONSOLIDATION_OUT",
                    "trade_date": "2024-05-01",
                    "quantity": "100",
                    "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
                },
            ],
        }
        with pytest.raises(ValueError, match="requires leg types"):
            svc.create_corporate_action(req)

    def test_sct10_dividend_legs_only_rejected(self, svc):
        """SC-T10: SHARE_CONSOLIDATION with only a CASH_DIVIDEND leg is rejected."""
        req = {
            "event_type": "SHARE_CONSOLIDATION",
            "security_id": _SECURITY_ID,
            "account_id": _ACCOUNT_ID,
            "payment_date": "2024-05-01",
            "legs": [
                {
                    "leg_type": "CASH_DIVIDEND",
                    "trade_date": "2024-05-01",
                    "gross": {"amount": "100.00", "currency": "EUR", "eur_amount": "100.00"},
                },
            ],
        }
        with pytest.raises(ValueError, match="requires leg types"):
            svc.create_corporate_action(req)


# ---------------------------------------------------------------------------
# SC-T5: CONSOLIDATION_IN without transfer_cost_basis_eur → rejected
# ---------------------------------------------------------------------------

class TestTransferCostBasisRequired:
    def test_sct5_missing_transfer_cost_basis_rejected(self, svc):
        req = {
            "event_type": "SHARE_CONSOLIDATION",
            "security_id": _SECURITY_ID,
            "account_id": _ACCOUNT_ID,
            "payment_date": "2024-05-01",
            "legs": [
                {
                    "leg_type": "CONSOLIDATION_OUT",
                    "trade_date": "2024-05-01",
                    "quantity": "100",
                    "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
                },
                {
                    "leg_type": "CONSOLIDATION_IN",
                    "trade_date": "2024-05-01",
                    "quantity": "80",
                    "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
                    # transfer_cost_basis_eur omitted
                },
            ],
        }
        with pytest.raises(ValueError, match="transfer_cost_basis_eur"):
            svc.create_corporate_action(req)

    def test_sct5_no_partial_write_on_rejection(self, svc):
        """All-or-nothing: rejected CONSOLIDATION_IN leaves no docs written."""
        req = {
            "event_type": "SHARE_CONSOLIDATION",
            "security_id": _SECURITY_ID,
            "account_id": _ACCOUNT_ID,
            "payment_date": "2024-05-01",
            "legs": [
                {
                    "leg_type": "CONSOLIDATION_OUT",
                    "trade_date": "2024-05-01",
                    "quantity": "100",
                    "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
                },
                {
                    "leg_type": "CONSOLIDATION_IN",
                    "trade_date": "2024-05-01",
                    "quantity": "80",
                    "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
                },
            ],
        }
        initial_count = len(svc.portfolio_container._store)
        with pytest.raises(ValueError):
            svc.create_corporate_action(req)
        assert len(svc.portfolio_container._store) == initial_count


# ---------------------------------------------------------------------------
# SC-T6: Void consolidation group
# ---------------------------------------------------------------------------

class TestVoidConsolidation:
    def test_sct6_void_all_legs(self, svc):
        create_result = svc.create_corporate_action(_SC_3_LEGS)
        group_id = create_result["ca_group_id"]
        void_result = svc.void_corporate_action_group(group_id, _ACCOUNT_ID, "test void")
        assert void_result["voided_count"] == 3
        for doc in void_result["movements"]:
            assert doc["correction_status"] == "VOIDED"


# ---------------------------------------------------------------------------
# SC-T7: Correct consolidation group
# ---------------------------------------------------------------------------

class TestCorrectConsolidation:
    def test_sct7_correction_supersedes_originals_and_creates_replacement(self, svc):
        original = svc.create_corporate_action(_SC_2_LEGS)
        orig_id = original["ca_group_id"]

        result = svc.correct_corporate_action_group(orig_id, {
            "account_id": _ACCOUNT_ID,
            "correction_note": "Corrected quantities",
            "event_type": "SHARE_CONSOLIDATION",
            "legs": [
                {
                    "leg_type": "CONSOLIDATION_OUT",
                    "trade_date": "2024-05-01",
                    "quantity": "100",
                    "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
                },
                {
                    "leg_type": "CONSOLIDATION_IN",
                    "trade_date": "2024-05-01",
                    "quantity": "75",
                    "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
                    "transfer_cost_basis_eur": "5000.00",
                },
            ],
        })

        assert result["ca_group_id"] != orig_id
        assert result["original_ca_group_id"] == orig_id
        for mvt in result["movements"]:
            assert mvt["replaces_ca_group_id"] == orig_id

        in_leg = next(m for m in result["movements"] if m["ca_leg_type"] == "CONSOLIDATION_IN")
        assert in_leg["quantity"] == "75"

        # Originals are superseded
        orig_leg = original["movements"][0]
        stored = svc.portfolio_container.read_item(item=orig_leg["id"], partition_key=_ACCOUNT_ID)
        assert stored["correction_status"] == "SUPERSEDED"
        assert stored["superseded_by_ca_group_id"] == result["ca_group_id"]

    def test_sct7_correction_missing_transfer_cost_basis_rejected(self, svc):
        original = svc.create_corporate_action(_SC_2_LEGS)
        orig_id = original["ca_group_id"]
        with pytest.raises(ValueError, match="transfer_cost_basis_eur"):
            svc.correct_corporate_action_group(orig_id, {
                "account_id": _ACCOUNT_ID,
                "correction_note": "Bad correction",
                "event_type": "SHARE_CONSOLIDATION",
                "legs": [
                    {
                        "leg_type": "CONSOLIDATION_OUT",
                        "trade_date": "2024-05-01",
                        "quantity": "100",
                        "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
                    },
                    {
                        "leg_type": "CONSOLIDATION_IN",
                        "trade_date": "2024-05-01",
                        "quantity": "75",
                        "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
                    },
                ],
            })


# ---------------------------------------------------------------------------
# SC-T8: FRACTIONAL_CASH_OUT sales_type is always ACCIONES
# ---------------------------------------------------------------------------

class TestFractionalCashOutSalesType:
    def test_sct8_sales_type_always_acciones(self, svc):
        result = svc.create_corporate_action(_SC_3_LEGS)
        fco = next(m for m in result["movements"] if m["ca_leg_type"] == "FRACTIONAL_CASH_OUT")
        assert fco["sales_type"] == "ACCIONES"


# ---------------------------------------------------------------------------
# SC-T9: ca_group_seq ordering
# ---------------------------------------------------------------------------

class TestCaGroupSeqOrdering:
    def test_sct9_seq_matches_leg_order(self, svc):
        result = svc.create_corporate_action(_SC_3_LEGS)
        by_type = {m["ca_leg_type"]: m["ca_group_seq"] for m in result["movements"]}
        assert by_type["CONSOLIDATION_OUT"] == 1
        assert by_type["CONSOLIDATION_IN"] == 2
        assert by_type["FRACTIONAL_CASH_OUT"] == 3
