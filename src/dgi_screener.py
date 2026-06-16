"""DGI Stock Screener — daily programmatic screening of S&P 500 for
dividend growth investing opportunities with technical timing.

100% programmatic (no LLM). Uses yfinance for data, custom metrics for scoring.
"""

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Yahoo Finance exchange code mapping
EXCHANGE_MAP = {
    "NYQ": "NYSE",
    "NMS": "NASDAQ",
    "NGM": "NASDAQ",   # NASDAQ Global Market
    "NCM": "NASDAQ",   # NASDAQ Capital Market
    "NIM": "NASDAQ",   # NASDAQ Intermarket
    "PCX": "NYSE",     # NYSE Arca
    "ASE": "AMEX",     # NYSE American (AMEX)
    "BTS": "NYSE",     # BATS → now Cboe, trades NYSE-listed
    "YHD": "NYSE",     # Yahoo default
}


def _normalize_exchange(raw: str) -> str:
    """Map yfinance exchange codes to normalized exchange names."""
    return EXCHANGE_MAP.get(raw, raw if raw else "NYSE")


def _load_symbols(symbols_str: str) -> list[str]:
    """Parse ticker symbols from comma-separated config string."""
    if not symbols_str:
        logger.warning("No symbols configured for DGI screener")
        return []
    symbols = [s.strip().upper() for s in symbols_str.split(",") if s.strip()]
    logger.info("Loaded %d symbols from config", len(symbols))
    return symbols





def analyze_single_symbol(symbol: str, filters: dict = None) -> dict:
    """Run the DGI scoring pipeline for a single symbol (no CosmosDB writes).

    Returns a dict with metrics, technicals, quality score breakdown,
    category, and entry tag — or an ``"error"`` key on failure.

    Args:
        symbol: Ticker symbol to analyze.
        filters: Optional filter overrides (from config.yaml dgi_screener.filters).
    """
    from .yfinance_fetcher import YFinanceFetcher
    from . import dgi_metrics

    symbol = symbol.strip().upper()
    if not symbol:
        return {"error": "No symbol provided"}

    fetcher = YFinanceFetcher()
    item = fetcher.get_ticker_data(symbol)
    if item is None:
        return {"error": f"No data available for {symbol}. Verify the ticker symbol is correct."}

    info = item.get("info", {})
    dividends = item.get("dividends")
    history = item.get("history")

    has_dividends = dividends is not None and not (
        hasattr(dividends, "empty") and dividends.empty
    )

    years = dgi_metrics.calculate_years_consecutive_increases(dividends) if has_dividends else 0
    cagr = dgi_metrics.calculate_dividend_cagr(dividends) if has_dividends else 0.0

    # yfinance dividendYield is ALWAYS percentage-form (0.88 = 0.88%, 2.42 = 2.42%)
    # Convert to decimal (0.0088, 0.0242) for internal use.
    raw_yield = info.get("dividendYield") or 0
    raw_yield = raw_yield / 100.0

    metrics = {
        "dividend_yield": raw_yield,
        "dividend_cagr_5y": cagr,
        "years_consecutive_increases": years,
        "payout_ratio": info.get("payoutRatio") or 0,
        "pe_ratio": info.get("trailingPE") or 0,
        "forward_pe": info.get("forwardPE") or 0,
        "debt_to_equity": (info.get("debtToEquity") or 0) / 100
        if info.get("debtToEquity") and info["debtToEquity"] > 10
        else (info.get("debtToEquity") or 0),
        "roe": info.get("returnOnEquity") or 0,
        "market_cap": info.get("marketCap") or 0,
        "current_price": info.get("currentPrice")
        or info.get("regularMarketPrice")
        or 0,
        "sector": info.get("sector", ""),
        "exchange": _normalize_exchange(info.get("exchange", "")),
    }

    # Technical timing
    if history is None or (hasattr(history, "empty") and history.empty):
        technicals = {"score": 0, "sub_scores": {}}
    else:
        technicals = dgi_metrics.calculate_technical_timing_score(
            history["Close"].values,
            history["High"].values,
            history["Low"].values,
            metrics.get("current_price", 0),
        )
        if not isinstance(technicals, dict):
            technicals = {"score": technicals}

    # Quality score with detailed breakdown
    quality_detail = dgi_metrics.calculate_quality_score_detailed(metrics, technicals)
    quality_score = quality_detail["total"]

    # Entry tag
    tech_timing = technicals.get("score", 0)
    if tech_timing >= 70:
        entry_tag = "Strong Buy"
    elif tech_timing >= 50:
        entry_tag = "Buy"
    elif tech_timing >= 35:
        entry_tag = "Accumulate"
    elif tech_timing >= 20:
        entry_tag = "Hold"
    else:
        entry_tag = "Wait"

    # Momentum (directional signal for options selling)
    sma_50 = technicals.get("sma_50", 0)
    sma_200 = technicals.get("sma_200", 0)
    price = metrics.get("current_price", 0)
    rsi = technicals.get("rsi", 50)
    adx = technicals.get("adx", 0)

    if sma_50 and sma_200 and price:
        # ADX < 20 = no real trend, force Neutral
        if adx < 20:
            momentum = "Neutral"
        elif sma_50 > sma_200 and price > sma_50:
            if rsi > 70:
                momentum = "Bullish (overextended)"
            else:
                momentum = "Bullish"
        elif sma_50 > sma_200 and price <= sma_50:
            momentum = "Weakening"
        elif sma_50 <= sma_200 and price >= sma_50:
            momentum = "Neutral"
        else:
            if rsi < 30:
                momentum = "Bearish (oversold)"
            else:
                momentum = "Bearish"
    else:
        momentum = "Unknown"

    category = dgi_metrics.categorize_stock(metrics)
    passes_filters = dgi_metrics.passes_minimum_filters(metrics, filters)
    filter_details = dgi_metrics.filter_detail(metrics, filters)

    return {
        "symbol": symbol,
        "name": info.get("shortName") or info.get("longName") or symbol,
        "metrics": metrics,
        "technicals": technicals,
        "quality_score": quality_score,
        "quality_detail": quality_detail,
        "entry_tag": entry_tag,
        "momentum": momentum,
        "category": category,
        "has_dividends": has_dividends,
        "passes_minimum_filters": passes_filters,
        "filter_detail": filter_details,
    }


async def run_dgi_screener(config, cosmos) -> dict:
    """Run the full DGI screening pipeline.

    Steps:
    1. Load symbols from config (comma-separated)
    2. Fetch yfinance data for each symbol
    3. Calculate fundamental + technical metrics
    4. Apply minimum filters
    5. Calculate quality scores
    6. Select Top N by score
    7. Categorize each stock
    8. Update days_on_list persistence
    9. Write to CosmosDB
    10. Write daily snapshot

    Returns:
        Summary dict with screening stats.
    """
    from .yfinance_fetcher import YFinanceFetcher
    from . import dgi_metrics

    dgi_config = config.config.get("dgi_screener", {})
    symbols_str = dgi_config.get("symbols", "")
    top_n = dgi_config.get("top_n", 40)
    filters = dgi_config.get("filters", {})
    tech_config = dgi_config.get("technical_indicators", {})
    weights = dgi_config.get("score_weights", {})

    now = datetime.now(timezone.utc)
    run_date = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. Load symbols from config
    symbols = _load_symbols(symbols_str)
    if not symbols:
        logger.error("No symbols loaded — aborting DGI screener run")
        return {"error": "No symbols loaded", "total_screened": 0}

    total_screened = len(symbols)
    logger.info("DGI Screener: starting with %d symbols", total_screened)

    # 2. Fetch yfinance data
    logger.info("DGI Screener: fetching yfinance data for %d symbols...", total_screened)
    fetcher = YFinanceFetcher()
    batch_data = fetcher.get_batch_data(symbols)
    logger.info("DGI Screener: fetched data for %d / %d symbols", len(batch_data), total_screened)
    if not batch_data:
        logger.error("DGI Screener: no data returned from yfinance — aborting")
        return {"error": "No data from yfinance", "total_screened": total_screened}

    # 3. Calculate metrics and score ALL stocks (no hard filter rejection)
    logger.info("DGI Screener: score_weights=%s", weights)
    logger.info("DGI Screener: filters=%s", filters)
    candidates = []
    skipped_no_dividend = 0
    errors = 0
    for idx, (symbol, item) in enumerate(batch_data.items()):
        if not symbol:
            continue

        try:
            logger.info("DGI: Analyzing %s (%d/%d)", symbol, idx + 1, len(batch_data))

            info = item.get("info", {})
            dividends = item.get("dividends")
            history = item.get("history")

            logger.info("[DGI %d/%d] %s — info keys: %d, dividends: %s, history: %s",
                        idx + 1, len(batch_data), symbol,
                        len(info) if info else 0,
                        f"{len(dividends)} entries" if dividends is not None and hasattr(dividends, '__len__') else str(type(dividends)),
                        f"{len(history)} rows" if history is not None and hasattr(history, '__len__') else str(type(history)))

            # Must have dividend history to be a DGI candidate
            if dividends is None or (hasattr(dividends, 'empty') and dividends.empty):
                skipped_no_dividend += 1
                continue

            years = dgi_metrics.calculate_years_consecutive_increases(dividends)
            cagr = dgi_metrics.calculate_dividend_cagr(dividends)

            # yfinance dividendYield is ALWAYS percentage-form — convert to decimal
            raw_yield = info.get("dividendYield") or 0
            raw_yield = raw_yield / 100.0

            metrics = {
                "dividend_yield": raw_yield,
                "dividend_cagr_5y": cagr,
                "years_consecutive_increases": years,
                "payout_ratio": info.get("payoutRatio") or 0,
                "pe_ratio": info.get("trailingPE") or 0,
                "forward_pe": info.get("forwardPE") or 0,
                "debt_to_equity": (info.get("debtToEquity") or 0) / 100
                if info.get("debtToEquity") and info["debtToEquity"] > 10
                else (info.get("debtToEquity") or 0),
                "roe": info.get("returnOnEquity") or 0,
                "market_cap": info.get("marketCap") or 0,
                "current_price": info.get("currentPrice")
                or info.get("regularMarketPrice")
                or 0,
                "sector": info.get("sector", ""),
                "exchange": _normalize_exchange(info.get("exchange", "")),
            }

            # Polite delay between requests (handled by cache for repeats)
            time.sleep(0.5)

            logger.info("[DGI %s] yield=%.3f, cagr=%.3f, years=%d, payout=%.2f, pe=%.1f, de=%.2f, mcap=%s",
                        symbol, metrics["dividend_yield"], cagr, years,
                        metrics["payout_ratio"], metrics["pe_ratio"],
                        metrics["debt_to_equity"],
                        f"{metrics['market_cap']/1e9:.1f}B" if metrics["market_cap"] else "0")

            # Technical timing (use neutral score if no history)
            if history is None or (hasattr(history, 'empty') and history.empty):
                technicals = {"score": 0}
            else:
                tech_kwargs = {}
                if "rsi_period" in tech_config:
                    tech_kwargs["rsi_period"] = tech_config["rsi_period"]
                if "bb_period" in tech_config:
                    tech_kwargs["bb_period"] = tech_config["bb_period"]
                if "bb_std" in tech_config:
                    tech_kwargs["bb_std"] = tech_config["bb_std"]
                tech_score = dgi_metrics.calculate_technical_timing_score(
                    history["Close"].values,
                    history["High"].values,
                    history["Low"].values,
                    metrics.get("current_price", 0),
                    **tech_kwargs,
                )
                technicals = tech_score if isinstance(tech_score, dict) else {
                    "score": tech_score
                }

            quality_detail = dgi_metrics.calculate_quality_score_detailed(
                metrics, technicals
            )
            quality = quality_detail["total"]

            # Entry timing tag based on technical score
            tech_timing = technicals.get("score", 0)
            if tech_timing >= 70:
                entry_tag = "Strong Buy"
            elif tech_timing >= 50:
                entry_tag = "Buy"
            elif tech_timing >= 35:
                entry_tag = "Accumulate"
            elif tech_timing >= 20:
                entry_tag = "Hold"
            else:
                entry_tag = "Wait"

            category_preview = dgi_metrics.categorize_stock(metrics)
            logger.info("[DGI %s] quality_score=%.2f, tech_timing=%.1f, entry_tag=%s, category=%s",
                        symbol, quality, tech_timing, entry_tag, category_preview)
            # Log quality breakdown components
            components = quality_detail.get("components", {})
            if components:
                logger.info("[DGI %s] score_breakdown: %s",
                            symbol,
                            " | ".join(f"{k}={v:.2f}" for k, v in components.items()))

            # Momentum (directional signal for options selling)
            sma_50 = technicals.get("sma_50", 0)
            sma_200 = technicals.get("sma_200", 0)
            price = metrics.get("current_price", 0)
            rsi = technicals.get("rsi", 50)
            adx = technicals.get("adx", 0)

            if sma_50 and sma_200 and price:
                if adx < 20:
                    momentum = "Neutral"
                elif sma_50 > sma_200 and price > sma_50:
                    if rsi > 70:
                        momentum = "Bullish (overextended)"
                    else:
                        momentum = "Bullish"
                elif sma_50 > sma_200 and price <= sma_50:
                    momentum = "Weakening"
                elif sma_50 <= sma_200 and price >= sma_50:
                    momentum = "Neutral"
                else:
                    if rsi < 30:
                        momentum = "Bearish (oversold)"
                    else:
                        momentum = "Bearish"
            else:
                momentum = "Unknown"

            candidates.append({
                "symbol": symbol,
                "metrics": metrics,
                "technicals": technicals,
                "quality_score": round(quality, 2),
                "quality_detail": quality_detail,
                "entry_tag": entry_tag,
                "momentum": momentum,
            })

        except Exception as e:
            logger.warning("[DGI %s] ERROR: %s", symbol, e, exc_info=True)
            errors += 1
            continue

    logger.info("DGI Screener: %d scored, %d skipped (no dividends), %d errors, out of %d fetched",
                len(candidates), skipped_no_dividend, errors, len(batch_data))

    # 4. Sort by score and take top N
    candidates.sort(key=lambda x: x["quality_score"], reverse=True)

    # Log full ranking for visibility
    logger.info("═══ DGI RANKING (all %d candidates sorted by quality score) ═══", len(candidates))
    for rank, c in enumerate(candidates, 1):
        marker = "✅" if rank <= top_n else "❌"
        logger.info("  %s #%03d  %-6s  score=%.2f  tech=%.1f  tag=%-12s  yield=%.2f%%  cagr=%.1f%%  years=%d",
                    marker, rank, c["symbol"], c["quality_score"],
                    c["technicals"].get("score", 0),
                    c.get("entry_tag", ""),
                    c["metrics"]["dividend_yield"] * 100,
                    c["metrics"]["dividend_cagr_5y"] * 100,
                    c["metrics"]["years_consecutive_increases"])

    top_entries = candidates[:top_n]
    cutoff_score = top_entries[-1]["quality_score"] if top_entries else 0
    logger.info("═══ DGI TOP %d CUTOFF: score >= %.2f ═══", top_n, cutoff_score)
    logger.info("DGI Screener: keeping top %d out of %d candidates (cutoff=%.2f)",
                len(top_entries), len(candidates), cutoff_score)

    # Log excluded symbols just below cutoff for context
    just_missed = candidates[top_n:top_n + 5]
    if just_missed:
        logger.info("DGI: Just below cutoff: %s",
                    ", ".join(f"{c['symbol']}({c['quality_score']:.2f})" for c in just_missed))

    # 7. Categorize each
    for i, entry in enumerate(top_entries):
        entry["rank"] = i + 1
        entry["category"] = dgi_metrics.categorize_stock(entry["metrics"])

    # 8. Update days_on_list
    previous_top = {}
    try:
        prev_docs = cosmos.get_dgi_top()
        for doc in prev_docs:
            sym = doc.get("symbol", "")
            if sym:
                previous_top[sym] = doc
    except Exception as e:
        logger.warning("Could not load previous top entries: %s", e)

    new_symbols = []
    dropped_symbols = []
    current_symbols = {e["symbol"] for e in top_entries}
    prev_symbols = set(previous_top.keys())

    today_str = now.strftime("%Y-%m-%d")
    MAX_SCORE_HISTORY = 90

    for entry in top_entries:
        sym = entry["symbol"]
        if sym in previous_top:
            prev = previous_top[sym]
            # Only increment days_on_list when the date changes
            prev_date = prev.get("last_seen_date", "")
            if prev_date == today_str:
                entry["days_on_list"] = prev.get("days_on_list", 1)
            else:
                entry["days_on_list"] = prev.get("days_on_list", 0) + 1
            entry["last_seen_date"] = today_str
            entry["first_appeared"] = prev.get("first_appeared", run_date)

            # Build score_history: carry forward previous history
            prev_history = list(prev.get("score_history", []))
            current_score = entry["quality_score"]
            last_entry = prev_history[-1] if prev_history else None
            if (
                not last_entry
                or last_entry.get("date") != today_str
                or last_entry.get("score") != current_score
            ):
                # Avoid same-day same-score duplicates
                if last_entry and last_entry.get("date") == today_str:
                    prev_history[-1] = {"date": today_str, "score": current_score}
                else:
                    prev_history.append({"date": today_str, "score": current_score})
            entry["score_history"] = prev_history[-MAX_SCORE_HISTORY:]
        else:
            entry["days_on_list"] = 1
            entry["last_seen_date"] = today_str
            entry["first_appeared"] = run_date
            entry["score_history"] = [{"date": today_str, "score": entry["quality_score"]}]
            new_symbols.append(sym)

    dropped_symbols = list(prev_symbols - current_symbols)

    # 9. Write to CosmosDB
    docs_to_upsert = []
    for entry in top_entries:
        tech_timing_score = (
            entry["technicals"].get("score", 0)
            if isinstance(entry["technicals"], dict)
            else 0
        )
        doc = {
            "id": f"top_{entry['symbol']}",
            "symbol": entry["symbol"],
            "doc_type": "dgi_top",
            "rank": entry["rank"],
            "quality_score": entry["quality_score"],
            "entry_tag": entry.get("entry_tag", ""),
            "category": entry["category"],
            "days_on_list": entry["days_on_list"],
            "first_appeared": entry["first_appeared"],
            "last_updated": run_date,
            "metrics": entry["metrics"],
            "technicals": entry["technicals"],
            "quality_detail": entry.get("quality_detail", {}),
            "score_history": entry.get("score_history", []),
            "sector": entry["metrics"].get("sector", ""),
            "exchange": entry["metrics"].get("exchange", ""),
        }
        docs_to_upsert.append(doc)

    logger.info("DGI: Saving %d entries to CosmosDB...", len(docs_to_upsert))
    try:
        cosmos.upsert_dgi_top(docs_to_upsert)
        if dropped_symbols:
            cosmos.delete_dgi_dropped(dropped_symbols)
        logger.info("DGI: ✅ All %d entries saved to CosmosDB", len(docs_to_upsert))
        logger.info("DGI Screener: wrote %d entries, dropped %d",
                     len(docs_to_upsert), len(dropped_symbols))
    except Exception as e:
        logger.error("Failed to write DGI results to CosmosDB: %s", e, exc_info=True)

    # 10. Write daily snapshot
    avg_days = (
        sum(e["days_on_list"] for e in top_entries) / len(top_entries)
        if top_entries
        else 0
    )
    snapshot = {
        "id": f"snapshot_{now.strftime('%Y%m%d')}",
        "symbol": "__GLOBAL__",
        "doc_type": "daily_snapshot",
        "day": now.strftime("%Y-%m-%d"),
        "run_date": run_date,
        "top_20": [
            {
                "symbol": e["symbol"],
                "rank": e["rank"],
                "score": e["quality_score"],
                "category": e["category"],
                "days_on_list": e["days_on_list"],
            }
            for e in top_entries
        ],
        "stats": {
            "total_screened": total_screened,
            "total_scored": len(candidates),
            "new_entries": len(new_symbols),
            "dropped": len(dropped_symbols),
            "avg_days_on_list": round(avg_days, 1),
        },
        "ttl": 7776000,  # 90 days
    }

    try:
        cosmos.write_dgi_snapshot(snapshot)
    except Exception as e:
        logger.error("Failed to write DGI snapshot: %s", e)

    summary = {
        "run_date": run_date,
        "total_screened": total_screened,
        "total_scored": len(candidates),
        "top_n": len(top_entries),
        "new_entries": new_symbols,
        "dropped": dropped_symbols,
        "avg_days_on_list": round(avg_days, 1),
        "top_entries": [
            {
                "rank": e["rank"],
                "symbol": e["symbol"],
                "score": e["quality_score"],
                "category": e["category"],
                "days_on_list": e["days_on_list"],
            }
            for e in top_entries
        ],
    }

    logger.info("DGI: Screener run complete — %d symbols analyzed, %d passed filters",
                total_screened, len(candidates))
    logger.info(
        "DGI Screener complete: %d screened, %d scored, %d in top list, "
        "%d new, %d dropped",
        total_screened, len(candidates), len(top_entries),
        len(new_symbols), len(dropped_symbols),
    )
    return summary
