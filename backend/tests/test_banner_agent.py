import asyncio
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

if "agent_framework" not in sys.modules:
    sys.modules["agent_framework"] = types.SimpleNamespace(Agent=object)

if "azure.cosmos" not in sys.modules:
    azure_module = types.ModuleType("azure")
    azure_module.__path__ = []
    core_module = types.ModuleType("azure.core")
    core_module.MatchConditions = object
    cosmos_module = types.ModuleType("azure.cosmos")
    cosmos_module.CosmosClient = object
    cosmos_module.PartitionKey = object
    exceptions_module = types.ModuleType("azure.cosmos.exceptions")

    class _CosmosResourceNotFoundError(Exception):
        pass

    exceptions_module.CosmosResourceNotFoundError = _CosmosResourceNotFoundError
    exceptions_module.CosmosHttpResponseError = Exception
    sys.modules["azure"] = azure_module
    sys.modules["azure.core"] = core_module
    sys.modules["azure.cosmos"] = cosmos_module
    sys.modules["azure.cosmos.exceptions"] = exceptions_module

from src import banner_agent
from src.banner_agent import (
    _NO_RECENT_DATA_ITEM,
    _RECOGNIZED_MOVING_AVERAGE_INDICATORS,
    _build_symbol_snapshot,
    _extract_banner_items,
    _market_payload_as_of,
    run_banner_agent,
)
from src.cosmos_db import CosmosDBService


class TestBannerItemExtraction:
    def test_extracts_and_normalizes_items_from_json_object(self):
        response_text = """
```json
{
  "items": [
    {
      "emoji": "⚠️",
      "text": "AAPL earnings in 2d — review covered call exposure immediately",
      "category": "earnings_proximity",
      "priority": 5,
      "symbol": "AAPL"
    },
    {
      "emoji": "📈",
      "text": "MSFT trend flipped to strong buy",
      "category": "trend_change",
      "priority": "4",
      "symbol": "MSFT"
    }
  ]
}
```
"""
        items = _extract_banner_items(
            response_text,
            max_items=10,
            known_symbols={"AAPL", "MSFT"},
        )

        assert len(items) == 2
        assert items[0]["symbol"] == "AAPL"
        assert items[0]["priority"] == 5
        assert len(items[0]["text"]) <= 80
        assert items[1]["category"] == "trend_change"

    def test_unknown_symbol_and_invalid_category_are_normalized(self):
        response_text = '{"items": [{"emoji": "🧠", "text": "Macro risk rising", "category": "macro", "priority": 9, "symbol": "SPY"}]}'

        items = _extract_banner_items(
            response_text,
            max_items=5,
            known_symbols={"AAPL"},
        )

        assert items == [{
            "emoji": "🧠",
            "text": "Macro risk rising",
            "category": "actionable_alert",
            "priority": 5,
            "symbol": "MARKET",
        }]


class TestCosmosBannerMethods:
    def test_save_banner_upserts_system_document(self):
        service = CosmosDBService.__new__(CosmosDBService)
        service.container = MagicMock()
        service.container.upsert_item.return_value = {"ok": True}

        items = [{"emoji": "⚠️", "text": "AAPL earnings soon", "category": "earnings_proximity", "priority": 5, "symbol": "AAPL"}]
        result = service.save_banner(items, model="gpt-5.4-mini")

        assert result == {"ok": True}
        saved_doc = service.container.upsert_item.call_args.args[0]
        assert saved_doc["id"] == "dashboard_banner"
        assert saved_doc["symbol"] == "_system"
        assert saved_doc["doc_type"] == "banner"
        assert saved_doc["schema_version"] == 2
        assert saved_doc["items"] == items
        assert saved_doc["model"] == "gpt-5.4-mini"

    def test_get_banner_reads_system_partition(self):
        service = CosmosDBService.__new__(CosmosDBService)
        service.container = MagicMock()
        service.container.read_item.return_value = {"id": "dashboard_banner", "items": []}

        result = service.get_banner()

        assert result == {"id": "dashboard_banner", "items": []}
        service.container.read_item.assert_called_once_with(
            item="dashboard_banner",
            partition_key="_system",
        )

    def test_banner_activity_query_parses_orders_and_consumes_all_pages(self):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=20)).isoformat()
        recent = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        newest = (now - timedelta(minutes=1)).isoformat(timespec="milliseconds")
        pages = iter([
            {"id": "old", "timestamp": old},
            {"id": "newest", "timestamp": newest},
            {"id": "recent", "timestamp": recent},
            {"id": "invalid", "timestamp": "09/25/2026"},
        ])
        service = CosmosDBService.__new__(CosmosDBService)
        service.container = MagicMock()
        service.container.query_items.return_value = pages

        result = service.get_recent_banner_activities(
            since=now - timedelta(hours=24),
            limit=2,
        )

        assert [item["id"] for item in result] == ["newest", "recent"]
        service.container.query_items.assert_called_once_with(
            query="SELECT * FROM c WHERE c.doc_type = 'activity'",
            enable_cross_partition_query=True,
        )


def _market_data(*, earnings, ex_dividend, price=100, source_as_of=None):
    source_as_of = source_as_of or datetime.now(timezone.utc).isoformat()
    return {
        "overview": (
            '{"fundamentals": {'
            f'"current_price": {{"value": {price}}},'
            f'"earnings_release_next_date_fq": {{"value": "{earnings}"}}'
            "}}"
        ),
        "dividends": (
            '{"dividends": {'
            f'"ex_dividend_date_recent": {{"value": "{ex_dividend}"}}'
            "}}"
        ),
        "technicals": "{}",
        "forecast": "{}",
        "_source": json.dumps({
            "timestamps": {"market": source_as_of},
            "errors": [],
        }),
    }


_INDICATOR_LABELS = {
    "RSI": "RSI (14)",
    "EMA20": "EMA (20)",
    "SMA50": "SMA (50)",
}


def _indicator(name, value, *, signal="Neutral"):
    return {
        "label": _INDICATOR_LABELS[name],
        "value": value,
        "formatted": str(value),
        "signal": signal,
    }


def _recommendation(label, value, *, buy, sell, neutral):
    return {
        "recommendation": {"label": label, "value": value},
        "buy": buy,
        "sell": sell,
        "neutral": neutral,
    }


def test_snapshot_excludes_twenty_day_old_events_and_expired_active_positions():
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    snapshot = _build_symbol_snapshot(
        {
            "symbol": "AAPL",
            "positions": [
                {
                    "status": "active",
                    "type": "call",
                    "strike": 200,
                    "expiration": "2026-09-05",
                },
                {
                    "status": "active",
                    "type": "put",
                    "strike": 180,
                    "expiration": "2026-10-16",
                },
            ],
        },
        _market_data(earnings="2026-09-05", ex_dividend="2026-09-04"),
        [],
        now=now,
    )

    assert snapshot["market_data"]["earnings_date"] is None
    assert snapshot["market_data"]["ex_dividend_date"] is None
    assert snapshot["stale_active_position_count"] == 1
    assert snapshot["active_positions"] == [
        {"type": "put", "strike": 180, "expiration": "2026-10-16"}
    ]


class _FakeCosmos:
    def __init__(self, *, activities=None, symbols=None):
        self.activities = activities or []
        self.symbols = symbols if symbols is not None else [{"symbol": "AAPL"}]
        self.saved = []
        self.current_banner = None

    def list_symbols(self):
        return self.symbols

    def get_recent_banner_activities(self, *, since, limit):
        eligible = []
        for item in self.activities:
            parsed = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00"))
            if parsed >= since:
                eligible.append(item)
        return sorted(eligible, key=lambda item: item["timestamp"], reverse=True)[:limit]

    def save_banner(self, items, **metadata):
        doc = {
            "id": "dashboard_banner",
            "items": items,
            **metadata,
        }
        self.saved.append(doc)
        self.current_banner = doc
        return doc

    def get_banner(self):
        return self.current_banner


class _Config:
    def __init__(self):
        self.config = {"banner_agent": {"max_items": 10}}
        self.yfinance_config = {}

    def model_for(self, _name):
        return "test-model"

    def llm_config_for_function(self, _name):
        return {}


def test_provider_failure_is_explicit_and_does_not_persist(monkeypatch):
    old = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    cosmos = _FakeCosmos(activities=[{"symbol": "AAPL", "timestamp": old}])

    class _FailingProvider:
        async def fetch_all(self, symbol, *, force_refresh=False):
            assert force_refresh is True
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(banner_agent, "get_shared_provider", lambda _config: _FailingProvider())

    with pytest.raises(RuntimeError, match="provider unavailable"):
        asyncio.run(run_banner_agent(_Config(), cosmos))

    assert cosmos.saved == []


def test_empty_provider_payload_is_unavailable_and_persists_no_recent_data(monkeypatch):
    cosmos = _FakeCosmos()

    class _EmptyProvider:
        async def fetch_all(self, symbol, *, force_refresh=False):
            return {
                "overview": "{}",
                "technicals": "{}",
                "forecast": "{}",
                "dividends": "{}",
                "_source": json.dumps({
                    "timestamps": {
                        "market": datetime.now(timezone.utc).isoformat(),
                    },
                    "errors": [],
                }),
            }

    monkeypatch.setattr(banner_agent, "get_shared_provider", lambda _config: _EmptyProvider())

    result = asyncio.run(run_banner_agent(_Config(), cosmos))

    assert result["items"] == [_NO_RECENT_DATA_ITEM]
    assert result["source_counts"] == {"activities": 0, "market_snapshots": 0}
    assert result["source_as_of"] is None


@pytest.mark.parametrize(
    ("overview", "technicals", "forecast", "dividends"),
    [
        (
            {"name": "", "ticker": "AAPL", "fundamentals": {}},
            {"summary": {}, "oscillators": {}, "moving_averages": {}},
            {"current_price": None, "price_target": None, "analyst_rating": None},
            {"dividends": {}},
        ),
        (
            {"fundamentals": {"current_price": {"value": None}}},
            {"summary": {"buy": 0, "sell": 0}},
            {"status": "ok", "data": None},
            {"error": "unavailable", "dividends": {}},
        ),
        (
            {"fundamentals": {"current_price": {"value": float("nan")}}},
            {"oscillators": {"indicators": {"RSI": {"value": float("inf")}}}},
            {"price_target": {"upside_pct": float("-inf")}},
            {"dividends": {"dividends_yield": {"value": float("nan")}}},
        ),
    ],
)
def test_provider_shaped_semantically_empty_payload_skips_llm(
    monkeypatch,
    overview,
    technicals,
    forecast,
    dividends,
):
    cosmos = _FakeCosmos()

    class _Provider:
        async def fetch_all(self, symbol, *, force_refresh=False):
            return {
                "overview": json.dumps(overview),
                "technicals": json.dumps(technicals),
                "forecast": json.dumps(forecast),
                "dividends": json.dumps(dividends),
                "_source": json.dumps({
                    "timestamps": {
                        "market": datetime.now(timezone.utc).isoformat(),
                        "history": datetime.now(timezone.utc).isoformat(),
                    },
                    "errors": [],
                }),
            }

    class _Agent:
        def __init__(self, **_kwargs):
            raise AssertionError("LLM must not be constructed for zero eligible facts")

    monkeypatch.setattr(banner_agent, "get_shared_provider", lambda _config: _Provider())
    monkeypatch.setattr(banner_agent, "Agent", _Agent)

    result = asyncio.run(run_banner_agent(_Config(), cosmos))

    assert result["items"] == [_NO_RECENT_DATA_ITEM]
    assert result["source_counts"] == {"activities": 0, "market_snapshots": 0}
    assert result["source_watermarks"] == {}
    assert result["source_as_of"] is None


@pytest.mark.parametrize(
    "technicals",
    [
        {"summary": {"recommendation": {"label": "NEUTRAL"}}},
        {"moving_averages": {"recommendation": {"label": "Neutral"}}},
        {"TechnicalSummary": {"Recommendation": {"Label": "nEuTrAl"}}},
        {
            "wrapper": {
                "Moving-Averages": {
                    "RecommendationLabel": "NEUTRAL",
                }
            }
        },
        {
            "summary": {
                "recommendation": {"label": "BUY"},
                "buy": 0,
                "sell": 0,
                "neutral": 0,
            }
        },
        {
            "summary": {
                "recommendation": {"label": "SELL", "value": -0.5},
                "sell": 3,
            },
            "oscillators": {
                "indicators": {"provider_default": {"value": 0}},
            },
        },
    ],
)
def test_unsupported_technical_recommendations_are_not_eligible(
    monkeypatch,
    technicals,
):
    now = datetime.now(timezone.utc)
    cosmos = _FakeCosmos()
    llm_calls = 0

    class _Provider:
        async def fetch_all(self, symbol, *, force_refresh=False):
            return {
                "overview": json.dumps({
                    "fundamentals": {
                        "current_price": {"value": None},
                    }
                }),
                "technicals": json.dumps(technicals),
                "forecast": "{}",
                "dividends": "{}",
                "_source": json.dumps({
                    "timestamps": {
                        "market": now.isoformat(),
                        "history": now.isoformat(),
                    },
                    "errors": [],
                }),
            }

    class _Agent:
        def __init__(self, **_kwargs):
            nonlocal llm_calls
            llm_calls += 1

    monkeypatch.setattr(banner_agent, "get_shared_provider", lambda _config: _Provider())
    monkeypatch.setattr(banner_agent, "Agent", _Agent)

    result = asyncio.run(run_banner_agent(_Config(), cosmos))

    assert llm_calls == 0
    assert result["items"] == [_NO_RECENT_DATA_ITEM]
    assert result["source_counts"] == {"activities": 0, "market_snapshots": 0}
    assert result["source_watermarks"] == {}
    assert result["source_as_of"] is None


def test_exact_sma_with_unsupported_recommendation_aliases_skips_everything(
    monkeypatch,
):
    source_as_of = datetime.now(timezone.utc) - timedelta(minutes=1)
    cosmos = _FakeCosmos()

    class _Provider:
        async def fetch_all(self, symbol, *, force_refresh=False):
            return {
                "overview": "{}",
                "technicals": json.dumps({
                    "moving_averages": {
                        "recommendation": {"name": "Buy", "score": 0.4},
                        "Buy": 9,
                        "sell": 3,
                        "neutral": 3,
                        "indicators": {
                            "SMA50": _indicator("SMA50", 100.5, signal="Buy"),
                        },
                    },
                }),
                "forecast": "{}",
                "dividends": "{}",
                "_source": json.dumps({
                    "timestamps": {"history": source_as_of.isoformat()},
                    "errors": [],
                }),
            }

    class _Agent:
        def __init__(self, **_kwargs):
            raise AssertionError("LLM must not run for recommendation aliases")

    monkeypatch.setattr(banner_agent, "get_shared_provider", lambda _config: _Provider())
    monkeypatch.setattr(banner_agent, "Agent", _Agent)

    result = asyncio.run(run_banner_agent(_Config(), cosmos))

    assert result["items"] == [_NO_RECENT_DATA_ITEM]
    assert result["source_counts"] == {"activities": 0, "market_snapshots": 0}
    assert result["source_watermarks"] == {}
    assert result["source_as_of"] is None


@pytest.mark.parametrize(
    "summary",
    [
        {
            "recommendation": {"name": "Buy", "score": 0.4},
            "buy": 15,
            "sell": 5,
            "neutral": 5,
        },
        {
            "recommendationLabel": "Buy",
            "recommendationScore": 0.4,
            "buy": 15,
            "sell": 5,
            "neutral": 5,
        },
        {
            "Recommendation": {"label": "Buy", "value": 0.4},
            "Buy": 15,
            "Sell": 5,
            "Neutral": 5,
        },
        {
            "wrapper": {
                "recommendation": {"label": "Buy", "value": 0.4},
                "buy": 15,
                "sell": 5,
                "neutral": 5,
            },
        },
    ],
)
def test_summary_aliases_nesting_and_casing_are_ignored(summary):
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    payload = {
        "overview": "{}",
        "technicals": json.dumps({"summary": summary}),
        "forecast": "{}",
        "dividends": "{}",
        "_source": json.dumps({
            "timestamps": {"history": "2026-09-25T11:59:00Z"},
            "errors": [],
        }),
    }

    eligible, as_of = _market_payload_as_of(payload, now=now)

    assert eligible == {}
    assert as_of is None


@pytest.mark.parametrize(
    "summary",
    [
        _recommendation("Sell", 0.4, buy=15, sell=5, neutral=5),
        _recommendation("Buy", -0.4, buy=15, sell=5, neutral=5),
        _recommendation("Buy", 0.3, buy=15, sell=5, neutral=5),
        _recommendation("Buy", 0.4, buy=14, sell=5, neutral=5),
    ],
)
def test_conflicting_authoritative_summary_fields_fail_closed(summary):
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    payload = {
        "overview": "{}",
        "technicals": json.dumps({
            "summary": summary,
            "oscillators": {
                "indicators": {"RSI": _indicator("RSI", 50.0)},
            },
        }),
        "forecast": "{}",
        "dividends": "{}",
        "_source": json.dumps({
            "timestamps": {"history": "2026-09-25T11:59:00Z"},
            "errors": [],
        }),
    }

    eligible, as_of = _market_payload_as_of(payload, now=now)
    technicals = json.loads(eligible["technicals"])

    assert as_of == datetime(2026, 9, 25, 11, 59, tzinfo=timezone.utc)
    assert "summary" not in technicals
    assert technicals["oscillators"]["indicators"]["RSI"]["value"] == 50.0


def test_valid_authoritative_fields_ignore_unsupported_aliases_without_duplication():
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    payload = {
        "overview": "{}",
        "technicals": json.dumps({
            "summary": {
                **_recommendation("Buy", 0.4, buy=15, sell=5, neutral=5),
                "name": "Sell",
                "score": -0.4,
                "Buy": 999,
            },
            "oscillators": {
                "indicators": {"RSI": _indicator("RSI", 50.0)},
            },
        }),
        "forecast": "{}",
        "dividends": "{}",
        "_source": json.dumps({
            "timestamps": {"history": "2026-09-25T11:59:00Z"},
            "errors": [],
        }),
    }

    eligible, _ = _market_payload_as_of(payload, now=now)
    summary = json.loads(eligible["technicals"])["summary"]

    assert summary == _recommendation("Buy", 0.4, buy=15, sell=5, neutral=5)


def test_computed_neutral_recommendations_require_and_keep_finite_evidence():
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    payload = {
        "overview": "{}",
        "technicals": json.dumps({
            "price": 101.25,
            "summary": _recommendation(
                "Neutral", 0.0, buy=10, sell=10, neutral=5
            ),
            "oscillators": {
                "indicators": {
                    "RSI": _indicator("RSI", 50.0),
                }
            },
            "moving_averages": {
                **_recommendation(
                    "Neutral", 0.0, buy=5, sell=5, neutral=5
                ),
                "indicators": {
                    "SMA50": _indicator("SMA50", 100.5),
                },
            },
        }),
        "forecast": "{}",
        "dividends": "{}",
        "_source": json.dumps({
            "timestamps": {"history": "2026-09-25T11:59:00Z"},
            "errors": [],
        }),
    }

    eligible, as_of = _market_payload_as_of(payload, now=now)
    technicals = json.loads(eligible["technicals"])

    assert as_of == datetime(2026, 9, 25, 11, 59, tzinfo=timezone.utc)
    assert technicals["summary"] == {
        "recommendation": {"label": "Neutral", "value": 0.0},
        "buy": 10,
        "sell": 10,
        "neutral": 5,
    }
    assert technicals["moving_averages"] == {
        "recommendation": {"label": "Neutral", "value": 0.0},
        "buy": 5,
        "sell": 5,
        "neutral": 5,
        "indicators": {"SMA50": {"value": 100.5}},
    }
    assert technicals["oscillators"]["indicators"]["RSI"]["value"] == 50.0


def test_moving_average_allowlist_matches_provider_schema():
    assert set(_RECOGNIZED_MOVING_AVERAGE_INDICATORS) == {
        "EMA10", "SMA10",
        "EMA20", "SMA20",
        "EMA30", "SMA30",
        "EMA50", "SMA50",
        "EMA100", "SMA100",
        "EMA200", "SMA200",
        "Ichimoku.BLine", "VWMA", "HullMA9",
    }


@pytest.mark.parametrize(
    "indicators",
    [
        {"MysteryMA": {"value": 100.0, "period": 50}},
        {"MysteryMA": {"value": 100.0, "length": 50}},
        {"SMA": {"value": 100.0, "period": 50}},
        {"SMA": {"value": 100.0, "period": 0}},
        {"sma50": {"value": 100.0}},
        {"SMA-50": {"value": 100.0}},
        {"SMA050": {"value": 100.0}},
        {"SimpleMovingAverage50": {"value": 100.0}},
        {"SMA50Alias": {"value": 100.0}},
        {"wrapper": {"SMA50": {"value": 100.0}}},
        {"SMA50": 100.0},
        {"SMA50": {"currentValue": 100.0}},
        {"SMA50": {"value": float("nan")}},
        {"EMA20": {"value": float("inf")}},
    ],
)
def test_unknown_deceptive_or_invalid_moving_averages_are_not_evidence(indicators):
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    payload = {
        "overview": "{}",
        "technicals": json.dumps({
            "summary": _recommendation(
                "Buy", 0.4, buy=15, sell=5, neutral=5
            ),
            "moving_averages": {
                **_recommendation(
                    "Buy", 0.4, buy=9, sell=3, neutral=3
                ),
                "indicators": indicators,
            },
        }),
        "forecast": "{}",
        "dividends": "{}",
        "_source": json.dumps({
            "timestamps": {"history": "2026-09-25T11:59:00Z"},
            "errors": [],
        }),
    }

    eligible, as_of = _market_payload_as_of(payload, now=now)

    assert eligible == {}
    assert as_of is None


def test_recognized_sma_and_ema_are_deduplicated_finite_evidence():
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    payload = {
        "overview": "{}",
        "technicals": json.dumps({
            "summary": _recommendation(
                "Neutral", 0.0, buy=10, sell=10, neutral=5
            ),
            "moving_averages": {
                **_recommendation(
                    "Buy", 0.4, buy=9, sell=3, neutral=3
                ),
                "indicators": {
                    "SMA50": _indicator("SMA50", 100.5),
                    "EMA20": _indicator("EMA20", 101.25, signal="Buy"),
                    "nested": {
                        "SMA50": {"value": 999.0},
                        "Unknown": {"value": 999.0, "period": 50},
                    },
                },
            },
        }),
        "forecast": "{}",
        "dividends": "{}",
        "_source": json.dumps({
            "timestamps": {"history": "2026-09-25T11:59:00Z"},
            "errors": [],
        }),
    }

    eligible, as_of = _market_payload_as_of(payload, now=now)
    technicals = json.loads(eligible["technicals"])

    assert as_of == datetime(2026, 9, 25, 11, 59, tzinfo=timezone.utc)
    assert technicals["moving_averages"]["indicators"] == {
        "EMA20": {"value": 101.25},
        "SMA50": {"value": 100.5},
    }


def test_unknown_moving_average_pseudo_indicators_skip_llm_counts_and_watermark(
    monkeypatch,
):
    source_as_of = datetime.now(timezone.utc) - timedelta(minutes=1)
    cosmos = _FakeCosmos()

    class _Provider:
        async def fetch_all(self, symbol, *, force_refresh=False):
            return {
                "overview": "{}",
                "technicals": json.dumps({
                    "summary": _recommendation(
                        "Buy", 0.4, buy=15, sell=5, neutral=5
                    ),
                    "moving_averages": {
                        **_recommendation(
                            "Buy", 0.4, buy=9, sell=3, neutral=3
                        ),
                        "indicators": {
                            "MysteryMA": {
                                "value": 100.0,
                                "period": 50,
                                "recommendation": "BUY",
                            },
                            "nested": {
                                "SMA50": {"value": 999.0},
                                "EMA20": {"value": 999.0},
                            },
                        },
                    },
                }),
                "forecast": "{}",
                "dividends": "{}",
                "_source": json.dumps({
                    "timestamps": {"history": source_as_of.isoformat()},
                    "errors": [],
                }),
            }

    class _Agent:
        def __init__(self, **_kwargs):
            raise AssertionError("LLM must not run for pseudo-indicators")

    monkeypatch.setattr(banner_agent, "get_shared_provider", lambda _config: _Provider())
    monkeypatch.setattr(banner_agent, "Agent", _Agent)

    result = asyncio.run(run_banner_agent(_Config(), cosmos))

    assert result["items"] == [_NO_RECENT_DATA_ITEM]
    assert result["source_counts"] == {"activities": 0, "market_snapshots": 0}
    assert result["source_watermarks"] == {}
    assert result["source_as_of"] is None


def test_mixed_recognized_and_unknown_moving_averages_count_only_valid_fact(
    monkeypatch,
):
    source_as_of = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(
        microsecond=0
    )
    cosmos = _FakeCosmos(symbols=[{"symbol": "AAPL"}, {"symbol": "MSFT"}])
    prompts = []

    class _Provider:
        async def fetch_all(self, symbol, *, force_refresh=False):
            indicator = (
                {"SMA50": _indicator("SMA50", 100.5)}
                if symbol == "MSFT"
                else {
                    "SMA-50": {"value": 999.0, "length": 50},
                    "nested": {"EMA20": {"value": 999.0}},
                }
            )
            return {
                "overview": "{}",
                "technicals": json.dumps({
                    "summary": _recommendation(
                        "Neutral", 0.0, buy=10, sell=10, neutral=5
                    ),
                    "moving_averages": {
                        **_recommendation(
                            "Neutral", 0.0, buy=5, sell=5, neutral=5
                        ),
                        "indicators": indicator,
                    },
                }),
                "forecast": "{}",
                "dividends": "{}",
                "_source": json.dumps({
                    "timestamps": {"history": source_as_of.isoformat()},
                    "errors": [],
                }),
            }

    class _Agent:
        def __init__(self, **_kwargs):
            pass

        async def run(self, prompt):
            prompts.append(prompt)
            return SimpleNamespace(
                text='{"items":[{"text":"MSFT measured neutral","symbol":"MSFT"}]}'
            )

    monkeypatch.setattr(banner_agent, "get_shared_provider", lambda _config: _Provider())
    monkeypatch.setattr(banner_agent, "Agent", _Agent)
    monkeypatch.setattr(
        banner_agent,
        "create_async_chat_client",
        lambda *_args: object(),
    )

    result = asyncio.run(run_banner_agent(_Config(), cosmos))

    expected = source_as_of.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert len(prompts) == 1
    assert '"symbol": "MSFT"' in prompts[0]
    assert '"symbol": "AAPL"' not in prompts[0]
    assert result["source_counts"] == {"activities": 0, "market_snapshots": 1}
    assert result["source_watermarks"] == {"market:MSFT": expected}
    assert result["source_as_of"] == expected


def test_computed_neutral_invokes_llm_once_and_counts_market_snapshot(monkeypatch):
    now = datetime.now(timezone.utc)
    source_as_of = (now - timedelta(minutes=1)).replace(microsecond=0)
    cosmos = _FakeCosmos()
    llm_calls = 0

    class _Provider:
        async def fetch_all(self, symbol, *, force_refresh=False):
            return {
                "overview": "{}",
                "technicals": json.dumps({
                    "summary": _recommendation(
                        "Neutral", 0.0, buy=10, sell=10, neutral=5
                    ),
                    "oscillators": {
                        "indicators": {"RSI": _indicator("RSI", 50.0)},
                    },
                }),
                "forecast": "{}",
                "dividends": "{}",
                "_source": json.dumps({
                    "timestamps": {"history": source_as_of.isoformat()},
                    "errors": [],
                }),
            }

    class _Agent:
        def __init__(self, **_kwargs):
            pass

        async def run(self, prompt):
            nonlocal llm_calls
            llm_calls += 1
            assert '"technical_recommendation": "Neutral"' in prompt
            assert '"rsi": 50.0' in prompt
            return SimpleNamespace(
                text='{"items":[{"text":"AAPL computed neutral","symbol":"AAPL"}]}'
            )

    monkeypatch.setattr(banner_agent, "get_shared_provider", lambda _config: _Provider())
    monkeypatch.setattr(banner_agent, "Agent", _Agent)
    monkeypatch.setattr(
        banner_agent,
        "create_async_chat_client",
        lambda *_args: object(),
    )

    result = asyncio.run(run_banner_agent(_Config(), cosmos))

    expected = source_as_of.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert llm_calls == 1
    assert result["source_counts"] == {"activities": 0, "market_snapshots": 1}
    assert result["source_watermarks"] == {"market:AAPL": expected}
    assert result["source_as_of"] == expected


def test_mixed_valid_market_fact_drops_placeholder_recommendations(monkeypatch):
    now = datetime.now(timezone.utc)
    source_as_of = (now - timedelta(minutes=1)).replace(microsecond=0)
    cosmos = _FakeCosmos()
    prompts = []

    class _Provider:
        async def fetch_all(self, symbol, *, force_refresh=False):
            return {
                "overview": json.dumps({
                    "fundamentals": {"current_price": {"value": 123.45}},
                }),
                "technicals": json.dumps({
                    "summary": {"recommendation": {"label": "NEUTRAL"}},
                    "moving_averages": {
                        "recommendation": {"label": "not available"},
                    },
                }),
                "forecast": "{}",
                "dividends": "{}",
                "_source": json.dumps({
                    "timestamps": {
                        "market": source_as_of.isoformat(),
                        "history": source_as_of.isoformat(),
                    },
                    "errors": [],
                }),
            }

    class _Agent:
        def __init__(self, **_kwargs):
            pass

        async def run(self, prompt):
            prompts.append(prompt)
            return SimpleNamespace(
                text='{"items":[{"text":"AAPL valid quote","symbol":"AAPL"}]}'
            )

    monkeypatch.setattr(banner_agent, "get_shared_provider", lambda _config: _Provider())
    monkeypatch.setattr(banner_agent, "Agent", _Agent)
    monkeypatch.setattr(
        banner_agent,
        "create_async_chat_client",
        lambda *_args: object(),
    )

    result = asyncio.run(run_banner_agent(_Config(), cosmos))

    assert len(prompts) == 1
    assert "NEUTRAL" not in prompts[0]
    assert "not available" not in prompts[0]
    assert "123.45" in prompts[0]
    expected = source_as_of.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert result["source_counts"] == {"activities": 0, "market_snapshots": 1}
    assert result["source_watermarks"] == {"market:AAPL": expected}
    assert result["source_as_of"] == expected


def test_mixed_market_payload_keeps_only_eligible_facts():
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    payload = {
        "overview": json.dumps({
            "fundamentals": {
                "current_price": {"value": 123.45},
                "earnings_release_next_date_fq": {"value": "2026-09-20"},
            }
        }),
        "technicals": json.dumps({
            "summary": {"buy": 0, "sell": 0},
            "oscillators": {"indicators": {"RSI": {"value": float("nan")}}},
        }),
        "forecast": json.dumps({"price_target": {"upside_pct": None}}),
        "dividends": json.dumps({
            "dividends": {
                "ex_dividend_date_recent": {"value": "2026-09-01"},
                "dividends_yield": {"value": float("inf")},
            }
        }),
        "_source": json.dumps({
            "timestamps": {
                "market": "2026-09-25T11:59:00Z",
                "history": "2026-09-25T11:58:00Z",
            },
            "errors": [],
        }),
    }

    eligible, as_of = _market_payload_as_of(payload, now=now)
    snapshot = _build_symbol_snapshot(
        {"symbol": "AAPL"},
        eligible,
        [],
        now=now,
    )

    assert as_of == datetime(2026, 9, 25, 11, 59, tzinfo=timezone.utc)
    assert snapshot["market_data"] == {
        "price": 123.45,
        "earnings_date": None,
        "ex_dividend_date": None,
        "dividend_yield_pct": None,
        "technical_recommendation": None,
        "technical_buy_count": None,
        "technical_sell_count": None,
        "rsi": None,
        "moving_average_recommendation": None,
        "analyst_rating": None,
        "target_upside_pct": None,
    }


def test_old_only_market_facts_and_semantically_empty_activity_skip_llm(monkeypatch):
    now = datetime.now(timezone.utc)
    cosmos = _FakeCosmos(activities=[{
        "symbol": "AAPL",
        "timestamp": (now - timedelta(minutes=1)).isoformat(),
        "activity": "monitor",
        "is_alert": True,
        "summary": "",
        "risk_flags": [],
    }])

    class _Provider:
        async def fetch_all(self, symbol, *, force_refresh=False):
            return {
                "overview": json.dumps({
                    "fundamentals": {
                        "current_price": {"value": float("nan")},
                        "earnings_release_next_date_fq": {
                            "value": (now - timedelta(days=2)).isoformat()
                        },
                    }
                }),
                "technicals": "{}",
                "forecast": "{}",
                "dividends": json.dumps({
                    "dividends": {
                        "ex_dividend_date_recent": {
                            "value": (now - timedelta(days=1)).isoformat()
                        }
                    }
                }),
                "_source": json.dumps({
                    "timestamps": {"market": now.isoformat()},
                    "errors": [],
                }),
            }

    class _Agent:
        def __init__(self, **_kwargs):
            raise AssertionError("LLM must not run for old-only facts")

    monkeypatch.setattr(banner_agent, "get_shared_provider", lambda _config: _Provider())
    monkeypatch.setattr(banner_agent, "Agent", _Agent)

    result = asyncio.run(run_banner_agent(_Config(), cosmos))

    assert result["items"] == [_NO_RECENT_DATA_ITEM]
    assert result["source_counts"] == {"activities": 0, "market_snapshots": 0}
    assert result["source_as_of"] is None


def test_future_market_timestamp_is_rejected_at_strict_boundary():
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    at_boundary = _market_data(
        earnings="2026-09-26",
        ex_dividend="2026-09-27",
        source_as_of=now.isoformat(),
    )
    future = _market_data(
        earnings="2026-09-26",
        ex_dividend="2026-09-27",
        source_as_of=(now + timedelta(microseconds=1)).isoformat(),
    )

    _, boundary_as_of = _market_payload_as_of(at_boundary, now=now)
    eligible_future, future_as_of = _market_payload_as_of(future, now=now)

    assert boundary_as_of == now
    assert eligible_future == {}
    assert future_as_of is None


def test_stale_market_source_does_not_count_as_recent(monkeypatch):
    cosmos = _FakeCosmos()
    stale_as_of = datetime.now(timezone.utc) - timedelta(days=20)

    class _StaleProvider:
        async def fetch_all(self, symbol, *, force_refresh=False):
            return _market_data(
                earnings=(datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
                ex_dividend=(datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
                source_as_of=stale_as_of.isoformat(),
            )

    monkeypatch.setattr(banner_agent, "get_shared_provider", lambda _config: _StaleProvider())

    result = asyncio.run(run_banner_agent(_Config(), cosmos))

    assert result["items"] == [_NO_RECENT_DATA_ITEM]
    assert result["source_counts"]["market_snapshots"] == 0
    assert result["source_watermarks"] == {}


def test_malformed_provider_payload_fails_without_persisting(monkeypatch):
    cosmos = _FakeCosmos()

    class _MalformedProvider:
        async def fetch_all(self, symbol, *, force_refresh=False):
            return {
                "overview": "{",
                "technicals": "{}",
                "forecast": "{}",
                "dividends": "{}",
                "_source": "{}",
            }

    monkeypatch.setattr(
        banner_agent,
        "get_shared_provider",
        lambda _config: _MalformedProvider(),
    )

    with pytest.raises(RuntimeError, match="malformed JSON"):
        asyncio.run(run_banner_agent(_Config(), cosmos))

    assert cosmos.saved == []


def test_provider_timeout_is_bounded_and_does_not_persist(monkeypatch):
    cosmos = _FakeCosmos()
    config = _Config()
    config.config["banner_agent"]["provider_timeout_seconds"] = 1

    class _HungProvider:
        async def fetch_all(self, symbol, *, force_refresh=False):
            await asyncio.Event().wait()

    monkeypatch.setattr(banner_agent, "get_shared_provider", lambda _config: _HungProvider())
    started = datetime.now(timezone.utc)

    with pytest.raises(RuntimeError, match="timed out"):
        asyncio.run(run_banner_agent(config, cosmos))

    assert (datetime.now(timezone.utc) - started).total_seconds() < 2
    assert cosmos.saved == []


def test_manual_and_scheduled_reruns_bypass_cache_and_align_watermark(monkeypatch):
    now = datetime.now(timezone.utc)
    cosmos = _FakeCosmos(
        activities=[
            {
                "symbol": "AAPL",
                "timestamp": (now - timedelta(minutes=2)).isoformat(),
                "activity": "monitor",
                "summary": "Recent alert",
            },
            {
                "symbol": "AAPL",
                "timestamp": (now - timedelta(days=20)).isoformat(),
                "activity": "monitor",
                "summary": "Old alert",
            },
        ]
    )

    class _Provider:
        def __init__(self):
            self.calls = []

        async def fetch_all(self, symbol, *, force_refresh=False):
            self.calls.append((symbol, force_refresh))
            return _market_data(
                earnings=(now + timedelta(days=3)).isoformat(),
                ex_dividend=(now - timedelta(days=20)).isoformat(),
                source_as_of=(now - timedelta(seconds=1)).isoformat(),
            )

    provider = _Provider()

    class _Agent:
        def __init__(self, **_kwargs):
            pass

        async def run(self, prompt):
            assert "Old alert" not in prompt
            assert (now - timedelta(days=20)).date().isoformat() not in prompt
            return SimpleNamespace(text='{"items":[{"emoji":"⚠️","text":"AAPL recent alert","category":"actionable_alert","priority":5,"symbol":"AAPL"}]}')

    monkeypatch.setattr(banner_agent, "get_shared_provider", lambda _config: provider)
    monkeypatch.setattr(banner_agent, "Agent", _Agent)
    monkeypatch.setattr(banner_agent, "create_async_chat_client", lambda *_args: object())

    first = asyncio.run(run_banner_agent(_Config(), cosmos))
    second = asyncio.run(run_banner_agent(_Config(), cosmos))

    assert provider.calls == [("AAPL", True), ("AAPL", True)]
    assert len(cosmos.saved) == 2
    assert first["source_as_of"]
    assert second["source_as_of"]
    assert first["source_watermarks"]["latest_activity_at"] == cosmos.activities[0]["timestamp"]
    assert first["source_counts"] == {"activities": 1, "market_snapshots": 1}
    assert first["content_hash"] == second["content_hash"]
    assert first["generation_id"] != second["generation_id"]
    assert first["generated_at"] != second["generated_at"]


def test_source_as_of_uses_actual_provider_timestamp_not_fetch_clock(monkeypatch):
    now = datetime.now(timezone.utc)
    source_as_of = (now - timedelta(days=2)).replace(microsecond=0)
    cosmos = _FakeCosmos()

    class _Provider:
        async def fetch_all(self, symbol, *, force_refresh=False):
            return _market_data(
                earnings=(now + timedelta(days=3)).isoformat(),
                ex_dividend=(now - timedelta(days=20)).isoformat(),
                source_as_of=source_as_of.isoformat(),
            )

    class _Agent:
        def __init__(self, **_kwargs):
            pass

        async def run(self, _prompt):
            return SimpleNamespace(text='{"items":[{"text":"Recent market fact","symbol":"AAPL"}]}')

    monkeypatch.setattr(banner_agent, "get_shared_provider", lambda _config: _Provider())
    monkeypatch.setattr(banner_agent, "Agent", _Agent)
    monkeypatch.setattr(banner_agent, "create_async_chat_client", lambda *_args: object())

    result = asyncio.run(run_banner_agent(_Config(), cosmos))

    expected = source_as_of.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert result["source_as_of"] == expected
    assert result["source_watermarks"] == {"market:AAPL": expected}
    assert result["source_counts"] == {"activities": 0, "market_snapshots": 1}


def test_generation_fails_when_persistence_returns_previous_document(monkeypatch):
    cosmos = _FakeCosmos(symbols=[])
    previous = {
        "generated_at": "2026-09-01T00:00:00Z",
        "items": [_NO_RECENT_DATA_ITEM],
        "content_hash": "old",
        "generation_id": "old-generation",
    }
    cosmos.save_banner = lambda *_args, **_kwargs: previous

    with pytest.raises(
        RuntimeError,
        match="persistence did not update content|persistence did not advance generation",
    ):
        asyncio.run(run_banner_agent(_Config(), cosmos))


def test_generation_rejects_stale_generated_at_even_when_identity_matches():
    cosmos = _FakeCosmos(symbols=[])

    def stale_save(items, **metadata):
        stale = {
            "id": "dashboard_banner",
            "items": items,
            **metadata,
            "generated_at": "2026-09-01T00:00:00Z",
        }
        cosmos.current_banner = stale
        return stale

    cosmos.save_banner = stale_save

    with pytest.raises(RuntimeError, match="stale source metadata"):
        asyncio.run(run_banner_agent(_Config(), cosmos))


def test_legacy_banner_without_generated_at_is_replaced_by_verified_generation():
    cosmos = _FakeCosmos(symbols=[])
    cosmos.current_banner = {
        "id": "dashboard_banner",
        "items": [{"text": "legacy"}],
    }

    result = asyncio.run(run_banner_agent(_Config(), cosmos))

    assert result["generated_at"]
    assert result["items"] == [_NO_RECENT_DATA_ITEM]
