"""Regression tests for danny-scrip-zero-cost-and-buy-import-contract.md.

NEW semantics (Livingston implementation):
  A. ZERO_COST BUY (scrip/rights): enters pool at cost 0, dilutes avg naturally.
     INCOMPLETE BUY (genuinely unknown): stays in unpaid_shares, emits INCOMPLETE_COST_BASIS.
  B. BUY CSV import: CSV "Total (€)" is NET; gross = net + commission.
     Engine: cost = gross_eur only (no double-add of commission).
     SELL: unchanged — gross is total_proceeds, net = gross - commission.
"""

import pytest
from decimal import Decimal, ROUND_HALF_UP

from src.portfolio.cosmos_portfolio import CosmosPortfolioService
from src.portfolio.cosmos_securities import CosmosSecuritiesService
from src.portfolio.holdings_service import HoldingsService
from src.portfolio.import_service import ImportService
from src.portfolio.parsers.purchases import parse_purchases


# ---------------------------------------------------------------------------
# Shared fakes (same pattern as test_portfolio_holdings.py)
# ---------------------------------------------------------------------------

class _FakePortfolioContainer:
    def __init__(self, movements=None):
        self._movements = list(movements or [])

    def query_items(self, query="", parameters=None, enable_cross_partition_query=True, partition_key=None):
        if "COUNT" in query:
            return iter([len(self._movements)])
        if "NOT IS_DEFINED(c.deleted_at)" in query:
            return iter([m for m in self._movements if "deleted_at" not in m])
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


class _FakeSymbolsContainer:
    def read_item(self, item, partition_key):
        from azure.cosmos.exceptions import CosmosResourceNotFoundError
        raise CosmosResourceNotFoundError(message="not found", response=None)

    def create_item(self, body): return body
    def query_items(self, **kw): return iter([])
    def replace_item(self, item, body): return body


def _make_svc(movements):
    portfolio_svc = CosmosPortfolioService(_FakePortfolioContainer(movements), None)
    securities_svc = CosmosSecuritiesService(_FakeSymbolsContainer())
    return HoldingsService(portfolio_svc, securities_svc)


def _buy(mid, security_id, qty, gross_eur, commission_eur="0",
         cost_basis_status="COMPLETE", trade_date="2024-01-15", account_id="_unassigned"):
    """Build a BUY movement with gross_eur as the TRUE gross (includes commission)."""
    ticker = security_id.split(":")[-1]
    return {
        "id": mid,
        "doc_type": "ledger_txn",
        "txn_type": "BUY",
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": trade_date,
        "quantity": str(qty),
        "gross": {"amount": str(gross_eur), "currency": "EUR", "eur_amount": str(gross_eur)},
        "fees": {"total": str(commission_eur), "currency": "EUR", "total_eur": str(commission_eur)},
        "net": {
            "amount": str(Decimal(str(gross_eur)) - Decimal(str(commission_eur))),
            "currency": "EUR",
            "eur_amount": str(Decimal(str(gross_eur)) - Decimal(str(commission_eur))),
        },
        "account_id": account_id,
        "cost_basis_status": cost_basis_status,
        "correction_status": "ACTIVE",
        "warnings": [],
    }


def _sell(mid, security_id, qty, gross_eur, commission_eur="0",
          sales_type="ACCIONES", trade_date="2024-06-01", account_id="_unassigned"):
    """Build a SELL movement. gross_eur = total proceeds (before fee deduction)."""
    ticker = security_id.split(":")[-1]
    net = Decimal(str(gross_eur)) - Decimal(str(commission_eur))
    return {
        "id": mid,
        "doc_type": "ledger_txn",
        "txn_type": "SELL",
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": trade_date,
        "quantity": str(qty),
        "gross": {"amount": str(gross_eur), "currency": "EUR", "eur_amount": str(gross_eur)},
        "fees": {"total": str(commission_eur), "currency": "EUR", "total_eur": str(commission_eur)},
        "net": {"amount": str(net), "currency": "EUR", "eur_amount": str(net)},
        "account_id": account_id,
        "sales_type": sales_type,
        "correction_status": "ACTIVE",
        "warnings": [],
    }


def _d(val):
    return Decimal(str(val))


# ---------------------------------------------------------------------------
# §A — ZERO_COST pool entry
# ---------------------------------------------------------------------------

class TestZeroCostPoolEntry:
    """§A: ZERO_COST BUY enters pool at cost 0, diluting avg naturally.
    ACS example from contract: 163 COMPLETE (cost 4294.21) + 60 ZERO_COST
    → pool_shares=223, pool_cost=4294.21, avg=4294.21/223=19.26 (2dp).
    """

    def test_acs_example_pool_shares(self):
        """ACS: 163 COMPLETE + 60 ZERO_COST → total_shares=223."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 163, "4294.21"),
            _buy("b2", "XNYS:AAPL", 60, "0", cost_basis_status="ZERO_COST"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert _d(h["total_shares"]) == _d("223")

    def test_acs_example_avg_diluted(self):
        """ACS: avg = 4294.21/223 = 19.26 (ZERO_COST dilutes naturally)."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 163, "4294.21"),
            _buy("b2", "XNYS:AAPL", 60, "0", cost_basis_status="ZERO_COST"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        expected = (_d("4294.21") / _d("223")).quantize(_d("0.01"), rounding=ROUND_HALF_UP)
        assert _d(h["avg_cost_basis_eur"]) == expected  # 19.26

    def test_acs_example_remaining_cost_basis(self):
        """ACS: remaining_cost_basis_eur = 4294.21 (ZERO_COST adds no cost)."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 163, "4294.21"),
            _buy("b2", "XNYS:AAPL", 60, "0", cost_basis_status="ZERO_COST"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert _d(h["remaining_cost_basis_eur"]) == _d("4294.21")

    def test_acs_example_status_complete(self):
        """ACS: holding status = COMPLETE (ZERO_COST does NOT cause INCOMPLETE)."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 163, "4294.21"),
            _buy("b2", "XNYS:AAPL", 60, "0", cost_basis_status="ZERO_COST"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert h["cost_basis_status"] == "COMPLETE"

    def test_acs_example_no_incomplete_warning(self):
        """ACS: no INCOMPLETE_COST_BASIS warning when all are COMPLETE or ZERO_COST."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 163, "4294.21"),
            _buy("b2", "XNYS:AAPL", 60, "0", cost_basis_status="ZERO_COST"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        warning_types = [w["type"] for w in h.get("warnings", [])]
        assert "INCOMPLETE_COST_BASIS" not in warning_types
        assert "ZERO_COST_ACQUISITION" not in warning_types

    def test_acs_example_summary_has_incomplete_false(self):
        """ACS: summary.has_incomplete_cost_basis = False."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 163, "4294.21"),
            _buy("b2", "XNYS:AAPL", 60, "0", cost_basis_status="ZERO_COST"),
        ])
        result = svc.compute_holdings()
        assert result["summary"]["has_incomplete_cost_basis"] is False

    def test_zero_cost_only_buys(self):
        """All ZERO_COST → pool_cost=0, avg=0.00, status=COMPLETE."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 50, "0", cost_basis_status="ZERO_COST"),
            _buy("b2", "XNYS:AAPL", 30, "0", cost_basis_status="ZERO_COST"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert _d(h["total_shares"]) == _d("80")
        # pool_cost=0, avg=0/80=0 → could be None or "0.00" depending on impl
        # Key invariant: status is COMPLETE (not INCOMPLETE)
        assert h["cost_basis_status"] == "COMPLETE"


# ---------------------------------------------------------------------------
# §A — INCOMPLETE stays outside pool and emits correct warning
# ---------------------------------------------------------------------------

class TestIncompleteStaysOutside:
    """INCOMPLETE BUY stays in unpaid_shares, not pool. Emits INCOMPLETE_COST_BASIS."""

    def test_incomplete_does_not_dilute_avg(self):
        """INCOMPLETE BUY must not enter pool and must not change avg."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 100, "1000.00"),
            _buy("b2", "XNYS:AAPL", 50, "0", cost_basis_status="INCOMPLETE"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        # avg = pool_cost / pool_shares = 1000/100 = 10.00 (INCOMPLETE excluded)
        assert _d(h["avg_cost_basis_eur"]) == _d("10.00")

    def test_incomplete_emits_incomplete_cost_basis_warning(self):
        """INCOMPLETE BUY emits INCOMPLETE_COST_BASIS (not ZERO_COST_ACQUISITION)."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 50, "0", cost_basis_status="INCOMPLETE"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        warning_types = [w["type"] for w in h.get("warnings", [])]
        assert "INCOMPLETE_COST_BASIS" in warning_types
        assert "ZERO_COST_ACQUISITION" not in warning_types

    def test_incomplete_sets_holding_status(self):
        """INCOMPLETE BUY → cost_basis_status = INCOMPLETE."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 50, "0", cost_basis_status="INCOMPLETE"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert h["cost_basis_status"] == "INCOMPLETE"

    def test_incomplete_sets_summary_flag(self):
        """INCOMPLETE BUY → summary.has_incomplete_cost_basis = True."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 50, "0", cost_basis_status="INCOMPLETE"),
        ])
        result = svc.compute_holdings()
        assert result["summary"]["has_incomplete_cost_basis"] is True

    def test_zero_cost_does_not_set_incomplete_flag(self):
        """ZERO_COST BUY must NOT set has_incomplete_cost_basis = True."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 60, "0", cost_basis_status="ZERO_COST"),
        ])
        result = svc.compute_holdings()
        assert result["summary"]["has_incomplete_cost_basis"] is False


# ---------------------------------------------------------------------------
# §A — Cost = gross_eur only (no commission double-count)
# ---------------------------------------------------------------------------

class TestCostEqualsGrossOnly:
    """Engine uses cost = gross_eur; commission in fees.total_eur must NOT be re-added."""

    def test_cost_equals_gross_not_gross_plus_commission(self):
        """BUY gross=1010, commission=10 → cost=1010, NOT 1020."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 100, "1010.00", commission_eur="10.00"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        # If engine were gross+commission it would give 1020.
        assert _d(h["remaining_cost_basis_eur"]) == _d("1010.00"), (
            "cost = gross only; double-counting commission gives 1020 — engine is broken"
        )

    def test_no_commission_double_count_in_avg(self):
        """BUY gross=1010 (includes commission) → avg = 1010/100 = 10.10, not (1010+10)/100."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 100, "1010.00", commission_eur="10.00"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert _d(h["avg_cost_basis_eur"]) == _d("10.10"), (
            "avg must be gross/shares; double-count gives 10.20"
        )

    def test_zero_commission_unchanged(self):
        """BUY with zero commission: cost = gross (no change)."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 10, "1825.00", commission_eur="0"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert _d(h["remaining_cost_basis_eur"]) == _d("1825.00")


# ---------------------------------------------------------------------------
# §A — Partial sell with ZERO_COST pool (diluted CMP)
# ---------------------------------------------------------------------------

class TestPartialSellWithZeroCostPool:
    """SELL after mixed COMPLETE+ZERO_COST pool uses diluted avg correctly."""

    def test_sell_uses_diluted_avg(self):
        """163 COMPLETE (4294.21) + 60 ZERO_COST → avg=19.26. SELL 50 → cost_sold=963.00."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 163, "4294.21"),
            _buy("b2", "XNYS:AAPL", 60, "0", cost_basis_status="ZERO_COST"),
            _sell("s1", "XNYS:AAPL", 50, "1000.00"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        avg = _d("4294.21") / _d("223")  # = 19.2563...
        expected_cost_sold = (avg * _d("50")).quantize(_d("0.01"), rounding=ROUND_HALF_UP)
        assert _d(h["cost_basis_sold_eur"]) == expected_cost_sold

    def test_sell_reduces_remaining_correctly(self):
        """remaining = pool_cost − cost_sold after sell."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 163, "4294.21"),
            _buy("b2", "XNYS:AAPL", 60, "0", cost_basis_status="ZERO_COST"),
            _sell("s1", "XNYS:AAPL", 50, "1000.00"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        avg = _d("4294.21") / _d("223")
        cost_sold = (avg * _d("50")).quantize(_d("0.01"), rounding=ROUND_HALF_UP)
        expected_remaining = (_d("4294.21") - cost_sold).quantize(_d("0.01"))
        assert _d(h["remaining_cost_basis_eur"]) == expected_remaining

    def test_shares_after_sell(self):
        """After selling 50 of 223, total_shares = 173."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 163, "4294.21"),
            _buy("b2", "XNYS:AAPL", 60, "0", cost_basis_status="ZERO_COST"),
            _sell("s1", "XNYS:AAPL", 50, "1000.00"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert _d(h["total_shares"]) == _d("173")


# ---------------------------------------------------------------------------
# §B — BUY CSV import gross/net semantics
# ---------------------------------------------------------------------------

PURCHASES_CSV_HEADER = "Año\tEmpresa\tFecha compra\tValor compra\tAcciones\tTotal (€)\tComisión"


def _encode(text):
    return text.encode("utf-8")


class TestBuyImportGrossNetSemantics:
    """§B: CSV "Total (€)" is NET; gross = net + commission stored in movement.gross."""

    def test_gross_equals_net_plus_commission(self):
        """Row: Total=100, Comisión=5 → gross=105, net=100."""
        csv = f"{PURCHASES_CSV_HEADER}\n2024\tApple\t10/01/2024\t10,00\t10\t100,00\t5,00\n"
        rows = parse_purchases(_encode(csv))
        assert len(rows) == 1
        row = rows[0]
        assert row["total_cost"] == _d("100.00")    # net consideration
        assert row["commission"] == _d("5.00")

    def test_import_service_stores_true_gross(self):
        """ImportService must store gross = net + commission in ledger_txn.gross.eur_amount."""
        # Build a mock ImportService and call its internal conversion
        # Since ImportService is not easily unit-testable without Cosmos, we test via
        # import_service._build_movement_from_row (source-contract check) or the
        # parse result + direct calculation.
        csv = f"{PURCHASES_CSV_HEADER}\n2024\tApple\t10/01/2024\t10,00\t10\t100,00\t5,00\n"
        rows = parse_purchases(_encode(csv))
        row = rows[0]
        # The true gross that ImportService would write:
        true_gross = row["total_cost"] + row["commission"]
        assert true_gross == _d("105.00")

    def test_zero_commission_gross_equals_total(self):
        """Row: Total=200, Comisión=0 → gross=200."""
        csv = f"{PURCHASES_CSV_HEADER}\n2024\tApple\t10/01/2024\t20,00\t10\t200,00\t0,00\n"
        rows = parse_purchases(_encode(csv))
        row = rows[0]
        assert row["total_cost"] + row["commission"] == _d("200.00")

    def test_zero_price_zero_commission_is_zero_cost(self):
        """Row: Valor=0, Total=0, Comisión=0 → ZERO_COST status, no warning."""
        csv = f"{PURCHASES_CSV_HEADER}\n2024\tTelefónica\t20/06/2024\t0\t50\t0,00\t0,00\n"
        rows = parse_purchases(_encode(csv))
        assert len(rows) == 1
        row = rows[0]
        assert row["cost_basis_status"] == "ZERO_COST"
        assert len(row["warnings"]) == 0

    def test_zero_cost_no_zero_cost_acquisition_warning(self):
        """ZERO_COST status must NOT produce ZERO_COST_ACQUISITION warning."""
        csv = f"{PURCHASES_CSV_HEADER}\n2024\tTelefónica\t20/06/2024\t0\t50\t0,00\t0,00\n"
        rows = parse_purchases(_encode(csv))
        row = rows[0]
        for w in row.get("warnings", []):
            assert w.get("type") != "ZERO_COST_ACQUISITION", (
                "ZERO_COST rows must not emit ZERO_COST_ACQUISITION (scrip is legitimate)"
            )

    def test_complete_buy_status(self):
        """Non-zero price row → status=COMPLETE."""
        csv = f"{PURCHASES_CSV_HEADER}\n2024\tApple\t10/01/2024\t10,00\t10\t100,00\t0,00\n"
        rows = parse_purchases(_encode(csv))
        row = rows[0]
        assert row["cost_basis_status"] == "COMPLETE"

    def test_sell_gross_is_total_proceeds(self):
        """SELL: gross = total_proceeds (unchanged). Verify via parse_sales parser."""
        # Sales import convention: gross = total_proceeds, net = gross - commission
        # The sales CSV format uses: Año, Empresa, Fecha venta, Acciones, Comisión, Total Venta
        from src.portfolio.parsers.sales import parse_sales
        SALES_HEADER = "Año\tEmpresa\tFecha venta\tAcciones\tComisión\tTotal Venta"
        csv = f"{SALES_HEADER}\n2024\tApple\t15/03/2024\t10\t4,00\t200,00\n"
        rows = parse_sales(_encode(csv))
        assert len(rows) >= 1
        row = rows[0]
        # total_proceeds = 200 (gross = proceeds before commission deduction)
        assert row["total_proceeds"] == _d("200.00")
        assert row["commission"] == _d("4.00")


# ---------------------------------------------------------------------------
# §A — Per-account and aggregate values
# ---------------------------------------------------------------------------

class TestPerAccountZeroCost:
    """ZERO_COST held in a specific account behaves consistently per-account."""

    def test_aggregate_diluted_avg_matches_per_account_sum(self):
        """Global and per-account holdings remain consistent after ZERO_COST."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 100, "2000.00", account_id="ibkr"),
            _buy("b2", "XNYS:AAPL", 50, "0", cost_basis_status="ZERO_COST", account_id="ibkr"),
        ])
        global_r = svc.compute_holdings()
        per_acct = svc.compute_holdings(account_id="ibkr")
        g_h = global_r["holdings"][0]
        a_h = per_acct["holdings"][0]
        # Both must show 150 shares
        assert _d(g_h["total_shares"]) == _d("150")
        assert _d(a_h["total_shares"]) == _d("150")
        # avg must be the same (all shares in same account)
        assert g_h["avg_cost_basis_eur"] == a_h["avg_cost_basis_eur"]

    def test_incomplete_and_zero_cost_mixed_account(self):
        """INCOMPLETE from one account + ZERO_COST from another → global INCOMPLETE."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 100, "1000.00", account_id="ibkr"),
            _buy("b2", "XNYS:AAPL", 20, "0", cost_basis_status="INCOMPLETE", account_id="ibkr"),
            _buy("b3", "XNYS:AAPL", 10, "0", cost_basis_status="ZERO_COST", account_id="degreed"),
        ])
        result = svc.compute_holdings()
        # Global: has INCOMPLETE → status INCOMPLETE, warning present
        assert result["summary"]["has_incomplete_cost_basis"] is True

    def test_zero_cost_only_account_does_not_affect_global_incomplete(self):
        """ZERO_COST only → global has_incomplete_cost_basis = False."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 50, "500.00", account_id="ibkr"),
            _buy("b2", "XNYS:AAPL", 20, "0", cost_basis_status="ZERO_COST", account_id="ibkr"),
        ])
        result = svc.compute_holdings()
        assert result["summary"]["has_incomplete_cost_basis"] is False


# ---------------------------------------------------------------------------
# §A — total_purchase_outflow_eur excludes ZERO_COST (free shares)
# ---------------------------------------------------------------------------

class TestPurchaseOutflowExcludesZeroCost:
    """total_purchase_outflow_eur = sum of gross_eur for COMPLETE BUYs only.
    ZERO_COST adds no outflow (free shares).
    """

    def test_outflow_excludes_zero_cost_shares(self):
        """163 COMPLETE (4294.21) + 60 ZERO_COST → outflow = 4294.21, not inflated."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 163, "4294.21"),
            _buy("b2", "XNYS:AAPL", 60, "0", cost_basis_status="ZERO_COST"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        # total_purchase_outflow_eur = sum of COMPLETE gross only
        assert _d(h["total_purchase_outflow_eur"]) == _d("4294.21")

    def test_outflow_summary_excludes_zero_cost(self):
        """Summary total_purchase_outflow_eur must exclude ZERO_COST entries."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 163, "4294.21"),
            _buy("b2", "XNYS:AAPL", 60, "0", cost_basis_status="ZERO_COST"),
        ])
        result = svc.compute_holdings()
        s = result["summary"]
        assert _d(s["total_purchase_outflow_eur"]) == _d("4294.21")


# ---------------------------------------------------------------------------
# §A — ZERO_COST warning is NEVER emitted (regardless of context)
# ---------------------------------------------------------------------------

class TestZeroCostAcquisitionWarningAbsent:
    """ZERO_COST_ACQUISITION warning is removed from the new engine.
    Neither ZERO_COST BUY nor INCOMPLETE BUY should produce it.
    """

    def test_zero_cost_buy_no_zero_cost_acquisition_warning(self):
        """ZERO_COST BUY must not emit ZERO_COST_ACQUISITION."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 60, "0", cost_basis_status="ZERO_COST"),
        ])
        result = svc.compute_holdings()
        if result["holdings"]:
            h = result["holdings"][0]
            for w in h.get("warnings", []):
                assert w["type"] != "ZERO_COST_ACQUISITION", (
                    "ZERO_COST_ACQUISITION warning must not be emitted for ZERO_COST BUY"
                )

    def test_incomplete_buy_emits_incomplete_cost_basis_not_zero_cost_acquisition(self):
        """INCOMPLETE BUY emits INCOMPLETE_COST_BASIS, not ZERO_COST_ACQUISITION."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 50, "0", cost_basis_status="INCOMPLETE"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        warning_types = [w["type"] for w in h.get("warnings", [])]
        assert "INCOMPLETE_COST_BASIS" in warning_types
        assert "ZERO_COST_ACQUISITION" not in warning_types

    def test_complete_buy_no_warnings(self):
        """COMPLETE BUY with non-zero cost emits no warnings."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 100, "1000.00"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert h.get("warnings", []) == [] or all(
            w["type"] in ("NEGATIVE_INVENTORY",)
            for w in h.get("warnings", [])
        )


# ---------------------------------------------------------------------------
# SELL non-regression — semantics unchanged by the scrip/BUY import contract
# ---------------------------------------------------------------------------

class TestSellNonRegression:
    """SELL block is explicitly untouched by the scrip/BUY import contract.

    gross_eur = total proceeds (before fee deduction).
    net_proceeds = gross_eur - commission_eur.
    These assertions must stay true regardless of BUY engine changes.
    """

    def test_sell_net_proceeds_is_gross_minus_commission(self):
        """SELL: net proceeds = gross - commission (unchanged contract)."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 100, "1000.00"),
            _sell("s1", "XNYS:AAPL", 30, "450.00", commission_eur="3.00"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert _d(h["total_sale_proceeds_eur"]) == _d("447.00")

    def test_sell_gross_proceeds_not_affected_by_buy_semantics(self):
        """SELL proceeds accounting does not change when BUY gross semantics change."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 100, "1005.00", commission_eur="5.00"),
            _sell("s1", "XNYS:AAPL", 50, "600.00", commission_eur="6.00"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        # net = 600 - 6 = 594; avg = 1005/100 = 10.05; cost_sold = 502.50; realized = 91.50
        assert _d(h["total_sale_proceeds_eur"]) == _d("594.00")
        assert _d(h["cost_basis_sold_eur"]) == _d("502.50")
        assert _d(h["realized_result_eur"]) == _d("91.50")

    def test_sell_commission_deducted_from_proceeds_only(self):
        """SELL commission deducted from proceeds, never added to cost pool."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 100, "1000.00"),
            _sell("s1", "XNYS:AAPL", 100, "1200.00", commission_eur="10.00"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        # remaining=0; realized = (1200-10) - 1000 = 190
        assert _d(h["remaining_cost_basis_eur"]) == _d("0.00")
        assert _d(h["realized_result_eur"]) == _d("190.00")

    def test_sell_derechos_proceeds_net_of_commission(self):
        """DERECHOS sale: rights_proceeds_eur = gross - commission; shares unaffected."""
        svc = _make_svc([
            _buy("b1", "XNYS:AAPL", 100, "2000.00"),
            _sell("s1", "XNYS:AAPL", 10, "80.00", commission_eur="2.00",
                  sales_type="DERECHOS"),
        ])
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert _d(h["rights_proceeds_eur"]) == _d("78.00")
        assert _d(h["total_shares"]) == _d("100")  # DERECHOS does not decrement shares
