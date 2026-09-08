"""Extended regression tests — Full movement correction field matrix.

Extends test_portfolio_phase2_corrections.py with:
  - BUY: gross+fees override, cost_basis_status, fx, trade_date changes
  - SELL: withholding source, sales_type ACCIONES↔DERECHOS, quantity+gross
  - DIVIDEND: all withholding destination states (null/zero/value), quantity
    null-preserved and null→value, FX
  - Net arithmetic: gross − fees − wht_source − wht_dest (server-side)
  - Decimal precision: 6dp monetary, partial override uses original values
  - TRANSFER correction rejected (xfail — not yet implemented)
  - Double-correction chain A→B→C; B correctly SUPERSEDED
  - Imported-movement correction retains provenance (import_source → manual)
  - Security/txn_type immutability; account mismatch → 404
  - Legacy narrow correction (only note, no overrides) still works
  - Withholding structure validation (xfail — not yet implemented)
  - Holdings impact: SELL ACCIONES corrected to DERECHOS → shares unchanged

Contract reference: danny-zero-filter-full-correction-contract.md Part B/C/F
"""

from __future__ import annotations

import pytest
from decimal import Decimal
from fastapi.testclient import TestClient

from src.portfolio.cosmos_portfolio import CosmosPortfolioService
from src.portfolio.cosmos_securities import CosmosSecuritiesService
from src.portfolio.holdings_service import HoldingsService
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from tests.conftest_portfolio_p2 import FakeCosmos


# ===========================================================================
# Fixtures / helpers
# ===========================================================================

@pytest.fixture
def client():
    from web.app import app
    fake = FakeCosmos()
    with TestClient(app) as c:
        app.state.cosmos = fake
        app.state.cosmos_error = None
        yield c, fake


def _seed(fake, mid, txn_type="BUY", quantity="100",
          gross_eur="18250.000000", commission_eur="7.500000",
          security_id="XNYS:AAPL", account_id="_unassigned",
          trade_date="2024-01-15", correction_status="ACTIVE",
          import_source="manual", withholding=None, sales_type=None,
          cost_basis_status=None, fx=None, extra_fields=None):
    """Seed a ledger_txn document directly into the fake portfolio container."""
    ticker = security_id.split(":")[-1]
    net = str(Decimal(gross_eur) - Decimal(commission_eur))
    doc = {
        "id": mid,
        "doc_type": "ledger_txn",
        "txn_type": txn_type,
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": trade_date,
        "quantity": quantity,
        "gross": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur},
        "fees": {"total": commission_eur, "currency": "EUR", "total_eur": commission_eur},
        "net": {"amount": net, "currency": "EUR", "eur_amount": net},
        "account_id": account_id,
        "correction_status": correction_status,
        "import_source": import_source,
        "created_at": "2026-09-06T10:00:00Z",
        "warnings": [],
    }
    if withholding is not None:
        doc["withholding"] = withholding
    if sales_type is not None:
        doc["sales_type"] = sales_type
    if cost_basis_status is not None:
        doc["cost_basis_status"] = cost_basis_status
    if fx is not None:
        doc["fx"] = fx
    if extra_fields:
        doc.update(extra_fields)
    fake.portfolio_container._store[mid] = doc
    return doc


def _correct(c, mid, account_id="_unassigned", note="test correction", **overrides):
    """POST correct endpoint with optional field overrides."""
    body = {"account_id": account_id, "correction_note": note}
    body.update(overrides)
    return c.post(f"/api/portfolio/movements/{mid}/correct", json=body)


def _d(v) -> Decimal:
    return Decimal(str(v))


# ===========================================================================
# BUY — full field correction
# ===========================================================================

class TestBuyFullCorrection:
    def test_buy_gross_fees_net_recomputed(self, client):
        """C-1 / C-8+C-9: BUY with new gross+fees → net = new_gross + new_fees."""
        c, fake = client
        _seed(fake, "buy_gf_001", gross_eur="18250.000000", commission_eur="7.500000")
        new_gross = "16000.000000"
        new_fees = "5.000000"
        resp = _correct(c, "buy_gf_001",
                        gross={"amount": new_gross, "currency": "EUR", "eur_amount": new_gross},
                        fees={"total": new_fees, "currency": "EUR", "total_eur": new_fees})
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        assert _d(repl["gross"]["eur_amount"]) == _d(new_gross)
        assert _d(repl["fees"]["total_eur"]) == _d(new_fees)
        expected_net = _d(new_gross) + _d(new_fees)
        assert _d(repl["net"]["eur_amount"]) == expected_net, (
            f"net must be {expected_net}; got {repl['net']['eur_amount']}"
        )

    def test_buy_only_gross_uses_original_fees(self, client):
        """C-8: Only gross changed → net = new_gross + original_fees."""
        c, fake = client
        _seed(fake, "buy_og_001", gross_eur="18250.000000", commission_eur="7.500000")
        new_gross = "20000.000000"
        original_fees = _d("7.500000")
        resp = _correct(c, "buy_og_001",
                        gross={"amount": new_gross, "currency": "EUR", "eur_amount": new_gross})
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        expected_net = _d(new_gross) + original_fees
        assert _d(repl["net"]["eur_amount"]) == expected_net

    def test_buy_only_fees_uses_original_gross(self, client):
        """C-9: Only fees changed → net = original_gross + new_fees."""
        c, fake = client
        _seed(fake, "buy_of_001", gross_eur="18250.000000", commission_eur="7.500000")
        new_fees = "15.000000"
        original_gross = _d("18250.000000")
        resp = _correct(c, "buy_of_001",
                        fees={"total": new_fees, "currency": "EUR", "total_eur": new_fees})
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        expected_net = original_gross + _d(new_fees)
        assert _d(repl["net"]["eur_amount"]) == expected_net

    def test_buy_cost_basis_status_incomplete_to_complete(self, client):
        """C-2: BUY cost_basis_status INCOMPLETE → COMPLETE via correction."""
        c, fake = client
        _seed(fake, "buy_cbs_001", cost_basis_status="INCOMPLETE")
        resp = _correct(c, "buy_cbs_001", cost_basis_status="COMPLETE")
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        assert repl["cost_basis_status"] == "COMPLETE"

    def test_buy_cost_basis_status_complete_to_incomplete(self, client):
        """C-2: BUY cost_basis_status COMPLETE → INCOMPLETE via correction."""
        c, fake = client
        _seed(fake, "buy_cbs_002", cost_basis_status="COMPLETE")
        resp = _correct(c, "buy_cbs_002", cost_basis_status="INCOMPLETE")
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        assert repl["cost_basis_status"] == "INCOMPLETE"

    def test_buy_fx_override(self, client):
        """C-11: BUY fx.rate and fx.rate_source updated."""
        c, fake = client
        _seed(fake, "buy_fx_001",
              fx={"rate": "1.000000000", "rate_source": "ECB"})
        resp = _correct(c, "buy_fx_001",
                        fx={"rate": "1.082500000", "rate_source": "MANUAL"})
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        assert repl["fx"]["rate"] == "1.082500000"
        assert repl["fx"]["rate_source"] == "MANUAL"

    def test_buy_trade_date_change(self, client):
        """BUY trade_date corrected to a different date."""
        c, fake = client
        _seed(fake, "buy_td_001", trade_date="2024-01-15")
        resp = _correct(c, "buy_td_001", trade_date="2024-01-20")
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        assert repl["trade_date"] == "2024-01-20"
        orig = resp.json()["original"]
        assert orig["trade_date"] == "2024-01-15", "original trade_date must be unchanged"

    def test_buy_quantity_change(self, client):
        """BUY quantity corrected — basic contract from existing suite, verified here."""
        c, fake = client
        _seed(fake, "buy_qty_001", quantity="100")
        resp = _correct(c, "buy_qty_001", quantity="95")
        assert resp.status_code == 200
        assert resp.json()["replacement"]["quantity"] == "95"

    def test_buy_zero_gross_incomplete_valid(self, client):
        """BUY with gross=0 and cost_basis_status=INCOMPLETE is valid (zero-cost acquisition)."""
        c, fake = client
        _seed(fake, "buy_zg_001", gross_eur="100.000000", commission_eur="5.000000")
        resp = _correct(c, "buy_zg_001",
                        gross={"amount": "0", "currency": "EUR", "eur_amount": "0"},
                        fees={"total": "0", "currency": "EUR", "total_eur": "0"},
                        cost_basis_status="INCOMPLETE")
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        assert _d(repl["gross"]["eur_amount"]) == _d("0")
        assert repl["cost_basis_status"] == "INCOMPLETE"
        assert _d(repl["net"]["eur_amount"]) == _d("0")


# ===========================================================================
# SELL — full field correction
# ===========================================================================

class TestSellCorrection:
    def test_sell_sales_type_acciones_to_derechos(self, client):
        """C-4: SELL sales_type ACCIONES → DERECHOS."""
        c, fake = client
        _seed(fake, "sell_std_001", txn_type="SELL", sales_type="ACCIONES")
        resp = _correct(c, "sell_std_001", sales_type="DERECHOS")
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        assert repl["sales_type"] == "DERECHOS"

    def test_sell_sales_type_derechos_to_acciones(self, client):
        """C-4: SELL sales_type DERECHOS → ACCIONES."""
        c, fake = client
        _seed(fake, "sell_dta_001", txn_type="SELL", sales_type="DERECHOS")
        resp = _correct(c, "sell_dta_001", sales_type="ACCIONES")
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        assert repl["sales_type"] == "ACCIONES"

    def test_sell_withholding_source_added(self, client):
        """C-3: SELL correction adds withholding.source; net reduced."""
        c, fake = client
        _seed(fake, "sell_ws_001", txn_type="SELL",
              gross_eur="5000.000000", commission_eur="5.000000",
              sales_type="ACCIONES")
        wht = {"source": {"country": "ES", "rate_pct": "19", "amount_eur": "950.000000"},
               "destination": None}
        resp = _correct(c, "sell_ws_001", withholding=wht)
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        assert repl["withholding"]["source"]["amount_eur"] == "950.000000"
        # net = gross - fees - wht_source - wht_dest(null=0)
        expected_net = _d("5000") - _d("5") - _d("950") - _d("0")
        assert _d(repl["net"]["eur_amount"]) == expected_net

    def test_sell_quantity_and_gross_corrected(self, client):
        """SELL: both quantity and gross corrected."""
        c, fake = client
        _seed(fake, "sell_qg_001", txn_type="SELL",
              quantity="100", gross_eur="5000.000000",
              commission_eur="5.000000", sales_type="ACCIONES")
        resp = _correct(c, "sell_qg_001",
                        quantity="80",
                        gross={"amount": "4000.000000", "currency": "EUR", "eur_amount": "4000.000000"})
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        assert repl["quantity"] == "80"
        assert _d(repl["gross"]["eur_amount"]) == _d("4000")
        # original fees used in net recompute
        expected_net = _d("4000") - _d("5")
        assert _d(repl["net"]["eur_amount"]) == expected_net

    def test_sell_fx_override(self, client):
        """C-11: SELL fx rate changed."""
        c, fake = client
        _seed(fake, "sell_fx_001", txn_type="SELL", sales_type="ACCIONES",
              fx={"rate": "1.000000000", "rate_source": "ECB"})
        resp = _correct(c, "sell_fx_001",
                        fx={"rate": "1.095000000", "rate_source": "BROKER"})
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        assert repl["fx"]["rate_source"] == "BROKER"
        assert _d(repl["fx"]["rate"]) == _d("1.095000000")


# ===========================================================================
# DIVIDEND — withholding null/zero/value lifecycle
# ===========================================================================

_DIVIDEND_SKIP_REASON = (
    "PAUSED — pending contract amendment: DIVIDEND composite action model under revision. "
    "New requirement: withholding amounts/percentages auto-calculated; DIVIDEND may be a "
    "linked composite corporate action (residual cash, partial rights sale, shares from "
    "remaining rights, optional cash top-up). Tests will be reconciled once the amended "
    "contract lands. Do not flatten into a single movement or fabricate quantity/cost."
)


class TestDividendWithholding:
    def _wht(self, dest=None):
        return {
            "source": {"country": "US", "rate_pct": "15", "amount_eur": "45.000000"},
            "destination": dest,
        }

    def test_dividend_wht_dest_null_to_value(self, client):
        """C-5: DIVIDEND withholding destination null → value; net reduced."""
        c, fake = client
        # Original: source=45, destination=null  →  net = 300 - 5 - 45 = 250
        _seed(fake, "div_null2val_001", txn_type="DIVIDEND",
              gross_eur="300.000000", commission_eur="5.000000",
              withholding=self._wht(dest=None))
        # Correction: add destination withholding of 57
        new_wht = {
            "source": {"country": "US", "rate_pct": "15", "amount_eur": "45.000000"},
            "destination": {"country": "ES", "rate_pct": "19", "amount_eur": "57.000000"},
        }
        resp = _correct(c, "div_null2val_001", withholding=new_wht)
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        assert repl["withholding"]["destination"]["amount_eur"] == "57.000000"
        expected_net = _d("300") - _d("5") - _d("45") - _d("57")
        assert _d(repl["net"]["eur_amount"]) == expected_net, (
            f"net must be {expected_net}; got {repl['net']['eur_amount']}"
        )

    def test_dividend_wht_dest_value_to_null(self, client):
        """C-6: DIVIDEND withholding destination value → null; net increases."""
        c, fake = client
        # Original: source=45, destination=57  → net = 300 - 5 - 45 - 57 = 193
        original_wht = {
            "source": {"country": "US", "rate_pct": "15", "amount_eur": "45.000000"},
            "destination": {"country": "ES", "rate_pct": "19", "amount_eur": "57.000000"},
        }
        _seed(fake, "div_val2null_001", txn_type="DIVIDEND",
              gross_eur="300.000000", commission_eur="5.000000",
              withholding=original_wht)
        # Correction: destination → null (broker stopped capturing)
        new_wht = {
            "source": {"country": "US", "rate_pct": "15", "amount_eur": "45.000000"},
            "destination": None,
        }
        resp = _correct(c, "div_val2null_001", withholding=new_wht)
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        # net = 300 - 5 - 45 - 0 = 250
        expected_net = _d("300") - _d("5") - _d("45")
        assert _d(repl["net"]["eur_amount"]) == expected_net
        assert repl["withholding"]["destination"] is None, (
            "destination must be null after correction to null"
        )

    def test_dividend_wht_dest_zero(self, client):
        """C-7: DIVIDEND withholding destination = {amount_eur: "0"} → net same as no-dest-wht."""
        c, fake = client
        _seed(fake, "div_zero_001", txn_type="DIVIDEND",
              gross_eur="300.000000", commission_eur="5.000000",
              withholding={"source": {"country": "US", "rate_pct": "15", "amount_eur": "45.000000"},
                           "destination": None})
        zero_dest_wht = {
            "source": {"country": "US", "rate_pct": "15", "amount_eur": "45.000000"},
            "destination": {"country": "ES", "rate_pct": "0", "amount_eur": "0"},
        }
        resp = _correct(c, "div_zero_001", withholding=zero_dest_wht)
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        # wht_dest = 0 → net = 300 - 5 - 45 = 250
        expected_net = _d("300") - _d("5") - _d("45")
        assert _d(repl["net"]["eur_amount"]) == expected_net
        assert repl["withholding"]["destination"]["amount_eur"] == "0"

    def test_dividend_quantity_null_preserved_when_not_sent(self, client):
        """C-12: DIVIDEND quantity=null preserved when correction omits quantity."""
        c, fake = client
        # Seed doc with quantity=null (as from CSV import without share count)
        _seed(fake, "div_qnull_001", txn_type="DIVIDEND",
              quantity=None,
              extra_fields={"quantity": None})  # explicit None in doc
        # Only correct trade_date — quantity must stay null
        resp = _correct(c, "div_qnull_001", trade_date="2024-06-15")
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        assert repl.get("quantity") is None, (
            "quantity must remain null when not included in correction"
        )

    def test_dividend_quantity_null_to_value(self, client):
        """C-13: DIVIDEND quantity null → value via correction."""
        c, fake = client
        _seed(fake, "div_q2v_001", txn_type="DIVIDEND",
              quantity=None, extra_fields={"quantity": None})
        resp = _correct(c, "div_q2v_001", quantity="150.500000")
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        assert repl["quantity"] == "150.500000"

    def test_dividend_gross_and_wht_source_net_recomputed(self, client):
        """C-10: DIVIDEND gross + wht_source change → net = new_gross - fees - new_wht_source."""
        c, fake = client
        _seed(fake, "div_gwht_001", txn_type="DIVIDEND",
              gross_eur="200.000000", commission_eur="0",
              withholding={"source": {"country": "US", "rate_pct": "15",
                                      "amount_eur": "30.000000"},
                           "destination": None})
        new_wht = {"source": {"country": "US", "rate_pct": "15", "amount_eur": "35.000000"},
                   "destination": None}
        resp = _correct(c, "div_gwht_001",
                        gross={"amount": "250.000000", "currency": "EUR", "eur_amount": "250.000000"},
                        withholding=new_wht)
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        expected_net = _d("250") - _d("0") - _d("35")
        assert _d(repl["net"]["eur_amount"]) == expected_net

    def test_dividend_fx_override(self, client):
        """C-11: DIVIDEND fx.rate updated."""
        c, fake = client
        _seed(fake, "div_fx_001", txn_type="DIVIDEND",
              fx={"rate": "1.000000000", "rate_source": "ECB"})
        resp = _correct(c, "div_fx_001",
                        fx={"rate": "1.090000000", "rate_source": "MANUAL"})
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        assert repl["fx"]["rate"] == "1.090000000"
        assert repl["fx"]["rate_source"] == "MANUAL"


# ===========================================================================
# Net arithmetic precision (C-8, C-9, C-10, C.3)
# ===========================================================================

class TestNetArithmetic:
    def test_all_four_components(self, client):
        """C-10: net = gross - fees - wht_source - wht_dest (all non-zero).

        Standalone DIVIDEND (no ca_group_id): individual correction allowed.
        rate_pct is server-derived from amount_eur/gross_eur per Amendment H.
        """
        c, fake = client
        _seed(fake, "net_all_001", txn_type="DIVIDEND",
              gross_eur="1000.000000", commission_eur="0")
        wht = {
            "source": {"country": "US", "rate_pct": "15", "amount_eur": "150.000000"},
            "destination": {"country": "ES", "rate_pct": "19", "amount_eur": "190.000000"},
        }
        resp = _correct(c, "net_all_001",
                        gross={"amount": "1000.000000", "currency": "EUR", "eur_amount": "1000.000000"},
                        fees={"total": "10.000000", "currency": "EUR", "total_eur": "10.000000"},
                        withholding=wht)
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        expected = _d("1000") - _d("10") - _d("150") - _d("190")
        assert _d(repl["net"]["eur_amount"]) == expected

    def test_wht_source_only(self, client):
        """net = gross - fees - wht_source (no destination).

        Standalone DIVIDEND with source-only withholding. destination=null → wht_dest=0.
        rate_pct is server-derived from 75/500*100 = 15.00 (Amendment H).
        """
        c, fake = client
        _seed(fake, "net_ws_001", txn_type="DIVIDEND",
              gross_eur="500.000000", commission_eur="5.000000")
        wht = {"source": {"country": "US", "rate_pct": "15", "amount_eur": "75.000000"},
               "destination": None}
        resp = _correct(c, "net_ws_001", withholding=wht)
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        expected = _d("500") - _d("5") - _d("75")
        assert _d(repl["net"]["eur_amount"]) == expected

    def test_decimal_precision_6dp(self, client):
        """C.3: Monetary net must be expressed at 6 decimal places."""
        c, fake = client
        _seed(fake, "net_prec_001",
              gross_eur="18250.123456", commission_eur="7.500000")
        resp = _correct(c, "net_prec_001",
                        gross={"amount": "18250.123456", "currency": "EUR",
                               "eur_amount": "18250.123456"},
                        fees={"total": "7.500000", "currency": "EUR",
                              "total_eur": "7.500000"})
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        net_str = repl["net"]["eur_amount"]
        parts = net_str.split(".")
        assert len(parts) == 2 and len(parts[1]) == 6, (
            f"net must have exactly 6 decimal places; got {net_str!r}"
        )
        expected = _d("18250.123456") + _d("7.500000")
        assert _d(net_str) == expected


# ===========================================================================
# TRANSFER correction — blocked (C-15 / §B.6)
# ===========================================================================

class TestTransferCorrectionRejected:
    def test_transfer_out_correction_rejected(self, client):
        """C-15: TRANSFER_OUT correction must return 405; transfers cannot be corrected.

        Implementation guard: correct_movement() raises ValueError with
        'transfer_not_correctable' prefix when txn_type is TRANSFER_OUT.
        Route maps this specific ValueError → 405 with error='transfer_not_correctable'.
        """
        c, fake = client
        _seed(fake, "tx_out_corr_001", txn_type="TRANSFER_OUT",
              extra_fields={"transfer_group_id": "tg_001",
                            "transfer_cost_basis_eur": "5000.000000"})
        resp = _correct(c, "tx_out_corr_001")
        assert resp.status_code == 405, (
            f"Expected 405 for TRANSFER_OUT correction; got {resp.status_code}"
        )
        data = resp.json()
        assert data.get("error") == "transfer_not_correctable"

    def test_transfer_in_correction_rejected(self, client):
        """C-15: TRANSFER_IN correction must return 405."""
        c, fake = client
        _seed(fake, "tx_in_corr_001", txn_type="TRANSFER_IN",
              extra_fields={"transfer_group_id": "tg_002",
                            "transfer_cost_basis_eur": "5000.000000"})
        resp = _correct(c, "tx_in_corr_001")
        assert resp.status_code == 405, (
            f"Expected 405 for TRANSFER_IN correction; got {resp.status_code}"
        )

    def test_transfer_error_detail_mentions_void(self, client):
        """Transfer correction error must guide user to void+recreate workflow."""
        c, fake = client
        _seed(fake, "tx_void_hint_001", txn_type="TRANSFER_OUT",
              extra_fields={"transfer_group_id": "tg_003"})
        resp = _correct(c, "tx_void_hint_001")
        assert resp.status_code == 405
        detail = resp.json().get("detail", "")
        assert "void" in detail.lower(), (
            f"Error must mention 'void' to guide user; got detail={detail!r}"
        )


# ===========================================================================
# Withholding validation (§D.2 — implemented in _validate_correction_fields)
# ===========================================================================

class TestWithholdingValidation:
    def test_invalid_withholding_source_missing_amount_eur(self, client):
        """C-14: withholding.source without amount_eur → 400.

        Amendment H: amount_eur IS required (it's the primary input; rate_pct is server-derived).
        Standalone DIVIDEND correction: individual endpoint is allowed (no ca_group_id).
        """
        c, fake = client
        _seed(fake, "wht_val_001", txn_type="DIVIDEND")
        bad_wht = {"source": {"country": "US", "rate_pct": "15"},  # missing amount_eur
                   "destination": None}
        resp = _correct(c, "wht_val_001", withholding=bad_wht)
        assert resp.status_code == 400, (
            f"Missing withholding.source.amount_eur must return 400; got {resp.status_code}"
        )
        data = resp.json()
        assert data.get("error") == "validation_error"
        assert "amount_eur" in data.get("detail", ""), (
            "Error detail must name the missing field"
        )

    def test_invalid_withholding_dest_missing_amount_eur(self, client):
        """C-14: withholding.destination object without amount_eur → 400.

        Amendment H: amount_eur is the primary WHT input; rate_pct is server-derived.
        """
        c, fake = client
        _seed(fake, "wht_val_002", txn_type="DIVIDEND")
        bad_wht = {"source": {"country": "US", "rate_pct": "15", "amount_eur": "45"},
                   "destination": {"country": "ES", "rate_pct": "19"}}  # missing amount_eur
        resp = _correct(c, "wht_val_002", withholding=bad_wht)
        assert resp.status_code == 400, (
            f"Missing withholding.destination.amount_eur must return 400; got {resp.status_code}"
        )

    def test_buy_withholding_rejected(self, client):
        """BUY correction with non-null withholding → 400 (not applicable to BUY)."""
        c, fake = client
        _seed(fake, "wht_buy_001", txn_type="BUY")
        bad_wht = {"source": {"country": "US", "rate_pct": "15", "amount_eur": "45"}}
        resp = _correct(c, "wht_buy_001", withholding=bad_wht)
        assert resp.status_code == 400, (
            f"BUY withholding correction must return 400; got {resp.status_code}"
        )

    def test_withholding_non_dict_rejected(self, client):
        """withholding must be an object (dict) or null/absent — string value rejected."""
        c, fake = client
        _seed(fake, "wht_str_001", txn_type="DIVIDEND")
        resp = c.post("/api/portfolio/movements/wht_str_001/correct", json={
            "account_id": "_unassigned",
            "correction_note": "test",
            "withholding": "bad-string-value",  # must be dict or null
        })
        assert resp.status_code == 400


# ===========================================================================
# Correction chain (A→B→C double correction)
# ===========================================================================

class TestCorrectionChain:
    def test_double_correction_chain_abc(self, client):
        """A corrected to B; B corrected to C. A and B both SUPERSEDED. C is ACTIVE."""
        c, fake = client
        _seed(fake, "chain_a_001", quantity="100")

        # First correction: A → B
        resp1 = _correct(c, "chain_a_001", quantity="95", note="step 1")
        assert resp1.status_code == 200
        b_id = resp1.json()["replacement"]["id"]

        # Second correction: B → C
        resp2 = _correct(c, b_id, quantity="88", note="step 2")
        assert resp2.status_code == 200, (
            f"Correcting B (the current replacement) must succeed; got {resp2.status_code}"
        )
        c_id = resp2.json()["replacement"]["id"]

        # A must be SUPERSEDED
        a_stored = fake.portfolio_container._store["chain_a_001"]
        assert a_stored["correction_status"] == "SUPERSEDED"

        # B must be SUPERSEDED (corrected to C)
        b_stored = fake.portfolio_container._store[b_id]
        assert b_stored["correction_status"] == "SUPERSEDED"
        assert b_stored.get("superseded_by") == c_id

        # C must be ACTIVE
        c_stored = fake.portfolio_container._store[c_id]
        assert c_stored["correction_status"] == "ACTIVE"
        assert c_stored.get("corrects_movement_id") == b_id

    def test_chain_only_latest_counted_in_holdings(self, client):
        """After A→B chain, only B shares appear in holdings (A excluded)."""
        c, fake = client
        # Seed A (BUY 100) and seed its replacement B (BUY 90) as if corrected
        _seed(fake, "chain_h_a", quantity="100", correction_status="SUPERSEDED",
              extra_fields={"superseded_by": "chain_h_b"})
        _seed(fake, "chain_h_b", quantity="90",
              extra_fields={"corrects_movement_id": "chain_h_a"})

        class _FakeContainer:
            def __init__(self, docs):
                self._docs = list(docs)

            def query_items(self, query="", parameters=None, enable_cross_partition_query=True,
                            partition_key=None):
                results = list(self._docs)
                if "NOT IS_DEFINED(c.deleted_at)" in query:
                    results = [d for d in results if "deleted_at" not in d]
                if "(NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE')" in query:
                    results = [d for d in results if d.get("correction_status", "ACTIVE") == "ACTIVE"]
                return iter(results)

            def read_item(self, *a, **kw):
                raise CosmosResourceNotFoundError("nf", None)

        docs = list(fake.portfolio_container._store.values())
        svc = HoldingsService(
            CosmosPortfolioService(_FakeContainer(docs), None),
            CosmosSecuritiesService(_FakeContainer([])),
        )
        result = svc.compute_holdings()
        aapl = next((h for h in result["holdings"] if h["security_id"] == "XNYS:AAPL"), None)
        assert aapl is not None
        assert _d(aapl["total_shares"]) == _d("90"), (
            "Only B's 90 shares should count; A (SUPERSEDED) must be excluded"
        )


# ===========================================================================
# Imported movement correction
# ===========================================================================

class TestImportedMovementCorrection:
    def test_imported_correction_replacement_has_manual_source(self, client):
        """C-16: Replacement of a csv_import movement gets import_source='manual'."""
        c, fake = client
        _seed(fake, "csv_corr_001", import_source="csv_import",
              extra_fields={"import_session_id": "sess_abc"})
        resp = _correct(c, "csv_corr_001", quantity="75")
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        assert repl["import_source"] == "manual", (
            "Replacement of imported movement must have import_source='manual'"
        )

    def test_original_import_source_preserved(self, client):
        """Original csv_import provenance must survive correction."""
        c, fake = client
        _seed(fake, "csv_orig_001", import_source="csv_import",
              extra_fields={"import_session_id": "sess_def"})
        _correct(c, "csv_orig_001", quantity="80")
        stored_original = fake.portfolio_container._store["csv_orig_001"]
        assert stored_original["import_source"] == "csv_import", (
            "Original import_source must remain 'csv_import'"
        )


# ===========================================================================
# Immutability: security_id, txn_type, account_id
# ===========================================================================

class TestImmutability:
    def test_security_id_not_overridden(self, client):
        """security_id in correction body is ignored; replacement keeps original."""
        c, fake = client
        _seed(fake, "imm_sec_001", security_id="XNYS:AAPL")
        resp = _correct(c, "imm_sec_001",
                        quantity="90",
                        **{"security_id": "XMAD:ITX"})  # ignored field
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        assert repl["security_id"] == "XNYS:AAPL", (
            "security_id must be immutable in corrections"
        )

    def test_txn_type_not_overridden(self, client):
        """txn_type in correction body is ignored; replacement keeps original."""
        c, fake = client
        _seed(fake, "imm_txn_001", txn_type="BUY")
        resp = _correct(c, "imm_txn_001",
                        quantity="90",
                        **{"txn_type": "SELL"})  # ignored field
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        assert repl["txn_type"] == "BUY", "txn_type must be immutable in corrections"

    def test_account_id_mismatch_returns_404(self, client):
        """Sending account_id that doesn't match original's partition → 404."""
        c, fake = client
        _seed(fake, "imm_acct_001", account_id="_unassigned")
        # Attempt correction with a different account_id
        resp = c.post("/api/portfolio/movements/imm_acct_001/correct", json={
            "account_id": "acct_ing_savings",  # does not match
            "correction_note": "try account change",
            "quantity": "90",
        })
        assert resp.status_code == 404, (
            "account_id mismatch (different partition) must return 404; "
            f"got {resp.status_code}"
        )


# ===========================================================================
# Legacy narrow correction (E.2: backward compatibility)
# ===========================================================================

class TestLegacyNarrowCorrection:
    def test_only_note_no_overrides(self, client):
        """E.2: Correction with only correction_note (no overrides) succeeds;
        all original values preserved in replacement."""
        c, fake = client
        _seed(fake, "leg_only_001", quantity="100", gross_eur="18250.000000",
              commission_eur="7.500000", trade_date="2024-01-15")
        resp = _correct(c, "leg_only_001")  # no extra fields
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        assert repl["quantity"] == "100", "quantity must be unchanged"
        assert _d(repl["gross"]["eur_amount"]) == _d("18250"), "gross must be unchanged"
        assert repl["trade_date"] == "2024-01-15", "trade_date must be unchanged"

    def test_only_trade_date_override(self, client):
        """Narrow legacy correction: only trade_date — works, other fields unchanged."""
        c, fake = client
        _seed(fake, "leg_td_001", trade_date="2024-01-10", quantity="200",
              gross_eur="5000.000000", commission_eur="10.000000")
        resp = _correct(c, "leg_td_001", trade_date="2024-01-15")
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        assert repl["trade_date"] == "2024-01-15"
        assert repl["quantity"] == "200", "quantity must be unchanged"
        assert _d(repl["gross"]["eur_amount"]) == _d("5000")

    def test_only_quantity_override(self, client):
        """Narrow legacy correction: only quantity — works, gross/fees unchanged."""
        c, fake = client
        _seed(fake, "leg_qty_001", quantity="100", gross_eur="10000.000000",
              commission_eur="5.000000")
        resp = _correct(c, "leg_qty_001", quantity="90")
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        assert repl["quantity"] == "90"
        assert _d(repl["gross"]["eur_amount"]) == _d("10000"), "gross unchanged"
        # net NOT recomputed (only quantity changed, no gross/fees/wht)
        # net still = original net
        assert _d(repl["net"]["eur_amount"]) == _d("10000") - _d("5"), (
            "net must reflect original computation when only quantity changes"
        )


# ===========================================================================
# Holdings — SELL ACCIONES corrected to DERECHOS (share impact)
# ===========================================================================

class TestHoldingsSELLTypeCorrection:
    class _FakeContainer:
        def __init__(self, docs):
            self._docs = list(docs)

        def query_items(self, query="", parameters=None, enable_cross_partition_query=True,
                        partition_key=None):
            results = list(self._docs)
            if "NOT IS_DEFINED(c.deleted_at)" in query:
                results = [d for d in results if "deleted_at" not in d]
            if "(NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE')" in query:
                results = [d for d in results if d.get("correction_status", "ACTIVE") == "ACTIVE"]
            return iter(results)

        def read_item(self, *a, **kw):
            raise CosmosResourceNotFoundError("nf", None)

    def _holdings(self, movements):
        svc = HoldingsService(
            CosmosPortfolioService(self._FakeContainer(movements), None),
            CosmosSecuritiesService(self._FakeContainer([])),
        )
        return svc.compute_holdings()

    def _mvt(self, mid, txn_type, qty, gross, sales_type=None, commission="0",
             correction_status="ACTIVE"):
        ticker = "AAPL"
        net = str(_d(gross) - _d(commission))
        doc = {
            "id": mid, "doc_type": "ledger_txn", "txn_type": txn_type,
            "security_id": "XNYS:AAPL", "ticker": ticker, "trade_date": "2024-01-15",
            "quantity": str(qty),
            "gross": {"amount": gross, "currency": "EUR", "eur_amount": gross},
            "fees": {"total": commission, "currency": "EUR", "total_eur": commission},
            "net": {"amount": net, "currency": "EUR", "eur_amount": net},
            "account_id": "_unassigned", "correction_status": correction_status,
            "warnings": [],
        }
        if sales_type:
            doc["sales_type"] = sales_type
        return doc

    def test_sell_acciones_corrected_to_derechos_shares_unchanged(self):
        """After ACCIONES→DERECHOS correction: shares stay at original BUY level.

        Scenario:
          BUY  100 shares (ACTIVE)
          SELL  50 shares ACCIONES (SUPERSEDED by correction)
          SELL  50 shares DERECHOS (replacement, ACTIVE)

        With DERECHOS, share count not decremented → total_shares = 100.
        """
        movements = [
            self._mvt("h_buy_001", "BUY", 100, "10000"),
            self._mvt("h_sell_sup", "SELL", 50, "2500",
                      sales_type="ACCIONES", correction_status="SUPERSEDED"),
            self._mvt("h_sell_derechos", "SELL", 50, "2500",
                      sales_type="DERECHOS"),  # replacement
        ]
        result = self._holdings(movements)
        aapl = next(h for h in result["holdings"] if h["security_id"] == "XNYS:AAPL")
        assert _d(aapl["total_shares"]) == _d("100"), (
            "DERECHOS SELL must not decrement shares; total must remain 100"
        )

    def test_sell_derechos_corrected_to_acciones_shares_decremented(self):
        """After DERECHOS→ACCIONES correction: shares decremented."""
        movements = [
            self._mvt("h2_buy_001", "BUY", 100, "10000"),
            self._mvt("h2_sell_sup", "SELL", 50, "2500",
                      sales_type="DERECHOS", correction_status="SUPERSEDED"),
            self._mvt("h2_sell_acc", "SELL", 50, "2500",
                      sales_type="ACCIONES"),  # replacement
        ]
        result = self._holdings(movements)
        aapl = next(h for h in result["holdings"] if h["security_id"] == "XNYS:AAPL")
        assert _d(aapl["total_shares"]) == _d("50"), (
            "ACCIONES SELL must decrement shares by 50; total must be 50"
        )


# ===========================================================================
# Auth / error contracts
# ===========================================================================

class TestEndpointErrorContracts:
    def test_storage_unavailable_503(self, client):
        """When cosmos is unavailable the endpoint returns 503."""
        c, fake = client
        from web.app import app
        # Remove the fake cosmos so _get_cosmos raises RuntimeError → route returns 503
        original_cosmos = app.state.cosmos
        app.state.cosmos = None
        app.state.cosmos_error = "cosmos offline for test"
        try:
            resp = c.post("/api/portfolio/movements/any_id/correct", json={
                "account_id": "_unassigned",
                "correction_note": "test",
            })
        finally:
            app.state.cosmos = original_cosmos
            app.state.cosmos_error = None
        assert resp.status_code == 503

    def test_correct_returns_json_content_type(self, client):
        """Correct endpoint always returns application/json."""
        c, fake = client
        _seed(fake, "ctype_001")
        resp = _correct(c, "ctype_001", quantity="90")
        assert "application/json" in resp.headers.get("content-type", "")

    def test_correct_400_has_error_field(self, client):
        """400 responses must have {'error': ..., 'detail': ...} shape."""
        c, _ = client
        resp = c.post("/api/portfolio/movements/some_id/correct", json={})
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data
