"""Deterministic post-LLM rule evaluator.

Builds a structured, versioned ``rule_evaluation`` object from an agent's
already-produced ``activity_data`` dict. This module performs NO LLM calls —
it is a pure, deterministic post-processing step that runs after premium/roll
corrections and before the activity is persisted to CosmosDB.

Design reference: `.squad/decisions/inbox/danny-rule-evaluation-design.md`
(original design + Amendment A1, both accepted).

Public API:
    build_buy_tracker_evidence(fetch_data, now=None) -> dict
    normalize_buy_tracker_activity(activity_data, evidence) -> dict
    build_rule_evaluation(agent_type, activity_data, phase=None,
                           category=None, enrichment_data=None) -> dict
    merge_phase_evaluations(assessment_eval, roll_eval) -> dict
"""

from __future__ import annotations

import copy
import json
import math
import re
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from src.options_math import executable_buyback_ask

SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Status vocabulary & ordering
# ---------------------------------------------------------------------------

STATUS_BLOCKED = "blocked"
STATUS_FAIL = "fail"
STATUS_WARNING = "warning"
STATUS_UNKNOWN = "unknown"
STATUS_PASS = "pass"
STATUS_NOT_APPLICABLE = "not_applicable"
STATUS_INFORMATIONAL = "informational"

_ALL_STATUSES = (
    STATUS_PASS, STATUS_FAIL, STATUS_BLOCKED, STATUS_WARNING,
    STATUS_UNKNOWN, STATUS_NOT_APPLICABLE, STATUS_INFORMATIONAL,
)

# Render order (blocked -> fail -> warning -> unknown -> pass -> N/A -> info)
_STATUS_ORDER = {
    STATUS_BLOCKED: 0,
    STATUS_FAIL: 1,
    STATUS_WARNING: 2,
    STATUS_UNKNOWN: 3,
    STATUS_PASS: 4,
    STATUS_NOT_APPLICABLE: 5,
    STATUS_INFORMATIONAL: 6,
}

# ---------------------------------------------------------------------------
# Category-aware thresholds (A1.1) — sourced verbatim from src/skills/*/SKILL.md
# No invented values. Defaults to "balanced" when category is None/unrecognized.
# ---------------------------------------------------------------------------

CATEGORY_THRESHOLDS_CC: Dict[str, Dict[str, Any]] = {
    "aristocrat": {
        "delta_lo": 0.20, "delta_hi": 0.30,
        "premium_min_pct": 0.5, "premium_wait_pct": 0.3,
        "iv_rank_min": None,
    },
    "compounder": {
        "delta_lo": 0.15, "delta_hi": 0.25,
        "premium_min_pct": 0.6, "premium_wait_pct": 0.4,
        "iv_rank_min": 30,
    },
    "balanced": {
        "delta_lo": 0.20, "delta_hi": 0.30,
        "premium_min_pct": 0.8, "premium_wait_pct": 0.5,
        "iv_rank_min": 35,
    },
    "high_yield": {
        "delta_lo": 0.25, "delta_hi": 0.35,
        "premium_min_pct": 0.8, "premium_wait_pct": 0.5,
        "iv_rank_min": 30,
    },
    "rising_star": {
        "delta_lo": 0.10, "delta_hi": 0.20,
        "premium_min_pct": 0.8, "premium_wait_pct": 0.5,
        "iv_rank_min": 40,
    },
}

CATEGORY_THRESHOLDS_CSP: Dict[str, Dict[str, Any]] = {
    "aristocrat": {
        "delta_lo": 0.25, "delta_hi": 0.35,
        "premium_min_pct": 0.8, "premium_wait_pct": 0.5,
        "iv_rank_min": None,
    },
    "compounder": {
        "delta_lo": 0.20, "delta_hi": 0.30,
        "premium_min_pct": 1.0, "premium_wait_pct": 0.6,
        "iv_rank_min": 30,
    },
    "balanced": {
        "delta_lo": 0.20, "delta_hi": 0.30,
        "premium_min_pct": 1.2, "premium_wait_pct": 0.7,
        "iv_rank_min": 35,
    },
    "high_yield": {
        "delta_lo": 0.25, "delta_hi": 0.35,
        "premium_min_pct": 1.0, "premium_wait_pct": 0.6,
        "iv_rank_min": 25,
    },
    "rising_star": {
        "delta_lo": 0.15, "delta_hi": 0.25,
        "premium_min_pct": 1.2, "premium_wait_pct": 0.8,
        "iv_rank_min": 40,
    },
}

_CATEGORY_ALIASES = {
    "high-yield": "high_yield",
    "highyield": "high_yield",
    "rising-star": "rising_star",
    "risingstar": "rising_star",
}

# Public, traceable view of the category thresholds for external verification
# (e.g. tests asserting no invented values vs. src/skills/*/SKILL.md). Mirrors
# CATEGORY_THRESHOLDS_CC / CATEGORY_THRESHOLDS_CSP exactly, just reshaped with
# a `delta_range` tuple instead of separate `delta_lo`/`delta_hi` keys.
CATEGORY_THRESHOLDS: Dict[str, Dict[str, Dict[str, Any]]] = {
    "covered_call": {
        cat: {
            "delta_range": (vals["delta_lo"], vals["delta_hi"]),
            "premium_min_pct": vals["premium_min_pct"],
            "premium_wait_pct": vals["premium_wait_pct"],
            "iv_rank_min": vals["iv_rank_min"],
        }
        for cat, vals in CATEGORY_THRESHOLDS_CC.items()
    },
    "cash_secured_put": {
        cat: {
            "delta_range": (vals["delta_lo"], vals["delta_hi"]),
            "premium_min_pct": vals["premium_min_pct"],
            "premium_wait_pct": vals["premium_wait_pct"],
            "iv_rank_min": vals["iv_rank_min"],
        }
        for cat, vals in CATEGORY_THRESHOLDS_CSP.items()
    },
}


def _normalize_category(category: Optional[str]) -> str:
    """Normalize a raw category string to a CATEGORY_THRESHOLDS_* key.

    Defaults to "balanced" for None/unrecognized values (required default).
    """
    if not category:
        return "balanced"
    key = str(category).strip().lower().replace(" ", "_")
    key = _CATEGORY_ALIASES.get(key, key)
    if key not in CATEGORY_THRESHOLDS_CC:
        return "balanced"
    return key


def _category_label(category_key: str) -> str:
    return category_key.replace("_", " ").title()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _risk_flags(activity_data: Dict) -> List[str]:
    flags = activity_data.get("risk_flags")
    if isinstance(flags, list):
        return [str(f) for f in flags]
    return []


def _earnings_analysis(activity_data: Dict) -> Dict[str, Any]:
    """Return the `earnings_analysis` block, tolerating malformed (non-dict) input."""
    earnings = activity_data.get("earnings_analysis")
    if isinstance(earnings, dict):
        return earnings
    return {}


def _rule_checks(activity_data: Dict) -> Dict[str, Dict[str, Any]]:
    """Return the optional `rule_checks` block, tolerating malformed input."""
    checks = activity_data.get("rule_checks")
    if isinstance(checks, dict):
        return checks
    return {}


def _rule_check_for(activity_data: Dict, rule_id: str) -> Optional[Dict[str, Any]]:
    entry = _rule_checks(activity_data).get(rule_id)
    if isinstance(entry, dict):
        status = entry.get("status")
        if status in _ALL_STATUSES:
            return entry
    return None


def _rule(
    rule_id: str,
    label: str,
    group: str,
    status: str,
    expected: Optional[str],
    observed: Optional[str],
    source: str,
    detail: Optional[str] = None,
    blocking: bool = False,
    data_refs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "rule_id": rule_id,
        "label": label,
        "group": group,
        "status": status,
        "blocking": bool(blocking),
        "expected": expected,
        "observed": observed,
        "source": source,
        "detail": detail,
        "data_refs": data_refs or {},
    }


def _llm_rule_from_checks_or_fallback(
    rule_id: str,
    label: str,
    group: str,
    activity_data: Dict,
    expected: str,
    fallback_status: str,
    fallback_observed: str,
    fallback_detail: Optional[str] = None,
    blocking_when: Optional[tuple] = (STATUS_FAIL, STATUS_BLOCKED),
    data_refs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build an LLM-sourced RuleResult, preferring `rule_checks` over fallback.

    Evaluator preference order (A1.2): rule_checks -> fallback signal -> unknown.
    """
    check = _rule_check_for(activity_data, rule_id)
    if check is not None:
        status = check.get("status", STATUS_UNKNOWN)
        detail = check.get("detail") or f"rule_checks: {status}"
        observed = detail
        blocking = status in (blocking_when or ())
        return _rule(
            rule_id, label, group, status, expected, observed,
            "llm", detail=detail, blocking=blocking, data_refs=data_refs,
        )
    # Fallback: heuristic signal already computed by caller
    blocking = fallback_status in (blocking_when or ())
    return _rule(
        rule_id, label, group, fallback_status, expected, fallback_observed,
        "llm", detail=fallback_detail, blocking=blocking, data_refs=data_refs,
    )


# ---------------------------------------------------------------------------
# Shared earnings-gate mapping (CC / CSP)
# ---------------------------------------------------------------------------

_SELL_EARNINGS_GATE_PASS = {"OPEN_NORMALLY", "ALLOWED", "IDEAL", "CONSERVATIVE_DTE"}
_SELL_EARNINGS_GATE_WARNING = {"ALLOWED_WITH_CAUTION"}
_SELL_EARNINGS_GATE_BLOCKED = {"BLOCKED"}

_MONITOR_EARNINGS_GATE_FAIL = {"ROLL_RECOMMENDED", "ROLL_URGENTLY", "CLOSE_OR_ROLL"}
_MONITOR_EARNINGS_GATE_WARNING = {"FLAG", "FLAG_MEDIUM", "FLAG_HIGH", "HOLD_WITH_CAUTION"}
_MONITOR_EARNINGS_GATE_PASS = {"HOLD", "CONSERVATIVE"}


def _sell_earnings_gate_rule(rule_id: str, label: str, activity_data: Dict) -> Dict[str, Any]:
    earnings = _earnings_analysis(activity_data)
    gate_result = earnings.get("earnings_gate_result")
    days_to_earnings = earnings.get("days_to_earnings")
    gap = earnings.get("expiration_to_earnings_gap")

    if gate_result in _SELL_EARNINGS_GATE_BLOCKED:
        status, blocking = STATUS_BLOCKED, True
    elif gate_result in _SELL_EARNINGS_GATE_WARNING:
        status, blocking = STATUS_WARNING, False
    elif gate_result in _SELL_EARNINGS_GATE_PASS:
        status, blocking = STATUS_PASS, False
    else:
        status, blocking = STATUS_UNKNOWN, False

    observed = (
        f"{gate_result} (earnings in {days_to_earnings} days, gap {gap})"
        if gate_result is not None else "No earnings_analysis data available"
    )
    return _rule(
        rule_id, label, "calendar", status,
        "Earnings gate is not BLOCKED", observed, "deterministic",
        blocking=blocking,
        data_refs={
            "earnings_gate_result": gate_result,
            "days_to_earnings": days_to_earnings,
            "expiration_to_earnings_gap": gap,
        },
    )


def _monitor_earnings_gate_rule(rule_id: str, label: str, activity_data: Dict) -> Dict[str, Any]:
    earnings = _earnings_analysis(activity_data)
    gate_result = earnings.get("earnings_gate_result")
    days_to_earnings = earnings.get("days_to_earnings")
    gap = earnings.get("expiration_to_earnings_gap")

    if gate_result in _MONITOR_EARNINGS_GATE_FAIL:
        status, blocking = STATUS_FAIL, True
    elif gate_result in _MONITOR_EARNINGS_GATE_WARNING:
        status, blocking = STATUS_WARNING, False
    elif gate_result in _MONITOR_EARNINGS_GATE_PASS:
        status, blocking = STATUS_PASS, False
    elif gate_result is None:
        status, blocking = STATUS_UNKNOWN, False
    else:
        # Present but not a recognized enum value — treat conservatively as a
        # blocking failure rather than silently reporting "unknown" for a
        # safety-critical calendar gate.
        status, blocking = STATUS_FAIL, True

    observed = (
        f"{gate_result} (earnings in {days_to_earnings} days, expiration gap {gap})"
        if gate_result is not None else "No earnings_analysis data available"
    )
    return _rule(
        rule_id, label, "calendar", status,
        "Earnings gate does not require ROLL", observed, "deterministic",
        blocking=blocking,
        data_refs={
            "earnings_gate_result": gate_result,
            "days_to_earnings": days_to_earnings,
            "expiration_to_earnings_gap": gap,
        },
    )


def _dte_cap_rule(rule_id: str, label: str, activity_data: Dict, dte_key: str = "dte") -> Dict[str, Any]:
    activity = _upper(activity_data.get("activity"))
    dte = _to_float(activity_data.get(dte_key))
    if activity != "SELL":
        return _rule(
            rule_id, label, "timing", STATUS_NOT_APPLICABLE,
            "DTE \u2264 45", "No contract selected (activity is not SELL)",
            "deterministic", blocking=False, data_refs={"dte": dte},
        )
    if dte is None:
        return _rule(
            rule_id, label, "timing", STATUS_UNKNOWN,
            "DTE \u2264 45", "DTE value unavailable",
            "deterministic", blocking=False, data_refs={},
        )
    status = STATUS_PASS if dte <= 45 else STATUS_FAIL
    return _rule(
        rule_id, label, "timing", status,
        "DTE \u2264 45", f"DTE = {int(dte)}",
        "deterministic", blocking=(status == STATUS_FAIL), data_refs={"dte": dte},
    )


def _delta_range_rule(
    rule_id: str, label: str, activity_data: Dict, thresholds: Dict[str, Any],
    category_key: str, use_abs: bool = False,
) -> Dict[str, Any]:
    activity = _upper(activity_data.get("activity"))
    delta = _to_float(activity_data.get("delta"))
    lo, hi = thresholds["delta_lo"], thresholds["delta_hi"]
    cat_label = _category_label(category_key)
    expected = f"Delta {lo}\u2013{hi} ({cat_label})"
    if activity != "SELL":
        return _rule(
            rule_id, label, "greeks", STATUS_NOT_APPLICABLE,
            expected, "No contract selected (activity is not SELL)",
            "deterministic", blocking=False, data_refs={"category": category_key},
        )
    if delta is None:
        return _rule(
            rule_id, label, "greeks", STATUS_UNKNOWN,
            expected, "Delta value unavailable", "deterministic",
            blocking=False, data_refs={"category": category_key},
        )
    eff_delta = abs(delta) if use_abs else delta
    status = STATUS_PASS if lo <= eff_delta <= hi else STATUS_FAIL
    return _rule(
        rule_id, label, "greeks", status,
        expected, f"Delta {eff_delta}", "deterministic",
        blocking=False, data_refs={"delta": eff_delta, "category": category_key},
    )


def _premium_floor_rule(
    rule_id: str, label: str, activity_data: Dict, thresholds: Dict[str, Any],
    category_key: str, basis_label: str,
) -> Dict[str, Any]:
    activity = _upper(activity_data.get("activity"))
    premium_pct = _to_float(activity_data.get("premium_pct"))
    premium = _to_float(activity_data.get("premium"))
    min_pct = thresholds["premium_min_pct"]
    cat_label = _category_label(category_key)
    expected = f"Premium \u2265 {min_pct}% of {basis_label} ({cat_label})"
    if activity != "SELL":
        return _rule(
            rule_id, label, "premium", STATUS_NOT_APPLICABLE,
            expected, "No contract selected (activity is not SELL)",
            "deterministic", blocking=False, data_refs={"category": category_key},
        )
    if premium_pct is None:
        return _rule(
            rule_id, label, "premium", STATUS_UNKNOWN,
            expected, "Premium percentage unavailable", "deterministic",
            blocking=False, data_refs={"category": category_key},
        )
    status = STATUS_PASS if premium_pct >= min_pct else STATUS_FAIL
    return _rule(
        rule_id, label, "premium", status,
        expected, f"{premium_pct}% (${premium})" if premium is not None else f"{premium_pct}%",
        "deterministic", blocking=False,
        data_refs={"premium_pct": premium_pct, "premium": premium, "category": category_key},
    )


def _iv_rank_rule(rule_id: str, label: str, activity_data: Dict, thresholds: Dict[str, Any], category_key: str) -> Dict[str, Any]:
    iv = _to_float(activity_data.get("iv"))
    iv_rank = _to_float(activity_data.get("iv_rank"))
    iv_min = thresholds.get("iv_rank_min")
    if iv_rank is None:
        return _rule(
            rule_id, label, "volatility", STATUS_UNKNOWN,
            None, "IV Rank unavailable", "deterministic",
            blocking=False, data_refs={"category": category_key},
        )
    if iv_min is not None and iv_rank < iv_min:
        status = STATUS_WARNING
        observed = f"IV {iv}% (Rank {int(iv_rank)}) \u2014 below category min {int(iv_min)}" if iv is not None else f"IV Rank {int(iv_rank)} \u2014 below category min {int(iv_min)}"
    else:
        status = STATUS_INFORMATIONAL
        observed = f"IV {iv}% (Rank {int(iv_rank)})" if iv is not None else f"IV Rank {int(iv_rank)}"
    return _rule(
        rule_id, label, "volatility", status, None, observed, "deterministic",
        blocking=False,
        data_refs={"iv": iv, "iv_rank": iv_rank, "category_iv_min": iv_min},
    )


def _risk_rating_rule(rule_id: str, label: str, activity_data: Dict) -> Dict[str, Any]:
    risk_rating = activity_data.get("risk_rating")
    if risk_rating is None:
        return _rule(
            rule_id, label, "overall", STATUS_UNKNOWN, None,
            "Risk rating unavailable", "deterministic", blocking=False, data_refs={},
        )
    return _rule(
        rule_id, label, "overall", STATUS_INFORMATIONAL, None,
        f"{risk_rating}/10", "deterministic", blocking=False,
        data_refs={"risk_rating": risk_rating},
    )


def _technical_bias_rule(rule_id: str, label: str, activity_data: Dict) -> Dict[str, Any]:
    breakdown = activity_data.get("risk_rating_breakdown") or {}
    technical = breakdown.get("technical")
    if technical is None:
        return _rule(
            rule_id, label, "technical", STATUS_UNKNOWN, None,
            "Technical risk data unavailable", "deterministic", blocking=False, data_refs={},
        )
    label_txt = "low" if technical == 0 else ("moderate" if technical == 1 else "high")
    return _rule(
        rule_id, label, "technical", STATUS_INFORMATIONAL, None,
        f"Technical risk: {technical}/2 ({label_txt})", "deterministic",
        blocking=False, data_refs={"technical_risk": technical},
    )


# ---------------------------------------------------------------------------
# 3.1 — Covered Call rules
# ---------------------------------------------------------------------------

def build_covered_call_rules(activity_data: Dict, category: Optional[str]) -> List[Dict[str, Any]]:
    cat_key = _normalize_category(category)
    thresholds = CATEGORY_THRESHOLDS_CC[cat_key]
    risk_flags = _risk_flags(activity_data)

    rules: List[Dict[str, Any]] = [
        _sell_earnings_gate_rule("cc_earnings_gate", "Earnings Gate", activity_data),
        _dte_cap_rule("cc_dte_cap", "DTE \u2264 45", activity_data),
        _delta_range_rule("cc_delta_range", "Delta Range", activity_data, thresholds, cat_key),
        _premium_floor_rule("cc_premium_floor", "Premium Floor", activity_data, thresholds, cat_key, "stock price"),
        _iv_rank_rule("cc_iv_rank", "IV Rank", activity_data, thresholds, cat_key),
    ]

    # cc_ex_div_check — llm, rule_checks -> risk_flags fallback
    has_ex_div = "ex_dividend_risk" in risk_flags
    rules.append(_llm_rule_from_checks_or_fallback(
        "cc_ex_div_check", "Ex-Dividend Check", "calendar", activity_data,
        expected="No ex-div conflict with strike proximity",
        fallback_status=STATUS_WARNING if has_ex_div else STATUS_UNKNOWN,
        fallback_observed=(
            "ex_dividend_risk flag present" if has_ex_div
            else "No ex_dividend_risk flag present and no rule_checks provided"
        ),
        blocking_when=(),
    ))

    # cc_catalyst_check — llm, hard fail when present (blocking); absent flag
    # without rule_checks is not proof of "no catalyst" (Amendment A1.2) -> unknown
    has_catalyst = "catalyst_pending" in risk_flags
    rules.append(_llm_rule_from_checks_or_fallback(
        "cc_catalyst_check", "Catalyst Check", "calendar", activity_data,
        expected="No pending catalysts within DTE",
        fallback_status=STATUS_FAIL if has_catalyst else STATUS_UNKNOWN,
        fallback_observed=(
            "catalyst_pending flag present" if has_catalyst
            else "No catalyst_pending flag present and no rule_checks provided"
        ),
    ))

    # cc_breakout_check — llm, hard fail when present (blocking); absent flag
    # without rule_checks is not proof of "no breakout" (Amendment A1.2) -> unknown
    has_breakout = "breakout_momentum" in risk_flags
    rules.append(_llm_rule_from_checks_or_fallback(
        "cc_breakout_check", "Explosive Breakout", "technical", activity_data,
        expected="No explosive gap above resistance with >2x volume",
        fallback_status=STATUS_FAIL if has_breakout else STATUS_UNKNOWN,
        fallback_observed=(
            "breakout_momentum flag present" if has_breakout
            else "No breakout_momentum flag present and no rule_checks provided"
        ),
    ))

    rules.append(_technical_bias_rule("cc_technical_bias", "Technical Bias", activity_data))
    rules.append(_risk_rating_rule("cc_risk_rating", "Risk Rating", activity_data))
    return rules


# ---------------------------------------------------------------------------
# 3.2 — Cash-Secured Put rules
# ---------------------------------------------------------------------------

def build_cash_secured_put_rules(activity_data: Dict, category: Optional[str]) -> List[Dict[str, Any]]:
    cat_key = _normalize_category(category)
    thresholds = CATEGORY_THRESHOLDS_CSP[cat_key]
    risk_flags = _risk_flags(activity_data)

    rules: List[Dict[str, Any]] = [
        _sell_earnings_gate_rule("csp_earnings_gate", "Earnings Gate", activity_data),
        _dte_cap_rule("csp_dte_cap", "DTE \u2264 45", activity_data),
    ]

    # csp_investment_worthiness — llm only, no deterministic fallback signal exists
    rules.append(_llm_rule_from_checks_or_fallback(
        "csp_investment_worthiness", "Investment Worthiness", "fundamental", activity_data,
        expected="Would you buy this stock at strike?",
        fallback_status=STATUS_UNKNOWN,
        fallback_observed="No rule_checks verdict available",
    ))

    rules.append(_delta_range_rule("csp_delta_range", "Delta Range", activity_data, thresholds, cat_key, use_abs=True))
    rules.append(_premium_floor_rule("csp_premium_floor", "Premium Floor", activity_data, thresholds, cat_key, "strike"))

    # csp_support_level — llm, but strike vs support_level is a deterministic proxy
    check = _rule_check_for(activity_data, "csp_support_level")
    if check is not None:
        status = check.get("status", STATUS_UNKNOWN)
        detail = check.get("detail") or f"rule_checks: {status}"
        rules.append(_rule(
            "csp_support_level", "Support Level", "technical", status,
            "Strike at or below key support", detail, "llm", detail=detail,
        ))
    else:
        strike = _to_float(activity_data.get("strike"))
        support = _to_float(activity_data.get("support_level"))
        if strike is None or support is None:
            rules.append(_rule(
                "csp_support_level", "Support Level", "technical", STATUS_UNKNOWN,
                "Strike at or below key support", "Strike/support data unavailable", "llm",
            ))
        else:
            status = STATUS_PASS if strike <= support else STATUS_FAIL
            rules.append(_rule(
                "csp_support_level", "Support Level", "technical", status,
                "Strike at or below key support",
                f"Strike ${strike} vs support ${support}", "llm",
                data_refs={"strike": strike, "support_level": support},
            ))

    rules.append(_iv_rank_rule("csp_iv_rank", "IV Rank", activity_data, thresholds, cat_key))

    has_ex_div = "ex_dividend_risk" in risk_flags
    rules.append(_llm_rule_from_checks_or_fallback(
        "csp_ex_div_check", "Ex-Dividend Check", "calendar", activity_data,
        expected="No ex-div conflict (less critical for puts)",
        fallback_status=STATUS_WARNING if has_ex_div else STATUS_UNKNOWN,
        fallback_observed=(
            "ex_dividend_risk flag present" if has_ex_div
            else "No ex_dividend_risk flag present and no rule_checks provided"
        ),
        blocking_when=(),
    ))

    # csp_catalyst_check — llm, hard fail when present (blocking); absent flag
    # without rule_checks is not proof of "no catalyst" (Amendment A1.2) -> unknown
    has_catalyst = "catalyst_pending" in risk_flags
    rules.append(_llm_rule_from_checks_or_fallback(
        "csp_catalyst_check", "Catalyst Check", "calendar", activity_data,
        expected="No pending negative catalysts within DTE",
        fallback_status=STATUS_FAIL if has_catalyst else STATUS_UNKNOWN,
        fallback_observed=(
            "catalyst_pending flag present" if has_catalyst
            else "No catalyst_pending flag present and no rule_checks provided"
        ),
    ))

    # csp_freefall_check — llm, hard fail when present (blocking); absent flag
    # without rule_checks is not proof of "not in free-fall" (Amendment A1.2) -> unknown
    has_freefall = "breakdown_momentum" in risk_flags
    rules.append(_llm_rule_from_checks_or_fallback(
        "csp_freefall_check", "Free-Fall Check", "technical", activity_data,
        expected="Not in free-fall with accelerating downside momentum",
        fallback_status=STATUS_FAIL if has_freefall else STATUS_UNKNOWN,
        fallback_observed=(
            "breakdown_momentum flag present" if has_freefall
            else "No breakdown_momentum flag present and no rule_checks provided"
        ),
    ))

    rules.append(_risk_rating_rule("csp_risk_rating", "Risk Rating", activity_data))
    return rules


# ---------------------------------------------------------------------------
# 3.3 / 3.5 — Open Call / Put Monitor — Assessment Phase
# ---------------------------------------------------------------------------

def _moneyness_rule(rule_id: str, label: str, activity_data: Dict, invert: bool = False) -> Dict[str, Any]:
    moneyness = activity_data.get("moneyness")
    price = _to_float(activity_data.get("underlying_price"))
    strike = _to_float(activity_data.get("current_strike") or activity_data.get("strike"))
    expected = "Position is OTM" if not invert else "Position is OTM (price above strike)"
    if moneyness is None:
        return _rule(
            rule_id, label, "position", STATUS_UNKNOWN, expected,
            "Moneyness data unavailable", "deterministic", blocking=False, data_refs={},
        )
    moneyness_up = _upper(moneyness)
    if moneyness_up == "OTM":
        status = STATUS_PASS
    elif moneyness_up == "ATM":
        status = STATUS_WARNING
    else:  # ITM
        status = STATUS_FAIL
    observed = f"{moneyness_up}"
    if price is not None and strike is not None:
        observed += f" \u2014 price ${price}, strike ${strike}"
    return _rule(
        rule_id, label, "position", status, expected, observed, "deterministic",
        blocking=False, data_refs={"price": price, "strike": strike, "moneyness": moneyness_up},
    )


def _delta_risk_rule(rule_id: str, label: str, activity_data: Dict) -> Dict[str, Any]:
    delta = _to_float(activity_data.get("delta"))
    expected = "Delta < 0.30 (pass), 0.30-0.50 (warning), >0.50 (fail)"
    if delta is None:
        return _rule(
            rule_id, label, "greeks", STATUS_UNKNOWN, expected,
            "Delta value unavailable", "deterministic", blocking=False, data_refs={},
        )
    eff_delta = abs(delta)
    if eff_delta < 0.30:
        status = STATUS_PASS
    elif eff_delta <= 0.50:
        status = STATUS_WARNING
    else:
        status = STATUS_FAIL
    return _rule(
        rule_id, label, "greeks", status, expected, f"Delta {eff_delta}",
        "deterministic", blocking=False, data_refs={"delta": eff_delta},
    )


def _buyback_quote_available(activity_data: Dict) -> Optional[bool]:
    """Return explicit executable buyback quote state when present."""
    for key in ("buyback_ask", "executable_buyback_ask", "buyback_cost"):
        if key in activity_data:
            return executable_buyback_ask(activity_data.get(key)) is not None

    roll_economics = activity_data.get("roll_economics")
    if isinstance(roll_economics, dict):
        if "buyback_cost" in roll_economics:
            return (
                executable_buyback_ask(
                    roll_economics.get("buyback_cost")
                )
                is not None
            )
        roll_explicit = roll_economics.get("buyback_available")
        if isinstance(roll_explicit, bool):
            return roll_explicit

    explicit = activity_data.get("buyback_available")
    if isinstance(explicit, bool):
        return explicit

    if activity_data.get("incomplete_data") is True:
        return False
    if "incomplete_data" in _risk_flags(activity_data):
        return False
    return None


def _profit_target_rule(rule_id: str, label: str, activity_data: Dict) -> Dict[str, Any]:
    expected = "P&L \u2265 70% AND DTE \u2265 10"
    quote_available = _buyback_quote_available(activity_data)
    triggered = activity_data.get("close_for_profit_recommended")
    profit_pct = _to_float(activity_data.get("profit_level_pct"))
    dte = _to_float(activity_data.get("dte_remaining"))
    if quote_available is False:
        return _rule(
            rule_id, label, "optimization", STATUS_UNKNOWN, expected,
            "Executable buyback ask unavailable \u2014 profit/CLOSE quote rule cannot be evaluated",
            "deterministic", blocking=False,
            data_refs={
                "buyback_available": False,
                "pnl_pct": None,
                "dte_remaining": dte,
            },
        )
    if triggered is None:
        return _rule(
            rule_id, label, "optimization", STATUS_UNKNOWN, expected,
            "Profit target trigger data unavailable", "deterministic",
            blocking=False, data_refs={},
        )
    if triggered:
        observed = f"P&L {profit_pct}% \u2014 profit target reached" if profit_pct is not None else "Profit target reached"
        return _rule(
            rule_id, label, "optimization", STATUS_PASS, expected, observed,
            "deterministic", blocking=False,
            data_refs={"pnl_pct": profit_pct, "dte_remaining": dte},
        )
    observed = f"P&L {profit_pct}% \u2014 below threshold" if profit_pct is not None else "Profit target not met"
    return _rule(
        rule_id, label, "optimization", STATUS_NOT_APPLICABLE, expected, observed,
        "deterministic", blocking=False, data_refs={"pnl_pct": profit_pct, "dte_remaining": dte},
    )


def _market_bias_rule(rule_id: str, label: str, activity_data: Dict) -> Dict[str, Any]:
    bias = activity_data.get("market_bias") or {}
    direction = bias.get("direction")
    rsi_14 = bias.get("rsi_14")
    if direction is None:
        return _rule(
            rule_id, label, "technical", STATUS_UNKNOWN, None,
            "Market bias unavailable", "deterministic", blocking=False, data_refs={},
        )
    observed = f"{direction.title() if isinstance(direction, str) else direction}"
    if rsi_14 is not None:
        observed += f" \u2014 RSI {rsi_14}"
    return _rule(
        rule_id, label, "technical", STATUS_INFORMATIONAL, None, observed,
        "deterministic", blocking=False, data_refs={"direction": direction, "rsi_14": rsi_14},
    )


def _assignment_risk_rule(rule_id: str, label: str, activity_data: Dict) -> Dict[str, Any]:
    check = _rule_check_for(activity_data, rule_id)
    if check is not None:
        status = check.get("status", STATUS_UNKNOWN)
        detail = check.get("detail") or f"rule_checks: {status}"
        return _rule(
            rule_id, label, "position", status, None, detail, "hybrid", detail=detail,
        )
    assignment_risk = activity_data.get("assignment_risk")
    if assignment_risk is None:
        return _rule(
            rule_id, label, "position", STATUS_UNKNOWN, None,
            "Assignment risk unavailable", "hybrid", blocking=False, data_refs={},
        )
    return _rule(
        rule_id, label, "position", STATUS_INFORMATIONAL, None,
        str(assignment_risk).title(), "hybrid", blocking=False,
        data_refs={"assignment_risk": assignment_risk},
    )


def _fundamental_check_rule(rule_id: str, label: str, activity_data: Dict) -> Dict[str, Any]:
    risk_flags = _risk_flags(activity_data)
    has_deteriorated = (
        "fundamental_deterioration" in risk_flags or "analyst_downgrade" in risk_flags
    )
    return _llm_rule_from_checks_or_fallback(
        rule_id, label, "fundamental", activity_data,
        expected="Still comfortable owning stock if assigned",
        fallback_status=STATUS_FAIL if has_deteriorated else STATUS_PASS,
        fallback_observed=(
            "fundamental_deterioration/analyst_downgrade flag present" if has_deteriorated
            else "No fundamental deterioration flags present"
        ),
    )


def _ex_div_risk_rule(rule_id: str, label: str, activity_data: Dict, applicable: bool) -> Dict[str, Any]:
    expected = "No ex-div before expiration with ITM call"
    if not applicable:
        return _rule(
            rule_id, label, "calendar", STATUS_NOT_APPLICABLE, expected,
            "Ex-dividend risk is not applicable to short puts", "llm",
            blocking=False, data_refs={},
        )
    risk_flags = _risk_flags(activity_data)
    has_ex_div = "ex_dividend_risk" in risk_flags
    return _llm_rule_from_checks_or_fallback(
        rule_id, label, "calendar", activity_data,
        expected=expected,
        fallback_status=STATUS_FAIL if has_ex_div else STATUS_UNKNOWN,
        fallback_observed=(
            "ex_dividend_risk flag present" if has_ex_div
            else "No ex_dividend_risk flag present and no rule_checks provided"
        ),
    )


def build_open_call_monitor_assessment_rules(activity_data: Dict) -> List[Dict[str, Any]]:
    return [
        _monitor_earnings_gate_rule("ocm_a_earnings_gate", "Earnings Gate", activity_data),
        _profit_target_rule("ocm_a_profit_target", "Profit Target Gate", activity_data),
        _moneyness_rule("ocm_a_moneyness", "Moneyness", activity_data),
        _delta_risk_rule("ocm_a_delta_risk", "Delta Risk", activity_data),
        _near_atm_stability_rule("ocm_a_near_atm_stability", "Near-ATM Stability", activity_data),
        _ex_div_risk_rule("ocm_a_ex_div_risk", "Ex-Dividend Risk", activity_data, applicable=True),
        _fundamental_check_rule("ocm_a_fundamental_check", "Fundamental Re-Check", activity_data),
        _market_bias_rule("ocm_a_technical_bias", "Technical / Market Bias", activity_data),
        _assignment_risk_rule("ocm_a_assignment_risk", "Assignment Risk Level", activity_data),
    ]


def _near_atm_stability_rule(rule_id: str, label: str, activity_data: Dict) -> Dict[str, Any]:
    risk_flags = _risk_flags(activity_data)
    expected = "Stability zone check: price within 2% above strike + favorable technicals \u2192 pass (hold)"
    if "near_atm_stability" in risk_flags:
        return _rule(
            rule_id, label, "position", STATUS_PASS, expected,
            "near_atm_stability flag present \u2014 holding through minor ITM noise",
            "hybrid", blocking=False, data_refs={},
        )
    moneyness = _upper(activity_data.get("moneyness"))
    if moneyness in ("OTM", "ATM"):
        return _rule(
            rule_id, label, "position", STATUS_NOT_APPLICABLE, expected,
            "Position not in the near-ATM ITM stability zone", "hybrid",
            blocking=False, data_refs={"moneyness": moneyness},
        )
    if moneyness == "ITM":
        return _rule(
            rule_id, label, "position", STATUS_FAIL, expected,
            "ITM without near_atm_stability flag \u2014 buffer not applied", "hybrid",
            blocking=False, data_refs={"moneyness": moneyness},
        )
    return _rule(
        rule_id, label, "position", STATUS_UNKNOWN, expected,
        "Moneyness data unavailable", "hybrid", blocking=False, data_refs={},
    )


def build_open_put_monitor_assessment_rules(activity_data: Dict) -> List[Dict[str, Any]]:
    return [
        _monitor_earnings_gate_rule("opm_a_earnings_gate", "Earnings Gate", activity_data),
        _profit_target_rule("opm_a_profit_target", "Profit Target Gate", activity_data),
        _moneyness_rule("opm_a_moneyness", "Moneyness", activity_data, invert=True),
        _delta_risk_rule("opm_a_delta_risk", "Delta Risk", activity_data),
        _near_atm_stability_rule("opm_a_near_atm_stability", "Near-ATM Stability", activity_data),
        _ex_div_risk_rule("opm_a_ex_div_risk", "Ex-Dividend Risk", activity_data, applicable=False),
        _fundamental_check_rule("opm_a_fundamental_check", "Fundamental Re-Check", activity_data),
        _market_bias_rule("opm_a_technical_bias", "Technical / Market Bias", activity_data),
        _assignment_risk_rule("opm_a_assignment_risk", "Assignment Risk Level", activity_data),
    ]


# ---------------------------------------------------------------------------
# 3.4 / 3.6 — Open Call / Put Monitor — Roll Phase
# ---------------------------------------------------------------------------

def _parse_date(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value)[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None


def _roll_candidate_rule(rule_id: str, label: str, activity_data: Dict) -> Dict[str, Any]:
    expected = "Viable roll candidate exists in candidates table"
    activity = _upper(activity_data.get("activity"))
    new_strike = activity_data.get("new_strike")
    new_expiration = activity_data.get("new_expiration")
    roll_economics = activity_data.get("roll_economics") or {}
    roll_tier = roll_economics.get("roll_tier")
    if _buyback_quote_available(activity_data) is False:
        return _rule(
            rule_id, label, "execution", STATUS_NOT_APPLICABLE, expected,
            "Executable buyback ask unavailable \u2014 roll candidate economics are N/A",
            "deterministic", blocking=False,
            data_refs={"buyback_available": False, "activity": activity},
        )
    if activity.startswith("ROLL") and new_strike is not None and new_expiration is not None:
        return _rule(
            rule_id, label, "execution", STATUS_PASS, expected,
            f"{activity} \u2192 strike ${new_strike}, exp {new_expiration}", "deterministic",
            blocking=False, data_refs={"new_strike": new_strike, "new_expiration": new_expiration},
        )
    if activity == "CLOSE" and roll_tier == "no_viable_roll":
        return _rule(
            rule_id, label, "execution", STATUS_FAIL, expected,
            "No viable roll candidate \u2014 converted to CLOSE", "deterministic",
            blocking=True, data_refs={"roll_tier": roll_tier},
        )
    if activity == "WAIT":
        return _rule(
            rule_id, label, "execution", STATUS_NOT_APPLICABLE, expected,
            "No roll needed (profit-optimization no-roll path)", "deterministic",
            blocking=False, data_refs={},
        )
    return _rule(
        rule_id, label, "execution", STATUS_UNKNOWN, expected,
        "Unable to determine roll candidate outcome", "deterministic",
        blocking=False, data_refs={"activity": activity},
    )


def _roll_tier_rule(rule_id: str, label: str, activity_data: Dict) -> Dict[str, Any]:
    expected = "Tier 1 (net credit) or Tier 2 (ultra-defensive \u2264$1 debit)"
    roll_economics = activity_data.get("roll_economics") or {}
    roll_tier = roll_economics.get("roll_tier")
    if _buyback_quote_available(activity_data) is False:
        return _rule(
            rule_id, label, "execution", STATUS_UNKNOWN, expected,
            "Executable buyback ask unavailable \u2014 roll tier cannot be calculated",
            "deterministic", blocking=False,
            data_refs={
                "buyback_cost": None,
                "new_premium": None,
                "net_credit": None,
                "roll_tier": None,
                "buyback_available": False,
            },
        )
    if roll_tier == "credit":
        status = STATUS_PASS
        observed = f"Tier 1 \u2014 net credit {roll_economics.get('net_credit')}"
    elif roll_tier == "ultra_defensive":
        status = STATUS_WARNING
        observed = f"Tier 2 \u2014 ultra-defensive debit {roll_economics.get('net_credit')}"
    elif roll_tier in (None, "no_viable_roll"):
        status = STATUS_NOT_APPLICABLE
        observed = "No roll attempted" if roll_tier is None else "No viable roll"
    else:
        status = STATUS_UNKNOWN
        observed = f"Unrecognized roll_tier '{roll_tier}'"
    return _rule(
        rule_id, label, "execution", status, expected, observed, "deterministic",
        blocking=False, data_refs={
            "buyback_cost": roll_economics.get("buyback_cost"),
            "new_premium": roll_economics.get("new_premium"),
            "net_credit": roll_economics.get("net_credit"),
            "roll_tier": roll_tier,
        },
    )


def _profit_opt_validation_rule(rule_id: str, label: str, activity_data: Dict) -> Dict[str, Any]:
    expected = "Profit optimization candidate-dependent conditions validated"
    gate = activity_data.get("profit_optimization_gate")
    risk_flags = _risk_flags(activity_data)
    if _buyback_quote_available(activity_data) is False:
        return _rule(
            rule_id, label, "optimization", STATUS_NOT_APPLICABLE, expected,
            "Executable buyback ask unavailable \u2014 profit optimization is not applicable",
            "deterministic", blocking=False,
            data_refs={
                "profit_optimization_gate": None,
                "buyback_available": False,
            },
        )
    if gate != "eligible":
        return _rule(
            rule_id, label, "optimization", STATUS_NOT_APPLICABLE, expected,
            "Not a profit-optimization roll" if gate is None else f"Gate result: {gate}",
            "deterministic", blocking=False, data_refs={"profit_optimization_gate": gate},
        )
    if "profit_optimization" in risk_flags:
        return _rule(
            rule_id, label, "optimization", STATUS_PASS, expected,
            "Profit optimization validated \u2014 flag retained", "deterministic",
            blocking=False, data_refs={"profit_optimization_gate": gate},
        )
    return _rule(
        rule_id, label, "optimization", STATUS_FAIL, expected,
        "Profit optimization downgraded \u2014 candidate-dependent conditions failed",
        "deterministic", blocking=False, data_refs={"profit_optimization_gate": gate},
    )


def _premium_cross_check_rule(rule_id: str, label: str, activity_data: Dict) -> Dict[str, Any]:
    expected = "Reported premium matches actual chain bid/ask"
    activity = _upper(activity_data.get("activity"))
    if _buyback_quote_available(activity_data) is False:
        status = (
            STATUS_UNKNOWN
            if activity.startswith("ROLL") or activity == "CLOSE"
            else STATUS_NOT_APPLICABLE
        )
        return _rule(
            rule_id, label, "execution", status, expected,
            "Executable buyback ask unavailable \u2014 CLOSE/ROLL quote verification is N/A",
            "deterministic", blocking=False,
            data_refs={"buyback_available": False, "activity": activity},
        )
    if not (activity.startswith("ROLL") or activity == "CLOSE"):
        return _rule(
            rule_id, label, "execution", STATUS_NOT_APPLICABLE, expected,
            "No premium cross-check applicable", "deterministic", blocking=False, data_refs={},
        )
    corrected = bool(activity_data.get("premium_corrected"))
    status = STATUS_WARNING if corrected else STATUS_PASS
    observed = (
        "Premium mismatch detected and auto-corrected against chain data" if corrected
        else "Reported premium verified against chain data"
    )
    return _rule(
        rule_id, label, "execution", status, expected, observed, "deterministic",
        blocking=False, data_refs={"premium_corrected": corrected},
    )


def _new_dte_target_rule(rule_id: str, label: str, activity_data: Dict) -> Dict[str, Any]:
    expected = "New DTE 21\u201345"
    new_expiration = activity_data.get("new_expiration")
    generated_at = activity_data.get("timestamp")
    if not new_expiration:
        return _rule(
            rule_id, label, "timing", STATUS_NOT_APPLICABLE, expected,
            "No new expiration selected", "deterministic", blocking=False, data_refs={},
        )
    exp_dt = _parse_date(new_expiration)
    ref_dt = _parse_date(generated_at) or datetime.now(timezone.utc).replace(tzinfo=None)
    if exp_dt is None:
        return _rule(
            rule_id, label, "timing", STATUS_UNKNOWN, expected,
            "Unable to parse new_expiration", "deterministic", blocking=False, data_refs={},
        )
    new_dte = (exp_dt - ref_dt).days
    status = STATUS_PASS if 21 <= new_dte <= 45 else STATUS_WARNING
    return _rule(
        rule_id, label, "timing", status, expected, f"DTE = {new_dte}",
        "deterministic", blocking=False, data_refs={"new_dte": new_dte},
    )


def _earnings_clear_rule(rule_id: str, label: str, activity_data: Dict) -> Dict[str, Any]:
    expected = "New expiration not in 0\u20137 day post-earnings zone"
    new_expiration = activity_data.get("new_expiration")
    earnings = _earnings_analysis(activity_data)
    next_earnings_date = earnings.get("next_earnings_date")
    if not new_expiration or not next_earnings_date or next_earnings_date == "unknown":
        return _rule(
            rule_id, label, "calendar", STATUS_UNKNOWN, expected,
            "Insufficient data to verify new expiration vs earnings date",
            "deterministic", blocking=False, data_refs={},
        )
    exp_dt = _parse_date(new_expiration)
    earn_dt = _parse_date(next_earnings_date)
    if exp_dt is None or earn_dt is None:
        return _rule(
            rule_id, label, "calendar", STATUS_UNKNOWN, expected,
            "Unable to parse dates", "deterministic", blocking=False, data_refs={},
        )
    gap = (exp_dt - earn_dt).days
    if 0 <= gap <= 7:
        status, blocking = STATUS_FAIL, True
    else:
        status, blocking = STATUS_PASS, False
    return _rule(
        rule_id, label, "calendar", status, expected,
        f"New exp {new_expiration}, earnings {next_earnings_date} \u2014 gap {gap} days",
        "deterministic", blocking=blocking,
        data_refs={"new_expiration": new_expiration, "earnings_date": next_earnings_date},
    )


def build_open_call_monitor_roll_rules(activity_data: Dict) -> List[Dict[str, Any]]:
    return [
        _roll_candidate_rule("ocm_r_roll_candidate", "Roll Candidate Found", activity_data),
        _roll_tier_rule("ocm_r_roll_tier", "Roll Economics Tier", activity_data),
        _profit_opt_validation_rule("ocm_r_profit_opt_validation", "Profit Optimization Validation", activity_data),
        _premium_cross_check_rule("ocm_r_premium_cross_check", "Premium Cross-Verification", activity_data),
        _new_dte_target_rule("ocm_r_dte_target", "New DTE in Range", activity_data),
        _earnings_clear_rule("ocm_r_earnings_clear", "New Expiration Clears Earnings", activity_data),
    ]


def build_open_put_monitor_roll_rules(activity_data: Dict) -> List[Dict[str, Any]]:
    return [
        _roll_candidate_rule("opm_r_roll_candidate", "Roll Candidate Found", activity_data),
        _roll_tier_rule("opm_r_roll_tier", "Roll Economics Tier", activity_data),
        _profit_opt_validation_rule("opm_r_profit_opt_validation", "Profit Optimization Validation", activity_data),
        _premium_cross_check_rule("opm_r_premium_cross_check", "Premium Cross-Verification", activity_data),
        _new_dte_target_rule("opm_r_dte_target", "New DTE in Range", activity_data),
        _earnings_clear_rule("opm_r_earnings_clear", "New Expiration Clears Earnings", activity_data),
    ]


# ---------------------------------------------------------------------------
# 3.7 — Buy Tracker normalization and rule reporting
# ---------------------------------------------------------------------------

BUY_TRACKER_DIMENSIONS = (
    "value_entry", "trend", "momentum", "income", "calendar",
)

BUY_TRACKER_EVIDENCE_FIELDS = (
    "current_price", "high_52w", "sma50", "sma200", "rsi_14",
    "macd_confirmation", "stochastic_confirmation",
    "annual_dividend_rate", "latest_dividend", "dividend_growth_years",
    "dividend_cut_or_suspended",
    "payout_ratio_pct", "analyst_target_price", "days_to_earnings",
    "ma_summary", "oscillator_summary",
)

BUY_TRACKER_HARD_WAIT_FLAGS = {
    "earnings": "earnings_within_2_days",
    "rsi": "rsi_over_80",
    "extended": "price_extended_above_mas",
    "dividend_cut": "dividend_cut_or_suspended",
    "triple_bear": "triple_bearish_breakdown",
}

_BUY_TRACKER_SUMMARIES = {
    "STRONG_BUY", "BUY", "NEUTRAL", "SELL", "STRONG_SELL",
}

_BUY_TRACKER_ENTRY_ZONE_RE = re.compile(
    r"^\s*\$?\s*([0-9]+(?:\.[0-9]+)?)\s*-\s*"
    r"\$?\s*([0-9]+(?:\.[0-9]+)?)\s*$"
)

_BUY_TRACKER_HARD_WAIT_META = {
    "earnings": {
        "rule_id": "bt_wait_earnings",
        "label": "WAIT: Earnings \u22642 Days",
        "expected": "Earnings are more than 2 days away",
        "reason": "earnings are within 2 days",
        "waiting_for": "earnings to be more than 2 days away",
    },
    "rsi": {
        "rule_id": "bt_wait_rsi_80",
        "label": "WAIT: RSI >80",
        "expected": "RSI is 80 or below",
        "reason": "RSI is above 80",
        "waiting_for": "RSI to return to 80 or below",
    },
    "extended": {
        "rule_id": "bt_wait_extended",
        "label": "WAIT: Price Extended",
        "expected": "Price is not both >10% above SMA50 and >15% above SMA200",
        "reason": "price is more than 10% above SMA50 and 15% above SMA200",
        "waiting_for": "price to move back within 10% of SMA50 or 15% of SMA200",
    },
    "dividend_cut": {
        "rule_id": "bt_wait_div_cut",
        "label": "WAIT: Dividend Cut",
        "expected": "No dividend cut or suspension",
        "reason": "a dividend cut or suspension is present",
        "waiting_for": "confirmation that the dividend is current and not cut or suspended",
    },
    "triple_bear": {
        "rule_id": "bt_wait_triple_bear",
        "label": "WAIT: Triple Bearish",
        "expected": "The oscillator, MA, and SMA200 breakdown conditions are not all bearish",
        "reason": "oscillators and moving averages are Strong Sell while price is >10% below SMA200",
        "waiting_for": "the triple-bearish oscillator, MA, and SMA200 breakdown to clear",
    },
}


def _parse_json_payload(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _finite_float(value: Any) -> Optional[float]:
    if (
        isinstance(value, bool)
        or value is None
        or not isinstance(value, (int, float))
    ):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _field_value(container: Any, key: str) -> Any:
    if not isinstance(container, dict):
        return None
    value = container.get(key)
    if isinstance(value, dict):
        return value.get("value")
    return value


def _canonical_summary(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        value = value.get("label")
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper().replace("-", "_").replace(" ", "_")
    return normalized if normalized in _BUY_TRACKER_SUMMARIES else None


def _recommendation_label(section: Any) -> Optional[str]:
    if not isinstance(section, dict):
        return None
    return _canonical_summary(section.get("recommendation"))


def _canonical_indicator_confirmation(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    return normalized if normalized in {"BUY", "NEUTRAL", "SELL"} else None


def _indicator_confirmation(indicators: Dict[str, Any], key: str) -> Optional[str]:
    indicator = indicators.get(key)
    if not isinstance(indicator, dict):
        return None
    return _canonical_indicator_confirmation(indicator.get("signal"))


def _days_until_earnings(value: Any, now: Optional[datetime | date]) -> Optional[int]:
    if not isinstance(value, str):
        return None
    earnings_date = _parse_date(value)
    if earnings_date is None:
        return None
    if isinstance(now, datetime):
        current_date = now.date()
    elif isinstance(now, date):
        current_date = now
    else:
        current_date = datetime.now(timezone.utc).date()
    return (earnings_date.date() - current_date).days


def _canonical_buy_tracker_evidence(evidence: Any) -> Dict[str, Any]:
    source = evidence if isinstance(evidence, dict) else {}
    normalized: Dict[str, Any] = {
        field: None for field in BUY_TRACKER_EVIDENCE_FIELDS
    }
    for field in (
        "current_price", "high_52w", "sma50", "sma200", "rsi_14",
        "annual_dividend_rate", "latest_dividend", "dividend_growth_years",
        "payout_ratio_pct", "analyst_target_price", "days_to_earnings",
    ):
        normalized[field] = _finite_float(source.get(field))
    normalized["macd_confirmation"] = _canonical_indicator_confirmation(
        source.get("macd_confirmation")
    )
    normalized["stochastic_confirmation"] = _canonical_indicator_confirmation(
        source.get("stochastic_confirmation")
    )
    dividend_cut = source.get("dividend_cut_or_suspended")
    normalized["dividend_cut_or_suspended"] = (
        dividend_cut if isinstance(dividend_cut, bool) else None
    )
    normalized["ma_summary"] = _canonical_summary(source.get("ma_summary"))
    normalized["oscillator_summary"] = _canonical_summary(
        source.get("oscillator_summary")
    )
    return normalized


def build_buy_tracker_evidence(
    fetch_data: Any,
    *,
    now: Optional[datetime | date] = None,
) -> Dict[str, Any]:
    """Adapt fetch_all output into the canonical, ephemeral Buy Tracker evidence."""
    source = fetch_data if isinstance(fetch_data, dict) else {}
    seeded = source.get("buy_tracker")
    evidence = _canonical_buy_tracker_evidence(
        seeded if isinstance(seeded, dict) else source
    )

    overview = _parse_json_payload(source.get("overview"))
    technicals = _parse_json_payload(source.get("technicals"))
    forecast = _parse_json_payload(source.get("forecast"))
    dividends_page = _parse_json_payload(source.get("dividends"))

    fundamentals = overview.get("fundamentals")
    if not isinstance(fundamentals, dict):
        fundamentals = {}
    oscillators = technicals.get("oscillators")
    if not isinstance(oscillators, dict):
        oscillators = {}
    oscillator_indicators = oscillators.get("indicators")
    if not isinstance(oscillator_indicators, dict):
        oscillator_indicators = {}
    moving_averages = technicals.get("moving_averages")
    if not isinstance(moving_averages, dict):
        moving_averages = {}
    ma_indicators = moving_averages.get("indicators")
    if not isinstance(ma_indicators, dict):
        ma_indicators = {}

    def set_number(field: str, *candidates: Any) -> None:
        if evidence[field] is not None:
            return
        for candidate in candidates:
            number = _finite_float(candidate)
            if number is not None:
                evidence[field] = number
                return

    set_number(
        "current_price",
        _field_value(fundamentals, "current_price"),
        technicals.get("price"),
    )
    set_number("high_52w", _field_value(fundamentals, "52w_high"))
    set_number("sma50", _field_value(ma_indicators, "SMA50"))
    set_number("sma200", _field_value(ma_indicators, "SMA200"))
    set_number("rsi_14", _field_value(oscillator_indicators, "RSI"))

    # The provider deliberately omits MACD.signal and Stoch.D values from its
    # public output. It does expose deterministic per-indicator signals:
    # MACD "Buy" means the line is above its internally computed signal line;
    # Stoch.K "Buy" means a bullish oversold K/D crossover. These are the
    # production-available objective confirmations used by the exceptional gate.
    if evidence["macd_confirmation"] is None:
        evidence["macd_confirmation"] = _indicator_confirmation(
            oscillator_indicators, "MACD.macd"
        )
    if evidence["stochastic_confirmation"] is None:
        evidence["stochastic_confirmation"] = _indicator_confirmation(
            oscillator_indicators, "Stoch.K"
        )

    if evidence["ma_summary"] is None:
        evidence["ma_summary"] = _recommendation_label(moving_averages)
    if evidence["oscillator_summary"] is None:
        evidence["oscillator_summary"] = _recommendation_label(oscillators)

    price_target = forecast.get("price_target")
    if not isinstance(price_target, dict):
        price_target = {}
    set_number(
        "analyst_target_price",
        _field_value(price_target, "price_target_average"),
    )

    dividend_fields = dividends_page.get("dividends")
    if not isinstance(dividend_fields, dict):
        dividend_fields = {}
    set_number(
        "payout_ratio_pct",
        _field_value(dividend_fields, "dividend_payout_ratio_ttm"),
    )

    # The provider exposes objective dividend-current inputs rather than a
    # current-state boolean. An explicit adapted cut/suspension boolean, when
    # supplied, remains authoritative and must survive this adaptation.
    set_number(
        "annual_dividend_rate",
        _field_value(dividend_fields, "dps_common_stock_prim_issue_fy"),
    )
    set_number(
        "latest_dividend",
        _field_value(dividend_fields, "dps_common_stock_prim_issue_fq"),
    )
    set_number(
        "dividend_growth_years",
        _field_value(dividend_fields, "continuous_dividend_growth"),
    )
    if evidence["dividend_cut_or_suspended"] is None:
        for candidate in (
            source.get("dividend_cut_or_suspended"),
            _field_value(dividend_fields, "dividend_cut_or_suspended"),
        ):
            if isinstance(candidate, bool):
                evidence["dividend_cut_or_suspended"] = candidate
                break

    if evidence["days_to_earnings"] is None:
        legacy_days = _finite_float(source.get("next_earnings_date_days_away"))
        if legacy_days is not None:
            evidence["days_to_earnings"] = legacy_days
        else:
            earnings_field = fundamentals.get("earnings_release_next_date_fq")
            formatted = (
                earnings_field.get("formatted")
                if isinstance(earnings_field, dict)
                else None
            )
            evidence["days_to_earnings"] = _days_until_earnings(formatted, now)

    return evidence


def _percent_from_baseline(value: Any, baseline: Any) -> Optional[float]:
    value_number = _finite_float(value)
    baseline_number = _finite_float(baseline)
    if value_number is None or value_number <= 0:
        return None
    if baseline_number is None or baseline_number <= 0:
        return None
    return ((value_number - baseline_number) / baseline_number) * 100


def _buy_tracker_hard_wait_checks(
    activity_data: Any,
    evidence: Any,
) -> Dict[str, Dict[str, Any]]:
    canonical = _canonical_buy_tracker_evidence(evidence)
    activity = activity_data if isinstance(activity_data, dict) else {}
    risk_flags = set(_risk_flags(activity))

    price_vs_sma50 = _percent_from_baseline(
        canonical["current_price"], canonical["sma50"]
    )
    price_vs_sma200 = _percent_from_baseline(
        canonical["current_price"], canonical["sma200"]
    )
    explicit_dividend_cut = canonical["dividend_cut_or_suspended"]

    raw_checks = {
        "earnings": (
            canonical["days_to_earnings"] is not None,
            canonical["days_to_earnings"] is not None
            and canonical["days_to_earnings"] <= 2,
            {"days_to_earnings": canonical["days_to_earnings"]},
        ),
        "rsi": (
            canonical["rsi_14"] is not None,
            canonical["rsi_14"] is not None and canonical["rsi_14"] > 80,
            {"rsi_14": canonical["rsi_14"]},
        ),
        "extended": (
            price_vs_sma50 is not None and price_vs_sma200 is not None,
            price_vs_sma50 is not None
            and price_vs_sma200 is not None
            and price_vs_sma50 > 10
            and price_vs_sma200 > 15,
            {
                "current_price": canonical["current_price"],
                "sma50": canonical["sma50"],
                "sma200": canonical["sma200"],
                "price_vs_sma50_pct": price_vs_sma50,
                "price_vs_sma200_pct": price_vs_sma200,
            },
        ),
        "dividend_cut": (
            explicit_dividend_cut is not None,
            explicit_dividend_cut is True,
            {
                "dividend_cut_or_suspended": explicit_dividend_cut,
            },
        ),
        "triple_bear": (
            canonical["oscillator_summary"] is not None
            and canonical["ma_summary"] is not None
            and price_vs_sma200 is not None,
            canonical["oscillator_summary"] == "STRONG_SELL"
            and canonical["ma_summary"] == "STRONG_SELL"
            and price_vs_sma200 is not None
            and price_vs_sma200 < -10,
            {
                "oscillator_summary": canonical["oscillator_summary"],
                "ma_summary": canonical["ma_summary"],
                "current_price": canonical["current_price"],
                "sma200": canonical["sma200"],
                "price_vs_sma200_pct": price_vs_sma200,
            },
        ),
    }

    checks: Dict[str, Dict[str, Any]] = {}
    for key, (available, raw_triggered, data_refs) in raw_checks.items():
        canonical_flag = BUY_TRACKER_HARD_WAIT_FLAGS[key]
        fallback_triggered = not available and canonical_flag in risk_flags
        checks[key] = {
            "available": available,
            "triggered": bool(raw_triggered or fallback_triggered),
            "source": (
                "deterministic" if available
                else "llm" if fallback_triggered
                else "unavailable"
            ),
            "canonical_flag": canonical_flag,
            "data_refs": data_refs if available else {},
        }
    return checks


def _buy_tracker_exceptional_gate(
    evidence: Any,
) -> tuple[bool, Dict[str, bool]]:
    canonical = _canonical_buy_tracker_evidence(evidence)
    pullback_pct = None
    if (
        canonical["current_price"] is not None
        and canonical["current_price"] > 0
        and canonical["high_52w"] is not None
        and canonical["high_52w"] > 0
    ):
        pullback_pct = (
            (canonical["high_52w"] - canonical["current_price"])
            / canonical["high_52w"]
        ) * 100
    price_vs_sma50 = _percent_from_baseline(
        canonical["current_price"], canonical["sma50"]
    )
    target_upside_pct = _percent_from_baseline(
        canonical["analyst_target_price"], canonical["current_price"]
    )

    checks = {
        "pullback_8_to_20_pct": (
            pullback_pct is not None and 8 <= pullback_pct <= 20
        ),
        "price_within_sma50_band": (
            price_vs_sma50 is not None and -5 <= price_vs_sma50 <= 2
        ),
        "price_at_or_above_sma200": (
            canonical["current_price"] is not None
            and canonical["current_price"] > 0
            and canonical["sma200"] is not None
            and canonical["sma200"] > 0
            and canonical["current_price"] >= canonical["sma200"]
        ),
        "sma50_at_or_above_sma200": (
            canonical["sma50"] is not None
            and canonical["sma50"] > 0
            and canonical["sma200"] is not None
            and canonical["sma200"] > 0
            and canonical["sma50"] >= canonical["sma200"]
        ),
        "rsi_25_to_45": (
            canonical["rsi_14"] is not None
            and 25 <= canonical["rsi_14"] <= 45
        ),
        "macd_confirmed": canonical["macd_confirmation"] == "BUY",
        "stochastic_confirmed": (
            canonical["stochastic_confirmation"] == "BUY"
        ),
        "dividend_current": (
            canonical["annual_dividend_rate"] is not None
            and canonical["annual_dividend_rate"] > 0
            and canonical["latest_dividend"] is not None
            and canonical["latest_dividend"] > 0
            and canonical["dividend_growth_years"] is not None
            and canonical["dividend_growth_years"] > 0
        ),
        "no_dividend_cut": (
            canonical["dividend_cut_or_suspended"] is not True
        ),
        "payout_ratio_at_or_below_75": (
            canonical["payout_ratio_pct"] is not None
            and canonical["payout_ratio_pct"] <= 75
        ),
        "analyst_upside_at_least_5": (
            target_upside_pct is not None and target_upside_pct >= 5
        ),
        "earnings_more_than_7_days": (
            canonical["days_to_earnings"] is not None
            and canonical["days_to_earnings"] > 7
        ),
    }
    return all(checks.values()), checks


def _validate_buy_tracker_breakdown(
    activity_data: Any,
) -> tuple[Dict[str, int], List[str]]:
    activity = activity_data if isinstance(activity_data, dict) else {}
    raw_breakdown = activity.get("score_breakdown")
    invalid_flags: List[str] = []
    if not isinstance(raw_breakdown, dict):
        raw_breakdown = {}
        invalid_flags.append("score_breakdown_invalid")

    breakdown: Dict[str, int] = {}
    for key in BUY_TRACKER_DIMENSIONS:
        value = raw_breakdown.get(key)
        valid = (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(float(value))
            and float(value) in (0.0, 1.0)
        )
        breakdown[key] = int(value) if valid else 0
        if not valid:
            invalid_flags.append(f"score_breakdown_{key}_invalid")
    return breakdown, invalid_flags


def _buy_tracker_reason_prefix(score: int, breakdown: Dict[str, int]) -> str:
    values = ", ".join(f"{key}:{breakdown[key]}" for key in BUY_TRACKER_DIMENSIONS)
    return f"Score {score}/5 ({values})."


def _existing_reason_tail(reason: Any) -> str:
    text = str(reason or "").strip()
    if not text:
        return ""
    if text.lower().startswith("score "):
        end = text.find(").")
        if end != -1:
            return text[end + 2:].strip()
        end = text.find(".")
        if end != -1:
            return text[end + 1:].strip()
    return text


def _deterministic_buy_tracker_explanation(
    activity: str,
    score: int,
    hard_wait_keys: List[str],
    exceptional_passed: bool,
) -> str:
    if hard_wait_keys:
        reasons = [
            _BUY_TRACKER_HARD_WAIT_META[key]["reason"] for key in hard_wait_keys
        ]
        return "Hard WAIT override: " + "; ".join(reasons) + "."
    if activity == "STRONG_BUY":
        return (
            "The validated 5/5 score passed every required exceptional "
            "price, trend, momentum, dividend, analyst, and calendar gate."
        )
    if activity == "BUY" and score == 5 and not exceptional_passed:
        return (
            "The validated score supports accumulation, but the exceptional "
            "STRONG_BUY evidence gate is incomplete or not fully confirmed; "
            "use a normal DCA-sized entry."
        )
    if activity == "BUY":
        return "The validated score supports a normal DCA-sized accumulation entry."
    return (
        "The validated score is below the BUY threshold; wait for more "
        "scoring dimensions to confirm."
    )


def _positive_buy_tracker_price(value: Any) -> Optional[float]:
    price = _finite_float(value)
    return price if price is not None and price > 0 else None


def _valid_buy_tracker_entry_zone(value: Any, price: Optional[float]) -> bool:
    if price is None:
        return False
    if not isinstance(value, str):
        return False
    match = _BUY_TRACKER_ENTRY_ZONE_RE.fullmatch(value)
    if match is None:
        return False
    low, high = (float(match.group(1)), float(match.group(2)))
    if not (math.isfinite(low) and math.isfinite(high) and 0 < low < high):
        return False
    return low <= price <= high


def _generated_buy_tracker_entry_zone(price: float) -> str:
    return f"${price * 0.98:.2f}-${price * 1.02:.2f}"


def normalize_buy_tracker_activity(
    activity_data: Any,
    evidence: Any,
) -> Dict[str, Any]:
    """Return a normalized Buy Tracker activity without mutating either input."""
    source = activity_data if isinstance(activity_data, dict) else {}
    normalized = copy.deepcopy(source)
    canonical_evidence = _canonical_buy_tracker_evidence(evidence)
    breakdown, validation_flags = _validate_buy_tracker_breakdown(source)
    score = sum(breakdown.values())
    score_text = f"{score}/5"

    exceptional_passed, exceptional_checks = _buy_tracker_exceptional_gate(
        canonical_evidence
    )
    base_activity = "WAIT" if score <= 2 else "BUY"
    final_activity = (
        "STRONG_BUY" if score == 5 and exceptional_passed else base_activity
    )

    hard_wait_checks = _buy_tracker_hard_wait_checks(source, canonical_evidence)
    hard_wait_keys = [
        key for key in _BUY_TRACKER_HARD_WAIT_META
        if hard_wait_checks[key]["triggered"]
    ]
    if hard_wait_keys:
        final_activity = "WAIT"

    original_activity = _upper(source.get("activity"))
    original_score = source.get("score")
    activity_changed = original_activity != final_activity
    score_changed = original_score != score_text
    semantic_changed = activity_changed or score_changed or bool(validation_flags)

    normalized["score_breakdown"] = breakdown
    normalized["score"] = score_text
    normalized["activity"] = final_activity
    canonical_price = _positive_buy_tracker_price(
        canonical_evidence["current_price"]
    )
    if canonical_price is not None:
        normalized["underlying_price"] = canonical_price

    if final_activity in {"BUY", "STRONG_BUY"}:
        entry_zone = source.get("entry_zone")
        if not _valid_buy_tracker_entry_zone(entry_zone, canonical_price):
            entry_zone = (
                _generated_buy_tracker_entry_zone(canonical_price)
                if canonical_price is not None
                else None
            )
        if entry_zone is None:
            normalized.pop("entry_zone", None)
        else:
            normalized["entry_zone"] = entry_zone
    else:
        normalized.pop("entry_zone", None)

    if final_activity == "STRONG_BUY":
        normalized["confidence"] = "high"
    elif final_activity == "BUY" or hard_wait_keys or score == 2:
        normalized["confidence"] = "medium"
    else:
        normalized["confidence"] = "low"

    if final_activity in {"BUY", "STRONG_BUY"}:
        normalized["waiting_for"] = ""
    elif hard_wait_keys:
        conditions = [
            _BUY_TRACKER_HARD_WAIT_META[key]["waiting_for"]
            for key in hard_wait_keys
        ]
        normalized["waiting_for"] = "Wait for " + "; ".join(conditions) + "."
    else:
        failed = [key for key in BUY_TRACKER_DIMENSIONS if breakdown[key] == 0]
        normalized["waiting_for"] = (
            "Wait for enough scoring confirmation to reach at least 3/5"
            + (f"; improve: {', '.join(failed)}." if failed else ".")
        )

    prefix = _buy_tracker_reason_prefix(score, breakdown)
    if semantic_changed:
        tail = _deterministic_buy_tracker_explanation(
            final_activity, score, hard_wait_keys, exceptional_passed,
        )
    else:
        tail = _existing_reason_tail(source.get("reason"))
        if not tail:
            tail = _deterministic_buy_tracker_explanation(
                final_activity, score, hard_wait_keys, exceptional_passed,
            )
    normalized["reason"] = f"{prefix} {tail}".strip()

    original_flags = _risk_flags(source)
    all_hard_flags = set(BUY_TRACKER_HARD_WAIT_FLAGS.values())
    triggered_flags = [
        hard_wait_checks[key]["canonical_flag"] for key in hard_wait_keys
    ]
    if semantic_changed:
        risk_flags: List[str] = []
        if final_activity == "WAIT" and not hard_wait_keys:
            risk_flags.append("score_below_buy_threshold")
        elif final_activity == "BUY" and score == 5 and not exceptional_passed:
            risk_flags.append("exceptional_gate_not_met")
    else:
        risk_flags = [
            flag for flag in original_flags if flag not in all_hard_flags
        ]
    for flag in triggered_flags + validation_flags:
        if flag not in risk_flags:
            risk_flags.append(flag)
    normalized["risk_flags"] = risk_flags

    original_triggers = source.get("technical_triggers")
    if final_activity == "WAIT":
        normalized["technical_triggers"] = []
    elif final_activity == "STRONG_BUY":
        normalized["technical_triggers"] = [
            key for key, passed in exceptional_checks.items() if passed
        ]
    elif semantic_changed or not isinstance(original_triggers, list):
        normalized["technical_triggers"] = []
    else:
        normalized["technical_triggers"] = [
            str(trigger) for trigger in original_triggers
        ]

    return normalized


def _bt_scoring_rule(
    rule_id: str,
    label: str,
    dimension_key: str,
    activity_data: Dict,
) -> Dict[str, Any]:
    breakdown = activity_data.get("score_breakdown")
    value = breakdown.get(dimension_key) if isinstance(breakdown, dict) else None
    expected = f"Score 1 if {label.lower()} supports accumulation"
    if value not in (0, 1) or isinstance(value, bool):
        return _rule(
            rule_id, label, "scoring", STATUS_UNKNOWN, expected,
            "Validated score breakdown unavailable", "deterministic",
            blocking=False, data_refs={},
        )
    status = STATUS_PASS if value == 1 else STATUS_FAIL
    return _rule(
        rule_id, label, "scoring", status, expected, f"{value}/1",
        "deterministic", blocking=False, data_refs={dimension_key: value},
    )


def build_buy_tracker_rules(
    activity_data: Dict,
    enrichment_data: Optional[Dict],
) -> List[Dict[str, Any]]:
    evidence = build_buy_tracker_evidence(enrichment_data)
    hard_wait_checks = _buy_tracker_hard_wait_checks(activity_data, evidence)

    rules: List[Dict[str, Any]] = [
        _bt_scoring_rule("bt_value_entry", "Value & Entry", "value_entry", activity_data),
        _bt_scoring_rule("bt_trend", "Trend", "trend", activity_data),
        _bt_scoring_rule("bt_momentum", "Momentum", "momentum", activity_data),
        _bt_scoring_rule("bt_income", "Income Quality", "income", activity_data),
        _bt_scoring_rule("bt_calendar", "Calendar", "calendar", activity_data),
    ]

    for key, meta in _BUY_TRACKER_HARD_WAIT_META.items():
        check = hard_wait_checks[key]
        if check["triggered"]:
            status = STATUS_BLOCKED
            observed = (
                f"Triggered from {check['source']} evidence"
                if check["source"] == "deterministic"
                else f"Triggered by exact canonical flag "
                     f"{check['canonical_flag']!r}"
            )
        elif check["available"]:
            status = STATUS_NOT_APPLICABLE
            observed = "Raw evidence available; trigger not present"
        else:
            status = STATUS_UNKNOWN
            observed = "Required raw evidence unavailable; no canonical flag present"
        rules.append(_rule(
            meta["rule_id"],
            meta["label"],
            "trigger",
            status,
            meta["expected"],
            observed,
            check["source"],
            blocking=check["triggered"],
            data_refs=check["data_refs"],
        ))

    return rules


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def _summary_counts(rules: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {status: 0 for status in _ALL_STATUSES}
    for rule in rules:
        status = rule.get("status")
        if status in counts:
            counts[status] += 1
    return counts


def _first_blocker(rules: List[Dict[str, Any]]) -> Optional[str]:
    for rule in rules:
        if rule.get("blocking"):
            return rule.get("rule_id")
    return None


def _sort_for_display(rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(rules, key=lambda r: _STATUS_ORDER.get(r.get("status"), 99))


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

_MONITOR_AGENT_TYPES = {"open_call_monitor", "open_put_monitor"}


def build_rule_evaluation(
    agent_type: str,
    activity_data: Dict[str, Any],
    phase: Optional[str] = None,
    category: Optional[str] = None,
    enrichment_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the rule_evaluation object from parsed activity JSON.

    Purely deterministic post-processing — reads fields the LLM already
    produced. Never raises; missing data degrades individual rules to
    ``status: "unknown"``.

    For monitor agent types (`open_call_monitor` / `open_put_monitor`), a
    ``phase`` ("assessment" or "roll") MUST be supplied and the result uses
    the ``phases`` array format (A1.3). For all other agent types, ``phase``
    is ignored and a flat ``rules`` array is returned with ``phase: null``.
    """
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if agent_type in _MONITOR_AGENT_TYPES:
        if phase == "roll":
            rules = (
                build_open_call_monitor_roll_rules(activity_data)
                if agent_type == "open_call_monitor"
                else build_open_put_monitor_roll_rules(activity_data)
            )
            phase_name = "roll"
        else:
            rules = (
                build_open_call_monitor_assessment_rules(activity_data)
                if agent_type == "open_call_monitor"
                else build_open_put_monitor_assessment_rules(activity_data)
            )
            phase_name = "assessment"

        phases = [{"phase": phase_name, "rules": _sort_for_display(rules)}]
        return {
            "schema_version": SCHEMA_VERSION,
            "agent_type": agent_type,
            "phases": phases,
            "generated_at": generated_at,
            "first_blocker": _first_blocker(rules),
            "summary_counts": _summary_counts(rules),
        }

    if agent_type == "covered_call":
        rules = build_covered_call_rules(activity_data, category)
    elif agent_type == "cash_secured_put":
        rules = build_cash_secured_put_rules(activity_data, category)
    elif agent_type == "buy_tracker":
        rules = build_buy_tracker_rules(activity_data, enrichment_data)
    else:
        raise ValueError(
            f"Unrecognized agent_type {agent_type!r} passed to build_rule_evaluation. "
            "Expected one of: covered_call, cash_secured_put, buy_tracker, "
            "open_call_monitor, open_put_monitor."
        )

    rules = _sort_for_display(rules)
    return {
        "schema_version": SCHEMA_VERSION,
        "agent_type": agent_type,
        "phase": None,
        "rules": rules,
        "generated_at": generated_at,
        "first_blocker": _first_blocker(rules),
        "summary_counts": _summary_counts(rules),
    }


def merge_phase_evaluations(
    assessment_eval: Dict[str, Any],
    roll_eval: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge a single-phase assessment evaluation with a roll evaluation.

    Both inputs are expected to be the ``phases``-array shaped dicts returned
    by :func:`build_rule_evaluation` for monitor agent types. Combines the
    phases arrays (assessment first, roll second), and recomputes
    ``summary_counts`` / ``first_blocker`` across all phases combined
    (A1.3: assessment scanned before roll for `first_blocker`).
    """
    assessment_phases = assessment_eval.get("phases") or []
    roll_phases = roll_eval.get("phases") or []
    combined_phases = list(assessment_phases) + list(roll_phases)

    all_rules: List[Dict[str, Any]] = []
    first_blocker = None
    for phase_block in combined_phases:
        phase_rules = phase_block.get("rules") or []
        all_rules.extend(phase_rules)
        if first_blocker is None:
            first_blocker = _first_blocker(phase_rules)

    return {
        "schema_version": roll_eval.get("schema_version", SCHEMA_VERSION),
        "agent_type": roll_eval.get("agent_type") or assessment_eval.get("agent_type"),
        "phases": combined_phases,
        "generated_at": roll_eval.get("generated_at") or assessment_eval.get("generated_at"),
        "first_blocker": first_blocker,
        "summary_counts": _summary_counts(all_rules),
    }
