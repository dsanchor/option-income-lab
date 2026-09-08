"use client";

import { useState, useCallback, useMemo, useEffect } from "react";
import Link from "next/link";
import { getHoldings, listAccounts } from "@/lib/portfolio-api";
import type { HoldingEntry, HoldingsResponse, WarningType } from "@/types/portfolio";
import type { BrokerAccount } from "@/types/portfolio";
import ReassignmentDialog from "./ReassignmentDialog";
import StatCard from "@/components/StatCard";
import Reveal from "@/components/Reveal";
import AccountBadge from "@/components/AccountBadge";
import { formatAccountName, getAccountName } from "@/lib/accountDisplay";

const WARNING_SHORT: Record<WarningType, string> = {
  NEGATIVE_INVENTORY: "Negative inventory",
  RIGHTS_AMOUNT: "Rights amount pending",
  PROBABLE_DUPLICATE: "Probable duplicate",
  DERECHOS_WITH_QUANTITY: "Rights sale with quantity",
  ACCIONES_ZERO_QUANTITY: "Share sale, zero quantity",
  INVALID_SALES_TYPE: "Invalid sale type",
};

function Skeleton() {
  return (
    <div className="space-y-2">
      {[...Array(4)].map((_, i) => (
        <div key={i} className="skeleton h-12 rounded-[var(--radius)]" />
      ))}
    </div>
  );
}

/** Client-side holdings table with account filter, empty, loading, and error states. */
export default function PortfolioHoldingsTable() {
  const [data, setData] = useState<HoldingsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [hideZeroShares, setHideZeroShares] = useState(true);
  const [accountFilter, setAccountFilter] = useState("");
  const [accounts, setAccounts] = useState<BrokerAccount[]>([]);
  const [showReassign, setShowReassign] = useState(false);

  const load = useCallback(async (acctId?: string) => {
    setLoading(true);
    setError(null);
    try {
      const [holdingsData, acctResp] = await Promise.allSettled([
        getHoldings(acctId || undefined),
        listAccounts(),
      ]);
      if (holdingsData.status === "fulfilled") setData(holdingsData.value);
      else {
        const e = holdingsData.reason as { status?: number; data?: { detail?: string } };
        if (e.status === 503) {
          setError("Portfolio storage is not yet configured. Movements will appear here once the backend is set up.");
        } else {
          setError(e.data?.detail ?? (holdingsData.reason instanceof Error ? holdingsData.reason.message : "Failed to load holdings"));
        }
      }
      if (acctResp.status === "fulfilled") setAccounts(acctResp.value.accounts);
    } finally {
      setLoading(false);
    }
  }, []);

  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { load(); }, [load]);

  function handleAccountFilter(acctId: string) {
    setAccountFilter(acctId);
    load(acctId || undefined);
  }

  const visibleHoldings = useMemo(() => {
    if (!data) return [];
    return data.holdings.filter((h) => {
      const shares = parseFloat(h.total_shares);
      if (shares < 0) return true;
      if (hideZeroShares && shares === 0) return false;
      if (searchQuery) {
        const q = searchQuery.toLowerCase();
        return (
          h.ticker.toLowerCase().includes(q) ||
          h.company_name.toLowerCase().includes(q) ||
          h.security_id.toLowerCase().includes(q)
        );
      }
      return true;
    });
  }, [data, hideZeroShares, searchQuery]);

  if (loading) return <Skeleton />;

  if (error) {
    return (
      <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
        ⚠️ {error}
      </div>
    );
  }

  if (!data || data.holdings.length === 0) {
    return (
      <div className="rounded-[var(--radius-card)] border border-border bg-bg-card p-10 text-center space-y-4">
        <div className="text-4xl">📂</div>
        <div className="text-base font-medium text-text">No holdings yet</div>
        <div className="text-sm text-text-muted">
          Holdings are derived from committed ledger movements.
          <br />Import your first CSV to get started.
        </div>
        <Link
          href="/portfolio/import"
          className="inline-flex items-center gap-2 rounded-[var(--radius)] bg-[image:var(--grad-blue)] px-5 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          Import CSV
        </Link>
      </div>
    );
  }

  const { summary } = data;
  const isFiltered = searchQuery.trim() !== "" || accountFilter !== "" || (hideZeroShares && data.holdings.some((h) => parseFloat(h.total_shares) === 0));

  return (
    <div className="space-y-5">
      {/* ── Portfolio summary — StatCard grid (same pattern as Economics/Dashboard) ── */}
      <div className="space-y-4">
        {/* Primary KPIs */}
        <div className="grid gap-4 sm:grid-cols-3">
          <Reveal index={0} className="h-full">
            <StatCard
              label="Inversión actual"
              value={parseFloat(summary.remaining_cost_basis_eur ?? summary.current_invested_eur)}
              prefix="€"
              decimals={2}
              tone="neutral"
              tooltip="Base de coste de las acciones que aún posees. = Total compras − Coste asignado a acciones vendidas. Calculado con media ponderada móvil (CMP). No es FIFO ni válido para fines fiscales."
            />
          </Reveal>
          <Reveal index={1} className="h-full">
            <StatCard
              label="Resultado realizado"
              value={summary.realized_result_eur != null ? parseFloat(summary.realized_result_eur) : undefined}
              prefix="€"
              decimals={2}
              tone={
                summary.realized_result_eur == null
                  ? "neutral"
                  : parseFloat(summary.realized_result_eur) >= 0
                  ? "green"
                  : "red"
              }
              tooltip="Ganancia o pérdida cerrada: ingresos por ventas de acciones y derechos menos el coste de adquisición de las acciones vendidas (media ponderada móvil). No válido para fines fiscales."
            />
          </Reveal>
          <Reveal index={2} className="h-full">
            <StatCard
              label="Dividendos netos"
              value={parseFloat(summary.total_dividends_eur)}
              prefix="€"
              decimals={2}
              tone="green"
              tooltip="Dividendos netos recibidos (tras retenciones)."
            />
          </Reveal>
        </div>

        {/* Indicators + warning + actions */}
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-xs text-text-muted">
            Valores: <strong className="font-medium text-text">{summary.total_securities}</strong>
          </span>
          {summary.has_incomplete_cost_basis && (
            <span
              role="alert"
              className="inline-flex items-center gap-1.5 rounded-full bg-accent-orange/10 px-2.5 py-1 text-xs font-medium text-accent-orange"
            >
              <span aria-hidden="true">⚠</span>
              Algún valor tiene coste incompleto — los cálculos de inversión pueden no ser exactos
            </span>
          )}
          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={() => setShowReassign(true)}
              className="rounded-[var(--radius-pill)] border border-border px-3 py-1.5 text-xs text-text-muted hover:bg-bg-hover"
              aria-label="Batch reassign movements"
            >
              Batch reassign
            </button>
            <button
              type="button"
              onClick={() => load(accountFilter || undefined)}
              className="rounded-[var(--radius-pill)] border border-border px-3 py-1.5 text-xs text-text-muted hover:bg-bg-hover"
              aria-label="Refresh holdings"
            >
              Refresh
            </button>
          </div>
        </div>
      </div>

      {/* Filter toolbar */}
      <div className="flex flex-wrap items-center gap-3 text-sm">
        {/* Account filter */}
        <select
          value={accountFilter}
          onChange={(e) => handleAccountFilter(e.target.value)}
          className="rounded-[var(--radius)] border border-border bg-bg-card px-3 py-2 text-sm text-text focus:outline-none focus:ring-2 focus:ring-border"
          aria-label="Filter by account"
        >
          <option value="">All accounts</option>
          <option value="_unassigned">Sin asignar</option>
          {accounts.map((a) => (
            <option key={a.account_id} value={a.account_id}>{formatAccountName(a)}</option>
          ))}
        </select>

        <label className="relative flex-1 min-w-[180px] max-w-xs">
          <span className="sr-only">Search holdings</span>
          <input
            type="search"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search ticker, name, ID…"
            aria-label="Search holdings by ticker, company name, or security ID"
            className="w-full rounded-[var(--radius)] border border-border bg-bg-card px-3 py-2 pl-8 text-sm text-text placeholder-text-muted focus:outline-none focus:ring-2 focus:ring-border"
          />
          <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted text-xs pointer-events-none" aria-hidden="true">
            🔍
          </span>
        </label>

        <label className="flex items-center gap-2 cursor-pointer select-none text-text-muted hover:text-text">
          <input
            type="checkbox"
            checked={hideZeroShares}
            onChange={(e) => setHideZeroShares(e.target.checked)}
            className="rounded border-border"
            aria-label="Hide zero-share holdings"
          />
          <span className="text-xs">Hide zero-share holdings</span>
        </label>
      </div>

      {/* Table or filtered-empty state */}
      {visibleHoldings.length === 0 ? (
        <div className="rounded-[var(--radius-card)] border border-border bg-bg-card p-10 text-center space-y-3">
          <div className="text-3xl">🔍</div>
          <div className="text-base font-medium text-text">No holdings match your filters</div>
          <div className="text-sm text-text-muted">
            Try adjusting your search or showing zero-share holdings.
          </div>
          <button
            type="button"
            onClick={() => { setSearchQuery(""); setHideZeroShares(false); setAccountFilter(""); load(); }}
            className="inline-flex items-center gap-2 rounded-[var(--radius)] border border-border px-4 py-2 text-sm text-text-muted hover:bg-bg-hover"
          >
            Clear filters
          </button>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-[var(--radius)] border border-border">
          {isFiltered && (
            <div className="flex items-center gap-2 border-b border-border/40 bg-bg-card/60 px-4 py-2 text-xs text-text-muted">
              <span>Showing {visibleHoldings.length} of {data.holdings.length} holdings</span>
              {accountFilter && (
                <span>· Account: <strong>{getAccountName(accountFilter, accounts)}</strong></span>
              )}
              <button
                type="button"
                onClick={() => { setSearchQuery(""); setHideZeroShares(false); setAccountFilter(""); load(); }}
                className="ml-2 underline hover:text-text"
                aria-label="Clear all filters"
              >
                Clear filters
              </button>
            </div>
          )}
          <table className="w-full table-modern text-sm">
            <thead>
              <tr className="border-b border-border bg-bg-card/80">
                {["Security", "Shares", "Avg Cost (€)", "Invested (€)", "Dividends (€)", "Accounts", ""].map(
                  (h, i) => (
                    <th
                      key={i}
                      scope="col"
                      className={`px-4 py-2.5 text-xs font-semibold uppercase tracking-wide text-text-muted ${
                        i >= 1 && i <= 4 ? "text-right" : "text-left"
                      }`}
                    >
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-border/40">
              {visibleHoldings.map((h) => (
                <HoldingRow key={h.security_id} holding={h} accounts={accounts} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showReassign && (
        <ReassignmentDialog
          mode="batch"
          onClose={() => setShowReassign(false)}
          onReassigned={() => { setShowReassign(false); load(accountFilter || undefined); }}
        />
      )}
    </div>
  );
}

function HoldingRow({ holding: h, accounts }: { holding: HoldingEntry; accounts: BrokerAccount[] }) {
  const [showWarnings, setShowWarnings] = useState(false);
  const shares = parseFloat(h.total_shares);
  const isNegative = shares < 0;

  return (
    <>
      <tr>
        <td className="px-4 py-3">
          <div className="font-mono font-semibold text-text">{h.ticker}</div>
          <div className="text-xs text-text-muted truncate max-w-[180px]">{h.company_name}</div>
        </td>
        <td className={`px-4 py-3 text-right font-mono ${isNegative ? "text-accent-red" : "text-text"}`}>
          {Number(h.total_shares).toLocaleString("es-ES", { maximumFractionDigits: 6 })}
        </td>
        <td className="px-4 py-3 text-right font-mono text-text">
          {h.avg_cost_basis_eur != null
            ? `€${Number(h.avg_cost_basis_eur).toLocaleString("es-ES", { minimumFractionDigits: 2 })}`
            : <span className="text-text-muted text-xs">Incomplete</span>}
        </td>
        <td className="px-4 py-3 text-right font-mono text-text">
          €{Number(h.remaining_cost_basis_eur ?? h.total_invested_eur).toLocaleString("es-ES", { minimumFractionDigits: 2 })}
        </td>
        <td className="px-4 py-3 text-right font-mono text-accent-green">
          €{Number(h.total_dividends_eur).toLocaleString("es-ES", { minimumFractionDigits: 2 })}
        </td>
        <td className="px-4 py-3">
          <div className="flex flex-wrap gap-1">
            {h.accounts.map((a) => (
              <AccountBadge key={a} accountId={a} accounts={accounts} />
            ))}
          </div>
        </td>
        <td className="px-3 py-3 text-right">
          {h.warnings.length > 0 && (
            <button
              type="button"
              onClick={() => setShowWarnings((v) => !v)}
              title="View warnings"
              aria-expanded={showWarnings}
              className="text-accent-orange hover:text-accent-orange/70 transition-colors"
            >
              ⚠
            </button>
          )}
        </td>
      </tr>
      {showWarnings && h.warnings.length > 0 && (
        <tr>
          <td colSpan={7} className="px-4 pb-3 pt-0">
            <div className="rounded-[var(--radius)] border border-accent-orange/30 bg-accent-orange/5 px-4 py-2 space-y-1">
              {h.warnings.map((w, i) => (
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
