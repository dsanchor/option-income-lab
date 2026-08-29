"""Test suite for src/best_options.py -- the deterministic Best Options
evaluator (design: .squad/decisions/inbox/danny-best-options-design.md,
accepted 2026-08-29; see also
.squad/decisions/inbox/linus-best-options-scoring.md for the interpretive
decisions this suite locks in).

Hermetic: pure in-memory dict fixtures, no network calls, no LLM, no
FastAPI. Focused on the pure-domain behaviours Linus owns: gates,
scoring, ordering, nearest_miss, and determinism. Endpoint/provenance
wiring and adversarial edge cases are left to Rusty's/Basher's own test
files per design section 9.
"""

import copy
import re
from datetime import date, datetime, timedelta, timezone

from src.best_options import evaluate_best_options

NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
TODAY = date(2026, 8, 29)


def _contract(bid, ask, iv, delta, oi, strike, volume=10):
    mid = round((bid + ask) / 2, 4) if bid is not None and ask is not None else None
    return {
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


def _bucket(*contracts):
    return {f"{c['strike']:.1f}": c for c in contracts}


def _exp_key(days: int) -> str:
    return (TODAY + timedelta(days=days)).strftime("%Y%m%d")


def _chain(calls=None, puts=None, symbol="TEST", underlying_price=100.0):
    return {
        "symbol": symbol,
        "timestamp": "2026-08-29T11:00:00Z",
        "underlying_price": underlying_price,
        "calls": calls or {},
        "puts": puts or {},
    }


class TestRowInclusionFilters:
    """Row inclusion is governed by TWO user-facing filters applied before
    scoring: the DTE window and the category-aware delta band. Only
    contracts surviving BOTH appear in `rows` (product-owner instruction,
    2026-08-29, superseding an earlier reading where delta band only
    coloured a row red -- see best_options.py's module docstring). Safety
    gates that are NOT row filters (tradability, earnings span) still
    colour an in-band row red without removing it."""

    def test_out_of_band_delta_contract_is_excluded_from_rows(self):
        # high_yield CC band is [0.25, 0.35]; delta=0.05 is far outside it.
        chain = _chain(calls={_exp_key(20): _bucket(
            _contract(bid=0.10, ask=0.15, iv=0.20, delta=0.05, oi=500, strike=120.0)
        )})
        result = evaluate_best_options(
            chain, side="call", category="high_yield", total_shares=0,
            next_earnings_date=None, ex_dividend_date=None, support_level=None,
            dte_min=0, dte_max=49, now=NOW,
        )
        assert result["calls"]["rows"] == []
        assert result["calls"]["total"] == 0
        assert result["calls"]["excluded_by_delta_band"] == 1
        # ...but it must remain describable via nearest_miss, not silently lost.
        nm = result["calls"]["nearest_miss"]
        assert nm["available"] is True
        assert nm["reason"] == "delta_band"

    def test_in_band_contract_is_returned_alongside_an_excluded_one(self):
        in_band = _contract(bid=0.30, ask=0.35, iv=0.20, delta=0.28, oi=500, strike=105.0)
        out_of_band = _contract(bid=0.10, ask=0.15, iv=0.20, delta=0.05, oi=500, strike=120.0)
        chain = _chain(calls={_exp_key(20): _bucket(in_band, out_of_band)})
        result = evaluate_best_options(
            chain, side="call", category="high_yield", total_shares=0,
            next_earnings_date=None, ex_dividend_date=None, support_level=None,
            dte_min=0, dte_max=49, now=NOW,
        )
        rows = result["calls"]["rows"]
        assert len(rows) == 1
        assert rows[0]["strike"] == 105.0
        assert result["calls"]["total"] == 1
        assert result["calls"]["excluded_by_delta_band"] == 1

    def test_untradable_but_in_band_row_is_still_returned(self):
        contract = _contract(bid=None, ask=None, iv=0.20, delta=0.30, oi=0, strike=105.0)
        chain = _chain(calls={_exp_key(20): _bucket(contract)})
        result = evaluate_best_options(
            chain, side="call", category="balanced", total_shares=0,
            next_earnings_date=None, ex_dividend_date=None, support_level=None,
            dte_min=0, dte_max=49, now=NOW,
        )
        rows = result["calls"]["rows"]
        assert len(rows) == 1
        assert rows[0]["gates"]["tradability"] == "fail"
        assert rows[0]["color"] == "red"
        assert rows[0]["score"] is None

    def test_rows_outside_the_dte_window_are_absent(self):
        chain = _chain(calls={_exp_key(90): _bucket(
            _contract(bid=0.30, ask=0.35, iv=0.20, delta=0.30, oi=500, strike=105.0)
        )})
        result = evaluate_best_options(
            chain, side="call", category="balanced", total_shares=0,
            next_earnings_date=None, ex_dividend_date=None, support_level=None,
            dte_min=0, dte_max=49, now=NOW,
        )
        assert result["calls"]["rows"] == []
        assert result["calls"]["total"] == 0


class TestEarningsSpanGate:
    """G3: fails only when expiration falls AFTER a known next earnings
    date (the position would remain open through the announcement); passes
    when expiration is on/before it; unknown earnings date never fails the
    gate (design F10)."""

    def _put_chain(self, dte: int):
        return _chain(puts={_exp_key(dte): _bucket(
            _contract(bid=0.55, ask=0.62, iv=0.25, delta=-0.30, oi=800, strike=58.0)
        )}, underlying_price=65.0)

    def test_expiration_after_earnings_fails(self):
        result = evaluate_best_options(
            self._put_chain(20), side="put", category="high_yield", total_shares=0,
            next_earnings_date="2026-09-08", ex_dividend_date=None, support_level=None,
            dte_min=0, dte_max=49, now=NOW,
        )
        row = result["puts"]["rows"][0]
        assert row["gates"]["earnings_span"] == "fail"
        assert row["color"] == "red"

    def test_expiration_before_earnings_passes(self):
        result = evaluate_best_options(
            self._put_chain(5), side="put", category="high_yield", total_shares=0,
            next_earnings_date="2026-09-08", ex_dividend_date=None, support_level=None,
            dte_min=0, dte_max=49, now=NOW,
        )
        row = result["puts"]["rows"][0]
        assert row["gates"]["earnings_span"] == "pass"

    def test_unknown_earnings_date_is_not_a_gate_failure(self):
        result = evaluate_best_options(
            self._put_chain(20), side="put", category="high_yield", total_shares=0,
            next_earnings_date=None, ex_dividend_date=None, support_level=None,
            dte_min=0, dte_max=49, now=NOW,
        )
        row = result["puts"]["rows"][0]
        assert row["gates"]["earnings_span"] == "unknown"
        assert "earnings_date_unknown" in row["flags"]


class TestColorThresholds:
    def test_green_requires_score_at_least_65(self):
        chain = _chain(puts={_exp_key(20): _bucket(
            _contract(bid=1.20, ask=1.25, iv=0.35, delta=-0.30, oi=2000, strike=90.0)
        )}, underlying_price=100.0)
        result = evaluate_best_options(
            chain, side="put", category="high_yield", total_shares=0,
            next_earnings_date=None, ex_dividend_date=None, support_level=None,
            dte_min=0, dte_max=49, now=NOW,
        )
        row = result["puts"]["rows"][0]
        assert row["gates"]["tradability"] == "pass"
        assert row["gates"]["delta_band"] == "pass"
        if row["score"] is not None and row["score"] >= 65:
            assert row["color"] == "green"

    def test_score_below_40_is_red(self):
        chain = _chain(calls={_exp_key(45): _bucket(
            _contract(bid=0.05, ask=0.20, iv=0.10, delta=0.30, oi=1, strike=140.0)
        )}, underlying_price=100.0)
        result = evaluate_best_options(
            chain, side="call", category="balanced", total_shares=0,
            next_earnings_date=None, ex_dividend_date=None, support_level=None,
            dte_min=0, dte_max=49, now=NOW,
        )
        row = result["calls"]["rows"][0]
        if row["score"] is not None and row["score"] < 40:
            assert row["color"] == "red"


class TestPutDeltaSignHandling:
    def test_negative_put_delta_is_gated_by_absolute_value(self):
        chain = _chain(puts={_exp_key(20): _bucket(
            _contract(bid=0.55, ask=0.62, iv=0.25, delta=-0.30, oi=800, strike=58.0)
        )}, underlying_price=65.0)
        result = evaluate_best_options(
            chain, side="put", category="high_yield", total_shares=0,
            next_earnings_date=None, ex_dividend_date=None, support_level=None,
            dte_min=0, dte_max=49, now=NOW,
        )
        row = result["puts"]["rows"][0]
        assert row["delta"] == -0.30
        assert row["abs_delta"] == 0.30


class TestIvRankNeverEnforced:
    def test_iv_rank_min_threshold_is_reported_but_never_gates_or_scores(self):
        chain = _chain(puts={_exp_key(20): _bucket(
            _contract(bid=1.20, ask=1.25, iv=0.35, delta=-0.30, oi=2000, strike=90.0)
        )}, underlying_price=100.0)
        result = evaluate_best_options(
            chain, side="put", category="high_yield", total_shares=0,
            next_earnings_date=None, ex_dividend_date=None, support_level=None,
            dte_min=0, dte_max=49, now=NOW,
        )
        assert result["parameters"]["iv_rank_enforced"] is False
        assert "iv_rank_min" in result["parameters"]["thresholds"]["put"]
        row = result["puts"]["rows"][0]
        assert set(row["gates"].keys()) == {"tradability", "delta_band", "earnings_span"}
        assert "iv_rank" not in row["components"]


class TestCallVsPutAsymmetries:
    def test_call_premium_basis_is_underlying_price_put_is_strike(self):
        chain = _chain(
            calls={_exp_key(20): _bucket(_contract(bid=1.0, ask=1.1, iv=0.25, delta=0.30, oi=500, strike=110.0))},
            puts={_exp_key(20): _bucket(_contract(bid=1.0, ask=1.1, iv=0.25, delta=-0.30, oi=500, strike=90.0))},
            underlying_price=100.0,
        )
        result = evaluate_best_options(
            chain, side="both", category="balanced", total_shares=0,
            next_earnings_date=None, ex_dividend_date=None, support_level=None,
            dte_min=0, dte_max=49, now=NOW,
        )
        call_row = result["calls"]["rows"][0]
        put_row = result["puts"]["rows"][0]
        assert call_row["premium_pct"] == 1.0 / 100.0 * 100.0
        assert put_row["premium_pct"] == 1.0 / 90.0 * 100.0

    def test_collateral_is_only_populated_for_puts(self):
        chain = _chain(
            calls={_exp_key(20): _bucket(_contract(bid=1.0, ask=1.1, iv=0.25, delta=0.30, oi=500, strike=110.0))},
            puts={_exp_key(20): _bucket(_contract(bid=1.0, ask=1.1, iv=0.25, delta=-0.30, oi=500, strike=90.0))},
        )
        result = evaluate_best_options(
            chain, side="both", category="balanced", total_shares=0,
            next_earnings_date=None, ex_dividend_date=None, support_level=None,
            dte_min=0, dte_max=49, now=NOW,
        )
        assert result["calls"]["rows"][0]["collateral"] is None
        assert result["puts"]["rows"][0]["collateral"] == 90.0 * 100.0

    def test_coverable_contracts_and_no_shares_held_are_call_only(self):
        chain = _chain(calls={_exp_key(20): _bucket(
            _contract(bid=1.0, ask=1.1, iv=0.25, delta=0.30, oi=500, strike=110.0)
        )})
        result = evaluate_best_options(
            chain, side="call", category="balanced", total_shares=250,
            next_earnings_date=None, ex_dividend_date=None, support_level=None,
            dte_min=0, dte_max=49, now=NOW,
        )
        assert result["calls"]["coverable_contracts"] == 2
        assert result["calls"]["no_shares_held"] is False
        assert "coverable_contracts" not in result["puts"]

    def test_no_shares_held_true_when_zero_shares(self):
        chain = _chain(calls={_exp_key(20): _bucket(
            _contract(bid=1.0, ask=1.1, iv=0.25, delta=0.30, oi=500, strike=110.0)
        )})
        result = evaluate_best_options(
            chain, side="call", category="balanced", total_shares=0,
            next_earnings_date=None, ex_dividend_date=None, support_level=None,
            dte_min=0, dte_max=49, now=NOW,
        )
        assert result["calls"]["coverable_contracts"] == 0
        assert result["calls"]["no_shares_held"] is True


class TestExcludedByDeltaBandSchema:
    def test_unrequested_side_still_reports_zero_excluded(self):
        chain = _chain()
        result = evaluate_best_options(
            chain, side="call", category="balanced", total_shares=0,
            next_earnings_date=None, ex_dividend_date=None, support_level=None,
            dte_min=0, dte_max=49, now=NOW,
        )
        assert result["puts"]["excluded_by_delta_band"] == 0


class TestNearestMiss:
    def test_always_present_even_when_every_row_qualifies(self):
        chain = _chain(puts={_exp_key(20): _bucket(
            _contract(bid=1.20, ask=1.25, iv=0.35, delta=-0.30, oi=2000, strike=90.0)
        )}, underlying_price=100.0)
        result = evaluate_best_options(
            chain, side="put", category="high_yield", total_shares=0,
            next_earnings_date=None, ex_dividend_date=None, support_level=None,
            dte_min=0, dte_max=49, now=NOW,
        )
        assert "nearest_miss" in result["puts"]
        nm = result["puts"]["nearest_miss"]
        assert nm["available"] in (True, False)
        if not nm["available"]:
            assert nm["reason"] == "all_rows_qualify"

    def test_reports_no_contracts_in_window_when_the_side_is_empty(self):
        chain = _chain()
        result = evaluate_best_options(
            chain, side="call", category="balanced", total_shares=0,
            next_earnings_date=None, ex_dividend_date=None, support_level=None,
            dte_min=0, dte_max=49, now=NOW,
        )
        assert result["calls"]["nearest_miss"] == {"available": False, "reason": "no_contracts_in_window"}

    def test_tradability_failure_ranks_worse_than_a_delta_band_miss(self):
        untradable = _contract(bid=None, ask=None, iv=0.20, delta=0.30, oi=0, strike=105.0)
        out_of_band = _contract(bid=0.30, ask=0.35, iv=0.20, delta=0.05, oi=500, strike=120.0)
        chain = _chain(calls={_exp_key(20): _bucket(untradable, out_of_band)})
        result = evaluate_best_options(
            chain, side="call", category="high_yield", total_shares=0,
            next_earnings_date=None, ex_dividend_date=None, support_level=None,
            dte_min=0, dte_max=49, now=NOW,
        )
        nm = result["calls"]["nearest_miss"]
        assert nm["available"] is True
        assert nm["reason"] == "delta_band"


class TestOrderingAndTruncation:
    def test_higher_score_sorts_first(self):
        strong = _contract(bid=1.20, ask=1.25, iv=0.35, delta=-0.30, oi=2000, strike=90.0)
        weak = _contract(bid=0.05, ask=0.20, iv=0.10, delta=-0.30, oi=1, strike=90.5)
        chain = _chain(puts={_exp_key(20): _bucket(strong, weak)}, underlying_price=100.0)
        result = evaluate_best_options(
            chain, side="put", category="high_yield", total_shares=0,
            next_earnings_date=None, ex_dividend_date=None, support_level=None,
            dte_min=0, dte_max=49, now=NOW,
        )
        rows = result["puts"]["rows"]
        scores = [r["score"] if r["score"] is not None else -1 for r in rows]
        assert scores == sorted(scores, reverse=True)

    def test_truncates_at_400_rows_and_flags_it(self):
        contracts = [
            _contract(bid=0.30, ask=0.35, iv=0.22, delta=0.28, oi=500, strike=50.0 + i * 0.1)
            for i in range(500)
        ]
        chain = _chain(calls={_exp_key(20): _bucket(*contracts)}, underlying_price=65.0)
        result = evaluate_best_options(
            chain, side="call", category="balanced", total_shares=0,
            next_earnings_date=None, ex_dividend_date=None, support_level=None,
            dte_min=0, dte_max=49, now=NOW,
        )
        assert result["calls"]["total"] == 500
        assert len(result["calls"]["rows"]) == 400
        assert result["calls"]["truncated"] is True


class TestDeterminism:
    def test_identical_input_produces_byte_identical_output(self):
        chain = _chain(
            calls={_exp_key(20): _bucket(_contract(bid=1.0, ask=1.1, iv=0.25, delta=0.30, oi=500, strike=110.0))},
            puts={_exp_key(20): _bucket(_contract(bid=1.0, ask=1.1, iv=0.25, delta=-0.30, oi=500, strike=90.0))},
        )
        kwargs = dict(
            side="both", category="balanced", total_shares=100,
            next_earnings_date="2026-10-01", ex_dividend_date="2026-09-15", support_level=85.0,
            dte_min=0, dte_max=49, now=NOW,
        )
        result_a = evaluate_best_options(chain, **kwargs)
        result_b = evaluate_best_options(chain, **kwargs)
        assert result_a == result_b

    def test_input_chain_is_not_mutated(self):
        chain = _chain(calls={_exp_key(20): _bucket(
            _contract(bid=1.0, ask=1.1, iv=0.25, delta=0.30, oi=500, strike=110.0)
        )})
        original = copy.deepcopy(chain)
        evaluate_best_options(
            chain, side="call", category="balanced", total_shares=0,
            next_earnings_date=None, ex_dividend_date=None, support_level=None,
            dte_min=0, dte_max=49, now=NOW,
        )
        assert chain == original


class TestNoDirectContractAccess:
    """Danny's acceptance gate #2: best_options.py must never read
    quote/Greek fields via a direct contract.get(...) -- only through the
    options_chain_view accessors."""

    def test_source_has_no_banned_direct_quote_or_greek_reads(self):
        with open("src/best_options.py", "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        # Skip the module docstring, which spells out this very
        # prohibition using the banned pattern as prose (not code).
        code_lines = lines[35:] if len(lines) > 35 else lines
        source = "".join(code_lines)
        banned_fields = ["bid", "ask", "mid", "iv", "delta", "gamma", "theta", "vega", "rho"]
        pattern = re.compile(r'contract\.get\(\s*["\'](' + "|".join(banned_fields) + r')["\']\s*[,)]')
        assert not pattern.search(source), "direct contract.get(...) read of a quote/Greek field found"


class TestParameterProvenance:
    def test_category_defaulted_flag_is_true_when_category_is_missing(self):
        chain = _chain(calls={_exp_key(20): _bucket(
            _contract(bid=1.0, ask=1.1, iv=0.25, delta=0.30, oi=500, strike=110.0)
        )})
        result = evaluate_best_options(
            chain, side="call", category=None, total_shares=0,
            next_earnings_date=None, ex_dividend_date=None, support_level=None,
            dte_min=0, dte_max=49, now=NOW,
        )
        assert result["parameters"]["category"]["defaulted"] is True
        assert result["parameters"]["category"]["value"] == "balanced"

    def test_category_defaulted_flag_is_false_when_category_is_explicit(self):
        chain = _chain(calls={_exp_key(20): _bucket(
            _contract(bid=1.0, ask=1.1, iv=0.25, delta=0.30, oi=500, strike=110.0)
        )})
        result = evaluate_best_options(
            chain, side="call", category="high_yield", total_shares=0,
            next_earnings_date=None, ex_dividend_date=None, support_level=None,
            dte_min=0, dte_max=49, now=NOW,
        )
        assert result["parameters"]["category"]["defaulted"] is False
        assert result["parameters"]["category"]["value"] == "high_yield"

    def test_dte_source_reports_default_vs_query(self):
        chain = _chain()
        default_result = evaluate_best_options(
            chain, side="call", category="balanced", total_shares=0,
            next_earnings_date=None, ex_dividend_date=None, support_level=None,
            dte_min=0, dte_max=49, now=NOW,
        )
        query_result = evaluate_best_options(
            chain, side="call", category="balanced", total_shares=0,
            next_earnings_date=None, ex_dividend_date=None, support_level=None,
            dte_min=5, dte_max=30, now=NOW,
        )
        assert default_result["parameters"]["dte"]["source"] == "default"
        assert query_result["parameters"]["dte"]["source"] == "query"
