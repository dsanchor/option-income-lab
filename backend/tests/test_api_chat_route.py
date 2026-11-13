"""Route-level tests for the `/api/chat` `mode == "api"` branch (API Chat).

Hermetic: no real LLM or MCP call. Mocks `AgentRunner.run_api_chat` and the
LLM config validation seam so only route wiring (request parsing, OIL_MCP_URL
gating, error mapping) is under test.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
import web.app as app_module
from web.app import app


@pytest.fixture
def client():
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_api_chat_mode_missing_mcp_url_returns_503(client, monkeypatch):
    monkeypatch.delenv("OIL_MCP_URL", raising=False)
    async with client as ac:
        resp = await ac.post(
            "/api/chat",
            json={"mode": "api", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code == 503
    assert "OIL_MCP_URL" in resp.json()["error"]


@pytest.mark.asyncio
async def test_api_chat_mode_requires_user_as_last_message(client, monkeypatch):
    monkeypatch.setenv("OIL_MCP_URL", "https://mcp.internal/mcp")
    async with client as ac:
        resp = await ac.post(
            "/api/chat",
            json={
                "mode": "api",
                "messages": [{"role": "assistant", "content": "hi"}],
            },
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("news_url", [None, "https://news.internal/mcp"])
async def test_api_chat_mode_happy_path_returns_agent_reply(client, monkeypatch, news_url):
    monkeypatch.setenv("OIL_MCP_URL", "https://mcp.internal/mcp")
    monkeypatch.delenv("FINANCIAL_NEWS_MCP_URL", raising=False)
    if news_url:
        monkeypatch.setenv("FINANCIAL_NEWS_MCP_URL", news_url)

    from src.llm import LlmConfig

    class FakeConfig:
        def llm_config(self):
            return LlmConfig(provider="azure", api_key="x", endpoint="https://example.test")

        model_deployment = "gpt-5.6-luna"

        def function_llm_configs(self):
            return {}

        def function_model_deployments(self):
            return {}

    monkeypatch.setattr(
        app_module, "_llm_settings_response", lambda function_id=None: (FakeConfig(), None)
    )

    captured = {}

    async def fake_run_api_chat(self, *, prompt, mcp_url, financial_news_mcp_url):
        captured["mcp_url"] = mcp_url
        captured["news_url"] = financial_news_mcp_url
        captured["message"] = prompt.message
        return "Here are your holdings: ..."

    # app.py imports AgentRunner lazily inside the route — patch the class
    # directly in its defining module so that lazy import picks up the fake.
    from src.agent_runner import AgentRunner

    monkeypatch.setattr(AgentRunner, "run_api_chat", fake_run_api_chat, raising=True)

    async with client as ac:
        resp = await ac.post(
            "/api/chat",
            json={
                "mode": "api",
                "messages": [{"role": "user", "content": "What are my holdings?"}],
                "financial_news_mcp_url": "https://client-controlled.invalid/mcp",
            },
        )
    assert resp.status_code == 200
    assert resp.json()["reply"] == "Here are your holdings: ..."
    assert captured["mcp_url"] == "https://mcp.internal/mcp"
    assert captured["news_url"] == news_url
    assert "What are my holdings?" in captured["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["portfolio", "quick-analysis"])
async def test_legacy_conversational_modes_send_no_provider_token_cap(client, monkeypatch, mode):
    from src.llm import LlmConfig

    cfg = SimpleNamespace(
        model_for=lambda function_id: "configured-model",
        llm_config_for_function=lambda function_id: LlmConfig(
            "azure", "key", "https://example.test",
        ),
    )
    monkeypatch.setattr(app_module, "_llm_settings_response", lambda function_id: (cfg, None))
    monkeypatch.setattr(app_module.app.state, "cosmos", None)
    provider = Mock()
    reply = "Unabridged response " * 2000
    provider.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=reply))],
    )
    monkeypatch.setattr("src.llm.create_sync_chat_client", lambda config: provider)
    messages = [{"role": "user", "content": "question " * 2000}]
    async with client as ac:
        resp = await ac.post("/api/chat", json={
            "mode": mode, "messages": messages,
            "symbol_data": {"symbol": "MSFT", "market": "XNAS", "data": {}},
        })
    assert resp.status_code == 200
    assert resp.json()["reply"] == reply
    options = provider.chat.completions.create.call_args.kwargs
    assert options == {
        "model": "configured-model",
        "temperature": 0.7,
        "messages": [options["messages"][0], *messages],
    }
