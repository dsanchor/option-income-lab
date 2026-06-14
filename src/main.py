import sys
import time
import signal
import asyncio
import asyncio.base_subprocess
import gc
from datetime import datetime
import pytz

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


class OptionsAgentScheduler:
    """Main scheduler for cron-based options agent execution."""
    
    def __init__(self):
        self.running = True
        self.config = None
        self.runner = None
        self.cosmos = None
        self.context_provider = None
        self._cron_changed = False
        self._summary_cron_changed = False
        self._options_chain_cron_changed = False
        self._dgi_screener_cron_changed = False
        self._banner_cron_changed = False
        self._calendar_cron_changed = False
        self._last_config_reload = None
        self._config_reload_interval = 60  # seconds
    
    def reschedule(self, new_cron: str, new_timezone: str = None):
        """Update cron expression and/or timezone. The run loop will pick it up on next iteration."""
        self.config.cron_expression = new_cron
        if new_timezone:
            self.config.timezone = new_timezone
        self._cron_changed = True
    
    def reschedule_summary(self, new_cron: str):
        """Update summary agent cron expression. The run loop will pick it up on next iteration."""
        summary_config = self.config.config.get('summary_agent', {})
        summary_config['cron'] = new_cron
        self.config.config['summary_agent'] = summary_config
        self._summary_cron_changed = True
    
    def reschedule_options_chain(self, new_cron: str):
        """Update options chain scheduler cron expression. The run loop will pick it up on next iteration."""
        options_chain_config = self.config.config.get('options_chain_scheduler', {})
        options_chain_config['cron'] = new_cron
        self.config.config['options_chain_scheduler'] = options_chain_config
        self._options_chain_cron_changed = True
    
    def reschedule_dgi_screener(self, new_cron: str):
        """Update DGI screener cron expression. The run loop will pick it up on next iteration."""
        dgi_config = self.config.config.get('dgi_screener', {})
        dgi_config['cron'] = new_cron
        self.config.config['dgi_screener'] = dgi_config
        self._dgi_screener_cron_changed = True

    def reschedule_banner(self, new_cron: str):
        """Update banner agent cron expression. The run loop will pick it up on next iteration."""
        banner_config = self.config.config.get('banner_agent', {})
        banner_config['cron'] = new_cron
        self.config.config['banner_agent'] = banner_config
        self._banner_cron_changed = True

    def reschedule_calendar(self, new_cron: str):
        """Update calendar sync cron expression. The run loop will pick it up on next iteration."""
        calendar_config = self.config.config.get('calendar_sync', {})
        calendar_config['cron'] = new_cron
        self.config.config['calendar_sync'] = calendar_config
        self._calendar_cron_changed = True
    
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
        )
        
        print(f"Scheduler configured with cron: {self.config.cron_expression}")
        print(f"Scheduler timezone: {self.config.timezone}")
        
        # Log summary agent configuration
        summary_config = self.config.config.get('summary_agent', {})
        summary_enabled = summary_config.get('enabled', True)
        summary_cron = summary_config.get('cron', '0 8 * * *')
        summary_activity_count = summary_config.get('activity_count', 3)
        
        print(f"\nSummary Agent Configuration:")
        print(f"  Enabled: {summary_enabled}")
        if summary_enabled:
            print(f"  Cron: {summary_cron}")
            print(f"  Timezone: {self.config.timezone}")
            print(f"  Activity count: {summary_activity_count}")
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
            print(f"  Timezone: {self.config.timezone}")
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
            print(f"  Timezone: {self.config.timezone}")
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
            print(f"  Timezone: {self.config.timezone}")
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
            print(f"  Timezone: {self.config.timezone}")
        else:
            print(f"  Status: Disabled in config")
    
    def run_all_agents(self):
        """Execute all agents (bridges async to sync for scheduler)."""
        _run_async(self._run_all_agents_async())
    
    async def _run_all_agents_async(self):
        """Execute all agents asynchronously."""
        tz = pytz.timezone(self.config.timezone)
        now_tz = datetime.now(tz)
        print(f"\n{'#'*70}")
        print(f"# Starting scheduled agent run at {now_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"{'#'*70}\n")
        
        try:
            cosmos = self.cosmos
            ctx = self.context_provider
            runner = self.runner
            config = self.config

            # Run covered call agent
            await run_covered_call_analysis(config, runner, cosmos, ctx)
            
            # Run cash secured put agent
            await run_cash_secured_put_analysis(config, runner, cosmos, ctx)

            # Run buy tracker agent
            await run_buy_tracker_analysis(config, runner, cosmos, ctx)

            # Run open position monitors
            await run_open_call_monitor(config, runner, cosmos, ctx)
            await run_open_put_monitor(config, runner, cosmos, ctx)
            
        except Exception as e:
            print(f"ERROR during agent execution: {str(e)}")
        
        tz = pytz.timezone(self.config.timezone)
        now_tz = datetime.now(tz)
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
        
        tz = pytz.timezone(self.config.timezone)
        now_tz = datetime.now(tz)
        print(f"\n{'='*70}")
        print(f"📊 Summary Agent - Scheduled run at {now_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"{'='*70}\n")
        
        activity_count = summary_config.get('activity_count', 3)
        await self.runner.run_summary_agent(
            cosmos=self.cosmos,
            telegram_notifier=self.runner.telegram_notifier,
            activity_count=activity_count,
            model=self.config.model_for('summary'),
        )
    
    def run_options_chain_fetch_job(self):
        """Execute options chain fetch job (bridges async to sync for scheduler)."""
        _run_async(self._run_options_chain_fetch_async())
    
    async def _run_options_chain_fetch_async(self):
        """Fetch all market data for all symbols via yfinance (pre-warm cache)."""
        options_chain_config = self.config.config.get('options_chain_scheduler', {})
        if not options_chain_config.get('enabled', True):
            print("⏭️  Options chain scheduler disabled in config")
            return
        
        from .yfinance_data_provider import get_shared_provider
        
        tz = pytz.timezone(self.config.timezone)
        now_tz = datetime.now(tz)
        print(f"\n{'~'*70}")
        print(f"📈 Market Data Fetcher - Scheduled run at {now_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"{'~'*70}\n")
        
        symbols = self.cosmos.list_symbols()
        
        print(f"Fetching market data for {len(symbols)} symbols...")
        success_count = 0
        error_count = 0
        
        provider = get_shared_provider(getattr(self.config, 'yfinance_config', None))
        for sym_doc in symbols:
            symbol = sym_doc["symbol"]
            
            try:
                data = await provider.fetch_all(symbol, force_refresh=True)
                oc = data.get("options_chain", "")
                success_count += 1
                print(f"  ✓ {symbol}: {len(oc)} chars options chain cached")
            except Exception as e:
                error_count += 1
                print(f"  ✗ {symbol}: {str(e)}")
        
        print(f"\n{'~'*70}")
        print(f"Market Data Fetch Complete: {success_count} success, {error_count} errors")
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
        
        tz = pytz.timezone(self.config.timezone)
        now_tz = datetime.now(tz)
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

    def run_banner_agent_job(self):
        """Execute banner agent (bridges async to sync for scheduler)."""
        _run_async(self._run_banner_agent_async())

    async def _run_banner_agent_async(self):
        """Run dashboard banner agent if enabled in config."""
        banner_config = self.config.config.get('banner_agent', {})
        if not banner_config.get('enabled', True):
            print("⏭️  Dashboard banner agent disabled in config")
            return

        tz = pytz.timezone(self.config.timezone)
        now_tz = datetime.now(tz)
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

    async def _run_calendar_sync_async(self):
        """Fetch earnings and ex-dividend dates from yfinance and store in CosmosDB."""
        calendar_config = self.config.config.get('calendar_sync', {})
        if not calendar_config.get('enabled', True):
            print("⏭️  Calendar sync disabled in config")
            return

        tz = pytz.timezone(self.config.timezone)
        now_tz = datetime.now(tz)
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

            has_active_position = any(
                p.get("status") == "active" for p in sym_doc.get("positions", [])
            )

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
                    self.cosmos.upsert_calendar_event(symbol, "earnings", earnings_date, has_active_position)
                    updated += 1
                except (OSError, ValueError):
                    pass

            # Ex-dividend date
            ex_div_ts = info.get("exDividendDate")
            if ex_div_ts:
                try:
                    ex_div_date = datetime.fromtimestamp(ex_div_ts, tz=timezone.utc).strftime("%Y-%m-%d")
                    self.cosmos.upsert_calendar_event(symbol, "ex_dividend", ex_div_date, has_active_position)
                    updated += 1
                except (OSError, ValueError):
                    pass

        print(f"\nCalendar Sync Complete: {updated} events updated, {errors} errors, {len(symbols)} symbols processed")
    
    def signal_handler(self, sig, frame):
        """Handle graceful shutdown on Ctrl+C."""
        print("\n\nShutdown signal received. Stopping scheduler...")
        self.running = False
    
    def _reload_config_from_cosmos(self):
        """Reload settings from CosmosDB and detect changes to cron/timezone.
        
        This method is called periodically to pick up configuration changes
        made through the web UI without requiring a scheduler restart.
        """
        try:
            cosmos_settings = self.cosmos.get_settings()
            if not cosmos_settings:
                return
            
            # Track if we need to update anything
            main_cron_changed = False
            summary_cron_changed = False
            options_chain_cron_changed = False
            banner_cron_changed = False
            timezone_changed = False
            
            # Check scheduler settings
            scheduler_settings = cosmos_settings.get('scheduler', {})
            new_cron = scheduler_settings.get('cron')
            new_timezone = scheduler_settings.get('timezone')
            
            if new_cron and new_cron != self.config.cron_expression:
                self.config.cron_expression = new_cron
                main_cron_changed = True
            
            if new_timezone and new_timezone != self.config.timezone:
                old_timezone = self.config.timezone
                self.config.timezone = new_timezone
                timezone_changed = True
                # If timezone changed, recalculate both schedules
                if not main_cron_changed:
                    main_cron_changed = True
            
            # Check summary agent settings
            summary_settings = cosmos_settings.get('summary_agent', {})
            new_summary_cron = summary_settings.get('cron')
            current_summary_cron = self.config.config.get('summary_agent', {}).get('cron', '0 8 * * *')
            
            if new_summary_cron and new_summary_cron != current_summary_cron:
                if 'summary_agent' not in self.config.config:
                    self.config.config['summary_agent'] = {}
                self.config.config['summary_agent']['cron'] = new_summary_cron
                summary_cron_changed = True
            
            # Update other summary agent settings
            if summary_settings:
                if 'summary_agent' not in self.config.config:
                    self.config.config['summary_agent'] = {}
                for key in ['enabled', 'activity_count']:
                    if key in summary_settings:
                        self.config.config['summary_agent'][key] = summary_settings[key]
            
            # Check options chain scheduler settings
            options_chain_settings = cosmos_settings.get('options_chain_scheduler', {})
            new_options_chain_cron = options_chain_settings.get('cron')
            current_options_chain_cron = self.config.config.get('options_chain_scheduler', {}).get('cron', '0 * * * *')
            
            if new_options_chain_cron and new_options_chain_cron != current_options_chain_cron:
                if 'options_chain_scheduler' not in self.config.config:
                    self.config.config['options_chain_scheduler'] = {}
                self.config.config['options_chain_scheduler']['cron'] = new_options_chain_cron
                options_chain_cron_changed = True
            
            # Update other options chain scheduler settings
            if options_chain_settings:
                if 'options_chain_scheduler' not in self.config.config:
                    self.config.config['options_chain_scheduler'] = {}
                for key in ['enabled']:
                    if key in options_chain_settings:
                        self.config.config['options_chain_scheduler'][key] = options_chain_settings[key]
            
            # Check DGI screener settings
            dgi_settings = cosmos_settings.get('dgi_screener', {})
            new_dgi_cron = dgi_settings.get('cron')
            current_dgi_cron = self.config.config.get('dgi_screener', {}).get('cron', '0 6 * * 1-5')
            
            dgi_cron_changed = False
            if new_dgi_cron and new_dgi_cron != current_dgi_cron:
                if 'dgi_screener' not in self.config.config:
                    self.config.config['dgi_screener'] = {}
                self.config.config['dgi_screener']['cron'] = new_dgi_cron
                dgi_cron_changed = True
            
            if dgi_settings:
                if 'dgi_screener' not in self.config.config:
                    self.config.config['dgi_screener'] = {}
                for key in ['enabled']:
                    if key in dgi_settings:
                        self.config.config['dgi_screener'][key] = dgi_settings[key]

            banner_settings = cosmos_settings.get('banner_agent', {})
            new_banner_cron = banner_settings.get('cron')
            current_banner_cron = self.config.config.get('banner_agent', {}).get('cron', '0 5 * * *')

            if new_banner_cron and new_banner_cron != current_banner_cron:
                if 'banner_agent' not in self.config.config:
                    self.config.config['banner_agent'] = {}
                self.config.config['banner_agent']['cron'] = new_banner_cron
                banner_cron_changed = True

            if banner_settings:
                if 'banner_agent' not in self.config.config:
                    self.config.config['banner_agent'] = {}
                for key in ['enabled', 'max_items']:
                    if key in banner_settings:
                        self.config.config['banner_agent'][key] = banner_settings[key]
            
            # Set flags for the main loop to pick up
            if main_cron_changed:
                self._cron_changed = True
                if timezone_changed:
                    print(f"✓ Config reloaded from CosmosDB: timezone changed to {new_timezone}")
                if new_cron:
                    print(f"✓ Config reloaded from CosmosDB: monitor cron changed to {new_cron}")
            
            if summary_cron_changed:
                self._summary_cron_changed = True
                print(f"✓ Config reloaded from CosmosDB: summary cron changed to {new_summary_cron}")
            
            if options_chain_cron_changed:
                self._options_chain_cron_changed = True
                print(f"✓ Config reloaded from CosmosDB: options chain cron changed to {new_options_chain_cron}")
            
            if dgi_cron_changed:
                self._dgi_screener_cron_changed = True
                print(f"✓ Config reloaded from CosmosDB: DGI screener cron changed to {new_dgi_cron}")

            if banner_cron_changed:
                self._banner_cron_changed = True
                print(f"✓ Config reloaded from CosmosDB: banner agent cron changed to {new_banner_cron}")

            # Check calendar sync settings
            calendar_settings = cosmos_settings.get('calendar_sync', {})
            new_calendar_cron = calendar_settings.get('cron')
            current_calendar_cron = self.config.config.get('calendar_sync', {}).get('cron', '0 5 * * 1-5')

            calendar_cron_changed = False
            if new_calendar_cron and new_calendar_cron != current_calendar_cron:
                if 'calendar_sync' not in self.config.config:
                    self.config.config['calendar_sync'] = {}
                self.config.config['calendar_sync']['cron'] = new_calendar_cron
                calendar_cron_changed = True

            if calendar_settings:
                if 'calendar_sync' not in self.config.config:
                    self.config.config['calendar_sync'] = {}
                for key in ['enabled']:
                    if key in calendar_settings:
                        self.config.config['calendar_sync'][key] = calendar_settings[key]

            if calendar_cron_changed:
                self._calendar_cron_changed = True
                print(f"✓ Config reloaded from CosmosDB: calendar sync cron changed to {new_calendar_cron}")
                
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
        
        tz = pytz.timezone(self.config.timezone)
        now_tz = datetime.now(tz)
        
        # Initialize main scheduler cron
        cron = croniter(self.config.cron_expression, now_tz)
        next_run = cron.get_next(datetime)
        
        # Initialize summary agent cron (if enabled)
        summary_config = self.config.config.get('summary_agent', {})
        summary_enabled = summary_config.get('enabled', True)
        summary_cron_expr = summary_config.get('cron', '0 8 * * *')
        summary_next_run = None
        summary_cron = None
        
        # Initialize options chain scheduler cron (if enabled)
        options_chain_config = self.config.config.get('options_chain_scheduler', {})
        options_chain_enabled = options_chain_config.get('enabled', True)
        options_chain_cron_expr = options_chain_config.get('cron', '0 * * * *')
        options_chain_next_run = None
        options_chain_cron = None
        
        # Initialize DGI screener cron (if enabled)
        dgi_config = self.config.config.get('dgi_screener', {})
        dgi_enabled = dgi_config.get('enabled', True)
        dgi_cron_expr = dgi_config.get('cron', '0 6 * * 1-5')
        dgi_next_run = None
        dgi_cron = None

        # Initialize dashboard banner cron (if enabled)
        banner_config = self.config.config.get('banner_agent', {})
        banner_enabled = banner_config.get('enabled', True)
        banner_cron_expr = banner_config.get('cron', '0 5 * * *')
        banner_next_run = None
        banner_cron = None

        # Initialize calendar sync cron (if enabled)
        calendar_config = self.config.config.get('calendar_sync', {})
        calendar_enabled = calendar_config.get('enabled', True)
        calendar_cron_expr = calendar_config.get('cron', '0 5 * * 1-5')
        calendar_next_run = None
        calendar_cron = None
        
        if summary_enabled:
            try:
                summary_cron = croniter(summary_cron_expr, now_tz)
                summary_next_run = summary_cron.get_next(datetime)
            except (ValueError, KeyError) as e:
                print(f"⚠️  Invalid summary agent cron expression '{summary_cron_expr}': {e}")
                print(f"⚠️  Summary agent scheduling disabled")
                summary_enabled = False
        
        if options_chain_enabled:
            try:
                options_chain_cron = croniter(options_chain_cron_expr, now_tz)
                options_chain_next_run = options_chain_cron.get_next(datetime)
            except (ValueError, KeyError) as e:
                print(f"⚠️  Invalid options chain cron expression '{options_chain_cron_expr}': {e}")
                print(f"⚠️  Options chain scheduling disabled")
                options_chain_enabled = False
        
        if dgi_enabled:
            try:
                dgi_cron = croniter(dgi_cron_expr, now_tz)
                dgi_next_run = dgi_cron.get_next(datetime)
            except (ValueError, KeyError) as e:
                print(f"⚠️  Invalid DGI screener cron expression '{dgi_cron_expr}': {e}")
                print(f"⚠️  DGI screener scheduling disabled")
                dgi_enabled = False

        if banner_enabled:
            try:
                banner_cron = croniter(banner_cron_expr, now_tz)
                banner_next_run = banner_cron.get_next(datetime)
            except (ValueError, KeyError) as e:
                print(f"⚠️  Invalid banner agent cron expression '{banner_cron_expr}': {e}")
                print(f"⚠️  Dashboard banner scheduling disabled")
                banner_enabled = False

        if calendar_enabled:
            try:
                calendar_cron = croniter(calendar_cron_expr, now_tz)
                calendar_next_run = calendar_cron.get_next(datetime)
            except (ValueError, KeyError) as e:
                print(f"⚠️  Invalid calendar sync cron expression '{calendar_cron_expr}': {e}")
                print(f"⚠️  Calendar sync scheduling disabled")
                calendar_enabled = False
        
        # Display initial schedule
        print(f"\nMonitor Agents        - Next run: {next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        if summary_enabled and summary_next_run:
            print(f"Summary Agent         - Next run: {summary_next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        else:
            print(f"Summary Agent         - Disabled")
        if options_chain_enabled and options_chain_next_run:
            print(f"Options Chain Fetcher - Next run: {options_chain_next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        else:
            print(f"Options Chain Fetcher - Disabled")
        if dgi_enabled and dgi_next_run:
            print(f"DGI Screener          - Next run: {dgi_next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        else:
            print(f"DGI Screener          - Disabled")
        if banner_enabled and banner_next_run:
            print(f"Dashboard Banner      - Next run: {banner_next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        else:
            print(f"Dashboard Banner      - Disabled")
        if calendar_enabled and calendar_next_run:
            print(f"Calendar Sync         - Next run: {calendar_next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        else:
            print(f"Calendar Sync         - Disabled")
        
        # Track when we last reloaded config
        self._last_config_reload = time.time()
        
        print("Press Ctrl+C to stop\n")
        
        while self.running:
            # Periodically reload config from CosmosDB to pick up web UI changes
            current_time = time.time()
            if current_time - self._last_config_reload >= self._config_reload_interval:
                self._reload_config_from_cosmos()
                self._last_config_reload = current_time
            
            # Check if main cron was updated from the web UI
            if self._cron_changed:
                self._cron_changed = False
                tz = pytz.timezone(self.config.timezone)
                now_tz = datetime.now(tz)
                cron = croniter(self.config.cron_expression, now_tz)
                next_run = cron.get_next(datetime)
                print(f"Monitor agents cron rescheduled to: {self.config.cron_expression}")
                print(f"Timezone: {self.config.timezone}")
                print(f"Next scheduled run: {next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
            
            # Check if summary cron was updated from the web UI
            if self._summary_cron_changed:
                self._summary_cron_changed = False
                summary_config = self.config.config.get('summary_agent', {})
                summary_cron_expr = summary_config.get('cron', '0 8 * * *')
                try:
                    tz = pytz.timezone(self.config.timezone)
                    now_tz = datetime.now(tz)
                    summary_cron = croniter(summary_cron_expr, now_tz)
                    summary_next_run = summary_cron.get_next(datetime)
                    summary_enabled = summary_config.get('enabled', True)
                    print(f"Summary agent cron rescheduled to: {summary_cron_expr}")
                    print(f"Next scheduled run: {summary_next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
                except (ValueError, KeyError) as e:
                    print(f"⚠️  Invalid summary agent cron expression '{summary_cron_expr}': {e}")
                    summary_enabled = False
            
            # Check if options chain cron was updated from the web UI
            if self._options_chain_cron_changed:
                self._options_chain_cron_changed = False
                options_chain_config = self.config.config.get('options_chain_scheduler', {})
                options_chain_cron_expr = options_chain_config.get('cron', '0 * * * *')
                try:
                    tz = pytz.timezone(self.config.timezone)
                    now_tz = datetime.now(tz)
                    options_chain_cron = croniter(options_chain_cron_expr, now_tz)
                    options_chain_next_run = options_chain_cron.get_next(datetime)
                    options_chain_enabled = options_chain_config.get('enabled', True)
                    print(f"Options chain scheduler cron rescheduled to: {options_chain_cron_expr}")
                    print(f"Next scheduled run: {options_chain_next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
                except (ValueError, KeyError) as e:
                    print(f"⚠️  Invalid options chain cron expression '{options_chain_cron_expr}': {e}")
                    options_chain_enabled = False
            
            # Check if DGI screener cron was updated from the web UI
            if self._dgi_screener_cron_changed:
                self._dgi_screener_cron_changed = False
                dgi_config = self.config.config.get('dgi_screener', {})
                dgi_cron_expr = dgi_config.get('cron', '0 6 * * 1-5')
                try:
                    tz = pytz.timezone(self.config.timezone)
                    now_tz = datetime.now(tz)
                    dgi_cron = croniter(dgi_cron_expr, now_tz)
                    dgi_next_run = dgi_cron.get_next(datetime)
                    dgi_enabled = dgi_config.get('enabled', True)
                    print(f"DGI screener cron rescheduled to: {dgi_cron_expr}")
                    print(f"Next scheduled run: {dgi_next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
                except (ValueError, KeyError) as e:
                    print(f"⚠️  Invalid DGI screener cron expression '{dgi_cron_expr}': {e}")
                    dgi_enabled = False

            # Check if dashboard banner cron was updated from the web UI
            if self._banner_cron_changed:
                self._banner_cron_changed = False
                banner_config = self.config.config.get('banner_agent', {})
                banner_cron_expr = banner_config.get('cron', '0 5 * * *')
                try:
                    tz = pytz.timezone(self.config.timezone)
                    now_tz = datetime.now(tz)
                    banner_cron = croniter(banner_cron_expr, now_tz)
                    banner_next_run = banner_cron.get_next(datetime)
                    banner_enabled = banner_config.get('enabled', True)
                    print(f"Dashboard banner cron rescheduled to: {banner_cron_expr}")
                    print(f"Next scheduled run: {banner_next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
                except (ValueError, KeyError) as e:
                    print(f"⚠️  Invalid banner agent cron expression '{banner_cron_expr}': {e}")
                    banner_enabled = False

            # Check if calendar cron was updated from the web UI
            if self._calendar_cron_changed:
                self._calendar_cron_changed = False
                calendar_config = self.config.config.get('calendar_sync', {})
                calendar_cron_expr = calendar_config.get('cron', '0 5 * * 1-5')
                try:
                    tz = pytz.timezone(self.config.timezone)
                    now_tz = datetime.now(tz)
                    calendar_cron = croniter(calendar_cron_expr, now_tz)
                    calendar_next_run = calendar_cron.get_next(datetime)
                    calendar_enabled = calendar_config.get('enabled', True)
                    print(f"Calendar sync cron rescheduled to: {calendar_cron_expr}")
                    print(f"Next scheduled run: {calendar_next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
                except (ValueError, KeyError) as e:
                    print(f"⚠️  Invalid calendar sync cron expression '{calendar_cron_expr}': {e}")
                    calendar_enabled = False

            now_tz = datetime.now(tz)
            
            # Check main scheduler
            if now_tz >= next_run:
                self.run_all_agents()
                next_run = cron.get_next(datetime)
                print(f"Monitor Agents - Next run: {next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
            
            # Check summary agent scheduler
            if summary_enabled and summary_next_run and now_tz >= summary_next_run:
                self.run_summary_agent_job()
                summary_next_run = summary_cron.get_next(datetime)
                print(f"Summary Agent  - Next run: {summary_next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
            
            # Check options chain scheduler
            if options_chain_enabled and options_chain_next_run and now_tz >= options_chain_next_run:
                self.run_options_chain_fetch_job()
                options_chain_next_run = options_chain_cron.get_next(datetime)
                print(f"Options Chain Fetcher - Next run: {options_chain_next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
                print(f"Summary Agent  - Next run: {summary_next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
            
            # Check DGI screener scheduler
            if dgi_enabled and dgi_next_run and now_tz >= dgi_next_run:
                self.run_dgi_screener_job()
                dgi_next_run = dgi_cron.get_next(datetime)
                print(f"DGI Screener          - Next run: {dgi_next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")

            # Check dashboard banner scheduler
            if banner_enabled and banner_next_run and now_tz >= banner_next_run:
                self.run_banner_agent_job()
                banner_next_run = banner_cron.get_next(datetime)
                print(f"Dashboard Banner      - Next run: {banner_next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")

            if calendar_enabled and calendar_next_run and now_tz >= calendar_next_run:
                self.run_calendar_sync_job()
                calendar_next_run = calendar_cron.get_next(datetime)
                print(f"Calendar Sync         - Next run: {calendar_next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
            
            time.sleep(1)
        
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
