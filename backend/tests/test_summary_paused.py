"""Summary agent must not repeat stale activities for paused watchlist symbols."""
import asyncio
import sys
import types

# Other test modules install an incomplete `agent_framework` stub in
# sys.modules (Agent only). Ensure the names agent_runner imports at module
# load time exist regardless of collection order.
_af = sys.modules.get("agent_framework")
if _af is not None and not hasattr(_af, "SkillsProvider"):
    # Another test module installed an incomplete stub (Agent only). Complete
    # it in place instead of replacing the (possibly real) module.
    _af.SkillsProvider = object
    if "agent_framework.openai" not in sys.modules:
        openai_stub = types.ModuleType("agent_framework.openai")
        openai_stub.OpenAIChatCompletionClient = object
        sys.modules["agent_framework.openai"] = openai_stub

from src import agent_runner as ar_mod  # noqa: E402
from src.agent_runner import AgentRunner  # noqa: E402
from src.llm import LlmConfig  # noqa: E402


class _FakeResponse:
    text = "📞 *ACTIVE CALLS*\n💤 No active calls"


class _FakeAgent:
    """Captures the prompt passed to agent.run()."""

    last_prompt = None

    def __init__(self, *args, **kwargs):
        pass

    async def run(self, prompt):
        _FakeAgent.last_prompt = prompt
        return _FakeResponse()


class _FakeTelegram:
    def _get_credentials(self):
        return {"token": "x", "chat_id": "y"}

    def send_message(self, msg):
        return True


class _FakeCosmos:
    def __init__(self, symbols, activities):
        self._symbols = symbols
        self._activities = activities

    def list_symbols(self):
        return self._symbols

    def get_recent_activities_by_symbol(self, limit_per_symbol=3, since=None):
        return {k: list(v) for k, v in self._activities.items()}

    def write_telemetry(self, *a, **k):
        pass


def _run(runner, cosmos, telegram):
    # Use an isolated loop and avoid asyncio.run(), which sets the global
    # policy's _set_called flag and breaks other tests that rely on
    # asyncio.get_event_loop().
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            runner.run_summary_agent(cosmos, telegram, activity_count=3)
        )
    finally:
        loop.close()


def _make_runner(monkeypatch):
    monkeypatch.setattr(ar_mod, "Agent", _FakeAgent)
    runner = AgentRunner(
        llm=LlmConfig(provider="azure", api_key="k", endpoint="https://e"),
        model="gpt-x",
    )
    monkeypatch.setattr(runner, "_get_client", lambda model=None, function_id=None: object())
    return runner


def test_paused_symbol_excluded_from_watching_and_activities(monkeypatch):
    symbols = [
        {"symbol": "KO", "watchlist": {"covered_call": True, "cash_secured_put": True},
         "positions": [], "watchlist_pause": {"until": "2999-01-01"}},
        {"symbol": "MSFT", "watchlist": {"covered_call": True},
         "positions": []},
    ]
    activities = {
        "KO": [{"agent_type": "covered_call", "activity": "WAIT",
                "timestamp": "2020-01-01T00:00:00Z"}],
        "MSFT": [{"agent_type": "covered_call", "activity": "WAIT",
                  "timestamp": "2025-01-01T00:00:00Z"}],
    }
    runner = _make_runner(monkeypatch)
    _run(runner, _FakeCosmos(symbols, activities), _FakeTelegram())

    prompt = _FakeAgent.last_prompt
    assert prompt is not None
    # KO is paused → its stale watchlist activity must not appear in the prompt.
    assert "2020-01-01T00:00:00Z" not in prompt
    # MSFT is active → its fresh activity remains.
    assert "2025-01-01T00:00:00Z" in prompt


def test_paused_symbol_keeps_monitor_activities(monkeypatch):
    """Position monitors keep running on paused symbols, so those stay."""
    symbols = [
        {"symbol": "KO",
         "watchlist": {"covered_call": True},
         "positions": [{"status": "active", "type": "call",
                        "strike": 60, "expiration": "2025-06-20",
                        "position_id": "p1"}],
         "watchlist_pause": {"until": "2999-01-01"}},
    ]
    activities = {
        "KO": [
            {"agent_type": "open_call_monitor", "activity": "HOLD",
             "position_id": "p1", "timestamp": "2025-05-01T00:00:00Z"},
            {"agent_type": "covered_call", "activity": "WAIT",
             "timestamp": "2020-01-01T00:00:00Z"},
        ],
    }
    runner = _make_runner(monkeypatch)
    _run(runner, _FakeCosmos(symbols, activities), _FakeTelegram())

    prompt = _FakeAgent.last_prompt
    assert prompt is not None
    # Stale watchlist activity dropped, monitor activity kept.
    assert "2020-01-01T00:00:00Z" not in prompt
    assert "2025-05-01T00:00:00Z" in prompt
