from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any

from src.dividends_economics import build_dividends_economics_report
from src.portfolio.holdings_service import OPTION_TXN_TYPES, HoldingsService

from .canonical import canonical_hash, normalize
from .section_schemas import logical_key


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value if value is not None else "0"))
    except (InvalidOperation, ValueError):
        return Decimal(0)


class _PortfolioAdapter:
    def __init__(self, movements: list[dict[str, Any]]) -> None:
        self.movements = movements

    def get_all_movements_for_holdings(self) -> list[dict[str, Any]]:
        return [
            deepcopy(item)
            for item in self.movements
            if not item.get("deleted_at")
            and item.get("correction_status") in (None, "", "ACTIVE")
        ]


class _ExistingConfigContainer:
    def read_item(self, item: str, partition_key: str) -> dict[str, Any]:
        return {"id": item, "symbol": partition_key}


class _SecuritiesAdapter:
    container = _ExistingConfigContainer()

    def __init__(self, securities: list[dict[str, Any]]) -> None:
        self.by_id = {item.get("security_id"): item for item in securities}

    def get_security(self, security_id: str) -> dict[str, Any] | None:
        return deepcopy(self.by_id.get(security_id))


def _option_economics(movements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item in movements:
        if (
            item.get("txn_type") not in OPTION_TXN_TYPES
            or item.get("deleted_at")
            or item.get("correction_status") not in (None, "", "ACTIVE")
        ):
            continue
        key = (
            str(item.get("account_id") or ""),
            str(item.get("security_id") or ""),
            str(item.get("option_position_id") or ""),
            str(item.get("option_type") or ""),
        )
        group = groups.setdefault(key, {
            "account_id": key[0],
            "security_id": key[1],
            "option_position_id": key[2],
            "option_type": key[3],
            "movement_count": 0,
            "gross_eur": Decimal(0),
            "fees_eur": Decimal(0),
            "net_eur": Decimal(0),
        })
        group["movement_count"] += 1
        group["gross_eur"] += _decimal((item.get("gross") or {}).get("eur_amount"))
        group["fees_eur"] += _decimal((item.get("fees") or {}).get("total_eur"))
        group["net_eur"] += _decimal((item.get("net") or {}).get("eur_amount"))
    return normalize([
        groups[key]
        for key in sorted(groups)
    ])


_LINK_FIELDS = (
    "corrects_movement_id", "superseded_by", "reassigned_from", "reassigned_to",
    "transfer_group_id", "transfer_pair_id", "paired_movement_id",
    "ca_group_id", "replacement_group_id", "replaces_ca_group_id",
    "replaced_by_ca_group_id", "superseded_by_ca_group_id",
    "option_position_id", "option_link_kind",
)


def _relationship_inventory(movements: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, dict[str, list[str]]] = {
        "transfer_group_id": defaultdict(list),
        "transfer_pair_id": defaultdict(list),
        "ca_group_id": defaultdict(list),
    }
    links = []
    for item in movements:
        key = logical_key("ledger_movements", item)
        values = {}
        for field in _LINK_FIELDS:
            value = item.get(field)
            if value not in (None, "", {}, []):
                values[field] = normalize(value)
        if values:
            links.append({"movement": key, "links": values})
        for field, index in groups.items():
            if item.get(field):
                index[str(item[field])].append(key)
    return {
        "groups": {
            field: {
                group_id: sorted(members)
                for group_id, members in sorted(index.items())
            }
            for field, index in groups.items()
        },
        "links": sorted(links, key=lambda item: item["movement"]),
    }


def compute_controls(sections: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    movements = sections.get("ledger_movements", [])
    holdings_service = HoldingsService(
        _PortfolioAdapter(movements), _SecuritiesAdapter(sections.get("securities", []))
    )
    holdings = holdings_service.compute_holdings()
    account_currencies = {
        str(item.get("account_id")): str(
            item.get("base_currency") or item.get("currency") or ""
        )
        for item in sections.get("accounts", [])
        if item.get("account_id")
    }
    holdings["by_account"] = [
        {
            "account_id": account_id,
            "currency": account_currencies[account_id],
            **holdings_service.compute_holdings(account_id),
        }
        for account_id in sorted(account_currencies)
    ]
    dividends = build_dividends_economics_report(movements)
    audit_states = Counter(
        "deleted" if item.get("deleted_at") else str(item.get("correction_status") or "ACTIVE")
        for item in movements
    )
    fx_withholding = [
        {
            "movement": logical_key("ledger_movements", item),
            "fx": item.get("fx"),
            "withholding": item.get("withholding"),
            "gross": item.get("gross"),
            "fees": item.get("fees"),
            "net": item.get("net"),
        }
        for item in movements
    ]
    fx_withholding.sort(key=lambda item: item["movement"])
    relationships = _relationship_inventory(movements)
    return normalize({
        "version": 1,
        "counts": {
            section: len(sections.get(section, []))
            for section in sorted(sections)
        },
        "section_hashes": {
            section: canonical_hash(sections.get(section, []))
            for section in sorted(sections)
        },
        "holdings": holdings,
        "dividends": {
            "summary": dividends.get("summary"),
            "monthly": dividends.get("monthly"),
            "by_symbol": dividends.get("by_symbol"),
            "yearly": dividends.get("yearly"),
        },
        "option_economics": _option_economics(movements),
        "fx_withholding": fx_withholding,
        "relationships": relationships,
        "audit_history": {
            "count": len(movements),
            "states": dict(sorted(audit_states.items())),
            "keys": sorted(logical_key("ledger_movements", item) for item in movements),
            "hash": canonical_hash(movements),
        },
    })
