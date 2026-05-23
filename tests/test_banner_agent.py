import sys
import types
from unittest.mock import MagicMock

if "agent_framework" not in sys.modules:
    sys.modules["agent_framework"] = types.SimpleNamespace(Agent=object)

if "azure.cosmos" not in sys.modules:
    azure_module = types.ModuleType("azure")
    cosmos_module = types.ModuleType("azure.cosmos")
    cosmos_module.CosmosClient = object
    cosmos_module.PartitionKey = object
    exceptions_module = types.ModuleType("azure.cosmos.exceptions")

    class _CosmosResourceNotFoundError(Exception):
        pass

    exceptions_module.CosmosResourceNotFoundError = _CosmosResourceNotFoundError
    sys.modules["azure"] = azure_module
    sys.modules["azure.cosmos"] = cosmos_module
    sys.modules["azure.cosmos.exceptions"] = exceptions_module

from src.banner_agent import _extract_banner_items
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
