"""Centralized options chain cache with TTL.

Single source of truth for options chain data across the application.
All consumers (agents, DPS, web endpoints) go through this cache.

Load procedure on miss or refresh:
  1. Fetch from yfinance (all expirations)
  2. Fetch from TradingView (overlay: overwrites matching strikes, adds missing ones)
  3. Store merged result in cache with 30-min TTL

This eliminates the need for market-open detection — data is always
the best available merge of both sources.
"""

import asyncio
import copy
import json
import logging
import time
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Default TTL: 30 minutes
_DEFAULT_TTL_SECONDS = 1800


class OptionsChainCache:
    """In-memory options chain cache with TTL and yfinance+TradingView merge."""

    def __init__(self, ttl_seconds: int = _DEFAULT_TTL_SECONDS):
        self._ttl = ttl_seconds
        self._store: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        # Reference to GreeksCalculator for yfinance processing
        self._greeks = None

    def _get_greeks(self):
        if self._greeks is None:
            from src.greeks_calculator import GreeksCalculator
            self._greeks = GreeksCalculator()
        return self._greeks

    def get(self, symbol: str) -> Optional[str]:
        """Get cached options chain JSON string for a symbol.

        Returns None on cache miss or expired entry.
        """
        with self._lock:
            entry = self._store.get(symbol)
            if entry is None:
                return None
            age = time.monotonic() - entry["cached_at"]
            if age >= self._ttl:
                logger.debug("%s: options chain cache expired (%.0fs old)", symbol, age)
                del self._store[symbol]
                return None
            logger.debug("%s: options chain cache hit (%.0fs old)", symbol, age)
            return entry["chain_json"]

    def get_or_load(self, symbol: str) -> str:
        """Get from cache or load synchronously on miss.

        Safe to call from sync contexts. If an event loop is already
        running (e.g. inside an async framework), uses a thread to avoid
        'Cannot run the event loop while another loop is running'.
        """
        cached = self.get(symbol)
        if cached is not None:
            return cached

        logger.info("%s: options chain cache miss — loading from sources", symbol)

        try:
            asyncio.get_running_loop()
            # Already inside an async context — run in a thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                chain_json = pool.submit(self._sync_refresh, symbol).result(timeout=120)
        except RuntimeError:
            # No running loop — safe to create one
            loop = asyncio.new_event_loop()
            try:
                chain_json = loop.run_until_complete(self.refresh(symbol))
            finally:
                loop.close()
        return chain_json

    def _sync_refresh(self, symbol: str) -> str:
        """Helper: run refresh() in a new event loop (for thread execution)."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.refresh(symbol))
        finally:
            loop.close()

    async def get_or_load_async(self, symbol: str) -> str:
        """Async version of get_or_load."""
        cached = self.get(symbol)
        if cached is not None:
            return cached

        logger.info("%s: options chain cache miss — loading from sources", symbol)
        return await self.refresh(symbol)

    async def refresh(self, symbol: str) -> str:
        """Force-refresh the cache for a symbol.

        Procedure:
          1. Fetch from yfinance (base layer — all expirations)
          2. Fetch from TradingView (overlay — overwrites/adds strikes)
          3. Merge and cache

        Returns the merged options chain as a JSON string.
        """
        # Step 1: yfinance
        yf_chain = await self._fetch_yfinance(symbol)

        # Step 2: TradingView overlay
        tv_chain = await self._fetch_tradingview(symbol)

        # Step 3: Merge — TV overwrites matching strikes, adds new ones
        merged = self._merge_chains(yf_chain, tv_chain, symbol)

        chain_json = json.dumps(merged, default=str)

        # Store in cache
        with self._lock:
            self._store[symbol] = {
                "chain_json": chain_json,
                "cached_at": time.monotonic(),
            }

        call_count = sum(len(strikes) for strikes in merged.get("calls", {}).values())
        put_count = sum(len(strikes) for strikes in merged.get("puts", {}).values())
        logger.info(
            "%s: options chain cached — %d call exps, %d put exps, "
            "%d total call contracts, %d total put contracts",
            symbol,
            len(merged.get("calls", {})),
            len(merged.get("puts", {})),
            call_count,
            put_count,
        )

        return chain_json

    async def refresh_all(self, symbols: list[str]) -> dict:
        """Refresh cache for all symbols. Returns summary stats."""
        success_count = 0
        error_count = 0

        for symbol in symbols:
            try:
                await self.refresh(symbol)
                success_count += 1
            except Exception as e:
                logger.error("%s: options chain refresh failed: %s", symbol, e)
                error_count += 1

        return {"success": success_count, "errors": error_count}

    def invalidate(self, symbol: str):
        """Remove a symbol from cache."""
        with self._lock:
            self._store.pop(symbol, None)

    def invalidate_all(self):
        """Clear the entire cache."""
        with self._lock:
            self._store.clear()

    def stats(self) -> dict:
        """Return cache statistics."""
        with self._lock:
            now = time.monotonic()
            entries = {}
            for sym, entry in self._store.items():
                age = now - entry["cached_at"]
                entries[sym] = {
                    "age_seconds": round(age, 1),
                    "expired": age >= self._ttl,
                }
            return {
                "ttl_seconds": self._ttl,
                "entries_count": len(self._store),
                "entries": entries,
            }

    # ------------------------------------------------------------------
    # Internal: yfinance fetch
    # ------------------------------------------------------------------

    async def _fetch_yfinance(self, symbol: str) -> dict:
        """Fetch options chain from yfinance. Returns parsed dict."""
        try:
            import yfinance as yf
        except ImportError:
            logger.error("yfinance not installed")
            return self._empty_chain(symbol)

        result = {
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "calls": {},
            "puts": {},
        }

        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info or {}
            current_price = info.get("regularMarketPrice") or info.get("currentPrice")

            if current_price is None:
                try:
                    history = ticker.history(period="5d")
                    if not history.empty:
                        current_price = float(history["Close"].iloc[-1])
                except Exception:
                    pass

            if current_price is None:
                logger.warning("%s: no current price, yfinance chain will be empty", symbol)
                return result

            try:
                expirations = ticker.options
            except Exception as exc:
                logger.error("%s: failed to fetch options expirations: %s", symbol, exc)
                return result

            if not expirations:
                logger.info("%s: no options expirations available from yfinance", symbol)
                return result

            now = datetime.now(timezone.utc)
            greeks = self._get_greeks()

            for exp_date_str in expirations:
                try:
                    exp_date = datetime.strptime(exp_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                except ValueError:
                    continue

                dte = (exp_date - now).days
                if dte < 0:
                    continue

                exp_key = exp_date.strftime("%Y%m%d")
                T = max(dte / 365.0, 1e-10)

                try:
                    chain = ticker.option_chain(exp_date_str)
                except Exception as exc:
                    logger.warning("%s: failed to fetch chain for %s: %s",
                                   symbol, exp_date_str, exc)
                    continue

                calls_dict = self._process_option_df(
                    chain.calls, "call", exp_key, current_price, T, greeks
                )
                puts_dict = self._process_option_df(
                    chain.puts, "put", exp_key, current_price, T, greeks
                )

                if calls_dict:
                    result["calls"][exp_key] = calls_dict
                if puts_dict:
                    result["puts"][exp_key] = puts_dict

            logger.info(
                "%s: yfinance chain fetched — %d call exps, %d put exps",
                symbol, len(result["calls"]), len(result["puts"]),
            )

        except Exception as exc:
            logger.exception("%s: yfinance options chain fetch failed: %s", symbol, exc)

        return result

    # ------------------------------------------------------------------
    # Internal: TradingView fetch
    # ------------------------------------------------------------------

    async def _fetch_tradingview(self, symbol: str) -> dict:
        """Fetch options chain from TradingView. Returns parsed dict."""
        try:
            from src.tv_options_chain_fetcher import fetch_tv_options_chain
            tv_result = await fetch_tv_options_chain(symbol)

            has_data = bool(tv_result.get("calls") or tv_result.get("puts"))
            if has_data:
                logger.info(
                    "%s: TradingView chain fetched — %d call exps, %d put exps",
                    symbol,
                    len(tv_result.get("calls", {})),
                    len(tv_result.get("puts", {})),
                )
                return tv_result
            else:
                logger.info("%s: TradingView returned empty chain", symbol)
                return self._empty_chain(symbol)

        except Exception as exc:
            logger.error("%s: TradingView fetch failed: %s", symbol, exc)
            return self._empty_chain(symbol)

    # ------------------------------------------------------------------
    # Internal: Merge logic
    # ------------------------------------------------------------------

    def _merge_chains(self, base: dict, overlay: dict, symbol: str) -> dict:
        """Merge two chains: overlay overwrites matching strikes, adds new ones.

        base = yfinance data (comprehensive expirations)
        overlay = TradingView data (may have strikes yfinance is missing)
        """
        merged = {
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "calls": copy.deepcopy(base.get("calls", {})),
            "puts": copy.deepcopy(base.get("puts", {})),
        }

        overlay_calls = overlay.get("calls", {})
        overlay_puts = overlay.get("puts", {})

        added_strikes = 0
        overwritten_strikes = 0
        skipped_strikes = 0

        # Merge calls
        for exp_key, strikes in overlay_calls.items():
            if exp_key not in merged["calls"]:
                merged["calls"][exp_key] = {}
            for strike_key, contract in strikes.items():
                existing = merged["calls"][exp_key].get(strike_key)
                if existing is not None:
                    # Only overwrite if overlay has non-zero bid or ask
                    if contract.get("bid", 0) > 0 or contract.get("ask", 0) > 0:
                        overwritten_strikes += 1
                        merged["calls"][exp_key][strike_key] = contract
                    else:
                        skipped_strikes += 1
                else:
                    added_strikes += 1
                    merged["calls"][exp_key][strike_key] = contract

        # Merge puts
        for exp_key, strikes in overlay_puts.items():
            if exp_key not in merged["puts"]:
                merged["puts"][exp_key] = {}
            for strike_key, contract in strikes.items():
                existing = merged["puts"][exp_key].get(strike_key)
                if existing is not None:
                    # Only overwrite if overlay has non-zero bid or ask
                    if contract.get("bid", 0) > 0 or contract.get("ask", 0) > 0:
                        overwritten_strikes += 1
                        merged["puts"][exp_key][strike_key] = contract
                    else:
                        skipped_strikes += 1
                else:
                    added_strikes += 1
                    merged["puts"][exp_key][strike_key] = contract

        if added_strikes > 0 or overwritten_strikes > 0 or skipped_strikes > 0:
            logger.info(
                "%s: merge complete — %d strikes added from TradingView, "
                "%d strikes overwritten, %d strikes skipped (overlay had zero bid/ask)",
                symbol, added_strikes, overwritten_strikes, skipped_strikes,
            )

        return merged

    # ------------------------------------------------------------------
    # Internal: Process yfinance DataFrame
    # ------------------------------------------------------------------

    @staticmethod
    def _process_option_df(df, option_type: str, exp_key: str,
                           current_price: float, T: float,
                           greeks_calc) -> dict:
        """Process a calls or puts DataFrame into strike-keyed dict."""
        import math
        import pandas as pd

        contracts = {}
        if df is None or df.empty:
            return contracts

        flag = "c" if option_type == "call" else "p"

        for _, row in df.iterrows():
            strike = row.get("strike")
            if strike is None or pd.isna(strike):
                continue

            def _is_nan(value):
                if value is None:
                    return True
                try:
                    return math.isnan(float(value))
                except (TypeError, ValueError):
                    return False

            bid = 0.0 if _is_nan(row.get("bid")) else float(row.get("bid", 0) or 0)
            ask = 0.0 if _is_nan(row.get("ask")) else float(row.get("ask", 0) or 0)
            iv = 0.0 if _is_nan(row.get("impliedVolatility")) else float(row.get("impliedVolatility", 0) or 0)

            computed_greeks = greeks_calc.compute(flag, current_price, strike, T, iv)

            ltd = row.get("lastTradeDate")
            if ltd is not None and pd.notna(ltd):
                if hasattr(ltd, 'strftime'):
                    ltd_str = ltd.strftime("%Y-%m-%dT%H:%M:%SZ")
                else:
                    ltd_str = str(ltd)
            else:
                ltd_str = None

            strike_key = f"{strike:.1f}" if strike == int(strike) else str(strike)

            contracts[strike_key] = {
                "contractSymbol": row.get("contractSymbol", ""),
                "strike": float(strike),
                "bid": bid,
                "ask": ask,
                "mid": round((bid + ask) / 2, 4) if (bid + ask) > 0 else 0.0,
                "iv": round(iv, 6),
                "delta": computed_greeks["delta"],
                "gamma": computed_greeks["gamma"],
                "theta": computed_greeks["theta"],
                "vega": computed_greeks["vega"],
                "rho": computed_greeks["rho"],
                "volume": int(row.get("volume", 0) or 0) if not _is_nan(row.get("volume")) else 0,
                "openInterest": int(row.get("openInterest", 0) or 0) if not _is_nan(row.get("openInterest")) else 0,
                "lastPrice": float(row.get("lastPrice", 0) or 0) if not _is_nan(row.get("lastPrice")) else 0.0,
                "lastTradeDate": ltd_str,
                "inTheMoney": bool(row.get("inTheMoney", False)),
                "expiration": exp_key,
                "option_type": option_type,
            }

        return contracts

    # ------------------------------------------------------------------
    # Internal: helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _empty_chain(symbol: str) -> dict:
        return {
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "calls": {},
            "puts": {},
        }


# ======================================================================
# Module-level singleton
# ======================================================================

_shared_cache: Optional[OptionsChainCache] = None
_shared_cache_lock = threading.Lock()


def get_options_chain_cache(ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> OptionsChainCache:
    """Return the process-wide shared options chain cache instance."""
    global _shared_cache
    if _shared_cache is None:
        with _shared_cache_lock:
            if _shared_cache is None:
                _shared_cache = OptionsChainCache(ttl_seconds)
    return _shared_cache
