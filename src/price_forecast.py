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

from .volatility import TRADING_DAYS, ewma_volatility, historical_volatility

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Confidence → z-multiplier (two-sided central interval of a normal dist).
# The band half-width is ``z * unit_sigma`` where ``unit_sigma`` is the √t
# volatility scale. 0.68 keeps the classic ±1σ meaning. The PRIMARY (inner) band
# is configurable; the SECONDARY (outer) band is a wider reference for context.
# ──────────────────────────────────────────────────────────────────────────────
_CONF_Z = {
    0.50: 0.6745,
    0.68: 1.0000,   # classic ±1σ (68.27%) — kept as the engine default
    0.80: 1.2816,
    0.90: 1.6449,
    0.95: 1.9600,
    0.99: 2.5758,
}
DEFAULT_CONFIDENCE = 0.68
OUTER_CONFIDENCE = 0.95

# Minimum |bias| to treat the directional signal as a real (high-conviction)
# claim. Below this the technicals aggregate is treated as noise and no
# directional call is scored. Also used by the trend-agreement filter.
BIAS_CONVICTION_THRESHOLD = 0.15


def z_for_confidence(confidence: Optional[float]) -> float:
    """Return the z-multiplier for a central confidence level (nearest known)."""
    if confidence is None or not _is_finite(confidence):
        confidence = DEFAULT_CONFIDENCE
    if confidence in _CONF_Z:
        return _CONF_Z[confidence]
    # Nearest supported level (keeps behaviour predictable & deterministic).
    key = min(_CONF_Z.keys(), key=lambda k: abs(k - float(confidence)))
    return _CONF_Z[key]


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


def compute_reading(
    bias: Optional[float],
    trend_slope: Optional[float],
    *,
    threshold: float = BIAS_CONVICTION_THRESHOLD,
) -> dict:
    """Combine directional bias (technicals) + trend slope into a trade reading.

    ``bias`` = oscillator/MA aggregate in [-1,+1] (momentum + overbought/oversold
    state). ``trend_slope`` = linear-regression drift ($/session) — pure price
    direction. They measure different things and their (dis)agreement is the
    signal:

      trend ↑ & bias ↑  → Bullish   (aligned, high conviction) → favours CSP
      trend ↓ & bias ↓  → Bearish   (aligned, high conviction) → favours CC
      trend ↑ & bias “not up”   → Topping   (uptrend, momentum fading) → time CC
      trend ↓ & bias “not down” → Bottoming (downtrend, momentum turning) → time CSP
      otherwise → Neutral (no edge)

    Returns a display-only dict (``code``/``label``/``icon``/``conviction``/
    ``agree``/``csp``/``cc``/``reason``). Never shifts the band. Deterministic.
    """
    b = float(bias) if _is_finite(bias) else 0.0
    s = float(trend_slope) if _is_finite(trend_slope) else 0.0
    bias_dir = 1 if b >= threshold else (-1 if b <= -threshold else 0)
    trend_dir = 1 if s > 0 else (-1 if s < 0 else 0)
    agree = bias_dir != 0 and bias_dir == trend_dir

    if agree and bias_dir > 0:
        code, label, icon, conv = "bull", "Bullish", "▲▲", "high"
        csp, cc = "favorable", "avoid"
        reason = ("Uptrend and momentum agree (high conviction). "
                  "Favorable to SELL CSP: you expect the price to hold or rise, "
                  "so the put expires worthless and you keep the premium. "
                  "Avoid selling CC now: risk of assignment that caps the upside.")
    elif agree and bias_dir < 0:
        code, label, icon, conv = "bear", "Bearish", "▼▼", "high"
        csp, cc = "avoid", "favorable"
        reason = ("Downtrend and momentum agree (high conviction). "
                  "Favorable to SELL CC: you expect the price to fall or not rise, "
                  "so the call expires worthless. Avoid CSP now: risk of downside "
                  "assignment while the price keeps falling.")
    elif trend_dir > 0 and bias_dir <= 0:
        code, label, icon, conv = "top", "Topping", "▲▽", "low"
        csp, cc = "caution", "favorable"
        reason = ("Uptrend but momentum is not following (possible overbought / "
                  "exhaustion). Good moment to SELL CC into the strength "
                  "(rich premium, possible top). CSP with caution: a pullback may "
                  "come. Low conviction — diverging signals.")
    elif trend_dir < 0 and bias_dir >= 0:
        code, label, icon, conv = "bottom", "Bottoming", "▽▲", "low"
        csp, cc = "favorable", "caution"
        reason = ("Downtrend but momentum is turning up (possible oversold / "
                  "bounce). Good moment to SELL CSP into the weakness "
                  "(rich premium, possible bottom). CC with caution: it may bounce. "
                  "Low conviction — diverging signals.")
    else:
        code, label, icon, conv = "neutral", "Neutral", "·", "none"
        csp, cc = "neutral", "neutral"
        reason = ("No clear trend or signal too weak. Range-bound market: "
                  "premium decays in your favor on both sides, but with no "
                  "directional edge. Low conviction — no clear bet.")

    return {
        "code": code,
        "label": label,
        "icon": icon,
        "conviction": conv,
        "agree": agree,
        "bias_dir": bias_dir,
        "trend_dir": trend_dir,
        "csp": csp,
        "cc": cc,
        "reason": reason,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Canonical momentum → trade reading
# ──────────────────────────────────────────────────────────────────────────────
# The momentum string is produced by the DGI screener engine
# (``dgi_metrics.classify_momentum``: SMA50/200 + ADX + RSI) and is the single
# directional source of truth shared by the watchlist enrichment and the price
# forecast, so both surfaces always show the same call. This maps each momentum
# state to the forecast's display ``reading`` and a directional ``bias`` sign used
# only for the directional hit-rate (never shifts the band).
_MOMENTUM_READING = {
    "Bullish": {
        "code": "bull", "icon": "▲▲", "conviction": "high",
        "csp": "favorable", "cc": "avoid", "bias": 1.0,
        "reason": ("SMA50 > SMA200, price above both — uptrend. "
                   "Sell Puts: favorable (price expected to hold or rise). "
                   "Sell Calls: assignment risk that caps the upside."),
    },
    "Bullish (overextended)": {
        "code": "top", "icon": "▲▽", "conviction": "low",
        "csp": "caution", "cc": "favorable", "bias": 0.6,
        "reason": ("Bullish BUT RSI > 70 (overbought) — may reverse soon. "
                   "Sell Calls: good timing into the strength. "
                   "Sell Puts: caution, a pullback may come."),
    },
    "Weakening": {
        "code": "top", "icon": "▲▽", "conviction": "low",
        "csp": "caution", "cc": "favorable", "bias": 0.0,
        "reason": ("SMA50 > SMA200 but price dropped below SMA50 — losing steam. "
                   "Sell Calls: ideal. Sell Puts: caution."),
    },
    "Neutral": {
        "code": "neutral", "icon": "·", "conviction": "none",
        "csp": "neutral", "cc": "neutral", "bias": 0.0,
        "reason": ("ADX < 20 or no clear trend — range-bound. Premium decays in "
                   "your favor on both sides, but with no directional edge."),
    },
    "Bearish": {
        "code": "bear", "icon": "▼▼", "conviction": "high",
        "csp": "avoid", "cc": "favorable", "bias": -1.0,
        "reason": ("SMA50 < SMA200, price below both — downtrend. "
                   "Sell Calls: favorable. "
                   "Sell Puts: assignment risk to the downside."),
    },
    "Bearish (oversold)": {
        "code": "bottom", "icon": "▽▲", "conviction": "low",
        "csp": "favorable", "cc": "caution", "bias": -0.6,
        "reason": ("Bearish BUT RSI < 30 (oversold) — may bounce soon. "
                   "Sell Puts: good timing into the weakness. "
                   "Sell Calls: caution, it may bounce."),
    },
    "Unknown": {
        "code": "neutral", "icon": "·", "conviction": "none",
        "csp": "neutral", "cc": "neutral", "bias": 0.0,
        "reason": "Insufficient data for a directional read.",
    },
}


def reading_from_momentum(momentum: Optional[str]) -> dict:
    """Build the forecast display ``reading`` from a canonical momentum label.

    Returns a dict with the same shape as :func:`compute_reading`
    (``code``/``label``/``icon``/``conviction``/``momentum``/``csp``/``cc``/
    ``reason``) so every consumer renders identically. Unknown/absent momentum
    degrades to the neutral "Unknown" reading.
    """
    key = momentum if momentum in _MOMENTUM_READING else "Unknown"
    spec = _MOMENTUM_READING[key]
    return {
        "code": spec["code"],
        "label": key,
        "icon": spec["icon"],
        "conviction": spec["conviction"],
        "momentum": key,
        "csp": spec["csp"],
        "cc": spec["cc"],
        "reason": spec["reason"],
    }


def momentum_bias(momentum: Optional[str]) -> float:
    """Directional bias sign in [-1, +1] implied by a momentum label.

    Used only for the directional hit-rate (``endpoint_direction_correct``) and the
    Bias column — never shifts the band. Non-directional states (Weakening, Neutral,
    Unknown) return 0.0 so they make no directional claim.
    """
    spec = _MOMENTUM_READING.get(momentum or "")
    return float(spec["bias"]) if spec else 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Forecast (volatility cone)
# ──────────────────────────────────────────────────────────────────────────────

def _band_for_sessions(
    price: float,
    vol: float,
    sessions: int,
    *,
    z1: float = 1.0,
    z2: float = 1.96,
    trend_slope: float = 0.0,
) -> Dict[str, float]:
    """Return primary/secondary price bands for a horizon ending at ``sessions``.

    ``unit_sigma = price * vol * sqrt(sessions / 252)`` where ``vol`` is annualized
    volatility (decimal, e.g. 0.28). The half-widths are ``z1`` (primary, inner,
    configurable confidence) and ``z2`` (secondary, outer reference) times
    ``unit_sigma``. The band centre stays at ``price`` (honest random-walk centre);
    ``trend_end`` is the projected linear-trend value at the horizon end session,
    drawn as an overlay only — it never shifts the band.
    """
    sigma = price * vol * math.sqrt(sessions / TRADING_DAYS)
    return {
        "center": round(price, 4),
        "sigma": round(sigma, 4),
        "z1": round(z1, 4),
        "z2": round(z2, 4),
        "low1": round(price - z1 * sigma, 4),
        "high1": round(price + z1 * sigma, 4),
        "low2": round(price - z2 * sigma, 4),
        "high2": round(price + z2 * sigma, 4),
        "trend_end": round(price + trend_slope * sessions, 4),
    }


def _median(values: Sequence[float]) -> float:
    """Median of a non-empty sequence (no external deps)."""
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return float(s[mid])
    return (float(s[mid - 1]) + float(s[mid])) / 2.0


def linear_trend(
    closes: Sequence[float],
    window: int = 20,
) -> Optional[dict]:
    """Robust, confidence-gated linear trend over the last ``window`` closes.

    Deterministic. Designed so the projected trend *stops pointing* when the data
    doesn't actually trend (the common "it never hits" failure of naive OLS
    extrapolation):

      1. **Theil–Sen slope** (median of pairwise slopes) instead of ordinary least
         squares — resistant to single-day gaps/earnings spikes.
      2. **R² quality gate**: the coefficient of determination of the robust fit,
         i.e. how much of the price variance the straight line explains.
      3. **Slope shrinkage**: the slope actually used for projection is shrunk
         toward zero by R² (``slope = slope_raw * r2``). A strong linear trend
         projects nearly in full; a noisy series flattens out. Sign is preserved,
         so the directional reading stays consistent.

    Returns ``{slope, slope_raw, r2, quality, resid_std, window}`` or ``None``.
    ``slope`` is the shrunk per-session drift used everywhere downstream
    (``trend_end``, chart overlay, directional reading). Never raises.
    """
    if closes is None:
        return None
    clean = [float(c) for c in closes if c is not None and _is_finite(c) and float(c) > 0]
    if window and window > 0:
        clean = clean[-window:]
    n = len(clean)
    if n < 3:
        return None

    xs = list(range(n))
    # ── Theil–Sen robust slope: median of pairwise slopes (n small → O(n²) fine) ──
    pair_slopes = [
        (clean[j] - clean[i]) / (j - i)
        for i in range(n)
        for j in range(i + 1, n)
    ]
    if not pair_slopes:
        return None
    slope_raw = _median(pair_slopes)
    intercept = _median([clean[i] - slope_raw * xs[i] for i in range(n)])

    # ── R² of the robust fit + residual dispersion ──
    mean_y = sum(clean) / n
    ss_tot = sum((y - mean_y) ** 2 for y in clean)
    residuals = [clean[i] - (intercept + slope_raw * xs[i]) for i in range(n)]
    ss_res = sum(r * r for r in residuals)
    if ss_tot <= 0:
        r2 = 0.0
    else:
        r2 = 1.0 - (ss_res / ss_tot)
    r2 = _clamp(r2, 0.0, 1.0)  # negative R² (fit worse than mean) → 0 = no trend
    resid_std = math.sqrt(ss_res / (n - 2)) if n > 2 and ss_res > 0 else 0.0

    # ── Shrink the projected slope by fit quality (confidence gate + damping) ──
    slope_eff = slope_raw * r2

    if r2 >= 0.5:
        quality = "strong"
    elif r2 >= 0.2:
        quality = "moderate"
    else:
        quality = "weak"

    return {
        "slope": round(slope_eff, 6),
        "slope_raw": round(slope_raw, 6),
        "r2": round(r2, 4),
        "quality": quality,
        "resid_std": round(resid_std, 4),
        "window": n,
    }


def compute_forecast(
    current_price: Optional[float],
    vol: Optional[float],
    technicals: Optional[dict] = None,
    *,
    confidence: float = DEFAULT_CONFIDENCE,
    trend: Optional[dict] = None,
    vol_source: str = "hv",
    momentum: Optional[str] = None,
) -> Optional[dict]:
    """Build a full forecast for all horizons, or ``None`` if inputs are unusable.

    Args:
        current_price: latest (adjusted) close.
        vol: annualized volatility (decimal) — HV, EWMA or IV. Use
            :func:`compute_forecast_from_closes` to derive it from a series.
        technicals: technicals dict from ``TechnicalsCalculator`` (legacy bias
            fallback, only used when ``momentum`` is not supplied).
        confidence: central confidence level for the PRIMARY band (default 0.68 =
            classic ±1σ). Drives ``z1``; the outer band uses ``OUTER_CONFIDENCE``.
        trend: optional ``linear_trend`` dict — its ``slope`` projects a trend
            overlay line (``trend_end`` per horizon). Never shifts the band centre.
        vol_source: label of where ``vol`` came from ("hv" | "ewma" | "iv").
        momentum: canonical momentum label (``dgi_metrics.classify_momentum``).
            When provided it is the single directional source of truth — it drives
            the ``reading`` and ``bias``; the legacy technicals aggregate is ignored.

    Returns a dict with ``price_at_creation``, ``hv`` (the vol used), ``vol_source``,
    ``confidence``, ``bias``, ``momentum``, ``trend`` and a ``horizons`` map. The band
    centre is always ``current_price`` — bias and trend are reported separately.
    """
    if current_price is None or not _is_finite(current_price) or current_price <= 0:
        return None
    if vol is None or not _is_finite(vol) or vol <= 0:
        return None

    price = float(current_price)
    z1 = z_for_confidence(confidence)
    outer_conf = OUTER_CONFIDENCE if confidence < OUTER_CONFIDENCE else 0.99
    z2 = z_for_confidence(outer_conf)
    slope = float(trend["slope"]) if isinstance(trend, dict) and _is_finite(trend.get("slope")) else 0.0

    horizons: Dict[str, dict] = {}
    for name, bounds in HORIZONS.items():
        band = _band_for_sessions(
            price, vol, bounds["end_session"], z1=z1, z2=z2, trend_slope=slope
        )
        band["start_session"] = bounds["start_session"]
        band["end_session"] = bounds["end_session"]
        horizons[name] = band

    # Directional read: the canonical momentum drives it when supplied; otherwise
    # fall back to the legacy technicals-consensus bias + trend agreement.
    if momentum is not None:
        bias_val = momentum_bias(momentum)
        reading = reading_from_momentum(momentum)
    else:
        bias_val = compute_bias(technicals)
        reading = compute_reading(bias_val, slope)
    return {
        "price_at_creation": round(price, 4),
        "hv": round(float(vol), 6),
        "vol_source": vol_source,
        "confidence": round(float(confidence), 4),
        "outer_confidence": round(float(outer_conf), 4),
        "bias": bias_val,
        "momentum": momentum,
        "trend": trend if isinstance(trend, dict) else None,
        "reading": reading,
        "horizons": horizons,
    }


def compute_forecast_from_closes(
    closes: Sequence[float],
    technicals: Optional[dict] = None,
    *,
    current_price: Optional[float] = None,
    hv_window: int = HV_WINDOW,
    confidence: float = DEFAULT_CONFIDENCE,
    vol_source: str = "hv",
    iv: Optional[float] = None,
    trend_window: int = 20,
    momentum: Optional[str] = None,
) -> Optional[dict]:
    """Convenience wrapper: derive vol + trend from a closes series.

    ``vol_source`` selects the volatility estimator:
      - ``"hv"``  : flat-window historical volatility (default).
      - ``"ewma"``: exponentially-weighted realized volatility (more responsive).
      - ``"iv"`` / ``"iv_hv"``: use the provided ``iv`` (annualized implied vol);
        ``"iv_hv"`` falls back to HV when ``iv`` is missing.

    ``current_price`` defaults to the last close. Returns ``None`` when there is
    not enough clean history to compute a volatility estimate.
    """
    if not has_enough_history(closes):
        return None

    src = (vol_source or "hv").lower()
    vol = None
    used = "hv"
    if src in ("iv", "iv_hv") and iv is not None and _is_finite(iv) and iv > 0:
        vol, used = float(iv), "iv"
    elif src == "ewma":
        vol, used = ewma_volatility(closes, span=hv_window), "ewma"
    if vol is None or not _is_finite(vol) or vol <= 0:
        # Fallback (covers "hv", "iv_hv" without IV, or a failed EWMA).
        vol, used = historical_volatility(closes, window=hv_window), "hv"
    if vol is None:
        return None

    if current_price is None:
        clean = [c for c in closes if c is not None and _is_finite(c)]
        current_price = clean[-1] if clean else None

    trend = linear_trend(closes, window=trend_window)
    return compute_forecast(
        current_price, vol, technicals,
        confidence=confidence, trend=trend, vol_source=used,
        momentum=momentum,
    )


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


def endpoint_direction_correct(
    band: dict,
    price: float,
    bias: float,
    trend_slope: Optional[float] = None,
    *,
    threshold: float = BIAS_CONVICTION_THRESHOLD,
) -> Optional[bool]:
    """Whether the endpoint move agreed with the (high-conviction) directional call.

    Only scores a direction when there is a real claim:
      * ``|bias| >= threshold`` — low-conviction bias is treated as noise (``None``).
      * If ``trend_slope`` is known and non-zero, bias and trend must **agree**
        (same sign); divergent signals make no directional claim (``None``). This
        is the trend-agreement filter — it raises ``direction_pct`` by only
        scoring the clean, aligned setups.

    Returns ``None`` when there is no claim or inputs are missing. Old docs without
    a stored trend pass ``trend_slope=None`` and fall back to bias-only scoring.
    """
    if band is None or price is None or not _is_finite(price):
        return None
    if bias is None or not _is_finite(bias) or abs(bias) < threshold:
        return None
    # Trend-agreement filter (only when the trend is known).
    if trend_slope is not None and _is_finite(trend_slope) and float(trend_slope) != 0.0:
        if (float(trend_slope) > 0) != (bias > 0):
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
      - ``center``/``sigma``/``low1``/``high1``/``low2``/``high2``: the predicted
        price band for that horizon (so callers can show the actual range).
      - ``trend_end``: projected linear-trend value at the horizon end session.
      - ``mean_dev`` / ``mean_dev_pct``: mean absolute deviation of the realized
        closes from the projected trend line within the horizon window (``None``
        with no snapshots or no trend).
    """
    horizons = pred.get("horizons", {}) if isinstance(pred, dict) else {}
    snapshots = pred.get("snapshots", []) if isinstance(pred, dict) else []
    endpoints = pred.get("endpoints", {}) if isinstance(pred, dict) else {}
    trend = pred.get("trend") if isinstance(pred, dict) else None
    anchor = pred.get("price_at_creation") if isinstance(pred, dict) else None
    slope = None
    if isinstance(trend, dict) and _is_finite(trend.get("slope")) and _is_finite(anchor):
        slope = float(trend["slope"])

    out: Dict[str, dict] = {}
    for name in HORIZONS:
        if name not in horizons:
            continue
        band = horizons.get(name) or {}
        in_h = [s for s in snapshots if s.get("horizon") == name]
        n = len(in_h)
        if n:
            pct1 = round(100.0 * sum(1 for s in in_h if s.get("inside_1sigma")) / n, 1)
            pct2 = round(100.0 * sum(1 for s in in_h if s.get("inside_2sigma")) / n, 1)
        else:
            pct1 = pct2 = None

        mean_dev = mean_dev_pct = None
        if slope is not None and n:
            devs = []
            for s in in_h:
                off = s.get("offset")
                px = s.get("price")
                if _is_finite(off) and _is_finite(px):
                    trend_at = float(anchor) + slope * float(off)
                    devs.append(abs(float(px) - trend_at))
            if devs:
                mean_dev = round(sum(devs) / len(devs), 4)
                if _is_finite(anchor) and float(anchor) > 0:
                    mean_dev_pct = round(100.0 * mean_dev / float(anchor), 2)

        out[name] = {
            "path_count": n,
            "path_pct_1sigma": pct1,
            "path_pct_2sigma": pct2,
            "endpoint": endpoints.get(name),
            "center": band.get("center"),
            "sigma": band.get("sigma"),
            "low1": band.get("low1"),
            "high1": band.get("high1"),
            "low2": band.get("low2"),
            "high2": band.get("high2"),
            "trend_end": band.get("trend_end"),
            "mean_dev": mean_dev,
            "mean_dev_pct": mean_dev_pct,
        }
    return out


def aggregate_hit_rate(preds: Sequence[dict]) -> dict:
    """Rolling endpoint calibration across many predictions, per horizon.

    Only *resolved* endpoints count for the hit/direction metrics (the real
    calibration metric). Returns per horizon: ``resolved`` (n), ``hit_pct_1sigma``
    / ``hit_pct_2sigma`` (should trend toward the band confidence),
    ``direction_pct`` over endpoints that made a directional claim, and
    ``mean_dev_pct`` — the average (across predictions with snapshots) of each
    prediction's mean deviation of closes from its projected trend line, as a % of
    the entry price. Percentages are ``None`` when there is no data yet.
    """
    out: Dict[str, dict] = {}
    # Pre-compute per-prediction summaries once (for the trend deviation metric).
    summaries = [summarize_prediction(p) for p in preds if isinstance(p, dict)]
    for name in HORIZONS:
        resolved = [
            p.get("endpoints", {}).get(name)
            for p in preds
            if isinstance(p, dict) and p.get("endpoints", {}).get(name)
        ]
        n = len(resolved)
        dir_claims = [e for e in resolved if e.get("direction_correct") is not None]
        dev_pcts = [
            s[name]["mean_dev_pct"]
            for s in summaries
            if name in s and _is_finite(s[name].get("mean_dev_pct"))
        ]
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
            "mean_dev_pct": (
                round(sum(dev_pcts) / len(dev_pcts), 2) if dev_pcts else None
            ),
            "mean_dev_n": len(dev_pcts),
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
