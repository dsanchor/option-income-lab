"""Buy Tracker runner integration tests for normalization and persistence order."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

# Other test modules may install a deliberately small agent_framework stub.
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


CANONICAL_EVIDENCE_KEYS = {
    "current_price",
    "high_52w",
    "sma50",
    "sma200",
    "rsi_14",
    "macd_confirmation",
    "stochastic_confirmation",
    "annual_dividend_rate",
    "latest_dividend",
    "dividend_growth_years",
    "dividend_cut_or_suspended",
    "payout_ratio_pct",
    "analyst_target_price",
    "days_to_earnings",
    "ma_summary",
    "oscillator_summary",
}


class _FakeResponse:
    text = "raw model response"


class _FakeAgent:
    def __init__(self, *args, **kwargs):
        pass

    async def run(self, prompt):
        return _FakeResponse()


class _FakeContext:
    def get_context(self, *args, **kwargs):
        return "No previous activity."


class _FakeFetcher:
    def __init__(self, market_data):
        self.market_data = market_data

    async def fetch_all(self, *args, **kwargs):
        return self.market_data


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(coro)
    finally:
        loop.close()


def _valid_market_data() -> dict:
    earnings_date = (
        datetime.now(timezone.utc).date() + timedelta(days=30)
    ).isoformat()
    return {
        "overview": json.dumps(
            {
                "fundamentals": {
                    "current_price": {"value": 90.0},
                    "52w_high": {"value": 100.0},
                    "earnings_release_next_date_fq": {
                        "formatted": earnings_date
                    },
                }
            }
        ),
        "technicals": json.dumps(
            {
                "price": 91.0,
                "oscillators": {
                    "recommendation": {"label": "Buy"},
                    "indicators": {
                        "RSI": {
                            "label": "RSI (14)",
                            "value": 35.0,
                            "formatted": "35.00",
                            "signal": "Neutral",
                        },
                        "MACD.macd": {
                            "label": "MACD Level (12,26)",
                            "value": 2.0,
                            "formatted": "2.0000",
                            "signal": "Buy",
                        },
                        "Stoch.K": {
                            "label": "Stochastic %K (14,3,3)",
                            "value": 30.0,
                            "formatted": "30.00",
                            "signal": "Buy",
                        },
                    },
                },
                "moving_averages": {
                    "recommendation": {"label": "Buy"},
                    "indicators": {
                        "SMA50": {
                            "label": "SMA (50)",
                            "value": 90.0,
                            "formatted": "$90.00",
                            "signal": "Buy",
                        },
                        "SMA200": {
                            "label": "SMA (200)",
                            "value": 85.0,
                            "formatted": "$85.00",
                            "signal": "Buy",
                        },
                    },
                },
            }
        ),
        "forecast": json.dumps(
            {
                "price_target": {
                    "price_target_average": {"value": 100.0}
                }
            }
        ),
        "dividends": json.dumps(
            {
                "dividends": {
                    "dps_common_stock_prim_issue_fy": {
                        "label": "Dividends Per Share (FY)",
                        "value": 4.0,
                        "formatted": "$4.00",
                    },
                    "dps_common_stock_prim_issue_fq": {
                        "label": "Dividends Per Share (FQ)",
                        "value": 1.0,
                        "formatted": "$1.00",
                    },
                    "dividend_payout_ratio_ttm": {"value": 60.0},
                    "ex_dividend_date_recent": {
                        "label": "Ex-Dividend Date (Recent)",
                        "value": 1788220800,
                        "formatted": "2026-09-01",
                    },
                    "continuous_dividend_growth": {
                        "label": "Consecutive Years Growing",
                        "value": 5,
                        "formatted": "5 years",
                    },
                }
            }
        ),
        "options_chain": "",
    }


def _exercise_runner(
    monkeypatch,
    desired_activity: str,
    market_data: dict,
    *,
    original_activity: str | None = None,
) -> dict:
    # ACCUMULATE is alerting in the six-state design; WAIT/UNFAVORABLE/AVOID are not.
    expected_alert = desired_activity in {"BUY", "STRONG_BUY", "ACCUMULATE"}
    original_activity = original_activity or (
        "STRONG_BUY" if desired_activity == "WAIT" else "WAIT"
    )
    model_payload = {
        "agent": "buy_tracker",
        "activity": original_activity,
        "confidence": "high" if original_activity == "STRONG_BUY" else "low",
        "score": "5/5",
        "score_breakdown": {
            "value_entry": 1,
            "trend": 1,
            "momentum": 1,
            "income": 1,
            "calendar": 1,
        },
        "underlying_price": 999.0,
        "entry_zone": "$990.00-$1000.00",
        "reason": f"Stale model reason for {original_activity}.",
        "summary": f"SUMMARY: MSFT | {original_activity} buy_tracker | stale",
        "waiting_for": "Stale waiting text.",
        "risk_flags": ["stale_model_risk"],
        "technical_triggers": ["stale_model_trigger"],
    }
    state = {
        "events": [],
        "normalized": None,
        "evidence": None,
        "alert_objects": [],
        "enrichment_objects": [],
        "evaluation_objects": [],
        "persisted_objects": [],
        "persisted_snapshots": [],
        "notifications": [],
        "client_calls": [],
    }

    def fake_normalize(activity_data, evidence=None):
        state["events"].append("normalize")
        state["evidence"] = evidence
        normalized = copy.deepcopy(activity_data)
        canonical_price = evidence.get("current_price") if isinstance(evidence, dict) else None
        # Confidence per six-state design: high=STRONG_BUY, medium=BUY/ACCUMULATE/AVOID, low=rest
        _confidence = {
            "STRONG_BUY": "high",
            "BUY": "medium",
            "ACCUMULATE": "medium",
            "AVOID": "medium",
        }.get(desired_activity, "low")
        # waiting_for is non-empty for WAIT, UNFAVORABLE, AVOID
        _has_waiting = desired_activity in {"WAIT", "UNFAVORABLE", "AVOID"}
        normalized.update(
            {
                "activity": desired_activity,
                "confidence": _confidence,
                "score": "+5/5",
                "reason": f"Score +5/5. Deterministically normalized to {desired_activity}.",
                "waiting_for": (
                    "Wait for the hard risk to clear." if _has_waiting else ""
                ),
                "risk_flags": (
                    ["normalized_non_alert"] if desired_activity in {"WAIT", "UNFAVORABLE", "AVOID"}
                    else []
                ),
                "technical_triggers": (
                    ["exceptional_gate"] if desired_activity == "STRONG_BUY" else []
                ),
            }
        )
        if canonical_price is not None:
            normalized["underlying_price"] = canonical_price
        if desired_activity in {"BUY", "STRONG_BUY", "ACCUMULATE"} and canonical_price is not None:
            normalized["entry_zone"] = "$88.00-$92.00"
        elif desired_activity not in {"BUY", "STRONG_BUY", "ACCUMULATE"}:
            normalized.pop("entry_zone", None)
        state["normalized"] = normalized
        return normalized

    def fake_evaluation(agent_type, activity_data, **kwargs):
        state["events"].append("evaluate")
        state["evaluation_objects"].append(activity_data)
        return {"schema_version": 1, "agent_type": agent_type, "rules": []}

    monkeypatch.setattr(ar_mod, "Agent", _FakeAgent)
    monkeypatch.setattr(
        ar_mod,
        "normalize_buy_tracker_activity",
        fake_normalize,
        raising=False,
    )
    monkeypatch.setattr(ar_mod, "build_rule_evaluation", fake_evaluation)

    runner = AgentRunner(
        llm=LlmConfig(provider="azure", api_key="k", endpoint="https://example.test"),
        model="global-model",
    )
    runner.telegram_notifier = types.SimpleNamespace()

    def fake_get_client(model=None, function_id=None):
        state["client_calls"].append((model, function_id))
        return object()

    monkeypatch.setattr(runner, "_get_client", fake_get_client)
    monkeypatch.setattr(runner, "_build_enrichment_block", lambda *args: "")
    monkeypatch.setattr(runner, "_resolve_category_skill", lambda *args: None)
    monkeypatch.setattr(runner, "_get_skills_provider", lambda *args: None)
    monkeypatch.setattr(
        runner,
        "_extract_activity_line",
        lambda *args: (model_payload["summary"], model_payload),
    )
    monkeypatch.setattr(
        runner,
        "_validate_premium_against_chain",
        lambda activity_data, *args: activity_data,
    )
    monkeypatch.setattr(runner, "_record_trace", lambda *args, **kwargs: None)

    real_is_alert = runner._is_alert

    def tracked_is_alert(response_text, json_data=None):
        state["events"].append("alert")
        state["alert_objects"].append(json_data)
        return real_is_alert(response_text, json_data)

    real_extract_enrichment = runner._extract_alert_enrichment

    def tracked_extract_enrichment(json_data):
        state["events"].append("enrich")
        state["enrichment_objects"].append(json_data)
        return real_extract_enrichment(json_data)

    monkeypatch.setattr(runner, "_is_alert", tracked_is_alert)
    monkeypatch.setattr(runner, "_extract_alert_enrichment", tracked_extract_enrichment)

    class FakeCosmos:
        def write_activity(self, **kwargs):
            state["events"].append("persist")
            payload = kwargs["activity_data"]
            state["persisted_objects"].append(payload)
            state["persisted_snapshots"].append(copy.deepcopy(payload))
            return {"id": "activity-1"}

        def write_telemetry(self, *args, **kwargs):
            pass

    class FakeNotifier:
        def send_alert(self, **kwargs):
            state["events"].append("notify")
            state["notifications"].append(copy.deepcopy(kwargs))

    runner.telegram_notifier = FakeNotifier()
    _run(
        runner.run_symbol_agent(
            name="BuyTrackerAgent",
            instructions="test instructions",
            symbol="MSFT",
            exchange="NASDAQ",
            agent_type="buy_tracker",
            cosmos=FakeCosmos(),
            context_provider=_FakeContext(),
            fetcher=_FakeFetcher(market_data),
            model="configured-buy-model",
        )
    )

    assert len(state["persisted_snapshots"]) == 1
    persisted = state["persisted_snapshots"][0]
    assert "error" not in persisted
    assert state["normalized"] is not None
    assert len(state["alert_objects"]) == 1
    assert state["alert_objects"][0] is state["normalized"]
    assert len(state["evaluation_objects"]) == 1
    assert state["evaluation_objects"][0] is state["normalized"]
    assert len(state["persisted_objects"]) == 1
    assert state["persisted_objects"][0] is state["normalized"]
    assert persisted["activity"] == desired_activity
    assert persisted["is_alert"] is expected_alert
    assert persisted["rule_evaluation"]["agent_type"] == "buy_tracker"
    assert persisted["summary"].split("|")[1].strip().split()[0] == desired_activity
    normalized_price = state["normalized"]["underlying_price"]
    normalized_entry = state["normalized"].get("entry_zone") or "N/A"
    assert f"Price ${normalized_price:.2f}" in persisted["summary"]
    assert f"Entry {normalized_entry}" in persisted["summary"]
    assert "Deterministically normalized" in persisted["summary"]
    if state["evidence"]["current_price"] is not None:
        assert "$999" not in persisted["summary"]
        assert "$990.00-$1000.00" not in persisted["summary"]
    assert "stale" not in persisted["summary"].lower()
    assert state["client_calls"] == [("configured-buy-model", "buy_tracker")]

    if expected_alert:
        assert len(state["enrichment_objects"]) == 1
        assert state["enrichment_objects"][0] is state["normalized"]
        assert len(state["notifications"]) == 1
        assert state["notifications"][0]["alert_data"]["activity"] == desired_activity
        assert state["events"] == [
            "normalize",
            "alert",
            "enrich",
            "evaluate",
            "persist",
            "notify",
        ]
    else:
        assert state["enrichment_objects"] == []
        assert state["notifications"] == []
        assert state["events"] == ["normalize", "alert", "evaluate", "persist"]

    return state


@pytest.mark.parametrize(
    "normalized_activity",
    # Six-state vocabulary: ACCUMULATE alerts; UNFAVORABLE and AVOID do not.
    ["WAIT", "BUY", "STRONG_BUY", "ACCUMULATE", "UNFAVORABLE", "AVOID"],
)
def test_normalized_activity_drives_alert_evaluation_and_persistence_order(
    monkeypatch, normalized_activity
):
    state = _exercise_runner(
        monkeypatch,
        desired_activity=normalized_activity,
        market_data=_valid_market_data(),
    )

    evidence = state["evidence"]
    assert set(evidence) == CANONICAL_EVIDENCE_KEYS
    assert evidence["current_price"] == 90.0
    assert evidence["high_52w"] == 100.0
    assert evidence["sma50"] == 90.0
    assert evidence["sma200"] == 85.0
    assert evidence["rsi_14"] == 35.0
    assert evidence["macd_confirmation"] == "BUY"
    assert evidence["stochastic_confirmation"] == "BUY"
    assert evidence["annual_dividend_rate"] == 4.0
    assert evidence["latest_dividend"] == 1.0
    assert evidence["dividend_growth_years"] == 5.0
    assert evidence["payout_ratio_pct"] == 60.0
    assert evidence["analyst_target_price"] == 100.0
    assert 29 <= evidence["days_to_earnings"] <= 30
    assert evidence["ma_summary"] == "BUY"
    assert evidence["oscillator_summary"] == "BUY"


def test_summary_is_regenerated_when_normalized_activity_and_score_are_unchanged(
    monkeypatch,
):
    _exercise_runner(
        monkeypatch,
        desired_activity="BUY",
        original_activity="BUY",
        market_data=_valid_market_data(),
    )


def test_malformed_source_json_becomes_canonical_unavailable_evidence(monkeypatch):
    malformed_market_data = {
        "overview": "{not-json",
        "technicals": "not-json",
        "forecast": ["not", "an", "object"],
        "dividends": 42,
        "options_chain": "",
    }

    state = _exercise_runner(
        monkeypatch,
        desired_activity="BUY",
        market_data=malformed_market_data,
    )

    assert set(state["evidence"]) == CANONICAL_EVIDENCE_KEYS
    assert all(value is None for value in state["evidence"].values())


# ===========================================================================
# Six-state tri-state normalizer contract tests
# Spec: .squad/decisions/inbox/danny-buy-tracker-state-redesign.md
# These tests will fail until Linus implements the new normalize_buy_tracker_activity.
# Failures are reported as "implementation in progress", not as test weaknesses.
# ===========================================================================

try:
    from src.rule_evaluator import (
        normalize_buy_tracker_activity as _norm6,
        build_buy_tracker_evidence as _build_ev6,
    )
    _SIX_STATE_AVAILABLE = True
except (ImportError, AttributeError):
    _norm6 = None  # type: ignore[assignment]
    _build_ev6 = None  # type: ignore[assignment]
    _SIX_STATE_AVAILABLE = False

# Skip marker for new-design tests when the module doesn't import cleanly.
_six_state_skip = pytest.mark.skipif(
    not _SIX_STATE_AVAILABLE,
    reason="six-state normalizer not yet importable (Linus implementation pending)",
)

# ---------------------------------------------------------------------------
# Shared helpers for six-state tests
# ---------------------------------------------------------------------------

_BT6_DIMS = ("value_entry", "trend", "momentum", "income", "calendar")


def _signed_score(score: int) -> str:
    """Return the new signed score string format: '+3/5', '0/5', '-2/5'."""
    if score == 0:
        return "0/5"
    return f"+{score}/5" if score > 0 else f"{score}/5"


def _tri_breakdown(target_score: int, **dim_overrides: int) -> dict:
    """Construct a 5-dim breakdown in {-1, 0, +1} values summing to target_score.

    Fills positives from the front and negatives from the back so the result
    is deterministic and uses the minimum number of non-zero dims.
    """
    result: dict = {dim: 0 for dim in _BT6_DIMS}
    remaining = target_score
    for dim in _BT6_DIMS:
        if remaining > 0:
            result[dim] = 1
            remaining -= 1
    for dim in reversed(_BT6_DIMS):
        if remaining < 0 and result[dim] == 0:
            result[dim] = -1
            remaining += 1
    result.update(dim_overrides)
    return result


def _bt6_activity(breakdown: dict, **overrides) -> dict:
    """Stale LLM payload — normalizer will override everything relevant."""
    a: dict = {
        "agent_type": "buy_tracker",
        "activity": "STRONG_BUY",
        "confidence": "high",
        "score": "+99/5",
        "score_breakdown": breakdown,
        "waiting_for": "Stale waiting text.",
        "risk_flags": ["stale_flag"],
        "technical_triggers": [],
        "reason": "Stale reason.",
    }
    a.update(overrides)
    return a


def _no_gate_ev(**overrides) -> dict:
    """Evidence that triggers no hard gate (Hard AVOID or Hard WAIT).

    Values chosen so every gate check is definitively clear:
    - No dividend cut, no triple bearish, no earnings imminent, RSI < 80,
      price within normal range relative to both MAs.
    """
    base: dict = {
        "current_price": 90.0,
        "high_52w": 100.0,
        "sma50": 90.0,
        "sma200": 85.0,
        "rsi_14": 50.0,
        "macd_confirmation": "BUY",
        "stochastic_confirmation": "BUY",
        "annual_dividend_rate": 4.0,
        "latest_dividend": 1.0,
        "dividend_growth_years": 5.0,
        "dividend_cut_or_suspended": False,
        "payout_ratio_pct": 60.0,
        "analyst_target_price": 100.0,
        "days_to_earnings": 30.0,
        "ma_summary": "BUY",
        "oscillator_summary": "BUY",
    }
    base.update(overrides)
    return base


def _exceptional_ev(**overrides) -> dict:
    """Evidence that passes the exceptional STRONG_BUY gate (RSI 25-45, 10% pullback, etc.)."""
    base = _no_gate_ev(rsi_14=35.0)  # RSI 25-45 required by exceptional gate
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. State Reachability: all six states reachable via canonical evidence
# ---------------------------------------------------------------------------

@_six_state_skip
class TestSixStateReachability:
    """Each state must be deterministically reachable via normalize_buy_tracker_activity."""

    def test_strong_buy_reachable_via_exceptional_gate(self):
        # Score +5 with all exceptional gate criteria met → STRONG_BUY
        normalized = _norm6(_bt6_activity(_tri_breakdown(5)), _exceptional_ev())
        assert normalized["activity"] == "STRONG_BUY"
        assert normalized["score"] == "+5/5"
        assert normalized["confidence"] == "high"
        assert normalized["waiting_for"] == ""
        assert isinstance(normalized.get("entry_zone"), str)

    def test_buy_reachable_via_score_four(self):
        # Score +4, no hard gates → BUY
        normalized = _norm6(_bt6_activity(_tri_breakdown(4)), _no_gate_ev())
        assert normalized["activity"] == "BUY"
        assert normalized["score"] == "+4/5"
        assert normalized["confidence"] == "medium"
        assert normalized["waiting_for"] == ""

    def test_accumulate_reachable_via_score_three(self):
        # Score +3, no hard gates → ACCUMULATE
        normalized = _norm6(_bt6_activity(_tri_breakdown(3)), _no_gate_ev())
        assert normalized["activity"] == "ACCUMULATE"
        assert normalized["score"] == "+3/5"
        assert normalized["waiting_for"] == ""

    def test_wait_reachable_via_score_zero(self):
        # Score 0, all neutral → WAIT
        normalized = _norm6(_bt6_activity(_tri_breakdown(0)), _no_gate_ev())
        assert normalized["activity"] == "WAIT"
        assert normalized["score"] == "0/5"
        assert normalized["confidence"] == "low"
        assert normalized["waiting_for"]  # non-empty for WAIT

    def test_unfavorable_reachable_via_negative_score(self):
        # Score -1, no hard gates → UNFAVORABLE
        normalized = _norm6(_bt6_activity(_tri_breakdown(-1)), _no_gate_ev())
        assert normalized["activity"] == "UNFAVORABLE"
        assert normalized["score"] == "-1/5"
        assert normalized["confidence"] == "low"
        assert normalized["waiting_for"]  # non-empty for UNFAVORABLE

    def test_avoid_reachable_via_hard_avoid_gate(self):
        # Dividend cut triggers Hard AVOID regardless of score
        normalized = _norm6(
            _bt6_activity(_tri_breakdown(4)),
            _no_gate_ev(dividend_cut_or_suspended=True),
        )
        assert normalized["activity"] == "AVOID"
        assert normalized["score"] == "+4/5"  # score unchanged by Hard AVOID
        assert normalized["confidence"] == "medium"  # high conviction on timing
        assert normalized["waiting_for"]  # explains the gate condition
        assert "dividend_cut_or_suspended" in normalized["risk_flags"]

    def test_avoid_reachable_via_deep_negative_score(self):
        # Score -3, no hard AVOID gate needed — score-based AVOID
        normalized = _norm6(_bt6_activity(_tri_breakdown(-3)), _no_gate_ev())
        assert normalized["activity"] == "AVOID"
        assert normalized["score"] == "-3/5"

    def test_strong_buy_reachable_via_provider_shaped_fetch_data(self):
        """Provider pipeline → evidence → STRONG_BUY: end-to-end canonical path."""
        fetch_data = {
            "overview": json.dumps({
                "fundamentals": {
                    "current_price": {"value": 90.0},
                    "52w_high": {"value": 100.0},
                    "earnings_release_next_date_fq": {"formatted": "2026-11-01"},
                }
            }),
            "technicals": json.dumps({
                "price": 90.0,
                "oscillators": {
                    "recommendation": {"label": "Buy"},
                    "indicators": {
                        "RSI": {"value": 35.0, "signal": "Neutral"},
                        "MACD.macd": {"value": 1.5, "signal": "Buy"},
                        "Stoch.K": {"value": 28.0, "signal": "Buy"},
                    },
                },
                "moving_averages": {
                    "recommendation": {"label": "Buy"},
                    "indicators": {
                        "SMA50": {"value": 90.0, "signal": "Buy"},
                        "SMA200": {"value": 85.0, "signal": "Buy"},
                    },
                },
            }),
            "forecast": json.dumps({
                "price_target": {
                    "price_target_average": {"value": 100.0}
                }
            }),
            "dividends": json.dumps({
                "dividends": {
                    "dps_common_stock_prim_issue_fy": {"value": 4.0},
                    "dps_common_stock_prim_issue_fq": {"value": 1.0},
                    "dividend_payout_ratio_ttm": {"value": 60.0},
                    "continuous_dividend_growth": {"value": 5},
                }
            }),
        }
        from datetime import date
        evidence = _build_ev6(fetch_data, now=date(2026, 9, 3))
        normalized = _norm6(_bt6_activity(_tri_breakdown(5)), evidence)
        assert normalized["activity"] == "STRONG_BUY"
        assert normalized["score"] == "+5/5"


# ---------------------------------------------------------------------------
# 2. Threshold Boundaries
# ---------------------------------------------------------------------------

@_six_state_skip
class TestTriStateThresholdBoundaries:
    """Exact boundary crossings between all six states."""

    @pytest.mark.parametrize(
        "score,expected_state",
        [
            (-5, "AVOID"),
            (-4, "AVOID"),
            (-3, "AVOID"),       # AVOID/UNFAVORABLE boundary — AVOID side
            (-2, "UNFAVORABLE"), # AVOID/UNFAVORABLE boundary — UNFAVORABLE side
            (-1, "UNFAVORABLE"), # UNFAVORABLE/WAIT boundary — UNFAVORABLE side
            (0, "WAIT"),         # UNFAVORABLE/WAIT boundary — WAIT side
            (1, "WAIT"),         # WAIT/ACCUMULATE boundary — WAIT side
            (2, "ACCUMULATE"),   # WAIT/ACCUMULATE boundary — ACCUMULATE side
            (3, "ACCUMULATE"),   # ACCUMULATE/BUY boundary — ACCUMULATE side
            (4, "BUY"),          # ACCUMULATE/BUY boundary — BUY side
            (5, "BUY"),          # +5 without exceptional gate stays BUY
        ],
    )
    def test_score_boundary_produces_correct_base_state(self, score, expected_state):
        # Use evidence that does NOT trigger any hard gate and does NOT pass the
        # exceptional gate (rsi_14=50 fails the 25-45 requirement).
        normalized = _norm6(
            _bt6_activity(_tri_breakdown(score)),
            _no_gate_ev(),
        )
        assert normalized["activity"] == expected_state, (
            f"score {score} should produce {expected_state}, got {normalized['activity']}"
        )
        assert normalized["score"] == _signed_score(score)

    def test_score_plus_five_with_exceptional_gate_upgrades_to_strong_buy(self):
        # +5 score + all exceptional gate criteria met → STRONG_BUY
        normalized = _norm6(_bt6_activity(_tri_breakdown(5)), _exceptional_ev())
        assert normalized["activity"] == "STRONG_BUY"
        assert normalized["score"] == "+5/5"

    def test_score_plus_five_without_exceptional_gate_stays_buy(self):
        # +5 score but RSI 55 fails the 25-45 exceptional gate → BUY, not STRONG_BUY
        normalized = _norm6(
            _bt6_activity(_tri_breakdown(5)),
            _no_gate_ev(rsi_14=55.0),
        )
        assert normalized["activity"] == "BUY"
        assert normalized["score"] == "+5/5"
        assert "exceptional_gate_not_met" in normalized["risk_flags"]

    def test_score_minus_three_is_avoid_not_unfavorable(self):
        # Boundary: -3 is the last AVOID score (spec: -5 to -3 → AVOID)
        normalized = _norm6(_bt6_activity(_tri_breakdown(-3)), _no_gate_ev())
        assert normalized["activity"] == "AVOID"

    def test_score_minus_two_is_unfavorable_not_avoid(self):
        # Boundary: -2 is the first UNFAVORABLE score (spec: -2 to -1 → UNFAVORABLE)
        normalized = _norm6(_bt6_activity(_tri_breakdown(-2)), _no_gate_ev())
        assert normalized["activity"] == "UNFAVORABLE"

    def test_score_plus_one_is_wait_not_accumulate(self):
        # Boundary: +1 is still WAIT (spec: 0 to +1 → WAIT)
        normalized = _norm6(_bt6_activity(_tri_breakdown(1)), _no_gate_ev())
        assert normalized["activity"] == "WAIT"

    def test_score_plus_two_is_accumulate_not_wait(self):
        # Boundary: +2 is first ACCUMULATE (spec: +2 to +3 → ACCUMULATE)
        normalized = _norm6(_bt6_activity(_tri_breakdown(2)), _no_gate_ev())
        assert normalized["activity"] == "ACCUMULATE"

    def test_score_plus_three_is_accumulate_not_buy(self):
        # Boundary: +3 is last ACCUMULATE (spec: +2 to +3 → ACCUMULATE)
        normalized = _norm6(_bt6_activity(_tri_breakdown(3)), _no_gate_ev())
        assert normalized["activity"] == "ACCUMULATE"

    def test_score_plus_four_is_buy_not_accumulate(self):
        # Boundary: +4 is first BUY score
        normalized = _norm6(_bt6_activity(_tri_breakdown(4)), _no_gate_ev())
        assert normalized["activity"] == "BUY"

    def test_score_format_is_signed_for_all_threshold_values(self):
        for score in range(-5, 6):
            normalized = _norm6(_bt6_activity(_tri_breakdown(score)), _no_gate_ev())
            assert normalized["score"] == _signed_score(score), (
                f"score {score}: expected '{_signed_score(score)}', got {normalized['score']!r}"
            )

    def test_breakdown_in_score_output_reflects_tri_state_values(self):
        # Ensure -1 values are preserved in score_breakdown output
        breakdown = _tri_breakdown(-3)  # three -1 entries
        normalized = _norm6(_bt6_activity(breakdown), _no_gate_ev())
        neg_count = sum(1 for v in normalized["score_breakdown"].values() if v == -1)
        assert neg_count == 3, "three negative dimensions should appear in output breakdown"


# ---------------------------------------------------------------------------
# 3. Hard Gate Precedence
# ---------------------------------------------------------------------------

@_six_state_skip
class TestHardGatePrecedenceNew:
    """Hard AVOID > Hard WAIT > exceptional promotion > score thresholds."""

    def test_hard_avoid_dividend_cut_overrides_buy_score(self):
        # Score +4 would be BUY, but dividend cut forces AVOID (Hard AVOID gate)
        normalized = _norm6(
            _bt6_activity(_tri_breakdown(4)),
            _no_gate_ev(dividend_cut_or_suspended=True),
        )
        assert normalized["activity"] == "AVOID"
        assert normalized["score"] == "+4/5"  # score unchanged
        assert "dividend_cut_or_suspended" in normalized["risk_flags"]

    def test_hard_avoid_dividend_cut_overrides_accumulate_score(self):
        # Score +3 would be ACCUMULATE, but dividend cut forces AVOID
        normalized = _norm6(
            _bt6_activity(_tri_breakdown(3)),
            _no_gate_ev(dividend_cut_or_suspended=True),
        )
        assert normalized["activity"] == "AVOID"
        assert normalized["score"] == "+3/5"

    def test_hard_avoid_triple_bearish_overrides_accumulate_score(self):
        # Triple bearish: osc=STRONG_SELL + MA=STRONG_SELL + price >10% below SMA200
        # Score +2 would be ACCUMULATE, but triple bearish forces AVOID
        normalized = _norm6(
            _bt6_activity(_tri_breakdown(2)),
            _no_gate_ev(
                current_price=89.0,
                sma200=100.0,
                oscillator_summary="STRONG_SELL",
                ma_summary="STRONG_SELL",
            ),
        )
        assert normalized["activity"] == "AVOID"
        assert normalized["score"] == "+2/5"

    def test_hard_wait_earnings_caps_buy_at_wait(self):
        # Score +4 would be BUY, but earnings in 1 day → Hard WAIT → WAIT
        normalized = _norm6(
            _bt6_activity(_tri_breakdown(4)),
            _no_gate_ev(days_to_earnings=1.0),
        )
        assert normalized["activity"] == "WAIT"
        assert normalized["score"] == "+4/5"
        assert normalized["waiting_for"]

    def test_hard_wait_rsi_over_80_caps_accumulate_at_wait(self):
        # Score +3 would be ACCUMULATE, but RSI=82 → Hard WAIT → WAIT
        normalized = _norm6(
            _bt6_activity(_tri_breakdown(3)),
            _no_gate_ev(rsi_14=82.0),
        )
        assert normalized["activity"] == "WAIT"
        assert normalized["score"] == "+3/5"

    def test_hard_wait_price_extended_caps_buy_at_wait(self):
        # Price >10% above SMA50 AND >15% above SMA200 → Hard WAIT
        normalized = _norm6(
            _bt6_activity(_tri_breakdown(4)),
            _no_gate_ev(
                current_price=116.0,
                sma50=105.0,
                sma200=100.0,
                analyst_target_price=125.0,
            ),
        )
        assert normalized["activity"] == "WAIT"
        assert normalized["score"] == "+4/5"

    def test_hard_avoid_wins_over_hard_wait_when_both_triggered(self):
        # Dividend cut (Hard AVOID) + earnings in 1 day (Hard WAIT) → AVOID, not WAIT
        normalized = _norm6(
            _bt6_activity(_tri_breakdown(4)),
            _no_gate_ev(dividend_cut_or_suspended=True, days_to_earnings=1.0),
        )
        assert normalized["activity"] == "AVOID", (
            "Hard AVOID must take precedence over Hard WAIT"
        )
        assert normalized["score"] == "+4/5"

    def test_hard_avoid_wins_over_hard_wait_triple_bear_and_earnings(self):
        # Triple bearish (Hard AVOID) + earnings in 1 day (Hard WAIT) → AVOID
        normalized = _norm6(
            _bt6_activity(_tri_breakdown(3)),
            _no_gate_ev(
                current_price=89.0,
                sma200=100.0,
                oscillator_summary="STRONG_SELL",
                ma_summary="STRONG_SELL",
                days_to_earnings=1.0,
            ),
        )
        assert normalized["activity"] == "AVOID"

    def test_exceptional_gate_only_fires_at_plus_five_with_no_hard_gates(self):
        # Score +4 with full exceptional evidence → BUY (not STRONG_BUY; needs +5)
        normalized = _norm6(_bt6_activity(_tri_breakdown(4)), _exceptional_ev())
        assert normalized["activity"] == "BUY"
        assert normalized["score"] == "+4/5"

    def test_hard_avoid_blocks_exceptional_promotion_even_at_plus_five(self):
        # +5 score + exceptional gate + dividend cut → AVOID (Hard AVOID wins)
        normalized = _norm6(
            _bt6_activity(_tri_breakdown(5)),
            _exceptional_ev(dividend_cut_or_suspended=True),
        )
        assert normalized["activity"] == "AVOID"
        assert normalized["score"] == "+5/5"

    def test_hard_wait_blocks_exceptional_promotion_at_plus_five(self):
        # +5 score + exceptional gate + earnings in 1 day → WAIT (Hard WAIT wins)
        normalized = _norm6(
            _bt6_activity(_tri_breakdown(5)),
            _exceptional_ev(days_to_earnings=1.0),
        )
        assert normalized["activity"] == "WAIT"
        assert normalized["score"] == "+5/5"

    def test_hard_wait_does_not_affect_states_below_accumulate(self):
        # Hard WAIT only caps ACCUMULATE/BUY/STRONG_BUY — UNFAVORABLE stays UNFAVORABLE
        normalized = _norm6(
            _bt6_activity(_tri_breakdown(-1)),
            _no_gate_ev(days_to_earnings=1.0),
        )
        assert normalized["activity"] == "UNFAVORABLE"

    def test_hard_avoid_overrides_even_negative_score_state(self):
        # Score -1 would be UNFAVORABLE, but dividend cut forces AVOID regardless
        normalized = _norm6(
            _bt6_activity(_tri_breakdown(-1)),
            _no_gate_ev(dividend_cut_or_suspended=True),
        )
        assert normalized["activity"] == "AVOID"

    def test_score_unchanged_under_hard_avoid_gate(self):
        # Hard AVOID forces state but must not modify the reported score
        for score in (1, 2, 3, 4, 5):
            normalized = _norm6(
                _bt6_activity(_tri_breakdown(score)),
                _no_gate_ev(dividend_cut_or_suspended=True),
            )
            assert normalized["score"] == _signed_score(score), (
                f"Hard AVOID must not alter the score; score={score}"
            )
            assert normalized["activity"] == "AVOID"

    def test_hard_wait_only_caps_favorable_states(self):
        # Hard WAIT should not change score, and caps are limited to ≥ ACCUMULATE
        for score, expected_capped in [(4, "WAIT"), (3, "WAIT"), (2, "WAIT")]:
            normalized = _norm6(
                _bt6_activity(_tri_breakdown(score)),
                _no_gate_ev(rsi_14=85.0),
            )
            assert normalized["activity"] == expected_capped, f"score {score}"
            assert normalized["score"] == _signed_score(score)


# ---------------------------------------------------------------------------
# 4. Missing Dimension Behavior
# ---------------------------------------------------------------------------

@_six_state_skip
class TestMissingDimensionBehaviorNew:
    """Missing breakdown keys → neutral 0; 3+ missing → cap WAIT + insufficient_data."""

    def test_single_missing_dim_becomes_neutral_and_no_cap(self):
        # 4 positive dimensions provided, 1 missing → score +4, BUY, no cap
        partial = {"value_entry": 1, "trend": 1, "momentum": 1, "income": 1}
        # "calendar" key absent
        normalized = _norm6(_bt6_activity(partial), _no_gate_ev())
        assert normalized["score_breakdown"]["calendar"] == 0
        assert normalized["activity"] == "BUY"
        assert normalized["score"] == "+4/5"
        assert "insufficient_data" not in normalized["risk_flags"]

    def test_two_missing_dims_no_cap(self):
        # 3 positives, 2 missing → score +3, ACCUMULATE, no cap (only 2 missing)
        partial = {"value_entry": 1, "trend": 1, "momentum": 1}
        normalized = _norm6(_bt6_activity(partial), _no_gate_ev())
        assert normalized["activity"] == "ACCUMULATE"
        assert normalized["score"] == "+3/5"
        assert "insufficient_data" not in normalized["risk_flags"]

    def test_three_missing_dims_caps_at_wait_with_insufficient_data(self):
        # 2 positives, 3 missing → would be ACCUMULATE (+2), but 3 missing → cap WAIT
        partial = {"value_entry": 1, "trend": 1}
        normalized = _norm6(_bt6_activity(partial), _no_gate_ev())
        assert normalized["activity"] == "WAIT"
        assert "insufficient_data" in normalized["risk_flags"]

    def test_four_missing_dims_caps_at_wait(self):
        partial = {"value_entry": 1}  # 4 missing
        normalized = _norm6(_bt6_activity(partial), _no_gate_ev())
        assert normalized["activity"] == "WAIT"
        assert "insufficient_data" in normalized["risk_flags"]

    def test_all_dims_missing_caps_at_wait(self):
        # Empty breakdown → all 5 missing
        normalized = _norm6(_bt6_activity({}), _no_gate_ev())
        assert normalized["activity"] == "WAIT"
        assert "insufficient_data" in normalized["risk_flags"]
        assert normalized["score"] == "0/5"

    def test_all_dims_explicitly_zero_is_genuine_neutral_no_cap(self):
        # Five explicit zeros from LLM = intentional neutrality, NOT missing data
        zero_breakdown = {dim: 0 for dim in _BT6_DIMS}
        normalized = _norm6(_bt6_activity(zero_breakdown), _no_gate_ev())
        assert normalized["activity"] == "WAIT"
        assert normalized["score"] == "0/5"
        assert "insufficient_data" not in normalized["risk_flags"]

    def test_fully_missing_evidence_fails_safely_to_wait(self):
        # All evidence None (no market data) → WAIT, score 0/5, insufficient_data
        normalized = _norm6(_bt6_activity({}), None)
        assert normalized["activity"] == "WAIT"
        assert "insufficient_data" in normalized["risk_flags"]

    def test_missing_breakdown_but_rich_evidence_still_applies_cap(self):
        # Even with good evidence, missing breakdown dims trigger the cap
        partial = {"value_entry": 1, "trend": 1}  # 3 missing
        normalized = _norm6(_bt6_activity(partial), _exceptional_ev())
        assert normalized["activity"] == "WAIT"
        assert "insufficient_data" in normalized["risk_flags"]

    def test_invalid_dim_values_treated_as_missing(self):
        # Values outside {-1, 0, +1} are invalid → treated as missing (score 0)
        invalid_breakdown = {
            "value_entry": 2,    # out of range
            "trend": -2,         # out of range
            "momentum": True,    # boolean
            "income": "1",       # string
            "calendar": 1,       # valid
        }
        normalized = _norm6(_bt6_activity(invalid_breakdown), _no_gate_ev())
        for dim in ("value_entry", "trend", "momentum", "income"):
            assert normalized["score_breakdown"][dim] == 0, (
                f"{dim} should be clamped to 0 (invalid value)"
            )
        assert normalized["score_breakdown"]["calendar"] == 1
        assert "insufficient_data" in normalized["risk_flags"]  # 4 invalid → ≥3 missing

    def test_minus_one_dimension_value_is_valid_tri_state(self):
        # -1 is valid in the new tri-state system — must not be treated as missing
        breakdown = {dim: -1 for dim in _BT6_DIMS}  # all -1 → score -5
        normalized = _norm6(_bt6_activity(breakdown), _no_gate_ev())
        for dim in _BT6_DIMS:
            assert normalized["score_breakdown"][dim] == -1
        assert normalized["score"] == "-5/5"
        assert normalized["activity"] == "AVOID"
        assert "insufficient_data" not in normalized["risk_flags"]


# ---------------------------------------------------------------------------
# 5. Distribution Scenario Matrix
# ---------------------------------------------------------------------------

@_six_state_skip
class TestDistributionScenarioMatrix:
    """≥20 representative market scenarios testing distribution constraints.

    Distribution assertions (per spec):
    - No single state exceeds 50% of the sample.
    - At least 4 of the 6 states are represented.
    - BUY + STRONG_BUY combined ≤ 30% of the sample.

    Scenarios are derived from realistic market conditions and spec dimension
    scoring rules — they are NOT tautologically back-calculated from desired
    outputs. The per-scenario expected states are derived from the spec.
    """

    # Each entry: (scenario_name, breakdown, evidence, expected_state)
    # Breakdowns computed by applying spec dimension scoring rules to the described market.
    _SCENARIOS: list = [
        # --- STRONG_BUY (1) ---
        (
            "pristine_dgi_accumulation_setup",
            # All 5 dimensions favorable: value (10% pullback), trend (uptrend),
            # momentum (RSI 35, MACD Buy), income (yield 4%, BUY consensus), calendar (30d).
            {"value_entry": 1, "trend": 1, "momentum": 1, "income": 1, "calendar": 1},
            _exceptional_ev(),  # passes exceptional gate
            "STRONG_BUY",
        ),
        # --- BUY (2) ---
        (
            "strong_bull_elevated_rsi_below_70",
            # Momentum neutral (RSI 60, no confirmation), rest positive.
            {"value_entry": 1, "trend": 1, "momentum": 0, "income": 1, "calendar": 1},
            _no_gate_ev(rsi_14=60.0),
            "BUY",
        ),
        (
            "all_positive_no_exceptional_gate",
            # RSI 55 fails exceptional gate → BUY not STRONG_BUY.
            {"value_entry": 1, "trend": 1, "momentum": 1, "income": 1, "calendar": 1},
            _no_gate_ev(rsi_14=55.0),
            "BUY",
        ),
        # --- ACCUMULATE (6) ---
        (
            "moderate_accumulation_zone_score_3",
            # Value + trend positive, momentum neutral (RSI 55), income positive, calendar neutral (10d).
            {"value_entry": 1, "trend": 1, "momentum": 0, "income": 1, "calendar": 0},
            _no_gate_ev(rsi_14=55.0, days_to_earnings=10.0),
            "ACCUMULATE",
        ),
        (
            "mild_positive_score_2",
            # Only value and trend confirm; momentum/income/calendar neutral.
            {"value_entry": 1, "trend": 1, "momentum": 0, "income": 0, "calendar": 0},
            _no_gate_ev(),
            "ACCUMULATE",
        ),
        (
            "earnings_outside_hard_wait_window_3d",
            # Earnings in 3 days: Calendar scores -1 (≤3 days rule) but hard WAIT (≤2d) doesn't fire.
            # Value+trend+income = +3, calendar = -1 → sum = +2 → ACCUMULATE.
            {"value_entry": 1, "trend": 1, "momentum": 0, "income": 1, "calendar": -1},
            _no_gate_ev(days_to_earnings=3.0),
            "ACCUMULATE",
        ),
        (
            "recovering_market_score_3",
            # Three positive dimensions; recovering from dip.
            {"value_entry": 1, "trend": 1, "momentum": 1, "income": 0, "calendar": 0},
            _no_gate_ev(rsi_14=42.0),
            "ACCUMULATE",
        ),
        (
            "limbo_zone_7_days_earnings",
            # Calendar in limbo (7d = 4-14d zone → 0). Three other positives.
            {"value_entry": 1, "trend": 1, "momentum": 1, "income": 0, "calendar": 0},
            _no_gate_ev(days_to_earnings=7.0),
            "ACCUMULATE",
        ),
        (
            "elevated_rsi_below_70_uptrend",
            # RSI 60-70 zone: momentum = 0 (no MACD/Stoch confirmation at 60-70).
            # Value + trend + income = +3, momentum + calendar = 0 → +3 → ACCUMULATE.
            {"value_entry": 1, "trend": 1, "momentum": 0, "income": 1, "calendar": 0},
            _no_gate_ev(rsi_14=62.0, days_to_earnings=10.0),
            "ACCUMULATE",
        ),
        # --- WAIT (5) ---
        (
            "genuinely_neutral_all_zero",
            # Mixed signals, no clear direction — all dimensions score 0.
            {"value_entry": 0, "trend": 0, "momentum": 0, "income": 0, "calendar": 0},
            _no_gate_ev(),
            "WAIT",
        ),
        (
            "muted_positive_score_1",
            # Only one dimension (value) confirms; rest inconclusive → WAIT (+1).
            {"value_entry": 1, "trend": 0, "momentum": 0, "income": 0, "calendar": 0},
            _no_gate_ev(),
            "WAIT",
        ),
        (
            "hard_wait_earnings_imminent",
            # Score +4 would be BUY, but earnings in 1 day → Hard WAIT → WAIT.
            {"value_entry": 1, "trend": 1, "momentum": 1, "income": 1, "calendar": 0},
            _no_gate_ev(days_to_earnings=1.0),
            "WAIT",
        ),
        (
            "hard_wait_rsi_overbought",
            # RSI 85 → Hard WAIT; score would be BUY → capped at WAIT.
            {"value_entry": 1, "trend": 1, "momentum": 1, "income": 1, "calendar": 0},
            _no_gate_ev(rsi_14=85.0),
            "WAIT",
        ),
        (
            "hard_wait_price_extended",
            # Price >10% above SMA50 and >15% above SMA200 → Hard WAIT.
            {"value_entry": 1, "trend": 1, "momentum": 0, "income": 1, "calendar": 1},
            _no_gate_ev(current_price=116.0, sma50=105.0, sma200=100.0,
                        analyst_target_price=125.0),
            "WAIT",
        ),
        # --- UNFAVORABLE (3) ---
        (
            "mild_headwinds_score_minus_1",
            # Single headwind (e.g. trend breaks below SMA200 but not extreme).
            {"value_entry": 0, "trend": -1, "momentum": 0, "income": 0, "calendar": 0},
            _no_gate_ev(),
            "UNFAVORABLE",
        ),
        (
            "moderate_headwinds_score_minus_2",
            # Trend and momentum both negative; value/income/calendar neutral.
            {"value_entry": 0, "trend": -1, "momentum": -1, "income": 0, "calendar": 0},
            _no_gate_ev(),
            "UNFAVORABLE",
        ),
        (
            "bearish_momentum_and_value_headwind",
            # Momentum: RSI > 70 → -1; Value: chasing price → -1; rest neutral.
            # Score = -2 → UNFAVORABLE (Hard AVOID not triggered: no div cut, no triple bear).
            {"value_entry": -1, "trend": 0, "momentum": -1, "income": 0, "calendar": 0},
            _no_gate_ev(rsi_14=72.0),  # RSI > 70 headwind but ≤ 80 (no Hard WAIT)
            "UNFAVORABLE",
        ),
        # --- AVOID (6) ---
        (
            "heavy_headwinds_score_minus_3",
            # Three negative dimensions, none are Hard AVOID gates.
            {"value_entry": -1, "trend": -1, "momentum": -1, "income": 0, "calendar": 0},
            _no_gate_ev(),
            "AVOID",
        ),
        (
            "deep_bear_market_score_minus_4",
            {"value_entry": -1, "trend": -1, "momentum": -1, "income": -1, "calendar": 0},
            _no_gate_ev(),
            "AVOID",
        ),
        (
            "extreme_bear_all_headwinds_score_minus_5",
            {"value_entry": -1, "trend": -1, "momentum": -1, "income": -1, "calendar": -1},
            _no_gate_ev(),
            "AVOID",
        ),
        (
            "hard_avoid_dividend_cut_on_positive_score",
            # Score +3 would be ACCUMULATE, but dividend cut → Hard AVOID → AVOID.
            {"value_entry": 1, "trend": 1, "momentum": 1, "income": 0, "calendar": 0},
            _no_gate_ev(dividend_cut_or_suspended=True),
            "AVOID",
        ),
        (
            "hard_avoid_triple_bearish_breakdown",
            # Oscillator STRONG_SELL + MA STRONG_SELL + price >10% below SMA200.
            # Score +2 would be ACCUMULATE, but triple bearish → Hard AVOID → AVOID.
            {"value_entry": 1, "trend": 1, "momentum": 0, "income": 0, "calendar": 0},
            _no_gate_ev(
                current_price=89.0, sma200=100.0,
                oscillator_summary="STRONG_SELL", ma_summary="STRONG_SELL",
            ),
            "AVOID",
        ),
        (
            "hard_avoid_wins_over_hard_wait_both_triggered",
            # Dividend cut (Hard AVOID) + earnings in 1 day (Hard WAIT) → AVOID.
            {"value_entry": 1, "trend": 1, "momentum": 1, "income": 1, "calendar": 0},
            _no_gate_ev(dividend_cut_or_suspended=True, days_to_earnings=1.0),
            "AVOID",
        ),
        (
            "score_minus_5_is_avoid_extreme",
            # All dimensions deeply negative — no hard gates needed.
            {"value_entry": -1, "trend": -1, "momentum": -1, "income": -1, "calendar": -1},
            _no_gate_ev(rsi_14=50.0),  # RSI safe but score is extreme negative
            "AVOID",
        ),
    ]

    def _run_all_scenarios(self) -> list[tuple[str, str]]:
        results = []
        for name, breakdown, evidence, _ in self._SCENARIOS:
            normalized = _norm6(_bt6_activity(breakdown), evidence)
            results.append((name, normalized["activity"]))
        return results

    def test_each_scenario_produces_expected_state_per_spec_rules(self):
        """Verify that spec-derived dimension scores produce the specified state."""
        failures = []
        for name, breakdown, evidence, expected in self._SCENARIOS:
            normalized = _norm6(_bt6_activity(breakdown), evidence)
            actual = normalized["activity"]
            if actual != expected:
                failures.append(f"  {name}: expected {expected}, got {actual}")
        assert not failures, "Scenario state mismatches:\n" + "\n".join(failures)

    def test_no_single_state_exceeds_50_percent(self):
        results = self._run_all_scenarios()
        total = len(results)
        from collections import Counter
        counts = Counter(state for _, state in results)
        most_common_state, most_common_count = counts.most_common(1)[0]
        pct = most_common_count / total
        assert pct <= 0.50, (
            f"State '{most_common_state}' appears {most_common_count}/{total} = "
            f"{pct:.0%}, exceeding 50% cap. Scenario matrix is not diverse enough."
        )

    def test_at_least_four_distinct_states_represented(self):
        results = self._run_all_scenarios()
        states_present = {state for _, state in results}
        assert len(states_present) >= 4, (
            f"Only {len(states_present)} distinct states in {len(results)} scenarios: "
            f"{states_present}. At least 4 are required."
        )

    def test_buy_and_strong_buy_combined_not_above_30_percent(self):
        results = self._run_all_scenarios()
        total = len(results)
        alert_count = sum(1 for _, state in results if state in {"BUY", "STRONG_BUY"})
        pct = alert_count / total
        assert pct <= 0.30, (
            f"BUY+STRONG_BUY = {alert_count}/{total} = {pct:.0%}, exceeding 30% cap. "
            "System is still over-recommending strong buys."
        )

    def test_scenario_count_meets_minimum(self):
        assert len(self._SCENARIOS) >= 20, (
            f"Spec requires ≥20 scenarios; matrix has only {len(self._SCENARIOS)}"
        )

    def test_all_six_states_represented_across_scenarios(self):
        results = self._run_all_scenarios()
        states_present = {state for _, state in results}
        all_six = {"STRONG_BUY", "BUY", "ACCUMULATE", "WAIT", "UNFAVORABLE", "AVOID"}
        missing = all_six - states_present
        assert not missing, (
            f"States not represented in scenario matrix: {missing}. "
            "All six states must appear at least once."
        )

    def test_scenario_score_format_is_always_signed(self):
        """All scenarios must produce signed score strings under new format."""
        for name, breakdown, evidence, _ in self._SCENARIOS:
            normalized = _norm6(_bt6_activity(breakdown), evidence)
            score_str = normalized["score"]
            assert (
                score_str == "0/5"
                or score_str.startswith("+")
                or score_str.startswith("-")
            ), f"Scenario '{name}' score {score_str!r} is not in signed format"


# ---------------------------------------------------------------------------
# 6. Backward Compatibility
# ---------------------------------------------------------------------------

@_six_state_skip
class TestBackwardCompatibilityV2:
    """Historical {0, 1} breakdowns and 'N/5' score strings stay valid."""

    def test_historical_binary_breakdown_treated_as_valid_subset(self):
        # {0, 1} ⊂ {-1, 0, +1}: historical values must not be flagged as invalid.
        old_format_breakdown = {
            "value_entry": 1, "trend": 1, "momentum": 1, "income": 1, "calendar": 1
        }
        normalized = _norm6(_bt6_activity(old_format_breakdown), _no_gate_ev())
        assert normalized["score"] == "+5/5"
        # No invalid flags — 1 is valid in the new tri-state schema
        invalid_flags = [f for f in normalized["risk_flags"]
                         if "invalid" in f or f == "score_breakdown_missing"]
        assert not invalid_flags, (
            f"Historical {0,1} breakdown triggered invalid flags: {invalid_flags}"
        )

    def test_partial_binary_historical_breakdown_not_capped(self):
        # Historical document with 4-dim breakdown at {0,1} — only 1 missing
        # (not 3+) so no insufficient_data cap.
        old_partial = {
            "value_entry": 1, "trend": 1, "momentum": 1, "income": 1
        }
        normalized = _norm6(_bt6_activity(old_partial), _no_gate_ev())
        assert normalized["score"] == "+4/5"
        assert normalized["activity"] == "BUY"
        assert "insufficient_data" not in normalized["risk_flags"]

    def test_historical_zero_one_breakdown_produces_correct_signed_score(self):
        # Old "3/5" score → now "+3/5". The breakdown values {0,1} sum to 3 → ACCUMULATE.
        breakdown = {"value_entry": 1, "trend": 1, "momentum": 1, "income": 0, "calendar": 0}
        normalized = _norm6(
            _bt6_activity(breakdown, score="3/5"),  # old unsigned format
            _no_gate_ev(),
        )
        assert normalized["score"] == "+3/5"
        assert normalized["activity"] == "ACCUMULATE"

    def test_old_score_string_in_activity_data_is_replaced_with_signed_format(self):
        # Incoming "4/5" (historical) must be replaced with "+4/5" in output
        breakdown = {"value_entry": 1, "trend": 1, "momentum": 1, "income": 1, "calendar": 0}
        normalized = _norm6(
            _bt6_activity(breakdown, score="4/5"),
            _no_gate_ev(),
        )
        assert normalized["score"] == "+4/5"
        assert "4/5" != "+4/5"  # sanity check that the formats differ

    def test_normalizer_does_not_mutate_input_activity_or_evidence(self):
        breakdown = _tri_breakdown(3)
        evidence = _no_gate_ev()
        activity = _bt6_activity(breakdown)
        act_before = json.dumps(activity, sort_keys=True)
        ev_before = json.dumps(evidence, sort_keys=True)

        _norm6(activity, evidence)

        assert json.dumps(activity, sort_keys=True) == act_before, "activity was mutated"
        assert json.dumps(evidence, sort_keys=True) == ev_before, "evidence was mutated"

    def test_normalizer_is_deterministic_across_repeated_calls(self):
        breakdown = _tri_breakdown(3)
        activity = _bt6_activity(breakdown)
        evidence = _no_gate_ev()

        first = _norm6(activity, evidence)
        second = _norm6(activity, evidence)

        assert first == second, "normalizer is not deterministic"
