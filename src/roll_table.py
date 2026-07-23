"""
Deterministic roll table calculator.

Given a parsed options chain and the parameters of the current short option
position, computes:

  - Buy-back cost of the current short (robust_mid × 100 × contracts)
  - Profit-capture percentage and 70% gate flag
  - Roll scenarios across the next N expirations (strictly after the current
    expiration) and up to 3 strike targets (ATM / +offset% / -offset%)

Pure Python — no LLM, no I/O, no side effects.

Chain format (from OptionsChainCache):
  {
    "symbol":    str,
    "timestamp": str,          # ISO 8601
    "calls": {
      "YYYYMMDD": {            # expiration key
        "115.0": {             # strike key (str float)
          "bid":   float,
          "ask":   float,
          "mid":   float,
          "delta": float,
          ...
        },
        ...
      },
      ...
    },
    "puts": { ... }            # same structure
  }
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Optional

from src.options_math import robust_mid
from src.options_chain_filters import get_contract

logger = logging.getLogger(__name__)

_PROFIT_TARGET_PCT = 0.70  # Mirrors open_call_assessment_instructions.py line 68


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _label_for_offset(offset: float) -> str:
    """Return a human-readable label for a strike offset."""
    if offset == 0.0:
        return "ATM"
    sign = "+" if offset > 0 else ""
    pct = int(round(offset * 100))
    return f"{sign}{pct}%"


def _parse_exp_key(exp_str: str) -> Optional[date]:
    """Convert YYYYMMDD (or YYYY-MM-DD) to a date object. Returns None on failure."""
    s = str(exp_str).replace("-", "")
    if len(s) == 8 and s.isdigit():
        try:
            return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            return None
    return None


def _to_exp_key(exp_str: str) -> str:
    """Normalize an expiration string to the YYYYMMDD chain-key format."""
    return str(exp_str).replace("-", "")[:8]


def _to_display_date(exp_str: str) -> str:
    """Convert YYYYMMDD → YYYY-MM-DD for output."""
    s = str(exp_str).replace("-", "")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return exp_str


def _select_strike(
    sorted_strikes: list[float],
    target: float,
    offset: float,
) -> Optional[float]:
    """Pick the best available strike for a given offset/target.

    offset == 0.0 → closest to target (ATM, any direction)
    offset  > 0   → smallest strike >= target; fallback to highest available
    offset  < 0   → largest  strike <= target; fallback to lowest available
    """
    if not sorted_strikes:
        return None
    if offset == 0.0:
        return min(sorted_strikes, key=lambda s: abs(s - target))
    if offset > 0:
        candidates = [s for s in sorted_strikes if s >= target]
        return candidates[0] if candidates else sorted_strikes[-1]
    # offset < 0
    candidates = [s for s in sorted_strikes if s <= target]
    return candidates[-1] if candidates else sorted_strikes[0]


def _gray_cell(exp_display: str, dte: int) -> dict:
    """Return a placeholder cell for missing/illiquid data."""
    return {
        "expiration": exp_display,
        "dte": dte,
        "strike": None,
        "bid": None,
        "ask": None,
        "delta": None,
        "net_credit": None,
        "color": "gray",
    }


def _bucket_key(option_type: str) -> str:
    """Map option_type string to the chain bucket key ('calls' or 'puts')."""
    if option_type in ("call", "covered_call", "open_call", "open_call_monitor"):
        return "calls"
    if option_type in ("put", "cash_secured_put", "open_put", "open_put_monitor"):
        return "puts"
    logger.warning("roll_table: unknown option_type '%s', defaulting to 'calls'", option_type)
    return "calls"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_roll_table(
    chain: "dict | str",
    current_strike: float,
    current_expiration: str,
    option_type: str,
    underlying_price: float,
    premium_received: float,
    contracts: int = 1,
    num_expiries: int = 4,
    strike_offsets: tuple = (0.0, +0.03, -0.03),
) -> dict:
    """Compute buy-back cost and roll scenarios for a short option position.

    Parameters
    ----------
    chain : dict or JSON str
        Full options chain from OptionsChainCache.get_or_load[_async]().
    current_strike : float
        Strike of the currently-held short option.
    current_expiration : str
        Expiration of the current position (YYYY-MM-DD or YYYYMMDD).
    option_type : str
        "call" / "covered_call" / "open_call" / "open_call_monitor" for calls;
        "put" / "cash_secured_put" / "open_put" / "open_put_monitor" for puts.
    underlying_price : float
        Current market price of the underlying (live, from yf_provider).
    premium_received : float
        Premium received per share when the position was opened.
    contracts : int
        Number of contracts (default 1; position schema has no contracts field yet).
    num_expiries : int
        Number of future expirations after current_expiration to include.
    strike_offsets : tuple[float]
        Strike selection offsets relative to underlying_price.
        0.0 → ATM (closest), +0.03 → +3%, -0.03 → -3%.

    Returns
    -------
    dict
        {
          "buyback_cost":         float,   # total cost to close (per-contract × N)
          "buyback_per_share":    float,   # robust_mid of current contract
          "pct_captured":         float,   # (premium_received - buyback_per_share) / premium_received
          "profit_target_reached":bool,    # pct_captured >= 0.70
          "underlying_price":     float,
          "chain_timestamp":      str | None,
          "current_position":     {strike, expiration, option_type, premium_received},
          "expirations":          [{date: str, dte: int}, ...],
          "rows": [
            {
              "offset":  float,
              "label":   str,             # "ATM" / "+3%" / "-3%"
              "strike":  float | None,    # first-expiry strike (display convenience)
              "cells": [
                {
                  "expiration": str,      # YYYY-MM-DD
                  "dte":        int,
                  "strike":     float | None,
                  "bid":        float | None,
                  "ask":        float | None,
                  "delta":      float | None,
                  "net_credit": float | None,  # new_bid×100×contracts − buyback_cost
                  "color":      str,           # "green" | "red" | "gray"
                }
              ]
            }
          ]
        }
    """
    # ── 0. Parse chain if given as JSON string ─────────────────────────────
    if isinstance(chain, str):
        try:
            chain = json.loads(chain)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("roll_table: failed to parse chain JSON: %s", exc)
            chain = {}

    chain_timestamp: Optional[str] = chain.get("timestamp")
    bkey = _bucket_key(option_type)
    bucket: dict = chain.get(bkey, {})

    # ── 1. Buy-back cost (current short position) ──────────────────────────
    current_contract = get_contract(chain, current_strike, current_expiration, option_type)
    if current_contract is not None:
        cb_bid = float(current_contract.get("bid") or 0)
        cb_ask = float(current_contract.get("ask") or 0)
        buyback_per_share = robust_mid(cb_bid, cb_ask)
    else:
        logger.warning(
            "roll_table: current contract not found (strike=%.2f, exp=%s) — buyback=0",
            current_strike,
            current_expiration,
        )
        buyback_per_share = 0.0

    buyback_cost = round(buyback_per_share * 100 * contracts, 2)

    # ── 2. Profit-capture metrics ──────────────────────────────────────────
    if premium_received and premium_received > 0:
        pct_captured = round(
            (premium_received - buyback_per_share) / premium_received, 4
        )
    else:
        pct_captured = 0.0
    profit_target_reached: bool = pct_captured >= _PROFIT_TARGET_PCT

    # ── 3. Select next N expirations strictly after current_expiration ─────
    current_exp_key = _to_exp_key(current_expiration)
    today = date.today()

    sorted_exp_keys = sorted(
        k for k in bucket.keys() if _parse_exp_key(k) is not None
    )

    future_exp_keys = [
        k for k in sorted_exp_keys
        if k > current_exp_key and (_parse_exp_key(k) or date.min) > today
    ]
    selected_exp_keys = future_exp_keys[:num_expiries]

    exp_entries: list[dict] = []
    for k in selected_exp_keys:
        d = _parse_exp_key(k)
        dte = (d - today).days if d else 0
        exp_entries.append({"key": k, "date": _to_display_date(k), "dte": dte})

    # ── 4. Build rows × cells ──────────────────────────────────────────────
    rows: list[dict] = []

    for offset in strike_offsets:
        label = _label_for_offset(offset)
        target = underlying_price * (1.0 + offset)

        cells: list[dict] = []
        first_strike: Optional[float] = None

        for exp in exp_entries:
            exp_key = exp["key"]
            exp_display = exp["date"]
            dte = exp["dte"]

            strikes_dict: dict = bucket.get(exp_key, {})
            if not strikes_dict:
                cells.append(_gray_cell(exp_display, dte))
                continue

            # Build sorted list of available float strikes
            available: list[float] = []
            for sk in strikes_dict:
                try:
                    available.append(float(sk))
                except (ValueError, TypeError):
                    pass
            available.sort()

            if not available:
                cells.append(_gray_cell(exp_display, dte))
                continue

            chosen_strike = _select_strike(available, target, offset)
            if chosen_strike is None:
                cells.append(_gray_cell(exp_display, dte))
                continue

            # Find the contract dict for the chosen strike
            contract: Optional[dict] = None
            for sk, c in strikes_dict.items():
                try:
                    if float(sk) == chosen_strike:
                        contract = c
                        break
                except (ValueError, TypeError):
                    pass

            if contract is None:
                cells.append(_gray_cell(exp_display, dte))
                continue

            new_bid = float(contract.get("bid") or 0)
            new_ask = float(contract.get("ask") or 0)
            raw_delta = contract.get("delta")
            delta: Optional[float] = (
                round(float(raw_delta), 4) if raw_delta is not None else None
            )

            new_premium = round(new_bid * 100 * contracts, 2)
            net_credit = round(new_premium - buyback_cost, 2)

            if new_bid == 0:
                color = "gray"
            elif net_credit > 0:
                color = "green"
            else:
                color = "red"

            if first_strike is None:
                first_strike = chosen_strike

            cells.append(
                {
                    "expiration": exp_display,
                    "dte": dte,
                    "strike": chosen_strike,
                    "bid": round(new_bid, 2),
                    "ask": round(new_ask, 2),
                    "delta": delta,
                    "net_credit": net_credit,
                    "color": color,
                }
            )

        rows.append(
            {
                "offset": offset,
                "label": label,
                "strike": first_strike,
                "cells": cells,
            }
        )

    # ── 5. Assemble result ─────────────────────────────────────────────────
    return {
        "buyback_cost": buyback_cost,
        "buyback_per_share": round(buyback_per_share, 4),
        "pct_captured": pct_captured,
        "profit_target_reached": profit_target_reached,
        "underlying_price": underlying_price,
        "chain_timestamp": chain_timestamp,
        "current_position": {
            "strike": current_strike,
            "expiration": _to_display_date(current_expiration),
            "option_type": option_type,
            "premium_received": premium_received,
        },
        "expirations": [{"date": e["date"], "dte": e["dte"]} for e in exp_entries],
        "rows": rows,
    }
