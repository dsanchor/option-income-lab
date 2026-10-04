#!/usr/bin/env python3
"""Entry point for the internal-only MCP (Model Context Protocol) server.

Runs the ``oil-mcp`` FastMCP server (see ``src/mcp_server.py``) as a
standalone ASGI app via uvicorn, in the same Docker image as the main API
but as a separate Container App with ``ingress.external: false``.

  python run_mcp.py                  # uses config.yaml host/port
  python run_mcp.py --port 9001      # override port

Environment variables:
  OIL_API_INTERNAL_URL          Base URL of the internal api Container App
                                 (defaults to http://localhost:8000 for
                                 local dev — see src/mcp_server.py).
  MCP_REQUEST_TIMEOUT_SECONDS   Timeout for proxied calls to oil-api
                                 (defaults to 30 seconds).
"""

import argparse

from dotenv import load_dotenv
import yaml
import uvicorn

load_dotenv()  # Load .env file if present


def _load_config():
    try:
        with open("config.yaml", "r") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def _host_port(args, config):
    mcp_cfg = config.get("mcp", {}) or {}
    host = mcp_cfg.get("host", "0.0.0.0")
    port = args.port if args.port is not None else mcp_cfg.get("port", 8001)
    return host, port


def main():
    parser = argparse.ArgumentParser(description="Option Income Lab MCP server")
    parser.add_argument("--port", type=int, default=None, help="Override MCP server port")
    args = parser.parse_args()

    config = _load_config()
    host, port = _host_port(args, config)

    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Mount, Route

    from src.mcp_server import create_mcp_server

    mcp = create_mcp_server()
    mcp_app = mcp.streamable_http_app()

    async def healthz(_request):
        return PlainTextResponse("ok")

    app = Starlette(
        routes=[Route("/healthz", healthz), Mount("/", app=mcp_app)],
        lifespan=mcp_app.router.lifespan_context,
    )
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
