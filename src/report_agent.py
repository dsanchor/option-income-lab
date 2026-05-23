import json
import logging
from typing import Dict, List

from .agent_runner import AgentRunner
from .cosmos_db import CosmosDBService

logger = logging.getLogger(__name__)


async def run_report_analysis(
    config,
    runner: AgentRunner,
    cosmos: CosmosDBService,
    symbol: str,
) -> Dict:
    """Generate a comprehensive report for a single symbol.

    Gathers all context (market data + CosmosDB positions/activities)
    and delegates to ``runner.run_report_agent()`` for LLM generation.

    Args:
        config: Configuration object.
        runner: Initialized AgentRunner instance.
        cosmos: CosmosDBService instance.
        symbol: Ticker symbol (e.g. "AAPL").

    Returns:
        Dict with ``report`` (markdown text), ``cached_resources``, and ``symbol``.
    """
    symbol = symbol.upper()

    symbol_doc = cosmos.get_symbol(symbol)
    if not symbol_doc:
        return {"error": f"Symbol {symbol} not found"}

    exchange = symbol_doc.get("exchange", "NYSE")
    context_parts: List[str] = []
    cached_resources: list = []

    # 1. Symbol config (positions, watchlist, etc.) — active positions only
    filtered_doc = {k: v for k, v in symbol_doc.items()
                    if k in ("symbol", "display_name", "exchange",
                             "watchlist", "positions")}
    if "positions" in filtered_doc:
        filtered_doc["positions"] = [
            p for p in filtered_doc["positions"]
            if p.get("status") == "active"
        ]
    context_parts.append("--- Symbol Config ---")
    context_parts.append(json.dumps(filtered_doc, indent=2, default=str))

    # Build closed position IDs for filtering activities/alerts
    closed_position_ids = {
        p["position_id"] for p in symbol_doc.get("positions", [])
        if p.get("status") != "active" and "position_id" in p
    }

    # 2. Recent activities AND alerts per agent type (last 3 each)
    for agent_type in ("covered_call", "cash_secured_put", "buy_tracker",
                       "open_call_monitor", "open_put_monitor"):
        try:
            activities = cosmos.get_recent_activities(
                symbol, agent_type, max_entries=3)
            alerts = cosmos.get_recent_alerts(
                symbol, agent_type, max_entries=3)

            # Merge and deduplicate by id, newest first
            seen_ids = set()
            merged: list = []
            for item in (alerts + activities):
                item_id = item.get("id")
                if item_id and item_id not in seen_ids:
                    seen_ids.add(item_id)
                    merged.append(item)
            merged.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
            merged = merged[:3]

            # Filter out items linked to closed positions
            if closed_position_ids:
                merged = [m for m in merged
                          if m.get("position_id") not in closed_position_ids]
        except Exception as exc:
            logger.warning("report: failed to load %s activities: %s",
                           agent_type, exc)
            merged = []

        label = agent_type.replace("_", " ").title()
        context_parts.append(
            f"\n--- Recent Activities: {label} (last 3) ---")
        if merged:
            for a in merged:
                clean = {k: v for k, v in a.items()
                         if not k.startswith("_")}
                context_parts.append(json.dumps(clean, indent=2, default=str))
        else:
            context_parts.append("(No recent activities)")

    # 3. Market data via yfinance provider
    try:
        from .yfinance_data_provider import get_shared_provider, OPTIONS_CHAIN_SCHEMA_DESCRIPTION

        provider = get_shared_provider(getattr(config, 'yfinance_config', None))
        yf_data = await provider.fetch_all(symbol)
        cached_resources = yf_data.get("cached_resources", [])

        for section_key, section_label in [
            ("overview", "Overview"),
            ("technicals", "Technicals"),
            ("forecast", "Forecast"),
            ("dividends", "Dividends"),
            ("options_chain", "Options Chain"),
        ]:
            content = yf_data.get(section_key, "")
            if content and not content.startswith("[ERROR"):
                if section_key == "options_chain":
                    context_parts.append(
                        f"\n--- {section_label} ---\n"
                        + OPTIONS_CHAIN_SCHEMA_DESCRIPTION + "\n" + content)
                else:
                    context_parts.append(
                        f"\n--- {section_label} ---\n{content}")
    except Exception as exc:
        logger.warning("report: yfinance fetch failed: %s", exc)
        context_parts.append("(Live market data unavailable)")

    context_text = "\n".join(context_parts)

    # 4. Run the report agent
    report_text = await runner.run_report_agent(
        symbol=symbol,
        exchange=exchange,
        context_text=context_text,
        cosmos=cosmos,
        cached_resources=cached_resources,
        model=config.model_for('report'),
    )

    return {
        "report": report_text,
        "cached_resources": cached_resources,
        "symbol": symbol,
    }
