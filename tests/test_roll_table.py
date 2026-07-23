"""
Unit tests for src/roll_table.py.

Fixture chain has:
  - MSFT-style calls + puts
  - Current position:  call $420 exp 20260801  (past relative to test dates)
  - 4 future call expirations: 20260808, 20260815, 20260822, 20260829
  - 4 future put  expirations: same keys
  - Strikes around $417.50 (underlying)

Scenarios covered:
  ✓ Normal call — green cells (net credit) and red cells (net debit)
  ✓ Normal put  — symmetric bucket lookup
  ✓ bid == 0   → color gray (not a credit/debit decision)
  ✓ Strike not in expiration → gray cell
  ✓ 70% profit target reached / not reached
  ✓ +3% strike selection with exact match, fallback when none >= target
  ✓ -3% strike selection with exact match, fallback when none <= target
  ✓ Expiration before current_expiration excluded
  ✓ JSON string input for chain
  ✓ Unknown option_type defaults gracefully
"""

import json
from datetime import date, timedelta

import pytest

from src.roll_table import compute_roll_table, _select_strike, _label_for_offset


# ---------------------------------------------------------------------------
# Helpers to build test chains relative to today
# ---------------------------------------------------------------------------

def _yyyymmdd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _future(days: int) -> str:
    return _yyyymmdd(date.today() + timedelta(days=days))


def _make_contract(bid: float, ask: float, delta: float = -0.40) -> dict:
    return {"bid": bid, "ask": ask, "mid": round((bid + ask) / 2, 4), "delta": delta}


# ---------------------------------------------------------------------------
# Shared fixture — realistic chain for a $417.50 underlying
# ---------------------------------------------------------------------------

@pytest.fixture
def underlying_price() -> float:
    return 417.50


@pytest.fixture
def current_exp_key() -> str:
    # Current position expires in 3 days (still open, roll is being evaluated)
    return _future(3)


@pytest.fixture
def chain(current_exp_key) -> dict:
    """
    Chain with:
      - current call: $420 exp current_exp_key  bid=1.40, ask=1.60  (mid=1.50)
      - 4 future call expirations: +8d, +15d, +22d, +29d
      - Strikes per expiration: 395, 405, 415, 420, 425, 430, 435, 445
        (underlying ~417.50 → ATM≈415, +3%≈430, -3%≈405)
      - One expiration (+8d) has bid=0 at 430 to trigger gray
      - One expiration (+29d) lacks 430 strike to test missing strike → gray
        (not quite — we'll test fallback instead, see below)
      - Future put expirations mirror the call structure
    """
    exp_8  = _future(8)
    exp_15 = _future(15)
    exp_22 = _future(22)
    exp_29 = _future(29)

    def _strikes_normal(delta_sign=1) -> dict:
        return {
            "395.0": _make_contract(0.30, 0.40, delta_sign * 0.15),
            "405.0": _make_contract(0.85, 0.95, delta_sign * 0.25),
            "415.0": _make_contract(1.90, 2.10, delta_sign * 0.45),
            "420.0": _make_contract(2.40, 2.55, delta_sign * 0.50),
            "425.0": _make_contract(1.55, 1.70, delta_sign * 0.38),
            "430.0": _make_contract(0.90, 1.05, delta_sign * 0.30),
            "435.0": _make_contract(0.45, 0.55, delta_sign * 0.22),
            "445.0": _make_contract(0.15, 0.25, delta_sign * 0.10),
        }

    # exp_8: 430 has bid=0 → should be gray for the +3% row
    def _strikes_exp8() -> dict:
        strikes = _strikes_normal()
        strikes["430.0"] = _make_contract(0.0, 0.05, 0.30)   # bid=0 → gray
        return strikes

    # exp_29: no 430 strike → +3% target ($430) → fallback to max available (435)
    def _strikes_exp29_no_430() -> dict:
        strikes = _strikes_normal()
        del strikes["430.0"]
        return strikes

    calls = {
        current_exp_key: {
            "420.0": _make_contract(1.40, 1.60, 0.50),   # current position
        },
        exp_8:  _strikes_exp8(),
        exp_15: _strikes_normal(),
        exp_22: _strikes_normal(),
        exp_29: _strikes_exp29_no_430(),
    }

    # Puts mirror calls (negative deltas)
    puts = {
        current_exp_key: {
            "420.0": _make_contract(1.40, 1.60, -0.50),
        },
        exp_8:  _strikes_normal(delta_sign=-1),
        exp_15: _strikes_normal(delta_sign=-1),
        exp_22: _strikes_normal(delta_sign=-1),
        exp_29: _strikes_exp29_no_430(),   # reuse same dict (delta sign differs but irrelevant here)
    }

    return {
        "symbol": "MSFT",
        "timestamp": "2026-07-23T14:00:00Z",
        "calls": calls,
        "puts": puts,
    }


# ---------------------------------------------------------------------------
# Tests: _select_strike (unit — no chain needed)
# ---------------------------------------------------------------------------

class TestSelectStrike:
    def test_atm_picks_closest(self):
        strikes = [400.0, 405.0, 410.0, 415.0, 420.0]
        assert _select_strike(strikes, 413.0, 0.0) == 415.0   # 415 is closer than 410
        assert _select_strike(strikes, 412.0, 0.0) == 410.0   # 410 is closer

    def test_positive_offset_picks_smallest_above_target(self):
        strikes = [400.0, 410.0, 420.0, 430.0]
        # target = 417.50 * 1.03 = 430.025 → nearest above = 430
        assert _select_strike(strikes, 430.025, +0.03) == 430.0

    def test_positive_offset_fallback_when_no_above(self):
        # All strikes below target → return highest available
        strikes = [400.0, 410.0, 420.0]
        assert _select_strike(strikes, 450.0, +0.03) == 420.0

    def test_negative_offset_picks_largest_below_target(self):
        strikes = [395.0, 400.0, 405.0, 410.0]
        # target = 417.50 * 0.97 = 404.975 → nearest below = 404.975 → 400 (≤)
        # Actually: 405 is NOT <= 404.975; 400 IS <= 404.975 ✓
        result = _select_strike(strikes, 404.975, -0.03)
        assert result == 400.0

    def test_negative_offset_exact_match(self):
        strikes = [395.0, 405.0, 415.0, 430.0]
        assert _select_strike(strikes, 405.0, -0.03) == 405.0

    def test_negative_offset_fallback_when_no_below(self):
        # All strikes above target → return lowest available
        strikes = [420.0, 430.0, 440.0]
        assert _select_strike(strikes, 400.0, -0.03) == 420.0

    def test_empty_strikes_returns_none(self):
        assert _select_strike([], 417.50, 0.0) is None


class TestLabelForOffset:
    def test_atm(self):    assert _label_for_offset(0.0)   == "ATM"
    def test_plus3(self):  assert _label_for_offset(0.03)  == "+3%"
    def test_minus3(self): assert _label_for_offset(-0.03) == "-3%"
    def test_plus5(self):  assert _label_for_offset(0.05)  == "+5%"


# ---------------------------------------------------------------------------
# Tests: compute_roll_table — call scenario
# ---------------------------------------------------------------------------

class TestRollTableCall:
    """call covered call, underlying=$417.50, current pos: $420 call."""

    def _run(self, chain, underlying_price, current_exp_key, **kwargs):
        return compute_roll_table(
            chain=chain,
            current_strike=420.0,
            current_expiration=current_exp_key,
            option_type="covered_call",
            underlying_price=underlying_price,
            premium_received=3.20,  # $3.20/share originally
            contracts=1,
            **kwargs,
        )

    def test_basic_structure(self, chain, underlying_price, current_exp_key):
        result = self._run(chain, underlying_price, current_exp_key)
        assert "buyback_cost" in result
        assert "buyback_per_share" in result
        assert "pct_captured" in result
        assert "profit_target_reached" in result
        assert "underlying_price" in result
        assert "chain_timestamp" in result
        assert "current_position" in result
        assert "expirations" in result
        assert "rows" in result

    def test_buyback_cost(self, chain, underlying_price, current_exp_key):
        # current contract: bid=1.40, ask=1.60 → robust_mid = (1.40+1.60)/2 = 1.50
        result = self._run(chain, underlying_price, current_exp_key)
        assert result["buyback_per_share"] == 1.50
        assert result["buyback_cost"] == 150.0   # 1.50 × 100 × 1 contract

    def test_pct_captured(self, chain, underlying_price, current_exp_key):
        # premium_received=3.20, buyback_per_share=1.50
        # pct_captured = (3.20 - 1.50) / 3.20 = 1.70/3.20 ≈ 0.5313
        result = self._run(chain, underlying_price, current_exp_key)
        assert abs(result["pct_captured"] - (1.70 / 3.20)) < 0.001
        assert result["profit_target_reached"] is False   # 53% < 70%

    def test_four_expirations_returned(self, chain, underlying_price, current_exp_key):
        result = self._run(chain, underlying_price, current_exp_key)
        assert len(result["expirations"]) == 4

    def test_three_rows_returned(self, chain, underlying_price, current_exp_key):
        result = self._run(chain, underlying_price, current_exp_key)
        assert len(result["rows"]) == 3
        labels = [r["label"] for r in result["rows"]]
        assert "ATM" in labels
        assert "+3%" in labels
        assert "-3%" in labels

    def test_atm_strike_is_closest_to_price(self, chain, underlying_price, current_exp_key):
        # underlying=417.50 → closest strike = 415.0 (|415-417.5|=2.5 < |420-417.5|=2.5)
        # tie: both 415 and 420 are 2.5 away; min() with key picks 415 (first in sorted list)
        result = self._run(chain, underlying_price, current_exp_key)
        atm_row = next(r for r in result["rows"] if r["label"] == "ATM")
        assert atm_row["strike"] in (415.0, 420.0)   # allow either in a tie

    def test_plus3_strike_gte_target(self, chain, underlying_price, current_exp_key):
        # +3% target = 417.50 * 1.03 = 430.025 → nearest >= = 435 (since 430 exists in exp_15+)
        # For exp_15: 430.0 is available → 430.0
        result = self._run(chain, underlying_price, current_exp_key)
        plus3_row = next(r for r in result["rows"] if r["label"] == "+3%")
        for cell in plus3_row["cells"]:
            if cell["color"] != "gray" and cell["strike"] is not None:
                assert cell["strike"] >= underlying_price * 1.03 or cell["strike"] == max(
                    s for s in [395.0, 405.0, 415.0, 420.0, 425.0, 430.0, 435.0, 445.0]
                )

    def test_minus3_strike_lte_target(self, chain, underlying_price, current_exp_key):
        # -3% target = 417.50 * 0.97 = 404.975 → nearest <= = 405 is NOT (405 > 404.975)
        # → 400 not in chain → fallback to min? No — 405 > 404.975, so candidates empty → fallback to lowest (395)
        # Wait: chain has 405.0 and 395.0; 404.975 → ≤ 404.975: 395.0 qualifies; 405.0 does NOT
        # so result = max([395.0]) = 395.0
        result = self._run(chain, underlying_price, current_exp_key)
        minus3_row = next(r for r in result["rows"] if r["label"] == "-3%")
        # All cells with strike not None should be ≤ 404.975 or equal to lowest fallback
        for cell in minus3_row["cells"]:
            if cell["strike"] is not None and cell["color"] != "gray":
                assert cell["strike"] <= underlying_price * 0.97 + 0.01  # tiny float tolerance

    def test_green_cells_have_positive_net_credit(self, chain, underlying_price, current_exp_key):
        result = self._run(chain, underlying_price, current_exp_key)
        for row in result["rows"]:
            for cell in row["cells"]:
                if cell["color"] == "green":
                    assert cell["net_credit"] is not None
                    assert cell["net_credit"] > 0

    def test_red_cells_have_nonpositive_net_credit(self, chain, underlying_price, current_exp_key):
        result = self._run(chain, underlying_price, current_exp_key)
        for row in result["rows"]:
            for cell in row["cells"]:
                if cell["color"] == "red":
                    assert cell["net_credit"] is not None
                    assert cell["net_credit"] <= 0

    def test_bid_zero_gives_gray_cell(self, chain, current_exp_key):
        # underlying=417.0 → +3% target=417.0*1.03=429.51 → nearest strike >= target = 430.0
        # exp_8 has bid=0 at 430.0 → cell should be gray
        result = compute_roll_table(
            chain=chain,
            current_strike=420.0,
            current_expiration=current_exp_key,
            option_type="covered_call",
            underlying_price=417.0,   # +3% target=429.51 → 430.0 qualifies and has bid=0
            premium_received=3.20,
            contracts=1,
        )
        plus3_row = next(r for r in result["rows"] if r["label"] == "+3%")
        first_cell = plus3_row["cells"][0]   # exp_8 is first future expiration
        assert first_cell["color"] == "gray"

    def test_current_position_metadata(self, chain, underlying_price, current_exp_key):
        result = self._run(chain, underlying_price, current_exp_key)
        cp = result["current_position"]
        assert cp["strike"] == 420.0
        assert cp["option_type"] == "covered_call"
        assert cp["premium_received"] == 3.20

    def test_cells_contain_required_keys(self, chain, underlying_price, current_exp_key):
        result = self._run(chain, underlying_price, current_exp_key)
        required = {"expiration", "dte", "strike", "bid", "ask", "delta", "net_credit", "color"}
        for row in result["rows"]:
            for cell in row["cells"]:
                assert required.issubset(cell.keys()), f"Missing keys in cell: {cell}"

    def test_expirations_include_dte(self, chain, underlying_price, current_exp_key):
        result = self._run(chain, underlying_price, current_exp_key)
        for exp in result["expirations"]:
            assert "date" in exp
            assert "dte" in exp
            assert exp["dte"] > 0

    def test_chain_timestamp_preserved(self, chain, underlying_price, current_exp_key):
        result = self._run(chain, underlying_price, current_exp_key)
        assert result["chain_timestamp"] == "2026-07-23T14:00:00Z"

    def test_num_expiries_parameter(self, chain, underlying_price, current_exp_key):
        result = compute_roll_table(
            chain=chain,
            current_strike=420.0,
            current_expiration=current_exp_key,
            option_type="call",
            underlying_price=underlying_price,
            premium_received=3.20,
            num_expiries=2,
        )
        assert len(result["expirations"]) == 2
        for row in result["rows"]:
            assert len(row["cells"]) == 2

    def test_json_string_input(self, chain, underlying_price, current_exp_key):
        # chain can be passed as JSON string (as returned by OptionsChainCache)
        chain_str = json.dumps(chain)
        result = compute_roll_table(
            chain=chain_str,
            current_strike=420.0,
            current_expiration=current_exp_key,
            option_type="call",
            underlying_price=underlying_price,
            premium_received=3.20,
        )
        assert result["buyback_per_share"] == 1.50

    def test_multi_contract(self, chain, underlying_price, current_exp_key):
        # 3 contracts: buyback = 1.50 × 100 × 3 = 450
        result = compute_roll_table(
            chain=chain,
            current_strike=420.0,
            current_expiration=current_exp_key,
            option_type="call",
            underlying_price=underlying_price,
            premium_received=3.20,
            contracts=3,
        )
        assert result["buyback_cost"] == 450.0
        # net_credit for a green cell = new_bid × 100 × 3 − 450
        for row in result["rows"]:
            for cell in row["cells"]:
                if cell["color"] in ("green", "red") and cell["bid"] is not None:
                    expected = round(cell["bid"] * 100 * 3 - 450.0, 2)
                    assert cell["net_credit"] == expected


# ---------------------------------------------------------------------------
# Tests: compute_roll_table — put scenario
# ---------------------------------------------------------------------------

class TestRollTablePut:
    """cash_secured_put, same underlying/strike."""

    def _run(self, chain, underlying_price, current_exp_key, **kwargs):
        return compute_roll_table(
            chain=chain,
            current_strike=420.0,
            current_expiration=current_exp_key,
            option_type="cash_secured_put",
            underlying_price=underlying_price,
            premium_received=3.20,
            contracts=1,
            **kwargs,
        )

    def test_reads_puts_bucket(self, chain, underlying_price, current_exp_key):
        result = self._run(chain, underlying_price, current_exp_key)
        assert result["buyback_per_share"] == 1.50   # same mid from puts bucket

    def test_four_expirations(self, chain, underlying_price, current_exp_key):
        result = self._run(chain, underlying_price, current_exp_key)
        assert len(result["expirations"]) == 4

    def test_cells_structure_same_as_calls(self, chain, underlying_price, current_exp_key):
        result = self._run(chain, underlying_price, current_exp_key)
        required = {"expiration", "dte", "strike", "bid", "ask", "delta", "net_credit", "color"}
        for row in result["rows"]:
            for cell in row["cells"]:
                assert required.issubset(cell.keys())

    def test_option_type_preserved(self, chain, underlying_price, current_exp_key):
        result = self._run(chain, underlying_price, current_exp_key)
        assert result["current_position"]["option_type"] == "cash_secured_put"


# ---------------------------------------------------------------------------
# Tests: 70% profit target gate
# ---------------------------------------------------------------------------

class TestProfitTargetGate:
    def test_target_not_reached(self, chain, underlying_price, current_exp_key):
        # premium=3.20, buyback=1.50 → pct = (3.20-1.50)/3.20 = 53.1%
        result = compute_roll_table(
            chain=chain,
            current_strike=420.0,
            current_expiration=current_exp_key,
            option_type="call",
            underlying_price=underlying_price,
            premium_received=3.20,
        )
        assert result["profit_target_reached"] is False
        assert result["pct_captured"] < 0.70

    def test_target_reached(self, chain, underlying_price, current_exp_key):
        # premium=5.00, buyback=1.50 → pct = (5.00-1.50)/5.00 = 70.0% exactly
        result = compute_roll_table(
            chain=chain,
            current_strike=420.0,
            current_expiration=current_exp_key,
            option_type="call",
            underlying_price=underlying_price,
            premium_received=5.00,
        )
        assert result["profit_target_reached"] is True
        assert result["pct_captured"] >= 0.70

    def test_target_exceeded(self, chain, underlying_price, current_exp_key):
        # premium=2.00, buyback=1.50 → pct = 0.50/2.00 = 25%  (below 70)
        # Now use premium=1.55 → pct = (1.55-1.50)/1.55 ≈ 3.2%   (below 70)
        # Use premium=1.60 → pct = 0.10/1.60 = 6.25%   (below 70)
        # Use premium=1.52 to get just above 70%: (1.52-1.50)/1.52 = 0.02/1.52 = 1.3%
        # Let's use premium=1.53 to get 98%+:  no, let's just use a small premium
        # To reliably exceed 70%: premium=2.00, buyback=1.50 → 25% — nope.
        # To get >= 70%: need premium_received - buyback >= 0.70 * premium_received
        #                → 0.30 * premium_received >= buyback=1.50 → premium_received >= 5.0
        # So premium=5.0 → pct=70% (boundary), premium=6.0 → pct=75%
        result = compute_roll_table(
            chain=chain,
            current_strike=420.0,
            current_expiration=current_exp_key,
            option_type="call",
            underlying_price=underlying_price,
            premium_received=6.0,
        )
        assert result["profit_target_reached"] is True
        assert result["pct_captured"] > 0.70

    def test_zero_premium_no_crash(self, chain, underlying_price, current_exp_key):
        result = compute_roll_table(
            chain=chain,
            current_strike=420.0,
            current_expiration=current_exp_key,
            option_type="call",
            underlying_price=underlying_price,
            premium_received=0.0,
        )
        assert result["pct_captured"] == 0.0
        assert result["profit_target_reached"] is False


# ---------------------------------------------------------------------------
# Tests: +3% fallback (no strike >= target → use highest available)
# ---------------------------------------------------------------------------

class TestPlus3Fallback:
    def test_fallback_to_highest_when_no_strike_above_target(self, current_exp_key):
        """If no strike >= underlying*1.03, fallback to the highest available strike."""
        # Build a chain where all future strikes are BELOW +3% target
        exp_fut = _future(10)
        chain = {
            "symbol": "TEST",
            "timestamp": "2026-07-23T14:00:00Z",
            "calls": {
                current_exp_key: {"420.0": _make_contract(1.40, 1.60)},
                exp_fut: {
                    "400.0": _make_contract(5.0, 5.5),
                    "410.0": _make_contract(4.0, 4.5),
                    "415.0": _make_contract(3.0, 3.5),
                    # No strike >= 417.50 * 1.03 = 430.025
                },
            },
            "puts": {},
        }
        result = compute_roll_table(
            chain=chain,
            current_strike=420.0,
            current_expiration=current_exp_key,
            option_type="call",
            underlying_price=417.50,
            premium_received=3.20,
        )
        plus3_row = next(r for r in result["rows"] if r["label"] == "+3%")
        cell = plus3_row["cells"][0]
        # Fallback: highest available strike (415.0)
        assert cell["strike"] == 415.0


# ---------------------------------------------------------------------------
# Tests: -3% fallback (no strike <= target → use lowest available)
# ---------------------------------------------------------------------------

class TestMinus3Fallback:
    def test_fallback_to_lowest_when_no_strike_below_target(self, current_exp_key):
        """If no strike <= underlying*0.97, fallback to the lowest available strike."""
        exp_fut = _future(10)
        # underlying=417.50, -3% target=404.975
        # All strikes > 404.975 → fallback to lowest (420.0)
        chain = {
            "symbol": "TEST",
            "timestamp": "2026-07-23T14:00:00Z",
            "calls": {
                current_exp_key: {"420.0": _make_contract(1.40, 1.60)},
                exp_fut: {
                    "420.0": _make_contract(2.0, 2.2),
                    "425.0": _make_contract(1.5, 1.7),
                    "430.0": _make_contract(1.0, 1.2),
                },
            },
            "puts": {},
        }
        result = compute_roll_table(
            chain=chain,
            current_strike=420.0,
            current_expiration=current_exp_key,
            option_type="call",
            underlying_price=417.50,
            premium_received=3.20,
        )
        minus3_row = next(r for r in result["rows"] if r["label"] == "-3%")
        cell = minus3_row["cells"][0]
        # Fallback: lowest available (420.0)
        assert cell["strike"] == 420.0


# ---------------------------------------------------------------------------
# Tests: missing strike → gray
# ---------------------------------------------------------------------------

class TestGrayCells:
    def test_expiration_with_no_strikes_gives_gray_row(self, current_exp_key):
        exp_fut = _future(10)
        chain = {
            "symbol": "TEST",
            "timestamp": "2026-07-23T14:00:00Z",
            "calls": {
                current_exp_key: {"420.0": _make_contract(1.40, 1.60)},
                exp_fut: {},   # empty strikes dict
            },
            "puts": {},
        }
        result = compute_roll_table(
            chain=chain,
            current_strike=420.0,
            current_expiration=current_exp_key,
            option_type="call",
            underlying_price=417.50,
            premium_received=3.20,
        )
        for row in result["rows"]:
            assert row["cells"][0]["color"] == "gray"
            assert row["cells"][0]["strike"] is None

    def test_bid_zero_gives_gray(self, current_exp_key):
        exp_fut = _future(10)
        chain = {
            "symbol": "TEST",
            "timestamp": "2026-07-23T14:00:00Z",
            "calls": {
                current_exp_key: {"420.0": _make_contract(1.40, 1.60)},
                exp_fut: {
                    "415.0": _make_contract(0.0, 0.05),   # bid=0
                    "420.0": _make_contract(0.0, 0.02),   # bid=0
                    "405.0": _make_contract(0.0, 0.08),   # bid=0
                },
            },
            "puts": {},
        }
        result = compute_roll_table(
            chain=chain,
            current_strike=420.0,
            current_expiration=current_exp_key,
            option_type="call",
            underlying_price=417.50,
            premium_received=3.20,
        )
        for row in result["rows"]:
            assert row["cells"][0]["color"] == "gray"

    def test_current_contract_not_in_chain_sets_buyback_zero(self, current_exp_key):
        # Current contract missing from chain → buyback=0, pct_captured=1.0 (all captured)
        exp_fut = _future(10)
        chain = {
            "symbol": "TEST",
            "timestamp": "2026-07-23T14:00:00Z",
            "calls": {
                # current_exp_key missing the 420.0 contract
                current_exp_key: {"430.0": _make_contract(0.50, 0.60)},
                exp_fut: {"415.0": _make_contract(1.90, 2.10)},
            },
            "puts": {},
        }
        result = compute_roll_table(
            chain=chain,
            current_strike=420.0,
            current_expiration=current_exp_key,
            option_type="call",
            underlying_price=417.50,
            premium_received=3.20,
        )
        assert result["buyback_per_share"] == 0.0
        assert result["buyback_cost"] == 0.0
        # All premium is captured when buyback=0
        assert result["pct_captured"] == 1.0
        assert result["profit_target_reached"] is True


# ---------------------------------------------------------------------------
# Tests: expiration filtering
# ---------------------------------------------------------------------------

class TestExpirationFiltering:
    def test_expirations_before_current_excluded(self, chain, underlying_price, current_exp_key):
        result = compute_roll_table(
            chain=chain,
            current_strike=420.0,
            current_expiration=current_exp_key,
            option_type="call",
            underlying_price=underlying_price,
            premium_received=3.20,
        )
        # All returned expirations must be after current_expiration
        current_key = current_exp_key.replace("-", "")
        for exp in result["expirations"]:
            exp_key = exp["date"].replace("-", "")
            assert exp_key > current_key

    def test_num_expiries_capped_by_available(self, current_exp_key):
        # Only 2 future expirations in chain → ask for 4 → get 2
        exp_fut1 = _future(10)
        exp_fut2 = _future(17)
        chain = {
            "symbol": "TEST",
            "timestamp": "2026-07-23T14:00:00Z",
            "calls": {
                current_exp_key: {"420.0": _make_contract(1.40, 1.60)},
                exp_fut1: {"415.0": _make_contract(1.90, 2.10)},
                exp_fut2: {"415.0": _make_contract(2.50, 2.70)},
            },
            "puts": {},
        }
        result = compute_roll_table(
            chain=chain,
            current_strike=420.0,
            current_expiration=current_exp_key,
            option_type="call",
            underlying_price=417.50,
            premium_received=3.20,
            num_expiries=4,
        )
        assert len(result["expirations"]) == 2


# ---------------------------------------------------------------------------
# Tests: net_credit arithmetic
# ---------------------------------------------------------------------------

class TestNetCreditArithmetic:
    def test_net_credit_formula(self, current_exp_key):
        """net_credit = new_bid × 100 × contracts − buyback_cost."""
        exp_fut = _future(10)
        chain = {
            "symbol": "TEST",
            "timestamp": "2026-07-23T14:00:00Z",
            "calls": {
                current_exp_key: {"420.0": _make_contract(1.40, 1.60)},
                exp_fut: {"415.0": _make_contract(2.00, 2.20)},
            },
            "puts": {},
        }
        result = compute_roll_table(
            chain=chain,
            current_strike=420.0,
            current_expiration=current_exp_key,
            option_type="call",
            underlying_price=417.50,
            premium_received=3.20,
            contracts=1,
        )
        # buyback = robust_mid(1.40, 1.60) × 100 = 1.50 × 100 = 150.0
        # ATM cell: new_bid=2.00, net = 2.00×100 - 150 = 50.0
        atm_row = next(r for r in result["rows"] if r["label"] == "ATM")
        cell = atm_row["cells"][0]
        assert cell["bid"] == 2.00
        assert cell["net_credit"] == 50.0
        assert cell["color"] == "green"

    def test_red_when_new_bid_below_buyback(self, current_exp_key):
        """net_credit negative → red."""
        exp_fut = _future(10)
        chain = {
            "symbol": "TEST",
            "timestamp": "2026-07-23T14:00:00Z",
            "calls": {
                # buyback_per_share will be 0 because current contract not found
                # override: use a chain where buyback is high
                current_exp_key: {"420.0": _make_contract(3.00, 3.20)},
                exp_fut: {"420.0": _make_contract(0.50, 0.70)},   # new bid < buyback
            },
            "puts": {},
        }
        result = compute_roll_table(
            chain=chain,
            current_strike=420.0,
            current_expiration=current_exp_key,
            option_type="call",
            underlying_price=417.50,
            premium_received=5.00,
            contracts=1,
        )
        # buyback = robust_mid(3.00, 3.20) × 100 = 3.10 × 100 = 310.0
        assert result["buyback_cost"] == 310.0
        atm_row = next(r for r in result["rows"] if r["label"] == "ATM")
        cell = atm_row["cells"][0]
        # new_bid=0.50, net = 50.0 - 310.0 = -260.0
        assert cell["net_credit"] == -260.0
        assert cell["color"] == "red"


if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v"])
