"""Basher's adversarial acceptance suite for Supervisor/Alpha execution
traces (``AgentRunner._run_supervisor_review`` / ``_run_alpha_review`` +
``_record_trace``), reviewed against the ACCEPTED design at
``.squad/decisions/inbox/danny-supervisor-alpha-traces-design.md``
(item #6 of its owner table, plus the reviewer-gate must-not-regress
checks in its §11).

Scope and intent (Basher's charter: testing/QA, never production code):
  * Exercises the REAL ``_run_supervisor_review`` / ``_run_alpha_review``
    directly, faking only the ``Agent``/LLM boundary (a per-test scripted
    fake, not a shared mutual fake) and a minimal fake Cosmos that records
    every ``write_agent_trace`` call verbatim. This proves the trace
    document actually written by the real method, not a stand-in for it.
  * Covers: full-field capture on success (prompt / raw response / parsed
    output / model / duration / phase / agent_type / run_id /
    parent_trace_id); the *unmapped* ``agent_type`` requirement for
    ``open_call_monitor`` / ``open_put_monitor`` (design §1); every
    enumerated ``error`` string from design §3 (``no_parseable_json``,
    ``missing_required_fields:...``, ``invalid_challenge_strength:...`` /
    ``invalid_opportunity_strength:...``, and the exception path); the
    "must be initialized to None before try:" requirement so a crash
    *before* ``agent.run()`` is ever called still yields a safe,
    all-None-fields trace (design §3); the model-completeness fix
    (``model=None`` resolves to the real deployment in the trace, design
    §3); tracing-failure isolation (design §8: a raising
    ``cosmos.write_agent_trace`` must never change the review method's
    return value or escape); the ``enabled_types`` toggle suppressing
    Supervisor/Alpha traces exactly like the primary phase (design §6e);
    and zero trace writes across all three named skip paths (design §4)
    end to end through the real orchestration methods.
  * The three skip-path tests deliberately reuse
    ``test_force_alpha_execution.py``'s already-passing
    ``_symbol_runner_fixture`` / ``_monitor_runner_fixture`` (Linus's
    fixtures, which independently drive the REAL
    ``run_symbol_agent`` / ``run_position_monitor`` orchestration) rather
    than reimplementing ~150 lines of monkeypatching from scratch --
    reimplementing it independently would itself be a source of new,
    unreviewed fixture bugs. This is disclosed reuse of test
    *infrastructure* for a *different* assertion axis (trace-write
    call-counting, which those tests do not check), not a mutual fake
    that could hide a defect: the ``_record_trace`` spy installed below
    is layered on *top* of the existing fixture via a second
    ``monkeypatch.setattr`` call, so it observes real call sites, not a
    fabricated result.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

# Other test modules may install a deliberately small agent_framework stub;
# reuse it if already present (mirrors the existing test files' pattern).
_af = sys.modules.get("agent_framework")
if _af is not None and not hasattr(_af, "SkillsProvider"):
    _af.SkillsProvider = object
    if "agent_framework.openai" not in sys.modules:
        openai_stub = types.ModuleType("agent_framework.openai")
        openai_stub.OpenAIChatCompletionClient = object
        sys.modules["agent_framework.openai"] = openai_stub

from src import agent_runner as ar_mod  # noqa: E402
from src.agent_runner import AgentRunner  # noqa: E402
from src.llm import LlmConfig  # noqa: E402


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_runner() -> AgentRunner:
    return AgentRunner(
        llm=LlmConfig(provider="azure", api_key="k", endpoint="https://example.test"),
        model="default-test-deployment",
    )


def _activity_payload(activity: str = "SELL") -> dict:
    return {
        "symbol": "MSFT",
        "activity": activity,
        "exchange": "NASDAQ",
        "summary": "Sell the 420 call for 3.20.",
    }


class _FakeAgentResponse:
    def __init__(self, text):
        self.text = text


def _agent_factory(*, text=None, exc=None):
    """Build a fake ``Agent`` class whose ``.run()`` either returns
    ``text`` (wrapped) or raises ``exc`` -- scripted per-test, never
    shared mutable state across tests."""

    class _ScriptedAgent:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, prompt):
            if exc is not None:
                raise exc
            return _FakeAgentResponse(text)

    return _ScriptedAgent


class _TraceCosmos:
    """Minimal fake Cosmos: records every trace doc verbatim, optionally
    simulates a per-agent-type enabled_types toggle or a write failure."""

    def __init__(self, *, enabled_types=None, raise_on_write=False):
        self.traces: list[dict] = []
        self._enabled_types = enabled_types or {}
        self._raise_on_write = raise_on_write

    def get_settings(self):
        return {"agent_trace": {"enabled_types": self._enabled_types}}

    def write_agent_trace(self, trace: dict):
        if self._raise_on_write:
            raise RuntimeError("simulated Cosmos write failure")
        self.traces.append(dict(trace))
        return {"id": trace.get("id", "generated-id")}


def _patch_client(monkeypatch, runner):
    monkeypatch.setattr(runner, "_get_client", lambda model=None, function_id=None: object())


# ===========================================================================
# Section A -- full-field capture on success (supervisor + alpha)
# ===========================================================================

_VALID_SUPERVISOR_JSON = """```json
{
  "challenge_strength": "MODERATE",
  "counter_arguments": ["Position is already near max profit."],
  "net_assessment": "Reasonable decision given current volatility.",
  "one_liner": "Solid, defensible trade."
}
```"""

_VALID_ALPHA_JSON = """```json
{
  "opportunity_strength": "STRONG",
  "alternative": {"action": "Sell the further OTM 425 call instead."},
  "relaxed_parameter": "delta_band",
  "parameter_detail": "widened from 0.30 to 0.35",
  "one_liner": "Bigger premium available further out."
}
```"""


def test_supervisor_success_records_full_trace_fields(monkeypatch):
    runner = _make_runner()
    _patch_client(monkeypatch, runner)
    monkeypatch.setattr(ar_mod, "Agent", _agent_factory(text=_VALID_SUPERVISOR_JSON))
    cosmos = _TraceCosmos()

    result = _run(runner._run_supervisor_review(
        activity_payload=_activity_payload(), market_data="=== MARKET ===",
        previous_context="No previous activity.", agent_type="covered_call",
        model=None, cosmos=cosmos, run_id="run-abc-123", parent_trace_id="parent-xyz",
    ))

    assert result is not None
    assert result["challenge_strength"] == "MODERATE"
    assert len(cosmos.traces) == 1
    trace = cosmos.traces[0]
    assert trace["phase"] == "supervisor"
    assert trace["agent_type"] == "covered_call"
    assert trace["system_prompt"]
    assert "MSFT" in trace["user_message"]
    assert trace["response_text"] == _VALID_SUPERVISOR_JSON
    assert trace["parsed"]["challenge_strength"] == "MODERATE"
    assert trace["error"] is None
    assert trace["model"] == "default-test-deployment"
    assert trace["run_id"] == "run-abc-123"
    assert trace["parent_trace_id"] == "parent-xyz"
    assert isinstance(trace["duration_seconds"], (int, float))


def test_alpha_success_records_full_trace_fields(monkeypatch):
    runner = _make_runner()
    _patch_client(monkeypatch, runner)
    monkeypatch.setattr(ar_mod, "Agent", _agent_factory(text=_VALID_ALPHA_JSON))
    cosmos = _TraceCosmos()

    result = _run(runner._run_alpha_review(
        activity_payload=_activity_payload(), market_data="=== MARKET ===",
        previous_context="No previous activity.", agent_type="cash_secured_put",
        model=None, cosmos=cosmos, run_id="run-def-456", parent_trace_id="parent-uvw",
    ))

    assert result is not None
    assert result["opportunity_strength"] == "STRONG"
    assert len(cosmos.traces) == 1
    trace = cosmos.traces[0]
    assert trace["phase"] == "alpha"
    assert trace["agent_type"] == "cash_secured_put"
    assert trace["system_prompt"]
    assert "MSFT" in trace["user_message"]
    assert trace["response_text"] == _VALID_ALPHA_JSON
    assert trace["parsed"]["opportunity_strength"] == "STRONG"
    assert trace["error"] is None
    assert trace["model"] == "default-test-deployment"
    assert trace["run_id"] == "run-def-456"
    assert trace["parent_trace_id"] == "parent-uvw"
    assert isinstance(trace["duration_seconds"], (int, float))


@pytest.mark.parametrize("monitor_type,expected_unmapped", [
    ("open_call_monitor", "open_call_monitor"),
    ("open_put_monitor", "open_put_monitor"),
])
def test_supervisor_trace_records_unmapped_agent_type_for_monitor_variants(
    monkeypatch, monitor_type, expected_unmapped,
):
    """§1: `_AGENT_TYPE_MAP` remaps `open_call_monitor`/`open_put_monitor`
    to select instructions, but the trace's own `agent_type` must remain
    the original, unmapped value so it lines up with the `enabled_types`
    toggle and the primary-phase trace for the same decision."""
    runner = _make_runner()
    _patch_client(monkeypatch, runner)
    monkeypatch.setattr(ar_mod, "Agent", _agent_factory(text=_VALID_SUPERVISOR_JSON))
    cosmos = _TraceCosmos()

    _run(runner._run_supervisor_review(
        activity_payload=_activity_payload(), market_data="", previous_context="",
        agent_type=monitor_type, model=None, cosmos=cosmos, run_id="r1",
    ))

    assert cosmos.traces[0]["agent_type"] == expected_unmapped


@pytest.mark.parametrize("monitor_type,expected_unmapped", [
    ("open_call_monitor", "open_call_monitor"),
    ("open_put_monitor", "open_put_monitor"),
])
def test_alpha_trace_records_unmapped_agent_type_for_monitor_variants(
    monkeypatch, monitor_type, expected_unmapped,
):
    runner = _make_runner()
    _patch_client(monkeypatch, runner)
    monkeypatch.setattr(ar_mod, "Agent", _agent_factory(text=_VALID_ALPHA_JSON))
    cosmos = _TraceCosmos()

    _run(runner._run_alpha_review(
        activity_payload=_activity_payload(), market_data="", previous_context="",
        agent_type=monitor_type, model=None, cosmos=cosmos, run_id="r1",
    ))

    assert cosmos.traces[0]["agent_type"] == expected_unmapped


# ===========================================================================
# Section B -- enumerated `error` strings (design §3)
# ===========================================================================

def test_supervisor_no_parseable_json_writes_specific_error_and_returns_none(monkeypatch):
    runner = _make_runner()
    _patch_client(monkeypatch, runner)
    garbled = "The supervisor thinks this trade looks fine. No structured data here."
    monkeypatch.setattr(ar_mod, "Agent", _agent_factory(text=garbled))
    cosmos = _TraceCosmos()

    result = _run(runner._run_supervisor_review(
        activity_payload=_activity_payload(), market_data="", previous_context="",
        agent_type="covered_call", cosmos=cosmos, run_id="r1",
    ))

    assert result is None
    trace = cosmos.traces[0]
    assert trace["error"] == "no_parseable_json"
    assert trace["parsed"] is None
    assert trace["response_text"] == garbled


def test_alpha_no_parseable_json_writes_specific_error_and_returns_none(monkeypatch):
    runner = _make_runner()
    _patch_client(monkeypatch, runner)
    garbled = "Alpha model output was truncated mid-sentence, no structured fields."
    monkeypatch.setattr(ar_mod, "Agent", _agent_factory(text=garbled))
    cosmos = _TraceCosmos()

    result = _run(runner._run_alpha_review(
        activity_payload=_activity_payload(), market_data="", previous_context="",
        agent_type="cash_secured_put", cosmos=cosmos, run_id="r1",
    ))

    assert result is None
    trace = cosmos.traces[0]
    assert trace["error"] == "no_parseable_json"
    assert trace["parsed"] is None
    assert trace["response_text"] == garbled


def test_supervisor_missing_required_field_writes_specific_error(monkeypatch):
    runner = _make_runner()
    _patch_client(monkeypatch, runner)
    # counter_arguments is missing -- the only genuinely-required field
    # supervisor never backfills.
    text = """```json
{"challenge_strength": "WEAK", "net_assessment": "Looks fine."}
```"""
    monkeypatch.setattr(ar_mod, "Agent", _agent_factory(text=text))
    cosmos = _TraceCosmos()

    result = _run(runner._run_supervisor_review(
        activity_payload=_activity_payload(), market_data="", previous_context="",
        agent_type="covered_call", cosmos=cosmos, run_id="r1",
    ))

    assert result is None
    assert cosmos.traces[0]["error"] == "missing_required_fields:['counter_arguments']"


def test_alpha_missing_required_field_writes_specific_error(monkeypatch):
    """`relaxed_parameter` is silently backfilled (backwards-compat) so it
    must NOT appear in the reported missing set -- ``alternative`` is used
    instead as a field with no backfill."""
    runner = _make_runner()
    _patch_client(monkeypatch, runner)
    text = """```json
{"opportunity_strength": "STRONG", "relaxed_parameter": "delta_band"}
```"""
    monkeypatch.setattr(ar_mod, "Agent", _agent_factory(text=text))
    cosmos = _TraceCosmos()

    result = _run(runner._run_alpha_review(
        activity_payload=_activity_payload(), market_data="", previous_context="",
        agent_type="cash_secured_put", cosmos=cosmos, run_id="r1",
    ))

    assert result is None
    assert cosmos.traces[0]["error"] == "missing_required_fields:['alternative']"


def test_supervisor_invalid_challenge_strength_writes_specific_error(monkeypatch):
    runner = _make_runner()
    _patch_client(monkeypatch, runner)
    text = """```json
{"challenge_strength": "bogus", "counter_arguments": [], "net_assessment": "x"}
```"""
    monkeypatch.setattr(ar_mod, "Agent", _agent_factory(text=text))
    cosmos = _TraceCosmos()

    result = _run(runner._run_supervisor_review(
        activity_payload=_activity_payload(), market_data="", previous_context="",
        agent_type="covered_call", cosmos=cosmos, run_id="r1",
    ))

    assert result is None
    assert cosmos.traces[0]["error"] == "invalid_challenge_strength:BOGUS"


def test_alpha_invalid_opportunity_strength_writes_specific_error(monkeypatch):
    runner = _make_runner()
    _patch_client(monkeypatch, runner)
    text = """```json
{"opportunity_strength": "bogus", "alternative": {"action": "x"}, "relaxed_parameter": "none"}
```"""
    monkeypatch.setattr(ar_mod, "Agent", _agent_factory(text=text))
    cosmos = _TraceCosmos()

    result = _run(runner._run_alpha_review(
        activity_payload=_activity_payload(), market_data="", previous_context="",
        agent_type="cash_secured_put", cosmos=cosmos, run_id="r1",
    ))

    assert result is None
    assert cosmos.traces[0]["error"] == "invalid_opportunity_strength:BOGUS"


def test_supervisor_exception_during_agent_run_writes_exception_trace(monkeypatch):
    runner = _make_runner()
    _patch_client(monkeypatch, runner)
    monkeypatch.setattr(ar_mod, "Agent", _agent_factory(exc=RuntimeError("boom")))
    cosmos = _TraceCosmos()

    result = _run(runner._run_supervisor_review(
        activity_payload=_activity_payload(), market_data="", previous_context="",
        agent_type="covered_call", cosmos=cosmos, run_id="r1",
    ))

    assert result is None
    trace = cosmos.traces[0]
    assert trace["error"] == "RuntimeError: boom"
    assert trace["response_text"] is None
    # Instructions/message were built before `agent.run()` raised.
    assert trace["system_prompt"]


def test_alpha_exception_during_agent_run_writes_exception_trace(monkeypatch):
    runner = _make_runner()
    _patch_client(monkeypatch, runner)
    monkeypatch.setattr(ar_mod, "Agent", _agent_factory(exc=ValueError("bad input")))
    cosmos = _TraceCosmos()

    result = _run(runner._run_alpha_review(
        activity_payload=_activity_payload(), market_data="", previous_context="",
        agent_type="cash_secured_put", cosmos=cosmos, run_id="r1",
    ))

    assert result is None
    trace = cosmos.traces[0]
    assert trace["error"] == "ValueError: bad input"
    assert trace["response_text"] is None
    assert trace["system_prompt"]


def test_supervisor_crash_before_agent_construction_still_writes_all_none_trace(monkeypatch):
    """§3: instructions/message/response_text must be initialized to None
    *before* the try: block, so a crash before `agent.run()` is ever
    reached (e.g. instructions lookup itself raises) still produces a
    safe trace instead of a NameError inside the `finally` block."""
    runner = _make_runner()
    _patch_client(monkeypatch, runner)

    def _boom(*a, **k):
        raise KeyError("no instructions for this agent_type")

    monkeypatch.setattr(ar_mod, "get_supervisor_instructions", _boom)
    monkeypatch.setattr(ar_mod, "Agent", _agent_factory(text=_VALID_SUPERVISOR_JSON))
    cosmos = _TraceCosmos()

    result = _run(runner._run_supervisor_review(
        activity_payload=_activity_payload(), market_data="", previous_context="",
        agent_type="covered_call", cosmos=cosmos, run_id="r1",
    ))

    assert result is None
    trace = cosmos.traces[0]
    assert trace["system_prompt"] is None
    assert trace["user_message"] is None
    assert trace["response_text"] is None
    assert trace["error"]


def test_alpha_crash_before_agent_construction_still_writes_all_none_trace(monkeypatch):
    runner = _make_runner()
    _patch_client(monkeypatch, runner)

    def _boom(*a, **k):
        raise KeyError("no instructions for this agent_type")

    monkeypatch.setattr(ar_mod, "get_alpha_instructions", _boom)
    monkeypatch.setattr(ar_mod, "Agent", _agent_factory(text=_VALID_ALPHA_JSON))
    cosmos = _TraceCosmos()

    result = _run(runner._run_alpha_review(
        activity_payload=_activity_payload(), market_data="", previous_context="",
        agent_type="cash_secured_put", cosmos=cosmos, run_id="r1",
    ))

    assert result is None
    trace = cosmos.traces[0]
    assert trace["system_prompt"] is None
    assert trace["user_message"] is None
    assert trace["response_text"] is None
    assert trace["error"]


# ===========================================================================
# Section C -- model-completeness fix (design §3)
# ===========================================================================

def test_supervisor_records_resolved_default_model_when_none_passed(monkeypatch):
    runner = _make_runner()
    _patch_client(monkeypatch, runner)
    monkeypatch.setattr(ar_mod, "Agent", _agent_factory(text=_VALID_SUPERVISOR_JSON))
    cosmos = _TraceCosmos()

    _run(runner._run_supervisor_review(
        activity_payload=_activity_payload(), market_data="", previous_context="",
        agent_type="covered_call", model=None, cosmos=cosmos, run_id="r1",
    ))

    assert cosmos.traces[0]["model"] == "default-test-deployment"


def test_alpha_records_explicit_model_override_verbatim(monkeypatch):
    runner = _make_runner()
    _patch_client(monkeypatch, runner)
    monkeypatch.setattr(ar_mod, "Agent", _agent_factory(text=_VALID_ALPHA_JSON))
    cosmos = _TraceCosmos()

    _run(runner._run_alpha_review(
        activity_payload=_activity_payload(), market_data="", previous_context="",
        agent_type="cash_secured_put", model="explicit-override-deploy",
        cosmos=cosmos, run_id="r1",
    ))

    assert cosmos.traces[0]["model"] == "explicit-override-deploy"


# ===========================================================================
# Section D -- tracing must never affect the decision (design §8)
# ===========================================================================

def test_supervisor_return_value_unaffected_when_trace_write_raises(monkeypatch):
    runner = _make_runner()
    _patch_client(monkeypatch, runner)
    monkeypatch.setattr(ar_mod, "Agent", _agent_factory(text=_VALID_SUPERVISOR_JSON))
    cosmos = _TraceCosmos(raise_on_write=True)

    result = _run(runner._run_supervisor_review(
        activity_payload=_activity_payload(), market_data="", previous_context="",
        agent_type="covered_call", cosmos=cosmos, run_id="r1",
    ))

    # A raising `write_agent_trace` must not surface as an exception here,
    # nor change the parsed result the primary decision flow depends on.
    assert result is not None
    assert result["challenge_strength"] == "MODERATE"


def test_alpha_return_value_unaffected_when_trace_write_raises(monkeypatch):
    runner = _make_runner()
    _patch_client(monkeypatch, runner)
    monkeypatch.setattr(ar_mod, "Agent", _agent_factory(text=_VALID_ALPHA_JSON))
    cosmos = _TraceCosmos(raise_on_write=True)

    result = _run(runner._run_alpha_review(
        activity_payload=_activity_payload(), market_data="", previous_context="",
        agent_type="cash_secured_put", cosmos=cosmos, run_id="r1",
    ))

    assert result is not None
    assert result["opportunity_strength"] == "STRONG"


# ===========================================================================
# Section E -- `enabled_types` toggle suppresses Supervisor/Alpha traces
# (design §6e) without suppressing the review itself
# ===========================================================================

def test_supervisor_trace_write_suppressed_when_agent_type_disabled(monkeypatch):
    runner = _make_runner()
    _patch_client(monkeypatch, runner)
    monkeypatch.setattr(ar_mod, "Agent", _agent_factory(text=_VALID_SUPERVISOR_JSON))
    cosmos = _TraceCosmos(enabled_types={"covered_call": False})

    result = _run(runner._run_supervisor_review(
        activity_payload=_activity_payload(), market_data="", previous_context="",
        agent_type="covered_call", cosmos=cosmos, run_id="r1",
    ))

    # Disabling capture must not disable the review itself.
    assert result is not None
    assert cosmos.traces == []


def test_alpha_trace_write_suppressed_when_agent_type_disabled(monkeypatch):
    runner = _make_runner()
    _patch_client(monkeypatch, runner)
    monkeypatch.setattr(ar_mod, "Agent", _agent_factory(text=_VALID_ALPHA_JSON))
    cosmos = _TraceCosmos(enabled_types={"cash_secured_put": False})

    result = _run(runner._run_alpha_review(
        activity_payload=_activity_payload(), market_data="", previous_context="",
        agent_type="cash_secured_put", cosmos=cosmos, run_id="r1",
    ))

    assert result is not None
    assert cosmos.traces == []


# ===========================================================================
# Section F -- zero trace writes for the three named skip paths (design §4)
# ===========================================================================
#
# These reuse `test_force_alpha_execution.py`'s already-passing, real
# `run_symbol_agent` / `run_position_monitor` orchestration fixtures
# (Linus's), then layer a second `_record_trace` spy on top via
# `monkeypatch.setattr` to observe phase-tagged calls -- a different
# assertion axis than what that file's own tests check (call counts on
# the review methods, not trace-write occurrence). See module docstring.

from tests.test_force_alpha_execution import (  # noqa: E402
    _symbol_runner_fixture,
    _run_symbol_agent,
    _monitor_runner_fixture,
    _run_position_monitor,
    _FakeFetcher,
    _market_data_for_monitor,
)


def _install_trace_spy(monkeypatch, runner):
    calls = []

    def spy(cosmos_arg, **kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(runner, "_record_trace", spy)
    return calls


def test_buy_tracker_skip_writes_zero_supervisor_or_alpha_traces(monkeypatch):
    """buy_tracker: Supervisor and Alpha never run at all -- no trace for
    either phase, even though `force_alpha=True` is passed."""
    runner, cosmos, notifier, state = _symbol_runner_fixture(
        monkeypatch, activity="WAIT", agent_type="buy_tracker",
    )
    calls = _install_trace_spy(monkeypatch, runner)
    _run_symbol_agent(runner, cosmos, agent_type="buy_tracker",
                       run_trigger="manual", force_alpha=True)

    phases = [c.get("phase") for c in calls]
    assert "supervisor" not in phases
    assert "alpha" not in phases
    assert "analysis" in phases  # the primary phase still traces normally


def test_calm_wait_non_forced_skip_writes_zero_alpha_trace_but_supervisor_traces(monkeypatch):
    """Calm WAIT, non-forced: Supervisor runs alone; Alpha is never called
    and must have zero trace writes."""
    runner, cosmos, notifier, state = _symbol_runner_fixture(monkeypatch, activity="WAIT")
    calls = _install_trace_spy(monkeypatch, runner)
    _run_symbol_agent(runner, cosmos, force_alpha=False)

    phases = [c.get("phase") for c in calls]
    assert "alpha" not in phases
    assert "analysis" in phases


def test_incomplete_quote_wait_skip_writes_zero_alpha_trace(monkeypatch):
    """incomplete_quote_wait wins over forcing -- Alpha is never called
    against a quote already known to be incomplete, so it has zero trace
    writes even though `force_alpha=True` is passed."""
    runner, cosmos, notifier, state = _monitor_runner_fixture(monkeypatch)
    calls = _install_trace_spy(monkeypatch, runner)
    _run_position_monitor(
        runner, cosmos, _FakeFetcher(_market_data_for_monitor(bad_ask=True)),
        run_trigger="manual", force_alpha=True,
    )

    phases = [c.get("phase") for c in calls]
    assert "alpha" not in phases
