"""Daily dashboard banner content generator."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

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
_ACTIVITY_MAX_AGE = timedelta(hours=24)
_MARKET_MAX_AGE = timedelta(days=7)
_DEFAULT_PROVIDER_TIMEOUT_SECONDS = 30.0
_RECOGNIZED_OSCILLATOR_INDICATORS = {
    "RSI": "RSI (14)",
    "Stoch.K": "Stochastic %K (14,3,3)",
    "CCI20": "CCI (20)",
    "ADX": "ADX (14)",
    "AO": "Awesome Oscillator",
    "Mom": "Momentum (10)",
    "MACD.macd": "MACD Level (12,26)",
    "W.R": "Williams %R (14)",
    "BBPower": "Bull Bear Power",
    "UO": "Ultimate Oscillator (7,14,28)",
}
_RECOGNIZED_MOVING_AVERAGE_INDICATORS = {
    "EMA10": "EMA (10)",
    "SMA10": "SMA (10)",
    "EMA20": "EMA (20)",
    "SMA20": "SMA (20)",
    "EMA30": "EMA (30)",
    "SMA30": "SMA (30)",
    "EMA50": "EMA (50)",
    "SMA50": "SMA (50)",
    "EMA100": "EMA (100)",
    "SMA100": "SMA (100)",
    "EMA200": "EMA (200)",
    "SMA200": "SMA (200)",
    "Ichimoku.BLine": "Ichimoku Base Line (9,26,52,26)",
    "VWMA": "VWMA (20)",
    "HullMA9": "Hull MA (9)",
}
_TECHNICAL_RECOMMENDATION_LABELS = frozenset({
    "Strong Buy",
    "Buy",
    "Neutral",
    "Sell",
    "Strong Sell",
})
_INDICATOR_SIGNALS = frozenset({"Buy", "Sell", "Neutral"})
_TECHNICAL_SIGNAL_COUNT = 25
_MOVING_AVERAGE_SIGNAL_COUNT = 15
_NO_RECENT_DATA_ITEM = {
    "emoji": "ℹ️",
    "text": "No recent eligible data — refresh market sources and monitoring outputs",
    "category": "actionable_alert",
    "priority": 5,
    "symbol": "MARKET",
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


def _require_json_object(raw: Any, field: str) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise TypeError(f"market payload {field} is not JSON")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"market payload {field} is malformed JSON") from exc
    if not isinstance(parsed, dict):
        raise TypeError(f"market payload {field} is not a JSON object")
    return parsed


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
        raise TypeError("Banner agent JSON did not contain an items list")

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


def _parse_utc(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromtimestamp(float(raw), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _eligible_source_time(value: Any, *, now: datetime) -> datetime | None:
    parsed = _parse_utc(value)
    if parsed is None or parsed < now - _MARKET_MAX_AGE or parsed > now:
        return None
    return parsed


def _finite_number(
    value: Any,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    if minimum is not None and value < minimum:
        return None
    if maximum is not None and value > maximum:
        return None
    return value


def _meaningful_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text or text.casefold() in {
        "n/a",
        "na",
        "none",
        "null",
        "unknown",
        "unavailable",
        "not available",
        "error",
    }:
        return None
    return text


def _recommendation_label(value: float) -> str:
    if value >= 0.5:
        return "Strong Buy"
    if value > 0.1:
        return "Buy"
    if value >= -0.1:
        return "Neutral"
    if value > -0.5:
        return "Sell"
    return "Strong Sell"


def _validated_recommendation_section(
    section: dict[str, Any],
    *,
    expected_signal_count: int,
) -> dict[str, Any] | None:
    recommendation = section.get("recommendation")
    if not isinstance(recommendation, dict):
        return None
    label = recommendation.get("label")
    value = recommendation.get("value")
    if (
        not isinstance(label, str)
        or label not in _TECHNICAL_RECOMMENDATION_LABELS
        or isinstance(value, bool)
        or not isinstance(value, float)
        or not math.isfinite(value)
        or not -1 <= value <= 1
    ):
        return None

    counts: dict[str, int] = {}
    for field in ("buy", "sell", "neutral"):
        count = section.get(field)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            return None
        counts[field] = count
    total = sum(counts.values())
    if total != expected_signal_count:
        return None

    computed_value = (counts["buy"] - counts["sell"]) / total
    if (
        not math.isclose(value, computed_value, rel_tol=1e-12, abs_tol=1e-12)
        or label != _recommendation_label(value)
    ):
        return None
    return {
        "recommendation": {"label": label, "value": value},
        **counts,
    }


def _recognized_indicator_measurements(
    section: dict[str, Any],
    recognized_indicators: dict[str, str],
) -> dict[str, int | float]:
    indicators = section.get("indicators")
    if not isinstance(indicators, dict):
        return {}
    measurements: dict[str, int | float] = {}
    for name, expected_label in recognized_indicators.items():
        indicator = indicators.get(name)
        if not isinstance(indicator, dict):
            continue
        value = _finite_number(indicator.get("value"))
        if (
            value is not None
            and indicator.get("label") == expected_label
            and isinstance(indicator.get("formatted"), str)
            and bool(indicator["formatted"])
            and indicator.get("signal") in _INDICATOR_SIGNALS
        ):
            measurements[name] = value
    return measurements


def _activity_is_eligible(activity: Any, known_symbols: set[str]) -> bool:
    if not isinstance(activity, dict):
        return False
    if str(activity.get("symbol", "")).strip().upper() not in known_symbols:
        return False
    if _meaningful_text(activity.get("summary") or activity.get("reason")):
        return True
    if _meaningful_text(activity.get("waiting_for")):
        return True
    risk_flags = activity.get("risk_flags")
    if isinstance(risk_flags, list) and any(
        _meaningful_text(flag) for flag in risk_flags
    ):
        return True
    action = _meaningful_text(activity.get("activity"))
    if action is None:
        return False
    normalized_action = action.upper()
    return normalized_action in {
        "ACCUMULATE",
        "AVOID",
        "BUY",
        "CLOSE",
        "SELL",
        "STRONG_BUY",
        "UNFAVORABLE",
    } or normalized_action.startswith("ROLL")


def _market_payload_as_of(
    payload: Any,
    *,
    now: datetime,
) -> tuple[dict[str, str], datetime | None]:
    """Return only banner facts backed by plausible, recent source timestamps."""
    if not isinstance(payload, dict):
        raise TypeError("market provider returned a non-object payload")

    parsed = {
        field: _require_json_object(payload.get(field), field)
        for field in ("overview", "technicals", "forecast", "dividends")
    }
    source = _require_json_object(payload.get("_source"), "_source")
    errors = source.get("errors", [])
    if errors:
        detail = ", ".join(str(error) for error in errors)
        raise RuntimeError(f"market provider reported an upstream failure: {detail}")

    timestamps = source.get("timestamps")
    if not isinstance(timestamps, dict):
        return {}, None
    market_as_of = _eligible_source_time(timestamps.get("market"), now=now)
    history_as_of = _eligible_source_time(timestamps.get("history"), now=now)

    overview: dict[str, Any] = {"fundamentals": {}}
    dividends: dict[str, Any] = {"dividends": {}}
    technicals: dict[str, Any] = {}
    forecast: dict[str, Any] = {}
    used_timestamps: list[datetime] = []

    if market_as_of is not None:
        fundamentals = parsed["overview"].get("fundamentals")
        if not isinstance(fundamentals, dict):
            fundamentals = {}
        clean_fundamentals = overview["fundamentals"]

        price = _finite_number(
            _field_value(fundamentals, "current_price", "value"),
            minimum=0.0000001,
        )
        if price is not None:
            clean_fundamentals["current_price"] = {"value": price}

        earnings = _upcoming_date(
            _field_value(fundamentals, "earnings_release_next_date_fq", "value"),
            now,
        )
        if earnings is not None:
            clean_fundamentals["earnings_release_next_date_fq"] = {
                "value": earnings
            }

        dividend_data = parsed["dividends"].get("dividends")
        if not isinstance(dividend_data, dict):
            dividend_data = {}
        clean_dividends = dividends["dividends"]
        ex_dividend = _upcoming_date(
            _field_value(dividend_data, "ex_dividend_date_recent", "value"),
            now,
        )
        if ex_dividend is not None:
            clean_dividends["ex_dividend_date_recent"] = {"value": ex_dividend}
        dividend_yield = _finite_number(
            _field_value(dividend_data, "dividends_yield", "value"),
            minimum=0.0000001,
        )
        if dividend_yield is not None:
            clean_dividends["dividends_yield"] = {"value": dividend_yield}

        analyst_label = _meaningful_text(
            _field_value(
                parsed["forecast"],
                "analyst_rating",
                "overall_rating",
                "label",
            )
        )
        analyst_value = _finite_number(
            _field_value(
                parsed["forecast"],
                "analyst_rating",
                "overall_rating",
                "value",
            ),
            minimum=1,
            maximum=5,
        )
        if analyst_label is not None and analyst_value is not None:
            forecast["analyst_rating"] = {
                "overall_rating": {
                    "label": analyst_label,
                    "value": analyst_value,
                }
            }
        upside = _finite_number(
            _field_value(parsed["forecast"], "price_target", "upside_pct")
        )
        if upside is not None:
            forecast["price_target"] = {"upside_pct": upside}

        if clean_fundamentals or clean_dividends or forecast:
            used_timestamps.append(market_as_of)

    if history_as_of is not None:
        summary = parsed["technicals"].get("summary")
        if not isinstance(summary, dict):
            summary = {}
        oscillators = parsed["technicals"].get("oscillators")
        if not isinstance(oscillators, dict):
            oscillators = {}
        moving_averages = parsed["technicals"].get("moving_averages")
        if not isinstance(moving_averages, dict):
            moving_averages = {}
        oscillator_measurements = _recognized_indicator_measurements(
            oscillators,
            _RECOGNIZED_OSCILLATOR_INDICATORS,
        )
        moving_average_measurements = _recognized_indicator_measurements(
            moving_averages,
            _RECOGNIZED_MOVING_AVERAGE_INDICATORS,
        )
        clean_summary = _validated_recommendation_section(
            summary,
            expected_signal_count=_TECHNICAL_SIGNAL_COUNT,
        )
        if clean_summary and (
            oscillator_measurements or moving_average_measurements
        ):
            technicals["summary"] = clean_summary

        rsi = _finite_number(
            oscillator_measurements.get("RSI"),
            minimum=0.0000001,
            maximum=99.9999999,
        )
        if rsi is not None:
            technicals["oscillators"] = {
                "indicators": {"RSI": {"value": rsi}}
            }
        clean_moving_average = _validated_recommendation_section(
            moving_averages,
            expected_signal_count=_MOVING_AVERAGE_SIGNAL_COUNT,
        )
        if clean_moving_average and moving_average_measurements:
            technicals["moving_averages"] = {
                **clean_moving_average,
                "indicators": {
                    name: {"value": value}
                    for name, value in sorted(moving_average_measurements.items())
                },
            }
        if technicals:
            used_timestamps.append(history_as_of)

    if not used_timestamps:
        return {}, None
    return {
        "overview": json.dumps(overview),
        "technicals": json.dumps(technicals),
        "forecast": json.dumps(forecast),
        "dividends": json.dumps(dividends),
    }, max(used_timestamps)


async def _fetch_market_payload(
    provider: Any,
    symbol: str,
    *,
    timeout_seconds: float,
) -> Any:
    """Run the blocking provider in an abandonable daemon thread."""
    loop = asyncio.get_running_loop()
    result: asyncio.Future[Any] = loop.create_future()

    def settle(value: Any = None, error: BaseException | None = None) -> None:
        if result.done():
            return
        if error is not None:
            result.set_exception(error)
        else:
            result.set_result(value)

    def worker() -> None:
        try:
            value = asyncio.run(provider.fetch_all(symbol, force_refresh=True))
        except BaseException as exc:  # noqa: BLE001
            try:
                loop.call_soon_threadsafe(settle, None, exc)
            except RuntimeError:
                pass
        else:
            try:
                loop.call_soon_threadsafe(settle, value, None)
            except RuntimeError:
                pass

    threading.Thread(
        target=worker,
        daemon=True,
        name=f"BannerMarket-{symbol}",
    ).start()
    try:
        return await asyncio.wait_for(result, timeout=timeout_seconds)
    except TimeoutError as exc:
        raise TimeoutError(
            f"market provider timed out for {symbol} after {timeout_seconds:g}s"
        ) from exc


def _upcoming_date(value: Any, now: datetime) -> str | None:
    parsed = _parse_utc(value)
    if parsed is None or parsed.date() < now.date():
        return None
    return parsed.date().isoformat()


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


def _build_symbol_snapshot(
    symbol_doc: dict[str, Any],
    market_data: dict[str, str],
    recent_activity: list[dict[str, Any]],
    *,
    now: datetime,
) -> dict[str, Any]:
    overview = _safe_json_loads(market_data.get("overview"))
    dividends = _safe_json_loads(market_data.get("dividends"))
    technicals = _safe_json_loads(market_data.get("technicals"))
    forecast = _safe_json_loads(market_data.get("forecast"))

    fundamentals = overview.get("fundamentals", {})
    dividend_data = dividends.get("dividends", {})

    active_positions = []
    stale_active_position_count = 0
    for position in symbol_doc.get("positions", []):
        if position.get("status") != "active":
            continue
        expiration = _parse_utc(position.get("expiration"))
        if expiration is not None and expiration.date() < now.date():
            stale_active_position_count += 1
            continue
        active_positions.append({
            "type": position.get("type"),
            "strike": position.get("strike"),
            "expiration": position.get("expiration"),
        })

    earnings_value = _field_value(
        fundamentals, "earnings_release_next_date_fq", "value"
    )
    ex_dividend_value = _field_value(
        dividend_data, "ex_dividend_date_recent", "value"
    )

    return {
        "symbol": symbol_doc.get("symbol"),
        "display_name": symbol_doc.get("display_name") or symbol_doc.get("symbol"),
        "exchange": symbol_doc.get("exchange"),
        "watchlist": symbol_doc.get("watchlist", {}),
        "active_positions": active_positions,
        "stale_active_position_count": stale_active_position_count,
        "market_data": {
            "price": _field_value(fundamentals, "current_price", "value") or technicals.get("price"),
            "earnings_date": _upcoming_date(earnings_value, now),
            "ex_dividend_date": _upcoming_date(ex_dividend_value, now),
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
    provider_timeout = max(
        1.0,
        min(
            300.0,
            float(
                banner_config.get(
                    "provider_timeout_seconds",
                    _DEFAULT_PROVIDER_TIMEOUT_SECONDS,
                )
            ),
        ),
    )
    model = config.model_for("banner")

    now = datetime.now(timezone.utc)
    all_symbols = cosmos.list_symbols()
    if not all_symbols:
        logger.info("Banner agent: no symbols configured")
        return _persist_banner(
            cosmos,
            [_NO_RECENT_DATA_ITEM],
            model=model,
            source_watermarks={},
            source_counts={"activities": 0, "market_snapshots": 0},
        )

    provider = get_shared_provider(getattr(config, "yfinance_config", None))
    activity_since = now - _ACTIVITY_MAX_AGE
    activity_limit = max(500, len(all_symbols) * 20)
    if hasattr(cosmos, "get_recent_banner_activities"):
        recent_activity_docs = cosmos.get_recent_banner_activities(
            since=activity_since,
            limit=activity_limit,
        )
    else:
        candidates = cosmos.get_all_activities(limit=activity_limit)
        recent_activity_docs = [
            activity
            for activity in candidates
            if (_parse_utc(activity.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc))
            >= activity_since
        ]
        recent_activity_docs.sort(
            key=lambda activity: _parse_utc(activity.get("timestamp"))
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

    recent_activity_docs = [
        activity
        for activity in recent_activity_docs
        if (
            (parsed := _parse_utc(activity.get("timestamp"))) is not None
            and activity_since <= parsed <= now
            and _activity_is_eligible(
                activity,
                {
                    str(symbol_doc.get("symbol", "")).strip().upper()
                    for symbol_doc in all_symbols
                },
            )
        )
    ]
    recent_activity_docs.sort(
        key=lambda activity: _parse_utc(activity.get("timestamp"))
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    activities_by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for activity in recent_activity_docs:
        symbol = str(activity.get("symbol", "")).upper()
        if symbol:
            activities_by_symbol[symbol].append(activity)

    symbol_snapshots: list[dict[str, Any]] = []
    market_watermarks: dict[str, str] = {}
    provider_failures: list[str] = []
    for symbol_doc in all_symbols:
        symbol = symbol_doc["symbol"]
        try:
            market_data = await _fetch_market_payload(
                provider,
                symbol,
                timeout_seconds=provider_timeout,
            )
            market_data, market_as_of = _market_payload_as_of(
                market_data,
                now=now,
            )
            if market_as_of is None:
                logger.warning(
                    "Banner agent: market data for %s has no recent actual source timestamp",
                    symbol,
                )
                if activities_by_symbol.get(symbol):
                    symbol_snapshots.append(
                        _build_symbol_snapshot(
                            symbol_doc,
                            {},
                            activities_by_symbol[symbol],
                            now=now,
                        )
                    )
                continue
            market_watermarks[f"market:{symbol}"] = market_as_of.strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            symbol_snapshots.append(
                _build_symbol_snapshot(
                    symbol_doc,
                    market_data,
                    activities_by_symbol.get(symbol, []),
                    now=now,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Banner agent: failed to load market data for %s: %s", symbol, exc)
            provider_failures.append(f"{symbol}: {exc}")

    if provider_failures:
        raise RuntimeError(
            "Dashboard banner market refresh failed: " + "; ".join(provider_failures)
        )

    source_watermarks = {
        "latest_activity_at": (
            recent_activity_docs[0].get("timestamp")
            if recent_activity_docs
            else None
        ),
        **market_watermarks,
    }
    source_watermarks = {
        key: value for key, value in source_watermarks.items() if value
    }
    source_counts = {
        "activities": len(recent_activity_docs),
        "market_snapshots": len(market_watermarks),
    }
    if not any(source_counts.values()):
        return _persist_banner(
            cosmos,
            [_NO_RECENT_DATA_ITEM],
            model=model,
            source_watermarks={},
            source_counts=source_counts,
        )

    prompt = f"""Current UTC timestamp: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}
Generate between 5 and {max_items} banner items.
Prioritize the strongest mix of earnings proximity, ex-div proximity, trend changes, actionable alerts, and risk warnings.
All event dates in the input have already passed freshness validation.
Do not infer missing event dates or reuse wording from any previous banner.

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

    persisted = _persist_banner(
        cosmos,
        items,
        model=model,
        source_watermarks=source_watermarks,
        source_counts=source_counts,
    )
    logger.info("Banner agent: saved %d items", len(items))
    persisted["symbols_analyzed"] = len(symbol_snapshots)
    return persisted


def _persist_banner(
    cosmos,
    items: list[dict[str, Any]],
    *,
    model: str,
    source_watermarks: dict[str, str],
    source_counts: dict[str, int],
) -> dict[str, Any]:
    canonical = json.dumps(items, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    generation_id = str(uuid4())
    parsed_watermarks = [
        parsed
        for parsed in (_parse_utc(value) for value in source_watermarks.values())
        if parsed is not None
    ]
    source_as_of = (
        max(parsed_watermarks).strftime("%Y-%m-%dT%H:%M:%SZ")
        if parsed_watermarks
        else None
    )
    prior = cosmos.get_banner() if hasattr(cosmos, "get_banner") else None
    prior_generated_at = (
        _parse_utc(prior.get("generated_at"))
        if isinstance(prior, dict)
        else None
    )
    generated_at_dt = datetime.now(timezone.utc)
    if prior_generated_at is not None and generated_at_dt <= prior_generated_at:
        generated_at_dt = prior_generated_at + timedelta(microseconds=1)
    generated_at = generated_at_dt.isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )
    persisted = cosmos.save_banner(
        items,
        model=model,
        generated_at=generated_at,
        source_as_of=source_as_of,
        source_watermarks=source_watermarks,
        source_counts=source_counts,
        content_hash=content_hash,
        generation_id=generation_id,
    )
    if not isinstance(persisted, dict):
        raise TypeError("Dashboard banner persistence returned no document")
    if persisted.get("content_hash") != content_hash or persisted.get("items") != items:
        raise RuntimeError("Dashboard banner persistence did not update content")
    if persisted.get("generation_id") != generation_id:
        raise RuntimeError("Dashboard banner persistence did not advance generation")
    expected_metadata = {
        "generated_at": generated_at,
        "source_as_of": source_as_of,
        "source_watermarks": source_watermarks,
        "source_counts": source_counts,
    }
    if any(persisted.get(key) != value for key, value in expected_metadata.items()):
        raise RuntimeError("Dashboard banner persistence returned stale source metadata")

    reloaded = cosmos.get_banner() if hasattr(cosmos, "get_banner") else persisted
    if not isinstance(reloaded, dict):
        raise TypeError("Dashboard banner persistence could not be reloaded")
    expected_document = {
        "generated_at": generated_at,
        "generation_id": generation_id,
        "content_hash": content_hash,
        "items": items,
        "source_as_of": source_as_of,
        "source_watermarks": source_watermarks,
        "source_counts": source_counts,
    }
    if any(reloaded.get(key) != value for key, value in expected_document.items()):
        raise RuntimeError("Dashboard banner persisted document failed verification")
    reloaded_generated_at = _parse_utc(reloaded.get("generated_at"))
    if reloaded_generated_at is None or (
        prior_generated_at is not None
        and reloaded_generated_at <= prior_generated_at
    ):
        raise RuntimeError("Dashboard banner generated_at did not advance")
    return reloaded
