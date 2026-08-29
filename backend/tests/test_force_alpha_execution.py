"""Runner/domain tests for explicit run_trigger + force_alpha semantics.

Covers §11 cases 1-14 of `.squad/decisions/inbox/danny-force-alpha-design.md`
(the backend gate-semantics and cooldown-neutrality cases owned by
Linus/runner-domain; cases 15-25 are API-layer (Rusty) and case 26 is the
seam/integration test (Livingston), both out of this file's scope per §13).

Two independent surfaces are exercised:
  * `AgentRunner._detect_prolonged_wait` directly, with a minimal fake
    Cosmos, for the H1 cooldown-neutrality fix (cases 12-14).
  * `AgentRunner.run_symbol_agent` / `run_position_monitor` end to end,
    faking only the network/LLM boundary — the same style used by
    `test_buy_tracker_normalization.py` and `test_open_call_zero_quote.py`
    — for the gate-formula and `alpha_run` persistence cases (1-11).
"""

from __future__ import annotations

import asyncio
import copy
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


def _make_runner(notifier=None) -> AgentRunner:
    return AgentRunner(
        llm=LlmConfig(provider="azure", api_key="k", endpoint="https://example.test"),
        model="test-model",
        telegram_notifier=notifier,
    )


# ===========================================================================
# Section A -- `_detect_prolonged_wait` cooldown neutrality (H1), cases 12-14
# ===========================================================================

class _RecentActivitiesCosmos:
    """Minimal fake Cosmos exposing only `get_recent_activities`."""

    def __init__(self, activities):
        self._activities = activities

    def get_recent_activities(self, max_entries=5, **kwargs):
        return copy.deepcopy(self._activities[:max_entries])


def _five_wait_activities(*, last_alpha_run=None):
    """Five most-recent activities, all plain WAITs (satisfies the
    threshold check). Index 0 is most recent (mirrors
    `get_recent_activities`'s newest-first ordering) and carries
    `alpha_view` (+ optionally `alpha_run`) so the cooldown scan has
    something to evaluate immediately."""
    reviewed = {"activity": "WAIT", "is_alert": False, "alpha_view": {"opportunity_strength": "NONE"}}
    if last_alpha_run is not None:
        reviewed["alpha_run"] = last_alpha_run
    activities = [reviewed] + [{"activity": "WAIT", "is_alert": False} for _ in range(4)]
    return activities


def test_case12_forced_alpha_review_does_not_consume_cooldown():
    """A forced Alpha review (`alpha_run.forced=True`) must not itself
    satisfy the cooldown -- prolonged-WAIT detection must still fire."""
    runner = _make_runner()
    cosmos = _RecentActivitiesCosmos(
        _five_wait_activities(last_alpha_run={"trigger": "manual", "forced": True, "status": "ok"})
    )
    runner.SUPERVISOR_COOLDOWN = 3
    assert runner._detect_prolonged_wait(cosmos, "MSFT", "covered_call", threshold=5) is True


def test_case13_due_alpha_review_consumes_cooldown_as_before():
    """A due (non-forced) Alpha review still resets the cooldown exactly
    like today -- the historical behaviour must be unchanged."""
    runner = _make_runner()
    cosmos = _RecentActivitiesCosmos(
        _five_wait_activities(last_alpha_run={"trigger": "scheduled", "forced": False, "status": "ok"})
    )
    runner.SUPERVISOR_COOLDOWN = 3
    assert runner._detect_prolonged_wait(cosmos, "MSFT", "covered_call", threshold=5) is False


def test_case14_legacy_alpha_view_without_alpha_run_is_treated_as_not_forced():
    """A legacy activity predating this feature has `alpha_view` but no
    `alpha_run` field at all -- it must be treated conservatively as a due
    review (not forced), preserving the old cooldown-consuming behaviour."""
    runner = _make_runner()
    cosmos = _RecentActivitiesCosmos(_five_wait_activities(last_alpha_run=None))
    runner.SUPERVISOR_COOLDOWN = 3
    assert runner._detect_prolonged_wait(cosmos, "MSFT", "covered_call", threshold=5) is False


# ===========================================================================
# Section B -- `run_symbol_agent` gate semantics, cases 1-3, 5-7, 9-10
# ===========================================================================

class _FakeAgentResponse:
    def __init__(self, text):
        self.text = text


class _FakeAgent:
    def __init__(self, *args, **kwargs):
        pass

    async def run(self, prompt):
        return _FakeAgentResponse("raw model response")


class _FakeContext:
    def get_context(self, *args, **kwargs):
        return "No previous activity."


class _FakeFetcher:
    def __init__(self, market_data):
        self.market_data = market_data

    async def fetch_all(self, *args, **kwargs):
        return copy.deepcopy(self.market_data)


class _SymbolCosmos:
    def __init__(self):
        self.activities = []

    def write_activity(self, **kwargs):
        payload = copy.deepcopy(kwargs["activity_data"])
        payload["id"] = f"activity-{len(self.activities) + 1}"
        self.activities.insert(0, payload)
        return payload

    def update_activity_field(self, **kwargs):
        if self.activities:
            self.activities[0][kwargs["field"]] = copy.deepcopy(kwargs["value"])

    def get_recent_activities(self, max_entries=5, **kwargs):
        return []

    def write_telemetry(self, *args, **kwargs):
        pass


class _Notifier:
    def __init__(self):
        self.alerts = []
        self.prolonged_wait_alerts = []

    def send_alert(self, **kwargs):
        self.alerts.append(copy.deepcopy(kwargs))

    def send_prolonged_wait_alert(self, **kwargs):
        self.prolonged_wait_alerts.append(copy.deepcopy(kwargs))


def _valid_market_data() -> dict:
    return {
        "overview": "{}",
        "technicals": "{}",
        "forecast": "{}",
        "dividends": "{}",
        "options_chain": "{}",
    }


def _symbol_runner_fixture(
    monkeypatch,
    *,
    activity="WAIT",
    agent_type="covered_call",
    alpha_result=None,
    alpha_raises=None,
    prolonged_wait=False,
):
    """Build an AgentRunner wired for `run_symbol_agent`, faking only the
    LLM boundary and the two review sub-methods, so the real gate formula
    (is_alert / prolonged_wait / force_alpha) and the real persistence
    calls are exercised."""
    state = {"alpha_calls": 0, "supervisor_calls": 0, "traces": []}
    notifier = _Notifier()
    cosmos = _SymbolCosmos()
    runner = _make_runner(notifier)

    async def fake_supervisor(**kwargs):
        state["supervisor_calls"] += 1
        return None

    async def fake_alpha(**kwargs):
        state["alpha_calls"] += 1
        if alpha_raises is not None:
            raise alpha_raises
        return alpha_result

    def fake_record_trace(cosmos_arg, **kwargs):
        state["traces"].append(kwargs)

    monkeypatch.setattr(ar_mod, "Agent", _FakeAgent)
    monkeypatch.setattr(
        ar_mod,
        "build_rule_evaluation",
        lambda agent_type, activity_data, **k: {
            "schema_version": 1, "agent_type": agent_type, "rules": [],
        },
    )
    monkeypatch.setattr(runner, "_get_client", lambda model=None, function_id=None: object())
    monkeypatch.setattr(runner, "_build_enrichment_block", lambda *a, **k: "")
    monkeypatch.setattr(runner, "_resolve_category_skill", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_get_skills_provider", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_format_options_chain", lambda *a, **k: "")
    monkeypatch.setattr(runner, "_volatility_text", lambda *a, **k: "")
    monkeypatch.setattr(
        runner,
        "_extract_activity_line",
        lambda symbol, response_text: (
            f"SUMMARY: {symbol} | {activity}",
            {"activity": activity, "symbol": symbol, "exchange": "NASDAQ"},
        ),
    )
    monkeypatch.setattr(
        runner, "_validate_premium_against_chain", lambda activity_data, *a, **k: activity_data,
    )
    monkeypatch.setattr(runner, "_build_alpha_options_chain", lambda *a, **k: "")
    monkeypatch.setattr(runner, "_build_market_data_block", lambda *a, **k: "")
    monkeypatch.setattr(runner, "_run_supervisor_review", fake_supervisor)
    monkeypatch.setattr(runner, "_run_alpha_review", fake_alpha)
    monkeypatch.setattr(runner, "_detect_prolonged_wait", lambda *a, **k: prolonged_wait)
    monkeypatch.setattr(runner, "_record_trace", fake_record_trace)

    return runner, cosmos, notifier, state


def _run_symbol_agent(runner, cosmos, *, agent_type="covered_call", run_trigger="scheduled", force_alpha=False):
    _run(
        runner.run_symbol_agent(
            name="CoveredCallAgent",
            instructions="test instructions",
            symbol="MSFT",
            exchange="NASDAQ",
            agent_type=agent_type,
            cosmos=cosmos,
            context_provider=_FakeContext(),
            fetcher=_FakeFetcher(_valid_market_data()),
            model="configured-model",
            run_trigger=run_trigger,
            force_alpha=force_alpha,
        )
    )


def test_case1_calm_wait_force_alpha_false_alpha_not_called(monkeypatch):
    """Regression lock: today's default (scheduled cron) behaviour is
    completely unaffected by this feature when force_alpha is left False."""
    runner, cosmos, notifier, state = _symbol_runner_fixture(monkeypatch, activity="WAIT")
    _run_symbol_agent(runner, cosmos, force_alpha=False)
    assert state["alpha_calls"] == 0
    assert state["supervisor_calls"] == 1
    activity = cosmos.activities[0]
    assert "alpha_view" not in activity
    assert "alpha_run" not in activity


def test_case2_calm_wait_force_alpha_true_calls_alpha_and_supervisor_once(monkeypatch):
    """Forcing on an otherwise-calm WAIT runs both reviews exactly once,
    concurrently, and records a forced `alpha_run`."""
    runner, cosmos, notifier, state = _symbol_runner_fixture(
        monkeypatch, activity="WAIT", alpha_result={"opportunity_strength": "MODERATE", "one_liner": "x"},
    )
    _run_symbol_agent(runner, cosmos, run_trigger="manual", force_alpha=True)
    assert state["alpha_calls"] == 1
    assert state["supervisor_calls"] == 1
    activity = cosmos.activities[0]
    assert activity["alpha_view"]["opportunity_strength"] == "MODERATE"
    assert activity["alpha_run"] == {"trigger": "manual", "forced": True, "status": "ok"}


def test_case3_alert_force_alpha_true_calls_alpha_exactly_once(monkeypatch):
    """An alert already runs Alpha on its own merits -- forcing must not
    cause a second, duplicate gather."""
    runner, cosmos, notifier, state = _symbol_runner_fixture(
        monkeypatch, activity="SELL", alpha_result={"opportunity_strength": "NONE", "one_liner": "x"},
    )
    _run_symbol_agent(runner, cosmos, run_trigger="manual", force_alpha=True)
    assert state["alpha_calls"] == 1
    assert state["supervisor_calls"] == 1
    activity = cosmos.activities[0]
    # Not "forced" for provenance -- it ran because it was an alert.
    assert activity["alpha_run"] == {"trigger": "manual", "forced": False, "status": "ok"}


def test_case5_forced_alpha_never_sets_prolonged_wait_no_telegram_send(monkeypatch):
    """force_alpha=True must never itself trigger `send_prolonged_wait_alert`,
    even when Alpha returns a STRONG finding -- assert on the notifier mock."""
    runner, cosmos, notifier, state = _symbol_runner_fixture(
        monkeypatch, activity="WAIT",
        alpha_result={"opportunity_strength": "STRONG", "one_liner": "big opportunity"},
        prolonged_wait=False,
    )
    _run_symbol_agent(runner, cosmos, run_trigger="manual", force_alpha=True)
    assert state["alpha_calls"] == 1
    assert notifier.prolonged_wait_alerts == []


def test_case6_forced_alpha_never_sets_is_alert_no_telegram_send(monkeypatch):
    """force_alpha=True on a calm WAIT must never trigger `send_alert` --
    only a genuine alert activity does that."""
    runner, cosmos, notifier, state = _symbol_runner_fixture(
        monkeypatch, activity="WAIT",
        alpha_result={"opportunity_strength": "STRONG", "one_liner": "big opportunity"},
    )
    _run_symbol_agent(runner, cosmos, run_trigger="manual", force_alpha=True)
    assert notifier.alerts == []


def test_case7_buy_tracker_force_alpha_true_alpha_not_called(monkeypatch):
    """buy_tracker has no Alpha playbook -- forcing is inert, not an error,
    and the skip is recorded (Linus's chosen minimal shape: a status field,
    not a silent no-op) so the guarantee is falsifiable."""
    runner, cosmos, notifier, state = _symbol_runner_fixture(
        monkeypatch, activity="WAIT", agent_type="buy_tracker",
    )
    _run_symbol_agent(runner, cosmos, agent_type="buy_tracker", run_trigger="manual", force_alpha=True)
    assert state["alpha_calls"] == 0
    assert state["supervisor_calls"] == 0
    activity = cosmos.activities[0]
    assert activity["alpha_run"] == {"trigger": "manual", "forced": True, "status": "skipped_agent_type"}


def test_case9_alpha_returns_none_under_forcing_activity_still_written(monkeypatch):
    """`_run_alpha_review` returning None (its designed failure signal)
    under forcing must not lose the primary decision or the supervisor
    view, and must record `alpha_run.status == "failed"`."""
    runner, cosmos, notifier, state = _symbol_runner_fixture(
        monkeypatch, activity="WAIT", alpha_result=None,
    )
    _run_symbol_agent(runner, cosmos, run_trigger="manual", force_alpha=True)
    activity = cosmos.activities[0]
    assert activity["activity"] == "WAIT"
    assert "alpha_view" not in activity
    assert activity["alpha_run"] == {"trigger": "manual", "forced": True, "status": "failed"}


def test_case10_alpha_raises_under_forcing_primary_decision_survives(monkeypatch):
    """`_run_alpha_review` is documented to never raise (it catches
    everything internally and returns None) -- if it somehow did, that
    exception must not escape `run_symbol_agent` or block persistence."""
    runner, cosmos, notifier, state = _symbol_runner_fixture(
        monkeypatch, activity="WAIT", alpha_raises=RuntimeError("boom"),
    )
    _run_symbol_agent(runner, cosmos, run_trigger="manual", force_alpha=True)
    # The primary WAIT decision was already durably written *before* the
    # supervisor/alpha gather ran, so it survives untouched even though the
    # (pre-existing, unrelated to force_alpha) outer try/except appends a
    # second "error" activity for the gather failure itself. The call must
    # complete without the exception escaping `run_symbol_agent`.
    assert len(cosmos.activities) == 2
    assert cosmos.activities[-1]["activity"] == "WAIT"
    assert cosmos.activities[0]["error"] == "boom"


# ===========================================================================
# Section C -- `run_position_monitor` gate semantics, cases 2, 5, 8, 9, 11
# ===========================================================================

EXPIRATION = "2026-09-18"


class _MonitorCosmos:
    def __init__(self):
        self.activities = []

    def write_activity(self, **kwargs):
        payload = copy.deepcopy(kwargs["activity_data"])
        payload["id"] = f"activity-{len(self.activities) + 1}"
        self.activities.insert(0, payload)
        return payload

    def get_recent_activities(self, max_entries=5, **kwargs):
        return copy.deepcopy(self.activities[:max_entries])

    def get_position_snapshots(self, *args, **kwargs):
        return []

    def update_activity_field(self, **kwargs):
        if self.activities:
            self.activities[0][kwargs["field"]] = copy.deepcopy(kwargs["value"])

    def write_position_snapshot(self, *args, **kwargs):
        raise AssertionError("fixture disables snapshot persistence")

    def update_snapshot_dps(self, *args, **kwargs):
        pass

    def write_telemetry(self, *args, **kwargs):
        pass


def _monitor_assessment_handoff() -> dict:
    return {
        "action_needed": None,
        "symbol": "MSFT",
        "exchange": "NASDAQ",
        "current_strike": 420.0,
        "current_expiration": EXPIRATION,
        "underlying_price": 410.0,
        "activity": "WAIT",
        "reason": "Nothing actionable this cycle.",
    }


def _monitor_runner_fixture(
    monkeypatch,
    *,
    alpha_result=None,
    prolonged_wait=False,
    roll_activity=None,
):
    """Build an AgentRunner wired for `run_position_monitor`. Phase 1
    always returns a WAIT-shaped activity (no handoff) unless
    `roll_activity` requests an alert/roll path instead.

    `incomplete_quote_wait` is NOT driven by this fixture's Phase 1 stub --
    `run_position_monitor` derives it independently from the real
    executable-buyback-ask check against the fetcher's options chain
    (`_apply_buyback_quote_state`/`open_call_quote_incomplete`), so tests
    needing that path must instead pass bad-ask market data via
    `_market_data_for_monitor(bad_ask=True)`.
    """
    state = {"alpha_calls": 0, "supervisor_calls": 0, "phase2_calls": 0}
    notifier = _Notifier()
    cosmos = _MonitorCosmos()
    runner = _make_runner(notifier)

    async def fake_assessment(**kwargs):
        if roll_activity is not None:
            return (
                "SUMMARY: MSFT | ROLL_OUT",
                None,
                {
                    "action_needed": "ROLL_OUT",
                    "symbol": "MSFT",
                    "exchange": "NASDAQ",
                    "current_strike": 420.0,
                    "current_expiration": EXPIRATION,
                    "underlying_price": 410.0,
                    "reason": "Rolling out for more premium.",
                },
            )
        return (
            "SUMMARY: MSFT | WAIT",
            _monitor_assessment_handoff(),
            None,
        )

    async def fake_roll_management(**kwargs):
        state["phase2_calls"] += 1
        activity = dict(roll_activity or {})
        activity.setdefault("activity", "ROLL_OUT")
        activity.setdefault("current_strike", 420.0)
        activity.setdefault("current_expiration", EXPIRATION)
        activity.setdefault("underlying_price", 410.0)
        activity.setdefault("reason", "Rolling out for more premium.")
        return "SUMMARY: MSFT | ROLL_OUT open call", activity

    async def fake_supervisor(**kwargs):
        state["supervisor_calls"] += 1
        return None

    async def fake_alpha(**kwargs):
        state["alpha_calls"] += 1
        return alpha_result

    def fake_rule_evaluation(agent_type, activity_data, phase=None, **kwargs):
        return {
            "schema_version": 1,
            "agent_type": agent_type,
            "phases": [{"phase": phase, "rules": []}],
            "summary_counts": {},
            "first_blocker": None,
        }

    monkeypatch.setattr(runner, "_run_position_assessment", fake_assessment)
    monkeypatch.setattr(runner, "_run_roll_management", fake_roll_management)
    monkeypatch.setattr(runner, "_run_supervisor_review", fake_supervisor)
    monkeypatch.setattr(runner, "_run_alpha_review", fake_alpha)
    monkeypatch.setattr(runner, "_detect_prolonged_wait", lambda *a, **k: prolonged_wait)
    monkeypatch.setattr(runner, "_build_position_snapshot_data", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_build_position_context_section", lambda *a, **k: "")
    monkeypatch.setattr(runner, "_build_market_data_block", lambda *a, **k: "")
    monkeypatch.setattr(runner, "_build_alpha_options_chain", lambda *a, **k: "")
    monkeypatch.setattr(runner, "_record_trace", lambda *a, **k: None)
    monkeypatch.setattr(
        runner, "_validate_premium_against_chain", lambda activity_data, *a, **k: activity_data,
    )
    monkeypatch.setattr(ar_mod, "build_rule_evaluation", fake_rule_evaluation)
    monkeypatch.setattr(
        ar_mod,
        "merge_phase_evaluations",
        lambda assessment, roll: {**assessment, "phases": assessment["phases"] + roll["phases"]},
    )
    return runner, cosmos, notifier, state


def _market_data_for_monitor(*, bad_ask=False) -> dict:
    """`bad_ask=True` produces a genuinely non-executable current-contract
    ask (mirrors `test_open_call_zero_quote.py`'s fixture), which is what
    the real pipeline uses to derive `open_call_quote_incomplete` ->
    `incomplete_quote_wait` -- NOT a field on the fake Phase 1 payload."""
    current_contract = {
        "bid": 0.0 if bad_ask else 1.10,
        "ask": 0.0 if bad_ask else 1.25,
        "mid": 0.0 if bad_ask else 1.175,
        "last": 1.15,
        "delta": 0.18, "gamma": 0.01, "theta": -0.04, "iv": 0.24,
    }
    chain = {
        "symbol": "MSFT",
        "timestamp": "2026-08-17T14:00:00Z",
        "calls": {"20260918": {"420.0": current_contract}},
        "puts": {},
    }
    import json as _json
    return {
        "overview": _json.dumps({"fundamentals": {"current_price": {"value": 410.0}}}),
        "technicals": _json.dumps({"price": 410.0, "oscillators": {"indicators": {}}}),
        "forecast": "{}",
        "options_chain": _json.dumps(chain),
    }


def _run_position_monitor(runner, cosmos, fetcher, *, run_trigger="scheduled", force_alpha=False):
    _run(
        runner.run_position_monitor(
            name="OpenCallMonitor",
            symbol="MSFT",
            exchange="NASDAQ",
            position={
                "position_id": "msft-call-420",
                "type": "call",
                "strike": 420.0,
                "expiration": EXPIRATION,
                "source": {"premium": 3.20},
            },
            agent_type="open_call_monitor",
            cosmos=cosmos,
            context_provider=_FakeContext(),
            fetcher=fetcher,
            assessment_instructions="test",
            roll_instructions="test",
            run_trigger=run_trigger,
            force_alpha=force_alpha,
        )
    )


def test_case8_incomplete_quote_wait_force_alpha_true_alpha_skipped(monkeypatch):
    """incomplete_quote_wait must win over forcing -- Alpha is never
    called against a quote already known to be incomplete, and the skip
    is recorded, not silent."""
    runner, cosmos, notifier, state = _monitor_runner_fixture(monkeypatch)
    _run_position_monitor(
        runner, cosmos, _FakeFetcher(_market_data_for_monitor(bad_ask=True)),
        run_trigger="manual", force_alpha=True,
    )
    assert state["alpha_calls"] == 0
    assert state["supervisor_calls"] == 1
    activity = cosmos.activities[0]
    assert activity["alpha_run"] == {
        "trigger": "manual", "forced": True, "status": "skipped_incomplete_quotes",
    }
    assert "alpha_view" not in activity


def test_case2_monitor_calm_wait_force_alpha_true_calls_alpha_once(monkeypatch):
    """Mirrors case 2 for the position-monitor entry point (case 11: both
    entry points must be independently covered)."""
    runner, cosmos, notifier, state = _monitor_runner_fixture(
        monkeypatch,
        alpha_result={"opportunity_strength": "MODERATE", "one_liner": "x"},
    )
    _run_position_monitor(
        runner, cosmos, _FakeFetcher(_market_data_for_monitor()),
        run_trigger="manual", force_alpha=True,
    )
    assert state["alpha_calls"] == 1
    assert state["supervisor_calls"] == 1
    activity = cosmos.activities[0]
    assert activity["alpha_view"]["opportunity_strength"] == "MODERATE"
    assert activity["alpha_run"] == {"trigger": "manual", "forced": True, "status": "ok"}


def test_case5_monitor_forced_alpha_never_sets_prolonged_wait_no_telegram(monkeypatch):
    """Mirrors case 5 for the position-monitor entry point."""
    runner, cosmos, notifier, state = _monitor_runner_fixture(
        monkeypatch,
        alpha_result={"opportunity_strength": "STRONG", "one_liner": "x"},
        prolonged_wait=False,
    )
    _run_position_monitor(
        runner, cosmos, _FakeFetcher(_market_data_for_monitor()),
        run_trigger="manual", force_alpha=True,
    )
    assert notifier.prolonged_wait_alerts == []
    assert notifier.alerts == []


def test_case4_monitor_prolonged_wait_and_force_alpha_alpha_run_not_forced(monkeypatch):
    """A due prolonged-WAIT review that also happens to be forced is
    recorded as due (`forced=False`), so it correctly consumes the
    cooldown -- mirrors design case 4 for the position-monitor path."""
    runner, cosmos, notifier, state = _monitor_runner_fixture(
        monkeypatch,
        alpha_result={"opportunity_strength": "NONE", "one_liner": "x"},
        prolonged_wait=True,
    )
    _run_position_monitor(
        runner, cosmos, _FakeFetcher(_market_data_for_monitor()),
        run_trigger="manual", force_alpha=True,
    )
    assert state["alpha_calls"] == 1
    activity = cosmos.activities[0]
    assert activity["alpha_run"] == {"trigger": "manual", "forced": False, "status": "ok"}


def test_case3_monitor_roll_alert_force_alpha_true_calls_alpha_exactly_once(monkeypatch):
    """A roll alert already runs Alpha on its own merits -- forcing must
    not cause a second, duplicate gather. Mirrors case 3/11 for the
    position-monitor path."""
    runner, cosmos, notifier, state = _monitor_runner_fixture(
        monkeypatch, roll_activity={"activity": "ROLL_OUT"},
        alpha_result={"opportunity_strength": "NONE", "one_liner": "x"},
    )
    _run_position_monitor(
        runner, cosmos, _FakeFetcher(_market_data_for_monitor()),
        run_trigger="manual", force_alpha=True,
    )
    assert state["alpha_calls"] == 1
    assert state["phase2_calls"] == 1
    activity = cosmos.activities[0]
    assert activity["is_alert"] is True
    assert activity["alpha_run"] == {"trigger": "manual", "forced": False, "status": "ok"}


# ===========================================================================
# Section D -- four agent-module pass-through wiring + main.py regression
# lock (not in design §11's numbered list, but directly in this file's
# ownership scope: the thin wrapper modules and the cron call site).
# ===========================================================================

class _FakeRunnerCapture:
    """Captures the kwargs each wrapper module forwards to the runner,
    without exercising any real runner/LLM logic."""

    def __init__(self):
        self.symbol_calls = []
        self.monitor_calls = []

    async def run_symbol_agent(self, **kwargs):
        self.symbol_calls.append(kwargs)

    async def run_position_monitor(self, **kwargs):
        self.monitor_calls.append(kwargs)


class _FakeConfig:
    max_activity_entries = 2
    yfinance_randomize_symbols = False
    yfinance_config = None

    def model_for(self, function_id):
        return f"model-{function_id}"


class _FakeSymbolCosmos:
    def get_covered_call_symbols(self):
        return [{"symbol": "MSFT", "exchange": "NASDAQ", "enrichment": {"category": "Balanced"}}]

    def get_cash_secured_put_symbols(self):
        return [{"symbol": "MSFT", "exchange": "NASDAQ", "enrichment": {"category": "Balanced"}}]

    def get_symbols_with_active_positions(self, option_type):
        return [{
            "symbol": "MSFT",
            "exchange": "NASDAQ",
            "enrichment": {"category": "Balanced"},
            "_active_positions": [{
                "position_id": "msft-1", "type": option_type,
                "status": "active", "strike": 420.0, "expiration": EXPIRATION,
            }],
        }]


@pytest.mark.parametrize(
    ("module_name", "func_name"),
    [
        ("src.covered_call_agent", "run_covered_call_analysis"),
        ("src.cash_secured_put_agent", "run_cash_secured_put_analysis"),
    ],
)
def test_symbol_agent_wrapper_forwards_run_trigger_and_force_alpha(monkeypatch, module_name, func_name):
    import importlib
    mod = importlib.import_module(module_name)
    monkeypatch.setattr("src.yfinance_data_provider.get_shared_provider", lambda *a, **k: object())
    runner = _FakeRunnerCapture()
    func = getattr(mod, func_name)
    _run(func(_FakeConfig(), runner, _FakeSymbolCosmos(), _FakeContext(),
              run_trigger="manual", force_alpha=True))
    assert len(runner.symbol_calls) == 1
    assert runner.symbol_calls[0]["run_trigger"] == "manual"
    assert runner.symbol_calls[0]["force_alpha"] is True


@pytest.mark.parametrize(
    ("module_name", "func_name"),
    [
        ("src.covered_call_agent", "run_covered_call_analysis"),
        ("src.cash_secured_put_agent", "run_cash_secured_put_analysis"),
    ],
)
def test_symbol_agent_wrapper_default_preserves_scheduled_behavior(monkeypatch, module_name, func_name):
    import importlib
    mod = importlib.import_module(module_name)
    monkeypatch.setattr("src.yfinance_data_provider.get_shared_provider", lambda *a, **k: object())
    runner = _FakeRunnerCapture()
    func = getattr(mod, func_name)
    _run(func(_FakeConfig(), runner, _FakeSymbolCosmos(), _FakeContext()))
    assert runner.symbol_calls[0]["run_trigger"] == "scheduled"
    assert runner.symbol_calls[0]["force_alpha"] is False


@pytest.mark.parametrize(
    ("module_name", "func_name", "option_type"),
    [
        ("src.open_call_monitor_agent", "run_open_call_monitor", "call"),
        ("src.open_put_monitor_agent", "run_open_put_monitor", "put"),
    ],
)
def test_monitor_wrapper_forwards_run_trigger_and_force_alpha(monkeypatch, module_name, func_name, option_type):
    import importlib
    mod = importlib.import_module(module_name)
    monkeypatch.setattr("src.yfinance_data_provider.get_shared_provider", lambda *a, **k: object())
    runner = _FakeRunnerCapture()
    func = getattr(mod, func_name)
    _run(func(_FakeConfig(), runner, _FakeSymbolCosmos(), _FakeContext(),
              run_trigger="manual", force_alpha=True))
    assert len(runner.monitor_calls) == 1
    assert runner.monitor_calls[0]["run_trigger"] == "manual"
    assert runner.monitor_calls[0]["force_alpha"] is True


def test_main_run_all_agents_async_passes_explicit_force_alpha_false(monkeypatch):
    """Regression lock (design case 25, runner-side half): the cron path
    must explicitly pass force_alpha=False for the four in-scope agents,
    not merely rely on the wrapper defaults, so a future default change
    elsewhere cannot silently alter scheduled behaviour."""
    import src.main as main_mod

    calls = []

    def make_recorder(name):
        async def _recorder(config, runner, cosmos, ctx, **kwargs):
            calls.append((name, kwargs))
        return _recorder

    scheduler = main_mod.OptionsAgentScheduler.__new__(main_mod.OptionsAgentScheduler)
    scheduler.cosmos = object()
    scheduler.context_provider = object()
    scheduler.runner = object()
    scheduler.config = object()

    monkeypatch.setattr(main_mod, "run_covered_call_analysis", make_recorder("covered_call"))
    monkeypatch.setattr(main_mod, "run_cash_secured_put_analysis", make_recorder("cash_secured_put"))
    monkeypatch.setattr(main_mod, "run_open_call_monitor", make_recorder("open_call_monitor"))
    monkeypatch.setattr(main_mod, "run_open_put_monitor", make_recorder("open_put_monitor"))

    async def fake_buy_tracker(config, runner, cosmos, ctx):
        calls.append(("buy_tracker", {}))

    monkeypatch.setattr(main_mod, "run_buy_tracker_analysis", fake_buy_tracker)

    _run(scheduler._run_all_agents_async())

    by_name = dict(calls)
    for name in ("covered_call", "cash_secured_put", "open_call_monitor", "open_put_monitor"):
        assert by_name[name] == {"run_trigger": "scheduled", "force_alpha": False}, name
    assert by_name["buy_tracker"] == {}
