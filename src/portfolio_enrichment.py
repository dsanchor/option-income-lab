"""Portfolio Enrichment — DGI-style scoring for portfolio symbols.

Reuses the DGI screener's ``analyze_single_symbol()`` to compute
quality scores, technicals, categories, and entry tags for every
symbol in the user's portfolio.  Results are stored directly on
each symbol's CosmosDB document (``enrichment`` field).

Runs as a scheduled job (default: hourly 9-17 Mon-Fri).
Also triggered on-demand when a new symbol is added.
"""

import logging
import time
from datetime import datetime, timezone

from src.dgi_screener import analyze_single_symbol

logger = logging.getLogger(__name__)


def enrich_symbol(symbol: str) -> dict | None:
    """Run DGI analysis for a single symbol.

    Returns enrichment dict ready to store, or None on failure.
    """
    try:
        result = analyze_single_symbol(symbol)
        if result.get("error"):
            logger.warning("Portfolio enrichment: %s — %s", symbol, result["error"])
            return None

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {
            "last_updated": now,
            "quality_score": result.get("quality_score", 0),
            "quality_detail": result.get("quality_detail", {}),
            "category": result.get("category", ""),
            "entry_tag": result.get("entry_tag", ""),
            "metrics": result.get("metrics", {}),
            "technicals": result.get("technicals", {}),
            "has_dividends": result.get("has_dividends", False),
            "filter_detail": result.get("filter_detail"),
        }
    except Exception as exc:
        logger.exception("Portfolio enrichment failed for %s: %s", symbol, exc)
        return None


async def run_portfolio_enrichment(cosmos) -> dict:
    """Enrich all portfolio symbols.

    Returns summary dict with counts.
    """
    if cosmos is None:
        logger.warning("Portfolio enrichment: CosmosDB unavailable — skipping")
        return {"status": "skipped", "reason": "cosmos_unavailable"}

    symbols = cosmos.list_symbols()
    total = len(symbols)
    success = 0
    errors = 0

    logger.info("Portfolio enrichment: starting for %d symbols", total)

    for sym_doc in symbols:
        symbol = sym_doc.get("symbol", "")
        if not symbol:
            continue

        enrichment = enrich_symbol(symbol)
        if enrichment is None:
            errors += 1
            continue

        try:
            cosmos.update_symbol_enrichment(symbol, enrichment)
            success += 1
            logger.info(
                "  ✓ %s: score=%.1f, category=%s, tag=%s",
                symbol,
                enrichment["quality_score"],
                enrichment["category"],
                enrichment["entry_tag"],
            )
        except Exception as exc:
            logger.error("  ✗ %s: failed to save enrichment: %s", symbol, exc)
            errors += 1

        # Polite delay between symbols
        time.sleep(0.5)

    logger.info(
        "Portfolio enrichment complete: %d/%d success, %d errors",
        success, total, errors,
    )

    return {
        "status": "completed",
        "total": total,
        "success": success,
        "errors": errors,
    }
