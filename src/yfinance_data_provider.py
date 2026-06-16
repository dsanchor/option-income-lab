"""yfinance-based data provider — replaces tv_data_fetcher.py.

Fetches all 5 resource types (overview, technicals, forecast, dividends,
options chain) via yfinance and formats them as JSON strings for agent
consumption.
"""

import json
import logging
import math
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pandas as pd

from src.greeks_calculator import GreeksCalculator
from src.options_chain_cache import get_options_chain_cache
from src.technicals_calculator import TechnicalsCalculator
from src.yfinance_fetcher import YFinanceFetcher

logger = logging.getLogger(__name__)

# In-memory cache of the last yfinance options chain per symbol.
# DEPRECATED: replaced by centralized OptionsChainCache (src/options_chain_cache.py).
# Kept only for backward-compat reference; no longer populated.
_chain_cache: dict[str, dict] = {}

try:
    import yfinance as yf
except ImportError:
    yf = None

# ---------------------------------------------------------------------------
# Schema description for options chain — updated for yfinance fields
# ---------------------------------------------------------------------------
OPTIONS_CHAIN_SCHEMA_DESCRIPTION = """\
OPTIONS CHAIN FORMAT:
The options chain is a JSON object with the following structure:
{
  "symbol": "<TICKER>",
  "timestamp": "<ISO 8601 fetch time>",
  "calls": { "<YYYYMMDD>": { "<strike>": {contract}, ... } },
  "puts":  { "<YYYYMMDD>": { "<strike>": {contract}, ... } }
}
Calls and puts are grouped by expiration date (YYYYMMDD key). Each expiration
contains a dictionary of contracts keyed by strike price (e.g. "475.0", "472.5").
Contract fields:
  - contractSymbol: yfinance contract identifier (e.g. "AAPL260523C00185000")
  - strike: Strike price in dollars
  - bid: Best bid price — what you RECEIVE when you SELL (open or close) this contract
  - ask: Best ask price — what you PAY when you BUY (open or close) this contract
  - mid: Mid-price ((bid+ask)/2)
  - iv: Implied volatility (decimal, e.g. 0.364 = 36.4%)
  - delta: Delta (0 to 1 for calls, -1 to 0 for puts)
  - gamma: Gamma (rate of delta change)
  - theta: Theta (daily time decay, negative value)
  - vega: Vega (sensitivity to volatility per 1% change)
  - rho: Rho (sensitivity to interest rates)
  - volume: Trading volume for the session
  - openInterest: Total open interest
  - lastPrice: Last traded price
  - lastTradeDate: ISO 8601 timestamp of last trade
  - inTheMoney: Boolean — whether the contract is currently in the money
  - expiration: Expiration date as YYYYMMDD string
  - option_type: "call" or "put"

PREMIUM CALCULATION (CRITICAL — read carefully):
All strategies in this application SELL (write) options. When SELLING an option:
  - premium_per_contract = bid (you sell at the bid price — what the buyer pays you)
  - total_premium = bid × 100 (each contract = 100 shares)
  - premium_pct (covered call) = (bid / current_stock_price) × 100
  - premium_pct (cash-secured put) = (bid / strike) × 100
  - annualized_return = premium_pct × (365 / DTE)
Do NOT use 'ask' or 'mid' as the premium received. The 'bid' is always the
realistic premium a seller collects. Use 'mid' only for theoretical/fair-value
comparisons, never as actual premium income.

ROLL OPERATIONS (buying back + selling new):
  - buyback_cost = ask of your CURRENT contract (you BUY to close → pay the ask)
  - new_premium  = bid of the NEW target contract (you SELL to open → receive the bid)
  - net_credit   = new_premium - buyback_cost (positive = you collect, negative = you pay)

HOW TO LOOK UP A CONTRACT:
  Example: find the premium for selling an MSFT $475 call expiring 2026-04-27:
  1. calls["20260427"]["475.0"]["bid"] → that is the premium you receive when selling
  Example: find the buyback cost for your current MSFT $470 call expiring 2026-04-18:
  1. calls["20260418"]["470.0"]["ask"] → that is the cost to buy back (close) the position
  Direct key access — no searching required.

DATA INTEGRITY (MANDATORY):
  Every price you report (bid, ask, premium, buyback cost) MUST be the EXACT value
  from a contract in this JSON data. NEVER estimate, interpolate, round, or fabricate prices.
  State the full path and value: e.g., calls["20260427"]["475.0"]["ask"] = 3.00
  If the key path does not exist in the chain, state "contract not found in chain" — do NOT invent a price.

  ⚠️ COMMON ERROR: When looking up a contract, ensure the expiration key (YYYYMMDD) matches
  your intended expiration date. The chain contains MULTIPLE expirations — do NOT accidentally
  read the bid/ask from a different expiration's entry for the same strike.

LIQUIDITY GUIDANCE:
  Prefer contracts with openInterest >= 100 and volume >= 10 for realistic fills.
  Contracts with very low open interest may have wide bid/ask spreads and poor execution.

STALENESS GUIDANCE:
  Skip contracts with lastTradeDate > 3 trading days ago — prices may not reflect
  current market conditions.
"""


# ======================================================================
# Helper functions
# ======================================================================

def _format_market_cap(value) -> str:
    if value is None:
        return "N/A"
    if abs(value) >= 1e12:
        return f"${value / 1e12:.2f}T"
    if abs(value) >= 1e9:
        return f"${value / 1e9:.2f}B"
    if abs(value) >= 1e6:
        return f"${value / 1e6:.2f}M"
    return f"${value:,.0f}"


def _forecast_recommendation_label(value) -> str:
    if value is None:
        return "N/A"
    if value <= 1.5:
        return "Strong Buy"
    if value <= 2.5:
        return "Buy"
    if value <= 3.5:
        return "Hold"
    if value <= 4.5:
        return "Sell"
    return "Strong Sell"


def _safe_timestamp(val) -> Optional[str]:
    """Convert a timestamp (int or datetime) to ISO 8601 string."""
    if val is None:
        return None
    try:
        if isinstance(val, (int, float)):
            return datetime.fromtimestamp(val, tz=timezone.utc).strftime("%Y-%m-%d")
        if isinstance(val, datetime):
            return val.strftime("%Y-%m-%d")
        return str(val)
    except (ValueError, TypeError, OSError):
        return str(val)


# ======================================================================
# Main provider
# ======================================================================

class YFinanceDataProvider:
    """Fetches all data for a symbol via yfinance, formatted for agents."""

    def __init__(self, fetcher: YFinanceFetcher, config: Optional[dict] = None):
        self.fetcher = fetcher
        self.greeks = GreeksCalculator()
        self.technicals = TechnicalsCalculator()
        self._cache: Dict[str, Dict[str, Any]] = {}
        config = config or {}
        self._cache_ttl = config.get("cache_ttl", 300)

    async def fetch_all(self, symbol: str, *, force_refresh: bool = False) -> dict:
        """Fetch all data for a symbol.

        Returns dict with keys: overview, technicals, forecast, dividends, options_chain.
        Each value is a JSON string.
        """
        cached = self._cache.get(symbol)
        if not force_refresh and cached:
            age = time.monotonic() - cached["timestamp"]
            if age < self._cache_ttl:
                logger.debug("%s: returning cached data (%.0fs old)", symbol, age)
                return cached["data"]

        logger.info("Fetching all data for %s via yfinance", symbol)
        ticker = yf.Ticker(symbol)

        try:
            info = ticker.info or {}
        except Exception as exc:
            logger.error("%s: failed to fetch info: %s", symbol, exc)
            info = {}

        try:
            history = ticker.history(period="1y")
        except Exception as exc:
            logger.error("%s: failed to fetch history: %s", symbol, exc)
            history = pd.DataFrame()

        current_price = info.get("regularMarketPrice") or info.get("currentPrice") or (
            float(history["Close"].iloc[-1]) if not history.empty else None
        )

        result = {
            "overview": self._build_overview(info, current_price),
            "technicals": self._build_technicals(history, info),
            "forecast": self._build_forecast(info, ticker),
            "dividends": self._build_dividends(info, ticker),
            "options_chain": self._build_options_chain(ticker, current_price, symbol),
        }

        self._cache[symbol] = {"data": result, "timestamp": time.monotonic()}
        return result

    # ------------------------------------------------------------------
    # Overview
    # ------------------------------------------------------------------
    def _build_overview(self, info: dict, current_price: Optional[float] = None) -> str:
        """Build overview JSON string from ticker.info."""
        name = info.get("longName") or info.get("shortName", "")
        symbol = info.get("symbol", "")
        exchange = info.get("exchange", "")

        # yfinance dividendYield is percentage-form (0.88 = 0.88%), divide by 100
        raw_div_yield = info.get("dividendYield")
        div_yield_decimal = raw_div_yield / 100.0 if raw_div_yield is not None else None
        div_yield_pct = raw_div_yield if raw_div_yield is not None else None

        fundamentals = {}
        _add = lambda key, label, val, fmt=None: _add_field(fundamentals, key, label, val, fmt)

        _add("market_cap_basic", "Market Cap", info.get("marketCap"),
             _format_market_cap(info.get("marketCap")))
        _add("price_earnings_ttm", "P/E Ratio (TTM)", info.get("trailingPE"),
             f"{info['trailingPE']:.2f}" if info.get("trailingPE") else None)
        _add("price_earnings_forward", "P/E Ratio (Forward)", info.get("forwardPE"),
             f"{info['forwardPE']:.2f}" if info.get("forwardPE") else None)
        _add("earnings_per_share_basic_ttm", "EPS (TTM)", info.get("trailingEps"),
             f"${info['trailingEps']:.2f}" if info.get("trailingEps") else None)
        _add("earnings_per_share_forward", "EPS (Forward)", info.get("forwardEps"),
             f"${info['forwardEps']:.2f}" if info.get("forwardEps") else None)
        _add("dividends_yield", "Dividend Yield (%)", div_yield_pct,
             f"{div_yield_pct:.2f}%" if div_yield_pct is not None else None)
        _add("sector", "Sector", info.get("sector"))
        _add("industry", "Industry", info.get("industry"))
        _add("number_of_employees", "Employees", info.get("fullTimeEmployees"),
             f"{info['fullTimeEmployees']:,}" if info.get("fullTimeEmployees") else None)
        _add("beta_1_year", "Beta (1Y)", info.get("beta"),
             f"{info['beta']:.2f}" if info.get("beta") else None)
        _add("current_price", "Current Price", current_price,
             f"${current_price:,.2f}" if current_price else None)
        _add("52w_high", "52-Week High", info.get("fiftyTwoWeekHigh"),
             f"${info['fiftyTwoWeekHigh']:,.2f}" if info.get("fiftyTwoWeekHigh") else None)
        _add("52w_low", "52-Week Low", info.get("fiftyTwoWeekLow"),
             f"${info['fiftyTwoWeekLow']:,.2f}" if info.get("fiftyTwoWeekLow") else None)

        earnings_ts = info.get("earningsTimestampStart")
        _add("earnings_release_next_date_fq", "Next Earnings Date", earnings_ts,
             _safe_timestamp(earnings_ts))

        rec_mark = info.get("recommendationMean")
        _add("recommendation_mark", "Analyst Rating (1=Strong Buy, 5=Strong Sell)", rec_mark,
             f"{rec_mark:.2f}" if rec_mark else None)

        _add("total_shares_outstanding", "Shares Outstanding", info.get("sharesOutstanding"),
             f"{info['sharesOutstanding']:,}" if info.get("sharesOutstanding") else None)
        _add("web_site_url", "Website", info.get("website"))

        result = {
            "name": name,
            "ticker": symbol,
            "exchange": exchange,
            "fundamentals": fundamentals,
        }
        return json.dumps(result, default=str)

    # ------------------------------------------------------------------
    # Technicals
    # ------------------------------------------------------------------
    def _build_technicals(self, history: pd.DataFrame, info: dict) -> str:
        """Build technicals JSON string from OHLCV history."""
        tech = self.technicals.compute_all(history)

        # Add metadata
        tech["name"] = info.get("longName") or info.get("shortName", "")
        tech["ticker"] = info.get("symbol", "")
        tech["exchange"] = info.get("exchange", "")

        return json.dumps(tech, default=str)

    # ------------------------------------------------------------------
    # Forecast
    # ------------------------------------------------------------------
    def _build_forecast(self, info: dict, ticker) -> str:
        """Build forecast JSON string from ticker analyst data."""
        name = info.get("longName") or info.get("shortName", "")
        symbol = info.get("symbol", "")
        exchange = info.get("exchange", "")
        current_price = info.get("regularMarketPrice") or info.get("currentPrice")

        # Price targets
        price_target = {}
        target_fields = [
            ("targetMeanPrice", "price_target_average", "Average Price Target"),
            ("targetHighPrice", "price_target_high", "High Price Target"),
            ("targetLowPrice", "price_target_low", "Low Price Target"),
            ("targetMedianPrice", "price_target_median", "Median Price Target"),
        ]
        for yf_key, our_key, label in target_fields:
            val = info.get(yf_key)
            if val is not None:
                price_target[our_key] = {
                    "label": label, "value": val, "formatted": f"${val:,.2f}",
                }

        avg_target = info.get("targetMeanPrice")
        if avg_target is not None and current_price is not None and current_price > 0:
            upside_pct = ((avg_target - current_price) / current_price) * 100
            price_target["upside_pct"] = round(upside_pct, 2)
            price_target["upside_direction"] = "Upside" if upside_pct >= 0 else "Downside"

        # Analyst ratings
        analyst_rating = {}
        rec_mark = info.get("recommendationMean")
        if rec_mark is not None:
            analyst_rating["overall_rating"] = {
                "value": rec_mark,
                "label": _forecast_recommendation_label(rec_mark),
            }

        num_analysts = info.get("numberOfAnalystOpinions")
        if num_analysts is not None:
            analyst_rating["recommendation_total"] = {
                "label": "Total Analysts", "value": int(num_analysts),
            }

        # Recommendations summary from ticker
        try:
            rec_summary = ticker.recommendations_summary
            if rec_summary is not None and not rec_summary.empty:
                latest = rec_summary.iloc[0]
                buy = int(latest.get("buy", 0) or 0) + int(latest.get("strongBuy", 0) or 0)
                hold = int(latest.get("hold", 0) or 0)
                sell = int(latest.get("sell", 0) or 0) + int(latest.get("strongSell", 0) or 0)
                total = buy + hold + sell
                if total > 0:
                    analyst_rating["recommendation_buy"] = {"label": "Buy", "value": buy}
                    analyst_rating["recommendation_hold"] = {"label": "Hold", "value": hold}
                    analyst_rating["recommendation_sell"] = {"label": "Sell", "value": sell}
                    analyst_rating["distribution"] = {
                        "buy_pct": round(buy / total * 100, 1),
                        "hold_pct": round(hold / total * 100, 1),
                        "sell_pct": round(sell / total * 100, 1),
                    }
        except Exception as exc:
            logger.debug("Could not fetch recommendations_summary: %s", exc)

        result = {
            "name": name,
            "ticker": symbol,
            "exchange": exchange,
            "current_price": current_price,
            "price_target": price_target if price_target else None,
            "analyst_rating": analyst_rating if analyst_rating else None,
        }
        return json.dumps(result, default=str)

    # ------------------------------------------------------------------
    # Dividends
    # ------------------------------------------------------------------
    def _build_dividends(self, info: dict, ticker) -> str:
        """Build dividends JSON string."""
        name = info.get("longName") or info.get("shortName", "")
        symbol = info.get("symbol", "")
        exchange = info.get("exchange", "")

        dividends = {}

        # yfinance dividendYield is percentage-form: divide by 100 for decimal
        raw_yield = info.get("dividendYield")
        if raw_yield is not None:
            dividends["dividends_yield"] = {
                "label": "Dividend Yield (%)",
                "value": raw_yield,
                "formatted": f"{raw_yield:.2f}%",
            }

        div_rate = info.get("dividendRate")
        if div_rate is not None:
            dividends["dps_common_stock_prim_issue_fy"] = {
                "label": "Dividends Per Share (FY)",
                "value": div_rate,
                "formatted": f"${div_rate:.2f}",
            }

        payout = info.get("payoutRatio")
        if payout is not None:
            payout_pct = payout * 100 if payout < 1.5 else payout
            dividends["dividend_payout_ratio_ttm"] = {
                "label": "Payout Ratio (TTM %)",
                "value": payout_pct,
                "formatted": f"{payout_pct:.2f}%",
            }

        ex_date = info.get("exDividendDate")
        if ex_date is not None:
            dividends["ex_dividend_date_recent"] = {
                "label": "Ex-Dividend Date (Recent)",
                "value": ex_date,
                "formatted": _safe_timestamp(ex_date),
            }

        last_div = info.get("lastDividendValue")
        if last_div is not None:
            dividends["dps_common_stock_prim_issue_fq"] = {
                "label": "Dividends Per Share (FQ)",
                "value": last_div,
                "formatted": f"${last_div:.2f}",
            }

        # EPS and P/E for context
        eps = info.get("trailingEps")
        if eps is not None:
            dividends["earnings_per_share_basic_ttm"] = {
                "label": "EPS (TTM)", "value": eps, "formatted": f"${eps:.2f}",
            }

        pe = info.get("trailingPE")
        if pe is not None:
            dividends["price_earnings_ttm"] = {
                "label": "P/E Ratio (TTM)", "value": pe, "formatted": f"{pe:.2f}",
            }

        mkt_cap = info.get("marketCap")
        if mkt_cap is not None:
            dividends["market_cap_basic"] = {
                "label": "Market Cap", "value": mkt_cap,
                "formatted": _format_market_cap(mkt_cap),
            }

        # Dividend history stats from series
        try:
            div_series = ticker.dividends
            if div_series is not None and not div_series.empty:
                # Compute consecutive growth years from dividend history
                annual = div_series.resample("YE").sum()
                annual = annual[annual > 0]
                if len(annual) >= 2:
                    growth_years = 0
                    for i in range(len(annual) - 1, 0, -1):
                        if annual.iloc[i] > annual.iloc[i - 1]:
                            growth_years += 1
                        else:
                            break
                    if growth_years > 0:
                        dividends["continuous_dividend_growth"] = {
                            "label": "Consecutive Years Growing",
                            "value": growth_years,
                            "formatted": f"{growth_years} years",
                        }
        except Exception as exc:
            logger.debug("Could not compute dividend history stats: %s", exc)

        result = {
            "name": name,
            "ticker": symbol,
            "exchange": exchange,
            "dividends": dividends,
        }
        return json.dumps(result, default=str)

    # ------------------------------------------------------------------
    # Options chain
    # ------------------------------------------------------------------
    def _build_options_chain(self, ticker, current_price: Optional[float],
                             symbol: str) -> str:
        """Get options chain from the centralized cache.

        The cache handles the load procedure (yfinance + TradingView merge)
        transparently on miss. TTL is 30 minutes.
        """
        cache = get_options_chain_cache()
        return cache.get_or_load(symbol)

    def _process_option_df(self, df: pd.DataFrame, option_type: str,
                           exp_key: str, current_price: float,
                           T: float) -> dict:
        """Process a calls or puts DataFrame into strike-keyed dict."""
        contracts = {}

        if df is None or df.empty:
            return contracts

        flag = "c" if option_type == "call" else "p"

        for _, row in df.iterrows():
            strike = row.get("strike")
            if strike is None or pd.isna(strike):
                continue

            bid = 0.0 if _is_nan(row.get("bid")) else float(row.get("bid", 0) or 0)
            ask = 0.0 if _is_nan(row.get("ask")) else float(row.get("ask", 0) or 0)
            iv = 0.0 if _is_nan(row.get("impliedVolatility")) else float(row.get("impliedVolatility", 0) or 0)

            # Compute Greeks
            greeks = self.greeks.compute(flag, current_price, strike, T, iv)

            # Format lastTradeDate
            ltd = row.get("lastTradeDate")
            if ltd is not None and pd.notna(ltd):
                if isinstance(ltd, pd.Timestamp):
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
                "delta": greeks["delta"],
                "gamma": greeks["gamma"],
                "theta": greeks["theta"],
                "vega": greeks["vega"],
                "rho": greeks["rho"],
                "volume": int(row.get("volume", 0) or 0) if not _is_nan(row.get("volume")) else 0,
                "openInterest": int(row.get("openInterest", 0) or 0) if not _is_nan(row.get("openInterest")) else 0,
                "lastPrice": float(row.get("lastPrice", 0) or 0) if not _is_nan(row.get("lastPrice")) else 0.0,
                "lastTradeDate": ltd_str,
                "inTheMoney": bool(row.get("inTheMoney", False)),
                "expiration": exp_key,
                "option_type": option_type,
            }

        return contracts


# ======================================================================
# Helper
# ======================================================================

def _is_nan(value) -> bool:
    """Check if a value is NaN (handles None, float NaN, numpy NaN)."""
    if value is None:
        return True
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return False

def _add_field(d: dict, key: str, label: str, value, formatted=None):
    """Add a field to a fundamentals dict if value is not None."""
    if value is None:
        return
    d[key] = {
        "label": label,
        "value": value,
        "formatted": formatted if formatted else str(value),
    }


# ======================================================================
# Factory
# ======================================================================

def create_provider(config: Optional[dict] = None) -> YFinanceDataProvider:
    """Factory function — drop-in replacement for create_fetcher()."""
    fetcher = YFinanceFetcher()
    return YFinanceDataProvider(fetcher, config)


# ======================================================================
# Shared singleton — centralized in-memory cache across all callers
# ======================================================================

_shared_provider: Optional[YFinanceDataProvider] = None
_shared_provider_lock = __import__("threading").Lock()


def get_shared_provider(config: Optional[dict] = None) -> YFinanceDataProvider:
    """Return a process-wide shared provider instance.

    All callers (web app, scheduler, agents) share the same in-memory
    cache when using this function.  The first call initializes the
    singleton; subsequent calls return the same instance (config param
    is ignored after initialization).
    """
    global _shared_provider
    if _shared_provider is None:
        with _shared_provider_lock:
            if _shared_provider is None:
                _shared_provider = create_provider(config)
    return _shared_provider
