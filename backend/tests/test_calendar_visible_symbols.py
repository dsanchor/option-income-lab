"""Events Calendar visibility predicate — unit tests.

Ref: src/calendar_visibility.py (compute_calendar_visible_symbols)
Ref: danny-unified-watchlist-contract.md §1.1 (shared visibility rule)

User request: the calendar must not show earnings/ex-dividend events for
"historical" symbols — portfolio positions that were fully sold (0 shares)
and are not otherwise being tracked (no watchlist toggle, no active option
position).

Coverage:
  CAL-VIS-1  shares > 0 -> visible, regardless of watchlist/position state.
  CAL-VIS-2  0 shares + explicit watchlist toggle on -> visible.
  CAL-VIS-3  0 shares + active option position -> visible (still relevant).
  CAL-VIS-4  0 shares + auto-enrolled + no watchlist + no active position
             -> excluded (the "historical" leftover the user asked to hide).
  CAL-VIS-5  Manually-added symbol (not auto-enrolled), 0 shares, no
             position -> visible (explicit membership via is_watchlist_member).
  CAL-VIS-6  Symbol absent from shares map (never in holdings_by_ticker)
             defaults to 0 shares, same as CAL-VIS-4.
"""
from __future__ import annotations

from decimal import Decimal

from src.calendar_visibility import compute_calendar_visible_symbols


def _doc(symbol: str, *, auto_enrolled: bool = True, watchlist=None,
         telegram: bool = False, positions=None) -> dict:
    return {
        "symbol": symbol,
        "_auto_enrolled": auto_enrolled,
        "watchlist": watchlist or {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
        "telegram_notifications_enabled": telegram,
        "positions": positions or [],
    }


def test_nonzero_shares_visible_regardless_of_other_flags():
    docs = [_doc("AAPL")]
    shares = {"AAPL": Decimal("10")}
    assert compute_calendar_visible_symbols(docs, shares) == {"AAPL"}


def test_zero_shares_explicit_watchlist_toggle_visible():
    docs = [_doc("MSFT", watchlist={"covered_call": True, "cash_secured_put": False, "buy_tracker": False})]
    shares = {"MSFT": Decimal("0")}
    assert compute_calendar_visible_symbols(docs, shares) == {"MSFT"}


def test_zero_shares_active_position_visible():
    docs = [_doc("TSLA", positions=[{"status": "active", "expiration": "2026-12-18"}])]
    shares = {"TSLA": Decimal("0")}
    assert compute_calendar_visible_symbols(docs, shares) == {"TSLA"}


def test_zero_shares_auto_enrolled_nothing_else_excluded():
    docs = [_doc("ADM")]
    shares = {"ADM": Decimal("0")}
    assert compute_calendar_visible_symbols(docs, shares) == set()


def test_manually_added_zero_shares_no_position_visible():
    docs = [_doc("GOOG", auto_enrolled=False)]
    shares = {"GOOG": Decimal("0")}
    assert compute_calendar_visible_symbols(docs, shares) == {"GOOG"}


def test_symbol_missing_from_shares_map_defaults_to_zero_and_excluded():
    docs = [_doc("NFLX")]
    assert compute_calendar_visible_symbols(docs, {}) == set()
