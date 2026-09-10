"""Tests for holdings derivation (HoldingsService).

Hermetic: fake Cosmos containers, no network.
"""

import pytest
from decimal import Decimal

from src.portfolio.cosmos_portfolio import CosmosPortfolioService
from src.portfolio.cosmos_securities import CosmosSecuritiesService
from src.portfolio.holdings_service import HoldingsService


# ---------------------------------------------------------------------------
# Fake Cosmos containers
# ---------------------------------------------------------------------------

class FakePortfolioContainer:
    def __init__(self, movements=None):
        self._movements = movements or []

    def query_items(self, query="", parameters=None, enable_cross_partition_query=True, partition_key=None):
        if "COUNT" in query:
            return iter([len(self._movements)])
        if "NOT IS_DEFINED(c.deleted_at)" in query:
            result = [m for m in self._movements if "deleted_at" not in m]
            return iter(result)
        return iter(list(self._movements))

    def upsert_item(self, body):
        self._movements.append(body)
        return body

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
        key = (partition_key, item)
        if key not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[key])

    def create_item(self, body):
        ticker = body["symbol"]
        key = (ticker, body["id"])
        self._store[key] = dict(body)
        return dict(body)

    def query_items(self, query="", parameters=None, enable_cross_partition_query=False, partition_key=None):
        return iter([v for v in self._store.values() if v.get("doc_type") == "security_master"])

    def replace_item(self, item, body):
        for key in self._store:
            if self._store[key].get("id") == item:
                self._store[key] = dict(body)
                return dict(body)
        from azure.cosmos.exceptions import CosmosResourceNotFoundError
        raise CosmosResourceNotFoundError(message="not found", response=None)


def _make_movement(
    movement_id, security_id, txn_type, quantity, gross_eur,
    account_id="_unassigned", commission_eur="0", net_eur=None,
    cost_basis_status="COMPLETE", trade_date="2024-01-15",
):
    """Build a ledger_txn movement document.

    Net convention (danny-fifo-net-accounting-contract.md §1):
      BUY:  gross = trade consideration (before fees); net = gross + commission.
            Pass gross_eur as the trade consideration; net is auto-derived.
      SELL: gross = total proceeds (before fee deduction); net = gross - commission.
    """
    ticker = security_id.split(":")[-1]
    if net_eur is not None:
        net = net_eur
    elif txn_type == "BUY":
        net = str(Decimal(gross_eur) + Decimal(commission_eur))
    else:
        net = str(Decimal(gross_eur) - Decimal(commission_eur))
    return {
        "id": movement_id,
        "doc_type": "ledger_txn",
        "txn_type": txn_type,
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": trade_date,
        "quantity": str(quantity),
        "gross": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur},
        "fees": {"total": commission_eur, "currency": "EUR", "total_eur": commission_eur},
        "net": {"amount": net, "currency": "EUR", "eur_amount": net},
        "account_id": account_id,
        "cost_basis_status": cost_basis_status,
        "warnings": [],
    }


def _make_services(movements):
    portfolio_svc = CosmosPortfolioService(
        FakePortfolioContainer(movements), None
    )
    securities_svc = CosmosSecuritiesService(FakeSymbolsContainer())
    return portfolio_svc, securities_svc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEmptyLedger:
    def test_empty_holdings(self):
        portfolio_svc, securities_svc = _make_services([])
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        assert result["holdings"] == []
        assert result["summary"]["total_securities"] == 0
        assert result["summary"]["total_invested_eur"] == "0.00"
        assert result["summary"]["total_dividends_eur"] == "0.00"


class TestShareCalculation:
    def test_buy_only(self):
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "18250.00", commission_eur="7.50"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        assert len(result["holdings"]) == 1
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("100")
        assert h["security_id"] == "XNYS:AAPL"

    def test_buy_then_sell(self):
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "18250.00"),
            _make_movement("t2", "XNYS:AAPL", "SELL", "30", "5800.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("70")

    def test_sell_before_buy_gives_negative_inventory(self):
        """Sales before buys are accepted with NEGATIVE_INVENTORY warning."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "SELL", "50", "9000.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("-50")
        warning_types = [w["type"] for w in h["warnings"]]
        assert "NEGATIVE_INVENTORY" in warning_types

    def test_dividend_counted_separately(self):
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "18250.00"),
            _make_movement(
                "t2", "XNYS:AAPL", "DIVIDEND", "0", "86.25",
                net_eur="73.31",
            ),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("100")
        assert Decimal(h["total_dividends_eur"]) == Decimal("73.31")


class TestCostBasis:
    def test_complete_cost_basis(self):
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "10", "1825.00", commission_eur="7.50"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert h["cost_basis_status"] == "COMPLETE"
        assert h["avg_cost_basis_eur"] is not None

    def test_zero_cost_acquisition_incomplete(self):
        movements = [
            _make_movement(
                "t1", "XNYS:AAPL", "BUY", "50", "0", cost_basis_status="INCOMPLETE"
            ),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert h["cost_basis_status"] == "INCOMPLETE"
        warning_types = [w["type"] for w in h["warnings"]]
        # INCOMPLETE (genuinely unknown cost) emits INCOMPLETE_COST_BASIS, not ZERO_COST_ACQUISITION.
        assert "INCOMPLETE_COST_BASIS" in warning_types

    def test_total_invested_excludes_dividends(self):
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "10", "1825.00"),
            _make_movement("t2", "XNYS:AAPL", "DIVIDEND", "0", "100.00", net_eur="85.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["total_invested_eur"]) == Decimal("1825.00")
        assert Decimal(h["total_dividends_eur"]) == Decimal("85.00")

    def test_option_movements_excluded_from_fifo_and_totals(self):
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "10", "1000.00"),
            _make_movement(
                "t2",
                "XNYS:AAPL",
                "CALL_SELL",
                "0",
                "150.00",
                commission_eur="5.00",
                net_eur="145.00",
            ),
            _make_movement(
                "t3",
                "XNYS:AAPL",
                "PUT_BUY",
                "0",
                "50.00",
                commission_eur="2.00",
                net_eur="52.00",
            ),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("10")
        assert Decimal(h["total_purchase_outflow_eur"]) == Decimal("1000.00")
        assert Decimal(h["total_sale_proceeds_eur"]) == Decimal("0.00")
        assert Decimal(h["total_dividends_eur"]) == Decimal("0.00")


class TestMultipleAccounts:
    def test_each_account_tracked(self):
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "10", "1000.00", account_id="ibkr"),
            _make_movement("t2", "XNYS:AAPL", "BUY", "20", "2000.00", account_id="_unassigned"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("30")
        assert "ibkr" in h["accounts"]
        assert "_unassigned" in h["accounts"]

    def test_filter_by_account(self):
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "10", "1000.00", account_id="ibkr"),
            _make_movement("t2", "XNYS:AAPL", "BUY", "20", "2000.00", account_id="_unassigned"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings(account_id="ibkr")
        assert len(result["holdings"]) == 1
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("10")


class TestMultipleSecurities:
    def test_separate_holdings(self):
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "10", "1000.00"),
            _make_movement("t2", "XMAD:TEF", "BUY", "100", "380.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        ids = {h["security_id"] for h in result["holdings"]}
        assert ids == {"XNYS:AAPL", "XMAD:TEF"}
        assert result["summary"]["total_securities"] == 2

    def test_summary_totals(self):
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "10", "1000.00"),
            _make_movement("t2", "XMAD:TEF", "BUY", "100", "380.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        assert Decimal(result["summary"]["total_invested_eur"]) == Decimal("1380.00")


# ---------------------------------------------------------------------------
# F1 — Commission (fees.total_eur) included in holdings cost basis
# ---------------------------------------------------------------------------

class TestCommissionInCostBasis:
    def test_commission_affects_holdings_cost_basis(self):
        movements = [
            _make_movement(
                "t1", "XNYS:AAPL", "BUY", "10", "1000.00",
                commission_eur="10.00",
            ),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        # gross=1000 (trade consideration), commission=10 → net=1010 → cost=1010
        assert Decimal(h["total_invested_eur"]) == Decimal("1010.00")

    def test_zero_commission_no_effect(self):
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "10", "1825.00", commission_eur="0"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["total_invested_eur"]) == Decimal("1825.00")


# ---------------------------------------------------------------------------
# F5 — Holdings total_shares unaffected by dividend with null quantity
# ---------------------------------------------------------------------------

class TestDividendQuantityNullHoldings:
    def test_holdings_ignores_dividend_quantity(self):
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "18250.00"),
            {
                "id": "t2",
                "doc_type": "ledger_txn",
                "txn_type": "DIVIDEND",
                "security_id": "XNYS:AAPL",
                "ticker": "AAPL",
                "trade_date": "2024-06-15",
                "quantity": None,
                "gross": {"amount": "100.00", "currency": "EUR", "eur_amount": "100.00"},
                "fees": {"total": "0.00", "currency": "EUR", "total_eur": "0.00"},
                "net": {"amount": "73.31", "currency": "EUR", "eur_amount": "73.31"},
                "account_id": "_unassigned",
                "cost_basis_status": "COMPLETE",
                "warnings": [],
            },
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("100")
        assert Decimal(h["total_dividends_eur"]) == Decimal("73.31")


# ---------------------------------------------------------------------------
# F7 — avg_cost_basis_eur must divide by paid shares, not transaction count
# ---------------------------------------------------------------------------

class TestAvgCostBasisPerShare:
    def test_avg_cost_basis_single_buy(self):
        """1 BUY: 10 shares, gross=€1825 (trade), commission=€7.50 → net=€1832.50 → avg=183.25."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "10", "1825.00", commission_eur="7.50"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert h["avg_cost_basis_eur"] == "183.25"

    def test_avg_cost_basis_multi_buy(self):
        """BUY 100@gross €1000 (€10 fee; net €1010) + BUY 50@gross €750 (€5 fee; net €755) → avg = (1010+755)/150 = 11.77."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "1000.00", commission_eur="10.00"),
            _make_movement("t2", "XNYS:AAPL", "BUY", "50", "750.00", commission_eur="5.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert h["avg_cost_basis_eur"] == "11.77"

    def test_avg_cost_basis_excludes_zero_cost(self):
        """Paid BUY (100 shares, gross €1000, €10 fee; net €1010) + INCOMPLETE BUY (50 shares) → avg = 1010/100 = 10.10."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "1000.00", commission_eur="10.00"),
            _make_movement("t2", "XNYS:AAPL", "BUY", "50", "0", cost_basis_status="INCOMPLETE"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert h["avg_cost_basis_eur"] == "10.10"

    def test_avg_cost_basis_no_paid_buys_is_null(self):
        """Only INCOMPLETE BUYs → avg_cost_basis_eur is None."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "50", "0", cost_basis_status="INCOMPLETE"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert h["avg_cost_basis_eur"] is None

    def test_avg_cost_basis_independent_of_sells(self):
        """BUY 100@gross €1000 (€10 fee; net €1010) then SELL 30 → avg = 1010/100 = 10.10 (FIFO single lot: avg unchanged)."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "1000.00", commission_eur="10.00"),
            _make_movement("t2", "XNYS:AAPL", "SELL", "30", "350.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert h["avg_cost_basis_eur"] == "10.10"
        assert Decimal(h["total_shares"]) == Decimal("70")

    def test_avg_cost_basis_dividends_only_is_null(self):
        """Only DIVIDEND movements → avg_cost_basis_eur is None."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "DIVIDEND", "0", "100.00", net_eur="85.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert h["avg_cost_basis_eur"] is None


# ---------------------------------------------------------------------------
# Summary totals — purchases, sales, current_invested, backward-compat alias
# ---------------------------------------------------------------------------

class TestSummaryTotals:
    def test_purchases_only(self):
        """BUY 100@gross €1000 (€10 fee; net €1010) → purchases=1010, sales=0, current_invested=1010."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "1000.00", commission_eur="10.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        s = result["summary"]
        assert s["total_purchases_eur"] == "1010.00"
        assert s["total_sales_eur"] == "0.00"
        assert s["current_invested_eur"] == "1010.00"

    def test_purchases_and_sales(self):
        """BUY 100@gross €1000 (€10 fee; net €1010) + SELL 30@€350 (€5 fee).

        FIFO avg = 1010/100 = 10.10.  cost_sold = 30×10.10 = 303.00.
        remaining = 1010 − 303 = 707.00.
        purchases=1010, sales=345, current_invested=remaining=707.00.
        """
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "1000.00", commission_eur="10.00"),
            _make_movement("t2", "XNYS:AAPL", "SELL", "30", "350.00", commission_eur="5.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        s = result["summary"]
        assert s["total_purchases_eur"] == "1010.00"
        assert s["total_sales_eur"] == "345.00"
        assert s["current_invested_eur"] == "707.00"

    def test_sales_exceed_purchases(self):
        """BUY 100@€500 + SELL 100@€800 (no fees).

        avg = 5.00.  cost_sold = 500.  remaining = 0.00.
        realized = 800 − 500 = 300.00.
        current_invested_eur = remaining_cost_basis = 0.00 (lots empty, not negative).
        """
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "500.00"),
            _make_movement("t2", "XNYS:AAPL", "SELL", "100", "800.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        s = result["summary"]
        assert s["total_purchases_eur"] == "500.00"
        assert s["total_sales_eur"] == "800.00"
        assert s["current_invested_eur"] == "0.00"

    def test_dividends_excluded_from_current_invested(self):
        """BUY + DIVIDEND → current_invested_eur equals total_purchases_eur (dividend excluded)."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "1000.00", commission_eur="10.00"),
            _make_movement("t2", "XNYS:AAPL", "DIVIDEND", "0", "86.25", net_eur="73.31"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        s = result["summary"]
        assert s["current_invested_eur"] == s["total_purchases_eur"]
        assert s["total_purchases_eur"] == "1010.00"

    def test_incomplete_buys_excluded_from_purchases(self):
        """INCOMPLETE BUY → total_purchases_eur=0.00."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "50", "0", cost_basis_status="INCOMPLETE"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        s = result["summary"]
        assert s["total_purchases_eur"] == "0.00"
        assert s["total_sales_eur"] == "0.00"
        assert s["current_invested_eur"] == "0.00"

    def test_multi_security_aggregation(self):
        """2 securities, mixed buys/sells → summary totals are portfolio-wide sums.

        AAPL: BUY 10@gross €1000 (€5 fee; net €1005), avg=100.50.
              SELL 5@€600 (€3 fee): cost_sold=502.50, remaining=502.50.
        TEF: BUY 100@gross €400 (€2 fee; net €402), avg=4.02.
             SELL 20@€90 (€1 fee): cost_sold=80.40, remaining=321.60.
        Portfolio remaining = 502.50 + 321.60 = 824.10.
        """
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "10", "1000.00", commission_eur="5.00"),
            _make_movement("t2", "XNYS:AAPL", "SELL", "5", "600.00", commission_eur="3.00"),
            _make_movement("t3", "XMAD:TEF", "BUY", "100", "400.00", commission_eur="2.00"),
            _make_movement("t4", "XMAD:TEF", "SELL", "20", "90.00", commission_eur="1.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        s = result["summary"]
        # AAPL net: 1000+5=1005; TEF net: 400+2=402 → 1407
        assert Decimal(s["total_purchases_eur"]) == Decimal("1407.00")
        # AAPL sales: 600-3=597; TEF sales: 90-1=89 → 686
        assert Decimal(s["total_sales_eur"]) == Decimal("686.00")
        # remaining_cost_basis = 502.50 + 321.60 = 824.10
        assert Decimal(s["current_invested_eur"]) == Decimal("824.10")
        assert s["total_securities"] == 2

    def test_backward_compat_total_invested(self):
        """summary.total_invested_eur == summary.total_purchases_eur (backward-compat alias)."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "1000.00", commission_eur="10.00"),
            _make_movement("t2", "XNYS:AAPL", "SELL", "30", "350.00", commission_eur="5.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        s = result["summary"]
        assert s["total_invested_eur"] == s["total_purchases_eur"]

    def test_empty_ledger_new_fields(self):
        """Empty ledger → all new summary fields are zero strings."""
        portfolio_svc, securities_svc = _make_services([])
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        s = result["summary"]
        assert s["total_purchases_eur"] == "0.00"
        assert s["total_sales_eur"] == "0.00"
        assert s["current_invested_eur"] == "0.00"

    def test_soft_deleted_excluded(self):
        """Movements with deleted_at are excluded from all totals."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "1000.00", commission_eur="10.00"),
            {
                **_make_movement("t2", "XNYS:AAPL", "BUY", "50", "500.00", commission_eur="5.00"),
                "deleted_at": "2024-03-01T00:00:00Z",
            },
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        s = result["summary"]
        # Only t1 should be counted (t2 is soft-deleted)
        assert s["total_purchases_eur"] == "1010.00"


# ---------------------------------------------------------------------------
# Per-security totals — total_purchases_eur and total_sales_eur on each holding
# ---------------------------------------------------------------------------

class TestPerSecurityTotals:
    def test_per_security_purchases_and_sales(self):
        """Per-holding total_purchases_eur and total_sales_eur are correct."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "18250.00", commission_eur="7.50"),
            _make_movement("t2", "XNYS:AAPL", "SELL", "30", "5500.00", commission_eur="5.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        # net cost = 18250 + 7.50 = 18257.50
        assert h["total_purchases_eur"] == "18257.50"
        # purchases alias equals total_invested_eur
        assert h["total_purchases_eur"] == h["total_invested_eur"]
        # sales = 5500 - 5 = 5495.00
        assert h["total_sales_eur"] == "5495.00"

    def test_per_security_no_sells(self):
        """Per-holding total_sales_eur is '0.00' when there are no sells."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "50", "5000.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert h["total_sales_eur"] == "0.00"

    def test_per_security_independent_of_other_securities(self):
        """Each security's sales/purchases are isolated from other securities."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "10", "1000.00", commission_eur="5.00"),
            _make_movement("t2", "XNYS:AAPL", "SELL", "5", "600.00", commission_eur="3.00"),
            _make_movement("t3", "XMAD:TEF", "BUY", "100", "400.00", commission_eur="2.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        by_id = {h["security_id"]: h for h in result["holdings"]}
        assert by_id["XNYS:AAPL"]["total_purchases_eur"] == "1005.00"
        assert by_id["XNYS:AAPL"]["total_sales_eur"] == "597.00"
        assert by_id["XMAD:TEF"]["total_purchases_eur"] == "402.00"
        assert by_id["XMAD:TEF"]["total_sales_eur"] == "0.00"


# ---------------------------------------------------------------------------
# Rights sales — ACCIONES / DERECHOS sales_type distinction
# Regression coverage for danny-rights-sale-contract design.
# Tests will fail until Livingston's implementation is merged; assertions are
# intentionally un-weakened so the failures are explicit.
# ---------------------------------------------------------------------------

def _make_movement_with_sales_type(
    movement_id, security_id, txn_type, quantity, gross_eur,
    sales_type=None, **kwargs,
):
    """Build a ledger movement dict with an explicit sales_type field."""
    m = _make_movement(movement_id, security_id, txn_type, quantity, gross_eur, **kwargs)
    if sales_type is not None:
        m["sales_type"] = sales_type
    return m


class TestRightsSaleHoldings:
    """Holdings computation must honour the ACCIONES/DERECHOS distinction."""

    def test_derechos_sale_does_not_decrement_shares(self):
        """BUY 100 + SELL 30 (DERECHOS) → total_shares remains 100."""
        movements = [
            _make_movement_with_sales_type("t1", "XNYS:AAPL", "BUY", "100", "20000.00"),
            _make_movement_with_sales_type(
                "t2", "XNYS:AAPL", "SELL", "30", "600.00", sales_type="DERECHOS"
            ),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("100")

    def test_acciones_sale_decrements_shares(self):
        """BUY 100 + SELL 30 (ACCIONES) → total_shares = 70."""
        movements = [
            _make_movement_with_sales_type("t1", "XNYS:AAPL", "BUY", "100", "20000.00"),
            _make_movement_with_sales_type(
                "t2", "XNYS:AAPL", "SELL", "30", "600.00", sales_type="ACCIONES"
            ),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("70")

    def test_mixed_sales_types_only_acciones_decrements(self):
        """BUY 100, SELL 30 (ACCIONES), SELL 20 (DERECHOS) → total_shares = 70."""
        movements = [
            _make_movement_with_sales_type("t1", "XNYS:AAPL", "BUY", "100", "20000.00"),
            _make_movement_with_sales_type(
                "t2", "XNYS:AAPL", "SELL", "30", "600.00",
                sales_type="ACCIONES", commission_eur="5",
            ),
            _make_movement_with_sales_type(
                "t3", "XNYS:AAPL", "SELL", "20", "400.00",
                sales_type="DERECHOS", commission_eur="5",
            ),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("70")

    def test_total_sales_eur_includes_both_acciones_and_derechos(self):
        """total_sales_eur sums net proceeds from both sale types."""
        movements = [
            _make_movement_with_sales_type("t1", "XNYS:AAPL", "BUY", "100", "20000.00"),
            _make_movement_with_sales_type(
                "t2", "XNYS:AAPL", "SELL", "30", "600.00",
                sales_type="ACCIONES", commission_eur="5",
            ),
            _make_movement_with_sales_type(
                "t3", "XNYS:AAPL", "SELL", "20", "400.00",
                sales_type="DERECHOS", commission_eur="5",
            ),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        # (600 - 5) + (400 - 5) = 595 + 395 = 990
        assert Decimal(h["total_sales_eur"]) == Decimal("990.00")

    def test_backward_compat_no_sales_type_defaults_to_acciones(self):
        """Legacy SELL without sales_type field defaults to ACCIONES (decrements shares)."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "20000.00"),
            _make_movement("t2", "XNYS:AAPL", "SELL", "30", "600.00"),  # no sales_type
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        # Must behave as ACCIONES: 100 - 30 = 70
        assert Decimal(h["total_shares"]) == Decimal("70")

    def test_design_doc_example_exact_values(self):
        """Exact example from design §4.3: BUY 100, SELL 30 ACCIONES, SELL 15 DERECHOS."""
        movements = [
            _make_movement_with_sales_type(
                "t1", "XNYS:AAPL", "BUY", "100", "2000.00", commission_eur="20"
            ),
            _make_movement_with_sales_type(
                "t2", "XNYS:AAPL", "SELL", "30", "600.00",
                sales_type="ACCIONES", commission_eur="5",
            ),
            _make_movement_with_sales_type(
                "t3", "XNYS:AAPL", "SELL", "15", "300.00",
                sales_type="DERECHOS", commission_eur="5",
            ),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        # total_shares = 100 - 30 = 70 (DERECHOS not subtracted)
        assert Decimal(h["total_shares"]) == Decimal("70")
        # total_invested_eur = net = gross 2000 + commission 20 = 2020
        assert Decimal(h["total_invested_eur"]) == Decimal("2020.00")
        # total_sales_eur = (600-5) + (300-5) = 595 + 295 = 890
        assert Decimal(h["total_sales_eur"]) == Decimal("890.00")

    def test_only_derechos_sales_leaves_shares_unchanged(self):
        """All SELLs are DERECHOS → total_shares equals total BUY quantity."""
        movements = [
            _make_movement_with_sales_type("t1", "XNYS:AAPL", "BUY", "100", "20000.00"),
            _make_movement_with_sales_type(
                "t2", "XNYS:AAPL", "SELL", "50", "1000.00", sales_type="DERECHOS"
            ),
            _make_movement_with_sales_type(
                "t3", "XNYS:AAPL", "SELL", "60", "1200.00", sales_type="DERECHOS"
            ),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("100")


# ---------------------------------------------------------------------------
# FIFO acceptance tests — Danny contract (S1–S10)
# ---------------------------------------------------------------------------

class TestFifoAcceptance:
    """Acceptance matrix for the FIFO cost-basis algorithm per Danny's contract."""

    def test_s1_buy_only(self):
        """S1: BUY 100@gross €1000 (€5 fee; net €1005) → remaining=1005, cost_sold=0, realized=0."""
        movements = [
            _make_movement("s1t1", "XNYS:AAPL", "BUY", "100", "1000.00", commission_eur="5.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        s = result["summary"]
        assert Decimal(h["remaining_cost_basis_eur"]) == Decimal("1005.00")
        assert Decimal(h["cost_basis_sold_eur"]) == Decimal("0.00")
        assert Decimal(h["realized_result_eur"]) == Decimal("0.00")
        assert Decimal(s["remaining_cost_basis_eur"]) == Decimal("1005.00")

    def test_s2_buy_partial_sell(self):
        """S2: BUY 100@gross €1000 (€5 fee; net €1005) → SELL 30@€15 (€3 fee).
        FIFO avg=10.05, cost_sold=301.50, remaining=703.50, proceeds=447, realized=145.50.
        """
        movements = [
            _make_movement("s2t1", "XNYS:AAPL", "BUY", "100", "1000.00", commission_eur="5.00"),
            _make_movement("s2t2", "XNYS:AAPL", "SELL", "30", "450.00", commission_eur="3.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["cost_basis_sold_eur"]) == Decimal("301.50")
        assert Decimal(h["remaining_cost_basis_eur"]) == Decimal("703.50")
        assert Decimal(h["total_sale_proceeds_eur"]) == Decimal("447.00")
        assert Decimal(h["realized_result_eur"]) == Decimal("145.50")

    def test_s3_buy_full_sell(self):
        """S3: BUY 100@€10 → SELL 100@€15. remaining=0, cost_sold=1000, realized=500."""
        movements = [
            _make_movement("s3t1", "XNYS:AAPL", "BUY", "100", "1000.00"),
            _make_movement("s3t2", "XNYS:AAPL", "SELL", "100", "1500.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["remaining_cost_basis_eur"]) == Decimal("0.00")
        assert Decimal(h["cost_basis_sold_eur"]) == Decimal("1000.00")
        assert Decimal(h["realized_result_eur"]) == Decimal("500.00")

    def test_s4_two_buys_then_sell(self):
        """S4: BUY 100@€10 (lot1), BUY 50@€20 (lot2) → SELL 60.
        FIFO: consume 60 from lot1.
        cost_sold = 60×10 = 600, remaining = 40×10 + 50×20 = 1400.
        """
        movements = [
            _make_movement("s4t1", "XNYS:AAPL", "BUY", "100", "1000.00",
                           trade_date="2024-01-10"),
            _make_movement("s4t2", "XNYS:AAPL", "BUY", "50", "1000.00",
                           trade_date="2024-02-10"),
            _make_movement("s4t3", "XNYS:AAPL", "SELL", "60", "1080.00",
                           trade_date="2024-03-10"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["cost_basis_sold_eur"]) == Decimal("600.00")
        assert Decimal(h["remaining_cost_basis_eur"]) == Decimal("1400.00")
        assert Decimal(h["total_sale_proceeds_eur"]) == Decimal("1080.00")

    def test_s5_derechos_only(self):
        """S5: BUY 100@€10 → SELL DERECHOS 20@€5.
        remaining=1000 (unchanged), cost_sold=0, rights=100, realized=100.
        """
        movements = [
            _make_movement("s5t1", "XNYS:AAPL", "BUY", "100", "1000.00"),
            _make_movement_with_sales_type(
                "s5t2", "XNYS:AAPL", "SELL", "20", "100.00", sales_type="DERECHOS"
            ),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["remaining_cost_basis_eur"]) == Decimal("1000.00")
        assert Decimal(h["cost_basis_sold_eur"]) == Decimal("0.00")
        assert Decimal(h["rights_proceeds_eur"]) == Decimal("100.00")
        assert Decimal(h["total_sale_proceeds_eur"]) == Decimal("100.00")
        assert Decimal(h["realized_result_eur"]) == Decimal("100.00")
        assert Decimal(h["total_shares"]) == Decimal("100")

    def test_s6_acciones_and_derechos(self):
        """S6: BUY 100@€10 → SELL 30 ACCIONES@€15 → SELL DERECHOS 10@€5.
        cost_sold=300, remaining=700, proceeds=500, rights=50, realized=200.
        """
        movements = [
            _make_movement("s6t1", "XNYS:AAPL", "BUY", "100", "1000.00"),
            _make_movement_with_sales_type(
                "s6t2", "XNYS:AAPL", "SELL", "30", "450.00", sales_type="ACCIONES"
            ),
            _make_movement_with_sales_type(
                "s6t3", "XNYS:AAPL", "SELL", "10", "50.00", sales_type="DERECHOS"
            ),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["cost_basis_sold_eur"]) == Decimal("300.00")
        assert Decimal(h["remaining_cost_basis_eur"]) == Decimal("700.00")
        assert Decimal(h["total_sale_proceeds_eur"]) == Decimal("500.00")
        assert Decimal(h["rights_proceeds_eur"]) == Decimal("50.00")
        assert Decimal(h["realized_result_eur"]) == Decimal("200.00")

    def test_s7_incomplete_buy_then_sell(self):
        """S7: BUY 50 INCOMPLETE (2024-01-10) + BUY 50@€10 (2024-01-15) → SELL 70.
        FIFO: consume INCOMPLETE lot first (50, cost=0, warning), then 20 from COMPLETE.
        cost_sold=200 (20×10), remaining=300 (30×10), has_incomplete=True.
        """
        movements = [
            _make_movement("s7t1", "XNYS:AAPL", "BUY", "50", "0",
                           cost_basis_status="INCOMPLETE",
                           trade_date="2024-01-10"),
            _make_movement("s7t2", "XNYS:AAPL", "BUY", "50", "500.00",
                           trade_date="2024-01-15"),
            _make_movement("s7t3", "XNYS:AAPL", "SELL", "70", "1050.00",
                           trade_date="2024-06-01"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        s = result["summary"]
        assert Decimal(h["cost_basis_sold_eur"]) == Decimal("200.00")
        assert Decimal(h["remaining_cost_basis_eur"]) == Decimal("300.00")
        assert s["has_incomplete_cost_basis"] is True
        # INCOMPLETE (genuinely unknown cost) emits INCOMPLETE_COST_BASIS warning, not ZERO_COST_ACQUISITION.
        assert any(w["type"] == "INCOMPLETE_COST_BASIS" for w in h["warnings"])

    def test_s8_transfer_preserves_basis(self):
        """S8: BUY 100@€10 acct-A → TRANSFER_OUT 40 → TRANSFER_IN 40 (carried=400).
        Global: remaining=1000. Acct-A: 600. Acct-B: 400.
        """
        movements = [
            _make_movement("s8t1", "XNYS:AAPL", "BUY", "100", "1000.00", account_id="acct-a"),
            {
                **_make_movement("s8t2", "XNYS:AAPL", "TRANSFER_OUT", "40", "0",
                                 account_id="acct-a"),
                "transfer_cost_basis_eur": "400",
            },
            {
                **_make_movement("s8t3", "XNYS:AAPL", "TRANSFER_IN", "40", "0",
                                 account_id="acct-b"),
                "transfer_cost_basis_eur": "400",
            },
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)

        global_result = svc.compute_holdings()
        assert Decimal(global_result["summary"]["remaining_cost_basis_eur"]) == Decimal("1000.00")

        acct_a = svc.compute_holdings(account_id="acct-a")
        assert Decimal(acct_a["summary"]["remaining_cost_basis_eur"]) == Decimal("600.00")

        acct_b = svc.compute_holdings(account_id="acct-b")
        assert Decimal(acct_b["summary"]["remaining_cost_basis_eur"]) == Decimal("400.00")

    def test_s9_negative_inventory(self):
        """S9: SELL 30@€15 (no prior buy). cost_sold=0, remaining=0, proceeds=450."""
        movements = [
            _make_movement("s9t1", "XNYS:AAPL", "SELL", "30", "450.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["cost_basis_sold_eur"]) == Decimal("0.00")
        assert Decimal(h["remaining_cost_basis_eur"]) == Decimal("0.00")
        assert Decimal(h["total_sale_proceeds_eur"]) == Decimal("450.00")
        assert any(w["type"] == "NEGATIVE_INVENTORY" for w in h["warnings"])

    def test_s10_multi_security(self):
        """S10: AAPL BUY+SELL (remaining=500), TEF BUY only (remaining=800). Sum=1300."""
        movements = [
            _make_movement("s10t1", "XNYS:AAPL", "BUY", "100", "1000.00"),
            _make_movement("s10t2", "XNYS:AAPL", "SELL", "50", "600.00"),
            _make_movement("s10t3", "XMAD:TEF", "BUY", "200", "800.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        s = result["summary"]
        assert Decimal(s["remaining_cost_basis_eur"]) == Decimal("1300.00")
        assert s["total_securities"] == 2

    def test_s11_backward_compat_aliases(self):
        """S11: aliases equal their canonical counterpart."""
        movements = [
            _make_movement("s11t1", "XNYS:AAPL", "BUY", "100", "1000.00", commission_eur="5.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        s = result["summary"]
        assert s["total_purchases_eur"] == s["total_purchase_outflow_eur"]
        assert s["total_sales_eur"] == s["total_sale_proceeds_eur"]
        assert s["total_invested_eur"] == s["total_purchase_outflow_eur"]
        assert s["current_invested_eur"] == s["remaining_cost_basis_eur"]

    def test_s12_avg_cost_stable_after_sell(self):
        """S12: BUY 100@€10 (lot1), BUY 100@€20 (lot2) → SELL 50.
        FIFO: consume 50 from lot1. remaining=lot1(50@€10)+lot2(100@€20)=2500 total.
        avg = 2500/150 = 16.67.
        """
        movements = [
            _make_movement("s12t1", "XNYS:AAPL", "BUY", "100", "1000.00",
                           trade_date="2024-01-01"),
            _make_movement("s12t2", "XNYS:AAPL", "BUY", "100", "2000.00",
                           trade_date="2024-02-01"),
            _make_movement("s12t3", "XNYS:AAPL", "SELL", "50", "900.00",
                           trade_date="2024-03-01"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert h["avg_cost_basis_eur"] == "16.67"

    def test_s13_full_sell_avg_null(self):
        """S13: BUY 100@€10 → SELL 100. avg_cost_basis_eur is None (pool empty)."""
        movements = [
            _make_movement("s13t1", "XNYS:AAPL", "BUY", "100", "1000.00"),
            _make_movement("s13t2", "XNYS:AAPL", "SELL", "100", "1200.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert h["avg_cost_basis_eur"] is None

    def test_s15_superseded_excluded(self):
        """S15: Only active replacement BUY + SELL; SUPERSEDED excluded by query."""
        replacement_buy = _make_movement("s15t1r", "XNYS:AAPL", "BUY", "100", "1000.00")
        sell = _make_movement("s15t2", "XNYS:AAPL", "SELL", "50", "600.00")
        portfolio_svc, securities_svc = _make_services([replacement_buy, sell])
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["total_purchase_outflow_eur"]) == Decimal("1000.00")
        assert Decimal(h["cost_basis_sold_eur"]) == Decimal("500.00")
        assert Decimal(h["remaining_cost_basis_eur"]) == Decimal("500.00")

    def test_new_fields_present_on_holding(self):
        """All new CMP fields must be present in every holding."""
        movements = [_make_movement("pf1", "XNYS:AAPL", "BUY", "10", "100.00")]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        h = svc.compute_holdings()["holdings"][0]
        for field in (
            "total_purchase_outflow_eur", "cost_basis_sold_eur",
            "remaining_cost_basis_eur", "total_sale_proceeds_eur",
            "rights_proceeds_eur", "realized_result_eur",
        ):
            assert field in h, f"Missing holding field: {field}"

    def test_new_fields_present_on_summary(self):
        """All new CMP fields must be present in the summary."""
        portfolio_svc, securities_svc = _make_services([])
        svc = HoldingsService(portfolio_svc, securities_svc)
        s = svc.compute_holdings()["summary"]
        for field in (
            "total_purchase_outflow_eur", "cost_basis_sold_eur",
            "remaining_cost_basis_eur", "total_sale_proceeds_eur",
            "rights_proceeds_eur", "realized_result_eur",
            "has_incomplete_cost_basis",
        ):
            assert field in s, f"Missing summary field: {field}"

    def test_has_incomplete_false_when_complete(self):
        movements = [_make_movement("hif1", "XNYS:AAPL", "BUY", "10", "100.00")]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        assert svc.compute_holdings()["summary"]["has_incomplete_cost_basis"] is False

    def test_has_incomplete_true_when_incomplete(self):
        movements = [
            _make_movement("hit1", "XNYS:AAPL", "BUY", "10", "0",
                           cost_basis_status="INCOMPLETE"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        assert svc.compute_holdings()["summary"]["has_incomplete_cost_basis"] is True
