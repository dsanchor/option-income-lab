"""Phase 2 regression tests — Manual movement entry & detail.

Covers:
- POST /api/portfolio/movements — manual BUY / SELL / DIVIDEND
- GET  /api/portfolio/movements/{movement_id} — detail with correction chain

Request shape for POST:
{
    "txn_type": "BUY",
    "security_id": "XNYS:AAPL",
    "trade_date": "2024-01-15",
    "quantity": "100",
    "gross": {"amount": "18250", "currency": "EUR", "eur_amount": "18250"},
    "fees": {"total": "7.50", "currency": "EUR", "total_eur": "7.50"},
    "account_id": "_unassigned"
}

GET response shape:
{
    "movement": { ...full doc... },
    "superseded_by": null | { ...replacement doc... }
}
"""

from __future__ import annotations

import pytest
from decimal import Decimal
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from fastapi.testclient import TestClient

from src.portfolio.cosmos_portfolio import CosmosPortfolioService
from src.portfolio.cosmos_securities import CosmosSecuritiesService
from src.portfolio.holdings_service import HoldingsService
from tests.conftest_portfolio_p2 import FakeCosmos


@pytest.fixture
def client():
    from web.app import app
    fake = FakeCosmos()
    with TestClient(app) as c:
        app.state.cosmos = fake
        app.state.cosmos_error = None
        yield c, fake


def _buy_body(security_id="XNYS:AAPL", quantity="100",
              gross_eur="18250", commission_eur="7.50",
              account_id="_unassigned", trade_date="2024-01-15"):
    return {
        "txn_type": "BUY",
        "security_id": security_id,
        "trade_date": trade_date,
        "quantity": quantity,
        "gross": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur},
        "fees": {"total": commission_eur, "currency": "EUR", "total_eur": commission_eur},
        "account_id": account_id,
    }


def _sell_body(security_id="XNYS:AAPL", quantity="50",
               gross_eur="9500", commission_eur="5.00",
               sales_type="ACCIONES", account_id="_unassigned",
               trade_date="2024-06-01"):
    return {
        "txn_type": "SELL",
        "security_id": security_id,
        "trade_date": trade_date,
        "quantity": quantity,
        "gross": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur},
        "fees": {"total": commission_eur, "currency": "EUR", "total_eur": commission_eur},
        "sales_type": sales_type,
        "account_id": account_id,
    }


def _dividend_body(security_id="XNYS:AAPL", gross_eur="100.00",
                   net_eur="73.31", wht_source_eur="12.94",
                   account_id="_unassigned", trade_date="2024-06-15"):
    return {
        "txn_type": "DIVIDEND",
        "security_id": security_id,
        "trade_date": trade_date,
        "quantity": "0",
        "gross": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur},
        "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
        "withholding": {
            "source": {"amount_eur": wht_source_eur},
            "destination": None,
        },
        "account_id": account_id,
    }


def _option_body(txn_type="CALL_SELL", gross_amount="150.00", fees_amount="3.50",
                 gross_eur="138.00", fees_eur="3.25", account_id="_unassigned",
                 trade_date="2024-07-19", option_position_id="pos_call_001",
                 option_link_kind="OPEN_SELL", option_type="call",
                 option_close_date=None):
    return {
        "txn_type": txn_type,
        "security_id": "XNYS:AAPL",
        "trade_date": trade_date,
        "quantity": "0",
        "gross": {"amount": gross_amount, "currency": "USD", "eur_amount": gross_eur},
        "fees": {"total": fees_amount, "currency": "USD", "total_eur": fees_eur},
        "account_id": account_id,
        "option_position_id": option_position_id,
        "option_link_kind": option_link_kind,
        "option_type": option_type,
        "option_strike": 210.0,
        "option_expiration": "2024-07-19",
        "option_symbol": "AAPL",
        "option_close_date": option_close_date,
    }


def _seed_movement(fake, movement_id, security_id, txn_type, quantity, gross_eur,
                   account_id="_unassigned", commission_eur="0", trade_date="2024-01-15",
                   sales_type=None, correction_status="ACTIVE"):
    ticker = security_id.split(":")[-1]
    net = str(Decimal(gross_eur) - Decimal(commission_eur))
    doc = {
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
        "cost_basis_status": "COMPLETE",
        "correction_status": correction_status,
        "import_source": "manual",
        "created_at": "2026-09-06T10:00:00Z",
        "warnings": [],
    }
    if sales_type:
        doc["sales_type"] = sales_type
    fake.portfolio_container._store[movement_id] = doc
    return doc


# ===========================================================================
# BUY creation
# ===========================================================================

class TestManualBuy:
    def test_post_buy_201(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/movements", json=_buy_body())
        assert resp.status_code == 201
        doc = resp.json()
        assert doc["txn_type"] == "BUY"
        assert doc["security_id"] == "XNYS:AAPL"
        assert "id" in doc

    def test_post_buy_import_source_manual(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/movements", json=_buy_body())
        assert resp.status_code == 201
        assert resp.json()["import_source"] == "manual"

    def test_post_buy_fees_stored(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/movements", json=_buy_body(commission_eur="7.50"))
        assert resp.status_code == 201
        fees = resp.json().get("fees", {})
        assert fees.get("total_eur") == "7.50"

    def test_post_buy_account_id_stored(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/movements", json=_buy_body(account_id="acct_heytrade_x"))
        assert resp.status_code == 201
        assert resp.json()["account_id"] == "acct_heytrade_x"

    def test_post_buy_defaults_to_unassigned(self, client):
        c, _ = client
        body = _buy_body()
        del body["account_id"]
        resp = c.post("/api/portfolio/movements", json=body)
        assert resp.status_code == 201
        assert resp.json().get("account_id", "_unassigned") == "_unassigned"

    def test_post_buy_missing_security_id_400(self, client):
        c, _ = client
        body = _buy_body()
        del body["security_id"]
        resp = c.post("/api/portfolio/movements", json=body)
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_post_buy_missing_trade_date_400(self, client):
        c, _ = client
        body = _buy_body()
        del body["trade_date"]
        resp = c.post("/api/portfolio/movements", json=body)
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_post_buy_missing_gross_400(self, client):
        c, _ = client
        body = _buy_body()
        del body["gross"]
        resp = c.post("/api/portfolio/movements", json=body)
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_post_buy_invalid_txn_type_400(self, client):
        c, _ = client
        body = _buy_body()
        body["txn_type"] = "TRANSFER"
        resp = c.post("/api/portfolio/movements", json=body)
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_post_buy_creates_correction_status_active(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/movements", json=_buy_body())
        assert resp.status_code == 201
        assert resp.json().get("correction_status") == "ACTIVE"


# ===========================================================================
# SELL ACCIONES vs DERECHOS
# ===========================================================================

class TestManualSell:
    def test_post_sell_acciones_201(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/movements", json=_sell_body(sales_type="ACCIONES"))
        assert resp.status_code == 201
        assert resp.json()["sales_type"] == "ACCIONES"

    def test_post_sell_derechos_201(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/movements", json=_sell_body(sales_type="DERECHOS"))
        assert resp.status_code == 201
        assert resp.json()["sales_type"] == "DERECHOS"

    def test_sell_no_sales_type_defaults_acciones(self, client):
        c, _ = client
        body = _sell_body()
        del body["sales_type"]
        resp = c.post("/api/portfolio/movements", json=body)
        assert resp.status_code == 201
        assert resp.json()["sales_type"] == "ACCIONES"

    def test_sell_invalid_sales_type_400(self, client):
        c, _ = client
        body = _sell_body(sales_type="BONOS")
        resp = c.post("/api/portfolio/movements", json=body)
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"


# ===========================================================================
# DIVIDEND creation
# ===========================================================================

class TestManualDividend:
    def test_post_dividend_201(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/movements", json=_dividend_body())
        assert resp.status_code == 201
        assert resp.json()["txn_type"] == "DIVIDEND"

    def test_dividend_fees_are_zero(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/movements", json=_dividend_body())
        assert resp.status_code == 201
        fees = resp.json().get("fees", {})
        assert Decimal(fees.get("total_eur", "0")) == Decimal("0")

    def test_dividend_withholding_stored(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/movements", json=_dividend_body(wht_source_eur="12.94"))
        assert resp.status_code == 201
        wht = resp.json().get("withholding", {})
        src = wht.get("source") or {}
        assert Decimal(src.get("amount_eur", "0")) == Decimal("12.94")


# ===========================================================================
# Option movement creation
# ===========================================================================

class TestManualOptionMovements:
    def test_option_sell_allows_missing_option_position_id(self, client):
        c, _ = client
        body = _option_body()
        del body["option_position_id"]
        resp = c.post("/api/portfolio/movements", json=body)
        assert resp.status_code == 201
        assert resp.json().get("option_position_id") is None

    def test_option_sell_persists_metadata_and_zero_quantity(self, client):
        c, _ = client
        resp = c.post(
            "/api/portfolio/movements",
            json=_option_body(option_close_date="2024-07-26"),
        )
        assert resp.status_code == 201
        doc = resp.json()
        assert doc["txn_type"] == "CALL_SELL"
        assert doc["quantity"] == "0"
        assert doc["option_position_id"] == "pos_call_001"
        assert doc["option_link_kind"] == "OPEN_SELL"
        assert doc["option_type"] == "call"
        assert doc["option_close_date"] == "2024-07-26"
        assert Decimal(doc["net"]["amount"]) == Decimal("146.500000")
        assert Decimal(doc["net"]["eur_amount"]) == Decimal("134.750000")

    def test_option_unlinked_movement_warning_only_on_listing(self, client):
        c, _ = client
        create_resp = c.post(
            "/api/portfolio/movements",
            json=_option_body(option_position_id=None),
        )
        assert create_resp.status_code == 201
        movement_id = create_resp.json()["id"]

        list_resp = c.get("/api/portfolio/movements")
        assert list_resp.status_code == 200
        listed = next(m for m in list_resp.json()["movements"] if m["id"] == movement_id)
        assert listed["movement_warnings"] == ["OPTION_MOVEMENT_UNLINKED"]


# ===========================================================================
# Movement detail endpoint
# ===========================================================================

class TestMovementDetail:
    def test_get_detail_200(self, client):
        c, fake = client
        _seed_movement(fake, "mvt_detail_001", "XNYS:AAPL", "BUY", 100, "18250")
        resp = c.get("/api/portfolio/movements/mvt_detail_001?account_id=_unassigned")
        assert resp.status_code == 200
        assert "movement" in resp.json()

    def test_get_detail_has_movement_facts(self, client):
        c, fake = client
        _seed_movement(fake, "mvt_facts_001", "XMAD:SAN", "DIVIDEND", 0, "45.30")
        resp = c.get("/api/portfolio/movements/mvt_facts_001?account_id=_unassigned")
        assert resp.status_code == 200
        mvt = resp.json()["movement"]
        assert mvt["security_id"] == "XMAD:SAN"
        assert mvt["txn_type"] == "DIVIDEND"
        assert "gross" in mvt
        assert "fees" in mvt
        assert "net" in mvt

    def test_get_detail_has_audit_metadata(self, client):
        c, fake = client
        _seed_movement(fake, "mvt_audit_001", "XNYS:AAPL", "BUY", 100, "18250")
        resp = c.get("/api/portfolio/movements/mvt_audit_001?account_id=_unassigned")
        assert resp.status_code == 200
        mvt = resp.json()["movement"]
        assert mvt.get("import_source") == "manual"
        assert "created_at" in mvt

    def test_get_detail_404(self, client):
        c, _ = client
        resp = c.get("/api/portfolio/movements/mvt_ghost?account_id=_unassigned")
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    def test_get_detail_wrong_account_404(self, client):
        c, fake = client
        _seed_movement(fake, "mvt_part_001", "XNYS:AAPL", "BUY", 100, "18250",
                       account_id="acct_heytrade_x")
        resp = c.get("/api/portfolio/movements/mvt_part_001?account_id=wrong")
        assert resp.status_code == 404

    def test_get_detail_no_account_defaults_unassigned(self, client):
        c, fake = client
        _seed_movement(fake, "mvt_def_001", "XNYS:AAPL", "BUY", 100, "18250",
                       account_id="_unassigned")
        resp = c.get("/api/portfolio/movements/mvt_def_001")
        assert resp.status_code == 200

    def test_get_detail_superseded_by_null_when_active(self, client):
        c, fake = client
        _seed_movement(fake, "mvt_active_001", "XNYS:AAPL", "BUY", 100, "18250")
        resp = c.get("/api/portfolio/movements/mvt_active_001?account_id=_unassigned")
        assert resp.status_code == 200
        assert resp.json()["superseded_by"] is None

    def test_get_detail_superseded_by_included_when_corrected(self, client):
        c, fake = client
        # Seed original with superseded_by pointer
        orig = _seed_movement(fake, "mvt_orig_det", "XNYS:AAPL", "BUY", 100, "18250")
        orig["superseded_by"] = "mvt_repl_det"
        fake.portfolio_container._store["mvt_orig_det"] = orig
        # Seed replacement
        _seed_movement(fake, "mvt_repl_det", "XNYS:AAPL", "BUY", 95, "17337.50")
        resp = c.get("/api/portfolio/movements/mvt_orig_det?account_id=_unassigned")
        assert resp.status_code == 200
        data = resp.json()
        assert data["superseded_by"] is not None
        assert data["superseded_by"]["id"] == "mvt_repl_det"


# ===========================================================================
# Unit tests — HoldingsService SELL ACCIONES/DERECHOS
# ===========================================================================

class FakePortfolioForHoldings:
    def __init__(self, movements):
        self._movements = list(movements)

    def query_items(self, query="", parameters=None, enable_cross_partition_query=True,
                    partition_key=None):
        results = self._movements
        if "NOT IS_DEFINED(c.deleted_at)" in query:
            results = [m for m in results if "deleted_at" not in m]
        if "(NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE')" in query:
            results = [m for m in results if m.get("correction_status", "ACTIVE") == "ACTIVE"]
        return iter(results)

    def read_item(self, item=None, partition_key=None, **kw):
        for m in self._movements:
            if m.get("id") == item:
                return dict(m)
        raise CosmosResourceNotFoundError(message="nf", response=None)

    def upsert_item(self, b): self._movements.append(b); return b
    def replace_item(self, i, b): return b


class FakeSymbolsForHoldings:
    def query_items(self, **kw): return iter([])
    def read_item(self, *a, **kw): raise CosmosResourceNotFoundError("nf", None)


def _make_svc(movements):
    p = CosmosPortfolioService(FakePortfolioForHoldings(movements), None)
    s = CosmosSecuritiesService(FakeSymbolsForHoldings())
    return HoldingsService(p, s)


def _mvt(mid, sid, txn, qty, gross, acct="_unassigned", commission="0",
         trade_date="2024-01-15", sales_type=None, correction_status="ACTIVE"):
    ticker = sid.split(":")[-1]
    net = str(Decimal(gross) - Decimal(commission))
    doc = {
        "id": mid, "doc_type": "ledger_txn", "txn_type": txn,
        "security_id": sid, "ticker": ticker, "trade_date": trade_date,
        "quantity": str(qty),
        "gross": {"amount": gross, "currency": "EUR", "eur_amount": gross},
        "fees": {"total": commission, "currency": "EUR", "total_eur": commission},
        "net": {"amount": net, "currency": "EUR", "eur_amount": net},
        "account_id": acct, "cost_basis_status": "COMPLETE",
        "correction_status": correction_status, "warnings": [],
    }
    if sales_type:
        doc["sales_type"] = sales_type
    return doc


class TestSellTypeHoldingsUnit:
    def test_acciones_decrements_shares(self):
        movements = [
            _mvt("b1", "XNYS:AAPL", "BUY", 100, "18250"),
            _mvt("s1", "XNYS:AAPL", "SELL", 40, "7600", sales_type="ACCIONES"),
        ]
        result = _make_svc(movements).compute_holdings()
        aapl = next(h for h in result["holdings"] if h["security_id"] == "XNYS:AAPL")
        assert Decimal(aapl["total_shares"]) == Decimal("60")

    def test_derechos_does_not_decrement_shares(self):
        movements = [
            _mvt("b2", "XNYS:AAPL", "BUY", 100, "18250"),
            _mvt("d1", "XNYS:AAPL", "SELL", 0, "300", sales_type="DERECHOS"),
        ]
        result = _make_svc(movements).compute_holdings()
        aapl = next(h for h in result["holdings"] if h["security_id"] == "XNYS:AAPL")
        assert Decimal(aapl["total_shares"]) == Decimal("100"), (
            "DERECHOS sale must NOT reduce share count"
        )

    def test_derechos_proceeds_in_total_sales_eur(self):
        movements = [
            _mvt("b3", "XNYS:AAPL", "BUY", 100, "18250"),
            _mvt("d2", "XNYS:AAPL", "SELL", 0, "500", sales_type="DERECHOS"),
        ]
        result = _make_svc(movements).compute_holdings()
        aapl = next(h for h in result["holdings"] if h["security_id"] == "XNYS:AAPL")
        assert Decimal(aapl["total_sales_eur"]) == Decimal("500.00")

    def test_no_sales_type_defaults_to_acciones(self):
        movements = [
            _mvt("b4", "XNYS:AAPL", "BUY", 100, "18250"),
            _mvt("s2", "XNYS:AAPL", "SELL", 30, "5700"),  # no sales_type
        ]
        result = _make_svc(movements).compute_holdings()
        aapl = next(h for h in result["holdings"] if h["security_id"] == "XNYS:AAPL")
        assert Decimal(aapl["total_shares"]) == Decimal("70"), (
            "Legacy SELL without sales_type defaults to ACCIONES (decrements shares)"
        )

    def test_dividend_does_not_change_shares(self):
        movements = [
            _mvt("b5", "XNYS:AAPL", "BUY", 50, "9125"),
            _mvt("div1", "XNYS:AAPL", "DIVIDEND", 0, "86.25"),
        ]
        result = _make_svc(movements).compute_holdings()
        aapl = next(h for h in result["holdings"] if h["security_id"] == "XNYS:AAPL")
        assert Decimal(aapl["total_shares"]) == Decimal("50")
