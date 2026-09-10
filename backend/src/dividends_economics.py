"""Pure dividends economics aggregation.

Accepts mixed ledger movement documents and produces the dividends-only
economics report shape consumed by the web layer.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from statistics import median
from typing import Any, Dict, Iterable, List, Optional

_ZERO = Decimal("0")
_TWOPLACES = Decimal("0.01")


def _decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or value is None:
        return _ZERO
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return _ZERO


def _round2(value: Decimal) -> float:
    return float(value.quantize(_TWOPLACES, rounding=ROUND_HALF_UP))


def _normalize_str_filter(
    values: Optional[Iterable[str] | str],
    *,
    uppercase: bool = False,
) -> Optional[set[str]]:
    if values is None:
        return None
    if isinstance(values, str):
        items = [values]
    else:
        items = list(values)
    normalized: set[str] = set()
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        normalized.add(text.upper() if uppercase else text)
    return normalized or None


def _normalize_int_filter(values: Optional[Iterable[int] | int]) -> Optional[set[int]]:
    if values is None:
        return None
    if isinstance(values, int):
        items = [values]
    else:
        items = list(values)
    normalized: set[int] = set()
    for item in items:
        try:
            normalized.add(int(item))
        except (TypeError, ValueError):
            continue
    return normalized or None


def _parse_trade_date(value: Any) -> tuple[Optional[int], Optional[int], Optional[str], Optional[str]]:
    if not isinstance(value, str) or not value.strip():
        return None, None, None, None
    raw = value.strip()
    try:
        trade_dt = datetime.strptime(raw[:10], "%Y-%m-%d")
    except ValueError:
        return None, None, None, raw
    month_key = f"{trade_dt.year:04d}-{trade_dt.month:02d}"
    return trade_dt.year, trade_dt.month, month_key, raw


def _parse_iso_date(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _extract_dividend_position(movement: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if movement.get("txn_type") != "DIVIDEND":
        return None

    correction_status = movement.get("correction_status")
    if correction_status not in (None, "", "ACTIVE"):
        return None

    year, month, month_key, trade_date = _parse_trade_date(movement.get("trade_date"))
    if year is None or month is None or month_key is None or trade_date is None:
        return None

    gross = movement.get("gross") or {}
    fees = movement.get("fees") or {}
    withholding = movement.get("withholding")
    withholding = withholding if isinstance(withholding, dict) else {}
    withholding_source = withholding.get("source") or {}
    withholding_destination = withholding.get("destination") or {}

    gross_amount = _decimal(gross.get("amount", "0"))
    gross_eur = _decimal(gross.get("eur_amount", "0"))
    fees_eur = _decimal(fees.get("total_eur", "0"))
    withholding_source_eur = _decimal(withholding_source.get("amount_eur", "0"))
    withholding_destination_eur = _decimal(withholding_destination.get("amount_eur", "0"))
    withholding_total_eur = withholding_source_eur + withholding_destination_eur
    cash_net_eur = _decimal((movement.get("net") or {}).get("eur_amount", "0"))
    derechos_eur = _decimal(movement.get("source_derechos_amount") or "0")
    total_net_eur = cash_net_eur + derechos_eur

    symbol = str(
        movement.get("ticker")
        or str(movement.get("security_id") or "").split(":")[-1]
    ).strip().upper()
    account_id = str(movement.get("account_id") or "").strip()

    return {
        "id": movement.get("id"),
        "account_id": account_id,
        "security_id": movement.get("security_id"),
        "symbol": symbol,
        "trade_date": trade_date,
        "gross_amount": _round2(gross_amount),
        "gross_currency": str(gross.get("currency") or "").strip().upper(),
        "gross_eur": _round2(gross_eur),
        "fees_eur": _round2(fees_eur),
        "withholding_source_eur": _round2(withholding_source_eur),
        "withholding_destination_eur": _round2(withholding_destination_eur),
        "withholding_total_eur": _round2(withholding_total_eur),
        "net_eur": _round2(cash_net_eur),
        "cash_net": _round2(cash_net_eur),
        "derechos_eur": _round2(derechos_eur),
        "derechos_net": _round2(derechos_eur),
        "total_net": _round2(total_net_eur),
        "correction_status": correction_status or "ACTIVE",
        "_year": year,
        "_month": month,
        "_month_key": month_key,
        "_gross_eur": gross_eur,
        "_fees_eur": fees_eur,
        "_withholding_source_eur": withholding_source_eur,
        "_withholding_destination_eur": withholding_destination_eur,
        "_withholding_total_eur": withholding_total_eur,
        "_net_eur": cash_net_eur,
        "_cash_net_eur": cash_net_eur,
        "_derechos_eur": derechos_eur,
        "_total_net_eur": total_net_eur,
    }


def infer_dividend_frequency(event_dates: List[str]) -> Optional[int]:
    """Infer annual payment count from the median gap between dividend dates.

    Thresholds use midpoints between common cadences so occasional special or
    shifted payments do not overreact:
      - < 60 days   → monthly   (12)
      - < 135 days  → quarterly (4)
      - < 270 days  → semiannual (2)
      - otherwise   → annual    (1)
    """
    parsed_dates = [parsed for raw in event_dates if (parsed := _parse_iso_date(raw)) is not None]
    if len(parsed_dates) < 2:
        return None

    parsed_dates.sort()
    gaps = [
        (current - previous).days
        for previous, current in zip(parsed_dates, parsed_dates[1:])
    ]
    if not gaps:
        return None

    median_gap = median(gaps)
    if median_gap < 60:
        return 12
    if median_gap < 135:
        return 4
    if median_gap < 270:
        return 2
    return 1


def build_dividend_yoc_snapshot(
    movements: List[dict],
    *,
    symbol_filter: List[str] | None = None,
    account_filter: List[str] | None = None,
) -> Dict[str, Dict[str, Any]]:
    """Build per-symbol trailing-dividend inputs used for Yield on Cost.

    This intentionally ignores year/month filters. YoC is derived from the
    full dividend history available for the selected symbol/account scope,
    then later joined to current holdings cost basis.
    """
    normalized_symbols = _normalize_str_filter(symbol_filter, uppercase=True)
    normalized_accounts = _normalize_str_filter(account_filter)
    per_symbol_event_totals: Dict[str, Dict[str, Decimal]] = defaultdict(dict)

    for movement in movements:
        position = _extract_dividend_position(movement)
        if position is None:
            continue
        if normalized_symbols is not None and position["symbol"] not in normalized_symbols:
            continue
        if normalized_accounts is not None and position["account_id"] not in normalized_accounts:
            continue

        symbol_events = per_symbol_event_totals[position["symbol"]]
        trade_date = position["trade_date"]
        symbol_events[trade_date] = symbol_events.get(trade_date, _ZERO) + position["_total_net_eur"]

    snapshot: Dict[str, Dict[str, Any]] = {}
    for symbol, event_totals in per_symbol_event_totals.items():
        sorted_events = sorted(event_totals.items(), key=lambda item: item[0])
        event_dates = [event_date for event_date, _ in sorted_events]

        if len(sorted_events) < 2:
            snapshot[symbol] = {
                "event_dates": event_dates,
                "event_net_eur": [_round2(amount) for _, amount in sorted_events],
                "yoc_basis": "insufficient_history",
                "yoc_dividend_frequency": None,
                "yoc_trailing_annual_dividend_net_eur": None,
            }
            continue

        frequency = infer_dividend_frequency(event_dates)
        if frequency is None or len(sorted_events) < frequency:
            # We intentionally return no annualized YoC input here. Even if a
            # cadence can be guessed from 2+ events, fewer than N observed
            # payments would understate a "trailing annual" sum.
            snapshot[symbol] = {
                "event_dates": event_dates,
                "event_net_eur": [_round2(amount) for _, amount in sorted_events],
                "yoc_basis": "insufficient_history",
                "yoc_dividend_frequency": frequency,
                "yoc_trailing_annual_dividend_net_eur": None,
            }
            continue

        trailing_annual_dividend_net = sum(
            (amount for _, amount in sorted_events[-frequency:]),
            _ZERO,
        )
        snapshot[symbol] = {
            "event_dates": event_dates,
            "event_net_eur": [_round2(amount) for _, amount in sorted_events],
            "yoc_basis": "annualized",
            "yoc_dividend_frequency": frequency,
            "yoc_trailing_annual_dividend_net_eur": _round2(trailing_annual_dividend_net),
        }

    return snapshot


def _summarize_dividends(positions: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_gross = sum((p["_gross_eur"] for p in positions), _ZERO)
    total_fees = sum((p["_fees_eur"] for p in positions), _ZERO)
    total_withholding = sum((p["_withholding_total_eur"] for p in positions), _ZERO)
    total_cash_net = sum((p["_cash_net_eur"] for p in positions), _ZERO)
    total_derechos = sum((p["_derechos_eur"] for p in positions), _ZERO)
    total_combined_net = sum((p["_total_net_eur"] for p in positions), _ZERO)
    effective_withholding_pct = _ZERO
    if total_gross != _ZERO:
        effective_withholding_pct = (total_withholding / total_gross) * Decimal("100")

    represented_accounts = {
        p["account_id"] for p in positions if str(p.get("account_id") or "").strip()
    }
    return {
        "total_gross_eur": _round2(total_gross),
        "total_fees_eur": _round2(total_fees),
        "total_withholding_eur": _round2(total_withholding),
        "total_net_eur": _round2(total_cash_net),
        "cash_net": _round2(total_cash_net),
        "derechos_net": _round2(total_derechos),
        "total_net": _round2(total_combined_net),
        "effective_withholding_pct": _round2(effective_withholding_pct),
        "total_dividends": len(positions),
        "total_accounts": len(represented_accounts),
    }


def build_dividends_economics_report(
    movements: List[dict],
    year: int | None = None,
    month_filter: List[int] | None = None,
    symbol_filter: List[str] | None = None,
    account_filter: List[str] | None = None,
) -> dict:
    """Build the accepted dividends economics response shape.

    `movements` is the mixed ledger_txn list returned by
    ``CosmosPortfolioService.get_all_movements_for_holdings()``; this function
    filters to active dividends internally so callers can stay simple.
    """
    normalized_months = _normalize_int_filter(month_filter)
    normalized_symbols = _normalize_str_filter(symbol_filter, uppercase=True)
    normalized_accounts = _normalize_str_filter(account_filter)

    all_positions: List[Dict[str, Any]] = []
    available_years: set[int] = set()
    available_symbols: set[str] = set()
    available_account_ids: set[str] = set()

    for movement in movements:
        position = _extract_dividend_position(movement)
        if position is None:
            continue
        all_positions.append(position)
        available_years.add(position["_year"])
        if position["symbol"]:
            available_symbols.add(position["symbol"])
        if position["account_id"]:
            available_account_ids.add(position["account_id"])

    filtered_positions = [
        position for position in all_positions
        if (year is None or position["_year"] == year)
        and (normalized_months is None or position["_month"] in normalized_months)
        and (normalized_symbols is None or position["symbol"] in normalized_symbols)
        and (normalized_accounts is None or position["account_id"] in normalized_accounts)
    ]

    all_years_scope_positions = [
        position for position in all_positions
        if (normalized_symbols is None or position["symbol"] in normalized_symbols)
        and (normalized_accounts is None or position["account_id"] in normalized_accounts)
    ]

    monthly_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    symbol_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    yearly_groups: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    cumulative_month_cash_net: Dict[str, Decimal] = defaultdict(lambda: _ZERO)
    cumulative_month_derechos: Dict[str, Decimal] = defaultdict(lambda: _ZERO)

    for position in filtered_positions:
        monthly_groups[position["_month_key"]].append(position)
        symbol_groups[position["symbol"]].append(position)

    for position in all_years_scope_positions:
        yearly_groups[position["_year"]].append(position)
        cumulative_month_cash_net[position["_month_key"]] += position["_cash_net_eur"]
        cumulative_month_derechos[position["_month_key"]] += position["_derechos_eur"]

    monthly = []
    for month_key in sorted(monthly_groups):
        group_positions = monthly_groups[month_key]
        gross_total = sum((p["_gross_eur"] for p in group_positions), _ZERO)
        fees_total = sum((p["_fees_eur"] for p in group_positions), _ZERO)
        withholding_source_total = sum((p["_withholding_source_eur"] for p in group_positions), _ZERO)
        withholding_destination_total = sum((p["_withholding_destination_eur"] for p in group_positions), _ZERO)
        withholding_total = withholding_source_total + withholding_destination_total
        cash_net_total = sum((p["_cash_net_eur"] for p in group_positions), _ZERO)
        derechos_total = sum((p["_derechos_eur"] for p in group_positions), _ZERO)
        total_net = sum((p["_total_net_eur"] for p in group_positions), _ZERO)
        monthly.append({
            "month": month_key,
            "gross_eur": _round2(gross_total),
            "fees_eur": _round2(fees_total),
            "withholding_source_eur": _round2(withholding_source_total),
            "withholding_destination_eur": _round2(withholding_destination_total),
            "withholding_total_eur": _round2(withholding_total),
            "net_eur": _round2(cash_net_total),
            "cash_net": _round2(cash_net_total),
            "derechos_net": _round2(derechos_total),
            "total_net": _round2(total_net),
            "dividend_count": len(group_positions),
        })

    by_symbol = []
    for grouped_symbol in sorted(symbol_groups):
        group_positions = symbol_groups[grouped_symbol]
        gross_total = sum((p["_gross_eur"] for p in group_positions), _ZERO)
        withholding_total = sum((p["_withholding_total_eur"] for p in group_positions), _ZERO)
        cash_net_total = sum((p["_cash_net_eur"] for p in group_positions), _ZERO)
        derechos_total = sum((p["_derechos_eur"] for p in group_positions), _ZERO)
        total_net = sum((p["_total_net_eur"] for p in group_positions), _ZERO)
        by_symbol.append({
            "symbol": grouped_symbol,
            "gross_eur": _round2(gross_total),
            "withholding_total_eur": _round2(withholding_total),
            "net_eur": _round2(cash_net_total),
            "cash_net": _round2(cash_net_total),
            "derechos_net": _round2(derechos_total),
            "total_net": _round2(total_net),
            "dividend_count": len(group_positions),
        })

    yearly = []
    for grouped_year in sorted(yearly_groups):
        group_positions = yearly_groups[grouped_year]
        gross_total = sum((p["_gross_eur"] for p in group_positions), _ZERO)
        withholding_total = sum((p["_withholding_total_eur"] for p in group_positions), _ZERO)
        cash_net_total = sum((p["_cash_net_eur"] for p in group_positions), _ZERO)
        derechos_total = sum((p["_derechos_eur"] for p in group_positions), _ZERO)
        total_net = sum((p["_total_net_eur"] for p in group_positions), _ZERO)
        yearly.append({
            "year": grouped_year,
            "gross_eur": _round2(gross_total),
            "withholding_eur": _round2(withholding_total),
            "net_eur": _round2(cash_net_total),
            "cash_net": _round2(cash_net_total),
            "derechos_net": _round2(derechos_total),
            "total_net": _round2(total_net),
            "dividend_count": len(group_positions),
        })

    cumulative = []
    running_cash_net = _ZERO
    running_derechos = _ZERO
    running_total = _ZERO
    for month_key in sorted(cumulative_month_cash_net):
        month_cash_net = cumulative_month_cash_net[month_key]
        month_derechos = cumulative_month_derechos[month_key]
        month_total_net = month_cash_net + month_derechos
        running_cash_net += month_cash_net
        running_derechos += month_derechos
        running_total += month_total_net
        cumulative.append({
            "month": month_key,
            "cumulative_net_eur": _round2(running_cash_net),
            "cumulative_cash_net_eur": _round2(running_cash_net),
            "cumulative_derechos_net_eur": _round2(running_derechos),
            "cumulative_total_net_eur": _round2(running_total),
            "cash_net": _round2(running_cash_net),
            "derechos_net": _round2(running_derechos),
            "total_net": _round2(running_total),
        })

    positions = sorted(
        [
            {key: value for key, value in position.items() if not key.startswith("_")}
            for position in filtered_positions
        ],
        key=lambda position: (
            position.get("trade_date") or "",
            position.get("id") or "",
        ),
        reverse=True,
    )

    return {
        "summary": _summarize_dividends(filtered_positions),
        "monthly": monthly,
        "by_symbol": by_symbol,
        "yearly": yearly,
        "cumulative": cumulative,
        "positions": positions,
        "filters": {
            "years": sorted(available_years, reverse=True),
            "symbols": sorted(available_symbols),
            "account_ids": sorted(available_account_ids),
        },
        "applied_filters": {
            "year": year,
            "months": sorted(normalized_months) if normalized_months is not None else None,
            "symbols": sorted(normalized_symbols) if normalized_symbols is not None else None,
            "account_ids": sorted(normalized_accounts) if normalized_accounts is not None else None,
        },
        "meta": {
            "bucket_field": "trade_date",
            "value_field": "net.eur_amount",
            "yearly_cumulative_scope": "all_years_symbol_account_filtered",
        },
    }
