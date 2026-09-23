"""Rusty's targeted tests for the force-alpha API/scheduler PLUMBING only
(`backend/web/app.py` and `backend/src/scheduler_registry.py`).

Scope boundary: this file does NOT test the Alpha-gating formula, cooldown
neutrality, or notification suppression -- that logic lives entirely in
`AgentRunner` (Linus's `test_force_alpha_execution.py` / Basher's
adversarial cases cover it). This file only proves the plumbing this task
was responsible for:

  1. `/api/trigger/{agent_type}` parses and defaults the `run_trigger`/
     `force_alpha` contract (manual + force_alpha=True by default; caller
     may override).
  2. The in-flight guard keyed by (agent_type, symbol-or-"*") returns 409
     for a duplicate concurrent request and releases on completion.
  3. `_call_agent_func`'s introspection-based forwarding is inert for
     wrapper functions that don't yet declare `force_alpha`/`run_trigger`
     (e.g. buy_tracker, or any agent wrapper before Linus's pass-through
     lands) and forwards correctly once they do.
  4. `TaskRegistry.trigger_task_now`/`_worker_loop` (scheduler plumbing
     backing "Settings Run Now") forward per-invocation kwargs only to
     job_funcs that declare them, leaving every other registered task
     completely unaffected.

Hermetic: no network, no real Cosmos, no real LLM, no real agent execution
-- every agent wrapper function is monkeypatched to a fast fake that only
records how it was called.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from starlette.testclient import TestClient

from src.scheduler_registry import TaskRegistry
from web import app as web_app
from web.app import (
    _DASHBOARD_COMPLETED_RUN_RETENTION,
    _DashboardProcessState,
    _acquire_trigger_slot,
    _call_agent_func,
    _complete_dashboard_run,
    _dashboard_run_status_snapshot,
    _release_trigger_slot,
    _start_dashboard_run,
)


class _FakeScheduler:
    """Minimal stand-in for the real OptionsAgentScheduler, matching the
    shape already used by `tests/test_agent_model_settings.py` -- only the
    attributes the trigger endpoint actually reads."""

    config = object()
    runner = object()
    cosmos = object()
    context_provider = object()


def _client_with_fake_scheduler(monkeypatch):
    monkeypatch.setattr(web_app.app.state, "scheduler", _FakeScheduler(), raising=False)
    monkeypatch.setattr(
        web_app.app.state,
        "_dashboard_process_state",
        _DashboardProcessState(),
        raising=False,
    )
    return TestClient(web_app.app)


def _blocking_fake_agent(calls, started_evt, release_evt):
    """Builds a fake wrapper function accepting the full modern signature
    (symbol/run_trigger/force_alpha) that blocks until released, so the
    test can control exactly when the in-flight slot is freed."""

    async def _fake(config, runner, cosmos, context_provider, symbol=None,
                     run_trigger="scheduled", force_alpha=False):
        calls.append({"symbol": symbol, "run_trigger": run_trigger, "force_alpha": force_alpha})
        started_evt.set()
        release_evt.wait(timeout=5)

    return _fake


def test_trigger_agent_defaults_to_manual_forced_alpha(monkeypatch):
    client = _client_with_fake_scheduler(monkeypatch)
    calls: list = []
    started = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(
        "src.covered_call_agent.run_covered_call_analysis",
        _blocking_fake_agent(calls, started, release),
    )

    resp = client.post("/api/trigger/covered_call", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "triggered"
    assert body["agent_type"] == "covered_call"
    assert body["symbol"] is None
    assert body["run_trigger"] == "manual"
    assert body["force_alpha"] is True
    assert body["run_id"]
    assert body["started_at"]
    assert started.wait(timeout=2), "background thread never invoked the fake agent"
    assert calls[-1] == {"symbol": None, "run_trigger": "manual", "force_alpha": True}
    release.set()


def test_trigger_agent_allows_explicit_override(monkeypatch):
    client = _client_with_fake_scheduler(monkeypatch)
    calls: list = []
    started = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(
        "src.covered_call_agent.run_covered_call_analysis",
        _blocking_fake_agent(calls, started, release),
    )

    resp = client.post(
        "/api/trigger/covered_call",
        json={"symbol": "AAPL", "force_alpha": False, "run_trigger": "scheduled"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "triggered"
    assert body["agent_type"] == "covered_call"
    assert body["symbol"] == "AAPL"
    assert body["run_trigger"] == "scheduled"
    assert body["force_alpha"] is False
    assert body["run_id"]
    assert started.wait(timeout=2)
    assert calls[-1] == {"symbol": "AAPL", "run_trigger": "scheduled", "force_alpha": False}
    release.set()


def test_trigger_agent_duplicate_request_returns_409_then_releases(monkeypatch):
    client = _client_with_fake_scheduler(monkeypatch)
    calls: list = []
    started = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(
        "src.covered_call_agent.run_covered_call_analysis",
        _blocking_fake_agent(calls, started, release),
    )

    first = client.post("/api/trigger/covered_call", json={})
    assert first.status_code == 200
    assert started.wait(timeout=2)

    duplicate = client.post("/api/trigger/covered_call", json={})
    assert duplicate.status_code == 409
    dup_body = duplicate.json()
    assert dup_body["status"] == "already_running"
    assert dup_body["agent_type"] == "covered_call"
    assert dup_body["symbol"] is None
    assert dup_body["force_alpha"] is True
    assert "started_at" in dup_body

    # A different symbol for the SAME agent_type must NOT be blocked by the
    # in-flight slot for symbol=None -- different keys, independent runs.
    calls2: list = []
    started2 = threading.Event()
    release2 = threading.Event()
    monkeypatch.setattr(
        "src.covered_call_agent.run_covered_call_analysis",
        _blocking_fake_agent(calls2, started2, release2),
    )
    other_symbol = client.post("/api/trigger/covered_call", json={"symbol": "MSFT"})
    assert other_symbol.status_code == 200
    assert started2.wait(timeout=2)
    release2.set()

    # Release the original slot, then confirm the guard is cleared.
    release.set()
    deadline = time.time() + 2
    ok = False
    while time.time() < deadline:
        retry = client.post("/api/trigger/covered_call", json={})
        if retry.status_code == 200:
            ok = True
            break
        time.sleep(0.05)
    assert ok, "in-flight slot was never released after the background run finished"


def test_trigger_buy_tracker_force_alpha_is_inert_not_error(monkeypatch):
    """buy_tracker's wrapper doesn't (and per design never will) accept
    force_alpha -- `_call_agent_func`'s introspection guard must silently
    omit it rather than raising a TypeError."""
    client = _client_with_fake_scheduler(monkeypatch)
    calls: list = []

    async def fake_buy_tracker(config, runner, cosmos, context_provider, symbol=None):
        calls.append({"symbol": symbol})

    monkeypatch.setattr("src.buy_tracker_agent.run_buy_tracker_analysis", fake_buy_tracker)

    resp = client.post("/api/trigger/buy_tracker", json={})
    assert resp.status_code == 200
    assert resp.json()["force_alpha"] is True  # contract default is still reported

    deadline = time.time() + 2
    while not calls and time.time() < deadline:
        time.sleep(0.02)
    assert calls == [{"symbol": None}]


def test_successful_dashboard_trigger_updates_last_run_status(monkeypatch):
    client = _client_with_fake_scheduler(monkeypatch)
    calls: list = []

    async def fake_agent(config, runner, cosmos, context_provider, symbol=None,
                         run_trigger="scheduled", force_alpha=False):
        calls.append((symbol, run_trigger, force_alpha))

    monkeypatch.setattr(
        "src.cash_secured_put_agent.run_cash_secured_put_analysis",
        fake_agent,
    )

    response = client.post("/api/trigger/cash_secured_put", json={})
    assert response.status_code == 200

    deadline = time.time() + 2
    status = {}
    while time.time() < deadline:
        status = client.get("/api/dashboard/status").json()
        if status.get("agent_statuses", {}).get("cash_secured_put", {}).get("status") == "succeeded":
            break
        time.sleep(0.02)

    assert calls == [(None, "manual", True)]
    run_id = response.json()["run_id"]
    run = status["runs"][run_id]
    assert run["status"] == "succeeded"
    aggregate = status["agent_statuses"]["cash_secured_put"]
    assert aggregate["last_run"]
    assert status["agents"]["cash_secured_put"] == aggregate["last_run"]


def test_failed_dashboard_trigger_is_visible_as_failure_without_last_run(monkeypatch):
    client = _client_with_fake_scheduler(monkeypatch)

    async def failing_agent(config, runner, cosmos, context_provider, symbol=None,
                            run_trigger="scheduled", force_alpha=False):
        raise RuntimeError("local runner failed")

    monkeypatch.setattr(
        "src.open_put_monitor_agent.run_open_put_monitor",
        failing_agent,
    )

    response = client.post("/api/trigger/open_put_monitor", json={})
    assert response.status_code == 200

    deadline = time.time() + 2
    status = {}
    while time.time() < deadline:
        status = client.get("/api/dashboard/status").json()
        if status.get("agent_statuses", {}).get("open_put_monitor", {}).get("status") == "failed":
            break
        time.sleep(0.02)

    run_id = response.json()["run_id"]
    run = status["runs"][run_id]
    assert run["status"] == "failed"
    assert run["error"] == "local runner failed"
    assert status["agent_statuses"]["open_put_monitor"]["last_run"] is None
    assert status["agents"]["open_put_monitor"] is None


def test_concurrent_same_agent_runs_keep_symbol_and_error_scoped(monkeypatch):
    client = _client_with_fake_scheduler(monkeypatch)
    aapl_started = threading.Event()
    msft_started = threading.Event()
    release_aapl = threading.Event()
    release_msft = threading.Event()

    async def fake_agent(config, runner, cosmos, context_provider, symbol=None,
                         run_trigger="scheduled", force_alpha=False):
        if symbol == "AAPL":
            aapl_started.set()
            release_aapl.wait(timeout=5)
            raise RuntimeError("AAPL failed")
        msft_started.set()
        release_msft.wait(timeout=5)

    monkeypatch.setattr(
        "src.covered_call_agent.run_covered_call_analysis",
        fake_agent,
    )

    aapl = client.post("/api/trigger/covered_call", json={"symbol": "AAPL"}).json()
    msft = client.post("/api/trigger/covered_call", json={"symbol": "MSFT"}).json()
    assert aapl_started.wait(timeout=2)
    assert msft_started.wait(timeout=2)

    release_aapl.set()
    release_msft.set()
    deadline = time.time() + 2
    payload = {}
    while time.time() < deadline:
        payload = client.get("/api/dashboard/status").json()
        statuses = payload.get("runs", {})
        if (
            statuses.get(aapl["run_id"], {}).get("status") == "failed"
            and statuses.get(msft["run_id"], {}).get("status") == "succeeded"
        ):
            break
        time.sleep(0.02)

    assert payload["runs"][aapl["run_id"]]["symbol"] == "AAPL"
    assert payload["runs"][aapl["run_id"]]["error"] == "AAPL failed"
    assert payload["runs"][msft["run_id"]]["symbol"] == "MSFT"
    assert payload["runs"][msft["run_id"]]["error"] is None
    assert payload["agent_statuses"]["covered_call"]["run_id"] == msft["run_id"]


def test_last_success_survives_later_running_and_failed_attempt():
    class _State:
        pass

    state = _State()
    successful = _start_dashboard_run(state, "covered_call", "AAPL")
    _complete_dashboard_run(state, successful["run_id"])
    aggregates, _ = _dashboard_run_status_snapshot(state)
    first_success = aggregates["covered_call"]["last_run"]

    failed = _start_dashboard_run(state, "covered_call", "MSFT")
    aggregates, _ = _dashboard_run_status_snapshot(state)
    assert aggregates["covered_call"]["status"] == "running"
    assert aggregates["covered_call"]["last_run"] == first_success

    _complete_dashboard_run(state, failed["run_id"], error="MSFT failed")
    aggregates, runs = _dashboard_run_status_snapshot(state)
    assert aggregates["covered_call"]["status"] == "failed"
    assert aggregates["covered_call"]["error"] == "MSFT failed"
    assert aggregates["covered_call"]["last_run"] == first_success
    assert runs[successful["run_id"]]["error"] is None


def test_dashboard_run_retention_keeps_only_newest_completed_runs():
    class _State:
        pass

    state = _State()
    run_ids = []
    for index in range(_DASHBOARD_COMPLETED_RUN_RETENTION + 3):
        run = _start_dashboard_run(state, "buy_tracker", f"S{index}")
        run_ids.append(run["run_id"])
        _complete_dashboard_run(state, run["run_id"])

    _, runs = _dashboard_run_status_snapshot(state)
    assert len(runs) == _DASHBOARD_COMPLETED_RUN_RETENTION
    assert not set(run_ids[:3]) & set(runs)
    assert set(run_ids[3:]) == set(runs)


def test_call_agent_func_forwards_only_declared_kwargs():
    calls: list = []

    async def legacy_wrapper(config, runner, cosmos, context_provider, symbol=None):
        calls.append({"symbol": symbol})

    async def modern_wrapper(config, runner, cosmos, context_provider, symbol=None,
                              run_trigger="scheduled", force_alpha=False):
        calls.append({"symbol": symbol, "run_trigger": run_trigger, "force_alpha": force_alpha})

    import asyncio

    asyncio.run(_call_agent_func(
        legacy_wrapper, None, None, None, None,
        symbol="AAPL", run_trigger="manual", force_alpha=True,
    ))
    asyncio.run(_call_agent_func(
        modern_wrapper, None, None, None, None,
        symbol="MSFT", run_trigger="manual", force_alpha=True,
    ))

    assert calls == [
        {"symbol": "AAPL"},
        {"symbol": "MSFT", "run_trigger": "manual", "force_alpha": True},
    ]


def test_acquire_and_release_trigger_slot_scoped_by_agent_and_symbol():
    class _State:
        pass

    state = _State()

    first = _acquire_trigger_slot(state, "covered_call", None, True, "run-1")
    assert first is None  # claimed successfully

    blocked = _acquire_trigger_slot(state, "covered_call", None, True, "run-2")
    assert blocked is not None
    assert blocked["agent_type"] == "covered_call"
    assert blocked["symbol"] is None

    # Different symbol, same agent_type -- independent key, not blocked.
    other_symbol = _acquire_trigger_slot(state, "covered_call", "AAPL", True, "run-3")
    assert other_symbol is None

    # Different agent_type, same symbol -- independent key, not blocked.
    other_agent = _acquire_trigger_slot(state, "cash_secured_put", None, True, "run-4")
    assert other_agent is None

    _release_trigger_slot(state, "covered_call", None, "run-1")
    reacquired = _acquire_trigger_slot(state, "covered_call", None, False, "run-5")
    assert reacquired is None


def test_stale_trigger_slot_is_reclaimed(monkeypatch):
    class _State:
        pass

    state = _State()
    fake_time = [1000.0]
    monkeypatch.setattr("web.app.time.monotonic", lambda: fake_time[0])

    assert _acquire_trigger_slot(state, "covered_call", None, True, "run-1") is None
    # Still well within the max-duration window -- must remain blocked.
    fake_time[0] += 5
    assert _acquire_trigger_slot(state, "covered_call", None, True, "run-2") is not None

    # Advance past the scheduler's own max-task-duration constant (reused,
    # not reinvented, per the design doc) -- the stale slot must be
    # silently reclaimed rather than wedging this agent/symbol forever.
    from src.scheduler_registry import _MAX_TASK_DURATION_SECONDS
    fake_time[0] += _MAX_TASK_DURATION_SECONDS + 1
    assert _acquire_trigger_slot(state, "covered_call", None, True, "run-2") is None


def test_stale_owner_cannot_release_replacement_slot(monkeypatch):
    class _State:
        pass

    state = _State()
    fake_time = [1000.0]
    monkeypatch.setattr("web.app.time.monotonic", lambda: fake_time[0])

    assert _acquire_trigger_slot(state, "covered_call", "AAPL", True, "run-1") is None
    from src.scheduler_registry import _MAX_TASK_DURATION_SECONDS
    fake_time[0] += _MAX_TASK_DURATION_SECONDS + 1
    assert _acquire_trigger_slot(state, "covered_call", "AAPL", True, "run-2") is None

    _release_trigger_slot(state, "covered_call", "AAPL", "run-1")
    blocked = _acquire_trigger_slot(state, "covered_call", "AAPL", True, "run-3")
    assert blocked is not None
    assert blocked["_owner_token"] == "run-2"


def test_simultaneous_first_acquisition_uses_one_process_state(monkeypatch):
    class _State:
        pass

    state = _State()
    constructor_entered = threading.Event()
    allow_constructor = threading.Event()
    constructor_calls = []
    original_state_class = _DashboardProcessState

    def controlled_state_factory():
        constructor_calls.append(threading.get_ident())
        constructor_entered.set()
        assert allow_constructor.wait(timeout=2)
        return original_state_class()

    monkeypatch.setattr(web_app, "_DashboardProcessState", controlled_state_factory)
    start = threading.Barrier(3)
    results = {}

    def acquire(owner):
        start.wait()
        results[owner] = _acquire_trigger_slot(
            state, "covered_call", "AAPL", True, owner,
        )

    threads = [
        threading.Thread(target=acquire, args=("run-1",)),
        threading.Thread(target=acquire, args=("run-2",)),
    ]
    for thread in threads:
        thread.start()
    start.wait()
    assert constructor_entered.wait(timeout=2)
    allow_constructor.set()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()

    assert len(constructor_calls) == 1
    assert sum(result is None for result in results.values()) == 1
    blocked = next(result for result in results.values() if result is not None)
    assert blocked["_owner_token"] in {"run-1", "run-2"}


def test_trigger_thread_construction_failure_is_failed_and_immediately_retryable(monkeypatch):
    client = _client_with_fake_scheduler(monkeypatch)
    original_threading = web_app.threading

    def fail_thread_construction(*args, **kwargs):
        raise RuntimeError("thread construction failed")

    with monkeypatch.context() as setup_failure:
        setup_failure.setattr(
            web_app,
            "threading",
            SimpleNamespace(Thread=fail_thread_construction),
        )
        failed = client.post("/api/trigger/covered_call", json={"symbol": "AAPL"})

    assert failed.status_code == 503
    body = failed.json()
    assert body["status"] == "failed_to_start"
    assert body["error"] == "thread construction failed"
    failed_run = client.get("/api/dashboard/status").json()["runs"][body["run_id"]]
    assert failed_run["status"] == "failed"
    assert failed_run["error"] == "thread construction failed"
    assert failed_run["completed_at"]

    completed = threading.Event()

    async def successful_agent(*args, **kwargs):
        completed.set()

    monkeypatch.setattr(web_app, "threading", original_threading)
    monkeypatch.setattr(
        "src.covered_call_agent.run_covered_call_analysis",
        successful_agent,
    )
    retry = client.post("/api/trigger/covered_call", json={"symbol": "AAPL"})
    assert retry.status_code == 200
    assert retry.json()["status"] == "triggered"
    assert completed.wait(timeout=2)


def test_trigger_thread_start_failure_is_failed_and_immediately_retryable(monkeypatch):
    client = _client_with_fake_scheduler(monkeypatch)
    original_threading = web_app.threading

    class StartFailureThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("thread start failed")

    with monkeypatch.context() as setup_failure:
        setup_failure.setattr(
            web_app,
            "threading",
            SimpleNamespace(Thread=StartFailureThread),
        )
        failed = client.post("/api/trigger/covered_call", json={"symbol": "MSFT"})

    assert failed.status_code == 503
    body = failed.json()
    assert body["status"] == "failed_to_start"
    assert body["error"] == "thread start failed"
    failed_run = client.get("/api/dashboard/status").json()["runs"][body["run_id"]]
    assert failed_run["status"] == "failed"
    assert failed_run["error"] == "thread start failed"
    assert failed_run["completed_at"]

    completed = threading.Event()

    async def successful_agent(*args, **kwargs):
        completed.set()

    monkeypatch.setattr(web_app, "threading", original_threading)
    monkeypatch.setattr(
        "src.covered_call_agent.run_covered_call_analysis",
        successful_agent,
    )
    retry = client.post("/api/trigger/covered_call", json={"symbol": "MSFT"})
    assert retry.status_code == 200
    assert retry.json()["status"] == "triggered"
    assert completed.wait(timeout=2)


def test_task_registry_forwards_kwargs_only_to_job_funcs_that_declare_them():
    """Scheduler plumbing backing 'Settings Run Now': a manual trigger may
    attach force_alpha/run_trigger, but every task that hasn't opted in
    (i.e. every task except monitor_agents, until main.py's run_all_agents
    grows the parameter) must be completely unaffected."""
    registry = TaskRegistry()
    calls: list = []

    def legacy_job():
        calls.append({})

    def alpha_aware_job(force_alpha=False, run_trigger="scheduled"):
        calls.append({"force_alpha": force_alpha, "run_trigger": run_trigger})

    registry.register("legacy", "Legacy Task", "legacy", "* * * * *", legacy_job)
    registry.register("alpha", "Alpha Aware Task", "alpha", "* * * * *", alpha_aware_job)

    worker = threading.Thread(target=registry._worker_loop, daemon=True)
    worker.start()
    try:
        result = registry.trigger_task_now("legacy", force_alpha=True, run_trigger="manual")
        assert result["success"] is True
        deadline = time.time() + 2
        while registry.tasks["legacy"].running and time.time() < deadline:
            time.sleep(0.02)
        assert calls == [{}], "legacy job_func must never receive kwargs it doesn't declare"

        result2 = registry.trigger_task_now("alpha", force_alpha=True, run_trigger="manual")
        assert result2["success"] is True
        deadline = time.time() + 2
        while registry.tasks["alpha"].running and time.time() < deadline:
            time.sleep(0.02)
        assert calls[-1] == {"force_alpha": True, "run_trigger": "manual"}

        # A scheduled (cron) enqueue carries no kwargs at all -- must still
        # call the alpha-aware job_func with its own defaults, i.e.
        # force_alpha=False, exactly like today's unforced cron behavior.
        registry.tasks["alpha"].running = True
        registry._job_queue.put(("alpha", {}))
        deadline = time.time() + 2
        while registry.tasks["alpha"].running and time.time() < deadline:
            time.sleep(0.02)
        assert calls[-1] == {"force_alpha": False, "run_trigger": "scheduled"}
    finally:
        registry._shutdown = True
        worker.join(timeout=2)
