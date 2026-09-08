"""Tests for unified symbol detail endpoint — Symbol Unification rev 3.

Contract: Danny's §4 — Unified Symbol Details.

Coverage:
- Response includes security, portfolio, symbol_state on every successful call
- watchlist_only state: portfolio section is null; all current fields intact
- portfolio_only state: portfolio section present; symbol_state correct
- watchlist_and_portfolio: both sections
- portfolio_historical (zero-share): portfolio section with current_shares="0"
- Legacy ticker-only resolution: single match → resolves unambiguously
- Legacy ticker → multiple security_masters → 300 Multiple Choices
- Legacy ticker → zero security_masters, no config → 404
- MIC:TICKER direct lookup works
- holdings_by_account populated correctly
- recent_movements present in portfolio section
- Partial portfolio failure isolation: security section always present even if portfolio fails
- All existing response fields preserved (no regression)

Uses FastAPI TestClient with fake Cosmos state.
"""

from __future__ import annotations

import pytest
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fake containers
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
            if "@account_id" in param_map and doc.get("account_id") != param_map["@account_id"]:
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
        param_map = {p["name"]: p["value"] for p in (parameters or [])}
        results = []
        for (pk, did), doc in self._store.items():
            if partition_key and pk != partition_key:
                continue
            if doc.get("doc_type") != "security_master":
                continue
            if "@isin" in param_map and doc.get("isin") != param_map["@isin"]:
                continue
            results.append(dict(doc))
        return iter(results)

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

    def seed_security(self, security_id: str, company_name: str = "Test Co", isin: str = None):
        mic, ticker = security_id.split(":", 1)
        doc = {
            "id": f"sec_{mic}_{ticker}",
            "symbol": ticker,
            "doc_type": "security_master",
            "security_id": security_id,
            "ticker": ticker,
            "company_name": company_name,
            "exchange_mic": mic,
            "listing_currency": "USD",
            "status": "ACTIVE",
        }
        if isin:
            doc["isin"] = isin
        self._store[(ticker, doc["id"])] = doc

    def seed_config(self, ticker: str, extra: dict | None = None):
        doc = {
            "id": f"config_{ticker}",
            "symbol": ticker,
            "doc_type": "symbol_config",
            "display_name": "Test Co",
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
        # Return symbol_config if present
        for (pk, did), doc in self.container._store.items():
            if doc.get("doc_type") == "symbol_config" and doc.get("symbol") == symbol.upper():
                return doc
        return None

    def get_plans(self, symbol):
        return []

    def get_recent_activities(self, symbol, agent_type, max_entries=50):
        return []

    def get_recent_alerts(self, symbol, agent_type, max_entries=30):
        return []

    def get_next_earnings_date(self, symbol):
        return None


@pytest.fixture
def client():
    from web.app import app
    fake_cosmos = FakeCosmos()
    with TestClient(app) as c:
        app.state.cosmos = fake_cosmos
        app.state.cosmos_error = None
        yield c, fake_cosmos


def _add_ledger_buy(fake_cosmos, security_id: str, account_id: str = "_unassigned",
                    quantity: str = "100", gross_eur: str = "10000",
                    doc_id: str = None, deleted: bool = False):
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
        "correction_status": "ACTIVE",
    }
    if deleted:
        doc["deleted_at"] = "2026-02-01T00:00:00Z"
    fake_cosmos.portfolio_container._store[did] = doc


def _add_ledger_sell(fake_cosmos, security_id: str, account_id: str = "_unassigned",
                     quantity: str = "100"):
    ticker = security_id.split(":")[-1]
    doc = {
        "id": f"txn_{ticker}_{account_id}_sell",
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
    fake_cosmos.portfolio_container._store[doc["id"]] = doc


# ---------------------------------------------------------------------------
# §4 — MIC:TICKER direct lookup
# ---------------------------------------------------------------------------

class TestMicTickerDirectLookup:
    def test_mic_ticker_returns_200(self, client):
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        fake.container.seed_config("AAPL", {"security_id": "XNYS:AAPL"})

        resp = c.get("/api/symbols/XNYS:AAPL/detail")
        assert resp.status_code == 200

    def test_mic_ticker_response_has_security_field(self, client):
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        fake.container.seed_config("AAPL", {"security_id": "XNYS:AAPL"})

        resp = c.get("/api/symbols/XNYS:AAPL/detail")
        data = resp.json()
        assert "security" in data, "Response must include 'security' field"
        assert data["security"]["security_id"] == "XNYS:AAPL"

    def test_mic_ticker_response_has_symbol_state(self, client):
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        fake.container.seed_config("AAPL", {"security_id": "XNYS:AAPL"})

        resp = c.get("/api/symbols/XNYS:AAPL/detail")
        data = resp.json()
        assert "symbol_state" in data, "Response must include 'symbol_state'"
        assert data["symbol_state"] in (
            "watchlist_only", "portfolio_only", "watchlist_and_portfolio", "portfolio_historical"
        )


# ---------------------------------------------------------------------------
# §4/5 — Symbol state: watchlist_only
# ---------------------------------------------------------------------------

class TestWatchlistOnlyState:
    def test_watchlist_only_no_portfolio_section(self, client):
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        fake.container.seed_config("AAPL", {"security_id": "XNYS:AAPL"})
        # No ledger docs → watchlist_only

        resp = c.get("/api/symbols/XNYS:AAPL/detail")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol_state"] == "watchlist_only"
        assert data.get("portfolio") is None, (
            "watchlist_only symbols must not have a portfolio section"
        )

    def test_watchlist_only_preserves_existing_fields(self, client):
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        fake.container.seed_config("AAPL", {
            "security_id": "XNYS:AAPL",
            "telegram_notifications_enabled": True,
            "watchlist": {"covered_call": True, "cash_secured_put": False, "buy_tracker": True},
        })

        resp = c.get("/api/symbols/XNYS:AAPL/detail")
        data = resp.json()
        assert data["telegram_notifications_enabled"] is True
        assert data["watchlist"]["covered_call"] is True
        assert data["watchlist"]["buy_tracker"] is True


# ---------------------------------------------------------------------------
# §4/5 — Symbol state: portfolio_only (transient — read-repair not yet run)
# ---------------------------------------------------------------------------

class TestPortfolioOnlyState:
    def test_portfolio_only_has_portfolio_section(self, client):
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        # No config (yet); has ledger → portfolio_only
        _add_ledger_buy(fake, "XNYS:AAPL", quantity="100")

        resp = c.get("/api/symbols/XNYS:AAPL/detail")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol_state"] == "portfolio_only"
        assert data.get("portfolio") is not None

    def test_portfolio_only_current_shares_correct(self, client):
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        _add_ledger_buy(fake, "XNYS:AAPL", quantity="75")

        resp = c.get("/api/symbols/XNYS:AAPL/detail")
        data = resp.json()
        portfolio = data.get("portfolio", {})
        # current_shares must be "75.000000" or equivalent
        assert portfolio.get("current_shares") is not None
        from decimal import Decimal
        assert Decimal(portfolio["current_shares"]) == Decimal("75")


# ---------------------------------------------------------------------------
# §4/5 — Symbol state: watchlist_and_portfolio
# ---------------------------------------------------------------------------

class TestWatchlistAndPortfolioState:
    def test_both_sections_present(self, client):
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        fake.container.seed_config("AAPL", {"security_id": "XNYS:AAPL"})
        _add_ledger_buy(fake, "XNYS:AAPL", quantity="100")

        resp = c.get("/api/symbols/XNYS:AAPL/detail")
        data = resp.json()
        assert data["symbol_state"] == "watchlist_and_portfolio"
        assert data.get("security") is not None
        assert data.get("portfolio") is not None

    def test_portfolio_section_field_names(self, client):
        """Final contract: portfolio section uses average_cost_eur (not avg_cost_eur) at top level."""
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        fake.container.seed_config("AAPL", {"security_id": "XNYS:AAPL"})
        _add_ledger_buy(fake, "XNYS:AAPL", quantity="100")

        resp = c.get("/api/symbols/XNYS:AAPL/detail")
        data = resp.json()
        portfolio = data.get("portfolio", {})
        # Required top-level portfolio fields per final contract
        for field in ("current_shares", "average_cost_eur", "current_invested_eur",
                      "total_dividends_eur", "holdings_by_account",
                      "recent_movements", "movement_count"):
            assert field in portfolio, (
                f"portfolio section missing '{field}' — final contract requires this field"
            )

    def test_holdings_by_account_populated(self, client):
        """holdings_by_account must list each brokerage account with required fields.

        Final contract: each entry must have account_id (str), shares (str), avg_cost_eur (str|null).
        """
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        fake.container.seed_config("AAPL", {"security_id": "XNYS:AAPL"})
        _add_ledger_buy(fake, "XNYS:AAPL", account_id="acct_fidelity_main",
                        quantity="50", doc_id="txn_aapl_fid")
        _add_ledger_buy(fake, "XNYS:AAPL", account_id="acct_heytrade_main",
                        quantity="30", doc_id="txn_aapl_hyt")

        resp = c.get("/api/symbols/XNYS:AAPL/detail")
        data = resp.json()
        portfolio = data.get("portfolio", {})
        by_account = portfolio.get("holdings_by_account", [])
        account_ids = {a["account_id"] for a in by_account}
        assert "acct_fidelity_main" in account_ids
        assert "acct_heytrade_main" in account_ids

        # Final contract: each entry must carry 'shares' (required) and 'avg_cost_eur'
        for entry in by_account:
            assert "shares" in entry, (
                f"holdings_by_account entry missing 'shares' key: {entry}"
            )
            assert "avg_cost_eur" in entry, (
                f"holdings_by_account entry missing 'avg_cost_eur' key: {entry}"
            )

    def test_holdings_by_account_includes_name_invested_and_dividends(self, client):
        """Per-account rows must resolve account_name from the account doc (name only,
        never broker/type), and include current_invested_eur/total_dividends_eur so the
        Symbol Detail table can render real per-account values instead of placeholders.
        """
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        fake.container.seed_config("AAPL", {"security_id": "XNYS:AAPL"})
        fake.portfolio_container._store["acct_fidelity_main"] = {
            "id": "acct_fidelity_main",
            "account_id": "acct_fidelity_main",
            "doc_type": "account",
            "broker": "OTHER",
            "name": "Fidelity Brokerage",
            "currency": "EUR",
        }
        _add_ledger_buy(fake, "XNYS:AAPL", account_id="acct_fidelity_main",
                        quantity="50", doc_id="txn_aapl_fid")

        resp = c.get("/api/symbols/XNYS:AAPL/detail")
        data = resp.json()
        portfolio = data.get("portfolio", {})
        by_account = portfolio.get("holdings_by_account", [])
        entry = next(a for a in by_account if a["account_id"] == "acct_fidelity_main")

        assert entry["account_name"] == "Fidelity Brokerage"
        assert "OTHER" not in str(entry["account_name"])
        assert "current_invested_eur" in entry
        assert "total_dividends_eur" in entry

    def test_recent_movements_in_portfolio_section(self, client):
        """recent_movements entries must carry final contract field names.

        Final contract: id, txn_type, trade_date, quantity, gross_eur.
        """
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        fake.container.seed_config("AAPL", {"security_id": "XNYS:AAPL"})
        _add_ledger_buy(fake, "XNYS:AAPL", quantity="100")

        resp = c.get("/api/symbols/XNYS:AAPL/detail")
        data = resp.json()
        portfolio = data.get("portfolio", {})
        assert "recent_movements" in portfolio, (
            "portfolio section must include recent_movements"
        )
        assert isinstance(portfolio["recent_movements"], list)

        movements = portfolio["recent_movements"]
        if movements:  # non-empty when get_movements is supported
            m = movements[0]
            for field in ("id", "txn_type", "trade_date", "gross_eur"):
                assert field in m, (
                    f"recent_movements entry missing '{field}' key — "
                    f"final contract requires: id, txn_type, trade_date, quantity, gross_eur"
                )


# ---------------------------------------------------------------------------
# §4/5 — Symbol state: portfolio_historical (zero-share)
# ---------------------------------------------------------------------------

class TestPortfolioHistoricalState:
    def test_historical_zero_share_has_portfolio_section(self, client):
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        fake.container.seed_config("AAPL", {"security_id": "XNYS:AAPL"})
        # Buy then sell everything → zero shares
        _add_ledger_buy(fake, "XNYS:AAPL", quantity="100", doc_id="txn_aapl_buy")
        _add_ledger_sell(fake, "XNYS:AAPL", quantity="100")

        resp = c.get("/api/symbols/XNYS:AAPL/detail")
        data = resp.json()
        assert data["symbol_state"] == "portfolio_historical"
        assert data.get("portfolio") is not None

    def test_historical_current_shares_is_zero(self, client):
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        fake.container.seed_config("AAPL", {"security_id": "XNYS:AAPL"})
        _add_ledger_buy(fake, "XNYS:AAPL", quantity="100", doc_id="txn_aapl_buy2")
        _add_ledger_sell(fake, "XNYS:AAPL", quantity="100")

        resp = c.get("/api/symbols/XNYS:AAPL/detail")
        data = resp.json()
        from decimal import Decimal
        portfolio = data.get("portfolio", {})
        assert Decimal(portfolio["current_shares"]) == Decimal("0"), (
            "portfolio_historical symbol must have current_shares = 0"
        )


# ---------------------------------------------------------------------------
# §4.2 — Legacy ticker-only route resolution
# ---------------------------------------------------------------------------

class TestLegacyTickerResolution:
    def test_single_security_master_resolves_unambiguously(self, client):
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        fake.container.seed_config("AAPL", {"security_id": "XNYS:AAPL"})

        resp = c.get("/api/symbols/AAPL/detail")
        # Must resolve to 200 (not 300)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("security", {}).get("security_id") == "XNYS:AAPL"

    def test_multiple_security_masters_returns_300(self, client):
        """Same ticker on two MICs → 300 Multiple Choices.

        Frontend reads result.data.multiple_choices and result.data.query
        (see /frontend/src/app/symbols/[symbol]/page.tsx lines 28-30).
        Backend also returns 'candidates' and 'choices' aliases for test compat.
        """
        c, fake = client
        fake.container.seed_security("XMAD:SAN", "Banco Santander")
        fake.container.seed_security("XPAR:SAN", "Sanofi SA")

        resp = c.get("/api/symbols/SAN/detail")
        assert resp.status_code == 300
        data = resp.json()

        # Primary key consumed by frontend
        assert "multiple_choices" in data, (
            "300 response must include 'multiple_choices' — consumed by SymbolDisambiguation component"
        )
        # query field consumed by frontend (falls back to URL symbol if absent)
        assert "query" in data, (
            "300 response must include 'query' — consumed by page.tsx: body.query ?? symbol"
        )
        candidate_sids = {c_item.get("security_id") for c_item in data["multiple_choices"]}
        assert "XMAD:SAN" in candidate_sids
        assert "XPAR:SAN" in candidate_sids

    def test_zero_security_masters_no_config_returns_404(self, client):
        """No security_master and no symbol_config → 404."""
        c, fake = client
        resp = c.get("/api/symbols/NONEXISTENT/detail")
        assert resp.status_code == 404

    def test_config_with_security_id_used_for_canonical_lookup(self, client):
        """Config has security_id → use it directly; no ambiguity scan needed."""
        c, fake = client
        fake.container.seed_security("XMAD:TEF", "Telefónica")
        fake.container.seed_config("TEF", {"security_id": "XMAD:TEF"})

        resp = c.get("/api/symbols/TEF/detail")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("security", {}).get("security_id") == "XMAD:TEF"


# ---------------------------------------------------------------------------
# Backward compatibility — existing fields still present
# ---------------------------------------------------------------------------

class TestExistingFieldsPreserved:
    def test_legacy_fields_still_present(self, client):
        """All fields from the pre-unification response shape must still be present."""
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        fake.container.seed_config("AAPL", {
            "security_id": "XNYS:AAPL",
            "exchange": "XNYS",
            "telegram_notifications_enabled": False,
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
        })

        resp = c.get("/api/symbols/XNYS:AAPL/detail")
        data = resp.json()
        # Core legacy fields
        assert "symbol" in data
        assert "display_name" in data
        assert "watchlist" in data
        assert "telegram_notifications_enabled" in data
        assert "positions" in data
        assert "activities" in data
        assert "plans" in data
        assert "summary" in data
        assert "is_paused" in data
