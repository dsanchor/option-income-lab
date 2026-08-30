"""Best Options precompute cycle body + targeted-refresh routine.

Design: `.squad/decisions/inbox/danny-best-options-scheduler-design.md` §13
(Livingston's ownership: the cycle job plus the single-symbol refresh with
its per-symbol in-flight async task registry).

The cycle is **synchronous** (plain `def`, not `async def`) — no asyncio
loop in this function, no `_run_async` wrapper needed. It does Cosmos reads,
options-chain reads (hydrate/memory, never a provider fetch, never an SWR
trigger), and pure CPU evaluation. Never calls `chain_cache.refresh()` or
`schedule_background_refresh()` (both require a running loop).

**Inputs:** Cosmos symbol list, calendar/index reads (batched), option-chain
cache reads via `get_or_hydrate(trigger_swr=False)`.

**Outputs:** Atomic publish to the Best Options cache via
`publish_snapshot()`.

**Carry-forward:** a symbol that fails (chain absent/unreadable/evaluator
exception) retains its prior entry with `status="stale"`, original
`generation`/`computed_at` intact, `error`/`reason` populated. A transient
failure never blanks the page.

**Soft deadline:** stops evaluating further symbols after
`_CYCLE_SOFT_DEADLINE_SECONDS` (900s, half the registry watchdog), marks
unprocessed remainder as carried-forward, still publishes with
`truncated: true`. A disclosed, truncated cycle beats being abandoned
mid-flight by the watchdog.

**The targeted-refresh routine** (`refresh_symbol`) is **asynchronous** —
it *forces* a chain refresh (the load-bearing call per §9b) via
`chain_cache.refresh(symbol)`, then runs the evaluator and publishes
one entry via `cache.replace_symbol()`. An in-flight registry holds live
`asyncio.Task` objects (strong ref, prevents GC mid-flight) with
duplicate/concurrent protection. This is kept outside the pure cache module
(Linus's `best_options_cache.py` stores only plain data, no tasks).

**Chain refresh is best-effort:** if the fetch fails, the recompute still
runs against the last-known-good chain. The entry's `refresh_error`/
`chain_refresh_error` reports the failure; the entry's `status` is never
downgraded below what it already held. An explicit user action (the Refresh
button) must never leave the entry worse than it was.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Soft deadline (half the registry watchdog). Beyond this, stop evaluating
# further symbols, carry forward the remainder, publish with `truncated: true`.
_CYCLE_SOFT_DEADLINE_SECONDS = 900  # 15 minutes

# Per-symbol in-flight task registry for targeted refresh (§9b). Holds
# strong refs to `asyncio.Task` objects, prevents GC mid-flight, provides
# duplicate/concurrent protection. Cleanup is in a `finally` so a crashing
# task can never wedge a symbol as permanently "refreshing".
_symbol_refresh_tasks: Dict[str, asyncio.Task] = {}
_refresh_tasks_lock = asyncio.Lock()


def run_best_options_precompute(
    cosmos,
    *,
    trigger: str = "scheduled",
) -> Dict[str, Any]:
    """Full-cycle precompute: list symbols, read chains/inputs, evaluate,
    publish snapshot. Synchronous (no async).

    Args:
        cosmos: CosmosDBService instance
        trigger: "scheduled" | "startup" | "manual"

    Returns:
        dict: {"success": int, "stale": int, "error": int, "warming": int,
               "truncated": bool, "duration_seconds": float}
    """
    cycle_start = time.time()
    cycle_started_at_iso = datetime.now(timezone.utc).isoformat()

    from src.best_options import DEFAULT_DTE_MAX, DEFAULT_DTE_MIN, evaluate_best_options
    from src.best_options_cache import get_best_options_cache
    from src.options_chain_cache import get_options_chain_cache

    cache = get_best_options_cache()
    chain_cache = get_options_chain_cache()

    # Take a snapshot of the current generation and entries (for carry-forward)
    old_snapshot = cache.snapshot()
    old_generation = old_snapshot.get("generation", 0)
    old_entries = old_snapshot.get("entries") or {}

    # Cosmos reads (batched)
    try:
        symbols_raw = cosmos.list_symbols()
    except Exception as exc:
        logger.exception("Precompute: failed to list symbols")
        return {"success": 0, "stale": 0, "error": 1, "warming": 0, "truncated": False, "duration_seconds": 0.0}

    # Normalize symbols: extract ticker strings from dict entries, handle legacy string format
    symbols = []
    for entry in symbols_raw:
        if isinstance(entry, str):
            # Legacy format: already a string ticker
            ticker = entry.strip().upper()
            if ticker:
                symbols.append(ticker)
        elif isinstance(entry, dict):
            # Real Cosmos format: full document with "symbol" field
            ticker = entry.get("symbol", "")
            if isinstance(ticker, str):
                ticker = ticker.strip().upper()
                if ticker:
                    symbols.append(ticker)
            else:
                logger.warning("Precompute: skipping malformed entry (symbol not a string): %s", entry)
        else:
            logger.warning("Precompute: skipping malformed entry (not string/dict): %s", entry)

    # Deduplicate while preserving order
    seen = set()
    symbols = [s for s in symbols if not (s in seen or seen.add(s))]

    if not symbols:
        logger.warning("Precompute: no symbols configured")
        return {"success": 0, "stale": 0, "error": 0, "warming": 0, "truncated": False, "duration_seconds": 0.0}

    # Batched calendar/index reads
    enrichments = {}
    try:
        for symbol in symbols:
            sym_doc = cosmos.get_symbol(symbol)
            if not sym_doc:
                continue
            category = (sym_doc.get("enrichment") or {}).get("category")
            total_shares = int(sym_doc.get("total_shares", 0) or 0)
            next_earnings_date = cosmos.get_next_earnings_date(symbol)
            ex_dividend_date = cosmos.get_next_calendar_event_date(symbol, "ex_dividend")
            enrichments[symbol] = {
                "category": category,
                "total_shares": total_shares,
                "next_earnings_date": next_earnings_date,
                "ex_dividend_date": ex_dividend_date,
            }
    except Exception as exc:
        logger.exception("Precompute: failed to read symbol enrichments")

    # Evaluate each symbol
    new_entries = {}
    success_count = 0
    stale_count = 0
    error_count = 0
    warming_count = 0
    truncated = False

    now = datetime.now(timezone.utc)

    for symbol in symbols:
        # Soft deadline check
        elapsed = time.time() - cycle_start
        if elapsed > _CYCLE_SOFT_DEADLINE_SECONDS:
            logger.warning(
                "Precompute: soft deadline exceeded after %.1fs, %d/%d symbols processed",
                elapsed, len(new_entries), len(symbols)
            )
            truncated = True
            # Carry forward remaining symbols from old snapshot
            for remaining_symbol in symbols:
                if remaining_symbol not in new_entries:
                    old_entry = old_entries.get(remaining_symbol)
                    if old_entry:
                        new_entries[remaining_symbol] = old_entry
            break

        enrichment = enrichments.get(symbol, {})

        # Chain read (hydrate/memory only, never a live fetch, never SWR trigger)
        chain_json = chain_cache.get_or_hydrate(symbol, trigger_swr=False)

        if chain_json is None:
            # Chain cold — carry forward old entry (if any) with status="warming"
            old_entry = old_entries.get(symbol)
            if old_entry:
                new_entries[symbol] = {
                    **old_entry,
                    "status": "warming" if old_entry.get("status") == "ok" else old_entry.get("status", "warming"),
                    "reason": "chain_cold",
                }
            else:
                new_entries[symbol] = {
                    "symbol": symbol,
                    "status": "warming",
                    "envelope": None,
                    "generation": old_generation + 1,
                    "computed_at": now.isoformat(),
                    "chain_timestamp": None,
                    "chain_stale_at_compute": False,
                    "inputs": enrichment,
                    "error": None,
                    "reason": "chain_cold",
                    "refreshing": False,
                    "refresh_started_at": None,
                    "refresh_completed_at": None,
                    "refresh_error": None,
                    "chain_refresh_error": None,
                }
            warming_count += 1
            continue

        # Parse chain
        try:
            import json
            chain = json.loads(chain_json) if isinstance(chain_json, str) else chain_json
        except (TypeError, ValueError) as exc:
            logger.warning("Precompute: %s chain unreadable: %s", symbol, exc)
            # Carry forward with error
            old_entry = old_entries.get(symbol)
            if old_entry:
                new_entries[symbol] = {
                    **old_entry,
                    "status": "error",
                    "error": str(exc),
                    "reason": "chain_unreadable",
                }
            else:
                new_entries[symbol] = {
                    "symbol": symbol,
                    "status": "error",
                    "envelope": None,
                    "generation": old_generation + 1,
                    "computed_at": now.isoformat(),
                    "chain_timestamp": None,
                    "chain_stale_at_compute": False,
                    "inputs": enrichment,
                    "error": str(exc),
                    "reason": "chain_unreadable",
                    "refreshing": False,
                    "refresh_started_at": None,
                    "refresh_completed_at": None,
                    "refresh_error": None,
                    "chain_refresh_error": None,
                }
            error_count += 1
            continue

        # Evaluate
        try:
            envelope = evaluate_best_options(
                chain,
                side="both",
                category=enrichment.get("category"),
                total_shares=enrichment.get("total_shares", 0),
                next_earnings_date=enrichment.get("next_earnings_date"),
                ex_dividend_date=enrichment.get("ex_dividend_date"),
                support_level=None,  # No deterministic source
                dte_min=DEFAULT_DTE_MIN,
                dte_max=DEFAULT_DTE_MAX,
                now=now,
            )
        except Exception as exc:
            logger.exception("Precompute: %s evaluator failed", symbol)
            # Carry forward with error
            old_entry = old_entries.get(symbol)
            if old_entry:
                new_entries[symbol] = {
                    **old_entry,
                    "status": "error",
                    "error": str(exc),
                    "reason": "evaluator_error",
                }
            else:
                new_entries[symbol] = {
                    "symbol": symbol,
                    "status": "error",
                    "envelope": None,
                    "generation": old_generation + 1,
                    "computed_at": now.isoformat(),
                    "chain_timestamp": chain.get("timestamp"),
                    "chain_stale_at_compute": chain_cache.is_stale(symbol),
                    "inputs": enrichment,
                    "error": str(exc),
                    "reason": "evaluator_error",
                    "refreshing": False,
                    "refresh_started_at": None,
                    "refresh_completed_at": None,
                    "refresh_error": None,
                    "chain_refresh_error": None,
                }
            error_count += 1
            continue

        # Success
        chain_timestamp = chain.get("timestamp")
        chain_stale = chain_cache.is_stale(symbol)

        new_entries[symbol] = {
            "symbol": symbol,
            "status": "ok",
            "envelope": envelope,
            "generation": old_generation + 1,
            "computed_at": now.isoformat(),
            "chain_timestamp": chain_timestamp,
            "chain_stale_at_compute": chain_stale,
            "inputs": enrichment,
            "error": None,
            "reason": None,
            "refreshing": False,
            "refresh_started_at": None,
            "refresh_completed_at": None,
            "refresh_error": None,
            "chain_refresh_error": None,
        }
        success_count += 1

    # Atomic publish
    cycle_finished_at_iso = datetime.now(timezone.utc).isoformat()
    cycle_duration = time.time() - cycle_start

    counts = {"ok": success_count, "stale": stale_count, "error": error_count, "warming": warming_count}

    new_snapshot = {
        "generation": old_generation + 1,
        "entries": new_entries,
        "cycle_started_at": cycle_started_at_iso,
        "cycle_finished_at": cycle_finished_at_iso,
        "cycle_duration_seconds": cycle_duration,
        "trigger": trigger,
        "truncated": truncated,
        "counts": counts,
    }

    cache.publish_snapshot(new_snapshot)

    logger.info(
        "Precompute complete: %d ok, %d stale, %d error, %d warming, truncated=%s, %.1fs",
        success_count, stale_count, error_count, warming_count, truncated, cycle_duration,
    )

    return {
        "success": success_count,
        "stale": stale_count,
        "error": error_count,
        "warming": warming_count,
        "truncated": truncated,
        "duration_seconds": cycle_duration,
    }


async def refresh_symbol(symbol: str, cosmos) -> Dict[str, Any]:
    """Single-symbol targeted refresh (Symbol Detail Refresh button).

    Forces a chain refresh via `chain_cache.refresh(symbol)` (best-effort),
    then runs the evaluator and publishes one entry via `cache.replace_symbol()`.

    In-flight protection: duplicate requests for the same symbol return the
    existing task's result (awaited via `asyncio.shield`). Cleanup is in a
    `finally` so a crashing task never wedges the symbol.

    Args:
        symbol: normalized symbol
        cosmos: CosmosDBService instance

    Returns:
        dict: The published entry
    """
    async with _refresh_tasks_lock:
        existing_task = _symbol_refresh_tasks.get(symbol)
        if existing_task and not existing_task.done():
            # Dedupe: await the existing task
            logger.info("Refresh %s: in-flight task found, awaiting", symbol)
            return await asyncio.shield(existing_task)

        # Start a new refresh task
        task = asyncio.create_task(_refresh_symbol_impl(symbol, cosmos))
        _symbol_refresh_tasks[symbol] = task

    try:
        return await task
    finally:
        async with _refresh_tasks_lock:
            _symbol_refresh_tasks.pop(symbol, None)


async def _refresh_symbol_impl(symbol: str, cosmos) -> Dict[str, Any]:
    """Implementation of single-symbol refresh (runs once per symbol)."""
    from src.best_options import DEFAULT_DTE_MAX, DEFAULT_DTE_MIN, evaluate_best_options
    from src.best_options_cache import get_best_options_cache
    from src.options_chain_cache import get_options_chain_cache

    cache = get_best_options_cache()
    chain_cache = get_options_chain_cache()

    refresh_started_at = datetime.now(timezone.utc).isoformat()

    # Mark as refreshing in the cache (optimistic update)
    old_entry = cache.get_entry(symbol)
    if old_entry:
        cache.replace_symbol(
            symbol,
            {**old_entry, "refreshing": True, "refresh_started_at": refresh_started_at},
            trigger="symbol_refresh",
        )

    chain_refresh_error = None
    chain_json = None

    # Force chain refresh (best-effort per §9b)
    try:
        logger.info("Refresh %s: forcing chain refresh", symbol)
        await chain_cache.refresh(symbol)
        chain_json = chain_cache.get_or_hydrate(symbol, trigger_swr=False)
    except Exception as exc:
        logger.warning("Refresh %s: chain refresh failed: %s", symbol, exc)
        chain_refresh_error = str(exc)
        # Best-effort: try to use last-known-good chain
        chain_json = chain_cache.get_or_hydrate(symbol, trigger_swr=False)

    # Read symbol inputs
    try:
        sym_doc = cosmos.get_symbol(symbol)
        if not sym_doc:
            raise ValueError(f"Symbol {symbol} not found in Cosmos")

        category = (sym_doc.get("enrichment") or {}).get("category")
        total_shares = int(sym_doc.get("total_shares", 0) or 0)
        next_earnings_date = cosmos.get_next_earnings_date(symbol)
        ex_dividend_date = cosmos.get_next_calendar_event_date(symbol, "ex_dividend")

        enrichment = {
            "category": category,
            "total_shares": total_shares,
            "next_earnings_date": next_earnings_date,
            "ex_dividend_date": ex_dividend_date,
        }
    except Exception as exc:
        logger.exception("Refresh %s: failed to read symbol inputs", symbol)
        refresh_error = str(exc)
        refresh_completed_at = datetime.now(timezone.utc).isoformat()

        # Publish error entry
        entry = {
            "symbol": symbol,
            "status": "error",
            "envelope": None,
            "generation": (old_entry or {}).get("generation", 0),
            "computed_at": refresh_started_at,
            "chain_timestamp": None,
            "chain_stale_at_compute": False,
            "inputs": {},
            "error": refresh_error,
            "reason": "cosmos_error",
            "refreshing": False,
            "refresh_started_at": refresh_started_at,
            "refresh_completed_at": refresh_completed_at,
            "refresh_error": refresh_error,
            "chain_refresh_error": chain_refresh_error,
        }
        cache.replace_symbol(symbol, entry, trigger="symbol_refresh")
        return entry

    # Parse chain
    if chain_json is None:
        refresh_error = "Chain unavailable after refresh"
        refresh_completed_at = datetime.now(timezone.utc).isoformat()

        entry = {
            "symbol": symbol,
            "status": "error",
            "envelope": None,
            "generation": (old_entry or {}).get("generation", 0),
            "computed_at": refresh_started_at,
            "chain_timestamp": None,
            "chain_stale_at_compute": False,
            "inputs": enrichment,
            "error": refresh_error,
            "reason": "chain_unavailable",
            "refreshing": False,
            "refresh_started_at": refresh_started_at,
            "refresh_completed_at": refresh_completed_at,
            "refresh_error": refresh_error,
            "chain_refresh_error": chain_refresh_error,
        }
        cache.replace_symbol(symbol, entry, trigger="symbol_refresh")
        return entry

    try:
        import json
        chain = json.loads(chain_json) if isinstance(chain_json, str) else chain_json
    except (TypeError, ValueError) as exc:
        logger.warning("Refresh %s: chain unreadable: %s", symbol, exc)
        refresh_error = str(exc)
        refresh_completed_at = datetime.now(timezone.utc).isoformat()

        entry = {
            "symbol": symbol,
            "status": "error",
            "envelope": None,
            "generation": (old_entry or {}).get("generation", 0),
            "computed_at": refresh_started_at,
            "chain_timestamp": None,
            "chain_stale_at_compute": False,
            "inputs": enrichment,
            "error": refresh_error,
            "reason": "chain_unreadable",
            "refreshing": False,
            "refresh_started_at": refresh_started_at,
            "refresh_completed_at": refresh_completed_at,
            "refresh_error": refresh_error,
            "chain_refresh_error": chain_refresh_error,
        }
        cache.replace_symbol(symbol, entry, trigger="symbol_refresh")
        return entry

    # Evaluate
    now = datetime.now(timezone.utc)
    try:
        envelope = evaluate_best_options(
            chain,
            side="both",
            category=enrichment.get("category"),
            total_shares=enrichment.get("total_shares", 0),
            next_earnings_date=enrichment.get("next_earnings_date"),
            ex_dividend_date=enrichment.get("ex_dividend_date"),
            support_level=None,
            dte_min=DEFAULT_DTE_MIN,
            dte_max=DEFAULT_DTE_MAX,
            now=now,
        )
    except Exception as exc:
        logger.exception("Refresh %s: evaluator failed", symbol)
        refresh_error = str(exc)
        refresh_completed_at = datetime.now(timezone.utc).isoformat()

        entry = {
            "symbol": symbol,
            "status": "error",
            "envelope": None,
            "generation": (old_entry or {}).get("generation", 0),
            "computed_at": now.isoformat(),
            "chain_timestamp": chain.get("timestamp"),
            "chain_stale_at_compute": chain_cache.is_stale(symbol),
            "inputs": enrichment,
            "error": refresh_error,
            "reason": "evaluator_error",
            "refreshing": False,
            "refresh_started_at": refresh_started_at,
            "refresh_completed_at": refresh_completed_at,
            "refresh_error": refresh_error,
            "chain_refresh_error": chain_refresh_error,
        }
        cache.replace_symbol(symbol, entry, trigger="symbol_refresh")
        return entry

    # Success
    refresh_completed_at = datetime.now(timezone.utc).isoformat()
    chain_timestamp = chain.get("timestamp")
    chain_stale = chain_cache.is_stale(symbol)

    entry = {
        "symbol": symbol,
        "status": "ok",
        "envelope": envelope,
        "generation": (old_entry or {}).get("generation", 0) + 1,
        "computed_at": now.isoformat(),
        "chain_timestamp": chain_timestamp,
        "chain_stale_at_compute": chain_stale,
        "inputs": enrichment,
        "error": None,
        "reason": None,
        "refreshing": False,
        "refresh_started_at": refresh_started_at,
        "refresh_completed_at": refresh_completed_at,
        "refresh_error": chain_refresh_error,  # Report chain failure even if evaluator succeeded
        "chain_refresh_error": chain_refresh_error,
    }

    cache.replace_symbol(symbol, entry, trigger="symbol_refresh")
    logger.info("Refresh %s: complete", symbol)
    return entry
