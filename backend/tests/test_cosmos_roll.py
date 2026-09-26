from copy import deepcopy
from unittest.mock import MagicMock

import pytest

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
        "contracts": 1,
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


def test_add_position_persists_current_schema_and_contract_count():
    service = _service_with_doc({
        "id": "config_AAPL",
        "symbol": "AAPL",
        "positions": [],
    })

    result = service.add_position("AAPL", "call", 100, "2026-09-18", contracts=3)

    assert result["positions"][0]["contracts"] == 3
    assert result["positions"][0]["position_schema_version"] == 2


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


def test_roll_position_preserves_explicit_remaining_contract_count():
    document = _active_symbol_doc()
    document["positions"][0].update({"contracts": 5, "open_contracts": "2"})
    service = _service_with_doc(document)

    result = service.roll_position("AAPL", "pos-old", "put", 105, "2026-09-18")

    assert result["positions"][-1]["contracts"] == 2
    assert result["positions"][-1]["position_schema_version"] == 2


def test_roll_position_persists_resolved_legacy_contract_count():
    document = _active_symbol_doc()
    document["positions"][0].pop("contracts")
    document["positions"][0].update({
        "position_id": "pos_AAPL_put_100_20260821_20260901_120000",
        "opened_at": "2026-09-01T12:00:00Z",
        "notes": "",
    })
    service = _service_with_doc(document)

    result = service.roll_position("AAPL", document["positions"][0]["position_id"], "put", 105, "2026-09-18")

    assert result["positions"][0]["status"] == "rolled"
    assert result["positions"][-1]["contracts"] == 1
    assert result["positions"][-1]["position_schema_version"] == 2


def test_roll_position_rejects_corrupt_quantity_before_mutating_source():
    document = _active_symbol_doc()
    document["positions"][0].pop("contracts")
    original = deepcopy(document)
    service = _service_with_doc(document)

    with pytest.raises(ValueError, match="missing an open contract count"):
        service.roll_position("AAPL", "pos-old", "put", 105, "2026-09-18")

    assert document == original
    service.container.replace_item.assert_not_called()


def test_set_position_paper_adds_flag_when_enabled():
    service = _service_with_doc(_active_symbol_doc())

    result = service.set_position_paper("AAPL", "pos-old", True)

    assert result["positions"][0]["is_paper"] is True
    service.container.replace_item.assert_called_once()


def test_set_position_paper_removes_flag_when_disabled():
    service = _service_with_doc(_active_symbol_doc(is_paper=True))

    result = service.set_position_paper("AAPL", "pos-old", False)

    assert "is_paper" not in result["positions"][0]
    service.container.replace_item.assert_called_once()
