from copy import deepcopy
from unittest.mock import MagicMock

from src.cosmos_db import CosmosDBService


def _service_with_doc(doc):
    service = CosmosDBService.__new__(CosmosDBService)
    service.container = MagicMock()
    service.get_symbol = MagicMock(return_value=doc)
    service.container.replace_item.side_effect = lambda item, body: body
    service._generate_position_id = MagicMock(return_value="pos-new")
    return service


def _active_symbol_doc(*, is_paper=None):
    position = {
        "position_id": "pos-old",
        "status": "active",
        "type": "put",
        "strike": 100,
        "expiration": "2026-08-21",
    }
    if is_paper is not None:
        position["is_paper"] = is_paper
    return {
        "id": "config_AAPL",
        "symbol": "AAPL",
        "positions": [position],
    }


def test_roll_position_preserves_is_paper_for_paper_positions():
    service = _service_with_doc(_active_symbol_doc(is_paper=True))

    result = service.roll_position("AAPL", "pos-old", "put", 105, "2026-09-18")

    rolled_position = result["positions"][-1]
    assert rolled_position["position_id"] == "pos-new"
    assert rolled_position["rolled_from"] == "pos-old"
    assert rolled_position["is_paper"] is True
    service.container.replace_item.assert_called_once()


def test_roll_position_does_not_introduce_is_paper_for_real_positions():
    original = _active_symbol_doc()
    service = _service_with_doc(deepcopy(original))

    result = service.roll_position("AAPL", "pos-old", "put", 105, "2026-09-18")

    rolled_position = result["positions"][-1]
    assert rolled_position["position_id"] == "pos-new"
    assert rolled_position["rolled_from"] == "pos-old"
    assert rolled_position.get("is_paper") in (None, False)
    assert "is_paper" not in rolled_position
    service.container.replace_item.assert_called_once()
