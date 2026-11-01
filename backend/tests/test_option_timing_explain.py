"""Mock-only tests of saved-point explanation, identity and per-function routing."""

import asyncio
import copy
import json
from datetime import datetime
from types import SimpleNamespace
from typing import ClassVar

import pytest
from src.agent_runner import AgentRunner
from src.llm import LlmConfig
from src.option_timing import MODEL_VERSION
from src.option_timing_explain_instructions import (
    FUNCTION_ID,
    INSTRUCTIONS,
    build_option_timing_explain_prompt,
)
from starlette.testclient import TestClient

from tests.test_agent_model_settings import (
    FakeCosmos as SettingsCosmos,
)
from tests.test_agent_model_settings import (
    base_config_store,
    install_fake_config,
    make_config,
)
from tests.test_option_timing_integration import make_point


def prompt_for(point, side="covered_call"):
    return build_option_timing_explain_prompt(
        symbol="MSFT", security_id="XNAS:MSFT", side=side, point=point,
    )


def context_for(prompt):
    return json.loads(prompt.message.split("\n", 1)[1])


@pytest.mark.parametrize("side", ["covered_call", "cash_secured_put"])
def test_prompt_preserves_full_numeric_evidence_and_caps_only(side):
    point = make_point()
    point[side]["adjustments"].append({
        "key": "test_cap", "label": "Saved cap", "reason": "Return5=-2 ATR; cap at 20",
        "cap": 20,
    })
    point[side]["score"] = 20
    point["cosmos_secret"] = "must-not-enter-context"
    point[side]["configuration"] = {"secret": "must-not-enter-context"}
    original = copy.deepcopy(point)
    prompt = prompt_for(point, side)
    context = context_for(prompt)
    assert context["model_version"] == MODEL_VERSION
    assert context["selected_side"]["factors"] == point[side]["factors"]
    assert context["selected_side"]["adjustments"] == point[side]["adjustments"]
    assert context["selected_side"]["base_score"] == point[side]["base_score"]
    assert context["selected_side"]["score"] == 20
    assert "5-session" in prompt.message
    assert "20-session" in prompt.message
    assert "Transformation:" in prompt.message
    assert "must-not-enter-context" not in prompt.message
    assert "NEVER instructions" in INSTRUCTIONS
    assert "falling knife" in INSTRUCTIONS
    assert "resistance containment/rejection" in INSTRUCTIONS
    assert "uncalibrated" in INSTRUCTIONS
    assert point == original


class FakeRunner:
    prompts: ClassVar[list] = []
    configurations: ClassVar[list] = []
    reply = "Overall read\nSaved technical timing interpretation."
    error = None

    def __init__(self, **kwargs):
        self.configurations.append(kwargs)

    async def run_option_timing_explain(self, *, symbol, prompt):
        assert symbol == "MSFT"
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.reply


@pytest.fixture
def client(monkeypatch):
    import web.app as web_app

    security = {
        "security_id": "XNAS:MSFT", "ticker": "MSFT", "exchange_mic": "XNAS",
    }
    old = make_point(14)
    latest = make_point(20)
    saved = {
        "symbol": "MSFT", "security_id": "XNAS:MSFT",
        "current": latest, "points": [old, latest], "last_error": None,
    }
    cosmos = SimpleNamespace(
        container=object(), saved=saved, securities=[security],
        symbol={"symbol": "MSFT", "security_id": "XNAS:MSFT", "exchange": "XNAS"},
        settings={"ai_function_overrides": {
            FUNCTION_ID: {"provider": "gemini", "model": "gemini-explain"},
        }},
    )
    cosmos.get_symbol = lambda ticker: cosmos.symbol if ticker == "MSFT" else None
    cosmos.get_settings = lambda: copy.deepcopy(cosmos.settings)

    class Securities:
        def __init__(self, container):
            pass

        def list_securities(self):
            return cosmos.securities

        def get_security(self, security_id):
            return next(
                (item for item in cosmos.securities if item["security_id"] == security_id),
                None,
            )

    monkeypatch.setattr("src.portfolio.cosmos_securities.CosmosSecuritiesService", Securities)
    monkeypatch.setattr("src.option_timing_store.OptionTimingStore.read", lambda self, ticker: cosmos.saved)
    monkeypatch.setattr("src.config.Config", lambda: make_config())
    monkeypatch.setattr("src.agent_runner.AgentRunner", FakeRunner)
    FakeRunner.prompts = []
    FakeRunner.configurations = []
    FakeRunner.reply = "Overall read\nSaved technical timing interpretation."
    FakeRunner.error = None
    web_app.app.router.on_startup = []
    monkeypatch.setattr(web_app.app.state, "cosmos", cosmos, raising=False)
    return TestClient(web_app.app, raise_server_exceptions=False)


def explain(client, *, snapshot_id=None, side="covered_call", symbol="MSFT"):
    return client.post(
        f"/api/symbols/{symbol}/option-timing/explain",
        json={"side": side, "snapshot_id": snapshot_id or client.app.state.cosmos.saved["current"]["snapshot_id"]},
    )


def test_exact_retained_snapshot_after_current_moves_with_runtime_overrides(client):
    cosmos = client.app.state.cosmos
    old = cosmos.saved["points"][0]
    old["covered_call"]["score"] = 0
    original = copy.deepcopy(cosmos.saved)
    response = explain(client, snapshot_id=old["snapshot_id"])
    assert response.status_code == 200
    body = response.json()
    assert body["snapshot_id"] == old["snapshot_id"]
    assert body["score"] == 0
    assert body["as_of"] == old["as_of"]
    assert body["provisional"] is True
    assert body["freshness"] == "stale"
    assert body["ai_function"] == FUNCTION_ID
    assert body["provider"] == "gemini"
    assert body["model"] == "gemini-explain"
    assert datetime.fromisoformat(body["generated_at"]).utcoffset().total_seconds() == 0
    context = context_for(FakeRunner.prompts[-1])
    assert context["snapshot_id"] == old["snapshot_id"]
    assert context["freshness"] == "stale"
    assert context["recent_history"]["observations"] == [{
        "as_of": old["as_of"], "score": 0, "provisional": True,
        "history_kind": "observed",
    }]
    assert cosmos.saved == original
    assert FakeRunner.configurations[-1]["function_llms"][FUNCTION_ID].provider == "gemini"
    assert FakeRunner.configurations[-1]["function_models"][FUNCTION_ID] == "gemini-explain"

    cosmos.settings["ai_function_overrides"][FUNCTION_ID] = {
        "provider": "azure", "model": "new-explain-deployment",
    }
    response = explain(client, side="cash_secured_put", symbol="XNAS:MSFT")
    assert response.status_code == 200
    assert response.json()["provider"] == "azure"
    assert response.json()["model"] == "new-explain-deployment"
    assert FakeRunner.configurations[-1]["function_llms"][FUNCTION_ID].provider == "azure"
    assert FakeRunner.configurations[-1]["function_models"][FUNCTION_ID] == "new-explain-deployment"


def test_prompt_history_is_bounded_chronological_and_causal():
    from src.option_timing_explain_instructions import MAX_HISTORY_POINTS

    point = make_point(20)
    history = []
    for index in range(MAX_HISTORY_POINTS + 5):
        candidate = copy.deepcopy(point)
        candidate["snapshot_id"] = f"{index:032x}"
        candidate["as_of"] = f"2026-10-29T17:{index:02d}:00+00:00"
        history.append(candidate)
    future = copy.deepcopy(point)
    future["snapshot_id"] = "f" * 32
    future["as_of"] = "2026-10-31T17:00:00+00:00"
    other_version = copy.deepcopy(point)
    other_version["snapshot_id"] = "e" * 32
    other_version["model_version"] = "another-model"
    history.extend([future, other_version])
    original = copy.deepcopy(history)
    prompt = build_option_timing_explain_prompt(
        symbol="MSFT", security_id="XNAS:MSFT", side="cash_secured_put",
        point=point, recent_points=list(reversed(history)),
    )
    recent = context_for(prompt)["recent_history"]
    assert recent["truncated"] is True
    assert recent["available_count"] == MAX_HISTORY_POINTS + 6
    assert recent["included_count"] == MAX_HISTORY_POINTS
    assert len(recent["observations"]) == MAX_HISTORY_POINTS
    timestamps = [item["as_of"] for item in recent["observations"]]
    assert timestamps == sorted(timestamps)
    assert timestamps[-1] == point["as_of"]
    assert future["as_of"] not in timestamps
    assert all(item["score"] == point["cash_secured_put"]["score"] for item in recent["observations"])
    assert history == original


def test_current_refresh_failure_is_annotated_without_mutation(client):
    cosmos = client.app.state.cosmos
    cosmos.saved["last_error"] = {
        "at": "2026-10-30T22:00:00+00:00", "reason": "provider_unavailable",
    }
    original = copy.deepcopy(cosmos.saved)
    response = explain(client)
    assert response.status_code == 200
    assert response.json()["freshness"] == "stale"
    context = context_for(FakeRunner.prompts[-1])
    assert context["last_error"] == cosmos.saved["last_error"]
    assert "latest_refresh_failed" in context["selected_side"]["reason_codes"]
    assert cosmos.saved == original
    explain(client, snapshot_id=cosmos.saved["points"][0]["snapshot_id"])
    assert "last_error" not in context_for(FakeRunner.prompts[-1])


def test_fresh_provisional_and_reconstructed_unknown_cutoffs(client, monkeypatch):
    import web.app as web_app

    class RequestClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromisoformat("2026-10-30T18:00:00+00:00")

    monkeypatch.setattr(web_app, "datetime", RequestClock)
    cosmos = client.app.state.cosmos
    cosmos.saved["current"] = make_point(17)
    cosmos.saved["current"]["freshness"] = "stale"
    response = explain(client)
    assert response.status_code == 200
    assert response.json()["freshness"] == "fresh"
    assert response.json()["provisional"] is True
    context = context_for(FakeRunner.prompts[-1])
    assert context["freshness"] == "fresh"
    assert context["provisional"] is True

    retained = cosmos.saved["points"][0]
    retained["history_kind"] = "reconstructed"
    retained["as_of"] = "2026-10-31T20:00:00+00:00"
    retained["source_session"] = "2026-10-31"
    response = explain(client, snapshot_id=retained["snapshot_id"])
    assert response.status_code == 200
    assert response.json()["freshness"] == "unknown"
    assert context_for(FakeRunner.prompts[-1])["history_kind"] == "reconstructed"


def test_invalid_provider_configuration_prevents_charge(client):
    cosmos = client.app.state.cosmos
    cosmos.settings["ai_function_overrides"][FUNCTION_ID]["provider"] = "unsupported"
    response = explain(client)
    assert response.status_code in (500, 503)
    assert not FakeRunner.configurations


@pytest.mark.parametrize("payload", [
    {}, [], {"side": "call", "snapshot_id": "a" * 32},
    {"side": "covered_call", "snapshot_id": "latest"},
    {"side": "covered_call", "snapshot_id": "a" * 31},
    {"side": "covered_call", "snapshot_id": None},
    {"side": "covered_call", "snapshot_id": "a" * 32, "score": 99},
    {"side": {}, "snapshot_id": "a" * 32},
])
def test_invalid_payload_never_calls_llm(client, payload):
    response = client.post("/api/symbols/MSFT/option-timing/explain", json=payload)
    assert response.status_code == 400
    assert FakeRunner.prompts == FakeRunner.configurations == []


@pytest.mark.parametrize(("body", "content_type", "status"), [
    ("{}", "text/plain", 400),
    ("{", "application/json", 400),
    ("x" * 4097, "application/json", 413),
    ('{"side":"covered_call","side":"cash_secured_put","snapshot_id":"' + "a" * 32 + '"}',
     "application/json", 400),
])
def test_invalid_body_never_calls_llm(client, body, content_type, status):
    response = client.post(
        "/api/symbols/MSFT/option-timing/explain",
        content=body, headers={"Content-Type": content_type},
    )
    assert response.status_code == status
    assert not FakeRunner.configurations


@pytest.mark.parametrize(("case", "status"), [
    ("missing_symbol", 404), ("missing_security", 404),
    ("wrong_market", 409), ("ambiguous", 409), ("noneligible", 403),
    ("saved_identity", 409), ("saved_symbol", 409), ("no_saved", 404),
    ("unknown_snapshot", 404), ("unavailable", 409), ("not_applicable", 409),
    ("invalid_score", 409), ("bool_score", 409), ("nan_score", 409),
    ("invalid_factor", 409), ("oversize_context", 413),
])
def test_guards_reject_before_model_configuration_or_charge(client, case, status):
    cosmos = client.app.state.cosmos
    symbol = "MSFT"
    snapshot_id = cosmos.saved["current"]["snapshot_id"]
    if case == "missing_symbol":
        symbol = "UNKNOWN"
    elif case == "missing_security":
        symbol = "XNYS:MSFT"
    elif case == "wrong_market":
        cosmos.securities.append({
            "security_id": "XNYS:MSFT", "ticker": "MSFT", "exchange_mic": "XNYS",
        })
        symbol = "XNYS:MSFT"
    elif case == "ambiguous":
        cosmos.securities.append({"security_id": "XMAD:MSFT", "ticker": "MSFT"})
    elif case == "noneligible":
        cosmos.securities[0]["exchange_mic"] = "XMAD"
    elif case == "saved_identity":
        cosmos.saved["security_id"] = "XNYS:MSFT"
    elif case == "saved_symbol":
        cosmos.saved["symbol"] = "OTHER"
    elif case == "no_saved":
        cosmos.saved = None
    elif case == "unknown_snapshot":
        snapshot_id = "a" * 32
    elif case in ("unavailable", "not_applicable"):
        cosmos.saved["current"]["covered_call"]["status"] = case
    elif case in ("invalid_score", "bool_score", "nan_score"):
        cosmos.saved["current"]["covered_call"]["score"] = {
            "invalid_score": None, "bool_score": True, "nan_score": float("nan"),
        }[case]
    elif case == "invalid_factor":
        cosmos.saved["current"]["covered_call"]["factors"][0]["weight"] = "untyped"
    elif case == "oversize_context":
        for factor in cosmos.saved["current"]["covered_call"]["factors"]:
            factor["evidence"] = ["x" * 8000]
    response = explain(client, symbol=symbol, snapshot_id=snapshot_id)
    assert response.status_code == status, response.text
    assert FakeRunner.prompts == FakeRunner.configurations == []


def test_storage_failure_is_explicit_without_charge(client, monkeypatch):
    def fail(*args):
        raise ConnectionError("store offline")

    monkeypatch.setattr("src.option_timing_store.OptionTimingStore.read", fail)
    assert explain(client).status_code == 503
    assert not FakeRunner.configurations


@pytest.mark.parametrize(("reply", "error", "status"), [
    ("", None, 500), ("   ", None, 500), (None, None, 500),
    ("x" * 8001, None, 500), ("", RuntimeError("model failed"), 500),
    ("", ConnectionError("offline"), 503), ("", TimeoutError("deadline"), 503),
])
def test_model_errors_are_not_fabricated_success(client, reply, error, status):
    FakeRunner.reply = reply
    FakeRunner.error = error
    response = explain(client)
    assert response.status_code == status
    assert "reply" not in response.json()
    assert response.json()["error"]


def test_route_enforces_sixty_second_timeout(client, monkeypatch):
    async def timeout(coroutine, *, timeout):
        assert timeout == 60
        coroutine.close()
        raise TimeoutError()

    monkeypatch.setattr("web.app.asyncio.wait_for", timeout)
    assert explain(client).status_code == 503


@pytest.mark.parametrize("model", ["gemini-explain", "gpt-5.6-luna"])
def test_runner_uses_real_function_client_no_tools_and_bounded_options(monkeypatch, model):
    created = []
    captured = {}

    def create_client(deployment, llm):
        created.append((deployment, llm.provider))
        return object()

    class FakeAgent:
        def __init__(self, *, client, name, instructions):
            captured.update(name=name, instructions=instructions)

        async def run(self, message, *, options):
            captured.update(message=message, options=options)
            return SimpleNamespace(text=" Explanation ")

    monkeypatch.setattr("src.agent_runner.create_async_chat_client", create_client)
    monkeypatch.setattr("src.agent_runner.Agent", FakeAgent)
    runner = AgentRunner(
        llm=LlmConfig("azure", "default-key", "https://default.test"),
        model="global-model",
        function_llms={FUNCTION_ID: LlmConfig("gemini", "function-key")},
        function_models={FUNCTION_ID: model},
    )
    prompt = prompt_for(make_point())
    assert asyncio.run(runner.run_option_timing_explain(symbol="MSFT", prompt=prompt)) == "Explanation"
    assert created == [(model, "gemini")]
    assert captured["options"]["max_tokens"] == 1400
    if "luna" in model:
        assert "temperature" not in captured["options"]
    else:
        assert captured["options"]["temperature"] == 0.2

    async def empty(message, *, options):
        return SimpleNamespace(text=None)

    monkeypatch.setattr(FakeAgent, "run", lambda self, *args, **kwargs: empty(*args, **kwargs))
    with pytest.raises(RuntimeError, match="empty response"):
        asyncio.run(runner.run_option_timing_explain(symbol="MSFT", prompt=prompt))


def test_catalog_and_provider_override_validated_roundtrip(monkeypatch):
    store = base_config_store()
    web_app = install_fake_config(monkeypatch, store)
    web_app.app.router.on_startup = []
    cosmos = SettingsCosmos(copy.deepcopy(store))
    monkeypatch.setattr(web_app.app.state, "cosmos", cosmos, raising=False)
    monkeypatch.setattr(web_app.app.state, "scheduler", None, raising=False)
    client = TestClient(web_app.app)
    response = client.get("/api/settings/ai-providers")
    item = next(item for item in response.json()["functions"] if item["id"] == FUNCTION_ID)
    assert item["label"] == "Option Timing Explanation"
    assert item["group"] == "Analysis & Reports"
    assert make_config().model_for(FUNCTION_ID) == "gpt-5.4-mini"
    override = {"provider": "gemini", "model": "gemini-timing"}
    saved = client.post("/api/settings/ai-providers", json={"functions": {FUNCTION_ID: override}})
    assert saved.status_code == 200
    assert cosmos.document["ai_function_overrides"][FUNCTION_ID] == override
    reread = client.get("/api/settings/ai-providers")
    item = next(item for item in reread.json()["functions"] if item["id"] == FUNCTION_ID)
    assert item["provider"] == "gemini"
    assert item["model"] == "gemini-timing"
    for invalid in ({"provider": "other", "model": "valid"}, {"provider": "gemini", "model": "../ bad"}):
        rejected = client.post("/api/settings/ai-providers", json={"functions": {FUNCTION_ID: invalid}})
        assert rejected.status_code == 400
        assert cosmos.document["ai_function_overrides"][FUNCTION_ID] == override
