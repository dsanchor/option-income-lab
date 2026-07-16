from .agent_runner import AgentRunner
from .cosmos_db import CosmosDBService, is_watchlist_paused
from .context import ContextProvider
from .cash_secured_put_instructions import TV_CASH_SECURED_PUT_INSTRUCTIONS
import random


async def run_cash_secured_put_analysis(config, runner: AgentRunner,
                                         cosmos: CosmosDBService,
                                         context_provider: ContextProvider,
                                         symbol: str = None):
    """Run cash secured put analysis for all enabled symbols from CosmosDB.

    Args:
        config: Configuration object
        runner: Initialized AgentRunner instance
        cosmos: CosmosDBService instance
        context_provider: ContextProvider for activity history
        symbol: Optional symbol to filter analysis (e.g., 'AAPL')
    """
    print(f"\n{'='*60}")
    print(f"Starting CashSecuredPutAgent analysis" + (f" for {symbol}" if symbol else ""))
    print(f"{'='*60}")

    if symbol:
        sym_doc = cosmos.get_symbol(symbol)
        if not sym_doc:
            print(f"Symbol {symbol} not found — skipping")
            return
        if not sym_doc.get("watchlist", {}).get("cash_secured_put", False):
            print(f"Symbol {symbol} not enabled for cash-secured put — skipping")
            return
        if is_watchlist_paused(sym_doc):
            print(f"Symbol {symbol} paused until earnings — skipping")
            return
        csp_symbols = [sym_doc]
    else:
        csp_symbols = cosmos.get_cash_secured_put_symbols()
        if not csp_symbols:
            print("No symbols enabled for cash-secured put — skipping")
            return
        if getattr(config, 'yfinance_randomize_symbols', True):
            random.shuffle(csp_symbols)

    symbol_names = [s["symbol"] for s in csp_symbols]
    print(f"Analyzing {len(csp_symbols)} symbols: {', '.join(symbol_names)}")

    from .yfinance_data_provider import get_shared_provider

    provider = get_shared_provider(getattr(config, 'yfinance_config', None))
    for sym_doc in csp_symbols:
        category = (sym_doc.get("enrichment") or {}).get("category", "Balanced")
        await runner.run_symbol_agent(
            name="CashSecuredPutAgent",
            instructions=TV_CASH_SECURED_PUT_INSTRUCTIONS,
            symbol=sym_doc["symbol"],
            exchange=sym_doc["exchange"],
            agent_type="cash_secured_put",
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
    print(f"Completed CashSecuredPutAgent analysis")
    print(f"{'='*60}\n")
