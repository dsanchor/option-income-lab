"""Dedicated FIFO regression tests.

Covers FIFO-specific scenarios not fully addressed by other test files:
- ADM regression (FIFO-3): multi-lot FIFO depletion with partial lot survival
- Lot ordering tie-break by movement ID
- Zero-cost lots in chronological FIFO order
- Partial lot depletion determinism
- SHARE_CONSOLIDATION FIFO walk-through (FIFO-SC1..SC3, danny-share-consolidation-
  contract.md §5.2) — TRANSFER_OUT/TRANSFER_IN carried-cost-basis lot creation and
  fractional cash-out FIFO consumption
- SHARE_CONSOLIDATION same-day CA-group leg ordering (FIFO-SC-ORDER-1..4,
  danny-share-consolidation-contract-rev1.md §R1/§R4) — deterministic
  (trade_date, ca_group_id, ca_group_seq, id) sort key, proven with randomized
  movement-ID shuffles so no leg combination can rely on lexicographic luck
"""

from __future__ import annotations

import random
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

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


def _sell(mid, security_id, qty, gross_eur, fee="0", trade_date="2024-06-01",
          ca_group_id=None, ca_group_seq=None):
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
        "ca_group_id": ca_group_id,
        "ca_group_seq": ca_group_seq,
        "warnings": [],
    }


def _transfer_out(mid, security_id, qty, trade_date="2024-05-01",
                   ca_group_id=None, ca_group_seq=None):
    """CONSOLIDATION_OUT leg shape — zero-financial-value TRANSFER_OUT."""
    return {
        "id": mid,
        "doc_type": "ledger_txn",
        "txn_type": "TRANSFER_OUT",
        "security_id": security_id,
        "ticker": security_id.split(":")[-1],
        "trade_date": trade_date,
        "quantity": str(qty),
        "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
        "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
        "net": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
        "account_id": "_unassigned",
        "correction_status": "ACTIVE",
        "ca_group_id": ca_group_id,
        "ca_group_seq": ca_group_seq,
        "warnings": [],
    }


def _transfer_in(mid, security_id, qty, transfer_cost_basis_eur, trade_date="2024-05-01",
                  ca_group_id=None, ca_group_seq=None):
    """CONSOLIDATION_IN leg shape — carries transfer_cost_basis_eur, zero cash value."""
    return {
        "id": mid,
        "doc_type": "ledger_txn",
        "txn_type": "TRANSFER_IN",
        "security_id": security_id,
        "ticker": security_id.split(":")[-1],
        "trade_date": trade_date,
        "quantity": str(qty),
        "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
        "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
        "net": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
        "transfer_cost_basis_eur": str(transfer_cost_basis_eur),
        "account_id": "_unassigned",
        "correction_status": "ACTIVE",
        "ca_group_id": ca_group_id,
        "ca_group_seq": ca_group_seq,
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


# ---------------------------------------------------------------------------
# FIFO-SC: SHARE_CONSOLIDATION walk-through
# (danny-share-consolidation-contract.md §5.2)
# ---------------------------------------------------------------------------

class TestFifoShareConsolidation:
    """CONSOLIDATION_OUT/CONSOLIDATION_IN/FRACTIONAL_CASH_OUT FIFO effects.

    These movements are hand-built here (not via CosmosPortfolioService.
    create_corporate_action) to test the holdings_service FIFO engine in
    isolation, matching the existing pattern in this file.
    """

    def test_fifo_sc1_full_consolidation_with_fractional(self):
        """FIFO-SC1: BUY 50@€100 (lot A) + BUY 22@€110 (lot B) = 72 shares, €7420.
        Consolidate OUT 72 / IN 69.12 (tcb=7420.00) / FRACTIONAL_CASH_OUT 0.12 @ €8.58.
        """
        svc = _make_svc([
            _buy("a1", "XNYS:TEST", 50, "5000.00", trade_date="2024-01-01"),
            _buy("a2", "XNYS:TEST", 22, "2420.00", trade_date="2024-01-15"),
            _transfer_out("co1", "XNYS:TEST", 72, trade_date="2024-05-01"),
            _transfer_in("ci1", "XNYS:TEST", "69.12", "7420.00", trade_date="2024-05-01"),
            _sell("fco1", "XNYS:TEST", "0.12", "8.58", trade_date="2024-05-01"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]

        assert _d(h["total_shares"]) == _d("69.00")
        # cost_basis_sold = 0.12 × (7420.00/69.12) = 12.88 (rounded 2dp)
        unit_cost = _d("7420.00") / _d("69.12")
        expected_sold = (unit_cost * _d("0.12")).quantize(_d("0.01"))
        assert _d(h["cost_basis_sold_eur"]) == expected_sold
        assert _d(h["remaining_cost_basis_eur"]) == _d("7420.00") - expected_sold
        assert _d(h["total_sale_proceeds_eur"]) == _d("8.58")
        # total_purchase_outflow_eur is unaffected by TRANSFER_IN/OUT
        assert _d(h["total_purchase_outflow_eur"]) == _d("7420.00")

    def test_fifo_sc2_consolidation_without_fractional_preserves_full_cost(self):
        """FIFO-SC2: BUY 100@€50=€5000. Consolidate OUT 100 / IN 50 tcb=5000.00 (exact 1:2)."""
        svc = _make_svc([
            _buy("b1", "XNYS:TEST", 100, "5000.00", trade_date="2024-01-01"),
            _transfer_out("co1", "XNYS:TEST", 100, trade_date="2024-05-01"),
            _transfer_in("ci1", "XNYS:TEST", 50, "5000.00", trade_date="2024-05-01"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]

        assert _d(h["total_shares"]) == _d("50")
        assert _d(h["remaining_cost_basis_eur"]) == _d("5000.00")
        assert h["avg_cost_basis_eur"] == "100.00"
        assert _d(h["cost_basis_sold_eur"]) == _d("0.00")
        assert _d(h["total_sale_proceeds_eur"]) == _d("0.00")

    def test_fifo_sc3_consolidation_then_sell_uses_post_consolidation_unit_cost(self):
        """FIFO-SC3: BUY 100@€50; consolidate to 50@tcb=5000; SELL 10@€120 each."""
        svc = _make_svc([
            _buy("b1", "XNYS:TEST", 100, "5000.00", trade_date="2024-01-01"),
            _transfer_out("co1", "XNYS:TEST", 100, trade_date="2024-05-01"),
            _transfer_in("ci1", "XNYS:TEST", 50, "5000.00", trade_date="2024-05-01"),
            _sell("s1", "XNYS:TEST", 10, "1200.00", trade_date="2024-06-01"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]

        assert _d(h["total_shares"]) == _d("40")
        assert _d(h["cost_basis_sold_eur"]) == _d("1000.00")
        assert _d(h["remaining_cost_basis_eur"]) == _d("4000.00")
        assert _d(h["total_sale_proceeds_eur"]) == _d("1200.00")
        assert _d(h["realized_result_eur"]) == _d("200.00")

    # -----------------------------------------------------------------------
    # FIFO-SC-ORDER: deterministic same-day CA-group leg ordering
    # (danny-share-consolidation-contract-rev1.md §R1/§R4)
    #
    # Fixes the defect Basher's 300-trial randomized reproducer found: sorting
    # movements by (trade_date, id) alone lets random movement-ID UUIDs
    # reorder same-date CONSOLIDATION_OUT / CONSOLIDATION_IN /
    # FRACTIONAL_CASH_OUT legs, corrupting FIFO lot depletion ~66% of the
    # time. The fix adds ca_group_id/ca_group_seq to the sort key ahead of
    # id, so legs within a CA group always process in stamped seq order
    # regardless of their random movement IDs.
    # -----------------------------------------------------------------------

    def test_fifo_sc_order_1_randomized_uuids_prove_ordering(self):
        """FIFO-SC-ORDER-1 (the Basher reproducer): BUY 50@€100 (lot A) +
        BUY 22@€110 (lot B) = 72 shares, €7420. Consolidate OUT 72 / IN 69.12
        (tcb=7420.00) / FRACTIONAL_CASH_OUT 0.12 @ €8.58, all same trade_date,
        with genuinely random UUID movement IDs. Shuffle insertion order across
        20 iterations; every iteration must produce the identical, correct
        result because ca_group_seq — not id — now decides in-group order.
        """
        for _ in range(20):
            ca_group_id = f"cag_{uuid4().hex}"
            legs = [
                _transfer_out(f"mvt_{uuid4().hex}", "XNYS:TEST", 72,
                              trade_date="2024-05-01",
                              ca_group_id=ca_group_id, ca_group_seq=1),
                _transfer_in(f"mvt_{uuid4().hex}", "XNYS:TEST", "69.12", "7420.00",
                             trade_date="2024-05-01",
                             ca_group_id=ca_group_id, ca_group_seq=2),
                _sell(f"mvt_{uuid4().hex}", "XNYS:TEST", "0.12", "8.58",
                      trade_date="2024-05-01",
                      ca_group_id=ca_group_id, ca_group_seq=3),
            ]
            random.shuffle(legs)
            movements = [
                _buy(f"mvt_{uuid4().hex}", "XNYS:TEST", 50, "5000.00", trade_date="2024-01-01"),
                _buy(f"mvt_{uuid4().hex}", "XNYS:TEST", 22, "2420.00", trade_date="2024-01-15"),
                *legs,
            ]
            random.shuffle(movements)

            svc = _make_svc(movements)
            result = svc.compute_holdings()
            h = result["holdings"][0]

            assert _d(h["total_shares"]) == _d("69.000000")
            assert _d(h["cost_basis_sold_eur"]) == _d("12.88")
            assert _d(h["remaining_cost_basis_eur"]) == _d("7407.12")
            warning_types = [w["type"] for w in h.get("warnings", [])]
            assert "NEGATIVE_INVENTORY" not in warning_types

    def test_fifo_sc_order_2_unrelated_same_day_movements_dont_interfere(self):
        """FIFO-SC-ORDER-2: a same-day CA group for one security must not be
        disturbed by unrelated same-day movements on other securities, and
        those unrelated movements must compute correctly themselves.
        """
        ca_group_id = f"cag_{uuid4().hex}"
        movements = [
            _buy(f"mvt_{uuid4().hex}", "XNYS:CONS", 100, "5000.00", trade_date="2026-01-01"),
            _transfer_out(f"mvt_{uuid4().hex}", "XNYS:CONS", 100, trade_date="2026-06-01",
                          ca_group_id=ca_group_id, ca_group_seq=1),
            _transfer_in(f"mvt_{uuid4().hex}", "XNYS:CONS", 50, "5000.00", trade_date="2026-06-01",
                         ca_group_id=ca_group_id, ca_group_seq=2),
            # Unrelated SELL of a different security, same date.
            _buy(f"mvt_{uuid4().hex}", "XNYS:OTHR", 10, "200.00", trade_date="2026-01-01"),
            _sell(f"mvt_{uuid4().hex}", "XNYS:OTHR", 10, "220.00", trade_date="2026-06-01"),
            # Unrelated BUY of a third security, same date.
            _buy(f"mvt_{uuid4().hex}", "XNYS:THRD", 5, "150.00", trade_date="2026-06-01"),
        ]
        random.shuffle(movements)

        svc = _make_svc(movements)
        result = svc.compute_holdings()
        by_ticker = {h["ticker"]: h for h in result["holdings"]}

        cons = by_ticker["CONS"]
        assert _d(cons["total_shares"]) == _d("50")
        assert _d(cons["remaining_cost_basis_eur"]) == _d("5000.00")

        othr = by_ticker["OTHR"]
        assert _d(othr["total_shares"]) == _d("0")
        assert _d(othr["cost_basis_sold_eur"]) == _d("200.00")
        assert _d(othr["total_sale_proceeds_eur"]) == _d("220.00")

        thrd = by_ticker["THRD"]
        assert _d(thrd["total_shares"]) == _d("5")
        assert _d(thrd["remaining_cost_basis_eur"]) == _d("150.00")

    def test_fifo_sc_order_3_two_ca_groups_same_date_independent(self):
        """FIFO-SC-ORDER-3: two SHARE_CONSOLIDATION groups (different
        securities) land on the same trade_date. Each group must be resolved
        independently using its own ca_group_id, not cross-contaminated.
        """
        group_a = f"cag_{uuid4().hex}"
        group_b = f"cag_{uuid4().hex}"
        movements = [
            _buy(f"mvt_{uuid4().hex}", "XNYS:SECA", 100, "5000.00", trade_date="2026-01-01"),
            _buy(f"mvt_{uuid4().hex}", "XNYS:SECB", 200, "5000.00", trade_date="2026-01-01"),
            _transfer_out(f"mvt_{uuid4().hex}", "XNYS:SECA", 100, trade_date="2026-06-01",
                          ca_group_id=group_a, ca_group_seq=1),
            _transfer_in(f"mvt_{uuid4().hex}", "XNYS:SECA", 50, "5000.00", trade_date="2026-06-01",
                         ca_group_id=group_a, ca_group_seq=2),
            _transfer_out(f"mvt_{uuid4().hex}", "XNYS:SECB", 200, trade_date="2026-06-01",
                          ca_group_id=group_b, ca_group_seq=1),
            _transfer_in(f"mvt_{uuid4().hex}", "XNYS:SECB", 100, "5000.00", trade_date="2026-06-01",
                         ca_group_id=group_b, ca_group_seq=2),
        ]
        random.shuffle(movements)

        svc = _make_svc(movements)
        result = svc.compute_holdings()
        by_ticker = {h["ticker"]: h for h in result["holdings"]}

        seca = by_ticker["SECA"]
        assert _d(seca["total_shares"]) == _d("50")
        assert _d(seca["remaining_cost_basis_eur"]) == _d("5000.00")

        secb = by_ticker["SECB"]
        assert _d(secb["total_shares"]) == _d("100")
        assert _d(secb["remaining_cost_basis_eur"]) == _d("5000.00")

    def test_fifo_sc_order_4_non_ca_ordering_preserved(self):
        """FIFO-SC-ORDER-4: two BUY movements (no CA group) on the same date
        must still sort by (trade_date, id) — lexicographically smaller id
        is consumed first, matching pre-existing TestFifoIdTieBreak behavior.
        """
        svc = _make_svc([
            _buy("aaa_first", "XNYS:TEST", 50, "500.00", trade_date="2024-01-01"),
            _buy("zzz_second", "XNYS:TEST", 50, "1000.00", trade_date="2024-01-01"),
            _sell("s1", "XNYS:TEST", 30, "600.00"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        # Consumed 30 from 'aaa_first' (€10/sh) = 300, proving lexicographic
        # id order still governs when no ca_group_id is present.
        assert _d(h["cost_basis_sold_eur"]) == _d("300.00")
        assert _d(h["remaining_cost_basis_eur"]) == _d("1200.00")
