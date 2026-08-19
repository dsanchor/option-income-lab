"""
Unit tests for src/roll_table.py.

Column layout (new behavior):
  [previous (optional, if future)] → [current (always)] → [next N futures]

Fixture chain:
  - current_exp_key = _future(3): NO previous (it is the nearest expiration).
    Columns = [current(_future3), exp_8, exp_15, exp_22, exp_29] = 5 total.
  - current_exp_key has a FULL strike ladder (395–445) with the current position
    at $420 overridden to bid=1.40, ask=1.60 (executable buyback=1.60).
  - underlying_price=417.50:
      ATM  target=417.50   → selected strike = 415.0  (tie 415/420, min picks 415)
      +3%  target=430.025  → selected strike = 435.0  (430 < target; 435 ≥ target)
      -3%  target=404.975  → selected strike = 395.0  (405 > target; 395 ≤ target)
  - exp_8  (+8d):  bid=0 at 430.0 (used by test_bid_zero_gives_gray_cell).
  - exp_29 (+29d): no 430.0 strike.
  - Puts mirror calls.

Strike is now FIXED from the first expiration with available strikes (which is
current_exp_key in the shared fixture), then re-used across all columns.
Cells are located by date string (not fragile integer index) wherever possible.
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


def _display_date(days: int) -> str:
    """Return a future date in YYYY-MM-DD format, *days* from today."""
    return (date.today() + timedelta(days=days)).strftime("%Y-%m-%d")


def _make_contract(bid: float, ask: float, delta: float = -0.40) -> dict:
    return {"bid": bid, "ask": ask, "mid": round((bid + ask) / 2, 4), "delta": delta}


def _cell_for_date(cells: list, date_str: str) -> dict:
    """Find a cell by its expiration date string. Raises StopIteration if not found."""
    return next(c for c in cells if c["expiration"] == date_str)


def _future_cells(result: dict, row_label: str) -> list:
    """Return cells that are NOT marked is_current and NOT is_previous."""
    future_dates = {
        e["date"] for e in result["expirations"]
        if not e["is_current"] and not e["is_previous"]
    }
    row = next(r for r in result["rows"] if r["label"] == row_label)
    return [c for c in row["cells"] if c["expiration"] in future_dates]


# ---------------------------------------------------------------------------
# Shared fixture — realistic chain for a $417.50 underlying
# ---------------------------------------------------------------------------

@pytest.fixture
def underlying_price() -> float:
    return 417.50


@pytest.fixture
def current_exp_key() -> str:
    # Current position expires in 3 days (still open, roll is being evaluated).
    # Being the nearest expiration, there is NO previous column.
    return _future(3)


@pytest.fixture
def chain(current_exp_key) -> dict:
    """
    Full-ladder chain.

    current_exp_key (_future(3)):
      Full strike ladder 395–445; position at 420 overridden to bid=1.40, ask=1.60
      so executable buyback = ask 1.60 → buyback_cost = $160.

    Strike selection (from current_exp_key, the first expiration):
      ATM (+0%)  target=417.50   → 415.0  (|415-417.5|=2.5, tie w/ 420, min picks 415)
      +3%        target=430.025  → 435.0  (430.0 < 430.025 → first ≥ is 435.0)
      -3%        target=404.975  → 395.0  (405.0 > 404.975 → max ≤ is 395.0)

    Future expirations:
      exp_8  (+8d):  _strikes_exp8  — 430.0 bid=0 (irrelevant for fixed +3% strike=435)
      exp_15 (+15d): _strikes_normal
      exp_22 (+22d): _strikes_normal
      exp_29 (+29d): _strikes_exp29_no_430 — no 430.0 (irrelevant; fixed strike=435 is present)
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

    def _strikes_current(delta_sign=1) -> dict:
        """Full ladder; position at 420 uses bid=1.40/ask=1.60 for the buyback test."""
        s = _strikes_normal(delta_sign)
        s["420.0"] = _make_contract(1.40, 1.60, delta_sign * 0.50)
        return s

    # exp_8: 430.0 bid=0.  +3% fixed strike is 435 (not 430), so this only matters
    # when underlying=417.0 is used to make +3% target=429.51 → chosen=430.
    def _strikes_exp8() -> dict:
        strikes = _strikes_normal()
        strikes["430.0"] = _make_contract(0.0, 0.05, 0.30)
        return strikes

    def _strikes_exp29_no_430() -> dict:
        strikes = _strikes_normal()
        del strikes["430.0"]
        return strikes

    calls = {
        current_exp_key: _strikes_current(),
        exp_8:  _strikes_exp8(),
        exp_15: _strikes_normal(),
        exp_22: _strikes_normal(),
        exp_29: _strikes_exp29_no_430(),
    }

    puts = {
        current_exp_key: _strikes_current(delta_sign=-1),
        exp_8:  _strikes_normal(delta_sign=-1),
        exp_15: _strikes_normal(delta_sign=-1),
        exp_22: _strikes_normal(delta_sign=-1),
        exp_29: _strikes_exp29_no_430(),
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
        # A short option is bought to close at the executable ask, not midpoint.
        result = self._run(chain, underlying_price, current_exp_key)
        assert result["buyback_per_share"] == 1.60
        assert result["buyback_cost"] == 160.0

    def test_pct_captured(self, chain, underlying_price, current_exp_key):
        # premium_received=3.20, executable ask=1.60 → 50% captured.
        result = self._run(chain, underlying_price, current_exp_key)
        assert result["pct_captured"] == pytest.approx(0.50)
        assert result["profit_target_reached"] is False

    def test_four_expirations_returned(self, chain, underlying_price, current_exp_key):
        # Columns: [current] + [exp_8, exp_15, exp_22, exp_29] = 5 total (no previous)
        result = self._run(chain, underlying_price, current_exp_key)
        assert len(result["expirations"]) == 5
        # Exactly one column is marked is_current, none is_previous
        assert sum(1 for e in result["expirations"] if e["is_current"]) == 1
        assert sum(1 for e in result["expirations"] if e["is_previous"]) == 0
        # 4 plain future columns
        futures = [e for e in result["expirations"] if not e["is_current"] and not e["is_previous"]]
        assert len(futures) == 4

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
        # Strike is fixed once from current_exp_key (full ladder).
        # +3% target = 417.50 * 1.03 = 430.025 → first strike >= target = 435.0
        result = self._run(chain, underlying_price, current_exp_key)
        plus3_row = next(r for r in result["rows"] if r["label"] == "+3%")
        # Row-level strike is from the first expiration
        assert plus3_row["strike"] == 435.0
        assert plus3_row["strike"] >= underlying_price * 1.03
        # Every non-gray cell uses the same fixed strike, which satisfies the constraint
        for cell in plus3_row["cells"]:
            if cell["color"] != "gray" and cell["strike"] is not None:
                assert cell["strike"] >= underlying_price * 1.03

    def test_minus3_strike_lte_target(self, chain, underlying_price, current_exp_key):
        # -3% target = 417.50 * 0.97 = 404.975 → largest strike ≤ target = 395.0
        # (405.0 > 404.975, so 395.0 is the best candidate)
        result = self._run(chain, underlying_price, current_exp_key)
        minus3_row = next(r for r in result["rows"] if r["label"] == "-3%")
        assert minus3_row["strike"] == 395.0
        assert minus3_row["strike"] <= underlying_price * 0.97 + 0.01  # float tolerance
        for cell in minus3_row["cells"]:
            if cell["color"] != "gray" and cell["strike"] is not None:
                assert cell["strike"] <= underlying_price * 0.97 + 0.01

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
        # underlying=417.0 → +3% target=417.0*1.03=429.51
        # From current_exp_key (full ladder): first strike ≥ 429.51 = 430.0
        # → chosen_strike=430.0; exp_8 has bid=0 at 430.0 → that cell is gray
        result = compute_roll_table(
            chain=chain,
            current_strike=420.0,
            current_expiration=current_exp_key,
            option_type="covered_call",
            underlying_price=417.0,   # +3% target=429.51 → 430.0 qualifies and has bid=0 in exp_8
            premium_received=3.20,
            contracts=1,
        )
        plus3_row = next(r for r in result["rows"] if r["label"] == "+3%")
        # Locate exp_8 cell by date, not index
        exp_8_cell = _cell_for_date(plus3_row["cells"], _display_date(8))
        assert exp_8_cell["color"] == "gray"
        # Z-R1 (danny-zero-free-agent-option-chains.md): a genuine bid=0
        # must never be fabricated into a numeric 0.0 cell value — it is
        # nulled just like a missing quote, so downstream consumers cannot
        # arithmetically treat "no market" as a real, tradeable $0.00 bid.
        assert exp_8_cell["bid"] is None
        assert exp_8_cell["net_credit"] is None

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
        # Columns = [current] + 2 futures = 3 total (no previous in shared fixture)
        assert len(result["expirations"]) == 3
        for row in result["rows"]:
            assert len(row["cells"]) == 3

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
        assert result["buyback_per_share"] == 1.60

    def test_multi_contract(self, chain, underlying_price, current_exp_key):
        # 3 contracts: executable ask 1.60 × 100 × 3 = 480
        result = compute_roll_table(
            chain=chain,
            current_strike=420.0,
            current_expiration=current_exp_key,
            option_type="call",
            underlying_price=underlying_price,
            premium_received=3.20,
            contracts=3,
        )
        assert result["buyback_cost"] == 480.0
        for row in result["rows"]:
            for cell in row["cells"]:
                if cell["color"] in ("green", "red") and cell["bid"] is not None:
                    expected = round(cell["bid"] * 100 * 3 - 480.0, 2)
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
        assert result["buyback_per_share"] == 1.60

    def test_four_expirations(self, chain, underlying_price, current_exp_key):
        # Same as call: [current] + 4 futures = 5 total (no previous in shared fixture)
        result = self._run(chain, underlying_price, current_exp_key)
        assert len(result["expirations"]) == 5

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
        # premium=3.20, executable ask=1.60 → 50% captured.
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
        # premium=8.00, executable ask=1.60 → 80% captured.
        result = compute_roll_table(
            chain=chain,
            current_strike=420.0,
            current_expiration=current_exp_key,
            option_type="call",
            underlying_price=underlying_price,
            premium_received=8.00,
        )
        assert result["profit_target_reached"] is True
        assert result["pct_captured"] >= 0.70

    def test_target_exceeded(self, chain, underlying_price, current_exp_key):
        # premium=6.00, executable ask=1.60 → 73.33% captured.
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
# Tests: executable current ask
# ---------------------------------------------------------------------------

class TestExecutableBuybackQuote:
    _MISSING = object()

    @classmethod
    def _chain(cls, current_exp_key, *, bid=0.0, ask=_MISSING, last=0.0):
        contract = {
            "bid": bid,
            "mid": 0.0,
            "last": last,
            "delta": 0.20,
        }
        if ask is not cls._MISSING:
            contract["ask"] = ask
        return {
            "symbol": "MSFT",
            "timestamp": "2026-08-17T14:00:00Z",
            "calls": {current_exp_key: {"420.0": contract}},
            "puts": {},
        }

    @pytest.mark.parametrize(
        ("bid", "ask", "last"),
        [
            pytest.param(0.0, 0.0, 2.40, id="zero-market-positive-last"),
            pytest.param(1.10, _MISSING, 2.40, id="missing-ask"),
            pytest.param(0.80, 0.0, 2.40, id="positive-bid-zero-ask"),
            pytest.param(0.80, -0.10, 2.40, id="negative-ask"),
            pytest.param(0.80, float("nan"), 2.40, id="nan-ask"),
            pytest.param(0.80, float("inf"), 2.40, id="infinite-ask"),
            pytest.param(0.80, "1.20", 2.40, id="non-numeric-ask"),
            pytest.param(0.80, True, 2.40, id="boolean-ask"),
        ],
    )
    def test_invalid_ask_keeps_buyback_and_profit_unavailable(
        self, current_exp_key, bid, ask, last
    ):
        result = compute_roll_table(
            chain=self._chain(
                current_exp_key, bid=bid, ask=ask, last=last
            ),
            current_strike=420.0,
            current_expiration=current_exp_key,
            option_type="open_call_monitor",
            underlying_price=410.0,
            premium_received=3.20,
        )

        assert result["buyback_per_share"] is None
        assert result["buyback_cost"] is None
        assert result["pct_captured"] is None
        assert result["profit_target_reached"] is False
        assert result["buyback_available"] is False
        assert result["incomplete_data"] is True

    def test_positive_ask_is_executable_even_when_bid_is_zero(self, current_exp_key):
        result = compute_roll_table(
            chain=self._chain(
                current_exp_key, bid=0.0, ask=1.20, last=9.99
            ),
            current_strike=420.0,
            current_expiration=current_exp_key,
            option_type="open_call_monitor",
            underlying_price=410.0,
            premium_received=3.20,
        )

        assert result["buyback_per_share"] == pytest.approx(1.20)
        assert result["buyback_cost"] == pytest.approx(120.0)
        assert result["pct_captured"] == pytest.approx(0.625)
        assert result["profit_target_reached"] is False
        assert result["buyback_available"] is True
        assert result["incomplete_data"] is False


# ---------------------------------------------------------------------------
# Tests: +3% fallback (no strike >= target → use highest available)
# ---------------------------------------------------------------------------

class TestPlus3Fallback:
    def test_fallback_to_highest_when_no_strike_above_target(self, current_exp_key):
        """If no strike in current_exp_key >= underlying*1.03, fallback to the highest
        available strike in current_exp_key (which is then fixed across all expirations)."""
        exp_fut = _future(10)
        # All strikes in current_exp_key are BELOW the +3% target (430.025).
        # Only strike = 420.0 → fallback = max([420.0]) = 420.0.
        # exp_fut also has 420.0 → non-gray cell with that fallback strike.
        chain = {
            "symbol": "TEST",
            "timestamp": "2026-07-23T14:00:00Z",
            "calls": {
                current_exp_key: {"420.0": _make_contract(1.40, 1.60)},
                exp_fut: {
                    "400.0": _make_contract(5.0, 5.5),
                    "410.0": _make_contract(4.0, 4.5),
                    "415.0": _make_contract(3.0, 3.5),
                    "420.0": _make_contract(2.0, 2.5),
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
        # Row-level strike is the fallback: highest in current_exp_key = 420.0
        assert plus3_row["strike"] == 420.0
        # exp_fut cell: strike=420.0 is present in exp_fut → non-gray
        exp_fut_cell = _cell_for_date(plus3_row["cells"], _display_date(10))
        assert exp_fut_cell["color"] != "gray"
        assert exp_fut_cell["strike"] == 420.0


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
        # Columns: [current_exp_key, exp_fut]; cells[1] = exp_fut = gray
        for row in result["rows"]:
            exp_fut_cell = _cell_for_date(row["cells"], _display_date(10))
            assert exp_fut_cell["color"] == "gray"
            assert exp_fut_cell["strike"] is None

    def test_bid_absent_gives_gray_with_null_bid(self, current_exp_key):
        """Z-R1: a contract present in the chain but with `bid` entirely
        absent (never `0`) must behave identically to bid=0 — gray cell,
        null bid/net_credit — never a fabricated `0.0` sneaking through a
        raw `contract.get("bid") or 0` collapse."""
        exp_fut = _future(10)
        chain = {
            "symbol": "TEST",
            "timestamp": "2026-07-23T14:00:00Z",
            "calls": {
                current_exp_key: {"420.0": _make_contract(1.40, 1.60)},
                exp_fut: {
                    "420.0": {"ask": 0.05, "delta": 0.10},  # bid key omitted entirely
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
            exp_fut_cell = _cell_for_date(row["cells"], _display_date(10))
            assert exp_fut_cell["color"] == "gray"
            assert exp_fut_cell["bid"] is None
            assert exp_fut_cell["net_credit"] is None
            # ask is a separately-usable field and must still surface.
            assert exp_fut_cell["ask"] == 0.05

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
        # Strike fixed from current_exp_key (420.0 only); exp_fut has 420.0 with bid=0 → gray
        for row in result["rows"]:
            exp_fut_cell = _cell_for_date(row["cells"], _display_date(10))
            assert exp_fut_cell["color"] == "gray"
            # Z-R1: bid=0 is nulled, never fabricated as a numeric 0.0.
            assert exp_fut_cell["bid"] is None
            assert exp_fut_cell["net_credit"] is None

    def test_current_contract_not_in_chain_leaves_buyback_unavailable(self, current_exp_key):
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
        assert result["buyback_per_share"] is None
        assert result["buyback_cost"] is None
        assert result["pct_captured"] is None
        assert result["profit_target_reached"] is False
        assert result["buyback_available"] is False
        assert result["incomplete_data"] is True


# ---------------------------------------------------------------------------
# Tests: expiration filtering
# ---------------------------------------------------------------------------

class TestExpirationFiltering:
    def test_expirations_before_current_excluded(self, chain, underlying_price, current_exp_key):
        # New rule: current IS included (is_current=True); only strictly PAST dates are excluded.
        # In the shared fixture, current_exp_key = _future(3) is the nearest expiration,
        # so there is no previous column (is_previous is never True).
        result = compute_roll_table(
            chain=chain,
            current_strike=420.0,
            current_expiration=current_exp_key,
            option_type="call",
            underlying_price=underlying_price,
            premium_received=3.20,
        )
        # All DTE values must be ≥ 0 (no past dates appear at all)
        for exp in result["expirations"]:
            assert exp["dte"] >= 0, f"Stale expiration appeared: {exp}"
        # Exactly one entry is is_current=True
        assert sum(1 for e in result["expirations"] if e["is_current"]) == 1
        # No is_previous in this fixture (current is already the nearest)
        assert sum(1 for e in result["expirations"] if e["is_previous"]) == 0
        # The current expiration's date must match current_exp_key
        curr = next(e for e in result["expirations"] if e["is_current"])
        assert curr["date"].replace("-", "") == current_exp_key.replace("-", "")

    def test_num_expiries_capped_by_available(self, current_exp_key):
        # Only 2 future expirations in chain → ask for 4 → get 2 + 1 current = 3 total
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
        # [current] + 2 futures = 3 total (no previous)
        assert len(result["expirations"]) == 3


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
        # Executable buyback ask is 1.60 × 100 = 160.0.
        # Strike from current_exp_key: only 420.0 → ATM=420.0 (fallback)
        # exp_fut has 420.0? No — exp_fut only has 415.0. Fixed strike=420.0 not in exp_fut → gray?
        # Wait: exp_fut has "415.0" only, and strike is fixed as 420.0 (from current).
        # 420.0 not in exp_fut → gray for exp_fut cell.
        # Current column: strike=420.0, bid=1.40, net=1.40*100-150=-10 → red.
        atm_row = next(r for r in result["rows"] if r["label"] == "ATM")
        # Current cell has bid=1.40
        current_cell = _cell_for_date(atm_row["cells"], _display_date(3))
        assert current_cell["bid"] == 1.40
        expected_net = round(1.40 * 100 * 1 - 160.0, 2)
        assert current_cell["net_credit"] == pytest.approx(expected_net)
        assert current_cell["color"] == "red"

    def test_red_when_new_bid_below_buyback(self, current_exp_key):
        """net_credit negative → red."""
        exp_fut = _future(10)
        chain = {
            "symbol": "TEST",
            "timestamp": "2026-07-23T14:00:00Z",
            "calls": {
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
        assert result["buyback_cost"] == 320.0
        atm_row = next(r for r in result["rows"] if r["label"] == "ATM")
        # exp_fut has 420.0 with bid=0.50; net = 0.50*100 - 320 = -270
        exp_fut_cell = _cell_for_date(atm_row["cells"], _display_date(10))
        assert exp_fut_cell["net_credit"] == -270.0
        assert exp_fut_cell["color"] == "red"


class TestSameStrikeAcrossExpirations:
    """The chosen strike is fixed from the first expiration and reused for all;
    a later expiration missing that exact strike yields a gray cell (it does NOT
    re-select a different nearby strike per expiration)."""

    def _chain(self):
        exp_a = _future(8)
        exp_b = _future(15)
        # First expiration: ATM target 417.50 → nearest is 415.0
        first = {
            "410.0": _make_contract(3.00, 3.20, 0.55),
            "415.0": _make_contract(2.00, 2.20, 0.50),
            "420.0": _make_contract(1.20, 1.40, 0.45),
        }
        # Second expiration LACKS 415.0 but has a nearby 416.0 — old per-exp logic
        # would have picked 416.0; new logic must return gray (415.0 absent).
        second = {
            "410.0": _make_contract(3.40, 3.60, 0.55),
            "416.0": _make_contract(2.10, 2.30, 0.50),
            "420.0": _make_contract(1.50, 1.70, 0.45),
        }
        return {
            "symbol": "TST",
            "timestamp": "2026-07-23T14:00:00Z",
            "calls": {_future(3): {"415.0": _make_contract(1.40, 1.60, 0.50)},
                      exp_a: first, exp_b: second},
            "puts": {},
        }

    def test_strike_fixed_and_missing_is_gray(self):
        result = compute_roll_table(
            chain=self._chain(),
            current_strike=415.0,
            current_expiration=_future(3),
            option_type="covered_call",
            underlying_price=417.50,
            premium_received=3.00,
            strike_offsets=(0.0,),
        )
        atm_row = next(r for r in result["rows"] if r["label"] == "ATM")
        # Strike fixed from current_exp_key (_future(3)) which has 415.0
        assert atm_row["strike"] == 415.0
        # Column order: [current(_future3), exp_a(_future8), exp_b(_future15)]
        # current column: has 415.0 → NOT gray
        current_cell = _cell_for_date(atm_row["cells"], _display_date(3))
        assert current_cell["strike"] == 415.0
        assert current_cell["color"] != "gray"
        # exp_a (_future8): has 415.0 in 'first' dict → NOT gray
        exp_a_cell = _cell_for_date(atm_row["cells"], _display_date(8))
        assert exp_a_cell["color"] != "gray"
        # exp_b (_future15): lacks 415.0 → gray (NOT re-selected to 416.0)
        exp_b_cell = _cell_for_date(atm_row["cells"], _display_date(15))
        assert exp_b_cell["color"] == "gray"
        assert exp_b_cell["strike"] is None




# ---------------------------------------------------------------------------
# Tests: column layout -- previous / current / future ordering and flags
# ---------------------------------------------------------------------------

class TestColumnLayout:
    """Verify the new [previous?] -> [current] -> [futures...] column layout."""

    def test_no_previous_when_current_is_nearest(self):
        """When current is the nearest open expiration, no previous column appears."""
        current_exp_key = _future(3)
        exp_fut = _future(10)
        chain = {
            "symbol": "TEST",
            "timestamp": "2026-07-23T14:00:00Z",
            "calls": {
                current_exp_key: {"420.0": _make_contract(1.40, 1.60)},
                exp_fut: {"420.0": _make_contract(2.00, 2.20)},
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
        assert sum(1 for e in result["expirations"] if e["is_previous"]) == 0
        assert result["expirations"][0]["is_current"] is True

    def test_previous_column_appears_when_future_exp_precedes_current(self):
        """When a future expiration exists before current, it appears as is_previous=True."""
        prev_exp    = _future(5)   # earlier than current, but still future
        current_exp = _future(10)  # current
        future_exp  = _future(17)  # regular future
        chain = {
            "symbol": "TEST",
            "timestamp": "2026-07-23T14:00:00Z",
            "calls": {
                prev_exp:    {"420.0": _make_contract(0.80, 1.00)},
                current_exp: {"420.0": _make_contract(1.40, 1.60)},
                future_exp:  {"420.0": _make_contract(2.00, 2.20)},
            },
            "puts": {},
        }
        result = compute_roll_table(
            chain=chain,
            current_strike=420.0,
            current_expiration=current_exp,
            option_type="call",
            underlying_price=417.50,
            premium_received=3.20,
            num_expiries=1,
        )
        exps = result["expirations"]
        # 3 columns: previous + current + 1 future
        assert len(exps) == 3
        assert exps[0]["is_previous"] is True
        assert exps[1]["is_current"] is True
        assert exps[2]["is_current"] is False
        assert exps[2]["is_previous"] is False

    def test_exactly_one_is_current(self):
        """Exactly one expiration entry has is_current=True regardless of num_expiries."""
        current_exp = _future(7)
        chain = {
            "symbol": "TEST",
            "timestamp": "2026-07-23T14:00:00Z",
            "calls": {
                current_exp: {"420.0": _make_contract(1.40, 1.60)},
                _future(14): {"420.0": _make_contract(1.80, 2.00)},
                _future(21): {"420.0": _make_contract(2.10, 2.30)},
                _future(28): {"420.0": _make_contract(2.40, 2.60)},
            },
            "puts": {},
        }
        result = compute_roll_table(
            chain=chain,
            current_strike=420.0,
            current_expiration=current_exp,
            option_type="call",
            underlying_price=417.50,
            premium_received=3.20,
        )
        assert sum(1 for e in result["expirations"] if e["is_current"]) == 1

    def test_column_order_previous_lt_current_lt_futures(self):
        """Columns must be ordered: previous.date < current.date < future[0].date < ..."""
        prev_exp    = _future(4)
        current_exp = _future(11)
        fut_exp1    = _future(18)
        fut_exp2    = _future(25)
        chain = {
            "symbol": "TEST",
            "timestamp": "2026-07-23T14:00:00Z",
            "calls": {
                prev_exp:    {"420.0": _make_contract(0.70, 0.90)},
                current_exp: {"420.0": _make_contract(1.40, 1.60)},
                fut_exp1:    {"420.0": _make_contract(1.90, 2.10)},
                fut_exp2:    {"420.0": _make_contract(2.30, 2.50)},
            },
            "puts": {},
        }
        result = compute_roll_table(
            chain=chain,
            current_strike=420.0,
            current_expiration=current_exp,
            option_type="call",
            underlying_price=417.50,
            premium_received=3.20,
            num_expiries=2,
        )
        dates = [e["date"].replace("-", "") for e in result["expirations"]]
        assert dates == sorted(dates), f"Columns not in ascending order: {dates}"
        exps = result["expirations"]
        assert exps[0]["is_previous"] is True
        assert exps[1]["is_current"] is True
        assert all(not e["is_current"] and not e["is_previous"] for e in exps[2:])

if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v"])
