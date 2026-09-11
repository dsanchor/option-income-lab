"""Cosmos operations for portfolio and import_sessions containers.

portfolio container:
  - Partition key: /account_id
  - Doc types: ledger_txn, account, import_batch

import_sessions container:
  - Partition key: /session_id
  - Doc types: import_session (7-day TTL)

Both containers are best-effort at startup (following repository convention).
When unavailable, operations raise StorageUnavailableError so the router can
return HTTP 503 with the frozen error shape.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional
from uuid import uuid4

from azure.cosmos.exceptions import CosmosResourceNotFoundError

from .models import (
    OPTION_BUY_TXN_TYPES as _MODEL_OPTION_BUY_TYPES,
    OPTION_SELL_TXN_TYPES as _MODEL_OPTION_SELL_TYPES,
    OPTION_TXN_TYPES as _MODEL_OPTION_TXN_TYPES,
)

logger = logging.getLogger(__name__)

_COSMOS_SYSTEM_KEYS = {"_rid", "_self", "_etag", "_attachments", "_ts"}

# 7-day TTL for import sessions (in seconds)
_SESSION_TTL_SECONDS = 7 * 24 * 3600

# Correction statuses excluded from active holdings
_EXCLUDED_CORRECTION_STATUSES = {"SUPERSEDED", "VOIDED"}

# Transfer txn_types
_TRANSFER_TYPES = {"TRANSFER_OUT", "TRANSFER_IN"}

_OPTION_TXN_TYPES = _MODEL_OPTION_TXN_TYPES
_OPTION_SELL_TYPES = _MODEL_OPTION_SELL_TYPES
_OPTION_BUY_TYPES = _MODEL_OPTION_BUY_TYPES
_STOCK_BUY_TYPES = frozenset({"BUY"})
_STOCK_SELL_TYPES = frozenset({"SELL"})
_BUY_LIKE_TXN_TYPES = _STOCK_BUY_TYPES | _OPTION_BUY_TYPES
_SELL_LIKE_TXN_TYPES = _STOCK_SELL_TYPES | _OPTION_SELL_TYPES
_OPTION_LINK_KINDS = frozenset({"OPEN_SELL", "CLOSE_BUY", "ASSIGNMENT_STOCK"})
_OPTION_TYPE_VALUES = frozenset({"call", "put"})

# Imported at module level so tests can patch `src.portfolio.cosmos_portfolio.ensure_symbol_config`
from .symbol_config_sync import ensure_symbol_config  # noqa: E402


class StorageUnavailableError(Exception):
    """Raised when a required Cosmos container is not available."""


class InsufficientSharesError(Exception):
    """Raised when a transfer source account lacks sufficient shares."""
    def __init__(self, available: str, requested: str) -> None:
        self.available = available
        self.requested = requested
        super().__init__(f"Insufficient shares: available={available}, requested={requested}")


def _clean(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in _COSMOS_SYSTEM_KEYS}


def _slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    nfkd = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in nfkd if not unicodedata.combining(c))
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _d(v: Any) -> Decimal:
    if isinstance(v, Decimal):
        return v
    if v is None:
        return Decimal("0")
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal("0")


def _derive_wht_rate_pct(amount_eur: Decimal, gross_eur: Decimal) -> str:
    """Derive WHT rate_pct = amount_eur / gross_eur × 100 (server-authoritative).

    Returns "0" when gross_eur is zero (no division error).
    Rounds to 2 decimal places.
    """
    if gross_eur == Decimal("0"):
        return "0"
    rate = (amount_eur / gross_eur) * Decimal("100")
    return str(rate.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _apply_wht_rate_derivation(wht: Optional[Dict[str, Any]], gross_eur: Decimal) -> Optional[Dict[str, Any]]:
    """Derive and overwrite rate_pct in withholding source/destination dicts.

    Returns a shallow-copied withholding dict with server-computed rate_pct fields.
    Does not mutate the input dict.
    """
    if not isinstance(wht, dict):
        return wht
    result = dict(wht)
    if isinstance(result.get("source"), dict):
        src = dict(result["source"])
        src["rate_pct"] = _derive_wht_rate_pct(_d(src.get("amount_eur", "0")), gross_eur)
        result["source"] = src
    if isinstance(result.get("destination"), dict):
        dst = dict(result["destination"])
        dst["rate_pct"] = _derive_wht_rate_pct(_d(dst.get("amount_eur", "0")), gross_eur)
        result["destination"] = dst
    return result


def _ensure_ledger_detail_fields(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Backfill required detail-dialog financial fields when absent.

    Imported movements already persist these fields consistently. Some manual
    write paths historically omitted them, so new/rebuilt docs must guarantee
    their presence.
    """
    currency = "EUR"
    gross = doc.get("gross")
    if isinstance(gross, dict):
        currency = str(gross.get("currency") or "EUR").upper()
    elif isinstance(doc.get("net"), dict):
        currency = str((doc["net"] or {}).get("currency") or "EUR").upper()

    if not isinstance(doc.get("gross"), dict):
        doc["gross"] = {"amount": "0", "currency": currency, "eur_amount": "0"}
    if not isinstance(doc.get("fees"), dict):
        doc["fees"] = {"total": "0", "currency": currency, "total_eur": "0"}
    if not isinstance(doc.get("net"), dict):
        doc["net"] = {"amount": "0", "currency": currency, "eur_amount": "0"}

    if not isinstance(doc.get("withholding"), dict):
        doc["withholding"] = {"source": None, "destination": None}
    else:
        doc["withholding"] = {
            "source": doc["withholding"].get("source"),
            "destination": doc["withholding"].get("destination"),
        }

    return doc


def _compute_manual_net(
    txn_type: str,
    gross: Dict[str, Any],
    fees: Dict[str, Any],
    withholding: Optional[Dict[str, Any]],
) -> Dict[str, str]:
    """Compute net amounts for manual and corrected ledger movements."""
    gross_eur = _d(gross.get("eur_amount", "0"))
    fees_eur = _d(fees.get("total_eur", "0"))
    gross_amount = _d(gross.get("amount", "0"))
    fee_amount = _d(fees.get("total", "0"))
    currency = str(gross.get("currency", "EUR")).upper()

    wht_source_eur = Decimal("0")
    wht_dest_eur = Decimal("0")
    if isinstance(withholding, dict):
        src = withholding.get("source") or {}
        dst = withholding.get("destination") or {}
        wht_source_eur = _d(src.get("amount_eur", "0"))
        wht_dest_eur = _d(dst.get("amount_eur", "0"))

    total_wht_eur = wht_source_eur + wht_dest_eur
    if txn_type in _BUY_LIKE_TXN_TYPES:
        net_eur = gross_eur + fees_eur
        net_amount = gross_amount + fee_amount
    else:
        net_eur = gross_eur - fees_eur - total_wht_eur
        if currency == "EUR":
            net_amount = gross_amount - fee_amount - total_wht_eur
        elif total_wht_eur == Decimal("0"):
            net_amount = gross_amount - fee_amount
        else:
            net_amount = net_eur

    return {
        "amount": str(net_amount.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)),
        "currency": currency,
        "eur_amount": str(net_eur.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)),
    }


def _normalize_option_metadata(txn_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and extract option-link metadata fields for a movement."""
    option_position_id = data.get("option_position_id")
    option_link_kind = data.get("option_link_kind")
    option_type = data.get("option_type")
    has_option_metadata = any(
        data.get(field) is not None
        for field in (
            "option_position_id",
            "option_link_kind",
            "option_type",
            "option_strike",
            "option_expiration",
            "option_symbol",
            "option_close_date",
        )
    )

    if txn_type in _OPTION_TXN_TYPES:
        if option_link_kind is not None and option_link_kind not in ("OPEN_SELL", "CLOSE_BUY"):
            raise ValueError("option_link_kind must be OPEN_SELL or CLOSE_BUY for option movements")
        if option_type is not None and option_type not in _OPTION_TYPE_VALUES:
            raise ValueError("option_type must be call or put")
        if option_link_kind is not None:
            if txn_type in _OPTION_SELL_TYPES and option_link_kind != "OPEN_SELL":
                raise ValueError("option_link_kind must be OPEN_SELL for CALL_SELL and PUT_SELL")
            if txn_type in _OPTION_BUY_TYPES and option_link_kind != "CLOSE_BUY":
                raise ValueError("option_link_kind must be CLOSE_BUY for CALL_BUY and PUT_BUY")
        if option_type is not None:
            if txn_type.startswith("CALL_") and option_type != "call":
                raise ValueError("option_type must be call for CALL_* movements")
            if txn_type.startswith("PUT_") and option_type != "put":
                raise ValueError("option_type must be put for PUT_* movements")
    elif txn_type in _STOCK_BUY_TYPES | _STOCK_SELL_TYPES:
        if has_option_metadata:
            if option_link_kind != "ASSIGNMENT_STOCK":
                raise ValueError("stock assignment-linked movements require option_link_kind=ASSIGNMENT_STOCK")
            if not option_position_id:
                raise ValueError("option_position_id is required when linking stock movements to an option position")
        if option_link_kind is not None and option_link_kind not in _OPTION_LINK_KINDS:
            raise ValueError(
                "option_link_kind must be OPEN_SELL, CLOSE_BUY, or ASSIGNMENT_STOCK"
            )
        if option_link_kind in _OPTION_LINK_KINDS and option_link_kind != "ASSIGNMENT_STOCK":
            raise ValueError("stock movements only support option_link_kind=ASSIGNMENT_STOCK")
        if option_type is not None and option_type not in _OPTION_TYPE_VALUES:
            raise ValueError("option_type must be call or put")
    elif has_option_metadata:
        raise ValueError("option-link metadata is only applicable to option or stock BUY/SELL movements")
    elif option_link_kind is not None and option_link_kind not in _OPTION_LINK_KINDS:
        raise ValueError(
            "option_link_kind must be OPEN_SELL, CLOSE_BUY, or ASSIGNMENT_STOCK"
        )
    elif option_type is not None and option_type not in _OPTION_TYPE_VALUES:
        raise ValueError("option_type must be call or put")

    metadata: Dict[str, Any] = {}
    for field in (
        "option_position_id",
        "option_link_kind",
        "option_type",
        "option_strike",
        "option_expiration",
        "option_symbol",
        "option_close_date",
    ):
        if data.get(field) is not None:
            metadata[field] = data.get(field)
    return metadata


# Leg type → txn_type mapping for corporate-action groups (Amendment H §H.3.3)
_CA_LEG_TXN_TYPE = {
    "CASH_DIVIDEND": "DIVIDEND",
    "RIGHTS_SOLD": "SELL",
    "SHARE_ACQUISITION": "BUY",
    "CASH_TOP_UP": "BUY",
    "CONSOLIDATION_OUT": "TRANSFER_OUT",
    "CONSOLIDATION_IN": "TRANSFER_IN",
    "FRACTIONAL_CASH_OUT": "SELL",
}

# Required leg types per event type (Amendment H §H.4)
_CA_REQUIRED_LEGS: Dict[str, set] = {
    "CASH_DIVIDEND": {"CASH_DIVIDEND"},
    "DIVIDEND_WITH_SCRIP": {"CASH_DIVIDEND", "SHARE_ACQUISITION"},
    "SCRIP_DIVIDEND": {"SHARE_ACQUISITION"},
    "RIGHTS_ISSUE": {"SHARE_ACQUISITION"},
    "SHARE_CONSOLIDATION": {"CONSOLIDATION_OUT", "CONSOLIDATION_IN"},
}

# Financial fields on a group leg that must not be patched individually.
# Non-financial fields (trade_date, notes) remain individually correctable.
_CA_FINANCIAL_FIELDS: frozenset = frozenset({
    "gross", "fees", "withholding", "quantity", "fx", "sales_type", "cost_basis_status",
})


def _validate_correction_fields(txn_type: str, correction_data: Dict[str, Any]) -> None:
    """Validate correction field constraints per movement type.

    Raises ValueError with a human-readable detail message on the first violation found.
    All per-field structural checks (enum values, numeric ranges, required sub-fields,
    type applicability) are centralised here so correct_movement stays readable.
    """
    # ── Type-specific field restrictions ──────────────────────────────────
    if txn_type in _STOCK_BUY_TYPES:
        if "withholding" in correction_data and correction_data["withholding"] is not None:
            raise ValueError("withholding is not applicable to BUY movements")
        if correction_data.get("sales_type") is not None:
            raise ValueError("sales_type is not applicable to BUY movements")

    if txn_type in _STOCK_SELL_TYPES:
        if correction_data.get("cost_basis_status") is not None:
            raise ValueError("cost_basis_status is not applicable to SELL movements")

    if txn_type in _OPTION_BUY_TYPES | _OPTION_SELL_TYPES:
        if "withholding" in correction_data and correction_data["withholding"] is not None:
            raise ValueError("withholding is not applicable to option movements")
        if correction_data.get("sales_type") is not None:
            raise ValueError("sales_type is not applicable to option movements")
        if correction_data.get("cost_basis_status") is not None:
            raise ValueError("cost_basis_status is not applicable to option movements")
        if "quantity" in correction_data and correction_data["quantity"] not in (None, "0", 0, 0.0):
            raise ValueError("quantity must remain 0 for option movements")

    if txn_type == "DIVIDEND":
        if correction_data.get("sales_type") is not None:
            raise ValueError("sales_type is not applicable to DIVIDEND movements")
        if correction_data.get("cost_basis_status") is not None:
            raise ValueError("cost_basis_status is not applicable to DIVIDEND movements")

    # ── Gross structure ───────────────────────────────────────────────────
    if correction_data.get("gross") is not None:
        g = correction_data["gross"]
        if not isinstance(g, dict):
            raise ValueError("gross must be an object with amount, currency, and eur_amount")
        for req in ("amount", "currency", "eur_amount"):
            if req not in g or g[req] is None:
                raise ValueError(f"gross.{req} is required when providing gross")
        try:
            if Decimal(str(g["amount"])) < Decimal("0"):
                raise ValueError("gross.amount must be >= 0")
            if Decimal(str(g["eur_amount"])) < Decimal("0"):
                raise ValueError("gross.eur_amount must be >= 0")
        except ValueError:
            raise
        except Exception:
            raise ValueError("gross.amount and gross.eur_amount must be valid numeric strings")

    # ── Fees structure ────────────────────────────────────────────────────
    if correction_data.get("fees") is not None:
        f = correction_data["fees"]
        if not isinstance(f, dict):
            raise ValueError("fees must be an object with total, currency, and total_eur")
        for req in ("total", "currency", "total_eur"):
            if req not in f or f[req] is None:
                raise ValueError(f"fees.{req} is required when providing fees")

    # ── FX structure ──────────────────────────────────────────────────────
    if correction_data.get("fx") is not None:
        fx = correction_data["fx"]
        if not isinstance(fx, dict):
            raise ValueError("fx must be an object with rate and rate_source")
        if "rate" not in fx:
            raise ValueError("fx.rate is required")
        try:
            rate_val = Decimal(str(fx["rate"]))
        except Exception:
            raise ValueError("fx.rate must be a valid positive number")
        if rate_val <= Decimal("0"):
            raise ValueError("fx.rate must be > 0")
        if "rate_source" in fx and fx["rate_source"] not in ("ECB", "BROKER", "MANUAL"):
            raise ValueError("fx.rate_source must be one of: ECB, BROKER, MANUAL")

    # ── sales_type enum ───────────────────────────────────────────────────
    if correction_data.get("sales_type") is not None:
        if correction_data["sales_type"] not in ("ACCIONES", "DERECHOS"):
            raise ValueError("sales_type must be ACCIONES or DERECHOS")

    # ── cost_basis_status enum ────────────────────────────────────────────
    if correction_data.get("cost_basis_status") is not None:
        if correction_data["cost_basis_status"] not in ("COMPLETE", "INCOMPLETE"):
            raise ValueError("cost_basis_status must be COMPLETE or INCOMPLETE")

    # ── Withholding structure (D.2) ───────────────────────────────────────
    if "withholding" in correction_data and correction_data["withholding"] is not None:
        wht = correction_data["withholding"]
        if not isinstance(wht, dict):
            raise ValueError("withholding must be an object or null")
        if "source" in wht and wht["source"] is not None:
            src = wht["source"]
            if not isinstance(src, dict):
                raise ValueError("withholding.source must be an object or null")
            if "amount_eur" not in src:
                raise ValueError("withholding.source must include amount_eur")
        if "destination" in wht and wht["destination"] is not None:
            dst = wht["destination"]
            if not isinstance(dst, dict):
                raise ValueError("withholding.destination must be an object or null")
            if "amount_eur" not in dst:
                raise ValueError("withholding.destination must include amount_eur")

    # ── Quantity: null only permitted for DIVIDEND ────────────────────────
    if "quantity" in correction_data and correction_data["quantity"] is None:
        if txn_type != "DIVIDEND":
            raise ValueError(
                "quantity must be a non-null numeric string for BUY and SELL movements"
            )

    if correction_data.get("quantity") is not None:
        try:
            q_val = Decimal(str(correction_data["quantity"]))
        except Exception:
            raise ValueError("quantity must be a valid numeric string")
        if q_val < Decimal("0"):
            raise ValueError("quantity must be >= 0")
        if txn_type in _OPTION_TXN_TYPES and q_val != Decimal("0"):
            raise ValueError("quantity must remain 0 for option movements")

    # ── Option-link metadata: field-level validation only; full
    # cross-field coherence is checked again after overlaying onto the
    # original movement in correct_movement().
    if correction_data.get("option_link_kind") is not None:
        if correction_data["option_link_kind"] not in _OPTION_LINK_KINDS:
            raise ValueError(
                "option_link_kind must be OPEN_SELL, CLOSE_BUY, or ASSIGNMENT_STOCK"
            )
    if correction_data.get("option_type") is not None:
        if correction_data["option_type"] not in _OPTION_TYPE_VALUES:
            raise ValueError("option_type must be call or put")
    if correction_data.get("option_expiration") is not None:
        try:
            datetime.fromisoformat(str(correction_data["option_expiration"]).replace("Z", "+00:00"))
        except ValueError:
            try:
                datetime.strptime(str(correction_data["option_expiration"]), "%Y-%m-%d")
            except ValueError:
                raise ValueError("option_expiration must be YYYY-MM-DD")
    if correction_data.get("option_close_date") is not None:
        try:
            datetime.fromisoformat(str(correction_data["option_close_date"]).replace("Z", "+00:00"))
        except ValueError:
            try:
                datetime.strptime(str(correction_data["option_close_date"]), "%Y-%m-%d")
            except ValueError:
                raise ValueError("option_close_date must be YYYY-MM-DD")
    if correction_data.get("option_strike") is not None:
        try:
            Decimal(str(correction_data["option_strike"]))
        except Exception:
            raise ValueError("option_strike must be numeric")


class CosmosPortfolioService:
    """Portfolio and import-sessions Cosmos operations."""

    def __init__(
        self,
        portfolio_container,
        import_sessions_container,
        symbols_container=None,
    ) -> None:
        self.portfolio_container = portfolio_container
        self.import_sessions_container = import_sessions_container
        # Optional — used for best-effort ensure_symbol_config calls.
        # When None, ensure calls are logged as warnings and skipped.
        self.symbols_container = symbols_container

    @property
    def portfolio_available(self) -> bool:
        return self.portfolio_container is not None

    @property
    def sessions_available(self) -> bool:
        return self.import_sessions_container is not None

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _require_sessions(self) -> None:
        if not self.sessions_available:
            raise StorageUnavailableError(
                "import_sessions container not configured — "
                "run scripts/provision_cosmosdb.sh"
            )

    def _require_portfolio(self) -> None:
        if not self.portfolio_available:
            raise StorageUnavailableError(
                "portfolio container not configured — "
                "run scripts/provision_cosmosdb.sh"
            )

    # ── Import Sessions ─────────────────────────────────────────────────

    def create_session(self, session_doc: Dict[str, Any]) -> Dict[str, Any]:
        """Write a new import_session document. Raises StorageUnavailableError."""
        self._require_sessions()
        created = self.import_sessions_container.create_item(session_doc)
        return _clean(created)

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Read an import_session by session_id. Returns None if not found."""
        self._require_sessions()
        try:
            doc = self.import_sessions_container.read_item(
                item=session_id, partition_key=session_id
            )
            return _clean(doc)
        except CosmosResourceNotFoundError:
            return None

    def update_session(self, session_doc: Dict[str, Any]) -> Dict[str, Any]:
        """Replace an existing import_session document."""
        self._require_sessions()
        session_doc["updated_at"] = self._now()
        updated = self.import_sessions_container.replace_item(
            item=session_doc["id"], body=session_doc
        )
        return _clean(updated)

    # ── Accounts ─────────────────────────────────────────────────────────

    def create_account(self, broker: str, name: str, currency: str = "EUR",
                       description: Optional[str] = None) -> Dict[str, Any]:
        """Create a broker account document in the portfolio container.

        The document ID and account_id are a stable slug derived from broker+name.
        Raises StorageUnavailableError or ValueError on duplicate.
        """
        self._require_portfolio()
        account_id = f"acct_{_slugify(broker)}_{_slugify(name)}"
        now = self._now()
        doc: Dict[str, Any] = {
            "id": account_id,
            "account_id": account_id,
            "doc_type": "account",
            "broker": broker,
            "name": name,
            "currency": currency.upper(),
            "created_at": now,
            "updated_at": now,
        }
        if description:
            doc["description"] = description

        try:
            # Use create_item to fail on duplicate
            created = self.portfolio_container.create_item(doc)
            return _clean(created)
        except Exception as exc:
            # Cosmos raises CosmosHttpResponseError (status 409) on duplicate id
            err_str = str(exc)
            if "409" in err_str or "Conflict" in err_str:
                raise ValueError(f"Account {account_id!r} already exists")
            raise

    def get_account(self, account_id: str) -> Optional[Dict[str, Any]]:
        """Read an account document by account_id."""
        self._require_portfolio()
        try:
            doc = self.portfolio_container.read_item(
                item=account_id, partition_key=account_id
            )
            if doc.get("doc_type") != "account":
                return None
            return _clean(doc)
        except CosmosResourceNotFoundError:
            return None

    def list_accounts(self) -> List[Dict[str, Any]]:
        """Return all account documents."""
        self._require_portfolio()
        query = "SELECT * FROM c WHERE c.doc_type = 'account'"
        try:
            items = list(self.portfolio_container.query_items(
                query=query,
                enable_cross_partition_query=True,
            ))
            return [_clean(d) for d in items]
        except Exception as exc:
            logger.warning("list_accounts failed: %s", exc)
            return []

    def update_account(self, account_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update mutable fields on an account document.

        account_id is immutable — the doc's id/account_id are never changed here.
        Returns the updated doc, or None if not found or soft-deleted.
        """
        self._require_portfolio()
        try:
            doc = self.portfolio_container.read_item(
                item=account_id, partition_key=account_id
            )
        except CosmosResourceNotFoundError:
            return None

        if doc.get("doc_type") != "account":
            return None
        if doc.get("deleted_at"):
            return None

        for key, value in updates.items():
            if value is None:
                doc.pop(key, None)
            else:
                doc[key] = value
        doc["updated_at"] = self._now()
        updated = self.portfolio_container.replace_item(item=doc["id"], body=doc)
        return _clean(updated)

    def delete_account(self, account_id: str) -> Optional[Dict[str, Any]]:
        """Soft-delete an account. Blocks if the account has active movements.

        Returns the soft-deleted doc, or None if not found.
        Raises ValueError with movement_count if movements exist.
        """
        self._require_portfolio()
        try:
            doc = self.portfolio_container.read_item(
                item=account_id, partition_key=account_id
            )
        except CosmosResourceNotFoundError:
            return None

        if doc.get("doc_type") != "account":
            return None

        # Count active (non-deleted, non-superseded) movements in this account
        active_count = self._count_active_movements(account_id)
        if active_count > 0:
            raise ValueError(f"account_has_movements:{active_count}")

        doc["deleted_at"] = self._now()
        updated = self.portfolio_container.replace_item(item=doc["id"], body=doc)
        return _clean(updated)

    def _count_active_movements(self, account_id: str) -> int:
        """Count active (non-deleted, non-superseded) ledger_txn docs for an account."""
        query = (
            "SELECT VALUE COUNT(1) FROM c "
            "WHERE c.doc_type = 'ledger_txn' "
            "AND NOT IS_DEFINED(c.deleted_at) "
            "AND (NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE') "
            "AND c.account_id = @account_id"
        )
        params = [{"name": "@account_id", "value": account_id}]
        try:
            result = list(self.portfolio_container.query_items(
                query=query,
                parameters=params,
                partition_key=account_id,
            ))
            return result[0] if result else 0
        except Exception as exc:
            logger.warning("_count_active_movements failed: %s", exc)
            return 0

    # ── Ledger (portfolio container) ─────────────────────────────────────

    class VoidedMovementError(Exception):
        """Raised when write_ledger_txn would silently restore a voided/superseded movement."""
        def __init__(self, movement_id: str, status: str) -> None:
            self.movement_id = movement_id
            self.status = status
            super().__init__(
                f"Refusing to overwrite {status} movement {movement_id!r}. "
                "Use correct_movement or delete-and-recreate instead."
            )

    def _collect_movement_chain(
        self, movement_id: str, account_id: str, doc: Dict[str, Any]
    ) -> Dict[str, Dict[str, Any]]:
        """Walk the full correction/reassignment chain for a movement.

        Follows both predecessor links (corrects_movement_id /
        reassigned_from.movement_id, possibly in a different account
        partition) and successor links (superseded_by) until the whole
        chain is discovered. Raises ValueError if any chain member
        (including the starting doc) belongs to a corporate-action group —
        those must be purged via delete_corporate_action_group() instead.
        """
        def _check_ca_group(d: Dict[str, Any]) -> None:
            if d.get("ca_group_id"):
                raise ValueError(
                    f"group_leg_hard_delete_required: Movement {d['id']} belongs to "
                    f"CA group {d['ca_group_id']!r}. Delete the whole group via "
                    f"DELETE /api/portfolio/corporate-actions/{d['ca_group_id']}"
                )

        def _fetch(mid: str, acct: str) -> Optional[Dict[str, Any]]:
            try:
                return self.portfolio_container.read_item(item=mid, partition_key=acct)
            except CosmosResourceNotFoundError:
                return None

        _check_ca_group(doc)
        chain: Dict[str, Dict[str, Any]] = {movement_id: doc}
        frontier = [(movement_id, account_id, doc)]
        while frontier:
            cur_id, cur_acct, cur_doc = frontier.pop()
            predecessor_id = cur_doc.get("corrects_movement_id") or (
                cur_doc.get("reassigned_from") or {}
            ).get("movement_id")
            predecessor_acct = (
                (cur_doc.get("reassigned_from") or {}).get("account_id") or cur_acct
            )
            successor_id = cur_doc.get("superseded_by")

            for nxt_id, nxt_acct in (
                (predecessor_id, predecessor_acct),
                (successor_id, cur_acct),
            ):
                if not nxt_id or nxt_id in chain:
                    continue
                nxt_doc = _fetch(nxt_id, nxt_acct)
                if nxt_doc is None:
                    continue
                _check_ca_group(nxt_doc)
                chain[nxt_id] = nxt_doc
                frontier.append((nxt_id, nxt_acct, nxt_doc))

        return chain

    def write_ledger_txn(self, txn_doc: Dict[str, Any]) -> Dict[str, Any]:
        """Upsert a ledger_txn document into the portfolio container.

        Self-healing overwrite: if a document with the same ID already exists
        and has correction_status VOIDED or SUPERSEDED, this is a stale
        tombstone left behind by a correction/reassignment whose active
        successor was later hard-deleted without purging the whole chain
        (e.g. via the pre-purge_chain delete_movement() behavior). Re-import
        of the same CSV row is expected to simply (re)create the movement, so
        the orphaned chain is purged first and the fresh document is written
        in its place. If any chain member belongs to a corporate-action group,
        the overwrite is refused (raises VoidedMovementError) since CA groups
        must be purged explicitly via delete_corporate_action_group().
        """
        self._require_portfolio()
        doc_id = txn_doc.get("id")
        account_id = txn_doc.get("account_id")

        # Guard: self-heal stale voided/superseded documents by purging their
        # whole orphaned chain, rather than silently restoring them to active.
        if doc_id and account_id:
            try:
                existing = self.portfolio_container.read_item(
                    item=doc_id, partition_key=account_id
                )
                cs = existing.get("correction_status")
                if cs in _EXCLUDED_CORRECTION_STATUSES:
                    try:
                        chain = self._collect_movement_chain(doc_id, account_id, existing)
                    except ValueError:
                        raise CosmosPortfolioService.VoidedMovementError(doc_id, cs)
                    for cid, cdoc in chain.items():
                        cacct = cdoc.get("account_id", account_id)
                        self.portfolio_container.delete_item(item=cid, partition_key=cacct)
            except CosmosResourceNotFoundError:
                pass  # New document — proceed with upsert

        created = self.portfolio_container.upsert_item(txn_doc)
        return _clean(created)

    def get_movement(self, movement_id: str, account_id: str) -> Optional[Dict[str, Any]]:
        """Read a single ledger_txn by id + account_id (partition key)."""
        self._require_portfolio()
        try:
            doc = self.portfolio_container.read_item(
                item=movement_id, partition_key=account_id
            )
            if doc.get("doc_type") != "ledger_txn":
                return None
            return _clean(doc)
        except CosmosResourceNotFoundError:
            return None

    def delete_movement(
        self, movement_id: str, account_id: str, purge_chain: bool = False
    ) -> Dict[str, Any]:
        """Permanently delete a single ledger_txn document from Cosmos.

        Allowed on any movement regardless of correction_status.
        Rejected if the movement belongs to a CA group (ca_group_id present);
        use delete_corporate_action_group() to purge the whole group.

        When purge_chain=True, also deletes every document linked to this one
        via the correction (corrects_movement_id/superseded_by) or account
        reassignment (reassigned_from/superseded_by) history — both directions.
        Without this, deleting only the currently ACTIVE leg of a corrected or
        reassigned movement leaves its SUPERSEDED/VOIDED predecessor(s) behind
        under their original deterministic id, which permanently blocks any
        future CSV re-import of that same row (write_ledger_txn refuses to
        overwrite a SUPERSEDED/VOIDED document).

        Returns {"deleted": True, "id": movement_id, "purged_ids": [...]}.
        Raises LookupError if not found.
        Raises ValueError with 'group_leg_hard_delete_required' if any chain
        member (including the requested one) belongs to a CA group.
        """
        self._require_portfolio()
        try:
            doc = self.portfolio_container.read_item(
                item=movement_id, partition_key=account_id
            )
        except CosmosResourceNotFoundError:
            raise LookupError(f"Movement {movement_id} not found in account {account_id}")
        if doc.get("doc_type") != "ledger_txn":
            raise LookupError(f"Movement {movement_id} not found in account {account_id}")

        if not purge_chain:
            if doc.get("ca_group_id"):
                raise ValueError(
                    f"group_leg_hard_delete_required: Movement {doc['id']} belongs to "
                    f"CA group {doc['ca_group_id']!r}. Delete the whole group via "
                    f"DELETE /api/portfolio/corporate-actions/{doc['ca_group_id']}"
                )
            self.portfolio_container.delete_item(item=movement_id, partition_key=account_id)
            return {"deleted": True, "id": movement_id, "purged_ids": [movement_id]}

        # Walk the full chain in both directions (correction and reassignment
        # links use different field names but the same superseded_by pointer).
        chain = self._collect_movement_chain(movement_id, account_id, doc)

        purged_ids: List[str] = []
        for cid, cdoc in chain.items():
            cacct = cdoc.get("account_id", account_id)
            self.portfolio_container.delete_item(item=cid, partition_key=cacct)
            purged_ids.append(cid)

        return {"deleted": True, "id": movement_id, "purged_ids": sorted(purged_ids)}

    def delete_corporate_action_group(
        self, ca_group_id: str, account_id: str
    ) -> Dict[str, Any]:
        """Permanently delete all ledger_txn documents belonging to a CA group.

        Deletes every document (any correction_status) that carries ca_group_id.
        All-or-nothing: on partial failure, already-deleted docs are gone but the
        operation raises so the caller knows it was incomplete.

        Returns {"deleted_count": N, "ids": [...]}.
        Raises ValueError("no_legs_found") if no docs exist for the group.
        """
        self._require_portfolio()
        query = (
            "SELECT * FROM c WHERE c.doc_type = 'ledger_txn' "
            "AND c.ca_group_id = @ca_group_id"
        )
        params = [{"name": "@ca_group_id", "value": ca_group_id}]
        legs = list(self.portfolio_container.query_items(
            query=query, parameters=params, enable_cross_partition_query=True,
        ))
        if not legs:
            raise ValueError(
                f"no_legs_found: No documents found for ca_group_id {ca_group_id!r}"
            )
        deleted_ids: List[str] = []
        for leg in legs:
            leg_account = leg.get("account_id", account_id)
            self.portfolio_container.delete_item(
                item=leg["id"], partition_key=leg_account
            )
            deleted_ids.append(leg["id"])
        return {"deleted_count": len(deleted_ids), "ids": deleted_ids}

    def create_manual_movement(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a manual ledger_txn document.

        Generates a unique ID (mvt_{uuid4}), sets import_source='manual',
        computes net, and stores the doc.
        Raises ValueError for business rule violations.
        """
        self._require_portfolio()
        txn_type = data.get("txn_type", "")
        security_id = data.get("security_id", "")
        trade_date = data.get("trade_date", "")
        account_id = data.get("account_id", "_unassigned")

        if not security_id:
            raise ValueError("security_id is required")
        if not trade_date:
            raise ValueError("trade_date is required")
        if txn_type not in {"BUY", "SELL", "DIVIDEND"} | _OPTION_TXN_TYPES:
            raise ValueError(
                "txn_type must be BUY, SELL, DIVIDEND, CALL_SELL, CALL_BUY, "
                f"PUT_SELL, or PUT_BUY for manual creation; got {txn_type!r}"
            )

        # DERECHOS: validate quantity=0 semantics
        sales_type = data.get("sales_type")
        if txn_type == "SELL" and not sales_type:
            sales_type = "ACCIONES"
        if txn_type == "SELL" and sales_type not in ("ACCIONES", "DERECHOS"):
            raise ValueError("sales_type must be ACCIONES or DERECHOS")
        if txn_type in _OPTION_TXN_TYPES and data.get("sales_type") is not None:
            raise ValueError("sales_type is not applicable to option movements")
        if txn_type in _OPTION_TXN_TYPES and data.get("cost_basis_status") is not None:
            raise ValueError("cost_basis_status is not applicable to option movements")

        gross = data.get("gross") or {}
        fees = data.get("fees") or {}
        wht = data.get("withholding")
        currency = gross.get("currency", "EUR").upper()
        option_metadata = _normalize_option_metadata(txn_type, data)
        quantity = "0" if txn_type in _OPTION_TXN_TYPES else str(data.get("quantity", "0"))
        if txn_type in _OPTION_TXN_TYPES and _d(data.get("quantity", "0")) != Decimal("0"):
            raise ValueError("quantity must be 0 for option movements")
        if txn_type in _OPTION_TXN_TYPES and wht is not None:
            raise ValueError("withholding is not applicable to option movements")
        net = _compute_manual_net(txn_type, gross, fees, wht)
        gross_eur = _d(gross.get("eur_amount", "0"))
        fees_eur = _d(fees.get("total_eur", "0"))

        now = self._now()
        movement_id = f"mvt_{uuid4().hex}"
        ticker = security_id.split(":")[-1] if ":" in security_id else security_id

        doc: Dict[str, Any] = {
            "id": movement_id,
            "account_id": account_id,
            "doc_type": "ledger_txn",
            "txn_type": txn_type,
            "security_id": security_id,
            "ticker": ticker,
            "trade_date": trade_date,
            "quantity": quantity,
            "gross": {
                "amount": str(gross.get("amount", "0")),
                "currency": currency,
                "eur_amount": str(gross_eur),
            },
            "fees": {
                "total": str(fees.get("total", "0")),
                "currency": fees.get("currency", currency),
                "total_eur": str(fees_eur),
            },
            "net": net,
            "import_source": "manual",
            "correction_status": "ACTIVE",
            "created_at": now,
            "updated_at": now,
        }

        # FX info
        fx = data.get("fx")
        if fx:
            doc["fx"] = fx
        else:
            doc["fx"] = {"rate": "1.000000000", "rate_source": "ECB"}

        if wht:
            doc["withholding"] = _apply_wht_rate_derivation(wht, gross_eur)
        _ensure_ledger_detail_fields(doc)

        if txn_type == "SELL":
            doc["sales_type"] = sales_type

        cost_basis_status = data.get("cost_basis_status")
        if txn_type == "BUY" and cost_basis_status:
            doc["cost_basis_status"] = cost_basis_status
        elif txn_type == "BUY":
            doc["cost_basis_status"] = "COMPLETE"

        doc.update(option_metadata)

        if data.get("notes"):
            doc["notes"] = data["notes"]

        # Corporate-action group fields (Amendment H §H.3.2)
        for ca_field in ("ca_group_id", "ca_leg_type", "ca_event_type", "ca_group_seq"):
            if data.get(ca_field) is not None:
                doc[ca_field] = data[ca_field]

        created = self.portfolio_container.upsert_item(doc)

        # Synchronous best-effort symbol_config enrollment.
        # Failure (including None symbols_container) must never roll back the ledger.
        try:
            ensure_symbol_config(
                self.symbols_container, security_id, source="manual_movement"
            )
        except Exception as exc:
            logger.warning(
                "ensure_symbol_config failed for manual_movement %s: %s",
                security_id,
                exc,
            )

        return _clean(created)

    def correct_movement(
        self, movement_id: str, account_id: str, correction_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Replace a movement via the correction workflow.

        Creates a new active doc linked to the original; marks original SUPERSEDED.
        Returns {"original": ..., "replacement": ...}.
        Raises ValueError if already superseded/voided or if correction fields are invalid.
        Raises ValueError with 'transfer_not_correctable' prefix for transfer movements.
        """
        self._require_portfolio()

        original = self.get_movement(movement_id, account_id)
        if original is None:
            raise LookupError(f"Movement {movement_id} not found in account {account_id}")

        if original.get("correction_status") in ("SUPERSEDED", "VOIDED"):
            raise ValueError(f"already_superseded: movement {movement_id} is already {original['correction_status']}")

        # Transfers must be voided+recreated — not patchable
        txn_type = original.get("txn_type", "")
        if txn_type in _TRANSFER_TYPES:
            raise ValueError(
                "transfer_not_correctable: Transfer movements cannot be corrected individually. "
                "Void the transfer pair and create a new one."
            )

        # Group legs: financial field changes must go through group correction endpoint
        _orig_ca_group_id = original.get("ca_group_id")
        if _orig_ca_group_id:
            _financial_keys = _CA_FINANCIAL_FIELDS.intersection(correction_data)
            if _financial_keys:
                raise ValueError(
                    f"group_leg_correction_required: Movement {movement_id} belongs to "
                    f"corporate-action group {_orig_ca_group_id!r}. "
                    f"Financial field(s) {sorted(_financial_keys)} must be corrected via "
                    f"POST /api/portfolio/corporate-actions/{_orig_ca_group_id}/correct"
                )

        correction_note = correction_data.get("correction_note", "").strip()
        if not correction_note:
            raise ValueError("correction_note is required")

        # Validate all supplied correction fields against type-specific rules
        _validate_correction_fields(txn_type, correction_data)

        now = self._now()
        new_id = f"mvt_{uuid4().hex}"

        # Build replacement doc: start from original, apply overrides
        replacement = dict(original)
        replacement["id"] = new_id
        replacement["account_id"] = account_id
        replacement["correction_status"] = "ACTIVE"
        replacement["corrects_movement_id"] = movement_id
        replacement["correction_note"] = correction_note
        replacement["import_source"] = "manual"
        replacement["created_at"] = now
        replacement["updated_at"] = now
        # Remove superseded_by if carried over
        replacement.pop("superseded_by", None)

        # Apply field overrides from correction_data.
        # Non-nullable fields: only applied when value is not None (absent means "no change").
        # Nullable fields (withholding, quantity for DIVIDEND): applied when key is present in
        # the body. An explicit JSON null clears the value, then the write path normalizes
        # the persisted movement back to the required detail-contract shape.
        _NONNULL_OVERRIDABLE = (
            "trade_date",
            "gross",
            "fees",
            "fx",
            "sales_type",
            "cost_basis_status",
            "notes",
            "option_position_id",
            "option_link_kind",
            "option_type",
            "option_strike",
            "option_expiration",
            "option_symbol",
            "option_close_date",
        )
        _NULLABLE_OVERRIDABLE = ("withholding", "quantity")

        for field in _NONNULL_OVERRIDABLE:
            if correction_data.get(field) is not None:
                replacement[field] = correction_data[field]

        for field in _NULLABLE_OVERRIDABLE:
            if field in correction_data:
                val = correction_data[field]
                if val is None:
                    # Explicit null: remove the field (clear it)
                    replacement.pop(field, None)
                else:
                    replacement[field] = val

        replacement.pop("is_paper", None)

        replacement.update(_normalize_option_metadata(txn_type, replacement))

        # Recompute net whenever gross, fees, or withholding are touched.
        # Key-presence check so explicit null (cleared withholding) also triggers recompute.
        _net_trigger_fields = {"gross", "fees", "withholding"}
        if _net_trigger_fields.intersection(correction_data):
            gross = replacement.get("gross") or {}
            fees = replacement.get("fees") or {}
            wht = replacement.get("withholding")
            gross_eur = _d(gross.get("eur_amount", "0"))
            replacement["net"] = _compute_manual_net(txn_type, gross, fees, wht)
            # Derive WHT rate_pct server-side after net recompute (Amendment H §H.1.2).
            if isinstance(replacement.get("withholding"), dict):
                replacement["withholding"] = _apply_wht_rate_derivation(
                    replacement["withholding"], gross_eur
                )
        if txn_type in _OPTION_TXN_TYPES:
            replacement["quantity"] = "0"
        _ensure_ledger_detail_fields(replacement)

        # Write replacement
        self.portfolio_container.upsert_item(replacement)

        # Mark original SUPERSEDED
        orig_doc = self.portfolio_container.read_item(item=movement_id, partition_key=account_id)
        orig_doc["correction_status"] = "SUPERSEDED"
        orig_doc["superseded_by"] = new_id
        orig_doc["updated_at"] = now
        self.portfolio_container.replace_item(item=movement_id, body=orig_doc)

        return {
            "original": _clean(orig_doc),
            "replacement": _clean(replacement),
        }

    def create_corporate_action(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Create a corporate-action group as linked ledger_txn documents.

        Amendment H §H.3.6: All legs share a ca_group_id.
        All-or-nothing: if any leg fails validation, the entire request is rejected
        before any document is written.

        Returns {"ca_group_id": ..., "event_type": ..., "movements": [...]}.
        """
        self._require_portfolio()

        event_type = request.get("event_type", "")
        if event_type not in _CA_REQUIRED_LEGS:
            raise ValueError(
                f"event_type must be one of {sorted(_CA_REQUIRED_LEGS)}; got {event_type!r}"
            )

        security_id = request.get("security_id", "")
        if not security_id:
            raise ValueError("security_id is required")

        account_id = request.get("account_id", "_unassigned")
        payment_date = request.get("payment_date", "")
        if not payment_date:
            raise ValueError("payment_date is required")
        notes = request.get("notes")
        legs = request.get("legs", [])
        if not legs:
            raise ValueError("legs must not be empty")

        # Validate required legs for this event type.
        provided_leg_types = {leg.get("leg_type") for leg in legs}
        required = _CA_REQUIRED_LEGS[event_type]
        missing = required - provided_leg_types
        if missing:
            raise ValueError(
                f"event_type {event_type!r} requires leg types: {sorted(missing)}"
            )

        # Validate unknown leg types.
        valid_leg_types = set(_CA_LEG_TXN_TYPE)
        invalid_legs = provided_leg_types - valid_leg_types
        if invalid_legs:
            raise ValueError(f"Unknown leg_type(s): {sorted(invalid_legs)}")

        # Build and validate all leg docs before writing any (all-or-nothing).
        # CASH_DIVIDEND is the only event type that is *always* exactly one leg
        # (_CA_REQUIRED_LEGS["CASH_DIVIDEND"] == {"CASH_DIVIDEND"}, no optional
        # legs apply). It is not a "group" in any meaningful sense — skip
        # ca_group_id/ca_leg_type/ca_event_type/ca_group_seq entirely so it
        # behaves like a normal standalone movement: individually deletable via
        # delete_movement and correctable via correct_movement, instead of
        # being force-routed through the group correct/void/delete endpoints.
        # Other event types (SCRIP_DIVIDEND, RIGHTS_ISSUE, ...) can also end up
        # with a single provided leg, but they remain grouped: their leg set is
        # extensible (e.g. DIVIDEND_WITH_SCRIP's optional CASH_TOP_UP/
        # RIGHTS_SOLD legs), so treating them as "not a group" would be
        # incorrect in general.
        is_single_leg = event_type == "CASH_DIVIDEND"
        ca_group_id = None if is_single_leg else f"cag_{uuid4().hex}"
        now = self._now()
        ticker = security_id.split(":")[-1] if ":" in security_id else security_id
        docs_to_write: List[Dict[str, Any]] = []

        for seq, leg in enumerate(legs, start=1):
            leg_type = leg.get("leg_type", "")
            txn_type = _CA_LEG_TXN_TYPE[leg_type]
            trade_date = leg.get("trade_date") or payment_date

            # Derive quantity and overrides per leg type.
            if leg_type == "CASH_TOP_UP":
                quantity = "0"
                cost_basis_status = "INCOMPLETE"
            elif leg_type == "SHARE_ACQUISITION":
                quantity = str(leg.get("quantity") or "0")
                cost_basis_status = leg.get("cost_basis_status") or "INCOMPLETE"
            else:
                quantity = str(leg.get("quantity") or "0")
                cost_basis_status = leg.get("cost_basis_status")

            if leg_type == "RIGHTS_SOLD":
                sales_type = "DERECHOS"
            elif leg_type == "FRACTIONAL_CASH_OUT":
                sales_type = "ACCIONES"
            else:
                sales_type = None

            transfer_cost_basis_eur = leg.get("transfer_cost_basis_eur")
            if leg_type == "CONSOLIDATION_IN" and not transfer_cost_basis_eur:
                raise ValueError(
                    "CONSOLIDATION_IN leg requires transfer_cost_basis_eur"
                )

            gross = leg.get("gross") or {}
            fees_data = leg.get("fees") or {}
            wht = leg.get("withholding")
            fx = leg.get("fx")

            gross_eur = _d(gross.get("eur_amount", "0"))
            fees_eur = _d(fees_data.get("total_eur", "0"))
            wht_s = Decimal("0")
            wht_d = Decimal("0")
            if isinstance(wht, dict):
                wht_s = _d((wht.get("source") or {}).get("amount_eur", "0"))
                wht_d = _d((wht.get("destination") or {}).get("amount_eur", "0"))

            net_eur = gross_eur - fees_eur - wht_s - wht_d
            currency = gross.get("currency", "EUR").upper()

            movement_id = f"mvt_{uuid4().hex}"
            doc: Dict[str, Any] = {
                "id": movement_id,
                "account_id": account_id,
                "doc_type": "ledger_txn",
                "txn_type": txn_type,
                "security_id": security_id,
                "ticker": ticker,
                "trade_date": trade_date,
                "quantity": quantity,
                "gross": {
                    "amount": str(gross.get("amount", "0")),
                    "currency": currency,
                    "eur_amount": str(gross_eur),
                },
                "fees": {
                    "total": str(fees_data.get("total", "0")),
                    "currency": fees_data.get("currency", currency),
                    "total_eur": str(fees_eur),
                },
                "net": {
                    "amount": str(net_eur.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)),
                    "currency": currency,
                    "eur_amount": str(net_eur.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)),
                },
                "import_source": "manual",
                "correction_status": "ACTIVE",
                "created_at": now,
                "updated_at": now,
            }
            if not is_single_leg:
                doc["ca_group_id"] = ca_group_id
                doc["ca_leg_type"] = leg_type
                doc["ca_event_type"] = event_type
                doc["ca_group_seq"] = seq

            doc["fx"] = fx if fx else {"rate": "1.000000000", "rate_source": "ECB"}

            if wht:
                doc["withholding"] = _apply_wht_rate_derivation(wht, gross_eur)
            _ensure_ledger_detail_fields(doc)

            if sales_type:
                doc["sales_type"] = sales_type

            if txn_type == "BUY":
                doc["cost_basis_status"] = cost_basis_status or "COMPLETE"

            if leg_type == "CONSOLIDATION_IN":
                doc["transfer_cost_basis_eur"] = str(transfer_cost_basis_eur)

            leg_notes = leg.get("notes") or notes
            if leg_notes:
                doc["notes"] = leg_notes

            docs_to_write.append(doc)

        # All validation passed — write all legs.
        created_movements = []
        for doc in docs_to_write:
            written = self.portfolio_container.upsert_item(doc)
            created_movements.append(_clean(written))

        # Best-effort symbol_config enrollment.
        try:
            ensure_symbol_config(
                self.symbols_container, security_id, source="manual_movement"
            )
        except Exception as exc:
            logger.warning(
                "ensure_symbol_config failed for corporate_action %s: %s", security_id, exc
            )

        return {
            "ca_group_id": ca_group_id,
            "event_type": event_type,
            "movements": created_movements,
        }

    def void_corporate_action_group(
        self, ca_group_id: str, account_id: str, reason: str = ""
    ) -> Dict[str, Any]:
        """Void all active legs of a corporate-action group.

        Amendment H §H.3.7: equivalent to voiding each leg individually but atomic.
        Returns {"ca_group_id": ..., "voided_count": N, "movements": [...]}.
        Raises ValueError if no active legs found for the given group.
        """
        self._require_portfolio()

        # Query all active legs for this ca_group_id across the account.
        conditions = [
            "c.doc_type = 'ledger_txn'",
            "c.ca_group_id = @ca_group_id",
            "(NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE')",
        ]
        params = [{"name": "@ca_group_id", "value": ca_group_id}]
        if account_id and account_id != "_unassigned":
            conditions.append("c.account_id = @account_id")
            params.append({"name": "@account_id", "value": account_id})

        where = " AND ".join(conditions)
        query = f"SELECT * FROM c WHERE {where}"
        items = list(self.portfolio_container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True,
        ))

        if not items:
            raise ValueError(
                f"no_active_legs: No active legs found for ca_group_id {ca_group_id!r}"
            )

        now = self._now()
        voided_docs = []
        for item in items:
            item_account = item.get("account_id", account_id)
            item["correction_status"] = "VOIDED"
            if reason:
                item["void_reason"] = reason
            item["updated_at"] = now
            self.portfolio_container.replace_item(item=item["id"], body=item)
            voided_docs.append(_clean(item))

        return {
            "ca_group_id": ca_group_id,
            "voided_count": len(voided_docs),
            "movements": voided_docs,
        }

    def correct_corporate_action_group(
        self, ca_group_id: str, request: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Replace a corporate-action group via atomic two-phase correction.

        Phase 1 — Write replacement legs:
          Build all new leg docs in memory (full validation before any write).
          Upsert each; on mid-write failure delete already-created docs and raise.

        Phase 2 — Supersede originals:
          Replace each original doc with correction_status=SUPERSEDED.
          On mid-supersession failure delete ALL new docs and raise integrity_error.

        Since all legs share the same account_id (same Cosmos partition) both phases
        operate within a single partition; cross-partition compensation logic is still
        implemented for future safety.

        Returns:
          {
            "original_ca_group_id": ...,
            "ca_group_id": ...,
            "event_type": ...,
            "correction_note": ...,
            "movements": [...]   # new replacement legs
          }

        Raises:
          ValueError("correction_note is required")
          ValueError("account_id is required")
          ValueError("no_active_legs: ...")
          ValueError("event_type must be ...")
          ValueError("ca_group_correction_failed: ...")  — write failed; no partial state
          ValueError("integrity_error: ...")             — phase 2 failed; new docs deleted
        """
        self._require_portfolio()

        correction_note = str(request.get("correction_note", "") or "").strip()
        if not correction_note:
            raise ValueError("correction_note is required")

        account_id = str(request.get("account_id", "") or "").strip()
        if not account_id:
            raise ValueError("account_id is required")

        # ── 1. Load all active original legs ────────────────────────────────
        orig_conditions = [
            "c.doc_type = 'ledger_txn'",
            "c.ca_group_id = @ca_group_id",
            "(NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE')",
        ]
        orig_params = [{"name": "@ca_group_id", "value": ca_group_id}]
        orig_query = f"SELECT * FROM c WHERE {' AND '.join(orig_conditions)}"
        original_legs = list(self.portfolio_container.query_items(
            query=orig_query,
            parameters=orig_params,
            enable_cross_partition_query=True,
        ))

        if not original_legs:
            raise ValueError(
                f"no_active_legs: No active legs found for ca_group_id {ca_group_id!r}"
            )

        # ── 2. Validate replacement request ─────────────────────────────────
        event_type = str(request.get("event_type") or "").strip()
        if event_type not in _CA_REQUIRED_LEGS:
            raise ValueError(
                f"event_type must be one of {sorted(_CA_REQUIRED_LEGS)}; got {event_type!r}"
            )

        security_id = (
            str(request.get("security_id") or "").strip()
            or original_legs[0].get("security_id", "")
        )
        if not security_id:
            raise ValueError("security_id is required")

        payment_date = (
            str(request.get("payment_date") or "").strip()
            or original_legs[0].get("trade_date", "")
        )

        legs = request.get("legs") or []
        if not legs:
            raise ValueError("legs must not be empty")

        provided_leg_types = {leg.get("leg_type") for leg in legs}
        required_types = _CA_REQUIRED_LEGS[event_type]
        missing = required_types - provided_leg_types
        if missing:
            raise ValueError(
                f"event_type {event_type!r} requires leg types: {sorted(missing)}"
            )
        invalid_types = provided_leg_types - set(_CA_LEG_TXN_TYPE)
        if invalid_types:
            raise ValueError(f"Unknown leg_type(s): {sorted(invalid_types)}")

        # ── 3. Build all replacement leg docs in memory ──────────────────────
        new_ca_group_id = f"cag_{uuid4().hex}"
        now = self._now()
        ticker = security_id.split(":")[-1] if ":" in security_id else security_id
        group_notes = request.get("notes")
        docs_to_write: List[Dict[str, Any]] = []

        for seq, leg in enumerate(legs, start=1):
            leg_type = leg.get("leg_type", "")
            txn_type = _CA_LEG_TXN_TYPE[leg_type]
            trade_date = leg.get("trade_date") or payment_date

            if leg_type == "CASH_TOP_UP":
                quantity = "0"
                cost_basis_status = "INCOMPLETE"
            elif leg_type == "SHARE_ACQUISITION":
                quantity = str(leg.get("quantity") or "0")
                cost_basis_status = leg.get("cost_basis_status") or "INCOMPLETE"
            else:
                quantity = str(leg.get("quantity") or "0")
                cost_basis_status = leg.get("cost_basis_status")

            if leg_type == "RIGHTS_SOLD":
                sales_type = "DERECHOS"
            elif leg_type == "FRACTIONAL_CASH_OUT":
                sales_type = "ACCIONES"
            else:
                sales_type = None

            transfer_cost_basis_eur = leg.get("transfer_cost_basis_eur")
            if leg_type == "CONSOLIDATION_IN" and not transfer_cost_basis_eur:
                raise ValueError(
                    "CONSOLIDATION_IN leg requires transfer_cost_basis_eur"
                )

            gross = leg.get("gross") or {}
            fees_data = leg.get("fees") or {}
            wht = leg.get("withholding")
            fx = leg.get("fx")

            gross_eur = _d(gross.get("eur_amount", "0"))
            fees_eur = _d(fees_data.get("total_eur", "0"))
            wht_s = Decimal("0")
            wht_d = Decimal("0")
            if isinstance(wht, dict):
                wht_s = _d((wht.get("source") or {}).get("amount_eur", "0"))
                wht_d = _d((wht.get("destination") or {}).get("amount_eur", "0"))

            net_eur = gross_eur - fees_eur - wht_s - wht_d
            currency = gross.get("currency", "EUR").upper()
            new_mvt_id = f"mvt_{uuid4().hex}"

            doc: Dict[str, Any] = {
                "id": new_mvt_id,
                "account_id": account_id,
                "doc_type": "ledger_txn",
                "txn_type": txn_type,
                "security_id": security_id,
                "ticker": ticker,
                "trade_date": trade_date,
                "quantity": quantity,
                "gross": {
                    "amount": str(gross.get("amount", "0")),
                    "currency": currency,
                    "eur_amount": str(gross_eur),
                },
                "fees": {
                    "total": str(fees_data.get("total", "0")),
                    "currency": fees_data.get("currency", currency),
                    "total_eur": str(fees_eur),
                },
                "net": {
                    "amount": str(net_eur.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)),
                    "currency": currency,
                    "eur_amount": str(net_eur.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)),
                },
                "import_source": "manual",
                "correction_status": "ACTIVE",
                "ca_group_id": new_ca_group_id,
                "ca_leg_type": leg_type,
                "ca_event_type": event_type,
                "ca_group_seq": seq,
                "replaces_ca_group_id": ca_group_id,
                "correction_note": correction_note,
                "created_at": now,
                "updated_at": now,
            }

            doc["fx"] = fx if fx else {"rate": "1.000000000", "rate_source": "ECB"}

            if wht:
                doc["withholding"] = _apply_wht_rate_derivation(wht, gross_eur)
            _ensure_ledger_detail_fields(doc)

            if sales_type:
                doc["sales_type"] = sales_type

            if txn_type == "BUY":
                doc["cost_basis_status"] = cost_basis_status or "COMPLETE"

            if leg_type == "CONSOLIDATION_IN":
                doc["transfer_cost_basis_eur"] = str(transfer_cost_basis_eur)

            leg_notes = leg.get("notes") or group_notes
            if leg_notes:
                doc["notes"] = leg_notes

            docs_to_write.append(doc)

        # ── Phase 1: Write replacement legs ─────────────────────────────────
        created_new: List[Dict[str, Any]] = []
        try:
            for doc in docs_to_write:
                written = self.portfolio_container.upsert_item(doc)
                created_new.append(_clean(written))
        except Exception as phase1_exc:
            for written in created_new:
                try:
                    self.portfolio_container.delete_item(
                        item=written["id"], partition_key=account_id
                    )
                except Exception:
                    logger.warning(
                        "ca_group_correction: compensation delete failed for %s", written["id"]
                    )
            raise ValueError(
                f"ca_group_correction_failed: Failed writing replacement leg: {phase1_exc}"
            ) from phase1_exc

        # ── Phase 2: Supersede original legs ─────────────────────────────────
        phase2_exc: Optional[Exception] = None
        for orig in original_legs:
            try:
                orig_account = orig.get("account_id", account_id)
                orig_doc = self.portfolio_container.read_item(
                    item=orig["id"], partition_key=orig_account
                )
                orig_doc["correction_status"] = "SUPERSEDED"
                orig_doc["superseded_by_ca_group_id"] = new_ca_group_id
                orig_doc["updated_at"] = now
                self.portfolio_container.replace_item(item=orig["id"], body=orig_doc)
            except Exception as exc:
                phase2_exc = exc
                break

        if phase2_exc is not None:
            # Compensate: delete all replacement legs, preserve original state
            for written in created_new:
                try:
                    self.portfolio_container.delete_item(
                        item=written["id"], partition_key=account_id
                    )
                except Exception:
                    logger.warning(
                        "ca_group_correction: integrity compensation delete failed for %s",
                        written["id"],
                    )
            raise ValueError(
                f"integrity_error: Group correction partially failed superseding originals: "
                f"{phase2_exc}. Replacement legs deleted; original group intact."
            ) from phase2_exc

        return {
            "original_ca_group_id": ca_group_id,
            "ca_group_id": new_ca_group_id,
            "event_type": event_type,
            "correction_note": correction_note,
            "movements": created_new,
        }

    def get_movements(
        self,
        account_id: Optional[str] = None,
        security_id: Optional[str] = None,
        txn_type: Optional[str] = None,
        option_position_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        include_superseded: bool = False,
    ) -> tuple[List[Dict[str, Any]], int]:
        """Return paginated ledger_txn documents.

        Returns (items, total_count). Excludes soft-deleted records.
        By default also excludes SUPERSEDED and VOIDED records.
        """
        self._require_portfolio()

        conditions = [
            "c.doc_type = 'ledger_txn'",
            "NOT IS_DEFINED(c.deleted_at)",
        ]
        if not include_superseded:
            conditions.append(
                "(NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE')"
            )
        params: List[Dict[str, Any]] = []

        if account_id:
            conditions.append("c.account_id = @account_id")
            params.append({"name": "@account_id", "value": account_id})
        if security_id:
            conditions.append("c.security_id = @security_id")
            params.append({"name": "@security_id", "value": security_id})
        if txn_type:
            conditions.append("c.txn_type = @txn_type")
            params.append({"name": "@txn_type", "value": txn_type})
        if option_position_id:
            conditions.append("c.option_position_id = @option_position_id")
            params.append({"name": "@option_position_id", "value": option_position_id})
        if date_from:
            conditions.append("c.trade_date >= @date_from")
            params.append({"name": "@date_from", "value": date_from})
        if date_to:
            conditions.append("c.trade_date <= @date_to")
            params.append({"name": "@date_to", "value": date_to})

        where = " AND ".join(conditions)
        count_query = f"SELECT VALUE COUNT(1) FROM c WHERE {where}"
        data_query = f"SELECT * FROM c WHERE {where} ORDER BY c.trade_date DESC"

        query_kwargs: Dict[str, Any] = {
            "enable_cross_partition_query": True,
        }
        if account_id:
            # Single-partition query when account is known
            query_kwargs = {"partition_key": account_id}

        try:
            count_result = list(self.portfolio_container.query_items(
                query=count_query,
                parameters=params,
                enable_cross_partition_query=True,
            ))
            total = count_result[0] if count_result else 0
        except Exception as exc:
            logger.warning("Count query failed: %s", exc)
            total = 0

        try:
            all_items = list(self.portfolio_container.query_items(
                query=data_query,
                parameters=params,
                enable_cross_partition_query=True,
            ))
            items = all_items[offset: offset + limit]
        except Exception as exc:
            logger.warning("Data query failed: %s", exc)
            items = []
            total = 0

        return [_clean(d) for d in items], total

    def get_all_movements_for_holdings(self) -> List[Dict[str, Any]]:
        """Return all active (non-deleted, non-superseded) ledger_txn docs."""
        self._require_portfolio()
        query = (
            "SELECT * FROM c WHERE c.doc_type = 'ledger_txn' "
            "AND NOT IS_DEFINED(c.deleted_at) "
            "AND (NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE')"
        )
        try:
            items = list(self.portfolio_container.query_items(
                query=query,
                enable_cross_partition_query=True,
            ))
            return [_clean(d) for d in items]
        except Exception as exc:
            logger.warning("get_all_movements_for_holdings failed: %s", exc)
            return []

    def get_ledger_security_ids_all(self) -> set:
        """Return distinct security_ids from ALL ledger_txn docs.

        Unlike get_all_movements_for_holdings this includes soft-deleted and
        SUPERSEDED/VOIDED documents.  Used for portfolio-membership classification
        on the Symbols overview page (§5.4.1): presence in the ledger — regardless
        of correction or deletion status — means a position was owned at some point.
        """
        self._require_portfolio()
        query = "SELECT c.security_id FROM c WHERE c.doc_type = 'ledger_txn'"
        try:
            items = list(self.portfolio_container.query_items(
                query=query,
                enable_cross_partition_query=True,
            ))
            return {i.get("security_id") for i in items if i.get("security_id")}
        except Exception as exc:
            logger.warning("get_ledger_security_ids_all failed: %s", exc)
            return set()

    def soft_delete_movement(self, movement_id: str, account_id: str) -> Optional[Dict[str, Any]]:
        """Soft-delete a movement by setting deleted_at.

        Returns the updated doc, or None if not found.
        """
        self._require_portfolio()
        try:
            doc = self.portfolio_container.read_item(
                item=movement_id, partition_key=account_id
            )
        except CosmosResourceNotFoundError:
            return None

        doc["deleted_at"] = self._now()
        updated = self.portfolio_container.replace_item(item=doc["id"], body=doc)
        return _clean(updated)

    def find_probable_duplicate(
        self,
        security_id: str,
        txn_type: str,
        trade_date: str,
        quantity: str,
        gross_eur: str,
    ) -> Optional[Dict[str, Any]]:
        """Check for an existing committed movement with matching fingerprint."""
        self._require_portfolio()
        query = (
            "SELECT TOP 1 * FROM c WHERE c.doc_type = 'ledger_txn' "
            "AND NOT IS_DEFINED(c.deleted_at) "
            "AND c.security_id = @security_id "
            "AND c.txn_type = @txn_type "
            "AND c.trade_date = @trade_date "
            "AND c.quantity = @quantity"
        )
        params = [
            {"name": "@security_id", "value": security_id},
            {"name": "@txn_type", "value": txn_type},
            {"name": "@trade_date", "value": trade_date},
            {"name": "@quantity", "value": quantity},
        ]
        try:
            results = list(self.portfolio_container.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True,
            ))
            return _clean(results[0]) if results else None
        except Exception as exc:
            logger.warning("find_probable_duplicate failed: %s", exc)
            return None

    # ── Transfers ────────────────────────────────────────────────────────

    def create_transfer_pair(
        self,
        security_id,
        trade_date: str = None,
        quantity: str = None,
        source_account_id: str = None,
        dest_account_id: str = None,
        cost_basis_override_eur: Optional[str] = None,
        transfer_fee: Optional[Dict[str, Any]] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a paired TRANSFER_OUT + TRANSFER_IN.

        Accepts either keyword arguments (original API) or a single dict as the
        first positional argument for convenience:
          svc.create_transfer_pair({"security_id": ..., "trade_date": ..., ...})

        Raises:
            ValueError: same source/dest, missing fields
            InsufficientSharesError: source lacks shares at trade_date
        """
        # Normalize: accept dict as first positional arg
        if isinstance(security_id, dict):
            data = security_id
            security_id = data["security_id"]
            trade_date = data.get("trade_date", trade_date)
            quantity = data.get("quantity", quantity)
            source_account_id = data.get("source_account_id", source_account_id)
            dest_account_id = data.get("dest_account_id", dest_account_id)
            cost_basis_override_eur = data.get("cost_basis_override_eur", cost_basis_override_eur)
            transfer_fee = data.get("transfer_fee", transfer_fee)
            notes = data.get("notes", notes)

        self._require_portfolio()

        if source_account_id == dest_account_id:
            raise ValueError("source_account_id and dest_account_id must differ")

        qty = _d(quantity)
        if qty <= Decimal("0"):
            raise ValueError("quantity must be > 0")

        # Compute source available shares at trade_date
        available = self._compute_shares_at_date(
            account_id=source_account_id,
            security_id=security_id,
            as_of_date=trade_date,
        )
        if available < qty:
            raise InsufficientSharesError(
                available=str(available.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)),
                requested=str(qty.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)),
            )

        # Derive cost basis from source avg cost × quantity
        derived_cost_basis = self._compute_cost_basis_at_date(
            account_id=source_account_id,
            security_id=security_id,
            as_of_date=trade_date,
            quantity=qty,
        )
        derived_str = str(derived_cost_basis.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

        if cost_basis_override_eur is not None:
            effective_cost_str = str(_d(cost_basis_override_eur).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            overridden = True
        else:
            effective_cost_str = derived_str
            overridden = False

        now = self._now()
        group_id = f"trf_{uuid4().hex}"
        out_id = f"mvt_{uuid4().hex}"
        in_id = f"mvt_{uuid4().hex}"
        ticker = security_id.split(":")[-1] if ":" in security_id else security_id

        shared_fields: Dict[str, Any] = {
            "doc_type": "ledger_txn",
            "security_id": security_id,
            "ticker": ticker,
            "trade_date": trade_date,
            "quantity": str(qty.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)),
            "gross": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
            "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
            "net": {"amount": "0", "currency": "EUR", "eur_amount": "0"},
            "withholding": {"source": None, "destination": None},
            "import_source": "manual",
            "correction_status": "ACTIVE",
            "transfer_group_id": group_id,
            "transfer_source_account_id": source_account_id,
            "transfer_dest_account_id": dest_account_id,
            "transfer_cost_basis_derived_eur": derived_str,
            "transfer_cost_basis_eur": effective_cost_str,
            "transfer_cost_basis_overridden": overridden,
            "created_at": now,
            "updated_at": now,
        }
        if transfer_fee:
            shared_fields["transfer_fee"] = transfer_fee
        if notes:
            shared_fields["notes"] = notes

        out_doc = {
            **shared_fields,
            "id": out_id,
            "account_id": source_account_id,
            "txn_type": "TRANSFER_OUT",
            "transfer_peer_id": in_id,
        }
        in_doc = {
            **shared_fields,
            "id": in_id,
            "account_id": dest_account_id,
            "txn_type": "TRANSFER_IN",
            "transfer_peer_id": out_id,
        }

        # Write both legs
        written_out = self.portfolio_container.upsert_item(out_doc)
        written_in = self.portfolio_container.upsert_item(in_doc)

        # Synchronous best-effort symbol_config enrollment for the TRANSFER_IN leg.
        # TRANSFER_OUT is the source account which should already have a config.
        # Failure must never roll back the ledger writes above.
        try:
            ensure_symbol_config(
                self.symbols_container, security_id, source="transfer_in"
            )
        except Exception as exc:
            logger.warning(
                "ensure_symbol_config failed for transfer_in %s: %s",
                security_id,
                exc,
            )

        return {
            "transfer_out": _clean(written_out),
            "transfer_in": _clean(written_in),
            "transfer_group_id": group_id,
        }

    def _compute_shares_at_date(
        self, account_id: str, security_id: str, as_of_date: str
    ) -> Decimal:
        """Compute net shares held in an account for a security up to (and including) as_of_date."""
        movements = self._get_movements_up_to_date(account_id, security_id, as_of_date)
        shares = Decimal("0")
        for m in movements:
            txn_type = m.get("txn_type", "")
            qty = _d(m.get("quantity", "0"))
            if txn_type == "BUY":
                shares += qty
            elif txn_type == "SELL":
                sale_type = m.get("sales_type") or "ACCIONES"
                if sale_type == "ACCIONES":
                    shares -= qty
            elif txn_type == "TRANSFER_IN":
                shares += qty
            elif txn_type == "TRANSFER_OUT":
                shares -= qty
            elif txn_type in _OPTION_TXN_TYPES:
                pass
        return shares

    def _compute_cost_basis_at_date(
        self, account_id: str, security_id: str, as_of_date: str, quantity: Decimal
    ) -> Decimal:
        """Compute total carried cost basis for transferring `quantity` shares.

        Uses average cost × quantity from source account at as_of_date.
        """
        movements = self._get_movements_up_to_date(account_id, security_id, as_of_date)
        total_cost = Decimal("0")
        total_paid_shares = Decimal("0")
        for m in movements:
            txn_type = m.get("txn_type", "")
            if txn_type == "BUY":
                qty = _d(m.get("quantity", "0"))
                cost_status = m.get("cost_basis_status", "COMPLETE")
                if cost_status != "INCOMPLETE":
                    gross_eur = _d((m.get("gross") or {}).get("eur_amount", "0"))
                    fee_eur = _d((m.get("fees") or {}).get("total_eur", "0"))
                    total_cost += gross_eur + fee_eur
                    total_paid_shares += qty
            elif txn_type == "TRANSFER_IN":
                qty = _d(m.get("quantity", "0"))
                cost_basis = _d(m.get("transfer_cost_basis_eur", "0"))
                if qty > Decimal("0"):
                    total_cost += cost_basis
                    total_paid_shares += qty
            elif txn_type in _OPTION_TXN_TYPES:
                pass

        if total_paid_shares <= Decimal("0"):
            return Decimal("0")

        avg_cost = total_cost / total_paid_shares
        return (avg_cost * quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def _get_movements_up_to_date(
        self, account_id: str, security_id: str, as_of_date: str
    ) -> List[Dict[str, Any]]:
        """Fetch active movements for account+security up to as_of_date (inclusive)."""
        query = (
            "SELECT * FROM c "
            "WHERE c.doc_type = 'ledger_txn' "
            "AND NOT IS_DEFINED(c.deleted_at) "
            "AND (NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE') "
            "AND c.security_id = @security_id "
            "AND c.trade_date <= @as_of_date "
            "ORDER BY c.trade_date ASC"
        )
        params = [
            {"name": "@security_id", "value": security_id},
            {"name": "@as_of_date", "value": as_of_date},
        ]
        try:
            items = list(self.portfolio_container.query_items(
                query=query,
                parameters=params,
                partition_key=account_id,
            ))
            return [_clean(d) for d in items if d.get("account_id") == account_id]
        except Exception as exc:
            logger.warning("_get_movements_up_to_date failed: %s", exc)
            return []

    # ── Reassignment ─────────────────────────────────────────────────────

    def reassign_movement(
        self,
        movement_id: str,
        source_account_id: str,
        dest_account_id: str,
        reason: str = "",
    ) -> Dict[str, Any]:
        """Move a single historical movement to a different account.

        Safe protocol:
          1. Read from source partition.
          2. Write new doc in dest partition with reassignment audit fields.
          3. Mark original SUPERSEDED in source partition.

        Returns {"original_id": ..., "new_id": ..., "dest_account_id": ...}.
        """
        self._require_portfolio()
        if source_account_id == dest_account_id:
            raise ValueError("same_account: source and dest must differ")

        doc = self.get_movement(movement_id, source_account_id)
        if doc is None:
            raise LookupError(f"Movement {movement_id!r} not found in account {source_account_id!r}")

        if doc.get("correction_status") in ("SUPERSEDED", "VOIDED"):
            raise ValueError(f"already_reassigned: movement is {doc.get('correction_status')}")

        now = self._now()
        new_id = f"mvt_{uuid4().hex}"

        new_doc = dict(doc)
        new_doc["id"] = new_id
        new_doc["account_id"] = dest_account_id
        new_doc["correction_status"] = "ACTIVE"
        new_doc["reassigned_from"] = {
            "account_id": source_account_id,
            "movement_id": movement_id,
        }
        new_doc["reassignment_reason"] = reason
        new_doc.pop("superseded_by", None)
        new_doc.pop("corrects_movement_id", None)
        new_doc["created_at"] = now
        new_doc["updated_at"] = now
        _ensure_ledger_detail_fields(new_doc)

        # Step 2: write to destination first (safe order)
        self.portfolio_container.upsert_item(new_doc)

        # Step 3: mark original SUPERSEDED
        try:
            orig_doc = self.portfolio_container.read_item(
                item=movement_id, partition_key=source_account_id
            )
            orig_doc["correction_status"] = "SUPERSEDED"
            orig_doc["superseded_by"] = new_id
            orig_doc["updated_at"] = now
            self.portfolio_container.replace_item(item=movement_id, body=orig_doc)
        except Exception as exc:
            logger.error(
                "reassign_movement: failed to mark original SUPERSEDED "
                "(new doc %s written; original %s in %s may need manual cleanup): %s",
                new_id, movement_id, source_account_id, exc
            )
            raise

        return {
            "original_id": movement_id,
            "new_id": new_id,
            "dest_account_id": dest_account_id,
        }

    def _fetch_reassign_candidates(
        self,
        source_account_id: str,
        security_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch active movements eligible for reassignment from source_account_id.

        This is the single canonical predicate shared by preview and execution.
        Read-only; no writes.
        """
        conditions = [
            "c.doc_type = 'ledger_txn'",
            "NOT IS_DEFINED(c.deleted_at)",
            "(NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE')",
        ]
        params: List[Dict[str, Any]] = []

        if security_id:
            conditions.append("c.security_id = @security_id")
            params.append({"name": "@security_id", "value": security_id})
        if date_from:
            conditions.append("c.trade_date >= @date_from")
            params.append({"name": "@date_from", "value": date_from})
        if date_to:
            conditions.append("c.trade_date <= @date_to")
            params.append({"name": "@date_to", "value": date_to})

        where = " AND ".join(conditions)
        query = f"SELECT * FROM c WHERE {where} ORDER BY c.trade_date ASC"

        try:
            docs = list(self.portfolio_container.query_items(
                query=query,
                parameters=params,
                partition_key=source_account_id,
            ))
        except Exception as exc:
            logger.warning("_fetch_reassign_candidates query failed: %s", exc)
            docs = []

        # Partition key scoping: only return docs belonging to source_account_id
        return [_clean(d) for d in docs if d.get("account_id") == source_account_id]

    _PREVIEW_SAMPLE_LIMIT = 10  # bounded sample returned in preview responses

    def preview_batch_reassign(
        self,
        source_account_id: str,
        dest_account_id: str,
        security_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Preview a batch reassignment without performing any writes.

        Uses the exact same selection predicate as batch_reassign_movements.
        Callers must NOT pass a count back to the execution endpoint — the
        server re-derives the set at execution time.

        Returns:
            {
                "affected_count": N,
                "movement_ids": [...all matching IDs...],
                "sample": [...first 10 docs with key fields...],
                "source_account_id": "...",
                "dest_account_id": "...",
            }
        """
        self._require_portfolio()
        if source_account_id == dest_account_id:
            raise ValueError("same_account: source and dest must differ")

        candidates = self._fetch_reassign_candidates(
            source_account_id=source_account_id,
            security_id=security_id,
            date_from=date_from,
            date_to=date_to,
        )

        movement_ids = [d["id"] for d in candidates]
        sample = [
            {
                "id": d["id"],
                "security_id": d.get("security_id"),
                "txn_type": d.get("txn_type"),
                "trade_date": d.get("trade_date"),
                "quantity": d.get("quantity"),
                "account_id": d.get("account_id"),
                "option_position_id": d.get("option_position_id"),
                "option_link_kind": d.get("option_link_kind"),
                "option_type": d.get("option_type"),
            }
            for d in candidates[: self._PREVIEW_SAMPLE_LIMIT]
        ]

        return {
            "affected_count": len(candidates),
            "movement_ids": movement_ids,
            "sample": sample,
            "source_account_id": source_account_id,
            "dest_account_id": dest_account_id,
        }

    def batch_reassign_movements(
        self,
        source_account_id: str,
        dest_account_id: str,
        security_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        reason: str = "",
    ) -> Dict[str, Any]:
        """Reassign all matching active movements from source to dest account.

        All-or-nothing: on first failure all prior reassignments in this batch
        are rolled back via compensating operations. Never trusts a client-supplied
        count or ID list — always re-derives candidates at execution time.

        Returns {"reassigned_count": N, "skipped_count": 0, "ids": [new_id, ...]}.
        Raises ValueError("batch_reassign_failed: ...") on partial failure after rollback.
        """
        self._require_portfolio()
        if source_account_id == dest_account_id:
            raise ValueError("same_account: source and dest must differ")

        candidates = self._fetch_reassign_candidates(
            source_account_id=source_account_id,
            security_id=security_id,
            date_from=date_from,
            date_to=date_to,
        )

        completed: List[Dict[str, Any]] = []  # {"original_id", "new_id", "source_account_id"}

        for doc in candidates:
            try:
                result = self.reassign_movement(
                    movement_id=doc["id"],
                    source_account_id=source_account_id,
                    dest_account_id=dest_account_id,
                    reason=reason,
                )
                completed.append({
                    "original_id": doc["id"],
                    "new_id": result["new_id"],
                    "source_account_id": source_account_id,
                })
            except Exception as exc:
                logger.error(
                    "batch_reassign: failed on %s after %d successes, rolling back: %s",
                    doc.get("id"), len(completed), exc,
                )
                self._rollback_batch_reassign(completed, dest_account_id)
                raise ValueError(
                    f"batch_reassign_failed: operation rolled back after failure on "
                    f"movement {doc.get('id')!r}. {len(completed)} prior reassignment(s) "
                    f"were reversed. Cause: {exc}"
                )

        return {
            "reassigned_count": len(completed),
            "skipped_count": 0,
            "ids": [c["new_id"] for c in completed],
        }

    def _rollback_batch_reassign(
        self, completed: List[Dict[str, Any]], dest_account_id: str
    ) -> None:
        """Best-effort compensating rollback for a failed batch reassignment.

        For each already-reassigned entry:
          1. Delete the new document from the destination partition.
          2. Restore the original document to ACTIVE in the source partition.

        Logs errors with MANUAL CLEANUP REQUIRED for any step that fails.
        """
        for entry in completed:
            try:
                self.portfolio_container.delete_item(
                    item=entry["new_id"],
                    partition_key=dest_account_id,
                )
            except Exception as del_exc:
                logger.error(
                    "batch_rollback: could not delete new doc %s from %s: %s "
                    "(MANUAL CLEANUP REQUIRED)",
                    entry["new_id"], dest_account_id, del_exc,
                )

            try:
                original = self.portfolio_container.read_item(
                    item=entry["original_id"],
                    partition_key=entry["source_account_id"],
                )
                original["correction_status"] = "ACTIVE"
                original.pop("superseded_by", None)
                self.portfolio_container.upsert_item(original)
            except Exception as restore_exc:
                logger.error(
                    "batch_rollback: could not restore original %s in %s: %s "
                    "(MANUAL CLEANUP REQUIRED)",
                    entry["original_id"], entry["source_account_id"], restore_exc,
                )
