"""Pure in-memory cache for precomputed Best Options envelopes.

Design: `.squad/decisions/inbox/danny-best-options-scheduler-design.md` §13
(Linus ownership slice).

This is a pure data structure — no Cosmos, no FastAPI, no scheduler imports,
and no asyncio.Task objects stored. Thread-safe via RLock. Snapshot publishing
is copy-on-write and atomic; all published envelopes are immutable by contract.

**Cache key is the normalized symbol alone** (one canonical `side="both"`
envelope serves both Symbol Detail and Screener consumers). Category, shares,
calendar, and DTE are inputs recorded for drift detection, never key dimensions.

**Carry-forward on failure:** a symbol that fails during cycle N+1 retains its
cycle-N entry with `status` downgraded to `"stale"`, its original
`generation`/`computed_at` intact, and `error`/`reason` populated. A transient
provider hiccup never blanks a working page.

**Module singleton:** `get_best_options_cache()` / `set_best_options_cache(cache_or_None)`.
Mirrors `options_chain_cache.py`'s pattern verbatim for hermetic test reset.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Mapping, Optional

_cache_lock = threading.Lock()
_cache_instance: Optional["BestOptionsCache"] = None


def get_best_options_cache() -> "BestOptionsCache":
    """Module-level singleton accessor. Thread-safe."""
    global _cache_instance
    with _cache_lock:
        if _cache_instance is None:
            _cache_instance = BestOptionsCache()
        return _cache_instance


def set_best_options_cache(cache: Optional["BestOptionsCache"]) -> None:
    """Module-level singleton setter (test-only reset hook). Thread-safe."""
    global _cache_instance
    with _cache_lock:
        _cache_instance = cache


def _normalize_symbol(value: Any) -> str:
    """Total ticker normalisation — matches the `.strip().upper()` convention
    already used across this codebase."""
    return str(value if value is not None else "").strip().upper()


class BestOptionsCache:
    """Thread-safe in-memory cache for precomputed Best Options envelopes.
    
    One canonical `side="both"` envelope per symbol, published atomically via
    copy-on-write snapshot replacement. All published envelopes are immutable
    by contract (enforced via documented discipline, not runtime enforcement).
    
    **Entry shape** (one per symbol, immutable once published):
        symbol                 str
        status                 "ok" | "stale" | "error" | "warming"
        envelope               dict | None  (None unless ok/stale)
        generation             int          (cycle that produced this envelope)
        computed_at            str          (ISO-8601 UTC)
        chain_timestamp        str | None   (hoisted from envelope for cheap comparison)
        chain_stale_at_compute bool
        inputs                 dict         {category, total_shares, next_earnings_date, ex_dividend_date}
        error                  str | None
        reason                 str | None   ("chain_cold" | "chain_unreadable" | "evaluator_error" | ...)
        
        # Refresh metadata (Symbol Detail Refresh button, §9b)
        refreshing             bool
        refresh_started_at     str | None   (ISO-8601 UTC)
        refresh_completed_at   str | None   (ISO-8601 UTC)
        refresh_error          str | None
        chain_refresh_error    str | None
    
    **Snapshot shape** (one per cache, immutable once published):
        generation             int    (monotonically increasing, +1 per completed cycle)
        entries                Mapping[str, Entry]
        cycle_started_at       str    (ISO-8601 UTC)
        cycle_finished_at      str    (ISO-8601 UTC)
        cycle_duration_seconds float
        trigger                "startup" | "scheduled" | "manual" | "symbol_refresh"
        truncated              bool   (soft-deadline hit)
        counts                 {ok, stale, error, warming}
    """

    def __init__(self):
        self._lock = threading.RLock()
        # Starts empty — generation 0, no entries
        self._snapshot: Dict[str, Any] = {
            "generation": 0,
            "entries": {},
            "cycle_started_at": None,
            "cycle_finished_at": None,
            "cycle_duration_seconds": 0.0,
            "trigger": None,
            "truncated": False,
            "counts": {"ok": 0, "stale": 0, "error": 0, "warming": 0},
        }

    def snapshot(self) -> Dict[str, Any]:
        """Returns the current snapshot reference. Thread-safe.
        
        Consumers must take the snapshot reference exactly once per request and
        read everything from that one object — this is what makes atomicity
        worth having (no mid-iteration generation jumps).
        """
        with self._lock:
            return self._snapshot

    def publish_snapshot(self, snapshot: Dict[str, Any]) -> None:
        """Atomically replace the entire snapshot (copy-on-write, never mutate
        in place). Thread-safe.
        
        This is the full-cycle publication primitive. The snapshot's `generation`
        advances by +1, and `trigger` is one of "startup" | "scheduled" | "manual".
        """
        with self._lock:
            self._snapshot = snapshot

    def replace_symbol(
        self,
        symbol: str,
        entry: Dict[str, Any],
        *,
        trigger: str = "symbol_refresh",
    ) -> None:
        """Copy-on-write replacement of one symbol's entry (§3, §9b).
        
        Atomically publishes a new snapshot with:
        - The specified symbol's entry replaced
        - Every other entry's object identity preserved
        - `generation` unchanged (that counter means "completed full cycle")
        - `trigger` set to the provided value (default "symbol_refresh")
        - `counts` recomputed from the new entry map
        
        This is the single-symbol Refresh primitive. Thread-safe.
        """
        normalized = _normalize_symbol(symbol)
        with self._lock:
            old_snapshot = self._snapshot
            old_entries = old_snapshot.get("entries") or {}
            
            # Build the new entry map: copy-on-write, not mutation
            new_entries = {**old_entries, normalized: entry}
            
            # Recompute counts from the new entry map
            counts = {"ok": 0, "stale": 0, "error": 0, "warming": 0}
            for e in new_entries.values():
                status = e.get("status")
                if status in counts:
                    counts[status] += 1
            
            # Build and publish the new snapshot
            new_snapshot = {
                "generation": old_snapshot.get("generation", 0),  # unchanged
                "entries": new_entries,
                "cycle_started_at": old_snapshot.get("cycle_started_at"),
                "cycle_finished_at": old_snapshot.get("cycle_finished_at"),
                "cycle_duration_seconds": old_snapshot.get("cycle_duration_seconds", 0.0),
                "trigger": trigger,
                "truncated": old_snapshot.get("truncated", False),
                "counts": counts,
            }
            self._snapshot = new_snapshot

    def get_entry(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Returns the entry for the specified symbol, or None if absent.
        
        Thread-safe convenience accessor. For batch reads, prefer taking a
        snapshot reference once and reading from it.
        """
        normalized = _normalize_symbol(symbol)
        with self._lock:
            entries = self._snapshot.get("entries") or {}
            return entries.get(normalized)

    def is_empty(self) -> bool:
        """Returns True if the cache has never been populated (generation 0).
        
        Thread-safe. Used for `GET /api/health/best-options` to report
        `status: "empty"` when nothing has ever been published — the signature
        of a split-process deployment (§5).
        """
        with self._lock:
            return self._snapshot.get("generation", 0) == 0
