import asyncio
import copy
import inspect
import json
import logging
import math
import os
import re
import threading
import time
from calendar import month_abbr
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict
from typing import Any, Dict, List, Optional

import yaml
from croniter import croniter
from fastapi import FastAPI, Request, Query, Body
from pydantic import BaseModel, Field

from src.market_hours import is_us_market_open
from fastapi.responses import JSONResponse

from src.cosmos_db import is_watchlist_paused
from src.scheduler_registry import _MAX_TASK_DURATION_SECONDS
from src.best_options import DEFAULT_DTE_MIN, DEFAULT_DTE_MAX

try:
    import yfinance as yf
except ImportError:
    yf = None

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Agent type metadata — labels only; data comes from CosmosDB
AGENT_TYPES = {
    "open_call_monitor": {"label": "Open Call Monitor", "is_position_monitor": True},
    "open_put_monitor": {"label": "Open Put Monitor", "is_position_monitor": True},
    "covered_call": {"label": "Following · Covered Call", "is_position_monitor": False},
    "cash_secured_put": {"label": "Following · Cash-Secured Put", "is_position_monitor": False},
    "buy_tracker": {"label": "Following · Buy Tracker", "is_position_monitor": False},
}

# ---------------------------------------------------------------------------
# Config utilities
# ---------------------------------------------------------------------------

def _load_config() -> Dict[str, Any]:
    """Load raw config.yaml without env-var substitution (web doesn't need secrets)."""
    config_path = PROJECT_ROOT / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def _write_config(config: Dict[str, Any]):
    config_path = PROJECT_ROOT / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


def _resolve_env(s: str) -> str:
    """Resolve ${VAR_NAME} patterns in a string."""
    def _repl(m):
        var_name = m.group(1)
        value = os.environ.get(var_name, "")
        if not value:
            logger.warning("Environment variable %s is not set", var_name)
        return value
    return re.sub(r'\$\{([^}]+)\}', _repl, s)


def _function_llm_config(config_obj, function_id: str):
    resolver = getattr(config_obj, "llm_config_for_function", None)
    return resolver(function_id) if resolver else config_obj.llm_config()


def _llm_settings_response(function_id: str | None = None):
    """Load Config and return (config_obj, error_response_or_none)."""
    from src.config import Config
    from src.llm import validate_llm_config

    config_obj = Config()
    llm = (
        _function_llm_config(config_obj, function_id)
        if function_id
        else config_obj.llm_config()
    )
    err = validate_llm_config(llm)
    if err:
        return config_obj, JSONResponse({"error": err}, status_code=500)
    return config_obj, None


def _load_settings_from_cosmos(cosmos) -> Optional[dict]:
    """Load settings from CosmosDB. Returns None if unavailable."""
    if cosmos is None:
        return None
    try:
        return cosmos.get_settings()
    except Exception:
        logger.warning("Failed to load settings from CosmosDB", exc_info=True)
        return None


def _load_settings_from_cosmos_required(cosmos) -> dict:
    """Load settings/app-config without hiding Cosmos failures."""
    if cosmos is None:
        raise RuntimeError("CosmosDB is not initialized")
    loader = getattr(cosmos, "get_settings_required", None)
    if loader is None:
        loader = cosmos.get_settings
    return loader()


def _save_settings_to_cosmos(cosmos, settings: dict):
    """Save settings to CosmosDB. Best-effort."""
    if cosmos is None:
        return
    try:
        cosmos.save_settings(settings)
        logger.info("Settings saved to CosmosDB")
    except Exception:
        logger.warning("Failed to save settings to CosmosDB", exc_info=True)


def parse_timestamp(ts: str) -> Optional[datetime]:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(ts, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _parse_numeric(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    if isinstance(value, str):
        cleaned = value.strip().replace("$", "").replace(",", "")
        if not cleaned or cleaned.upper() in {"N/A", "NA", "NONE", "NULL", "—", "-"}:
            return None
        try:
            numeric = float(cleaned)
        except ValueError:
            return None
        return numeric if math.isfinite(numeric) else None
    return None


def _parse_non_negative_number(value: Any) -> Optional[float]:
    numeric = _parse_numeric(value)
    return numeric if numeric is not None and numeric >= 0 else None


def _parse_datetime_value(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = parse_timestamp(value.strip())
    if parsed is not None:
        return parsed
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_date_value(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _round2(value: float) -> float:
    return round(value, 2)


def _average(values: List[float]) -> float:
    return _round2(sum(values) / len(values)) if values else 0.0


def _group_economics_metrics(positions: List[Dict[str, Any]]) -> Dict[str, float]:
    total_premium = sum(p["premium"] for p in positions)
    total_buyback = sum(
        p["buyback_cost"] for p in positions if p["buyback_cost"] is not None
    )
    total_net = total_premium - total_buyback
    # Weighted RoC: net / total capital deployed (sum of strikes × 100)
    total_capital = sum(
        p["strike"] * 100 for p in positions if p["strike"] is not None and p["strike"] > 0
    )
    avg_roc_pct = _round2((total_net / total_capital) * 100) if total_capital > 0 else 0.0
    # Annualized: weight by average days to expiration
    days_values = [p["_days_to_exp"] for p in positions if p.get("_days_to_exp") and p["_days_to_exp"] > 0]
    avg_days = sum(days_values) / len(days_values) if days_values else 0
    avg_roc_annualized = _round2(avg_roc_pct * (365 / avg_days)) if avg_days > 0 else avg_roc_pct
    return {
        "premium": _round2(total_premium),
        "buyback": _round2(total_buyback),
        "net": _round2(total_net),
        "count": len(positions),
        "avg_roc_pct": avg_roc_pct,
        "avg_roc_annualized": avg_roc_annualized,
    }


def _build_economics_report(symbol_docs: List[Dict[str, Any]],
                            year: Optional[int] = None,
                            month_filter: Optional[List[int]] = None,
                            symbol_filter: Optional[List[str]] = None,
                            option_type: Optional[str] = None,
                            status_filter: Optional[str] = None,
                            now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    all_positions: List[Dict[str, Any]] = []
    available_years: set[int] = set()
    available_symbols: set[str] = set()

    for symbol_doc in symbol_docs:
        symbol = str(symbol_doc.get("symbol", "")).upper()
        if not symbol:
            continue
        for position in symbol_doc.get("positions", []):
            source = position.get("source")
            if not isinstance(source, dict):
                source = {}
            premium = _parse_numeric(source.get("premium"))
            if premium is None:
                continue

            # Options contracts are for 100 shares
            CONTRACT_MULTIPLIER = 100

            opened_dt = _parse_datetime_value(position.get("opened_at"))
            closed_dt = _parse_datetime_value(position.get("closed_at"))
            expiration_dt = _parse_date_value(position.get("expiration"))
            strike = _parse_numeric(position.get("strike"))
            buyback_cost = _parse_numeric(position.get("buyback_cost"))
            status = str(position.get("status", "active")).lower()
            pos_type = str(position.get("type", "")).lower()

            # Dollar amounts (per contract = premium × 100)
            premium_total = premium * CONTRACT_MULTIPLIER
            buyback_total = buyback_cost * CONTRACT_MULTIPLIER if buyback_cost is not None else None

            # Net RoC uses (premium - buyback) when buyback exists
            net_per_share = premium - buyback_cost if buyback_cost is not None else premium
            roc_pct = None
            if strike not in (None, 0):
                roc_pct = _round2((net_per_share / strike) * 100)

            roc_annualized = None
            days_to_expiration = 0
            if roc_pct is not None and opened_dt and expiration_dt:
                days_to_expiration = (
                    expiration_dt.date() - opened_dt.astimezone(timezone.utc).date()
                ).days
                if days_to_expiration > 0:
                    roc_annualized = _round2(
                        roc_pct * (365 / days_to_expiration)
                    )

            days_held = None
            if opened_dt is not None:
                end_dt = closed_dt or now
                days_held = max(
                    (end_dt.astimezone(timezone.utc).date()
                     - opened_dt.astimezone(timezone.utc).date()).days,
                    0,
                )
                # Cap at expiration date to avoid inflated days when
                # position is closed late (after expiration)
                if expiration_dt and days_to_expiration > 0:
                    days_held = min(days_held, days_to_expiration)

            position_data = {
                "symbol": symbol,
                "position_id": position.get("position_id"),
                "type": pos_type,
                "strike": strike,
                "expiration": position.get("expiration"),
                "premium": _round2(premium_total),
                "premium_per_share": _round2(premium),
                "buyback_cost": _round2(buyback_total) if buyback_total is not None else None,
                "buyback_per_share": _round2(buyback_cost) if buyback_cost is not None else None,
                "net": _round2(premium_total - buyback_total) if buyback_total is not None else _round2(premium_total),
                "roc_pct": roc_pct,
                "roc_annualized": roc_annualized,
                "days_held": days_held,
                "status": status,
                "opened_at": position.get("opened_at"),
                "_opened_year": opened_dt.year if opened_dt else None,
                "_opened_month": opened_dt.month if opened_dt else None,
                "_days_to_exp": days_to_expiration if days_to_expiration > 0 else None,
            }
            all_positions.append(position_data)
            available_symbols.add(symbol)
            if opened_dt is not None:
                available_years.add(opened_dt.year)

    filtered_positions = [
        position for position in all_positions
        if (year is None or position["_opened_year"] == year)
        and (month_filter is None or position["_opened_month"] in month_filter)
        and (symbol_filter is None or position["symbol"] in symbol_filter)
        and (option_type is None or position["type"] == option_type)
        and (status_filter is None or position["status"] == status_filter)
    ]

    summary_metrics = _group_economics_metrics(filtered_positions)
    settled_positions = [
        position for position in filtered_positions
        if position["status"] in {"closed", "rolled"}
    ]
    wins = [
        position for position in settled_positions
        if position["status"] == "closed"
        or (position["status"] == "rolled" and (position.get("net") or 0) > 0)
    ]
    win_rate = _round2((len(wins) / len(settled_positions)) * 100) if settled_positions else 0.0

    monthly_groups: Dict[tuple[int, int], List[Dict[str, Any]]] = defaultdict(list)
    symbol_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for position in filtered_positions:
        symbol_groups[position["symbol"]].append(position)
        if position["_opened_year"] and position["_opened_month"]:
            monthly_groups[(position["_opened_year"], position["_opened_month"])].append(position)

    monthly = []
    for (group_year, group_month) in sorted(monthly_groups):
        group_positions = monthly_groups[(group_year, group_month)]
        metrics = _group_economics_metrics(group_positions)
        calls_in_group = [p for p in group_positions if p["type"] == "call"]
        puts_in_group = [p for p in group_positions if p["type"] == "put"]
        calls_metrics = _group_economics_metrics(calls_in_group) if calls_in_group else {"net": 0}
        puts_metrics = _group_economics_metrics(puts_in_group) if puts_in_group else {"net": 0}
        monthly.append({
            "month": group_month,
            "year": group_year,
            "label": f"{month_abbr[group_month]} {group_year}",
            "premium": metrics["premium"],
            "buyback": metrics["buyback"],
            "net": metrics["net"],
            "calls_net": calls_metrics["net"],
            "puts_net": puts_metrics["net"],
            "positions_count": metrics["count"],
            "avg_roc_pct": metrics["avg_roc_pct"],
            "avg_roc_annualized": metrics["avg_roc_annualized"],
            "calls_count": len(calls_in_group),
            "puts_count": len(puts_in_group),
        })

    by_symbol = []
    for grouped_symbol in sorted(symbol_groups):
        group_positions = symbol_groups[grouped_symbol]
        metrics = _group_economics_metrics(group_positions)
        by_symbol.append({
            "symbol": grouped_symbol,
            "premium": metrics["premium"],
            "buyback": metrics["buyback"],
            "net": metrics["net"],
            "positions_count": metrics["count"],
            "avg_roc_pct": metrics["avg_roc_pct"],
            "avg_roc_annualized": metrics["avg_roc_annualized"],
        })

    calls_positions = [p for p in filtered_positions if p["type"] == "call"]
    puts_positions = [p for p in filtered_positions if p["type"] == "put"]
    calls_metrics = _group_economics_metrics(calls_positions)
    puts_metrics = _group_economics_metrics(puts_positions)

    return {
        "summary": {
            "total_premium": summary_metrics["premium"],
            "total_buyback": summary_metrics["buyback"],
            "net_income": summary_metrics["net"],
            "avg_roc_pct": summary_metrics["avg_roc_pct"],
            "avg_roc_annualized": summary_metrics["avg_roc_annualized"],
            "win_rate": win_rate,
            "total_positions": summary_metrics["count"],
        },
        "monthly": monthly,
        "by_symbol": by_symbol,
        "by_type": {
            "calls": {
                "premium": calls_metrics["premium"],
                "buyback": calls_metrics["buyback"],
                "net": calls_metrics["net"],
                "count": calls_metrics["count"],
                "avg_roc_pct": calls_metrics["avg_roc_pct"],
                "avg_roc_annualized": calls_metrics["avg_roc_annualized"],
            },
            "puts": {
                "premium": puts_metrics["premium"],
                "buyback": puts_metrics["buyback"],
                "net": puts_metrics["net"],
                "count": puts_metrics["count"],
                "avg_roc_pct": puts_metrics["avg_roc_pct"],
                "avg_roc_annualized": puts_metrics["avg_roc_annualized"],
            },
        },
        "positions": sorted(
            [
                {
                    key: value for key, value in position.items()
                    if not key.startswith("_")
                }
                for position in filtered_positions
            ],
            key=lambda position: position.get("opened_at") or "",
            reverse=True,
        ),
        "filters": {
            "years": sorted(available_years, reverse=True),
            "symbols": sorted(available_symbols),
        },
        "applied_filters": {
            "year": year,
            "symbols": symbol_filter,
            "type": option_type,
            "status": status_filter,
        },
    }


def _count_by_range(entries: List[Dict[str, Any]]) -> Dict[str, int]:
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)
    counts = {"today": 0, "week": 0, "month": 0, "total": len(entries)}
    for e in entries:
        ts = parse_timestamp(e.get("timestamp", ""))
        if ts is None:
            continue
        if ts >= today_start:
            counts["today"] += 1
        if ts >= seven_days_ago:
            counts["week"] += 1
        if ts >= thirty_days_ago:
            counts["month"] += 1
    return counts


_COSMOS_SYSTEM_KEYS = {"_rid", "_self", "_etag", "_attachments", "_ts"}


def _clean_doc(doc: dict) -> dict:
    """Strip CosmosDB system properties for API responses."""
    return {k: v for k, v in doc.items() if k not in _COSMOS_SYSTEM_KEYS}


def _sort_by_updated_at_desc(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(items, key=lambda item: item.get("updated_at", ""), reverse=True)


def _local_now() -> datetime:
    return datetime.now().astimezone()


def _format_time(dt: datetime) -> str:
    """Format datetime in the system local timezone."""
    if dt is None:
        return ""

    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Option Income Lab")


@app.get("/healthz", include_in_schema=False)
async def healthz():
    """Liveness probe — no external dependencies (safe for Container Apps)."""
    return {"status": "ok"}


async def init_cosmos(app_instance):
    """Initialise CosmosDB on the given FastAPI app. Safe to call from
    either the on_event("startup") handler or an external lifespan."""
    try:
        config = _load_config()
        cosmos_cfg = config.get("cosmosdb", {})
        endpoint = _resolve_env(cosmos_cfg.get("endpoint", ""))
        key = _resolve_env(cosmos_cfg.get("key", ""))
        database = cosmos_cfg.get("database", "stock-options-manager")

        logger.info("CosmosDB config — endpoint: %s, database: %s, "
                     "key present: %s, key length: %d",
                     endpoint or "(empty)", database,
                     bool(key), len(key))

        if endpoint and key:
            from src.cosmos_db import CosmosDBService
            cosmos = CosmosDBService(
                endpoint=endpoint, key=key, database_name=database,
            )
            # Eagerly validate the connection so failures surface at startup
            cosmos.database.read()
            app_instance.state.cosmos = cosmos
            app_instance.state.cosmos_error = None
            logger.info("CosmosDB initialized successfully: %s, database=%s",
                        endpoint, database)

            # Merge config.yaml defaults into CosmosDB (first-run seed + new keys)
            settings_defaults = {
                k: v for k, v in config.items()
                if k not in ('ai', 'azure', 'gemini', 'cosmosdb')
            }
            # Resolve env vars in defaults before storing
            from src.config import Config
            resolved_config = Config()
            resolved_defaults = {
                k: v for k, v in resolved_config.config.items()
                if k not in ('ai', 'azure', 'gemini', 'cosmosdb')
            }
            cosmos.merge_defaults(resolved_defaults)
        else:
            missing = []
            if not endpoint:
                missing.append("COSMOSDB_ENDPOINT")
            if not key:
                missing.append("COSMOSDB_KEY")
            error_msg = (f"{' and '.join(missing)} environment variable"
                         f"{'s' if len(missing) > 1 else ''} not set")
            app_instance.state.cosmos = None
            app_instance.state.cosmos_error = error_msg
            logger.warning("CosmosDB not initialized: %s", error_msg)
    except Exception as e:
        logger.exception("CosmosDB init failed")
        app_instance.state.cosmos = None
        app_instance.state.cosmos_error = str(e)


@app.on_event("startup")
async def startup():
    await init_cosmos(app)
    # Initialize yfinance provider singleton
    try:
        from src.yfinance_data_provider import get_shared_provider
        app.state.yf_provider = get_shared_provider()
        logger.info("YFinance data provider initialized successfully (shared singleton)")
    except Exception as e:
        logger.exception("YFinance provider init failed")
        app.state.yf_provider = None

    # Eager options-chain persistence probe (Danny's zero-free decision
    # §4.2): forces the first `get_options_chain_store()` construction
    # attempt now, at web-app-lifespan startup, rather than lazily on the
    # first refresh — so a broken/unreachable Cosmos connection is
    # immediately visible in the logs at ERROR (with the retry interval),
    # never a bare WARNING that scrolls away. Non-fatal either way: the app
    # continues to serve with a memory-only chain cache on failure. Note:
    # the scheduler bootstrap (src/main.py / run.py) is outside this file's
    # authorized scope — that process's own first `refresh_all` cycle
    # already triggers the same probe automatically via
    # `get_options_chain_store()`'s own first-call construction, since the
    # probe/logging lives inside the store module itself, not here.
    try:
        from src.options_chain_store import get_options_chain_store
        chain_store = get_options_chain_store()
        if chain_store.is_available():
            logger.info("options chain persistence: ENABLED at startup")
        else:
            from src.options_chain_store import get_persistence_health
            health = get_persistence_health()
            if health.get("last_error"):
                logger.error(
                    "options chain persistence: DISABLED at startup — %s "
                    "(retry in ~%ss)",
                    health["last_error"], health.get("retry_in_seconds"),
                )
            else:
                logger.info(
                    "options chain persistence: disabled at startup "
                    "(persistence_enabled=false) — memory-only mode."
                )
    except Exception:
        logger.exception("options chain persistence: startup probe failed unexpectedly")


def _get_cosmos(request: Request):
    cosmos = getattr(request.app.state, "cosmos", None)
    if cosmos is None:
        error = getattr(request.app.state, "cosmos_error", "unknown")
        raise RuntimeError(f"CosmosDB not available: {error}")
    return cosmos


# ===========================================================================
# REST API — Symbol Management
# ===========================================================================

@app.get("/api/symbols")
async def api_list_symbols(request: Request):
    try:
        cosmos = _get_cosmos(request)
        symbols = cosmos.list_symbols()
        return JSONResponse([_clean_doc(s) for s in symbols])
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


def _compute_symbols_overview(cosmos):
    """View-model for the Symbols list page (mirrors the legacy /symbols HTML).

    Returns per-symbol rows with enrichment-derived columns plus active-position
    exposure, sorted by DGI quality score descending, and aggregate totals.
    """
    symbols = cosmos.list_symbols() if cosmos else []
    rows = []
    total_call_exposure = 0.0
    total_put_exposure = 0.0
    enrichment_ts = ""
    for s in symbols:
        enr = s.get("enrichment") or {}
        metrics = enr.get("metrics") or {}
        technicals = enr.get("technicals") or {}
        active = [p for p in s.get("positions", []) if p.get("status") == "active"]
        in_calls = sum(100 for p in active if p.get("type") == "call")
        put_exposure = sum(
            float(p.get("strike", 0)) * 100 for p in active if p.get("type") == "put"
        )
        call_exposure = sum(
            float(p.get("strike", 0)) * 100 for p in active if p.get("type") == "call"
        )
        total_call_exposure += call_exposure
        total_put_exposure += put_exposure
        last_updated = enr.get("last_updated", "") or ""
        if last_updated > enrichment_ts:
            enrichment_ts = last_updated
        wl = s.get("watchlist") or {}
        rows.append({
            "symbol": s.get("symbol"),
            "display_name": s.get("display_name", ""),
            "category": enr.get("category", "") or "",
            "dgi_score": enr.get("quality_score"),
            "tech_timing": technicals.get("score"),
            "entry_tag": enr.get("entry_tag", "") or "",
            "momentum": enr.get("momentum", "") or "",
            "price": metrics.get("current_price"),
            "total_shares": s.get("total_shares", 0) or 0,
            "active_count": len(active),
            "in_calls": in_calls,
            "put_exposure": put_exposure,
            "call_exposure": call_exposure,
            "watchlist": {
                "covered_call": bool(wl.get("covered_call", False)),
                "cash_secured_put": bool(wl.get("cash_secured_put", False)),
                "buy_tracker": bool(wl.get("buy_tracker", False)),
            },
        })
    rows.sort(
        key=lambda r: r["dgi_score"] if r["dgi_score"] is not None else -1,
        reverse=True,
    )
    return {
        "rows": rows,
        "symbol_count": len(rows),
        "total_call_exposure": total_call_exposure,
        "total_put_exposure": total_put_exposure,
        "last_update_ts": enrichment_ts,
    }


@app.get("/api/symbols/overview")
async def api_symbols_overview(request: Request):
    try:
        cosmos = _get_cosmos(request)
        return JSONResponse(_compute_symbols_overview(cosmos))
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/economics")
async def api_economics(request: Request,
                        year: Optional[int] = Query(default=None),
                        month: Optional[str] = Query(default=None),
                        symbol: Optional[str] = Query(default=None),
                        option_type: Optional[str] = Query(default=None, alias="type"),
                        status: Optional[str] = Query(default=None)):
    try:
        cosmos = _get_cosmos(request)
        # Support comma-separated symbols (e.g., ?symbol=MSFT,AAPL)
        symbol_list = None
        if symbol:
            symbol_list = [s.strip().upper() for s in symbol.split(",") if s.strip()]
            if not symbol_list:
                symbol_list = None
        # Support comma-separated months (e.g., ?month=1,2,3)
        month_list = None
        if month:
            try:
                month_list = [int(m.strip()) for m in month.split(",") if m.strip()]
                if not month_list:
                    month_list = None
            except ValueError:
                month_list = None
        normalized_type = option_type.strip().lower() if option_type else None
        normalized_status = status.strip().lower() if status else None

        if normalized_type and normalized_type not in {"call", "put"}:
            return JSONResponse(
                {"error": "type must be 'call' or 'put'"},
                status_code=400,
            )
        if normalized_status and normalized_status not in {"active", "closed", "rolled"}:
            return JSONResponse(
                {"error": "status must be 'active', 'closed', or 'rolled'"},
                status_code=400,
            )

        get_all_symbols = getattr(cosmos, "get_all_symbols", None)
        symbol_docs = (
            get_all_symbols()
            if callable(get_all_symbols)
            else cosmos.list_symbols()
        )
        return JSONResponse(
            _build_economics_report(
                symbol_docs,
                year=year,
                month_filter=month_list,
                symbol_filter=symbol_list,
                option_type=normalized_type,
                status_filter=normalized_status,
            )
        )
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/symbols")
async def api_create_symbol(request: Request):
    try:
        cosmos = _get_cosmos(request)
        body = await request.json()
        symbol = body.get("symbol", "").strip().upper()
        exchange = body.get("exchange", "").strip().upper()
        display_name = body.get("display_name", "").strip()
        if not display_name:
            display_name = f"{exchange}:{symbol}"
        covered_call = bool(body.get("covered_call", False))
        cash_secured_put = bool(body.get("cash_secured_put", False))
        buy_tracker = bool(body.get("buy_tracker", False))

        if not symbol or not exchange:
            return JSONResponse({"error": "symbol and exchange are required"},
                                status_code=400)

        existing = cosmos.get_symbol(symbol)
        if existing:
            return JSONResponse({"error": f"Symbol {symbol} already exists"},
                                status_code=409)

        doc = cosmos.create_symbol(symbol, exchange, display_name,
                                   covered_call, cash_secured_put, buy_tracker)

        # Enrich the new symbol in background (non-blocking)
        import threading
        def _enrich():
            try:
                from src.portfolio_enrichment import enrich_symbol
                enrichment = enrich_symbol(symbol)
                if enrichment:
                    cosmos.update_symbol_enrichment(symbol, enrichment)
                    cosmos.record_enrichment_snapshot(
                        symbol,
                        (enrichment.get("technicals") or {}).get("score"),
                        enrichment.get("momentum", ""),
                    )
            except Exception:
                pass
        threading.Thread(target=_enrich, daemon=True).start()

        # Seed the deterministic price-forecast history (last ~45 sessions ≈ 60
        # calendar days) so the forecast table/chart are populated from day one
        # instead of waiting for the daily cron to accumulate them. Enough depth
        # for the 40-session long trend window and to resolve 4w endpoints.
        # Point-in-time, no look-ahead, no LLM.
        yf_provider = getattr(request.app.state, "yf_provider", None)
        def _seed_forecasts():
            try:
                from src.forecast_cron import (
                    DEFAULT_BACKFILL_SESSIONS,
                    backfill_symbol_forecasts,
                )
                backfill = backfill_symbol_forecasts(
                    cosmos, yf_provider, symbol,
                    sessions=DEFAULT_BACKFILL_SESSIONS,
                )
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    result = asyncio.run(backfill)
                    logger.info("Forecast backfill completed for %s: %s", symbol, result)
                else:
                    task = loop.create_task(backfill)

                    def _log_backfill_result(completed):
                        try:
                            logger.info(
                                "Forecast backfill completed for %s: %s",
                                symbol,
                                completed.result(),
                            )
                        except Exception as exc:
                            logger.warning(
                                "Forecast backfill failed for newly created symbol %s: %s",
                                symbol,
                                exc,
                                exc_info=True,
                            )

                    task.add_done_callback(_log_backfill_result)
            except Exception as exc:
                logger.warning(
                    "Forecast backfill failed for newly created symbol %s: %s",
                    symbol,
                    exc,
                    exc_info=True,
                )
        threading.Thread(target=_seed_forecasts, daemon=True).start()

        return JSONResponse(_clean_doc(doc), status_code=201)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/symbols/{symbol}")
async def api_get_symbol(request: Request, symbol: str):
    try:
        cosmos = _get_cosmos(request)
        doc = cosmos.get_symbol(symbol.upper())
        if not doc:
            return JSONResponse({"error": f"Symbol {symbol} not found"},
                                status_code=404)
        return JSONResponse(_clean_doc(doc))
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


def _compute_symbol_detail(cosmos, symbol: str) -> Optional[dict]:
    """View-model for the Symbol detail page (mirrors the legacy HTML route).

    Returns the cleaned symbol doc plus a unified recent activity/alert feed,
    monitor-enriched active positions, action plans, watchlist toggles, summary
    exposure stats, next earnings date and paused state. Returns None if the
    symbol does not exist.
    """
    sym = symbol.upper()
    doc = cosmos.get_symbol(sym)
    if not doc:
        return None

    plans = _sort_by_updated_at_desc(cosmos.get_plans(sym))

    # Unified recent activity + alert feed across all agent types.
    activities: List[Dict] = []
    for agent_type, meta in AGENT_TYPES.items():
        acts = cosmos.get_recent_activities(sym, agent_type, max_entries=50)
        for a in acts:
            a["_agent_key"] = str(a.get("agent_type", ""))
            a["_agent_label"] = meta["label"]
        activities.extend(acts)
        alts = cosmos.get_recent_alerts(sym, agent_type, max_entries=30)
        for a in alts:
            a["_agent_key"] = str(a.get("agent_type", ""))
            a["_agent_label"] = meta["label"]
        activities.extend(alts)
    activities.sort(key=lambda d: d.get("timestamp", ""), reverse=True)
    activities = activities[:80]

    # Latest monitor activity per position (assignment risk / moneyness).
    _monitor_agents = {"open_call_monitor", "open_put_monitor"}
    latest_monitor: Dict[str, Dict] = {}
    for act in activities:
        pid = act.get("position_id")
        if pid and act.get("agent_type") in _monitor_agents and pid not in latest_monitor:
            latest_monitor[pid] = act

    positions = []
    for pos in doc.get("positions", []):
        p = dict(pos)
        mon = latest_monitor.get(pos.get("position_id"))
        if mon:
            p["assignment_risk"] = mon.get("assignment_risk")
            p["moneyness"] = mon.get("moneyness")
        source = pos.get("source")
        if not isinstance(source, dict):
            source = {}
        p["display_premium"] = _parse_numeric(source.get("premium"))
        p["display_buyback"] = _parse_numeric(pos.get("buyback_cost"))
        positions.append(p)

    active_positions = [p for p in doc.get("positions", []) if p.get("status") == "active"]
    summary_in_calls = sum(100 for p in active_positions if p.get("type") == "call")
    summary_put_exposure = sum(
        float(p.get("strike", 0)) * 100 for p in active_positions if p.get("type") == "put"
    )
    summary_call_exposure = sum(
        float(p.get("strike", 0)) * 100 for p in active_positions if p.get("type") == "call"
    )

    watchlist = doc.get("watchlist") or {}
    enr = doc.get("enrichment") or {}

    clean = _clean_doc(doc)
    clean["positions"] = [
        {k: v for k, v in p.items() if k not in _COSMOS_SYSTEM_KEYS} for p in positions
    ]
    return {
        "symbol": clean.get("symbol", sym),
        "display_name": clean.get("display_name", ""),
        "exchange": clean.get("exchange", ""),
        "total_shares": clean.get("total_shares", 0) or 0,
        "watchlist": {
            "covered_call": bool(watchlist.get("covered_call", False)),
            "cash_secured_put": bool(watchlist.get("cash_secured_put", False)),
            "buy_tracker": bool(watchlist.get("buy_tracker", False)),
        },
        "telegram_notifications_enabled": bool(
            clean.get("telegram_notifications_enabled", False)
        ),
        "enrichment": enr,
        "positions": clean["positions"],
        "activities": [_clean_doc(a) for a in activities],
        "agent_types": [
            {"key": k, "label": m["label"]} for k, m in AGENT_TYPES.items()
        ],
        "plans": [_clean_doc(p) for p in plans],
        "summary": {
            "in_calls": summary_in_calls,
            "put_exposure": summary_put_exposure,
            "call_exposure": summary_call_exposure,
            "active_count": len(active_positions),
        },
        "next_earnings_date": cosmos.get_next_earnings_date(sym),
        "is_paused": is_watchlist_paused(doc),
    }


@app.get("/api/symbols/{symbol}/detail")
async def api_symbol_detail(request: Request, symbol: str):
    try:
        cosmos = _get_cosmos(request)
        data = _compute_symbol_detail(cosmos, symbol)
        if data is None:
            return JSONResponse({"error": f"Symbol {symbol} not found"},
                                status_code=404)
        return JSONResponse(data)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.put("/api/symbols/{symbol}")
async def api_update_symbol(request: Request, symbol: str):
    try:
        cosmos = _get_cosmos(request)
        doc = cosmos.get_symbol(symbol.upper())
        if not doc:
            return JSONResponse({"error": f"Symbol {symbol} not found"},
                                status_code=404)

        body = await request.json()
        update_fields = {
            "display_name", "covered_call", "cash_secured_put", "buy_tracker",
            "exchange", "telegram_notifications_enabled", "total_shares",
        }
        if not any(field in body for field in update_fields):
            return JSONResponse(
                {"error": "total_shares or another supported update field is required"},
                status_code=400,
            )
        if "display_name" in body:
            doc["display_name"] = body["display_name"]
        if "covered_call" in body:
            doc["watchlist"]["covered_call"] = bool(body["covered_call"])
        if "cash_secured_put" in body:
            doc["watchlist"]["cash_secured_put"] = bool(body["cash_secured_put"])
        if "buy_tracker" in body:
            doc.setdefault("watchlist", {})["buy_tracker"] = bool(body["buy_tracker"])
        if "exchange" in body:
            doc["exchange"] = body["exchange"].strip().upper()
        if "telegram_notifications_enabled" in body:
            doc["telegram_notifications_enabled"] = bool(body["telegram_notifications_enabled"])
        if "total_shares" in body:
            total_shares = body["total_shares"]
            if type(total_shares) is not int:
                return JSONResponse(
                    {"error": "total_shares must be a non-negative integer"},
                    status_code=400,
                )
            if total_shares < 0:
                return JSONResponse(
                    {"error": "total_shares must be a non-negative integer"},
                    status_code=400,
                )
            doc["total_shares"] = total_shares

        doc["updated_at"] = datetime.utcnow().isoformat() + "Z"
        updated = cosmos.replace_symbol(doc)

        # Activities are kept when watchlist agents are toggled OFF.
        # CosmosDB TTL (30 days) handles cleanup automatically.

        return JSONResponse(_clean_doc(updated))
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/symbols/{symbol}/pause")
async def api_pause_symbol_watchlist(request: Request, symbol: str):
    try:
        cosmos = _get_cosmos(request)
        symbol = symbol.upper()
        doc = cosmos.get_symbol(symbol)
        if not doc:
            return JSONResponse({"error": f"Symbol {symbol} not found"},
                                status_code=404)

        body = {}
        raw_body = await request.body()
        if raw_body:
            try:
                body = json.loads(raw_body)
            except json.JSONDecodeError:
                return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

        until = body.get("until") if isinstance(body, dict) else None
        if until:
            try:
                datetime.strptime(until, "%Y-%m-%d")
            except ValueError:
                return JSONResponse({"error": "Invalid until date; expected YYYY-MM-DD"},
                                    status_code=400)
        else:
            until = cosmos.get_next_earnings_date(symbol)
            if not until:
                return JSONResponse({
                    "error": "No upcoming earnings date found for this symbol. Sync the calendar first."
                }, status_code=400)

        updated = cosmos.set_watchlist_pause(
            symbol,
            until,
            ["covered_call", "cash_secured_put", "buy_tracker"],
        )
        return JSONResponse(_clean_doc(updated))
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/symbols/{symbol}/pause")
async def api_clear_symbol_watchlist_pause(request: Request, symbol: str):
    try:
        cosmos = _get_cosmos(request)
        symbol = symbol.upper()
        doc = cosmos.get_symbol(symbol)
        if not doc:
            return JSONResponse({"error": f"Symbol {symbol} not found"},
                                status_code=404)
        updated = cosmos.clear_watchlist_pause(symbol)
        return JSONResponse(_clean_doc(updated))
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/symbols/{symbol}")
async def api_delete_symbol(request: Request, symbol: str):
    try:
        cosmos = _get_cosmos(request)
        doc = cosmos.get_symbol(symbol.upper())
        if not doc:
            return JSONResponse({"error": f"Symbol {symbol} not found"},
                                status_code=404)
        cosmos.delete_symbol(symbol.upper())
        return JSONResponse({"status": "deleted", "symbol": symbol.upper()})
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ===========================================================================
# REST API — Position Management
# ===========================================================================

@app.post("/api/symbols/{symbol}/positions")
async def api_add_position(request: Request, symbol: str):
    try:
        cosmos = _get_cosmos(request)
        body = await request.json()
        position_type = body.get("type", "").strip().lower()
        strike = body.get("strike")
        expiration = body.get("expiration", "").strip()
        notes = body.get("notes", "").strip()
        source_activity_id = body.get("source_activity_id", "").strip() if body.get("source_activity_id") else ""

        if position_type not in ("call", "put"):
            return JSONResponse({"error": "type must be 'call' or 'put'"},
                                status_code=400)
        if not strike or not expiration:
            return JSONResponse({"error": "strike and expiration are required"},
                                status_code=400)
        try:
            strike = float(strike)
        except (TypeError, ValueError):
            return JSONResponse({"error": "strike must be a number"},
                                status_code=400)

        source = None
        if source_activity_id:
            activity = cosmos.get_activity_by_id(source_activity_id)
            if activity is not None:
                source = {
                    "source_type": "manual_with_alert",
                    "activity_id": activity["id"],
                    "agent_type": activity.get("agent_type"),
                    "activity": activity.get("activity"),
                    "confidence": activity.get("confidence"),
                    "reason": activity.get("reason"),
                    "underlying_price": activity.get("underlying_price"),
                    "premium": activity.get("premium"),
                    "iv": activity.get("iv"),
                    "risk_flags": activity.get("risk_flags", []),
                    "timestamp": activity.get("timestamp"),
                }

        # If premium provided manually, ensure it's stored in source
        premium = body.get("premium")
        if premium is not None:
            try:
                premium = float(premium)
            except (TypeError, ValueError):
                premium = None
            if premium is not None:
                if source is None:
                    source = {"source_type": "manual", "premium": premium}
                else:
                    source["premium"] = premium

        doc = cosmos.add_position(symbol.upper(), position_type, strike,
                                  expiration, notes, source=source)
        return JSONResponse(_clean_doc(doc), status_code=201)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/symbols/{symbol}/positions/from-activity/{activity_id}")
async def api_add_position_from_activity(request: Request, symbol: str,
                                         activity_id: str):
    """Create a position from an existing activity and disable watchlist.
    Activities are preserved (CosmosDB TTL handles cleanup)."""
    try:
        cosmos = _get_cosmos(request)
        activity = cosmos.get_activity_by_id(activity_id)
        if activity is None:
            return JSONResponse({"error": f"Activity {activity_id} not found"},
                                status_code=404)

        strike = activity.get("strike")
        expiration = activity.get("expiration")
        agent_type = activity.get("agent_type")

        if not strike or not expiration or not agent_type:
            return JSONResponse(
                {"error": "Activity missing required fields (strike, expiration, agent_type)"},
                status_code=400,
            )

        agent_type_map = {"covered_call": "call", "cash_secured_put": "put"}
        position_type = agent_type_map.get(agent_type)
        if position_type is None:
            return JSONResponse(
                {"error": f"Unsupported agent_type '{agent_type}'"},
                status_code=400,
            )

        source = {
            "activity_id": activity["id"],
            "agent_type": activity.get("agent_type"),
            "activity": activity.get("activity"),
            "confidence": activity.get("confidence"),
            "reason": activity.get("reason"),
            "underlying_price": activity.get("underlying_price"),
            "premium": activity.get("premium"),
            "iv": activity.get("iv"),
            "risk_flags": activity.get("risk_flags", []),
            "timestamp": activity.get("timestamp"),
        }

        doc = cosmos.add_position(
            symbol.upper(), position_type, float(strike),
            expiration, notes="", source=source,
        )

        # Disable the watchlist for this agent type
        sym_doc = cosmos.get_symbol(symbol.upper())
        if agent_type in ("covered_call", "cash_secured_put"):
            sym_doc["watchlist"][agent_type] = False
            sym_doc["updated_at"] = datetime.utcnow().isoformat() + "Z"
            cosmos.replace_symbol(sym_doc)

        return JSONResponse(_clean_doc(doc), status_code=201)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/symbols/{symbol}/positions/roll-from-activity/{activity_id}")
async def api_roll_position_from_activity(request: Request, symbol: str,
                                          activity_id: str):
    """Roll a position from a monitor-agent activity: close old + open new."""
    try:
        cosmos = _get_cosmos(request)
        activity = cosmos.get_activity_by_id(activity_id)
        if activity is None:
            return JSONResponse({"error": f"Activity {activity_id} not found"},
                                status_code=404)

        strike = (activity.get("strike")
                  or activity.get("new_strike")
                  or activity.get("current_strike"))
        expiration = (activity.get("expiration")
                      or activity.get("new_expiration")
                      or activity.get("current_expiration"))
        agent_type = activity.get("agent_type")
        position_id = activity.get("position_id")

        if not strike or not expiration or not agent_type or not position_id:
            return JSONResponse(
                {"error": "Activity missing required fields (strike, expiration, agent_type, position_id)"},
                status_code=400,
            )

        monitor_type_map = {"open_call_monitor": "call", "open_put_monitor": "put"}
        position_type = monitor_type_map.get(agent_type)
        if position_type is None:
            return JSONResponse(
                {"error": f"Unsupported monitor agent_type '{agent_type}'"},
                status_code=400,
            )

        snapshot = {
            "activity_id": activity["id"],
            "agent_type": activity.get("agent_type"),
            "activity": activity.get("activity"),
            "confidence": activity.get("confidence"),
            "reason": activity.get("reason"),
            "underlying_price": activity.get("underlying_price"),
            "premium": activity.get("premium"),
            "iv": activity.get("iv"),
            "risk_flags": activity.get("risk_flags", []),
            "timestamp": activity.get("timestamp"),
        }

        doc = cosmos.roll_position(
            symbol.upper(), position_id, position_type,
            float(strike), expiration,
            source=snapshot, closing_source=snapshot,
        )

        return JSONResponse(_clean_doc(doc), status_code=201)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/symbols/{symbol}/positions/{position_id}/roll")
async def api_manual_roll_position(request: Request, symbol: str,
                                   position_id: str):
    """Manually roll a position to a new strike/expiration, optionally attaching alert data."""
    try:
        cosmos = _get_cosmos(request)
        body = await request.json()

        new_strike = body.get("new_strike")
        new_expiration = body.get("new_expiration")
        if new_strike is None or not new_expiration:
            return JSONResponse(
                {"error": "new_strike and new_expiration are required"},
                status_code=400,
            )

        # Determine position type from existing position
        sym_doc = cosmos.get_symbol(symbol.upper())
        if sym_doc is None:
            return JSONResponse({"error": f"Symbol {symbol} not found"},
                                status_code=404)
        pos = None
        for p in sym_doc.get("positions", []):
            if p["position_id"] == position_id:
                pos = p
                break
        if pos is None:
            return JSONResponse(
                {"error": f"Position {position_id} not found"},
                status_code=404,
            )

        notes = body.get("notes", "")
        source_activity_id = body.get("source_activity_id", "").strip() if body.get("source_activity_id") else ""

        # Build source from activity if provided
        source = None
        if source_activity_id:
            activity = cosmos.get_activity_by_id(source_activity_id)
            if activity is not None:
                source = {
                    "source_type": "manual_with_alert",
                    "activity_id": activity["id"],
                    "agent_type": activity.get("agent_type"),
                    "activity": activity.get("activity"),
                    "confidence": activity.get("confidence"),
                    "reason": activity.get("reason"),
                    "underlying_price": activity.get("underlying_price"),
                    "premium": activity.get("premium"),
                    "iv": activity.get("iv"),
                    "risk_flags": activity.get("risk_flags", []),
                    "timestamp": activity.get("timestamp"),
                }

        # If premium provided manually for the new position
        premium = body.get("premium")
        if premium is not None:
            try:
                premium = float(premium)
            except (TypeError, ValueError):
                premium = None
            if premium is not None:
                if source is None:
                    source = {"source_type": "manual", "premium": premium}
                else:
                    source["premium"] = premium

        # Buyback cost for the old (closed) position
        buyback_cost = body.get("buyback_cost")
        if buyback_cost is not None:
            try:
                buyback_cost = float(buyback_cost)
            except (TypeError, ValueError):
                buyback_cost = None

        doc = cosmos.roll_position(
            symbol.upper(), position_id, pos["type"],
            float(new_strike), new_expiration,
            source=source,
            notes=notes,
        )

        # Set buyback_cost on the old (now closed) position
        if buyback_cost is not None:
            for p in doc.get("positions", []):
                if p["position_id"] == position_id:
                    p["buyback_cost"] = buyback_cost
                    break
            doc["updated_at"] = datetime.utcnow().isoformat() + "Z"
            doc = cosmos.replace_symbol(doc)

        return JSONResponse(_clean_doc(doc), status_code=201)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.put("/api/symbols/{symbol}/positions/{position_id}/close")
async def api_close_position(request: Request, symbol: str, position_id: str):
    try:
        cosmos = _get_cosmos(request)
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        close_reason = body.get("close_reason", "manual")
        buyback_cost = body.get("buyback_cost")
        if buyback_cost is not None:
            try:
                buyback_cost = float(buyback_cost)
            except (TypeError, ValueError):
                buyback_cost = None
        if close_reason != "manual":
            buyback_cost = None
        doc = cosmos.close_position(
            symbol.upper(), position_id, close_reason=close_reason,
            buyback_cost=buyback_cost,
        )
        return JSONResponse(_clean_doc(doc))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.patch("/api/symbols/{symbol}/positions/{position_id}/notes")
async def api_update_position_notes(request: Request, symbol: str,
                                    position_id: str):
    """Update notes on a position."""
    try:
        cosmos = _get_cosmos(request)
        body = await request.json()
        notes = body.get("notes", "")
        if not isinstance(notes, str):
            return JSONResponse({"error": "notes must be a string"},
                                status_code=400)
        doc = cosmos.update_position_notes(symbol.upper(), position_id, notes)
        return JSONResponse(_clean_doc(doc))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.patch("/api/symbols/{symbol}/positions/{position_id}/premium")
async def api_update_position_premium(request: Request, symbol: str,
                                      position_id: str):
    """Update premium on a position."""
    try:
        cosmos = _get_cosmos(request)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "JSON body must be an object"},
                                status_code=400)
        premium = body.get("premium")
        if premium is None:
            return JSONResponse({"error": "premium is required"},
                                status_code=400)
        premium = _parse_non_negative_number(premium)
        if premium is None:
            return JSONResponse(
                {"error": "premium must be a finite, non-negative number"},
                                status_code=400)
        doc = cosmos.update_position_premium(symbol.upper(), position_id, premium)
        return JSONResponse(_clean_doc(doc))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.patch("/api/symbols/{symbol}/positions/{position_id}/buyback_cost")
async def api_update_position_buyback_cost(request: Request, symbol: str,
                                           position_id: str):
    """Update buyback cost on a rolled position."""
    try:
        cosmos = _get_cosmos(request)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "JSON body must be an object"},
                                status_code=400)
        buyback_cost = body.get("buyback_cost")
        if buyback_cost is None:
            return JSONResponse({"error": "buyback_cost is required"},
                                status_code=400)
        buyback_cost = _parse_non_negative_number(buyback_cost)
        if buyback_cost is None:
            return JSONResponse(
                {"error": "buyback_cost must be a finite, non-negative number"},
                                status_code=400)
        doc = cosmos.update_position_buyback_cost(symbol.upper(), position_id,
                                                  buyback_cost)
        return JSONResponse(_clean_doc(doc))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/symbols/{symbol}/positions/{position_id}")
async def api_delete_position(request: Request, symbol: str, position_id: str):
    try:
        cosmos = _get_cosmos(request)
        doc = cosmos.delete_position(symbol.upper(), position_id)
        return JSONResponse(_clean_doc(doc))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/symbols/{symbol}/positions/{position_id}/snapshots")
async def api_position_snapshots(request: Request, symbol: str, position_id: str,
                                 limit: int = Query(default=100, le=500)):
    """Return time-series snapshots for a position (for charting)."""
    try:
        cosmos = _get_cosmos(request)
        snapshots = cosmos.get_position_snapshots(symbol.upper(), position_id,
                                                  limit=limit)
        snapshots.reverse()
        return JSONResponse({"snapshots": snapshots})
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/symbols/{symbol}/positions/{position_id}/dps-analysis")
async def api_dps_analysis(request: Request, symbol: str, position_id: str):
    """Run deterministic position scoring (DPS) for an open position."""
    try:
        cosmos = _get_cosmos(request)
        symbol = symbol.upper()

        # Find the position
        sym_doc = cosmos.get_symbol(symbol)
        if not sym_doc:
            return JSONResponse({"error": f"Symbol {symbol} not found"}, status_code=404)

        position = None
        for pos in sym_doc.get("positions", []):
            if pos.get("position_id") == position_id:
                position = pos
                break
        if position is None:
            return JSONResponse({"error": f"Position {position_id} not found"}, status_code=404)

        strike = float(position["strike"])
        expiration = position["expiration"]
        option_type = position.get("type", "call")

        # Get snapshots (oldest first)
        snapshots = cosmos.get_position_snapshots(symbol, position_id, limit=20)
        snapshots.reverse()

        # Fetch options chain from centralized cache
        from src.options_chain_cache import get_options_chain_cache
        chain_cache = get_options_chain_cache()
        chain_json = await chain_cache.get_or_load_async(symbol)

        # Get current price from yf_provider (overview data)
        yf_provider = getattr(request.app.state, "yf_provider", None)
        underlying_price = None
        if yf_provider is not None:
            import json as _json
            data = await yf_provider.fetch_all(symbol)
            overview = data.get("overview", "{}")
            if isinstance(overview, str):
                try:
                    overview = _json.loads(overview)
                except (ValueError, TypeError):
                    overview = {}
            fundamentals = overview.get("fundamentals", {})
            price_field = fundamentals.get("current_price", {})
            underlying_price = price_field.get("value") if isinstance(price_field, dict) else price_field
            if underlying_price is not None:
                underlying_price = float(underlying_price)

        # Run DPS
        from src.dps_scorer import run_dps_analysis
        _source = position.get("source") or {}
        _premium = None
        try:
            _premium = float(_source.get("premium") or _source.get("new_premium") or 0) or None
        except (TypeError, ValueError):
            pass
        result = run_dps_analysis(
            symbol=symbol,
            strike=strike,
            expiration=expiration,
            option_type=option_type,
            chain_json=chain_json,
            snapshots=snapshots,
            underlying_price=underlying_price,
            premium_received=_premium,
        )

        return JSONResponse(result)

    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        logger.exception("DPS analysis failed for %s/%s", symbol, position_id)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/symbols/{symbol}/positions/{position_id}/dps-insights")
async def api_dps_insights(request: Request, symbol: str, position_id: str):
    """Generate LLM narrative summary of position's DPS health (one-shot)."""
    try:
        cosmos = _get_cosmos(request)
        symbol = symbol.upper()

        # Find the position
        sym_doc = cosmos.get_symbol(symbol)
        if not sym_doc:
            return JSONResponse({"error": f"Symbol {symbol} not found"}, status_code=404)

        position = None
        for pos in sym_doc.get("positions", []):
            if pos.get("position_id") == position_id:
                position = pos
                break
        if position is None:
            return JSONResponse({"error": f"Position {position_id} not found"}, status_code=404)

        # Get snapshots (oldest first)
        snapshots = cosmos.get_position_snapshots(symbol, position_id, limit=30)
        snapshots.reverse()

        # Build LLM message with exact headers
        message = f"""=== POSITION ===
{json.dumps(position, indent=2, default=str)}

=== DPS SNAPSHOT HISTORY (oldest first) ===
{json.dumps(snapshots, indent=2, default=str)}

Summarize this position's DPS: current state, trend, notable history, and likely short-term outlook."""

        # Call LLM via Agent Framework
        from agent_framework import Agent
        from src.llm import create_async_chat_client
        from src.dps_interpret_instructions import get_dps_interpret_instructions
        from src.config import Config

        cfg = Config()
        model = cfg.dps_insights_model
        client = create_async_chat_client(
            model, _function_llm_config(cfg, "dps_insights")
        )
        agent = Agent(
            client=client,
            name="DPSInsights",
            instructions=get_dps_interpret_instructions()
        )

        result = await agent.run(message)
        insights = result.text or str(result)

        return JSONResponse({"insights": insights})

    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        logger.exception("DPS insights failed for %s/%s", symbol, position_id)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/symbols/{symbol}/positions/{position_id}/roll-table")
async def api_roll_table(request: Request, symbol: str, position_id: str):
    """Compute roll scenarios table for an open position (calls and puts)."""
    try:
        cosmos = _get_cosmos(request)
        symbol = symbol.upper()

        sym_doc = cosmos.get_symbol(symbol)
        if not sym_doc:
            return JSONResponse({"error": f"Symbol {symbol} not found"}, status_code=404)

        position = None
        for pos in sym_doc.get("positions", []):
            if pos.get("position_id") == position_id:
                position = pos
                break
        if position is None:
            return JSONResponse({"error": f"Position {position_id} not found"}, status_code=404)

        strike = float(position["strike"])
        expiration = position["expiration"]
        option_type = position.get("type", "call")

        _source = position.get("source") or {}
        premium = None
        try:
            premium = float(_source.get("premium") or _source.get("new_premium") or 0) or None
        except (TypeError, ValueError):
            pass

        # Get current price from yf_provider (overview data) — same block as api_dps_analysis
        yf_provider = getattr(request.app.state, "yf_provider", None)
        underlying_price = None
        if yf_provider is not None:
            import json as _json
            data = await yf_provider.fetch_all(symbol)
            overview = data.get("overview", "{}")
            if isinstance(overview, str):
                try:
                    overview = _json.loads(overview)
                except (ValueError, TypeError):
                    overview = {}
            fundamentals = overview.get("fundamentals", {})
            price_field = fundamentals.get("current_price", {})
            underlying_price = price_field.get("value") if isinstance(price_field, dict) else price_field
            if underlying_price is not None:
                underlying_price = float(underlying_price)

        if not underlying_price:
            return JSONResponse({"error": "Underlying price unavailable"}, status_code=503)

        # Fetch options chain from centralized cache
        from src.options_chain_cache import get_options_chain_cache
        chain_json = await get_options_chain_cache().get_or_load_async(symbol)

        # Compute roll table
        from src.roll_table import compute_roll_table
        result = compute_roll_table(
            chain=chain_json,
            current_strike=strike,
            current_expiration=expiration,
            option_type=option_type,
            underlying_price=underlying_price,
            premium_received=premium,
            strike_offsets=(0.03, 0.0, -0.03),
        )

        return JSONResponse(result)

    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        logger.exception("Roll table failed for %s/%s", symbol, position_id)
        return JSONResponse({"error": str(e)}, status_code=500)


# ===========================================================================
# REST API — Action Plans
# ===========================================================================

_PLAN_TYPES = {"sell_put", "sell_call", "buy_shares", "sell_shares", "roll", "close", "other"}
_PLAN_STATUSES = {"planned", "active", "completed", "cancelled"}
_PLAN_PRIORITIES = {"high", "medium", "low"}


@app.get("/api/plans")
async def api_list_plans(request: Request,
                         status: Optional[str] = Query(default=None),
                         symbol: Optional[str] = Query(default=None)):
    try:
        cosmos = _get_cosmos(request)
        normalized_status = status.strip().lower() if status else None
        normalized_symbol = symbol.strip().upper() if symbol else None

        if normalized_status and normalized_status not in _PLAN_STATUSES:
            return JSONResponse(
                {"error": "status must be 'planned', 'active', 'completed', or 'cancelled'"},
                status_code=400,
            )

        plans = cosmos.get_plans(symbol=normalized_symbol, status=normalized_status)
        return JSONResponse([_clean_doc(plan) for plan in plans])
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/symbols/{symbol}/plans")
async def api_list_symbol_plans(request: Request, symbol: str):
    try:
        cosmos = _get_cosmos(request)
        symbol = symbol.upper()
        if not cosmos.get_symbol(symbol):
            return JSONResponse({"error": f"Symbol {symbol} not found"}, status_code=404)
        plans = cosmos.get_plans(symbol=symbol)
        return JSONResponse([_clean_doc(plan) for plan in plans])
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/symbols/{symbol}/plans")
async def api_create_plan(request: Request, symbol: str):
    try:
        cosmos = _get_cosmos(request)
        symbol = symbol.upper()
        if not cosmos.get_symbol(symbol):
            return JSONResponse({"error": f"Symbol {symbol} not found"}, status_code=404)

        body = await request.json()
        title = body.get("title", "")
        objective = body.get("objective", "")
        conditions = body.get("conditions", "")
        plan_type = str(body.get("plan_type", "other")).strip().lower()
        status = str(body.get("status", "planned")).strip().lower()
        priority = str(body.get("priority", "medium")).strip().lower()

        if not isinstance(title, str) or not title.strip():
            return JSONResponse({"error": "title is required"}, status_code=400)
        if objective is not None and not isinstance(objective, str):
            return JSONResponse({"error": "objective must be a string"}, status_code=400)
        if conditions is not None and not isinstance(conditions, str):
            return JSONResponse({"error": "conditions must be a string"}, status_code=400)
        if plan_type not in _PLAN_TYPES:
            return JSONResponse(
                {"error": "plan_type must be 'sell_put', 'sell_call', 'buy_shares', 'sell_shares', 'roll', 'close', or 'other'"},
                status_code=400,
            )
        if status not in _PLAN_STATUSES:
            return JSONResponse(
                {"error": "status must be 'planned', 'active', 'completed', or 'cancelled'"},
                status_code=400,
            )
        if priority not in _PLAN_PRIORITIES:
            return JSONResponse(
                {"error": "priority must be 'high', 'medium', or 'low'"},
                status_code=400,
            )

        doc = cosmos.create_plan(symbol, {
            "title": title.strip(),
            "objective": objective.strip() if isinstance(objective, str) else "",
            "plan_type": plan_type,
            "status": status,
            "priority": priority,
            "conditions": conditions.strip() if isinstance(conditions, str) else "",
            "agent_notes": body.get("agent_notes", []) if isinstance(body.get("agent_notes"), list) else [],
        })
        return JSONResponse(_clean_doc(doc), status_code=201)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/symbols/{symbol}/plans/{plan_id}")
async def api_get_plan(request: Request, symbol: str, plan_id: str):
    try:
        cosmos = _get_cosmos(request)
        doc = cosmos.get_plan(symbol.upper(), plan_id)
        if not doc:
            return JSONResponse({"error": f"Plan {plan_id} not found"}, status_code=404)
        return JSONResponse(_clean_doc(doc))
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.put("/api/symbols/{symbol}/plans/{plan_id}")
async def api_update_plan(request: Request, symbol: str, plan_id: str):
    try:
        cosmos = _get_cosmos(request)
        body = await request.json()
        updates = {}

        if "title" in body:
            if not isinstance(body["title"], str) or not body["title"].strip():
                return JSONResponse({"error": "title must be a non-empty string"}, status_code=400)
            updates["title"] = body["title"].strip()
        if "objective" in body:
            if not isinstance(body["objective"], str):
                return JSONResponse({"error": "objective must be a string"}, status_code=400)
            updates["objective"] = body["objective"].strip()
        if "conditions" in body:
            if not isinstance(body["conditions"], str):
                return JSONResponse({"error": "conditions must be a string"}, status_code=400)
            updates["conditions"] = body["conditions"].strip()
        if "plan_type" in body:
            plan_type = str(body["plan_type"]).strip().lower()
            if plan_type not in _PLAN_TYPES:
                return JSONResponse(
                    {"error": "plan_type must be 'sell_put', 'sell_call', 'buy_shares', 'sell_shares', 'roll', 'close', or 'other'"},
                    status_code=400,
                )
            updates["plan_type"] = plan_type
        if "status" in body:
            status = str(body["status"]).strip().lower()
            if status not in _PLAN_STATUSES:
                return JSONResponse(
                    {"error": "status must be 'planned', 'active', 'completed', or 'cancelled'"},
                    status_code=400,
                )
            updates["status"] = status
        if "priority" in body:
            priority = str(body["priority"]).strip().lower()
            if priority not in _PLAN_PRIORITIES:
                return JSONResponse(
                    {"error": "priority must be 'high', 'medium', or 'low'"},
                    status_code=400,
                )
            updates["priority"] = priority
        if "agent_notes" in body:
            if not isinstance(body["agent_notes"], list):
                return JSONResponse({"error": "agent_notes must be a list"}, status_code=400)
            updates["agent_notes"] = body["agent_notes"]

        doc = cosmos.update_plan(symbol.upper(), plan_id, updates)
        return JSONResponse(_clean_doc(doc))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/symbols/{symbol}/plans/{plan_id}")
async def api_delete_plan(request: Request, symbol: str, plan_id: str):
    try:
        cosmos = _get_cosmos(request)
        cosmos.delete_plan(symbol.upper(), plan_id)
        return JSONResponse({"status": "deleted", "id": plan_id, "symbol": symbol.upper()})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/symbols/{symbol}/plans/{plan_id}/notes")
async def api_add_plan_note(request: Request, symbol: str, plan_id: str):
    try:
        cosmos = _get_cosmos(request)
        body = await request.json()
        note = body.get("note", "")
        if not isinstance(note, str) or not note.strip():
            return JSONResponse({"error": "note is required"}, status_code=400)
        doc = cosmos.add_plan_note(symbol.upper(), plan_id, note.strip())
        return JSONResponse(_clean_doc(doc))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ===========================================================================
# REST API — Data Views
# ===========================================================================

@app.get("/api/alerts")
async def api_alerts(request: Request, agent_type: str = None,
                     since: str = None, limit: int = 100):
    try:
        cosmos = _get_cosmos(request)
        results = cosmos.get_all_alerts(agent_type, since, limit)
        return JSONResponse([_clean_doc(r) for r in results])
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/activities")
async def api_activities(request: Request, agent_type: str = None,
                         symbol: str = None, since: str = None,
                         limit: int = 100):
    try:
        cosmos = _get_cosmos(request)
        if symbol:
            results = cosmos.get_symbol_activities(symbol.upper(), agent_type, since, limit)
        else:
            results = cosmos.get_all_activities(agent_type, since, limit)
        return JSONResponse([_clean_doc(r) for r in results])
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ===========================================================================
# Page Routes — Dashboard
# ===========================================================================

def _is_complete_triplet(strike, expiration, premium) -> bool:
    """Return True if strike/expiration/premium form a complete, valid contract triplet."""
    try:
        s = float(strike) if strike is not None else 0.0
    except (ValueError, TypeError):
        s = 0.0
    try:
        p = float(premium) if premium is not None else 0.0
    except (ValueError, TypeError):
        p = 0.0
    return s > 0 and bool(expiration) and p > 0


def _build_dashboard_tables(cosmos, all_symbols, all_alerts, all_activities):
    """Build per-agent table data for the dashboard from CosmosDB data."""
    agent_tables = []
    grand_totals = {"today": 0, "week": 0, "month": 0, "total": 0}
    sym_cfg_map = {s["symbol"]: s for s in all_symbols}

    for agent_key, agent_meta in AGENT_TYPES.items():
        is_pm = agent_meta["is_position_monitor"]
        agent_alerts = [s for s in all_alerts
                        if s.get("agent_type") == agent_key]

        groups: Dict[str, List[Dict]] = {}
        display_map: Dict[str, str] = {}

        # Seed rows from symbol configs so every watched symbol/position appears
        for sym_cfg in all_symbols:
            sym = sym_cfg["symbol"]
            if is_pm:
                ptype = "call" if agent_key == "open_call_monitor" else "put"
                for pos in sym_cfg.get("positions", []):
                    if pos.get("status") == "active" and pos["type"] == ptype:
                        key = f"{sym}_{pos['strike']}_{pos['expiration']}"
                        display_map[key] = (
                            f"{sym} ${pos['strike']} exp {pos['expiration']}"
                        )
                        groups.setdefault(key, [])
            else:
                wl = sym_cfg.get("watchlist", {})
                if ((agent_key == "covered_call" and wl.get("covered_call"))
                        or (agent_key == "cash_secured_put"
                            and wl.get("cash_secured_put"))
                        or (agent_key == "buy_tracker"
                            and wl.get("buy_tracker"))):
                    groups.setdefault(sym, [])
                    display_map.setdefault(
                        sym, sym_cfg.get("display_name", sym))

        # Layer alerts onto groups
        for alert in agent_alerts:
            sym = alert.get("symbol", "")
            if is_pm:
                strike = (alert.get("current_strike")
                          or alert.get("strike", ""))
                exp = (alert.get("current_expiration")
                       or alert.get("expiration", ""))
                key = f"{sym}_{strike}_{exp}" if strike and exp else sym
                if key not in display_map:
                    display_map[key] = (
                        f"{sym} ${strike} exp {exp}" if strike and exp
                        else sym
                    )
            else:
                key = sym
                if key not in groups:
                    continue
                display_map.setdefault(
                    key, sym_cfg_map.get(sym, {}).get("display_name", sym))
            groups.setdefault(key, []).append(alert)

        # Latest activity per key — for health metrics and risk flags
        # Filter out SKIPPED activities so we show meaningful data
        agent_acts = [d for d in all_activities
                      if d.get("agent_type") == agent_key
                      and d.get("activity", "").upper() != "SKIPPED"]
        latest_by_key: Dict[str, Dict] = {}
        recent_by_key: Dict[str, List[Dict]] = {}
        for d in agent_acts:
            sym = d.get("symbol", "")
            if is_pm:
                strike = (d.get("current_strike")
                          or d.get("strike", ""))
                exp = (d.get("current_expiration")
                       or d.get("expiration", ""))
                key = f"{sym}_{strike}_{exp}" if strike and exp else sym
            else:
                key = sym
            prev = latest_by_key.get(key)
            if (prev is None
                    or d.get("timestamp", "") > prev.get("timestamp", "")):
                latest_by_key[key] = d
            recent_by_key.setdefault(key, []).append(d)

        # Keep only the last activity per key
        for k, acts in recent_by_key.items():
            acts.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
            recent_by_key[k] = acts[:1]

        # Most recent activity timestamp for this agent (for "last update X ago")
        agent_last_ts = ""
        if agent_acts:
            agent_last_ts = max(
                (a.get("timestamp", "") for a in agent_acts), default=""
            )

        rows = []
        for key, group in groups.items():
            # Extract the base symbol from the key for linking
            base_symbol = key.split("_")[0] if "_" in key else key
            sym_cfg = sym_cfg_map.get(base_symbol, {})
            pause_doc = sym_cfg.get("watchlist_pause") or {}
            recent = [
                {
                    "activity": a.get("activity", "N/A"),
                    "timestamp": a.get("timestamp", ""),
                    "id": a.get("id", ""),
                    "reason": a.get("reason", ""),
                }
                for a in recent_by_key.get(key, [])
            ]
            row: Dict[str, Any] = {
                "key": key,
                "symbol": base_symbol,
                "display": display_map.get(key, key),
                "underlying_price": latest_by_key.get(key, {}).get(
                    "underlying_price"),
                "recent_activities": recent,
                "risk_flags": latest_by_key.get(key, {}).get(
                    "risk_flags", []),
                "paused": is_watchlist_paused(sym_cfg) and not is_pm,
                "paused_until": pause_doc.get("until") if not is_pm else None,
            }
            if is_pm:
                dec = latest_by_key.get(key, {})
                row["dte"] = dec.get("dte_remaining")
                row["moneyness"] = dec.get("moneyness")
                row["assignment_risk"] = dec.get("assignment_risk")
                row["delta"] = dec.get("delta")
                # % Strike: percentage difference between underlying and strike
                parts = key.split("_")
                try:
                    strike = float(parts[1]) if len(parts) > 1 else None
                except (ValueError, IndexError):
                    strike = None
                up = row.get("underlying_price")
                if strike and up is not None:
                    row["strike_pct"] = ((up - strike) / strike) * 100
                else:
                    row["strike_pct"] = None
                row["option_type"] = (
                    "call" if agent_key == "open_call_monitor" else "put"
                )
                # DPS score + deltas (7d / 1d) + P&L
                row["dps_score"] = None
                row["dps_delta_7d"] = None
                row["dps_delta_1d"] = None
                row["pnl_pct"] = None
                try:
                    pos_id = None
                    # Find position_id from sym_cfg
                    ptype = "call" if agent_key == "open_call_monitor" else "put"
                    sym_c = sym_cfg_map.get(base_symbol, {})
                    key_strike_str = parts[1] if len(parts) > 1 else ""
                    key_exp = parts[2] if len(parts) > 2 else ""
                    try:
                        key_strike_f = float(key_strike_str)
                    except (ValueError, TypeError):
                        key_strike_f = None
                    for p in sym_c.get("positions", []):
                        if p.get("status") != "active" or p["type"] != ptype:
                            continue
                        # Compare strikes as floats to avoid "48.5" != "48.50"
                        try:
                            p_strike_f = float(p.get("strike", ""))
                        except (ValueError, TypeError):
                            p_strike_f = None
                        strike_match = (key_strike_f is not None
                                        and p_strike_f is not None
                                        and abs(key_strike_f - p_strike_f) < 0.001)
                        if strike_match and p.get("expiration") == key_exp:
                            pos_id = p.get("position_id")
                            break
                    if pos_id and cosmos:
                        snaps = cosmos.get_position_snapshots(base_symbol, pos_id, limit=200)
                        # P&L from most recent snapshot
                        if snaps:
                            row["pnl_pct"] = snaps[0].get("pnl_pct")
                        dps_snaps = [s for s in snaps if s.get("dps_score") is not None]
                        if dps_snaps:
                            row["dps_score"] = dps_snaps[0].get("dps_score")
                            # 7d and 1d deltas only
                            from datetime import timedelta
                            now_utc = datetime.now(timezone.utc)
                            seven_days_ago = now_utc - timedelta(days=7)
                            one_day_ago = now_utc - timedelta(days=1)
                            snap_7d = None
                            snap_1d = None
                            for s in dps_snaps:
                                ts_str = s.get("timestamp", "")
                                try:
                                    ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                                except (ValueError, TypeError):
                                    continue
                                if snap_7d is None and ts_dt <= seven_days_ago:
                                    snap_7d = s
                                if snap_1d is None and ts_dt <= one_day_ago:
                                    snap_1d = s
                            if snap_7d:
                                row["dps_delta_7d"] = dps_snaps[0]["dps_score"] - snap_7d["dps_score"]
                            if snap_1d:
                                row["dps_delta_1d"] = dps_snaps[0]["dps_score"] - snap_1d["dps_score"]
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning(
                        "DPS delta lookup failed for %s: %s", key, e)
            else:
                dec = latest_by_key.get(key, {})
                if agent_key == "buy_tracker":
                    row["entry_zone"] = dec.get("entry_zone")
                    row["technical_triggers"] = dec.get(
                        "technical_triggers", [])
                    row["strike_pct"] = None
                    row["option_type"] = "put"
                else:
                    main_strike = dec.get("strike")
                    main_expiration = dec.get("expiration")
                    main_premium = dec.get("premium")
                    # Alpha fallback: when main triplet is incomplete, use alpha alternative
                    # only if opportunity_strength is MODERATE or STRONG and alt triplet is valid.
                    use_alpha = False
                    if not _is_complete_triplet(main_strike, main_expiration, main_premium):
                        av = dec.get("alpha_view") or {}
                        if av.get("opportunity_strength") in ("MODERATE", "STRONG"):
                            alt = av.get("alternative") or {}
                            a_strike = alt.get("strike")
                            a_exp = alt.get("expiration")
                            a_prem = alt.get("premium")
                            if _is_complete_triplet(a_strike, a_exp, a_prem):
                                use_alpha = True
                                main_strike = a_strike
                                main_expiration = a_exp
                                main_premium = a_prem
                    row["strike"] = main_strike
                    row["expiration"] = main_expiration
                    row["premium"] = main_premium
                    row["recommendation_source"] = "alpha" if use_alpha else "agent"
                    # Gap: uses the displayed (possibly alpha-sourced) strike
                    up = row.get("underlying_price")
                    try:
                        rec_strike_f = float(main_strike) if main_strike else None
                    except (ValueError, TypeError):
                        rec_strike_f = None
                    if rec_strike_f and up is not None:
                        row["strike_pct"] = ((up - rec_strike_f) / rec_strike_f) * 100
                    else:
                        row["strike_pct"] = None
                    row["option_type"] = (
                        "call" if agent_key == "covered_call" else "put"
                    )
            rows.append(row)

        total_counts = _count_by_range(agent_alerts)
        for k in grand_totals:
            grand_totals[k] += total_counts[k]

        # Sort position monitors by DTE ascending (soonest expiration first)
        if is_pm:
            rows.sort(key=lambda r: (r.get("dte") is None, r.get("dte") or 0))

        agent_tables.append({
            "key": agent_key,
            "label": agent_meta["label"],
            "rows": rows,
            "totals": total_counts,
            "is_position_monitor": is_pm,
            "last_update_ts": agent_last_ts,
        })

    return agent_tables, grand_totals




@app.get("/api/dashboard")
async def api_dashboard(request: Request):
    """JSON dashboard payload consumed by the Next.js frontend (BFF)."""
    cosmos = getattr(request.app.state, "cosmos", None)
    market_open = is_us_market_open()
    if cosmos is None:
        error_detail = getattr(request.app.state, "cosmos_error", "unknown")
        return JSONResponse(
            {"error": f"CosmosDB not available: {error_detail}"}, status_code=503)
    try:
        data = _compute_dashboard_data(cosmos)
    except Exception as e:
        return JSONResponse(
            {"error": f"CosmosDB query failed: {e}"}, status_code=502)
    return {**data, "market_open": market_open}


@app.get("/api/dashboard/status")
async def api_dashboard_status(request: Request):
    """Lightweight change-signature for the dashboard (BFF polling).

    Returns per-agent last_run timestamps plus the latest activity
    timestamp. The frontend polls this cheap endpoint and only re-fetches
    the full dashboard when the signature changes — avoiding constant heavy
    reloads. Reads in-memory scheduler state + a single TOP 1 activity query.
    """
    agents: Dict[str, Any] = {}
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None and getattr(scheduler, "registry", None) is not None:
        try:
            for task in scheduler.registry.get_all_task_metadata():
                agents[task["name"]] = task.get("last_run")
        except Exception:  # pragma: no cover - defensive
            pass

    latest_activity = None
    cosmos = getattr(request.app.state, "cosmos", None)
    if cosmos is not None:
        try:
            recent = cosmos.get_all_activities(limit=1)
            if recent:
                latest_activity = recent[0].get("timestamp")
        except Exception:  # pragma: no cover - defensive
            pass

    return JSONResponse({"agents": agents, "latest_activity": latest_activity})


def _compute_dashboard_data(cosmos) -> Dict[str, Any]:
    """Shared dashboard computation used by both the HTML page and the JSON API.

    Returns the data-only context (no `request`); callers add framing.
    """
    all_symbols = cosmos.list_symbols()
    all_alerts = cosmos.get_all_alerts(limit=500)
    all_activities = cosmos.get_all_activities(limit=200)
    banner_doc = cosmos.get_banner()

    # Build set of closed position IDs so we can exclude their data
    closed_position_ids: set = set()
    for sym_cfg in all_symbols:
        for pos in sym_cfg.get("positions", []):
            if pos.get("status") != "active":
                closed_position_ids.add(pos["position_id"])

    # Exclude activities/alerts linked to closed positions from dashboard
    if closed_position_ids:
        closed_activity_ids = {
            d["id"] for d in all_activities
            if d.get("position_id") in closed_position_ids
        }
        all_activities = [
            d for d in all_activities
            if d.get("position_id") not in closed_position_ids
        ]
        all_alerts = [
            s for s in all_alerts
            if s.get("position_id") not in closed_position_ids
            and s.get("activity_id") not in closed_activity_ids
        ]

    symbol_count = len(all_symbols)
    position_count = sum(
        len([p for p in s.get("positions", []) if p.get("status") == "active"])
        for s in all_symbols
    )
    # Aggregate exposure totals for dashboard cards
    total_call_exposure = 0
    total_put_exposure = 0
    for s in all_symbols:
        active_positions = [p for p in s.get("positions", []) if p.get("status") == "active"]
        total_call_exposure += sum(
            float(p.get("strike", 0)) * 100
            for p in active_positions if p.get("type") == "call"
        )
        total_put_exposure += sum(
            float(p.get("strike", 0)) * 100
            for p in active_positions if p.get("type") == "put"
        )

    agent_tables, grand_totals = _build_dashboard_tables(
        cosmos, all_symbols, all_alerts, all_activities)

    # Annualized RoC on currently-open positions (income currently working).
    # Reuses the economics engine for a consistent, capital-weighted figure.
    open_roc_annualized = _build_economics_report(
        all_symbols, status_filter="active"
    )["summary"]["avg_roc_annualized"]

    activity = []
    for d in all_activities[:100]:
        agent_key = str(d.get("agent_type", ""))
        d["_agent_key"] = agent_key
        d["_agent_label"] = AGENT_TYPES.get(agent_key, {}).get(
            "label", agent_key)
        activity.append(d)

    return {
        "agent_tables": agent_tables,
        "grand_totals": grand_totals,
        "symbol_count": symbol_count,
        "position_count": position_count,
        "total_call_exposure": total_call_exposure,
        "total_put_exposure": total_put_exposure,
        "open_roc_annualized": open_roc_annualized,
        "activity": activity,
        "banner_items": (banner_doc or {}).get("items", []),
    }


# ===========================================================================
# Page Routes — Economics
# ===========================================================================



# ===========================================================================
# Page Routes — Plans
# ===========================================================================



# ===========================================================================
# Page Routes — Symbols
# ===========================================================================





@app.get("/api/calendar")
async def api_calendar(request: Request):
    """Return earnings and ex-dividend dates from the calendar container."""
    cosmos = getattr(request.app.state, "cosmos", None)
    if cosmos is None:
        return {"events": [], "error": "CosmosDB not available"}

    events = cosmos.get_calendar_events()
    return {"events": events}


@app.post("/api/calendar/refresh")
async def api_calendar_refresh(request: Request):
    """Refresh calendar events from yfinance and store in CosmosDB."""
    cosmos = getattr(request.app.state, "cosmos", None)
    if cosmos is None:
        return JSONResponse({"error": "CosmosDB not available"}, status_code=503)

    if yf is None:
        return JSONResponse({"error": "yfinance not installed"}, status_code=503)

    symbols = cosmos.list_symbols() if cosmos else []
    updated = 0
    errors = 0

    for sym_doc in symbols:
        symbol = sym_doc.get("symbol", "")
        if not symbol:
            continue

        # Collect active positions with their expiration dates
        active_positions = []
        for p in sym_doc.get("positions", []):
            if p.get("status") == "active" and p.get("expiration"):
                active_positions.append(p)

        def _has_position_active_on(event_date_str: str) -> bool:
            """Check if any active position covers the event date (expiration >= event date)."""
            for p in active_positions:
                try:
                    exp_str = p["expiration"][:10]  # handle ISO datetime
                    if exp_str >= event_date_str:
                        return True
                except (TypeError, IndexError):
                    continue
            return False

        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info or {}
        except Exception as exc:
            logger.warning("Calendar: failed to fetch info for %s: %s", symbol, exc)
            errors += 1
            continue

        # Earnings date — try multiple keys from yfinance
        earnings_ts = (
            info.get("earningsTimestampStart")
            or info.get("earningsTimestamp")
            or info.get("mostRecentQuarter")
        )
        if not earnings_ts:
            # Try the calendar endpoint for next earnings
            try:
                cal = ticker.calendar
                if cal is not None:
                    if isinstance(cal, dict):
                        ed = cal.get("Earnings Date")
                        if ed and len(ed) > 0:
                            # Returns list of Timestamp objects
                            earnings_date = str(ed[0].date()) if hasattr(ed[0], 'date') else str(ed[0])[:10]
                            has_active = _has_position_active_on(earnings_date)
                            cosmos.upsert_calendar_event(symbol, "earnings", earnings_date, has_active)
                            updated += 1
                            earnings_ts = "done"
                    elif hasattr(cal, 'iloc'):
                        # DataFrame format
                        if "Earnings Date" in cal.columns:
                            earnings_date = str(cal["Earnings Date"].iloc[0])[:10]
                            has_active = _has_position_active_on(earnings_date)
                            cosmos.upsert_calendar_event(symbol, "earnings", earnings_date, has_active)
                            updated += 1
                            earnings_ts = "done"
            except Exception:
                pass

        if earnings_ts and earnings_ts != "done":
            try:
                earnings_date = datetime.fromtimestamp(int(earnings_ts), tz=timezone.utc).strftime("%Y-%m-%d")
                has_active = _has_position_active_on(earnings_date)
                cosmos.upsert_calendar_event(symbol, "earnings", earnings_date, has_active)
                updated += 1
            except (OSError, ValueError, TypeError):
                pass

        # Ex-dividend date
        ex_div_ts = info.get("exDividendDate")
        if not ex_div_ts:
            ex_div_ts = info.get("lastDividendDate")
        if ex_div_ts:
            try:
                ex_div_date = datetime.fromtimestamp(int(ex_div_ts), tz=timezone.utc).strftime("%Y-%m-%d")
                has_active = _has_position_active_on(ex_div_date)
                cosmos.upsert_calendar_event(symbol, "ex_dividend", ex_div_date, has_active)
                updated += 1
            except (OSError, ValueError, TypeError):
                pass

    return {"updated": updated, "errors": errors, "symbols_processed": len(symbols),
            "calendar_container_available": cosmos.calendar_container is not None}




# ===========================================================================
# Page Routes — Fetch Preview (raw market data)
# ===========================================================================



# ===========================================================================
# API — Symbol Position Report (LLM-generated)
# ===========================================================================

@app.post("/api/symbols/{symbol}/report")
async def symbol_report_api(request: Request, symbol: str):
    """Generate a comprehensive position/situation report for a symbol.

    Uses the ReportAgent (same pattern as other agents) to produce a
    structured markdown report from cached market data + CosmosDB
    activities/alerts.
    """
    symbol = symbol.upper()

    try:
        cosmos = _get_cosmos(request)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)

    symbol_doc = cosmos.get_symbol(symbol)
    if not symbol_doc:
        return JSONResponse({"error": f"Symbol {symbol} not found"},
                            status_code=404)

    config_obj, err_resp = _llm_settings_response("report")
    if err_resp:
        return err_resp

    try:
        from src.agent_runner import AgentRunner
        from src.report_agent import run_report_analysis

        runner = AgentRunner(
            llm=config_obj.llm_config(),
            model=config_obj.model_deployment,
            function_llms=config_obj.function_llm_configs(),
        )

        result = await run_report_analysis(
            config=config_obj,
            runner=runner,
            cosmos=cosmos,
            symbol=symbol,
        )

        if "error" in result:
            return JSONResponse({"error": result["error"]}, status_code=404)

        return JSONResponse(result)

    except Exception as e:
        logger.exception("Report generation failed for %s", symbol)
        return JSONResponse({"error": str(e)}, status_code=500)






@app.post("/api/symbols/{symbol}/technical-analysis")
async def symbol_technical_analysis_api(request: Request, symbol: str):
    """Generate a detailed technical analysis for a symbol.

    Uses the TechnicalAnalysisAgent to produce a structured markdown analysis
    from cached market data (technicals, overview, forecast, dividends).
    """
    symbol = symbol.upper()

    try:
        cosmos = _get_cosmos(request)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)

    symbol_doc = cosmos.get_symbol(symbol)
    if not symbol_doc:
        return JSONResponse({"error": f"Symbol {symbol} not found"},
                            status_code=404)

    config_obj, err_resp = _llm_settings_response("technical_analysis")
    if err_resp:
        return err_resp

    try:
        from src.agent_runner import AgentRunner
        from src.technical_analysis_agent import run_technical_analysis

        runner = AgentRunner(
            llm=config_obj.llm_config(),
            model=config_obj.model_deployment,
            function_llms=config_obj.function_llm_configs(),
        )

        result = await run_technical_analysis(
            config=config_obj,
            runner=runner,
            cosmos=cosmos,
            symbol=symbol,
        )

        if "error" in result:
            return JSONResponse({"error": result["error"]}, status_code=404)

        return JSONResponse(result)

    except Exception as e:
        logger.exception("Technical analysis generation failed for %s", symbol)
        return JSONResponse({"error": str(e)}, status_code=500)






def _resolve_forecast_range(range_param, date_from, date_to):
    """Resolve table filters to (date_from, date_to) YYYY-MM-DD, defaulting to last month."""
    if date_from or date_to:
        return date_from, date_to
    days_map = {"1d": 1, "7d": 7, "30d": 30, "90d": 90}
    days = days_map.get(range_param or "30d", 30)
    today = datetime.now(timezone.utc)
    return (today - timedelta(days=days)).strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


@app.get("/api/symbols/{symbol}/forecasts")
async def api_symbol_forecasts(request: Request, symbol: str,
                               range: str = "30d",
                               from_: str = Query(default=None, alias="from"),
                               to: str = Query(default=None)):
    """Deterministic price-forecast table rows + rolling endpoint calibration.

    Query params: ``range`` (1d/7d/30d/90d, default 30d) OR explicit ``from``/``to``
    (YYYY-MM-DD). Returns per-prediction rows (path % + endpoint per horizon) and a
    rolling per-horizon endpoint hit-rate aggregate.
    """
    from src.price_forecast import summarize_prediction, aggregate_hit_rate, aggregate_forecast_averages, compute_reading

    try:
        cosmos = _get_cosmos(request)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)

    sym = symbol.upper()
    date_from, date_to = _resolve_forecast_range(range, from_, to)
    preds = cosmos.get_price_forecasts(sym, date_from, date_to)

    rows = []
    for p in preds:
        rows.append({
            "id": p.get("id"),
            "created_date": p.get("created_date"),
            "start_date": p.get("start_date"),
            "end_date": p.get("end_date"),
            "status": p.get("status"),
            "price_at_creation": p.get("price_at_creation"),
            "hv": p.get("hv"),
            "vol_source": p.get("vol_source", "hv"),
            "confidence": p.get("confidence", 0.68),
            "outer_confidence": p.get("outer_confidence", 0.95),
            "bias": p.get("bias"),
            "trend": p.get("trend"),
            "reading": p.get("reading") or compute_reading(
                p.get("bias"), (p.get("trend") or {}).get("slope")
            ),
            "flags": p.get("flags", {}),
            "horizons": summarize_prediction(p),
        })

    # Confidence used by the most recent prediction — drives UI labels/target.
    latest_conf = rows[0]["confidence"] if rows else 0.68
    latest_outer = rows[0]["outer_confidence"] if rows else 0.95
    # Per-symbol volatility calibration from the newest prediction (band-width
    # multiplier that self-adjusts each cron run). None for legacy docs.
    latest_calibration = preds[0].get("calibration") if preds else None

    # Averages use a fixed per-horizon lookback (1d→1, 1w→5, 2w→10, 4w→20 most
    # recent predictions), independent of the table range selector. Fetch a window
    # wide enough (~45 calendar days ≈ 30 sessions) to always satisfy the 4w=20
    # lookback; the aggregator caps per horizon.
    avg_from = (datetime.now(timezone.utc) - timedelta(days=45)).strftime("%Y-%m-%d")
    avg_preds = cosmos.get_price_forecasts(sym, avg_from, None)

    return JSONResponse({
        "symbol": sym,
        "range": {"from": date_from, "to": date_to},
        "count": len(rows),
        "confidence": latest_conf,
        "outer_confidence": latest_outer,
        "calibration": latest_calibration,
        "rows": rows,
        "hit_rate": aggregate_hit_rate(preds),
        "averages": aggregate_forecast_averages(avg_preds),
    })


@app.get("/api/symbols/{symbol}/forecasts/{forecast_id}")
async def api_symbol_forecast_detail(request: Request, symbol: str, forecast_id: str):
    """Full detail for a single prediction — feeds the modal fan chart."""
    try:
        cosmos = _get_cosmos(request)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)

    doc = cosmos.get_price_forecast(symbol.upper(), forecast_id)
    if not doc:
        return JSONResponse(
            {"error": f"Forecast {forecast_id} not found"}, status_code=404)
    return JSONResponse(doc)


@app.get("/api/symbols/{symbol}/options-chain")
async def api_symbol_options_chain(request: Request, symbol: str):
    """Return parsed option chain data from yfinance provider."""
    try:
        cosmos = _get_cosmos(request)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)

    doc = cosmos.get_symbol(symbol.upper())
    if not doc:
        return JSONResponse({"error": f"Symbol {symbol} not found"},
                            status_code=404)

    provider = getattr(request.app.state, "yf_provider", None)
    if provider is None:
        return JSONResponse({"error": "Data provider not initialized"}, status_code=503)

    try:
        data = await provider.fetch_all(symbol.upper())
        raw = data.get("options_chain", "{}")
        result = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as e:
        logger.exception("Options chain fetch failed for %s", symbol)
        return JSONResponse(
            {"error": f"Failed to fetch options chain: {e}", "symbol": symbol.upper()},
            status_code=500,
        )

    if not result.get("calls") and not result.get("puts"):
        return JSONResponse(
            {"error": "No options chain data available.",
             "symbol": symbol.upper()},
            status_code=404,
        )

    # Reference/display view (Danny's zero-free decision, Rule Z10):
    # retained with nulls + `_meta.field_status`, never filtered — the
    # user is entitled to see the whole market picture. Applied to the
    # raw stored chain; never mutates it.
    from src.options_chain_cache import apply_agent_view
    result = apply_agent_view(result)

    return JSONResponse({
        "symbol": symbol.upper(),
        "timestamp": result.get("timestamp", ""),
        "calls": result.get("calls", {}),
        "puts": result.get("puts", {}),
    })


@app.get("/api/symbols/{symbol}/best-options")
async def api_symbol_best_options(
     request: Request,
     symbol: str,
     side: str = Query(default="both"),
     dte_min: int = Query(default=DEFAULT_DTE_MIN, ge=0),
     dte_max: int = Query(default=DEFAULT_DTE_MAX, ge=0, le=60),
     support_level: Optional[float] = Query(default=None),
):
     """Best Options for Symbol Detail — precomputed-by-default
     (`.squad/decisions/inbox/danny-best-options-scheduler-design.md` §11a).

     **Canonical request** (`side="both"`, `dte_min=0`, `dte_max=45`,
     `support_level=None`) returns the shared precomputed cache entry,
     never computes. On cache miss with chain present: `status="warming"`,
     `retry_after=15`, `reason="precompute_pending"`. On cache miss with
     chain cold: same, plus kicks off a background chain refresh (Symbol
     Detail cold-path warming, still preserved per §11a).

     **Non-canonical override** (explicit `dte_min`/`dte_max`/`support_level`
     or `side != "both"`) computes live and returns `cache: {"used": false,
     "reason": "non_canonical_parameters"}`. This preserves the deliberate
     45-day alignment decision's "override survives" ruling (§9c).

     Zero LLM calls. On a cold cache, never blocks on a live yfinance fetch.
     """
     if side not in ("call", "put", "both"):
         return JSONResponse(
             {"error": "side must be one of: call, put, both"}, status_code=400,
         )
     if dte_min > dte_max:
         return JSONResponse(
             {"error": "dte_min must be <= dte_max"}, status_code=400,
         )

     try:
         cosmos = _get_cosmos(request)
     except RuntimeError as e:
         return JSONResponse({"error": str(e)}, status_code=503)

     sym_upper = symbol.upper()
     sym_doc = cosmos.get_symbol(sym_upper)
     if not sym_doc:
         return JSONResponse({"error": f"Symbol {sym_upper} not found"}, status_code=404)

     # Check if canonical parameters (precomputed path)
     is_canonical = (
         side == "both"
         and dte_min == DEFAULT_DTE_MIN
         and dte_max == DEFAULT_DTE_MAX
         and support_level is None
     )

     if is_canonical:
         # Precomputed path
         from src.best_options_cache import get_best_options_cache
         from src.options_chain_cache import get_options_chain_cache

         best_cache = get_best_options_cache()
         chain_cache = get_options_chain_cache()

         entry = best_cache.get_entry(sym_upper)

         if entry and entry.get("status") in ("ok", "stale"):
             # Cache hit
             envelope = entry.get("envelope")
             if not envelope:
                 return JSONResponse(
                     {"status": "unavailable", "symbol": sym_upper,
                      "error": "Entry envelope missing"}, status_code=200,
                 )

             # Compute inputs_drift (compare cached inputs to current)
             cached_inputs = entry.get("inputs") or {}
             category = (sym_doc.get("enrichment") or {}).get("category")
             total_shares = int(sym_doc.get("total_shares", 0) or 0)
             next_earnings_date = cosmos.get_next_earnings_date(sym_upper)
             ex_dividend_date = cosmos.get_next_calendar_event_date(sym_upper, "ex_dividend")

             inputs_drift = []
             if cached_inputs.get("category") != category:
                 inputs_drift.append("category")
             if cached_inputs.get("total_shares") != total_shares:
                 inputs_drift.append("total_shares")
             if cached_inputs.get("next_earnings_date") != next_earnings_date:
                 inputs_drift.append("next_earnings_date")
             if cached_inputs.get("ex_dividend_date") != ex_dividend_date:
                 inputs_drift.append("ex_dividend_date")

             # Get next_run from scheduler (if available)
             scheduler = getattr(request.app.state, "scheduler", None)
             next_run_iso = None
             if scheduler:
                 task = scheduler.registry.get_task("best_options")
                 if task:
                     next_run_iso = task.next_run.isoformat() if task.next_run else None

             # Build cache metadata
             cache_meta = {
                 "used": True,
                 "generation": entry.get("generation"),
                 "entry_status": entry.get("status"),
                 "computed_at": entry.get("computed_at"),
                 "chain_timestamp": entry.get("chain_timestamp"),
                 "chain_stale": chain_cache.is_stale(sym_upper),
                 "inputs_drift": inputs_drift,
                 "next_run": next_run_iso,
                 "refreshing": entry.get("refreshing", False),
                 "refresh_started_at": entry.get("refresh_started_at"),
                 "refresh_completed_at": entry.get("refresh_completed_at"),
                 "refresh_error": entry.get("refresh_error"),
                 "chain_refresh_error": entry.get("chain_refresh_error"),
             }

             return JSONResponse({**envelope, "cache": cache_meta})

         # Cache miss
         if entry and entry.get("status") == "error":
             # Permanent error
             scheduler = getattr(request.app.state, "scheduler", None)
             next_run_iso = None
             if scheduler:
                 task = scheduler.registry.get_task("best_options")
                 if task:
                     next_run_iso = task.next_run.isoformat() if task.next_run else None

             return JSONResponse({
                 "status": "unavailable",
                 "symbol": sym_upper,
                 "error": entry.get("error"),
                 "reason": entry.get("reason"),
                 "cache": {
                     "used": True,
                     "generation": entry.get("generation"),
                     "entry_status": "error",
                     "computed_at": entry.get("computed_at"),
                     "next_run": next_run_iso,
                 },
             }, status_code=200)

         # No entry or warming — check if chain is present
         chain_json = chain_cache.get_or_hydrate(sym_upper, trigger_swr=False)
         if chain_json is None:
             # Chain cold — schedule background refresh (Symbol Detail cold-path, §11a)
             chain_cache.schedule_background_refresh(sym_upper)
             reason = "chain_cold"
         else:
             reason = "precompute_pending"

         scheduler = getattr(request.app.state, "scheduler", None)
         next_run_iso = None
         if scheduler:
             task = scheduler.registry.get_task("best_options")
             if task:
                 next_run_iso = task.next_run.isoformat() if task.next_run else None

         return JSONResponse({
             "status": "warming",
             "symbol": sym_upper,
             "retry_after": 15,
             "reason": reason,
             "next_run": next_run_iso,
         }, status_code=200)

     # Non-canonical path: compute live
     from src.options_chain_cache import get_options_chain_cache

     cache = get_options_chain_cache()
     chain_json = cache.get_or_hydrate(sym_upper)
     if chain_json is None:
         # True cold miss: never block on a live fetch (F6). Kick off a
         # background refresh (non-blocking, at-most-one-in-flight-per-
         # symbol) and let the client poll again shortly.
         cache.schedule_background_refresh(sym_upper)
         return JSONResponse({
             "status": "warming",
             "symbol": sym_upper,
             "retry_after": 15,
         })

     try:
         chain = json.loads(chain_json) if isinstance(chain_json, str) else chain_json
     except (TypeError, ValueError) as e:
         logger.exception("Best options: corrupt cached chain for %s", sym_upper)
         return JSONResponse(
             {"error": f"Cached option chain unreadable: {e}", "symbol": sym_upper},
             status_code=500,
         )

     # Input assembly (Rusty's part per Danny's design §9) — category,
     # shares, earnings/ex-dividend calendar dates. Spot price and ATM IV
     # come from inside the chain itself (F7), not from a separate
     # overview fetch, so they are not assembled here.
     category = (sym_doc.get("enrichment") or {}).get("category")
     total_shares = int(sym_doc.get("total_shares", 0) or 0)
     next_earnings_date = cosmos.get_next_earnings_date(sym_upper)
     ex_dividend_date = cosmos.get_next_calendar_event_date(sym_upper, "ex_dividend")
     # `support_level` has no deterministic source in this codebase today
     # (pivot points are currently LLM-extracted prompt context only, not a
     # callable technical-analysis function) — accepted as an optional
     # query param so a future deterministic source (or the UI) can supply
     # it; omitted entirely, it simply disables the `below_support` flag.

     try:
         from src.best_options import evaluate_best_options
     except ImportError as e:
         logger.error("Best options: scorer module not available yet: %s", e)
         return JSONResponse(
             {"error": "Best Options scorer is not available yet", "symbol": sym_upper},
             status_code=503,
         )

     try:
         result = evaluate_best_options(
             chain,
             side=side,
             category=category,
             total_shares=total_shares,
             next_earnings_date=next_earnings_date,
             ex_dividend_date=ex_dividend_date,
             support_level=support_level,
             dte_min=dte_min,
             dte_max=dte_max,
             now=datetime.now(timezone.utc),
         )
     except Exception as e:
         logger.exception("Best options evaluation failed for %s", sym_upper)
         return JSONResponse(
             {"error": str(e), "symbol": sym_upper}, status_code=500,
         )

     # Add cache metadata showing this was NOT from the shared cache
     result["cache"] = {
         "used": False,
         "reason": "non_canonical_parameters",
     }

     return JSONResponse(result)


# ===========================================================================
# REST API — Options Screener (Rusty's plumbing around Linus's pure
# aggregator `src.options_screener.evaluate_options_screener`; design:
# `.squad/decisions/inbox/copilot-options-screener-approved.md`)
# ===========================================================================

_SCREENER_MAX_COLD_WARMS_PER_REQUEST = 4
# Generous sentinel for "fetch effectively everything the aggregator has
# already filtered" when re-sorting by a non-default column below — a
# real ceiling (400 rows/symbol/side, `best_options._MAX_ROWS_PER_SIDE`)
# already bounds how large this can ever get, this is just comfortably
# above any realistic total across the whole symbol universe.
_SCREENER_UNBOUNDED_FETCH_LIMIT = 100_000

# Presentation-layer sort keys only — re-ordering rows the aggregator
# already scored/gated/paginated, never a re-derivation of score, colour,
# or admission. "default" is the aggregator's own canonical order (score
# desc, DTE asc, category-relative delta fit asc) and is passed straight
# through untouched, no re-sort performed.
_SCREENER_SORT_FIELDS = {
     "default": None,
     "annualized_return_pct": lambda r: r.get("annualized_return_pct"),
     "premium_pct": lambda r: r.get("premium_pct"),
     "dte": lambda r: r.get("dte"),
     "open_interest": lambda r: r.get("open_interest"),
     "abs_delta": lambda r: r.get("abs_delta"),
     "symbol": lambda r: r.get("symbol"),
}


def _resort_screener_rows(rows: list, sort_field: str, direction: str) -> list:
     """Re-orders already-filtered, already-scored rows by a caller-chosen
     column. A stable sort; missing values on the chosen field always sort
     last regardless of `direction` (never let an absent metric look like
     "best" just because direction flipped)."""
     getter = _SCREENER_SORT_FIELDS[sort_field]
     reverse = direction == "desc"
     if sort_field == "symbol":
         return sorted(rows, key=lambda r: getter(r) or "", reverse=reverse)

     def key(row):
         value = getter(row)
         if value is None:
             return (1, 0.0)
         numeric = float(value)
         return (0, -numeric if reverse else numeric)

     return sorted(rows, key=key)


def _build_screener_symbol_inputs(cosmos, cache, symbol_filter):
     """Assemble per-symbol `evaluate_options_screener` inputs with the
     minimum Cosmos/cache work possible, per the approved directive
     ("batch list symbols/calendar data rather than 3N Cosmos queries").

     One `list_symbols()` call and one `get_calendar_events()` call cover
     every symbol (vs. one plus two per-symbol queries each); calendar
     events are grouped in Python into the earliest future
     earnings/ex-dividend date per symbol, mirroring what
     `get_next_earnings_date`/`get_next_calendar_event_date` compute one
     symbol at a time.

     Cold chains are capped at `_SCREENER_MAX_COLD_WARMS_PER_REQUEST` new
     background refreshes per request — beyond that cap a cold symbol is
     reported as `"cold"` (not actively being warmed by this request) so a
     page full of never-fetched symbols cannot fan out an unbounded
     refresh storm; it will be warmed by a later request/poll instead.

     Returns `(symbol_inputs, status_detail)`: `symbol_inputs` uses only
     the aggregator's own three statuses (`ready`/`warming`/`error`);
     `status_detail` is the richer `ok`/`warming`/`cold`/`error` per-symbol
     diagnostic list the API response surfaces for its partial-status
     header and per-row stale/error badges — the aggregator itself never
     sees this finer distinction.

     Livingston (integration fix): this function performs the whole
     `list_symbols`/`get_calendar_events`/per-symbol-hydrate read is
     synchronous, real Cosmos/persistence I/O (`OptionsChainStore.hydrate`
     issues a blocking `query_items` call on a true cache miss) — across a
     many-symbol watchlist that is exactly the "bounded/sequential in a
     worker thread" work the approved directive calls for, so the caller
     runs this whole function via `loop.run_in_executor`, off the request's
     event loop. Because of that, this function must never itself call
     `cache.schedule_background_refresh`/rely on `get_or_hydrate`'s in-line
     SWR trigger — both go through `asyncio.create_task`, which requires a
     running loop on *this* (executor worker) thread and would silently
     no-op there. Instead it only *decides* which symbols need warming
     (applying the same `_SCREENER_MAX_COLD_WARMS_PER_REQUEST` cap to cold
     misses, uncapped for stale-but-present hits, matching the SWR contract
     every other `get_or_hydrate` caller already gets) and returns them in
     `to_warm` for the caller to actually schedule back on the event loop.
     """
     docs = cosmos.list_symbols()
     if symbol_filter is not None:
         docs = [d for d in docs if (d.get("symbol") or "").strip().upper() in symbol_filter]

     today = datetime.now(timezone.utc).date().isoformat()
     earnings_by_symbol: Dict[str, str] = {}
     ex_div_by_symbol: Dict[str, str] = {}
     for ev in cosmos.get_calendar_events():
         sym = (ev.get("symbol") or "").strip().upper()
         date_val = ev.get("date")
         if not sym or not date_val or date_val < today:
             continue
         bucket = ev.get("type")
         target = earnings_by_symbol if bucket == "earnings" else (
             ex_div_by_symbol if bucket == "ex_dividend" else None
         )
         if target is None:
             continue
         if target.get(sym) is None or date_val < target[sym]:
             target[sym] = date_val

     symbol_inputs = []
     status_detail = []
     to_warm: List[str] = []
     warms_scheduled = 0
     for doc in docs:
         symbol = (doc.get("symbol") or "").strip().upper()
         if not symbol:
             continue
         enrichment = doc.get("enrichment") or {}
         category = enrichment.get("category")
         total_shares = int(doc.get("total_shares", 0) or 0)
         next_earnings_date = earnings_by_symbol.get(symbol)
         ex_dividend_date = ex_div_by_symbol.get(symbol)
         common = {
             "symbol": symbol, "category": category, "total_shares": total_shares,
             "next_earnings_date": next_earnings_date, "ex_dividend_date": ex_dividend_date,
         }

         chain_json = cache.get_or_hydrate(symbol, trigger_swr=False)
         if chain_json is None:
             if warms_scheduled < _SCREENER_MAX_COLD_WARMS_PER_REQUEST:
                 to_warm.append(symbol)
                 warms_scheduled += 1
                 status_detail.append({"symbol": symbol, "status": "warming"})
             else:
                 status_detail.append({"symbol": symbol, "status": "cold"})
             symbol_inputs.append({**common, "status": "warming"})
             continue

         try:
             chain = json.loads(chain_json) if isinstance(chain_json, str) else chain_json
         except (TypeError, ValueError) as e:
             error_msg = f"Cached option chain unreadable: {e}"
             status_detail.append({"symbol": symbol, "status": "error", "error": error_msg})
             symbol_inputs.append({**common, "status": "error", "error": error_msg})
             continue

         stale = cache.is_stale(symbol)
         if stale:
             to_warm.append(symbol)
         status_detail.append({
             "symbol": symbol, "status": "ok",
             "stale": stale,
             "chain_timestamp": chain.get("timestamp") if isinstance(chain, dict) else None,
         })
         # `support_level` has no deterministic source in this codebase
         # today (same finding as the single-symbol endpoint above) —
         # always None across a many-symbol screener, never a single
         # shared value that would make sense for every symbol at once.
         symbol_inputs.append({**common, "status": "ready", "chain": chain, "support_level": None})

     return symbol_inputs, status_detail, to_warm


@app.get("/api/screener/options")
async def api_screener_options(
     request: Request,
     side: str = Query(default="call"),
     symbols: Optional[str] = Query(default=None),
     preferences: Optional[str] = Query(default=None),
     min_annualized_return_pct: Optional[float] = Query(default=None),
     min_abs_delta: Optional[float] = Query(default=None, ge=0, le=1),
     max_abs_delta: Optional[float] = Query(default=None, ge=0, le=1),
     dte_min: int = Query(default=0, ge=0),
     dte_max: int = Query(default=45, ge=0, le=60),
     min_open_interest: Optional[float] = Query(default=None, ge=0),
     min_gap_pct: Optional[float] = Query(default=None, ge=-100, le=200),
     max_gap_pct: Optional[float] = Query(default=None, ge=-100, le=200),
     sort: str = Query(default="default"),
     sort_dir: str = Query(default="desc", alias="dir"),
     offset: int = Query(default=0, ge=0),
     limit: int = Query(default=100, ge=1, le=500),
):
     """Options Screener — precomputed-only (binding directive
     `.squad/decisions/inbox/copilot-options-screener-precomputed-only.md`).

     **Strictly precomputed:** never evaluates and never hydrates on request.
     Reads the shared in-memory Best Options cache only. Only symbols with
     an already-precomputed result contribute rows. Readiness is always
     stated as **`N of X loaded`**; whenever `N < X`, the response and UI
     carry a warning to wait for the next scheduled cycle.

     **Zero chain-cache coupling** — this endpoint references the option-chain
     cache not at all (§11b enforcement gate). Every row comes from a
     precomputed envelope's already-computed `chain_stale_at_compute`.

     Zero LLM calls. Never blocks, never hydrates, never warms. Always
     returns HTTP 200 immediately.
     """
     if side not in ("call", "put"):
         return JSONResponse({"error": "side must be one of: call, put"}, status_code=400)
     if dte_min > dte_max:
         return JSONResponse({"error": "dte_min must be <= dte_max"}, status_code=400)
     if min_abs_delta is not None and max_abs_delta is not None and min_abs_delta > max_abs_delta:
         return JSONResponse({"error": "min_abs_delta must be <= max_abs_delta"}, status_code=400)
     if min_gap_pct is not None and max_gap_pct is not None and min_gap_pct > max_gap_pct:
         return JSONResponse({"error": "min_gap_pct must be <= max_gap_pct"}, status_code=400)
     if sort not in _SCREENER_SORT_FIELDS:
         return JSONResponse(
             {"error": f"sort must be one of: {', '.join(_SCREENER_SORT_FIELDS)}"}, status_code=400,
         )
     if sort_dir not in ("asc", "desc"):
         return JSONResponse({"error": "dir must be one of: asc, desc"}, status_code=400)

     try:
         cosmos = _get_cosmos(request)
     except RuntimeError as e:
         return JSONResponse({"error": str(e)}, status_code=503)

     symbol_filter = None
     if symbols is not None:
         symbol_filter = {s.strip().upper() for s in symbols.split(",") if s.strip()}
     preference_list = None
     if preferences is not None:
         preference_list = [p.strip() for p in preferences.split(",") if p.strip()]

     from src.best_options_cache import get_best_options_cache

     best_cache = get_best_options_cache()
     snapshot = best_cache.snapshot()
     precomputed_entries = snapshot.get("entries") or {}

     # Build precomputed envelope map for the aggregator
     precomputed_envelopes = {}
     for sym, entry in precomputed_entries.items():
         if entry.get("status") in ("ok", "stale"):
             envelope = entry.get("envelope")
             if envelope:
                 precomputed_envelopes[sym] = envelope

     # Compute X (total configured universe, filtered by symbols= if supplied)
     docs = cosmos.list_symbols()
     if symbol_filter is not None:
         docs = [d for d in docs if (d.get("symbol") or "").strip().upper() in symbol_filter]

     # Build symbol_inputs for the aggregator (precomputed-only path)
     # The aggregator needs: symbol, status ("ready"|"warming"|"error")
     # For "ready" symbols, the `precomputed` parameter provides the envelope
     symbol_inputs = []
     for doc in docs:
         symbol = (doc.get("symbol") or "").strip().upper()
         if not symbol:
             continue

         entry = precomputed_entries.get(symbol)
         if entry and entry.get("status") in ("ok", "stale") and entry.get("envelope"):
             # Ready
             symbol_inputs.append({"symbol": symbol, "status": "ready"})
         elif entry and entry.get("status") == "error":
             # Error
             error_msg = entry.get("error", "Precompute error")
             symbol_inputs.append({"symbol": symbol, "status": "error", "error": error_msg})
         else:
             # Warming or absent
             symbol_inputs.append({"symbol": symbol, "status": "warming"})

     try:
         from src.options_screener import evaluate_options_screener
     except ImportError as e:
         logger.error("Options screener: aggregator module not available yet: %s", e)
         return JSONResponse({"error": "Options Screener is not available yet"}, status_code=503)

     # A non-default sort needs every matching row in hand before it can be
     # re-ordered and re-paginated here — the aggregator's own offset/limit
     # pagination is only valid against its own canonical order.
     resorting = sort != "default"
     fetch_offset = 0 if resorting else offset
     fetch_limit = _SCREENER_UNBOUNDED_FETCH_LIMIT if resorting else limit

     try:
         result = await asyncio.get_event_loop().run_in_executor(
             None,
             lambda: evaluate_options_screener(
                 symbol_inputs,
                 now=datetime.now(timezone.utc),
                 side=side,
                 preferences=preference_list,
                 symbols=sorted(symbol_filter) if symbol_filter is not None else None,
                 min_annualized_return_pct=min_annualized_return_pct,
                 min_abs_delta=min_abs_delta,
                 max_abs_delta=max_abs_delta,
                 min_dte=dte_min,
                 max_dte=dte_max,
                 min_open_interest=min_open_interest,
                 min_gap_pct=min_gap_pct,
                 max_gap_pct=max_gap_pct,
                 offset=fetch_offset,
                 limit=fetch_limit,
                 precomputed=precomputed_envelopes,
             ),
         )
     except Exception as e:
         logger.exception("Options screener evaluation failed")
         return JSONResponse({"error": str(e)}, status_code=500)

     section = result["calls"] if side == "call" else result["puts"]
     rows = section["rows"]
     pagination = section["pagination"]

     if resorting:
         rows = _resort_screener_rows(rows, sort, sort_dir)
         total_matching = pagination["total_matching"]
         rows = rows[offset: offset + limit]
         pagination = {
             "offset": offset, "limit": limit,
             "total_matching": total_matching,
             "returned": len(rows),
             "has_more": (offset + len(rows)) < total_matching,
         }

     # Per-row enrichment (no shares check, chain_stale from cached entry)
     shares_by_symbol = {(doc.get("symbol") or "").strip().upper(): int(doc.get("total_shares", 0) or 0) for doc in docs}
     stale_by_symbol = {sym: entry.get("chain_stale_at_compute", False) for sym, entry in precomputed_entries.items()}

     for row in rows:
         if side == "call":
             row["no_shares_held"] = shares_by_symbol.get(row["symbol"], 0) < 100
         row["chain_stale"] = stale_by_symbol.get(row["symbol"], False)

     # Build status summary (X and N per §11b)
     total_symbols = len(docs)  # X: configured universe (filtered by symbols=)
     symbols_ready = sum(1 for si in symbol_inputs if si["status"] == "ready")  # N: usable cache entries
     symbols_warming = sum(1 for si in symbol_inputs if si["status"] == "warming")
     symbols_error = sum(1 for si in symbol_inputs if si["status"] == "error")

     counts = {
         "total": total_symbols,
         "loaded": symbols_ready,
         "loaded_fresh": sum(1 for e in precomputed_entries.values() if e.get("status") == "ok"),
         "loaded_stale": sum(1 for e in precomputed_entries.values() if e.get("status") == "stale"),
         "pending": symbols_warming,
         "error": symbols_error,
     }

     # Next run from scheduler
     scheduler = getattr(request.app.state, "scheduler", None)
     next_run_iso = None
     if scheduler:
         task = scheduler.registry.get_task("best_options")
         if task:
             next_run_iso = task.next_run.isoformat() if task.next_run else None

     return JSONResponse({
         "schema_version": result["schema_version"],
         "generated_at": result["generated_at"],
         "side": side,
         "filters": {**result["filters"], "sort": sort, "dir": sort_dir, "offset": offset, "limit": limit},
         "symbols": {"counts": counts, "next_run": next_run_iso},
         "rows": rows,
         "nearest_miss": section["nearest_miss"],
         "pagination": pagination,
     })


# ===========================================================================
# REST API — Best Options Single-Symbol Refresh
# ===========================================================================

@app.post("/api/symbols/{symbol}/best-options/refresh")
async def api_symbol_best_options_refresh(request: Request, symbol: str):
    """Targeted single-symbol Best Options refresh (Symbol Detail Refresh
    button, §9b). Forces a chain refresh (best-effort), then runs the
    evaluator and publishes one entry atomically.

    Non-blocking POST: returns 202 Accepted with a task status. Client
    polls GET /api/symbols/{symbol}/best-options for `cache.refreshing` to
    become false and the new result to appear.

    In-flight protection: duplicate requests for the same symbol return 409.
    """
    try:
        cosmos = _get_cosmos(request)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)

    sym_upper = symbol.upper()
    sym_doc = cosmos.get_symbol(sym_upper)
    if not sym_doc:
        return JSONResponse({"error": f"Symbol {sym_upper} not found"}, status_code=404)

    from src.best_options_precompute import refresh_symbol, _symbol_refresh_tasks

    # Check if already in flight
    async with _refresh_tasks_lock if '_refresh_tasks_lock' in dir() else asyncio.Lock():
        if sym_upper in _symbol_refresh_tasks:
            existing_task = _symbol_refresh_tasks[sym_upper]
            if not existing_task.done():
                return JSONResponse(
                    {"message": "Refresh already in progress", "symbol": sym_upper},
                    status_code=409,
                )

    # Start refresh (non-blocking)
    asyncio.create_task(refresh_symbol(sym_upper, cosmos))

    return JSONResponse({"message": "Refresh started", "symbol": sym_upper}, status_code=202)


@app.post("/api/trigger/best_options")
async def api_trigger_best_options(request: Request):
    """Manual full-cycle Best Options precompute trigger (Settings Run Now,
    §7). Enqueues the task in the scheduler's worker queue.

    Returns 200 immediately; the cycle runs asynchronously on the scheduler
    worker thread.
    """
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        return JSONResponse({"error": "Scheduler not available"}, status_code=503)

    task = scheduler.registry.get_task("best_options")
    if task is None:
        return JSONResponse({"error": "Best Options task not registered"}, status_code=404)

    # Trigger the task (scheduler registry pattern)
    result = scheduler.registry.trigger_task_now("best_options", trigger="manual")

    if not result.get("success"):
        return JSONResponse({"error": result.get("message", "Failed to trigger task")}, status_code=400)

    return JSONResponse({"message": result.get("message", "Best Options precompute triggered")})


@app.get("/api/health/best-options")
async def api_health_best_options(request: Request):
    """Best Options cache health observability (§5). Always returns HTTP 200
    — an empty cache is non-fatal by design (post-restart, or split-process
    before first cycle).

    Returns the snapshot metadata (generation, cycle timing, truncated flag,
    status counts) plus readiness state.
    """
    from src.best_options_cache import get_best_options_cache

    cache = get_best_options_cache()
    snapshot = cache.snapshot()

    generation = snapshot.get("generation", 0)
    if generation == 0:
        status = "empty"
        message = "Cache never populated (post-restart or pre-first-cycle)"
    else:
        status = "ok"
        message = None

    # Get scheduler next run
    scheduler = getattr(request.app.state, "scheduler", None)
    next_run_iso = None
    if scheduler:
        task = scheduler.registry.get_task("best_options")
        if task:
            next_run_iso = task.next_run.isoformat() if task.next_run else None

    return JSONResponse({
        "status": status,
        "message": message,
        "generation": generation,
        "cycle_started_at": snapshot.get("cycle_started_at"),
        "cycle_finished_at": snapshot.get("cycle_finished_at"),
        "cycle_duration_seconds": snapshot.get("cycle_duration_seconds"),
        "trigger": snapshot.get("trigger"),
        "truncated": snapshot.get("truncated", False),
        "counts": snapshot.get("counts", {}),
        "next_run": next_run_iso,
    })


class ValidateContractRequest(BaseModel):
    """Contract validation request matching frontend payload."""
    symbol: str = Field(..., description="Ticker symbol")
    side: str = Field(..., description="'call' or 'put'")
    strike: float = Field(..., description="Exact strike price")
    expiration: str = Field(..., description="ISO expiration date (YYYY-MM-DD)")
    source: str = Field(..., description="'best_options' or 'options_screener'")
    displayed_snapshot: Optional[dict] = Field(None, description="Optional snapshot of displayed data")


@app.post("/api/best-options/validate")
async def api_best_options_validate(
    request: Request,
    payload: ValidateContractRequest,
):
    """Start exact-contract validation (Best Option Validate flow).

    Asynchronous POST returning 202 Accepted with run_id for status polling.

    Request body matches ValidateContractRequest schema sent by frontend:
    - symbol: Ticker symbol
    - side: "call" or "put"
    - strike: Exact strike price
    - expiration: ISO expiration date (YYYY-MM-DD)
    - source: "best_options" or "options_screener"
    - displayed_snapshot: Optional snapshot of displayed data (can be null or omitted)

    Returns:
        202 Accepted: {status: "accepted", run_id, started_at, status_url}
        409 Conflict: {status: "duplicate", run_id, started_at}
        429 Too Many Requests: {status: "max_concurrency", retry_after}
        400 Bad Request: {status: "error", message}
    """
    try:
        cosmos = _get_cosmos(request)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)

    # Get agent runner
    from src.agent_runner import AgentRunner
    from src.context import ContextProvider
    from src.contract_validation_integration import start_validation

    # Initialize agent runner (reuse app state if available)
    agent_runner = getattr(request.app.state, "agent_runner", None)
    if agent_runner is None:
        # Create a new one
        llm_config = {"provider": "azure"}  # Will use environment vars
        agent_runner = AgentRunner(
            llm=llm_config,
            model="gpt-5.4-mini",
            telegram_notifier=None,
        )

    context_provider = ContextProvider(cosmos)

    # Start validation
    result = await start_validation(
        symbol=payload.symbol,
        side=payload.side,
        strike=payload.strike,
        expiration=payload.expiration,
        source=payload.source,
        displayed_snapshot=payload.displayed_snapshot,
        cosmos=cosmos,
        agent_runner=agent_runner,
        context_provider=context_provider,
    )

    # Map status to HTTP status code
    if result["status"] == "accepted":
        return JSONResponse(result, status_code=202)
    elif result["status"] == "duplicate":
        return JSONResponse(result, status_code=409)
    elif result["status"] == "max_concurrency":
        return JSONResponse(result, status_code=429)
    else:  # error
        return JSONResponse(result, status_code=400)


@app.get("/api/best-options/validate/{run_id}")
async def api_best_options_validate_status(request: Request, run_id: str):
    """Get contract validation status (polling endpoint).

    Returns:
        200 OK: {status: "in_progress"|"completed"|"not_found", ...}
    """
    try:
        cosmos = _get_cosmos(request)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)

    from src.contract_validation_integration import get_validation_status

    result = await get_validation_status(run_id, cosmos)

    if result["status"] == "not_found":
        return JSONResponse(result, status_code=404)

    return JSONResponse(result)


@app.get("/api/debug/agent-chain/{symbol}")
async def api_debug_agent_chain(request: Request, symbol: str,
                                  option_type: str = Query(default="call"),
                                  strike: float = Query(default=None),
                                  expiration: str = Query(default=None),
                                  roll_type: str = Query(default=None)):
    """Return the exact options chain text that agents receive, with all pipeline filters applied."""
    try:
        cosmos = _get_cosmos(request)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)

    doc = cosmos.get_symbol(symbol.upper())
    if not doc:
        return JSONResponse({"error": f"Symbol {symbol} not found"},
                            status_code=404)

    from src.options_chain_filters import (
        filter_options_chain_by_type,
        filter_options_chain_by_delta,
        filter_options_chain_for_position, filter_options_chain_by_roll_direction,
        format_roll_candidates_table, get_contract,
    )
    from src.options_math import executable_buyback_ask
    from src.yfinance_data_provider import OPTIONS_CHAIN_SCHEMA_DESCRIPTION
    import json as _json

    provider = getattr(request.app.state, "yf_provider", None)
    if provider is None:
        return JSONResponse({"error": "Data provider not initialized"}, status_code=503)

    sym_upper = symbol.upper()

    try:
        data = await provider.fetch_all(sym_upper)
        raw = data.get("options_chain", "{}")
        structured = _json.loads(raw) if isinstance(raw, str) else raw
    except Exception as e:
        return JSONResponse({"error": f"Failed to fetch: {e}", "symbol": sym_upper}, status_code=500)

    if not structured.get("calls") and not structured.get("puts"):
        return JSONResponse(
            {"error": "No options chain data available", "symbol": sym_upper},
            status_code=404,
        )

    # Mirrors the production agent prompt pipeline exactly (this endpoint's
    # whole purpose): apply the same agent-facing normalization boundary
    # before any pipeline stage runs, so what this debug view shows is
    # byte-for-byte what the agent actually sees.
    from src.options_chain_cache import apply_agent_view
    structured = apply_agent_view(structured)

    # Helper to count expirations/contracts for one side of a chain
    def _chain_stats(chain_data, opt_type):
        side = "calls" if opt_type == "call" else "puts"
        bucket = chain_data.get(side, {})
        n_exp = len(bucket)
        n_con = sum(len(strikes) for strikes in bucket.values())
        return n_exp, n_con

    # --- Stage 0: Type filter (calls or puts only) ---
    type_filtered = filter_options_chain_by_type(structured, option_type)
    s0_exp, s0_con = _chain_stats(type_filtered, option_type)

    pipeline = {
        "stage_0_type_filtered": {
            "num_expirations": s0_exp,
            "num_contracts": s0_con,
            "text": OPTIONS_CHAIN_SCHEMA_DESCRIPTION + "\n" + _json.dumps(type_filtered, indent=2),
        },
    }

    # --- Stage 1: Delta filter (applied to type-filtered chain) ---
    delta_filtered = filter_options_chain_by_delta(type_filtered)
    s1_exp, s1_con = _chain_stats(delta_filtered, option_type)

    pipeline["stage_1_delta_filtered"] = {
        "num_expirations": s1_exp,
        "num_contracts": s1_con,
        "text": OPTIONS_CHAIN_SCHEMA_DESCRIPTION + "\n" + _json.dumps(delta_filtered, indent=2),
    }

    # --- Underlying price (from technicals data) ---
    underlying_price = 0.0
    underlying_price_source = "not available"
    try:
        tech_raw = data.get("technicals", "{}")
        tech_data = _json.loads(tech_raw) if isinstance(tech_raw, str) else tech_raw
        px = tech_data.get("price")
        if px is not None:
            underlying_price = float(px)
            underlying_price_source = "yfinance technicals"
    except (ValueError, TypeError, AttributeError):
        pass

    # --- Stage 2: Position filter (±15 strikes) ---
    position_filtered = None
    if strike is not None:
        position_filtered = filter_options_chain_for_position(
            delta_filtered, strike, option_type,
        )
        position_filtered = filter_options_chain_by_delta(position_filtered)
        s2_exp, s2_con = _chain_stats(position_filtered, option_type)
        pipeline["stage_2_position_filtered"] = {
            "num_expirations": s2_exp,
            "num_contracts": s2_con,
            "text": OPTIONS_CHAIN_SCHEMA_DESCRIPTION + "\n" + _json.dumps(position_filtered, indent=2),
        }

    # --- Stage 3: Direction filter ---
    direction_filtered = None
    if strike is not None and expiration and roll_type and position_filtered is not None:
        direction_filtered = filter_options_chain_by_roll_direction(
            position_filtered,
            current_strike=float(strike),
            current_expiration=expiration,
            roll_type=roll_type,
            option_type=option_type,
        )
        s3_exp, s3_con = _chain_stats(direction_filtered, option_type)
        pipeline["stage_3_direction_filtered"] = {
            "num_expirations": s3_exp,
            "num_contracts": s3_con,
            "text": _json.dumps(direction_filtered, indent=2),
        }

    # --- Stage 4: Pre-computed candidate table ---
    if direction_filtered is not None and position_filtered is not None:
        # Get the current contract from the RAW (unfiltered) chain — not from
        # position_filtered/delta_filtered. A held contract's own delta can
        # legitimately fall outside the standard candidate band (e.g. a
        # stale/degenerate IV yfinance returns while the market is closed),
        # which means the delta filter drops it before any later stage ever
        # sees it, even though it objectively exists in the chain data. This
        # mirrors the "capture reference before filters" pattern used for the
        # production monitor pipeline in agent_runner.py.
        current_contract_ref = get_contract(
            structured, float(strike), expiration, option_type,
        )
        bb_cost = None
        if current_contract_ref is not None:
            bb_cost = executable_buyback_ask(current_contract_ref.get("ask"))

        candidate_table = format_roll_candidates_table(
            chain=direction_filtered,
            current_strike=float(strike),
            current_expiration=expiration,
            option_type=option_type,
            underlying_price=underlying_price,
            roll_type=roll_type,
            buyback_cost=bb_cost,
            current_contract=current_contract_ref,
        )
        pipeline["stage_4_candidate_table"] = {
            "text": candidate_table,
        }

    # Build position context (when params provided)
    position_context = None
    if strike is not None:
        position_context = {
            "strike": strike,
            "expiration": expiration,
            "roll_type": roll_type,
            "underlying_price": underlying_price,
            "underlying_price_source": underlying_price_source,
        }

    result = {
        "symbol": sym_upper,
        "option_type": option_type,
        "pipeline": pipeline,
    }
    if position_context:
        result["position_context"] = position_context

    return JSONResponse(result)


@app.get("/api/symbols/{symbol}/fetch-preview")
async def api_fetch_preview(request: Request, symbol: str):
    """Fetch raw market data for a symbol and return as JSON.

    Always forces a fresh fetch (debug endpoint).
    """
    try:
        cosmos = _get_cosmos(request)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)

    doc = cosmos.get_symbol(symbol.upper())
    if not doc:
        return JSONResponse({"error": f"Symbol {symbol} not found"},
                            status_code=404)

    provider = getattr(request.app.state, "yf_provider", None)
    if provider is None:
        return JSONResponse({"error": "Data provider not initialized"}, status_code=503)

    try:
        import time as _time
        t0 = _time.monotonic()
        data = await provider.fetch_all(symbol.upper(), force_refresh=True)
        elapsed = _time.monotonic() - t0
    except Exception as e:
        logger.exception("Fetch preview failed for %s", symbol)
        return JSONResponse({"error": f"Fetch failed: {e}"}, status_code=500)

    resources = {}
    for key in ("overview", "technicals", "forecast", "dividends", "options_chain"):
        text = data.get(key, "")
        resources[key] = {
            "text": text,
            "size": len(text),
            "cached": False,
            "duration_seconds": round(elapsed, 2),
        }

    return JSONResponse({
        "symbol": symbol.upper(),
        "resources": resources,
    })


@app.get("/api/cache/status")
async def cache_status(request: Request):
    """Return yfinance provider cache statistics."""
    provider = getattr(request.app.state, "yf_provider", None)
    if provider is None:
        return JSONResponse({"total_entries": 0, "symbols": []})
    cache = provider._cache
    symbols = list(cache.keys())
    info = {
        "total_entries": len(symbols),
        "symbols": symbols,
        "detail": {
            sym: {"age_seconds": round(time.monotonic() - entry["timestamp"], 1)}
            for sym, entry in cache.items()
        },
    }
    return JSONResponse(info)


@app.delete("/api/cache")
async def cache_clear(request: Request):
    """Clear yfinance provider cache. Pass ``{"symbol": "MSFT"}`` to clear
    a single symbol, or empty body to clear everything."""
    provider = getattr(request.app.state, "yf_provider", None)
    if provider is None:
        return JSONResponse({"cleared": "none"})
    try:
        body = await request.json()
    except Exception:
        body = {}
    sym = body.get("symbol")
    if sym:
        provider._cache.pop(sym, None)
        return JSONResponse({"cleared": sym})
    provider._cache.clear()
    return JSONResponse({"cleared": "all"})

# ===========================================================================
# REST API — Create Activity from Recommendation
# ===========================================================================

@app.post("/api/activities/from-recommendation")
async def api_create_activity_from_recommendation(request: Request):
    """Create a new activity based on a supervisor or alpha agent recommendation.

    The user validates and edits all fields before submitting.
    The new activity is linked back to the source activity.
    """
    try:
        cosmos = _get_cosmos(request)
        body = await request.json()

        source_activity_id = body.get("source_activity_id")
        source_agent = body.get("source_agent")  # "supervisor" or "alpha_advisor"
        activity_data = body.get("activity_data", {})
        include_other_agent = body.get("include_other_agent", False)

        if not source_activity_id or not source_agent:
            return JSONResponse(
                {"error": "source_activity_id and source_agent are required"},
                status_code=400,
            )
        if source_agent not in ("supervisor", "alpha_advisor"):
            return JSONResponse(
                {"error": "source_agent must be 'supervisor' or 'alpha_advisor'"},
                status_code=400,
            )

        # Validate required fields
        required = ["activity", "strike", "expiration", "premium"]
        missing = [f for f in required if not activity_data.get(f)]
        if missing:
            return JSONResponse(
                {"error": f"Missing required fields: {', '.join(missing)}"},
                status_code=400,
            )

        source_activity = cosmos.get_activity_by_id(source_activity_id)
        if source_activity is None:
            return JSONResponse(
                {"error": f"Source activity {source_activity_id} not found"},
                status_code=404,
            )

        symbol = source_activity["symbol"]
        agent_type = source_activity["agent_type"]

        # Build recommendation text from the source agent's view
        recommendation = ""
        if source_agent == "alpha_advisor" and source_activity.get("alpha_view"):
            av = source_activity["alpha_view"]
            recommendation = (av.get("alternative", {}).get("action", "")
                              or av.get("one_liner", ""))
        elif source_agent == "supervisor":
            sv = (source_activity.get("supervisor_view")
                  or {})
            recommendation = sv.get("one_liner", "")

        # Clone the source activity, then overlay user edits
        # Exclude CosmosDB system fields and identity fields (will be reassigned)
        exclude_keys = {"id", "_rid", "_self", "_etag", "_attachments", "_ts",
                        "doc_type", "ttl"}
        new_activity = {k: v for k, v in source_activity.items()
                        if k not in exclude_keys}

        # Strip the recommending agent's view from the clone
        # Optionally strip the other agent's view too (unless user checked "include")
        if source_agent == "supervisor":
            new_activity.pop("supervisor_view", None)
            if not include_other_agent:
                new_activity.pop("alpha_view", None)
        elif source_agent == "alpha_advisor":
            new_activity.pop("alpha_view", None)
            if not include_other_agent:
                new_activity.pop("supervisor_view", None)

        # Apply user overrides
        new_activity["activity"] = activity_data["activity"]
        new_activity["strike"] = float(activity_data["strike"])
        new_activity["expiration"] = activity_data["expiration"]
        new_activity["premium"] = float(activity_data["premium"])
        new_activity["is_alert"] = True

        if activity_data.get("confidence"):
            new_activity["confidence"] = activity_data["confidence"]
        # Use the agent's finding as reason; fall back to the recommendation text
        if activity_data.get("reason"):
            new_activity["reason"] = activity_data["reason"]
        elif recommendation:
            new_activity["reason"] = recommendation
        if activity_data.get("iv"):
            new_activity["iv"] = float(activity_data["iv"])
        if activity_data.get("risk_rating") is not None:
            try:
                new_activity["risk_rating"] = int(activity_data["risk_rating"])
            except (ValueError, TypeError):
                pass

        new_activity["created_from"] = {
            "source_activity_id": source_activity_id,
            "source_agent": source_agent,
            "recommendation": recommendation,
        }

        doc = cosmos.write_activity(symbol, agent_type, new_activity)
        return JSONResponse(_clean_doc(doc), status_code=201)

    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ===========================================================================
# REST API — Activity Delete
# ===========================================================================

@app.get("/api/activities/{activity_id}")
async def api_activity_detail(request: Request, activity_id: str):
    """JSON detail for a single activity (mirrors the /activities/{id} page)."""
    try:
        cosmos = _get_cosmos(request)
        activity = cosmos.get_activity_by_id(activity_id)
        if not activity:
            return JSONResponse({"error": "Activity not found"},
                                status_code=404)
        symbol = activity.get("symbol", "")
        agent_type = activity.get("agent_type", "")
        agent_label = AGENT_TYPES.get(agent_type, {}).get("label", agent_type)
        sym_doc = cosmos.get_symbol(symbol) if symbol else None
        display_name = sym_doc["display_name"] if sym_doc else symbol
        return JSONResponse({
            "activity": _clean_doc(activity),
            "symbol": symbol,
            "display_name": display_name,
            "agent_type": agent_type,
            "agent_label": agent_label,
            "is_alert": activity.get("is_alert", False),
        })
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/activities/{activity_id}")
async def api_delete_activity(request: Request, activity_id: str):
    try:
        cosmos = _get_cosmos(request)
        activity = cosmos.get_activity_by_id(activity_id)
        if not activity:
            return JSONResponse({"error": "Activity not found"},
                                status_code=404)
        symbol = activity["symbol"]
        cosmos.delete_activity(activity_id, symbol)
        return JSONResponse({"ok": True})
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ===========================================================================
# REST API — Activity Chat
# ===========================================================================

@app.post("/api/activities/{activity_id}/chat")
async def api_activity_chat(request: Request, activity_id: str):
    """Chat endpoint for discussing a specific activity with an LLM.

    Provides LIVE context: current option chain (filtered for position),
    current technical analysis, the historical activity record, and linked position.
    """
    try:
        # Parse request body
        body = await request.json()
        user_message = body.get("message", "").strip()
        history = body.get("history", [])

        if not user_message:
            return JSONResponse({"error": "Message cannot be empty"}, status_code=400)

        # Load activity and related data
        cosmos = _get_cosmos(request)
        activity = cosmos.get_activity_by_id(activity_id)
        if not activity:
            return JSONResponse({"error": "Activity not found"}, status_code=404)

        symbol = activity.get("symbol", "")
        position_id = activity.get("position_id")

        # Load position from symbol config
        position_data = "(no linked position)"
        sym_doc = cosmos.get_symbol(symbol)
        if sym_doc and position_id:
            positions = sym_doc.get("positions", [])
            matched = [p for p in positions if p.get("position_id") == position_id]
            if matched:
                position_data = json.dumps(matched[0], indent=2, default=str)

        # Get CURRENT option chain
        from src.options_chain_cache import get_options_chain_cache
        from src.options_chain_filters import filter_options_chain_for_position

        chain_data = "(option chain unavailable)"
        try:
            cache = get_options_chain_cache()
            full_chain = cache.get_or_load(symbol)
            # Parse the JSON string to dict
            chain_dict = json.loads(full_chain) if isinstance(full_chain, str) else full_chain
            # Agent-facing normalization boundary (Danny's zero-free
            # decision §2.2): applied to the raw stored/cached chain
            # *before* `filter_options_chain_for_position` runs below, so
            # the filter's own `current_position` addition survives
            # (`to_agent_view`'s output is strictly `{symbol, timestamp,
            # calls, puts}` and would otherwise silently drop it if
            # applied after).
            from src.options_chain_cache import apply_agent_view
            chain_dict = apply_agent_view(chain_dict)

            # Filter for position if we have one
            if sym_doc and position_id and matched:
                pos = matched[0]
                strike = pos.get("strike")
                option_type = pos.get("type", "").upper()
                if strike:
                    filtered_chain = filter_options_chain_for_position(
                        chain_dict,
                        current_strike=strike,
                        option_type=option_type,
                        num_strikes=10
                    )
                    chain_data = json.dumps(filtered_chain, indent=2, default=str)
                else:
                    chain_data = json.dumps(chain_dict, indent=2, default=str)
            else:
                # No position — provide compact chain
                chain_data = json.dumps(chain_dict, indent=2, default=str)
        except Exception as e:
            logger.warning("Failed to load option chain for %s: %s", symbol, e)
            chain_data = f"(option chain unavailable: {e})"

        # Get CURRENT technical analysis (best-effort — never fatal to the chat)
        technical_data = "(technical analysis unavailable)"
        try:
            from src.technical_analysis_agent import run_technical_analysis
            from src.config import Config
            from src.agent_runner import AgentRunner

            # Query most recent technical_analysis doc
            doc = cosmos.get_latest_technical_analysis(symbol)

            if doc:
                ts = doc.get("timestamp", "unknown")
                analysis = doc.get("analysis", "")
                technical_data = f"[Generated at: {ts}]\n\n{analysis}"
            else:
                # No persisted doc — generate fresh (best-effort)
                logger.info("No persisted technical analysis for %s; generating fresh", symbol)
                cfg = Config()
                runner = AgentRunner(
                    llm=cfg.llm_config(),
                    model=cfg.model_deployment,
                    function_llms=cfg.function_llm_configs(),
                )
                result = await run_technical_analysis(cfg, runner, cosmos, symbol)
                if "analysis" in result:
                    technical_data = f"[Generated fresh]\n\n{result['analysis']}"
                else:
                    technical_data = "(technical analysis generation failed)"
        except Exception as e:
            logger.warning("Failed to load technical analysis for %s: %s", symbol, e, exc_info=True)
            technical_data = f"(technical analysis unavailable: {e})"

        # Build conversation history string
        conversation_str = "(none)"
        if history:
            lines = []
            for turn in history:
                role = turn.get("role", "unknown")
                content = turn.get("content", "")
                lines.append(f"{role.upper()}: {content}")
            conversation_str = "\n\n".join(lines)

        # Build the complete prompt with exact headers
        message = f"""=== AGENT DECISION (historical, exact — what the agents actually decided) ===
{json.dumps(activity, indent=2, default=str)}

=== POSITION ===
{position_data}

=== CURRENT MARKET DATA (LIVE NOW — NOT what the agents used) ===
{chain_data}

Technical Analysis:
{technical_data}

=== CONVERSATION SO FAR ===
{conversation_str}

=== USER QUESTION ===
{user_message}"""

        # Call LLM via Agent Framework
        from agent_framework import Agent
        from src.llm import create_async_chat_client
        from src.activity_chat_instructions import get_activity_chat_instructions
        from src.config import Config

        cfg = Config()
        model = cfg.activity_chat_model
        client = create_async_chat_client(
            model, _function_llm_config(cfg, "activity_chat")
        )
        agent = Agent(
            client=client,
            name="ActivityChat",
            instructions=get_activity_chat_instructions()
        )

        result = await agent.run(message)
        answer = result.text or str(result)

        return JSONResponse({"answer": answer})

    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    except Exception as e:
        logger.exception("Activity chat error for %s", activity_id)
        return JSONResponse({"error": str(e)}, status_code=500)


# ===========================================================================
# Page Routes — Activity Detail
# ===========================================================================



# ===========================================================================
# Settings - Split Views
# ===========================================================================

def _build_settings_config_context(
    request: Request,
    cosmos,
    saved: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build template context for the configuration settings page."""
    cosmos_settings = _load_settings_from_cosmos(cosmos)
    if cosmos_settings:
        config = cosmos_settings
    else:
        config = _load_config()

    # Get scheduler tasks from registry (if available)
    scheduler = getattr(request.app.state, "scheduler", None)
    scheduler_tasks = []
    if scheduler and hasattr(scheduler, "registry"):
        scheduler_tasks = scheduler.registry.get_all_task_metadata()

    # Build task lookup for backward compatibility with existing template variables
    tasks_by_name = {t["name"]: t for t in scheduler_tasks}

    # Telegram settings (not scheduler-related)
    telegram_cfg = config.get("telegram", {})
    telegram_enabled = telegram_cfg.get("enabled", False)
    telegram_bot_token = telegram_cfg.get("bot_token", "")
    telegram_chat_id = telegram_cfg.get("chat_id", "")

    # Resolve env vars for display
    if telegram_bot_token.startswith("${"):
        telegram_bot_token = _resolve_env(telegram_bot_token)
    if telegram_chat_id.startswith("${"):
        telegram_chat_id = _resolve_env(telegram_chat_id)

    # Extract per-task extra config (for tasks with has_extra_config=True)
    summary_cfg = config.get("summary_agent", {})
    summary_activity_count = summary_cfg.get("activity_count", 3)

    dgi_cfg = config.get("dgi_screener", {})
    dgi_top_n = dgi_cfg.get("top_n", 40)
    dgi_symbols = dgi_cfg.get("symbols", "")

    banner_cfg = config.get("banner_agent", {})
    banner_max_items = banner_cfg.get("max_items", 10)

    best_options_cfg = config.get("best_options_scheduler", {})
    best_options_run_on_startup = best_options_cfg.get("run_on_startup", True)

    # Helper to resolve last_run from Cosmos when in-memory value is None
    # (makes last_run restart-durable by falling back to persisted timestamps)
    def get_persisted_last_run(task_name: str) -> str:
        """Resolve last_run from CosmosDB for a task when in-memory value is None."""
        if not cosmos:
            return ""

        try:
            if task_name == "monitor_agents":
                # Monitoring: most recent activity timestamp
                all_activities = cosmos.get_all_activities(limit=1)
                if all_activities:
                    timestamp_str = all_activities[0].get("timestamp", "")
                    if timestamp_str:
                        return timestamp_str

            elif task_name == "summary_agent":
                # Summary: most recent agent_notes timestamp from symbol configs
                symbols = cosmos.list_symbols()
                timestamps = []
                for sym in symbols:
                    notes = sym.get("agent_notes", [])
                    if isinstance(notes, list):
                        for note in notes:
                            if isinstance(note, dict) and note.get("timestamp"):
                                timestamps.append(note["timestamp"])
                if timestamps:
                    return max(timestamps)

            elif task_name == "dgi_screener":
                # DGI: most recent last_updated from dgi_top entries
                dgi_entries = cosmos.get_dgi_top()
                timestamps = [e.get("last_updated", "") for e in dgi_entries if e.get("last_updated")]
                if timestamps:
                    return max(timestamps)

            elif task_name == "banner_agent":
                # Banner: generated_at from dashboard_banner doc
                banner_doc = cosmos.get_banner()
                if banner_doc and banner_doc.get("generated_at"):
                    return banner_doc["generated_at"]

            elif task_name == "plan_monitor":
                # Plan Monitor: most recent plan note timestamp
                plans = cosmos.get_plans()
                timestamps = []
                for plan in plans:
                    notes = plan.get("notes", [])
                    if isinstance(notes, list):
                        for note in notes:
                            if isinstance(note, dict) and note.get("timestamp"):
                                timestamps.append(note["timestamp"])
                if timestamps:
                    return max(timestamps)

            elif task_name == "options_chain":
                # Options Chain: no persisted timestamp available (in-memory only)
                return ""

            elif task_name == "calendar_sync":
                # Calendar: most recent updated_at from calendar events
                events = cosmos.get_calendar_events()
                timestamps = [e.get("updated_at", "") for e in events if e.get("updated_at")]
                if timestamps:
                    return max(timestamps)

            elif task_name == "portfolio_enrichment":
                # Portfolio Enrichment: most recent updated_at from enriched symbol configs
                symbols = cosmos.list_symbols()
                timestamps = []
                for sym in symbols:
                    enrichment = sym.get("enrichment")
                    if enrichment:
                        # The parent doc's updated_at is touched when enrichment is saved
                        ts = sym.get("updated_at")
                        if ts:
                            timestamps.append(ts)
                if timestamps:
                    return max(timestamps)

            elif task_name == "best_options":
                # Best Options: cycle_finished_at from the cache snapshot
                try:
                    from src.best_options_cache import get_best_options_cache
                    cache = get_best_options_cache()
                    snapshot = cache.snapshot()
                    cycle_finished = snapshot.get("cycle_finished_at")
                    if cycle_finished:
                        return cycle_finished
                except Exception:
                    pass
                return ""

        except Exception as exc:
            logger.warning("Failed to resolve persisted last_run for %s: %s", task_name, exc)

        return ""

    # Helper to format timestamps for display
    def fmt_time(iso_str):
        if not iso_str:
            return ""
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            return _format_time(dt)
        except Exception:
            return iso_str

    # Helper to get raw ISO timestamp (for client-side relative time calculations)
    def to_iso(iso_str):
        """Return normalized ISO string with timezone, or empty string."""
        if not iso_str:
            return ""
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            # Return ISO format with timezone (UTC)
            return dt.astimezone(timezone.utc).isoformat()
        except Exception:
            return ""

    # Unified helper to get last_run (prefers in-memory, falls back to persisted)
    def resolve_last_run(task_name: str, in_memory_last_run: str) -> str:
        """Resolve last_run: prefer in-memory, else persisted from Cosmos."""
        if in_memory_last_run:
            return fmt_time(in_memory_last_run)
        # Fall back to persisted timestamp
        persisted = get_persisted_last_run(task_name)
        return fmt_time(persisted)

    # Unified helper to get raw last_run ISO (for client-side relative time)
    def resolve_last_run_iso(task_name: str, in_memory_last_run: str) -> str:
        """Resolve last_run as ISO string: prefer in-memory, else persisted from Cosmos."""
        if in_memory_last_run:
            return to_iso(in_memory_last_run)
        # Fall back to persisted timestamp
        persisted = get_persisted_last_run(task_name)
        return to_iso(persisted)

    # Build backward-compatible individual task variables for template
    # (Until template is refactored to use scheduler_tasks loop)
    monitoring = tasks_by_name.get("monitor_agents", {})
    monitoring_enabled = monitoring.get("enabled", True)
    cron_expr = monitoring.get("cron", "30 9-16/4 * * 1-5")
    monitoring_last_run = resolve_last_run("monitor_agents", monitoring.get("last_run"))
    monitoring_next_run = fmt_time(monitoring.get("next_run"))
    monitoring_last_run_iso = resolve_last_run_iso("monitor_agents", monitoring.get("last_run"))
    monitoring_next_run_iso = to_iso(monitoring.get("next_run"))

    summary = tasks_by_name.get("summary_agent", {})
    summary_enabled = summary.get("enabled", True)
    summary_cron = summary.get("cron", "0 8 * * *")
    summary_last_run = resolve_last_run("summary_agent", summary.get("last_run"))
    summary_next_run = fmt_time(summary.get("next_run"))
    summary_last_run_iso = resolve_last_run_iso("summary_agent", summary.get("last_run"))
    summary_next_run_iso = to_iso(summary.get("next_run"))

    plan_monitor = tasks_by_name.get("plan_monitor", {})
    plan_monitor_enabled = plan_monitor.get("enabled", True)
    plan_monitor_cron = plan_monitor.get("cron", "0 4,16 * * 1-5")
    plan_monitor_last_run = resolve_last_run("plan_monitor", plan_monitor.get("last_run"))
    plan_monitor_next_run = fmt_time(plan_monitor.get("next_run"))
    plan_monitor_last_run_iso = resolve_last_run_iso("plan_monitor", plan_monitor.get("last_run"))
    plan_monitor_next_run_iso = to_iso(plan_monitor.get("next_run"))

    options_chain = tasks_by_name.get("options_chain", {})
    options_chain_enabled = options_chain.get("enabled", True)
    options_chain_cron = options_chain.get("cron", "0 * * * *")
    options_chain_last_run = resolve_last_run("options_chain", options_chain.get("last_run"))
    options_chain_next_run = fmt_time(options_chain.get("next_run"))
    options_chain_last_run_iso = resolve_last_run_iso("options_chain", options_chain.get("last_run"))
    options_chain_next_run_iso = to_iso(options_chain.get("next_run"))

    dgi = tasks_by_name.get("dgi_screener", {})
    dgi_enabled = dgi.get("enabled", True)
    dgi_cron = dgi.get("cron", "0 6 * * 1-5")
    dgi_last_run = resolve_last_run("dgi_screener", dgi.get("last_run"))
    dgi_next_run = fmt_time(dgi.get("next_run"))
    dgi_last_run_iso = resolve_last_run_iso("dgi_screener", dgi.get("last_run"))
    dgi_next_run_iso = to_iso(dgi.get("next_run"))

    banner = tasks_by_name.get("banner_agent", {})
    banner_enabled = banner.get("enabled", True)
    banner_cron = banner.get("cron", "0 5 * * *")
    banner_last_run = resolve_last_run("banner_agent", banner.get("last_run"))
    banner_next_run = fmt_time(banner.get("next_run"))
    banner_last_run_iso = resolve_last_run_iso("banner_agent", banner.get("last_run"))
    banner_next_run_iso = to_iso(banner.get("next_run"))

    calendar = tasks_by_name.get("calendar_sync", {})
    calendar_enabled = calendar.get("enabled", True)
    calendar_cron = calendar.get("cron", "0 5 * * 1-5")
    calendar_last_run = resolve_last_run("calendar_sync", calendar.get("last_run"))
    calendar_next_run = fmt_time(calendar.get("next_run"))
    calendar_last_run_iso = resolve_last_run_iso("calendar_sync", calendar.get("last_run"))
    calendar_next_run_iso = to_iso(calendar.get("next_run"))

    pe = tasks_by_name.get("portfolio_enrichment", {})
    pe_enabled = pe.get("enabled", True)
    pe_cron = pe.get("cron", "0 9-17 * * 1-5")
    pe_last_run = resolve_last_run("portfolio_enrichment", pe.get("last_run"))
    pe_next_run = fmt_time(pe.get("next_run"))
    pe_last_run_iso = resolve_last_run_iso("portfolio_enrichment", pe.get("last_run"))
    pe_next_run_iso = to_iso(pe.get("next_run"))

    best_options = tasks_by_name.get("best_options", {})
    best_options_enabled = best_options.get("enabled", True)
    best_options_cron = best_options.get("cron", "5 10-23 * * 1-5")
    best_options_last_run = resolve_last_run("best_options", best_options.get("last_run"))
    best_options_next_run = fmt_time(best_options.get("next_run"))
    best_options_last_run_iso = resolve_last_run_iso("best_options", best_options.get("last_run"))
    best_options_next_run_iso = to_iso(best_options.get("next_run"))

    pf = tasks_by_name.get("price_forecast", {})
    pf_enabled = pf.get("enabled", True)
    pf_cron = pf.get("cron", "0 21 * * 1-5")
    pf_last_run = resolve_last_run("price_forecast", pf.get("last_run"))
    pf_next_run = fmt_time(pf.get("next_run"))
    pf_last_run_iso = resolve_last_run_iso("price_forecast", pf.get("last_run"))
    pf_next_run_iso = to_iso(pf.get("next_run"))
    _pf_cfg = config.get("price_forecast", {})
    pf_band_confidence = _pf_cfg.get("band_confidence", 0.50)
    pf_vol_source = _pf_cfg.get("vol_source", "iv_hv")
    pf_trend_window = _pf_cfg.get("trend_window", 20)
    pf_trend_window_long = _pf_cfg.get("trend_window_long", 40)

    return {
        "request": request,
        "saved": saved or [],
        "server_time": _format_time(_local_now()),
        "scheduler_tasks": scheduler_tasks,  # NEW: unified task list for template
        "monitoring_enabled": monitoring_enabled,
        "cron_expr": cron_expr,
        "telegram_enabled": telegram_enabled,
        "telegram_bot_token": telegram_bot_token,
        "telegram_chat_id": telegram_chat_id,
        "summary_enabled": summary_enabled,
        "summary_cron": summary_cron,
        "summary_activity_count": summary_activity_count,
        "monitoring_last_run": monitoring_last_run,
        "monitoring_next_run": monitoring_next_run,
        "monitoring_last_run_iso": monitoring_last_run_iso,
        "monitoring_next_run_iso": monitoring_next_run_iso,
        "summary_last_run": summary_last_run,
        "summary_next_run": summary_next_run,
        "summary_last_run_iso": summary_last_run_iso,
        "summary_next_run_iso": summary_next_run_iso,
        "plan_monitor_enabled": plan_monitor_enabled,
        "plan_monitor_cron": plan_monitor_cron,
        "plan_monitor_last_run": plan_monitor_last_run,
        "plan_monitor_next_run": plan_monitor_next_run,
        "plan_monitor_last_run_iso": plan_monitor_last_run_iso,
        "plan_monitor_next_run_iso": plan_monitor_next_run_iso,
        "options_chain_enabled": options_chain_enabled,
        "options_chain_cron": options_chain_cron,
        "options_chain_last_run": options_chain_last_run,
        "options_chain_next_run": options_chain_next_run,
        "options_chain_last_run_iso": options_chain_last_run_iso,
        "options_chain_next_run_iso": options_chain_next_run_iso,
        "dgi_enabled": dgi_enabled,
        "dgi_cron": dgi_cron,
        "dgi_top_n": dgi_top_n,
        "dgi_symbols": dgi_symbols,
        "dgi_last_run": dgi_last_run,
        "dgi_next_run": dgi_next_run,
        "dgi_last_run_iso": dgi_last_run_iso,
        "dgi_next_run_iso": dgi_next_run_iso,
        "banner_enabled": banner_enabled,
        "banner_cron": banner_cron,
        "banner_max_items": banner_max_items,
        "banner_last_run": banner_last_run,
        "banner_next_run": banner_next_run,
        "banner_last_run_iso": banner_last_run_iso,
        "banner_next_run_iso": banner_next_run_iso,
        "calendar_enabled": calendar_enabled,
        "calendar_cron": calendar_cron,
        "calendar_last_run": calendar_last_run,
        "calendar_next_run": calendar_next_run,
        "calendar_last_run_iso": calendar_last_run_iso,
        "calendar_next_run_iso": calendar_next_run_iso,
        "pe_enabled": pe_enabled,
        "pe_cron": pe_cron,
        "pe_last_run": pe_last_run,
        "pe_next_run": pe_next_run,
        "pe_last_run_iso": pe_last_run_iso,
        "pe_next_run_iso": pe_next_run_iso,
        "best_options_enabled": best_options_enabled,
        "best_options_cron": best_options_cron,
        "best_options_run_on_startup": best_options_run_on_startup,
        "best_options_last_run": best_options_last_run,
        "best_options_next_run": best_options_next_run,
        "best_options_last_run_iso": best_options_last_run_iso,
        "best_options_next_run_iso": best_options_next_run_iso,
        "pf_enabled": pf_enabled,
        "pf_cron": pf_cron,
        "pf_last_run": pf_last_run,
        "pf_next_run": pf_next_run,
        "pf_last_run_iso": pf_last_run_iso,
        "pf_next_run_iso": pf_next_run_iso,
        "pf_band_confidence": pf_band_confidence,
        "pf_vol_source": pf_vol_source,
        "pf_trend_window": pf_trend_window,
        "pf_trend_window_long": pf_trend_window_long,
    }




def _apply_settings_config(request: Request, cosmos, form) -> List[str]:
    """Core settings-config apply logic, shared by the HTML form route and the
    JSON API route. ``form`` is any object exposing ``.get(key, default)``
    (Starlette ``FormData`` for the form route, or a small dict-like wrapper for
    JSON). Booleans are represented as the string ``"true"`` when set."""
    saved: List[str] = []

    # Monitoring agent enabled toggle
    monitoring_enabled = form.get("monitoring_enabled") == "true"

    # Cron schedule
    new_cron = str(form.get("cron_expr", "")).strip()
    if new_cron:
        try:
            croniter(new_cron)

            # Update CosmosDB first
            if cosmos:
                cosmos_settings = _load_settings_from_cosmos(cosmos) or {}
                cosmos_settings.setdefault("scheduler", {})
                cosmos_settings["scheduler"]["cron"] = new_cron
                cosmos_settings["scheduler"]["enabled"] = monitoring_enabled
                _save_settings_to_cosmos(cosmos, cosmos_settings)

            # Also update config.yaml for backward compat
            config = _load_config()
            config.setdefault("scheduler", {})
            config["scheduler"]["cron"] = new_cron
            config["scheduler"]["enabled"] = monitoring_enabled
            _write_config(config)
            saved.append("Cron schedule")

            scheduler = getattr(request.app.state, "scheduler", None)
            if scheduler is not None:
                scheduler.reschedule(new_cron)
        except (ValueError, KeyError):
            pass

    # Telegram settings
    telegram_enabled = form.get("telegram_enabled") == "true"
    telegram_bot_token = str(form.get("telegram_bot_token", "")).strip()
    telegram_chat_id = str(form.get("telegram_chat_id", "")).strip()

    # Update CosmosDB first
    if cosmos:
        cosmos_settings = _load_settings_from_cosmos(cosmos) or {}
        cosmos_settings.setdefault("telegram", {})
        cosmos_settings["telegram"]["enabled"] = telegram_enabled
        if telegram_bot_token:
            cosmos_settings["telegram"]["bot_token"] = telegram_bot_token
        if telegram_chat_id:
            cosmos_settings["telegram"]["chat_id"] = telegram_chat_id
        _save_settings_to_cosmos(cosmos, cosmos_settings)

    # Also update config.yaml for backward compat
    config = _load_config()
    config.setdefault("telegram", {})
    config["telegram"]["enabled"] = telegram_enabled
    if telegram_bot_token:
        config["telegram"]["bot_token"] = telegram_bot_token
    if telegram_chat_id:
        config["telegram"]["chat_id"] = telegram_chat_id
    _write_config(config)
    saved.append("Telegram settings")

    # Summary agent settings
    summary_enabled = form.get("summary_enabled") == "true"
    summary_cron = str(form.get("summary_cron", "0 8 * * *")).strip()
    summary_activity_count_str = str(form.get("summary_activity_count", "3")).strip()
    try:
        summary_activity_count = int(summary_activity_count_str)
        summary_activity_count = max(1, min(10, summary_activity_count))  # Clamp to 1-10
    except ValueError:
        summary_activity_count = 3

    # Validate cron if provided
    if summary_cron:
        try:
            croniter(summary_cron)
            # Update CosmosDB first
            if cosmos:
                cosmos_settings = _load_settings_from_cosmos(cosmos) or {}
                cosmos_settings.setdefault("summary_agent", {})
                cosmos_settings["summary_agent"]["enabled"] = summary_enabled
                cosmos_settings["summary_agent"]["cron"] = summary_cron
                cosmos_settings["summary_agent"]["activity_count"] = summary_activity_count
                _save_settings_to_cosmos(cosmos, cosmos_settings)

            # Also update config.yaml for backward compat
            config = _load_config()
            config.setdefault("summary_agent", {})
            config["summary_agent"]["enabled"] = summary_enabled
            config["summary_agent"]["cron"] = summary_cron
            config["summary_agent"]["activity_count"] = summary_activity_count
            _write_config(config)
            saved.append("Summary agent")

            # Notify scheduler of change
            scheduler = getattr(request.app.state, "scheduler", None)
            if scheduler is not None:
                scheduler.reschedule_summary(summary_cron)
                scheduler.registry.update_task_enabled("summary_agent", summary_enabled, scheduler.config)
        except (ValueError, KeyError):
            pass

    # Plan monitor settings
    plan_monitor_enabled = form.get("plan_monitor_enabled") == "true"
    plan_monitor_cron = str(form.get("plan_monitor_cron", "0 4,16 * * 1-5")).strip()

    if plan_monitor_cron:
        try:
            croniter(plan_monitor_cron)
            if cosmos:
                cosmos_settings = _load_settings_from_cosmos(cosmos) or {}
                cosmos_settings.setdefault("plan_monitor", {})
                cosmos_settings["plan_monitor"]["enabled"] = plan_monitor_enabled
                cosmos_settings["plan_monitor"]["cron"] = plan_monitor_cron
                _save_settings_to_cosmos(cosmos, cosmos_settings)

            config = _load_config()
            config.setdefault("plan_monitor", {})
            config["plan_monitor"]["enabled"] = plan_monitor_enabled
            config["plan_monitor"]["cron"] = plan_monitor_cron
            _write_config(config)
            saved.append("Plan monitor")

            scheduler = getattr(request.app.state, "scheduler", None)
            if scheduler is not None:
                scheduler.reschedule_plan_monitor(plan_monitor_cron)
                scheduler.registry.update_task_enabled("plan_monitor", plan_monitor_enabled, scheduler.config)
        except (ValueError, KeyError):
            pass

    # Options chain scheduler settings
    options_chain_enabled = form.get("options_chain_enabled") == "true"
    options_chain_cron = str(form.get("options_chain_cron", "0 * * * *")).strip()

    # Validate cron if provided
    if options_chain_cron:
        try:
            croniter(options_chain_cron)
            # Update CosmosDB first
            if cosmos:
                cosmos_settings = _load_settings_from_cosmos(cosmos) or {}
                cosmos_settings.setdefault("options_chain_scheduler", {})
                cosmos_settings["options_chain_scheduler"]["enabled"] = options_chain_enabled
                cosmos_settings["options_chain_scheduler"]["cron"] = options_chain_cron
                _save_settings_to_cosmos(cosmos, cosmos_settings)

            # Also update config.yaml for backward compat
            config = _load_config()
            config.setdefault("options_chain_scheduler", {})
            config["options_chain_scheduler"]["enabled"] = options_chain_enabled
            config["options_chain_scheduler"]["cron"] = options_chain_cron
            _write_config(config)
            saved.append("Options chain scheduler")

            # Notify scheduler of change
            scheduler = getattr(request.app.state, "scheduler", None)
            if scheduler is not None:
                scheduler.reschedule_options_chain(options_chain_cron)
                scheduler.registry.update_task_enabled("options_chain", options_chain_enabled, scheduler.config)
        except (ValueError, KeyError):
            pass

    # DGI screener settings
    dgi_enabled = form.get("dgi_enabled") == "true"
    dgi_cron = str(form.get("dgi_cron", "0 6 * * 1-5")).strip()
    dgi_symbols = str(form.get("dgi_symbols", "")).strip()
    dgi_top_n_str = str(form.get("dgi_top_n", "40")).strip()
    try:
        dgi_top_n = int(dgi_top_n_str)
        dgi_top_n = max(1, min(500, dgi_top_n))
    except ValueError:
        dgi_top_n = 40

    if dgi_cron:
        try:
            croniter(dgi_cron)
            if cosmos:
                cosmos_settings = _load_settings_from_cosmos(cosmos) or {}
                cosmos_settings.setdefault("dgi_screener", {})
                cosmos_settings["dgi_screener"]["enabled"] = dgi_enabled
                cosmos_settings["dgi_screener"]["cron"] = dgi_cron
                cosmos_settings["dgi_screener"]["symbols"] = dgi_symbols
                cosmos_settings["dgi_screener"]["top_n"] = dgi_top_n
                _save_settings_to_cosmos(cosmos, cosmos_settings)

            config = _load_config()
            config.setdefault("dgi_screener", {})
            config["dgi_screener"]["enabled"] = dgi_enabled
            config["dgi_screener"]["cron"] = dgi_cron
            config["dgi_screener"]["symbols"] = dgi_symbols
            config["dgi_screener"]["top_n"] = dgi_top_n
            _write_config(config)
            saved.append("DGI screener")

            scheduler = getattr(request.app.state, "scheduler", None)
            if scheduler is not None:
                scheduler.reschedule_dgi_screener(dgi_cron)
                scheduler.registry.update_task_enabled("dgi_screener", dgi_enabled, scheduler.config)
        except (ValueError, KeyError):
            pass

    # Banner agent settings
    banner_enabled = form.get("banner_enabled") == "true"
    banner_cron = str(form.get("banner_cron", "0 5 * * *")).strip()
    banner_max_items_str = str(form.get("banner_max_items", "10")).strip()
    try:
        banner_max_items = int(banner_max_items_str)
        banner_max_items = max(3, min(20, banner_max_items))
    except ValueError:
        banner_max_items = 10

    if banner_cron:
        try:
            croniter(banner_cron)
            if cosmos:
                cosmos_settings = _load_settings_from_cosmos(cosmos) or {}
                cosmos_settings.setdefault("banner_agent", {})
                cosmos_settings["banner_agent"]["enabled"] = banner_enabled
                cosmos_settings["banner_agent"]["cron"] = banner_cron
                cosmos_settings["banner_agent"]["max_items"] = banner_max_items
                _save_settings_to_cosmos(cosmos, cosmos_settings)

            config = _load_config()
            config.setdefault("banner_agent", {})
            config["banner_agent"]["enabled"] = banner_enabled
            config["banner_agent"]["cron"] = banner_cron
            config["banner_agent"]["max_items"] = banner_max_items
            _write_config(config)
            saved.append("Banner agent")

            scheduler = getattr(request.app.state, "scheduler", None)
            if scheduler is not None:
                scheduler.reschedule_banner(banner_cron)
                scheduler.registry.update_task_enabled("banner_agent", banner_enabled, scheduler.config)
        except (ValueError, KeyError):
            pass

    # Calendar sync settings
    calendar_enabled = form.get("calendar_enabled") == "true"
    calendar_cron = str(form.get("calendar_cron", "0 5 * * 1-5")).strip()

    if calendar_cron:
        try:
            croniter(calendar_cron)
            if cosmos:
                cosmos_settings = _load_settings_from_cosmos(cosmos) or {}
                cosmos_settings.setdefault("calendar_sync", {})
                cosmos_settings["calendar_sync"]["enabled"] = calendar_enabled
                cosmos_settings["calendar_sync"]["cron"] = calendar_cron
                _save_settings_to_cosmos(cosmos, cosmos_settings)

            config = _load_config()
            config.setdefault("calendar_sync", {})
            config["calendar_sync"]["enabled"] = calendar_enabled
            config["calendar_sync"]["cron"] = calendar_cron
            _write_config(config)
            saved.append("Calendar sync")

            scheduler = getattr(request.app.state, "scheduler", None)
            if scheduler is not None:
                scheduler.reschedule_calendar(calendar_cron)
                scheduler.registry.update_task_enabled("calendar_sync", calendar_enabled, scheduler.config)
        except (ValueError, KeyError):
            pass

    # Portfolio enrichment settings
    pe_enabled = form.get("pe_enabled") == "true"
    pe_cron = str(form.get("pe_cron", "0 9-17 * * 1-5")).strip()

    if pe_cron:
        try:
            croniter(pe_cron)
            if cosmos:
                cosmos_settings = _load_settings_from_cosmos(cosmos) or {}
                cosmos_settings.setdefault("portfolio_enrichment", {})
                cosmos_settings["portfolio_enrichment"]["enabled"] = pe_enabled
                cosmos_settings["portfolio_enrichment"]["cron"] = pe_cron
                _save_settings_to_cosmos(cosmos, cosmos_settings)

            config = _load_config()
            config.setdefault("portfolio_enrichment", {})
            config["portfolio_enrichment"]["enabled"] = pe_enabled
            config["portfolio_enrichment"]["cron"] = pe_cron
            _write_config(config)
            saved.append("Portfolio enrichment")

            scheduler = getattr(request.app.state, "scheduler", None)
            if scheduler is not None:
                scheduler.reschedule_portfolio_enrichment(pe_cron)
                scheduler.registry.update_task_enabled("portfolio_enrichment", pe_enabled, scheduler.config)
        except (ValueError, KeyError):
            pass

    # Best Options scheduler settings
    best_options_enabled = form.get("best_options_enabled") == "true"
    best_options_cron = str(form.get("best_options_cron", "5 10-23 * * 1-5")).strip()
    best_options_run_on_startup = form.get("best_options_run_on_startup") == "true"

    if best_options_cron:
        try:
            croniter(best_options_cron)
            if cosmos:
                cosmos_settings = _load_settings_from_cosmos(cosmos) or {}
                cosmos_settings.setdefault("best_options_scheduler", {})
                cosmos_settings["best_options_scheduler"]["enabled"] = best_options_enabled
                cosmos_settings["best_options_scheduler"]["cron"] = best_options_cron
                cosmos_settings["best_options_scheduler"]["run_on_startup"] = best_options_run_on_startup
                _save_settings_to_cosmos(cosmos, cosmos_settings)

            config = _load_config()
            config.setdefault("best_options_scheduler", {})
            config["best_options_scheduler"]["enabled"] = best_options_enabled
            config["best_options_scheduler"]["cron"] = best_options_cron
            config["best_options_scheduler"]["run_on_startup"] = best_options_run_on_startup
            _write_config(config)
            saved.append("Best Options scheduler")

            scheduler = getattr(request.app.state, "scheduler", None)
            if scheduler is not None:
                scheduler.registry.reschedule("best_options", best_options_cron, scheduler.config)
                scheduler.registry.update_task_enabled("best_options", best_options_enabled, scheduler.config)
        except (ValueError, KeyError):
            pass

    # Price forecast settings
    pf_enabled = form.get("pf_enabled") == "true"
    pf_cron = str(form.get("pf_cron", "0 21 * * 1-5")).strip()

    # Model settings (band confidence / vol source / trend window).
    def _pf_conf():
        try:
            v = float(form.get("pf_band_confidence", 0.50))
        except (TypeError, ValueError):
            return 0.50
        return v if v in (0.50, 0.68, 0.80, 0.90, 0.95) else 0.50

    def _pf_vol():
        v = str(form.get("pf_vol_source", "iv_hv")).lower()
        return v if v in ("hv", "ewma", "iv_hv") else "iv_hv"

    def _pf_trend():
        try:
            v = int(form.get("pf_trend_window", 20))
        except (TypeError, ValueError):
            return 20
        return v if 5 <= v <= 120 else 20

    def _pf_trend_long():
        try:
            v = int(form.get("pf_trend_window_long", 40))
        except (TypeError, ValueError):
            return 40
        return v if 5 <= v <= 120 else 40

    pf_band_confidence = _pf_conf()
    pf_vol_source = _pf_vol()
    pf_trend_window = _pf_trend()
    pf_trend_window_long = _pf_trend_long()

    if pf_cron:
        try:
            croniter(pf_cron)
            if cosmos:
                cosmos_settings = _load_settings_from_cosmos(cosmos) or {}
                cosmos_settings.setdefault("price_forecast", {})
                cosmos_settings["price_forecast"]["enabled"] = pf_enabled
                cosmos_settings["price_forecast"]["cron"] = pf_cron
                cosmos_settings["price_forecast"]["band_confidence"] = pf_band_confidence
                cosmos_settings["price_forecast"]["vol_source"] = pf_vol_source
                cosmos_settings["price_forecast"]["trend_window"] = pf_trend_window
                cosmos_settings["price_forecast"]["trend_window_long"] = pf_trend_window_long
                _save_settings_to_cosmos(cosmos, cosmos_settings)

            config = _load_config()
            config.setdefault("price_forecast", {})
            config["price_forecast"]["enabled"] = pf_enabled
            config["price_forecast"]["cron"] = pf_cron
            config["price_forecast"]["band_confidence"] = pf_band_confidence
            config["price_forecast"]["vol_source"] = pf_vol_source
            config["price_forecast"]["trend_window"] = pf_trend_window
            config["price_forecast"]["trend_window_long"] = pf_trend_window_long
            _write_config(config)
            saved.append("Price forecast")

            scheduler = getattr(request.app.state, "scheduler", None)
            if scheduler is not None:
                scheduler.registry.reschedule("price_forecast", pf_cron, scheduler.config)
                scheduler.registry.update_task_enabled("price_forecast", pf_enabled, scheduler.config)
        except (ValueError, KeyError):
            pass

    return saved




@app.get("/api/settings/config")
async def api_settings_config(request: Request):
    """Configuration settings context (JSON) for the Next.js frontend."""
    cosmos = getattr(request.app.state, "cosmos", None)
    ctx = _build_settings_config_context(request, cosmos)
    ctx.pop("request", None)
    return JSONResponse(ctx)


@app.post("/api/settings/config")
async def api_settings_config_save(request: Request):
    """Save configuration settings (JSON). The body mirrors the form field
    names; booleans are accepted as JSON booleans. Returns the list of saved
    sections."""
    cosmos = getattr(request.app.state, "cosmos", None)
    try:
        body = await request.json()
    except Exception:
        body = {}

    class _JsonForm:
        def __contains__(self, key):
            return key in body

        def get(self, key, default=""):
            if key not in body:
                return default
            v = body[key]
            if isinstance(v, bool):
                return "true" if v else "false"
            return str(v)

    try:
        saved = _apply_settings_config(request, cosmos, _JsonForm())
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"success": True, "saved": saved})


def _config_with_persisted_ai_overrides(
    cosmos,
    persisted_settings: Optional[dict] = None,
):
    from src.config import Config

    config_obj = Config()
    settings = (
        persisted_settings
        if persisted_settings is not None
        else (_load_settings_from_cosmos(cosmos) or {})
    )
    for key in (
        "ai_function_overrides",
        "scheduler",
        "summary_agent",
        "banner_agent",
        "plan_monitor",
    ):
        if key in settings:
            config_obj.config[key] = copy.deepcopy(settings[key])
        elif persisted_settings is not None:
            if key == "ai_function_overrides":
                config_obj.config.pop(key, None)
            else:
                config_obj.config.setdefault(key, {}).pop("provider", None)
                if key != "plan_monitor":
                    config_obj.config.setdefault(key, {}).pop("model", None)
    return config_obj


def _build_ai_providers_context(
    cosmos,
    persisted_settings: Optional[dict] = None,
) -> dict:
    from src.ai_functions import AI_FUNCTIONS, SUPPORTED_AI_PROVIDERS
    from src.config import Config

    config_obj = _config_with_persisted_ai_overrides(
        cosmos, persisted_settings
    )
    functions = []
    for function_id, metadata in AI_FUNCTIONS.items():
        override = dict(
            config_obj.config.get("ai_function_overrides", {}).get(function_id)
            or {}
        )
        legacy_task = metadata.get("legacy_task")
        legacy_cfg = config_obj.config.get(legacy_task, {}) if legacy_task else {}
        legacy_provider = str(legacy_cfg.get("provider") or "").strip().lower()
        legacy_model = (
            str(legacy_cfg.get("model") or "").strip()
            if function_id != "plan_monitor"
            else ""
        )
        provider_value = str(override.get("provider") or "").strip().lower()
        model_value = str(override.get("model") or "").strip()
        if not provider_value and legacy_provider in SUPPORTED_AI_PROVIDERS:
            provider_value = legacy_provider
        if not model_value and legacy_model:
            model_value = legacy_model

        inherited_config = Config.__new__(Config)
        inherited_config.config = copy.deepcopy(config_obj.config)
        inherited_config.config.setdefault("ai_function_overrides", {}).pop(
            function_id, None
        )
        if legacy_task:
            inherited_config.config.setdefault(legacy_task, {}).pop(
                "provider", None
            )
            if function_id != "plan_monitor":
                inherited_config.config.setdefault(legacy_task, {}).pop(
                    "model", None
                )

        functions.append({
            "id": function_id,
            "label": metadata["label"],
            "group": metadata["group"],
            "description": metadata["description"],
            "provider": provider_value,
            "model": model_value,
            "effective_provider": config_obj.provider_for(function_id),
            "effective_model": config_obj.model_for(function_id),
            "inherited_provider": inherited_config.provider_for(function_id),
            "inherited_model": inherited_config.model_for(function_id),
            "provider_source": (
                "override" if override.get("provider")
                else "legacy" if legacy_provider in SUPPORTED_AI_PROVIDERS
                else "inherited"
            ),
            "model_source": (
                "override" if override.get("model")
                else "legacy" if legacy_model
                else "inherited"
            ),
        })
    return {
        "providers": list(SUPPORTED_AI_PROVIDERS),
        "functions": functions,
    }


def _cosmos_persistence_configured() -> bool:
    config = _load_config()
    cosmos_cfg = config.get("cosmosdb", {})
    return bool(
        _resolve_env(str(cosmos_cfg.get("endpoint") or ""))
        and _resolve_env(str(cosmos_cfg.get("key") or ""))
    )


def _save_ai_provider_overrides(
    request: Request,
    cosmos,
    submitted: dict,
) -> tuple[dict, str]:
    from src.ai_functions import AI_FUNCTIONS, SUPPORTED_AI_PROVIDERS

    unknown = sorted(set(submitted) - set(AI_FUNCTIONS))
    if unknown:
        raise ValueError(f"Unknown AI function: {', '.join(unknown)}")

    normalized = {}
    for function_id, values in submitted.items():
        if not isinstance(values, dict):
            raise ValueError(f"Invalid settings for {function_id}")
        provider = str(values.get("provider") or "").strip().lower()
        model = str(values.get("model") or "").strip()
        if provider and provider not in SUPPORTED_AI_PROVIDERS:
            raise ValueError(
                f"Unsupported provider for {function_id}: {provider}"
            )
        if model and not _MODEL_NAME_RE.fullmatch(model):
            raise ValueError(
                f"Invalid model for {function_id}. Use a deployment/model "
                "name containing letters, numbers, '.', '_', ':', '/', or '-'."
            )
        normalized[function_id] = {
            key: value
            for key, value in (("provider", provider), ("model", model))
            if value
        }

    if cosmos is not None:
        persisted_before = _load_settings_from_cosmos_required(cosmos)
    elif _cosmos_persistence_configured():
        detail = getattr(request.app.state, "cosmos_error", None)
        raise RuntimeError(
            "CosmosDB settings persistence is unavailable"
            + (f": {detail}" if detail else "")
        )
    else:
        persisted_before = None

    config_obj = _config_with_persisted_ai_overrides(
        cosmos, persisted_before
    )
    candidate_overrides = copy.deepcopy(
        config_obj.config.get("ai_function_overrides", {})
    )
    for function_id, values in normalized.items():
        if values:
            candidate_overrides[function_id] = values
        else:
            candidate_overrides.pop(function_id, None)
    config_obj.config["ai_function_overrides"] = candidate_overrides
    from src.llm import validate_llm_config
    for function_id, values in normalized.items():
        if not values.get("provider"):
            continue
        error = validate_llm_config(
            config_obj.llm_config_for_function(function_id)
        )
        if error:
            raise ValueError(f"{function_id}: {error}")

    def apply(config: dict) -> None:
        overrides = config.setdefault("ai_function_overrides", {})
        for function_id, values in normalized.items():
            if values:
                overrides[function_id] = values
            else:
                overrides.pop(function_id, None)

        roles_by_task = defaultdict(set)
        for function_id, metadata in AI_FUNCTIONS.items():
            if metadata.get("legacy_task"):
                roles_by_task[metadata["legacy_task"]].add(function_id)
        submitted_ids = set(normalized)
        for task_key, function_ids in roles_by_task.items():
            if not function_ids.intersection(submitted_ids):
                continue
            section = config.setdefault(task_key, {})
            legacy_provider = str(section.get("provider") or "").strip().lower()
            legacy_model = str(section.get("model") or "").strip()
            for function_id in function_ids - submitted_ids:
                sibling = overrides.setdefault(function_id, {})
                if legacy_provider and not sibling.get("provider"):
                    sibling["provider"] = legacy_provider
                if (
                    task_key != "plan_monitor"
                    and legacy_model
                    and not sibling.get("model")
                ):
                    sibling["model"] = legacy_model
            section.pop("provider", None)
            if task_key != "plan_monitor":
                section.pop("model", None)
        if not overrides:
            config.pop("ai_function_overrides", None)

    if cosmos is not None:
        updater = getattr(cosmos, "update_settings", None)
        if updater is None:
            raise RuntimeError(
                "CosmosDB service does not support atomic settings updates"
            )
        saved_settings = updater(apply)
        verified_settings = _load_settings_from_cosmos_required(cosmos)
        if (
            verified_settings.get("ai_function_overrides")
            != saved_settings.get("ai_function_overrides")
        ):
            raise RuntimeError(
                "CosmosDB settings verification failed for settings/app-config"
            )
        for task_key in ("scheduler", "summary_agent", "banner_agent", "plan_monitor"):
            if verified_settings.get(task_key) != saved_settings.get(task_key):
                raise RuntimeError(
                    "CosmosDB settings verification failed for settings/app-config"
                )

        config = _load_config()
        apply(config)
        try:
            _write_config(config)
        except Exception:
            logger.warning(
                "CosmosDB settings were saved, but local config.yaml sync failed",
                exc_info=True,
            )
        effective_settings = verified_settings
        persistence = "cosmos"
    else:
        config = _load_config()
        apply(config)
        _write_config(config)
        effective_settings = config
        persistence = "local"

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None and getattr(scheduler, "config", None) is not None:
        for key in (
            "ai_function_overrides",
            "scheduler",
            "summary_agent",
            "banner_agent",
            "plan_monitor",
        ):
            if key in effective_settings:
                scheduler.config.config[key] = copy.deepcopy(
                    effective_settings[key]
                )
            elif key == "ai_function_overrides":
                scheduler.config.config.pop(key, None)
            else:
                scheduler.config.config.setdefault(key, {}).pop(
                    "provider", None
                )
                if key != "plan_monitor":
                    scheduler.config.config.setdefault(key, {}).pop(
                        "model", None
                    )
        scheduler.runner.set_function_llms(
            scheduler.config.function_llm_configs()
        )
    return effective_settings, persistence


@app.get("/api/settings/ai-providers")
async def api_settings_ai_providers(request: Request):
    cosmos = getattr(request.app.state, "cosmos", None)
    try:
        if cosmos is not None:
            settings = _load_settings_from_cosmos_required(cosmos)
            persistence = "cosmos"
        elif _cosmos_persistence_configured():
            detail = getattr(request.app.state, "cosmos_error", None)
            raise RuntimeError(
                "CosmosDB settings persistence is unavailable"
                + (f": {detail}" if detail else "")
            )
        else:
            settings = None
            persistence = "local"
    except RuntimeError as exc:
        logger.exception("Failed to load AI provider settings")
        return JSONResponse({"error": str(exc)}, status_code=503)
    except Exception:
        logger.exception("Failed to load AI provider settings")
        return JSONResponse(
            {"error": "Failed to read settings/app-config from CosmosDB"},
            status_code=503,
        )
    return JSONResponse({
        **_build_ai_providers_context(cosmos, settings),
        "persistence": persistence,
    })


@app.post("/api/settings/ai-providers")
async def api_settings_ai_providers_save(request: Request):
    cosmos = getattr(request.app.state, "cosmos", None)
    try:
        body = await request.json()
        submitted = body.get("functions", {}) if isinstance(body, dict) else {}
        if not isinstance(submitted, dict):
            raise ValueError("'functions' must be an object")
        settings, persistence = _save_ai_provider_overrides(
            request, cosmos, submitted
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        logger.exception("Failed to save AI provider settings")
        return JSONResponse(
            {"error": str(exc) or "Failed to persist AI provider settings"},
            status_code=503,
        )
    except Exception:
        logger.exception("Failed to save AI provider settings")
        return JSONResponse(
            {"error": "Failed to persist settings/app-config in CosmosDB"},
            status_code=503,
        )
    return JSONResponse({
        "success": True,
        "persistence": persistence,
        **_build_ai_providers_context(cosmos, settings),
    })




@app.get("/api/settings/runtime")
async def api_settings_runtime(request: Request):
    """Runtime stats (JSON): telemetry, options-chain cache, recent errors."""
    cosmos = getattr(request.app.state, "cosmos", None)
    telemetry_stats: dict = {}
    recent_errors: list = []
    if cosmos:
        try:
            telemetry_stats = cosmos.get_telemetry_stats()
        except Exception:
            pass
        try:
            recent_errors = cosmos.get_recent_fetch_errors(limit=10)
        except Exception:
            pass

    cache_stats: dict = {}
    try:
        from src.options_chain_cache import get_options_chain_cache
        cache_stats = get_options_chain_cache().stats()
    except Exception:
        pass

    return JSONResponse({
        "telemetry_stats": telemetry_stats,
        "cache_stats": cache_stats,
        "recent_errors": recent_errors,
    })


@app.get("/api/health/options-chain")
async def api_health_options_chain(request: Request):
    """Options-chain persistence/serving health (Danny's zero-free
    decision §4.2). Always returns HTTP 200 — degraded persistence is
    non-fatal by design (memory-only chain caching keeps serving), so
    monitoring can alert on `status == "degraded"` without the endpoint
    itself failing health checks.

    Returns the merged persistence health block (construction/retry state
    from `get_persistence_health()` plus the live store instance's own
    `stats()` — availability, error counts, write counters) together with,
    per cached symbol, the last refresh cycle's quality counters
    (`contracts_total`, `contracts_no_usable_bid`, `contracts_greeks_invalid`,
    `contracts_stale`).
    """
    try:
        from src.options_chain_cache import get_options_chain_cache
        stats = get_options_chain_cache().stats()
    except Exception as exc:
        logger.exception("GET /api/health/options-chain: failed to read cache stats")
        return JSONResponse({"status": "degraded", "error": str(exc)}, status_code=200)

    persistence = stats.get("persistence", {})
    status = "ok" if persistence.get("available") else "degraded"

    symbols = {}
    for sym, entry in (stats.get("entries") or {}).items():
        symbols[sym] = {
            "contracts_total": entry.get("contracts_total", 0),
            "contracts_no_usable_bid": entry.get("contracts_no_usable_bid", 0),
            "contracts_greeks_invalid": entry.get("contracts_greeks_invalid", 0),
            "contracts_stale": entry.get("contracts_stale", 0),
        }

    return JSONResponse({
        "status": status,
        "persistence": persistence,
        "symbols": symbols,
    })


# ===========================================================================
# Settings - Agent Execution Logs (traces)
# ===========================================================================

# Agent types that can be individually traced (superset of AGENT_TYPES).
TRACEABLE_AGENT_TYPES = {
    **AGENT_TYPES,
    "plan_monitor": {"label": "Plan Monitor"},
}


def _get_trace_enabled_types(cosmos) -> dict:
    """Return {agent_type: bool} trace enablement (default: all enabled)."""
    stored = {}
    if cosmos is not None:
        try:
            settings = cosmos.get_settings() or {}
            stored = (settings.get("agent_trace") or {}).get("enabled_types") or {}
        except Exception:
            stored = {}
    return {key: bool(stored.get(key, True)) for key in TRACEABLE_AGENT_TYPES}










# ---------------------------------------------------------------------------
# Agent Logs — JSON API (for the Next.js frontend)
# ---------------------------------------------------------------------------

@app.get("/api/agent-traces")
async def api_agent_traces(request: Request):
    """List agent execution traces plus filter metadata (JSON)."""
    cosmos = getattr(request.app.state, "cosmos", None)
    traces: List[dict] = []
    total = 0
    symbols: List[str] = []
    if cosmos is not None:
        try:
            traces = cosmos.list_agent_traces(limit=500)
        except Exception:
            traces = []
        try:
            total = cosmos.count_agent_traces()
        except Exception:
            total = len(traces)
        try:
            symbols = sorted({
                t.get("symbol") for t in traces
                if t.get("symbol") and t.get("symbol") != "_"
            })
        except Exception:
            symbols = []
    agent_types = {
        key: {"label": meta.get("label", key)}
        for key, meta in TRACEABLE_AGENT_TYPES.items()
    }
    return JSONResponse({
        "traces": traces,
        "total": total,
        "symbols": symbols,
        "agent_types": agent_types,
        "trace_enabled": _get_trace_enabled_types(cosmos),
        "cosmos_available": cosmos is not None,
    })


@app.get("/api/agent-traces/{trace_id}")
async def api_agent_trace_detail(request: Request, trace_id: str):
    """Full detail of a single agent execution trace (JSON)."""
    cosmos = getattr(request.app.state, "cosmos", None)
    if cosmos is None:
        return JSONResponse({"error": "CosmosDB not available"}, status_code=503)
    trace = cosmos.get_agent_trace(trace_id)
    if not trace:
        return JSONResponse({"error": "Trace not found"}, status_code=404)
    agent_label = TRACEABLE_AGENT_TYPES.get(
        trace.get("agent_type"), {}).get("label", trace.get("agent_type", ""))
    return JSONResponse({"trace": trace, "agent_label": agent_label})


@app.post("/api/agent-traces/config")
async def api_agent_traces_config(request: Request):
    """Persist per-agent-type trace enablement (JSON body: {enabled_types})."""
    cosmos = getattr(request.app.state, "cosmos", None)
    try:
        body = await request.json()
    except Exception:
        body = {}
    requested = body.get("enabled_types") or {}
    enabled = {key: bool(requested.get(key, False)) for key in TRACEABLE_AGENT_TYPES}
    if cosmos is not None:
        try:
            settings = _load_settings_from_cosmos(cosmos) or {}
            settings.setdefault("agent_trace", {})
            settings["agent_trace"]["enabled_types"] = enabled
            _save_settings_to_cosmos(cosmos, settings)
        except Exception:
            logger.warning("Failed to save agent_trace config", exc_info=True)
            return JSONResponse({"error": "Failed to save"}, status_code=500)
    return JSONResponse({"success": True, "enabled_types": enabled})


@app.post("/api/agent-traces/purge")
async def api_agent_traces_purge(request: Request):
    """Purge agent traces (JSON body: {older_than_days: int|null|'all'})."""
    cosmos = getattr(request.app.state, "cosmos", None)
    try:
        body = await request.json()
    except Exception:
        body = {}
    older_than = body.get("older_than_days")
    deleted = 0
    if cosmos is not None:
        try:
            days = int(older_than) if older_than not in (None, "", "all") else None
        except (TypeError, ValueError):
            days = None
        try:
            deleted = cosmos.purge_agent_traces(older_than_days=days)
        except Exception:
            logger.warning("Failed to purge agent traces", exc_info=True)
            return JSONResponse({"error": "Failed to purge"}, status_code=500)
    return JSONResponse({"success": True, "deleted": deleted})


@app.post("/api/debug/clear-cache")
async def api_debug_clear_cache(request: Request):
    """Clear all yfinance provider cache entries."""
    provider = getattr(request.app.state, "yf_provider", None)
    if provider is None:
        return JSONResponse({"success": True, "cleared": 0})
    cleared = len(provider._cache)
    provider._cache.clear()
    return JSONResponse({"success": True, "cleared": cleared})




@app.get("/api/settings/debug")
async def api_settings_debug(request: Request):
    """Debug diagnostics (JSON): Cosmos connection info, provider cache, symbols."""
    cosmos = getattr(request.app.state, "cosmos", None)
    config = _load_config()
    cosmos_endpoint = _resolve_env(config.get("cosmosdb", {}).get("endpoint", ""))
    cosmos_database = config.get("cosmosdb", {}).get("database", "stock-options-manager")
    cosmos_status = "Connected" if cosmos else "Not connected"
    cosmos_error = getattr(request.app.state, "cosmos_error", None)

    provider = getattr(request.app.state, "yf_provider", None)
    if provider:
        cache_stats = {
            "total_entries": len(provider._cache),
            "symbols": list(provider._cache.keys()),
        }
    else:
        cache_stats = {"total_entries": 0, "symbols": []}

    symbols = []
    if cosmos:
        try:
            symbols = [
                {"symbol": s.get("symbol"), "display_name": s.get("display_name", s.get("symbol"))}
                for s in cosmos.list_symbols()
            ]
        except Exception:
            symbols = []

    return JSONResponse({
        "cosmos_endpoint": cosmos_endpoint,
        "cosmos_database": cosmos_database,
        "cosmos_status": cosmos_status,
        "cosmos_error": cosmos_error,
        "symbols": symbols,
        "cache_stats": cache_stats,
    })


# Redirect old /settings to /settings/config for backward compatibility


# ===========================================================================
# Telegram Test
# ===========================================================================

@app.post("/api/telegram/test")
async def telegram_test(request: Request):
    """Send a test message via Telegram."""
    cosmos = getattr(request.app.state, "cosmos", None)
    cosmos_settings = _load_settings_from_cosmos(cosmos)
    config = cosmos_settings if cosmos_settings else _load_config()
    telegram_cfg = config.get("telegram", {})
    if not telegram_cfg.get("enabled"):
        return JSONResponse({"ok": False, "error": "Telegram not enabled"})

    bot_token = _resolve_env(telegram_cfg.get("bot_token", ""))
    chat_id = _resolve_env(telegram_cfg.get("chat_id", ""))

    if not bot_token or not chat_id:
        return JSONResponse({"ok": False, "error": "Bot token or chat ID missing"})

    try:
        import requests as req
        resp = req.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": "✅ Option Income Lab — Telegram notifications are working!", "parse_mode": "HTML"},
            timeout=10,
        )
        data = resp.json()
        if data.get("ok"):
            return JSONResponse({"ok": True})
        return JSONResponse({"ok": False, "error": data.get("description", "Unknown error")})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


# ===========================================================================
# Trigger (Run Now)
# ===========================================================================

AGENT_FUNCTIONS = {
    "covered_call": "run_covered_call_analysis",
    "cash_secured_put": "run_cash_secured_put_analysis",
    "buy_tracker": "run_buy_tracker_analysis",
    "open_call_monitor": "run_open_call_monitor",
    "open_put_monitor": "run_open_put_monitor",
}


# ---------------------------------------------------------------------------
# Force-alpha trigger contract (danny-force-alpha-design.md §6-§7)
# ---------------------------------------------------------------------------
# `run_trigger` records provenance (who asked: "scheduled" cron vs "manual"
# API/UI click); `force_alpha` requests an unconditional Alpha Advisor
# review. The two are orthogonal and must never be collapsed. The actual
# gate (`run_alpha = is_alert or prolonged_wait or force_alpha`) and its
# cooldown-neutrality/notification-suppression safeguards live entirely in
# AgentRunner (Linus's file) — this module only carries the flags to the
# call boundary.


def _call_agent_func(func, config, runner, cosmos, context_provider, *,
                      symbol: str = None, run_trigger: str = "scheduled",
                      force_alpha: bool = False):
    """Invoke an agent wrapper function, forwarding run_trigger/force_alpha
    only if its signature currently declares them.

    The wrapper functions (run_covered_call_analysis and siblings) gain
    these parameters incrementally as they're threaded through to
    AgentRunner.run_symbol_agent. Introspecting the target signature lets
    this API layer roll out the contract independently of that work: calls
    are unaffected (today's exact behavior) until a given wrapper accepts
    the parameter, then start forwarding it automatically with no further
    change here. buy_tracker is expected to never accept force_alpha (it's
    excluded from Alpha/Supervisor review entirely) — forcing it is inert,
    never an error.
    """
    kwargs = {"symbol": symbol}
    accepted = inspect.signature(func).parameters
    if "run_trigger" in accepted:
        kwargs["run_trigger"] = run_trigger
    if "force_alpha" in accepted:
        kwargs["force_alpha"] = force_alpha
    return func(config, runner, cosmos, context_provider, **kwargs)


def _trigger_slot_key(agent_type: str, symbol: Optional[str]) -> str:
    return f"{agent_type}:{symbol or '*'}"


def _acquire_trigger_slot(app_state, agent_type: str, symbol: Optional[str],
                           force_alpha: bool) -> Optional[dict]:
    """Claim the in-flight slot for (agent_type, symbol-or-wildcard).

    Returns None if claimed (caller may proceed to start the run), or a
    snapshot of the existing in-flight record if another run for the same
    key is still active (caller should respond 409). A slot is considered
    stale — and reclaimable — after `_MAX_TASK_DURATION_SECONDS` (the same
    30-minute constant the scheduler's own worker uses to abandon a
    runaway job; reused here rather than inventing a second timeout).
    """
    registry = getattr(app_state, "_trigger_inflight", None)
    if registry is None:
        registry = {}
        app_state._trigger_inflight = registry
    lock = getattr(app_state, "_trigger_inflight_lock", None)
    if lock is None:
        lock = threading.Lock()
        app_state._trigger_inflight_lock = lock

    key = _trigger_slot_key(agent_type, symbol)
    now = time.monotonic()
    with lock:
        existing = registry.get(key)
        if existing is not None and (now - existing["_monotonic_started"]) < _MAX_TASK_DURATION_SECONDS:
            return dict(existing)
        registry[key] = {
            "agent_type": agent_type,
            "symbol": symbol,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "force_alpha": force_alpha,
            "_monotonic_started": now,
        }
        return None


def _release_trigger_slot(app_state, agent_type: str, symbol: Optional[str]) -> None:
    registry = getattr(app_state, "_trigger_inflight", None)
    if not registry:
        return
    lock = getattr(app_state, "_trigger_inflight_lock", None)
    key = _trigger_slot_key(agent_type, symbol)
    if lock is not None:
        with lock:
            registry.pop(key, None)
    else:
        registry.pop(key, None)


def _run_agent_in_background(agent_type: str, scheduler, symbol: str = None,
                              run_trigger: str = "manual", force_alpha: bool = True):
    import asyncio
    from src.covered_call_agent import run_covered_call_analysis
    from src.cash_secured_put_agent import run_cash_secured_put_analysis
    from src.buy_tracker_agent import run_buy_tracker_analysis
    from src.open_call_monitor_agent import run_open_call_monitor
    from src.open_put_monitor_agent import run_open_put_monitor

    funcs = {
        "covered_call": run_covered_call_analysis,
        "cash_secured_put": run_cash_secured_put_analysis,
        "buy_tracker": run_buy_tracker_analysis,
        "open_call_monitor": run_open_call_monitor,
        "open_put_monitor": run_open_put_monitor,
    }
    func = funcs[agent_type]
    try:
        asyncio.run(_call_agent_func(
            func, scheduler.config, scheduler.runner,
            scheduler.cosmos, scheduler.context_provider,
            symbol=symbol, run_trigger=run_trigger, force_alpha=force_alpha,
        ))
    except Exception as e:
        print(f"ERROR running {agent_type} trigger: {e}")


# ---------------------------------------------------------------------------
# DGI Screener — manual trigger (must be before generic {agent_type} route)
# ---------------------------------------------------------------------------

def _run_dgi_screener_in_background(scheduler, state_ref):
    """Run the DGI screener in a background thread."""
    import asyncio
    from src.dgi_screener import run_dgi_screener

    try:
        asyncio.run(run_dgi_screener(scheduler.config, scheduler.cosmos))
    except Exception as e:
        logger.error("DGI screener trigger error: %s", e, exc_info=True)
    finally:
        state_ref["running"] = False


@app.post("/api/trigger/dgi_screener")
async def trigger_dgi_screener(request: Request):
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None or scheduler.config is None:
        return JSONResponse(
            {"error": "Scheduler not running — cannot trigger DGI screener"},
            status_code=503)

    state_ref = getattr(request.app.state, "_dgi_screener_status", None)
    if state_ref is None:
        state_ref = {"running": False}
        request.app.state._dgi_screener_status = state_ref

    if state_ref.get("running"):
        return JSONResponse(
            {"error": "DGI screener already running"},
            status_code=409)

    state_ref["running"] = True
    thread = threading.Thread(
        target=_run_dgi_screener_in_background,
        args=(scheduler, state_ref),
        daemon=True,
    )
    thread.start()
    return JSONResponse({"status": "triggered", "agent_type": "dgi_screener"})


@app.get("/api/trigger/dgi_screener/status")
async def trigger_dgi_screener_status(request: Request):
    state_ref = getattr(request.app.state, "_dgi_screener_status", None)
    running = state_ref.get("running", False) if state_ref else False
    return JSONResponse({"running": running})


# ---------------------------------------------------------------------------
# Summary Agent — manual trigger
# ---------------------------------------------------------------------------

def _run_summary_agent_in_background(scheduler, state_ref):
    """Run the summary agent in a background thread."""
    import asyncio
    try:
        asyncio.run(scheduler._run_summary_agent_async())
    except Exception as e:
        logger.error("Summary agent trigger error: %s", e, exc_info=True)
    finally:
        state_ref["running"] = False


@app.post("/api/trigger/summary_agent")
async def trigger_summary_agent(request: Request):
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None or scheduler.config is None:
        return JSONResponse(
            {"error": "Scheduler not running — cannot trigger summary agent"},
            status_code=503)

    state_ref = getattr(request.app.state, "_summary_agent_status", None)
    if state_ref is None:
        state_ref = {"running": False}
        request.app.state._summary_agent_status = state_ref

    if state_ref.get("running"):
        return JSONResponse(
            {"error": "Summary agent already running"},
            status_code=409)

    state_ref["running"] = True
    thread = threading.Thread(
        target=_run_summary_agent_in_background,
        args=(scheduler, state_ref),
        daemon=True,
    )
    thread.start()
    return JSONResponse({"status": "triggered", "agent_type": "summary_agent"})


# ---------------------------------------------------------------------------
# Banner Agent — manual trigger
# ---------------------------------------------------------------------------

def _run_banner_agent_in_background(scheduler, state_ref):
    """Run the banner agent in a background thread."""
    import asyncio
    try:
        asyncio.run(scheduler._run_banner_agent_async())
    except Exception as e:
        logger.error("Banner agent trigger error: %s", e, exc_info=True)
    finally:
        state_ref["running"] = False


@app.post("/api/trigger/banner_agent")
async def trigger_banner_agent(request: Request):
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None or scheduler.config is None:
        return JSONResponse(
            {"error": "Scheduler not running — cannot trigger banner agent"},
            status_code=503)

    state_ref = getattr(request.app.state, "_banner_agent_status", None)
    if state_ref is None:
        state_ref = {"running": False}
        request.app.state._banner_agent_status = state_ref

    if state_ref.get("running"):
        return JSONResponse(
            {"error": "Banner agent already running"},
            status_code=409)

    state_ref["running"] = True
    thread = threading.Thread(
        target=_run_banner_agent_in_background,
        args=(scheduler, state_ref),
        daemon=True,
    )
    thread.start()
    return JSONResponse({"status": "triggered", "agent_type": "banner_agent"})


# ---------------------------------------------------------------------------
# DPS Scorer Cron — manual trigger
# ---------------------------------------------------------------------------

def _run_dps_cron_in_background(cosmos, yf_provider, state_ref):
    """Run the DPS cron in a background thread."""
    import asyncio
    from src.dps_cron import run_dps_cron
    try:
        result = asyncio.run(run_dps_cron(cosmos, yf_provider))
        state_ref["last_result"] = result
    except Exception as e:
        logger.error("DPS cron trigger error: %s", e, exc_info=True)
        state_ref["last_result"] = {"status": "error", "error": str(e)}
    finally:
        state_ref["running"] = False


@app.post("/api/trigger/dps_scorer")
async def trigger_dps_scorer(request: Request):
    cosmos = getattr(request.app.state, "cosmos", None)
    yf_provider = getattr(request.app.state, "yf_provider", None)

    if cosmos is None:
        return JSONResponse(
            {"error": "CosmosDB not available — cannot run DPS scorer"},
            status_code=503)

    state_ref = getattr(request.app.state, "_dps_cron_status", None)
    if state_ref is None:
        state_ref = {"running": False, "last_result": None}
        request.app.state._dps_cron_status = state_ref

    if state_ref.get("running"):
        return JSONResponse(
            {"error": "DPS scorer already running"},
            status_code=409)

    state_ref["running"] = True
    thread = threading.Thread(
        target=_run_dps_cron_in_background,
        args=(cosmos, yf_provider, state_ref),
        daemon=True,
    )
    thread.start()
    return JSONResponse({"status": "triggered", "agent_type": "dps_scorer"})


def _run_forecast_cron_in_background(cosmos, yf_provider, state_ref):
    """Run the price-forecast cron in a background thread."""
    import asyncio
    from src.forecast_cron import run_forecast_cron
    try:
        result = asyncio.run(run_forecast_cron(cosmos, yf_provider))
        state_ref["last_result"] = result
    except Exception as e:
        logger.error("Price forecast cron trigger error: %s", e, exc_info=True)
        state_ref["last_result"] = {"status": "error", "error": str(e)}
    finally:
        state_ref["running"] = False


@app.post("/api/trigger/price_forecast")
async def trigger_price_forecast(request: Request):
    cosmos = getattr(request.app.state, "cosmos", None)
    yf_provider = getattr(request.app.state, "yf_provider", None)

    if cosmos is None:
        return JSONResponse(
            {"error": "CosmosDB not available — cannot run price forecast"},
            status_code=503)

    state_ref = getattr(request.app.state, "_forecast_cron_status", None)
    if state_ref is None:
        state_ref = {"running": False, "last_result": None}
        request.app.state._forecast_cron_status = state_ref

    if state_ref.get("running"):
        return JSONResponse(
            {"error": "Price forecast already running"},
            status_code=409)

    state_ref["running"] = True
    thread = threading.Thread(
        target=_run_forecast_cron_in_background,
        args=(cosmos, yf_provider, state_ref),
        daemon=True,
    )
    thread.start()
    return JSONResponse({"status": "triggered", "agent_type": "price_forecast"})


@app.post("/api/trigger/portfolio_enrichment")
async def trigger_portfolio_enrichment(request: Request):
    """Manually trigger portfolio enrichment for all symbols."""
    cosmos = getattr(request.app.state, "cosmos", None)
    if cosmos is None:
        return JSONResponse({"error": "CosmosDB not available"}, status_code=503)

    state_ref = getattr(request.app.state, "_pe_status", None)
    if state_ref is None:
        state_ref = {"running": False, "last_result": None}
        request.app.state._pe_status = state_ref

    if state_ref.get("running"):
        return JSONResponse({"error": "Portfolio enrichment already running"}, status_code=409)

    state_ref["running"] = True

    def _run():
        try:
            import asyncio
            from src.portfolio_enrichment import run_portfolio_enrichment
            asyncio.run(run_portfolio_enrichment(cosmos))
            state_ref["last_result"] = {"status": "ok"}
        except Exception as e:
            state_ref["last_result"] = {"status": "error", "error": str(e)}
        finally:
            state_ref["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return JSONResponse({"status": "triggered", "agent_type": "portfolio_enrichment"})


@app.get("/api/symbols/{symbol}/enrichment-history")
async def get_enrichment_history(request: Request, symbol: str):
    """Return the rolling tech-timing / momentum history for a symbol.

    Response: {"symbol": "AAPL", "points": [{date, tech_timing, momentum}, ...]}
    ordered chronologically (oldest first). Empty list when no history yet.
    """
    cosmos = getattr(request.app.state, "cosmos", None)
    if cosmos is None:
        return JSONResponse({"error": "CosmosDB not available"}, status_code=503)
    try:
        points = cosmos.get_enrichment_history(symbol.upper())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return JSONResponse({"symbol": symbol.upper(), "points": points})


@app.post("/api/trigger/plan_monitor")
async def trigger_plan_monitor(request: Request):
    """Manually trigger plan monitor for all planned action plans."""
    cosmos = getattr(request.app.state, "cosmos", None)
    if cosmos is None:
        return JSONResponse({"error": "CosmosDB not available"}, status_code=503)

    state_ref = getattr(request.app.state, "_pm_status", None)
    if state_ref is None:
        state_ref = {"running": False, "last_result": None}
        request.app.state._pm_status = state_ref

    if state_ref.get("running"):
        return JSONResponse({"error": "Plan monitor already running"}, status_code=409)

    state_ref["running"] = True
    scheduler = getattr(request.app.state, "scheduler", None)

    def _run():
        try:
            import asyncio
            if scheduler:
                asyncio.run(scheduler._run_plan_monitor_async())
                state_ref["last_result"] = {"status": "ok"}
            else:
                state_ref["last_result"] = {"status": "error", "error": "Scheduler not available"}
        except Exception as e:
            state_ref["last_result"] = {"status": "error", "error": str(e)}
        finally:
            state_ref["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return JSONResponse({"status": "triggered", "agent_type": "plan_monitor"})


@app.post("/api/trigger/options_chain")
async def trigger_options_chain(request: Request):
    """Manually trigger options chain cache refresh for all symbols."""
    cosmos = getattr(request.app.state, "cosmos", None)
    if cosmos is None:
        return JSONResponse({"error": "CosmosDB not available"}, status_code=503)

    def _run():
        try:
            import asyncio
            from src.options_chain_cache import get_options_chain_cache
            symbols = cosmos.list_symbols()
            symbol_names = [s["symbol"] for s in symbols]
            cache = get_options_chain_cache()
            asyncio.run(cache.refresh_all(symbol_names))
        except Exception as e:
            print(f"ERROR running options_chain trigger: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return JSONResponse({"status": "triggered", "agent_type": "options_chain"})


@app.post("/api/trigger/{agent_type}")
async def trigger_agent(request: Request, agent_type: str):
    if agent_type not in AGENT_FUNCTIONS:
        return JSONResponse({"error": f"Unknown agent type: {agent_type}"},
                            status_code=404)

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None or scheduler.config is None:
        return JSONResponse(
            {"error": "Scheduler not running — cannot trigger agents"},
            status_code=503)

    body = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            raw = await request.body()
            if raw:
                body = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            body = {}
    symbol = body.get("symbol") if isinstance(body, dict) else None

    # Explicit trigger contract (danny-force-alpha-design.md §6). Manual
    # API/UI calls default force_alpha=True — a human clicked this, so it
    # gets a fresh Alpha Advisor review unconditionally — but a caller may
    # still override either field explicitly in the request body.
    run_trigger = body.get("run_trigger") if isinstance(body, dict) else None
    if run_trigger not in ("scheduled", "manual"):
        run_trigger = "manual"
    force_alpha = body.get("force_alpha") if isinstance(body, dict) else None
    if not isinstance(force_alpha, bool):
        force_alpha = True

    # In-flight guard, keyed by (agent_type, symbol-or-"*"): a duplicate
    # click/request for the same key returns 409 instead of launching a
    # second concurrent (and, with force_alpha, potentially expensive) run.
    existing = _acquire_trigger_slot(request.app.state, agent_type, symbol, force_alpha)
    if existing is not None:
        return JSONResponse(
            {
                "status": "already_running",
                "agent_type": existing["agent_type"],
                "symbol": existing["symbol"],
                "started_at": existing["started_at"],
                "force_alpha": existing["force_alpha"],
            },
            status_code=409,
        )

    def _run_and_release():
        try:
            _run_agent_in_background(agent_type, scheduler, symbol,
                                      run_trigger=run_trigger, force_alpha=force_alpha)
        finally:
            _release_trigger_slot(request.app.state, agent_type, symbol)

    thread = threading.Thread(target=_run_and_release, daemon=True)
    thread.start()
    return JSONResponse({
        "status": "triggered",
        "agent_type": agent_type,
        "symbol": symbol,
        "run_trigger": run_trigger,
        "force_alpha": force_alpha,
    })


# ---------------------------------------------------------------------------
# Full analysis — sequential execution of all agent types
# ---------------------------------------------------------------------------

_FULL_ANALYSIS_AGENT_ORDER = [
    "covered_call", "cash_secured_put", "buy_tracker", "open_call_monitor", "open_put_monitor"
]


def _default_full_analysis_status() -> dict:
    return {"running": False, "current": None, "completed": [], "total": 5, "errors": []}


def _run_all_agents_sequentially(scheduler, status: dict, run_trigger: str = "manual",
                                  force_alpha: bool = False):
    """Run all watchlist and monitor agent types sequentially in a single thread.

    Defaults preserve today's exact behavior: /api/trigger-all is
    "scheduled/due-only" by default (force_alpha=False) per the user's
    confirmed decision, which overrode Danny's own proposed default of
    forcing this call path too. The frontend's Settings "Monitoring Agent"
    Run Now button is the one caller within scope that must force Alpha
    (same decision), so it explicitly opts in via the request body -- see
    trigger_all_agents below. Any other/future caller that omits the body
    stays due-only, which is this call path's actual identity.
    """
    import asyncio
    from src.covered_call_agent import run_covered_call_analysis
    from src.cash_secured_put_agent import run_cash_secured_put_analysis
    from src.buy_tracker_agent import run_buy_tracker_analysis
    from src.open_call_monitor_agent import run_open_call_monitor
    from src.open_put_monitor_agent import run_open_put_monitor

    funcs = {
        "covered_call": run_covered_call_analysis,
        "cash_secured_put": run_cash_secured_put_analysis,
        "buy_tracker": run_buy_tracker_analysis,
        "open_call_monitor": run_open_call_monitor,
        "open_put_monitor": run_open_put_monitor,
    }

    for agent_type in _FULL_ANALYSIS_AGENT_ORDER:
        status["current"] = agent_type
        try:
            asyncio.run(_call_agent_func(
                funcs[agent_type], scheduler.config, scheduler.runner,
                scheduler.cosmos, scheduler.context_provider,
                run_trigger=run_trigger, force_alpha=force_alpha,
            ))
            status["completed"].append(agent_type)
        except Exception as e:
            logger.error("Full analysis error running %s: %s", agent_type, e)
            status["errors"].append({"agent": agent_type, "error": str(e)})
            status["completed"].append(agent_type)

    status["running"] = False
    status["current"] = None

    # Auto-reset status after 30 seconds
    def _reset():
        import time
        time.sleep(30)
        status.clear()
        status.update(_default_full_analysis_status())

    threading.Thread(target=_reset, daemon=True).start()


# ---------------------------------------------------------------------------
# Unified Scheduler API — consistent access to all scheduled tasks
# ---------------------------------------------------------------------------

@app.get("/api/scheduler/tasks")
async def get_scheduler_tasks(request: Request):
    """Get metadata for all scheduled tasks (unified endpoint).

    Returns: list of {name, display_name, config_key, enabled, cron, last_run, next_run, has_extra_config}
    """
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None or scheduler.registry is None:
        return JSONResponse(
            {"error": "Scheduler not running"},
            status_code=503)

    tasks = scheduler.registry.get_all_task_metadata()
    return JSONResponse({"tasks": tasks})


@app.post("/api/scheduler/tasks/{task_name}/run")
async def run_scheduler_task_now(request: Request, task_name: str):
    """Manually trigger a scheduled task (Run Now button).

    Returns: {"success": bool, "message": str}
    """
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None or scheduler.registry is None:
        return JSONResponse(
            {"error": "Scheduler not running"},
            status_code=503)

    # Run in background thread to avoid blocking
    result = {"success": False, "message": "Starting task..."}

    def _run_in_background():
        nonlocal result
        # Corrected trigger contract (copilot-force-alpha-semantics-superseded.md):
        # ONLY the dashboard CC/CSP buttons (POST /api/trigger/{agent_type})
        # force Alpha. Settings "Run Now" for any scheduled task -- like
        # "Full analysis"/trigger-all -- must preserve due-only Alpha
        # semantics, so force_alpha stays False here even though a human
        # clicked the button (run_trigger="manual" still records that
        # provenance accurately for the audit trail). This is inert for
        # every task whose job_func doesn't declare run_trigger/force_alpha
        # (see TaskRegistry._worker_loop's introspection guard).
        result.update(scheduler.registry.trigger_task_now(
            task_name, run_trigger="manual", force_alpha=False,
        ))

    thread = threading.Thread(target=_run_in_background, daemon=True)
    thread.start()
    thread.join(timeout=1.0)  # Wait up to 1s for quick feedback

    return JSONResponse(result)


@app.post("/api/scheduler/tasks/{task_name}/cron")
async def update_scheduler_task_cron(request: Request, task_name: str):
    """Update a task's cron expression (live reschedule).

    Body: {"cron": "0 14 * * *"}
    Returns: {"success": bool, "message": str}
    """
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None or scheduler.registry is None:
        return JSONResponse(
            {"error": "Scheduler not running"},
            status_code=503)

    try:
        body = await request.json()
        new_cron = body.get("cron")
        if not new_cron:
            return JSONResponse(
                {"success": False, "message": "Missing 'cron' field in request body"},
                status_code=400)

        scheduler.registry.reschedule(task_name, new_cron, scheduler.config)

        # Persist to CosmosDB
        task = scheduler.registry.get_task(task_name)
        if task and scheduler.cosmos:
            scheduler.cosmos.save_settings({task.config_key: {"cron": new_cron}})

        return JSONResponse({"success": True, "message": f"Cron updated to {new_cron}"})
    except Exception as e:
        logger.exception(f"Error updating cron for {task_name}")
        return JSONResponse(
            {"success": False, "message": str(e)},
            status_code=500)


@app.post("/api/scheduler/tasks/{task_name}/enabled")
async def update_scheduler_task_enabled(request: Request, task_name: str):
    """Toggle a task's enabled state.

    Body: {"enabled": true}
    Returns: {"success": bool, "message": str}
    """
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None or scheduler.registry is None:
        return JSONResponse(
            {"error": "Scheduler not running"},
            status_code=503)

    try:
        body = await request.json()
        enabled = body.get("enabled")
        if enabled is None:
            return JSONResponse(
                {"success": False, "message": "Missing 'enabled' field in request body"},
                status_code=400)

        success = scheduler.registry.update_task_enabled(task_name, bool(enabled), scheduler.config)
        if not success:
            return JSONResponse(
                {"success": False, "message": f"Task '{task_name}' not found"},
                status_code=404)

        # Persist to CosmosDB
        task = scheduler.registry.get_task(task_name)
        if task and scheduler.cosmos:
            scheduler.cosmos.save_settings({task.config_key: {"enabled": bool(enabled)}})

        status = "enabled" if enabled else "disabled"
        return JSONResponse({"success": True, "message": f"Task {status}"})
    except Exception as e:
        logger.exception(f"Error updating enabled state for {task_name}")
        return JSONResponse(
            {"success": False, "message": str(e)},
            status_code=500)


@app.post("/api/trigger-all")
async def trigger_all_agents(request: Request):
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None or scheduler.config is None:
        return JSONResponse(
            {"error": "Scheduler not running — cannot trigger agents"},
            status_code=503)

    status = getattr(request.app.state, "_full_analysis_status", None)
    if status and status.get("running"):
        return JSONResponse(
            {"error": "Full analysis already running", "status": status},
            status_code=409)

    # Corrected trigger contract (copilot-force-alpha-semantics-superseded.md):
    # "Run Full"/"Full analysis" always stays due-only -- no override
    # surface. Only the per-agent dashboard trigger route
    # (POST /api/trigger/{agent_type}) forces Alpha by default. run_trigger
    # is still "manual" for audit-trace accuracy: a human clicked this.
    run_trigger = "manual"
    force_alpha = False

    status = _default_full_analysis_status()
    status["running"] = True
    request.app.state._full_analysis_status = status

    thread = threading.Thread(
        target=_run_all_agents_sequentially,
        args=(scheduler, status),
        kwargs={"run_trigger": run_trigger, "force_alpha": force_alpha},
        daemon=True,
    )
    thread.start()
    return JSONResponse({"status": "started", "run_trigger": run_trigger, "force_alpha": force_alpha})


@app.get("/api/trigger-all/status")
async def trigger_all_status(request: Request):
    status = getattr(request.app.state, "_full_analysis_status", None)
    if status is None:
        return JSONResponse(_default_full_analysis_status())
    return JSONResponse(dict(status))


@app.get("/api/scheduler/health")
async def scheduler_health(request: Request):
    """Check if the scheduler thread is alive and running."""
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        return JSONResponse({"alive": False, "reason": "no scheduler instance"}, status_code=503)
    return JSONResponse({
        "alive": getattr(scheduler, "alive", False),
        "running": getattr(scheduler, "running", False),
    })


# ===========================================================================
# DGI Screener — Page & API
# ===========================================================================





@app.get("/api/dgi/analyze/{symbol}")
async def api_dgi_analyze_symbol(request: Request, symbol: str):
    """DGI single-symbol analysis (JSON) — detailed scoring breakdown.

    Returns the full ``analyze_single_symbol`` result (metrics, technicals,
    quality-score breakdown, category, entry tag, momentum) or ``{"error": ...}``
    with a 400/500 status so the Next.js frontend can render the failure.
    """
    symbol = symbol.strip().upper()
    if not symbol or len(symbol) > 10:
        return JSONResponse({"error": "Invalid symbol"}, status_code=400)

    from src.dgi_screener import analyze_single_symbol

    cosmos = getattr(request.app.state, "cosmos", None)
    cosmos_settings = _load_settings_from_cosmos(cosmos)
    cfg = cosmos_settings if cosmos_settings else _load_config()
    dgi_filters = cfg.get("dgi_screener", {}).get("filters", {})

    result = await asyncio.get_event_loop().run_in_executor(
        None, analyze_single_symbol, symbol, dgi_filters
    )

    error = result.get("error") if isinstance(result, dict) else "Analysis failed"
    if error:
        return JSONResponse({"error": error}, status_code=500)
    return JSONResponse(result)


@app.get("/api/dgi/top")
async def api_dgi_top(request: Request):
    """Return the DGI top entries as JSON."""
    cosmos = getattr(request.app.state, "cosmos", None)
    if cosmos is None:
        return JSONResponse({"error": "CosmosDB not available"}, status_code=503)
    try:
        entries = cosmos.get_dgi_top()
        entries.sort(key=lambda x: x.get("rank", 999))
        return JSONResponse({"top": entries})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ===========================================================================
# Chat
# ===========================================================================



@app.post("/api/chat/fetch-symbol")
async def fetch_symbol_data(request: Request):
    """Fetch market data for a symbol without saving to database.

    Uses cache by default.  Pass ``"refresh": true`` in the JSON body
    to force a fresh fetch.
    """
    body = await request.json()
    symbol = body.get("symbol", "").strip().upper()
    market = body.get("market", "").strip().upper()
    option_type = body.get("option_type", "").strip().lower()
    force_refresh = body.get("refresh", False)

    if not symbol or not market:
        return JSONResponse(
            {"error": "Symbol and market are required"},
            status_code=400
        )

    if option_type not in ("call", "put"):
        return JSONResponse(
            {"error": "Option type must be 'call' or 'put'"},
            status_code=400
        )

    try:
        provider = getattr(request.app.state, "yf_provider", None)
        if provider is None:
            return JSONResponse({"error": "Data provider not initialized"}, status_code=503)

        data = await provider.fetch_all(symbol, force_refresh=force_refresh)

        return JSONResponse({
            "symbol": symbol,
            "market": market,
            "option_type": option_type,
            "data": data,
        })

    except Exception as e:
        logger.error("Error fetching symbol data: %s", e, exc_info=True)
        return JSONResponse(
            {"error": f"Failed to fetch data: {str(e)}"},
            status_code=500
        )


@app.post("/api/chat")
async def chat_api(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    mode = body.get("mode", "portfolio")
    symbol_data = body.get("symbol_data")
    first_analysis = body.get("first_analysis", False)

    if not messages and not first_analysis:
        return JSONResponse({"error": "No messages provided"},
                            status_code=400)

    context_parts: List[str] = []

    # Build context based on mode
    if mode == "portfolio":
        selected_agents = body.get("selected_agents")
        include_symbol_data = bool(body.get("include_symbol_data", False))
        if selected_agents:
            selected_agent_set = set(selected_agents)
            selected_agent_keys = [
                key for key in AGENT_TYPES if key in selected_agent_set
            ]
        else:
            selected_agent_keys = list(AGENT_TYPES.keys())

        try:
            activities_limit = int(body.get("activities_limit", 3))
        except (TypeError, ValueError):
            activities_limit = 3
        activities_limit = max(1, min(activities_limit, 50))

        cosmos = getattr(request.app.state, "cosmos", None)
        if cosmos:
            try:
                all_symbols = cosmos.list_symbols() if cosmos else []
                sym_cfg_by_symbol = {c["symbol"]: c for c in all_symbols}
                context_symbols: List[str] = []
                seen_context_symbols = set()

                def remember_context_symbol(symbol: str) -> None:
                    if symbol not in seen_context_symbols:
                        seen_context_symbols.add(symbol)
                        context_symbols.append(symbol)

                for agent_key in selected_agent_keys:
                    meta = AGENT_TYPES[agent_key]
                    is_pm = meta["is_position_monitor"]
                    context_parts.append(f"\n--- {meta['label']} ---")

                    if is_pm:
                        ptype = (
                            "call" if agent_key == "open_call_monitor"
                            else "put"
                        )
                        for sym_cfg in all_symbols:
                            sym = sym_cfg["symbol"]
                            for pos in sym_cfg.get("positions", []):
                                if (pos.get("status") == "active"
                                        and pos.get("type") == ptype):
                                    remember_context_symbol(sym)
                                    context_parts.append(
                                        f"\n## {sym} ${pos.get('strike')} "
                                        f"exp {pos.get('expiration')}"
                                    )
                                    context_parts.append("Open position:")
                                    position_doc = (
                                        _clean_doc(pos)
                                        if isinstance(pos, dict) else pos
                                    )
                                    context_parts.append(
                                        json.dumps(position_doc, indent=2,
                                                   default=str)
                                    )

                                    acts = cosmos.get_recent_activities(
                                        sym, agent_key,
                                        max_entries=activities_limit,
                                        position_id=pos.get("position_id"),
                                        include_alerts=True
                                    )
                                    context_parts.append(
                                        f"Activities (last {len(acts)}):"
                                    )
                                    if acts:
                                        for act in acts:
                                            context_parts.append(
                                                json.dumps(
                                                    _clean_doc(act), indent=2,
                                                    default=str
                                                )
                                            )
                                    else:
                                        context_parts.append(
                                            "No activities recorded."
                                        )
                    else:
                        for sym_cfg in all_symbols:
                            sym = sym_cfg["symbol"]
                            if sym_cfg.get("watchlist", {}).get(agent_key):
                                remember_context_symbol(sym)
                                display_name = sym_cfg.get("display_name", sym)
                                context_parts.append(f"\n## {display_name}")
                                acts = cosmos.get_recent_activities(
                                    sym, agent_key,
                                    max_entries=activities_limit,
                                    include_alerts=True
                                )
                                context_parts.append(
                                    f"Activities (last {len(acts)}):"
                                )
                                if acts:
                                    for act in acts:
                                        context_parts.append(
                                            json.dumps(
                                                _clean_doc(act), indent=2,
                                                default=str
                                            )
                                        )
                                else:
                                    context_parts.append(
                                        "No activities recorded."
                                    )

                if include_symbol_data and context_symbols:
                    context_parts.append("\n=== SYMBOL DATA ===")
                    for sym in sorted(context_symbols):
                        sym_cfg = sym_cfg_by_symbol.get(sym, {})
                        display_name = sym_cfg.get("display_name", sym)
                        context_parts.append(f"\n## {display_name} ({sym})")
                        enrichment = sym_cfg.get("enrichment")
                        if enrichment:
                            enrichment_doc = (
                                _clean_doc(enrichment)
                                if isinstance(enrichment, dict)
                                else enrichment
                            )
                            context_parts.append(
                                json.dumps(enrichment_doc, indent=2,
                                          default=str)
                            )
                        else:
                            context_parts.append(
                                "No enrichment data available."
                            )
            except Exception:
                context_parts.append("(Error loading context from CosmosDB)")

        context_text = (
            "\n".join(context_parts) if context_parts else
            "No open positions or watchlist symbols found for the selected "
            "agents."
        )

        system_prompt = (
            "You are an Option Income Lab advisor. For the selected agents, "
            "you have each open position for position monitors and each "
            "watchlist symbol for following agents, plus up to "
            f"{activities_limit} recent activities or alerts for each row. "
            "When present, the SYMBOL DATA section provides fundamentals, "
            "technicals, and quality metrics per symbol. "
            "Answer questions about positions, risks, and recommended actions "
            "based on this data.\n\n"
            f"Portfolio context:\n{context_text}"
        )

    elif mode == "quick-analysis":
        # Quick analysis mode using fetched symbol data
        if not symbol_data:
            return JSONResponse(
                {"error": "Symbol data required for quick analysis mode"},
                status_code=400
            )

        symbol = symbol_data.get("symbol", "?")
        market = symbol_data.get("market", "?")
        option_type = symbol_data.get("option_type", "call")
        data = symbol_data.get("data", {})

        # Build context from fetched data
        context_parts.append(f"Symbol: {market}:{symbol}\n")

        if "overview" in data and data["overview"]:
            context_parts.append("=== OVERVIEW PAGE ===")
            context_parts.append(data["overview"])

        if "technicals" in data and data["technicals"]:
            context_parts.append("\n=== TECHNICALS PAGE ===")
            context_parts.append(data["technicals"])

        if "forecast" in data and data["forecast"]:
            context_parts.append("\n=== FORECAST PAGE ===")
            context_parts.append(data["forecast"])

        if "dividends" in data and data["dividends"]:
            context_parts.append("\n=== DIVIDENDS ===")
            context_parts.append(data["dividends"])

        if "options_chain" in data and data["options_chain"]:
            from src.yfinance_data_provider import OPTIONS_CHAIN_SCHEMA_DESCRIPTION
            context_parts.append("\n=== OPTIONS CHAIN ===")
            context_parts.append(OPTIONS_CHAIN_SCHEMA_DESCRIPTION)
            context_parts.append(data["options_chain"])

        context_text = "\n\n".join(context_parts)

        # For first analysis, use conversational chat instructions (not monitoring agent JSON output)
        if first_analysis:
            import sys
            sys.path.insert(0, str(PROJECT_ROOT / "src"))

            if option_type == "call":
                from open_call_chat_instructions import TV_OPEN_CALL_CHAT_INSTRUCTIONS
                instructions = TV_OPEN_CALL_CHAT_INSTRUCTIONS
            else:  # put
                from open_put_chat_instructions import TV_OPEN_PUT_CHAT_INSTRUCTIONS
                instructions = TV_OPEN_PUT_CHAT_INSTRUCTIONS

            system_prompt = f"{instructions}\n\n{context_text}"
        else:
            # Normal chat mode after first analysis
            system_prompt = (
                f"You are a friendly and knowledgeable options analyst discussing {option_type} options for {market}:{symbol}. "
                "Provide conversational, human-friendly responses. Use the market data provided below to answer questions about "
                "the stock's price, technicals, earnings, dividends, and options. Avoid JSON or structured output — talk naturally.\n\n"
                f"Market Data:\n{context_text}"
            )

    else:
        return JSONResponse(
            {"error": f"Invalid mode: {mode}"},
            status_code=400
        )

    config_obj, err_resp = _llm_settings_response("chat")
    if err_resp:
        return err_resp

    model = config_obj.model_for('chat')

    try:
        from src.llm import create_sync_chat_client, chat_completion

        client = create_sync_chat_client(
            _function_llm_config(config_obj, "chat")
        )
        api_messages = [{"role": "system", "content": system_prompt}]
        for m in messages:
            api_messages.append({"role": m["role"], "content": m["content"]})

        reply = chat_completion(
            client,
            model=model,
            messages=api_messages,
            temperature=0.7,
            max_completion_tokens=2048,
        )
        return JSONResponse({"reply": reply})

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ===========================================================================
# Per-Symbol Chat
# ===========================================================================



async def _build_symbol_context(symbol: str, cosmos,
                                preferences: dict = None,
                                force_refresh: bool = False,
                                provider=None) -> dict:
    """Build context data for a symbol (CosmosDB + yfinance).

    Args:
        symbol: Stock symbol
        cosmos: CosmosDB client
        preferences: Dict with keys: market_data (market data), positions, activities (all bool)
        force_refresh: When True, bypass cache.
        provider: YFinanceDataProvider instance (optional, creates one if not provided)

    Returns dict with keys: context, exchange, display_name, cached_resources.
    """
    # Default to all enabled for backward compatibility
    if preferences is None:
        preferences = {
            'market_data': True,
            'positions': True,
            'activities': True
        }

    context_parts: List[str] = []
    symbol_doc = None
    exchange = "NYSE"
    cached_resources: list = []

    if cosmos:
        try:
            symbol_doc = cosmos.get_symbol(symbol)
            if symbol_doc:
                exchange = symbol_doc.get("exchange", "NYSE")
                # Only include positions if requested
                if preferences.get('positions', True):
                    context_parts.append("--- Symbol Config ---")
                    context_parts.append(json.dumps(
                        {k: v for k, v in symbol_doc.items()
                         if k in ("symbol", "display_name", "exchange",
                                  "watchlist", "positions")},
                        indent=2, default=str))
        except Exception as exc:
            logger.warning("symbol_chat: failed to load symbol doc: %s", exc)

    # Only include activities if requested
    if cosmos and preferences.get('activities', True):
        try:
            activities: List[Dict] = []
            for agent_type, meta in AGENT_TYPES.items():
                acts = cosmos.get_recent_activities(
                    symbol, agent_type, max_entries=5,
                    include_alerts=True)
                for d in acts:
                    d["_agent_label"] = meta["label"]
                activities.extend(acts)
            activities.sort(key=lambda d: d.get("timestamp", ""),
                            reverse=True)
            # Limit to last 3 activities as per requirements
            activities = activities[:3]

            if activities:
                context_parts.append("\n--- Recent Activities (Last 3) ---")
                for d in activities:
                    context_parts.append(json.dumps(
                        _clean_doc(d), indent=2, default=str))
        except Exception as exc:
            logger.warning("symbol_chat: failed to load activities: %s", exc)
            context_parts.append("(Error loading activities from CosmosDB)")

    # Only include market data if requested
    if preferences.get('market_data', True):
        try:
            if provider is None:
                from src.yfinance_data_provider import get_shared_provider
                provider = get_shared_provider()

            market_data = await provider.fetch_all(symbol, force_refresh=force_refresh)

            sections = []
            for section_key, section_label in [
                ("overview", "Overview"),
                ("technicals", "Technicals"),
                ("forecast", "Forecast"),
                ("dividends", "Dividends"),
                ("options_chain", "Options Chain"),
            ]:
                content = market_data.get(section_key, "")
                if content and not content.startswith("[ERROR"):
                    if section_key == "options_chain":
                        from src.yfinance_data_provider import OPTIONS_CHAIN_SCHEMA_DESCRIPTION
                        content = OPTIONS_CHAIN_SCHEMA_DESCRIPTION + "\n" + content
                    sections.append(
                        f"\n--- {section_label} ---\n{content}")

            if sections:
                context_parts.append("\n".join(sections))
        except Exception as exc:
            logger.warning("symbol_chat: market data fetch failed: %s", exc)
            context_parts.append("(Live market data unavailable)")

    context_text = ("\n".join(context_parts) if context_parts
                    else "No context data available.")
    display_name = (symbol_doc.get("display_name", symbol)
                    if symbol_doc else symbol)

    return {
        "context": context_text,
        "exchange": exchange,
        "display_name": display_name,
        "cached_resources": cached_resources,
    }


def _build_symbol_system_prompt(symbol: str, exchange: str,
                                context_text: str) -> str:
    """Build the system prompt for per-symbol chat."""
    return (
        f"You are a stock options advisor focused exclusively on "
        f"{symbol} ({exchange}:{symbol}).\n"
        f"You have access to:\n"
        f"1. Recent analysis activities for this symbol\n"
        f"2. Live market data "
        f"(overview, technicals, forecast, dividends, options chain)\n"
        f"3. Current positions and watchlist status\n\n"
        f"Answer questions about this symbol's options opportunities, "
        f"risks, positions, and market conditions.\n"
        f"Stay focused on {symbol} — redirect if the user asks about "
        f"other symbols.\n\n"
        f"Context data:\n{context_text}"
    )


@app.post("/api/symbols/{symbol}/chat/context")
async def symbol_chat_context(request: Request, symbol: str):
    """Pre-fetch all heavy context (CosmosDB + market data) for a symbol.

    Pass ``"refresh": true`` in the JSON body to bypass the cache.
    """
    symbol = symbol.upper()
    cosmos = getattr(request.app.state, "cosmos", None)

    # Get preferences from request body
    try:
        body = await request.json()
        preferences = body.get('preferences', {})
        force_refresh = body.get('refresh', False)
    except Exception:
        preferences = {}
        force_refresh = False

    try:
        provider = getattr(request.app.state, "yf_provider", None)
        result = await _build_symbol_context(symbol, cosmos, preferences,
                                             force_refresh=force_refresh,
                                             provider=provider)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/symbols/{symbol}/chat")
async def symbol_chat_api(request: Request, symbol: str):
    body = await request.json()
    messages = body.get("messages", [])
    if not messages:
        return JSONResponse({"error": "No messages provided"},
                            status_code=400)

    symbol = symbol.upper()

    # Use pre-fetched context if provided, otherwise fetch fresh
    pre_context = body.get("context")
    if pre_context:
        context_text = pre_context
        # Infer exchange from context or fall back
        cosmos = getattr(request.app.state, "cosmos", None)
        exchange = "NYSE"
        if cosmos:
            try:
                symbol_doc = cosmos.get_symbol(symbol)
                if symbol_doc:
                    exchange = symbol_doc.get("exchange", "NYSE")
            except Exception:
                pass
    else:
        cosmos = getattr(request.app.state, "cosmos", None)
        provider = getattr(request.app.state, "yf_provider", None)
        result = await _build_symbol_context(symbol, cosmos, provider=provider)
        context_text = result["context"]
        exchange = result["exchange"]

    system_prompt = _build_symbol_system_prompt(symbol, exchange, context_text)

    config_obj, err_resp = _llm_settings_response("symbol_chat")
    if err_resp:
        return err_resp

    model = config_obj.model_for('symbol_chat')

    try:
        from src.llm import create_sync_chat_client, chat_completion

        client = create_sync_chat_client(
            _function_llm_config(config_obj, "symbol_chat")
        )
        api_messages = [{"role": "system", "content": system_prompt}]
        for m in messages:
            api_messages.append({"role": m["role"], "content": m["content"]})

        reply = chat_completion(
            client,
            model=model,
            messages=api_messages,
            temperature=0.7,
            max_completion_tokens=2048,
        )
        return JSONResponse({"reply": reply})

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
