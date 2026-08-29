"""Basher-owned adversarial coverage for `src/options_screener.py`.

Complements (does not duplicate) Linus's own `test_options_screener.py`
(32 tests, verified passing independently by Basher). This file targets
the seam Linus's own design doc (`.squad/decisions/inbox/
linus-options-screener-design.md`) explicitly calls out as
interpretive/adversarial-prone: exact, unmodified reuse of
`best_options.evaluate_best_options` (never a narrowed/widened DTE
window passed into the per-symbol call itself), category-band
narrowing-only on BOTH sides (not just calls), the `rows`/`nearest_miss`
mutual-exclusion invariant under a mixed multi-symbol scenario, an
explicit "Avoid" preference inclusion path, and a defense-in-depth full
payload scan proving `coverable_contracts` cannot resurface at this
aggregation layer regardless of what `best_options.py` ever does.

Hermetic: real `src.options_screener.evaluate_options_screener` calling
the real `src.best_options.evaluate_best_options` against in-memory
chain fixtures -- no network, no LLM, no Cosmos, no FastAPI. Uses the
real evaluator/aggregation seam per the reviewer charter's "use real
modules across at least the evaluator/cache/API seam" instruction; the
only monkeypatching below is a pure observation spy (asserting on the
real call's own arguments), never a fake that fabricates a result.
"""

import json
from datetime import date, datetime, timedelta, timezone

import src.options_screener as screener_mod
from src.options_screener import DEFAULT_PREFERENCES, evaluate_options_screener

NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
TODAY = date(2026, 8, 29)


def _contract(bid, ask, iv, delta, oi, strike, volume=10):
    mid = round((bid + ask) / 2, 4) if bid is not None and ask is not None else None
    contract = {
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "iv": iv,
        "delta": delta,
        "gamma": 0.01,
        "theta": -0.02,
        "vega": 0.05,
        "rho": 0.01,
        "lastPrice": bid,
        "openInterest": oi,
        "volume": volume,
        "inTheMoney": False,
        "contractSymbol": f"TEST{strike}",
        "_meta": {
            "quote_asof": "2026-08-29T11:00:00Z",
            "greeks_valid": True,
            "greeks_asof": "2026-08-29T11:00:00Z",
        },
    }
    if oi is None:
        del contract["openInterest"]
    return contract


def _bucket(*contracts):
    return {f"{c['strike']:.1f}": c for c in contracts}


def _exp_key(days: int) -> str:
    return (TODAY + timedelta(days=days)).strftime("%Y%m%d")


def _chain(calls=None, puts=None, symbol="TEST", underlying_price=100.0, timestamp="2026-08-29T11:00:00Z"):
    return {
        "symbol": symbol,
        "timestamp": timestamp,
        "underlying_price": underlying_price,
        "calls": calls or {},
        "puts": puts or {},
    }


def _ready(symbol, chain, category="balanced", **extra):
    entry = {"symbol": symbol, "status": "ready", "chain": chain, "category": category, "total_shares": 0}
    entry.update(extra)
    return entry


class TestExactEvaluatorReuse:
    """Danny/Linus's central promise: this module never renegotiates a
    per-symbol category's own admission -- it must always hand
    `evaluate_best_options` that module's OWN default DTE window, never
    the screener-level `min_dte`/`max_dte` filter values, regardless of
    how a caller narrows the aggregated view afterwards."""

    def test_screener_level_dte_bounds_never_reach_the_per_symbol_evaluator_call(self, monkeypatch):
        from src.best_options import DEFAULT_DTE_MAX, DEFAULT_DTE_MIN

        captured_kwargs = []
        original = screener_mod.evaluate_best_options

        def _spy(*args, **kwargs):
            captured_kwargs.append(kwargs)
            return original(*args, **kwargs)

        monkeypatch.setattr(screener_mod, "evaluate_best_options", _spy)

        chain = _chain(calls={_exp_key(20): _bucket(
            _contract(bid=1.20, ask=1.30, iv=0.30, delta=0.25, oi=500, strike=105.0)
        )}, symbol="AAA")
        evaluate_options_screener(
            [_ready("AAA", chain)], side="call", now=NOW, min_dte=30, max_dte=35,
        )

        assert len(captured_kwargs) == 1
        assert captured_kwargs[0]["dte_min"] == DEFAULT_DTE_MIN
        assert captured_kwargs[0]["dte_max"] == DEFAULT_DTE_MAX

    def test_symbol_level_facts_pass_through_unmodified(self, monkeypatch):
        # category/total_shares/earnings/ex-div/support are exactly the
        # caller-supplied values, verbatim -- this module must not
        # normalise, default-substitute, or otherwise "helpfully" alter
        # any of them before handing them to the real evaluator.
        captured_kwargs = []
        original = screener_mod.evaluate_best_options

        def _spy(*args, **kwargs):
            captured_kwargs.append(kwargs)
            return original(*args, **kwargs)

        monkeypatch.setattr(screener_mod, "evaluate_best_options", _spy)

        chain = _chain(calls={_exp_key(20): _bucket(
            _contract(bid=1.20, ask=1.30, iv=0.30, delta=0.25, oi=500, strike=105.0)
        )}, symbol="AAA")
        evaluate_options_screener(
            [_ready(
                "AAA", chain, category="high_yield", total_shares=300,
                next_earnings_date="2026-09-15", ex_dividend_date="2026-09-01",
                support_level=97.5,
            )],
            side="call", now=NOW,
        )

        assert len(captured_kwargs) == 1
        kwargs = captured_kwargs[0]
        assert kwargs["category"] == "high_yield"
        assert kwargs["total_shares"] == 300
        assert kwargs["next_earnings_date"] == "2026-09-15"
        assert kwargs["ex_dividend_date"] == "2026-09-01"
        assert kwargs["support_level"] == 97.5


class TestPutSideCategoryBandNarrowingOnly:
    """Linus's own test suite proves calls-side narrow-never-widen. Puts
    use a distinct category table (`cash_secured_put`, negative raw
    delta) -- must independently confirm the same invariant holds for
    puts, since a delta-sign bug in the screener's own abs_delta
    post-filter would not be caught by a calls-only test."""

    def test_put_outside_its_own_category_band_cannot_be_resurrected_by_a_wide_screener_window(self):
        # balanced CSP band is [0.20, 0.30]; a put with raw delta -0.60
        # (abs 0.60) is never admitted by best_options itself.
        chain = _chain(puts={_exp_key(15): _bucket(
            _contract(bid=0.50, ask=0.60, iv=0.20, delta=-0.60, oi=100, strike=95.0)
        )}, symbol="PPP")
        result = evaluate_options_screener(
            [_ready("PPP", chain, category="balanced")],
            side="put",
            preferences={"Preferred", "Acceptable", "Avoid"},
            min_abs_delta=0.0, max_abs_delta=1.0,
            now=NOW,
        )
        assert result["puts"]["rows"] == []
        # Zero admitted rows upstream -> describable via nearest_miss,
        # not silently absent.
        assert len(result["puts"]["nearest_miss"]) == 1
        assert result["puts"]["nearest_miss"][0]["symbol"] == "PPP"

    def test_put_admitted_row_still_subject_to_screener_post_filter_narrowing(self):
        # A put delta of -0.28 IS inside balanced CSP's [0.20, 0.30] band
        # (abs 0.28) -- admitted upstream -- but a screener-level
        # min_abs_delta=0.29 must narrow it away (not reach past it, but
        # correctly apply as a strictly-narrower post-filter).
        chain = _chain(puts={_exp_key(15): _bucket(
            _contract(bid=0.50, ask=0.60, iv=0.20, delta=-0.28, oi=100, strike=95.0)
        )}, symbol="QQQ")
        admitted = evaluate_options_screener(
            [_ready("QQQ", chain, category="balanced")], side="put", now=NOW,
        )
        narrowed = evaluate_options_screener(
            [_ready("QQQ", chain, category="balanced")], side="put", min_abs_delta=0.29, now=NOW,
        )
        assert len(admitted["puts"]["rows"]) == 1
        assert narrowed["puts"]["rows"] == []


class TestRowsAndNearestMissAreMutuallyExclusive:
    def test_no_symbol_contributes_to_both_rows_and_nearest_miss_in_a_mixed_batch(self):
        admitted_chain = _chain(calls={_exp_key(20): _bucket(
            _contract(bid=1.20, ask=1.30, iv=0.30, delta=0.25, oi=500, strike=105.0)
        )}, symbol="AAA")
        zero_row_chain = _chain(calls={_exp_key(15): _bucket(
            _contract(bid=0.50, ask=0.60, iv=0.20, delta=0.60, oi=100, strike=95.0)
        )}, symbol="ZERO")
        filtered_away_chain = _chain(calls={_exp_key(20): _bucket(
            _contract(bid=1.20, ask=1.30, iv=0.30, delta=0.25, oi=500, strike=200.0)
        )}, symbol="FILTERED")

        result = evaluate_options_screener(
            [
                _ready("AAA", admitted_chain),
                _ready("ZERO", zero_row_chain),
                _ready("FILTERED", filtered_away_chain),
            ],
            side="call",
            min_annualized_return_pct=999.0,  # excludes AAA's and FILTERED's admitted rows post-hoc
            preferences={"Preferred", "Acceptable", "Avoid"},
            now=NOW,
        )
        row_symbols = {r["symbol"] for r in result["calls"]["rows"]}
        miss_symbols = {m["symbol"] for m in result["calls"]["nearest_miss"]}
        assert row_symbols == set()  # both AAA and FILTERED's rows were post-filtered away
        assert miss_symbols == {"ZERO"}  # only the symbol with zero upstream-admitted rows
        assert row_symbols.isdisjoint(miss_symbols)


class TestAvoidPreferenceExplicitlySelectable:
    def test_avoid_only_preference_surfaces_red_rows_hidden_by_default(self):
        # DTE-10, tiny 0.10/0.30 premium against balanced CC's
        # premium_wait_pct floor -- forced red/"Avoid" regardless of
        # component score (mirrors Linus's BBB fixture).
        chain = _chain(calls={_exp_key(10): _bucket(
            _contract(bid=0.10, ask=0.30, iv=0.15, delta=0.29, oi=2, strike=110.0)
        )}, symbol="BBB")
        default_view = evaluate_options_screener([_ready("BBB", chain)], side="call", now=NOW)
        avoid_only = evaluate_options_screener(
            [_ready("BBB", chain)], side="call", preferences={"Avoid"}, now=NOW,
        )
        assert default_view["calls"]["rows"] == []  # DEFAULT_PREFERENCES hides it
        assert len(avoid_only["calls"]["rows"]) == 1
        assert avoid_only["calls"]["rows"][0]["label"] == "Avoid"


class TestNoCoverableContractsFieldAnywhere:
    """Defense in depth: even if `best_options.py` ever regresses and
    reintroduces `coverable_contracts` on a row, this aggregation layer
    must not be the thing that lets it leak into a Screener response
    undetected -- scan the ENTIRE serialised payload, not just the
    top-level section keys."""

    def test_full_payload_json_dump_contains_no_coverable_contracts_key(self):
        chain = _chain(calls={_exp_key(20): _bucket(
            _contract(bid=1.20, ask=1.30, iv=0.30, delta=0.25, oi=500, strike=105.0)
        )}, symbol="AAA")
        result = evaluate_options_screener(
            [_ready("AAA", chain)], side="both",
            preferences={"Preferred", "Acceptable", "Avoid"}, now=NOW,
        )
        dumped = json.dumps(result, default=str)
        assert "coverable_contracts" not in dumped


class TestByteStableAcrossRepeatedCallsWithMixedStatuses:
    def test_repeated_calls_with_warming_and_error_symbols_mixed_in_are_still_byte_identical(self):
        chain = _chain(calls={_exp_key(20): _bucket(
            _contract(bid=1.20, ask=1.30, iv=0.30, delta=0.25, oi=500, strike=105.0)
        )}, symbol="AAA")
        entries = [
            _ready("AAA", chain),
            {"symbol": "WARM", "status": "warming"},
            {"symbol": "BAD", "status": "error", "error": "chain fetch failed"},
        ]
        result_1 = evaluate_options_screener([dict(e) for e in entries], side="call", now=NOW)
        result_2 = evaluate_options_screener([dict(e) for e in entries], side="call", now=NOW)
        assert json.dumps(result_1, sort_keys=True) == json.dumps(result_2, sort_keys=True)
