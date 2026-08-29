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
"""
from __future__ import annotations

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

        resp = client.get("/api/symbols/CONTRACT/best-options")
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

        params = client.get("/api/symbols/CONTRACT/best-options").json()["parameters"]
        # These are exactly the accessor chains BestOptionsParams.tsx evaluates.
        assert isinstance(params["thresholds"]["call"]["delta_lo"], float)
        assert isinstance(params["thresholds"]["put"]["delta_hi"], float)
        assert isinstance(params["thresholds_source"]["call"], str)
        assert isinstance(params["skill_reference"]["put"], str)
        assert isinstance(params["premium"]["basis"]["call"], str)


class TestSectionLevelTransparencyFields:
    """`excluded_by_delta_band` (both sides) and `coverable_contracts`/
    `no_shares_held` (call side only) are the count-metadata transparency
    surface the binding visual-consistency/excluded-contracts directive
    requires the UI to expose (Basher D3)."""

    def test_excluded_by_delta_band_present_both_sides(self, client_and_cosmos, monkeypatch):
        client, fake_cosmos = client_and_cosmos
        fake_cosmos.symbols["CONTRACT"] = {"enrichment": {"category": "balanced"}, "total_shares": 300}
        cache = _make_cache(monkeypatch, _sample_chain())
        cache.get_or_load("CONTRACT")  # warm synchronously before any request
        set_options_chain_cache(cache)

        body = client.get("/api/symbols/CONTRACT/best-options").json()
        assert isinstance(body["calls"]["excluded_by_delta_band"], int)
        assert isinstance(body["puts"]["excluded_by_delta_band"], int)

    def test_coverable_contracts_and_no_shares_held_are_call_only(self, client_and_cosmos, monkeypatch):
        client, fake_cosmos = client_and_cosmos
        fake_cosmos.symbols["CONTRACT"] = {"enrichment": {"category": "balanced"}, "total_shares": 300}
        cache = _make_cache(monkeypatch, _sample_chain())
        cache.get_or_load("CONTRACT")  # warm synchronously before any request
        set_options_chain_cache(cache)

        body = client.get("/api/symbols/CONTRACT/best-options").json()
        assert body["calls"]["coverable_contracts"] == 3  # 300 shares // 100
        assert body["calls"]["no_shares_held"] is False
        # Never on the put section -- a per-row `no_shares_held` flag would be
        # the wrong place to look for this (the old, broken UI check).
        assert "coverable_contracts" not in body["puts"]
        assert "no_shares_held" not in body["puts"]
        for row in body["calls"]["rows"]:
            assert "no_shares_held" not in row["flags"]

    def test_zero_shares_sets_no_shares_held_true(self, client_and_cosmos, monkeypatch):
        client, fake_cosmos = client_and_cosmos
        fake_cosmos.symbols["CONTRACT"] = {"enrichment": {"category": "balanced"}, "total_shares": 0}
        cache = _make_cache(monkeypatch, _sample_chain())
        cache.get_or_load("CONTRACT")  # warm synchronously before any request
        set_options_chain_cache(cache)

        body = client.get("/api/symbols/CONTRACT/best-options").json()
        assert body["calls"]["coverable_contracts"] == 0
        assert body["calls"]["no_shares_held"] is True
