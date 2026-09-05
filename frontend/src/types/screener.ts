// Shapes returned by GET /api/screener/options (backend/web/app.py, wrapping
// src/options_screener.py's evaluate_options_screener + src/best_options.py's
// evaluate_best_options). Design: .squad/decisions/inbox/
// copilot-options-screener-approved.md ("Options Screener", approved
// 2026-08-29) and danny-options-screener-share-availability.md (accepted
// 2026-09-05). Deterministic aggregation across every symbol -- no LLM in
// this path, and no field here is derived or re-computed by the frontend;
// every value is produced by the pure backend evaluator/aggregator.

import type { BestOptionGates, BestOptionColor } from "@/types/best-options";

export type ScreenerSide = "call" | "put";
export type ScreenerPreference = "Preferred" | "Acceptable" | "Avoid";

/** Share availability status for covered-call rows only (never present on put
 * rows). Computed once per symbol in app.py from position state (Cosmos) and
 * stamped identically on every call row for that symbol:
 *   "available"        — at least one full lot (100 shares) free for a new call
 *   "shares_committed" — ≥100 shares owned, but all lots tied to active calls
 *   "no_shares"        — fewer than 100 total shares owned */
export type ShareStatus = "available" | "shares_committed" | "no_shares";

/** Presentation-layer sort keys the API accepts (`sort`/`dir` query params) --
 * "default" is the aggregator's own canonical order (score desc, DTE asc,
 * category-relative delta fit asc); every other value is a plain re-order of
 * already-scored/gated rows, never a re-derivation of score or admission. */
export type ScreenerSortField =
  | "default"
  | "annualized_return_pct"
  | "premium_pct"
  | "dte"
  | "open_interest"
  | "abs_delta"
  | "symbol";
export type ScreenerSortDir = "asc" | "desc";

/** One row per admitted contract, across every symbol on the requested side --
 * the same per-contract shape `best_options.py` produces (score/label/color/
 * gates/flags/etc.), tagged with `symbol`/`category` by the aggregator since a
 * screener spans many symbols at once. Share-availability fields and
 * `chain_stale` are added by the API layer itself (never the aggregator or
 * evaluator). Share-availability (`share_status`, `total_shares`,
 * `active_call_count`, `free_lots`) is a covered-CALL-only concept and is
 * only ever present on call-side rows. `chain_stale` reflects this request's
 * own cache-TTL freshness check for the row's symbol and is distinct from the
 * row's own `stale` field (that one is the evaluator's per-contract
 * quote-level staleness -- the two answer different questions and are never
 * conflated). */
export interface ScreenerOptionRow {
  symbol: string;
  category: string | null;
  underlying_price: number | null;
  expiration: string;
  dte: number;
  strike: number;
  bid: number | null;
  ask: number | null;
  mid: number | null;
  iv: number | null;
  delta: number | null;
  abs_delta: number | null;
  open_interest: number | null;
  premium_pct: number | null;
  annualized_return_pct: number | null;
  effective_min_pct: number | null;
  effective_wait_pct: number | null;
  collateral: number | null;
  score: number | null;
  color: BestOptionColor;
  label: string;
  components: Record<string, number | null>;
  components_missing: string[];
  weight_basis: number;
  gates: BestOptionGates;
  flags: string[];
  quote_asof: string | null;
  stale: boolean;
  /** Call rows only: classification of share availability for covered calls. */
  share_status?: ShareStatus;
  /** Call rows only: total shares held for this symbol (clamped ≥0). */
  total_shares?: number;
  /** Call rows only: number of active call positions consuming lots. */
  active_call_count?: number;
  /** Call rows only: shares committed to active calls (active_call_count * 100). */
  committed_shares?: number;
  /** Call rows only: shares free for a new covered call (total_shares - committed_shares, ≥ 0). */
  free_shares?: number;
  /** Call rows only: free lots available for a new covered call (free_shares // 100). */
  free_lots?: number;
  chain_stale: boolean;
  entry_stale?: boolean;
}

/** Per zero-row *symbol* (never per filtered-out row) explaining why that
 * symbol contributed nothing on this side -- tagged with `symbol`/`category`
 * the same way rows are. Shape mirrors `BestOptionsNearestMiss` (descriptive,
 * not fully fixed by the design doc) plus the two aggregator-added tags. */
export interface ScreenerNearestMiss {
  symbol: string;
  category: string | null;
  description?: string;
  expiration?: string | null;
  dte?: number | null;
  strike?: number | null;
  abs_delta?: number | null;
  missed_gate?: string | null;
  missed_threshold?: string | null;
  missed_by?: number | null;
  missed_by_pct?: number | null;
  [key: string]: unknown;
}

/** Richer than the aggregator's own ready/warming/error summary: distinguishes
 * a symbol actively being warmed by *this* request (`warming`, capped at 4 new
 * refreshes/request) from one left cold because that cap was already reached
 * (`cold`, will warm on a later request/poll) -- both map to the aggregator's
 * single "warming" status internally, but the UI's partial-status header needs
 * the distinction. DEPRECATED after precomputed-only refactor: replaced by
 * loaded/pending readiness summary. */
export type ScreenerSymbolStatus = "ok" | "pending" | "error";

export interface ScreenerSymbolDetail {
  symbol: string;
  status: ScreenerSymbolStatus;
  generation?: number;
  computed_at?: string | null;
  chain_timestamp?: string | null;
  reason?: string | null;
  error?: string;
}

export interface ScreenerSymbolsSummary {
  total: number;
  loaded: number;
  loaded_fresh: number;
  loaded_stale: number;
  pending: number;
  error: number;
  detail: ScreenerSymbolDetail[];
}

/** Echoes the resolved/effective filter values the response was produced
 * with -- the single source of truth for "what am I actually looking at,"
 * never re-derived by the frontend from its own input state. */
export interface ScreenerFilters {
  side: ScreenerSide;
  preferences: string[];
  symbols: string[] | null;
  min_annualized_return_pct: number | null;
  min_abs_delta: number | null;
  max_abs_delta: number | null;
  min_dte: number | null;
  max_dte: number | null;
  min_open_interest: number | null;
  min_gap_pct: number | null;
  max_gap_pct: number | null;
  /** null = no filter (show all); subset of ShareStatus values when active. */
  share_availability: ShareStatus[] | null;
  offset: number;
  limit: number;
  sort: ScreenerSortField;
  dir: ScreenerSortDir;
}

export interface ScreenerPagination {
  offset: number;
  limit: number;
  total_matching: number;
  returned: number;
  has_more: boolean;
}

export interface ScreenerOptionsResponse {
  schema_version: number;
  generated_at: string;
  side: ScreenerSide;
  filters: ScreenerFilters;
  symbols: ScreenerSymbolsSummary;
  rows: ScreenerOptionRow[];
  nearest_miss: ScreenerNearestMiss[];
  pagination: ScreenerPagination;
  cache?: {
    generation?: number;
    computed_at?: string | null;
    trigger?: string;
    truncated?: boolean;
    next_run?: string | null;
  };
}

export interface ScreenerErrorResponse {
  error: string;
}

export type ScreenerOptionsApiResponse = ScreenerOptionsResponse | ScreenerErrorResponse;
