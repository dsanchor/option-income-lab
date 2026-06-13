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

def _compute_trend(series: List[Optional[float]], window: int = 21) -> Tuple[str, dict]:
    """Determine if a numeric series is improving, worsening, or flat.

    Uses linear slope of last `window` non-None values plus first-to-last delta.
    Returns: (direction, details) where direction is "improving"/"worsening"/"flat"
    and details contains diagnostic info.
    """
    valid = [v for v in series if v is not None]
    details = {"points_used": len(valid), "series": valid[-window:] if valid else []}

    if len(valid) < 3:
        details["reason"] = "insufficient data"
        return "flat", details

    segment = valid[-window:]
    details["series"] = [round(v, 2) for v in segment]
    details["first"] = round(segment[0], 2)
    details["last"] = round(segment[-1], 2)

    n = len(segment)
    x_mean = (n - 1) / 2.0
    y_mean = sum(segment) / n
    num = sum((i - x_mean) * (segment[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    if den == 0:
        details["reason"] = "zero variance"
        return "flat", details

    slope = num / den
    # Normalize slope relative to value range in segment
    val_range = max(segment) - min(segment)
    magnitude = val_range if val_range > 0 else (abs(y_mean) if y_mean != 0 else 1.0)
    rel_slope = slope / magnitude if magnitude != 0 else 0

    # Also check absolute change from first to last
    abs_change = segment[-1] - segment[0]
    pct_change = (abs_change / abs(segment[0]) * 100) if segment[0] != 0 else 0

    details["slope"] = round(slope, 4)
    details["rel_slope"] = round(rel_slope, 4)
    details["change"] = round(abs_change, 2)
    details["change_pct"] = round(pct_change, 2)

    # Determine direction and strength
    abs_rel = abs(rel_slope)
    abs_pct = abs(pct_change)

    if abs_rel > 0.08 or abs_pct > 3.0:
        # Strength levels
        if abs_rel > 0.30 or abs_pct > 15.0:
            strength = "strong"
        elif abs_rel > 0.15 or abs_pct > 8.0:
            strength = "moderate"
        else:
            strength = "weak"
        details["strength"] = strength
        direction = "improving" if (rel_slope > 0 or pct_change > 3.0) else "worsening"
        return direction, details

    details["strength"] = None
    return "flat", details


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
    premium_received: Optional[float] = None,
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
    rsi_trend, rsi_trend_details = _compute_trend(series["rsi"])
    macd_trend, macd_trend_details = _compute_trend(series["macd"])
    adx_trend, adx_trend_details = _compute_trend(series["adx"])

    # GAP (snapshot convention: negative = OTM for put)
    gap_absolute = strike - underlying_price
    gap_percent = (gap_absolute / strike * 100.0) if strike else 0.0
    # For scoring: positive = OTM (same as CALL scorer approach)
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
    score_breakdown = [{"factor": "Base", "points": 50, "reason": "Starting score"}]

    # Delta
    if delta < 0.30:
        score += 13
        score_breakdown.append({"factor": "Delta", "points": 13, "reason": f"Δ {delta:.3f} < 0.30 (OTM)"})
        key_drivers.append(f"Delta {delta:.2f} favorable (OTM)")
    elif delta < 0.45:
        score_breakdown.append({"factor": "Delta", "points": 0, "reason": f"Δ {delta:.3f} in 0.30–0.45 (monitor)"})
    else:
        score -= 13
        score_breakdown.append({"factor": "Delta", "points": -13, "reason": f"Δ {delta:.3f} ≥ 0.45 (ATM critical)"})
        key_drivers.append(f"Delta {delta:.2f} ATM critical")
        rule_hits.append("delta_critical")

    # GAP (kept for snapshot/chart but NOT scored — redundant with Delta)
    score_breakdown.append({"factor": "GAP", "points": 0, "reason": f"Gap {gap_percent:.1f}% (informational, not scored)"})

    # RSI (for short put: low RSI = oversold = favorable for bounce)
    if rsi < 35:
        score += 10
        score_breakdown.append({"factor": "RSI level", "points": 10, "reason": f"RSI {rsi:.1f} < 35 (oversold)"})
        key_drivers.append(f"RSI {rsi:.1f} oversold (bounce likely)")
    elif rsi < 50:
        score += 4
        score_breakdown.append({"factor": "RSI level", "points": 4, "reason": f"RSI {rsi:.1f} in 35–50"})
    elif rsi <= 60:
        score_breakdown.append({"factor": "RSI level", "points": 0, "reason": f"RSI {rsi:.1f} in 50–60 (neutral)"})
    else:
        score -= 4
        score_breakdown.append({"factor": "RSI level", "points": -4, "reason": f"RSI {rsi:.1f} > 60"})

    # RSI trend (graduated by strength)
    rsi_strength = rsi_trend_details.get("strength")
    rsi_pct = rsi_trend_details.get('change_pct', 0)
    if rsi_trend == "improving":
        rsi_pts = {"strong": 10, "moderate": 7, "weak": 3}.get(rsi_strength, 3)
        score += rsi_pts
        score_breakdown.append({"factor": "RSI trend", "points": rsi_pts, "reason": f"RSI improving ({rsi_pct:+.1f}%, {rsi_strength})"})
        key_drivers.append(f"RSI improving ({rsi_strength})")
    elif rsi_trend == "worsening":
        rsi_pts = {"strong": -10, "moderate": -7, "weak": -3}.get(rsi_strength, -3)
        score += rsi_pts
        score_breakdown.append({"factor": "RSI trend", "points": rsi_pts, "reason": f"RSI worsening ({rsi_pct:+.1f}%, {rsi_strength})"})
        key_drivers.append(f"RSI worsening ({rsi_strength})")
        rule_hits.append("rsi_worsening")
    else:
        score_breakdown.append({"factor": "RSI trend", "points": 0, "reason": "RSI flat"})

    # MACD trend (graduated by strength)
    macd_strength = macd_trend_details.get("strength")
    macd_pct = macd_trend_details.get('change_pct', 0)
    if macd_trend == "improving":
        macd_pts = {"strong": 13, "moderate": 8, "weak": 4}.get(macd_strength, 4)
        score += macd_pts
        score_breakdown.append({"factor": "MACD trend", "points": macd_pts, "reason": f"MACD improving ({macd_pct:+.1f}%, {macd_strength})"})
        key_drivers.append(f"MACD improving ({macd_strength})")
    elif macd_trend == "worsening":
        macd_pts = {"strong": -13, "moderate": -8, "weak": -4}.get(macd_strength, -4)
        score += macd_pts
        score_breakdown.append({"factor": "MACD trend", "points": macd_pts, "reason": f"MACD worsening ({macd_pct:+.1f}%, {macd_strength})"})
        key_drivers.append(f"MACD worsening ({macd_strength})")
        rule_hits.append("macd_worsening")
    else:
        score_breakdown.append({"factor": "MACD trend", "points": 0, "reason": "MACD flat"})

    # ADX (graduated — direction-aware via MACD)
    adx_strength = adx_trend_details.get("strength")
    # For short put: MACD improving = stock rising = trend moves AWAY from strike
    trend_favorable = macd_trend == "improving"
    if adx < 20:
        score += 8
        score_breakdown.append({"factor": "ADX", "points": 8, "reason": f"ADX {adx:.1f} < 20 (no trend)"})
        key_drivers.append(f"ADX {adx:.1f} low (no trend)")
    elif adx <= 25:
        score += 4
        score_breakdown.append({"factor": "ADX", "points": 4, "reason": f"ADX {adx:.1f} in 20–25"})
    elif adx_trend == "worsening":  # ADX falling = trend weakening = good
        adx_pts = {"strong": 5, "moderate": 3, "weak": 2}.get(adx_strength, 3)
        score += adx_pts
        score_breakdown.append({"factor": "ADX", "points": adx_pts, "reason": f"ADX {adx:.1f} > 25 but falling ({adx_strength})"})
        key_drivers.append(f"ADX {adx:.1f} falling ({adx_strength})")
    elif adx_trend == "flat":  # ADX > 25 stable — existing trend not accelerating
        adx_pts = -4
        score += adx_pts
        score_breakdown.append({"factor": "ADX", "points": adx_pts, "reason": f"ADX {adx:.1f} > 25 flat (trend stable, not accelerating)"})
    elif trend_favorable:  # ADX rising but trend direction is favorable
        adx_pts = {"strong": -4, "moderate": -3, "weak": -2}.get(adx_strength, -3)
        score += adx_pts
        score_breakdown.append({"factor": "ADX", "points": adx_pts, "reason": f"ADX {adx:.1f} > 25 rising ({adx_strength}) but trend favorable"})
    else:  # ADX > 25 and rising with unfavorable trend
        adx_pts = {"strong": -13, "moderate": -10, "weak": -7}.get(adx_strength, -8)
        score += adx_pts
        score_breakdown.append({"factor": "ADX", "points": adx_pts, "reason": f"ADX {adx:.1f} > 25 rising ({adx_strength}) — unfavorable trend"})
        key_drivers.append(f"ADX {adx:.1f} rising ({adx_strength} trend)")
        rule_hits.append("adx_rising")

    # DTE (contextual: depends on moneyness)
    if dte > 21:
        dte_pts = 7
        dte_reason = f"DTE {dte} > 21 (time comfortable)"
    elif delta < 0.30:
        dte_pts = 8
        dte_reason = f"DTE {dte} short BUT OTM (Δ {delta:.2f}) — near expiry profit"
        key_drivers.append(f"DTE {dte} short + OTM (expiring safely)")
    elif delta < 0.45:
        dte_pts = -4
        dte_reason = f"DTE {dte} ≤ 21 + Δ {delta:.2f} (monitor zone, time pressure)"
        key_drivers.append(f"DTE {dte} short in monitor zone")
        rule_hits.append("dte_short_monitor")
    else:
        dte_pts = -10
        dte_reason = f"DTE {dte} ≤ 21 + Δ {delta:.2f} (ATM, high gamma risk)"
        key_drivers.append(f"DTE {dte} short + ATM critical")
        rule_hits.append("dte_short_atm")
    score += dte_pts
    score_breakdown.append({"factor": "DTE", "points": dte_pts, "reason": dte_reason})

    # Gamma penalty
    if gamma > 0.05 and delta >= 0.45:
        score -= 8
        score_breakdown.append({"factor": "Gamma", "points": -8, "reason": f"Γ {gamma:.3f} high + ATM"})
        rule_hits.append("high_gamma_atm")
    else:
        score_breakdown.append({"factor": "Gamma", "points": 0, "reason": f"Γ {gamma:.3f} (no penalty)"})

    # Theta (informational — replaced by P&L which is theta materialized)
    score_breakdown.append({"factor": "Theta", "points": 0, "reason": f"Θ {theta:.4f} (informational, not scored)"})

    # IV level (single value — no trend available)
    if iv > 0.50:
        iv_pts = 7
        iv_reason = f"IV {iv*100:.0f}% > 50% (high — favorable for short)"
    elif iv > 0.35:
        iv_pts = 4
        iv_reason = f"IV {iv*100:.0f}% in 35–50% (elevated)"
    elif iv > 0.20:
        iv_pts = 0
        iv_reason = f"IV {iv*100:.0f}% in 20–35% (normal)"
    elif iv > 0.10:
        iv_pts = -4
        iv_reason = f"IV {iv*100:.0f}% in 10–20% (low — less cushion)"
    else:
        iv_pts = -7
        iv_reason = f"IV {iv*100:.0f}% < 10% (very low — minimal premium)"
    score += iv_pts
    score_breakdown.append({"factor": "IV level", "points": iv_pts, "reason": iv_reason})

    # ── Combo Modifiers (cross-factor interactions) ──

    # #1 P&L factor: mark-to-market profitability
    pnl_pct = None
    if premium_received is not None and premium_received > 0:
        contract_mid = abs(greeks.get("mid") or 0)
        if contract_mid > 0:
            pnl_pct = (premium_received - contract_mid) / premium_received * 100
    if pnl_pct is not None:
        if pnl_pct >= 80:
            pnl_pts = 10
            pnl_reason = f"P&L {pnl_pct:.0f}% ≥ 80% (near max profit)"
        elif pnl_pct >= 60:
            pnl_pts = 7
            pnl_reason = f"P&L {pnl_pct:.0f}% ≥ 60% (strong profit)"
        elif pnl_pct >= 40:
            pnl_pts = 4
            pnl_reason = f"P&L {pnl_pct:.0f}% ≥ 40% (healthy profit)"
        elif pnl_pct >= 20:
            pnl_pts = 2
            pnl_reason = f"P&L {pnl_pct:.0f}% ≥ 20% (moderate profit)"
        elif pnl_pct >= 0:
            pnl_pts = 0
            pnl_reason = f"P&L {pnl_pct:.0f}% ≥ 0% (breakeven)"
        elif pnl_pct >= -20:
            pnl_pts = -4
            pnl_reason = f"P&L {pnl_pct:.0f}% in -20–0% (slight loss)"
        else:
            pnl_pts = -8
            pnl_reason = f"P&L {pnl_pct:.0f}% < -20% (significant loss)"
            rule_hits.append("pnl_significant_loss")
        score += pnl_pts
        score_breakdown.append({"factor": "P&L", "points": pnl_pts, "reason": pnl_reason})
        if pnl_pts != 0:
            key_drivers.append(pnl_reason)
    else:
        score_breakdown.append({"factor": "P&L", "points": 0, "reason": "P&L unavailable (no premium data)"})

    # #2 Combo P&L+DTE: near max profit + short DTE = close opportunity
    if pnl_pct is not None and pnl_pct >= 70 and dte <= 7:
        combo_pts = 5
        score += combo_pts
        score_breakdown.append({"factor": "Combo: P&L+DTE", "points": combo_pts,
                                "reason": f"P&L {pnl_pct:.0f}% + DTE {dte} — near max profit, consider closing"})
        key_drivers.append(f"P&L {pnl_pct:.0f}% + DTE {dte} (close opportunity)")
    elif pnl_pct is not None and pnl_pct < -20 and dte <= 14:
        combo_pts = -5
        score += combo_pts
        score_breakdown.append({"factor": "Combo: P&L+DTE", "points": combo_pts,
                                "reason": f"P&L {pnl_pct:.0f}% + DTE {dte} — loss + time pressure"})
        key_drivers.append(f"P&L {pnl_pct:.0f}% + DTE {dte} (loss + time pressure)")
        rule_hits.append("combo_pnl_dte_loss")
    else:
        score_breakdown.append({"factor": "Combo: P&L+DTE", "points": 0,
                                "reason": "Not triggered"})

    # #3 IV + DTE: High IV + short DTE = accelerated decay (exponential theta)
    if iv > 0.35 and dte <= 14 and delta < 0.45:
        combo_pts = 6
        score += combo_pts
        score_breakdown.append({"factor": "Combo: IV+DTE", "points": combo_pts,
                                "reason": f"IV {iv*100:.0f}% elevated + DTE {dte} short — accelerated decay"})
        key_drivers.append(f"IV {iv*100:.0f}% + DTE {dte} (decay acceleration)")
        rule_hits.append("combo_iv_dte_decay")
    else:
        score_breakdown.append({"factor": "Combo: IV+DTE", "points": 0,
                                "reason": "Not triggered"})

    # #4 MACD + RSI concordance: Both trends agree → signal confirmed
    if macd_trend == rsi_trend and macd_trend != "flat":
        macd_s = macd_trend_details.get("strength", "weak")
        rsi_s = rsi_trend_details.get("strength", "weak")
        strength_map = {"strong": 3, "moderate": 2, "weak": 1}
        avg_strength = (strength_map.get(macd_s, 1) + strength_map.get(rsi_s, 1)) / 2
        # For puts: improving = favorable, worsening = unfavorable
        if macd_trend == "improving":
            combo_pts = round(3 * avg_strength)
            score += combo_pts
            score_breakdown.append({"factor": "Combo: MACD+RSI", "points": combo_pts,
                                    "reason": f"MACD+RSI both improving — confirmed recovery"})
        else:
            combo_pts = -round(3 * avg_strength)
            score += combo_pts
            score_breakdown.append({"factor": "Combo: MACD+RSI", "points": combo_pts,
                                    "reason": f"MACD+RSI both worsening — confirmed deterioration"})
            rule_hits.append("combo_concordance_negative")
    else:
        score_breakdown.append({"factor": "Combo: MACD+RSI", "points": 0,
                                "reason": "Not triggered"})

    # #5 Delta + ADX unfavorable: Compound assignment risk
    if delta >= 0.45 and adx > 25 and adx_trend == "improving" and not trend_favorable:
        combo_pts = -5
        score += combo_pts
        score_breakdown.append({"factor": "Combo: Delta+ADX", "points": combo_pts,
                                "reason": f"Δ {delta:.2f} ATM + ADX {adx:.0f} rising unfavorable — compound risk"})
        rule_hits.append("combo_delta_adx_compound")
    else:
        score_breakdown.append({"factor": "Combo: Delta+ADX", "points": 0,
                                "reason": "Not triggered"})


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
        "score_breakdown": score_breakdown,
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
        "trend_analysis": {
            "rsi": rsi_trend_details,
            "macd": macd_trend_details,
            "adx": adx_trend_details,
            "snapshots_count": len(snapshots),
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
    premium_received: Optional[float] = None,
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
    rsi_trend, rsi_trend_details = _compute_trend(series["rsi"])
    macd_trend, macd_trend_details = _compute_trend(series["macd"])
    adx_trend, adx_trend_details = _compute_trend(series["adx"])

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
    score_breakdown = [{"factor": "Base", "points": 50, "reason": "Starting score"}]

    # Delta
    if delta < 0.30:
        score += 13
        score_breakdown.append({"factor": "Delta", "points": 13, "reason": f"Δ {delta:.3f} < 0.30 (OTM)"})
        key_drivers.append(f"Delta {delta:.2f} favorable (OTM)")
    elif delta < 0.45:
        score_breakdown.append({"factor": "Delta", "points": 0, "reason": f"Δ {delta:.3f} in 0.30–0.45 (monitor)"})
    else:
        score -= 13
        score_breakdown.append({"factor": "Delta", "points": -13, "reason": f"Δ {delta:.3f} ≥ 0.45 (ATM critical)"})
        key_drivers.append(f"Delta {delta:.2f} ATM critical")
        rule_hits.append("delta_critical")

    # GAP (kept for snapshot/chart but NOT scored — redundant with Delta)
    score_breakdown.append({"factor": "GAP", "points": 0, "reason": f"Gap {otm_gap:.1f}% (informational, not scored)"})

    # RSI (for short call: high RSI = overbought = favorable for pullback)
    if rsi > 65:
        score += 10
        score_breakdown.append({"factor": "RSI level", "points": 10, "reason": f"RSI {rsi:.1f} > 65 (overbought)"})
        key_drivers.append(f"RSI {rsi:.1f} overbought (pullback likely)")
    elif rsi >= 50:
        score += 4
        score_breakdown.append({"factor": "RSI level", "points": 4, "reason": f"RSI {rsi:.1f} in 50–65"})
    elif rsi >= 40:
        score_breakdown.append({"factor": "RSI level", "points": 0, "reason": f"RSI {rsi:.1f} in 40–50 (neutral)"})
    else:
        score -= 4
        score_breakdown.append({"factor": "RSI level", "points": -4, "reason": f"RSI {rsi:.1f} < 40"})

    # RSI trend (for calls: weakening RSI = favorable — graduated)
    rsi_strength = rsi_trend_details.get("strength")
    rsi_pct = rsi_trend_details.get('change_pct', 0)
    if rsi_trend == "worsening":  # RSI falling = good for short call
        rsi_pts = {"strong": 10, "moderate": 7, "weak": 3}.get(rsi_strength, 3)
        score += rsi_pts
        score_breakdown.append({"factor": "RSI trend", "points": rsi_pts, "reason": f"RSI weakening ({rsi_pct:+.1f}%, {rsi_strength}) — favorable"})
        key_drivers.append(f"RSI weakening ({rsi_strength})")
    elif rsi_trend == "improving":  # RSI rising = bad for short call
        rsi_pts = {"strong": -10, "moderate": -7, "weak": -3}.get(rsi_strength, -3)
        score += rsi_pts
        score_breakdown.append({"factor": "RSI trend", "points": rsi_pts, "reason": f"RSI strengthening ({rsi_pct:+.1f}%, {rsi_strength}) — unfavorable"})
        key_drivers.append(f"RSI strengthening ({rsi_strength})")
        rule_hits.append("rsi_strengthening")
    else:
        score_breakdown.append({"factor": "RSI trend", "points": 0, "reason": "RSI flat"})

    # MACD trend (for calls: weakening MACD = favorable — graduated)
    macd_strength = macd_trend_details.get("strength")
    macd_pct = macd_trend_details.get('change_pct', 0)
    if macd_trend == "worsening":  # MACD falling = good for short call
        macd_pts = {"strong": 13, "moderate": 8, "weak": 4}.get(macd_strength, 4)
        score += macd_pts
        score_breakdown.append({"factor": "MACD trend", "points": macd_pts, "reason": f"MACD weakening ({macd_pct:+.1f}%, {macd_strength}) — favorable"})
        key_drivers.append(f"MACD weakening ({macd_strength})")
    elif macd_trend == "improving":  # MACD rising = bad for short call
        macd_pts = {"strong": -13, "moderate": -8, "weak": -4}.get(macd_strength, -4)
        score += macd_pts
        score_breakdown.append({"factor": "MACD trend", "points": macd_pts, "reason": f"MACD improving ({macd_pct:+.1f}%, {macd_strength}) — unfavorable"})
        key_drivers.append(f"MACD improving ({macd_strength})")
        rule_hits.append("macd_improving")
    else:
        score_breakdown.append({"factor": "MACD trend", "points": 0, "reason": "MACD flat"})

    # ADX (graduated — direction-aware via MACD)
    adx_strength = adx_trend_details.get("strength")
    # For short call: MACD worsening = stock falling = trend moves AWAY from strike
    trend_favorable = macd_trend == "worsening"
    if adx < 20:
        score += 8
        score_breakdown.append({"factor": "ADX", "points": 8, "reason": f"ADX {adx:.1f} < 20 (no trend)"})
        key_drivers.append(f"ADX {adx:.1f} low (no trend)")
    elif adx <= 25:
        score += 4
        score_breakdown.append({"factor": "ADX", "points": 4, "reason": f"ADX {adx:.1f} in 20–25"})
    elif adx_trend == "worsening":  # ADX falling = trend weakening = good
        adx_pts = {"strong": 5, "moderate": 3, "weak": 2}.get(adx_strength, 3)
        score += adx_pts
        score_breakdown.append({"factor": "ADX", "points": adx_pts, "reason": f"ADX {adx:.1f} > 25 but falling ({adx_strength})"})
        key_drivers.append(f"ADX {adx:.1f} falling ({adx_strength})")
    elif adx_trend == "flat":  # ADX > 25 stable — existing trend not accelerating
        adx_pts = -4
        score += adx_pts
        score_breakdown.append({"factor": "ADX", "points": adx_pts, "reason": f"ADX {adx:.1f} > 25 flat (trend stable, not accelerating)"})
    elif trend_favorable:  # ADX rising but trend direction is favorable
        adx_pts = {"strong": -4, "moderate": -3, "weak": -2}.get(adx_strength, -3)
        score += adx_pts
        score_breakdown.append({"factor": "ADX", "points": adx_pts, "reason": f"ADX {adx:.1f} > 25 rising ({adx_strength}) but trend favorable"})
    else:  # ADX > 25 and rising with unfavorable trend
        adx_pts = {"strong": -13, "moderate": -10, "weak": -7}.get(adx_strength, -8)
        score += adx_pts
        score_breakdown.append({"factor": "ADX", "points": adx_pts, "reason": f"ADX {adx:.1f} > 25 rising ({adx_strength}) — unfavorable trend"})
        key_drivers.append(f"ADX {adx:.1f} rising ({adx_strength} trend)")
        rule_hits.append("adx_rising")

    # DTE (contextual: depends on moneyness)
    if dte > 21:
        dte_pts = 7
        dte_reason = f"DTE {dte} > 21 (time comfortable)"
    elif delta < 0.30:
        dte_pts = 8
        dte_reason = f"DTE {dte} short BUT OTM (Δ {delta:.2f}) — near expiry profit"
        key_drivers.append(f"DTE {dte} short + OTM (expiring safely)")
    elif delta < 0.45:
        dte_pts = -4
        dte_reason = f"DTE {dte} ≤ 21 + Δ {delta:.2f} (monitor zone, time pressure)"
        key_drivers.append(f"DTE {dte} short in monitor zone")
        rule_hits.append("dte_short_monitor")
    else:
        dte_pts = -10
        dte_reason = f"DTE {dte} ≤ 21 + Δ {delta:.2f} (ATM, high gamma risk)"
        key_drivers.append(f"DTE {dte} short + ATM critical")
        rule_hits.append("dte_short_atm")
    score += dte_pts
    score_breakdown.append({"factor": "DTE", "points": dte_pts, "reason": dte_reason})

    # Gamma penalty
    if gamma > 0.05 and delta >= 0.45:
        score -= 8
        score_breakdown.append({"factor": "Gamma", "points": -8, "reason": f"Γ {gamma:.3f} high + ATM"})
        rule_hits.append("high_gamma_atm")
    else:
        score_breakdown.append({"factor": "Gamma", "points": 0, "reason": f"Γ {gamma:.3f} (no penalty)"})

    # Theta (informational — replaced by P&L which is theta materialized)
    score_breakdown.append({"factor": "Theta", "points": 0, "reason": f"Θ {theta:.4f} (informational, not scored)"})

    # IV level (single value — no trend available)
    if iv > 0.50:
        iv_pts = 7
        iv_reason = f"IV {iv*100:.0f}% > 50% (high — favorable for short)"
    elif iv > 0.35:
        iv_pts = 4
        iv_reason = f"IV {iv*100:.0f}% in 35–50% (elevated)"
    elif iv > 0.20:
        iv_pts = 0
        iv_reason = f"IV {iv*100:.0f}% in 20–35% (normal)"
    elif iv > 0.10:
        iv_pts = -4
        iv_reason = f"IV {iv*100:.0f}% in 10–20% (low — less cushion)"
    else:
        iv_pts = -7
        iv_reason = f"IV {iv*100:.0f}% < 10% (very low — minimal premium)"
    score += iv_pts
    score_breakdown.append({"factor": "IV level", "points": iv_pts, "reason": iv_reason})

    # ── Combo Modifiers (cross-factor interactions) ──

    # #1 P&L factor: mark-to-market profitability
    pnl_pct = None
    if premium_received is not None and premium_received > 0:
        contract_mid = abs(greeks.get("mid") or 0)
        if contract_mid > 0:
            pnl_pct = (premium_received - contract_mid) / premium_received * 100
    if pnl_pct is not None:
        if pnl_pct >= 80:
            pnl_pts = 10
            pnl_reason = f"P&L {pnl_pct:.0f}% ≥ 80% (near max profit)"
        elif pnl_pct >= 60:
            pnl_pts = 7
            pnl_reason = f"P&L {pnl_pct:.0f}% ≥ 60% (strong profit)"
        elif pnl_pct >= 40:
            pnl_pts = 4
            pnl_reason = f"P&L {pnl_pct:.0f}% ≥ 40% (healthy profit)"
        elif pnl_pct >= 20:
            pnl_pts = 2
            pnl_reason = f"P&L {pnl_pct:.0f}% ≥ 20% (moderate profit)"
        elif pnl_pct >= 0:
            pnl_pts = 0
            pnl_reason = f"P&L {pnl_pct:.0f}% ≥ 0% (breakeven)"
        elif pnl_pct >= -20:
            pnl_pts = -4
            pnl_reason = f"P&L {pnl_pct:.0f}% in -20–0% (slight loss)"
        else:
            pnl_pts = -8
            pnl_reason = f"P&L {pnl_pct:.0f}% < -20% (significant loss)"
            rule_hits.append("pnl_significant_loss")
        score += pnl_pts
        score_breakdown.append({"factor": "P&L", "points": pnl_pts, "reason": pnl_reason})
        if pnl_pts != 0:
            key_drivers.append(pnl_reason)
    else:
        score_breakdown.append({"factor": "P&L", "points": 0, "reason": "P&L unavailable (no premium data)"})

    # #2 Combo P&L+DTE: near max profit + short DTE = close opportunity
    if pnl_pct is not None and pnl_pct >= 70 and dte <= 7:
        combo_pts = 5
        score += combo_pts
        score_breakdown.append({"factor": "Combo: P&L+DTE", "points": combo_pts,
                                "reason": f"P&L {pnl_pct:.0f}% + DTE {dte} — near max profit, consider closing"})
        key_drivers.append(f"P&L {pnl_pct:.0f}% + DTE {dte} (close opportunity)")
    elif pnl_pct is not None and pnl_pct < -20 and dte <= 14:
        combo_pts = -5
        score += combo_pts
        score_breakdown.append({"factor": "Combo: P&L+DTE", "points": combo_pts,
                                "reason": f"P&L {pnl_pct:.0f}% + DTE {dte} — loss + time pressure"})
        key_drivers.append(f"P&L {pnl_pct:.0f}% + DTE {dte} (loss + time pressure)")
        rule_hits.append("combo_pnl_dte_loss")
    else:
        score_breakdown.append({"factor": "Combo: P&L+DTE", "points": 0,
                                "reason": "Not triggered"})

    # #3 IV + DTE: High IV + short DTE = accelerated decay (exponential theta)
    if iv > 0.35 and dte <= 14 and delta < 0.45:
        combo_pts = 6
        score += combo_pts
        score_breakdown.append({"factor": "Combo: IV+DTE", "points": combo_pts,
                                "reason": f"IV {iv*100:.0f}% elevated + DTE {dte} short — accelerated decay"})
        key_drivers.append(f"IV {iv*100:.0f}% + DTE {dte} (decay acceleration)")
        rule_hits.append("combo_iv_dte_decay")
    else:
        score_breakdown.append({"factor": "Combo: IV+DTE", "points": 0,
                                "reason": "Not triggered"})

    # #4 MACD + RSI concordance: Both trends agree → signal confirmed
    if macd_trend == rsi_trend and macd_trend != "flat":
        macd_s = macd_trend_details.get("strength", "weak")
        rsi_s = rsi_trend_details.get("strength", "weak")
        strength_map = {"strong": 3, "moderate": 2, "weak": 1}
        avg_strength = (strength_map.get(macd_s, 1) + strength_map.get(rsi_s, 1)) / 2
        # For calls: worsening = favorable (stock falling), improving = unfavorable
        if macd_trend == "worsening":
            combo_pts = round(3 * avg_strength)
            score += combo_pts
            score_breakdown.append({"factor": "Combo: MACD+RSI", "points": combo_pts,
                                    "reason": f"MACD+RSI both weakening — confirmed pullback"})
        else:
            combo_pts = -round(3 * avg_strength)
            score += combo_pts
            score_breakdown.append({"factor": "Combo: MACD+RSI", "points": combo_pts,
                                    "reason": f"MACD+RSI both strengthening — confirmed rally"})
            rule_hits.append("combo_concordance_negative")
    else:
        score_breakdown.append({"factor": "Combo: MACD+RSI", "points": 0,
                                "reason": "Not triggered"})

    # #5 Delta + ADX unfavorable: Compound assignment risk
    if delta >= 0.45 and adx > 25 and adx_trend == "improving" and not trend_favorable:
        combo_pts = -5
        score += combo_pts
        score_breakdown.append({"factor": "Combo: Delta+ADX", "points": combo_pts,
                                "reason": f"Δ {delta:.2f} ATM + ADX {adx:.0f} rising unfavorable — compound risk"})
        rule_hits.append("combo_delta_adx_compound")
    else:
        score_breakdown.append({"factor": "Combo: Delta+ADX", "points": 0,
                                "reason": "Not triggered"})


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
        "score_breakdown": score_breakdown,
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
        "trend_analysis": {
            "rsi": rsi_trend_details,
            "macd": macd_trend_details,
            "adx": adx_trend_details,
            "snapshots_count": len(snapshots),
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
    premium_received: Optional[float] = None,
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
        premium_received: Original premium received when position was opened.

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
        result = score_short_put(greeks, snapshots, strike, expiration, underlying_price, premium_received=premium_received)
    else:
        result = score_short_call(greeks, snapshots, strike, expiration, underlying_price, premium_received=premium_received)

    result["ticker"] = symbol
    return result
