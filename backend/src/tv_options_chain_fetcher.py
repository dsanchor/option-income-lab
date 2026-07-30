"""Standalone TradingView options chain fetcher via Playwright.

Used as a **fallback** when the US market is closed and yfinance returns
zeroed bid/ask/IV/volume data.  Intercepts TradingView scanner API
responses (same approach as the original ``tv_data_fetcher.py``) and
parses them into the **same JSON structure** that yfinance produces.

This module is intentionally self-contained — it does not depend on
``tv_data_fetcher.py`` or any other legacy module.
"""

import asyncio
import json
import logging
import random
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# User-Agent rotation (matches patterns from the original tv_data_fetcher)
# -------------------------------------------------------------------
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
]

# TradingView scanner endpoints that carry options chain data
_OPTIONS_SCAN_URLS = [
    "scanner.tradingview.com/global/scan2?label-product=symbols-options",
    "scanner.tradingview.com/options/scan2?label-product=symbols-options",
    "scanner.tradingview.com/global/scan?label-product=symbols-options",
    "scanner.tradingview.com/options/scan?label-product=symbols-options",
    "scanner.tradingview.com/global/screener?label-product=symbols-options",
    "scanner.tradingview.com/options/screener?label-product=symbols-options",
    "scanner.tradingview.com/global/scan3?label-product=symbols-options",
    "scanner.tradingview.com/options/scan3?label-product=symbols-options",
]
_OPTIONS_SCAN_FALLBACK = "scanner.tradingview.com"

# Field-name mapping: lowercased TradingView API field → canonical name
_FIELD_MAP = {
    "ask": "ask",
    "bid": "bid",
    "currency": "currency",
    "delta": "delta",
    "expiration": "expiration",
    "gamma": "gamma",
    "iv": "iv",
    "option-type": "option_type",
    "option_type": "option_type",
    "pricescale": "pricescale",
    "rho": "rho",
    "root": "root",
    "strike": "strike",
    "theoprice": "mid",
    "theo_price": "mid",
    "midprice": "mid",
    "mid": "mid",
    "theta": "theta",
    "vega": "vega",
    "bid_iv": "bid_iv",
    "ask_iv": "ask_iv",
    "option_bid": "bid",
    "option_ask": "ask",
    "option-bid": "bid",
    "option-ask": "ask",
    "bid_price": "bid",
    "ask_price": "ask",
    "implied_volatility": "iv",
    "implied-volatility": "iv",
}


# -------------------------------------------------------------------
# Parse raw TradingView scanner JSON into yfinance-compatible format
# -------------------------------------------------------------------

def _parse_tv_to_yfinance_format(
    raw_responses: list[dict],
    symbol: str,
) -> dict:
    """Convert captured TradingView scanner API responses into the same
    JSON structure that ``YFinanceDataProvider._build_options_chain``
    produces.

    The output dict has keys: symbol, timestamp, market_status, calls, puts.
    calls/puts are ``{expiration_YYYYMMDD: {strike_key: contract_dict}}``.
    """
    calls: Dict[str, dict] = defaultdict(dict)
    puts: Dict[str, dict] = defaultdict(dict)

    for resp in raw_responses:
        try:
            parsed = json.loads(resp["body"]) if isinstance(resp.get("body"), str) else resp
        except (json.JSONDecodeError, TypeError):
            continue

        items = parsed.get("symbols", parsed.get("data", []))
        fields_arr = parsed.get("fields", [])

        if fields_arr:
            idx_map = {}
            for i, name in enumerate(fields_arr):
                lowered = name.lower()
                canon = _FIELD_MAP.get(lowered, lowered.replace("-", "_"))
                idx_map[canon] = i
        else:
            # Legacy fallback positions
            idx_map = {
                "ask": 0, "bid": 1, "currency": 2, "delta": 3,
                "expiration": 4, "gamma": 5, "iv": 6, "option_type": 7,
                "pricescale": 8, "rho": 9, "root": 10, "strike": 11,
                "mid": 12, "theta": 13, "vega": 14, "bid_iv": 15, "ask_iv": 16,
            }

        opt_type_idx = idx_map.get("option_type")
        exp_idx = idx_map.get("expiration")
        if opt_type_idx is None or exp_idx is None:
            continue

        for item in items:
            f = item.get("f")
            if not f or len(f) <= max(opt_type_idx, exp_idx):
                continue

            option_type = f[opt_type_idx]
            raw_exp = f[exp_idx]
            if raw_exp is None or option_type not in ("call", "put"):
                continue
            # Normalize expiration to YYYYMMDD to match yfinance keys
            try:
                raw_exp_val = float(raw_exp)
                if raw_exp_val > 1e9:
                    # Unix timestamp — convert to YYYYMMDD
                    expiration = datetime.fromtimestamp(raw_exp_val, tz=timezone.utc).strftime("%Y%m%d")
                elif raw_exp_val > 19000000:
                    # Already YYYYMMDD as a number
                    expiration = str(int(raw_exp_val))
                else:
                    expiration = str(raw_exp)
            except (ValueError, TypeError, OSError):
                expiration = str(raw_exp)
            if not expiration:
                continue

            def _get(key: str, _f=f, _idx_map=idx_map):
                i = _idx_map.get(key)
                return _f[i] if i is not None and i < len(_f) else None

            strike = _get("strike")
            if strike is None:
                continue

            bid = _get("bid") or 0.0
            ask = _get("ask") or 0.0
            iv = _get("iv") or 0.0
            delta = _get("delta") or 0.0
            gamma = _get("gamma") or 0.0
            theta = _get("theta") or 0.0
            vega = _get("vega") or 0.0
            rho = _get("rho") or 0.0
            mid = _get("mid") or (round((bid + ask) / 2, 4) if (bid + ask) > 0 else 0.0)

            strike_f = float(strike)
            strike_key = f"{strike_f:.1f}" if strike_f == int(strike_f) else str(strike_f)

            # Build contract dict matching yfinance output format
            contract = {
                "contractSymbol": item.get("s", ""),
                "strike": strike_f,
                "bid": float(bid),
                "ask": float(ask),
                "mid": round(float(mid), 4),
                "iv": round(float(iv), 6),
                "delta": round(float(delta), 6),
                "gamma": round(float(gamma), 6),
                "theta": round(float(theta), 6),
                "vega": round(float(vega), 6),
                "rho": round(float(rho), 6),
                "volume": 0,           # Not available from TradingView scanner
                "openInterest": 0,     # Not available from TradingView scanner
                "lastPrice": 0.0,      # Not available from TradingView scanner
                "lastTradeDate": None,  # Not available from TradingView scanner
                "inTheMoney": False,    # Cannot determine without current price
                "expiration": expiration,
                "option_type": option_type,
            }

            bucket = calls if option_type == "call" else puts
            bucket[expiration][strike_key] = contract

    # Sort strikes and expirations
    for bucket in (calls, puts):
        for exp in bucket:
            bucket[exp] = dict(sorted(bucket[exp].items(), key=lambda kv: float(kv[0])))

    return {
        "symbol": symbol,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "market_status": "closed",
        "calls": dict(sorted(calls.items())),
        "puts": dict(sorted(puts.items())),
    }


# -------------------------------------------------------------------
# Playwright-based fetcher
# -------------------------------------------------------------------

async def fetch_tv_options_chain(symbol: str, *, timeout: int = 50000) -> dict:
    """Fetch options chain from TradingView using Playwright browser
    automation and API response interception.

    Parameters
    ----------
    symbol : str
        Ticker symbol (e.g. ``"AAPL"``).  Will be converted to
        TradingView format (``NASDAQ-AAPL`` → ``NASDAQ:AAPL``).
    timeout : int
        Page navigation timeout in milliseconds.

    Returns
    -------
    dict
        Parsed options chain in yfinance-compatible format, or an empty
        chain dict on failure.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("Playwright not installed — cannot use TradingView fallback")
        return _empty_chain(symbol)

    # Convert symbol format for TradingView URL
    tv_symbol = symbol.replace("-", ":")
    # If it's just a ticker without exchange prefix, use as-is
    url = f"https://www.tradingview.com/symbols/{tv_symbol}/options-chain/"

    captured_responses: list[dict] = []

    async def _on_response(response):
        resp_url = response.url
        if not response.ok:
            return

        is_known = any(ep in resp_url for ep in _OPTIONS_SCAN_URLS)
        is_fallback = (
            not is_known
            and _OPTIONS_SCAN_FALLBACK in resp_url
            and "symbols-options" in resp_url
        )

        if not is_known and not is_fallback:
            return

        try:
            body = await response.text()
        except Exception:
            return

        try:
            parsed = json.loads(body)
            if parsed.get("totalCount", 0) <= 1:
                return
            if is_fallback and not (parsed.get("symbols") or parsed.get("data")):
                return
        except (json.JSONDecodeError, ValueError):
            pass

        captured_responses.append({"url": resp_url, "body": body})

    browser = None
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=random.choice(_USER_AGENTS),
            locale="en-US",
            timezone_id="America/New_York",
        )

        # Stealth: hide webdriver flag
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
        """)

        page = await context.new_page()
        page.on("response", _on_response)

        await asyncio.sleep(random.uniform(0.5, 1.5))
        await page.goto(url, wait_until="networkidle", timeout=timeout)

        # Dismiss overlays
        for selector in [
            '[class*="cookie"] button',
            '[class*="consent"] button',
            'button:has-text("Accept")',
            'button:has-text("OK")',
        ]:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible(timeout=1000):
                    await btn.click()
                    await page.wait_for_timeout(random.randint(300, 600))
            except Exception:
                pass

        # Wait for async data loads
        await page.wait_for_timeout(random.randint(2500, 4000))

        await page.close()
        await context.close()

    except Exception as exc:
        logger.error("TradingView Playwright fetch failed for %s: %s", symbol, exc)
        return _empty_chain(symbol)
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass

    if not captured_responses:
        logger.warning("TradingView: no API responses captured for %s", symbol)
        return _empty_chain(symbol)

    logger.info(
        "TradingView fallback: captured %d API responses for %s",
        len(captured_responses), symbol,
    )
    return _parse_tv_to_yfinance_format(captured_responses, symbol)


def _empty_chain(symbol: str) -> dict:
    """Return an empty options chain structure."""
    return {
        "symbol": symbol,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "market_status": "closed",
        "calls": {},
        "puts": {},
    }
