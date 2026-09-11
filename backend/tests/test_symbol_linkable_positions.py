"""Tests for GET /api/symbols/{symbol}/positions/linkable.

Covers the eligibility rules used to populate the "link this movement to a
position" dropdown (see .squad/designs/option-movements-design.md addendum):

- `CALL_SELL` / `PUT_SELL`: same option type, no existing linked opening-sell
  movement yet (position may be open or already closed).
- `CALL_BUY` / `PUT_BUY`: same option type, position was closed via a buyback
  (`status == "rolled"` or `close_reason == "manual"`), no existing linked
  closing-buy movement yet.
"""

import pytest
from fastapi.testclient import TestClient

from tests.conftest_portfolio_p2 import FakeCosmos
from web.app import app


def _symbol_doc():
    return {
        "symbol": "AAPL",
        "security_id": "XNAS:AAPL",
        "positions": [
            # Open call, never linked to an opening sell -> eligible for CALL_SELL.
            {
                "position_id": "call-open-unlinked",
                "type": "call",
                "strike": 150,
                "expiration": "2026-03-20",
                "opened_at": "2026-01-01T00:00:00Z",
                "status": "active",
            },
            # Call already linked to CALL_SELL -> not eligible for CALL_SELL again.
            {
                "position_id": "call-open-linked",
                "type": "call",
                "strike": 155,
                "expiration": "2026-03-20",
                "opened_at": "2026-01-02T00:00:00Z",
                "status": "active",
            },
            # Manually closed call with a buyback still missing its CALL_BUY link
            # -> eligible for CALL_BUY (and also has an opening sell already).
            {
                "position_id": "call-closed-needs-buy",
                "type": "call",
                "strike": 160,
                "expiration": "2026-02-20",
                "opened_at": "2025-12-01T00:00:00Z",
                "closed_at": "2026-01-15T00:00:00Z",
                "status": "closed",
                "close_reason": "manual",
            },
            # Rolled put missing its PUT_BUY link -> eligible for PUT_BUY.
            {
                "position_id": "put-rolled-needs-buy",
                "type": "put",
                "strike": 90,
                "expiration": "2026-02-09",
                "opened_at": "2026-01-05T00:00:00Z",
                "closed_at": "2026-01-25T00:00:00Z",
                "status": "rolled",
            },
            # Assigned put -> never needs a closing buy, so NOT eligible for PUT_BUY.
            {
                "position_id": "put-assigned-no-buyback",
                "type": "put",
                "strike": 95,
                "expiration": "2026-02-04",
                "opened_at": "2026-01-06T00:00:00Z",
                "closed_at": "2026-01-30T00:00:00Z",
                "status": "closed",
                "close_reason": "assigned",
            },
        ],
    }


def _seed_security(fake: FakeCosmos, security_id: str, ticker: str):
    fake.container.create_item(
        {
            "id": f"security_master::{security_id}",
            "doc_type": "security_master",
            "security_id": security_id,
            "ticker": ticker,
            "company_name": ticker,
            "exchange_mic": security_id.split(":")[0],
            "listing_currency": "USD",
            "status": "ACTIVE",
        }
    )


def _option_movement(*, id_, txn_type, position_id, security_id="XNAS:AAPL", account_id="acct-1"):
    return {
        "id": id_,
        "doc_type": "ledger_txn",
        "txn_type": txn_type,
        "security_id": security_id,
        "account_id": account_id,
        "trade_date": "2026-01-10",
        "quantity": "0",
        "option_position_id": position_id,
        "gross": {"amount": 100.0, "currency": "USD", "eur_amount": 92.0},
        "net": {"amount": 95.0, "eur_amount": 87.0},
        "fees": {"total": 5.0, "total_eur": 4.6},
    }


class _FakeLinkableCosmos(FakeCosmos):
    def __init__(self, symbol_doc):
        super().__init__()
        self._symbol_doc = symbol_doc

    def get_symbol(self, symbol):
        if symbol.upper() == self._symbol_doc["symbol"]:
            return self._symbol_doc
        return None

    def get_all_symbols(self):
        return [self._symbol_doc]

    def list_symbols(self):
        return [self._symbol_doc]


@pytest.fixture
def linkable_client():
    original_cosmos = getattr(app.state, "cosmos", None)
    original_error = getattr(app.state, "cosmos_error", None)
    fake = _FakeLinkableCosmos(_symbol_doc())
    _seed_security(fake, "XNAS:AAPL", "AAPL")
    fake.portfolio_container._store["call-sell-linked"] = _option_movement(
        id_="call-sell-linked", txn_type="CALL_SELL", position_id="call-open-linked",
    )
    fake.portfolio_container._store["call-sell-for-closed"] = _option_movement(
        id_="call-sell-for-closed", txn_type="CALL_SELL", position_id="call-closed-needs-buy",
    )
    fake.portfolio_container._store["put-sell-for-rolled"] = _option_movement(
        id_="put-sell-for-rolled", txn_type="PUT_SELL", position_id="put-rolled-needs-buy",
    )
    fake.portfolio_container._store["put-sell-for-assigned"] = _option_movement(
        id_="put-sell-for-assigned", txn_type="PUT_SELL", position_id="put-assigned-no-buyback",
    )

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            app.state.cosmos = fake
            app.state.cosmos_error = None
            yield client
    finally:
        app.state.cosmos = original_cosmos
        app.state.cosmos_error = original_error


def test_rejects_invalid_txn_type(linkable_client):
    response = linkable_client.get("/api/symbols/AAPL/positions/linkable", params={"txn_type": "DIVIDEND"})
    assert response.status_code == 400


def test_call_sell_returns_only_unlinked_open_positions_of_matching_type(linkable_client):
    response = linkable_client.get("/api/symbols/AAPL/positions/linkable", params={"txn_type": "CALL_SELL"})
    assert response.status_code == 200
    position_ids = {p["position_id"] for p in response.json()["positions"]}
    assert position_ids == {"call-open-unlinked"}


def test_call_buy_returns_closed_positions_with_buyback_missing_link(linkable_client):
    response = linkable_client.get("/api/symbols/AAPL/positions/linkable", params={"txn_type": "CALL_BUY"})
    assert response.status_code == 200
    position_ids = {p["position_id"] for p in response.json()["positions"]}
    assert position_ids == {"call-closed-needs-buy"}


def test_put_buy_includes_rolled_but_excludes_assigned_without_buyback(linkable_client):
    response = linkable_client.get("/api/symbols/AAPL/positions/linkable", params={"txn_type": "PUT_BUY"})
    assert response.status_code == 200
    position_ids = {p["position_id"] for p in response.json()["positions"]}
    assert position_ids == {"put-rolled-needs-buy"}


def test_put_sell_returns_unlinked_put_positions(linkable_client):
    response = linkable_client.get("/api/symbols/AAPL/positions/linkable", params={"txn_type": "PUT_SELL"})
    assert response.status_code == 200
    position_ids = {p["position_id"] for p in response.json()["positions"]}
    # put-rolled-needs-buy and put-assigned-no-buyback both already have a
    # linked PUT_SELL in the fixture, so neither is eligible here.
    assert position_ids == set()


def test_unknown_symbol_returns_empty_list(linkable_client):
    response = linkable_client.get("/api/symbols/MSFT/positions/linkable", params={"txn_type": "CALL_SELL"})
    assert response.status_code == 200
    assert response.json()["positions"] == []
