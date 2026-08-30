"""Focused tests for per-function AI provider/model configuration."""

import copy
from pathlib import Path

import pytest
from azure.cosmos.exceptions import CosmosHttpResponseError
from starlette.testclient import TestClient

from src.agent_runner import AgentRunner
from src.ai_functions import AI_FUNCTIONS
from src.config import Config
from src.cosmos_db import CosmosDBService
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


def test_function_models_resolve_when_no_explicit_model_provided(monkeypatch):
    """Regression: contract validation should use function-specific models, not global default."""
    created = []

    def fake_client(model, llm):
        client = object()
        created.append((model, llm.provider, client))
        return client

    monkeypatch.setattr("src.agent_runner.create_async_chat_client", fake_client)
    runner = AgentRunner(
        llm=LlmConfig("azure", "azure-key", "https://azure.test"),
        model="global-default-model",
        function_llms={
            "analysis": LlmConfig("gemini", "gemini-key"),
            "supervisor": LlmConfig("azure", "supervisor-key", "https://supervisor.test"),
            "alpha": LlmConfig("azure", "alpha-key", "https://alpha.test"),
        },
        function_models={
            "analysis": "following-analysis-model",
            "supervisor": "supervisor-specific-model",
            "alpha": "alpha-specific-model",
        },
    )

    # Simulate contract validation flow: no explicit model parameter, only function_id
    analysis_client = runner._get_client(model=None, function_id="analysis")
    supervisor_client = runner._get_client(model=None, function_id="supervisor")
    alpha_client = runner._get_client(model=None, function_id="alpha")

    # Should use function-specific models, NOT global default
    assert created[0] == ("following-analysis-model", "gemini", analysis_client)
    assert created[1] == ("supervisor-specific-model", "azure", supervisor_client)
    assert created[2] == ("alpha-specific-model", "azure", alpha_client)

    # Verify that global default is NOT used when function-specific model exists
    assert all(model != "global-default-model" for model, _, _ in created)


def test_function_models_fallback_to_global_default(monkeypatch):
    """When function has no specific model, should fall back to global default."""
    created = []

    def fake_client(model, llm):
        client = object()
        created.append((model, llm.provider, client))
        return client

    monkeypatch.setattr("src.agent_runner.create_async_chat_client", fake_client)
    runner = AgentRunner(
        llm=LlmConfig("azure", "azure-key", "https://azure.test"),
        model="global-default-model",
        function_llms={},
        function_models={
            "analysis": "analysis-model",
            # No entry for "supervisor" or "alpha"
        },
    )

    analysis_client = runner._get_client(model=None, function_id="analysis")
    supervisor_client = runner._get_client(model=None, function_id="supervisor")

    # Analysis should use its specific model
    assert created[0] == ("analysis-model", "azure", analysis_client)
    # Supervisor has no specific model, should use global default
    assert created[1] == ("global-default-model", "azure", supervisor_client)


def test_explicit_model_overrides_function_model(monkeypatch):
    """Explicit model parameter should always win over function-specific model."""
    created = []

    def fake_client(model, llm):
        client = object()
        created.append((model, llm.provider, client))
        return client

    monkeypatch.setattr("src.agent_runner.create_async_chat_client", fake_client)
    runner = AgentRunner(
        llm=LlmConfig("azure", "azure-key", "https://azure.test"),
        model="global-default-model",
        function_models={
            "analysis": "analysis-model",
        },
    )

    # Explicit model should override function-specific model
    client = runner._get_client(model="explicit-override", function_id="analysis")

    assert created[0] == ("explicit-override", "azure", client)


def test_set_function_models_updates_routing(monkeypatch):
    """set_function_models should update model routing dynamically."""
    created = []

    def fake_client(model, llm):
        client = object()
        created.append((model, llm.provider, client))
        return client

    monkeypatch.setattr("src.agent_runner.create_async_chat_client", fake_client)
    runner = AgentRunner(
        llm=LlmConfig("azure", "azure-key", "https://azure.test"),
        model="global-model",
        function_models={
            "analysis": "old-analysis-model",
        },
    )

    # Initial model
    c1 = runner._get_client(model=None, function_id="analysis")
    assert created[0] == ("old-analysis-model", "azure", c1)

    # Update function models
    runner.set_function_models({
        "analysis": "new-analysis-model",
        "supervisor": "new-supervisor-model",
    })

    # New clients should use updated models
    c2 = runner._get_client(model=None, function_id="analysis")
    c3 = runner._get_client(model=None, function_id="supervisor")

    assert created[1] == ("new-analysis-model", "azure", c2)
    assert created[2] == ("new-supervisor-model", "azure", c3)


class FakeCosmos:
    def __init__(self, settings=None, *, fail_update=False):
        self.document = {
            "id": "app-config",
            **copy.deepcopy(settings or {}),
        }
        self.fail_update = fail_update
        self.reads = []
        self.writes = []

    def get_settings(self):
        return self.get_settings_required()

    def get_settings_required(self):
        self.reads.append(("app-config", "app-config"))
        return {
            key: copy.deepcopy(value)
            for key, value in self.document.items()
            if key != "id" and not key.startswith("_")
        }

    def update_settings(self, mutate):
        if self.fail_update:
            raise RuntimeError("simulated Cosmos write failure")
        settings = self.get_settings_required()
        mutate(settings)
        self.document = {"id": "app-config", **copy.deepcopy(settings)}
        self.writes.append(("app-config", "app-config"))
        return copy.deepcopy(settings)


class FakeSettingsContainer:
    def __init__(self, document):
        self.document = copy.deepcopy(document)
        self.reads = []
        self.replaces = []

    def read_item(self, *, item, partition_key):
        self.reads.append((item, partition_key))
        return copy.deepcopy(self.document)

    def replace_item(
        self,
        *,
        item,
        body,
        etag,
        match_condition,
    ):
        self.replaces.append((item, body["id"], etag, match_condition))
        self.document = {
            **copy.deepcopy(body),
            "_etag": "etag-2",
        }
        return copy.deepcopy(self.document)


class ConflictSettingsContainer(FakeSettingsContainer):
    def replace_item(self, **kwargs):
        if not self.replaces:
            self.replaces.append((
                kwargs["item"],
                kwargs["body"]["id"],
                kwargs["etag"],
                kwargs["match_condition"],
            ))
            self.document["concurrent"] = {"preserve": True}
            self.document["_etag"] = "etag-concurrent"
            raise CosmosHttpResponseError(
                status_code=412,
                message="precondition failed",
            )
        return super().replace_item(**kwargs)


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


def test_cosmos_settings_update_targets_app_config_and_preserves_fields():
    service = CosmosDBService.__new__(CosmosDBService)
    service.settings_container = FakeSettingsContainer({
        "id": "app-config",
        "_etag": "etag-1",
        "scheduler": {"cron": "0 9 * * 1-5"},
        "unrelated": {"preserve": True},
    })

    saved = service.update_settings(
        lambda settings: settings.update({
            "ai_function_overrides": {
                "summary": {"provider": "gemini", "model": "gemini-2.5-pro"},
            },
        })
    )

    assert service.settings_container.reads == [("app-config", "app-config")]
    assert service.settings_container.replaces[0][:3] == (
        "app-config", "app-config", "etag-1",
    )
    assert saved["unrelated"] == {"preserve": True}
    assert service.settings_container.document["scheduler"] == {
        "cron": "0 9 * * 1-5",
    }
    assert service.settings_container.document["ai_function_overrides"][
        "summary"
    ]["model"] == "gemini-2.5-pro"


def test_cosmos_save_settings_deep_merges_partial_sections():
    service = CosmosDBService.__new__(CosmosDBService)
    service.settings_container = FakeSettingsContainer({
        "id": "app-config",
        "_etag": "etag-1",
        "scheduler": {"cron": "old", "enabled": True},
        "unrelated": {"preserve": True},
    })

    saved = service.save_settings({"scheduler": {"cron": "new"}})

    assert saved["scheduler"] == {"cron": "new", "enabled": True}
    assert saved["unrelated"] == {"preserve": True}


def test_cosmos_settings_update_retries_etag_conflict_without_data_loss():
    service = CosmosDBService.__new__(CosmosDBService)
    service.settings_container = ConflictSettingsContainer({
        "id": "app-config",
        "_etag": "etag-1",
        "unrelated": {"preserve": True},
    })

    saved = service.update_settings(
        lambda settings: settings.update({
            "ai_function_overrides": {
                "summary": {"provider": "gemini", "model": "model"},
            },
        })
    )

    assert len(service.settings_container.reads) == 2
    assert saved["concurrent"] == {"preserve": True}
    assert saved["ai_function_overrides"]["summary"]["model"] == "model"


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
    assert response.json()["persistence"] == "cosmos"
    assert store["ai_function_overrides"]["summary"] == {
        "provider": "gemini", "model": "custom-summary",
    }
    assert cosmos.document["id"] == "app-config"
    assert cosmos.document["ai_function_overrides"]["summary"] == {
        "provider": "gemini", "model": "custom-summary",
    }
    assert cosmos.document["cosmosdb"]["endpoint"] == "https://cosmos.test"
    assert cosmos.writes == [("app-config", "app-config")]
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
    assert cosmos.document.get("ai_function_overrides", {}).get("summary") is None


def test_ai_providers_get_reads_app_config_not_local_yaml(monkeypatch):
    store = base_config_store()
    store["ai_function_overrides"] = {
        "summary": {"provider": "azure", "model": "local-only"},
    }
    cosmos_settings = copy.deepcopy(store)
    cosmos_settings["ai_function_overrides"]["summary"] = {
        "provider": "gemini", "model": "cosmos-model",
    }
    web_app = install_fake_config(monkeypatch, store)
    cosmos = FakeCosmos(cosmos_settings)
    web_app.app.state.cosmos = cosmos
    web_app.app.state.scheduler = None

    response = TestClient(web_app.app).get("/api/settings/ai-providers")

    assert response.status_code == 200
    assert response.json()["persistence"] == "cosmos"
    summary = next(
        item for item in response.json()["functions"]
        if item["id"] == "summary"
    )
    assert summary["provider"] == "gemini"
    assert summary["model"] == "cosmos-model"
    assert cosmos.reads[-1] == ("app-config", "app-config")


def test_ai_providers_get_ignores_stale_local_override_when_cosmos_has_none(
    monkeypatch,
):
    store = base_config_store()
    store["ai_function_overrides"] = {
        "summary": {"provider": "gemini", "model": "stale-local"},
    }
    cosmos_settings = base_config_store()
    web_app = install_fake_config(monkeypatch, store)
    web_app.app.state.cosmos = FakeCosmos(cosmos_settings)
    web_app.app.state.scheduler = None

    response = TestClient(web_app.app).get("/api/settings/ai-providers")

    summary = next(
        item for item in response.json()["functions"]
        if item["id"] == "summary"
    )
    assert summary["provider"] == ""
    assert summary["model"] == ""
    assert summary["provider_source"] == "inherited"
    assert summary["model_source"] == "inherited"


def test_ai_provider_save_reloads_scheduler_from_verified_cosmos(monkeypatch):
    store = base_config_store()
    web_app = install_fake_config(monkeypatch, store)
    cosmos = FakeCosmos(store)
    scheduler_config = make_config()

    class Runner:
        function_llms = None
        function_models = None

        def set_function_llms(self, values):
            self.function_llms = values

        def set_function_models(self, values):
            self.function_models = values

    class Scheduler:
        config = scheduler_config
        runner = Runner()

    scheduler = Scheduler()
    web_app.app.state.cosmos = cosmos
    web_app.app.state.scheduler = scheduler

    response = TestClient(web_app.app).post(
        "/api/settings/ai-providers",
        json={"functions": {
            "summary": {"provider": "gemini", "model": "cosmos-summary"},
        }},
    )

    assert response.status_code == 200
    assert scheduler.config.config["ai_function_overrides"]["summary"] == {
        "provider": "gemini", "model": "cosmos-summary",
    }
    assert scheduler.runner.function_llms["summary"].provider == "gemini"


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


def test_ai_providers_api_propagates_cosmos_write_failure(monkeypatch):
    store = base_config_store()
    original = copy.deepcopy(store)
    web_app = install_fake_config(monkeypatch, store)
    web_app.app.state.cosmos = FakeCosmos(store, fail_update=True)
    web_app.app.state.scheduler = None

    response = TestClient(web_app.app).post(
        "/api/settings/ai-providers",
        json={"functions": {
            "summary": {"provider": "gemini", "model": "custom-summary"},
        }},
    )

    assert response.status_code == 503
    assert response.json() == {"error": "simulated Cosmos write failure"}
    assert store == original


def test_ai_providers_api_uses_local_yaml_only_without_cosmos_config(
    monkeypatch,
):
    store = base_config_store()
    store.pop("cosmosdb")
    web_app = install_fake_config(monkeypatch, store)
    web_app.app.state.cosmos = None
    web_app.app.state.cosmos_error = "COSMOSDB_ENDPOINT not set"
    web_app.app.state.scheduler = None

    response = TestClient(web_app.app).post(
        "/api/settings/ai-providers",
        json={"functions": {
            "summary": {"provider": "gemini", "model": "local-summary"},
        }},
    )

    assert response.status_code == 200
    assert response.json()["persistence"] == "local"
    assert store["ai_function_overrides"]["summary"] == {
        "provider": "gemini", "model": "local-summary",
    }


def test_ai_providers_api_rejects_local_fallback_when_cosmos_is_configured(
    monkeypatch,
):
    store = base_config_store()
    original = copy.deepcopy(store)
    web_app = install_fake_config(monkeypatch, store)
    web_app.app.state.cosmos = None
    web_app.app.state.cosmos_error = "authentication failed"
    web_app.app.state.scheduler = None

    response = TestClient(web_app.app).post(
        "/api/settings/ai-providers",
        json={"functions": {
            "summary": {"provider": "gemini", "model": "must-not-save-locally"},
        }},
    )

    assert response.status_code == 503
    assert "CosmosDB settings persistence is unavailable" in response.json()["error"]
    assert store == original


def test_ai_providers_get_fails_when_configured_cosmos_is_unavailable(
    monkeypatch,
):
    store = base_config_store()
    web_app = install_fake_config(monkeypatch, store)
    web_app.app.state.cosmos = None
    web_app.app.state.cosmos_error = "connection failed"

    response = TestClient(web_app.app).get("/api/settings/ai-providers")

    assert response.status_code == 503
    assert "CosmosDB settings persistence is unavailable" in response.json()["error"]


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
