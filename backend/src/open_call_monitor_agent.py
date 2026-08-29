from .agent_runner import AgentRunner
from .cosmos_db import CosmosDBService
from .context import ContextProvider
import random


async def run_open_call_monitor(config, runner: AgentRunner,
                                 cosmos: CosmosDBService,
                                 context_provider: ContextProvider,
                                 symbol: str = None,
                                 run_trigger: str = "scheduled",
                                 force_alpha: bool = False):
    """Run open covered call position monitoring from CosmosDB.

    Args:
        config: Configuration object
        runner: Initialized AgentRunner instance
        cosmos: CosmosDBService instance
        context_provider: ContextProvider for activity history
        symbol: Optional symbol to filter positions (e.g., 'AAPL')
        run_trigger: Execution provenance — "scheduled" (cron) or
            "manual" (dashboard/API triggered). Pass-through only.
        force_alpha: When True, requests an Alpha Advisor review even for
            positions that would otherwise stay Alpha-silent this cycle.
            Pass-through only; see AgentRunner.run_position_monitor.
    """
    from .open_call_assessment_instructions import get_open_call_assessment_instructions
    from .open_call_roll_instructions import get_open_call_roll_instructions
    assessment_instructions = get_open_call_assessment_instructions()
    roll_instructions = get_open_call_roll_instructions()

    print(f"\n{'='*60}")
    print(f"Starting OpenCallMonitor monitoring" + (f" for {symbol}" if symbol else ""))
    print(f"{'='*60}")

    if symbol:
        sym_doc = cosmos.get_symbol(symbol)
        if not sym_doc:
            print(f"Symbol {symbol} not found — skipping OpenCallMonitor")
            return
        active_positions = [
            p for p in sym_doc.get("positions", [])
            if p["type"] == "call" and p["status"] == "active"
        ]
        if not active_positions:
            print(f"No active call positions for {symbol} — skipping OpenCallMonitor")
            return
        sym_doc["_active_positions"] = active_positions
        call_symbols = [sym_doc]
    else:
        call_symbols = cosmos.get_symbols_with_active_positions("call")
        if not call_symbols:
            print("No active call positions — skipping OpenCallMonitor")
            return
        if getattr(config, 'yfinance_randomize_symbols', True):
            random.shuffle(call_symbols)

    total = sum(len(s["_active_positions"]) for s in call_symbols)
    print(f"Monitoring {total} open call position(s)")

    from .yfinance_data_provider import get_shared_provider

    provider = get_shared_provider(getattr(config, 'yfinance_config', None))
    for sym_doc in call_symbols:
        _category = sym_doc.get("enrichment", {}).get("category")
        for pos in sym_doc["_active_positions"]:
            await runner.run_position_monitor(
                name="OpenCallMonitor",
                symbol=sym_doc["symbol"],
                exchange=sym_doc["exchange"],
                position=pos,
                agent_type="open_call_monitor",
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
                run_trigger=run_trigger,
                force_alpha=force_alpha,
            )

    print(f"\n{'='*60}")
    print(f"Completed OpenCallMonitor monitoring")
    print(f"{'='*60}\n")
