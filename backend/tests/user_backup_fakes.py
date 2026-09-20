from __future__ import annotations

from copy import deepcopy
from hashlib import sha1
from types import SimpleNamespace


class FakeContainer:
    def __init__(self, partition_field: str):
        self.partition_field = partition_field
        self.store = {}
        self.fail_create_id = None
        self.replace_calls = 0
        self.fail_replace_at = None

    def _key(self, body):
        return (str(body.get(self.partition_field, body["id"])), body["id"])

    def create_item(self, body=None, **kwargs):
        body = deepcopy(body or kwargs["body"])
        if body["id"] == self.fail_create_id:
            raise RuntimeError("injected create failure")
        key = self._key(body)
        if key in self.store:
            raise RuntimeError("409 Conflict")
        body["_etag"] = sha1(repr(body).encode()).hexdigest()
        self.store[key] = body
        return deepcopy(body)

    def read_item(self, item=None, partition_key=None, **kwargs):
        key = (str(partition_key), item)
        if key not in self.store:
            raise RuntimeError("404 not found")
        return deepcopy(self.store[key])

    def replace_item(self, item=None, body=None, **kwargs):
        self.replace_calls += 1
        if self.replace_calls == self.fail_replace_at:
            raise RuntimeError("injected journal replace failure")
        key = self._key(body)
        if key not in self.store:
            raise RuntimeError("404 not found")
        body = deepcopy(body)
        body["_etag"] = sha1(repr(body).encode()).hexdigest()
        self.store[key] = body
        return deepcopy(body)

    def delete_item(self, item=None, partition_key=None, **kwargs):
        key = (str(partition_key), item)
        if key not in self.store:
            raise RuntimeError("404 not found")
        del self.store[key]

    def query_items(self, query="", **kwargs):
        values = list(self.store.values())
        for doc_type in ("account", "ledger_txn", "security_master", "symbol_config", "action_plan"):
            if f"doc_type = '{doc_type}'" in query:
                values = [item for item in values if item.get("doc_type") == doc_type]
        return iter(deepcopy(values))


class FakeCosmos:
    def __init__(self):
        self.container = FakeContainer("symbol")
        self.portfolio_container = FakeContainer("account_id")
        self.settings_container = FakeContainer("id")
        self.import_sessions_container = FakeContainer("session_id")


def populated_cosmos() -> FakeCosmos:
    cosmos = FakeCosmos()
    cosmos.portfolio_container.create_item(body={
        "id": "acct_demo", "account_id": "acct_demo", "doc_type": "account",
        "broker": "Demo", "name": "Primary", "currency": "EUR",
    })
    cosmos.container.create_item(body={
        "id": "sec_XNAS_AAPL", "symbol": "AAPL", "doc_type": "security_master",
        "security_id": "XNAS:AAPL", "ticker": "AAPL", "company_name": "Apple",
        "exchange_mic": "XNAS", "listing_currency": "USD", "status": "ACTIVE",
        "aliases": [], "provider_symbols": {"yfinance": "AAPL"},
        "created_by_migration": True, "migrated_from": "LEGACY:AAPL",
        "migration_note": "Canonical identity migration",
    })
    cosmos.container.create_item(body={
        "id": "config_AAPL", "symbol": "AAPL", "doc_type": "symbol_config",
        "security_id": "XNAS:AAPL", "watchlist": {"enabled": True},
        "pricing_cache": {"status": "ok", "price_eur": "180.00"},
        "positions": [{
            "position_id": "pos_1", "type": "call", "strike": 200,
            "expiration": "2027-01-15", "status": "active",
            "opened_at": "2026-09-01T00:00:00Z", "notes": "covered", "is_paper": True,
            "source": {"activity_id": "aB3dE5fG7hJ9kL2mN4pQ6rS8tU1vW3xY"},
        }],
    })
    cosmos.container.create_item(body={
        "id": "plan_1", "symbol": "AAPL", "doc_type": "action_plan",
        "title": "Hold", "objective": "Income", "plan_type": "covered_call",
        "status": "planned", "priority": "medium", "conditions": "Above 200",
    })
    cosmos.portfolio_container.create_item(body={
        "id": "mvt_1", "account_id": "acct_demo", "doc_type": "ledger_txn",
        "txn_type": "CALL_SELL", "security_id": "XNAS:AAPL", "ticker": "AAPL",
        "trade_date": "2026-09-01", "quantity": "0",
        "gross": {"amount": "100.00", "currency": "USD", "eur_amount": "90.00"},
        "fees": {"total": "1.00", "currency": "USD", "total_eur": "0.90"},
        "net": {"amount": "99.00", "currency": "USD", "eur_amount": "89.10"},
        "withholding": {"source": None, "destination": None},
        "fx": {"rate": "0.9", "rate_source": "BROKER"},
        "option_position_id": "pos_1", "option_link_kind": "OPEN_SELL",
        "option_type": "call", "option_strike": "200",
        "option_expiration": "2027-01-15", "correction_status": "ACTIVE",
        "import_source": "manual", "company_name": "Apple",
        "warnings": [{"type": "PROBABLE_DUPLICATE", "message": "Reviewed"}],
    })
    cosmos.settings_container.create_item(body={
        "id": "app-config",
        "scheduler": {"enabled": True},
        "telegram": {"enabled": True, "bot_token": "must-not-export"},
        "cosmosdb": {"key": "must-not-export"},
        "calendar_sync": {"enabled": True},
        "agent_trace": {"enabled_types": ["decision"]},
    })
    return cosmos


class FakeDownloader:
    def __init__(self, data):
        self.data = data

    def readall(self):
        return self.data


class FakeBlobClient:
    def __init__(self, blobs, path):
        self.blobs, self.path = blobs, path

    def upload_blob(self, data, overwrite=False, metadata=None, tags=None, **kwargs):
        if self.path in self.blobs and not overwrite:
            raise RuntimeError("409 Conflict")
        self.blobs[self.path] = {
            "data": bytes(data), "metadata": metadata or {},
            "tags": tags or {},
            "etag": sha1(bytes(data)).hexdigest(),
        }
        return SimpleNamespace(etag=self.blobs[self.path]["etag"])

    def download_blob(self):
        if self.path not in self.blobs:
            raise RuntimeError("404")
        return FakeDownloader(self.blobs[self.path]["data"])

    def get_blob_properties(self):
        if self.path not in self.blobs:
            raise RuntimeError("404")
        item = self.blobs[self.path]
        return SimpleNamespace(etag=item["etag"], size=len(item["data"]))

    def set_blob_tags(self, tags):
        if self.path not in self.blobs:
            raise RuntimeError("404")
        self.blobs[self.path]["tags"] = deepcopy(tags)

    def delete_blob(self):
        if self.path not in self.blobs:
            raise RuntimeError("404")
        del self.blobs[self.path]

    def acquire_lease(self, lease_duration=60):
        if self.blobs[self.path].get("leased"):
            raise RuntimeError("409 lease conflict")
        self.blobs[self.path]["leased"] = True
        blobs, path = self.blobs, self.path

        class Lease:
            def release(self):
                blobs[path]["leased"] = False
        return Lease()


class FakeBlobContainer:
    def __init__(self):
        self.blobs = {}

    def get_blob_client(self, path):
        return FakeBlobClient(self.blobs, path)

    def list_blobs(self, name_starts_with=""):
        return [
            SimpleNamespace(name=path)
            for path in sorted(self.blobs)
            if path.startswith(name_starts_with)
        ]
