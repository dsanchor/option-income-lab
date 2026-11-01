"""Causal CALL/CSP timing rubric v1; not calibrated or profitability validated.

All indicators use adjusted daily OHLCV, including the in-progress daily bar.
Wilder ATR/RSI/ADX are seeded explicitly; no optional indicator libraries.
The five independent dimensions are location (25%), structure (20%), momentum
evolution (25%), multi-session confirmation (20%), and realized stability (10%).
Scores are context for covered calls/CSP, not probabilities or contract advice.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import numpy as np
import pandas as pd

MODEL_VERSION = "option-timing-1.0.0"
SCHEMA_VERSION = 1
MIN_BARS = 220
DEFAULT_CRON = "0 14,17,20 * * 1-5"
METHODOLOGY = (
    "Initial conservative deterministic rubric, not calibrated or profitability "
    "validated. Covered CALL and cash-secured PUT timing are distinct contexts, "
    "not probabilities, profit forecasts, or contract recommendations. Adjusted "
    "daily OHLCV; 220-bar warmup; Wilder ATR14/RSI14/ADX14 and SMA50/200. "
    "Prior-bar-only 20-session support/resistance. Weights: localization 25%, "
    "trend 20%, momentum evolution 25%, temporal confirmation 20%, realized "
    "stability 10%. Weighted sum followed by explained risk caps. Historical "
    "daily closes are reconstructed causally, not invented intraday observations. "
    "Live input timestamps are Yahoo daily-bar retrieval cutoffs, not exchange "
    "trade timestamps; in-progress bars are provisional. US session calendar "
    "uses America/New_York, standard NYSE holidays and scheduled early closes."
)
WEIGHTS = (
    ("localization", "Price localization", .25),
    ("trend", "Trend structure", .20),
    ("momentum_evolution", "Momentum evolution", .25),
    ("temporal_confirmation", "Temporal confirmation", .20),
    ("realized_stability", "Realized stability", .10),
)


class TimingInputError(ValueError):
    """A required causal input is absent or invalid."""


def band(score: float | None) -> str | None:
    if score is None:
        return None
    return ("red" if score < 25 else "orange" if score < 50 else
            "yellow" if score < 75 else "green")


def _clip(value: float) -> float:
    return float(np.clip(value, 0, 100))


def _wilder(values: np.ndarray, period: int = 14) -> np.ndarray:
    result = np.full(len(values), np.nan)
    valid = np.flatnonzero(np.isfinite(values))
    if len(valid) < period:
        return result
    start = int(valid[0])
    seed = start + period - 1
    result[seed] = np.mean(values[start:seed + 1])
    for i in range(seed + 1, len(values)):
        result[i] = (result[i - 1] * (period - 1) + values[i]) / period
    return result


def features(history: pd.DataFrame) -> dict:
    """Extract only from the supplied slice; adding future bars cannot affect it."""
    if history is None or len(history) < MIN_BARS:
        raise TimingInputError("warmup_requires_220_valid_bars")
    columns = ["Open", "High", "Low", "Close", "Volume"]
    if not all(c in history for c in columns):
        raise TimingInputError("missing_ohlcv")
    try:
        data = history[columns].to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise TimingInputError("invalid_ohlcv") from exc
    if not np.isfinite(data).all():
        raise TimingInputError("non_finite_ohlcv")
    o, h, low, c, volume = data.T
    if (np.any(data[:, :4] <= 0) or np.any(volume < 0) or
            np.any(h < np.maximum(o, c)) or np.any(low > np.minimum(o, c)) or
            np.any(h < low) or np.sum(volume[-20:]) <= 0):
        raise TimingInputError("invalid_ohlcv_geometry_or_volume")
    if np.any(volume[-20:] <= 0):
        raise TimingInputError("missing_volume_context")
    if not history.index.is_monotonic_increasing or history.index.has_duplicates:
        raise TimingInputError("unordered_or_duplicate_sessions")
    previous = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - low, np.maximum(abs(h - previous), abs(low - previous)))
    atr = _wilder(tr)
    delta = np.r_[np.nan, np.diff(c)]
    gain = _wilder(np.maximum(delta, 0))
    loss = _wilder(np.maximum(-delta, 0))
    rsi = np.full(len(c), 50.0)
    np.divide(100 * gain, gain + loss, out=rsi, where=(gain + loss) > 0)
    up = np.r_[0, np.diff(h)]
    down = np.r_[0, -np.diff(low)]
    plus = 100 * _wilder(np.where((up > down) & (up > 0), up, 0)) / atr
    minus = 100 * _wilder(np.where((down > up) & (down > 0), down, 0)) / atr
    dx = np.zeros(len(c))
    np.divide(100 * abs(plus - minus), plus + minus, out=dx, where=(plus + minus) > 0)
    dx[~np.isfinite(plus + minus)] = np.nan
    adx = _wilder(dx)
    sma50 = pd.Series(c).rolling(50).mean().to_numpy()
    sma200 = pd.Series(c).rolling(200).mean().to_numpy()
    a = float(atr[-2])  # Prior ATR cannot be inflated by today's abrupt event.
    if not np.isfinite(a) or a <= 0:
        raise TimingInputError("missing_or_zero_prior_atr")
    support = float(np.min(low[-21:-1]))
    resistance = float(np.max(h[-21:-1]))
    support_by_bar = pd.Series(low).shift(1).rolling(20).min().to_numpy()
    resistance_by_bar = pd.Series(h).shift(1).rolling(20).max().to_numpy()
    atr_by_bar = np.r_[np.nan, atr[:-1]]
    recent = slice(-3, None)
    support_holds = float(np.mean(
        (c[recent] >= support_by_bar[recent] - .25 * atr_by_bar[recent]) &
        (c[recent] >= previous[recent] - .1 * atr_by_bar[recent])
    ))
    contained = float(np.mean(c[recent] <= resistance_by_bar[recent] + .25 * atr_by_bar[recent]))
    sell_volume = volume * (c < previous)
    old_pressure = float(np.sum(sell_volume[-10:-5]) / np.sum(volume[-10:-5])) if np.sum(volume[-10:-5]) > 0 else None
    new_pressure = float(np.sum(sell_volume[-5:]) / np.sum(volume[-5:])) if np.sum(volume[-5:]) > 0 else None
    if old_pressure is None or new_pressure is None:
        raise TimingInputError("missing_volume_context")
    result = {
        "close": float(c[-1]), "atr14": float(atr[-1]), "prior_atr14": a,
        "support20": support, "resistance20": resistance,
        "support_distance_atr": float((c[-1] - support) / a),
        "resistance_distance_atr": float((resistance - c[-1]) / a),
        "sma50": float(sma50[-1]), "sma200": float(sma200[-1]),
        "sma50_slope20_atr": float((sma50[-1] - sma50[-21]) / (20 * a)),
        "sma200_slope20_atr": float((sma200[-1] - sma200[-21]) / (20 * a)),
        "rsi14": float(rsi[-1]), "rsi14_change5": float(rsi[-1] - rsi[-6]),
        "return5_atr": float((c[-1] - c[-6]) / a),
        "previous_return5_atr": float((c[-6] - c[-11]) / a),
        "change1_atr": float((c[-1] - c[-2]) / a),
        "adx14": float(adx[-1]), "adx14_change5": float(adx[-1] - adx[-6]),
        "sell_pressure5": new_pressure, "sell_pressure_change5": new_pressure - old_pressure,
        "support_holds3": support_holds, "contained_closes3": contained,
        "rejection_wick_atr": float((h[-1] - max(o[-1], c[-1])) / a),
        "defense_wick_atr": float((min(o[-1], c[-1]) - low[-1]) / a),
        "range1_prior_atr": float(tr[-1] / a),
        "atr_ratio": float(atr[-1] / atr[-21]),
        "realized_std20_atr": float(np.std(np.diff(c[-21:]), ddof=1) / a),
        "below_sma200_atr": float((sma200[-1] - c[-1]) / a),
        "above_sma50_atr": float((c[-1] - sma50[-1]) / a),
        "support_losses2": bool(np.all(
            c[-2:] < support_by_bar[-2:] - .5 * atr_by_bar[-2:]
        )),
        "resistance_breaks2": bool(np.all(
            c[-2:] > resistance_by_bar[-2:] + .25 * atr_by_bar[-2:]
        )),
    }
    if not all(np.isfinite(v) for v in result.values()):
        raise TimingInputError("missing_critical_indicator")
    return result


def calculate_sides(history: pd.DataFrame) -> dict:
    f = features(history)
    a = f["prior_atr14"]
    rising = f["sma50_slope20_atr"]
    long_slope = f["sma200_slope20_atr"]
    acceleration = f["return5_atr"] - f["previous_return5_atr"]
    rejection = min(f["rejection_wick_atr"], 1)
    defense = min(f["defense_wick_atr"], 1)
    structure = _clip(65 + 150 * rising + 150 * long_slope
                      - 8 * max(f["below_sma200_atr"], 0))
    stable = _clip(100 - 35 * max(f["atr_ratio"] - 1, 0)
                   - 30 * max(f["range1_prior_atr"] - 1.5, 0)
                   - 40 * max(f["realized_std20_atr"] - .5, 0))
    call_values = [
        _clip(100 - 30 * abs(f["resistance_distance_atr"])),
        _clip(80 - 120 * max(-rising, 0) - 100 * max(rising - .15, 0)
              - 8 * max(f["below_sma200_atr"], 0)),
        _clip(65 - 2 * f["rsi14_change5"] - 10 * max(acceleration, 0)),
        _clip(80 * f["contained_closes3"] + 20 * rejection),
        stable,
    ]
    put_values = [
        _clip(100 - 30 * abs(f["support_distance_atr"])),
        structure,
        _clip(55 + 2 * f["rsi14_change5"] - 50 * f["sell_pressure_change5"]),
        _clip(80 * f["support_holds3"] + 20 * defense),
        stable,
    ]
    shared = {
        "trend": (
            f"SMA50={f['sma50']:.4f}; SMA200={f['sma200']:.4f}; "
            f"SMA50 20-session slope={rising:.4f} ATR/session; "
            f"SMA200 slope={long_slope:.4f} ATR/session; "
            f"distance below SMA200={f['below_sma200_atr']:.4f} prior ATR."
        ),
        "realized_stability": (
            f"ATR14={f['atr14']:.4f}; prior ATR14={a:.4f}; "
            f"ATR/ATR 20 sessions ago={f['atr_ratio']:.4f}; "
            f"current true range/prior ATR={f['range1_prior_atr']:.4f}; "
            f"20-session price-change standard deviation/prior ATR={f['realized_std20_atr']:.4f}."
        ),
    }
    evidence = {
        "covered_call": [
            f"Close={f['close']:.4f}; prior resistance20={f['resistance20']:.4f}; distance={f['resistance_distance_atr']:.4f} prior ATR; rejection wick={rejection:.4f} ATR.",
            shared["trend"],
            f"RSI14={f['rsi14']:.4f}; 5-session RSI change={f['rsi14_change5']:+.4f}; return5={f['return5_atr']:.4f} ATR; previous return5={f['previous_return5_atr']:.4f} ATR; acceleration={acceleration:+.4f} ATR.",
            f"Contained closes in last 3 sessions={f['contained_closes3'] * 3:.0f}/3 (each relative to its own prior resistance + 0.25 prior ATR); rejection wick={rejection:.4f} ATR.",
            shared["realized_stability"],
        ],
        "cash_secured_put": [
            f"Close={f['close']:.4f}; prior support20={f['support20']:.4f}; distance={f['support_distance_atr']:.4f} prior ATR; defense wick={defense:.4f} ATR.",
            shared["trend"],
            f"RSI14={f['rsi14']:.4f}; 5-session RSI change={f['rsi14_change5']:+.4f}; sell-volume share5={f['sell_pressure5']:.4f}; change vs previous 5 sessions={f['sell_pressure_change5']:+.4f}.",
            f"Defended/reclaimed closes in last 3 sessions={f['support_holds3'] * 3:.0f}/3 (above own prior support - 0.25 prior ATR and no decline >0.1 prior ATR); defense wick={defense:.4f} ATR.",
            shared["realized_stability"],
        ],
    }
    transformations = {
        "covered_call": [
            "clip(100 - 30*abs(resistance distance))",
            "clip(80 - 120*max(-SMA50 slope,0) - 100*max(SMA50 slope-.15,0) - 8*max(distance below SMA200,0))",
            "clip(65 - 2*RSI change5 - 10*max(return5-previous return5,0))",
            "clip(80*contained fraction3 + 20*min(rejection wick,1))",
            "clip(100 - 35*max(ATR ratio-1,0) - 30*max(range/prior ATR-1.5,0) - 40*max(realized std/prior ATR-.5,0))",
        ],
        "cash_secured_put": [
            "clip(100 - 30*abs(support distance))",
            "clip(65 + 150*SMA50 slope + 150*SMA200 slope - 8*max(distance below SMA200,0))",
            "clip(55 + 2*RSI change5 - 50*sell-pressure change5)",
            "clip(80*support-held fraction3 + 20*min(defense wick,1))",
            "clip(100 - 35*max(ATR ratio-1,0) - 30*max(range/prior ATR-1.5,0) - 40*max(realized std/prior ATR-.5,0))",
        ],
    }
    severe = (f["below_sma200_atr"] > 2 and rising < -.1) or (
        f["change1_atr"] < -3 and f["range1_prior_atr"] > 3
    )
    breakout = (
        f["resistance_distance_atr"] < -.5 and f["return5_atr"] > 2 and
        (f["resistance_breaks2"] or
         (f["change1_atr"] > 1.5 and f["adx14"] >= 25 and f["adx14_change5"] > 0))
    )
    support_loss = f["support_losses2"] or (
        f["support_distance_atr"] < -.5 and f["change1_atr"] < -1.5
    )
    falling_knife = (
        f["return5_atr"] < -1 and f["rsi14"] < 35 and
        f["rsi14_change5"] < 0 and
        (f["below_sma200_atr"] > 1 or
         (rising < -.1 and f["above_sma50_atr"] < -1))
    )
    out = {}
    for side, values in (("covered_call", call_values), ("cash_secured_put", put_values)):
        factors = [
            {"key": key, "label": label, "score": round(value, 4), "weight": weight,
             "contribution": round(value * weight, 4),
             "evidence": [ev, "Transformation: " + formula + "; clip to 0..100."]}
            for (key, label, weight), value, ev, formula in
            zip(WEIGHTS, values, evidence[side], transformations[side])
        ]
        adjustments = []
        if severe:
            adjustments.append({
                "key": "severe_structural_deterioration", "label": "Structural deterioration",
                "reason": f"Below SMA200={f['below_sma200_atr']:.4f} ATR, SMA50 slope={rising:.4f}; one-session change={f['change1_atr']:.4f} prior ATR, range={f['range1_prior_atr']:.4f} prior ATR. Cap when >2 ATR below SMA200 with slope < -0.1, or change < -3 ATR with range >3 ATR.",
                "cap": 15,
            })
        if side == "covered_call" and breakout:
            adjustments.append({
                "key": "accelerated_breakout", "label": "Accelerated upward breakout",
                "reason": f"Resistance distance={f['resistance_distance_atr']:.4f} prior ATR, return5={f['return5_atr']:.4f} ATR, two prior-resistance breaks={f['resistance_breaks2']}; change1={f['change1_atr']:.4f} ATR, ADX14={f['adx14']:.4f}, ADX change5={f['adx14_change5']:.4f}. Requires >0.5 ATR above resistance and return5>2 ATR, plus two breaks or change1>1.5 ATR with ADX>=25 rising.",
                "cap": 20,
            })
        if side == "cash_secured_put" and support_loss:
            adjustments.append({
                "key": "confirmed_support_loss", "label": "Confirmed support loss",
                "reason": f"Support distance={f['support_distance_atr']:.4f} prior ATR; change1={f['change1_atr']:.4f} prior ATR; two support losses={f['support_losses2']}. Cap after two closes >0.5 ATR below their prior support, or a >1.5 ATR decline closing >0.5 ATR below support.",
                "cap": 20,
            })
        if side == "cash_secured_put" and falling_knife:
            adjustments.append({
                "key": "falling_knife", "label": "Decline not yet stabilizing",
                "reason": f"Return5={f['return5_atr']:.4f} prior ATR; RSI14={f['rsi14']:.4f}; RSI change5={f['rsi14_change5']:+.4f}; below SMA200={f['below_sma200_atr']:.4f} ATR; SMA50 slope={rising:.4f}; above SMA50={f['above_sma50_atr']:.4f} ATR. Requires return5<-1 ATR, RSI<35 and declining, plus >1 ATR below SMA200 or SMA50 slope<-.1 with close >1 ATR below SMA50.",
                "cap": 20,
            })
        base = sum(factor["contribution"] for factor in factors)
        score = round(min([base] + [x["cap"] for x in adjustments]), 2)
        out[side] = {
            "score": score, "band": band(score),
            "label": f"{'CALL' if side == 'covered_call' else 'PUT'} Timing",
            "status": "available", "reason_codes": [x["key"] for x in adjustments],
            "factors": factors, "adjustments": adjustments, "base_score": round(base, 4),
        }
    return out


def empty_sides(reason: str, applicable: bool = True) -> dict:
    return {
        side: {"score": None, "band": None, "label": label,
               "status": "unavailable" if applicable else "not_applicable",
               "reason_codes": [reason], "factors": [], "adjustments": []}
        for side, label in (("covered_call", "CALL Timing"),
                            ("cash_secured_put", "PUT Timing"))
    }


def snapshot(symbol: str, sides: dict, as_of: datetime, computed_at: datetime,
             source_session: str, provisional: bool, history_kind: str = "observed") -> dict:
    timestamp = as_of.astimezone(timezone.utc).isoformat()
    identity = f"{symbol}|{MODEL_VERSION}|{timestamp}|{history_kind}|{provisional}"
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": hashlib.sha256(identity.encode()).hexdigest()[:32],
        "model_version": MODEL_VERSION, "as_of": timestamp,
        "computed_at": computed_at.astimezone(timezone.utc).isoformat(),
        "source_session": source_session, "provisional": provisional,
        "freshness": "fresh" if any(v["status"] == "available" for v in sides.values()) else "unknown",
        "history_kind": history_kind, **sides,
    }
