import pytest

from src.position_contracts import (
    PositionContractCountError,
    resolve_open_contract_count,
)


def _legacy_position(**overrides):
    position = {
        "position_id": "pos_AAPL_call_200_20270115_20260901_120000",
        "type": "call",
        "strike": 200,
        "expiration": "2027-01-15",
        "opened_at": "2026-09-01T12:00:00Z",
        "status": "active",
        "notes": "",
    }
    position.update(overrides)
    return position


@pytest.mark.parametrize(
    ("position", "count", "source"),
    [
        ({"type": "call", "contracts": 3}, 3, "contracts"),
        ({"type": "put", "quantity": "4"}, 4, "quantity"),
        ({"type": "call", "contracts": 5}, 5, "contracts"),
        ({"type": "put", "quantity": -6}, 6, "quantity"),
        ({"type": "call", "open_contracts": "2", "contracts": 7}, 2, "open_contracts"),
        ({"type": "put", "contracts_open": -3, "quantity": -8}, 3, "contracts_open"),
        ({"type": "call", "contracts": 2, "is_paper": True}, 2, "contracts"),
        ({"type": "put", "quantity": "9", "rolled_from": "pos-old"}, 9, "quantity"),
    ],
)
def test_resolves_manual_imported_rolled_paper_and_remaining_shapes(position, count, source):
    resolved = resolve_open_contract_count(position)
    assert resolved.contracts == count
    assert resolved.quantity_source == source


def test_matching_base_aliases_are_accepted():
    resolved = resolve_open_contract_count({"type": "call", "contracts": "-4", "quantity": 4})
    assert resolved.contracts == 4
    assert resolved.quantity_source == "contracts"


def test_proven_repository_legacy_shape_uses_visible_one_contract_fallback():
    resolved = resolve_open_contract_count(_legacy_position())
    assert resolved.contracts == 1
    assert resolved.quantity_source == "legacy_implicit_one"
    assert "historical one-contract default" in resolved.warnings[0]


@pytest.mark.parametrize(
    "position",
    [
        _legacy_position(
            position_id="pos_AAPL_call_200_20270115_20260926_104442",
            opened_at="2026-09-26T10:44:42Z",
        ),
        _legacy_position(position_schema_version=2),
        _legacy_position(
            position_id="pos_AAPL_call_200_20270115_20260901_120001",
        ),
    ],
)
def test_current_version_boundary_and_inconsistent_timestamp_cannot_use_legacy_fallback(position):
    with pytest.raises(PositionContractCountError) as exc:
        resolve_open_contract_count(position)
    assert exc.value.code == "position_quantity_missing"


@pytest.mark.parametrize(
    "position",
    [
        {"type": "call"},
        _legacy_position(position_id="imported-position"),
        _legacy_position(notes=None, opened_at=None),
    ],
)
def test_missing_nonlegacy_or_corrupt_shape_fails_precisely(position):
    with pytest.raises(PositionContractCountError) as exc:
        resolve_open_contract_count(position)
    assert exc.value.code == "position_quantity_missing"
    assert "Set 'contracts'" in str(exc.value)


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (True, "position_quantity_invalid"),
        ("NaN", "position_quantity_invalid"),
        ("Infinity", "position_quantity_invalid"),
        (float("inf"), "position_quantity_invalid"),
        ("1.5", "position_quantity_fractional"),
        (0, "position_quantity_zero"),
        ("-0", "position_quantity_zero"),
        (None, "position_quantity_invalid"),
    ],
)
def test_rejects_boolean_nonfinite_fractional_and_zero(value, code):
    with pytest.raises(PositionContractCountError) as exc:
        resolve_open_contract_count({"contracts": value})
    assert exc.value.code == code


def test_negative_count_requires_short_option_semantics():
    with pytest.raises(PositionContractCountError) as exc:
        resolve_open_contract_count({"contracts": -2})
    assert exc.value.code == "position_quantity_invalid"


def test_conflicting_same_precedence_aliases_fail():
    with pytest.raises(PositionContractCountError) as exc:
        resolve_open_contract_count({"contracts": 2, "quantity": 3})
    assert exc.value.code == "position_quantity_conflict"


def test_never_reads_another_position():
    selected = resolve_open_contract_count({"position_id": "selected", "contracts": 2})
    other = {"position_id": "other", "contracts": 99}
    assert selected.contracts == 2
    assert other["contracts"] == 99
