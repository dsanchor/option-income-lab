"""Pydantic models for the portfolio domain.

Frozen enums and shapes from contract v1.1.
All financial amounts are represented as Decimal to guarantee arithmetic
precision; JSON serialisation uses string representation.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
from datetime import datetime

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


class CostBasisStatus(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    ZERO_COST = "ZERO_COST"       # Explicit zero acquisition cost (e.g. scrip dividend)


class WarningType(str, Enum):
    NEGATIVE_INVENTORY = "NEGATIVE_INVENTORY"
    ZERO_COST_ACQUISITION = "ZERO_COST_ACQUISITION"
    INCOMPLETE_COST_BASIS = "INCOMPLETE_COST_BASIS"   # Genuinely unknown cost
    RIGHTS_AMOUNT = "RIGHTS_AMOUNT"
    PROBABLE_DUPLICATE = "PROBABLE_DUPLICATE"
    DERECHOS_WITH_QUANTITY = "DERECHOS_WITH_QUANTITY"
    ACCIONES_ZERO_QUANTITY = "ACCIONES_ZERO_QUANTITY"
    INVALID_SALES_TYPE = "INVALID_SALES_TYPE"


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
    cost_basis_sold_eur: str          # Σ CMP cost assigned to SELL ACCIONES
    remaining_cost_basis_eur: str     # pool_cost residual
    total_sale_proceeds_eur: str      # Σ(gross-fee) SELL (all types)
    rights_proceeds_eur: str          # Σ(gross-fee) SELL DERECHOS
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
    cost_basis_sold_eur: str          # Σ CMP cost assigned to SELL ACCIONES
    remaining_cost_basis_eur: str     # pool_cost residual (= "Inversión actual")
    total_sale_proceeds_eur: str      # Σ(gross-fee) SELL (all types)
    rights_proceeds_eur: str          # Σ(gross-fee) SELL DERECHOS
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
    sales_type: Optional[str] = None      # SELL only: ACCIONES | DERECHOS
    cost_basis_status: Optional[str] = None  # BUY only: COMPLETE | INCOMPLETE
    notes: Optional[str] = None
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

    @field_validator("sales_type")
    @classmethod
    def valid_sales_type(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("ACCIONES", "DERECHOS"):
            raise ValueError("sales_type must be ACCIONES or DERECHOS")
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
    sales_type: Optional[str] = None
    cost_basis_status: Optional[str] = None
    notes: Optional[str] = None

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
    RIGHTS_SOLD = "RIGHTS_SOLD"
    SHARE_ACQUISITION = "SHARE_ACQUISITION"
    CASH_TOP_UP = "CASH_TOP_UP"


class CaEventType(str, Enum):
    CASH_DIVIDEND = "CASH_DIVIDEND"
    DIVIDEND_WITH_SCRIP = "DIVIDEND_WITH_SCRIP"
    SCRIP_DIVIDEND = "SCRIP_DIVIDEND"
    RIGHTS_ISSUE = "RIGHTS_ISSUE"


class CorporateActionLegCreate(BaseModel):
    """One leg within a corporate-action group (maps to a ledger_txn document)."""
    leg_type: str                                   # CaLegType value
    trade_date: str
    quantity: Optional[str] = None                  # null for CASH_DIVIDEND
    gross: MoneyAmountInput
    fees: Optional[FeesInput] = None
    withholding: Optional[Any] = None
    fx: Optional[Dict[str, str]] = None
    cost_basis_status: Optional[str] = None
    notes: Optional[str] = None

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
