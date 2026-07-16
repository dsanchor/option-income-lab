from .agent_runner import AgentRunner
from .cosmos_db import CosmosDBService, is_watchlist_paused
from .context import ContextProvider
from .buy_tracker_instructions import BUY_TRACKER_INSTRUCTIONS
import random


async def run_buy_tracker_analysis(config, runner: AgentRunner,
                                   cosmos: CosmosDBService,
                                   context_provider: ContextProvider,
                                   symbol: str = None):
    """Run buy tracker analysis for all enabled symbols from CosmosDB.

    Args:
        config: Configuration object
        runner: Initialized AgentRunner instance
        cosmos: CosmosDBService instance
        context_provider: ContextProvider for activity history
        symbol: Optional symbol to filter analysis (e.g., 'AAPL')
    """
    print(f"\n{'='*60}")
    print(f"Starting BuyTrackerAgent analysis" + (f" for {symbol}" if symbol else ""))
    print(f"{'='*60}")

    if symbol:
        sym_doc = cosmos.get_symbol(symbol)
        if not sym_doc:
            print(f"Symbol {symbol} not found — skipping")
            return
        if not sym_doc.get("watchlist", {}).get("buy_tracker", False):
            print(f"Symbol {symbol} not enabled for buy tracker — skipping")
            return
        if is_watchlist_paused(sym_doc):
            print(f"Symbol {symbol} paused until earnings — skipping")
            return
        buy_tracker_symbols = [sym_doc]
    else:
        buy_tracker_symbols = cosmos.get_buy_tracker_symbols()
        if not buy_tracker_symbols:
            print("No symbols enabled for buy tracker — skipping")
            return
        if getattr(config, 'yfinance_randomize_symbols', True):
            random.shuffle(buy_tracker_symbols)

    symbol_names = [s["symbol"] for s in buy_tracker_symbols]
    print(f"Analyzing {len(buy_tracker_symbols)} symbols: {', '.join(symbol_names)}")

    from .yfinance_data_provider import get_shared_provider

    provider = get_shared_provider(getattr(config, 'yfinance_config', None))
    for sym_doc in buy_tracker_symbols:
        await runner.run_symbol_agent(
            name="BuyTrackerAgent",
            instructions=BUY_TRACKER_INSTRUCTIONS,
            symbol=sym_doc["symbol"],
            exchange=sym_doc["exchange"],
            agent_type="buy_tracker",
            cosmos=cosmos,
            context_provider=context_provider,
            max_activity_entries=config.max_activity_entries,
            fetcher=provider,
            model=config.model_for('buy_tracker'),
        )

    print(f"\n{'='*60}")
    print("Completed BuyTrackerAgent analysis")
    print(f"{'='*60}\n")
