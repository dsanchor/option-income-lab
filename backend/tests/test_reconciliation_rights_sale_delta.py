"""Compatibility tests for removed legacy rights movements."""

import pytest

from src.portfolio.rights_policy import (
    contains_legacy_rights_data,
    is_legacy_rights_movement,
    sanitize_legacy_movement,
)


def test_legacy_rights_sale_is_inert():
    movement = {
        "id": "legacy",
        "txn_type": "SELL",
        "sales_type": "DERECHOS",
        "gross": {"eur_amount": "4554"},
    }
    assert is_legacy_rights_movement(movement)
    assert sanitize_legacy_movement(movement) is None


def test_ordinary_sell_loses_obsolete_sale_metadata():
    movement = {
        "id": "ordinary",
        "txn_type": "SELL",
        "sales_type": "ACCIONES",
        "is_rights_sale": False,
    }
    assert sanitize_legacy_movement(movement) == {
        "id": "ordinary",
        "txn_type": "SELL",
    }


def test_zero_legacy_rights_source_column_is_removed():
    movement = {
        "id": "legacy-zero-rights-column",
        "txn_type": "DIVIDEND",
        "source_row": {
            "Empresa": "Example",
            "Importe en derechos": "0,00",
            "Importe neto": "12,50",
        },
    }

    assert sanitize_legacy_movement(movement) == {
        "id": "legacy-zero-rights-column",
        "txn_type": "DIVIDEND",
        "source_row": {
            "Empresa": "Example",
            "Importe neto": "12,50",
        },
    }


def test_legacy_rights_issue_group_leg_is_inert():
    movement = {
        "id": "legacy-issue",
        "txn_type": "BUY",
        "ca_event_type": "RIGHTS_ISSUE",
        "ca_leg_type": "SHARE_ACQUISITION",
    }
    assert sanitize_legacy_movement(movement) is None


@pytest.mark.parametrize(
    "rights_data",
    [
        {"source_derechos_amount": 1},
        {"SOURCE_DERECHOS_AMOUNT": " 1,25 "},
        {"rights amount": "2"},
        {"Importe en Derechos": "2"},
        {"source_derechos_eur": "3"},
        {"source_payload": {"Source Derechos Amount": "3"}},
        {"source_row": {"nested": {"RIGHTS AMOUNT": "4"}}},
        {"source_row": {"Importe en derechos": "not-a-number"}},
        {"source_derechos_amount": "NaN"},
        {"source_derechos_amount": "Infinity"},
        {"is_rights_sale": "maybe"},
        {"sales_type": "  derechos  "},
        {"sales_type_raw": " rights sold "},
        {"sales_type": "unknown-legacy-value"},
        {"ca_leg_type": " rights/sold "},
        {"ca_event_type": " Rights Issue "},
    ],
)
def test_all_legacy_rights_aliases_and_malformed_values_are_inert(rights_data):
    movement = {
        "id": "legacy-dividend",
        "txn_type": "DIVIDEND",
        "net": {"eur_amount": "100"},
        **rights_data,
    }

    assert contains_legacy_rights_data(movement)
    assert is_legacy_rights_movement(movement)
    assert sanitize_legacy_movement(movement) is None


def test_safe_obsolete_metadata_is_stripped_only_when_it_cannot_indicate_rights():
    movement = {
        "id": "ordinary",
        "txn_type": "SELL",
        "sales_type": " ACCIONES ",
        "is_rights_sale": "false",
        "source_derechos_amount": "0.00",
        "source_payload": {
            "Rights Amount": "0,00",
            "broker_reference": "safe",
        },
    }

    assert sanitize_legacy_movement(movement) == {
        "id": "ordinary",
        "txn_type": "SELL",
        "source_payload": {"broker_reference": "safe"},
    }
