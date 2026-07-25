"""Deterministic price forecast (volatility cone) — no LLM.

Pure-code forecasting engine. Given a symbol's price history it produces a
probabilistic price *range* (not a point prediction) for four horizons:
1 day, 1 week, 2 weeks and 4 weeks. Ranges are a volatility cone centred on
the current price, widened by realized volatility (HV) scaled by the square
root of the number of trading sessions in each horizon.

A directional ``bias`` in [-1, +1] is derived from the existing TradingView-style
technicals aggregate and reported *separately* — it is NEVER folded into the band
centre, so the band's hit-rate calibrates volatility cleanly.

No LLM is involved. No stored (CosmosDB) data is required: everything is computed
from the price series and the technicals dict, both derived from the live yfinance
history. See ``src/volatility.py`` and ``src/technicals_calculator.py``.
"""

import logging
import math
from typing import Dict, List, Optional, Sequence

from .volatility import TRADING_DAYS, historical_volatility

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Horizon definition — in TRADING SESSIONS (never calendar days).
# Each horizon is a non-overlapping window of session offsets measured from the
# prediction's creation date (session 0). The band width for a horizon is
# evaluated at its END session.
#   1d : session 1
#   1w : sessions 2–5
#   2w : sessions 6–10
#   4w : sessions 11–20   → full lifecycle = 20 sessions
# ──────────────────────────────────────────────────────────────────────────────
HORIZONS: Dict[str, Dict[str, int]] = {
    "1d": {"start_session": 1, "end_session": 1},
    "1w": {"start_session": 2, "end_session": 5},
    "2w": {"start_session": 6, "end_session": 10},
    "4w": {"start_session": 11, "end_session": 20},
}

# Full prediction lifecycle in trading sessions.
LIFECYCLE_SESSIONS = max(h["end_session"] for h in HORIZONS.values())

# Indicator warm-up: MACD(26/9) + ADX(14) need ~35 bars before the first valid
# technicals; ``TechnicalsCalculator.compute_all`` returns empty below 30 bars.
# Used by the backfill to shrink the range for very young tickers.
MIN_HISTORY_BARS = 35

# HV lookback window (trading sessions). Matches the app's volatility default.
HV_WINDOW = 20


# ──────────────────────────────────────────────────────────────────────────────
# Session-window helpers
# ──────────────────────────────────────────────────────────────────────────────

def horizon_for_offset(offset: int) -> Optional[str]:
    """Return the horizon name a given session offset falls into, or ``None``.

    ``offset`` is the number of trading sessions since prediction creation
    (session 0 = creation day, which belongs to no horizon). Offsets beyond the
    lifecycle (> 20) return ``None``.
    """
    for name, bounds in HORIZONS.items():
        if bounds["start_session"] <= offset <= bounds["end_session"]:
            return name
    return None


def is_endpoint(offset: int) -> Optional[str]:
    """Return the horizon whose END session equals ``offset``, else ``None``."""
    for name, bounds in HORIZONS.items():
        if bounds["end_session"] == offset:
            return name
    return None


def has_enough_history(closes: Sequence[float]) -> bool:
    """True when there are enough clean bars to compute valid technicals/HV."""
    if closes is None:
        return False
    clean = [c for c in closes if c is not None]
    return len(clean) >= MIN_HISTORY_BARS


def trading_session_offset(
    session_dates: Sequence[str], created_date: str, as_of: str
) -> int:
    """Number of trading sessions strictly after ``created_date`` up to ``as_of``.

    ``session_dates`` is a list of ``YYYY-MM-DD`` trading-day strings (the actual
    market sessions, e.g. a price-history index — weekends/holidays are naturally
    absent). The offset is the count of sessions ``d`` with
    ``created_date < d <= as_of``. Session 0 (the creation day) therefore returns
    0; the next trading day returns 1, and so on. This is the ground-truth mapping
    from calendar dates to horizon session offsets, robust to holidays.
    """
    if not session_dates or created_date is None or as_of is None:
        return 0
    return sum(1 for d in session_dates if created_date < d <= as_of)


# ──────────────────────────────────────────────────────────────────────────────
# Directional bias (display + directional hit-rate only — never shifts the band)
# ──────────────────────────────────────────────────────────────────────────────

def compute_bias(technicals: Optional[dict]) -> float:
    """Deterministic directional bias in [-1, +1] from the technicals aggregate.

    Reuses ``summary.recommendation.value`` produced by
    ``TechnicalsCalculator`` — already a normalized ``(buy - sell) / total`` over
    all oscillators (RSI, MACD, ADX, Stoch, …) and moving averages. Returns 0.0
    when technicals are missing/empty.
    """
    if not isinstance(technicals, dict):
        return 0.0
    summary = technicals.get("summary") or {}
    rec = summary.get("recommendation")
    if not isinstance(rec, dict):
        return 0.0
    value = rec.get("value")
    if value is None or not _is_finite(value):
        return 0.0
    return _clamp(float(value), -1.0, 1.0)


# ──────────────────────────────────────────────────────────────────────────────
# Forecast (volatility cone)
# ──────────────────────────────────────────────────────────────────────────────

def _band_for_sessions(price: float, hv: float, sessions: int) -> Dict[str, float]:
    """Return ±1σ / ±2σ price bands for a horizon ending at ``sessions``.

    ``sigma = price * hv * sqrt(sessions / 252)`` where ``hv`` is annualized
    realized volatility (decimal, e.g. 0.28).
    """
    sigma = price * hv * math.sqrt(sessions / TRADING_DAYS)
    return {
        "center": round(price, 4),
        "sigma": round(sigma, 4),
        "low1": round(price - sigma, 4),
        "high1": round(price + sigma, 4),
        "low2": round(price - 2 * sigma, 4),
        "high2": round(price + 2 * sigma, 4),
    }


def compute_forecast(
    current_price: Optional[float],
    hv: Optional[float],
    technicals: Optional[dict] = None,
) -> Optional[dict]:
    """Build a full forecast for all horizons, or ``None`` if inputs are unusable.

    Args:
        current_price: latest (adjusted) close.
        hv: annualized realized volatility (decimal). Use
            :func:`compute_forecast_from_closes` to derive it from a series.
        technicals: technicals dict from ``TechnicalsCalculator`` (for bias).

    Returns a dict with ``price_at_creation``, ``hv``, ``bias`` and a ``horizons``
    map. The band centre is always ``current_price`` — the bias is reported
    separately and never shifts it.
    """
    if current_price is None or not _is_finite(current_price) or current_price <= 0:
        return None
    if hv is None or not _is_finite(hv) or hv <= 0:
        return None

    price = float(current_price)
    horizons: Dict[str, dict] = {}
    for name, bounds in HORIZONS.items():
        band = _band_for_sessions(price, hv, bounds["end_session"])
        band["start_session"] = bounds["start_session"]
        band["end_session"] = bounds["end_session"]
        horizons[name] = band

    return {
        "price_at_creation": round(price, 4),
        "hv": round(float(hv), 6),
        "bias": compute_bias(technicals),
        "horizons": horizons,
    }


def compute_forecast_from_closes(
    closes: Sequence[float],
    technicals: Optional[dict] = None,
    *,
    current_price: Optional[float] = None,
    hv_window: int = HV_WINDOW,
) -> Optional[dict]:
    """Convenience wrapper: derive HV (and default price) from a closes series.

    ``current_price`` defaults to the last close. Returns ``None`` when there is
    not enough clean history to compute HV.
    """
    if not has_enough_history(closes):
        return None
    hv = historical_volatility(closes, window=hv_window)
    if hv is None:
        return None
    if current_price is None:
        clean = [c for c in closes if c is not None and _is_finite(c)]
        current_price = clean[-1] if clean else None
    return compute_forecast(current_price, hv, technicals)


# ──────────────────────────────────────────────────────────────────────────────
# Validation (self-checking against realized prices)
# ──────────────────────────────────────────────────────────────────────────────

def price_inside(band: dict, price: float, sigmas: int = 1) -> bool:
    """True when ``price`` lies within the ±``sigmas`` band."""
    if band is None or price is None or not _is_finite(price):
        return False
    low = band["low1"] if sigmas == 1 else band["low2"]
    high = band["high1"] if sigmas == 1 else band["high2"]
    return low <= price <= high


def evaluate_snapshot(
    horizons: dict, price: float, offset: int
) -> Optional[dict]:
    """Classify a realized ``price`` observed ``offset`` sessions after creation.

    Returns ``None`` when the offset falls outside any horizon window (e.g. the
    creation day itself, or beyond the 20-session lifecycle). Otherwise returns
    the bucket assignment plus inside-band flags and whether this is the horizon's
    endpoint session.
    """
    name = horizon_for_offset(offset)
    if name is None or not isinstance(horizons, dict) or name not in horizons:
        return None
    band = horizons[name]
    return {
        "horizon": name,
        "offset": offset,
        "price": round(float(price), 4) if _is_finite(price) else None,
        "inside_1sigma": price_inside(band, price, 1),
        "inside_2sigma": price_inside(band, price, 2),
        "is_endpoint": offset == band.get("end_session"),
    }


def endpoint_direction_correct(band: dict, price: float, bias: float) -> Optional[bool]:
    """Whether the endpoint move agreed with the directional bias.

    Returns ``None`` when bias is neutral (~0) or inputs are missing — a neutral
    bias makes no directional claim and should not be scored.
    """
    if band is None or price is None or not _is_finite(price):
        return None
    if bias is None or abs(bias) < 1e-9:
        return None
    move = price - band.get("center", 0.0)
    if move == 0:
        return None
    return (move > 0) == (bias > 0)


# ──────────────────────────────────────────────────────────────────────────────
# Aggregation (table rows + rolling calibration) — pure, no I/O
# ──────────────────────────────────────────────────────────────────────────────

def summarize_prediction(pred: dict) -> dict:
    """Per-horizon summary for one prediction: path % + endpoint resolution.

    Returns a dict keyed by horizon name with:
      - ``path_count``: snapshots recorded in that horizon window.
      - ``path_pct_1sigma`` / ``path_pct_2sigma``: % of those snapshots inside the
        band (path metric — visual only, biased high). ``None`` when no snapshots.
      - ``endpoint``: the resolved endpoint dict (or ``None`` if not yet reached).
    """
    horizons = pred.get("horizons", {}) if isinstance(pred, dict) else {}
    snapshots = pred.get("snapshots", []) if isinstance(pred, dict) else []
    endpoints = pred.get("endpoints", {}) if isinstance(pred, dict) else {}

    out: Dict[str, dict] = {}
    for name in HORIZONS:
        if name not in horizons:
            continue
        in_h = [s for s in snapshots if s.get("horizon") == name]
        n = len(in_h)
        if n:
            pct1 = round(100.0 * sum(1 for s in in_h if s.get("inside_1sigma")) / n, 1)
            pct2 = round(100.0 * sum(1 for s in in_h if s.get("inside_2sigma")) / n, 1)
        else:
            pct1 = pct2 = None
        out[name] = {
            "path_count": n,
            "path_pct_1sigma": pct1,
            "path_pct_2sigma": pct2,
            "endpoint": endpoints.get(name),
        }
    return out


def aggregate_hit_rate(preds: Sequence[dict]) -> dict:
    """Rolling endpoint calibration across many predictions, per horizon.

    Only *resolved* endpoints count (the real calibration metric). Returns per
    horizon: ``resolved`` (n), ``hit_pct_1sigma`` / ``hit_pct_2sigma`` (should
    trend ~68% / ~95%) and ``direction_pct`` over endpoints that made a
    directional claim. Percentages are ``None`` when there is no data yet.
    """
    out: Dict[str, dict] = {}
    for name in HORIZONS:
        resolved = [
            p.get("endpoints", {}).get(name)
            for p in preds
            if isinstance(p, dict) and p.get("endpoints", {}).get(name)
        ]
        n = len(resolved)
        dir_claims = [e for e in resolved if e.get("direction_correct") is not None]
        out[name] = {
            "resolved": n,
            "hit_pct_1sigma": (
                round(100.0 * sum(1 for e in resolved if e.get("inside_1sigma")) / n, 1)
                if n else None
            ),
            "hit_pct_2sigma": (
                round(100.0 * sum(1 for e in resolved if e.get("inside_2sigma")) / n, 1)
                if n else None
            ),
            "direction_pct": (
                round(
                    100.0 * sum(1 for e in dir_claims if e.get("direction_correct"))
                    / len(dir_claims), 1
                ) if dir_claims else None
            ),
        }
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Small numeric helpers
# ──────────────────────────────────────────────────────────────────────────────

def _is_finite(x) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))
