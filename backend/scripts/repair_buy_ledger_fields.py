#!/usr/bin/env python3
"""Audit and repair BUY ledger field inversion + INCOMPLETE→ZERO_COST reclassification.

Implements danny-scrip-zero-cost-and-buy-import-contract.md §3 exactly.

Background
----------
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

Modes
-----
  (default) --audit  Read-only. Prints csv auto-repair + manual candidate report.
  --apply            Mandatory backup → ETag-gated patch for csv_import records.
  --apply-manual-ids ID1,ID2,...  Also apply confirmed manual records (with --apply).
  --restore FILE     Restore original values from a backup JSON file.

Usage::

    # Audit (default — read-only)
    python -m scripts.repair_buy_ledger_fields --database stock-options-manager --portfolio-container portfolio

    # Apply (mandatory backup, then ETag-gated patch)
    python -m scripts.repair_buy_ledger_fields --apply --database stock-options-manager --portfolio-container portfolio

    # Restore from backup
    python -m scripts.repair_buy_ledger_fields --restore /path/to/backup.json --database stock-options-manager --portfolio-container portfolio

Exit codes:
    0  Clean (audit or apply with all records processed)
    1  Bad CLI arguments
    2  Backup failed or Cosmos connection error
    3  One or more records failed to patch (partial apply — run --restore)
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
            "Audit and repair BUY CSV import gross/net field inversion and "
            "INCOMPLETE→ZERO_COST reclassification. Default mode: --audit."
        ),
    )
    mode_grp = parser.add_mutually_exclusive_group()
    mode_grp.add_argument(
        "--audit",
        action="store_true",
        help="(default) Read-only audit; print per-record diff report.",
    )
    mode_grp.add_argument(
        "--apply",
        action="store_true",
        help="Mandatory backup → ETag-gated patch for each flagged csv_import record.",
    )
    mode_grp.add_argument(
        "--restore",
        metavar="BACKUP_FILE",
        help="Restore original values from a backup JSON file.",
    )
    parser.add_argument(
        "--apply-manual-ids",
        metavar="ID1,ID2,...",
        help=(
            "Comma-separated movement IDs for operator-approved manual BUY records "
            "to include in --apply. Each ID must appear in the audit manual-candidates "
            "list. Use only after verifying the record against the original trade "
            "confirmation."
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
        help="Override backup file path (--apply only).",
    )
    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()

    # Default to audit when no mode flag given
    mode = "audit"
    if args.apply:
        mode = "apply"
    elif args.restore:
        mode = "restore"

    manual_approved_ids: Optional[List[str]] = None
    if getattr(args, "apply_manual_ids", None):
        manual_approved_ids = [i.strip() for i in args.apply_manual_ids.split(",") if i.strip()]

    container = _build_cosmos_container(args.database, args.portfolio_container)

    if mode == "audit":
        run_audit(container)
        return 0

    if mode == "restore":
        backup_file = Path(args.restore)
        failures = run_restore(container, backup_file)
        return 0 if failures == 0 else 3

    # mode == "apply"
    plans = run_audit(container)
    auto_plans = [p for p in plans if not p.get("candidate_only")]
    if not auto_plans and not manual_approved_ids:
        print("Audit found nothing to auto-repair. Exiting without writes.")
        return 0

    backup_path = Path(args.backup_path) if args.backup_path else None
    run_backup(container, plans, backup_path)  # exits(2) on failure

    failures = run_apply(container, plans, manual_approved_ids=manual_approved_ids)
    return 0 if failures == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
