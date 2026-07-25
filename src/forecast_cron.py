"""Daily deterministic price-forecast cron job — no LLM.

On each run, for every tracked symbol:
  1. Fetch the dated OHLCV history (single network call).
  2. Stale/holiday guard: skip if the latest session is not today's session.
  3. VALIDATE every open prediction: append today's realized price to its correct
     horizon window, flag inside/outside ±1σ/±2σ, resolve endpoints, close when the
     20-session lifecycle is complete.
  4. GENERATE today's new prediction (volatility cone + directional bias) using the
     same code as the app (``technicals_calculator`` + ``volatility``).
  5. PRUNE predictions older than the retention window (code-side TTL).

Everything is deterministic and derived from the price series — no CosmosDB reads
feed the forecast math (only optional earnings/ex-div flags are looked up).
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.price_forecast import (
    HV_WINDOW,
    LIFECYCLE_SESSIONS,
    compute_forecast_from_closes,
    endpoint_direction_correct,
    evaluate_snapshot,
    has_enough_history,
    trading_session_offset,
)
from src.technicals_calculator import TechnicalsCalculator

logger = logging.getLogger(__name__)

# Retention window for stored predictions (code-side TTL). ~1 year.
RETENTION_DAYS = 365
# Generous calendar upper bound for a 20-session window (~28 days; +42 is safe
# against holiday stretches). Used only for the open-window date query.
WINDOW_CALENDAR_BOUND_DAYS = 42
# Calendar horizon (~4 weeks) used for the best-effort earnings/ex-div flags.
FLAG_HORIZON_DAYS = 30

_TECH = TechnicalsCalculator()


async def run_forecast_cron(cosmos, yf_provider) -> dict:
    """Run the price-forecast job for all tracked symbols.

    Returns a summary dict with per-stage counts.
    """
    if cosmos is None:
        logger.warning("Forecast cron: CosmosDB unavailable — skipping")
        return {"status": "skipped", "reason": "cosmos_unavailable"}
    if yf_provider is None:
        logger.warning("Forecast cron: YFinance provider unavailable — skipping")
        return {"status": "skipped", "reason": "yf_provider_unavailable"}

    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    ).strftime("%Y-%m-%d")

    created = 0
    validated = 0
    resolved = 0
    skipped = 0
    pruned = 0
    failed = 0

    for sym_doc in cosmos.list_symbols():
        symbol = sym_doc.get("symbol", "")
        if not symbol:
            continue
        try:
            outcome = await _process_symbol(cosmos, yf_provider, symbol, run_date, cutoff)
            created += outcome["created"]
            validated += outcome["validated"]
            resolved += outcome["resolved"]
            pruned += outcome["pruned"]
            skipped += outcome["skipped"]
        except Exception as exc:
            logger.warning("Forecast cron: error processing %s: %s", symbol, exc)
            failed += 1

    summary = {
        "status": "completed",
        "created": created,
        "validated": validated,
        "resolved": resolved,
        "pruned": pruned,
        "skipped": skipped,
        "failed": failed,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    logger.info(
        "Forecast cron completed: %d created, %d validated, %d resolved, "
        "%d pruned, %d skipped, %d failed",
        created, validated, resolved, pruned, skipped, failed,
    )
    return summary


async def _process_symbol(cosmos, yf_provider, symbol, run_date, cutoff) -> dict:
    """Process a single symbol. Returns per-stage counts."""
    out = {"created": 0, "validated": 0, "resolved": 0, "pruned": 0, "skipped": 0}

    history = await yf_provider.get_ohlcv_history(symbol, period="1y")
    if history is None or history.empty or "Close" not in history:
        out["skipped"] = 1
        return out

    closes_series = history["Close"].dropna()
    if not has_enough_history(closes_series.tolist()):
        out["skipped"] = 1
        return out

    session_dates = [d.strftime("%Y-%m-%d") for d in closes_series.index]
    today = session_dates[-1]
    latest_price = float(closes_series.iloc[-1])

    # Stale/holiday guard: if the latest available session predates the run date,
    # the market had no new close today (holiday) — do not record a snapshot or
    # regenerate. Weekends are already excluded by the cron schedule.
    if today < run_date:
        logger.info(
            "Forecast cron: %s latest session %s < run date %s — no new close, skipping",
            symbol, today, run_date,
        )
        out["skipped"] = 1
        return out

    # ── Validate open predictions ──────────────────────────────────────
    for pred in cosmos.get_open_price_forecasts(symbol, today):
        offset = trading_session_offset(session_dates, pred.get("created_date", ""), today)
        result = evaluate_snapshot(pred.get("horizons", {}), latest_price, offset)
        if result is None:
            continue

        snapshots = [s for s in pred.get("snapshots", []) if s.get("date") != today]
        snapshots.append({"date": today, **result})
        pred["snapshots"] = sorted(snapshots, key=lambda s: s.get("date", ""))
        out["validated"] += 1

        if result["is_endpoint"]:
            band = pred["horizons"][result["horizon"]]
            pred.setdefault("endpoints", {})[result["horizon"]] = {
                "date": today,
                "price": result["price"],
                "inside_1sigma": result["inside_1sigma"],
                "inside_2sigma": result["inside_2sigma"],
                "direction_correct": endpoint_direction_correct(
                    band, latest_price, pred.get("bias", 0.0)
                ),
            }
            out["resolved"] += 1

        if offset >= LIFECYCLE_SESSIONS:
            pred["status"] = "closed"

        cosmos.write_price_forecast(symbol, pred)

    # ── Generate today's prediction ────────────────────────────────────
    technicals = _TECH.compute_all(history)
    forecast = compute_forecast_from_closes(
        closes_series.tolist(),
        technicals,
        current_price=latest_price,
        hv_window=HV_WINDOW,
    )
    if forecast is None:
        out["skipped"] = 1
    else:
        end_date = (
            datetime.strptime(today, "%Y-%m-%d")
            + timedelta(days=WINDOW_CALENDAR_BOUND_DAYS)
        ).strftime("%Y-%m-%d")
        forecast.update({
            "created_date": today,
            "start_date": today,
            "end_date": end_date,
            "status": "open",
            "snapshots": [],
            "endpoints": {},
            "flags": _event_flags(cosmos, symbol, today),
        })
        if cosmos.write_price_forecast(symbol, forecast):
            out["created"] = 1

    # ── Prune (code-side TTL) ──────────────────────────────────────────
    out["pruned"] = cosmos.prune_price_forecasts(symbol, cutoff)
    return out


def _event_flags(cosmos, symbol, today) -> dict:
    """Best-effort earnings/ex-div-in-window flags. Degrade gracefully to False."""
    horizon_date = (
        datetime.strptime(today, "%Y-%m-%d") + timedelta(days=FLAG_HORIZON_DAYS)
    ).strftime("%Y-%m-%d")

    def _in_window(event_date: Optional[str]) -> bool:
        return bool(event_date and today < event_date <= horizon_date)

    try:
        earnings = cosmos.get_next_earnings_date(symbol)
    except Exception:
        earnings = None
    try:
        exdiv = cosmos.get_next_calendar_event_date(symbol, "ex_dividend")
    except Exception:
        exdiv = None

    return {
        "earnings_in_window": _in_window(earnings),
        "exdiv_in_window": _in_window(exdiv),
    }
