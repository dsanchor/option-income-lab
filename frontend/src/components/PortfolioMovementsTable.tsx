"use client";

import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import Link from "next/link";
import { FileUp, Plus } from "lucide-react";
import { getMovements, deleteMovement, listAccounts } from "@/lib/portfolio-api";
import { getDefaultMovementsDateRange } from "@/lib/dateHelpers";
import type { MovementsResponse, LedgerMovement, TxnType, WarningType } from "@/types/portfolio";
import { SALES_TYPE_LABELS } from "@/types/portfolio";
import type { BrokerAccount } from "@/types/portfolio";
import type { MovementsFilter } from "@/lib/portfolio-api";
import MovementDetailDialog from "./MovementDetailDialog";
import AddMovementDialog from "./AddMovementDialog";
import AccountBadge from "./AccountBadge";
import { formatAccountName } from "@/lib/accountDisplay";
import NativeSelect from "./ui/NativeSelect";

const TXN_BADGE: Record<TxnType, string> = {
  BUY: "bg-accent-green/15 text-accent-green",
  SELL: "bg-accent-red/15 text-accent-red",
  DIVIDEND: "bg-accent-blue/15 text-accent-blue",
  TRANSFER_OUT: "bg-accent-orange/15 text-accent-orange",
  TRANSFER_IN: "bg-accent-orange/15 text-accent-orange",
};

const WARNING_SHORT: Record<WarningType, string> = {
  NEGATIVE_INVENTORY: "Negative inventory",
  RIGHTS_AMOUNT: "Rights amount",
  PROBABLE_DUPLICATE: "Probable duplicate",
  DERECHOS_WITH_QUANTITY: "Rights sale with quantity",
  ACCIONES_ZERO_QUANTITY: "Share sale, zero quantity",
  INVALID_SALES_TYPE: "Invalid sale type",
};

const PAGE_SIZE = 50;
// Symbol-search batch: matches the backend cap (le=500 on the movements route).
const SEARCH_BATCH_SIZE = 500;

type MovementTypeFilter = "" | "BUY" | "SELL" | "DIVIDEND" | "TRANSFER_OUT" | "TRANSFER_IN";

const TYPE_PILLS: Array<{ value: MovementTypeFilter; label: string }> = [
  { value: "", label: "All" },
  { value: "BUY", label: "Buy" },
  { value: "SELL", label: "Sell" },
  { value: "DIVIDEND", label: "Dividend" },
  { value: "TRANSFER_OUT", label: "Transfer Out" },
  { value: "TRANSFER_IN", label: "Transfer In" },
];

/** Case-insensitive multi-field symbol predicate — parity with SymbolsTable search. */
function matchesMovementSymbol(m: LedgerMovement, query: string): boolean {
  const q = query.trim().toUpperCase();
  if (!q) return true;
  return (
    (m.security_id || "").toUpperCase().includes(q) ||
    (m.ticker || "").toUpperCase().includes(q) ||
    (m.company_name || "").toUpperCase().includes(q)
  );
}

function fmtEur(amount: string | null | undefined): string {
  if (!amount) return "—";
  const n = Number(amount);
  if (Number.isNaN(n)) return "—";
  return `€${n.toLocaleString("es-ES", { minimumFractionDigits: 2 })}`;
}

function Skeleton() {
  return (
    <div className="space-y-2">
      {[...Array(6)].map((_, i) => (
        <div key={i} className="skeleton h-10 rounded-[var(--radius)]" />
      ))}
    </div>
  );
}

/** Client-side movements table with account filter, pagination, detail view, and manual entry. */
export default function PortfolioMovementsTable() {
  // When a symbol query is active: allRows holds the complete (unfiltered-by-symbol) fetch.
  // When no symbol query: serverData holds the current server-paginated page.
  // Exactly one is non-null after a successful load.
  const [allRows, setAllRows] = useState<LedgerMovement[] | null>(null);
  const [serverData, setServerData] = useState<MovementsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [accounts, setAccounts] = useState<BrokerAccount[]>([]);
  const [selectedMovement, setSelectedMovement] = useState<LedgerMovement | null>(null);
  const [showAddMovement, setShowAddMovement] = useState(false);
  const loadGenRef = useRef(0);
  // Set when the batch loop exits with accumulated < total_count (defense-in-depth only).
  const [searchIncomplete, setSearchIncomplete] = useState(false);

  // Filters — date defaults to today back 3 calendar months
  const [accountFilter, setAccountFilter] = useState("");
  const [txnType, setTxnType] = useState<MovementTypeFilter>("");
  const [securityId, setSecurityId] = useState("");
  const [dateFrom, setDateFrom] = useState(() => getDefaultMovementsDateRange().from);
  const [dateTo, setDateTo] = useState(() => getDefaultMovementsDateRange().to);

  // symbol_id is always handled client-side; never sent to the backend.
  const buildFilter = useCallback((): MovementsFilter => ({
    account_id: accountFilter || undefined,
    txn_type: txnType || undefined,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
  }), [accountFilter, txnType, dateFrom, dateTo]);

  /**
   * Load movements.
   * - symbolQuery non-empty: batch-fetch ALL rows matching account/type/date filters
   *   (no backend symbol filter) using SEARCH_BATCH_SIZE pages until total_count is
   *   satisfied or a short page signals end-of-data; multi-field predicate and
   *   client pagination are applied over the full accumulated dataset.
   *   Generation checks before AND after every awaited fetch prevent stale writes.
   * - symbolQuery empty: efficient server-side pagination using offset/PAGE_SIZE.
   */
  const load = useCallback(async (off: number, filter: MovementsFilter, symbolQuery: string) => {
    const gen = ++loadGenRef.current;
    setLoading(true);
    setError(null);
    setSearchIncomplete(false);
    const q = symbolQuery.trim();
    try {
      if (q) {
        // Batch-fetch all rows matching account/type/date filters.
        // Client-side predicate handles substring/company-name match across the full set.
        // accumulated: raw page-boundary count (including duplicates) used ONLY for
        // loop termination and genuine short-page/exhaustion mismatch warning.
        const accumulated: LedgerMovement[] = [];
        // seenIds + dedupedRows: unique rows in first-seen server order, used for
        // display, symbol matching, and local pagination.
        const seenIds = new Set<string>();
        const dedupedRows: LedgerMovement[] = [];
        let batchOffset = 0;
        let reportedTotal: number | null = null;

        while (reportedTotal === null || accumulated.length < reportedTotal) {
          if (gen !== loadGenRef.current) return; // cancelled — newer query started

          const page = await getMovements({
            ...filter,
            limit: SEARCH_BATCH_SIZE,
            offset: batchOffset,
          });

          if (gen !== loadGenRef.current) return; // stale response — discard

          // First page sets the authoritative total count for this filter set.
          if (reportedTotal === null) {
            reportedTotal = page.total_count;
          }

          accumulated.push(...page.movements);
          for (const m of page.movements) {
            if (m.id) {
              if (!seenIds.has(m.id)) {
                seenIds.add(m.id);
                dedupedRows.push(m);
              }
              // else: page-boundary duplicate — skip, first-seen row is canonical
            } else {
              // id is required by schema; if absent, include without dedup to
              // avoid silently collapsing unrelated rows.
              dedupedRows.push(m);
            }
          }
          batchOffset += SEARCH_BATCH_SIZE;

          // Short page (including empty) signals end of data from the server.
          if (page.movements.length < SEARCH_BATCH_SIZE) break;
        }

        if (gen !== loadGenRef.current) return; // final guard before any state commit

        // Defense-in-depth: loop exited via short page but count < reported total —
        // only fires on genuine data inconsistency, never merely from large result sets.
        // Keyed on raw accumulated count so duplicate removal never triggers false warning.
        const incomplete =
          reportedTotal !== null && accumulated.length < reportedTotal;
        setSearchIncomplete(incomplete);
        setAllRows(dedupedRows);
        setServerData(null);
      } else {
        const d = await getMovements({ ...filter, limit: PAGE_SIZE, offset: off });
        if (gen !== loadGenRef.current) return;
        setAllRows(null);
        setServerData(d);
      }
    } catch (err) {
      if (gen !== loadGenRef.current) return;
      const e = err as { status?: number; data?: { error?: string; detail?: string } };
      if (e.status === 503) {
        setError("Portfolio storage is not yet configured.");
      } else {
        setError(e.data?.detail ?? (err instanceof Error ? err.message : "Failed to load movements"));
      }
    } finally {
      if (gen === loadGenRef.current) setLoading(false);
    }
  }, []);

  const loadAccounts = useCallback(async () => {
    try {
      const resp = await listAccounts();
      setAccounts(resp.accounts);
    } catch {
      setAccounts([]);
    }
  }, []);

  // eslint-disable-next-line react-hooks/set-state-in-effect, react-hooks/exhaustive-deps
  useEffect(() => {
    const { from, to } = getDefaultMovementsDateRange();
    load(0, { date_from: from, date_to: to }, "");
    loadAccounts();
  }, []);

  // Symbol predicate applied over the full fetch (client mode only).
  const filteredAllRows = useMemo<LedgerMovement[] | null>(() => {
    if (allRows === null) return null;
    const q = securityId.trim();
    return q ? allRows.filter((m) => matchesMovementSymbol(m, q)) : allRows;
  }, [allRows, securityId]);

  // The actual page to render — sliced from filteredAllRows (client mode) or from server page.
  const displayedRows = useMemo<LedgerMovement[]>(() => {
    if (filteredAllRows !== null) return filteredAllRows.slice(offset, offset + PAGE_SIZE);
    return serverData?.movements ?? [];
  }, [filteredAllRows, serverData, offset]);

  // Single source of truth for footer count and Prev/Next guard.
  const totalCount = filteredAllRows !== null ? filteredAllRows.length : (serverData?.total_count ?? 0);
  const loaded = filteredAllRows !== null || serverData !== null;

  function applyFilter() {
    setOffset(0);
    load(0, buildFilter(), securityId);
  }

  function resetFilter() {
    const { from, to } = getDefaultMovementsDateRange();
    setAccountFilter("");
    setTxnType("");
    setSecurityId("");
    setDateFrom(from);
    setDateTo(to);
    setOffset(0);
    load(0, { date_from: from, date_to: to }, "");
  }

  function handleTypeFilter(v: MovementTypeFilter) {
    setTxnType(v);
    setOffset(0);
  }

  async function handleDelete(id: string, accountId?: string) {
    setDeleteError(null);
    try {
      await deleteMovement(id, accountId);
      load(offset, buildFilter(), securityId);
    } catch (err) {
      const e = err as { data?: { detail?: string; error?: string } };
      setDeleteError(e.data?.detail ?? (err instanceof Error ? err.message : "Delete failed"));
    }
  }

  const inputCls =
    "rounded-[var(--radius)] border border-border bg-bg-input px-3 py-1.5 text-sm text-text placeholder:text-text-muted focus:border-accent-blue focus:outline-none";

  return (
    <div className="space-y-5">
      {/* Action row — outside the filter card */}
      <div className="flex flex-wrap items-center justify-end gap-2">
        <button
          type="button"
          onClick={() => setShowAddMovement(true)}
          className="inline-flex items-center gap-1.5 rounded-[var(--radius)] bg-accent-blue/15 px-3 py-1.5 text-sm text-accent-blue hover:bg-accent-blue/25"
        >
          <Plus size={14} className="shrink-0" aria-hidden />
          Add movement
        </button>
        <Link
          href="/portfolio/import"
          className="inline-flex items-center gap-1.5 rounded-[var(--radius)] border border-border px-3 py-1.5 text-sm text-text-muted hover:bg-bg-hover"
        >
          <FileUp size={14} className="shrink-0" aria-hidden />
          Bulk import
        </Link>
      </div>

      {/* Filter card */}
      <div className="flex flex-wrap items-end gap-4 rounded-[var(--radius)] border border-border bg-bg-card px-4 py-3">

        {/* Account */}
        <div className="flex flex-col gap-1">
          <span className="text-xs text-text-muted">Account</span>
          <NativeSelect
            value={accountFilter}
            onChange={(e) => setAccountFilter(e.target.value)}
            className={`${inputCls} w-40`}
            aria-label="Filter by account"
          >
            <option value="">All accounts</option>
            <option value="_unassigned">Sin asignar</option>
            {accounts.map((a) => (
              <option key={a.account_id} value={a.account_id}>{formatAccountName(a)}</option>
            ))}
          </NativeSelect>
        </div>

        {/* Type — Economics Pills style */}
        <div className="flex flex-col gap-1">
          <span className="text-xs text-text-muted">Type</span>
          <div
            className="flex items-center gap-1 rounded-[var(--radius-pill)] border border-border bg-bg-card p-1"
            aria-label="Filter by transaction type"
            role="group"
          >
            {TYPE_PILLS.map((p) => (
              <button
                key={p.value}
                type="button"
                onClick={() => handleTypeFilter(p.value)}
                className={`rounded-[var(--radius-pill)] px-3 py-1 text-xs transition-colors ${
                  txnType === p.value
                    ? "bg-accent-blue text-white"
                    : "text-text-muted hover:text-text"
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>

        {/* Symbol */}
        <div className="flex flex-col gap-1">
          <span className="text-xs text-text-muted">Symbol</span>
          <input
            value={securityId}
            onChange={(e) => setSecurityId(e.target.value)}
            placeholder="Search…"
            className={`${inputCls} w-36`}
            aria-label="Filter by symbol"
          />
        </div>

        {/* Date range */}
        <div className="flex flex-col gap-1">
          <span className="text-xs text-text-muted">From</span>
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
            className={`${inputCls} w-36`}
            aria-label="From date"
            title="From date"
          />
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-xs text-text-muted">To</span>
          <input
            type="date"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
            className={`${inputCls} w-36`}
            aria-label="To date"
            title="To date"
          />
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={applyFilter}
            className="rounded-[var(--radius)] bg-accent-blue/15 px-3 py-1.5 text-sm text-accent-blue hover:bg-accent-blue/25"
          >
            Apply
          </button>
          <button
            type="button"
            onClick={resetFilter}
            className="rounded-[var(--radius)] border border-border px-3 py-1.5 text-sm text-text-muted hover:bg-bg-hover"
          >
            Reset
          </button>
        </div>
      </div>

      {searchIncomplete && !loading && (
        <div
          role="alert"
          aria-live="polite"
          className="rounded-[var(--radius)] border border-accent-orange/40 bg-accent-orange/10 px-4 py-2 text-sm text-accent-orange"
        >
          ⚠️ Search results may be incomplete — the server returned fewer movements than
          reported. Try narrowing the date range or other filters.
        </div>
      )}

      {deleteError && (
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-2 text-sm text-accent-red">
          {deleteError}
        </div>
      )}

      {error && (
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
          ⚠️ {error}
        </div>
      )}

      {loading ? (
        <Skeleton />
      ) : !loaded || displayedRows.length === 0 ? (
        <div className="rounded-[var(--radius-card)] border border-border bg-bg-card p-10 text-center space-y-3">
          <div className="text-3xl">📋</div>
          <div className="text-sm font-medium text-text">No movements found</div>
          <div className="text-xs text-text-muted">
            {offset > 0 || txnType || securityId || accountFilter || dateFrom || dateTo
              ? "Try adjusting the filters or date range."
              : "Import a CSV or add a movement to start your ledger."}
          </div>
          {!txnType && !securityId && !accountFilter && !dateFrom && !dateTo && (
            <div className="flex items-center justify-center gap-3">
              <button
                type="button"
                onClick={() => setShowAddMovement(true)}
                className="inline-flex items-center gap-1.5 rounded-[var(--radius)] bg-[image:var(--grad-blue)] px-4 py-2 text-sm font-medium text-white hover:opacity-90"
              >
                <Plus size={14} /> Add movement
              </button>
              <Link
                href="/portfolio/import"
                className="inline-flex items-center rounded-[var(--radius)] border border-border px-4 py-2 text-sm font-medium text-text-muted hover:bg-bg-hover"
              >
                Import CSV
              </Link>
            </div>
          )}
        </div>
      ) : (
        <>
          {/* Table */}
          <div className="overflow-x-auto rounded-[var(--radius)] border border-border">
            <table className="w-full table-modern text-sm">
              <thead>
                <tr className="border-b border-border bg-bg-card/80">
                  {["Type", "Symbol", "Date", "Qty", "Gross (€)", "Net (€)", "Account", ""].map(
                    (h, i) => (
                      <th
                        key={i}
                        className={`px-4 py-2.5 text-xs font-semibold uppercase tracking-wide text-text-muted ${
                          i >= 2 && i <= 5 ? "text-right" : "text-left"
                        }`}
                      >
                        {h}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-border/40">
                {displayedRows.map((m) => (
                  <MovementRow
                    key={m.id}
                    movement={m}
                    accounts={accounts}
                    onDelete={handleDelete}
                    onSelect={() => setSelectedMovement(m)}
                  />
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between text-sm text-text-muted">
            <span>
              Showing {offset + 1}–{Math.min(offset + PAGE_SIZE, totalCount)} of{" "}
              {totalCount}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => {
                  const next = Math.max(0, offset - PAGE_SIZE);
                  setOffset(next);
                  // Server-paginated mode: re-fetch new page; client mode: slice only.
                  if (filteredAllRows === null) load(next, buildFilter(), securityId);
                }}
                disabled={offset === 0}
                className="rounded-[var(--radius)] border border-border px-3 py-1.5 hover:bg-bg-hover disabled:opacity-40"
              >
                ← Prev
              </button>
              <button
                type="button"
                onClick={() => {
                  const next = offset + PAGE_SIZE;
                  setOffset(next);
                  if (filteredAllRows === null) load(next, buildFilter(), securityId);
                }}
                disabled={offset + PAGE_SIZE >= totalCount}
                className="rounded-[var(--radius)] border border-border px-3 py-1.5 hover:bg-bg-hover disabled:opacity-40"
              >
                Next →
              </button>
            </div>
          </div>
        </>
      )}

      {/* Detail dialog */}
      {selectedMovement && (
        <MovementDetailDialog
          movement={selectedMovement}
          accounts={accounts}
          onClose={() => setSelectedMovement(null)}
          onRefresh={() => { setSelectedMovement(null); load(offset, buildFilter(), securityId); }}
        />
      )}

      {/* Add movement dialog */}
      {showAddMovement && (
        <AddMovementDialog
          onClose={() => setShowAddMovement(false)}
          onCreated={() => { setShowAddMovement(false); setOffset(0); load(0, buildFilter(), securityId); }}
        />
      )}
    </div>
  );
}

function MovementRow({
  movement: m,
  accounts,
  onDelete,
  onSelect,
}: {
  movement: LedgerMovement;
  accounts: BrokerAccount[];
  onDelete: (id: string, accountId?: string) => void;
  onSelect: () => void;
}) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [showWarnings, setShowWarnings] = useState(false);
  const hasWarnings = m.warnings && m.warnings.length > 0;
  const hasIncomplete = m.cost_basis_status === "INCOMPLETE";
  const showWarningIcon = hasWarnings || hasIncomplete;

  return (
    <>
      <tr className="cursor-pointer hover:bg-bg-hover/30 transition-colors" onClick={onSelect}>
        <td className="px-4 py-2" onClick={(e) => e.stopPropagation()}>
          <span
            className={`rounded-full px-2 py-0.5 text-xs font-medium ${
              TXN_BADGE[m.txn_type] ?? "bg-bg-hover text-text-muted"
            }`}
          >
            {m.txn_type}
          </span>
          {m.txn_type === "SELL" && m.sales_type === "DERECHOS" && (
            <span className="ml-1 rounded-full px-1.5 py-0.5 text-xs bg-accent-orange/15 text-accent-orange">
              {SALES_TYPE_LABELS.DERECHOS}
            </span>
          )}
        </td>
        <td className="px-4 py-2">
          <div className="font-mono font-semibold text-text">{m.ticker}</div>
          <div className="text-xs text-text-muted truncate max-w-[160px]">{m.company_name}</div>
        </td>
        <td className="px-4 py-2 text-right text-text-muted">{m.trade_date}</td>
        <td className="px-4 py-2 text-right font-mono text-text">
          {m.quantity != null
            ? Number(m.quantity).toLocaleString("es-ES", { maximumFractionDigits: 6 })
            : "—"}
        </td>
        <td className="px-4 py-2 text-right font-mono text-text">
          {fmtEur(m.gross?.eur_amount)}
        </td>
        <td className="px-4 py-2 text-right font-mono text-text">
          {fmtEur(m.net?.eur_amount)}
        </td>
        <td className="px-4 py-2"><AccountBadge accountId={m.account_id} accounts={accounts} /></td>
        <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
          <div className="flex items-center gap-2 justify-end">
            {showWarningIcon && (
              <button
                type="button"
                onClick={() => setShowWarnings((v) => !v)}
                title="View warnings"
                className="text-accent-orange hover:opacity-70 transition-opacity"
              >
                ⚠
              </button>
            )}
            {confirmDelete ? (
              <span className="flex gap-1">
                <button
                  type="button"
                  onClick={() => onDelete(m.id, m.account_id)}
                  className="text-xs text-accent-red hover:underline"
                >
                  Confirm
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmDelete(false)}
                  className="text-xs text-text-muted hover:underline"
                >
                  Cancel
                </button>
              </span>
            ) : (
              <button
                type="button"
                onClick={() => setConfirmDelete(true)}
                className="text-xs text-text-muted hover:text-accent-red transition-colors"
                title="Soft-delete movement"
              >
                ×
              </button>
            )}
          </div>
        </td>
      </tr>
      {showWarnings && showWarningIcon && (
        <tr>
          <td colSpan={8} className="px-4 pb-3 pt-0">
            <div className="rounded-[var(--radius)] border border-accent-orange/30 bg-accent-orange/5 px-4 py-2 space-y-1">
              {hasIncomplete && !hasWarnings && (
                <div className="text-xs text-text-muted">
                  <span className="font-medium text-text mr-1">Incomplete cost basis:</span>
                  Zero-cost acquisition — cost basis not yet assigned.
                </div>
              )}
              {m.warnings?.map((w, i) => (
                <div key={i} className="flex items-start gap-2 text-xs text-text-muted">
                  <span className="text-accent-orange mt-0.5 shrink-0">⚠</span>
                  <span>
                    <span className="font-medium text-text mr-1">
                      {WARNING_SHORT[w.type as WarningType] ?? w.type}:
                    </span>
                    {w.message}
                  </span>
                </div>
              ))}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
