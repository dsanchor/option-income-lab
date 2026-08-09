"""Daily dashboard banner content generator."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from agent_framework import Agent

from .banner_instructions import DASHBOARD_BANNER_INSTRUCTIONS
from .llm import create_async_chat_client
from .yfinance_data_provider import get_shared_provider

logger = logging.getLogger(__name__)

_ALLOWED_CATEGORIES = {
    "earnings_proximity",
    "ex_div_proximity",
    "trend_change",
    "actionable_alert",
    "risk_warning",
}


def _safe_json_loads(raw: str | dict | None) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_json_object(response_text: str) -> dict[str, Any] | None:
    response_text = (response_text or "").strip()
    if not response_text:
        return None

    candidates = [response_text]
    if "```json" in response_text:
        for block in response_text.split("```json")[1:]:
            candidate = block.split("```", 1)[0].strip()
            if candidate:
                candidates.append(candidate)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"items": parsed}
    return None


def _truncate_text(text: str, limit: int = 80) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _normalize_banner_item(item: dict[str, Any], known_symbols: set[str]) -> dict[str, Any] | None:
    text = _truncate_text(str(item.get("text", "")).strip())
    if not text:
        return None

    category = str(item.get("category", "")).strip().lower()
    if category not in _ALLOWED_CATEGORIES:
        category = "actionable_alert"

    try:
        priority = int(item.get("priority", 3))
    except (TypeError, ValueError):
        priority = 3
    priority = max(1, min(5, priority))

    raw_symbol = str(item.get("symbol", "")).strip().upper()
    symbol = raw_symbol if raw_symbol in known_symbols else "MARKET"

    emoji = str(item.get("emoji", "📊")).strip() or "📊"

    return {
        "emoji": emoji,
        "text": text,
        "category": category,
        "priority": priority,
        "symbol": symbol,
    }


def _extract_banner_items(response_text: str, *, max_items: int, known_symbols: set[str]) -> list[dict[str, Any]]:
    payload = _extract_json_object(response_text)
    if payload is None:
        raise ValueError("Banner agent did not return valid JSON")

    raw_items = payload.get("items", [])
    if not isinstance(raw_items, list):
        raise ValueError("Banner agent JSON did not contain an items list")

    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        item = _normalize_banner_item(raw_item, known_symbols)
        if item is None:
            continue
        dedupe_key = (item["symbol"], item["text"].lower())
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized.append(item)

    normalized.sort(key=lambda item: (-item["priority"], item["symbol"], item["text"]))
    return normalized[:max_items]


def _field_value(data: dict[str, Any], *path: str) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _recent_activity_summary(activity: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": activity.get("timestamp"),
        "agent_type": activity.get("agent_type"),
        "activity": activity.get("activity"),
        "is_alert": bool(activity.get("is_alert")),
        "confidence": activity.get("confidence"),
        "summary": _truncate_text(str(activity.get("summary") or activity.get("reason") or ""), 120),
        "assignment_risk": activity.get("assignment_risk"),
        "risk_flags": activity.get("risk_flags", []),
        "waiting_for": activity.get("waiting_for"),
    }


def _build_symbol_snapshot(symbol_doc: dict[str, Any], market_data: dict[str, str], recent_activity: list[dict[str, Any]]) -> dict[str, Any]:
    overview = _safe_json_loads(market_data.get("overview"))
    dividends = _safe_json_loads(market_data.get("dividends"))
    technicals = _safe_json_loads(market_data.get("technicals"))
    forecast = _safe_json_loads(market_data.get("forecast"))

    fundamentals = overview.get("fundamentals", {})
    dividend_data = dividends.get("dividends", {})

    return {
        "symbol": symbol_doc.get("symbol"),
        "display_name": symbol_doc.get("display_name") or symbol_doc.get("symbol"),
        "exchange": symbol_doc.get("exchange"),
        "watchlist": symbol_doc.get("watchlist", {}),
        "active_positions": [
            {
                "type": position.get("type"),
                "strike": position.get("strike"),
                "expiration": position.get("expiration"),
            }
            for position in symbol_doc.get("positions", [])
            if position.get("status") == "active"
        ],
        "market_data": {
            "price": _field_value(fundamentals, "current_price", "value") or technicals.get("price"),
            "earnings_date": _field_value(fundamentals, "earnings_release_next_date_fq", "formatted"),
            "ex_dividend_date": _field_value(dividend_data, "ex_dividend_date_recent", "formatted"),
            "dividend_yield_pct": _field_value(dividend_data, "dividends_yield", "value"),
            "technical_recommendation": _field_value(technicals, "summary", "recommendation", "label"),
            "technical_buy_count": _field_value(technicals, "summary", "buy"),
            "technical_sell_count": _field_value(technicals, "summary", "sell"),
            "rsi": _field_value(technicals, "oscillators", "indicators", "RSI", "value"),
            "moving_average_recommendation": _field_value(technicals, "moving_averages", "recommendation", "label"),
            "analyst_rating": _field_value(forecast, "analyst_rating", "overall_rating", "label"),
            "target_upside_pct": _field_value(forecast, "price_target", "upside_pct"),
        },
        "recent_activity": [_recent_activity_summary(item) for item in recent_activity[:5]],
    }


async def run_banner_agent(config, cosmos) -> dict[str, Any]:
    """Generate and persist the daily dashboard banner."""
    banner_config = config.config.get("banner_agent", {})
    max_items = max(1, min(20, int(banner_config.get("max_items", 10))))
    model = config.model_for("banner")

    all_symbols = cosmos.list_symbols()
    if not all_symbols:
        logger.info("Banner agent: no symbols configured")
        cosmos.save_banner([], model=model)
        return {"items": [], "symbols_analyzed": 0}

    provider = get_shared_provider(getattr(config, "yfinance_config", None))
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    recent_activity_docs = cosmos.get_all_activities(since=since, limit=max(500, len(all_symbols) * 20))

    activities_by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for activity in recent_activity_docs:
        symbol = str(activity.get("symbol", "")).upper()
        if symbol:
            activities_by_symbol[symbol].append(activity)

    symbol_snapshots: list[dict[str, Any]] = []
    for symbol_doc in all_symbols:
        symbol = symbol_doc["symbol"]
        try:
            market_data = await provider.fetch_all(symbol)
            symbol_snapshots.append(
                _build_symbol_snapshot(symbol_doc, market_data, activities_by_symbol.get(symbol, []))
            )
        except Exception as exc:
            logger.warning("Banner agent: failed to load market data for %s: %s", symbol, exc)
            symbol_snapshots.append(
                {
                    "symbol": symbol,
                    "display_name": symbol_doc.get("display_name") or symbol,
                    "exchange": symbol_doc.get("exchange"),
                    "watchlist": symbol_doc.get("watchlist", {}),
                    "active_positions": [
                        {
                            "type": position.get("type"),
                            "strike": position.get("strike"),
                            "expiration": position.get("expiration"),
                        }
                        for position in symbol_doc.get("positions", [])
                        if position.get("status") == "active"
                    ],
                    "market_data": {},
                    "recent_activity": [_recent_activity_summary(item) for item in activities_by_symbol.get(symbol, [])[:5]],
                }
            )

    prompt = f"""Current UTC timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
Generate between 5 and {max_items} banner items.
Prioritize the strongest mix of earnings proximity, ex-div proximity, trend changes, actionable alerts, and risk warnings.

Source data:
```json
{json.dumps({'symbols': symbol_snapshots}, indent=2, default=str)}
```
"""

    agent = Agent(
        name="DashboardBannerAgent",
        client=create_async_chat_client(
            model, config.llm_config_for_function("banner")
        ),
        instructions=DASHBOARD_BANNER_INSTRUCTIONS,
    )
    response = await agent.run(prompt)
    banner_text = (response.text or "").strip()
    items = _extract_banner_items(
        banner_text,
        max_items=max_items,
        known_symbols={doc["symbol"] for doc in all_symbols},
    )
    if not items:
        raise ValueError("Banner agent returned no valid items")

    cosmos.save_banner(items, model=model)
    logger.info("Banner agent: saved %d items", len(items))
    return {
        "items": items,
        "symbols_analyzed": len(symbol_snapshots),
        "source_activity_count": len(recent_activity_docs),
    }
