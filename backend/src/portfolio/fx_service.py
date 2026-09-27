"""FX rate service for the portfolio domain.

Fetches daily EUR reference rates from the ECB's public XML endpoint.
Rates are expressed as EUR per 1 unit of foreign currency
(i.e. eur_amount = txn_amount × rate).

No additional dependency — uses `requests` which is already in requirements.txt.

Supported: any currency published by the ECB (USD, GBP, CHF, JPY, etc.).
EUR-to-EUR always returns 1.0 without a network call.
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from xml.etree import ElementTree

import requests

logger = logging.getLogger(__name__)

# Full ECB reference-rate history; no auth required.
_ECB_HIST_XML = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml"
_ECB_TIMEOUT = (5, 20)
_ECB_MAX_BYTES = 32 * 1024 * 1024
_ECB_USER_AGENT = "option-income-lab/1.0"

# Lightweight daily cache: maps (iso_date, currency) → rate_str
_rate_cache: dict[tuple[str, str], str] = {}
_cache_lock = threading.Lock()
_refresh_lock = threading.Lock()
_cache_fetched_date: str | None = None  # track when the cache was last filled


class FxUnavailableError(Exception):
    """Raised when the ECB endpoint cannot be reached."""


class FxRateNotFoundError(Exception):
    """Raised when the requested date/currency combination has no rate."""
    def __init__(self, currency: str, rate_date: str) -> None:
        self.currency = currency
        self.rate_date = rate_date
        super().__init__(f"No ECB rate for {currency} on {rate_date}")


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _fetch_and_cache() -> None:
    """Fetch the full ECB history XML and populate _rate_cache.

    The ECB publishes an XML with a table of <Cube time="YYYY-MM-DD"> rows,
    each containing <Cube currency="USD" rate="1.0843"/> children.

    Rate semantics: ECB publishes rates as *units of foreign currency per 1 EUR*
    (EUR is the base). We invert so our convention is EUR per 1 unit of foreign
    currency (i.e. eur_amount = txn_amount × rate).
    """
    global _cache_fetched_date
    try:
        response = requests.get(
            _ECB_HIST_XML,
            headers={"User-Agent": _ECB_USER_AGENT},
            timeout=_ECB_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise FxUnavailableError(f"ECB API unreachable: {exc}") from exc

    content = response.content
    if not isinstance(content, bytes):
        content = response.text.encode("utf-8")
    if not content or len(content) > _ECB_MAX_BYTES:
        raise FxUnavailableError("ECB history response has an invalid size")

    new_entries: dict[tuple[str, str], str] = {}
    try:
        for _event, element in ElementTree.iterparse(
            BytesIO(content), events=("end",)
        ):
            rate_date = element.attrib.get("time")
            if not rate_date or not element.tag.endswith("Cube"):
                continue
            date.fromisoformat(rate_date)
            for child in element:
                currency = child.attrib.get("currency", "").upper()
                raw_rate = child.attrib.get("rate")
                if not currency or raw_rate is None:
                    continue
                ecb_rate = Decimal(raw_rate)  # foreign currency per 1 EUR
                if ecb_rate > 0:
                    new_entries[(rate_date, currency)] = str(
                        (Decimal(1) / ecb_rate).quantize(
                            Decimal("0.000000001")
                        )
                    )
            element.clear()
    except (ElementTree.ParseError, ValueError, ArithmeticError) as exc:
        raise FxUnavailableError("ECB history response could not be parsed") from exc

    if not new_entries:
        raise FxUnavailableError("ECB history response contained no reference rates")

    with _cache_lock:
        _rate_cache.clear()
        _rate_cache.update(new_entries)
        _cache_fetched_date = _today_iso()
    logger.debug("ECB rates loaded: %d entries", len(new_entries))


def _ensure_cache_fresh() -> None:
    """Refresh the cache once per calendar day."""
    today = _today_iso()
    with _cache_lock:
        if _cache_fetched_date == today and _rate_cache:
            return
    with _refresh_lock:
        with _cache_lock:
            if _cache_fetched_date == today and _rate_cache:
                return
        _fetch_and_cache()


def get_historical_fx_rate(
    from_currency: str,
    to_currency: str = "EUR",
    rate_date: str | None = None,
) -> tuple[str, str]:
    """Return ``(EUR-per-unit rate, effective ECB date)``.

    Args:
        from_currency: 3-letter ISO currency code (e.g. "USD").
        to_currency: Must be "EUR" (only EUR base supported in Phase 2).
        rate_date: ISO date string (YYYY-MM-DD). Defaults to today.

    Returns:
        A 9-decimal rate string and the actual publication date used.

    Raises:
        ValueError: Unsupported to_currency or malformed date.
        FxUnavailableError: ECB API unreachable.
        FxRateNotFoundError: No rate for the given currency/date.
    """
    from_currency = from_currency.strip().upper()
    to_currency = to_currency.strip().upper()

    if to_currency != "EUR":
        raise ValueError(
            f"Only EUR is supported as to_currency in Phase 2; got {to_currency!r}"
        )

    if rate_date is None:
        rate_date = _today_iso()
    else:
        try:
            target = date.fromisoformat(rate_date)
        except ValueError:
            raise ValueError(f"rate_date must be YYYY-MM-DD, got {rate_date!r}")
        if target > datetime.now(timezone.utc).date():
            raise FxRateNotFoundError(from_currency, rate_date)

    if from_currency == "EUR":
        return "1.000000000", rate_date

    _ensure_cache_fresh()

    with _cache_lock:
        rate = _rate_cache.get((rate_date, from_currency))

    if rate is not None:
        return rate, rate_date

    # Try adjacent business days (ECB doesn't publish on weekends/holidays)
    # Look back up to 5 calendar days
    target = date.fromisoformat(rate_date)
    for days_back in range(1, 6):
        fallback = (target - timedelta(days=days_back)).isoformat()
        with _cache_lock:
            rate = _rate_cache.get((fallback, from_currency))
        if rate is not None:
            logger.debug(
                "FX rate for %s on %s not found; using %s rate",
                from_currency, rate_date, fallback,
            )
            return rate, fallback

    raise FxRateNotFoundError(from_currency, rate_date)


def get_fx_rate_with_effective_date(
    from_currency: str,
    to_currency: str = "EUR",
    rate_date: str | None = None,
) -> tuple[str, str]:
    """Compatibility alias for callers that need the ECB observation date."""
    return get_historical_fx_rate(from_currency, to_currency, rate_date)


def get_historical_fx_observation(
    from_currency: str,
    to_currency: str = "EUR",
    rate_date: str | None = None,
) -> tuple[str, str]:
    """Explicit observation-oriented alias used by migration tooling."""
    return get_historical_fx_rate(from_currency, to_currency, rate_date)


def get_fx_rate(
    from_currency: str,
    to_currency: str = "EUR",
    rate_date: str | None = None,
) -> str:
    """Return only the rate, preserving the established portfolio API."""
    rate, _effective_date = get_historical_fx_rate(
        from_currency, to_currency, rate_date
    )
    return rate
