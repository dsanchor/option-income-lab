"""
Deterministic roll table calculator.

Given a parsed options chain and the parameters of the current short option
position, computes:

  - Buy-back cost of the current short (executable ask × 100 × contracts)
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
import math
import re
from collections.abc import Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Optional

from src.options_chain_filters import (
    canonical_strike,
    get_contract,
    parse_strike_decimal,
)
from src.options_chain_view import usable_greek, usable_quote
from src.options_math import executable_buyback_ask, robust_mid_optional

logger = logging.getLogger(__name__)

_PROFIT_TARGET_PCT = 0.70  # Mirrors open_call_assessment_instructions.py line 68
_MONEY_4 = Decimal("0.0001")
_MONEY_2 = Decimal("0.01")
_QUOTE_NUMBER_PATTERN = re.compile(r"^(?:0|[1-9]\d*)(?:\.\d+)?$")
_USABLE_CHAIN_STATUSES = frozenset({"ok", "success", "stale", "carried"})
_CHAIN_STATUS_WARNINGS = {
    "stale": "Options chain wrapper is stale.",
    "carried": "Options chain wrapper contains carried last-known-good data.",
}


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


class RollSimulationError(ValueError):
    """Structured, caller-safe failure from the pure roll simulation."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _decode_chain_json(value: Any, *, label: str) -> Any:
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError, ValueError) as exc:
            raise RollSimulationError(
                "chain_unavailable",
                f"Options chain retrieval returned invalid {label} JSON",
            ) from exc
    return value


def _plain_mapping(value: Any, *, label: str) -> dict:
    if not isinstance(value, Mapping):
        raise RollSimulationError(
            "chain_unavailable",
            f"Options chain retrieval returned an invalid {label}",
        )
    try:
        return dict(value)
    except Exception as exc:
        raise RollSimulationError(
            "chain_unavailable",
            f"Options chain retrieval returned an unreadable {label}",
        ) from exc


def _validate_chain_status(container: dict, warnings: list[str]) -> None:
    normalized_status: str | None = None
    if "status" in container:
        status = container["status"]
        if not isinstance(status, str) or status.strip().lower() not in _USABLE_CHAIN_STATUSES:
            raise RollSimulationError(
                "chain_unavailable",
                "Options chain wrapper reported an unavailable status",
            )
        normalized_status = status.strip().lower()
        warning = _CHAIN_STATUS_WARNINGS.get(normalized_status)
        if warning and warning not in warnings:
            warnings.append(warning)
    if container.get("error") or container.get("errors"):
        if normalized_status not in {"stale", "carried"}:
            raise RollSimulationError(
                "chain_unavailable",
                "Options chain wrapper reported an error",
            )
        warning = "Options chain wrapper reports a refresh error; using retained data."
        if warning not in warnings:
            warnings.append(warning)


def _extract_chain_mapping(payload: Any) -> tuple[dict, list[str]]:
    """Decode only documented JSON/Mapping wrappers and validate status first."""
    candidate = _decode_chain_json(payload, label="top-level")
    warnings: list[str] = []
    seen: set[int] = set()

    for _ in range(4):
        if isinstance(candidate, Mapping):
            identity = id(candidate)
            if identity in seen:
                raise RollSimulationError(
                    "chain_unavailable",
                    "Options chain wrapper is recursive",
                )
            seen.add(identity)
        container = _plain_mapping(candidate, label="chain payload")
        _validate_chain_status(container, warnings)
        if "options_chain" not in container:
            return container, warnings
        candidate = _decode_chain_json(
            container["options_chain"],
            label="options_chain",
        )

    raise RollSimulationError(
        "chain_unavailable",
        "Options chain wrapper nesting is invalid",
    )


def _roll_chain_payload(payload: Any, *, required_side: str) -> tuple[dict, list[str]]:
    """Normalize a cache/provider chain without mutating Mapping inputs."""
    candidate, warnings = _extract_chain_mapping(payload)
    if "calls" not in candidate and "puts" not in candidate:
        raise RollSimulationError(
            "chain_unavailable",
            "Options chain retrieval returned no chain payload",
        )

    normalized = dict(candidate)
    for side in ("calls", "puts"):
        if side not in candidate:
            normalized[side] = {}
            continue
        side_bucket = candidate.get(side)
        if not isinstance(side_bucket, Mapping):
            raise RollSimulationError(
                "chain_unavailable",
                f"Options chain has an invalid {side.upper()} container",
            )
        plain_side = _plain_mapping(side_bucket, label=f"{side} container")
        normalized_side = {}
        for expiration, strikes in plain_side.items():
            if not isinstance(expiration, str) or _parse_exp_key(expiration) is None:
                raise RollSimulationError(
                    "chain_unavailable",
                    f"Options chain has an invalid {side.upper()} expiration",
                )
            if not isinstance(strikes, Mapping):
                raise RollSimulationError(
                    "chain_unavailable",
                    f"Options chain has an invalid {side.upper()} expiry container",
                )
            plain_strikes = _plain_mapping(
                strikes,
                label=f"{side} expiry container",
            )
            normalized_strikes = {}
            for strike, contract in plain_strikes.items():
                if not isinstance(strike, str) or not isinstance(contract, Mapping):
                    raise RollSimulationError(
                        "chain_unavailable",
                        f"Options chain has an invalid {side.upper()} contract record",
                    )
                normalized_contract = _plain_mapping(
                    contract,
                    label=f"{side} contract",
                )
                meta = normalized_contract.get("_meta")
                if isinstance(meta, Mapping):
                    normalized_meta = _plain_mapping(meta, label="contract metadata")
                    field_status = normalized_meta.get("field_status")
                    if isinstance(field_status, Mapping):
                        normalized_meta["field_status"] = _plain_mapping(
                            field_status,
                            label="contract field status",
                        )
                    normalized_contract["_meta"] = normalized_meta
                for field in ("bid", "ask"):
                    value = normalized_contract.get(field)
                    if isinstance(value, str) and _QUOTE_NUMBER_PATTERN.fullmatch(value):
                        try:
                            parsed = Decimal(value)
                        except InvalidOperation:
                            continue
                        if parsed.is_finite():
                            normalized_contract[field] = float(parsed)
                normalized_strikes[strike] = normalized_contract
            normalized_side[expiration] = normalized_strikes
        normalized[side] = normalized_side

    required_bucket = normalized.get(required_side)
    has_contract_records = (
        isinstance(required_bucket, dict)
        and any(
            isinstance(contracts, dict) and bool(contracts)
            for contracts in required_bucket.values()
        )
    )
    if not has_contract_records:
        raise RollSimulationError(
            "chain_unavailable",
            f"Options chain has no usable {required_side.upper()} contract records",
        )

    from src.options_chain_cache import apply_agent_view

    view = apply_agent_view(normalized)
    if not isinstance(view, dict):
        raise RollSimulationError(
            "chain_unavailable",
            "Options chain retrieval returned an unusable chain payload",
        )
    for field in ("source", "_source", "status"):
        if field in normalized:
            view[field] = normalized[field]
    return view, warnings


def _positive_decimal(value, *, field: str) -> Decimal:
    if isinstance(value, bool):
        raise RollSimulationError("invalid_input", f"{field} must be a finite positive number")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise RollSimulationError("invalid_input", f"{field} must be a finite positive number")
    if not parsed.is_finite() or parsed <= 0:
        raise RollSimulationError("invalid_input", f"{field} must be a finite positive number")
    return parsed


def _strike_decimal(value, *, field: str) -> Decimal:
    try:
        return parse_strike_decimal(value)
    except ValueError as exc:
        raise RollSimulationError("invalid_input", f"{field} {exc}") from exc


def _two_sided_midpoint(contract: Mapping | None, *, leg: str) -> Decimal:
    """Return the repository robust midpoint for a valid two-sided market."""
    label = leg.capitalize()
    if not isinstance(contract, Mapping):
        raise RollSimulationError(f"{leg}_contract_not_found", f"{label} contract was not found")
    bid = usable_quote(contract, "bid")
    ask = usable_quote(contract, "ask")
    meta = contract.get("_meta")
    field_status = meta.get("field_status") if isinstance(meta, Mapping) else {}
    field_status = field_status if isinstance(field_status, Mapping) else {}
    if bid is None:
        reason = field_status.get("bid")
        detail = f"bid is {str(reason).replace('_', ' ')}" if reason else "bid is unavailable"
        raise RollSimulationError(
            f"{leg}_midpoint_unavailable",
            f"{label} contract midpoint unavailable: {detail}",
        )
    if ask is None:
        reason = field_status.get("ask")
        detail = f"ask is {str(reason).replace('_', ' ')}" if reason else "ask is unavailable"
        raise RollSimulationError(
            f"{leg}_midpoint_unavailable",
            f"{label} contract midpoint unavailable: {detail}",
        )
    if ask < bid:
        raise RollSimulationError(
            f"{leg}_midpoint_unavailable",
            f"{label} contract midpoint unavailable: market is crossed (ask below bid)",
        )
    midpoint = robust_mid_optional(bid, ask)
    if midpoint is None or not math.isfinite(midpoint) or midpoint <= 0:
        raise RollSimulationError(
            f"{leg}_midpoint_unavailable",
            f"{label} contract midpoint unavailable: midpoint is not positive and finite",
        )
    return Decimal(str(midpoint)).quantize(_MONEY_4, rounding=ROUND_HALF_UP)


def _quote_provenance(contract: Mapping) -> dict:
    meta = contract.get("_meta") if isinstance(contract, Mapping) else {}
    meta = meta if isinstance(meta, Mapping) else {}
    field_status = meta.get("field_status")
    return {
        "quote_asof": meta.get("quote_asof"),
        "source": meta.get("quote_source"),
        "stale": bool(meta.get("stale")),
        "carried": bool(meta.get("carried")),
        "field_status": dict(field_status) if isinstance(field_status, Mapping) else {},
    }


def compute_roll_simulation(
    chain: Any,
    *,
    current_strike,
    current_expiration: str,
    target_strike,
    target_expiration: str,
    option_type: str,
    contracts,
    multiplier: int,
) -> dict:
    """Calculate an informational close-and-open roll using exact contracts.

    Both legs use the repository robust midpoint over a valid, positive,
    non-crossed two-sided market. The function is pure and never mutates the
    chain or position.
    """
    current_strike_d = _strike_decimal(current_strike, field="current strike")
    target_strike_d = _strike_decimal(target_strike, field="target strike")
    contracts_d = _positive_decimal(contracts, field="contracts")
    if contracts_d != contracts_d.to_integral_value():
        raise RollSimulationError("invalid_input", "contracts must be a positive whole number")
    if isinstance(multiplier, bool) or not isinstance(multiplier, int) or multiplier <= 0:
        raise RollSimulationError("invalid_input", "multiplier must be a positive integer")

    normalized_type = str(option_type or "").strip().lower()
    if normalized_type not in {"call", "put"}:
        raise RollSimulationError("invalid_input", "position option type must be CALL or PUT")
    chain, chain_warnings = _roll_chain_payload(
        chain,
        required_side="calls" if normalized_type == "call" else "puts",
    )
    try:
        current_exp = date.fromisoformat(str(current_expiration))
        target_exp = date.fromisoformat(str(target_expiration))
    except ValueError:
        raise RollSimulationError("invalid_input", "expiration must use YYYY-MM-DD")
    if current_exp == target_exp and current_strike_d == target_strike_d:
        raise RollSimulationError(
            "same_contract",
            "Target contract is identical to the current contract and is not a roll",
        )

    current_contract = get_contract(chain, current_strike_d, current_exp.isoformat(), normalized_type)
    target_contract = get_contract(chain, target_strike_d, target_exp.isoformat(), normalized_type)
    if current_contract is None:
        raise RollSimulationError("current_contract_not_found", "Current contract is not in the option chain")
    if target_contract is None:
        raise RollSimulationError("target_contract_not_found", "Exact target contract was not found")

    current_mid = _two_sided_midpoint(current_contract, leg="current")
    target_mid = _two_sided_midpoint(target_contract, leg="target")
    per_share_net = (target_mid - current_mid).quantize(_MONEY_4, rounding=ROUND_HALF_UP)
    multiplier_d = Decimal(multiplier)
    per_contract_net = (per_share_net * multiplier_d).quantize(_MONEY_2, rounding=ROUND_HALF_UP)
    total_net = (per_contract_net * abs(contracts_d)).quantize(_MONEY_2, rounding=ROUND_HALF_UP)
    outcome = "credit" if total_net > 0 else "debit" if total_net < 0 else "even"

    return {
        "pricing_method": "robust_midpoint",
        "option_type": normalized_type.upper(),
        "contracts": int(contracts_d),
        "multiplier": multiplier,
        "current_contract": {
            "strike": canonical_strike(current_strike_d),
            "expiration": current_exp.isoformat(),
            "midpoint": float(current_mid),
            **_quote_provenance(current_contract),
        },
        "target_contract": {
            "strike": canonical_strike(target_strike_d),
            "expiration": target_exp.isoformat(),
            "midpoint": float(target_mid),
            **_quote_provenance(target_contract),
        },
        "per_share_net": float(per_share_net),
        "per_contract_net": float(per_contract_net),
        "total_net": float(total_net),
        "outcome": outcome,
        "chain_timestamp": chain.get("timestamp"),
        "chain_source": chain.get("source") or chain.get("_source"),
        "chain_warnings": chain_warnings,
        "informational_only": True,
        "commissions_included": False,
    }


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
        Number of contracts. API callers resolve this from the exact position;
        the default remains only for direct helper compatibility.
    num_expiries : int
        Number of future expirations after current_expiration to include.
    strike_offsets : tuple[float]
        Strike selection offsets relative to underlying_price.
        0.0 → ATM (closest), +0.03 → +3%, -0.03 → -3%.

    Returns
    -------
    dict
        {
          "buyback_cost":         float | None, # total executable cost to close
          "buyback_per_share":    float | None, # executable ask of current contract
          "pct_captured":         float | None, # null when buyback ask is unavailable
          "profit_target_reached":bool,    # pct_captured >= 0.70
          "buyback_available":    bool,
          "incomplete_data":      bool,
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
        buyback_per_share = executable_buyback_ask(usable_quote(current_contract, "ask"))
    else:
        logger.warning(
            "roll_table: current contract not found (strike=%.2f, exp=%s) — buyback unavailable",
            current_strike,
            current_expiration,
        )
        buyback_per_share = None

    buyback_available = buyback_per_share is not None
    incomplete_data = not buyback_available
    buyback_cost = (
        round(buyback_per_share * 100 * contracts, 2)
        if buyback_per_share is not None
        else None
    )

    # ── 2. Profit-capture metrics ──────────────────────────────────────────
    if buyback_per_share is None:
        pct_captured = None
    elif premium_received and premium_received > 0:
        pct_captured = round(
            (premium_received - buyback_per_share) / premium_received, 4
        )
    else:
        pct_captured = 0.0
    profit_target_reached: bool = (
        pct_captured is not None and pct_captured >= _PROFIT_TARGET_PCT
    )

    # ── 3. Select expirations: previous (optional) + current + next N ──────
    current_exp_key = _to_exp_key(current_expiration)
    today = date.today()

    sorted_exp_keys = sorted(
        k for k in bucket.keys() if _parse_exp_key(k) is not None
    )

    # Previous FUTURE expiration strictly before current (roll-in candidate).
    # Only shown when such an expiration still exists ahead of today; if the
    # current option is already the nearest expiration, there is no previous.
    prev_candidates = [
        k for k in sorted_exp_keys
        if k < current_exp_key and (_parse_exp_key(k) or date.min) > today
    ]
    prev_exp_key = prev_candidates[-1] if prev_candidates else None

    # Next N expirations strictly after current
    future_exp_keys = [
        k for k in sorted_exp_keys
        if k > current_exp_key and (_parse_exp_key(k) or date.min) > today
    ][:num_expiries]

    # Ordered columns: previous (if any) → current (always) → futures
    ordered_keys: list[str] = []
    if prev_exp_key is not None:
        ordered_keys.append(prev_exp_key)
    ordered_keys.append(current_exp_key)
    ordered_keys.extend(future_exp_keys)

    exp_entries: list[dict] = []
    for k in ordered_keys:
        d = _parse_exp_key(k)
        dte = (d - today).days if d else 0
        exp_entries.append({
            "key": k,
            "date": _to_display_date(k),
            "dte": dte,
            "is_current": k == current_exp_key,
            "is_previous": prev_exp_key is not None and k == prev_exp_key,
        })

    # ── 4. Build rows × cells ──────────────────────────────────────────────
    rows: list[dict] = []

    for offset in strike_offsets:
        label = _label_for_offset(offset)
        target = underlying_price * (1.0 + offset)

        # Determine the strike ONCE, from the first expiration that has
        # available strikes, then reuse the SAME strike across all expirations.
        chosen_strike: Optional[float] = None
        for exp in exp_entries:
            strikes_dict = bucket.get(exp["key"], {})
            available_first: list[float] = []
            for sk in strikes_dict:
                try:
                    available_first.append(float(sk))
                except (ValueError, TypeError):
                    pass
            if available_first:
                available_first.sort()
                chosen_strike = _select_strike(available_first, target, offset)
                if chosen_strike is not None:
                    break

        cells: list[dict] = []

        for exp in exp_entries:
            exp_key = exp["key"]
            exp_display = exp["date"]
            dte = exp["dte"]

            if chosen_strike is None:
                cells.append(_gray_cell(exp_display, dte))
                continue

            strikes_dict = bucket.get(exp_key, {})

            # Look up the SAME chosen strike in this expiration
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

            new_bid = usable_quote(contract, "bid")
            new_ask = usable_quote(contract, "ask")
            raw_delta = usable_greek(contract, "delta")
            delta: Optional[float] = round(raw_delta, 4) if raw_delta is not None else None

            new_premium = round(new_bid * 100 * contracts, 2) if new_bid is not None else None
            net_credit = (
                round(new_premium - buyback_cost, 2)
                if (new_premium is not None and buyback_cost is not None)
                else None
            )

            if new_bid is None or net_credit is None:
                color = "gray"
            elif net_credit > 0:
                color = "green"
            else:
                color = "red"

            cells.append(
                {
                    "expiration": exp_display,
                    "dte": dte,
                    "strike": chosen_strike,
                    "bid": round(new_bid, 2) if new_bid is not None else None,
                    "ask": round(new_ask, 2) if new_ask is not None else None,
                    "delta": delta,
                    "net_credit": net_credit,
                    "color": color,
                }
            )

        rows.append(
            {
                "offset": offset,
                "label": label,
                "strike": chosen_strike,
                "cells": cells,
            }
        )

    # ── 5. Assemble result ─────────────────────────────────────────────────
    return {
        "buyback_cost": buyback_cost,
        "buyback_per_share": (
            round(buyback_per_share, 4)
            if buyback_per_share is not None
            else None
        ),
        "pct_captured": pct_captured,
        "profit_target_reached": profit_target_reached,
        "buyback_available": buyback_available,
        "incomplete_data": incomplete_data,
        "underlying_price": underlying_price,
        "chain_timestamp": chain_timestamp,
        "current_position": {
            "strike": current_strike,
            "expiration": _to_display_date(current_expiration),
            "option_type": option_type,
            "premium_received": premium_received,
            "buyback_available": buyback_available,
            "incomplete_data": incomplete_data,
        },
        "expirations": [
            {
                "date": e["date"],
                "dte": e["dte"],
                "is_current": e["is_current"],
                "is_previous": e["is_previous"],
            }
            for e in exp_entries
        ],
        "rows": rows,
    }
