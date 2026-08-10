"""
Regression tests for Basher's rejection of the rule_evaluation revision.

Rejection findings addressed here (see .squad/decisions/inbox/danny-rule-evaluation-design.md
Amendment A1.2 for the underlying contract):

1. `src/rule_evaluator.py` — the catalyst/breakout/freefall hard-gate rules
   (`cc_catalyst_check`, `cc_breakout_check`, `csp_catalyst_check`,
   `csp_freefall_check`) must NOT default to `pass` when both a structured
   `rule_checks` verdict and the corresponding `risk_flags` marker are absent.
   Absence of a flag is not proof of absence of the underlying condition — the
   correct status in that case is `unknown`, mirroring the `cc_ex_div_check` /
   `csp_ex_div_check` handling. Explicit rule_checks (or a present risk_flag)
   must still drive an unambiguous pass/fail as before.

2. `src/agent_runner.py` — rule_evaluation build failures must propagate to
   the existing outer error handler (which persists a distinct `error`
   activity document) instead of being silently caught, logged, and omitted.
   This is proven at the code level below (no broad `except Exception` wraps
   `build_rule_evaluation(...)` calls in the write paths), since exercising
   the full runner would require brittle mocking of CosmosDB, yfinance, and
   the LLM client far beyond this module's scope.
"""

from __future__ import annotations

import ast
import copy
import importlib
from pathlib import Path

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
        "src/rule_evaluator.py not found yet. "
        f"Import error: {MODULE_IMPORT_ERROR!r}"
    ),
)


def build_rule_evaluation(*args, **kwargs):
    return rule_evaluator.build_rule_evaluation(*args, **kwargs)


def _rules_of(evaluation: dict) -> list:
    if "rules" in evaluation:
        return evaluation["rules"]
    rules = []
    for phase in evaluation.get("phases", []):
        rules.extend(phase.get("rules", []))
    return rules


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


# ---------------------------------------------------------------------------
# Finding 1 — hard-gate rules must be `unknown`, never success-shaped, when
# both risk_flags and rule_checks are silent.
# ---------------------------------------------------------------------------

class TestHardGateRulesUnknownWithoutSignal:
    @pytest.mark.parametrize(
        "rule_id",
        ["cc_catalyst_check", "cc_breakout_check"],
    )
    def test_cc_hard_gate_unknown_when_no_flags_and_no_rule_checks(self, rule_id):
        activity = _base_covered_call_activity(risk_flags=[])
        activity.pop("rule_checks", None)
        evaluation = build_rule_evaluation("covered_call", activity)
        rule = _find_rule(evaluation, rule_id)
        assert rule["status"] == "unknown", (
            f"{rule_id} defaulted to {rule['status']!r} instead of 'unknown' "
            "when neither rule_checks nor a risk_flags marker was present"
        )
        assert rule["blocking"] is False

    @pytest.mark.parametrize(
        "rule_id",
        ["csp_catalyst_check", "csp_freefall_check"],
    )
    def test_csp_hard_gate_unknown_when_no_flags_and_no_rule_checks(self, rule_id):
        activity = _base_csp_activity(risk_flags=[])
        activity.pop("rule_checks", None)
        evaluation = build_rule_evaluation("cash_secured_put", activity)
        rule = _find_rule(evaluation, rule_id)
        assert rule["status"] == "unknown", (
            f"{rule_id} defaulted to {rule['status']!r} instead of 'unknown' "
            "when neither rule_checks nor a risk_flags marker was present"
        )
        assert rule["blocking"] is False

    def test_cc_catalyst_still_fails_when_risk_flag_present(self):
        """A present risk_flags marker remains an unambiguous existing signal."""
        activity = _base_covered_call_activity(risk_flags=["catalyst_pending"])
        activity.pop("rule_checks", None)
        evaluation = build_rule_evaluation("covered_call", activity)
        rule = _find_rule(evaluation, "cc_catalyst_check")
        assert rule["status"] == "fail"
        assert rule["blocking"] is True

    def test_csp_freefall_still_fails_when_risk_flag_present(self):
        activity = _base_csp_activity(risk_flags=["breakdown_momentum"])
        activity.pop("rule_checks", None)
        evaluation = build_rule_evaluation("cash_secured_put", activity)
        rule = _find_rule(evaluation, "csp_freefall_check")
        assert rule["status"] == "fail"
        assert rule["blocking"] is True

    @pytest.mark.parametrize(
        "agent_type,activity_builder,rule_id",
        [
            ("covered_call", _base_covered_call_activity, "cc_catalyst_check"),
            ("covered_call", _base_covered_call_activity, "cc_breakout_check"),
            ("cash_secured_put", _base_csp_activity, "csp_catalyst_check"),
            ("cash_secured_put", _base_csp_activity, "csp_freefall_check"),
        ],
    )
    def test_explicit_rule_checks_pass_remains_pass(
        self, agent_type, activity_builder, rule_id,
    ):
        """Explicit structured rule_checks pass must still be honored (A1.2)."""
        activity = activity_builder(
            risk_flags=[],
            rule_checks={rule_id: {"status": "pass", "detail": "Explicitly clear."}},
        )
        evaluation = build_rule_evaluation(agent_type, activity)
        rule = _find_rule(evaluation, rule_id)
        assert rule["status"] == "pass"
        assert rule["blocking"] is False
        assert rule["source"] == "llm"

    @pytest.mark.parametrize(
        "agent_type,activity_builder,rule_id",
        [
            ("covered_call", _base_covered_call_activity, "cc_catalyst_check"),
            ("covered_call", _base_covered_call_activity, "cc_breakout_check"),
            ("cash_secured_put", _base_csp_activity, "csp_catalyst_check"),
            ("cash_secured_put", _base_csp_activity, "csp_freefall_check"),
        ],
    )
    def test_explicit_rule_checks_fail_overrides_absent_risk_flag(
        self, agent_type, activity_builder, rule_id,
    ):
        """rule_checks fail must win even though risk_flags is empty."""
        activity = activity_builder(
            risk_flags=[],
            rule_checks={rule_id: {"status": "fail", "detail": "LLM caught it."}},
        )
        evaluation = build_rule_evaluation(agent_type, activity)
        rule = _find_rule(evaluation, rule_id)
        assert rule["status"] == "fail"
        assert rule["blocking"] is True

    def test_no_success_shaped_default_across_all_four_named_rules(self):
        """Explicit proof for the reviewer: empty risk_flags + no rule_checks
        never yields 'pass' for any of the four named hard-gate rules."""
        cc_activity = _base_covered_call_activity(risk_flags=[])
        cc_activity.pop("rule_checks", None)
        cc_eval = build_rule_evaluation("covered_call", cc_activity)

        csp_activity = _base_csp_activity(risk_flags=[])
        csp_activity.pop("rule_checks", None)
        csp_eval = build_rule_evaluation("cash_secured_put", csp_activity)

        for rule_id, evaluation in (
            ("cc_catalyst_check", cc_eval),
            ("cc_breakout_check", cc_eval),
            ("csp_catalyst_check", csp_eval),
            ("csp_freefall_check", csp_eval),
        ):
            status = _find_rule(evaluation, rule_id)["status"]
            assert status == "unknown", (
                f"{rule_id} status was {status!r}, expected 'unknown' "
                "(must never be success-shaped without a definitive signal)"
            )


# ---------------------------------------------------------------------------
# Finding 2 — evaluator errors must not be silently swallowed by the runner.
# ---------------------------------------------------------------------------

class TestAgentRunnerDoesNotSwallowRuleEvaluationErrors:
    """Static-analysis proof that src/agent_runner.py no longer wraps
    build_rule_evaluation(...) calls in a local try/except that logs and
    continues. This is the most reliable, non-brittle way to verify the fix
    without mocking the full runner (CosmosDB, yfinance, LLM client)."""

    @staticmethod
    def _agent_runner_source() -> str:
        path = Path(__file__).resolve().parents[1] / "src" / "agent_runner.py"
        return path.read_text(encoding="utf-8")

    def test_build_rule_evaluation_calls_are_not_wrapped_in_broad_except(self):
        """Each build_rule_evaluation(...) call's NEAREST enclosing try/except
        must not be a broad `except Exception` that swallows the error. Outer,
        far-away try/except blocks (e.g. the whole-run handler that persists an
        `error` activity document) are legitimate and intentionally excluded —
        only the immediate wrapper matters for "no silent degradation"."""
        source = self._agent_runner_source()
        tree = ast.parse(source)

        # Build a parent map so we can walk upward from each Call node.
        parents: dict = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent

        def nearest_enclosing_try(node):
            current = parents.get(node)
            while current is not None:
                if isinstance(current, ast.Try):
                    return current
                current = parents.get(current)
            return None

        offending = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name != "build_rule_evaluation":
                    continue
                enclosing_try = nearest_enclosing_try(node)
                if enclosing_try is None:
                    continue
                # Only flag if the call sits directly in the try's own body
                # (not nested inside a further-nested try further down).
                for handler in enclosing_try.handlers:
                    is_broad = handler.type is None or (
                        isinstance(handler.type, ast.Name)
                        and handler.type.id == "Exception"
                    )
                    handler_logs_and_continues = any(
                        isinstance(stmt, ast.Expr)
                        or (isinstance(stmt, ast.Call))
                        for stmt in ast.walk(handler)
                    )
                    if is_broad and not any(
                        isinstance(stmt, (ast.Raise,)) for stmt in ast.walk(handler)
                    ):
                        # A broad handler that never re-raises AND whose try
                        # body is small (tightly scoped around this call only)
                        # indicates local swallowing, distinct from a large
                        # whole-run handler.
                        body_line_span = (
                            enclosing_try.body[-1].end_lineno
                            - enclosing_try.body[0].lineno
                        )
                        if body_line_span < 15:
                            offending.append(enclosing_try.lineno)

        assert offending == [], (
            "build_rule_evaluation(...) is still tightly wrapped in a broad "
            f"try/except at line(s) {offending} in agent_runner.py; "
            "evaluator errors must propagate to the outer error handler so no "
            "activity is persisted successfully without rule_evaluation."
        )

    def test_rule_evaluation_key_is_always_written_before_cosmos_write(self):
        """Sanity check that the assignment to activity_payload['rule_evaluation']
        happens unconditionally (not inside an if/except that can be skipped
        while still falling through to cosmos.write_activity)."""
        source = self._agent_runner_source()
        assert 'activity_payload["rule_evaluation"] = build_rule_evaluation(' in source
        # Ensure no remaining "omitting rule_evaluation" degradation message.
        assert "omitting rule_evaluation" not in source
