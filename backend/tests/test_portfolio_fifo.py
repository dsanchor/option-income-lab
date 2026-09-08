"""Dedicated FIFO regression tests.

Covers FIFO-specific scenarios not fully addressed by other test files:
- ADM regression (FIFO-3): multi-lot FIFO depletion with partial lot survival
- Lot ordering tie-break by movement ID
- Zero-cost lots in chronological FIFO order
- Partial lot depletion determinism
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from src.portfolio.cosmos_portfolio import CosmosPortfolioService
from src.portfolio.cosmos_securities import CosmosSecuritiesService
from src.portfolio.holdings_service import HoldingsService


# ---------------------------------------------------------------------------
# Minimal fakes
# ---------------------------------------------------------------------------

class _FakeContainer:
    def __init__(self, movements=None):
        self._movements = list(movements or [])

    def query_items(self, query="", parameters=None,
                    enable_cross_partition_query=True, partition_key=None):
        results = self._movements
        if "NOT IS_DEFINED(c.deleted_at)" in query:
            results = [m for m in results if "deleted_at" not in m]
        if "correction_status" in query:
            results = [
                m for m in results
                if m.get("correction_status", "ACTIVE") == "ACTIVE"
            ]
        if partition_key is not None:
            results = [m for m in results if m.get("account_id") == partition_key]
        return iter(list(results))

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


class _FakeSymbols:
    def query_items(self, **_):
        return iter([])

    def read_item(self, item, partition_key):
        from azure.cosmos.exceptions import CosmosResourceNotFoundError
        raise CosmosResourceNotFoundError(message="not found", response=None)


def _make_svc(movements):
    portfolio_svc = CosmosPortfolioService(_FakeContainer(movements), None)
    securities_svc = CosmosSecuritiesService(_FakeSymbols())
    return HoldingsService(portfolio_svc, securities_svc)


def _buy(mid, security_id, qty, gross_eur, fee="0",
         status="COMPLETE", trade_date="2024-01-01"):
    """BUY movement: net = gross + fee (total cash outflow); engine reads net for cost."""
    return {
        "id": mid,
        "doc_type": "ledger_txn",
        "txn_type": "BUY",
        "security_id": security_id,
        "ticker": security_id.split(":")[-1],
        "trade_date": trade_date,
        "quantity": str(qty),
        "gross": {"amount": str(gross_eur), "currency": "EUR", "eur_amount": str(gross_eur)},
        "fees": {"total": str(fee), "currency": "EUR", "total_eur": str(fee)},
        "net": {
            "amount": str(Decimal(str(gross_eur)) + Decimal(str(fee))),
            "currency": "EUR",
            "eur_amount": str(Decimal(str(gross_eur)) + Decimal(str(fee))),
        },
        "account_id": "_unassigned",
        "cost_basis_status": status,
        "correction_status": "ACTIVE",
        "warnings": [],
    }


def _sell(mid, security_id, qty, gross_eur, fee="0", trade_date="2024-06-01"):
    net = str(Decimal(str(gross_eur)) - Decimal(str(fee)))
    return {
        "id": mid,
        "doc_type": "ledger_txn",
        "txn_type": "SELL",
        "security_id": security_id,
        "ticker": security_id.split(":")[-1],
        "trade_date": trade_date,
        "quantity": str(qty),
        "gross": {"amount": str(gross_eur), "currency": "EUR", "eur_amount": str(gross_eur)},
        "fees": {"total": str(fee), "currency": "EUR", "total_eur": str(fee)},
        "net": {"amount": net, "currency": "EUR", "eur_amount": net},
        "account_id": "_unassigned",
        "sales_type": "ACCIONES",
        "correction_status": "ACTIVE",
        "warnings": [],
    }


def _d(v):
    return Decimal(str(v))


# ---------------------------------------------------------------------------
# FIFO-3: ADM regression — BUY 85, BUY 15, BUY 10, SELL 100
# ---------------------------------------------------------------------------

class TestFifo3AdmRegression:
    """FIFO-3 (ADM): BUY 85@€3500, BUY 15@€800, BUY 10@€490.38, SELL 100.

    FIFO lot depletion:
      - lot1 (b1, 2024-01-01): 85 shares, net=€3500
      - lot2 (b2, 2024-01-15): 15 shares, net=€800
      - lot3 (b3, 2024-02-01): 10 shares, net=€490.38
    SELL 100 (2024-06-01):
      - Consume lot1 entirely (85): cost=3500
      - Consume lot2 entirely (15): cost=800
      - Total cost_sold = 4300
    Remaining: lot3 (10 shares), remaining_cost = 490.38, avg = 49.038.
    """

    def setup_method(self):
        self.svc = _make_svc([
            _buy("b1", "XNYS:ADM", 85, "3500.00", trade_date="2024-01-01"),
            _buy("b2", "XNYS:ADM", 15, "800.00", trade_date="2024-01-15"),
            _buy("b3", "XNYS:ADM", 10, "490.38", trade_date="2024-02-01"),
            _sell("s1", "XNYS:ADM", 100, "5000.00", trade_date="2024-06-01"),
        ])
        self.result = self.svc.compute_holdings()
        self.h = self.result["holdings"][0]

    def test_remaining_shares(self):
        assert _d(self.h["total_shares"]) == _d("10")

    def test_remaining_cost_basis(self):
        """10 shares from lot3 remain: €490.38."""
        assert _d(self.h["remaining_cost_basis_eur"]) == _d("490.38")

    def test_avg_cost_basis(self):
        """avg = 490.38 / 10 = 49.038 → formatted as 49.04 (2dp ROUND_HALF_UP)."""
        # 490.38 / 10 = 49.038 exactly → ROUND_HALF_UP → 49.04
        assert self.h["avg_cost_basis_eur"] == "49.04"

    def test_cost_basis_sold(self):
        """lot1 (85@3500) + lot2 (15@800) fully consumed = 4300."""
        assert _d(self.h["cost_basis_sold_eur"]) == _d("4300.00")

    def test_total_purchase_outflow(self):
        assert _d(self.h["total_purchase_outflow_eur"]) == _d("4790.38")

    def test_realized_result(self):
        """realized = proceeds(5000) - cost_sold(4300) = 700."""
        assert _d(self.h["realized_result_eur"]) == _d("700.00")


# ---------------------------------------------------------------------------
# FIFO lot ordering: same trade_date, tie-break by movement ID (lexicographic)
# ---------------------------------------------------------------------------

class TestFifoIdTieBreak:
    """When two lots have the same trade_date, older ID is consumed first."""

    def test_earlier_id_consumed_first(self):
        """Lot 'aaa' (€10/sh) consumed before lot 'zzz' (€20/sh); same date."""
        svc = _make_svc([
            _buy("aaa", "XNYS:TEST", 50, "500.00", trade_date="2024-01-01"),
            _buy("zzz", "XNYS:TEST", 50, "1000.00", trade_date="2024-01-01"),
            _sell("s1", "XNYS:TEST", 30, "600.00"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        # Consumed 30 from 'aaa' (€10/sh) = 300
        assert _d(h["cost_basis_sold_eur"]) == _d("300.00")
        # Remaining: 20×€10 + 50×€20 = 200 + 1000 = 1200
        assert _d(h["remaining_cost_basis_eur"]) == _d("1200.00")


# ---------------------------------------------------------------------------
# FIFO: zero-cost scrip BUYs in chronological order
# ---------------------------------------------------------------------------

class TestFifoZeroCostScrip:
    """ZERO_COST scrip lots are real zero-cost lots ordered chronologically.

    When a COMPLETE lot precedes a ZERO_COST lot (same date, COMPLETE has
    lexicographically smaller ID), COMPLETE is consumed first.
    """

    def test_complete_then_zero_cost_fifo_order(self):
        """COMPLETE lot consumed before ZERO_COST lot when COMPLETE has smaller ID."""
        svc = _make_svc([
            _buy("a_complete", "XNYS:TEST", 100, "1000.00",
                 status="COMPLETE", trade_date="2024-01-01"),
            _buy("b_zero", "XNYS:TEST", 50, "0",
                 status="ZERO_COST", trade_date="2024-01-01"),
            _sell("s1", "XNYS:TEST", 80, "1600.00"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        # FIFO: 80 from COMPLETE lot (€10/sh) = 800
        assert _d(h["cost_basis_sold_eur"]) == _d("800.00")
        # remaining: 20×€10 (COMPLETE) + 50×€0 (ZERO_COST) = 200
        assert _d(h["remaining_cost_basis_eur"]) == _d("200.00")

    def test_zero_cost_no_incomplete_warning(self):
        """ZERO_COST BUY produces no INCOMPLETE_COST_BASIS warning."""
        svc = _make_svc([
            _buy("z1", "XNYS:TEST", 60, "0", status="ZERO_COST"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        warning_types = [w["type"] for w in h.get("warnings", [])]
        assert "INCOMPLETE_COST_BASIS" not in warning_types
        assert result["summary"]["has_incomplete_cost_basis"] is False


# ---------------------------------------------------------------------------
# FIFO: partial lot depletion is proportional and deterministic
# ---------------------------------------------------------------------------

class TestFifoPartialLotDepletion:
    """Partial lot depletion preserves exact fractional unit cost."""

    def test_partial_lot_cost_determinism(self):
        """BUY 163@€4294.21 → SELL 50: cost = 50 × (4294.21/163), deterministic."""
        svc = _make_svc([
            _buy("b1", "XNYS:TEST", 163, "4294.21"),
            _sell("s1", "XNYS:TEST", 50, "1300.00"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        unit_cost = _d("4294.21") / _d("163")
        expected_sold = (unit_cost * _d("50")).quantize(_d("0.01"), rounding=ROUND_HALF_UP)
        assert _d(h["cost_basis_sold_eur"]) == expected_sold

        # Run again; must give identical result (determinism)
        result2 = svc.compute_holdings()
        h2 = result2["holdings"][0]
        assert h2["cost_basis_sold_eur"] == h["cost_basis_sold_eur"]
