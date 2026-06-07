"""Deterministic Position Scorer (DPS).

Pure-code scoring engine for open short put and short call positions.
No LLM involved — uses fixed rules to output HOLD / WATCH / ROLL.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Trend helpers
# ──────────────────────────────────────────────────────────────────────────────

def _compute_trend(series: List[Optional[float]], window: int = 5) -> str:
    """Determine if a numeric series is improving, worsening, or flat.

    Uses linear slope of last `window` non-None values.
    Returns: "improving", "worsening", or "flat".
    """
    valid = [v for v in series if v is not None]
    if len(valid) < 3:
        return "flat"
    segment = valid[-window:]
    if len(segment) < 3:
        return "flat"
    n = len(segment)
    x_mean = (n - 1) / 2.0
    y_mean = sum(segment) / n
    num = sum((i - x_mean) * (segment[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    if den == 0:
        return "flat"
    slope = num / den
    # Normalize slope relative to value magnitude
    magnitude = abs(y_mean) if y_mean != 0 else 1.0
    rel_slope = slope / magnitude
    if rel_slope > 0.02:
        return "improving"
    elif rel_slope < -0.02:
        return "worsening"
    return "flat"


def _compute_dte(expiration: str) -> int:
    """Days to expiration from YYYYMMDD or YYYY-MM-DD string."""
    exp_str = expiration.replace("-", "")
    try:
        exp_date = datetime.strptime(exp_str, "%Y%m%d").date()
    except ValueError:
        return 999
    today = datetime.now(timezone.utc).date()
    return (exp_date - today).days


# ──────────────────────────────────────────────────────────────────────────────
# Greeks extraction from options chain
# ──────────────────────────────────────────────────────────────────────────────

def extract_greeks_from_chain(
    chain_json: str | dict,
    strike: float,
    expiration: str,
    option_type: str,
) -> Optional[Dict]:
    """Extract greeks for a specific contract from the full options chain.

    Args:
        chain_json: Options chain as JSON string or dict.
        strike: Position strike price.
        expiration: Expiration in YYYYMMDD or YYYY-MM-DD format.
        option_type: "call" or "put".

    Returns:
        Dict with delta, gamma, theta, iv, or None if not found.
    """
    if isinstance(chain_json, str):
        try:
            chain = json.loads(chain_json)
        except (json.JSONDecodeError, TypeError):
            return None
    else:
        chain = chain_json

    bucket_key = "calls" if option_type == "call" else "puts"
    bucket = chain.get(bucket_key, {})

    exp_key = expiration.replace("-", "")
    exp_data = bucket.get(exp_key)
    if exp_data is None:
        return None

    # Try exact strike match (format: "150.0" or "150")
    strike_keys = [
        f"{strike:.1f}" if strike == int(strike) else str(strike),
        str(strike),
        str(int(strike)) + ".0",
    ]

    contract = None
    for sk in strike_keys:
        if sk in exp_data:
            contract = exp_data[sk]
            break

    # Fuzzy match: find closest strike within $0.50
    if contract is None:
        best_key = None
        best_diff = float("inf")
        for k, v in exp_data.items():
            try:
                k_strike = float(v.get("strike", k))
            except (ValueError, TypeError):
                continue
            diff = abs(k_strike - strike)
            if diff < best_diff and diff <= 0.5:
                best_diff = diff
                best_key = k
        if best_key:
            contract = exp_data[best_key]

    if contract is None:
        return None

    return {
        "delta": contract.get("delta"),
        "gamma": contract.get("gamma"),
        "theta": contract.get("theta"),
        "iv": contract.get("iv"),
        "bid": contract.get("bid"),
        "ask": contract.get("ask"),
        "mid": contract.get("mid"),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Snapshot series extraction
# ──────────────────────────────────────────────────────────────────────────────

def extract_series_from_snapshots(
    snapshots: List[Dict],
) -> Dict[str, List[Optional[float]]]:
    """Extract time series from position snapshots (ordered oldest→newest)."""
    return {
        "rsi": [s.get("rsi_14") for s in snapshots],
        "macd": [s.get("macd_level") for s in snapshots],
        "adx": [s.get("adx") for s in snapshots],
        "gap_percent": [s.get("gap_percent") for s in snapshots],
        "underlying_price": [s.get("underlying_price") for s in snapshots],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Short PUT scorer
# ──────────────────────────────────────────────────────────────────────────────

def score_short_put(
    greeks: Dict,
    snapshots: List[Dict],
    strike: float,
    expiration: str,
    underlying_price: float,
) -> Dict:
    """Score a short put position deterministically.

    Returns the full analysis dict with score, status, drivers, etc.
    """
    delta = abs(greeks.get("delta") or 0)
    gamma = abs(greeks.get("gamma") or 0)
    theta = abs(greeks.get("theta") or 0)
    iv = greeks.get("iv") or 0

    dte = _compute_dte(expiration)
    series = extract_series_from_snapshots(snapshots)

    # Current values (latest snapshot)
    rsi = series["rsi"][-1] if series["rsi"] and series["rsi"][-1] is not None else 50.0
    macd = series["macd"][-1] if series["macd"] and series["macd"][-1] is not None else 0.0
    adx = series["adx"][-1] if series["adx"] and series["adx"][-1] is not None else 15.0

    # Trends
    rsi_trend = _compute_trend(series["rsi"])
    macd_trend = _compute_trend(series["macd"])
    adx_trend = _compute_trend(series["adx"])
    iv_series = [greeks.get("iv")]  # Only current point available from chain
    iv_trend = "flat"

    # GAP
    gap_absolute = strike - underlying_price  # positive = OTM for put
    gap_percent = (gap_absolute / strike * 100.0) if strike else 0.0

    # Risk zone
    if delta < 0.30:
        risk_zone = "SAFE"
    elif delta < 0.45:
        risk_zone = "MONITOR"
    else:
        risk_zone = "ATM_CRITICAL"

    # ── Scoring ──
    score = 50
    key_drivers = []
    rule_hits = []

    # Delta
    if delta < 0.30:
        score += 15
        key_drivers.append(f"Delta {delta:.2f} favorable (OTM)")
    elif delta < 0.45:
        pass  # +0
    else:
        score -= 15
        key_drivers.append(f"Delta {delta:.2f} ATM critical")
        rule_hits.append("delta_critical")

    # GAP
    if gap_percent > 5.0:
        score += 10
        key_drivers.append(f"Gap {gap_percent:.1f}% comfortably OTM")
    elif gap_percent > 0 and gap_percent <= 2.0:
        score -= 10
        key_drivers.append(f"Gap {gap_percent:.1f}% near ATM")
        rule_hits.append("gap_near_atm")
    elif gap_percent <= 0:
        score -= 20
        key_drivers.append(f"Gap {gap_percent:.1f}% ITM")
        rule_hits.append("gap_itm")

    # RSI (for short put: low RSI = oversold = favorable for bounce)
    if rsi < 35:
        score += 12
        key_drivers.append(f"RSI {rsi:.1f} oversold (bounce likely)")
    elif rsi < 50:
        score += 5
    elif rsi <= 60:
        pass
    else:
        score -= 5

    # RSI trend
    if rsi_trend == "improving":
        score += 8
        key_drivers.append("RSI trend improving")
    elif rsi_trend == "worsening":
        score -= 8
        key_drivers.append("RSI trend worsening")
        rule_hits.append("rsi_worsening")

    # MACD (improving = less negative = favorable for put)
    if macd_trend == "improving":
        score += 12
        key_drivers.append("MACD improving")
    elif macd_trend == "worsening":
        score -= 12
        key_drivers.append("MACD worsening")
        rule_hits.append("macd_worsening")

    # ADX
    if adx < 20:
        score += 10
        key_drivers.append(f"ADX {adx:.1f} low (no trend)")
    elif adx <= 25:
        score += 5
    elif adx_trend == "worsening":  # ADX falling = trend weakening = good
        score += 3
        key_drivers.append(f"ADX {adx:.1f} but falling")
    else:  # ADX > 25 and rising
        score -= 12
        key_drivers.append(f"ADX {adx:.1f} rising (strong trend)")
        rule_hits.append("adx_rising")

    # DTE
    if dte > 21:
        score += 8
    else:
        score -= 8
        key_drivers.append(f"DTE {dte} short")
        rule_hits.append("dte_short")

    # Gamma penalty
    if gamma > 0.05 and delta >= 0.45:
        score -= 10
        rule_hits.append("high_gamma_atm")

    # Theta benefit
    if theta > 0.10:
        score += 5

    # IV
    if iv_trend == "worsening":
        score -= 5
    elif iv_trend == "improving":
        score += 5

    # Clamp
    score = max(0, min(100, score))

    # ── Overrides ──
    forced = None

    # Force ROLL: Delta ≥ 0.55 AND ADX > 25 rising AND MACD worsening
    if delta >= 0.55 and adx > 25 and adx_trend == "improving" and macd_trend == "worsening":
        forced = "ROLL"
        rule_hits.append("override_force_roll")

    # Allow HOLD: Delta ≥ 0.45 BUT RSI < 35 improving AND MACD improving AND ADX falling
    if (delta >= 0.45 and rsi < 35 and rsi_trend == "improving"
            and macd_trend == "improving" and adx_trend == "worsening"):
        forced = "HOLD"
        rule_hits.append("override_allow_hold")

    # ── Final decision ──
    if forced:
        status = forced
    elif score >= 70:
        status = "HOLD"
    elif score >= 50:
        status = "WATCH"
    else:
        status = "ROLL"

    # Summary
    summary_parts = []
    if risk_zone == "ATM_CRITICAL":
        summary_parts.append("ATM pressure")
    if macd_trend == "worsening":
        summary_parts.append("MACD deteriorating")
    if adx > 25 and adx_trend == "improving":
        summary_parts.append("strong downtrend")
    if rsi < 35:
        summary_parts.append("oversold")
    if not summary_parts:
        summary_parts.append("position stable" if status == "HOLD" else "monitoring")

    return {
        "strategy": "short_put_monitor",
        "ticker": None,  # Caller fills in
        "status": status,
        "score": score,
        "risk_zone": risk_zone,
        "summary": ", ".join(summary_parts),
        "key_drivers": key_drivers[:5],
        "rule_hits": rule_hits,
        "next_focus": _next_focus_put(delta, dte, adx, adx_trend, rsi_trend),
        "inputs": {
            "delta": round(delta, 4),
            "gamma": round(gamma, 4),
            "theta": round(theta, 4),
            "iv": round(iv, 4),
            "dte": dte,
            "rsi": round(rsi, 2),
            "rsi_trend": rsi_trend,
            "macd": round(macd, 4),
            "macd_trend": macd_trend,
            "adx": round(adx, 2),
            "adx_trend": adx_trend,
            "gap_percent": round(gap_percent, 2),
            "underlying_price": underlying_price,
            "strike": strike,
        },
    }


def _next_focus_put(delta, dte, adx, adx_trend, rsi_trend) -> str:
    if delta >= 0.45:
        return "Watch delta closely — near ATM"
    if dte <= 14:
        return "DTE very short — monitor time decay"
    if adx > 25 and adx_trend == "improving":
        return "ADX rising — trend gaining strength"
    if rsi_trend == "worsening":
        return "RSI weakening — watch for momentum shift"
    return "Continue monitoring — no immediate concern"


# ──────────────────────────────────────────────────────────────────────────────
# Short CALL scorer
# ──────────────────────────────────────────────────────────────────────────────

def score_short_call(
    greeks: Dict,
    snapshots: List[Dict],
    strike: float,
    expiration: str,
    underlying_price: float,
) -> Dict:
    """Score a short call position deterministically.

    Returns the full analysis dict with score, status, drivers, etc.
    """
    delta = abs(greeks.get("delta") or 0)
    gamma = abs(greeks.get("gamma") or 0)
    theta = abs(greeks.get("theta") or 0)
    iv = greeks.get("iv") or 0

    dte = _compute_dte(expiration)
    series = extract_series_from_snapshots(snapshots)

    # Current values
    rsi = series["rsi"][-1] if series["rsi"] and series["rsi"][-1] is not None else 50.0
    macd = series["macd"][-1] if series["macd"] and series["macd"][-1] is not None else 0.0
    adx = series["adx"][-1] if series["adx"] and series["adx"][-1] is not None else 15.0

    # Trends
    rsi_trend = _compute_trend(series["rsi"])
    macd_trend = _compute_trend(series["macd"])
    adx_trend = _compute_trend(series["adx"])
    iv_trend = "flat"

    # GAP (for call: positive = OTM)
    gap_absolute = underlying_price - strike  # negative = OTM for call
    gap_percent = (gap_absolute / strike * 100.0) if strike else 0.0
    # Invert: positive gap_percent means ITM for calls
    otm_gap = -gap_percent

    # Risk zone
    if delta < 0.30:
        risk_zone = "SAFE"
    elif delta < 0.45:
        risk_zone = "MONITOR"
    else:
        risk_zone = "ATM_CRITICAL"

    # ── Scoring ──
    score = 50
    key_drivers = []
    rule_hits = []

    # Delta
    if delta < 0.30:
        score += 15
        key_drivers.append(f"Delta {delta:.2f} favorable (OTM)")
    elif delta < 0.45:
        pass
    else:
        score -= 15
        key_drivers.append(f"Delta {delta:.2f} ATM critical")
        rule_hits.append("delta_critical")

    # GAP
    if otm_gap > 5.0:
        score += 10
        key_drivers.append(f"Gap {otm_gap:.1f}% comfortably OTM")
    elif otm_gap > 0 and otm_gap <= 2.0:
        score -= 10
        key_drivers.append(f"Gap {otm_gap:.1f}% near ATM")
        rule_hits.append("gap_near_atm")
    elif otm_gap <= 0:
        score -= 20
        key_drivers.append(f"Gap {otm_gap:.1f}% ITM")
        rule_hits.append("gap_itm")

    # RSI (for short call: high RSI = overbought = favorable for pullback)
    if rsi > 65:
        score += 12
        key_drivers.append(f"RSI {rsi:.1f} overbought (pullback likely)")
    elif rsi >= 50:
        score += 5
    elif rsi >= 40:
        pass
    else:
        score -= 5

    # RSI trend (for calls: weakening RSI = favorable)
    if rsi_trend == "worsening":  # RSI falling = good for short call
        score += 8
        key_drivers.append("RSI weakening (favorable)")
    elif rsi_trend == "improving":  # RSI rising = bad for short call
        score -= 8
        key_drivers.append("RSI strengthening (unfavorable)")
        rule_hits.append("rsi_strengthening")

    # MACD (for calls: weakening MACD = favorable)
    if macd_trend == "worsening":  # MACD falling = good for short call
        score += 12
        key_drivers.append("MACD weakening (favorable)")
    elif macd_trend == "improving":  # MACD rising = bad for short call
        score -= 12
        key_drivers.append("MACD improving (unfavorable)")
        rule_hits.append("macd_improving")

    # ADX
    if adx < 20:
        score += 10
        key_drivers.append(f"ADX {adx:.1f} low (no trend)")
    elif adx <= 25:
        score += 5
    elif adx_trend == "worsening":  # ADX falling = trend weakening = good
        score += 3
        key_drivers.append(f"ADX {adx:.1f} but falling")
    else:  # ADX > 25 and rising = strong uptrend = bad for short call
        score -= 12
        key_drivers.append(f"ADX {adx:.1f} rising (strong uptrend)")
        rule_hits.append("adx_rising")

    # DTE
    if dte > 21:
        score += 8
    else:
        score -= 8
        key_drivers.append(f"DTE {dte} short")
        rule_hits.append("dte_short")

    # Gamma penalty
    if gamma > 0.05 and delta >= 0.45:
        score -= 10
        rule_hits.append("high_gamma_atm")

    # Theta benefit
    if theta > 0.10:
        score += 5

    # IV
    if iv_trend == "worsening":
        score -= 5
    elif iv_trend == "improving":
        score += 5

    # Clamp
    score = max(0, min(100, score))

    # ── Overrides ──
    forced = None

    # Force ROLL: Delta ≥ 0.55 AND ADX > 25 rising AND MACD improving
    if delta >= 0.55 and adx > 25 and adx_trend == "improving" and macd_trend == "improving":
        forced = "ROLL"
        rule_hits.append("override_force_roll")

    # Allow HOLD: Delta ≥ 0.45 BUT RSI > 65 turning down AND MACD weakening AND ADX falling
    if (delta >= 0.45 and rsi > 65 and rsi_trend == "worsening"
            and macd_trend == "worsening" and adx_trend == "worsening"):
        forced = "HOLD"
        rule_hits.append("override_allow_hold")

    # ── Final decision ──
    if forced:
        status = forced
    elif score >= 70:
        status = "HOLD"
    elif score >= 50:
        status = "WATCH"
    else:
        status = "ROLL"

    # Summary
    summary_parts = []
    if risk_zone == "ATM_CRITICAL":
        summary_parts.append("ATM pressure")
    if macd_trend == "improving":
        summary_parts.append("MACD strengthening")
    if adx > 25 and adx_trend == "improving":
        summary_parts.append("strong uptrend")
    if rsi > 65:
        summary_parts.append("overbought")
    if not summary_parts:
        summary_parts.append("position stable" if status == "HOLD" else "monitoring")

    return {
        "strategy": "short_call_monitor",
        "ticker": None,
        "status": status,
        "score": score,
        "risk_zone": risk_zone,
        "summary": ", ".join(summary_parts),
        "key_drivers": key_drivers[:5],
        "rule_hits": rule_hits,
        "next_focus": _next_focus_call(delta, dte, adx, adx_trend, rsi_trend),
        "inputs": {
            "delta": round(delta, 4),
            "gamma": round(gamma, 4),
            "theta": round(theta, 4),
            "iv": round(iv, 4),
            "dte": dte,
            "rsi": round(rsi, 2),
            "rsi_trend": rsi_trend,
            "macd": round(macd, 4),
            "macd_trend": macd_trend,
            "adx": round(adx, 2),
            "adx_trend": adx_trend,
            "gap_percent": round(otm_gap, 2),
            "underlying_price": underlying_price,
            "strike": strike,
        },
    }


def _next_focus_call(delta, dte, adx, adx_trend, rsi_trend) -> str:
    if delta >= 0.45:
        return "Watch delta closely — near ATM"
    if dte <= 14:
        return "DTE very short — monitor time decay"
    if adx > 25 and adx_trend == "improving":
        return "ADX rising — uptrend gaining strength"
    if rsi_trend == "improving":
        return "RSI rising — watch for continued momentum"
    return "Continue monitoring — no immediate concern"


# ──────────────────────────────────────────────────────────────────────────────
# Public API — run full analysis
# ──────────────────────────────────────────────────────────────────────────────

def run_dps_analysis(
    symbol: str,
    strike: float,
    expiration: str,
    option_type: str,
    chain_json: str | dict,
    snapshots: List[Dict],
    underlying_price: Optional[float] = None,
) -> Dict:
    """Run the full DPS analysis for a position.

    Args:
        symbol: Ticker symbol.
        strike: Position strike price.
        expiration: Expiration in YYYYMMDD or YYYY-MM-DD.
        option_type: "call" or "put".
        chain_json: Full options chain JSON.
        snapshots: List of snapshot dicts (oldest first).
        underlying_price: Current underlying price (optional, derived from snapshots).

    Returns:
        Full analysis result dict, or error dict on failure.
    """
    # Extract greeks
    greeks = extract_greeks_from_chain(chain_json, strike, expiration, option_type)
    if greeks is None:
        return {
            "error": f"Could not find contract in chain for {symbol} "
                     f"{option_type} ${strike} exp {expiration}",
            "status": "ERROR",
        }

    # Determine underlying price
    if underlying_price is None and snapshots:
        underlying_price = snapshots[-1].get("underlying_price")
    if underlying_price is None:
        return {
            "error": "Could not determine current underlying price",
            "status": "ERROR",
        }

    # Run scorer
    if option_type == "put":
        result = score_short_put(greeks, snapshots, strike, expiration, underlying_price)
    else:
        result = score_short_call(greeks, snapshots, strike, expiration, underlying_price)

    result["ticker"] = symbol
    return result
