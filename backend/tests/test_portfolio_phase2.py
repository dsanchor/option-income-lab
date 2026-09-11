"""Portfolio Phase 2 backend tests.

Covers:
- Account CRUD (create, list, get, delete with guard)
- Manual movement creation (BUY/SELL/DIVIDEND) with validation
- Movement correction workflow (replacement + SUPERSEDED chain)
- Paired transfer creation (TRANSFER_OUT + TRANSFER_IN)
- Holdings computation with TRANSFER and SUPERSEDED exclusion
- Movement reassignment (individual and batch)
- FX service (EUR passthrough, mock for non-EUR)

All tests are hermetic — fake Cosmos containers, no network.
"""

from __future__ import annotations

import pytest
from decimal import Decimal
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError, CosmosHttpResponseError

from src.portfolio.cosmos_portfolio import (
    CosmosPortfolioService,
    StorageUnavailableError,
    InsufficientSharesError,
)
from src.portfolio.cosmos_securities import CosmosSecuritiesService
from src.portfolio.holdings_service import HoldingsService
from src.portfolio.fx_service import get_fx_rate, FxRateNotFoundError, FxUnavailableError


# ---------------------------------------------------------------------------
# Fake containers
# ---------------------------------------------------------------------------

class FakePortfolioContainer:
    def __init__(self, initial=None):
        self._store: dict = {}
        for doc in (initial or []):
            self._store[doc["id"]] = dict(doc)

    def read(self):
        return {}

    def create_item(self, body):
        if body["id"] in self._store:
            raise CosmosHttpResponseError(status_code=409, message="Conflict")
        self._store[body["id"]] = dict(body)
        return dict(body)

    def upsert_item(self, body):
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
        if item not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        self._store[item] = dict(body)
        return dict(body)

    def query_items(self, query="", parameters=None, enable_cross_partition_query=True,
                    partition_key=None):
        param_map = {p["name"]: p["value"] for p in (parameters or [])}
        results = []

        for doc in self._store.values():
            # Partition filter
            if partition_key is not None and doc.get("account_id") != partition_key:
                continue

            # doc_type filter
            if "doc_type = 'ledger_txn'" in query and doc.get("doc_type") != "ledger_txn":
                continue
            if "doc_type = 'account'" in query and doc.get("doc_type") != "account":
                continue

            # deleted_at filter
            if "NOT IS_DEFINED(c.deleted_at)" in query and "deleted_at" in doc:
                continue

            # correction_status filter
            if ("NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE'") in query:
                cs = doc.get("correction_status")
                if cs is not None and cs not in ("ACTIVE",):
                    continue

            # account_id filter
            if "@account_id" in param_map:
                if doc.get("account_id") != param_map["@account_id"]:
                    continue

            # security_id filter
            if "@security_id" in param_map:
                if doc.get("security_id") != param_map["@security_id"]:
                    continue
            if "@txn_type" in param_map:
                if doc.get("txn_type") != param_map["@txn_type"]:
                    continue
            if "@option_position_id" in param_map:
                if doc.get("option_position_id") != param_map["@option_position_id"]:
                    continue

            # date filters
            if "@date_from" in param_map:
                if doc.get("trade_date", "") < param_map["@date_from"]:
                    continue
            if "@date_to" in param_map:
                if doc.get("trade_date", "") > param_map["@date_to"]:
                    continue
            if "@as_of_date" in param_map:
                if doc.get("trade_date", "") > param_map["@as_of_date"]:
                    continue

            if "c.is_paper = true" in query and not bool(doc.get("is_paper")):
                continue
            if "(NOT IS_DEFINED(c.is_paper) OR c.is_paper = false)" in query and bool(doc.get("is_paper")):
                continue

            results.append(dict(doc))

        if "COUNT" in query:
            return iter([len([d for d in results if d.get("doc_type") == "ledger_txn"])])
        return iter(results)


class FakeSymbolsContainer:
    def __init__(self):
        self._store = {}

    def read_item(self, item, partition_key):
        key = (partition_key, item)
        if key not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[key])

    def create_item(self, body):
        ticker = body["symbol"]
        key = (ticker, body["id"])
        self._store[key] = dict(body)
        return dict(body)

    def query_items(self, query="", parameters=None, enable_cross_partition_query=False,
                    partition_key=None):
        return iter([v for v in self._store.values() if v.get("doc_type") == "security_master"])

    def replace_item(self, item, body):
        for key in self._store:
            if self._store[key].get("id") == item:
                self._store[key] = dict(body)
                return dict(body)
        raise CosmosResourceNotFoundError(message="not found", response=None)

    def upsert_item(self, body):
        ticker = body.get("symbol", "")
        key = (ticker, body["id"])
        self._store[key] = dict(body)
        return dict(body)


def _make_svc(docs=None):
    symbols_container = FakeSymbolsContainer()
    portfolio_svc = CosmosPortfolioService(FakePortfolioContainer(docs), None, symbols_container)
    securities_svc = CosmosSecuritiesService(symbols_container)
    return portfolio_svc, securities_svc


def _seed_symbol_position(
    symbols_container: FakeSymbolsContainer,
    *,
    symbol: str,
    position_id: str,
    position_type: str = "call",
    is_paper: bool = False,
):
    position = {
        "position_id": position_id,
        "type": position_type,
        "strike": 210.0,
        "expiration": "2026-01-16",
        "opened_at": "2026-01-01T00:00:00Z",
        "status": "active",
        "notes": "",
    }
    if is_paper:
        position["is_paper"] = True
    symbols_container._store[(symbol, f"config_{symbol}")] = {
        "id": f"config_{symbol}",
        "symbol": symbol,
        "doc_type": "symbol_config",
        "positions": [position],
    }


# ---------------------------------------------------------------------------
# Account CRUD
# ---------------------------------------------------------------------------

class TestAccountCRUD:
    def test_create_account(self):
        svc, _ = _make_svc()
        doc = svc.create_account(broker="fidelity", name="My Fidelity", currency="USD")
        assert doc["doc_type"] == "account"
        assert doc["broker"] == "fidelity"
        assert doc["name"] == "My Fidelity"
        assert doc["currency"] == "USD"
        assert doc["id"].startswith("acct_fidelity_")

    def test_account_id_is_stable_slug(self):
        svc, _ = _make_svc()
        doc = svc.create_account(broker="interactive_brokers", name="Main Account")
        assert doc["id"] == "acct_interactive_brokers_main_account"

    def test_create_duplicate_raises(self):
        svc, _ = _make_svc()
        svc.create_account(broker="fidelity", name="Main")
        with pytest.raises(ValueError, match="already exists"):
            svc.create_account(broker="fidelity", name="Main")

    def test_list_accounts(self):
        svc, _ = _make_svc()
        svc.create_account(broker="fidelity", name="A")
        svc.create_account(broker="heytrade", name="B")
        accounts = svc.list_accounts()
        assert len(accounts) == 2

    def test_get_account(self):
        svc, _ = _make_svc()
        created = svc.create_account(broker="ing", name="ING Direct")
        doc = svc.get_account(created["id"])
        assert doc is not None
        assert doc["broker"] == "ing"

    def test_get_nonexistent_account_returns_none(self):
        svc, _ = _make_svc()
        assert svc.get_account("acct_nonexistent") is None

    def test_delete_account_without_movements(self):
        svc, _ = _make_svc()
        created = svc.create_account(broker="other", name="Test")
        doc = svc.delete_account(created["id"])
        assert doc is not None
        assert "deleted_at" in doc

    def test_delete_account_with_movements_raises(self):
        svc, _ = _make_svc()
        acct = svc.create_account(broker="fidelity", name="Blocked")
        acct_id = acct["id"]
        # Add active movement in same account partition
        svc.portfolio_container._store["mvt_test"] = {
            "id": "mvt_test",
            "account_id": acct_id,
            "doc_type": "ledger_txn",
            "correction_status": "ACTIVE",
        }
        with pytest.raises(ValueError, match="account_has_movements"):
            svc.delete_account(acct_id)

    def test_delete_nonexistent_account_returns_none(self):
        svc, _ = _make_svc()
        assert svc.delete_account("acct_nope") is None


# ---------------------------------------------------------------------------
# Manual Movement Creation
# ---------------------------------------------------------------------------

def _buy_body(**kwargs):
    base = {
        "txn_type": "BUY",
        "security_id": "XNYS:AAPL",
        "trade_date": "2026-01-15",
        "account_id": "_unassigned",
        "quantity": "100",
        "gross": {"amount": "18250.00", "currency": "EUR", "eur_amount": "18250.00"},
        "fees": {"total": "7.50", "currency": "EUR", "total_eur": "7.50"},
    }
    base.update(kwargs)
    return base


def _option_body(**kwargs):
    base = {
        "txn_type": "CALL_SELL",
        "security_id": "XNYS:AAPL",
        "trade_date": "2026-01-15",
        "account_id": "_unassigned",
        "quantity": "0",
        "gross": {"amount": "150.00", "currency": "USD", "eur_amount": "138.00"},
        "fees": {"total": "3.50", "currency": "USD", "total_eur": "3.25"},
        "option_position_id": "pos_call_001",
        "option_link_kind": "OPEN_SELL",
        "option_type": "call",
        "option_strike": 210.0,
        "option_expiration": "2026-01-16",
        "option_symbol": "AAPL",
    }
    base.update(kwargs)
    return base


class TestManualMovementCreation:
    def test_buy_creates_doc(self):
        svc, _ = _make_svc()
        doc = svc.create_manual_movement(_buy_body())
        assert doc["doc_type"] == "ledger_txn"
        assert doc["txn_type"] == "BUY"
        assert doc["id"].startswith("mvt_")
        assert doc["import_source"] == "manual"
        assert doc["correction_status"] == "ACTIVE"

    def test_net_computed_from_gross_minus_fees(self):
        svc, _ = _make_svc()
        doc = svc.create_manual_movement(_buy_body())
        net = Decimal(doc["net"]["eur_amount"])
        assert net == Decimal("18257.50")  # gross + fees = 18250 + 7.50

    def test_sell_defaults_to_acciones(self):
        svc, _ = _make_svc()
        doc = svc.create_manual_movement({
            "txn_type": "SELL",
            "security_id": "XNYS:AAPL",
            "trade_date": "2026-01-20",
            "quantity": "50",
            "gross": {"amount": "9000.00", "currency": "EUR", "eur_amount": "9000.00"},
        })
        assert doc["sales_type"] == "ACCIONES"

    def test_sell_derechos_accepted(self):
        svc, _ = _make_svc()
        doc = svc.create_manual_movement({
            "txn_type": "SELL",
            "security_id": "XMAD:TEF",
            "trade_date": "2026-02-01",
            "quantity": "10",
            "gross": {"amount": "5.00", "currency": "EUR", "eur_amount": "5.00"},
            "sales_type": "DERECHOS",
        })
        assert doc["sales_type"] == "DERECHOS"

    def test_invalid_txn_type_raises(self):
        svc, _ = _make_svc()
        with pytest.raises(ValueError, match="txn_type"):
            svc.create_manual_movement({
                "txn_type": "TRANSFER_OUT",
                "security_id": "XNYS:AAPL",
                "trade_date": "2026-01-15",
                "quantity": "10",
                "gross": {"amount": "100", "currency": "EUR", "eur_amount": "100"},
            })

    def test_invalid_sales_type_raises(self):
        svc, _ = _make_svc()
        with pytest.raises(ValueError, match="sales_type"):
            svc.create_manual_movement({
                "txn_type": "SELL",
                "security_id": "XNYS:AAPL",
                "trade_date": "2026-01-15",
                "quantity": "10",
                "gross": {"amount": "100", "currency": "EUR", "eur_amount": "100"},
                "sales_type": "BONOS",
            })

    def test_buy_sets_cost_basis_complete(self):
        svc, _ = _make_svc()
        doc = svc.create_manual_movement(_buy_body())
        assert doc["cost_basis_status"] == "COMPLETE"

    def test_buy_incomplete_cost_basis(self):
        svc, _ = _make_svc()
        doc = svc.create_manual_movement(_buy_body(cost_basis_status="INCOMPLETE"))
        assert doc["cost_basis_status"] == "INCOMPLETE"

    def test_withholding_deducted_from_net(self):
        svc, _ = _make_svc()
        doc = svc.create_manual_movement({
            "txn_type": "DIVIDEND",
            "security_id": "XNYS:AAPL",
            "trade_date": "2026-03-01",
            "quantity": "0",
            "gross": {"amount": "100.00", "currency": "EUR", "eur_amount": "100.00"},
            "withholding": {
                "source": {"amount_eur": "15.00"},
                "destination": {"amount_eur": "5.00"},
            },
        })
        net = Decimal(doc["net"]["eur_amount"])
        # 100 - 0 (fees) - 15 - 5 = 80
        assert net == Decimal("80.000000")

    def test_missing_withholding_defaults_to_null_shape(self):
        svc, _ = _make_svc()
        doc = svc.create_manual_movement(_buy_body())
        assert doc["withholding"] == {"source": None, "destination": None}

    def test_option_sell_allows_missing_option_position_id(self):
        svc, _ = _make_svc()
        doc = svc.create_manual_movement(_option_body(option_position_id=None))
        assert "option_position_id" not in doc or doc["option_position_id"] is None

    def test_option_sell_uses_sell_like_net(self):
        svc, _ = _make_svc()
        doc = svc.create_manual_movement(_option_body())
        assert doc["quantity"] == "0"
        assert Decimal(doc["net"]["amount"]) == Decimal("146.500000")
        assert Decimal(doc["net"]["eur_amount"]) == Decimal("134.750000")

    def test_manual_option_movement_persists_is_paper(self):
        svc, _ = _make_svc()
        doc = svc.create_manual_movement(_option_body(is_paper=True))
        assert doc["is_paper"] is True

    def test_manual_option_movement_rejects_paper_real_position_mismatch(self):
        svc, _ = _make_svc()
        _seed_symbol_position(
            svc.symbols_container,
            symbol="AAPL",
            position_id="pos_call_001",
            is_paper=False,
        )
        with pytest.raises(ValueError, match="paper parity mismatch"):
            svc.create_manual_movement(_option_body(is_paper=True))


# ---------------------------------------------------------------------------
# Movement Correction
# ---------------------------------------------------------------------------

class TestMovementCorrection:
    def _setup_with_movement(self):
        svc, _ = _make_svc()
        doc = svc.create_manual_movement(_buy_body())
        return svc, doc["id"]

    def test_correction_marks_original_superseded(self):
        svc, mid = self._setup_with_movement()
        result = svc.correct_movement(
            movement_id=mid,
            account_id="_unassigned",
            correction_data={
                "account_id": "_unassigned",
                "correction_note": "Price was wrong",
                "gross": {"amount": "18000.00", "currency": "EUR", "eur_amount": "18000.00"},
            },
        )
        assert result["original"]["correction_status"] == "SUPERSEDED"
        assert result["original"]["superseded_by"] == result["replacement"]["id"]

    def test_replacement_links_to_original(self):
        svc, mid = self._setup_with_movement()
        result = svc.correct_movement(
            movement_id=mid,
            account_id="_unassigned",
            correction_data={
                "account_id": "_unassigned",
                "correction_note": "Fix",
            },
        )
        assert result["replacement"]["corrects_movement_id"] == mid
        assert result["replacement"]["correction_status"] == "ACTIVE"

    def test_correct_already_superseded_raises(self):
        svc, mid = self._setup_with_movement()
        svc.correct_movement(
            movement_id=mid,
            account_id="_unassigned",
            correction_data={"account_id": "_unassigned", "correction_note": "First fix"},
        )
        with pytest.raises(ValueError, match="already_superseded"):
            svc.correct_movement(
                movement_id=mid,
                account_id="_unassigned",
                correction_data={"account_id": "_unassigned", "correction_note": "Second fix"},
            )

    def test_correct_not_found_raises(self):
        svc, _ = _make_svc()
        with pytest.raises(LookupError):
            svc.correct_movement(
                movement_id="mvt_nonexistent",
                account_id="_unassigned",
                correction_data={"account_id": "_unassigned", "correction_note": "Fix"},
            )

    def test_empty_correction_note_raises(self):
        svc, mid = self._setup_with_movement()
        with pytest.raises(ValueError, match="correction_note"):
            svc.correct_movement(
                movement_id=mid,
                account_id="_unassigned",
                correction_data={"account_id": "_unassigned", "correction_note": ""},
            )

    def test_option_correction_recomputes_sell_like_and_buy_like_net(self):
        svc, _ = _make_svc()
        sell_doc = svc.create_manual_movement(_option_body())
        sell_result = svc.correct_movement(
            movement_id=sell_doc["id"],
            account_id="_unassigned",
            correction_data={
                "account_id": "_unassigned",
                "correction_note": "adjust premium",
                "gross": {"amount": "175.00", "currency": "USD", "eur_amount": "161.00"},
                "fees": {"total": "4.00", "currency": "USD", "total_eur": "3.75"},
            },
        )
        assert Decimal(sell_result["replacement"]["net"]["amount"]) == Decimal("171.000000")
        assert Decimal(sell_result["replacement"]["net"]["eur_amount"]) == Decimal("157.250000")

        buy_doc = svc.create_manual_movement(_option_body(
            txn_type="PUT_BUY",
            option_position_id="pos_put_001",
            option_link_kind="CLOSE_BUY",
            option_type="put",
            gross={"amount": "80.00", "currency": "USD", "eur_amount": "74.00"},
            fees={"total": "2.00", "currency": "USD", "total_eur": "1.85"},
        ))
        buy_result = svc.correct_movement(
            movement_id=buy_doc["id"],
            account_id="_unassigned",
            correction_data={
                "account_id": "_unassigned",
                "correction_note": "adjust buyback",
                "gross": {"amount": "90.00", "currency": "USD", "eur_amount": "83.00"},
                "fees": {"total": "2.50", "currency": "USD", "total_eur": "2.25"},
            },
        )
        assert Decimal(buy_result["replacement"]["net"]["amount"]) == Decimal("92.500000")
        assert Decimal(buy_result["replacement"]["net"]["eur_amount"]) == Decimal("85.250000")

    def test_option_correction_can_add_option_position_id_after_creation(self):
        svc, _ = _make_svc()
        unlinked = svc.create_manual_movement(_option_body(option_position_id=None))
        result = svc.correct_movement(
            movement_id=unlinked["id"],
            account_id="_unassigned",
            correction_data={
                "account_id": "_unassigned",
                "correction_note": "link movement to option position",
                "option_position_id": "pos_call_002",
            },
        )
        assert result["replacement"]["option_position_id"] == "pos_call_002"

    def test_option_correction_rejects_real_movement_link_to_paper_position(self):
        svc, _ = _make_svc()
        linked = svc.create_manual_movement(_option_body(option_position_id=None))
        _seed_symbol_position(
            svc.symbols_container,
            symbol="AAPL",
            position_id="pos_call_paper",
            is_paper=True,
        )
        with pytest.raises(ValueError, match="paper parity mismatch"):
            svc.correct_movement(
                movement_id=linked["id"],
                account_id="_unassigned",
                correction_data={
                    "account_id": "_unassigned",
                    "correction_note": "attempt mismatched relink",
                    "option_position_id": "pos_call_paper",
                },
            )

    def test_option_correction_can_add_option_close_date_after_creation(self):
        svc, _ = _make_svc()
        doc = svc.create_manual_movement(_option_body(option_close_date=None))
        assert doc.get("option_close_date") is None
        result = svc.correct_movement(
            movement_id=doc["id"],
            account_id="_unassigned",
            correction_data={
                "account_id": "_unassigned",
                "correction_note": "record eventual close date",
                "option_close_date": "2026-01-22",
            },
        )
        assert result["replacement"]["option_close_date"] == "2026-01-22"


# ---------------------------------------------------------------------------
# Movements list / duplicate detection
# ---------------------------------------------------------------------------

class TestMovementsListAndDuplicates:
    def test_get_movements_filters_by_option_position_id(self):
        svc, _ = _make_svc([
            _make_txn(
                "m_linked_1",
                "XNYS:AAPL",
                "CALL_SELL",
                "0",
                "100",
                "acct_a",
                option_position_id="pos_a",
            ),
            _make_txn(
                "m_linked_2",
                "XNYS:AAPL",
                "CALL_BUY",
                "0",
                "50",
                "acct_a",
                option_position_id="pos_a",
            ),
            _make_txn(
                "m_other",
                "XNYS:AAPL",
                "CALL_SELL",
                "0",
                "75",
                "acct_a",
                option_position_id="pos_b",
            ),
        ])

        movements, total = svc.get_movements(option_position_id="pos_a", limit=10, offset=0)
        assert total == 2
        assert {movement["id"] for movement in movements} == {"m_linked_1", "m_linked_2"}

    def test_find_probable_duplicate_separates_real_and_paper_lanes(self):
        svc, _ = _make_svc([
            _make_txn(
                "m_real",
                "XNYS:AAPL",
                "CALL_SELL",
                "0",
                "100",
                "acct_a",
                trade_date="2026-01-15",
                option_position_id="pos_real",
            ),
            _make_txn(
                "m_paper",
                "XNYS:AAPL",
                "CALL_SELL",
                "0",
                "100",
                "acct_a",
                trade_date="2026-01-15",
                option_position_id="pos_paper",
                is_paper=True,
            ),
        ])

        assert svc.find_probable_duplicate("XNYS:AAPL", "CALL_SELL", "2026-01-15", "0", "100", is_paper=False)["id"] == "m_real"
        assert svc.find_probable_duplicate("XNYS:AAPL", "CALL_SELL", "2026-01-15", "0", "100", is_paper=True)["id"] == "m_paper"


# ---------------------------------------------------------------------------
# Holdings — TRANSFER semantics
# ---------------------------------------------------------------------------

def _make_txn(id_, security_id, txn_type, qty, gross_eur, account_id, **extra):
    ticker = security_id.split(":")[-1]
    doc = {
        "id": id_,
        "doc_type": "ledger_txn",
        "txn_type": txn_type,
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": extra.get("trade_date", "2026-01-15"),
        "quantity": str(qty),
        "account_id": account_id,
        "gross": {"amount": str(gross_eur), "currency": "EUR", "eur_amount": str(gross_eur)},
        "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
        "net": {"amount": str(gross_eur), "currency": "EUR", "eur_amount": str(gross_eur)},
        "correction_status": "ACTIVE",
        "warnings": [],
    }
    doc.update(extra)
    return doc


class TestHoldingsWithTransfers:
    def test_transfer_out_subtracts_from_source(self):
        docs = [
            _make_txn("t1", "XNYS:AAPL", "BUY", "100", "10000", "acct_a"),
            _make_txn("t2", "XNYS:AAPL", "TRANSFER_OUT", "30", "0", "acct_a",
                      transfer_cost_basis_eur="3000"),
        ]
        svc, securities_svc = _make_svc(docs)
        holdings = HoldingsService(svc, securities_svc)
        result = holdings.compute_holdings(account_id="acct_a")
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("70")

    def test_transfer_in_adds_to_dest(self):
        docs = [
            _make_txn("t1", "XNYS:AAPL", "TRANSFER_IN", "30", "0", "acct_b",
                      transfer_cost_basis_eur="3000"),
        ]
        svc, securities_svc = _make_svc(docs)
        holdings = HoldingsService(svc, securities_svc)
        result = holdings.compute_holdings(account_id="acct_b")
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("30")

    def test_global_transfer_nets_to_zero(self):
        docs = [
            _make_txn("t1", "XNYS:AAPL", "BUY", "100", "10000", "acct_a"),
            _make_txn("t2", "XNYS:AAPL", "TRANSFER_OUT", "30", "0", "acct_a",
                      transfer_cost_basis_eur="3000"),
            _make_txn("t3", "XNYS:AAPL", "TRANSFER_IN", "30", "0", "acct_b",
                      transfer_cost_basis_eur="3000"),
        ]
        svc, securities_svc = _make_svc(docs)
        holdings = HoldingsService(svc, securities_svc)
        result = holdings.compute_holdings()
        h = result["holdings"][0]
        # Global: BUY 100, OUT 30, IN 30 → net 100
        assert Decimal(h["total_shares"]) == Decimal("100")

    def test_superseded_movement_excluded_from_holdings(self):
        docs = [
            _make_txn("t1", "XNYS:AAPL", "BUY", "100", "10000", "_unassigned"),
            _make_txn("t2", "XNYS:AAPL", "BUY", "50", "5000", "_unassigned",
                      correction_status="SUPERSEDED"),
        ]
        svc, securities_svc = _make_svc(docs)
        holdings = HoldingsService(svc, securities_svc)
        result = holdings.compute_holdings()
        h = result["holdings"][0]
        # Only the ACTIVE BUY counts
        assert Decimal(h["total_shares"]) == Decimal("100")

    def test_voided_movement_excluded_from_holdings(self):
        docs = [
            _make_txn("t1", "XNYS:AAPL", "BUY", "100", "10000", "_unassigned"),
            _make_txn("t2", "XNYS:AAPL", "BUY", "50", "5000", "_unassigned",
                      correction_status="VOIDED"),
        ]
        svc, securities_svc = _make_svc(docs)
        holdings = HoldingsService(svc, securities_svc)
        result = holdings.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("100")


# ---------------------------------------------------------------------------
# Transfer pair creation
# ---------------------------------------------------------------------------

class TestTransferPairCreation:
    def _setup_with_shares(self, shares="100"):
        svc, _ = _make_svc()
        # Manually add a BUY
        svc.portfolio_container._store["t1"] = _make_txn(
            "t1", "XNYS:AAPL", "BUY", shares, "10000", "acct_a",
            trade_date="2026-01-10"
        )
        return svc

    def test_transfer_pair_created(self):
        svc = self._setup_with_shares("100")
        result = svc.create_transfer_pair(
            security_id="XNYS:AAPL",
            trade_date="2026-01-15",
            quantity="30",
            source_account_id="acct_a",
            dest_account_id="acct_b",
        )
        assert "transfer_out" in result
        assert "transfer_in" in result
        assert result["transfer_out"]["txn_type"] == "TRANSFER_OUT"
        assert result["transfer_in"]["txn_type"] == "TRANSFER_IN"
        assert result["transfer_out"]["transfer_group_id"] == result["transfer_in"]["transfer_group_id"]

    def test_insufficient_shares_raises(self):
        svc = self._setup_with_shares("20")
        with pytest.raises(InsufficientSharesError) as exc_info:
            svc.create_transfer_pair(
                security_id="XNYS:AAPL",
                trade_date="2026-01-15",
                quantity="30",
                source_account_id="acct_a",
                dest_account_id="acct_b",
            )
        assert Decimal(exc_info.value.available) < Decimal("30")

    def test_same_account_raises(self):
        svc = self._setup_with_shares("100")
        with pytest.raises(ValueError, match="must differ"):
            svc.create_transfer_pair(
                security_id="XNYS:AAPL",
                trade_date="2026-01-15",
                quantity="10",
                source_account_id="acct_a",
                dest_account_id="acct_a",
            )

    def test_transfer_cost_basis_derived(self):
        svc = self._setup_with_shares("100")
        result = svc.create_transfer_pair(
            security_id="XNYS:AAPL",
            trade_date="2026-01-15",
            quantity="50",
            source_account_id="acct_a",
            dest_account_id="acct_b",
        )
        # avg cost = 10000/100 = 100/share; 50 shares → 5000
        out = result["transfer_out"]
        assert Decimal(out["transfer_cost_basis_eur"]) == Decimal("5000")
        assert out["transfer_cost_basis_overridden"] is False

    def test_transfer_cost_basis_override(self):
        svc = self._setup_with_shares("100")
        result = svc.create_transfer_pair(
            security_id="XNYS:AAPL",
            trade_date="2026-01-15",
            quantity="50",
            source_account_id="acct_a",
            dest_account_id="acct_b",
            cost_basis_override_eur="4500",
        )
        out = result["transfer_out"]
        assert Decimal(out["transfer_cost_basis_derived_eur"]) == Decimal("5000")
        assert Decimal(out["transfer_cost_basis_eur"]) == Decimal("4500")
        assert out["transfer_cost_basis_overridden"] is True

    def test_transfer_peer_ids_linked(self):
        svc = self._setup_with_shares("100")
        result = svc.create_transfer_pair(
            security_id="XNYS:AAPL",
            trade_date="2026-01-15",
            quantity="10",
            source_account_id="acct_a",
            dest_account_id="acct_b",
        )
        out = result["transfer_out"]
        in_ = result["transfer_in"]
        assert out["transfer_peer_id"] == in_["id"]
        assert in_["transfer_peer_id"] == out["id"]

    def test_transfer_docs_include_zero_financial_and_null_withholding_fields(self):
        svc = self._setup_with_shares("100")
        result = svc.create_transfer_pair(
            security_id="XNYS:AAPL",
            trade_date="2026-01-15",
            quantity="10",
            source_account_id="acct_a",
            dest_account_id="acct_b",
        )
        expected_gross_net = {"amount": "0", "currency": "EUR", "eur_amount": "0"}
        expected_fees = {"total": "0", "currency": "EUR", "total_eur": "0"}
        expected_withholding = {"source": None, "destination": None}

        for leg in (result["transfer_out"], result["transfer_in"]):
            assert leg["gross"] == expected_gross_net
            assert leg["fees"] == expected_fees
            assert leg["net"] == expected_gross_net
            assert leg["withholding"] == expected_withholding


# ---------------------------------------------------------------------------
# Movement Reassignment
# ---------------------------------------------------------------------------

class TestMovementReassignment:
    def _setup_with_movement(self):
        svc, _ = _make_svc()
        doc = svc.create_manual_movement(_buy_body(account_id="_unassigned"))
        return svc, doc["id"]

    def test_reassign_single(self):
        svc, mid = self._setup_with_movement()
        result = svc.reassign_movement(
            movement_id=mid,
            source_account_id="_unassigned",
            dest_account_id="acct_b",
            reason="Imported to wrong account",
        )
        assert result["original_id"] == mid
        assert result["new_id"] != mid
        assert result["dest_account_id"] == "acct_b"

        # Original should be SUPERSEDED
        orig = svc.portfolio_container._store[mid]
        assert orig["correction_status"] == "SUPERSEDED"

        # New doc in dest partition
        new_doc = svc.portfolio_container._store[result["new_id"]]
        assert new_doc["account_id"] == "acct_b"
        assert new_doc["reassigned_from"]["account_id"] == "_unassigned"
        assert new_doc["reassigned_from"]["movement_id"] == mid

    def test_reassign_same_account_raises(self):
        svc, mid = self._setup_with_movement()
        with pytest.raises(ValueError, match="same_account"):
            svc.reassign_movement(mid, "_unassigned", "_unassigned")

    def test_reassign_not_found_raises(self):
        svc, _ = _make_svc()
        with pytest.raises(LookupError):
            svc.reassign_movement("mvt_none", "_unassigned", "acct_b")

    def test_reassign_already_superseded_raises(self):
        svc, mid = self._setup_with_movement()
        svc.reassign_movement(mid, "_unassigned", "acct_b")
        with pytest.raises(ValueError, match="already_reassigned"):
            svc.reassign_movement(mid, "_unassigned", "acct_c")

    def test_batch_reassign(self):
        svc, _ = _make_svc()
        svc.create_manual_movement(_buy_body(account_id="_unassigned", trade_date="2026-01-10"))
        svc.create_manual_movement(_buy_body(account_id="_unassigned", trade_date="2026-01-20"))
        result = svc.batch_reassign_movements(
            source_account_id="_unassigned",
            dest_account_id="acct_b",
        )
        assert result["reassigned_count"] == 2
        assert result["skipped_count"] == 0
        assert len(result["ids"]) == 2

    def test_batch_reassign_with_date_filter(self):
        svc, _ = _make_svc()
        svc.create_manual_movement(_buy_body(account_id="_unassigned", trade_date="2026-01-10"))
        svc.create_manual_movement(_buy_body(account_id="_unassigned", trade_date="2026-03-01"))
        result = svc.batch_reassign_movements(
            source_account_id="_unassigned",
            dest_account_id="acct_b",
            date_from="2026-01-01",
            date_to="2026-01-31",
        )
        assert result["reassigned_count"] == 1


# ---------------------------------------------------------------------------
# FX Service
# ---------------------------------------------------------------------------

class TestFxService:
    def test_eur_always_returns_one(self):
        rate = get_fx_rate("EUR", "EUR")
        assert rate == "1.000000000"

    def test_unsupported_to_currency_raises(self):
        with pytest.raises(ValueError, match="EUR"):
            get_fx_rate("USD", "GBP")

    def test_fx_unavailable_raises(self):
        import requests
        with patch("src.portfolio.fx_service.requests.get") as mock_get:
            mock_get.side_effect = requests.RequestException("timeout")
            # Clear cache
            import src.portfolio.fx_service as fx_mod
            fx_mod._rate_cache.clear()
            fx_mod._cache_fetched_date = None
            with pytest.raises(FxUnavailableError):
                get_fx_rate("USD", "EUR", rate_date="2026-01-15")

    def test_fx_rate_not_found_when_currency_missing(self):
        import src.portfolio.fx_service as fx_mod
        # Manually populate cache without the target currency
        fx_mod._rate_cache.clear()
        fx_mod._rate_cache[("2026-01-15", "GBP")] = "0.850000000"
        fx_mod._cache_fetched_date = "2026-01-15"
        with pytest.raises(FxRateNotFoundError):
            get_fx_rate("XYZ", "EUR", rate_date="2026-01-15")

    def test_fx_rate_from_cache(self):
        import src.portfolio.fx_service as fx_mod
        fx_mod._rate_cache.clear()
        fx_mod._rate_cache[("2026-01-15", "USD")] = "0.921500000"
        fx_mod._cache_fetched_date = "2026-01-15"
        rate = get_fx_rate("USD", "EUR", rate_date="2026-01-15")
        assert rate == "0.921500000"

    def test_fx_falls_back_to_adjacent_day(self):
        import src.portfolio.fx_service as fx_mod
        fx_mod._rate_cache.clear()
        # Only have rate for Jan 14 (weekend), not Jan 15
        fx_mod._rate_cache[("2026-01-14", "USD")] = "0.920000000"
        fx_mod._cache_fetched_date = "2026-01-15"
        rate = get_fx_rate("USD", "EUR", rate_date="2026-01-15")
        assert rate == "0.920000000"


# ---------------------------------------------------------------------------
# API Endpoint tests for Phase 2 (using FastAPI TestClient)
# ---------------------------------------------------------------------------

class FakeCosmos:
    def __init__(self):
        self.container = FakeSymbolsContainer()
        self.portfolio_container = FakePortfolioContainer()
        self.import_sessions_container = None


@pytest.fixture
def client():
    from web.app import app
    fake_cosmos = FakeCosmos()
    with TestClient(app) as c:
        app.state.cosmos = fake_cosmos
        app.state.cosmos_error = None
        yield c, fake_cosmos


class TestAccountEndpoints:
    def test_create_account_201(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/accounts", json={
            "broker": "fidelity",
            "name": "My Fidelity",
            "currency": "USD",
        })
        assert resp.status_code == 201
        doc = resp.json()
        assert doc["broker"] == "fidelity"

    def test_create_account_invalid_broker_400(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/accounts", json={
            "broker": "unknown_bank",
            "name": "Test",
        })
        assert resp.status_code == 400

    def test_list_accounts_200(self, client):
        c, _ = client
        c.post("/api/portfolio/accounts", json={"broker": "heytrade", "name": "HT"})
        resp = c.get("/api/portfolio/accounts")
        assert resp.status_code == 200
        data = resp.json()
        assert "accounts" in data
        assert len(data["accounts"]) >= 1

    def test_get_account_404(self, client):
        c, _ = client
        resp = c.get("/api/portfolio/accounts/acct_nope")
        assert resp.status_code == 404

    def test_delete_account_404(self, client):
        c, _ = client
        resp = c.delete("/api/portfolio/accounts/acct_nope")
        assert resp.status_code == 404

    def test_delete_account_with_movements_409(self, client):
        c, fake = client
        # Create account
        resp = c.post("/api/portfolio/accounts", json={"broker": "fidelity", "name": "Blocked"})
        account_id = resp.json()["id"]
        # Inject active movement in the fake container
        fake.portfolio_container._store["mvt_x"] = {
            "id": "mvt_x",
            "account_id": account_id,
            "doc_type": "ledger_txn",
            "correction_status": "ACTIVE",
        }
        resp = c.delete(f"/api/portfolio/accounts/{account_id}")
        assert resp.status_code == 409
        assert resp.json()["error"] == "account_has_movements"


class TestMovementCreationEndpoints:
    def test_create_buy_201(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/movements", json={
            "txn_type": "BUY",
            "security_id": "XNYS:AAPL",
            "trade_date": "2026-01-15",
            "quantity": "100",
            "gross": {"amount": "18250.00", "currency": "EUR", "eur_amount": "18250.00"},
        })
        assert resp.status_code == 201
        doc = resp.json()
        assert doc["txn_type"] == "BUY"

    def test_create_transfer_via_movements_400(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/movements", json={
            "txn_type": "TRANSFER_OUT",
            "security_id": "XNYS:AAPL",
            "trade_date": "2026-01-15",
            "quantity": "10",
            "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
        })
        assert resp.status_code == 400

    def test_create_sell_without_security_id_400(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/movements", json={
            "txn_type": "SELL",
            "trade_date": "2026-01-15",
            "quantity": "10",
            "gross": {"amount": "100", "currency": "EUR", "eur_amount": "100"},
        })
        assert resp.status_code == 400


class TestTransferEndpoint:
    def test_create_transfer_insufficient_shares_409(self, client):
        c, _ = client
        # No prior BUY → source has 0 shares
        resp = c.post("/api/portfolio/transfers", json={
            "security_id": "XNYS:AAPL",
            "trade_date": "2026-01-15",
            "quantity": "10",
            "source_account_id": "acct_a",
            "dest_account_id": "acct_b",
        })
        assert resp.status_code == 409
        assert resp.json()["error"] == "insufficient_shares"

    def test_create_transfer_same_account_400(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/transfers", json={
            "security_id": "XNYS:AAPL",
            "trade_date": "2026-01-15",
            "quantity": "10",
            "source_account_id": "acct_a",
            "dest_account_id": "acct_a",
        })
        assert resp.status_code == 400


class TestFxEndpoint:
    def test_eur_rate_returns_one(self, client):
        c, _ = client
        resp = c.get("/api/fx/rates?from_currency=EUR")
        assert resp.status_code == 200
        data = resp.json()
        assert data["rate"] == "1.000000000"
        assert data["from_currency"] == "EUR"

    def test_unsupported_to_currency_400(self, client):
        c, _ = client
        resp = c.get("/api/fx/rates?from_currency=USD&to_currency=GBP")
        assert resp.status_code == 400

    def test_fx_rate_from_cache(self, client):
        c, _ = client
        import src.portfolio.fx_service as fx_mod
        fx_mod._rate_cache.clear()
        fx_mod._rate_cache[("2026-01-15", "USD")] = "0.921500000"
        fx_mod._cache_fetched_date = "2026-01-15"
        resp = c.get("/api/fx/rates?from_currency=USD&date=2026-01-15")
        assert resp.status_code == 200
        data = resp.json()
        assert data["rate"] == "0.921500000"
        assert data["rate_source"] == "ECB"


# ---------------------------------------------------------------------------
# Batch-reassign preview — service-level tests
# ---------------------------------------------------------------------------

class TestPreviewBatchReassign:
    def _setup(self):
        svc, _ = _make_svc()
        svc.create_manual_movement(_buy_body(account_id="_unassigned", trade_date="2026-01-10"))
        svc.create_manual_movement(_buy_body(account_id="_unassigned", trade_date="2026-02-15"))
        svc.create_manual_movement(_buy_body(account_id="_unassigned", trade_date="2026-03-20",
                                              **{"security_id": "XMAD:TEF"}))
        return svc

    def test_preview_returns_affected_count(self):
        svc = self._setup()
        result = svc.preview_batch_reassign("_unassigned", "acct_b")
        assert result["affected_count"] == 3

    def test_preview_returns_all_movement_ids(self):
        svc = self._setup()
        result = svc.preview_batch_reassign("_unassigned", "acct_b")
        assert len(result["movement_ids"]) == 3
        # All IDs are distinct
        assert len(set(result["movement_ids"])) == 3

    def test_preview_returns_sample_fields(self):
        svc = self._setup()
        result = svc.preview_batch_reassign("_unassigned", "acct_b")
        sample = result["sample"]
        assert len(sample) >= 1
        for row in sample:
            assert "id" in row
            assert "security_id" in row
            assert "txn_type" in row
            assert "trade_date" in row
            assert "quantity" in row
            assert "account_id" in row

    def test_preview_sample_bounded_at_ten(self):
        svc, _ = _make_svc()
        for i in range(15):
            date_str = f"2026-{(i % 12) + 1:02d}-01"
            svc.create_manual_movement(
                _buy_body(account_id="_unassigned", trade_date=date_str)
            )
        result = svc.preview_batch_reassign("_unassigned", "acct_b")
        assert result["affected_count"] == 15
        assert len(result["movement_ids"]) == 15
        assert len(result["sample"]) == 10  # bounded

    def test_preview_respects_security_filter(self):
        svc = self._setup()
        result = svc.preview_batch_reassign(
            "_unassigned", "acct_b", security_id="XMAD:TEF"
        )
        assert result["affected_count"] == 1
        assert result["sample"][0]["security_id"] == "XMAD:TEF"

    def test_preview_respects_date_filter(self):
        svc = self._setup()
        result = svc.preview_batch_reassign(
            "_unassigned", "acct_b",
            date_from="2026-02-01", date_to="2026-02-28",
        )
        assert result["affected_count"] == 1

    def test_preview_excludes_superseded(self):
        svc, _ = _make_svc()
        svc.create_manual_movement(_buy_body(account_id="_unassigned", trade_date="2026-01-10"))
        # Manually inject a SUPERSEDED movement in same account
        svc.portfolio_container._store["mvt_sup"] = _make_txn(
            "mvt_sup", "XNYS:AAPL", "BUY", "50", "5000", "_unassigned",
            trade_date="2026-01-05", correction_status="SUPERSEDED",
        )
        result = svc.preview_batch_reassign("_unassigned", "acct_b")
        assert result["affected_count"] == 1  # only the ACTIVE one

    def test_preview_does_not_write(self):
        svc = self._setup()
        store_before = set(svc.portfolio_container._store.keys())
        svc.preview_batch_reassign("_unassigned", "acct_b")
        store_after = set(svc.portfolio_container._store.keys())
        assert store_before == store_after  # no docs added or removed

    def test_preview_same_account_raises(self):
        svc = self._setup()
        with pytest.raises(ValueError, match="same_account"):
            svc.preview_batch_reassign("_unassigned", "_unassigned")

    def test_preview_empty_returns_zero(self):
        svc, _ = _make_svc()
        result = svc.preview_batch_reassign("acct_empty", "acct_b")
        assert result["affected_count"] == 0
        assert result["movement_ids"] == []
        assert result["sample"] == []

    def test_preview_returns_source_and_dest(self):
        svc = self._setup()
        result = svc.preview_batch_reassign("_unassigned", "acct_b")
        assert result["source_account_id"] == "_unassigned"
        assert result["dest_account_id"] == "acct_b"

    def test_execution_rederives_set_ignoring_stale_preview(self):
        """Executing immediately after preview reassigns the current live set,
        not any count/IDs from a previous preview response."""
        svc = self._setup()
        preview = svc.preview_batch_reassign("_unassigned", "acct_b")
        assert preview["affected_count"] == 3

        # Simulate a movement being deleted between preview and execution
        ids = list(svc.portfolio_container._store.keys())
        one_txn = next(
            k for k in ids
            if svc.portfolio_container._store[k].get("doc_type") == "ledger_txn"
        )
        svc.portfolio_container._store[one_txn]["deleted_at"] = "2026-09-06T10:00:00Z"

        # Execution ignores preview result; re-derives (now 2 eligible)
        exec_result = svc.batch_reassign_movements("_unassigned", "acct_b")
        assert exec_result["reassigned_count"] == 2  # not 3


# ---------------------------------------------------------------------------
# Batch-reassign preview — endpoint tests
# ---------------------------------------------------------------------------

class TestPreviewBatchReassignEndpoint:
    def test_preview_200_with_count(self, client):
        c, fake = client
        # Inject two active movements in the source account
        for i, mid in enumerate(["mvt_p1", "mvt_p2"]):
            fake.portfolio_container._store[mid] = _make_txn(
                mid, "XNYS:AAPL", "BUY", "10", "1000", "_unassigned",
                trade_date=f"2026-0{i+1}-01",
            )
        resp = c.post("/api/portfolio/movements/batch-reassign/preview", json={
            "source_account_id": "_unassigned",
            "dest_account_id": "acct_b",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["affected_count"] == 2
        assert len(data["movement_ids"]) == 2
        assert "sample" in data
        assert data["source_account_id"] == "_unassigned"
        assert data["dest_account_id"] == "acct_b"

    def test_preview_zero_results(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/movements/batch-reassign/preview", json={
            "source_account_id": "acct_empty",
            "dest_account_id": "acct_b",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["affected_count"] == 0

    def test_preview_same_account_400(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/movements/batch-reassign/preview", json={
            "source_account_id": "acct_a",
            "dest_account_id": "acct_a",
        })
        assert resp.status_code == 400

    def test_preview_missing_source_400(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/movements/batch-reassign/preview", json={
            "dest_account_id": "acct_b",
        })
        assert resp.status_code == 400

    def test_preview_missing_dest_400(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/movements/batch-reassign/preview", json={
            "source_account_id": "_unassigned",
        })
        assert resp.status_code == 400

    def test_preview_with_security_filter(self, client):
        c, fake = client
        fake.portfolio_container._store["mvt_aapl"] = _make_txn(
            "mvt_aapl", "XNYS:AAPL", "BUY", "10", "1000", "_unassigned",
        )
        fake.portfolio_container._store["mvt_tef"] = _make_txn(
            "mvt_tef", "XMAD:TEF", "BUY", "100", "300", "_unassigned",
        )
        resp = c.post("/api/portfolio/movements/batch-reassign/preview", json={
            "source_account_id": "_unassigned",
            "dest_account_id": "acct_b",
            "security_id": "XNYS:AAPL",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["affected_count"] == 1
        assert data["movement_ids"] == ["mvt_aapl"]

    def test_preview_does_not_create_documents(self, client):
        c, fake = client
        fake.portfolio_container._store["mvt_q1"] = _make_txn(
            "mvt_q1", "XNYS:AAPL", "BUY", "10", "1000", "_unassigned",
        )
        ids_before = set(fake.portfolio_container._store.keys())
        c.post("/api/portfolio/movements/batch-reassign/preview", json={
            "source_account_id": "_unassigned",
            "dest_account_id": "acct_b",
        })
        ids_after = set(fake.portfolio_container._store.keys())
        assert ids_before == ids_after  # no documents written
