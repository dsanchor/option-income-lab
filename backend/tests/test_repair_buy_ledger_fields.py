"""Regression tests for repair_buy_ledger_fields.py.

Tests the detection, application, idempotency, source_row cross-validation,
manual-candidate logic, SELL/fee-free/corporate exclusions, and
backup/checksum/restore mechanics per Danny's review verdict (2026-09-08).

Test IDs: RBL-* (sequential within each class).

Key contracts being tested:
  §3.1  Source-row cross-validation for csv_import records (not tautological).
  §3.2  Marker-first idempotency: _repair_buy_fields_v1 present → unconditional skip.
  §3.3  Manual BUY candidate reporting: candidate_only=True, no auto-apply.
  §3.4  Fail-closed: ambiguous/missing/mismatch source_row → swap skipped.
  §3.5  SELL, fee-free BUY, SUPERSEDED/VOIDED excluded.
  §3.6  Backup SHA-256 checksum; tampered backup rejected on restore.
  §3.7  _apply_repair produces correct new gross/net/status; does not mutate input.
"""

import json
import pytest
from decimal import Decimal
from pathlib import Path

try:
    from scripts.repair_buy_ledger_fields import (
        _analyse_record,
        _analyse_manual_candidate,
        _apply_repair,
        _parse_spanish_decimal_str,
        _look_up_source_value,
        _sha256,
        run_backup,
        run_restore,
        _MATCH_TOLERANCE,
        _SOURCE_TOTAL_ALIASES,
        _SOURCE_COMMISSION_ALIASES,
    )
    _SCRIPT_AVAILABLE = True
except ImportError:
    _SCRIPT_AVAILABLE = False
    # Placeholders so the module loads; all tests will be skipped.
    _analyse_record = _analyse_manual_candidate = _apply_repair = None  # type: ignore
    _parse_spanish_decimal_str = _look_up_source_value = _sha256 = None  # type: ignore
    run_backup = run_restore = None  # type: ignore
    _MATCH_TOLERANCE = _SOURCE_TOTAL_ALIASES = _SOURCE_COMMISSION_ALIASES = None  # type: ignore

_skip = pytest.mark.skipif(
    not _SCRIPT_AVAILABLE,
    reason=(
        "scripts/repair_buy_ledger_fields.py not available. "
        "Reuben: implement per danny-review-scrip-buy-import-implementation.md §B3 revision."
    ),
)


# ---------------------------------------------------------------------------
# Document builders
# ---------------------------------------------------------------------------

def _csv_buy(mid, *, gross_eur="1825.00", fees_eur="7.50",
             cost_basis_status="COMPLETE", correction_status="ACTIVE",
             source_total=None, source_commission=None,
             qty="10", marker=None, security_id="XNYS:AAPL"):
    """Build a csv_import BUY document in old-semantics shape.

    gross_eur = net consideration (old: CSV "Total" stored directly as gross).
    net_eur = gross_eur - fees_eur (old derivation).
    source_row reflects original CSV cell values when provided.
    """
    source_row = {}
    if source_total is not None:
        source_row["Total (€)"] = source_total
    if source_commission is not None:
        source_row["Comisión"] = source_commission
    net = str(Decimal(str(gross_eur)) - Decimal(str(fees_eur)))
    doc = {
        "id": mid,
        "doc_type": "ledger_txn",
        "txn_type": "BUY",
        "import_source": "csv_import",
        "account_id": "_unassigned",
        "security_id": security_id,
        "trade_date": "2024-01-15",
        "quantity": qty,
        "gross": {"eur_amount": str(gross_eur), "amount": str(gross_eur), "currency": "EUR"},
        "fees": {"total_eur": str(fees_eur), "total": str(fees_eur), "currency": "EUR"},
        "net": {"eur_amount": net, "amount": net, "currency": "EUR"},
        "cost_basis_status": cost_basis_status,
        "correction_status": correction_status,
    }
    if source_row:
        doc["source_row"] = source_row
    if marker:
        doc["_repair_buy_fields_v1"] = marker
    return doc


def _manual_buy(mid, *, gross_eur="1000.00", fees_eur="10.00",
                correction_status="ACTIVE", marker=None, security_id="XNYS:AAPL"):
    """Build a manual BUY in old-semantics shape (gross = trade_value, net = gross-fees)."""
    net = str(Decimal(str(gross_eur)) - Decimal(str(fees_eur)))
    doc = {
        "id": mid,
        "doc_type": "ledger_txn",
        "txn_type": "BUY",
        "import_source": "manual",
        "account_id": "_unassigned",
        "security_id": security_id,
        "trade_date": "2024-01-15",
        "quantity": "100",
        "gross": {"eur_amount": str(gross_eur), "amount": str(gross_eur), "currency": "EUR"},
        "fees": {"total_eur": str(fees_eur), "total": str(fees_eur), "currency": "EUR"},
        "net": {"eur_amount": net, "amount": net, "currency": "EUR"},
        "cost_basis_status": "COMPLETE",
        "correction_status": correction_status,
    }
    if marker:
        doc["_repair_buy_fields_v1"] = marker
    return doc


def _sell_doc(mid, *, gross_eur="600.00", fees_eur="6.00"):
    """Build a SELL document (should always be excluded from repair)."""
    net = str(Decimal(str(gross_eur)) - Decimal(str(fees_eur)))
    return {
        "id": mid,
        "doc_type": "ledger_txn",
        "txn_type": "SELL",
        "import_source": "csv_import",
        "account_id": "_unassigned",
        "security_id": "XNYS:AAPL",
        "trade_date": "2024-01-15",
        "quantity": "50",
        "gross": {"eur_amount": str(gross_eur), "amount": str(gross_eur), "currency": "EUR"},
        "fees": {"total_eur": str(fees_eur), "total": str(fees_eur), "currency": "EUR"},
        "net": {"eur_amount": net, "amount": net, "currency": "EUR"},
        "cost_basis_status": "COMPLETE",
        "correction_status": "ACTIVE",
    }


def _d(v):
    return Decimal(str(v))


# ---------------------------------------------------------------------------
# Fake container for backup/restore tests
# ---------------------------------------------------------------------------

class _FakeContainer:
    def __init__(self, docs=None):
        self._store = {}
        for doc in (docs or []):
            self._store[doc["id"]] = {**doc, "_etag": f"etag-{doc['id']}"}

    def read_item(self, item, partition_key):
        from azure.cosmos.exceptions import CosmosResourceNotFoundError
        if item not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[item])

    def query_items(self, query="", parameters=None, enable_cross_partition_query=True, partition_key=None):
        return iter(list(self._store.values()))

    def replace_item(self, item, body, etag=None, match_condition=None):
        if item not in self._store:
            from azure.cosmos.exceptions import CosmosResourceNotFoundError
            raise CosmosResourceNotFoundError(message="not found", response=None)
        current_etag = self._store[item].get("_etag", "")
        if etag and match_condition is not None:
            if current_etag != etag:
                from azure.cosmos.exceptions import CosmosHttpResponseError
                err = CosmosHttpResponseError(message="precondition failed", response=None)
                err.status_code = 412
                raise err
        self._store[item] = {**body, "_etag": f"etag-{item}-new"}
        return dict(self._store[item])


# ---------------------------------------------------------------------------
# §1 — _parse_spanish_decimal_str
# ---------------------------------------------------------------------------

@_skip
class TestParseSpanishDecimal:
    """RBL-1x: Spanish-locale decimal parsing."""

    def test_rbl_11_spanish_thousands_dot(self):
        """'1.825,00' → Decimal('1825.00')."""
        assert _parse_spanish_decimal_str("1.825,00") == _d("1825.00")

    def test_rbl_12_simple_integer(self):
        """'100' → Decimal('100')."""
        assert _parse_spanish_decimal_str("100") == _d("100")

    def test_rbl_13_zero(self):
        """'0' → Decimal('0')."""
        assert _parse_spanish_decimal_str("0") == _d("0")

    def test_rbl_14_blank_returns_none(self):
        """Empty string → None."""
        assert _parse_spanish_decimal_str("") is None

    def test_rbl_15_na_returns_none(self):
        """'N/A' → None."""
        assert _parse_spanish_decimal_str("N/A") is None

    def test_rbl_16_none_input_returns_none(self):
        """None input → None."""
        assert _parse_spanish_decimal_str(None) is None  # type: ignore

    def test_rbl_17_dash_returns_none(self):
        """'-' → None."""
        assert _parse_spanish_decimal_str("-") is None

    def test_rbl_18_comma_decimal(self):
        """'7,50' → Decimal('7.50')."""
        assert _parse_spanish_decimal_str("7,50") == _d("7.50")


# ---------------------------------------------------------------------------
# §2 — _look_up_source_value
# ---------------------------------------------------------------------------

@_skip
class TestLookUpSourceValue:
    """RBL-2x: Source-row header lookup with alias normalization."""

    def test_rbl_21_spanish_total_header(self):
        """'Total (€)' → found via alias set."""
        row = {"Total (€)": "1.825,00"}
        assert _look_up_source_value(row, _SOURCE_TOTAL_ALIASES) == _d("1825.00")

    def test_rbl_22_english_total_header(self):
        """'Total' (English) → found via alias normalization."""
        row = {"Total": "100"}
        assert _look_up_source_value(row, _SOURCE_TOTAL_ALIASES) == _d("100")

    def test_rbl_23_comision_diacritics(self):
        """'Comisión' (with accent) → found via diacritic stripping."""
        row = {"Comisión": "7,50"}
        assert _look_up_source_value(row, _SOURCE_COMMISSION_ALIASES) == _d("7.50")

    def test_rbl_24_commission_english(self):
        """'Commission' (English) → found."""
        row = {"Commission": "5"}
        assert _look_up_source_value(row, _SOURCE_COMMISSION_ALIASES) == _d("5")

    def test_rbl_25_unknown_header_returns_none(self):
        """No matching header → None."""
        row = {"FooBar": "100"}
        assert _look_up_source_value(row, _SOURCE_TOTAL_ALIASES) is None

    def test_rbl_26_unparseable_value_returns_none(self):
        """Header present but value unparseable → None."""
        row = {"Total (€)": "N/A"}
        assert _look_up_source_value(row, _SOURCE_TOTAL_ALIASES) is None


# ---------------------------------------------------------------------------
# §3 — _analyse_record (csv_import BUY detection)
# ---------------------------------------------------------------------------

@_skip
class TestAnalyseRecordCSV:
    """RBL-3x: Detection logic for csv_import BUY records."""

    # --- Base cases ---

    def test_rbl_31_old_semantics_source_row_confirmed(self):
        """gross ≈ source_total (old: CSV Total stored as gross) → needs_swap=True."""
        doc = _csv_buy("t1", gross_eur="1825.00", fees_eur="7.50",
                       source_total="1.825,00", source_commission="7,50")
        plan = _analyse_record(doc)
        assert plan is not None
        assert plan["needs_swap"] is True
        assert _d(plan["new_gross_eur"]) == _d("1832.50")  # 1825 + 7.50
        assert _d(plan["new_net_eur"]) == _d("1825.00")

    def test_rbl_32_already_correct_source_row(self):
        """gross ≈ source_total + commission → already correct, returns None or needs_swap=False."""
        # Already-corrected record: gross = 1832.50 (= 1825 + 7.50)
        doc = _csv_buy("t2", gross_eur="1832.50", fees_eur="7.50",
                       source_total="1.825,00", source_commission="7,50")
        plan = _analyse_record(doc)
        # Either None (nothing to do) or plan with needs_swap=False
        if plan is not None:
            assert plan.get("needs_swap") is False

    def test_rbl_33_no_source_row_fail_closed(self):
        """No source_row with fees > 0 → fail-closed: swap NOT flagged."""
        doc = _csv_buy("t3", gross_eur="1825.00", fees_eur="7.50",
                       source_total=None, source_commission=None)
        # source_row absent → cannot verify inversion → no swap
        plan = _analyse_record(doc)
        if plan is not None:
            assert plan.get("needs_swap") is False

    def test_rbl_34_ambiguous_source_neither_match(self):
        """source_total doesn't match either old or new → fail-closed, no swap."""
        # Stored gross=1825, fees=7.50, but source_total=999 (completely different)
        doc = _csv_buy("t4", gross_eur="1825.00", fees_eur="7.50",
                       source_total="999,00", source_commission="7,50")
        plan = _analyse_record(doc)
        if plan is not None:
            assert plan.get("needs_swap") is False

    def test_rbl_35_zero_fees_no_swap(self):
        """fees_eur = 0 → no swap needed (gross and net are identical by definition)."""
        doc = _csv_buy("t5", gross_eur="1825.00", fees_eur="0",
                       source_total="1.825,00", source_commission="0")
        plan = _analyse_record(doc)
        if plan is not None:
            assert plan.get("needs_swap") is False

    # --- Exclusion cases ---

    def test_rbl_36_sell_excluded(self):
        """SELL movements are not BUY → excluded from all analysis."""
        doc = _sell_doc("s1")
        assert _analyse_record(doc) is None

    def test_rbl_37_superseded_excluded(self):
        """correction_status=SUPERSEDED → excluded."""
        doc = _csv_buy("t7", gross_eur="1825.00", fees_eur="7.50",
                       correction_status="SUPERSEDED",
                       source_total="1.825,00", source_commission="7,50")
        assert _analyse_record(doc) is None

    def test_rbl_38_voided_excluded(self):
        """correction_status=VOIDED → excluded."""
        doc = _csv_buy("t8", gross_eur="1825.00", fees_eur="7.50",
                       correction_status="VOIDED",
                       source_total="1.825,00", source_commission="7,50")
        assert _analyse_record(doc) is None

    def test_rbl_39_manual_import_excluded(self):
        """import_source='manual' → not processed by _analyse_record (different path)."""
        doc = _manual_buy("t9", gross_eur="1000.00", fees_eur="10.00")
        assert _analyse_record(doc) is None

    # --- Idempotency ---

    def test_rbl_3a_marker_present_unconditional_skip(self):
        """_repair_buy_fields_v1 marker present → None (already repaired)."""
        doc = _csv_buy("t10", gross_eur="1825.00", fees_eur="7.50",
                       source_total="1.825,00", source_commission="7,50",
                       marker="20260908T120000Z")
        assert _analyse_record(doc) is None

    def test_rbl_3b_second_run_zero_after_apply(self):
        """After _apply_repair, the marker prevents re-detection on second run."""
        doc = _csv_buy("t11", gross_eur="1825.00", fees_eur="7.50",
                       source_total="1.825,00", source_commission="7,50")
        plan = _analyse_record(doc)
        assert plan is not None  # detected on first run
        patched = _apply_repair(doc, plan)
        # Second run on patched doc → skip
        assert "_repair_buy_fields_v1" in patched
        plan2 = _analyse_record(patched)
        assert plan2 is None  # idempotent

    # --- Status reclassification ---

    def test_rbl_3c_incomplete_zero_price_reclassified(self):
        """INCOMPLETE + zero source_total + qty > 0 → needs_status=True (→ZERO_COST)."""
        doc = _csv_buy("t12", gross_eur="0", fees_eur="0",
                       cost_basis_status="INCOMPLETE",
                       source_total="0", source_commission="0", qty="50")
        plan = _analyse_record(doc)
        assert plan is not None
        assert plan["needs_status"] is True
        assert plan["new_status"] == "ZERO_COST"

    def test_rbl_3d_incomplete_nonzero_price_not_reclassified(self):
        """INCOMPLETE but non-zero price → fail-closed, no status reclassification."""
        doc = _csv_buy("t13", gross_eur="1825.00", fees_eur="0",
                       cost_basis_status="INCOMPLETE",
                       source_total="1.825,00", source_commission="0", qty="10")
        plan = _analyse_record(doc)
        if plan is not None:
            assert plan.get("needs_status") is False

    def test_rbl_3e_bilingual_english_headers_detected(self):
        """English header 'Total' and 'Commission' also trigger detection via alias."""
        doc = _csv_buy("t14", gross_eur="100.00", fees_eur="5.00",
                       source_total=None, source_commission=None)
        # Add source_row with English headers
        doc["source_row"] = {"Total": "100", "Commission": "5"}
        plan = _analyse_record(doc)
        assert plan is not None
        assert plan["needs_swap"] is True
        assert _d(plan["new_gross_eur"]) == _d("105.00")


# ---------------------------------------------------------------------------
# §4 — _analyse_manual_candidate
# ---------------------------------------------------------------------------

@_skip
class TestAnalyseManualCandidate:
    """RBL-4x: Manual BUY candidate detection (report-only, no auto-apply)."""

    def test_rbl_41_manual_with_fees_is_candidate(self):
        """Manual BUY with fees > 0 and net = gross - fees → candidate_only=True."""
        doc = _manual_buy("m1", gross_eur="1000.00", fees_eur="10.00")
        plan = _analyse_manual_candidate(doc)
        assert plan is not None
        assert plan["candidate_only"] is True
        assert plan["needs_swap"] is True

    def test_rbl_42_manual_fee_free_excluded(self):
        """Manual BUY with fees = 0 → None (nothing to fix)."""
        doc = _manual_buy("m2", gross_eur="1000.00", fees_eur="0")
        assert _analyse_manual_candidate(doc) is None

    def test_rbl_43_manual_marker_present_skipped(self):
        """Manual BUY with _repair_buy_fields_v1 marker → None (already handled)."""
        doc = _manual_buy("m3", gross_eur="1000.00", fees_eur="10.00",
                          marker="20260908T120000Z")
        assert _analyse_manual_candidate(doc) is None

    def test_rbl_44_manual_superseded_excluded(self):
        """Manual BUY SUPERSEDED → None."""
        doc = _manual_buy("m4", gross_eur="1000.00", fees_eur="10.00",
                          correction_status="SUPERSEDED")
        assert _analyse_manual_candidate(doc) is None

    def test_rbl_45_csv_import_excluded_from_manual_path(self):
        """csv_import BUY → not processed by _analyse_manual_candidate."""
        doc = _csv_buy("t_m5", gross_eur="1825.00", fees_eur="7.50",
                       source_total="1.825,00", source_commission="7,50")
        assert _analyse_manual_candidate(doc) is None

    def test_rbl_46_manual_sell_excluded(self):
        """SELL → not processed by _analyse_manual_candidate."""
        doc = _sell_doc("s_m6")
        assert _analyse_manual_candidate(doc) is None

    def test_rbl_47_manual_unusual_net_skipped(self):
        """Manual BUY where net does not follow gross - fees → skipped (unexpected shape)."""
        # net = 900 (not 990 = 1000-10) → unusual
        doc = _manual_buy("m7", gross_eur="1000.00", fees_eur="10.00")
        doc["net"]["eur_amount"] = "900.00"  # fabricated inconsistency
        doc["net"]["amount"] = "900.00"
        plan = _analyse_manual_candidate(doc)
        assert plan is None

    def test_rbl_48_manual_candidate_proposed_values_correct(self):
        """Proposed new_gross = old_gross + fees; new_net = old_gross."""
        doc = _manual_buy("m8", gross_eur="500.00", fees_eur="5.00")
        plan = _analyse_manual_candidate(doc)
        assert plan is not None
        assert _d(plan["new_gross_eur"]) == _d("505.00")
        assert _d(plan["new_net_eur"]) == _d("500.00")

    def test_rbl_49_manual_cannot_autodistinguish_noted_in_reason(self):
        """Manual candidate reason must mention it requires operator confirmation."""
        doc = _manual_buy("m9", gross_eur="1000.00", fees_eur="10.00")
        plan = _analyse_manual_candidate(doc)
        assert plan is not None
        assert "confirm" in plan["reason"].lower() or "manual" in plan["reason"].lower()


# ---------------------------------------------------------------------------
# §5 — _apply_repair
# ---------------------------------------------------------------------------

@_skip
class TestApplyRepair:
    """RBL-5x: _apply_repair produces correct patch; original doc not mutated."""

    def _make_plan(self, gross_eur="1825.00", fees_eur="7.50",
                   needs_status=False, new_status="COMPLETE"):
        return {
            "id": "t1",
            "account_id": "_unassigned",
            "needs_swap": True,
            "needs_status": needs_status,
            "new_gross_eur": str(_d(gross_eur) + _d(fees_eur)),
            "new_net_eur": str(gross_eur),
            "new_gross_amt": str(_d(gross_eur) + _d(fees_eur)),
            "new_net_amt": str(gross_eur),
            "new_status": new_status,
            "current_gross_eur": str(gross_eur),
            "current_net_eur": str(_d(gross_eur) - _d(fees_eur)),
        }

    def test_rbl_51_gross_corrected(self):
        """After apply, gross.eur_amount = old_gross + fees."""
        doc = _csv_buy("t1", gross_eur="1825.00", fees_eur="7.50")
        plan = self._make_plan("1825.00", "7.50")
        patched = _apply_repair(doc, plan)
        assert _d(patched["gross"]["eur_amount"]) == _d("1832.50")

    def test_rbl_52_net_corrected(self):
        """After apply, net.eur_amount = old_gross (the net consideration)."""
        doc = _csv_buy("t2", gross_eur="1825.00", fees_eur="7.50")
        plan = self._make_plan("1825.00", "7.50")
        patched = _apply_repair(doc, plan)
        assert _d(patched["net"]["eur_amount"]) == _d("1825.00")

    def test_rbl_53_marker_written(self):
        """After apply, _repair_buy_fields_v1 marker is set."""
        doc = _csv_buy("t3", gross_eur="1825.00", fees_eur="7.50")
        plan = self._make_plan("1825.00", "7.50")
        patched = _apply_repair(doc, plan)
        assert "_repair_buy_fields_v1" in patched

    def test_rbl_54_original_not_mutated(self):
        """Input doc is not mutated by _apply_repair (deepcopy semantics)."""
        doc = _csv_buy("t4", gross_eur="1825.00", fees_eur="7.50")
        original_gross = doc["gross"]["eur_amount"]
        plan = self._make_plan("1825.00", "7.50")
        _apply_repair(doc, plan)
        assert doc["gross"]["eur_amount"] == original_gross  # original unchanged

    def test_rbl_55_status_reclassified(self):
        """needs_status=True → cost_basis_status set to new_status."""
        doc = _csv_buy("t5", gross_eur="0", fees_eur="0",
                       cost_basis_status="INCOMPLETE")
        plan = {
            "id": "t5",
            "account_id": "_unassigned",
            "needs_swap": False,
            "needs_status": True,
            "new_status": "ZERO_COST",
            "new_gross_eur": "0",
            "new_net_eur": "0",
            "new_gross_amt": "0",
            "new_net_amt": "0",
            "current_gross_eur": "0",
            "current_net_eur": "0",
        }
        patched = _apply_repair(doc, plan)
        assert patched["cost_basis_status"] == "ZERO_COST"
        assert "_repair_buy_fields_v1" in patched

    def test_rbl_56_fees_unchanged(self):
        """fees.total_eur is not modified by the swap (only gross/net change)."""
        doc = _csv_buy("t6", gross_eur="1825.00", fees_eur="7.50")
        plan = self._make_plan("1825.00", "7.50")
        patched = _apply_repair(doc, plan)
        assert _d(patched["fees"]["total_eur"]) == _d("7.50")


# ---------------------------------------------------------------------------
# §6 — _sha256 and backup/restore mechanics
# ---------------------------------------------------------------------------

@_skip
class TestSha256:
    """RBL-6x: SHA-256 checksum is deterministic and order-independent."""

    def test_rbl_61_deterministic(self):
        """Same input → same hash."""
        docs = [{"id": "a", "val": 1}, {"id": "b", "val": 2}]
        assert _sha256(docs) == _sha256(docs)

    def test_rbl_62_order_independent(self):
        """Sorted internally → order of input list doesn't matter."""
        docs1 = [{"id": "a", "val": 1}, {"id": "b", "val": 2}]
        docs2 = [{"id": "b", "val": 2}, {"id": "a", "val": 1}]
        assert _sha256(docs1) == _sha256(docs2)

    def test_rbl_63_different_docs_different_hash(self):
        """Different documents → different hash."""
        docs1 = [{"id": "a", "val": 1}]
        docs2 = [{"id": "a", "val": 2}]
        assert _sha256(docs1) != _sha256(docs2)


@_skip
class TestBackupRestore:
    """RBL-7x: Backup/restore mechanics with fake container."""

    def _make_plans(self, doc):
        """Build a minimal plan list for a single document."""
        return [{
            "id": doc["id"],
            "account_id": doc.get("account_id", "_unassigned"),
            "candidate_only": False,
            "needs_swap": True,
            "needs_status": False,
            "new_gross_eur": "1832.50",
            "new_net_eur": "1825.00",
            "new_gross_amt": "1832.50",
            "new_net_amt": "1825.00",
            "new_status": "COMPLETE",
            "current_gross_eur": "1825.00",
            "current_net_eur": "1817.50",
        }]

    def test_rbl_71_backup_file_written(self, tmp_path):
        """Backup produces a JSON file with documents and sha256."""
        doc = _csv_buy("doc1", gross_eur="1825.00", fees_eur="7.50")
        container = _FakeContainer([doc])
        plans = self._make_plans(doc)
        backup_path = tmp_path / "backup.json"
        result_path = run_backup(container, plans, backup_path=backup_path)
        assert result_path.exists()
        payload = json.loads(backup_path.read_text())
        assert "documents" in payload
        assert "sha256" in payload
        assert payload["record_count"] == 1

    def test_rbl_72_backup_checksum_matches_content(self, tmp_path):
        """SHA-256 in backup file must match actual content."""
        doc = _csv_buy("doc2", gross_eur="1825.00", fees_eur="7.50")
        container = _FakeContainer([doc])
        plans = self._make_plans(doc)
        backup_path = tmp_path / "backup2.json"
        run_backup(container, plans, backup_path=backup_path)
        payload = json.loads(backup_path.read_text())
        stored_checksum = payload["sha256"]
        actual_checksum = _sha256(payload["documents"])
        assert stored_checksum == actual_checksum

    def test_rbl_73_tampered_backup_rejected_on_restore(self, tmp_path):
        """Tampered backup (checksum mismatch) → SystemExit on restore."""
        doc = _csv_buy("doc3", gross_eur="1825.00", fees_eur="7.50")
        container = _FakeContainer([doc])
        plans = self._make_plans(doc)
        backup_path = tmp_path / "backup3.json"
        run_backup(container, plans, backup_path=backup_path)

        # Tamper: change a value, leave checksum unchanged
        payload = json.loads(backup_path.read_text())
        payload["documents"][0]["gross"]["eur_amount"] = "9999.00"
        backup_path.write_text(json.dumps(payload))

        with pytest.raises(SystemExit):
            run_restore(container, backup_path)

    def test_rbl_74_backup_excludes_manual_candidates(self, tmp_path):
        """Manual candidates (candidate_only=True) are not included in backup."""
        doc = _csv_buy("doc4", gross_eur="1825.00", fees_eur="7.50")
        container = _FakeContainer([doc])
        manual_plan = {
            "id": "manual1",
            "account_id": "_unassigned",
            "candidate_only": True,   # manual — should not be backed up
            "needs_swap": True,
            "needs_status": False,
            "new_gross_eur": "1010.00",
            "new_net_eur": "1000.00",
            "new_gross_amt": "1010.00",
            "new_net_amt": "1000.00",
            "new_status": "COMPLETE",
            "current_gross_eur": "1000.00",
            "current_net_eur": "990.00",
        }
        auto_plan = self._make_plans(doc)[0]
        plans = [manual_plan, auto_plan]
        backup_path = tmp_path / "backup4.json"
        run_backup(container, plans, backup_path=backup_path)
        payload = json.loads(backup_path.read_text())
        backed_up_ids = {d["id"] for d in payload["documents"]}
        assert "manual1" not in backed_up_ids
        assert "doc4" in backed_up_ids


# ---------------------------------------------------------------------------
# §7 — Full pipeline: old-semantics → detect → apply → verify → second run zero
# ---------------------------------------------------------------------------

@_skip
class TestFullPipeline:
    """RBL-8x: End-to-end detection, apply, and second-run idempotency."""

    def test_rbl_81_detect_apply_second_run_zero(self):
        """Complete pipeline: detect on first run, apply, zero candidates on second run."""
        # Old-semantics doc: gross = CSV Total = net consideration
        doc = _csv_buy("pipe1", gross_eur="1825.00", fees_eur="7.50",
                       source_total="1.825,00", source_commission="7,50")

        # First run: detect
        plan = _analyse_record(doc)
        assert plan is not None, "First run must detect the inversion"

        # Apply
        patched = _apply_repair(doc, plan)
        assert _d(patched["gross"]["eur_amount"]) == _d("1832.50")
        assert "_repair_buy_fields_v1" in patched

        # Second run: zero candidates
        plan2 = _analyse_record(patched)
        assert plan2 is None, "Second run must produce zero candidates (idempotent)"

    def test_rbl_82_zero_cost_status_pipeline(self):
        """INCOMPLETE zero-cost record → reclassified to ZERO_COST on apply."""
        doc = _csv_buy("pipe2", gross_eur="0", fees_eur="0",
                       cost_basis_status="INCOMPLETE",
                       source_total="0", source_commission="0", qty="50")

        plan = _analyse_record(doc)
        assert plan is not None
        assert plan["needs_status"] is True

        patched = _apply_repair(doc, plan)
        assert patched["cost_basis_status"] == "ZERO_COST"

        # Second run: zero candidates
        plan2 = _analyse_record(patched)
        assert plan2 is None

    def test_rbl_83_already_correct_record_not_flagged(self):
        """Record already using true gross (post-fix import) → not flagged."""
        # Already correct: gross = 1832.50 (= net 1825 + commission 7.50)
        doc = _csv_buy("pipe3", gross_eur="1832.50", fees_eur="7.50",
                       source_total="1.825,00", source_commission="7,50")
        plan = _analyse_record(doc)
        # Either None or plan with needs_swap=False
        if plan is not None:
            assert not plan.get("needs_swap"), (
                "Already-correct record must not be flagged for swap — "
                "detection is tautological if this fails"
            )

    def test_rbl_84_sell_excluded_from_pipeline(self):
        """SELL documents never appear in any repair plan."""
        sell = _sell_doc("sell1")
        assert _analyse_record(sell) is None
        assert _analyse_manual_candidate(sell) is None

    def test_rbl_85_fee_free_buy_not_flagged(self):
        """BUY with fees=0 never needs swap (gross == net)."""
        doc = _csv_buy("pipe5", gross_eur="1825.00", fees_eur="0",
                       source_total="1.825,00", source_commission="0")
        plan = _analyse_record(doc)
        if plan is not None:
            assert not plan.get("needs_swap")
