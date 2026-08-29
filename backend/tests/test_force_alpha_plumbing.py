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

from starlette.testclient import TestClient

from src.scheduler_registry import TaskRegistry
from web import app as web_app
from web.app import (
    _acquire_trigger_slot,
    _call_agent_func,
    _release_trigger_slot,
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
    # Ensure a clean in-flight registry per test (module-level app.state
    # persists across tests since `web_app.app` is a process-wide singleton).
    monkeypatch.setattr(web_app.app.state, "_trigger_inflight", {}, raising=False)
    monkeypatch.setattr(web_app.app.state, "_trigger_inflight_lock", threading.Lock(), raising=False)
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
    assert body == {
        "status": "triggered",
        "agent_type": "covered_call",
        "symbol": None,
        "run_trigger": "manual",
        "force_alpha": True,
    }
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
    assert body == {
        "status": "triggered",
        "agent_type": "covered_call",
        "symbol": "AAPL",
        "run_trigger": "scheduled",
        "force_alpha": False,
    }
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

    first = _acquire_trigger_slot(state, "covered_call", None, True)
    assert first is None  # claimed successfully

    blocked = _acquire_trigger_slot(state, "covered_call", None, True)
    assert blocked is not None
    assert blocked["agent_type"] == "covered_call"
    assert blocked["symbol"] is None

    # Different symbol, same agent_type -- independent key, not blocked.
    other_symbol = _acquire_trigger_slot(state, "covered_call", "AAPL", True)
    assert other_symbol is None

    # Different agent_type, same symbol -- independent key, not blocked.
    other_agent = _acquire_trigger_slot(state, "cash_secured_put", None, True)
    assert other_agent is None

    _release_trigger_slot(state, "covered_call", None)
    reacquired = _acquire_trigger_slot(state, "covered_call", None, False)
    assert reacquired is None


def test_stale_trigger_slot_is_reclaimed(monkeypatch):
    class _State:
        pass

    state = _State()
    fake_time = [1000.0]
    monkeypatch.setattr("web.app.time.monotonic", lambda: fake_time[0])

    assert _acquire_trigger_slot(state, "covered_call", None, True) is None
    # Still well within the max-duration window -- must remain blocked.
    fake_time[0] += 5
    assert _acquire_trigger_slot(state, "covered_call", None, True) is not None

    # Advance past the scheduler's own max-task-duration constant (reused,
    # not reinvented, per the design doc) -- the stale slot must be
    # silently reclaimed rather than wedging this agent/symbol forever.
    from src.scheduler_registry import _MAX_TASK_DURATION_SECONDS
    fake_time[0] += _MAX_TASK_DURATION_SECONDS + 1
    assert _acquire_trigger_slot(state, "covered_call", None, True) is None


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
