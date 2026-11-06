"use client";

import { useEffect, useState, useCallback, useMemo, useRef } from "react";
import { Link2 } from "lucide-react";
import { getMovements, listAccounts } from "@/lib/portfolio-api";
import { subCalendarMonths, toLocalDateString } from "@/lib/dateHelpers";
import type { LedgerMovement, BrokerAccount, TxnType } from "@/types/portfolio";
import MovementDetailDialog from "./MovementDetailDialog";
import ReassignmentDialog from "./ReassignmentDialog";
import { getAccountName } from "@/lib/accountDisplay";
import { getMovementTypeLabel } from "@/lib/movementTypeLabel";
import {
  filterMovementsByType,
  getServerMovementTypeFilter,
} from "@/lib/filterMovementsByType";
import {
  dedupeMovementsById,
  fetchAllMovementPages,
  isAbortError,
  LatestMovementRequest,
} from "@/lib/movementPagination";

const PAGE_SIZE = 20;
const FILTER_BATCH_SIZE = 500;

const TXN_BADGE: Record<TxnType, string> = {
  BUY: "bg-accent-green/15 text-accent-green",
  SELL: "bg-accent-red/15 text-accent-red",
  DIVIDEND: "bg-accent-blue/15 text-accent-blue",
  TRANSFER_OUT: "bg-accent-orange/15 text-accent-orange",
  TRANSFER_IN: "bg-accent-orange/15 text-accent-orange",
  CALL_SELL: "bg-accent-purple/15 text-accent-purple",
  CALL_BUY: "bg-accent-cyan/15 text-accent-cyan",
  PUT_SELL: "bg-accent-purple/15 text-accent-purple",
  PUT_BUY: "bg-accent-cyan/15 text-accent-cyan",
};

type TypeFilter = "ALL" | "BUY" | "SELL" | "DIVIDEND";

const TYPE_PILLS: Array<{ value: TypeFilter; label: string }> = [
  { value: "ALL", label: "All" },
  { value: "BUY", label: "Buy" },
  { value: "SELL", label: "Sell" },
  { value: "DIVIDEND", label: "Dividend" },
];

type TimeFilter = "1m" | "3m" | "6m" | "1y" | "ALL";

const TIME_FILTER_MONTHS: Record<Exclude<TimeFilter, "ALL">, number> = {
  "1m": 1,
  "3m": 3,
  "6m": 6,
  "1y": 12,
};

const TIME_PILLS: Array<{ value: TimeFilter; label: string }> = [
  { value: "1m", label: "1m" },
  { value: "3m", label: "3m" },
  { value: "6m", label: "6m" },
  { value: "1y", label: "1y" },
  { value: "ALL", label: "All" },
];

function fmt(amount: string | null | undefined): string {
  if (!amount) return "—";
  const n = parseFloat(amount);
  if (isNaN(n)) return "—";
  return `€${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function whtTotal(m: LedgerMovement): number {
  const src = parseFloat(m.withholding?.source?.amount_eur ?? "0") || 0;
  const dst = parseFloat(m.withholding?.destination?.amount_eur ?? "0") || 0;
  return src + dst;
}

interface Props {
  securityId: string;
}

/**
 * Full BUY/SELL/DIVIDEND transaction history for a security.
 * Calls the existing movements API with security_id filter.
 * Amendment I — replaces SymbolMovementsTable inside the Stocks section.
 */
export default function StockTransactionsTable({ securityId }: Props) {
  const [movements, setMovements] = useState<LedgerMovement[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [page, setPage] = useState(0);
  const [typeFilter, setTypeFilter] = useState<TypeFilter>("ALL");
  const [timeFilter, setTimeFilter] = useState<TimeFilter>("3m");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [accounts, setAccounts] = useState<BrokerAccount[]>([]);
  const [selected, setSelected] = useState<LedgerMovement | null>(null);
  const [showReassign, setShowReassign] = useState(false);
  const requestRef = useRef(new LatestMovementRequest());

  const load = useCallback(async (pg: number, tf: TypeFilter, timef: TimeFilter) => {
    const request = requestRef.current.begin();
    setLoading(true);
    setError(null);
    try {
      const date_from = timef !== "ALL"
        ? toLocalDateString(subCalendarMonths(new Date(), TIME_FILTER_MONTHS[timef]))
        : undefined;
      const serverTxnType = getServerMovementTypeFilter(tf);
      if (tf === "DIVIDEND") {
        const result = await fetchAllMovementPages({
          pageSize: FILTER_BATCH_SIZE,
          signal: request.signal,
          fetchPage: (offset, limit, signal) =>
            getMovements({
              security_id: securityId,
              txn_type: serverTxnType,
              date_from,
              limit,
              offset,
            }, { signal }),
        });
        if (!request.isCurrent()) return;
        const filtered = filterMovementsByType(result.movements, tf);
        setMovements(filtered.slice(pg * PAGE_SIZE, (pg + 1) * PAGE_SIZE));
        setTotalCount(filtered.length);
      } else {
        const data = await getMovements({
          security_id: securityId,
          txn_type: serverTxnType,
          date_from,
          limit: PAGE_SIZE,
          offset: pg * PAGE_SIZE,
        }, { signal: request.signal });
        if (!request.isCurrent()) return;
        setMovements(dedupeMovementsById(data.movements));
        setTotalCount(data.total_count);
      }
    } catch (err) {
      if (!request.isCurrent() || isAbortError(err)) return;
      setError("Failed to load transactions.");
    } finally {
      if (request.isCurrent()) setLoading(false);
    }
  }, [securityId]);

  useEffect(() => () => {
    requestRef.current.cancel();
  }, []);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (!cancelled) void load(page, typeFilter, timeFilter);
    });
    return () => {
      cancelled = true;
    };
  }, [load, page, typeFilter, timeFilter]);

  useEffect(() => {
    listAccounts()
      .then((r) => setAccounts(r.accounts))
      .catch(() => {/* non-fatal */});
  }, []);

  const accountMap = useMemo(() =>
    Object.fromEntries(accounts.map((a) => [a.account_id, getAccountName(a.account_id, accounts)])),
    [accounts]
  );

  // Derived column visibility
  const hasFees = movements.some((m) => parseFloat(m.fees?.total_eur ?? "0") > 0);
  const hasWht = movements.some((m) => whtTotal(m) > 0);
  const uniqueAccounts = new Set(movements.map((m) => m.account_id));
  const showAccount = uniqueAccounts.size > 1;

  const totalPages = Math.ceil(totalCount / PAGE_SIZE);

  function handleTypeFilter(tf: TypeFilter) {
    setTypeFilter(tf);
    setPage(0);
  }

  function handleTimeFilter(tf: TimeFilter) {
    setTimeFilter(tf);
    setPage(0);
  }

  return (
    <section className="space-y-3">
      {/* Toolbar: type filter pills + time period pills + batch reassign */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-text">Stock Transactions</h2>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex flex-wrap items-center gap-1 rounded-[var(--radius-pill)] border border-border bg-bg-card p-1" role="group" aria-label="Transaction type filter">
            {TYPE_PILLS.map((p) => (
              <button
                key={p.value}
                type="button"
                onClick={() => handleTypeFilter(p.value)}
                className={`rounded-[var(--radius-pill)] px-3 py-1 text-xs transition ${
                  typeFilter === p.value
                    ? "bg-accent-blue text-white"
                    : "text-text-muted hover:text-text"
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-1 rounded-[var(--radius-pill)] border border-border bg-bg-card p-1" role="group" aria-label="Time period">
            {TIME_PILLS.map((p) => (
              <button
                key={p.value}
                type="button"
                onClick={() => handleTimeFilter(p.value)}
                className={`rounded-[var(--radius-pill)] px-3 py-1 text-xs transition ${
                  timeFilter === p.value
                    ? "bg-accent-blue text-white"
                    : "text-text-muted hover:text-text"
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={() => setShowReassign(true)}
            className="ml-auto rounded-[var(--radius-pill)] border border-border px-3 py-1 text-xs font-medium text-text-muted hover:bg-bg-hover hover:text-text transition-colors"
            aria-label={`Batch reassign movements for ${securityId}`}
          >
            Reassign accounts
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-[var(--radius)] border border-accent-red/30 bg-accent-red/5 px-4 py-2 text-sm text-accent-red">
          {error}
        </div>
      )}

      {loading ? (
        <div className="rounded-[var(--radius)] border border-border bg-bg-card px-4 py-6 text-center text-sm text-text-muted">Loading…</div>
      ) : movements.length === 0 ? (
        <div className="rounded-[var(--radius)] border border-border bg-bg-card px-4 py-6 text-center text-sm text-text-muted">
          No stock transactions recorded for this symbol.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-[var(--radius)] border border-border bg-bg-card">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-bg-card/80 text-left text-xs uppercase tracking-wide text-text-muted">
                <th scope="col" className="px-4 py-2.5 font-semibold">Date</th>
                <th scope="col" className="px-4 py-2.5 font-semibold">Type</th>
                <th scope="col" className="px-4 py-2.5 font-semibold text-right">Qty</th>
                <th scope="col" className="px-4 py-2.5 font-semibold text-right">Avg Price / Share (€)</th>
                <th scope="col" className="px-4 py-2.5 font-semibold text-right">Gross (€)</th>
                {hasFees && <th scope="col" className="px-4 py-2.5 font-semibold text-right">Fees (€)</th>}
                {hasWht && <th scope="col" className="px-4 py-2.5 font-semibold text-right">WHT (€)</th>}
                <th scope="col" className="px-4 py-2.5 font-semibold text-right">Net (€)</th>
                {showAccount && <th scope="col" className="px-4 py-2.5 font-semibold">Account</th>}
              </tr>
            </thead>
            <tbody>
              {movements.map((m) => {
                const wht = whtTotal(m);
                const isGrouped = !!m.ca_group_id;
                return (
                  <tr
                    key={m.id}
                    className="cursor-pointer border-b border-border/40 transition-colors hover:bg-bg-hover last:border-0"
                    onClick={() => setSelected(m)}
                  >
                    <td className="px-4 py-2.5 font-mono text-text-muted text-xs">{m.trade_date}</td>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-1">
                        <span
                          className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                            TXN_BADGE[m.txn_type] ?? "bg-bg-hover text-text-muted"
                          }`}
                        >
                          {getMovementTypeLabel(m)}
                        </span>
                        {isGrouped && (
                          <span
                            title={`Corporate action group: ${m.ca_group_id}`}
                            className="text-accent-blue/60"
                          >
                            <Link2 size={10} />
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono text-text">
                      {m.quantity != null && Number(m.quantity) !== 0
                        ? Number(m.quantity).toLocaleString("en-US", { maximumFractionDigits: 6 })
                        : "—"}
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono text-text">
                      {fmt(m.lot_average_price_eur)}
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono text-text">{fmt(m.gross?.eur_amount)}</td>
                    {hasFees && (
                      <td className="px-4 py-2.5 text-right font-mono text-text-muted">
                        {parseFloat(m.fees?.total_eur ?? "0") > 0 ? fmt(m.fees?.total_eur) : "—"}
                      </td>
                    )}
                    {hasWht && (
                      <td className="px-4 py-2.5 text-right font-mono text-text-muted">
                        {wht > 0
                          ? `€${wht.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                          : "—"}
                      </td>
                    )}
                    <td className="px-4 py-2.5 text-right font-mono font-medium text-text">{fmt(m.net?.eur_amount)}</td>
                    {showAccount && (
                      <td className="px-4 py-2.5 text-text-muted text-xs">
                        {accountMap[m.account_id] ?? m.account_id}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between text-xs text-text-muted">
          <span>
            {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, totalCount)} of {totalCount}
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={page === 0}
              onClick={() => setPage((p) => p - 1)}
              className="rounded-[var(--radius)] border border-border px-3 py-1 hover:bg-bg-hover disabled:opacity-40 disabled:cursor-not-allowed"
            >
              ← Prev
            </button>
            <button
              type="button"
              disabled={page >= totalPages - 1}
              onClick={() => setPage((p) => p + 1)}
              className="rounded-[var(--radius)] border border-border px-3 py-1 hover:bg-bg-hover disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Next →
            </button>
          </div>
        </div>
      )}

      {/* Movement detail dialog */}
      {selected && (
        <MovementDetailDialog
          movement={selected}
          onClose={() => setSelected(null)}
          onRefresh={() => {
            setSelected(null);
            load(page, typeFilter, timeFilter);
          }}
        />
      )}

      {/* Batch account reassignment — prefilled and locked to this security */}
      {showReassign && (
        <ReassignmentDialog
          mode="batch"
          lockedSecurityId={securityId}
          onClose={() => setShowReassign(false)}
          onReassigned={() => { setShowReassign(false); load(page, typeFilter, timeFilter); }}
        />
      )}
    </section>
  );
}
