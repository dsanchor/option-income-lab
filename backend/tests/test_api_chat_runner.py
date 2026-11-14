"""API-only optional news lifecycle and real SDK transport compatibility."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from agent_framework.exceptions import ToolExecutionException

import src.agent_runner as module
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
            self.functions = [SimpleNamespace(name="list_symbols")]

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
async def test_contextual_chat_real_news_initialize_timeout_keeps_business_tools(monkeypatch, caplog):
    methods = []

    async def upstream(request):
        if request.method != "POST":
            return httpx.Response(405)
        body = json.loads(request.content)
        method = body["method"]
        methods.append((request.url.host, method))
        if request.url.host == "news.internal" and method == "initialize":
            await asyncio.sleep(0.2)
        if method.startswith("notifications/"):
            return httpx.Response(204)
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "test-mcp", "version": "1.0.0"},
            }
        elif method == "tools/list":
            result = {"tools": [{
                "name": "list_symbols", "description": "List symbols",
                "inputSchema": {"type": "object", "properties": {}},
            }]}
        elif method == "tools/call":
            result = {"content": [{"type": "text", "text": "Business evidence"}]}
        else:
            assert method == "ping"
            result = {}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    class Agent:
        def __init__(self, **kwargs):
            self.tools = kwargs["tools"]
            assert "Financial news access is unavailable" in kwargs["instructions"]

        async def run(self, message):
            assert [tool.name for tool in self.tools] == ["oil-mcp"]
            result = await self.tools[0].call_tool("list_symbols")
            return SimpleNamespace(text=result[0].text)

    real_business = module.MCPStreamableHTTPTool
    real_news = module._FinancialNewsMCPTool
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        monkeypatch.setattr(module, "MCPStreamableHTTPTool",
                            lambda **kwargs: real_business(**kwargs, http_client=client))

        def news_tool(**kwargs):
            kwargs["request_timeout"] = 0.02
            return real_news(**kwargs, http_client=client)

        monkeypatch.setattr(module, "_FinancialNewsMCPTool", news_tool)
        monkeypatch.setattr(module, "Agent", Agent)
        monkeypatch.setenv("OIL_MCP_URL", "https://business.internal/mcp")
        monkeypatch.setenv("FINANCIAL_NEWS_MCP_URL", "https://news.internal/mcp")
        result = await module.run_contextual_chat(
            client="test-client", name="ActivityChat", instructions="Activity rules",
            message="Question",
        )
    assert result == "Business evidence"
    assert ("business.internal", "tools/call") in methods
    assert ("news.internal", "tools/list") not in methods
    assert "Optional financial news MCP discovery failed" in caplog.text


@pytest.mark.asyncio
async def test_real_sdk_shared_business_access_excludes_admin_and_mutating_tools(monkeypatch):
    methods = []

    def upstream(request):
        if request.method != "POST":
            return httpx.Response(200)
        body = json.loads(request.content)
        method = body["method"]
        methods.append(method)
        if method.startswith("notifications/"):
            return httpx.Response(204)
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "oil-mcp", "version": "1.0.0"},
            }
        elif method == "tools/list":
            result = {"tools": [
                {"name": name, "description": name,
                 "inputSchema": {"type": "object", "properties": {}}}
                for name in ("list_symbols", "delete_symbol", "get_settings")
            ]}
        elif method == "tools/call":
            assert body["params"]["name"] == "list_symbols"
            result = {"content": [{"type": "text", "text": "MSFT"}]}
        else:
            assert method == "ping"
            result = {}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    real_tool = module.MCPStreamableHTTPTool
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        monkeypatch.setattr(
            module, "MCPStreamableHTTPTool",
            lambda **kwargs: real_tool(**kwargs, http_client=client),
        )
        async with module.chat_mcp_tools(
            "Contextual chat rules", mcp_url="https://business.internal/mcp",
        ) as (tools, _):
            assert len(tools) == 1
            assert [f.name for f in tools[0].functions] == ["list_symbols"]
            result = await tools[0].call_tool("list_symbols")
            assert result[0].text == "MSFT"
    assert "tools/call" in methods
    assert "prompts/list" not in methods


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
                    "symbol": {"type": "string"},
                }, "anyOf": [{"required": ["symbol"]}, {"required": ["query"]}]},
            }]}
        elif method == "tools/call":
            assert body["params"]["arguments"] == {"symbol": "MSFT", "query": "earnings"}
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
                await tool.call_tool("get_financial_news", symbol="MSFT", query="earnings")
        else:
            result = await tool.call_tool("get_financial_news", symbol="MSFT", query="earnings")
            assert result[0].text == "Article"
    assert "ping" not in methods
    assert "prompts/list" not in methods
