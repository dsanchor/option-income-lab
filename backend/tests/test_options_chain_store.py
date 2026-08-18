"""
Test suite for the sharded CosmosDB options chain persistence layer
(src/options_chain_store.py).

Hermetic: no real Cosmos connectivity. A small in-memory fake container
(``FakeContainer``) stands in for ``azure.cosmos``'s container client,
supporting ``read_item``/``create_item``/``replace_item`` (with ETag +
``MatchConditions.IfNotModified`` semantics, raising
``CosmosHttpResponseError(412)`` on a stale ETag) /``delete_item``/
``query_items``.

Livingston's 2026-08-18 D1/D2/D5 revision: the store's write path
(``_write_shard``) no longer imports or calls
``options_chain_merge.merge_prior`` at all — the read-reconcile-write CAS
loop uses its own store-owned, monotone verbatim union
(``options_chain_store._reconcile_bucket``), because both sides it
reconciles (whatever is currently persisted, and this cycle's already-
merged-and-recomputed chain) are already fully-formed accumulated
contracts, not raw live-source observations. Calling `merge_prior` on that
input was exactly the bug (D1: drops mid/greeks; D2: manufactures fresh
`_meta`) — see the module docstring in ``src/options_chain_store.py``.
Consequently these tests need no fake/stand-in for the merge module at
all: the store is fully self-contained and its own tests exercise the
real reconcile/CAS-retry control flow directly.
"""

import copy
from datetime import date, datetime, timedelta, timezone

import pytest
from azure.core import MatchConditions
from azure.cosmos.exceptions import (
    CosmosHttpResponseError,
    CosmosResourceNotFoundError,
)

from src.options_chain_store import OptionsChainStore, get_options_chain_store, set_options_chain_store


# ===========================================================================
# Fake Cosmos container
# ===========================================================================

class FakeContainer:
    """In-memory stand-in for a Cosmos container client.

    Tracks per-item ``_etag`` and enforces optimistic concurrency exactly
    like the real SDK: ``replace_item`` with a stale ``etag`` under
    ``MatchConditions.IfNotModified`` raises ``CosmosHttpResponseError(412)``.
    """

    def __init__(self):
        self.store: dict[str, dict] = {}
        self._etag_counter = 0
        # Test hook: when set, replace_item raises this many more times
        # before succeeding (simulates a concurrent writer winning the race).
        self.force_conflicts = 0
        # Status code used for the *forced* conflicts above. Real Cosmos CAS
        # conflicts surface as either 409 (Conflict) or 412 (Precondition
        # Failed) depending on the operation; the store must retry both
        # identically.
        self.force_conflict_status_code = 412

    def _next_etag(self) -> str:
        self._etag_counter += 1
        return f"etag-{self._etag_counter}"

    def read_item(self, item, partition_key):
        doc = self.store.get(item)
        if doc is None:
            raise CosmosResourceNotFoundError()
        return copy.deepcopy(doc)

    def create_item(self, body):
        doc = copy.deepcopy(body)
        doc["_etag"] = self._next_etag()
        self.store[doc["id"]] = doc
        return copy.deepcopy(doc)

    def replace_item(self, item, body, etag=None, match_condition=None):
        if self.force_conflicts > 0:
            self.force_conflicts -= 1
            raise CosmosHttpResponseError(
                status_code=self.force_conflict_status_code, message="Conflict"
            )
        current = self.store.get(item)
        if (
            match_condition == MatchConditions.IfNotModified
            and current is not None
            and current.get("_etag") != etag
        ):
            raise CosmosHttpResponseError(status_code=412, message="Precondition failed")
        doc = copy.deepcopy(body)
        doc["_etag"] = self._next_etag()
        self.store[item] = doc
        return copy.deepcopy(doc)

    def delete_item(self, item, partition_key):
        if item not in self.store:
            raise CosmosResourceNotFoundError()
        del self.store[item]

    def query_items(self, query, parameters=None, partition_key=None):
        params = {p["name"]: p["value"] for p in (parameters or [])}
        symbol = params.get("@s")
        results = [
            copy.deepcopy(doc) for doc in self.store.values()
            if doc.get("symbol") == symbol
        ]
        return results


def _contract(**overrides):
    base = {
        "contractSymbol": "AAPL260101C00100000",
        "strike": 100.0,
        "bid": 1.0,
        "ask": 1.2,
        "mid": 1.1,
        "iv": 0.25,
        "delta": 0.5,
        "gamma": 0.05,
        "theta": -0.02,
        "vega": 0.1,
        "rho": 0.01,
        "volume": 10,
        "openInterest": 100,
        "lastPrice": 1.1,
        "lastTradeDate": "2026-01-01T00:00:00Z",
        "inTheMoney": False,
        "expiration": "20260821",
        "option_type": "call",
    }
    base.update(overrides)
    return base


def _future_exp_key(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y%m%d")



@pytest.fixture
def container():
    return FakeContainer()


@pytest.fixture
def store(container):
    return OptionsChainStore(container=container)


# ===========================================================================
# Availability / disabled mode
# ===========================================================================

class TestAvailability:
    def test_disabled_store_is_unavailable(self):
        s = OptionsChainStore(enabled=False)
        assert s.is_available() is False

    def test_enabled_without_container_is_unavailable(self):
        s = OptionsChainStore(container=None, enabled=True)
        assert s.is_available() is False

    def test_enabled_with_container_is_available(self, store):
        assert store.is_available() is True

    def test_disabled_hydrate_returns_none(self):
        s = OptionsChainStore(enabled=False)
        assert s.hydrate("AAPL") is None

    def test_disabled_persist_is_noop(self):
        s = OptionsChainStore(enabled=False)
        result = s.persist("AAPL", {"calls": {"20260101": {"100.0": _contract()}}, "puts": {}})
        assert result == {"written": 0, "unchanged": 0, "conflicts_skipped": 0, "errors": 0}

    def test_disabled_prune_returns_zero(self):
        s = OptionsChainStore(enabled=False)
        assert s.prune_expired("AAPL", today_et=date(2026, 8, 18)) == 0

    def test_disabled_purge_returns_zero(self):
        s = OptionsChainStore(enabled=False)
        assert s.purge("AAPL") == 0


# ===========================================================================
# T13/T14 — hydrate (cold start / restart)
# ===========================================================================

class TestHydrate:
    def test_hydrate_empty_store_returns_none(self, store):
        assert store.hydrate("AAPL") is None

    def test_hydrate_reassembles_shards_into_a_chain(self, store, container):
        exp1, exp2 = "20260821", "20260828"
        container.store["optchain_AAPL_20260821"] = {
            "id": "optchain_AAPL_20260821", "symbol": "AAPL", "doc_type": "options_chain",
            "expiration": exp1, "schema_version": 2,
            "calls": {"100.0": _contract(expiration=exp1)}, "puts": {},
            "_etag": "e1",
        }
        container.store["optchain_AAPL_20260828"] = {
            "id": "optchain_AAPL_20260828", "symbol": "AAPL", "doc_type": "options_chain",
            "expiration": exp2, "schema_version": 2,
            "calls": {}, "puts": {"95.0": _contract(expiration=exp2, option_type="put")},
            "_etag": "e2",
        }

        chain = store.hydrate("AAPL")
        assert chain["calls"][exp1]["100.0"]["strike"] == 100.0
        assert chain["puts"][exp2]["95.0"]["strike"] == 100.0  # base fixture strike

    def test_hydrate_restart_simulation_serves_prior_quotes(self, container):
        """A fresh store instance (simulating process restart) reading a
        previously populated fake container must reconstruct the same
        chain a prior process persisted — no data loss across restart."""
        exp = "20260821"
        container.store["optchain_AAPL_20260821"] = {
            "id": "optchain_AAPL_20260821", "symbol": "AAPL", "doc_type": "options_chain",
            "expiration": exp, "schema_version": 2,
            "calls": {"100.0": _contract(bid=3.3, expiration=exp)}, "puts": {},
            "_etag": "e1",
        }

        fresh_store = OptionsChainStore(container=container)
        chain = fresh_store.hydrate("AAPL")
        assert chain["calls"][exp]["100.0"]["bid"] == 3.3

    def test_hydrate_ignores_other_symbols(self, store, container):
        container.store["optchain_MSFT_20260821"] = {
            "id": "optchain_MSFT_20260821", "symbol": "MSFT", "doc_type": "options_chain",
            "expiration": "20260821", "calls": {"100.0": _contract()}, "puts": {}, "_etag": "e1",
        }
        assert store.hydrate("AAPL") is None

    def test_hydrate_read_failure_returns_none_not_raises(self, store, container):
        def _boom(*a, **kw):
            raise RuntimeError("connection reset")
        container.query_items = _boom
        assert store.hydrate("AAPL") is None
        assert store.stats()["persist_errors"] == 1

    def test_hydrate_migrates_legacy_shard_missing_schema_version(self, store, container):
        """A shard written before ``schema_version`` existed (or written by
        any process that omits it) must still hydrate correctly — hydrate()
        only reads ``expiration``/``calls``/``puts``, it never gates on
        ``schema_version`` being present or matching ``_SCHEMA_VERSION``.
        Forward-compatible-by-construction, not by an explicit version
        check."""
        exp = "20260821"
        container.store[f"optchain_AAPL_{exp}"] = {
            "id": f"optchain_AAPL_{exp}",
            "symbol": "AAPL",
            "doc_type": "options_chain",
            "expiration": exp,
            # Deliberately no "schema_version" key at all.
            "calls": {"100.0": _contract(bid=1.5, expiration=exp)},
            "puts": {},
            "_etag": "e1",
        }

        chain = store.hydrate("AAPL")
        assert chain["calls"][exp]["100.0"]["bid"] == 1.5


# ===========================================================================
# T15 — persistence failure is non-fatal
# ===========================================================================

class TestPersistenceFailureNonFatal:
    def test_write_failure_counted_not_raised(self, store, container):
        def _boom(*a, **kw):
            raise RuntimeError("Cosmos unavailable")
        container.create_item = _boom

        exp = _future_exp_key(5)
        result = store.persist("AAPL", {"calls": {exp: {"100.0": _contract(expiration=exp)}}, "puts": {}})

        assert result["errors"] == 1
        assert result["written"] == 0
        stats = store.stats()
        assert stats["persist_errors"] == 1
        assert stats["last_persist_error_at"] is not None

    def test_persist_never_raises_out_of_the_call(self, store, container):
        def _boom(*a, **kw):
            raise RuntimeError("boom")
        container.read_item = _boom
        container.create_item = _boom

        exp = _future_exp_key(5)
        # Must not raise.
        result = store.persist("AAPL", {"calls": {exp: {"100.0": _contract(expiration=exp)}}, "puts": {}})
        assert result["errors"] >= 1


# ===========================================================================
# Basic persist / unchanged detection
# ===========================================================================

class TestPersistBasic:
    def test_first_write_creates_shard(self, store, container):
        exp = _future_exp_key(5)
        chain = {"calls": {exp: {"100.0": _contract(expiration=exp)}}, "puts": {}}
        result = store.persist("AAPL", chain)
        assert result["written"] == 1
        assert f"optchain_AAPL_{exp}" in container.store

    def test_identical_content_is_not_rewritten(self, store, container):
        exp = _future_exp_key(5)
        chain = {"calls": {exp: {"100.0": _contract(expiration=exp)}}, "puts": {}}
        # Same fixed instant on both calls: an idempotent re-persist of the
        # exact same cycle's already-merged result must not rewrite the
        # shard (real merge_prior would otherwise legitimately advance
        # _meta.last_seen/quote_asof forward on every distinct observation,
        # which is expected to change the hash — that's a different,
        # intentional behavior covered by test_changed_content_is_rewritten).
        fixed_now = datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)
        store.persist("AAPL", chain, now=fixed_now)
        etag_after_first = container.store[f"optchain_AAPL_{exp}"]["_etag"]

        result = store.persist("AAPL", chain, now=fixed_now)
        assert result["unchanged"] == 1
        assert result["written"] == 0
        assert container.store[f"optchain_AAPL_{exp}"]["_etag"] == etag_after_first

    def test_changed_content_is_rewritten(self, store, container):
        exp = _future_exp_key(5)
        chain1 = {"calls": {exp: {"100.0": _contract(bid=1.0, expiration=exp)}}, "puts": {}}
        store.persist("AAPL", chain1)

        chain2 = {"calls": {exp: {"100.0": _contract(bid=2.0, expiration=exp)}}, "puts": {}}
        result = store.persist("AAPL", chain2)
        assert result["written"] == 1
        assert container.store[f"optchain_AAPL_{exp}"]["calls"]["100.0"]["bid"] == 2.0

    def test_one_shard_per_expiration(self, store, container):
        exp1, exp2 = _future_exp_key(5), _future_exp_key(12)
        chain = {
            "calls": {
                exp1: {"100.0": _contract(expiration=exp1)},
                exp2: {"105.0": _contract(expiration=exp2, strike=105.0)},
            },
            "puts": {},
        }
        store.persist("AAPL", chain)
        assert f"optchain_AAPL_{exp1}" in container.store
        assert f"optchain_AAPL_{exp2}" in container.store
        assert container.store[f"optchain_AAPL_{exp1}"]["doc_type"] == "options_chain"
        # schema_version 3 (Livingston, D1/D3): adds `underlying_price`.
        assert container.store[f"optchain_AAPL_{exp1}"]["schema_version"] == 3


# ===========================================================================
# D1/D2 — the store-side write path must never drop derived fields or
# manufacture provenance when reconciling against an already-persisted
# shard (the exact defect the 2026-08-18 review rejected).
# ===========================================================================

class TestReconcileFidelity:
    def test_write_time_reconcile_preserves_derived_fields_and_meta_verbatim(self, store, container):
        """A shard rewrite triggered by a genuine CAS conflict (the only
        code path that exercises `_reconcile_bucket`) must not lose the
        prior contract's mid/greeks or fabricate a new `_meta` for it — the
        untouched contract from `stored` must survive byte-for-byte."""
        exp = _future_exp_key(5)
        original_meta = {
            "quote_asof": "2026-08-10T00:00:00+00:00",
            "quote_source": "tradingview",
            "carried": True,
            "first_seen": "2026-07-01T00:00:00+00:00",
            "last_seen": "2026-08-10T00:00:00+00:00",
            "greeks_valid": True,
        }
        chain1 = {
            "calls": {exp: {"100.0": _contract(
                expiration=exp, bid=4.0, ask=4.2, mid=4.1, delta=0.42,
                gamma=0.03, theta=-0.05, vega=0.11, rho=0.02,
                _meta=dict(original_meta),
            )}},
            "puts": {},
        }
        store.persist("AAPL", chain1)

        # Force a CAS conflict on the next write for an unrelated contract
        # (105.0) so the retry path re-reads `stored` and reconciles it
        # against `want` via `_reconcile_bucket`.
        container.force_conflicts = 1
        chain2 = {
            "calls": {exp: {"105.0": _contract(expiration=exp, strike=105.0, bid=1.0)}},
            "puts": {},
        }
        store.persist("AAPL", chain2)

        shard = container.store[f"optchain_AAPL_{exp}"]
        untouched = shard["calls"]["100.0"]
        # Derived fields (D1): never dropped by the reconcile.
        assert untouched["mid"] == 4.1
        assert untouched["delta"] == 0.42
        assert untouched["gamma"] == 0.03
        assert untouched["theta"] == -0.05
        assert untouched["vega"] == 0.11
        assert untouched["rho"] == 0.02
        # Provenance (D2): transported verbatim, never manufactured.
        assert untouched["_meta"] == original_meta

    def test_newer_meta_wins_when_a_contract_exists_on_both_sides(self, store, container):
        """When the *same* contract genuinely exists on both the currently
        persisted shard and this cycle's chain (e.g. a concurrent writer
        raced this process), the reconcile must keep the more recently
        observed side wholesale — never blend fields from both."""
        exp = _future_exp_key(5)
        stale_meta = {"quote_asof": "2026-08-01T00:00:00+00:00", "quote_source": "yfinance",
                      "carried": False, "first_seen": "2026-07-01T00:00:00+00:00",
                      "last_seen": "2026-08-01T00:00:00+00:00", "greeks_valid": True}
        fresh_meta = {"quote_asof": "2026-08-15T00:00:00+00:00", "quote_source": "tradingview",
                      "carried": False, "first_seen": "2026-07-01T00:00:00+00:00",
                      "last_seen": "2026-08-15T00:00:00+00:00", "greeks_valid": True}

        container.store[f"optchain_AAPL_{exp}"] = {
            "id": f"optchain_AAPL_{exp}", "symbol": "AAPL", "doc_type": "options_chain",
            "expiration": exp, "schema_version": 3,
            "calls": {"100.0": _contract(expiration=exp, bid=1.0, _meta=stale_meta)},
            "puts": {}, "_etag": "e1",
        }

        chain = {"calls": {exp: {"100.0": _contract(expiration=exp, bid=9.0, _meta=fresh_meta)}}, "puts": {}}
        store.persist("AAPL", chain)

        shard = container.store[f"optchain_AAPL_{exp}"]
        # The fresher (`want`) side wins wholesale -- bid AND meta together,
        # never a field-level blend of the two sides.
        assert shard["calls"]["100.0"]["bid"] == 9.0
        assert shard["calls"]["100.0"]["_meta"] == fresh_meta


# ===========================================================================
# D5 — write-skip guard must be effective against market-observable
# content, not defeated by `_meta.last_seen`/`quote_asof` always advancing.
# ===========================================================================

class TestContentHashExcludesVolatileMeta:
    def test_only_last_seen_and_quote_asof_advancing_is_still_unchanged(self, store, container):
        """Simulates what the real refresh cycle actually produces every
        30 minutes when nothing market-observable changed: the contract's
        `_meta.last_seen`/`quote_asof` advance (a still-listed contract
        always gets a fresh `last_seen` — see
        `options_chain_merge._merge_prior_contract`) but every visible
        field (bid/ask/iv/mid/greeks/...) is bit-identical. That must
        still be a no-op write — the old hash (over the whole `_meta`
        blob) could never converge in production; this is the regression
        guard for that."""
        exp = _future_exp_key(5)
        chain1 = {
            "calls": {exp: {"100.0": _contract(
                expiration=exp, bid=1.0, ask=1.2, mid=1.1,
                _meta={"quote_asof": "2026-08-18T07:00:00+00:00", "quote_source": "yfinance",
                       "carried": False, "first_seen": "2026-08-01T00:00:00+00:00",
                       "last_seen": "2026-08-18T07:00:00+00:00", "greeks_valid": True},
            )}},
            "puts": {},
            "underlying_price": 150.0,
        }
        store.persist("AAPL", chain1)
        etag_after_first = container.store[f"optchain_AAPL_{exp}"]["_etag"]

        chain2 = {
            "calls": {exp: {"100.0": _contract(
                expiration=exp, bid=1.0, ask=1.2, mid=1.1,
                _meta={"quote_asof": "2026-08-18T07:30:00+00:00", "quote_source": "yfinance",
                       "carried": False, "first_seen": "2026-08-01T00:00:00+00:00",
                       "last_seen": "2026-08-18T07:30:00+00:00", "greeks_valid": True},
            )}},
            "puts": {},
            "underlying_price": 150.0,
        }
        result = store.persist("AAPL", chain2)

        assert result["unchanged"] == 1
        assert result["written"] == 0
        assert container.store[f"optchain_AAPL_{exp}"]["_etag"] == etag_after_first

    def test_a_real_field_change_still_triggers_a_rewrite(self, store, container):
        """The write-skip guard must not become so lenient it hides a real
        change -- a genuine bid move alongside advancing timestamps is
        still written."""
        exp = _future_exp_key(5)
        chain1 = {
            "calls": {exp: {"100.0": _contract(
                expiration=exp, bid=1.0,
                _meta={"last_seen": "2026-08-18T07:00:00+00:00"},
            )}},
            "puts": {}, "underlying_price": 150.0,
        }
        store.persist("AAPL", chain1)

        chain2 = {
            "calls": {exp: {"100.0": _contract(
                expiration=exp, bid=1.5,
                _meta={"last_seen": "2026-08-18T07:30:00+00:00"},
            )}},
            "puts": {}, "underlying_price": 150.0,
        }
        result = store.persist("AAPL", chain2)
        assert result["written"] == 1
        assert container.store[f"optchain_AAPL_{exp}"]["calls"]["100.0"]["bid"] == 1.5


# ===========================================================================
# D3 — underlying_price round-trips through the shard so a cold hydrate can
# restore the top-level field the schema documents.
# ===========================================================================

class TestUnderlyingPricePersistence:
    def test_underlying_price_persisted_and_restored_on_hydrate(self, store, container):
        exp = _future_exp_key(5)
        chain = {
            "calls": {exp: {"100.0": _contract(expiration=exp)}},
            "puts": {},
            "underlying_price": 187.65,
        }
        store.persist("AAPL", chain)
        assert container.store[f"optchain_AAPL_{exp}"]["underlying_price"] == 187.65

        hydrated = store.hydrate("AAPL")
        assert hydrated["underlying_price"] == 187.65
        assert hydrated["timestamp"] == container.store[f"optchain_AAPL_{exp}"]["updated_at"]

    def test_legacy_shard_without_underlying_price_hydrates_without_it(self, store, container):
        exp = "20260821"
        container.store[f"optchain_AAPL_{exp}"] = {
            "id": f"optchain_AAPL_{exp}", "symbol": "AAPL", "doc_type": "options_chain",
            "expiration": exp, "schema_version": 2,
            "calls": {"100.0": _contract(expiration=exp)}, "puts": {}, "_etag": "e1",
        }
        hydrated = store.hydrate("AAPL")
        assert hydrated is not None
        assert "underlying_price" not in hydrated


# ===========================================================================
# T16 — ETag CAS retry
# ===========================================================================

class TestCASRetry:
    @pytest.mark.parametrize("status_code", [409, 412])
    def test_conflict_then_success_converges(self, store, container, status_code):
        exp = _future_exp_key(5)
        chain1 = {"calls": {exp: {"100.0": _contract(bid=1.0, expiration=exp)}}, "puts": {}}
        store.persist("AAPL", chain1)

        # Simulate a concurrent writer winning one race, forcing our first
        # replace_item attempt to hit a stale ETag. Real Cosmos surfaces this
        # as either 409 (Conflict) or 412 (Precondition Failed) — both must
        # retry identically.
        container.force_conflicts = 1
        container.force_conflict_status_code = status_code

        chain2 = {"calls": {exp: {"105.0": _contract(bid=2.0, strike=105.0, expiration=exp)}}, "puts": {}}
        result = store.persist("AAPL", chain2)

        assert result["written"] == 1
        assert result["errors"] == 0
        shard = container.store[f"optchain_AAPL_{exp}"]
        # Union of both cycles' contracts survives the retried merge.
        assert "100.0" in shard["calls"]
        assert "105.0" in shard["calls"]

    @pytest.mark.parametrize("status_code", [409, 412])
    def test_conflicts_exhausted_skips_shard_no_data_loss(self, store, container, status_code):
        exp = _future_exp_key(5)
        chain1 = {"calls": {exp: {"100.0": _contract(bid=1.0, expiration=exp)}}, "puts": {}}
        store.persist("AAPL", chain1)

        # Force more conflicts than the retry budget (<= 3 attempts) allows.
        container.force_conflicts = 10
        container.force_conflict_status_code = status_code

        chain2 = {"calls": {exp: {"105.0": _contract(bid=2.0, strike=105.0, expiration=exp)}}, "puts": {}}
        result = store.persist("AAPL", chain2)

        assert result["conflicts_skipped"] == 1
        assert result["written"] == 0
        assert result["errors"] == 0  # exhaustion is logged+skipped, never an error/raise
        # Prior good data (the 100.0 contract) is untouched — no loss.
        shard = container.store[f"optchain_AAPL_{exp}"]
        assert shard["calls"]["100.0"]["bid"] == 1.0
        assert "105.0" not in shard["calls"]

    def test_two_concurrent_refresh_cycles_union_no_lost_update(self, container):
        """T19-style scenario at the store layer: two independent
        'processes' (two store instances against the same fake backing
        store) each persist a distinct new contract for the same shard;
        the result must be the union of both, not last-writer-wins
        clobbering the other."""
        exp = _future_exp_key(5)
        store_a = OptionsChainStore(container=container)
        store_b = OptionsChainStore(container=container)

        chain_a = {"calls": {exp: {"100.0": _contract(bid=1.0, expiration=exp)}}, "puts": {}}
        store_a.persist("SYM", chain_a)

        chain_b = {"calls": {exp: {"110.0": _contract(bid=4.0, strike=110.0, expiration=exp)}}, "puts": {}}
        store_b.persist("SYM", chain_b)

        shard = container.store[f"optchain_SYM_{exp}"]
        assert "100.0" in shard["calls"]
        assert "110.0" in shard["calls"]


# ===========================================================================
# T17 — sharding + grace-window pruning
# ===========================================================================

class TestPruning:
    def test_prune_deletes_only_past_grace_window(self, store, container):
        today = date(2026, 8, 18)
        # Expired 3 days ago — inside the default 7-day grace window: kept.
        recent_exp = (today - timedelta(days=3)).strftime("%Y%m%d")
        container.store[f"optchain_AAPL_{recent_exp}"] = {
            "id": f"optchain_AAPL_{recent_exp}", "symbol": "AAPL", "doc_type": "options_chain",
            "expiration": recent_exp, "calls": {}, "puts": {}, "_etag": "e1",
        }
        # Expired 10 days ago — past the default 7-day grace window: deleted.
        old_exp = (today - timedelta(days=10)).strftime("%Y%m%d")
        container.store[f"optchain_AAPL_{old_exp}"] = {
            "id": f"optchain_AAPL_{old_exp}", "symbol": "AAPL", "doc_type": "options_chain",
            "expiration": old_exp, "calls": {}, "puts": {}, "_etag": "e2",
        }
        # Future expiration: kept.
        future_exp = (today + timedelta(days=5)).strftime("%Y%m%d")
        container.store[f"optchain_AAPL_{future_exp}"] = {
            "id": f"optchain_AAPL_{future_exp}", "symbol": "AAPL", "doc_type": "options_chain",
            "expiration": future_exp, "calls": {}, "puts": {}, "_etag": "e3",
        }

        deleted = store.prune_expired("AAPL", today_et=today)

        assert deleted == 1
        assert f"optchain_AAPL_{old_exp}" not in container.store
        assert f"optchain_AAPL_{recent_exp}" in container.store
        assert f"optchain_AAPL_{future_exp}" in container.store

    def test_prune_respects_custom_grace_days(self, container):
        s = OptionsChainStore(container=container, expired_shard_grace_days=1)
        today = date(2026, 8, 18)
        exp = (today - timedelta(days=3)).strftime("%Y%m%d")
        container.store[f"optchain_AAPL_{exp}"] = {
            "id": f"optchain_AAPL_{exp}", "symbol": "AAPL", "doc_type": "options_chain",
            "expiration": exp, "calls": {}, "puts": {}, "_etag": "e1",
        }
        deleted = s.prune_expired("AAPL", today_et=today)
        assert deleted == 1

    def test_ttl_alone_never_deletes_a_shard(self, store, container):
        """TTL/staleness is not a persistence-store concept at all — only
        prune_expired (real contract expiration + grace) ever deletes."""
        exp = _future_exp_key(30)
        chain = {"calls": {exp: {"100.0": _contract(expiration=exp)}}, "puts": {}}
        store.persist("AAPL", chain)
        assert f"optchain_AAPL_{exp}" in container.store
        # No amount of "waiting" (there is no TTL parameter on the store at
        # all) causes persist()/hydrate() to delete this shard.
        assert store.hydrate("AAPL") is not None
        assert f"optchain_AAPL_{exp}" in container.store

    def test_purge_deletes_all_shards_ignoring_grace(self, store, container):
        exp1, exp2 = "20260101", "20261231"
        for exp in (exp1, exp2):
            container.store[f"optchain_AAPL_{exp}"] = {
                "id": f"optchain_AAPL_{exp}", "symbol": "AAPL", "doc_type": "options_chain",
                "expiration": exp, "calls": {}, "puts": {}, "_etag": "e1",
            }
        deleted = store.purge("AAPL")
        assert deleted == 2
        assert store.hydrate("AAPL") is None


# ===========================================================================
# Size escape valve
# ===========================================================================

class TestSizeValve:
    def test_undersized_shard_untouched(self, store, container):
        exp = _future_exp_key(5)
        chain = {"calls": {exp: {"100.0": _contract(expiration=exp)}}, "puts": {}}
        store.persist("AAPL", chain)
        shard = container.store[f"optchain_AAPL_{exp}"]
        assert "100.0" in shard["calls"]

    def test_oversized_shard_evicts_dead_carried_contracts_first(self, container):
        s = OptionsChainStore(container=container, max_shard_bytes=2000)
        exp = _future_exp_key(5)
        old_seen = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()

        calls = {}
        # A dead, carried, zero-OI contract far in the past — eligible for eviction.
        calls["50.0"] = _contract(
            strike=50.0, expiration=exp, openInterest=0,
            _meta={"carried": True, "last_seen": old_seen},
        )
        # A live (non-carried, non-zero-OI) contract with a large padding
        # blob to force the shard over the byte limit — must never be evicted.
        calls["100.0"] = _contract(
            strike=100.0, expiration=exp, openInterest=500,
            _meta={"carried": False, "last_seen": datetime.now(timezone.utc).isoformat()},
            _padding="x" * 3000,
        )
        chain = {"calls": {exp: calls}, "puts": {}}

        result = s.persist("AAPL", chain)
        assert result["written"] == 1
        shard = container.store[f"optchain_AAPL_{exp}"]
        assert "50.0" not in shard["calls"]
        assert "100.0" in shard["calls"]

    def test_live_contract_never_evicted_even_if_still_oversized(self, container):
        s = OptionsChainStore(container=container, max_shard_bytes=500)
        exp = _future_exp_key(5)
        calls = {
            "100.0": _contract(
                strike=100.0, expiration=exp, openInterest=500,
                _meta={"carried": False, "last_seen": datetime.now(timezone.utc).isoformat()},
                _padding="x" * 3000,
            )
        }
        chain = {"calls": {exp: calls}, "puts": {}}
        result = s.persist("AAPL", chain)
        assert result["written"] == 1
        shard = container.store[f"optchain_AAPL_{exp}"]
        assert "100.0" in shard["calls"]


# ===========================================================================
# Module-level singleton wiring
# ===========================================================================

class TestSharedStoreWiring:
    def test_set_and_get_shared_store(self):
        fake = OptionsChainStore(enabled=False)
        set_options_chain_store(fake)
        try:
            assert get_options_chain_store() is fake
        finally:
            set_options_chain_store(None)

    def test_get_without_config_degrades_to_disabled(self, monkeypatch):
        """Config() unavailable/invalid (e.g. missing config.yaml or env
        vars) ⇒ the shared store must degrade to memory-only, never raise."""
        set_options_chain_store(None)

        def _boom(*a, **kw):
            raise FileNotFoundError("config.yaml not found")

        monkeypatch.setattr("src.config.Config", _boom)
        try:
            store = get_options_chain_store()
            assert store.is_available() is False
        finally:
            set_options_chain_store(None)
