"""Regression tests for format_roll_candidates_table's current-contract
reference handling (src/options_chain_filters.py).

Root cause covered: a held contract's own computed delta can legitimately
fall outside the standard candidate delta band (e.g. yfinance returns a
degenerate/near-zero implied volatility for a contract while the market is
closed, which Black-Scholes turns into a ~0.0 delta for a call that is
objectively not at-the-money-adjacent). filter_options_chain_by_delta drops
such a contract before the direction filter ever runs, so a caller who only
looks up the current contract inside the direction-filtered chain (the old
behavior) incorrectly reports "no chain data" / no executable buyback ask
for a contract that is actually present with a valid ask.

Shape mirrors the reported case exactly: MSFT $525 call, expiration
2026-09-04 (17 DTE as of 2026-08-18).
"""

import datetime
import re

from src.options_chain_filters import (
    filter_options_chain_by_delta,
    filter_options_chain_by_roll_direction,
    filter_options_chain_by_type,
    filter_options_chain_for_position,
    format_roll_candidates_table,
    get_contract,
)


def _msft_chain_with_degenerate_current_contract():
    """Raw (unfiltered) chain shaped like the live MSFT case: the held
    $525 call at 2026-09-04 has a valid non-zero ask but a ~0.0 delta
    (degenerate IV from a closed-market yfinance fetch), while a handful of
    healthy near-ATM candidates exist at a later expiration.
    """
    return {
        "symbol": "MSFT",
        "timestamp": "2026-08-18T06:00:00Z",
        "calls": {
            # Current position: valid ask, but delta rounds to 0.0 — outside
            # the standard (0.15, 0.90) call delta band.
            "20260904": {
                "525.0": {
                    "contractSymbol": "MSFT260904C00525000",
                    "strike": 525.0,
                    "bid": 0.0,
                    "ask": 3.20,
                    "mid": 1.6,
                    "iv": 0.062509,
                    "delta": 0.0,
                    "gamma": 0.0,
                    "theta": -0.0,
                    "vega": 0.0,
                    "rho": 0.0,
                    "volume": 195,
                    "openInterest": 0,
                    "lastPrice": 0.92,
                    "lastTradeDate": "2026-08-17T19:36:44Z",
                    "inTheMoney": False,
                    "expiration": "20260904",
                    "option_type": "call",
                },
            },
            # A later, in-band expiration with real candidates.
            "20261016": {
                "490.0": {
                    "strike": 490.0, "bid": 8.50, "ask": 8.80, "mid": 8.65,
                    "iv": 0.22, "delta": 0.45, "gamma": 0.01, "theta": -0.05,
                    "vega": 0.4, "rho": 0.1, "volume": 500, "openInterest": 1000,
                    "lastPrice": 8.60, "lastTradeDate": "2026-08-17T20:00:00Z",
                    "inTheMoney": False, "expiration": "20261016", "option_type": "call",
                },
            },
        },
        "puts": {},
    }


def _run_pipeline(chain, *, strike=525.0, expiration="2026-09-04",
                   option_type="call", roll_type="ROLL_OUT"):
    """Replicates the exact stage sequence used by the debug pipeline
    endpoint and the production roll-management path."""
    type_filtered = filter_options_chain_by_type(chain, option_type)
    delta_filtered = filter_options_chain_by_delta(type_filtered)
    position_filtered = filter_options_chain_for_position(delta_filtered, strike, option_type)
    position_filtered = filter_options_chain_by_delta(position_filtered)
    direction_filtered = filter_options_chain_by_roll_direction(
        position_filtered, current_strike=strike, current_expiration=expiration,
        roll_type=roll_type, option_type=option_type,
    )
    return delta_filtered, position_filtered, direction_filtered


class TestRootCauseReproduction:
    """Proves *why* the MSFT $525 call / 2026-09-04 contract disappears by
    the time the candidate table is built."""

    def test_degenerate_delta_contract_dropped_by_delta_filter(self):
        chain = _msft_chain_with_degenerate_current_contract()
        delta_filtered, position_filtered, direction_filtered = _run_pipeline(chain)

        # Objectively present in the raw chain:
        assert "525.0" in chain["calls"]["20260904"]
        # ... but gone as early as stage 1 (delta filter) — this is the bug.
        assert "20260904" not in delta_filtered.get("calls", {}) or \
            "525.0" not in delta_filtered["calls"].get("20260904", {})
        # ... and therefore absent from every later stage too.
        assert "20260904" not in position_filtered.get("calls", {})
        assert "20260904" not in direction_filtered.get("calls", {})

    def test_get_contract_finds_it_on_the_raw_chain(self):
        """The fix: get_contract() looked up on the RAW chain (before any
        filtering) always finds the held contract regardless of its delta.
        """
        chain = _msft_chain_with_degenerate_current_contract()
        contract = get_contract(chain, 525.0, "2026-09-04", "call")
        assert contract is not None
        assert contract["ask"] == 3.20


class TestFormatRollCandidatesTableCurrentContractParam:
    """Exercises the `current_contract` parameter added to
    format_roll_candidates_table to fix the debug-pipeline / production
    divergence."""

    def test_buyback_cost_surfaces_via_current_contract_override(self):
        chain = _msft_chain_with_degenerate_current_contract()
        _, _, direction_filtered = _run_pipeline(chain)
        current_contract = get_contract(chain, 525.0, "2026-09-04", "call")

        table = format_roll_candidates_table(
            chain=direction_filtered,
            current_strike=525.0,
            current_expiration="2026-09-04",
            option_type="call",
            underlying_price=480.35,
            roll_type="ROLL_OUT",
            buyback_cost=None,  # not pre-computed by caller
            current_contract=current_contract,
        )

        assert "Buyback cost (ask): $3.20" in table
        assert "Buyback available: true" in table
        assert "NO EXECUTABLE BUYBACK QUOTE" not in table
        assert "17 DTE" in table  # 2026-09-04 minus "today" in the table's DTE calc

    def test_without_current_contract_param_reports_no_chain_data(self):
        """Documents the pre-fix symptom: a caller that only relies on the
        (already filtered) `chain` param for the current contract, and never
        supplies an externally-captured reference or buyback_cost, cannot
        find the contract's real $3.20 ask — this is the exact bug being
        guarded against. A same-side candidate can still exist at another
        expiration, but its economics come back N/A because the true
        buyback cost was lost.
        """
        chain = _msft_chain_with_degenerate_current_contract()
        _, _, direction_filtered = _run_pipeline(chain)

        table = format_roll_candidates_table(
            chain=direction_filtered,
            current_strike=525.0,
            current_expiration="2026-09-04",
            option_type="call",
            underlying_price=480.35,
            roll_type="ROLL_OUT",
            buyback_cost=None,
            current_contract=None,
        )

        assert "Buyback cost: N/A" in table
        assert "$3.20" not in table
        assert "Buyback/P&L/net-credit economics are N/A until a positive finite ask is available" in table

    def test_explicit_buyback_cost_still_takes_precedence(self):
        """buyback_cost explicitly passed by the caller wins over
        current_contract's own ask (backward-compatible precedence)."""
        chain = _msft_chain_with_degenerate_current_contract()
        _, _, direction_filtered = _run_pipeline(chain)
        current_contract = get_contract(chain, 525.0, "2026-09-04", "call")

        table = format_roll_candidates_table(
            chain=direction_filtered,
            current_strike=525.0,
            current_expiration="2026-09-04",
            option_type="call",
            underlying_price=480.35,
            roll_type="ROLL_OUT",
            buyback_cost=2.75,
            current_contract=current_contract,
        )

        assert "Buyback cost (ask): $2.75" in table

    def test_zero_ask_current_contract_still_reports_incomplete(self):
        """A genuinely zero ask (real market-closed state) must still be
        treated as non-executable — current_contract is a data source, not
        a bypass of the positive-finite-ask safety rule. Use a chain with no
        other candidates so the "no executable quote" branch is exercised
        directly.
        """
        chain = _msft_chain_with_degenerate_current_contract()
        chain["calls"]["20260904"]["525.0"]["ask"] = 0.0
        del chain["calls"]["20261016"]  # no other candidates for this check
        _, _, direction_filtered = _run_pipeline(chain)
        current_contract = get_contract(chain, 525.0, "2026-09-04", "call")

        table = format_roll_candidates_table(
            chain=direction_filtered,
            current_strike=525.0,
            current_expiration="2026-09-04",
            option_type="call",
            underlying_price=480.35,
            roll_type="ROLL_OUT",
            buyback_cost=None,
            current_contract=current_contract,
        )

        assert "Buyback cost: N/A" in table
        assert "NO EXECUTABLE BUYBACK QUOTE for ROLL_OUT" in table

    def test_backward_compatible_lookup_when_contract_survives_filters(self):
        """A caller that passes a `chain` param which still contains the
        exact contract (e.g. before any direction filter narrows it out —
        direction filters always exclude the identical strike+expiration by
        design) continues to find it via the original in-chain lookup, with
        no `current_contract` argument needed."""
        chain = {
            "symbol": "TEST",
            "timestamp": "2026-08-18T06:00:00Z",
            "calls": {
                "20260904": {
                    "525.0": {
                        "strike": 525.0, "bid": 3.00, "ask": 3.20, "mid": 3.10,
                        "iv": 0.22, "delta": 0.35, "gamma": 0.01, "theta": -0.05,
                        "vega": 0.3, "rho": 0.05, "volume": 100, "openInterest": 50,
                        "lastPrice": 3.10, "lastTradeDate": "2026-08-17T20:00:00Z",
                        "inTheMoney": False, "expiration": "20260904", "option_type": "call",
                    },
                },
            },
            "puts": {},
        }
        table = format_roll_candidates_table(
            chain=chain,
            current_strike=525.0,
            current_expiration="2026-09-04",
            option_type="call",
            underlying_price=480.35,
            roll_type="ROLL_UP",
        )
        assert "Buyback cost (ask): $3.20" in table


# ===========================================================================
# Z-F1 / Z-F2 (danny-zero-free-agent-option-chains.md, §6.1): hidden-count
# footer accuracy and no fabricated-zero rendering.
# ===========================================================================

def _mixed_eligibility_chain():
    """A chain with: 1 current position (excluded from candidates by
    strike/expiration match, not by eligibility), 2 healthy candidates,
    and 3 contracts that must each be excluded as roll candidates for a
    distinct Z10 reason (zero/absent bid, zero open interest, invalid
    greeks)."""
    return {
        "symbol": "TEST",
        "timestamp": "2026-08-18T06:00:00Z",
        "calls": {
            "20260904": {
                "500.0": {  # current position
                    "strike": 500.0, "bid": 3.00, "ask": 3.20, "mid": 3.10,
                    "iv": 0.22, "delta": 0.35, "gamma": 0.01, "theta": -0.05,
                    "vega": 0.3, "rho": 0.05, "volume": 100, "openInterest": 50,
                    "lastPrice": 3.10, "lastTradeDate": "2026-08-17T20:00:00Z",
                    "inTheMoney": False, "expiration": "20260904", "option_type": "call",
                },
            },
            "20261016": {
                "490.0": {  # healthy candidate #1
                    "strike": 490.0, "bid": 8.50, "ask": 8.80, "mid": 8.65,
                    "iv": 0.22, "delta": 0.45, "gamma": 0.01, "theta": -0.05,
                    "vega": 0.4, "rho": 0.1, "volume": 500, "openInterest": 1000,
                    "lastPrice": 8.60, "lastTradeDate": "2026-08-17T20:00:00Z",
                    "inTheMoney": False, "expiration": "20261016", "option_type": "call",
                },
                "495.0": {  # healthy candidate #2
                    "strike": 495.0, "bid": 6.20, "ask": 6.50, "mid": 6.35,
                    "iv": 0.21, "delta": 0.40, "gamma": 0.01, "theta": -0.04,
                    "vega": 0.35, "rho": 0.09, "volume": 300, "openInterest": 800,
                    "lastPrice": 6.30, "lastTradeDate": "2026-08-17T20:00:00Z",
                    "inTheMoney": False, "expiration": "20261016", "option_type": "call",
                },
                "500.0": {  # hidden: zero bid (no_market -> unusable)
                    "strike": 500.0, "bid": 0.0, "ask": 5.50, "mid": None,
                    "iv": 0.20, "delta": 0.38, "gamma": 0.01, "theta": -0.04,
                    "vega": 0.3, "rho": 0.08, "volume": 20, "openInterest": 200,
                    "lastPrice": 0.0, "lastTradeDate": "2026-08-17T20:00:00Z",
                    "inTheMoney": False, "expiration": "20261016", "option_type": "call",
                },
                "505.0": {  # hidden: zero open interest
                    "strike": 505.0, "bid": 4.80, "ask": 5.10, "mid": 4.95,
                    "iv": 0.20, "delta": 0.33, "gamma": 0.01, "theta": -0.04,
                    "vega": 0.3, "rho": 0.07, "volume": 5, "openInterest": 0,
                    "lastPrice": 4.90, "lastTradeDate": "2026-08-17T20:00:00Z",
                    "inTheMoney": False, "expiration": "20261016", "option_type": "call",
                },
                "510.0": {  # hidden: invalid greeks (greeks_valid False)
                    "strike": 510.0, "bid": 3.60, "ask": 3.90, "mid": 3.75,
                    "iv": 0.0, "delta": None, "gamma": None, "theta": None,
                    "vega": None, "rho": None, "volume": 10, "openInterest": 150,
                    "lastPrice": 3.70, "lastTradeDate": "2026-08-17T20:00:00Z",
                    "inTheMoney": False, "expiration": "20261016", "option_type": "call",
                    "_meta": {"greeks_valid": False},
                },
            },
        },
        "puts": {},
    }


class TestZF1HiddenCountFooter:
    def test_footer_present_and_accurate_with_mixed_eligibility(self):
        chain = _mixed_eligibility_chain()
        table = format_roll_candidates_table(
            chain=chain,
            current_strike=500.0,
            current_expiration="2026-09-04",
            option_type="call",
            underlying_price=495.0,
            roll_type="ROLL_OUT",
        )
        # 3 ineligible candidates hidden (zero bid, zero OI, invalid greeks).
        assert "3 contracts hidden: no usable bid / no open interest / greeks unavailable" in table
        # And the 2 healthy candidates are visible.
        assert "490.0" in table
        assert "495.0" in table
        # The excluded strikes must not appear as candidate rows.
        assert "| 505.0 " not in table
        assert "| 510.0 " not in table

    def test_footer_present_even_when_all_candidates_hidden(self):
        chain = _mixed_eligibility_chain()
        del chain["calls"]["20261016"]["490.0"]
        del chain["calls"]["20261016"]["495.0"]
        table = format_roll_candidates_table(
            chain=chain,
            current_strike=500.0,
            current_expiration="2026-09-04",
            option_type="call",
            underlying_price=495.0,
            roll_type="ROLL_OUT",
        )
        assert "3 contracts hidden: no usable bid / no open interest / greeks unavailable" in table
        assert "NO VALID CANDIDATES found for ROLL_OUT" in table

    def test_footer_reports_zero_when_nothing_hidden(self):
        chain = _mixed_eligibility_chain()
        del chain["calls"]["20261016"]["500.0"]
        del chain["calls"]["20261016"]["505.0"]
        del chain["calls"]["20261016"]["510.0"]
        table = format_roll_candidates_table(
            chain=chain,
            current_strike=500.0,
            current_expiration="2026-09-04",
            option_type="call",
            underlying_price=495.0,
            roll_type="ROLL_OUT",
        )
        assert "0 contracts hidden: no usable bid / no open interest / greeks unavailable" in table


class TestZF2NoFabricatedZeroRendering:
    def test_rendered_table_has_no_dollar_zero_or_percent_zero_cells(self):
        """No `$0.00`, ` 0.00 `, or `0.0%` cell for an unavailable field —
        every value rendered is either a real positive number or the `N/A`/
        `-` placeholder, never a manufactured zero."""
        chain = _mixed_eligibility_chain()
        table = format_roll_candidates_table(
            chain=chain,
            current_strike=500.0,
            current_expiration="2026-09-04",
            option_type="call",
            underlying_price=495.0,
            roll_type="ROLL_OUT",
        )
        assert not re.search(r"\$0\.00\b", table)
        assert not re.search(r"\s0\.00\s", table)
        assert not re.search(r"\b0\.0%", table)

    def test_current_position_with_no_usable_quotes_renders_na_not_zero(self):
        """A current position with no usable bid/delta/theta must render
        `N/A`, never a fabricated `$0.00` / `0.00` / `0` cell."""
        chain = _mixed_eligibility_chain()
        current = chain["calls"]["20260904"]["500.0"]
        current["bid"] = 0.0
        current["delta"] = None
        current["theta"] = None
        current["ask"] = 0.0
        table = format_roll_candidates_table(
            chain=chain,
            current_strike=500.0,
            current_expiration="2026-09-04",
            option_type="call",
            underlying_price=495.0,
            roll_type="ROLL_OUT",
        )
        assert "Bid: N/A" in table
        assert "Delta: N/A" in table
        assert "Theta: N/A" in table
        assert not re.search(r"\$0\.00\b", table)
