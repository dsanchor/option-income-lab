"""Exercise the actual monitor persistence and both single-call review parsers."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from src.agent_runner import AgentRunner
from src.dual_roll import primary_roll_target

from tests.test_force_alpha_execution import (
    _FakeContext,
    _FakeFetcher,
    _market_data_for_monitor,
    _monitor_runner_fixture,
    _run,
    _run_position_monitor,
    ar_mod,
)


@pytest.mark.parametrize("option_type", ["call", "put"])
@pytest.mark.parametrize("extension_case", ["available", "filtered", "stale"])
def test_monitor_persists_both_targets_scoped_reviews_and_notifies_in_one_pipeline(monkeypatch, option_type, extension_case):
    now = datetime.now(timezone.utc)
    exp = lambda days: (now.date() + timedelta(days=days)).isoformat()
    target = 425.0 if option_type == "call" else 415.0
    sign = 1 if option_type == "call" else -1
    action = "ROLL_UP_AND_OUT" if option_type == "call" else "ROLL_DOWN_AND_OUT"
    primary = {
        "activity": action, "new_strike": target, "new_expiration": exp(28),
        "current_strike": 420.0, "current_expiration": exp(7),
        "roll_economics": {"buyback_cost": 2, "new_premium": 1.5, "net_credit": -0.5, "roll_tier": "ultra_defensive"},
    }
    runner, cosmos, notifier, state = _monitor_runner_fixture(monkeypatch, roll_activity=primary)
    roll_prompts = []
    roll_management = runner._run_roll_management

    async def principal(**kwargs):
        roll_prompts.append(kwargs["filtered_chain_text"])
        return await roll_management(**kwargs)

    monkeypatch.setattr(runner, "_run_roll_management", principal)
    handoff = {
        "symbol": "MSFT", "action_needed": action, "underlying_price": 421,
        "current_strike": 420.0, "current_expiration": exp(7),
        "earnings_analysis": {"next_earnings_date": exp(120)},
    }

    async def assessment(**kwargs):
        return "SUMMARY: MSFT | defensive roll", None, handoff, "assessment-trace"

    monkeypatch.setattr(runner, "_run_position_assessment", assessment)
    monkeypatch.setattr(runner, "_get_client", lambda *args: object())
    for method in ("_run_supervisor_review", "_run_alpha_review", "_build_market_data_block", "_build_alpha_options_chain"):
        monkeypatch.setattr(runner, method, getattr(AgentRunner, method).__get__(runner, AgentRunner))
    calls = []

    class FakeAgent:
        def __init__(self, *, name, instructions, **kwargs):
            self.name = name
            self.instructions = instructions

        async def run(self, prompt):
            calls.append((self.name, self.instructions, prompt))
            if self.name.startswith("Supervisor"):
                verdict = {"challenge_strength": "WEAK", "counter_arguments": [], "net_assessment": "ORIGINAL_HOLDS", "one_liner": "Defensive holds"}
                response = {"alternative_reviews": {
                    "DEFENSIVE": verdict,
                    "CREDIT_EXTENDED": {**verdict, "challenge_strength": "STRONG", "net_assessment": "RECONSIDER", "one_liner": "Longer assignment exposure"},
                }}
            else:
                response = {
                    "preferred_alternative_id": "CREDIT_EXTENDED",
                    "comparison_rationale": "Credit improves, but ties up capital longer.",
                }
            return SimpleNamespace(text=json.dumps(response))

    monkeypatch.setattr(ar_mod, "Agent", FakeAgent)
    traces = []
    monkeypatch.setattr(runner, "_record_trace", lambda cosmos, **kwargs: traces.append(kwargs))
    bucket = {}
    for days, strike, bid, ask in [(7, 420, 1, 2), (28, target, 1.5, 1.6), (50, target, 2.5, 2.6)]:
        delta = (0.95 if option_type == "call" else -0.7) if days == 50 and extension_case == "filtered" else 0.3 * sign
        quote_asof = now - timedelta(days=1) if days == 50 and extension_case == "stale" else now
        bucket[exp(days).replace("-", "")] = {str(float(strike)): {
            "bid": bid, "ask": ask, "delta": delta, "theta": -0.02,
            "openInterest": 100,
            "_meta": {"greeks_valid": True, "quote_asof": quote_asof.isoformat()},
        }}
    chain = {"symbol": "MSFT", "calls": bucket if option_type == "call" else {}, "puts": bucket if option_type == "put" else {}}
    market = {
        "overview": "{}", "technicals": "{}", "forecast": "{}",
        "options_chain": json.dumps(chain),
    }
    _run(runner.run_position_monitor(
        name="Monitor", symbol="MSFT", exchange="NASDAQ",
        position={"position_id": "position-1", "type": option_type, "strike": 420.0, "expiration": exp(7), "contracts": 3},
        agent_type=f"open_{option_type}_monitor", cosmos=cosmos,
        context_provider=_FakeContext(), fetcher=_FakeFetcher(market),
        assessment_instructions="test", roll_instructions="test",
    ))
    assert state["phase2_calls"] == 1
    assert len(roll_prompts) == 1 and "DUAL-ROLL PAIRS" in roll_prompts[0]
    assert "Principal preference: DEFENSIVE" in roll_prompts[0]
    assert len(calls) == 2
    assert len(cosmos.activities) == 1
    activity = cosmos.activities[0]
    alternatives = activity["roll_alternatives"]["alternatives"]
    assert alternatives["DEFENSIVE"]["new_expiration"] == exp(28)
    extended = alternatives["CREDIT_EXTENDED"]
    check, = extended["expiry_checks"]
    assert check["expiration"] == exp(50) and check["quote_asof"] == quote_asof.isoformat()
    if extension_case == "available":
        assert extended["new_expiration"] == exp(50)
        assert extended["roll_economics"]["net_credit_total"] == 150
        assert activity["supervisor_view"]["alternative_reviews"]["CREDIT_EXTENDED"]["net_assessment"] == "RECONSIDER"
        assert activity["alpha_view"]["preferred_alternative_id"] == "CREDIT_EXTENDED"
    else:
        assert extended["status"] == ("no_candidate" if extension_case == "filtered" else "incomplete_data")
        assert "new_expiration" not in extended
        assert ("candidate filters" if extension_case == "filtered" else "stale") in " ".join(check["reasons"])
        assert "CREDIT_EXTENDED" not in activity["supervisor_view"]["alternative_reviews"]
        assert activity["alpha_view"]["preferred_alternative_id"] is None
    assert activity["supervisor_view"]["alternative_reviews"]["DEFENSIVE"]["net_assessment"] == "ORIGINAL_HOLDS"
    assert activity["alpha_view"]["alternative"] == {}
    assert primary_roll_target(activity) == (target, exp(28))
    assert len(notifier.alerts) == 1
    assert notifier.alerts[0]["alert_data"]["roll_alternatives"] == activity["roll_alternatives"]
    assert len(traces) == 2 and all(trace["parsed"] for trace in traces)
    for _, instructions, prompt in calls:
        assert "90" in instructions and "CREDIT_EXTENDED" in instructions
        assert exp(28) in prompt and exp(50) in prompt and "net_credit_total" in prompt


def test_unscoped_legacy_review_response_cannot_approve_both_new_contracts(monkeypatch):
    runner, _, _, _ = _monitor_runner_fixture(monkeypatch)
    monkeypatch.setattr(runner, "_get_client", lambda *args: object())
    monkeypatch.setattr(runner, "_run_supervisor_review", AgentRunner._run_supervisor_review.__get__(runner))

    class LegacyAgent:
        def __init__(self, **kwargs):
            pass

        async def run(self, prompt):
            return SimpleNamespace(text=json.dumps({
                "challenge_strength": "WEAK", "counter_arguments": [],
                "net_assessment": "ORIGINAL_HOLDS", "one_liner": "Everything holds",
            }))

    monkeypatch.setattr(ar_mod, "Agent", LegacyAgent)
    activity = {"symbol": "MSFT", "activity": "ROLL_UP_AND_OUT", "roll_alternatives": {
        "alternatives": {key: {"status": "available"} for key in ("DEFENSIVE", "CREDIT_EXTENDED")},
    }}
    result = _run(runner._run_supervisor_review(
        activity_payload=activity, market_data="", previous_context="", agent_type="open_call_monitor",
    ))
    assert result["net_assessment"] == "REVIEW_INCOMPLETE"
    assert all(item["review_status"] == "incomplete" for item in result["alternative_reviews"].values())


def test_model_cannot_attach_a_dual_roll_contract_to_an_unrelated_roll_direction(monkeypatch):
    runner, cosmos, _, state = _monitor_runner_fixture(monkeypatch, roll_activity={
        "activity": "ROLL_OUT", "new_strike": 420, "new_expiration": "2099-01-16",
        "roll_economics": {"buyback_cost": 1.25, "new_premium": 2.5, "net_credit": 1.25},
        "roll_alternatives": {"alternatives": {"CREDIT_EXTENDED": {"status": "available"}}},
    })
    _run_position_monitor(runner, cosmos, _FakeFetcher(_market_data_for_monitor()))
    assert "roll_alternatives" not in cosmos.activities[0]
    assert state["alpha_calls"] == state["supervisor_calls"] == state["phase2_calls"] == 1
