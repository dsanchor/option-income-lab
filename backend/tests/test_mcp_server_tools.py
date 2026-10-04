"""Tests for the internal-only MCP server tool proxies (src/mcp_server.py).

Verifies that each tool issues the expected GET request to the internal
oil-api base URL with the expected query parameters, and that HTTP/transport
errors are surfaced as JSON error strings rather than raised exceptions.
"""

import json

import httpx
import pytest

from src.mcp_server import create_mcp_server


async def _call_tool(mcp, name, **kwargs):
    tool = mcp._tool_manager._tools[name]
    return await tool.run(kwargs)


@pytest.fixture
def mcp_server(monkeypatch):
    monkeypatch.setenv("OIL_API_INTERNAL_URL", "http://internal-api.local")
    return create_mcp_server()


def _mock_transport(expected_path, expected_params, json_body):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == expected_path
        actual_params = dict(request.url.params)
        assert actual_params == expected_params
        return httpx.Response(200, json=json_body)
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_list_symbols_proxies_to_internal_api(mcp_server, monkeypatch):
    transport = _mock_transport("/api/symbols", {}, [{"symbol": "AAPL"}])

    class FakeAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("src.mcp_server.httpx.AsyncClient", FakeAsyncClient)
    result = await _call_tool(mcp_server, "list_symbols")
    payload = json.loads(result)
    assert payload == [{"symbol": "AAPL"}]


@pytest.mark.asyncio
async def test_get_portfolio_holdings_omits_none_params(mcp_server, monkeypatch):
    transport = _mock_transport("/api/portfolio/holdings", {}, {"holdings": []})

    class FakeAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("src.mcp_server.httpx.AsyncClient", FakeAsyncClient)
    result = await _call_tool(mcp_server, "get_portfolio_holdings", account_id=None)
    text = result
    assert json.loads(text) == {"holdings": []}


@pytest.mark.asyncio
async def test_search_securities_passes_query_params(mcp_server, monkeypatch):
    transport = _mock_transport(
        "/api/securities/search", {"q": "AAPL", "limit": "5"}, {"candidates": []}
    )

    class FakeAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("src.mcp_server.httpx.AsyncClient", FakeAsyncClient)
    result = await _call_tool(mcp_server, "search_securities", q="AAPL", limit=5)
    text = result
    assert json.loads(text) == {"candidates": []}


@pytest.mark.asyncio
async def test_tool_surfaces_http_error_status_as_json(mcp_server, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    class FakeAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("src.mcp_server.httpx.AsyncClient", FakeAsyncClient)
    result = await _call_tool(mcp_server, "get_calendar")
    text = result
    payload = json.loads(text)
    assert "error" in payload
    assert "503" in payload["error"]


@pytest.mark.asyncio
async def test_tool_surfaces_connection_error_as_json(mcp_server, monkeypatch):
    def handler(request: httpx.Request):
        raise httpx.ConnectError("connection refused", request=request)

    class FakeAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("src.mcp_server.httpx.AsyncClient", FakeAsyncClient)
    result = await _call_tool(mcp_server, "get_dgi_top")
    text = result
    payload = json.loads(text)
    assert "error" in payload
    assert "Failed to reach oil-api" in payload["error"]


def test_mcp_scope_excludes_admin_and_config_tools(mcp_server):
    """Repository convention: MCP exposes only business-domain data — no
    admin, config, infra, or operational/debug endpoints."""
    tool_names = set(mcp_server._tool_manager._tools.keys())
    forbidden_substrings = ("admin", "config", "backfill", "reconciliation", "debug", "trigger")
    for name in tool_names:
        for forbidden in forbidden_substrings:
            assert forbidden not in name.lower(), f"tool {name} looks like an admin/config tool"
