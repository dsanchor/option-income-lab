"""Migration script tests — legacy symbol_config normalization.

Ref: danny-legacy-symbol-config-migration-contract.md §3–4

Coverage (MCG = MiGration Config):
  MCG-1   Already-canonical config → zero writes, classified already_canonical.
  MCG-2   Legacy 'NASDAQ' → normalized to XNAS, linked to existing security_master.
  MCG-3   Legacy 'NYSE' → normalized to XNYS, linked to existing security_master.
  MCG-4   Legacy 'AMEX' → normalized to XASE, linked to existing security_master.
  MCG-5   No existing security_master → new one created with migration marker.
  MCG-6   security_master exists under DIFFERENT MIC → collision_ambiguous, zero writes.
  MCG-7   Unrecognized exchange ('OTC') → unresolved_exchange, zero writes.
  MCG-8   Empty exchange string → unresolved_exchange, zero writes.
  MCG-9   CAS conflict (etag changed) → cas_conflict reported, doc not overwritten.
  MCG-10  --audit mode never calls create_item/replace_item.
  MCG-11  Backup file contains correct checksum and _etag capture.
  MCG-12  Restore round-trip: apply → restore → state equals original.
  MCG-13  Idempotent re-run: second --apply produces zero additional writes.
  MCG-14  Exit code 0 on normal run (including flagged items).
  MCG-15  Exit code 1 on bad CLI arguments (mutually exclusive flags).
  MCG-16  Exit code 2 when backup step fails.
  MCG-17  Exit code 3 on post-migration verification mismatch.
  MCG-18  LEGACY_ALIAS_TO_MIC in provider_symbols.py maps all three aliases.
  MCG-19  total_shares/watchlist/telegram never written by migration.
  MCG-20  migration_backups/ in .gitignore.

WILL FAIL until Livingston creates backend/scripts/migrate_legacy_symbol_config.py
and adds LEGACY_ALIAS_TO_MIC to backend/src/portfolio/provider_symbols.py (§5).
"""

from __future__ import annotations

import json
import hashlib
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from azure.cosmos.exceptions import CosmosResourceNotFoundError, CosmosHttpResponseError


# ---------------------------------------------------------------------------
# MCG-18: LEGACY_ALIAS_TO_MIC in provider_symbols.py — can test this now
# ---------------------------------------------------------------------------

class TestLegacyAliasMicTable:
    """MCG-18: LEGACY_ALIAS_TO_MIC must be in provider_symbols.py and correct.
    Livingston adds this as the first step in §3.1.
    FAILS until Livingston adds LEGACY_ALIAS_TO_MIC to provider_symbols.py.
    """

    def test_mcg18_legacy_alias_to_mic_importable(self):
        """MCG-18: LEGACY_ALIAS_TO_MIC exists in provider_symbols."""
        from src.portfolio.provider_symbols import LEGACY_ALIAS_TO_MIC  # noqa
        assert isinstance(LEGACY_ALIAS_TO_MIC, dict), (
            "MCG-18: LEGACY_ALIAS_TO_MIC must be a dict"
        )

    def test_mcg18_nyse_maps_to_xnys(self):
        """MCG-18: NYSE → XNYS."""
        from src.portfolio.provider_symbols import LEGACY_ALIAS_TO_MIC
        assert LEGACY_ALIAS_TO_MIC.get("NYSE") == "XNYS", (
            f"MCG-18: NYSE must map to XNYS, got {LEGACY_ALIAS_TO_MIC.get('NYSE')!r}"
        )

    def test_mcg18_nasdaq_maps_to_xnas(self):
        """MCG-18: NASDAQ → XNAS."""
        from src.portfolio.provider_symbols import LEGACY_ALIAS_TO_MIC
        assert LEGACY_ALIAS_TO_MIC.get("NASDAQ") == "XNAS"

    def test_mcg18_amex_maps_to_xase(self):
        """MCG-18: AMEX → XASE."""
        from src.portfolio.provider_symbols import LEGACY_ALIAS_TO_MIC
        assert LEGACY_ALIAS_TO_MIC.get("AMEX") == "XASE"

    def test_mcg18_table_has_exactly_three_entries(self):
        """MCG-18: exactly three entries — no speculative additions."""
        from src.portfolio.provider_symbols import LEGACY_ALIAS_TO_MIC
        assert len(LEGACY_ALIAS_TO_MIC) == 3, (
            f"MCG-18: LEGACY_ALIAS_TO_MIC must have exactly 3 entries (NYSE/NASDAQ/AMEX). "
            f"Got {len(LEGACY_ALIAS_TO_MIC)}: {list(LEGACY_ALIAS_TO_MIC.keys())}"
        )

    def test_mcg18_gitignore_has_migration_backups(self):
        """MCG-20: migration_backups/ must be in .gitignore.
        FAILS until Livingston adds it per §3.3 (operational artifacts not source).
        """
        gitignore = Path(__file__).resolve().parents[2] / ".gitignore"
        if not gitignore.exists():
            pytest.fail(".gitignore not found — Livingston: create it or add the entry")
        content = gitignore.read_text()
        assert "migration_backups" in content, (
            "MCG-20: 'migration_backups' must be in .gitignore. "
            "Livingston: add 'backend/scripts/migration_backups/' to .gitignore "
            "per §3.3 — backup files are operational artifacts, not source."
        )


# ---------------------------------------------------------------------------
# Import the migration script — will fail until Livingston creates it
# ---------------------------------------------------------------------------

try:
    from scripts.migrate_legacy_symbol_config import (
        MigrationReport,
        audit,
        backup,
        apply_migration,
        restore_migration,
    )
    _SCRIPT_AVAILABLE = True
except ImportError:
    _SCRIPT_AVAILABLE = False
    MigrationReport = audit = backup = apply_migration = restore_migration = None  # type: ignore

_skip_if_no_script = pytest.mark.skipif(
    not _SCRIPT_AVAILABLE,
    reason=(
        "scripts.migrate_legacy_symbol_config not yet created. "
        "Livingston: implement backend/scripts/migrate_legacy_symbol_config.py per §3. "
        "MCG-18 (LEGACY_ALIAS_TO_MIC) is the only test that can run independently."
    ),
)


# ---------------------------------------------------------------------------
# Fake Cosmos container with ETag support for CAS testing
# ---------------------------------------------------------------------------

class FakeMigrationContainer:
    """In-memory container for migration tests.

    Stores symbol_config and security_master docs.
    Bumps _etag on every write (simulates Cosmos ETag behavior).
    """

    def __init__(self):
        self._store: dict = {}  # (partition_key, id) → doc
        self._write_calls: list = []

    def _etag(self, pk: str, id_: str) -> str:
        return f"etag-{pk}-{id_}-v{len(self._store.get((pk, id_), {}))}"

    def read_item(self, item: str, partition_key: str) -> dict:
        key = (partition_key, item)
        if key not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[key])

    def create_item(self, body: dict) -> dict:
        ticker = body.get("symbol", "")
        key = (ticker, body["id"])
        if key in self._store:
            raise CosmosHttpResponseError(status_code=409, message="Conflict", response=None)
        doc = {**body, "_etag": f"etag-new-{body['id']}"}
        self._store[key] = doc
        self._write_calls.append(("create", body["id"]))
        return dict(doc)

    def replace_item(self, item: str, body: dict, etag: str | None = None, **kw) -> dict:
        for key, stored in list(self._store.items()):
            if stored.get("id") == item:
                if etag and stored.get("_etag") != etag:
                    raise CosmosHttpResponseError(
                        status_code=412, message="Precondition Failed", response=None
                    )
                new_etag = f"etag-updated-{item}-{len(self._store)}"
                updated = {**body, "_etag": new_etag}
                self._store[key] = updated
                self._write_calls.append(("replace", item))
                return dict(updated)
        raise CosmosResourceNotFoundError(message="not found", response=None)

    def query_items(self, query="", parameters=None,
                    enable_cross_partition_query=False, partition_key=None):
        param_map = {p["name"]: p["value"] for p in (parameters or [])}
        results = []
        for (pk, did), doc in self._store.items():
            if partition_key and pk != partition_key:
                continue
            # Filter by doc_type based on query string
            if "doc_type = 'symbol_config'" in query:
                if doc.get("doc_type") != "symbol_config":
                    continue
            elif "doc_type = 'security_master'" in query:
                if doc.get("doc_type") != "security_master":
                    continue
            # Filter by @id parameter if present
            if "@id" in param_map and doc.get("id") != param_map["@id"]:
                continue
            results.append(dict(doc))
        return iter(results)

    def seed_config(self, ticker: str, exchange: str, extra: dict | None = None) -> dict:
        doc = {
            "id": f"config_{ticker}",
            "symbol": ticker,
            "doc_type": "symbol_config",
            "exchange": exchange,
            "display_name": f"{ticker} Corp.",
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
            "telegram_notifications_enabled": False,
            "_etag": f"etag-config-{ticker}-v0",
        }
        if extra:
            doc.update(extra)
        self._store[(ticker, doc["id"])] = doc
        return doc

    def seed_security(self, security_id: str, company_name="Test Co.") -> dict:
        mic, ticker = security_id.split(":", 1)
        doc = {
            "id": f"sec_{mic}_{ticker}",
            "symbol": ticker,
            "doc_type": "security_master",
            "security_id": security_id,
            "exchange_mic": mic,
            "ticker": ticker,
            "company_name": company_name,
            "_etag": f"etag-sec-{security_id.replace(':', '-')}-v0",
        }
        self._store[(ticker, doc["id"])] = doc
        return doc

    @property
    def write_count(self) -> int:
        return len(self._write_calls)


# ---------------------------------------------------------------------------
# MCG-1..8: classification and write contract
# ---------------------------------------------------------------------------

@_skip_if_no_script
class TestMigrationClassification:
    def test_mcg1_already_canonical_zero_writes(self):
        """MCG-1: Config with valid MIC exchange and security_id → already_canonical, zero writes."""
        container = FakeMigrationContainer()
        # Seed already-canonical config
        container.seed_config("AAPL", "XNYS", extra={"security_id": "XNYS:AAPL"})
        container.seed_security("XNYS:AAPL")

        report = audit(container)

        assert report.already_canonical >= 1, "MCG-1: AAPL must be classified already_canonical"
        assert container.write_count == 0, "MCG-1: audit must produce zero writes"

    def test_mcg2_legacy_nasdaq_normalized_to_xnas(self):
        """MCG-2: exchange='NASDAQ' normalized to XNAS, linked to existing security_master."""
        container = FakeMigrationContainer()
        container.seed_config("MSFT", "NASDAQ")
        container.seed_security("XNAS:MSFT")

        report = apply_migration(container, dry_run=False)

        assert report.normalized_exchange_count >= 1, (
            "MCG-2: MSFT NASDAQ→XNAS normalization must be counted"
        )
        assert report.security_master_linked_count >= 1, "MCG-2: must link security_master"
        # Verify the doc was updated
        updated = container.read_item("config_MSFT", "MSFT")
        assert updated.get("exchange") == "XNAS", (
            f"MCG-2: exchange must be XNAS after migration, got {updated.get('exchange')!r}"
        )
        assert updated.get("security_id") == "XNAS:MSFT", (
            "MCG-2: security_id must be set after migration"
        )

    def test_mcg3_legacy_nyse_normalized_to_xnys(self):
        """MCG-3: exchange='NYSE' normalized to XNYS."""
        container = FakeMigrationContainer()
        container.seed_config("ABBV", "NYSE")
        container.seed_security("XNYS:ABBV")

        report = apply_migration(container, dry_run=False)

        updated = container.read_item("config_ABBV", "ABBV")
        assert updated.get("exchange") == "XNYS"
        assert updated.get("security_id") == "XNYS:ABBV"

    def test_mcg4_legacy_amex_normalized_to_xase(self):
        """MCG-4: exchange='AMEX' normalized to XASE."""
        container = FakeMigrationContainer()
        container.seed_config("SPY", "AMEX")
        container.seed_security("XASE:SPY")

        report = apply_migration(container, dry_run=False)

        updated = container.read_item("config_SPY", "SPY")
        assert updated.get("exchange") == "XASE"
        assert updated.get("security_id") == "XASE:SPY"

    def test_mcg5_no_security_master_creates_one_with_marker(self):
        """MCG-5: No existing security_master → new one created with created_by_migration=True."""
        container = FakeMigrationContainer()
        container.seed_config("NEWCO", "NYSE")
        # No security_master seeded

        report = apply_migration(container, dry_run=False)

        assert report.security_master_created_count >= 1, (
            "MCG-5: A new security_master must be created for NEWCO"
        )
        # Created security_master must have migration marker
        sec = container.read_item("sec_XNYS_NEWCO", "NEWCO")
        assert sec.get("created_by_migration") is True, (
            "MCG-5: migration-created security_master must have created_by_migration=True"
        )

    def test_mcg6_different_mic_collision_ambiguous(self):
        """MCG-6: security_master exists under different MIC → collision_ambiguous, zero writes."""
        container = FakeMigrationContainer()
        container.seed_config("ABB", "NYSE")
        # A security_master exists, but under XSWX (not XNYS)
        container.seed_security("XSWX:ABB", "ABB Ltd.")

        initial_write_count = container.write_count
        report = audit(container)

        assert len(report.collision_ambiguous) >= 1, (
            "MCG-6: ABB with XSWX collision must be in collision_ambiguous list"
        )
        assert container.write_count == initial_write_count, (
            "MCG-6: collision_ambiguous must produce zero writes"
        )

    def test_mcg7_unrecognized_exchange_skipped(self):
        """MCG-7: unrecognized exchange 'OTC' → unresolved_exchange, zero writes."""
        container = FakeMigrationContainer()
        container.seed_config("OTCCO", "OTC")

        initial_write_count = container.write_count
        report = apply_migration(container, dry_run=False)

        assert len(report.unresolved_exchange) >= 1, (
            "MCG-7: OTC exchange must be in unresolved_exchange list"
        )
        assert container.write_count == initial_write_count, (
            "MCG-7: unresolved_exchange must produce zero writes"
        )

    def test_mcg8_empty_exchange_string_unresolved(self):
        """MCG-8: empty exchange string → unresolved_exchange, zero writes."""
        container = FakeMigrationContainer()
        container.seed_config("EMPTY", "")

        initial_write_count = container.write_count
        report = apply_migration(container, dry_run=False)

        assert len(report.unresolved_exchange) >= 1
        assert container.write_count == initial_write_count


@_skip_if_no_script
class TestCasConflict:
    def test_mcg9_cas_conflict_skips_without_overwrite(self):
        """MCG-9: if etag changed between backup and write → cas_conflict, doc unchanged."""
        container = FakeMigrationContainer()
        container.seed_config("CLASH", "NYSE")
        container.seed_security("XNYS:CLASH")

        # Simulate CAS conflict by patching replace_item to raise 412
        original_replace = container.replace_item
        call_count = [0]

        def _conflict_replace(item, body, etag=None, **kw):
            call_count[0] += 1
            raise CosmosHttpResponseError(
                status_code=412, message="ETag conflict", response=None
            )

        container.replace_item = _conflict_replace

        report = apply_migration(container, dry_run=False)

        assert report.cas_conflicts >= 1, (
            "MCG-9: ETag 412 must be classified as cas_conflict"
        )
        # The doc must be unchanged (replace was not re-tried with wrong data)
        container.replace_item = original_replace
        doc = container.read_item("config_CLASH", "CLASH")
        assert doc.get("exchange") == "NYSE", (
            "MCG-9: doc exchange must remain 'NYSE' after CAS conflict (not overwritten)"
        )


@_skip_if_no_script
class TestAuditMode:
    def test_mcg10_audit_never_writes(self):
        """MCG-10: --audit mode (dry_run=True) never calls create_item or replace_item."""
        container = FakeMigrationContainer()
        container.seed_config("NYSESTOCK", "NYSE")
        container.seed_config("ALREADY", "XNYS", extra={"security_id": "XNYS:ALREADY"})
        container.seed_security("XNYS:ALREADY")

        report = audit(container)

        assert container.write_count == 0, (
            f"MCG-10: --audit must produce zero writes, got {container.write_count} "
            f"calls: {container._write_calls}"
        )


@_skip_if_no_script
class TestIdempotency:
    def test_mcg13_second_apply_produces_zero_writes(self):
        """MCG-13: running --apply twice → second run produces zero writes."""
        container = FakeMigrationContainer()
        container.seed_config("MSFT", "NASDAQ")
        container.seed_security("XNAS:MSFT")

        # First run
        report1 = apply_migration(container, dry_run=False)
        writes_after_first = container.write_count

        # Second run
        report2 = apply_migration(container, dry_run=False)
        writes_after_second = container.write_count

        assert writes_after_second == writes_after_first, (
            f"MCG-13: second --apply must produce zero new writes, "
            f"got {writes_after_second - writes_after_first} new writes"
        )
        assert report2.already_canonical >= 1, (
            "MCG-13: second run must classify already-migrated doc as already_canonical"
        )


@_skip_if_no_script
class TestBackupAndRestore:
    def test_mcg11_backup_contains_checksum_and_etag(self, tmp_path):
        """MCG-11: backup file has sha256 checksum and _etag per document."""
        container = FakeMigrationContainer()
        container.seed_config("AAPL", "XNYS", extra={"security_id": "XNYS:AAPL"})

        backup_path = tmp_path / "test_backup.json"
        backup(container, backup_path)

        assert backup_path.exists(), "MCG-11: backup file must be created"
        data = json.loads(backup_path.read_text())

        assert "sha256" in data, "MCG-11: backup must contain sha256 checksum"
        assert "generated_at" in data, "MCG-11: backup must contain generated_at"
        assert "documents" in data, "MCG-11: backup must contain documents list"

        # Verify checksum integrity
        docs_json = json.dumps(data["documents"], sort_keys=True)
        expected_sha = hashlib.sha256(docs_json.encode()).hexdigest()
        assert data["sha256"] == expected_sha, (
            "MCG-11: checksum must match serialized documents"
        )

        # Each doc must have _etag captured separately
        for doc in data["documents"]:
            assert "_etag" in doc, f"MCG-11: doc {doc.get('id')} missing _etag in backup"

    def test_mcg12_restore_reverts_to_pre_migration_state(self, tmp_path):
        """MCG-12: apply → restore → state equals original backup."""
        container = FakeMigrationContainer()
        container.seed_config("MSFT", "NASDAQ")
        container.seed_security("XNAS:MSFT")

        # Take backup
        backup_path = tmp_path / "restore_test.json"
        backup(container, backup_path)

        # Apply migration
        apply_migration(container, dry_run=False)
        # Verify migration changed the exchange
        updated = container.read_item("config_MSFT", "MSFT")
        assert updated.get("exchange") == "XNAS", "Migration must have changed exchange first"

        # Restore
        restore_migration(container, backup_path)

        # State must be restored
        restored = container.read_item("config_MSFT", "MSFT")
        assert restored.get("exchange") == "NASDAQ", (
            f"MCG-12: restore must revert exchange to 'NASDAQ', got {restored.get('exchange')!r}"
        )
        assert "security_id" not in restored or restored.get("security_id") is None, (
            "MCG-12: restore must remove security_id that was added by migration"
        )


@_skip_if_no_script
class TestInvariantFields:
    def test_mcg19_watchlist_flags_never_written(self):
        """MCG-19: total_shares/watchlist/telegram never modified by migration."""
        container = FakeMigrationContainer()
        original_flags = {
            "watchlist": {"covered_call": True, "cash_secured_put": True, "buy_tracker": False},
            "telegram_notifications_enabled": True,
            "total_shares": 42,
        }
        container.seed_config("LEGACY", "NYSE", extra=original_flags)
        container.seed_security("XNYS:LEGACY")

        apply_migration(container, dry_run=False)

        updated = container.read_item("config_LEGACY", "LEGACY")
        assert updated.get("watchlist") == original_flags["watchlist"], (
            "MCG-19: watchlist flags must never be touched by migration"
        )
        assert updated.get("telegram_notifications_enabled") == True, (
            "MCG-19: telegram_notifications_enabled must never be touched"
        )
        assert updated.get("total_shares") == 42, (
            "MCG-19: total_shares must never be touched"
        )


@_skip_if_no_script
class TestExitCodes:
    def test_mcg14_exit_code_0_on_normal_run(self):
        """MCG-14: normal run (including flagged items) → exit code 0."""
        container = FakeMigrationContainer()
        container.seed_config("OTC_GHOST", "OTC")  # flagged but not an error

        report = apply_migration(container, dry_run=False)
        assert report.exit_code == 0, (
            f"MCG-14: exit code must be 0 on normal run (flagged OK), got {report.exit_code}"
        )

    def test_mcg17_verification_mismatch_exit_code_3(self):
        """MCG-17: post-migration verification failure → exit code 3."""
        container = FakeMigrationContainer()
        container.seed_config("BUGGY", "NYSE")
        container.seed_security("XNYS:BUGGY")

        # Simulate verification failure by patching verify step
        with patch("scripts.migrate_legacy_symbol_config._verify_migration") as mock_verify:
            mock_verify.side_effect = RuntimeError("Verification failed: exchange still NASDAQ")
            report = apply_migration(container, dry_run=False)

        assert report.exit_code == 3, (
            f"MCG-17: verification failure must return exit code 3, got {report.exit_code}"
        )
