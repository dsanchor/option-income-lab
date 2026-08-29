"""Deterministic "Best Options" evaluator.

Design: `.squad/decisions/inbox/danny-best-options-design.md` ("Best
Options" Analyze Page, accepted 2026-08-29). Pure, no I/O, no LLM, no
Cosmos, no FastAPI: identical input must produce byte-identical output.

Two layers (design §4):

  * Layer A — two binary safety gates on tradability and earnings span. A
    gate failure colours a row **red** and nulls its `score` (absence is
    not zero, mirroring the 2026-08-18 accumulated-chain rule) but the row
    is still returned — nothing is ever silently dropped by a safety gate.
  * Layer B — a graded 0..100 quality score from four components
    (annualized return, cushion, delta fit, liquidity). The premium floor
    is graded (DTE-scaled), never a hard gate, so aristocrat-style
    structurally-low-IV names don't empty the table (design §4.3).

**Row inclusion is governed by TWO user-facing filters, applied before a
contract is ever scored:** the DTE window (`options_chain_filters.
filter_options_chain_by_dte`, applied by the caller before this module
sees a contract) and the category-aware delta band (`abs(delta)` inside
`[delta_lo, delta_hi]` for the requested side/category, applied locally
by `_evaluate_side` below). Only contracts surviving *both* filters
appear in a side's `rows`. This intentionally does NOT reuse
`filter_options_chain_by_delta` (design's F2 finding: that function's
bands are wide, not category-aware, and it reads `contract.get("delta")`
directly) — the equivalent check here goes through this module's own
`_gate_delta_band`, which reads delta only via the `options_chain_view`
accessors and uses the category's own configured band.

A contract excluded by the delta band is never silently discarded: it is
still considered when computing `nearest_miss` (so "just outside the
band" remains visible as the direct answer to "why am I not seeing this
contract"), and each side's result reports `excluded_by_delta_band`, a
count of how many DTE-window contracts were dropped by the delta filter.
It never appears in `rows` itself.

**Provenance note (interpretive decision, superseding an earlier reading
of this module):** design §4.1's literal text ("nothing inside the [DTE]
window is ever hidden") and §4.2's framing of delta band as "Layer A gate
G2" read, taken alone, as if delta band only coloured a row red rather
than excluding it — Linus's first implementation of this module took that
reading. This was corrected per an explicit product-owner instruction
(2026-08-29) that the displayed chain must be filtered by the configured
delta range in addition to the DTE window, with only contracts surviving
*both* filters shown as primary rows; excluded contracts remain
describable via `nearest_miss`/`excluded_by_delta_band`. See
`.squad/decisions/inbox/linus-best-options-scoring.md` for the full
account of both readings.

Every quote/Greek read goes through the `options_chain_view` accessors
(`contract_view` / `is_candidate_eligible`) -- never a direct
`contract.get("bid"/"ask"/"delta"/...)` (Danny's acceptance gate #2).
`iv_rank` is never read or enforced anywhere in this module: it is not
observable from yfinance (see `src/volatility.py`'s module docstring) and
the category `iv_rank_min` threshold is reported to the caller for display
only (design F3).
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

from src.category_params import category_label, resolve_category, thresholds_for
from src.options_chain_filters import filter_options_chain_by_dte
from src.options_chain_view import contract_view, is_candidate_eligible

SCHEMA_VERSION = 1

# Layer B component weights (design §4.3 table). Sum to 1.0.
WEIGHTS: Dict[str, float] = {
    "annualized_return": 0.45,
    "cushion": 0.25,
    "delta_fit": 0.20,
    "liquidity": 0.10,
}

# Colour thresholds on the 0..100 score (design §4.4).
COLOR_THRESHOLDS: Dict[str, int] = {"green": 65, "yellow": 40}

LIQUIDITY_DEFAULTS: Dict[str, float] = {
    "min_open_interest": 1,      # matches options_chain_view.is_candidate_eligible's own default
    "max_spread_pct": 0.25,      # design §4.3 liquidity formula constant
}

# Default DTE window (design §4.1/§7). Exposed publicly so the FastAPI
# endpoint (Rusty's layer) imports these instead of re-declaring "49"
# independently — a second source of truth for the window this module's
# own provenance block reports against.
DEFAULT_DTE_MIN = 0
DEFAULT_DTE_MAX = 49

# The agents' own hard DTE cap (rule_evaluator._dte_cap_rule's "DTE <= 45").
# rule_evaluator has no public constant for it (an inline literal there);
# reproduced here as an explicit, documented copy rather than reaching into
# a private name — same pattern dps_scorer.py already uses for its own
# stale-seconds default.
SYSTEM_DTE_CAP = 45

# Mirrors dps_scorer.py's own local copy of
# options_chain_cache.stale_quote_warn_seconds's default; this module has
# no config.yaml access either, so it is not a second source of truth to
# reconcile, just the same documented duplication dps_scorer.py already
# established.
_DEFAULT_STALE_AFTER_SECONDS = 86400

_MAX_ROWS_PER_SIDE = 400

_SIDES = ("call", "put")
_SIDE_STRATEGY = {"call": "covered_call", "put": "cash_secured_put"}
_SIDE_BUCKET = {"call": "calls", "put": "puts"}
_COLOR_LABELS = {"green": "Preferred", "yellow": "Acceptable", "red": "Avoid"}

_ET_ZONE = ZoneInfo("America/New_York")  # mirrors options_chain_cache._ET_ZONE


# ---------------------------------------------------------------------------
# Small, total, side-effect-free helpers
# ---------------------------------------------------------------------------

def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _finite(value: Any) -> Optional[float]:
    """Total numeric coercion: a real, finite number of either sign, or
    None. Never coerces None/NaN/bool into 0 (mirrors dps_scorer.py's
    `_finite_or_none` -- a missing/invalid input must stay missing, not
    silently become a worst/best-case scoring signal)."""
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


def _safe_int(value: Any, default: int = 0) -> int:
    """Total int coercion for caller-supplied scalars (`total_shares`,
    `dte_min`/`dte_max`) -- a non-numeric/malformed value degrades to
    `default` rather than raising, keeping `evaluate_best_options` total
    by construction instead of via a blanket try/except."""
    coerced = _finite(value)
    return int(coerced) if coerced is not None else default


def _parse_expiration(exp_key: Any) -> Optional[date]:
    """Parse a chain expiration key (``YYYYMMDD`` or ``YYYY-MM-DD``) into a
    calendar date, or None. A small, local, intentionally-duplicated copy
    of the same parse every options-chain module in this codebase already
    implements independently (options_chain_merge._parse_expiration_date,
    options_chain_filters._dte, dps_scorer._compute_dte) rather than a
    fifth cross-module coupling to any one of their private helpers."""
    try:
        digits = str(exp_key).replace("-", "")
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except (ValueError, IndexError, TypeError):
        return None


def _dte(exp_key: Any, today_et: date) -> Optional[int]:
    exp_date = _parse_expiration(exp_key)
    if exp_date is None:
        return None
    return (exp_date - today_et).days


def _parse_date_param(value: Any) -> Optional[date]:
    """Total parse of an optional caller-supplied ISO date (`YYYY-MM-DD`
    string, a `date`/`datetime`, or None/malformed -> None). Callers (the
    FastAPI layer) pass already-resolved values; this module never raises
    on a bad one -- an unparseable earnings/ex-dividend date is treated the
    same as "not known" (design F10: unknown must not be conflated with a
    gate failure)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def _today_et(now: datetime) -> date:
    """Design §4.1/§8.11: DTE is computed in America/New_York, not UTC --
    using UTC "today" flips DTE by one after 20:00 ET and silently moves
    rows across the `dte_max` boundary. Mirrors
    `options_chain_cache._ET_ZONE`'s same-day-pruning convention."""
    aware_now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return aware_now.astimezone(_ET_ZONE).date()


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Layer A — hard gates (design §4.2)
# ---------------------------------------------------------------------------

def _gate_tradability(contract: Mapping) -> str:
    """G1: `is_candidate_eligible` -- usable bid, OI >= 1, Greeks valid. A
    contract that cannot be sold at all is not a candidate at any price."""
    return "pass" if is_candidate_eligible(
        contract, min_open_interest=LIQUIDITY_DEFAULTS["min_open_interest"]
    ) else "fail"


def _gate_delta_band(abs_delta: Optional[float], delta_lo: float, delta_hi: float) -> str:
    """`abs(delta)` inside the category band for the side. A missing delta
    cannot be confirmed to be in-band, so it fails closed rather than
    passing open.

    Despite the name, this is used as a row-INCLUSION filter by
    `_evaluate_side` (a "fail" result excludes the contract from `rows`
    entirely), not as a colour-only gate -- see the module docstring's
    provenance note. It is still computed and returned as part of every
    row's `gates` (always "pass" for anything that made it into `rows`)
    so the response schema matches design §7's row shape, and so
    `_nearest_miss` can describe delta-excluded contracts using the exact
    same predicate."""
    if abs_delta is None:
        return "fail"
    return "pass" if delta_lo <= abs_delta <= delta_hi else "fail"


def _gate_earnings_span(exp_date: Optional[date], next_earnings_date: Optional[date]) -> str:
    """G3, named "earnings span": the gate FAILS when the option's window
    would span a known earnings event -- i.e. expiration falls AFTER the
    next earnings date, so a newly-opened position would remain open
    through the announcement (`src/skills/earnings-gate-sell/SKILL.md`:
    "the key risk is opening a position that remains open during
    earnings"). It PASSES when expiration is on/before the known earnings
    date (the position safely resolves before the event). An unknown
    earnings date is explicitly NOT a gate failure (design F10) -- it is
    the informational `earnings_date_unknown` flag instead, so an unsynced
    symbol does not become permanently non-green."""
    if next_earnings_date is None:
        return "unknown"
    if exp_date is None:
        return "unknown"
    return "fail" if exp_date > next_earnings_date else "pass"


# ---------------------------------------------------------------------------
# Layer B — 0..1 score components (design §4.3)
# ---------------------------------------------------------------------------

def _component_annualized_return(
    premium_pct: Optional[float], dte: Optional[int], premium_min_pct: float
) -> Optional[float]:
    if premium_pct is None or dte is None or dte <= 0:
        return None
    floor_ann = premium_min_pct * 365 / 30
    if floor_ann <= 0:
        return None
    ann = premium_pct * 365 / dte
    return _clamp(ann / floor_ann, 0.0, 2.0) / 2.0


def _component_cushion(
    side: str, spot: Optional[float], strike: float, iv: Optional[float], dte: Optional[int]
) -> Optional[float]:
    if spot is None or spot <= 0 or iv is None or dte is None or dte <= 0:
        return None
    sigma = spot * iv * math.sqrt(dte / 365.0)
    if sigma <= 1e-9:
        return None
    ratio = (strike - spot) / sigma if side == "call" else (spot - strike) / sigma
    return _clamp(ratio / 1.5, 0.0, 1.0)


def _component_delta_fit(abs_delta: Optional[float], delta_lo: float, delta_hi: float) -> Optional[float]:
    if abs_delta is None:
        return None
    midpoint = (delta_lo + delta_hi) / 2.0
    half_width = (delta_hi - delta_lo) / 2.0
    if half_width <= 0:
        return 1.0
    distance = abs(abs_delta - midpoint)
    fit = 1.0 - 0.5 * (distance / half_width)
    return _clamp(fit, 0.0, 1.0)


def _component_liquidity(
    open_interest: Optional[float], bid: Optional[float], ask: Optional[float], mid: Optional[float]
) -> Optional[float]:
    """Both halves of the design's formula must be computable, or the
    whole component is missing (renormalised away at the top level) --
    there is no partial-formula fallback invented here beyond what §4.3
    literally specifies."""
    if open_interest is None or bid is None or ask is None or mid is None or mid <= 0:
        return None
    spread_pct = (ask - bid) / mid
    oi_part = _clamp(math.log10(open_interest + 1) / 3.0, 0.0, 1.0)
    spread_part = _clamp(1.0 - spread_pct / LIQUIDITY_DEFAULTS["max_spread_pct"], 0.0, 1.0)
    return 0.5 * oi_part + 0.5 * spread_part


# ---------------------------------------------------------------------------
# One row (one contract) — the ONLY place gates + score + colour combine
# ---------------------------------------------------------------------------

def _build_row(
    contract: Mapping,
    *,
    exp_key: str,
    strike: float,
    side: str,
    thresholds: Dict[str, Any],
    spot: Optional[float],
    now: datetime,
    today_et: date,
    next_earnings_date: Optional[date],
    ex_dividend_date: Optional[date],
    support_level: Optional[float],
) -> Dict[str, Any]:
    view = contract_view(contract, now=now, stale_after_seconds=_DEFAULT_STALE_AFTER_SECONDS)
    meta = view.get("_meta") if isinstance(view.get("_meta"), Mapping) else {}

    bid = view.get("bid")
    ask = view.get("ask")
    mid = view.get("mid")
    iv = view.get("iv")
    delta = view.get("delta")
    abs_delta = abs(delta) if delta is not None else None
    open_interest = _finite(view.get("openInterest"))  # Z2 carve-out: real 0 is decisive, kept as-is

    exp_date = _parse_expiration(exp_key)
    dte = _dte(exp_key, today_et)

    delta_lo, delta_hi = thresholds["delta_lo"], thresholds["delta_hi"]
    gates = {
        "tradability": _gate_tradability(contract),
        "delta_band": _gate_delta_band(abs_delta, delta_lo, delta_hi),
        "earnings_span": _gate_earnings_span(exp_date, next_earnings_date),
    }
    safety_pass = gates["tradability"] == "pass" and gates["delta_band"] == "pass" and gates["earnings_span"] != "fail"

    basis = spot if side == "call" else strike
    premium_pct = (bid / basis * 100.0) if (bid is not None and basis is not None and basis > 0) else None
    effective_min_pct = thresholds["premium_min_pct"] * dte / 30.0 if dte is not None else None
    effective_wait_pct = thresholds["premium_wait_pct"] * dte / 30.0 if dte is not None else None
    annualized_return_pct = (premium_pct * 365.0 / dte) if (premium_pct is not None and dte and dte > 0) else None

    components: Dict[str, float] = {}
    components_missing: List[str] = []
    if safety_pass:
        candidates = {
            "annualized_return": _component_annualized_return(premium_pct, dte, thresholds["premium_min_pct"]),
            "cushion": _component_cushion(side, spot, strike, iv, dte),
            "delta_fit": _component_delta_fit(abs_delta, delta_lo, delta_hi),
            "liquidity": _component_liquidity(open_interest, bid, ask, mid),
        }
        for name, value in candidates.items():
            if value is None:
                components_missing.append(name)
            else:
                components[name] = value

    weight_basis = sum(WEIGHTS[name] for name in components) if safety_pass else 0.0
    insufficient_data = safety_pass and weight_basis < 0.5
    if not safety_pass or insufficient_data:
        score: Optional[int] = None
    else:
        raw_score = sum(components[name] * WEIGHTS[name] for name in components) / weight_basis
        score = round(raw_score * 100.0)

    flags: List[str] = []
    below_wait_floor = (
        safety_pass and premium_pct is not None and effective_wait_pct is not None
        and premium_pct < effective_wait_pct
    )
    if not safety_pass:
        color = "red"
    elif below_wait_floor:
        color = "red"
        flags.append("premium_below_wait_floor")
    elif insufficient_data:
        color = "yellow"
        flags.append("insufficient_data")
    elif score is not None and score >= COLOR_THRESHOLDS["green"]:
        color = "green"
    elif score is not None and score >= COLOR_THRESHOLDS["yellow"]:
        color = "yellow"
    else:
        color = "red"

    # Flags that never change colour (design F10) — badges only.
    if next_earnings_date is None:
        flags.append("earnings_date_unknown")
    if meta.get("stale"):
        flags.append("stale_quote")
    if dte is not None and dte < 7:
        flags.append("very_short_dte")
    if dte is not None and dte > SYSTEM_DTE_CAP:
        flags.append("exceeds_system_dte_cap")
    if premium_pct is not None and effective_min_pct is not None and premium_pct < effective_min_pct:
        flags.append("below_category_floor")
    if side == "call" and ex_dividend_date is not None and dte is not None and 0 <= (ex_dividend_date - today_et).days <= dte:
        if spot is not None and spot > 0 and strike < spot * 1.10:
            flags.append("ex_div_within_dte")
    if side == "put" and support_level is not None and strike <= support_level:
        flags.append("below_support")

    return {
        "expiration": exp_key,
        "dte": dte,
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "iv": iv,
        "delta": delta,
        "abs_delta": abs_delta,
        "open_interest": open_interest,
        "premium_pct": premium_pct,
        "annualized_return_pct": annualized_return_pct,
        "effective_min_pct": effective_min_pct,
        "effective_wait_pct": effective_wait_pct,
        "collateral": (strike * 100.0) if side == "put" else None,
        "score": score,
        "color": color,
        "label": _COLOR_LABELS[color],
        "components": components,
        "components_missing": components_missing,
        "weight_basis": round(weight_basis, 4),
        "gates": gates,
        "flags": flags,
        "quote_asof": meta.get("quote_asof"),
        "stale": bool(meta.get("stale")),
    }


# ---------------------------------------------------------------------------
# Total ordering + truncation (design §4.5)
# ---------------------------------------------------------------------------

def _row_sort_key(row: Dict[str, Any], delta_midpoint: float) -> Tuple[float, float, float]:
    """1) score desc (None last)  2) DTE asc  3) |abs_delta - midpoint| asc
    (None last on every axis, via +inf)."""
    score = row["score"]
    score_key = -score if score is not None else float("inf")
    dte = row["dte"]
    dte_key = float(dte) if dte is not None else float("inf")
    abs_delta = row["abs_delta"]
    delta_key = abs(abs_delta - delta_midpoint) if abs_delta is not None else float("inf")
    return (score_key, dte_key, delta_key)


def _order_and_truncate(rows: List[Dict[str, Any]], delta_lo: float, delta_hi: float) -> Tuple[List[Dict[str, Any]], bool]:
    midpoint = (delta_lo + delta_hi) / 2.0
    ordered = sorted(rows, key=lambda r: _row_sort_key(r, midpoint))
    truncated = len(ordered) > _MAX_ROWS_PER_SIDE
    return ordered[:_MAX_ROWS_PER_SIDE], truncated


# ---------------------------------------------------------------------------
# nearest_miss (design §4.6) — always computed, the direct answer to
# "why am I not getting alerts."
# ---------------------------------------------------------------------------

def _nearest_miss(rows: List[Dict[str, Any]], delta_lo: float, delta_hi: float) -> Dict[str, Any]:
    """Among every non-green contract in the DTE window -- including ones
    `_evaluate_side` excludes from `rows` for being outside the category
    delta band -- find the one closest to qualifying. `rows` (this
    function's `rows` parameter) is the FULL DTE-window set here, not the
    delta-filtered primary rows: a contract just outside the band must
    still be describable as "closest to qualifying" even though it will
    never appear in the primary table.

    "Closest" is a tiered, deterministic ordering from most- to
    least-fixable, each tier ranked internally by a quantified gap where
    one exists:

      0. Safety passes, only the graded premium floor is missed — exactly
         the case the design's own example illustrates ("missed premium
         floor by 0.12pp"). Gap = pp short of `effective_wait_pct`.
      1. Safety passes, floor is cleared, but the yellow-band score (40..65)
         needs more points. Gap = points short of 65.
      2. Safety passes but >50% of score weight is missing
         (`insufficient_data`) — not quantifiable as a "gap", ranked below
         a real score.
      3. Outside the category delta band (excluded from `rows` by
         `_evaluate_side`, not merely coloured red) — quantifiable as
         delta-points outside the nearest band edge.
      4. G3 earnings-span gate failed — not quantifiable (a hard calendar
         fact), ranked worse than a graded/delta miss.
      5. G1 tradability gate failed — the least fixable failure (no
         market at all), always ranked worst.

    A lower (tier, gap) always beats a higher tier regardless of gap
    magnitude — tiers are not a continuous scale, gaps only break ties
    within a tier."""
    best: Optional[Dict[str, Any]] = None
    best_key: Optional[Tuple[int, float, float]] = None

    for row in rows:
        if row["color"] == "green":
            continue
        gates = row["gates"]
        dte_key = float(row["dte"]) if row["dte"] is not None else float("inf")

        if gates["tradability"] == "fail":
            tier, gap = 5, 0.0
            reason, detail = "tradability", "no usable bid, insufficient open interest, or invalid Greeks"
        elif gates["delta_band"] == "fail":
            tier = 3
            reason = "delta_band"
            ad = row["abs_delta"]
            if ad is None:
                gap = 999.0
                detail = "delta unavailable -- cannot confirm band membership"
            else:
                gap = min(abs(ad - delta_lo), abs(ad - delta_hi))
                detail = f"|delta| {ad:.3f} outside band [{delta_lo:.2f}, {delta_hi:.2f}] by {gap:.3f}"
        elif gates["earnings_span"] == "fail":
            tier, gap = 4, 0.0
            reason, detail = "earnings_span", "expiration does not clear the next known earnings date"
        elif "premium_below_wait_floor" in row["flags"]:
            tier = 0
            reason = "premium_floor"
            gap = (row["effective_wait_pct"] or 0.0) - (row["premium_pct"] or 0.0)
            ad = row["abs_delta"]
            ad_str = f" at |delta| {ad:.2f}" if ad is not None else ""
            detail = f"missed premium floor by {gap:.2f}pp{ad_str}"
        elif row["score"] is None:
            tier, gap = 2, 100.0
            reason, detail = "insufficient_data", "insufficient scoring data (>50% of weight missing)"
        else:
            tier = 1
            gap = COLOR_THRESHOLDS["green"] - row["score"]
            reason, detail = "score", f"scored {row['score']}, needs {gap:.0f} more point(s) to reach green"

        key = (tier, gap, dte_key)
        if best_key is None or key < best_key:
            best_key = key
            best = {
                "expiration": row["expiration"],
                "dte": row["dte"],
                "strike": row["strike"],
                "abs_delta": row["abs_delta"],
                "score": row["score"],
                "color": row["color"],
                "reason": reason,
                "detail": detail,
                "gap": round(gap, 4),
            }

    if best is None:
        reason = "all_rows_qualify" if rows else "no_contracts_in_window"
        return {"available": False, "reason": reason}
    return {"available": True, **best}


# ---------------------------------------------------------------------------
# One side (calls or puts)
# ---------------------------------------------------------------------------

def _evaluate_side(
    bucket: Dict[str, Any],
    *,
    side: str,
    thresholds: Dict[str, Any],
    spot: Optional[float],
    now: datetime,
    today_et: date,
    next_earnings_date: Optional[date],
    ex_dividend_date: Optional[date],
    support_level: Optional[float],
    total_shares: int,
) -> Dict[str, Any]:
    all_rows: List[Dict[str, Any]] = []
    for exp_key, strikes in (bucket or {}).items():
        if not isinstance(strikes, dict):
            continue
        for strike_key, contract in strikes.items():
            if not isinstance(contract, dict):
                continue
            try:
                strike = float(strike_key)
            except (TypeError, ValueError):
                strike = _finite(contract.get("strike")) or 0.0
            all_rows.append(_build_row(
                contract,
                exp_key=exp_key,
                strike=strike,
                side=side,
                thresholds=thresholds,
                spot=spot,
                now=now,
                today_et=today_et,
                next_earnings_date=next_earnings_date,
                ex_dividend_date=ex_dividend_date,
                support_level=support_level,
            ))

    # `nearest_miss` is computed over EVERY DTE-window contract, including
    # ones the delta-band filter below excludes -- a contract just outside
    # the band is exactly the kind of "why am I not seeing this" case it
    # exists to explain (design §4.6). `rows` itself, however, is the
    # DTE-window AND delta-band intersection: only contracts that pass
    # `_gate_delta_band` are primary, user-facing rows (product-owner
    # instruction, 2026-08-29 -- see module docstring's provenance note).
    nearest_miss = _nearest_miss(all_rows, thresholds["delta_lo"], thresholds["delta_hi"])
    in_band_rows = [r for r in all_rows if r["gates"]["delta_band"] == "pass"]
    ordered_rows, truncated = _order_and_truncate(in_band_rows, thresholds["delta_lo"], thresholds["delta_hi"])

    result: Dict[str, Any] = {
        "rows": ordered_rows,
        "nearest_miss": nearest_miss,
        "truncated": truncated,
        "total": len(in_band_rows),
        "excluded_by_delta_band": len(all_rows) - len(in_band_rows),
    }
    if side == "call":
        # Section 5: capital is a page/section-level banner, not a per-row
        # field — total_shares is symbol-wide, not per-contract.
        coverable = max(_safe_int(total_shares), 0) // 100
        result["coverable_contracts"] = coverable
        result["no_shares_held"] = coverable == 0
    return result


# ---------------------------------------------------------------------------
# ATM IV (context only — never scored, never gated; design §6)
# ---------------------------------------------------------------------------

def _atm_iv(
    calls_bucket: Dict[str, Any],
    puts_bucket: Dict[str, Any],
    spot: Optional[float],
    now: datetime,
    today_et: date,
) -> Optional[float]:
    """Nearest-DTE, then nearest-strike-to-spot contract with a usable IV,
    across both sides. Computed from the chain alone (no price-history
    I/O) — shown in the parameters panel as context only; never a score
    component (design §4.3's `vol_richness` removal rationale applies
    equally here: symbol-level context, not row-level ordering signal)."""
    best_iv: Optional[float] = None
    best_key: Optional[Tuple[int, float]] = None
    for bucket in (calls_bucket, puts_bucket):
        for exp_key, strikes in (bucket or {}).items():
            dte = _dte(exp_key, today_et)
            if dte is None:
                continue
            for strike_key, contract in (strikes or {}).items():
                if not isinstance(contract, dict):
                    continue
                try:
                    strike = float(strike_key)
                except (TypeError, ValueError):
                    continue
                iv = contract_view(contract, now=now, stale_after_seconds=_DEFAULT_STALE_AFTER_SECONDS).get("iv")
                if iv is None:
                    continue
                distance = abs(strike - spot) if spot is not None else 0.0
                key = (dte, distance)
                if best_key is None or key < best_key:
                    best_key = key
                    best_iv = iv
    return best_iv


# ---------------------------------------------------------------------------
# Public entrypoint (design §7)
# ---------------------------------------------------------------------------

def evaluate_best_options(
    chain: dict,
    *,
    side: str,
    category: Optional[str],
    total_shares: int,
    next_earnings_date: Optional[str],
    ex_dividend_date: Optional[str],
    support_level: Optional[float],
    dte_min: int,
    dte_max: int,
    now: datetime,
) -> dict:
    """Evaluate the Best Options table for one symbol's option chain.

    Pure, total, deterministic: identical input produces byte-identical
    output. `underlying_price` and `atm_iv` are read from `chain` itself
    (design F7) -- never from a separate parameter -- so they can never
    desynchronise from the Greeks the chain's own contracts were computed
    against.
    """
    if not isinstance(chain, Mapping):
        chain = {}

    normalized_side = side if side in ("call", "put", "both") else "both"
    sides_to_eval = ["call", "put"] if normalized_side == "both" else [normalized_side]

    today_et = _today_et(now if isinstance(now, datetime) else datetime.now(timezone.utc))
    lo_dte, hi_dte = _safe_int(dte_min, DEFAULT_DTE_MIN), _safe_int(dte_max, DEFAULT_DTE_MAX)
    if lo_dte > hi_dte:
        lo_dte, hi_dte = hi_dte, lo_dte

    filtered = filter_options_chain_by_dte(dict(chain), min_dte=lo_dte, max_dte=hi_dte, today_et=today_et)
    calls_bucket = filtered.get("calls") or {}
    puts_bucket = filtered.get("puts") or {}

    spot = _finite(chain.get("underlying_price"))
    category_key, defaulted = resolve_category(category)
    next_earn = _parse_date_param(next_earnings_date)
    ex_div = _parse_date_param(ex_dividend_date)
    support = _finite(support_level)

    thresholds_by_side = {
        "call": thresholds_for("covered_call", category_key),
        "put": thresholds_for("cash_secured_put", category_key),
    }

    sections: Dict[str, Dict[str, Any]] = {}
    for s in _SIDES:
        if s in sides_to_eval:
            bucket = calls_bucket if s == "call" else puts_bucket
            sections[s] = _evaluate_side(
                bucket, side=s, thresholds=thresholds_by_side[s], spot=spot,
                now=now, today_et=today_et, next_earnings_date=next_earn,
                ex_dividend_date=ex_div, support_level=support,
                total_shares=total_shares,
            )
        else:
            sections[s] = {
                "rows": [], "total": 0, "truncated": False, "excluded_by_delta_band": 0,
                "nearest_miss": {"available": False, "reason": "side_not_requested"},
            }
            if s == "call":
                sections[s]["coverable_contracts"] = None
                sections[s]["no_shares_held"] = None

    atm_iv = _atm_iv(calls_bucket, puts_bucket, spot, now, today_et)

    total_contracts = 0
    stale_contracts = 0
    quote_asof_values: List[str] = []
    for bucket in (calls_bucket, puts_bucket):
        for strikes in (bucket or {}).values():
            for contract in (strikes or {}).values() if isinstance(strikes, dict) else ():
                if not isinstance(contract, dict):
                    continue
                total_contracts += 1
                meta = contract_view(
                    contract, now=now, stale_after_seconds=_DEFAULT_STALE_AFTER_SECONDS
                ).get("_meta") or {}
                if meta.get("stale"):
                    stale_contracts += 1
                asof = meta.get("quote_asof")
                if isinstance(asof, str) and asof:
                    quote_asof_values.append(asof)

    dte_source = "default" if (lo_dte, hi_dte) == (DEFAULT_DTE_MIN, DEFAULT_DTE_MAX) else "query"
    cat_key_dash = category_key.replace("_", "-")

    parameters: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "evaluated_at": _iso(now if isinstance(now, datetime) else None),
        "category": {
            "value": category_key,
            "label": category_label(category_key),
            "raw": category,
            "source": "input.category",
            "defaulted": defaulted,
        },
        "thresholds": dict(thresholds_by_side),
        "thresholds_source": {
            "call": "backend/src/rule_evaluator.py:CATEGORY_THRESHOLDS_CC",
            "put": "backend/src/rule_evaluator.py:CATEGORY_THRESHOLDS_CSP",
        },
        "skill_reference": {
            "call": f"backend/src/skills/cc-{cat_key_dash}/SKILL.md",
            "put": f"backend/src/skills/csp-{cat_key_dash}/SKILL.md",
        },
        "iv_rank_enforced": False,
        "iv_rank_note": (
            "IV Rank is not observable from yfinance (see backend/src/volatility.py). "
            "It is NOT enforced here. The agent path evaluates it against a "
            "model-supplied value, so an agent WAIT citing IV Rank may not "
            "correspond to anything measurable."
        ),
        "dte": {
            "min": lo_dte, "max": hi_dte, "source": dte_source,
            "system_cap": SYSTEM_DTE_CAP, "timezone": "America/New_York",
        },
        "premium": {
            "basis": {"call": "underlying_price", "put": "strike"},
            "input_field": "bid",
            "dte_scaling": "effective_pct = base_pct * DTE / 30",
        },
        "liquidity": dict(LIQUIDITY_DEFAULTS),
        "underlying": {"price": spot, "source": "chain.underlying_price"},
        "atm_iv": atm_iv,
        "earnings": {
            "next_earnings_date": next_earn.isoformat() if next_earn is not None else None,
            "source": "cosmos.get_next_earnings_date",
            "known": next_earn is not None,
        },
        "chain": {
            "timestamp": chain.get("timestamp"),
            "quote_asof_min": min(quote_asof_values) if quote_asof_values else None,
            "quote_asof_max": max(quote_asof_values) if quote_asof_values else None,
            "stale_contracts": stale_contracts,
            "total_contracts": total_contracts,
        },
        "weights": dict(WEIGHTS),
        "color_thresholds": dict(COLOR_THRESHOLDS),
    }

    return {
        "symbol": chain.get("symbol"),
        "status": "ok",
        "schema_version": SCHEMA_VERSION,
        "parameters": parameters,
        "calls": sections["call"],
        "puts": sections["put"],
    }