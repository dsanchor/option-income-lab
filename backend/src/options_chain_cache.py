"""Centralized options chain cache with stale-while-revalidate semantics.

Single source of truth for options chain data across the application.
All consumers (agents, DPS, web endpoints) go through this cache.

Load procedure on miss or refresh:
  1. Fetch from yfinance (all expirations)
  2. Fetch from TradingView (overlay: overwrites matching strikes, adds missing ones)
  3. Merge the two fresh sources using explicit, field-level precedence
  4. Merge the result against the previously cached ("last known good") chain so
     that a freshly fetched zero/null/NaN quote field never clobbers a previously
     stored valid non-zero value for the same contract (same expiration + strike
     + option type)
  5. Drop any expiration whose actual contract expiration date has passed
  6. Store the merged result in cache

TTL controls *freshness* (whether a background refetch is warranted), not
*availability* — cached entries are never deleted purely because they aged
past the TTL. Last-known-good data stays readable indefinitely so consumers
never regress to zeros just because a refresh cycle was missed or a source
temporarily returned bad data. This eliminates the need for market-open
detection — data is always the best available merge of both sources plus
prior history.
"""

import asyncio
import copy
import concurrent.futures
import json
import logging
import math
import time
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.options_math import robust_mid

logger = logging.getLogger(__name__)

# Default TTL: 30 minutes. Controls staleness/refetch decisions only —
# an expired entry is NOT evicted, it just becomes eligible for background
# refresh on next access (stale-while-revalidate).
_DEFAULT_TTL_SECONDS = 1800

# Per-symbol refresh timeout to prevent hung jobs from blocking the queue
_REFRESH_SYMBOL_TIMEOUT = 90

# Contract fields treated as "quote/market" data: a freshly fetched value of
# zero/None/NaN for one of these fields is considered missing/invalid and
# must NOT overwrite a previously stored valid (non-zero) value for the same
# contract. Fields intentionally excluded from this list (volume,
# openInterest, lastTradeDate, inTheMoney, contractSymbol, strike,
# expiration, option_type) legitimately change from fetch to fetch and zero
# is a semantically valid value for them (e.g. zero volume on a quiet day),
# so they always take the freshest fetched value.
_QUOTE_FIELDS = (
    "bid", "ask", "mid", "iv",
    "delta", "gamma", "theta", "vega", "rho",
    "lastPrice",
)


def _is_invalid_quote_value(value: Any) -> bool:
    """True if `value` is missing/invalid for a quote/market field.

    Missing/invalid means: None, NaN, non-numeric, or exactly zero. Zero is
    treated as invalid for these particular fields because the observed bug
    is exactly "bid/ask/iv/greeks show up as 0" whenever a source fails to
    return real data — it is not a legitimate steady-state value for these
    fields the way it is for e.g. volume or openInterest.
    """
    if value is None:
        return True
    try:
        f = float(value)
    except (TypeError, ValueError):
        return True
    if math.isnan(f):
        return True
    return f == 0.0


def _merge_contract_fields(old: Optional[dict], new: dict) -> dict:
    """Field-level last-known-good merge of a single contract.

    Non-quote fields always come from `new` (the freshest fetch). For each
    field in `_QUOTE_FIELDS`, keep `old`'s value when `new`'s value is
    invalid (zero/None/NaN) AND `old` has a valid value for that field.
    If `old` is None (no prior contract at this expiration+strike), `new`
    is returned unchanged — first-fetch zeros are not fabricated.
    """
    if old is None:
        return new

    merged = dict(new)
    for field in _QUOTE_FIELDS:
        new_val = new.get(field)
        if _is_invalid_quote_value(new_val):
            old_val = old.get(field)
            if not _is_invalid_quote_value(old_val):
                merged[field] = old_val
    return merged


def _prune_expired_expirations(chain: dict, symbol: str) -> dict:
    """Drop expiration buckets whose actual contract expiration date has
    already passed. This is a business-logic filter (option contracts that
    are no longer tradable), distinct from cache TTL/staleness — it applies
    regardless of how the entry got here (fresh fetch or carried forward
    from last-known-good merge).
    """
    today_key = datetime.now(timezone.utc).strftime("%Y%m%d")
    dropped = 0
    for side in ("calls", "puts"):
        bucket = chain.get(side, {})
        for exp_key in list(bucket.keys()):
            # exp_key is expected to be YYYYMMDD; only prune keys we can
            # confidently parse as dates, leave anything unexpected alone.
            if len(exp_key) == 8 and exp_key.isdigit() and exp_key < today_key:
                del bucket[exp_key]
                dropped += 1
    if dropped:
        logger.debug("%s: pruned %d expired expiration bucket(s) from chain", symbol, dropped)
    return chain


class OptionsChainCache:
    """In-memory options chain cache with TTL and yfinance+TradingView merge."""

    def __init__(self, ttl_seconds: int = _DEFAULT_TTL_SECONDS):
        self._ttl = ttl_seconds
        self._store: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        # Reference to GreeksCalculator for yfinance processing
        self._greeks = None
        # Symbols with a background stale-while-revalidate refresh in flight,
        # to avoid piling up duplicate concurrent refreshes for one symbol.
        self._refresh_in_progress: set = set()

    def _get_greeks(self):
        if self._greeks is None:
            from src.greeks_calculator import GreeksCalculator
            self._greeks = GreeksCalculator()
        return self._greeks

    def _is_stale(self, entry: Dict[str, Any]) -> bool:
        age = time.monotonic() - entry["cached_at"]
        return age >= self._ttl

    def get(self, symbol: str) -> Optional[str]:
        """Get cached options chain JSON string for a symbol.

        Returns None only on a true cache miss (never fetched before).
        Entries are never evicted purely due to TTL expiry — last-known-good
        data remains readable indefinitely. Use `is_stale()` to check
        whether a background refresh is warranted.
        """
        with self._lock:
            entry = self._store.get(symbol)
            if entry is None:
                return None
            if self._is_stale(entry):
                age = time.monotonic() - entry["cached_at"]
                logger.debug(
                    "%s: options chain cache stale (%.0fs old) — serving last-known-good",
                    symbol, age,
                )
            return entry["chain_json"]

    def is_stale(self, symbol: str) -> bool:
        """True if the cached entry is missing or past its freshness TTL."""
        with self._lock:
            entry = self._store.get(symbol)
            if entry is None:
                return True
            return self._is_stale(entry)

    def get_or_load(self, symbol: str) -> str:
        """Get from cache or load synchronously on miss.

        On a true cache miss, blocks and fetches. On a stale-but-present
        entry, returns the last-known-good data immediately (stale data
        stays readable; refetching is driven by the scheduled refresh job).
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
        """Async version of get_or_load.

        Stale-while-revalidate: a true cache miss blocks and fetches. A
        stale-but-present entry is returned immediately, with a background
        refresh kicked off (at most one in flight per symbol) so future
        calls see fresher data without making this call pay the latency.
        """
        cached = self.get(symbol)
        if cached is not None:
            if self.is_stale(symbol):
                self._schedule_background_refresh(symbol)
            return cached

        logger.info("%s: options chain cache miss — loading from sources", symbol)
        return await self.refresh(symbol)

    def _schedule_background_refresh(self, symbol: str) -> None:
        """Fire-and-forget refresh for stale entries, deduped per symbol."""
        with self._lock:
            if symbol in self._refresh_in_progress:
                return
            self._refresh_in_progress.add(symbol)

        async def _run():
            try:
                await self.refresh(symbol)
            except Exception as exc:
                logger.warning("%s: background stale-while-revalidate refresh failed: %s", symbol, exc)
            finally:
                with self._lock:
                    self._refresh_in_progress.discard(symbol)

        try:
            asyncio.create_task(_run())
        except RuntimeError:
            # No running loop to schedule on (shouldn't happen when called
            # from get_or_load_async, but fail safe rather than crash).
            with self._lock:
                self._refresh_in_progress.discard(symbol)

    async def refresh(self, symbol: str) -> str:
        """Force-refresh the cache for a symbol.

        Procedure:
          1. Fetch from yfinance (base layer — all expirations)
          2. Fetch from TradingView (overlay — overwrites/adds strikes)
          3. Merge the two fresh sources (explicit source precedence)
          4. Merge against the previously cached chain (last-known-good):
             a freshly fetched zero/null/NaN quote field never overwrites a
             previously stored valid non-zero value for the same contract
          5. Drop expiration buckets whose actual contract date has passed
          6. Cache the result

        Returns the merged options chain as a JSON string.
        """
        # Step 1: yfinance
        yf_chain = await self._fetch_yfinance(symbol)

        # Step 2: TradingView overlay
        tv_chain = await self._fetch_tradingview(symbol)

        # Step 3: Merge — explicit source precedence (TV overlay preferred,
        # field-level, over yfinance base) between the two fresh fetches.
        merged = self._merge_chains(yf_chain, tv_chain, symbol)

        # Step 4: Last-known-good merge against whatever was previously
        # cached, so a bad/empty fetch this cycle can't regress good data
        # from a prior cycle.
        previous_chain = self._load_previous_chain(symbol)
        if previous_chain is not None:
            merged = self._merge_last_known_good(merged, previous_chain, symbol)

        # Step 5: drop expirations that have actually passed (contract
        # expiry, distinct from cache TTL/staleness).
        merged = _prune_expired_expirations(merged, symbol)

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
        """Refresh cache for all symbols with per-symbol timeout. Returns summary stats."""
        success_count = 0
        error_count = 0

        # Use ThreadPoolExecutor with bounded concurrency to handle symbols.
        # Each thread runs _sync_refresh which creates its own event loop.
        # NOTE: we deliberately do NOT use `with ... as executor` here, because
        # the context manager exit calls shutdown(wait=True), which would block
        # forever on a truly hung yfinance/requests call — reintroducing the very
        # deadlock this method guards against. Instead we enforce a per-symbol
        # timeout and then shutdown(wait=False, cancel_futures=True) so hung
        # threads are abandoned (they linger harmlessly) without blocking.
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        try:
            futures_map = {
                symbol: executor.submit(self._sync_refresh, symbol)
                for symbol in symbols
            }

            # Process each symbol with timeout enforcement
            for symbol, future in futures_map.items():
                try:
                    future.result(timeout=_REFRESH_SYMBOL_TIMEOUT)
                    success_count += 1
                except concurrent.futures.TimeoutError:
                    logger.warning(
                        "%s: options chain refresh timed out after %d seconds "
                        "(abandoning, will retry next run)",
                        symbol, _REFRESH_SYMBOL_TIMEOUT
                    )
                    error_count += 1
                except Exception as e:
                    logger.error("%s: options chain refresh failed: %s", symbol, e)
                    error_count += 1
        finally:
            # Never block on hung worker threads: don't wait, cancel pending.
            executor.shutdown(wait=False, cancel_futures=True)

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
        """Return cache statistics.

        Note: entries are never evicted for being past the TTL — `expired`
        here means "stale" (eligible for a background refetch), not "gone".
        """
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
    # Internal: last-known-good merge against previously cached chain
    # ------------------------------------------------------------------

    def _load_previous_chain(self, symbol: str) -> Optional[dict]:
        """Return the previously cached chain (parsed dict), if any."""
        with self._lock:
            entry = self._store.get(symbol)
        if entry is None:
            return None
        try:
            return json.loads(entry["chain_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            logger.warning("%s: failed to parse previously cached chain — ignoring", symbol)
            return None

    def _merge_last_known_good(self, fresh: dict, previous: dict, symbol: str) -> dict:
        """Merge a freshly fetched+source-merged chain against the last
        cached chain so invalid/zero fields in `fresh` fall back to
        `previous`'s valid values, contract by contract (matched on
        expiration + strike + option side — the dict nesting already keys
        on exactly that).
        """
        preserved = 0

        for side in ("calls", "puts"):
            fresh_bucket = fresh.setdefault(side, {})
            prev_bucket = previous.get(side, {})

            for exp_key, prev_strikes in prev_bucket.items():
                fresh_strikes = fresh_bucket.setdefault(exp_key, {})
                for strike_key, prev_contract in prev_strikes.items():
                    fresh_contract = fresh_strikes.get(strike_key)
                    if fresh_contract is None:
                        # Fresh fetch has no data at all for this contract
                        # this cycle (e.g. source omitted it) — carry the
                        # last-known-good contract forward as-is.
                        fresh_strikes[strike_key] = prev_contract
                        preserved += 1
                        continue
                    merged_contract = _merge_contract_fields(prev_contract, fresh_contract)
                    if merged_contract is not fresh_contract:
                        preserved += 1
                    fresh_strikes[strike_key] = merged_contract

        if preserved:
            logger.info(
                "%s: last-known-good merge preserved/backfilled %d contract(s) "
                "from previous cache where fresh fetch was zero/missing",
                symbol, preserved,
            )

        return fresh

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
        """Merge two freshly fetched chains with explicit, deterministic
        source precedence: TradingView (`overlay`) is preferred over
        yfinance (`base`) field-by-field, but an invalid/zero overlay field
        never overwrites a valid non-zero base field, and vice versa when
        adding brand-new contracts the overlay provides that base doesn't
        have.

        base = yfinance data (comprehensive expirations)
        overlay = TradingView data (may have strikes yfinance is missing,
                  but typically only for a handful of near-term expirations)
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
        merged_strikes = 0

        for side, overlay_bucket in (("calls", overlay_calls), ("puts", overlay_puts)):
            merged_bucket = merged[side]
            for exp_key, strikes in overlay_bucket.items():
                if exp_key not in merged_bucket:
                    merged_bucket[exp_key] = {}
                for strike_key, contract in strikes.items():
                    existing = merged_bucket[exp_key].get(strike_key)
                    if existing is not None:
                        # Field-level precedence: TV (contract) preferred,
                        # falling back to yfinance (existing) per-field only
                        # where TV's value is invalid/zero.
                        merged_bucket[exp_key][strike_key] = _merge_contract_fields(existing, contract)
                        merged_strikes += 1
                    else:
                        added_strikes += 1
                        merged_bucket[exp_key][strike_key] = contract

        if added_strikes > 0 or merged_strikes > 0:
            logger.info(
                "%s: source merge complete — %d strikes added from TradingView, "
                "%d strikes field-merged (TV preferred, yfinance fallback for zero/missing fields)",
                symbol, added_strikes, merged_strikes,
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
            last_price = float(row.get("lastPrice", 0) or 0) if not _is_nan(row.get("lastPrice")) else 0.0

            contracts[strike_key] = {
                "contractSymbol": row.get("contractSymbol", ""),
                "strike": float(strike),
                "bid": bid,
                "ask": ask,
                "mid": robust_mid(bid, ask, last_price),
                "iv": round(iv, 6),
                "delta": computed_greeks["delta"],
                "gamma": computed_greeks["gamma"],
                "theta": computed_greeks["theta"],
                "vega": computed_greeks["vega"],
                "rho": computed_greeks["rho"],
                "volume": int(row.get("volume", 0) or 0) if not _is_nan(row.get("volume")) else 0,
                "openInterest": int(row.get("openInterest", 0) or 0) if not _is_nan(row.get("openInterest")) else 0,
                "lastPrice": last_price,
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
