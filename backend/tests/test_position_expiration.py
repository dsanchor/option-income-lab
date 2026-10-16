"""Expiration corrections preserve execution identity and reset monitoring only."""

from copy import deepcopy

import pytest
from azure.core import MatchConditions
from azure.cosmos.exceptions import CosmosHttpResponseError
from src.backup.section_schemas import project_positions, validate_record
from src.context import ContextProvider
from src.cosmos_db import CosmosDBService
from starlette.testclient import TestClient


def symbol_doc():
    return {
        "id": "config_AAPL", "symbol": "AAPL", "exchange": "XNAS", "_etag": "v1",
        "watchlist": {"enabled": True},
        "positions": [
            {"position_id": "previous", "status": "rolled", "rolled_to": "target"},
            {
                "position_id": "target", "type": "call", "strike": 200,
                "expiration": "2026-11-20", "status": "active",
                "rolled_from": "previous", "rolled_to": "next",
                "opened_at": "2026-09-01T10:00:00Z", "closed_at": None,
                "contracts": 2, "account_id": "broker", "is_paper": True,
                "notes": "Keep me", "buyback_cost": 1.2,
                "source": {"premium": 3.4, "expiration": "2026-11-20"},
            },
            {"position_id": "next", "rolled_from": "target", "status": "closed"},
        ],
    }


class Container:
    def __init__(self, doc):
        self.doc = deepcopy(doc)
        self.writes = []
        self.snapshots = []
        self.queries = []

    def replace_item(self, *, item, body, **kwargs):
        self.writes.append((item, deepcopy(body), kwargs))
        self.doc = deepcopy(body)
        return deepcopy(body)

    def query_items(self, *, query, parameters, partition_key):
        self.queries.append((query, parameters, partition_key))
        params = {p["name"]: p["value"] for p in parameters}
        rows = [s for s in self.snapshots if s["position_id"] == params["@position_id"]]
        if "@corrected_at" in params:
            rows = [s for s in rows
                    if s["timestamp"] > params["@corrected_at"]
                    and s.get("expiration") == params["@expiration"]]
        limit = int(query.split("TOP ")[1].split()[0])
        return sorted(rows, key=lambda s: s["timestamp"], reverse=True)[:limit]


def service():
    instance = CosmosDBService.__new__(CosmosDBService)
    instance.container = Container(symbol_doc())
    instance.get_symbol = lambda symbol: (
        deepcopy(instance.container.doc) if symbol == "AAPL" else None
    )
    return instance


@pytest.mark.parametrize("status", ["active", "rolled", "closed", "expired"])
def test_correction_preserves_every_unrelated_field_and_roll_links(status):
    cosmos = service()
    cosmos.container.doc["positions"][1]["status"] = status
    before = deepcopy(cosmos.container.doc)

    result = cosmos.update_position_expiration("AAPL", "target", "2024-02-29")

    expected = deepcopy(before)
    expected["positions"][1]["expiration"] = "2024-02-29"
    expected["positions"][1]["expiration_corrected_at"] = result["updated_at"]
    expected["updated_at"] = result["updated_at"]
    assert result == expected == cosmos.get_symbol("AAPL")
    assert cosmos.container.writes[0][2] == {
        "etag": "v1", "match_condition": MatchConditions.IfNotModified,
    }


def test_unchanged_expiration_does_not_reset_monitoring_or_write():
    cosmos = service()
    before = cosmos.get_symbol("AAPL")
    assert cosmos.update_position_expiration("AAPL", "target", "2026-11-20") == before
    assert cosmos.container.writes == []


def test_backup_preserves_corrected_expiration_and_monitoring_boundary():
    cosmos = service()
    cosmos.container.doc["positions"][1].pop("account_id")
    corrected = cosmos.update_position_expiration("AAPL", "target", "2024-02-29")
    records = project_positions(corrected, include_paper=True)
    target = next(record for record in records if record["position_id"] == "target")
    validate_record("option_positions", target)
    assert target["expiration"] == "2024-02-29"
    assert target["expiration_corrected_at"] == corrected["updated_at"]
    assert target["rolled_from"] == "previous"
    assert target["rolled_to"] == "next"


INVALID_DATES = [
    None, True, 20261120, "", "2026-02-29", "2026-04-31", "0000-01-01",
    "2026-1-02", "20261120", "2026-W47-5", "2026-11-20T00:00:00Z",
    " 2026-11-20", "2026-11-20 ",
]


@pytest.mark.parametrize("value", INVALID_DATES)
def test_invalid_expiration_never_writes(value):
    cosmos = service()
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        cosmos.update_position_expiration("AAPL", "target", value)
    assert cosmos.container.writes == []


def test_snapshot_boundary_preserves_history_but_excludes_old_contract_and_trends():
    cosmos = service()
    position = cosmos.container.doc["positions"][1]
    position["expiration"] = "2026-10-16"
    position["expiration_corrected_at"] = "2026-10-01T10:00:00.500000Z"
    cosmos.container.snapshots = [
        {"id": name, "position_id": "target", "expiration": expiration, "timestamp": ts}
        for name, expiration, ts in [
            ("historical", "2026-10-16", "2026-09-30T10:00:00Z"),
            ("same-second-before", "2026-10-16", "2026-10-01T10:00:00Z"),
            ("in-flight-old", "2026-11-20", "2026-10-02T10:00:00Z"),
            ("legacy", None, "2026-10-02T10:00:00Z"),
            ("fresh", "2026-10-16", "2026-10-03T10:00:00Z"),
        ]
    ]
    assert [s["id"] for s in cosmos.get_position_snapshots("AAPL", "target")] == ["fresh"]
    assert len(cosmos.container.snapshots) == 5
    query, _, partition = cosmos.container.queries[0]
    assert "c.expiration = @expiration" in query
    assert partition == "AAPL"
    assert cosmos.container.writes == []


def test_uncorrected_position_keeps_legacy_snapshots():
    cosmos = service()
    cosmos.container.snapshots = [
        {"position_id": "target", "timestamp": "2026-09-01T00:00:00Z"},
    ]
    assert cosmos.get_position_snapshots("AAPL", "target") == cosmos.container.snapshots


def test_old_in_flight_dps_cannot_update_corrected_contract():
    cosmos = service()
    cosmos.get_position_snapshots = lambda *args, **kwargs: [
        {"id": "new", "expiration": "2026-10-16", "dps_score": 50},
    ]
    cosmos.update_snapshot_dps("AAPL", "target", 99, expected_expiration="2026-11-20")
    assert cosmos.container.writes == []


def test_old_dps_cannot_score_a_newer_snapshot_even_after_expiration_is_restored():
    cosmos = service()
    cosmos.get_position_snapshots = lambda *args, **kwargs: [
        {"id": "new", "expiration": "2026-11-20", "timestamp": "2026-10-02T10:00:00Z"},
    ]
    cosmos.update_snapshot_dps(
        "AAPL", "target", 99, expected_expiration="2026-11-20",
        expected_timestamp="2026-10-01T10:00:00Z",
    )
    assert cosmos.container.writes == []


def test_monitor_prompt_context_does_not_reuse_mistaken_contract_decisions():
    cosmos = service()
    position = cosmos.container.doc["positions"][1]
    position["expiration_corrected_at"] = "2026-10-01T10:00:00Z"
    cosmos.get_recent_activities = lambda *args: [
        {"timestamp": "2026-10-02T10:00:00Z", "current_expiration": "2026-11-20",
         "reason": "fresh-contract-context"},
        {"timestamp": "2026-10-02T10:00:00Z", "current_expiration": "2026-10-16",
         "reason": "old-in-flight-context"},
        {"timestamp": "2026-09-30T10:00:00Z", "current_expiration": "2026-11-20",
         "reason": "pre-correction-context"},
    ]
    context = ContextProvider(cosmos).get_context(
        "AAPL", "open_call_monitor", position_id="target", position=position,
    )
    assert "fresh-contract-context" in context
    assert "old-in-flight-context" not in context
    assert "pre-correction-context" not in context


@pytest.fixture
def client(monkeypatch):
    from web.app import app

    cosmos = service()
    monkeypatch.setattr(app.state, "cosmos", cosmos, raising=False)
    return TestClient(app, raise_server_exceptions=False), cosmos


def test_endpoint_corrects_historical_date_and_round_trips(client):
    http, cosmos = client
    response = http.patch(
        "/api/symbols/aapl/positions/target/expiration", json={"expiration": "2024-02-29"},
    )
    assert response.status_code == 200
    assert response.json()["positions"] == cosmos.get_symbol("AAPL")["positions"]
    assert response.json()["positions"][1]["expiration"] == "2024-02-29"


@pytest.mark.parametrize("value", INVALID_DATES)
def test_endpoint_invalid_date_is_400_without_write(client, value):
    http, cosmos = client
    response = http.patch(
        "/api/symbols/AAPL/positions/target/expiration", json={"expiration": value},
    )
    assert response.status_code == 400
    assert cosmos.container.writes == []


@pytest.mark.parametrize("content", ["{", "[]", "{}", "null"])
def test_endpoint_invalid_body_is_400(client, content):
    http, cosmos = client
    response = http.patch(
        "/api/symbols/AAPL/positions/target/expiration",
        content=content, headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert cosmos.container.writes == []


@pytest.mark.parametrize("path", ["AAPL/positions/missing", "MISSING/positions/target"])
def test_missing_position_or_symbol_is_404(client, path):
    http, cosmos = client
    assert http.patch(
        f"/api/symbols/{path}/expiration", json={"expiration": "2024-02-29"},
    ).status_code == 404
    assert cosmos.container.writes == []


def test_concurrent_edit_returns_retryable_conflict(client, monkeypatch):
    http, cosmos = client

    def conflict(**kwargs):
        raise CosmosHttpResponseError(status_code=412, message="ETag mismatch")

    monkeypatch.setattr(cosmos.container, "replace_item", conflict)
    response = http.patch(
        "/api/symbols/AAPL/positions/target/expiration", json={"expiration": "2024-02-29"},
    )
    assert response.status_code == 409
    assert cosmos.get_symbol("AAPL")["positions"][1]["expiration"] == "2026-11-20"


def test_non_us_options_guard_is_preserved(client):
    http, cosmos = client
    cosmos.container.doc["exchange"] = "XLON"
    assert http.patch(
        "/api/symbols/AAPL/positions/target/expiration", json={"expiration": "2024-02-29"},
    ).status_code == 403
    assert cosmos.container.writes == []
