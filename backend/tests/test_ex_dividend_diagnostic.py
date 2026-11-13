"""Authoritative, informative-only dividend-capture evidence."""

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest
from src.ex_dividend_diagnostic import build_ex_dividend_evidence
from src.rule_evaluator import build_rule_evaluation

NOW = datetime(2026, 11, 13, 15, tzinfo=timezone.utc)
EXPIRY = "2026-11-20"


def _evidence(*, ex_date=EXPIRY, calendar_date=None, spot=102, strike=100,
              quote=None, expiration=EXPIRY, option_type="call", dividends=None,
              now=NOW):
    quote = quote if quote is not None else {
        "bid": 2.25, "ask": 2.50,
        "_meta": {
            "stale": False, "carried": False, "quote_asof": NOW.isoformat(),
            "field_status": {"bid": "live", "ask": "live"},
        },
    }
    dividend_fields = {
        "ex_dividend_date_recent": {"value": ex_date},
        "dps_common_stock_prim_issue_fq": {"value": 0.8},
        "dps_common_stock_prim_issue_fy": {"value": 3.2},
    }
    if dividends is not None:
        dividend_fields = dividends
    data = {
        "overview": {"fundamentals": {"current_price": {"value": spot}}},
        "dividends": json.dumps({"dividends": dividend_fields}),
    }
    chain = {"calls": {"20261120": {"100.0": quote}}}
    return build_ex_dividend_evidence(
        data, chain, strike=strike, expiration=expiration,
        option_type=option_type, now=now, calendar_date=calendar_date,
    )


@pytest.mark.parametrize(
    "provider,saved,expected,source,conflict",
    [
        (None, EXPIRY, EXPIRY, "cosmos_calendar", False),
        ("bad", EXPIRY, EXPIRY, "cosmos_calendar", False),
        ("2026-11-12", EXPIRY, EXPIRY, "cosmos_calendar", False),
        (EXPIRY, "2026-11-19", EXPIRY, "yfinance", True),
        (None, None, None, "unknown", False),
        ("2026-11-12", "2026-11-11", None, "unknown", False),
        (1795132800, None, EXPIRY, "yfinance", False),
    ],
)
def test_upcoming_date_resolution_is_frozen_and_expiration_inclusive(provider, saved, expected, source, conflict):
    evidence = _evidence(ex_date=provider, calendar_date=saved)
    refs = evidence["data_refs"]
    assert refs["ex_dividend_date"] == expected
    assert refs["ex_dividend_date_source"] == source
    assert refs["calendar_conflict"] is conflict
    assert refs["spans_ex_dividend"] is (True if expected else None)
    assert evidence["status"] == ("warning" if expected else "unknown")


@pytest.mark.parametrize("spot,itm,distance,status", [
    (102, True, -2, "warning"),
    (100, False, 0, "informational"),
    (98, False, 2, "informational"),
    (None, None, None, "unknown"),
    (float("nan"), None, None, "unknown"),
])
def test_strike_is_only_itm_threshold_not_future_assignment_boundary(spot, itm, distance, status):
    evidence = _evidence(spot=spot, strike="100.00")
    refs = evidence["data_refs"]
    assert refs["itm_threshold"] == 100
    assert refs["currently_itm"] is itm
    assert refs["distance_to_strike"] == distance
    assert evidence["status"] == status
    assert "not a unique or guaranteed future assignment price" in evidence["detail"]
    assert "previous trading session" in evidence["detail"]
    if itm:
        assert refs["extrinsic_bid"] == 0.25
        assert refs["extrinsic_ask"] == 0.5
        assert refs["historical_dividend_exceeds_bid_extrinsic"] is True
    assert refs["confirmed_upcoming_dividend"] is None


@pytest.mark.parametrize("change", [
    {"bid": None}, {"ask": None}, {"bid": 0}, {"bid": -1},
    {"ask": float("inf")}, {"bid": float("nan")}, {"bid": "2.25"},
    {"bid": 3}, {"bid": 1.99}, {"_meta": {}},
    {"_meta": {"stale": True}},
    {"_meta": {"carried": True}},
    {"_meta": {"field_status": {"bid": "last_known_good", "ask": "live"}}},
    {"_meta": {"quote_asof": "bad"}},
    {"_meta": {"quote_asof": None}},
    {"_meta": {"quote_asof": "2026-11-13T15:00:00"}},
])
def test_invalid_or_noncurrent_quotes_never_assert_safety(change):
    quote = {
        "bid": 2.25, "ask": 2.5,
        "_meta": {
            "stale": False, "carried": False, "quote_asof": NOW.isoformat(),
            "field_status": {"bid": "live", "ask": "live"},
        },
    }
    if change.get("_meta"):
        quote["_meta"].update(change["_meta"])
    else:
        quote.update(change)
    evidence = _evidence(quote=quote)
    refs = evidence["data_refs"]
    assert evidence["status"] == "warning"
    assert refs["quote_sufficient"] is False
    assert refs["historical_dividend_exceeds_bid_extrinsic"] is None
    assert "Quote evidence insufficient" in evidence["detail"]
    if change.get("bid") == 1.99:
        assert refs["raw_extrinsic_bid"] < 0
        assert refs["extrinsic_bid"] is None
        assert "not zero time value" in evidence["detail"]
    if "bid" in change and change["bid"] is None:
        assert refs["extrinsic_bid"] is None


@pytest.mark.parametrize("dividends", [
    {"ex_dividend_date_recent": {"formatted": EXPIRY}},
    {"ex_dividend_date_recent": {"value": EXPIRY}, "dps_common_stock_prim_issue_fy": {"value": 99}},
    {"ex_dividend_date_recent": {"value": EXPIRY}, "dps_common_stock_prim_issue_fq": {"value": float("nan")}},
])
def test_annual_or_missing_dividend_is_not_an_upcoming_payment(dividends):
    evidence = _evidence(dividends=dividends)
    refs = evidence["data_refs"]
    assert refs["confirmed_upcoming_dividend"] is None
    assert refs["historical_last_dividend"] is None
    assert refs["historical_dividend_exceeds_bid_extrinsic"] is None
    assert evidence["status"] == "warning"


def test_past_date_on_utc_clock_can_still_be_current_us_market_date():
    evidence = _evidence(ex_date="2026-11-12", now=datetime(2026, 11, 13, 1, tzinfo=timezone.utc))
    assert evidence["data_refs"]["ex_dividend_date"] == "2026-11-12"
    assert evidence["data_refs"]["date_basis"] == "America/New_York"


@pytest.mark.parametrize("dividends", ["malformed", "[]", None, {"dividends": []}])
def test_malformed_provider_data_remains_unknown(dividends):
    evidence = build_ex_dividend_evidence(
        {"dividends": dividends, "overview": "[]", "options_chain": "null"},
        {}, strike=100, expiration=EXPIRY, option_type="call", now=NOW,
    )
    assert evidence["status"] == "unknown"
    assert evidence["data_refs"]["underlying_price"] is None


@pytest.mark.parametrize("phase", ["assessment", "roll"])
def test_authoritative_evidence_overrides_model_rule_but_not_actions_or_other_rules(phase):
    rule_id = "ocm_a_ex_div_risk" if phase == "assessment" else "ocm_r_ex_div_risk"
    activity = {
        "activity": "WAIT", "action": "WAIT", "risk_flags": [],
        "underlying_price": 1, "current_strike": 999,
        "rule_checks": {rule_id: {"status": "pass", "detail": "Invented model safety"}},
        "ex_dividend_evidence": {"status": "pass"},
    }
    original = copy.deepcopy(activity)
    before = build_rule_evaluation("open_call_monitor", activity, phase=phase)
    after = build_rule_evaluation("open_call_monitor", activity, phase=phase, ex_dividend_evidence=_evidence())
    old_rules = {rule["rule_id"]: rule for rule in before["phases"][0]["rules"]}
    new_rules = {rule["rule_id"]: rule for rule in after["phases"][0]["rules"]}
    diagnostic = new_rules.pop(rule_id)
    old_rules.pop(rule_id, None)
    assert diagnostic["status"] == "warning"
    assert diagnostic["blocking"] is False
    assert diagnostic["source"] == "deterministic"
    assert diagnostic["data_refs"]["strike"] == 100
    assert diagnostic["data_refs"]["underlying_price"] == 102
    assert activity == original
    assert old_rules == new_rules


def test_puts_and_known_european_calls_do_not_get_american_dividend_capture_claim():
    assert _evidence(option_type="put")["status"] == "not_applicable"
    assert _evidence(quote={"exercise_style": "european"})["status"] == "not_applicable"
    activity = {"activity": "WAIT"}
    before = build_rule_evaluation("open_put_monitor", activity, phase="assessment")
    after = build_rule_evaluation("open_put_monitor", activity, phase="assessment", ex_dividend_evidence=_evidence())
    assert before == after


@pytest.mark.parametrize("case", ["wait", "missing_ask", "regular_roll", "dual_roll"])
def test_monitor_persists_exact_contract_diagnostics_without_changing_policy_or_review_inputs(monkeypatch, case):
    from src.rule_evaluator import add_ex_dividend_diagnostic, merge_phase_evaluations

    from tests.test_force_alpha_execution import (
        _FakeContext,
        _FakeFetcher,
        _monitor_runner_fixture,
        _run,
        ar_mod,
    )

    now = datetime.now(timezone.utc)
    exp = lambda days: (now.date() + timedelta(days=days)).isoformat()
    rolling = case in ("regular_roll", "dual_roll")
    action = "ROLL_UP_AND_OUT" if case == "dual_roll" else "ROLL_OUT"
    target_strike = 105 if case == "dual_roll" else 100
    primary = {
        "activity": action, "new_strike": target_strike, "new_expiration": exp(28),
        "risk_flags": ["test_existing_flag"],
        "roll_economics": {"buyback_cost": 2.5, "new_premium": 1.5, "net_credit": -1},
    } if rolling else None
    bucket = {}
    for days, strike, bid, ask in [(7, 100, 2.25, 2.5), (28, target_strike, 1.5, 1.6), (50, target_strike, 3.5, 3.6)]:
        bucket[exp(days).replace("-", "")] = {str(float(strike)): {
            "bid": bid, "ask": None if case == "missing_ask" and days == 7 else ask,
            "delta": 0.3, "theta": -0.02, "openInterest": 100,
            "_meta": {"greeks_valid": True, "quote_asof": now.isoformat()},
        }}
    market = {
        "overview": "{}", "technicals": "{}", "forecast": "{}",
        "dividends": json.dumps({"dividends": {
            "ex_dividend_date_recent": {"value": exp(-1)},
            "dps_common_stock_prim_issue_fq": {"value": 0.8},
        }}),
        "options_chain": json.dumps({"underlying_price": 102, "calls": bucket, "puts": {}}),
    }

    def run_monitor(include_diagnostic):
        runner, cosmos, _, state = _monitor_runner_fixture(monkeypatch, roll_activity=primary)
        monkeypatch.setattr(ar_mod, "build_rule_evaluation", build_rule_evaluation)
        monkeypatch.setattr(ar_mod, "merge_phase_evaluations", merge_phase_evaluations)
        monkeypatch.setattr(
            ar_mod, "add_ex_dividend_diagnostic",
            add_ex_dividend_diagnostic if include_diagnostic else lambda evaluation, evidence, phase: evaluation,
        )
        calendar_calls = []

        def calendar(symbol, event_type):
            calendar_calls.append((symbol, event_type))
            return exp(3)

        monkeypatch.setattr(cosmos, "get_next_calendar_event_date", calendar, raising=False)
        handoff = {
            "symbol": "TEST", "activity": "WAIT",
            "action_needed": action if rolling else None,
            "current_strike": 999, "current_expiration": exp(7),
            "underlying_price": 999,
            "earnings_analysis": {"next_earnings_date": exp(120)},
        }

        async def assessment(**kwargs):
            return "SUMMARY: TEST | monitor", None if rolling else handoff, handoff if rolling else None, None

        reviews = []

        async def review(**kwargs):
            reviews.append(copy.deepcopy(kwargs["activity_payload"]))

        monkeypatch.setattr(runner, "_run_position_assessment", assessment)
        monkeypatch.setattr(runner, "_run_supervisor_review", review)
        monkeypatch.setattr(runner, "_run_alpha_review", review)
        _run(runner.run_position_monitor(
            name="Monitor", symbol="TEST", exchange="NASDAQ",
            position={"position_id": "position-1", "type": "call", "strike": 100,
                      "expiration": exp(7), "contracts": 3},
            agent_type="open_call_monitor", cosmos=cosmos,
            context_provider=_FakeContext(), fetcher=_FakeFetcher(market),
            assessment_instructions="test", roll_instructions="test",
        ))
        assert calendar_calls == [("TEST", "ex_dividend")]
        assert len(cosmos.activities) == 1
        assert state["phase2_calls"] == int(rolling)
        return cosmos.activities[0], reviews

    baseline, baseline_reviews = run_monitor(False)
    activity, reviews = run_monitor(True)

    def strip_reporting(payload):
        payload = copy.deepcopy(payload)
        for field in ("run_id", "timestamp", "rule_evaluation"):
            payload.pop(field, None)
        for alternative in payload.get("roll_alternatives", {}).get("alternatives", {}).values():
            alternative.pop("rule_evaluation", None)
        return payload

    assert strip_reporting(activity) == strip_reporting(baseline)
    assert len(reviews) == len(baseline_reviews)
    for actual, expected in zip(reviews, baseline_reviews):
        for payload in (actual, expected):
            payload.pop("timestamp", None)
            payload.pop("run_id", None)
            payload["rule_evaluation"].pop("generated_at", None)
            for alternative in payload.get("roll_alternatives", {}).get("alternatives", {}).values():
                alternative.get("rule_evaluation", {}).pop("generated_at", None)
        assert actual == expected

    def diagnostic(evaluation, phase):
        block = next(block for block in evaluation["phases"] if block["phase"] == phase)
        return next(rule for rule in block["rules"] if rule["label"] == "Ex-Dividend Early-Assignment Diagnostic")

    held = diagnostic(activity["rule_evaluation"], "assessment")
    assert held["blocking"] is False
    assert held["status"] == "warning"
    refs = held["data_refs"]
    assert refs["strike"] == 100 and refs["underlying_price"] == 102
    assert refs["expiration"] == exp(7)
    assert refs["extrinsic_bid"] == 0.25
    assert refs["ex_dividend_date_source"] == "cosmos_calendar"
    assert refs["quote_sufficient"] is (case != "missing_ask")
    if rolling:
        target = diagnostic(activity["rule_evaluation"], "roll")["data_refs"]
        assert target["strike"] == target_strike and target["expiration"] == exp(28)
        assert target["bid"] == 1.5
        assert target["extrinsic_bid"] == (1.5 if target_strike == 105 else None)
    if case == "dual_roll":
        alternatives = activity["roll_alternatives"]["alternatives"]
        for alternative_id, days, bid in [("DEFENSIVE", 28, 1.5), ("CREDIT_EXTENDED", 50, 3.5)]:
            alternative = alternatives[alternative_id]
            assert alternative["status"] == "available"
            refs = diagnostic(alternative["rule_evaluation"], "roll")["data_refs"]
            assert refs["scope"] == alternative_id
            assert refs["strike"] == 105
            assert refs["expiration"] == exp(days)
            assert refs["bid"] == refs["extrinsic_bid"] == bid
            assert refs["spans_ex_dividend"] is True
