"""Rusty's pipeline-plumbing tests for Supervisor/Alpha execution traces
(design item #5 of `.squad/decisions/inbox/danny-supervisor-alpha-traces-design.md`).

Scope discipline (Rusty's charter: framework/plumbing, never strategy):
these tests exercise the *orchestration wiring* added to
`run_symbol_agent` / `run_position_monitor` -- the one-`run_id`-per-cycle
mint, and the `parent_trace_id` chain threaded into every Supervisor/Alpha
call site -- not the LLM-facing parsing/error-string internals of
`_run_supervisor_review`/`_run_alpha_review` themselves. That per-method
adversarial surface (full-field capture, enumerated error strings, the
`enabled_types` toggle, tracing-failure isolation) is Basher's
`test_agent_trace_adversarial.py`; duplicating it here would just be two
homes for the same assertion. This file instead answers the two questions
item #5 asks that adversarial suite does not:
  1. Does `activity_payload["run_id"]` on the persisted activity match the
     `run_id` every phase of that cycle actually traced with (design §5)?
  2. Does `parent_trace_id` chain to the correct *causally preceding*
     phase in every pipeline shape -- single-agent (`analysis` ->
     supervisor/alpha), 2-phase without a roll (`assessment` ->
     supervisor/alpha), and 2-phase with a roll (`assessment` -> `roll`
     -> supervisor/alpha, never back to `assessment`) (design §2)?

Reuses Linus's already-passing `run_symbol_agent`/`run_position_monitor`
fixtures from `test_force_alpha_execution.py` (the same disclosed-reuse
pattern Basher's adversarial suite uses -- a second `monkeypatch.setattr`
layered on top observes the real call sites, it does not fabricate a
result) rather than re-deriving ~150 lines of LLM-boundary/Cosmos fakes
from scratch.
"""

from __future__ import annotations

import sys
import types

# Other test modules may install a deliberately small agent_framework stub;
# reuse it if already present (mirrors the existing test files' pattern).
_af = sys.modules.get("agent_framework")
if _af is not None and not hasattr(_af, "SkillsProvider"):
    _af.SkillsProvider = object
    if "agent_framework.openai" not in sys.modules:
        openai_stub = types.ModuleType("agent_framework.openai")
        openai_stub.OpenAIChatCompletionClient = object
        sys.modules["agent_framework.openai"] = openai_stub

from tests.test_force_alpha_execution import (  # noqa: E402
    _symbol_runner_fixture,
    _run_symbol_agent,
    _monitor_runner_fixture,
    _run_position_monitor,
    _FakeFetcher,
    _market_data_for_monitor,
)


def _install_trace_spy(monkeypatch, runner):
    """Layer a stateful `_record_trace` spy on top of an already-built
    fixture: records every call's kwargs and returns a distinguishable,
    incrementing trace id so `parent_trace_id` linkage can be asserted,
    mirroring the real method's `Optional[str]` contract."""
    calls = []

    def spy(cosmos_arg, **kwargs):
        trace_id = f"trace-{len(calls)}"
        calls.append({"id": trace_id, **kwargs})
        return trace_id

    monkeypatch.setattr(runner, "_record_trace", spy)
    return calls


def _install_review_spy(monkeypatch, runner, *, alpha_result=None):
    """Layer spies on top of `_run_supervisor_review`/`_run_alpha_review`
    that record the kwargs they were called with (run_id, parent_trace_id,
    cosmos) without touching any LLM/network boundary."""
    calls = []

    async def spy_supervisor(**kwargs):
        calls.append({"phase": "supervisor", **kwargs})
        return None

    async def spy_alpha(**kwargs):
        calls.append({"phase": "alpha", **kwargs})
        return alpha_result

    monkeypatch.setattr(runner, "_run_supervisor_review", spy_supervisor)
    monkeypatch.setattr(runner, "_run_alpha_review", spy_alpha)
    return calls


# ===========================================================================
# `run_symbol_agent` -- single-agent path (`analysis` -> supervisor/alpha)
# ===========================================================================

def test_symbol_agent_calm_wait_forced_run_id_and_parent_trace_id_chain(monkeypatch):
    """Forcing Alpha on a calm WAIT runs both reviews; both must receive
    the *same* `run_id` as the analysis trace, and a `parent_trace_id`
    pointing at the analysis trace's own document id -- not `None`, not a
    stale/mismatched value."""
    runner, cosmos, notifier, state = _symbol_runner_fixture(monkeypatch, activity="WAIT")
    trace_calls = _install_trace_spy(monkeypatch, runner)
    review_calls = _install_review_spy(
        monkeypatch, runner,
        alpha_result={"opportunity_strength": "MODERATE", "one_liner": "x"},
    )

    _run_symbol_agent(runner, cosmos, run_trigger="manual", force_alpha=True)

    # Only the primary phase actually calls the (now-faked) review methods'
    # internal `_record_trace` -- since those methods are spied out here,
    # the sole real `_record_trace` invocation left is the analysis phase.
    assert len(trace_calls) == 1
    analysis_trace = trace_calls[0]
    assert analysis_trace["phase"] == "analysis"
    assert analysis_trace["parent_trace_id"] is None
    run_id = analysis_trace["run_id"]
    assert run_id

    assert len(review_calls) == 2
    for call in review_calls:
        assert call["run_id"] == run_id
        assert call["parent_trace_id"] == analysis_trace["id"]
        assert call["cosmos"] is cosmos

    assert cosmos.activities[0]["run_id"] == run_id


def test_symbol_agent_alert_path_alpha_parent_trace_id_matches_analysis(monkeypatch):
    """An alert (SELL) already runs Alpha on its own merits -- the same
    `run_id`/`parent_trace_id` chaining must hold whether or not forcing
    was involved."""
    runner, cosmos, notifier, state = _symbol_runner_fixture(
        monkeypatch, activity="SELL",
    )
    trace_calls = _install_trace_spy(monkeypatch, runner)
    review_calls = _install_review_spy(
        monkeypatch, runner,
        alpha_result={"opportunity_strength": "NONE", "one_liner": "x"},
    )

    _run_symbol_agent(runner, cosmos, run_trigger="scheduled", force_alpha=False)

    assert len(trace_calls) == 1
    analysis_trace = trace_calls[0]
    assert analysis_trace["phase"] == "analysis"
    run_id = analysis_trace["run_id"]

    assert len(review_calls) == 2
    for call in review_calls:
        assert call["run_id"] == run_id
        assert call["parent_trace_id"] == analysis_trace["id"]

    assert cosmos.activities[0]["run_id"] == run_id


# ===========================================================================
# `run_position_monitor` -- 2-phase path, no roll (`assessment` -> supervisor/alpha)
# ===========================================================================

def test_monitor_two_phase_no_roll_parent_trace_id_is_assessment(monkeypatch):
    """When Phase 2 (roll) never runs, Supervisor/Alpha's `parent_trace_id`
    must point at the assessment trace -- `final_phase_trace_id` must stay
    at its initial value, never a stale/None fallback."""
    runner, cosmos, notifier, state = _monitor_runner_fixture(
        monkeypatch,
        alpha_result={"opportunity_strength": "MODERATE", "one_liner": "x"},
    )

    async def fake_assessment_with_trace_id(**kwargs):
        return ("SUMMARY: MSFT | WAIT", None, None, "assess-trace-id")

    monkeypatch.setattr(runner, "_run_position_assessment", fake_assessment_with_trace_id)
    review_calls = _install_review_spy(
        monkeypatch, runner,
        alpha_result={"opportunity_strength": "MODERATE", "one_liner": "x"},
    )

    _run_position_monitor(
        runner, cosmos, _FakeFetcher(_market_data_for_monitor()),
        run_trigger="manual", force_alpha=True,
    )

    assert state["phase2_calls"] == 0  # no roll ran this cycle
    assert len(review_calls) == 2
    for call in review_calls:
        assert call["parent_trace_id"] == "assess-trace-id"
        assert call["run_id"]

    assert cosmos.activities[0]["run_id"] == review_calls[0]["run_id"]


def test_monitor_two_phase_with_roll_parent_trace_id_is_roll_not_assessment(monkeypatch):
    """When Phase 2 (roll) does run, Supervisor/Alpha's `parent_trace_id`
    must switch to the roll trace -- the causally-nearest phase -- not
    fall back to the (now stale) assessment trace."""
    runner, cosmos, notifier, state = _monitor_runner_fixture(
        monkeypatch, roll_activity={"activity": "ROLL_OUT"},
        alpha_result={"opportunity_strength": "NONE", "one_liner": "x"},
    )

    async def fake_assessment_with_trace_id(**kwargs):
        return (
            "SUMMARY: MSFT | ROLL_OUT",
            None,
            {
                "action_needed": "ROLL_OUT",
                "symbol": "MSFT",
                "exchange": "NASDAQ",
                "current_strike": 420.0,
                "current_expiration": "2026-09-18",
                "underlying_price": 410.0,
                "reason": "Rolling out for more premium.",
            },
            "assess-trace-id",
        )

    async def fake_roll_with_trace_id(**kwargs):
        state["phase2_calls"] += 1
        activity = {
            "activity": "ROLL_OUT",
            "current_strike": 420.0,
            "current_expiration": "2026-09-18",
            "underlying_price": 410.0,
            "reason": "Rolling out for more premium.",
        }
        return "SUMMARY: MSFT | ROLL_OUT open call", activity, "roll-trace-id"

    monkeypatch.setattr(runner, "_run_position_assessment", fake_assessment_with_trace_id)
    monkeypatch.setattr(runner, "_run_roll_management", fake_roll_with_trace_id)
    review_calls = _install_review_spy(
        monkeypatch, runner,
        alpha_result={"opportunity_strength": "NONE", "one_liner": "x"},
    )

    _run_position_monitor(
        runner, cosmos, _FakeFetcher(_market_data_for_monitor()),
        run_trigger="manual", force_alpha=True,
    )

    assert state["phase2_calls"] == 1  # roll did run this cycle
    assert len(review_calls) == 2
    for call in review_calls:
        assert call["parent_trace_id"] == "roll-trace-id"
        assert call["parent_trace_id"] != "assess-trace-id"
        assert call["run_id"]

    assert cosmos.activities[0]["run_id"] == review_calls[0]["run_id"]
    assert cosmos.activities[0]["is_alert"] is True
