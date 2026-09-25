from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient
from src import main as scheduler_main
from src import scheduler_registry
from src.scheduler_registry import TaskRegistry
from web import app as web_app
from web.app import _build_settings_config_context


class _Cosmos:
    def __init__(self, banner=None):
        self.banner = banner

    def get_settings(self):
        return {}

    def get_banner(self):
        return self.banner

    def get_all_activities(self, limit=1):
        return []


def _request_with_tasks(tasks=None):
    registry = SimpleNamespace(get_all_task_metadata=lambda: tasks or [])
    scheduler = SimpleNamespace(registry=registry)
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(scheduler=scheduler))
    )


def _start_registry(registry):
    worker = threading.Thread(target=registry._worker_loop, daemon=True)
    worker.start()
    return worker


def _stop_registry(registry, worker):
    registry._shutdown = True
    worker.join(timeout=2)


def _wait_until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not reached before timeout")


def test_banner_last_run_is_empty_only_when_no_persisted_or_runtime_run_exists():
    context = _build_settings_config_context(_request_with_tasks(), _Cosmos())

    assert context["banner_last_run"] == ""
    assert context["banner_last_run_iso"] == ""


def test_banner_last_run_falls_back_to_persisted_generation_after_restart():
    generated_at = "2026-09-24T12:34:56Z"
    context = _build_settings_config_context(
        _request_with_tasks(),
        _Cosmos({"generated_at": generated_at}),
    )

    assert context["banner_last_run"]
    assert context["banner_last_run_iso"] == "2026-09-24T12:34:56+00:00"


@pytest.mark.parametrize("generated_at", [None, "", "not-a-timestamp", "2026-09-24"])
def test_legacy_banner_without_valid_generated_at_remains_never(generated_at):
    context = _build_settings_config_context(
        _request_with_tasks(),
        _Cosmos({"generated_at": generated_at, "items": [{"text": "legacy"}]}),
    )

    assert context["banner_last_run"] == ""
    assert context["banner_last_run_iso"] == ""


def test_banner_trigger_waits_for_registry_success_and_returns_last_run(monkeypatch):
    calls = []

    class _Registry:
        def trigger_task_now(self, name, *, retain_result=False):
            calls.append((name, retain_result))
            return {
                "success": True,
                "message": "Dashboard Banner queued for execution",
                "run_id": "run-1",
            }

        def wait_for_run(self, run_id):
            assert run_id == "run-1"
            return {
                "success": True,
                "completed": True,
                "result": {"generated_at": "2026-09-24T13:44:00Z"},
                "completed_at": "2026-09-24T13:45:00+00:00",
            }

    scheduler = SimpleNamespace(config=object(), registry=_Registry())
    monkeypatch.setattr(web_app.app.state, "scheduler", scheduler, raising=False)
    client = TestClient(web_app.app)

    response = client.post("/api/trigger/banner_agent")

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["banner_last_run_iso"] == "2026-09-24T13:44:00+00:00"
    assert calls == [("banner_agent", True)]


def test_banner_trigger_propagates_registry_failure(monkeypatch):
    class _Registry:
        def trigger_task_now(self, name, *, retain_result=False):
            assert retain_result is True
            return {"success": True, "message": "queued", "run_id": "failed-run"}

        def wait_for_run(self, run_id):
            return {
                "success": False,
                "completed": True,
                "error": "Cosmos banner write failed",
            }

    scheduler = SimpleNamespace(config=object(), registry=_Registry())
    monkeypatch.setattr(web_app.app.state, "scheduler", scheduler, raising=False)
    client = TestClient(web_app.app)

    response = client.post("/api/trigger/banner_agent")

    assert response.status_code == 500
    assert response.json() == {"error": "Cosmos banner write failed"}


def test_registry_failure_does_not_advance_successful_last_run():
    registry = TaskRegistry()
    prior_success = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)

    def fail():
        raise RuntimeError("banner generation failed")

    registry.register("banner_agent", "Dashboard Banner", "banner_agent", "0 5 * * *", fail)
    task = registry.get_task("banner_agent")
    task.enabled = True
    task.last_success = prior_success
    worker = _start_registry(registry)
    try:
        queued = registry.trigger_task_now("banner_agent", retain_result=True)
        result = registry.wait_for_run(queued["run_id"], timeout=2)

        assert result["completed"] is True
        assert result["success"] is False
        assert result["error"] == "banner generation failed"
        assert task.last_run == task.last_attempt
        assert task.last_run > prior_success
        assert task.last_success == prior_success
        assert task.last_error == "banner generation failed"
    finally:
        _stop_registry(registry, worker)


def test_registry_success_advances_last_success_and_clears_error():
    registry = TaskRegistry()
    registry.register(
        "banner_agent",
        "Dashboard Banner",
        "banner_agent",
        "0 5 * * *",
        lambda: {"generated_at": "persisted"},
    )
    task = registry.get_task("banner_agent")
    task.enabled = True
    task.last_error = "older failure"
    worker = _start_registry(registry)
    try:
        queued = registry.trigger_task_now("banner_agent", retain_result=True)
        result = registry.wait_for_run(queued["run_id"], timeout=2)

        assert result["success"] is True
        assert result["result"] == {"generated_at": "persisted"}
        assert task.last_run == task.last_attempt
        assert task.last_success is not None
        assert task.last_success >= task.last_run
        assert task.last_error is None
    finally:
        _stop_registry(registry, worker)


@pytest.mark.parametrize(
    ("job", "expected_success", "expected_error"),
    [
        (lambda: "ok", True, None),
        (
            lambda: (_ for _ in ()).throw(RuntimeError("generic failure")),
            False,
            "generic failure",
        ),
    ],
)
def test_unrelated_task_last_run_remains_latest_attempt_on_completion(
    job,
    expected_success,
    expected_error,
):
    registry = TaskRegistry()
    prior = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
    registry.register("generic", "Generic", "generic", "* * * * *", job)
    task = registry.get_task("generic")
    task.enabled = True
    task.last_run = prior
    worker = _start_registry(registry)
    try:
        queued = registry.trigger_task_now("generic", retain_result=True)
        result = registry.wait_for_run(queued["run_id"], timeout=2)

        assert result["success"] is expected_success
        assert task.last_run == task.last_attempt
        assert task.last_run > prior
        assert task.last_error == expected_error
        if expected_success:
            assert task.last_success is not None
        else:
            assert task.last_success is None

        metadata = registry.get_all_task_metadata()[0]
        assert metadata["last_run"] == metadata["last_attempt"]
        assert metadata["last_error"] == expected_error
        assert (metadata["last_success"] is not None) is expected_success
    finally:
        _stop_registry(registry, worker)


def test_unrelated_task_timeout_advances_attempt_last_run(monkeypatch):
    monkeypatch.setattr(scheduler_registry, "_MAX_TASK_DURATION_SECONDS", 0.01)
    registry = TaskRegistry()
    release = threading.Event()
    started = threading.Event()
    prior = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)

    def blocked():
        started.set()
        release.wait(1)

    registry.register("generic", "Generic", "generic", "* * * * *", blocked)
    task = registry.get_task("generic")
    task.enabled = True
    task.last_run = prior
    worker = _start_registry(registry)
    try:
        queued = registry.trigger_task_now("generic", retain_result=True)
        assert started.wait(1)
        result = registry.wait_for_run(queued["run_id"], timeout=1)

        assert result["success"] is False
        assert "exceeded" in result["error"]
        assert task.last_run == task.last_attempt
        assert task.last_run > prior
        assert task.last_success is None
        assert task.last_error == result["error"]
    finally:
        release.set()
        _stop_registry(registry, worker)


def test_fire_and_forget_runs_never_retain_completion_state():
    registry = TaskRegistry()
    completed = 0
    completed_lock = threading.Lock()

    def job():
        nonlocal completed
        with completed_lock:
            completed += 1

    registry.register("manual", "Manual", "manual", "* * * * *", job)
    registry.get_task("manual").enabled = True
    worker = _start_registry(registry)
    try:
        for expected in range(1, 26):
            queued = registry.trigger_task_now("manual")
            assert queued["success"] is True
            assert "run_id" not in queued
            _wait_until(lambda: completed >= expected)
            _wait_until(lambda: not registry.get_task("manual").running)

        assert registry._runs == {}
    finally:
        _stop_registry(registry, worker)


def test_retained_run_supports_late_error_retrieval():
    registry = TaskRegistry()

    def fail():
        raise RuntimeError("late failure")

    registry.register("failure", "Failure", "failure", "* * * * *", fail)
    registry.get_task("failure").enabled = True
    worker = _start_registry(registry)
    try:
        queued = registry.trigger_task_now("failure", retain_result=True)
        run_id = queued["run_id"]
        _wait_until(lambda: registry._runs[run_id].completed.is_set())

        result = registry.wait_for_run(run_id, timeout=0)

        assert result["completed"] is True
        assert result["success"] is False
        assert result["error"] == "late failure"
        assert run_id not in registry._runs
    finally:
        _stop_registry(registry, worker)


def test_timed_out_wait_can_retrieve_run_after_completion():
    registry = TaskRegistry()
    release = threading.Event()
    started = threading.Event()

    def blocked():
        started.set()
        release.wait(2)
        return "done"

    registry.register("blocked", "Blocked", "blocked", "* * * * *", blocked)
    registry.get_task("blocked").enabled = True
    worker = _start_registry(registry)
    try:
        queued = registry.trigger_task_now("blocked", retain_result=True)
        run_id = queued["run_id"]
        assert started.wait(1)

        timed_out = registry.wait_for_run(run_id, timeout=0)
        assert timed_out["completed"] is False
        assert run_id in registry._runs

        release.set()
        result = registry.wait_for_run(run_id, timeout=2)
        assert result["success"] is True
        assert result["result"] == "done"
        assert run_id not in registry._runs
    finally:
        release.set()
        _stop_registry(registry, worker)


def test_concurrent_waiters_receive_same_result_and_cleanup_once():
    registry = TaskRegistry()
    release = threading.Event()
    started = threading.Event()

    def blocked():
        started.set()
        release.wait(2)
        return {"value": 7}

    registry.register("shared", "Shared", "shared", "* * * * *", blocked)
    registry.get_task("shared").enabled = True
    worker = _start_registry(registry)
    waiter_threads = []
    try:
        queued = registry.trigger_task_now("shared", retain_result=True)
        run_id = queued["run_id"]
        assert started.wait(1)
        results = []

        for _ in range(2):
            thread = threading.Thread(
                target=lambda: results.append(
                    registry.wait_for_run(run_id, timeout=2)
                )
            )
            thread.start()
            waiter_threads.append(thread)

        _wait_until(lambda: registry._runs[run_id].waiter_count == 2)
        release.set()
        for thread in waiter_threads:
            thread.join(timeout=2)

        assert len(results) == 2
        assert all(result["success"] is True for result in results)
        assert all(result["result"] == {"value": 7} for result in results)
        assert run_id not in registry._runs
    finally:
        release.set()
        for thread in waiter_threads:
            thread.join(timeout=2)
        _stop_registry(registry, worker)


def test_retention_capacity_never_deletes_active_run():
    registry = TaskRegistry()
    registry._MAX_RETAINED_COMPLETED_RUNS = 1
    third_started = threading.Event()
    release_third = threading.Event()

    registry.register("first", "First", "first", "* * * * *", lambda: 1)
    registry.register("second", "Second", "second", "* * * * *", lambda: 2)

    def third():
        third_started.set()
        release_third.wait(2)
        return 3

    registry.register("third", "Third", "third", "* * * * *", third)
    for task in registry.tasks.values():
        task.enabled = True
    worker = _start_registry(registry)
    try:
        first_id = registry.trigger_task_now(
            "first", retain_result=True
        )["run_id"]
        second_id = registry.trigger_task_now(
            "second", retain_result=True
        )["run_id"]
        third_id = registry.trigger_task_now(
            "third", retain_result=True
        )["run_id"]
        assert third_started.wait(1)

        assert third_id in registry._runs
        assert registry._runs[third_id].completed.is_set() is False
        completed_ids = {
            run_id
            for run_id, run in registry._runs.items()
            if run.completed.is_set()
        }
        assert completed_ids == {second_id}
        assert first_id not in registry._runs

        release_third.set()
        third_result = registry.wait_for_run(third_id, timeout=2)
        assert third_result["success"] is True
    finally:
        release_third.set()
        _stop_registry(registry, worker)


def test_banner_job_does_not_swallow_generation_failure(monkeypatch):
    async def fail(config, cosmos):
        raise RuntimeError("persist failed")

    monkeypatch.setattr(scheduler_main, "run_banner_agent", fail)
    scheduler = SimpleNamespace(
        config=SimpleNamespace(config={"banner_agent": {"enabled": True}}),
        cosmos=object(),
    )

    with pytest.raises(RuntimeError, match="persist failed"):
        asyncio.run(
            scheduler_main.OptionsAgentScheduler._run_banner_agent_async(scheduler)
        )


def test_settings_context_uses_verified_persisted_banner_generation():
    runtime_timestamp = "2026-09-24T13:45:00+00:00"
    tasks = [{
        "name": "banner_agent",
        "enabled": True,
        "cron": "0 5 * * *",
        "last_run": "2026-09-24T14:00:00+00:00",
        "last_success": runtime_timestamp,
        "next_run": None,
    }]
    context = _build_settings_config_context(
        _request_with_tasks(tasks),
        _Cosmos({"generated_at": "2026-09-24T12:34:56Z"}),
    )

    assert context["banner_last_run"]
    assert context["banner_last_run_iso"] == "2026-09-24T12:34:56+00:00"


def test_banner_prior_success_survives_later_failed_attempt_in_settings():
    prior_success = "2026-09-24T13:45:00+00:00"
    tasks = [{
        "name": "banner_agent",
        "enabled": True,
        "cron": "0 5 * * *",
        "last_run": "2026-09-24T14:00:00+00:00",
        "last_attempt": "2026-09-24T14:00:00+00:00",
        "last_success": prior_success,
        "last_error": "Cosmos banner write failed",
        "next_run": None,
    }]

    context = _build_settings_config_context(
        _request_with_tasks(tasks),
        _Cosmos({"generated_at": prior_success}),
    )

    assert context["banner_last_run_iso"] == prior_success


def test_banner_first_failure_stays_never_in_settings():
    tasks = [{
        "name": "banner_agent",
        "enabled": True,
        "cron": "0 5 * * *",
        "last_run": "2026-09-24T14:00:00+00:00",
        "last_attempt": "2026-09-24T14:00:00+00:00",
        "last_success": None,
        "last_error": "first banner failed",
        "next_run": None,
    }]

    context = _build_settings_config_context(
        _request_with_tasks(tasks),
        _Cosmos(),
    )

    assert context["banner_last_run"] == ""
    assert context["banner_last_run_iso"] == ""


def test_dashboard_status_exposes_banner_generation_for_auto_refresh(monkeypatch):
    generated_at = "2026-09-24T14:56:00Z"
    monkeypatch.setattr(web_app.app.state, "scheduler", None, raising=False)
    monkeypatch.setattr(
        web_app.app.state,
        "cosmos",
        _Cosmos({"generated_at": generated_at}),
        raising=False,
    )
    client = TestClient(web_app.app)

    response = client.get("/api/dashboard/status")

    assert response.status_code == 200
    assert response.json()["banner_generated_at"] == generated_at
