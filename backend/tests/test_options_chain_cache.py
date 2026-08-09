"""
Test suite for the centralized options chain cache (src/options_chain_cache.py).

Covers:
  - Source merge precedence between yfinance (base) and TradingView (overlay)
  - Field-level last-known-good merge: a fresh zero/null/NaN quote value
    never overwrites a previously stored valid non-zero value
  - First-fetch zeros are preserved as-is when there is no prior data
  - Stale cache entries remain readable (never evicted purely by TTL)
  - Actual expired-contract (past expiration date) pruning

Hermetic: no network calls. `_fetch_yfinance`/`_fetch_tradingview` are
monkeypatched per test so `refresh()` exercises only the merge/cache logic.

No pytest-asyncio dependency: coroutines are driven with an isolated event
loop via `run_async()`, matching the pattern used elsewhere in this test
suite (see tests/test_summary_paused.py) rather than `asyncio.run()`, which
sets the global policy's "_set_called" flag and can interfere with other
tests relying on `asyncio.get_event_loop()`.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.options_chain_cache import (
    OptionsChainCache,
    _is_invalid_quote_value,
    _merge_contract_fields,
    _prune_expired_expirations,
)


def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _contract(**overrides):
    base = {
        "contractSymbol": "AAPL240101C00100000",
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
        "lastTradeDate": "2024-01-01T00:00:00Z",
        "inTheMoney": False,
        "expiration": "20240101",
        "option_type": "call",
    }
    base.update(overrides)
    return base


def _future_exp_key(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y%m%d")


def _past_exp_key(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y%m%d")


def _empty_chain(symbol="TEST"):
    return {"symbol": symbol, "timestamp": "2024-01-01T00:00:00Z", "calls": {}, "puts": {}}


def _patch_sources(monkeypatch, cache_obj, yf_chain, tv_chain):
    async def _fake_yf(symbol):
        return yf_chain

    async def _fake_tv(symbol):
        return tv_chain

    monkeypatch.setattr(cache_obj, "_fetch_yfinance", _fake_yf)
    monkeypatch.setattr(cache_obj, "_fetch_tradingview", _fake_tv)


# ===========================================================================
# _is_invalid_quote_value / _merge_contract_fields (unit-level)
# ===========================================================================

class TestInvalidQuoteValue:
    def test_none_is_invalid(self):
        assert _is_invalid_quote_value(None) is True

    def test_zero_is_invalid(self):
        assert _is_invalid_quote_value(0) is True
        assert _is_invalid_quote_value(0.0) is True

    def test_nan_is_invalid(self):
        assert _is_invalid_quote_value(float("nan")) is True

    def test_non_numeric_is_invalid(self):
        assert _is_invalid_quote_value("n/a") is True

    def test_positive_value_is_valid(self):
        assert _is_invalid_quote_value(1.5) is False

    def test_negative_value_is_valid(self):
        # Not realistic for bid/ask, but the helper only judges zero/NaN/None
        assert _is_invalid_quote_value(-0.5) is False


class TestMergeContractFields:
    def test_no_prior_contract_returns_new_unchanged(self):
        new = _contract(bid=0.0, ask=0.0)
        merged = _merge_contract_fields(None, new)
        assert merged == new
        assert merged["bid"] == 0.0

    def test_zero_new_bid_falls_back_to_prior_valid_bid(self):
        old = _contract(bid=1.5, ask=1.7)
        new = _contract(bid=0.0, ask=0.0)
        merged = _merge_contract_fields(old, new)
        assert merged["bid"] == 1.5
        assert merged["ask"] == 1.7

    def test_valid_new_value_takes_precedence_over_old(self):
        old = _contract(bid=1.0, ask=1.2)
        new = _contract(bid=2.0, ask=2.2)
        merged = _merge_contract_fields(old, new)
        assert merged["bid"] == 2.0
        assert merged["ask"] == 2.2

    def test_non_quote_fields_always_come_from_new(self):
        old = _contract(volume=999, openInterest=888, lastTradeDate="2020-01-01T00:00:00Z")
        new = _contract(volume=0, openInterest=0, lastTradeDate=None)
        merged = _merge_contract_fields(old, new)
        # Zero volume/openInterest is legitimate — must come from `new`, not preserved
        assert merged["volume"] == 0
        assert merged["openInterest"] == 0
        assert merged["lastTradeDate"] is None

    def test_partial_preservation_field_by_field(self):
        old = _contract(bid=1.0, ask=1.2, iv=0.3)
        new = _contract(bid=0.0, ask=1.5, iv=0.0)
        merged = _merge_contract_fields(old, new)
        assert merged["bid"] == 1.0       # preserved (new was zero)
        assert merged["ask"] == 1.5       # new value used (valid)
        assert merged["iv"] == 0.3        # preserved (new was zero)


class TestPruneExpiredExpirations:
    def test_past_expiration_is_dropped(self):
        past_key = _past_exp_key(5)
        future_key = _future_exp_key(5)
        chain = {
            "calls": {past_key: {"100.0": _contract()}, future_key: {"100.0": _contract()}},
            "puts": {},
        }
        result = _prune_expired_expirations(chain, "TEST")
        assert past_key not in result["calls"]
        assert future_key in result["calls"]

    def test_unparseable_key_left_untouched(self):
        chain = {"calls": {"not-a-date": {"100.0": _contract()}}, "puts": {}}
        result = _prune_expired_expirations(chain, "TEST")
        assert "not-a-date" in result["calls"]


# ===========================================================================
# OptionsChainCache.refresh() — end-to-end merge behavior
# ===========================================================================

@pytest.fixture
def cache():
    return OptionsChainCache(ttl_seconds=1800)


class TestSourceMergePrecedence:
    def test_tv_overwrites_when_valid(self, cache, monkeypatch):
        exp = _future_exp_key(5)
        yf_chain = {"symbol": "TEST", "calls": {exp: {"100.0": _contract(bid=1.0, ask=1.2)}}, "puts": {}}
        tv_chain = {"symbol": "TEST", "calls": {exp: {"100.0": _contract(bid=1.5, ask=1.7)}}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain, tv_chain)

        result = json.loads(run_async(cache.refresh("TEST")))
        assert result["calls"][exp]["100.0"]["bid"] == 1.5
        assert result["calls"][exp]["100.0"]["ask"] == 1.7

    def test_tv_zero_does_not_overwrite_valid_yfinance(self, cache, monkeypatch):
        exp = _future_exp_key(5)
        yf_chain = {"symbol": "TEST", "calls": {exp: {"100.0": _contract(bid=1.0, ask=1.2)}}, "puts": {}}
        tv_chain = {"symbol": "TEST", "calls": {exp: {"100.0": _contract(bid=0.0, ask=0.0)}}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain, tv_chain)

        result = json.loads(run_async(cache.refresh("TEST")))
        assert result["calls"][exp]["100.0"]["bid"] == 1.0
        assert result["calls"][exp]["100.0"]["ask"] == 1.2

    def test_tv_adds_new_strikes_yfinance_is_missing(self, cache, monkeypatch):
        exp = _future_exp_key(5)
        yf_chain = {"symbol": "TEST", "calls": {}, "puts": {}}
        tv_chain = {"symbol": "TEST", "calls": {exp: {"105.0": _contract(strike=105.0, bid=2.0, ask=2.2)}}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain, tv_chain)

        result = json.loads(run_async(cache.refresh("TEST")))
        assert result["calls"][exp]["105.0"]["bid"] == 2.0


class TestBeyondFiveExpirations:
    def test_yfinance_zero_beyond_tv_coverage_no_prior_data(self, cache, monkeypatch):
        """TV (near-term only) covers the first few expirations; yfinance
        zeros for later expirations remain zero on a first fetch (no prior
        good data exists to fall back to) — matches observed production
        behavior beyond ~5 expirations before this fix."""
        near_exps = [_future_exp_key(d) for d in (5, 10, 15)]
        far_exps = [_future_exp_key(d) for d in (40, 70, 100)]

        yf_calls = {}
        for exp in near_exps + far_exps:
            yf_calls[exp] = {"100.0": _contract(bid=0.0, ask=0.0, expiration=exp)}

        tv_calls = {}
        for exp in near_exps:
            tv_calls[exp] = {"100.0": _contract(bid=1.0, ask=1.2, expiration=exp)}

        yf_chain = {"symbol": "TEST", "calls": yf_calls, "puts": {}}
        tv_chain = {"symbol": "TEST", "calls": tv_calls, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain, tv_chain)

        result = json.loads(run_async(cache.refresh("TEST")))
        for exp in near_exps:
            assert result["calls"][exp]["100.0"]["bid"] == 1.0
        for exp in far_exps:
            assert result["calls"][exp]["100.0"]["bid"] == 0.0

    def test_far_expiration_backfilled_from_last_known_good(self, cache, monkeypatch):
        """Once a valid value has been observed for a far expiration on a
        prior refresh, a later refresh returning zero for it must not wipe
        it out — this is the core regression this fix addresses."""
        far_exp = _future_exp_key(100)

        # First refresh: TV doesn't reach this far out, but yfinance had a
        # real quote that cycle.
        yf_chain_1 = {"symbol": "TEST", "calls": {far_exp: {"100.0": _contract(bid=3.0, ask=3.2, expiration=far_exp)}}, "puts": {}}
        tv_chain_1 = {"symbol": "TEST", "calls": {}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain_1, tv_chain_1)
        first = json.loads(run_async(cache.refresh("TEST")))
        assert first["calls"][far_exp]["100.0"]["bid"] == 3.0

        # Second refresh: yfinance now returns zeros for the same contract
        # (rate limit / thin quote / stale snapshot), TV still doesn't cover it.
        yf_chain_2 = {"symbol": "TEST", "calls": {far_exp: {"100.0": _contract(bid=0.0, ask=0.0, expiration=far_exp)}}, "puts": {}}
        tv_chain_2 = {"symbol": "TEST", "calls": {}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain_2, tv_chain_2)
        second = json.loads(run_async(cache.refresh("TEST")))

        assert second["calls"][far_exp]["100.0"]["bid"] == 3.0
        assert second["calls"][far_exp]["100.0"]["ask"] == 3.2


class TestLastKnownGoodMerge:
    def test_first_fetch_zeros_preserved_as_is(self, cache, monkeypatch):
        """No prior cache entry exists — zeros from a first fetch are not
        fabricated into fake non-zero values."""
        exp = _future_exp_key(5)
        yf_chain = {"symbol": "TEST", "calls": {exp: {"100.0": _contract(bid=0.0, ask=0.0)}}, "puts": {}}
        tv_chain = {"symbol": "TEST", "calls": {}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain, tv_chain)

        result = json.loads(run_async(cache.refresh("TEST")))
        assert result["calls"][exp]["100.0"]["bid"] == 0.0
        assert result["calls"][exp]["100.0"]["ask"] == 0.0

    def test_contract_missing_from_fresh_fetch_carried_forward(self, cache, monkeypatch):
        """If a contract present in the previous cache is entirely absent
        from both fresh sources this cycle, the last-known-good contract is
        carried forward rather than disappearing."""
        exp = _future_exp_key(5)
        yf_chain_1 = {"symbol": "TEST", "calls": {exp: {"100.0": _contract(bid=1.0, ask=1.2)}}, "puts": {}}
        tv_chain_1 = {"symbol": "TEST", "calls": {}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain_1, tv_chain_1)
        run_async(cache.refresh("TEST"))

        # Next cycle: source omits this contract/expiration entirely.
        yf_chain_2 = {"symbol": "TEST", "calls": {}, "puts": {}}
        tv_chain_2 = {"symbol": "TEST", "calls": {}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain_2, tv_chain_2)
        result = json.loads(run_async(cache.refresh("TEST")))

        assert result["calls"][exp]["100.0"]["bid"] == 1.0

    def test_volume_and_open_interest_not_preserved_when_zero(self, cache, monkeypatch):
        """Volume/openInterest legitimately go to zero — they must always
        reflect the freshest fetch, never be pinned to a stale prior value."""
        exp = _future_exp_key(5)
        yf_chain_1 = {"symbol": "TEST", "calls": {exp: {"100.0": _contract(volume=500, openInterest=1000)}}, "puts": {}}
        tv_chain_1 = {"symbol": "TEST", "calls": {}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain_1, tv_chain_1)
        run_async(cache.refresh("TEST"))

        yf_chain_2 = {"symbol": "TEST", "calls": {exp: {"100.0": _contract(volume=0, openInterest=0)}}, "puts": {}}
        tv_chain_2 = {"symbol": "TEST", "calls": {}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain_2, tv_chain_2)
        result = json.loads(run_async(cache.refresh("TEST")))

        assert result["calls"][exp]["100.0"]["volume"] == 0
        assert result["calls"][exp]["100.0"]["openInterest"] == 0


class TestActualExpirationPruning:
    def test_past_expiration_dropped_after_merge(self, cache, monkeypatch):
        past_exp = _past_exp_key(3)
        future_exp = _future_exp_key(3)
        yf_chain = {
            "symbol": "TEST",
            "calls": {
                past_exp: {"100.0": _contract(expiration=past_exp)},
                future_exp: {"100.0": _contract(expiration=future_exp)},
            },
            "puts": {},
        }
        tv_chain = {"symbol": "TEST", "calls": {}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain, tv_chain)

        result = json.loads(run_async(cache.refresh("TEST")))
        assert past_exp not in result["calls"]
        assert future_exp in result["calls"]

    def test_previously_cached_past_expiration_pruned_on_next_refresh(self, cache, monkeypatch):
        """A contract that was valid and cached, but whose expiration date
        has since passed, must not be carried forward by the last-known-good
        merge — cache TTL/staleness and actual contract expiry are distinct."""
        soon_to_expire = _future_exp_key(1)
        yf_chain_1 = {"symbol": "TEST", "calls": {soon_to_expire: {"100.0": _contract(bid=1.0, expiration=soon_to_expire)}}, "puts": {}}
        tv_chain_1 = {"symbol": "TEST", "calls": {}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain_1, tv_chain_1)
        run_async(cache.refresh("TEST"))

        # Simulate time passing: manually rewrite the exp key to be in the past
        # in the stored cache entry, then refresh again with sources empty.
        with cache._lock:
            entry = cache._store["TEST"]
            chain = json.loads(entry["chain_json"])
            past_exp = _past_exp_key(1)
            chain["calls"][past_exp] = chain["calls"].pop(soon_to_expire)
            chain["calls"][past_exp]["100.0"]["expiration"] = past_exp
            entry["chain_json"] = json.dumps(chain)

        yf_chain_2 = {"symbol": "TEST", "calls": {}, "puts": {}}
        tv_chain_2 = {"symbol": "TEST", "calls": {}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain_2, tv_chain_2)
        result = json.loads(run_async(cache.refresh("TEST")))

        assert past_exp not in result["calls"]


class TestStaleCacheReadability:
    def test_get_returns_data_even_when_ttl_expired(self, cache):
        with cache._lock:
            cache._store["TEST"] = {
                "chain_json": json.dumps(_empty_chain()),
                "cached_at": -10_000.0,  # far in the past relative to monotonic()
            }
        assert cache.get("TEST") is not None

    def test_is_stale_true_after_ttl(self, cache):
        with cache._lock:
            cache._store["TEST"] = {
                "chain_json": json.dumps(_empty_chain()),
                "cached_at": -10_000.0,
            }
        assert cache.is_stale("TEST") is True

    def test_is_stale_true_for_missing_entry(self, cache):
        assert cache.is_stale("NEVER_FETCHED") is True

    def test_true_cache_miss_returns_none(self, cache):
        assert cache.get("NEVER_FETCHED") is None

    def test_get_or_load_async_returns_stale_data_immediately(self, cache, monkeypatch):
        exp = _future_exp_key(5)
        yf_chain = {"symbol": "TEST", "calls": {exp: {"100.0": _contract(bid=1.0)}}, "puts": {}}
        tv_chain = {"symbol": "TEST", "calls": {}, "puts": {}}
        _patch_sources(monkeypatch, cache, yf_chain, tv_chain)
        run_async(cache.refresh("TEST"))

        # Force staleness
        with cache._lock:
            cache._store["TEST"]["cached_at"] -= 10_000.0

        async def _scenario():
            # Background refresh should not block the read from returning
            # immediately with the last-known-good (stale) data.
            result_json = await cache.get_or_load_async("TEST")
            # Give the scheduled background refresh task a chance to run
            # to completion so it doesn't leak into other tests.
            pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            return result_json

        result = json.loads(run_async(_scenario()))
        assert result["calls"][exp]["100.0"]["bid"] == 1.0
