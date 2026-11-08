"""Tests for symbols overview two-section partition — Symbol Unification rev 3.

Contract: Danny's §5.4 — Symbols Page Two Mutually Exclusive Sections.

Coverage:
- portfolio_rows and watchlist_rows are mutually exclusive (AC-14)
- Portfolio precedence: any ledger history → portfolio_rows (AC-15)
- Historical/zero-share positions in portfolio_rows (not watchlist)
- Soft-deleted/voided/superseded movements still confer portfolio membership
- watchlist_rows entries have portfolio_* fields as null (AC-16)
- portfolio_rows entries carry portfolio_shares, avg_cost, invested_eur (AC-16)
- Backward-compat 'rows' array = portfolio_rows + watchlist_rows flat union (AC-18)
- symbol_count, portfolio_count, watchlist_count correct
- A symbol never appears in both lists
- Migration scenario: config + ledger → moves to portfolio section
- Legacy 'rows' shape unchanged for existing callers

Uses FastAPI TestClient with fake Cosmos state.
"""

from __future__ import annotations

import pytest
from decimal import Decimal
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fake containers (reused pattern from test_portfolio_endpoints)
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
        return iter([
            doc for (pk, did), doc in self._store.items()
            if doc.get("doc_type") == "security_master"
        ])

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


def _add_buy(fake_cosmos, security_id: str, account_id: str = "_unassigned",
             quantity: str = "100", gross_eur: str = "10000",
             doc_id: str = None, deleted: bool = False,
             correction_status: str = "ACTIVE"):
    ticker = security_id.split(":")[-1]
    did = doc_id or f"txn_{ticker}_{account_id}"
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
        "net_eur": gross_eur,
        "correction_status": correction_status,
        "cost_basis_status": "COMPLETE",
    }
    if deleted:
        doc["deleted_at"] = "2026-02-01T00:00:00Z"
    fake_cosmos.portfolio_container._store[did] = doc


def _add_sell(fake_cosmos, security_id: str, account_id: str = "_unassigned", quantity: str = "100"):
    ticker = security_id.split(":")[-1]
    doc_id = f"txn_{ticker}_{account_id}_sell"
    doc = {
        "id": doc_id,
        "account_id": account_id,
        "doc_type": "ledger_txn",
        "txn_type": "SELL",
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": "2026-06-01",
        "quantity": quantity,
        "sales_type": "ACCIONES",
        "gross": {"amount": "10000", "currency": "EUR", "eur_amount": "10000"},
        "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
        "net_eur": "10000",
        "correction_status": "ACTIVE",
    }
    fake_cosmos.portfolio_container._store[doc_id] = doc


def _add_option(fake_cosmos, security_id: str, txn_type: str, account_id: str = "ibkr",
                 doc_id: str = None):
    ticker = security_id.split(":")[-1]
    did = doc_id or f"txn_{ticker}_{txn_type}_{account_id}"
    doc = {
        "id": did,
        "account_id": account_id,
        "doc_type": "ledger_txn",
        "txn_type": txn_type,
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": "2026-01-01",
        "quantity": "0",
        "gross": {"amount": "150.00", "currency": "EUR", "eur_amount": "150.00"},
        "fees": {"total": "5.00", "currency": "EUR", "total_eur": "5.00"},
        "net_eur": "145.00",
        "correction_status": "ACTIVE",
        "cost_basis_status": "COMPLETE",
    }
    fake_cosmos.portfolio_container._store[did] = doc


def test_forecast_native_classification_matches_active_holdings_and_refreshes(client, monkeypatch):
    from web.app import _compute_symbols_overview

    http, cosmos = client
    tickers = ["LIVE", "SOLD", "SHORT", "WATCH", "OPTION", "DELETED", "SUPERSEDED", "VOIDED", "DIV", "TRANSFER"]
    for ticker in tickers:
        cosmos.container.seed_security(f"XNAS:{ticker}")
        cosmos.container.seed_config(ticker, {
            "security_id": f"XNAS:{ticker}",
            "total_shares": 999 if ticker == "WATCH" else 0,
            "_auto_enrolled": True,
        })
    cosmos.container.seed_config("LEGACY")
    _add_buy(cosmos, "XNAS:LIVE")
    _add_buy(cosmos, "XNAS:SOLD")
    _add_sell(cosmos, "XNAS:SOLD")
    _add_sell(cosmos, "XNAS:SHORT", quantity="2")
    _add_option(cosmos, "XNAS:OPTION", "CALL_SELL")
    _add_buy(cosmos, "XNAS:DELETED", deleted=True)
    _add_buy(cosmos, "XNAS:SUPERSEDED", correction_status="SUPERSEDED")
    _add_buy(cosmos, "XNAS:VOIDED", correction_status="VOIDED")
    _add_buy(cosmos, "XNAS:DIV")
    cosmos.portfolio_container._store["txn_DIV__unassigned"]["txn_type"] = "DIVIDEND"
    _add_buy(cosmos, "XNAS:TRANSFER", account_id="source", quantity="12")
    _add_sell(cosmos, "XNAS:TRANSFER", account_id="source", quantity="12")
    cosmos.portfolio_container._store["txn_TRANSFER_source_sell"]["txn_type"] = "TRANSFER_OUT"
    _add_buy(cosmos, "XNAS:TRANSFER", account_id="destination", quantity="12")
    cosmos.portfolio_container._store["txn_TRANSFER_destination"]["txn_type"] = "TRANSFER_IN"
    cosmos.container._store[("SOLD", "config_SOLD")]["watchlist"]["buy_tracker"] = True

    def classification(rows):
        return {
            row["security_id"]: (row["row_source"], row["is_historical"], row["portfolio_shares"])
            for row in rows if row.get("security_id")
        }

    portfolio = _compute_symbols_overview(cosmos, cosmos.portfolio_container, view="portfolio")
    options = _compute_symbols_overview(cosmos, cosmos.portfolio_container, view="options")
    assert classification(options["rows"]) == classification(portfolio["rows"])

    def forbidden(*args, **kwargs):
        pytest.fail("Forecast must not compute full holdings, options or valuation")

    monkeypatch.setattr("src.portfolio.holdings_service.HoldingsService.compute_holdings", forbidden)
    monkeypatch.setattr("src.option_timing_store.load_current_rows", forbidden)
    monkeypatch.setattr("web.app._portfolio_valuation_fields", forbidden)
    monkeypatch.setattr("web.app._build_share_availability_map", forbidden)
    monkeypatch.setattr("web.app._build_symbols_forecast_overview", lambda *args: {"symbols": {}})
    queries = []
    original_query = cosmos.portfolio_container.query_items

    def projected_query(**kwargs):
        queries.append(kwargs["query"])
        return (
            {key: row[key] for key in ("security_id", "txn_type", "quantity") if key in row}
            for row in original_query(**kwargs)
        )

    monkeypatch.setattr(cosmos.portfolio_container, "query_items", projected_query)
    response = http.get("/api/symbols/overview?view=forecast")
    assert response.status_code == 200
    initial = response.json()
    assert classification(initial["rows"]) == classification(portfolio["rows"])
    assert classification(initial["rows"])["XNAS:SOLD"] == ("both", True, "0.000000")
    assert classification(initial["rows"])["XNAS:WATCH"] == ("watchlist", False, None)
    assert classification(initial["rows"])["XNAS:SHORT"] == ("portfolio", True, "-2.000000")
    legacy = next(row for row in initial["rows"] if row["symbol"] == "LEGACY")
    assert "row_source" not in legacy and "is_historical" not in legacy
    assert "portfolio_summary" not in initial
    assert all("option_timing" not in row for row in initial["rows"])

    # Identical ticker, different canonical security: never borrow the holding.
    current_symbols = cosmos.list_symbols()
    cosmos.list_symbols = lambda: [
        *current_symbols,
        {"symbol": "SOLD", "security_id": "XNYS:SOLD"},
    ]
    _add_buy(cosmos, "XNAS:SOLD", quantity="10", doc_id="reopened")
    _add_sell(cosmos, "XNAS:LIVE")
    refreshed = http.get("/api/symbols/overview?view=forecast")
    assert refreshed.status_code == 200
    rows = classification(refreshed.json()["rows"])
    assert rows["XNAS:SOLD"] == ("both", False, "10.000000")
    assert rows["XNAS:LIVE"] == ("portfolio", True, "0.000000")
    assert rows["XNYS:SOLD"] == ("watchlist", False, None)
    assert len(queries) == 2
    assert all("SELECT c.security_id, c.txn_type, c.quantity" in query for query in queries)
    assert all("SELECT *" not in query for query in queries)


@pytest.mark.parametrize("unavailable", ["missing", "query-error", "invalid-quantity"])
def test_forecast_classification_failure_is_not_a_valid_empty_watchlist(client, monkeypatch, unavailable):
    http, cosmos = client
    if unavailable == "missing":
        cosmos.portfolio_container = None
    elif unavailable == "query-error":
        def fail(**kwargs):
            raise RuntimeError("storage failed")
        monkeypatch.setattr(cosmos.portfolio_container, "query_items", fail)
    else:
        _add_buy(cosmos, "XNAS:LIVE", quantity="NaN")
    response = http.get("/api/symbols/overview?view=forecast")
    assert response.status_code == 503
    assert response.json() == {"error": "Holdings classification is unavailable"}


@pytest.mark.parametrize(
    ("shares", "positions", "open_options", "call_capacity"),
    [
        ("99", [], False, False),
        ("100", [], False, True),
        (None, [], False, False),
        ("100", [{"type": "call", "status": "active", "contracts": 1}], True, False),
        ("100", [{"type": "put", "status": "active", "quantity": "-2"}], True, True),
        ("100", [{"type": "call", "status": "closed", "contracts": 2}], False, True),
        ("100", [{"type": "put", "status": "rolled", "contracts": 2}], False, True),
        ("300", [{"type": "call", "status": "active", "contracts": 3}], True, False),
        ("300", [{"type": "call", "status": "active", "contracts": 3, "open_contracts": 2}], True, True),
        ("299.9999", [{"type": "call", "status": "active", "contracts": 3, "contracts_open": "-2"}], True, False),
        ("100", [{"type": "call", "status": "active", "contracts": 3, "is_paper": True}], True, True),
        ("100", [{"type": "put", "status": "active", "contracts": 1, "is_paper": True}], True, True),
        ("200", [{
            "position_id": "pos_AAPL_call_200_20270115_20260901_120000",
            "type": "call", "status": "active", "strike": 200,
            "expiration": "2027-01-15", "opened_at": "2026-09-01T12:00:00Z",
            "notes": "",
        }], True, True),
        ("100", [{"type": "call", "status": "active", "contracts": 3, "open_contracts": 0}], False, False),
        ("100", [{"type": "call", "status": "active"}], False, False),
        ("100", [{"type": "call", "status": "active", "contracts": 3, "closed_contracts": 1}], False, False),
    ],
)
def test_position_filter_projections_use_remaining_options_and_real_free_shares(
    client, shares, positions, open_options, call_capacity,
):
    import copy

    c, fake = client
    fake.container.seed_config("AAPL", {
        "positions": positions,
        # Neither a manual override nor agent enrollment proves real capacity.
        "total_shares": 1000,
        "watchlist": {"covered_call": True, "cash_secured_put": True},
    })
    if shares is not None:
        _add_buy(fake, "XNYS:AAPL", quantity=shares)
    before = copy.deepcopy(fake.container._store)

    response = c.get("/api/symbols/overview")

    assert response.status_code == 200
    data = response.json()
    row = data["rows"][0]
    assert row["has_open_options"] is open_options
    assert row["can_open_covered_calls"] is call_capacity
    section = "portfolio_rows" if shares is not None else "watchlist_rows"
    assert data[section][0] == row
    assert fake.container._store == before


def test_position_filter_counts_accumulate_live_calls_and_keep_option_only_puts(client):
    c, fake = client
    fake.container.seed_config("AAPL", {"positions": [
        {"type": "call", "status": "active", "contracts": 3, "open_contracts": 1},
        {"type": "call", "status": "active", "quantity": "-2"},
        {"type": "put", "status": "active", "contracts": 4},
        {"type": "call", "status": "active", "contracts": 20, "is_paper": True},
        {"type": "call", "status": "closed", "contracts": 20},
    ]})
    _add_buy(fake, "XNYS:AAPL", quantity="399")
    fake.container.seed_config("PUTONLY", {"positions": [
        {"type": "put", "status": "active", "contracts": 1},
    ]})

    response = c.get("/api/symbols/overview")
    assert response.status_code == 200
    rows = {r["symbol"]: r for r in response.json()["rows"]}
    assert rows["AAPL"]["has_open_options"] is True
    assert rows["AAPL"]["can_open_covered_calls"] is False
    assert rows["PUTONLY"]["has_open_options"] is True
    assert rows["PUTONLY"]["can_open_covered_calls"] is False

    _add_buy(fake, "XNYS:AAPL", quantity="400")
    rows = {r["symbol"]: r for r in c.get("/api/symbols/overview").json()["rows"]}
    assert rows["AAPL"]["can_open_covered_calls"] is True


# ---------------------------------------------------------------------------
# §5.4 — Two-section partition
# ---------------------------------------------------------------------------

class TestOverviewReturnsNewSections:
    def test_overview_has_portfolio_rows(self, client):
        c, fake = client
        resp = c.get("/api/symbols/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert "portfolio_rows" in data, "Response must include 'portfolio_rows'"

    def test_overview_has_watchlist_rows(self, client):
        c, fake = client
        resp = c.get("/api/symbols/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert "watchlist_rows" in data, "Response must include 'watchlist_rows'"

    def test_overview_has_portfolio_count_and_watchlist_count(self, client):
        c, fake = client
        resp = c.get("/api/symbols/overview")
        data = resp.json()
        assert "portfolio_count" in data
        assert "watchlist_count" in data


class TestPortfolioPrecedence:
    def test_symbol_with_ledger_goes_to_portfolio_rows(self, client):
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        fake.container.seed_config("AAPL", {"security_id": "XNYS:AAPL"})
        _add_buy(fake, "XNYS:AAPL")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        portfolio_symbols = [r["symbol"] for r in data.get("portfolio_rows", [])]
        watchlist_symbols = [r["symbol"] for r in data.get("watchlist_rows", [])]
        assert "AAPL" in portfolio_symbols, "Symbol with ledger must be in portfolio_rows"
        assert "AAPL" not in watchlist_symbols, "Symbol with ledger must NOT be in watchlist_rows"

    def test_symbol_without_ledger_goes_to_watchlist_rows(self, client):
        c, fake = client
        fake.container.seed_config("MSFT", {"security_id": "XNAS:MSFT"})
        # No ledger docs for MSFT

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        watchlist_symbols = [r["symbol"] for r in data.get("watchlist_rows", [])]
        portfolio_symbols = [r["symbol"] for r in data.get("portfolio_rows", [])]
        assert "MSFT" in watchlist_symbols, "Symbol without ledger must be in watchlist_rows"
        assert "MSFT" not in portfolio_symbols, "Symbol without ledger must NOT be in portfolio_rows"

    def test_mutual_exclusivity_single_symbol(self, client):
        c, fake = client
        fake.container.seed_config("AAPL")
        _add_buy(fake, "XNYS:AAPL")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        p_syms = {r["symbol"] for r in data.get("portfolio_rows", [])}
        w_syms = {r["symbol"] for r in data.get("watchlist_rows", [])}
        overlap = p_syms & w_syms
        assert overlap == set(), (
            f"Symbols appear in both sections (AC-14 violation): {overlap}"
        )

    def test_mutual_exclusivity_mixed_symbols(self, client):
        c, fake = client
        # AAPL: has ledger → portfolio
        fake.container.seed_config("AAPL")
        _add_buy(fake, "XNYS:AAPL")
        # MSFT: no ledger → watchlist
        fake.container.seed_config("MSFT")
        # O: has ledger → portfolio
        fake.container.seed_config("O")
        _add_buy(fake, "XNYS:O", quantity="50", gross_eur="5000", doc_id="txn_o_001")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        p_syms = {r["symbol"] for r in data.get("portfolio_rows", [])}
        w_syms = {r["symbol"] for r in data.get("watchlist_rows", [])}
        overlap = p_syms & w_syms
        assert overlap == set(), f"No symbol may appear in both lists: {overlap}"
        assert "AAPL" in p_syms
        assert "MSFT" in w_syms
        assert "O" in p_syms


class TestHistoricalAndZeroShare:
    def test_zero_share_historical_in_portfolio_rows(self, client):
        """Rev 6: symbol with full sell-out (net 0 shares) stays in portfolio_rows —
        the active holdings snapshot still has an entry for it (real equity trade
        history), so it's a "historical" portfolio row, not watchlist."""
        c, fake = client
        fake.container.seed_config("AAPL")
        _add_buy(fake, "XNYS:AAPL", quantity="100", doc_id="txn_aapl_buy_hist")
        _add_sell(fake, "XNYS:AAPL", quantity="100")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        portfolio_symbols = [r["symbol"] for r in data.get("portfolio_rows", [])]
        watchlist_symbols = [r["symbol"] for r in data.get("watchlist_rows", [])]
        assert "AAPL" in portfolio_symbols, (
            "Rev 6: a fully-sold symbol still has a holdings entry (shares=0) -> portfolio, "
            "marked is_historical for the frontend's default-on 'Hide historical' filter"
        )
        assert "AAPL" not in watchlist_symbols
        aapl_row = next(r for r in data["portfolio_rows"] if r["symbol"] == "AAPL")
        assert aapl_row["is_historical"] is True

    def test_soft_deleted_movement_does_not_confer_portfolio_membership(self, client):
        """Rev 6: an entirely soft-deleted movement never enters the active holdings
        snapshot, so the symbol has no holdings entry at all -> watchlist."""
        c, fake = client
        fake.container.seed_config("AAPL")
        # Deleted movement → excluded from the active holdings snapshot entirely
        _add_buy(fake, "XNYS:AAPL", doc_id="txn_aapl_deleted", deleted=True)

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        portfolio_symbols = [r["symbol"] for r in data.get("portfolio_rows", [])]
        watchlist_symbols = [r["symbol"] for r in data.get("watchlist_rows", [])]
        assert "AAPL" in watchlist_symbols, (
            "Rev 6: soft-deleted-only movements produce no holdings entry -> watchlist"
        )
        assert "AAPL" not in portfolio_symbols

    def test_superseded_movement_does_not_confer_portfolio_membership(self, client):
        """Rev 6: an entirely SUPERSEDED movement never enters the active holdings
        snapshot, so the symbol has no holdings entry at all -> watchlist."""
        c, fake = client
        fake.container.seed_config("AAPL")
        _add_buy(fake, "XNYS:AAPL", doc_id="txn_aapl_superseded",
                 correction_status="SUPERSEDED")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        portfolio_symbols = [r["symbol"] for r in data.get("portfolio_rows", [])]
        watchlist_symbols = [r["symbol"] for r in data.get("watchlist_rows", [])]
        assert "AAPL" in watchlist_symbols
        assert "AAPL" not in portfolio_symbols

    def test_option_only_symbol_goes_to_watchlist_not_hidden_portfolio(self, client):
        """A symbol that has NEVER had a real equity trade — only option
        movements (e.g. CALL_SELL/PUT_SELL) — must appear in watchlist_rows,
        not as a hidden "historical" portfolio row. Regression test for the
        bug where compute_holdings created a spurious zero-share holdings
        entry from option-only cashflows alone."""
        c, fake = client
        fake.container.seed_config("NKE")
        _add_option(fake, "XNAS:NKE", "PUT_SELL", doc_id="txn_nke_put_sell")
        _add_option(fake, "XNAS:NKE", "CALL_SELL", doc_id="txn_nke_call_sell")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        portfolio_symbols = [r["symbol"] for r in data.get("portfolio_rows", [])]
        watchlist_symbols = [r["symbol"] for r in data.get("watchlist_rows", [])]
        assert "NKE" in watchlist_symbols, (
            "option-only symbol (no equity trade history) must be in watchlist_rows"
        )
        assert "NKE" not in portfolio_symbols
        nke_row = next(r for r in data["watchlist_rows"] if r["symbol"] == "NKE")
        assert nke_row["row_source"] == "watchlist"
        assert nke_row["portfolio_shares"] is None


# ---------------------------------------------------------------------------
# §5.4.2 — Portfolio-derived fields
# ---------------------------------------------------------------------------

class TestPortfolioDerivedFields:
    def test_portfolio_rows_have_portfolio_shares(self, client):
        c, fake = client
        fake.container.seed_config("AAPL")
        _add_buy(fake, "XNYS:AAPL", quantity="75", gross_eur="7500")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        aapl_row = next(r for r in data.get("portfolio_rows", []) if r["symbol"] == "AAPL")
        assert "portfolio_shares" in aapl_row, "portfolio_rows must have portfolio_shares"
        assert aapl_row["portfolio_shares"] is not None
        assert Decimal(aapl_row["portfolio_shares"]) == Decimal("75")

    def test_portfolio_rows_have_avg_cost(self, client):
        c, fake = client
        fake.container.seed_config("AAPL")
        _add_buy(fake, "XNYS:AAPL", quantity="10", gross_eur="1000")  # avg = 100

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        aapl_row = next(r for r in data.get("portfolio_rows", []) if r["symbol"] == "AAPL")
        assert "portfolio_avg_cost_eur" in aapl_row
        assert aapl_row["portfolio_avg_cost_eur"] is not None

    def test_portfolio_rows_have_invested_eur(self, client):
        c, fake = client
        fake.container.seed_config("AAPL")
        _add_buy(fake, "XNYS:AAPL", quantity="10", gross_eur="1000")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        aapl_row = next(r for r in data.get("portfolio_rows", []) if r["symbol"] == "AAPL")
        assert "portfolio_invested_eur" in aapl_row
        assert aapl_row["portfolio_invested_eur"] is not None

    def test_watchlist_rows_have_null_portfolio_fields(self, client):
        c, fake = client
        fake.container.seed_config("MSFT")
        # No ledger

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        msft_row = next(r for r in data.get("watchlist_rows", []) if r["symbol"] == "MSFT")
        assert msft_row.get("portfolio_shares") is None
        assert msft_row.get("portfolio_avg_cost_eur") is None
        assert msft_row.get("portfolio_invested_eur") is None

    def test_portfolio_rows_have_list_section_portfolio(self, client):
        c, fake = client
        fake.container.seed_config("AAPL")
        _add_buy(fake, "XNYS:AAPL")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        aapl_row = next(r for r in data.get("portfolio_rows", []) if r["symbol"] == "AAPL")
        assert aapl_row.get("list_section") == "portfolio"

    def test_watchlist_rows_have_list_section_watchlist(self, client):
        c, fake = client
        fake.container.seed_config("MSFT")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        msft_row = next(r for r in data.get("watchlist_rows", []) if r["symbol"] == "MSFT")
        assert msft_row.get("list_section") == "watchlist"


# ---------------------------------------------------------------------------
# §5.4.2 — Backward compat: 'rows' flat union
# ---------------------------------------------------------------------------

class TestBackwardCompatRows:
    def test_rows_present_as_flat_union(self, client):
        """Legacy 'rows' must be the flat union of portfolio_rows + watchlist_rows."""
        c, fake = client
        fake.container.seed_config("AAPL")
        _add_buy(fake, "XNYS:AAPL")  # portfolio
        fake.container.seed_config("MSFT")  # watchlist

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        assert "rows" in data, "Backward-compat 'rows' field must be present (AC-18)"
        all_symbols = {r["symbol"] for r in data["rows"]}
        assert "AAPL" in all_symbols
        assert "MSFT" in all_symbols

    def test_rows_is_union_of_both_sections(self, client):
        c, fake = client
        fake.container.seed_config("AAPL")
        _add_buy(fake, "XNYS:AAPL")
        fake.container.seed_config("MSFT")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        rows_set = {r["symbol"] for r in data["rows"]}
        expected = (
            {r["symbol"] for r in data.get("portfolio_rows", [])} |
            {r["symbol"] for r in data.get("watchlist_rows", [])}
        )
        assert rows_set == expected, (
            f"rows must be union of portfolio_rows + watchlist_rows; "
            f"got rows={rows_set}, expected={expected}"
        )

    def test_counts_match_section_lengths(self, client):
        c, fake = client
        fake.container.seed_config("AAPL")
        _add_buy(fake, "XNYS:AAPL")
        fake.container.seed_config("MSFT")
        fake.container.seed_config("O")
        _add_buy(fake, "XNYS:O", doc_id="txn_o_counts")

        resp = c.get("/api/symbols/overview")
        data = resp.json()
        assert data["portfolio_count"] == len(data.get("portfolio_rows", []))
        assert data["watchlist_count"] == len(data.get("watchlist_rows", []))
        assert data["symbol_count"] == data["portfolio_count"] + data["watchlist_count"]
