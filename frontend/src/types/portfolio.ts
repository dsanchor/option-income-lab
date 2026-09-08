/** Frozen TypeScript types matching the portfolio contract v1.1 + Phase 2 (Livingston) */

// ─── Enums ───────────────────────────────────────────────────────────────────

export type TxnType = "BUY" | "SELL" | "DIVIDEND" | "TRANSFER_OUT" | "TRANSFER_IN";
export type CostBasisStatus = "COMPLETE" | "INCOMPLETE" | "ZERO_COST";
export type CorrectionStatus = "ACTIVE" | "SUPERSEDED" | "VOIDED";
export type WarningType =
  | "NEGATIVE_INVENTORY"
  | "RIGHTS_AMOUNT"
  | "PROBABLE_DUPLICATE"
  | "DERECHOS_WITH_QUANTITY"
  | "ACCIONES_ZERO_QUANTITY"
  | "INVALID_SALES_TYPE";
export type AssetClass = "Equity";
export type SecurityStatus = "ACTIVE" | "DELISTED";
export type FxRateSource = "ECB" | "BROKER" | "MANUAL";
export type ImportSource = "csv_import" | "manual";

// Amendment G: Display-only labels for sales_type enum values (internal enum stays ACCIONES/DERECHOS)
export const SALES_TYPE_LABELS: Record<"ACCIONES" | "DERECHOS", string> = {
  ACCIONES: "Stocks",
  DERECHOS: "Rights",
};

// Amendment H: Corporate action group types (Phase H-α — linked ledger legs)
export type CaLegType = "CASH_DIVIDEND" | "RIGHTS_SOLD" | "SHARE_ACQUISITION" | "CASH_TOP_UP" | "CONSOLIDATION_OUT" | "CONSOLIDATION_IN" | "FRACTIONAL_CASH_OUT";
export type CaEventType = "DIVIDEND_WITH_SCRIP" | "SCRIP_DIVIDEND" | "RIGHTS_ISSUE" | "CASH_DIVIDEND" | "SHARE_CONSOLIDATION";

// ─── Security Master ─────────────────────────────────────────────────────────

export interface SecurityAlias {
  source: string;
  value: string;
  normalized?: string;
}

export interface SecurityMaster {
  security_id: string;         // "XNYS:AAPL"
  ticker: string;
  company_name: string;
  exchange_mic: string;
  asset_class: AssetClass;
  listing_currency: string;    // ISO 4217
  isin?: string;
  cusip?: string;
  sedol?: string;
  aliases?: SecurityAlias[];
  broker_ids?: Record<string, string>;
  provider_symbols?: Record<string, string>;   // e.g. { yfinance: "ENG.MC" }
  status: SecurityStatus;
  created_at?: string;
  updated_at?: string;
}

export interface CreateSecurityRequest {
  ticker: string;
  company_name: string;
  exchange_mic: string;
  asset_class?: AssetClass;
  listing_currency: string;
  isin?: string;
  cusip?: string;
  sedol?: string;
  aliases?: SecurityAlias[];
  provider_symbols?: Record<string, string>;   // e.g. { yfinance: "ENG.MC" }
}

export interface SecuritiesResponse {
  securities: SecurityMaster[];
}

// ─── Movement Warning ────────────────────────────────────────────────────────

export interface MovementWarning {
  type: WarningType;
  message: string;
  security?: string;
  security_id?: string;
  shares?: string;
  row_index?: number;
  count?: number;
  amount?: string;
  row_indices?: number[];
  existing_movement_id?: string;
  company?: string;
}

// ─── Ledger Movement ─────────────────────────────────────────────────────────

export interface MoneyAmount {
  amount: string;
  currency: string;
  eur_amount: string;
}

export interface FeeAmount {
  total: string;
  currency: string;
  total_eur: string;
}

export interface WithholdingLeg {
  country: string;
  rate_pct: string;
  amount_eur: string;
}

export interface Withholding {
  source?: WithholdingLeg | null;
  destination?: WithholdingLeg | null;
}

export interface FxInfo {
  rate: string;
  rate_source: FxRateSource;
}

export interface LedgerMovement {
  id: string;
  txn_type: TxnType;
  security_id: string;
  ticker: string;
  company_name: string;
  trade_date: string;           // "YYYY-MM-DD"
  quantity: string | null;      // null for DIVIDEND (no share count in source schema)
  gross: MoneyAmount;
  fees: FeeAmount;
  withholding: Withholding;
  net: MoneyAmount;
  fx: FxInfo;
  account_id: string;
  import_source: ImportSource;
  created_at: string;
  cost_basis_status?: CostBasisStatus;
  source_derechos_amount?: string;
  // Sale classification — present only on SELL movements
  sales_type?: "ACCIONES" | "DERECHOS" | null;
  is_rights_sale?: boolean | null;
  warnings?: MovementWarning[];

  // Phase 2: correction audit chain
  correction_status?: CorrectionStatus;
  corrects_movement_id?: string;
  superseded_by?: string;
  correction_note?: string;

  // Phase 2: transfer fields (TRANSFER_OUT / TRANSFER_IN only)
  transfer_group_id?: string;
  transfer_peer_id?: string;
  transfer_source_account_id?: string;
  transfer_dest_account_id?: string;
  transfer_cost_basis_derived_eur?: string;
  transfer_cost_basis_eur?: string;
  transfer_cost_basis_overridden?: boolean;
  transfer_fee?: FeeAmount;

  // Phase 2: reassignment provenance
  reassigned_from?: { account_id: string; movement_id: string };

  // Amendment H: corporate action group linkage (Phase H-α)
  ca_group_id?: string;
  ca_leg_type?: CaLegType;
  ca_event_type?: CaEventType;
  ca_group_seq?: number;
}

export interface MovementsResponse {
  movements: LedgerMovement[];
  total_count: number;
  limit: number;
  offset: number;
}

// ─── Holdings ────────────────────────────────────────────────────────────────

export interface HoldingEntry {
  security_id: string;
  ticker: string;
  company_name: string;
  total_shares: string;
  avg_cost_basis_eur: string | null;
  cost_basis_status: CostBasisStatus;
  total_invested_eur: string;
  total_purchases_eur: string;      // NEW (= total_invested_eur for this security)
  total_sales_eur: string;          // NEW
  total_dividends_eur: string;
  accounts: string[];
  warnings: MovementWarning[];
  // Cost-basis v2 fields (Danny's contract §3.2; optional until backend ships)
  total_purchase_outflow_eur?: string;
  cost_basis_sold_eur?: string;
  remaining_cost_basis_eur?: string;
  total_sale_proceeds_eur?: string;
  rights_proceeds_eur?: string;
  realized_result_eur?: string;
}

export interface HoldingsSummary {
  total_securities: number;
  total_invested_eur: string;       // kept (= total_purchases_eur, backward compat)
  total_dividends_eur: string;      // kept
  total_purchases_eur: string;      // kept
  total_sales_eur: string;          // kept
  current_invested_eur: string;     // kept (alias for remaining_cost_basis_eur post-migration)
  // Cost-basis v2 fields (Danny's contract §3.1; optional until backend ships)
  total_purchase_outflow_eur?: string;
  cost_basis_sold_eur?: string;
  remaining_cost_basis_eur?: string;
  total_sale_proceeds_eur?: string;
  rights_proceeds_eur?: string;
  realized_result_eur?: string;
  has_incomplete_cost_basis?: boolean;
}

export interface HoldingsResponse {
  holdings: HoldingEntry[];
  summary: HoldingsSummary;
}

// ─── Phase 2: Broker Accounts ────────────────────────────────────────────────

/** Broker slugs exactly as Livingston's backend uses them (lowercase). */
export type BrokerType =
  | "fidelity"
  | "heytrade"
  | "ing"
  | "interactive_brokers"
  | "other";

export const BROKER_LABELS: Record<BrokerType, string> = {
  fidelity: "Fidelity",
  heytrade: "HeyTrade",
  ing: "ING",
  interactive_brokers: "Interactive Brokers",
  other: "Other",
};

export interface BrokerAccount {
  /** Same value as `id`; both returned by backend. */
  account_id: string;
  id?: string;
  broker: BrokerType;
  name: string;
  currency?: string;
  description?: string;
  created_at?: string;
  updated_at?: string;
}

export interface CreateAccountRequest {
  broker: BrokerType;           // lowercase slug
  name: string;
  currency?: string;
  description?: string;
}

export type UpdateAccountRequest = Partial<CreateAccountRequest>;

export interface AccountsResponse {
  accounts: BrokerAccount[];
}

// ─── Phase 2: Manual Movement Creation ───────────────────────────────────────

/** Amount object used in manual movement requests (mirrors backend MoneyAmount). */
export interface AmountInput {
  amount: string;
  currency: string;
  eur_amount: string;
}

/** Fees input object. */
export interface FeesInput {
  total: string;
  currency: string;
  total_eur: string;
}

/** Withholding leg input (optional). */
export interface WithholdingLegInput {
  country?: string;
  rate_pct?: string;
  amount_eur?: string;
}

export interface WithholdingInput {
  source?: WithholdingLegInput | null;
  destination?: WithholdingLegInput | null;
}

/** Payload for POST /api/portfolio/movements (BUY / SELL / DIVIDEND only). */
export interface ManualMovementRequest {
  txn_type: "BUY" | "SELL" | "DIVIDEND";
  security_id: string;
  trade_date: string;
  account_id?: string;           // defaults to "_unassigned"
  quantity?: string;             // required for BUY/SELL; 0 OK for DIVIDEND
  gross: AmountInput;
  fees?: FeesInput;
  withholding?: WithholdingInput | null;
  fx?: { rate: string; rate_source: FxRateSource };
  sales_type?: "ACCIONES" | "DERECHOS"; // SELL only; default ACCIONES
  cost_basis_status?: CostBasisStatus;  // BUY only
  notes?: string;
}

/** Payload for POST /api/portfolio/transfers (creates TRANSFER_OUT + TRANSFER_IN pair). */
export interface TransferRequest {
  security_id: string;
  trade_date: string;
  quantity: string;
  source_account_id: string;
  dest_account_id: string;
  cost_basis_override_eur?: string | null;  // null = auto-derived
  transfer_fee?: FeesInput;
  notes?: string;
}

export interface TransferResponse {
  transfer_out: LedgerMovement;
  transfer_in: LedgerMovement;
  transfer_group_id: string;
}

// ─── Phase 2: Movement Correction ────────────────────────────────────────────

/**
 * POST /api/portfolio/movements/{id}/correct
 * Creates a corrected replacement; original is marked SUPERSEDED.
 * Body: same shape as movement create (minus txn_type and security_id), plus correction_note.
 */
export interface MovementCorrectionRequest {
  account_id: string;           // required: must match original partition key
  correction_note: string;      // required for audit trail
  // Any corrected fields:
  trade_date?: string;
  quantity?: string | null;     // null clears quantity (DIVIDEND only)
  gross?: AmountInput;
  fees?: FeesInput;
  withholding?: WithholdingInput | null;   // null = clear; object = override both legs
  fx?: { rate: string; rate_source: FxRateSource };
  sales_type?: "ACCIONES" | "DERECHOS";
  cost_basis_status?: CostBasisStatus;     // BUY only
  notes?: string;
}

export interface MovementCorrectionResponse {
  original: LedgerMovement;
  replacement: LedgerMovement;
}

// ─── Phase 2: Account Reassignment ───────────────────────────────────────────

/** POST /api/portfolio/movements/{id}/reassign — individual account reassignment. */
export interface IndividualReassignmentRequest {
  source_account_id: string;    // current partition (required)
  dest_account_id: string;
  reason?: string;
}

export interface IndividualReassignmentResponse {
  original_id: string;
  new_id: string;
  dest_account_id: string;
}

/** POST /api/portfolio/movements/batch-reassign — bulk reassignment. */
export interface BatchReassignmentRequest {
  source_account_id: string;    // required: current account
  dest_account_id: string;      // required: target account
  security_id?: string;         // optional filter
  date_from?: string;
  date_to?: string;
  reason?: string;
}

export interface BatchReassignmentResponse {
  reassigned_count: number;
  skipped_count: number;
  ids: string[];
}

/**
 * POST /api/portfolio/movements/batch-reassign/preview
 * Dry-run — same predicate as execution, no writes.
 * Client MUST NOT pass returned count back to execution; server re-derives.
 */
export interface BatchReassignmentPreviewRequest {
  source_account_id: string;
  dest_account_id: string;
  security_id?: string;
  date_from?: string;
  date_to?: string;
}

export interface BatchReassignmentPreviewItem {
  id: string;
  security_id: string;
  txn_type: TxnType;
  trade_date: string;
  quantity: string | null;
  account_id: string;
}

export interface BatchReassignmentPreviewResponse {
  affected_count: number;
  movement_ids: string[];
  sample: BatchReassignmentPreviewItem[];
  source_account_id: string;
  dest_account_id: string;
}

// ─── Phase 2: FX Rate Helper ─────────────────────────────────────────────────

export interface FxRateResponse {
  from_currency: string;
  to_currency: string;
  date: string;
  rate: string;
  rate_source: string;
  note?: string | null;
}

// ─── Amendment H: Corporate Action Groups ────────────────────────────────────

export interface CorporateActionLegRequest {
  leg_type: CaLegType;
  trade_date: string;                        // REQUIRED per leg
  quantity?: string;                         // SHARE_ACQUISITION, RIGHTS_SOLD
  gross?: AmountInput;
  fees?: FeesInput | null;
  withholding?: WithholdingInput | null;
  fx?: { rate: string; rate_source: FxRateSource };
  cost_basis_status?: CostBasisStatus;       // SHARE_ACQUISITION only
  notes?: string;
  transfer_cost_basis_eur?: string;          // CONSOLIDATION_IN only
}

/** POST /api/portfolio/corporate-actions */
export interface CorporateActionCreateRequest {
  event_type: CaEventType;                   // CASH_DIVIDEND | DIVIDEND_WITH_SCRIP | SCRIP_DIVIDEND | RIGHTS_ISSUE
  security_id: string;
  account_id: string;
  payment_date: string;                      // REQUIRED; YYYY-MM-DD
  ex_dividend_date?: string;                 // optional
  notes?: string;                            // applied to all legs unless leg.notes set
  legs: CorporateActionLegRequest[];
}

export interface CorporateActionCreateResponse {
  ca_group_id: string;
  event_type: CaEventType;
  movements: LedgerMovement[];
}

/** POST /api/portfolio/corporate-actions/{ca_group_id}/void */
export interface CorporateActionVoidRequest {
  account_id: string;
  reason?: string;
}

export interface CorporateActionVoidResponse {
  ca_group_id: string;
  voided_count: number;
  movements: LedgerMovement[];
}

/** POST /api/portfolio/corporate-actions/{ca_group_id}/correct — Amendment H group correction */
export interface CorporateActionCorrectRequest {
  account_id: string;
  correction_note: string;              // required, non-empty
  event_type: CaEventType;
  security_id?: string;                 // inferred from original if omitted
  payment_date?: string;                // inferred from original if omitted
  notes?: string;
  legs: CorporateActionLegRequest[];
}

export interface CorporateActionCorrectResponse {
  original_ca_group_id: string;
  ca_group_id: string;
  event_type: CaEventType;
  correction_note: string;
  movements: LedgerMovement[];
}
