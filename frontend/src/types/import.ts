/** Frozen TypeScript types for the import session state machine (contract v1.1) */

import type { MovementWarning, SecurityMaster } from "./portfolio";

// ─── Enums ───────────────────────────────────────────────────────────────────

export type ImportFormat = "dividends" | "purchases" | "sales" | "options";

export type SessionState =
  | "CREATED"
  | "FILE_PARSED"
  | "BATCH_QUESTIONS"
  | "ENTITY_QUESTIONS"
  | "ROW_GROUP_QUESTIONS"
  | "PREVIEW_READY"
  | "COMMIT_CONFIRMED"
  | "COMMITTED"
  | "EXPIRED";

export type QuestionScope = "BATCH" | "ENTITY" | "ROW_GROUP";

export type AnswerType =
  | "SELECTED_CANDIDATE"
  | "CREATED_NEW_SECURITY"
  | "SKIPPED_COMPANY"
  | "EXCLUDED_COMPANY"
  | "BATCH_VALUE";

// ─── Questions ───────────────────────────────────────────────────────────────

export interface SecurityCandidate {
  security_id: string;
  company_name: string;
  ticker?: string;
  score: number;
}

export interface ImportAnswer {
  question_id: string;
  answer_type: AnswerType;
  selected_security_id?: string;
  batch_value?: string;
}

interface BaseQuestion {
  question_id: string;
  scope: QuestionScope;
  answer: ImportAnswer | null;
}

export interface BatchQuestion extends BaseQuestion {
  scope: "BATCH";
  batch_key: string;
  current_value: string;
  candidates?: Array<{ value: string; label?: string }>;
}

export interface EntityQuestion extends BaseQuestion {
  scope: "ENTITY";
  company_name: string;
  normalized_name: string;
  candidates: SecurityCandidate[];
  row_count?: number;
}

export interface RowGroupQuestion extends BaseQuestion {
  scope: "ROW_GROUP";
  [key: string]: unknown;
}

export type ImportQuestion = BatchQuestion | EntityQuestion | RowGroupQuestion;

// ─── Session ─────────────────────────────────────────────────────────────────

export interface StagedSummary {
  total_rows: number;
  resolved_rows?: number;
  unresolved_rows?: number;
  currencies?: string[];
  date_range?: [string, string];
}

export interface ImportSession {
  session_id: string;
  state: SessionState;
  row_count?: number;
  detected_format?: ImportFormat;
  currency?: string;
  account_id?: string;
  warnings?: MovementWarning[];
  questions?: ImportQuestion[];
  staged_summary?: StagedSummary;
}

// ─── Preview ─────────────────────────────────────────────────────────────────

export interface PreviewMovement {
  row_index: number;
  txn_type: string;
  security_id: string;
  ticker: string;
  company_name: string;
  trade_date: string;
  quantity: string | null;
  gross_eur: string;
  fees_eur: string;
  wht_source_eur?: string;
  net_eur: string;
  // Sale classification — present on SELL rows from 7-column CSVs
  sales_type?: "ACCIONES" | "DERECHOS";
  warnings?: MovementWarning[];
}

export interface SkipReason {
  company: string;
  reason: string;
  row_count: number;
}

export interface ImportPreviewData {
  movements: PreviewMovement[];
  warnings: MovementWarning[];
  total_movements: number;
  skipped_rows: number;
  skip_reasons: SkipReason[];
}

export interface ImportPreviewResponse {
  session_id: string;
  state: SessionState;
  preview: ImportPreviewData;
}

// ─── Commit ───────────────────────────────────────────────────────────────────

export interface CommitResult {
  session_id: string;
  state: SessionState;
  committed_count: number;
  skipped_count: number;
}

// ─── Upload params ────────────────────────────────────────────────────────────

export interface UploadParams {
  file?: File;
  content?: string;
  filename?: string;
  format_hint?: ImportFormat;
  currency?: string;
  account_id?: string;
}

// ─── Inline create answer ─────────────────────────────────────────────────────

export interface CreateSecurityAnswer {
  question_id: string;
  answer_type: "CREATED_NEW_SECURITY";
  selected_security_id: string;  // ID of the newly created security_master
}

// Re-export SecurityMaster for components that only import from import.ts
export type { SecurityMaster };
