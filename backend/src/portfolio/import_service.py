"""Import session orchestration, question generation, and state machine.

Safety invariant: no ledger writes occur before COMMITTED state.
Inline security creation (during ENTITY_QUESTIONS) is the only
write permitted before commit — it writes to the symbols container only.

State machine transitions:
  CREATED → FILE_PARSED → BATCH_QUESTIONS → ENTITY_QUESTIONS →
  ROW_GROUP_QUESTIONS → PREVIEW_READY → COMMIT_CONFIRMED → COMMITTED

BATCH_QUESTIONS and ROW_GROUP_QUESTIONS are skipped when there are no
questions for those scopes. Questions are deterministic; no LLM involved.
"""

from __future__ import annotations

import hashlib
import logging
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .cosmos_portfolio import CosmosPortfolioService, StorageUnavailableError, _SESSION_TTL_SECONDS
from .models import OPTION_TXN_TYPES

# Alias for readability in the commit loop
_VoidedMovementError = CosmosPortfolioService.VoidedMovementError
from .cosmos_securities import CosmosSecuritiesService, _CollisionError, make_security_id
from .parsers.common import normalize_company_name, row_idempotency_hash
from .parsers.dividends import parse_dividends
from .parsers.options import parse_options
from .parsers.purchases import parse_purchases
from .parsers.sales import parse_sales
from .symbol_config_sync import ensure_symbol_config

logger = logging.getLogger(__name__)

_TERMINAL_STATES = {"COMMITTED", "EXPIRED"}
_COSMOS_SYSTEM_KEYS = {"_rid", "_self", "_etag", "_attachments", "_ts"}


def _clean(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in _COSMOS_SYSTEM_KEYS}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Format auto-detection
# ---------------------------------------------------------------------------

def _detect_format(content: bytes) -> str:
    """Infer import format from header row column names."""
    import io, csv
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    from .parsers.common import _detect_delimiter
    delim = _detect_delimiter(text)
    reader = csv.reader(io.StringIO(text.strip()), delimiter=delim)
    for row in reader:
        if row:
            header = [_normalize_header(h) for h in row]
            if "fecha de cobro" in header:
                return "dividends"
            if "fecha compra" in header:
                return "purchases"
            if "fecha venta" in header:
                return "sales"
            if "strike" in header and ("expiracion" in header or "expiration" in header):
                return "options"
            break
    return "dividends"  # fallback


def _normalize_header(h: str) -> str:
    nfkd = unicodedata.normalize("NFKD", str(h))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


# ---------------------------------------------------------------------------
# Import service
# ---------------------------------------------------------------------------

class ImportService:
    """Orchestrates the conversational import state machine."""

    def __init__(
        self,
        portfolio_svc: CosmosPortfolioService,
        securities_svc: CosmosSecuritiesService,
    ) -> None:
        self.portfolio_svc = portfolio_svc
        self.securities_svc = securities_svc

    # ── Public API ─────────────────────────────────────────────────────

    def create_session(
        self,
        content: bytes,
        *,
        format_hint: Optional[str] = None,
        currency: str = "EUR",
        account_id: str = "_unassigned",
    ) -> Dict[str, Any]:
        """Parse CSV, create session, generate initial questions.

        Returns the session dict (ready for API response).
        Raises ValueError on parse failure.
        """
        # Detect format
        fmt = format_hint or _detect_format(content)

        # Parse content
        parsed_rows = _parse_content(content, fmt)
        parsed_rows = _resolve_parsed_row_accounts(
            parsed_rows, self.portfolio_svc, account_id or "_unassigned"
        )

        session_id = f"imp_{uuid4().hex}"
        batch_id = f"batch_{uuid4().hex[:12]}"
        now = _now()

        # Generate initial warnings (parser-level)
        all_warnings: List[Dict[str, Any]] = []
        for row in parsed_rows:
            all_warnings.extend(row.get("warnings", []))

        # Generate ENTITY questions — one per distinct normalised company name
        entity_questions = _generate_entity_questions(
            parsed_rows, self.securities_svc
        )

        # Determine initial state
        if entity_questions:
            initial_state = "ENTITY_QUESTIONS"
        else:
            # All companies auto-resolved (e.g., no companies at all)
            initial_state = "PREVIEW_READY" if not parsed_rows else "PREVIEW_READY"

        # Build session document
        session_doc: Dict[str, Any] = {
            "id": session_id,
            "session_id": session_id,
            "doc_type": "import_session",
            "state": initial_state,
            "batch_id": batch_id,
            "detected_format": fmt,
            "currency": currency,
            "account_id": account_id or "_unassigned",
            "questions": entity_questions,
            "parsed_rows": _serialize_parsed_rows(parsed_rows),
            "staged_rows": [],
            "resolution_map": {},
            "skipped_companies": [],
            "warnings": [_serialize_warning(w) for w in all_warnings],
            "row_count": len(parsed_rows),
            "created_at": now,
            "updated_at": now,
            "ttl": _SESSION_TTL_SECONDS,
        }

        saved = self.portfolio_svc.create_session(session_doc)
        return _build_session_response(saved)

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Return session response dict, or None if not found."""
        doc = self.portfolio_svc.get_session(session_id)
        if doc is None:
            return None
        return _build_session_response(doc)

    def answer_question(
        self,
        session_id: str,
        answer_request: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Process one question answer and return updated session.

        Fan-out: ENTITY answers apply to ALL rows with same empresa_normalized.
        """
        doc = self.portfolio_svc.get_session(session_id)
        if doc is None:
            raise LookupError(f"Session {session_id} not found or expired")

        state = doc.get("state", "")
        if state in _TERMINAL_STATES:
            raise StateError(f"Session is in terminal state {state}")

        question_id = answer_request.get("question_id")
        answer_type = answer_request.get("answer_type")
        selected_security_id = answer_request.get("selected_security_id")
        batch_value = answer_request.get("batch_value")

        # Find the question
        questions = doc.get("questions", [])
        question = next((q for q in questions if q["question_id"] == question_id), None)
        if question is None:
            raise ValueError(f"Question {question_id} not found")

        # Update the question with the answer
        question["answer_type"] = answer_type
        question["answer"] = answer_type

        if answer_type == "SELECTED_CANDIDATE":
            if not selected_security_id:
                raise ValueError("selected_security_id required for SELECTED_CANDIDATE")
            question["selected_security_id"] = selected_security_id
            # Fan-out: apply to all rows with same empresa_normalized
            normalized = question.get("normalized_name", "")
            doc.setdefault("resolution_map", {})[normalized] = selected_security_id

        elif answer_type == "CREATED_NEW_SECURITY":
            if not selected_security_id:
                raise ValueError("selected_security_id required for CREATED_NEW_SECURITY")
            question["selected_security_id"] = selected_security_id
            normalized = question.get("normalized_name", "")
            doc.setdefault("resolution_map", {})[normalized] = selected_security_id

        elif answer_type in ("SKIPPED_COMPANY", "EXCLUDED_COMPANY"):
            normalized = question.get("normalized_name", "")
            doc.setdefault("skipped_companies", [])
            if normalized not in doc["skipped_companies"]:
                doc["skipped_companies"].append(normalized)

        elif answer_type == "BATCH_VALUE":
            batch_key = question.get("batch_key", "")
            if batch_key == "currency":
                doc["currency"] = str(batch_value or "EUR").upper()
            elif batch_key == "account_id":
                doc["account_id"] = str(batch_value or "_unassigned")
            question["batch_value"] = batch_value

        # Advance state if all ENTITY questions answered
        doc["state"] = _compute_state(doc)
        updated = self.portfolio_svc.update_session(doc)
        return _build_session_response(updated)

    def generate_preview(self, session_id: str) -> Dict[str, Any]:
        """Generate commit preview. Session must be in PREVIEW_READY state."""
        doc = self.portfolio_svc.get_session(session_id)
        if doc is None:
            raise LookupError(f"Session {session_id} not found or expired")

        state = doc.get("state", "")
        if state in _TERMINAL_STATES:
            raise StateError(f"Session is in terminal state {state}")

        # Check for unresolved entity questions
        pending = _pending_entity_questions(doc)
        if pending:
            raise UnresolvedQuestionsError(pending)

        # Build preview movements
        movements, warnings, skipped = _build_preview_movements(
            doc, self.portfolio_svc
        )

        # Resolve canonical company names from security catalog (F3)
        security_names = _resolve_preview_company_names(doc, self.securities_svc)
        for m in movements:
            m["company_name"] = security_names.get(m.get("security_id", ""), "")

        doc["state"] = "PREVIEW_READY"
        doc["preview_movements"] = [_serialize_movement(m) for m in movements]
        doc["preview_warnings"] = [_serialize_warning(w) for w in warnings]
        doc["preview_skipped"] = skipped
        updated = self.portfolio_svc.update_session(doc)
        return _build_preview_response(updated, movements, warnings, skipped)

    def commit_session(self, session_id: str) -> Dict[str, Any]:
        """Commit previewed movements to ledger. Idempotent."""
        doc = self.portfolio_svc.get_session(session_id)
        if doc is None:
            raise LookupError(f"Session {session_id} not found or expired")

        state = doc.get("state", "")
        if state == "COMMITTED":
            raise AlreadyCommittedError()
        if state not in ("PREVIEW_READY", "COMMIT_CONFIRMED"):
            raise StateError(f"Cannot commit from state {state}; must be PREVIEW_READY")

        # Check for unresolved entity questions
        pending = _pending_entity_questions(doc)
        if pending:
            raise UnresolvedQuestionsError(pending)

        # Build movements if preview not yet done
        if not doc.get("preview_movements"):
            movements, warnings, skipped = _build_preview_movements(
                doc, self.portfolio_svc
            )
            doc["preview_movements"] = [_serialize_movement(m) for m in movements]
        else:
            movements = [_deserialize_movement(m) for m in doc["preview_movements"]]

        # Write ledger_txns — idempotency via upsert on deterministic ID
        committed = 0
        skipped_count = 0
        commit_skip_reasons: List[Dict[str, Any]] = []
        account_id = doc.get("account_id", "_unassigned")

        for movement in movements:
            try:
                self.portfolio_svc.write_ledger_txn(movement)
                committed += 1
            except _VoidedMovementError as exc:
                # Voided/superseded movement: skip silently and preserve integrity
                logger.warning(
                    "Skipping voided/superseded movement %s during commit: %s",
                    movement.get("id"), exc,
                )
                skipped_count += 1
                commit_skip_reasons.append({
                    "row_index": movement.get("source_row_index"),
                    "id": movement.get("id"),
                    "reason": "SUPERSEDED_OR_VOIDED_ID_COLLISION",
                    "blocking_status": getattr(exc, "status", None),
                    "message": str(exc),
                })
            except Exception as exc:
                logger.warning("Failed to write ledger_txn %s: %s", movement.get("id"), exc)
                skipped_count += 1
                commit_skip_reasons.append({
                    "row_index": movement.get("source_row_index"),
                    "id": movement.get("id"),
                    "reason": "WRITE_ERROR",
                    "message": str(exc),
                })

        # ── Synchronous best-effort enrollment in symbol_config ──────────
        # Ledger writes are already committed above.  Config enrollment is a
        # secondary write to a different Cosmos container.  Failure must never
        # roll back the ledger — log and continue.
        symbols_container = self.securities_svc.container
        enrolled_ids: set = set()
        enrollment_warnings: List[str] = []
        for movement in movements:
            sid = movement.get("security_id")
            if sid and sid not in enrolled_ids:
                try:
                    ensure_symbol_config(symbols_container, sid, source="import_commit")
                    enrolled_ids.add(sid)
                except Exception as exc:
                    logger.warning(
                        "ensure_symbol_config failed for %s during import_commit: %s",
                        sid,
                        exc,
                        exc_info=True,
                    )
                    enrollment_warnings.append(f"{sid}: {exc}")

        # Mark session as COMMITTED
        doc["state"] = "COMMITTED"
        doc["committed_count"] = committed
        doc["skipped_count"] = skipped_count
        doc["commit_skip_reasons"] = commit_skip_reasons
        self.portfolio_svc.update_session(doc)

        result: Dict[str, Any] = {
            "session_id": session_id,
            "state": "COMMITTED",
            "committed_count": committed,
            "skipped_count": skipped_count,
            "enrolled_security_ids": sorted(enrolled_ids),
        }
        if commit_skip_reasons:
            result["commit_skip_reasons"] = commit_skip_reasons
        if enrollment_warnings:
            result["enrollment_warnings"] = enrollment_warnings
        return result

    def inline_create_security(
        self,
        session_id: str,
        question_id: str,
        security_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Create a security inline during ENTITY_QUESTIONS and update session.

        This is the only write to symbols container permitted before commit.
        Returns updated session.
        Raises _CollisionError on ISIN/security_id collision.
        """
        doc = self.portfolio_svc.get_session(session_id)
        if doc is None:
            raise LookupError(f"Session {session_id} not found or expired")

        state = doc.get("state", "")
        if state in _TERMINAL_STATES:
            raise StateError(f"Session is in terminal state {state}")

        # Create the security
        created_sec = self.securities_svc.create_security(security_data)
        security_id = created_sec["security_id"]

        # Update the question with the resolved security
        questions = doc.get("questions", [])
        question = next((q for q in questions if q["question_id"] == question_id), None)
        if question is None:
            raise ValueError(f"Question {question_id} not found")

        question["answer_type"] = "CREATED_NEW_SECURITY"
        question["answer"] = "CREATED_NEW_SECURITY"
        question["selected_security_id"] = security_id
        normalized = question.get("normalized_name", "")
        doc.setdefault("resolution_map", {})[normalized] = security_id

        # Advance state
        doc["state"] = _compute_state(doc)
        updated = self.portfolio_svc.update_session(doc)
        return _build_session_response(updated)


# ---------------------------------------------------------------------------
# State machine helpers
# ---------------------------------------------------------------------------

def _compute_state(doc: Dict[str, Any]) -> str:
    """Compute the correct session state based on which questions remain."""
    questions = doc.get("questions", [])
    entity_questions = [q for q in questions if q.get("scope") == "ENTITY"]
    unanswered_entity = [q for q in entity_questions if not q.get("answer")]

    if unanswered_entity:
        return "ENTITY_QUESTIONS"
    # All entity questions answered → ready for preview
    return "PREVIEW_READY"


def _pending_entity_questions(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return unanswered ENTITY questions."""
    return [
        q for q in doc.get("questions", [])
        if q.get("scope") == "ENTITY" and not q.get("answer")
    ]


# ---------------------------------------------------------------------------
# Question generation
# ---------------------------------------------------------------------------

def _generate_entity_questions(
    parsed_rows: List[Dict[str, Any]],
    securities_svc: CosmosSecuritiesService,
) -> List[Dict[str, Any]]:
    """One ENTITY question per distinct normalised company name."""
    seen: Dict[str, Dict[str, Any]] = {}  # normalized_name → question

    for row in parsed_rows:
        normalized = row.get("empresa_normalized", "")
        empresa_raw = row.get("empresa_raw", "")
        if not normalized or normalized in seen:
            continue

        candidates_docs = securities_svc.find_candidates_for_name(normalized)
        candidates = [
            {
                "security_id": s.get("security_id", ""),
                "company_name": s.get("company_name", ""),
                "score": round(_score_candidate(s, normalized), 2),
            }
            for s in candidates_docs
        ]

        seen[normalized] = {
            "question_id": f"q_{uuid4().hex[:12]}",
            "scope": "ENTITY",
            "company_name": empresa_raw,
            "normalized_name": normalized,
            "candidates": candidates,
            "answer": None,
            "answer_type": None,
            "selected_security_id": None,
        }

    return list(seen.values())


def _score_candidate(sec: Dict[str, Any], query: str) -> float:
    """Simple scoring — mirrors cosmos_securities._score_match."""
    from .cosmos_securities import _normalize_text
    cname = _normalize_text(sec.get("company_name", ""))
    if cname == query:
        return 0.95
    for alias in sec.get("aliases", []):
        if alias.get("normalized", "").lower() == query:
            return 0.90
    if query in cname:
        return 0.8
    if _normalize_text(sec.get("ticker", "")) == query:
        return 0.7
    return 0.5


# ---------------------------------------------------------------------------
# Preview / commit helpers
# ---------------------------------------------------------------------------

def _parse_content(content: bytes, fmt: str) -> List[Dict[str, Any]]:
    """Dispatch to the correct parser."""
    if fmt == "dividends":
        return parse_dividends(content)
    elif fmt == "purchases":
        return parse_purchases(content)
    elif fmt == "sales":
        return parse_sales(content)
    elif fmt == "options":
        return parse_options(content)
    raise ValueError(f"Unknown format: {fmt}")


def _resolve_parsed_row_accounts(
    parsed_rows: List[Dict[str, Any]],
    portfolio_svc: CosmosPortfolioService,
    default_account_id: str,
) -> List[Dict[str, Any]]:
    """Resolve optional per-row account labels to account_id values."""
    def _normalize_account_label(value: Any) -> str:
        text = str(value or "").strip().replace("_", " ").replace("-", " ")
        normalized = normalize_company_name(text)
        if normalized.startswith("acct "):
            normalized = normalized[5:].strip()
        return normalized

    rows_with_account = [row for row in parsed_rows if "account_raw" in row]
    if not rows_with_account:
        return parsed_rows

    accounts = portfolio_svc.list_accounts()
    by_id = {str(a.get("account_id", "")): a for a in accounts if a.get("account_id")}
    by_name = {}
    for account in accounts:
        account_name = account.get("name")
        if account_name:
            by_name[_normalize_account_label(account_name)] = account
        account_id = account.get("account_id")
        if account_id:
            by_name[_normalize_account_label(account_id)] = account

    resolved_rows: List[Dict[str, Any]] = []
    for row in parsed_rows:
        resolved = dict(row)
        account_raw = str(row.get("account_raw", "") or "").strip()
        if not account_raw:
            resolved["account_id"] = default_account_id
            resolved_rows.append(resolved)
            continue

        if account_raw == default_account_id:
            resolved["account_id"] = default_account_id
            resolved_rows.append(resolved)
            continue

        if account_raw in by_id:
            resolved["account_id"] = account_raw
            resolved_rows.append(resolved)
            continue

        normalized = _normalize_account_label(account_raw)
        match = by_name.get(normalized)
        if match is None:
            raise ValueError(
                f"Unknown account {account_raw!r} in import row {row.get('row_index', 0) + 2}"
            )
        resolved["account_id"] = str(match.get("account_id"))
        resolved_rows.append(resolved)
    return resolved_rows


def _build_preview_movements(
    doc: Dict[str, Any],
    portfolio_svc: CosmosPortfolioService,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Build ledger_txn dicts from parsed rows + entity resolution.

    Returns (movements, warnings, skipped_rows).
    Movements are not yet written to Cosmos.
    """
    fmt = doc.get("detected_format", "dividends")
    parsed_rows = _deserialize_parsed_rows(doc.get("parsed_rows", []))
    resolution_map: Dict[str, str] = doc.get("resolution_map", {})
    skipped_companies: List[str] = doc.get("skipped_companies", [])
    currency = doc.get("currency", "EUR")
    account_id = doc.get("account_id", "_unassigned")
    batch_id = doc.get("batch_id", "")
    session_id = doc.get("session_id", "")

    movements: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    # Track holdings per security for NEGATIVE_INVENTORY check
    holdings_delta: Dict[str, Decimal] = {}

    for row in parsed_rows:
        normalized = row.get("empresa_normalized", "")
        if normalized in skipped_companies:
            skipped.append({
                "company": row.get("empresa_raw", ""),
                "reason": "SKIPPED_COMPANY",
                "row_count": 1,
            })
            continue

        security_id = resolution_map.get(normalized)
        if not security_id:
            skipped.append({
                "company": row.get("empresa_raw", ""),
                "reason": "UNRESOLVED_ENTITY",
                "row_count": 1,
            })
            continue

        row_warnings = list(row.get("warnings", []))

        movement = _row_to_movement(
            row, fmt, security_id, currency, account_id, batch_id, session_id
        )

        # Cross-batch probable duplicate check
        try:
            dup = portfolio_svc.find_probable_duplicate(
                security_id=security_id,
                txn_type=movement.get("txn_type", ""),
                trade_date=movement.get("trade_date", ""),
                quantity=movement.get("quantity") or "",
                gross_eur=movement.get("gross", {}).get("eur_amount", ""),
            )
            if dup and dup.get("id") != movement.get("id"):
                # A different existing movement matches these fields — genuine
                # possible duplicate. When dup.id equals this row's own
                # deterministic id, it's simply the same row being re-imported
                # (idempotent upsert/overwrite) — not worth warning about.
                row_warnings.append({
                    "type": "PROBABLE_DUPLICATE",
                    "row_index": row.get("row_index"),
                    "existing_movement_id": dup.get("id"),
                    "row_indices": [row.get("row_index")],
                    "message": (
                        f"Possible duplicate of previously committed movement {dup.get('id')}"
                    ),
                })
        except Exception:
            pass  # Best-effort; don't fail preview on duplicate-check error

        # Negative inventory tracking (BUY/SELL only; DIVIDEND has no quantity)
        txn_type = movement.get("txn_type", "")
        quantity_str = movement.get("quantity") or "0"
        try:
            qty = Decimal(quantity_str)
        except Exception:
            qty = Decimal("0")

        if txn_type == "BUY":
            holdings_delta[security_id] = holdings_delta.get(security_id, Decimal("0")) + qty
        elif txn_type == "SELL":
            # DERECHOS sales do not affect share count
            if (movement.get("sales_type") or "ACCIONES") == "ACCIONES":
                holdings_delta[security_id] = holdings_delta.get(security_id, Decimal("0")) - qty
        elif txn_type in OPTION_TXN_TYPES:
            pass

        if row_warnings:
            movement["warnings"] = row_warnings
            warnings.extend(row_warnings)

        movements.append(movement)

    # Check for negative inventory warnings after all rows processed
    for security_id, delta in holdings_delta.items():
        if delta < Decimal("0"):
            warnings.append({
                "type": "NEGATIVE_INVENTORY",
                "security_id": security_id,
                "shares": str(delta),
                "message": (
                    f"Holdings for {security_id} would be negative ({delta} shares). "
                    "This may self-heal when earlier purchases are imported."
                ),
            })

    return movements, warnings, _deduplicate_skipped(skipped)


def _row_to_movement(
    row: Dict[str, Any],
    fmt: str,
    security_id: str,
    currency: str,
    account_id: str,
    batch_id: str,
    session_id: str,
) -> Dict[str, Any]:
    """Convert a parsed row to a ledger_txn document dict."""
    row_index = row.get("row_index", 0)

    if fmt == "dividends":
        trade_date = row.get("payment_date", "")
        gross = row.get("gross", Decimal("0"))
        net = row.get("net", Decimal("0"))
        wht_source = row.get("wht_source", Decimal("0"))
        wht_dest = row.get("wht_destination", Decimal("0"))
        quantity = None  # Cash dividend — source schema has no share count; never fabricate 0
        commission = Decimal("0")
        txn_type = "DIVIDEND"
        cost_basis_status = "COMPLETE"
        derechos = row.get("derechos", Decimal("0"))
        source_derechos = str(derechos) if derechos > Decimal("0") else None
        sales_type = None
        sales_type_raw = None
    elif fmt == "purchases":
        trade_date = row.get("purchase_date", "")
        # CSV "Total (€)" is the trade consideration (price × qty) — gross.
        # net = gross + commission (actual cash outflow; net > gross for BUY).
        gross = row.get("total_cost", Decimal("0"))
        commission = row.get("commission", Decimal("0"))
        net = gross + commission
        wht_source = Decimal("0")
        wht_dest = Decimal("0")
        quantity = row.get("quantity", Decimal("0"))
        txn_type = "BUY"
        cost_basis_status = row.get("cost_basis_status", "COMPLETE")
        source_derechos = None
        sales_type = None
        sales_type_raw = None
    elif fmt == "sales":
        trade_date = row.get("sale_date", "")
        gross = row.get("total_proceeds", Decimal("0"))
        commission = row.get("commission", Decimal("0"))
        net = gross - commission
        wht_source = Decimal("0")
        wht_dest = Decimal("0")
        quantity = row.get("quantity", Decimal("0"))
        txn_type = "SELL"
        cost_basis_status = "COMPLETE"
        source_derechos = None
        sales_type = row.get("sales_type", "ACCIONES")
        sales_type_raw = row.get("sales_type_raw", "")
    else:  # options
        trade_date = row.get("trade_date", "")
        gross = row.get("gross_usd", Decimal("0"))
        gross_eur = row.get("gross_eur", Decimal("0"))
        commission = _derive_option_fee_amount(
            gross_amount=gross,
            gross_eur=gross_eur,
            fee_eur=row.get("commission_eur", Decimal("0")),
        )
        net_override = row.get("net_eur")
        wht_source = Decimal("0")
        wht_dest = Decimal("0")
        quantity = Decimal("0")
        txn_type = row.get("txn_type", "")
        source_derechos = None
        sales_type = None
        sales_type_raw = None
        gross_currency = "USD"
        fees_currency = "USD"
        gross_eur_value = gross_eur
        fees_eur_value = row.get("commission_eur", Decimal("0"))
        net = None

    if fmt != "options":
        gross_currency = currency
        fees_currency = currency
        gross_eur_value = gross
        fees_eur_value = commission

    # Deterministic movement ID
    movement_account_id = row.get("account_id") or account_id
    ticker = security_id_to_ticker(security_id)
    date_compact = (trade_date or "").replace("-", "")
    movement_id = (
        f"txn_{movement_account_id}_{date_compact}_{ticker}_{txn_type}_{row_index:03d}"
    )

    # Idempotency hash
    idempotency_hash = row_idempotency_hash(
        security_id, txn_type, trade_date or "", quantity, gross
    )

    movement: Dict[str, Any] = {
        "id": movement_id,
        "doc_type": "ledger_txn",
        "txn_type": txn_type,
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": trade_date or "",
        "quantity": str(quantity.normalize()) if quantity is not None else None,
        "gross": {
            "amount": str(gross.normalize()),
            "currency": gross_currency,
            "eur_amount": str(gross_eur_value.normalize()),
        },
        "fees": {
            "total": f"{commission:.2f}",
            "currency": fees_currency,
            "total_eur": f"{fees_eur_value:.2f}",
        },
        "withholding": {
            "source": {
                "amount_eur": str(wht_source.normalize()),
            } if wht_source > Decimal("0") else None,
            "destination": {
                "amount_eur": str(wht_dest.normalize()),
            } if wht_dest > Decimal("0") else None,
        },
        "net": {
            "amount": str(net.normalize()) if net is not None else "0",
            "currency": gross_currency,
            "eur_amount": str(net.normalize()) if net is not None else "0",
        },
        "fx": {"rate": "1.000000000", "rate_source": "ECB"},
        "account_id": movement_account_id,
        "import_source": "csv_import",
        "batch_id": batch_id,
        "session_id": session_id,
        "idempotency_hash": idempotency_hash,
        "source_row_index": row_index,
        "source_row": row.get("source_row", {}),
        "created_at": _now(),
    }

    if source_derechos:
        movement["source_derechos_amount"] = source_derechos

    if sales_type is not None:
        movement["sales_type"] = sales_type
        movement["sales_type_raw"] = sales_type_raw or ""
        movement["is_rights_sale"] = (sales_type == "DERECHOS")

    if fmt in {"purchases", "dividends", "sales"}:
        movement["cost_basis_status"] = cost_basis_status

    if fmt == "options":
        computed_net = _compute_option_net(
            txn_type=txn_type,
            gross=movement["gross"],
            fees=movement["fees"],
        )
        if net_override is not None:
            movement["net"] = {
                "amount": computed_net["amount"],
                "currency": movement["gross"]["currency"],
                "eur_amount": str(net_override.normalize()),
            }
        else:
            movement["net"] = computed_net
        movement["option_position_id"] = None
        movement["option_link_kind"] = "OPEN_SELL" if txn_type.endswith("_SELL") else "CLOSE_BUY"
        movement["option_type"] = "call" if txn_type.startswith("CALL_") else "put"
        movement["option_strike"] = row.get("option_strike")
        movement["option_expiration"] = row.get("option_expiration")
        movement["option_symbol"] = row.get("empresa_raw", "")
        movement["option_close_date"] = row.get("option_close_date")

    return movement


def _derive_option_fee_amount(
    gross_amount: Decimal,
    gross_eur: Decimal,
    fee_eur: Decimal,
) -> Decimal:
    if gross_eur in (None, Decimal("0")):
        return fee_eur
    try:
        return (fee_eur * gross_amount / gross_eur).quantize(Decimal("0.01"))
    except Exception:
        return fee_eur


def _compute_option_net(
    txn_type: str,
    gross: Dict[str, Any],
    fees: Dict[str, Any],
) -> Dict[str, str]:
    from .cosmos_portfolio import _compute_manual_net

    return _compute_manual_net(txn_type, gross, fees, None)


def security_id_to_ticker(security_id: str) -> str:
    parts = security_id.split(":", 1)
    return parts[1] if len(parts) == 2 else security_id


def _deduplicate_skipped(skipped: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Dict[str, Dict[str, Any]] = {}
    for s in skipped:
        key = f"{s.get('company')}|{s.get('reason')}"
        if key in seen:
            seen[key]["row_count"] = seen[key].get("row_count", 0) + 1
        else:
            seen[key] = dict(s)
    return list(seen.values())


# ---------------------------------------------------------------------------
# Serialisation helpers (Decimal → str for Cosmos storage)
# ---------------------------------------------------------------------------

def _serialize_decimal(v: Any) -> Any:
    if isinstance(v, Decimal):
        return str(v.normalize())
    return v


def _serialize_parsed_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result = []
    for row in rows:
        serialized = {}
        for k, v in row.items():
            if isinstance(v, Decimal):
                serialized[k] = str(v.normalize())
            elif isinstance(v, list) and k == "warnings":
                serialized[k] = v
            elif isinstance(v, dict) and k == "source_row":
                serialized[k] = v
            else:
                serialized[k] = v
        result.append(serialized)
    return result


def _deserialize_parsed_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    decimal_fields = {
        "gross", "net", "derechos", "wht_source", "wht_destination",
        "price_per_share", "quantity", "total_cost", "commission",
        "total_proceeds", "gross_usd", "gross_eur", "commission_eur", "net_eur",
    }
    result = []
    for row in rows:
        deserialized = {}
        for k, v in row.items():
            if k in decimal_fields and v is not None:
                try:
                    deserialized[k] = Decimal(str(v))
                except Exception:
                    deserialized[k] = Decimal("0")
            else:
                deserialized[k] = v
        result.append(deserialized)
    return result


def _serialize_movement(m: Dict[str, Any]) -> Dict[str, Any]:
    return m


def _deserialize_movement(m: Dict[str, Any]) -> Dict[str, Any]:
    return m


def _serialize_warning(w: Dict[str, Any]) -> Dict[str, Any]:
    return {k: str(v) if isinstance(v, Decimal) else v for k, v in w.items()}


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------

def _resolve_preview_company_names(
    doc: Dict[str, Any],
    securities_svc: CosmosSecuritiesService,
) -> Dict[str, str]:
    """Resolve canonical company names for preview movements.

    Priority: security_master.company_name > empresa_raw from parsed rows.
    """
    resolution_map: Dict[str, str] = doc.get("resolution_map", {})
    parsed_rows = doc.get("parsed_rows", [])

    empresa_by_security: Dict[str, str] = {}
    for row in parsed_rows:
        normalized = row.get("empresa_normalized", "")
        sid = resolution_map.get(normalized, "")
        if sid and sid not in empresa_by_security:
            empresa_by_security[sid] = row.get("empresa_raw", "")

    result: Dict[str, str] = {}
    for sid, fallback in empresa_by_security.items():
        try:
            sec = securities_svc.get_security(sid)
            result[sid] = sec.get("company_name", fallback) if sec else fallback
        except Exception:
            result[sid] = fallback
    return result


def _build_session_response(doc: Dict[str, Any]) -> Dict[str, Any]:
    doc = _clean(doc)
    # Don't leak large internal fields in GET response
    response = {
        "session_id": doc.get("session_id"),
        "state": doc.get("state"),
        "row_count": doc.get("row_count", 0),
        "detected_format": doc.get("detected_format"),
        "currency": doc.get("currency", "EUR"),
        "account_id": doc.get("account_id", "_unassigned"),
        "warnings": doc.get("warnings", []),
        "questions": doc.get("questions", []),
        "staged_summary": {
            "total_rows": doc.get("row_count", 0),
            "resolved_rows": _count_resolved_rows(doc),
            "unresolved_rows": _count_unresolved_rows(doc),
        },
    }
    return response


def _build_preview_response(
    doc: Dict[str, Any],
    movements: List[Dict[str, Any]],
    warnings: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
) -> Dict[str, Any]:
    doc = _clean(doc)
    # Build preview movement shapes per contract
    preview_movements = []
    for m in movements:
        row_idx = m.get("source_row_index", 0)
        entry: Dict[str, Any] = {
            "row_index": row_idx,
            "txn_type": m.get("txn_type"),
            "security_id": m.get("security_id"),
            "ticker": m.get("ticker"),
            "company_name": m.get("company_name", ""),
            "trade_date": m.get("trade_date"),
            "quantity": m.get("quantity"),
            "gross_eur": m.get("gross", {}).get("eur_amount"),
            "fees_eur": m.get("fees", {}).get("total_eur", "0.00"),
            "wht_source_eur": (m.get("withholding") or {}).get("source", {}) and
                              (m.get("withholding", {}).get("source") or {}).get("amount_eur", "0.00"),
            "net_eur": m.get("net", {}).get("eur_amount"),
        }
        if m.get("sales_type") is not None:
            entry["sales_type"] = m["sales_type"]
        preview_movements.append(entry)

    return {
        "session_id": doc.get("session_id"),
        "state": "PREVIEW_READY",
        "preview": {
            "movements": preview_movements,
            "warnings": [_serialize_warning(w) for w in warnings],
            "total_movements": len(movements),
            "skipped_rows": sum(s.get("row_count", 1) for s in skipped),
            "skip_reasons": skipped,
        },
    }


def _count_resolved_rows(doc: Dict[str, Any]) -> int:
    resolution_map = doc.get("resolution_map", {})
    skipped = set(doc.get("skipped_companies", []))
    parsed_rows = doc.get("parsed_rows", [])
    count = 0
    for row in parsed_rows:
        normalized = row.get("empresa_normalized", "")
        if normalized in resolution_map or normalized in skipped:
            count += 1
    return count


def _count_unresolved_rows(doc: Dict[str, Any]) -> int:
    total = doc.get("row_count", 0)
    return total - _count_resolved_rows(doc)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class StateError(Exception):
    """Invalid session state transition."""


class UnresolvedQuestionsError(Exception):
    """Cannot preview/commit with unanswered questions."""
    def __init__(self, pending: List[Dict[str, Any]]) -> None:
        self.pending = pending
        super().__init__(f"{len(pending)} unresolved questions")


class AlreadyCommittedError(Exception):
    """Session already committed."""
