#!/usr/bin/env python3
"""Audit and repair BUY ledger field inversion + INCOMPLETE→ZERO_COST reclassification.

Two migration generations are implemented in this file:

  V1 (commit 4ca553e, 2026-09-08)  — danny-scrip-zero-cost-and-buy-import-contract.md §3
  V2 (FIFO/net release)             — danny-fifo-net-accounting-contract.md §1

===========================================================================
V1 — Background (retained for auditability)
===========================================================================
BUY movements imported from the purchases CSV before the gross/net fix have
inverted field semantics:

  stored gross.eur_amount  = CSV "Total (€)"  = net consideration (price × qty)
  stored net.eur_amount    = gross - commission = meaningless
  stored fees.total_eur    = commission         = correct

The holdings engine previously compensated with ``cost = gross + commission``,
producing the correct total outflow *by accident*.  After the engine fix
(``cost = gross_eur`` — gross now already includes commission), legacy records
must be corrected to avoid under-counting.

  Correct gross.eur_amount = old_gross + fees.total_eur   (true total outflow)
  Correct net.eur_amount   = old_gross                    (CSV "Total" = net)

Additionally, zero-price BUY rows imported via CSV were stored with
``cost_basis_status = "INCOMPLETE"`` — correct reclassification is
``"ZERO_COST"``, which causes the holdings engine to enter these shares in
the pool at cost 0, naturally diluting avg_cost_basis_eur.

Detection criteria (only CSV-imported active BUY records)
---------------------------------------------------------
1. doc_type == "ledger_txn"
2. txn_type == "BUY"
3. import_source == "csv_import"
4. correction_status in ("ACTIVE", absent) — skip SUPERSEDED/VOIDED
5. _repair_buy_fields_v1 marker absent — skip already-repaired records

Per-record repair decisions
---------------------------
A. Gross/net swap: detected via source_row cross-validation (fail-closed).
   Parse source_row["Total (€)"] (aliases: total/comision/commission/fees)
   and source_row["Comisión"] to confirm inversion:
   - |stored_gross − source_total| < 0.01: confirmed inversion → swap
   - |stored_gross − (source_total + source_commission)| < 0.01: already correct → skip
   - source_row absent or either field unparseable: fail closed → no swap

B. Status reclassification: cost_basis_status == "INCOMPLETE" AND
   source_total == 0 (or implied price == 0) AND quantity > 0
   → reclassify to ZERO_COST.

Manual BUY candidates
---------------------
Manual BUY records (import_source == "manual") have no source_row. The
arithmetic invariant net = gross − fees holds for BOTH old (inverted) and
new (correct) format and cannot distinguish them. These records are REPORTED
in the audit as candidates requiring operator confirmation; they are never
auto-applied. Use --apply-manual-ids ID1,ID2,... after verification.

Safe-skip rules (fail closed)
------------------------------
- _repair_buy_fields_v1 marker present: already repaired — unconditional skip.
- source_row absent or total field unparseable (csv_import): swap skipped.
- source values do not match either old or new semantics: ambiguous → skip.
- fees.total_eur == "0.00": no swap needed (gross == net).
- correction_status in ("SUPERSEDED", "VOIDED"): archived; do not touch.

===========================================================================
V2 — Background (danny-fifo-net-accounting-contract.md §1)
===========================================================================
The authoritative net-centric accounting convention defines for BUY:

  gross = trade consideration (price × qty, BEFORE commission)
  net   = gross + fees = total cash outflow (AFTER commission)

Commit 4ca553e introduced the opposite BUY convention:
  gross = trade + commission (total outflow)
  net   = trade consideration

V2 corrects all active BUY records to the authoritative shape.  Critically
the NUMERIC COST is unchanged (current gross_eur == target net_eur == total
outflow), so holdings engine output does not change until the engine is
updated to read net_eur instead of gross_eur.

Detection (all active BUY records — csv_import and manual):
-----------------------------------------------------------
Case A — _repair_buy_fields_v1 marker present, fees > 0:
    Record is in 4ca553e shape: gross = trade+fees, net = trade.
    Source-row cross-validation: net_eur ≈ source_total (trade value) → swap.
    If source_row absent: arithmetic check (gross ≈ net+fees) used as fallback
    since v1 already validated these records; fail-closed if inconsistent.

Case B — no v1/v2 marker, fees > 0:
    May be 4ca553e shape (net ≈ source_total) or old pre-v1 shape
    (gross ≈ source_total, net ≈ gross−fees) or already-correct new-directive
    shape (gross ≈ source_total, net ≈ source_total+fees).
    Source-row is required to distinguish; fail-closed if absent.

Fee-free / ZERO_COST (fees == 0):
    gross == net == 0 or gross == net. Already correct. No field change.
    Policy: these records are skipped by v2 (no marker written). Fee-free
    records are self-consistent under both conventions.

Already-correct records (gross ≈ source_total, net ≈ source_total+fees):
    Skipped; no writes.

Ambiguous records (source row missing for Case B, or values match neither
    shape): fail-closed; not flagged; operator must investigate manually.

Idempotency: _repair_buy_fields_v2 marker present → unconditional skip.

V2 CLI Modes
------------
  --audit-v2             Read-only. Print per-record diff report.
  --apply-v2             Mandatory backup → ETag-gated patch for all v2 candidates.
  --verify-v2            Post-apply check; expects 0 candidates; exits 3 if any remain.
  --restore-v2 FILE      Restore from a v2 backup JSON (ETag/CAS-gated).

Usage::

    python -m scripts.repair_buy_ledger_fields --audit-v2 --database stock-options-manager --portfolio-container portfolio
    python -m scripts.repair_buy_ledger_fields --apply-v2 --database stock-options-manager --portfolio-container portfolio
    python -m scripts.repair_buy_ledger_fields --verify-v2 --database stock-options-manager --portfolio-container portfolio
    python -m scripts.repair_buy_ledger_fields --restore-v2 /path/to/backup.json --database stock-options-manager --portfolio-container portfolio

V1 Modes (retained)
-------------------
  (default) --audit  Read-only. Prints csv auto-repair + manual candidate report.
  --apply            Mandatory backup → ETag-gated patch for csv_import records.
  --apply-manual-ids ID1,ID2,...  Also apply confirmed manual records (with --apply).
  --restore FILE     Restore original values from a v1 backup JSON file.

Exit codes:
    0  Clean (audit/verify with zero candidates, or apply with all records processed)
    1  Bad CLI arguments
    2  Backup failed or Cosmos connection error
    3  One or more records failed to patch (partial apply — run --restore-v2)
       OR verify found remaining candidates after apply
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Backup directory relative to this script's location
_SCRIPT_DIR = Path(__file__).parent
_BACKUP_DIR = _SCRIPT_DIR / "migration_backups"

_COSMOS_SYSTEM_KEYS = frozenset({"_rid", "_self", "_etag", "_attachments", "_ts"})

# Source-row column aliases matching purchases.py _PURCHASES_HEADER_ALIASES (normalized).
# Position 5 = total/net consideration; position 6 = commission/fees.
_SOURCE_TOTAL_ALIASES: frozenset = frozenset(
    {"total (€)", "total (eur)", "total", "total cost", "trade value"}
)
_SOURCE_COMMISSION_ALIASES: frozenset = frozenset(
    {"comision", "commission", "fees"}
)

_MATCH_TOLERANCE = Decimal("0.01")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _d(v: Any) -> Decimal:
    """Safe Decimal coercion; returns Decimal('0') on failure."""
    if isinstance(v, Decimal):
        return v
    if v is None:
        return Decimal("0")
    try:
        return Decimal(str(v).strip())
    except InvalidOperation:
        return Decimal("0")


def _clean(doc: dict) -> dict:
    """Strip Cosmos system keys before upsert/replace."""
    return {k: v for k, v in doc.items() if k not in _COSMOS_SYSTEM_KEYS}


def _sha256(entries: List[dict]) -> str:
    sorted_entries = sorted(entries, key=lambda e: e.get("id", ""))
    return hashlib.sha256(
        json.dumps(sorted_entries, sort_keys=True).encode()
    ).hexdigest()


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _normalize_header(h: str) -> str:
    """Lower-case, strip, remove combining diacritics — identical to purchases.py."""
    nfkd = unicodedata.normalize("NFKD", h)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def _parse_spanish_decimal_str(raw: str) -> Optional[Decimal]:
    """Parse a Spanish-locale decimal string.

    "1.825,00" → Decimal("1825.00"); "0" → Decimal("0"); "" / "N/A" → None.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.upper() in {"N/A", "NA", "NONE", "-", "—"}:
        return None
    s = s.replace(".", "")      # dots are thousands separators in Spanish locale
    s = s.replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _look_up_source_value(
    source_row: dict, aliases: frozenset
) -> Optional[Decimal]:
    """Find a value in source_row by matching normalized header against aliases.

    Returns parsed Decimal or None if not found / unparseable.
    """
    for key, raw_value in source_row.items():
        if _normalize_header(key) in aliases:
            return _parse_spanish_decimal_str(str(raw_value))
    return None


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _analyse_record(doc: dict) -> Optional[Dict[str, Any]]:
    """Return a repair plan dict for a csv_import BUY record, or None if no fix needed.

    Detection is fail-closed: ambiguous records are skipped (not flagged).

    Idempotency guard
    -----------------
    The ``_repair_buy_fields_v1`` marker is written during apply.  Any record
    that already carries the marker is unconditionally skipped — a second audit/
    apply run will therefore produce zero candidates among repaired records.

    Source-row cross-validation
    ---------------------------
    For CSV-imported records, the original ``source_row`` dict preserves the
    raw cell values.  We parse the "Total (€)" alias (net consideration) and
    "Comisión" alias (commission) and cross-check against stored gross:

    - If ``|stored_gross − source_total| < 0.01``: gross stores net (old
      semantics) → needs_swap = True.
    - If ``|stored_gross − (source_total + source_commission)| < 0.01``:
      gross already includes commission (already correct) → skip.
    - If source_row absent or either value unparseable: fail closed for the
      swap decision; status reclassification may still proceed independently.

    This replaces the old tautological check ``gross < gross + fees`` which
    would erroneously flag already-repaired records on a second run.
    """
    doc_id = doc.get("id", "?")

    # ── Idempotency: unconditionally skip already-repaired records ──────────
    if doc.get("_repair_buy_fields_v1"):
        return None

    # ── Base criteria ───────────────────────────────────────────────────────
    if doc.get("doc_type") != "ledger_txn":
        return None
    if doc.get("txn_type") != "BUY":
        return None
    if doc.get("import_source") != "csv_import":
        return None
    cs = doc.get("correction_status")
    if cs in ("SUPERSEDED", "VOIDED"):
        return None

    gross_block = doc.get("gross") or {}
    fees_block  = doc.get("fees") or {}
    net_block   = doc.get("net") or {}
    gross_eur        = _d(gross_block.get("eur_amount"))
    gross_amt        = _d(gross_block.get("amount"))
    fees_eur         = _d(fees_block.get("total_eur"))
    net_eur          = _d(net_block.get("eur_amount"))
    net_amt          = _d(net_block.get("amount"))
    currency         = gross_block.get("currency", "EUR")
    cost_basis_status = doc.get("cost_basis_status", "COMPLETE")
    qty              = _d(doc.get("quantity") or "0")

    needs_swap   = False
    needs_status = False
    reason_parts: List[str] = []

    # ── A. Gross/net swap detection via source_row cross-validation ─────────
    if fees_eur > Decimal("0"):
        source_row = doc.get("source_row") or {}
        source_total      = _look_up_source_value(source_row, _SOURCE_TOTAL_ALIASES)
        source_commission = _look_up_source_value(source_row, _SOURCE_COMMISSION_ALIASES) or Decimal("0")

        if source_total is not None:
            if abs(gross_eur - source_total) < _MATCH_TOLERANCE:
                # stored_gross ≈ source_total (net) → confirmed inversion, needs swap
                needs_swap = True
                reason_parts.append(
                    f"gross/net inverted (source_row): stored_gross={gross_eur} "
                    f"≈ source_total={source_total}; true_gross={source_total + source_commission}"
                )
            elif abs(gross_eur - (source_total + source_commission)) < _MATCH_TOLERANCE:
                # stored_gross ≈ source_total + commission → already correct
                logger.info(
                    "SKIP %s: gross_eur (%s) already matches source_total+commission (%s) "
                    "— already repaired or imported after the fix",
                    doc_id, gross_eur, source_total + source_commission,
                )
                # Only status reclassification might still be needed (fall through)
            else:
                # Neither match — ambiguous; fail closed for swap
                logger.warning(
                    "SKIP %s swap: gross_eur=%s source_total=%s source_commission=%s — "
                    "values do not match either old or new semantics; skipping swap",
                    doc_id, gross_eur, source_total, source_commission,
                )
        else:
            # source_row absent or total column unparseable — fail closed for swap
            logger.warning(
                "SKIP %s swap: source_row absent or total column unparseable — "
                "cannot verify inversion; skipping swap (only status fix may apply)",
                doc_id,
            )

    # ── B. Status reclassification needed? ──────────────────────────────────
    if cost_basis_status == "INCOMPLETE":
        # Confirm zero implied price from source_row when available, else from stored gross
        source_row = doc.get("source_row") or {}
        source_total = _look_up_source_value(source_row, _SOURCE_TOTAL_ALIASES)
        confirmed_zero = False
        if source_total is not None:
            confirmed_zero = (source_total == Decimal("0") and qty > Decimal("0"))
        elif qty > Decimal("0"):
            # Fallback: zero gross on record implies zero price
            implied_price = gross_eur / qty
            confirmed_zero = (implied_price == Decimal("0"))

        if confirmed_zero:
            needs_status = True
            reason_parts.append(
                "cost_basis_status INCOMPLETE → ZERO_COST (zero-price row confirmed)"
            )
        else:
            # Non-zero source total but INCOMPLETE — genuine unknown cost or
            # unexpected state; fail closed.
            logger.warning(
                "SKIP %s status: cost_basis_status=INCOMPLETE but price appears non-zero "
                "(gross=%s, qty=%s) — cannot safely reclassify",
                doc_id, gross_eur, qty,
            )
            # If swap is also needed, still apply swap; just skip status change
            if not needs_swap:
                return None

    if not needs_swap and not needs_status:
        return None  # Nothing to do

    # ── Compute proposed new field values ────────────────────────────────────
    if needs_swap:
        new_gross_eur = gross_eur + fees_eur
        new_net_eur   = gross_eur       # old gross IS the net consideration
        # Native-currency amount: apply same delta (assumes EUR-denominated imports)
        new_gross_amt = gross_amt + fees_eur
        new_net_amt   = gross_amt
    else:
        new_gross_eur = gross_eur
        new_net_eur   = net_eur
        new_gross_amt = gross_amt
        new_net_amt   = net_amt

    new_status = "ZERO_COST" if needs_status else cost_basis_status

    return {
        "id": doc_id,
        "account_id": doc.get("account_id", ""),
        "security_id": doc.get("security_id", ""),
        "trade_date": doc.get("trade_date", ""),
        "quantity": str(qty),
        "currency": currency,
        "import_source": "csv_import",
        "candidate_only": False,            # auto-apply eligible
        "needs_swap": needs_swap,
        "needs_status": needs_status,
        "reason": "; ".join(reason_parts),
        # Current values (for diff reporting / restore verification)
        "current_gross_eur": str(gross_eur),
        "current_net_eur": str(net_eur),
        "current_fees_eur": str(fees_eur),
        "current_status": cost_basis_status,
        # Proposed values
        "new_gross_eur": str(new_gross_eur),
        "new_net_eur": str(new_net_eur),
        "new_gross_amt": str(new_gross_amt),
        "new_net_amt": str(new_net_amt),
        "new_status": new_status,
    }


def _analyse_manual_candidate(doc: dict) -> Optional[Dict[str, Any]]:
    """Detect a manually-created BUY movement whose gross/net may be inverted.

    Manual BUY records have no ``source_row``, so ground-truth cross-validation
    is impossible.  The arithmetic invariant ``net ≈ gross − fees`` holds for
    BOTH the old (inverted) and new (correct) format because the backend always
    derives net from gross:

        old:  gross = trade_value,                net = trade_value − fees
        new:  gross = trade_value + fees (true),  net = trade_value

    Both satisfy net = gross − fees.  Therefore this function can only REPORT
    candidates — it cannot auto-apply.  The operator must confirm each record
    using ``--apply-manual-ids``.

    A record is a candidate when ALL of:
    - import_source == "manual" (not csv_import)
    - txn_type == "BUY"
    - correction_status ACTIVE (not SUPERSEDED/VOIDED)
    - fees_eur > 0 (zero-fee records are self-consistent regardless of format)
    - ``_repair_buy_fields_v1`` marker absent (not already repaired)
    - ``|net_eur − (gross_eur − fees_eur)| < 0.01`` (arithmetic consistent
      with the net = gross − fees invariant — necessary but not sufficient)
    """
    doc_id = doc.get("id", "?")

    if doc.get("_repair_buy_fields_v1"):
        return None
    if doc.get("doc_type") != "ledger_txn":
        return None
    if doc.get("txn_type") != "BUY":
        return None
    if doc.get("import_source") != "manual":
        return None
    cs = doc.get("correction_status")
    if cs in ("SUPERSEDED", "VOIDED"):
        return None

    gross_block  = doc.get("gross") or {}
    fees_block   = doc.get("fees") or {}
    net_block    = doc.get("net") or {}
    gross_eur    = _d(gross_block.get("eur_amount"))
    gross_amt    = _d(gross_block.get("amount"))
    fees_eur     = _d(fees_block.get("total_eur"))
    net_eur      = _d(net_block.get("eur_amount"))
    net_amt      = _d(net_block.get("amount"))
    currency     = gross_block.get("currency", "EUR")
    cost_basis_status = doc.get("cost_basis_status", "COMPLETE")
    qty          = _d(doc.get("quantity") or "0")

    # Only records with non-zero fees can have a gross/net inversion that matters
    if fees_eur <= Decimal("0"):
        return None

    # Arithmetic invariant: net = gross - fees (necessary for candidate)
    if abs(net_eur - (gross_eur - fees_eur)) >= _MATCH_TOLERANCE:
        # net doesn't even follow the backend's derivation — unexpected shape
        logger.warning(
            "SKIP manual %s: net_eur=%s does not match gross_eur-fees_eur=%s "
            "— unexpected shape, cannot classify",
            doc_id, net_eur, gross_eur - fees_eur,
        )
        return None

    # Proposed correction (for reporting only — not auto-applied)
    new_gross_eur = gross_eur + fees_eur    # true gross = old_gross + fees
    new_net_eur   = gross_eur               # net consideration = old_gross
    new_gross_amt = gross_amt + fees_eur
    new_net_amt   = gross_amt

    return {
        "id": doc_id,
        "account_id": doc.get("account_id", ""),
        "security_id": doc.get("security_id", ""),
        "trade_date": doc.get("trade_date", ""),
        "quantity": str(qty),
        "currency": currency,
        "import_source": "manual",
        "candidate_only": True,             # requires explicit operator approval
        "needs_swap": True,
        "needs_status": False,              # manual records don't have ZERO_COST scrip
        "reason": (
            f"manual BUY with fees: net={net_eur} ≈ gross({gross_eur}) − fees({fees_eur}) — "
            "likely inverted (frontend sent trade_value as gross before fix); "
            "REQUIRES OPERATOR CONFIRMATION — cannot auto-distinguish old from new records"
        ),
        "current_gross_eur": str(gross_eur),
        "current_net_eur": str(net_eur),
        "current_fees_eur": str(fees_eur),
        "current_status": cost_basis_status,
        "new_gross_eur": str(new_gross_eur),
        "new_net_eur": str(new_net_eur),
        "new_gross_amt": str(new_gross_amt),
        "new_net_amt": str(new_net_amt),
        "new_status": cost_basis_status,    # status unchanged for manual records
    }


def _apply_repair(doc: dict, plan: Dict[str, Any]) -> dict:
    """Return a new doc dict with corrected fields applied (does not mutate input)."""
    import copy
    patched = copy.deepcopy(doc)

    if plan["needs_swap"]:
        patched["gross"]["eur_amount"] = plan["new_gross_eur"]
        patched["gross"]["amount"] = plan["new_gross_amt"]
        patched["net"]["eur_amount"] = plan["new_net_eur"]
        patched["net"]["amount"] = plan["new_net_amt"]

    if plan["needs_status"]:
        patched["cost_basis_status"] = plan["new_status"]

    patched["_repair_buy_fields_v1"] = _now_utc()
    return patched


# ---------------------------------------------------------------------------
# Cosmos helpers
# ---------------------------------------------------------------------------

def _etag_replace(container, doc_id: str, partition_key: str, body: dict, etag: str) -> bool:
    """ETag-gated replace_item. Returns True on success, False on CAS conflict."""
    from azure.core import MatchConditions
    from azure.cosmos.exceptions import CosmosHttpResponseError
    try:
        container.replace_item(
            item=doc_id,
            body=_clean(body),
            etag=etag,
            match_condition=MatchConditions.IfNotModified,
        )
        return True
    except CosmosHttpResponseError as exc:
        if exc.status_code in (409, 412):
            return False
        raise


def _get_all_buy_candidates(container) -> List[Tuple[dict, str]]:
    """Query all active csv_import BUY ledger_txns. Returns (doc, etag) pairs."""
    query = (
        "SELECT * FROM c "
        "WHERE c.doc_type = 'ledger_txn' "
        "AND c.txn_type = 'BUY' "
        "AND c.import_source = 'csv_import'"
    )
    results = []
    for item in container.query_items(query=query, enable_cross_partition_query=True):
        etag = item.get("_etag", "")
        results.append((item, etag))
    return results


def _get_all_manual_buy_candidates(container) -> List[Tuple[dict, str]]:
    """Query all active manual BUY ledger_txns (for candidate reporting only)."""
    query = (
        "SELECT * FROM c "
        "WHERE c.doc_type = 'ledger_txn' "
        "AND c.txn_type = 'BUY' "
        "AND c.import_source = 'manual'"
    )
    results = []
    for item in container.query_items(query=query, enable_cross_partition_query=True):
        etag = item.get("_etag", "")
        results.append((item, etag))
    return results


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def run_audit(container) -> List[Dict[str, Any]]:
    """Read-only audit.

    Returns list of ALL repair plans including manual candidates.
    Auto-apply eligible plans have ``candidate_only=False``.
    Manual candidates have ``candidate_only=True`` — they are reported but
    never auto-applied; they require explicit operator confirmation.
    """
    csv_candidates = _get_all_buy_candidates(container)
    logger.info("Found %d csv_import BUY records", len(csv_candidates))

    manual_candidates = _get_all_manual_buy_candidates(container)
    logger.info("Found %d manual BUY records", len(manual_candidates))

    plans: List[Dict[str, Any]] = []
    for doc, _ in csv_candidates:
        plan = _analyse_record(doc)
        if plan:
            plans.append(plan)

    manual_plans: List[Dict[str, Any]] = []
    for doc, _ in manual_candidates:
        plan = _analyse_manual_candidate(doc)
        if plan:
            manual_plans.append(plan)

    auto_plans = [p for p in plans if not p.get("candidate_only")]
    needs_swap   = sum(1 for p in auto_plans if p["needs_swap"])
    needs_status = sum(1 for p in auto_plans if p["needs_status"])
    both         = sum(1 for p in auto_plans if p["needs_swap"] and p["needs_status"])

    print(f"\n{'='*70}")
    print(f"AUDIT REPORT — BUY ledger field repair")
    print(f"{'='*70}")
    print(f"CSV import BUY records scanned:       {len(csv_candidates)}")
    print(f"Records needing auto-repair:          {len(auto_plans)}")
    print(f"  - gross/net swap needed:            {needs_swap}")
    print(f"  - status reclassification needed:   {needs_status}")
    print(f"  - both:                             {both}")
    print()

    for p in auto_plans:
        print(f"  [{p['id']}]  {p['security_id']}  {p['trade_date']}")
        print(f"    Reason: {p['reason']}")
        if p["needs_swap"]:
            print(
                f"    gross: {p['current_gross_eur']} → {p['new_gross_eur']}  "
                f"net: {p['current_net_eur']} → {p['new_net_eur']}  "
                f"fees: {p['current_fees_eur']} (unchanged)"
            )
        if p["needs_status"]:
            print(f"    status: {p['current_status']} → {p['new_status']}")
        print()

    if manual_plans:
        print(f"{'='*70}")
        print(f"MANUAL BUY CANDIDATES — require operator confirmation ({len(manual_plans)} records)")
        print("These records cannot be auto-distinguished from correctly-created records.")
        print("Verify each against the original trade confirmation before approving.")
        print()
        for p in manual_plans:
            print(f"  [MANUAL] [{p['id']}]  {p['security_id']}  {p['trade_date']}")
            print(
                f"    current: gross={p['current_gross_eur']} net={p['current_net_eur']} "
                f"fees={p['current_fees_eur']}"
            )
            print(
                f"    proposed: gross={p['new_gross_eur']} net={p['new_net_eur']} "
                f"(if inverted)"
            )
            print(f"    Note: {p['reason']}")
            print()
        print("To apply manual records: --apply-manual-ids ID1,ID2,...")
    else:
        print("No manual BUY candidates found with non-zero fees.")

    print(f"{'='*70}")
    print("Run with --apply to execute csv_import repairs (mandatory backup taken first).")
    return plans + manual_plans


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

def run_backup(container, plans: List[Dict[str, Any]], backup_path: Optional[Path] = None) -> Path:
    """Export all records targeted by auto-repair plans to a JSON backup file.

    Manual candidates (candidate_only=True) are excluded — they are not written.
    Raises SystemExit(2) if backup fails.
    """
    auto_plans = [p for p in plans if not p.get("candidate_only")]
    if not auto_plans:
        logger.info("No auto-repair records to backup (nothing to apply).")
        return Path(os.devnull)

    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if backup_path is None:
        backup_path = _BACKUP_DIR / f"buy_ledger_repair_{_now_utc()}.json"

    # Re-read each targeted document to get fresh ETags for the backup
    docs_for_backup = []
    for plan in auto_plans:
        try:
            doc = container.read_item(item=plan["id"], partition_key=plan["account_id"])
            docs_for_backup.append(dict(doc))
        except Exception as exc:
            logger.error("Backup: could not read %s: %s", plan["id"], exc)
            print(f"\nERROR: Backup failed for {plan['id']}: {exc}", file=sys.stderr)
            sys.exit(2)

    checksum = _sha256(docs_for_backup)
    payload = {
        "created_at": _now_utc(),
        "record_count": len(docs_for_backup),
        "sha256": checksum,
        "documents": docs_for_backup,
    }

    with open(backup_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)

    # Verify written file matches
    with open(backup_path, encoding="utf-8") as fh:
        written = json.load(fh)
    verify_checksum = _sha256(written["documents"])
    if verify_checksum != checksum:
        logger.error("Backup checksum mismatch! File is corrupt — aborting.")
        sys.exit(2)

    logger.info("Backup written: %s  (%d docs, SHA-256: %s)", backup_path, len(docs_for_backup), checksum)
    print(f"\nBackup: {backup_path}")
    print(f"  Records: {len(docs_for_backup)}  SHA-256: {checksum}")
    return backup_path


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def run_apply(container, plans: List[Dict[str, Any]], manual_approved_ids: Optional[List[str]] = None) -> int:
    """ETag-gated patch for each auto-repair eligible plan. Returns number of failures.

    Manual candidates (``candidate_only=True``) are always skipped here.
    To apply a specific manual record after operator confirmation, pass its ID
    in ``manual_approved_ids``.
    """
    auto_plans = [p for p in plans if not p.get("candidate_only")]
    if manual_approved_ids:
        approved_set = set(manual_approved_ids)
        manual_approved = [p for p in plans if p.get("candidate_only") and p["id"] in approved_set]
        auto_plans = auto_plans + manual_approved
        unapproved = approved_set - {p["id"] for p in manual_approved}
        if unapproved:
            print(f"WARNING: --apply-manual-ids included unknown IDs: {unapproved}", file=sys.stderr)

    if not auto_plans:
        print("Nothing to apply.")
        return 0

    print(f"\nApplying {len(auto_plans)} record(s)...")
    failures = 0

    for plan in auto_plans:
        doc_id = plan["id"]
        acct = plan["account_id"]
        is_manual = plan.get("candidate_only", False)
        try:
            # Fresh read for current ETag
            doc = container.read_item(item=doc_id, partition_key=acct)
            etag = doc.get("_etag", "")

            # Re-analyse in case data changed between audit and apply
            if is_manual:
                fresh_plan = _analyse_manual_candidate(doc)
            else:
                fresh_plan = _analyse_record(doc)

            if fresh_plan is None:
                logger.info("  SKIP %s — already correct or changed since audit", doc_id)
                continue

            patched = _apply_repair(doc, fresh_plan)
            ok = _etag_replace(container, doc_id, acct, patched, etag)
            if ok:
                print(f"  OK   {doc_id}  [{fresh_plan['reason']}]")
            else:
                print(f"  CONFLICT {doc_id} — ETag changed; retry to reapply")
                failures += 1
        except Exception as exc:
            logger.error("  FAIL %s: %s", doc_id, exc)
            failures += 1

    print(f"\nApply complete: {len(auto_plans) - failures} patched, {failures} failed.")
    return failures


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def run_restore(container, backup_file: Path) -> int:
    """Restore original values from a backup JSON. Returns number of failures."""
    if not backup_file.exists():
        logger.error("Backup file not found: %s", backup_file)
        sys.exit(2)

    with open(backup_file, encoding="utf-8") as fh:
        payload = json.load(fh)

    docs = payload.get("documents", [])
    stored_checksum = payload.get("sha256", "")
    actual_checksum = _sha256(docs)
    if actual_checksum != stored_checksum:
        logger.error(
            "Backup checksum mismatch: stored=%s actual=%s — aborting restore",
            stored_checksum, actual_checksum,
        )
        sys.exit(2)

    print(f"\nRestoring {len(docs)} record(s) from {backup_file}...")
    failures = 0

    for doc in docs:
        doc_id = doc.get("id", "?")
        acct = doc.get("account_id", "")
        try:
            # Read current ETag for CAS on restore
            current = container.read_item(item=doc_id, partition_key=acct)
            etag = current.get("_etag", "")
            ok = _etag_replace(container, doc_id, acct, doc, etag)
            if ok:
                print(f"  RESTORED {doc_id}")
            else:
                print(f"  CONFLICT {doc_id} — ETag changed during restore; retry")
                failures += 1
        except Exception as exc:
            logger.error("  FAIL restore %s: %s", doc_id, exc)
            failures += 1

    print(f"\nRestore complete: {len(docs) - failures} restored, {failures} failed.")
    return failures


# ===========================================================================
# V2 Migration — net-centric BUY accounting
# danny-fifo-net-accounting-contract.md §1
# ===========================================================================

# ---------------------------------------------------------------------------
# V2 Detection
# ---------------------------------------------------------------------------

def _analyse_record_v2(doc: dict) -> Optional[Dict[str, Any]]:
    """Return a v2 repair plan for a BUY record, or None if no fix needed / fail-closed.

    Target state (new directive):
        gross.eur_amount = trade consideration (price × qty, pre-commission)
        net.eur_amount   = gross + fees = total cash outflow

    Case A — _repair_buy_fields_v1 present, fees > 0 (4ca553e shape):
        Stored as gross = trade+fees, net = trade.
        Cross-validation: net_eur ≈ source_total (trade value) → swap gross↔net.
        Fallback when source_row absent: arithmetic check (gross ≈ net+fees) is
        sufficient since v1 already validated source evidence; fail-closed if
        arithmetic is inconsistent.

    Case B — no v1/v2 marker, fees > 0:
        May be 4ca553e shape (post-fix imports) or old pre-v1 shape (unrepaired).
        Requires source_row to distinguish safely; fail-closed if absent.
        - net_eur ≈ source_total: 4ca553e shape → swap gross↔net
        - gross_eur ≈ source_total AND net_eur ≈ source_total+fees: already correct → skip
        - gross_eur ≈ source_total (net wrong): old/partial shape → set net = gross+fees
        - neither: ambiguous → fail-closed

    Fee-free (fees == 0):
        Already correct under new directive (gross == net, no commission component).
        Policy: skipped silently — no v2 marker written. Self-consistent in both conventions.

    Idempotency: _repair_buy_fields_v2 present → unconditional skip.
    """
    doc_id = doc.get("id", "?")

    # Idempotency: skip already v2-repaired records unconditionally.
    if doc.get("_repair_buy_fields_v2"):
        return None

    # Must be an active BUY ledger_txn.
    if doc.get("doc_type") != "ledger_txn":
        return None
    if doc.get("txn_type") != "BUY":
        return None
    cs = doc.get("correction_status")
    if cs in ("SUPERSEDED", "VOIDED"):
        return None

    gross_block = doc.get("gross") or {}
    fees_block  = doc.get("fees") or {}
    net_block   = doc.get("net") or {}
    gross_eur   = _d(gross_block.get("eur_amount"))
    gross_amt   = _d(gross_block.get("amount"))
    fees_eur    = _d(fees_block.get("total_eur"))
    net_eur     = _d(net_block.get("eur_amount"))
    net_amt     = _d(net_block.get("amount"))
    currency    = gross_block.get("currency", "EUR")
    has_v1      = bool(doc.get("_repair_buy_fields_v1"))

    # Fee-free records (ZERO_COST or no-commission): already correct; skip silently.
    if fees_eur <= Decimal("0"):
        return None

    # --- Resolve trade value from source_row (primary) or v1-inference (Case A fallback) ---
    source_row = doc.get("source_row") or {}
    source_total = _look_up_source_value(source_row, _SOURCE_TOTAL_ALIASES)
    trade_value: Optional[Decimal] = None

    if source_total is not None:
        trade_value = source_total
    elif has_v1:
        # Case A fallback: v1 already validated via source_row at repair time.
        # After v1: gross = trade+fees, net = trade — verify shape arithmetically.
        if abs(gross_eur - (net_eur + fees_eur)) < _MATCH_TOLERANCE:
            trade_value = net_eur  # inferred: post-v1 net == trade value
        else:
            logger.warning(
                "SKIP v2 %s (Case A, no source_row): arithmetic inconsistent "
                "gross=%s net=%s fees=%s — fail-closed",
                doc_id, gross_eur, net_eur, fees_eur,
            )
            return None
    else:
        # Case B without source_row: cannot distinguish old/4ca553e/correct shapes.
        logger.warning(
            "SKIP v2 %s (Case B, no source_row): cannot determine trade value — fail-closed",
            doc_id,
        )
        return None

    # --- Compute target state ---
    target_gross = trade_value
    target_net   = trade_value + fees_eur

    # Already in correct final shape?
    if (abs(gross_eur - target_gross) < _MATCH_TOLERANCE
            and abs(net_eur - target_net) < _MATCH_TOLERANCE):
        logger.info(
            "SKIP v2 %s: already correct (gross=%s=trade, net=%s=trade+fees)",
            doc_id, gross_eur, net_eur,
        )
        return None

    # 4ca553e shape: gross = trade+fees, net = trade → swap gross↔net
    if (abs(gross_eur - target_net) < _MATCH_TOLERANCE
            and abs(net_eur - target_gross) < _MATCH_TOLERANCE):
        case_label = "A" if has_v1 else "B"
        return {
            "id": doc_id,
            "account_id": doc.get("account_id", ""),
            "security_id": doc.get("security_id", ""),
            "trade_date": doc.get("trade_date", ""),
            "quantity": str(doc.get("quantity") or "0"),
            "currency": currency,
            "case": case_label,
            "action": "swap",
            "reason": (
                f"Case {case_label}: 4ca553e shape (gross={gross_eur}=trade+fees, "
                f"net={net_eur}=trade); swap → gross=trade, net=gross+fees"
            ),
            "current_gross_eur": str(gross_eur),
            "current_net_eur":   str(net_eur),
            "current_fees_eur":  str(fees_eur),
            "new_gross_eur":     str(net_eur),    # new gross = old net (trade value)
            "new_net_eur":       str(gross_eur),  # new net  = old gross (trade+fees)
            "new_gross_amt":     str(net_amt),
            "new_net_amt":       str(gross_amt),
        }

    # Old/partial shape: gross ≈ trade value but net is not gross+fees → update net only
    if abs(gross_eur - target_gross) < _MATCH_TOLERANCE:
        case_label = "B" if not has_v1 else "A-partial"
        return {
            "id": doc_id,
            "account_id": doc.get("account_id", ""),
            "security_id": doc.get("security_id", ""),
            "trade_date": doc.get("trade_date", ""),
            "quantity": str(doc.get("quantity") or "0"),
            "currency": currency,
            "case": case_label,
            "action": "update_net",
            "reason": (
                f"Case {case_label}: gross already = trade ({gross_eur}); "
                f"net={net_eur} updated to gross+fees={target_net}"
            ),
            "current_gross_eur": str(gross_eur),
            "current_net_eur":   str(net_eur),
            "current_fees_eur":  str(fees_eur),
            "new_gross_eur":     str(gross_eur),  # unchanged
            "new_net_eur":       str(target_net), # updated to gross + fees
            "new_gross_amt":     str(gross_amt),  # unchanged
            "new_net_amt":       str(target_net), # updated
        }

    # Neither shape matches within tolerance → ambiguous → fail-closed
    logger.warning(
        "SKIP v2 %s: ambiguous shape — gross=%s net=%s fees=%s trade_value=%s "
        "matches neither 4ca553e nor old shape nor already-correct within tolerance",
        doc_id, gross_eur, net_eur, fees_eur, trade_value,
    )
    return None


# ---------------------------------------------------------------------------
# V2 Apply
# ---------------------------------------------------------------------------

def _apply_repair_v2(doc: dict, plan: Dict[str, Any]) -> dict:
    """Return a new doc dict with v2 corrections applied. Does not mutate input."""
    import copy
    patched = copy.deepcopy(doc)
    patched["gross"]["eur_amount"] = plan["new_gross_eur"]
    patched["gross"]["amount"]     = plan["new_gross_amt"]
    patched["net"]["eur_amount"]   = plan["new_net_eur"]
    patched["net"]["amount"]       = plan["new_net_amt"]
    patched["_repair_buy_fields_v2"] = _now_utc()
    return patched


# ---------------------------------------------------------------------------
# V2 Query
# ---------------------------------------------------------------------------

def _get_all_buy_candidates_v2(container) -> List[Tuple[dict, str]]:
    """Query all active BUY ledger_txns (csv_import + manual) for v2 analysis."""
    query = (
        "SELECT * FROM c "
        "WHERE c.doc_type = 'ledger_txn' "
        "AND c.txn_type = 'BUY'"
    )
    results = []
    for item in container.query_items(query=query, enable_cross_partition_query=True):
        etag = item.get("_etag", "")
        results.append((item, etag))
    return results


# ---------------------------------------------------------------------------
# V2 Audit
# ---------------------------------------------------------------------------

def run_audit_v2(container) -> List[Dict[str, Any]]:
    """Read-only v2 audit. Returns list of repair plans (no writes).

    Plans with ``action='swap'`` need gross↔net swap.
    Plans with ``action='update_net'`` need only net updated to gross+fees.
    Plans with ``case='A'`` carry the v1 marker; ``case='B'`` do not.
    """
    all_candidates = _get_all_buy_candidates_v2(container)
    logger.info("V2 audit: %d active BUY records scanned", len(all_candidates))

    plans: List[Dict[str, Any]] = []
    skipped_already_v2   = 0
    skipped_fee_free     = 0
    skipped_already_correct = 0
    skipped_fail_closed  = 0

    for doc, _ in all_candidates:
        if doc.get("_repair_buy_fields_v2"):
            skipped_already_v2 += 1
            continue
        fees_eur = _d((doc.get("fees") or {}).get("total_eur", "0"))
        if fees_eur <= Decimal("0"):
            skipped_fee_free += 1
            continue
        plan = _analyse_record_v2(doc)
        if plan is None:
            # Distinguish already-correct from fail-closed (logged at WARNING level above)
            # Use arithmetic heuristic to guess: if gross ≈ net+fees, it's 4ca553e still
            # otherwise it may be already correct or genuinely ambiguous
            gross_eur = _d((doc.get("gross") or {}).get("eur_amount", "0"))
            net_eur   = _d((doc.get("net") or {}).get("eur_amount", "0"))
            if abs(gross_eur - (net_eur + fees_eur)) < _MATCH_TOLERANCE and abs(net_eur - gross_eur) > _MATCH_TOLERANCE:
                skipped_fail_closed += 1
            else:
                skipped_already_correct += 1
        else:
            plans.append(plan)

    case_a   = sum(1 for p in plans if p.get("case") == "A")
    case_b   = sum(1 for p in plans if p.get("case") == "B")
    swaps    = sum(1 for p in plans if p.get("action") == "swap")
    net_upd  = sum(1 for p in plans if p.get("action") == "update_net")

    print(f"\n{'='*70}")
    print(f"V2 AUDIT REPORT — BUY net-centric field repair")
    print(f"{'='*70}")
    print(f"Active BUY records scanned:            {len(all_candidates)}")
    print(f"Already v2-repaired (skip):            {skipped_already_v2}")
    print(f"Fee-free / ZERO_COST (skip):           {skipped_fee_free}")
    print(f"Already correct / fail-closed (skip):  {skipped_already_correct + skipped_fail_closed}")
    print(f"Records needing v2 repair:             {len(plans)}")
    print(f"  Case A (v1 marker + fees>0):         {case_a}")
    print(f"  Case B (no marker + fees>0):         {case_b}")
    print(f"  Action = swap gross↔net:             {swaps}")
    print(f"  Action = update_net only:            {net_upd}")
    print()

    for p in plans:
        print(f"  [{p['id']}]  {p.get('security_id')}  {p.get('trade_date')}")
        print(f"    Case: {p['case']}  Action: {p['action']}")
        print(f"    Reason: {p['reason']}")
        print(
            f"    gross: {p['current_gross_eur']} → {p['new_gross_eur']}  "
            f"net: {p['current_net_eur']} → {p['new_net_eur']}  "
            f"fees: {p['current_fees_eur']} (unchanged)"
        )
        print()

    print(f"{'='*70}")
    print("Run with --apply-v2 to execute repairs (mandatory backup taken first).")
    return plans


# ---------------------------------------------------------------------------
# V2 Backup
# ---------------------------------------------------------------------------

def run_backup_v2(container, plans: List[Dict[str, Any]], backup_path: Optional[Path] = None) -> Path:
    """Export all records targeted by v2 repair plans to a JSON backup file.

    Checksum-validates the written file before returning.
    Raises SystemExit(2) if backup fails.
    """
    if not plans:
        logger.info("V2 backup: no candidates to backup.")
        return Path(os.devnull)

    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if backup_path is None:
        backup_path = _BACKUP_DIR / f"repair_buy_fields_v2_{_now_utc()}.json"

    docs_for_backup = []
    for plan in plans:
        try:
            doc = container.read_item(item=plan["id"], partition_key=plan["account_id"])
            docs_for_backup.append(dict(doc))
        except Exception as exc:
            logger.error("V2 backup: could not read %s: %s", plan["id"], exc)
            print(f"\nERROR: V2 backup failed for {plan['id']}: {exc}", file=sys.stderr)
            sys.exit(2)

    checksum = _sha256(docs_for_backup)
    payload = {
        "migration_version": "v2",
        "created_at": _now_utc(),
        "record_count": len(docs_for_backup),
        "sha256": checksum,
        "documents": docs_for_backup,
    }

    with open(backup_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)

    # Verify written file
    with open(backup_path, encoding="utf-8") as fh:
        written = json.load(fh)
    verify_checksum = _sha256(written["documents"])
    if verify_checksum != checksum:
        logger.error("V2 backup checksum mismatch — file corrupt, aborting.")
        sys.exit(2)

    logger.info(
        "V2 backup written: %s  (%d docs, SHA-256: %s)",
        backup_path, len(docs_for_backup), checksum,
    )
    print(f"\nV2 Backup: {backup_path}")
    print(f"  Records: {len(docs_for_backup)}  SHA-256: {checksum}")
    return backup_path


# ---------------------------------------------------------------------------
# V2 Apply
# ---------------------------------------------------------------------------

def run_apply_v2(container, plans: List[Dict[str, Any]]) -> int:
    """ETag-gated v2 patch for each repair plan. Returns number of failures."""
    if not plans:
        print("V2: nothing to apply.")
        return 0

    print(f"\nV2 applying {len(plans)} record(s)...")
    failures = 0

    for plan in plans:
        doc_id = plan["id"]
        acct   = plan["account_id"]
        try:
            # Fresh read for current ETag
            doc  = container.read_item(item=doc_id, partition_key=acct)
            etag = doc.get("_etag", "")

            # Re-analyse in case data changed between audit and apply
            fresh_plan = _analyse_record_v2(doc)
            if fresh_plan is None:
                logger.info("  SKIP v2 %s — already correct or changed since audit", doc_id)
                continue

            patched = _apply_repair_v2(doc, fresh_plan)
            ok = _etag_replace(container, doc_id, acct, patched, etag)
            if ok:
                print(f"  OK   {doc_id}  [{fresh_plan['action']}] [{fresh_plan['reason']}]")
            else:
                print(f"  CONFLICT {doc_id} — ETag changed; retry --apply-v2")
                failures += 1
        except Exception as exc:
            logger.error("  FAIL v2 %s: %s", doc_id, exc)
            failures += 1

    print(f"\nV2 apply complete: {len(plans) - failures} patched, {failures} failed.")
    return failures


# ---------------------------------------------------------------------------
# V2 Verify
# ---------------------------------------------------------------------------

def run_verify_v2(container) -> int:
    """Post-apply verification: run audit_v2 and expect zero candidates.

    Returns 0 if clean, 3 if candidates remain.
    """
    print(f"\n{'='*70}")
    print("V2 VERIFY — post-apply audit (expect 0 candidates)")
    print(f"{'='*70}")
    plans = run_audit_v2(container)
    if plans:
        print(
            f"\nVERIFY FAILED: {len(plans)} candidate(s) remain after apply. "
            "Re-run --apply-v2 to patch remaining records.",
            file=sys.stderr,
        )
        return 3
    print("\nVERIFY PASSED: 0 v2 candidates remain.")
    return 0


# ---------------------------------------------------------------------------
# V2 Restore
# ---------------------------------------------------------------------------

def run_restore_v2(container, backup_file: Path) -> int:
    """Restore v2 backup using ETag/CAS writes. Returns number of failures.

    Only accepts backup files with migration_version == 'v2'.
    Checksum-validates before any writes (fail-closed on mismatch).
    Each restore uses a fresh ETag read — safe for concurrent environments.
    """
    if not backup_file.exists():
        logger.error("V2 restore: backup file not found: %s", backup_file)
        sys.exit(2)

    with open(backup_file, encoding="utf-8") as fh:
        payload = json.load(fh)

    # Version guard: reject non-v2 backups
    if payload.get("migration_version") != "v2":
        logger.error(
            "V2 restore: backup file is not a v2 backup (migration_version=%r). "
            "Use --restore for v1 backups.",
            payload.get("migration_version"),
        )
        sys.exit(2)

    docs = payload.get("documents", [])
    stored_checksum = payload.get("sha256", "")
    actual_checksum = _sha256(docs)
    if actual_checksum != stored_checksum:
        logger.error(
            "V2 restore: checksum mismatch: stored=%s actual=%s — aborting",
            stored_checksum, actual_checksum,
        )
        sys.exit(2)

    print(f"\nV2 restoring {len(docs)} record(s) from {backup_file}...")
    failures = 0

    for doc in docs:
        doc_id = doc.get("id", "?")
        acct   = doc.get("account_id", "")
        try:
            # Read current ETag (not backup ETag — CAS uses live DB state)
            current = container.read_item(item=doc_id, partition_key=acct)
            etag    = current.get("_etag", "")
            ok = _etag_replace(container, doc_id, acct, doc, etag)
            if ok:
                print(f"  RESTORED {doc_id}")
            else:
                print(f"  CONFLICT {doc_id} — ETag changed during restore; retry")
                failures += 1
        except Exception as exc:
            logger.error("  FAIL v2 restore %s: %s", doc_id, exc)
            failures += 1

    print(f"\nV2 restore complete: {len(docs) - failures} restored, {failures} failed.")
    return failures


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_cosmos_container(database: str, portfolio_cname: str):
    """Build Cosmos raw container from environment variables."""
    from azure.cosmos import CosmosClient

    endpoint = os.environ.get("COSMOSDB_ENDPOINT")
    key = os.environ.get("COSMOSDB_KEY")
    if not endpoint or not key:
        logger.error("COSMOSDB_ENDPOINT and COSMOSDB_KEY must be set.")
        sys.exit(2)
    client = CosmosClient(endpoint, credential=key)
    db = client.get_database_client(database)
    return db.get_container_client(portfolio_cname)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repair_buy_ledger_fields",
        description=(
            "Audit and repair BUY ledger gross/net field semantics. "
            "V1 modes fix the pre-4ca553e inversion; V2 modes apply the "
            "net-centric accounting convention (danny-fifo-net-accounting-contract.md). "
            "Default (no flag): --audit (v1 read-only)."
        ),
    )
    mode_grp = parser.add_mutually_exclusive_group()
    # ---- V1 modes ----
    mode_grp.add_argument(
        "--audit",
        action="store_true",
        help="(default) V1 read-only audit; print per-record diff report.",
    )
    mode_grp.add_argument(
        "--apply",
        action="store_true",
        help="V1: mandatory backup → ETag-gated patch for each flagged csv_import record.",
    )
    mode_grp.add_argument(
        "--restore",
        metavar="BACKUP_FILE",
        help="V1: restore original values from a v1 backup JSON file.",
    )
    # ---- V2 modes ----
    mode_grp.add_argument(
        "--audit-v2",
        action="store_true",
        help="V2 read-only audit; print diff report for net-centric convention candidates.",
    )
    mode_grp.add_argument(
        "--apply-v2",
        action="store_true",
        help="V2: mandatory backup → ETag-gated patch for all v2 candidates.",
    )
    mode_grp.add_argument(
        "--verify-v2",
        action="store_true",
        help="V2: post-apply check; expects 0 candidates; exits 3 if any remain.",
    )
    mode_grp.add_argument(
        "--restore-v2",
        metavar="BACKUP_FILE",
        help="V2: restore from a v2 backup JSON file (ETag/CAS-gated).",
    )
    parser.add_argument(
        "--apply-manual-ids",
        metavar="ID1,ID2,...",
        help=(
            "Comma-separated movement IDs for operator-approved manual BUY records "
            "to include in --apply (v1 only). Each ID must appear in the audit "
            "manual-candidates list."
        ),
    )
    parser.add_argument(
        "--database",
        default="stock-options-manager",
        help="Cosmos DB database name (default: stock-options-manager)",
    )
    parser.add_argument(
        "--portfolio-container",
        default="portfolio",
        help="Cosmos DB portfolio container name (default: portfolio)",
    )
    parser.add_argument(
        "--backup-path",
        metavar="FILE",
        help="Override backup file path (--apply and --apply-v2 only).",
    )
    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()

    # Determine mode
    if args.apply:
        mode = "apply"
    elif args.restore:
        mode = "restore"
    elif getattr(args, "audit_v2", False):
        mode = "audit-v2"
    elif getattr(args, "apply_v2", False):
        mode = "apply-v2"
    elif getattr(args, "verify_v2", False):
        mode = "verify-v2"
    elif getattr(args, "restore_v2", None):
        mode = "restore-v2"
    else:
        mode = "audit"  # default

    manual_approved_ids: Optional[List[str]] = None
    if getattr(args, "apply_manual_ids", None):
        manual_approved_ids = [i.strip() for i in args.apply_manual_ids.split(",") if i.strip()]

    container = _build_cosmos_container(args.database, args.portfolio_container)

    # ---- V1 modes ----
    if mode == "audit":
        run_audit(container)
        return 0

    if mode == "restore":
        backup_file = Path(args.restore)
        failures = run_restore(container, backup_file)
        return 0 if failures == 0 else 3

    if mode == "apply":
        plans = run_audit(container)
        auto_plans = [p for p in plans if not p.get("candidate_only")]
        if not auto_plans and not manual_approved_ids:
            print("V1 audit found nothing to auto-repair. Exiting without writes.")
            return 0
        backup_path = Path(args.backup_path) if args.backup_path else None
        run_backup(container, plans, backup_path)  # exits(2) on failure
        failures = run_apply(container, plans, manual_approved_ids=manual_approved_ids)
        return 0 if failures == 0 else 3

    # ---- V2 modes ----
    if mode == "audit-v2":
        run_audit_v2(container)
        return 0

    if mode == "verify-v2":
        return run_verify_v2(container)

    if mode == "restore-v2":
        backup_file = Path(args.restore_v2)
        failures = run_restore_v2(container, backup_file)
        return 0 if failures == 0 else 3

    if mode == "apply-v2":
        plans = run_audit_v2(container)
        if not plans:
            print("V2 audit found nothing to repair. Exiting without writes.")
            return 0
        backup_path = Path(args.backup_path) if args.backup_path else None
        run_backup_v2(container, plans, backup_path)  # exits(2) on failure
        failures = run_apply_v2(container, plans)
        return 0 if failures == 0 else 3

    # Should not reach here
    logger.error("Unknown mode: %s", mode)
    return 1


if __name__ == "__main__":
    sys.exit(main())
