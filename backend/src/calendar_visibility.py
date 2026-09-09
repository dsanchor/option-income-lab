"""Events Calendar visibility predicate — single source of truth.

Mirrors the shape of ``options_screener_universe.compute_options_screener_universe``
so both predicates stay easy to compare and neither drifts from the Unified
Watchlist visibility rule (danny-unified-watchlist-contract.md §1.1).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Dict, Iterable, Set

from src.portfolio.watchlist_membership import is_watchlist_member


def compute_calendar_visible_symbols(
    symbol_configs: Iterable[dict],
    shares_by_ticker: Dict[str, Decimal],
) -> Set[str]:
    """Return the set of tickers eligible to appear on/be refreshed by the
    Events Calendar.

    A symbol is included iff:
      1. current portfolio shares (``shares_by_ticker``) > 0, OR
      2. it has at least one active option position, OR
      3. ``is_watchlist_member(doc)`` is True (manually added, any
         watchlist toggle on, or Telegram alerts enabled).

    Excluded only when none of the above hold — i.e. an auto-enrolled,
    fully-sold, non-watchlisted symbol whose only history is a closed
    portfolio position ("historical" leftovers per user request).

    Args:
        symbol_configs: iterable of ``symbol_config`` documents (as
            returned by ``cosmos.list_symbols()``).
        shares_by_ticker: ticker (uppercase) -> current share count
            (``Decimal``), typically derived from one
            ``HoldingsService.compute_holdings()`` call.

    Returns:
        Set of uppercase tickers eligible for calendar display/refresh.
    """
    visible: Set[str] = set()
    for doc in symbol_configs:
        ticker = (doc.get("symbol") or "").strip().upper()
        if not ticker:
            continue

        shares = shares_by_ticker.get(ticker, Decimal("0"))
        if not isinstance(shares, Decimal):
            try:
                shares = Decimal(str(shares))
            except Exception:
                shares = Decimal("0")

        has_active_position = any(
            p.get("status") == "active" for p in (doc.get("positions") or [])
        )

        if shares != 0 or has_active_position or is_watchlist_member(doc):
            visible.add(ticker)

    return visible
