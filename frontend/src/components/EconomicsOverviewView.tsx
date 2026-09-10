"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import EconomicsTabs from "@/components/EconomicsTabs";
import MultiSelect from "@/components/MultiSelect";
import Reveal from "@/components/Reveal";
import StatCard from "@/components/StatCard";
import { getAccountName } from "@/lib/accountDisplay";
import { averageLastNExcludingZero } from "@/lib/format";
import { listAccounts } from "@/lib/portfolio-api";
import type { BrokerAccount } from "@/types/portfolio";
import type {
  EconomicsAggregatedBySymbolRow,
  EconomicsAggregatedMonthlyRow,
  EconomicsAggregatedReport,
  EconomicsAggregatedSummary,
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

const eur = (value: number | null | undefined) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "EUR",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Number(value || 0));

const pct = (value: number | null | undefined) => `${Number(value || 0).toFixed(1)}%`;
const signedColor = (value: number) => (value >= 0 ? "text-accent-green" : "text-accent-red");

function formatMonthLabel(value: string) {
  const [year, month] = value.split("-");
  const date = new Date(Number(year), Number(month) - 1, 1);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("en-US", { month: "short", year: "numeric" }).format(date);
}

function buildAccountOptions(accounts: BrokerAccount[], selectedAccountIds: string[]) {
  const seen = new Set<string>();
  const options = [...accounts]
    .sort((left, right) => getAccountName(left.account_id, accounts).localeCompare(getAccountName(right.account_id, accounts)))
    .map((account) => ({ value: account.account_id, label: getAccountName(account.account_id, accounts) }))
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

function readInitialOverviewFilters() {
  const fallbackYear = String(new Date().getFullYear());
  if (typeof window === "undefined") {
    return {
      year: fallbackYear,
      months: [] as string[],
      symbols: [] as string[],
      accountIds: [] as string[],
    };
  }

  const params = new URLSearchParams(window.location.search);
  return {
    year: params.get("year") ?? fallbackYear,
    months: params.get("month") ? params.get("month")!.split(",").filter(Boolean) : [],
    symbols: params.get("symbol") ? params.get("symbol")!.split(",").filter(Boolean) : [],
    accountIds: params.get("account_id") ? params.get("account_id")!.split(",").filter(Boolean) : [],
  };
}

function SummaryRow({ summary, monthly }: { summary: EconomicsAggregatedSummary; monthly: EconomicsAggregatedMonthlyRow[] }) {
  const coverage = summary.options_coverage ?? {
    linked_positions: 0,
    total_positions: 0,
    linked_ratio: 0,
  };
  const coverageDisplay = `${coverage.linked_positions ?? 0}/${coverage.total_positions ?? 0}`;
  const avgLast12 = averageLastNExcludingZero(monthly.map((row) => row.combined_net_eur), 12);
  const yoc = summary.portfolio_yoc_pct;

  const cards = [
    {
      label: "Options Net Cash Flow",
      value: summary.options_net_eur ?? 0,
      prefix: "€",
      decimals: 2,
      tone: ((summary.options_net_eur ?? 0) >= 0 ? "blue" : "red") as "blue" | "red",
      hint: `Coverage: ${coverageDisplay} linked`,
    },
    {
      label: "Dividends Net Cash Flow",
      value: summary.dividends_net_eur ?? 0,
      prefix: "€",
      decimals: 2,
      tone: ((summary.dividends_net_eur ?? 0) >= 0 ? "green" : "red") as "green" | "red",
    },
    {
      label: "Combined Cash Flow",
      value: summary.combined_net_eur ?? 0,
      prefix: "€",
      decimals: 2,
      tone: ((summary.combined_net_eur ?? 0) >= 0 ? "purple" : "red") as "purple" | "red",
    },
    {
      label: "Avg Monthly Net (last 12mo)",
      value: avgLast12 ?? undefined,
      prefix: "€",
      decimals: 2,
      tone: ((avgLast12 ?? 0) >= 0 ? "green" : "red") as "green" | "red",
      hint: "Months with no activity are excluded from the average",
    },
    {
      label: "Portfolio Yield on Cost",
      value: yoc ?? undefined,
      suffix: "%",
      decimals: 2,
      tone: "green" as const,
      hint: "Annualized dividends on current cost basis, held positions only",
    },
    {
      label: "Option Positions in Scope",
      value: summary.total_option_positions ?? 0,
      decimals: 0,
      tone: "orange" as const,
    },
    {
      label: "Options Coverage",
      display: coverageDisplay,
      tone: (coverage.linked_positions === coverage.total_positions ? "green" : "orange") as "green" | "orange",
      hint: `${pct((coverage.linked_ratio ?? 0) * 100)} linked`,
    },
  ];

  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4 xl:grid-cols-7">
      {cards.map((card, index) => (
        <Reveal key={card.label} index={index} className="h-full">
          <StatCard
            label={card.label}
            value={"value" in card ? card.value : undefined}
            display={"display" in card ? card.display : undefined}
            prefix={card.prefix}
            decimals={card.decimals}
            suffix={"suffix" in card ? card.suffix : undefined}
            tone={card.tone}
            hint={card.hint}
          />
        </Reveal>
      ))}
    </div>
  );
}

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

function OverviewBarChart({
  rows,
  dataKey,
  title,
  fill,
}: {
  rows: EconomicsAggregatedMonthlyRow[];
  dataKey: "options_net_eur" | "dividends_net_eur";
  title: string;
  fill: string;
}) {
  if (!rows.length) return <p className="text-sm text-text-muted">No data.</p>;

  const chartData = rows.map((row) => ({
    label: formatMonthLabel(row.month),
    value: row[dataKey],
  }));

  return (
    <div>
      <h3 className="mb-2 text-sm font-medium text-text-muted">{title}</h3>
      <div style={{ height: 260 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chartData} margin={{ top: 8, right: 8, bottom: 4, left: 2 }}>
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
            <Tooltip content={<ChartTooltip />} cursor={{ fill: "rgba(148,163,184,0.08)" }} />
            <Bar dataKey="value" name={title} fill={fill} radius={[3, 3, 0, 0]} maxBarSize={28} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function MonthlySection({ rows }: { rows: EconomicsAggregatedMonthlyRow[] }) {
  return (
    <div className="surface overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3">
        <h2 className="text-base font-semibold">Unified Monthly Table</h2>
        <span className="rounded-[var(--radius-pill)] bg-bg-input px-2 py-0.5 text-xs text-text-muted">
          {rows.length} months
        </span>
      </div>
      <div className="overflow-x-auto border-t border-border">
        <table className="w-full min-w-[760px] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
              <th className="px-3 py-2 font-medium">Month</th>
              <th className="px-3 py-2 text-right font-medium">Options Net (EUR)</th>
              <th className="px-3 py-2 text-right font-medium">Dividends Net (EUR)</th>
              <th className="px-3 py-2 text-right font-medium">Combined Net (EUR)</th>
              <th className="px-3 py-2 text-right font-medium">Option Positions</th>
              <th className="px-3 py-2 text-right font-medium">Dividend Events</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-text-muted">
                  No economics rows match the selected filters.
                </td>
              </tr>
            )}
            {rows.map((row) => (
              <tr key={row.month} className="border-b border-border/60 transition-colors last:border-0 hover:bg-bg-hover/40">
                <td className="px-3 py-2">{formatMonthLabel(row.month)}</td>
                <td className={`px-3 py-2 text-right font-mono ${signedColor(row.options_net_eur)}`}>{eur(row.options_net_eur)}</td>
                <td className={`px-3 py-2 text-right font-mono ${signedColor(row.dividends_net_eur)}`}>{eur(row.dividends_net_eur)}</td>
                <td className={`px-3 py-2 text-right font-mono ${signedColor(row.combined_net_eur)}`}>{eur(row.combined_net_eur)}</td>
                <td className="px-3 py-2 text-right font-mono">{row.option_positions}</td>
                <td className="px-3 py-2 text-right font-mono">{row.dividend_events}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

type BySymbolKey = keyof Pick<
  EconomicsAggregatedBySymbolRow,
  "symbol" | "options_net_eur" | "dividends_net_eur" | "combined_net_eur" | "option_positions" | "dividend_events"
>;

const BY_SYMBOL_COLS: { key: BySymbolKey; label: string; num?: boolean }[] = [
  { key: "symbol", label: "Symbol" },
  { key: "options_net_eur", label: "Options Net (EUR)", num: true },
  { key: "dividends_net_eur", label: "Dividends Net (EUR)", num: true },
  { key: "combined_net_eur", label: "Combined Net (EUR)", num: true },
  { key: "option_positions", label: "Option Positions", num: true },
  { key: "dividend_events", label: "Dividend Events", num: true },
];

function compareValues(left: unknown, right: unknown): number {
  const a = left ?? "";
  const b = right ?? "";
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b));
}

function BySymbolSection({ rows }: { rows: EconomicsAggregatedBySymbolRow[] }) {
  const [sortKey, setSortKey] = useState<BySymbolKey>("combined_net_eur");
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
        <h2 className="text-base font-semibold">By-Symbol Contribution</h2>
        <span className="rounded-[var(--radius-pill)] bg-bg-input px-2 py-0.5 text-xs text-text-muted">
          {rows.length} symbols
        </span>
      </div>
      <div className="overflow-x-auto border-t border-border">
        <table className="w-full min-w-[820px] text-sm">
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
                <td className={`px-3 py-2 text-right font-mono ${signedColor(row.options_net_eur)}`}>{eur(row.options_net_eur)}</td>
                <td className={`px-3 py-2 text-right font-mono ${signedColor(row.dividends_net_eur)}`}>{eur(row.dividends_net_eur)}</td>
                <td className={`px-3 py-2 text-right font-mono ${signedColor(row.combined_net_eur)}`}>{eur(row.combined_net_eur)}</td>
                <td className="px-3 py-2 text-right font-mono">{row.option_positions}</td>
                <td className="px-3 py-2 text-right font-mono">{row.dividend_events}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function EconomicsOverviewView() {
  const [initialFilters] = useState(readInitialOverviewFilters);
  const [year, setYear] = useState<string>(initialFilters.year);
  const [months, setMonths] = useState<string[]>(initialFilters.months);
  const [symbols, setSymbols] = useState<string[]>(initialFilters.symbols);
  const [accountIds, setAccountIds] = useState<string[]>(initialFilters.accountIds);
  const [accounts, setAccounts] = useState<BrokerAccount[]>([]);
  const [data, setData] = useState<EconomicsAggregatedReport | null>(null);
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

    const queryString = params.toString();
    window.history.replaceState({}, "", queryString ? `/economics?${queryString}` : "/economics");

    try {
      const response = await fetch(`/api/economics/overview${queryString ? `?${queryString}` : ""}`);
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
      const report = body as EconomicsAggregatedReport;
      const withTotals: EconomicsAggregatedReport = {
        ...report,
        summary: {
          ...report.summary,
          dividends_net_eur: report.summary.dividends_total_net_eur ?? report.summary.dividends_net_eur,
        },
        monthly: report.monthly.map((row) => ({
          ...row,
          dividends_net_eur: row.dividends_total_net_eur ?? row.dividends_net_eur,
        })),
        by_symbol: report.by_symbol.map((row) => ({
          ...row,
          dividends_net_eur: row.dividends_total_net_eur ?? row.dividends_net_eur,
        })),
      };
      setData(withTotals);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load economics overview.");
    } finally {
      setLoading(false);
    }
  }, [accountIds, months, symbols, year]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void fetchData();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [fetchData]);

  const yearOptions = data?.filters.years ?? [];
  const symbolOptions = (data?.filters.symbols ?? []).map((symbol) => ({ value: symbol, label: symbol }));
  const accountOptions = useMemo(() => buildAccountOptions(accounts, accountIds), [accountIds, accounts]);
  const coverage = data?.summary.options_coverage;

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Economics Overview</h1>
        <p className="mt-1 text-sm text-text-muted">
          EUR-based options and dividends cash-flow trends across your current filter scope.
        </p>
      </div>

      <EconomicsTabs />

      {error && (
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
          ⚠️ {error}
        </div>
      )}

      {data && <SummaryRow summary={data.summary} monthly={data.monthly} />}

      {(coverage || accountIds.length > 0) && (
        <div className="rounded-[var(--radius)] border border-border bg-bg-card px-4 py-3 text-sm text-text-muted">
          {coverage && (
            <div className="font-medium text-text">
              Coverage: {coverage.linked_positions} / {coverage.total_positions} linked positions.
            </div>
          )}
          {accountIds.length > 0 && (
            <div className={coverage ? "mt-1" : ""}>
              Unlinked positions excluded from account-scoped options totals.
            </div>
          )}
        </div>
      )}

      <div className="surface p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-base font-semibold">Filters</h2>
          <span className="rounded-[var(--radius-pill)] bg-bg-input px-2 py-0.5 text-xs text-text-muted">
            {data?.summary.total_symbols ?? 0} symbols in scope
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
        </div>
      </div>

      {loading && !data && (
        <div className="surface px-4 py-12 text-center text-text-muted">
          Loading economics overview…
        </div>
      )}

      {data && (
        <>
          <MonthlySection rows={data.monthly} />
          <BySymbolSection rows={data.by_symbol} />

          <div className="surface p-4">
            <h2 className="mb-4 text-base font-semibold">Charts</h2>
            <div className="grid gap-6 lg:grid-cols-2">
              <OverviewBarChart rows={data.monthly} dataKey="options_net_eur" title="Monthly Options Net (EUR)" fill="#5b61ff" />
              <OverviewBarChart rows={data.monthly} dataKey="dividends_net_eur" title="Monthly Dividends Net (EUR)" fill="#00c493" />
            </div>
          </div>
        </>
      )}
    </div>
  );
}
