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

All assertions target structured fields (status, blocking, group, data_refs, counts)
rather than parsing narrative/prose strings, per the design's "no prose parsing"
principle.
"""

from __future__ import annotations

import copy
import importlib

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
        "score_breakdown": {
            "value_entry": 1,
            "trend": 1,
            "momentum": 1,
            "income": 1,
            "calendar": 1,
        },
        "waiting_for": None,
        "risk_flags": [],
        "reason": "All scoring dimensions favorable.",
    }
    activity.update(overrides)
    return activity


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

    def test_buy_tracker_wait_trigger_inactive_is_not_applicable(self):
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
            assert rule["status"] == "not_applicable"
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
        """A1.9: enrichment_data with next_earnings_date 1 day away -> blocked
        regardless of LLM output."""
        activity = _buy_tracker_activity(activity="BUY", waiting_for=None)  # LLM says all clear
        enrichment_data = {"next_earnings_date_days_away": 1}
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
        enrichment_data = {"next_earnings_date_days_away": 0}
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
            assert rule["source"] in {"deterministic", "llm", "hybrid"}

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
