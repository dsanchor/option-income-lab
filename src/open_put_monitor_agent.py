from .agent_runner import AgentRunner
from .cosmos_db import CosmosDBService
from .context import ContextProvider
import random


async def run_open_put_monitor(config, runner: AgentRunner,
                                cosmos: CosmosDBService,
                                context_provider: ContextProvider,
                                symbol: str = None):
    """Run open cash-secured put position monitoring from CosmosDB.

    Args:
        config: Configuration object
        runner: Initialized AgentRunner instance
        cosmos: CosmosDBService instance
        context_provider: ContextProvider for activity history
        symbol: Optional symbol to filter positions (e.g., 'AAPL')
    """
    from .open_put_assessment_instructions import get_open_put_assessment_instructions
    from .open_put_roll_instructions import get_open_put_roll_instructions
    assessment_instructions = get_open_put_assessment_instructions()
    roll_instructions = get_open_put_roll_instructions()

    print(f"\n{'='*60}")
    print(f"Starting OpenPutMonitor monitoring" + (f" for {symbol}" if symbol else ""))
    print(f"{'='*60}")

    if symbol:
        sym_doc = cosmos.get_symbol(symbol)
        if not sym_doc:
            print(f"Symbol {symbol} not found — skipping OpenPutMonitor")
            return
        active_positions = [
            p for p in sym_doc.get("positions", [])
            if p["type"] == "put" and p["status"] == "active"
        ]
        if not active_positions:
            print(f"No active put positions for {symbol} — skipping OpenPutMonitor")
            return
        sym_doc["_active_positions"] = active_positions
        put_symbols = [sym_doc]
    else:
        put_symbols = cosmos.get_symbols_with_active_positions("put")
        if not put_symbols:
            print("No active put positions — skipping OpenPutMonitor")
            return
        if getattr(config, 'yfinance_randomize_symbols', True):
            random.shuffle(put_symbols)

    total = sum(len(s["_active_positions"]) for s in put_symbols)
    print(f"Monitoring {total} open put position(s)")

    from .yfinance_data_provider import get_shared_provider

    provider = get_shared_provider(getattr(config, 'yfinance_config', None))
    for sym_doc in put_symbols:
        _category = sym_doc.get("enrichment", {}).get("category")
        for pos in sym_doc["_active_positions"]:
            await runner.run_position_monitor(
                name="OpenPutMonitor",
                symbol=sym_doc["symbol"],
                exchange=sym_doc["exchange"],
                position=pos,
                agent_type="open_put_monitor",
                cosmos=cosmos,
                context_provider=context_provider,
                max_activity_entries=config.max_activity_entries,
                fetcher=provider,
                assessment_instructions=assessment_instructions,
                roll_instructions=roll_instructions,
                assessment_model=config.model_for('monitor_assessment'),
                roll_model=config.model_for('monitor_roll'),
                supervisor_model=config.model_for('supervisor'),
                alpha_model=config.model_for('alpha'),
                symbol_category=_category,
            )

    print(f"\n{'='*60}")
    print(f"Completed OpenPutMonitor monitoring")
    print(f"{'='*60}\n")
