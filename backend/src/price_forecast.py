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
from itertools import pairwise
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
BIAS_CONVICTION_THRESHOLD = 0.10


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

# ──────────────────────────────────────────────────────────────────────────────
# Both windows contribute to ONE adaptive trend, at every horizon.
# ──────────────────────────────────────────────────────────────────────────────
DEFAULT_TREND_WINDOW = 8
DEFAULT_TREND_WINDOW_LONG = 32
TREND_MIN_SAMPLES = 8
TREND_CALIBRATION_SAMPLES = 60
TREND_PRIOR_SAMPLES = 8
TREND_FALLBACK_FACTOR = 0.25

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
    state). ``trend_slope`` = first-session projected price change — pure price
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
        "csp": "caution", "cc": "favorable", "bias": -0.2,
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
    Bias column — never shifts the band. Non-directional states (Neutral, Unknown)
    return 0.0; Weakening carries a mild bearish lean (-0.2, fading uptrend).
    """
    spec = _MOMENTUM_READING.get(momentum or "")
    return float(spec["bias"]) if spec else 0.0


# Tunable scaling for the graded bias magnitude.
_ADX_FLOOR = 20.0          # below this ADX there is no real trend
_ADX_SPAN = 30.0           # ADX 20 -> 0 strength, ADX 50+ -> full strength
_SMA_DIST_FULL = 0.08      # price >= 8% away from SMA50 -> full extension strength
_ADX_WEIGHT = 0.5          # blend: trend strength (ADX) vs price extension (SMA dist)


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else (hi if x > hi else x)


def graded_momentum_bias(
    momentum: Optional[str],
    *,
    adx: Optional[float] = None,
    price: Optional[float] = None,
    sma_50: Optional[float] = None,
    sma_200: Optional[float] = None,
) -> float:
    """Continuous directional bias in [-1, +1].

    The **sign** is fixed by the momentum regime (Bullish → +, Bearish → −;
    non-directional states → 0). The **magnitude** is graded by two deterministic
    strength inputs and never exceeds the regime cap (1.0 for a clean trend, 0.6
    for an overextended/oversold one):

      - ADX (trend strength): ``ADX 20 → 0``, ``ADX 50+ → 1``.
      - Price distance from SMA50 (extension): ``0% → 0``, ``≥8% → 1``.

    ``bias = sign × cap × (w·adx_strength + (1−w)·dist_strength)``.

    Falls back to the discrete regime bias when the ADX/SMA inputs are missing or
    non-finite. Never shifts the band centre — used only for the Bias column and
    the directional hit-rate.
    """
    spec = _MOMENTUM_READING.get(momentum or "")
    if not spec:
        return 0.0
    base = float(spec["bias"])
    if base == 0.0:
        return 0.0
    sign = 1.0 if base > 0 else -1.0
    cap = abs(base)

    if not all(_is_finite(v) for v in (adx, price, sma_50, sma_200)) or not sma_50:
        return round(base, 4)  # discrete fallback

    adx_strength = _clamp((float(adx) - _ADX_FLOOR) / _ADX_SPAN, 0.0, 1.0)
    dist = abs(float(price) - float(sma_50)) / abs(float(sma_50))
    dist_strength = _clamp(dist / _SMA_DIST_FULL, 0.0, 1.0)
    strength = _ADX_WEIGHT * adx_strength + (1.0 - _ADX_WEIGHT) * dist_strength

    return round(sign * cap * strength, 4)


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
) -> Dict[str, float]:
    """Return primary/secondary price bands for a horizon ending at ``sessions``.

    ``unit_sigma = price * vol * sqrt(sessions / 252)`` where ``vol`` is annualized
    volatility (decimal, e.g. 0.28). The half-widths are ``z1`` (primary, inner,
    configurable confidence) and ``z2`` (secondary, outer reference) times
    ``unit_sigma``. The band centre stays at ``price`` (honest random-walk centre);
    the separate trend overlay never shifts the band.
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
    }


def _median(values: Sequence[float]) -> float:
    """Median of a non-empty sequence (no external deps)."""
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return float(s[mid])
    return (float(s[mid - 1]) + float(s[mid])) / 2.0


def _trend_base(prices: Sequence[float], short: int, long: int) -> dict | None:
    """Causal price-only regime proxy; never reuses today's ADX at past origins.

    Theil–Sen estimates on recent/slow windows are blended 65/35 (90/10 on
    reversal). Path efficiency |net change| / total absolute change gates drift
    and sets decay time (3..12 sessions). It is regime evidence, NOT accuracy.
    Correlated technicals/momentum remain independent display signals.
    """
    tail = prices[-long:]
    if len(tail) < long or any(not _is_finite(p) or p <= 0 for p in tail):
        return None

    def component(xs):
        n = len(xs)
        slope = _median([
            (xs[j] - xs[i]) / (j - i)
            for i in range(n) for j in range(i + 1, n)
        ])
        travel = sum(abs(b - a) for a, b in pairwise(xs))
        efficiency = abs(xs[-1] - xs[0]) / travel if travel else 0.0
        return slope, efficiency

    fast, ef = component(tail[-short:])
    slow, el = component(tail)
    reversal = fast * slow < 0
    weight = 0.9 if reversal else 0.65
    efficiency = ef if reversal else 0.8 * ef + 0.2 * el
    raw = weight * fast + (1 - weight) * slow
    return {
        "drift": raw * efficiency,
        "short_slope": fast, "long_slope": slow,
        "recent_weight": weight, "efficiency": efficiency,
        "regime": "reversal" if reversal else ("trending" if efficiency >= 0.6 else "ranging"),
        "decay_sessions": 3.0 + 9.0 * efficiency,
    }


def _damped_delta(base: dict, sessions: int) -> float:
    tau = base["decay_sessions"]
    return base["drift"] * tau * -math.expm1(-sessions / tau)


def adaptive_trend(
    closes: Sequence[float],
    window: int = DEFAULT_TREND_WINDOW,
    window_long: int = DEFAULT_TREND_WINDOW_LONG,
) -> dict | None:
    """One adaptive, damped, predictively shrunk trend (price units).

    At each horizon h, replay at most 60 NON-OVERLAPPING h-session returns,
    latest endpoint <= current origin. Each raw prediction uses only its own
    prefix; prefix bases are cached locally, never fitted on the full series.
    Regress realized fractional returns on raw damped predicted returns through
    zero; clamp the magnitude coefficient to [0,1] and shrink by n/(n+8).
    This cannot invert/amplify drift. Below 8 usable samples, an explicit 0.25
    conservative prior applies, with unavailable predictive error metrics.
    Samples with zero predicted drift carry no information and do not count.

    Store a 0/1/5/10/20-knot piecewise-linear session path: calibrated endpoints
    can have different shrinkage, so this is not a constant-slope extrapolation.
    Damping bounds the raw drift envelope; horizon-specific shrinkage can reshape
    individual segments, rather than promising monotone projected changes.
    ``slope`` means the first-session change; per-band slopes are horizon averages.
    Reported errors evaluate RAW out-of-sample predictions, not fitted shrinkage
    on its training set. They are descriptive, not a probability or performance
    improvement claim. No volatility/cone calibration is touched.
    """
    if closes is None:
        return None
    short = max(3, min(120, int(window)))
    long = max(short, min(120, int(window_long)))
    prices = [float(p) if _is_finite(p) else math.nan for p in closes]
    n = len(prices)
    cache = {}

    def base_at(origin):
        if origin not in cache:
            prefix = prices[max(0, origin - long + 1):origin + 1]
            cache[origin] = _trend_base(prefix, short, long)
        return cache[origin]

    base = base_at(n - 1)
    if base is None:
        return None
    calibration, knots = {}, [(0, 0.0)]
    for name, bounds in HORIZONS.items():
        h = bounds["end_session"]
        samples = []
        latest_origin = None
        for origin in range(n - 1 - h, long - 2, -h):
            if len(samples) >= TREND_CALIBRATION_SAMPLES:
                break
            past = base_at(origin)
            # Invalid intervening bars must not be collapsed into shorter time.
            future = prices[origin + 1:origin + h + 1]
            if past is None or any(not _is_finite(p) or p <= 0 for p in future):
                continue
            x = _damped_delta(past, h) / prices[origin]
            y = prices[origin + h] / prices[origin] - 1
            if abs(x) <= 1e-10:
                continue
            samples.append((x, y))
            if latest_origin is None:
                latest_origin = origin
        count = len(samples)
        enough = count >= TREND_MIN_SAMPLES
        factor = TREND_FALLBACK_FACTOR
        if enough:
            coefficient = sum(x * y for x, y in samples) / sum(x * x for x, _ in samples)
            factor = _clamp(coefficient, 0.0, 1.0) * count / (count + TREND_PRIOR_SAMPLES)
        calibration[name] = {
            "status": "available" if enough else "insufficient",
            "n": count, "min_n": TREND_MIN_SAMPLES,
            "factor": round(factor, 6),
            "raw_rmse_pct": (
                round(100 * math.sqrt(sum((y - x) ** 2 for x, y in samples) / count), 4)
                if enough else None
            ),
            "zero_drift_rmse_pct": (
                round(100 * math.sqrt(sum(y * y for _, y in samples) / count), 4)
                if enough else None
            ),
            "latest_origin_offset": latest_origin - (n - 1) if latest_origin is not None else None,
            "latest_endpoint_offset": latest_origin + h - (n - 1) if latest_origin is not None else None,
            "sample_stride": h,
        }
        knots.append((h, _damped_delta(base, h) * factor))
    deltas = [0.0]
    for (a, da), (b, db) in pairwise(knots):
        deltas.extend(
            round(da + (db - da) * (s - a) / (b - a), 6)
            for s in range(a + 1, b + 1)
        )
    return {
        "model": "adaptive_damped_v1", "projection": "piecewise_linear_sessions",
        "slope": deltas[1], "slope_kind": "first_session_change",
        "window": short, "window_long": long,
        **{k: round(v, 6) if isinstance(v, float) else v for k, v in base.items()},
        "session_deltas": deltas, "calibration": calibration,
    }


def project_trend(anchor: float, trend: dict | None, offset: float,
                  band: dict | None = None) -> float | None:
    """Authoritative stored path; linear fallback is READ compatibility only."""
    if not _is_finite(anchor) or not _is_finite(offset):
        return None
    trend = trend or {}
    path = trend.get("session_deltas")
    if isinstance(path, list) and 0 <= offset <= len(path) - 1:
        lo, hi = math.floor(offset), math.ceil(offset)
        if _is_finite(path[lo]) and _is_finite(path[hi]):
            return float(anchor) + path[lo] + (path[hi] - path[lo]) * (offset - lo)
    if trend.get("model") == "adaptive_damped_v1":
        return None
    slope = (band or {}).get("trend_slope")
    if not _is_finite(slope):
        slope = trend.get("slope")
    return float(anchor) + float(slope) * offset if _is_finite(slope) else None


def compute_forecast(
    current_price: Optional[float],
    vol: Optional[float],
    technicals: Optional[dict] = None,
    *,
    confidence: float = DEFAULT_CONFIDENCE,
    trend: Optional[dict] = None,
    vol_source: str = "hv",
    vol_scale: float = 1.0,
    momentum: Optional[str] = None,
    momentum_technicals: Optional[dict] = None,
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
        trend: optional ``adaptive_trend`` dict. Its stored session path supplies
            every overlay endpoint and deviation. Never shifts the band centre.
        vol_source: label of where ``vol`` came from ("hv" | "ewma" | "iv").
        momentum: canonical momentum label (``dgi_metrics.classify_momentum``).
            When provided it is the single directional source of truth — it drives
            the ``reading`` and the sign of ``bias``; the legacy technicals aggregate
            is ignored.
        momentum_technicals: optional DGI technicals dict (``adx``/``sma_50``/
            ``sma_200`` from ``calculate_technical_timing_score``). When present, the
            ``bias`` magnitude is graded continuously by ADX (trend strength) and the
            price↔SMA50 distance (extension); otherwise the discrete regime bias is
            used.

    Returns a dict with ``price_at_creation``, ``hv`` (the vol used), ``vol_source``,
    ``confidence``, ``bias``, ``momentum``, ``trend`` and a ``horizons`` map. The band
    centre is always ``current_price`` — bias and trend are reported separately.
    """
    if current_price is None or not _is_finite(current_price) or current_price <= 0:
        return None
    if vol is None or not _is_finite(vol) or vol <= 0:
        return None

    price = float(current_price)
    # Per-symbol calibration multiplier scales the volatility (and thus the band
    # width) without ever moving the honest random-walk centre.
    scale = float(vol_scale) if _is_finite(vol_scale) and vol_scale > 0 else 1.0
    vol = float(vol) * scale
    z1 = z_for_confidence(confidence)
    outer_conf = OUTER_CONFIDENCE if confidence < OUTER_CONFIDENCE else 0.99
    z2 = z_for_confidence(outer_conf)
    slope = float(trend["slope"]) if isinstance(trend, dict) and _is_finite(trend.get("slope")) else 0.0

    horizons: Dict[str, dict] = {}
    anchor = round(price, 4)
    for name, bounds in HORIZONS.items():
        band = _band_for_sessions(
            price, vol, bounds["end_session"], z1=z1, z2=z2,
        )
        endpoint = project_trend(anchor, trend, bounds["end_session"])
        if endpoint is not None:
            band["trend_end"] = round(endpoint, 4)
            band["trend_slope"] = round((endpoint - anchor) / bounds["end_session"], 6)
            band["trend_slope_kind"] = "horizon_average"
        band["start_session"] = bounds["start_session"]
        band["end_session"] = bounds["end_session"]
        horizons[name] = band

    # Directional read: the canonical momentum drives it when supplied; otherwise
    # fall back to the legacy technicals-consensus bias + trend agreement.
    if momentum is not None:
        mt = momentum_technicals or {}
        bias_val = graded_momentum_bias(
            momentum,
            adx=mt.get("adx"),
            price=price,
            sma_50=mt.get("sma_50"),
            sma_200=mt.get("sma_200"),
        )
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
    vol_scale: float = 1.0,
    iv: Optional[float] = None,
    trend_window: int = DEFAULT_TREND_WINDOW,
    trend_window_long: int = DEFAULT_TREND_WINDOW_LONG,
    momentum: Optional[str] = None,
    momentum_technicals: Optional[dict] = None,
) -> Optional[dict]:
    """Convenience wrapper: derive vol + trend from a closes series.

    ``vol_source`` selects the volatility estimator:
      - ``"hv"``  : flat-window historical volatility (default).
      - ``"ewma"``: exponentially-weighted realized volatility (more responsive).
      - ``"iv"`` / ``"iv_hv"``: use the provided ``iv`` (annualized implied vol);
        ``"iv_hv"`` falls back to HV when ``iv`` is missing.

    ``current_price`` defaults to the last close. Returns ``None`` when there is
    not enough clean history to compute a volatility estimate.

    ``trend_window`` and ``trend_window_long`` set the recent/slow robust
    components of the single adaptive trend, shared by every horizon.
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

    trend = adaptive_trend(closes, window=trend_window, window_long=trend_window_long)
    return compute_forecast(
        current_price, vol, technicals,
        confidence=confidence, trend=trend,
        vol_source=used, vol_scale=vol_scale,
        momentum=momentum, momentum_technicals=momentum_technicals,
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
      - ``trend_end``: stored trend-path value at the horizon end session.
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
        # Prefer the horizon's own slope (horizon-aware windows); fall back to the
        # top-level trend slope for legacy docs whose bands have no ``trend_slope``.
        band_slope = band.get("trend_slope")
        h_slope = float(band_slope) if _is_finite(band_slope) else slope
        in_h = [s for s in snapshots if s.get("horizon") == name]
        n = len(in_h)
        if n:
            pct1 = round(100.0 * sum(1 for s in in_h if s.get("inside_1sigma")) / n, 1)
            pct2 = round(100.0 * sum(1 for s in in_h if s.get("inside_2sigma")) / n, 1)
        else:
            pct1 = pct2 = None

        mean_dev = mean_dev_pct = None
        if (h_slope is not None or (trend or {}).get("session_deltas")) and _is_finite(anchor) and n:
            devs = []
            for s in in_h:
                off = s.get("offset")
                px = s.get("price")
                if _is_finite(off) and _is_finite(px):
                    trend_at = project_trend(float(anchor), trend, float(off), band)
                    if trend_at is not None:
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
            "trend_slope": band.get("trend_slope"),
            "trend_slope_kind": band.get("trend_slope_kind"),
            "start_session": band.get("start_session"),
            "end_session": band.get("end_session"),
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
            "direction_n": len(dir_claims),
            "mean_dev_pct": (
                round(sum(dev_pcts) / len(dev_pcts), 2) if dev_pcts else None
            ),
            "mean_dev_n": len(dev_pcts),
        }
    return out


# Per-symbol volatility recalibration (self-adjusting band width).
# The band is a target-confidence interval (e.g. 50%). If a symbol's realized
# closes land inside the band more/less often than the target, its volatility is
# mis-estimated for that symbol. ``compute_calibration_factor`` derives a
# multiplier ``k`` applied to volatility on the NEXT forecast so the band's
# hit-rate drifts toward the target. It is never retroactive: past predictions
# keep their width; each run nudges ``k`` from the recent resolved history.
CALIBRATION_MIN_N = 20        # need this many resolved endpoints to recalibrate
CALIBRATION_K_MIN = 0.5       # clamp: never shrink the band below half
CALIBRATION_K_MAX = 2.0       # clamp: never widen the band beyond double
CALIBRATION_ALPHA = 0.3       # EWMA weight on the freshly-computed target k
CALIBRATION_WINDOW = 60       # most-recent predictions scanned for residuals


def _percentile(sorted_vals: Sequence[float], q: float) -> float:
    """Linear-interpolated ``q``-quantile (0..1) of an ascending sequence."""
    if not sorted_vals:
        return 0.0
    if q <= 0:
        return sorted_vals[0]
    if q >= 1:
        return sorted_vals[-1]
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_vals[lo]
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def compute_calibration_factor(
    preds: Sequence[dict],
    target_confidence: float,
    *,
    prev_k: float = 1.0,
    min_n: int = CALIBRATION_MIN_N,
    k_min: float = CALIBRATION_K_MIN,
    k_max: float = CALIBRATION_K_MAX,
    alpha: float = CALIBRATION_ALPHA,
    window: int = CALIBRATION_WINDOW,
) -> dict:
    """Derive a per-symbol volatility multiplier ``k`` from resolved endpoints.

    For each resolved endpoint we standardize the realized-vs-centre deviation by
    the band's own (raw, pre-``k``) sigma::

        r = |realized_close - center| / (sigma / k_applied)

    where ``sigma`` is the stored band width and ``k_applied`` is the multiplier
    that was in force when that prediction was made (``calibration.k`` on the doc,
    default ``1.0`` for legacy docs). Standardizing removes the √t horizon scale,
    so residuals from all four horizons pool into one sample.

    The target-central quantile of ``|r|`` is compared with the target z-value::

        k_target = percentile(|r|, target_confidence) / z_for_confidence(target)

    A perfectly-calibrated band gives ``k_target ≈ 1``; an over-wide band (band
    hit-rate above target) gives ``k_target < 1`` (shrink); an under-wide band
    gives ``k_target > 1`` (widen). The raw target is clamped to
    ``[k_min, k_max]`` and EWMA-smoothed against ``prev_k`` so ``k`` moves
    gradually and never oscillates or explodes.

    Returns ``{k, k_target, prev_k, n, target, applied}``:
      - ``k``: the multiplier to apply to volatility on the next forecast.
      - ``k_target``: the freshly-computed (clamped) target before smoothing.
      - ``n``: resolved endpoints used.
      - ``applied``: ``False`` (and ``k == prev_k``) when ``n < min_n``.
    """
    z1 = z_for_confidence(target_confidence)
    residuals: List[float] = []
    for p in preds[:window] if window else preds:
        if not isinstance(p, dict):
            continue
        endpoints = p.get("endpoints") or {}
        horizons = p.get("horizons") or {}
        k_applied = (p.get("calibration") or {}).get("k")
        k_applied = float(k_applied) if _is_finite(k_applied) and k_applied else 1.0
        for name, ep in endpoints.items():
            if not isinstance(ep, dict):
                continue
            realized = ep.get("price")
            band = horizons.get(name) or {}
            center = band.get("center")
            sigma = band.get("sigma")
            if not (_is_finite(realized) and _is_finite(center) and _is_finite(sigma)):
                continue
            sigma_raw = float(sigma) / k_applied
            if sigma_raw <= 0:
                continue
            residuals.append(abs(float(realized) - float(center)) / sigma_raw)

    n = len(residuals)
    prev_k = float(prev_k) if _is_finite(prev_k) and prev_k > 0 else 1.0
    if n < min_n or z1 <= 0:
        return {"k": round(prev_k, 4), "k_target": None, "prev_k": round(prev_k, 4),
                "n": n, "target": round(float(target_confidence), 4), "applied": False}

    residuals.sort()
    q = _percentile(residuals, float(target_confidence))
    k_target = q / z1
    k_target = max(k_min, min(k_max, k_target))
    k = alpha * k_target + (1.0 - alpha) * prev_k
    k = max(k_min, min(k_max, k))
    return {"k": round(k, 4), "k_target": round(k_target, 4), "prev_k": round(prev_k, 4),
            "n": n, "target": round(float(target_confidence), 4), "applied": True}


# Minimum number of samples required for the trimmed mean to be meaningful.
# Below this the trim would drop too much (or nothing), so callers fall back to
# the plain mean. With n=5 and trim=0.20 we drop 1 from each tail, leaving 3.
TRIMMED_MEAN_MIN_N = 5


def _trimmed_mean(values: Sequence[float], trim: float = 0.20) -> Optional[float]:
    """Symmetric trimmed mean: drop ``floor(n*trim)`` values from each tail.

    Robust to outliers (earnings gaps, spikes). Returns ``None`` when there is
    not enough data for the trim to be meaningful (``n < TRIMMED_MEAN_MIN_N``)
    or when nothing survives the trim. Never raises.
    """
    clean = sorted(float(v) for v in values if _is_finite(v))
    n = len(clean)
    if n < TRIMMED_MEAN_MIN_N:
        return None
    k = int(math.floor(n * trim))
    kept = clean[k: n - k] if k > 0 else clean
    if not kept:
        return None
    return sum(kept) / len(kept)


def aggregate_forecast_averages(
    preds: Sequence[dict],
    *,
    trim: float = 0.20,
) -> dict:
    """Smooth horizon-average effective changes, with a price-centred range.

    Rather than averaging each prediction's ``trend_end`` (which blends
    projections anchored on different days and therefore aimed at different
    target dates), this **averages recent per-horizon endpoint changes / sessions** and
    projects **once** from the latest anchor price to the horizon end::

        price = anchor + mean_slope * sessions

    where ``sessions`` is the horizon length (1d→1, 1w→5, 2w→10, 4w→20) and
    ``anchor`` is the most recent prediction's band centre (today's price).
    The slope is averaged over the most recent ``lookback`` predictions, with
    ``lookback`` matching the horizon length (1 / 5 / 10 / 20). This smooths
    day-to-day jitter in the direction while keeping a single, clean target
    date.

    A symmetric range comes from the latest band's primary volatility
    half-width (``high1 - center`` ≈ ``z1 * sigma``), so it is horizon-scaled
    (narrow for 1d, wide for 4w) and reuses the same confidence the fan chart
    uses. It is centred on the **current price** (the anchor), not on the
    projection, keeping volatility calibration independent of direction.
    Consequence: when the trend is
    strong and the horizon long, the projected price may sit near the edge of —
    or outside — this range.

    Predictions are ordered newest-first by ``created_date``. Legacy documents
    without per-band ``trend_slope``/``center`` fall back to the top-level
    ``trend.slope`` and ``price_at_creation``.

    Returns per horizon ``{n, lookback, anchor, mean, trimmed_mean, low, high}``:
      - ``lookback``: recent predictions whose slope is averaged (= sessions).
      - ``n``: predictions actually contributing a finite slope (``<= lookback``).
      - ``anchor``: latest anchor price the projection starts from (``None`` if
        unavailable).
      - ``mean``: price projected with the plain mean slope (``None`` when
        ``n == 0`` or no anchor).
      - ``trimmed_mean``: price projected with the symmetric 20% trimmed slope
        (``None`` when ``n < TRIMMED_MEAN_MIN_N``; always ``None`` for 1d).
      - ``low`` / ``high``: ``anchor`` ± volatility half-width — the ±1σ cone
        centred on the current price (``None`` when the band width or projection
        is unavailable).
    """
    # Newest-first, so a per-horizon lookback picks the latest predictions.
    ordered = sorted(
        (p for p in preds if isinstance(p, dict)),
        key=lambda p: p.get("created_date", ""),
        reverse=True,
    )

    def _band(p: dict, name: str) -> dict:
        return (p.get("horizons") or {}).get(name) or {}

    def _slope(p: dict, name: str) -> Optional[float]:
        band = _band(p, name)
        if (p.get("trend") or {}).get("model") == "adaptive_damped_v1":
            anchor = p.get("price_at_creation")
            h = HORIZONS[name]["end_session"]
            endpoint = project_trend(anchor, p.get("trend"), h, band)
            return (endpoint - anchor) / h if endpoint is not None else None
        sl = band.get("trend_slope")
        if sl is None:
            top = p.get("trend")
            sl = top.get("slope") if isinstance(top, dict) else None
        return float(sl) if _is_finite(sl) else None

    out: Dict[str, dict] = {}
    for name in HORIZONS:
        sessions = HORIZONS[name]["end_session"]  # 1d→1, 1w→5, 2w→10, 4w→20
        lookback = sessions

        slopes: List[float] = []
        for p in ordered:
            if len(slopes) >= lookback:
                break
            sl = _slope(p, name)
            if sl is not None:
                slopes.append(sl)
        n = len(slopes)

        # Anchor price + volatility half-width from the latest usable band.
        anchor: Optional[float] = None
        half_width: Optional[float] = None
        for p in ordered:
            band = _band(p, name)
            center = band.get("center")
            if not _is_finite(center):
                center = p.get("price_at_creation")
            if not _is_finite(center):
                continue
            anchor = float(center)
            high1 = band.get("high1")
            band_center = band.get("center")
            if _is_finite(high1) and _is_finite(band_center):
                half_width = abs(float(high1) - float(band_center))
            elif _is_finite(band.get("sigma")) and _is_finite(band.get("z1")):
                half_width = abs(float(band["sigma"]) * float(band["z1"]))
            break

        def _project(slope: Optional[float]) -> Optional[float]:
            if slope is None or anchor is None:
                return None
            return round(anchor + slope * sessions, 4)

        mean_slope = (sum(slopes) / n) if n else None
        trimmed_slope = _trimmed_mean(slopes, trim=trim)

        mean_price = _project(mean_slope)
        trimmed_price = _project(trimmed_slope)

        low = high = None
        if mean_price is not None and anchor is not None and half_width is not None:
            # Range is the honest ±1σ volatility cone centred on the CURRENT price
            # (like the fan chart), not on the trend projection: recentring on the
            # projection was validated to reduce coverage (the realized close stays
            # near today's price, since direction is ~coin-flip at these horizons).
            low = round(anchor - half_width, 4)
            high = round(anchor + half_width, 4)

        out[name] = {
            "n": n,
            "lookback": lookback,
            "anchor": round(anchor, 4) if anchor is not None else None,
            "mean": mean_price,
            "trimmed_mean": trimmed_price,
            "low": low,
            "high": high,
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
