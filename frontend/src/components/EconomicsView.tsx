"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import AccountBadge from "@/components/AccountBadge";
import EconomicsTabs from "@/components/EconomicsTabs";
import MultiSelect from "@/components/MultiSelect";
import {
  CoverageStatusBadge,
  PaperBadge,
  WarningBadge,
} from "@/components/OptionLinkageBadges";
import Reveal from "@/components/Reveal";
import StatCard from "@/components/StatCard";
import { getAccountName } from "@/lib/accountDisplay";
import { averageLastNExcludingZero } from "@/lib/format";
import { getMovements, listAccounts, setPositionPaper } from "@/lib/portfolio-api";
import MovementDetailDialog from "@/components/MovementDetailDialog";
import type { BrokerAccount, LedgerMovement } from "@/types/portfolio";
import type {
  EconomicsBySymbolRow,
  EconomicsCoverage,
  EconomicsMonthlyRow,
  EconomicsPosition,
  EconomicsReport,
  EconomicsSortKey,
  EconomicsSummary,
} from "@/types/economics";

const MONTHS = [
  { value: "1", label: "Jan" },
  { value: "2", label: "Feb" },
  { value: "3", label: "Mar" },
  { value: "4", label: "Apr" },
  { value: "5", label: "May" },
  { value: "6", label: "Jun" },
  { value: "7", label: "Jul" },
  { value: "8", label: "Aug" },
  { value: "9", label: "Sep" },
  { value: "10", label: "Oct" },
  { value: "11", label: "Nov" },
  { value: "12", label: "Dec" },
];

const TYPE_PILLS = [
  { value: "", label: "All" },
  { value: "call", label: "Calls" },
  { value: "put", label: "Puts" },
];

const STATUS_PILLS = [
  { value: "", label: "All" },
  { value: "active", label: "Active" },
  { value: "closed", label: "Closed" },
  { value: "rolled", label: "Rolled" },
];

const usd = (value: number | null | undefined) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Number(value || 0));

const eur = (value: number | null | undefined) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "EUR",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Number(value || 0));

const pct = (value: number | null | undefined) => `${Number(value || 0).toFixed(2)}%`;
const netColor = (value: number) => (value >= 0 ? "text-accent-green" : "text-accent-red");

function Pills({
  options,
  value,
  onChange,
}: {
  options: { value: string; label: string }[];
  value: string;
  onChange: (next: string) => void;
}) {
  return (
    <div className="flex items-center gap-1 rounded-[var(--radius-pill)] border border-border bg-bg-card p-1">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onChange(option.value)}
          className={`rounded-[var(--radius-pill)] px-3 py-1 text-xs transition ${
            value === option.value ? "bg-accent-blue text-white" : "text-text-muted hover:text-text"
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function buildAccountOptions(accounts: BrokerAccount[], selectedAccountIds: string[]) {
  const seen = new Set<string>();
  const options = [...accounts]
    .sort((left, right) => getAccountName(left.account_id, accounts).localeCompare(getAccountName(right.account_id, accounts)))
    .map((account) => ({
      value: account.account_id,
      label: getAccountName(account.account_id, accounts),
    }))
    .filter((option) => {
      if (seen.has(option.value)) return false;
      seen.add(option.value);
      return true;
    });

  for (const accountId of selectedAccountIds) {
    if (!seen.has(accountId)) {
      options.push({ value: accountId, label: accountId });
      seen.add(accountId);
    }
  }

  return options;
}

function readInitialOptionFilters() {
  const fallbackYear = String(new Date().getFullYear());
  if (typeof window === "undefined") {
    return {
      year: fallbackYear,
      months: [] as string[],
      symbols: [] as string[],
      accountIds: [] as string[],
      type: "",
      status: "",
    };
  }

  const params = new URLSearchParams(window.location.search);
  return {
    year: params.get("year") ?? fallbackYear,
    months: params.get("month") ? params.get("month")!.split(",").filter(Boolean) : [],
    symbols: params.get("symbol") ? params.get("symbol")!.split(",").filter(Boolean) : [],
    accountIds: params.get("account_id") ? params.get("account_id")!.split(",").filter(Boolean) : [],
    type: params.get("type") ?? "",
    status: params.get("status") ?? "",
  };
}

function SummaryRow({ summary, monthly }: { summary: EconomicsSummary; monthly: EconomicsMonthlyRow[] }) {
  const avgLast12 = averageLastNExcludingZero(monthly.map((row) => row.net_income_eur), 12);
  const cards = [
    {
      label: "Premium Sold (USD)",
      value: summary.total_premium_usd ?? 0,
      prefix: "$",
      decimals: 2,
      tone: "green" as const,
    },
    {
      label: "Buybacks (USD)",
      value: summary.total_buyback_usd ?? 0,
      prefix: "$",
      decimals: 2,
      tone: "orange" as const,
    },
    {
      label: "Net Cash Flow (EUR)",
      value: summary.net_income_eur ?? 0,
      prefix: "€",
      decimals: 2,
      tone: ((summary.net_income_eur ?? 0) >= 0 ? "green" : "red") as "green" | "red",
    },
    {
      label: "Avg RoC% (USD basis)",
      value: summary.avg_roc_pct ?? 0,
      suffix: "%",
      decimals: 2,
      tone: "blue" as const,
      hint: `Annualized avg: ${pct(summary.avg_roc_annualized)}`,
    },
    {
      label: "Win Rate",
      value: summary.win_rate ?? 0,
      suffix: "%",
      decimals: 2,
      tone: "purple" as const,
      hint: `${summary.total_positions ?? 0} positions in scope`,
    },
    {
      label: "Avg Monthly Net (last 12mo)",
      value: avgLast12 ?? undefined,
      prefix: "€",
      decimals: 2,
      tone: ((avgLast12 ?? 0) >= 0 ? "green" : "red") as "green" | "red",
      hint: "Months with no activity are excluded from the average",
    },
  ];

  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-6">
      {cards.map((card, index) => (
        <Reveal key={card.label} index={index} className="h-full">
          <StatCard
            label={card.label}
            value={card.value}
            prefix={card.prefix}
            suffix={card.suffix}
            decimals={card.decimals}
            tone={card.tone}
            hint={card.hint}
          />
        </Reveal>
      ))}
    </div>
  );
}

function CoverageBanner({ coverage, hasAccountFilter }: { coverage: EconomicsCoverage; hasAccountFilter: boolean }) {
  const ratio = `${coverage.linked_positions} / ${coverage.total_positions}`;
  const details = [
    `${coverage.positions_missing_opening_sell} positions missing opening sells`,
    `${coverage.positions_missing_closing_buy} positions missing buybacks`,
    `${coverage.positions_missing_assignment_stock} assigned positions missing stock assignment movements`,
  ];

  if (coverage.positions_with_unresolved_security > 0) {
    details.push(`${coverage.positions_with_unresolved_security} positions have unresolved security mapping`);
  }
  if (hasAccountFilter && coverage.excluded_positions_linked_only_outside_account_filter > 0) {
    details.push(
      `${coverage.excluded_positions_linked_only_outside_account_filter} positions only link outside the selected account filter`,
    );
  }
  if (coverage.excluded_paper_positions > 0) {
    details.push(`${coverage.excluded_paper_positions} paper positions excluded from real totals`);
  }

  return (
    <div className="rounded-[var(--radius)] border border-accent-orange/30 bg-accent-orange/10 px-4 py-3 text-sm text-text">
      <div className="font-medium text-accent-orange">Coverage: {ratio} positions linked for current filters.</div>
      <div className="mt-1 text-text-muted">
        EUR totals reflect linked non-paper positions only. {details.join(". ")}.
      </div>
    </div>
  );
}

const CALLS_COLOR = "#a78bfa";
const PUTS_COLOR = "#22d3ee";
const NET_COLOR = "#5b61ff";

function ChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: { name?: string; value?: number; color?: string }[];
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-[10px] border border-border bg-bg-card px-3 py-2 text-xs shadow-lg">
      {label && <div className="mb-1 font-medium text-text">{label}</div>}
      {payload.map((entry, index) => (
        <div key={`${entry.name}-${index}`} className="flex items-center gap-2">
          <span className="inline-block h-2 w-2 rounded-sm" style={{ background: entry.color }} />
          <span className="text-text-muted">{entry.name}</span>
          <span className="ml-auto font-mono text-text">{eur(Number(entry.value ?? 0))}</span>
        </div>
      ))}
    </div>
  );
}

function MonthlyNetChart({ rows }: { rows: EconomicsMonthlyRow[] }) {
  if (!rows.length) return <p className="text-sm text-text-muted">No data.</p>;

  const chartData = rows.map((row) => ({
    label: row.label,
    calls_net_income_eur: row.calls_net_income_eur,
    puts_net_income_eur: row.puts_net_income_eur,
    net_income_eur: row.net_income_eur,
  }));

  return (
    <div style={{ height: 260 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={chartData} margin={{ top: 8, right: 8, bottom: 4, left: 2 }} barGap={2}>
          <CartesianGrid stroke="rgba(148,163,184,0.08)" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fill: "#8d969e", fontSize: 10 }}
            tickLine={false}
            axisLine={{ stroke: "rgba(148,163,184,0.15)" }}
            interval={0}
            angle={-30}
            textAnchor="end"
            height={48}
          />
          <YAxis
            tick={{ fill: "#8d969e", fontSize: 10 }}
            tickLine={false}
            axisLine={{ stroke: "rgba(148,163,184,0.15)" }}
            tickFormatter={(value) => eur(Number(value))}
            width={72}
          />
          <ReferenceLine y={0} stroke="rgba(148,163,184,0.35)" />
          <Tooltip cursor={{ fill: "rgba(148,163,184,0.08)" }} content={<ChartTooltip />} />
          <Legend wrapperStyle={{ fontSize: 11, color: "#8d969e" }} iconType="circle" iconSize={8} />
          <Bar dataKey="calls_net_income_eur" name="Calls Net" fill={CALLS_COLOR} radius={[3, 3, 0, 0]} maxBarSize={22} isAnimationActive={false} />
          <Bar dataKey="puts_net_income_eur" name="Puts Net" fill={PUTS_COLOR} radius={[3, 3, 0, 0]} maxBarSize={22} isAnimationActive={false} />
          <Bar dataKey="net_income_eur" name="Net" fill={NET_COLOR} radius={[3, 3, 0, 0]} maxBarSize={22} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function TypeDoughnut({ callsNet, putsNet }: { callsNet: number; putsNet: number }) {
  const callsAbs = Math.abs(callsNet);
  const putsAbs = Math.abs(putsNet);
  const total = callsAbs + putsAbs;
  const pieData = [
    { name: "Calls", value: callsAbs, raw: callsNet, color: CALLS_COLOR },
    { name: "Puts", value: putsAbs, raw: putsNet, color: PUTS_COLOR },
  ];

  return (
    <div style={{ height: 260 }} className="flex flex-col">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={total > 0 ? pieData : [{ name: "No data", value: 1, raw: 0, color: "var(--color-border)" }]}
            dataKey="value"
            nameKey="name"
            cx="50%"
            cy="50%"
            innerRadius={58}
            outerRadius={88}
            paddingAngle={total > 0 ? 2 : 0}
            stroke="none"
            isAnimationActive={false}
          >
            {(total > 0 ? pieData : [{ color: "var(--color-border)" }]).map((datum, index) => (
              <Cell key={index} fill={datum.color} />
            ))}
          </Pie>
          {total > 0 && (
            <Tooltip
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const point = payload[0].payload as { name: string; raw: number; color: string };
                return (
                  <div className="rounded-[10px] border border-border bg-bg-card px-3 py-2 text-xs shadow-lg">
                    <span className="flex items-center gap-2">
                      <span className="inline-block h-2 w-2 rounded-sm" style={{ background: point.color }} />
                      <span className="text-text-muted">{point.name}</span>
                      <span className="ml-auto font-mono text-text">{eur(point.raw)}</span>
                    </span>
                  </div>
                );
              }}
            />
          )}
          <Legend wrapperStyle={{ fontSize: 11, color: "#8d969e" }} iconType="circle" iconSize={8} />
        </PieChart>
      </ResponsiveContainer>
      <div className="flex justify-center gap-4 text-xs text-text-muted">
        <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-sm" style={{ background: CALLS_COLOR }} /> Calls {eur(callsNet)}</span>
        <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-sm" style={{ background: PUTS_COLOR }} /> Puts {eur(putsNet)}</span>
      </div>
    </div>
  );
}

type PositionColumn = {
  label: string;
  sortKey?: EconomicsSortKey;
  num?: boolean;
};

const POSITION_COLS: PositionColumn[] = [
  { label: "Symbol", sortKey: "symbol" },
  { label: "Type", sortKey: "type" },
  { label: "Strike", sortKey: "strike", num: true },
  { label: "Expiration", sortKey: "expiration" },
  { label: "Premium Sold (USD)", sortKey: "premium_usd", num: true },
  { label: "Buybacks (USD)", sortKey: "buyback_usd", num: true },
  { label: "Net Cash Flow (EUR)", sortKey: "net_income_eur", num: true },
  { label: "RoC%", sortKey: "roc_pct", num: true },
  { label: "RoC% Ann.", sortKey: "roc_annualized", num: true },
  { label: "Days Held", sortKey: "days_held", num: true },
  { label: "Status", sortKey: "status" },
  { label: "Movements" },
  { label: "Accounts" },
  { label: "Coverage" },
  { label: "Paper" },
  { label: "Warnings" },
  { label: "Opened", sortKey: "opened_at" },
];

function compareValues(left: unknown, right: unknown): number {
  const a = left ?? "";
  const b = right ?? "";
  if (typeof a === "number" && typeof b === "number") return a - b;
  const aDate = Date.parse(String(a));
  const bDate = Date.parse(String(b));
  if (!Number.isNaN(aDate) && !Number.isNaN(bDate)) return aDate - bDate;
  return String(a).localeCompare(String(b));
}

function statusBadgeClass(status: string): string {
  if (status === "rolled") return "border-accent-orange/40 bg-accent-orange/10 text-accent-orange";
  if (status === "closed") return "border-accent-blue/40 bg-accent-blue/10 text-accent-blue";
  return "border-accent-green/40 bg-accent-green/10 text-accent-green";
}

function renderLinkedAccounts(accountsForRow: string[], accounts: BrokerAccount[]) {
  if (accountsForRow.length === 0) return <span className="text-text-muted">—</span>;

  const visible = accountsForRow.slice(0, 2);
  const hiddenCount = accountsForRow.length - visible.length;
  const tooltip = accountsForRow.map((accountId) => getAccountName(accountId, accounts)).join("\n");

  return (
    <div className="flex flex-wrap justify-end gap-1" title={tooltip}>
      {visible.map((accountId) => (
        <AccountBadge key={accountId} accountId={accountId} accounts={accounts} />
      ))}
      {hiddenCount > 0 && (
        <span className="inline-block rounded-full border border-border bg-bg-input px-2 py-0.5 text-xs text-text-muted">
          +{hiddenCount}
        </span>
      )}
    </div>
  );
}

function PositionMovementsDialog({
  position,
  movements,
  accounts,
  loading,
  error,
  onClose,
  onSelect,
}: {
  position: EconomicsPosition | null;
  movements: LedgerMovement[];
  accounts: BrokerAccount[];
  loading: boolean;
  error: string | null;
  onClose: () => void;
  onSelect: (movement: LedgerMovement) => void;
}) {
  if (!position) return null;

  return (
    <div
      className="fixed inset-0 z-[190] flex items-start justify-center overflow-auto bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        className="mt-12 mb-12 w-full max-w-[720px] rounded-[var(--radius)] border border-border bg-bg-card"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Position movements"
      >
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <div>
            <h3 className="text-base font-semibold text-text">Position Movements</h3>
            <p className="mt-0.5 text-xs text-text-muted">
              {position.symbol} {position.type.toUpperCase()} {position.strike != null ? usd(position.strike) : ""}{" "}
              {position.expiration ?? ""}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-[var(--radius)] px-2 py-1 text-sm text-text-muted hover:bg-bg-hover hover:text-text"
          >
            Close
          </button>
        </div>
        <div className="px-5 py-4">
          {loading ? (
            <div className="text-sm text-text-muted">Loading linked movements…</div>
          ) : error ? (
            <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm text-accent-red">
              {error}
            </div>
          ) : movements.length === 0 ? (
            <div className="text-sm text-text-muted">No linked movements found.</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[560px] text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
                    <th className="px-3 py-2 font-medium">Date</th>
                    <th className="px-3 py-2 font-medium">Type</th>
                    <th className="px-3 py-2 text-right font-medium">Net (EUR)</th>
                    <th className="px-3 py-2 font-medium">Account</th>
                  </tr>
                </thead>
                <tbody>
                  {movements.map((movement) => (
                    <tr
                      key={movement.id}
                      className="cursor-pointer border-b border-border/60 transition-colors last:border-0 hover:bg-bg-hover/40"
                      onClick={() => onSelect(movement)}
                    >
                      <td className="px-3 py-2 font-mono">{movement.trade_date}</td>
                      <td className="px-3 py-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <span>{movement.txn_type}</span>
                        </div>
                      </td>
                      <td className={`px-3 py-2 text-right font-mono ${netColor(Number(movement.net?.eur_amount ?? 0))}`}>
                        {eur(Number(movement.net?.eur_amount ?? 0))}
                      </td>
                      <td className="px-3 py-2">{getAccountName(movement.account_id, accounts)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function PositionsDetail({
  rows,
  accounts,
  onRefresh,
}: {
  rows: EconomicsPosition[];
  accounts: BrokerAccount[];
  onRefresh: () => void;
}) {
  const [sortKey, setSortKey] = useState<EconomicsSortKey>("opened_at");
  const [dir, setDir] = useState<"asc" | "desc">("desc");
  const [showPaperPositions, setShowPaperPositions] = useState(false);
  const [loadingPositionId, setLoadingPositionId] = useState<string | null>(null);
  const [togglingPaperPositionId, setTogglingPaperPositionId] = useState<string | null>(null);
  const [paperToggleError, setPaperToggleError] = useState<string | null>(null);
  const [movementsError, setMovementsError] = useState<string | null>(null);
  const [positionMovements, setPositionMovements] = useState<LedgerMovement[]>([]);
  const [selectedPosition, setSelectedPosition] = useState<EconomicsPosition | null>(null);
  const [selectedMovement, setSelectedMovement] = useState<LedgerMovement | null>(null);

  const visibleRows = useMemo(
    () => rows.filter((position) => showPaperPositions || !position.is_paper),
    [rows, showPaperPositions],
  );

  const sorted = useMemo(
    () =>
      [...visibleRows].sort((left, right) => {
        const comparison = compareValues(left[sortKey], right[sortKey]);
        return dir === "asc" ? comparison : -comparison;
      }),
    [dir, sortKey, visibleRows],
  );

  function onSort(key?: EconomicsSortKey) {
    if (!key) return;
    if (key === sortKey) setDir((current) => (current === "asc" ? "desc" : "asc"));
    else {
      setSortKey(key);
      setDir(key === "opened_at" ? "desc" : "asc");
    }
  }

  async function handleOpenMovements(position: EconomicsPosition) {
    if (!position.position_id || position.linked_movement_count === 0) return;
    setLoadingPositionId(position.position_id);
    setMovementsError(null);
    try {
      const response = await getMovements({
        option_position_id: position.position_id,
        limit: Math.max(position.linked_movement_count, 10),
      });
      const movements = response.movements ?? [];
      if (position.linked_movement_count === 1 && movements.length === 1) {
        setSelectedPosition(null);
        setPositionMovements([]);
        setSelectedMovement(movements[0]);
      } else {
        setSelectedMovement(null);
        setSelectedPosition(position);
        setPositionMovements(movements);
      }
    } catch (error) {
      setSelectedMovement(null);
      setSelectedPosition(position);
      setPositionMovements([]);
      setMovementsError(error instanceof Error ? error.message : "Failed to load linked movements.");
    } finally {
      setLoadingPositionId(null);
    }
  }

  async function handleTogglePaper(position: EconomicsPosition) {
    if (!position.position_id) return;
    setTogglingPaperPositionId(position.position_id);
    setPaperToggleError(null);
    try {
      await setPositionPaper(position.symbol, position.position_id, !position.is_paper);
      await onRefresh();
    } catch (error) {
      setPaperToggleError(error instanceof Error ? error.message : "Failed to update paper position.");
    } finally {
      setTogglingPaperPositionId(null);
    }
  }

  return (
    <>
      {paperToggleError && (
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-3 py-2 text-sm text-accent-red">
          {paperToggleError}
        </div>
      )}

      <details open className="surface overflow-hidden">
        <summary className="flex cursor-pointer items-center justify-between px-4 py-3">
          <span className="text-base font-semibold">Positions Detail</span>
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-2 text-xs font-normal text-text-muted">
              <input
                type="checkbox"
                checked={showPaperPositions}
                onChange={(event) => setShowPaperPositions(event.target.checked)}
                onClick={(event) => event.stopPropagation()}
                className="accent-accent-purple"
              />
              <span>Show paper positions</span>
            </label>
            <span className="rounded-[var(--radius-pill)] bg-bg-input px-2 py-0.5 text-xs text-text-muted">
              {sorted.length} rows
            </span>
          </div>
        </summary>
        <div className="overflow-x-auto border-t border-border">
        <table className="w-full min-w-[1380px] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
              {POSITION_COLS.map((column) => (
                <th
                  key={column.label}
                  onClick={() => onSort(column.sortKey)}
                  className={`${column.sortKey ? "cursor-pointer select-none hover:text-text" : ""} px-3 py-2 font-medium ${column.num ? "text-right" : ""}`}
                >
                  {column.label}
                  {column.sortKey && (
                    <span className="ml-1">{sortKey === column.sortKey ? (dir === "asc" ? "▲" : "▼") : ""}</span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 && (
              <tr>
                <td colSpan={POSITION_COLS.length} className="px-3 py-6 text-center text-text-muted">
                  No positions match the selected filters.
                </td>
              </tr>
            )}
            {sorted.map((position, index) => {
              const warnings = position.warnings?.filter(Boolean) ?? [];
              return (
                <tr key={position.position_id ?? index} className="border-b border-border/60 transition-colors last:border-0 hover:bg-bg-hover/40">
                  <td className="px-3 py-2 font-semibold">{position.symbol}</td>
                  <td className="px-3 py-2">
                    <span
                      className={`inline-block rounded-[var(--radius-pill)] border px-2 py-0.5 text-xs ${
                        position.type === "call"
                          ? "border-accent-green/40 bg-accent-green/10 text-accent-green"
                          : "border-accent-blue/40 bg-accent-blue/10 text-accent-blue"
                      }`}
                    >
                      {position.type.toUpperCase()}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right font-mono">{position.strike != null ? usd(position.strike) : "—"}</td>
                  <td className="px-3 py-2">{position.expiration || "—"}</td>
                  <td className="px-3 py-2 text-right font-mono">{usd(position.premium_usd)}</td>
                  <td className="px-3 py-2 text-right font-mono">{usd(position.buyback_usd)}</td>
                  <td className={`px-3 py-2 text-right font-mono ${netColor(position.net_income_eur)}`}>{eur(position.net_income_eur)}</td>
                  <td className="px-3 py-2 text-right font-mono">{position.roc_pct != null ? pct(position.roc_pct) : "—"}</td>
                  <td className="px-3 py-2 text-right font-mono">{position.roc_annualized != null ? pct(position.roc_annualized) : "—"}</td>
                  <td className="px-3 py-2 text-right font-mono">{position.days_held ?? "—"}</td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      <span className={`inline-block rounded-[var(--radius-pill)] border px-2 py-0.5 text-xs ${statusBadgeClass(position.status)}`}>
                        {position.status}
                      </span>
                      {position.is_paper && <PaperBadge />}
                      {position.close_reason && position.status === "closed" && (
                        <span className="inline-block rounded-[var(--radius-pill)] border border-border bg-bg-input px-2 py-0.5 text-xs text-text-muted">
                          {position.close_reason}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-3 py-2">
                    {position.linked_movement_count === 0 || !position.position_id ? (
                      <span className="text-text-muted">—</span>
                    ) : (
                      <button
                        type="button"
                        onClick={() => void handleOpenMovements(position)}
                        disabled={loadingPositionId === position.position_id}
                        className="rounded-[var(--radius-pill)] border border-accent-blue/40 bg-accent-blue/10 px-2 py-0.5 text-xs text-accent-blue hover:bg-accent-blue/15 disabled:opacity-50"
                      >
                        {loadingPositionId === position.position_id
                          ? "Loading…"
                          : `${position.linked_movement_count} movement${position.linked_movement_count === 1 ? "" : "s"}`}
                      </button>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right">{renderLinkedAccounts(position.linked_accounts ?? [], accounts)}</td>
                  <td className="px-3 py-2"><CoverageStatusBadge status={position.coverage_status} /></td>
                  <td className="px-3 py-2">
                    {position.position_id ? (
                      <button
                        type="button"
                        onClick={() => void handleTogglePaper(position)}
                        disabled={togglingPaperPositionId === position.position_id}
                        className="rounded-[var(--radius-pill)] border border-accent-purple/40 bg-accent-purple/10 px-2 py-0.5 text-xs text-accent-purple hover:bg-accent-purple/15 disabled:opacity-50"
                      >
                        {togglingPaperPositionId === position.position_id
                          ? "Saving…"
                          : position.is_paper
                            ? "Unmark Paper"
                            : "Mark as Paper"}
                      </button>
                    ) : (
                      <span className="text-text-muted">—</span>
                    )}
                  </td>
                  <td className="px-3 py-2"><WarningBadge warnings={warnings} /></td>
                  <td className="px-3 py-2 font-mono">{(position.opened_at || "").slice(0, 10) || "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        </div>
      </details>

      <PositionMovementsDialog
        position={selectedPosition}
        movements={positionMovements}
        accounts={accounts}
        loading={loadingPositionId === selectedPosition?.position_id}
        error={movementsError}
        onClose={() => {
          setSelectedPosition(null);
          setPositionMovements([]);
          setMovementsError(null);
        }}
        onSelect={(movement) => setSelectedMovement(movement)}
      />
      {selectedMovement && (
        <MovementDetailDialog
          movement={selectedMovement}
          accounts={accounts}
          onClose={() => setSelectedMovement(null)}
          onRefresh={() => {
            void onRefresh();
            setSelectedMovement(null);
            setSelectedPosition(null);
            setPositionMovements([]);
          }}
        />
      )}
    </>
  );
}

function MonthlySection({ rows }: { rows: EconomicsMonthlyRow[] }) {
  return (
    <div className="surface overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3">
        <h2 className="text-base font-semibold">Monthly P&amp;L</h2>
        <span className="rounded-[var(--radius-pill)] bg-bg-input px-2 py-0.5 text-xs text-text-muted">
          {rows.length} months
        </span>
      </div>
      <div className="overflow-x-auto border-t border-border">
        <table className="w-full min-w-[760px] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
              <th className="px-3 py-2 font-medium">Month</th>
              <th className="px-3 py-2 text-right font-medium">Premium Sold (USD)</th>
              <th className="px-3 py-2 text-right font-medium">Buybacks (USD)</th>
              <th className="px-3 py-2 text-right font-medium">Net Cash Flow (EUR)</th>
              <th className="px-3 py-2 text-right font-medium"># Positions</th>
              <th className="px-3 py-2 text-right font-medium">Avg RoC% (Ann.)</th>
              <th className="px-3 py-2 text-right font-medium">Calls</th>
              <th className="px-3 py-2 text-right font-medium">Puts</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={8} className="px-3 py-6 text-center text-text-muted">
                  No positions match the selected filters.
                </td>
              </tr>
            )}
            {rows.map((row) => (
              <tr key={`${row.year}-${row.month}`} className="border-b border-border/60 transition-colors last:border-0 hover:bg-bg-hover/40">
                <td className="px-3 py-2">{row.label}</td>
                <td className="px-3 py-2 text-right font-mono">{usd(row.total_premium_usd)}</td>
                <td className="px-3 py-2 text-right font-mono">{usd(row.total_buyback_usd)}</td>
                <td className={`px-3 py-2 text-right font-mono ${netColor(row.net_income_eur)}`}>{eur(row.net_income_eur)}</td>
                <td className="px-3 py-2 text-right font-mono">{row.positions_count}</td>
                <td className="px-3 py-2 text-right font-mono">{pct(row.avg_roc_annualized ?? row.avg_roc_pct)}</td>
                <td className="px-3 py-2 text-right font-mono">{row.calls_count}</td>
                <td className="px-3 py-2 text-right font-mono">{row.puts_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

type BySymbolKey = keyof Pick<
  EconomicsBySymbolRow,
  "symbol" | "total_premium_usd" | "total_buyback_usd" | "net_income_eur" | "positions_count" | "avg_roc_annualized"
>;

const BY_SYMBOL_COLS: { key: BySymbolKey; label: string; num?: boolean }[] = [
  { key: "symbol", label: "Symbol" },
  { key: "total_premium_usd", label: "Premium Sold (USD)", num: true },
  { key: "total_buyback_usd", label: "Buybacks (USD)", num: true },
  { key: "net_income_eur", label: "Net Cash Flow (EUR)", num: true },
  { key: "positions_count", label: "# Positions", num: true },
  { key: "avg_roc_annualized", label: "Avg RoC% (Ann.)", num: true },
];

function BySymbolSection({ rows }: { rows: EconomicsBySymbolRow[] }) {
  const [sortKey, setSortKey] = useState<BySymbolKey>("net_income_eur");
  const [dir, setDir] = useState<"asc" | "desc">("desc");

  const sorted = useMemo(
    () =>
      [...rows].sort((left, right) => {
        const comparison = compareValues(left[sortKey], right[sortKey]);
        return dir === "asc" ? comparison : -comparison;
      }),
    [dir, rows, sortKey],
  );

  function onSort(key: BySymbolKey) {
    if (key === sortKey) setDir((current) => (current === "asc" ? "desc" : "asc"));
    else {
      setSortKey(key);
      setDir(key === "symbol" ? "asc" : "desc");
    }
  }

  return (
    <div className="surface overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3">
        <h2 className="text-base font-semibold">By Symbol</h2>
        <span className="rounded-[var(--radius-pill)] bg-bg-input px-2 py-0.5 text-xs text-text-muted">
          {rows.length} symbols
        </span>
      </div>
      <div className="overflow-x-auto border-t border-border">
        <table className="w-full min-w-[700px] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
              {BY_SYMBOL_COLS.map((column) => (
                <th
                  key={column.key}
                  onClick={() => onSort(column.key)}
                  className={`cursor-pointer select-none px-3 py-2 font-medium hover:text-text ${column.num ? "text-right" : ""}`}
                >
                  {column.label}
                  <span className="ml-1">{sortKey === column.key ? (dir === "asc" ? "▲" : "▼") : ""}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-text-muted">
                  No symbol-level economics available.
                </td>
              </tr>
            )}
            {sorted.map((row) => (
              <tr key={row.symbol} className="border-b border-border/60 transition-colors last:border-0 hover:bg-bg-hover/40">
                <td className="px-3 py-2 font-semibold">{row.symbol}</td>
                <td className="px-3 py-2 text-right font-mono">{usd(row.total_premium_usd)}</td>
                <td className="px-3 py-2 text-right font-mono">{usd(row.total_buyback_usd)}</td>
                <td className={`px-3 py-2 text-right font-mono ${netColor(row.net_income_eur)}`}>{eur(row.net_income_eur)}</td>
                <td className="px-3 py-2 text-right font-mono">{row.positions_count}</td>
                <td className="px-3 py-2 text-right font-mono">{pct(row.avg_roc_annualized ?? row.avg_roc_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function EconomicsView({
  basePath = "/economics",
  title = "Economics",
  description = "Premium, buybacks, net income, and options RoC analytics across all symbols.",
}: {
  basePath?: string;
  title?: string;
  description?: string;
}) {
  const [initialFilters] = useState(readInitialOptionFilters);
  const [year, setYear] = useState<string>(initialFilters.year);
  const [months, setMonths] = useState<string[]>(initialFilters.months);
  const [symbols, setSymbols] = useState<string[]>(initialFilters.symbols);
  const [accountIds, setAccountIds] = useState<string[]>(initialFilters.accountIds);
  const [type, setType] = useState<string>(initialFilters.type);
  const [status, setStatus] = useState<string>(initialFilters.status);
  const [accounts, setAccounts] = useState<BrokerAccount[]>([]);
  const [data, setData] = useState<EconomicsReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listAccounts()
      .then((response) => setAccounts(response.accounts ?? []))
      .catch(() => setAccounts([]));
  }, []);

  const fetchData = useCallback(async () => {
    setError(null);
    setLoading(true);

    const params = new URLSearchParams();
    if (year) params.set("year", year);
    if (months.length) params.set("month", months.join(","));
    if (symbols.length) params.set("symbol", symbols.join(","));
    if (accountIds.length) params.set("account_id", accountIds.join(","));
    if (type) params.set("type", type);
    if (status) params.set("status", status);

    const queryString = params.toString();
    window.history.replaceState({}, "", queryString ? `${basePath}?${queryString}` : basePath);

    try {
      const response = await fetch(`/api/economics${queryString ? `?${queryString}` : ""}`);
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
      setData(body as EconomicsReport);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load economics data.");
    } finally {
      setLoading(false);
    }
  }, [accountIds, basePath, months, status, symbols, type, year]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void fetchData();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [fetchData]);

  const yearOptions = data?.filters.years ?? [];
  const symbolOptions = (data?.filters.symbols ?? []).map((symbol) => ({ value: symbol, label: symbol }));
  const accountOptions = useMemo(() => buildAccountOptions(accounts, accountIds), [accountIds, accounts]);
  const coverage = data?.summary.coverage;

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        <p className="mt-1 text-sm text-text-muted">{description}</p>
      </div>

      <EconomicsTabs />

      {error && (
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
          ⚠️ {error}
        </div>
      )}

      {data && <SummaryRow summary={data.summary} monthly={data.monthly} />}

      {coverage && <CoverageBanner coverage={coverage} hasAccountFilter={accountIds.length > 0} />}

      <div className="surface p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-base font-semibold">Filters</h2>
          <span className="rounded-[var(--radius-pill)] bg-bg-input px-2 py-0.5 text-xs text-text-muted">
            {data?.summary.total_positions ?? 0} positions
          </span>
        </div>
        <div className="flex flex-wrap items-end gap-4">
          <div className="flex flex-col gap-1">
            <span className="text-xs text-text-muted">Year</span>
            <select
              value={year}
              onChange={(event) => setYear(event.target.value)}
              className="rounded-[var(--radius)] border border-border bg-bg-input px-3 py-1.5 text-sm text-text"
            >
              <option value="">All Years</option>
              {yearOptions.map((option) => (
                <option key={option} value={String(option)}>
                  {option}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs text-text-muted">Months</span>
            <MultiSelect options={MONTHS} selected={months} onChange={setMonths} allLabel="All Months" />
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs text-text-muted">Symbols</span>
            <MultiSelect options={symbolOptions} selected={symbols} onChange={setSymbols} allLabel="All Symbols" />
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs text-text-muted">Account</span>
            <MultiSelect options={accountOptions} selected={accountIds} onChange={setAccountIds} allLabel="All Accounts" />
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs text-text-muted">Type</span>
            <Pills options={TYPE_PILLS} value={type} onChange={setType} />
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs text-text-muted">Status</span>
            <Pills options={STATUS_PILLS} value={status} onChange={setStatus} />
          </div>
        </div>
      </div>

      {loading && !data && (
        <div className="surface px-4 py-12 text-center text-text-muted">
          Loading economics data…
        </div>
      )}

      {data && (
        <>
          <MonthlySection rows={data.monthly} />
          <BySymbolSection rows={data.by_symbol} />

          <div className="surface p-4">
            <h2 className="mb-4 text-base font-semibold">Charts</h2>
            <div className="grid gap-6 lg:grid-cols-2">
              <div>
                <h3 className="mb-2 text-sm font-medium text-text-muted">Monthly Net Cash Flow (EUR)</h3>
                <MonthlyNetChart rows={data.monthly} />
              </div>
              <div>
                <h3 className="mb-2 text-sm font-medium text-text-muted">Calls vs Puts Net Cash Flow (EUR)</h3>
                <TypeDoughnut callsNet={data.by_type.calls.net_income_eur} putsNet={data.by_type.puts.net_income_eur} />
              </div>
            </div>
          </div>

          <PositionsDetail rows={data.positions} accounts={accounts} onRefresh={fetchData} />
        </>
      )}
    </div>
  );
}
