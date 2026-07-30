import sys
import time
import signal
import asyncio
import asyncio.base_subprocess
import gc
from datetime import datetime, timezone

from croniter import croniter

# ── Python 3.12 workaround ───────────────────────────────────────────────────
# httpx/aiohttp spawn subprocess transports for DNS resolution. When the event
# loop closes, GC finalizes them triggering "RuntimeError: Event loop is closed"
# in BaseSubprocessTransport.__del__. This is harmless but noisy — suppress it.
_original_subprocess_del = asyncio.base_subprocess.BaseSubprocessTransport.__del__


def _patched_subprocess_del(self):
    try:
        _original_subprocess_del(self)
    except RuntimeError:
        pass


asyncio.base_subprocess.BaseSubprocessTransport.__del__ = _patched_subprocess_del
# ─────────────────────────────────────────────────────────────────────────────

from .config import Config
from .agent_runner import AgentRunner
from .cosmos_db import CosmosDBService
from .context import ContextProvider
from .covered_call_agent import run_covered_call_analysis
from .cash_secured_put_agent import run_cash_secured_put_analysis
from .buy_tracker_agent import run_buy_tracker_analysis
from .open_call_monitor_agent import run_open_call_monitor
from .open_put_monitor_agent import run_open_put_monitor
from .dgi_screener import run_dgi_screener
from .banner_agent import run_banner_agent
from .scheduler_registry import TaskRegistry


def _run_async(coro):
    """Run a coroutine with proper cleanup to avoid 'Event loop is closed' errors.

    Python 3.12 triggers RuntimeError in BaseSubprocessTransport finalizers when
    the GC collects HTTP-client transports after asyncio.run() closes the loop.
    This helper forces GC while the loop is still open.
    """
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        try:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
        finally:
            gc.collect()
            asyncio.set_event_loop(None)
            loop.close()


def _now_local():
    """Return the current local timezone-aware datetime."""
    return datetime.now().astimezone()


class OptionsAgentScheduler:
    """Main scheduler for cron-based options agent execution."""
    
    def __init__(self):
        self.running = True
        self.alive = False  # Health flag — True while scheduler loop is active
        self.config = None
        self.runner = None
        self.cosmos = None
        self.context_provider = None
        self._last_config_reload = None
        self._config_reload_interval = 60  # seconds
        self._last_heartbeat = 0  # epoch for periodic heartbeat log
        self._heartbeat_interval = 600  # heartbeat every 10 minutes
        self.registry = TaskRegistry()  # Centralized task registry
    
    def reschedule(self, new_cron: str):
        """Update cron expression. The run loop will pick it up on next iteration."""
        self.config.cron_expression = new_cron
        task = self.registry.get_task("monitor_agents")
        if task:
            task._cron_changed = True
    
    def reschedule_summary(self, new_cron: str):
        """Update summary agent cron expression. The run loop will pick it up on next iteration."""
        self.registry.reschedule("summary_agent", new_cron, self.config)

    def reschedule_plan_monitor(self, new_cron: str):
        """Update plan monitor cron expression. The run loop will pick it up on next iteration."""
        self.registry.reschedule("plan_monitor", new_cron, self.config)
    
    def reschedule_options_chain(self, new_cron: str):
        """Update options chain scheduler cron expression. The run loop will pick it up on next iteration."""
        self.registry.reschedule("options_chain", new_cron, self.config)
    
    def reschedule_dgi_screener(self, new_cron: str):
        """Update DGI screener cron expression. The run loop will pick it up on next iteration."""
        self.registry.reschedule("dgi_screener", new_cron, self.config)
    
    def reschedule_portfolio_enrichment(self, new_cron: str):
        """Update portfolio enrichment cron expression."""
        self.registry.reschedule("portfolio_enrichment", new_cron, self.config)

    def reschedule_banner(self, new_cron: str):
        """Update banner agent cron expression. The run loop will pick it up on next iteration."""
        self.registry.reschedule("banner_agent", new_cron, self.config)

    def reschedule_calendar(self, new_cron: str):
        """Update calendar sync cron expression. The run loop will pick it up on next iteration."""
        self.registry.reschedule("calendar_sync", new_cron, self.config)
    
    def setup(self):
        """Initialize configuration, CosmosDB, and agent runner."""
        print("Loading configuration...")
        self.config = Config()
        
        print("Initializing CosmosDB service...")
        self.cosmos = CosmosDBService(
            endpoint=self.config.cosmosdb_endpoint,
            key=self.config.cosmosdb_key,
            database_name=self.config.cosmosdb_database,
        )
        self.context_provider = ContextProvider(self.cosmos)

        # Merge config.yaml defaults into CosmosDB (first-run seed + new keys)
        settings_defaults = {
            k: v for k, v in self.config.config.items()
            if k not in ('ai', 'azure', 'gemini', 'cosmosdb')
        }
        merged_settings = self.cosmos.merge_defaults(settings_defaults)
        
        # Update Config object with merged settings from CosmosDB (CosmosDB takes precedence)
        if merged_settings:
            for key, value in merged_settings.items():
                if key not in ('ai', 'azure', 'gemini', 'cosmosdb'):
                    self.config.config[key] = value

        from .telegram_notifier import TelegramNotifier
        telegram_notifier = TelegramNotifier(cosmos=self.cosmos)

        print("Initializing Agent Framework Runner...")
        self.runner = AgentRunner(
            llm=self.config.llm_config(),
            model=self.config.model_deployment,
            telegram_notifier=telegram_notifier,
            plan_monitor_model=self.config.plan_monitor_model,
        )
        
        print(f"Scheduler configured with cron: {self.config.cron_expression}")
        print(f"System timezone: {self.config.timezone}")
        
        # Log summary agent configuration
        summary_config = self.config.config.get('summary_agent', {})
        summary_enabled = summary_config.get('enabled', True)
        summary_cron = summary_config.get('cron', '0 8 * * *')
        summary_activity_count = summary_config.get('activity_count', 3)
        
        print(f"\nSummary Agent Configuration:")
        print(f"  Enabled: {summary_enabled}")
        if summary_enabled:
            print(f"  Cron: {summary_cron}")
            print(f"  Activity count: {summary_activity_count}")
        else:
            print(f"  Status: Disabled in config")

        plan_monitor_config = self.config.config.get('plan_monitor', {})
        plan_monitor_enabled = plan_monitor_config.get('enabled', True)
        plan_monitor_cron = plan_monitor_config.get('cron', '0 4,16 * * 1-5')
        plan_monitor_model = plan_monitor_config.get('model', 'gpt-5.4-mini')

        print(f"\nPlan Monitor Configuration:")
        print(f"  Enabled: {plan_monitor_enabled}")
        if plan_monitor_enabled:
            print(f"  Cron: {plan_monitor_cron}")
            print(f"  Model: {plan_monitor_model}")
        else:
            print(f"  Status: Disabled in config")
        
        # Log options chain scheduler configuration
        options_chain_config = self.config.config.get('options_chain_scheduler', {})
        options_chain_enabled = options_chain_config.get('enabled', True)
        options_chain_cron = options_chain_config.get('cron', '0 * * * *')
        
        print(f"\nOptions Chain Scheduler Configuration:")
        print(f"  Enabled: {options_chain_enabled}")
        if options_chain_enabled:
            print(f"  Cron: {options_chain_cron}")
        else:
            print(f"  Status: Disabled in config")
        
        # Log DGI screener configuration
        dgi_config = self.config.config.get('dgi_screener', {})
        dgi_enabled = dgi_config.get('enabled', True)
        dgi_cron = dgi_config.get('cron', '0 6 * * 1-5')
        
        print(f"\nDGI Screener Configuration:")
        print(f"  Enabled: {dgi_enabled}")
        if dgi_enabled:
            print(f"  Cron: {dgi_cron}")
        else:
            print(f"  Status: Disabled in config")

        banner_config = self.config.config.get('banner_agent', {})
        banner_enabled = banner_config.get('enabled', True)
        banner_cron = banner_config.get('cron', '0 5 * * *')
        banner_max_items = banner_config.get('max_items', 10)

        print(f"\nDashboard Banner Configuration:")
        print(f"  Enabled: {banner_enabled}")
        if banner_enabled:
            print(f"  Cron: {banner_cron}")
            print(f"  Model: {self.config.model_for('banner')}")
            print(f"  Max items: {banner_max_items}")
        else:
            print(f"  Status: Disabled in config")

        calendar_config = self.config.config.get('calendar_sync', {})
        calendar_enabled = calendar_config.get('enabled', True)
        calendar_cron = calendar_config.get('cron', '0 5 * * 1-5')

        print(f"\nCalendar Sync Configuration:")
        print(f"  Enabled: {calendar_enabled}")
        if calendar_enabled:
            print(f"  Cron: {calendar_cron}")
        else:
            print(f"  Status: Disabled in config")

        dps_config = self.config.config.get('dps_scorer', {})
        dps_enabled = dps_config.get('enabled', True)
        dps_cron = dps_config.get('cron', '0 22 * * 1-5')

        print(f"\nDPS Scorer Configuration:")
        print(f"  Enabled: {dps_enabled}")
        if dps_enabled:
            print(f"  Cron: {dps_cron}")
        else:
            print(f"  Status: Disabled in config")
    
    def run_all_agents(self):
        """Execute all agents (bridges async to sync for scheduler)."""
        _run_async(self._run_all_agents_async())
    
    async def _run_all_agents_async(self):
        """Execute all agents asynchronously."""
        now_tz = _now_local()
        print(f"\n{'#'*70}")
        print(f"# Starting scheduled agent run at {now_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"{'#'*70}\n")
        
        cosmos = self.cosmos
        ctx = self.context_provider
        runner = self.runner
        config = self.config

        agents = [
            ("covered_call", run_covered_call_analysis),
            ("cash_secured_put", run_cash_secured_put_analysis),
            ("buy_tracker", run_buy_tracker_analysis),
            ("open_call_monitor", run_open_call_monitor),
            ("open_put_monitor", run_open_put_monitor),
        ]

        for agent_name, agent_func in agents:
            try:
                await agent_func(config, runner, cosmos, ctx)
            except Exception as e:
                print(f"ERROR running {agent_name}: {str(e)}")
        
        now_tz = _now_local()
        print(f"\n{'#'*70}")
        print(f"# Completed scheduled agent run at {now_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"{'#'*70}\n")
    
    def run_summary_agent_job(self):
        """Execute summary agent (bridges async to sync for scheduler)."""
        _run_async(self._run_summary_agent_async())
    
    async def _run_summary_agent_async(self):
        """Run summary agent if enabled in config."""
        summary_config = self.config.config.get('summary_agent', {})
        if not summary_config.get('enabled', True):
            print("⏭️  Summary agent disabled in config")
            return
        
        now_tz = _now_local()
        print(f"\n{'='*70}")
        print(f"📊 Summary Agent - Scheduled run at {now_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"{'='*70}\n")
        
        activity_count = summary_config.get('activity_count', 3)
        max_age_hours = summary_config.get('max_activity_age_hours', 24)
        await self.runner.run_summary_agent(
            cosmos=self.cosmos,
            telegram_notifier=self.runner.telegram_notifier,
            activity_count=activity_count,
            model=self.config.model_for('summary'),
            max_age_hours=max_age_hours,
        )

    def run_plan_monitor_job(self):
        """Execute plan monitor (bridges async to sync for scheduler)."""
        _run_async(self._run_plan_monitor_async())

    async def _run_plan_monitor_async(self):
        """Run plan monitor if enabled in config."""
        plan_monitor_config = self.config.config.get('plan_monitor', {})
        if not plan_monitor_config.get('enabled', True):
            print("⏭️  Plan monitor disabled in config")
            return
        self.runner._plan_monitor_model = plan_monitor_config.get('model', 'gpt-5.4-mini')

        now_tz = _now_local()
        print(f"\n{'📝'*35}")
        print(f"📝 Plan Monitor - Scheduled run at {now_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"{'📝'*35}\n")

        plans = [
            plan for plan in self.cosmos.get_plans()
            if str(plan.get('status', '')).lower() == 'planned'
        ]
        if not plans:
            print("ℹ️  Plan monitor: no planned plans found")
            return

        success = 0
        errors = 0
        for plan in plans:
            symbol = str(plan.get('symbol') or '').upper()
            plan_id = plan.get('id')
            title = plan.get('title', '')
            if not symbol or not plan_id:
                errors += 1
                print(f"  ✗ Skipping invalid plan record: {title or '(untitled)'}")
                continue

            symbol_doc = self.cosmos.get_symbol(symbol)
            if symbol_doc is None:
                errors += 1
                print(f"  ✗ {symbol}: symbol config not found for plan {title or plan_id}")
                continue

            try:
                result = await self.runner.run_plan_monitor(plan, symbol_doc, self.cosmos)
                success += 1
                print(
                    f"  ✓ {symbol} | {title or plan_id} | "
                    f"alert={result.get('alert_level', 'none')} | "
                    f"conditions_met={result.get('conditions_met', False)}"
                )
            except Exception as e:
                errors += 1
                print(f"  ✗ {symbol} | {title or plan_id}: {e}")

        print(f"\nPlan Monitor Complete: {success} success, {errors} errors, {len(plans)} plans processed")
    
    def run_options_chain_fetch_job(self):
        """Execute options chain fetch job (bridges async to sync for scheduler)."""
        _run_async(self._run_options_chain_fetch_async())
    
    async def _run_options_chain_fetch_async(self):
        """Refresh options chain cache for all symbols (yfinance + TradingView merge)."""
        options_chain_config = self.config.config.get('options_chain_scheduler', {})
        if not options_chain_config.get('enabled', True):
            print("⏭️  Options chain scheduler disabled in config")
            return
        
        from .options_chain_cache import get_options_chain_cache
        
        now_tz = _now_local()
        print(f"\n{'~'*70}")
        print(f"📈 Options Chain Cache Refresh - Scheduled run at {now_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"{'~'*70}\n")
        
        symbols = self.cosmos.list_symbols()
        symbol_names = [s["symbol"] for s in symbols]
        
        print(f"Refreshing options chain cache for {len(symbol_names)} symbols...")
        
        cache = get_options_chain_cache()
        stats = await cache.refresh_all(symbol_names)
        
        print(f"\n{'~'*70}")
        print(f"Options Chain Cache Refresh Complete: {stats['success']} success, {stats['errors']} errors")
        print(f"{'~'*70}\n")
    
    def run_dgi_screener_job(self):
        """Execute DGI screener (bridges async to sync for scheduler)."""
        _run_async(self._run_dgi_screener_async())
    
    async def _run_dgi_screener_async(self):
        """Run DGI screener if enabled in config."""
        dgi_config = self.config.config.get('dgi_screener', {})
        if not dgi_config.get('enabled', True):
            print("⏭️  DGI Screener disabled in config")
            return
        
        now_tz = _now_local()
        print(f"\n{'+'*70}")
        print(f"🔍 DGI Screener - Scheduled run at {now_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"{'+'*70}\n")
        
        try:
            result = await run_dgi_screener(self.config, self.cosmos)
            print(f"DGI Screener complete: {result.get('total_screened', 0)} screened, "
                  f"{result.get('passed_filters', 0)} passed, "
                  f"{result.get('top_n', 0)} in top list")
        except Exception as e:
            print(f"ERROR during DGI screener: {e}")

    def run_portfolio_enrichment_job(self):
        """Execute portfolio enrichment (bridges async to sync for scheduler)."""
        _run_async(self._run_portfolio_enrichment_async())

    async def _run_portfolio_enrichment_async(self):
        """Run portfolio enrichment if enabled in config."""
        pe_config = self.config.config.get('portfolio_enrichment', {})
        if not pe_config.get('enabled', True):
            print("⏭️  Portfolio Enrichment disabled in config")
            return

        from .portfolio_enrichment import run_portfolio_enrichment

        now_tz = _now_local()
        print(f"\n{'📊'*1}{'='*68}")
        print(f"📊 Portfolio Enrichment - Scheduled run at {now_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"{'='*70}\n")

        try:
            result = await run_portfolio_enrichment(self.cosmos)
            print(f"Portfolio Enrichment complete: {result.get('success', 0)}/{result.get('total', 0)} success, "
                  f"{result.get('errors', 0)} errors")
        except Exception as e:
            print(f"ERROR during Portfolio Enrichment: {e}")

    def run_price_forecast_job(self):
        """Execute the deterministic price-forecast job (async→sync bridge)."""
        _run_async(self._run_price_forecast_async())

    async def _run_price_forecast_async(self):
        """Run the price-forecast cron if enabled in config."""
        pf_config = self.config.config.get('price_forecast', {})
        if not pf_config.get('enabled', True):
            print("⏭️  Price Forecast disabled in config")
            return

        from .forecast_cron import run_forecast_cron
        from .yfinance_data_provider import get_shared_provider

        now_tz = _now_local()
        print(f"\n{'='*70}")
        print(f"📈 Price Forecast - Scheduled run at {now_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"{'='*70}\n")

        try:
            yf_provider = get_shared_provider(self.config.config.get('yfinance'))
            result = await run_forecast_cron(self.cosmos, yf_provider)
            print(
                f"Price Forecast complete: {result.get('created', 0)} created, "
                f"{result.get('validated', 0)} validated, "
                f"{result.get('resolved', 0)} resolved, "
                f"{result.get('pruned', 0)} pruned"
            )
        except Exception as e:
            print(f"ERROR during Price Forecast: {e}")

    def run_banner_agent_job(self):
        """Execute banner agent (bridges async to sync for scheduler)."""
        _run_async(self._run_banner_agent_async())
    async def _run_banner_agent_async(self):
        """Run dashboard banner agent if enabled in config."""
        banner_config = self.config.config.get('banner_agent', {})
        if not banner_config.get('enabled', True):
            print("⏭️  Dashboard banner agent disabled in config")
            return

        now_tz = _now_local()
        print(f"\n{'*'*70}")
        print(f"📰 Dashboard Banner Agent - Scheduled run at {now_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"{'*'*70}\n")

        try:
            result = await run_banner_agent(self.config, self.cosmos)
            print(f"Banner agent complete: {len(result.get('items', []))} items from "
                  f"{result.get('symbols_analyzed', 0)} symbols")
        except Exception as e:
            print(f"ERROR during dashboard banner generation: {e}")

    def run_calendar_sync_job(self):
        """Execute calendar sync (bridges async to sync for scheduler)."""
        _run_async(self._run_calendar_sync_async())

    def run_watchlist_reactivation_job(self):
        """Execute watchlist pause reactivation (bridges async to sync for scheduler)."""
        _run_async(self._run_watchlist_reactivation_async())

    async def _run_watchlist_reactivation_async(self):
        """Clear expired watchlist pauses after their earnings date has passed."""
        reactivation_config = self.config.config.get('watchlist_reactivation', {})
        if not reactivation_config.get('enabled', True):
            print("⏭️  Watchlist reactivation disabled in config")
            return

        now_tz = _now_local()
        today = now_tz.strftime("%Y-%m-%d")
        print(f"\n{'⏯️'*35}")
        print(f"⏯️ Watchlist Reactivation - Scheduled run at {now_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"{'⏯️'*35}\n")

        cleared = 0
        errors = 0
        for sym_doc in self.cosmos.get_paused_symbols():
            symbol = sym_doc.get("symbol", "")
            until = (sym_doc.get("watchlist_pause") or {}).get("until")
            if not symbol or not until or until >= today:
                continue
            try:
                self.cosmos.clear_watchlist_pause(symbol)
                cleared += 1
                print(f"  ✓ {symbol}: watchlist pause expired after {until}; resumed")
            except Exception as e:
                errors += 1
                print(f"  ✗ {symbol}: failed to clear watchlist pause: {e}")

        print(f"\nWatchlist Reactivation Complete: {cleared} pauses cleared, {errors} errors")

    async def _run_calendar_sync_async(self):
        """Fetch earnings and ex-dividend dates from yfinance and store in CosmosDB."""
        calendar_config = self.config.config.get('calendar_sync', {})
        if not calendar_config.get('enabled', True):
            print("⏭️  Calendar sync disabled in config")
            return

        now_tz = _now_local()
        print(f"\n{'📅'*35}")
        print(f"📅 Calendar Sync - Scheduled run at {now_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"{'📅'*35}\n")

        try:
            import yfinance as yf_lib
        except ImportError:
            print("ERROR: yfinance not installed — calendar sync skipped")
            return

        symbols = self.cosmos.list_symbols()
        updated = 0
        errors = 0

        for sym_doc in symbols:
            symbol = sym_doc.get("symbol", "")
            if not symbol:
                continue

            active_positions = [
                p for p in sym_doc.get("positions", [])
                if p.get("status") == "active" and p.get("expiration")
            ]

            def _has_position_active_on(event_date_str: str) -> bool:
                """True if any active position covers the event date (expiration >= event date)."""
                for p in active_positions:
                    try:
                        exp_str = p["expiration"][:10]  # handle ISO datetime strings
                        if exp_str >= event_date_str:
                            return True
                    except (TypeError, IndexError):
                        continue
                return False

            try:
                ticker = yf_lib.Ticker(symbol)
                info = ticker.info or {}
            except Exception as e:
                errors += 1
                print(f"  ✗ {symbol}: {e}")
                continue

            # Earnings date
            earnings_ts = info.get("earningsTimestampStart")
            if earnings_ts:
                try:
                    earnings_date = datetime.fromtimestamp(earnings_ts, tz=timezone.utc).strftime("%Y-%m-%d")
                    self.cosmos.upsert_calendar_event(symbol, "earnings", earnings_date, _has_position_active_on(earnings_date))
                    updated += 1
                except (OSError, ValueError):
                    pass

            # Ex-dividend date
            ex_div_ts = info.get("exDividendDate")
            if ex_div_ts:
                try:
                    ex_div_date = datetime.fromtimestamp(ex_div_ts, tz=timezone.utc).strftime("%Y-%m-%d")
                    self.cosmos.upsert_calendar_event(symbol, "ex_dividend", ex_div_date, _has_position_active_on(ex_div_date))
                    updated += 1
                except (OSError, ValueError):
                    pass

        print(f"\nCalendar Sync Complete: {updated} events updated, {errors} errors, {len(symbols)} symbols processed")
    
    def signal_handler(self, sig, frame):
        """Handle graceful shutdown on Ctrl+C."""
        print("\n\nShutdown signal received. Stopping scheduler...")
        self.running = False
    
    def _reload_config_from_cosmos(self):
        """Reload settings from CosmosDB and detect cron changes.
        
        This method is called periodically to pick up configuration changes
        made through the web UI without requiring a scheduler restart.
        """
        try:
            cosmos_settings = self.cosmos.get_settings()
            if not cosmos_settings:
                return
            
            # Handle main monitor agents cron (special case, not in registry)
            scheduler_settings = cosmos_settings.get('scheduler', {})
            new_cron = scheduler_settings.get('cron')
            
            if new_cron and new_cron != self.config.cron_expression:
                self.config.cron_expression = new_cron
                task = self.registry.get_task("monitor_agents")
                if task:
                    task._cron_changed = True
                    print(f"✓ Config reloaded from CosmosDB: monitor cron changed to {new_cron}")
            
            # Reload all registered tasks via the registry
            self.registry.reload_from_cosmos(self.config, cosmos_settings)
                
        except Exception as e:
            # Don't crash the scheduler on config reload errors
            print(f"⚠️  Error reloading config from CosmosDB: {e}")

    
    def run(self, install_signals=True):
        """Main execution loop using cron expression.
        
        Args:
            install_signals: Install SIGINT/SIGTERM handlers. Set to False when
                 running inside a thread (signals can only be set in the main thread).
        """
        if install_signals:
            signal.signal(signal.SIGINT, self.signal_handler)
            signal.signal(signal.SIGTERM, self.signal_handler)
        
        self.setup()
        
        now_tz = _now_local()
        
        # Register all tasks in the registry
        self.registry.register(
            "monitor_agents",
            "Monitor Agents",
            "scheduler",  # Uses scheduler.cron from config (via self.config.cron_expression)
            "30 9-16/4 * * 1-5",
            self.run_all_agents,
            has_extra_config=False,  # No extra per-task config beyond the 5 standard fields
        )
        self.registry.register(
            "summary_agent",
            "Summary Agent",
            "summary_agent",
            "0 8 * * *",
            self.run_summary_agent_job,
            has_extra_config=True,  # Has activity_count extra config
        )
        self.registry.register(
            "plan_monitor",
            "Plan Monitor",
            "plan_monitor",
            "0 4,16 * * 1-5",
            self.run_plan_monitor_job,
            has_extra_config=False,
        )
        self.registry.register(
            "options_chain",
            "Options Chain Fetcher",
            "options_chain_scheduler",
            "0 * * * *",
            self.run_options_chain_fetch_job,
            has_extra_config=False,
        )
        self.registry.register(
            "dgi_screener",
            "DGI Screener",
            "dgi_screener",
            "0 6 * * 1-5",
            self.run_dgi_screener_job,
            has_extra_config=True,  # Has symbols + top_n extra config
        )
        self.registry.register(
            "banner_agent",
            "Dashboard Banner",
            "banner_agent",
            "0 5 * * *",
            self.run_banner_agent_job,
            has_extra_config=True,  # Has max_items extra config
        )
        self.registry.register(
            "calendar_sync",
            "Calendar Sync",
            "calendar_sync",
            "0 5 * * 1-5",
            self.run_calendar_sync_job,
            has_extra_config=False,
        )
        self.registry.register(
            "watchlist_reactivation",
            "Watchlist Reactivation",
            "watchlist_reactivation",
            "0 6 * * 1-5",
            self.run_watchlist_reactivation_job,
            has_extra_config=False,
        )
        self.registry.register(
            "portfolio_enrichment",
            "Portfolio Enrichment",
            "portfolio_enrichment",
            "0 9-17 * * 1-5",
            self.run_portfolio_enrichment_job,
            has_extra_config=False,
        )
        self.registry.register(
            "price_forecast",
            "Price Forecast",
            "price_forecast",
            "0 21 * * 1-5",
            self.run_price_forecast_job,
            has_extra_config=False,
        )
        
        # Store config reference for registry's handle_cron_changes
        self.registry.set_config(self.config)
        
        # Initialize all registered tasks (including monitor_agents)
        # Note: monitor_agents uses self.config.cron_expression as its cron source
        self.registry.initialize_all(self.config, now_tz)
        
        # Display initial schedule
        self.registry.display_schedule()
        
        # Track when we last reloaded config
        self._last_config_reload = time.time()
        
        print("Press Ctrl+C to stop\n")
        
        self.alive = True
        self._last_heartbeat = time.time()
        
        while self.running:
          try:
            # Heartbeat — periodic log to confirm scheduler is alive
            current_time = time.time()
            if current_time - self._last_heartbeat >= self._heartbeat_interval:
                self._last_heartbeat = current_time
                now_hb = _now_local()
                monitor_task = self.registry.get_task("monitor_agents")
                monitor_next = monitor_task.next_run.strftime('%H:%M:%S') if monitor_task and monitor_task.next_run else "N/A"
                print(f"💓 Scheduler alive at {now_hb.strftime('%Y-%m-%d %H:%M:%S %Z')} | Next monitor run: {monitor_next}")

            # Periodically reload config from CosmosDB to pick up web UI changes
            if current_time - self._last_config_reload >= self._config_reload_interval:
                self._reload_config_from_cosmos()
                self._last_config_reload = current_time
            
            # Handle cron changes for all registered tasks (including monitor_agents)
            self.registry.handle_cron_changes(now_tz)
            
            now_tz = _now_local()
            
            # Execute all registered tasks that are due (no separate monitor_agents path)
            self.registry.execute_due_tasks(now_tz)
            
            time.sleep(1)
          except Exception as e:
            print(f"❌ SCHEDULER LOOP ERROR (recovering): {e}")
            import traceback
            traceback.print_exc()
            time.sleep(5)  # Brief pause before retrying
        
        self.alive = False
        self.registry.shutdown()
        print("Scheduler stopped. Goodbye!")


def main():
    """Entry point for the options agent scheduler."""
    print("="*70)
    print(" Option Income Lab Scheduler")
    print(" Using Microsoft Agent Framework + yfinance")
    print("="*70)
    print()
    
    try:
        scheduler = OptionsAgentScheduler()
        scheduler.run()
    except Exception as e:
        print(f"FATAL ERROR: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    print("TIP: Use 'python run.py' to start both web dashboard and scheduler.")
    print("     Use 'python run.py --scheduler-only' for scheduler only.")
    print()
    main()
