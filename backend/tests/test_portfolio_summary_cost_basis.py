"""Regression tests — Portfolio summary with CMP (moving weighted average) cost-basis semantics.

Requested by: Copilot on behalf of Danny (Lead Architect).
Written by: Basher (independent tester/reviewer).
Decision document: .squad/decisions/inbox/danny-portfolio-summary-cost-basis.md

──────────────────────────────────────────────────────────────────────────────
SCOPE
──────────────────────────────────────────────────────────────────────────────
These tests verify the NEW semantics defined in Danny's contract:
  - CMP (coste medio ponderado / moving weighted average) cost-basis algorithm
  - New fields: total_purchase_outflow_eur, cost_basis_sold_eur,
    remaining_cost_basis_eur, total_sale_proceeds_eur, rights_proceeds_eur,
    realized_result_eur, has_incomplete_cost_basis
  - Changed semantics: current_invested_eur → remaining_cost_basis_eur
  - Changed semantics: avg_cost_basis_eur → CMP (reduces with sells)
  - DERECHOS: proceeds counted; pool unchanged
  - Transfer: global basis unchanged; per-account carried basis coherent
  - Correction/superseded/voided/deleted exclusion
  - Negative inventory: no fabricated cost

Tests are STRICT (no xfail). Tests that target unimplemented features will
fail; failures are intentional defect markers. Do NOT weaken assertions.

──────────────────────────────────────────────────────────────────────────────
EXPECTED FAILURES vs CURRENT IMPLEMENTATION
──────────────────────────────────────────────────────────────────────────────
The following are NOT yet implemented (holdings_service.py uses old algorithm):
  - new summary fields (total_purchase_outflow_eur, cost_basis_sold_eur,
    remaining_cost_basis_eur, total_sale_proceeds_eur, rights_proceeds_eur,
    realized_result_eur, has_incomplete_cost_basis)
  - new per-holding fields (same set)
  - current_invested_eur now equals remaining_cost_basis_eur (CMP)
  - avg_cost_basis_eur is CMP-adjusted (changes when shares are sold)
These will raise KeyError or fail assertions until the implementation is updated.
"""

from __future__ import annotations

import pytest
from decimal import Decimal

from src.portfolio.cosmos_portfolio import CosmosPortfolioService
from src.portfolio.cosmos_securities import CosmosSecuritiesService
from src.portfolio.holdings_service import HoldingsService


# ---------------------------------------------------------------------------
# Fake containers — enhanced to honour correction_status and deleted_at
# ---------------------------------------------------------------------------

class FakePortfolioContainerCMP:
    """Hermetic fake that mirrors the real Cosmos query behaviour needed for
    get_all_movements_for_holdings() — filters both deleted_at and SUPERSEDED/VOIDED.
    """

    def __init__(self, movements=None):
        self._movements = list(movements or [])

    def query_items(self, query="", parameters=None,
                    enable_cross_partition_query=True, partition_key=None):
        results = list(self._movements)
        # Soft-delete filter
        if "NOT IS_DEFINED(c.deleted_at)" in query:
            results = [m for m in results if "deleted_at" not in m]
        # Correction status filter (SUPERSEDED and VOIDED are excluded)
        if "correction_status" in query:
            results = [
                m for m in results
                if m.get("correction_status", "ACTIVE") == "ACTIVE"
            ]
        if partition_key is not None:
            results = [m for m in results if m.get("account_id") == partition_key]
        return iter(results)

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


class FakeSymbolsContainerCMP:
    def query_items(self, **_):
        return iter([])

    def read_item(self, item, partition_key):
        from azure.cosmos.exceptions import CosmosResourceNotFoundError
        raise CosmosResourceNotFoundError(message="not found", response=None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_svc(movements):
    portfolio_svc = CosmosPortfolioService(
        FakePortfolioContainerCMP(movements), None
    )
    securities_svc = CosmosSecuritiesService(FakeSymbolsContainerCMP())
    return HoldingsService(portfolio_svc, securities_svc)


def _buy(mid, security_id, qty, gross, fee="0", status="COMPLETE",
         date="2024-01-01", account_id="_unassigned"):
    """Build a COMPLETE BUY ledger_txn document."""
    return {
        "id": mid,
        "doc_type": "ledger_txn",
        "txn_type": "BUY",
        "security_id": security_id,
        "ticker": security_id.split(":")[-1],
        "trade_date": date,
        "quantity": str(qty),
        "gross": {"amount": str(gross), "currency": "EUR", "eur_amount": str(gross)},
        "fees": {"total": str(fee), "currency": "EUR", "total_eur": str(fee)},
        "net": {"amount": str(Decimal(str(gross)) + Decimal(str(fee))),
                "currency": "EUR",
                "eur_amount": str(Decimal(str(gross)) + Decimal(str(fee)))},
        "account_id": account_id,
        "cost_basis_status": status,
        "correction_status": "ACTIVE",
        "warnings": [],
    }


def _sell(mid, security_id, qty, gross, fee="0", sales_type="ACCIONES",
          date="2024-02-01", account_id="_unassigned"):
    """Build a SELL ledger_txn document."""
    return {
        "id": mid,
        "doc_type": "ledger_txn",
        "txn_type": "SELL",
        "security_id": security_id,
        "ticker": security_id.split(":")[-1],
        "trade_date": date,
        "quantity": str(qty),
        "gross": {"amount": str(gross), "currency": "EUR", "eur_amount": str(gross)},
        "fees": {"total": str(fee), "currency": "EUR", "total_eur": str(fee)},
        "net": {"amount": str(Decimal(str(gross)) - Decimal(str(fee))),
                "currency": "EUR",
                "eur_amount": str(Decimal(str(gross)) - Decimal(str(fee)))},
        "account_id": account_id,
        "sales_type": sales_type,
        "correction_status": "ACTIVE",
        "warnings": [],
    }


def _transfer_out(mid, security_id, qty, carried_cost, date="2024-03-01",
                  account_id="acct-a"):
    return {
        "id": mid,
        "doc_type": "ledger_txn",
        "txn_type": "TRANSFER_OUT",
        "security_id": security_id,
        "ticker": security_id.split(":")[-1],
        "trade_date": date,
        "quantity": str(qty),
        "transfer_cost_basis_eur": str(carried_cost),
        "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
        "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
        "net": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
        "account_id": account_id,
        "correction_status": "ACTIVE",
        "warnings": [],
    }


def _transfer_in(mid, security_id, qty, carried_cost, date="2024-03-01",
                 account_id="acct-b"):
    return {
        "id": mid,
        "doc_type": "ledger_txn",
        "txn_type": "TRANSFER_IN",
        "security_id": security_id,
        "ticker": security_id.split(":")[-1],
        "trade_date": date,
        "quantity": str(qty),
        "transfer_cost_basis_eur": str(carried_cost),
        "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
        "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
        "net": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
        "account_id": account_id,
        "correction_status": "ACTIVE",
        "warnings": [],
    }


def _d(v):
    return Decimal(str(v))


def _holding(result, security_id):
    """Return the holding dict for a specific security_id."""
    for h in result["holdings"]:
        if h["security_id"] == security_id:
            return h
    raise KeyError(f"Security {security_id!r} not found in holdings")


# ---------------------------------------------------------------------------
# S1 — Single BUY: purchase outflow and remaining basis equal cost including
#      commission.
# ---------------------------------------------------------------------------

class TestS1SingleBuy:
    """S1: BUY 100@gross €1005 (€5 fee; net 1000).
    Expected:
      total_purchase_outflow_eur  = 1005
      cost_basis_sold_eur         = 0
      remaining_cost_basis_eur    = 1005
      total_sale_proceeds_eur     = 0
      realized_result_eur         = 0
      current_invested_eur        = 1005  (alias of remaining_cost_basis_eur)
      has_incomplete_cost_basis   = False
    """

    def setup_method(self):
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 100, "1000.00", fee="5.00"),
        ])
        self.result = svc.compute_holdings()
        self.s = self.result["summary"]
        self.h = _holding(self.result, "XNYS:AAPL")

    # --- summary new fields ---

    def test_summary_total_purchase_outflow(self):
        assert self.s["total_purchase_outflow_eur"] == "1005.00"

    def test_summary_cost_basis_sold_zero(self):
        assert self.s["cost_basis_sold_eur"] == "0.00"

    def test_summary_remaining_cost_basis(self):
        assert self.s["remaining_cost_basis_eur"] == "1005.00"

    def test_summary_total_sale_proceeds_zero(self):
        assert self.s["total_sale_proceeds_eur"] == "0.00"

    def test_summary_realized_result_zero(self):
        assert self.s["realized_result_eur"] == "0.00"

    def test_summary_current_invested_equals_remaining(self):
        """current_invested_eur must equal remaining_cost_basis_eur."""
        assert self.s["current_invested_eur"] == self.s["remaining_cost_basis_eur"]

    def test_summary_has_incomplete_cost_basis_false(self):
        assert self.s["has_incomplete_cost_basis"] is False

    # --- per-holding new fields ---

    def test_holding_total_purchase_outflow(self):
        assert self.h["total_purchase_outflow_eur"] == "1005.00"

    def test_holding_cost_basis_sold_zero(self):
        assert self.h["cost_basis_sold_eur"] == "0.00"

    def test_holding_remaining_cost_basis(self):
        assert self.h["remaining_cost_basis_eur"] == "1005.00"

    def test_holding_total_sale_proceeds_zero(self):
        assert self.h["total_sale_proceeds_eur"] == "0.00"

    def test_holding_realized_result_zero(self):
        assert self.h["realized_result_eur"] == "0.00"

    # --- existing fields still correct ---

    def test_total_shares(self):
        assert _d(self.h["total_shares"]) == _d("100")

    def test_cost_basis_status_complete(self):
        assert self.h["cost_basis_status"] == "COMPLETE"


# ---------------------------------------------------------------------------
# S2 — Partial SELL ACCIONES: remove shares at moving weighted average cost;
#      sale proceeds separate; realized result correct.
# ---------------------------------------------------------------------------

class TestS2PartialSellAcciones:
    """S2: BUY 100@gross €1005 (€5 fee) → SELL 30@€15 (€3 fee).
    avg_cost = 1005/100 = 10.05
    cost_sold = 30 × 10.05 = 301.50
    remaining  = 1005 − 301.50 = 703.50
    sale_proceeds = 450 − 3 = 447.00
    realized  = 447.00 − 301.50 = 145.50
    """

    def setup_method(self):
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 100, "1000.00", fee="5.00"),
            _sell("s1", "XNYS:AAPL", 30, "450.00", fee="3.00"),
        ])
        self.result = svc.compute_holdings()
        self.s = self.result["summary"]
        self.h = _holding(self.result, "XNYS:AAPL")

    def test_summary_cost_basis_sold(self):
        assert _d(self.s["cost_basis_sold_eur"]) == _d("301.50")

    def test_summary_remaining_cost_basis(self):
        assert _d(self.s["remaining_cost_basis_eur"]) == _d("703.50")

    def test_summary_total_sale_proceeds(self):
        assert _d(self.s["total_sale_proceeds_eur"]) == _d("447.00")

    def test_summary_realized_result(self):
        assert _d(self.s["realized_result_eur"]) == _d("145.50")

    def test_summary_current_invested_equals_remaining(self):
        assert self.s["current_invested_eur"] == self.s["remaining_cost_basis_eur"]

    def test_holding_cost_basis_sold(self):
        assert _d(self.h["cost_basis_sold_eur"]) == _d("301.50")

    def test_holding_remaining_cost_basis(self):
        assert _d(self.h["remaining_cost_basis_eur"]) == _d("703.50")

    def test_holding_total_sale_proceeds(self):
        assert _d(self.h["total_sale_proceeds_eur"]) == _d("447.00")

    def test_holding_realized_result(self):
        assert _d(self.h["realized_result_eur"]) == _d("145.50")

    def test_remaining_shares(self):
        assert _d(self.h["total_shares"]) == _d("70")

    def test_avg_cost_basis_unchanged_after_sell(self):
        """CMP avg does not change when selling (pool proportionally reduced).
        avg = pool_cost / pool_shares = 703.50 / 70 = 10.05
        """
        assert self.h["avg_cost_basis_eur"] == "10.05"


# ---------------------------------------------------------------------------
# S3 — Full exit leaves zero remaining basis (within Decimal exactness).
# ---------------------------------------------------------------------------

class TestS3FullExit:
    """S3: BUY 100@€10 (€0 fee) → SELL 100@€15 (€0 fee).
    remaining = 0, cost_sold = 1000, realized = 500.
    """

    def setup_method(self):
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 100, "1000.00"),
            _sell("s1", "XNYS:AAPL", 100, "1500.00"),
        ])
        self.result = svc.compute_holdings()
        self.s = self.result["summary"]
        self.h = _holding(self.result, "XNYS:AAPL")

    def test_remaining_cost_basis_exactly_zero(self):
        assert _d(self.s["remaining_cost_basis_eur"]) == _d("0.00")

    def test_holding_remaining_cost_basis_zero(self):
        assert _d(self.h["remaining_cost_basis_eur"]) == _d("0.00")

    def test_cost_basis_sold(self):
        assert _d(self.s["cost_basis_sold_eur"]) == _d("1000.00")

    def test_realized_result(self):
        assert _d(self.s["realized_result_eur"]) == _d("500.00")

    def test_current_invested_zero_after_full_exit(self):
        """current_invested_eur must be 0 after selling all shares (not negative)."""
        assert self.s["current_invested_eur"] == self.s["remaining_cost_basis_eur"]
        assert _d(self.s["current_invested_eur"]) == _d("0.00")

    def test_avg_cost_null_after_full_exit(self):
        """No remaining shares → avg_cost_basis_eur is null."""
        assert self.h["avg_cost_basis_eur"] is None

    def test_total_shares_zero(self):
        assert _d(self.h["total_shares"]) == _d("0")


# ---------------------------------------------------------------------------
# S4 — Multiple BUYs before/after sales: chronological moving average pool.
# ---------------------------------------------------------------------------

class TestS4MultipleBuysAndSale:
    """S4: BUY 100@€10 (lot1, 2024-01-01), BUY 50@€20 (lot2, 2024-01-02) → SELL 60.
    FIFO: consume 60 from lot1.
    cost_sold = 60×10 = 600.00
    remaining = lot1(40×€10) + lot2(50×€20) = 400+1000 = 1400.00
    sale_proceeds = 1080.00
    realized  = 1080 − 600 = 480.00
    avg after sell = 1400/90 = 15.56
    """

    def setup_method(self):
        svc = _make_svc([
            _buy("b1", "XMAD:TEF", 100, "1000.00", date="2024-01-01"),
            _buy("b2", "XMAD:TEF", 50, "1000.00", date="2024-01-02"),
            _sell("s1", "XMAD:TEF", 60, "1080.00", date="2024-03-01"),
        ])
        self.result = svc.compute_holdings()
        self.s = self.result["summary"]
        self.h = _holding(self.result, "XMAD:TEF")

    def test_summary_total_purchase_outflow(self):
        assert _d(self.s["total_purchase_outflow_eur"]) == _d("2000.00")

    def test_summary_cost_basis_sold(self):
        assert _d(self.s["cost_basis_sold_eur"]) == _d("600.00")

    def test_summary_remaining_cost_basis(self):
        assert _d(self.s["remaining_cost_basis_eur"]) == _d("1400.00")

    def test_summary_realized_result(self):
        assert _d(self.s["realized_result_eur"]) == _d("480.00")

    def test_remaining_shares(self):
        assert _d(self.h["total_shares"]) == _d("90")

    def test_avg_cost_basis_after_sell(self):
        """After FIFO sell: avg = 1400/90 = 15.56."""
        assert self.h["avg_cost_basis_eur"] == "15.56"


# ---------------------------------------------------------------------------
# S5 — SELL DERECHOS: rights proceeds and realized result increase;
#      shares/cost pool unchanged.
# ---------------------------------------------------------------------------

class TestS5SellDerechos:
    """S5: BUY 100@€10 (€0) → SELL DERECHOS 20@€5 (€0).
    Pool unchanged: 100 shares, €1000.
    rights_proceeds = 100, sale_proceeds = 100.
    cost_sold = 0 (DERECHOS don't consume pool).
    realized = 100 (rights income with no cost).
    """

    def setup_method(self):
        svc = _make_svc([
            _buy("b1", "XNYS:MSFT", 100, "1000.00"),
            _sell("s1", "XNYS:MSFT", 20, "100.00", sales_type="DERECHOS"),
        ])
        self.result = svc.compute_holdings()
        self.s = self.result["summary"]
        self.h = _holding(self.result, "XNYS:MSFT")

    def test_shares_unchanged(self):
        """DERECHOS sale does NOT decrement share count."""
        assert _d(self.h["total_shares"]) == _d("100")

    def test_cost_pool_unchanged(self):
        """DERECHOS sale does NOT consume from cost pool."""
        assert _d(self.h["remaining_cost_basis_eur"]) == _d("1000.00")

    def test_cost_basis_sold_zero(self):
        assert _d(self.h["cost_basis_sold_eur"]) == _d("0.00")

    def test_rights_proceeds(self):
        assert _d(self.h["rights_proceeds_eur"]) == _d("100.00")

    def test_total_sale_proceeds_equals_rights(self):
        assert _d(self.h["total_sale_proceeds_eur"]) == _d("100.00")

    def test_realized_result_equals_rights(self):
        """Rights income with no assigned cost → realized = 100."""
        assert _d(self.h["realized_result_eur"]) == _d("100.00")

    def test_summary_rights_proceeds(self):
        assert _d(self.s["rights_proceeds_eur"]) == _d("100.00")

    def test_summary_realized(self):
        assert _d(self.s["realized_result_eur"]) == _d("100.00")


# ---------------------------------------------------------------------------
# S6 — Mixed ACCIONES/DERECHOS totals.
# ---------------------------------------------------------------------------

class TestS6MixedAccionesDerechos:
    """S6: BUY 100@€10 → SELL 30 ACCIONES@€15 → SELL DERECHOS 10@€5.
    cost_sold = 30×10 = 300, remaining = 700
    sale_proceeds = 450+50 = 500
    rights = 50
    realized = 500 − 300 = 200
    """

    def setup_method(self):
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 100, "1000.00"),
            _sell("s1", "XNYS:AAPL", 30, "450.00", sales_type="ACCIONES"),
            _sell("s2", "XNYS:AAPL", 10, "50.00", sales_type="DERECHOS"),
        ])
        self.result = svc.compute_holdings()
        self.s = self.result["summary"]
        self.h = _holding(self.result, "XNYS:AAPL")

    def test_shares_only_acciones_decremented(self):
        assert _d(self.h["total_shares"]) == _d("70")

    def test_cost_basis_sold(self):
        assert _d(self.h["cost_basis_sold_eur"]) == _d("300.00")

    def test_remaining_cost_basis(self):
        assert _d(self.h["remaining_cost_basis_eur"]) == _d("700.00")

    def test_rights_proceeds(self):
        assert _d(self.h["rights_proceeds_eur"]) == _d("50.00")

    def test_total_sale_proceeds(self):
        assert _d(self.h["total_sale_proceeds_eur"]) == _d("500.00")

    def test_realized_result(self):
        assert _d(self.h["realized_result_eur"]) == _d("200.00")

    def test_summary_rights_proceeds(self):
        assert _d(self.s["rights_proceeds_eur"]) == _d("50.00")

    def test_summary_total_sale_proceeds(self):
        assert _d(self.s["total_sale_proceeds_eur"]) == _d("500.00")


# ---------------------------------------------------------------------------
# S7 — Zero-cost/incomplete acquisitions and later sales produce explicit
#      incomplete flag without fabricated cost.
# ---------------------------------------------------------------------------

class TestS7IncompleteCostBasis:
    """S7: BUY 50@€0 (INCOMPLETE, id=b1) + BUY 50@€10 (COMPLETE, id=b2) → SELL 70@€15.
    FIFO order: b1 (INCOMPLETE) first, then b2 (COMPLETE). Both same date; id b1 < b2.
    Sell 70: consume 50 from INCOMPLETE lot (cost=0) + 20 from COMPLETE lot (cost=200).
    cost_sold = 200, remaining = 30×€10 = 300.
    has_incomplete_cost_basis = True.
    """

    def setup_method(self):
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 50, "0", status="INCOMPLETE"),
            _buy("b2", "XNYS:AAPL", 50, "500.00"),
            _sell("s1", "XNYS:AAPL", 70, "1050.00"),
        ])
        self.result = svc.compute_holdings()
        self.s = self.result["summary"]
        self.h = _holding(self.result, "XNYS:AAPL")

    def test_holding_cost_basis_status_incomplete(self):
        assert self.h["cost_basis_status"] == "INCOMPLETE"

    def test_summary_has_incomplete_cost_basis_true(self):
        assert self.s["has_incomplete_cost_basis"] is True

    def test_cost_basis_sold_only_paid_portion(self):
        """FIFO: 50 INCOMPLETE (cost=0) + 20 COMPLETE (cost=200) = 200 total."""
        assert _d(self.h["cost_basis_sold_eur"]) == _d("200.00")

    def test_remaining_cost_basis(self):
        """30 shares remain from COMPLETE lot: 30 × €10 = 300."""
        assert _d(self.h["remaining_cost_basis_eur"]) == _d("300.00")

    def test_no_fabricated_cost_for_incomplete_shares(self):
        """cost_basis_sold must not exceed the known cost pool (no fabrication)."""
        assert _d(self.h["cost_basis_sold_eur"]) <= _d("500.00")

    def test_warning_zero_cost_acquisition(self):
        warning_types = [w["type"] for w in self.h["warnings"]]
        # INCOMPLETE (genuinely unknown cost) emits INCOMPLETE_COST_BASIS, not ZERO_COST_ACQUISITION.
        assert "INCOMPLETE_COST_BASIS" in warning_types

    def test_remaining_shares(self):
        assert _d(self.h["total_shares"]) == _d("30")


# ---------------------------------------------------------------------------
# S8 — Transfer between accounts: global basis/results unchanged;
#      per-account carried basis coherent.
# ---------------------------------------------------------------------------

class TestS8TransferPreservesGlobalBasis:
    """S8: BUY 100@€10 acct-A → TRANSFER_OUT 40 acct-A → TRANSFER_IN 40 acct-B (carried=400).
    Global: remaining = 1000 (unchanged).
    acct-A: remaining = 600.
    acct-B: remaining = 400.
    Transfers do NOT appear in purchase_outflow or sale_proceeds.
    """

    def setup_method(self):
        self._movements = [
            _buy("b1", "XNYS:AAPL", 100, "1000.00",
                 date="2024-01-01", account_id="acct-a"),
            _transfer_out("to1", "XNYS:AAPL", 40, "400.00",
                          date="2024-02-01", account_id="acct-a"),
            _transfer_in("ti1", "XNYS:AAPL", 40, "400.00",
                         date="2024-02-01", account_id="acct-b"),
        ]

    def _run(self, account_id=None):
        svc = _make_svc(self._movements)
        return svc.compute_holdings(account_id=account_id)

    def test_global_remaining_cost_basis_unchanged(self):
        result = self._run()
        s = result["summary"]
        assert _d(s["remaining_cost_basis_eur"]) == _d("1000.00")

    def test_global_current_invested_unchanged(self):
        result = self._run()
        s = result["summary"]
        assert _d(s["current_invested_eur"]) == _d("1000.00")

    def test_global_total_purchase_outflow_excludes_transfers(self):
        """TRANSFER_IN/OUT must NOT appear in total_purchase_outflow."""
        result = self._run()
        s = result["summary"]
        assert _d(s["total_purchase_outflow_eur"]) == _d("1000.00")

    def test_global_sale_proceeds_excludes_transfers(self):
        """TRANSFER_OUT must NOT appear in total_sale_proceeds."""
        result = self._run()
        s = result["summary"]
        assert _d(s["total_sale_proceeds_eur"]) == _d("0.00")

    def test_acct_a_remaining_after_transfer_out(self):
        result = self._run(account_id="acct-a")
        h = _holding(result, "XNYS:AAPL")
        assert _d(h["remaining_cost_basis_eur"]) == _d("600.00")

    def test_acct_b_remaining_equals_carried_cost(self):
        result = self._run(account_id="acct-b")
        h = _holding(result, "XNYS:AAPL")
        assert _d(h["remaining_cost_basis_eur"]) == _d("400.00")

    def test_global_shares_unchanged(self):
        result = self._run()
        h = _holding(result, "XNYS:AAPL")
        assert _d(h["total_shares"]) == _d("100")


# ---------------------------------------------------------------------------
# S9 — Negative inventory does not create negative remaining basis or fake
#      realized cost.
# ---------------------------------------------------------------------------

class TestS9NegativeInventory:
    """S9: SELL 30@€15 with no prior BUY.
    pool_shares = 0 → cost_sold = 0, remaining = 0.
    sale_proceeds = 450.
    Warning NEGATIVE_INVENTORY emitted.
    remaining_cost_basis_eur must be 0 (not negative).
    """

    def setup_method(self):
        svc = _make_svc([
            _sell("s1", "XNYS:AAPL", 30, "450.00"),
        ])
        self.result = svc.compute_holdings()
        self.s = self.result["summary"]
        self.h = _holding(self.result, "XNYS:AAPL")

    def test_negative_inventory_warning(self):
        warning_types = [w["type"] for w in self.h["warnings"]]
        assert "NEGATIVE_INVENTORY" in warning_types

    def test_cost_basis_sold_not_fabricated(self):
        """No buy data → cost_sold must be 0, not fabricated."""
        assert _d(self.h["cost_basis_sold_eur"]) == _d("0.00")

    def test_remaining_cost_basis_not_negative(self):
        """Remaining cost must be ≥ 0 (no negative cost basis)."""
        assert _d(self.h["remaining_cost_basis_eur"]) >= _d("0.00")

    def test_sale_proceeds_recorded(self):
        assert _d(self.h["total_sale_proceeds_eur"]) == _d("450.00")

    def test_negative_shares(self):
        assert _d(self.h["total_shares"]) == _d("-30")


# ---------------------------------------------------------------------------
# S10 — Multi-security aggregation: portfolio-wide summary is sum of holdings.
# ---------------------------------------------------------------------------

class TestS10MultiSecurityAggregation:
    """S10: AAPL: BUY 10@€100 → SELL 5@€120. TEF: BUY 100@€4 (no sell).
    AAPL: purchase_outflow=1000, cost_sold=500, remaining=500, proceeds=600, realized=100.
    TEF:  purchase_outflow=400,  cost_sold=0,   remaining=400, proceeds=0,   realized=0.
    Summary: outflow=1400, cost_sold=500, remaining=900, proceeds=600, realized=100.
    """

    def setup_method(self):
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 10, "1000.00"),
            _sell("s1", "XNYS:AAPL", 5, "600.00"),
            _buy("b2", "XMAD:TEF", 100, "400.00"),
        ])
        self.result = svc.compute_holdings()
        self.s = self.result["summary"]

    def test_total_purchase_outflow(self):
        assert _d(self.s["total_purchase_outflow_eur"]) == _d("1400.00")

    def test_total_cost_basis_sold(self):
        assert _d(self.s["cost_basis_sold_eur"]) == _d("500.00")

    def test_total_remaining_cost_basis(self):
        assert _d(self.s["remaining_cost_basis_eur"]) == _d("900.00")

    def test_total_sale_proceeds(self):
        assert _d(self.s["total_sale_proceeds_eur"]) == _d("600.00")

    def test_total_realized(self):
        assert _d(self.s["realized_result_eur"]) == _d("100.00")

    def test_total_securities(self):
        assert self.s["total_securities"] == 2

    def test_holdings_count(self):
        assert len(self.result["holdings"]) == 2


# ---------------------------------------------------------------------------
# S11 — Backward compatibility aliases.
# ---------------------------------------------------------------------------

class TestS11BackwardCompatAliases:
    """S11: BUY 100@gross €1005 (€5 fee).
    Old alias fields must still be present and equal their new counterparts.
    """

    def setup_method(self):
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 100, "1000.00", fee="5.00"),
        ])
        self.result = svc.compute_holdings()
        self.s = self.result["summary"]
        self.h = _holding(self.result, "XNYS:AAPL")

    def test_summary_total_purchases_alias(self):
        """total_purchases_eur is an alias for total_purchase_outflow_eur."""
        assert self.s["total_purchases_eur"] == self.s["total_purchase_outflow_eur"]

    def test_summary_total_sales_alias(self):
        """total_sales_eur is an alias for total_sale_proceeds_eur."""
        assert self.s["total_sales_eur"] == self.s["total_sale_proceeds_eur"]

    def test_summary_total_invested_alias(self):
        """total_invested_eur is an alias for total_purchase_outflow_eur."""
        assert self.s["total_invested_eur"] == self.s["total_purchase_outflow_eur"]

    def test_summary_current_invested_alias(self):
        """current_invested_eur is an alias for remaining_cost_basis_eur."""
        assert self.s["current_invested_eur"] == self.s["remaining_cost_basis_eur"]

    def test_holding_total_invested_alias(self):
        """Per-holding: total_invested_eur equals total_purchase_outflow_eur."""
        assert self.h["total_invested_eur"] == self.h["total_purchase_outflow_eur"]

    def test_holding_total_purchases_alias(self):
        """Per-holding: total_purchases_eur equals total_purchase_outflow_eur."""
        assert self.h["total_purchases_eur"] == self.h["total_purchase_outflow_eur"]

    def test_holding_total_sales_alias(self):
        """Per-holding: total_sales_eur equals total_sale_proceeds_eur."""
        assert self.h["total_sales_eur"] == self.h["total_sale_proceeds_eur"]

    # New field explicit values
    def test_new_outflow_value(self):
        assert self.s["total_purchase_outflow_eur"] == "1005.00"

    def test_new_remaining_value(self):
        assert self.s["remaining_cost_basis_eur"] == "1005.00"

    def test_old_purchases_value_unchanged(self):
        """Old total_purchases_eur must still be 1005.00 (no numeric regression)."""
        assert self.s["total_purchases_eur"] == "1005.00"


# ---------------------------------------------------------------------------
# S12 — avg_cost_basis_eur changes (or stays) with CMP.
# ---------------------------------------------------------------------------

class TestS12AvgCostCmp:
    """S12: BUY 100@€10 (lot1, 2024-01-01), BUY 100@€20 (lot2, 2024-01-02) → SELL 50.
    FIFO: consume 50 from lot1.
    cost_sold = 500. remaining = lot1(50×€10) + lot2(100×€20) = 500+2000 = 2500.
    avg after sell = 2500/150 = 16.67.
    """

    def setup_method(self):
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 100, "1000.00", date="2024-01-01"),
            _buy("b2", "XNYS:AAPL", 100, "2000.00", date="2024-01-02"),
            _sell("s1", "XNYS:AAPL", 50, "750.00", date="2024-02-01"),
        ])
        self.result = svc.compute_holdings()
        self.h = _holding(self.result, "XNYS:AAPL")

    def test_avg_cost_after_sell(self):
        """FIFO avg after sell = 2500/150 = 16.67."""
        assert self.h["avg_cost_basis_eur"] == "16.67"

    def test_remaining_shares(self):
        assert _d(self.h["total_shares"]) == _d("150")

    def test_remaining_cost_basis(self):
        assert _d(self.h["remaining_cost_basis_eur"]) == _d("2500.00")


# ---------------------------------------------------------------------------
# S13 — Full exit leaves avg null.
# ---------------------------------------------------------------------------

class TestS13FullExitAvgNull:
    """S13: BUY 100@€10 → SELL 100@€12.
    pool_shares = 0 → avg_cost_basis_eur = null.
    """

    def setup_method(self):
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 100, "1000.00"),
            _sell("s1", "XNYS:AAPL", 100, "1200.00"),
        ])
        self.result = svc.compute_holdings()
        self.h = _holding(self.result, "XNYS:AAPL")

    def test_avg_cost_null(self):
        assert self.h["avg_cost_basis_eur"] is None

    def test_remaining_cost_basis_zero(self):
        assert _d(self.h["remaining_cost_basis_eur"]) == _d("0.00")

    def test_total_shares_zero(self):
        assert _d(self.h["total_shares"]) == _d("0")


# ---------------------------------------------------------------------------
# S14 — Soft-delete excluded from all cost-basis computations.
# ---------------------------------------------------------------------------

class TestS14SoftDeleteExclusion:
    """S14: BUY(active) + BUY(deleted) + SELL.
    Only the active BUY contributes to the pool. Deleted BUY is invisible.
    """

    def setup_method(self):
        deleted_buy = _buy("b2", "XNYS:AAPL", 100, "2000.00",
                           fee="0", date="2024-01-02")
        deleted_buy["deleted_at"] = "2024-01-10T00:00:00Z"
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 50, "500.00", date="2024-01-01"),
            deleted_buy,
            _sell("s1", "XNYS:AAPL", 20, "300.00", date="2024-02-01"),
        ])
        self.result = svc.compute_holdings()
        self.s = self.result["summary"]
        self.h = _holding(self.result, "XNYS:AAPL")

    def test_purchase_outflow_excludes_deleted(self):
        """Only the €500 active buy counted; deleted €2000 buy is invisible."""
        assert _d(self.s["total_purchase_outflow_eur"]) == _d("500.00")

    def test_remaining_cost_basis_excludes_deleted(self):
        """pool = 50@€10, sell 20 → remaining = 30 shares × €10 = €300."""
        assert _d(self.h["remaining_cost_basis_eur"]) == _d("300.00")

    def test_cost_basis_sold_excludes_deleted(self):
        """cost_sold = 20 × (500/50) = 200."""
        assert _d(self.h["cost_basis_sold_eur"]) == _d("200.00")

    def test_shares_excludes_deleted(self):
        """50 - 20 = 30 (deleted buy of 100 not counted)."""
        assert _d(self.h["total_shares"]) == _d("30")


# ---------------------------------------------------------------------------
# S15 — Correction/SUPERSEDED exclusion: only replacement (ACTIVE) counts.
# ---------------------------------------------------------------------------

class TestS15SupersededExclusion:
    """S15: BUY(SUPERSEDED) + BUY(ACTIVE replacement) + SELL.
    Only the replacement counts; SUPERSEDED is invisible.
    """

    def setup_method(self):
        superseded_buy = _buy("b-orig", "XNYS:AAPL", 200, "4000.00",
                              fee="0", date="2024-01-01")
        superseded_buy["correction_status"] = "SUPERSEDED"
        replacement_buy = _buy("b-repl", "XNYS:AAPL", 100, "1000.00",
                               fee="0", date="2024-01-01")
        replacement_buy["correction_status"] = "ACTIVE"
        replacement_buy["corrects_movement_id"] = "b-orig"
        svc = _make_svc([
            superseded_buy,
            replacement_buy,
            _sell("s1", "XNYS:AAPL", 30, "450.00"),
        ])
        self.result = svc.compute_holdings()
        self.s = self.result["summary"]
        self.h = _holding(self.result, "XNYS:AAPL")

    def test_purchase_outflow_excludes_superseded(self):
        """Only the ACTIVE replacement buy of €1000 counts; SUPERSEDED €4000 is invisible."""
        assert _d(self.s["total_purchase_outflow_eur"]) == _d("1000.00")

    def test_cost_basis_sold_excludes_superseded(self):
        """pool=100@€10, sell 30 → cost_sold=300."""
        assert _d(self.h["cost_basis_sold_eur"]) == _d("300.00")

    def test_remaining_cost_basis_excludes_superseded(self):
        """remaining=1000−300=700."""
        assert _d(self.h["remaining_cost_basis_eur"]) == _d("700.00")

    def test_shares_excludes_superseded(self):
        """100 - 30 = 70 (SUPERSEDED 200-share buy not counted)."""
        assert _d(self.h["total_shares"]) == _d("70")


# ---------------------------------------------------------------------------
# Commission assignment — buy/sell commissions assigned correctly.
# ---------------------------------------------------------------------------

class TestCommissionAssignment:
    """Buy commission is INCLUDED in pool_cost (via gross_eur which is true gross).
    Sell commission is DEDUCTED from sale_proceeds (not from pool_cost).
    """

    def setup_method(self):
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 100, "1000.00", fee="10.00"),
            _sell("s1", "XNYS:AAPL", 50, "600.00", fee="6.00"),
        ])
        self.result = svc.compute_holdings()
        self.s = self.result["summary"]
        self.h = _holding(self.result, "XNYS:AAPL")

    def test_buy_commission_in_pool_cost(self):
        """BUY gross=1010 (net 1000 + commission 10) → pool_cost=1010."""
        assert _d(self.s["total_purchase_outflow_eur"]) == _d("1010.00")

    def test_sell_commission_deducted_from_proceeds(self):
        """SELL gross=600, commission=6 → proceeds=594."""
        assert _d(self.s["total_sale_proceeds_eur"]) == _d("594.00")

    def test_cost_basis_sold_uses_pool_avg_including_buy_fee(self):
        """avg = 1010/100 = 10.10; cost_sold = 50 × 10.10 = 505.00."""
        assert _d(self.h["cost_basis_sold_eur"]) == _d("505.00")

    def test_remaining_cost_basis_includes_buy_fee(self):
        """remaining = 1010 − 505 = 505.00."""
        assert _d(self.h["remaining_cost_basis_eur"]) == _d("505.00")

    def test_realized_result(self):
        """realized = 594 − 505 = 89.00."""
        assert _d(self.h["realized_result_eur"]) == _d("89.00")


# ---------------------------------------------------------------------------
# Stable ordering — same trade_date movements processed in id order.
# ---------------------------------------------------------------------------

class TestSameDateOrdering:
    """Two BUYs on the same date: chronological within day must be stable (by id).
    BUY-A: 100@€10 (€0), BUY-B: 100@€20 (€0) both on 2024-01-01.
    Expected avg = (1000+2000)/200 = 15.00 regardless of id ordering.
    """

    def _run_ids(self, id_order):
        movements_map = {
            "b-a": _buy("b-a", "XNYS:AAPL", 100, "1000.00",
                        fee="0", date="2024-01-01"),
            "b-b": _buy("b-b", "XNYS:AAPL", 100, "2000.00",
                        fee="0", date="2024-01-01"),
        }
        svc = _make_svc([movements_map[i] for i in id_order])
        return svc.compute_holdings()

    def test_both_orders_give_same_pool_cost(self):
        r1 = self._run_ids(["b-a", "b-b"])
        r2 = self._run_ids(["b-b", "b-a"])
        h1 = _holding(r1, "XNYS:AAPL")
        h2 = _holding(r2, "XNYS:AAPL")
        # Pool cost must be 3000 in both orderings
        assert _d(h1["remaining_cost_basis_eur"]) == _d("3000.00")
        assert _d(h2["remaining_cost_basis_eur"]) == _d("3000.00")

    def test_same_avg_regardless_of_storage_order(self):
        r1 = self._run_ids(["b-a", "b-b"])
        r2 = self._run_ids(["b-b", "b-a"])
        h1 = _holding(r1, "XNYS:AAPL")
        h2 = _holding(r2, "XNYS:AAPL")
        assert h1["avg_cost_basis_eur"] == h2["avg_cost_basis_eur"]


# ---------------------------------------------------------------------------
# API field presence — all contract-mandated fields must be in the response.
# ---------------------------------------------------------------------------

class TestApiFieldPresence:
    """Verify that every field from the contract is present on summary and holding."""

    SUMMARY_REQUIRED = {
        "total_securities",
        "total_invested_eur",
        "total_purchases_eur",
        "total_purchase_outflow_eur",
        "cost_basis_sold_eur",
        "remaining_cost_basis_eur",
        "total_sales_eur",
        "total_sale_proceeds_eur",
        "rights_proceeds_eur",
        "realized_result_eur",
        "current_invested_eur",
        "total_dividends_eur",
        "has_incomplete_cost_basis",
    }

    HOLDING_REQUIRED = {
        "security_id",
        "ticker",
        "total_shares",
        "avg_cost_basis_eur",
        "cost_basis_status",
        "total_invested_eur",
        "total_purchases_eur",
        "total_purchase_outflow_eur",
        "cost_basis_sold_eur",
        "remaining_cost_basis_eur",
        "total_sales_eur",
        "total_sale_proceeds_eur",
        "rights_proceeds_eur",
        "realized_result_eur",
        "total_dividends_eur",
        "accounts",
        "warnings",
    }

    def setup_method(self):
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 10, "100.00"),
        ])
        self.result = svc.compute_holdings()

    def test_all_summary_fields_present(self):
        missing = self.SUMMARY_REQUIRED - set(self.result["summary"].keys())
        assert missing == set(), f"Missing summary fields: {sorted(missing)}"

    def test_all_holding_fields_present(self):
        h = self.result["holdings"][0]
        missing = self.HOLDING_REQUIRED - set(h.keys())
        assert missing == set(), f"Missing holding fields: {sorted(missing)}"


# ---------------------------------------------------------------------------
# Multiple BUYs interleaved with sales — chronological CMP pool.
# ---------------------------------------------------------------------------

class TestMultipleBuysInterleavedSales:
    """BUY1@€10 → SELL_PARTIAL → BUY2@€20 → SELL_REMAINDER.
    Verifies that CMP pool is updated at each BUY before the next SELL.
    """

    def setup_method(self):
        # BUY1: 100@€10 (pool=100, cost=1000, avg=10)
        # SELL1: 50@€12  (cost_sold=50×10=500, pool=50, cost=500)
        # BUY2: 50@€20   (pool=100, cost=500+1000=1500, avg=15)
        # SELL2: 100@€18 (cost_sold=100×15=1500, pool=0, cost=0)
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 100, "1000.00", date="2024-01-01"),
            _sell("s1", "XNYS:AAPL", 50, "600.00", date="2024-02-01"),
            _buy("b2", "XNYS:AAPL", 50, "1000.00", date="2024-03-01"),
            _sell("s2", "XNYS:AAPL", 100, "1800.00", date="2024-04-01"),
        ])
        self.result = svc.compute_holdings()
        self.s = self.result["summary"]
        self.h = _holding(self.result, "XNYS:AAPL")

    def test_total_purchase_outflow(self):
        assert _d(self.s["total_purchase_outflow_eur"]) == _d("2000.00")

    def test_total_cost_basis_sold(self):
        # SELL1: 50×10=500, SELL2: 100×15=1500 → total=2000
        assert _d(self.s["cost_basis_sold_eur"]) == _d("2000.00")

    def test_remaining_cost_basis_zero(self):
        assert _d(self.s["remaining_cost_basis_eur"]) == _d("0.00")

    def test_total_sale_proceeds(self):
        assert _d(self.s["total_sale_proceeds_eur"]) == _d("2400.00")

    def test_realized_result(self):
        # 2400 - 2000 = 400
        assert _d(self.s["realized_result_eur"]) == _d("400.00")

    def test_total_shares_zero(self):
        assert _d(self.h["total_shares"]) == _d("0")


# ---------------------------------------------------------------------------
# Portfolio-wide summary unaffected by per-account filtering contract.
# ---------------------------------------------------------------------------

class TestPortfolioWideSummaryAccountFilter:
    """Global (no account filter) vs per-account must not contaminate each other.
    Global remaining = acct-a remaining + acct-b remaining.
    """

    def setup_method(self):
        self._movements = [
            _buy("b1", "XNYS:AAPL", 100, "1000.00",
                 date="2024-01-01", account_id="acct-a"),
            _buy("b2", "XNYS:AAPL", 50, "600.00",
                 date="2024-01-02", account_id="acct-b"),
        ]

    def test_global_remaining_equals_sum_of_per_account(self):
        svc_global = _make_svc(self._movements)
        svc_a = _make_svc(self._movements)
        svc_b = _make_svc(self._movements)
        r_global = svc_global.compute_holdings()
        r_a = svc_a.compute_holdings(account_id="acct-a")
        r_b = svc_b.compute_holdings(account_id="acct-b")
        global_rem = _d(r_global["summary"]["remaining_cost_basis_eur"])
        a_rem = _d(r_a["summary"]["remaining_cost_basis_eur"])
        b_rem = _d(r_b["summary"]["remaining_cost_basis_eur"])
        assert global_rem == a_rem + b_rem

    def test_global_purchase_outflow_equals_sum(self):
        svc_global = _make_svc(self._movements)
        svc_a = _make_svc(self._movements)
        svc_b = _make_svc(self._movements)
        r_global = svc_global.compute_holdings()
        r_a = svc_a.compute_holdings(account_id="acct-a")
        r_b = svc_b.compute_holdings(account_id="acct-b")
        global_out = _d(r_global["summary"]["total_purchase_outflow_eur"])
        a_out = _d(r_a["summary"]["total_purchase_outflow_eur"])
        b_out = _d(r_b["summary"]["total_purchase_outflow_eur"])
        assert global_out == a_out + b_out


# ---------------------------------------------------------------------------
# VOIDED correction_status exclusion.
# ---------------------------------------------------------------------------

class TestVoidedExclusion:
    """Movements with correction_status = 'VOIDED' must be excluded."""

    def setup_method(self):
        voided_buy = _buy("b-void", "XNYS:AAPL", 500, "10000.00")
        voided_buy["correction_status"] = "VOIDED"
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 100, "1000.00"),
            voided_buy,
        ])
        self.result = svc.compute_holdings()
        self.s = self.result["summary"]
        self.h = _holding(self.result, "XNYS:AAPL")

    def test_purchase_outflow_excludes_voided(self):
        assert _d(self.s["total_purchase_outflow_eur"]) == _d("1000.00")

    def test_shares_excludes_voided(self):
        assert _d(self.h["total_shares"]) == _d("100")


# ---------------------------------------------------------------------------
# Current_invested_eur semantic guard — must equal remaining_cost_basis_eur
# (not purchases - sale_proceeds).
# ---------------------------------------------------------------------------

class TestCurrentInvestedSemanticChange:
    """The key breaking change: current_invested_eur = remaining_cost_basis_eur
    (CMP pool), NOT purchases - sale_proceeds.

    Old formula gave 665.00; new formula gives 703.50.
    This test documents the NEW contract value.
    """

    def setup_method(self):
        # BUY 100@gross €1000 (€5 fee; net €1005) → SELL 30@€15 (€3 fee)
        # Old: 1005 - (450-3) = 1005 - 447 = 558. (Wrong!)
        # New (FIFO): avg=10.05, cost_sold=30×10.05=301.50, remaining=703.50. (Correct)
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 100, "1000.00", fee="5.00"),
            _sell("s1", "XNYS:AAPL", 30, "450.00", fee="3.00"),
        ])
        self.result = svc.compute_holdings()
        self.s = self.result["summary"]

    def test_current_invested_is_cmp_remaining_not_old_formula(self):
        """current_invested_eur must NOT use old formula (purchases − sale_proceeds)."""
        old_formula_value = _d("1005.00") - _d("447.00")  # = 558.00
        actual = _d(self.s["current_invested_eur"])
        assert actual != old_formula_value, (
            "current_invested_eur still uses old formula (purchases − sale_proceeds). "
            "New contract: current_invested_eur = remaining_cost_basis_eur (CMP)."
        )

    def test_current_invested_equals_cmp_remaining(self):
        """current_invested_eur must equal remaining_cost_basis_eur."""
        assert self.s["current_invested_eur"] == self.s["remaining_cost_basis_eur"]

    def test_current_invested_is_703_50(self):
        """Exact value: 703.50 (CMP remaining after selling 30 shares at avg 10.05)."""
        assert _d(self.s["current_invested_eur"]) == _d("703.50")


# ---------------------------------------------------------------------------
# Current_invested_eur cannot be negative by design (pool_cost >= 0).
# ---------------------------------------------------------------------------

class TestCurrentInvestedNonNegative:
    """After selling all shares at a gain, current_invested_eur must be 0,
    not negative (old formula could produce -300.00).
    """

    def setup_method(self):
        # BUY 100@€5 → SELL 100@€8 (sale_proceeds > purchase → old formula negative)
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 100, "500.00"),
            _sell("s1", "XNYS:AAPL", 100, "800.00"),
        ])
        self.result = svc.compute_holdings()
        self.s = self.result["summary"]

    def test_current_invested_not_negative(self):
        """current_invested_eur must be >= 0 (pool_cost can't be negative)."""
        assert _d(self.s["current_invested_eur"]) >= _d("0.00")

    def test_current_invested_zero_after_full_exit(self):
        assert _d(self.s["current_invested_eur"]) == _d("0.00")

    def test_old_formula_was_negative(self):
        """Document that old formula gave -300.00; new gives 0.00."""
        assert _d(self.s["current_invested_eur"]) != _d("-300.00"), (
            "current_invested_eur still uses old formula which goes negative. "
            "New contract: CMP pool can never be negative."
        )


# ---------------------------------------------------------------------------
# Explicit rights_proceeds_eur = 0.00 when there are no rights sales.
# ---------------------------------------------------------------------------

class TestRightsProceedsZeroDefault:
    def test_rights_proceeds_zero_when_no_derechos(self):
        svc = _make_svc([_buy("b1", "XNYS:AAPL", 100, "1000.00")])
        result = svc.compute_holdings()
        assert result["summary"]["rights_proceeds_eur"] == "0.00"
        h = _holding(result, "XNYS:AAPL")
        assert h["rights_proceeds_eur"] == "0.00"
