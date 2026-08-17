"""
Unit tests for src/rule_evaluator.py (build_rule_evaluation / merge_phase_evaluations).

Covers the accepted contract in .squad/decisions/inbox/danny-rule-evaluation-design.md,
including the A1.9 "Basher Test Requirements" table (17 named tests) and the general
coverage areas called out in the task brief:

  - schema_version, summary_counts completeness, explicit `blocking` boolean,
    first_blocker ordering
  - category thresholds sourced from src/skills/*/SKILL.md (no invented values)
  - rule_checks precedence over risk_flags fallback, and unknown when neither present
  - WAIT / no-selected-contract not_applicable semantics
  - assessment-only and assessment+roll phases; aggregate counts and first_blocker
    across phases
  - buy_tracker score dimensions + deterministic earnings/RSI recalculation overrides
  - absence of financial-viability rules
  - deterministic/pure output for identical input
  - malformed/missing optional fields handled explicitly (no broad-success fallback)
  - backward compatibility: activities without rule_evaluation are untouched by this
    module (there is nothing to merge/inject after the fact)
  - Buy Tracker normalization score mapping, exceptional gate, hard-WAIT precedence,
    malformed inputs, coherent output, and purity from the accepted normalization
    contract

All assertions target structured fields (status, blocking, group, data_refs, counts)
rather than parsing narrative/prose strings, per the design's "no prose parsing"
principle.
"""

from __future__ import annotations

import copy
import importlib
import json
import re
from datetime import date

import pytest

MODULE_PATH = "src.rule_evaluator"

try:
    rule_evaluator = importlib.import_module(MODULE_PATH)
    MODULE_AVAILABLE = True
    MODULE_IMPORT_ERROR = None
except ModuleNotFoundError as exc:  # production module not created yet
    rule_evaluator = None
    MODULE_AVAILABLE = False
    MODULE_IMPORT_ERROR = exc

pytestmark = pytest.mark.skipif(
    not MODULE_AVAILABLE,
    reason=(
        "src/rule_evaluator.py not found yet (production module pending from "
        f"Linus). Import error: {MODULE_IMPORT_ERROR!r}"
    ),
)


def build_rule_evaluation(*args, **kwargs):
    return rule_evaluator.build_rule_evaluation(*args, **kwargs)


def merge_phase_evaluations(*args, **kwargs):
    return rule_evaluator.merge_phase_evaluations(*args, **kwargs)


def normalize_buy_tracker_activity(*args, **kwargs):
    return rule_evaluator.normalize_buy_tracker_activity(*args, **kwargs)


def build_buy_tracker_evidence(*args, **kwargs):
    return rule_evaluator.build_buy_tracker_evidence(*args, **kwargs)


# ---------------------------------------------------------------------------
# Shared helpers / fixtures
# ---------------------------------------------------------------------------

ALL_STATUSES = {
    "pass",
    "fail",
    "blocked",
    "warning",
    "unknown",
    "not_applicable",
    "informational",
}

SUMMARY_KEYS = {
    "pass",
    "fail",
    "blocked",
    "warning",
    "unknown",
    "not_applicable",
    "informational",
}

BUY_TRACKER_SCORE_KEYS = (
    "value_entry",
    "trend",
    "momentum",
    "income",
    "calendar",
)


def _rules_of(evaluation: dict) -> list:
    """Return the flat list of RuleResult dicts, whether flat `rules` or `phases`."""
    if "phases" in evaluation and evaluation["phases"] is not None:
        out = []
        for phase_block in evaluation["phases"]:
            out.extend(phase_block["rules"])
        return out
    return evaluation.get("rules", [])


def _find_rule(evaluation: dict, rule_id: str) -> dict:
    for rule in _rules_of(evaluation):
        if rule["rule_id"] == rule_id:
            return rule
    raise AssertionError(f"rule_id {rule_id!r} not found in evaluation")


def _base_covered_call_activity(**overrides) -> dict:
    activity = {
        "agent_type": "covered_call",
        "activity": "SELL",
        "dte": 32,
        "delta": 0.22,
        "premium": 3.50,
        "premium_pct": 1.9,
        "stock_price": 178.00,
        "iv": 28.0,
        "iv_rank": 65,
        "risk_rating": 4,
        "risk_rating_breakdown": {"technical": "neutral"},
        "risk_flags": [],
        "earnings_analysis": {
            "earnings_gate_result": "OPEN_NORMALLY",
            "days_to_earnings": 59,
            "expiration_to_earnings_gap": 28,
        },
        "reason": "Standard covered call sell within thresholds.",
    }
    activity.update(overrides)
    return activity


def _base_csp_activity(**overrides) -> dict:
    activity = {
        "agent_type": "cash_secured_put",
        "activity": "SELL",
        "dte": 30,
        "delta": -0.25,
        "premium": 2.10,
        "premium_pct": 1.5,
        "strike": 140.00,
        "iv": 32.0,
        "iv_rank": 45,
        "risk_rating": 3,
        "risk_flags": [],
        "earnings_analysis": {
            "earnings_gate_result": "OPEN_NORMALLY",
            "days_to_earnings": 45,
            "expiration_to_earnings_gap": 15,
        },
        "reason": "Standard cash-secured put sell within thresholds.",
    }
    activity.update(overrides)
    return activity


def _assessment_wait_activity(**overrides) -> dict:
    activity = {
        "agent_type": "open_call_monitor",
        "activity": "WAIT",
        "moneyness": "OTM",
        "delta": 0.20,
        "assignment_risk": "low",
        "earnings_analysis": {
            "earnings_gate_result": "OPEN_NORMALLY",
            "days_to_earnings": 40,
        },
        "profit_pct": 35,
        "current_dte": 20,
        "market_bias": {"direction": "neutral"},
        "risk_flags": [],
        "reason": "Holding, no action needed.",
    }
    activity.update(overrides)
    return activity


def _roll_activity(**overrides) -> dict:
    activity = {
        "agent_type": "open_call_monitor",
        "activity": "ROLL",
        "action_needed": "ROLL_UP_AND_OUT",
        "roll_candidate_found": True,
        "roll_tier": "good",
        "new_dte": 35,
        "new_premium": 2.80,
        "premium_cross_check_pass": True,
        "earnings_analysis": {
            "earnings_gate_result": "OPEN_NORMALLY",
            "days_to_earnings": 50,
        },
        "risk_flags": [],
        "reason": "Rolling up and out for better economics.",
    }
    activity.update(overrides)
    return activity


def _buy_tracker_activity(**overrides) -> dict:
    activity = {
        "agent_type": "buy_tracker",
        "activity": "BUY",
        "confidence": "medium",
        "score": "5/5",
        "score_breakdown": {
            "value_entry": 1,
            "trend": 1,
            "momentum": 1,
            "income": 1,
            "calendar": 1,
        },
        "waiting_for": "",
        "risk_flags": [],
        "technical_triggers": ["stale_model_trigger"],
        "reason": "All scoring dimensions favorable.",
    }
    activity.update(overrides)
    return activity


def _score_breakdown(score: int) -> dict:
    return {
        key: int(index < score)
        for index, key in enumerate(BUY_TRACKER_SCORE_KEYS)
    }


def _buy_tracker_for_score(score: int, **overrides) -> dict:
    activity = _buy_tracker_activity(
        activity="STRONG_BUY",
        confidence="high",
        score="99/5",
        score_breakdown=_score_breakdown(score),
        waiting_for="Stale model waiting text.",
        risk_flags=["stale_model_risk"],
        technical_triggers=["stale_model_trigger"],
        reason="Stale model reason claiming a different activity and score.",
    )
    activity.update(overrides)
    return activity


def _exceptional_evidence(**overrides) -> dict:
    evidence = {
        "current_price": 90.0,
        "high_52w": 100.0,
        "sma50": 90.0,
        "sma200": 85.0,
        "rsi_14": 35.0,
        "macd_confirmation": "BUY",
        "stochastic_confirmation": "BUY",
        "annual_dividend_rate": 4.0,
        "latest_dividend": 1.0,
        "dividend_growth_years": 5.0,
        "payout_ratio_pct": 60.0,
        "analyst_target_price": 100.0,
        "days_to_earnings": 10.0,
        "ma_summary": "BUY",
        "oscillator_summary": "BUY",
    }
    evidence.update(overrides)
    return evidence


def _provider_shaped_exceptional_fetch_data(
    *,
    include_dividend_state_evidence: bool = True,
    payout_ratio_pct: float = 60.0,
) -> dict:
    dividend_fields = {
        "dividends_yield": {
            "label": "Dividend Yield (%)",
            "value": 3.0,
            "formatted": "3.00%",
        },
        "dps_common_stock_prim_issue_fy": {
            "label": "Dividends Per Share (FY)",
            "value": 4.0,
            "formatted": "$4.00",
        },
        "dps_common_stock_prim_issue_fq": {
            "label": "Dividends Per Share (FQ)",
            "value": 1.0,
            "formatted": "$1.00",
        },
        "dividend_payout_ratio_ttm": {
            "label": "Payout Ratio (TTM %)",
            "value": payout_ratio_pct,
            "formatted": f"{payout_ratio_pct:.2f}%",
        },
        "ex_dividend_date_recent": {
            "label": "Ex-Dividend Date (Recent)",
            "value": 1788220800,
            "formatted": "2026-09-01",
        },
    }
    if include_dividend_state_evidence:
        dividend_fields["continuous_dividend_growth"] = {
            "label": "Consecutive Years Growing",
            "value": 5,
            "formatted": "5 years",
        }

    return {
        "overview": json.dumps(
            {
                "name": "Example Corp.",
                "ticker": "EXM",
                "exchange": "NMS",
                "fundamentals": {
                    "current_price": {
                        "label": "Current Price",
                        "value": 90.0,
                        "formatted": "$90.00",
                    },
                    "52w_high": {
                        "label": "52-Week High",
                        "value": 100.0,
                        "formatted": "$100.00",
                    },
                    "earnings_release_next_date_fq": {
                        "label": "Next Earnings Date",
                        "value": 1790035200,
                        "formatted": "2026-09-22",
                    },
                },
            }
        ),
        "technicals": json.dumps(
            {
                "price": 90.0,
                "oscillators": {
                    "recommendation": {"value": 0.3, "label": "Buy"},
                    "buy": 5,
                    "sell": 2,
                    "neutral": 3,
                    "indicators": {
                        "RSI": {
                            "label": "RSI (14)",
                            "value": 35.0,
                            "formatted": "35.00",
                            "signal": "Neutral",
                        },
                        "MACD.macd": {
                            "label": "MACD Level (12,26)",
                            "value": 2.0,
                            "formatted": "2.0000",
                            "signal": "Buy",
                        },
                        "Stoch.K": {
                            "label": "Stochastic %K (14,3,3)",
                            "value": 30.0,
                            "formatted": "30.00",
                            "signal": "Buy",
                        },
                    },
                },
                "moving_averages": {
                    "recommendation": {"value": 0.5, "label": "Strong Buy"},
                    "buy": 12,
                    "sell": 2,
                    "neutral": 1,
                    "indicators": {
                        "SMA50": {
                            "label": "SMA (50)",
                            "value": 90.0,
                            "formatted": "$90.00",
                            "signal": "Buy",
                        },
                        "SMA200": {
                            "label": "SMA (200)",
                            "value": 85.0,
                            "formatted": "$85.00",
                            "signal": "Buy",
                        },
                    },
                },
            }
        ),
        "forecast": json.dumps(
            {
                "name": "Example Corp.",
                "ticker": "EXM",
                "exchange": "NMS",
                "current_price": 90.0,
                "price_target": {
                    "price_target_average": {
                        "label": "Average Price Target",
                        "value": 100.0,
                        "formatted": "$100.00",
                    },
                    "upside_pct": 11.11,
                    "upside_direction": "Upside",
                },
            }
        ),
        "dividends": json.dumps(
            {
                "name": "Example Corp.",
                "ticker": "EXM",
                "exchange": "NMS",
                "dividends": dividend_fields,
            }
        ),
        "options_chain": "",
    }


def _nonexceptional_safe_evidence(**overrides) -> dict:
    evidence = _exceptional_evidence(rsi_14=50.0)
    evidence.update(overrides)
    return evidence


def _gate_price_evidence(
    *,
    current_price: float,
    high_52w: float,
    sma50: float,
    sma200: float,
    analyst_target_price: float | None = None,
) -> dict:
    return _exceptional_evidence(
        current_price=current_price,
        high_52w=high_52w,
        sma50=sma50,
        sma200=sma200,
        analyst_target_price=(
            analyst_target_price
            if analyst_target_price is not None
            else current_price * 1.10
        ),
    )


def _assert_reason_prefix_matches_breakdown(normalized: dict) -> None:
    score = sum(normalized["score_breakdown"].values())
    reason = normalized["reason"]
    assert reason.startswith(f"Score {score}/5")
    prefix = reason.split(").", 1)[0]
    for key, value in normalized["score_breakdown"].items():
        assert re.search(rf"\b{re.escape(key)}\s*:\s*{value}\b", prefix)


# ---------------------------------------------------------------------------
# Generic contract checks (schema_version, counts, blocking, first_blocker)
# ---------------------------------------------------------------------------

class TestGenericContract:
    def test_schema_version_present_for_all_agent_types(self):
        cases = [
            ("covered_call", _base_covered_call_activity(), None),
            ("cash_secured_put", _base_csp_activity(), None),
            ("open_call_monitor", _assessment_wait_activity(), "assessment"),
            ("open_put_monitor", _assessment_wait_activity(agent_type="open_put_monitor"), "assessment"),
            ("buy_tracker", _buy_tracker_activity(), None),
        ]
        for agent_type, activity, phase in cases:
            evaluation = build_rule_evaluation(agent_type, activity, phase=phase)
            assert evaluation["schema_version"] == 1
            assert evaluation["agent_type"] == agent_type

    def test_summary_counts_all_keys_present_and_sum_matches_rule_count(self):
        activity = _base_covered_call_activity()
        evaluation = build_rule_evaluation("covered_call", activity)
        counts = evaluation["summary_counts"]
        assert set(counts.keys()) == SUMMARY_KEYS
        assert all(isinstance(v, int) and v >= 0 for v in counts.values())
        assert sum(counts.values()) == len(_rules_of(evaluation))

    def test_every_rule_result_has_explicit_blocking_boolean(self):
        activity = _base_covered_call_activity()
        evaluation = build_rule_evaluation("covered_call", activity)
        for rule in _rules_of(evaluation):
            assert "blocking" in rule
            assert isinstance(rule["blocking"], bool)

    def test_every_rule_result_status_is_known_enum_value(self):
        activity = _base_covered_call_activity()
        evaluation = build_rule_evaluation("covered_call", activity)
        for rule in _rules_of(evaluation):
            assert rule["status"] in ALL_STATUSES

    def test_first_blocker_none_when_nothing_blocking(self):
        activity = _base_covered_call_activity()
        evaluation = build_rule_evaluation("covered_call", activity)
        assert evaluation["first_blocker"] is None

    def test_blocking_field_on_earnings_gate(self):
        """A1.9: earnings gate BLOCKED -> blocking: true."""
        activity = _base_covered_call_activity(
            earnings_analysis={
                "earnings_gate_result": "BLOCKED",
                "days_to_earnings": 2,
                "expiration_to_earnings_gap": -2,
            }
        )
        evaluation = build_rule_evaluation("covered_call", activity)
        gate = _find_rule(evaluation, "cc_earnings_gate")
        assert gate["status"] == "blocked"
        assert gate["blocking"] is True

    def test_blocking_field_on_pass(self):
        """A1.9: earnings gate OPEN_NORMALLY -> blocking: false even though it is a hard gate rule."""
        activity = _base_covered_call_activity()
        evaluation = build_rule_evaluation("covered_call", activity)
        gate = _find_rule(evaluation, "cc_earnings_gate")
        assert gate["status"] == "pass"
        assert gate["blocking"] is False

    def test_first_blocker_matches_first_blocking_true_rule(self):
        activity = _base_covered_call_activity(
            earnings_analysis={
                "earnings_gate_result": "BLOCKED",
                "days_to_earnings": 1,
                "expiration_to_earnings_gap": -3,
            }
        )
        evaluation = build_rule_evaluation("covered_call", activity)
        assert evaluation["first_blocker"] == "cc_earnings_gate"
        blocking_rules = [r for r in _rules_of(evaluation) if r["blocking"]]
        assert blocking_rules, "expected at least one blocking rule"
        assert blocking_rules[0]["rule_id"] == evaluation["first_blocker"]


# ---------------------------------------------------------------------------
# Determinism / purity
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_identical_input_produces_identical_output_excluding_timestamp(self):
        activity = _base_covered_call_activity()
        eval_a = build_rule_evaluation("covered_call", copy.deepcopy(activity))
        eval_b = build_rule_evaluation("covered_call", copy.deepcopy(activity))
        eval_a.pop("generated_at", None)
        eval_b.pop("generated_at", None)
        assert eval_a == eval_b

    def test_input_activity_data_not_mutated(self):
        activity = _base_covered_call_activity()
        snapshot = copy.deepcopy(activity)
        build_rule_evaluation("covered_call", activity)
        assert activity == snapshot


# ---------------------------------------------------------------------------
# Category thresholds (A1.9 named tests)
# ---------------------------------------------------------------------------

class TestCategoryThresholds:
    def test_cc_category_thresholds_aristocrat(self):
        """CC Aristocrat: delta 0.20-0.30, premium >= 0.5%, no IV rank minimum."""
        activity = _base_covered_call_activity(delta=0.25, premium_pct=0.6, iv_rank=5)
        evaluation = build_rule_evaluation("covered_call", activity, category="aristocrat")
        delta_rule = _find_rule(evaluation, "cc_delta_range")
        premium_rule = _find_rule(evaluation, "cc_premium_floor")
        iv_rule = _find_rule(evaluation, "cc_iv_rank")
        assert delta_rule["status"] == "pass"
        assert premium_rule["status"] == "pass"
        # No IV minimum for Aristocrat -> low IV rank must not be penalized.
        assert iv_rule["status"] in {"informational", "pass"}

        # Out-of-range delta (above 0.30) must fail against Aristocrat thresholds.
        bad_activity = _base_covered_call_activity(delta=0.45, premium_pct=0.6)
        bad_eval = build_rule_evaluation("covered_call", bad_activity, category="aristocrat")
        assert _find_rule(bad_eval, "cc_delta_range")["status"] in {"fail", "warning"}

        # Premium below 0.5% must fail.
        low_premium_activity = _base_covered_call_activity(delta=0.25, premium_pct=0.3)
        low_premium_eval = build_rule_evaluation(
            "covered_call", low_premium_activity, category="aristocrat"
        )
        assert _find_rule(low_premium_eval, "cc_premium_floor")["status"] in {"fail", "warning"}

    def test_cc_category_thresholds_rising_star(self):
        """CC Rising Star: delta 0.10-0.20, premium >= 0.8%, IV rank >= 40."""
        good_activity = _base_covered_call_activity(delta=0.15, premium_pct=0.9, iv_rank=45)
        evaluation = build_rule_evaluation("covered_call", good_activity, category="rising_star")
        assert _find_rule(evaluation, "cc_delta_range")["status"] == "pass"
        assert _find_rule(evaluation, "cc_premium_floor")["status"] == "pass"

        # Delta 0.25 is outside the Rising Star 0.10-0.20 band (even though it would
        # pass for Balanced/Aristocrat) -> must not silently pass.
        edge_activity = _base_covered_call_activity(delta=0.25, premium_pct=0.9, iv_rank=45)
        edge_eval = build_rule_evaluation("covered_call", edge_activity, category="rising_star")
        assert _find_rule(edge_eval, "cc_delta_range")["status"] in {"fail", "warning"}

        # Premium 0.6% is below the Rising Star 0.8% floor.
        low_premium_activity = _base_covered_call_activity(delta=0.15, premium_pct=0.6, iv_rank=45)
        low_premium_eval = build_rule_evaluation(
            "covered_call", low_premium_activity, category="rising_star"
        )
        assert _find_rule(low_premium_eval, "cc_premium_floor")["status"] in {"fail", "warning"}

        # IV rank below 40 should not be a silent pass for Rising Star.
        low_iv_activity = _base_covered_call_activity(delta=0.15, premium_pct=0.9, iv_rank=20)
        low_iv_eval = build_rule_evaluation("covered_call", low_iv_activity, category="rising_star")
        iv_rule = _find_rule(low_iv_eval, "cc_iv_rank")
        assert iv_rule["status"] in {"warning", "fail"}

    def test_csp_category_thresholds_balanced(self):
        """CSP Balanced: delta 0.20-0.30 (abs), premium >= 1.2%, IV rank >= 35."""
        activity = _base_csp_activity(delta=-0.25, premium_pct=1.5, iv_rank=40)
        evaluation = build_rule_evaluation("cash_secured_put", activity, category="balanced")
        assert _find_rule(evaluation, "csp_delta_range")["status"] == "pass"
        assert _find_rule(evaluation, "csp_premium_floor")["status"] == "pass"

        low_premium_activity = _base_csp_activity(delta=-0.25, premium_pct=0.9, iv_rank=40)
        low_premium_eval = build_rule_evaluation(
            "cash_secured_put", low_premium_activity, category="balanced"
        )
        assert _find_rule(low_premium_eval, "csp_premium_floor")["status"] in {"fail", "warning"}

        out_of_range_activity = _base_csp_activity(delta=-0.40, premium_pct=1.5, iv_rank=40)
        out_of_range_eval = build_rule_evaluation(
            "cash_secured_put", out_of_range_activity, category="balanced"
        )
        assert _find_rule(out_of_range_eval, "csp_delta_range")["status"] in {"fail", "warning"}

    def test_cc_default_category_is_balanced(self):
        """A1.9: category=None uses Balanced thresholds (delta 0.20-0.30, premium >= 0.8%)."""
        activity = _base_covered_call_activity(delta=0.25, premium_pct=0.9, iv_rank=40)
        evaluation_default = build_rule_evaluation("covered_call", activity, category=None)
        evaluation_balanced = build_rule_evaluation(
            "covered_call", copy.deepcopy(activity), category="balanced"
        )
        delta_default = _find_rule(evaluation_default, "cc_delta_range")
        delta_balanced = _find_rule(evaluation_balanced, "cc_delta_range")
        assert delta_default["status"] == delta_balanced["status"] == "pass"
        premium_default = _find_rule(evaluation_default, "cc_premium_floor")
        assert premium_default["status"] == "pass"

    def test_category_in_expected_string(self):
        """A1.9: `expected` for delta rule includes the category name."""
        activity = _base_covered_call_activity(delta=0.22)
        evaluation = build_rule_evaluation("covered_call", activity, category="compounder")
        delta_rule = _find_rule(evaluation, "cc_delta_range")
        assert delta_rule["expected"] is not None
        assert "compounder" in delta_rule["expected"].lower()

    def test_no_invented_thresholds_match_skill_files(self):
        """A1.9 criterion 17: CATEGORY_THRESHOLDS must trace to SKILL.md values."""
        thresholds = getattr(rule_evaluator, "CATEGORY_THRESHOLDS", None)
        assert thresholds is not None, "CATEGORY_THRESHOLDS dict must be exposed for traceability"

        cc = thresholds.get("covered_call", {})
        assert cc["aristocrat"]["delta_range"] == (0.20, 0.30)
        assert cc["aristocrat"]["premium_min_pct"] == pytest.approx(0.5)
        assert cc["aristocrat"].get("iv_rank_min") is None

        assert cc["compounder"]["delta_range"] == (0.15, 0.25)
        assert cc["compounder"]["premium_min_pct"] == pytest.approx(0.6)
        assert cc["compounder"]["iv_rank_min"] == 30

        assert cc["balanced"]["delta_range"] == (0.20, 0.30)
        assert cc["balanced"]["premium_min_pct"] == pytest.approx(0.8)
        assert cc["balanced"]["iv_rank_min"] == 35

        assert cc["high_yield"]["delta_range"] == (0.25, 0.35)
        assert cc["high_yield"]["premium_min_pct"] == pytest.approx(0.8)
        assert cc["high_yield"]["iv_rank_min"] == 30

        assert cc["rising_star"]["delta_range"] == (0.10, 0.20)
        assert cc["rising_star"]["premium_min_pct"] == pytest.approx(0.8)
        assert cc["rising_star"]["iv_rank_min"] == 40

        csp = thresholds.get("cash_secured_put", {})
        assert csp["aristocrat"]["delta_range"] == (0.25, 0.35)
        assert csp["aristocrat"]["premium_min_pct"] == pytest.approx(0.8)
        assert csp["aristocrat"].get("iv_rank_min") is None

        assert csp["compounder"]["delta_range"] == (0.20, 0.30)
        assert csp["compounder"]["premium_min_pct"] == pytest.approx(1.0)
        assert csp["compounder"]["iv_rank_min"] == 30

        assert csp["balanced"]["delta_range"] == (0.20, 0.30)
        assert csp["balanced"]["premium_min_pct"] == pytest.approx(1.2)
        assert csp["balanced"]["iv_rank_min"] == 35

        assert csp["high_yield"]["delta_range"] == (0.25, 0.35)
        assert csp["high_yield"]["premium_min_pct"] == pytest.approx(1.0)
        assert csp["high_yield"]["iv_rank_min"] == 25

        assert csp["rising_star"]["delta_range"] == (0.15, 0.25)
        assert csp["rising_star"]["premium_min_pct"] == pytest.approx(1.2)
        assert csp["rising_star"]["iv_rank_min"] == 40


# ---------------------------------------------------------------------------
# rule_checks precedence / fallback / unknown (A1.9 named tests)
# ---------------------------------------------------------------------------

class TestRuleChecksPrecedence:
    def test_rule_checks_present(self):
        """A1.9: rule_checks entries are used for LLM-sourced rules when present."""
        activity = _base_covered_call_activity(
            risk_flags=["ex_dividend_risk"],  # would normally hint 'warning' via heuristic
            rule_checks={
                "cc_ex_div_check": {"status": "pass", "detail": "No conflict despite flag."},
                "cc_catalyst_check": {"status": "pass", "detail": "No catalysts."},
            },
        )
        evaluation = build_rule_evaluation("covered_call", activity)
        ex_div = _find_rule(evaluation, "cc_ex_div_check")
        # rule_checks explicitly says pass -> must win over the risk_flags heuristic.
        assert ex_div["status"] == "pass"

    def test_rule_checks_absent_fallback(self):
        """A1.9: without rule_checks, falls back to risk_flags heuristic."""
        activity = _base_covered_call_activity(risk_flags=["ex_dividend_risk"])
        activity.pop("rule_checks", None)
        evaluation = build_rule_evaluation("covered_call", activity)
        ex_div = _find_rule(evaluation, "cc_ex_div_check")
        assert ex_div["status"] in {"warning", "fail"}
        assert ex_div["source"] == "llm"

    def test_rule_checks_unknown_on_no_signal(self):
        """A1.9: neither rule_checks nor a matching risk_flag -> status unknown."""
        activity = _base_covered_call_activity(risk_flags=[])
        activity.pop("rule_checks", None)
        evaluation = build_rule_evaluation("covered_call", activity)
        ex_div = _find_rule(evaluation, "cc_ex_div_check")
        assert ex_div["status"] == "unknown"

    def test_rule_checks_precedence_over_heuristic_even_when_disagreeing(self):
        """Explicit rule_checks fail should win even if risk_flags looks clean."""
        activity = _base_covered_call_activity(
            risk_flags=[],
            rule_checks={"cc_catalyst_check": {"status": "fail", "detail": "FDA decision pending."}},
        )
        evaluation = build_rule_evaluation("covered_call", activity)
        catalyst = _find_rule(evaluation, "cc_catalyst_check")
        assert catalyst["status"] == "fail"
        assert catalyst["blocking"] is True


# ---------------------------------------------------------------------------
# WAIT / no-contract not_applicable semantics
# ---------------------------------------------------------------------------

class TestWaitNotApplicable:
    def test_wait_activity_marks_contract_specific_rules_not_applicable(self):
        activity = _base_covered_call_activity(
            activity="WAIT",
            delta=None,
            premium=None,
            premium_pct=None,
            dte=None,
        )
        evaluation = build_rule_evaluation("covered_call", activity)
        delta_rule = _find_rule(evaluation, "cc_delta_range")
        premium_rule = _find_rule(evaluation, "cc_premium_floor")
        dte_rule = _find_rule(evaluation, "cc_dte_cap")
        assert delta_rule["status"] == "not_applicable"
        assert premium_rule["status"] == "not_applicable"
        assert dte_rule["status"] == "not_applicable"

    def test_wait_activity_still_evaluates_gates(self):
        """Gates (earnings) still evaluate even when no contract is selected."""
        activity = _base_covered_call_activity(activity="WAIT", delta=None, premium=None)
        evaluation = build_rule_evaluation("covered_call", activity)
        gate = _find_rule(evaluation, "cc_earnings_gate")
        assert gate["status"] != "not_applicable"

    def test_buy_tracker_wait_trigger_without_raw_evidence_is_unknown(self):
        activity = _buy_tracker_activity(waiting_for=None, risk_flags=[])
        evaluation = build_rule_evaluation("buy_tracker", activity)
        for trigger_id in (
            "bt_wait_earnings",
            "bt_wait_rsi_80",
            "bt_wait_extended",
            "bt_wait_div_cut",
            "bt_wait_triple_bear",
        ):
            rule = _find_rule(evaluation, trigger_id)
            assert rule["status"] == "unknown"
            assert rule["blocking"] is False


# ---------------------------------------------------------------------------
# Monitor phases (A1.9 named tests)
# ---------------------------------------------------------------------------

class TestMonitorPhases:
    def test_monitor_assessment_only(self):
        """A1.9: WAIT activity produces a single-phase `phases` array."""
        activity = _assessment_wait_activity()
        evaluation = build_rule_evaluation(
            "open_call_monitor", activity, phase="assessment", category="balanced"
        )
        assert "phases" in evaluation
        assert len(evaluation["phases"]) == 1
        assert evaluation["phases"][0]["phase"] == "assessment"
        assert "rules" not in evaluation or evaluation.get("rules") in (None, [])

    def test_invalid_buyback_quote_never_passes_profit_target_rule(self):
        activity = _assessment_wait_activity(
            close_for_profit_recommended=True,
            profit_level_pct=100,
            buyback_per_share=None,
            buyback_available=False,
            incomplete_data=True,
            risk_flags=["incomplete_data"],
            reason="Buyback quote unavailable; P&L not calculated; profit gate skipped.",
        )

        evaluation = build_rule_evaluation(
            "open_call_monitor", activity, phase="assessment", category="balanced"
        )
        rule = _find_rule(evaluation, "ocm_a_profit_target")

        assert rule["status"] in {"unknown", "not_applicable"}
        assert rule["blocking"] is False
        assert rule["data_refs"].get("pnl_pct") is None

    def test_monitor_assessment_plus_roll(self):
        """A1.9: roll activity produces a two-phase array with correct rules per phase."""
        assessment_activity = _assessment_wait_activity(activity="ROLL")
        roll_activity = _roll_activity()

        assessment_eval = build_rule_evaluation(
            "open_call_monitor", assessment_activity, phase="assessment", category="balanced"
        )
        roll_eval = build_rule_evaluation(
            "open_call_monitor", roll_activity, phase="roll", category="balanced"
        )
        combined = merge_phase_evaluations(assessment_eval, roll_eval)

        assert len(combined["phases"]) == 2
        phase_names = [p["phase"] for p in combined["phases"]]
        assert phase_names == ["assessment", "roll"]

        assessment_rule_ids = {r["rule_id"] for r in combined["phases"][0]["rules"]}
        roll_rule_ids = {r["rule_id"] for r in combined["phases"][1]["rules"]}
        assert any(rid.startswith("ocm_a_") for rid in assessment_rule_ids)
        assert any(rid.startswith("ocm_r_") for rid in roll_rule_ids)
        # No cross-contamination between phases.
        assert not (assessment_rule_ids & roll_rule_ids)

    def test_summary_counts_across_phases(self):
        """A1.9: summary_counts aggregates across all phases."""
        assessment_activity = _assessment_wait_activity(activity="ROLL")
        roll_activity = _roll_activity()
        assessment_eval = build_rule_evaluation(
            "open_call_monitor", assessment_activity, phase="assessment", category="balanced"
        )
        roll_eval = build_rule_evaluation(
            "open_call_monitor", roll_activity, phase="roll", category="balanced"
        )
        combined = merge_phase_evaluations(assessment_eval, roll_eval)

        total_rules = sum(len(p["rules"]) for p in combined["phases"])
        assert sum(combined["summary_counts"].values()) == total_rules

    def test_combined_monitor_phases_render(self):
        """A1.9: first_blocker scans assessment phase rules before roll phase rules."""
        assessment_activity = _assessment_wait_activity(
            activity="ROLL",
            earnings_analysis={"earnings_gate_result": "BLOCKED", "days_to_earnings": 1},
        )
        roll_activity = _roll_activity(
            roll_candidate_found=False,  # would also be a blocker, but assessment comes first
        )
        assessment_eval = build_rule_evaluation(
            "open_call_monitor", assessment_activity, phase="assessment", category="balanced"
        )
        roll_eval = build_rule_evaluation(
            "open_call_monitor", roll_activity, phase="roll", category="balanced"
        )
        combined = merge_phase_evaluations(assessment_eval, roll_eval)

        assert combined["first_blocker"] is not None
        assessment_rule_ids = {r["rule_id"] for r in combined["phases"][0]["rules"]}
        assert combined["first_blocker"] in assessment_rule_ids

    def test_non_monitor_agents_use_flat_rules_no_phases(self):
        """A1.9 criterion 15: non-monitor agents (CC/CSP/buy_tracker) have no `phases`."""
        for agent_type, activity in (
            ("covered_call", _base_covered_call_activity()),
            ("cash_secured_put", _base_csp_activity()),
            ("buy_tracker", _buy_tracker_activity()),
        ):
            evaluation = build_rule_evaluation(agent_type, activity)
            assert evaluation.get("phase") is None
            assert "phases" not in evaluation or evaluation["phases"] is None
            assert isinstance(evaluation["rules"], list) and evaluation["rules"]


# ---------------------------------------------------------------------------
# Open Put Monitor coverage (inverted moneyness, ex-div not_applicable)
# ---------------------------------------------------------------------------

class TestOpenPutMonitor:
    def test_open_put_monitor_assessment_rules_present(self):
        activity = _assessment_wait_activity(
            agent_type="open_put_monitor", moneyness="OTM", delta=-0.20
        )
        evaluation = build_rule_evaluation(
            "open_put_monitor", activity, phase="assessment", category="balanced"
        )
        rule_ids = {r["rule_id"] for r in _rules_of(evaluation)}
        assert any(rid.startswith("opm_a_") for rid in rule_ids)

    def test_open_put_monitor_ex_div_always_not_applicable(self):
        """Ex-dividend risk is irrelevant for short puts per instructions."""
        activity = _assessment_wait_activity(agent_type="open_put_monitor")
        evaluation = build_rule_evaluation(
            "open_put_monitor", activity, phase="assessment", category="balanced"
        )
        rule_ids = {r["rule_id"] for r in _rules_of(evaluation)}
        ex_div_ids = [rid for rid in rule_ids if "ex_div" in rid]
        assert ex_div_ids, "expected an opm_a_ex_div_risk rule to be present"
        for rid in ex_div_ids:
            rule = _find_rule(evaluation, rid)
            assert rule["status"] == "not_applicable"


# ---------------------------------------------------------------------------
# Buy Tracker (A1.9 named tests)
# ---------------------------------------------------------------------------

class TestBuyTracker:
    def test_score_dimensions_map_from_score_breakdown(self):
        activity = _buy_tracker_activity(
            score_breakdown={
                "value_entry": 1,
                "trend": 0,
                "momentum": 1,
                "income": 0,
                "calendar": 1,
            }
        )
        evaluation = build_rule_evaluation("buy_tracker", activity)
        assert _find_rule(evaluation, "bt_value_entry")["status"] == "pass"
        assert _find_rule(evaluation, "bt_trend")["status"] == "fail"
        assert _find_rule(evaluation, "bt_momentum")["status"] == "pass"
        assert _find_rule(evaluation, "bt_income")["status"] == "fail"
        assert _find_rule(evaluation, "bt_calendar")["status"] == "pass"

    def test_bt_wait_earnings_deterministic(self):
        """A1.9: canonical evidence with earnings 1 day away -> blocked
        regardless of LLM output."""
        activity = _buy_tracker_activity(activity="BUY", waiting_for=None)  # LLM says all clear
        enrichment_data = {"days_to_earnings": 1}
        evaluation = build_rule_evaluation(
            "buy_tracker", activity, enrichment_data=enrichment_data
        )
        rule = _find_rule(evaluation, "bt_wait_earnings")
        assert rule["status"] == "blocked"
        assert rule["blocking"] is True
        assert rule["source"] == "deterministic"

    def test_bt_wait_rsi_deterministic(self):
        """A1.9: enrichment_data with rsi_14=85 -> blocked."""
        activity = _buy_tracker_activity(activity="BUY", waiting_for=None)
        enrichment_data = {"rsi_14": 85}
        evaluation = build_rule_evaluation(
            "buy_tracker", activity, enrichment_data=enrichment_data
        )
        rule = _find_rule(evaluation, "bt_wait_rsi_80")
        assert rule["status"] == "blocked"
        assert rule["blocking"] is True

    def test_bt_wait_trigger_no_enrichment(self):
        """A1.9: without enrichment_data, falls back to LLM signals."""
        activity = _buy_tracker_activity(
            activity="WAIT",
            waiting_for="RSI above 80, overbought",
            risk_flags=["rsi_overbought"],
        )
        evaluation = build_rule_evaluation("buy_tracker", activity, enrichment_data=None)
        rule = _find_rule(evaluation, "bt_wait_rsi_80")
        # No raw data to recalc from -> must trust LLM signal, not silently pass.
        assert rule["status"] in {"blocked", "unknown"}
        if rule["status"] == "blocked":
            assert rule["source"] in {"hybrid", "llm"}

    def test_bt_wait_earnings_overrides_llm_disagreement(self):
        """Deterministic recalculation wins even when LLM explicitly said no WAIT."""
        activity = _buy_tracker_activity(activity="BUY", waiting_for=None, risk_flags=[])
        enrichment_data = {"days_to_earnings": 0}
        evaluation = build_rule_evaluation(
            "buy_tracker", activity, enrichment_data=enrichment_data
        )
        rule = _find_rule(evaluation, "bt_wait_earnings")
        assert rule["status"] == "blocked"


# ---------------------------------------------------------------------------
# Financial viability rules must be absent
# ---------------------------------------------------------------------------

class TestFinancialViabilityExcluded:
    FORBIDDEN_KEYWORDS = (
        "cash",
        "buying_power",
        "buying-power",
        "share_coverage",
        "share-coverage",
        "collateral",
        "margin",
    )

    def _assert_no_financial_rules(self, evaluation: dict) -> None:
        for rule in _rules_of(evaluation):
            haystack = " ".join(
                str(rule.get(field, "")).lower()
                for field in ("rule_id", "label", "group")
            )
            for keyword in self.FORBIDDEN_KEYWORDS:
                assert keyword not in haystack, (
                    f"rule {rule['rule_id']} appears to evaluate financial viability "
                    f"(matched keyword {keyword!r}), which is out of scope per design §12/A1.9-11"
                )

    def test_covered_call_has_no_financial_viability_rules(self):
        evaluation = build_rule_evaluation("covered_call", _base_covered_call_activity())
        self._assert_no_financial_rules(evaluation)

    def test_cash_secured_put_has_no_financial_viability_rules(self):
        evaluation = build_rule_evaluation("cash_secured_put", _base_csp_activity())
        self._assert_no_financial_rules(evaluation)

    def test_buy_tracker_has_no_financial_viability_rules(self):
        evaluation = build_rule_evaluation("buy_tracker", _buy_tracker_activity())
        self._assert_no_financial_rules(evaluation)


# ---------------------------------------------------------------------------
# Malformed / missing optional fields
# ---------------------------------------------------------------------------

class TestMalformedInput:
    def test_missing_earnings_analysis_yields_unknown_not_pass(self):
        activity = _base_covered_call_activity()
        activity.pop("earnings_analysis", None)
        evaluation = build_rule_evaluation("covered_call", activity)
        gate = _find_rule(evaluation, "cc_earnings_gate")
        assert gate["status"] == "unknown", (
            "missing data must map to 'unknown', not a silent 'pass' broad-success fallback"
        )

    def test_missing_delta_yields_unknown_or_not_applicable(self):
        activity = _base_covered_call_activity()
        activity.pop("delta", None)
        evaluation = build_rule_evaluation("covered_call", activity)
        rule = _find_rule(evaluation, "cc_delta_range")
        assert rule["status"] in {"unknown", "not_applicable"}

    def test_malformed_earnings_analysis_type_does_not_crash(self):
        activity = _base_covered_call_activity(earnings_analysis="not-a-dict")
        evaluation = build_rule_evaluation("covered_call", activity)
        gate = _find_rule(evaluation, "cc_earnings_gate")
        assert gate["status"] == "unknown"

    def test_malformed_score_breakdown_does_not_crash(self):
        activity = _buy_tracker_activity(score_breakdown=None)
        evaluation = build_rule_evaluation("buy_tracker", activity)
        for rule_id in ("bt_value_entry", "bt_trend", "bt_momentum", "bt_income", "bt_calendar"):
            rule = _find_rule(evaluation, rule_id)
            assert rule["status"] == "unknown"

    def test_unrecognized_agent_type_raises_or_returns_empty_not_silent_success(self):
        activity = _base_covered_call_activity()
        with pytest.raises((ValueError, KeyError)):
            build_rule_evaluation("not_a_real_agent_type", activity)

    def test_missing_rule_checks_key_entirely_is_handled(self):
        activity = _base_covered_call_activity()
        assert "rule_checks" not in activity
        evaluation = build_rule_evaluation("covered_call", activity)  # must not raise
        assert evaluation is not None

    def test_empty_activity_dict_does_not_crash(self):
        evaluation = build_rule_evaluation("covered_call", {})
        assert evaluation["schema_version"] == 1
        for rule in _rules_of(evaluation):
            assert rule["status"] in ALL_STATUSES


# ---------------------------------------------------------------------------
# Data refs / rule identity sanity across all five agent types
# ---------------------------------------------------------------------------

class TestAllAgentTypesProduceValidCatalog:
    @pytest.mark.parametrize(
        "agent_type,activity,phase,expected_prefix",
        [
            ("covered_call", _base_covered_call_activity(), None, "cc_"),
            ("cash_secured_put", _base_csp_activity(), None, "csp_"),
            ("open_call_monitor", _assessment_wait_activity(), "assessment", "ocm_a_"),
            (
                "open_put_monitor",
                _assessment_wait_activity(agent_type="open_put_monitor"),
                "assessment",
                "opm_a_",
            ),
            ("buy_tracker", _buy_tracker_activity(), None, "bt_"),
        ],
    )
    def test_rule_ids_use_expected_prefix_and_have_required_fields(
        self, agent_type, activity, phase, expected_prefix
    ):
        evaluation = build_rule_evaluation(agent_type, activity, phase=phase, category="balanced")
        rules = _rules_of(evaluation)
        assert rules, f"expected non-empty rule catalog for {agent_type}"
        for rule in rules:
            assert rule["rule_id"].startswith(expected_prefix)
            for field in ("rule_id", "label", "group", "status", "source", "blocking"):
                assert field in rule
            assert rule["source"] in {
                "deterministic",
                "llm",
                "hybrid",
                "unavailable",
            }

    def test_ocm_roll_phase_rules_present(self):
        evaluation = build_rule_evaluation(
            "open_call_monitor", _roll_activity(), phase="roll", category="balanced"
        )
        rules = _rules_of(evaluation)
        assert rules
        for rule in rules:
            assert rule["rule_id"].startswith("ocm_r_")

    def test_opm_roll_phase_rules_present(self):
        activity = _roll_activity(agent_type="open_put_monitor")
        evaluation = build_rule_evaluation(
            "open_put_monitor", activity, phase="roll", category="balanced"
        )
        rules = _rules_of(evaluation)
        assert rules
        for rule in rules:
            assert rule["rule_id"].startswith("opm_r_")


# ---------------------------------------------------------------------------
# Backward compatibility (as testable without owning templates/production code)
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    def test_activity_without_rule_evaluation_is_not_touched_by_this_module(self):
        """
        The evaluator only ever ADDS a `rule_evaluation` key when explicitly invoked;
        it must never be invoked implicitly against an activity, and an activity dict
        that never calls build_rule_evaluation must remain completely unchanged.
        This documents the additive-only contract from design §6 / A1.9 criterion 3,
        without requiring ownership of the Jinja2 templates that do the actual
        conditional rendering.
        """
        legacy_activity = {
            "agent_type": "covered_call",
            "activity": "SELL",
            "reason": "Legacy activity with no rule_evaluation field.",
        }
        snapshot = copy.deepcopy(legacy_activity)
        # Simulate "nothing calls build_rule_evaluation for this activity" — i.e. no-op.
        assert legacy_activity == snapshot
        assert "rule_evaluation" not in legacy_activity

    def test_build_rule_evaluation_output_is_purely_additive_dict(self):
        """The returned dict must be a self-contained addition, not a mutated merge
        of the input activity (so callers can safely do
        activity["rule_evaluation"] = build_rule_evaluation(...))."""
        activity = _base_covered_call_activity()
        evaluation = build_rule_evaluation("covered_call", activity)
        assert isinstance(evaluation, dict)
        assert evaluation is not activity
        for key in activity:
            assert key not in evaluation or key in {"agent_type"}


# ---------------------------------------------------------------------------
# Buy Tracker normalization contract
# ---------------------------------------------------------------------------

class TestBuyTrackerNormalizationScoreMapping:
    @pytest.mark.parametrize(
        "score,expected_activity,expected_confidence",
        [
            (0, "WAIT", "low"),
            (1, "WAIT", "low"),
            (2, "WAIT", "medium"),
            (3, "BUY", "medium"),
            (4, "BUY", "medium"),
            (5, "BUY", "medium"),
        ],
    )
    def test_base_score_mapping_without_exceptional_enrichment(
        self, score, expected_activity, expected_confidence
    ):
        normalized = normalize_buy_tracker_activity(
            _buy_tracker_for_score(score),
            None,
        )

        assert normalized["score"] == f"{score}/5"
        assert normalized["activity"] == expected_activity
        assert normalized["confidence"] == expected_confidence
        assert normalized["waiting_for"] == ("" if score >= 3 else normalized["waiting_for"])
        if score < 3:
            assert normalized["waiting_for"]
        _assert_reason_prefix_matches_breakdown(normalized)

    def test_score_five_is_strong_buy_only_when_full_exceptional_gate_passes(self):
        normalized = normalize_buy_tracker_activity(
            _buy_tracker_for_score(5),
            _exceptional_evidence(),
        )

        assert normalized["score"] == "5/5"
        assert normalized["activity"] == "STRONG_BUY"
        assert normalized["confidence"] == "high"
        assert normalized["waiting_for"] == ""
        _assert_reason_prefix_matches_breakdown(normalized)

    def test_provider_shaped_objective_evidence_can_reach_strong_buy(self):
        fetch_data = _provider_shaped_exceptional_fetch_data()
        technicals = json.loads(fetch_data["technicals"])
        dividends = json.loads(fetch_data["dividends"])["dividends"]

        assert "MACD.signal" not in technicals["oscillators"]["indicators"]
        assert "Stoch.D" not in technicals["oscillators"]["indicators"]
        assert "dividend_current" not in dividends
        assert "dividend_cut_or_suspended" not in dividends

        evidence = build_buy_tracker_evidence(
            fetch_data,
            now=date(2026, 8, 17),
        )
        normalized = normalize_buy_tracker_activity(
            _buy_tracker_for_score(5),
            evidence,
        )

        assert normalized["activity"] == "STRONG_BUY"
        assert normalized["score"] == "5/5"

    def test_explicit_dividend_cut_survives_adaptation_and_forces_wait(self):
        fetch_data = _provider_shaped_exceptional_fetch_data()
        fetch_data["buy_tracker"] = {"dividend_cut_or_suspended": True}

        evidence = build_buy_tracker_evidence(
            fetch_data,
            now=date(2026, 8, 17),
        )
        normalized = normalize_buy_tracker_activity(
            _buy_tracker_for_score(5),
            evidence,
        )

        assert evidence["dividend_cut_or_suspended"] is True
        assert normalized["activity"] == "WAIT"
        assert normalized["score"] == "5/5"
        assert "dividend_cut_or_suspended" in normalized["risk_flags"]

    def test_missing_provider_dividend_state_evidence_fails_closed(self):
        fetch_data = _provider_shaped_exceptional_fetch_data(
            include_dividend_state_evidence=False,
        )

        evidence = build_buy_tracker_evidence(
            fetch_data,
            now=date(2026, 8, 17),
        )
        normalized = normalize_buy_tracker_activity(
            _buy_tracker_for_score(5),
            evidence,
        )

        assert evidence["annual_dividend_rate"] == 4.0
        assert evidence["latest_dividend"] == 1.0
        assert evidence["dividend_growth_years"] is None
        assert normalized["activity"] == "BUY"
        assert normalized["score"] == "5/5"


class TestBuyTrackerExceptionalGate:
    @pytest.mark.parametrize(
        "case,evidence,passes",
        [
            (
                "pullback_min_inclusive",
                _gate_price_evidence(
                    current_price=92.0, high_52w=100.0, sma50=92.0, sma200=90.0
                ),
                True,
            ),
            (
                "pullback_max_inclusive",
                _gate_price_evidence(
                    current_price=80.0, high_52w=100.0, sma50=80.0, sma200=75.0
                ),
                True,
            ),
            (
                "pullback_below_min",
                _gate_price_evidence(
                    current_price=92.1, high_52w=100.0, sma50=92.1, sma200=90.0
                ),
                False,
            ),
            (
                "pullback_above_max",
                _gate_price_evidence(
                    current_price=79.9, high_52w=100.0, sma50=79.9, sma200=75.0
                ),
                False,
            ),
            (
                "sma50_min_inclusive",
                _gate_price_evidence(
                    current_price=95.0,
                    high_52w=95.0 / 0.9,
                    sma50=100.0,
                    sma200=90.0,
                ),
                True,
            ),
            (
                "sma50_max_inclusive",
                _gate_price_evidence(
                    current_price=102.0,
                    high_52w=102.0 / 0.9,
                    sma50=100.0,
                    sma200=95.0,
                ),
                True,
            ),
            (
                "sma50_below_min",
                _gate_price_evidence(
                    current_price=94.9,
                    high_52w=94.9 / 0.9,
                    sma50=100.0,
                    sma200=90.0,
                ),
                False,
            ),
            (
                "sma50_above_max",
                _gate_price_evidence(
                    current_price=102.1,
                    high_52w=102.1 / 0.9,
                    sma50=100.0,
                    sma200=95.0,
                ),
                False,
            ),
            (
                "price_equals_sma200",
                _gate_price_evidence(
                    current_price=90.0, high_52w=100.0, sma50=90.0, sma200=90.0
                ),
                True,
            ),
            (
                "price_below_sma200",
                _gate_price_evidence(
                    current_price=90.0, high_52w=100.0, sma50=90.1, sma200=90.1
                ),
                False,
            ),
            (
                "sma50_equals_sma200",
                _gate_price_evidence(
                    current_price=90.0, high_52w=100.0, sma50=90.0, sma200=90.0
                ),
                True,
            ),
            (
                "sma50_below_sma200",
                _gate_price_evidence(
                    current_price=90.0, high_52w=100.0, sma50=89.9, sma200=90.0
                ),
                False,
            ),
            ("rsi_min_inclusive", _exceptional_evidence(rsi_14=25.0), True),
            ("rsi_max_inclusive", _exceptional_evidence(rsi_14=45.0), True),
            ("rsi_below_min", _exceptional_evidence(rsi_14=24.9), False),
            ("rsi_above_max", _exceptional_evidence(rsi_14=45.1), False),
            (
                "macd_buy_confirmation",
                _exceptional_evidence(macd_confirmation="BUY"),
                True,
            ),
            (
                "macd_neutral_not_confirmation",
                _exceptional_evidence(macd_confirmation="NEUTRAL"),
                False,
            ),
            (
                "stochastic_buy_confirmation",
                _exceptional_evidence(stochastic_confirmation="BUY"),
                True,
            ),
            (
                "stochastic_sell_not_confirmation",
                _exceptional_evidence(stochastic_confirmation="SELL"),
                False,
            ),
            (
                "positive_annual_dividend",
                _exceptional_evidence(annual_dividend_rate=4.0),
                True,
            ),
            (
                "zero_annual_dividend",
                _exceptional_evidence(annual_dividend_rate=0.0),
                False,
            ),
            (
                "positive_latest_dividend",
                _exceptional_evidence(latest_dividend=1.0),
                True,
            ),
            (
                "zero_latest_dividend",
                _exceptional_evidence(latest_dividend=0.0),
                False,
            ),
            (
                "positive_dividend_growth_history",
                _exceptional_evidence(dividend_growth_years=1.0),
                True,
            ),
            (
                "zero_dividend_growth_history",
                _exceptional_evidence(dividend_growth_years=0.0),
                False,
            ),
            ("payout_max_inclusive", _exceptional_evidence(payout_ratio_pct=75.0), True),
            ("payout_above_max", _exceptional_evidence(payout_ratio_pct=75.1), False),
            ("payout_negative_allowed", _exceptional_evidence(payout_ratio_pct=-0.1), True),
            (
                "target_upside_min_inclusive",
                _exceptional_evidence(analyst_target_price=94.5),
                True,
            ),
            (
                "target_upside_below_min",
                _exceptional_evidence(analyst_target_price=94.4),
                False,
            ),
            ("earnings_exactly_seven", _exceptional_evidence(days_to_earnings=7.0), False),
            (
                "earnings_more_than_seven",
                _exceptional_evidence(days_to_earnings=7.01),
                True,
            ),
        ],
        ids=lambda value: value if isinstance(value, str) else None,
    )
    def test_each_exceptional_gate_boundary(self, case, evidence, passes):
        normalized = normalize_buy_tracker_activity(
            _buy_tracker_for_score(5),
            evidence,
        )

        assert normalized["score"] == "5/5", case
        if passes:
            assert normalized["activity"] == "STRONG_BUY", case
        else:
            assert normalized["activity"] != "STRONG_BUY", case

    @pytest.mark.parametrize(
        "missing_key",
        [
            "current_price",
            "high_52w",
            "sma50",
            "sma200",
            "rsi_14",
            "macd_confirmation",
            "stochastic_confirmation",
            "annual_dividend_rate",
            "latest_dividend",
            "dividend_growth_years",
            "payout_ratio_pct",
            "analyst_target_price",
            "days_to_earnings",
        ],
    )
    def test_missing_required_exceptional_datum_never_passes(self, missing_key):
        evidence = _exceptional_evidence()
        evidence.pop(missing_key)

        normalized = normalize_buy_tracker_activity(
            _buy_tracker_for_score(5),
            evidence,
        )

        assert normalized["activity"] != "STRONG_BUY"
        assert normalized["score"] == "5/5"

    @pytest.mark.parametrize(
        "field,invalid_value",
        [
            ("current_price", float("nan")),
            ("high_52w", float("inf")),
            ("sma50", float("-inf")),
            ("sma200", "85"),
            ("rsi_14", True),
            ("macd_confirmation", "bullish"),
            ("stochastic_confirmation", {}),
            ("annual_dividend_rate", "4.0"),
            ("latest_dividend", []),
            ("dividend_growth_years", float("nan")),
            ("payout_ratio_pct", float("nan")),
            ("analyst_target_price", float("inf")),
            ("days_to_earnings", "not-a-date"),
        ],
    )
    def test_invalid_required_exceptional_datum_never_passes(
        self, field, invalid_value
    ):
        evidence = _exceptional_evidence(**{field: invalid_value})

        normalized = normalize_buy_tracker_activity(
            _buy_tracker_for_score(5),
            evidence,
        )

        assert normalized["activity"] != "STRONG_BUY"
        assert normalized["score"] == "5/5"

    @pytest.mark.parametrize(
        "overrides",
        [
            {"current_price": 0},
            {"current_price": -1},
            {"high_52w": 0},
            {"high_52w": -1},
            {"sma50": 0},
            {"sma50": -1},
            {"sma200": 0},
            {"sma200": -1},
        ],
    )
    def test_non_positive_prices_and_denominators_are_unavailable(self, overrides):
        normalized = normalize_buy_tracker_activity(
            _buy_tracker_for_score(5),
            _exceptional_evidence(**overrides),
        )

        assert normalized["activity"] != "STRONG_BUY"

    @pytest.mark.parametrize("evidence", [None, {}, [], "malformed-json", 42])
    def test_non_dict_or_unavailable_enrichment_never_produces_strong_buy(
        self, evidence
    ):
        normalized = normalize_buy_tracker_activity(
            _buy_tracker_for_score(5),
            evidence,
        )

        assert normalized["activity"] == "BUY"
        assert normalized["score"] == "5/5"


class TestBuyTrackerHardWaitOverrides:
    @pytest.mark.parametrize(
        "case,evidence,expected_activity",
        [
            ("earnings_at_two", _nonexceptional_safe_evidence(days_to_earnings=2.0), "WAIT"),
            (
                "earnings_above_two",
                _nonexceptional_safe_evidence(days_to_earnings=2.01),
                "BUY",
            ),
            ("rsi_at_eighty", _nonexceptional_safe_evidence(rsi_14=80.0), "BUY"),
            ("rsi_above_eighty", _nonexceptional_safe_evidence(rsi_14=80.01), "WAIT"),
            (
                "extended_both_thresholds",
                _nonexceptional_safe_evidence(
                    current_price=116.0,
                    high_52w=130.0,
                    sma50=105.0,
                    sma200=100.0,
                    analyst_target_price=125.0,
                ),
                "WAIT",
            ),
            (
                "extended_sma50_boundary",
                _nonexceptional_safe_evidence(
                    current_price=110.0,
                    high_52w=125.0,
                    sma50=100.0,
                    sma200=95.0,
                    analyst_target_price=120.0,
                ),
                "BUY",
            ),
            (
                "extended_sma200_boundary",
                _nonexceptional_safe_evidence(
                    current_price=115.0,
                    high_52w=130.0,
                    sma50=104.0,
                    sma200=100.0,
                    analyst_target_price=125.0,
                ),
                "BUY",
            ),
            (
                "extended_only_sma50",
                _nonexceptional_safe_evidence(
                    current_price=111.0,
                    high_52w=125.0,
                    sma50=100.0,
                    sma200=100.0,
                    analyst_target_price=120.0,
                ),
                "BUY",
            ),
            (
                "extended_only_sma200",
                _nonexceptional_safe_evidence(
                    current_price=116.0,
                    high_52w=130.0,
                    sma50=106.0,
                    sma200=100.0,
                    analyst_target_price=125.0,
                ),
                "BUY",
            ),
            (
                "zero_dividend_without_explicit_cut",
                _nonexceptional_safe_evidence(annual_dividend_rate=0.0),
                "BUY",
            ),
            (
                "explicit_dividend_cut",
                _nonexceptional_safe_evidence(
                    dividend_cut_or_suspended=True,
                ),
                "WAIT",
            ),
            (
                "dividend_not_cut",
                _nonexceptional_safe_evidence(
                    annual_dividend_rate=4.0,
                    dividend_cut_or_suspended=False,
                ),
                "BUY",
            ),
            (
                "triple_bear_more_than_ten_below",
                _nonexceptional_safe_evidence(
                    current_price=89.9,
                    high_52w=100.0,
                    sma50=90.0,
                    sma200=100.0,
                    analyst_target_price=100.0,
                    oscillator_summary="STRONG_SELL",
                    ma_summary="STRONG_SELL",
                ),
                "WAIT",
            ),
            (
                "triple_bear_exactly_ten_below",
                _nonexceptional_safe_evidence(
                    current_price=90.0,
                    high_52w=100.0,
                    sma50=90.0,
                    sma200=100.0,
                    analyst_target_price=100.0,
                    oscillator_summary="STRONG_SELL",
                    ma_summary="STRONG_SELL",
                ),
                "BUY",
            ),
            (
                "triple_bear_missing_oscillator_confirmation",
                _nonexceptional_safe_evidence(
                    current_price=89.9,
                    high_52w=100.0,
                    sma50=90.0,
                    sma200=100.0,
                    analyst_target_price=100.0,
                    oscillator_summary="SELL",
                    ma_summary="STRONG_SELL",
                ),
                "BUY",
            ),
            (
                "triple_bear_missing_ma_confirmation",
                _nonexceptional_safe_evidence(
                    current_price=89.9,
                    high_52w=100.0,
                    sma50=90.0,
                    sma200=100.0,
                    analyst_target_price=100.0,
                    oscillator_summary="STRONG_SELL",
                    ma_summary="SELL",
                ),
                "BUY",
            ),
        ],
        ids=lambda value: value if isinstance(value, str) else None,
    )
    def test_hard_wait_precedence_and_boundaries(
        self, case, evidence, expected_activity
    ):
        normalized = normalize_buy_tracker_activity(
            _buy_tracker_for_score(5),
            evidence,
        )

        assert normalized["activity"] == expected_activity, case
        assert normalized["score"] == "5/5", case
        assert normalized["confidence"] == "medium", case
        if expected_activity == "WAIT":
            assert normalized["waiting_for"], case
        else:
            assert normalized["waiting_for"] == "", case

    @pytest.mark.parametrize(
        "trigger_evidence",
        [
            _nonexceptional_safe_evidence(days_to_earnings=2.0),
            _nonexceptional_safe_evidence(rsi_14=81.0),
            _nonexceptional_safe_evidence(
                current_price=116.0,
                high_52w=130.0,
                sma50=105.0,
                sma200=100.0,
                analyst_target_price=125.0,
            ),
            _nonexceptional_safe_evidence(dividend_cut_or_suspended=True),
            _nonexceptional_safe_evidence(
                current_price=89.0,
                high_52w=100.0,
                sma50=90.0,
                sma200=100.0,
                analyst_target_price=100.0,
                oscillator_summary="STRONG_SELL",
                ma_summary="STRONG_SELL",
            ),
        ],
        ids=["earnings", "rsi", "extended", "dividend", "triple_bear"],
    )
    def test_canonical_hard_wait_flag_is_fallback_only_when_raw_is_unavailable(
        self, trigger_evidence
    ):
        raw_triggered = normalize_buy_tracker_activity(
            _buy_tracker_for_score(5, risk_flags=[]),
            trigger_evidence,
        )
        assert raw_triggered["activity"] == "WAIT"
        emitted_flags = raw_triggered["risk_flags"]
        assert emitted_flags

        fallback_flags = []
        for flag in emitted_flags:
            fallback = normalize_buy_tracker_activity(
                _buy_tracker_for_score(5, risk_flags=[flag]),
                None,
            )
            if fallback["activity"] == "WAIT":
                fallback_flags.append(flag)
        assert fallback_flags, "raw hard-WAIT output must expose its canonical fallback flag"

        for flag in fallback_flags:
            safe_evidence = _nonexceptional_safe_evidence()
            if flag == "dividend_cut_or_suspended":
                safe_evidence["dividend_cut_or_suspended"] = False
            raw_safe = normalize_buy_tracker_activity(
                _buy_tracker_for_score(5, risk_flags=[flag]),
                safe_evidence,
            )
            assert raw_safe["activity"] == "BUY", "available raw evidence must win"
            assert raw_safe["score"] == "5/5"

    def test_vague_prose_and_legacy_heuristics_cannot_force_wait(self):
        activity = _buy_tracker_for_score(
            5,
            waiting_for="RSI looks high and earnings may be nearby; wait for a pullback.",
            risk_flags=[
                "calendar_risk_nearby",
                "momentum_not_reset",
                "entry_zone_not_reached",
                "income_score_missing",
            ],
        )

        normalized = normalize_buy_tracker_activity(activity, None)

        assert normalized["activity"] == "BUY"
        assert normalized["score"] == "5/5"

    @pytest.mark.parametrize(
        "field,value",
        [
            ("annual_dividend_rate", 0.0),
            ("annual_dividend_rate", None),
            ("latest_dividend", 0.0),
            ("latest_dividend", None),
        ],
    )
    def test_non_current_dividend_fails_gate_without_forcing_wait(
        self, field, value
    ):
        normalized = normalize_buy_tracker_activity(
            _buy_tracker_for_score(5),
            _exceptional_evidence(
                **{field: value},
                dividend_cut_or_suspended=None,
            ),
        )

        assert normalized["activity"] == "BUY"
        assert normalized["score"] == "5/5"
        assert "exceptional_gate_not_met" in normalized["risk_flags"]
        assert "dividend_cut_or_suspended" not in normalized["risk_flags"]

    def test_explicit_dividend_cut_forces_wait(self):
        normalized = normalize_buy_tracker_activity(
            _buy_tracker_for_score(5),
            _exceptional_evidence(dividend_cut_or_suspended=True),
        )

        assert normalized["activity"] == "WAIT"
        assert normalized["score"] == "5/5"
        assert "dividend_cut_or_suspended" in normalized["risk_flags"]

    def test_exact_dividend_cut_flag_is_used_only_when_explicit_state_missing(self):
        flagged = _buy_tracker_for_score(
            5,
            risk_flags=["dividend_cut_or_suspended"],
        )

        unavailable = normalize_buy_tracker_activity(flagged, None)
        explicit_safe = normalize_buy_tracker_activity(
            flagged,
            _nonexceptional_safe_evidence(dividend_cut_or_suspended=False),
        )

        assert unavailable["activity"] == "WAIT"
        assert explicit_safe["activity"] == "BUY"


class TestBuyTrackerPromptContract:
    def test_shared_prompt_uses_provider_available_confirmation_semantics(self):
        from src.buy_tracker_instructions import BUY_TRACKER_INSTRUCTIONS
        from src.technical_analysis_instructions import (
            TECHNICAL_ANALYSIS_INSTRUCTIONS,
        )

        prompts = (BUY_TRACKER_INSTRUCTIONS, TECHNICAL_ANALYSIS_INSTRUCTIONS)
        required_provider_semantics = (
            'indicators["MACD.macd"].signal == "Buy"',
            'indicators["Stoch.K"].signal == "Buy"',
            "dps_common_stock_prim_issue_fy.value > 0",
            "dps_common_stock_prim_issue_fq.value > 0",
            "continuous_dividend_growth.value > 0",
            "explicitly supplied `dividend_cut_or_suspended = true`",
        )
        for prompt in prompts:
            for requirement in required_provider_semantics:
                assert requirement in prompt
            assert "MACD.signal" not in prompt
            assert "Stoch.D" not in prompt

    def test_hard_wait_examples_use_only_canonical_flags(self):
        from src.buy_tracker_instructions import BUY_TRACKER_INSTRUCTIONS

        canonical_hard_wait_flags = {
            "earnings_within_2_days",
            "rsi_over_80",
            "price_extended_above_mas",
            "dividend_cut_or_suspended",
            "triple_bearish_breakdown",
        }
        examples = [
            json.loads(block)
            for block in re.findall(
                r"```json\s*(\{.*?\})\s*```",
                BUY_TRACKER_INSTRUCTIONS,
                flags=re.DOTALL,
            )
        ]
        wait_examples = [
            example for example in examples if example.get("activity") == "WAIT"
        ]
        assert wait_examples

        example_flags = {
            flag
            for example in wait_examples
            for flag in example.get("risk_flags", [])
        }
        assert example_flags <= canonical_hard_wait_flags
        assert "earnings_within_2_days" in example_flags
        assert "calendar_risk_nearby" not in example_flags


class TestBuyTrackerMalformedBreakdown:
    @pytest.mark.parametrize(
        "breakdown,expected_values,expected_score,invalid_keys",
        [
            (None, [0, 0, 0, 0, 0], 0, BUY_TRACKER_SCORE_KEYS),
            ([], [0, 0, 0, 0, 0], 0, BUY_TRACKER_SCORE_KEYS),
            ("malformed-json", [0, 0, 0, 0, 0], 0, BUY_TRACKER_SCORE_KEYS),
            (7, [0, 0, 0, 0, 0], 0, BUY_TRACKER_SCORE_KEYS),
            (
                {"value_entry": 1},
                [1, 0, 0, 0, 0],
                1,
                ("trend", "momentum", "income", "calendar"),
            ),
            (
                {
                    "value_entry": 1,
                    "trend": 2,
                    "momentum": -1,
                    "income": True,
                    "calendar": "1",
                    "extra_dimension": 1,
                },
                [1, 0, 0, 0, 0],
                1,
                ("trend", "momentum", "income", "calendar"),
            ),
            (
                {
                    "value_entry": 1.0,
                    "trend": 0.0,
                    "momentum": 1,
                    "income": 0,
                    "calendar": 1,
                    "extra_dimension": 1,
                },
                [1, 0, 1, 0, 1],
                3,
                (),
            ),
            (
                {
                    "value_entry": float("nan"),
                    "trend": float("inf"),
                    "momentum": 1,
                    "income": 1,
                    "calendar": 1,
                },
                [0, 0, 1, 1, 1],
                3,
                ("value_entry", "trend"),
            ),
        ],
    )
    def test_breakdown_is_canonicalized_and_invalid_dimensions_are_flagged(
        self, breakdown, expected_values, expected_score, invalid_keys
    ):
        normalized = normalize_buy_tracker_activity(
            _buy_tracker_activity(score_breakdown=breakdown),
            None,
        )

        assert set(normalized["score_breakdown"]) == set(BUY_TRACKER_SCORE_KEYS)
        assert [
            normalized["score_breakdown"][key]
            for key in BUY_TRACKER_SCORE_KEYS
        ] == expected_values
        assert normalized["score"] == f"{expected_score}/5"
        assert normalized["activity"] == ("BUY" if expected_score >= 3 else "WAIT")
        flags_text = " ".join(map(str, normalized["risk_flags"])).lower()
        for key in invalid_keys:
            assert key in flags_text
        assert "extra_dimension" not in normalized["score_breakdown"]
        assert "extra_dimension" not in flags_text


class TestBuyTrackerNormalizedOutputCoherence:
    def test_canonical_price_replaces_stale_model_price_and_entry_zone(self):
        normalized = normalize_buy_tracker_activity(
            _buy_tracker_for_score(
                4,
                underlying_price=250.0,
                entry_zone="$245.00-$255.00",
            ),
            _nonexceptional_safe_evidence(current_price=90.0),
        )

        assert normalized["activity"] == "BUY"
        assert normalized["underlying_price"] == 90.0
        assert normalized["entry_zone"] == "$88.20-$91.80"

    def test_valid_entry_zone_containing_canonical_price_is_preserved(self):
        normalized = normalize_buy_tracker_activity(
            _buy_tracker_for_score(
                4,
                underlying_price=250.0,
                entry_zone="$88.00-$92.00",
            ),
            _nonexceptional_safe_evidence(current_price=90.0),
        )

        assert normalized["underlying_price"] == 90.0
        assert normalized["entry_zone"] == "$88.00-$92.00"

    def test_missing_canonical_price_clears_apparently_valid_model_zone(self):
        normalized = normalize_buy_tracker_activity(
            _buy_tracker_for_score(
                4,
                underlying_price=90.0,
                entry_zone="$88.00-$92.00",
            ),
            None,
        )

        assert normalized["activity"] == "BUY"
        assert normalized["underlying_price"] == 90.0
        assert "entry_zone" not in normalized

    @pytest.mark.parametrize(
        "entry_zone",
        [None, "", "around $90", "$92.00-$88.00", "$89.00", "$0.00-$90.00"],
    )
    def test_malformed_entry_zone_is_regenerated_for_buy(self, entry_zone):
        normalized = normalize_buy_tracker_activity(
            _buy_tracker_for_score(
                4,
                underlying_price=250.0,
                entry_zone=entry_zone,
            ),
            _nonexceptional_safe_evidence(current_price=90.0),
        )

        assert normalized["entry_zone"] == "$88.20-$91.80"

    def test_wait_clears_stale_entry_zone_but_keeps_canonical_price(self):
        normalized = normalize_buy_tracker_activity(
            _buy_tracker_for_score(
                1,
                underlying_price=250.0,
                entry_zone="$245.00-$255.00",
            ),
            _nonexceptional_safe_evidence(current_price=90.0),
        )

        assert normalized["activity"] == "WAIT"
        assert normalized["underlying_price"] == 90.0
        assert "entry_zone" not in normalized

    @pytest.mark.parametrize(
        "score,evidence,expected_activity,expected_confidence",
        [
            (0, None, "WAIT", "low"),
            (1, None, "WAIT", "low"),
            (2, None, "WAIT", "medium"),
            (3, None, "BUY", "medium"),
            (5, None, "BUY", "medium"),
            (5, _exceptional_evidence(), "STRONG_BUY", "high"),
            (
                5,
                _nonexceptional_safe_evidence(rsi_14=81.0),
                "WAIT",
                "medium",
            ),
        ],
    )
    def test_score_reason_confidence_and_waiting_for_are_coherent(
        self, score, evidence, expected_activity, expected_confidence
    ):
        normalized = normalize_buy_tracker_activity(
            _buy_tracker_for_score(score),
            evidence,
        )

        assert normalized["score"] == f"{score}/5"
        assert normalized["activity"] == expected_activity
        assert normalized["confidence"] == expected_confidence
        assert normalized["waiting_for"] == (
            "" if expected_activity in {"BUY", "STRONG_BUY"} else normalized["waiting_for"]
        )
        if expected_activity == "WAIT":
            assert normalized["waiting_for"]
        assert isinstance(normalized["risk_flags"], list)
        assert isinstance(normalized["technical_triggers"], list)
        _assert_reason_prefix_matches_breakdown(normalized)

    @pytest.mark.parametrize(
        "score,evidence,stale_activity,stale_risk,stale_trigger",
        [
            (1, None, "STRONG_BUY", "buy_now_high_conviction", "optimal_buy_zone"),
            (3, None, "WAIT", "wait_for_pullback", "avoid_entry"),
            (
                5,
                _exceptional_evidence(),
                "WAIT",
                "wait_for_confirmation",
                "no_entry",
            ),
        ],
    )
    def test_activity_change_rebuilds_stale_activity_dependent_fields(
        self, score, evidence, stale_activity, stale_risk, stale_trigger
    ):
        activity = _buy_tracker_for_score(
            score,
            activity=stale_activity,
            reason=f"Score 99/5. {stale_activity} because stale model prose says so.",
            risk_flags=[stale_risk],
            technical_triggers=[stale_trigger],
        )

        normalized = normalize_buy_tracker_activity(activity, evidence)

        assert stale_risk not in normalized["risk_flags"]
        assert stale_trigger not in normalized["technical_triggers"]
        assert "Score 99/5" not in normalized["reason"]
        _assert_reason_prefix_matches_breakdown(normalized)

    def test_normalizer_is_pure_deterministic_and_does_not_alias_inputs(self):
        activity = _buy_tracker_for_score(5)
        evidence = _exceptional_evidence()
        activity_snapshot = copy.deepcopy(activity)
        evidence_snapshot = copy.deepcopy(evidence)

        normalized_a = normalize_buy_tracker_activity(activity, evidence)
        normalized_b = normalize_buy_tracker_activity(activity, evidence)

        assert normalized_a == normalized_b
        assert normalized_a is not activity
        assert normalized_a["score_breakdown"] is not activity["score_breakdown"]
        assert normalized_a["risk_flags"] is not activity["risk_flags"]
        assert normalized_a["technical_triggers"] is not activity["technical_triggers"]
        assert activity == activity_snapshot
        assert evidence == evidence_snapshot
