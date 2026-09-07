"""Symbol Pricing Cache — scheduled pricing job with multi-currency FX support.

Implements the danny-symbol-pricing-cache-contract.md §7 public API.

Fetches current prices for every symbol_config document, converts to EUR using
the existing ECB fx_service, and writes a ``pricing_cache`` sibling field onto
each symbol_config doc.  All symbols in a single run share coherent FX rates
fetched once at run start.

Minor-unit handling (GBp/GBX): conversion from pence to pounds happens exactly
once here.  Downstream consumers (overview endpoint, frontend) use ``price_major``
and ``price_eur`` directly — they never divide by 100.

FX abort rule: if the ECB endpoint is unreachable (FxUnavailableError) the
entire run is aborted and no existing cache entry is touched.  A per-currency
FxRateNotFoundError is non-fatal: those symbols get ``price_eur=null`` with
``status="ok"`` (price data is still valid; only EUR conversion is missing).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional, Tuple

import yfinance as yf

from src.portfolio.fx_service import (
    FxRateNotFoundError,
    FxUnavailableError,
    get_fx_rate,
)
from src.portfolio.provider_symbols import resolve_yfinance_symbol

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Minor-unit code tables (contract §4.2)
# ---------------------------------------------------------------------------

# Maps exact provider-returned minor-unit code → (major_code, divisor).
# Comparison is CASE-SENSITIVE: "GBp" ≠ "GBP".  If Yahoo ever returns "GBP"
# for an XLON security it is already in major units and must NOT be divided.
_MINOR_UNIT_MAP: Dict[str, Tuple[str, int]] = {
    "GBp": ("GBP", 100),
    "GBX": ("GBP", 100),
    "ILA": ("ILS", 100),
    "ZAc": ("ZAR", 100),
}

# MIC → expected quote currencies for sanity-check logging (contract §4.1).
_MIC_EXPECTED_CURRENCIES: Dict[str, set] = {
    "XNYS": {"USD"},
    "XNAS": {"USD"},
    "XMAD": {"EUR"},
    "XAMS": {"EUR"},
    "XLON": {"GBp", "GBX", "GBP"},
    "XSWX": {"CHF"},
    "XETR": {"EUR"},
    "XPAR": {"EUR"},
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_id_from_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _fetch_yf_info(yf_symbol: str) -> Optional[Dict[str, Any]]:
    """Fetch yfinance info dict for *yf_symbol*.  Returns None on failure."""
    try:
        ticker = yf.Ticker(yf_symbol)
        info = ticker.info
        if not info:
            return None
        return info
    except Exception as exc:  # noqa: BLE001
        logger.warning("symbol_pricing: yfinance fetch failed for %s: %s", yf_symbol, exc)
        return None


def _extract_price(info: Dict[str, Any]) -> Optional[float]:
    """Return regularMarketPrice or currentPrice from yfinance info dict."""
    price = info.get("regularMarketPrice") or info.get("currentPrice")
    if price is None:
        return None
    try:
        return float(price)
    except (TypeError, ValueError):
        return None


def _apply_minor_unit(
    raw_price: float, quote_currency: str
) -> Tuple[float, str, str, str]:
    """Convert raw price to major unit if quote_currency is a minor-unit code.

    Returns (price_major, price_currency, quote_unit, quote_currency_display).
    quote_currency_display preserves the original code (e.g. "GBp") for UI.
    """
    if quote_currency in _MINOR_UNIT_MAP:
        major_code, divisor = _MINOR_UNIT_MAP[quote_currency]
        return (
            raw_price / divisor,
            major_code,
            "minor",
            quote_currency,
        )
    return (raw_price, quote_currency, "major", quote_currency)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def run_symbol_pricing(cosmos) -> dict:
    """Fetch current prices for all symbols, convert to EUR, update cache.

    Returns summary:
        {
            "status": "completed" | "aborted",
            "total": int,
            "success": int,
            "errors": int,
            "error_symbols": [...],
            "fx_rates_used": {currency: rate_str, ...},
            "run_id": str,
        }
    """
    if cosmos is None:
        return {"status": "aborted", "reason": "cosmos_unavailable",
                "total": 0, "success": 0, "errors": 0,
                "error_symbols": [], "fx_rates_used": {}, "run_id": ""}

    run_id = _run_id_from_now()
    fetched_at_run = _now_utc_iso()
    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    symbols = cosmos.list_symbols()
    total = len(symbols)
    logger.info("symbol_pricing [%s]: starting for %d symbols", run_id, total)

    if not symbols:
        return {"status": "completed", "total": 0, "success": 0, "errors": 0,
                "error_symbols": [], "fx_rates_used": {}, "run_id": run_id}

    # ── Security-master service for provider_symbols override resolution ──
    try:
        from src.portfolio.cosmos_securities import CosmosSecuritiesService
        securities_svc = CosmosSecuritiesService(cosmos.container)
    except Exception as exc:
        logger.error("symbol_pricing: cannot init CosmosSecuritiesService: %s", exc)
        securities_svc = None

    # ── Pass 1: resolve yfinance symbols + fetch info for every symbol ────
    # Collecting all raw data before writing ensures FX abort leaves cache intact.
    fetched: List[Dict[str, Any]] = []  # per-symbol result dicts

    for sym_doc in symbols:
        symbol: str = (sym_doc.get("symbol") or "").strip().upper()
        if not symbol:
            continue

        exchange_mic: str = (sym_doc.get("exchange") or "").strip().upper()
        security_id: str = sym_doc.get("security_id") or (
            f"{exchange_mic}:{symbol}" if exchange_mic else ""
        )

        # Resolve security_master for provider_symbols override
        security_doc: Optional[Dict[str, Any]] = None
        if security_id and securities_svc is not None:
            try:
                security_doc = securities_svc.get_security(security_id)
            except Exception as exc:
                logger.warning(
                    "symbol_pricing: %s — security_master lookup failed (%s): %s",
                    symbol, security_id, exc,
                )

        yf_symbol = resolve_yfinance_symbol(symbol, exchange_mic, security_doc)
        if yf_symbol is None:
            logger.warning(
                "symbol_pricing: %s — no Yahoo symbol mapping for MIC=%s",
                symbol, exchange_mic or "unknown",
            )
            fetched.append({
                "symbol": symbol,
                "error": "no Yahoo symbol mapping",
                "info": None,
            })
            time.sleep(0.5)
            continue

        info = _fetch_yf_info(yf_symbol)
        fetched.append({
            "symbol": symbol,
            "yf_symbol": yf_symbol,
            "exchange_mic": exchange_mic,
            "info": info,
            "error": None if info is not None else "yfinance fetch returned no data",
        })
        time.sleep(0.5)

    # ── Collect unique price_currencies for FX pre-fetch ─────────────────
    needed_currencies: set = set()
    for item in fetched:
        info = item.get("info")
        if info is None:
            continue
        qc = str(info.get("currency") or "").strip()
        if not qc:
            continue
        if qc in _MINOR_UNIT_MAP:
            major_code = _MINOR_UNIT_MAP[qc][0]
            needed_currencies.add(major_code)
        else:
            needed_currencies.add(qc)
    needed_currencies.discard("EUR")  # EUR identity — no ECB call needed

    # ── Fetch FX rates (abort entire run on ECB outage) ───────────────────
    fx_rates: Dict[str, Optional[str]] = {}   # price_currency → rate_str or None
    fx_errors: set = set()  # currencies with FxRateNotFoundError

    for ccy in sorted(needed_currencies):
        try:
            rate_str = get_fx_rate(from_currency=ccy, to_currency="EUR")
            fx_rates[ccy] = rate_str
            logger.info("symbol_pricing [%s]: FX rate %s/EUR = %s", run_id, ccy, rate_str)
        except FxUnavailableError as exc:
            logger.error(
                "symbol_pricing [%s]: ECB endpoint unreachable (%s) — aborting run, "
                "existing cache untouched", run_id, exc,
            )
            return {
                "status": "aborted",
                "reason": f"ECB FX unavailable: {exc}",
                "total": total,
                "success": 0,
                "errors": total,
                "error_symbols": [i["symbol"] for i in fetched],
                "fx_rates_used": {},
                "run_id": run_id,
            }
        except FxRateNotFoundError as exc:
            logger.warning(
                "symbol_pricing [%s]: FX rate not found for %s (%s) — "
                "price_eur will be null for these symbols", run_id, ccy, exc,
            )
            fx_rates[ccy] = None
            fx_errors.add(ccy)

    # EUR identity rate
    fx_rates["EUR"] = "1.000000000"

    # ── Pass 2: build and write pricing_cache per symbol ─────────────────
    success = 0
    errors = 0
    error_symbols: List[str] = []

    for item in fetched:
        symbol = item["symbol"]
        info = item.get("info")
        fetch_error = item.get("error")

        if info is None or fetch_error:
            # Symbol fetch failed — preserve prior cache, do not write
            logger.warning("symbol_pricing: %s — skipped: %s", symbol, fetch_error)
            errors += 1
            error_symbols.append(symbol)
            continue

        # Extract raw price
        raw_price = _extract_price(info)
        if raw_price is None:
            logger.warning("symbol_pricing: %s — no price in yfinance info", symbol)
            errors += 1
            error_symbols.append(symbol)
            continue

        # Extract quote currency (mandatory — no MIC fallback)
        quote_currency = str(info.get("currency") or "").strip()
        if not quote_currency:
            logger.warning(
                "symbol_pricing: %s — no quote currency from provider; "
                "existing cache preserved (§10.2)", symbol
            )
            errors += 1
            error_symbols.append(symbol)
            continue

        # Sanity-check currency vs MIC (warn only — trust provider)
        exchange_mic = item.get("exchange_mic", "")
        expected = _MIC_EXPECTED_CURRENCIES.get(exchange_mic, set())
        if expected and quote_currency not in expected:
            logger.warning(
                "symbol_pricing: %s — quote currency %r unexpected for MIC=%s "
                "(expected %s); trusting provider",
                symbol, quote_currency, exchange_mic, sorted(expected),
            )

        # Apply minor-unit conversion (exactly once, here)
        price_major, price_currency, quote_unit, quote_currency_display = (
            _apply_minor_unit(raw_price, quote_currency)
        )

        # FX rate lookup
        rate_str: Optional[str] = fx_rates.get(price_currency)
        if price_currency not in fx_rates:
            # Unsupported/unknown currency — valid price, no EUR conversion
            logger.warning(
                "symbol_pricing: %s — currency %s not in FX rate cache "
                "(unsupported); price_eur will be null", symbol, price_currency,
            )
            rate_str = None

        # Compute price_eur
        price_eur: Optional[float] = None
        if rate_str is not None:
            try:
                price_eur = float(
                    (Decimal(str(price_major)) * Decimal(rate_str)).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "symbol_pricing: %s — price_eur computation failed: %s",
                    symbol, exc,
                )

        fx_pair = f"{price_currency}/EUR" if price_currency else None

        pricing_cache = {
            "raw_price": raw_price,
            "quote_currency": quote_currency_display,
            "quote_unit": quote_unit,
            "price_major": round(price_major, 2),
            "price_currency": price_currency,
            "fx_rate": rate_str,
            "fx_pair": fx_pair,
            "fx_rate_date": today_iso,
            "fx_source": "ECB",
            "price_eur": price_eur,
            "fetched_at": fetched_at_run,
            "status": "ok",
            "error_message": None,
            "run_id": run_id,
        }

        try:
            cosmos.update_symbol_pricing_cache(symbol, pricing_cache)
            success += 1
            logger.info(
                "  ✓ %s: raw=%s %s → %s %s, EUR=%s",
                symbol, raw_price, quote_currency_display,
                price_major, price_currency, price_eur,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("  ✗ %s: failed to save pricing_cache: %s", symbol, exc)
            errors += 1
            error_symbols.append(symbol)

    fx_rates_used = {
        ccy: rate for ccy, rate in fx_rates.items()
        if rate is not None and ccy != "EUR"
    }

    logger.info(
        "symbol_pricing [%s]: complete — %d/%d success, %d errors",
        run_id, success, total, errors,
    )

    return {
        "status": "completed",
        "total": total,
        "success": success,
        "errors": errors,
        "error_symbols": error_symbols,
        "fx_rates_used": fx_rates_used,
        "run_id": run_id,
    }


def _write_cache(
    cosmos,
    symbol: str,
    pricing_cache: Dict[str, Any],
    error_symbols: List[str],
) -> None:
    """Write pricing_cache and append to error_symbols on failure."""
    try:
        cosmos.update_symbol_pricing_cache(symbol, pricing_cache)
    except Exception as exc:  # noqa: BLE001
        logger.error("  ✗ %s: failed to save pricing_cache: %s", symbol, exc)
        error_symbols.append(symbol)
