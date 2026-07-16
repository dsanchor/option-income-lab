from .agent_runner import AgentRunner
from .cosmos_db import CosmosDBService, is_watchlist_paused
from .context import ContextProvider
from .covered_call_instructions import TV_COVERED_CALL_INSTRUCTIONS
import random


async def run_covered_call_analysis(config, runner: AgentRunner,
                                     cosmos: CosmosDBService,
                                     context_provider: ContextProvider,
                                     symbol: str = None):
    """Run covered call analysis for all enabled symbols from CosmosDB.

    Args:
        config: Configuration object
        runner: Initialized AgentRunner instance
        cosmos: CosmosDBService instance
        context_provider: ContextProvider for activity history
        symbol: Optional symbol to filter analysis (e.g., 'AAPL')
    """
    print(f"\n{'='*60}")
    print(f"Starting CoveredCallAgent analysis" + (f" for {symbol}" if symbol else ""))
    print(f"{'='*60}")

    if symbol:
        sym_doc = cosmos.get_symbol(symbol)
        if not sym_doc:
            print(f"Symbol {symbol} not found — skipping")
            return
        if not sym_doc.get("watchlist", {}).get("covered_call", False):
            print(f"Symbol {symbol} not enabled for covered call — skipping")
            return
        if is_watchlist_paused(sym_doc):
            print(f"Symbol {symbol} paused until earnings — skipping")
            return
        cc_symbols = [sym_doc]
    else:
        cc_symbols = cosmos.get_covered_call_symbols()
        if not cc_symbols:
            print("No symbols enabled for covered call — skipping")
            return
        if getattr(config, 'yfinance_randomize_symbols', True):
            random.shuffle(cc_symbols)

    symbol_names = [s["symbol"] for s in cc_symbols]
    print(f"Analyzing {len(cc_symbols)} symbols: {', '.join(symbol_names)}")

    from .yfinance_data_provider import get_shared_provider

    provider = get_shared_provider(getattr(config, 'yfinance_config', None))
    for sym_doc in cc_symbols:
        category = (sym_doc.get("enrichment") or {}).get("category", "Balanced")
        await runner.run_symbol_agent(
            name="CoveredCallAgent",
            instructions=TV_COVERED_CALL_INSTRUCTIONS,
            symbol=sym_doc["symbol"],
            exchange=sym_doc["exchange"],
            agent_type="covered_call",
            cosmos=cosmos,
            context_provider=context_provider,
            max_activity_entries=config.max_activity_entries,
            fetcher=provider,
            model=config.model_for('analysis'),
            supervisor_model=config.model_for('supervisor'),
            alpha_model=config.model_for('alpha'),
            symbol_category=category,
        )

    print(f"\n{'='*60}")
    print(f"Completed CoveredCallAgent analysis")
    print(f"{'='*60}\n")
