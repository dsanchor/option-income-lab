from __future__ import annotations

import asyncio
import copy
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from src.config import (
    MONITOR_AGENT_NAMES,
    is_monitor_agent_enabled,
    normalize_monitor_agent_gates,
)
from src.scheduler_registry import TaskRegistry
from web import app as web_app
from web.app import (
    _apply_settings_config,
    _build_dashboard_tables,
    _build_settings_config_context,
    _default_full_analysis_status,
    _run_all_agents_sequentially,
)


class _Config:
    def __init__(self, agents=None):
        self.config = {"scheduler": {"cron": "0 * * * *"}}
        if agents is not None:
            self.config["scheduler"]["agents"] = agents
        self.cosmosdb_endpoint = "https://example.test"
        self.cosmosdb_key = "key"
        self.cosmosdb_database = "database"
        self.model_deployment = "model"
        self.plan_monitor_model = "model"
        self.timezone = "UTC"

    @property
    def cron_expression(self):
        return self.config["scheduler"]["cron"]

    @cron_expression.setter
    def cron_expression(self, value):
        self.config["scheduler"]["cron"] = value

    def llm_config(self):
        return object()

    def function_llm_configs(self):
        return {}

    def function_model_deployments(self):
        return {}

    def model_for(self, function_id):
        return self.model_deployment


class _Scheduler:
    def __init__(self, config):
        self.config = config
        self.runner = object()
        self.cosmos = object()
        self.context_provider = object()
        self.registry = SimpleNamespace(
            get_all_task_metadata=list,
            update_task_enabled=lambda *args: True,
            reschedule=lambda *args: None,
        )

    def __getattr__(self, name):
        if name.startswith("reschedule_"):
            return lambda *args: None
        raise AttributeError(name)


def test_missing_and_invalid_monitor_agent_config_default_enabled():
    for raw in ({}, {"scheduler": {}}, {"scheduler": {"agents": None}},
                {"scheduler": {"agents": "invalid"}}):
        for agent_name in MONITOR_AGENT_NAMES:
            assert is_monitor_agent_enabled(raw, agent_name) is True

    config = _Config({"buy_tracker": "false", "covered_call": False})
    assert is_monitor_agent_enabled(config, "buy_tracker") is True
    assert is_monitor_agent_enabled(config, "covered_call") is False


def test_monitor_agent_normalization_rebuilds_all_five_members():
    assert normalize_monitor_agent_gates({
        "scheduler": {
            "agents": {
                "covered_call": False,
                "cash_secured_put": True,
                "buy_tracker": "false",
                "open_call_monitor": 0,
            }
        }
    }) == {
        "covered_call": False,
        "cash_secured_put": True,
        "buy_tracker": True,
        "open_call_monitor": True,
        "open_put_monitor": True,
    }


@pytest.mark.parametrize(
    ("persisted_scheduler", "expected_buy_tracker"),
    [
        ({"cron": "15 * * * *"}, True),
        ({"cron": "15 * * * *", "agents": "invalid"}, True),
        ({"cron": "15 * * * *", "agents": {"buy_tracker": False}}, False),
    ],
)
def test_startup_uses_cosmos_as_monitor_gate_authority(
    monkeypatch,
    persisted_scheduler,
    expected_buy_tracker,
):
    import src.main as main_mod

    config = _Config({"buy_tracker": False})
    config.config["scheduler"]["reload_interval"] = 37
    captured_defaults = {}

    class Cosmos:
        def merge_defaults(self, defaults):
            captured_defaults.update(copy.deepcopy(defaults))

            def merge(stored, fallback):
                result = copy.deepcopy(stored)
                for key, value in fallback.items():
                    if key not in result:
                        result[key] = copy.deepcopy(value)
                    elif isinstance(result[key], dict) and isinstance(value, dict):
                        result[key] = merge(result[key], value)
                return result

            return merge(
                {"scheduler": copy.deepcopy(persisted_scheduler)},
                defaults,
            )

    cosmos = Cosmos()
    monkeypatch.setattr(main_mod, "Config", lambda: config)
    monkeypatch.setattr(main_mod, "CosmosDBService", lambda **kwargs: cosmos)
    monkeypatch.setattr(main_mod, "ContextProvider", lambda value: object())
    monkeypatch.setattr(main_mod, "AgentRunner", lambda **kwargs: object())
    monkeypatch.setattr(
        "src.telegram_notifier.TelegramNotifier",
        lambda cosmos: object(),
    )

    scheduler = main_mod.OptionsAgentScheduler()
    scheduler.setup()

    assert "agents" not in captured_defaults["scheduler"]
    assert scheduler.config.config["scheduler"]["reload_interval"] == 37
    assert (
        is_monitor_agent_enabled(scheduler.config, "buy_tracker")
        is expected_buy_tracker
    )


def test_scheduled_sweep_skips_globally_disabled_buy_tracker(monkeypatch):
    import src.main as main_mod

    calls = []

    def recorder(name):
        async def run(*args, **kwargs):
            calls.append(name)
        return run

    for name, attr in (
        ("covered_call", "run_covered_call_analysis"),
        ("cash_secured_put", "run_cash_secured_put_analysis"),
        ("buy_tracker", "run_buy_tracker_analysis"),
        ("open_call_monitor", "run_open_call_monitor"),
        ("open_put_monitor", "run_open_put_monitor"),
    ):
        monkeypatch.setattr(main_mod, attr, recorder(name))

    scheduler = main_mod.OptionsAgentScheduler.__new__(main_mod.OptionsAgentScheduler)
    scheduler.config = _Config({"buy_tracker": False})
    scheduler.runner = object()
    scheduler.cosmos = object()
    scheduler.context_provider = object()

    asyncio.run(scheduler._run_all_agents_async())

    assert "buy_tracker" not in calls
    assert calls == [
        "covered_call",
        "cash_secured_put",
        "open_call_monitor",
        "open_put_monitor",
    ]


def test_full_analysis_skips_disabled_member_and_runs_others(monkeypatch):
    calls = []

    def recorder(name):
        async def run(*args, **kwargs):
            calls.append(name)
        return run

    monkeypatch.setattr(
        "src.covered_call_agent.run_covered_call_analysis", recorder("covered_call")
    )
    monkeypatch.setattr(
        "src.cash_secured_put_agent.run_cash_secured_put_analysis",
        recorder("cash_secured_put"),
    )
    monkeypatch.setattr(
        "src.buy_tracker_agent.run_buy_tracker_analysis", recorder("buy_tracker")
    )
    monkeypatch.setattr(
        "src.open_call_monitor_agent.run_open_call_monitor",
        recorder("open_call_monitor"),
    )
    monkeypatch.setattr(
        "src.open_put_monitor_agent.run_open_put_monitor",
        recorder("open_put_monitor"),
    )

    status = _default_full_analysis_status()
    status["running"] = True
    _run_all_agents_sequentially(
        _Scheduler(_Config({"buy_tracker": False})), status
    )

    assert "buy_tracker" not in calls
    assert status["skipped"] == ["buy_tracker"]
    assert status["completed"] == list(MONITOR_AGENT_NAMES)
    assert status["errors"] == []


def test_direct_disabled_agent_trigger_returns_explicit_conflict(monkeypatch):
    scheduler = _Scheduler(_Config({"buy_tracker": False}))
    monkeypatch.setattr(web_app.app.state, "scheduler", scheduler, raising=False)
    client = TestClient(web_app.app)

    response = client.post("/api/trigger/buy_tracker", json={"symbol": "MSFT"})

    assert response.status_code == 409
    assert response.json() == {
        "status": "disabled",
        "agent_type": "buy_tracker",
        "error": "Following · Buy Tracker is globally disabled",
    }


def test_dashboard_status_surfaces_disabled_monitor_members(monkeypatch):
    scheduler = _Scheduler(_Config({"buy_tracker": False}))
    monkeypatch.setattr(web_app.app.state, "scheduler", scheduler, raising=False)
    monkeypatch.setattr(web_app.app.state, "cosmos", None, raising=False)
    client = TestClient(web_app.app)

    response = client.get("/api/dashboard/status")

    assert response.status_code == 200
    body = response.json()
    assert body["monitor_agent_enabled"]["buy_tracker"] is False
    assert body["disabled_monitor_agents"] == ["buy_tracker"]


def test_web_only_dashboard_status_uses_persisted_gate_authority(monkeypatch):
    persisted = {
        "scheduler": {
            "agents": {"buy_tracker": False},
        }
    }

    class Cosmos:
        def get_settings(self):
            return copy.deepcopy(persisted)

        def get_all_activities(self, limit):
            assert limit == 1
            return []

    monkeypatch.setattr(web_app.app.state, "scheduler", None, raising=False)
    monkeypatch.setattr(web_app.app.state, "cosmos", Cosmos(), raising=False)
    client = TestClient(web_app.app)

    disabled = client.get("/api/dashboard/status").json()
    disabled_signature = json.dumps(
        disabled["monitor_agent_enabled"],
        sort_keys=True,
    )
    assert disabled["monitor_agent_enabled"]["buy_tracker"] is False

    persisted["scheduler"]["agents"]["buy_tracker"] = True
    enabled = client.get("/api/dashboard/status").json()
    enabled_signature = json.dumps(
        enabled["monitor_agent_enabled"],
        sort_keys=True,
    )

    assert enabled["monitor_agent_enabled"]["buy_tracker"] is True
    assert enabled_signature != disabled_signature


def test_dashboard_table_contract_marks_disabled_enrolled_agent():
    symbol = {
        "symbol": "MSFT",
        "display_name": "Microsoft",
        "watchlist": {
            "covered_call": True,
            "cash_secured_put": False,
            "buy_tracker": True,
        },
        "positions": [],
    }

    tables, _ = _build_dashboard_tables(
        None,
        [symbol],
        [],
        [],
        {"buy_tracker": False},
    )

    by_key = {table["key"]: table for table in tables}
    assert by_key["buy_tracker"]["enabled"] is False
    assert by_key["buy_tracker"]["rows"][0]["symbol"] == "MSFT"
    assert symbol["watchlist"]["buy_tracker"] is True
    assert by_key["covered_call"]["enabled"] is True


def test_dashboard_api_uses_scheduler_global_gate_state(monkeypatch):
    scheduler = _Scheduler(_Config({"buy_tracker": False}))
    cosmos = object()
    captured = {}

    def fake_compute(received_cosmos, monitor_agent_enabled):
        captured.update(monitor_agent_enabled)
        assert received_cosmos is cosmos
        return {"agent_tables": []}

    monkeypatch.setattr(web_app, "_compute_dashboard_data", fake_compute)
    monkeypatch.setattr(web_app, "is_us_market_open", lambda: False)
    monkeypatch.setattr(web_app.app.state, "scheduler", scheduler, raising=False)
    monkeypatch.setattr(web_app.app.state, "cosmos", cosmos, raising=False)
    client = TestClient(web_app.app)

    response = client.get("/api/dashboard")

    assert response.status_code == 200
    assert captured["buy_tracker"] is False
    assert captured["covered_call"] is True


def test_settings_context_and_save_round_trip_monitor_agent_gates(monkeypatch):
    persisted = {
        "scheduler": {
            "cron": "0 * * * *",
            "enabled": True,
            "reload_interval": 37,
            "agents": {"buy_tracker": False},
        }
    }

    class Cosmos:
        def get_settings(self):
            return copy.deepcopy(persisted)

        def save_settings(self, settings):
            persisted.clear()
            persisted.update(copy.deepcopy(settings))

    config = _Config()
    scheduler = _Scheduler(config)
    scheduler.reschedule = lambda cron: None
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(scheduler=scheduler))
    )
    cosmos = Cosmos()

    context = _build_settings_config_context(request, cosmos)
    assert context["monitor_buy_tracker_enabled"] is False
    assert context["monitor_covered_call_enabled"] is True

    monkeypatch.setattr(web_app, "_load_config", lambda: copy.deepcopy(persisted))
    monkeypatch.setattr(web_app, "_write_config", lambda config: None)

    class Form:
        def __init__(self):
            self.values = {
                "monitoring_enabled": "true",
                "cron_expr": "0 * * * *",
                "monitor_covered_call_enabled": "true",
                "monitor_cash_secured_put_enabled": "true",
                "monitor_buy_tracker_enabled": "false",
                "monitor_open_call_enabled": "true",
                "monitor_open_put_enabled": "false",
            }

        def get(self, key, default=""):
            return self.values.get(key, default)

    saved = _apply_settings_config(request, cosmos, Form())

    assert "Cron schedule" in saved
    assert persisted["scheduler"]["agents"] == {
        "covered_call": True,
        "cash_secured_put": True,
        "buy_tracker": False,
        "open_call_monitor": True,
        "open_put_monitor": False,
    }
    assert persisted["scheduler"]["reload_interval"] == 37
    assert scheduler.config.config["scheduler"]["agents"] == persisted["scheduler"]["agents"]


def _scheduler_for_monitor_gate_reload(cosmos_settings):
    import src.main as main_mod

    scheduler = main_mod.OptionsAgentScheduler.__new__(main_mod.OptionsAgentScheduler)
    scheduler.config = _Config({"buy_tracker": False})
    scheduler.config.config["scheduler"]["reload_interval"] = 37
    scheduler.cosmos = SimpleNamespace(get_settings=lambda: cosmos_settings)
    scheduler.registry = SimpleNamespace(
        get_task=lambda name: None,
        reload_from_cosmos=lambda *args: None,
    )
    scheduler.runner = SimpleNamespace(
        set_function_llms=lambda value: None,
        set_function_models=lambda value: None,
    )
    scheduler.config.function_llm_configs = dict
    scheduler.config.function_model_deployments = dict
    return scheduler


def test_cosmos_reload_updates_monitor_agent_gates_without_restart():
    scheduler = _scheduler_for_monitor_gate_reload({
        "scheduler": {
            "cron": "0 * * * *",
            "agents": {"buy_tracker": False},
        }
    })

    scheduler._reload_config_from_cosmos()

    assert is_monitor_agent_enabled(scheduler.config, "buy_tracker") is False


def test_cosmos_reload_replaces_false_with_missing_agents_default():
    scheduler = _scheduler_for_monitor_gate_reload({})

    scheduler._reload_config_from_cosmos()

    assert normalize_monitor_agent_gates(scheduler.config) == {
        name: True for name in MONITOR_AGENT_NAMES
    }
    assert scheduler.config.config["scheduler"]["reload_interval"] == 37


def test_cosmos_reload_replaces_false_with_malformed_agents_default():
    scheduler = _scheduler_for_monitor_gate_reload({
        "scheduler": {"cron": "0 * * * *", "agents": "invalid"}
    })

    scheduler._reload_config_from_cosmos()

    assert normalize_monitor_agent_gates(scheduler.config) == {
        name: True for name in MONITOR_AGENT_NAMES
    }


def test_cosmos_reload_replaces_false_with_invalid_member_default():
    scheduler = _scheduler_for_monitor_gate_reload({
        "scheduler": {
            "cron": "0 * * * *",
            "agents": {"buy_tracker": "false"},
        }
    })

    scheduler._reload_config_from_cosmos()

    assert is_monitor_agent_enabled(scheduler.config, "buy_tracker") is True


def test_cosmos_reload_replaces_false_with_explicit_true():
    scheduler = _scheduler_for_monitor_gate_reload({
        "scheduler": {
            "cron": "0 * * * *",
            "agents": {"buy_tracker": True},
        }
    })

    scheduler._reload_config_from_cosmos()

    assert is_monitor_agent_enabled(scheduler.config, "buy_tracker") is True


def test_monitor_master_toggle_remains_disabled_after_reschedule():
    registry = TaskRegistry()
    registry.register(
        "monitor_agents",
        "Monitor Agents",
        "scheduler",
        "0 * * * *",
        lambda: None,
    )
    config = _Config()
    config.config["scheduler"]["enabled"] = False
    registry.set_config(config)
    task = registry.get_task("monitor_agents")
    task._cron_changed = True

    registry.handle_cron_changes(datetime.now().astimezone())

    assert task.enabled is False


def test_frontend_exposes_all_monitor_agent_controls():
    root = Path(__file__).resolve().parents[2]
    component = (
        root / "frontend/src/components/SettingsConfigView.tsx"
    ).read_text(encoding="utf-8")
    settings_types = (
        root / "frontend/src/types/settings.ts"
    ).read_text(encoding="utf-8")

    for field in (
        "monitor_covered_call_enabled",
        "monitor_cash_secured_put_enabled",
        "monitor_buy_tracker_enabled",
        "monitor_open_call_enabled",
        "monitor_open_put_enabled",
    ):
        assert field in component
        assert field in settings_types
    assert "Global agent controls override symbol-level enrollment" in component


def test_frontend_dashboard_renders_global_deactivation_state():
    root = Path(__file__).resolve().parents[2]
    component = (
        root / "frontend/src/components/DashboardAgentTables.tsx"
    ).read_text(encoding="utf-8")
    auto_refresh = (
        root / "frontend/src/components/AutoRefresh.tsx"
    ).read_text(encoding="utf-8")
    dashboard_types = (
        root / "frontend/src/types/dashboard.ts"
    ).read_text(encoding="utf-8")

    assert "enabled?: boolean" in dashboard_types
    assert "Deactivated globally" in component
    assert "aria-disabled={globallyDisabled}" in component
    assert "aria-describedby={globallyDisabled ? deactivatedId : undefined}" in component
    assert 'globallyDisabled ? "bg-bg-input/30" : ""' in component
    assert "data.monitor_agent_enabled ?? {}" in auto_refresh
    assert component.count("globallyDisabled={globallyDisabled}") == 2
