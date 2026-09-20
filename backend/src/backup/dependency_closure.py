from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from src.portfolio.cosmos_portfolio import _CA_LEG_TXN_TYPE, _CA_REQUIRED_LEGS

from .section_schemas import logical_key

MOVEMENT_LINK_FIELDS = (
    "corrects_movement_id", "superseded_by", "paired_movement_id",
    "transfer_peer_id",
)
CA_GROUP_LINK_FIELDS = (
    "replacement_group_id", "replaces_ca_group_id",
    "replaced_by_ca_group_id", "superseded_by_ca_group_id",
)
LEGACY_ACCOUNT_SENTINEL = "_unassigned"


def _is_supported_account_reference(account_id: Any, accounts: set[Any]) -> bool:
    return account_id == LEGACY_ACCOUNT_SENTINEL or account_id in accounts


def _linked_movement_ids(movement: dict[str, Any]) -> set[str]:
    linked = {
        str(movement[field])
        for field in MOVEMENT_LINK_FIELDS
        if movement.get(field)
    }
    for field in ("reassigned_from", "reassigned_to"):
        value = movement.get(field)
        if isinstance(value, dict):
            value = value.get("movement_id") or value.get("id")
        if value:
            linked.add(str(value))
    return linked


def _security(record: dict[str, Any]) -> str | None:
    return record.get("security_id")


def _transfer_accounts(movement: dict[str, Any]) -> tuple[Any, Any]:
    return (
        movement.get("transfer_source_account_id")
        or movement.get("source_account_id"),
        movement.get("transfer_dest_account_id")
        or movement.get("destination_account_id"),
    )


def _transfer_peer_id(movement: dict[str, Any]) -> Any:
    return movement.get("transfer_peer_id") or movement.get("paired_movement_id")


def _is_corporate_action_movement(movement: dict[str, Any]) -> bool:
    return bool(movement.get("ca_event_type") or movement.get("ca_leg_type"))


def _is_ordinary_transfer(movement: dict[str, Any]) -> bool:
    if _is_corporate_action_movement(movement):
        return False
    return (
        movement.get("txn_type") in {"TRANSFER_OUT", "TRANSFER_IN"}
        or movement.get("transfer_group_id")
        or movement.get("transfer_pair_id")
    )


def apply_filters(
    records: dict[str, list[dict[str, Any]]], filters: dict[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    account_ids = set(filters.get("account_ids") or [])
    security_ids = set(filters.get("security_ids") or [])
    symbols = {str(value).upper() for value in filters.get("symbols") or []}
    date_from, date_to = filters.get("date_from"), filters.get("date_to")
    selected = {name: list(items) for name, items in records.items()}
    if security_ids or symbols:
        def matches_security(item: dict[str, Any]) -> bool:
            if security_ids and item.get("security_id") not in security_ids:
                return False
            symbol = str(item.get("symbol") or item.get("ticker", "")).upper()
            if symbols and symbol not in symbols:
                return False
            return True

        for section in ("securities", "symbol_configs", "option_positions", "action_plans"):
            selected[section] = [
                item for item in records.get(section, []) if matches_security(item)
            ]
    if account_ids:
        selected["accounts"] = [
            item for item in records.get("accounts", [])
            if item.get("account_id") in account_ids
        ]
    if account_ids or security_ids or symbols or date_from or date_to:
        movements = []
        for item in records.get("ledger_movements", []):
            if account_ids and item.get("account_id") not in account_ids:
                continue
            if security_ids and item.get("security_id") not in security_ids:
                continue
            if symbols and str(item.get("ticker") or item.get("symbol", "")).upper() not in symbols:
                continue
            if date_from and str(item.get("trade_date", "")) < date_from:
                continue
            if date_to and str(item.get("trade_date", "")) > date_to:
                continue
            movements.append(item)
        selected["ledger_movements"] = movements
    return selected


def close_dependencies(
    all_records: dict[str, list[dict[str, Any]]],
    selected: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int], list[str]]:
    by_section = {
        section: {logical_key(section, item): item for item in items}
        for section, items in all_records.items()
    }
    chosen = {
        section: {logical_key(section, item): item for item in items}
        for section, items in selected.items()
    }
    initial = {section: len(items) for section, items in chosen.items()}
    warnings: list[str] = []

    accounts = {item.get("account_id"): item for item in all_records.get("accounts", [])}
    securities = {item.get("security_id"): item for item in all_records.get("securities", [])}
    configs = {
        identity: item
        for item in all_records.get("symbol_configs", [])
        for identity in (item.get("security_id"), item.get("symbol"))
        if identity
    }
    positions = {
        item.get("position_id"): item
        for item in all_records.get("option_positions", [])
    }
    movements = all_records.get("ledger_movements", [])
    movement_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in movements:
        if item.get("id"):
            movement_index[str(item["id"])].append(item)
    reverse_links: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in movements:
        for linked_id in _linked_movement_ids(item):
            reverse_links[linked_id].append(item)
    ca_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    transfer_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for movement in movements:
        if movement.get("ca_group_id"):
            ca_groups[str(movement["ca_group_id"])].append(movement)
        group = movement.get("transfer_group_id") or movement.get("transfer_pair_id")
        if group:
            transfer_groups[str(group)].append(movement)
    ca_group_links: dict[str, set[str]] = defaultdict(set)
    for movement in movements:
        source_group = movement.get("ca_group_id")
        if not source_group:
            continue
        for field in CA_GROUP_LINK_FIELDS:
            target_group = movement.get(field)
            if target_group:
                ca_group_links[str(source_group)].add(str(target_group))
                ca_group_links[str(target_group)].add(str(source_group))

    queue = deque(chosen.get("ledger_movements", {}).values())
    seen: set[str] = set()
    while queue:
        movement = queue.popleft()
        marker = f"{movement.get('account_id')}|{movement.get('id')}"
        if marker in seen:
            continue
        seen.add(marker)
        account_id = movement.get("account_id")
        security_id = _security(movement)
        if account_id in accounts:
            item = accounts[account_id]
            chosen["accounts"][logical_key("accounts", item)] = item
        elif account_id != LEGACY_ACCOUNT_SENTINEL:
            warnings.append(f"BROKEN_ACCOUNT_REFERENCE:{marker}:{account_id}")
        if security_id in securities:
            item = securities[security_id]
            chosen["securities"][logical_key("securities", item)] = item
        else:
            warnings.append(f"BROKEN_SECURITY_REFERENCE:{marker}:{security_id}")
        linked_movements = []
        for linked_id in _linked_movement_ids(movement):
            linked_movements.extend(movement_index.get(linked_id, []))
            if not movement_index.get(linked_id):
                warnings.append(f"BROKEN_MOVEMENT_REFERENCE:{marker}:{linked_id}")
        linked_movements.extend(reverse_links.get(str(movement.get("id")), []))
        for linked in linked_movements:
            key = logical_key("ledger_movements", linked)
            if key not in chosen["ledger_movements"]:
                chosen["ledger_movements"][key] = linked
                queue.append(linked)
        source_group = movement.get("ca_group_id")
        groups_to_add = {str(source_group)} if source_group else set()
        pending_groups = list(groups_to_add)
        while pending_groups:
            group_id = pending_groups.pop()
            for linked_group in ca_group_links.get(group_id, set()):
                if linked_group not in groups_to_add:
                    groups_to_add.add(linked_group)
                    pending_groups.append(linked_group)
        for group_id in groups_to_add:
            for member in ca_groups.get(group_id, []):
                key = logical_key("ledger_movements", member)
                if key not in chosen["ledger_movements"]:
                    chosen["ledger_movements"][key] = member
                    queue.append(member)
        group = movement.get("transfer_group_id") or movement.get("transfer_pair_id")
        for member in transfer_groups.get(str(group), []):
            key = logical_key("ledger_movements", member)
            if key not in chosen["ledger_movements"]:
                chosen["ledger_movements"][key] = member
                queue.append(member)
        position_id = movement.get("option_position_id")
        if position_id:
            position = positions.get(position_id)
            if position:
                chosen["option_positions"][logical_key("option_positions", position)] = position
            else:
                warnings.append(f"BROKEN_POSITION_REFERENCE:{marker}:{position_id}")

    for section in ("option_positions", "action_plans"):
        for record in list(chosen.get(section, {}).values()):
            security_id = record.get("security_id")
            config = configs.get(security_id) or configs.get(record.get("symbol"))
            if config:
                chosen["symbol_configs"][logical_key("symbol_configs", config)] = config
            else:
                warnings.append(f"BROKEN_SYMBOL_CONFIG_REFERENCE:{logical_key(section, record)}")
            security = securities.get(security_id)
            if security:
                chosen["securities"][logical_key("securities", security)] = security
            elif security_id:
                warnings.append(f"BROKEN_SECURITY_REFERENCE:{logical_key(section, record)}:{security_id}")

    result = {
        section: list(chosen.get(section, {}).values())
        for section in by_section
    }
    additions = {
        section: max(0, len(chosen.get(section, {})) - initial.get(section, 0))
        for section in by_section
        if len(chosen.get(section, {})) > initial.get(section, 0)
    }
    return result, additions, sorted(set(warnings))


def validate_dependency_closure(records: dict[str, list[dict[str, Any]]]) -> list[str]:
    errors: list[str] = []
    accounts = {item.get("account_id") for item in records.get("accounts", [])}
    securities = {item.get("security_id") for item in records.get("securities", [])}
    configs = {
        identity
        for item in records.get("symbol_configs", [])
        for identity in (item.get("security_id"), item.get("symbol"))
        if identity
    }
    positions = {item.get("position_id") for item in records.get("option_positions", [])}
    movements = records.get("ledger_movements", [])
    movement_ids = {str(item.get("id")) for item in movements if item.get("id")}
    ca_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    transfer_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for movement in movements:
        if movement.get("ca_group_id"):
            ca_groups[str(movement["ca_group_id"])].append(movement)
        if _is_ordinary_transfer(movement):
            for field in ("transfer_group_id", "transfer_pair_id"):
                if movement.get(field):
                    transfer_groups[(field, str(movement[field]))].append(movement)
    for movement in movements:
        key = logical_key("ledger_movements", movement)
        if not _is_supported_account_reference(movement.get("account_id"), accounts):
            errors.append(f"{key}:missing_account")
        if movement.get("security_id") not in securities:
            errors.append(f"{key}:missing_security")
        if movement.get("option_position_id") and movement["option_position_id"] not in positions:
            errors.append(f"{key}:missing_option_position")
        for linked_id in _linked_movement_ids(movement):
            if linked_id not in movement_ids:
                errors.append(f"{key}:missing_movement_link:{linked_id}")
        for field in CA_GROUP_LINK_FIELDS:
            linked_group = movement.get(field)
            if linked_group and str(linked_group) not in ca_groups:
                errors.append(f"{key}:missing_{field}:{linked_group}")
        transfer_group_fields = [
            field for field in ("transfer_group_id", "transfer_pair_id")
            if movement.get(field)
        ]
        if (
            _is_ordinary_transfer(movement)
            and movement.get("txn_type") in {"TRANSFER_OUT", "TRANSFER_IN"}
        ):
            if not transfer_group_fields:
                errors.append(f"{key}:missing_transfer_group")
            elif len(transfer_group_fields) > 1:
                errors.append(f"{key}:ambiguous_transfer_group")
        if (
            (movement.get("ca_event_type") or movement.get("ca_leg_type"))
            and not movement.get("ca_group_id")
        ):
            errors.append(f"{key}:orphan_ca_metadata")
    for (field, group_id), members in transfer_groups.items():
        group_key = f"{field}:{group_id}"
        if len(members) != 2:
            errors.append(f"{group_key}:incomplete_group")
            continue
        by_direction: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for member in members:
            by_direction[str(member.get("txn_type"))].append(member)
        if (
            len(by_direction["TRANSFER_OUT"]) != 1
            or len(by_direction["TRANSFER_IN"]) != 1
            or set(by_direction) != {"TRANSFER_OUT", "TRANSFER_IN"}
        ):
            errors.append(f"{group_key}:invalid_directions")
            continue
        transfer_out = by_direction["TRANSFER_OUT"][0]
        transfer_in = by_direction["TRANSFER_IN"][0]
        out_id = str(transfer_out.get("id") or "")
        in_id = str(transfer_in.get("id") or "")
        if not out_id or not in_id or out_id == in_id:
            errors.append(f"{group_key}:invalid_leg_ids")
        if (
            str(_transfer_peer_id(transfer_out) or "") != in_id
            or str(_transfer_peer_id(transfer_in) or "") != out_id
        ):
            errors.append(f"{group_key}:nonreciprocal_peers")
        out_source, out_destination = _transfer_accounts(transfer_out)
        in_source, in_destination = _transfer_accounts(transfer_in)
        if (
            not out_source
            or not out_destination
            or out_source == out_destination
            or out_source != in_source
            or out_destination != in_destination
            or transfer_out.get("account_id") != out_source
            or transfer_in.get("account_id") != out_destination
            or not _is_supported_account_reference(out_source, accounts)
            or not _is_supported_account_reference(out_destination, accounts)
        ):
            errors.append(f"{group_key}:invalid_account_relationship")
        if transfer_out.get("security_id") != transfer_in.get("security_id"):
            errors.append(f"{group_key}:mismatched_security")

    for group_id, members in ca_groups.items():
        event_types = {member.get("ca_event_type") for member in members}
        if len(event_types) != 1:
            errors.append(f"ca_group_id:{group_id}:inconsistent_event_type")
            continue
        event_type = next(iter(event_types))
        if event_type not in _CA_REQUIRED_LEGS:
            errors.append(f"ca_group_id:{group_id}:invalid_event_type:{event_type}")
            continue
        leg_types = {member.get("ca_leg_type") for member in members}
        missing = _CA_REQUIRED_LEGS[event_type] - leg_types
        if missing:
            errors.append(
                f"ca_group_id:{group_id}:missing_required_legs:"
                f"{','.join(sorted(missing))}"
            )
        for member in members:
            member_key = logical_key("ledger_movements", member)
            leg_type = member.get("ca_leg_type")
            expected_txn_type = _CA_LEG_TXN_TYPE.get(leg_type)
            if expected_txn_type is None:
                errors.append(
                    f"{member_key}:invalid_ca_leg_type:{leg_type}"
                )
            elif member.get("txn_type") != expected_txn_type:
                errors.append(
                    f"{member_key}:invalid_ca_leg_txn_type:"
                    f"{leg_type}:{member.get('txn_type')}:{expected_txn_type}"
                )
    for section in ("option_positions", "action_plans"):
        for record in records.get(section, []):
            identity = record.get("security_id") or record.get("symbol")
            if identity not in configs:
                errors.append(f"{logical_key(section, record)}:missing_symbol_config")
            if record.get("security_id") and record["security_id"] not in securities:
                errors.append(f"{logical_key(section, record)}:missing_security")
    return sorted(set(errors))
