import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from web.app import _build_dashboard_tables, _compute_dashboard_data


class _Snapshots:
    def __init__(self, snapshots):
        self.snapshots = snapshots

    def get_position_snapshots(self, symbol, position_id, limit=200):
        assert symbol == "MSFT"
        return list(self.snapshots.get(position_id, []))


class _DashboardCosmos(_Snapshots):
    portfolio_container = None

    def __init__(self, positions, activities, snapshots=None):
        super().__init__(snapshots or {})
        self.positions = positions
        self.activities = activities

    def list_symbols(self):
        return [_symbol(*self.positions)]

    def get_all_alerts(self, limit=500):
        return [activity for activity in self.activities if activity.get("is_alert")]

    def get_all_activities(self, limit=200):
        return list(self.activities)

    def get_banner(self):
        return None


def _symbol(*positions):
    return {
        "symbol": "MSFT",
        "display_name": "Microsoft",
        "watchlist": {},
        "positions": list(positions),
    }


def _position(
    position_id,
    strike=500,
    expiration="2026-10-16",
    option_type="call",
    **identity,
):
    return {
        "position_id": position_id,
        "type": option_type,
        "strike": strike,
        "expiration": expiration,
        "status": "active",
        "opened_at": f"2026-09-{1 if position_id == 'pos-a' else 2:02d}T10:00:00Z",
        **identity,
    }


def _activity(
    position_id,
    timestamp,
    *,
    risk,
    price,
    option_type="call",
):
    return {
        "id": f"activity-{position_id}",
        "symbol": "MSFT",
        "agent_type": f"open_{option_type}_monitor",
        "position_id": position_id,
        "activity": "WAIT",
        "timestamp": timestamp,
        "current_strike": 500,
        "current_expiration": "2026-10-16",
        "underlying_price": price,
        "assignment_risk": risk,
        "dte_remaining": 22,
    }


def _monitor_rows(
    positions,
    activities,
    snapshots=None,
    agent_key="open_call_monitor",
):
    tables, _ = _build_dashboard_tables(
        _Snapshots(snapshots or {}),
        [_symbol(*positions)],
        [],
        activities,
    )
    return next(t["rows"] for t in tables if t["key"] == agent_key)


def test_two_same_contract_positions_keep_separate_latest_monitor_data():
    positions = [_position("pos-a"), _position("pos-b")]
    activities = [
        _activity("pos-a", "2026-09-24T10:00:00Z", risk="LOW", price=490),
        _activity("pos-b", "2026-09-24T11:00:00Z", risk="HIGH", price=510),
    ]
    rows = _monitor_rows(
        positions,
        activities,
        snapshots={
            "pos-a": [{"pnl_pct": 12.5, "timestamp": "2026-09-24T10:00:00Z"}],
            "pos-b": [{"pnl_pct": -7.0, "timestamp": "2026-09-24T11:00:00Z"}],
        },
    )

    assert len(rows) == 2
    by_id = {row["position_id"]: row for row in rows}
    assert set(by_id) == {"pos-a", "pos-b"}
    assert by_id["pos-a"]["key"] != by_id["pos-b"]["key"]
    assert by_id["pos-a"]["assignment_risk"] == "LOW"
    assert by_id["pos-a"]["underlying_price"] == 490
    assert by_id["pos-a"]["pnl_pct"] == 12.5
    assert by_id["pos-a"]["recent_activities"][0]["id"] == "activity-pos-a"
    assert by_id["pos-b"]["assignment_risk"] == "HIGH"
    assert by_id["pos-b"]["underlying_price"] == 510
    assert by_id["pos-b"]["pnl_pct"] == -7.0
    assert by_id["pos-b"]["recent_activities"][0]["id"] == "activity-pos-b"


def test_missing_position_data_stays_explicit_and_is_not_borrowed():
    rows = _monitor_rows(
        [_position("pos-a"), _position("pos-b")],
        [_activity("pos-a", "2026-09-24T10:00:00Z", risk="LOW", price=490)],
    )

    by_id = {row["position_id"]: row for row in rows}
    assert by_id["pos-a"]["assignment_risk"] == "LOW"
    assert by_id["pos-b"]["assignment_risk"] is None
    assert by_id["pos-b"]["underlying_price"] is None
    assert by_id["pos-b"]["recent_activities"] == []


def test_legacy_symbol_only_activity_is_not_assigned_when_ambiguous():
    legacy = _activity("", "2026-09-24T12:00:00Z", risk="HIGH", price=520)
    legacy.pop("position_id")
    legacy.pop("current_strike")
    legacy.pop("current_expiration")

    rows = _monitor_rows(
        [_position("pos-a"), _position("pos-b")],
        [legacy],
    )

    assert len(rows) == 2
    assert all(row["recent_activities"] == [] for row in rows)
    assert all(row["assignment_risk"] is None for row in rows)


def test_legacy_contract_activity_falls_back_only_when_unambiguous():
    legacy = _activity("", "2026-09-24T12:00:00Z", risk="MEDIUM", price=505)
    legacy.pop("position_id")

    rows = _monitor_rows(
        [
            _position("pos-a", strike=500, expiration="2026-10-16"),
            _position("pos-b", strike=520, expiration="2026-11-20"),
        ],
        [legacy],
    )

    by_id = {row["position_id"]: row for row in rows}
    assert by_id["pos-a"]["assignment_risk"] == "MEDIUM"
    assert by_id["pos-b"]["assignment_risk"] is None
    assert by_id["pos-b"]["recent_activities"] == []


def test_legacy_symbol_only_activity_falls_back_when_uniquely_resolvable():
    legacy = _activity("", "2026-09-24T12:00:00Z", risk="MEDIUM", price=505)
    for field in ("position_id", "current_strike", "current_expiration"):
        legacy.pop(field)

    rows = _monitor_rows([_position("pos-a")], [legacy])

    assert rows[0]["assignment_risk"] == "MEDIUM"
    assert rows[0]["underlying_price"] == 505
    assert rows[0]["recent_activities"][0]["id"] == "activity-"


def test_legacy_explicit_stale_strike_never_degrades_to_symbol_only():
    stale = _activity("", "2026-09-24T12:00:00Z", risk="HIGH", price=520)
    stale.pop("position_id")
    stale["current_strike"] = 450

    rows = _monitor_rows([_position("pos-a")], [stale])

    assert rows[0]["assignment_risk"] is None
    assert rows[0]["underlying_price"] is None
    assert rows[0]["recent_activities"] == []


def test_legacy_explicit_stale_expiration_never_degrades_to_symbol_only():
    stale = _activity("", "2026-09-24T12:00:00Z", risk="HIGH", price=520)
    stale.pop("position_id")
    stale["current_expiration"] = "2026-12-18"

    rows = _monitor_rows([_position("pos-a")], [stale])

    assert rows[0]["assignment_risk"] is None
    assert rows[0]["recent_activities"] == []


def test_legacy_explicit_identity_fields_must_all_match():
    position = _position(
        "pos-a",
        account_id="acct-a",
        is_paper=True,
        contract_id="contract-a",
        instrument_id="instrument-a",
    )
    mismatches = {
        "account_id": "acct-b",
        "option_type": "put",
        "is_paper": False,
        "contract_id": "contract-b",
        "instrument_id": "instrument-b",
    }

    for field, mismatched_value in mismatches.items():
        legacy = _activity(
            "", "2026-09-24T12:00:00Z", risk="HIGH", price=520
        )
        legacy.pop("position_id")
        legacy[field] = mismatched_value

        rows = _monitor_rows([position], [legacy])

        assert rows[0]["assignment_risk"] is None, field
        assert rows[0]["underlying_price"] is None, field
        assert rows[0]["recent_activities"] == [], field


def test_legacy_explicit_identity_can_select_one_of_same_symbol_positions():
    legacy = _activity("", "2026-09-24T12:00:00Z", risk="LOW", price=495)
    legacy.pop("position_id")
    legacy.update({
        "account_id": "acct-b",
        "is_paper": True,
        "contract_id": "contract-b",
    })
    positions = [
        _position(
            "pos-a", account_id="acct-a", contract_id="contract-a"
        ),
        _position(
            "pos-b",
            account_id="acct-b",
            is_paper=True,
            contract_id="contract-b",
        ),
    ]

    rows = _monitor_rows(positions, [legacy])

    by_id = {row["position_id"]: row for row in rows}
    assert by_id["pos-a"]["assignment_risk"] is None
    assert by_id["pos-b"]["assignment_risk"] == "LOW"


def test_legacy_paper_identity_accepts_only_booleans_and_true_false_strings():
    positions = [
        _position("pos-a", account_id="acct-a"),
        _position("pos-b", account_id="acct-a", is_paper=True),
    ]
    accepted = (
        (False, "pos-a"),
        ("false", "pos-a"),
        (" FALSE ", "pos-a"),
        (True, "pos-b"),
        ("true", "pos-b"),
        (" TRUE ", "pos-b"),
    )

    for value, expected_position_id in accepted:
        legacy = _activity(
            "", "2026-09-24T12:00:00Z", risk="LOW", price=495
        )
        legacy.pop("position_id")
        legacy["is_paper"] = value

        rows = _monitor_rows(positions, [legacy])

        assigned = [
            row["position_id"]
            for row in rows
            if row["assignment_risk"] == "LOW"
        ]
        assert assigned == [expected_position_id], value


def test_legacy_malformed_paper_identity_remains_unassigned():
    for value in ("yes", "no", "1", "0", 1, 0, [], {}):
        legacy = _activity(
            "", "2026-09-24T12:00:00Z", risk="HIGH", price=520
        )
        legacy.pop("position_id")
        legacy["is_paper"] = value

        rows = _monitor_rows([_position("pos-a")], [legacy])

        assert rows[0]["assignment_risk"] is None, value
        assert rows[0]["underlying_price"] is None, value
        assert rows[0]["recent_activities"] == [], value


def test_legacy_conflicting_top_level_and_source_account_is_unassigned():
    legacy = _activity(
        "", "2026-09-24T12:00:00Z", risk="HIGH", price=520
    )
    legacy.pop("position_id")
    legacy["account_id"] = "acct-a"
    legacy["source"] = {"account_id": "acct-b"}

    rows = _monitor_rows(
        [
            _position("pos-a", account_id="acct-a"),
            _position("pos-b", account_id="acct-b"),
        ],
        [legacy],
    )

    assert all(row["assignment_risk"] is None for row in rows)
    assert all(row["recent_activities"] == [] for row in rows)


def test_legacy_same_account_in_top_level_and_source_can_match():
    legacy = _activity(
        "", "2026-09-24T12:00:00Z", risk="LOW", price=495
    )
    legacy.pop("position_id")
    legacy["account_id"] = " ACCT-A "
    legacy["source"] = {"brokerage_account_id": "acct-a"}

    rows = _monitor_rows(
        [_position("pos-a", account_id="acct-a")],
        [legacy],
    )

    assert rows[0]["assignment_risk"] == "LOW"
    assert rows[0]["underlying_price"] == 495


def test_legacy_missing_account_and_paper_values_keep_unique_fallback():
    legacy = _activity(
        "", "2026-09-24T12:00:00Z", risk="LOW", price=495
    )
    legacy.pop("position_id")
    legacy["account_id"] = ""
    legacy["source"] = {"account_id": None, "is_paper": ""}

    rows = _monitor_rows([_position("pos-a")], [legacy])

    assert rows[0]["assignment_risk"] == "LOW"
    assert rows[0]["underlying_price"] == 495


def test_legacy_conflicting_top_level_and_source_option_type_is_unassigned():
    legacy = _activity(
        "", "2026-09-24T12:00:00Z", risk="HIGH", price=520
    )
    legacy.pop("position_id")
    legacy["option_type"] = "call"
    legacy["source"] = {"current_option_type": "put"}

    rows = _monitor_rows([_position("pos-a")], [legacy])

    assert rows[0]["assignment_risk"] is None
    assert rows[0]["recent_activities"] == []


def test_legacy_canonical_type_call_and_put_match_their_positions():
    cases = (
        ("call", "open_call_monitor"),
        ("put", "open_put_monitor"),
    )
    for option_type, agent_key in cases:
        legacy = _activity(
            "",
            "2026-09-24T12:00:00Z",
            risk="LOW",
            price=495,
            option_type=option_type,
        )
        legacy.pop("position_id")
        legacy["type"] = option_type

        rows = _monitor_rows(
            [_position("pos-a", option_type=option_type)],
            [legacy],
            agent_key=agent_key,
        )

        assert rows[0]["assignment_risk"] == "LOW", option_type
        assert rows[0]["recent_activities"][0]["id"] == "activity-"


def test_legacy_option_type_aliases_trim_and_ignore_case():
    legacy = _activity(
        "", "2026-09-24T12:00:00Z", risk="LOW", price=495
    )
    legacy.pop("position_id")
    legacy.update({
        "type": " CALL ",
        "option_type": "Call",
        "right": "call ",
    })
    legacy["source"] = {"current_option_type": " cAlL "}

    rows = _monitor_rows([_position("pos-a")], [legacy])

    assert rows[0]["assignment_risk"] == "LOW"
    assert rows[0]["underlying_price"] == 495


def test_legacy_conflicting_option_type_aliases_are_unassigned():
    for conflicting_alias in ("option_type", "right"):
        legacy = _activity(
            "", "2026-09-24T12:00:00Z", risk="HIGH", price=520
        )
        legacy.pop("position_id")
        legacy["type"] = "put"
        legacy[conflicting_alias] = "call"

        rows = _monitor_rows([_position("pos-a")], [legacy])

        assert rows[0]["assignment_risk"] is None, conflicting_alias
        assert rows[0]["recent_activities"] == [], conflicting_alias


def test_legacy_malformed_canonical_type_is_unassigned():
    for value in (None, "", " ", "calls", "unknown", "c", 1, True, [], {}):
        legacy = _activity(
            "", "2026-09-24T12:00:00Z", risk="HIGH", price=520
        )
        legacy.pop("position_id")
        legacy["type"] = value

        rows = _monitor_rows([_position("pos-a")], [legacy])

        assert rows[0]["assignment_risk"] is None, value
        assert rows[0]["underlying_price"] is None, value
        assert rows[0]["recent_activities"] == [], value


def test_exact_position_id_rejects_mismatched_canonical_type():
    conflicting = _activity(
        "pos-a", "2026-09-24T12:00:00Z", risk="HIGH", price=520
    )
    conflicting["type"] = "put"

    rows = _monitor_rows([_position("pos-a")], [conflicting])

    assert rows[0]["assignment_risk"] is None
    assert rows[0]["underlying_price"] is None
    assert rows[0]["recent_activities"] == []


def test_nested_source_option_type_aliases_match_and_conflict_check():
    matching = _activity(
        "", "2026-09-24T12:00:00Z", risk="LOW", price=495
    )
    matching.pop("position_id")
    matching["source"] = {
        "type": " CALL ",
        "option_type": "call",
        "current_option_type": "Call",
        "right": "cAlL",
    }

    rows = _monitor_rows([_position("pos-a")], [matching])
    assert rows[0]["assignment_risk"] == "LOW"

    conflicting = dict(matching)
    conflicting["source"] = {**matching["source"], "right": "put"}

    rows = _monitor_rows([_position("pos-a")], [conflicting])
    assert rows[0]["assignment_risk"] is None
    assert rows[0]["recent_activities"] == []


def test_legacy_conflicting_top_level_and_source_paper_lane_is_unassigned():
    legacy = _activity(
        "", "2026-09-24T12:00:00Z", risk="HIGH", price=520
    )
    legacy.pop("position_id")
    legacy["is_paper"] = False
    legacy["source"] = {"paper": "true"}

    rows = _monitor_rows(
        [_position("pos-a"), _position("pos-b", is_paper=True)],
        [legacy],
    )

    assert all(row["assignment_risk"] is None for row in rows)
    assert all(row["recent_activities"] == [] for row in rows)


def test_exact_position_id_requires_compatible_explicit_identity():
    conflicting = _activity(
        "pos-a", "2026-09-24T12:00:00Z", risk="HIGH", price=520
    )
    conflicting["account_id"] = "acct-b"
    conflicting["source"] = {"is_paper": "true"}

    rows = _monitor_rows(
        [_position("pos-a", account_id="acct-a")],
        [conflicting],
    )

    assert rows[0]["assignment_risk"] is None
    assert rows[0]["underlying_price"] is None
    assert rows[0]["recent_activities"] == []


def test_exact_position_id_with_compatible_duplicate_identity_can_match():
    compatible = _activity(
        "pos-a", "2026-09-24T12:00:00Z", risk="LOW", price=495
    )
    compatible["account_id"] = "acct-a"
    compatible["source"] = {
        "position_id": "pos-a",
        "brokerage_account_id": " ACCT-A ",
        "paper": "false",
    }

    rows = _monitor_rows(
        [_position("pos-a", account_id="acct-a")],
        [compatible],
    )

    assert rows[0]["assignment_risk"] == "LOW"
    assert rows[0]["underlying_price"] == 495


def test_conflicting_top_level_and_source_position_id_is_unassigned():
    conflicting = _activity(
        "pos-a", "2026-09-24T12:00:00Z", risk="HIGH", price=520
    )
    conflicting["source"] = {"position_id": "pos-b"}

    rows = _monitor_rows(
        [_position("pos-a"), _position("pos-b")],
        [conflicting],
    )

    assert all(row["assignment_risk"] is None for row in rows)
    assert all(row["recent_activities"] == [] for row in rows)


def test_stale_position_identity_is_never_reassigned_by_matching_contract():
    stale = _activity(
        "rolled-position",
        "2026-09-24T12:00:00Z",
        risk="HIGH",
        price=520,
    )

    rows = _monitor_rows([_position("pos-a")], [stale])

    assert len(rows) == 1
    assert rows[0]["position_id"] == "pos-a"
    assert rows[0]["assignment_risk"] is None
    assert rows[0]["recent_activities"] == []


def test_dashboard_payload_keeps_both_general_activities_and_position_rows():
    activities = [
        _activity("pos-a", "2026-09-24T10:00:00Z", risk="LOW", price=490),
        _activity("pos-b", "2026-09-24T11:00:00Z", risk="HIGH", price=510),
    ]
    cosmos = _DashboardCosmos(
        [_position("pos-a"), _position("pos-b")],
        activities,
    )

    payload = _compute_dashboard_data(cosmos)

    rows = next(
        table["rows"]
        for table in payload["agent_tables"]
        if table["key"] == "open_call_monitor"
    )
    assert {row["position_id"] for row in rows} == {"pos-a", "pos-b"}
    assert {activity["id"] for activity in payload["activity"]} == {
        "activity-pos-a",
        "activity-pos-b",
    }


def test_unassigned_stale_legacy_activity_remains_in_global_activity_feed():
    stale = _activity("", "2026-09-24T12:00:00Z", risk="HIGH", price=520)
    stale.pop("position_id")
    stale["current_strike"] = 450
    cosmos = _DashboardCosmos([_position("pos-a")], [stale])

    payload = _compute_dashboard_data(cosmos)

    rows = next(
        table["rows"]
        for table in payload["agent_tables"]
        if table["key"] == "open_call_monitor"
    )
    assert rows[0]["recent_activities"] == []
    assert rows[0]["assignment_risk"] is None
    assert [activity["id"] for activity in payload["activity"]] == [
        "activity-"
    ]


def test_incompatible_account_identity_remains_in_global_activity_feed():
    incompatible = _activity(
        "", "2026-09-24T12:00:00Z", risk="HIGH", price=520
    )
    incompatible.pop("position_id")
    incompatible["account_id"] = "acct-a"
    incompatible["source"] = {"account_id": "acct-b"}
    cosmos = _DashboardCosmos(
        [_position("pos-a", account_id="acct-a")],
        [incompatible],
    )

    payload = _compute_dashboard_data(cosmos)

    rows = next(
        table["rows"]
        for table in payload["agent_tables"]
        if table["key"] == "open_call_monitor"
    )
    assert rows[0]["recent_activities"] == []
    assert rows[0]["assignment_risk"] is None
    assert [activity["id"] for activity in payload["activity"]] == [
        "activity-"
    ]
