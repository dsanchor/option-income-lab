import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from web.app import _is_complete_triplet, _build_dashboard_tables


# ---------------------------------------------------------------------------
# _is_complete_triplet
# ---------------------------------------------------------------------------

def test_triplet_complete_valid():
    assert _is_complete_triplet(185.0, "2026-07-18", 1.10) is True


def test_triplet_complete_string_values():
    assert _is_complete_triplet("185", "2026-07-18", "1.10") is True


def test_triplet_missing_strike():
    assert _is_complete_triplet(None, "2026-07-18", 1.10) is False


def test_triplet_zero_strike():
    assert _is_complete_triplet(0, "2026-07-18", 1.10) is False


def test_triplet_missing_expiration():
    assert _is_complete_triplet(185.0, None, 1.10) is False


def test_triplet_empty_expiration():
    assert _is_complete_triplet(185.0, "", 1.10) is False


def test_triplet_missing_premium():
    assert _is_complete_triplet(185.0, "2026-07-18", None) is False


def test_triplet_zero_premium():
    assert _is_complete_triplet(185.0, "2026-07-18", 0) is False


def test_triplet_non_numeric_strike():
    assert _is_complete_triplet("n/a", "2026-07-18", 1.10) is False


def test_triplet_non_numeric_premium():
    assert _is_complete_triplet(185.0, "2026-07-18", "n/a") is False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sym(symbol, agent_keys=("covered_call",)):
    wl = {k: True for k in agent_keys}
    return {
        "symbol": symbol,
        "display_name": symbol,
        "watchlist": wl,
        "positions": [],
    }


def _act(symbol, agent_type, strike=None, expiration=None, premium=None,
         alpha_view=None, underlying_price=100.0):
    return {
        "id": f"{symbol}-{agent_type}",
        "symbol": symbol,
        "agent_type": agent_type,
        "activity": "WAIT",
        "timestamp": "2026-07-01T10:00:00Z",
        "underlying_price": underlying_price,
        "strike": strike,
        "expiration": expiration,
        "premium": premium,
        "alpha_view": alpha_view,
    }


def _get_row(tables, agent_key, symbol):
    for t in tables:
        if t["key"] == agent_key:
            for r in t["rows"]:
                if r["symbol"] == symbol:
                    return r
    return None


# ---------------------------------------------------------------------------
# _build_dashboard_tables -- covered_call / cash_secured_put alpha fallback
# ---------------------------------------------------------------------------

def test_complete_main_rec_uses_agent_source():
    act = _act("MSFT", "covered_call", strike=185.0, expiration="2026-07-18", premium=1.10)
    tables, _ = _build_dashboard_tables(None, [_sym("MSFT")], [], [act])
    row = _get_row(tables, "covered_call", "MSFT")
    assert row is not None
    assert row["recommendation_source"] == "agent"
    assert row["strike"] == 185.0
    assert row["expiration"] == "2026-07-18"
    assert row["premium"] == 1.10


def test_incomplete_main_strong_alpha_uses_alpha_source():
    alpha_view = {
        "opportunity_strength": "STRONG",
        "alternative": {"strike": 180.0, "expiration": "2026-07-25", "premium": 0.90},
    }
    act = _act("MSFT", "covered_call", alpha_view=alpha_view)
    tables, _ = _build_dashboard_tables(None, [_sym("MSFT")], [], [act])
    row = _get_row(tables, "covered_call", "MSFT")
    assert row is not None
    assert row["recommendation_source"] == "alpha"
    assert row["strike"] == 180.0
    assert row["expiration"] == "2026-07-25"
    assert row["premium"] == 0.90


def test_incomplete_main_moderate_alpha_uses_alpha_source():
    alpha_view = {
        "opportunity_strength": "MODERATE",
        "alternative": {"strike": 175.0, "expiration": "2026-08-01", "premium": 0.75},
    }
    act = _act("AAPL", "cash_secured_put", alpha_view=alpha_view)
    tables, _ = _build_dashboard_tables(
        None, [_sym("AAPL", agent_keys=("cash_secured_put",))], [], [act]
    )
    row = _get_row(tables, "cash_secured_put", "AAPL")
    assert row is not None
    assert row["recommendation_source"] == "alpha"
    assert row["strike"] == 175.0


def test_incomplete_main_none_alpha_strength_keeps_agent_source():
    alpha_view = {
        "opportunity_strength": "NONE",
        "alternative": {"strike": 180.0, "expiration": "2026-07-25", "premium": 0.90},
    }
    act = _act("MSFT", "covered_call", alpha_view=alpha_view)
    tables, _ = _build_dashboard_tables(None, [_sym("MSFT")], [], [act])
    row = _get_row(tables, "covered_call", "MSFT")
    assert row["recommendation_source"] == "agent"
    assert row["strike"] is None


def test_incomplete_main_alpha_incomplete_triplet_skips_fallback():
    # Alpha alternative has no premium -- triplet invalid, fallback must not activate
    alpha_view = {
        "opportunity_strength": "STRONG",
        "alternative": {"strike": 180.0, "expiration": "2026-07-25", "premium": None},
    }
    act = _act("MSFT", "covered_call", alpha_view=alpha_view)
    tables, _ = _build_dashboard_tables(None, [_sym("MSFT")], [], [act])
    row = _get_row(tables, "covered_call", "MSFT")
    assert row["recommendation_source"] == "agent"
    assert row["strike"] is None


def test_no_alpha_view_keeps_agent_source():
    act = _act("MSFT", "covered_call")  # no alpha_view, no strike/exp/prem
    tables, _ = _build_dashboard_tables(None, [_sym("MSFT")], [], [act])
    row = _get_row(tables, "covered_call", "MSFT")
    assert row["recommendation_source"] == "agent"


def test_alpha_strike_used_for_gap_calculation():
    underlying = 170.0
    alpha_view = {
        "opportunity_strength": "STRONG",
        "alternative": {"strike": 160.0, "expiration": "2026-07-25", "premium": 1.00},
    }
    act = _act("MSFT", "covered_call", alpha_view=alpha_view, underlying_price=underlying)
    tables, _ = _build_dashboard_tables(None, [_sym("MSFT")], [], [act])
    row = _get_row(tables, "covered_call", "MSFT")
    assert row["recommendation_source"] == "alpha"
    # strike_pct = (170 - 160) / 160 * 100 = 6.25
    assert abs(row["strike_pct"] - 6.25) < 0.001


def test_complete_main_not_overridden_by_strong_alpha():
    """A complete main recommendation must NOT be replaced even when alpha is STRONG."""
    alpha_view = {
        "opportunity_strength": "STRONG",
        "alternative": {"strike": 180.0, "expiration": "2026-07-25", "premium": 0.90},
    }
    act = _act("MSFT", "covered_call", strike=185.0, expiration="2026-07-18",
               premium=1.10, alpha_view=alpha_view)
    tables, _ = _build_dashboard_tables(None, [_sym("MSFT")], [], [act])
    row = _get_row(tables, "covered_call", "MSFT")
    assert row["recommendation_source"] == "agent"
    assert row["strike"] == 185.0
    assert row["premium"] == 1.10


def test_buy_tracker_row_has_no_recommendation_source():
    """buy_tracker rows must not carry recommendation_source -- only FOLLOWING agents do."""
    act = {
        "id": "SPY-bt",
        "symbol": "SPY",
        "agent_type": "buy_tracker",
        "activity": "WAIT",
        "timestamp": "2026-07-01T10:00:00Z",
        "underlying_price": 540.0,
        "entry_zone": "530-535",
        "technical_triggers": ["RSI oversold"],
    }
    tables, _ = _build_dashboard_tables(
        None, [_sym("SPY", agent_keys=("buy_tracker",))], [], [act]
    )
    row = _get_row(tables, "buy_tracker", "SPY")
    assert row is not None
    assert "recommendation_source" not in row
