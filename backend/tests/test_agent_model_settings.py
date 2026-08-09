"""Focused tests for per-function AI provider/model configuration."""

import copy
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from src.agent_runner import AgentRunner
from src.ai_functions import AI_FUNCTIONS
from src.config import Config
from src.llm import LlmConfig


EXPECTED_FUNCTIONS = {
    "monitor_assessment", "monitor_roll", "supervisor", "alpha", "analysis",
    "buy_tracker", "summary", "banner", "report", "chat", "symbol_chat",
    "technical_analysis", "plan_monitor", "activity_chat", "dps_insights",
}


def make_config(overrides=None):
    config = Config.__new__(Config)
    config.config = {
        "ai": {
            "provider": "azure",
            "model_deployment": "global-model",
            "models": {
                "analysis": "analysis-model",
                "summary": "summary-model",
                "chat": "chat-model",
            },
        },
        "azure": {"project_endpoint": "https://azure.test", "api_key": "azure-key"},
        "gemini": {"api_key": "gemini-key"},
        "scheduler": {"cron": "0 9 * * 1-5"},
        "summary_agent": {"enabled": True, "cron": "0 8 * * *"},
        "plan_monitor": {"model": "legacy-plan-model"},
        "activity_chat": {"model": "legacy-activity-model"},
        "dps_insights": {"model": "legacy-dps-model"},
    }
    for key, value in (overrides or {}).items():
        config.config.setdefault(key, {}).update(value)
    return config


def test_function_catalog_covers_all_real_functions():
    assert set(AI_FUNCTIONS) == EXPECTED_FUNCTIONS
    assert all(item["label"] and item["group"] for item in AI_FUNCTIONS.values())


def test_function_resolution_defaults_and_independent_overrides():
    config = make_config({
        "ai_function_overrides": {
            "analysis": {"provider": "gemini"},
            "summary": {"model": "custom-summary"},
        },
    })

    assert config.provider_for("analysis") == "gemini"
    assert config.model_for("analysis") == "analysis-model"
    assert config.provider_for("summary") == "azure"
    assert config.model_for("summary") == "custom-summary"
    assert config.model_for("chat") == "chat-model"
    assert config.model_for("report") == "global-model"


def test_legacy_task_overrides_remain_safe_fallbacks():
    config = make_config({
        "scheduler": {"provider": "gemini", "model": "legacy-monitor"},
        "summary_agent": {"provider": "gemini", "model": "legacy-summary"},
    })

    assert config.provider_for("monitor_roll") == "gemini"
    assert config.model_for("monitor_roll") == "legacy-monitor"
    assert config.provider_for("summary") == "gemini"
    assert config.model_for("summary") == "legacy-summary"
    assert config.model_for("plan_monitor") == "legacy-plan-model"


def test_function_override_beats_legacy_task_override():
    config = make_config({
        "scheduler": {"provider": "gemini", "model": "legacy-monitor"},
        "ai_function_overrides": {
            "analysis": {"provider": "azure", "model": "function-analysis"},
        },
    })
    assert config.provider_for("analysis") == "azure"
    assert config.model_for("analysis") == "function-analysis"


def test_agent_runner_selects_provider_by_function(monkeypatch):
    created = []

    def fake_client(model, llm):
        client = object()
        created.append((model, llm.provider, client))
        return client

    monkeypatch.setattr("src.agent_runner.create_async_chat_client", fake_client)
    runner = AgentRunner(
        llm=LlmConfig("azure", "azure-key", "https://azure.test"),
        model="global-model",
        function_llms={
            "analysis": LlmConfig("gemini", "gemini-key"),
            "supervisor": LlmConfig("azure", "azure-key", "https://azure.test"),
        },
    )

    analysis_client = runner._get_client("shared-model", "analysis")
    supervisor_client = runner._get_client("shared-model", "supervisor")
    assert analysis_client is not supervisor_client
    assert [(model, provider) for model, provider, _ in created] == [
        ("shared-model", "gemini"),
        ("shared-model", "azure"),
    ]


class FakeCosmos:
    def __init__(self, settings=None):
        self.settings = copy.deepcopy(settings or {})

    def get_settings(self):
        return copy.deepcopy(self.settings)

    def save_settings(self, settings):
        self.settings = copy.deepcopy(settings)
        return settings


def install_fake_config(monkeypatch, config_store):
    from src import config as config_module
    from web import app as web_app

    class FakeConfig(Config):
        def __init__(self):
            self.config = copy.deepcopy(config_store)

    def load_config():
        return copy.deepcopy(config_store)

    def write_config(value):
        config_store.clear()
        config_store.update(copy.deepcopy(value))

    monkeypatch.setattr(config_module, "Config", FakeConfig)
    monkeypatch.setattr(web_app, "_load_config", load_config)
    monkeypatch.setattr(web_app, "_write_config", write_config)
    return web_app


def base_config_store():
    return {
        "ai": {
            "provider": "azure",
            "model_deployment": "global-model",
            "models": {role: f"{role}-default" for role in EXPECTED_FUNCTIONS},
        },
        "azure": {"project_endpoint": "https://azure.test", "api_key": "azure-key"},
        "gemini": {"api_key": "gemini-key"},
        "cosmosdb": {"endpoint": "https://cosmos.test", "key": "key"},
        "scheduler": {"cron": "0 9 * * 1-5"},
        "summary_agent": {"enabled": True, "cron": "0 8 * * *"},
        "banner_agent": {"enabled": True, "cron": "0 5 * * *"},
        "plan_monitor": {"enabled": True, "cron": "0 4 * * 1-5"},
    }


def test_ai_providers_api_round_trip_migrates_legacy_and_resets(monkeypatch):
    store = base_config_store()
    store["scheduler"].update({"provider": "gemini", "model": "legacy-monitor"})
    cosmos = FakeCosmos(copy.deepcopy(store))
    web_app = install_fake_config(monkeypatch, store)
    web_app.app.state.cosmos = cosmos
    web_app.app.state.scheduler = None
    client = TestClient(web_app.app)

    initial = client.get("/api/settings/ai-providers").json()
    assert len(initial["functions"]) == len(EXPECTED_FUNCTIONS)
    analysis = next(item for item in initial["functions"] if item["id"] == "analysis")
    assert analysis["provider_source"] == "legacy"
    assert analysis["model_source"] == "legacy"

    payload = {
        item["id"]: {"provider": item["provider"], "model": item["model"]}
        for item in initial["functions"]
    }
    payload["summary"] = {"provider": "gemini", "model": "custom-summary"}
    response = client.post("/api/settings/ai-providers", json={"functions": payload})
    assert response.status_code == 200
    assert store["ai_function_overrides"]["summary"] == {
        "provider": "gemini", "model": "custom-summary",
    }
    assert "provider" not in store["scheduler"]
    assert "model" not in store["scheduler"]

    reset = copy.deepcopy(payload)
    reset["summary"] = {"provider": "", "model": ""}
    response = client.post("/api/settings/ai-providers", json={"functions": reset})
    assert response.status_code == 200
    assert "summary" not in store.get("ai_function_overrides", {})
    summary = next(
        item for item in response.json()["functions"]
        if item["id"] == "summary"
    )
    assert summary["provider_source"] == "inherited"
    assert summary["model_source"] == "inherited"


def test_partial_reset_preserves_shared_legacy_values_for_sibling_functions(
    monkeypatch,
):
    store = base_config_store()
    store["scheduler"].update({"provider": "gemini", "model": "legacy-monitor"})
    web_app = install_fake_config(monkeypatch, store)
    web_app.app.state.cosmos = FakeCosmos()
    web_app.app.state.scheduler = None

    response = TestClient(web_app.app).post(
        "/api/settings/ai-providers",
        json={"functions": {"analysis": {"provider": "", "model": ""}}},
    )

    assert response.status_code == 200
    assert "provider" not in store["scheduler"]
    assert "model" not in store["scheduler"]
    assert "analysis" not in store["ai_function_overrides"]
    for function_id in (
        "monitor_assessment",
        "monitor_roll",
        "buy_tracker",
        "supervisor",
        "alpha",
    ):
        assert store["ai_function_overrides"][function_id] == {
            "provider": "gemini",
            "model": "legacy-monitor",
        }


@pytest.mark.parametrize("functions", [
    {"summary": {"provider": "unsupported", "model": ""}},
    {"summary": {"provider": "", "model": "bad model!"}},
    {"not_real": {"provider": "azure", "model": "model"}},
])
def test_ai_providers_api_rejects_invalid_values(monkeypatch, functions):
    store = base_config_store()
    web_app = install_fake_config(monkeypatch, store)
    web_app.app.state.cosmos = FakeCosmos()
    web_app.app.state.scheduler = None
    response = TestClient(web_app.app).post(
        "/api/settings/ai-providers",
        json={"functions": functions},
    )
    assert response.status_code == 400
    assert "error" in response.json()


def test_ai_providers_api_rejects_unconfigured_provider(monkeypatch):
    store = base_config_store()
    store["gemini"]["api_key"] = ""
    web_app = install_fake_config(monkeypatch, store)
    web_app.app.state.cosmos = FakeCosmos()
    web_app.app.state.scheduler = None
    response = TestClient(web_app.app).post(
        "/api/settings/ai-providers",
        json={"functions": {
            "summary": {"provider": "gemini", "model": "gemini-model"},
        }},
    )
    assert response.status_code == 400
    assert "API key not configured" in response.json()["error"]


def test_cron_settings_ui_has_no_ai_provider_controls():
    root = Path(__file__).resolve().parents[2]
    text = (
        root / "frontend/src/components/SettingsConfigView.tsx"
    ).read_text(encoding="utf-8")
    assert "AgentAiFields" not in text
    assert "monitoring_provider" not in text
    assert "AI runtime" not in text
    nav = (root / "frontend/src/components/TopNav.tsx").read_text(encoding="utf-8")
    assert 'href: "/settings/ai-providers"' in nav
    assert 'label: "AI Providers"' in nav
