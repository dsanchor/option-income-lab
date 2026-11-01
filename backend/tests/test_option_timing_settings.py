from __future__ import annotations

import copy
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from src.option_timing import DEFAULT_CRON
from src.scheduler_registry import TaskRegistry
from starlette.testclient import TestClient
from web import app as web_app


@pytest.fixture
def settings(monkeypatch):
    local = {"option_timing": {"enabled": False, "cron": "15 16 * * 1-5", "other": 7}}
    persisted = {}
    writes = []
    cosmos_writes = []
    cosmos = object()

    def write(config):
        writes.append(copy.deepcopy(config))
        local.clear()
        local.update(copy.deepcopy(config))

    def save_cosmos(service, config):
        assert service is cosmos
        cosmos_writes.append(copy.deepcopy(config))
        persisted.clear()
        persisted.update(copy.deepcopy(config))

    monkeypatch.setattr(web_app, "_load_config", lambda: copy.deepcopy(local))
    monkeypatch.setattr(web_app, "_write_config", write)
    monkeypatch.setattr(
        web_app, "_load_settings_from_cosmos",
        lambda service: copy.deepcopy(persisted) if service else None,
    )
    monkeypatch.setattr(web_app, "_save_settings_to_cosmos", save_cosmos)
    monkeypatch.setattr(web_app.app.state, "cosmos", cosmos, raising=False)
    monkeypatch.setattr(web_app.app.state, "scheduler", None, raising=False)
    return SimpleNamespace(
        local=local, persisted=persisted, writes=writes,
        cosmos_writes=cosmos_writes, client=TestClient(web_app.app),
    )


def registry_scheduler():
    registry = TaskRegistry()
    registry.register(
        "option_timing", "Option Timing", "option_timing", DEFAULT_CRON,
        lambda: pytest.fail("Trading job must not execute"), cron_timezone="UTC",
    )
    config = SimpleNamespace(config={})
    registry.set_config(config)
    scheduler = SimpleNamespace(registry=registry, config=config, running=True)
    for name in (
        "summary", "plan_monitor", "options_chain", "dgi_screener",
        "calendar", "portfolio_enrichment", "symbol_pricing",
    ):
        setattr(scheduler, f"reschedule_{name}", lambda cron: None)
    scheduler.reschedule_option_timing = (
        lambda cron: registry.reschedule("option_timing", cron, config)
    )
    return scheduler


@pytest.mark.parametrize("empty_registry", [False, True])
def test_get_falls_back_to_local_and_cosmos_with_no_task_metadata(settings, monkeypatch, empty_registry):
    if empty_registry:
        monkeypatch.setattr(
            web_app.app.state, "scheduler",
            SimpleNamespace(registry=TaskRegistry()),
        )
    response = settings.client.get("/api/settings/config")
    assert response.status_code == 200
    assert response.json()["option_timing_enabled"] is False
    assert response.json()["option_timing_cron"] == "15 16 * * 1-5"
    settings.persisted["option_timing"] = {"enabled": True, "cron": "30 18 * * 1-5"}
    body = settings.client.get("/api/settings/config").json()
    assert body["option_timing_enabled"] is True
    assert body["option_timing_cron"] == "30 18 * * 1-5"
    assert body["option_timing_last_run_iso"] == ""
    assert body["option_timing_next_run_iso"] == ""
    settings.persisted.clear()
    settings.local.clear()
    body = settings.client.get("/api/settings/config").json()
    assert body["option_timing_enabled"] is True
    assert body["option_timing_cron"] == DEFAULT_CRON


@pytest.mark.parametrize("enabled", [False, True])
def test_get_registered_task_values_and_timestamps(settings, monkeypatch, enabled):
    scheduler = registry_scheduler()
    task = scheduler.registry.get_task("option_timing")
    task.enabled = enabled
    task.cron_expr = "45 19 * * 1-5"
    task.last_run = datetime(2026, 10, 30, 14, tzinfo=ZoneInfo("UTC"))
    task.next_run = datetime(2026, 10, 30, 17, tzinfo=ZoneInfo("UTC"))
    monkeypatch.setattr(web_app.app.state, "scheduler", scheduler)
    body = settings.client.get("/api/settings/config").json()
    assert body["option_timing_enabled"] is enabled
    assert body["option_timing_cron"] == task.cron_expr
    assert body["option_timing_last_run_iso"] == task.last_run.isoformat()
    assert body["option_timing_next_run_iso"] == task.next_run.isoformat()
    assert body["option_timing_last_run"]
    assert body["option_timing_next_run"]


@pytest.mark.parametrize("with_scheduler", [False, True])
@pytest.mark.parametrize("with_cosmos", [False, True])
def test_save_persists_both_fields_and_reschedules_in_utc(settings, monkeypatch, with_scheduler, with_cosmos):
    scheduler = registry_scheduler()
    if with_scheduler:
        monkeypatch.setattr(web_app.app.state, "scheduler", scheduler)
    if not with_cosmos:
        monkeypatch.setattr(web_app.app.state, "cosmos", None)
    settings.persisted["option_timing"] = dict(settings.local["option_timing"])
    response = settings.client.post("/api/settings/config", json={
        "option_timing_enabled": True, "option_timing_cron": " 30 18 * * 1-5 ",
    })
    assert response.status_code == 200
    assert "Option Timing" in response.json()["saved"]
    expected = {"enabled": True, "cron": "30 18 * * 1-5", "other": 7}
    assert settings.local["option_timing"] == expected
    if with_cosmos:
        assert settings.persisted["option_timing"] == expected
    else:
        assert settings.cosmos_writes == []
    if with_scheduler:
        assert scheduler.config.config["option_timing"] == {
            "enabled": True, "cron": "30 18 * * 1-5",
        }
        task = scheduler.registry.get_task("option_timing")
        assert task.enabled is True
        assert task._cron_changed
        scheduler.registry.handle_cron_changes(
            datetime(2026, 10, 30, 12, tzinfo=ZoneInfo("America/New_York"))
        )
        assert task.cron_expr == "30 18 * * 1-5"
        assert str(task.next_run.tzinfo) == "UTC"
        assert task.next_run.hour == 18
        assert task.next_run.minute == 30


@pytest.mark.parametrize("payload", [
    {}, {"option_timing_enabled": True}, {"option_timing_cron": "30 18 * * 1-5"},
])
def test_save_preserves_fields_not_submitted(settings, payload):
    before = copy.deepcopy(settings.local["option_timing"])
    settings.persisted["option_timing"] = dict(before)
    response = settings.client.post("/api/settings/config", json=payload)
    assert response.status_code == 200
    expected = dict(before)
    if "option_timing_enabled" in payload:
        expected["enabled"] = payload["option_timing_enabled"]
    if "option_timing_cron" in payload:
        expected["cron"] = payload["option_timing_cron"]
    assert settings.local["option_timing"] == expected
    assert settings.persisted["option_timing"] == expected
    assert ("Option Timing" in response.json()["saved"]) == bool(payload)


@pytest.mark.parametrize("cron", ["", "   ", "invalid cron", "0 99 * * *"])
def test_invalid_cron_rejects_entire_save_before_writes(settings, cron):
    response = settings.client.post("/api/settings/config", json={
        "option_timing_cron": cron, "option_timing_enabled": True,
        "cron_expr": "0 12 * * *", "monitoring_enabled": True,
    })
    assert response.status_code == 400
    assert "Option Timing cron expression" in response.json()["error"]
    assert settings.writes == []
    assert settings.cosmos_writes == []


def test_run_now_enqueues_once_and_rejects_overlap(settings, monkeypatch):
    scheduler = registry_scheduler()
    monkeypatch.setattr(web_app.app.state, "scheduler", scheduler)
    response = settings.client.post("/api/trigger/option_timing")
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["status"] == "queued"
    assert "queued" in response.json()["message"]
    response = settings.client.post("/api/trigger/option_timing")
    assert response.status_code == 400
    assert "already running" in response.json()["error"]
    assert scheduler.registry._job_queue.qsize() == 1
    assert scheduler.registry._job_queue.get_nowait() == ("option_timing", {})


@pytest.mark.parametrize("unavailable", ["absent", "stopped", "registry", "missing", "shutdown", "disabled"])
def test_run_now_unavailable_or_disabled_does_not_enqueue(settings, monkeypatch, unavailable):
    scheduler = registry_scheduler()
    registry = scheduler.registry
    if unavailable == "absent":
        scheduler = None
    elif unavailable == "stopped":
        scheduler.running = False
    elif unavailable == "registry":
        scheduler.registry = None
    elif unavailable == "missing":
        registry.tasks.clear()
    elif unavailable == "shutdown":
        registry._shutdown = True
    else:
        registry.get_task("option_timing").enabled = False
    monkeypatch.setattr(web_app.app.state, "scheduler", scheduler)
    response = settings.client.post("/api/trigger/option_timing")
    assert response.status_code == (400 if unavailable == "disabled" else 503)
    assert response.json()["error"]
    assert registry._job_queue.empty()
