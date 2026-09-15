"""Tests for read-repair holdings computation — Symbol Unification rev 3.

Contract: Danny's §2.5 — Read-Repair on Holdings Computation.

Coverage:
- compute_holdings triggers ensure_symbol_config for security_ids with no config
- Multiple missing configs: all enrolled via read-repair
- Existing configs not re-created or modified by read-repair
- Read-repair does not change the holdings result itself
- Repair is transparent: holdings result unchanged whether repair runs or not

All tests are hermetic — in-memory fakes, no network.
"""

from __future__ import annotations

import logging
import pytest
from decimal import Decimal
from unittest.mock import patch, call
from azure.cosmos.exceptions import CosmosResourceNotFoundError

from src.portfolio.cosmos_portfolio import CosmosPortfolioService
from src.portfolio.cosmos_securities import CosmosSecuritiesService
from src.portfolio.holdings_service import HoldingsService


# ---------------------------------------------------------------------------
# Fake containers (aligned with existing conventions)
# ---------------------------------------------------------------------------

class FakePortfolioContainer:
    def __init__(self, docs=None):
        self._store: dict = {}
        for doc in (docs or []):
            self._store[doc["id"]] = dict(doc)

    def read(self):
        return {}

    def create_item(self, body):
        self._store[body["id"]] = dict(body)
        return dict(body)

    def upsert_item(self, body):
        self._store[body["id"]] = dict(body)
        return dict(body)

    def read_item(self, item, partition_key):
        if item not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[item])

    def replace_item(self, item, body):
        self._store[item] = dict(body)
        return dict(body)

    def query_items(self, query="", parameters=None, enable_cross_partition_query=True,
                    partition_key=None):
        param_map = {p["name"]: p["value"] for p in (parameters or [])}
        results = []
        for doc in self._store.values():
            if partition_key is not None and doc.get("account_id") != partition_key:
                continue
            if "doc_type = 'ledger_txn'" in query and doc.get("doc_type") != "ledger_txn":
                continue
            if "NOT IS_DEFINED(c.deleted_at)" in query and "deleted_at" in doc:
                continue
            if ("NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE'") in query:
                cs = doc.get("correction_status")
                if cs is not None and cs not in ("ACTIVE",):
                    continue
            if "@security_id" in param_map and doc.get("security_id") != param_map["@security_id"]:
                continue
            results.append(dict(doc))
        return iter(results)


class FakeSymbolsContainer:
    def __init__(self):
        self._store: dict = {}

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

    def seed_security(self, security_id: str, company_name: str = "Test Co"):
        mic, ticker = security_id.split(":", 1)
        doc = {
            "id": f"sec_{mic}_{ticker}",
            "symbol": ticker,
            "doc_type": "security_master",
            "security_id": security_id,
            "ticker": ticker,
            "company_name": company_name,
            "exchange_mic": mic,
            "status": "ACTIVE",
        }
        self._store[(ticker, doc["id"])] = doc

    def seed_config(self, ticker: str, extra: dict | None = None):
        doc = {
            "id": f"config_{ticker}",
            "symbol": ticker,
            "doc_type": "symbol_config",
            "telegram_notifications_enabled": True,  # intentionally True to detect overwrites
            "watchlist": {"covered_call": True, "cash_secured_put": True, "buy_tracker": True},
            "total_shares": 999,  # sentinel to detect overwrites
        }
        if extra:
            doc.update(extra)
        self._store[(ticker, doc["id"])] = doc
        return doc

    def has_config(self, ticker: str) -> bool:
        return (ticker, f"config_{ticker}") in self._store


def _make_buy_doc(security_id: str, quantity: str = "100", gross_eur: str = "10000",
                  doc_id: str = None) -> dict:
    ticker = security_id.split(":")[-1]
    return {
        "id": doc_id or f"txn_{ticker}_buy",
        "account_id": "_unassigned",
        "doc_type": "ledger_txn",
        "txn_type": "BUY",
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": "2026-01-01",
        "quantity": quantity,
        "gross": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur},
        "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
        "net_eur": gross_eur,
        "correction_status": "ACTIVE",
        "cost_basis_status": "COMPLETE",
    }


def _make_services(portfolio_docs=None, symbols_ctr=None):
    portfolio_ctr = FakePortfolioContainer(portfolio_docs or [])
    if symbols_ctr is None:
        symbols_ctr = FakeSymbolsContainer()
    portfolio_svc = CosmosPortfolioService(portfolio_ctr, None)
    securities_svc = CosmosSecuritiesService(symbols_ctr)
    return HoldingsService(portfolio_svc, securities_svc), portfolio_ctr, symbols_ctr


# ---------------------------------------------------------------------------
# §2.5 — Read-repair tests
# ---------------------------------------------------------------------------

class TestReadRepairMissingConfig:
    def test_compute_holdings_triggers_ensure_for_missing_config(self):
        """compute_holdings must call ensure_symbol_config for any security_id
        present in holdings but missing from configs."""
        symbols_ctr = FakeSymbolsContainer()
        symbols_ctr.seed_security("XNYS:AAPL", "Apple Inc.")
        # No config seeded for AAPL

        holdings_svc, _, _ = _make_services(
            portfolio_docs=[_make_buy_doc("XNYS:AAPL")],
            symbols_ctr=symbols_ctr,
        )

        called = []
        with patch("src.portfolio.holdings_service.ensure_symbol_config",
                   side_effect=lambda c, sid, source: called.append(sid)):
            holdings_svc.compute_holdings()

        assert "XNYS:AAPL" in called, (
            "compute_holdings must trigger read-repair ensure for missing config"
        )

    def test_read_repair_source_is_backfill(self):
        """Read-repair must use a recognisable enrollment source label.
        Implementation uses source='read_repair'."""
        symbols_ctr = FakeSymbolsContainer()
        symbols_ctr.seed_security("XNYS:AAPL", "Apple Inc.")

        holdings_svc, _, _ = _make_services(
            portfolio_docs=[_make_buy_doc("XNYS:AAPL")],
            symbols_ctr=symbols_ctr,
        )

        sources = []
        with patch("src.portfolio.holdings_service.ensure_symbol_config",
                   side_effect=lambda c, sid, source: sources.append(source)):
            holdings_svc.compute_holdings()

        # Accept any of the documented source labels from the contract
        accepted = {"backfill", "read_repair", "read_repair_holdings"}
        assert any(s in accepted for s in sources), (
            f"Read-repair source must be one of {accepted}, got: {sources}"
        )

    def test_read_repair_multiple_missing_configs(self):
        """All missing configs enrolled during a single compute_holdings call."""
        symbols_ctr = FakeSymbolsContainer()
        symbols_ctr.seed_security("XNYS:AAPL", "Apple Inc.")
        symbols_ctr.seed_security("XMAD:TEF", "Telefónica")

        holdings_svc, _, _ = _make_services(
            portfolio_docs=[
                _make_buy_doc("XNYS:AAPL", doc_id="txn_aapl"),
                _make_buy_doc("XMAD:TEF", doc_id="txn_tef"),
            ],
            symbols_ctr=symbols_ctr,
        )

        called = []
        with patch("src.portfolio.holdings_service.ensure_symbol_config",
                   side_effect=lambda c, sid, source: called.append(sid)):
            holdings_svc.compute_holdings()

        assert "XNYS:AAPL" in called
        assert "XMAD:TEF" in called

    def test_read_repair_covers_option_only_tickers(self):
        """A ticker with ONLY option movements never enters `per_security` /
        the holdings response (rev 6), but it must still be enrolled in
        symbol_config via read-repair — otherwise it would silently drop out
        of the Symbols Overview entirely instead of appearing in Watchlist.
        """
        symbols_ctr = FakeSymbolsContainer()
        symbols_ctr.seed_security("XNAS:NKE", "Nike Inc.")
        option_doc = {
            "id": "txn_nke_put_sell",
            "account_id": "ibkr",
            "doc_type": "ledger_txn",
            "txn_type": "PUT_SELL",
            "security_id": "XNAS:NKE",
            "ticker": "NKE",
            "trade_date": "2026-01-01",
            "quantity": "0",
            "gross": {"amount": "150.00", "currency": "EUR", "eur_amount": "150.00"},
            "fees": {"total": "5.00", "currency": "EUR", "total_eur": "5.00"},
            "net_eur": "145.00",
            "correction_status": "ACTIVE",
            "cost_basis_status": "COMPLETE",
        }

        holdings_svc, _, _ = _make_services(
            portfolio_docs=[option_doc],
            symbols_ctr=symbols_ctr,
        )

        called = []
        with patch("src.portfolio.holdings_service.ensure_symbol_config",
                   side_effect=lambda c, sid, source: called.append(sid)):
            result = holdings_svc.compute_holdings()

        assert result["holdings"] == []
        assert "XNAS:NKE" in called, (
            "option-only ticker must still be enrolled via read-repair even "
            "though it never enters the holdings response"
        )

    def test_read_repair_skips_existing_configs(self):
        """Contract §2.5: ensure_symbol_config must only be triggered for securities
        missing a config.  The implementation pre-reads config_{ticker} and skips
        the ensure call when the config already exists — this test verifies that
        pre-check is honoured."""
        symbols_ctr = FakeSymbolsContainer()
        symbols_ctr.seed_security("XNYS:AAPL", "Apple Inc.")
        symbols_ctr.seed_config("AAPL")  # Config already exists

        holdings_svc, _, _ = _make_services(
            portfolio_docs=[_make_buy_doc("XNYS:AAPL")],
            symbols_ctr=symbols_ctr,
        )

        called = []
        with patch("src.portfolio.holdings_service.ensure_symbol_config",
                   side_effect=lambda c, sid, source: called.append(sid)):
            holdings_svc.compute_holdings()

        assert "XNYS:AAPL" not in called, (
            "ensure_symbol_config must NOT be called for XNYS:AAPL when config "
            "already exists — implementation should pre-read and skip."
        )

    def test_read_repair_does_not_modify_existing_config(self):
        """Read-repair must never overwrite an existing config's fields."""
        symbols_ctr = FakeSymbolsContainer()
        symbols_ctr.seed_security("XNYS:AAPL", "Apple Inc.")
        original_config = symbols_ctr.seed_config("AAPL")  # sentinel values inside

        holdings_svc, _, _ = _make_services(
            portfolio_docs=[_make_buy_doc("XNYS:AAPL")],
            symbols_ctr=symbols_ctr,
        )
        # No patching: ensure is NOT called for AAPL since config exists
        holdings_svc.compute_holdings()

        # Config must remain exactly as seeded
        persisted = symbols_ctr._store.get(("AAPL", "config_AAPL"))
        assert persisted is not None
        assert persisted["telegram_notifications_enabled"] == original_config["telegram_notifications_enabled"]
        assert persisted["total_shares"] == original_config["total_shares"]

    def test_holdings_result_unchanged_by_read_repair(self):
        """Holdings computation result is the same whether or not read-repair runs."""
        symbols_ctr = FakeSymbolsContainer()
        symbols_ctr.seed_security("XNYS:AAPL", "Apple Inc.")

        buy_doc = _make_buy_doc("XNYS:AAPL", quantity="100", gross_eur="10000")

        # Run with repair active
        holdings_svc, _, _ = _make_services(
            portfolio_docs=[buy_doc],
            symbols_ctr=symbols_ctr,
        )
        with patch("src.portfolio.holdings_service.ensure_symbol_config",
                   side_effect=lambda c, sid, source: None):
            result_with_repair = holdings_svc.compute_holdings()

        # Run without repair (mock raises, but result is still computed)
        holdings_svc2, _, _ = _make_services(
            portfolio_docs=[buy_doc],
            symbols_ctr=symbols_ctr,
        )
        with patch("src.portfolio.holdings_service.ensure_symbol_config",
                   side_effect=Exception("skipped")):
            result_without_repair = holdings_svc2.compute_holdings()

        # Compare holding shapes (shares must match)
        h_with = {h["ticker"]: h["total_shares"] for h in result_with_repair["holdings"]}
        h_without = {h["ticker"]: h["total_shares"] for h in result_without_repair["holdings"]}
        assert h_with == h_without, (
            "Read-repair must not alter holdings computation result"
        )
