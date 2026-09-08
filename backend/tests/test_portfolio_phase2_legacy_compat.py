"""Phase 2 regression tests — Legacy compatibility and cross-cutting invariants.

Verifies that Phase 2 changes do NOT regress Phase 1 behavior:
- Existing imports (CSV) still work unchanged.
- _unassigned account_id still silently accepted everywhere.
- Rights-sale invariant (DERECHOS) holds after all Phase 2 operations.
- Holdings service exclusions (deleted, superseded) do not affect unrelated movements.
- Endpoint auth/error contract shapes unchanged (error codes unchanged).
- Portfolio movements list pagination unchanged.
- Securities catalog unaffected by portfolio Phase 2 changes.
- Existing test_portfolio_endpoints.py contract signatures unchanged.

These tests must ALL pass even before Phase 2 is implemented (they guard Phase 1).
"""

from __future__ import annotations

import io
import pytest
from decimal import Decimal
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from fastapi.testclient import TestClient

from src.portfolio.cosmos_portfolio import CosmosPortfolioService
from src.portfolio.cosmos_securities import CosmosSecuritiesService
from src.portfolio.holdings_service import HoldingsService


# ---------------------------------------------------------------------------
# Reuse fake containers from existing test patterns
# ---------------------------------------------------------------------------

class FakePortfolioContainer:
    def __init__(self):
        self._store: dict = {}

    def read(self):
        return {}

    def upsert_item(self, body):
        self._store[body["id"]] = dict(body)
        return dict(body)

    def create_item(self, body):
        self._store[body["id"]] = dict(body)
        return dict(body)

    def read_item(self, item, partition_key):
        if item not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        doc = self._store[item]
        if partition_key is not None and doc.get("account_id") != partition_key:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(doc)

    def replace_item(self, item, body):
        self._store[item] = dict(body)
        return dict(body)

    def delete_item(self, item=None, partition_key=None, **kw):
        if item is None:
            item = kw.get("item")
        if item in self._store:
            del self._store[item]

    def query_items(self, query="", parameters=None, enable_cross_partition_query=True, partition_key=None):
        param_map = {p["name"]: p["value"] for p in (parameters or [])}
        results = list(self._store.values())
        if "NOT IS_DEFINED(c.deleted_at)" in query:
            results = [d for d in results if "deleted_at" not in d]
        if "ledger_txn" in query:
            results = [d for d in results if d.get("doc_type") == "ledger_txn"]
        if "@account_id" in param_map:
            results = [d for d in results if d.get("account_id") == param_map["@account_id"]]
        if "COUNT" in query:
            return iter([len(results)])
        return iter(results)


class FakeImportSessionsContainer:
    def __init__(self):
        self._store: dict = {}

    def read(self):
        return {}

    def create_item(self, body):
        self._store[body["id"]] = dict(body)
        return dict(body)

    def read_item(self, item, partition_key):
        if item not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[item])

    def replace_item(self, item, body):
        self._store[item] = dict(body)
        return dict(body)

    def query_items(self, query="", parameters=None, enable_cross_partition_query=True, partition_key=None):
        return iter([])


class FakeSymbolsContainer:
    def __init__(self):
        self._store: dict = {}

    def read(self):
        return {}

    def read_item(self, item, partition_key):
        key = (partition_key, item)
        if key not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[key])

    def create_item(self, body):
        ticker = body.get("symbol", body.get("ticker", ""))
        key = (ticker, body["id"])
        self._store[key] = dict(body)
        return dict(body)

    def query_items(self, query="", parameters=None, enable_cross_partition_query=False, partition_key=None):
        param_map = {p["name"]: p["value"] for p in (parameters or [])}
        results = [v for v in self._store.values() if v.get("doc_type") == "security_master"]
        if "@isin" in param_map:
            results = [r for r in results if r.get("isin") == param_map["@isin"]]
        return iter(results)

    def replace_item(self, item, body):
        for key in self._store:
            if self._store[key].get("id") == item:
                self._store[key] = dict(body)
                return dict(body)
        raise CosmosResourceNotFoundError(message="not found", response=None)

    def upsert_item(self, body):
        ticker = body.get("symbol", body.get("ticker", ""))
        key = (ticker, body["id"])
        self._store[key] = dict(body)
        return dict(body)


class FakeCosmos:
    def __init__(self):
        self.container = FakeSymbolsContainer()
        self.portfolio_container = FakePortfolioContainer()
        self.import_sessions_container = FakeImportSessionsContainer()

    def list_symbols(self):
        return []

    def get_symbol(self, symbol):
        return None


@pytest.fixture
def client():
    from web.app import app
    fake_cosmos = FakeCosmos()
    with TestClient(app) as c:
        app.state.cosmos = fake_cosmos
        app.state.cosmos_error = None
        yield c, fake_cosmos


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PURCHASES_CSV = (
    "Año\tEmpresa\tFecha compra\tValor compra\tAcciones\tTotal (€)\tComisión\n"
    "2024\tApple Inc.\t10/01/2024\t182,50\t10\t1.825,00\t7,50\n"
).encode()

_SALES_CSV_6COL = (
    "Año\tEmpresa\tFecha venta\tAcciones\tComisión\tTotal Venta\n"
    "2024\tApple Inc.\t15/06/2024\t5\t5,00\t950,00\n"
).encode()

_SALES_CSV_7COL = (
    "Año\tEmpresa\tFecha venta\tAcciones\tComisión\tTotal Venta\tTipo\n"
    "2024\tApple Inc.\t15/06/2024\t5\t5,00\t950,00\tAcciones\n"
).encode()

_SALES_CSV_DERECHOS = (
    "Año\tEmpresa\tFecha venta\tAcciones\tComisión\tTotal Venta\tTipo\n"
    "2024\tApple Inc.\t15/06/2024\t0\t0,00\t300,00\tDerechos\n"
).encode()

_DIVIDENDS_CSV = (
    "Año\tEmpresa\tFecha de cobro\tImporte Bruto\tImporte Neto\t"
    "Importe en Derechos\tRetención Origen\tRetención Destino\n"
    "2024\tApple Inc.\t15/06/2024\t100,00\t73,31\t0,00\t12,94\t13,75\n"
).encode()


def _make_movement(
    movement_id, security_id, txn_type, quantity, gross_eur,
    account_id="_unassigned", commission_eur="0", net_eur=None,
    cost_basis_status="COMPLETE", trade_date="2024-01-15",
    sales_type=None,
):
    ticker = security_id.split(":")[-1]
    net = net_eur or str(Decimal(gross_eur) - Decimal(commission_eur))
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
        "cost_basis_status": cost_basis_status,
        "import_source": "csv_import",
        "warnings": [],
    }
    if sales_type:
        doc["sales_type"] = sales_type
    return doc


# ---------------------------------------------------------------------------
# Tests — Phase 1 endpoints unbroken
# ---------------------------------------------------------------------------

class TestLegacySecuritiesCatalog:
    """Securities catalog endpoints from Phase 1 must continue to work."""

    def test_list_securities_still_200(self, client):
        c, _ = client
        resp = c.get("/api/securities")
        assert resp.status_code == 200
        assert "securities" in resp.json()

    def test_create_security_still_201(self, client):
        c, _ = client
        resp = c.post("/api/securities", json={
            "ticker": "AAPL",
            "company_name": "Apple Inc.",
            "exchange_mic": "XNYS",
        })
        assert resp.status_code == 201
        assert resp.json()["security_id"] == "XNYS:AAPL"

    def test_get_security_still_works(self, client):
        c, _ = client
        c.post("/api/securities", json={
            "ticker": "MSFT",
            "company_name": "Microsoft Corp.",
            "exchange_mic": "XNAS",
        })
        resp = c.get("/api/securities/XNAS:MSFT")
        assert resp.status_code == 200

    def test_security_not_found_still_404(self, client):
        c, _ = client
        resp = c.get("/api/securities/XNYS:ZZZZZ")
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"


class TestLegacyImportSessions:
    """Import session endpoints from Phase 1 must continue to work."""

    def test_create_import_session_still_201(self, client):
        c, _ = client
        resp = c.post(
            "/api/import/sessions",
            files={"file": ("test.csv", io.BytesIO(_PURCHASES_CSV), "text/csv")},
            data={"format_hint": "purchases"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["session_id"].startswith("imp_")
        assert "questions" in data
        assert "state" in data

    def test_import_empty_file_still_400(self, client):
        c, _ = client
        resp = c.post(
            "/api/import/sessions",
            files={"file": ("empty.csv", io.BytesIO(b""), "text/csv")},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "parse_error"

    def test_import_default_account_still_unassigned(self, client):
        """CSV import without account_id still defaults to _unassigned."""
        c, _ = client
        resp = c.post(
            "/api/import/sessions",
            files={"file": ("test.csv", io.BytesIO(_PURCHASES_CSV), "text/csv")},
            data={"format_hint": "purchases"},
        )
        assert resp.status_code == 201
        assert resp.json().get("account_id") == "_unassigned"

    def test_session_not_found_still_404(self, client):
        c, _ = client
        resp = c.get("/api/import/sessions/imp_nonexistent")
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"


class TestLegacyHoldingsAndMovements:
    """Holdings and movements endpoints from Phase 1 must continue to work."""

    def test_holdings_empty_still_200(self, client):
        c, _ = client
        resp = c.get("/api/portfolio/holdings")
        assert resp.status_code == 200
        data = resp.json()
        assert "holdings" in data
        assert "summary" in data

    def test_movements_empty_still_200(self, client):
        c, _ = client
        resp = c.get("/api/portfolio/movements")
        assert resp.status_code == 200
        data = resp.json()
        assert "movements" in data
        assert "total_count" in data
        assert "limit" in data
        assert "offset" in data

    def test_delete_unassigned_movement_still_200(self, client):
        """F6 fix: DELETE without account_id still defaults to _unassigned."""
        c, fake = client
        fake.portfolio_container._store["txn__unassigned_legacy"] = {
            "id": "txn__unassigned_legacy",
            "doc_type": "ledger_txn",
            "account_id": "_unassigned",
            "txn_type": "BUY",
        }
        resp = c.delete("/api/portfolio/movements/txn__unassigned_legacy")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "txn__unassigned_legacy"
        assert data.get("deleted") is True
        assert "txn__unassigned_legacy" not in fake.portfolio_container._store

    def test_movements_pagination_still_works(self, client):
        c, _ = client
        resp = c.get("/api/portfolio/movements?limit=10&offset=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] == 10
        assert data["offset"] == 5

    def test_movements_filter_by_account_id(self, client):
        """account_id filter still narrows results correctly."""
        c, fake = client
        fake.portfolio_container._store["txn_acct_a"] = _make_movement(
            "txn_acct_a", "XNYS:AAPL", "BUY", 100, "18250", account_id="ibkr"
        )
        fake.portfolio_container._store["txn_acct_b"] = _make_movement(
            "txn_acct_b", "XNYS:AAPL", "BUY", 50, "9125", account_id="_unassigned"
        )
        resp = c.get("/api/portfolio/movements?account_id=ibkr")
        assert resp.status_code == 200
        ids = [m["id"] for m in resp.json()["movements"]]
        assert "txn_acct_a" in ids
        assert "txn_acct_b" not in ids


# ---------------------------------------------------------------------------
# Tests — rights-sale invariant cross-cutting
# ---------------------------------------------------------------------------

class FakePortfolioHoldings:
    def __init__(self, movements):
        self._movements = list(movements)

    def query_items(self, query="", parameters=None, enable_cross_partition_query=True, partition_key=None):
        results = self._movements
        if "NOT IS_DEFINED(c.deleted_at)" in query:
            results = [m for m in results if "deleted_at" not in m]
        if "NOT IS_DEFINED(c.superseded_by)" in query:
            results = [m for m in results if "superseded_by" not in m]
        return iter(results)

    def read_item(self, item, partition_key):
        for m in self._movements:
            if m.get("id") == item:
                return dict(m)
        raise CosmosResourceNotFoundError(message="not found", response=None)

    def upsert_item(self, body):
        self._movements.append(body)
        return body

    def replace_item(self, item, body):
        for i, m in enumerate(self._movements):
            if m.get("id") == item:
                self._movements[i] = dict(body)
                return dict(body)
        raise CosmosResourceNotFoundError(message="not found", response=None)


class FakeSymbols:
    def __init__(self):
        pass

    def query_items(self, **kw):
        return iter([])

    def read_item(self, item, partition_key):
        raise CosmosResourceNotFoundError(message="not found", response=None)


def _make_holdings_service(movements):
    portfolio_svc = CosmosPortfolioService(FakePortfolioHoldings(movements), None)
    securities_svc = CosmosSecuritiesService(FakeSymbols())
    return HoldingsService(portfolio_svc, securities_svc)


class TestRightsSaleInvariant:
    """The DERECHOS rights-sale invariant must hold in all configurations."""

    def test_derechos_from_csv_does_not_decrement_shares(self):
        """CSV-imported DERECHOS (with sales_type='DERECHOS') does not reduce shares."""
        movements = [
            _make_movement("buy_d1", "XNYS:AAPL", "BUY", 100, "18250"),
            _make_movement("sell_d1", "XNYS:AAPL", "SELL", 0, "300",
                           sales_type="DERECHOS"),
        ]
        svc = _make_holdings_service(movements)
        result = svc.compute_holdings()
        aapl = next((h for h in result["holdings"] if h["security_id"] == "XNYS:AAPL"), None)
        assert aapl is not None
        assert Decimal(aapl["total_shares"]) == Decimal("100"), (
            "DERECHOS sale from CSV must not decrement shares"
        )

    def test_acciones_from_6col_csv_decrements_shares(self):
        """6-column CSV SELL (no Tipo column, defaults to ACCIONES) decrements shares."""
        # A movement without sales_type defaults to ACCIONES behavior
        movements = [
            _make_movement("buy_a1", "XNYS:AAPL", "BUY", 100, "18250"),
            _make_movement("sell_a1", "XNYS:AAPL", "SELL", 30, "5700"),
            # No sales_type — defaults to ACCIONES
        ]
        svc = _make_holdings_service(movements)
        result = svc.compute_holdings()
        aapl = next((h for h in result["holdings"] if h["security_id"] == "XNYS:AAPL"), None)
        assert aapl is not None
        assert Decimal(aapl["total_shares"]) == Decimal("70"), (
            "6-column SELL defaults to ACCIONES and must decrement shares"
        )

    def test_derechos_does_not_affect_other_securities(self):
        """DERECHOS sale for security A does not affect security B holdings."""
        movements = [
            _make_movement("buy_aapl", "XNYS:AAPL", "BUY", 100, "18250"),
            _make_movement("buy_msft", "XNAS:MSFT", "BUY", 50, "9000"),
            _make_movement("sell_aapl_dr", "XNYS:AAPL", "SELL", 0, "300",
                           sales_type="DERECHOS"),
        ]
        svc = _make_holdings_service(movements)
        result = svc.compute_holdings()
        msft = next((h for h in result["holdings"] if h["security_id"] == "XNAS:MSFT"), None)
        assert msft is not None
        assert Decimal(msft["total_shares"]) == Decimal("50"), (
            "DERECHOS sale must not affect unrelated security holdings"
        )

    def test_mixed_acciones_and_derechos_correct_share_count(self):
        """Mix of ACCIONES and DERECHOS sales computes correct net shares."""
        movements = [
            _make_movement("buy_mix", "XNYS:AAPL", "BUY", 200, "36500"),
            # ACCIONES: reduces shares
            _make_movement("sell_acc", "XNYS:AAPL", "SELL", 50, "9500",
                           sales_type="ACCIONES"),
            # DERECHOS: does not reduce shares
            _make_movement("sell_der", "XNYS:AAPL", "SELL", 0, "500",
                           sales_type="DERECHOS"),
        ]
        svc = _make_holdings_service(movements)
        result = svc.compute_holdings()
        aapl = next((h for h in result["holdings"] if h["security_id"] == "XNYS:AAPL"), None)
        assert aapl is not None
        # 200 - 50 (ACCIONES) = 150; DERECHOS doesn't reduce
        assert Decimal(aapl["total_shares"]) == Decimal("150")

    def test_derechos_proceeds_in_total_sales_eur(self):
        """DERECHOS gross is included in total_sales_eur (it's a sale, just not shares)."""
        movements = [
            _make_movement("buy_ts", "XNYS:AAPL", "BUY", 100, "18250"),
            _make_movement("sell_ts_dr", "XNYS:AAPL", "SELL", 0, "500",
                           commission_eur="0", sales_type="DERECHOS"),
        ]
        svc = _make_holdings_service(movements)
        result = svc.compute_holdings()
        aapl = next((h for h in result["holdings"] if h["security_id"] == "XNYS:AAPL"), None)
        assert aapl is not None
        assert Decimal(aapl["total_sales_eur"]) == Decimal("500.00"), (
            "DERECHOS proceeds must appear in total_sales_eur"
        )


# ---------------------------------------------------------------------------
# Tests — storage unavailable contract (Phase 2 endpoints included)
# ---------------------------------------------------------------------------

class TestStorageUnavailable:
    """503 error contract must work for all portfolio endpoints."""

    def test_holdings_503_when_portfolio_unavailable(self, client):
        c, fake = client
        fake.portfolio_container = None
        resp = c.get("/api/portfolio/holdings")
        assert resp.status_code == 503
        assert resp.json()["error"] == "storage_unavailable"

    def test_movements_503_when_portfolio_unavailable(self, client):
        c, fake = client
        fake.portfolio_container = None
        resp = c.get("/api/portfolio/movements")
        assert resp.status_code == 503
        assert resp.json()["error"] == "storage_unavailable"

    def test_import_session_503_when_sessions_unavailable(self, client):
        c, fake = client
        fake.import_sessions_container = None
        resp = c.post(
            "/api/import/sessions",
            files={"file": ("test.csv", io.BytesIO(_PURCHASES_CSV), "text/csv")},
            data={"format_hint": "purchases"},
        )
        assert resp.status_code == 503
        assert resp.json()["error"] == "storage_unavailable"


# ---------------------------------------------------------------------------
# Tests — legacy document compatibility (no migration needed)
# ---------------------------------------------------------------------------

class TestLegacyDocumentCompatibility:
    """Pre-Phase-2 ledger_txn documents (without Phase 2 fields) still compute correctly."""

    def test_movement_without_sales_type_treated_as_acciones(self):
        """Legacy SELL movement (no sales_type field) defaults to ACCIONES behavior."""
        movements = [
            _make_movement("buy_leg", "XNYS:AAPL", "BUY", 100, "18250"),
            # Old-style SELL without sales_type
            {
                "id": "sell_leg",
                "doc_type": "ledger_txn",
                "txn_type": "SELL",
                "security_id": "XNYS:AAPL",
                "ticker": "AAPL",
                "trade_date": "2024-06-01",
                "quantity": "40",
                "gross": {"amount": "7600", "currency": "EUR", "eur_amount": "7600"},
                "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
                "net": {"amount": "7600", "currency": "EUR", "eur_amount": "7600"},
                "account_id": "_unassigned",
                "cost_basis_status": "COMPLETE",
                # NO sales_type field — legacy doc
                "warnings": [],
            },
        ]
        svc = _make_holdings_service(movements)
        result = svc.compute_holdings()
        aapl = next((h for h in result["holdings"] if h["security_id"] == "XNYS:AAPL"), None)
        assert aapl is not None
        assert Decimal(aapl["total_shares"]) == Decimal("60"), (
            "Legacy SELL without sales_type must behave as ACCIONES: 100 - 40 = 60"
        )

    def test_movement_without_superseded_by_is_always_active(self):
        """Legacy movements without superseded_by field are always active (not excluded)."""
        movements = [
            _make_movement("buy_no_sup", "XNYS:AAPL", "BUY", 100, "18250"),
            # No superseded_by — legacy
        ]
        svc = _make_holdings_service(movements)
        result = svc.compute_holdings()
        aapl = next((h for h in result["holdings"] if h["security_id"] == "XNYS:AAPL"), None)
        assert aapl is not None
        assert Decimal(aapl["total_shares"]) == Decimal("100"), (
            "Legacy movement without superseded_by must be fully active"
        )

    def test_cost_basis_status_complete_on_regular_buy(self):
        """Regular BUY movements have cost_basis_status=COMPLETE and contribute to basis."""
        movements = [
            # true gross = net 1825 + commission 7.50 = 1832.50; engine: cost = gross = 1832.50
            _make_movement("buy_cb", "XNYS:AAPL", "BUY", 10, "1832.5",
                           commission_eur="7.50", cost_basis_status="COMPLETE"),
        ]
        svc = _make_holdings_service(movements)
        result = svc.compute_holdings()
        aapl = next((h for h in result["holdings"] if h["security_id"] == "XNYS:AAPL"), None)
        assert aapl is not None
        assert aapl["cost_basis_status"] == "COMPLETE"
        # avg_cost = 1832.50 / 10 = 183.25
        assert Decimal(aapl["avg_cost_basis_eur"]) == Decimal("183.25")

    def test_zero_cost_acquisition_still_produces_incomplete_status(self):
        """Zero-cost BUY (INCOMPLETE) still sets cost_basis_status=INCOMPLETE."""
        movements = [
            _make_movement("buy_zero", "XNYS:AAPL", "BUY", 10, "0",
                           cost_basis_status="INCOMPLETE"),
        ]
        svc = _make_holdings_service(movements)
        result = svc.compute_holdings()
        aapl = next((h for h in result["holdings"] if h["security_id"] == "XNYS:AAPL"), None)
        assert aapl is not None
        assert aapl["cost_basis_status"] == "INCOMPLETE"
        assert aapl["avg_cost_basis_eur"] is None
