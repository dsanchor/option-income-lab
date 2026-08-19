"""
Options math utilities.

Shared mathematical helpers for option pricing, P&L, and data quality.
"""

import math
from numbers import Real


def executable_buyback_ask(ask) -> float | None:
    """Return an executable buy-to-close ask, or None when unavailable."""
    if isinstance(ask, bool) or not isinstance(ask, Real):
        return None
    value = float(ask)
    return value if math.isfinite(value) and value > 0 else None


def robust_mid(bid, ask, last=0.0):
    """Fair option mid that resists one-sided / stale-wide illiquid quotes.

    yfinance frequently returns garbage one-sided quotes for illiquid
    options (e.g. bid=0, ask=3.9 on a deep-OTM put truly worth ~0.15),
    which a naive (bid+ask)/2 turns into an absurd 1.95 mark and wrecks
    downstream P&L. lastPrice is intentionally NOT trusted (it is stale
    for illiquid names). Rules:
      * Sane two-sided quote            -> (bid+ask)/2
      * Real bid but implausibly wide   -> anchor to bid
        ask (stale-high)
      * No bid at all (bid<=0)          -> option is bid-less; mark
                                           conservatively near 0, never ask/2
      * Nothing usable                  -> 0.0

    Args:
        bid: Current bid price (or 0/None/NaN if missing)
        ask: Current ask price (or 0/None/NaN if missing)
        last: Last traded price (not currently used; kept for future heuristics)

    Returns:
        float: Robust mid-price rounded to 4 decimals
    """
    bid = bid if (bid and bid > 0) else 0.0
    ask = ask if (ask and ask > 0) else 0.0
    if bid > 0 and ask > 0:
        if ask > bid * 8 + 0.20:   # implausibly wide -> stale/garbage ask
            return round(bid, 4)
        return round((bid + ask) / 2, 4)
    if bid > 0:
        return round(bid, 4)
    if ask > 0:
        # no buyers -> near-worthless to the holder; hard cap so a
        # stale-high ask cannot inflate the mark (only bites on garbage)
        return round(min(ask, 0.10), 4)
    return 0.0


def robust_mid_optional(bid, ask, last=None) -> float | None:
    """Like ``robust_mid``, but returns ``None`` instead of a fabricated
    ``0.0`` when neither bid nor ask is usable (Z3, danny-zero-free-agent-
    option-chains.md): a mid with no real bid/ask input is our own
    manufactured artifact, not a market fact, and must never be served as
    a usable ``$0.00`` mark.

    Delegates to ``robust_mid`` for the actual computation whenever at
    least one side is usable, so the numeric result is byte-identical to
    ``robust_mid`` on every path that used to return a real price — only
    the "nothing usable" fallback differs (``None`` instead of ``0.0``).
    ``robust_mid`` itself is intentionally left unchanged so any existing
    caller keeps its current (0.0-on-nothing-usable) behavior.

    Args:
        bid: Current bid price (or 0/None/NaN if missing)
        ask: Current ask price (or 0/None/NaN if missing)
        last: Last traded price (unused; kept for signature parity)

    Returns:
        float | None: Robust mid-price rounded to 4 decimals, or None when
        neither bid nor ask is a usable (finite, positive) quote.
    """
    bid_usable = bool(bid) and bid > 0
    ask_usable = bool(ask) and ask > 0
    if not bid_usable and not ask_usable:
        return None
    return robust_mid(bid, ask, last if last is not None else 0.0)
