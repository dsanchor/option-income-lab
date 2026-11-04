"""Exercise upstream HTTP sessions without invoking Finnhub or needing a real key."""

import http.client
import json
import sys
import time
import urllib.error
import urllib.request


def validate_tool_schemas(tools):
    forbidden = {"oneOf", "anyOf", "allOf", "enum", "const", "not"}
    for tool in tools:
        schema = tool["inputSchema"]
        assert schema.get("type") == "object", f"{tool['name']}: root must be object"
        assert not forbidden.intersection(schema), f"{tool['name']}: provider rejects root schema keywords"
    assert any(tool["name"] == "get_financial_news" for tool in tools)


def smoke(base):
    for attempt in range(30):
        try:
            with urllib.request.urlopen(f"{base}/health", timeout=3) as response:
                assert json.load(response)["status"] == "healthy"
            break
        except (OSError, http.client.HTTPException):
            if attempt == 29:
                raise
            time.sleep(1)

    def rpc(method, number, session=None):
        headers = {"Content-Type": "application/json"}
        if session:
            headers["Mcp-Session-Id"] = session
        request = urllib.request.Request(
            f"{base}/mcp", headers=headers,
            data=json.dumps({
                "jsonrpc": "2.0", "id": number, "method": method,
                "params": {
                    "protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "deployment-smoke", "version": "1.0"},
                } if method == "initialize" else {},
            }).encode(),
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response), response.headers.get("Mcp-Session-Id")

    initialized, session = rpc("initialize", 1)
    assert session and initialized["result"]["capabilities"]["tools"] == {}
    listed, _ = rpc("tools/list", 2, session)
    validate_tool_schemas(listed["result"]["tools"])
    try:
        rpc("tools/list", 3)
    except urllib.error.HTTPError as error:
        assert error.code == 400
    else:
        raise AssertionError("Sessionless tools/list was incorrectly accepted")
    print("Health, initialization, provider-compatible tools/list, and session rejection passed.")


if __name__ == "__main__":
    smoke(sys.argv[1].rstrip("/"))
