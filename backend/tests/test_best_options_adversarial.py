"""Basher's adversarial acceptance suite for Best Options
(``src/best_options.py``, ``src/category_params.py``,
``src/options_chain_filters.py``), reviewed against the ACCEPTED design at
``.squad/decisions/inbox/danny-best-options-design.md`` (accepted
2026-08-29).

Scope and intent (Basher's charter: testing/QA, never production code):
  * Exercises the REAL ``evaluate_best_options`` entrypoint end to end for
    every case below -- no mocking of the evaluator itself, no mutual
    fakes with Linus's/Rusty's/Livingston's own test files. Fixtures here
    are independently authored (not imported from ``test_best_options.py``)
    so this suite cannot silently inherit an author's own blind spot.
  * Deliberately adversarial: boundary values, missing/invalid data,
    stale/ambiguous inputs, and exact numeric thresholds are targeted
    rather than "happy path" examples. Several fixtures below are
    calibrated by calling the module's OWN private component helpers
    (``_component_annualized_return`` etc.) to solve for the exact `bid`
    that produces a target raw score -- this is white-box test
    construction, not reliance on hardcoded magic numbers that could
    silently drift from the real formula.
  * No test in this file expects/asserts an IV Rank *filter* effect, and
    none constructs, mocks, patches, or otherwise touches an LLM/agent
    entry point -- this module is pure, no I/O, no LLM (module docstring,
    design F3/F8). ``TestNoLlmOrIvRankEnforcementSurface`` makes this an
    explicit, checked invariant rather than an implicit assumption.
  * ``TestRowInclusionDesignDeviation`` documents, rather than silently
    accepts, a real behavioural gap between the ACCEPTED design's literal
    text (design §4.1/§4.2: delta band is a Layer-A *gate*, colour-only,
    row still shown) and the shipped implementation (delta band is a row
    *filter*: excluded rows never appear in ``rows`` at all). This is
    covered here as "documents current behaviour" rather than "asserts
    the design's literal text", because failing it would only prove what
    is already known and does not, by itself, resolve which behaviour is
    actually wanted -- see this reviewer's findings in
    ``.squad/agents/basher/history.md`` and
    ``.squad/decisions/inbox/basher-best-options-review.md`` for why this
    is flagged as a REJECT-worthy process gap rather than silently
    accepted as correct.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.best_options import (
    WEIGHTS,
    _component_annualized_return,
    _component_cushion,
    _component_delta_fit,
    _component_liquidity,
    evaluate_best_options,
)
from src.category_params import thresholds_for

NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
TODAY = date(2026, 8, 29)


def _exp_key(days: int) -> str:
    return (TODAY + timedelta(days=days)).strftime("%Y%m%d")


def _contract(
    *,
    bid=1.0,
    ask=1.05,
    iv=0.30,
    delta=0.24,
    oi=500,
    strike=100.0,
    volume=10,
    quote_asof="2026-08-29T11:00:00Z",
    greeks_valid=True,
    include_delta=True,
    include_meta=True,
):
    mid = round((bid + ask) / 2, 6) if bid is not None and ask is not None else None
    out = {
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "iv": iv,
        "gamma": 0.01,
        "theta": -0.02,
        "vega": 0.05,
        "rho": 0.01,
        "lastPrice": bid,
        "openInterest": oi,
        "volume": volume,
        "inTheMoney": False,
    }
    if include_delta:
        out["delta"] = delta
    if include_meta:
        out["_meta"] = {
            "quote_asof": quote_asof,
            "greeks_valid": greeks_valid,
            "greeks_asof": quote_asof,
        }
    return out


def _chain(*, calls=None, puts=None, symbol="TEST", underlying_price=100.0, timestamp="2026-08-29T11:00:00Z"):
    return {
        "symbol": symbol,
        "timestamp": timestamp,
        "underlying_price": underlying_price,
        "calls": calls or {},
        "puts": puts or {},
    }


def _evaluate(chain, *, side="call", category="balanced", total_shares=0,
              next_earnings_date=None, ex_dividend_date=None, support_level=None,
              dte_min=0, dte_max=49, now=NOW):
    return evaluate_best_options(
        chain, side=side, category=category, total_shares=total_shares,
        next_earnings_date=next_earnings_date, ex_dividend_date=ex_dividend_date,
        support_level=support_level, dte_min=dte_min, dte_max=dte_max, now=now,
    )


def _bid_ask_for_raw_score(
    raw_target: float,
    *,
    side: str,
    spot: float,
    strike: float,
    iv: float,
    dte: int,
    abs_delta: float,
    delta_lo: float,
    delta_hi: float,
    premium_min_pct: float,
    oi: float,
    spread_ratio: float = 1.05,
) -> tuple[float, float]:
    """White-box fixture solver: computes the exact `bid` (and a matching
    `ask` that keeps `spread_pct` -- and therefore the liquidity component
    -- fixed regardless of `bid`'s absolute size) that makes the four
    weighted score components sum to exactly `raw_target` (0..1), given a
    fixed strike/delta/oi/dte/side. Uses the evaluator's OWN private
    component functions (not a re-derivation) so this can never silently
    drift from the real scoring formula. Only `annualized_return` (driven
    by `bid`) is solved for; `cushion`/`delta_fit`/`liquidity` are held
    fixed by the other parameters.

    The floor-clearing ratio (`premium_wait_pct` / `premium_min_pct` / 2)
    is dte-independent -- both `premium_pct` and `effective_wait_pct`
    scale by `dte / 30` -- so a raw target reachable at one dte is
    reachable (without tripping the wait-floor override) at any dte.
    """
    fit = _component_delta_fit(abs_delta, delta_lo, delta_hi)
    cushion = _component_cushion(side, spot, strike, iv, dte)
    probe_bid, probe_ask = 1.0, spread_ratio
    probe_mid = (probe_bid + probe_ask) / 2.0
    liq = _component_liquidity(oi, probe_bid, probe_ask, probe_mid)
    fixed = WEIGHTS["cushion"] * cushion + WEIGHTS["delta_fit"] * fit + WEIGHTS["liquidity"] * liq
    ann_needed = (raw_target - fixed) / WEIGHTS["annualized_return"]
    assert 0.0 <= ann_needed <= 1.0, (
        f"raw target {raw_target} unreachable with this fixed baseline "
        f"(fixed={fixed:.4f}, ann_needed={ann_needed:.4f}); adjust strike/delta/oi."
    )
    premium_pct = ann_needed * 2.0 * premium_min_pct * dte / 30.0
    bid = premium_pct / 100.0 * spot
    ask = bid * spread_ratio
    return bid, ask


# ---------------------------------------------------------------------------
# DTE window boundaries (task: "DTE 0/49/50")
# ---------------------------------------------------------------------------

class TestDteWindowBoundaries:
    """Default window is [0, 49] inclusive (`DEFAULT_DTE_MIN`/`_MAX`).
    `SYSTEM_DTE_CAP` (45) is a *separate*, informational boundary (the
    `exceeds_system_dte_cap` flag never removes a row or changes colour)."""

    def test_dte_zero_is_included(self):
        chain = _chain(calls={_exp_key(0): {"100.0": _contract(strike=100.0)}})
        result = _evaluate(chain)
        assert len(result["calls"]["rows"]) == 1
        assert result["calls"]["rows"][0]["dte"] == 0

    def test_dte_49_is_included(self):
        chain = _chain(calls={_exp_key(49): {"100.0": _contract(strike=100.0)}})
        result = _evaluate(chain)
        assert len(result["calls"]["rows"]) == 1
        assert result["calls"]["rows"][0]["dte"] == 49

    def test_dte_50_is_excluded_by_default_window(self):
        chain = _chain(calls={_exp_key(50): {"100.0": _contract(strike=100.0)}})
        result = _evaluate(chain)
        assert result["calls"]["rows"] == []
        assert result["calls"]["total"] == 0
        # Excluded entirely by the DTE filter (never reaches this module),
        # so it cannot appear in nearest_miss either -- distinct from the
        # delta-band exclusion case, which IS still describable.
        assert result["calls"]["nearest_miss"] == {"available": False, "reason": "no_contracts_in_window"}

    def test_negative_dte_expired_contract_is_excluded(self):
        chain = _chain(calls={_exp_key(-1): {"100.0": _contract(strike=100.0)}})
        result = _evaluate(chain)
        assert result["calls"]["rows"] == []
        assert result["calls"]["total"] == 0

    def test_dte_45_does_not_carry_exceeds_system_dte_cap_flag(self):
        chain = _chain(calls={_exp_key(45): {"100.0": _contract(strike=100.0)}})
        result = _evaluate(chain)
        row = result["calls"]["rows"][0]
        assert "exceeds_system_dte_cap" not in row["flags"]

    def test_dte_46_carries_exceeds_system_dte_cap_flag_but_still_scores(self):
        chain = _chain(calls={_exp_key(46): {"100.0": _contract(strike=100.0)}})
        result = _evaluate(chain)
        row = result["calls"]["rows"][0]
        assert "exceeds_system_dte_cap" in row["flags"]
        # Informational only (design F10-style flag): does not null the
        # score or force a colour.
        assert row["score"] is not None
        assert row["color"] in ("green", "yellow", "red")

    def test_explicit_dte_window_can_include_50_when_requested(self):
        # dte_max is caller-controlled; the DEFAULT window excludes 50, but
        # an explicit wider request (as the endpoint's own query-param
        # validation permits, up to its own cap) must still include it --
        # 49 is a DEFAULT boundary, not a hardcoded ceiling in this module.
        chain = _chain(calls={_exp_key(50): {"100.0": _contract(strike=100.0)}})
        result = _evaluate(chain, dte_max=60)
        assert len(result["calls"]["rows"]) == 1
        assert result["calls"]["rows"][0]["dte"] == 50


# ---------------------------------------------------------------------------
# Absolute delta normalization + category-specific bands (task item)
# ---------------------------------------------------------------------------

_ALL_CATEGORIES = ["aristocrat", "compounder", "balanced", "high_yield", "rising_star"]


class TestAbsoluteDeltaNormalizationAcrossCategories:
    """`abs(delta)` must be what is compared to the category band -- CSP
    deltas are stored/reported negative but must be treated identically to
    an equal-magnitude positive (call) delta for band membership. Sweeps
    all 5 categories x both sides at each category's own in-band/
    out-of-band edges (not just one hardcoded example)."""

    @pytest.mark.parametrize("category", _ALL_CATEGORIES)
    @pytest.mark.parametrize("side,strategy", [("call", "covered_call"), ("put", "cash_secured_put")])
    def test_in_band_edges_included_out_of_band_excluded(self, category, side, strategy):
        th = thresholds_for(strategy, category)
        lo, hi = th["delta_lo"], th["delta_hi"]
        sign = 1 if side == "call" else -1
        bucket_key = "calls" if side == "call" else "puts"

        def rows_for(raw_delta):
            c = _contract(delta=raw_delta, strike=100.0)
            chain = _chain(**{bucket_key: {_exp_key(20): {"100.0": c}}})
            result = _evaluate(chain, side=side, category=category)
            return result[bucket_key]["rows"], result[bucket_key]["excluded_by_delta_band"]

        # Exactly at both inclusive edges -> included.
        rows_lo, excl_lo = rows_for(sign * lo)
        assert len(rows_lo) == 1 and excl_lo == 0
        rows_hi, excl_hi = rows_for(sign * hi)
        assert len(rows_hi) == 1 and excl_hi == 0

        # Just outside either edge -> excluded (never in rows), but the
        # sign of the raw delta must not matter -- only its magnitude.
        rows_below, excl_below = rows_for(sign * (lo - 0.01))
        assert rows_below == [] and excl_below == 1
        rows_above, excl_above = rows_for(sign * (hi + 0.01))
        assert rows_above == [] and excl_above == 1

        # Sign flip on an in-band magnitude must not change eligibility --
        # this is the core "absolute delta normalization" guarantee.
        midpoint = (lo + hi) / 2.0
        rows_pos, _ = rows_for(midpoint)
        rows_neg, _ = rows_for(-midpoint)
        assert len(rows_pos) == len(rows_neg) == 1


# ---------------------------------------------------------------------------
# Calls vs puts asymmetric flags/fields
# ---------------------------------------------------------------------------

class TestCallsVsPutsAsymmetry:
    def test_collateral_only_populated_for_puts(self):
        chain = _chain(
            calls={_exp_key(20): {"105.0": _contract(delta=0.24, strike=105.0)}},
            puts={_exp_key(20): {"95.0": _contract(delta=-0.24, strike=95.0)}},
        )
        result = _evaluate(chain, side="both")
        call_row = result["calls"]["rows"][0]
        put_row = result["puts"]["rows"][0]
        assert call_row["collateral"] is None
        assert put_row["collateral"] == pytest.approx(95.0 * 100.0)

    def test_ex_div_within_dte_flag_only_applies_to_calls(self):
        # Ex-dividend 10 days out, DTE=20, strike close to spot (<1.10x) --
        # design's early-assignment risk flag for covered calls only.
        ex_div = (TODAY + timedelta(days=10)).isoformat()
        chain = _chain(
            calls={_exp_key(20): {"101.0": _contract(delta=0.24, strike=101.0)}},
            puts={_exp_key(20): {"99.0": _contract(delta=-0.24, strike=99.0)}},
        )
        result = _evaluate(chain, side="both", ex_dividend_date=ex_div)
        assert "ex_div_within_dte" in result["calls"]["rows"][0]["flags"]
        assert "ex_div_within_dte" not in result["puts"]["rows"][0]["flags"]

    def test_below_support_flag_only_applies_to_puts(self):
        chain = _chain(
            calls={_exp_key(20): {"105.0": _contract(delta=0.24, strike=105.0)}},
            puts={_exp_key(20): {"90.0": _contract(delta=-0.24, strike=90.0)}},
        )
        result = _evaluate(chain, side="both", support_level=95.0)
        assert "below_support" not in result["calls"]["rows"][0]["flags"]
        assert "below_support" in result["puts"]["rows"][0]["flags"]

    def test_call_and_put_thresholds_differ_for_same_category(self):
        # balanced: CC band (0.20-0.30) vs CSP band (0.20-0.30) share a
        # band here, but premium floors differ (0.8/0.5 vs 1.2/0.7) --
        # confirms the two sides are never accidentally sharing one table.
        cc = thresholds_for("covered_call", "balanced")
        csp = thresholds_for("cash_secured_put", "balanced")
        assert cc["premium_min_pct"] != csp["premium_min_pct"]
        assert cc["premium_wait_pct"] != csp["premium_wait_pct"]


# ---------------------------------------------------------------------------
# Deterministic ordering / tie-breaking (task item)
# ---------------------------------------------------------------------------

class TestDeterministicOrderingAndTieBreaking:
    """Sort key (design §4.5): 1) score desc (None last) 2) DTE asc
    3) |abs_delta - band_midpoint| asc. Constructs EXACT ties (not just
    plausibly-different scores) to prove the secondary/tertiary keys
    actually run, using the real component functions to solve for bids
    that land on identical integer scores."""

    CATEGORY = "balanced"
    STRATEGY = "covered_call"

    def test_tie_on_score_broken_by_dte_ascending(self):
        th = thresholds_for(self.STRATEGY, self.CATEGORY)
        spot, iv, strike, abs_delta, oi = 100.0, 0.30, 103.0, 0.24, 500
        bid20, ask20 = _bid_ask_for_raw_score(
            0.60, side="call", spot=spot, strike=strike, iv=iv, dte=20, abs_delta=abs_delta,
            delta_lo=th["delta_lo"], delta_hi=th["delta_hi"], premium_min_pct=th["premium_min_pct"], oi=oi,
        )
        bid35, ask35 = _bid_ask_for_raw_score(
            0.60, side="call", spot=spot, strike=strike, iv=iv, dte=35, abs_delta=abs_delta,
            delta_lo=th["delta_lo"], delta_hi=th["delta_hi"], premium_min_pct=th["premium_min_pct"], oi=oi,
        )
        chain = _chain(calls={
            _exp_key(20): {f"{strike:.4f}": _contract(bid=bid20, ask=ask20, delta=abs_delta, strike=strike)},
            _exp_key(35): {f"{strike:.4f}": _contract(bid=bid35, ask=ask35, delta=abs_delta, strike=strike)},
        })
        result = _evaluate(chain, category=self.CATEGORY)
        rows = result["calls"]["rows"]
        assert len(rows) == 2
        assert rows[0]["score"] == rows[1]["score"] == 60
        assert [r["dte"] for r in rows] == [20, 35]  # lower DTE first on a tie

    def test_tie_on_score_and_dte_broken_by_delta_distance_from_midpoint(self):
        th = thresholds_for(self.STRATEGY, self.CATEGORY)
        lo, hi = th["delta_lo"], th["delta_hi"]
        midpoint = (lo + hi) / 2.0
        spot, iv, strike, oi, dte = 100.0, 0.30, 103.0, 500, 20
        d_close, d_far = midpoint - 0.01, midpoint - 0.04  # both in-band, different distances
        bid_close, ask_close = _bid_ask_for_raw_score(
            0.60, side="call", spot=spot, strike=strike, iv=iv, dte=dte, abs_delta=d_close,
            delta_lo=lo, delta_hi=hi, premium_min_pct=th["premium_min_pct"], oi=oi,
        )
        bid_far, ask_far = _bid_ask_for_raw_score(
            0.60, side="call", spot=spot, strike=strike, iv=iv, dte=dte, abs_delta=d_far,
            delta_lo=lo, delta_hi=hi, premium_min_pct=th["premium_min_pct"], oi=oi,
        )
        # Two distinct dict keys that both parse to the identical numeric
        # strike (the evaluator keys off `float(strike_key)`, not the
        # contract's own "strike" field) -- holds `cushion` exactly equal
        # across both rows so only the delta-distance term can break the tie.
        chain = _chain(calls={_exp_key(dte): {
            "103.00": _contract(bid=bid_far, ask=ask_far, delta=d_far, strike=strike),
            "103.0": _contract(bid=bid_close, ask=ask_close, delta=d_close, strike=strike),
        }})
        result = _evaluate(chain, category=self.CATEGORY)
        rows = result["calls"]["rows"]
        assert len(rows) == 2
        assert rows[0]["score"] == rows[1]["score"] == 60
        assert rows[0]["dte"] == rows[1]["dte"] == 20
        assert [r["abs_delta"] for r in rows] == [d_close, d_far]  # closer to midpoint first

    def test_score_none_rows_sort_last(self):
        # A gate failure (score=None) must never outrank any scored row,
        # regardless of how low that row's score is.
        chain = _chain(calls={_exp_key(20): {
            "100.0": _contract(bid=None, ask=None, delta=0.24, strike=100.0),  # no bid -> tradability fails -> score None
            "101.0": _contract(bid=0.01, ask=0.02, delta=0.24, strike=101.0),  # tiny but scored
        }})
        result = _evaluate(chain)
        rows = result["calls"]["rows"]
        assert len(rows) == 2
        assert rows[-1]["score"] is None

    def test_ordering_is_repeatable_across_calls(self):
        # Byte-identical determinism (design's own framing): the same
        # input must produce the same row order every time, not just "a"
        # valid order.
        chain = _chain(calls={_exp_key(20): {
            "100.0": _contract(bid=1.0, ask=1.05, delta=0.24, strike=100.0),
            "101.0": _contract(bid=1.2, ask=1.25, delta=0.26, strike=101.0),
            "102.0": _contract(bid=0.8, ask=0.85, delta=0.22, strike=102.0),
        }})
        first = _evaluate(chain)["calls"]["rows"]
        second = _evaluate(chain)["calls"]["rows"]
        assert [r["strike"] for r in first] == [r["strike"] for r in second]


# ---------------------------------------------------------------------------
# Score / colour threshold boundaries (task: "39.999/40/64.999/65")
# ---------------------------------------------------------------------------

class TestScoreColorThresholdsExact:
    """Colour thresholds (design §4.4): green >= 65, yellow 40..64, red < 40
    (or any safety/floor override). Every bid below is solved via the
    module's OWN component math (`_bid_ask_for_raw_score`), then run
    through the REAL `evaluate_best_options`, so the assertion is checking
    actual end-to-end rounding behaviour, not a hand re-derivation that
    could itself be wrong."""

    CATEGORY = "balanced"
    STRATEGY = "covered_call"
    SPOT, IV, STRIKE, DTE, OI = 100.0, 0.30, 105.93361968175093, 30, 1
    ABS_DELTA = 0.30  # exactly at balanced CC's upper band edge (0.20-0.30)

    def _row_for_raw(self, raw_target):
        th = thresholds_for(self.STRATEGY, self.CATEGORY)
        bid, ask = _bid_ask_for_raw_score(
            raw_target, side="call", spot=self.SPOT, strike=self.STRIKE, iv=self.IV, dte=self.DTE,
            abs_delta=self.ABS_DELTA, delta_lo=th["delta_lo"], delta_hi=th["delta_hi"],
            premium_min_pct=th["premium_min_pct"], oi=self.OI, spread_ratio=1.6,
        )
        chain = _chain(calls={_exp_key(self.DTE): {
            f"{self.STRIKE:.4f}": _contract(bid=bid, ask=ask, delta=self.ABS_DELTA, strike=self.STRIKE, oi=self.OI)
        }})
        result = _evaluate(chain, category=self.CATEGORY)
        return result["calls"]["rows"][0]

    def test_raw_0_39_is_score_39_red(self):
        row = self._row_for_raw(0.39)
        assert row["score"] == 39
        assert row["color"] == "red"

    def test_raw_0_40_is_score_40_yellow(self):
        row = self._row_for_raw(0.40)
        assert row["score"] == 40
        assert row["color"] == "yellow"

    def test_raw_0_64_is_score_64_yellow(self):
        row = self._row_for_raw(0.64)
        assert row["score"] == 64
        assert row["color"] == "yellow"

    def test_raw_0_65_is_score_65_green(self):
        row = self._row_for_raw(0.65)
        assert row["score"] == 65
        assert row["color"] == "green"

    def test_raw_0_39999_rounds_up_to_40_yellow_not_39_red(self):
        # Demonstrates round-half-up-ish behaviour at the yellow boundary:
        # a raw score infinitesimally below the 40-point mark must still
        # resolve correctly once rounded to the nearest integer.
        row = self._row_for_raw(0.39999)
        assert row["score"] == 40
        assert row["color"] == "yellow"

    def test_raw_0_64999_rounds_up_to_65_green_not_64_yellow(self):
        row = self._row_for_raw(0.64999)
        assert row["score"] == 65
        assert row["color"] == "green"


# ---------------------------------------------------------------------------
# Zero / missing bid
# ---------------------------------------------------------------------------

class TestZeroOrMissingBid:
    def test_zero_bid_fails_tradability_row_still_shown_red_score_null(self):
        chain = _chain(calls={_exp_key(20): {"100.0": _contract(bid=0.0, ask=1.05, strike=100.0)}})
        result = _evaluate(chain)
        rows = result["calls"]["rows"]
        assert len(rows) == 1  # tradability is colour-only, NOT a row filter
        row = rows[0]
        assert row["gates"]["tradability"] == "fail"
        assert row["score"] is None
        assert row["color"] == "red"

    def test_missing_bid_key_fails_tradability_same_as_zero(self):
        c = _contract(strike=100.0)
        del c["bid"]
        chain = _chain(calls={_exp_key(20): {"100.0": c}})
        result = _evaluate(chain)
        row = result["calls"]["rows"][0]
        assert row["gates"]["tradability"] == "fail"
        assert row["score"] is None
        assert row["color"] == "red"

    def test_zero_bid_is_reported_as_null_not_as_zero(self):
        # Absence-not-zero (Z1 accessor contract): a zero bid must read
        # back as `None`, never a numeric 0.0 that a client could
        # mistakenly format as "$0.00" instead of "no quote".
        chain = _chain(calls={_exp_key(20): {"100.0": _contract(bid=0.0, ask=1.05, strike=100.0)}})
        row = _evaluate(chain)["calls"]["rows"][0]
        assert row["bid"] is None


# ---------------------------------------------------------------------------
# Missing / invalid Greeks
# ---------------------------------------------------------------------------

class TestMissingOrInvalidGreeks:
    def test_delta_key_entirely_absent_fails_closed_excluded_from_rows(self):
        c = _contract(strike=100.0, include_delta=False)
        chain = _chain(calls={_exp_key(20): {"100.0": c}})
        result = _evaluate(chain)
        assert result["calls"]["rows"] == []
        assert result["calls"]["excluded_by_delta_band"] == 1
        nm = result["calls"]["nearest_miss"]
        assert nm["available"] is True
        assert nm["reason"] == "delta_band"
        assert "delta unavailable" in nm["detail"]

    def test_greeks_valid_false_nulls_delta_even_with_legacy_numeric_value(self):
        # Z4 accessor contract: an explicit greeks_valid=False must null
        # the Greek even if a stale numeric delta is still physically
        # present on the contract -- this also fails G1 tradability
        # (Greeks must be valid to assess risk), so the row is excluded
        # from `rows` for BOTH reasons; nearest_miss ranks tradability
        # (tier 5, least fixable) ahead of delta_band (tier 3).
        c = _contract(strike=100.0, delta=0.24, greeks_valid=False)
        chain = _chain(calls={_exp_key(20): {"100.0": c}})
        result = _evaluate(chain)
        assert result["calls"]["rows"] == []
        nm = result["calls"]["nearest_miss"]
        assert nm["reason"] == "tradability"

    def test_missing_greeks_meta_entirely_trusts_raw_numeric_delta(self):
        # A hand-built/legacy contract with no `_meta` block at all is not
        # the contamination Z4 targets -- its raw delta must be trusted.
        c = _contract(strike=100.0, delta=0.24, include_meta=False)
        chain = _chain(calls={_exp_key(20): {"100.0": c}})
        result = _evaluate(chain)
        assert len(result["calls"]["rows"]) == 1
        assert result["calls"]["rows"][0]["abs_delta"] == 0.24


# ---------------------------------------------------------------------------
# Stale chains
# ---------------------------------------------------------------------------

class TestStaleChainNeverDowngradesColor:
    def test_stale_quote_flagged_but_does_not_downgrade_an_otherwise_green_row(self):
        old_ts = "2026-08-20T11:00:00Z"  # 9 days before NOW; default staleness is 24h
        c = _contract(bid=1.5, ask=1.6, delta=0.24, strike=100.0, quote_asof=old_ts)
        chain = _chain(calls={_exp_key(30): {"100.0": c}})
        result = _evaluate(chain)
        row = result["calls"]["rows"][0]
        assert row["stale"] is True
        assert "stale_quote" in row["flags"]
        assert row["color"] == "green"  # staleness never gates or recolors

    def test_fresh_quote_is_not_flagged_stale(self):
        c = _contract(bid=1.5, ask=1.6, delta=0.24, strike=100.0, quote_asof="2026-08-29T11:30:00Z")
        chain = _chain(calls={_exp_key(30): {"100.0": c}})
        row = _evaluate(chain)["calls"]["rows"][0]
        assert row["stale"] is False
        assert "stale_quote" not in row["flags"]

    def test_stale_contracts_counted_in_chain_level_parameters(self):
        old_ts = "2026-08-20T11:00:00Z"
        chain = _chain(calls={_exp_key(30): {
            "100.0": _contract(bid=1.5, ask=1.6, delta=0.24, strike=100.0, quote_asof=old_ts),
            "101.0": _contract(bid=1.4, ask=1.5, delta=0.23, strike=101.0),
        }})
        result = _evaluate(chain)
        assert result["parameters"]["chain"]["stale_contracts"] == 1
        assert result["parameters"]["chain"]["total_contracts"] == 2


# ---------------------------------------------------------------------------
# Unknown / known / spanning earnings
# ---------------------------------------------------------------------------

class TestEarningsGate:
    def test_unknown_earnings_flags_only_never_gates_a_green_row(self):
        c = _contract(bid=1.5, ask=1.6, delta=0.24, strike=100.0)
        chain = _chain(calls={_exp_key(30): {"100.0": c}})
        row = _evaluate(chain, next_earnings_date=None)["calls"]["rows"][0]
        assert row["gates"]["earnings_span"] == "unknown"
        assert "earnings_date_unknown" in row["flags"]
        assert row["color"] == "green"
        assert row["score"] is not None

    def test_expiration_exactly_on_earnings_date_passes(self):
        earn = (TODAY + timedelta(days=20)).isoformat()
        c = _contract(bid=1.5, ask=1.6, delta=0.24, strike=100.0)
        chain = _chain(calls={_exp_key(20): {"100.0": c}})
        row = _evaluate(chain, next_earnings_date=earn)["calls"]["rows"][0]
        assert row["gates"]["earnings_span"] == "pass"
        assert row["color"] == "green"

    def test_expiration_one_day_after_earnings_spans_it_and_fails(self):
        earn = (TODAY + timedelta(days=20)).isoformat()
        c = _contract(bid=1.5, ask=1.6, delta=0.24, strike=100.0)
        chain = _chain(calls={_exp_key(21): {"100.0": c}})
        result = _evaluate(chain, next_earnings_date=earn)
        row = result["calls"]["rows"][0]
        assert row["gates"]["earnings_span"] == "fail"
        # Absence-not-zero: a failed safety gate nulls the score, it does
        # not just force a low numeric score.
        assert row["score"] is None
        assert row["color"] == "red"


# ---------------------------------------------------------------------------
# Sparse liquidity boundary
# ---------------------------------------------------------------------------

class TestSparseLiquidityBoundary:
    def test_open_interest_zero_fails_tradability_row_still_shown(self):
        c = _contract(bid=1.5, ask=1.6, delta=0.24, strike=100.0, oi=0)
        chain = _chain(calls={_exp_key(30): {"100.0": c}})
        row = _evaluate(chain)["calls"]["rows"][0]
        assert row["gates"]["tradability"] == "fail"
        assert row["score"] is None
        assert row["color"] == "red"

    def test_open_interest_exactly_one_passes_tradability(self):
        c = _contract(bid=1.5, ask=1.6, delta=0.24, strike=100.0, oi=1)
        chain = _chain(calls={_exp_key(30): {"100.0": c}})
        row = _evaluate(chain)["calls"]["rows"][0]
        assert row["gates"]["tradability"] == "pass"
        assert row["score"] is not None


# ---------------------------------------------------------------------------
# Category profile default / provenance (task item)
# ---------------------------------------------------------------------------

class TestCategoryProfileDefaultAndProvenance:
    @pytest.mark.parametrize("category", _ALL_CATEGORIES)
    @pytest.mark.parametrize("form", ["underscore", "space", "title", "hyphen"])
    def test_recognized_category_never_defaults_across_input_forms(self, category, form):
        if form == "underscore":
            raw = category
        elif form == "space":
            raw = category.replace("_", " ")
        elif form == "title":
            raw = category.replace("_", " ").title()
        else:  # hyphen (only meaningful for the two hyphen-alias categories)
            raw = category.replace("_", "-")
        c = _contract(bid=1.5, ask=1.6, delta=0.24, strike=100.0)
        chain = _chain(calls={_exp_key(20): {"100.0": c}})
        result = _evaluate(chain, category=raw)
        assert result["parameters"]["category"]["value"] == category
        assert result["parameters"]["category"]["defaulted"] is False
        assert result["parameters"]["category"]["raw"] == raw
        # The thresholds actually applied must match the resolved category
        # exactly -- not silently fall back to "balanced" under the hood
        # while still reporting `defaulted=False`.
        expected = thresholds_for("covered_call", category)
        assert result["parameters"]["thresholds"]["call"] == expected

    @pytest.mark.parametrize("raw", [None, "", "   ", "not_a_real_category", "unrecognized-value"])
    def test_unrecognized_or_missing_category_defaults_to_balanced_with_provenance_flag(self, raw):
        c = _contract(bid=1.5, ask=1.6, delta=0.24, strike=100.0)
        chain = _chain(calls={_exp_key(20): {"100.0": c}})
        result = _evaluate(chain, category=raw)
        assert result["parameters"]["category"]["value"] == "balanced"
        assert result["parameters"]["category"]["defaulted"] is True
        assert result["parameters"]["category"]["raw"] == raw

    def test_explicit_balanced_is_not_flagged_as_a_guess(self):
        # An explicit "balanced" is a real user choice, not a fallback --
        # design §6 requires the panel to distinguish "we guessed" from
        # "you picked the same value we'd have guessed anyway".
        c = _contract(bid=1.5, ask=1.6, delta=0.24, strike=100.0)
        chain = _chain(calls={_exp_key(20): {"100.0": c}})
        result = _evaluate(chain, category="balanced")
        assert result["parameters"]["category"]["defaulted"] is False


# ---------------------------------------------------------------------------
# DTE-scaled premium expectations (task item)
# ---------------------------------------------------------------------------

class TestDteScaledPremiumExpectations:
    """`effective_min_pct`/`effective_wait_pct` = base_pct * DTE / 30
    (design §4.3/§7's `premium.dte_scaling`)."""

    @pytest.mark.parametrize("dte,expected_multiplier", [(15, 0.5), (30, 1.0), (49, 49 / 30)])
    def test_effective_floors_scale_linearly_with_dte(self, dte, expected_multiplier):
        th = thresholds_for("covered_call", "balanced")
        c = _contract(bid=1.0, ask=1.02, delta=0.24, strike=100.0)
        chain = _chain(calls={_exp_key(dte): {"100.0": c}})
        row = _evaluate(chain, category="balanced")["calls"]["rows"][0]
        assert row["effective_min_pct"] == pytest.approx(th["premium_min_pct"] * expected_multiplier)
        assert row["effective_wait_pct"] == pytest.approx(th["premium_wait_pct"] * expected_multiplier)

    def test_dte_zero_structurally_cannot_fail_the_premium_wait_floor(self):
        # At DTE=0, both effective floors scale to exactly 0 (base_pct * 0
        # / 30), so `premium_pct < effective_wait_pct` can never be true
        # for any non-negative premium -- a direct, literal consequence of
        # the linear DTE-scaling formula at its own zero boundary, not a
        # bug, but load-bearing enough to assert explicitly.
        c = _contract(bid=0.01, ask=0.02, delta=0.24, strike=100.0, oi=500)
        chain = _chain(calls={_exp_key(0): {"100.0": c}})
        row = _evaluate(chain, category="balanced")["calls"]["rows"][0]
        assert row["effective_min_pct"] == 0.0
        assert row["effective_wait_pct"] == 0.0
        assert "premium_below_wait_floor" not in row["flags"]

    def test_dte_zero_structurally_forces_insufficient_data(self):
        # A same-day expiration can never be annualized (`annualized_return`
        # component requires dte > 0) and cushion also requires dte > 0 --
        # together that's 0.45 + 0.25 = 0.70 of the weight basis missing,
        # leaving only delta_fit (0.20) + liquidity (0.10) = 0.30 < 0.5 --
        # so EVERY DTE=0 row is structurally `insufficient_data`, however
        # good its delta fit and liquidity are.
        c = _contract(bid=1.0, ask=1.05, delta=0.25, strike=100.0, oi=500)
        chain = _chain(calls={_exp_key(0): {"100.0": c}})
        row = _evaluate(chain, category="balanced")["calls"]["rows"][0]
        assert row["score"] is None
        assert row["color"] == "yellow"
        assert "insufficient_data" in row["flags"]
        assert sorted(row["components_missing"]) == ["annualized_return", "cushion"]
        assert row["weight_basis"] == pytest.approx(0.30)


# ---------------------------------------------------------------------------
# nearest_miss with existing qualifying (green) rows present (task item)
# ---------------------------------------------------------------------------

class TestNearestMissWithExistingQualifyingRows:
    def test_nearest_miss_reflects_the_best_non_green_row_even_when_a_green_row_exists(self):
        green = _contract(bid=1.5, ask=1.6, delta=0.24, strike=100.0)
        # A second, in-band contract that scores well below green.
        near_miss = _contract(bid=0.5, ask=0.55, delta=0.28, strike=101.0)
        chain = _chain(calls={_exp_key(30): {"100.0": green, "101.0": near_miss}})
        result = _evaluate(chain, category="balanced")
        rows = result["calls"]["rows"]
        assert any(r["color"] == "green" for r in rows)
        nm = result["calls"]["nearest_miss"]
        assert nm["available"] is True
        assert nm["strike"] == 101.0
        assert nm["reason"] == "score"
        assert nm["color"] != "green"

    def test_nearest_miss_absent_reason_all_rows_qualify_when_every_row_is_green(self):
        c = _contract(bid=1.5, ask=1.6, delta=0.24, strike=100.0)
        chain = _chain(calls={_exp_key(30): {"100.0": c}})
        result = _evaluate(chain, category="balanced")
        assert result["calls"]["rows"][0]["color"] == "green"
        assert result["calls"]["nearest_miss"] == {"available": False, "reason": "all_rows_qualify"}

    def test_nearest_miss_tier_ordering_prefers_premium_floor_miss_over_low_score(self):
        # A row that clears safety but misses only the graded premium
        # floor (tier 0) must outrank a row that clears the floor but has
        # a low score (tier 1), even though both are in-band and non-green.
        floor_miss = _contract(bid=0.05, ask=0.10, delta=0.24, strike=100.0)  # premium_pct far below wait floor
        low_score = _contract(bid=0.50, ask=0.55, delta=0.28, strike=101.0)   # clears floor, low score
        chain = _chain(calls={_exp_key(30): {"100.0": floor_miss, "101.0": low_score}})
        result = _evaluate(chain, category="balanced")
        nm = result["calls"]["nearest_miss"]
        assert nm["reason"] == "premium_floor"
        assert nm["strike"] == 100.0


# ---------------------------------------------------------------------------
# Payload / UI-contract invariants (task item)
# ---------------------------------------------------------------------------

class TestPayloadContractInvariants:
    """Locks in the exact shape of `parameters` the frontend consumes,
    since the type contract lives in a different codebase (TypeScript)
    that pytest cannot execute. `frontend/src/types/best-options.ts` and
    `frontend/src/components/BestOptionsParams.tsx` are inspected
    read-only (Basher's charter: no production changes) -- this test
    documents the REAL backend shape those files must match."""

    def _result(self):
        c = _contract(bid=1.5, ask=1.6, delta=0.24, strike=100.0)
        chain = _chain(
            calls={_exp_key(20): {"100.0": c}},
            puts={_exp_key(20): {"90.0": _contract(bid=1.0, ask=1.05, delta=-0.24, strike=90.0)}},
        )
        return _evaluate(chain, side="both")

    def test_thresholds_is_a_per_side_dict_not_a_flat_object(self):
        # See Basher's review: frontend/src/types/best-options.ts currently
        # declares `thresholds` as a FLAT `BestOptionsThresholds` object and
        # `BestOptionsParams.tsx` reads `parameters.thresholds.delta_lo`
        # directly -- this assertion is the backend ground truth that
        # contradicts that frontend assumption (a live UI defect, D2).
        params = self._result()["parameters"]
        assert set(params["thresholds"].keys()) == {"call", "put"}
        for side_key in ("call", "put"):
            assert set(params["thresholds"][side_key].keys()) == {
                "delta_lo", "delta_hi", "premium_min_pct", "premium_wait_pct", "iv_rank_min",
            }
        assert "delta_lo" not in params["thresholds"]

    def test_thresholds_source_and_skill_reference_are_also_per_side_dicts(self):
        params = self._result()["parameters"]
        assert set(params["thresholds_source"].keys()) == {"call", "put"}
        assert set(params["skill_reference"].keys()) == {"call", "put"}
        assert isinstance(params["thresholds_source"]["call"], str)
        assert isinstance(params["skill_reference"]["put"], str)

    def test_row_schema_has_the_fields_a_ui_table_needs(self):
        row = self._result()["calls"]["rows"][0]
        for field in (
            "expiration", "dte", "strike", "bid", "ask", "mid", "delta", "abs_delta",
            "score", "color", "label", "gates", "flags", "components", "components_missing",
        ):
            assert field in row
        assert row["color"] in ("green", "yellow", "red")
        assert row["label"] in ("Preferred", "Acceptable", "Avoid")

    def test_color_thresholds_and_weights_are_echoed_for_the_ui_to_render_legends(self):
        params = self._result()["parameters"]
        assert params["color_thresholds"] == {"green": 65, "yellow": 40}
        assert params["weights"] == WEIGHTS


# ---------------------------------------------------------------------------
# No IV Rank enforcement, no LLM surface (task requirement)
# ---------------------------------------------------------------------------

class TestNoLlmOrIvRankEnforcementSurface:
    def test_iv_rank_is_never_enforced_and_explicitly_labelled_display_only(self):
        c = _contract(bid=1.5, ask=1.6, delta=0.24, strike=100.0)
        chain = _chain(calls={_exp_key(20): {"100.0": c}})
        params = _evaluate(chain, category="aristocrat")["parameters"]
        assert params["iv_rank_enforced"] is False
        assert "not enforced" in params["iv_rank_note"].lower()

    def test_a_high_scoring_row_is_never_penalized_for_missing_iv_rank_data(self):
        # aristocrat category has iv_rank_min=None (never applicable);
        # other categories carry a non-null iv_rank_min purely for display
        # -- neither ever gates or scores a row in THIS module.
        c = _contract(bid=1.5, ask=1.6, delta=0.30, strike=100.0)  # high_yield CC band is [0.25, 0.35]
        chain = _chain(calls={_exp_key(20): {"100.0": c}})
        row = _evaluate(chain, category="high_yield")["calls"]["rows"][0]
        assert row["color"] == "green"
        assert "iv_rank" not in "".join(row["flags"])
        assert set(row["components"].keys()) == {"annualized_return", "cushion", "delta_fit", "liquidity"}

    def test_module_source_imports_no_llm_or_agent_framework(self):
        # Static-analysis guard: `best_options.py` must remain pure
        # (module docstring: "no I/O, no LLM"). A future edit accidentally
        # importing an LLM/agent client here would silently reintroduce
        # non-determinism and network I/O into a path the design requires
        # to be byte-identical for identical input.
        src_path = Path(__file__).resolve().parents[1] / "src" / "best_options.py"
        source = src_path.read_text(encoding="utf-8")
        forbidden = re.compile(
            r"^\s*(import|from)\s+(openai|agent_framework|anthropic|azure\.ai|litellm)\b",
            re.MULTILINE | re.IGNORECASE,
        )
        assert forbidden.search(source) is None

    def test_evaluate_best_options_never_raises_and_never_blocks_on_network(self):
        # A pure function called with a malformed/empty chain must still
        # return a well-formed, total result -- never raise, never hang.
        result = evaluate_best_options(
            {}, side="both", category=None, total_shares=0,
            next_earnings_date=None, ex_dividend_date=None, support_level=None,
            dte_min=0, dte_max=49, now=NOW,
        )
        assert result["status"] == "ok"
        assert result["calls"]["rows"] == []
        assert result["puts"]["rows"] == []


# ---------------------------------------------------------------------------
# Row-inclusion semantics: DTE window AND delta band, both required
# (formerly flagged as an undocumented deviation; now the explicit, binding
# specification per the user's 2026-08-29T11:50 directive)
# ---------------------------------------------------------------------------

class TestRowInclusionDesignDeviation:
    """Binding specification (user directive, 2026-08-29T11:50, superseding the
    ACCEPTED design's own literal §4.1/§4.2 text): a side's `rows` contain all
    and only contracts that are (a) inside the requested DTE window and (b)
    have `abs(delta)` inside that category/side's configured `[delta_lo,
    delta_hi]` band. A contract failing the delta band is never a `rows`
    entry, red or otherwise -- it is describable only via `nearest_miss` and
    counted in `excluded_by_delta_band`.

    History: the ACCEPTED design's literal text read (in isolation) as if
    delta band only coloured a row red rather than excluding it; Linus's
    first implementation followed that reading, then was corrected same-day
    per an unrecorded "product owner" instruction
    (`.squad/decisions/inbox/linus-best-options-scoring.md`). Basher's first
    review pass (`.squad/agents/basher/history.md`) flagged this as
    REJECT-worthy specifically because `.squad/decisions.md` had zero durable
    record of that correction. The user has since directly reconfirmed, in
    this exact wording, that this row-inclusion behaviour is binding --
    closing that process gap for review purposes. (`.squad/decisions.md`
    itself still has no dedicated entry for this; recommended as a follow-up
    ledger cleanup, not a blocker.) This test class is kept as the permanent,
    grep-able regression fixture for the behaviour either way.
    """

    def test_out_of_band_contract_is_excluded_from_rows_not_merely_colored_red(self):
        # high_yield CC band is [0.25, 0.35]; delta=0.05 is far outside it.
        c = _contract(bid=1.0, ask=1.05, delta=0.05, strike=120.0, oi=500)
        chain = _chain(calls={_exp_key(20): {"120.0": c}})
        result = _evaluate(chain, category="high_yield")
        # Design §4.1/§4.2 literal reading would put this row in `rows`,
        # coloured red. The shipped code excludes it entirely instead.
        assert result["calls"]["rows"] == []
        assert result["calls"]["total"] == 0
        assert result["calls"]["excluded_by_delta_band"] == 1
        # It remains describable via nearest_miss, per the shipped code's
        # own compensating claim that "nothing is truly hidden".
        nm = result["calls"]["nearest_miss"]
        assert nm["available"] is True
        assert nm["reason"] == "delta_band"
