"""FX rate service for the portfolio domain.

Fetches daily EUR reference rates from the ECB's public CSV endpoint.
Rates are expressed as EUR per 1 unit of foreign currency
(i.e. eur_amount = txn_amount × rate).

No additional dependency — uses `requests` which is already in requirements.txt.

Supported: any currency published by the ECB (USD, GBP, CHF, JPY, etc.).
EUR-to-EUR always returns 1.0 without a network call.
"""

from __future__ import annotations

import csv
import io
import logging
import threading
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# ECB publishes ~90 days of history in this URL; no auth required
_ECB_HIST_90D_CSV = (
    "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"
)

# Lightweight daily cache: maps (iso_date, currency) → rate_str
_rate_cache: Dict[Tuple[str, str], str] = {}
_cache_lock = threading.Lock()
_cache_fetched_date: Optional[str] = None  # track when the cache was last filled


class FxUnavailableError(Exception):
    """Raised when the ECB endpoint cannot be reached."""


class FxRateNotFoundError(Exception):
    """Raised when the requested date/currency combination has no rate."""
    def __init__(self, currency: str, rate_date: str) -> None:
        self.currency = currency
        self.rate_date = rate_date
        super().__init__(f"No ECB rate for {currency} on {rate_date}")


def _today_iso() -> str:
    return date.today().isoformat()


def _fetch_and_cache() -> None:
    """Fetch the ECB 90-day history XML and populate _rate_cache.

    The ECB publishes an XML with a table of <Cube time="YYYY-MM-DD"> rows,
    each containing <Cube currency="USD" rate="1.0843"/> children.

    Rate semantics: ECB publishes rates as *units of foreign currency per 1 EUR*
    (EUR is the base). We invert so our convention is EUR per 1 unit of foreign
    currency (i.e. eur_amount = txn_amount × rate).
    """
    global _cache_fetched_date
    try:
        response = requests.get(_ECB_HIST_90D_CSV, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise FxUnavailableError(f"ECB API unreachable: {exc}") from exc

    xml_text = response.text
    # Simple streaming parse — avoid xml.etree dependency version issues
    # Pattern: <Cube time="YYYY-MM-DD"> ... <Cube currency="XXX" rate="N.NNN"/>
    import re
    date_pattern = re.compile(r'<Cube\s+time=["\'](\d{4}-\d{2}-\d{2})["\']')
    rate_pattern = re.compile(r'<Cube\s+currency=["\']([A-Z]+)["\']\s+rate=["\']([0-9.]+)["\']')

    current_date: Optional[str] = None
    new_entries: Dict[Tuple[str, str], str] = {}

    for line in xml_text.splitlines():
        dm = date_pattern.search(line)
        if dm:
            current_date = dm.group(1)
            # No `continue` here: ECB XML is single-line per date block, so the
            # rate <Cube> elements appear on the same line as the time attribute.
        if current_date:
            for rm in rate_pattern.finditer(line):
                currency = rm.group(1).upper()
                ecb_rate = Decimal(rm.group(2))  # foreign per 1 EUR
                if ecb_rate > Decimal("0"):
                    eur_per_foreign = (Decimal("1") / ecb_rate)
                    new_entries[(current_date, currency)] = str(
                        eur_per_foreign.quantize(Decimal("0.000000001"))
                    )

    with _cache_lock:
        _rate_cache.update(new_entries)
        _cache_fetched_date = _today_iso()
    logger.debug("ECB rates loaded: %d entries", len(new_entries))


def _ensure_cache_fresh() -> None:
    """Refresh the cache once per calendar day."""
    today = _today_iso()
    with _cache_lock:
        if _cache_fetched_date == today and _rate_cache:
            return
    _fetch_and_cache()


def get_fx_rate(from_currency: str, to_currency: str = "EUR", rate_date: Optional[str] = None) -> str:
    """Return the FX rate (EUR per 1 unit of from_currency) as a string.

    Args:
        from_currency: 3-letter ISO currency code (e.g. "USD").
        to_currency: Must be "EUR" (only EUR base supported in Phase 2).
        rate_date: ISO date string (YYYY-MM-DD). Defaults to today.

    Returns:
        Rate string with 9 decimal places (e.g. "0.921500000").

    Raises:
        ValueError: Unsupported to_currency or malformed date.
        FxUnavailableError: ECB API unreachable.
        FxRateNotFoundError: No rate for the given currency/date.
    """
    from_currency = from_currency.strip().upper()
    to_currency = to_currency.strip().upper()

    if to_currency != "EUR":
        raise ValueError(f"Only EUR is supported as to_currency in Phase 2; got {to_currency!r}")

    if from_currency == "EUR":
        return "1.000000000"

    if rate_date is None:
        rate_date = _today_iso()
    else:
        # Validate format
        try:
            date.fromisoformat(rate_date)
        except ValueError:
            raise ValueError(f"rate_date must be YYYY-MM-DD, got {rate_date!r}")

    _ensure_cache_fresh()

    with _cache_lock:
        rate = _rate_cache.get((rate_date, from_currency))

    if rate is not None:
        return rate

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
            return rate

    raise FxRateNotFoundError(from_currency, rate_date)
