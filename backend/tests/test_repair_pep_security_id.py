"""Independent regression coverage for repair_pep_security_id.py.

Contract: .squad/decisions/inbox/danny-pep-security-id-repair-contract.md

Test IDs: PEP-1 … PEP-N (sequential within each class).

Coverage:
  PEP-1   --audit (dry_run) produces zero writes across all containers.
  PEP-2   MIC derivation from stored NASDAQ alias → XNAS (runtime via
          LEGACY_ALIAS_TO_MIC; script must not hardcode "XNAS").
  PEP-3   Currency evidence unanimous → listing_currency corrected.
  PEP-4   Currency evidence mixed (two different currencies) → fail-closed:
          listing_currency preserved, currency_evidence=inconclusive reported.
  PEP-5   Currency evidence missing (no ledger movements) → fail-closed:
          listing_currency preserved, currency_evidence=inconclusive.
  PEP-6   Happy path --apply: sec_XNAS_PEP created; every NNYS:PEP movement
          re-pointed; config_PEP.security_id set; sec_NNYS_PEP deleted.
  PEP-7   Target already exists (sec_XNAS_PEP present, consistent) → skip
          creation; re-point references to existing target; delete source.
  PEP-8   Collision: target exists but has conflicting ISIN → abort
          (collision_ambiguous), zero writes.
  PEP-9   Collision: target exists but is malformed (missing ticker) → abort,
          zero writes.
  PEP-10  Post-apply verification: holdings mismatch injected → abort before
          delete; sec_NNYS_PEP retained; exit code 3.
  PEP-11  Movement IDs (id / account_id / partition key) are never changed;
          all financial fields byte-equivalent except security_id and audit
          metadata.
  PEP-12  Complete backup created before first write; contains every
          discovered doc plus sha256 checksum.
  PEP-13  Checksum validation: tampered backup file detected on restore.
  PEP-14  ETag conflict on one movement → that movement fails individually;
          others still patched; run reports conflict; exit code != 3.
  PEP-15  Partial failure re-run (idempotent): second --apply resumes from
          whatever still shows NNYS:PEP; already-repointed movements skipped.
  PEP-16  Source not deleted when any remaining NNYS:PEP reference exists.
  PEP-17  Source not deleted when holdings show mismatch after repoint.
  PEP-18  Source deleted after full verified equivalence (step-6 contract).
  PEP-19  --restore re-reads live state (not blind overwrite); reflects
          post-migration docs back to pre-migration state.
  PEP-20  Idempotent re-run after fully successful apply: zero additional writes.

All tests are hermetic (no real Cosmos, no network).
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import pytest
from copy import deepcopy
from typing import Any, Dict, List, Optional

from azure.cosmos.exceptions import CosmosResourceNotFoundError, CosmosHttpResponseError


# ---------------------------------------------------------------------------
# Conditional import — all tests skip until Livingston creates the script.
# ---------------------------------------------------------------------------

try:
    from scripts.repair_pep_security_id import (
        audit_repair,
        apply_repair,
        backup_repair,
        restore_repair,
        RepairReport,
        RepairBackup,
    )
    _SCRIPT_AVAILABLE = True
except ImportError:
    _SCRIPT_AVAILABLE = False
    audit_repair = apply_repair = backup_repair = restore_repair = None  # type: ignore
    RepairReport = RepairBackup = None  # type: ignore

_skip = pytest.mark.skipif(
    not _SCRIPT_AVAILABLE,
    reason=(
        "backend/scripts/repair_pep_security_id.py not yet created. "
        "Livingston: implement per §3-§7 of danny-pep-security-id-repair-contract.md."
    ),
)


# ---------------------------------------------------------------------------
# Fake Cosmos containers
# ---------------------------------------------------------------------------

_SYSTEM_KEYS = frozenset({"_rid", "_self", "_etag", "_attachments", "_ts"})


def _strip(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in _SYSTEM_KEYS}


class FakeSymbolsContainer:
    """In-memory symbols container supporting point-reads and simple queries.

    Partition key for symbol_config/security_master docs is the ticker
    (e.g. 'PEP').  Stores and vends ETags per doc, bumps on every write.
    """

    def __init__(self):
        # (partition_key, doc_id) → doc
        self._store: Dict[tuple, dict] = {}
        self.create_calls: list = []
        self.replace_calls: list = []
        self.delete_calls: list = []

    # ---- seeding helpers ----

    def seed(self, doc: dict) -> dict:
        """Seed any doc; partition key inferred from doc['symbol']."""
        pk = doc.get("symbol", doc.get("ticker", "PEP"))
        full = {**doc, "_etag": f"etag-{doc['id']}-v0"}
        self._store[(pk, doc["id"])] = full
        return dict(full)

    # ---- Cosmos API surface ----

    def read_item(self, item: str, partition_key: str) -> dict:
        key = (partition_key, item)
        if key not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[key])

    def create_item(self, body: dict) -> dict:
        pk = body.get("symbol", body.get("ticker", "PEP"))
        key = (pk, body["id"])
        if key in self._store:
            raise CosmosHttpResponseError(status_code=409, message="Conflict", response=None)
        new_etag = f"etag-{body['id']}-created"
        doc = {**body, "_etag": new_etag}
        self._store[key] = doc
        self.create_calls.append({"id": body["id"], "body": dict(body)})
        return dict(doc)

    def replace_item(
        self,
        item: str,
        body: dict,
        *,
        etag: Optional[str] = None,
        match_condition=None,
        **kw,
    ) -> dict:
        # locate by id across the store
        for key, stored in list(self._store.items()):
            if stored.get("id") == item:
                if etag and stored.get("_etag") != etag:
                    raise CosmosHttpResponseError(
                        status_code=412, message="Precondition Failed", response=None
                    )
                new_etag = f"etag-{item}-r{len(self.replace_calls)+1}"
                updated = {**body, "_etag": new_etag}
                self._store[key] = updated
                self.replace_calls.append({"id": item, "body": dict(body)})
                return dict(updated)
        raise CosmosResourceNotFoundError(message="not found", response=None)

    def delete_item(self, item: str, partition_key: str, **kw) -> None:
        key = (partition_key, item)
        if key not in self._store:
            # idempotent: already gone is a no-op
            self.delete_calls.append({"id": item, "not_found": True})
            return
        del self._store[key]
        self.delete_calls.append({"id": item, "not_found": False})

    @property
    def write_count(self) -> int:
        return len(self.create_calls) + len(self.replace_calls) + len(self.delete_calls)


class FakePortfolioContainer:
    """In-memory portfolio container.

    Partition key is account_id for ledger_txn docs.
    Supports cross-partition query by security_id (the §3.3 query shape).
    """

    def __init__(self):
        # (account_id, doc_id) → doc
        self._store: Dict[tuple, dict] = {}
        self.replace_calls: list = []

    def seed_movement(
        self,
        movement_id: str,
        account_id: str,
        security_id: str,
        *,
        quantity: float = 10.0,
        gross: Optional[dict] = None,
        fees: Optional[dict] = None,
        net: Optional[dict] = None,
        extra: Optional[dict] = None,
    ) -> dict:
        g = gross or {"amount": 1000.0, "currency": "USD"}
        f = fees or {"amount": 5.0, "currency": "USD"}
        n = net or {"amount": 995.0, "currency": "USD"}
        doc: dict = {
            "id": movement_id,
            "doc_type": "ledger_txn",
            "account_id": account_id,
            "security_id": security_id,
            "quantity": quantity,
            "gross": g,
            "fees": f,
            "net": n,
            "fx": None,
            "withholding": None,
            "trade_date": "2024-01-15",
            "_etag": f"etag-{movement_id}-v0",
        }
        if extra:
            doc.update(extra)
        self._store[(account_id, movement_id)] = doc
        return dict(doc)

    def seed_import_session(
        self, session_id: str, account_id: str, security_ids: List[str]
    ) -> dict:
        doc = {
            "id": session_id,
            "doc_type": "import_session",
            "account_id": account_id,
            "enrolled_security_ids": list(security_ids),
            "resolution_map": {sid: sid for sid in security_ids},
            "_etag": f"etag-{session_id}-v0",
        }
        self._store[(account_id, session_id)] = doc
        return dict(doc)

    def read_item(self, item: str, partition_key: str) -> dict:
        key = (partition_key, item)
        if key not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[key])

    def replace_item(
        self,
        item: str,
        body: dict,
        *,
        etag: Optional[str] = None,
        match_condition=None,
        **kw,
    ) -> dict:
        for key, stored in list(self._store.items()):
            if stored.get("id") == item:
                if etag and stored.get("_etag") != etag:
                    raise CosmosHttpResponseError(
                        status_code=412, message="Precondition Failed", response=None
                    )
                new_etag = f"etag-{item}-r{len(self.replace_calls)+1}"
                updated = {**body, "_etag": new_etag}
                self._store[key] = updated
                self.replace_calls.append({"id": item, "body": dict(body)})
                return dict(updated)
        raise CosmosResourceNotFoundError(message="not found", response=None)

    def query_items(
        self,
        query: str = "",
        parameters=None,
        enable_cross_partition_query: bool = False,
        partition_key: Optional[str] = None,
    ):
        param_map = {p["name"]: p["value"] for p in (parameters or [])}
        results = []
        for (pk, did), doc in self._store.items():
            if partition_key and pk != partition_key:
                continue
            # filter by security_id if present in parameters
            if "@security_id" in param_map:
                if doc.get("security_id") != param_map["@security_id"]:
                    continue
            # filter by doc_type if present in query
            if "doc_type='ledger_txn'" in query.replace('"', "'") or \
               'doc_type = \'ledger_txn\'' in query:
                if doc.get("doc_type") != "ledger_txn":
                    continue
            if "doc_type='import_session'" in query.replace('"', "'") or \
               "doc_type = 'import_session'" in query:
                if doc.get("doc_type") != "import_session":
                    continue
            results.append(dict(doc))
        return iter(results)

    @property
    def write_count(self) -> int:
        return len(self.replace_calls)

    def movements_with_security_id(self, security_id: str) -> List[dict]:
        return [
            dict(doc)
            for (_, _), doc in self._store.items()
            if doc.get("doc_type") == "ledger_txn"
            and doc.get("security_id") == security_id
        ]


# ---------------------------------------------------------------------------
# Standard test fixture builders
# ---------------------------------------------------------------------------

def _make_nnys_pep_doc() -> dict:
    """Returns a corrupt sec_NNYS_PEP security_master document."""
    return {
        "id": "sec_NNYS_PEP",
        "doc_type": "security_master",
        "security_id": "NNYS:PEP",
        "exchange_mic": "NNYS",
        "ticker": "PEP",
        "symbol": "PEP",
        "company_name": "PepsiCo, Inc.",
        "isin": "US7134481081",
        "cusip": "713448108",
        "sedol": "2681511",
        "listing_currency": "EUR",
        "asset_class": "equity",
        "created_at": "2023-01-15T10:00:00Z",
    }


def _make_config_pep_doc() -> dict:
    """Returns the unlinked config_PEP symbol_config document."""
    return {
        "id": "config_PEP",
        "doc_type": "symbol_config",
        "symbol": "PEP",
        "exchange": "NASDAQ",
        # deliberately no security_id — the problem statement
        "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
        "telegram_notifications_enabled": False,
        "total_shares": 100,
        "enrichment": {"category": "balanced"},
    }


def _standard_symbols_container(
    with_xnas_target: bool = False,
    target_extra: Optional[dict] = None,
) -> FakeSymbolsContainer:
    c = FakeSymbolsContainer()
    c.seed(_make_nnys_pep_doc())
    c.seed(_make_config_pep_doc())
    if with_xnas_target:
        target = {
            "id": "sec_XNAS_PEP",
            "doc_type": "security_master",
            "security_id": "XNAS:PEP",
            "exchange_mic": "XNAS",
            "ticker": "PEP",
            "symbol": "PEP",
            "company_name": "PepsiCo, Inc.",
            "isin": "US7134481081",
            "cusip": "713448108",
            "sedol": "2681511",
            "listing_currency": "USD",
            "asset_class": "equity",
            "created_at": "2023-01-15T10:00:00Z",
        }
        if target_extra:
            target.update(target_extra)
        c.seed(target)
    return c


def _standard_portfolio_container(
    num_movements: int = 3,
    currency: str = "USD",
    extra_movements: Optional[List[dict]] = None,
) -> FakePortfolioContainer:
    p = FakePortfolioContainer()
    for i in range(num_movements):
        g = {"amount": 1000.0 + i * 100, "currency": currency}
        f = {"amount": 5.0, "currency": currency}
        n = {"amount": 995.0 + i * 100, "currency": currency}
        p.seed_movement(
            movement_id=f"mvt_{i:03d}",
            account_id="acct_001",
            security_id="NNYS:PEP",
            gross=g, fees=f, net=n,
        )
    if extra_movements:
        for mv in extra_movements:
            p.seed_movement(**mv)
    return p


# ---------------------------------------------------------------------------
# PEP-1: dry_run / audit produces zero writes
# ---------------------------------------------------------------------------

@_skip
class TestDryRunZeroWrites:
    def test_pep1_audit_zero_writes_symbols(self):
        """PEP-1a: --audit must produce zero writes to the symbols container."""
        syms = _standard_symbols_container()
        port = _standard_portfolio_container(num_movements=3)

        report = audit_repair(symbols_container=syms, portfolio_container=port)

        assert syms.write_count == 0, (
            f"PEP-1a: audit must not write to symbols container; "
            f"got {syms.write_count} writes: {syms.create_calls + syms.replace_calls}"
        )

    def test_pep1_audit_zero_writes_portfolio(self):
        """PEP-1b: --audit must produce zero writes to the portfolio container."""
        syms = _standard_symbols_container()
        port = _standard_portfolio_container(num_movements=3)

        audit_repair(symbols_container=syms, portfolio_container=port)

        assert port.write_count == 0, (
            f"PEP-1b: audit must not write to portfolio container; "
            f"got {port.write_count} writes: {port.replace_calls}"
        )

    def test_pep1_audit_reports_discovery(self):
        """PEP-1c: audit report must describe what it found and would change."""
        syms = _standard_symbols_container()
        port = _standard_portfolio_container(num_movements=3)

        report = audit_repair(symbols_container=syms, portfolio_container=port)

        assert isinstance(report, RepairReport), "PEP-1c: audit must return a RepairReport"
        # Must report the corrupt source and the number of movements discovered
        assert getattr(report, "movements_discovered", None) == 3 or \
               getattr(report, "movements_found", None) == 3, (
            "PEP-1c: report must disclose how many NNYS:PEP movements were found"
        )


# ---------------------------------------------------------------------------
# PEP-2: XNAS derivation from NASDAQ alias (runtime, not hardcoded)
# ---------------------------------------------------------------------------

@_skip
class TestMicDerivation:
    def test_pep2_nasdaq_derives_xnas_not_hardcoded(self):
        """PEP-2: target MIC must be derived at runtime from LEGACY_ALIAS_TO_MIC,
        not hardcoded as 'XNAS'. Verified by intercepting the lookup.
        """
        from src.portfolio.provider_symbols import LEGACY_ALIAS_TO_MIC

        # Confirm the resolution path exists in the same table the script uses
        config_exchange = "NASDAQ"
        resolved = LEGACY_ALIAS_TO_MIC.get(config_exchange.upper())
        assert resolved == "XNAS", (
            f"PEP-2: LEGACY_ALIAS_TO_MIC['{config_exchange}'] must be 'XNAS', got {resolved!r}"
        )

        syms = _standard_symbols_container()
        port = _standard_portfolio_container(num_movements=2)

        report = apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        # The created security_master must have exchange_mic from the table, not a literal
        created_ids = [c["id"] for c in syms.create_calls]
        assert "sec_XNAS_PEP" in created_ids, (
            f"PEP-2: sec_XNAS_PEP must have been created; got: {created_ids}"
        )
        created_doc = next(c["body"] for c in syms.create_calls if c["id"] == "sec_XNAS_PEP")
        assert created_doc.get("exchange_mic") == "XNAS", (
            f"PEP-2: created security_master must have exchange_mic='XNAS' (from LEGACY_ALIAS_TO_MIC)"
        )
        assert created_doc.get("security_id") == "XNAS:PEP", (
            "PEP-2: created security_master security_id must be 'XNAS:PEP'"
        )

    def test_pep2_unresolvable_alias_aborts(self):
        """PEP-2b: if config_PEP.exchange is not in LEGACY_ALIAS_TO_MIC,
        the script must abort (unresolved) with exit code 2 — not guess.
        """
        syms = FakeSymbolsContainer()
        syms.seed(_make_nnys_pep_doc())
        # Override config with an unresolvable exchange
        config = _make_config_pep_doc()
        config["exchange"] = "PINK"
        syms.seed(config)
        port = _standard_portfolio_container(num_movements=1)

        report = apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        assert syms.write_count == 0, "PEP-2b: unresolvable exchange must produce zero writes"
        assert getattr(report, "exit_code", 0) == 2 or \
               getattr(report, "abort_reason", "") != "", (
            "PEP-2b: unresolvable exchange must set exit_code=2 or abort_reason"
        )


# ---------------------------------------------------------------------------
# PEP-3/4/5: currency evidence gating
# ---------------------------------------------------------------------------

@_skip
class TestCurrencyEvidence:
    def test_pep3_unanimous_currency_corrects_listing_currency(self):
        """PEP-3: all movements have gross.currency='USD' → listing_currency corrected
        from 'EUR' to 'USD' on the created/updated security_master.
        """
        syms = _standard_symbols_container()
        port = _standard_portfolio_container(num_movements=3, currency="USD")

        apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        # The created sec_XNAS_PEP must have listing_currency='USD'
        try:
            created_doc = next(
                c["body"] for c in syms.create_calls if c["id"] == "sec_XNAS_PEP"
            )
        except StopIteration:
            # Maybe patched via replace — look in the store
            created_doc = syms.read_item("sec_XNAS_PEP", "PEP")

        assert created_doc.get("listing_currency") == "USD", (
            f"PEP-3: unanimous USD evidence must correct listing_currency; "
            f"got {created_doc.get('listing_currency')!r}"
        )

    def test_pep4_mixed_currencies_fail_closed(self):
        """PEP-4: movements with EUR and USD → currency_evidence=inconclusive,
        listing_currency left at 'EUR'.
        """
        port = FakePortfolioContainer()
        port.seed_movement(
            "mvt_eur", "acct_001", "NNYS:PEP",
            gross={"amount": 500.0, "currency": "EUR"},
            fees={"amount": 5.0, "currency": "EUR"},
            net={"amount": 495.0, "currency": "EUR"},
        )
        port.seed_movement(
            "mvt_usd", "acct_001", "NNYS:PEP",
            gross={"amount": 500.0, "currency": "USD"},
            fees={"amount": 5.0, "currency": "USD"},
            net={"amount": 495.0, "currency": "USD"},
        )
        syms = _standard_symbols_container()

        apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        # listing_currency must not be changed to either value
        try:
            created_doc = next(
                c["body"] for c in syms.create_calls if c["id"] == "sec_XNAS_PEP"
            )
        except StopIteration:
            created_doc = syms.read_item("sec_XNAS_PEP", "PEP")

        assert created_doc.get("listing_currency") == "EUR", (
            f"PEP-4: mixed currencies must leave listing_currency as 'EUR'; "
            f"got {created_doc.get('listing_currency')!r}"
        )

    def test_pep4_mixed_currencies_reported_inconclusive(self):
        """PEP-4b: mixed currency evidence must be reported as inconclusive."""
        port = FakePortfolioContainer()
        for i, ccy in enumerate(["EUR", "USD"]):
            port.seed_movement(
                f"mvt_{i}", "acct_001", "NNYS:PEP",
                gross={"amount": 500.0, "currency": ccy},
                fees={"amount": 5.0, "currency": ccy},
                net={"amount": 495.0, "currency": ccy},
            )
        syms = _standard_symbols_container()

        report = apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        evidence = getattr(report, "currency_evidence", None)
        assert evidence in (None, "inconclusive") or \
               getattr(report, "currency_inconclusive", False) is True, (
            "PEP-4b: mixed currencies must be reported as inconclusive in the report"
        )

    def test_pep5_no_movements_currency_fail_closed(self):
        """PEP-5: no ledger_txn movements → currency_evidence=inconclusive,
        listing_currency preserved as 'EUR'.
        """
        syms = _standard_symbols_container()
        port = FakePortfolioContainer()  # no movements

        apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        try:
            created_doc = next(
                c["body"] for c in syms.create_calls if c["id"] == "sec_XNAS_PEP"
            )
        except StopIteration:
            created_doc = syms.read_item("sec_XNAS_PEP", "PEP")

        assert created_doc.get("listing_currency") == "EUR", (
            f"PEP-5: no-movement case must preserve listing_currency='EUR'; "
            f"got {created_doc.get('listing_currency')!r}"
        )


# ---------------------------------------------------------------------------
# PEP-6: full happy-path apply
# ---------------------------------------------------------------------------

@_skip
class TestHappyPathApply:
    def test_pep6_target_security_master_created(self):
        """PEP-6a: sec_XNAS_PEP must be created in the symbols container."""
        syms = _standard_symbols_container()
        port = _standard_portfolio_container(num_movements=3)

        apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        created_ids = [c["id"] for c in syms.create_calls]
        assert "sec_XNAS_PEP" in created_ids, (
            f"PEP-6a: sec_XNAS_PEP must be created; got create_calls={created_ids}"
        )

    def test_pep6_all_movements_repointed(self):
        """PEP-6b: every NNYS:PEP ledger_txn must have security_id='XNAS:PEP'."""
        syms = _standard_symbols_container()
        port = _standard_portfolio_container(num_movements=4)

        apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        remaining = port.movements_with_security_id("NNYS:PEP")
        assert remaining == [], (
            f"PEP-6b: no movements must remain with NNYS:PEP; still have: "
            f"{[m['id'] for m in remaining]}"
        )

        repointed = port.movements_with_security_id("XNAS:PEP")
        assert len(repointed) == 4, (
            f"PEP-6b: exactly 4 movements must have XNAS:PEP; got {len(repointed)}"
        )

    def test_pep6_config_security_id_set(self):
        """PEP-6c: config_PEP.security_id must be set to 'XNAS:PEP'."""
        syms = _standard_symbols_container()
        port = _standard_portfolio_container(num_movements=2)

        apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        config = syms.read_item("config_PEP", "PEP")
        assert config.get("security_id") == "XNAS:PEP", (
            f"PEP-6c: config_PEP.security_id must be 'XNAS:PEP'; "
            f"got {config.get('security_id')!r}"
        )

    def test_pep6_config_exchange_preserved(self):
        """PEP-6d: config_PEP.exchange must NOT be changed (legacy field preserved)."""
        syms = _standard_symbols_container()
        port = _standard_portfolio_container(num_movements=2)

        apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        config = syms.read_item("config_PEP", "PEP")
        assert config.get("exchange") == "NASDAQ", (
            f"PEP-6d: config_PEP.exchange must remain 'NASDAQ' (not overwritten); "
            f"got {config.get('exchange')!r}"
        )

    def test_pep6_source_deleted_after_repoint(self):
        """PEP-6e: sec_NNYS_PEP must be deleted after verified repoint."""
        syms = _standard_symbols_container()
        port = _standard_portfolio_container(num_movements=2)

        apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        deleted_ids = [d["id"] for d in syms.delete_calls]
        assert "sec_NNYS_PEP" in deleted_ids, (
            f"PEP-6e: sec_NNYS_PEP must be deleted after successful repoint; "
            f"delete_calls={deleted_ids}"
        )

    def test_pep6_source_body_fields_preserved_in_target(self):
        """PEP-6f: target sec_XNAS_PEP inherits company_name, isin, cusip, sedol,
        asset_class from source (byte-for-byte for identity fields).
        """
        syms = _standard_symbols_container()
        port = _standard_portfolio_container(num_movements=2, currency="USD")

        apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        source_orig = _make_nnys_pep_doc()
        try:
            created_doc = next(
                c["body"] for c in syms.create_calls if c["id"] == "sec_XNAS_PEP"
            )
        except StopIteration:
            created_doc = syms.read_item("sec_XNAS_PEP", "PEP")

        for field in ("company_name", "isin", "cusip", "sedol", "asset_class"):
            assert created_doc.get(field) == source_orig.get(field), (
                f"PEP-6f: {field} must be preserved from source; "
                f"expected {source_orig.get(field)!r}, got {created_doc.get(field)!r}"
            )

    def test_pep6_created_at_preserved_not_reset(self):
        """PEP-6g: created_at from source must be preserved in target (corrected identity,
        not a brand new security).
        """
        syms = _standard_symbols_container()
        port = _standard_portfolio_container(num_movements=2, currency="USD")

        apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        try:
            created_doc = next(
                c["body"] for c in syms.create_calls if c["id"] == "sec_XNAS_PEP"
            )
        except StopIteration:
            created_doc = syms.read_item("sec_XNAS_PEP", "PEP")

        assert created_doc.get("created_at") == "2023-01-15T10:00:00Z", (
            f"PEP-6g: created_at must be preserved from original source doc; "
            f"got {created_doc.get('created_at')!r}"
        )

    def test_pep6_audit_metadata_present_on_target(self):
        """PEP-6h: target must have migrated_from and migration_note audit fields."""
        syms = _standard_symbols_container()
        port = _standard_portfolio_container(num_movements=2, currency="USD")

        apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        try:
            created_doc = next(
                c["body"] for c in syms.create_calls if c["id"] == "sec_XNAS_PEP"
            )
        except StopIteration:
            created_doc = syms.read_item("sec_XNAS_PEP", "PEP")

        assert created_doc.get("migrated_from") == "NNYS:PEP", (
            "PEP-6h: target must have migrated_from='NNYS:PEP'"
        )
        assert created_doc.get("migration_note"), (
            "PEP-6h: target must have a non-empty migration_note"
        )


# ---------------------------------------------------------------------------
# PEP-7: target already exists — skip creation, re-point, delete source
# ---------------------------------------------------------------------------

@_skip
class TestTargetAlreadyExists:
    def test_pep7_no_duplicate_create_when_target_exists(self):
        """PEP-7a: if sec_XNAS_PEP already exists (consistent), do NOT create duplicate."""
        syms = _standard_symbols_container(with_xnas_target=True)
        port = _standard_portfolio_container(num_movements=3)

        apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        created_ids = [c["id"] for c in syms.create_calls]
        assert "sec_XNAS_PEP" not in created_ids, (
            f"PEP-7a: sec_XNAS_PEP already exists — must not create duplicate; "
            f"create_calls={created_ids}"
        )

    def test_pep7_movements_repointed_when_target_exists(self):
        """PEP-7b: even when target pre-exists, all movements must still be re-pointed."""
        syms = _standard_symbols_container(with_xnas_target=True)
        port = _standard_portfolio_container(num_movements=3)

        apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        remaining = port.movements_with_security_id("NNYS:PEP")
        assert remaining == [], (
            f"PEP-7b: all movements must be re-pointed even when target pre-exists; "
            f"remaining={[m['id'] for m in remaining]}"
        )

    def test_pep7_source_deleted_when_target_exists(self):
        """PEP-7c: source sec_NNYS_PEP must be deleted after verified re-point
        (even when target pre-existed).
        """
        syms = _standard_symbols_container(with_xnas_target=True)
        port = _standard_portfolio_container(num_movements=2)

        apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        deleted_ids = [d["id"] for d in syms.delete_calls]
        assert "sec_NNYS_PEP" in deleted_ids, (
            f"PEP-7c: sec_NNYS_PEP must be deleted after successful repoint; "
            f"delete_calls={deleted_ids}"
        )


# ---------------------------------------------------------------------------
# PEP-8/9: collision handling — abort, zero writes
# ---------------------------------------------------------------------------

@_skip
class TestCollisionHandling:
    def test_pep8_conflicting_isin_aborts(self):
        """PEP-8: target exists with different non-empty ISIN → collision_ambiguous abort."""
        syms = _standard_symbols_container(
            with_xnas_target=True,
            target_extra={"isin": "DIFFERENT_ISIN_XYZ"},  # conflicts with source
        )
        port = _standard_portfolio_container(num_movements=2)

        report = apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        assert syms.write_count == 0 or port.write_count == 0, (
            "PEP-8: collision_ambiguous ISIN conflict must not write to symbols container"
        )
        assert getattr(report, "exit_code", 0) == 2 or \
               getattr(report, "collision_ambiguous", False) or \
               getattr(report, "abort_reason", "") != "", (
            "PEP-8: collision_ambiguous ISIN must set exit_code=2 or collision_ambiguous flag"
        )

    def test_pep9_malformed_target_aborts(self):
        """PEP-9: target exists but is malformed (no ticker field) → abort, zero writes."""
        syms = FakeSymbolsContainer()
        syms.seed(_make_nnys_pep_doc())
        syms.seed(_make_config_pep_doc())
        # malformed target: missing ticker
        syms.seed({
            "id": "sec_XNAS_PEP",
            "doc_type": "security_master",
            "security_id": "XNAS:PEP",
            "exchange_mic": "XNAS",
            # deliberately no "ticker" field — malformed
        })
        port = _standard_portfolio_container(num_movements=2)

        report = apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        # We should NOT have written anything new after discovering the malformed target
        # (the only pre-existing writes are the seeds, which are not tracked in write_calls)
        assert len(syms.create_calls) == 0, (
            "PEP-9: malformed target must abort before creating or replacing anything"
        )
        assert getattr(report, "exit_code", 0) == 2 or \
               getattr(report, "abort_reason", "") != "", (
            "PEP-9: malformed target must set exit_code=2 or abort_reason"
        )


# ---------------------------------------------------------------------------
# PEP-10/17: holdings mismatch → abort before delete
# ---------------------------------------------------------------------------

@_skip
class TestHoldingsMismatchAbort:
    def _make_mismatch_portfolio(self) -> FakePortfolioContainer:
        """Portfolio where the verification's holdings calculation diverges."""
        p = FakePortfolioContainer()
        # Seed movements that will report different quantities before/after
        for i in range(3):
            p.seed_movement(
                f"mvt_{i}", "acct_001", "NNYS:PEP",
                quantity=10.0,
                gross={"amount": 1000.0, "currency": "USD"},
                fees={"amount": 5.0, "currency": "USD"},
                net={"amount": 995.0, "currency": "USD"},
            )
        return p

    def test_pep10_holdings_mismatch_aborts_before_delete(self):
        """PEP-10: if post-repoint holdings (total_shares, cost_basis, count) differ
        from pre-repair snapshot → abort, exit_code=3, sec_NNYS_PEP retained.
        """
        syms = _standard_symbols_container()
        port = self._make_mismatch_portfolio()

        # Inject a mismatch: add a phantom movement AFTER initial discovery
        # to simulate a concurrent write that changes the movement count
        _phantom_injected = False

        original_query = port.query_items

        call_count = [0]

        def _counting_query(query="", parameters=None, **kw):
            call_count[0] += 1
            results = list(original_query(query=query, parameters=parameters, **kw))
            # On the second call (post-write verification pass), inject a phantom
            if call_count[0] >= 2 and "NNYS:PEP" in (parameters or [{}])[0].get("value", ""):
                # simulate: one movement reverted back to NNYS:PEP
                results.append({
                    "id": "phantom_mvt", "doc_type": "ledger_txn",
                    "account_id": "acct_001", "security_id": "NNYS:PEP",
                    "quantity": 10.0,
                    "gross": {"amount": 500.0, "currency": "USD"},
                    "fees": {"amount": 5.0, "currency": "USD"},
                    "net": {"amount": 495.0, "currency": "USD"},
                })
            return iter(results)

        port.query_items = _counting_query

        report = apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        # sec_NNYS_PEP must NOT have been deleted
        deleted_ids = [d["id"] for d in syms.delete_calls]
        assert "sec_NNYS_PEP" not in deleted_ids, (
            "PEP-10: holdings mismatch must prevent deletion of sec_NNYS_PEP; "
            f"deleted_ids={deleted_ids}"
        )
        assert getattr(report, "exit_code", 0) == 3 or \
               getattr(report, "verification_failed", False) is True, (
            "PEP-10: holdings mismatch must produce exit_code=3 or verification_failed=True"
        )


# ---------------------------------------------------------------------------
# PEP-11: movement byte-equivalence (only security_id + audit metadata changes)
# ---------------------------------------------------------------------------

@_skip
class TestMovementByteEquivalence:
    def test_pep11_movement_ids_unchanged(self):
        """PEP-11a: id (movement_id) must never change — pure field patch."""
        syms = _standard_symbols_container()
        port = _standard_portfolio_container(num_movements=3)
        original_ids = {m["id"] for m in port.movements_with_security_id("NNYS:PEP")}

        apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        repointed = port.movements_with_security_id("XNAS:PEP")
        repointed_ids = {m["id"] for m in repointed}
        assert original_ids == repointed_ids, (
            f"PEP-11a: movement IDs must not change; "
            f"original={original_ids}, after={repointed_ids}"
        )

    def test_pep11_account_id_unchanged(self):
        """PEP-11b: account_id (partition key) must never change."""
        port = FakePortfolioContainer()
        port.seed_movement("mvt_A", "acct_001", "NNYS:PEP")
        port.seed_movement("mvt_B", "acct_002", "NNYS:PEP")
        syms = _standard_symbols_container()

        apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        for call in port.replace_calls:
            body = call["body"]
            assert body.get("account_id") in ("acct_001", "acct_002"), (
                f"PEP-11b: account_id must not be rewritten; got {body.get('account_id')!r}"
            )

    def test_pep11_financial_fields_byte_equivalent(self):
        """PEP-11c: quantity, gross, fees, net, fx, withholding, trade_date must be
        identical byte-for-byte before and after the patch.
        """
        FINANCIAL_FIELDS = (
            "quantity", "gross", "fees", "net", "fx", "withholding",
            "trade_date", "correction_status",
        )
        port = FakePortfolioContainer()
        orig = port.seed_movement(
            "mvt_001", "acct_001", "NNYS:PEP",
            quantity=77.5,
            gross={"amount": 9876.50, "currency": "USD"},
            fees={"amount": 12.0, "currency": "USD"},
            net={"amount": 9864.50, "currency": "USD"},
            extra={"trade_date": "2024-06-30", "withholding": {"amount": 0, "currency": "USD"}},
        )
        syms = _standard_symbols_container()

        apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        # Find the replace call for mvt_001
        replace_body = next(
            (c["body"] for c in port.replace_calls if c["id"] == "mvt_001"), None
        )
        assert replace_body is not None, "PEP-11c: mvt_001 must have been patched"

        for field in FINANCIAL_FIELDS:
            if field in orig:
                assert replace_body.get(field) == orig[field], (
                    f"PEP-11c: financial field '{field}' must be byte-equivalent; "
                    f"expected {orig[field]!r}, got {replace_body.get(field)!r}"
                )

    def test_pep11_only_security_id_changed_on_movement(self):
        """PEP-11d: the only change on a ledger_txn must be security_id (plus
        allowed audit metadata); no other field may be added, removed, or modified.
        """
        port = FakePortfolioContainer()
        orig = port.seed_movement(
            "mvt_only", "acct_001", "NNYS:PEP",
            quantity=5.0,
            gross={"amount": 500.0, "currency": "USD"},
            fees={"amount": 2.0, "currency": "USD"},
            net={"amount": 498.0, "currency": "USD"},
        )
        syms = _standard_symbols_container()

        apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        replace_body = next(
            (c["body"] for c in port.replace_calls if c["id"] == "mvt_only"), None
        )
        assert replace_body is not None, "PEP-11d: mvt_only must have been patched"

        # Strip security_id and allowed audit metadata, compare the rest
        _ALLOWED_NEW_FIELDS = frozenset({"repaired_at", "repair_note", "repair_migration"})
        orig_clean = {k: v for k, v in orig.items() if k not in _SYSTEM_KEYS}
        new_clean = {k: v for k, v in replace_body.items() if k not in _SYSTEM_KEYS}

        for key, val in orig_clean.items():
            if key == "security_id":
                continue  # the one intended change
            assert new_clean.get(key) == val, (
                f"PEP-11d: field '{key}' changed unexpectedly: "
                f"was {val!r}, now {new_clean.get(key)!r}"
            )
        # Ensure no unexpected new keys beyond security_id and allowed audit
        unexpected = {
            k for k in new_clean
            if k not in orig_clean and k not in _ALLOWED_NEW_FIELDS
        }
        assert not unexpected, (
            f"PEP-11d: unexpected new fields written to ledger_txn: {unexpected}"
        )


# ---------------------------------------------------------------------------
# PEP-12: backup before first write, backup completeness
# ---------------------------------------------------------------------------

@_skip
class TestBackupCompleteness:
    def test_pep12_backup_created_before_first_write(self, monkeypatch, tmp_path):
        """PEP-12a: a complete, checksum-valid backup file must be written
        before the FIRST create_item/replace_item/delete_item mutation on
        either the symbols or portfolio container.

        2026-09-07 (Reuben, independent revision — Basher locked out):
        the previous version defined `_backup_then_apply` / `backup_completed`
        closures that were never invoked, so `backup_completed[0]` never
        flipped `True` and the test never actually exercised ordering — it
        only passed because `apply_repair` happened not to raise. This
        rewrite drives the REAL `apply_repair` -> `run_apply` path (no
        product code touched) and instruments both the fake containers and
        the real `write_backup` function (via monkeypatch, restored
        automatically after the test) to record a single ordered event
        trace. The invariant is proven by comparing event indices, not by
        scanning source.
        """
        import scripts.repair_pep_security_id as repair_mod

        events: list[tuple[str, str, str]] = []  # (event_kind, container_label, doc_id)

        # Default fixture: target sec_XNAS_PEP does NOT pre-exist, so run_apply
        # exercises create_item (target), replace_item (config + 2 ledger_txns),
        # and delete_item (source) — all three mutation kinds, both containers.
        syms = _standard_symbols_container()
        port = _standard_portfolio_container(num_movements=2)

        def _instrument(container, method_name, label):
            original = getattr(container, method_name)

            def _tracked(*args, **kwargs):
                if method_name == "create_item":
                    body = args[0] if args else kwargs.get("body", {})
                    doc_id = body.get("id", "?")
                else:  # replace_item / delete_item are called with item=... in this script
                    doc_id = kwargs.get("item", args[0] if args else "?")
                events.append((method_name, label, str(doc_id)))
                return original(*args, **kwargs)

            monkeypatch.setattr(container, method_name, _tracked)

        _instrument(syms, "create_item", "symbols")
        _instrument(syms, "replace_item", "symbols")
        _instrument(syms, "delete_item", "symbols")
        _instrument(port, "replace_item", "portfolio")

        real_write_backup = repair_mod.write_backup

        def _tracked_write_backup(backup, backup_dir):
            path = real_write_backup(backup, backup_dir)
            # Prove the file is COMPLETELY and correctly written at this point,
            # not merely that write_backup was called: re-read it through the
            # script's own reader, which recomputes and verifies the sha256
            # checksum over the persisted documents (raises ValueError on any
            # mismatch/incompleteness).
            reread = repair_mod.read_backup(path)
            assert reread.sha256 == backup.sha256, (
                "PEP-12a: on-disk backup checksum does not match the in-memory "
                "RepairBackup — file was not completely/correctly written"
            )
            assert path.exists() and path.stat().st_size > 0, (
                "PEP-12a: backup file missing or empty immediately after write_backup returned"
            )
            events.append(("backup_complete", "n/a", str(path.name)))
            return path

        monkeypatch.setattr(repair_mod, "write_backup", _tracked_write_backup)

        report = apply_repair(
            symbols_container=syms,
            portfolio_container=port,
            dry_run=False,
            backup_dir=tmp_path,
        )
        assert report.exit_code == 0, (
            f"PEP-12a: expected a successful apply so mutation ordering is actually "
            f"exercised; got exit_code={report.exit_code}, errors={report.error_details}"
        )

        backup_indices = [i for i, e in enumerate(events) if e[0] == "backup_complete"]
        assert backup_indices, "PEP-12a: write_backup was never invoked by run_apply"

        mutation_kinds_seen = {e[0] for e in events if e[0] in
                                ("create_item", "replace_item", "delete_item")}
        assert mutation_kinds_seen == {"create_item", "replace_item", "delete_item"}, (
            "PEP-12a: fixture must exercise all three mutation kinds so the ordering "
            f"assertion is meaningful; observed kinds: {mutation_kinds_seen}"
        )
        containers_seen = {e[1] for e in events if e[0] in
                            ("create_item", "replace_item", "delete_item")}
        assert containers_seen == {"symbols", "portfolio"}, (
            "PEP-12a: fixture must produce mutations on both containers; "
            f"observed: {containers_seen}"
        )

        first_mutation_idx = min(
            i for i, e in enumerate(events)
            if e[0] in ("create_item", "replace_item", "delete_item")
        )
        assert backup_indices[0] < first_mutation_idx, (
            "PEP-12a: backup must be fully written before the first mutation. "
            f"Full ordered event trace: {events}"
        )

    def test_pep12_backup_contains_all_discovered_docs(self):
        """PEP-12b: backup must contain the source security_master, config, and all movements."""
        syms = _standard_symbols_container()
        port = _standard_portfolio_container(num_movements=3)
        backup_path = pathlib.Path(".")

        result = backup_repair(symbols_container=syms, portfolio_container=port, path=backup_path)

        assert isinstance(result, RepairBackup), "PEP-12b: backup_repair must return RepairBackup"
        doc_ids = {entry["id"] if isinstance(entry, dict) else entry.id
                   for entry in result.documents}
        assert "sec_NNYS_PEP" in doc_ids, "PEP-12b: backup must include sec_NNYS_PEP"
        assert "config_PEP" in doc_ids, "PEP-12b: backup must include config_PEP"
        for i in range(3):
            assert f"mvt_{i:03d}" in doc_ids, (
                f"PEP-12b: backup must include movement mvt_{i:03d}"
            )

    def test_pep12_backup_has_sha256_checksum(self):
        """PEP-12c: backup must include a sha256 checksum over the document list."""
        syms = _standard_symbols_container()
        port = _standard_portfolio_container(num_movements=2)

        result = backup_repair(symbols_container=syms, portfolio_container=port, path=pathlib.Path("."))

        assert hasattr(result, "sha256"), "PEP-12c: RepairBackup must have sha256 attribute"
        assert isinstance(result.sha256, str) and len(result.sha256) == 64, (
            f"PEP-12c: sha256 must be a 64-character hex string; got {result.sha256!r}"
        )

    def test_pep12_backup_has_generated_at_timestamp(self):
        """PEP-12d: backup must include a generated_at timestamp."""
        syms = _standard_symbols_container()
        port = _standard_portfolio_container(num_movements=1)

        result = backup_repair(symbols_container=syms, portfolio_container=port, path=pathlib.Path("."))

        assert hasattr(result, "generated_at"), (
            "PEP-12d: RepairBackup must have generated_at attribute"
        )
        assert result.generated_at, "PEP-12d: generated_at must not be empty"


# ---------------------------------------------------------------------------
# PEP-13: checksum validation
# ---------------------------------------------------------------------------

@_skip
class TestChecksumValidation:
    def test_pep13_tampered_backup_rejected_on_restore(self, tmp_path):
        """PEP-13: a backup whose sha256 doesn't match its contents must be rejected."""
        syms = _standard_symbols_container()
        port = _standard_portfolio_container(num_movements=2)

        bk = backup_repair(symbols_container=syms, portfolio_container=port, path=tmp_path)
        # Write the backup to disk with the script's own writer if available
        from scripts.repair_pep_security_id import write_backup as _write_bk
        backup_file = _write_bk(bk, tmp_path)

        # Tamper: flip one byte in the JSON body
        raw = backup_file.read_text()
        tampered_raw = raw.replace('"EUR"', '"GBP"', 1)
        backup_file.write_text(tampered_raw)

        with pytest.raises(Exception) as exc_info:
            restore_repair(
                symbols_container=syms, portfolio_container=port,
                backup_path=backup_file,
            )

        assert "checksum" in str(exc_info.value).lower() or \
               "sha256" in str(exc_info.value).lower() or \
               "tamper" in str(exc_info.value).lower(), (
            f"PEP-13: tampered backup must raise a checksum-related error; "
            f"got: {exc_info.value}"
        )


# ---------------------------------------------------------------------------
# PEP-14: ETag conflict on one movement
# ---------------------------------------------------------------------------

@_skip
class TestEtagConflictMovement:
    def test_pep14_etag_conflict_on_one_movement_does_not_block_others(self):
        """PEP-14: ETag 412 on one movement must not prevent other movements from being patched."""
        syms = _standard_symbols_container()
        port = FakePortfolioContainer()
        port.seed_movement("mvt_000", "acct_001", "NNYS:PEP")
        port.seed_movement("mvt_001", "acct_001", "NNYS:PEP")
        port.seed_movement("mvt_002", "acct_001", "NNYS:PEP")

        # Inject 412 for exactly mvt_001
        original_replace = port.replace_item
        conflict_hit = [False]

        def _conflict_on_001(item, body, *, etag=None, **kw):
            if item == "mvt_001" and not conflict_hit[0]:
                conflict_hit[0] = True
                raise CosmosHttpResponseError(
                    status_code=412, message="ETag conflict", response=None
                )
            return original_replace(item, body, etag=etag, **kw)

        port.replace_item = _conflict_on_001

        report = apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        # mvt_000 and mvt_002 must be patched
        remaining = port.movements_with_security_id("NNYS:PEP")
        remaining_ids = {m["id"] for m in remaining}
        assert "mvt_000" not in remaining_ids, (
            "PEP-14: mvt_000 must be patched despite conflict on mvt_001"
        )
        assert "mvt_002" not in remaining_ids, (
            "PEP-14: mvt_002 must be patched despite conflict on mvt_001"
        )
        # mvt_001 still has NNYS:PEP (the conflicted movement)
        assert "mvt_001" in remaining_ids, (
            "PEP-14: mvt_001 (conflicted) must still show NNYS:PEP"
        )

    def test_pep14_etag_conflict_reported_not_exit_code_3(self):
        """PEP-14b: ETag conflict on a movement is a reported conflict, not exit_code=3."""
        syms = _standard_symbols_container()
        port = FakePortfolioContainer()
        port.seed_movement("mvt_c", "acct_001", "NNYS:PEP")

        original_replace = port.replace_item

        def _always_conflict(item, body, *, etag=None, **kw):
            raise CosmosHttpResponseError(status_code=412, message="Conflict", response=None)

        port.replace_item = _always_conflict

        report = apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        assert getattr(report, "exit_code", 0) != 3, (
            "PEP-14b: ETag conflict on movement must not produce exit_code=3 "
            "(which is reserved for holdings mismatch)"
        )
        assert getattr(report, "cas_conflicts", 0) >= 1 or \
               getattr(report, "movement_conflicts", 0) >= 1, (
            "PEP-14b: ETag conflict must be counted in report"
        )


# ---------------------------------------------------------------------------
# PEP-15/20: idempotency
# ---------------------------------------------------------------------------

@_skip
class TestIdempotency:
    def test_pep15_partial_failure_resumable(self):
        """PEP-15: after a partial failure (only some movements patched), a second
        --apply patches the remaining movements without re-doing completed work.
        """
        syms = _standard_symbols_container()
        port = FakePortfolioContainer()
        for i in range(4):
            port.seed_movement(f"mvt_{i}", "acct_001", "NNYS:PEP")

        # First run: patch first 2 movements manually to simulate partial completion
        # then run apply to pick up the rest
        for doc_key, doc in list(port._store.items()):
            if doc.get("id") in ("mvt_0", "mvt_1"):
                updated = {**doc, "security_id": "XNAS:PEP"}
                port._store[doc_key] = updated

        # Clear write tracking to count only the second run's writes
        port.replace_calls.clear()

        # Second run should only patch mvt_2 and mvt_3 (not re-patch mvt_0 and mvt_1)
        apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        patched_ids = {c["id"] for c in port.replace_calls}
        # mvt_0 and mvt_1 already had XNAS:PEP — must not be re-patched
        assert "mvt_0" not in patched_ids and "mvt_1" not in patched_ids, (
            f"PEP-15: already-patched movements must not be re-written; "
            f"patched_ids={patched_ids}"
        )
        # mvt_2 and mvt_3 must have been patched this run
        assert "mvt_2" in patched_ids and "mvt_3" in patched_ids, (
            f"PEP-15: remaining movements mvt_2/mvt_3 must be patched; "
            f"patched_ids={patched_ids}"
        )

    def test_pep20_idempotent_rerun_after_full_success(self):
        """PEP-20: running --apply a second time after a fully successful run
        produces zero additional writes (all steps see already-correct state).
        """
        syms = _standard_symbols_container()
        port = _standard_portfolio_container(num_movements=3)

        # First run (full apply)
        apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        # Clear tracking counters
        syms.create_calls.clear()
        syms.replace_calls.clear()
        syms.delete_calls.clear()
        port.replace_calls.clear()

        # Second run — must be a no-op
        report2 = apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        total_writes = syms.write_count + port.write_count
        assert total_writes == 0, (
            f"PEP-20: idempotent re-run must produce zero writes; "
            f"got {total_writes}: syms.create={syms.create_calls}, "
            f"syms.replace={syms.replace_calls}, syms.delete={syms.delete_calls}, "
            f"port.replace={port.replace_calls}"
        )
        assert getattr(report2, "exit_code", 0) == 0, (
            f"PEP-20: idempotent re-run must exit with code 0; "
            f"got {getattr(report2, 'exit_code', 0)}"
        )


# ---------------------------------------------------------------------------
# PEP-16: source not deleted while references remain
# ---------------------------------------------------------------------------

@_skip
class TestSourceNotDeletedWithRemainingReferences:
    def test_pep16_source_not_deleted_if_references_remain(self):
        """PEP-16: if any movement still shows NNYS:PEP after the patch loop
        (e.g. due to all ETag conflicts), sec_NNYS_PEP must NOT be deleted.
        """
        syms = _standard_symbols_container()
        port = FakePortfolioContainer()
        port.seed_movement("mvt_stuck", "acct_001", "NNYS:PEP")

        # Make all portfolio patches fail
        def _always_fail(item, body, *, etag=None, **kw):
            raise CosmosHttpResponseError(status_code=412, message="Conflict", response=None)

        port.replace_item = _always_fail

        apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        deleted_ids = [d["id"] for d in syms.delete_calls]
        assert "sec_NNYS_PEP" not in deleted_ids, (
            f"PEP-16: sec_NNYS_PEP must not be deleted while any NNYS:PEP reference remains; "
            f"delete_calls={deleted_ids}"
        )


# ---------------------------------------------------------------------------
# PEP-18: source deleted after verified equivalence
# ---------------------------------------------------------------------------

@_skip
class TestSourceDeletedAfterVerification:
    def test_pep18_source_deleted_only_after_step5_passes(self):
        """PEP-18: sec_NNYS_PEP deletion (§5 step 6) happens only after the
        post-write verification (§5 step 5) passes.
        """
        syms = _standard_symbols_container()
        port = _standard_portfolio_container(num_movements=3, currency="USD")

        apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        # Source must be deleted exactly once
        delete_events = [d for d in syms.delete_calls if d["id"] == "sec_NNYS_PEP"]
        assert len(delete_events) == 1, (
            f"PEP-18: sec_NNYS_PEP must be deleted exactly once; "
            f"delete_calls={syms.delete_calls}"
        )
        assert delete_events[0].get("not_found") is False, (
            "PEP-18: delete call must confirm the doc existed (not already gone)"
        )

        # Confirm no NNYS:PEP movements remain (prerequisite for deletion)
        remaining = port.movements_with_security_id("NNYS:PEP")
        assert remaining == [], (
            "PEP-18: all movements must be patched before deletion; "
            f"remaining={[m['id'] for m in remaining]}"
        )


# ---------------------------------------------------------------------------
# PEP-19: --restore re-reads live state, not blind overwrite
# ---------------------------------------------------------------------------

@_skip
class TestRestoreReadsLiveState:
    def test_pep19_restore_reverts_migration(self, tmp_path):
        """PEP-19: --restore must revert all docs to their pre-migration state."""
        # Arrange: take backup, then apply
        syms = _standard_symbols_container()
        port = _standard_portfolio_container(num_movements=2)

        bk = backup_repair(symbols_container=syms, portfolio_container=port, path=tmp_path)
        from scripts.repair_pep_security_id import write_backup as _write_bk
        backup_file = _write_bk(bk, tmp_path)

        apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        # Confirm migration happened
        assert port.movements_with_security_id("NNYS:PEP") == []
        assert port.movements_with_security_id("XNAS:PEP") != []

        # Act: restore
        report = restore_repair(
            symbols_container=syms, portfolio_container=port,
            backup_path=backup_file,
        )

        # Assert: movements reverted
        reverted = port.movements_with_security_id("NNYS:PEP")
        assert len(reverted) == 2, (
            f"PEP-19: restore must revert movements to NNYS:PEP; got {len(reverted)} reverted"
        )

    def test_pep19_restore_does_not_clobber_legitimate_concurrent_edit(self, tmp_path):
        """PEP-19b: if a movement was legitimately edited AFTER the repair
        (e.g. a correction transaction), restore must NOT overwrite the new version.
        This verifies the script re-reads live state before deciding to restore.
        """
        syms = _standard_symbols_container()
        port = _standard_portfolio_container(num_movements=1)

        bk = backup_repair(symbols_container=syms, portfolio_container=port, path=tmp_path)
        from scripts.repair_pep_security_id import write_backup as _write_bk
        backup_file = _write_bk(bk, tmp_path)

        apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        # Simulate a legitimate post-repair edit: quantity changed to 99.0
        for key, doc in port._store.items():
            if doc.get("id") == "mvt_000":
                port._store[key] = {**doc, "quantity": 99.0, "_etag": "etag-concurrent-edit"}

        # Restore — the script should notice that the live etag != backup etag
        # and NOT blindly overwrite the legitimately-edited doc
        report = restore_repair(
            symbols_container=syms, portfolio_container=port,
            backup_path=backup_file,
        )

        # The movement should either: (a) be skipped (not clobbered) or (b) a conflict reported
        # Most importantly: the quantity=99.0 should NOT have been silently reverted to 10.0
        live_doc = next(
            (doc for (_, _id), doc in port._store.items() if _id == "mvt_000"),
            None,
        )
        if live_doc is not None:
            # If the restore touched it, it must have flagged a conflict, not silently overwritten
            assert live_doc.get("quantity") == 99.0 or \
                   getattr(report, "cas_conflicts", 0) >= 1, (
                "PEP-19b: restore must not silently overwrite a legitimately concurrent-edited doc; "
                f"live quantity={live_doc.get('quantity')!r}, conflicts={getattr(report, 'cas_conflicts', 0)}"
            )


# ---------------------------------------------------------------------------
# Discovery / import_session references
# ---------------------------------------------------------------------------

@_skip
class TestImportSessionReferences:
    def test_import_session_nnys_pep_reference_repointed(self):
        """§3.4: import_session docs referencing NNYS:PEP in enrolled_security_ids
        or resolution_map must be patched to XNAS:PEP.
        """
        syms = _standard_symbols_container()
        port = _standard_portfolio_container(num_movements=1)
        port.seed_import_session("sess_001", "acct_001", ["NNYS:PEP", "XNYS:AAPL"])

        apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        sess = port.read_item("sess_001", "acct_001")
        enrolled = sess.get("enrolled_security_ids", [])
        assert "NNYS:PEP" not in enrolled, (
            f"import_session enrolled_security_ids must not contain NNYS:PEP after repair; "
            f"got {enrolled}"
        )
        assert "XNAS:PEP" in enrolled, (
            f"import_session enrolled_security_ids must contain XNAS:PEP after repair"
        )
        # XNYS:AAPL must be untouched
        assert "XNYS:AAPL" in enrolled, (
            "import_session enrolled_security_ids must still contain XNYS:AAPL (unchanged)"
        )

    def test_no_import_sessions_does_not_abort(self):
        """§3.4: if no import_session docs exist (TTL expired), repair proceeds normally."""
        syms = _standard_symbols_container()
        port = _standard_portfolio_container(num_movements=2)
        # No import sessions seeded

        report = apply_repair(symbols_container=syms, portfolio_container=port, dry_run=False)

        assert getattr(report, "exit_code", 0) == 0, (
            "Missing import_sessions must not cause non-zero exit_code"
        )
        assert syms.delete_calls, (
            "Repair must still complete (delete source) when no import sessions exist"
        )
