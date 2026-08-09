from copy import deepcopy
from unittest.mock import MagicMock

from src.cosmos_db import CosmosDBService


def _service_with_doc(doc):
    service = CosmosDBService.__new__(CosmosDBService)
    service.container = MagicMock()
    service.get_symbol = MagicMock(return_value=doc)
    service.container.replace_item.side_effect = lambda item, body: body
    return service


def _active_symbol_doc():
    return {
        "id": "AAPL",
        "symbol": "AAPL",
        "positions": [
            {
                "position_id": "pos-1",
                "status": "active",
                "type": "put",
                "strike": 100,
                "expiration": "2026-08-21",
            }
        ],
    }


def test_close_position_sets_buyback_cost_when_provided():
    service = _service_with_doc(_active_symbol_doc())

    result = service.close_position("AAPL", "pos-1", buyback_cost=1.23)

    closed_position = result["positions"][0]
    assert closed_position["status"] == "closed"
    assert closed_position["close_reason"] == "manual"
    assert closed_position["buyback_cost"] == 1.23
    service.container.replace_item.assert_called_once()


def test_close_position_leaves_buyback_cost_unset_when_omitted():
    doc = _active_symbol_doc()
    service = _service_with_doc(deepcopy(doc))

    result = service.close_position("AAPL", "pos-1")

    closed_position = result["positions"][0]
    assert closed_position["status"] == "closed"
    assert closed_position["close_reason"] == "manual"
    assert "buyback_cost" not in closed_position
    service.container.replace_item.assert_called_once()
