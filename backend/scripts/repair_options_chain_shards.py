#!/usr/bin/env python3
"""Batch repair for legacy (pre-schema_version-4) options-chain shards.

Danny's "Zero-Free Agent-Facing Option Chains" decision (§4.4) makes the
on-read migration in ``OptionsChainStore.hydrate()`` mandatory and
lazy — a legacy shard is corrected the moment it is next read. This script
exists only to *proactively* sweep every persisted shard for symbols that
may not be read again soon (or ever, e.g. a delisted/removed watchlist
symbol), so Cosmos storage does not indefinitely retain fabricated
``mid``/Greek values for a shard nobody happens to hydrate.

It is a thin CLI wrapper around ``OptionsChainStore``'s own repair-support
methods (``list_symbols_with_shards``, ``list_shard_expirations``,
``repair_shard``) — no migration logic lives here. Per Rule Z11, only
derived/fabricated fields (``mid`` + the 5 Greeks) are ever nulled; every
observed raw field (bid/ask/lastPrice/iv/volume/openInterest/provenance)
is preserved byte-for-byte. Writes use Cosmos ETag compare-and-swap
(handled inside ``repair_shard``) so a concurrent refresh's write is never
clobbered.

Usage::

    python -m scripts.repair_options_chain_shards --all                 # dry-run (default)
    python -m scripts.repair_options_chain_shards --all --apply          # actually write
    python -m scripts.repair_options_chain_shards --symbol AAPL --apply
    python -m scripts.repair_options_chain_shards --all --apply --limit 50

Exit code is always 0 on a normal scan (including "no shards needed
repair" and "persistence unavailable" — those are reported, not treated
as script failures); a non-zero exit is reserved for bad CLI arguments.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# Allow running as `python scripts/repair_options_chain_shards.py` (no
# package context) in addition to `python -m scripts.repair_...`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.options_chain_store import get_options_chain_store  # noqa: E402

logger = logging.getLogger("repair_options_chain_shards")


@dataclass
class RepairReport:
    """Aggregate counters for a repair run — mirrors the fields Danny's
    decision (§4.4, §6) requires the script to report."""

    shards_scanned: int = 0
    shards_changed: int = 0
    shards_written: int = 0
    cas_conflicts: int = 0
    errors: int = 0
    error_details: List[str] = field(default_factory=list)


def repair_symbol(store, symbol: str, *, dry_run: bool, limit: Optional[int], report: RepairReport) -> None:
    """Repair every persisted shard for one symbol, respecting ``limit``
    (a global remaining-shard budget shared across symbols by the caller)."""
    for exp_key in store.list_shard_expirations(symbol):
        if limit is not None and report.shards_scanned >= limit:
            return
        report.shards_scanned += 1
        outcome = store.repair_shard(symbol, exp_key, dry_run=dry_run)
        if outcome.get("error"):
            error = outcome["error"]
            if error == "shard not found":
                # Benign race: the shard was deleted/re-sharded between
                # listing and repair — not a repair failure.
                continue
            if "conflict" in error.lower() or "412" in error or "409" in error:
                report.cas_conflicts += 1
            else:
                report.errors += 1
                report.error_details.append(f"{symbol}/{exp_key}: {error}")
            continue
        if outcome.get("changed"):
            report.shards_changed += 1
        if outcome.get("written"):
            report.shards_written += 1


def run(*, symbol: Optional[str], all_symbols: bool, dry_run: bool, limit: Optional[int]) -> RepairReport:
    report = RepairReport()
    store = get_options_chain_store()
    if not store.is_available():
        logger.error(
            "Persistence is not available (disabled or not yet connected) "
            "— nothing to repair. Re-run once the store is connected "
            "(see GET /api/health/options-chain)."
        )
        return report

    if all_symbols:
        symbols = store.list_symbols_with_shards()
        if not symbols:
            logger.info("No persisted options-chain shards found — nothing to repair.")
            return report
    else:
        symbols = [symbol]

    for sym in symbols:
        if limit is not None and report.shards_scanned >= limit:
            break
        repair_symbol(store, sym, dry_run=dry_run, limit=limit, report=report)

    return report


def _print_report(report: RepairReport, *, dry_run: bool) -> None:
    mode = "DRY RUN (no writes)" if dry_run else "APPLY (writes performed)"
    print(f"repair_options_chain_shards — {mode}")
    print(f"  shards_scanned:  {report.shards_scanned}")
    print(f"  shards_changed:  {report.shards_changed}  (needed migration)")
    print(f"  shards_written:  {report.shards_written}")
    print(f"  cas_conflicts:   {report.cas_conflicts}  (safe to re-run)")
    print(f"  errors:          {report.errors}")
    for detail in report.error_details:
        print(f"    - {detail}")
    if dry_run and report.shards_changed:
        print(
            "\nRe-run with --apply to write these changes "
            "(dry-run never writes)."
        )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Proactively migrate legacy options-chain shards (nulling "
            "only fabricated mid/Greek fields; never touches observed "
            "bid/ask/lastPrice/iv/volume/openInterest)."
        ),
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--symbol", metavar="SYMBOL", help="Repair shards for a single symbol only.")
    target.add_argument("--all", action="store_true", help="Repair shards for every symbol with persisted data.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write changes. Without this flag the script only reports what would change (default).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Stop after scanning this many shards (across all symbols), for incremental/bounded runs.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    dry_run = not args.apply
    report = run(symbol=args.symbol, all_symbols=args.all, dry_run=dry_run, limit=args.limit)
    _print_report(report, dry_run=dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
