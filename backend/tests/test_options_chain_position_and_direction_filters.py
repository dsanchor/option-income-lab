"""Direct unit tests for filter_options_chain_for_position and
filter_options_chain_by_roll_direction (src/options_chain_filters.py).

Coverage gap closed: prior to this file, neither function had a dedicated
unit test — both were only ever exercised indirectly through higher-level
pipeline tests. Basher's root-cause review of the "Debug Agent Chain
Pipeline" bug (MSFT $525 call / 2026-09-04) flagged this as a required
acceptance criterion alongside the format_roll_candidates_table and debug
route tests.
"""

from src.options_chain_filters import (
    filter_options_chain_by_roll_direction,
    filter_options_chain_for_position,
)


def _make_call_chain(strikes, expiration="20260904"):
    """Build a minimal calls-only chain with the given strike list at one
    expiration. Delta/bid/ask are irrelevant here — these two filters don't
    look at them.
    """
    return {
        "symbol": "MSFT",
        "timestamp": "2026-08-18T06:00:00Z",
        "calls": {
            expiration: {
                str(float(s)): {"strike": float(s), "bid": 1.0, "ask": 1.1}
                for s in strikes
            },
        },
        "puts": {},
    }


class TestFilterOptionsChainForPosition:
    def test_keeps_window_around_current_strike_per_expiration(self):
        strikes = [490 + i * 5 for i in range(20)]  # 490..585 step 5
        chain = _make_call_chain(strikes)

        result = filter_options_chain_for_position(chain, current_strike=525.0, num_strikes=2)

        kept = sorted(float(k) for k in result["calls"]["20260904"])
        # 525.0 is present; exactly 2 strikes on either side (515,520,525,530,535)
        assert kept == [515.0, 520.0, 525.0, 530.0, 535.0]

    def test_adds_current_position_metadata(self):
        chain = _make_call_chain([500, 505, 510])
        result = filter_options_chain_for_position(chain, current_strike=505.0, option_type="call")

        assert result["current_position"]["strike"] == 505.0
        assert result["current_position"]["strike_key"] == "505.0"
        assert result["current_position"]["option_type"] == "call"

    def test_option_type_optional_omits_key_when_not_given(self):
        chain = _make_call_chain([500, 505, 510])
        result = filter_options_chain_for_position(chain, current_strike=505.0)
        assert "option_type" not in result["current_position"]

    def test_zero_num_strikes_keeps_only_closest_strike(self):
        chain = _make_call_chain([500, 505, 510])
        result = filter_options_chain_for_position(chain, current_strike=505.0, num_strikes=0)
        kept = list(result["calls"]["20260904"].keys())
        assert kept == ["505.0"]

    def test_preserves_symbol_and_timestamp(self):
        chain = _make_call_chain([500, 505])
        result = filter_options_chain_for_position(chain, current_strike=500.0)
        assert result["symbol"] == "MSFT"
        assert result["timestamp"] == "2026-08-18T06:00:00Z"

    def test_windows_are_independent_per_expiration(self):
        """Each expiration bucket windows around its OWN closest strike to
        current_strike, so a far expiration with only distant strikes still
        keeps a window centered on its nearest match, not an empty result.
        """
        chain = {
            "symbol": "MSFT",
            "timestamp": "2026-08-18T06:00:00Z",
            "calls": {
                "20260904": {str(float(s)): {"strike": float(s)} for s in [520, 525, 530]},
                "20261016": {str(float(s)): {"strike": float(s)} for s in [700, 705, 710]},
            },
            "puts": {},
        }
        result = filter_options_chain_for_position(chain, current_strike=525.0, num_strikes=1)
        assert set(result["calls"]["20260904"].keys()) == {"520.0", "525.0", "530.0"}
        # 700 is closest of the far bucket's own strikes to 525 (still windowed, not dropped)
        assert set(result["calls"]["20261016"].keys()) == {"700.0", "705.0"}

    def test_empty_calls_bucket_returns_empty_result(self):
        chain = {"symbol": "MSFT", "timestamp": None, "calls": {}, "puts": {}}
        result = filter_options_chain_for_position(chain, current_strike=525.0)
        assert result["calls"] == {}


class TestFilterOptionsChainByRollDirection:
    def test_roll_down_keeps_strikes_strictly_below_and_same_or_later_expiration(self):
        chain = _make_call_chain([510, 520, 525, 530])
        result = filter_options_chain_by_roll_direction(
            chain, current_strike=525.0, current_expiration="2026-09-04",
            roll_type="ROLL_DOWN", option_type="call",
        )
        kept = sorted(float(k) for k in result["calls"]["20260904"])
        assert kept == [510.0, 520.0]

    def test_roll_up_keeps_strikes_strictly_above(self):
        chain = _make_call_chain([510, 520, 525, 530, 540])
        result = filter_options_chain_by_roll_direction(
            chain, current_strike=525.0, current_expiration="2026-09-04",
            roll_type="ROLL_UP", option_type="call",
        )
        kept = sorted(float(k) for k in result["calls"]["20260904"])
        assert kept == [530.0, 540.0]

    def test_roll_out_keeps_only_adjacent_strikes_at_strictly_later_expiration(self):
        chain = {
            "symbol": "MSFT",
            "timestamp": "2026-08-18T06:00:00Z",
            "calls": {
                # same expiration as current position — must be fully excluded
                # for ROLL_OUT regardless of strike, since it requires a
                # strictly LATER expiration.
                "20260904": {str(float(s)): {"strike": float(s)} for s in [520, 525, 530]},
                "20261016": {str(float(s)): {"strike": float(s)} for s in [510, 520, 525, 530, 540]},
            },
            "puts": {},
        }
        result = filter_options_chain_by_roll_direction(
            chain, current_strike=525.0, current_expiration="2026-09-04",
            roll_type="ROLL_OUT", option_type="call",
        )
        # Same-expiration bucket is dropped entirely (not strictly later).
        assert "20260904" not in result["calls"]
        # Later expiration keeps only the nearest strike and its immediate neighbors.
        kept = sorted(float(k) for k in result["calls"]["20261016"])
        assert kept == [520.0, 525.0, 530.0]

    def test_roll_out_excludes_identical_held_contract_even_when_only_candidate(self):
        """The exact strike+expiration of the held contract is never
        retained by ROLL_OUT — expiration must be strictly greater, by
        design (see 2026-07-09 'Preserve Buyback Cost Reference' decision:
        candidacy exclusion is intentional and orthogonal to buyback-cost
        preservation, which format_roll_candidates_table's current_contract
        parameter now handles separately).
        """
        chain = _make_call_chain([525], expiration="20260904")
        result = filter_options_chain_by_roll_direction(
            chain, current_strike=525.0, current_expiration="2026-09-04",
            roll_type="ROLL_OUT", option_type="call",
        )
        assert result["calls"] == {}

    def test_roll_up_and_out_keeps_strikes_at_or_above_and_strictly_later_expiration(self):
        chain = {
            "symbol": "MSFT",
            "timestamp": "2026-08-18T06:00:00Z",
            "calls": {
                "20260904": {str(float(s)): {"strike": float(s)} for s in [525, 530]},
                "20261016": {str(float(s)): {"strike": float(s)} for s in [520, 525, 530]},
            },
            "puts": {},
        }
        result = filter_options_chain_by_roll_direction(
            chain, current_strike=525.0, current_expiration="2026-09-04",
            roll_type="ROLL_UP_AND_OUT", option_type="call",
        )
        assert "20260904" not in result["calls"]
        kept = sorted(float(k) for k in result["calls"]["20261016"])
        assert kept == [525.0, 530.0]

    def test_roll_down_and_out_keeps_strikes_at_or_below_and_strictly_later_expiration(self):
        chain = {
            "symbol": "MSFT",
            "timestamp": "2026-08-18T06:00:00Z",
            "calls": {
                "20260904": {str(float(s)): {"strike": float(s)} for s in [520, 525]},
                "20261016": {str(float(s)): {"strike": float(s)} for s in [510, 520, 525, 530]},
            },
            "puts": {},
        }
        result = filter_options_chain_by_roll_direction(
            chain, current_strike=525.0, current_expiration="2026-09-04",
            roll_type="ROLL_DOWN_AND_OUT", option_type="call",
        )
        assert "20260904" not in result["calls"]
        kept = sorted(float(k) for k in result["calls"]["20261016"])
        assert kept == [510.0, 520.0, 525.0]

    def test_unknown_roll_type_returns_chain_unchanged(self):
        chain = _make_call_chain([510, 520, 525, 530])
        result = filter_options_chain_by_roll_direction(
            chain, current_strike=525.0, current_expiration="2026-09-04",
            roll_type="NOT_A_REAL_ROLL_TYPE", option_type="call",
        )
        assert result is chain

    def test_puts_bucket_untouched_when_filtering_calls(self):
        chain = _make_call_chain([510, 520, 525, 530])
        chain["puts"] = {"20260904": {"525.0": {"strike": 525.0}}}
        result = filter_options_chain_by_roll_direction(
            chain, current_strike=525.0, current_expiration="2026-09-04",
            roll_type="ROLL_DOWN", option_type="call",
        )
        assert result["puts"] == chain["puts"]

    def test_current_position_metadata_preserved(self):
        chain = _make_call_chain([510, 520, 525, 530])
        chain["current_position"] = {"strike": 525.0, "strike_key": "525.0"}
        result = filter_options_chain_by_roll_direction(
            chain, current_strike=525.0, current_expiration="2026-09-04",
            roll_type="ROLL_DOWN", option_type="call",
        )
        assert result["current_position"] == chain["current_position"]
