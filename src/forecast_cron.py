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

from src import dgi_metrics
from src.price_forecast import (
    HV_WINDOW,
    LIFECYCLE_SESSIONS,
    MIN_HISTORY_BARS,
    compute_forecast_from_closes,
    endpoint_direction_correct,
    evaluate_snapshot,
    has_enough_history,
    trading_session_offset,
)
from src.technicals_calculator import TechnicalsCalculator

logger = logging.getLogger(__name__)


def _momentum_from_history(history, current_price: float):
    """Canonical DGI momentum + technicals for an OHLC slice.

    Returns ``(momentum_label, tt_dict)`` — the ``tt_dict`` carries ``adx``/
    ``sma_50``/``sma_200`` used to grade the forecast bias magnitude. Mirrors the
    watchlist enrichment engine (``calculate_technical_timing_score`` +
    ``classify_momentum``) so the forecast reading always agrees with the watchlist
    momentum. Requires High/Low/Close; degrades to ``(None, None)`` on any error
    (the forecast then falls back to the legacy technicals bias).
    """
    try:
        if history is None or history.empty:
            return None, None
        if not all(col in history for col in ("Close", "High", "Low")):
            return None, None
        tt = dgi_metrics.calculate_technical_timing_score(
            history["Close"].values,
            history["High"].values,
            history["Low"].values,
            current_price,
        )
        return dgi_metrics.classify_momentum(tt, current_price), tt
    except Exception:
        return None, None

# Retention window for stored predictions (code-side TTL). ~1 year.
RETENTION_DAYS = 365
# Generous calendar upper bound for a 20-session window (~28 days; +42 is safe
# against holiday stretches). Used only for the open-window date query.
WINDOW_CALENDAR_BOUND_DAYS = 42
# Calendar horizon (~4 weeks) used for the best-effort earnings/ex-div flags.
FLAG_HORIZON_DAYS = 30
# Target DTE for the ATM implied-vol lookup (~4-week horizon, matches lifecycle).
IV_TARGET_DTE = 30
# Number of trailing trading sessions to reconstruct when a symbol is first added,
# so the forecast table/chart have history from day one instead of accumulating it
# one session at a time. Kept small (~5 weeks) — enough to populate the 1d/1w/2w
# horizons and start resolving endpoints.
DEFAULT_BACKFILL_SESSIONS = 25

_TECH = TechnicalsCalculator()


def _load_pf_settings() -> dict:
    """Read price-forecast model settings from Config (safe defaults on error)."""
    defaults = {"band_confidence": 0.50, "vol_source": "iv_hv", "trend_window": 20}
    try:
        from src.config import Config
        cfg = Config()
        return {
            "band_confidence": cfg.price_forecast_band_confidence,
            "vol_source": cfg.price_forecast_vol_source,
            "trend_window": cfg.price_forecast_trend_window,
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Forecast cron: could not read config, using defaults: %s", exc)
        return defaults


async def _fetch_atm_iv(symbol: str, price: float) -> Optional[float]:
    """Best-effort ATM implied vol (annualized decimal) for ~30 DTE. None on failure."""
    try:
        import json
        from src.options_chain_cache import get_options_chain_cache
        from src.volatility import extract_atm_iv

        cache = get_options_chain_cache()
        raw = await cache.get_or_load_async(symbol)
        chain = json.loads(raw) if isinstance(raw, str) else (raw or {})
        result = extract_atm_iv(chain, price, target_dte=IV_TARGET_DTE)
        if result is None:
            return None
        iv, _dte = result
        return iv if iv and iv > 0 else None
    except Exception as exc:
        logger.info("Forecast cron: IV lookup failed for %s: %s", symbol, exc)
        return None


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

    pf_settings = _load_pf_settings()
    logger.info(
        "Forecast cron settings: confidence=%s vol_source=%s trend_window=%s",
        pf_settings["band_confidence"], pf_settings["vol_source"],
        pf_settings["trend_window"],
    )

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
            outcome = await _process_symbol(
                cosmos, yf_provider, symbol, run_date, cutoff, pf_settings
            )
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


async def _process_symbol(cosmos, yf_provider, symbol, run_date, cutoff, pf_settings=None) -> dict:
    """Process a single symbol. Returns per-stage counts."""
    if pf_settings is None:
        pf_settings = {"band_confidence": 0.50, "vol_source": "iv_hv", "trend_window": 20}
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
                    band, latest_price, pred.get("bias", 0.0),
                    (pred.get("trend") or {}).get("slope"),
                ),
            }
            out["resolved"] += 1

        if offset >= LIFECYCLE_SESSIONS:
            pred["status"] = "closed"

        cosmos.write_price_forecast(symbol, pred)

    # ── Generate today's prediction ────────────────────────────────────
    technicals = _TECH.compute_all(history)
    momentum, momentum_tt = _momentum_from_history(history, latest_price)

    vol_source = pf_settings.get("vol_source", "iv_hv")
    iv = None
    if vol_source in ("iv", "iv_hv"):
        iv = await _fetch_atm_iv(symbol, latest_price)

    forecast = compute_forecast_from_closes(
        closes_series.tolist(),
        technicals,
        current_price=latest_price,
        hv_window=HV_WINDOW,
        confidence=pf_settings.get("band_confidence", 0.50),
        vol_source=vol_source,
        iv=iv,
        trend_window=pf_settings.get("trend_window", 20),
        momentum=momentum,
        momentum_technicals=momentum_tt,
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


def _build_backfill_prediction(cosmos, symbol, history, session_dates, closes,
                               t_idx, *, confidence, vol_source, trend_window):
    """Reconstruct one point-in-time prediction created at session index ``t_idx``.

    Uses only history up to and including ``t_idx`` (no look-ahead), then validates
    it forward against the real sessions that followed (which we legitimately have
    now). Returns the prediction doc, or ``None`` when the forecast can't be built.

    This is the exact same ``compute_forecast_from_closes`` / technicals / vol code
    path as the live daily cron — the only difference is the as-of slice. IV is not
    available historically, so ``vol_source`` is limited to ``hv``/``ewma``.
    """
    created_date = session_dates[t_idx]
    closes_upto = closes[: t_idx + 1]
    hist_upto = history.iloc[: t_idx + 1]

    technicals = _TECH.compute_all(hist_upto)
    momentum, momentum_tt = _momentum_from_history(hist_upto, float(closes[t_idx]))
    forecast = compute_forecast_from_closes(
        closes_upto,
        technicals,
        current_price=float(closes[t_idx]),
        hv_window=HV_WINDOW,
        confidence=confidence,
        vol_source=vol_source,
        trend_window=trend_window,
        momentum=momentum,
        momentum_technicals=momentum_tt,
    )
    if forecast is None:
        return None

    end_date = (
        datetime.strptime(created_date, "%Y-%m-%d")
        + timedelta(days=WINDOW_CALENDAR_BOUND_DAYS)
    ).strftime("%Y-%m-%d")
    forecast.update({
        "created_date": created_date,
        "start_date": created_date,
        "end_date": end_date,
        "status": "open",
        "snapshots": [],
        "endpoints": {},
        "flags": _event_flags(cosmos, symbol, created_date),
        "backfilled": True,
    })

    # Validate forward against the real subsequent sessions.
    last_idx = len(session_dates) - 1
    for u_idx in range(t_idx + 1, last_idx + 1):
        as_of = session_dates[u_idx]
        offset = trading_session_offset(session_dates, created_date, as_of)
        if offset > LIFECYCLE_SESSIONS:
            break
        price = float(closes[u_idx])
        result = evaluate_snapshot(forecast["horizons"], price, offset)
        if result is None:
            continue
        forecast["snapshots"].append({"date": as_of, **result})
        if result["is_endpoint"]:
            band = forecast["horizons"][result["horizon"]]
            forecast["endpoints"][result["horizon"]] = {
                "date": as_of,
                "price": result["price"],
                "inside_1sigma": result["inside_1sigma"],
                "inside_2sigma": result["inside_2sigma"],
                "direction_correct": endpoint_direction_correct(
                    band, price, forecast.get("bias", 0.0),
                    (forecast.get("trend") or {}).get("slope"),
                ),
            }
        if offset >= LIFECYCLE_SESSIONS:
            forecast["status"] = "closed"

    forecast["snapshots"].sort(key=lambda s: s.get("date", ""))
    return forecast


async def backfill_symbol_forecasts(cosmos, yf_provider, symbol,
                                    *, sessions=DEFAULT_BACKFILL_SESSIONS,
                                    pf_settings=None, force=False) -> dict:
    """Reconstruct the last ``sessions`` trading-session forecasts for one symbol.

    Intended to run when a symbol is first added so the forecast table/chart have
    history immediately (matching what the daily cron would have accumulated). Fully
    deterministic and point-in-time (no look-ahead). Existing predictions are kept
    unless ``force`` is set. Returns per-stage counts.

    Volatility is realized-only (``hv``/``ewma``) because implied vol cannot be
    reconstructed historically; ``iv``/``iv_hv`` settings degrade to ``hv``.
    """
    out = {"created": 0, "skipped": 0, "reason": "ok"}
    if cosmos is None or yf_provider is None:
        out["reason"] = "unavailable"
        return out

    if pf_settings is None:
        pf_settings = _load_pf_settings()
    confidence = pf_settings.get("band_confidence", 0.50)
    trend_window = max(5, min(120, int(pf_settings.get("trend_window", 20))))
    vol_source = (pf_settings.get("vol_source") or "hv").lower()
    if vol_source not in ("hv", "ewma"):
        vol_source = "hv"  # IV unavailable historically
    sessions = max(1, int(sessions))

    try:
        history = await yf_provider.get_ohlcv_history(symbol, period="1y")
    except Exception as exc:
        logger.info("Forecast backfill: history fetch failed for %s: %s", symbol, exc)
        out["reason"] = "no_history"
        return out
    if history is None or history.empty or "Close" not in history:
        out["reason"] = "no_history"
        return out

    closes_series = history["Close"].dropna()
    history = history.loc[closes_series.index]  # align indices
    closes = closes_series.tolist()
    session_dates = [d.strftime("%Y-%m-%d") for d in closes_series.index]

    if not has_enough_history(closes) or len(closes) <= MIN_HISTORY_BARS:
        out["reason"] = "insufficient_history"
        return out

    # Candidate creation sessions: the last ``sessions`` sessions that still have
    # >= MIN_HISTORY_BARS of warm-up history behind them.
    earliest_feasible_idx = MIN_HISTORY_BARS
    start_idx = max(earliest_feasible_idx, len(session_dates) - sessions)

    for t_idx in range(start_idx, len(session_dates)):
        created_date = session_dates[t_idx]
        doc_id = f"{symbol}_forecast_{created_date}"
        if not force and cosmos.get_price_forecast(symbol, doc_id) is not None:
            out["skipped"] += 1
            continue
        pred = _build_backfill_prediction(
            cosmos, symbol, history, session_dates, closes, t_idx,
            confidence=confidence, vol_source=vol_source, trend_window=trend_window,
        )
        if pred is None:
            out["skipped"] += 1
            continue
        if cosmos.write_price_forecast(symbol, pred):
            out["created"] += 1
        else:
            out["skipped"] += 1

    logger.info(
        "Forecast backfill for %s: %d created, %d skipped (%s)",
        symbol, out["created"], out["skipped"], out["reason"],
    )
    return out
