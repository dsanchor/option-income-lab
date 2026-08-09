"""US market open detection via live options-data probe.

Instead of calendar/rule-based heuristics, we check whether the market
is actually open by probing real bid/ask data from a liquid option
(MSFT ATM call, nearest expiration) via yfinance.  When the market is
closed, yfinance returns zeroed bid/ask — this is a reliable signal.

Result is cached for 5 minutes to avoid excessive API calls.
"""

import logging
import time as _time
from datetime import datetime

import yfinance as yf

logger = logging.getLogger(__name__)

# -- Probe configuration ------------------------------------------------
_PROBE_SYMBOL = "MSFT"
_CACHE_TTL_SECONDS = 300  # 5 minutes

# -- Module-level cache --------------------------------------------------
_cached_result: bool | None = None
_cached_at: float = 0.0


def _probe_market_open() -> bool | None:
    """Probe live bid/ask on a liquid MSFT ATM call option.

    Returns True if bid or ask > 0 (market open), False if zeroed (closed),
    None on error/unavailable data.
    """
    try:
        ticker = yf.Ticker(_PROBE_SYMBOL)
        expirations = ticker.options
        if not expirations:
            logger.warning("Market probe: no option expirations for %s — unknown state", _PROBE_SYMBOL)
            return None

        nearest_exp = expirations[0]
        chain = ticker.option_chain(nearest_exp)
        calls = chain.calls

        if calls is None or calls.empty:
            logger.warning("Market probe: empty call chain for %s %s — unknown state", _PROBE_SYMBOL, nearest_exp)
            return None

        # Pick the ATM call (closest strike to current price)
        current_price = ticker.fast_info.get("lastPrice") or ticker.fast_info.get("last_price")
        if current_price is None:
            # Fallback: use the middle of the chain
            atm_row = calls.iloc[len(calls) // 2]
        else:
            idx = (calls["strike"] - current_price).abs().idxmin()
            atm_row = calls.loc[idx]

        bid = atm_row.get("bid") or 0
        ask = atm_row.get("ask") or 0

        if bid > 0 or ask > 0:
            logger.info(
                "Market probe: %s %s ATM call strike=%.1f bid=%.2f ask=%.2f → OPEN",
                _PROBE_SYMBOL, nearest_exp, atm_row.get("strike", 0), bid, ask,
            )
            return True

        logger.info(
            "Market probe: %s %s ATM call strike=%.1f bid=%.2f ask=%.2f → CLOSED",
            _PROBE_SYMBOL, nearest_exp, atm_row.get("strike", 0), bid, ask,
        )
        return False

    except Exception:
        logger.exception("Market probe failed — unknown state")
        return None


def is_us_market_open(now: datetime | None = None) -> bool | None:
    """Return True (open), False (closed), or None (unknown/error).

    Probes live options bid/ask data.  The ``now`` parameter is accepted
    for backward compatibility but ignored — we trust real market data.

    Results are cached for ~5 minutes to limit API calls.
    """
    global _cached_result, _cached_at

    current_time = _time.monotonic()
    if _cached_at > 0 and (current_time - _cached_at) < _CACHE_TTL_SECONDS:
        state = "OPEN" if _cached_result is True else ("CLOSED" if _cached_result is False else "UNKNOWN")
        logger.debug("Market probe: returning cached result → %s", state)
        return _cached_result

    result = _probe_market_open()
    _cached_result = result
    _cached_at = current_time
    return result
