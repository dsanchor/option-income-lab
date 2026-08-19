"""
Test suite for ``scripts/repair_options_chain_shards.py`` (Z-P6 /
Danny's "Zero-Free Agent-Facing Option Chains" decision, §4.4).

Two layers:
  - ``_FakeStore``-based unit tests exercise the script's own control
    flow (unavailable store, ``--limit`` budget, per-shard error
    classification) in isolation from real Cosmos/CAS behavior.
  - End-to-end tests compose the script against a *real*
    ``OptionsChainStore`` + the existing hermetic ``FakeContainer`` (the
    same fake used by ``test_options_chain_store.py``), proving
    dry-run/apply/idempotence/CAS-safety through the actual repair path,
    not just through the store's own already-tested methods.

All tests monkeypatch ``scripts.repair_options_chain_shards.get_options_chain_store``
directly rather than touching the process-wide singleton, so this file
never leaks retry/backoff state into other test modules.
"""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.repair_options_chain_shards as repair_script
from src.options_chain_store import OptionsChainStore
from tests.test_options_chain_store import FakeContainer, _contract, _future_exp_key


# ===========================================================================
# Fake store — isolates the script's own control flow from real CAS/Cosmos
# ===========================================================================

class _FakeStore:
    def __init__(self, *, available=True, symbols=None, expirations=None, repair_results=None):
        self._available = available
        self._symbols = symbols or []
        self._expirations = expirations or {}
        self._repair_results = repair_results or {}
        self.repair_calls = []

    def is_available(self):
        return self._available

    def list_symbols_with_shards(self):
        return list(self._symbols)

    def list_shard_expirations(self, symbol):
        return list(self._expirations.get(symbol, []))

    def repair_shard(self, symbol, exp_key, *, dry_run=True):
        self.repair_calls.append((symbol, exp_key, dry_run))
        return copy.deepcopy(
            self._repair_results.get(
                (symbol, exp_key), {"changed": False, "written": False, "error": None}
            )
        )


@pytest.fixture
def patch_store(monkeypatch):
    """Returns a function that installs a given fake/real store as the
    script's ``get_options_chain_store()``."""

    def _patch(store):
        monkeypatch.setattr(repair_script, "get_options_chain_store", lambda: store)
        return store

    return _patch


class TestUnavailableStore:
    def test_run_reports_unavailable_without_raising(self, patch_store):
        patch_store(_FakeStore(available=False))
        report = repair_script.run(symbol=None, all_symbols=True, dry_run=True, limit=None)
        assert report.shards_scanned == 0
        assert report.errors == 0

    def test_main_exits_zero_when_unavailable(self, patch_store, capsys):
        patch_store(_FakeStore(available=False))
        exit_code = repair_script.main(["--all"])
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "DRY RUN" in out


class TestNoShardsFound:
    def test_all_with_no_symbols_is_a_clean_noop(self, patch_store):
        patch_store(_FakeStore(available=True, symbols=[]))
        report = repair_script.run(symbol=None, all_symbols=True, dry_run=True, limit=None)
        assert report.shards_scanned == 0
        assert report.errors == 0


class TestSingleSymbolTargeting:
    def test_symbol_flag_only_repairs_that_symbol(self, patch_store):
        store = _FakeStore(
            available=True,
            expirations={"AAPL": ["20260901", "20260902"], "MSFT": ["20260901"]},
            repair_results={
                ("AAPL", "20260901"): {"changed": True, "written": True, "error": None},
                ("AAPL", "20260902"): {"changed": False, "written": False, "error": None},
            },
        )
        patch_store(store)
        report = repair_script.run(symbol="AAPL", all_symbols=False, dry_run=False, limit=None)
        assert report.shards_scanned == 2
        assert report.shards_changed == 1
        assert report.shards_written == 1
        assert all(call[0] == "AAPL" for call in store.repair_calls)


class TestAllSymbolsSweep:
    def test_all_flag_covers_every_symbol(self, patch_store):
        store = _FakeStore(
            available=True,
            symbols=["AAPL", "MSFT"],
            expirations={"AAPL": ["20260901"], "MSFT": ["20260902", "20260903"]},
        )
        patch_store(store)
        report = repair_script.run(symbol=None, all_symbols=True, dry_run=True, limit=None)
        assert report.shards_scanned == 3
        symbols_seen = {call[0] for call in store.repair_calls}
        assert symbols_seen == {"AAPL", "MSFT"}
        # dry_run flag must propagate through to every repair_shard call.
        assert all(call[2] is True for call in store.repair_calls)


class TestLimitBudget:
    def test_limit_bounds_total_shards_scanned_across_symbols(self, patch_store):
        store = _FakeStore(
            available=True,
            symbols=["AAPL", "MSFT"],
            expirations={
                "AAPL": ["20260901", "20260902", "20260903"],
                "MSFT": ["20260901", "20260902", "20260903"],
            },
        )
        patch_store(store)
        report = repair_script.run(symbol=None, all_symbols=True, dry_run=True, limit=2)
        assert report.shards_scanned == 2
        assert len(store.repair_calls) == 2


class TestErrorClassification:
    def test_shard_not_found_race_is_silently_skipped(self, patch_store):
        store = _FakeStore(
            available=True,
            expirations={"AAPL": ["20260901"]},
            repair_results={("AAPL", "20260901"): {"changed": False, "written": False, "error": "shard not found"}},
        )
        patch_store(store)
        report = repair_script.run(symbol="AAPL", all_symbols=False, dry_run=True, limit=None)
        assert report.errors == 0
        assert report.cas_conflicts == 0
        assert report.shards_scanned == 1

    def test_cas_conflict_error_counted_separately_from_hard_errors(self, patch_store):
        store = _FakeStore(
            available=True,
            expirations={"AAPL": ["20260901"]},
            repair_results={("AAPL", "20260901"): {"changed": True, "written": False, "error": "412 Precondition failed (conflict)"}},
        )
        patch_store(store)
        report = repair_script.run(symbol="AAPL", all_symbols=False, dry_run=False, limit=None)
        assert report.cas_conflicts == 1
        assert report.errors == 0

    def test_genuine_error_is_reported_and_counted(self, patch_store):
        store = _FakeStore(
            available=True,
            expirations={"AAPL": ["20260901"]},
            repair_results={("AAPL", "20260901"): {"changed": False, "written": False, "error": "connection reset"}},
        )
        patch_store(store)
        report = repair_script.run(symbol="AAPL", all_symbols=False, dry_run=False, limit=None)
        assert report.errors == 1
        assert "AAPL/20260901" in report.error_details[0]


# ===========================================================================
# End-to-end: real OptionsChainStore + hermetic FakeContainer
# ===========================================================================

class TestEndToEndAgainstRealStore:
    def _seed_legacy_shard(self, container, symbol, exp):
        legacy = _contract(expiration=exp, iv=0.0, delta=0.5)
        legacy.pop("_meta", None)
        container.store[f"optchain_{symbol}_{exp}"] = {
            "id": f"optchain_{symbol}_{exp}", "symbol": symbol, "doc_type": "options_chain",
            "expiration": exp, "schema_version": 2,
            "calls": {"100.0": legacy}, "puts": {}, "_etag": "e1",
        }

    def test_dry_run_default_never_writes(self, patch_store):
        container = FakeContainer()
        store = OptionsChainStore(container=container)
        exp = _future_exp_key(5)
        self._seed_legacy_shard(container, "AAPL", exp)
        patch_store(store)

        exit_code = repair_script.main(["--symbol", "AAPL"])

        assert exit_code == 0
        # No --apply flag ⇒ dry-run ⇒ untouched.
        assert container.store[f"optchain_AAPL_{exp}"]["calls"]["100.0"]["delta"] == 0.5
        assert container.store[f"optchain_AAPL_{exp}"]["schema_version"] == 2

    def test_apply_writes_and_preserves_observed_zero(self, patch_store):
        container = FakeContainer()
        store = OptionsChainStore(container=container)
        exp = _future_exp_key(5)
        legacy = _contract(expiration=exp, iv=0.0, delta=0.5, bid=0.0)
        legacy.pop("_meta", None)
        container.store[f"optchain_AAPL_{exp}"] = {
            "id": f"optchain_AAPL_{exp}", "symbol": "AAPL", "doc_type": "options_chain",
            "expiration": exp, "schema_version": 2,
            "calls": {"100.0": legacy}, "puts": {}, "_etag": "e1",
        }
        patch_store(store)

        exit_code = repair_script.main(["--symbol", "AAPL", "--apply"])

        assert exit_code == 0
        repaired = container.store[f"optchain_AAPL_{exp}"]["calls"]["100.0"]
        # Fabricated derived field nulled...
        assert repaired["delta"] is None
        # ...but an *observed* raw zero (a real market quote of 0.0) is
        # preserved byte-for-byte — repair must never touch bid/ask/etc.
        assert repaired["bid"] == 0.0
        assert container.store[f"optchain_AAPL_{exp}"]["schema_version"] == 4

    def test_apply_is_idempotent_second_run_no_writes(self, patch_store):
        container = FakeContainer()
        store = OptionsChainStore(container=container)
        exp = _future_exp_key(5)
        self._seed_legacy_shard(container, "AAPL", exp)
        patch_store(store)

        repair_script.main(["--symbol", "AAPL", "--apply"])
        etag_after_first = container.store[f"optchain_AAPL_{exp}"]["_etag"]

        repair_script.main(["--symbol", "AAPL", "--apply"])
        etag_after_second = container.store[f"optchain_AAPL_{exp}"]["_etag"]

        assert etag_after_first == etag_after_second

    def test_all_flag_sweeps_multiple_symbols_end_to_end(self, patch_store, capsys):
        container = FakeContainer()
        store = OptionsChainStore(container=container)
        exp = _future_exp_key(5)
        self._seed_legacy_shard(container, "AAPL", exp)
        self._seed_legacy_shard(container, "MSFT", exp)
        patch_store(store)

        exit_code = repair_script.main(["--all", "--apply"])

        assert exit_code == 0
        assert container.store[f"optchain_AAPL_{exp}"]["calls"]["100.0"]["delta"] is None
        assert container.store[f"optchain_MSFT_{exp}"]["calls"]["100.0"]["delta"] is None
        out = capsys.readouterr().out
        assert "shards_scanned:  2" in out
