"""Basher's adversarial seam suite for `GET /api/screener/options`
(`backend/web/app.py`), exercising the REAL FastAPI endpoint together with
a REAL `OptionsChainCache`, REAL `src.options_screener.evaluate_options_screener`,
and (through it) the REAL `src.best_options.evaluate_best_options` -- per
this task's instruction to use real modules across the evaluator/cache/API
seam rather than mocking the seam itself. Only the true edges are faked:
Cosmos (`FakeScreenerCosmos`, no real DB -- deliberately independently
authored here, not imported from `test_best_options_endpoint.py`'s
`FakeCosmos`, per this suite's "avoid mutual fakes" instruction, and
extended with the two batch-read methods (`list_symbols`,
`get_calendar_events`) this endpoint specifically calls) and the
options-chain data providers (`_fetch_yfinance`/`_fetch_tradingview`,
monkeypatched exactly like `tests/test_options_chain_cache.py` and
`tests/test_best_options_endpoint.py` already do -- no network calls).

Design references: `.squad/decisions/inbox/copilot-options-screener-approved.md`,
`.squad/decisions/inbox/linus-options-screener-design.md`.

Hermetic: no network, no real Cosmos, no real LLM. The process-wide
options-chain-cache singleton is saved/restored around every test via an
autouse fixture, matching the established convention.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from src.best_options import evaluate_best_options
from src.best_options_cache import BestOptionsCache, set_best_options_cache
from src.options_chain_cache import (
    OptionsChainCache,
    get_options_chain_cache,
    set_options_chain_cache,
)
from src.options_chain_store import OptionsChainStore
from web.app import app


def _real_now() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> date:
    return _real_now().date()


def _exp_key(days: int) -> str:
    return (_today() + timedelta(days=days)).strftime("%Y%m%d")


def _contract(bid=1.2, ask=1.3, strike=105.0, oi=500):
    # Deliberately no Greeks -- the real cache pipeline's
    # `recompute_derived` is the sole writer of delta/mid/etc (same
    # empirically-chosen strike/iv/underlying as
    # `test_best_options_endpoint.py`'s own fixture, reused verbatim so
    # the recomputed delta is known to land in-band for balanced/high_yield
    # covered-call thresholds without re-deriving that math here).
    mid = round((bid + ask) / 2, 4)
    return {
        "strike": strike, "bid": bid, "ask": ask, "mid": mid, "iv": 0.30,
        "lastPrice": bid, "openInterest": oi, "volume": 10, "inTheMoney": False,
    }


def _sample_chain(symbol="TEST"):
    return {
        "symbol": symbol,
        "timestamp": "2026-08-29T11:00:00Z",
        "underlying_price": 100.0,
        "calls": {_exp_key(20): {"105.0": _contract(bid=1.2, ask=1.3, strike=105.0)}},
        "puts": {_exp_key(20): {"96.0": _contract(bid=1.0, ask=1.05, strike=96.0, oi=2000)}},
    }


class FakeScreenerCosmos:
    """Independently-authored fake (see module docstring). Counts calls to
    `list_symbols`/`get_calendar_events` so tests can assert the endpoint
    reads symbol/calendar metadata O(1) (once total) rather than once per
    symbol."""

    def __init__(self):
        self.symbols_by_name = {}
        self.calendar_events = []
        self.list_symbols_calls = 0
        self.get_calendar_events_calls = 0

    def add_symbol(self, symbol, category="balanced", total_shares=0, exchange="XNYS"):
        # exchange defaults to XNYS so every test symbol passes the
        # compute_options_screener_universe filter; _auto_enrolled absent
        # → is_watchlist_member returns True (treats as manually added).
        self.symbols_by_name[symbol] = {
            "symbol": symbol,
            "exchange": exchange,
            "enrichment": {"category": category},
            "total_shares": total_shares,
        }

    def list_symbols(self):
        self.list_symbols_calls += 1
        return list(self.symbols_by_name.values())

    def get_calendar_events(self):
        self.get_calendar_events_calls += 1
        return list(self.calendar_events)


def _make_cache(monkeypatch, *, yf_chain=None):
    cache = OptionsChainCache(ttl_seconds=1800, store=OptionsChainStore(enabled=False))

    async def _fake_yf(symbol):
        return yf_chain if yf_chain is not None else {"symbol": symbol, "calls": {}, "puts": {}}

    async def _fake_tv(symbol):
        return yf_chain if yf_chain is not None else {"symbol": symbol, "calls": {}, "puts": {}}

    monkeypatch.setattr(cache, "_fetch_yfinance", _fake_yf)
    monkeypatch.setattr(cache, "_fetch_tradingview", _fake_tv)
    return cache


@pytest.fixture(autouse=True)
def _isolate_shared_cache_singleton():
    import src.options_chain_cache as occ_module
    import src.best_options_cache as boc_module
    saved_occ = occ_module._shared_cache
    saved_boc = boc_module._cache_instance
    yield
    set_options_chain_cache(saved_occ)
    set_best_options_cache(saved_boc)


@pytest.fixture
def client_and_cosmos():
    fake_cosmos = FakeScreenerCosmos()
    app.router.on_startup = []
    app.state.cosmos = fake_cosmos
    client = TestClient(app, raise_server_exceptions=False)
    return client, fake_cosmos


def _warm_symbol(monkeypatch, client, cosmos, symbol, chain=None, category="balanced", total_shares=0, cache=None):
    """Populate the cache for `symbol` via a real cold-warm round trip
    (mirrors `test_best_options_endpoint.py`'s `TestColdCacheWarmingBehavior`
    pattern) so screener tests exercise the real cache, not a hand-seeded
    internal dict). A first request against the real endpoint is what
    actually schedules the background refresh (`cache.get_or_hydrate`
    alone never fetches, by design -- F6) -- polling `cache.get` directly
    without first triggering that request would hang forever.

    Pass an already-active `cache` (returned by an earlier call) to warm
    several symbols into the SAME cache instance -- each call otherwise
    replaces the process-wide singleton, which would silently un-warm
    every previously-warmed symbol in a multi-symbol test."""
    cosmos.add_symbol(symbol, category=category, total_shares=total_shares)
    if cache is None:
        cache = _make_cache(monkeypatch, yf_chain=chain or _sample_chain(symbol))
        set_options_chain_cache(cache)
    else:
        monkeypatch.setattr(cache, "_fetch_yfinance", _fake_fetch(chain or _sample_chain(symbol)))
        monkeypatch.setattr(cache, "_fetch_tradingview", _fake_fetch(chain or _sample_chain(symbol)))
    client.get("/api/screener/options")  # triggers schedule_background_refresh for the cold symbol
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if cache.get(symbol) is not None:
            return cache
        time.sleep(0.05)
    raise AssertionError(f"cache never warmed for {symbol}")


def _fake_fetch(chain):
    async def _fetch(symbol):
        return chain
    return _fetch


# ---------------------------------------------------------------------------
# BestOptionsCache injection helpers (precomputed-only path)
# The endpoint is strictly precomputed-only. Tests that exercise row-level
# behaviour (sort, pagination) must inject pre-computed envelopes via
# BestOptionsCache instead of warming OptionsChainCache.
# ---------------------------------------------------------------------------

_SCREENER_NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
_SCREENER_TODAY = _SCREENER_NOW.date()


def _boc_exp_key(days: int) -> str:
    return (_SCREENER_TODAY + timedelta(days=days)).strftime("%Y%m%d")


def _boc_call_chain(symbol: str, *, oi: int = 500) -> dict:
    mid = round((1.2 + 1.3) / 2, 4)
    return {
        "symbol": symbol,
        "timestamp": "2026-09-05T11:00:00Z",
        "underlying_price": 100.0,
        "calls": {
            _boc_exp_key(20): {
                "105.0": {
                    "strike": 105.0, "bid": 1.2, "ask": 1.3, "mid": mid,
                    "iv": 0.30, "delta": 0.25, "gamma": 0.01, "theta": -0.02,
                    "vega": 0.05, "rho": 0.01, "lastPrice": 1.2,
                    "openInterest": oi, "volume": 10, "inTheMoney": False,
                    "_meta": {
                        "quote_asof": "2026-09-05T11:00:00Z",
                        "greeks_valid": True, "greeks_asof": "2026-09-05T11:00:00Z",
                    },
                }
            }
        },
        "puts": {_boc_exp_key(20): {}},
    }


def _boc_make_envelope(chain: dict) -> dict:
    return evaluate_best_options(
        chain, side="both", category="balanced", total_shares=0,
        next_earnings_date=None, ex_dividend_date=None, support_level=None,
        dte_min=0, dte_max=45, now=_SCREENER_NOW,
    )


def _boc_entry(symbol: str, envelope: dict) -> dict:
    return {
        "symbol": symbol, "status": "ok", "envelope": envelope,
        "generation": 1, "computed_at": "2026-09-05T00:00:00Z",
        "chain_stale_at_compute": False,
        "inputs": {"category": "balanced", "total_shares": 0},
        "error": None, "reason": None, "refreshing": False,
        "refresh_started_at": None, "refresh_completed_at": None,
        "refresh_error": None, "chain_refresh_error": None,
    }


def _boc_snap(entries: dict) -> dict:
    return {
        "generation": 1, "entries": entries,
        "cycle_started_at": "2026-09-05T00:00:00Z",
        "cycle_finished_at": "2026-09-05T00:01:00Z",
        "cycle_duration_seconds": 60.0, "trigger": "scheduled",
        "truncated": False,
        "counts": {"ok": len(entries), "stale": 0, "error": 0, "warming": 0},
    }


def _inject_boc(*sym_oi_pairs) -> None:
    """Inject (symbol, oi) pairs into a fresh BestOptionsCache snapshot."""
    entries = {}
    for sym, oi in sym_oi_pairs:
        chain = _boc_call_chain(sym, oi=oi)
        entries[sym] = _boc_entry(sym, _boc_make_envelope(chain))
    cache = BestOptionsCache()
    cache.publish_snapshot(_boc_snap(entries))
    set_best_options_cache(cache)


class TestQueryParamValidation:
    def test_invalid_side_returns_400(self, client_and_cosmos):
        client, cosmos = client_and_cosmos
        resp = client.get("/api/screener/options", params={"side": "both"})
        assert resp.status_code == 400
    def test_invalid_side_returns_400(self, client_and_cosmos):
        client, cosmos = client_and_cosmos
        resp = client.get("/api/screener/options", params={"side": "both"})
        assert resp.status_code == 400

    def test_dte_min_greater_than_dte_max_returns_400(self, client_and_cosmos):
        client, cosmos = client_and_cosmos
        resp = client.get("/api/screener/options", params={"dte_min": 40, "dte_max": 10})
        assert resp.status_code == 400

    def test_min_abs_delta_greater_than_max_abs_delta_returns_400(self, client_and_cosmos):
        client, cosmos = client_and_cosmos
        resp = client.get(
            "/api/screener/options", params={"min_abs_delta": 0.9, "max_abs_delta": 0.1},
        )
        assert resp.status_code == 400

    def test_invalid_sort_field_returns_400(self, client_and_cosmos):
        client, cosmos = client_and_cosmos
        resp = client.get("/api/screener/options", params={"sort": "bogus"})
        assert resp.status_code == 400

    def test_invalid_dir_returns_400(self, client_and_cosmos):
        client, cosmos = client_and_cosmos
        resp = client.get("/api/screener/options", params={"dir": "sideways"})
        assert resp.status_code == 400

    def test_dte_max_beyond_hard_cap_rejected_by_query_validation(self, client_and_cosmos):
        client, cosmos = client_and_cosmos
        resp = client.get("/api/screener/options", params={"dte_max": 61})
        assert resp.status_code == 422

    def test_negative_min_abs_delta_rejected_by_query_validation(self, client_and_cosmos):
        client, cosmos = client_and_cosmos
        resp = client.get("/api/screener/options", params={"min_abs_delta": -0.1})
        assert resp.status_code == 422


class TestCosmosMetadataReadsAreConstant:
    """The approved directive requires batched (O(1)) Cosmos metadata
    reads across the whole symbol universe, not one query per symbol."""

    def test_list_symbols_and_calendar_events_each_called_exactly_once_regardless_of_symbol_count(
        self, client_and_cosmos,
    ):
        """list_symbols is called once per request (O(1) regardless of symbol count).
        get_calendar_events is NOT called by the precomputed-only endpoint — it was
        only called by the now-dead _build_screener_symbol_inputs helper.
        This test verifies the live api_screener_options behavior.
        """
        client, cosmos = client_and_cosmos
        for i in range(6):
            cosmos.add_symbol(f"CMETA{i}")
        # Inject BestOptionsCache entries so the endpoint has something to count
        _inject_boc(*[(f"CMETA{i}", 100 + i * 50) for i in range(6)])
        # Reset counters before the measured request
        cosmos.list_symbols_calls = 0
        cosmos.get_calendar_events_calls = 0
        resp = client.get("/api/screener/options")
        assert resp.status_code == 200
        assert cosmos.list_symbols_calls == 1, (
            "list_symbols must be called exactly once per screener request"
        )
        # The precomputed-only endpoint does NOT call get_calendar_events
        # (_build_screener_symbol_inputs was the only caller, and it is dead code)
        assert cosmos.get_calendar_events_calls == 0, (
            "get_calendar_events must NOT be called by the precomputed-only endpoint"
        )


class TestColdWarmConcurrencyCap:
    """DEAD CODE — the concurrent-warming cap (4 per request) was part of the
    warm-on-request architecture that was replaced by the precomputed-only
    endpoint refactor.  The new endpoint never triggers OptionsChainCache
    refreshes on request; warming is scheduler-only.  The `warming`/`cold`
    counts no longer exist in the response schema.
    These tests are skipped, not removed, to document the removed behavior."""

    @pytest.mark.skip(
        reason=(
            "Dead warm-on-request behavior: api_screener_options is now precomputed-only "
            "and never triggers OptionsChainCache refreshes. The warming/cold response "
            "counts no longer exist. Coverage of the live endpoint behavior lives in "
            "test_options_screener_share_availability.py."
        )
    )
    def test_more_than_four_cold_symbols_only_schedules_four_warming_the_rest_cold(self, client_and_cosmos):
        client, cosmos = client_and_cosmos
        for i in range(6):
            cosmos.add_symbol(f"COLD{i}")
        set_options_chain_cache(OptionsChainCache(ttl_seconds=1800, store=OptionsChainStore(enabled=False)))
        resp = client.get("/api/screener/options")
        assert resp.status_code == 200
        body = resp.json()
        counts = body["symbols"]["counts"]
        assert counts["warming"] == 4
        assert counts["cold"] == 2
        assert counts["ok"] == 0


class TestShareStatusPutSideDefect:
    """Share-availability fields (`share_status`, `total_shares`,
    `active_call_count`, `free_lots`) are covered-CALL-only concepts.
    Put rows must never carry any of these fields.  The legacy `no_shares_held`
    boolean was removed from per-row enrichment entirely
    (`.squad/decisions/inbox/danny-options-screener-share-availability.md`,
    §3.2 / §8.2); it must not appear on any row regardless of side.

    NOTE: `_warm_symbol` is incompatible with the precomputed-only endpoint
    refactor (symbols not in `best_options_cache` contribute zero rows).
    Comprehensive put-side coverage lives in
    `test_options_screener_share_availability.py::TestShareStatusPutSideDefect`
    which uses the correct precomputed injection pattern.  This class is
    retained as a documentation anchor and will be re-enabled once
    `_warm_symbol` is updated to populate `best_options_cache`."""

    @pytest.mark.skip(
        reason=(
            "_warm_symbol populates OptionsChainCache but the screener endpoint "
            "is now precomputed-only (reads best_options_cache).  Full put-side "
            "share-availability assertions live in "
            "test_options_screener_share_availability.py::TestShareStatusPutSideDefect."
        )
    )
    def test_put_side_rows_do_not_carry_share_status_or_no_shares_held(self, client_and_cosmos, monkeypatch):
        client, cosmos = client_and_cosmos
        _warm_symbol(monkeypatch, client, cosmos, "PUTSYM", total_shares=0)
        resp = client.get("/api/screener/options", params={"side": "put"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["rows"]) >= 1, "fixture must actually admit a put row for this assertion to be meaningful"
        for row in body["rows"]:
            assert "share_status" not in row, (
                "share_status is a covered-call-only field; must not appear on put rows"
            )
            assert "no_shares_held" not in row, (
                "no_shares_held was removed from per-row enrichment entirely; "
                "must not appear on any row"
            )


class TestNoCoverableContractsOrMixedNearestMiss:
    @pytest.mark.skip(
        reason=(
            "_warm_symbol populates OptionsChainCache but the screener endpoint "
            "is now precomputed-only (reads best_options_cache). "
            "This test needs rework to inject precomputed envelopes."
        )
    )
    def test_full_payload_has_no_coverable_contracts_key(self, client_and_cosmos, monkeypatch):
        client, cosmos = client_and_cosmos
        _warm_symbol(monkeypatch, client, cosmos, "AAA")
        resp = client.get("/api/screener/options", params={"side": "call"})
        assert resp.status_code == 200
        payload_str = json.dumps(resp.json())
        assert "coverable_contracts" not in payload_str
        assert "no_shares_held" not in payload_str, (
            "no_shares_held was removed from per-row enrichment; must not appear in payload"
        )

    @pytest.mark.skip(
        reason=(
            "_warm_symbol populates OptionsChainCache; the precomputed-only endpoint "
            "reads BestOptionsCache. Needs rewrite to inject a nearest-miss envelope "
            "via BestOptionsCache. Structural invariant (rows ∩ nearest_miss = ∅) is "
            "correct but the fixture mechanism must be updated."
        )
    )
    def test_nearest_miss_rows_never_appear_in_main_rows(self, client_and_cosmos, monkeypatch):
        client, cosmos = client_and_cosmos
        chain = _sample_chain("MISS")
        chain["calls"] = {_exp_key(15): {"120.0": _contract(bid=0.3, ask=0.4, strike=120.0)}}
        _warm_symbol(monkeypatch, client, cosmos, "MISS", chain=chain)
        resp = client.get(
            "/api/screener/options",
            params={"side": "call", "preferences": "Preferred,Acceptable,Avoid"},
        )
        assert resp.status_code == 200
        body = resp.json()
        row_symbols = {r["symbol"] for r in body["rows"]}
        miss_symbols = {m["symbol"] for m in body["nearest_miss"]}
        assert row_symbols.isdisjoint(miss_symbols)


class TestSortAndPagination:
    """Ported from warm-on-request to precomputed injection pattern.
    Two symbols with distinct open_interest values verify that sort and
    pagination operate on the precomputed envelope rows, not raw chain data.
    """

    def test_non_default_sort_reorders_by_the_requested_column(self, client_and_cosmos):
        """open_interest asc: SORTLO (oi=10) before SORTHI (oi=900)."""
        client, cosmos = client_and_cosmos
        cosmos.add_symbol("SORTLO")
        cosmos.add_symbol("SORTHI")
        _inject_boc(("SORTLO", 10), ("SORTHI", 900))
        resp = client.get(
            "/api/screener/options",
            params={"side": "call", "symbols": "SORTLO,SORTHI", "sort": "open_interest", "dir": "asc"},
        )
        assert resp.status_code == 200
        body = resp.json()
        ois = [r["open_interest"] for r in body["rows"]]
        assert len(ois) >= 2, "Both symbols must produce rows"
        assert ois == sorted(ois), f"Rows must be ascending by open_interest, got {ois}"
        assert ois[0] == 10 and ois[-1] == 900

    def test_offset_and_limit_still_apply_after_a_non_default_resort(self, client_and_cosmos):
        """limit=1 offset=0 with asc OI sort returns PGNLO (oi=100), has_more=True."""
        client, cosmos = client_and_cosmos
        cosmos.add_symbol("PGNLO")
        cosmos.add_symbol("PGNHI")
        _inject_boc(("PGNLO", 100), ("PGNHI", 800))
        resp = client.get(
            "/api/screener/options",
            params={
                "side": "call", "symbols": "PGNLO,PGNHI",
                "sort": "open_interest", "dir": "asc", "limit": 1, "offset": 0,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["rows"]) == 1
        assert body["rows"][0]["open_interest"] == 100
        assert body["pagination"]["has_more"] is True


class TestGapPercentageFilters:
    """Endpoint tests for gap percentage filters."""

    def test_min_gap_pct_greater_than_max_gap_pct_returns_400(self, client_and_cosmos):
        """min_gap_pct > max_gap_pct is rejected with 400."""
        client, cosmos = client_and_cosmos
        resp = client.get(
            "/api/screener/options",
            params={"side": "call", "min_gap_pct": 10.0, "max_gap_pct": 5.0},
        )
        assert resp.status_code == 400
        assert "min_gap_pct must be <= max_gap_pct" in resp.json()["error"]

    def test_gap_filter_api_bounds(self, client_and_cosmos):
        """API enforces bounds on gap filter parameters."""
        client, cosmos = client_and_cosmos

        # Below lower bound
        resp = client.get(
            "/api/screener/options",
            params={"side": "call", "min_gap_pct": -150.0},
        )
        assert resp.status_code == 422  # FastAPI validation error

        # Above upper bound
        resp = client.get(
            "/api/screener/options",
            params={"side": "call", "max_gap_pct": 250.0},
        )
        assert resp.status_code == 422  # FastAPI validation error


# ---------------------------------------------------------------------------
# Options Screener Universe enforcement at the endpoint level.
# Ref: danny-options-screener-universe-contract.md §2.2 (filter-before-work)
#
# THESE TESTS WILL FAIL until Linus wires compute_options_screener_universe
# into _build_screener_symbol_inputs (app.py §2.2).
# ---------------------------------------------------------------------------

class TestUniverseFilterEnforcement:
    """Assert that the screener universe predicate is enforced at the endpoint
    level: non-US symbols and zero-share non-watchlist symbols must never
    appear in rows or screener counts (summary.total / warming / error).

    FakeScreenerCosmos is extended here with exchange/watchlist fields that
    compute_options_screener_universe needs.  The base `add_symbol` helper
    only stores enrichment/total_shares — we add `add_ineligible_symbol` for
    the failure cases.
    """

    def _add_eligible(self, cosmos, symbol):
        """Seed a US watchlist-member symbol (no cache warm needed for count tests)."""
        cosmos.symbols_by_name[symbol] = {
            "symbol": symbol,
            "exchange": "XNYS",
            "_auto_enrolled": False,   # manually added → watchlist member → eligible
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
            "telegram_notifications_enabled": False,
            "enrichment": {"category": "balanced"},
            "total_shares": 0,
        }

    def _add_ineligible(self, cosmos, symbol, exchange="XMAD", shares=100):
        """Add a non-US or zero-share-no-watchlist symbol (should be excluded)."""
        cosmos.symbols_by_name[symbol] = {
            "symbol": symbol,
            "exchange": exchange,
            "_auto_enrolled": True,
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
            "telegram_notifications_enabled": False,
            "enrichment": {"category": "balanced"},
            "total_shares": shares,
        }

    def test_osu_endpoint_non_us_symbol_absent_from_rows(self, client_and_cosmos, monkeypatch):
        """OSU endpoint: a non-US (XMAD) symbol must not appear in screener counts.
        Tests summary.total rather than rows (avoids cache-warm dependency).
        """
        client, cosmos = client_and_cosmos
        self._add_eligible(cosmos, "AAPL")
        self._add_ineligible(cosmos, "ACS", exchange="XMAD", shares=500)

        resp = client.get("/api/screener/options", params={"side": "call"})
        assert resp.status_code == 200
        body = resp.json()
        summary = body.get("summary", {})

        # ACS (XMAD) must not be counted at all in the screener universe
        total = (summary.get("total") or 0) + (summary.get("warming") or 0) + (summary.get("error") or 0)
        assert total <= 1, (
            f"OSU endpoint: summary counts must include at most 1 symbol (AAPL). "
            f"Non-US ACS must be excluded. summary={summary}"
        )
        # Also verify ACS doesn't appear in any row
        row_symbols = {r.get("symbol") for r in body.get("rows", [])}
        assert "ACS" not in row_symbols, (
            "OSU endpoint: non-US XMAD symbol 'ACS' must not appear in screener rows."
        )

    def test_osu_endpoint_non_us_symbol_absent_from_summary_counts(
        self, client_and_cosmos, monkeypatch
    ):
        """OSU endpoint: non-US symbol (XSWX) must not be counted in summary.
        """
        client, cosmos = client_and_cosmos
        self._add_eligible(cosmos, "MSFT")
        self._add_ineligible(cosmos, "NESN", exchange="XSWX", shares=200)

        resp = client.get("/api/screener/options", params={"side": "call"})
        assert resp.status_code == 200
        body = resp.json()
        summary = body.get("summary", {})

        # NESN (XSWX) must not appear in total/warming/error
        total = (summary.get("total") or 0) + (summary.get("warming") or 0) + (summary.get("error") or 0)
        assert total <= 1, (
            f"OSU endpoint: only MSFT (US, watchlist member) should be counted. "
            f"NESN (XSWX) must be excluded. summary={summary}"
        )

    def test_osu_endpoint_zero_share_no_watchlist_excluded_from_counts(
        self, client_and_cosmos, monkeypatch
    ):
        """OSU endpoint: US symbol with 0 shares and no watchlist membership
        must not appear in summary counts.
        """
        client, cosmos = client_and_cosmos
        self._add_eligible(cosmos, "ABBV")  # US, watchlist member → IN

        # US but zero shares, auto-enrolled, no watchlist — must be excluded
        cosmos.symbols_by_name["ZERO"] = {
            "symbol": "ZERO",
            "exchange": "XNYS",
            "_auto_enrolled": True,   # NOT a watchlist member
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
            "telegram_notifications_enabled": False,
            "enrichment": {"category": "balanced"},
            "total_shares": 0,
        }

        resp = client.get("/api/screener/options", params={"side": "call"})
        assert resp.status_code == 200
        body = resp.json()
        summary = body.get("summary", {})

        total = (summary.get("total") or 0) + (summary.get("warming") or 0) + (summary.get("error") or 0)
        assert total <= 1, (
            f"OSU endpoint: US zero-share auto-enrolled ZERO must not be counted. "
            f"Only ABBV (watchlist member) should appear. summary={summary}"
        )
