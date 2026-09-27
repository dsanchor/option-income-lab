"""Tests for the securities catalog (CosmosSecuritiesService).

Hermetic: uses an in-memory FakeContainer instead of real Cosmos.
"""

import pytest
from azure.cosmos.exceptions import CosmosResourceNotFoundError

from src.portfolio.cosmos_securities import (
    CosmosSecuritiesService,
    _CollisionError,
    make_security_id,
    security_id_to_doc_id,
    security_id_to_ticker,
)


# ---------------------------------------------------------------------------
# Fake Cosmos container
# ---------------------------------------------------------------------------

class FakeContainer:
    """In-memory stand-in for a Cosmos symbols container."""

    def __init__(self):
        self._store: dict = {}

    def read_item(self, item: str, partition_key: str):
        key = (partition_key, item)
        if key not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[key])

    def create_item(self, body: dict):
        ticker = body["symbol"]
        doc_id = body["id"]
        key = (ticker, doc_id)
        if key in self._store:
            raise Exception("Conflict")
        self._store[key] = dict(body)
        return dict(body)

    def query_items(self, query: str, parameters=None, enable_cross_partition_query=False, partition_key=None):
        param_map = {p["name"]: p["value"] for p in (parameters or [])}

        # Minimal query interpretation
        results = []
        for (pk, did), doc in self._store.items():
            if partition_key and pk != partition_key:
                continue
            if doc.get("doc_type") != "security_master":
                continue

            if "@isin" in param_map:
                if doc.get("isin") != param_map["@isin"]:
                    continue

            results.append(dict(doc))
        return iter(results)

    def replace_item(self, item: str, body: dict):
        for key, stored in self._store.items():
            if stored.get("id") == item:
                self._store[key] = dict(body)
                return dict(body)
        raise CosmosResourceNotFoundError(message="not found", response=None)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_svc():
    return CosmosSecuritiesService(FakeContainer())


_AAPL_DATA = {
    "ticker": "AAPL",
    "company_name": "Apple Inc.",
    "exchange_mic": "XNYS",
    "asset_class": "Equity",
    "listing_currency": "USD",
    "isin": "US0378331005",
    "aliases": [{"source": "user", "value": "Apple"}],
}

_TEF_DATA = {
    "ticker": "TEF",
    "company_name": "Telefónica SA",
    "exchange_mic": "XMAD",
    "asset_class": "Equity",
    "listing_currency": "EUR",
    "isin": "ES0178430E18",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestSecurityIdHelpers:
    def test_make_security_id(self):
        assert make_security_id("XNYS", "AAPL") == "XNYS:AAPL"

    def test_doc_id(self):
        assert security_id_to_doc_id("XNYS:AAPL") == "sec_XNYS_AAPL"

    def test_ticker(self):
        assert security_id_to_ticker("XNYS:AAPL") == "AAPL"
        assert security_id_to_ticker("XMAD:TEF") == "TEF"


def test_create_security_requires_explicit_iso_listing_currency():
    svc = _make_svc()
    with pytest.raises(ValueError, match="required for non-US listings"):
        svc.create_security(
            {
                "ticker": "ULVR",
                "company_name": "Unilever",
                "exchange_mic": "XLON",
            }
        )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

class TestCreateSecurity:
    def test_create_returns_doc(self):
        svc = _make_svc()
        doc = svc.create_security(_AAPL_DATA)
        assert doc["security_id"] == "XNYS:AAPL"
        assert doc["ticker"] == "AAPL"
        assert doc["company_name"] == "Apple Inc."
        assert doc["exchange_mic"] == "XNYS"
        assert doc["status"] == "ACTIVE"

    def test_create_sets_id(self):
        svc = _make_svc()
        doc = svc.create_security(_AAPL_DATA)
        assert doc["id"] == "sec_XNYS_AAPL"

    def test_create_normalises_aliases(self):
        svc = _make_svc()
        doc = svc.create_security(_AAPL_DATA)
        aliases = doc.get("aliases", [])
        assert len(aliases) == 1
        assert aliases[0]["normalized"] == "apple"

    def test_duplicate_security_id_raises_collision(self):
        svc = _make_svc()
        svc.create_security(_AAPL_DATA)
        with pytest.raises(_CollisionError) as exc_info:
            svc.create_security(_AAPL_DATA)
        assert exc_info.value.field == "security_id"

    def test_duplicate_isin_raises_collision(self):
        svc = _make_svc()
        svc.create_security(_AAPL_DATA)
        duplicate = dict(_AAPL_DATA)
        duplicate["ticker"] = "AAPL2"
        duplicate["exchange_mic"] = "XNAS"
        # Same ISIN
        with pytest.raises(_CollisionError) as exc_info:
            svc.create_security(duplicate)
        assert exc_info.value.field == "isin"

    def test_create_different_exchange_same_ticker(self):
        """Multi-market support: same ticker, different MIC — both allowed."""
        svc = _make_svc()
        svc.create_security(_AAPL_DATA)
        aapl_nas = dict(_AAPL_DATA)
        aapl_nas["exchange_mic"] = "XNAS"
        aapl_nas["isin"] = "US0378331099"  # different ISIN
        doc = svc.create_security(aapl_nas)
        assert doc["security_id"] == "XNAS:AAPL"

    def test_no_isin_no_collision_check(self):
        svc = _make_svc()
        data = dict(_AAPL_DATA)
        del data["isin"]
        doc = svc.create_security(data)
        assert doc["security_id"] == "XNYS:AAPL"


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

class TestGetSecurity:
    def test_get_existing(self):
        svc = _make_svc()
        svc.create_security(_AAPL_DATA)
        doc = svc.get_security("XNYS:AAPL")
        assert doc is not None
        assert doc["security_id"] == "XNYS:AAPL"

    def test_get_nonexistent_returns_none(self):
        svc = _make_svc()
        assert svc.get_security("XNYS:AAPL") is None

    def test_list_securities_empty(self):
        svc = _make_svc()
        assert svc.list_securities() == []

    def test_list_securities_returns_all(self):
        svc = _make_svc()
        svc.create_security(_AAPL_DATA)
        svc.create_security(_TEF_DATA)
        docs = svc.list_securities()
        ids = {d["security_id"] for d in docs}
        assert ids == {"XNYS:AAPL", "XMAD:TEF"}

    def test_cosmos_system_keys_stripped(self):
        svc = _make_svc()
        doc = svc.create_security(_AAPL_DATA)
        for key in {"_rid", "_self", "_etag", "_attachments", "_ts"}:
            assert key not in doc


# ---------------------------------------------------------------------------
# Candidate search
# ---------------------------------------------------------------------------

class TestFindCandidates:
    def test_exact_name_match(self):
        svc = _make_svc()
        svc.create_security(_AAPL_DATA)
        candidates = svc.find_candidates_for_name("apple inc.")
        assert len(candidates) == 1
        assert candidates[0]["security_id"] == "XNYS:AAPL"

    def test_no_match_returns_empty(self):
        svc = _make_svc()
        svc.create_security(_AAPL_DATA)
        candidates = svc.find_candidates_for_name("nonexistent corp")
        assert len(candidates) == 0

    def test_alias_match(self):
        svc = _make_svc()
        svc.create_security(_AAPL_DATA)
        candidates = svc.find_candidates_for_name("apple")
        assert len(candidates) >= 1
        assert candidates[0]["security_id"] == "XNYS:AAPL"
