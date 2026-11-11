from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from starlette.testclient import TestClient
from web.app import app


@pytest.fixture
def api(monkeypatch):
    activity = {
        "id": "activity-1", "symbol": "TEST", "agent_type": "open_call_monitor",
        "position_id": "position-1", "activity": "ROLL_UP_AND_OUT",
        "strike": 100, "expiration": "2026-11-18",
        "new_strike": 105, "new_expiration": "2026-12-09",
        "roll_alternatives": {
            "action_target_id": "DEFENSIVE", "preferred_alternative_id": "DEFENSIVE",
            "alternatives": {
                "DEFENSIVE": {"status": "available", "new_strike": 105, "new_expiration": "2026-12-09"},
                "CREDIT_EXTENDED": {"status": "available", "new_strike": 105, "new_expiration": "2026-12-31"},
            },
        },
        "alpha_view": {"preferred_alternative_id": "CREDIT_EXTENDED"},
    }
    cosmos = SimpleNamespace(
        get_activity_by_id=Mock(return_value=activity),
        get_symbol=Mock(return_value={"symbol": "TEST", "display_name": "Test", "exchange": "XNAS"}),
        roll_position=Mock(return_value={"id": "rolled"}),
        write_activity=Mock(),
    )
    monkeypatch.setattr(app.state, "cosmos", cosmos, raising=False)
    return TestClient(app, raise_server_exceptions=False), activity, cosmos


def test_apply_roll_ignores_alpha_preference_and_uses_primary_not_holding_or_extended(api):
    client, activity, cosmos = api
    before = deepcopy(activity)
    response = client.post("/api/symbols/TEST/positions/roll-from-activity/activity-1")
    assert response.status_code == 201, response.text
    args = cosmos.roll_position.call_args.args
    assert args[:5] == ("TEST", "position-1", "call", 105.0, "2026-12-09")
    assert activity == before


def test_legacy_apply_semantics_remain_unchanged_and_activity_api_passes_structured_contract(api):
    client, activity, cosmos = api
    response = client.get("/api/activities/activity-1")
    assert response.status_code == 200
    assert response.json()["activity"]["roll_alternatives"] == activity["roll_alternatives"]
    activity.pop("roll_alternatives")
    response = client.post("/api/symbols/TEST/positions/roll-from-activity/activity-1")
    assert response.status_code == 201
    assert cosmos.roll_position.call_args.args[3:5] == (100.0, "2026-11-18")


def test_unavailable_defensive_and_advisory_review_clone_are_not_actionable(api):
    client, activity, cosmos = api
    activity["roll_alternatives"]["alternatives"]["DEFENSIVE"]["status"] = "incomplete_data"
    response = client.post("/api/symbols/TEST/positions/roll-from-activity/activity-1")
    assert response.status_code == 400
    cosmos.roll_position.assert_not_called()
    response = client.post("/api/activities/from-recommendation", json={
        "source_activity_id": "activity-1", "source_agent": "alpha_advisor",
        "activity_data": {"activity": "ROLL_UP_AND_OUT", "strike": 105, "expiration": "2026-12-31", "premium": 2.5},
    })
    assert response.status_code == 400
    assert "advisory" in response.json()["error"]
    cosmos.write_activity.assert_not_called()
