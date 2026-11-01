"""Dedicated fresh-data timing job and bounded one-time causal reconstruction."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from src.option_timing import (
    MIN_BARS,
    TimingInputError,
    calculate_sides,
    snapshot,
)
from src.option_timing_sessions import (
    NY,
    is_session,
    latest_session,
    observation_cutoff,
    session_close,
    session_open,
)
from src.option_timing_store import OptionTimingStore
from src.options_screener_universe import _resolve_eligibility_mic
from src.portfolio.cosmos_securities import CosmosSecuritiesService
from src.portfolio.provider_symbols import resolve_yfinance_symbol
from src.us_exchange_eligibility import is_us_options_eligible

logger = logging.getLogger(__name__)


def prepare_history(history, now: datetime):
    if history is None or history.empty:
        raise TimingInputError("provider_history_unavailable")
    # Daily index dates identify sessions, not timestamps of a quote observation.
    days = [index.date() for index in history.index]
    allowed = [
        is_session(day) and session_open(day) <= now for day in days
    ]
    selected = history.loc[allowed].copy()
    if selected.empty:
        raise TimingInputError("provider_history_unavailable")
    if selected.index[-1].date() != latest_session(now):
        raise TimingInputError("provider_latest_session_missing")
    return selected


def reconstruct(history, now: datetime, symbol: str = "") -> list[dict]:
    """At most one close per completed session in the last 90 calendar days."""
    cutoff = (now - timedelta(days=90)).date()
    result = []
    for i in range(MIN_BARS - 1, len(history)):
        day = history.index[i].date()
        if day < cutoff or session_close(day) + timedelta(minutes=15) > now:
            continue
        sides = calculate_sides(history.iloc[:i + 1])
        result.append(snapshot(
            symbol, sides, session_close(day), datetime.now(timezone.utc), day.isoformat(),
            False, "reconstructed",
        ))
    return result


async def process_symbol(store, provider, symbol_doc: dict, security_doc: dict | None,
                         now: datetime | None = None) -> dict:
    symbol = symbol_doc["symbol"]
    mic = _resolve_eligibility_mic(
        (security_doc or {}).get("exchange_mic") or symbol_doc.get("exchange")
    )
    security_id = symbol_doc.get("security_id") or f"{mic}:{symbol}"
    if not is_us_options_eligible(mic):
        return {"status": "not_applicable"}
    try:
        yf_symbol = resolve_yfinance_symbol(symbol, mic, security_doc)
        if not yf_symbol:
            raise TimingInputError("provider_symbol_unavailable")
        # get_ohlcv_history always makes a new Yahoo history call, not the quote
        # or options-chain cache. 2y supplies 220 warmup + the full 90d horizon.
        history = await provider.get_ohlcv_history(yf_symbol, period="2y")
        fetched_at = now or datetime.now(timezone.utc)
        history = prepare_history(history, fetched_at)
        sides = calculate_sides(history)
        day = history.index[-1].date()
        as_of, provisional = observation_cutoff(day, fetched_at)
        computed_at = now or datetime.now(timezone.utc)
        point = snapshot(symbol, sides, as_of, computed_at, day.isoformat(), provisional)
        point["source"] = {
            "provider": "yfinance", "provider_symbol": yf_symbol,
            "price_basis": "split_and_dividend_adjusted_daily_ohlcv",
            "fetched_at": fetched_at.isoformat(),
            "timestamp_kind": "retrieval_cutoff" if provisional else "session_close",
            "market_timezone": str(NY),
        }
        previous = store.read(symbol)
        points = [point]
        backfilled = bool((previous or {}).get("backfilled"))
        if not backfilled:
            points.extend(reconstruct(history, fetched_at, symbol))
            backfilled = True
        store.publish(symbol, security_id, points, fetched_at,
                      current=point, backfilled=backfilled)
        return {"status": "available", "snapshot_id": point["snapshot_id"]}
    except Exception as exc:
        reason = str(exc) if isinstance(exc, TimingInputError) else "calculation_or_persistence_failed"
        logger.exception("Option timing failed for %s (%s)", symbol, reason)
        failed_at = now or datetime.now(timezone.utc)
        # Keep the last valid current point, but publish explicit stale/error state.
        # If there has never been a valid point the API returns unavailable/null.
        store.publish(symbol, security_id, [], failed_at, error=reason)
        return {"status": "failed", "reason": reason}


async def run_option_timing(cosmos, provider, now: datetime | None = None) -> dict:
    store = OptionTimingStore(cosmos.container)
    securities = CosmosSecuritiesService(cosmos.container)
    counts = {"available": 0, "failed": 0, "not_applicable": 0}
    for doc in cosmos.list_symbols():
        try:
            sid = doc.get("security_id")
            security = securities.get_security(sid) if sid else None
            result = await process_symbol(store, provider, doc, security, now)
            counts[result["status"]] += 1
        except Exception:
            counts["failed"] += 1
            logger.exception("Option timing symbol processing/publication failed: %s", doc.get("symbol"))
    if counts["failed"]:
        # Existing registry persists last_error on raised jobs; do not record a
        # silent successful scheduler run after any symbol failed.
        raise RuntimeError(f"Option timing failures: {counts}")
    logger.info("Option timing completed: %s", counts)
    return counts
