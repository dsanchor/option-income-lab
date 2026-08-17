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
    expected_alert = desired_activity in {"BUY", "STRONG_BUY"}
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
        canonical_price = evidence.get("current_price")
        normalized.update(
            {
                "activity": desired_activity,
                "confidence": "high" if desired_activity == "STRONG_BUY" else "medium",
                "score": "5/5",
                "reason": f"Score 5/5. Deterministically normalized to {desired_activity}.",
                "waiting_for": "Wait for the hard risk to clear." if desired_activity == "WAIT" else "",
                "risk_flags": ["normalized_wait"] if desired_activity == "WAIT" else [],
                "technical_triggers": (
                    ["exceptional_gate"] if desired_activity == "STRONG_BUY" else []
                ),
            }
        )
        if canonical_price is not None:
            normalized["underlying_price"] = canonical_price
        if (
            desired_activity in {"BUY", "STRONG_BUY"}
            and canonical_price is not None
        ):
            normalized["entry_zone"] = "$88.00-$92.00"
        elif desired_activity == "WAIT":
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
    ["WAIT", "BUY", "STRONG_BUY"],
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
