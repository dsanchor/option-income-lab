"""Test suite for src/options_chain_merge.py — pure accumulate-and-merge
semantics for the persistent options chain (see
.squad/decisions/inbox/danny-persistent-option-chain-merge.md).

Covers the required test matrix (design §7, Linus's T1-T12) plus the task's
explicit scenarios: Yahoo all-zero bucket, a genuinely bid-less contract with
a positive ask, TradingView partial overlay, stale prior fill, no input
mutation, malformed expiration, and expiration pruning.

Hermetic: no network calls. `recompute_derived` uses a GreeksCalculator with
a fixed risk-free rate (module-internal), so it never fetches ^TNX.
"""

import copy
import math
import random
from datetime import date, datetime, timedelta, timezone

import pytest

from src.options_chain_merge import (
    _iso,
    gate_bucket,
    gate_contract,
    is_accepted,
    merge_prior,
    merge_sources,
    prune_by_expiration,
    recompute_derived,
)
from src.greeks_calculator import GreeksCalculator
from src.options_math import executable_buyback_ask, robust_mid


# ===========================================================================
# Fixtures / helpers
# ===========================================================================

_OCC_SYMBOL = "TEST260101C00100000"


def _yf_contract(**overrides):
    """A realistic yfinance-style contract: every observed field present."""
    base = {
        "contractSymbol": _OCC_SYMBOL,
        "strike": 100.0,
        "bid": 1.0,
        "ask": 1.2,
        "iv": 0.30,
        "lastPrice": 1.1,
        "lastTradeDate": "2026-01-01T15:00:00Z",
        "volume": 10,
        "openInterest": 100,
        "inTheMoney": False,
        "expiration": "20260101",
        "option_type": "call",
    }
    base.update(overrides)
    return base


def _tv_contract(**overrides):
    """A realistic *post-fix* TradingView contract: only the quote group
    (bid/ask/iv) and identity fields are ever present — volume/openInterest/
    lastPrice/lastTradeDate/inTheMoney/contractSymbol are always absent
    (Rule S1), matching the updated `tv_options_chain_fetcher` normalizer.
    """
    base = {
        "strike": 100.0,
        "bid": 1.05,
        "ask": 1.15,
        "iv": 0.32,
        "expiration": "20260101",
        "option_type": "call",
    }
    base.update(overrides)
    return base


def _chain(calls=None, puts=None, symbol="TEST"):
    return {
        "symbol": symbol,
        "timestamp": "2026-01-01T00:00:00Z",
        "calls": calls or {},
        "puts": puts or {},
    }


def _bucket(*contracts):
    """Build a {strike_key: contract} bucket from a list of contracts."""
    return {str(c["strike"]) if c["strike"] != int(c["strike"]) else f"{c['strike']:.1f}": c
            for c in contracts}


def _strip_meta(chain):
    """Deep-copy a chain with every contract's `_meta` key removed, for
    comparing observable field values independent of provenance."""
    result = copy.deepcopy(chain)
    for side in ("calls", "puts"):
        for strikes in result.get(side, {}).values():
            for contract in strikes.values():
                contract.pop("_meta", None)
                contract.pop("_quote_source", None)
    return result


NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


# ===========================================================================
# T1 — is_accepted: per-field validity matrix
# ===========================================================================

class TestIsAcceptedFieldMatrix:
    @pytest.mark.parametrize("value,expected", [
        (None, False), (0, False), (0.0, False), (-1.0, False),
        (float("nan"), False), (float("inf"), False), ("1.20", False),
        (True, False), (1.20, True), (100.0, True),
    ])
    def test_ask(self, value, expected):
        assert is_accepted("ask", value) is expected

    @pytest.mark.parametrize("value,expected", [
        (None, False), (0, False), (0.0, False), (-0.1, False),
        (float("nan"), False), ("0.3", False), (0.3, True),
        (5.0, False),  # sanity cap is exclusive
        (4.999, True),
    ])
    def test_iv(self, value, expected):
        assert is_accepted("iv", value) is expected

    @pytest.mark.parametrize("field", ["bid", "lastPrice"])
    @pytest.mark.parametrize("value,expected", [
        (None, False), (0, True), (0.0, True), (-0.01, False),
        (float("nan"), False), ("1.0", False), (True, False), (2.5, True),
    ])
    def test_bid_and_last_price_zero_is_valid(self, field, value, expected):
        assert is_accepted(field, value) is expected

    @pytest.mark.parametrize("field", ["volume", "openInterest"])
    @pytest.mark.parametrize("value,expected", [
        (None, False), (0, True), (0.0, True), (-1, False),
        (float("nan"), False), ("5", False), (5, True), (5.5, False),
    ])
    def test_volume_and_open_interest(self, field, value, expected):
        assert is_accepted(field, value) is expected

    @pytest.mark.parametrize("value,expected", [
        (None, False), ("garbage", False), ("", False),
        ("2026-01-01T00:00:00Z", True), (1735689600, True),
        (datetime(2026, 1, 1, tzinfo=timezone.utc), True),
    ])
    def test_last_trade_date(self, value, expected):
        assert is_accepted("lastTradeDate", value) is expected

    @pytest.mark.parametrize("value,expected", [
        (None, False), (True, True), (False, True), (1, False), ("true", False),
    ])
    def test_in_the_money_requires_actual_bool(self, value, expected):
        assert is_accepted("inTheMoney", value) is expected

    @pytest.mark.parametrize("value,expected", [
        (None, False), ("", False), ("   ", False), (123, False),
        (_OCC_SYMBOL, True), ("garbage", True),
    ])
    def test_contract_symbol(self, value, expected):
        assert is_accepted("contractSymbol", value) is expected

    @pytest.mark.parametrize("field", ["mid", "delta", "gamma", "theta", "vega", "rho"])
    def test_derived_fields_never_accepted(self, field):
        assert is_accepted(field, 0.5) is False
        assert is_accepted(field, None) is False

    @pytest.mark.parametrize("field", ["strike", "expiration", "option_type"])
    def test_identity_fields_never_accepted(self, field):
        assert is_accepted(field, "anything") is False


# ===========================================================================
# T2/T3 — gate_contract: the trust gate
# ===========================================================================

class TestGateContract:
    def test_bidless_contract_with_valid_ask_is_trusted(self):
        """A genuinely bid-less contract with a positive ask is real — the
        gate must trust it (robust_mid then marks it conservatively)."""
        contract = {"bid": 0.0, "ask": 4.25, "iv": None}
        assert gate_contract(contract) is True
        assert is_accepted("bid", contract["bid"]) is True

    def test_valid_iv_alone_also_trusts_the_gate(self):
        contract = {"bid": 0.0, "ask": None, "iv": 0.22}
        assert gate_contract(contract) is True

    def test_all_zero_quote_group_is_not_trusted(self):
        contract = {"bid": 0.0, "ask": 0.0, "iv": 0.0}
        assert gate_contract(contract) is False

    def test_missing_ask_and_iv_is_not_trusted(self):
        contract = {"bid": 5.0}
        assert gate_contract(contract) is False

    def test_invalid_ask_and_out_of_range_iv_is_not_trusted(self):
        contract = {"bid": 1.0, "ask": -1.0, "iv": 6.0}
        assert gate_contract(contract) is False

    def test_non_dict_is_not_trusted(self):
        assert gate_contract(None) is False
        assert gate_contract("not a contract") is False


# ===========================================================================
# T4/T5 — gate_bucket: per-bucket degeneracy
# ===========================================================================

class TestGateBucket:
    def test_empty_bucket_is_usable(self):
        assert gate_bucket({}) is True

    def test_three_contracts_all_failing_gate_is_degenerate(self):
        bucket = {
            "95.0": {"bid": 0.0, "ask": 0.0, "iv": 0.0},
            "100.0": {"bid": 0.0, "ask": 0.0, "iv": 0.0},
            "105.0": {"bid": 0.0, "ask": 0.0, "iv": 0.0},
        }
        assert gate_bucket(bucket) is False

    def test_two_contracts_all_zero_is_not_degenerate(self):
        """Below the floor of 3, per-contract gating alone is the safe
        mechanism — the bucket itself is not thrown out."""
        bucket = {
            "95.0": {"bid": 0.0, "ask": 0.0, "iv": 0.0},
            "100.0": {"bid": 0.0, "ask": 0.0, "iv": 0.0},
        }
        assert gate_bucket(bucket) is True
        # ...but every contract still individually fails the trust gate.
        assert all(not gate_contract(c) for c in bucket.values())

    def test_three_contracts_one_valid_is_not_degenerate(self):
        bucket = {
            "95.0": {"bid": 0.0, "ask": 0.0, "iv": 0.0},
            "100.0": {"bid": 0.0, "ask": 0.0, "iv": 0.0},
            "105.0": {"bid": 1.0, "ask": 1.2, "iv": 0.3},
        }
        assert gate_bucket(bucket) is True


# ===========================================================================
# merge_sources — Phase 1 (T6 TV partial overlay, Yahoo all-zero bucket,
# bid-less-with-ask, malformed expiration)
# ===========================================================================

class TestMergeSourcesPartialOverlay:
    def test_tv_omitted_fields_do_not_clobber_yfinance(self):
        """Direct regression test for G2: TV's updated normalizer never
        emits volume/openInterest/lastTradeDate/inTheMoney/contractSymbol,
        so those yfinance-observed values must survive the overlay."""
        yf = _chain(calls={"20260101": _bucket(_yf_contract(
            volume=500, openInterest=1200, inTheMoney=True,
            lastTradeDate="2026-01-05T14:30:00Z", contractSymbol=_OCC_SYMBOL,
        ))})
        tv = _chain(calls={"20260101": _bucket(_tv_contract(bid=1.05, ask=1.15, iv=0.31))})

        merged = merge_sources(yf, tv)
        contract = merged["calls"]["20260101"]["100.0"]

        # Quote group: TradingView wins (fresher, still valid).
        assert contract["bid"] == 1.05
        assert contract["ask"] == 1.15
        assert contract["iv"] == 0.31
        # Everything TV can't supply: yfinance stands untouched.
        assert contract["volume"] == 500
        assert contract["openInterest"] == 1200
        assert contract["inTheMoney"] is True
        assert contract["lastTradeDate"] == "2026-01-05T14:30:00Z"
        assert contract["contractSymbol"] == _OCC_SYMBOL

    def test_tv_adds_strike_yfinance_is_missing(self):
        yf = _chain(calls={})
        tv = _chain(calls={"20260101": _bucket(_tv_contract(strike=110.0))})
        merged = merge_sources(yf, tv)
        assert "110.0" in merged["calls"]["20260101"]

    def test_contract_symbol_never_downgraded_from_occ(self):
        yf = _chain(calls={"20260101": _bucket(_yf_contract(contractSymbol=_OCC_SYMBOL))})
        tv = _chain(calls={"20260101": _bucket(_tv_contract(contractSymbol="garbage-not-occ"))})
        merged = merge_sources(yf, tv)
        assert merged["calls"]["20260101"]["100.0"]["contractSymbol"] == _OCC_SYMBOL


class TestMergeSourcesTVSingleQuoteFieldOnly:
    """Basher review edge cases: TradingView supplying only one field of
    the quote group (e.g. a partial scanner response, or bid/ask genuinely
    withheld by the venue for a no-quote strike). The gate must still pass
    on that single field, but per-field merge means only the field TV
    actually supplied should override yfinance's own value."""

    def test_tv_supplies_only_iv_no_bid_or_ask(self):
        yf = _chain(calls={"20260101": _bucket(_yf_contract(bid=1.0, ask=1.2, iv=0.30))})
        tv = _chain(calls={"20260101": _bucket(
            _tv_contract(bid=None, ask=None, iv=0.45),
        )})
        merged = merge_sources(yf, tv)
        contract = merged["calls"]["20260101"]["100.0"]
        # Gate passes on iv alone (>0) -> TV's quote group is trusted, but
        # it only actually supplied iv; bid/ask were never accepted from
        # TV (None fails is_accepted), so yfinance's values stand.
        assert contract["iv"] == 0.45
        assert contract["bid"] == 1.0
        assert contract["ask"] == 1.2

    def test_tv_supplies_only_ask_no_bid_or_iv(self):
        yf = _chain(calls={"20260101": _bucket(_yf_contract(bid=1.0, ask=1.2, iv=0.30))})
        tv = _chain(calls={"20260101": _bucket(
            _tv_contract(bid=None, ask=1.35, iv=None),
        )})
        merged = merge_sources(yf, tv)
        contract = merged["calls"]["20260101"]["100.0"]
        # Gate passes on ask alone (>0) -> TV's ask overrides; bid/iv were
        # never supplied by TV so yfinance's values stand.
        assert contract["ask"] == 1.35
        assert contract["bid"] == 1.0
        assert contract["iv"] == 0.30

    def test_tv_only_iv_with_no_yfinance_counterpart_still_produces_a_trusted_contract(self):
        tv = _chain(calls={"20260101": _bucket(_tv_contract(bid=None, ask=None, iv=0.5))})
        merged = merge_sources(_chain(), tv)
        contract = merged["calls"]["20260101"]["100.0"]
        assert contract["iv"] == 0.5
        assert "bid" not in contract
        assert "ask" not in contract


class TestMergeSourcesYahooAllZeroBucket:
    """Explicit task scenario: a whole Yahoo bucket returns degenerate
    zeros (market closed / feed failure) — the quote group must be
    discarded for the whole bucket, but independent observations (volume/
    openInterest) still pass through."""

    def _degenerate_yf_chain(self):
        return _chain(calls={"20260101": _bucket(
            _yf_contract(strike=95.0, bid=0.0, ask=0.0, iv=0.0, lastPrice=0.0, volume=12, openInterest=88),
            _yf_contract(strike=100.0, bid=0.0, ask=0.0, iv=0.0, lastPrice=0.0, volume=0, openInterest=150),
            _yf_contract(strike=105.0, bid=0.0, ask=0.0, iv=0.0, lastPrice=0.0, volume=5, openInterest=40),
        )})

    def test_quote_group_dropped_for_every_contract_in_bucket(self):
        merged = merge_sources(self._degenerate_yf_chain(), _chain())
        for strike_key in ("95.0", "100.0", "105.0"):
            contract = merged["calls"]["20260101"][strike_key]
            for field in ("bid", "ask", "iv", "lastPrice"):
                assert field not in contract

    def test_volume_and_open_interest_still_pass_through(self):
        merged = merge_sources(self._degenerate_yf_chain(), _chain())
        assert merged["calls"]["20260101"]["95.0"]["volume"] == 12
        assert merged["calls"]["20260101"]["100.0"]["volume"] == 0
        assert merged["calls"]["20260101"]["105.0"]["openInterest"] == 40

    def test_prior_quote_group_survives_the_degenerate_cycle(self):
        """The full pipeline: prior had good quotes, this cycle's Yahoo
        fetch is an all-zero bucket -> merge_prior must carry the prior
        quote group forward untouched."""
        prior = merge_prior({}, merge_sources(
            _chain(calls={"20260101": _bucket(_yf_contract())}), _chain(),
        ), now=NOW)

        live = merge_sources(self._degenerate_yf_chain(), _chain())
        accumulated = merge_prior(prior, live, now=NOW + timedelta(hours=1))

        contract = accumulated["calls"]["20260101"]["100.0"]
        assert contract["bid"] == 1.0
        assert contract["ask"] == 1.2
        assert contract["iv"] == 0.30
        # quote_asof must NOT advance — nothing trustworthy arrived this cycle.
        assert contract["_meta"]["quote_asof"] == prior["calls"]["20260101"]["100.0"]["_meta"]["quote_asof"]


class TestMergeSourcesBidlessWithPositiveAsk:
    """Explicit task scenario: a real bid-less contract (bid=0) with a
    genuinely positive ask must survive the merge and drive a conservative
    (not naive-average) mid, per options_math.robust_mid."""

    def test_bid_zero_ask_positive_survives_merge_and_mid(self):
        yf = _chain(calls={"20260101": _bucket(_yf_contract(bid=0.0, ask=4.25, iv=0.40))})
        merged = merge_sources(yf, _chain())
        contract = merged["calls"]["20260101"]["100.0"]
        assert contract["bid"] == 0.0
        assert contract["ask"] == 4.25

        derived = recompute_derived(merged, underlying_price=100.0, now=NOW)
        result_contract = derived["calls"]["20260101"]["100.0"]
        assert result_contract["mid"] == robust_mid(0.0, 4.25, 0.0)
        assert result_contract["mid"] == pytest.approx(0.10)


class TestMergeSourcesMalformedExpiration:
    """T10 + G5 regression: an unparseable expiration key must never be
    stored, even if it somehow reaches merge_sources directly."""

    @pytest.mark.parametrize("bad_key", ["2026-08-21", "not-a-date", "202608211", "2026082", ""])
    def test_malformed_expiration_key_rejected(self, bad_key):
        tv = _chain(calls={bad_key: _bucket(_tv_contract())})
        merged = merge_sources(_chain(), tv)
        assert bad_key not in merged["calls"]
        assert merged["calls"] == {}

    def test_impossible_calendar_date_rejected(self):
        # 8 digits, but not a real date (month 13).
        tv = _chain(calls={"20261301": _bucket(_tv_contract(expiration="20261301"))})
        merged = merge_sources(_chain(), tv)
        assert "20261301" not in merged["calls"]

    def test_valid_expiration_key_kept(self):
        tv = _chain(calls={"20260101": _bucket(_tv_contract())})
        merged = merge_sources(_chain(), tv)
        assert "20260101" in merged["calls"]

    @pytest.mark.parametrize("bad_key", [
        "20261301",  # month 13
        "20260230",  # Feb 30 never exists
        "20260231",  # Feb 31 never exists
        "20260132",  # day 32
        "20260100",  # day 00
        "20260001",  # month 00
        "20250229",  # Feb 29 in a non-leap year (2025)
    ])
    def test_calendar_invalid_yyyymmdd_rejected(self, bad_key):
        """Basher review: Rule S3 must reject every calendar-invalid
        YYYYMMDD, not just the month-13 case already covered above --
        including impossible days-of-month and non-leap-year Feb 29."""
        tv = _chain(calls={bad_key: _bucket(_tv_contract(expiration=bad_key))})
        merged = merge_sources(_chain(), tv)
        assert merged["calls"] == {}

    def test_leap_year_feb_29_is_a_valid_expiration(self):
        """2024 is a leap year -- Feb 29 is a real calendar date and must
        NOT be rejected merely for being an unusual one."""
        tv = _chain(calls={"20240229": _bucket(_tv_contract(expiration="20240229"))})
        merged = merge_sources(_chain(), tv)
        assert "20240229" in merged["calls"]


# ===========================================================================
# merge_prior — Phase 2 (T7 observed-zero overwrite, T8 iv/greek consistency,
# stale prior fill)
# ===========================================================================

class TestMergePriorObservedZeroOverwrite:
    def test_yfinance_observed_volume_zero_overwrites_prior_500(self):
        """T7: a genuinely observed zero (quiet trading day) legitimately
        overwrites a prior non-zero value — unlike the quote group, volume
        is never gated."""
        prior = merge_prior({}, merge_sources(
            _chain(calls={"20260101": _bucket(_yf_contract(volume=500))}), _chain(),
        ), now=NOW)
        live = merge_sources(_chain(calls={"20260101": _bucket(_yf_contract(volume=0))}), _chain())
        accumulated = merge_prior(prior, live, now=NOW + timedelta(minutes=30))
        assert accumulated["calls"]["20260101"]["100.0"]["volume"] == 0

    def test_z_m4_live_bid_zero_passing_trust_gate_overwrites_and_is_stored_as_zero(self):
        """Z-M4 (provenance regression guard): a live bid=0.0 that passes
        the source's trust gate (bid is per-field-zero-valid) overwrites a
        non-zero prior and is faithfully stored as 0.0 in the raw layer —
        never coerced/nulled at the raw merge layer."""
        prior = merge_prior({}, merge_sources(
            _chain(calls={"20260101": _bucket(_yf_contract(bid=3.0, ask=3.2))}), _chain(),
        ), now=NOW)
        live = merge_sources(
            _chain(calls={"20260101": _bucket(_yf_contract(bid=0.0, ask=3.2))}), _chain(),
        )
        accumulated = merge_prior(prior, live, now=NOW + timedelta(minutes=30))
        contract = accumulated["calls"]["20260101"]["100.0"]
        assert contract["bid"] == 0.0
        assert isinstance(contract["bid"], float)


class TestMergePriorStaleFill:
    """Explicit task scenario: prior has valid data, this cycle supplies
    nothing usable for the contract -> prior fields are retained and
    provenance correctly communicates staleness (quote_asof does not
    advance)."""

    def test_missing_live_contract_carries_prior_verbatim(self):
        prior = merge_prior({}, merge_sources(
            _chain(calls={"20260101": _bucket(_yf_contract())}), _chain(),
        ), now=NOW)
        live = merge_sources(_chain(), _chain())  # nothing from any source this cycle

        accumulated = merge_prior(prior, live, now=NOW + timedelta(days=3))
        contract = accumulated["calls"]["20260101"]["100.0"]

        assert contract["bid"] == 1.0
        assert contract["ask"] == 1.2
        assert contract["iv"] == 0.30
        assert contract["_meta"]["carried"] is True
        assert contract["_meta"]["quote_asof"] == prior["calls"]["20260101"]["100.0"]["_meta"]["quote_asof"]

    def test_invalid_live_quote_group_falls_back_to_prior_field_by_field(self):
        prior_raw = _yf_contract(bid=1.0, ask=1.2, iv=0.30, volume=50)
        prior = merge_prior({}, merge_sources(_chain(calls={"20260101": _bucket(prior_raw)}), _chain()), now=NOW)

        # This cycle: Yahoo returns a degenerate quote group for the SAME
        # contract (still present, still listed -- just untrustworthy),
        # but a fresh, legitimately-zero volume.
        live_raw = _yf_contract(bid=0.0, ask=0.0, iv=0.0, lastPrice=0.0, volume=0)
        live = merge_sources(_chain(calls={"20260101": _bucket(live_raw)}), _chain())

        accumulated = merge_prior(prior, live, now=NOW + timedelta(hours=2))
        contract = accumulated["calls"]["20260101"]["100.0"]
        assert contract["bid"] == 1.0
        assert contract["ask"] == 1.2
        assert contract["iv"] == 0.30
        assert contract["volume"] == 0  # independent observation, still fresh
        assert contract["_meta"]["quote_asof"] == prior["calls"]["20260101"]["100.0"]["_meta"]["quote_asof"]


class TestMergePriorGreekConsistency:
    def test_merged_iv_drives_recomputed_greeks_not_stale_prior_greeks(self):
        """T8: greeks recomputed after a merge must reflect the merged
        (fresh) iv, never a frozen prior snapshot."""
        prior = merge_prior({}, merge_sources(
            _chain(calls={"20260601": _bucket(_yf_contract(expiration="20260601", iv=0.20))}), _chain(),
        ), now=NOW)
        prior = recompute_derived(prior, underlying_price=100.0, now=NOW)
        stale_delta = prior["calls"]["20260601"]["100.0"]["delta"]

        # Fresh cycle: TradingView supplies a materially different iv.
        live = merge_sources(
            _chain(calls={"20260601": _bucket(_yf_contract(expiration="20260601", bid=1.0, ask=1.2, iv=0.20))}),
            _chain(calls={"20260601": _bucket(_tv_contract(expiration="20260601", bid=1.0, ask=1.2, iv=0.55))}),
        )
        accumulated = merge_prior(prior, live, now=NOW + timedelta(hours=1))
        result = recompute_derived(accumulated, underlying_price=100.0, now=NOW + timedelta(hours=1))
        contract = result["calls"]["20260601"]["100.0"]

        assert contract["iv"] == 0.55
        greeks_calc = GreeksCalculator(risk_free_rate=0.045)
        T = max((datetime(2026, 6, 1, tzinfo=timezone.utc) - (NOW + timedelta(hours=1))).days / 365.0, 1e-10)
        expected = greeks_calc.compute("c", 100.0, 100.0, T, 0.55)
        assert contract["delta"] == pytest.approx(expected["delta"])
        assert contract["delta"] != pytest.approx(stale_delta)
        assert contract["_meta"]["greeks_valid"] is True


class TestCarriedForwardDecay:
    def test_theta_and_time_to_expiry_advance_across_a_simulated_day(self):
        """T9: a carried-forward contract (no source lists it this cycle)
        must still decay theta/DTE-consistent greeks on recompute — it is
        never a frozen snapshot."""
        day1 = NOW
        day2 = NOW + timedelta(days=1)

        prior = merge_prior({}, merge_sources(
            _chain(calls={"20260201": _bucket(_yf_contract(expiration="20260201", iv=0.30))}), _chain(),
        ), now=day1)
        day1_result = recompute_derived(prior, underlying_price=100.0, now=day1)
        day1_theta = day1_result["calls"]["20260201"]["100.0"]["theta"]

        # Day 2: no source lists the contract at all this cycle.
        carried = merge_prior(day1_result, merge_sources(_chain(), _chain()), now=day2)
        assert carried["calls"]["20260201"]["100.0"]["_meta"]["carried"] is True

        day2_result = recompute_derived(carried, underlying_price=100.0, now=day2)
        day2_theta = day2_result["calls"]["20260201"]["100.0"]["theta"]

        assert day2_theta != day1_theta
        assert day2_result["calls"]["20260201"]["100.0"]["iv"] == 0.30  # quote data preserved


class TestCarriedForwardDownstreamConsumption:
    """Basher review: a carried-forward contract (no source lists it this
    cycle) must still produce derived fields (delta, executable buyback
    ask) that real downstream consumers (roll-candidate scoring, position
    filters) can safely use — not stale/frozen or nonsensical values."""

    def test_carried_forward_contract_delta_and_buyback_ask_stay_sane_across_a_day(self):
        day1 = NOW
        day2 = NOW + timedelta(days=1)

        prior = merge_prior({}, merge_sources(
            _chain(calls={"20260201": _bucket(_yf_contract(
                expiration="20260201", strike=100.0, bid=1.00, ask=1.20, iv=0.30,
            ))}), _chain(),
        ), now=day1)
        day1_result = recompute_derived(prior, underlying_price=100.0, now=day1)
        contract_day1 = day1_result["calls"]["20260201"]["100.0"]

        # A real downstream consumer can already act on day 1's derived fields.
        assert 0.0 < contract_day1["delta"] < 1.0
        assert executable_buyback_ask(contract_day1["ask"]) == pytest.approx(1.20)

        # Day 2: no source lists the contract this cycle -- carried forward.
        carried = merge_prior(day1_result, merge_sources(_chain(), _chain()), now=day2)
        assert carried["calls"]["20260201"]["100.0"]["_meta"]["carried"] is True
        day2_result = recompute_derived(carried, underlying_price=100.0, now=day2)
        contract_day2 = day2_result["calls"]["20260201"]["100.0"]

        # Derived fields remain well-formed (finite, sane range) on the
        # carried-forward contract -- a roll-candidate scorer or position
        # filter consuming this contract sees a valid, DTE-advanced delta,
        # not a stale/frozen snapshot, NaN, or None.
        assert math.isfinite(contract_day2["delta"])
        assert 0.0 < contract_day2["delta"] < 1.0
        # bid/ask are carried verbatim (no source updated them), so the
        # executable buyback ask is unchanged and still usable downstream.
        assert contract_day2["ask"] == contract_day1["ask"] == 1.20
        assert executable_buyback_ask(contract_day2["ask"]) == pytest.approx(1.20)

    def test_carried_forward_contract_with_no_ask_yields_no_executable_buyback(self):
        """A carried-forward contract that never had a valid ask (only a
        bid) must correctly report no executable buyback, not fabricate
        one from a stale/derived field."""
        prior = merge_prior({}, merge_sources(
            _chain(calls={"20260201": _bucket(_yf_contract(
                expiration="20260201", strike=100.0, bid=0.50, ask=0.0, iv=0.30,
            ))}), _chain(),
        ), now=NOW)
        result = recompute_derived(prior, underlying_price=100.0, now=NOW)
        contract = result["calls"]["20260201"]["100.0"]
        assert "ask" not in contract  # never accepted (0.0 fails is_accepted)
        assert executable_buyback_ask(contract.get("ask")) is None


# ===========================================================================
# recompute_derived — Phase 3
# ===========================================================================

class TestRecomputeDerived:
    def test_greeks_valid_false_when_iv_missing(self):
        chain = _chain(calls={"20260101": _bucket(_yf_contract(iv=0.0))})
        result = recompute_derived(chain, underlying_price=100.0, now=NOW)
        contract = result["calls"]["20260101"]["100.0"]
        assert contract["_meta"]["greeks_valid"] is False

    def test_greeks_valid_true_when_iv_present(self):
        chain = _chain(calls={"20260601": _bucket(_yf_contract(expiration="20260601", iv=0.25))})
        result = recompute_derived(chain, underlying_price=100.0, now=NOW)
        contract = result["calls"]["20260601"]["100.0"]
        assert contract["_meta"]["greeks_valid"] is True
        assert contract["delta"] > 0

    def test_derived_fields_never_read_from_source(self):
        """Even if a stale/garbage `delta` is sitting on the input
        contract, recompute_derived must always overwrite it."""
        chain = _chain(calls={"20260601": _bucket(_yf_contract(expiration="20260601", iv=0.25, delta=999))})
        result = recompute_derived(chain, underlying_price=100.0, now=NOW)
        assert result["calls"]["20260601"]["100.0"]["delta"] != 999

    def test_z_m1_invalid_iv_nulls_all_five_greeks_and_greeks_asof(self):
        """Z-M1: invalid/missing iv -> delta/gamma/theta/vega/rho are all
        None, greeks_valid is False, and greeks_asof is None. No 0.0
        anywhere."""
        chain = _chain(calls={"20260101": _bucket(_yf_contract(iv=0.0))})
        result = recompute_derived(chain, underlying_price=100.0, now=NOW)
        contract = result["calls"]["20260101"]["100.0"]
        for field in ("delta", "gamma", "theta", "vega", "rho"):
            assert contract[field] is None, f"{field} should be None, got {contract[field]!r}"
        assert contract["_meta"]["greeks_valid"] is False
        assert contract["_meta"]["greeks_asof"] is None

    def test_z_m2_no_usable_bid_or_ask_mid_is_none_not_zero(self):
        """Z-M2: no usable bid and no usable ask -> mid is None, never
        0.0."""
        chain = _chain(calls={"20260101": _bucket(_yf_contract(bid=0.0, ask=0.0))})
        result = recompute_derived(chain, underlying_price=100.0, now=NOW)
        contract = result["calls"]["20260101"]["100.0"]
        assert contract["mid"] is None

    def test_z_m3_happy_path_regression_unchanged_numeric_greeks(self):
        """Z-M3: valid iv + valid price/strike -> unchanged numeric
        Greeks/mid (no behavior change on the happy path)."""
        chain = _chain(calls={"20260601": _bucket(_yf_contract(
            expiration="20260601", bid=2.0, ask=2.2, iv=0.28,
        ))})
        result = recompute_derived(chain, underlying_price=100.0, now=NOW)
        contract = result["calls"]["20260601"]["100.0"]
        assert contract["mid"] == pytest.approx(robust_mid(2.0, 2.2))
        assert contract["_meta"]["greeks_valid"] is True
        assert contract["_meta"]["greeks_asof"] == _iso(NOW)
        for field in ("delta", "gamma", "theta", "vega", "rho"):
            assert isinstance(contract[field], float)
            assert math.isfinite(contract[field])


# ===========================================================================
# prune_by_expiration — T11
# ===========================================================================

class TestPruneByExpiration:
    def test_past_expiration_dropped_today_and_future_kept(self):
        today = date(2026, 3, 15)
        yesterday_key = "20260314"
        today_key = "20260315"
        tomorrow_key = "20260316"
        chain = _chain(calls={
            yesterday_key: _bucket(_yf_contract(expiration=yesterday_key)),
            today_key: _bucket(_yf_contract(expiration=today_key)),
            tomorrow_key: _bucket(_yf_contract(expiration=tomorrow_key)),
        })
        pruned = prune_by_expiration(chain, today_et=today)
        assert yesterday_key not in pruned["calls"]
        assert today_key in pruned["calls"]
        assert tomorrow_key in pruned["calls"]

    def test_prune_never_driven_by_ttl_or_staleness(self):
        """prune_by_expiration has no concept of TTL/staleness at all — a
        far-future expiration is kept regardless of how "stale" its quote
        provenance looks."""
        today = date(2026, 3, 15)
        future_key = "20261231"
        contract = _yf_contract(expiration=future_key)
        contract["_meta"] = {"quote_asof": "2020-01-01T00:00:00Z", "carried": True}
        chain = _chain(calls={future_key: _bucket(contract)})
        pruned = prune_by_expiration(chain, today_et=today)
        assert future_key in pruned["calls"]


# ===========================================================================
# No input mutation
# ===========================================================================

class TestNoInputMutation:
    def test_merge_sources_does_not_mutate_inputs(self):
        yf = _chain(calls={"20260101": _bucket(_yf_contract())})
        tv = _chain(calls={"20260101": _bucket(_tv_contract())})
        yf_before, tv_before = copy.deepcopy(yf), copy.deepcopy(tv)
        merge_sources(yf, tv)
        assert yf == yf_before
        assert tv == tv_before

    def test_merge_prior_does_not_mutate_inputs(self):
        prior = merge_prior({}, merge_sources(
            _chain(calls={"20260101": _bucket(_yf_contract())}), _chain(),
        ), now=NOW)
        live = merge_sources(_chain(calls={"20260101": _bucket(_yf_contract(bid=2.0, ask=2.2))}), _chain())
        prior_before, live_before = copy.deepcopy(prior), copy.deepcopy(live)
        merge_prior(prior, live, now=NOW + timedelta(hours=1))
        assert prior == prior_before
        assert live == live_before

    def test_recompute_derived_does_not_mutate_input(self):
        chain = _chain(calls={"20260601": _bucket(_yf_contract(expiration="20260601"))})
        chain_before = copy.deepcopy(chain)
        recompute_derived(chain, underlying_price=100.0, now=NOW)
        assert chain == chain_before

    def test_prune_by_expiration_does_not_mutate_input(self):
        chain = _chain(calls={"20200101": _bucket(_yf_contract(expiration="20200101"))})
        chain_before = copy.deepcopy(chain)
        prune_by_expiration(chain, today_et=date(2026, 1, 1))
        assert chain == chain_before


# ===========================================================================
# T12 — monotonicity property
# ===========================================================================

class TestMonotonicityProperty:
    def test_sequential_merge_equals_single_combined_merge(self):
        """merge(merge(P, L1), L2) == merge(P, merge(L1, L2)) over the
        accepted-field sets (provenance/_meta timestamps excluded) — this
        is what makes the ETag CAS-retry safe."""
        P = _chain(calls={"20260101": _bucket(_yf_contract(
            strike=100.0, bid=1.0, ask=1.1, iv=0.20, volume=10, openInterest=100,
        ))})
        prior = merge_prior({}, merge_sources(P, _chain()), now=NOW)

        L1 = _chain(calls={"20260101": _bucket(
            _yf_contract(strike=100.0, bid=1.05, ask=1.15, iv=0.22, volume=12, openInterest=100),
            _yf_contract(strike=105.0, bid=0.50, ask=0.60, iv=0.28, volume=3, openInterest=20),
        )})
        L1_live = merge_sources(L1, _chain())

        L2 = _chain(calls={"20260101": _bucket(
            _tv_contract(strike=100.0, bid=None, ask=None, iv=0.25),
            _tv_contract(strike=110.0, bid=0.20, ask=0.30, iv=0.35),
        )})
        L2_live = merge_sources(_chain(), L2)

        t1 = NOW + timedelta(minutes=10)
        t2 = NOW + timedelta(minutes=20)

        sequential = merge_prior(merge_prior(prior, L1_live, now=t1), L2_live, now=t2)
        combined_live = merge_prior(L1_live, L2_live, now=t2)
        single = merge_prior(prior, combined_live, now=t2)

        assert _strip_meta(sequential) == _strip_meta(single)

    @staticmethod
    def _random_field_value(rng, field):
        """A plausible fuzz value for one *raw source* field: sometimes
        absent (None), sometimes an invalid observation (should be ignored
        by is_accepted), sometimes a genuinely valid observation."""
        roll = rng.random()
        if field == "ask":
            if roll < 0.30:
                return None
            if roll < 0.40:
                return 0.0  # invalid: ask must be > 0
            return round(rng.uniform(0.01, 25.0), 2)
        if field == "iv":
            if roll < 0.30:
                return None
            if roll < 0.40:
                return rng.choice([0.0, 5.0, -0.2])  # invalid boundary/negative
            return round(rng.uniform(0.001, 4.99), 4)
        if field in ("bid", "lastPrice"):
            if roll < 0.30:
                return None
            if roll < 0.35:
                return -1.0  # invalid: negative
            return round(rng.uniform(0.0, 25.0), 2)
        if field in ("volume", "openInterest"):
            if roll < 0.30:
                return None
            return rng.randint(0, 5000)
        if field == "lastTradeDate":
            if roll < 0.40:
                return None
            day_offset = rng.randint(0, 30)
            return (NOW - timedelta(days=day_offset)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return None

    @classmethod
    def _random_raw_source_contract(cls, rng, strike, exp_key):
        """A randomized *raw, single-source* contract (as a real yfinance
        or TradingView payload would look before any gating/merging) --
        independently random per field, including invalid/missing values."""
        contract = {"strike": strike, "expiration": exp_key, "option_type": "call"}
        for field in ("bid", "ask", "iv", "lastPrice", "volume", "openInterest", "lastTradeDate"):
            value = cls._random_field_value(rng, field)
            if value is not None:
                contract[field] = value
        return contract

    @classmethod
    def _random_raw_source_chain(cls, rng, exp_key="20260101", n_strikes=4):
        bucket = {}
        for i in range(n_strikes):
            strike = 90.0 + i * 5.0
            bucket[f"{strike:.1f}"] = cls._random_raw_source_contract(rng, strike, exp_key)
        return _chain(calls={exp_key: bucket})

    @classmethod
    def _random_live_cycle(cls, rng, exp_key="20260101"):
        """A realistic *live* payload the way the real pipeline produces
        one: `merge_sources(yf_chain, tv_chain)` on two independently
        randomized raw source chains. This is important, not cosmetic --
        `merge_sources` is precisely what enforces the invariant that a
        contract's quote-group fields are only ever present because *some*
        source's own `gate_contract` passed for that same contract. Feeding
        `merge_prior` a raw, directly-fabricated dict (fields sampled fully
        independently, with no source ever having actually vetted them)
        can violate that invariant and is not a payload the real system can
        ever produce -- see the associativity note on the test below."""
        yf = cls._random_raw_source_chain(rng, exp_key)
        tv = cls._random_raw_source_chain(rng, exp_key)
        return merge_sources(yf, tv)

    @pytest.mark.parametrize("seed", range(300))
    def test_merge_prior_is_monotone_under_random_field_combinations(self, seed):
        """Property/fuzz test (Basher review): for many random (P, L1, L2)
        triples -- each live cycle built the *realistic* way via
        `merge_sources` on independently randomized yfinance/TradingView
        payloads, covering the full mix of absent / invalid / valid values
        across the quote group and observed fields --
        merge_prior(merge_prior(P, L1), L2) must equal
        merge_prior(P, merge_prior(L1, L2)) on observable field values.
        This is the associativity that makes the ETag CAS-retry (re-read
        prior, re-merge the *same* live payload) safe regardless of how
        many concurrent writers or retries interleave.

        Note: this property holds for realistic, self-consistent live
        payloads (anything `merge_sources` can actually produce). It is
        NOT guaranteed for an arbitrary, directly-fabricated dict whose
        quote-group fields were never actually vetted by any source's own
        gate -- that is not a shape the real pipeline ever produces, since
        `merge_prior`'s "prior" side is by design never re-gated (a prior
        is assumed already-vetted history, precisely so carried-forward
        contracts don't need to keep re-proving themselves every cycle).
        """
        rng = random.Random(seed)
        exp_key = "20260101"

        P0 = self._random_live_cycle(rng, exp_key)
        prior = merge_prior({}, P0, now=NOW)

        L1 = self._random_live_cycle(rng, exp_key)
        L2 = self._random_live_cycle(rng, exp_key)

        t1 = NOW + timedelta(minutes=10)
        t2 = NOW + timedelta(minutes=20)

        sequential = merge_prior(merge_prior(prior, L1, now=t1), L2, now=t2)
        combined_live = merge_prior(L1, L2, now=t2)
        single = merge_prior(prior, combined_live, now=t2)

        assert _strip_meta(sequential) == _strip_meta(single), (
            f"monotonicity violated for seed={seed}"
        )

    def test_merge_prior_only_adds_contracts_never_removes(self):
        """Monotonicity's other half: the accumulated contract set can only
        grow across cycles, even when a later cycle's live payload omits
        contracts the prior cycle had."""
        prior = merge_prior({}, _chain(calls={"20260101": _bucket(
            _yf_contract(strike=95.0), _yf_contract(strike=100.0), _yf_contract(strike=105.0),
        )}), now=NOW)
        # This cycle's live payload only lists one of the three strikes.
        live = _chain(calls={"20260101": _bucket(_yf_contract(strike=100.0))})
        accumulated = merge_prior(prior, live, now=NOW + timedelta(hours=1))
        assert set(accumulated["calls"]["20260101"].keys()) >= {"95.0", "100.0", "105.0"}


# ===========================================================================
# External schema compatibility
# ===========================================================================

class TestSchemaCompatibility:
    def test_full_pipeline_preserves_all_legacy_fields_plus_meta(self):
        yf = _chain(calls={"20260601": _bucket(_yf_contract(expiration="20260601"))})
        tv = _chain(calls={"20260601": _bucket(_tv_contract(expiration="20260601"))})
        live = merge_sources(yf, tv)
        accumulated = merge_prior({}, live, now=NOW)
        final = recompute_derived(accumulated, underlying_price=100.0, now=NOW)
        final = prune_by_expiration(final, today_et=date(2026, 1, 1))

        contract = final["calls"]["20260601"]["100.0"]
        legacy_fields = {
            "contractSymbol", "strike", "bid", "ask", "mid", "iv", "delta",
            "gamma", "theta", "vega", "rho", "volume", "openInterest",
            "lastPrice", "lastTradeDate", "inTheMoney", "expiration", "option_type",
        }
        assert legacy_fields.issubset(contract.keys())
        assert "_meta" in contract
        assert "quote_asof" in contract["_meta"]
