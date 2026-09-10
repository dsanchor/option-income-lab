"""Phase 2 regression tests — Account CRUD.

Covers:
- GET /api/portfolio/accounts — list accounts
- POST /api/portfolio/accounts — create with name + broker
- GET /api/portfolio/accounts/{account_id} — get one
- DELETE /api/portfolio/accounts/{account_id} — delete with hard-block

Actual broker enum (from implementation): {fidelity, heytrade, ing, interactive_brokers, other}
account_id is server-generated: acct_{slugify(broker)}_{slugify(name)}
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError

from tests.conftest_portfolio_p2 import FakeCosmos


@pytest.fixture
def client():
    from web.app import app
    fake = FakeCosmos()
    with TestClient(app) as c:
        app.state.cosmos = fake
        app.state.cosmos_error = None
        yield c, fake


def _create(c, broker="heytrade", name="Main Account", currency="EUR", description=None):
    body = {"broker": broker, "name": name, "currency": currency}
    if description:
        body["description"] = description
    return c.post("/api/portfolio/accounts", json=body)


def _seed_movement(fake, account_id):
    fake.portfolio_container._store[f"mvt_{account_id}_001"] = {
        "id": f"mvt_{account_id}_001",
        "doc_type": "ledger_txn",
        "account_id": account_id,
        "txn_type": "BUY",
        "correction_status": "ACTIVE",
        "security_id": "XNYS:AAPL",
    }


# ---------------------------------------------------------------------------
# List accounts
# ---------------------------------------------------------------------------

class TestAccountList:
    def test_list_empty_200(self, client):
        c, _ = client
        resp = c.get("/api/portfolio/accounts")
        assert resp.status_code == 200
        assert "accounts" in resp.json()
        assert isinstance(resp.json()["accounts"], list)

    def test_list_includes_created(self, client):
        c, _ = client
        _create(c, broker="heytrade", name="HT Main")
        resp = c.get("/api/portfolio/accounts")
        assert resp.status_code == 200
        ids = [a["account_id"] for a in resp.json()["accounts"]]
        assert "acct_heytrade_ht_main" in ids


# ---------------------------------------------------------------------------
# Create account
# ---------------------------------------------------------------------------

class TestAccountCreate:
    def test_create_201(self, client):
        c, _ = client
        resp = _create(c, broker="heytrade", name="Primary")
        assert resp.status_code == 201
        doc = resp.json()
        assert doc["broker"] == "heytrade"
        assert doc["name"] == "Primary"
        assert "account_id" in doc
        assert "created_at" in doc

    def test_create_id_is_stable_slug(self, client):
        c, _ = client
        resp = _create(c, broker="heytrade", name="My Account")
        assert resp.status_code == 201
        assert resp.json()["account_id"] == "acct_heytrade_my_account"

    def test_create_all_valid_brokers(self, client):
        c, _ = client
        for broker, suffix in [
            ("fidelity", "a"), ("heytrade", "b"), ("ing", "c"),
            ("interactive_brokers", "d"), ("other", "e"),
        ]:
            resp = _create(c, broker=broker, name=f"Account {suffix}")
            assert resp.status_code == 201, f"Broker {broker!r} should be accepted"

    def test_create_invalid_broker_400(self, client):
        c, _ = client
        resp = _create(c, broker="robinhood", name="Test")
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_create_ibkr_rejected_400(self, client):
        """'ibkr' is NOT a valid broker; correct value is 'interactive_brokers'."""
        c, _ = client
        resp = _create(c, broker="ibkr", name="Test")
        assert resp.status_code == 400

    def test_create_missing_name_400(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/accounts", json={"broker": "heytrade"})
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_create_missing_broker_400(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/accounts", json={"name": "Test Account"})
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_create_duplicate_409(self, client):
        """Same broker + name produces same slug → 409 conflict."""
        c, _ = client
        _create(c, broker="heytrade", name="Dup")
        resp = _create(c, broker="heytrade", name="Dup")
        assert resp.status_code == 409
        assert resp.json()["error"] == "conflict"

    def test_create_default_currency_eur(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/accounts", json={"broker": "heytrade", "name": "No Currency"})
        assert resp.status_code == 201
        assert resp.json()["currency"] == "EUR"

    def test_create_custom_currency(self, client):
        c, _ = client
        resp = _create(c, broker="heytrade", name="USD Account", currency="usd")
        assert resp.status_code == 201
        assert resp.json()["currency"] == "USD"  # normalised to uppercase

    def test_create_with_description(self, client):
        c, _ = client
        resp = _create(c, broker="other", name="Custom", description="My custom broker")
        assert resp.status_code == 201
        assert resp.json().get("description") == "My custom broker"


# ---------------------------------------------------------------------------
# Get account
# ---------------------------------------------------------------------------

class TestAccountGet:
    def test_get_200(self, client):
        c, _ = client
        _create(c, broker="heytrade", name="Fetch Me")
        resp = c.get("/api/portfolio/accounts/acct_heytrade_fetch_me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["account_id"] == "acct_heytrade_fetch_me"
        assert data["broker"] == "heytrade"

    def test_get_404(self, client):
        c, _ = client
        resp = c.get("/api/portfolio/accounts/acct_does_not_exist")
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    def test_get_unassigned_404(self, client):
        """_unassigned is a virtual partition — no account doc → 404."""
        c, _ = client
        resp = c.get("/api/portfolio/accounts/_unassigned")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Delete account — hard-block when movements exist
# ---------------------------------------------------------------------------

class TestAccountDeleteHardBlock:
    def test_delete_empty_account_200(self, client):
        c, _ = client
        _create(c, broker="heytrade", name="Empty")
        resp = c.delete("/api/portfolio/accounts/acct_heytrade_empty")
        assert resp.status_code == 200
        assert "deleted_at" in resp.json()

    def test_delete_with_movements_409(self, client):
        c, fake = client
        _create(c, broker="heytrade", name="Active")
        _seed_movement(fake, "acct_heytrade_active")
        resp = c.delete("/api/portfolio/accounts/acct_heytrade_active")
        assert resp.status_code == 409
        data = resp.json()
        assert data["error"] == "account_has_movements"
        assert "movement_count" in data

    def test_delete_movement_count_in_response(self, client):
        c, fake = client
        _create(c, broker="heytrade", name="Many")
        for i in range(3):
            fake.portfolio_container._store[f"mvt_acct_heytrade_many_{i:02d}"] = {
                "id": f"mvt_acct_heytrade_many_{i:02d}",
                "doc_type": "ledger_txn",
                "account_id": "acct_heytrade_many",
                "txn_type": "BUY",
                "correction_status": "ACTIVE",
                "security_id": "XNYS:AAPL",
            }
        resp = c.delete("/api/portfolio/accounts/acct_heytrade_many")
        assert resp.status_code == 409
        assert resp.json()["movement_count"] >= 3

    def test_delete_nonexistent_404(self, client):
        c, _ = client
        resp = c.delete("/api/portfolio/accounts/acct_ghost")
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    def test_delete_unassigned_404(self, client):
        """_unassigned has no account doc → 404 (not a special blocked error)."""
        c, _ = client
        resp = c.delete("/api/portfolio/accounts/_unassigned")
        assert resp.status_code == 404

    def test_superseded_movements_do_not_block_delete(self, client):
        """Movements with correction_status=SUPERSEDED do NOT block account deletion."""
        c, fake = client
        _create(c, broker="heytrade", name="Cleaned")
        fake.portfolio_container._store["mvt_superseded"] = {
            "id": "mvt_superseded",
            "doc_type": "ledger_txn",
            "account_id": "acct_heytrade_cleaned",
            "txn_type": "BUY",
            "correction_status": "SUPERSEDED",  # not ACTIVE
            "security_id": "XNYS:AAPL",
        }
        resp = c.delete("/api/portfolio/accounts/acct_heytrade_cleaned")
        assert resp.status_code == 200, (
            "SUPERSEDED movements must not block account deletion"
        )


# ---------------------------------------------------------------------------
# _unassigned compatibility
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Update account — PUT /api/portfolio/accounts/{account_id}  (Gap B fix)
# ---------------------------------------------------------------------------

class TestAccountUpdate:
    def test_update_200_returns_updated_fields(self, client):
        c, _ = client
        _create(c, broker="heytrade", name="Edit Me", description="old desc")
        resp = c.put(
            "/api/portfolio/accounts/acct_heytrade_edit_me",
            json={"description": "new desc"},
        )
        assert resp.status_code == 200
        doc = resp.json()
        assert doc["description"] == "new desc"
        assert doc["account_id"] == "acct_heytrade_edit_me"

    def test_update_name_does_not_change_account_id(self, client):
        c, _ = client
        _create(c, broker="heytrade", name="Stable")
        resp = c.put(
            "/api/portfolio/accounts/acct_heytrade_stable",
            json={"name": "Renamed Display"},
        )
        assert resp.status_code == 200
        doc = resp.json()
        assert doc["name"] == "Renamed Display"
        assert doc["account_id"] == "acct_heytrade_stable"

    def test_update_broker_to_valid_value(self, client):
        c, _ = client
        _create(c, broker="heytrade", name="Switch Broker")
        resp = c.put(
            "/api/portfolio/accounts/acct_heytrade_switch_broker",
            json={"broker": "ing"},
        )
        assert resp.status_code == 200
        assert resp.json()["broker"] == "ing"

    def test_update_currency_normalised_uppercase(self, client):
        c, _ = client
        _create(c, broker="heytrade", name="Curr Test", currency="EUR")
        resp = c.put(
            "/api/portfolio/accounts/acct_heytrade_curr_test",
            json={"currency": "usd"},
        )
        assert resp.status_code == 200
        assert resp.json()["currency"] == "USD"

    def test_update_invalid_broker_400(self, client):
        c, _ = client
        _create(c, broker="heytrade", name="Bad Broker")
        resp = c.put(
            "/api/portfolio/accounts/acct_heytrade_bad_broker",
            json={"broker": "robinhood"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_update_blank_name_400(self, client):
        c, _ = client
        _create(c, broker="heytrade", name="Not Blank")
        resp = c.put(
            "/api/portfolio/accounts/acct_heytrade_not_blank",
            json={"name": "   "},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_update_empty_body_400(self, client):
        c, _ = client
        _create(c, broker="heytrade", name="Empty Body")
        resp = c.put(
            "/api/portfolio/accounts/acct_heytrade_empty_body",
            json={},
        )
        assert resp.status_code == 400

    def test_update_nonexistent_404(self, client):
        c, _ = client
        resp = c.put(
            "/api/portfolio/accounts/acct_does_not_exist",
            json={"description": "anything"},
        )
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    def test_update_deleted_account_404(self, client):
        c, _ = client
        _create(c, broker="heytrade", name="Soft Deleted")
        c.delete("/api/portfolio/accounts/acct_heytrade_soft_deleted")
        resp = c.put(
            "/api/portfolio/accounts/acct_heytrade_soft_deleted",
            json={"description": "update after delete"},
        )
        assert resp.status_code == 404

    def test_update_preserves_account_id_immutable(self, client):
        """Sending account_id in body must not rename the account."""
        c, _ = client
        _create(c, broker="heytrade", name="Immutable Id")
        resp = c.put(
            "/api/portfolio/accounts/acct_heytrade_immutable_id",
            json={"name": "New Name", "account_id": "acct_injected_id"},
        )
        assert resp.status_code == 200
        assert resp.json()["account_id"] == "acct_heytrade_immutable_id"


# ---------------------------------------------------------------------------
# Movements filter — TRANSFER_OUT / TRANSFER_IN (Gap A fix)
# ---------------------------------------------------------------------------

def _seed_transfer(fake, account_id, txn_type, txn_id):
    fake.portfolio_container._store[txn_id] = {
        "id": txn_id,
        "doc_type": "ledger_txn",
        "account_id": account_id,
        "txn_type": txn_type,
        "correction_status": "ACTIVE",
        "security_id": "XNYS:AAPL",
        "trade_date": "2024-06-01",
    }


class TestMovementsTransferFilter:
    def test_transfer_out_filter_200(self, client):
        c, fake = client
        _create(c, broker="heytrade", name="Transfer Acct")
        _seed_transfer(fake, "acct_heytrade_transfer_acct", "TRANSFER_OUT", "txn_out_001")
        resp = c.get("/api/portfolio/movements?txn_type=TRANSFER_OUT")
        assert resp.status_code == 200
        ids = [m["id"] for m in resp.json()["movements"]]
        assert "txn_out_001" in ids

    def test_transfer_in_filter_200(self, client):
        c, fake = client
        _create(c, broker="heytrade", name="Transfer Acct 2")
        _seed_transfer(fake, "acct_heytrade_transfer_acct_2", "TRANSFER_IN", "txn_in_001")
        resp = c.get("/api/portfolio/movements?txn_type=TRANSFER_IN")
        assert resp.status_code == 200
        ids = [m["id"] for m in resp.json()["movements"]]
        assert "txn_in_001" in ids

    def test_buy_filter_still_works(self, client):
        c, _ = client
        resp = c.get("/api/portfolio/movements?txn_type=BUY")
        assert resp.status_code == 200

    def test_sell_filter_still_works(self, client):
        c, _ = client
        resp = c.get("/api/portfolio/movements?txn_type=SELL")
        assert resp.status_code == 200

    def test_dividend_filter_still_works(self, client):
        c, _ = client
        resp = c.get("/api/portfolio/movements?txn_type=DIVIDEND")
        assert resp.status_code == 200

    def test_option_filter_200(self, client):
        c, fake = client
        _create(c, broker="heytrade", name="Option Filter Acct")
        _seed_transfer(fake, "acct_heytrade_option_filter_acct", "CALL_SELL", "txn_call_sell_001")
        resp = c.get("/api/portfolio/movements?txn_type=CALL_SELL")
        assert resp.status_code == 200
        ids = [m["id"] for m in resp.json()["movements"]]
        assert "txn_call_sell_001" in ids

    def test_invalid_txn_type_still_400(self, client):
        c, _ = client
        resp = c.get("/api/portfolio/movements?txn_type=UNKNOWN")
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_generic_transfer_rejected_400(self, client):
        """Frontend never sends bare 'TRANSFER'; only OUT/IN are valid."""
        c, _ = client
        resp = c.get("/api/portfolio/movements?txn_type=TRANSFER")
        assert resp.status_code == 400


class TestUnassignedCompatibility:
    def test_movements_in_unassigned_queryable(self, client):
        c, fake = client
        fake.portfolio_container._store["txn_u_001"] = {
            "id": "txn_u_001",
            "doc_type": "ledger_txn",
            "account_id": "_unassigned",
            "txn_type": "BUY",
            "correction_status": "ACTIVE",
            "security_id": "XNYS:AAPL",
        }
        resp = c.get("/api/portfolio/movements?account_id=_unassigned")
        assert resp.status_code == 200
        ids = [m["id"] for m in resp.json()["movements"]]
        assert "txn_u_001" in ids

    def test_import_defaults_to_unassigned(self, client):
        import io
        c, _ = client
        csv_bytes = (
            "Año\tEmpresa\tFecha compra\tValor compra\tAcciones\tTotal (€)\tComisión\n"
            "2024\tApple Inc.\t10/01/2024\t182,50\t10\t1.825,00\t7,50\n"
        ).encode()
        resp = c.post(
            "/api/import/sessions",
            files={"file": ("t.csv", io.BytesIO(csv_bytes), "text/csv")},
            data={"format_hint": "purchases"},
        )
        assert resp.status_code == 201
        assert resp.json().get("account_id") == "_unassigned"
