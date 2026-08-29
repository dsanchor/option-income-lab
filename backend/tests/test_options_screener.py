"""Test suite for src/options_screener.py -- the deterministic Options
Screener aggregation layer (design: `.squad/decisions/inbox/
copilot-options-screener-approved.md`; interpretive decisions locked in
here are additionally recorded in
`.squad/decisions/inbox/linus-options-screener-design.md`).

Hermetic: pure in-memory dict fixtures, no network calls, no LLM, no
FastAPI, no Cosmos. Every row exercised here comes from a real (in this
test process) call to `best_options.evaluate_best_options` -- fixtures
are chosen so the resulting row shapes (score/color/label/abs_delta/
open_interest/annualized_return_pct/dte) are known and stable, rather
than re-deriving best_options's own scoring in this file.
"""

import copy
import json
from datetime import date, datetime, timedelta, timezone

import pytest

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


# AAA: two admitted covered-call rows (balanced band [0.20, 0.30]) --
# DTE 20 scores 85 (green/"Preferred"), DTE 40 scores 73 (green/"Preferred").
AAA_CHAIN = _chain(calls={
    _exp_key(20): _bucket(_contract(bid=1.20, ask=1.30, iv=0.30, delta=0.25, oi=500, strike=105.0)),
    _exp_key(40): _bucket(_contract(bid=1.80, ask=1.95, iv=0.28, delta=0.28, oi=50, strike=108.0)),
}, symbol="AAA")

# BBB: one admitted row whose premium sits below the category's DTE-scaled
# wait floor -- forced red/"Avoid" regardless of its raw component score.
BBB_CHAIN = _chain(calls={
    _exp_key(10): _bucket(_contract(bid=0.10, ask=0.30, iv=0.15, delta=0.29, oi=2, strike=110.0)),
}, symbol="BBB")

# CCC: one contract whose delta (0.50) sits outside the balanced band
# [0.20, 0.30] -- admits zero rows, and *is* describable via nearest_miss.
CCC_CHAIN = _chain(calls={
    _exp_key(15): _bucket(_contract(bid=0.50, ask=0.60, iv=0.20, delta=0.50, oi=100, strike=95.0)),
}, symbol="CCC")


def _aaa_rows(result, side="calls"):
    return result[side]["rows"]


class TestFilterIntersection:
    def test_default_preferences_admit_only_green_and_yellow(self):
        result = evaluate_options_screener(
            [_ready("AAA", AAA_CHAIN), _ready("BBB", BBB_CHAIN)], side="call", now=NOW,
        )
        symbols_shown = {r["symbol"] for r in result["calls"]["rows"]}
        assert symbols_shown == {"AAA"}  # BBB's only row is red/"Avoid", excluded by default

    def test_combined_filters_only_narrow_never_widen(self):
        # min_abs_delta=0.26 alone would admit only AAA's DTE-40 row;
        # adding min_open_interest=100 on top must narrow further, not
        # bring back anything min_abs_delta already excluded.
        broad = evaluate_options_screener(
            [_ready("AAA", AAA_CHAIN)], side="call", min_abs_delta=0.26, now=NOW,
        )
        narrow = evaluate_options_screener(
            [_ready("AAA", AAA_CHAIN)], side="call", min_abs_delta=0.26, min_open_interest=100, now=NOW,
        )
        broad_strikes = {r["strike"] for r in broad["calls"]["rows"]}
        narrow_strikes = {r["strike"] for r in narrow["calls"]["rows"]}
        assert broad_strikes == {108.0}
        assert narrow_strikes == set()  # the 40-DTE row's OI is 50, below 100
        assert narrow_strikes <= broad_strikes

    def test_global_delta_window_cannot_reach_past_a_symbols_own_category_band(self):
        # CCC's only contract (delta 0.50) was never admitted by its own
        # category's [0.20, 0.30] band. A generously wide screener-level
        # abs_delta window must not resurrect it -- it simply never
        # entered `sections[s]["rows"]` in the first place.
        result = evaluate_options_screener(
            [_ready("CCC", CCC_CHAIN)], side="call",
            preferences={"Preferred", "Acceptable", "Avoid"},
            min_abs_delta=0.0, max_abs_delta=1.0, now=NOW,
        )
        assert result["calls"]["rows"] == []

    def test_symbol_allowlist_filter(self):
        result = evaluate_options_screener(
            [_ready("AAA", AAA_CHAIN), _ready("BBB", BBB_CHAIN)], side="call",
            preferences={"Preferred", "Acceptable", "Avoid"}, symbols=["aaa"], now=NOW,
        )
        assert {r["symbol"] for r in result["calls"]["rows"]} == {"AAA"}
        assert result["symbols"]["total"] == 1  # BBB excluded from consideration entirely

    def test_min_annualized_return_pct_filter(self):
        result = evaluate_options_screener(
            [_ready("AAA", AAA_CHAIN)], side="call", min_annualized_return_pct=20.0, now=NOW,
        )
        # Only the DTE-20 row (21.9%) clears 20%; the DTE-40 row (16.4%) is excluded.
        assert [r["strike"] for r in result["calls"]["rows"]] == [105.0]

    def test_dte_bounds_filter(self):
        result = evaluate_options_screener(
            [_ready("AAA", AAA_CHAIN)], side="call", min_dte=25, now=NOW,
        )
        assert [r["dte"] for r in result["calls"]["rows"]] == [40]


class TestDeterministicOrdering:
    def test_default_sort_is_score_desc_then_dte_asc(self):
        result = evaluate_options_screener([_ready("AAA", AAA_CHAIN)], side="call", now=NOW)
        rows = result["calls"]["rows"]
        assert [r["score"] for r in rows] == [85, 73]
        assert [r["dte"] for r in rows] == [20, 40]

    def test_output_independent_of_symbol_input_order(self):
        inputs_a = [_ready("AAA", AAA_CHAIN), _ready("BBB", BBB_CHAIN), _ready("CCC", CCC_CHAIN)]
        inputs_b = list(reversed(inputs_a))
        kwargs = dict(side="call", preferences={"Preferred", "Acceptable", "Avoid"}, now=NOW)
        result_a = evaluate_options_screener(copy.deepcopy(inputs_a), **kwargs)
        result_b = evaluate_options_screener(copy.deepcopy(inputs_b), **kwargs)
        keys_a = [(r["symbol"], r["expiration"], r["strike"]) for r in result_a["calls"]["rows"]]
        keys_b = [(r["symbol"], r["expiration"], r["strike"]) for r in result_b["calls"]["rows"]]
        assert keys_a == keys_b

    def test_stable_tiebreaker_when_score_dte_and_delta_fit_are_equal(self):
        # Two different symbols, identical score/DTE/delta -- only the
        # explicit (symbol, expiration, strike) tiebreaker can order them.
        chain_z = _chain(calls={_exp_key(20): _bucket(
            _contract(bid=1.20, ask=1.30, iv=0.30, delta=0.25, oi=500, strike=105.0)
        )}, symbol="ZZZ")
        chain_a = _chain(calls={_exp_key(20): _bucket(
            _contract(bid=1.20, ask=1.30, iv=0.30, delta=0.25, oi=500, strike=105.0)
        )}, symbol="AAA2")
        result = evaluate_options_screener(
            [_ready("ZZZ", chain_z), _ready("AAA2", chain_a)], side="call", now=NOW,
        )
        symbols_in_order = [r["symbol"] for r in result["calls"]["rows"]]
        assert symbols_in_order == ["AAA2", "ZZZ"]  # alphabetical tiebreaker


class TestNullMetricBehavior:
    NO_OI_CHAIN = _chain(calls={_exp_key(20): _bucket(
        _contract(bid=1.20, ask=1.30, iv=0.30, delta=0.25, oi=None, strike=105.0)
    )}, symbol="NOOI")

    NO_BID_CHAIN = _chain(calls={_exp_key(20): _bucket(
        _contract(bid=None, ask=1.30, iv=0.30, delta=0.25, oi=500, strike=105.0)
    )}, symbol="NOBID")

    def test_none_open_interest_fails_a_set_minimum(self):
        result = evaluate_options_screener(
            [_ready("NOOI", self.NO_OI_CHAIN)], side="call",
            preferences={"Preferred", "Acceptable", "Avoid"}, min_open_interest=1, now=NOW,
        )
        assert result["calls"]["rows"] == []

    def test_none_open_interest_passes_through_when_filter_unset(self):
        result = evaluate_options_screener(
            [_ready("NOOI", self.NO_OI_CHAIN)], side="call",
            preferences={"Preferred", "Acceptable", "Avoid"}, now=NOW,
        )
        assert len(result["calls"]["rows"]) == 1
        assert result["calls"]["rows"][0]["open_interest"] is None

    def test_none_annualized_return_fails_a_set_minimum(self):
        result = evaluate_options_screener(
            [_ready("NOBID", self.NO_BID_CHAIN)], side="call",
            preferences={"Preferred", "Acceptable", "Avoid"}, min_annualized_return_pct=0.0, now=NOW,
        )
        assert result["calls"]["rows"] == []

    def test_none_annualized_return_passes_through_when_filter_unset(self):
        result = evaluate_options_screener(
            [_ready("NOBID", self.NO_BID_CHAIN)], side="call",
            preferences={"Preferred", "Acceptable", "Avoid"}, now=NOW,
        )
        assert len(result["calls"]["rows"]) == 1
        assert result["calls"]["rows"][0]["annualized_return_pct"] is None


class TestMemoization:
    def test_second_call_with_same_memo_reuses_cached_result(self, monkeypatch):
        import src.options_screener as mod

        calls_made = []
        original = mod.evaluate_best_options

        def _counting(*args, **kwargs):
            calls_made.append(1)
            return original(*args, **kwargs)

        monkeypatch.setattr(mod, "evaluate_best_options", _counting)

        memo = {}
        entry = _ready("AAA", AAA_CHAIN)
        result_1 = evaluate_options_screener([entry], side="call", now=NOW, memo=memo)
        result_2 = evaluate_options_screener([entry], side="call", now=NOW, memo=memo)

        assert len(calls_made) == 1  # second call was a memo hit
        assert result_1["calls"]["rows"] == result_2["calls"]["rows"]

    @pytest.mark.parametrize("mutate", [
        lambda c, e: {**c, "timestamp": "2026-08-30T11:00:00Z"},
    ])
    def test_chain_timestamp_change_invalidates_memo(self, monkeypatch, mutate):
        import src.options_screener as mod
        calls_made = []
        original = mod.evaluate_best_options
        monkeypatch.setattr(mod, "evaluate_best_options", lambda *a, **k: (calls_made.append(1), original(*a, **k))[1])

        memo = {}
        entry = _ready("AAA", AAA_CHAIN)
        evaluate_options_screener([entry], side="call", now=NOW, memo=memo)
        mutated_entry = _ready("AAA", mutate(AAA_CHAIN, entry))
        evaluate_options_screener([mutated_entry], side="call", now=NOW, memo=memo)
        assert len(calls_made) == 2

    def test_category_change_invalidates_memo(self, monkeypatch):
        import src.options_screener as mod
        calls_made = []
        original = mod.evaluate_best_options
        monkeypatch.setattr(mod, "evaluate_best_options", lambda *a, **k: (calls_made.append(1), original(*a, **k))[1])

        memo = {}
        evaluate_options_screener([_ready("AAA", AAA_CHAIN, category="balanced")], side="call", now=NOW, memo=memo)
        evaluate_options_screener([_ready("AAA", AAA_CHAIN, category="high_yield")], side="call", now=NOW, memo=memo)
        assert len(calls_made) == 2

    def test_total_shares_change_invalidates_memo(self, monkeypatch):
        import src.options_screener as mod
        calls_made = []
        original = mod.evaluate_best_options
        monkeypatch.setattr(mod, "evaluate_best_options", lambda *a, **k: (calls_made.append(1), original(*a, **k))[1])

        memo = {}
        evaluate_options_screener([_ready("AAA", AAA_CHAIN, total_shares=0)], side="call", now=NOW, memo=memo)
        evaluate_options_screener([_ready("AAA", AAA_CHAIN, total_shares=200)], side="call", now=NOW, memo=memo)
        assert len(calls_made) == 2

    def test_earnings_and_ex_div_and_support_changes_each_invalidate_memo(self, monkeypatch):
        import src.options_screener as mod
        calls_made = []
        original = mod.evaluate_best_options
        monkeypatch.setattr(mod, "evaluate_best_options", lambda *a, **k: (calls_made.append(1), original(*a, **k))[1])

        memo = {}
        base = _ready("AAA", AAA_CHAIN)
        evaluate_options_screener([base], side="call", now=NOW, memo=memo)
        evaluate_options_screener(
            [_ready("AAA", AAA_CHAIN, next_earnings_date="2026-09-10")], side="call", now=NOW, memo=memo,
        )
        evaluate_options_screener(
            [_ready("AAA", AAA_CHAIN, ex_dividend_date="2026-09-05")], side="call", now=NOW, memo=memo,
        )
        evaluate_options_screener(
            [_ready("AAA", AAA_CHAIN, support_level=95.0)], side="call", now=NOW, memo=memo,
        )
        assert len(calls_made) == 4

    def test_now_change_alone_does_not_invalidate_memo_but_generated_at_still_updates(self, monkeypatch):
        import src.options_screener as mod
        calls_made = []
        original = mod.evaluate_best_options
        monkeypatch.setattr(mod, "evaluate_best_options", lambda *a, **k: (calls_made.append(1), original(*a, **k))[1])

        memo = {}
        entry = _ready("AAA", AAA_CHAIN)
        later = NOW + timedelta(hours=1)
        result_1 = evaluate_options_screener([entry], side="call", now=NOW, memo=memo)
        result_2 = evaluate_options_screener([entry], side="call", now=later, memo=memo)

        assert len(calls_made) == 1  # `now` alone is not part of the memo key
        assert result_1["generated_at"] != result_2["generated_at"]
        assert result_1["calls"]["rows"] == result_2["calls"]["rows"]


class TestPagination:
    def test_offset_and_limit_slice_the_ordered_rows(self):
        result = evaluate_options_screener([_ready("AAA", AAA_CHAIN)], side="call", offset=1, limit=1, now=NOW)
        assert [r["dte"] for r in result["calls"]["rows"]] == [40]
        pagination = result["calls"]["pagination"]
        assert pagination == {"offset": 1, "limit": 1, "total_matching": 2, "returned": 1, "has_more": False}

    def test_has_more_true_when_rows_remain(self):
        result = evaluate_options_screener([_ready("AAA", AAA_CHAIN)], side="call", offset=0, limit=1, now=NOW)
        assert result["calls"]["pagination"]["has_more"] is True

    def test_offset_beyond_total_returns_empty_page(self):
        result = evaluate_options_screener([_ready("AAA", AAA_CHAIN)], side="call", offset=100, limit=10, now=NOW)
        assert result["calls"]["rows"] == []
        assert result["calls"]["pagination"]["has_more"] is False

    def test_negative_offset_and_limit_are_clamped_not_raised(self):
        result = evaluate_options_screener([_ready("AAA", AAA_CHAIN)], side="call", offset=-5, limit=-5, now=NOW)
        assert result["calls"]["pagination"]["offset"] == 0
        assert result["calls"]["pagination"]["limit"] == 0
        assert result["calls"]["rows"] == []


class TestNearestMiss:
    def test_zero_row_symbol_surfaces_nearest_miss_tagged_with_symbol(self):
        result = evaluate_options_screener([_ready("CCC", CCC_CHAIN)], side="call", now=NOW)
        misses = result["calls"]["nearest_miss"]
        assert len(misses) == 1
        assert misses[0]["symbol"] == "CCC"
        assert misses[0]["reason"] == "delta_band"

    def test_symbol_with_upstream_rows_filtered_to_zero_is_not_a_nearest_miss(self):
        # AAA has 2 admitted rows upstream; an aggressive screener filter
        # can hide both from `rows`, but that is NOT the same fact as
        # "this symbol's own category rules found nothing" -- no
        # nearest_miss entry should appear for it.
        result = evaluate_options_screener(
            [_ready("AAA", AAA_CHAIN)], side="call", min_annualized_return_pct=999.0, now=NOW,
        )
        assert result["calls"]["rows"] == []
        assert result["calls"]["nearest_miss"] == []


class TestSymbolStatusHandling:
    def test_warming_and_error_symbols_are_skipped_and_recorded(self):
        result = evaluate_options_screener(
            [
                _ready("AAA", AAA_CHAIN),
                {"symbol": "WARM", "status": "warming"},
                {"symbol": "BAD", "status": "error", "error": "chain fetch failed"},
            ],
            side="call", now=NOW,
        )
        assert result["symbols"]["total"] == 3
        assert result["symbols"]["ready"] == 1
        assert result["symbols"]["warming"] == ["WARM"]
        assert result["symbols"]["error"] == [{"symbol": "BAD", "error": "chain fetch failed"}]
        assert {r["symbol"] for r in result["calls"]["rows"]} == {"AAA"}

    def test_unrecognised_status_downgrades_to_error(self):
        result = evaluate_options_screener(
            [{"symbol": "ODD", "status": "bogus"}], side="call", now=NOW,
        )
        assert result["symbols"]["error"][0]["symbol"] == "ODD"
        assert "bogus" in result["symbols"]["error"][0]["error"]

    def test_ready_status_without_usable_chain_downgrades_to_error(self):
        result = evaluate_options_screener(
            [{"symbol": "NOCHAIN", "status": "ready", "chain": None}], side="call", now=NOW,
        )
        assert result["symbols"]["error"][0]["symbol"] == "NOCHAIN"
        assert result["symbols"]["ready"] == 0


class TestSideHandling:
    def test_call_only_side_leaves_puts_as_empty_placeholder(self):
        result = evaluate_options_screener([_ready("AAA", AAA_CHAIN)], side="call", now=NOW)
        assert len(result["calls"]["rows"]) == 2
        assert result["puts"] == {
            "rows": [], "nearest_miss": [],
            "pagination": {"offset": 0, "limit": 50, "total_matching": 0, "returned": 0, "has_more": False},
        }

    def test_both_sides_populate_independently(self):
        put_chain = _chain(puts={_exp_key(20): _bucket(
            _contract(bid=1.20, ask=1.25, iv=0.35, delta=-0.30, oi=2000, strike=90.0)
        )}, symbol="AAA", underlying_price=100.0)
        combined = {**AAA_CHAIN, "puts": put_chain["puts"]}
        result = evaluate_options_screener(
            [_ready("AAA", combined, category="high_yield")], side="both", now=NOW,
        )
        assert len(result["calls"]["rows"]) >= 1
        assert len(result["puts"]["rows"]) == 1


class TestByteStableResults:
    def test_repeated_calls_with_identical_input_are_byte_identical(self):
        entries = [_ready("AAA", AAA_CHAIN), _ready("BBB", BBB_CHAIN), _ready("CCC", CCC_CHAIN)]
        result_1 = evaluate_options_screener(copy.deepcopy(entries), side="call", now=NOW)
        result_2 = evaluate_options_screener(copy.deepcopy(entries), side="call", now=NOW)
        assert json.dumps(result_1, sort_keys=True) == json.dumps(result_2, sort_keys=True)

    def test_defaults_documented_on_the_public_constant(self):
        assert DEFAULT_PREFERENCES == {"Preferred", "Acceptable"}
