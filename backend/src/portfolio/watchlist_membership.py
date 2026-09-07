"""Explicit Watchlist membership predicate — single source of truth.

Relocated (pure-function extraction, behavior-preserving) from
``backend/web/app.py::_is_watchlist_member`` so non-web layers (e.g.
``src/main.py``'s scheduler) can reuse the exact same predicate without
importing the web module.

Contract: danny-unified-watchlist-contract.md §1.2
Reused by: danny-options-screener-universe-contract.md §2.1
"""
from __future__ import annotations


def is_watchlist_member(config: dict) -> bool:
    """Return True if the symbol has explicit watchlist membership.

    Explicit membership = manually added OR any watchlist toggle on OR
    telegram enabled.  An auto-enrolled symbol with no interactions is
    purely historical (not explicit).

    Contract: danny-unified-watchlist-contract.md §1.2
    """
    if not config.get("_auto_enrolled", False):
        return True  # manually added
    wl = config.get("watchlist") or {}
    if wl.get("covered_call") or wl.get("cash_secured_put") or wl.get("buy_tracker"):
        return True
    if config.get("telegram_notifications_enabled", False):
        return True
    return False
