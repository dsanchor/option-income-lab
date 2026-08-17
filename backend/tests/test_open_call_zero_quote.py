"""Regression coverage for unavailable OpenCallMonitor buyback quotes."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
import types

import pytest

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


EXPIRATION = "2026-09-18"
_MISSING = object()


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _market_data(*, bid=0.0, ask=_MISSING, last=2.40, mid=0.0) -> dict:
    current_contract = {
        "bid": bid,
        "last": last,
        "mid": mid,
        "delta": 0.18,
        "gamma": 0.01,
        "theta": -0.04,
        "iv": 0.24,
    }
    if ask is not _MISSING:
        current_contract["ask"] = ask

    chain = {
        "symbol": "MSFT",
        "timestamp": "2026-08-17T14:00:00Z",
        "calls": {
            "20260918": {"420.0": current_contract},
            "20261016": {
                "420.0": {
                    "bid": 2.10,
                    "ask": 2.25,
                    "mid": 2.175,
                    "last": 2.15,
                    "delta": 0.22,
                }
            },
        },
        "puts": {},
    }
    return {
        "overview": json.dumps(
            {"fundamentals": {"current_price": {"value": 410.0}}}
        ),
        "technicals": json.dumps(
            {
                "price": 410.0,
                "oscillators": {
                    "indicators": {
                        "RSI": {"value": 45.0},
                        "MACD.macd": {"value": -0.2},
                        "ADX": {"value": 20.0},
                    }
                },
            }
        ),
        "forecast": "{}",
        "options_chain": json.dumps(chain),
    }


@pytest.mark.parametrize(
    ("bid", "ask", "last"),
    [
        pytest.param(0.0, 0.0, 2.40, id="zero-market-positive-last"),
        pytest.param(1.10, _MISSING, 2.40, id="missing-ask"),
        pytest.param(0.80, 0.0, 2.40, id="positive-bid-zero-ask"),
        pytest.param(0.80, -0.10, 2.40, id="negative-ask"),
        pytest.param(0.80, float("nan"), 2.40, id="nan-ask"),
        pytest.param(0.80, float("inf"), 2.40, id="infinite-ask"),
    ],
)
def test_position_snapshot_does_not_infer_pnl_without_executable_ask(
    bid, ask, last
):
    snapshot = AgentRunner._build_position_snapshot_data(
        _market_data(bid=bid, ask=ask, last=last),
        strike=420.0,
        position_type="call",
        timestamp="2026-08-17T14:00:00Z",
        expiration=EXPIRATION,
        premium_received=3.20,
    )

    assert snapshot is not None
    assert snapshot.get("buyback_ask") is None
    assert snapshot.get("buyback_per_share") is None
    assert snapshot.get("pnl_pct") is None
    assert snapshot["buyback_available"] is False
    assert snapshot["incomplete_data"] is True


def test_position_snapshot_uses_positive_ask_not_midpoint():
    snapshot = AgentRunner._build_position_snapshot_data(
        _market_data(bid=0.0, ask=1.20, last=9.99, mid=0.10),
        strike=420.0,
        position_type="call",
        timestamp="2026-08-17T14:00:00Z",
        expiration=EXPIRATION,
        premium_received=3.20,
    )

    assert snapshot is not None
    assert snapshot["buyback_ask"] == pytest.approx(1.20)
    assert snapshot["midprice"] == pytest.approx(0.10)
    assert snapshot["pnl_pct"] == pytest.approx(62.5)
    assert snapshot["buyback_available"] is True
    assert snapshot["incomplete_data"] is False


class _FakeContext:
    def get_context(self, *args, **kwargs):
        return "No previous activity."


class _FakeFetcher:
    def __init__(self, data):
        self.data = data
        self.last_fetch_stats = {}

    async def fetch_all(self, *args, **kwargs):
        return copy.deepcopy(self.data)


class _FakeCosmos:
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
        raise AssertionError("integration fixture disables snapshot persistence")

    def update_snapshot_dps(self, *args, **kwargs):
        pass

    def write_telemetry(self, *args, **kwargs):
        pass


class _FakeNotifier:
    def __init__(self):
        self.alerts = []
        self.prolonged_wait_alerts = []

    def send_alert(self, **kwargs):
        self.alerts.append(copy.deepcopy(kwargs))

    def send_prolonged_wait_alert(self, **kwargs):
        self.prolonged_wait_alerts.append(copy.deepcopy(kwargs))


def _assessment_handoff() -> dict:
    return {
        "action_needed": "ROLL_DOWN",
        "close_for_profit_recommended": True,
        "profit_level_pct": 100.0,
        "profit_optimization_gate": "eligible",
        "symbol": "MSFT",
        "exchange": "NASDAQ",
        "current_strike": 420.0,
        "current_expiration": EXPIRATION,
        "underlying_price": 410.0,
        "moneyness": "OTM",
        "delta": 0.18,
        "assignment_risk": "low",
        "dte_remaining": 32,
        "risk_flags": ["profit_optimization"],
        "reason": "Fully realized at a $0 buyback; 100% profit captured.",
    }


def _runner_fixture(monkeypatch, data):
    state = {
        "phase2_calls": 0,
        "alpha_calls": 0,
    }
    notifier = _FakeNotifier()
    cosmos = _FakeCosmos()
    runner = AgentRunner(
        llm=LlmConfig(
            provider="azure",
            api_key="test-key",
            endpoint="https://example.test",
        ),
        model="test-model",
        telegram_notifier=notifier,
    )

    async def fake_assessment(**kwargs):
        return (
            "SUMMARY: MSFT | CLOSE | $0 buyback | 100% fully realized",
            None,
            _assessment_handoff(),
        )

    async def fake_roll_management(**kwargs):
        state["phase2_calls"] += 1
        activity = {
            "activity": "CLOSE",
            "current_strike": 420.0,
            "current_expiration": EXPIRATION,
            "underlying_price": 410.0,
            "new_strike": None,
            "new_expiration": None,
            "roll_economics": {"buyback_cost": 0.50},
            "risk_flags": ["close_for_profit"],
            "reason": "Valid positive ask confirms the profit close.",
        }
        return "SUMMARY: MSFT | CLOSE open call", activity

    async def fake_supervisor(**kwargs):
        return None

    async def fake_alpha(**kwargs):
        state["alpha_calls"] += 1
        return None

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
    monkeypatch.setattr(runner, "_build_position_snapshot_data", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_build_position_context_section", lambda *a, **k: "")
    monkeypatch.setattr(runner, "_build_market_data_block", lambda *a, **k: "")
    monkeypatch.setattr(runner, "_build_alpha_options_chain", lambda *a, **k: "")
    monkeypatch.setattr(runner, "_record_trace", lambda *a, **k: None)
    monkeypatch.setattr(
        runner,
        "_validate_premium_against_chain",
        lambda activity_data, *args: activity_data,
    )
    monkeypatch.setattr(ar_mod, "build_rule_evaluation", fake_rule_evaluation)
    monkeypatch.setattr(
        ar_mod,
        "merge_phase_evaluations",
        lambda assessment, roll: {
            **assessment,
            "phases": assessment["phases"] + roll["phases"],
        },
    )
    return runner, cosmos, notifier, state, _FakeFetcher(data)


def _run_monitor(runner, cosmos, fetcher):
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
        )
    )


@pytest.mark.parametrize(
    ("bid", "ask", "last"),
    [
        pytest.param(0.0, 0.0, 2.40, id="zero-market-positive-last"),
        pytest.param(1.10, _MISSING, 2.40, id="missing-ask"),
        pytest.param(0.80, 0.0, 2.40, id="positive-bid-zero-ask"),
        pytest.param(0.80, -0.10, 2.40, id="negative-ask"),
        pytest.param(0.80, float("nan"), 2.40, id="nan-ask"),
        pytest.param(0.80, float("inf"), 2.40, id="infinite-ask"),
    ],
)
def test_invalid_quote_forces_non_alert_wait_and_skips_phase2(
    monkeypatch, bid, ask, last
):
    runner, cosmos, notifier, state, fetcher = _runner_fixture(
        monkeypatch, _market_data(bid=bid, ask=ask, last=last)
    )

    _run_monitor(runner, cosmos, fetcher)

    assert len(cosmos.activities) == 1
    activity = cosmos.activities[0]
    assert "error" not in activity
    assert activity["activity"] == "WAIT"
    assert activity["is_alert"] is False
    assert state["phase2_calls"] == 0
    assert len(activity["rule_evaluation"]["phases"]) == 1
    assert notifier.alerts == []
    assert "incomplete_data" in activity["risk_flags"]
    assert activity.get("close_for_profit_recommended") is False
    assert activity.get("profit_level_pct") is None
    assert activity.get("profit_optimization_gate") is None
    for field in (
        "buyback_cost",
        "buyback_per_share",
        "pct_captured",
        "pnl_pct",
    ):
        assert activity.get(field) is None

    user_text = " ".join(
        str(activity.get(field) or "")
        for field in ("summary", "reason", "waiting_for")
    ).lower()
    assert "$0" not in user_text
    assert "100%" not in user_text
    assert "fully realized" not in user_text


def test_valid_positive_ask_preserves_close_profit_flow(monkeypatch):
    runner, cosmos, notifier, state, fetcher = _runner_fixture(
        monkeypatch, _market_data(bid=0.0, ask=0.50, last=9.99, mid=0.10)
    )

    _run_monitor(runner, cosmos, fetcher)

    activity = cosmos.activities[0]
    assert "error" not in activity
    assert state["phase2_calls"] == 1
    assert activity["activity"] == "CLOSE"
    assert activity["is_alert"] is True
    assert "incomplete_data" not in activity.get("risk_flags", [])
    assert len(notifier.alerts) == 1


def test_repeated_invalid_quote_cycles_do_not_become_prolonged_wait(
    monkeypatch,
):
    runner, cosmos, notifier, state, fetcher = _runner_fixture(
        monkeypatch, _market_data(bid=0.0, ask=0.0, last=2.40)
    )
    runner.PROLONGED_WAIT_THRESHOLD = 2

    _run_monitor(runner, cosmos, fetcher)
    _run_monitor(runner, cosmos, fetcher)

    assert len(cosmos.activities) == 2
    assert all(activity["activity"] == "WAIT" for activity in cosmos.activities)
    assert all(activity["is_alert"] is False for activity in cosmos.activities)
    assert state["phase2_calls"] == 0
    assert state["alpha_calls"] == 0
    assert notifier.alerts == []
    assert notifier.prolonged_wait_alerts == []
