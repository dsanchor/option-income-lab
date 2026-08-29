"""Integration lock for the corrected force-alpha trigger contract
(copilot-force-alpha-semantics-superseded.md, binding over the earlier
danny-force-alpha-design.md §12-D1 assumption and its first, reversed
"Settings forces Alpha" reading).

Semantic matrix under test:
  - Dashboard CC/CSP/monitor buttons (POST /api/trigger/{agent_type}) ->
    force_alpha defaults True unless the caller explicitly overrides it.
  - Settings "Run Now" for any scheduled task (POST
    /api/scheduler/tasks/{task_name}/run) -> force_alpha is always False,
    even though run_trigger stays "manual" for audit accuracy.
  - "Run Full"/"Full analysis" (POST /api/trigger-all) -> force_alpha is
    always False for every agent in the sequential sweep.
  - Scheduler cron sweep (OptionsAgentScheduler._run_all_agents) ->
    run_trigger="scheduled", force_alpha=False for every non-buy_tracker
    agent.

These are real-module seam tests: they exercise the actual FastAPI route
handlers / scheduler sweep functions in web/app.py and src/main.py, with
only the outermost I/O (agent functions, Cosmos, provider fetches)
replaced by fakes -- nothing about the force_alpha/run_trigger plumbing
itself is faked.
"""
from __future__ import annotations

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from starlette.testclient import TestClient

from web.app import app, _run_all_agents_sequentially, _default_full_analysis_status


class FakeTaskRegistry:
    """Records the kwargs a caller forwards to trigger_task_now, without
    spinning up TaskRegistry's real worker thread -- the trigger contract
    (what gets passed in), not the worker's dispatch mechanics, is what
    this test locks down."""

    def __init__(self):
        self.calls = []

    def trigger_task_now(self, name, **job_kwargs):
        self.calls.append((name, job_kwargs))
        return {"success": True, "message": f"{name} queued for execution"}


class FakeScheduler:
    def __init__(self, registry=None, config=object()):
        self.registry = registry
        self.config = config
        self.runner = object()
        self.cosmos = object()
        self.context_provider = object()


@pytest.fixture
def client():
    app.router.on_startup = []  # skip real Cosmos/provider startup wiring
    return TestClient(app, raise_server_exceptions=False)


class TestSettingsRunNowNeverForces:
    """POST /api/scheduler/tasks/{task_name}/run -- Settings' generic
    "Run Now" contract. Even though a human clicked it (run_trigger stays
    "manual" for audit accuracy), force_alpha must always be False."""

    def test_run_now_forwards_force_alpha_false(self, client):
        registry = FakeTaskRegistry()
        app.state.scheduler = FakeScheduler(registry=registry)
        try:
            resp = client.post("/api/scheduler/tasks/monitor_agents/run")
        finally:
            del app.state.scheduler

        assert resp.status_code == 200
        assert registry.calls, "trigger_task_now was never called"
        name, kwargs = registry.calls[0]
        assert name == "monitor_agents"
        assert kwargs["force_alpha"] is False
        assert kwargs["run_trigger"] == "manual"


class TestTriggerAllNeverForces:
    """POST /api/trigger-all ("Run Full"/"Full analysis") -- must stay
    due-only for every agent in the sweep, regardless of the per-agent
    manual-trigger default used by the dashboard's own trigger route."""

    def test_sequential_sweep_never_forces_any_agent(self, monkeypatch):
        calls = []

        def _make_fake(agent_name):
            async def _fake(config, runner, cosmos, context_provider,
                             symbol=None, run_trigger="scheduled", force_alpha=False):
                calls.append((agent_name, run_trigger, force_alpha))
            return _fake

        import src.covered_call_agent as cc_mod
        import src.cash_secured_put_agent as csp_mod
        import src.buy_tracker_agent as bt_mod
        import src.open_call_monitor_agent as ocm_mod
        import src.open_put_monitor_agent as opm_mod

        monkeypatch.setattr(cc_mod, "run_covered_call_analysis", _make_fake("covered_call"))
        monkeypatch.setattr(csp_mod, "run_cash_secured_put_analysis", _make_fake("cash_secured_put"))
        monkeypatch.setattr(bt_mod, "run_buy_tracker_analysis", _make_fake("buy_tracker"))
        monkeypatch.setattr(ocm_mod, "run_open_call_monitor", _make_fake("open_call_monitor"))
        monkeypatch.setattr(opm_mod, "run_open_put_monitor", _make_fake("open_put_monitor"))

        scheduler = FakeScheduler()
        status = _default_full_analysis_status()
        status["running"] = True
        _run_all_agents_sequentially(scheduler, status)

        assert len(calls) == 5
        for agent_name, run_trigger, force_alpha in calls:
            assert force_alpha is False, f"{agent_name} was forced during Full analysis"
            assert run_trigger == "manual"
        assert status["errors"] == []


class TestSchedulerCronNeverForces:
    """OptionsAgentScheduler._run_all_agents (the cron sweep) must always
    pass run_trigger="scheduled", force_alpha=False for every agent except
    buy_tracker, which never accepts the kwargs at all."""

    def test_cron_sweep_passes_scheduled_unforced(self, monkeypatch):
        from src.main import OptionsAgentScheduler
        import src.main as main_mod

        calls = []

        def _make_fake(agent_name, accepts_flags=True):
            if accepts_flags:
                async def _fake(config, runner, cosmos, ctx, run_trigger=None, force_alpha=None):
                    calls.append((agent_name, run_trigger, force_alpha))
            else:
                async def _fake(config, runner, cosmos, ctx):
                    calls.append((agent_name, None, None))
            return _fake

        monkeypatch.setattr(main_mod, "run_covered_call_analysis", _make_fake("covered_call"))
        monkeypatch.setattr(main_mod, "run_cash_secured_put_analysis", _make_fake("cash_secured_put"))
        monkeypatch.setattr(main_mod, "run_buy_tracker_analysis", _make_fake("buy_tracker", accepts_flags=False))
        monkeypatch.setattr(main_mod, "run_open_call_monitor", _make_fake("open_call_monitor"))
        monkeypatch.setattr(main_mod, "run_open_put_monitor", _make_fake("open_put_monitor"))

        scheduler = OptionsAgentScheduler.__new__(OptionsAgentScheduler)
        scheduler.config = object()
        scheduler.runner = object()
        scheduler.cosmos = object()
        scheduler.context_provider = object()

        asyncio.run(scheduler._run_all_agents_async())

        watched = [c for c in calls if c[0] != "buy_tracker"]
        assert len(watched) == 4
        for _, run_trigger, force_alpha in watched:
            assert run_trigger == "scheduled"
            assert force_alpha is False
        assert ("buy_tracker", None, None) in calls