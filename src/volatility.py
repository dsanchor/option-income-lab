"""Volatility helpers: realized (historical) volatility and IV/HV richness.

These are pure, dependency-light functions used to give the options agents a
sense of whether option premium is *rich* or *cheap* relative to how much the
underlying actually moves.

Why IV/HV instead of IV Rank?
    IV Rank / IV Percentile require a long (~1 year) stored history of implied
    volatility, which Yahoo/yfinance does NOT provide and which resets to zero
    every time a new symbol is added. The IV/HV ratio answers the same
    practical question — "is option premium expensive vs. realized movement?" —
    using only data available *today* (implied vol from the current chain,
    realized vol from the price history). It is therefore stateless and works
    from the very first analysis of any symbol.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Optional, Sequence

# Annualization factor: trading days per year.
TRADING_DAYS = 252

# IV/HV richness thresholds. Ratio = implied / realized.
RICH_THRESHOLD = 1.20   # IV >= 1.2x HV → premium is rich (favours selling)
CHEAP_THRESHOLD = 0.90  # IV < 0.9x HV → premium is cheap (avoid selling)


def historical_volatility(
    closes: Sequence[float],
    window: int = 20,
) -> Optional[float]:
    """Annualized close-to-close realized volatility (decimal, e.g. 0.28).

    Uses the most recent ``window`` daily log returns. Returns ``None`` when
    there is not enough clean data to compute at least 2 returns.
    """
    if closes is None:
        return None
    clean = [float(c) for c in closes if c is not None and _is_finite(c) and float(c) > 0]
    if len(clean) < 3:
        return None

    # Daily log returns.
    returns = []
    for prev, cur in zip(clean[:-1], clean[1:]):
        try:
            returns.append(math.log(cur / prev))
        except (ValueError, ZeroDivisionError):
            continue
    if len(returns) < 2:
        return None

    if window and window > 0:
        returns = returns[-window:]
    if len(returns) < 2:
        return None

    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    daily_std = math.sqrt(variance)
    return daily_std * math.sqrt(TRADING_DAYS)


def _parse_exp_key(exp_key: str) -> Optional[date]:
    """Parse an expiration key (``YYYYMMDD`` or ``YYYY-MM-DD``) to a date."""
    if not exp_key:
        return None
    raw = str(exp_key).strip().replace("-", "")
    if len(raw) != 8 or not raw.isdigit():
        return None
    try:
        return datetime.strptime(raw, "%Y%m%d").date()
    except ValueError:
        return None


def extract_atm_iv(
    chain: dict,
    underlying_price: Optional[float],
    target_dte: int = 30,
    today: Optional[date] = None,
) -> Optional[tuple]:
    """Return ``(atm_iv, dte)`` for the expiration closest to ``target_dte``.

    Picks the expiration whose DTE is nearest to ``target_dte`` (only future
    expirations), then averages the implied vol of the call and put strikes
    nearest to ``underlying_price``. Returns ``None`` if the data is missing.
    """
    if not chain or underlying_price is None or not _is_finite(underlying_price):
        return None
    if today is None:
        today = datetime.utcnow().date()

    calls = chain.get("calls") or {}
    puts = chain.get("puts") or {}
    exp_keys = set(calls.keys()) | set(puts.keys())
    if not exp_keys:
        return None

    # Choose the expiration with DTE closest to target (future only).
    best_key = None
    best_dte = None
    best_delta = None
    for key in exp_keys:
        exp_date = _parse_exp_key(key)
        if exp_date is None:
            continue
        dte = (exp_date - today).days
        if dte < 1:
            continue
        delta = abs(dte - target_dte)
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best_key = key
            best_dte = dte
    if best_key is None:
        return None

    call_iv = _nearest_strike_iv(calls.get(best_key), underlying_price)
    put_iv = _nearest_strike_iv(puts.get(best_key), underlying_price)
    ivs = [iv for iv in (call_iv, put_iv) if iv is not None and iv > 0]
    if not ivs:
        return None
    return (sum(ivs) / len(ivs), best_dte)


def _nearest_strike_iv(
    strikes: Optional[dict],
    underlying_price: float,
) -> Optional[float]:
    """IV of the strike nearest to ``underlying_price`` within one expiration."""
    if not strikes:
        return None
    best_iv = None
    best_dist = None
    for _sk, contract in strikes.items():
        if not isinstance(contract, dict):
            continue
        strike = contract.get("strike")
        iv = contract.get("iv")
        if strike is None or iv is None:
            continue
        try:
            strike = float(strike)
            iv = float(iv)
        except (TypeError, ValueError):
            continue
        if iv <= 0:
            continue
        dist = abs(strike - underlying_price)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_iv = iv
    return best_iv


def classify_richness(ratio: Optional[float]) -> Optional[str]:
    """Map an IV/HV ratio to a human label."""
    if ratio is None or not _is_finite(ratio):
        return None
    if ratio >= RICH_THRESHOLD:
        return "rich"
    if ratio < CHEAP_THRESHOLD:
        return "cheap"
    return "fair"


def build_volatility_summary(
    chain: dict,
    underlying_price: Optional[float],
    closes: Sequence[float],
    target_dte: int = 30,
    hv_window: int = 20,
    today: Optional[date] = None,
) -> dict:
    """Compute an IV/HV volatility summary.

    Returns a dict with keys: ``atm_iv``, ``atm_dte``, ``hv``, ``hv_window``,
    ``iv_hv_ratio``, ``richness``. Missing pieces are ``None`` — never raises.
    """
    summary = {
        "atm_iv": None,
        "atm_dte": None,
        "hv": None,
        "hv_window": hv_window,
        "iv_hv_ratio": None,
        "richness": None,
    }
    try:
        hv = historical_volatility(closes, window=hv_window)
        summary["hv"] = round(hv, 4) if hv is not None else None

        atm = extract_atm_iv(chain, underlying_price, target_dte=target_dte, today=today)
        if atm is not None:
            atm_iv, atm_dte = atm
            summary["atm_iv"] = round(atm_iv, 4)
            summary["atm_dte"] = atm_dte

        if summary["atm_iv"] and hv and hv > 0:
            ratio = summary["atm_iv"] / hv
            summary["iv_hv_ratio"] = round(ratio, 2)
            summary["richness"] = classify_richness(ratio)
    except Exception:
        # Volatility context is best-effort; never break the caller.
        return summary
    return summary


def format_volatility_block(summary: Optional[dict]) -> str:
    """Render a compact, agent-readable volatility block.

    Returns an empty string when there is no usable signal so callers can skip
    injecting it.
    """
    if not summary:
        return ""
    atm_iv = summary.get("atm_iv")
    hv = summary.get("hv")
    ratio = summary.get("iv_hv_ratio")
    richness = summary.get("richness")
    if atm_iv is None and hv is None:
        return ""

    lines = []
    if atm_iv is not None:
        dte = summary.get("atm_dte")
        dte_txt = f" (~{dte}d expiry)" if dte else ""
        lines.append(f"- ATM implied volatility{dte_txt}: {atm_iv * 100:.1f}%")
    if hv is not None:
        win = summary.get("hv_window", 20)
        lines.append(f"- Realized (historical) volatility, {win}d: {hv * 100:.1f}%")
    if ratio is not None:
        guidance = {
            "rich": "IV is RICH vs. realized movement → premium selling is favourable.",
            "fair": "IV is FAIR vs. realized movement → premium is roughly priced to risk.",
            "cheap": "IV is CHEAP vs. realized movement → premium selling is poorly compensated; be cautious.",
        }.get(richness, "")
        lines.append(f"- IV/HV ratio: {ratio:.2f} ({richness}). {guidance}")
    if not lines:
        return ""
    return (
        "IV/HV VOLATILITY CONTEXT (computed now — no IV Rank; IV/HV is a "
        "stateless proxy for premium richness):\n" + "\n".join(lines)
    )


def _is_finite(x) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False
