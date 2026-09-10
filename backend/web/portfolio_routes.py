"""FastAPI router for portfolio, securities catalog, and import session endpoints.

All endpoints follow the frozen contract v1.1 (Phase 1) and Phase 2 extensions.
Error shapes: { "error": "<code>", "detail": "<human message>" }
Storage-unavailable: 503 with { "error": "storage_unavailable", "detail": "..." }
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Dict, Optional

from azure.cosmos.exceptions import CosmosResourceNotFoundError
from fastapi import APIRouter, Request, Query, UploadFile, File, Form
from fastapi.responses import JSONResponse

from src.portfolio.cosmos_portfolio import (
    CosmosPortfolioService,
    StorageUnavailableError,
    InsufficientSharesError,
)
from src.portfolio.cosmos_securities import (
    CosmosSecuritiesService,
    _CollisionError,
    security_id_to_ticker,
)
from src.portfolio.holdings_service import HoldingsService, _resolve_security_names
from src.portfolio.import_service import (
    ImportService,
    StateError,
    UnresolvedQuestionsError,
    AlreadyCommittedError,
)
from src.portfolio.provider_symbols import (
    validate_provider_symbols,
    resolve_yfinance_symbol,
)
from src.portfolio.symbol_config_sync import ensure_symbol_config
from src.portfolio.fx_service import (
    FxUnavailableError,
    FxRateNotFoundError,
    get_fx_rate,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_COSMOS_SYSTEM_KEYS = {"_rid", "_self", "_etag", "_attachments", "_ts"}


def _clean(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in _COSMOS_SYSTEM_KEYS}


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------

def _get_cosmos(request: Request):
    cosmos = getattr(request.app.state, "cosmos", None)
    if cosmos is None:
        error = getattr(request.app.state, "cosmos_error", "unknown")
        raise RuntimeError(f"CosmosDB not available: {error}")
    return cosmos


def _get_securities_svc(request: Request) -> CosmosSecuritiesService:
    cosmos = _get_cosmos(request)
    return CosmosSecuritiesService(cosmos.container)


def _get_portfolio_svc(request: Request) -> CosmosPortfolioService:
    cosmos = _get_cosmos(request)
    portfolio_container = getattr(cosmos, "portfolio_container", None)
    import_sessions_container = getattr(cosmos, "import_sessions_container", None)
    symbols_container = getattr(cosmos, "container", None)
    return CosmosPortfolioService(
        portfolio_container, import_sessions_container, symbols_container
    )


def _get_import_service(request: Request) -> ImportService:
    return ImportService(_get_portfolio_svc(request), _get_securities_svc(request))


def _get_holdings_service(request: Request) -> HoldingsService:
    return HoldingsService(_get_portfolio_svc(request), _get_securities_svc(request))


def _storage_503(detail: str) -> JSONResponse:
    return JSONResponse(
        {"error": "storage_unavailable", "detail": detail},
        status_code=503,
    )


def _err(code: str, detail: str, status: int) -> JSONResponse:
    return JSONResponse({"error": code, "detail": detail}, status_code=status)


# ===========================================================================
# Securities Catalog
# ===========================================================================

@router.get("/api/securities")
async def list_securities(request: Request):
    """GET /api/securities — list all security_master docs."""
    try:
        svc = _get_securities_svc(request)
        docs = svc.list_securities()
        return JSONResponse({
            "securities": [
                {
                    "security_id": d.get("security_id"),
                    "ticker": d.get("ticker"),
                    "company_name": d.get("company_name"),
                    "exchange_mic": d.get("exchange_mic"),
                    "asset_class": d.get("asset_class", "Equity"),
                    "listing_currency": d.get("listing_currency"),
                    "isin": d.get("isin"),
                    "status": d.get("status", "ACTIVE"),
                    **({"provider_symbols": d["provider_symbols"]} if d.get("provider_symbols") else {}),
                }
                for d in docs
            ]
        })
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("list_securities failed")
        return _err("internal_error", str(exc), 500)


@router.post("/api/securities")
async def create_security(request: Request):
    """POST /api/securities — create a new security_master."""
    try:
        body = await request.json()
    except Exception:
        return _err("validation_error", "Invalid JSON body", 400)

    required = ["ticker", "company_name", "exchange_mic"]
    missing = [f for f in required if not body.get(f)]
    if missing:
        return _err(
            "validation_error",
            f"Missing required fields: {', '.join(missing)}",
            400,
        )

    # Validate and normalise provider_symbols if provided
    raw_ps = body.get("provider_symbols")
    if raw_ps is not None:
        try:
            cleaned_ps = validate_provider_symbols(raw_ps)
        except ValueError as exc:
            return _err("validation_error", str(exc), 400)
        body["provider_symbols"] = cleaned_ps if cleaned_ps else None

    try:
        svc = _get_securities_svc(request)
        doc = svc.create_security(body)
        return JSONResponse(_clean(doc), status_code=201)
    except _CollisionError as exc:
        return JSONResponse(
            {"error": "collision", "existing": exc.existing},
            status_code=409,
        )
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("create_security failed")
        return _err("internal_error", str(exc), 500)


@router.get("/api/securities/search")
async def search_securities(
    request: Request,
    q: str = Query(default="", description="Ticker, company name, or alias fragment"),
    limit: int = Query(default=10, ge=1, le=50),
):
    """GET /api/securities/search?q=...&limit=10 — search security_master catalog.

    Returns candidates ordered by relevance.  Includes a 'has_config' boolean
    indicating whether a symbol_config already exists for each result (for
    display purposes — "Already in watchlist" badge).

    MUST be defined BEFORE /api/securities/{security_id:path} so it is matched first.
    """
    if not q.strip():
        return JSONResponse({"candidates": []})

    try:
        svc = _get_securities_svc(request)
    except RuntimeError as exc:
        return _storage_503(str(exc))

    try:
        from src.portfolio.cosmos_securities import _normalize_text
        normalized = _normalize_text(q.strip())
        candidates = svc.find_candidates_for_name(normalized, limit=limit)

        # Augment with ticker-exact match (case-insensitive) if not already present
        q_upper = q.strip().upper()
        candidate_ids = {c.get("security_id") for c in candidates}
        try:
            all_secs = svc.list_securities()
            for sec in all_secs:
                if sec.get("ticker", "").upper() == q_upper and sec.get("security_id") not in candidate_ids:
                    candidates.append(sec)
                    candidate_ids.add(sec.get("security_id"))
                    if len(candidates) >= limit:
                        break
        except Exception:
            pass  # best-effort ticker search

        candidates = candidates[:limit]

        # Check which tickers already have a symbol_config
        symbols_container = _get_cosmos(request).container
        result_candidates = []
        for sec in candidates:
            ticker = sec.get("ticker", "")
            has_config = False
            if ticker:
                try:
                    symbols_container.read_item(
                        item=f"config_{ticker.upper()}",
                        partition_key=ticker.upper(),
                    )
                    has_config = True
                except Exception:
                    has_config = False
            result_candidates.append({
                "security_id": sec.get("security_id"),
                "ticker": sec.get("ticker"),
                "company_name": sec.get("company_name"),
                "exchange_mic": sec.get("exchange_mic"),
                "has_config": has_config,
            })

        return JSONResponse({"candidates": result_candidates})
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("search_securities failed")
        return _err("internal_error", str(exc), 500)


@router.get("/api/securities/{security_id:path}")
async def get_security(request: Request, security_id: str):
    """GET /api/securities/{security_id} — lookup by MIC:TICKER."""
    # URL decode the security_id (colon may be %-encoded)
    from urllib.parse import unquote
    security_id = unquote(security_id)

    try:
        svc = _get_securities_svc(request)
        doc = svc.get_security(security_id)
        if doc is None:
            return _err("not_found", f"Security {security_id} not found", 404)
        return JSONResponse(_clean(doc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("get_security failed")
        return _err("internal_error", str(exc), 500)


# ===========================================================================
# Import Sessions
# ===========================================================================

@router.post("/api/import/sessions")
async def create_import_session(
    request: Request,
    file: UploadFile = File(...),
    format_hint: Optional[str] = Form(default=None),
    currency: str = Form(default="EUR"),
    account_id: str = Form(default="_unassigned"),
):
    """POST /api/import/sessions — upload CSV and create import session."""
    try:
        content = await file.read()
    except Exception as exc:
        return _err("parse_error", f"Failed to read file: {exc}", 400)

    if not content:
        return _err("parse_error", "Empty file", 400)

    # Validate format_hint
    valid_formats = {"dividends", "purchases", "sales", None}
    if format_hint not in valid_formats:
        return _err(
            "validation_error",
            f"format_hint must be one of: dividends, purchases, sales",
            400,
        )

    try:
        svc = _get_import_service(request)
        session = svc.create_session(
            content,
            format_hint=format_hint,
            currency=currency or "EUR",
            account_id=account_id or "_unassigned",
        )
        # Add staged_summary to response per contract
        session["staged_summary"] = {
            "total_rows": session.get("row_count", 0),
            "currencies": [session.get("currency", "EUR")],
            "date_range": [],
        }
        return JSONResponse(session, status_code=201)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except ValueError as exc:
        return _err("parse_error", str(exc), 400)
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("create_import_session failed")
        return _err("internal_error", str(exc), 500)


@router.get("/api/import/sessions/{session_id}")
async def get_import_session(request: Request, session_id: str):
    """GET /api/import/sessions/{session_id} — get session state and questions."""
    try:
        svc = _get_import_service(request)
        session = svc.get_session(session_id)
        if session is None:
            return _err("not_found", f"Session {session_id} not found or expired", 404)
        return JSONResponse(session)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("get_import_session failed")
        return _err("internal_error", str(exc), 500)


@router.post("/api/import/sessions/{session_id}/answers")
async def submit_answer(request: Request, session_id: str):
    """POST /api/import/sessions/{session_id}/answers — submit one answer."""
    try:
        body = await request.json()
    except Exception:
        return _err("validation_error", "Invalid JSON body", 400)

    required = ["question_id", "answer_type"]
    missing = [f for f in required if not body.get(f)]
    if missing:
        return _err(
            "validation_error",
            f"Missing required fields: {', '.join(missing)}",
            400,
        )

    valid_answer_types = {
        "SELECTED_CANDIDATE", "CREATED_NEW_SECURITY", "SKIPPED_COMPANY",
        "EXCLUDED_COMPANY", "BATCH_VALUE",
    }
    if body.get("answer_type") not in valid_answer_types:
        return _err(
            "invalid_answer",
            f"answer_type must be one of: {', '.join(sorted(valid_answer_types))}",
            400,
        )

    try:
        svc = _get_import_service(request)
        session = svc.answer_question(session_id, body)
        return JSONResponse(session)
    except LookupError:
        return _err("not_found", f"Session {session_id} not found or expired", 404)
    except StateError as exc:
        return _err("invalid_state", str(exc), 409)
    except ValueError as exc:
        return _err("invalid_answer", str(exc), 400)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("submit_answer failed")
        return _err("internal_error", str(exc), 500)


@router.post("/api/import/sessions/{session_id}/securities")
async def inline_create_security(request: Request, session_id: str):
    """POST /api/import/sessions/{session_id}/securities — inline security creation."""
    try:
        body = await request.json()
    except Exception:
        return _err("validation_error", "Invalid JSON body", 400)

    question_id = body.pop("question_id", None)
    if not question_id:
        return _err("validation_error", "question_id is required", 400)

    required = ["ticker", "company_name", "exchange_mic"]
    missing = [f for f in required if not body.get(f)]
    if missing:
        return _err(
            "validation_error",
            f"Missing required fields: {', '.join(missing)}",
            400,
        )

    # Validate and normalise provider_symbols if provided
    raw_ps = body.get("provider_symbols")
    if raw_ps is not None:
        try:
            cleaned_ps = validate_provider_symbols(raw_ps)
        except ValueError as exc:
            return _err("validation_error", str(exc), 400)
        body["provider_symbols"] = cleaned_ps if cleaned_ps else None

    try:
        svc = _get_import_service(request)
        session = svc.inline_create_security(session_id, question_id, body)
        return JSONResponse(session)
    except LookupError:
        return _err("not_found", f"Session {session_id} not found or expired", 404)
    except StateError as exc:
        return _err("invalid_state", str(exc), 409)
    except _CollisionError as exc:
        return JSONResponse(
            {"error": "collision", "existing": exc.existing},
            status_code=409,
        )
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("inline_create_security failed")
        return _err("internal_error", str(exc), 500)


@router.post("/api/import/sessions/{session_id}/preview")
async def generate_preview(request: Request, session_id: str):
    """POST /api/import/sessions/{session_id}/preview — generate commit preview."""
    try:
        svc = _get_import_service(request)
        result = svc.generate_preview(session_id)
        return JSONResponse(result)
    except LookupError:
        return _err("not_found", f"Session {session_id} not found or expired", 404)
    except StateError as exc:
        return _err("invalid_state", str(exc), 409)
    except UnresolvedQuestionsError as exc:
        return JSONResponse(
            {"error": "unresolved_questions", "pending": exc.pending},
            status_code=409,
        )
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("generate_preview failed")
        return _err("internal_error", str(exc), 500)


@router.post("/api/import/sessions/{session_id}/commit")
async def commit_session(request: Request, session_id: str):
    """POST /api/import/sessions/{session_id}/commit — commit movements to ledger."""
    try:
        svc = _get_import_service(request)
        result = svc.commit_session(session_id)
        return JSONResponse(result)
    except LookupError:
        return _err("not_found", f"Session {session_id} not found or expired", 404)
    except AlreadyCommittedError:
        return _err("already_committed", "Session already committed", 409)
    except StateError as exc:
        return _err("invalid_state", str(exc), 409)
    except UnresolvedQuestionsError as exc:
        return JSONResponse(
            {"error": "unresolved_questions", "pending": exc.pending},
            status_code=409,
        )
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("commit_session failed")
        return _err("internal_error", str(exc), 500)


# ===========================================================================
# Portfolio — Holdings & Movements
# ===========================================================================

@router.get("/api/portfolio/holdings")
async def get_holdings(
    request: Request,
    account_id: Optional[str] = Query(default=None),
):
    """GET /api/portfolio/holdings — derived holdings from ledger."""
    try:
        svc = _get_holdings_service(request)
        result = svc.compute_holdings(account_id=account_id)
        return JSONResponse(result)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("get_holdings failed")
        return _err("internal_error", str(exc), 500)


@router.get("/api/portfolio/movements")
async def get_movements(
    request: Request,
    account_id: Optional[str] = Query(default=None),
    security_id: Optional[str] = Query(default=None),
    txn_type: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """GET /api/portfolio/movements — paginated ledger entries."""
    _ALLOWED_TXN_TYPES = {"BUY", "SELL", "DIVIDEND", "TRANSFER_OUT", "TRANSFER_IN"}
    if txn_type and txn_type not in _ALLOWED_TXN_TYPES:
        return _err(
            "validation_error",
            "txn_type must be BUY, SELL, DIVIDEND, TRANSFER_OUT, or TRANSFER_IN",
            400,
        )

    try:
        svc = _get_portfolio_svc(request)
        movements, total = svc.get_movements(
            account_id=account_id,
            security_id=security_id,
            txn_type=txn_type,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
        )
        # Manually-created movements (and single-leg corporate actions) don't
        # have company_name written on the doc — only bulk-imported ones do.
        # Backfill it from security_master so the UI always shows a name.
        missing_ids = {
            m.get("security_id")
            for m in movements
            if m.get("security_id") and not m.get("company_name")
        }
        if missing_ids:
            names = _resolve_security_names(list(missing_ids), _get_securities_svc(request))
            for m in movements:
                if not m.get("company_name"):
                    m["company_name"] = names.get(m.get("security_id"), "")
        return JSONResponse({
            "movements": [_clean(m) for m in movements],
            "total_count": total,
            "limit": limit,
            "offset": offset,
        })
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("get_movements failed")
        return _err("internal_error", str(exc), 500)


@router.delete("/api/portfolio/movements/{movement_id}")
async def delete_movement(
    request: Request,
    movement_id: str,
    account_id: Optional[str] = Query(default=None),
):
    """DELETE /api/portfolio/movements/{movement_id} — permanently delete a movement.

    Removes the Cosmos document entirely regardless of correction_status.
    Group legs (ca_group_id present) are rejected; use the group endpoint instead.

    Query params:
      account_id  — required partition key (defaults to _unassigned if omitted)

    Responses:
      200  {"deleted": true, "id": "..."}
      404  not_found
      400  group_leg_hard_delete_required
    """
    if not account_id:
        account_id = "_unassigned"

    try:
        svc = _get_portfolio_svc(request)
        result = svc.delete_movement(movement_id, account_id)
        return JSONResponse(result)
    except LookupError as exc:
        return _err("not_found", str(exc), 404)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except ValueError as exc:
        msg = str(exc)
        if "group_leg_hard_delete_required" in msg:
            return _err("group_leg_hard_delete_required", msg, 400)
        return _err("validation_error", msg, 400)
    except Exception as exc:
        logger.exception("delete_movement failed")
        return _err("internal_error", str(exc), 500)


# ===========================================================================
# Phase 2 — Accounts
# ===========================================================================

@router.get("/api/portfolio/accounts")
async def list_accounts(request: Request):
    """GET /api/portfolio/accounts — list all broker accounts."""
    try:
        svc = _get_portfolio_svc(request)
        accounts = svc.list_accounts()
        return JSONResponse({"accounts": [_clean(a) for a in accounts]})
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("list_accounts failed")
        return _err("internal_error", str(exc), 500)


@router.post("/api/portfolio/accounts")
async def create_account(request: Request):
    """POST /api/portfolio/accounts — create a broker account."""
    try:
        body = await request.json()
    except Exception:
        return _err("validation_error", "Invalid JSON body", 400)

    valid_brokers = {"fidelity", "heytrade", "ing", "interactive_brokers", "other"}
    broker = body.get("broker", "")
    if broker not in valid_brokers:
        return _err(
            "validation_error",
            f"broker must be one of: {', '.join(sorted(valid_brokers))}",
            400,
        )
    name = str(body.get("name", "")).strip()
    if not name:
        return _err("validation_error", "name is required", 400)

    try:
        svc = _get_portfolio_svc(request)
        doc = svc.create_account(
            broker=broker,
            name=name,
            currency=str(body.get("currency", "EUR")).upper(),
            description=body.get("description"),
        )
        return JSONResponse(_clean(doc), status_code=201)
    except ValueError as exc:
        msg = str(exc)
        if "already exists" in msg:
            return _err("conflict", msg, 409)
        return _err("validation_error", msg, 400)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("create_account failed")
        return _err("internal_error", str(exc), 500)


@router.get("/api/portfolio/accounts/{account_id}")
async def get_account(request: Request, account_id: str):
    """GET /api/portfolio/accounts/{account_id} — get a single account."""
    try:
        svc = _get_portfolio_svc(request)
        doc = svc.get_account(account_id)
        if doc is None:
            return _err("not_found", f"Account {account_id!r} not found", 404)
        return JSONResponse(_clean(doc))
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("get_account failed")
        return _err("internal_error", str(exc), 500)


@router.put("/api/portfolio/accounts/{account_id}")
async def update_account(request: Request, account_id: str):
    """PUT /api/portfolio/accounts/{account_id} — update mutable account fields.

    account_id is immutable; supplying a different id in the body is ignored.
    Accepted fields: broker, name, currency, description.
    Returns 404 for a missing or soft-deleted account.
    """
    try:
        body = await request.json()
    except Exception:
        return _err("validation_error", "Invalid JSON body", 400)

    valid_brokers = {"fidelity", "heytrade", "ing", "interactive_brokers", "other"}
    broker = body.get("broker")
    if broker is not None and broker not in valid_brokers:
        return _err(
            "validation_error",
            f"broker must be one of: {', '.join(sorted(valid_brokers))}",
            400,
        )

    updates: Dict[str, Any] = {}
    if broker is not None:
        updates["broker"] = broker
    if "name" in body:
        name = str(body["name"]).strip()
        if not name:
            return _err("validation_error", "name cannot be blank", 400)
        updates["name"] = name
    if "currency" in body:
        updates["currency"] = str(body["currency"]).upper()
    if "description" in body:
        updates["description"] = body["description"]

    if not updates:
        return _err("validation_error", "No updatable fields provided", 400)

    try:
        svc = _get_portfolio_svc(request)
        doc = svc.update_account(account_id, updates)
        if doc is None:
            return _err("not_found", f"Account {account_id!r} not found", 404)
        return JSONResponse(_clean(doc))
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("update_account failed")
        return _err("internal_error", str(exc), 500)


@router.delete("/api/portfolio/accounts/{account_id}")
async def delete_account(request: Request, account_id: str):
    """DELETE /api/portfolio/accounts/{account_id} — soft-delete an account.

    Blocked with 409 if the account has active movements.
    """
    try:
        svc = _get_portfolio_svc(request)
        doc = svc.delete_account(account_id)
        if doc is None:
            return _err("not_found", f"Account {account_id!r} not found", 404)
        return JSONResponse({"id": doc.get("id"), "deleted_at": doc.get("deleted_at")})
    except ValueError as exc:
        msg = str(exc)
        if msg.startswith("account_has_movements:"):
            count = int(msg.split(":", 1)[1])
            return JSONResponse(
                {
                    "error": "account_has_movements",
                    "detail": f"Cannot delete account with {count} active movement(s)",
                    "movement_count": count,
                },
                status_code=409,
            )
        return _err("validation_error", msg, 400)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("delete_account failed")
        return _err("internal_error", str(exc), 500)


# ===========================================================================
# Phase 2 — Manual Movement Creation
# ===========================================================================

@router.post("/api/portfolio/movements")
async def create_movement(request: Request):
    """POST /api/portfolio/movements — create a manual BUY, SELL, or DIVIDEND."""
    try:
        body = await request.json()
    except Exception:
        return _err("validation_error", "Invalid JSON body", 400)

    txn_type = body.get("txn_type", "")
    if txn_type not in {"BUY", "SELL", "DIVIDEND"}:
        return _err(
            "validation_error",
            "txn_type must be BUY, SELL, or DIVIDEND (use POST /api/portfolio/transfers for transfers)",
            400,
        )

    for field in ("security_id", "trade_date", "gross"):
        if not body.get(field):
            return _err("validation_error", f"{field} is required", 400)

    if not isinstance(body.get("gross"), dict) or not body["gross"].get("eur_amount"):
        return _err("validation_error", "gross must include eur_amount", 400)

    try:
        svc = _get_portfolio_svc(request)

        # Duplicate check before writing
        duplicate = svc.find_probable_duplicate(
            security_id=body["security_id"],
            txn_type=txn_type,
            trade_date=body["trade_date"],
            quantity=str(body.get("quantity", "0")),
            gross_eur=str(body["gross"].get("eur_amount", "0")),
        )
        if duplicate:
            return JSONResponse(
                {"error": "probable_duplicate", "existing": _clean(duplicate)},
                status_code=409,
            )

        doc = svc.create_manual_movement(body)

        response_doc = _clean(doc)
        return JSONResponse(response_doc, status_code=201)
    except ValueError as exc:
        return _err("validation_error", str(exc), 400)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("create_movement failed")
        return _err("internal_error", str(exc), 500)


# ===========================================================================
# Phase 2 — Movement Detail & Correction
# ===========================================================================

@router.get("/api/portfolio/movements/{movement_id}")
async def get_movement(
    request: Request,
    movement_id: str,
    account_id: Optional[str] = Query(default="_unassigned"),
):
    """GET /api/portfolio/movements/{movement_id} — movement detail with correction chain."""
    try:
        svc = _get_portfolio_svc(request)
        doc = svc.get_movement(movement_id, account_id or "_unassigned")
        if doc is None:
            return _err("not_found", f"Movement {movement_id!r} not found", 404)

        # If superseded, include the replacement doc
        superseded_by = doc.get("superseded_by")
        replacement = None
        if superseded_by:
            replacement = svc.get_movement(superseded_by, account_id or "_unassigned")
            if replacement is None:
                # replacement may be in the same partition but not found — best-effort
                pass

        return JSONResponse({
            "movement": _clean(doc),
            "superseded_by": _clean(replacement) if replacement else None,
        })
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("get_movement failed")
        return _err("internal_error", str(exc), 500)


@router.post("/api/portfolio/movements/{movement_id}/correct")
async def correct_movement(request: Request, movement_id: str):
    """POST /api/portfolio/movements/{movement_id}/correct — replace a movement."""
    try:
        body = await request.json()
    except Exception:
        return _err("validation_error", "Invalid JSON body", 400)

    account_id = body.get("account_id", "")
    if not account_id:
        return _err("validation_error", "account_id is required", 400)
    if not body.get("correction_note", "").strip():
        return _err("validation_error", "correction_note is required", 400)

    try:
        svc = _get_portfolio_svc(request)
        result = svc.correct_movement(movement_id, account_id, body)
        return JSONResponse(result)
    except LookupError as exc:
        return _err("not_found", str(exc), 404)
    except ValueError as exc:
        msg = str(exc)
        if "already_superseded" in msg:
            return _err("already_superseded", msg, 409)
        if "transfer_not_correctable" in msg:
            return _err("transfer_not_correctable", msg, 405)
        return _err("validation_error", msg, 400)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("correct_movement failed")
        return _err("internal_error", str(exc), 500)


# ===========================================================================
# Phase 2 — Transfers
# ===========================================================================

@router.post("/api/portfolio/transfers")
async def create_transfer(request: Request):
    """POST /api/portfolio/transfers — create paired TRANSFER_OUT + TRANSFER_IN."""
    try:
        body = await request.json()
    except Exception:
        return _err("validation_error", "Invalid JSON body", 400)

    required = ["security_id", "trade_date", "quantity", "source_account_id", "dest_account_id"]
    missing = [f for f in required if not body.get(f)]
    if missing:
        return _err("validation_error", f"Missing required fields: {', '.join(missing)}", 400)

    if body["source_account_id"] == body["dest_account_id"]:
        return _err("validation_error", "source_account_id and dest_account_id must differ", 400)

    try:
        svc = _get_portfolio_svc(request)
        result = svc.create_transfer_pair(
            security_id=body["security_id"],
            trade_date=body["trade_date"],
            quantity=str(body["quantity"]),
            source_account_id=body["source_account_id"],
            dest_account_id=body["dest_account_id"],
            cost_basis_override_eur=body.get("cost_basis_override_eur"),
            transfer_fee=body.get("transfer_fee"),
            notes=body.get("notes"),
        )
        return JSONResponse(result, status_code=201)
    except InsufficientSharesError as exc:
        return JSONResponse(
            {
                "error": "insufficient_shares",
                "detail": f"Source account has {exc.available} shares, requested {exc.requested}",
                "available": exc.available,
                "requested": exc.requested,
            },
            status_code=409,
        )
    except ValueError as exc:
        return _err("validation_error", str(exc), 400)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("create_transfer failed")
        return _err("internal_error", str(exc), 500)


# ===========================================================================
# Phase 2 — Movement Reassignment
# ===========================================================================

@router.post("/api/portfolio/movements/{movement_id}/reassign")
async def reassign_movement(request: Request, movement_id: str):
    """POST /api/portfolio/movements/{movement_id}/reassign — move to different account."""
    try:
        body = await request.json()
    except Exception:
        return _err("validation_error", "Invalid JSON body", 400)

    source = body.get("source_account_id", "")
    dest = body.get("dest_account_id", "")
    if not source:
        return _err("validation_error", "source_account_id is required", 400)
    if not dest:
        return _err("validation_error", "dest_account_id is required", 400)
    reason = str(body.get("reason", "")).strip()
    if not reason:
        return _err("validation_error", "reason is required", 400)

    try:
        svc = _get_portfolio_svc(request)
        result = svc.reassign_movement(
            movement_id=movement_id,
            source_account_id=source,
            dest_account_id=dest,
            reason=reason,
        )
        return JSONResponse(result)
    except LookupError as exc:
        return _err("not_found", str(exc), 404)
    except ValueError as exc:
        msg = str(exc)
        if "same_account" in msg:
            return _err("same_account", msg, 409)
        if "already_reassigned" in msg:
            return _err("already_reassigned", msg, 409)
        return _err("validation_error", msg, 400)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("reassign_movement failed")
        return _err("internal_error", str(exc), 500)


@router.post("/api/portfolio/movements/batch-reassign/preview")
async def preview_batch_reassign(request: Request):
    """POST /api/portfolio/movements/batch-reassign/preview — dry-run count.

    Uses the exact same selection predicate as batch-reassign execution.
    Read-only: no writes are performed.

    The client must NOT pass the returned count back to execution; the
    server always re-derives the candidate set at execution time.
    """
    try:
        body = await request.json()
    except Exception:
        return _err("validation_error", "Invalid JSON body", 400)

    source = body.get("source_account_id", "")
    dest = body.get("dest_account_id", "")
    if not source:
        return _err("validation_error", "source_account_id is required", 400)
    if not dest:
        return _err("validation_error", "dest_account_id is required", 400)
    if source == dest:
        return _err("validation_error", "source_account_id and dest_account_id must differ", 400)

    try:
        svc = _get_portfolio_svc(request)
        result = svc.preview_batch_reassign(
            source_account_id=source,
            dest_account_id=dest,
            security_id=body.get("security_id"),
            date_from=body.get("date_from"),
            date_to=body.get("date_to"),
        )
        return JSONResponse(result)
    except ValueError as exc:
        return _err("validation_error", str(exc), 400)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("preview_batch_reassign failed")
        return _err("internal_error", str(exc), 500)


@router.post("/api/portfolio/movements/batch-reassign")
async def batch_reassign_movements(request: Request):
    """POST /api/portfolio/movements/batch-reassign — bulk reassignment."""
    try:
        body = await request.json()
    except Exception:
        return _err("validation_error", "Invalid JSON body", 400)

    source = body.get("source_account_id", "")
    dest = body.get("dest_account_id", "")
    if not source:
        return _err("validation_error", "source_account_id is required", 400)
    if not dest:
        return _err("validation_error", "dest_account_id is required", 400)
    if source == dest:
        return _err("validation_error", "source_account_id and dest_account_id must differ", 400)
    reason = (body.get("reason") or "").strip() or "Batch account reassignment"

    try:
        svc = _get_portfolio_svc(request)
        result = svc.batch_reassign_movements(
            source_account_id=source,
            dest_account_id=dest,
            security_id=body.get("security_id"),
            date_from=body.get("date_from"),
            date_to=body.get("date_to"),
            reason=reason,
        )
        return JSONResponse(result)
    except ValueError as exc:
        msg = str(exc)
        if "batch_reassign_failed" in msg:
            return _err("batch_reassign_failed", msg, 500)
        if "same_account" in msg:
            return _err("validation_error", msg, 400)
        return _err("validation_error", msg, 400)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except Exception as exc:
        logger.exception("batch_reassign_movements failed")
        return _err("internal_error", str(exc), 500)


# ===========================================================================
# Symbol Unification — Unified Add Symbol  (R6)
# ===========================================================================

def _start_symbol_warmup(request: Request, cosmos, ticker: str, security: dict) -> bool:
    """Fire-and-forget enrichment + forecast-backfill warm-up for a brand-new
    symbol_config (never for a pre-existing one — caller must gate on
    ``config_created is True``).

    Relocated verbatim (behavior-preserving) from the legacy
    ``POST /api/symbols`` handler (danny-single-add-symbol-contract.md §2.4).
    Resolves the Yahoo provider symbol first via the single resolution
    point (``resolve_yfinance_symbol`` — no duplicated suffix table); if the
    MIC can't be resolved (fail-closed), warm-up is skipped entirely and
    logged at info level — this is not an error.

    Returns:
        True iff a yf_symbol was resolved and both background threads were
        scheduled (``warmup_started``); False if skipped.
    """
    exchange_mic = security.get("exchange_mic") if security else None
    yf_symbol = resolve_yfinance_symbol(ticker, exchange_mic, security)
    if yf_symbol is None:
        logger.info(
            "add_symbol warm-up skipped for %s — no Yahoo symbol mapping for MIC=%s",
            ticker, exchange_mic or "unknown",
        )
        return False

    def _enrich():
        try:
            from src.portfolio_enrichment import enrich_symbol
            enrichment = enrich_symbol(ticker, yf_symbol=yf_symbol)
            if enrichment:
                cosmos.update_symbol_enrichment(ticker, enrichment)
                cosmos.record_enrichment_snapshot(
                    ticker,
                    (enrichment.get("technicals") or {}).get("score"),
                    enrichment.get("momentum", ""),
                )
        except Exception as exc:
            logger.warning(
                "add_symbol warm-up: enrichment failed for %s (yf_symbol=%s): %s",
                ticker, yf_symbol, exc, exc_info=True,
            )
    threading.Thread(target=_enrich, daemon=True).start()

    yf_provider = getattr(request.app.state, "yf_provider", None)

    def _seed_forecasts():
        try:
            from src.forecast_cron import (
                DEFAULT_BACKFILL_SESSIONS,
                backfill_symbol_forecasts,
            )
            backfill = backfill_symbol_forecasts(
                cosmos, yf_provider, ticker,
                sessions=DEFAULT_BACKFILL_SESSIONS,
            )
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                result = asyncio.run(backfill)
                logger.info("Forecast backfill completed for %s: %s", ticker, result)
            else:
                task = loop.create_task(backfill)

                def _log_backfill_result(completed):
                    try:
                        logger.info(
                            "Forecast backfill completed for %s: %s",
                            ticker,
                            completed.result(),
                        )
                    except Exception as exc:
                        logger.warning(
                            "Forecast backfill failed for newly created symbol %s: %s",
                            ticker, exc, exc_info=True,
                        )

                task.add_done_callback(_log_backfill_result)
        except Exception as exc:
            logger.warning(
                "Forecast backfill failed for newly created symbol %s: %s",
                ticker, exc, exc_info=True,
            )
    threading.Thread(target=_seed_forecasts, daemon=True).start()

    return True


@router.post("/api/symbols/add")
async def add_symbol(request: Request):
    """POST /api/symbols/add — create-or-select SecurityMaster + ensure symbol_config.

    Body (select existing):
        { "security_id": "XNYS:AAPL" }

    Body (create new):
        { "create": { "ticker": "AAPL", "exchange_mic": "XNYS",
                      "company_name": "Apple Inc.", "listing_currency": "USD", ... } }

    Returns 200 on select-existing, 201 on create-new.
    """
    try:
        body = await request.json()
    except Exception:
        return _err("validation_error", "Invalid JSON body", 400)

    svc = None
    security = None
    security_id = None

    try:
        svc = _get_securities_svc(request)
    except RuntimeError as exc:
        return _storage_503(str(exc))

    if body.get("security_id"):
        # SELECT existing SecurityMaster
        security_id = str(body["security_id"]).strip().upper()
        security = svc.get_security(security_id)
        if security is None:
            return _err("not_found", f"Security {security_id} not found", 404)
        created_new = False
    elif body.get("create"):
        # CREATE new SecurityMaster (reuses existing validation logic)
        create_data = body["create"]
        for field in ("ticker", "exchange_mic", "company_name"):
            if not create_data.get(field):
                return _err("validation_error", f"create.{field} is required", 400)
        try:
            security = svc.create_security(create_data)
            security_id = security["security_id"]
            created_new = True
        except _CollisionError as exc:
            return JSONResponse(
                {
                    "error": "collision",
                    "field": exc.field,
                    "detail": f"{exc.field} collision with existing security",
                    "existing": exc.existing,           # test compat key
                    "existing_security": exc.existing,  # Rusty contract key
                },
                status_code=409,
            )
        except ValueError as exc:
            return _err("validation_error", str(exc), 400)
        except RuntimeError as exc:
            return _storage_503(str(exc))
    else:
        return _err(
            "validation_error",
            "Provide 'security_id' to select existing or 'create' object to create new",
            400,
        )

    # ensure symbol_config (idempotent — existing configs are never touched)
    # Pre-check whether a config already exists *before* this call so
    # config_created/config_existed reflect the transition on THIS request,
    # not the persisted `_auto_enrolled` flag (which never resets on an
    # already-auto-enrolled doc and would otherwise misreport "created"
    # forever on every subsequent re-add of the same security — breaking
    # the exactly-once warm-up gate in §2.3 of the contract).
    config_warning = None
    config = None
    cosmos = None
    ticker = security_id_to_ticker(security_id)
    try:
        cosmos = _get_cosmos(request)
        symbols_container = cosmos.container
        try:
            symbols_container.read_item(
                item=f"config_{ticker}", partition_key=ticker,
            )
            pre_existed = True
        except CosmosResourceNotFoundError:
            pre_existed = False
        config = ensure_symbol_config(symbols_container, security_id, source="add_symbol")
    except Exception as exc:
        logger.warning(
            "ensure_symbol_config failed for add_symbol %s: %s",
            security_id,
            exc,
            exc_info=True,
        )
        config_warning = str(exc)
        pre_existed = None

    config_created = config is not None and pre_existed is False
    config_existed = config is not None and pre_existed is True

    warmup_started = False
    if config_created and cosmos is not None:
        try:
            warmup_started = _start_symbol_warmup(request, cosmos, ticker, security)
        except Exception as exc:
            # Warm-up scheduling itself must never fail the request.
            logger.warning(
                "add_symbol warm-up scheduling failed for %s: %s",
                security_id, exc, exc_info=True,
            )
            warmup_started = False

    return JSONResponse(
        {
            "security": security,
            "config_created": config_created,
            "config_existed": config_existed,
            "config_warning": config_warning,
            "warmup_started": warmup_started,
            "navigate_to": f"/symbols/{security_id}",
        },
        status_code=201 if created_new else 200,
    )


# ===========================================================================
# Symbol Unification — Admin Backfill Endpoints  (R3)
# ===========================================================================

@router.get("/api/admin/symbol-config-backfill")
async def symbol_config_backfill_dry_run(request: Request):
    """GET /api/admin/symbol-config-backfill?dry_run=true — backfill gap report.

    Scans all distinct security_ids in the portfolio ledger and reports which
    have no symbol_config (missing) and which have a config without a
    security_id field (collision_warnings).  Never writes anything.
    """
    try:
        portfolio_svc = _get_portfolio_svc(request)
        svc = _get_securities_svc(request)
        symbols_container = _get_cosmos(request).container
    except RuntimeError as exc:
        return _storage_503(str(exc))

    try:
        movements = portfolio_svc.get_all_movements_for_holdings()
    except Exception as exc:
        return _storage_503(f"portfolio scan failed: {exc}")

    # Distinct security_ids present in the ledger
    all_sids: dict = {}  # security_id → security_master doc or None
    for m in movements:
        sid = m.get("security_id")
        if sid and sid not in all_sids:
            all_sids[sid] = None

    # Resolve security_master for each (for company_name in report)
    for sid in list(all_sids.keys()):
        try:
            all_sids[sid] = svc.get_security(sid)
        except Exception:
            pass

    missing = []
    collision_warnings = []
    already_have_config = 0

    for sid, sec in all_sids.items():
        parts = sid.split(":", 1)
        ticker = parts[1].upper() if len(parts) == 2 else sid.upper()
        company_name = sec.get("company_name", "") if sec else ""
        config_id = f"config_{ticker}"
        try:
            config_doc = symbols_container.read_item(
                item=config_id, partition_key=ticker
            )
            already_have_config += 1
            existing_sid = config_doc.get("security_id")
            if not existing_sid or existing_sid != sid:
                collision_warnings.append({
                    "ticker": ticker,
                    "existing_config_security_id": existing_sid,
                    "candidate_security_id": sid,
                    "note": (
                        "Existing config has no security_id; will not overwrite"
                        if not existing_sid
                        else f"Existing config security_id={existing_sid!r} differs from portfolio security_id={sid!r}"
                    ),
                })
        except Exception:
            missing.append({
                "security_id": sid,
                "ticker": ticker,
                "company_name": company_name,
            })

    return JSONResponse({
        "dry_run": True,
        "total_portfolio_securities": len(all_sids),
        "already_have_config": already_have_config,
        "missing_config": len(missing),
        "missing": missing,
        "collision_warnings": collision_warnings,
    })


@router.post("/api/admin/symbol-config-backfill")
async def symbol_config_backfill_execute(request: Request):
    """POST /api/admin/symbol-config-backfill — confirmed backfill execution.

    Body: { "confirm": true }

    Creates symbol_configs for all portfolio securities that are missing one.
    Existing configs are NEVER modified.  Collision warnings reported but not
    auto-resolved.  Safe to re-run; idempotent.
    """
    try:
        body = await request.json()
    except Exception:
        return _err("validation_error", "Invalid JSON body", 400)

    if not body.get("confirm"):
        return _err(
            "confirmation_required",
            "Include { \"confirm\": true } to execute backfill",
            400,
        )

    try:
        portfolio_svc = _get_portfolio_svc(request)
        svc = _get_securities_svc(request)
        symbols_container = _get_cosmos(request).container
    except RuntimeError as exc:
        return _storage_503(str(exc))

    try:
        movements = portfolio_svc.get_all_movements_for_holdings()
    except Exception as exc:
        return _storage_503(f"portfolio scan failed: {exc}")

    all_sids: dict = {}
    for m in movements:
        sid = m.get("security_id")
        if sid and sid not in all_sids:
            all_sids[sid] = None

    for sid in list(all_sids.keys()):
        try:
            all_sids[sid] = svc.get_security(sid)
        except Exception:
            pass

    created = 0
    skipped_existing = 0
    collision_warnings = []
    errors = []

    for sid, sec in all_sids.items():
        parts = sid.split(":", 1)
        ticker = parts[1].upper() if len(parts) == 2 else sid.upper()
        company_name = sec.get("company_name", "") if sec else ""
        config_id = f"config_{ticker}"

        # Check existing config
        try:
            config_doc = symbols_container.read_item(
                item=config_id, partition_key=ticker
            )
            skipped_existing += 1
            existing_sid = config_doc.get("security_id")
            if not existing_sid or existing_sid != sid:
                collision_warnings.append({
                    "ticker": ticker,
                    "existing_config_security_id": existing_sid,
                    "candidate_security_id": sid,
                })
            continue
        except Exception:
            pass  # config missing → proceed to create

        # Create missing config
        try:
            ensure_symbol_config(symbols_container, sid, source="backfill")
            created += 1
            logger.info("backfill: created config_%s for %s", ticker, sid)
        except Exception as exc:
            logger.warning(
                "backfill: failed to create config_%s for %s: %s",
                ticker,
                sid,
                exc,
                exc_info=True,
            )
            errors.append({"security_id": sid, "error": str(exc)})

    return JSONResponse({
        "dry_run": False,
        "created": created,
        "skipped_existing": skipped_existing,
        "collision_warnings": collision_warnings,
        "errors": errors,
    })


# ===========================================================================
# Symbol Unification — total_shares Reconciliation Report  (R7)
# ===========================================================================

@router.get("/api/admin/total-shares-reconciliation")
async def total_shares_reconciliation(request: Request):
    """GET /api/admin/total-shares-reconciliation — report only, no writes.

    Compares symbol_config.total_shares against portfolio-derived share counts
    using the full CMP holdings computation.  Statuses: 'match', 'mismatch',
    'no_portfolio_data', 'no_config'.
    """
    from decimal import Decimal, ROUND_HALF_UP

    try:
        holdings_svc = _get_holdings_service(request)
        symbols_container = _get_cosmos(request).container
    except RuntimeError as exc:
        return _storage_503(str(exc))

    try:
        holdings_result = holdings_svc.compute_holdings()
    except Exception as exc:
        logger.warning("total_shares_reconciliation: compute_holdings failed: %s", exc)
        return _err("internal_error", f"holdings computation failed: {exc}", 500)

    holdings_by_sid = {
        h["security_id"]: h for h in holdings_result.get("holdings", [])
    }

    # Load all symbol_configs
    try:
        configs_query = "SELECT * FROM c WHERE c.doc_type = 'symbol_config'"
        all_configs = list(symbols_container.query_items(
            query=configs_query, enable_cross_partition_query=True
        ))
    except Exception as exc:
        return _err("internal_error", f"symbol_config scan failed: {exc}", 500)

    reconciliation = []
    matched = 0
    mismatched = 0
    no_portfolio_data = 0

    for config in all_configs:
        ticker = config.get("symbol", "")
        sid = config.get("security_id")
        config_total_shares = config.get("total_shares", 0) or 0

        holding = holdings_by_sid.get(sid) if sid else None

        if holding is None:
            # Try ticker fallback (catches configs without security_id)
            holding = next(
                (h for h in holdings_result.get("holdings", [])
                 if h.get("ticker", "").upper() == ticker.upper()),
                None,
            )

        if holding is None:
            portfolio_derived = None
            delta = None
            status = "no_portfolio_data"
            no_portfolio_data += 1
        else:
            portfolio_derived = holding.get("total_shares", "0")
            try:
                delta_d = Decimal(str(portfolio_derived)) - Decimal(str(config_total_shares))
                delta = str(delta_d.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))
                if delta_d == Decimal("0"):
                    status = "match"
                    matched += 1
                else:
                    status = "mismatch"
                    mismatched += 1
            except Exception:
                delta = None
                status = "mismatch"
                mismatched += 1

        reconciliation.append({
            "ticker": ticker,
            "security_id": sid,
            "config_total_shares": config_total_shares,
            "portfolio_derived_shares": portfolio_derived,
            "delta": delta,
            "status": status,
        })

    reconciliation.sort(key=lambda r: (r["status"] != "mismatch", r["ticker"] or ""))

    return JSONResponse({
        "reconciliation": reconciliation,
        "summary": {
            "total_symbols": len(reconciliation),
            "matched": matched,
            "mismatched": mismatched,
            "no_portfolio_data": no_portfolio_data,
        },
    })

# ===========================================================================
# Phase 2 — FX Rates
# ===========================================================================

@router.get("/api/fx/rates")
async def get_fx_rate_endpoint(
    request: Request,
    from_currency: str = Query(..., description="Source currency (e.g. USD)"),
    to_currency: str = Query(default="EUR", description="Target currency (must be EUR)"),
    date: Optional[str] = Query(default=None, description="Date YYYY-MM-DD; defaults to today"),
):
    """GET /api/fx/rates — look up EUR reference rate from ECB."""
    from_currency = from_currency.strip().upper()
    to_currency = to_currency.strip().upper()

    if to_currency != "EUR":
        return _err("validation_error", "Only EUR is supported as to_currency", 400)

    try:
        rate = get_fx_rate(from_currency, to_currency, rate_date=date)
        from datetime import date as _date
        effective_date = date or _date.today().isoformat()
        return JSONResponse({
            "from_currency": from_currency,
            "to_currency": to_currency,
            "date": effective_date,
            "rate": rate,
            "rate_source": "ECB",
            "note": None,
        })
    except ValueError as exc:
        return _err("validation_error", str(exc), 400)
    except FxRateNotFoundError as exc:
        return _err("rate_not_found", str(exc), 404)
    except FxUnavailableError as exc:
        return JSONResponse(
            {"error": "fx_unavailable", "detail": str(exc)},
            status_code=503,
        )
    except Exception as exc:
        logger.exception("get_fx_rate_endpoint failed")
        return _err("internal_error", str(exc), 500)


# ===========================================================================
# Amendment H — Corporate-Action Groups
# ===========================================================================

@router.post("/api/portfolio/corporate-actions")
async def create_corporate_action(request: Request):
    """POST /api/portfolio/corporate-actions — create a linked corporate-action group.

    Amendment H §H.3.6: creates N ledger_txn docs sharing a ca_group_id.
    All-or-nothing: if any leg fails validation, the entire request is rejected.
    """
    try:
        body = await request.json()
    except Exception:
        return _err("validation_error", "Invalid JSON body", 400)

    for field in ("event_type", "security_id", "payment_date", "legs"):
        if not body.get(field):
            return _err("validation_error", f"{field} is required", 400)

    if not isinstance(body.get("legs"), list):
        return _err("validation_error", "legs must be a list", 400)

    try:
        svc = _get_portfolio_svc(request)
        result = svc.create_corporate_action(body)
        return JSONResponse(result, status_code=201)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except ValueError as exc:
        return _err("validation_error", str(exc), 400)
    except Exception as exc:
        logger.exception("create_corporate_action failed")
        return _err("internal_error", str(exc), 500)


@router.post("/api/portfolio/corporate-actions/{ca_group_id}/void")
async def void_corporate_action_group(request: Request, ca_group_id: str):
    """POST /api/portfolio/corporate-actions/{ca_group_id}/void — void all active legs.

    Amendment H §H.3.7: atomically voids all active legs in the group.
    """
    try:
        body = await request.json()
    except Exception:
        return _err("validation_error", "Invalid JSON body", 400)

    account_id = body.get("account_id", "")
    if not account_id:
        return _err("validation_error", "account_id is required", 400)

    reason = body.get("reason", "")

    try:
        svc = _get_portfolio_svc(request)
        result = svc.void_corporate_action_group(ca_group_id, account_id, reason)
        return JSONResponse(result)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except ValueError as exc:
        msg = str(exc)
        if "no_active_legs" in msg:
            return _err("not_found", msg, 404)
        return _err("validation_error", msg, 400)
    except Exception as exc:
        logger.exception("void_corporate_action_group failed")
        return _err("internal_error", str(exc), 500)


@router.post("/api/portfolio/corporate-actions/{ca_group_id}/correct")
async def correct_corporate_action_group(request: Request, ca_group_id: str):
    """POST /api/portfolio/corporate-actions/{ca_group_id}/correct

    Atomically replace a corporate-action group with corrected legs.
    All original legs are superseded; new legs share a new ca_group_id and
    carry replaces_ca_group_id pointing to the original.

    Request body:
      {
        "account_id": "<str>",          # required
        "correction_note": "<str>",     # required, non-empty
        "event_type": "<CaEventType>",  # e.g. CASH_DIVIDEND
        "security_id": "<str|null>",    # optional — inferred from original legs
        "payment_date": "<YYYY-MM-DD|null>",
        "notes": "<str|null>",
        "legs": [                       # required, non-empty list
          {
            "leg_type": "CASH_DIVIDEND|RIGHTS_SOLD|SHARE_ACQUISITION|CASH_TOP_UP",
            "trade_date": "...",
            "quantity": "...",
            "gross": {"amount": "...", "currency": "...", "eur_amount": "..."},
            "fees": {"total": "...", "currency": "...", "total_eur": "..."},
            "withholding": {... or null},
            "fx": {... or null},
            "cost_basis_status": "COMPLETE|INCOMPLETE|null",
            "notes": "<str|null>"
          }
        ]
      }

    Errors:
      400 validation_error   — invalid fields / missing required legs / empty correction_note
      404 not_found          — no active legs for ca_group_id
      409 integrity_error    — phase-2 supersession failed; new docs deleted; original intact
      400 ca_group_correction_failed — phase-1 write failed; no partial state
    """
    try:
        body = await request.json()
    except Exception:
        return _err("validation_error", "Invalid JSON body", 400)

    body["ca_group_id_path"] = ca_group_id  # informational only

    try:
        svc = _get_portfolio_svc(request)
        result = svc.correct_corporate_action_group(ca_group_id, body)
        return JSONResponse(result, status_code=201)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except ValueError as exc:
        msg = str(exc)
        if "no_active_legs" in msg:
            return _err("not_found", msg, 404)
        if "integrity_error" in msg:
            return _err("integrity_error", msg, 409)
        if "ca_group_correction_failed" in msg:
            return _err("ca_group_correction_failed", msg, 400)
        return _err("validation_error", msg, 400)
    except Exception as exc:
        logger.exception("correct_corporate_action_group failed")
        return _err("internal_error", str(exc), 500)


@router.delete("/api/portfolio/corporate-actions/{ca_group_id}")
async def delete_corporate_action_group(
    request: Request,
    ca_group_id: str,
    account_id: Optional[str] = Query(default=None),
):
    """DELETE /api/portfolio/corporate-actions/{ca_group_id}

    Permanently deletes ALL documents (any correction_status) belonging to the group.

    Query params:
      account_id — used as fallback partition key when a leg lacks one

    Responses:
      200  {"deleted_count": N, "ids": [...]}
      404  not_found (no docs for this ca_group_id)
    """
    if not account_id:
        account_id = "_unassigned"

    try:
        svc = _get_portfolio_svc(request)
        result = svc.delete_corporate_action_group(ca_group_id, account_id)
        return JSONResponse(result)
    except LookupError as exc:
        return _err("not_found", str(exc), 404)
    except StorageUnavailableError as exc:
        return _storage_503(str(exc))
    except RuntimeError as exc:
        return _storage_503(str(exc))
    except ValueError as exc:
        msg = str(exc)
        if "no_legs_found" in msg:
            return _err("not_found", msg, 404)
        return _err("validation_error", msg, 400)
    except Exception as exc:
        logger.exception("delete_corporate_action_group failed")
        return _err("internal_error", str(exc), 500)
