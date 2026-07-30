"""Standalone filter pipeline for structured options chain data.

Works on the unified dict format produced by yfinance provider — keyed by expiration (YYYYMMDD) then strike (str float).
No dependency on options_chain_parser.py.
"""

import datetime
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Roll-direction constants
# ---------------------------------------------------------------------------

# Roll types and their directional semantics (same for calls and puts)
_ROLL_STRIKE_FILTERS = {
    "ROLL_DOWN":         "below",
    "ROLL_UP":           "above",
    "ROLL_OUT":          "same",       # ±1 adjacent strike
    "ROLL_UP_AND_OUT":   "above_eq",
    "ROLL_DOWN_AND_OUT": "below_eq",
}

# Rolls containing "OUT" require strictly later expirations
_STRICT_LATER_ROLLS = {"ROLL_OUT", "ROLL_UP_AND_OUT", "ROLL_DOWN_AND_OUT"}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _fmt_exp(exp: str) -> str:
    """Convert YYYYMMDD → YYYY-MM-DD for display."""
    if len(exp) == 8 and exp.isdigit():
        return f"{exp[:4]}-{exp[4:6]}-{exp[6:]}"
    return exp


# ---------------------------------------------------------------------------
# Filter functions
# ---------------------------------------------------------------------------

def filter_options_chain_by_type(chain: dict, option_type: str) -> dict:
    """Filter a parsed options chain to keep only calls or only puts.

    This is the FIRST filter in the pipeline — it strips the irrelevant side
    immediately so downstream filters process less data.

    Parameters
    ----------
    chain : dict
        Structured chain dict (output of ``parse_options_chain``).
    option_type : str
        ``"call"`` to keep only calls, ``"put"`` to keep only puts.
    """
    result = {k: v for k, v in chain.items() if k not in ("calls", "puts")}
    if option_type == "call":
        result["calls"] = chain.get("calls", {})
        result["puts"] = {}
    elif option_type == "put":
        result["calls"] = {}
        result["puts"] = chain.get("puts", {})
    else:
        # Unknown type — pass through unchanged
        result["calls"] = chain.get("calls", {})
        result["puts"] = chain.get("puts", {})
    return result


def filter_options_chain_for_position(
    chain: dict,
    current_strike: float,
    option_type: Optional[str] = None,
    num_strikes: int = 15,
) -> dict:
    """Filter a parsed options chain to ±num_strikes around current_strike.

    Keeps only strikes within range for each expiration in calls/puts.
    Adds a ``current_position`` key with the reference strike.
    """
    strike_val = float(current_strike)

    def _filter_bucket(bucket: dict) -> dict:
        filtered = {}
        for exp, strikes_dict in bucket.items():
            sorted_keys = sorted(strikes_dict.keys(), key=lambda k: float(k))
            # Find the index of the strike closest to current_strike
            closest_idx = min(
                range(len(sorted_keys)),
                key=lambda i: abs(float(sorted_keys[i]) - strike_val),
            ) if sorted_keys else 0
            lo = max(0, closest_idx - num_strikes)
            hi = min(len(sorted_keys), closest_idx + num_strikes + 1)
            kept = sorted_keys[lo:hi]
            if kept:
                filtered[exp] = {k: strikes_dict[k] for k in kept}
        return filtered

    result = {
        "symbol": chain.get("symbol", ""),
        "timestamp": chain.get("timestamp"),
        "current_position": {
            "strike": strike_val,
            "strike_key": str(float(strike_val)),
        },
    }
    if option_type:
        result["current_position"]["option_type"] = option_type

    result["calls"] = _filter_bucket(chain.get("calls", {}))
    result["puts"] = _filter_bucket(chain.get("puts", {}))
    return result


def filter_options_chain_by_delta(
    chain: dict,
    call_delta_range: tuple[float, float] = (0.15, 0.90),
    put_delta_range: tuple[float, float] = (-0.60, -0.15),
) -> dict:
    """Filter a parsed options chain to keep only contracts within delta ranges.

    Removes contracts with delta outside the specified ranges or with missing delta.
    This reduces noise for agents by eliminating deep ITM/OTM contracts.
    """
    def _filter_bucket(bucket: dict, delta_min: float, delta_max: float) -> dict:
        filtered = {}
        for exp, strikes_dict in bucket.items():
            kept = {}
            for strike_key, contract in strikes_dict.items():
                delta = contract.get("delta")
                if delta is not None and delta_min <= delta <= delta_max:
                    kept[strike_key] = contract
            if kept:
                filtered[exp] = kept
        return filtered

    return {
        "symbol": chain.get("symbol", ""),
        "timestamp": chain.get("timestamp"),
        "calls": _filter_bucket(chain.get("calls", {}), *call_delta_range),
        "puts": _filter_bucket(chain.get("puts", {}), *put_delta_range),
        **({"current_position": chain["current_position"]} if "current_position" in chain else {}),
    }


def get_contract(
    chain: dict,
    current_strike: float,
    current_expiration: str,
    option_type: str,
) -> Optional[dict]:
    """Retrieve the EXACT contract dict for the given strike+expiration.
    
    Returns the contract dict (bid/ask/delta/etc.) for the specified strike and
    expiration, or None if not found. Null-safe: None args → None.
    
    Parameters
    ----------
    chain : dict
        Structured chain dict (calls/puts buckets).
    current_strike : float
        Strike of the contract to find.
    current_expiration : str
        Expiration of the contract to find (YYYY-MM-DD or YYYYMMDD).
    option_type : str
        "call" / "covered_call" / "open_call" → searches "calls" bucket.
        "put" / "cash_secured_put" / "open_put" → searches "puts" bucket.
    
    Returns
    -------
    dict or None
        The contract dict if found, otherwise None.
    """
    # Null-safe: if either strike or expiration is None, return None
    if current_strike is None or current_expiration is None:
        return None
    
    # Determine which bucket to search
    if option_type in ("call", "covered_call", "open_call", "open_call_monitor"):
        bucket_key = "calls"
    elif option_type in ("put", "cash_secured_put", "open_put", "open_put_monitor"):
        bucket_key = "puts"
    else:
        # Unknown type — return None
        return None
    
    # Normalize expiration to chain key format (YYYYMMDD)
    exp_key = str(current_expiration).replace("-", "")[:8]
    
    # Get the bucket
    bucket = chain.get(bucket_key, {})
    if not bucket or exp_key not in bucket:
        # Expiration not in chain
        return None
    
    # Search for the strike by float value (handles "65.0", "65.00", "65")
    strikes_dict = bucket[exp_key]
    for sk, contract in strikes_dict.items():
        try:
            if float(sk) == float(current_strike):
                return contract
        except (ValueError, TypeError):
            # Non-numeric strike key — skip
            continue
    
    # Strike not found in this expiration
    return None


def exclude_contract(
    chain: dict,
    current_strike: float,
    current_expiration: str,
    option_type: str,
) -> dict:
    """Return a copy of the chain with the EXACT current contract removed.
    
    Removes the contract matching BOTH the current strike AND current expiration.
    A same-strike different-expiration (roll-out) or same-expiration different-strike
    (roll-down/up) candidate is preserved — only the identical no-op contract is dropped.
    
    Parameters
    ----------
    chain : dict
        Structured chain dict (output of filter_options_chain_by_delta).
    current_strike : float
        Strike of the currently-held contract.
    current_expiration : str
        Expiration of the currently-held contract (YYYY-MM-DD or YYYYMMDD).
    option_type : str
        "call" / "covered_call" / "open_call" → operates on "calls" bucket.
        "put" / "cash_secured_put" / "open_put" → operates on "puts" bucket.
    
    Returns
    -------
    dict
        Chain with the EXACT current contract removed (if found), otherwise unchanged.
    """
    # Null-safe: if either strike or expiration is None, return unchanged
    if current_strike is None or current_expiration is None:
        return chain
    
    # Determine which bucket to filter
    if option_type in ("call", "covered_call", "open_call", "open_call_monitor"):
        bucket_key = "calls"
    elif option_type in ("put", "cash_secured_put", "open_put", "open_put_monitor"):
        bucket_key = "puts"
    else:
        # Unknown type — return unchanged
        return chain
    
    # Normalize expiration to chain key format (YYYYMMDD)
    exp_key = str(current_expiration).replace("-", "")[:8]
    
    # Get the bucket
    bucket = chain.get(bucket_key, {})
    if not bucket or exp_key not in bucket:
        # Expiration not in chain — nothing to exclude
        return chain
    
    # Make a shallow copy of the chain to avoid mutating the original
    result = {
        "symbol": chain.get("symbol", ""),
        "timestamp": chain.get("timestamp"),
    }
    if "current_position" in chain:
        result["current_position"] = chain["current_position"]
    
    # Deep-ish copy of the affected bucket
    other_key = "puts" if bucket_key == "calls" else "calls"
    result[other_key] = chain.get(other_key, {})
    
    # Copy the bucket, removing the matching contract
    filtered_bucket = {}
    for exp, strikes_dict in bucket.items():
        if exp == exp_key:
            # This is the expiration we need to filter
            kept = {}
            for sk, contract in strikes_dict.items():
                # Match strike by float value (handles "65.0", "65.00", "65")
                try:
                    if float(sk) != float(current_strike):
                        kept[sk] = contract
                except (ValueError, TypeError):
                    # Non-numeric strike key — keep it
                    kept[sk] = contract
            # Only add this expiration if it still has strikes after filtering
            if kept:
                filtered_bucket[exp] = kept
        else:
            # Other expirations — keep as-is
            filtered_bucket[exp] = strikes_dict
    
    result[bucket_key] = filtered_bucket
    return result


def filter_options_chain_by_roll_direction(
    chain: dict,
    current_strike: float,
    current_expiration: str,
    roll_type: str,
    option_type: str,
) -> dict:
    """Filter an already-filtered chain based on the roll direction from Phase 1.

    Narrows strikes and expirations so Phase 2 only sees candidates that are
    valid for the given roll type.  Unrecognised roll types pass the chain
    through unchanged (safe fallback).

    Parameters
    ----------
    chain : dict
        Structured chain dict (output of ``filter_options_chain_by_delta``).
    current_strike : float
        The strike of the current position being rolled.
    current_expiration : str
        Expiration of the current position (``YYYY-MM-DD`` or ``YYYYMMDD``).
    roll_type : str
        Roll action from Phase 1 (e.g. ``ROLL_DOWN``, ``ROLL_UP_AND_OUT``).
    option_type : str
        ``"call"`` or ``"put"``.
    """
    direction = _ROLL_STRIKE_FILTERS.get(roll_type)
    if direction is None:
        logger.warning(
            "filter_options_chain_by_roll_direction: unknown roll_type '%s' — returning chain unchanged",
            roll_type,
        )
        return chain

    # Normalise expiration to YYYYMMDD for chain-key comparison
    exp_key = current_expiration.replace("-", "")
    strict_later = roll_type in _STRICT_LATER_ROLLS

    # Determine which bucket to filter based on option_type
    bucket_key = "calls" if option_type == "call" else "puts"
    bucket = chain.get(bucket_key, {})

    # Pre-compute adjacent strikes for ROLL_OUT (±1 nearest)
    all_strikes: set[float] = set()
    for strikes_dict in bucket.values():
        all_strikes.update(float(k) for k in strikes_dict)
    sorted_strikes = sorted(all_strikes)

    adjacent_strikes: set[float] = set()
    if direction == "same" and sorted_strikes:
        # Find index of the closest strike to current_strike
        closest_idx = min(
            range(len(sorted_strikes)),
            key=lambda i: abs(sorted_strikes[i] - current_strike),
        )
        adjacent_strikes.add(sorted_strikes[closest_idx])
        if closest_idx > 0:
            adjacent_strikes.add(sorted_strikes[closest_idx - 1])
        if closest_idx < len(sorted_strikes) - 1:
            adjacent_strikes.add(sorted_strikes[closest_idx + 1])

    def _strike_ok(strike_val: float) -> bool:
        if direction == "below":
            return strike_val < current_strike
        elif direction == "above":
            return strike_val > current_strike
        elif direction == "below_eq":
            return strike_val <= current_strike
        elif direction == "above_eq":
            return strike_val >= current_strike
        elif direction == "same":
            return strike_val in adjacent_strikes
        return True  # fallback: keep

    def _exp_ok(exp: str) -> bool:
        if strict_later:
            return exp > exp_key
        return exp >= exp_key

    filtered_bucket: dict = {}
    for exp, strikes_dict in bucket.items():
        if not _exp_ok(exp):
            continue
        kept = {
            k: v for k, v in strikes_dict.items()
            if _strike_ok(float(k))
        }
        if kept:
            filtered_bucket[exp] = kept

    # Preserve the other bucket untouched and keep chain metadata
    other_key = "puts" if bucket_key == "calls" else "calls"
    result = {
        "symbol": chain.get("symbol", ""),
        "timestamp": chain.get("timestamp"),
        bucket_key: filtered_bucket,
        other_key: chain.get(other_key, {}),
    }
    if "current_position" in chain:
        result["current_position"] = chain["current_position"]
    return result


def format_roll_candidates_table(
    chain: dict,
    current_strike: float,
    current_expiration: str,
    option_type: str,
    underlying_price: float,
    roll_type: str,
    buyback_cost: float | None = None,
) -> str:
    """Build a flat markdown table of roll candidates with pre-computed economics.

    Parameters
    ----------
    chain : dict
        Direction-filtered chain (output of ``filter_options_chain_by_roll_direction``).
    current_strike : float
        Strike of the position being rolled.
    current_expiration : str
        Expiration of the current position (YYYY-MM-DD or YYYYMMDD).
    option_type : str
        ``"call"`` or ``"put"``.
    underlying_price : float
        Current price of the underlying stock.
    roll_type : str
        Roll action from Phase 1 (e.g. ``ROLL_DOWN``).
    buyback_cost : float | None
        Ask price of the current contract (cost to buy-to-close).  Pass this
        explicitly because the direction-filtered chain usually excludes the
        current contract.  When *None*, the function attempts a fallback
        lookup in ``chain`` but this will often miss.

    Returns
    -------
    str
        Human-readable text block with current position summary and candidate table.
    """
    bucket_key = "calls" if option_type == "call" else "puts"
    bucket = chain.get(bucket_key, {})
    symbol = chain.get("symbol", "")
    today = datetime.date.today()

    # Normalise current expiration to YYYYMMDD for chain lookup
    exp_key = current_expiration.replace("-", "")
    strike_key = str(float(current_strike))

    # --- Find current position contract (may be absent after direction filter) ---
    current_contract = None
    if exp_key in bucket and strike_key in bucket[exp_key]:
        current_contract = bucket[exp_key][strike_key]

    # Use explicitly-provided buyback_cost; fall back to chain lookup only if needed
    if buyback_cost is None and current_contract and current_contract.get("ask") is not None:
        buyback_cost = float(current_contract["ask"])

    # --- Current position summary ---
    current_exp_display = _fmt_exp(exp_key)
    try:
        current_exp_date = datetime.date(int(exp_key[:4]), int(exp_key[4:6]), int(exp_key[6:]))
        current_dte = (current_exp_date - today).days
    except (ValueError, IndexError):
        current_dte = None

    lines = [f"CURRENT POSITION:"]
    lines.append(f"  Symbol: {symbol} | Type: {option_type}")
    dte_str = f" ({current_dte} DTE)" if current_dte is not None else ""
    lines.append(f"  Strike: ${current_strike:.1f} | Expiration: {current_exp_display}{dte_str}")

    if current_contract:
        bid_str = f"${current_contract.get('bid', 'N/A')}" if current_contract.get('bid') is not None else "N/A"
        ask_str = f"${current_contract.get('ask', 'N/A')}" if current_contract.get('ask') is not None else "N/A"
        delta_str = f"{current_contract.get('delta', 'N/A')}" if current_contract.get('delta') is not None else "N/A"
        theta_str = f"${current_contract.get('theta', 'N/A')}" if current_contract.get('theta') is not None else "N/A"
        lines.append(f"  Bid: {bid_str} | Ask: {ask_str} | Delta: {delta_str} | Theta: {theta_str}")
    if buyback_cost is not None:
        lines.append(f"  Buyback cost (ask): ${buyback_cost:.2f} per share (${buyback_cost * 100:.2f} per contract)")
    else:
        lines.append("  Buyback cost: NOT AVAILABLE — current contract not in chain data. Use the buyback cost from Phase 1 handoff if available.")

    # --- Build candidate rows ---
    candidates = []
    for exp, strikes_dict in sorted(bucket.items()):
        for sk, contract in sorted(strikes_dict.items(), key=lambda kv: float(kv[0])):
            # Skip the current position itself
            if exp == exp_key and sk == strike_key:
                continue
            bid = contract.get("bid")
            if bid is None or bid == 0:
                continue

            bid = float(bid)
            ask_val = float(contract["ask"]) if contract.get("ask") is not None else None
            delta = contract.get("delta")
            theta = contract.get("theta")
            strike_val = float(sk)
            exp_display = _fmt_exp(exp)

            try:
                exp_date = datetime.date(int(exp[:4]), int(exp[4:6]), int(exp[6:]))
                dte = (exp_date - today).days
            except (ValueError, IndexError):
                dte = 0

            net_credit = (bid - buyback_cost) if buyback_cost is not None else None

            if option_type == "call" and underlying_price > 0:
                premium_pct = (bid / underlying_price) * 100
            elif option_type == "put" and strike_val > 0:
                premium_pct = (bid / strike_val) * 100
            else:
                premium_pct = 0.0

            ann_ret = (premium_pct * 365 / dte) if dte > 0 else 0.0

            candidates.append({
                "strike": strike_val,
                "exp": exp_display,
                "dte": dte,
                "delta": delta,
                "theta": theta,
                "bid": bid,
                "ask": ask_val,
                "net_credit": net_credit,
                "premium_pct": premium_pct,
                "ann_ret": ann_ret,
            })

    # Sort by annualized return descending (best return per day first).
    if buyback_cost is not None:
        candidates.sort(key=lambda c: c["ann_ret"] if c["ann_ret"] is not None else -9999, reverse=True)
    else:
        candidates.sort(key=lambda c: c["ann_ret"] if c["ann_ret"] is not None else -9999, reverse=True)

    if not candidates:
        lines.append("")
        lines.append(f"NO VALID CANDIDATES found for {roll_type}. Consider CLOSE.")
        return "\n".join(lines)

    # --- Format table ---
    lines.append("")
    lines.append(f"ROLL CANDIDATES ({roll_type} — {len(candidates)} candidates, sorted by annualized return (Ann.Ret%)):")
    header = "| #  | Strike | Expiration | DTE | Delta |  Bid  |  Ask  | New Prem | Buyback | Net Credit | Prem% | Ann.Ret% |"
    sep    = "|----|--------|------------|-----|-------|-------|-------|----------|---------|------------|-------|----------|"
    lines.append(header)
    lines.append(sep)

    for i, c in enumerate(candidates, 1):
        delta_s = f"{c['delta']:.2f}" if c["delta"] is not None else "  -  "
        ask_s = f"{c['ask']:.2f}" if c["ask"] is not None else "  -  "
        buyback_s = f"{buyback_cost:.2f}" if buyback_cost is not None else "  N/A "
        if c["net_credit"] is not None:
            nc_s = f"{c['net_credit']:+.2f}"
        else:
            nc_s = "  N/A "
        row = (
            f"| {i:>2} "
            f"| {c['strike']:<6.1f} "
            f"| {c['exp']:>10} "
            f"| {c['dte']:>3} "
            f"| {delta_s:>5} "
            f"| {c['bid']:>5.2f} "
            f"| {ask_s:>5} "
            f"| {c['bid']:>8.2f} "
            f"| {buyback_s:>7} "
            f"| {nc_s:>10} "
            f"| {c['premium_pct']:>4.1f}% "
            f"| {c['ann_ret']:>7.1f}% |"
        )
        lines.append(row)

    # --- Notes ---
    lines.append("")
    lines.append("NOTES:")
    if buyback_cost is not None:
        lines.append(f"- Buyback cost is FIXED at ${buyback_cost:.2f} (current contract ask) for all candidates")
    lines.append("- Net Credit = New Premium (bid) - Buyback Cost (ask). Positive = you collect, negative = you pay")
    lines.append("- All prices are per share. Multiply by 100 for per-contract amounts")
    if option_type == "call":
        lines.append("- Premium% for calls = bid / underlying_price × 100")
    else:
        lines.append("- Premium% for puts = bid / strike × 100")
    lines.append("- Ann.Ret% = Premium% × 365 / DTE")

    return "\n".join(lines)
