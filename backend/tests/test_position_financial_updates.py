"""Hermetic tests for position premium and buyback-cost updates."""

from copy import deepcopy

import pytest
from starlette.testclient import TestClient

from src.cosmos_db import CosmosDBService


POSITION_ID = "pos-123"


def _position(*, rolled: bool = True, buyback_cost=...):
    position = {
        "position_id": POSITION_ID,
        "source": {"source_type": "manual", "premium": 1.25},
    }
    if rolled:
        position["rolled_to"] = "pos-456"
    if buyback_cost is not ...:
        position["buyback_cost"] = buyback_cost
    return position


def _symbol_doc(position: dict | None = None) -> dict:
    return {
        "id": "config_AAPL",
        "symbol": "AAPL",
        "positions": [position or _position()],
    }


class RecordingContainer:
    def __init__(self):
        self.replacements = []

    def replace_item(self, *, item, body):
        self.replacements.append((item, deepcopy(body)))
        return body


def _service_with(doc: dict) -> tuple[CosmosDBService, RecordingContainer]:
    service = CosmosDBService.__new__(CosmosDBService)
    container = RecordingContainer()
    service.container = container
    service.get_symbol = lambda symbol: deepcopy(doc) if symbol == "AAPL" else None
    return service, container


@pytest.mark.parametrize("premium", [0, 2.75])
def test_service_persists_premium_in_position_source(premium):
    service, container = _service_with(_symbol_doc())

    result = service.update_position_premium("AAPL", POSITION_ID, premium)

    assert result["positions"][0]["source"] == {
        "source_type": "manual",
        "premium": premium,
    }
    assert container.replacements[0][1]["positions"][0]["source"]["premium"] == premium


@pytest.mark.parametrize("buyback_cost", [0, 0.85])
def test_service_persists_buyback_cost_when_field_was_absent(buyback_cost):
    service, container = _service_with(_symbol_doc(_position(buyback_cost=...)))

    result = service.update_position_buyback_cost("AAPL", POSITION_ID, buyback_cost)

    assert result["positions"][0]["buyback_cost"] == buyback_cost
    assert container.replacements[0][1]["positions"][0]["buyback_cost"] == buyback_cost


def test_service_overwrites_existing_buyback_cost():
    service, container = _service_with(_symbol_doc(_position(buyback_cost=0.45)))

    result = service.update_position_buyback_cost("AAPL", POSITION_ID, 0.9)

    assert result["positions"][0]["buyback_cost"] == 0.9
    assert container.replacements[0][1]["positions"][0]["buyback_cost"] == 0.9


class EndpointCosmos:
    def __init__(self, failure: Exception | None = None):
        self.failure = failure
        self.writes = []

    def _update(self, field, symbol, position_id, value):
        if self.failure:
            raise self.failure
        self.writes.append((field, symbol, position_id, value))
        position = _position(buyback_cost=None)
        if field == "premium":
            position["source"]["premium"] = value
        else:
            position["buyback_cost"] = value
        return _symbol_doc(position)

    def update_position_premium(self, symbol, position_id, premium):
        return self._update("premium", symbol, position_id, premium)

    def update_position_buyback_cost(self, symbol, position_id, buyback_cost):
        return self._update("buyback_cost", symbol, position_id, buyback_cost)


@pytest.fixture
def client_factory():
    from web.app import app

    def make(cosmos):
        app.state.cosmos = cosmos
        return TestClient(app, raise_server_exceptions=False)

    return make


@pytest.mark.parametrize(
    "field,path,value",
    [
        ("premium", "premium", 0),
        ("premium", "premium", 3.5),
        ("buyback_cost", "buyback_cost", 0),
        ("buyback_cost", "buyback_cost", 1.1),
    ],
)
def test_valid_financial_update_returns_200_and_writes(
    client_factory, field, path, value
):
    cosmos = EndpointCosmos()
    client = client_factory(cosmos)

    response = client.patch(
        f"/api/symbols/aapl/positions/{POSITION_ID}/{path}",
        json={field: value},
    )

    assert response.status_code == 200
    assert cosmos.writes == [(field, "AAPL", POSITION_ID, float(value))]


INVALID_VALUES = [None, True, False, "not-a-number", -0.01, "NaN", "Infinity"]


@pytest.mark.parametrize("field,path", [("premium", "premium"), ("buyback_cost", "buyback_cost")])
@pytest.mark.parametrize("value", INVALID_VALUES)
def test_invalid_financial_value_returns_400_without_write(
    client_factory, field, path, value
):
    cosmos = EndpointCosmos()
    client = client_factory(cosmos)

    response = client.patch(
        f"/api/symbols/AAPL/positions/{POSITION_ID}/{path}",
        json={field: value},
    )

    assert (response.status_code, cosmos.writes) == (400, [])


@pytest.mark.parametrize("field,path", [("premium", "premium"), ("buyback_cost", "buyback_cost")])
def test_missing_financial_value_returns_400_without_write(client_factory, field, path):
    cosmos = EndpointCosmos()
    client = client_factory(cosmos)

    response = client.patch(
        f"/api/symbols/AAPL/positions/{POSITION_ID}/{path}", json={}
    )

    assert response.status_code == 400
    assert cosmos.writes == []


@pytest.mark.parametrize("field,path", [("premium", "premium"), ("buyback_cost", "buyback_cost")])
def test_malformed_json_returns_400_without_write(client_factory, field, path):
    cosmos = EndpointCosmos()
    client = client_factory(cosmos)

    response = client.patch(
        f"/api/symbols/AAPL/positions/{POSITION_ID}/{path}",
        content=b'{"broken":',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert cosmos.writes == []


@pytest.mark.parametrize("field,path", [("premium", "premium"), ("buyback_cost", "buyback_cost")])
def test_non_object_json_returns_400_without_write(client_factory, field, path):
    cosmos = EndpointCosmos()
    client = client_factory(cosmos)

    response = client.patch(
        f"/api/symbols/AAPL/positions/{POSITION_ID}/{path}", json=[1.0]
    )

    assert response.status_code == 400
    assert cosmos.writes == []


@pytest.mark.parametrize("field,path", [("premium", "premium"), ("buyback_cost", "buyback_cost")])
@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_json_number_returns_400_without_write(
    client_factory, field, path, literal
):
    cosmos = EndpointCosmos()
    client = client_factory(cosmos)

    response = client.patch(
        f"/api/symbols/AAPL/positions/{POSITION_ID}/{path}",
        content=f'{{"{field}": {literal}}}'.encode(),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert cosmos.writes == []


@pytest.mark.parametrize("field,path", [("premium", "premium"), ("buyback_cost", "buyback_cost")])
@pytest.mark.parametrize(
    "failure,expected_status",
    [
        (ValueError("Symbol not found"), 404),
        (ValueError("Position not found"), 404),
        (RuntimeError("Cosmos unavailable"), 503),
    ],
)
def test_financial_update_preserves_service_error_status(
    client_factory, field, path, failure, expected_status
):
    cosmos = EndpointCosmos(failure)
    client = client_factory(cosmos)

    response = client.patch(
        f"/api/symbols/AAPL/positions/{POSITION_ID}/{path}",
        json={field: 1.0},
    )

    assert response.status_code == expected_status
    assert cosmos.writes == []