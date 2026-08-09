"""
Options math utilities.

Shared mathematical helpers for option pricing, P&L, and data quality.
"""


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
