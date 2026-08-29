"""Test suite for src/best_options_cache.py — pure in-memory cache for
precomputed Best Options envelopes.

Design: `.squad/decisions/inbox/danny-best-options-scheduler-design.md` §13
(Linus ownership slice).

Hermetic: pure in-memory, no network calls, no LLM, no FastAPI, no Cosmos.
Tests thread safety, copy-on-write semantics, generation/status mechanics,
and module singleton behavior.
"""

import threading
from datetime import datetime, timezone

import pytest

from src.best_options_cache import (
    BestOptionsCache,
    get_best_options_cache,
    set_best_options_cache,
)


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the module singleton before and after each test."""
    set_best_options_cache(None)
    yield
    set_best_options_cache(None)


NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)


def _make_entry(
    symbol: str,
    status: str = "ok",
    generation: int = 1,
    envelope: dict = None,
) -> dict:
    """Build a minimal entry for testing."""
    return {
        "symbol": symbol,
        "status": status,
        "envelope": envelope or {"calls": {"rows": []}, "puts": {"rows": []}},
        "generation": generation,
        "computed_at": NOW.isoformat(),
        "chain_timestamp": "2026-08-29T11:00:00Z",
        "chain_stale_at_compute": False,
        "inputs": {
            "category": "balanced",
            "total_shares": 0,
            "next_earnings_date": None,
            "ex_dividend_date": None,
        },
        "error": None,
        "reason": None,
        "refreshing": False,
        "refresh_started_at": None,
        "refresh_completed_at": None,
        "refresh_error": None,
        "chain_refresh_error": None,
    }


def _make_snapshot(generation: int = 1, entries: dict = None) -> dict:
    """Build a minimal snapshot for testing."""
    entries = entries or {}
    counts = {"ok": 0, "stale": 0, "error": 0, "warming": 0}
    for e in entries.values():
        status = e.get("status")
        if status in counts:
            counts[status] += 1
    
    return {
        "generation": generation,
        "entries": entries,
        "cycle_started_at": NOW.isoformat(),
        "cycle_finished_at": NOW.isoformat(),
        "cycle_duration_seconds": 5.0,
        "trigger": "scheduled",
        "truncated": False,
        "counts": counts,
    }


class TestModuleSingleton:
    def test_get_returns_same_instance_across_calls(self):
        cache1 = get_best_options_cache()
        cache2 = get_best_options_cache()
        assert cache1 is cache2

    def test_set_allows_explicit_instance_override(self):
        custom = BestOptionsCache()
        set_best_options_cache(custom)
        assert get_best_options_cache() is custom

    def test_set_none_clears_instance_forcing_new_on_next_get(self):
        cache1 = get_best_options_cache()
        set_best_options_cache(None)
        cache2 = get_best_options_cache()
        assert cache1 is not cache2


class TestInitialState:
    def test_new_cache_starts_empty_generation_zero(self):
        cache = BestOptionsCache()
        snapshot = cache.snapshot()
        assert snapshot["generation"] == 0
        assert snapshot["entries"] == {}
        assert snapshot["counts"] == {"ok": 0, "stale": 0, "error": 0, "warming": 0}

    def test_is_empty_true_on_new_cache(self):
        cache = BestOptionsCache()
        assert cache.is_empty() is True

    def test_get_entry_returns_none_for_absent_symbol(self):
        cache = BestOptionsCache()
        assert cache.get_entry("AAA") is None


class TestPublishSnapshot:
    def test_publish_replaces_entire_snapshot_atomically(self):
        cache = BestOptionsCache()
        snapshot1 = _make_snapshot(generation=1, entries={"AAA": _make_entry("AAA")})
        cache.publish_snapshot(snapshot1)
        assert cache.snapshot() is snapshot1

        snapshot2 = _make_snapshot(generation=2, entries={"BBB": _make_entry("BBB")})
        cache.publish_snapshot(snapshot2)
        assert cache.snapshot() is snapshot2
        assert "AAA" not in cache.snapshot()["entries"]
        assert "BBB" in cache.snapshot()["entries"]

    def test_publish_advances_generation(self):
        cache = BestOptionsCache()
        cache.publish_snapshot(_make_snapshot(generation=1))
        assert cache.snapshot()["generation"] == 1

        cache.publish_snapshot(_make_snapshot(generation=2))
        assert cache.snapshot()["generation"] == 2

    def test_is_empty_false_after_first_publish(self):
        cache = BestOptionsCache()
        cache.publish_snapshot(_make_snapshot(generation=1))
        assert cache.is_empty() is False


class TestReplaceSymbol:
    def test_replace_symbol_updates_only_specified_symbol(self):
        cache = BestOptionsCache()
        initial = _make_snapshot(
            generation=5,
            entries={
                "AAA": _make_entry("AAA", generation=5),
                "BBB": _make_entry("BBB", generation=5),
            },
        )
        cache.publish_snapshot(initial)

        new_aaa = _make_entry("AAA", status="stale", generation=5)
        cache.replace_symbol("AAA", new_aaa)

        snapshot = cache.snapshot()
        assert snapshot["entries"]["AAA"]["status"] == "stale"
        assert snapshot["entries"]["BBB"]["status"] == "ok"  # unchanged

    def test_replace_symbol_preserves_other_entries_object_identity(self):
        cache = BestOptionsCache()
        aaa_entry = _make_entry("AAA")
        bbb_entry = _make_entry("BBB")
        initial = _make_snapshot(generation=1, entries={"AAA": aaa_entry, "BBB": bbb_entry})
        cache.publish_snapshot(initial)

        new_aaa = _make_entry("AAA", status="stale")
        cache.replace_symbol("AAA", new_aaa)

        snapshot = cache.snapshot()
        assert snapshot["entries"]["AAA"] is new_aaa
        assert snapshot["entries"]["BBB"] is bbb_entry  # object identity preserved

    def test_replace_symbol_does_not_advance_generation(self):
        cache = BestOptionsCache()
        cache.publish_snapshot(_make_snapshot(generation=5, entries={"AAA": _make_entry("AAA")}))
        cache.replace_symbol("AAA", _make_entry("AAA", status="stale"))
        assert cache.snapshot()["generation"] == 5  # unchanged

    def test_replace_symbol_sets_trigger_to_symbol_refresh(self):
        cache = BestOptionsCache()
        cache.publish_snapshot(_make_snapshot(generation=1, entries={"AAA": _make_entry("AAA")}))
        cache.replace_symbol("AAA", _make_entry("AAA"))
        assert cache.snapshot()["trigger"] == "symbol_refresh"

    def test_replace_symbol_can_override_trigger(self):
        cache = BestOptionsCache()
        cache.publish_snapshot(_make_snapshot(generation=1, entries={"AAA": _make_entry("AAA")}))
        cache.replace_symbol("AAA", _make_entry("AAA"), trigger="manual")
        assert cache.snapshot()["trigger"] == "manual"

    def test_replace_symbol_recomputes_counts(self):
        cache = BestOptionsCache()
        initial = _make_snapshot(
            generation=1,
            entries={
                "AAA": _make_entry("AAA", status="ok"),
                "BBB": _make_entry("BBB", status="ok"),
            },
        )
        cache.publish_snapshot(initial)
        assert cache.snapshot()["counts"]["ok"] == 2
        assert cache.snapshot()["counts"]["stale"] == 0

        cache.replace_symbol("AAA", _make_entry("AAA", status="stale"))
        assert cache.snapshot()["counts"]["ok"] == 1
        assert cache.snapshot()["counts"]["stale"] == 1

    def test_replace_symbol_normalizes_symbol_key(self):
        cache = BestOptionsCache()
        cache.publish_snapshot(_make_snapshot(generation=1, entries={"AAA": _make_entry("AAA")}))
        cache.replace_symbol(" aaa ", _make_entry("AAA", status="stale"))
        assert "AAA" in cache.snapshot()["entries"]
        assert cache.snapshot()["entries"]["AAA"]["status"] == "stale"

    def test_replace_symbol_can_add_new_symbol(self):
        cache = BestOptionsCache()
        cache.publish_snapshot(_make_snapshot(generation=1, entries={"AAA": _make_entry("AAA")}))
        cache.replace_symbol("BBB", _make_entry("BBB"))
        snapshot = cache.snapshot()
        assert "AAA" in snapshot["entries"]
        assert "BBB" in snapshot["entries"]
        assert snapshot["counts"]["ok"] == 2


class TestGetEntry:
    def test_get_entry_returns_entry_when_present(self):
        cache = BestOptionsCache()
        aaa_entry = _make_entry("AAA")
        cache.publish_snapshot(_make_snapshot(generation=1, entries={"AAA": aaa_entry}))
        assert cache.get_entry("AAA") is aaa_entry

    def test_get_entry_returns_none_when_absent(self):
        cache = BestOptionsCache()
        cache.publish_snapshot(_make_snapshot(generation=1, entries={"AAA": _make_entry("AAA")}))
        assert cache.get_entry("BBB") is None

    def test_get_entry_normalizes_symbol(self):
        cache = BestOptionsCache()
        cache.publish_snapshot(_make_snapshot(generation=1, entries={"AAA": _make_entry("AAA")}))
        assert cache.get_entry(" aaa ") is not None
        assert cache.get_entry("aaa")["symbol"] == "AAA"


class TestThreadSafety:
    def test_snapshot_read_concurrent_with_publish_never_partial(self):
        cache = BestOptionsCache()
        cache.publish_snapshot(_make_snapshot(generation=1, entries={"AAA": _make_entry("AAA")}))

        read_generations = []
        publish_done = threading.Event()

        def reader():
            for _ in range(100):
                snapshot = cache.snapshot()
                gen = snapshot["generation"]
                read_generations.append(gen)
                if publish_done.is_set() and gen >= 2:
                    break

        def writer():
            for gen in range(2, 12):
                cache.publish_snapshot(_make_snapshot(generation=gen, entries={"AAA": _make_entry("AAA")}))
            publish_done.set()

        t_reader = threading.Thread(target=reader)
        t_writer = threading.Thread(target=writer)
        t_reader.start()
        t_writer.start()
        t_reader.join(timeout=5)
        t_writer.join(timeout=5)

        # Every generation read is a valid, complete snapshot (never torn reads)
        assert all(gen >= 1 for gen in read_generations)

    def test_replace_symbol_concurrent_with_reads_stays_atomic(self):
        cache = BestOptionsCache()
        initial = _make_snapshot(
            generation=1,
            entries={f"SYM{i}": _make_entry(f"SYM{i}") for i in range(10)},
        )
        cache.publish_snapshot(initial)

        def updater():
            for i in range(10):
                cache.replace_symbol(f"SYM{i}", _make_entry(f"SYM{i}", status="stale"))

        def reader():
            for _ in range(50):
                snapshot = cache.snapshot()
                # Every snapshot must have exactly 10 entries (never torn)
                assert len(snapshot["entries"]) == 10

        threads = [threading.Thread(target=updater), threading.Thread(target=reader)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

    def test_module_singleton_get_is_thread_safe(self):
        # Multiple threads calling get_best_options_cache() concurrently
        # should all get the same instance
        instances = []

        def get_and_store():
            instances.append(get_best_options_cache())

        threads = [threading.Thread(target=get_and_store) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # All threads got the same singleton instance
        assert all(inst is instances[0] for inst in instances)


class TestCopyOnWriteSemantics:
    def test_replace_symbol_does_not_mutate_old_snapshot_entries(self):
        cache = BestOptionsCache()
        aaa_v1 = _make_entry("AAA", status="ok")
        bbb_v1 = _make_entry("BBB", status="ok")
        cache.publish_snapshot(_make_snapshot(generation=1, entries={"AAA": aaa_v1, "BBB": bbb_v1}))

        old_snapshot = cache.snapshot()
        old_entries_map = old_snapshot["entries"]

        # Replace AAA
        aaa_v2 = _make_entry("AAA", status="stale")
        cache.replace_symbol("AAA", aaa_v2)

        # Old snapshot's entry map is unchanged
        assert old_entries_map["AAA"] is aaa_v1
        assert old_entries_map["AAA"]["status"] == "ok"

        # New snapshot has the new entry
        new_snapshot = cache.snapshot()
        assert new_snapshot["entries"]["AAA"] is aaa_v2
        assert new_snapshot["entries"]["AAA"]["status"] == "stale"

    def test_publish_snapshot_does_not_mutate_caller_supplied_snapshot(self):
        cache = BestOptionsCache()
        aaa_entry = _make_entry("AAA")
        snapshot_v1 = _make_snapshot(generation=1, entries={"AAA": aaa_entry})
        snapshot_v1_copy_for_test = dict(snapshot_v1)

        cache.publish_snapshot(snapshot_v1)

        # Publish a second snapshot
        cache.publish_snapshot(_make_snapshot(generation=2, entries={"BBB": _make_entry("BBB")}))

        # The original snapshot dict we passed is unchanged
        assert snapshot_v1 == snapshot_v1_copy_for_test


class TestStatusCounts:
    def test_counts_reflect_entry_statuses(self):
        cache = BestOptionsCache()
        entries = {
            "AAA": _make_entry("AAA", status="ok"),
            "BBB": _make_entry("BBB", status="stale"),
            "CCC": _make_entry("CCC", status="error"),
            "DDD": _make_entry("DDD", status="warming"),
        }
        cache.publish_snapshot(_make_snapshot(generation=1, entries=entries))
        counts = cache.snapshot()["counts"]
        assert counts == {"ok": 1, "stale": 1, "error": 1, "warming": 1}

    def test_replace_symbol_updates_counts_correctly(self):
        cache = BestOptionsCache()
        cache.publish_snapshot(
            _make_snapshot(
                generation=1,
                entries={
                    "AAA": _make_entry("AAA", status="ok"),
                    "BBB": _make_entry("BBB", status="ok"),
                },
            )
        )
        assert cache.snapshot()["counts"]["ok"] == 2

        cache.replace_symbol("AAA", _make_entry("AAA", status="error"))
        counts = cache.snapshot()["counts"]
        assert counts["ok"] == 1
        assert counts["error"] == 1


class TestImmutabilityContract:
    """Tests that verify the documented immutability contract: once published,
    entries and snapshots are never mutated. This is a discipline enforced by
    code review, not runtime checks."""

    def test_published_entry_is_not_modified_by_cache(self):
        cache = BestOptionsCache()
        aaa_entry = _make_entry("AAA")
        aaa_entry_copy = dict(aaa_entry)
        cache.publish_snapshot(_make_snapshot(generation=1, entries={"AAA": aaa_entry}))

        # Publish a new snapshot
        cache.publish_snapshot(_make_snapshot(generation=2, entries={"BBB": _make_entry("BBB")}))

        # The original entry dict is unchanged (cache never mutates published data)
        assert aaa_entry == aaa_entry_copy

    def test_snapshot_entries_map_is_not_mutated_after_publish(self):
        cache = BestOptionsCache()
        entries_map = {"AAA": _make_entry("AAA")}
        entries_map_copy = dict(entries_map)
        snapshot = _make_snapshot(generation=1, entries=entries_map)
        cache.publish_snapshot(snapshot)

        # Replace a symbol
        cache.replace_symbol("AAA", _make_entry("AAA", status="stale"))

        # The original entries map is unchanged
        assert entries_map == entries_map_copy
        assert entries_map["AAA"]["status"] == "ok"


class TestCarryForwardScenarios:
    """Tests for the carry-forward-on-failure pattern (§3 of design)."""

    def test_carry_forward_stale_entry_preserves_generation_and_computed_at(self):
        cache = BestOptionsCache()
        aaa_v1 = _make_entry("AAA", status="ok", generation=1)
        aaa_v1["computed_at"] = "2026-08-29T10:00:00Z"
        cache.publish_snapshot(_make_snapshot(generation=1, entries={"AAA": aaa_v1}))

        # Simulate carry-forward: same entry, status downgraded to stale
        aaa_v2 = dict(aaa_v1)
        aaa_v2["status"] = "stale"
        aaa_v2["error"] = "chain fetch failed"
        aaa_v2["reason"] = "chain_unreadable"
        # generation and computed_at stay unchanged (carry-forward semantics)

        cache.publish_snapshot(_make_snapshot(generation=2, entries={"AAA": aaa_v2}))

        entry = cache.get_entry("AAA")
        assert entry["status"] == "stale"
        assert entry["generation"] == 1  # original cycle
        assert entry["computed_at"] == "2026-08-29T10:00:00Z"  # original time
        assert entry["error"] == "chain fetch failed"
