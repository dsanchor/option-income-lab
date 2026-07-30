"""yfinance data fetcher with rate limiting and retry logic.

Wraps yfinance to fetch stock fundamentals, dividend history, and
OHLCV price data for the DGI Screener pipeline.
"""

import logging
import time
from typing import Any, Callable, Dict, List, Optional

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


class YFinanceFetcher:
    """Rate-limited yfinance wrapper with exponential-backoff retries."""

    def __init__(
        self,
        requests_per_minute: int = 60,
        max_retries: int = 3,
    ) -> None:
        self.requests_per_minute = requests_per_minute
        self.max_retries = max_retries
        self._min_interval = 60.0 / requests_per_minute
        self._last_request_time: float = 0.0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _rate_limit(self) -> None:
        """Block until the minimum inter-request interval has elapsed."""
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.monotonic()

    def _fetch_with_retry(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch a single ticker with exponential backoff on failure."""
        for attempt in range(1, self.max_retries + 1):
            try:
                self._rate_limit()
                ticker = yf.Ticker(symbol)

                info = ticker.info
                if not info or (
                    info.get("regularMarketPrice") is None
                    and info.get("currentPrice") is None
                ):
                    logger.warning("%s: no market data returned", symbol)
                    return None

                dividends: pd.Series = ticker.dividends
                history: pd.DataFrame = ticker.history(period="1y")

                if history.empty:
                    logger.warning("%s: empty price history", symbol)
                    return None

                return {
                    "info": info,
                    "dividends": dividends,
                    "history": history,
                }

            except Exception as exc:  # noqa: BLE001
                wait = 2 ** attempt
                logger.warning(
                    "%s: attempt %d/%d failed (%s) — retrying in %ds",
                    symbol,
                    attempt,
                    self.max_retries,
                    exc,
                    wait,
                )
                if attempt < self.max_retries:
                    time.sleep(wait)

        logger.error("%s: all %d attempts exhausted", symbol, self.max_retries)
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_ticker_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch info, dividends, and 1-year daily OHLCV for *symbol*.

        Returns ``{"info": dict, "dividends": Series, "history": DataFrame}``
        or ``None`` on failure.
        """
        logger.info("Fetching data for %s", symbol)
        return self._fetch_with_retry(symbol)

    def get_batch_data(
        self,
        symbols: List[str],
        callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Fetch data for multiple symbols with optional progress callback.

        Parameters
        ----------
        symbols:
            List of ticker symbols to fetch.
        callback:
            Optional ``callback(symbol, current_index, total)`` invoked
            after each symbol is processed (success or failure).

        Returns
        -------
        dict mapping symbol → ticker data dict (failures omitted).
        """
        results: Dict[str, Dict[str, Any]] = {}
        total = len(symbols)

        for idx, symbol in enumerate(symbols, 1):
            data = self.get_ticker_data(symbol)
            if data is not None:
                results[symbol] = data

            if callback is not None:
                callback(symbol, idx, total)

        logger.info(
            "Batch complete: %d/%d symbols fetched successfully",
            len(results),
            total,
        )
        return results
