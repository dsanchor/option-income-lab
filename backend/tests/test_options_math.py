"""Tests for options_math module."""

import pytest
from src.options_math import executable_buyback_ask, robust_mid, robust_mid_optional


class TestExecutableBuybackAsk:
    @pytest.mark.parametrize(
        "ask",
        [
            None,
            0,
            -0.01,
            float("nan"),
            float("inf"),
            "1.20",
            True,
        ],
    )
    def test_rejects_non_numeric_non_finite_or_non_positive_asks(self, ask):
        assert executable_buyback_ask(ask) is None

    @pytest.mark.parametrize("ask", [0.01, 1, 1.20])
    def test_accepts_positive_finite_numeric_asks(self, ask):
        assert executable_buyback_ask(ask) == pytest.approx(float(ask))


class TestRobustMid:
    """Test robust_mid function with various market data scenarios."""

    def test_sane_two_sided_quote(self):
        """Normal two-sided quote -> simple midpoint."""
        # Normal tight market
        assert robust_mid(1.0, 1.1) == 1.05
        # Another sane spread
        assert robust_mid(2.5, 2.7) == 2.6

    def test_garbage_one_sided_quote_no_bid(self):
        """No bid (bid=0) with high ask -> capped near zero, NOT ask/2."""
        # The bug case: bid=0, ask=3.9 should NOT return 1.95
        result = robust_mid(0, 3.9)
        assert result <= 0.10
        assert result > 0  # should be positive but very small
        # Hard cap at 0.10 for stale-high asks
        assert result == 0.10

    def test_reasonable_no_bid_quote(self):
        """No bid but small ask -> capped at min(ask, 0.10)."""
        # Small ask below cap
        assert robust_mid(0, 0.05) == 0.05
        assert robust_mid(0, 0.08) == 0.08
        # Larger ask hits cap
        assert robust_mid(0, 0.25) == 0.10
        assert robust_mid(0, 1.5) == 0.10

    def test_normal_spread(self):
        """Normal liquid option spread -> midpoint."""
        # The ADM example from live data (bid=0.05, ask=0.25)
        assert robust_mid(0.05, 0.25) == 0.15

    def test_implausibly_wide_spread(self):
        """Real bid but stale/garbage-wide ask -> anchor to bid."""
        # bid=0.05, ask=3.9 is implausibly wide (> 8*bid + 0.20)
        assert robust_mid(0.05, 3.9) == 0.05
        # Another wide example
        assert robust_mid(0.10, 5.0) == 0.10

    def test_moderately_wide_but_acceptable_spread(self):
        """Wider spread but not implausibly so -> still use midpoint."""
        # bid=1.0, ask=5.0 -> spread is 4.0
        # 5.0 > 1.0 * 8 + 0.20 = 8.20? No, 5.0 < 8.20, so it's acceptable
        assert robust_mid(1.0, 5.0) == 3.0

    def test_both_zero(self):
        """No bid, no ask -> 0.0."""
        assert robust_mid(0, 0) == 0.0
        assert robust_mid(None, None) == 0.0

    def test_only_bid(self):
        """Only bid available, no ask -> use bid."""
        assert robust_mid(1.5, 0) == 1.5
        assert robust_mid(0.75, None) == 0.75

    def test_negative_and_none_handling(self):
        """Negative or None values treated as 0."""
        assert robust_mid(-1.0, 2.0) == 0.10  # negative bid -> 0, only ask
        assert robust_mid(1.0, -2.0) == 1.0   # negative ask -> 0, only bid
        assert robust_mid(None, 1.5) == 0.10  # None bid -> only ask, capped
        assert robust_mid(1.5, None) == 1.5   # None ask -> only bid

    def test_rounding(self):
        """Result is rounded to 4 decimals."""
        # Midpoint that needs rounding
        result = robust_mid(1.1111, 1.3333)
        assert result == 1.2222  # (1.1111 + 1.3333) / 2 = 1.2222

    def test_last_price_parameter_ignored(self):
        """Last price parameter is accepted but currently unused."""
        # Should produce same results regardless of last price
        assert robust_mid(1.0, 1.1, last=0.9) == 1.05
        assert robust_mid(1.0, 1.1, last=1.2) == 1.05
        assert robust_mid(0, 3.9, last=1.5) == 0.10


class TestRobustMidOptional:
    """robust_mid_optional (Z3, danny-zero-free-agent-option-chains.md):
    None instead of a fabricated 0.0 when neither side is usable; byte-
    identical to robust_mid on every path that has a real usable side."""

    def test_neither_bid_nor_ask_usable_returns_none(self):
        assert robust_mid_optional(0, 0) is None
        assert robust_mid_optional(None, None) is None
        assert robust_mid_optional(0, None) is None
        assert robust_mid_optional(None, 0) is None
        assert robust_mid_optional(-1.0, -2.0) is None

    def test_matches_robust_mid_when_bid_usable(self):
        assert robust_mid_optional(1.0, 1.1) == robust_mid(1.0, 1.1) == 1.05
        assert robust_mid_optional(1.5, 0) == robust_mid(1.5, 0) == 1.5

    def test_matches_robust_mid_when_only_ask_usable(self):
        assert robust_mid_optional(0, 3.9) == robust_mid(0, 3.9) == 0.10
        assert robust_mid_optional(None, 0.05) == robust_mid(None, 0.05) == 0.05

    def test_wide_spread_and_rounding_paths_unchanged(self):
        assert robust_mid_optional(0.05, 3.9) == robust_mid(0.05, 3.9) == 0.05
        assert robust_mid_optional(1.1111, 1.3333) == robust_mid(1.1111, 1.3333) == 1.2222

    def test_last_price_parameter_ignored_like_robust_mid(self):
        assert robust_mid_optional(1.0, 1.1, last=0.9) == 1.05
        assert robust_mid_optional(0, 0, last=5.0) is None
