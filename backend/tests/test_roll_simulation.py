from __future__ import annotations

from copy import deepcopy

import pytest
from starlette.testclient import TestClient

from src.options_chain_cache import set_options_chain_cache
from src.options_chain_filters import canonical_strike, get_contract
from src.roll_table import RollSimulationError, compute_roll_simulation
from web.app import app


def _contract(bid, ask, *, source="yfinance", carried=False):
    return {
        "bid": bid,
        "ask": ask,
        "_meta": {
            "quote_asof": "2099-01-01T12:00:00Z",
            "quote_source": source,
            "carried": carried,
        },
    }


def _chain(option_type="call"):
    side = "calls" if option_type == "call" else "puts"
    return {
        "symbol": "TEST",
        "timestamp": "2099-01-01T12:01:00Z",
        "source": "merged",
        "calls": {},
        "puts": {},
        side: {
            "20261016": {
                "100.0": _contract(1.0, 1.2),
                "105.0": _contract(1.5, 1.7, source="tradingview", carried=True),
                "110.0": _contract(2.0, 2.2),
            },
            "20261120": {
                "100.0": _contract(1.1, 1.3),
                "105.0": _contract(0.4, 0.6),
            },
            "20260918": {"105.0": _contract(1.2, 1.4)},
        },
    }


def _simulate(chain, **overrides):
    params = {
        "current_strike": 100,
        "current_expiration": "2026-10-16",
        "target_strike": 105,
        "target_expiration": "2026-10-16",
        "option_type": "call",
        "contracts": 3,
        "multiplier": 100,
    }
    params.update(overrides)
    return compute_roll_simulation(chain, **params)


@pytest.mark.parametrize("option_type", ["call", "put"])
def test_call_and_put_exact_contract_calculation(option_type):
    result = _simulate(_chain(option_type), option_type=option_type)

    assert result["option_type"] == option_type.upper()
    assert result["current_contract"]["midpoint"] == 1.1
    assert result["target_contract"]["midpoint"] == 1.6
    assert result["per_share_net"] == 0.5
    assert result["per_contract_net"] == 50.0
    assert result["total_net"] == 150.0
    assert result["outcome"] == "credit"
    assert result["contracts"] == 3
    assert result["multiplier"] == 100


@pytest.mark.parametrize(
    ("target_strike", "expected_net", "outcome"),
    [(105, 0.5, "credit"), (105, -0.6, "debit"), (100, 0.0, "even")],
)
def test_positive_negative_and_zero_midpoint_formulas(target_strike, expected_net, outcome):
    chain = _chain()
    if expected_net < 0:
        chain["calls"]["20261016"]["105.0"] = _contract(0.4, 0.6)
    target_expiration = "2026-11-20" if target_strike == 100 else "2026-10-16"
    if expected_net == 0:
        chain["calls"]["20261120"]["100.0"] = _contract(1.0, 1.2)

    result = _simulate(
        chain,
        target_strike=target_strike,
        target_expiration=target_expiration,
    )

    assert result["per_share_net"] == expected_net
    assert result["outcome"] == outcome


@pytest.mark.parametrize("target_expiration", ["2026-09-18", "2026-10-16", "2026-11-20"])
def test_earlier_same_and_later_expirations_are_allowed(target_expiration):
    result = _simulate(
        _chain(),
        target_strike=105,
        target_expiration=target_expiration,
    )
    assert result["target_contract"]["expiration"] == target_expiration


@pytest.mark.parametrize("target_strike", [100, "100", "100.0", "100.000"])
def test_exact_same_contract_uses_canonical_strike_equality(target_strike):
    with pytest.raises(RollSimulationError, match="identical") as exc:
        _simulate(_chain(), target_strike=target_strike, target_expiration="2026-10-16")
    assert exc.value.code == "same_contract"


def test_missing_target_is_not_nearest_matched():
    with pytest.raises(RollSimulationError, match="Exact target") as exc:
        _simulate(_chain(), target_strike=104.99)
    assert exc.value.code == "target_contract_not_found"


def test_high_precision_strikes_remain_distinct_for_exact_lookup():
    lower = "99.99999999999999999999"
    upper = "100.00000000000000000001"
    chain = _chain()
    chain["calls"]["20261016"][lower] = _contract(0.8, 1.0)
    chain["calls"]["20261016"][upper] = _contract(1.4, 1.6)

    lower_result = _simulate(chain, target_strike=lower)
    upper_result = _simulate(chain, target_strike=upper)

    assert lower_result["target_contract"]["strike"] == lower
    assert lower_result["target_contract"]["midpoint"] == 0.9
    assert upper_result["target_contract"]["strike"] == upper
    assert upper_result["target_contract"]["midpoint"] == 1.5
    assert get_contract(chain, lower, "2026-10-16", "call") is chain["calls"]["20261016"][lower]
    assert get_contract(chain, upper, "2026-10-16", "call") is chain["calls"]["20261016"][upper]


def test_high_precision_absent_strikes_do_not_collapse_to_current_contract():
    for target in ("99.99999999999999999999", "100.00000000000000000001"):
        with pytest.raises(RollSimulationError) as exc:
            _simulate(_chain(), target_strike=target)
        assert exc.value.code == "target_contract_not_found"


@pytest.mark.parametrize(
    ("value", "canonical"),
    [(100, "100"), ("100.0", "100"), ("100.000", "100"), ("000100.2500", "100.25")],
)
def test_canonical_strike_normalization(value, canonical):
    assert canonical_strike(value) == canonical


@pytest.mark.parametrize(
    "target_strike",
    [
        True,
        "NaN",
        "Infinity",
        "1e2",
        "100.000000000000000000001",
        "1000000000",
        "-100",
        "+100",
    ],
)
def test_strike_contract_rejects_unsupported_forms(target_strike):
    with pytest.raises(RollSimulationError) as exc:
        _simulate(_chain(), target_strike=target_strike)
    assert exc.value.code == "invalid_input"


@pytest.mark.parametrize("contracts", [None, 0, -1, 1.5, "bad", float("nan")])
def test_missing_or_invalid_quantity_fails_closed(contracts):
    with pytest.raises(RollSimulationError) as exc:
        _simulate(_chain(), contracts=contracts)
    assert exc.value.code == "invalid_input"


@pytest.mark.parametrize(
    ("bid", "ask"),
    [(None, 1.2), (1.0, None), (0, 1.2), (1.0, 0), (float("nan"), 1.2), (1.3, 1.2)],
)
def test_unusable_or_crossed_market_is_unavailable(bid, ask):
    chain = _chain()
    chain["calls"]["20261016"]["105.0"] = _contract(bid, ask)
    with pytest.raises(RollSimulationError) as exc:
        _simulate(chain)
    assert exc.value.code == "quote_unavailable"


def test_provenance_and_chain_metadata_are_returned_without_mutation():
    chain = _chain()
    original = deepcopy(chain)

    result = _simulate(chain)

    assert result["chain_timestamp"] == "2099-01-01T12:01:00Z"
    assert result["chain_source"] == "merged"
    assert result["target_contract"]["quote_asof"] == "2099-01-01T12:00:00Z"
    assert result["target_contract"]["source"] == "tradingview"
    assert result["target_contract"]["carried"] is True
    assert chain == original


class _FakeCosmos:
    def __init__(self, doc):
        self.doc = doc

    def get_symbol(self, symbol):
        return self.doc if symbol == "TEST" else None


class _FakeCache:
    def __init__(self, chain):
        self.chain = chain

    async def get_or_load_async(self, symbol):
        return deepcopy(self.chain)


@pytest.fixture
def endpoint_client():
    document = {
        "symbol": "TEST",
        "exchange": "XNAS",
        "positions": [
            {
                "position_id": "position-a",
                "status": "active",
                "type": "call",
                "strike": 100,
                "expiration": "2026-10-16",
                "contracts": 2,
                "account_id": "account-a",
            },
            {
                "position_id": "position-b",
                "status": "active",
                "type": "call",
                "strike": 110,
                "expiration": "2026-10-16",
                "contracts": 4,
                "account_id": "account-b",
                "is_paper": True,
            },
        ],
    }
    original = deepcopy(document)
    app.router.on_startup = []
    app.state.cosmos = _FakeCosmos(document)
    set_options_chain_cache(_FakeCache(_chain()))
    client = TestClient(app, raise_server_exceptions=False)
    yield client, document, original
    set_options_chain_cache(None)


def test_endpoint_uses_exact_position_identity_and_does_not_mutate(endpoint_client):
    client, document, original = endpoint_client
    response = client.post(
        "/api/symbols/TEST/positions/position-b/roll-simulation",
        json={"target_strike": "105", "target_expiration": "2026-10-16"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["position"]["position_id"] == "position-b"
    assert payload["position"]["account_id"] == "account-b"
    assert payload["position"]["is_paper"] is True
    assert payload["current_contract"]["strike"] == "110"
    assert payload["contracts"] == 4
    assert payload["total_net"] == -200.0
    assert document == original


def test_endpoint_rejects_closed_position(endpoint_client):
    client, document, _ = endpoint_client
    document["positions"][0]["status"] = "closed"
    response = client.post(
        "/api/symbols/TEST/positions/position-a/roll-simulation",
        json={"target_strike": 105, "target_expiration": "2026-10-16"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "inactive_position"


@pytest.mark.parametrize("target_strike", [100, "100", "100.0", "100.000"])
def test_endpoint_rejects_canonical_same_contract_before_pricing(endpoint_client, target_strike):
    client, _, _ = endpoint_client
    response = client.post(
        "/api/symbols/TEST/positions/position-a/roll-simulation",
        json={"target_strike": target_strike, "target_expiration": "2026-10-16"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "same_contract"


def test_endpoint_rejects_missing_quantity(endpoint_client):
    client, document, _ = endpoint_client
    document["positions"][0].pop("contracts")
    response = client.post(
        "/api/symbols/TEST/positions/position-a/roll-simulation",
        json={"target_strike": 105, "target_expiration": "2026-10-16"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_input"


def test_endpoint_returns_404_for_exact_target_miss(endpoint_client):
    client, _, _ = endpoint_client
    response = client.post(
        "/api/symbols/TEST/positions/position-a/roll-simulation",
        json={"target_strike": 104.99, "target_expiration": "2026-10-16"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "target_contract_not_found"


@pytest.mark.parametrize(
    "body",
    [
        '{"target_strike":true,"target_expiration":"2026-10-16"}',
        '{"target_strike":NaN,"target_expiration":"2026-10-16"}',
        '{"target_strike":Infinity,"target_expiration":"2026-10-16"}',
        '{"target_strike":1e2,"target_expiration":"2026-10-16"}',
        '{"target_strike":"1e2","target_expiration":"2026-10-16"}',
        '{"target_strike":100.000000000000000000001,"target_expiration":"2026-10-16"}',
        '{"target_strike":1000000000,"target_expiration":"2026-10-16"}',
    ],
)
def test_endpoint_rejects_invalid_json_strike_forms_without_float_round_trip(endpoint_client, body):
    client, _, _ = endpoint_client
    response = client.post(
        "/api/symbols/TEST/positions/position-a/roll-simulation",
        content=body,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["code"] in {"invalid_input", "invalid_request"}


@pytest.mark.parametrize("target_strike", ["105", 105])
def test_endpoint_accepts_string_and_numeric_plain_decimal_strikes(endpoint_client, target_strike):
    client, _, _ = endpoint_client
    response = client.post(
        "/api/symbols/TEST/positions/position-a/roll-simulation",
        json={"target_strike": target_strike, "target_expiration": "2026-10-16"},
    )
    assert response.status_code == 200
    assert response.json()["target_contract"]["strike"] == "105"


def test_endpoint_preserves_high_precision_numeric_target(endpoint_client):
    client, _, _ = endpoint_client
    precise = "100.00000000000000000001"
    chain = _chain()
    chain["calls"]["20261016"][precise] = _contract(1.4, 1.6)
    set_options_chain_cache(_FakeCache(chain))

    response = client.post(
        "/api/symbols/TEST/positions/position-a/roll-simulation",
        content=(
            '{"target_strike":100.00000000000000000001,'
            '"target_expiration":"2026-10-16"}'
        ),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json()["target_contract"]["strike"] == precise


def test_endpoint_rejects_non_us_multiplier_assumption(endpoint_client):
    client, document, _ = endpoint_client
    document["exchange"] = "XLON"
    response = client.post(
        "/api/symbols/TEST/positions/position-a/roll-simulation",
        json={"target_strike": 105, "target_expiration": "2026-10-16"},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "options_not_eligible"
