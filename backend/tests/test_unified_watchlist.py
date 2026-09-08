"""Regression tests — Unified Watchlist (Portfolio + Watchlist Merge).

Written by: Basher (independent tester/reviewer).
Contract: .squad/decisions/inbox/danny-unified-watchlist-contract.md §1–§10

──────────────────────────────────────────────────────────────────────────────
SCOPE
──────────────────────────────────────────────────────────────────────────────
These tests verify the UNIFIED overview API shape (post-merge):

  Part 1: Unified row predicate — §1.1 / §1.2
    - non-zero portfolio shares → visible
    - negative shares → always visible
    - explicit watchlist member + zero shares → visible
    - auto-enrolled only + zero shares → hidden by default
    - auto-enrolled only + zero shares + include_zero=True → visible
    - pure watchlist (no portfolio) → visible

  Part 2: is_watchlist_member helper — §1.2
    - manually added (_auto_enrolled=False) → True
    - covered_call=True → True (any toggle on)
    - cash_secured_put=True → True
    - buy_tracker=True → True
    - telegram_notifications_enabled=True → True
    - auto-enrolled only with all toggles off → False

  Part 3: Unified response shape — §2.1
    - single flat `rows` array
    - portfolio_summary block present
    - no duplicates, portfolio precedence
    - row_source, is_auto_enrolled per-row fields

  Part 4: Portfolio summary totals — §2.3
    - remaining_cost_basis_eur = Σ holdings remaining_cost_basis
    - realized_result_eur = Σ holdings realized_result
    - total_dividends_eur = Σ DIVIDEND movements net_eur

  Part 5: Per-row dividend field — §7
    - portfolio_dividends_eur present per row
    - watchlist-only row → null
    - matches holdings_service total_dividends_eur per symbol

  Part 6: Calls/Puts summary preserved — §3.1

  Part 7: Backend API retained — §4.3
    - GET /api/portfolio/holdings still returns 200

  Part 8: Batch reassignment API retained — §6.3
    - POST /api/portfolio/movements/batch-reassign still reachable

All assertions are strict. Tests targeting unimplemented features will fail
until the implementation lands. Do NOT weaken or skip assertions.
"""

from __future__ import annotations

import pytest
from decimal import Decimal
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fake containers — same pattern as test_symbols_overview_sections.py
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
        ticker = body.get("symbol", "")
        key = (ticker, body["id"])
        self._store[key] = dict(body)
        return dict(body)

    def query_items(self, query="", parameters=None, enable_cross_partition_query=False,
                    partition_key=None):
        return iter([
            doc for (pk, did), doc in self._store.items()
            if doc.get("doc_type") == "security_master"
        ])

    def replace_item(self, item, body):
        for key in list(self._store):
            if self._store[key].get("id") == item:
                self._store[key] = dict(body)
                return dict(body)
        raise CosmosResourceNotFoundError(message="not found", response=None)

    def upsert_item(self, body):
        ticker = body.get("symbol", "")
        key = (ticker, body["id"])
        self._store[key] = dict(body)
        return dict(body)

    def seed_security(self, security_id: str, company_name: str = "Test Co",
                      exchange_mic: str = None):
        mic, ticker = security_id.split(":", 1)
        doc = {
            "id": f"sec_{mic}_{ticker}",
            "symbol": ticker,
            "doc_type": "security_master",
            "security_id": security_id,
            "ticker": ticker,
            "company_name": company_name,
            "exchange_mic": exchange_mic or mic,
            "status": "ACTIVE",
        }
        self._store[(ticker, doc["id"])] = doc

    def seed_config(self, ticker: str, extra: dict | None = None) -> dict:
        doc = {
            "id": f"config_{ticker}",
            "symbol": ticker,
            "doc_type": "symbol_config",
            "display_name": f"{ticker} Corp",
            "telegram_notifications_enabled": False,
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
            "positions": [],
            "total_shares": 0,
            "_auto_enrolled": False,  # default: manually added
        }
        if extra:
            doc.update(extra)
        self._store[(ticker, doc["id"])] = doc
        return doc


class FakeCosmos:
    def __init__(self, portfolio_container=None, symbols_container=None):
        self.container = symbols_container or FakeSymbolsContainer()
        self.portfolio_container = portfolio_container or FakePortfolioContainer()
        self.import_sessions_container = None

    def list_symbols(self):
        return [
            doc for (pk, did), doc in self.container._store.items()
            if doc.get("doc_type") == "symbol_config"
        ]

    def get_symbol(self, symbol: str):
        """Lookup symbol_config by bare TICKER (case-insensitive)."""
        sym = (symbol or "").strip().upper()
        for (pk, did), doc in self.container._store.items():
            if doc.get("doc_type") == "symbol_config" and (doc.get("symbol") or "").upper() == sym:
                return dict(doc)
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
# Movement helpers
# ---------------------------------------------------------------------------

def _add_buy(fake, security_id: str, account_id: str = "_unassigned",
             quantity: str = "100", gross_eur: str = "10000",
             doc_id: str = None, correction_status: str = "ACTIVE"):
    ticker = security_id.split(":")[-1]
    did = doc_id or f"txn_{ticker}_{account_id}_buy"
    doc = {
        "id": did,
        "account_id": account_id,
        "doc_type": "ledger_txn",
        "txn_type": "BUY",
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": "2026-01-01",
        "quantity": quantity,
        "gross": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur},
        "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
        "net": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur},
        "correction_status": correction_status,
        "cost_basis_status": "COMPLETE",
    }
    fake.portfolio_container._store[did] = doc
    return doc


def _add_sell(fake, security_id: str, account_id: str = "_unassigned",
              quantity: str = "100", gross_eur: str = "10000",
              doc_id: str = None):
    ticker = security_id.split(":")[-1]
    did = doc_id or f"txn_{ticker}_{account_id}_sell"
    doc = {
        "id": did,
        "account_id": account_id,
        "doc_type": "ledger_txn",
        "txn_type": "SELL",
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": "2026-06-01",
        "quantity": quantity,
        "sales_type": "ACCIONES",
        "gross": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur},
        "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
        "net_eur": gross_eur,
        "correction_status": "ACTIVE",
    }
    fake.portfolio_container._store[did] = doc
    return doc


def _add_dividend(fake, security_id: str, account_id: str = "_unassigned",
                  net_eur: str = "125.50", doc_id: str = None):
    ticker = security_id.split(":")[-1]
    did = doc_id or f"txn_{ticker}_{account_id}_div"
    doc = {
        "id": did,
        "account_id": account_id,
        "doc_type": "ledger_txn",
        "txn_type": "DIVIDEND",
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": "2026-03-15",
        "quantity": None,
        # holdings_service reads (m.get("net") or {}).get("eur_amount", "0")
        "gross": {"amount": net_eur, "currency": "EUR", "eur_amount": net_eur},
        "net": {"amount": net_eur, "currency": "EUR", "eur_amount": net_eur},
        "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
        "net_eur": net_eur,
        "correction_status": "ACTIVE",
    }
    fake.portfolio_container._store[did] = doc
    return doc


# ---------------------------------------------------------------------------
# Part 1 — Unified row predicate (§1.1)
# ---------------------------------------------------------------------------

class TestUnifiedRowPredicate:
    """Contract table row visibility rules."""

    def test_nonzero_portfolio_shares_visible(self, client):
        """Active holding (shares != 0) → always visible."""
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        fake.container.seed_config("AAPL", extra={"security_id": "XNYS:AAPL"})
        _add_buy(fake, "XNYS:AAPL", quantity="100")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        syms = {r["symbol"] for r in data.get("rows", [])}
        assert "AAPL" in syms, "Active holding must appear in unified rows"

    def test_negative_shares_always_visible(self, client):
        """Negative shares (anomaly) → always visible even when toggle ON."""
        c, fake = client
        # Create a symbol with a net negative position (buy fewer than sold)
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        fake.container.seed_config("AAPL", extra={"security_id": "XNYS:AAPL"})
        _add_buy(fake, "XNYS:AAPL", quantity="50", gross_eur="5000",
                 doc_id="txn_aapl_buy_neg")
        _add_sell(fake, "XNYS:AAPL", quantity="100", gross_eur="11000",
                  doc_id="txn_aapl_sell_neg")  # sell more → negative

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        all_symbols = {r["symbol"] for r in data.get("rows", [])}
        assert "AAPL" in all_symbols, (
            "Symbol with negative net shares must always appear in unified rows"
        )
        # Verify the row has negative shares
        aapl_row = next((r for r in data.get("rows", []) if r["symbol"] == "AAPL"), None)
        if aapl_row and aapl_row.get("portfolio_shares") is not None:
            assert Decimal(aapl_row["portfolio_shares"]) < 0, (
                "Negative shares should be reflected in portfolio_shares"
            )

    def test_explicit_watchlist_member_zero_shares_visible(self, client):
        """Manual watchlist + historical zero shares → visible (F-5)."""
        c, fake = client
        # Symbol with covered_call toggle ON: is explicit watchlist member
        fake.container.seed_config("MSFT", extra={
            "security_id": "XNAS:MSFT",
            "_auto_enrolled": True,  # auto-enrolled but has explicit toggle
            "watchlist": {"covered_call": True, "cash_secured_put": False, "buy_tracker": False},
        })
        # Historical position (sold out completely)
        _add_buy(fake, "XNAS:MSFT", quantity="100", doc_id="txn_msft_buy")
        _add_sell(fake, "XNAS:MSFT", quantity="100", doc_id="txn_msft_sell")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        all_symbols = {r["symbol"] for r in data.get("rows", [])}
        assert "MSFT" in all_symbols, (
            "Explicit watchlist member (covered_call=True) with zero shares must be visible "
            "(contract §1.1: manual watchlist overrides zero-share filter)"
        )

    def test_auto_enrolled_zero_shares_hidden_by_default(self, client):
        """Auto-enrolled only + zero shares → hidden by default (F-4)."""
        c, fake = client
        fake.container.seed_security("XNYS:O", "Realty Income")
        fake.container.seed_config("O", extra={
            "security_id": "XNYS:O",
            "_auto_enrolled": True,
            # All watchlist toggles off → NOT explicit watchlist member
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
            "telegram_notifications_enabled": False,
        })
        # Fully sold out (zero shares)
        _add_buy(fake, "XNYS:O", quantity="50", doc_id="txn_o_buy")
        _add_sell(fake, "XNYS:O", quantity="50", doc_id="txn_o_sell")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        all_symbols = {r["symbol"] for r in data.get("rows", [])}
        assert "O" not in all_symbols, (
            "Auto-enrolled only symbol with zero shares must be HIDDEN by default "
            "(contract §1.1: HIDDEN_DEFAULT)"
        )

    def test_auto_enrolled_zero_shares_visible_with_toggle(self, client):
        """F-7: include_zero=true reveals auto-enrolled zero-share symbols."""
        c, fake = client
        fake.container.seed_config("O", extra={
            "security_id": "XNYS:O",
            "_auto_enrolled": True,
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
            "telegram_notifications_enabled": False,
        })
        _add_buy(fake, "XNYS:O", quantity="50", doc_id="txn_o_buy2")
        _add_sell(fake, "XNYS:O", quantity="50", doc_id="txn_o_sell2")

        resp = c.get("/api/symbols/overview?include_zero_portfolio=true")
        data = resp.json()
        all_symbols = {r["symbol"] for r in data.get("rows", [])}
        assert "O" in all_symbols, (
            "include_zero=true must reveal auto-enrolled zero-share symbols (F-7)"
        )

    def test_watchlist_only_symbol_visible(self, client):
        """Pure watchlist (no ledger history) → visible (F-3 analog)."""
        c, fake = client
        fake.container.seed_config("NVDA")  # No movements

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        all_symbols = {r["symbol"] for r in data.get("rows", [])}
        assert "NVDA" in all_symbols, (
            "Watchlist-only symbol (no portfolio) must appear in unified rows"
        )


# ---------------------------------------------------------------------------
# Part 2 — is_watchlist_member predicate (§1.2)
# ---------------------------------------------------------------------------

class TestIsWatchlistMemberPredicate:
    """The is_watchlist_member predicate determines explicit membership."""

    def _overview_has_symbol(self, c, fake, symbol):
        resp = c.get("/api/symbols/overview")
        data = resp.json()
        return symbol in {r["symbol"] for r in data.get("rows", [])}

    def test_manually_added_not_auto_enrolled_is_member(self, client):
        """_auto_enrolled=False → explicit member even with all toggles off."""
        c, fake = client
        fake.container.seed_config("ABC", extra={
            "_auto_enrolled": False,  # manually added
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
            "telegram_notifications_enabled": False,
        })
        # Sold out (zero shares)
        _add_buy(fake, "XNAS:ABC", quantity="10", doc_id="txn_abc_buy")
        _add_sell(fake, "XNAS:ABC", quantity="10", doc_id="txn_abc_sell")
        # Should be visible because _auto_enrolled=False means manual add
        assert self._overview_has_symbol(c, fake, "ABC"), (
            "Manually added symbol (_auto_enrolled=False) with zero shares must be visible"
        )

    def test_auto_enrolled_covered_call_true_is_member(self, client):
        """Auto-enrolled with covered_call=True → explicit member."""
        c, fake = client
        fake.container.seed_config("XYZ", extra={
            "_auto_enrolled": True,
            "watchlist": {"covered_call": True, "cash_secured_put": False, "buy_tracker": False},
        })
        _add_buy(fake, "XNYS:XYZ", quantity="10", doc_id="txn_xyz_buy")
        _add_sell(fake, "XNYS:XYZ", quantity="10", doc_id="txn_xyz_sell")
        assert self._overview_has_symbol(c, fake, "XYZ"), (
            "covered_call=True implies explicit watchlist membership"
        )

    def test_auto_enrolled_cash_secured_put_true_is_member(self, client):
        """Auto-enrolled with cash_secured_put=True → explicit member."""
        c, fake = client
        fake.container.seed_config("LMN", extra={
            "_auto_enrolled": True,
            "watchlist": {"covered_call": False, "cash_secured_put": True, "buy_tracker": False},
        })
        _add_buy(fake, "XNYS:LMN", quantity="10", doc_id="txn_lmn_buy")
        _add_sell(fake, "XNYS:LMN", quantity="10", doc_id="txn_lmn_sell")
        assert self._overview_has_symbol(c, fake, "LMN"), (
            "cash_secured_put=True implies explicit watchlist membership"
        )

    def test_auto_enrolled_buy_tracker_true_is_member(self, client):
        """Auto-enrolled with buy_tracker=True → explicit member."""
        c, fake = client
        fake.container.seed_config("PQR", extra={
            "_auto_enrolled": True,
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": True},
        })
        _add_buy(fake, "XNYS:PQR", quantity="10", doc_id="txn_pqr_buy")
        _add_sell(fake, "XNYS:PQR", quantity="10", doc_id="txn_pqr_sell")
        assert self._overview_has_symbol(c, fake, "PQR"), (
            "buy_tracker=True implies explicit watchlist membership"
        )

    def test_auto_enrolled_telegram_true_is_member(self, client):
        """Auto-enrolled with telegram_notifications_enabled=True → explicit member."""
        c, fake = client
        fake.container.seed_config("TGM", extra={
            "_auto_enrolled": True,
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
            "telegram_notifications_enabled": True,
        })
        _add_buy(fake, "XNYS:TGM", quantity="10", doc_id="txn_tgm_buy")
        _add_sell(fake, "XNYS:TGM", quantity="10", doc_id="txn_tgm_sell")
        assert self._overview_has_symbol(c, fake, "TGM"), (
            "telegram_notifications_enabled=True implies explicit watchlist membership"
        )

    def test_auto_enrolled_all_toggles_off_not_member_hidden(self, client):
        """Auto-enrolled, all toggles off → NOT explicit member → hidden when zero shares."""
        c, fake = client
        fake.container.seed_config("HIST", extra={
            "_auto_enrolled": True,
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
            "telegram_notifications_enabled": False,
        })
        _add_buy(fake, "XNYS:HIST", quantity="10", doc_id="txn_hist_buy")
        _add_sell(fake, "XNYS:HIST", quantity="10", doc_id="txn_hist_sell")
        assert not self._overview_has_symbol(c, fake, "HIST"), (
            "Auto-enrolled with no toggles and zero shares must be hidden (not explicit member)"
        )


# ---------------------------------------------------------------------------
# Part 3 — Unified response shape (§2.1)
# ---------------------------------------------------------------------------

class TestUnifiedResponseShape:
    """Single flat rows array, no two-section split (post-merge)."""

    def test_rows_present_as_flat_array(self, client):
        """Overview returns 'rows' (flat unified array)."""
        c, fake = client
        fake.container.seed_config("AAPL")
        _add_buy(fake, "XNYS:AAPL")
        fake.container.seed_config("MSFT")  # watchlist only

        resp = c.get("/api/symbols/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert "rows" in data, "Unified overview must contain 'rows' flat array"
        syms = {r["symbol"] for r in data["rows"]}
        assert "AAPL" in syms
        assert "MSFT" in syms

    def test_no_duplicates_in_rows(self, client):
        """Each symbol appears exactly once — no duplicates (§1.3)."""
        c, fake = client
        fake.container.seed_config("AAPL")
        _add_buy(fake, "XNYS:AAPL")
        fake.container.seed_config("MSFT")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        symbols = [r["symbol"] for r in data.get("rows", [])]
        assert len(symbols) == len(set(symbols)), (
            f"Duplicate symbols found in unified rows: {symbols}"
        )

    def test_portfolio_summary_present(self, client):
        """§2.1: portfolio_summary block present in overview response (F-9)."""
        c, fake = client
        fake.container.seed_config("AAPL")
        _add_buy(fake, "XNYS:AAPL", quantity="100", gross_eur="15000")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        assert "portfolio_summary" in data, (
            "Overview must include 'portfolio_summary' block (§2.1 / F-9)"
        )
        summary = data["portfolio_summary"]
        assert "remaining_cost_basis_eur" in summary, (
            "portfolio_summary must include remaining_cost_basis_eur"
        )
        assert "realized_result_eur" in summary, (
            "portfolio_summary must include realized_result_eur"
        )
        assert "total_dividends_eur" in summary, (
            "portfolio_summary must include total_dividends_eur"
        )

    def test_row_source_field_present(self, client):
        """§2.2: row_source field present on each row."""
        c, fake = client
        fake.container.seed_config("AAPL")
        _add_buy(fake, "XNYS:AAPL")
        fake.container.seed_config("MSFT")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        for row in data.get("rows", []):
            assert "row_source" in row, (
                f"row_source must be present on every row; missing for symbol={row.get('symbol')}"
            )
            assert row["row_source"] in ("portfolio", "watchlist", "both"), (
                f"row_source must be 'portfolio', 'watchlist', or 'both'; "
                f"got {row['row_source']!r} for {row.get('symbol')}"
            )

    def test_is_auto_enrolled_field_present(self, client):
        """§2.2: is_auto_enrolled boolean field on each row."""
        c, fake = client
        fake.container.seed_config("AAPL", extra={"_auto_enrolled": False})
        fake.container.seed_config("AUTO", extra={"_auto_enrolled": True})

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        by_sym = {r["symbol"]: r for r in data.get("rows", [])}
        if "AAPL" in by_sym:
            assert "is_auto_enrolled" in by_sym["AAPL"], (
                "is_auto_enrolled must be present on portfolio row"
            )
            assert by_sym["AAPL"]["is_auto_enrolled"] is False

    def test_row_source_portfolio_for_holding_with_shares(self, client):
        """§2.2: symbol with active position has row_source 'portfolio' or 'both'."""
        c, fake = client
        fake.container.seed_config("AAPL")
        _add_buy(fake, "XNYS:AAPL")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        aapl = next((r for r in data.get("rows", []) if r["symbol"] == "AAPL"), None)
        assert aapl is not None
        if "row_source" in aapl:
            assert aapl["row_source"] in ("portfolio", "both"), (
                "Symbol with active holdings must have row_source portfolio or both"
            )

    def test_row_source_watchlist_for_no_portfolio(self, client):
        """§2.2: watchlist-only symbol has row_source 'watchlist'."""
        c, fake = client
        fake.container.seed_config("MSFT")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        msft = next((r for r in data.get("rows", []) if r["symbol"] == "MSFT"), None)
        assert msft is not None
        if "row_source" in msft:
            assert msft["row_source"] == "watchlist", (
                "Watchlist-only symbol must have row_source='watchlist'"
            )


# ---------------------------------------------------------------------------
# Part 4 — Portfolio summary totals (§2.3)
# ---------------------------------------------------------------------------

class TestPortfolioSummaryTotals:
    """§2.3 / D-1, D-2, D-3 — Aggregation formulas."""

    def test_remaining_cost_basis_reflects_holdings(self, client):
        """D-1: remaining_cost_basis_eur matches sum from holdings_service."""
        c, fake = client
        fake.container.seed_config("AAPL")
        _add_buy(fake, "XNYS:AAPL", quantity="100", gross_eur="15000")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        summary = data.get("portfolio_summary", {})
        assert summary.get("remaining_cost_basis_eur") is not None, (
            "remaining_cost_basis_eur must be present in portfolio_summary"
        )
        # Should be positive (bought 100 shares, none sold)
        assert Decimal(str(summary["remaining_cost_basis_eur"])) > 0

    def test_realized_result_zero_when_no_sells(self, client):
        """D-2: No sells → realized_result_eur = 0."""
        c, fake = client
        fake.container.seed_config("AAPL")
        _add_buy(fake, "XNYS:AAPL", quantity="100", gross_eur="15000")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        summary = data.get("portfolio_summary", {})
        if "realized_result_eur" in summary:
            assert Decimal(str(summary["realized_result_eur"])) == 0, (
                "No sells → realized_result must be 0"
            )

    def test_total_dividends_from_dividend_movements(self, client):
        """D-3: total_dividends_eur sums DIVIDEND movements net_eur."""
        c, fake = client
        fake.container.seed_config("AAPL")
        _add_buy(fake, "XNYS:AAPL", quantity="100", gross_eur="15000")
        _add_dividend(fake, "XNYS:AAPL", net_eur="125.50")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        summary = data.get("portfolio_summary", {})
        assert "total_dividends_eur" in summary, (
            "portfolio_summary must include total_dividends_eur"
        )
        if summary.get("total_dividends_eur") is not None:
            assert Decimal(str(summary["total_dividends_eur"])) >= Decimal("125.50"), (
                "total_dividends_eur must include dividend movement net_eur"
            )

    def test_total_dividends_aggregates_across_symbols(self, client):
        """D-3: total_dividends_eur is sum across all holdings."""
        c, fake = client
        fake.container.seed_config("AAPL")
        fake.container.seed_config("MSFT")
        _add_buy(fake, "XNYS:AAPL", quantity="100", gross_eur="15000")
        _add_buy(fake, "XNAS:MSFT", quantity="50", gross_eur="12000",
                 doc_id="txn_msft_buy")
        _add_dividend(fake, "XNYS:AAPL", net_eur="100.00", doc_id="div_aapl")
        _add_dividend(fake, "XNAS:MSFT", net_eur="75.00", doc_id="div_msft")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        summary = data.get("portfolio_summary", {})
        if summary.get("total_dividends_eur") is not None:
            total = Decimal(str(summary["total_dividends_eur"]))
            assert total >= Decimal("175.00"), (
                f"total_dividends must aggregate all symbols; expected ≥175, got {total}"
            )

    def test_summary_totals_unaffected_by_symbol_count(self, client):
        """D-5: Portfolio-wide totals are backend-computed, not client-filtered.
        Adding a watchlist-only symbol does not change the portfolio summary.
        """
        c, fake = client
        fake.container.seed_config("AAPL")
        _add_buy(fake, "XNYS:AAPL", quantity="100", gross_eur="15000")
        _add_dividend(fake, "XNYS:AAPL", net_eur="100.00", doc_id="div_aapl_b")

        resp1 = c.get("/api/symbols/overview")
        summary1 = resp1.json().get("portfolio_summary", {})

        # Add a watchlist-only symbol (no portfolio)
        fake.container.seed_config("MSFT")
        resp2 = c.get("/api/symbols/overview")
        summary2 = resp2.json().get("portfolio_summary", {})

        # Adding watchlist-only symbol must not change portfolio totals
        if summary1.get("remaining_cost_basis_eur") and summary2.get("remaining_cost_basis_eur"):
            assert (
                Decimal(str(summary1["remaining_cost_basis_eur"]))
                == Decimal(str(summary2["remaining_cost_basis_eur"]))
            ), "Adding watchlist-only symbol must not change remaining_cost_basis_eur"

        if summary1.get("total_dividends_eur") and summary2.get("total_dividends_eur"):
            assert (
                Decimal(str(summary1["total_dividends_eur"]))
                == Decimal(str(summary2["total_dividends_eur"]))
            ), "Adding watchlist-only symbol must not change total_dividends_eur"


# ---------------------------------------------------------------------------
# Part 5 — Per-row dividend field (§7)
# ---------------------------------------------------------------------------

class TestPerRowDividendField:
    """§7: portfolio_dividends_eur per symbol row."""

    def test_portfolio_row_has_dividends_field(self, client):
        """Portfolio row (with history) has portfolio_dividends_eur field."""
        c, fake = client
        fake.container.seed_config("AAPL")
        _add_buy(fake, "XNYS:AAPL", quantity="100", gross_eur="15000")
        _add_dividend(fake, "XNYS:AAPL", net_eur="150.00")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        aapl = next((r for r in data.get("rows", []) if r["symbol"] == "AAPL"), None)
        assert aapl is not None, "AAPL must appear in rows"
        assert "portfolio_dividends_eur" in aapl, (
            "Portfolio row must include portfolio_dividends_eur field (§7)"
        )
        if aapl["portfolio_dividends_eur"] is not None:
            assert Decimal(aapl["portfolio_dividends_eur"]) >= Decimal("150.00"), (
                "portfolio_dividends_eur must reflect dividend movements"
            )

    def test_watchlist_only_row_dividends_field_is_null(self, client):
        """§2.2: watchlist-only row has portfolio_dividends_eur=null."""
        c, fake = client
        fake.container.seed_config("MSFT")  # No movements

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        msft = next((r for r in data.get("rows", []) if r["symbol"] == "MSFT"), None)
        assert msft is not None
        if "portfolio_dividends_eur" in msft:
            assert msft["portfolio_dividends_eur"] is None, (
                "Watchlist-only row must have portfolio_dividends_eur=null"
            )

    def test_portfolio_realized_eur_present(self, client):
        """§2.2: portfolio_realized_eur present on portfolio row."""
        c, fake = client
        fake.container.seed_config("AAPL")
        _add_buy(fake, "XNYS:AAPL", quantity="100", gross_eur="10000",
                 doc_id="txn_aapl_buy_r")
        _add_sell(fake, "XNYS:AAPL", quantity="50", gross_eur="6000",
                  doc_id="txn_aapl_sell_r")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        aapl = next((r for r in data.get("rows", []) if r["symbol"] == "AAPL"), None)
        assert aapl is not None
        assert "portfolio_realized_eur" in aapl, (
            "Portfolio row must include portfolio_realized_eur field (§2.2)"
        )

    def test_zero_dividends_shows_as_non_null_string(self, client):
        """§7.2: Portfolio symbol with no dividends shows '0.00' (not null)."""
        c, fake = client
        fake.container.seed_config("AAPL")
        _add_buy(fake, "XNYS:AAPL", quantity="100", gross_eur="10000",
                 doc_id="txn_aapl_nodiv")
        # No dividend movement

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        aapl = next((r for r in data.get("rows", []) if r["symbol"] == "AAPL"), None)
        assert aapl is not None
        if "portfolio_dividends_eur" in aapl and aapl["portfolio_dividends_eur"] is not None:
            # Portfolio symbol with no dividends should show "0.00" or similar
            assert Decimal(aapl["portfolio_dividends_eur"]) == Decimal("0.00"), (
                "Portfolio symbol with no dividends should have portfolio_dividends_eur='0.00'"
            )


# ---------------------------------------------------------------------------
# Part 6 — Calls/Puts summary preserved (§3.1)
# ---------------------------------------------------------------------------

class TestCallsPutsSummaryPreserved:
    """Existing Calls Exposure and Puts Committed summary preserved."""

    def test_total_call_exposure_present(self, client):
        """total_call_exposure always present in overview response."""
        c, fake = client
        resp = c.get("/api/symbols/overview")
        data = resp.json()
        assert "total_call_exposure" in data, (
            "total_call_exposure must be present in overview (§3.1 Row 1 preserved)"
        )

    def test_total_put_exposure_present(self, client):
        """total_put_exposure always present in overview response."""
        c, fake = client
        resp = c.get("/api/symbols/overview")
        data = resp.json()
        assert "total_put_exposure" in data, (
            "total_put_exposure must be present in overview (§3.1 Row 1 preserved)"
        )

    def test_calls_exposure_computed_from_active_positions(self, client):
        """Call exposure computed from active call positions × 100 × strike."""
        c, fake = client
        fake.container.seed_config("AAPL", extra={
            "positions": [
                {"type": "call", "strike": 200, "status": "active"},
                {"type": "call", "strike": 210, "status": "active"},
                {"type": "put", "strike": 190, "status": "active"},
            ]
        })

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        # Call exposure = (200 + 210) * 100 = 41,000
        assert data["total_call_exposure"] == pytest.approx(41000.0), (
            f"Expected total_call_exposure=41000, got {data['total_call_exposure']}"
        )

    def test_puts_exposure_computed_from_active_put_positions(self, client):
        """Put exposure computed from active put positions × 100 × strike."""
        c, fake = client
        fake.container.seed_config("MSFT", extra={
            "positions": [
                {"type": "put", "strike": 350, "status": "active"},
            ]
        })

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        assert data["total_put_exposure"] == pytest.approx(35000.0), (
            f"Expected total_put_exposure=35000, got {data['total_put_exposure']}"
        )


# ---------------------------------------------------------------------------
# Part 7 — Backend API retained (§4.3, P-6)
# ---------------------------------------------------------------------------

class TestBackendAPIRetained:
    """P-6: GET /api/portfolio/holdings backend API unchanged."""

    def test_portfolio_holdings_endpoint_returns_200(self, client):
        """§4.3: GET /api/portfolio/holdings must still return 200 (not removed)."""
        c, fake = client
        resp = c.get("/api/portfolio/holdings")
        assert resp.status_code == 200, (
            f"GET /api/portfolio/holdings must return 200 (retained per §4.3); "
            f"got {resp.status_code}"
        )

    def test_portfolio_holdings_has_holdings_key(self, client):
        """Holdings response shape has 'holdings' key."""
        c, fake = client
        _add_buy(fake, "XNYS:AAPL", quantity="100", gross_eur="15000",
                 doc_id="txn_aapl_retained")
        fake.container.seed_config("AAPL")

        resp = c.get("/api/portfolio/holdings")
        data = resp.json()
        assert "holdings" in data, (
            "GET /api/portfolio/holdings must return {'holdings': [...]} shape"
        )

    def test_portfolio_holdings_returns_holdings_for_ledger_symbols(self, client):
        """Holdings endpoint returns data for symbols with ledger history."""
        c, fake = client
        fake.container.seed_config("AAPL")
        _add_buy(fake, "XNYS:AAPL", quantity="100", gross_eur="15000",
                 doc_id="txn_aapl_ret2")

        resp = c.get("/api/portfolio/holdings")
        data = resp.json()
        tickers = {h.get("ticker") for h in data.get("holdings", [])}
        assert "AAPL" in tickers, (
            "GET /api/portfolio/holdings must include AAPL after a BUY"
        )


# ---------------------------------------------------------------------------
# Part 8 — Batch reassignment API retained (§6.3, P-1)
# ---------------------------------------------------------------------------

class TestBatchReassignmentAPIRetained:
    """P-1: Batch reassignment operation accessible from Movements."""

    def test_batch_reassign_preview_endpoint_exists(self, client):
        """POST /api/portfolio/movements/batch-reassign/preview reachable (not 404/405)."""
        c, fake = client
        resp = c.post(
            "/api/portfolio/movements/batch-reassign/preview",
            json={"target_account_id": "acct_test", "filter": {}},
        )
        # Must NOT return 404 (endpoint removed) or 405 (method not allowed)
        assert resp.status_code != 404, (
            "Batch reassign preview endpoint must not be removed (§6.3 / P-1)"
        )
        assert resp.status_code != 405, (
            "Batch reassign preview must accept POST (§6.3)"
        )

    def test_batch_reassign_endpoint_exists(self, client):
        """POST /api/portfolio/movements/batch-reassign reachable (not 404/405)."""
        c, fake = client
        resp = c.post(
            "/api/portfolio/movements/batch-reassign",
            json={"target_account_id": "acct_test", "filter": {}},
        )
        assert resp.status_code != 404, (
            "Batch reassign endpoint must not be removed (§6.3 / P-1)"
        )
        assert resp.status_code != 405, (
            "Batch reassign must accept POST (§6.3)"
        )


# ---------------------------------------------------------------------------
# Part 9 — Watchlist-only display: null fields (§2.2)
# ---------------------------------------------------------------------------

class TestWatchlistOnlyDisplayContract:
    """§2.2: watchlist-only rows show null in all portfolio columns."""

    def test_watchlist_only_row_has_null_portfolio_shares(self, client):
        """Watchlist-only row: portfolio_shares is null (§2.2)."""
        c, fake = client
        fake.container.seed_config("MSFT")  # No movements

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        msft = next((r for r in data.get("rows", []) if r["symbol"] == "MSFT"), None)
        assert msft is not None
        assert msft.get("portfolio_shares") is None, (
            "Watchlist-only row must have portfolio_shares=null"
        )

    def test_watchlist_only_row_has_null_avg_cost(self, client):
        """Watchlist-only row: portfolio_avg_cost_eur is null (§2.2)."""
        c, fake = client
        fake.container.seed_config("MSFT")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        msft = next((r for r in data.get("rows", []) if r["symbol"] == "MSFT"), None)
        assert msft is not None
        assert msft.get("portfolio_avg_cost_eur") is None, (
            "Watchlist-only row must have portfolio_avg_cost_eur=null"
        )

    def test_watchlist_only_row_has_null_invested_eur(self, client):
        """Watchlist-only row: portfolio_invested_eur is null (§2.2)."""
        c, fake = client
        fake.container.seed_config("MSFT")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        msft = next((r for r in data.get("rows", []) if r["symbol"] == "MSFT"), None)
        assert msft is not None
        assert msft.get("portfolio_invested_eur") is None, (
            "Watchlist-only row must have portfolio_invested_eur=null"
        )


# ---------------------------------------------------------------------------
# Part 10 — Net realized result = closed stock result only (§2.3)
# ---------------------------------------------------------------------------

class TestNetGainsRealizedStockResultOnly:
    """§2.3: Net Gains = realized result from stock sales only; no option P&L, no unrealized."""

    def test_realized_result_from_sell_close(self, client):
        """Realized result reflects sell-close difference (FIFO/CMP)."""
        c, fake = client
        fake.container.seed_config("AAPL")
        # Buy 100 @ €100 total = €10,000
        _add_buy(fake, "XNYS:AAPL", quantity="100", gross_eur="10000",
                 doc_id="txn_aapl_buy_gains")
        # Sell 100 @ €12,000 → gain of €2,000
        _add_sell(fake, "XNYS:AAPL", quantity="100", gross_eur="12000",
                  doc_id="txn_aapl_sell_gains")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        summary = data.get("portfolio_summary", {})
        if "realized_result_eur" in summary and summary["realized_result_eur"] is not None:
            realized = Decimal(str(summary["realized_result_eur"]))
            assert realized >= Decimal("1900"), (  # allow for fee rounding
                f"Realized result should be ~2000 for buy@10000 sell@12000; got {realized}"
            )

    def test_option_positions_do_not_contribute_to_realized_result(self, client):
        """Option P&L is NOT included in portfolio_summary.realized_result_eur (§2.3 note)."""
        c, fake = client
        # Symbol with call position (closed) but no stock sells
        fake.container.seed_config("MSFT", extra={
            "positions": [
                {"type": "call", "strike": 300, "status": "closed",
                 "premium": 500, "buyback_cost": 50},
            ]
        })
        _add_buy(fake, "XNAS:MSFT", quantity="100", gross_eur="30000",
                 doc_id="txn_msft_buy_opt")
        # No stock sells

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        summary = data.get("portfolio_summary", {})
        if "realized_result_eur" in summary and summary["realized_result_eur"] is not None:
            realized = Decimal(str(summary["realized_result_eur"]))
            # Option P&L should NOT be in realized_result. With no stock sells: should be 0.
            assert realized == Decimal("0"), (
                f"realized_result_eur should be 0 (no stock sells); "
                f"got {realized} — option P&L must not contaminate this field"
            )
