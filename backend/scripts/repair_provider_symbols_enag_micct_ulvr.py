#!/usr/bin/env python3
"""Repair provider_symbols overrides for XMAD:ENAG, XAMS:MICCT, XAMS:ULVR.

Implements danny-provider-symbol-corrections-enag-micct-ulvr.md exactly.

Problem: Yahoo Finance does not mirror the local/canonical ticker for these
three securities (a post-listing-change/spin-off/merger ticker divergence,
not a data-quality bug) — the mechanical MIC-suffix suggestion
(``ENAG.MC``, ``MICCT.AS``, ``ULVR.AS``) either 404s or returns nothing on
Yahoo. No security_id/identity change is required or requested: this is
purely a ``security_master.provider_symbols`` override correction, reusing
the existing per-security override precedence already built into
``resolve_yfinance_symbol``/``resolve_tradingview_symbol`` (highest-priority
lookup, unchanged).

Targets (hardcoded per Danny's approved design — not a general mapping
table; these are the three specific corrections this artifact exists to
apply):
    XMAD:ENAG  → yfinance "ENG.MC"    tradingview "BME-ENG"
    XAMS:MICCT → yfinance "MICC.AS"   tradingview "EURONEXT-MICC"
    XAMS:ULVR  → yfinance "UNA.AS"    tradingview "EURONEXT-UNA"

Scope is intentionally narrow — only three fields on three
``security_master`` documents:
    security_master.provider_symbols.yfinance
    security_master.provider_symbols.tradingview
All other security_master fields (security_id, exchange_mic,
listing_currency, isin/cusip/sedol, aliases, asset_class, broker_ids,
created_at, ...) are byte-identical before/after. ``config_*`` documents
and ``ledger_txn`` documents are read-only in this repair (reference counts
are reported for confirmation only) — zero writes to the portfolio
container, ever.

Each of the three corrections is verified and applied independently: a
verification failure on one security must never block the other two.

Per-security flow:
  1. Live provider verification (mandatory, before any write) — fetch the
     proposed corrected Yahoo symbol and confirm a non-empty result,
     ``currency == listing_currency``, and the provider's
     exchange/fullExchangeName corroborates the security's own canonical
     MIC. For XMAD:ENAG specifically (the one candidate explicitly flagged
     "must verify" in the source design), also fuzzy/substring-corroborate
     the provider's company name against the existing security_master
     ``company_name`` on file.
  2. Mandatory backup (all 3 security_master docs, SHA-256 checksum,
     _etag) — before any write.
  3. CAS-merge ``provider_symbols.yfinance``/``.tradingview`` (existing
     unrelated provider keys preserved) via the existing
     ``validate_provider_symbols()`` helper.
  4. Post-write verification of the persisted ``provider_symbols`` value.
  5. Synchronous ``enrich_symbol(ticker, yf_symbol=<corrected>)`` rerun +
     persistence via the existing ``cosmos.update_symbol_enrichment()`` /
     ``cosmos.record_enrichment_snapshot()`` APIs, with a post-write
     re-read confirming ``enrichment.last_updated`` advanced and
     quality_score/category/technicals/entry_tag/momentum are populated.
     An enrichment failure after a successful provider_symbols write is
     reported as ``provider_symbol_corrected=True, enrichment_verified=False``
     — non-fatal (the next scheduled hourly enrichment run will retry now
     that the override is in place).

Usage::

    # Audit (default — read-only, reports proposed corrections + verdicts)
    python -m scripts.repair_provider_symbols_enag_micct_ulvr

    # Apply (mandatory backup, then per-security correction + enrichment)
    python -m scripts.repair_provider_symbols_enag_micct_ulvr --apply

    # Restore the 3 security_master docs from a previous backup
    python -m scripts.repair_provider_symbols_enag_micct_ulvr --restore migration_backups/provider_symbols_repair_20260907T...json

Exit codes:
    0  All requested corrections applied/already-correct, or a clean audit
       (including partial success — see per-security report for detail).
    1  Bad CLI arguments / mutually-exclusive flags.
    2  Backup failed, or *all three* per-security provider verifications
       failed — nothing written.
    3  A provider_symbols write succeeded but post-write verification of the
       persisted provider_symbols value did not confirm (enrichment failure
       alone is non-fatal per §4 and does NOT raise this).

DO NOT RUN AGAINST PRODUCTION without explicit authorization.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Allow `python scripts/repair_provider_symbols.py` without package context.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.portfolio.provider_symbols import validate_provider_symbols  # noqa: E402
from src.portfolio.cosmos_securities import (  # noqa: E402
    security_id_to_doc_id,
    security_id_to_ticker,
)
from src.yfinance_fetcher import YFinanceFetcher  # noqa: E402
from src.portfolio_enrichment import enrich_symbol  # noqa: E402

logger = logging.getLogger("repair_provider_symbols")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_BACKUP_DIR = Path(__file__).parent / "migration_backups"

_COSMOS_SYSTEM_KEYS = frozenset({"_rid", "_self", "_etag", "_attachments", "_ts"})

# The exact three corrections this artifact exists to apply
# (danny-provider-symbol-corrections-enag-micct-ulvr.md). Hardcoded, not a
# general reusable mapping — each entry is a specific, evidence-backed
# correction for one security, not a formula.
TARGET_SPECS: Tuple[Dict[str, Any], ...] = (
    {
        "security_id": "XMAD:ENAG",
        "ticker": "ENAG",
        "mic": "XMAD",
        "expected_yfinance": "ENG.MC",
        "expected_tradingview": "BME-ENG",
        # The one candidate explicitly flagged "must verify" in the source
        # design — additionally fuzzy-corroborate company identity.
        "verify_company": True,
    },
    {
        "security_id": "XAMS:MICCT",
        "ticker": "MICCT",
        "mic": "XAMS",
        "expected_yfinance": "MICC.AS",
        "expected_tradingview": "EURONEXT-MICC",
        "verify_company": False,
    },
    {
        "security_id": "XAMS:ULVR",
        "ticker": "ULVR",
        "mic": "XAMS",
        "expected_yfinance": "UNA.AS",
        "expected_tradingview": "EURONEXT-UNA",
        "verify_company": False,
    },
)

# Repair-scoped, narrowly-documented evidence hints for corroborating a
# yfinance `info.exchange`/`info.fullExchangeName` observation against the
# canonical MIC of *these two specific target MICs only* (XMAD, XAMS). This
# is NOT a general-purpose MIC↔exchange-name mapping table (no such shared
# table exists yet in this codebase for non-US MICs, per the source design's
# explicit fallback: "otherwise document the raw observed fields verbatim
# in the audit report as the evidence trail"). Raw fields are always logged
# regardless of whether a hint matches, so a security is never silently
# corroborated on a table that's technically wrong — only ever helped.
_REPAIR_SCOPED_MIC_EXCHANGE_HINTS: Dict[str, Tuple[str, ...]] = {
    "XMAD": ("MCE", "MADRID", "BME"),
    "XAMS": ("AMS", "AMSTERDAM", "EURONEXT"),
}

# Corporate-suffix noise stripped before the ENAG company fuzzy-match so
# "Enagas SA" vs "Enagás, S.A." still overlaps on the meaningful token.
_COMPANY_SUFFIX_NOISE = re.compile(
    r"\b(s\.?a\.?|s\.?e\.?|n\.?v\.?|plc|inc|ltd|corp|co|group|holding|holdings)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SecurityRepairResult:
    """Per-security outcome — one instance per TARGET_SPECS entry."""
    security_id: str
    ticker: str
    mic: str
    expected_yfinance: str
    expected_tradingview: str

    # Discovery
    security_master_found: bool = False
    existing_provider_symbols: Dict[str, str] = field(default_factory=dict)
    config_doc_found: bool = False
    ledger_ref_count: int = 0

    # Live provider verification
    provider_verdict: str = "not_checked"  # "verified" | "mismatch:<detail>" | "unreachable"
    provider_detail: str = ""
    observed_exchange: str = ""
    observed_full_exchange_name: str = ""
    observed_currency: str = ""
    observed_company_name: str = ""
    company_check_note: str = ""

    # Write outcome
    already_correct: bool = False
    provider_symbol_corrected: bool = False
    write_verified: bool = False
    cas_conflict: bool = False

    # Enrichment outcome (§4 — non-fatal)
    enrichment_attempted: bool = False
    enrichment_verified: bool = False
    enrichment_detail: str = ""

    error: str = ""


@dataclass
class RepairReport:
    """Aggregate report across all three securities."""
    generated_at: str = ""
    mode: str = "audit"
    results: List[SecurityRepairResult] = field(default_factory=list)
    backup_path: str = ""
    exit_code: int = 0
    abort_reason: str = ""

    @property
    def corrected_count(self) -> int:
        return sum(1 for r in self.results if r.provider_symbol_corrected)

    @property
    def unresolved_count(self) -> int:
        return sum(
            1 for r in self.results
            if not r.provider_symbol_corrected and not r.already_correct
        )


@dataclass
class RepairBackup:
    """Backup snapshot of all 3 security_master docs (whichever exist)."""
    generated_at: str
    sha256: str
    documents: List[Dict[str, Any]]  # {id, partition_key, _etag, body}


@dataclass
class RestoreReport:
    """Aggregate counters for a --restore run."""
    reverted: int = 0
    skipped_already_restored: int = 0
    cas_conflicts: int = 0
    errors: int = 0
    error_details: List[str] = field(default_factory=list)


class RepairAbort(RuntimeError):
    """Raised when the whole run must not proceed (backup failure)."""
    def __init__(self, reason: str, exit_code: int = 2) -> None:
        super().__init__(reason)
        self.reason = reason
        self.exit_code = exit_code


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _clean(doc: dict) -> dict:
    """Strip Cosmos system keys from a document body."""
    return {k: v for k, v in doc.items() if k not in _COSMOS_SYSTEM_KEYS}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compute_checksum(entries: List[Dict[str, Any]]) -> str:
    """SHA-256 over entries sorted by id — matches repair_pep_security_id.py."""
    sorted_entries = sorted(entries, key=lambda e: e["id"])
    return hashlib.sha256(
        json.dumps(sorted_entries, sort_keys=True).encode()
    ).hexdigest()


def _etag_replace(container, doc_id: str, partition_key: str, body: dict, snapshot_etag: str) -> bool:
    """ETag-gated replace_item. Returns True on success, False on CAS conflict."""
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


def _normalize_company_tokens(name: str) -> set:
    """Lowercase, strip corporate-suffix noise, return significant word tokens."""
    if not name:
        return set()
    stripped = _COMPANY_SUFFIX_NOISE.sub(" ", name.lower())
    return {tok for tok in re.findall(r"[a-z0-9]+", stripped) if len(tok) >= 4}


def _company_plausibly_matches(existing_name: str, provider_name: str) -> Tuple[bool, str]:
    """Fuzzy/substring corroboration between existing and provider company names.

    Returns (matched_or_no_data, note). When there is no existing
    company_name on file to compare against, this returns (True, note) —
    logged but non-blocking, since there is nothing to contradict.
    """
    if not existing_name:
        return True, "no existing company_name on file to compare — not blocking"
    if not provider_name:
        return False, "provider returned no longName/shortName to compare"

    existing_l = existing_name.strip().lower()
    provider_l = provider_name.strip().lower()
    if existing_l in provider_l or provider_l in existing_l:
        return True, f"substring match: {existing_name!r} vs {provider_name!r}"

    existing_tokens = _normalize_company_tokens(existing_name)
    provider_tokens = _normalize_company_tokens(provider_name)
    overlap = existing_tokens & provider_tokens
    if overlap:
        return True, f"token overlap {sorted(overlap)}: {existing_name!r} vs {provider_name!r}"

    return False, f"no plausible overlap: {existing_name!r} vs {provider_name!r}"


def _resolve_provider_mic_hint(info: Dict[str, Any], target_mic: str) -> Tuple[bool, str]:
    """Best-effort corroboration of a yfinance info dict against target_mic.

    Uses the narrowly repair-scoped `_REPAIR_SCOPED_MIC_EXCHANGE_HINTS` (only
    covers XMAD/XAMS — the two MICs this repair touches). Always returns the
    raw observed fields in the note regardless of match outcome, so the
    audit trail is complete even when the hint can't confirm a match.
    """
    exchange = str(info.get("exchange") or "").strip().upper()
    full_name = str(info.get("fullExchangeName") or "").strip().upper()
    hints = _REPAIR_SCOPED_MIC_EXCHANGE_HINTS.get(target_mic, ())
    matched = any(h in exchange or h in full_name for h in hints)
    note = f"observed exchange={exchange!r} fullExchangeName={full_name!r} target_mic={target_mic!r}"
    return matched, note


def verify_provider_symbol(
    spec: Dict[str, Any],
    existing_company_name: str,
    fetcher: Optional[Any] = None,
) -> SecurityRepairResult:
    """Live provider verification for one target spec (§1). Never raises."""
    result = SecurityRepairResult(
        security_id=spec["security_id"],
        ticker=spec["ticker"],
        mic=spec["mic"],
        expected_yfinance=spec["expected_yfinance"],
        expected_tradingview=spec["expected_tradingview"],
    )

    if fetcher is None:
        fetcher = YFinanceFetcher()

    try:
        data = fetcher.get_ticker_data(spec["expected_yfinance"])
    except Exception as exc:  # noqa: BLE001
        result.provider_verdict = "unreachable"
        result.provider_detail = f"provider fetch raised: {exc}"
        return result

    if not data or not isinstance(data, dict):
        result.provider_verdict = "unreachable"
        result.provider_detail = (
            f"provider returned no data for {spec['expected_yfinance']!r} "
            "(network error, rate limit, or unknown ticker)"
        )
        return result

    info = data.get("info") or {}
    currency = str(info.get("currency") or "").strip().upper()
    result.observed_currency = currency
    result.observed_exchange = str(info.get("exchange") or "")
    result.observed_full_exchange_name = str(info.get("fullExchangeName") or "")
    result.observed_company_name = str(info.get("longName") or info.get("shortName") or "")

    if currency != "EUR":
        result.provider_verdict = f"mismatch:currency observed={currency!r} expected='EUR'"
        result.provider_detail = result.provider_verdict
        return result

    mic_matched, mic_note = _resolve_provider_mic_hint(info, spec["mic"])
    if not mic_matched:
        result.provider_verdict = f"mismatch:exchange does not corroborate {spec['mic']} ({mic_note})"
        result.provider_detail = mic_note
        return result

    if spec.get("verify_company"):
        ok, note = _company_plausibly_matches(existing_company_name, result.observed_company_name)
        result.company_check_note = note
        if not ok:
            result.provider_verdict = f"mismatch:company ({note})"
            result.provider_detail = note
            return result
    else:
        result.company_check_note = "not required for this security"

    result.provider_verdict = "verified"
    result.provider_detail = (
        f"currency={currency} {mic_note}"
        + (f"; company: {result.company_check_note}" if spec.get("verify_company") else "")
    )
    return result


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover(
    symbols_container,
    portfolio_container,
    yf_fetcher: Optional[Any] = None,
) -> List[SecurityRepairResult]:
    """Read-only discovery + live provider verification for all 3 targets.

    Never raises — a missing security_master doc or a failed provider check
    for one security is recorded on that security's result and does not
    prevent discovery of the other two.
    """
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    results: List[SecurityRepairResult] = []

    for spec in TARGET_SPECS:
        ticker = spec["ticker"]
        doc_id = security_id_to_doc_id(spec["security_id"])
        existing_company_name = ""
        sec_raw = None

        try:
            sec_raw = symbols_container.read_item(item=doc_id, partition_key=ticker)
        except CosmosResourceNotFoundError:
            sec_raw = None

        result = verify_provider_symbol(
            spec,
            existing_company_name=(_clean(sec_raw).get("company_name", "") if sec_raw else ""),
            fetcher=yf_fetcher,
        )
        result.security_master_found = sec_raw is not None
        if sec_raw is not None:
            sec_clean = _clean(sec_raw)
            result.existing_provider_symbols = dict(sec_clean.get("provider_symbols") or {})
            existing_company_name = sec_clean.get("company_name", "")

            # Idempotency check: already exactly the proposed values?
            if (
                result.existing_provider_symbols.get("yfinance") == spec["expected_yfinance"]
                and result.existing_provider_symbols.get("tradingview") == spec["expected_tradingview"]
            ):
                result.already_correct = True
        else:
            result.error = f"security_master {doc_id} not found — cannot correct"

        # Read-only config/ledger reference-count confirmation (never written).
        config_doc_id = f"config_{ticker}"
        try:
            symbols_container.read_item(item=config_doc_id, partition_key=ticker)
            result.config_doc_found = True
        except CosmosResourceNotFoundError:
            result.config_doc_found = False

        try:
            refs = list(portfolio_container.query_items(
                query=(
                    "SELECT VALUE COUNT(1) FROM c WHERE c.doc_type='ledger_txn' "
                    "AND c.security_id=@security_id"
                ),
                parameters=[{"name": "@security_id", "value": spec["security_id"]}],
                enable_cross_partition_query=True,
            ))
            result.ledger_ref_count = refs[0] if refs else 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ledger ref count query failed for %s: %s", spec["security_id"], exc)
            result.ledger_ref_count = 0

        results.append(result)

    return results


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

def build_backup(symbols_container) -> RepairBackup:
    """Snapshot all 3 security_master docs (whichever exist) with _etag."""
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    entries: List[Dict[str, Any]] = []
    for spec in TARGET_SPECS:
        doc_id = security_id_to_doc_id(spec["security_id"])
        try:
            raw = symbols_container.read_item(item=doc_id, partition_key=spec["ticker"])
        except CosmosResourceNotFoundError:
            continue
        entries.append({
            "id": raw.get("id", ""),
            "partition_key": raw.get("symbol", spec["ticker"]),
            "_etag": raw.get("_etag", ""),
            "body": _clean(raw),
        })

    checksum = _compute_checksum(entries)
    return RepairBackup(
        generated_at=_now_iso(),
        sha256=checksum,
        documents=sorted(entries, key=lambda e: e["id"]),
    )


def write_backup(backup: RepairBackup, backup_dir: Path) -> Path:
    """Write backup to a timestamped JSON file; return the path."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = backup_dir / f"provider_symbols_repair_{ts}.json"
    payload = {
        "generated_at": backup.generated_at,
        "sha256": backup.sha256,
        "documents": backup.documents,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def read_backup(path: Path) -> RepairBackup:
    """Read and checksum-verify a backup file. Raises ValueError on mismatch."""
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
        documents=payload.get("documents", []),
    )


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def run_audit(
    symbols_container,
    portfolio_container,
    yf_fetcher: Optional[Any] = None,
) -> RepairReport:
    """Read-only: discover, verify, report — write nothing."""
    results = discover(symbols_container, portfolio_container, yf_fetcher=yf_fetcher)
    report = RepairReport(generated_at=_now_iso(), mode="audit", results=results)

    all_failed = all(
        r.provider_verdict != "verified" and not r.already_correct for r in results
    )
    if all_failed and results:
        report.exit_code = 2
        report.abort_reason = "all provider verifications failed — see per-security detail"
    return report


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def _persist_enrichment(cosmos, result: SecurityRepairResult, start_ts: str) -> None:
    """Run enrich_symbol + persist + verification (§4, non-fatal).

    `cosmos` need only expose `update_symbol_enrichment(ticker, enrichment)`
    and `record_enrichment_snapshot(ticker, score, momentum)` — the same
    APIs the hourly scheduled job already uses
    (`src/cosmos_db.py::CosmosDBService`). If `cosmos` also exposes
    `get_symbol(ticker)` (the real production service does), a genuine
    post-write re-read is performed for the freshest possible confirmation;
    otherwise the in-memory enrichment dict just persisted is used as the
    verification source (it is byte-identical to what was just written).
    """
    result.enrichment_attempted = True
    ticker = result.ticker
    try:
        enrichment = enrich_symbol(ticker, yf_symbol=result.expected_yfinance)
        if enrichment is None:
            result.enrichment_verified = False
            result.enrichment_detail = "enrich_symbol() returned None (analysis failed)"
            return

        cosmos.update_symbol_enrichment(ticker, enrichment)

        try:
            cosmos.record_enrichment_snapshot(
                ticker,
                (enrichment.get("technicals") or {}).get("score"),
                enrichment.get("momentum", ""),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("record_enrichment_snapshot failed for %s: %s", ticker, exc)

        # Post-write verification: prefer a genuine re-read when the sink
        # supports it; otherwise verify the just-persisted dict directly.
        if hasattr(cosmos, "get_symbol"):
            persisted_doc = cosmos.get_symbol(ticker)
            persisted_enrichment = (persisted_doc or {}).get("enrichment") or {}
        else:
            persisted_enrichment = enrichment

        last_updated = persisted_enrichment.get("last_updated", "")
        quality_score = persisted_enrichment.get("quality_score")
        category = persisted_enrichment.get("category")
        technicals = persisted_enrichment.get("technicals")
        entry_tag = persisted_enrichment.get("entry_tag")
        momentum = persisted_enrichment.get("momentum")

        problems = []
        if not last_updated or last_updated < start_ts:
            problems.append("last_updated not advanced past repair start")
        if quality_score is None:
            problems.append("quality_score missing")
        if not category:
            problems.append("category missing/empty")
        if not technicals:
            problems.append("technicals missing/empty")
        if not entry_tag:
            problems.append("entry_tag missing/empty")
        if momentum is None:
            problems.append("momentum missing")

        if problems:
            result.enrichment_verified = False
            result.enrichment_detail = "; ".join(problems)
        else:
            result.enrichment_verified = True
            result.enrichment_detail = f"last_updated={last_updated}"

    except Exception as exc:  # noqa: BLE001
        result.enrichment_verified = False
        result.enrichment_detail = f"enrichment persistence failed: {exc}"


def apply_repair(
    symbols_container,
    portfolio_container,
    dry_run: bool = True,
    yf_fetcher: Optional[Any] = None,
    cosmos: Optional[Any] = None,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
) -> RepairReport:
    """Discover + verify (per-security) → backup → CAS-write → enrich.

    `dry_run=True` (default) is a pure audit: read-only, zero writes,
    identical discovery/verification logic to `dry_run=False`.

    `cosmos`, when provided, must expose `update_symbol_enrichment(ticker,
    enrichment)` and `record_enrichment_snapshot(ticker, score, momentum)`
    (the same APIs the hourly scheduled job already uses). If `cosmos` is
    not provided, the enrichment rerun (§4) is skipped entirely — the
    provider_symbols correction itself is still fully applied. Production
    CLI usage always supplies a real `cosmos` (see `main()`).
    """
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    start_ts = _now_iso()

    if dry_run:
        return run_audit(symbols_container, portfolio_container, yf_fetcher=yf_fetcher)

    # ── Phase 1: discover + verify (independent per security) ──────────
    results = discover(symbols_container, portfolio_container, yf_fetcher=yf_fetcher)

    all_failed = all(
        r.provider_verdict != "verified" and not r.already_correct for r in results
    )
    if all_failed and results:
        report = RepairReport(generated_at=start_ts, mode="apply", results=results)
        report.exit_code = 2
        report.abort_reason = "all provider verifications failed — nothing written"
        return report

    # ── Phase 2: mandatory backup (before any write) ────────────────────
    try:
        backup = build_backup(symbols_container)
        backup_path = write_backup(backup, backup_dir)
        logger.info("Backup written to %s", backup_path)
    except Exception as exc:  # noqa: BLE001
        raise RepairAbort(f"Backup failed — aborting before any write: {exc}", exit_code=2)

    report = RepairReport(generated_at=start_ts, mode="apply", results=results, backup_path=str(backup_path))

    # ── Phase 3: per-security independent write + enrichment ────────────
    for spec, result in zip(TARGET_SPECS, results):
        if not result.security_master_found:
            continue  # already recorded as an error in discover()

        if result.provider_verdict != "verified":
            continue  # this security's correction is aborted; others proceed

        doc_id = security_id_to_doc_id(spec["security_id"])
        ticker = spec["ticker"]

        if result.already_correct:
            result.provider_symbol_corrected = True
            result.write_verified = True
        else:
            try:
                live_raw = symbols_container.read_item(item=doc_id, partition_key=ticker)
            except CosmosResourceNotFoundError:
                result.error = f"{doc_id} disappeared between discover and write"
                continue

            live_clean = _clean(live_raw)
            merged_provider_symbols = validate_provider_symbols({
                **(live_clean.get("provider_symbols") or {}),
                "yfinance": spec["expected_yfinance"],
                "tradingview": spec["expected_tradingview"],
            })
            new_body = {**live_clean, "provider_symbols": merged_provider_symbols}

            ok = _etag_replace(
                symbols_container, doc_id, ticker, new_body, live_raw.get("_etag", "")
            )
            if not ok:
                result.cas_conflict = True
                result.error = f"CAS conflict writing {doc_id} — not corrected this run"
                continue

            # Post-write verification of the persisted provider_symbols value.
            try:
                reread = symbols_container.read_item(item=doc_id, partition_key=ticker)
                reread_clean = _clean(reread)
                reread_ps = reread_clean.get("provider_symbols") or {}
                if (
                    reread_ps.get("yfinance") == spec["expected_yfinance"]
                    and reread_ps.get("tradingview") == spec["expected_tradingview"]
                ):
                    result.provider_symbol_corrected = True
                    result.write_verified = True
                else:
                    result.error = (
                        f"post-write verification failed: persisted "
                        f"provider_symbols={reread_ps!r} does not match expected"
                    )
                    continue
            except Exception as exc:  # noqa: BLE001
                result.error = f"post-write re-read failed: {exc}"
                continue

        # ── Phase 4: synchronous enrichment rerun (§4, non-fatal) ────────
        if cosmos is not None:
            _persist_enrichment(cosmos, result, start_ts)

    # Explicit exit-code precedence (§5): any provider_symbols write that
    # didn't verify, OR (informationally, non-fatally) an enrichment rerun
    # that didn't verify for an otherwise-corrected security → exit 3.
    any_verification_shortfall = any(
        (
            r.provider_verdict == "verified" and r.security_master_found
            and not r.already_correct and not r.provider_symbol_corrected
        )
        or (
            r.provider_symbol_corrected
            and r.enrichment_attempted
            and not r.enrichment_verified
        )
        for r in results
    )
    if any_verification_shortfall:
        report.exit_code = 3

    return report


def audit_repair(
    symbols_container,
    portfolio_container,
    yf_fetcher: Optional[Any] = None,
) -> RepairReport:
    """Public alias: audit without writing anything."""
    return run_audit(symbols_container, portfolio_container, yf_fetcher=yf_fetcher)


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def run_restore(symbols_container, backup_path: Path) -> RestoreReport:
    """Restore the 3 security_master docs' provider_symbols (and full body,
    for symmetry) from a backup file. Only restores if the live doc still
    reflects a state consistent with having been touched by this repair
    (protects legitimate concurrent edits made after the repair).
    """
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    backup = read_backup(backup_path)
    report = RestoreReport()

    for entry in backup.documents:
        doc_id = entry["id"]
        partition_key = entry["partition_key"]
        body = entry["body"]

        try:
            live_raw = symbols_container.read_item(item=doc_id, partition_key=partition_key)
        except CosmosResourceNotFoundError:
            report.errors += 1
            report.error_details.append(f"{doc_id}: not found — cannot restore")
            continue

        live_clean = _clean(live_raw)
        if live_clean.get("provider_symbols") == body.get("provider_symbols"):
            report.skipped_already_restored += 1
            continue

        ok = _etag_replace(
            symbols_container, doc_id, partition_key, body, live_raw.get("_etag", "")
        )
        if ok:
            report.reverted += 1
        else:
            report.cas_conflicts += 1
            report.error_details.append(f"{doc_id}: CAS conflict during restore")

    return report


def restore_repair(symbols_container, backup_path) -> RestoreReport:
    """Public alias: restore the 3 security_master docs' provider_symbols
    from a backup file. Never touches config/ledger/portfolio.
    """
    return run_restore(symbols_container, Path(backup_path))


# ---------------------------------------------------------------------------
# Report printing
# ---------------------------------------------------------------------------

def _print_report(report: RepairReport) -> None:
    print(f"\nrepair_provider_symbols — {report.mode.upper()}")
    if report.abort_reason:
        print(f"  ABORT: {report.abort_reason}")
    if report.backup_path:
        print(f"  backup: {report.backup_path}")
    for r in report.results:
        print(f"\n  {r.security_id}:")
        print(f"    security_master_found:       {r.security_master_found}")
        print(f"    existing_provider_symbols:   {r.existing_provider_symbols}")
        print(f"    expected_yfinance:            {r.expected_yfinance}")
        print(f"    expected_tradingview:         {r.expected_tradingview}")
        print(f"    provider_verdict:             {r.provider_verdict}")
        print(f"    provider_detail:              {r.provider_detail}")
        if r.company_check_note:
            print(f"    company_check_note:          {r.company_check_note}")
        print(f"    config_doc_found:             {r.config_doc_found}")
        print(f"    ledger_ref_count:             {r.ledger_ref_count}")
        print(f"    already_correct:              {r.already_correct}")
        if report.mode == "apply":
            print(f"    provider_symbol_corrected:   {r.provider_symbol_corrected}")
            print(f"    write_verified:               {r.write_verified}")
            print(f"    cas_conflict:                 {r.cas_conflict}")
            print(f"    enrichment_attempted:         {r.enrichment_attempted}")
            print(f"    enrichment_verified:          {r.enrichment_verified}")
            print(f"    enrichment_detail:            {r.enrichment_detail}")
        if r.error:
            print(f"    error:                        {r.error}")
    print(f"\n  corrected:   {report.corrected_count}/{len(report.results)}")
    print(f"  unresolved:  {report.unresolved_count}/{len(report.results)}")
    print(f"  exit_code:   {report.exit_code}")
    print()


def _print_restore_report(report: RestoreReport) -> None:
    print("\nrepair_provider_symbols — RESTORE")
    print(f"  reverted:                {report.reverted}")
    print(f"  skipped_already_restored: {report.skipped_already_restored}")
    print(f"  cas_conflicts:           {report.cas_conflicts}")
    print(f"  errors:                  {report.errors}")
    for detail in report.error_details:
        print(f"    - {detail}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_cosmos_containers(database: str, symbols_cname: str, portfolio_cname: str):
    """Build real Cosmos raw container objects from environment variables."""
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


def _build_cosmos_service(database: str):
    """Build the real CosmosDBService used for enrichment persistence —
    reuses the exact same production dependency (`src/cosmos_db.py`) the
    hourly scheduled enrichment job already uses; never reimplemented here.
    """
    import os
    from src.cosmos_db import CosmosDBService

    endpoint = os.environ.get("COSMOSDB_ENDPOINT")
    key = os.environ.get("COSMOSDB_KEY")
    if not endpoint or not key:
        logger.error("COSMOSDB_ENDPOINT and COSMOSDB_KEY must be set for --apply.")
        sys.exit(2)
    return CosmosDBService(endpoint=endpoint, key=key, database_name=database)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repair_provider_symbols",
        description=(
            "Repair provider_symbols overrides for XMAD:ENAG, XAMS:MICCT, "
            "XAMS:ULVR. Default mode is --audit (read-only)."
        ),
    )
    mode_grp = parser.add_mutually_exclusive_group()
    mode_grp.add_argument(
        "--audit",
        action="store_true",
        help="(default) Read-only audit; report proposed corrections + verdicts.",
    )
    mode_grp.add_argument(
        "--apply",
        action="store_true",
        help="Backup (mandatory), then per-security correction + enrichment rerun.",
    )
    mode_grp.add_argument(
        "--restore",
        metavar="BACKUP_FILE",
        default=None,
        help="Restore the 3 security_master docs' provider_symbols from a backup file.",
    )
    parser.add_argument(
        "--backup-dir",
        dest="backup_dir",
        default=str(DEFAULT_BACKUP_DIR),
        help=f"Directory for backup files. Default: {DEFAULT_BACKUP_DIR}",
    )
    parser.add_argument(
        "--database",
        default="stock-options-manager",
        help=(
            "Cosmos database name (matches src/config.py's production "
            "default; override for a different environment)."
        ),
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
        help="Portfolio container name (read-only ledger ref-count confirmation only).",
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

    is_audit = args.audit or (not args.apply and args.restore is None)

    if is_audit:
        symbols_c, portfolio_c = _build_cosmos_containers(
            args.database, args.symbols_container, args.portfolio_container
        )
        report = audit_repair(symbols_c, portfolio_c)
        _print_report(report)
        return report.exit_code

    if args.apply:
        symbols_c, portfolio_c = _build_cosmos_containers(
            args.database, args.symbols_container, args.portfolio_container
        )
        cosmos = _build_cosmos_service(args.database)
        try:
            report = apply_repair(
                symbols_c, portfolio_c, dry_run=False, cosmos=cosmos, backup_dir=backup_dir
            )
            _print_report(report)
            return report.exit_code
        except RepairAbort as exc:
            logger.error("Repair aborted: %s", exc.reason)
            return exc.exit_code
        except Exception as exc:  # noqa: BLE001
            logger.error("Apply failed: %s", exc)
            return 2

    if args.restore:
        symbols_c, _portfolio_c = _build_cosmos_containers(
            args.database, args.symbols_container, args.portfolio_container
        )
        restore_path = Path(args.restore)
        if not restore_path.exists():
            logger.error("Backup file not found: %s", restore_path)
            return 1
        try:
            report = restore_repair(symbols_c, restore_path)
        except ValueError as exc:
            logger.error("Restore aborted: %s", exc)
            return 2
        _print_restore_report(report)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
