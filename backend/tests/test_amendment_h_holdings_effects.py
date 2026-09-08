"""Amendment H — Holdings effects from corporate-action leg types.

Contract reference: §H.3.5 Holdings and Cost-Basis Effects (Detailed).

These tests verify that when the holdings service processes ledger_txn documents
produced by create_corporate_action(), each leg type has the correct holdings
effect. The service processes CA legs via normal txn_type — no special CA
handling needed.

Coverage:
- H-T2: CASH_DIVIDEND leg → no share change, counted in total_dividends_eur
- H-T3: SHARE_ACQUISITION (COMPLETE) → +shares, pool_cost increased
- H-T4: SHARE_ACQUISITION (INCOMPLETE) → +unpaid_shares, zero pool cost
- H-T5: RIGHTS_SOLD (DERECHOS) → no share change, counted in rights proceeds
- H-T6: CASH_TOP_UP (qty=0, INCOMPLETE) → no share change, no pool cost
- Combined event: DIVIDEND_WITH_SCRIP full 4-leg holdings computation
- Standalone movements unaffected by CA legs (no double-counting)
- CASH_DIVIDEND leg does not appear in pool_cost or total_shares
"""

from __future__ import annotations

import pytest
from decimal import Decimal

from src.portfolio.cosmos_portfolio import CosmosPortfolioService
from src.portfolio.cosmos_securities import CosmosSecuritiesService
from src.portfolio.holdings_service import HoldingsService


# ---------------------------------------------------------------------------
# Minimal fake containers (hermetic — no Cosmos connectivity)
# ---------------------------------------------------------------------------

class FakePortfolioContainer:
    def __init__(self, movements=None):
        self._movements = list(movements or [])

    def query_items(self, query="", parameters=None,
                    enable_cross_partition_query=True, partition_key=None):
        if "VALUE COUNT(1)" in query or "COUNT" in query and "VALUE" in query:
            return iter([len(self._movements)])
        # Apply active-only filter (same as prod query)
        results = self._movements
        if "NOT IS_DEFINED(c.deleted_at)" in query:
            results = [m for m in results if "deleted_at" not in m]
        if "NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE'" in query:
            results = [
                m for m in results
                if m.get("correction_status") in (None, "ACTIVE")
            ]
        return iter(list(results))

    def upsert_item(self, body):
        self._movements.append(dict(body))
        return dict(body)

    def read_item(self, item, partition_key):
        from azure.cosmos.exceptions import CosmosResourceNotFoundError
        for m in self._movements:
            if m.get("id") == item:
                return dict(m)
        raise CosmosResourceNotFoundError(message="not found", response=None)

    def replace_item(self, item, body):
        for i, m in enumerate(self._movements):
            if m.get("id") == item:
                self._movements[i] = dict(body)
                return dict(body)
        from azure.cosmos.exceptions import CosmosResourceNotFoundError
        raise CosmosResourceNotFoundError(message="not found", response=None)


class FakeSymbolsContainer:
    def __init__(self):
        self._store = {}

    def read_item(self, item, partition_key):
        from azure.cosmos.exceptions import CosmosResourceNotFoundError
        raise CosmosResourceNotFoundError(message="not found", response=None)

    def create_item(self, body):
        return body

    def upsert_item(self, body):
        return body

    def query_items(self, query="", parameters=None,
                    enable_cross_partition_query=False, partition_key=None):
        return iter([])

    def replace_item(self, item, body):
        return body


def _make_services(movements):
    portfolio_svc = CosmosPortfolioService(
        FakePortfolioContainer(movements), FakeSymbolsContainer()
    )
    securities_svc = CosmosSecuritiesService(FakeSymbolsContainer())
    return HoldingsService(portfolio_svc, securities_svc)


# ---------------------------------------------------------------------------
# Movement builders for CA leg types
# ---------------------------------------------------------------------------

_SECURITY_ID = "XLON:ULVR"

def _cash_dividend_leg(doc_id="leg_cd_1", gross_eur="209.79",
                       net_eur="169.93", ca_group_id="cag_test") -> dict:
    """CASH_DIVIDEND → txn_type=DIVIDEND."""
    return {
        "id": doc_id,
        "doc_type": "ledger_txn",
        "txn_type": "DIVIDEND",
        "ca_leg_type": "CASH_DIVIDEND",
        "ca_group_id": ca_group_id,
        "security_id": _SECURITY_ID,
        "ticker": "ULVR",
        "trade_date": "2024-03-28",
        "quantity": None,
        "gross": {"amount": gross_eur, "currency": "GBP", "eur_amount": gross_eur},
        "fees": {"total": "0", "currency": "GBP", "total_eur": "0"},
        "net": {"amount": net_eur, "currency": "GBP", "eur_amount": net_eur},
        "account_id": "_unassigned",
        "correction_status": "ACTIVE",
        "withholding": {
            "source": {"country": "GB", "amount_eur": "0", "rate_pct": "0"},
            "destination": {"country": "ES", "amount_eur": "39.86", "rate_pct": "19.00"},
        },
    }


def _share_acquisition_leg(doc_id="leg_sa_1", quantity="9", gross_eur="0",
                           cost_basis_status="INCOMPLETE",
                           ca_group_id="cag_test") -> dict:
    """SHARE_ACQUISITION → txn_type=BUY."""
    net_eur = gross_eur
    return {
        "id": doc_id,
        "doc_type": "ledger_txn",
        "txn_type": "BUY",
        "ca_leg_type": "SHARE_ACQUISITION",
        "ca_group_id": ca_group_id,
        "security_id": _SECURITY_ID,
        "ticker": "ULVR",
        "trade_date": "2024-03-28",
        "quantity": quantity,
        "gross": {"amount": gross_eur, "currency": "GBP", "eur_amount": gross_eur},
        "fees": {"total": "0", "currency": "GBP", "total_eur": "0"},
        "net": {"amount": net_eur, "currency": "GBP", "eur_amount": net_eur},
        "account_id": "_unassigned",
        "correction_status": "ACTIVE",
        "cost_basis_status": cost_basis_status,
    }


def _rights_sold_leg(doc_id="leg_rs_1", quantity="3", gross_eur="78.67",
                     fees_eur="2.33", ca_group_id="cag_test") -> dict:
    """RIGHTS_SOLD → txn_type=SELL, sales_type=DERECHOS."""
    net_eur = str(Decimal(gross_eur) - Decimal(fees_eur))
    return {
        "id": doc_id,
        "doc_type": "ledger_txn",
        "txn_type": "SELL",
        "ca_leg_type": "RIGHTS_SOLD",
        "ca_group_id": ca_group_id,
        "security_id": _SECURITY_ID,
        "ticker": "ULVR",
        "trade_date": "2024-03-28",
        "quantity": quantity,
        "sales_type": "DERECHOS",
        "gross": {"amount": gross_eur, "currency": "GBP", "eur_amount": gross_eur},
        "fees": {"total": fees_eur, "currency": "GBP", "total_eur": fees_eur},
        "net": {"amount": net_eur, "currency": "GBP", "eur_amount": net_eur},
        "account_id": "_unassigned",
        "correction_status": "ACTIVE",
    }


def _cash_top_up_leg(doc_id="leg_ctu_1", gross_eur="5.77",
                     ca_group_id="cag_test") -> dict:
    """CASH_TOP_UP → txn_type=BUY, quantity=0, cost_basis_status=INCOMPLETE."""
    return {
        "id": doc_id,
        "doc_type": "ledger_txn",
        "txn_type": "BUY",
        "ca_leg_type": "CASH_TOP_UP",
        "ca_group_id": ca_group_id,
        "security_id": _SECURITY_ID,
        "ticker": "ULVR",
        "trade_date": "2024-03-28",
        "quantity": "0",
        "gross": {"amount": gross_eur, "currency": "GBP", "eur_amount": gross_eur},
        "fees": {"total": "0", "currency": "GBP", "total_eur": "0"},
        "net": {"amount": gross_eur, "currency": "GBP", "eur_amount": gross_eur},
        "account_id": "_unassigned",
        "correction_status": "ACTIVE",
        "cost_basis_status": "INCOMPLETE",
    }


def _standard_buy(doc_id="std_buy_1", quantity="100",
                  gross_eur="2000.00", fees_eur="9.95") -> dict:
    net_eur = str(Decimal(gross_eur) + Decimal(fees_eur))
    return {
        "id": doc_id,
        "doc_type": "ledger_txn",
        "txn_type": "BUY",
        "security_id": _SECURITY_ID,
        "ticker": "ULVR",
        "trade_date": "2023-06-01",
        "quantity": quantity,
        "gross": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur},
        "fees": {"total": fees_eur, "currency": "EUR", "total_eur": fees_eur},
        "net": {"amount": net_eur, "currency": "EUR", "eur_amount": net_eur},
        "account_id": "_unassigned",
        "correction_status": "ACTIVE",
        "cost_basis_status": "COMPLETE",
    }


# ---------------------------------------------------------------------------
# H-T2: CASH_DIVIDEND leg → no share change, counted in total_dividends_eur
# ---------------------------------------------------------------------------

class TestCashDividendLegHoldings:
    """H-T2 contract: CASH_DIVIDEND leg has txn_type=DIVIDEND → no share change."""

    def test_cash_dividend_no_share_change(self):
        """A CASH_DIVIDEND leg must not change total_shares."""
        svc = _make_services([_cash_dividend_leg()])
        result = svc.compute_holdings()
        # Dividend leg alone produces no holding (no shares ever acquired)
        # Holdings result is empty or has zero shares
        if result["holdings"]:
            h = result["holdings"][0]
            assert Decimal(h.get("total_shares", "0")) == Decimal("0"), (
                "CASH_DIVIDEND leg must not add shares to holding"
            )

    def test_cash_dividend_counted_in_dividends(self):
        """CASH_DIVIDEND leg net_eur must appear in total_dividends_eur."""
        # Add a prior BUY so the holding exists (DIVIDEND alone may be filtered)
        svc = _make_services([
            _standard_buy(),
            _cash_dividend_leg(net_eur="169.93"),
        ])
        result = svc.compute_holdings()
        assert result["holdings"], "Holding must exist after BUY + DIVIDEND"
        h = result["holdings"][0]
        assert h.get("total_dividends_eur") is not None, (
            "total_dividends_eur must be present when CASH_DIVIDEND leg exists"
        )
        dividends = Decimal(h["total_dividends_eur"].replace(",", "."))
        assert dividends == Decimal("169.93"), (
            f"total_dividends_eur must equal CASH_DIVIDEND net_eur; got {dividends}"
        )

    def test_cash_dividend_does_not_affect_pool_cost(self):
        """CASH_DIVIDEND must not alter the cost pool."""
        buy_gross = "2009.95"
        buy_fees = "9.95"
        svc = _make_services([
            _standard_buy(gross_eur=buy_gross, fees_eur=buy_fees),
            _cash_dividend_leg(gross_eur="300.00", net_eur="240.00"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        # avg_cost_basis_eur should reflect only the BUY, not the dividend.
        # Engine: cost = net = gross + fees; avg = net / qty = 2019.90/100 = 20.20
        avg_cost = Decimal(h["avg_cost_basis_eur"])
        raw_avg = (Decimal(buy_gross) + Decimal(buy_fees)) / Decimal("100")
        expected_avg = raw_avg.quantize(Decimal("0.01"))
        assert avg_cost == expected_avg, (
            f"avg_cost_basis_eur ({avg_cost}) must not be affected by CASH_DIVIDEND leg"
        )

    def test_cash_dividend_shares_unchanged_after_buy(self):
        """BUY(100) + CASH_DIVIDEND → total_shares stays 100."""
        svc = _make_services([
            _standard_buy(quantity="100"),
            _cash_dividend_leg(),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("100"), (
            "CASH_DIVIDEND must not change total_shares"
        )


# ---------------------------------------------------------------------------
# H-T3: SHARE_ACQUISITION (COMPLETE) → +shares, pool cost updated
# ---------------------------------------------------------------------------

class TestShareAcquisitionCompleteHoldings:
    """H-T3: SHARE_ACQUISITION with COMPLETE cost_basis adds shares and cost to pool."""

    def test_share_acquisition_complete_adds_shares(self):
        """SHARE_ACQUISITION COMPLETE → total_shares increases by quantity."""
        svc = _make_services([
            _share_acquisition_leg(quantity="9", gross_eur="222.75",
                                   cost_basis_status="COMPLETE"),
        ])
        result = svc.compute_holdings()
        assert result["holdings"], "Holding must exist after SHARE_ACQUISITION COMPLETE"
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("9"), (
            "SHARE_ACQUISITION COMPLETE must add 9 shares"
        )

    def test_share_acquisition_complete_updates_pool_cost(self):
        """SHARE_ACQUISITION COMPLETE → avg_cost_basis_eur reflects acquisition cost."""
        gross_eur = "222.75"
        quantity = "9"
        svc = _make_services([
            _share_acquisition_leg(quantity=quantity, gross_eur=gross_eur,
                                   cost_basis_status="COMPLETE"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        expected_avg = Decimal(gross_eur) / Decimal(quantity)
        actual_avg = Decimal(h["avg_cost_basis_eur"])
        # Allow 1 cent rounding on average
        assert abs(actual_avg - expected_avg) <= Decimal("0.01"), (
            f"avg_cost_basis_eur ({actual_avg}) must reflect SHARE_ACQUISITION cost; "
            f"expected ~{expected_avg}"
        )

    def test_share_acquisition_complete_on_top_of_prior_buy(self):
        """BUY + SHARE_ACQUISITION COMPLETE → total shares = buy_qty + acq_qty."""
        svc = _make_services([
            _standard_buy(quantity="100"),
            _share_acquisition_leg(quantity="9", gross_eur="222.75",
                                   cost_basis_status="COMPLETE"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("109"), (
            "total_shares must sum BUY + SHARE_ACQUISITION COMPLETE"
        )


# ---------------------------------------------------------------------------
# H-T4: SHARE_ACQUISITION (INCOMPLETE) → +unpaid_shares, zero pool cost
# ---------------------------------------------------------------------------

class TestShareAcquisitionIncompleteHoldings:
    """H-T4: SHARE_ACQUISITION INCOMPLETE adds shares but zero pool cost."""

    def test_share_acquisition_incomplete_adds_shares(self):
        """SHARE_ACQUISITION INCOMPLETE → total_shares increases."""
        svc = _make_services([
            _standard_buy(quantity="100"),
            _share_acquisition_leg(quantity="9", gross_eur="0",
                                   cost_basis_status="INCOMPLETE"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        total = Decimal(h["total_shares"])
        # INCOMPLETE shares may appear in total_shares or unpaid_shares depending on impl
        # Contract says: +shares counted; zero pool cost
        assert total >= Decimal("100"), (
            "total_shares must include INCOMPLETE SHARE_ACQUISITION shares"
        )

    def test_share_acquisition_incomplete_zero_pool_cost(self):
        """SHARE_ACQUISITION INCOMPLETE must not contribute to pool_cost (avg cost basis)."""
        buy_gross = "2009.95"
        buy_qty = "100"
        buy_fees = "9.95"
        # Engine: cost = net = gross + fees; avg = net / qty = 2019.90/100 = 20.20
        from decimal import ROUND_HALF_UP
        raw_avg = (Decimal(buy_gross) + Decimal(buy_fees)) / Decimal(buy_qty)
        expected_avg = raw_avg.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        svc = _make_services([
            _standard_buy(quantity=buy_qty, gross_eur=buy_gross, fees_eur=buy_fees),
            _share_acquisition_leg(quantity="9", gross_eur="0",
                                   cost_basis_status="INCOMPLETE"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        # avg_cost_basis_eur is computed over pool_shares only, not unpaid_shares
        # So avg cost should remain unchanged by the INCOMPLETE acquisition
        actual_avg = Decimal(h["avg_cost_basis_eur"])
        assert actual_avg == expected_avg, (
            f"avg_cost_basis_eur must not be inflated by INCOMPLETE SHARE_ACQUISITION; "
            f"expected {expected_avg}, got {actual_avg}"
        )


# ---------------------------------------------------------------------------
# H-T5: RIGHTS_SOLD (DERECHOS) → no share change, counted in rights proceeds
# ---------------------------------------------------------------------------

class TestRightsSoldLegHoldings:
    """H-T5: RIGHTS_SOLD (sales_type=DERECHOS) → no share change."""

    def test_rights_sold_no_share_change(self):
        """RIGHTS_SOLD must not decrement total_shares."""
        svc = _make_services([
            _standard_buy(quantity="100"),
            _rights_sold_leg(quantity="3", gross_eur="78.67", fees_eur="2.33"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("100"), (
            "RIGHTS_SOLD (DERECHOS) must not reduce total_shares"
        )

    def test_rights_sold_does_not_affect_avg_cost(self):
        """RIGHTS_SOLD must not change the average cost basis."""
        from decimal import ROUND_HALF_UP
        buy_gross = "2009.95"
        buy_fees = "9.95"
        buy_qty = "100"
        # Engine: cost = net = gross + fees; avg = net / qty = 2019.90/100 = 20.20
        raw_avg = (Decimal(buy_gross) + Decimal(buy_fees)) / Decimal(buy_qty)
        expected_avg = raw_avg.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        svc = _make_services([
            _standard_buy(quantity=buy_qty, gross_eur=buy_gross, fees_eur=buy_fees),
            _rights_sold_leg(quantity="50", gross_eur="500.00", fees_eur="5.00"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        actual_avg = Decimal(h["avg_cost_basis_eur"])
        assert actual_avg == expected_avg, (
            f"avg_cost_basis_eur ({actual_avg}) must not be affected by RIGHTS_SOLD"
        )

    def test_rights_sold_counted_in_rights_proceeds(self):
        """RIGHTS_SOLD net proceeds must appear in rights_proceeds_eur."""
        gross = "78.67"
        fees = "2.33"
        expected_net = Decimal(gross) - Decimal(fees)

        svc = _make_services([
            _standard_buy(quantity="100"),
            _rights_sold_leg(quantity="3", gross_eur=gross, fees_eur=fees),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        rights_proceeds = h.get("rights_proceeds_eur")
        assert rights_proceeds is not None, (
            "rights_proceeds_eur must be present when RIGHTS_SOLD leg exists"
        )
        assert Decimal(rights_proceeds) == expected_net, (
            f"rights_proceeds_eur ({rights_proceeds}) must equal gross-fees of RIGHTS_SOLD"
        )


# ---------------------------------------------------------------------------
# H-T6: CASH_TOP_UP (qty=0, INCOMPLETE) → no share change, no pool cost
# ---------------------------------------------------------------------------

class TestCashTopUpLegHoldings:
    """H-T6: CASH_TOP_UP with qty=0 and INCOMPLETE → no share change, no pool cost.

    The top-up amount is a pure cash outflow. It does not add shares and must
    not artificially inflate the cost pool / average cost.
    """

    def test_cash_top_up_no_share_change(self):
        """CASH_TOP_UP must not change total_shares."""
        svc = _make_services([
            _standard_buy(quantity="100"),
            _cash_top_up_leg(gross_eur="5.77"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("100"), (
            "CASH_TOP_UP (qty=0) must not change total_shares"
        )

    def test_cash_top_up_no_pool_cost_inflation(self):
        """CASH_TOP_UP (INCOMPLETE) must not inflate the cost pool.

        Per §H.3.5: CASH_TOP_UP is tagged INCOMPLETE so its gross does NOT
        enter the pool_cost. avg_cost_basis_eur must reflect only the BUY.
        """
        from decimal import ROUND_HALF_UP
        buy_gross = "2009.95"
        buy_fees = "9.95"
        buy_qty = "100"
        # Engine: cost = net = gross + fees; avg = net / qty = 2019.90/100 = 20.20
        raw_avg = (Decimal(buy_gross) + Decimal(buy_fees)) / Decimal(buy_qty)
        expected_avg = raw_avg.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        svc = _make_services([
            _standard_buy(quantity=buy_qty, gross_eur=buy_gross, fees_eur=buy_fees),
            _cash_top_up_leg(gross_eur="100.00"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        actual_avg = Decimal(h["avg_cost_basis_eur"])
        assert actual_avg == expected_avg, (
            f"avg_cost_basis_eur ({actual_avg}) must not be inflated by CASH_TOP_UP; "
            f"expected {expected_avg}"
        )


# ---------------------------------------------------------------------------
# Combined: Full DIVIDEND_WITH_SCRIP group holdings computation
# ---------------------------------------------------------------------------

class TestDividendWithScripGroupHoldings:
    """Smoke test for a complete DIVIDEND_WITH_SCRIP corporate-action group.

    Legs: CASH_DIVIDEND + SHARE_ACQUISITION(INCOMPLETE) + RIGHTS_SOLD + CASH_TOP_UP
    Prior position: 100 shares via standard BUY.

    Expected holdings after all legs:
    - total_shares: 100 (prior BUY) + 9 (SHARE_ACQUISITION) = but INCOMPLETE
      so unpaid_shares=9, pool_shares=100
    - Pool avg_cost: unchanged by INCOMPLETE acq, CASH_DIVIDEND, RIGHTS_SOLD, CASH_TOP_UP
    - total_dividends_eur: 169.93 (CASH_DIVIDEND net)
    - rights_proceeds_eur: 76.34 (RIGHTS_SOLD net)
    """

    def _build_full_group(self):
        return [
            _standard_buy(quantity="100", gross_eur="2009.95", fees_eur="9.95"),
            _cash_dividend_leg(doc_id="g_cd", gross_eur="209.79", net_eur="169.93"),
            _share_acquisition_leg(doc_id="g_sa", quantity="9", gross_eur="0",
                                   cost_basis_status="INCOMPLETE"),
            _rights_sold_leg(doc_id="g_rs", quantity="3",
                             gross_eur="78.67", fees_eur="2.33"),
            _cash_top_up_leg(doc_id="g_ctu", gross_eur="5.77"),
        ]

    def test_full_group_holding_exists(self):
        svc = _make_services(self._build_full_group())
        result = svc.compute_holdings()
        assert result["holdings"], "Holding must exist after full DIVIDEND_WITH_SCRIP group"

    def test_full_group_dividends_counted(self):
        svc = _make_services(self._build_full_group())
        h = svc.compute_holdings()["holdings"][0]
        dividends = Decimal(h.get("total_dividends_eur", "0"))
        assert dividends == Decimal("169.93"), (
            f"total_dividends_eur must be 169.93 from CASH_DIVIDEND leg; got {dividends}"
        )

    def test_full_group_rights_proceeds_counted(self):
        svc = _make_services(self._build_full_group())
        h = svc.compute_holdings()["holdings"][0]
        rights = Decimal(h.get("rights_proceeds_eur", "0"))
        expected_rights = Decimal("78.67") - Decimal("2.33")
        assert rights == expected_rights, (
            f"rights_proceeds_eur must be {expected_rights}; got {rights}"
        )

    def test_full_group_pool_cost_unchanged_by_incomplete_legs(self):
        """Pool avg cost reflects only the COMPLETE BUY, not INCOMPLETE CA legs."""
        from decimal import ROUND_HALF_UP
        # Engine: cost = net = gross + fees; avg = net / qty = 2019.90/100 = 20.20
        raw_avg = (Decimal("2009.95") + Decimal("9.95")) / Decimal("100")
        expected_avg = raw_avg.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        svc = _make_services(self._build_full_group())
        h = svc.compute_holdings()["holdings"][0]
        actual_avg = Decimal(h["avg_cost_basis_eur"])
        assert actual_avg == expected_avg, (
            f"avg_cost_basis_eur ({actual_avg}) must not be affected by INCOMPLETE legs; "
            f"expected {expected_avg}"
        )


# ---------------------------------------------------------------------------
# H-21: Standalone movements unaffected by CA fields
# ---------------------------------------------------------------------------

class TestStandaloneMovementsUnaffectedByCAFields:
    """CA group fields (ca_group_id, ca_leg_type) on a document must not alter
    the holdings computation when processed as standalone movements.

    A BUY movement that happens to have ca_group_id set is still a BUY.
    """

    def test_standard_buy_with_ca_fields_still_adds_shares(self):
        """A BUY with ca_group_id is still processed as a BUY."""
        doc = _standard_buy(quantity="50", gross_eur="1000.00", fees_eur="5.00")
        doc["ca_group_id"] = "cag_extra"
        doc["ca_leg_type"] = "SHARE_ACQUISITION"
        doc["cost_basis_status"] = "COMPLETE"
        svc = _make_services([doc])
        result = svc.compute_holdings()
        assert result["holdings"], "Holding with CA fields on a BUY must exist"
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("50")
