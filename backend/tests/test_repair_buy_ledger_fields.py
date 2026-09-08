"""Regression tests for repair_buy_ledger_fields.py.

Tests the detection, application, idempotency, source_row cross-validation,
manual-candidate logic, SELL/fee-free/corporate exclusions, and
backup/checksum/restore mechanics per Danny's review verdict (2026-09-08).

Test IDs: RBL-* (v1 tests, sequential within each class).
           RBL-V2-* (v2 tests, sequential within each class).

Key contracts being tested (v1):
  §3.1  Source-row cross-validation for csv_import records (not tautological).
  §3.2  Marker-first idempotency: _repair_buy_fields_v1 present → unconditional skip.
  §3.3  Manual BUY candidate reporting: candidate_only=True, no auto-apply.
  §3.4  Fail-closed: ambiguous/missing/mismatch source_row → swap skipped.
  §3.5  SELL, fee-free BUY, SUPERSEDED/VOIDED excluded.
  §3.6  Backup SHA-256 checksum; tampered backup rejected on restore.
  §3.7  _apply_repair produces correct new gross/net/status; does not mutate input.

Key contracts being tested (v2 — danny-fifo-net-accounting-contract.md §1):
  §V2.1  _repair_buy_fields_v2 marker → unconditional skip (idempotency).
  §V2.2  Case A (v1 marker + fees>0): source-row confirms 4ca553e shape → swap.
  §V2.3  Case A fallback (no source_row): arithmetic check → swap when consistent.
  §V2.4  Case B (no marker): source_row required; 4ca553e shape → swap.
  §V2.5  Case B (no marker): old shape (gross=trade) → update net only.
  §V2.6  Already-correct (gross=trade, net=trade+fees) → skip (no write).
  §V2.7  Fee-free / ZERO_COST → skip silently (no v2 marker written).
  §V2.8  SELL, SUPERSEDED, VOIDED excluded from v2 analysis.
  §V2.9  _apply_repair_v2 produces correct fields; does not mutate input.
  §V2.10 V2 backup writes migration_version='v2'; checksum validated.
  §V2.11 V2 restore rejects non-v2 backups and tampered backups.
  §V2.12 Idempotent second run produces zero v2 candidates.
  §V2.13 ADM-like scenario: 3 lots, FIFO target values verified (MIG-1/MIG-9).
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
        _analyse_record_v2,
        _apply_repair_v2,
        run_backup_v2,
        run_restore_v2,
        run_audit_v2,
        run_verify_v2,
    )
    _SCRIPT_AVAILABLE = True
except ImportError:
    _SCRIPT_AVAILABLE = False
    # Placeholders so the module loads; all tests will be skipped.
    _analyse_record = _analyse_manual_candidate = _apply_repair = None  # type: ignore
    _parse_spanish_decimal_str = _look_up_source_value = _sha256 = None  # type: ignore
    run_backup = run_restore = None  # type: ignore
    _MATCH_TOLERANCE = _SOURCE_TOTAL_ALIASES = _SOURCE_COMMISSION_ALIASES = None  # type: ignore
    _analyse_record_v2 = _apply_repair_v2 = None  # type: ignore
    run_backup_v2 = run_restore_v2 = run_audit_v2 = run_verify_v2 = None  # type: ignore

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


# ===========================================================================
# V2 TEST SUITE — danny-fifo-net-accounting-contract.md §1
# ===========================================================================

# ---------------------------------------------------------------------------
# V2 Document builders
# ---------------------------------------------------------------------------

def _csv_buy_v1repaired(mid, *, trade_eur="1825.00", fees_eur="7.50",
                        source_total=None, source_commission=None,
                        security_id="XNYS:AAPL", trade_date="2024-01-15"):
    """Build a csv_import BUY in post-v1 / 4ca553e shape.

    After the v1 repair: gross = trade+fees (total outflow), net = trade.
    This is the input shape for Case A v2 detection.
    """
    trade = Decimal(str(trade_eur))
    fees  = Decimal(str(fees_eur))
    gross = str(trade + fees)   # 4ca553e shape: gross = trade+fees
    net   = str(trade)          # 4ca553e shape: net   = trade value

    # Default source_row uses the trade value as source total
    sr_total      = source_total if source_total is not None else str(trade).replace(".", ",")
    sr_commission = source_commission if source_commission is not None else str(fees).replace(".", ",")
    doc = {
        "id": mid,
        "doc_type": "ledger_txn",
        "txn_type": "BUY",
        "import_source": "csv_import",
        "account_id": "_unassigned",
        "security_id": security_id,
        "trade_date": trade_date,
        "quantity": "10",
        "gross": {"eur_amount": gross, "amount": gross, "currency": "EUR"},
        "fees": {"total_eur": str(fees), "total": str(fees), "currency": "EUR"},
        "net":  {"eur_amount": net,   "amount": net,   "currency": "EUR"},
        "cost_basis_status": "COMPLETE",
        "correction_status": "ACTIVE",
        "source_row": {"Total (€)": sr_total, "Comisión": sr_commission},
        "_repair_buy_fields_v1": "20260908T075000Z",
    }
    return doc


def _csv_buy_new_correct(mid, *, trade_eur="1825.00", fees_eur="7.50",
                         source_total=None, source_commission=None,
                         security_id="XNYS:AAPL", trade_date="2024-01-15"):
    """Build a csv_import BUY already in the new-directive shape.

    New directive: gross = trade (pre-commission), net = trade + fees.
    These records should NOT be flagged by v2 detection.
    """
    trade = Decimal(str(trade_eur))
    fees  = Decimal(str(fees_eur))
    gross = str(trade)           # new directive: gross = trade value
    net   = str(trade + fees)    # new directive: net   = trade + fees

    sr_total      = source_total if source_total is not None else str(trade).replace(".", ",")
    sr_commission = source_commission if source_commission is not None else str(fees).replace(".", ",")
    doc = {
        "id": mid,
        "doc_type": "ledger_txn",
        "txn_type": "BUY",
        "import_source": "csv_import",
        "account_id": "_unassigned",
        "security_id": security_id,
        "trade_date": trade_date,
        "quantity": "10",
        "gross": {"eur_amount": gross, "amount": gross, "currency": "EUR"},
        "fees": {"total_eur": str(fees), "total": str(fees), "currency": "EUR"},
        "net":  {"eur_amount": net,   "amount": net,   "currency": "EUR"},
        "cost_basis_status": "COMPLETE",
        "correction_status": "ACTIVE",
        "source_row": {"Total (€)": sr_total, "Comisión": sr_commission},
    }
    return doc


def _csv_buy_old_shape(mid, *, trade_eur="1825.00", fees_eur="7.50",
                       source_total=None, source_commission=None,
                       security_id="XNYS:AAPL", trade_date="2024-01-15"):
    """Build a csv_import BUY in old/original shape (pre-v1, not repaired by v1).

    Old shape: gross = trade value (not total outflow), net = trade - fees.
    This records a pre-v1 import that somehow escaped the v1 migration.
    Target after v2: gross = trade (unchanged), net = trade + fees.
    """
    trade = Decimal(str(trade_eur))
    fees  = Decimal(str(fees_eur))
    gross = str(trade)           # old shape: gross = trade value
    net   = str(trade - fees)    # old shape: net   = trade - fees (wrong)

    sr_total      = source_total if source_total is not None else str(trade).replace(".", ",")
    sr_commission = source_commission if source_commission is not None else str(fees).replace(".", ",")
    doc = {
        "id": mid,
        "doc_type": "ledger_txn",
        "txn_type": "BUY",
        "import_source": "csv_import",
        "account_id": "_unassigned",
        "security_id": security_id,
        "trade_date": trade_date,
        "quantity": "10",
        "gross": {"eur_amount": gross, "amount": gross, "currency": "EUR"},
        "fees": {"total_eur": str(fees), "total": str(fees), "currency": "EUR"},
        "net":  {"eur_amount": net,   "amount": net,   "currency": "EUR"},
        "cost_basis_status": "COMPLETE",
        "correction_status": "ACTIVE",
        "source_row": {"Total (€)": sr_total, "Comisión": sr_commission},
        # No _repair_buy_fields_v1 (never repaired by v1)
    }
    return doc


# ---------------------------------------------------------------------------
# §V2.1 — Idempotency
# ---------------------------------------------------------------------------

@_skip
class TestV2Idempotency:
    """RBL-V2-1x: _repair_buy_fields_v2 marker → unconditional skip."""

    def test_rbl_v2_11_v2_marker_skip(self):
        """Record with _repair_buy_fields_v2 → None regardless of field state."""
        doc = _csv_buy_v1repaired("v2-id1")
        doc["_repair_buy_fields_v2"] = "20260908T120000Z"
        assert _analyse_record_v2(doc) is None

    def test_rbl_v2_12_after_apply_second_run_zero(self):
        """After _apply_repair_v2, marker prevents re-detection."""
        doc = _csv_buy_v1repaired("v2-id2")
        plan = _analyse_record_v2(doc)
        assert plan is not None, "First run must detect the 4ca553e shape"
        patched = _apply_repair_v2(doc, plan)
        assert "_repair_buy_fields_v2" in patched
        plan2 = _analyse_record_v2(patched)
        assert plan2 is None, "Second run must produce zero candidates (MIG-7)"

    def test_rbl_v2_13_v1_marker_alone_does_not_skip_v2(self):
        """v1 marker alone does NOT skip v2 detection — record still needs v2 repair."""
        doc = _csv_buy_v1repaired("v2-id3")
        # Has v1 marker, no v2 marker → should still be detected
        plan = _analyse_record_v2(doc)
        assert plan is not None


# ---------------------------------------------------------------------------
# §V2.2 — Case A: v1 marker + fees > 0
# ---------------------------------------------------------------------------

@_skip
class TestV2CaseA:
    """RBL-V2-2x: Case A — v1-repaired records (4ca553e shape) → swap gross↔net."""

    def test_rbl_v2_21_case_a_swap_detected(self):
        """Case A: v1 marker, 4ca553e shape → plan with action='swap' (MIG-1)."""
        doc = _csv_buy_v1repaired("v2-2a1", trade_eur="1825.00", fees_eur="7.50")
        plan = _analyse_record_v2(doc)
        assert plan is not None
        assert plan["case"] == "A"
        assert plan["action"] == "swap"

    def test_rbl_v2_22_case_a_new_gross_equals_trade(self):
        """After Case A swap: new_gross_eur = trade value (pre-commission)."""
        doc = _csv_buy_v1repaired("v2-2a2", trade_eur="1825.00", fees_eur="7.50")
        plan = _analyse_record_v2(doc)
        assert plan is not None
        assert _d(plan["new_gross_eur"]) == _d("1825.00")  # trade value

    def test_rbl_v2_23_case_a_new_net_equals_trade_plus_fees(self):
        """After Case A swap: new_net_eur = trade + fees (total outflow)."""
        doc = _csv_buy_v1repaired("v2-2a3", trade_eur="1825.00", fees_eur="7.50")
        plan = _analyse_record_v2(doc)
        assert plan is not None
        assert _d(plan["new_net_eur"]) == _d("1832.50")  # 1825 + 7.50

    def test_rbl_v2_24_case_a_fees_unchanged(self):
        """Case A: fees are not modified."""
        doc = _csv_buy_v1repaired("v2-2a4", trade_eur="1825.00", fees_eur="7.50")
        plan = _analyse_record_v2(doc)
        assert plan is not None
        assert _d(plan["current_fees_eur"]) == _d("7.50")

    def test_rbl_v2_25_case_a_source_row_cross_validation(self):
        """Case A: source_row used to cross-validate trade value (MIG-9)."""
        # Provide source_row with matching Total
        doc = _csv_buy_v1repaired("v2-2a5", trade_eur="1825.00", fees_eur="7.50",
                                  source_total="1.825,00", source_commission="7,50")
        plan = _analyse_record_v2(doc)
        assert plan is not None
        assert _d(plan["new_gross_eur"]) == _d("1825.00")  # from source_row

    def test_rbl_v2_26_case_a_source_mismatch_fail_closed(self):
        """Case A: source_row total doesn't match either shape → fail-closed."""
        doc = _csv_buy_v1repaired("v2-2a6", trade_eur="1825.00", fees_eur="7.50",
                                  source_total="999,00", source_commission="7,50")
        plan = _analyse_record_v2(doc)
        assert plan is None, "Source mismatch must fail-closed"

    def test_rbl_v2_27_case_a_no_source_row_arithmetic_fallback(self):
        """Case A: no source_row but arithmetic confirms 4ca553e shape → swap allowed."""
        doc = _csv_buy_v1repaired("v2-2a7", trade_eur="1825.00", fees_eur="7.50")
        del doc["source_row"]  # remove source_row
        plan = _analyse_record_v2(doc)
        # v1 already validated — arithmetic fallback should allow swap
        assert plan is not None
        assert plan["action"] == "swap"

    def test_rbl_v2_28_case_a_no_source_row_arithmetic_inconsistent_fail_closed(self):
        """Case A: no source_row AND arithmetic inconsistent → fail-closed."""
        doc = _csv_buy_v1repaired("v2-2a8", trade_eur="1825.00", fees_eur="7.50")
        del doc["source_row"]
        # Corrupt arithmetic: gross is not net+fees
        doc["gross"]["eur_amount"] = "9999.00"
        doc["gross"]["amount"]     = "9999.00"
        plan = _analyse_record_v2(doc)
        assert plan is None, "Inconsistent arithmetic must fail-closed"


# ---------------------------------------------------------------------------
# §V2.3 — Case B: no marker, 4ca553e shape (post-fix import)
# ---------------------------------------------------------------------------

@_skip
class TestV2CaseB4ca553e:
    """RBL-V2-3x: Case B — no v1 marker, 4ca553e shape → swap via source_row."""

    def test_rbl_v2_31_case_b_4ca553e_swap_detected(self):
        """Case B: post-4ca553e import (no marker, gross=trade+fees) → swap."""
        # Simulate a record imported after 4ca553e deploy:
        # import_service now writes gross=net+commission
        # No _repair_buy_fields_v1 because it wasn't in the DB when v1 ran
        doc = {
            "id": "v2-3b1",
            "doc_type": "ledger_txn",
            "txn_type": "BUY",
            "import_source": "csv_import",
            "account_id": "_unassigned",
            "security_id": "XNYS:AAPL",
            "trade_date": "2026-09-08",
            "quantity": "10",
            "gross": {"eur_amount": "1832.50", "amount": "1832.50", "currency": "EUR"},
            "fees": {"total_eur": "7.50", "total": "7.50", "currency": "EUR"},
            "net":  {"eur_amount": "1825.00", "amount": "1825.00", "currency": "EUR"},
            "cost_basis_status": "COMPLETE",
            "correction_status": "ACTIVE",
            "source_row": {"Total (€)": "1.825,00", "Comisión": "7,50"},
            # No v1 marker — post-4ca553e import
        }
        plan = _analyse_record_v2(doc)
        assert plan is not None
        assert plan["case"] == "B"
        assert plan["action"] == "swap"

    def test_rbl_v2_32_case_b_4ca553e_correct_values(self):
        """Case B 4ca553e: new_gross = trade, new_net = trade+fees."""
        doc = {
            "id": "v2-3b2",
            "doc_type": "ledger_txn",
            "txn_type": "BUY",
            "import_source": "csv_import",
            "account_id": "_unassigned",
            "security_id": "XNYS:AAPL",
            "trade_date": "2026-09-08",
            "quantity": "10",
            "gross": {"eur_amount": "1832.50", "amount": "1832.50", "currency": "EUR"},
            "fees": {"total_eur": "7.50", "total": "7.50", "currency": "EUR"},
            "net":  {"eur_amount": "1825.00", "amount": "1825.00", "currency": "EUR"},
            "cost_basis_status": "COMPLETE",
            "correction_status": "ACTIVE",
            "source_row": {"Total (€)": "1.825,00", "Comisión": "7,50"},
        }
        plan = _analyse_record_v2(doc)
        assert plan is not None
        assert _d(plan["new_gross_eur"]) == _d("1825.00")  # trade value
        assert _d(plan["new_net_eur"])   == _d("1832.50")  # trade + fees

    def test_rbl_v2_33_case_b_no_source_row_fail_closed(self):
        """Case B: no source_row AND no v1 marker → fail-closed (cannot distinguish)."""
        doc = _csv_buy("v2-3b3", gross_eur="1832.50", fees_eur="7.50")  # no source_row
        plan = _analyse_record_v2(doc)
        assert plan is None, "Case B without source_row must fail-closed"

    def test_rbl_v2_34_case_b_ambiguous_source_fail_closed(self):
        """Case B: source_total matches neither 4ca553e nor old shape → fail-closed."""
        doc = {
            "id": "v2-3b4",
            "doc_type": "ledger_txn",
            "txn_type": "BUY",
            "import_source": "csv_import",
            "account_id": "_unassigned",
            "security_id": "XNYS:AAPL",
            "trade_date": "2024-01-15",
            "quantity": "10",
            "gross": {"eur_amount": "1832.50", "amount": "1832.50", "currency": "EUR"},
            "fees": {"total_eur": "7.50", "total": "7.50", "currency": "EUR"},
            "net":  {"eur_amount": "1825.00", "amount": "1825.00", "currency": "EUR"},
            "cost_basis_status": "COMPLETE",
            "correction_status": "ACTIVE",
            "source_row": {"Total (€)": "999,00", "Comisión": "7,50"},  # mismatch
        }
        plan = _analyse_record_v2(doc)
        assert plan is None, "Ambiguous source must fail-closed"


# ---------------------------------------------------------------------------
# §V2.4 — Case B: no marker, old/partial shape
# ---------------------------------------------------------------------------

@_skip
class TestV2CaseBOldShape:
    """RBL-V2-4x: Case B — no marker, old shape (gross=trade, net=trade-fees) → update_net."""

    def test_rbl_v2_41_old_shape_detected(self):
        """Case B old shape: gross=trade (pre-commission), net=trade-fees → update_net."""
        doc = _csv_buy_old_shape("v2-4b1", trade_eur="1825.00", fees_eur="7.50")
        plan = _analyse_record_v2(doc)
        assert plan is not None
        assert plan["action"] == "update_net"

    def test_rbl_v2_42_old_shape_gross_unchanged(self):
        """Case B old shape: gross stays at trade value (MIG-2 — gross unchanged)."""
        doc = _csv_buy_old_shape("v2-4b2", trade_eur="1825.00", fees_eur="7.50")
        plan = _analyse_record_v2(doc)
        assert plan is not None
        assert _d(plan["new_gross_eur"]) == _d("1825.00")  # unchanged

    def test_rbl_v2_43_old_shape_net_updated(self):
        """Case B old shape: net becomes gross + fees = total outflow."""
        doc = _csv_buy_old_shape("v2-4b3", trade_eur="1825.00", fees_eur="7.50")
        plan = _analyse_record_v2(doc)
        assert plan is not None
        assert _d(plan["new_net_eur"]) == _d("1832.50")  # 1825 + 7.50


# ---------------------------------------------------------------------------
# §V2.5 — Already-correct records
# ---------------------------------------------------------------------------

@_skip
class TestV2AlreadyCorrect:
    """RBL-V2-5x: Records already in new-directive shape → skipped."""

    def test_rbl_v2_51_already_correct_no_plan(self):
        """gross=trade, net=trade+fees → skipped (no plan returned)."""
        doc = _csv_buy_new_correct("v2-5c1", trade_eur="1825.00", fees_eur="7.50")
        plan = _analyse_record_v2(doc)
        assert plan is None

    def test_rbl_v2_52_already_correct_with_v1_marker(self):
        """v1-marked record already in correct shape (edge case) → skipped."""
        doc = _csv_buy_new_correct("v2-5c2", trade_eur="1825.00", fees_eur="7.50")
        doc["_repair_buy_fields_v1"] = "20260908T075000Z"
        plan = _analyse_record_v2(doc)
        assert plan is None

    def test_rbl_v2_53_already_correct_idempotent_after_v2_apply(self):
        """After v2 apply, resulting doc is already-correct → second run skips."""
        doc = _csv_buy_v1repaired("v2-5c3", trade_eur="1825.00", fees_eur="7.50")
        plan = _analyse_record_v2(doc)
        assert plan is not None
        patched = _apply_repair_v2(doc, plan)
        # Second run: already correct AND has v2 marker
        assert _analyse_record_v2(patched) is None


# ---------------------------------------------------------------------------
# §V2.6 — Fee-free / ZERO_COST records
# ---------------------------------------------------------------------------

@_skip
class TestV2FeeFree:
    """RBL-V2-6x: Fee-free records skipped silently (no v2 marker written)."""

    def test_rbl_v2_61_zero_cost_fee_free_skipped(self):
        """ZERO_COST record with fees=0 → skipped (no field change needed)."""
        doc = _csv_buy("v2-ff1", gross_eur="0", fees_eur="0",
                       cost_basis_status="ZERO_COST",
                       source_total="0", source_commission="0", qty="50")
        doc["_repair_buy_fields_v1"] = "20260908T075000Z"
        plan = _analyse_record_v2(doc)
        assert plan is None

    def test_rbl_v2_62_fee_free_with_price_skipped(self):
        """Fee-free BUY (e.g. no-commission broker) → skipped (gross==net, correct by definition)."""
        doc = _csv_buy("v2-ff2", gross_eur="1825.00", fees_eur="0",
                       source_total="1.825,00", source_commission="0")
        plan = _analyse_record_v2(doc)
        assert plan is None


# ---------------------------------------------------------------------------
# §V2.7 — Exclusions
# ---------------------------------------------------------------------------

@_skip
class TestV2Exclusions:
    """RBL-V2-7x: SELL, SUPERSEDED, VOIDED excluded from v2 analysis."""

    def test_rbl_v2_71_sell_excluded(self):
        """SELL → not analysed by v2."""
        sell = _sell_doc("v2-sell1")
        assert _analyse_record_v2(sell) is None

    def test_rbl_v2_72_superseded_excluded(self):
        """correction_status=SUPERSEDED → excluded."""
        doc = _csv_buy_v1repaired("v2-sup1")
        doc["correction_status"] = "SUPERSEDED"
        assert _analyse_record_v2(doc) is None

    def test_rbl_v2_73_voided_excluded(self):
        """correction_status=VOIDED → excluded."""
        doc = _csv_buy_v1repaired("v2-void1")
        doc["correction_status"] = "VOIDED"
        assert _analyse_record_v2(doc) is None


# ---------------------------------------------------------------------------
# §V2.8 — _apply_repair_v2
# ---------------------------------------------------------------------------

@_skip
class TestApplyRepairV2:
    """RBL-V2-8x: _apply_repair_v2 produces correct fields; original not mutated."""

    def test_rbl_v2_81_swap_action_gross_corrected(self):
        """Swap: new_gross_eur applied to doc.gross.eur_amount."""
        doc = _csv_buy_v1repaired("v2-ar1", trade_eur="1825.00", fees_eur="7.50")
        plan = _analyse_record_v2(doc)
        assert plan is not None
        patched = _apply_repair_v2(doc, plan)
        assert _d(patched["gross"]["eur_amount"]) == _d("1825.00")  # trade value

    def test_rbl_v2_82_swap_action_net_corrected(self):
        """Swap: new_net_eur applied to doc.net.eur_amount."""
        doc = _csv_buy_v1repaired("v2-ar2", trade_eur="1825.00", fees_eur="7.50")
        plan = _analyse_record_v2(doc)
        assert plan is not None
        patched = _apply_repair_v2(doc, plan)
        assert _d(patched["net"]["eur_amount"]) == _d("1832.50")  # trade + fees

    def test_rbl_v2_83_v2_marker_written(self):
        """After apply: _repair_buy_fields_v2 marker is set."""
        doc = _csv_buy_v1repaired("v2-ar3")
        plan = _analyse_record_v2(doc)
        assert plan is not None
        patched = _apply_repair_v2(doc, plan)
        assert "_repair_buy_fields_v2" in patched

    def test_rbl_v2_84_original_not_mutated(self):
        """Input doc is not mutated (deepcopy semantics)."""
        doc = _csv_buy_v1repaired("v2-ar4", trade_eur="1825.00", fees_eur="7.50")
        original_gross = doc["gross"]["eur_amount"]
        plan = _analyse_record_v2(doc)
        assert plan is not None
        _apply_repair_v2(doc, plan)
        assert doc["gross"]["eur_amount"] == original_gross

    def test_rbl_v2_85_fees_unchanged_after_apply(self):
        """fees.total_eur is not modified by v2 repair."""
        doc = _csv_buy_v1repaired("v2-ar5", trade_eur="1825.00", fees_eur="7.50")
        plan = _analyse_record_v2(doc)
        assert plan is not None
        patched = _apply_repair_v2(doc, plan)
        assert _d(patched["fees"]["total_eur"]) == _d("7.50")

    def test_rbl_v2_86_update_net_action_gross_unchanged(self):
        """update_net action: gross field is unchanged."""
        doc = _csv_buy_old_shape("v2-ar6", trade_eur="1825.00", fees_eur="7.50")
        plan = _analyse_record_v2(doc)
        assert plan is not None
        assert plan["action"] == "update_net"
        patched = _apply_repair_v2(doc, plan)
        assert _d(patched["gross"]["eur_amount"]) == _d("1825.00")  # unchanged

    def test_rbl_v2_87_update_net_action_net_becomes_gross_plus_fees(self):
        """update_net action: net becomes gross + fees."""
        doc = _csv_buy_old_shape("v2-ar7", trade_eur="1825.00", fees_eur="7.50")
        plan = _analyse_record_v2(doc)
        assert plan is not None
        patched = _apply_repair_v2(doc, plan)
        assert _d(patched["net"]["eur_amount"]) == _d("1832.50")  # 1825 + 7.50

    def test_rbl_v2_88_both_markers_after_case_a(self):
        """Case A doc has both v1 and v2 markers after apply."""
        doc = _csv_buy_v1repaired("v2-ar8")
        plan = _analyse_record_v2(doc)
        assert plan is not None
        patched = _apply_repair_v2(doc, plan)
        assert "_repair_buy_fields_v1" in patched  # v1 preserved
        assert "_repair_buy_fields_v2" in patched  # v2 added


# ---------------------------------------------------------------------------
# §V2.9 — Backup and restore mechanics
# ---------------------------------------------------------------------------

@_skip
class TestV2BackupRestore:
    """RBL-V2-9x: V2 backup/restore with version guard and checksum."""

    def _make_v2_plans(self, doc):
        plan = _analyse_record_v2(doc)
        assert plan is not None
        return [plan]

    def test_rbl_v2_91_backup_v2_written_with_version(self, tmp_path):
        """V2 backup file includes migration_version='v2'."""
        doc  = _csv_buy_v1repaired("v2-br1")
        container = _FakeContainer([doc])
        plans = self._make_v2_plans(doc)
        bp = tmp_path / "v2_backup.json"
        run_backup_v2(container, plans, backup_path=bp)
        payload = json.loads(bp.read_text())
        assert payload.get("migration_version") == "v2"

    def test_rbl_v2_92_backup_v2_checksum_valid(self, tmp_path):
        """V2 backup SHA-256 in file matches actual content."""
        doc  = _csv_buy_v1repaired("v2-br2")
        container = _FakeContainer([doc])
        plans = self._make_v2_plans(doc)
        bp = tmp_path / "v2_backup2.json"
        run_backup_v2(container, plans, backup_path=bp)
        payload = json.loads(bp.read_text())
        assert payload["sha256"] == _sha256(payload["documents"])

    def test_rbl_v2_93_restore_v2_rejects_v1_backup(self, tmp_path):
        """run_restore_v2 rejects a v1 backup (no migration_version='v2')."""
        # Create a v1-style backup (no migration_version)
        v1_payload = {
            "created_at": "20260908T071437Z",
            "record_count": 1,
            "sha256": _sha256([{"id": "x"}]),
            "documents": [{"id": "x"}],
        }
        bp = tmp_path / "v1_backup.json"
        bp.write_text(json.dumps(v1_payload))
        container = _FakeContainer([])
        with pytest.raises(SystemExit):
            run_restore_v2(container, bp)

    def test_rbl_v2_94_restore_v2_rejects_tampered_backup(self, tmp_path):
        """run_restore_v2 rejects a tampered v2 backup (checksum mismatch)."""
        doc  = _csv_buy_v1repaired("v2-br4")
        container = _FakeContainer([doc])
        plans = self._make_v2_plans(doc)
        bp = tmp_path / "v2_backup4.json"
        run_backup_v2(container, plans, backup_path=bp)
        payload = json.loads(bp.read_text())
        payload["documents"][0]["gross"]["eur_amount"] = "9999.00"
        bp.write_text(json.dumps(payload))  # tampered; checksum still old
        with pytest.raises(SystemExit):
            run_restore_v2(container, bp)

    def test_rbl_v2_95_restore_v2_succeeds_on_valid_backup(self, tmp_path):
        """run_restore_v2 successfully restores documents from a valid v2 backup."""
        doc  = _csv_buy_v1repaired("v2-br5")
        container = _FakeContainer([doc])
        plans = self._make_v2_plans(doc)
        bp = tmp_path / "v2_backup5.json"
        run_backup_v2(container, plans, backup_path=bp)
        failures = run_restore_v2(container, bp)
        assert failures == 0


# ---------------------------------------------------------------------------
# §V2.10 — Full pipeline: audit → apply → verify cycle
# ---------------------------------------------------------------------------

@_skip
class TestV2Pipeline:
    """RBL-V2-10x: End-to-end v2 pipeline with fake container."""

    def test_rbl_v2_101_audit_finds_case_a_candidates(self):
        """run_audit_v2 returns plans for all v1-repaired records."""
        doc1 = _csv_buy_v1repaired("v2-p1a", trade_eur="1825.00", fees_eur="7.50")
        doc2 = _csv_buy_v1repaired("v2-p1b", trade_eur="500.00", fees_eur="5.00")
        container = _FakeContainer([doc1, doc2])
        plans = run_audit_v2(container)
        ids = {p["id"] for p in plans}
        assert "v2-p1a" in ids
        assert "v2-p1b" in ids

    def test_rbl_v2_102_audit_excludes_fee_free(self):
        """run_audit_v2 does not flag fee-free records."""
        doc_fee  = _csv_buy_v1repaired("v2-p2a", trade_eur="1825.00", fees_eur="7.50")
        doc_free = _csv_buy("v2-p2b", gross_eur="0", fees_eur="0",
                            cost_basis_status="ZERO_COST",
                            source_total="0", source_commission="0", qty="50")
        doc_free["_repair_buy_fields_v1"] = "20260908T075000Z"
        container = _FakeContainer([doc_fee, doc_free])
        plans = run_audit_v2(container)
        ids = {p["id"] for p in plans}
        assert "v2-p2b" not in ids  # fee-free excluded
        assert "v2-p2a" in ids

    def test_rbl_v2_103_verify_fails_when_candidates_remain(self):
        """run_verify_v2 returns 3 when candidates remain."""
        doc = _csv_buy_v1repaired("v2-p3a")
        container = _FakeContainer([doc])
        result = run_verify_v2(container)
        assert result == 3

    def test_rbl_v2_104_verify_passes_when_no_candidates(self):
        """run_verify_v2 returns 0 when zero v2 candidates (all repaired/fee-free)."""
        doc = _csv_buy_v1repaired("v2-p4a")
        plan = _analyse_record_v2(doc)
        assert plan is not None
        patched = _apply_repair_v2(doc, plan)
        container = _FakeContainer([patched])
        result = run_verify_v2(container)
        assert result == 0


# ---------------------------------------------------------------------------
# §V2.11 — ADM scenario (MIG-1 / MIG-9 / FIFO target values)
# ---------------------------------------------------------------------------

@_skip
class TestV2ADMScenario:
    """RBL-V2-11x: ADM 3-lot scenario verifying exact FIFO target values.

    From Reuben's audit report and backup analysis:
      Lot 1 (2019-08-06): 85 shares, pre-mig gross=2776.03, fees=31.96
        → post-v1: gross=2807.99, net=2776.03
        → after v2: gross=2776.03 (trade), net=2807.99 (total outflow)
        → net unit cost = 2807.99/85 = 33.0352/share

      Lot 2 (2024-01-23): 15 shares, pre-mig gross=731.44, fees=7.17
        → post-v1: gross=738.61, net=731.44
        → after v2: gross=731.44, net=738.61
        → net unit cost = 738.61/15 = 49.2407/share

      Lot 3 (2025-08-05): 10 shares, pre-mig gross=484.86, fees=5.52
        → post-v1: gross=490.38, net=484.86
        → after v2: gross=484.86, net=490.38
        → net unit cost = 490.38/10 = 49.038/share

    FIFO sell 100: consumes lots 1+2 entirely, leaving lot 3 intact.
    Remaining 10 shares at net unit cost 49.038.
    """

    def _adm_lot(self, mid, *, trade_eur, fees_eur, qty, trade_date):
        """Build an ADM post-v1 BUY lot (4ca553e shape)."""
        return _csv_buy_v1repaired(
            mid,
            trade_eur=trade_eur,
            fees_eur=fees_eur,
            security_id="XNYS:ADM",
            trade_date=trade_date,
        )

    def test_rbl_v2_111_adm_lot1_detected(self):
        """ADM lot 1 (85 shares): v2 swap detected correctly."""
        lot1 = self._adm_lot("adm-l1", trade_eur="2776.03", fees_eur="31.96",
                             qty="85", trade_date="2019-08-06")
        plan = _analyse_record_v2(lot1)
        assert plan is not None
        assert plan["action"] == "swap"
        assert _d(plan["new_gross_eur"]) == _d("2776.03")
        assert _d(plan["new_net_eur"])   == _d("2807.99")

    def test_rbl_v2_112_adm_lot2_detected(self):
        """ADM lot 2 (15 shares): v2 swap detected correctly."""
        lot2 = self._adm_lot("adm-l2", trade_eur="731.44", fees_eur="7.17",
                             qty="15", trade_date="2024-01-23")
        plan = _analyse_record_v2(lot2)
        assert plan is not None
        assert _d(plan["new_gross_eur"]) == _d("731.44")
        assert _d(plan["new_net_eur"])   == _d("738.61")

    def test_rbl_v2_113_adm_lot3_detected(self):
        """ADM lot 3 (10 shares): v2 swap detected correctly."""
        lot3 = self._adm_lot("adm-l3", trade_eur="484.86", fees_eur="5.52",
                             qty="10", trade_date="2025-08-05")
        plan = _analyse_record_v2(lot3)
        assert plan is not None
        assert _d(plan["new_gross_eur"]) == _d("484.86")
        assert _d(plan["new_net_eur"])   == _d("490.38")

    def test_rbl_v2_114_adm_lot3_net_unit_cost(self):
        """ADM lot 3 net unit cost = 490.38/10 = 49.038 (FIFO residual per directive)."""
        lot3 = self._adm_lot("adm-l3b", trade_eur="484.86", fees_eur="5.52",
                             qty="10", trade_date="2025-08-05")
        plan = _analyse_record_v2(lot3)
        assert plan is not None
        net_total = _d(plan["new_net_eur"])   # 490.38
        qty       = _d("10")
        unit_cost = net_total / qty
        # 490.38 / 10 = 49.038
        assert abs(unit_cost - _d("49.038")) < _d("0.001"), (
            f"ADM lot 3 net unit cost should be 49.038, got {unit_cost}"
        )

    def test_rbl_v2_115_adm_all_lots_idempotent_after_apply(self):
        """After v2 apply on all 3 ADM lots, second audit finds 0 candidates."""
        lots = [
            self._adm_lot("adm-i1", trade_eur="2776.03", fees_eur="31.96",
                          qty="85", trade_date="2019-08-06"),
            self._adm_lot("adm-i2", trade_eur="731.44", fees_eur="7.17",
                          qty="15", trade_date="2024-01-23"),
            self._adm_lot("adm-i3", trade_eur="484.86", fees_eur="5.52",
                          qty="10", trade_date="2025-08-05"),
        ]
        patched_lots = []
        for lot in lots:
            plan = _analyse_record_v2(lot)
            assert plan is not None
            patched_lots.append(_apply_repair_v2(lot, plan))

        # Second pass: all should be skipped
        for patched in patched_lots:
            assert _analyse_record_v2(patched) is None, (
                f"Second run on {patched['id']} must return None (idempotent)"
            )
