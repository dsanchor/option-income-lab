/**
 * Typed client for all portfolio / securities / import proxy routes.
 * These are browser-side calls: /api/securities, /api/portfolio/*, /api/import/*, /api/fx/*.
 * All paths go through the Next.js BFF proxy to the internal Python backend.
 */

import type {
  SecurityMaster,
  CreateSecurityRequest,
  SecuritiesResponse,
  HoldingsResponse,
  MovementsResponse,
  LedgerMovement,
  BrokerAccount,
  CreateAccountRequest,
  UpdateAccountRequest,
  AccountsResponse,
  ManualMovementRequest,
  TransferRequest,
  TransferResponse,
  MovementCorrectionRequest,
  MovementCorrectionResponse,
  IndividualReassignmentRequest,
  IndividualReassignmentResponse,
  BatchReassignmentRequest,
  BatchReassignmentResponse,
  BatchReassignmentPreviewRequest,
  BatchReassignmentPreviewResponse,
  FxRateResponse,
  CorporateActionCreateRequest,
  CorporateActionCreateResponse,
  CorporateActionVoidRequest,
  CorporateActionVoidResponse,
  CorporateActionCorrectRequest,
  CorporateActionCorrectResponse,
} from "@/types/portfolio";
import type {
  ImportSession,
  ImportAnswer,
  ImportPreviewResponse,
  CommitResult,
  UploadParams,
} from "@/types/import";

// ─── Helpers ─────────────────────────────────────────────────────────────────

interface ApiError extends Error {
  status: number;
  data: { error?: string; detail?: string; [key: string]: unknown };
}

async function fetchJSON<T>(url: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers as HeadersInit | undefined);
  headers.set("Accept", "application/json");

  const res = await fetch(url, { ...init, headers });

  if (!res.ok) {
    const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
    const err = new Error(data.detail ?? data.error ?? `HTTP ${res.status}`) as ApiError;
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return res.json() as Promise<T>;
}

// ─── Securities Catalog ──────────────────────────────────────────────────────

export async function listSecurities(): Promise<SecuritiesResponse> {
  return fetchJSON<SecuritiesResponse>("/api/securities");
}

export async function getSecurity(securityId: string): Promise<SecurityMaster> {
  return fetchJSON<SecurityMaster>(`/api/securities/${encodeURIComponent(securityId)}`);
}

export async function createSecurity(
  data: CreateSecurityRequest,
): Promise<SecurityMaster> {
  return fetchJSON<SecurityMaster>("/api/securities", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

// ─── Portfolio / Holdings ─────────────────────────────────────────────────────

export async function getHoldings(accountId?: string): Promise<HoldingsResponse> {
  const qs = accountId ? `?account_id=${encodeURIComponent(accountId)}` : "";
  return fetchJSON<HoldingsResponse>(`/api/portfolio/holdings${qs}`);
}

// ─── Portfolio / Movements ────────────────────────────────────────────────────

export interface MovementsFilter {
  account_id?: string;
  security_id?: string;
  txn_type?: string;
  ca_group_id?: string;        // filter to one corporate-action group
  date_from?: string;
  date_to?: string;
  limit?: number;
  offset?: number;
}

export async function getMovements(
  filter: MovementsFilter = {},
): Promise<MovementsResponse> {
  const params = new URLSearchParams();
  if (filter.account_id) params.set("account_id", filter.account_id);
  if (filter.security_id) params.set("security_id", filter.security_id);
  if (filter.txn_type) params.set("txn_type", filter.txn_type);
  if (filter.ca_group_id) params.set("ca_group_id", filter.ca_group_id);
  if (filter.date_from) params.set("date_from", filter.date_from);
  if (filter.date_to) params.set("date_to", filter.date_to);
  if (filter.limit !== undefined) params.set("limit", String(filter.limit));
  if (filter.offset !== undefined) params.set("offset", String(filter.offset));
  const qs = params.toString() ? `?${params.toString()}` : "";
  return fetchJSON<MovementsResponse>(`/api/portfolio/movements${qs}`);
}

export async function deleteMovement(
  movementId: string,
  accountId?: string,
): Promise<Pick<LedgerMovement, "id"> & { deleted_at: string }> {
  const params = new URLSearchParams();
  if (accountId) params.set("account_id", accountId);
  const qs = params.toString() ? `?${params.toString()}` : "";
  return fetchJSON(`/api/portfolio/movements/${encodeURIComponent(movementId)}${qs}`, {
    method: "DELETE",
  });
}

// ─── Import Sessions ──────────────────────────────────────────────────────────

export async function createImportSession(
  params: UploadParams,
): Promise<ImportSession> {
  const form = new FormData();

  if (params.file) {
    form.append("file", params.file);
  } else if (params.content !== undefined) {
    const blob = new Blob([params.content], { type: "text/csv" });
    form.append("file", blob, params.filename ?? "import.csv");
  } else {
    throw new Error("Either file or content must be provided");
  }

  if (params.format_hint) form.append("format_hint", params.format_hint);
  if (params.currency) form.append("currency", params.currency);
  if (params.account_id) form.append("account_id", params.account_id);

  // Do NOT set Content-Type manually — browser sets multipart boundary automatically
  const res = await fetch("/api/import/sessions", {
    method: "POST",
    headers: { Accept: "application/json" },
    body: form,
  });

  if (!res.ok) {
    const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
    const err = new Error(data.detail ?? data.error ?? `HTTP ${res.status}`) as ApiError;
    (err as ApiError).status = res.status;
    (err as ApiError).data = data;
    throw err;
  }
  return res.json() as Promise<ImportSession>;
}

export async function getImportSession(sessionId: string): Promise<ImportSession> {
  return fetchJSON<ImportSession>(`/api/import/sessions/${encodeURIComponent(sessionId)}`);
}

export async function answerQuestion(
  sessionId: string,
  answer: ImportAnswer | { question_id: string; answer_type: string; selected_security_id?: string; batch_value?: string },
): Promise<ImportSession> {
  return fetchJSON<ImportSession>(
    `/api/import/sessions/${encodeURIComponent(sessionId)}/answers`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(answer),
    },
  );
}

export async function generatePreview(
  sessionId: string,
): Promise<ImportPreviewResponse> {
  return fetchJSON<ImportPreviewResponse>(
    `/api/import/sessions/${encodeURIComponent(sessionId)}/preview`,
    { method: "POST" },
  );
}

export async function commitImport(sessionId: string): Promise<CommitResult> {
  return fetchJSON<CommitResult>(
    `/api/import/sessions/${encodeURIComponent(sessionId)}/commit`,
    { method: "POST" },
  );
}

// ─── Phase 2: Broker Accounts ─────────────────────────────────────────────────

export async function listAccounts(): Promise<AccountsResponse> {
  return fetchJSON<AccountsResponse>("/api/portfolio/accounts");
}

export async function getAccount(accountId: string): Promise<BrokerAccount> {
  return fetchJSON<BrokerAccount>(`/api/portfolio/accounts/${encodeURIComponent(accountId)}`);
}

export async function createAccount(data: CreateAccountRequest): Promise<BrokerAccount> {
  return fetchJSON<BrokerAccount>("/api/portfolio/accounts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export async function updateAccount(
  accountId: string,
  data: UpdateAccountRequest,
): Promise<BrokerAccount> {
  return fetchJSON<BrokerAccount>(`/api/portfolio/accounts/${encodeURIComponent(accountId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export async function deleteAccount(accountId: string): Promise<void> {
  await fetchJSON<unknown>(`/api/portfolio/accounts/${encodeURIComponent(accountId)}`, {
    method: "DELETE",
  });
}

// ─── Phase 2: Manual Movement Entry ──────────────────────────────────────────

/** POST /api/portfolio/movements — BUY, SELL, or DIVIDEND only. */
export async function createMovement(data: ManualMovementRequest): Promise<LedgerMovement> {
  return fetchJSON<LedgerMovement>("/api/portfolio/movements", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

/** POST /api/portfolio/transfers — creates TRANSFER_OUT + TRANSFER_IN pair. */
export async function createTransfer(data: TransferRequest): Promise<TransferResponse> {
  return fetchJSON<TransferResponse>("/api/portfolio/transfers", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

// ─── Phase 2: Movement Correction ────────────────────────────────────────────

export async function correctMovement(
  movementId: string,
  data: MovementCorrectionRequest,
): Promise<MovementCorrectionResponse> {
  return fetchJSON<MovementCorrectionResponse>(
    `/api/portfolio/movements/${encodeURIComponent(movementId)}/correct`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    },
  );
}

// ─── Phase 2: Account Reassignment ───────────────────────────────────────────

/** POST /api/portfolio/movements/{id}/reassign — individual movement reassignment. */
export async function reassignMovement(
  movementId: string,
  data: IndividualReassignmentRequest,
): Promise<IndividualReassignmentResponse> {
  return fetchJSON<IndividualReassignmentResponse>(
    `/api/portfolio/movements/${encodeURIComponent(movementId)}/reassign`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    },
  );
}

/**
 * POST /api/portfolio/movements/batch-reassign — bulk reassignment.
 * NOTE: No preview endpoint exists in the backend. The UI provides a confirmation
 * checkbox before calling this. Flag: accepted UX requires preview; backend gap.
 */
export async function batchReassignMovements(
  data: BatchReassignmentRequest,
): Promise<BatchReassignmentResponse> {
  return fetchJSON<BatchReassignmentResponse>("/api/portfolio/movements/batch-reassign", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

/**
 * POST /api/portfolio/movements/batch-reassign/preview
 * Dry-run with same selection predicate. Read-only; no writes.
 * Client MUST NOT forward the returned count to execution — server re-derives.
 */
export async function getBatchReassignmentPreview(
  data: BatchReassignmentPreviewRequest,
): Promise<BatchReassignmentPreviewResponse> {
  return fetchJSON<BatchReassignmentPreviewResponse>(
    "/api/portfolio/movements/batch-reassign/preview",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    },
  );
}

// ─── Symbol Unification rev 3 ─────────────────────────────────────────────────

export interface SecuritySearchCandidate {
  security_id: string;
  ticker: string;
  company_name: string;
  exchange_mic: string;
  has_config: boolean;
}

export interface SecuritySearchResponse {
  candidates: SecuritySearchCandidate[];
}

export interface AddSymbolSelectBody {
  security_id: string;
}

export interface AddSymbolCreateBody {
  create: {
    ticker: string;
    exchange_mic: string;
    company_name: string;
    listing_currency: string;
    isin?: string;
    cusip?: string;
    sedol?: string;
    provider_symbols?: Record<string, string>;
  };
}

export type AddSymbolBody = AddSymbolSelectBody | AddSymbolCreateBody;

export interface AddSymbolResponse {
  security: SecurityMaster;
  config_created: boolean;
  config_existed: boolean;
  config_warning: string | null;
  navigate_to: string;
  warmup_started?: boolean;
}

/** GET /api/securities/search?q=...&limit=... */
export async function searchSecurities(
  q: string,
  limit = 10,
): Promise<SecuritySearchResponse> {
  const params = new URLSearchParams({ q, limit: String(limit) });
  return fetchJSON<SecuritySearchResponse>(`/api/securities/search?${params.toString()}`);
}

/** POST /api/symbols/add — unified add symbol (select or create + ensure config). */
export async function addSymbol(body: AddSymbolBody): Promise<AddSymbolResponse> {
  return fetchJSON<AddSymbolResponse>("/api/symbols/add", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/**
 * GET /api/fx/rates — look up FX rate from ECB.
 * Query: from_currency, to_currency, date.
 */
export async function getFxRate(
  fromCurrency: string,
  toCurrency: string,
  date: string,
): Promise<FxRateResponse> {
  const qs = new URLSearchParams({ from_currency: fromCurrency, to_currency: toCurrency, date });
  return fetchJSON<FxRateResponse>(`/api/fx/rates?${qs.toString()}`);
}

// ─── Amendment H: Corporate Action Groups ────────────────────────────────────

/** POST /api/portfolio/corporate-actions — creates N linked legs sharing a ca_group_id. */
export async function createCorporateAction(
  data: CorporateActionCreateRequest,
): Promise<CorporateActionCreateResponse> {
  return fetchJSON<CorporateActionCreateResponse>("/api/portfolio/corporate-actions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

/** POST /api/portfolio/corporate-actions/{ca_group_id}/void — voids all active legs in a group. */
export async function voidCorporateActionGroup(
  caGroupId: string,
  data: CorporateActionVoidRequest,
): Promise<CorporateActionVoidResponse> {
  return fetchJSON<CorporateActionVoidResponse>(
    `/api/portfolio/corporate-actions/${encodeURIComponent(caGroupId)}/void`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    },
  );
}

/** POST /api/portfolio/corporate-actions/{ca_group_id}/correct — atomically replaces all legs. */
export async function correctCorporateActionGroup(
  caGroupId: string,
  data: CorporateActionCorrectRequest,
): Promise<CorporateActionCorrectResponse> {
  return fetchJSON<CorporateActionCorrectResponse>(
    `/api/portfolio/corporate-actions/${encodeURIComponent(caGroupId)}/correct`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    },
  );
}
