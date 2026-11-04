"""API-only optional news lifecycle and real SDK transport compatibility."""

import json
from types import SimpleNamespace

import httpx
import pytest
import src.agent_runner as module
from agent_framework.exceptions import ToolExecutionException
from src.api_chat_instructions import build_api_chat_prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("news_state", ["absent", "available", "unavailable", "missing_tool"])
@pytest.mark.parametrize("deployment", ["gpt-5.6-luna", "other-model"])
async def test_api_chat_optional_news_registration_and_cleanup(
    monkeypatch, caplog, news_state, deployment,
):
    events = []
    captured = {}

    class PrimaryTool:
        def __init__(self, **kwargs):
            self.name = kwargs["name"]

        async def __aenter__(self):
            events.append("primary-enter")
            return self

        async def __aexit__(self, *args):
            events.append("primary-close")

    class NewsTool:
        def __init__(self, **kwargs):
            self.name = kwargs["name"]
            captured["news_config"] = kwargs
            self.functions = (
                [] if news_state == "missing_tool"
                else [SimpleNamespace(name="get_financial_news")]
            )

        async def connect(self):
            events.append("news-connect")
            if news_state == "unavailable":
                raise TimeoutError("news unavailable")

        async def close(self):
            events.append("news-close")

    class Agent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run(self, message, *, options):
            captured["options"] = options
            events.append("model-run")
            return SimpleNamespace(text="Portfolio reply")

    monkeypatch.setattr(module, "MCPStreamableHTTPTool", PrimaryTool)
    monkeypatch.setattr(module, "_FinancialNewsMCPTool", NewsTool)
    monkeypatch.setattr(module, "Agent", Agent)
    runner = module.AgentRunner.__new__(module.AgentRunner)
    runner._get_client = lambda **kwargs: ("client", kwargs)
    runner._resolve_model_deployment = lambda **kwargs: deployment
    result = await runner.run_api_chat(
        prompt=build_api_chat_prompt(message="Show holdings"),
        mcp_url="https://oil.internal/mcp",
        financial_news_mcp_url=None if news_state == "absent" else "https://news.internal/mcp",
    )
    assert result == "Portfolio reply"
    assert captured["client"] == ("client", {"model": None, "function_id": "api_chat"})
    assert captured["options"] == ({} if "luna" in deployment else {"temperature": 0.3})
    assert [t.name for t in captured["tools"]] == (
        ["oil-mcp", "financial-news-mcp"] if news_state == "available" else ["oil-mcp"]
    )
    assert ("news access is unavailable" in captured["instructions"]) == (news_state != "available")
    assert events[0] == "primary-enter"
    assert events[-1] == "primary-close"
    if news_state != "absent":
        assert events.index("model-run") < events.index("news-close")
        assert captured["news_config"]["load_prompts"] is False
    if news_state in {"unavailable", "missing_tool"}:
        assert "Optional financial news MCP discovery failed" in caplog.text


@pytest.mark.asyncio
async def test_news_setup_context_does_not_swallow_model_or_tool_failure(monkeypatch):
    closed = []

    class NewsTool:
        def __init__(self, **kwargs):
            self.functions = [SimpleNamespace(name="get_financial_news")]

        async def connect(self):
            pass

        async def close(self):
            closed.append(True)

    monkeypatch.setattr(module, "_FinancialNewsMCPTool", NewsTool)
    with pytest.raises(RuntimeError, match="model/tool failed"):
        async with module._api_chat_news_tool("https://news.internal/mcp"):
            raise RuntimeError("model/tool failed")
    assert closed == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("call_error", [False, True])
async def test_real_sdk_supports_upstream_session_protocol_without_ping_or_prompts(call_error):
    """Mirror pinned upstream: 2024 protocol, session headers, GET/DELETE 405."""
    methods = []

    def upstream(request):
        if request.method != "POST":
            return httpx.Response(405)
        body = json.loads(request.content)
        method = body["method"]
        methods.append(method)
        if method == "initialize":
            return httpx.Response(
                200,
                headers={"Mcp-Session-Id": "upstream-session"},
                json={"jsonrpc": "2.0", "id": body["id"], "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "FinanceNews-MCP", "version": "1.0.0"},
                }},
            )
        assert request.headers["Mcp-Session-Id"] == "upstream-session"
        if method.startswith("notifications/"):
            return httpx.Response(204)
        if method == "tools/list":
            result = {"tools": [{
                "name": "get_financial_news",
                "description": "Fetch financial news",
                "inputSchema": {"type": "object", "properties": {
                    "query": {"type": "string"},
                }, "required": ["query"]},
            }]}
        elif method == "tools/call":
            assert body["params"]["arguments"] == {"query": "Apple AAPL earnings"}
            result = {
                "content": [{"type": "text", "text": "Upstream news failed" if call_error else "Article"}],
                "isError": call_error,
            }
        else:
            return httpx.Response(400, json={
                "jsonrpc": "2.0", "id": body.get("id"),
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            })
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client,
        module._FinancialNewsMCPTool(
            name="financial-news-mcp",
            url="https://news.internal/mcp",
            http_client=client,
            request_timeout=2,
            load_prompts=False,
            terminate_on_close=False,
            allowed_tools=["get_financial_news"],
        ) as tool,
    ):
        assert [f.name for f in tool.functions] == ["get_financial_news"]
        assert "tools/call" not in methods
        if call_error:
            with pytest.raises(ToolExecutionException, match="Upstream news failed"):
                await tool.call_tool("get_financial_news", query="Apple AAPL earnings")
        else:
            result = await tool.call_tool("get_financial_news", query="Apple AAPL earnings")
            assert result[0].text == "Article"
    assert "ping" not in methods
    assert "prompts/list" not in methods
