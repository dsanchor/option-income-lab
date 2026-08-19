"""Test suite for src/options_chain_view.py — the agent-facing / scoring
normalization boundary (see
.squad/decisions/inbox/danny-zero-free-agent-option-chains.md, rules
Z1-Z4/Z10).

Covers the required Z-V1 through Z-V6 test matrix (design §6.1) plus
direct coverage of the three frozen accessor functions
(usable_quote/usable_greek/is_candidate_eligible).

Hermetic: pure in-memory dict fixtures, no network calls.
"""

import copy
from datetime import datetime, timedelta, timezone

import pytest

from src.options_chain_view import (
    contract_view,
    is_candidate_eligible,
    to_agent_view,
    usable_greek,
    usable_quote,
)

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
_STALE_AFTER = 86400  # mirrors options_chain_cache.stale_quote_warn_seconds default


def _contract(**overrides):
    """A realistic fully-populated raw contract (post recompute_derived)."""
    base = {
        "contractSymbol": "TEST260101C00100000",
        "strike": 100.0,
        "bid": 1.0,
        "ask": 1.2,
        "mid": 1.1,
        "iv": 0.30,
        "delta": 0.45,
        "gamma": 0.02,
        "theta": -0.03,
        "vega": 0.10,
        "rho": 0.05,
        "lastPrice": 1.05,
        "lastTradeDate": "2026-01-01T15:00:00Z",
        "volume": 10,
        "openInterest": 100,
        "inTheMoney": False,
        "expiration": "20260101",
        "option_type": "call",
        "_meta": {
            "quote_asof": "2026-01-01T11:55:00Z",
            "greeks_valid": True,
            "greeks_asof": "2026-01-01T11:55:00Z",
        },
    }
    base.update(overrides)
    return base


def _chain(calls=None, puts=None, symbol="TEST"):
    return {
        "symbol": symbol,
        "timestamp": "2026-01-01T12:00:00Z",
        "calls": calls or {},
        "puts": puts or {},
    }


def _bucket(*contracts):
    return {f"{c['strike']:.1f}": c for c in contracts}


# ===========================================================================
# Z-V1: idempotence, purity, totality
# ===========================================================================

class TestZV1PurityIdempotenceTotality:
    def test_purity_input_not_mutated(self):
        chain = _chain(calls={"20260101": _bucket(_contract())})
        chain_before = copy.deepcopy(chain)
        to_agent_view(chain, now=NOW, stale_after_seconds=_STALE_AFTER)
        assert chain == chain_before

    def test_contract_view_purity_meta_not_mutated(self):
        contract = _contract()
        contract_before = copy.deepcopy(contract)
        contract_view(contract, now=NOW, stale_after_seconds=_STALE_AFTER)
        assert contract == contract_before

    def test_idempotence_second_pass_equals_first(self):
        chain = _chain(calls={"20260101": _bucket(_contract(bid=0.0))})
        first = to_agent_view(chain, now=NOW, stale_after_seconds=_STALE_AFTER)
        second = to_agent_view(first, now=NOW, stale_after_seconds=_STALE_AFTER)
        assert second == first

    def test_idempotence_preserves_no_market_vs_unavailable_distinction(self):
        """The idempotence-fix scenario: a genuine bid=0.0 must still read
        back as `no_market` (not `unavailable`) after a second pass, even
        though both were nulled to None by the first pass."""
        chain = _chain(calls={
            "20260101": _bucket(_contract(bid=0.0)),
        })
        first = to_agent_view(chain, now=NOW, stale_after_seconds=_STALE_AFTER)
        second = to_agent_view(first, now=NOW, stale_after_seconds=_STALE_AFTER)
        status = second["calls"]["20260101"]["100.0"]["_meta"]["field_status"]
        assert status["bid"] == "no_market"

    @pytest.mark.parametrize("bad_input", [None, {}, "not-a-chain", 42, []])
    def test_totality_to_agent_view_never_raises(self, bad_input):
        result = to_agent_view(bad_input, now=NOW, stale_after_seconds=_STALE_AFTER)
        assert isinstance(result, dict)
        assert result["calls"] == {}
        assert result["puts"] == {}

    @pytest.mark.parametrize("bad_input", [None, "not-a-contract", 42, [], {"malformed": object()}])
    def test_totality_contract_view_never_raises(self, bad_input):
        result = contract_view(bad_input, now=NOW, stale_after_seconds=_STALE_AFTER)
        assert isinstance(result, dict)

    def test_totality_malformed_bucket_shapes(self):
        chain = _chain(calls={"20260101": "not-a-bucket"}, puts={"20260201": None})
        result = to_agent_view(chain, now=NOW, stale_after_seconds=_STALE_AFTER)
        assert result["calls"]["20260101"] == {}
        assert result["puts"]["20260201"] == {}

    def test_shape_preserving_same_keys(self):
        chain = _chain(calls={"20260101": _bucket(_contract())})
        result = to_agent_view(chain, now=NOW, stale_after_seconds=_STALE_AFTER)
        assert set(result.keys()) == {"symbol", "timestamp", "calls", "puts"}
        assert "20260101" in result["calls"]
        assert "100.0" in result["calls"]["20260101"]


# ===========================================================================
# Z-V2: bid = 0.0 -> view bid None, field_status.bid == "no_market"
# ===========================================================================

class TestZV2GenuineZeroBid:
    def test_bid_zero_becomes_none_with_no_market_status(self):
        contract = _contract(bid=0.0)
        view = contract_view(contract, now=NOW, stale_after_seconds=_STALE_AFTER)
        assert view["bid"] is None
        assert view["_meta"]["field_status"]["bid"] == "no_market"

    def test_last_price_zero_becomes_none_with_no_trades_status(self):
        """Same ambiguity as bid applies to lastPrice (Z-F/Z10 wording)."""
        contract = _contract(lastPrice=0.0)
        view = contract_view(contract, now=NOW, stale_after_seconds=_STALE_AFTER)
        assert view["lastPrice"] is None
        assert view["_meta"]["field_status"]["lastPrice"] == "no_trades"


# ===========================================================================
# Z-V3: volume=0 / openInterest=0 kept as integer 0 (anti-over-correction)
# ===========================================================================

class TestZV3VolumeOpenInterestZeroPreserved:
    def test_volume_zero_kept_as_zero(self):
        contract = _contract(volume=0)
        view = contract_view(contract, now=NOW, stale_after_seconds=_STALE_AFTER)
        assert view["volume"] == 0
        assert view["volume"] is not None

    def test_open_interest_zero_kept_as_zero(self):
        contract = _contract(openInterest=0)
        view = contract_view(contract, now=NOW, stale_after_seconds=_STALE_AFTER)
        assert view["openInterest"] == 0
        assert view["openInterest"] is not None

    def test_both_zero_together_kept_as_zero_through_whole_chain(self):
        chain = _chain(calls={"20260101": _bucket(_contract(volume=0, openInterest=0))})
        result = to_agent_view(chain, now=NOW, stale_after_seconds=_STALE_AFTER)
        contract = result["calls"]["20260101"]["100.0"]
        assert contract["volume"] == 0
        assert contract["openInterest"] == 0


# ===========================================================================
# Z-V4: absent bid -> None, field_status.bid == "unavailable"
# ===========================================================================

class TestZV4AbsentBid:
    def test_absent_bid_is_none_with_unavailable_status(self):
        contract = _contract()
        del contract["bid"]
        view = contract_view(contract, now=NOW, stale_after_seconds=_STALE_AFTER)
        assert view["bid"] is None
        assert view["_meta"]["field_status"]["bid"] == "unavailable"

    def test_absent_distinct_from_no_market(self):
        """The two ambiguous-zero fields must produce different status
        labels for 'never observed' vs 'observed as exactly zero'."""
        present_zero = contract_view(_contract(bid=0.0), now=NOW, stale_after_seconds=_STALE_AFTER)
        absent = _contract()
        del absent["bid"]
        absent_view = contract_view(absent, now=NOW, stale_after_seconds=_STALE_AFTER)
        assert present_zero["_meta"]["field_status"]["bid"] == "no_market"
        assert absent_view["_meta"]["field_status"]["bid"] == "unavailable"
        assert present_zero["bid"] is absent_view["bid"] is None


# ===========================================================================
# Z-V5: staleness
# ===========================================================================

class TestZV5Staleness:
    def test_fresh_quote_asof_is_not_stale(self):
        contract = _contract()
        contract["_meta"]["quote_asof"] = (NOW - timedelta(seconds=10)).isoformat().replace("+00:00", "Z")
        view = contract_view(contract, now=NOW, stale_after_seconds=_STALE_AFTER)
        assert view["_meta"]["stale"] is False

    def test_quote_asof_older_than_threshold_is_stale(self):
        contract = _contract()
        contract["_meta"]["quote_asof"] = (NOW - timedelta(seconds=_STALE_AFTER + 60)).isoformat().replace("+00:00", "Z")
        view = contract_view(contract, now=NOW, stale_after_seconds=_STALE_AFTER)
        assert view["_meta"]["stale"] is True

    def test_missing_quote_asof_is_conservatively_stale(self):
        contract = _contract()
        contract["_meta"].pop("quote_asof", None)
        view = contract_view(contract, now=NOW, stale_after_seconds=_STALE_AFTER)
        assert view["_meta"]["stale"] is True

    def test_custom_stale_after_seconds_is_honoured(self):
        contract = _contract()
        contract["_meta"]["quote_asof"] = (NOW - timedelta(seconds=120)).isoformat().replace("+00:00", "Z")
        view_short = contract_view(contract, now=NOW, stale_after_seconds=60)
        view_long = contract_view(contract, now=NOW, stale_after_seconds=300)
        assert view_short["_meta"]["stale"] is True
        assert view_long["_meta"]["stale"] is False


# ===========================================================================
# Z-V6: legacy contract with numeric Greeks but greeks_valid == False
# ===========================================================================

class TestZV6LegacyGreeksValidFalse:
    def test_explicit_greeks_valid_false_nulls_numeric_greeks(self):
        """A legacy/malformed contract still carrying numeric Greek values
        alongside an explicit greeks_valid=False must have every Greek
        nulled in the view (Z4) — the flag is binding regardless of what
        numeric leftovers are present."""
        contract = _contract(delta=0.5, gamma=0.02, theta=-0.03, vega=0.1, rho=0.05)
        contract["_meta"]["greeks_valid"] = False
        view = contract_view(contract, now=NOW, stale_after_seconds=_STALE_AFTER)
        for greek in ("delta", "gamma", "theta", "vega", "rho"):
            assert view[greek] is None, f"{greek} should be None when greeks_valid is False"
        assert view["_meta"]["field_status"]["greeks"] == "unavailable"

    def test_missing_meta_entirely_still_trusts_raw_numeric_greeks(self):
        """A hand-built fixture that never modeled `_meta` at all is not
        the contamination Z4 targets -- its raw numeric Greek is trusted
        (absence of greeks_valid is not itself disqualifying)."""
        contract = {
            "strike": 100.0, "bid": 1.0, "ask": 1.2, "delta": 0.5,
            "expiration": "20260101", "option_type": "call",
        }
        assert usable_greek(contract, "delta") == 0.5


# ===========================================================================
# usable_quote / usable_greek — direct accessor coverage
# ===========================================================================

class TestUsableQuote:
    @pytest.mark.parametrize("field", ["bid", "ask", "lastPrice", "iv", "mid"])
    def test_zero_is_unusable(self, field):
        assert usable_quote({field: 0.0}, field) is None

    @pytest.mark.parametrize("field", ["bid", "ask", "lastPrice", "iv", "mid"])
    def test_absent_is_unusable(self, field):
        assert usable_quote({}, field) is None

    @pytest.mark.parametrize("field", ["bid", "ask", "lastPrice", "iv", "mid"])
    def test_positive_is_usable(self, field):
        assert usable_quote({field: 1.5}, field) == 1.5

    def test_negative_is_unusable(self):
        assert usable_quote({"bid": -1.0}, "bid") is None

    def test_nan_and_inf_are_unusable(self):
        assert usable_quote({"bid": float("nan")}, "bid") is None
        assert usable_quote({"bid": float("inf")}, "bid") is None

    def test_field_not_in_quote_group_returns_none(self):
        assert usable_quote({"volume": 10}, "volume") is None

    def test_totality_never_raises_on_malformed_input(self):
        assert usable_quote(None, "bid") is None
        assert usable_quote("not-a-dict", "bid") is None
        assert usable_quote({"bid": "garbage"}, "bid") is None


class TestUsableGreek:
    def test_zero_greek_is_usable_unlike_quotes(self):
        """Unlike bid/ask/iv, a Greek can legitimately be exactly 0.0 (a
        deep OTM delta) -- only greeks_valid=False or non-finite disqualifies it."""
        assert usable_greek({"delta": 0.0, "_meta": {"greeks_valid": True}}, "delta") == 0.0

    def test_greeks_valid_false_nulls_even_present_numeric_value(self):
        assert usable_greek({"delta": 0.5, "_meta": {"greeks_valid": False}}, "delta") is None

    def test_greeks_valid_absent_trusts_raw_value(self):
        assert usable_greek({"delta": 0.5}, "delta") == 0.5

    def test_non_finite_greek_is_unusable(self):
        assert usable_greek({"delta": float("nan"), "_meta": {"greeks_valid": True}}, "delta") is None

    def test_totality_never_raises_on_malformed_input(self):
        assert usable_greek(None, "delta") is None
        assert usable_greek({"delta": "garbage"}, "delta") is None


# ===========================================================================
# is_candidate_eligible — Z10
# ===========================================================================

class TestIsCandidateEligible:
    def test_usable_bid_positive_oi_valid_greeks_is_eligible(self):
        assert is_candidate_eligible(_contract()) is True

    def test_zero_bid_is_ineligible(self):
        assert is_candidate_eligible(_contract(bid=0.0)) is False

    def test_zero_open_interest_is_ineligible_by_default(self):
        assert is_candidate_eligible(_contract(openInterest=0)) is False

    def test_min_open_interest_threshold_is_configurable(self):
        contract = _contract(openInterest=5)
        assert is_candidate_eligible(contract, min_open_interest=1) is True
        assert is_candidate_eligible(contract, min_open_interest=10) is False

    def test_greeks_valid_false_is_ineligible(self):
        contract = _contract()
        contract["_meta"]["greeks_valid"] = False
        assert is_candidate_eligible(contract) is False

    def test_totality_never_raises_on_malformed_input(self):
        assert is_candidate_eligible(None) is False
        assert is_candidate_eligible("not-a-contract") is False
        assert is_candidate_eligible({}) is False
