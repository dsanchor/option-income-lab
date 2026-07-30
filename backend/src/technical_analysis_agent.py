import json
import logging
from typing import Dict, List

from .agent_runner import AgentRunner
from .cosmos_db import CosmosDBService

logger = logging.getLogger(__name__)


async def run_technical_analysis(
    config,
    runner: AgentRunner,
    cosmos: CosmosDBService,
    symbol: str,
) -> Dict:
    """Generate a detailed technical analysis for a single symbol.

    Gathers market data (overview, technicals, forecast, dividends)
    and delegates to ``runner.run_technical_analysis_agent()`` for LLM generation.

    Args:
        config: Configuration object.
        runner: Initialized AgentRunner instance.
        cosmos: CosmosDBService instance.
        symbol: Ticker symbol (e.g. "AAPL").

    Returns:
        Dict with ``analysis`` (markdown text), ``cached_resources``, and ``symbol``.
    """
    symbol = symbol.upper()

    symbol_doc = cosmos.get_symbol(symbol)
    if not symbol_doc:
        return {"error": f"Symbol {symbol} not found"}

    exchange = symbol_doc.get("exchange", "NYSE")
    context_parts: List[str] = []
    cached_resources: list = []

    # 1. Basic symbol info
    context_parts.append("--- Symbol Info ---")
    basic_info = {
        "symbol": symbol_doc.get("symbol"),
        "display_name": symbol_doc.get("display_name"),
        "exchange": symbol_doc.get("exchange"),
    }
    context_parts.append(json.dumps(basic_info, indent=2, default=str))

    # 2. Market data via yfinance provider (all sections except options chain)
    try:
        from .yfinance_data_provider import get_shared_provider

        provider = get_shared_provider(getattr(config, 'yfinance_config', None))
        yf_data = await provider.fetch_all(symbol)
        cached_resources = yf_data.get("cached_resources", [])

        for section_key, section_label in [
            ("overview", "Overview"),
            ("technicals", "Technicals"),
            ("forecast", "Forecast"),
            ("dividends", "Dividends"),
        ]:
            content = yf_data.get(section_key, "")
            if content and not content.startswith("[ERROR"):
                context_parts.append(f"\n--- {section_label} ---\n{content}")
    except Exception as exc:
        logger.warning("technical_analysis: yfinance fetch failed: %s", exc)
        context_parts.append("(Live market data unavailable)")

    context_text = "\n".join(context_parts)

    # 3. Run the technical analysis agent
    analysis_text = await runner.run_technical_analysis_agent(
        symbol=symbol,
        exchange=exchange,
        context_text=context_text,
        cosmos=cosmos,
        cached_resources=cached_resources,
        model=config.model_for('technical_analysis'),
    )

    return {
        "analysis": analysis_text,
        "cached_resources": cached_resources,
        "symbol": symbol,
    }
