"""Internal-only MCP (Model Context Protocol) server.

Exposes a small, explicit allowlist of read-only business-domain endpoints
(symbols, portfolio, options, screeners, economics, calendar, plans, alerts,
DGI) as MCP tools. Never exposes admin, configuration, infrastructure, or
operational/debug endpoints — this is an intentional scope boundary (see
`.squad/decisions.md`: "MCP scope").

Every tool is a thin proxy: it makes an HTTP GET call to the existing
`api` Container App (reached over ``OIL_API_INTERNAL_URL``) and returns the
JSON body as text. No business logic lives here and Cosmos is never touched
directly — this keeps the MCP surface trivially auditable against the REST
API it mirrors.

This server is run as a separate process (see ``run_mcp.py``) using the same
backend Docker image as the main API, with ``ingress.external: false`` in
Azure Container Apps — it is never reachable from outside the Container
Apps environment.
"""

import json
import logging
import os
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

logger = logging.getLogger(__name__)

DEFAULT_OIL_API_BASE_URL = "http://localhost:8000"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0


def _oil_api_base_url() -> str:
    return (os.environ.get("OIL_API_INTERNAL_URL") or DEFAULT_OIL_API_BASE_URL).rstrip("/")


def _request_timeout() -> float:
    raw = os.environ.get("MCP_REQUEST_TIMEOUT_SECONDS")
    try:
        return float(raw) if raw else DEFAULT_REQUEST_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        return DEFAULT_REQUEST_TIMEOUT_SECONDS


async def _get_json(path: str, params: Optional[dict[str, Any]] = None) -> str:
    """Proxy a GET request to the internal oil-api and return the raw JSON body as text.

    Errors are returned as a JSON error string (never raised) so the calling
    agent can see and relay them instead of the whole tool call failing silently.
    """
    clean_params = {k: v for k, v in (params or {}).items() if v is not None}
    url = f"{_oil_api_base_url()}{path}"
    try:
        async with httpx.AsyncClient(timeout=_request_timeout()) as client:
            response = await client.get(url, params=clean_params)
        if response.status_code >= 400:
            return json.dumps({
                "error": f"oil-api returned HTTP {response.status_code}",
                "path": path,
                "body": response.text,
            })
        return response.text
    except httpx.HTTPError as exc:
        logger.warning("MCP tool proxy call failed path=%s error=%s", path, exc)
        return json.dumps({
            "error": f"Failed to reach oil-api: {exc}",
            "path": path,
        })



def create_mcp_server() -> FastMCP:
    """Build and return the configured FastMCP server instance.

    DNS-rebinding protection is disabled because this server is reached via a
    dynamic Azure Container Apps internal FQDN (not localhost). This is safe
    here because the container has ``ingress.external: false`` — network-level
    isolation inside the Container Apps environment is the real defense, not
    Host-header checking.
    """
    mcp = FastMCP(
        name="oil-mcp",
        instructions=(
            "Read-only tools for the Option Income Lab portfolio: symbols, "
            "holdings, movements, accounts, securities search, options "
            "screener, best-options, economics (overview/dividends), "
            "calendar, plans, alerts, and the DGI (Dividend Growth Investing) "
            "top list. All tools are GET-only proxies to internal business "
            "endpoints — there are no write/mutating tools."
        ),
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        ),
    )

    @mcp.tool()
    async def list_symbols() -> str:
        """List every tracked Symbol with its configuration and enrichment data."""
        return await _get_json("/api/symbols")

    @mcp.tool()
    async def get_symbol_detail(symbol: str) -> str:
        """Get unified detail for one Symbol (accepts MIC:TICKER or a bare ticker)."""
        return await _get_json(f"/api/symbols/{symbol}/detail")

    @mcp.tool()
    async def search_securities(q: str, limit: int = 10) -> str:
        """Search the security master catalog by ticker, company name, or alias fragment."""
        return await _get_json("/api/securities/search", {"q": q, "limit": limit})

    @mcp.tool()
    async def get_portfolio_holdings(account_id: Optional[str] = None) -> str:
        """Get derived current portfolio holdings (optionally filtered by account_id)."""
        return await _get_json("/api/portfolio/holdings", {"account_id": account_id})

    @mcp.tool()
    async def get_portfolio_movements(
        account_id: Optional[str] = None,
        security_id: Optional[str] = None,
        txn_type: Optional[str] = None,
        option_position_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """Get paginated portfolio ledger movements (BUY/SELL/DIVIDEND/TRANSFER/options)."""
        return await _get_json(
            "/api/portfolio/movements",
            {
                "account_id": account_id,
                "security_id": security_id,
                "txn_type": txn_type,
                "option_position_id": option_position_id,
                "date_from": date_from,
                "date_to": date_to,
                "limit": limit,
                "offset": offset,
            },
        )

    @mcp.tool()
    async def list_portfolio_accounts() -> str:
        """List every broker account on the portfolio."""
        return await _get_json("/api/portfolio/accounts")

    @mcp.tool()
    async def get_economics_overview(
        year: Optional[int] = None,
        month: Optional[str] = None,
        symbol: Optional[str] = None,
        source: Optional[str] = None,
        account_id: Optional[str] = None,
        currency: Optional[str] = None,
    ) -> str:
        """Get aggregated portfolio economics (realized P&L, dividends, premiums)."""
        return await _get_json(
            "/api/economics/overview",
            {
                "year": year,
                "month": month,
                "symbol": symbol,
                "source": source,
                "account_id": account_id,
                "currency": currency,
            },
        )

    @mcp.tool()
    async def get_economics_dividends(
        year: Optional[int] = None,
        month: Optional[str] = None,
        symbol: Optional[str] = None,
        account_id: Optional[str] = None,
        currency: Optional[str] = None,
    ) -> str:
        """Get dividend income detail, optionally filtered by year/month/symbol/account/currency."""
        return await _get_json(
            "/api/economics/dividends",
            {
                "year": year,
                "month": month,
                "symbol": symbol,
                "account_id": account_id,
                "currency": currency,
            },
        )

    @mcp.tool()
    async def get_options_screener(
        side: str = "call",
        symbols: Optional[str] = None,
        preferences: Optional[str] = None,
        min_annualized_return_pct: Optional[float] = None,
        min_abs_delta: Optional[float] = None,
        max_abs_delta: Optional[float] = None,
        dte_min: int = 0,
        dte_max: int = 45,
        min_open_interest: Optional[float] = None,
        limit: int = 100,
    ) -> str:
        """Screen precomputed covered-call/cash-secured-put option candidates across symbols."""
        return await _get_json(
            "/api/screener/options",
            {
                "side": side,
                "symbols": symbols,
                "preferences": preferences,
                "min_annualized_return_pct": min_annualized_return_pct,
                "min_abs_delta": min_abs_delta,
                "max_abs_delta": max_abs_delta,
                "dte_min": dte_min,
                "dte_max": dte_max,
                "min_open_interest": min_open_interest,
                "limit": limit,
            },
        )

    @mcp.tool()
    async def get_symbol_best_options(
        symbol: str,
        side: str = "both",
        dte_min: int = 0,
        dte_max: int = 45,
    ) -> str:
        """Get precomputed best option candidates (calls/puts) for one Symbol."""
        return await _get_json(
            f"/api/symbols/{symbol}/best-options",
            {"side": side, "dte_min": dte_min, "dte_max": dte_max},
        )

    @mcp.tool()
    async def get_calendar() -> str:
        """Get upcoming earnings and ex-dividend events for tracked symbols."""
        return await _get_json("/api/calendar")

    @mcp.tool()
    async def list_plans(status: Optional[str] = None, symbol: Optional[str] = None) -> str:
        """List action plans, optionally filtered by status or symbol."""
        return await _get_json("/api/plans", {"status": status, "symbol": symbol})

    @mcp.tool()
    async def list_alerts(agent_type: Optional[str] = None, since: Optional[str] = None, limit: int = 100) -> str:
        """List recent agent alerts, optionally filtered by agent_type or since a timestamp."""
        return await _get_json("/api/alerts", {"agent_type": agent_type, "since": since, "limit": limit})

    @mcp.tool()
    async def get_dgi_top() -> str:
        """Get the Dividend Growth Investing (DGI) screener top-ranked list."""
        return await _get_json("/api/dgi/top")

    return mcp


__all__ = ["create_mcp_server"]
