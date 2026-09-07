#!/usr/bin/env python3
"""Repair the NNYS:PEP → XNAS:PEP security identity corruption.

Implements danny-pep-security-id-repair-contract.md §3-§7 exactly.

Problem: sec_NNYS_PEP has exchange_mic="NNYS" (not a valid ISO 10383 MIC).
Fix: derive the correct MIC from config_PEP.exchange via LEGACY_ALIAS_TO_MIC,
create sec_XNAS_PEP, re-point config_PEP + all ledger_txn references, verify,
then delete the invalid sec_NNYS_PEP.

Discovery covers (§3):
  symbols container:
    - sec_NNYS_PEP  (source security_master, point-read)
    - config_PEP    (symbol_config, point-read — authoritative MIC source)
    - sec_XNAS_PEP  (candidate target, collision check)
  portfolio container:
    - ledger_txn WHERE security_id='NNYS:PEP'  (cross-partition)
    - import_session  (scanned in-memory for NNYS:PEP refs, best-effort)

Write ordering (create-before-delete, §5):
  1. Create/confirm sec_XNAS_PEP (target)
  2. Patch config_PEP.security_id = "XNAS:PEP"
  3. Patch each ledger_txn.security_id = "XNAS:PEP" (ETag per doc)
  4. Patch import_session refs (if any)
  5. Verify: zero NNYS:PEP remaining + holdings equivalence
  6. Delete sec_NNYS_PEP (only after step 5 passes)

Usage::

    # Audit (default — read-only, reports what would change)
    python -m scripts.repair_pep_security_id

    # Backup only
    python -m scripts.repair_pep_security_id --backup-only

    # Apply (mandatory backup, then repair, then verify)
    python -m scripts.repair_pep_security_id --apply

    # Restore from a previous backup
    python -m scripts.repair_pep_security_id --restore migration_backups/pep_security_id_repair_20260907T...json

    # Override source identity (for testing or future re-use)
    python -m scripts.repair_pep_security_id --from-security-id NNYS:PEP --apply

Exit codes:
    0  Normal run (incl. idempotent no-op, --audit, --backup-only)
    1  Bad CLI arguments / mutually-exclusive flags
    2  Backup failed / collision_ambiguous / discovery inconsistency — never proceeds to write
    3  Post-apply verification failed (holdings mismatch or nonzero refs remaining);
       sec_NNYS_PEP is retained, not deleted, in this case

DO NOT RUN AGAINST PRODUCTION without explicit authorization.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Allow `python scripts/repair_pep_security_id.py` without package context.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.portfolio.provider_symbols import (  # noqa: E402
    LEGACY_ALIAS_TO_MIC,
    MIC_TO_YFINANCE_SUFFIX,
)
from src.portfolio.cosmos_securities import (  # noqa: E402
    make_security_id,
    security_id_to_doc_id,
    security_id_to_ticker,
)
from src.us_exchange_eligibility import US_OPTIONS_ELIGIBLE_MICS  # noqa: E402
from src.yfinance_fetcher import YFinanceFetcher  # noqa: E402

# Backend twin of the frontend's `toExchangeMic` (DgiScreenerView.tsx) —
# reused here (not reinvented) to normalise a yfinance `info.exchange` code
# (e.g. "NMS") to the free-text alias LEGACY_ALIAS_TO_MIC already knows how
# to resolve to a MIC (danny-pep-repair-currency-correction.md §3b.3).
from src.dgi_screener import EXCHANGE_MAP as _YF_EXCHANGE_MAP  # noqa: E402

logger = logging.getLogger("repair_pep_security_id")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_FROM_SECURITY_ID = "NNYS:PEP"
DEFAULT_BACKUP_DIR = Path(__file__).parent / "migration_backups"

_COSMOS_SYSTEM_KEYS = frozenset({"_rid", "_self", "_etag", "_attachments", "_ts"})

# All valid MIC codes in this codebase — used to validate target MIC and
# detect an already-malformed candidate target.
_KNOWN_MICS: frozenset = (
    frozenset(MIC_TO_YFINANCE_SUFFIX)
    | US_OPTIONS_ELIGIBLE_MICS
    | frozenset(LEGACY_ALIAS_TO_MIC.values())
)

# Hard identifier fields that must not conflict on a collision candidate.
_HARD_ID_FIELDS = ("isin", "cusip", "sedol")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RepairBackup:
    """Backup snapshot — identical shape to MigrationBackup with two extra
    top-level fields (from/to security ids) and a per-doc 'container' field."""
    generated_at: str
    sha256: str
    from_security_id: str
    to_security_id: str
    documents: List[Dict[str, Any]]  # {id, partition_key, _etag, container, body}


@dataclass
class RepairReport:
    """Aggregate counters for a repair run."""
    from_security_id: str = ""
    to_security_id: str = ""
    # Discovery
    source_security_master_found: bool = False
    target_security_master_pre_existed: bool = False
    config_doc_found: bool = False
    ledger_txn_found: int = 0
    import_session_refs_found: int = 0
    # Diagnostic only — NEVER an input to listing_currency (§3c of
    # danny-pep-repair-currency-correction.md). Reflects the portfolio's
    # booking/accounting currency for these ledger movements, not the
    # security's listing currency.
    ledger_accounting_currency_note: str = ""
    # Live-provider verification outcome for an operator-supplied
    # --listing-currency (§3b/§3d). One of: "not_requested",
    # "verified:<CUR>", "unreachable", "mismatch:<details>".
    provider_currency_verdict: str = "not_requested"
    # Write outcomes
    target_created: bool = False
    config_patched: bool = False
    config_already_correct: bool = False
    ledger_patched: int = 0
    ledger_already_correct: int = 0
    import_session_patched: int = 0
    source_deleted: bool = False
    source_already_gone: bool = False
    # Errors
    cas_conflicts: int = 0
    errors: int = 0
    error_details: List[str] = field(default_factory=list)
    # Abort / exit
    abort_reason: str = ""
    exit_code: int = 0
    verification_failed: bool = False

    @property
    def movements_found(self) -> int:
        """Alias for ledger_txn_found (Basher contract field name)."""
        return self.ledger_txn_found

    @property
    def movements_discovered(self) -> int:
        """Alias for ledger_txn_found (Basher contract field name)."""
        return self.ledger_txn_found


@dataclass
class RestoreReport:
    """Aggregate counters for a restore run."""
    reverted: int = 0
    skipped_already_restored: int = 0
    deleted_migration_docs: int = 0
    cas_conflicts: int = 0
    errors: int = 0
    error_details: List[str] = field(default_factory=list)


class RepairAbort(RuntimeError):
    """Raised during discovery/pre-checks when writes must not proceed."""
    def __init__(self, reason: str, exit_code: int = 2) -> None:
        super().__init__(reason)
        self.reason = reason
        self.exit_code = exit_code


class VerificationError(RuntimeError):
    """Raised when post-write verification fails (exit code 3)."""
    def __init__(self, errors: List[str], report: Optional[RepairReport] = None) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors
        self.report = report


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _clean(doc: dict) -> dict:
    """Strip Cosmos system keys from a document body."""
    return {k: v for k, v in doc.items() if k not in _COSMOS_SYSTEM_KEYS}


def _compute_checksum(entries: List[Dict[str, Any]]) -> str:
    """SHA-256 over entries sorted by (container, id).

    Uses json.dumps with sort_keys=True and default separators — matches
    the convention established in migrate_legacy_symbol_config.py.
    """
    sorted_entries = sorted(entries, key=lambda e: (e.get("container", ""), e["id"]))
    return hashlib.sha256(
        json.dumps(sorted_entries, sort_keys=True).encode()
    ).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _derive_target_security_id(config_doc: dict, ticker: str) -> Optional[str]:
    """Derive target security_id from config_doc.exchange via LEGACY_ALIAS_TO_MIC.

    Never hardcodes 'XNAS' — resolves at runtime from the stored exchange field
    so the single source of MIC-alias truth remains in provider_symbols.py.
    Returns None if the exchange is unresolvable.
    """
    exchange = (config_doc.get("exchange") or "").strip().upper()
    if exchange in LEGACY_ALIAS_TO_MIC:
        mic = LEGACY_ALIAS_TO_MIC[exchange]
        return make_security_id(mic, ticker)
    if exchange in _KNOWN_MICS:
        # Exchange is already a valid canonical MIC — use it directly.
        return make_security_id(exchange, ticker)
    return None


def _extract_gross_currencies(ledger_txns: List[dict]) -> List[str]:
    """Return non-empty gross.currency values from ledger movements.

    Diagnostic extraction only — feeds `_ledger_accounting_currency_note`,
    never a listing_currency decision (§3c of
    danny-pep-repair-currency-correction.md).
    """
    result = []
    for m in ledger_txns:
        currency = ((m.get("gross") or {}).get("currency") or "").strip().upper()
        if currency:
            result.append(currency)
    return result


def _ledger_accounting_currency_note(currencies: List[str]) -> str:
    """Diagnostic-only note about ledger `gross.currency` values (§3c).

    This reflects the portfolio's booking/accounting currency for these
    movements, not the security's listing currency. It must never influence
    `listing_currency` — unlike the rejected `_currency_verdict` mechanism,
    this function has no "proposed currency" output at all, so it is
    structurally incapable of feeding a write path.
    """
    if not currencies:
        return "no_movements"
    unique = set(currencies)
    if len(unique) == 1:
        return f"unanimous_accounting_currency:{unique.pop()}"
    return "mixed_accounting_currencies"


def _resolve_provider_mic(info: Dict[str, Any]) -> Optional[str]:
    """Resolve a yfinance `info` dict's exchange fields to a canonical MIC.

    Reuses `_YF_EXCHANGE_MAP` (backend twin of the frontend's
    `toExchangeMic`, already defined in `src/dgi_screener.py`) to normalise
    a raw yfinance exchange code (e.g. "NMS") to a free-text alias
    ("NASDAQ"), then `LEGACY_ALIAS_TO_MIC` (the single existing alias→MIC
    table) to resolve that alias to a MIC. No second/divergent mapping
    table is introduced (§3b.3).

    Falls back to matching the alias as a substring of `fullExchangeName`
    (e.g. "NasdaqGS" contains "NASDAQ") if the raw exchange code itself
    isn't recognised. Returns None if neither corroborates a known MIC.
    """
    exchange_code = (info.get("exchange") or "").strip().upper()
    alias = _YF_EXCHANGE_MAP.get(exchange_code, exchange_code)
    mic = LEGACY_ALIAS_TO_MIC.get(alias)
    if mic:
        return mic

    full_exchange_name = (info.get("fullExchangeName") or "").strip().upper()
    if full_exchange_name:
        for alias_key, mic_value in LEGACY_ALIAS_TO_MIC.items():
            if alias_key in full_exchange_name:
                return mic_value
    return None


def _verify_listing_currency_with_provider(
    ticker: str,
    requested_currency: str,
    target_mic: str,
    fetcher: Optional[Any] = None,
) -> Tuple[str, str]:
    """Live provider verification for an operator-supplied `--listing-currency`
    (§3b). Never trusts the operator's word alone; never silently applies if
    the live check is unreachable.

    Returns (verdict, detail):
      verdict — exactly one of:
        f"verified:{CUR}"    — all three checks passed; safe to apply.
        "unreachable"        — provider fetch failed / empty / missing
                                required fields (network error, rate limit,
                                None result, or missing currency/exchange).
        f"mismatch:{detail}" — currency/financialCurrency/MIC disagreement.
      detail — human-readable diagnostic (superset of what's embedded in a
        "mismatch:" verdict; also populated for "unreachable" for logging/
        abort messages, even though the report field itself stays plain
        "unreachable" per §3d).

    Never raises — callers (discover(), run_apply(), run_audit()) decide
    whether to abort (--apply) or just report (--audit) based on the
    returned verdict.
    """
    if fetcher is None:
        fetcher = YFinanceFetcher()

    try:
        data = fetcher.get_ticker_data(ticker)
    except Exception as exc:  # noqa: BLE001
        return "unreachable", f"provider fetch raised: {exc}"

    if not data or not isinstance(data, dict):
        return "unreachable", (
            "provider returned no data (network error, rate limit, or "
            "unknown ticker) — cannot verify listing_currency"
        )

    info = data.get("info") or {}
    currency = info.get("currency")
    financial_currency = info.get("financialCurrency")
    provider_exchange = info.get("exchange")
    full_exchange_name = info.get("fullExchangeName")

    if not currency or not financial_currency or not provider_exchange:
        return "unreachable", (
            f"provider response missing required fields "
            f"(currency={currency!r}, financialCurrency={financial_currency!r}, "
            f"exchange={provider_exchange!r}) — cannot verify listing_currency"
        )

    requested = requested_currency.strip().upper()
    currency_u = str(currency).strip().upper()
    financial_currency_u = str(financial_currency).strip().upper()

    if not (currency_u == financial_currency_u == requested):
        detail = (
            f"currency={currency_u!r} financialCurrency={financial_currency_u!r} "
            f"requested={requested!r} (all three must agree)"
        )
        return f"mismatch:{detail}", detail

    resolved_mic = _resolve_provider_mic(info)
    target_mic_u = (target_mic or "").strip().upper()
    if resolved_mic is None or resolved_mic.upper() != target_mic_u:
        detail = (
            f"provider exchange={provider_exchange!r} "
            f"fullExchangeName={full_exchange_name!r} "
            f"resolved_mic={resolved_mic!r} does not corroborate "
            f"target_mic={target_mic_u!r}"
        )
        return f"mismatch:{detail}", detail

    detail = (
        f"currency={currency_u} financialCurrency={financial_currency_u} "
        f"exchange={provider_exchange!r} fullExchangeName={full_exchange_name!r} "
        f"resolved_mic={resolved_mic}"
    )
    return f"verified:{requested}", detail


def _holdings_snapshot(movements: List[dict]) -> Dict[str, Dict[str, Any]]:
    """Minimal per-security_id snapshot for holdings equivalence check.

    Returns: {security_id: {movement_count: int, net_qty: str}}
    net_qty = Σ BUY_ACCIONES_qty − Σ SELL_ACCIONES_qty (sign per txn_type).
    """
    agg: Dict[str, Dict[str, Any]] = {}
    for m in movements:
        sid = m.get("security_id", "")
        if not sid:
            continue
        if sid not in agg:
            agg[sid] = {"movement_count": 0, "net_qty": Decimal("0")}
        agg[sid]["movement_count"] += 1
        qty = Decimal(str(m.get("quantity") or "0"))
        txn_type = m.get("txn_type", "")
        if txn_type == "BUY":
            agg[sid]["net_qty"] += qty
        elif txn_type == "SELL":
            if (m.get("sales_type") or "ACCIONES") == "ACCIONES":
                agg[sid]["net_qty"] -= qty
    return {
        sid: {"movement_count": v["movement_count"], "net_qty": str(v["net_qty"])}
        for sid, v in agg.items()
    }


def _check_collision(source_sec: dict, target_sec: dict) -> Optional[str]:
    """Return None if target is compatible, else a human-readable conflict reason."""
    for f in _HARD_ID_FIELDS:
        src = (source_sec.get(f) or "").strip()
        tgt = (target_sec.get(f) or "").strip()
        if src and tgt and src != tgt:
            return (
                f"Hard identifier conflict on '{f}': "
                f"source={src!r}, target={tgt!r}"
            )
    src_ticker = (source_sec.get("ticker") or "").upper()
    tgt_ticker = (target_sec.get("ticker") or "").upper()
    if src_ticker and not tgt_ticker:
        return (
            f"Malformed target: source has ticker={src_ticker!r} but target has no ticker — "
            "manual review required"
        )
    if src_ticker and tgt_ticker and src_ticker != tgt_ticker:
        return f"Ticker mismatch: source={src_ticker!r}, target={tgt_ticker!r}"
    # Pre-existing target must have a valid exchange_mic.
    if target_sec.get("exchange_mic", "").upper() not in _KNOWN_MICS:
        return (
            f"Candidate target has malformed exchange_mic="
            f"{target_sec.get('exchange_mic')!r} — manual review required"
        )
    return None


def _find_import_session_refs(
    sessions: List[dict], from_security_id: str
) -> List[dict]:
    """Filter import_session docs that reference from_security_id."""
    refs = []
    for doc in sessions:
        found = False
        res_map = doc.get("resolution_map") or {}
        if isinstance(res_map, dict) and any(
            v == from_security_id for v in res_map.values()
        ):
            found = True
        enrolled = doc.get("enrolled_security_ids") or []
        if isinstance(enrolled, list) and from_security_id in enrolled:
            found = True
        if found:
            refs.append(doc)
    return refs


def _etag_replace(
    container,
    doc_id: str,
    body: dict,
    snapshot_etag: str,
) -> bool:
    """ETag-gated replace_item.  Returns True on success, False on CAS conflict."""
    from azure.core import MatchConditions
    from azure.cosmos.exceptions import CosmosHttpResponseError

    try:
        container.replace_item(
            item=doc_id,
            body=_clean(body),
            etag=snapshot_etag,
            match_condition=MatchConditions.IfNotModified,
        )
        return True
    except CosmosHttpResponseError as exc:
        if exc.status_code in (409, 412):
            return False
        raise


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

@dataclass
class _Discovery:
    """Internal discovery result — all data needed for backup + write passes."""
    from_security_id: str
    to_security_id: str
    ticker: str
    target_mic: str
    # Symbols container docs (raw, include _etag)
    config_raw: Optional[dict]       # config_PEP
    source_sec_raw: Optional[dict]   # sec_NNYS_PEP
    target_sec_raw: Optional[dict]   # sec_XNAS_PEP (pre-existing, if any)
    # Portfolio container docs (raw)
    ledger_txns_raw: List[dict]      # ledger_txn with security_id==from_security_id
    import_session_refs_raw: List[dict]  # import_session docs with refs
    # Ledger accounting-currency note (diagnostic only, §3c — never an
    # input to listing_currency) and live-provider listing_currency
    # verification outcome (§3b/§3d).
    ledger_accounting_currency_note: str
    provider_currency_verdict: str
    provider_currency_detail: str
    proposed_listing_currency: Optional[str]
    # Holdings snapshot (pre-write, includes any already-patched XNAS:PEP movements)
    holdings_before: Dict[str, Dict[str, Any]]
    # Abort flag (set when collision found — caller raises RepairAbort)
    collision_abort: bool = False
    collision_reason: str = ""


def discover(
    symbols_container,
    portfolio_container,
    from_security_id: str = DEFAULT_FROM_SECURITY_ID,
    to_security_id: Optional[str] = None,
    listing_currency: Optional[str] = None,
    yf_fetcher: Optional[Any] = None,
) -> _Discovery:
    """Perform full §3 discovery.

    Raises RepairAbort (exit_code=2) on unresolvable config or fatal inconsistency.
    Returns a _Discovery with collision_abort=True for collision cases (caller
    raises RepairAbort after logging the reason — so tests can inspect state).

    Args:
        listing_currency: Optional operator-supplied target listing currency
            (danny-pep-repair-currency-correction.md §3b). If provided, a
            live provider verification is performed here (best-effort, never
            raises) and the outcome is recorded on the returned _Discovery's
            `provider_currency_verdict`/`provider_currency_detail`. If
            omitted, no currency change is proposed — the existing source
            `listing_currency` is preserved (§3b bullet 1), matching
            pre-mechanism behaviour exactly.
        yf_fetcher: Optional injected fetcher (tests) — defaults to a real
            `YFinanceFetcher()` instance when a live check is needed.
    """
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    ticker = security_id_to_ticker(from_security_id)
    source_doc_id = security_id_to_doc_id(from_security_id)
    config_doc_id = f"config_{ticker}"

    # ── Step 1a: config_PEP (required — provides authoritative MIC) ──
    try:
        config_raw = symbols_container.read_item(
            item=config_doc_id, partition_key=ticker
        )
    except CosmosResourceNotFoundError:
        raise RepairAbort(
            f"{config_doc_id} not found in symbols container — "
            "cannot derive target MIC without it",
            exit_code=2,
        )

    # Derive to_security_id (unless explicitly overridden via CLI/test)
    if to_security_id is None:
        config_clean = _clean(config_raw)
        derived = _derive_target_security_id(config_clean, ticker)
        if derived is None:
            raise RepairAbort(
                f"Cannot derive target security_id from "
                f"{config_doc_id}.exchange={config_clean.get('exchange')!r} "
                "— not in LEGACY_ALIAS_TO_MIC or _KNOWN_MICS",
                exit_code=2,
            )
        to_security_id = derived

    target_mic = to_security_id.split(":", 1)[0].upper()
    target_doc_id = security_id_to_doc_id(to_security_id)

    # ── Step 1b: source security_master (sec_NNYS_PEP) ──────────────
    try:
        source_sec_raw = symbols_container.read_item(
            item=source_doc_id, partition_key=ticker
        )
    except CosmosResourceNotFoundError:
        source_sec_raw = None  # already deleted (idempotent re-run)

    # ── Step 2: collision check on candidate target ──────────────────
    try:
        target_sec_raw = symbols_container.read_item(
            item=target_doc_id, partition_key=ticker
        )
        # Check compatibility
        if source_sec_raw is not None:
            conflict = _check_collision(_clean(source_sec_raw), _clean(target_sec_raw))
            if conflict:
                return _Discovery(
                    from_security_id=from_security_id,
                    to_security_id=to_security_id,
                    ticker=ticker,
                    target_mic=target_mic,
                    config_raw=config_raw,
                    source_sec_raw=source_sec_raw,
                    target_sec_raw=target_sec_raw,
                    ledger_txns_raw=[],
                    import_session_refs_raw=[],
                    ledger_accounting_currency_note="",
                    provider_currency_verdict="not_requested",
                    provider_currency_detail="",
                    proposed_listing_currency=None,
                    holdings_before={},
                    collision_abort=True,
                    collision_reason=conflict,
                )
    except CosmosResourceNotFoundError:
        target_sec_raw = None

    # ── Step 3: portfolio ledger_txn query ───────────────────────────
    ledger_txns_raw = list(portfolio_container.query_items(
        query=(
            "SELECT * FROM c WHERE c.doc_type='ledger_txn' "
            "AND c.security_id=@security_id"
        ),
        parameters=[{"name": "@security_id", "value": from_security_id}],
        enable_cross_partition_query=True,
    ))

    # ── Step 4: import_session scan (best-effort) ────────────────────
    all_sessions_raw = list(portfolio_container.query_items(
        query="SELECT * FROM c WHERE c.doc_type='import_session'",
        enable_cross_partition_query=True,
    ))
    import_refs_raw = []
    for raw in all_sessions_raw:
        doc_clean = _clean(raw)
        found = False
        res_map = doc_clean.get("resolution_map") or {}
        if isinstance(res_map, dict) and any(
            v == from_security_id for v in res_map.values()
        ):
            found = True
        enrolled = doc_clean.get("enrolled_security_ids") or []
        if isinstance(enrolled, list) and from_security_id in enrolled:
            found = True
        if found:
            import_refs_raw.append(raw)

    # ── Ledger accounting-currency note (diagnostic only, §3c) ────────
    # This is NEVER an input to listing_currency — see docstring on
    # `_ledger_accounting_currency_note`.
    ledger_clean = [_clean(d) for d in ledger_txns_raw]
    currencies = _extract_gross_currencies(ledger_clean)
    ledger_accounting_currency_note = _ledger_accounting_currency_note(currencies)

    # ── Live-provider listing_currency verification (§3b) ─────────────
    # No currency change happens unless the operator explicitly passes
    # --listing-currency. Absent it, no live check is performed and
    # proposed_listing_currency stays None (caller preserves the existing
    # source listing_currency unchanged, exactly as before this mechanism
    # existed).
    provider_currency_verdict = "not_requested"
    provider_currency_detail = ""
    proposed_currency: Optional[str] = None
    if listing_currency:
        provider_currency_verdict, provider_currency_detail = (
            _verify_listing_currency_with_provider(
                ticker, listing_currency, target_mic, fetcher=yf_fetcher,
            )
        )
        if provider_currency_verdict.startswith("verified:"):
            proposed_currency = listing_currency.strip().upper()

    # ── Holdings snapshot before write ───────────────────────────────
    # Merge in any already-patched XNAS:PEP movements (for idempotent re-run)
    already_patched_raw = list(portfolio_container.query_items(
        query=(
            "SELECT * FROM c WHERE c.doc_type='ledger_txn' "
            "AND c.security_id=@security_id"
        ),
        parameters=[{"name": "@security_id", "value": to_security_id}],
        enable_cross_partition_query=True,
    ))
    all_pep_clean = ledger_clean + [_clean(d) for d in already_patched_raw]
    holdings_before = _holdings_snapshot(all_pep_clean)

    return _Discovery(
        from_security_id=from_security_id,
        to_security_id=to_security_id,
        ticker=ticker,
        target_mic=target_mic,
        config_raw=config_raw,
        source_sec_raw=source_sec_raw,
        target_sec_raw=target_sec_raw,
        ledger_txns_raw=ledger_txns_raw,
        import_session_refs_raw=import_refs_raw,
        ledger_accounting_currency_note=ledger_accounting_currency_note,
        provider_currency_verdict=provider_currency_verdict,
        provider_currency_detail=provider_currency_detail,
        proposed_listing_currency=proposed_currency,
        holdings_before=holdings_before,
    )


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

def build_backup(disc: _Discovery) -> RepairBackup:
    """Snapshot every discovered document into a RepairBackup.

    Captures _etag per document (needed for ETag-gated restore and CAS
    during apply).  Backup covers both containers (§4).
    """
    entries: List[Dict[str, Any]] = []

    def _add(raw_doc: dict, container_name: str) -> None:
        if raw_doc is None:
            return
        entries.append({
            "id": raw_doc.get("id", ""),
            "partition_key": raw_doc.get("symbol") or raw_doc.get("account_id", ""),
            "_etag": raw_doc.get("_etag", ""),
            "container": container_name,
            "body": _clean(raw_doc),
        })

    _add(disc.config_raw, "symbols")
    _add(disc.source_sec_raw, "symbols")
    if disc.target_sec_raw is not None:
        _add(disc.target_sec_raw, "symbols")  # safety: if target pre-existed

    for raw in disc.ledger_txns_raw:
        _add(raw, "portfolio")
    for raw in disc.import_session_refs_raw:
        _add(raw, "portfolio")

    checksum = _compute_checksum(entries)
    return RepairBackup(
        generated_at=_now_iso(),
        sha256=checksum,
        from_security_id=disc.from_security_id,
        to_security_id=disc.to_security_id,
        documents=sorted(entries, key=lambda e: (e.get("container", ""), e["id"])),
    )


def write_backup(backup: RepairBackup, backup_dir: Path) -> Path:
    """Write backup to a timestamped JSON file; return the path."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = backup_dir / f"pep_security_id_repair_{ts}.json"
    payload = {
        "generated_at": backup.generated_at,
        "sha256": backup.sha256,
        "from_security_id": backup.from_security_id,
        "to_security_id": backup.to_security_id,
        "documents": backup.documents,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def read_backup(path: Path) -> RepairBackup:
    """Read and checksum-verify a backup file.

    Raises ValueError if the checksum does not match.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    stored = payload.get("sha256", "")
    computed = _compute_checksum(payload.get("documents", []))
    if stored != computed:
        raise ValueError(
            f"Backup checksum mismatch — file may have been altered.\n"
            f"  stored:   {stored}\n"
            f"  computed: {computed}"
        )
    return RepairBackup(
        generated_at=payload.get("generated_at", ""),
        sha256=stored,
        from_security_id=payload.get("from_security_id", ""),
        to_security_id=payload.get("to_security_id", ""),
        documents=payload.get("documents", []),
    )


# ---------------------------------------------------------------------------
# Post-write verification
# ---------------------------------------------------------------------------

def _verify_repair(
    portfolio_container,
    from_security_id: str,
    to_security_id: str,
    holdings_before: Dict[str, Dict[str, Any]],
) -> List[str]:
    """Post-flight checks (§5.5).

    1. Zero remaining ledger_txn with security_id == from_security_id.
    2. Holdings equivalence: net_qty and movement_count unchanged, key shifted.

    Returns a list of error strings (empty = pass).
    """
    errors: List[str] = []

    # (a) Re-run §3.3 query — must return zero rows.
    remaining = list(portfolio_container.query_items(
        query=(
            "SELECT * FROM c WHERE c.doc_type='ledger_txn' "
            "AND c.security_id=@security_id"
        ),
        parameters=[{"name": "@security_id", "value": from_security_id}],
        enable_cross_partition_query=True,
    ))
    if remaining:
        errors.append(
            f"Verification: {len(remaining)} ledger_txn(s) still have "
            f"security_id={from_security_id!r} after repair"
        )

    # (b) Holdings equivalence.
    all_after_raw = list(portfolio_container.query_items(
        query=(
            "SELECT * FROM c WHERE c.doc_type='ledger_txn' "
            "AND c.security_id=@security_id"
        ),
        parameters=[{"name": "@security_id", "value": to_security_id}],
        enable_cross_partition_query=True,
    ))
    holdings_after = _holdings_snapshot([_clean(d) for d in all_after_raw])

    # Combine before-snapshot: use whichever key (from or to) had the
    # movements — in an idempotent re-run some may already be under to_security_id.
    before_count = sum(
        v["movement_count"]
        for sid, v in holdings_before.items()
        if sid in (from_security_id, to_security_id)
    )
    before_qty_dec = sum(
        Decimal(v["net_qty"])
        for sid, v in holdings_before.items()
        if sid in (from_security_id, to_security_id)
    )

    after_snap = holdings_after.get(to_security_id, {"movement_count": 0, "net_qty": "0"})
    after_count = after_snap["movement_count"]
    after_qty_dec = Decimal(after_snap["net_qty"])

    if before_count != after_count:
        errors.append(
            f"Holdings mismatch: movement_count before={before_count}, "
            f"after={after_count}"
        )
    if before_qty_dec != after_qty_dec:
        errors.append(
            f"Holdings mismatch: net_qty before={before_qty_dec}, "
            f"after={after_qty_dec}"
        )

    # from_security_id must be gone from after holdings
    if from_security_id in holdings_after:
        errors.append(
            f"Verification: {from_security_id} still present in holdings "
            f"after repair (count={holdings_after[from_security_id]['movement_count']})"
        )

    return errors


# ---------------------------------------------------------------------------
# Run modes
# ---------------------------------------------------------------------------

def run_audit(
    symbols_container,
    portfolio_container,
    from_security_id: str = DEFAULT_FROM_SECURITY_ID,
    to_security_id: Optional[str] = None,
    listing_currency: Optional[str] = None,
    yf_fetcher: Optional[Any] = None,
) -> RepairReport:
    """Read-only: discover, derive, report — write nothing."""
    try:
        disc = discover(
            symbols_container, portfolio_container, from_security_id, to_security_id,
            listing_currency=listing_currency, yf_fetcher=yf_fetcher,
        )
    except RepairAbort as exc:
        report = RepairReport(
            from_security_id=from_security_id,
            to_security_id=to_security_id or "",
            abort_reason=exc.reason,
            exit_code=exc.exit_code,
        )
        return report

    report = RepairReport(
        from_security_id=from_security_id,
        to_security_id=disc.to_security_id,
        source_security_master_found=disc.source_sec_raw is not None,
        target_security_master_pre_existed=disc.target_sec_raw is not None,
        config_doc_found=disc.config_raw is not None,
        ledger_txn_found=len(disc.ledger_txns_raw),
        import_session_refs_found=len(disc.import_session_refs_raw),
        ledger_accounting_currency_note=disc.ledger_accounting_currency_note,
        provider_currency_verdict=disc.provider_currency_verdict,
        exit_code=0,
    )
    if disc.collision_abort:
        report.abort_reason = disc.collision_reason
        report.exit_code = 2
    return report


def run_backup_only(
    symbols_container,
    portfolio_container,
    from_security_id: str = DEFAULT_FROM_SECURITY_ID,
    to_security_id: Optional[str] = None,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    listing_currency: Optional[str] = None,
    yf_fetcher: Optional[Any] = None,
) -> Path:
    """Backup only — no analysis writes beyond the backup file."""
    disc = discover(
        symbols_container, portfolio_container, from_security_id, to_security_id,
        listing_currency=listing_currency, yf_fetcher=yf_fetcher,
    )
    backup = build_backup(disc)
    return write_backup(backup, backup_dir)


def run_apply(
    symbols_container,
    portfolio_container,
    from_security_id: str = DEFAULT_FROM_SECURITY_ID,
    to_security_id: Optional[str] = None,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    listing_currency: Optional[str] = None,
    yf_fetcher: Optional[Any] = None,
) -> RepairReport:
    """Backup → discover → write (ETag-gated) → verify → delete source.

    Raises VerificationError (exit_code=3) if post-write checks fail —
    sec_NNYS_PEP is NOT deleted in that case.

    If `listing_currency` is supplied, live provider verification (§3b)
    must succeed (`provider_currency_verdict` starts with "verified:")
    BEFORE any backup/mutation occurs — otherwise raises RepairAbort
    (exit_code=2). This gate sits between Phase 1 (discover) and Phase 2
    (backup), per danny-pep-repair-currency-correction.md §3b/§3d ordering.
    """
    from azure.cosmos.exceptions import CosmosResourceNotFoundError, CosmosHttpResponseError
    from azure.core import MatchConditions

    # ── Phase 1: discover ─────────────────────────────────────────────
    try:
        disc = discover(
            symbols_container, portfolio_container, from_security_id, to_security_id,
            listing_currency=listing_currency, yf_fetcher=yf_fetcher,
        )
    except RepairAbort as exc:
        raise

    if disc.collision_abort:
        raise RepairAbort(disc.collision_reason, exit_code=2)

    # ── Currency gate (§3b/§3d): must abort BEFORE backup/mutations ───
    if listing_currency and not disc.provider_currency_verdict.startswith("verified:"):
        logger.error(
            "Aborting before backup/mutations: --listing-currency=%s requested "
            "but live provider verification did not pass (verdict=%s, detail=%s)",
            listing_currency, disc.provider_currency_verdict, disc.provider_currency_detail,
        )
        raise RepairAbort(
            f"--listing-currency={listing_currency!r} requested but live provider "
            f"verification failed: {disc.provider_currency_verdict} "
            f"({disc.provider_currency_detail})",
            exit_code=2,
        )

    # ── Phase 2: backup (mandatory, before first write) ───────────────
    backup = build_backup(disc)
    backup_path = write_backup(backup, backup_dir)
    logger.info("Backup written to %s", backup_path)

    report = RepairReport(
        from_security_id=from_security_id,
        to_security_id=disc.to_security_id,
        source_security_master_found=disc.source_sec_raw is not None,
        target_security_master_pre_existed=disc.target_sec_raw is not None,
        config_doc_found=disc.config_raw is not None,
        ledger_txn_found=len(disc.ledger_txns_raw),
        import_session_refs_found=len(disc.import_session_refs_raw),
        ledger_accounting_currency_note=disc.ledger_accounting_currency_note,
        provider_currency_verdict=disc.provider_currency_verdict,
    )

    ticker = disc.ticker
    now = _now_iso()
    target_sid = disc.to_security_id
    target_doc_id = security_id_to_doc_id(target_sid)
    source_doc_id = security_id_to_doc_id(disc.from_security_id)

    # ETag map: from discovery snapshot
    def _etag(raw_doc: Optional[dict]) -> str:
        if raw_doc is None:
            return ""
        return raw_doc.get("_etag", "")

    # ── Phase 3a: create or confirm target security_master ────────────
    if disc.target_sec_raw is None:
        # Build new doc: clone source body with corrected identity fields.
        if disc.source_sec_raw is None:
            raise RepairAbort(
                f"Neither source ({source_doc_id}) nor target ({target_doc_id}) "
                "security_master found — cannot repair without a source to clone",
                exit_code=2,
            )
        source_clean = _clean(disc.source_sec_raw)
        # Determine listing_currency to apply: only ever the provider-verified
        # proposed value: no flag / not verified → preserve existing source
        # listing_currency unchanged (§3b bullet 1).
        if disc.proposed_listing_currency is not None:
            resolved_listing_currency = disc.proposed_listing_currency
        else:
            resolved_listing_currency = source_clean.get("listing_currency", "EUR")

        new_target = {
            **source_clean,
            "id": target_doc_id,
            "security_id": target_sid,
            "exchange_mic": disc.target_mic,
            "listing_currency": resolved_listing_currency,
            # Preserve original created_at (this is a corrected identity, not new)
            "updated_at": now,
            "migrated_from": disc.from_security_id,
            "migration_note": (
                f"Repaired by repair_pep_security_id.py: "
                f"{disc.from_security_id} had invalid exchange_mic "
                f"{source_clean.get('exchange_mic')!r}; "
                f"corrected to {disc.target_mic} from config_{ticker}.exchange"
            ),
        }
        try:
            symbols_container.create_item(new_target)
            report.target_created = True
            logger.info("Created target security_master: %s", target_doc_id)
        except CosmosHttpResponseError as exc:
            if exc.status_code == 409:
                # Created by a concurrent or prior partial run — that's fine.
                logger.info("Target %s already exists (409) — idempotent skip", target_doc_id)
            else:
                raise
    else:
        logger.info(
            "Target %s already exists — skipping create, will re-point references to it",
            target_doc_id,
        )

    # ── Phase 3b: patch config_PEP.security_id ────────────────────────
    config_clean = _clean(disc.config_raw)
    if config_clean.get("security_id") == target_sid:
        report.config_already_correct = True
        logger.info("config_%s.security_id already correct — skip", ticker)
    else:
        config_patched = dict(config_clean)
        config_patched["security_id"] = target_sid
        if _etag_replace(
            symbols_container, config_clean["id"], config_patched, _etag(disc.config_raw)
        ):
            report.config_patched = True
            logger.info("Patched %s.security_id = %s", config_clean["id"], target_sid)
        else:
            report.cas_conflicts += 1
            report.error_details.append(
                f"CAS conflict patching {config_clean['id']} — re-run to retry"
            )

    # ── Phase 3c: patch each ledger_txn ──────────────────────────────
    for raw in disc.ledger_txns_raw:
        doc = _clean(raw)
        doc_id = doc["id"]
        account_id = doc.get("account_id", "")

        if doc.get("security_id") == target_sid:
            report.ledger_already_correct += 1
            continue

        patched = dict(doc)
        patched["security_id"] = target_sid
        try:
            if _etag_replace(portfolio_container, doc_id, patched, _etag(raw)):
                report.ledger_patched += 1
                logger.info("Patched ledger_txn %s.security_id = %s", doc_id, target_sid)
            else:
                report.cas_conflicts += 1
                logger.warning("CAS conflict on ledger_txn %s — will retry on re-run", doc_id)
        except Exception as exc:  # noqa: BLE001
            report.errors += 1
            report.error_details.append(f"ledger_txn {doc_id}: {exc}")

    # ── Phase 3d: patch import_session refs ───────────────────────────
    for raw in disc.import_session_refs_raw:
        doc = _clean(raw)
        doc_id = doc["id"]
        modified = False

        res_map = doc.get("resolution_map") or {}
        if isinstance(res_map, dict):
            for k, v in res_map.items():
                if v == from_security_id:
                    res_map[k] = target_sid
                    modified = True

        enrolled = doc.get("enrolled_security_ids") or []
        if isinstance(enrolled, list):
            new_enrolled = [
                target_sid if e == from_security_id else e for e in enrolled
            ]
            if new_enrolled != enrolled:
                doc["enrolled_security_ids"] = new_enrolled
                modified = True

        if modified:
            try:
                if _etag_replace(portfolio_container, doc_id, doc, _etag(raw)):
                    report.import_session_patched += 1
                    logger.info("Patched import_session %s", doc_id)
                else:
                    report.cas_conflicts += 1
            except Exception as exc:  # noqa: BLE001
                report.errors += 1
                report.error_details.append(f"import_session {doc_id}: {exc}")

    # ── Phase 4: post-write verification ─────────────────────────────
    # If CAS conflicts occurred, skip verification — those movements need a
    # re-run to patch.  Source is NOT deleted; repair is resumable (exit_code=0).
    if report.cas_conflicts > 0:
        logger.warning(
            "%d CAS conflict(s) on ledger_txns — repair is partial/resumable; "
            "source NOT deleted; re-run to complete",
            report.cas_conflicts,
        )
        return report

    try:
        verification_errors = _verify_repair(
            portfolio_container,
            from_security_id,
            target_sid,
            disc.holdings_before,
        )
    except Exception as exc:
        raise VerificationError([str(exc)], report=report) from exc

    if verification_errors:
        for err in verification_errors:
            logger.error("VERIFICATION FAILED: %s", err)
        raise VerificationError(verification_errors, report=report)

    # ── Phase 5: delete source security_master ────────────────────────
    if disc.source_sec_raw is None:
        report.source_already_gone = True
        logger.info("Source %s already absent — idempotent skip", source_doc_id)
    else:
        try:
            # Re-read for fresh etag immediately before delete (§5.6)
            fresh_source = symbols_container.read_item(
                item=source_doc_id, partition_key=ticker
            )
            # Re-confirm zero references before deleting
            re_check = list(portfolio_container.query_items(
                query=(
                    "SELECT * FROM c WHERE c.doc_type='ledger_txn' "
                    "AND c.security_id=@security_id"
                ),
                parameters=[{"name": "@security_id", "value": from_security_id}],
                enable_cross_partition_query=True,
            ))
            if re_check:
                raise VerificationError(
                    [
                        f"Pre-delete re-check: {len(re_check)} ledger_txn(s) still "
                        f"reference {from_security_id} — delete aborted"
                    ],
                    report=report,
                )
            fresh_etag = fresh_source.get("_etag", "")
            symbols_container.delete_item(
                item=source_doc_id, partition_key=ticker
            )
            report.source_deleted = True
            logger.info("Deleted source %s", source_doc_id)
        except CosmosResourceNotFoundError:
            report.source_already_gone = True
            logger.info("Source %s already absent — idempotent skip", source_doc_id)
        except VerificationError:
            raise
        except Exception as exc:  # noqa: BLE001
            report.errors += 1
            report.error_details.append(f"delete {source_doc_id}: {exc}")

    report.exit_code = 0
    return report


def run_restore(
    symbols_container,
    portfolio_container,
    backup_path: Path,
) -> RestoreReport:
    """Restore each backed-up document to its pre-repair state (§7 --restore).

    Unlike migrate_legacy_symbol_config.py (unconditional replace), this
    restore first reads the live state and only writes back if the document
    still reflects the post-migration state — protecting legitimate concurrent
    edits (e.g. a correction transaction entered after the repair).

    Routing: 'container' field in each backup entry determines which container
    receives the restore write.
    """
    from azure.cosmos.exceptions import CosmosResourceNotFoundError, CosmosHttpResponseError
    from azure.core import MatchConditions

    backup = read_backup(backup_path)
    report = RestoreReport()
    from_sid = backup.from_security_id
    to_sid = backup.to_security_id
    from_ticker = security_id_to_ticker(from_sid)
    target_doc_id = security_id_to_doc_id(to_sid)

    for entry in backup.documents:
        doc_id = entry["id"]
        partition_key = entry["partition_key"]
        body = entry["body"]
        container_name = entry.get("container", "symbols")
        container = symbols_container if container_name == "symbols" else portfolio_container

        # Migration-created target security_master: delete it (undo create)
        if doc_id == target_doc_id and body.get("migrated_from") == from_sid:
            try:
                live = container.read_item(item=doc_id, partition_key=partition_key)
                # Only delete if it still has the migration marker
                if _clean(live).get("migrated_from") == from_sid:
                    container.delete_item(item=doc_id, partition_key=partition_key)
                    report.deleted_migration_docs += 1
                    logger.info("Deleted migration-created target: %s", doc_id)
                else:
                    report.skipped_already_restored += 1
                    logger.info("Target %s modified after migration — skipping", doc_id)
            except CosmosResourceNotFoundError:
                logger.info("Migration-created target %s already absent — ok", doc_id)
            except Exception as exc:  # noqa: BLE001
                report.errors += 1
                report.error_details.append(f"delete {doc_id}: {exc}")
            continue

        # Source security_master (sec_NNYS_PEP): recreate if deleted by repair
        source_doc_id_local = security_id_to_doc_id(from_sid)
        if doc_id == source_doc_id_local:
            try:
                container.read_item(item=doc_id, partition_key=partition_key)
                # Already exists — skip
                report.skipped_already_restored += 1
            except CosmosResourceNotFoundError:
                try:
                    container.create_item(_clean(body))
                    report.reverted += 1
                    logger.info("Recreated source security_master: %s", doc_id)
                except Exception as exc:  # noqa: BLE001
                    report.errors += 1
                    report.error_details.append(f"recreate {doc_id}: {exc}")
            continue

        # All other docs (config + ledger_txn + import_session):
        # Read live state; only restore if it reflects the post-repair state.
        try:
            live_raw = container.read_item(item=doc_id, partition_key=partition_key)
            live_clean = _clean(live_raw)
            live_etag = live_raw.get("_etag", "")

            # Detect whether this doc was modified by the repair
            live_sid = live_clean.get("security_id", "")
            backup_sid = body.get("security_id", "")

            if live_sid == to_sid and backup_sid != to_sid:
                # Doc was patched by repair — restore
                restored_body = {**live_clean, "security_id": backup_sid}
                # For config_PEP, restore also removes security_id if it wasn't set
                if backup_sid == "":
                    restored_body.pop("security_id", None)

                try:
                    container.replace_item(
                        item=doc_id,
                        body=_clean(restored_body),
                        etag=live_etag,
                        match_condition=MatchConditions.IfNotModified,
                    )
                    report.reverted += 1
                    logger.info("Restored: %s", doc_id)
                except CosmosHttpResponseError as exc:
                    if exc.status_code in (409, 412):
                        report.cas_conflicts += 1
                        logger.warning("CAS conflict restoring %s — skipping", doc_id)
                    else:
                        report.errors += 1
                        report.error_details.append(f"restore {doc_id}: {exc}")
            else:
                # Doc already at pre-repair state or was legitimately modified
                report.skipped_already_restored += 1
                logger.debug(
                    "Skipped %s: live_sid=%r, backup_sid=%r (already restored or legitimately edited)",
                    doc_id, live_sid, backup_sid,
                )

        except CosmosResourceNotFoundError:
            # Shouldn't happen for ledger docs (they're never deleted by repair)
            report.errors += 1
            report.error_details.append(f"read {doc_id}: not found during restore")
        except Exception as exc:  # noqa: BLE001
            report.errors += 1
            report.error_details.append(f"restore {doc_id}: {exc}")

    return report


# ---------------------------------------------------------------------------
# Public API — names expected by Basher's test contract
# ---------------------------------------------------------------------------

def audit_repair(
    symbols_container,
    portfolio_container,
    from_security_id: str = DEFAULT_FROM_SECURITY_ID,
    to_security_id: Optional[str] = None,
    listing_currency: Optional[str] = None,
    yf_fetcher: Optional[Any] = None,
) -> RepairReport:
    """Public alias: audit without writing anything."""
    return run_audit(
        symbols_container, portfolio_container, from_security_id, to_security_id,
        listing_currency=listing_currency, yf_fetcher=yf_fetcher,
    )


def apply_repair(
    symbols_container,
    portfolio_container,
    dry_run: bool = True,
    from_security_id: str = DEFAULT_FROM_SECURITY_ID,
    to_security_id: Optional[str] = None,
    backup_dir: str = DEFAULT_BACKUP_DIR,
    listing_currency: Optional[str] = None,
    yf_fetcher: Optional[Any] = None,
) -> RepairReport:
    """Public alias: run audit (if dry_run=True) or full apply.

    Never raises — exceptions from RepairAbort or VerificationError are caught
    and surfaced as the returned report with appropriate exit_code.
    """
    if dry_run:
        return run_audit(
            symbols_container, portfolio_container, from_security_id, to_security_id,
            listing_currency=listing_currency, yf_fetcher=yf_fetcher,
        )
    try:
        report = run_apply(
            symbols_container, portfolio_container,
            from_security_id=from_security_id,
            to_security_id=to_security_id,
            backup_dir=backup_dir,
            listing_currency=listing_currency,
            yf_fetcher=yf_fetcher,
        )
        report.exit_code = 0
        return report
    except RepairAbort as exc:
        report = RepairReport(from_security_id=from_security_id, to_security_id=to_security_id or "")
        report.abort_reason = str(exc)
        report.exit_code = 2
        return report
    except VerificationError as exc:
        report = exc.report if exc.report is not None \
            else RepairReport(from_security_id=from_security_id, to_security_id=to_security_id or "")
        report.verification_failed = True
        report.exit_code = 3
        return report


def backup_repair(
    symbols_container,
    portfolio_container,
    path=None,
    from_security_id: str = DEFAULT_FROM_SECURITY_ID,
    to_security_id: Optional[str] = None,
    listing_currency: Optional[str] = None,
    yf_fetcher: Optional[Any] = None,
) -> "RepairBackup":
    """Public alias: discover all relevant docs and build a RepairBackup object.

    Does not write to disk. Returns the RepairBackup instance so callers can
    inspect it or pass it to write_backup() separately.
    """
    disc = discover(
        symbols_container, portfolio_container, from_security_id, to_security_id,
        listing_currency=listing_currency, yf_fetcher=yf_fetcher,
    )
    return build_backup(disc)


def restore_repair(
    symbols_container,
    portfolio_container,
    backup_path,
) -> "RestoreReport":
    """Public alias: restore containers to the pre-repair state from backup_path."""
    return run_restore(symbols_container, portfolio_container, backup_path)


# ---------------------------------------------------------------------------
# Report printing
# ---------------------------------------------------------------------------

def _print_report(report: RepairReport, *, mode: str) -> None:
    print(f"\nrepair_pep_security_id — {mode}")
    print(f"  from: {report.from_security_id}  →  to: {report.to_security_id}")
    if report.abort_reason:
        print(f"  ABORT: {report.abort_reason}")
    print(f"  source_security_master_found: {report.source_security_master_found}")
    print(f"  target_pre_existed:           {report.target_security_master_pre_existed}")
    print(f"  ledger_txn_found:             {report.ledger_txn_found}")
    print(f"  import_session_refs_found:    {report.import_session_refs_found}")
    print(f"  ledger_accounting_currency_note: {report.ledger_accounting_currency_note}")
    print(f"  provider_currency_verdict:    {report.provider_currency_verdict}")
    if mode != "AUDIT (dry-run)":
        print(f"  target_created:               {report.target_created}")
        print(f"  config_patched:               {report.config_patched}")
        print(f"  config_already_correct:       {report.config_already_correct}")
        print(f"  ledger_patched:               {report.ledger_patched}")
        print(f"  ledger_already_correct:       {report.ledger_already_correct}")
        print(f"  import_session_patched:       {report.import_session_patched}")
        print(f"  source_deleted:               {report.source_deleted}")
        print(f"  source_already_gone:          {report.source_already_gone}")
    print(f"  cas_conflicts:                {report.cas_conflicts}")
    print(f"  errors:                       {report.errors}")
    for e in report.error_details:
        print(f"    {e}")
    print()


def _print_restore_report(report: RestoreReport) -> None:
    print("\nrepair_pep_security_id — RESTORE")
    print(f"  reverted:                {report.reverted}")
    print(f"  skipped_already_restored:{report.skipped_already_restored}")
    print(f"  deleted_migration_docs:  {report.deleted_migration_docs}")
    print(f"  cas_conflicts:           {report.cas_conflicts}")
    print(f"  errors:                  {report.errors}")
    for e in report.error_details:
        print(f"    {e}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_cosmos(database: str, symbols_cname: str, portfolio_cname: str):
    """Build real Cosmos container objects from environment variables.

    Only called from main() — tests always inject fake containers.
    """
    import os
    from azure.cosmos import CosmosClient

    endpoint = os.environ.get("COSMOSDB_ENDPOINT")
    key = os.environ.get("COSMOSDB_KEY")
    if not endpoint or not key:
        logger.error(
            "COSMOSDB_ENDPOINT and COSMOSDB_KEY must be set for --apply / --restore."
        )
        sys.exit(2)
    client = CosmosClient(endpoint, credential=key)
    db = client.get_database_client(database)
    return (
        db.get_container_client(symbols_cname),
        db.get_container_client(portfolio_cname),
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repair_pep_security_id",
        description=(
            "Repair the NNYS:PEP → XNAS:PEP security identity corruption. "
            "Default mode is --audit (read-only)."
        ),
    )
    mode_grp = parser.add_mutually_exclusive_group()
    mode_grp.add_argument(
        "--audit",
        action="store_true",
        help="(default) Read-only audit; report what would change.",
    )
    mode_grp.add_argument(
        "--backup-only",
        dest="backup_only",
        action="store_true",
        help="Back up discovered documents without writing.",
    )
    mode_grp.add_argument(
        "--apply",
        action="store_true",
        help="Backup (mandatory), then repair, then verify.",
    )
    mode_grp.add_argument(
        "--restore",
        metavar="BACKUP_FILE",
        default=None,
        help="Restore to pre-repair state from a backup file.",
    )
    parser.add_argument(
        "--from-security-id",
        dest="from_security_id",
        default=DEFAULT_FROM_SECURITY_ID,
        help=f"Source (invalid) security_id. Default: {DEFAULT_FROM_SECURITY_ID}",
    )
    parser.add_argument(
        "--to-security-id",
        dest="to_security_id",
        default=None,
        help=(
            "Target security_id. Default: derived at runtime from "
            "config_<TICKER>.exchange via LEGACY_ALIAS_TO_MIC."
        ),
    )
    parser.add_argument(
        "--backup-dir",
        dest="backup_dir",
        default=str(DEFAULT_BACKUP_DIR),
        help=f"Directory for backup files. Default: {DEFAULT_BACKUP_DIR}",
    )
    parser.add_argument(
        "--database",
        default="option-income-lab",
        help="Cosmos database name.",
    )
    parser.add_argument(
        "--symbols-container",
        dest="symbols_container",
        default="symbols",
        help="Symbols container name.",
    )
    parser.add_argument(
        "--portfolio-container",
        dest="portfolio_container",
        default="portfolio",
        help="Portfolio container name.",
    )
    parser.add_argument(
        "--listing-currency",
        dest="listing_currency",
        default=None,
        metavar="CUR",
        help=(
            "Optional target listing_currency (e.g. USD). If omitted, the "
            "existing source listing_currency is preserved unchanged — no "
            "currency correction is made. If provided, --apply performs a "
            "mandatory live YFinanceFetcher('PEP') verification (currency == "
            "financialCurrency == CUR, and provider exchange corroborates "
            "the target MIC) BEFORE any backup/mutation; a failed/mismatched/"
            "unreachable check aborts with exit code 2. --audit reports the "
            "same check best-effort without writing. "
            "Example: --apply --listing-currency USD"
        ),
    )
    return parser


def main(argv=None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    backup_dir = Path(args.backup_dir)

    is_audit = args.audit or (
        not args.backup_only and not args.apply and args.restore is None
    )

    if is_audit:
        symbols_c, portfolio_c = _build_cosmos(
            args.database, args.symbols_container, args.portfolio_container
        )
        report = run_audit(
            symbols_c, portfolio_c, args.from_security_id, args.to_security_id,
            listing_currency=args.listing_currency,
        )
        _print_report(report, mode="AUDIT (dry-run)")
        return report.exit_code

    if args.backup_only:
        symbols_c, portfolio_c = _build_cosmos(
            args.database, args.symbols_container, args.portfolio_container
        )
        try:
            path = run_backup_only(
                symbols_c, portfolio_c,
                args.from_security_id, args.to_security_id, backup_dir,
                listing_currency=args.listing_currency,
            )
            print(f"Backup written: {path}")
        except RepairAbort as exc:
            logger.error("Backup aborted: %s", exc.reason)
            return exc.exit_code
        except Exception as exc:  # noqa: BLE001
            logger.error("Backup failed: %s", exc)
            return 2
        return 0

    if args.apply:
        symbols_c, portfolio_c = _build_cosmos(
            args.database, args.symbols_container, args.portfolio_container
        )
        try:
            report = run_apply(
                symbols_c, portfolio_c,
                args.from_security_id, args.to_security_id, backup_dir,
                listing_currency=args.listing_currency,
            )
            _print_report(report, mode="APPLY (writes performed)")
            return 0
        except RepairAbort as exc:
            logger.error("Repair aborted: %s", exc.reason)
            return exc.exit_code
        except VerificationError as exc:
            logger.error("Post-repair verification failed: %s", exc)
            return 3
        except Exception as exc:  # noqa: BLE001
            logger.error("Backup/apply failed: %s", exc)
            return 2

    if args.restore:
        symbols_c, portfolio_c = _build_cosmos(
            args.database, args.symbols_container, args.portfolio_container
        )
        restore_path = Path(args.restore)
        if not restore_path.exists():
            logger.error("Backup file not found: %s", restore_path)
            return 1
        try:
            report = run_restore(symbols_c, portfolio_c, restore_path)
        except ValueError as exc:
            logger.error("Restore aborted: %s", exc)
            return 2
        _print_restore_report(report)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
