from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from src.dual_roll import (
    build_roll_alternatives,
    primary_roll_target,
    scope_alpha_review,
    scope_supervisor_review,
)
from src.options_chain_cache import apply_agent_view
from src.options_chain_filters import (
    filter_options_chain_by_delta,
    filter_options_chain_for_position,
)
from src.rule_evaluator import build_rule_evaluation
from src.telegram_notifier import TelegramNotifier

NOW = datetime(2026, 11, 11, 15, tzinfo=timezone.utc)


def expiry(dte):
    return (NOW.date() + timedelta(days=dte)).isoformat()


def quote(bid=2.5, **overrides):
    result = {
        "bid": bid, "ask": 12.0, "delta": 0.3, "theta": -0.02,
        "openInterest": 100,
        "_meta": {"stale": False, "greeks_valid": True, "quote_asof": NOW.isoformat()},
    }
    result.update(overrides)
    return result


def scenario(option_type="call", later=None):
    target = 105.0 if option_type == "call" else 95.0
    sign = 1 if option_type == "call" else -1
    position = {
        "type": option_type, "strike": 100.0, "expiration": expiry(7), "contracts": 3,
    }
    action = "ROLL_UP_AND_OUT" if option_type == "call" else "ROLL_DOWN_AND_OUT"
    activity = {
        "symbol": "TEST", "activity": action, "current_strike": 100.0,
        "current_expiration": expiry(7), "new_strike": target, "new_expiration": expiry(28),
        "roll_economics": {"buyback_cost": 2.0, "new_premium": 1.5, "net_credit": -0.5, "roll_tier": "ultra_defensive"},
        "timestamp": NOW.isoformat(),
    }
    assessment = {
        "action_needed": action,
        "earnings_analysis": {"next_earnings_date": expiry(120)},
    }
    bucket = {
        expiry(7): {"100.0": quote(1.0, ask=2.0, delta=0.6 * sign)},
        expiry(28): {str(target): quote(1.5, delta=0.3 * sign)},
    }
    for dte, contract in (later if later is not None else [(50, quote()), (70, quote(9))]):
        bucket[expiry(dte)] = {str(target): contract}
    chain = {"calls": bucket if option_type == "call" else {}, "puts": bucket if option_type == "put" else {}}
    return activity, chain, position, assessment


def build(s):
    activity, chain, position, assessment = s
    return build_roll_alternatives(activity, chain, position=position, assessment=assessment, now=NOW)


@pytest.mark.parametrize("option_type", ["call", "put"])
def test_same_defensive_strike_nearest_expiry_not_max_credit_and_primary_unchanged(option_type):
    s = scenario(option_type)
    before = deepcopy(s)
    result = build(s)
    candidate = result["alternatives"]["CREDIT_EXTENDED"]
    assert candidate["status"] == "available"
    assert candidate["new_strike"] == s[0]["new_strike"]
    assert candidate["new_expiration"] == expiry(50)
    assert candidate["roll_economics"]["net_credit"] == 0.5
    assert candidate["roll_economics"]["net_credit_total"] == 150
    assert candidate["roll_economics"]["before_commissions"] is True
    assert result["preferred_alternative_id"] == result["action_target_id"] == "DEFENSIVE"
    assert s == before


@pytest.mark.parametrize("option_type", ["call", "put"])
def test_90_from_today_boundary_and_strictly_positive_credit(option_type):
    s = scenario(option_type, [(40, quote(1)), (50, quote(2)), (90, quote(2.01)), (91, quote(9))])
    candidate = build(s)["alternatives"]["CREDIT_EXTENDED"]
    assert candidate["status"] == "available"
    assert candidate["new_expiration"] == expiry(90)
    assert candidate["dte"] == 90
    s[1]["calls" if option_type == "call" else "puts"].pop(expiry(90))
    assert build(s)["alternatives"]["CREDIT_EXTENDED"]["status"] == "no_candidate"


@pytest.mark.parametrize("option_type", ["call", "put"])
def test_no_same_expiry_duplicate_or_other_strike_fallback(option_type):
    s = scenario(option_type, [])
    bucket = s[1]["calls" if option_type == "call" else "puts"]
    bucket[expiry(28)][str(s[0]["new_strike"])] = quote(9)
    bucket[expiry(50)] = {"110.0": quote(9)}
    assert build(s)["alternatives"]["CREDIT_EXTENDED"]["status"] == "incomplete_data"
    s[0]["activity"] = "CLOSE"
    result = build(s)
    assert result["alternatives"]["DEFENSIVE"]["status"] == "no_defensive"
    assert "new_strike" not in result["alternatives"]["CREDIT_EXTENDED"]


@pytest.mark.parametrize("option_type,target", [("call", 100), ("call", 95), ("put", 100), ("put", 105)])
def test_extended_requires_strict_improvement(option_type, target):
    s = scenario(option_type)
    old_target = str(s[0]["new_strike"])
    s[0]["new_strike"] = target
    for rows in s[1]["calls" if option_type == "call" else "puts"].values():
        if old_target in rows:
            rows[str(float(target))] = rows.pop(old_target)
    assert build(s)["alternatives"]["CREDIT_EXTENDED"]["status"] == "no_candidate"


@pytest.mark.parametrize("bad", [
    {"bid": 0}, {"bid": float("nan")}, {"bid": float("inf")}, {"bid": True},
    {"ask": 0}, {"ask": None}, {"openInterest": 0},
    {"_meta": {"stale": True}}, {"_meta": {"greeks_valid": False, "stale": False}},
    {"bid": 13, "ask": 12},
])
def test_missing_invalid_or_stale_quote_never_becomes_a_success_candidate(bad):
    s = scenario(later=[(50, quote(**bad))])
    result = build(s)["alternatives"]["CREDIT_EXTENDED"]
    assert result["status"] == "incomplete_data"
    assert "new_expiration" not in result


@pytest.mark.parametrize("option_type", ["call", "put"])
def test_unusable_earlier_quote_does_not_hide_a_valid_later_eligible_credit(option_type):
    s = scenario(option_type, later=[(50, quote(ask=0)), (70, quote(9))])
    candidate = build(s)["alternatives"]["CREDIT_EXTENDED"]
    assert candidate["status"] == "available" and candidate["new_expiration"] == expiry(70)


def test_missing_quote_asof_becomes_stale_at_repository_boundary():
    s = scenario()
    for bucket in s[1].values():
        for rows in bucket.values():
            for contract in rows.values():
                contract.pop("_meta")
    s = (s[0], apply_agent_view(s[1], now=NOW), s[2], s[3])
    result = build(s)
    assert result["alternatives"]["DEFENSIVE"]["status"] == "incomplete_data"
    assert result["alternatives"]["CREDIT_EXTENDED"]["status"] == "incomplete_data"


@pytest.mark.parametrize("wrapper", [{"status": "error"}, {"status": "unexpected"}, {"status": None}, {"error": "Provider unavailable"}])
def test_failed_or_unknown_source_availability_cannot_make_a_candidate(wrapper):
    s = scenario()
    s[1].update(wrapper)
    assert build(s)["alternatives"]["CREDIT_EXTENDED"]["status"] == "incomplete_data"


@pytest.mark.parametrize("ask", [None, 0, -1, float("nan"), float("inf")])
def test_invalid_held_ask_prevents_alternative(ask):
    s = scenario()
    s[1]["calls"][expiry(7)]["100.0"]["ask"] = ask
    assert build(s)["alternatives"]["CREDIT_EXTENDED"]["status"] == "incomplete_data"


def test_no_later_expiry_data_is_distinct_from_complete_nonpositive_search():
    assert build(scenario(later=[]))["alternatives"]["CREDIT_EXTENDED"]["status"] == "incomplete_data"
    assert build(scenario(later=[(50, quote(2))]))["alternatives"]["CREDIT_EXTENDED"]["status"] == "no_candidate"
    assert build(scenario(later=[(91, quote(9))]))["alternatives"]["CREDIT_EXTENDED"]["status"] != "available"


@pytest.mark.parametrize("option_type,delta", [("call", 0.95), ("put", -0.7)])
def test_source_evidence_explains_filtered_expiry_without_reintroducing_its_contract(option_type, delta):
    activity, source, position, assessment = scenario(option_type, [(50, quote(delta=delta))])
    source = apply_agent_view(source, now=NOW)
    before = deepcopy(source)
    filtered = filter_options_chain_by_delta(
        filter_options_chain_for_position(source, position["strike"], option_type),
    )
    result = build_roll_alternatives(
        activity, filtered, position=position, assessment=assessment, now=NOW,
        source_chain=source,
    )
    extended = result["alternatives"]["CREDIT_EXTENDED"]
    assert extended["status"] == "no_candidate"
    assert "new_expiration" not in extended
    check, = extended["expiry_checks"]
    assert check["expiration"] == expiry(50)
    assert check["bid"] == 2.5 and check["buyback_ask"] == 2
    assert check["reasons"] == [
        "Excluded by the existing position/delta candidate filters; source quote is diagnostic only.",
    ]
    assert source == before


def test_diagnostic_source_cannot_replace_the_nearest_eligible_filtered_destination():
    activity, source, position, assessment = scenario(later=[
        (40, quote(9, delta=0.95)), (50, quote()), (70, quote(9)),
    ])
    filtered = filter_options_chain_by_delta(source)
    extended = build_roll_alternatives(
        activity, filtered, position=position, assessment=assessment, now=NOW,
        source_chain=source,
    )["alternatives"]["CREDIT_EXTENDED"]
    assert extended["status"] == "available"
    assert extended["new_expiration"] == expiry(50)
    assert [check["expiration"] for check in extended["expiry_checks"]] == [expiry(40), expiry(50)]
    assert [check["status"] for check in extended["expiry_checks"]] == ["no_candidate", "available"]


@pytest.mark.parametrize("changes,reason", [
    ({"ask": None}, "New ask is missing or invalid."),
    ({"bid": 13}, "Bid exceeds ask (crossed market)."),
    ({"_meta": {"stale": True}}, "Quote is stale or freshness is unknown."),
    ({"openInterest": 0}, "Existing bid/open-interest/Greek-validity eligibility gate fails."),
])
def test_expiry_checks_identify_the_execution_evidence_that_failed(changes, reason):
    extended = build(scenario(later=[(50, quote(**changes))]))["alternatives"]["CREDIT_EXTENDED"]
    assert extended["status"] == "incomplete_data"
    check, = extended["expiry_checks"]
    assert check["status"] == "incomplete_data"
    assert reason in check["reasons"]
    assert check["expiration"] == expiry(50)


def test_earnings_exception_missing_evidence_is_not_reported_as_missing_quotes():
    s = scenario(later=[(50, quote())])
    s[3]["earnings_analysis"]["next_earnings_date"] = expiry(40)
    extended = build(s)["alternatives"]["CREDIT_EXTENDED"]
    assert extended["status"] == "incomplete_data"
    check, = extended["expiry_checks"]
    assert check["bid"] == 2.5 and check["ask"] == 12
    assert check["reasons"] == [
        "Post-earnings 8–13-day exception requires evidence not established by this search.",
    ]
    assert "quotes" not in extended["reason"]


def test_expiry_checks_distinguish_absent_strike_nonpositive_credit_and_event_blocks():
    s = scenario(later=[(40, quote(2)), (50, quote()), (70, quote(9))])
    s[1]["calls"][expiry(50)] = {"110.0": quote(9)}
    s[3]["earnings_analysis"]["next_earnings_date"] = expiry(69)
    extended = build(s)["alternatives"]["CREDIT_EXTENDED"]
    assert extended["status"] == "incomplete_data"
    assert [check["status"] for check in extended["expiry_checks"]] == [
        "no_candidate", "incomplete_data", "no_candidate",
    ]
    reasons = [check["reasons"][0] for check in extended["expiry_checks"]]
    assert "strictly positive" in reasons[0]
    assert "Exact defensive strike is absent" in reasons[1]
    assert "earnings safety buffer" in reasons[2]


def test_same_strike_identity_is_exact_decimal_not_a_float_or_nearest_match():
    s = scenario(later=[(50, quote())])
    target = "105.00000000000000000001"
    s[0]["new_strike"] = target
    s[2]["strike"] = "105"
    s[1]["calls"][expiry(7)]["105"] = s[1]["calls"][expiry(7)].pop("100.0")
    s[1]["calls"][expiry(28)][target] = s[1]["calls"][expiry(28)].pop("105.0")
    assert build(s)["alternatives"]["CREDIT_EXTENDED"]["status"] == "incomplete_data"
    s[1]["calls"][expiry(50)][target] = quote()
    candidate = build(s)["alternatives"]["CREDIT_EXTENDED"]
    assert candidate["status"] == "available" and candidate["new_strike"] == target
    assert primary_roll_target({**s[0], "roll_alternatives": build(s)}) == (None, None)


def test_earnings_event_and_unknown_calendar_gates_are_not_relaxed():
    s = scenario(later=[(50, quote())])
    s[3]["earnings_analysis"]["next_earnings_date"] = expiry(49)
    assert build(s)["alternatives"]["CREDIT_EXTENDED"]["status"] == "no_candidate"
    s[3]["earnings_analysis"]["next_earnings_date"] = "unknown"
    assert build(s)["alternatives"]["CREDIT_EXTENDED"]["status"] == "incomplete_data"
    s[3]["earnings_analysis"]["next_earnings_date"] = expiry(120)
    s[3]["risk_flags"] = ["catalyst_pending"]
    assert build(s)["alternatives"]["CREDIT_EXTENDED"]["status"] == "no_candidate"


def test_authoritative_provider_calendar_overrides_handoff_and_ex_dividend_is_respected():
    activity, chain, position, assessment = scenario(later=[(50, quote())])
    result = build_roll_alternatives(
        activity, chain, position=position, assessment=assessment, now=NOW,
        market_data={"overview": {"fundamentals": {
            "earnings_release_next_date_fq": {"formatted": expiry(49)},
        }}},
    )
    assert result["alternatives"]["CREDIT_EXTENDED"]["status"] == "no_candidate"
    result = build_roll_alternatives(
        activity, chain, position=position, assessment=assessment, now=NOW,
        market_data={"dividends": {"ex_dividend_date_recent": {"formatted": expiry(40)}}},
    )
    assert result["alternatives"]["CREDIT_EXTENDED"]["status"] == "no_candidate"


def test_open_quantity_resolution_and_nonstandard_multiplier_fail_closed():
    s = scenario()
    s[2].update(open_contracts=2, quantity=-3)
    economics = build(s)["alternatives"]["CREDIT_EXTENDED"]["roll_economics"]
    assert economics["quantity"] == 2 and economics["net_credit_total"] == 100
    s[2].pop("open_contracts")
    s[2]["quantity"] = 4
    assert build(s)["alternatives"]["CREDIT_EXTENDED"]["status"] == "incomplete_data"
    s[2].pop("quantity")
    s[2]["multiplier"] = 10
    assert build(s)["alternatives"]["CREDIT_EXTENDED"]["status"] == "incomplete_data"


@pytest.mark.parametrize("action", ["SELL", "ROLL_OUT", "ROLL_UP", "ROLL_DOWN", "WAIT"])
def test_unrelated_flows_remain_legacy(action):
    s = scenario()
    s[0]["activity"] = s[3]["action_needed"] = action
    assert build(s) is None


def scoped_activity():
    s = scenario()
    return {**s[0], "roll_alternatives": build(s)}


def test_supervisor_verdicts_independent_and_legacy_not_fanned_out():
    activity = scoped_activity()
    hold = {"challenge_strength": "WEAK", "net_assessment": "ORIGINAL_HOLDS", "counter_arguments": [], "one_liner": "Fine"}
    reconsider = {**hold, "challenge_strength": "STRONG", "net_assessment": "RECONSIDER"}
    review = scope_supervisor_review({"alternative_reviews": {"DEFENSIVE": hold, "CREDIT_EXTENDED": reconsider}}, activity)
    assert review["net_assessment"] == "ORIGINAL_HOLDS"
    assert review["alternative_reviews"]["CREDIT_EXTENDED"]["net_assessment"] == "RECONSIDER"
    legacy = scope_supervisor_review(dict(hold), activity)
    assert all(item["review_status"] == "incomplete" for item in legacy["alternative_reviews"].values())
    assert legacy["net_assessment"] == "REVIEW_INCOMPLETE"
    assert scope_supervisor_review(dict(hold), {}) == hold


def test_alpha_can_prefer_existing_credit_without_third_trade_or_action_mutation():
    activity = scoped_activity()
    # Identity strike may refer to the holding; the explicit primary remains the target.
    activity.update(strike=100, expiration=expiry(7))
    review = scope_alpha_review({
        "preferred_alternative_id": "CREDIT_EXTENDED", "comparison_rationale": "More credit, longer exposure.",
    }, activity)
    assert review["comparison_status"] == "complete" and review["alternative"] == {}
    activity["alpha_view"] = review
    assert primary_roll_target(activity) == (105, expiry(28))
    assert scope_alpha_review({"preferred_alternative_id": "FABRICATED", "alternative": {"strike": 999}}, activity)["comparison_status"] == "incomplete"
    legacy = scope_alpha_review({"opportunity_strength": "STRONG", "one_liner": "Both approved"}, activity)
    assert legacy["opportunity_strength"] == "NONE" and "Both approved" not in legacy["one_liner"]
    assert primary_roll_target({"strike": 100, "expiration": expiry(7)}) == (100, expiry(7))


def additional_review(**overrides):
    review = {
        "preferred_alternative_id": "DEFENSIVE", "comparison_rationale": "Shorter exposure.",
        "baseline_alternative_id": "DEFENSIVE", "relaxed_parameter": "technical_borderline",
        "parameter_detail": "Higher strike needs borderline resistance confirmation.",
        "alternative": {
            "action": "Roll to the higher strike", "strike": 110, "expiration": expiry(28),
            "rationale": "More upside room with only the technical threshold relaxed.",
            "trade_off": "Less premium, more room.", "premium_comparison": "Credit still positive.",
        },
    }
    review.update(overrides)
    return review


def test_optional_additional_alpha_trade_has_baseline_one_soft_parameter_and_remains_advisory():
    s = scenario()
    s[0]["underlying_price"] = 100
    s[1]["calls"][expiry(28)]["110.0"] = quote(2.3, ask=2.4)
    activity = {**s[0], "roll_alternatives": build(s)}
    result = scope_alpha_review(additional_review(), activity, chain=s[1])
    assert result["comparison_status"] == "complete"
    assert result["additional_proposal_status"] == "available"
    assert result["additional_alternative"]["baseline_alternative_id"] == "DEFENSIVE"
    assert result["additional_alternative"]["advisory_only"] is True
    assert result["additional_alternative"]["roll_economics"]["net_credit"] == pytest.approx(0.3)
    assert result["alternative"] == {}
    assert primary_roll_target(activity) == (105, expiry(28))


def test_alpha_cannot_author_its_own_validated_additional_contract_status():
    result = scope_alpha_review({
        "preferred_alternative_id": "DEFENSIVE", "comparison_rationale": "Shorter exposure.",
        "additional_proposal_status": "available",
        "additional_alternative": {"strike": 999, "expiration": expiry(120), "status": "available"},
    }, scoped_activity())
    assert "additional_alternative" not in result
    assert "additional_proposal_status" not in result
    assert result["comparison_status"] == "complete"


@pytest.mark.parametrize("violation", ["missing_baseline", "hard_parameter", "over_45", "two_coordinates", "fabricated_contract", "earnings"])
def test_additional_alpha_trade_cannot_reuse_extended_exception_or_skip_existing_safety(violation):
    s = scenario()
    s[0]["underlying_price"] = 100
    s[1]["calls"][expiry(28)]["110.0"] = quote(2.3, ask=2.4)
    activity = {**s[0], "roll_alternatives": build(s)}
    review = additional_review()
    if violation == "missing_baseline":
        review.pop("baseline_alternative_id")
    elif violation == "hard_parameter":
        review["relaxed_parameter"] = "maxDTE"
    elif violation == "over_45":
        review["baseline_alternative_id"] = "CREDIT_EXTENDED"
        review["alternative"].update(strike=105, expiration=expiry(50))
    elif violation == "two_coordinates":
        review["alternative"]["expiration"] = expiry(35)
        s[1]["calls"][expiry(35)] = {"110.0": quote(2.3, ask=2.4)}
    elif violation == "fabricated_contract":
        review["alternative"]["strike"] = 999
    else:
        activity["roll_alternatives"]["safety_evidence"]["next_earnings_date"] = expiry(20)
    result = scope_alpha_review(review, activity, chain=s[1])
    assert result["additional_proposal_status"] == "rejected"
    assert result["additional_proposal_reason"]
    assert "additional_alternative" not in result
    assert result["preferred_alternative_id"] == "DEFENSIVE"
    assert result["alternative"] == {}


def test_extended_rule_scope_does_not_change_defensive_sell_or_general_alpha_cap():
    activity = scoped_activity()
    extended = activity["roll_alternatives"]["alternatives"]["CREDIT_EXTENDED"]
    scoped = {**activity, **extended, "roll_alternative_id": "CREDIT_EXTENDED"}
    rules = build_rule_evaluation("open_call_monitor", scoped, phase="roll")["phases"][0]["rules"]
    assert next(r for r in rules if r["rule_id"] == "ocm_r_dte_target")["status"] == "pass"
    assert next(r for r in rules if r["rule_id"] == "ocm_r_roll_tier")["status"] == "pass"
    unscoped = {**scoped, "roll_alternative_id": "DEFENSIVE"}
    rules = build_rule_evaluation("open_call_monitor", unscoped, phase="roll")["phases"][0]["rules"]
    assert next(r for r in rules if r["rule_id"] == "ocm_r_dte_target")["status"] == "warning"
    rules = build_rule_evaluation("covered_call", {**scoped, "activity": "SELL", "dte": 50})["rules"]
    assert next(r for r in rules if r["rule_id"] == "cc_dte_cap")["status"] == "fail"


def test_extended_dte_rule_uses_full_timestamp_not_midnight_utc_at_90_boundary():
    s = scenario(later=[(90, quote())])
    activity = {**s[0], "roll_alternatives": build(s)}
    extended = activity["roll_alternatives"]["alternatives"]["CREDIT_EXTENDED"]
    scoped = {**activity, **extended, "roll_alternative_id": "CREDIT_EXTENDED"}
    rules = build_rule_evaluation("open_call_monitor", scoped, phase="roll")["phases"][0]["rules"]
    dte_rule = next(r for r in rules if r["rule_id"] == "ocm_r_dte_target")
    assert dte_rule["status"] == "pass" and dte_rule["data_refs"]["new_dte"] == 90


def test_conditional_post_earnings_window_is_not_eligible_without_stabilization_evidence():
    s = scenario(later=[(50, quote()), (70, quote(9))])
    s[3]["earnings_analysis"]["next_earnings_date"] = expiry(40)
    candidate = build(s)["alternatives"]["CREDIT_EXTENDED"]
    assert candidate["status"] == "available"
    assert candidate["new_expiration"] == expiry(70)


def test_notifications_name_contracts_and_scoped_reviews_without_global_approval():
    activity = scoped_activity()
    activity["supervisor_view"] = scope_supervisor_review({
        "challenge_strength": "WEAK", "counter_arguments": [], "net_assessment": "ORIGINAL_HOLDS",
    }, activity)
    text = TelegramNotifier._format_roll_alert("TEST", "Monitor", activity)
    assert "DEFENSIVE" in text and "CREDIT_EXTENDED" in text
    assert expiry(28) in text and expiry(50) in text
    assert "before commissions" in text and "not guaranteed" in text
    assert "scoped audit unavailable" in text and "ORIGINAL_HOLDS" not in text
