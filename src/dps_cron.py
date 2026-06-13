"""Daily DPS (Deterministic Position Scoring) cron job.

Runs DPS analysis for all active positions and stores the score
in their respective snapshot documents.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


async def run_dps_cron(cosmos, yf_provider) -> dict:
    """Run DPS analysis for all active positions and store scores in snapshots.

    Returns:
        Summary dict with counts of processed/skipped/failed positions.
    """
    from src.dps_scorer import run_dps_analysis

    if cosmos is None:
        logger.warning("DPS cron: CosmosDB unavailable — skipping")
        return {"status": "skipped", "reason": "cosmos_unavailable"}

    if yf_provider is None:
        logger.warning("DPS cron: YFinance provider unavailable — skipping")
        return {"status": "skipped", "reason": "yf_provider_unavailable"}

    all_symbols = cosmos.list_symbols()
    processed = 0
    skipped = 0
    failed = 0
    results = []

    for sym_doc in all_symbols:
        symbol = sym_doc.get("symbol", "")
        positions = sym_doc.get("positions", [])
        active_positions = [
            p for p in positions if p.get("status") == "active"
        ]

        if not active_positions:
            continue

        # Fetch data once per symbol
        try:
            data = await yf_provider.fetch_all(symbol, force_refresh=True)
        except Exception as exc:
            logger.warning("DPS cron: Failed to fetch data for %s: %s", symbol, exc)
            failed += len(active_positions)
            continue

        chain_json = data.get("options_chain", "{}")

        # Get underlying price
        overview = data.get("overview", "{}")
        if isinstance(overview, str):
            try:
                overview = json.loads(overview)
            except (ValueError, TypeError):
                overview = {}
        fundamentals = overview.get("fundamentals", {})
        price_field = fundamentals.get("current_price", {})
        underlying_price = (
            price_field.get("value")
            if isinstance(price_field, dict)
            else price_field
        )
        if underlying_price is not None:
            underlying_price = float(underlying_price)

        for pos in active_positions:
            position_id = pos.get("position_id", "")
            strike = pos.get("strike")
            expiration = pos.get("expiration")
            option_type = pos.get("type", "call")

            if not position_id or not strike or not expiration:
                skipped += 1
                continue

            try:
                strike_f = float(strike)

                # Get premium_received from position source
                _src = pos.get("source") or {}
                _prem = None
                try:
                    _prem = float(_src.get("premium") or _src.get("new_premium") or 0) or None
                except (TypeError, ValueError):
                    pass

                # Get snapshots for this position (oldest first)
                snapshots = cosmos.get_position_snapshots(
                    symbol, position_id, limit=20
                )
                snapshots.reverse()

                result = run_dps_analysis(
                    symbol=symbol,
                    strike=strike_f,
                    expiration=expiration,
                    option_type=option_type,
                    chain_json=chain_json,
                    snapshots=snapshots,
                    underlying_price=underlying_price,
                    premium_received=_prem,
                )

                if result.get("status") == "ERROR" or result.get("error"):
                    logger.debug(
                        "DPS cron: Analysis failed for %s/%s: %s",
                        symbol, position_id, result.get("error", "unknown"),
                    )
                    skipped += 1
                    continue

                # Extract the DPS score
                dps_score = result.get("score")
                if dps_score is None:
                    skipped += 1
                    continue

                # Write the DPS score as a snapshot field
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                snapshot_data = {
                    "timestamp": ts,
                    "dps_score": dps_score,
                    "dps_status": result.get("status", ""),
                    "dps_risk_zone": result.get("risk_zone", ""),
                }

                # Also include current technicals if available from latest snapshot
                if snapshots:
                    latest = snapshots[-1]
                    for key in ("underlying_price", "strike", "gap_percent",
                                "gap_absolute", "rsi_14", "macd_level", "adx"):
                        if latest.get(key) is not None:
                            snapshot_data.setdefault(key, latest[key])

                cosmos.write_position_snapshot(symbol, position_id, snapshot_data)
                processed += 1
                results.append({
                    "symbol": symbol,
                    "position_id": position_id,
                    "score": dps_score,
                    "status": result.get("status", ""),
                })

            except Exception as exc:
                logger.warning(
                    "DPS cron: Error processing %s/%s: %s",
                    symbol, position_id, exc,
                )
                failed += 1

    summary = {
        "status": "completed",
        "processed": processed,
        "skipped": skipped,
        "failed": failed,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    logger.info(
        "DPS cron completed: %d processed, %d skipped, %d failed",
        processed, skipped, failed,
    )
    return summary
