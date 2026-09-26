from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from types import MappingProxyType

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


class _ReadOnlyMapping(Mapping):
    def __init__(self, values):
        self._values = values

    def __getitem__(self, key):
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)


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
    assert exc.value.code == "target_midpoint_unavailable"


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
    def __init__(self, chain, *, serialize=True, error=None):
        self.chain = chain
        self.serialize = serialize
        self.error = error
        self.calls = 0

    async def get_or_load_async(self, symbol):
        self.calls += 1
        if self.error:
            raise self.error
        chain = deepcopy(self.chain)
        return json.dumps(chain) if self.serialize else chain


class _FakeYFProvider:
    async def fetch_all(self, symbol):
        return {
            "overview": json.dumps(
                {"fundamentals": {"current_price": {"value": 103}}}
            )
        }


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
    app.state.yf_provider = _FakeYFProvider()
    cache = _FakeCache(_chain())
    app.state.roll_test_cache = cache
    set_options_chain_cache(cache)
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
    assert payload["quantity_source"] == "contracts"
    assert payload["total_net"] == -200.0
    assert document == original


def test_roll_table_uses_same_authoritative_position_quantity(endpoint_client):
    client, document, _ = endpoint_client
    document["positions"][1]["open_contracts"] = "2"

    response = client.get("/api/symbols/TEST/positions/position-b/roll-table")

    assert response.status_code == 200
    assert response.json()["contracts"] == 2
    assert response.json()["quantity_source"] == "open_contracts"


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
    assert response.json()["code"] == "position_quantity_missing"
    assert "Set 'contracts'" in response.json()["error"]


def test_endpoint_uses_proven_legacy_one_contract_shape_with_warning(endpoint_client):
    client, document, _ = endpoint_client
    position = document["positions"][0]
    position.pop("contracts")
    position.update({
        "position_id": "pos_TEST_call_100_20261016_20260901_120000",
        "opened_at": "2026-09-01T12:00:00Z",
        "notes": "",
    })
    response = client.post(
        f"/api/symbols/TEST/positions/{position['position_id']}/roll-simulation",
        json={"target_strike": 105, "target_expiration": "2026-10-16"},
    )
    assert response.status_code == 200
    assert response.json()["contracts"] == 1
    assert response.json()["quantity_source"] == "legacy_implicit_one"
    assert response.json()["quantity_warnings"]


def test_endpoint_rejects_current_writer_shape_with_omitted_quantity(endpoint_client):
    client, document, _ = endpoint_client
    position = document["positions"][0]
    position.pop("contracts")
    position.update({
        "position_id": "pos_TEST_call_100_20261016_20260926_120000",
        "opened_at": "2026-09-26T12:00:00Z",
        "notes": "",
    })
    response = client.post(
        f"/api/symbols/TEST/positions/{position['position_id']}/roll-simulation",
        json={"target_strike": 105, "target_expiration": "2026-10-16"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "position_quantity_missing"


@pytest.mark.parametrize("status", ["closed", "expired", "rolled"])
def test_roll_table_rejects_inactive_position_before_chain_retrieval(endpoint_client, status):
    client, document, _ = endpoint_client
    document["positions"][0]["status"] = status

    response = client.get("/api/symbols/TEST/positions/position-a/roll-table")

    assert response.status_code == 409
    assert response.json()["code"] == "inactive_position"
    assert app.state.roll_test_cache.calls == 0


def test_roll_table_rejects_current_missing_quantity_before_chain_retrieval(endpoint_client):
    client, document, _ = endpoint_client
    document["positions"][0].pop("contracts")

    response = client.get("/api/symbols/TEST/positions/position-a/roll-table")

    assert response.status_code == 400
    assert response.json()["code"] == "position_quantity_missing"
    assert app.state.roll_test_cache.calls == 0


def test_endpoint_accepts_numeric_string_negative_short_quantity(endpoint_client):
    client, document, _ = endpoint_client
    document["positions"][0].pop("contracts")
    document["positions"][0]["quantity"] = "-2"
    response = client.post(
        "/api/symbols/TEST/positions/position-a/roll-simulation",
        json={"target_strike": 105, "target_expiration": "2026-10-16"},
    )
    assert response.status_code == 200
    assert response.json()["contracts"] == 2
    assert response.json()["quantity_source"] == "quantity"


def test_endpoint_uses_remaining_quantity_after_partial_close(endpoint_client):
    client, document, _ = endpoint_client
    document["positions"][0]["open_contracts"] = "1"
    response = client.post(
        "/api/symbols/TEST/positions/position-a/roll-simulation",
        json={"target_strike": 105, "target_expiration": "2026-10-16"},
    )
    assert response.status_code == 200
    assert response.json()["contracts"] == 1
    assert response.json()["quantity_source"] == "open_contracts"


def test_endpoint_rejects_explicit_zero_open_quantity(endpoint_client):
    client, document, _ = endpoint_client
    document["positions"][0]["open_contracts"] = 0
    response = client.post(
        "/api/symbols/TEST/positions/position-a/roll-simulation",
        json={"target_strike": 105, "target_expiration": "2026-10-16"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "position_quantity_zero"


def test_endpoint_returns_404_for_exact_target_miss(endpoint_client):
    client, _, _ = endpoint_client
    response = client.post(
        "/api/symbols/TEST/positions/position-a/roll-simulation",
        json={"target_strike": 104.99, "target_expiration": "2026-10-16"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "target_contract_not_found"


def test_actual_cache_json_string_is_decoded_before_agent_view(endpoint_client):
    client, _, _ = endpoint_client
    response = client.post(
        "/api/symbols/TEST/positions/position-a/roll-simulation",
        json={"target_strike": "105", "target_expiration": "2026-10-16"},
    )

    assert response.status_code == 200
    assert response.json()["target_contract"]["midpoint"] == 1.6


def test_endpoint_never_prices_explicit_error_wrapper(endpoint_client):
    client, _, _ = endpoint_client
    set_options_chain_cache(
        _FakeCache(
            {
                "status": "error",
                "options_chain": _chain(),
            },
            serialize=False,
        )
    )

    response = client.post(
        "/api/symbols/TEST/positions/position-a/roll-simulation",
        json={"target_strike": "105", "target_expiration": "2026-10-16"},
    )

    assert response.status_code == 503
    assert response.json()["code"] == "chain_unavailable"


def test_endpoint_returns_stale_wrapper_warning(endpoint_client):
    client, _, _ = endpoint_client
    set_options_chain_cache(
        _FakeCache(
            {
                "status": "stale",
                "error": "latest refresh failed",
                "options_chain": _chain(),
            },
            serialize=False,
        )
    )

    response = client.post(
        "/api/symbols/TEST/positions/position-a/roll-simulation",
        json={"target_strike": "105", "target_expiration": "2026-10-16"},
    )

    assert response.status_code == 200
    assert "Options chain wrapper is stale." in response.json()["chain_warnings"]


def test_provider_options_chain_wrapper_is_supported():
    wrapped = {"options_chain": json.dumps(_chain())}
    result = _simulate(wrapped)

    assert result["current_contract"]["midpoint"] == 1.1
    assert result["target_contract"]["midpoint"] == 1.6


def test_serialized_bytes_wrapper_is_supported():
    wrapped = json.dumps(
        {"status": "success", "options_chain": json.dumps(_chain())}
    ).encode()

    result = _simulate(wrapped)

    assert result["current_contract"]["midpoint"] == 1.1
    assert result["target_contract"]["midpoint"] == 1.6


def test_generic_mapping_normalizes_wrapper_payload_expiry_contract_and_metadata():
    current_meta = {"quote_source": "proxy", "field_status": {"bid": "live", "ask": "live"}}
    current = {"bid": "1.00", "ask": "1.20", "_meta": MappingProxyType(current_meta)}
    target = {"bid": "1.50", "ask": "1.70"}
    backing = {
        "symbol": "TEST",
        "calls": _ReadOnlyMapping(
            {
                "20261016": MappingProxyType(
                    {
                        "100.0": _ReadOnlyMapping(current),
                        "105.0": MappingProxyType(target),
                    }
                )
            }
        ),
    }
    wrapped = _ReadOnlyMapping(
        {
            "status": "success",
            "options_chain": MappingProxyType(backing),
        }
    )

    result = _simulate(wrapped)

    assert result["current_contract"]["midpoint"] == 1.1
    assert result["target_contract"]["midpoint"] == 1.6
    assert current["bid"] == "1.00"
    assert target["ask"] == "1.70"
    assert current_meta["field_status"] == {"bid": "live", "ask": "live"}


@pytest.mark.parametrize("option_type", ["call", "put"])
def test_valid_matching_one_side_chain_does_not_require_other_side(option_type):
    chain = _chain(option_type)
    chain.pop("puts" if option_type == "call" else "calls")

    result = _simulate(chain, option_type=option_type)

    assert result["option_type"] == option_type.upper()


@pytest.mark.parametrize(
    ("payload", "option_type"),
    [
        ({"calls": [], "puts": []}, "call"),
        ({"calls": {}, "puts": {}}, "call"),
        ({"calls": {"20261016": []}, "puts": {}}, "call"),
        ({"calls": {"not-a-date": {"100.0": _contract(1.0, 1.2)}}, "puts": {}}, "call"),
        ({"calls": {"20261016": {"100.0": "not-a-contract"}}, "puts": {}}, "call"),
        ({"calls": _chain("call")["calls"], "puts": []}, "call"),
        ({"calls": _chain("call")["calls"]}, "put"),
        ({"puts": _chain("put")["puts"]}, "call"),
    ],
)
def test_structurally_unusable_required_side_is_chain_unavailable(payload, option_type):
    with pytest.raises(RollSimulationError) as exc:
        _simulate(payload, option_type=option_type)

    assert exc.value.code == "chain_unavailable"


@pytest.mark.parametrize("status", ["error", "failed", "unavailable", "warming"])
def test_explicit_failure_or_unknown_wrapper_status_fails_closed(status):
    wrapped = {"status": status, "options_chain": _chain()}

    with pytest.raises(RollSimulationError) as exc:
        _simulate(wrapped)

    assert exc.value.code == "chain_unavailable"


def test_explicit_wrapper_error_fails_even_with_success_status_and_valid_chain():
    wrapped = {
        "status": "success",
        "error": "provider timeout; serving embedded payload",
        "options_chain": _chain(),
    }

    with pytest.raises(RollSimulationError) as exc:
        _simulate(wrapped)

    assert exc.value.code == "chain_unavailable"


@pytest.mark.parametrize("status", ["stale", "carried"])
def test_retained_wrapper_may_report_refresh_error_with_visible_warning(status):
    result = _simulate(
        {
            "status": status,
            "error": "latest refresh failed",
            "options_chain": _chain(),
        }
    )

    assert result["target_contract"]["midpoint"] == 1.6
    assert (
        "Options chain wrapper reports a refresh error; using retained data."
        in result["chain_warnings"]
    )


@pytest.mark.parametrize(
    ("status", "warning"),
    [
        ("ok", None),
        ("success", None),
        ("stale", "Options chain wrapper is stale."),
        ("carried", "Options chain wrapper contains carried last-known-good data."),
    ],
)
def test_accepted_wrapper_statuses_preserve_visible_warnings(status, warning):
    result = _simulate({"status": status, "options_chain": _chain()})

    if warning is None:
        assert result["chain_warnings"] == []
    else:
        assert warning in result["chain_warnings"]


@pytest.mark.parametrize(
    ("bid", "ask"),
    [("1.50", "1.70"), (1.5, "1.70"), ("1.50", 1.7)],
)
def test_numeric_string_quotes_are_normalized_at_roll_boundary(bid, ask):
    chain = _chain()
    chain["calls"]["20261016"]["105.0"] = _contract(bid, ask)

    result = _simulate(chain)

    assert result["target_contract"]["midpoint"] == 1.6


@pytest.mark.parametrize("fallback_field", ["lastPrice", "mark", "mid"])
def test_one_sided_quote_does_not_fall_back_to_display_price(fallback_field):
    chain = _chain()
    target = _contract(None, 1.7)
    target[fallback_field] = 1.6
    chain["calls"]["20261016"]["105.0"] = target

    with pytest.raises(RollSimulationError) as exc:
        _simulate(chain)

    assert exc.value.code == "target_midpoint_unavailable"
    assert "bid" in str(exc.value)


def test_hyphenated_expiry_keys_retain_exact_decimal_lookup():
    chain = _chain()
    chain["calls"]["2026-10-16"] = chain["calls"].pop("20261016")

    result = _simulate(chain)

    assert result["current_contract"]["strike"] == "100"
    assert result["target_contract"]["strike"] == "105"


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


def test_current_contract_miss_is_not_reported_as_target_or_chain_failure(endpoint_client):
    client, _, _ = endpoint_client
    chain = _chain()
    del chain["calls"]["20261016"]["100.0"]
    set_options_chain_cache(_FakeCache(chain))

    response = client.post(
        "/api/symbols/TEST/positions/position-a/roll-simulation",
        json={"target_strike": 105, "target_expiration": "2026-10-16"},
    )

    assert response.status_code == 404
    assert response.json() == {
        "error": "Current contract is not in the option chain",
        "code": "current_contract_not_found",
    }


@pytest.mark.parametrize(
    ("leg", "bid", "ask", "reason"),
    [
        ("current", None, 1.2, "bid is unavailable"),
        ("target", 1.5, None, "ask is unavailable"),
        ("target", 1.8, 1.7, "market is crossed"),
        ("target", 0, 1.7, "bid is no market"),
    ],
)
def test_endpoint_distinguishes_leg_and_midpoint_reason(
    endpoint_client, leg, bid, ask, reason
):
    client, _, _ = endpoint_client
    chain = _chain()
    strike = "100.0" if leg == "current" else "105.0"
    chain["calls"]["20261016"][strike] = _contract(bid, ask)
    set_options_chain_cache(_FakeCache(chain))

    response = client.post(
        "/api/symbols/TEST/positions/position-a/roll-simulation",
        json={"target_strike": 105, "target_expiration": "2026-10-16"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == f"{leg}_midpoint_unavailable"
    assert reason in response.json()["error"]


def test_stale_and_carried_quotes_are_warnings_not_pricing_gates(endpoint_client):
    client, _, _ = endpoint_client
    chain = _chain()
    contract = chain["calls"]["20261016"]["105.0"]
    contract["_meta"].update(
        {
            "quote_asof": "2000-01-01T00:00:00Z",
            "carried": True,
            "field_status": {
                "bid": "last_known_good",
                "ask": "last_known_good",
            },
        }
    )
    set_options_chain_cache(_FakeCache(chain))

    response = client.post(
        "/api/symbols/TEST/positions/position-a/roll-simulation",
        json={"target_strike": 105, "target_expiration": "2026-10-16"},
    )

    assert response.status_code == 200
    target = response.json()["target_contract"]
    assert target["stale"] is True
    assert target["carried"] is True
    assert target["field_status"]["bid"] == "last_known_good"


def test_chain_retrieval_failure_has_chain_unavailable_contract(endpoint_client):
    client, _, _ = endpoint_client
    set_options_chain_cache(_FakeCache({}, error=RuntimeError("provider timeout")))

    response = client.post(
        "/api/symbols/TEST/positions/position-a/roll-simulation",
        json={"target_strike": 105, "target_expiration": "2026-10-16"},
    )

    assert response.status_code == 503
    assert response.json()["code"] == "chain_unavailable"
    assert response.json()["error"] == "Options chain retrieval failed: provider timeout"


@pytest.mark.parametrize(
    "payload",
    [None, "not-json", b"\xff", {}, {"options_chain": "bad"}],
)
def test_malformed_chain_payload_is_retrieval_unavailable(payload):
    with pytest.raises(RollSimulationError) as exc:
        _simulate(payload)
    assert exc.value.code == "chain_unavailable"


def test_endpoint_rejects_non_us_multiplier_assumption(endpoint_client):
    client, document, _ = endpoint_client
    document["exchange"] = "XLON"
    response = client.post(
        "/api/symbols/TEST/positions/position-a/roll-simulation",
        json={"target_strike": 105, "target_expiration": "2026-10-16"},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "options_not_eligible"
