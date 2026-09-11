from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .cosmos_portfolio import CosmosPortfolioService
from .cosmos_securities import CosmosSecuritiesService
from .models import OPTION_TXN_TYPES

logger = logging.getLogger(__name__)

OPTION_SECURITY_UNRESOLVED = "OPTION_SECURITY_UNRESOLVED"
OPTION_OPENING_SELL_MISSING = "OPTION_OPENING_SELL_MISSING"
OPTION_MANUAL_CLOSE_BUY_MISSING = "OPTION_MANUAL_CLOSE_BUY_MISSING"
OPTION_ASSIGNMENT_STOCK_MISSING = "OPTION_ASSIGNMENT_STOCK_MISSING"
OPTION_ACCOUNT_FILTER_EXCLUDED_UNLINKED = "OPTION_ACCOUNT_FILTER_EXCLUDED_UNLINKED"

_OPTION_POSITION_TYPES = frozenset({"call", "put"})
_STOCK_TXN_TYPES = frozenset({"BUY", "SELL"})
_QUERY_CHUNK_SIZE = 50


def _parse_numeric(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    if isinstance(value, str):
        cleaned = value.strip().replace("$", "").replace(",", "")
        if not cleaned or cleaned.upper() in {"N/A", "NA", "NONE", "NULL", "—", "-"}:
            return None
        try:
            numeric = float(cleaned)
        except ValueError:
            return None
        return numeric if math.isfinite(numeric) else None
    return None



def _chunked(values: Sequence[str], chunk_size: int = _QUERY_CHUNK_SIZE) -> Iterable[List[str]]:
    for start in range(0, len(values), chunk_size):
        yield list(values[start:start + chunk_size])



def collect_option_positions(symbol_docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for symbol_doc in symbol_docs:
        symbol = str(symbol_doc.get("symbol", "")).strip().upper()
        if not symbol:
            continue
        symbol_doc_security_id = symbol_doc.get("security_id")
        for position in symbol_doc.get("positions", []):
            position_type = str(position.get("type", "")).strip().lower()
            if position_type not in _OPTION_POSITION_TYPES:
                continue
            source = position.get("source") if isinstance(position.get("source"), dict) else {}
            rows.append({
                "symbol": symbol,
                "position_id": position.get("position_id"),
                "type": position_type,
                "is_paper": bool(position.get("is_paper")),
                "status": str(position.get("status", "active")).strip().lower(),
                "close_reason": str(position.get("close_reason", "")).strip().lower() or None,
                "strike": position.get("strike"),
                "expiration": position.get("expiration"),
                "opened_at": position.get("opened_at"),
                "closed_at": position.get("closed_at"),
                "rolled_from": position.get("rolled_from"),
                "rolled_to": position.get("rolled_to"),
                "legacy_premium_present": _parse_numeric(source.get("premium")) is not None,
                "legacy_buyback_present": _parse_numeric(position.get("buyback_cost")) is not None,
                "symbol_doc_security_id": symbol_doc_security_id,
            })
    return rows



def _resolve_security_ids(
    position_rows: List[Dict[str, Any]],
    *,
    securities: Optional[List[Dict[str, Any]]] = None,
    securities_svc: Optional[CosmosSecuritiesService] = None,
) -> List[Dict[str, Any]]:
    if securities is None and securities_svc is not None:
        try:
            securities = securities_svc.list_securities()
        except Exception as exc:
            logger.warning("list_securities failed during option linkage: %s", exc)
            securities = []
    securities = securities or []

    active_by_ticker: Dict[str, List[str]] = defaultdict(list)
    for sec in securities:
        if str(sec.get("status", "ACTIVE")).upper() != "ACTIVE":
            continue
        ticker = str(sec.get("ticker", "")).strip().upper()
        security_id = sec.get("security_id")
        if ticker and security_id:
            active_by_ticker[ticker].append(security_id)

    resolved_rows: List[Dict[str, Any]] = []
    for row in position_rows:
        resolved = row.get("symbol_doc_security_id")
        unresolved = False
        if not resolved:
            matches = active_by_ticker.get(row["symbol"], [])
            if len(matches) == 1:
                resolved = matches[0]
            else:
                unresolved = True
        resolved_rows.append({
            **row,
            "resolved_security_id": resolved,
            "security_unresolved": unresolved,
        })
    return resolved_rows



def _is_active_linked_movement(
    movement: Dict[str, Any],
    *,
    security_ids: Set[str],
    txn_types: Set[str],
    account_ids: Optional[Set[str]] = None,
) -> bool:
    if movement.get("doc_type") != "ledger_txn":
        return False
    if movement.get("deleted_at") is not None:
        return False
    if movement.get("correction_status", "ACTIVE") != "ACTIVE":
        return False
    if movement.get("txn_type") not in txn_types:
        return False
    if not movement.get("option_position_id"):
        return False
    if movement.get("security_id") not in security_ids:
        return False
    if account_ids is not None and movement.get("account_id") not in account_ids:
        return False
    return True



def _fetch_linked_movements(
    portfolio_svc: CosmosPortfolioService,
    *,
    security_ids: Set[str],
    txn_types: Set[str],
    account_filter: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    if not security_ids:
        return []

    account_ids = set(account_filter) if account_filter else None
    seen_ids: Set[Tuple[str, str]] = set()
    results: List[Dict[str, Any]] = []
    fallback_items: Optional[List[Dict[str, Any]]] = None

    for security_chunk in _chunked(sorted(security_ids)):
        query = (
            "SELECT * FROM c "
            "WHERE c.doc_type = 'ledger_txn' "
            "AND NOT IS_DEFINED(c.deleted_at) "
            "AND (NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE') "
            "AND ARRAY_CONTAINS(@txn_types, c.txn_type) "
            "AND IS_DEFINED(c.option_position_id) "
            "AND ARRAY_CONTAINS(@security_ids, c.security_id)"
        )
        parameters = [
            {"name": "@txn_types", "value": list(txn_types)},
            {"name": "@security_ids", "value": security_chunk},
        ]
        if account_filter:
            query += " AND ARRAY_CONTAINS(@account_ids, c.account_id)"
            parameters.append({"name": "@account_ids", "value": list(account_filter)})
        try:
            items = list(portfolio_svc.portfolio_container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True,
            ))
        except Exception as exc:
            logger.warning("option linkage query failed; falling back to full movement scan: %s", exc)
            if fallback_items is None:
                fallback_items = portfolio_svc.get_all_movements_for_holdings()
            items = fallback_items

        for movement in items:
            if not _is_active_linked_movement(
                movement,
                security_ids=set(security_chunk),
                txn_types=txn_types,
                account_ids=account_ids,
            ):
                continue
            key = (
                str(movement.get("account_id") or ""),
                str(movement.get("id") or ""),
            )
            if key in seen_ids:
                continue
            seen_ids.add(key)
            results.append(movement)

    results.sort(key=lambda m: (
        m.get("trade_date") or "",
        m.get("id") or "",
    ))
    return results



def _filter_linked_movements(
    movements: Iterable[Dict[str, Any]],
    *,
    security_ids: Set[str],
    txn_types: Set[str],
    account_filter: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    account_ids = set(account_filter) if account_filter else None
    results = [
        movement
        for movement in movements
        if _is_active_linked_movement(
            movement,
            security_ids=security_ids,
            txn_types=txn_types,
            account_ids=account_ids,
        )
    ]
    results.sort(key=lambda m: (
        m.get("trade_date") or "",
        m.get("id") or "",
    ))
    return results



def _build_indexes(movements: Iterable[Dict[str, Any]]) -> tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Set[str]]]:
    movements_by_position_id: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    accounts_by_position_id: Dict[str, Set[str]] = defaultdict(set)
    for movement in movements:
        position_id = movement.get("option_position_id")
        if not position_id:
            continue
        movements_by_position_id[position_id].append(movement)
        account_id = movement.get("account_id")
        if account_id:
            accounts_by_position_id[position_id].add(str(account_id))
    for docs in movements_by_position_id.values():
        docs.sort(key=lambda m: (
            m.get("trade_date") or "",
            m.get("id") or "",
        ))
    return dict(movements_by_position_id), dict(accounts_by_position_id)



def _merge_account_indexes(*indexes: Dict[str, Set[str]]) -> Dict[str, Set[str]]:
    merged: Dict[str, Set[str]] = defaultdict(set)
    for index in indexes:
        for position_id, accounts in index.items():
            merged[position_id].update(accounts)
    return dict(merged)



def _expected_option_txn_types(position_type: str) -> tuple[str, str, str]:
    if position_type == "call":
        return "CALL_SELL", "CALL_BUY", "SELL"
    return "PUT_SELL", "PUT_BUY", "BUY"



def _build_position_warnings(
    row: Dict[str, Any],
    option_movements: List[Dict[str, Any]],
    assignment_movements: List[Dict[str, Any]],
) -> List[str]:
    if row.get("is_paper"):
        return []
    if row.get("security_unresolved"):
        return [OPTION_SECURITY_UNRESOLVED]

    expected_open_txn, expected_close_txn, expected_assignment_txn = _expected_option_txn_types(row["type"])
    has_opening_sell = any(m.get("txn_type") == expected_open_txn for m in option_movements)
    has_closing_buy = any(m.get("txn_type") == expected_close_txn for m in option_movements)
    has_assignment_stock = any(m.get("txn_type") == expected_assignment_txn for m in assignment_movements)

    warnings: List[str] = []
    if row.get("legacy_premium_present") and not has_opening_sell:
        warnings.append(OPTION_OPENING_SELL_MISSING)
    if row.get("status") == "rolled" and not has_closing_buy:
        warnings.append(OPTION_MANUAL_CLOSE_BUY_MISSING)
    elif row.get("close_reason") == "manual" and not has_closing_buy:
        warnings.append(OPTION_MANUAL_CLOSE_BUY_MISSING)
    if row.get("close_reason") == "assigned" and not has_assignment_stock:
        warnings.append(OPTION_ASSIGNMENT_STOCK_MISSING)
    return warnings



def build_option_position_linkage(
    symbol_docs: List[Dict[str, Any]],
    *,
    account_filter: Optional[List[str]] = None,
    movements: Optional[List[Dict[str, Any]]] = None,
    securities: Optional[List[Dict[str, Any]]] = None,
    portfolio_svc: Optional[CosmosPortfolioService] = None,
    securities_svc: Optional[CosmosSecuritiesService] = None,
) -> Dict[str, Any]:
    position_rows = _resolve_security_ids(
        collect_option_positions(symbol_docs),
        securities=securities,
        securities_svc=securities_svc,
    )

    resolved_security_ids = {
        str(row["resolved_security_id"])
        for row in position_rows
        if row.get("resolved_security_id")
    }

    if movements is None and portfolio_svc is not None:
        all_option_movements = _fetch_linked_movements(
            portfolio_svc,
            security_ids=resolved_security_ids,
            txn_types=set(OPTION_TXN_TYPES),
        )
        all_assignment_movements = _fetch_linked_movements(
            portfolio_svc,
            security_ids=resolved_security_ids,
            txn_types=set(_STOCK_TXN_TYPES),
        )
        if account_filter:
            scoped_option_movements = _fetch_linked_movements(
                portfolio_svc,
                security_ids=resolved_security_ids,
                txn_types=set(OPTION_TXN_TYPES),
                account_filter=account_filter,
            )
            scoped_assignment_movements = _fetch_linked_movements(
                portfolio_svc,
                security_ids=resolved_security_ids,
                txn_types=set(_STOCK_TXN_TYPES),
                account_filter=account_filter,
            )
        else:
            scoped_option_movements = list(all_option_movements)
            scoped_assignment_movements = list(all_assignment_movements)
    else:
        active_movements = movements or []
        all_option_movements = _filter_linked_movements(
            active_movements,
            security_ids=resolved_security_ids,
            txn_types=set(OPTION_TXN_TYPES),
        )
        all_assignment_movements = _filter_linked_movements(
            active_movements,
            security_ids=resolved_security_ids,
            txn_types=set(_STOCK_TXN_TYPES),
        )
        scoped_option_movements = _filter_linked_movements(
            active_movements,
            security_ids=resolved_security_ids,
            txn_types=set(OPTION_TXN_TYPES),
            account_filter=account_filter,
        )
        scoped_assignment_movements = _filter_linked_movements(
            active_movements,
            security_ids=resolved_security_ids,
            txn_types=set(_STOCK_TXN_TYPES),
            account_filter=account_filter,
        )

    option_moves_by_position_id, option_accounts_by_position_id = _build_indexes(all_option_movements)
    assignment_stock_by_position_id, assignment_accounts_by_position_id = _build_indexes(all_assignment_movements)
    scoped_option_moves_by_position_id, scoped_option_accounts_by_position_id = _build_indexes(scoped_option_movements)
    scoped_assignment_stock_by_position_id, scoped_assignment_accounts_by_position_id = _build_indexes(scoped_assignment_movements)

    accounts_by_position_id = _merge_account_indexes(
        option_accounts_by_position_id,
        assignment_accounts_by_position_id,
    )
    scoped_accounts_by_position_id = _merge_account_indexes(
        scoped_option_accounts_by_position_id,
        scoped_assignment_accounts_by_position_id,
    )

    enriched_rows: List[Dict[str, Any]] = []
    for row in position_rows:
        position_id = row.get("position_id")
        option_movements = option_moves_by_position_id.get(position_id, [])
        assignment_movements = assignment_stock_by_position_id.get(position_id, [])
        scoped_option_rows = scoped_option_moves_by_position_id.get(position_id, [])
        scoped_assignment_rows = scoped_assignment_stock_by_position_id.get(position_id, [])
        linked_count = len(option_movements) + len(assignment_movements)
        scoped_linked_count = len(scoped_option_rows) + len(scoped_assignment_rows)

        if row.get("is_paper"):
            coverage_status = "paper"
        elif row.get("security_unresolved"):
            coverage_status = "unresolved_security"
        elif scoped_linked_count > 0:
            coverage_status = "linked"
        elif account_filter and linked_count > 0:
            coverage_status = "account_filtered_out"
        else:
            coverage_status = "unlinked"

        enriched_rows.append({
            **row,
            "warnings": _build_position_warnings(row, option_movements, assignment_movements),
            "coverage_status": coverage_status,
            "linked_accounts": sorted(accounts_by_position_id.get(position_id, set())),
            "linked_accounts_in_scope": sorted(scoped_accounts_by_position_id.get(position_id, set())),
            "linked_movement_count": linked_count,
            "linked_movement_count_in_scope": scoped_linked_count,
        })

    return {
        "positions": enriched_rows,
        "option_moves_by_position_id": option_moves_by_position_id,
        "assignment_stock_by_position_id": assignment_stock_by_position_id,
        "accounts_by_position_id": accounts_by_position_id,
        "scoped_option_moves_by_position_id": scoped_option_moves_by_position_id,
        "scoped_assignment_stock_by_position_id": scoped_assignment_stock_by_position_id,
        "scoped_accounts_by_position_id": scoped_accounts_by_position_id,
    }
