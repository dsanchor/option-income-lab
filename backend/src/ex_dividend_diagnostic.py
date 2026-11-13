"""Read-only dividend-capture diagnostics, never inputs to trading policy.

The provider's FQ dividend is lastDividendValue (historical), not a confirmed
upcoming payment. Its FY dividend is annual and is deliberately never used.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .options_chain_filters import get_contract, parse_strike_decimal
from .options_chain_view import usable_quote


def _object(value) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def _positive(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) and value > 0 else None
    except OverflowError:
        return None


def _date(value) -> date | None:
    if isinstance(value, dict):
        return _date(value.get("value")) or _date(value.get("formatted"))
    if _positive(value) is not None:
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).date()
        except (ValueError, OverflowError, OSError):
            return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def build_ex_dividend_evidence(
    market_data: dict,
    source_chain: dict,
    *,
    strike,
    expiration,
    option_type: str,
    now: datetime,
    calendar_date=None,
    calendar_lookup_failed: bool = False,
    scope: str = "held",
) -> dict:
    """Use exact pre-filter contracts and a frozen US-session date basis.

    Unlike validation's wall-clock calendar extractor this is reproducible for
    the whole monitor cycle, including every proposed alternative.
    """
    today = now.astimezone(ZoneInfo("America/New_York")).date()
    dividends = _object(_object(market_data.get("dividends")).get("dividends"))
    provider_date = _date(dividends.get("ex_dividend_date_recent"))
    saved_date = _date(calendar_date)
    provider_upcoming = provider_date if provider_date and provider_date >= today else None
    saved_upcoming = saved_date if saved_date and saved_date >= today else None
    ex_date = provider_upcoming or saved_upcoming
    date_source = "yfinance" if provider_upcoming else "cosmos_calendar" if saved_upcoming else "unknown"
    conflict = bool(provider_upcoming and saved_upcoming and provider_upcoming != saved_upcoming)
    expiry = _date(expiration)
    try:
        strike_value = float(parse_strike_decimal(strike))
    except ValueError:
        strike_value = None
    # Agent views omit the chain's top-level underlying_price. Read that field
    # from the provider payload, never the model's underlying_price/moneyness.
    raw_chain = _object(market_data.get("options_chain"))
    spot = _positive(raw_chain.get("underlying_price"))
    spot_source = "options_chain.underlying_price" if spot is not None else "unknown"
    if spot is None:
        fundamentals = _object(_object(market_data.get("overview")).get("fundamentals"))
        spot = _positive(_object(fundamentals.get("current_price")).get("value"))
        if spot is not None:
            spot_source = "overview.fundamentals.current_price.value"
    contract = (
        get_contract(source_chain, strike_value, expiry.isoformat(), option_type)
        if strike_value is not None and expiry is not None else None
    ) or {}
    meta = _object(contract.get("_meta"))
    field_status = _object(meta.get("field_status"))
    bid, ask = usable_quote(contract, "bid"), usable_quote(contract, "ask")
    intrinsic = max(spot - strike_value, 0) if spot is not None and strike_value is not None else None
    raw_bid = bid - intrinsic if bid is not None and intrinsic is not None else None
    raw_ask = ask - intrinsic if ask is not None and intrinsic is not None else None
    crossed = bid is not None and ask is not None and bid > ask
    extrinsic_bid = raw_bid if raw_bid is not None and raw_bid >= 0 and not crossed else None
    extrinsic_ask = raw_ask if raw_ask is not None and raw_ask >= 0 and not crossed else None
    try:
        quote_asof = datetime.fromisoformat(str(meta.get("quote_asof")).replace("Z", "+00:00"))
        quote_time_known = quote_asof.tzinfo is not None
    except ValueError:
        quote_time_known = False
    fresh = (
        meta.get("stale") is False
        and meta.get("carried") is not True
        and quote_time_known
        and field_status.get("bid") == "live"
        and field_status.get("ask") == "live"
    )
    quote_sufficient = fresh and bid is not None and ask is not None and extrinsic_bid is not None and extrinsic_ask is not None
    historical_dividend = _positive(_object(dividends.get("dps_common_stock_prim_issue_fq")).get("value"))
    spans = ex_date <= expiry if ex_date and expiry and expiry >= today else None
    itm = spot > strike_value if spot is not None and strike_value is not None else None
    reasons = ["Confirmed upcoming dividend amount unavailable; no quantitative safety pass."]
    if ex_date is None:
        reasons.append("Upcoming ex-dividend date unknown; a missing/past date does not mean no future event.")
    if date_source == "cosmos_calendar":
        reasons.append("Saved calendar fallback: provider ex-date missing, invalid or past.")
    if conflict:
        reasons.append("Provider/calendar date conflict; upcoming provider date preferred.")
    if calendar_lookup_failed:
        reasons.append("Saved calendar lookup unavailable.")
    if expiry is None or expiry < today:
        reasons.append("Contract expiration missing, invalid or past.")
    if spot is None or strike_value is None:
        reasons.append("Valid underlying price and strike required.")
    if not quote_sufficient:
        reasons.append("Quote evidence insufficient: requires positive non-crossed bid/ask and fresh, non-carried live fields.")
    if (raw_bid is not None and raw_bid < 0) or (raw_ask is not None and raw_ask < 0):
        reasons.append("Negative raw extrinsic indicates quote/intrinsic inconsistency, not zero time value.")
    indicative_comparison = (
        historical_dividend > extrinsic_bid
        if historical_dividend is not None and quote_sufficient else None
    )
    style = str(contract.get("exercise_style") or contract.get("option_style") or "unknown").strip().lower()
    if option_type != "call" or style == "european":
        status = "not_applicable"
        reasons.append("Dividend-capture early exercise applies to American-style short calls, not short puts or European-style options.")
    elif spans and itm:
        status = "warning"
    elif spans is None or itm is None:
        status = "unknown"
    else:
        status = "informational"

    def show(value) -> str:
        return f"{value:.4f}" if value is not None else "unknown"

    observed = (
        f"{scope}: ex-date {ex_date or 'unknown'} ({date_source}); expiration {expiry or 'unknown'}; "
        f"stock {show(spot)}; strike / ITM threshold S > {show(strike_value)}; "
        f"S-K {show(spot - strike_value if itm is not None else None)}; "
        f"currently {'ITM' if itm else 'ATM/OTM' if itm is False else 'unknown'}; "
        f"bid/ask extrinsic {show(extrinsic_bid)} / {show(extrinsic_ask)} per share; "
        f"confirmed upcoming dividend unknown; last dividend {show(historical_dividend)} per share (historical, unconfirmed)."
    )
    if indicative_comparison is not None:
        reasons.append(f"Historical-only indicative comparison: last dividend {'>' if indicative_comparison else '<='} bid extrinsic; not confirmation of the next payment.")
    reasons.append(
        "Informative only. American-style exercise assumed unless style is known; holder may exercise in the previous trading session before ex-date. "
        "Dividend greater than remaining bid extrinsic while ITM can favor exercise; financing/costs also matter. "
        "Strike is only the ITM trigger, not a unique or guaranteed future assignment price. "
        "Current OTM or rolling farther out does not guarantee safety if the contract spans ex-date."
    )
    return {
        "status": status, "observed": observed, "detail": " ".join(reasons),
        "data_refs": {
            "scope": scope, "analysis_timestamp": now.isoformat(), "date_basis": "America/New_York",
            "ex_dividend_date": ex_date.isoformat() if ex_date else None,
            "ex_dividend_date_source": date_source,
            "provider_ex_dividend_date": provider_date.isoformat() if provider_date else None,
            "calendar_ex_dividend_date": saved_date.isoformat() if saved_date else None,
            "calendar_conflict": conflict, "calendar_lookup_failed": calendar_lookup_failed,
            "expiration": expiry.isoformat() if expiry else None,
            "spans_ex_dividend": spans, "option_type": option_type, "exercise_style": style,
            "strike": strike_value, "itm_threshold": strike_value, "underlying_price": spot,
            "underlying_price_source": spot_source, "currently_itm": itm,
            "distance_to_strike": strike_value - spot if itm is not None else None,
            "intrinsic": intrinsic, "bid": bid, "ask": ask,
            "raw_extrinsic_bid": raw_bid, "raw_extrinsic_ask": raw_ask,
            "extrinsic_bid": extrinsic_bid, "extrinsic_ask": extrinsic_ask,
            "quote_asof": meta.get("quote_asof"), "quote_stale": meta.get("stale"),
            "quote_carried": meta.get("carried"), "quote_field_status": field_status,
            "quote_sufficient": quote_sufficient,
            "confirmed_upcoming_dividend": None, "historical_last_dividend": historical_dividend,
            "historical_dividend_exceeds_bid_extrinsic": indicative_comparison,
        },
    }
