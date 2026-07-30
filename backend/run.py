#!/usr/bin/env python3
"""
Unified entry point for Option Income Lab.

  python run.py                  # web dashboard + scheduler
  python run.py --web-only       # web dashboard only
  python run.py --scheduler-only # scheduler only (no web UI)
  python run.py --port 9000      # override web server port
"""

import argparse
import threading
from contextlib import asynccontextmanager

from dotenv import load_dotenv
import yaml
import uvicorn

load_dotenv()  # Load .env file if present

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_config():
    with open("config.yaml", "r") as f:
        return yaml.safe_load(f)


def _web_host_port(args, config):
    web_cfg = config.get("web", {})
    host = web_cfg.get("host", "0.0.0.0")
    port = args.port if args.port is not None else web_cfg.get("port", 8000)
    return host, port


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------

_scheduler_instance = None
_scheduler_thread = None


def _start_scheduler():
    global _scheduler_instance, _scheduler_thread
    from src.main import OptionsAgentScheduler

    _scheduler_instance = OptionsAgentScheduler()
    _scheduler_thread = threading.Thread(
        target=_scheduler_instance.run,
        kwargs={"install_signals": False},
        daemon=True,
    )
    _scheduler_thread.start()


def _stop_scheduler():
    global _scheduler_instance, _scheduler_thread
    if _scheduler_instance:
        _scheduler_instance.running = False
    if _scheduler_thread:
        _scheduler_thread.join(timeout=10)


@asynccontextmanager
async def lifespan(app):
    # Initialise CosmosDB (on_event("startup") is skipped when lifespan is set)
    from web.app import init_cosmos
    await init_cosmos(app)

    # Initialize yfinance provider (must be here — on_event("startup") is
    # skipped when a lifespan context manager is attached)
    import logging
    _logger = logging.getLogger(__name__)
    try:
        from src.yfinance_data_provider import get_shared_provider
        app.state.yf_provider = get_shared_provider()
        _logger.info("YFinance data provider initialized successfully (shared singleton)")
    except Exception as e:
        _logger.exception("YFinance provider init failed")
        app.state.yf_provider = None

    _start_scheduler()
    app.state.scheduler = _scheduler_instance
    yield
    _stop_scheduler()


# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

def _print_banner(host, port, cron, mode):
    url = f"http://{'localhost' if host == '0.0.0.0' else host}:{port}"
    print()
    print("══════════════════════════════════════════════════════════════════════")
    if mode == "both":
        print(" Option Income Lab — Web Dashboard + Scheduler")
        print(f" Dashboard: {url}")
        print(f" Cron:      {cron}")
    elif mode == "web":
        print(" Option Income Lab — Web Dashboard")
        print(f" Dashboard: {url}")
    elif mode == "scheduler":
        print(" Option Income Lab — Scheduler Only")
        print(f" Cron:      {cron}")
    print("══════════════════════════════════════════════════════════════════════")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Option Income Lab")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--web-only", action="store_true", help="Start web dashboard only (no scheduler)")
    group.add_argument("--scheduler-only", action="store_true", help="Start scheduler only (no web UI)")
    group.add_argument("--api-only", action="store_true", help="Start JSON API + scheduler, no HTML UI (for the api container)")
    parser.add_argument("--port", type=int, default=None, help="Web server port (default: from config or 8000)")
    args = parser.parse_args()

    config = _load_config()
    cron = config.get("scheduler", {}).get("cron", "0 14-21/2 * * 1-5")
    host, port = _web_host_port(args, config)

    if args.scheduler_only:
        _print_banner(host, port, cron, "scheduler")
        from src.main import OptionsAgentScheduler
        scheduler = OptionsAgentScheduler()
        scheduler.run()
    elif args.web_only:
        _print_banner(host, port, cron, "web")
        from web.app import app
        uvicorn.run(app, host=host, port=port)
    elif args.api_only:
        # JSON API + scheduler, HTML routes blocked (api container).
        import os as _os
        _os.environ["API_ONLY"] = "1"
        _print_banner(host, port, cron, "both")
        from web.app import app
        app.router.lifespan_context = lifespan
        uvicorn.run(app, host=host, port=port)
    else:
        _print_banner(host, port, cron, "both")
        from web.app import app
        app.router.lifespan_context = lifespan
        uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
