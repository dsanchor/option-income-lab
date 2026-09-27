"""Shared Yahoo Open and historical ECB valuation for Dividend · Buy."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4

from src.symbol_pricing import normalize_quote_price
from src.yfinance_fetcher import YFinanceFetcher

from .cosmos_securities import security_id_to_doc_id, security_id_to_ticker
from .fx_service import (
    FxRateNotFoundError,
    FxUnavailableError,
    get_historical_fx_rate,
)
from .models import normalize_share_fmv
from .provider_symbols import resolve_yfinance_symbol

SCRIPT_VERSION = "dividend-buy-fmv-v1"
_Q6 = Decimal("0.000001")
_Q9 = Decimal("0.000000001")


@dataclass(frozen=True)
class YahooFmvError(RuntimeError):
    error: str
    detail: str
    stage: str
    retryable: bool
    status_code: int

    def __str__(self) -> str:
        return self.detail


def validate_yahoo_instruction(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"source"}:
        raise ValueError("share_fmv_instruction must contain only source='YAHOO_OPEN'")
    if value.get("source") != "YAHOO_OPEN":
        raise ValueError("share_fmv_instruction.source must be YAHOO_OPEN")


def validate_client_request_id(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("client_request_id must be a UUID")  # noqa: TRY004
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError("client_request_id must be a UUID") from exc
    if str(parsed) != value.lower():
        raise ValueError("client_request_id must be a canonical UUID")
    return str(parsed)


def validate_yahoo_request(request: dict[str, Any]) -> str | None:
    """Validate FMV directive shape and return the Yahoo idempotency UUID."""
    event_type = request.get("event_type")
    yahoo_requested = False
    for leg in request.get("legs") or []:
        if not isinstance(leg, dict):
            raise ValueError("legs must contain objects")  # noqa: TRY004
        has_fmv = "share_fmv" in leg
        has_instruction = "share_fmv_instruction" in leg
        if has_fmv and has_instruction:
            raise ValueError(
                "share_fmv and share_fmv_instruction are mutually exclusive"
            )
        instruction = leg.get("share_fmv_instruction")
        if has_instruction:
            validate_yahoo_instruction(instruction)
            if (
                event_type not in {"SCRIP_DIVIDEND", "DIVIDEND_WITH_SCRIP"}
                or leg.get("leg_type") != "SHARE_ACQUISITION"
            ):
                raise ValueError(
                    "share_fmv_instruction is only valid on SHARE_ACQUISITION "
                    "legs of SCRIP_DIVIDEND or DIVIDEND_WITH_SCRIP"
                )
            yahoo_requested = True
        supplied_fmv = leg.get("share_fmv")
        if (
            isinstance(supplied_fmv, dict)
            and str(supplied_fmv.get("source") or "").strip().upper() == "YAHOO_OPEN"
        ):
            raise ValueError(
                "share_fmv.source=YAHOO_OPEN is reserved for the server; "
                "use share_fmv_instruction"
            )
    if not yahoo_requested:
        return None
    return validate_client_request_id(request.get("client_request_id"))


def canonical_request_hash(request: dict[str, Any]) -> str:
    payload = {
        key: value for key, value in request.items() if key != "ca_group_id_path"
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _decimal(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _iso_date(value: Any) -> str | None:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError):
        return None


def read_security(symbols_container: Any, security_id: str) -> dict | None:
    if symbols_container is None or not security_id:
        return None
    try:
        return symbols_container.read_item(
            item=security_id_to_doc_id(security_id),
            partition_key=security_id_to_ticker(security_id),
        )
    except Exception as exc:
        if exc.__class__.__name__ == "CosmosResourceNotFoundError":
            return None
        raise


def _call_with_timeout(
    func: Callable[..., Any],
    *args: Any,
    timeout_seconds: float,
    timeout_error: YahooFmvError,
    **kwargs: Any,
) -> Any:
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(func, *args, **kwargs)
    try:
        return future.result(timeout=timeout_seconds)
    except FutureTimeout as exc:
        future.cancel()
        raise timeout_error from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def observe_yahoo_open(
    fetcher: Any,
    provider_symbol: str,
    requested_date: str,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    method = getattr(fetcher, "get_daily_open", None) or getattr(
        fetcher, "get_historical_open", None
    )
    if method is None:
        raise YahooFmvError(
            "yahoo_fmv_upstream_unavailable",
            "Yahoo daily Open provider is unavailable",
            "yahoo",
            True,
            503,
        )
    try:
        raw = _call_with_timeout(
            method,
            provider_symbol,
            requested_date,
            max_calendar_days=7,
            timeout_seconds=timeout_seconds,
            timeout_error=YahooFmvError(
                "yahoo_fmv_timeout",
                "Yahoo Open lookup timed out",
                "yahoo",
                True,
                504,
            ),
        )
    except TypeError:
        raw = _call_with_timeout(
            method,
            provider_symbol,
            requested_date,
            7,
            timeout_seconds=timeout_seconds,
            timeout_error=YahooFmvError(
                "yahoo_fmv_timeout",
                "Yahoo Open lookup timed out",
                "yahoo",
                True,
                504,
            ),
        )
    except YahooFmvError:
        raise
    except Exception as exc:
        raise YahooFmvError(
            "yahoo_fmv_upstream_unavailable",
            "Yahoo Open lookup failed",
            "yahoo",
            True,
            503,
        ) from exc
    if not isinstance(raw, dict):
        return {"status": "provider_error"}
    if (
        not raw.get("status")
        and raw.get("open") is not None
        and raw.get("market_session_date")
    ):
        return {**raw, "status": "ok"}
    return raw


def build_yahoo_share_fmv(
    *,
    quantity: Any,
    trade_date: str,
    security: dict,
    provider_symbol: str,
    observation: dict,
    fx_getter: Callable[..., tuple[str, str]] = get_historical_fx_rate,
    fx_timeout_seconds: float = 20,
    fetched_at: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    requested_date = _iso_date(trade_date)
    if requested_date is None:
        raise ValueError("trade_date must be YYYY-MM-DD")

    status = observation.get("status")
    unavailable = {
        "no_market_session": "No Yahoo market session was found on or after the payment date",
        "invalid_open": "Yahoo returned an invalid Open price",
        "currency_unavailable": "Yahoo listing currency is unavailable",
    }
    if status in unavailable:
        raise YahooFmvError(
            "yahoo_fmv_unavailable", unavailable[status], "yahoo", False, 422
        )
    if status != "ok":
        raise YahooFmvError(
            "yahoo_fmv_upstream_unavailable",
            "Yahoo Open lookup failed",
            "yahoo",
            True,
            503,
        )

    session_date = _iso_date(observation.get("market_session_date"))
    if session_date is None:
        raise YahooFmvError(
            "yahoo_fmv_unavailable",
            "Yahoo market session date is invalid",
            "yahoo",
            False,
            422,
        )
    delta = (date.fromisoformat(session_date) - date.fromisoformat(requested_date)).days
    if delta < 0 or delta > 7:
        raise YahooFmvError(
            "yahoo_fmv_unavailable",
            "Yahoo did not return an eligible session within seven calendar days",
            "yahoo",
            False,
            422,
        )

    listing_currency = str(security.get("listing_currency") or "").strip().upper()
    observed_currency = str(observation.get("currency") or "").strip()
    try:
        price, normalized_currency, _, _ = normalize_quote_price(
            observation.get("open"), observed_currency
        )
    except ValueError as exc:
        raise YahooFmvError(
            "yahoo_fmv_unavailable",
            "Yahoo returned an invalid Open price or quote currency",
            "yahoo",
            False,
            422,
        ) from exc
    if not listing_currency or normalized_currency != listing_currency:
        raise YahooFmvError(
            "yahoo_fmv_unavailable",
            "Yahoo currency does not match the Security Master listing currency",
            "currency",
            False,
            422,
        )

    qty = _decimal(quantity)
    if price <= 0 or qty is None or qty <= 0:
        raise YahooFmvError(
            "yahoo_fmv_unavailable",
            "Yahoo returned an invalid Open price or share quantity",
            "yahoo",
            False,
            422,
        )

    if listing_currency == "EUR":
        fx_rate, fx_date, fx_source = Decimal(1), requested_date, "IDENTITY"
    else:
        try:
            try:
                raw_rate, raw_date = _call_with_timeout(
                    fx_getter,
                    listing_currency,
                    "EUR",
                    rate_date=requested_date,
                    timeout_seconds=fx_timeout_seconds,
                    timeout_error=YahooFmvError(
                        "fx_timeout",
                        "Historical ECB FX lookup timed out",
                        "fx",
                        True,
                        504,
                    ),
                )
            except TypeError:
                raw_rate, raw_date = _call_with_timeout(
                    fx_getter,
                    listing_currency,
                    "EUR",
                    requested_date,
                    timeout_seconds=fx_timeout_seconds,
                    timeout_error=YahooFmvError(
                        "fx_timeout",
                        "Historical ECB FX lookup timed out",
                        "fx",
                        True,
                        504,
                    ),
                )
        except YahooFmvError:
            raise
        except FxRateNotFoundError as exc:
            raise YahooFmvError(
                "fx_unavailable",
                "No eligible historical ECB FX rate was found",
                "fx",
                True,
                503,
            ) from exc
        except (FxUnavailableError, ValueError) as exc:
            raise YahooFmvError(
                "fx_unavailable",
                "Historical ECB FX lookup failed",
                "fx",
                True,
                503,
            ) from exc
        fx_rate = _decimal(raw_rate)
        fx_date = _iso_date(raw_date)
        fx_source = "ECB"
        if fx_rate is None or fx_rate <= 0 or fx_date is None:
            raise YahooFmvError(
                "fx_unavailable",
                "Historical ECB FX rate is invalid",
                "fx",
                True,
                503,
            )
        fx_age = (date.fromisoformat(requested_date) - date.fromisoformat(fx_date)).days
        if fx_age < 0 or fx_age > 5:
            raise YahooFmvError(
                "fx_unavailable",
                "Historical ECB FX rate is outside the five-day fallback window",
                "fx",
                True,
                503,
            )

    price_q = price.quantize(_Q6, rounding=ROUND_HALF_UP)
    amount_q = (price_q * qty).quantize(_Q6, rounding=ROUND_HALF_UP)
    rate_q = fx_rate.quantize(_Q9, rounding=ROUND_HALF_UP)
    price_eur_q = (price_q * rate_q).quantize(_Q6, rounding=ROUND_HALF_UP)
    amount_eur_q = (amount_q * rate_q).quantize(_Q6, rounding=ROUND_HALF_UP)
    value = {
        "valuation_date": requested_date,
        "amount": format(amount_q, "f"),
        "currency": listing_currency,
        "eur_amount": format(amount_eur_q, "f"),
        "price_per_share": format(price_q, "f"),
        "price_per_share_eur": format(price_eur_q, "f"),
        "source": "YAHOO_OPEN",
        "confidence": "MARKET_ESTIMATE",
        "fx": {
            "rate": format(rate_q, "f"),
            "date": fx_date,
            "source": fx_source,
        },
        "provenance": {
            "provider": "yfinance",
            "provider_symbol": provider_symbol,
            "price_field": "OPEN",
            "requested_date": requested_date,
            "market_session_date": session_date,
            "fetched_at": fetched_at or datetime.now(timezone.utc).isoformat(),
            "script_version": SCRIPT_VERSION,
            "run_id": run_id or str(uuid4()),
        },
    }
    return normalize_share_fmv(
        value,
        quantity=quantity,
        trade_date=requested_date,
        allow_yahoo=True,
    )


class ShareFmvService:
    def __init__(
        self,
        symbols_container: Any,
        *,
        fetcher: Any | None = None,
        fx_getter: Callable[..., tuple[str, str]] = get_historical_fx_rate,
        yahoo_timeout_seconds: float | None = None,
        fx_timeout_seconds: float | None = None,
        total_timeout_seconds: float | None = None,
    ) -> None:
        self.symbols_container = symbols_container
        self.fetcher = fetcher or YFinanceFetcher()
        self.fx_getter = fx_getter
        self.yahoo_timeout_seconds = (
            yahoo_timeout_seconds
            if yahoo_timeout_seconds is not None
            else float(os.getenv("SHARE_FMV_YAHOO_TIMEOUT_SECONDS", "15"))
        )
        self.fx_timeout_seconds = (
            fx_timeout_seconds
            if fx_timeout_seconds is not None
            else float(os.getenv("SHARE_FMV_FX_TIMEOUT_SECONDS", "20"))
        )
        self.total_timeout_seconds = (
            total_timeout_seconds
            if total_timeout_seconds is not None
            else float(os.getenv("SHARE_FMV_TOTAL_TIMEOUT_SECONDS", "30"))
        )

    def resolve(
        self,
        *,
        security_id: str,
        quantity: Any,
        trade_date: str,
        run_id: str | None = None,
        fetched_at: str | None = None,
    ) -> dict[str, Any]:
        started = datetime.now(timezone.utc)
        try:
            security = read_security(self.symbols_container, security_id)
        except Exception as exc:
            raise YahooFmvError(
                "yahoo_fmv_upstream_unavailable",
                "Security Master lookup failed",
                "security",
                True,
                503,
            ) from exc
        if not security:
            raise YahooFmvError(
                "yahoo_fmv_unavailable",
                "Security Master record was not found",
                "security",
                False,
                422,
            )
        ticker = str(security.get("ticker") or security_id_to_ticker(security_id))
        provider_symbol = resolve_yfinance_symbol(
            ticker, security.get("exchange_mic"), security
        )
        if not provider_symbol:
            raise YahooFmvError(
                "yahoo_fmv_unavailable",
                "Yahoo provider symbol could not be resolved",
                "symbol",
                False,
                422,
            )
        observation = observe_yahoo_open(
            self.fetcher,
            provider_symbol,
            trade_date,
            timeout_seconds=min(self.yahoo_timeout_seconds, self.total_timeout_seconds),
        )
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        remaining = self.total_timeout_seconds - elapsed
        if remaining <= 0:
            raise YahooFmvError(
                "yahoo_fmv_timeout",
                "Yahoo FMV valuation timed out",
                "yahoo",
                True,
                504,
            )
        return build_yahoo_share_fmv(
            quantity=quantity,
            trade_date=trade_date,
            security=security,
            provider_symbol=provider_symbol,
            observation=observation,
            fx_getter=self.fx_getter,
            fx_timeout_seconds=min(self.fx_timeout_seconds, remaining),
            fetched_at=fetched_at,
            run_id=run_id,
        )
