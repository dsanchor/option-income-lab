"""Pydantic models for the portfolio domain.

Frozen enums and shapes from contract v1.1.
All financial amounts are represented as Decimal to guarantee arithmetic
precision; JSON serialisation uses string representation.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Frozen enums (contract §Frozen Enums)
# ---------------------------------------------------------------------------

class TxnType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    DIVIDEND = "DIVIDEND"
    TRANSFER_OUT = "TRANSFER_OUT"
    TRANSFER_IN = "TRANSFER_IN"
    CALL_SELL = "CALL_SELL"
    CALL_BUY = "CALL_BUY"
    PUT_SELL = "PUT_SELL"
    PUT_BUY = "PUT_BUY"


OPTION_TXN_TYPES = frozenset({
    TxnType.CALL_SELL.value,
    TxnType.CALL_BUY.value,
    TxnType.PUT_SELL.value,
    TxnType.PUT_BUY.value,
})

OPTION_SELL_TXN_TYPES = frozenset({
    TxnType.CALL_SELL.value,
    TxnType.PUT_SELL.value,
})

OPTION_BUY_TXN_TYPES = frozenset({
    TxnType.CALL_BUY.value,
    TxnType.PUT_BUY.value,
})


class AccountBroker(str, Enum):
    fidelity = "fidelity"
    heytrade = "heytrade"
    ing = "ing"
    interactive_brokers = "interactive_brokers"
    other = "other"


class CorrectionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    VOIDED = "VOIDED"


class ImportFormat(str, Enum):
    dividends = "dividends"
    purchases = "purchases"
    sales = "sales"
    options = "options"


class CostBasisStatus(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    ZERO_COST = "ZERO_COST"       # Explicit zero acquisition cost (e.g. scrip dividend)


class WarningType(str, Enum):
    NEGATIVE_INVENTORY = "NEGATIVE_INVENTORY"
    ZERO_COST_ACQUISITION = "ZERO_COST_ACQUISITION"
    INCOMPLETE_COST_BASIS = "INCOMPLETE_COST_BASIS"   # Genuinely unknown cost
    PROBABLE_DUPLICATE = "PROBABLE_DUPLICATE"


class SessionState(str, Enum):
    CREATED = "CREATED"
    FILE_PARSED = "FILE_PARSED"
    BATCH_QUESTIONS = "BATCH_QUESTIONS"
    ENTITY_QUESTIONS = "ENTITY_QUESTIONS"
    ROW_GROUP_QUESTIONS = "ROW_GROUP_QUESTIONS"
    PREVIEW_READY = "PREVIEW_READY"
    COMMIT_CONFIRMED = "COMMIT_CONFIRMED"
    COMMITTED = "COMMITTED"
    EXPIRED = "EXPIRED"


class QuestionScope(str, Enum):
    BATCH = "BATCH"
    ENTITY = "ENTITY"
    ROW_GROUP = "ROW_GROUP"


class AnswerType(str, Enum):
    SELECTED_CANDIDATE = "SELECTED_CANDIDATE"
    CREATED_NEW_SECURITY = "CREATED_NEW_SECURITY"
    SKIPPED_COMPANY = "SKIPPED_COMPANY"
    EXCLUDED_COMPANY = "EXCLUDED_COMPANY"
    BATCH_VALUE = "BATCH_VALUE"


class AssetClass(str, Enum):
    Equity = "Equity"


class SecurityStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DELISTED = "DELISTED"


class FxRateSource(str, Enum):
    ECB = "ECB"
    BROKER = "BROKER"
    MANUAL = "MANUAL"


class ImportSource(str, Enum):
    csv_import = "csv_import"
    manual = "manual"


# ---------------------------------------------------------------------------
# Security Master
# ---------------------------------------------------------------------------

class SecurityAlias(BaseModel):
    source: str
    value: str
    normalized: str = ""

    def model_post_init(self, __context: Any) -> None:
        if not self.normalized:
            object.__setattr__(self, "normalized", self.value.lower().strip())


class SecurityMasterCreate(BaseModel):
    ticker: str
    company_name: str
    exchange_mic: str
    asset_class: AssetClass = AssetClass.Equity
    listing_currency: str = "USD"
    isin: Optional[str] = None
    cusip: Optional[str] = None
    sedol: Optional[str] = None
    broker_ids: Optional[Dict[str, str]] = None
    aliases: Optional[List[SecurityAlias]] = None
    provider_symbols: Optional[Dict[str, str]] = None

    @field_validator("ticker")
    @classmethod
    def ticker_upper(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("exchange_mic")
    @classmethod
    def mic_upper(cls, v: str) -> str:
        return v.strip().upper()


class SecurityMasterDoc(BaseModel):
    """Returned shape for a security_master document (contract §GET /api/securities)."""
    security_id: str
    ticker: str
    company_name: str
    exchange_mic: str
    asset_class: str = "Equity"
    listing_currency: str
    isin: Optional[str] = None
    status: str = "ACTIVE"
    provider_symbols: Optional[Dict[str, str]] = None


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

class MoneyAmount(BaseModel):
    amount: str
    currency: str
    eur_amount: str


class WithholdingDetail(BaseModel):
    country: Optional[str] = None
    rate_pct: Optional[str] = None
    amount_eur: str


class WithholdingInfo(BaseModel):
    source: Optional[WithholdingDetail] = None
    destination: Optional[WithholdingDetail] = None


class FxInfo(BaseModel):
    rate: str
    rate_source: str = "ECB"


class ImportWarning(BaseModel):
    type: str
    message: str
    security_id: Optional[str] = None
    security: Optional[str] = None
    shares: Optional[str] = None
    row_index: Optional[int] = None
    company: Optional[str] = None
    amount: Optional[str] = None
    count: Optional[int] = None
    row_indices: Optional[List[int]] = None
    existing_movement_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Import session
# ---------------------------------------------------------------------------

class SecurityCandidate(BaseModel):
    security_id: str
    company_name: str
    score: float = 0.0


class ImportQuestion(BaseModel):
    question_id: str
    scope: str
    company_name: Optional[str] = None
    normalized_name: Optional[str] = None
    candidates: Optional[List[SecurityCandidate]] = None
    answer: Optional[str] = None
    answer_type: Optional[str] = None
    selected_security_id: Optional[str] = None
    batch_key: Optional[str] = None
    batch_value: Optional[str] = None


class AnswerRequest(BaseModel):
    question_id: str
    answer_type: AnswerType
    selected_security_id: Optional[str] = None
    batch_value: Optional[str] = None


# ---------------------------------------------------------------------------
# Holdings
# ---------------------------------------------------------------------------

class HoldingItem(BaseModel):
    security_id: str
    ticker: str
    company_name: str
    total_shares: str
    # CMP-adjusted average: pool_cost / pool_shares (null when pool is empty)
    avg_cost_basis_eur: Optional[str]
    cost_basis_status: str
    # CMP cost basis fields (Danny contract §3.2)
    total_purchase_outflow_eur: str   # Σ gross_eur BUY COMPLETE (ZERO_COST excluded — no cash outflow)
    cost_basis_sold_eur: str          # Σ FIFO cost assigned to SELL
    remaining_cost_basis_eur: str     # pool_cost residual
    total_sale_proceeds_eur: str      # Σ(gross-fee) SELL
    realized_result_eur: str          # total_sale_proceeds − cost_basis_sold
    # Backward-compatible aliases
    total_invested_eur: str           # alias: total_purchase_outflow_eur
    total_purchases_eur: str          # alias: total_purchase_outflow_eur
    total_sales_eur: str              # alias: total_sale_proceeds_eur
    current_invested_eur: str         # alias: remaining_cost_basis_eur
    total_dividends_eur: str
    accounts: List[str]
    warnings: List[ImportWarning]


class HoldingsSummary(BaseModel):
    total_securities: int
    # CMP cost basis fields (Danny contract §3.1)
    total_purchase_outflow_eur: str   # Σ gross_eur BUY COMPLETE (ZERO_COST excluded)
    cost_basis_sold_eur: str          # Σ FIFO cost assigned to SELL
    remaining_cost_basis_eur: str     # pool_cost residual (= "Inversión actual")
    total_sale_proceeds_eur: str      # Σ(gross-fee) SELL
    realized_result_eur: str          # total_sale_proceeds − cost_basis_sold
    has_incomplete_cost_basis: bool   # true if any security has genuinely INCOMPLETE buys
    # Backward-compatible aliases
    total_invested_eur: str           # alias: total_purchase_outflow_eur
    total_purchases_eur: str          # alias: total_purchase_outflow_eur
    total_sales_eur: str              # alias: total_sale_proceeds_eur
    current_invested_eur: str         # alias: remaining_cost_basis_eur (BREAKING: was purchases−sale_proceeds)
    total_dividends_eur: str


class HoldingsResponse(BaseModel):
    holdings: List[HoldingItem]
    summary: HoldingsSummary


# ---------------------------------------------------------------------------
# Phase 2: Accounts
# ---------------------------------------------------------------------------

class AccountCreate(BaseModel):
    broker: AccountBroker
    name: str
    currency: str = "EUR"
    description: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v

    @field_validator("currency")
    @classmethod
    def currency_upper(cls, v: str) -> str:
        return v.strip().upper()


class AccountDoc(BaseModel):
    id: str
    account_id: str
    broker: str
    name: str
    currency: str
    description: Optional[str] = None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Phase 2: Manual movement creation
# ---------------------------------------------------------------------------

class MoneyAmountInput(BaseModel):
    amount: str
    currency: str
    eur_amount: str


class FeesInput(BaseModel):
    total: str
    currency: str
    total_eur: str


class TransferFeeInput(BaseModel):
    amount: str
    currency: str
    eur_amount: str


class ManualMovementCreate(BaseModel):
    txn_type: TxnType
    security_id: str
    trade_date: str
    account_id: str = "_unassigned"
    quantity: str = "0"
    gross: MoneyAmountInput
    fees: Optional[FeesInput] = None
    withholding: Optional[Any] = None
    fx: Optional[Dict[str, str]] = None
    cost_basis_status: Optional[str] = None  # BUY only: COMPLETE | INCOMPLETE
    notes: Optional[str] = None
    option_position_id: Optional[str] = None
    option_link_kind: Optional[str] = None   # OPEN_SELL | CLOSE_BUY | ASSIGNMENT_STOCK
    option_type: Optional[str] = None        # call | put
    option_strike: Optional[float] = None
    option_expiration: Optional[str] = None
    option_symbol: Optional[str] = None
    option_close_date: Optional[str] = None
    # Corporate-action group fields (Amendment H — set by server, echoed from request)
    ca_group_id: Optional[str] = None
    ca_leg_type: Optional[str] = None
    ca_event_type: Optional[str] = None
    ca_group_seq: Optional[int] = None

    @field_validator("trade_date")
    @classmethod
    def valid_date(cls, v: str) -> str:
        from datetime import date
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ValueError(f"trade_date must be YYYY-MM-DD, got {v!r}")
        return v

    @field_validator("quantity")
    @classmethod
    def quantity_nonnegative(cls, v: str) -> str:
        from decimal import Decimal as D
        try:
            d = D(str(v))
        except Exception:
            raise ValueError(f"quantity must be a number, got {v!r}")
        if d < D("0"):
            raise ValueError("quantity must be >= 0")
        return v

    @field_validator("option_link_kind")
    @classmethod
    def valid_option_link_kind(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("OPEN_SELL", "CLOSE_BUY", "ASSIGNMENT_STOCK"):
            raise ValueError(
                "option_link_kind must be OPEN_SELL, CLOSE_BUY, or ASSIGNMENT_STOCK"
            )
        return v

    @field_validator("option_type")
    @classmethod
    def valid_option_type(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("call", "put"):
            raise ValueError("option_type must be call or put")
        return v


# ---------------------------------------------------------------------------
# Phase 2: Movement correction
# ---------------------------------------------------------------------------

class MovementCorrectionRequest(BaseModel):
    account_id: str
    correction_note: str
    trade_date: Optional[str] = None
    quantity: Optional[str] = None
    gross: Optional[MoneyAmountInput] = None
    fees: Optional[FeesInput] = None
    withholding: Optional[Any] = None
    fx: Optional[Dict[str, str]] = None
    cost_basis_status: Optional[str] = None
    notes: Optional[str] = None
    option_position_id: Optional[str] = None
    option_link_kind: Optional[str] = None
    option_type: Optional[str] = None
    option_strike: Optional[float] = None
    option_expiration: Optional[str] = None
    option_symbol: Optional[str] = None
    option_close_date: Optional[str] = None

    @field_validator("correction_note")
    @classmethod
    def note_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("correction_note must not be empty")
        return v


# ---------------------------------------------------------------------------
# Phase 2: Transfers
# ---------------------------------------------------------------------------

class TransferCreateRequest(BaseModel):
    security_id: str
    trade_date: str
    quantity: str
    source_account_id: str
    dest_account_id: str
    cost_basis_override_eur: Optional[str] = None
    transfer_fee: Optional[TransferFeeInput] = None
    notes: Optional[str] = None

    @field_validator("trade_date")
    @classmethod
    def valid_date(cls, v: str) -> str:
        from datetime import date
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ValueError(f"trade_date must be YYYY-MM-DD, got {v!r}")
        return v

    @field_validator("quantity")
    @classmethod
    def quantity_positive(cls, v: str) -> str:
        from decimal import Decimal as D
        try:
            d = D(str(v))
        except Exception:
            raise ValueError(f"quantity must be a number")
        if d <= D("0"):
            raise ValueError("quantity must be > 0")
        return v


# ---------------------------------------------------------------------------
# Phase 2: Movement reassignment
# ---------------------------------------------------------------------------

class MovementReassignRequest(BaseModel):
    source_account_id: str
    dest_account_id: str
    reason: str = ""


class BatchReassignRequest(BaseModel):
    source_account_id: str
    dest_account_id: str
    security_id: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    reason: str = ""


# ---------------------------------------------------------------------------
# Phase 2: FX Rate
# ---------------------------------------------------------------------------

class FxRateResponse(BaseModel):
    from_currency: str
    to_currency: str
    date: str
    rate: str
    rate_source: str
    note: Optional[str] = None


# ---------------------------------------------------------------------------
# Amendment H: Corporate-Action Groups (Phase H-α)
# ---------------------------------------------------------------------------

class CaLegType(str, Enum):
    CASH_DIVIDEND = "CASH_DIVIDEND"
    SHARE_ACQUISITION = "SHARE_ACQUISITION"
    CASH_TOP_UP = "CASH_TOP_UP"
    CONSOLIDATION_OUT = "CONSOLIDATION_OUT"
    CONSOLIDATION_IN = "CONSOLIDATION_IN"
    FRACTIONAL_CASH_OUT = "FRACTIONAL_CASH_OUT"


class CaEventType(str, Enum):
    CASH_DIVIDEND = "CASH_DIVIDEND"
    DIVIDEND_WITH_SCRIP = "DIVIDEND_WITH_SCRIP"
    SCRIP_DIVIDEND = "SCRIP_DIVIDEND"
    SHARE_CONSOLIDATION = "SHARE_CONSOLIDATION"


_FMV_SOURCES = {
    "OFFICIAL_NOTICE": "AUTHORITATIVE",
    "BROKER": "AUTHORITATIVE",
    "MANUAL": "USER_ASSERTED",
    "YAHOO_OPEN": "MARKET_ESTIMATE",
}
_FMV_FX_SOURCES = frozenset({"IDENTITY", "ECB", "BROKER", "MANUAL"})
_PORTFOLIO_SUPPORTED_CURRENCIES = frozenset({
    "AUD", "BGN", "BRL", "CAD", "CHF", "CNY", "CZK", "DKK", "EUR", "GBP",
    "HKD", "HUF", "IDR", "ILS", "INR", "ISK", "JPY", "KRW", "MXN", "MYR",
    "NOK", "NZD", "PHP", "PLN", "RON", "SEK", "SGD", "THB", "TRY", "USD",
    "ZAR",
})
_FMV_Q6 = Decimal("0.000001")
_FMV_Q9 = Decimal("0.000000001")


def _fmv_decimal(
    value: Any, field: str, *, require_string: bool = True
) -> Decimal:
    if (
        isinstance(value, bool)
        or value is None
        or (require_string and not isinstance(value, str))
        or not str(value).strip()
    ):
        raise ValueError(f"share_fmv.{field} must be a positive decimal string")
    raw = str(value).strip()
    if not re.fullmatch(r"(?:0|[1-9]\d*)(?:\.\d+)?", raw):
        raise ValueError(f"share_fmv.{field} must be a positive decimal string")
    try:
        parsed = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"share_fmv.{field} must be a positive decimal string") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError(f"share_fmv.{field} must be finite and greater than zero")
    return parsed


def _fmv_date(value: Any, field: str) -> str:
    from datetime import date

    if not isinstance(value, str):
        raise ValueError(f"share_fmv.{field} must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"share_fmv.{field} must be YYYY-MM-DD") from exc
    canonical = parsed.isoformat()
    if canonical != value:
        raise ValueError(f"share_fmv.{field} must be YYYY-MM-DD")
    return canonical


def normalize_share_fmv(
    value: Any,
    *,
    quantity: Any,
    trade_date: str,
    allow_yahoo: bool = False,
    persisted: bool = False,
) -> Dict[str, Any]:
    """Validate and canonicalize independent fair value metadata."""
    if not isinstance(value, dict) or not value:
        raise ValueError("share_fmv must be a non-empty object or null")
    allowed_fields = {
        "valuation_date", "amount", "currency", "eur_amount",
        "price_per_share", "price_per_share_eur", "source", "confidence",
        "fx", "reference", "provenance",
    }
    unknown_fields = set(value) - allowed_fields
    if unknown_fields:
        raise ValueError(f"share_fmv contains unknown fields: {sorted(unknown_fields)}")

    qty = _fmv_decimal(quantity, "quantity", require_string=False)
    valuation_date = _fmv_date(value.get("valuation_date"), "valuation_date")
    if valuation_date != trade_date:
        raise ValueError("share_fmv.valuation_date must equal the share leg trade_date")

    currency_raw = value.get("currency")
    currency = str(currency_raw).strip().upper() if currency_raw is not None else ""
    if currency not in _PORTFOLIO_SUPPORTED_CURRENCIES:
        raise ValueError(
            "share_fmv.currency must be a supported ISO-4217 code"
        )

    source = str(value.get("source") or "").strip().upper()
    if source not in _FMV_SOURCES:
        raise ValueError(f"share_fmv.source must be one of {sorted(_FMV_SOURCES)}")
    if source == "YAHOO_OPEN" and not allow_yahoo:
        raise ValueError("share_fmv.source=YAHOO_OPEN is reserved for internal backfill")
    confidence = _FMV_SOURCES[source]
    supplied_confidence = value.get("confidence")
    if supplied_confidence is not None and supplied_confidence != confidence:
        raise ValueError("share_fmv.confidence is incompatible with share_fmv.source")

    fx = value.get("fx")
    if fx is None and currency == "EUR":
        fx = {
            "rate": "1",
            "date": valuation_date,
            "source": "IDENTITY",
        }
    if not isinstance(fx, dict):
        raise ValueError("share_fmv.fx is required for non-EUR currency")
    fx_source = str(fx.get("source") or "").strip().upper()
    fx_date = _fmv_date(fx.get("date"), "fx.date")
    rate = _fmv_decimal(fx.get("rate"), "fx.rate")
    if fx_source not in _FMV_FX_SOURCES:
        raise ValueError(f"share_fmv.fx.source must be one of {sorted(_FMV_FX_SOURCES)}")
    if currency == "EUR":
        if rate != Decimal("1") or fx_date != valuation_date or fx_source != "IDENTITY":
            raise ValueError(
                "EUR share_fmv requires IDENTITY FX at valuation_date with rate 1"
            )
    else:
        if fx_source == "IDENTITY":
            raise ValueError("non-EUR share_fmv cannot use IDENTITY FX")
        if source == "YAHOO_OPEN" and fx_source != "ECB":
            raise ValueError("YAHOO_OPEN share_fmv requires ECB FX")

    amount_in = value.get("amount")
    unit_in = value.get("price_per_share")
    if amount_in is None and unit_in is None:
        raise ValueError("share_fmv requires amount or price_per_share")
    amount = _fmv_decimal(amount_in, "amount") if amount_in is not None else None
    unit = (
        _fmv_decimal(unit_in, "price_per_share")
        if unit_in is not None else None
    )
    if amount is not None and unit is not None:
        if amount.quantize(_FMV_Q6, rounding=ROUND_HALF_UP) != (
            unit * qty
        ).quantize(_FMV_Q6, rounding=ROUND_HALF_UP):
            raise ValueError(
                "share_fmv.amount must equal price_per_share multiplied by quantity"
            )
    elif unit is None:
        unit = (amount / qty).quantize(_FMV_Q6, rounding=ROUND_HALF_UP)
        amount = (unit * qty).quantize(_FMV_Q6, rounding=ROUND_HALF_UP)
    else:
        amount = (unit * qty).quantize(_FMV_Q6, rounding=ROUND_HALF_UP)

    amount = amount.quantize(_FMV_Q6, rounding=ROUND_HALF_UP)
    unit = unit.quantize(_FMV_Q6, rounding=ROUND_HALF_UP)
    eur_amount = (amount * rate).quantize(_FMV_Q6, rounding=ROUND_HALF_UP)
    unit_eur = (unit * rate).quantize(_FMV_Q6, rounding=ROUND_HALF_UP)

    for field, derived in (
        ("eur_amount", eur_amount),
        ("price_per_share_eur", unit_eur),
    ):
        if value.get(field) is not None:
            supplied = _fmv_decimal(value[field], field).quantize(
                _FMV_Q6, rounding=ROUND_HALF_UP
            )
            if supplied != derived:
                raise ValueError(f"share_fmv.{field} is incompatible with derived value")

    provenance = value.get("provenance")
    reference = value.get("reference")
    if source == "YAHOO_OPEN":
        required = {
            "provider", "provider_symbol", "price_field", "requested_date",
            "market_session_date", "fetched_at", "script_version", "run_id",
        }
        if not isinstance(provenance, dict) or any(
            not isinstance(provenance.get(field), str)
            or not provenance[field].strip()
            for field in required
        ):
            raise ValueError("YAHOO_OPEN share_fmv requires complete provenance")
        if provenance.get("provider") != "yfinance" or provenance.get("price_field") != "OPEN":
            raise ValueError("YAHOO_OPEN provenance must identify yfinance OPEN")
        unknown_provenance = set(provenance) - required
        if unknown_provenance:
            raise ValueError(
                "YAHOO_OPEN provenance contains unsupported fields: "
                f"{sorted(unknown_provenance)}"
            )
        _fmv_date(provenance.get("requested_date"), "provenance.requested_date")
        _fmv_date(
            provenance.get("market_session_date"),
            "provenance.market_session_date",
        )
        if provenance["requested_date"] != valuation_date:
            raise ValueError(
                "share_fmv.provenance.requested_date must equal valuation_date"
            )
        try:
            fetched_at = datetime.fromisoformat(
                provenance["fetched_at"].replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError(
                "share_fmv.provenance.fetched_at must be ISO-8601 UTC"
            ) from exc
        if (
            fetched_at.tzinfo is None
            or fetched_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("share_fmv.provenance.fetched_at must be ISO-8601 UTC")
        from uuid import UUID
        try:
            UUID(provenance["run_id"])
        except (ValueError, AttributeError) as exc:
            raise ValueError("share_fmv.provenance.run_id must be a UUID") from exc
        if provenance["script_version"] != "dividend-buy-fmv-v1":
            raise ValueError(
                "share_fmv.provenance.script_version must be dividend-buy-fmv-v1"
            )
        if reference is not None:
            raise ValueError("share_fmv.reference is not valid for YAHOO_OPEN")
        normalized_provenance = dict(provenance)
    else:
        if provenance is not None:
            if not persisted or not isinstance(provenance, dict):
                raise ValueError("share_fmv.provenance is reserved for YAHOO_OPEN")
            unknown = set(provenance) - {"reference"}
            if unknown:
                raise ValueError("manual share_fmv provenance only supports reference")
            reference = provenance.get("reference")
        if reference is not None and (
            not isinstance(reference, str) or not reference.strip()
        ):
            raise ValueError("share_fmv.reference must be a non-empty string")
        normalized_provenance = (
            {"reference": reference.strip()} if reference is not None else None
        )

    result: Dict[str, Any] = {
        "valuation_date": valuation_date,
        "amount": f"{amount:.6f}",
        "currency": currency,
        "eur_amount": f"{eur_amount:.6f}",
        "price_per_share": f"{unit:.6f}",
        "price_per_share_eur": f"{unit_eur:.6f}",
        "source": source,
        "confidence": confidence,
        "fx": {
            "rate": f"{rate.quantize(_FMV_Q9, rounding=ROUND_HALF_UP):.9f}",
            "date": fx_date,
            "source": fx_source,
        },
    }
    if normalized_provenance is not None:
        result["provenance"] = normalized_provenance
    return result


class CorporateActionLegCreate(BaseModel):
    """One leg within a corporate-action group (maps to a ledger_txn document)."""
    leg_type: str                                   # CaLegType value
    trade_date: str
    quantity: Optional[str] = None                  # null for CASH_DIVIDEND
    gross: MoneyAmountInput
    fees: Optional[FeesInput] = None
    withholding: Optional[Any] = None
    fx: Optional[Dict[str, str]] = None
    share_fmv: Optional[Dict[str, Any]] = None
    share_fmv_instruction: Optional[Dict[str, str]] = None
    cost_basis_status: Optional[str] = None
    notes: Optional[str] = None
    transfer_cost_basis_eur: Optional[str] = None   # required for CONSOLIDATION_IN

    @field_validator("leg_type")
    @classmethod
    def valid_leg_type(cls, v: str) -> str:
        valid = {e.value for e in CaLegType}
        if v not in valid:
            raise ValueError(f"leg_type must be one of {sorted(valid)}; got {v!r}")
        return v

    @field_validator("trade_date")
    @classmethod
    def valid_date(cls, v: str) -> str:
        from datetime import date
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ValueError(f"trade_date must be YYYY-MM-DD, got {v!r}")
        return v


class CorporateActionCorrectRequest(BaseModel):
    """Request body for POST /api/portfolio/corporate-actions/{ca_group_id}/correct."""
    account_id: str
    correction_note: str
    event_type: str                                 # CaEventType value
    security_id: Optional[str] = None              # inferred from original when omitted
    payment_date: Optional[str] = None             # inferred from original when omitted
    notes: Optional[str] = None
    client_request_id: Optional[str] = None
    legs: List[CorporateActionLegCreate]

    @field_validator("correction_note")
    @classmethod
    def note_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("correction_note must not be empty")
        return v

    @field_validator("event_type")
    @classmethod
    def valid_event_type(cls, v: str) -> str:
        valid = {e.value for e in CaEventType}
        if v not in valid:
            raise ValueError(f"event_type must be one of {sorted(valid)}; got {v!r}")
        return v

    @field_validator("legs")
    @classmethod
    def legs_nonempty(cls, v: List[CorporateActionLegCreate]) -> List[CorporateActionLegCreate]:
        if not v:
            raise ValueError("legs must not be empty")
        return v


class CorporateActionCreateRequest(BaseModel):
    """Request body for POST /api/portfolio/corporate-actions."""
    event_type: str                                 # CaEventType value
    security_id: str
    account_id: str = "_unassigned"
    payment_date: str
    ex_dividend_date: Optional[str] = None
    notes: Optional[str] = None
    client_request_id: Optional[str] = None
    legs: List[CorporateActionLegCreate]

    @field_validator("event_type")
    @classmethod
    def valid_event_type(cls, v: str) -> str:
        valid = {e.value for e in CaEventType}
        if v not in valid:
            raise ValueError(f"event_type must be one of {sorted(valid)}; got {v!r}")
        return v

    @field_validator("payment_date")
    @classmethod
    def valid_payment_date(cls, v: str) -> str:
        from datetime import date
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ValueError(f"payment_date must be YYYY-MM-DD, got {v!r}")
        return v

    @field_validator("legs")
    @classmethod
    def legs_nonempty(cls, v: List[CorporateActionLegCreate]) -> List[CorporateActionLegCreate]:
        if not v:
            raise ValueError("legs must not be empty")
        return v
