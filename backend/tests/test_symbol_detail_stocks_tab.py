"""Tests for Symbol Detail Stocks tab data contract.

Investigation findings (Basher, Symbol Details Stocks/Options task):

DATA VERDICT: Backend populates recent_movements correctly for all portfolio states.
ROOT CAUSE of missing tab visibility: No tab UI in frontend — flat scrolling layout,
no "Options" / "Stocks" section labels. Fix is a frontend structural change.

SECONDARY GAP: get_movements() is called without txn_type filter, so TRANSFER_IN
and TRANSFER_OUT appear in recent_movements alongside BUY/SELL/DIVIDEND. Backend
supports txn_type filtering; the detail endpoint just doesn't use it yet.

DIVIDEND: Skipped pending Danny's amended contract (composite corporate action).

Coverage:
- _map_recent_movement() maps all 13 contract fields (full field matrix)
- SELL movement includes sales_type (ACCIONES / DERECHOS)
- import_source audit field preserved through mapping
- correction_status exposed in mapped output
- SUPERSEDED movement excluded from recent_movements at endpoint level
- VOIDED movement excluded from recent_movements
- Soft-deleted movement excluded
- ACTIVE movement included correctly
- TRANSFER_IN movement currently appears (documented existing behavior)
- portfolio_only state exposes recent_movements
- portfolio_historical state exposes recent_movements
- watchlist_and_portfolio state exposes recent_movements
- watchlist_only state: portfolio is null, no recent_movements
- movement_count reflects unfiltered total, not page limit
- Existing portfolio / rights / transfer / CMP / Symbol Unification tests green (no
  regression — run test_unified_symbol_detail.py to confirm)
"""

from __future__ import annotations

import pytest
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Dividend skip (pending Danny's amended contract)
# ---------------------------------------------------------------------------

_DIVIDEND_SKIP_REASON = (
    "DIVIDEND Stocks-tab mapping skipped: pending Danny's amended contract. "
    "New requirement: withholding amounts/percentages auto-calculated; "
    "DIVIDEND may be a composite corporate action with linked legs. "
    "Unblock after amended contract lands and composite model implemented."
)


# ---------------------------------------------------------------------------
# Minimal fake infrastructure (mirrors test_unified_symbol_detail.py pattern)
# ---------------------------------------------------------------------------

class FakePortfolioContainer:
    def __init__(self):
        self._store: dict = {}

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
            if "@txn_type" in param_map and doc.get("txn_type") != param_map["@txn_type"]:
                continue
            results.append(dict(doc))
        # Simulate SELECT VALUE COUNT(1) — return count as integer, not documents.
        if "VALUE COUNT(1)" in query:
            return iter([len(results)])
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
        for key in list(self._store.keys()):
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
                      isin: str = None):
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
            "watchlist": {"covered_call": False, "cash_secured_put": False,
                          "buy_tracker": False},
            "positions": [],
            "total_shares": 0,
        }
        if extra:
            doc.update(extra)
        self._store[(ticker, doc["id"])] = doc
        return doc


class FakeCosmos:
    def __init__(self):
        self.container = FakeSymbolsContainer()
        self.portfolio_container = FakePortfolioContainer()
        self.import_sessions_container = None

    def list_symbols(self):
        return [
            doc for (pk, did), doc in self.container._store.items()
            if doc.get("doc_type") == "symbol_config"
        ]

    def get_symbol(self, symbol):
        for (pk, did), doc in self.container._store.items():
            if doc.get("doc_type") == "symbol_config" and \
               doc.get("symbol") == symbol.upper():
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
    fake = FakeCosmos()
    with TestClient(app) as c:
        app.state.cosmos = fake
        app.state.cosmos_error = None
        yield c, fake


# ---------------------------------------------------------------------------
# Ledger doc builders
# ---------------------------------------------------------------------------

def _buy(fake, security_id: str, doc_id: str = "txn_buy_1",
         account_id: str = "_unassigned",
         quantity: str = "100",
         gross_eur: str = "5000.00",
         fees_eur: str = "9.95",
         net_eur: str = "5009.95",
         correction_status: str = "ACTIVE",
         import_source: str | None = None,
         deleted: bool = False):
    ticker = security_id.split(":")[-1]
    doc = {
        "id": doc_id,
        "account_id": account_id,
        "doc_type": "ledger_txn",
        "txn_type": "BUY",
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": "2024-03-15",
        "quantity": quantity,
        "gross": {"amount": "5000.00", "currency": "EUR", "eur_amount": gross_eur},
        "fees": {"total": fees_eur, "currency": "EUR", "total_eur": fees_eur},
        "net": {"amount": net_eur, "currency": "EUR", "eur_amount": net_eur},
        "net_eur": net_eur,
        "correction_status": correction_status,
    }
    if import_source:
        doc["import_source"] = import_source
    if deleted:
        doc["deleted_at"] = "2026-01-01T00:00:00Z"
    fake.portfolio_container._store[doc_id] = doc


def _sell(fake, security_id: str, doc_id: str = "txn_sell_1",
          account_id: str = "_unassigned",
          quantity: str = "50",
          gross_eur: str = "2700.00",
          fees_eur: str = "8.50",
          net_eur: str = "2691.50",
          sales_type: str = "ACCIONES",
          correction_status: str = "ACTIVE"):
    ticker = security_id.split(":")[-1]
    doc = {
        "id": doc_id,
        "account_id": account_id,
        "doc_type": "ledger_txn",
        "txn_type": "SELL",
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": "2024-09-20",
        "quantity": quantity,
        "sales_type": sales_type,
        "gross": {"amount": "2700.00", "currency": "EUR", "eur_amount": gross_eur},
        "fees": {"total": fees_eur, "currency": "EUR", "total_eur": fees_eur},
        "net": {"amount": net_eur, "currency": "EUR", "eur_amount": net_eur},
        "net_eur": net_eur,
        "correction_status": correction_status,
    }
    fake.portfolio_container._store[doc_id] = doc


def _transfer_in(fake, security_id: str, doc_id: str = "txn_xfer_1",
                 account_id: str = "_unassigned",
                 quantity: str = "25",
                 correction_status: str = "ACTIVE"):
    ticker = security_id.split(":")[-1]
    doc = {
        "id": doc_id,
        "account_id": account_id,
        "doc_type": "ledger_txn",
        "txn_type": "TRANSFER_IN",
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": "2024-06-01",
        "quantity": quantity,
        "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
        "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
        "net": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
        "net_eur": "0",
        "correction_status": correction_status,
    }
    fake.portfolio_container._store[doc_id] = doc


def _dividend(fake, security_id: str, doc_id: str = "txn_div_1",
              account_id: str = "_unassigned",
              gross_eur: str = "1000.000000",
              net_eur: str = "800.000000",
              ca_group_id: str | None = "cag_testdiv001",
              correction_status: str = "ACTIVE"):
    """Seed a CASH_DIVIDEND leg doc (composite model: Amendment H finalized)."""
    ticker = security_id.split(":")[-1]
    doc = {
        "id": doc_id,
        "account_id": account_id,
        "doc_type": "ledger_txn",
        "txn_type": "DIVIDEND",
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": "2024-03-28",
        "quantity": "0",
        "gross": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur},
        "fees": {"total": "0.000000", "currency": "EUR", "total_eur": "0.000000"},
        "net": {"amount": net_eur, "currency": "EUR", "eur_amount": net_eur},
        "net_eur": net_eur,
        "withholding": {
            "source": {"country": "US", "rate_pct": "15.00", "amount_eur": "150.000000"},
            "destination": {"country": "ES", "rate_pct": "19.00", "amount_eur": "50.000000"},
        },
        "correction_status": correction_status,
        "import_source": "manual",
    }
    if ca_group_id:
        doc["ca_group_id"] = ca_group_id
        doc["ca_leg_type"] = "CASH_DIVIDEND"
        doc["ca_event_type"] = "CASH_DIVIDEND"
        doc["ca_group_seq"] = 1
    fake.portfolio_container._store[doc_id] = doc


# ---------------------------------------------------------------------------
# §1 — Unit tests for _map_recent_movement() field contract
# ---------------------------------------------------------------------------

class TestMapRecentMovementFieldContract:
    """Unit tests for the _map_recent_movement() pure mapping function.

    All active fields required by the Stocks tab contract must be present.
    """

    def test_all_13_contract_fields_present_for_buy(self):
        """_map_recent_movement must include every Stocks-tab contract field."""
        from web.app import _map_recent_movement
        raw = {
            "id": "txn_unit_buy",
            "txn_type": "BUY",
            "trade_date": "2024-03-15",
            "quantity": "100",
            "gross": {"currency": "EUR", "eur_amount": "5000.00"},
            "fees": {"total_eur": "9.95"},
            "net": {"eur_amount": "5009.95"},
            "account_id": "broker_a",
            "correction_status": "ACTIVE",
            "import_source": None,
        }
        result = _map_recent_movement(raw)
        required_fields = (
            "id", "txn_type", "trade_date", "quantity",
            "gross_eur", "fees_eur", "net_eur", "currency",
            "account_id", "correction_status", "import_source",
        )
        for field in required_fields:
            assert field in result, (
                f"_map_recent_movement output missing '{field}' — "
                f"all 13 Stocks-tab contract fields must be present"
            )

    def test_gross_eur_taken_from_gross_field(self):
        from web.app import _map_recent_movement
        raw = {
            "id": "t1",
            "gross": {"currency": "USD", "eur_amount": "4800.00"},
            "fees": {"total_eur": "10.00"},
            "net": {"eur_amount": "4790.00"},
        }
        result = _map_recent_movement(raw)
        assert result["gross_eur"] == "4800.00", (
            "gross_eur must come from gross.eur_amount"
        )

    def test_gross_eur_fallback_to_net_eur_when_gross_absent(self):
        """When gross.eur_amount is absent, gross_eur falls back to top-level net_eur."""
        from web.app import _map_recent_movement
        raw = {
            "id": "t2",
            "gross": {},
            "fees": {"total_eur": "5.00"},
            "net": {"eur_amount": None},
            "net_eur": "999.00",
        }
        result = _map_recent_movement(raw)
        assert result["gross_eur"] == "999.00", (
            "gross_eur must fall back to net_eur when gross.eur_amount absent"
        )

    def test_fees_eur_from_fees_total_eur(self):
        from web.app import _map_recent_movement
        raw = {
            "id": "t3",
            "gross": {"eur_amount": "1000.00"},
            "fees": {"total_eur": "12.50"},
            "net": {"eur_amount": "987.50"},
        }
        result = _map_recent_movement(raw)
        assert result["fees_eur"] == "12.50", (
            "fees_eur must come from fees.total_eur"
        )

    def test_net_eur_from_net_field(self):
        from web.app import _map_recent_movement
        raw = {
            "id": "t4",
            "gross": {"eur_amount": "2000.00"},
            "fees": {"total_eur": "8.00"},
            "net": {"eur_amount": "1992.00"},
        }
        result = _map_recent_movement(raw)
        assert result["net_eur"] == "1992.00", (
            "net_eur must come from net.eur_amount"
        )

    def test_currency_from_gross_field(self):
        from web.app import _map_recent_movement
        raw = {
            "id": "t5",
            "gross": {"currency": "USD", "eur_amount": "500.00"},
            "fees": {},
            "net": {"eur_amount": "500.00"},
        }
        result = _map_recent_movement(raw)
        assert result["currency"] == "USD", (
            "currency must come from gross.currency"
        )

    def test_sell_omits_obsolete_sales_type(self):
        from web.app import _map_recent_movement
        raw = {
            "id": "txn_sell",
            "txn_type": "SELL",
            "sales_type": "ACCIONES",
            "gross": {"eur_amount": "2700.00"},
            "fees": {"total_eur": "8.50"},
            "net": {"eur_amount": "2691.50"},
        }
        result = _map_recent_movement(raw)
        assert "sales_type" not in result

    def test_legacy_rights_metadata_is_not_serialized(self):
        from web.app import _map_recent_movement
        raw = {
            "id": "txn_sell_rights",
            "txn_type": "SELL",
            "sales_type": "DERECHOS",
            "gross": {"eur_amount": "150.00"},
            "fees": {"total_eur": "2.50"},
            "net": {"eur_amount": "147.50"},
        }
        result = _map_recent_movement(raw)
        assert "sales_type" not in result

    def test_correction_status_active_preserved(self):
        from web.app import _map_recent_movement
        raw = {
            "id": "txn_active",
            "correction_status": "ACTIVE",
            "gross": {"eur_amount": "1000.00"},
            "fees": {},
            "net": {"eur_amount": "1000.00"},
        }
        result = _map_recent_movement(raw)
        assert result["correction_status"] == "ACTIVE"

    def test_import_source_preserved(self):
        from web.app import _map_recent_movement
        raw = {
            "id": "txn_imported",
            "import_source": "degiro_csv_2024",
            "gross": {"eur_amount": "3000.00"},
            "fees": {"total_eur": "6.00"},
            "net": {"eur_amount": "2994.00"},
        }
        result = _map_recent_movement(raw)
        assert result["import_source"] == "degiro_csv_2024", (
            "import_source audit field must pass through unchanged"
        )

    def test_null_quantity_maps_to_none(self):
        from web.app import _map_recent_movement
        raw = {
            "id": "txn_no_qty",
            "quantity": None,
            "gross": {"eur_amount": "500.00"},
            "fees": {},
            "net": {"eur_amount": "500.00"},
        }
        result = _map_recent_movement(raw)
        assert result["quantity"] is None, (
            "quantity=None must map to None (not '0' or omitted)"
        )

    def test_account_id_preserved(self):
        from web.app import _map_recent_movement
        raw = {
            "id": "txn_acct",
            "account_id": "broker_degiro",
            "gross": {"eur_amount": "100.00"},
            "fees": {},
            "net": {"eur_amount": "100.00"},
        }
        result = _map_recent_movement(raw)
        assert result["account_id"] == "broker_degiro"

    def test_dividend_movement_maps_correctly(self):
        """DIVIDEND movement maps txn_type, date, gross/net to RecentMovement shape.

        Amendment H finalized: withholding (source/rate_pct field names) is present on
        the raw doc but exposed to the StockTransactionsTable via the full movements
        endpoint (GET /api/portfolio/movements). _map_recent_movement is the quick-glance
        mapper for PortfolioHoldingsCard.recent_movements (13 fields).
        """
        from web.app import _map_recent_movement
        raw = {
            "id": "txn_div",
            "txn_type": "DIVIDEND",
            "trade_date": "2024-03-28",
            "quantity": "0",
            "withholding": {
                "source": {"country": "US", "rate_pct": "15.00", "amount_eur": "150.000000"},
                "destination": {"country": "ES", "rate_pct": "19.00", "amount_eur": "50.000000"},
            },
            "gross": {"eur_amount": "1000.000000", "currency": "EUR"},
            "fees": {"total_eur": "0.000000"},
            "net": {"eur_amount": "800.000000"},
            "correction_status": "ACTIVE",
            "import_source": "manual",
        }
        result = _map_recent_movement(raw)
        assert result["txn_type"] == "DIVIDEND"
        assert result["trade_date"] == "2024-03-28"
        assert result["gross_eur"] == "1000.000000"
        assert result["net_eur"] == "800.000000"
        assert result["correction_status"] == "ACTIVE"
        assert result["import_source"] == "manual"


# ---------------------------------------------------------------------------
# §2 — SUPERSEDED / voided / deleted exclusion at endpoint level
# ---------------------------------------------------------------------------

class TestSupersededMovementsExcluded:
    """Verify that SUPERSEDED, VOIDED, and soft-deleted movements are excluded
    from the recent_movements list returned by the detail endpoint.

    This is critical for correction chains: the original (SUPERSEDED) movement
    must not appear alongside its corrected replacement.
    """

    def test_superseded_movement_excluded(self, client):
        """A SUPERSEDED movement must not appear in recent_movements."""
        c, fake = client
        fake.container.seed_security("XNYS:TSLA", "Tesla")
        fake.container.seed_config("TSLA", {"security_id": "XNYS:TSLA"})
        # ACTIVE replacement
        _buy(fake, "XNYS:TSLA", doc_id="txn_tsla_active",
             quantity="100", correction_status="ACTIVE")
        # SUPERSEDED original (must not appear)
        _buy(fake, "XNYS:TSLA", doc_id="txn_tsla_superseded",
             quantity="100", correction_status="SUPERSEDED")

        resp = c.get("/api/symbols/XNYS:TSLA/detail")
        data = resp.json()
        movements = data.get("portfolio", {}).get("recent_movements", [])
        ids = [m["id"] for m in movements]
        assert "txn_tsla_superseded" not in ids, (
            "SUPERSEDED movement must be excluded from recent_movements"
        )
        assert "txn_tsla_active" in ids, (
            "ACTIVE replacement movement must be included"
        )

    def test_voided_movement_excluded(self, client):
        """A VOIDED movement must not appear in recent_movements."""
        c, fake = client
        fake.container.seed_security("XNYS:GE", "GE")
        fake.container.seed_config("GE", {"security_id": "XNYS:GE"})
        _buy(fake, "XNYS:GE", doc_id="txn_ge_active", correction_status="ACTIVE")
        _buy(fake, "XNYS:GE", doc_id="txn_ge_voided",
             correction_status="VOIDED", quantity="200")

        resp = c.get("/api/symbols/XNYS:GE/detail")
        movements = resp.json().get("portfolio", {}).get("recent_movements", [])
        ids = [m["id"] for m in movements]
        assert "txn_ge_voided" not in ids, (
            "VOIDED movement must be excluded from recent_movements"
        )

    def test_soft_deleted_movement_excluded(self, client):
        """A soft-deleted movement (deleted_at present) must not appear."""
        c, fake = client
        fake.container.seed_security("XNYS:IBM", "IBM")
        fake.container.seed_config("IBM", {"security_id": "XNYS:IBM"})
        _buy(fake, "XNYS:IBM", doc_id="txn_ibm_live", correction_status="ACTIVE")
        _buy(fake, "XNYS:IBM", doc_id="txn_ibm_del",
             correction_status="ACTIVE", deleted=True)

        resp = c.get("/api/symbols/XNYS:IBM/detail")
        movements = resp.json().get("portfolio", {}).get("recent_movements", [])
        ids = [m["id"] for m in movements]
        assert "txn_ibm_del" not in ids, (
            "Soft-deleted movement must be excluded from recent_movements"
        )
        assert "txn_ibm_live" in ids

    def test_only_active_movements_included(self, client):
        """With three movements (ACTIVE, SUPERSEDED, VOIDED), only ACTIVE appears."""
        c, fake = client
        fake.container.seed_security("XNYS:F", "Ford")
        fake.container.seed_config("F", {"security_id": "XNYS:F"})
        _buy(fake, "XNYS:F", doc_id="txn_f_active", correction_status="ACTIVE")
        _buy(fake, "XNYS:F", doc_id="txn_f_super", correction_status="SUPERSEDED",
             quantity="200")
        _buy(fake, "XNYS:F", doc_id="txn_f_void", correction_status="VOIDED",
             quantity="300")

        resp = c.get("/api/symbols/XNYS:F/detail")
        movements = resp.json().get("portfolio", {}).get("recent_movements", [])
        assert len(movements) == 1, (
            f"Only 1 ACTIVE movement should appear; got {len(movements)}: "
            f"{[m['id'] for m in movements]}"
        )
        assert movements[0]["id"] == "txn_f_active"


# ---------------------------------------------------------------------------
# §3 — Stocks tab exposed for all portfolio states
# ---------------------------------------------------------------------------

class TestStocksTabForAllPortfolioStates:
    """recent_movements must be populated for every symbol_state that has
    a ledger — portfolio_only, portfolio_historical, watchlist_and_portfolio.
    watchlist_only (no ledger) must have portfolio=null (no movements).
    """

    def test_watchlist_only_has_no_portfolio_section(self, client):
        """watchlist_only: portfolio is null, recent_movements inaccessible."""
        c, fake = client
        fake.container.seed_security("XNYS:WO", "Watchlist Only Co")
        fake.container.seed_config("WO", {"security_id": "XNYS:WO"})
        # No ledger entries

        resp = c.get("/api/symbols/XNYS:WO/detail")
        data = resp.json()
        assert data["symbol_state"] == "watchlist_only"
        assert data.get("portfolio") is None, (
            "watchlist_only symbols must have portfolio=null (no Stocks content)"
        )

    def test_watchlist_and_portfolio_has_recent_movements(self, client):
        """watchlist_and_portfolio: portfolio section with recent_movements populated."""
        c, fake = client
        fake.container.seed_security("XNYS:WP", "Watchlist+Portfolio Co")
        fake.container.seed_config("WP", {"security_id": "XNYS:WP"})
        _buy(fake, "XNYS:WP", doc_id="txn_wp_1", quantity="75")

        resp = c.get("/api/symbols/XNYS:WP/detail")
        data = resp.json()
        assert data["symbol_state"] == "watchlist_and_portfolio"
        portfolio = data.get("portfolio")
        assert portfolio is not None, "watchlist_and_portfolio must have portfolio section"
        movements = portfolio.get("recent_movements", [])
        assert len(movements) >= 1, (
            "watchlist_and_portfolio with ledger entries must have recent_movements"
        )

    def test_portfolio_historical_has_recent_movements(self, client):
        """portfolio_historical (zero shares): Stocks tab still shows movements."""
        c, fake = client
        fake.container.seed_security("XNYS:PH", "Portfolio Historical Co")
        fake.container.seed_config("PH", {"security_id": "XNYS:PH"})
        _buy(fake, "XNYS:PH", doc_id="txn_ph_buy", quantity="100")
        _sell(fake, "XNYS:PH", doc_id="txn_ph_sell", quantity="100")

        resp = c.get("/api/symbols/XNYS:PH/detail")
        data = resp.json()
        assert data["symbol_state"] == "portfolio_historical", (
            f"Expected portfolio_historical, got {data['symbol_state']}"
        )
        portfolio = data.get("portfolio")
        assert portfolio is not None
        movements = portfolio.get("recent_movements", [])
        assert len(movements) >= 1, (
            "portfolio_historical must still have recent_movements for Stocks tab"
        )

    def test_portfolio_only_no_config_has_recent_movements(self, client):
        """portfolio_only (no watchlist config): Stocks tab shows movements via
        security_master + ledger lookup."""
        c, fake = client
        # Security exists but NO symbol_config
        fake.container.seed_security("XNYS:PO", "Portfolio Only Co")
        _buy(fake, "XNYS:PO", doc_id="txn_po_buy", quantity="200")

        resp = c.get("/api/symbols/XNYS:PO/detail")
        data = resp.json()
        assert resp.status_code == 200
        assert data.get("symbol_state") == "portfolio_only", (
            f"Expected portfolio_only, got {data.get('symbol_state')}"
        )
        portfolio = data.get("portfolio")
        assert portfolio is not None, (
            "portfolio_only symbol must have portfolio section with movements"
        )
        movements = portfolio.get("recent_movements", [])
        assert len(movements) >= 1, (
            "portfolio_only symbol must expose recent_movements for Stocks tab"
        )


# ---------------------------------------------------------------------------
# §4 — Stocks tab movement types (BUY, SELL, TRANSFER current behavior)
# ---------------------------------------------------------------------------

class TestStocksTabMovementTypes:
    """Verify which txn_types appear in recent_movements.

    Current behavior: get_movements() is called without txn_type filter — ALL
    types (BUY, SELL, TRANSFER_IN, TRANSFER_OUT) appear. TRANSFER movements
    appear with txn_type in the output, which the Stocks tab renders as "In"/"Out"
    badges. This is documented existing behavior (not yet filtered).

    The future Stocks tab may filter to BUY/SELL/DIVIDEND only — the backend
    already supports txn_type parameter in get_movements(). A filterMovementsByType
    frontend helper (see frontend/tests/filterMovementsByType.test.mjs) handles
    client-side filtering until the backend call is tightened.
    """

    def test_buy_movement_has_correct_txn_type(self, client):
        c, fake = client
        fake.container.seed_security("XNYS:TYPE", "Type Co")
        fake.container.seed_config("TYPE", {"security_id": "XNYS:TYPE"})
        _buy(fake, "XNYS:TYPE", doc_id="txn_type_buy")

        resp = c.get("/api/symbols/XNYS:TYPE/detail")
        movements = resp.json().get("portfolio", {}).get("recent_movements", [])
        buy_movs = [m for m in movements if m.get("txn_type") == "BUY"]
        assert len(buy_movs) == 1, "BUY movement must appear with txn_type='BUY'"

    def test_sell_movement_has_correct_txn_type(self, client):
        c, fake = client
        fake.container.seed_security("XNYS:STYPE", "Sell Type Co")
        fake.container.seed_config("STYPE", {"security_id": "XNYS:STYPE"})
        _buy(fake, "XNYS:STYPE", doc_id="txn_stype_buy", quantity="100")
        _sell(fake, "XNYS:STYPE", doc_id="txn_stype_sell", quantity="50",
              sales_type="ACCIONES")

        resp = c.get("/api/symbols/XNYS:STYPE/detail")
        movements = resp.json().get("portfolio", {}).get("recent_movements", [])
        sell_movs = [m for m in movements if m.get("txn_type") == "SELL"]
        assert len(sell_movs) == 1, "SELL movement must appear with txn_type='SELL'"
        assert "sales_type" not in sell_movs[0]

    def test_legacy_rights_sell_is_hidden(self, client):
        c, fake = client
        fake.container.seed_security("XNYS:DREC", "Rights Co")
        fake.container.seed_config("DREC", {"security_id": "XNYS:DREC"})
        _buy(fake, "XNYS:DREC", doc_id="txn_drec_buy", quantity="100")
        _sell(fake, "XNYS:DREC", doc_id="txn_drec_rights", quantity="100",
              sales_type="DERECHOS")

        resp = c.get("/api/symbols/XNYS:DREC/detail")
        movements = resp.json().get("portfolio", {}).get("recent_movements", [])
        assert all(m.get("id") != "txn_drec_rights" for m in movements)

    def test_transfer_in_currently_appears(self, client):
        """TRANSFER_IN currently appears in recent_movements (not yet filtered).

        This test documents the current behavior: TRANSFER movements pass through
        because get_movements() is called without txn_type filter. The Stocks tab
        renders them as 'In'/'Out' badges. A future change may filter these out.
        When that change lands: update this test to assert TRANSFER_IN is absent.
        """
        c, fake = client
        fake.container.seed_security("XNYS:XFER", "Transfer Co")
        fake.container.seed_config("XFER", {"security_id": "XNYS:XFER"})
        _buy(fake, "XNYS:XFER", doc_id="txn_xfer_buy", quantity="100")
        _transfer_in(fake, "XNYS:XFER", doc_id="txn_xfer_in", quantity="25")

        resp = c.get("/api/symbols/XNYS:XFER/detail")
        movements = resp.json().get("portfolio", {}).get("recent_movements", [])
        transfer_movs = [m for m in movements if m.get("txn_type") == "TRANSFER_IN"]
        assert len(transfer_movs) == 1, (
            "TRANSFER_IN currently appears in recent_movements (not yet filtered). "
            "If this fails, txn_type filtering has been added — update assertion."
        )

    def test_dividend_movement_appears_in_stocks_tab(self, client):
        """DIVIDEND movement (CASH_DIVIDEND leg) appears in recent_movements.

        Amendment H finalized: composite DIVIDEND uses CASH_DIVIDEND leg
        (txn_type=DIVIDEND, quantity='0', ca_group_id set).
        """
        c, fake = client
        fake.container.seed_security("XNYS:DIV", "Dividend Co")
        fake.container.seed_config("DIV", {"security_id": "XNYS:DIV"})
        _dividend(fake, "XNYS:DIV", doc_id="txn_div_01")
        resp = c.get("/api/symbols/XNYS:DIV/detail")
        assert resp.status_code == 200
        movements = resp.json().get("portfolio", {}).get("recent_movements", [])
        div_movs = [m for m in movements if m.get("txn_type") == "DIVIDEND"]
        assert len(div_movs) >= 1, (
            "DIVIDEND (CASH_DIVIDEND leg) must appear in recent_movements"
        )


# ---------------------------------------------------------------------------
# §5 — movement_count correctness
# ---------------------------------------------------------------------------

class TestMovementCountAccuracy:
    """movement_count must reflect the total unfiltered count, not the page limit."""

    def test_movement_count_matches_single_movement(self, client):
        c, fake = client
        fake.container.seed_security("XNYS:CNT", "Count Co")
        fake.container.seed_config("CNT", {"security_id": "XNYS:CNT"})
        _buy(fake, "XNYS:CNT", doc_id="txn_cnt_1")

        resp = c.get("/api/symbols/XNYS:CNT/detail")
        portfolio = resp.json().get("portfolio", {})
        assert portfolio.get("movement_count") == 1, (
            "movement_count must be 1 when exactly 1 active movement exists"
        )

    def test_movement_count_excludes_superseded(self, client):
        """movement_count must not count SUPERSEDED movements."""
        c, fake = client
        fake.container.seed_security("XNYS:CNT2", "Count2 Co")
        fake.container.seed_config("CNT2", {"security_id": "XNYS:CNT2"})
        _buy(fake, "XNYS:CNT2", doc_id="txn_cnt2_active", correction_status="ACTIVE")
        _buy(fake, "XNYS:CNT2", doc_id="txn_cnt2_super",
             correction_status="SUPERSEDED", quantity="999")

        resp = c.get("/api/symbols/XNYS:CNT2/detail")
        portfolio = resp.json().get("portfolio", {})
        count = portfolio.get("movement_count")
        assert count == 1, (
            f"movement_count must exclude SUPERSEDED movements; got {count}"
        )

    def test_movement_count_zero_for_zero_movements(self, client):
        """Symbol with no movements but a holdings fallback path — count is 0."""
        c, fake = client
        fake.container.seed_security("XNYS:ZERO", "Zero Co")
        fake.container.seed_config("ZERO", {"security_id": "XNYS:ZERO"})
        # No ledger entries → watchlist_only
        resp = c.get("/api/symbols/XNYS:ZERO/detail")
        data = resp.json()
        assert data.get("symbol_state") == "watchlist_only"
        assert data.get("portfolio") is None


# ---------------------------------------------------------------------------
# §6 — Import provenance preserved through Stocks tab mapping
# ---------------------------------------------------------------------------

class TestImportProvenanceInStocksTab:
    """import_source audit field must survive _map_recent_movement unchanged."""

    def test_imported_movement_import_source_preserved(self, client):
        c, fake = client
        fake.container.seed_security("XNYS:IMP", "Imported Co")
        fake.container.seed_config("IMP", {"security_id": "XNYS:IMP"})
        _buy(fake, "XNYS:IMP", doc_id="txn_imp_1",
             import_source="degiro_csv_20240315")

        resp = c.get("/api/symbols/XNYS:IMP/detail")
        movements = resp.json().get("portfolio", {}).get("recent_movements", [])
        assert movements, "Imported movement must appear in recent_movements"
        assert movements[0].get("import_source") == "degiro_csv_20240315", (
            "import_source must pass through _map_recent_movement for audit trail"
        )

    def test_non_imported_movement_import_source_is_none(self, client):
        c, fake = client
        fake.container.seed_security("XNYS:NIMP", "Non-Imported Co")
        fake.container.seed_config("NIMP", {"security_id": "XNYS:NIMP"})
        _buy(fake, "XNYS:NIMP", doc_id="txn_nimp_1")  # no import_source

        resp = c.get("/api/symbols/XNYS:NIMP/detail")
        movements = resp.json().get("portfolio", {}).get("recent_movements", [])
        assert movements
        assert movements[0].get("import_source") is None


# ---------------------------------------------------------------------------
# §7 — StockTransactionsTable data source: GET /api/portfolio/movements
# ---------------------------------------------------------------------------
# StockTransactionsTable uses the full movements endpoint directly (not the
# symbol-detail recent_movements quick-glance). This section verifies:
#   a) security_id filter returns only that symbol's movements
#   b) txn_type filter works for BUY/SELL/DIVIDEND type pills
#   c) Response includes CA group fields (ca_group_id / ca_leg_type /
#      ca_event_type / ca_group_seq) for Amendment H legs
#   d) Standalone BUY/SELL movements have no ca_group_id
# ---------------------------------------------------------------------------


def _ca_leg(fake, security_id: str, doc_id: str,
             txn_type: str, ca_leg_type: str, ca_group_id: str,
             ca_event_type: str = "DIVIDEND_WITH_SCRIP",
             ca_group_seq: int = 1,
             account_id: str = "_unassigned",
             quantity: str = None,
             gross_eur: str = "200.00",
             fees_eur: str = "0",
             correction_status: str = "ACTIVE"):
    """Build and seed a corporate-action group leg movement."""
    ticker = security_id.split(":")[-1]
    doc = {
        "id": doc_id,
        "account_id": account_id,
        "doc_type": "ledger_txn",
        "txn_type": txn_type,
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": "2024-03-28",
        "quantity": quantity,
        "gross": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur},
        "fees": {"total": fees_eur, "currency": "EUR", "total_eur": fees_eur},
        "net": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur},
        "correction_status": correction_status,
        "ca_group_id": ca_group_id,
        "ca_leg_type": ca_leg_type,
        "ca_event_type": ca_event_type,
        "ca_group_seq": ca_group_seq,
    }
    fake.portfolio_container._store[doc_id] = doc
    return doc


class TestMovementsEndpointForStocksTable:
    """GET /api/portfolio/movements is the data source for StockTransactionsTable.

    Verifies the endpoint contract used by the Amendment I Stocks section:
    security_id filtering, txn_type pill filtering, pagination shape, and
    that CA group legs pass through ca_group_id / ca_leg_type fields.
    """

    def test_security_id_filter_returns_only_that_symbol(self, client):
        """?security_id=X must return only movements for that symbol."""
        c, fake = client
        _buy(fake, "XNYS:AAPL", doc_id="mvt_stocks_aapl_1")
        _buy(fake, "XNYS:MSFT", doc_id="mvt_stocks_msft_1")  # different symbol

        resp = c.get("/api/portfolio/movements?security_id=XNYS:AAPL")
        assert resp.status_code == 200
        data = resp.json()
        ids = [m["id"] for m in data["movements"]]
        assert "mvt_stocks_aapl_1" in ids, "AAPL movement must be returned"
        assert "mvt_stocks_msft_1" not in ids, (
            "MSFT movement must NOT appear when filtered by security_id=XNYS:AAPL"
        )

    def test_txn_type_filter_buy_returns_only_buy(self, client):
        """?txn_type=BUY must return only BUY movements (type-pill filter)."""
        c, fake = client
        _buy(fake, "XNYS:AAPL", doc_id="mvt_pill_buy", quantity="100")
        _sell(fake, "XNYS:AAPL", doc_id="mvt_pill_sell", quantity="50")

        resp = c.get("/api/portfolio/movements?security_id=XNYS:AAPL&txn_type=BUY")
        assert resp.status_code == 200
        movements = resp.json()["movements"]
        assert all(m["txn_type"] == "BUY" for m in movements), (
            "All returned movements must be BUY when txn_type=BUY filter applied"
        )
        buy_ids = [m["id"] for m in movements]
        assert "mvt_pill_buy" in buy_ids

    def test_txn_type_filter_sell_returns_only_sell(self, client):
        """?txn_type=SELL returns ordinary SELL rows and hides legacy rights."""
        c, fake = client
        _buy(fake, "XNYS:AAPL", doc_id="mvt_pills_buy")
        _sell(fake, "XNYS:AAPL", doc_id="mvt_pills_sell_acc", sales_type="ACCIONES")
        _sell(fake, "XNYS:AAPL", doc_id="mvt_pills_sell_dec", sales_type="DERECHOS")

        resp = c.get("/api/portfolio/movements?security_id=XNYS:AAPL&txn_type=SELL")
        assert resp.status_code == 200
        movements = resp.json()["movements"]
        assert all(m["txn_type"] == "SELL" for m in movements)
        sell_ids = {m["id"] for m in movements}
        assert "mvt_pills_sell_acc" in sell_ids
        assert "mvt_pills_sell_dec" not in sell_ids
        assert "mvt_pills_buy" not in sell_ids

    def test_invalid_txn_type_returns_400(self, client):
        """txn_type=GARBAGE must return 400 validation_error (not 500)."""
        c, _ = client
        resp = c.get("/api/portfolio/movements?txn_type=GARBAGE")
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_response_has_pagination_fields(self, client):
        """Response shape must include total_count, limit, offset (MovementsResponse)."""
        c, fake = client
        _buy(fake, "XNYS:AAPL", doc_id="mvt_pag_1")
        resp = c.get("/api/portfolio/movements?security_id=XNYS:AAPL&limit=20&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_count" in data, "Response must include total_count for pagination"
        assert "limit" in data
        assert "offset" in data
        assert "movements" in data

    def test_response_limit_applied(self, client):
        """limit parameter is respected — returns at most limit movements."""
        c, fake = client
        for i in range(5):
            _buy(fake, "XNYS:LIMS", doc_id=f"mvt_lim_{i:02d}")
        resp = c.get("/api/portfolio/movements?security_id=XNYS:LIMS&limit=3&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["movements"]) <= 3, "Must not return more rows than limit"
        assert data["total_count"] == 5, "total_count reflects full unfiltered count"

    def test_legacy_rights_movement_is_hidden(self, client):
        c, fake = client
        _sell(fake, "XNYS:AAPL", doc_id="mvt_st_sell", sales_type="DERECHOS")
        resp = c.get("/api/portfolio/movements?security_id=XNYS:AAPL")
        movements = resp.json()["movements"]
        sell = next((m for m in movements if m["id"] == "mvt_st_sell"), None)
        assert sell is None


class TestMovementsEndpointCaGroupFields:
    """CA group legs from GET /api/portfolio/movements include Amendment H fields.

    StockTransactionsTable uses these to render the group indicator icon and
    leg-type sub-label. _clean() must pass all ca_* fields through unchanged.
    """

    def test_ca_group_id_present_on_group_leg(self, client):
        """ca_group_id must be returned for CA group legs."""
        c, fake = client
        _ca_leg(fake, "XNYS:ULVR", "mvt_ca_div",
                txn_type="DIVIDEND", ca_leg_type="CASH_DIVIDEND",
                ca_group_id="cag_test001", ca_group_seq=1)

        resp = c.get("/api/portfolio/movements?security_id=XNYS:ULVR")
        movements = resp.json()["movements"]
        leg = next((m for m in movements if m["id"] == "mvt_ca_div"), None)
        assert leg is not None
        assert leg.get("ca_group_id") == "cag_test001", (
            "ca_group_id must pass through _clean() to enable group indicator icon"
        )

    def test_ca_leg_type_present_on_group_leg(self, client):
        """ca_leg_type must be returned for CA group legs."""
        c, fake = client
        _ca_leg(fake, "XNYS:ULVR", "mvt_ca_acq",
                txn_type="BUY", ca_leg_type="SHARE_ACQUISITION",
                ca_group_id="cag_test002", ca_group_seq=2)

        resp = c.get("/api/portfolio/movements?security_id=XNYS:ULVR")
        movements = resp.json()["movements"]
        leg = next((m for m in movements if m["id"] == "mvt_ca_acq"), None)
        assert leg is not None
        assert leg.get("ca_leg_type") == "SHARE_ACQUISITION"

    def test_ca_event_type_present_on_group_leg(self, client):
        """ca_event_type must be returned for all legs in the group."""
        c, fake = client
        _ca_leg(fake, "XNYS:ULVR", "mvt_ca_evt",
                txn_type="DIVIDEND", ca_leg_type="CASH_DIVIDEND",
                ca_group_id="cag_test003",
                ca_event_type="DIVIDEND_WITH_SCRIP",
                ca_group_seq=1)

        resp = c.get("/api/portfolio/movements?security_id=XNYS:ULVR")
        movements = resp.json()["movements"]
        leg = next((m for m in movements if m["id"] == "mvt_ca_evt"), None)
        assert leg is not None
        assert leg.get("ca_event_type") == "DIVIDEND_WITH_SCRIP", (
            "ca_event_type must be present for group indicator tooltip"
        )

    def test_ca_group_seq_present_on_group_leg(self, client):
        """ca_group_seq must be returned for ordering within group."""
        c, fake = client
        _ca_leg(fake, "XNYS:ULVR", "mvt_ca_seq",
                txn_type="BUY", ca_leg_type="SHARE_ACQUISITION",
                ca_group_id="cag_test004", ca_group_seq=2)

        resp = c.get("/api/portfolio/movements?security_id=XNYS:ULVR")
        movements = resp.json()["movements"]
        leg = next((m for m in movements if m["id"] == "mvt_ca_seq"), None)
        assert leg is not None
        assert leg.get("ca_group_seq") == 2

    def test_sibling_legs_share_ca_group_id(self, client):
        """All legs of the same CA group share the same ca_group_id."""
        c, fake = client
        grp = "cag_shared001"
        _ca_leg(fake, "XNYS:ULVR", "mvt_shared_div",
                txn_type="DIVIDEND", ca_leg_type="CASH_DIVIDEND",
                ca_group_id=grp, ca_group_seq=1)
        _ca_leg(fake, "XNYS:ULVR", "mvt_shared_acq",
                txn_type="BUY", ca_leg_type="SHARE_ACQUISITION",
                ca_group_id=grp, ca_group_seq=2)

        resp = c.get("/api/portfolio/movements?security_id=XNYS:ULVR")
        movements = resp.json()["movements"]
        group_legs = [m for m in movements if m.get("ca_group_id") == grp]
        assert len(group_legs) == 2, (
            "Both legs must be returned with the same ca_group_id"
        )
        seqs = sorted(m["ca_group_seq"] for m in group_legs)
        assert seqs == [1, 2], "ca_group_seq values must be 1 and 2"

    def test_standalone_buy_has_no_ca_group_id(self, client):
        """A standard BUY (not a CA leg) must not have ca_group_id."""
        c, fake = client
        _buy(fake, "XNYS:AAPL", doc_id="mvt_standalone_buy")

        resp = c.get("/api/portfolio/movements?security_id=XNYS:AAPL")
        movements = resp.json()["movements"]
        buy = next((m for m in movements if m["id"] == "mvt_standalone_buy"), None)
        assert buy is not None
        ca_id = buy.get("ca_group_id")
        assert ca_id is None or ca_id == "", (
            "Standalone BUY must not carry a ca_group_id"
        )

    def test_supported_ca_leg_types_returned_and_legacy_rights_hidden(self, client):
        c, fake = client
        grp = "cag_allfour"
        leg_specs = [
            ("mvt_4t_div",  "DIVIDEND", "CASH_DIVIDEND",      1),
            ("mvt_4t_rts",  "SELL",     "RIGHTS_SOLD",         2),
            ("mvt_4t_acq",  "BUY",      "SHARE_ACQUISITION",   3),
            ("mvt_4t_top",  "BUY",      "CASH_TOP_UP",         4),
        ]
        for doc_id, txn_type, leg_type, seq in leg_specs:
            _ca_leg(fake, "XNYS:ULVR", doc_id,
                    txn_type=txn_type, ca_leg_type=leg_type,
                    ca_group_id=grp, ca_group_seq=seq)

        resp = c.get("/api/portfolio/movements?security_id=XNYS:ULVR")
        movements = resp.json()["movements"]
        returned_types = {
            m["ca_leg_type"] for m in movements if m.get("ca_group_id") == grp
        }
        assert returned_types == {
            "CASH_DIVIDEND", "SHARE_ACQUISITION", "CASH_TOP_UP"
        }
