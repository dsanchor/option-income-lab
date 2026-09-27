"""Pure, event-grained dividends economics aggregation."""

from __future__ import annotations

import itertools
from collections import defaultdict
from collections.abc import Iterable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from statistics import median
from typing import Any

from .portfolio.rights_policy import contains_legacy_rights_data

_ZERO = Decimal(0)
_TWOPLACES = Decimal("0.01")
_SCRIP_EVENT_TYPES = {"SCRIP_DIVIDEND", "DIVIDEND_WITH_SCRIP"}
_VALUED_COST_BASIS_STATUSES = {"COMPLETE", "ZERO_COST"}
_MAX_UNVALUED_SCRIP_EVENTS = 100


def _decimal(value: Any) -> Decimal:
    parsed = _decimal_or_none(value)
    return parsed if parsed is not None else _ZERO


def _decimal_or_none(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _nonnegative_decimal(
    value: Any, *, omitted_is_zero: bool = False
) -> Decimal | None:
    if omitted_is_zero and value is None:
        return _ZERO
    parsed = _decimal_or_none(value)
    return parsed if parsed is not None and parsed >= _ZERO else None


def _round2(value: Decimal) -> float:
    return float(value.quantize(_TWOPLACES, rounding=ROUND_HALF_UP))


def _normalize_str_filter(
    values: Iterable[str] | str | None,
    *,
    uppercase: bool = False,
) -> set[str] | None:
    if values is None:
        return None
    items = [values] if isinstance(values, str) else list(values)
    normalized = {
        text.upper() if uppercase else text
        for item in items
        if (text := str(item).strip())
    }
    return normalized or None


def _normalize_int_filter(values: Iterable[int] | int | None) -> set[int] | None:
    if values is None:
        return None
    items = [values] if isinstance(values, int) else list(values)
    normalized: set[int] = set()
    for item in items:
        try:
            normalized.add(int(item))
        except (TypeError, ValueError):
            continue
    return normalized or None


def _parse_trade_date(
    value: Any,
) -> tuple[int | None, int | None, str | None, str | None]:
    if not isinstance(value, str) or not value.strip():
        return None, None, None, None
    raw = value.strip()
    try:
        trade_dt = date.fromisoformat(raw[:10])
    except ValueError:
        return None, None, None, raw
    return (
        trade_dt.year,
        trade_dt.month,
        f"{trade_dt.year:04d}-{trade_dt.month:02d}",
        raw,
    )


def _parse_iso_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _is_active_movement(movement: dict[str, Any]) -> bool:
    return (
        not contains_legacy_rights_data(movement)
        and movement.get("is_deleted") is not True
        and movement.get("deleted_at") is None
        and movement.get("correction_status") in (None, "", "ACTIVE")
    )


def _movement_identity(movement: dict[str, Any]) -> tuple[str, str, str]:
    security_id = str(movement.get("security_id") or "").strip()
    symbol = str(movement.get("ticker") or security_id.split(":")[-1]).strip().upper()
    return str(movement.get("account_id") or "").strip(), security_id, symbol


def _extract_cash_position(movement: dict[str, Any]) -> dict[str, Any] | None:
    if not _is_active_movement(movement) or movement.get("txn_type") != "DIVIDEND":
        return None
    if movement.get("ca_group_id") and movement.get("ca_leg_type") != "CASH_DIVIDEND":
        return None

    year, month, month_key, trade_date = _parse_trade_date(movement.get("trade_date"))
    if year is None or month is None or month_key is None or trade_date is None:
        return None

    gross = movement.get("gross") if isinstance(movement.get("gross"), dict) else {}
    fees = movement.get("fees") if isinstance(movement.get("fees"), dict) else {}
    withholding = movement.get("withholding")
    withholding = withholding if isinstance(withholding, dict) else {}
    withholding_source = withholding.get("source")
    withholding_source = (
        withholding_source if isinstance(withholding_source, dict) else {}
    )
    withholding_destination = withholding.get("destination")
    withholding_destination = (
        withholding_destination if isinstance(withholding_destination, dict) else {}
    )

    gross_amount = _decimal(gross.get("amount"))
    gross_eur = _decimal(gross.get("eur_amount"))
    fees_eur = _decimal(fees.get("total_eur"))
    withholding_source_eur = _decimal(withholding_source.get("amount_eur"))
    withholding_destination_eur = _decimal(withholding_destination.get("amount_eur"))
    withholding_total_eur = withholding_source_eur + withholding_destination_eur
    cash_net_eur = _decimal(
        (movement.get("net") if isinstance(movement.get("net"), dict) else {}).get(
            "eur_amount"
        )
    )
    account_id, security_id, symbol = _movement_identity(movement)

    return {
        "id": movement.get("id"),
        "event_key": str(
            movement.get("ca_group_id") or movement.get("id") or ""
        ).strip(),
        "ca_group_id": movement.get("ca_group_id"),
        "account_id": account_id,
        "security_id": security_id or None,
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
        "total_net": _round2(cash_net_eur),
        "correction_status": movement.get("correction_status") or "ACTIVE",
        "_year": year,
        "_month": month,
        "_month_key": month_key,
        "_gross_eur": gross_eur,
        "_fees_eur": fees_eur,
        "_withholding_source_eur": withholding_source_eur,
        "_withholding_destination_eur": withholding_destination_eur,
        "_withholding_total_eur": withholding_total_eur,
        "_cash_net_eur": cash_net_eur,
        "_currency": str(gross.get("currency") or "").strip().upper(),
    }


# Retained for callers/tests that exercise the former private helper indirectly.
_extract_dividend_position = _extract_cash_position


def _deduplicate_active_movements(movements: list[dict]) -> list[dict[str, Any]]:
    deduplicated: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for movement in movements:
        if not isinstance(movement, dict) or not _is_active_movement(movement):
            continue
        movement_id = str(movement.get("id") or "").strip()
        if movement_id:
            if movement_id in seen_ids:
                continue
            seen_ids.add(movement_id)
        deduplicated.append(movement)
    return deduplicated


def _build_events(movements: list[dict]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    standalone: list[dict[str, Any]] = []
    for movement in _deduplicate_active_movements(movements):
        group_id = str(movement.get("ca_group_id") or "").strip()
        if group_id:
            grouped[group_id].append(movement)
        else:
            cash = _extract_cash_position(movement)
            if cash is not None:
                standalone.append(_event_from_legs(cash["event_key"], [movement]))

    events = standalone
    events.extend(
        _event_from_legs(group_id, legs) for group_id, legs in grouped.items()
    )
    return [
        event
        for event in events
        if event["cash_positions"] or event["scrip_events_total"]
    ]


def _event_from_legs(event_key: str, legs: list[dict[str, Any]]) -> dict[str, Any]:
    cash_positions = [
        cash
        for movement in legs
        if (cash := _extract_cash_position(movement)) is not None
    ]
    share_legs = [
        movement
        for movement in legs
        if movement.get("txn_type") == "BUY"
        and movement.get("ca_leg_type") == "SHARE_ACQUISITION"
        and movement.get("ca_event_type") in _SCRIP_EVENT_TYPES
        and str(movement.get("ca_group_id") or "").strip()
    ]
    scrip_eligible = bool(share_legs)
    top_up_legs = (
        [movement for movement in legs if movement.get("ca_leg_type") == "CASH_TOP_UP"]
        if scrip_eligible
        else []
    )
    raw_relevant = [
        movement
        for movement in legs
        if movement.get("ca_leg_type")
        in {"CASH_DIVIDEND", "SHARE_ACQUISITION", "CASH_TOP_UP"}
    ]
    identity_source = raw_relevant if raw_relevant else legs
    identities = {_movement_identity(movement) for movement in identity_source}
    identity_valid = len(identities) == 1
    account_id, security_id, symbol = (
        next(iter(identities))
        if len(identities) == 1
        else _movement_identity(share_legs[0])
        if share_legs
        else _movement_identity(legs[0])
    )

    share_dates = {
        parsed[3]
        for movement in share_legs
        if (parsed := _parse_trade_date(movement.get("trade_date")))[0] is not None
    }
    share_date_valid = bool(share_legs) and len(share_dates) == 1
    event_trade_date = (
        next(iter(share_dates))
        if share_date_valid
        else cash_positions[0]["trade_date"]
        if cash_positions
        else None
    )
    year, month, month_key, parsed_trade_date = _parse_trade_date(event_trade_date)

    share_fmv_total = _ZERO
    personal_contribution_total = _ZERO
    attributable_fees_total = _ZERO
    scrip_valued = scrip_eligible and identity_valid and share_date_valid
    scrip_currencies: set[str] = set()
    unvalued_reasons: set[str] = set()
    if scrip_eligible and not identity_valid:
        unvalued_reasons.add("INVALID_EVENT_IDENTITY")
    if scrip_eligible and not share_date_valid:
        unvalued_reasons.add("INVALID_EVENT_DATE")

    for movement in share_legs:
        quantity = _decimal_or_none(movement.get("quantity"))
        share_fmv = movement.get("share_fmv")
        share_fmv = share_fmv if isinstance(share_fmv, dict) else {}
        fmv_eur = _nonnegative_decimal(share_fmv.get("eur_amount"))
        gross = movement.get("gross")
        gross = gross if isinstance(gross, dict) else {}
        contribution = _nonnegative_decimal(gross.get("eur_amount"))
        fees = movement.get("fees")
        fees = fees if isinstance(fees, dict) else {}
        fee = _nonnegative_decimal(fees.get("total_eur"), omitted_is_zero=True)
        currency = str(share_fmv.get("currency") or "").strip().upper()
        if currency:
            scrip_currencies.add(currency)
        if quantity is None or quantity <= _ZERO:
            unvalued_reasons.add("MISSING_OR_INVALID_QUANTITY")
        if movement.get("cost_basis_status") not in _VALUED_COST_BASIS_STATUSES:
            unvalued_reasons.add("COST_BASIS_INCOMPLETE")
        if fmv_eur is None:
            unvalued_reasons.add("MISSING_OR_INVALID_SHARE_FMV_EUR")
        if contribution is None:
            unvalued_reasons.add("MISSING_CONTRIBUTION_EUR")
        if fee is None:
            unvalued_reasons.add("INVALID_SHARE_FEES_EUR")
        if any(
            (
                quantity is None or quantity <= _ZERO,
                movement.get("cost_basis_status") not in _VALUED_COST_BASIS_STATUSES,
                fmv_eur is None,
                contribution is None,
                fee is None,
            )
        ):
            scrip_valued = False
            continue
        share_fmv_total += fmv_eur
        personal_contribution_total += contribution
        attributable_fees_total += fee

    for movement in top_up_legs:
        gross = movement.get("gross")
        gross = gross if isinstance(gross, dict) else {}
        contribution = _nonnegative_decimal(gross.get("eur_amount"))
        fees = movement.get("fees")
        fees = fees if isinstance(fees, dict) else {}
        fee = _nonnegative_decimal(fees.get("total_eur"), omitted_is_zero=True)
        if contribution is None:
            unvalued_reasons.add("INVALID_TOP_UP_CONTRIBUTION_EUR")
        if fee is None:
            unvalued_reasons.add("INVALID_TOP_UP_FEES_EUR")
        if contribution is None or fee is None:
            scrip_valued = False
            continue
        personal_contribution_total += contribution
        attributable_fees_total += fee

    scrip_value = (
        share_fmv_total - personal_contribution_total - attributable_fees_total
        if scrip_valued
        else None
    )
    cash_net = sum((position["_cash_net_eur"] for position in cash_positions), _ZERO)
    cash_currencies = {
        position["_currency"] for position in cash_positions if position["_currency"]
    }
    return {
        "event_key": event_key,
        "cash_positions": cash_positions,
        "account_id": account_id,
        "security_id": security_id,
        "symbol": symbol,
        "trade_date": parsed_trade_date,
        "_year": year,
        "_month": month,
        "_month_key": month_key,
        "_cash_net_eur": cash_net,
        "_scrip_value_eur": scrip_value,
        "_scrip_fmv_eur": share_fmv_total if scrip_valued else None,
        "_scrip_personal_contribution_eur": (
            personal_contribution_total if scrip_valued else None
        ),
        "_scrip_attributable_fees_eur": attributable_fees_total
        if scrip_valued
        else None,
        "_cash_currencies": cash_currencies,
        "_scrip_currencies": scrip_currencies,
        "scrip_events_total": 1 if scrip_eligible else 0,
        "scrip_events_valued": 1 if scrip_valued else 0,
        "scrip_events_unvalued": 1 if scrip_eligible and not scrip_valued else 0,
        "_unvalued_scrip_diagnostic": (
            {
                "event_id": event_key,
                "movement_ids": sorted(
                    str(movement.get("id") or "").strip()
                    for movement in raw_relevant
                    if str(movement.get("id") or "").strip()
                ),
                "share_leg_ids": sorted(
                    str(movement.get("id") or "").strip()
                    for movement in share_legs
                    if str(movement.get("id") or "").strip()
                ),
                "top_up_movement_ids": sorted(
                    str(movement.get("id") or "").strip()
                    for movement in top_up_legs
                    if str(movement.get("id") or "").strip()
                ),
                "account_id": account_id or None,
                "security_id": security_id or None,
                "symbol": symbol or None,
                "trade_date": parsed_trade_date,
                "reason_codes": sorted(unvalued_reasons),
            }
            if scrip_eligible and not scrip_valued
            else None
        ),
        "_identity_valid": identity_valid,
        "_has_event_date": year is not None,
    }


def infer_dividend_frequency(event_dates: list[str]) -> int | None:
    """Infer annual payment count from the median gap between dividend dates."""
    parsed_dates = [
        parsed for raw in event_dates if (parsed := _parse_iso_date(raw)) is not None
    ]
    if len(parsed_dates) < 2:
        return None
    parsed_dates.sort()
    gaps = [
        (current - previous).days
        for previous, current in itertools.pairwise(parsed_dates)
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
    movements: list[dict],
    *,
    symbol_filter: list[str] | None = None,
    account_filter: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build cash-only per-symbol trailing-dividend inputs for Yield on Cost."""
    normalized_symbols = _normalize_str_filter(symbol_filter, uppercase=True)
    normalized_accounts = _normalize_str_filter(account_filter)
    per_symbol_event_totals: dict[str, dict[str, Decimal]] = defaultdict(dict)

    for movement in _deduplicate_active_movements(movements):
        position = _extract_cash_position(movement)
        if position is None:
            continue
        if (
            normalized_symbols is not None
            and position["symbol"] not in normalized_symbols
        ):
            continue
        if (
            normalized_accounts is not None
            and position["account_id"] not in normalized_accounts
        ):
            continue
        symbol_events = per_symbol_event_totals[position["symbol"]]
        trade_date = position["trade_date"]
        symbol_events[trade_date] = (
            symbol_events.get(trade_date, _ZERO) + position["_cash_net_eur"]
        )

    snapshot: dict[str, dict[str, Any]] = {}
    for symbol, event_totals in per_symbol_event_totals.items():
        sorted_events = sorted(event_totals.items(), key=lambda item: item[0])
        event_dates = [event_date for event_date, _ in sorted_events]
        frequency = infer_dividend_frequency(event_dates)
        if (
            len(sorted_events) < 2
            or frequency is None
            or len(sorted_events) < frequency
        ):
            snapshot[symbol] = {
                "event_dates": event_dates,
                "event_net_eur": [_round2(amount) for _, amount in sorted_events],
                "yoc_basis": "insufficient_history",
                "yoc_dividend_frequency": frequency,
                "yoc_trailing_annual_dividend_net_eur": None,
            }
            continue
        trailing = sum((amount for _, amount in sorted_events[-frequency:]), _ZERO)
        snapshot[symbol] = {
            "event_dates": event_dates,
            "event_net_eur": [_round2(amount) for _, amount in sorted_events],
            "yoc_basis": "annualized",
            "yoc_dividend_frequency": frequency,
            "yoc_trailing_annual_dividend_net_eur": _round2(trailing),
        }
    return snapshot


def _coverage(events: list[dict[str, Any]]) -> dict[str, Any]:
    total = sum(event["scrip_events_total"] for event in events)
    valued = sum(event["scrip_events_valued"] for event in events)
    unvalued = total - valued
    if total == 0:
        status = "NOT_APPLICABLE"
    elif valued == total:
        status = "COMPLETE"
    elif valued == 0:
        status = "UNAVAILABLE"
    else:
        status = "PARTIAL"
    known_value = sum(
        (
            event["_scrip_value_eur"]
            for event in events
            if event["_scrip_value_eur"] is not None
        ),
        _ZERO,
    )
    return {
        "scrip_dividends_eur": (
            _round2(_ZERO) if total == 0 else _round2(known_value) if valued else None
        ),
        "total_dividends_is_partial": status in {"PARTIAL", "UNAVAILABLE"},
        "scrip_valuation_status": status,
        "scrip_events_total": total,
        "scrip_events_valued": valued,
        "scrip_events_unvalued": unvalued,
        "_known_scrip_eur": known_value,
    }


def _aggregate(
    events: list[dict[str, Any]], cash_positions: list[dict[str, Any]]
) -> dict[str, Any]:
    total_gross = sum((position["_gross_eur"] for position in cash_positions), _ZERO)
    total_fees = sum((position["_fees_eur"] for position in cash_positions), _ZERO)
    source_withholding = sum(
        (position["_withholding_source_eur"] for position in cash_positions), _ZERO
    )
    destination_withholding = sum(
        (position["_withholding_destination_eur"] for position in cash_positions), _ZERO
    )
    total_withholding = source_withholding + destination_withholding
    cash_net = sum((event["_cash_net_eur"] for event in events), _ZERO)
    coverage = _coverage(events)
    total_dividends = cash_net + coverage["_known_scrip_eur"]
    return {
        "_gross_eur": total_gross,
        "_fees_eur": total_fees,
        "_withholding_source_eur": source_withholding,
        "_withholding_destination_eur": destination_withholding,
        "_withholding_total_eur": total_withholding,
        "_cash_net_eur": cash_net,
        "_total_dividends_eur": total_dividends,
        **coverage,
    }


def _coverage_components(events: list[dict[str, Any]], field: str) -> float | None:
    coverage = _coverage(events)
    if coverage["scrip_events_total"] == 0:
        return 0.0
    values = [event[field] for event in events if event[field] is not None]
    return _round2(sum(values, _ZERO)) if values else None


def _row_coverage_fields(aggregate: dict[str, Any]) -> dict[str, Any]:
    return {
        "scrip_dividends_eur": aggregate["scrip_dividends_eur"],
        "total_dividends_eur": _round2(aggregate["_total_dividends_eur"]),
        "total_dividends_is_partial": aggregate["total_dividends_is_partial"],
        "scrip_valuation_status": aggregate["scrip_valuation_status"],
        "scrip_events_total": aggregate["scrip_events_total"],
        "scrip_events_valued": aggregate["scrip_events_valued"],
        "scrip_events_unvalued": aggregate["scrip_events_unvalued"],
    }


def _summarize_dividends(events: list[dict[str, Any]]) -> dict[str, Any]:
    cash_positions = [
        position for event in events for position in event["cash_positions"]
    ]
    aggregate = _aggregate(events, cash_positions)
    effective_withholding_pct = (
        (aggregate["_withholding_total_eur"] / aggregate["_gross_eur"]) * Decimal(100)
        if aggregate["_gross_eur"] != _ZERO
        else _ZERO
    )
    represented_accounts = {
        event["account_id"]
        for event in events
        if event["_identity_valid"] and event["account_id"]
    }
    return {
        "total_gross_eur": _round2(aggregate["_gross_eur"]),
        "total_fees_eur": _round2(aggregate["_fees_eur"]),
        "total_withholding_eur": _round2(aggregate["_withholding_total_eur"]),
        "total_net_eur": _round2(aggregate["_cash_net_eur"]),
        "cash_net": _round2(aggregate["_cash_net_eur"]),
        "total_net": _round2(aggregate["_total_dividends_eur"]),
        "effective_withholding_pct": _round2(effective_withholding_pct),
        "total_dividends": len(events),
        "total_accounts": len(represented_accounts),
        "scrip_fmv_eur": _coverage_components(events, "_scrip_fmv_eur"),
        "scrip_personal_contribution_eur": _coverage_components(
            events, "_scrip_personal_contribution_eur"
        ),
        "scrip_attributable_fees_eur": _coverage_components(
            events, "_scrip_attributable_fees_eur"
        ),
        **_row_coverage_fields(aggregate),
    }


def build_dividends_economics_report(
    movements: list[dict],
    year: int | None = None,
    month_filter: list[int] | None = None,
    symbol_filter: list[str] | None = None,
    account_filter: list[str] | None = None,
    currency_filter: list[str] | None = None,
) -> dict:
    """Build cash fiscal and combined cash+scrip dividend economics."""
    normalized_months = _normalize_int_filter(month_filter)
    normalized_symbols = _normalize_str_filter(symbol_filter, uppercase=True)
    normalized_accounts = _normalize_str_filter(account_filter)
    normalized_currencies = _normalize_str_filter(currency_filter, uppercase=True)

    all_events = _build_events(movements)
    available_years = {
        event["_year"] for event in all_events if event["_year"] is not None
    }
    available_symbols = {
        event["symbol"]
        for event in all_events
        if event["_identity_valid"] and event["symbol"]
    }
    available_account_ids = {
        event["account_id"]
        for event in all_events
        if event["_identity_valid"] and event["account_id"]
    }
    available_currencies = {
        currency
        for event in all_events
        for currency in event["_cash_currencies"] | event["_scrip_currencies"]
    }

    def matches_scope(event: dict[str, Any], *, include_date: bool) -> bool:
        if normalized_symbols is not None and (
            not event["_identity_valid"] or event["symbol"] not in normalized_symbols
        ):
            return False
        if normalized_accounts is not None and (
            not event["_identity_valid"]
            or event["account_id"] not in normalized_accounts
        ):
            return False
        if normalized_currencies is not None and not (
            (event["_cash_currencies"] | event["_scrip_currencies"])
            & normalized_currencies
        ):
            return False
        if not include_date:
            return True
        if year is None and normalized_months is None:
            return True
        return event["_has_event_date"] and (
            (year is None or event["_year"] == year)
            and (normalized_months is None or event["_month"] in normalized_months)
        )

    filtered_events = [
        event for event in all_events if matches_scope(event, include_date=True)
    ]
    all_years_events = [
        event for event in all_events if matches_scope(event, include_date=False)
    ]

    monthly_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    symbol_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    yearly_groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    cumulative_month_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in filtered_events:
        if event["_month_key"] is not None:
            monthly_groups[event["_month_key"]].append(event)
        symbol_groups[event["symbol"]].append(event)
    for event in all_years_events:
        if event["_year"] is not None and event["_month_key"] is not None:
            yearly_groups[event["_year"]].append(event)
            cumulative_month_groups[event["_month_key"]].append(event)

    def cash_positions(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [position for event in events for position in event["cash_positions"]]

    monthly = []
    for month_key in sorted(monthly_groups):
        events = monthly_groups[month_key]
        aggregate = _aggregate(events, cash_positions(events))
        monthly.append(
            {
                "month": month_key,
                "gross_eur": _round2(aggregate["_gross_eur"]),
                "fees_eur": _round2(aggregate["_fees_eur"]),
                "withholding_source_eur": _round2(aggregate["_withholding_source_eur"]),
                "withholding_destination_eur": _round2(
                    aggregate["_withholding_destination_eur"]
                ),
                "withholding_total_eur": _round2(aggregate["_withholding_total_eur"]),
                "net_eur": _round2(aggregate["_cash_net_eur"]),
                "cash_net": _round2(aggregate["_cash_net_eur"]),
                "total_net": _round2(aggregate["_total_dividends_eur"]),
                "dividend_count": len(events),
                **_row_coverage_fields(aggregate),
            }
        )

    by_symbol = []
    for symbol in sorted(symbol_groups):
        events = symbol_groups[symbol]
        aggregate = _aggregate(events, cash_positions(events))
        by_symbol.append(
            {
                "symbol": symbol,
                "gross_eur": _round2(aggregate["_gross_eur"]),
                "withholding_total_eur": _round2(aggregate["_withholding_total_eur"]),
                "net_eur": _round2(aggregate["_cash_net_eur"]),
                "cash_net": _round2(aggregate["_cash_net_eur"]),
                "total_net": _round2(aggregate["_total_dividends_eur"]),
                "dividend_count": len(events),
                **_row_coverage_fields(aggregate),
            }
        )

    yearly = []
    for grouped_year in sorted(yearly_groups):
        events = yearly_groups[grouped_year]
        aggregate = _aggregate(events, cash_positions(events))
        yearly.append(
            {
                "year": grouped_year,
                "gross_eur": _round2(aggregate["_gross_eur"]),
                "withholding_eur": _round2(aggregate["_withholding_total_eur"]),
                "net_eur": _round2(aggregate["_cash_net_eur"]),
                "cash_net": _round2(aggregate["_cash_net_eur"]),
                "total_net": _round2(aggregate["_total_dividends_eur"]),
                "dividend_count": len(events),
                **_row_coverage_fields(aggregate),
            }
        )

    cumulative = []
    running_events: list[dict[str, Any]] = []
    for month_key in sorted(cumulative_month_groups):
        running_events.extend(cumulative_month_groups[month_key])
        aggregate = _aggregate(running_events, cash_positions(running_events))
        cumulative.append(
            {
                "month": month_key,
                "cumulative_net_eur": _round2(aggregate["_cash_net_eur"]),
                "cumulative_cash_net_eur": _round2(aggregate["_cash_net_eur"]),
                "cumulative_total_net_eur": _round2(aggregate["_total_dividends_eur"]),
                "cash_net": _round2(aggregate["_cash_net_eur"]),
                "total_net": _round2(aggregate["_total_dividends_eur"]),
                **_row_coverage_fields(aggregate),
            }
        )

    positions = sorted(
        [
            {key: value for key, value in position.items() if not key.startswith("_")}
            for event in filtered_events
            for position in event["cash_positions"]
        ],
        key=lambda position: (
            position.get("trade_date") or "",
            position.get("id") or "",
        ),
        reverse=True,
    )
    all_unvalued_scrip_events = sorted(
        (
            event["_unvalued_scrip_diagnostic"]
            for event in filtered_events
            if event["_unvalued_scrip_diagnostic"] is not None
        ),
        key=lambda item: (
            item.get("trade_date") or "",
            item.get("event_id") or "",
        ),
    )
    unvalued_scrip_events = all_unvalued_scrip_events[:_MAX_UNVALUED_SCRIP_EVENTS]
    return {
        "summary": _summarize_dividends(filtered_events),
        "monthly": monthly,
        "by_symbol": by_symbol,
        "yearly": yearly,
        "cumulative": cumulative,
        "positions": positions,
        "unvalued_scrip_events": unvalued_scrip_events,
        "filters": {
            "years": sorted(available_years, reverse=True),
            "symbols": sorted(available_symbols),
            "account_ids": sorted(available_account_ids),
            "currencies": sorted(available_currencies),
        },
        "applied_filters": {
            "year": year,
            "months": sorted(normalized_months)
            if normalized_months is not None
            else None,
            "symbols": sorted(normalized_symbols)
            if normalized_symbols is not None
            else None,
            "account_ids": sorted(normalized_accounts)
            if normalized_accounts is not None
            else None,
            "currencies": (
                sorted(normalized_currencies)
                if normalized_currencies is not None
                else None
            ),
        },
        "meta": {
            "bucket_field": "trade_date",
            "value_field": (
                "DIVIDEND.net.eur_amount + SHARE_ACQUISITION.share_fmv.eur_amount "
                "- SHARE_ACQUISITION.gross.eur_amount - CASH_TOP_UP.gross.eur_amount "
                "- attributable fees"
            ),
            "event_granularity": "ca_group_id_or_movement_id",
            "yearly_cumulative_scope": ("all_years_symbol_account_currency_filtered"),
            "unvalued_scrip_events_total": len(all_unvalued_scrip_events),
            "unvalued_scrip_events_limit": _MAX_UNVALUED_SCRIP_EVENTS,
            "unvalued_scrip_events_truncated": (
                len(all_unvalued_scrip_events) > len(unvalued_scrip_events)
            ),
        },
    }
