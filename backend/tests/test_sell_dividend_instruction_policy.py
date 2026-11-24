"""New-sell dividend policy contracts across primary and review prompts."""

import json

import pytest
from src.alpha_instructions import get_alpha_instructions
from src.cash_secured_put_instructions import TV_CASH_SECURED_PUT_INSTRUCTIONS
from src.covered_call_instructions import TV_COVERED_CALL_INSTRUCTIONS
from src.rule_evaluator import build_rule_evaluation
from src.supervisor_instructions import get_supervisor_instructions


@pytest.mark.parametrize(
    ("agent_type", "instructions", "check_id"),
    [
        ("covered_call", TV_COVERED_CALL_INSTRUCTIONS, "cc_ex_div_check"),
        ("cash_secured_put", TV_CASH_SECURED_PUT_INSTRUCTIONS, "csp_ex_div_check"),
    ],
)
def test_primary_schema_accepts_informational_only_for_dividend_check(
    agent_type, instructions, check_id,
):
    start = instructions.index('"rule_checks":') + len('"rule_checks":')
    schema, _ = json.JSONDecoder().raw_decode(instructions[start:].lstrip())
    assert "informational" in schema[check_id]["status"].split("|")
    for rule_id, rule in schema.items():
        if rule_id != check_id:
            assert "informational" not in rule["status"].split("|")

    # A response conforming to the advertised exception survives evaluation.
    evaluation = build_rule_evaluation(
        agent_type,
        {
            "activity": "WAIT",
            "rule_checks": {
                check_id: {"status": "informational", "detail": "No upcoming date supplied."},
            },
        },
    )
    rule = next(rule for rule in evaluation["rules"] if rule["rule_id"] == check_id)
    assert rule["status"] == "informational"
    assert rule["blocking"] is False
    assert "Upcoming ex-dividend date unavailable" in rule["detail"]
    assert 'Exception for `' + check_id + '` ONLY' in instructions
    assert 'takes precedence over category-skill wording such as "ALWAYS check' in instructions
    assert "When an upcoming date IS known, preserve all existing" in instructions
    assert "Do NOT force SELL when another gate fails" in instructions


@pytest.mark.parametrize("builder", [get_supervisor_instructions, get_alpha_instructions])
@pytest.mark.parametrize("agent_type", ["covered_call", "cash_secured_put"])
@pytest.mark.parametrize("decision", ["SELL", "WAIT", "NOT_NOW"])
def test_reviews_do_not_treat_missing_date_as_a_gate(builder, agent_type, decision):
    instructions = builder(agent_type, decision)
    assert "Missing, null, unavailable or\npast-only dates" in instructions
    assert "Upcoming ex-dividend date unavailable" in instructions
    assert "This absence alone must NOT cause WAIT / NOT_NOW, block SELL, lower confidence" in instructions
    assert "increase risk_rating (including its calendar component)" in instructions
    assert "or raise Supervisor challenge_strength" in instructions
    assert "needs NO Alpha parameter relaxation" in instructions
    assert "Review an unjustified missing-date WAIT under this policy" in instructions
    assert "Earnings,\nDTE, liquidity, Greeks, fundamental and catalyst gates remain unchanged" in instructions


@pytest.mark.parametrize("builder", [get_supervisor_instructions, get_alpha_instructions])
@pytest.mark.parametrize(
    ("agent_type", "decision"),
    [("open_call", "WAIT"), ("open_call", "ROLL_OUT"), ("open_put", "WAIT"), ("buy_tracker", "WAIT")],
)
def test_missing_date_sell_policy_does_not_leak_to_other_agents(builder, agent_type, decision):
    instructions = builder(agent_type, decision)
    assert "UPCOMING EX-DIVIDEND DATE — NEW SELL AGENTS ONLY" not in instructions
    assert "Upcoming ex-dividend date unavailable" not in instructions
