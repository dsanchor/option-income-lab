"""Livingston's contract lock for `GET /api/symbols/{symbol}/best-options`
vs. `frontend/src/types/best-options.ts` (Basher's reviewer verdict,
`.squad/decisions/inbox/basher-best-options-review.md`, defects D2/D3).

Real-module seam test: real `OptionsChainCache` + real `evaluate_best_options`
  + real FastAPI endpoint via `TestClient`, only the true edges faked
  (`FakeCosmos`, monkeypatched provider fetchers -- no network). This is the
  independently-authored fixture set for this file (not imported from
  `test_best_options_endpoint.py`) -- consistent with the squad's "avoid
  mutual fakes" convention -- and its exact purpose is to pin the JSON KEY
  SHAPE the frontend types must mirror, since a TypeScript compile alone
  cannot catch a type declared wrong against a shape it never observes
  (Basher's finding: `npx tsc --noEmit` passed with 0 errors while the
  types were still wrong).

What broke acceptance: `frontend/src/types/best-options.ts` typed
`thresholds`/`thresholds_source`/`skill_reference`/`premium.basis` as flat
values, and `BestOptionsParams.tsx` read them as such
(`parameters.thresholds.delta_lo.toFixed(2)`) -- but the real evaluator
necessarily nests all four per `{call, put}` (CC and CSP thresholds
genuinely differ per category), a `TypeError` on first render (D2). Two
more section-level fields the evaluator reports
(`excluded_by_delta_band`, `coverable_contracts`/`no_shares_held`) were
never present on the frontend type or read anywhere in the UI, and the
page's own "0 shares held" banner checked a per-row flag that
`best_options.py` never sets (`no_shares_held` is section-level only,
design §5's "capital" row) so it could never render (D3).

2026-08-29 follow-up (`.squad/decisions/inbox/danny-best-options-45d-design.md`,
ACCEPTED, superseding the original design's `[0, 49]` default window):
the default DTE window moved to `[0, 45]` inclusive to match the agents'
own hard cap (`rule_evaluator._dte_cap_rule`'s `DTE <= 45`,
`best_options.SYSTEM_DTE_CAP`), and `coverable_contracts` was removed
entirely (from domain output, API contract, frontend type, and UI) --
`no_shares_held` is preserved as an independent, section-level boolean
computed directly from `total_shares`, not derived from the now-deleted
count. This file's `coverable_contracts` assertions are removed
accordingly, and a new test class below locks the `[0, 45]` inclusive
default boundary at the same real cache+evaluator+endpoint seam.
"""
from __future__ import annotations
import json
from datetime import date, datetime, timedelta, timezone

from starlette.testclient import TestClient

from src.options_chain_cache import (
    OptionsChainCache,
    set_options_chain_cache,
)
from src.options_chain_store import OptionsChainStore
from web.app import app

import pytest


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _exp_key(days: int) -> str:
    return (_today() + timedelta(days=days)).strftime("%Y%m%d")


def _contract(bid, ask, strike, oi=500):
    mid = round((bid + ask) / 2, 4)
    return {
        "strike": strike, "bid": bid, "ask": ask, "mid": mid, "iv": 0.30,
        "lastPrice": bid, "openInterest": oi, "volume": 10, "inTheMoney": False,
    }


def _sample_chain(symbol="CONTRACT"):
    # Strikes/IV chosen (same recipe as test_best_options_endpoint.py's
    # `_contract`) so the cache pipeline's recomputed delta lands in-band
    # for the "balanced" category on both sides, guaranteeing at least one
    # real row per side rather than an all-excluded/empty table.
    return {
        "symbol": symbol,
        "timestamp": "2026-08-29T11:00:00Z",
        "underlying_price": 100.0,
        "calls": {_exp_key(20): {"105.0": _contract(bid=1.2, ask=1.3, strike=105.0)}},
        "puts": {_exp_key(20): {"96.0": _contract(bid=1.0, ask=1.05, strike=96.0)}},
    }


class FakeCosmos:
    """Independently-authored fake, scoped to only what this endpoint reads."""

    def __init__(self):
        self.symbols = {}

    def get_symbol(self, symbol):
        return self.symbols.get(symbol)

    def get_next_earnings_date(self, symbol):
        return None

    def get_next_calendar_event_date(self, symbol, event_type):
        return None


def _make_cache(monkeypatch, chain):
    cache = OptionsChainCache(ttl_seconds=1800, store=OptionsChainStore(enabled=False))

    async def _fake_yf(symbol):
        return chain

    async def _fake_tv(symbol):
        return chain

    monkeypatch.setattr(cache, "_fetch_yfinance", _fake_yf)
    monkeypatch.setattr(cache, "_fetch_tradingview", _fake_tv)
    return cache


@pytest.fixture(autouse=True)
def _isolate_shared_cache_singleton():
    import src.options_chain_cache as occ_module
    saved = occ_module._shared_cache
    yield
    set_options_chain_cache(saved)


@pytest.fixture
def client_and_cosmos():
    fake_cosmos = FakeCosmos()
    app.router.on_startup = []
    app.state.cosmos = fake_cosmos
    client = TestClient(app, raise_server_exceptions=False)
    return client, fake_cosmos


class TestParametersNestedPerSide:
    """`thresholds`/`thresholds_source`/`skill_reference`/`premium.basis` are
    nested `{call, put}` -- CC/CSP thresholds genuinely differ per category, and
    `side=both` shares one `parameters` panel, so a flat shape is not coherent
    (Basher D2)."""

    def test_thresholds_and_sources_are_nested_by_side(self, client_and_cosmos, monkeypatch):
        client, fake_cosmos = client_and_cosmos
        fake_cosmos.symbols["CONTRACT"] = {"enrichment": {"category": "balanced"}, "total_shares": 300}
        cache = _make_cache(monkeypatch, _sample_chain())
        cache.get_or_load("CONTRACT")  # warm synchronously before any request
        set_options_chain_cache(cache)

        resp = client.get("/api/symbols/CONTRACT/best-options?support_level=100.0")
        assert resp.status_code == 200
        params = resp.json()["parameters"]

        for field in ("thresholds", "thresholds_source", "skill_reference"):
            value = params[field]
            assert isinstance(value, dict), f"{field} must be a dict, not {type(value)}"
            assert set(value.keys()) == {"call", "put"}, f"{field} must be keyed by {{call, put}}"

        # CC and CSP thresholds genuinely differ in the "balanced" category
        # (rule_evaluator.CATEGORY_THRESHOLDS_CC/_CSP) -- a flat shape could
        # never have represented both correctly at once.
        assert params["thresholds"]["call"]["premium_min_pct"] != params["thresholds"]["put"]["premium_min_pct"]
        for side in ("call", "put"):
            th = params["thresholds"][side]
            for key in ("delta_lo", "delta_hi", "premium_min_pct", "premium_wait_pct", "iv_rank_min"):
                assert key in th

        assert isinstance(params["premium"]["basis"], dict)
        assert params["premium"]["basis"] == {"call": "underlying_price", "put": "strike"}

    def test_frontend_type_accessors_do_not_throw(self, client_and_cosmos, monkeypatch):
        """Regression lock for the exact crash path Basher found:
        `parameters.thresholds.delta_lo.toFixed(2)` on a `{call, put}`
        object is `undefined.toFixed`, a `TypeError` on first render. This
        can't execute TSX, but it can assert the real payload supports
        exactly the accessors `BestOptionsParams.tsx` now uses
        (`parameters.thresholds.call.delta_lo`, etc.) and would fail loudly
        if a future change flattened the shape back out."""
        client, fake_cosmos = client_and_cosmos
        fake_cosmos.symbols["CONTRACT"] = {"enrichment": {"category": "balanced"}, "total_shares": 300}
        cache = _make_cache(monkeypatch, _sample_chain())
        cache.get_or_load("CONTRACT")  # warm synchronously before any request
        set_options_chain_cache(cache)

        params = client.get("/api/symbols/CONTRACT/best-options?support_level=100.0").json()["parameters"]
        # These are exactly the accessor chains BestOptionsParams.tsx evaluates.
        assert isinstance(params["thresholds"]["call"]["delta_lo"], float)
        assert isinstance(params["thresholds"]["put"]["delta_hi"], float)
        assert isinstance(params["thresholds_source"]["call"], str)
        assert isinstance(params["skill_reference"]["put"], str)
        assert isinstance(params["premium"]["basis"]["call"], str)


class TestSectionLevelTransparencyFields:
    """`excluded_by_delta_band` (both sides) and `no_shares_held` (call
    side only) are the count-metadata transparency surface the binding
    visual-consistency/excluded-contracts directive requires the UI to
    expose (Basher D3). `coverable_contracts` itself was removed by the
    2026-08-29 45d-alignment design -- must not appear anywhere in the
    response, on either side -- while `no_shares_held` remains and is
    now computed directly from `total_shares`, independent of the
    deleted count."""

    def test_excluded_by_delta_band_present_both_sides(self, client_and_cosmos, monkeypatch):
        client, fake_cosmos = client_and_cosmos
        fake_cosmos.symbols["CONTRACT"] = {"enrichment": {"category": "balanced"}, "total_shares": 300}
        cache = _make_cache(monkeypatch, _sample_chain())
        cache.get_or_load("CONTRACT")  # warm synchronously before any request
        set_options_chain_cache(cache)

        body = client.get("/api/symbols/CONTRACT/best-options?support_level=100.0").json()
        assert isinstance(body["calls"]["excluded_by_delta_band"], int)
        assert isinstance(body["puts"]["excluded_by_delta_band"], int)

    def test_no_shares_held_is_call_only_and_coverable_contracts_absent(self, client_and_cosmos, monkeypatch):
        client, fake_cosmos = client_and_cosmos
        fake_cosmos.symbols["CONTRACT"] = {"enrichment": {"category": "balanced"}, "total_shares": 300}
        cache = _make_cache(monkeypatch, _sample_chain())
        cache.get_or_load("CONTRACT")  # warm synchronously before any request
        set_options_chain_cache(cache)

        body = client.get("/api/symbols/CONTRACT/best-options?support_level=100.0").json()
        assert body["calls"]["no_shares_held"] is False
        # Never on the put section -- a per-row `no_shares_held` flag would be
        # the wrong place to look for this (the old, broken UI check).
        assert "no_shares_held" not in body["puts"]
        for row in body["calls"]["rows"]:
            assert "no_shares_held" not in row["flags"]
        # `coverable_contracts` was removed entirely -- must not survive on
        # either side, not as a value, not as a null placeholder.
        assert "coverable_contracts" not in body["calls"]
        assert "coverable_contracts" not in body["puts"]

    def test_zero_shares_sets_no_shares_held_true(self, client_and_cosmos, monkeypatch):
        client, fake_cosmos = client_and_cosmos
        fake_cosmos.symbols["CONTRACT"] = {"enrichment": {"category": "balanced"}, "total_shares": 0}
        cache = _make_cache(monkeypatch, _sample_chain())
        cache.get_or_load("CONTRACT")  # warm synchronously before any request
        set_options_chain_cache(cache)

        body = client.get("/api/symbols/CONTRACT/best-options?support_level=100.0").json()
        assert body["calls"]["no_shares_held"] is True
        assert "coverable_contracts" not in body["calls"]


class TestDefaultDteWindowAlignedTo45:
    """`.squad/decisions/inbox/danny-best-options-45d-design.md` (ACCEPTED):
    the default DTE window is `[0, 45]` inclusive -- matching the agents'
    own hard cap (`rule_evaluator._dte_cap_rule`'s `DTE <= 45`) -- not
    `[0, 49]`. Exercised at the real cache+evaluator+endpoint seam (no
    query params supplied, i.e. the actual default a caller gets), not
    via a white-box unit call, since `app.py`'s own `Query(default=...)`
    is a second, independently-editable source of truth for this number
    (Rusty's surface) and only the real endpoint proves the two stay in
    sync.

    Fixture strikes/DTEs below were empirically verified (not assumed)
    against the real evaluator: a 109-strike call at DTE 45 and DTE 46
    both land in the "balanced" category's call delta band, isolating
    the DTE boundary itself as the only variable that can explain either
    contract's presence or absence.
    """

    def _dte_boundary_chain(self, symbol="CONTRACT"):
        return {
            "symbol": symbol,
            "timestamp": "2026-08-29T11:00:00Z",
            "underlying_price": 100.0,
            "calls": {
                _exp_key(45): {"109.0": _contract(bid=0.6, ask=0.7, strike=109.0)},
                _exp_key(46): {"109.0": _contract(bid=0.6, ask=0.7, strike=109.0)},
            },
            "puts": {_exp_key(20): {"96.0": _contract(bid=1.0, ask=1.05, strike=96.0)}},
        }

    def test_default_window_reports_0_to_45_inclusive(self, client_and_cosmos, monkeypatch):
        client, fake_cosmos = client_and_cosmos
        fake_cosmos.symbols["CONTRACT"] = {"enrichment": {"category": "balanced"}, "total_shares": 300}
        cache = _make_cache(monkeypatch, self._dte_boundary_chain())
        cache.get_or_load("CONTRACT")
        set_options_chain_cache(cache)

        body = client.get("/api/symbols/CONTRACT/best-options?support_level=100.0").json()
        assert body["parameters"]["dte"] == {
            "min": 0, "max": 45, "source": "default",
            "system_cap": 45, "timezone": "America/New_York",
        }

    def test_dte_45_included_dte_46_entirely_absent_by_default(self, client_and_cosmos, monkeypatch):
        client, fake_cosmos = client_and_cosmos
        fake_cosmos.symbols["CONTRACT"] = {"enrichment": {"category": "balanced"}, "total_shares": 300}
        cache = _make_cache(monkeypatch, self._dte_boundary_chain())
        cache.get_or_load("CONTRACT")
        set_options_chain_cache(cache)

        body = client.get("/api/symbols/CONTRACT/best-options?support_level=100.0").json()
        rows = body["calls"]["rows"]
        assert [r["dte"] for r in rows] == [45]  # 45 is IN the default window...
        # ...46 is not merely filtered from the visible rows -- it never
        # reaches evaluation at all under the default window: not a row,
        # not the nearest-miss, not counted in excluded_by_delta_band.
        assert body["calls"]["nearest_miss"]["dte"] == 45
        assert body["calls"]["excluded_by_delta_band"] == 0

    def test_dte_46_reachable_and_flagged_only_via_explicit_override(self, client_and_cosmos, monkeypatch):
        client, fake_cosmos = client_and_cosmos
        fake_cosmos.symbols["CONTRACT"] = {"enrichment": {"category": "balanced"}, "total_shares": 300}
        cache = _make_cache(monkeypatch, self._dte_boundary_chain())
        cache.get_or_load("CONTRACT")
        set_options_chain_cache(cache)

        body = client.get("/api/symbols/CONTRACT/best-options?dte_max=60").json()
        assert body["parameters"]["dte"] == {
            "min": 0, "max": 60, "source": "query",
            "system_cap": 45, "timezone": "America/New_York",
        }
        rows_by_dte = {r["dte"]: r for r in body["calls"]["rows"]}
        assert set(rows_by_dte) == {45, 46}
        # The explicit-override path preserves `exceeds_system_dte_cap` as a
        # live, reachable flag (design §2) -- it only ever fires once a
        # caller has deliberately widened past the agents' own 45d cap.
        assert "exceeds_system_dte_cap" not in rows_by_dte[45]["flags"]
        assert "exceeds_system_dte_cap" in rows_by_dte[46]["flags"]


class TestCacheMetadataOnCanonicalRequest:
    """Precomputed-only semantics (Livingston's endpoint rewire): a canonical
    request (`side=both`, default DTE, `support_level=None`) returns cache
    metadata when a precomputed entry exists, or a warming/unavailable state
    when it doesn't. Non-canonical overrides compute live with
    `cache.used=false`.

    Design: `.squad/decisions/inbox/danny-best-options-scheduler-design.md` §11a.
    """

    def test_canonical_request_with_precomputed_entry_has_cache_metadata(self, client_and_cosmos, monkeypatch):
        """Canonical request with a cache hit includes cache metadata."""
        client, fake_cosmos = client_and_cosmos
        fake_cosmos.symbols["CONTRACT"] = {"enrichment": {"category": "balanced"}, "total_shares": 300}

        # Pre-populate Best Options cache
        from src.best_options_cache import get_best_options_cache, set_best_options_cache, BestOptionsCache
        from src.best_options import evaluate_best_options

        cache = _make_cache(monkeypatch, _sample_chain())
        cache.get_or_load("CONTRACT")
        set_options_chain_cache(cache)

        # Evaluate and cache
        chain_json = cache.get_or_hydrate("CONTRACT")
        chain = json.loads(chain_json)
        envelope = evaluate_best_options(
            chain, side="both", category="balanced", total_shares=300,
            next_earnings_date=None, ex_dividend_date=None, support_level=None,
            dte_min=0, dte_max=45, now=datetime.now(timezone.utc),
        )

        best_cache = BestOptionsCache()
        best_cache.publish_snapshot({
            "generation": 1,
            "entries": {
                "CONTRACT": {
                    "symbol": "CONTRACT",
                    "status": "ok",
                    "envelope": envelope,
                    "generation": 1,
                    "computed_at": "2026-08-29T10:00:00Z",
                    "chain_timestamp": "2026-08-29T09:00:00Z",
                    "chain_stale_at_compute": False,
                    "inputs": {"category": "balanced", "total_shares": 300, "next_earnings_date": None, "ex_dividend_date": None},
                    "error": None,
                    "reason": None,
                    "refreshing": False,
                    "refresh_started_at": None,
                    "refresh_completed_at": None,
                    "refresh_error": None,
                    "chain_refresh_error": None,
                }
            },
            "cycle_started_at": "2026-08-29T10:00:00Z",
            "cycle_finished_at": "2026-08-29T10:00:10Z",
            "cycle_duration_seconds": 10.0,
            "trigger": "scheduled",
            "truncated": False,
            "counts": {"ok": 1, "stale": 0, "error": 0, "warming": 0},
        })
        set_best_options_cache(best_cache)

        # Canonical request
        resp = client.get("/api/symbols/CONTRACT/best-options")
        assert resp.status_code == 200
        body = resp.json()

        assert "cache" in body
        cache_meta = body["cache"]
        assert cache_meta["used"] is True
        assert cache_meta["generation"] == 1
        assert cache_meta["entry_status"] == "ok"
        assert cache_meta["computed_at"] == "2026-08-29T10:00:00Z"
        assert cache_meta["chain_timestamp"] == "2026-08-29T09:00:00Z"
        assert isinstance(cache_meta["chain_stale"], bool)
        assert isinstance(cache_meta["inputs_drift"], list)
        assert cache_meta["refreshing"] is False

    def test_non_canonical_request_computes_live_with_cache_used_false(self, client_and_cosmos, monkeypatch):
        """Non-canonical request (explicit dte_max override) computes live."""
        client, fake_cosmos = client_and_cosmos
        fake_cosmos.symbols["CONTRACT"] = {"enrichment": {"category": "balanced"}, "total_shares": 300}
        cache = _make_cache(monkeypatch, _sample_chain())
        cache.get_or_load("CONTRACT")
        set_options_chain_cache(cache)

        # Non-canonical: dte_max=30 (not default 45)
        resp = client.get("/api/symbols/CONTRACT/best-options", params={"dte_max": 30})
        assert resp.status_code == 200
        body = resp.json()

        assert "cache" in body
        cache_meta = body["cache"]
        assert cache_meta["used"] is False
        assert cache_meta["reason"] == "non_canonical_parameters"

    def test_canonical_request_cache_miss_returns_warming(self, client_and_cosmos, monkeypatch):
        """Canonical request with no cache entry returns status=warming."""
        client, fake_cosmos = client_and_cosmos
        fake_cosmos.symbols["CONTRACT"] = {"enrichment": {"category": "balanced"}, "total_shares": 300}

        # Chain is present but Best Options cache is empty
        cache = _make_cache(monkeypatch, _sample_chain())
        cache.get_or_load("CONTRACT")
        set_options_chain_cache(cache)

        from src.best_options_cache import BestOptionsCache, set_best_options_cache
        best_cache = BestOptionsCache()  # Empty
        set_best_options_cache(best_cache)

        resp = client.get("/api/symbols/CONTRACT/best-options")
        assert resp.status_code == 200
        body = resp.json()

        assert body["status"] == "warming"
        assert body["symbol"] == "CONTRACT"
        assert body["retry_after"] == 15
        assert body["reason"] == "precompute_pending"
