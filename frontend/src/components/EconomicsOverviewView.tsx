"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
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
  InvestedCapitalReport,
  InvestedCapitalYearlyRow,
  ScripValuationStatus,
} from "@/types/economics";

const YEAR_MONTH_CAPTION = "Shows all years · filtered by symbol/account only, not by Year/Month.";
const LINE_COLORS = ["#5b61ff", "#00c493", "#ff9416", "#c084fc", "#38bdf8", "#f43f5e"];

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
  }).format(Number(value ?? 0));

const signedColor = (value: number) => (value >= 0 ? "text-accent-green" : "text-accent-red");

type OverviewDividendCoverage = {
  dividends_total_is_partial?: boolean;
  total_dividends_is_partial?: boolean;
  dividends_scrip_valuation_status?: ScripValuationStatus;
  scrip_valuation_status?: ScripValuationStatus;
  scrip_events_total?: number;
  scrip_events_valued?: number;
  scrip_events_unvalued?: number;
};

function dividendsTotal(row: { dividends_total_net_eur?: number; dividends_net_eur: number }) {
  return row.dividends_total_net_eur ?? row.dividends_net_eur;
}

function dividendsCash(row: { dividends_cash_net_eur?: number; dividends_net_eur: number }) {
  return row.dividends_cash_net_eur ?? row.dividends_net_eur;
}

function isPartial(row: OverviewDividendCoverage) {
  return row.dividends_total_is_partial ?? row.total_dividends_is_partial ?? false;
}

function overviewCoverageTitle(row: OverviewDividendCoverage) {
  const status = row.dividends_scrip_valuation_status ?? row.scrip_valuation_status;
  if (status === "NOT_APPLICABLE") return "No scrip dividend events in scope";
  return `${row.scrip_events_valued ?? 0} of ${row.scrip_events_total ?? 0} scrip events valued${
    row.scrip_events_unvalued ? `; ${row.scrip_events_unvalued} unavailable` : ""
  }`;
}

function PartialBadge({ coverage }: { coverage: OverviewDividendCoverage }) {
  if (!isPartial(coverage)) return null;
  return (
    <span
      className="rounded-[var(--radius-pill)] border border-accent-orange/40 bg-accent-orange/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-accent-orange"
      title={overviewCoverageTitle(coverage)}
    >
      Partial
    </span>
  );
}

function NullableAmount({ value }: { value: number | null | undefined }) {
  return value == null || !Number.isFinite(value) ? <span className="text-text-muted">—</span> : <>{eur(value)}</>;
}

function formatMonthLabel(value: string) {
  const [year, month] = value.split("-");
  const date = new Date(Number(year), Number(month) - 1, 1);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("en-US", { month: "short", year: "numeric" }).format(date);
}

function parseYearMonth(value: string) {
  const [yearStr, monthStr] = value.split("-");
  return { year: Number(yearStr), month: Number(monthStr) };
}

type PassiveIncomeYoyRow = {
  year: number;
  priorYear: number;
  monthsCompared: number;
  thisYearNet: number;
  priorYearNet: number;
  pctChange: number | null;
  isPartialYear: boolean;
};

/** Number of fully-elapsed calendar months in `year` as of `now` (0-12). */
function completeMonthsForYear(year: number, now: Date): number {
  if (year > now.getFullYear()) return 0;
  if (year < now.getFullYear()) return 12;
  return now.getMonth();
}

function rowsThroughMonth(rows: EconomicsAggregatedMonthlyRow[], year: number, monthLimit: number) {
  return rows.filter((row) => {
    const { year: rowYear, month } = parseYearMonth(row.month);
    return rowYear === year && month <= monthLimit;
  });
}

function sumCombinedNet(rows: EconomicsAggregatedMonthlyRow[]) {
  return rows.reduce((total, row) => total + row.combined_net_eur, 0);
}

/**
 * Builds year-over-year total passive income (dividends net + scrip + options)
 * growth rows, most recent year first. Mirrors DividendsView's computeYoyGrowth
 * but is driven by the combined_net_eur field (dividends total + options net).
 */
function computePassiveIncomeYoy(rows: EconomicsAggregatedMonthlyRow[]): PassiveIncomeYoyRow[] {
  const now = new Date();
  const years = Array.from(new Set(rows.map((row) => parseYearMonth(row.month).year))).sort((a, b) => b - a);
  const result: PassiveIncomeYoyRow[] = [];
  for (const year of years) {
    const priorYear = year - 1;
    if (!years.includes(priorYear)) continue;
    const monthsCompared = Math.min(completeMonthsForYear(year, now), completeMonthsForYear(priorYear, now));
    if (monthsCompared <= 0) continue;
    const thisYearRows = rowsThroughMonth(rows, year, monthsCompared);
    const priorYearRows = rowsThroughMonth(rows, priorYear, monthsCompared);
    const thisYearNet = sumCombinedNet(thisYearRows);
    const priorYearNet = sumCombinedNet(priorYearRows);
    const pctChange = priorYearNet !== 0 ? ((thisYearNet - priorYearNet) / Math.abs(priorYearNet)) * 100 : null;
    result.push({
      year,
      priorYear,
      monthsCompared,
      thisYearNet,
      priorYearNet,
      pctChange,
      isPartialYear: monthsCompared < 12,
    });
  }
  return result;
}

function PassiveIncomeYoySection({ rows }: { rows: EconomicsAggregatedMonthlyRow[] }) {
  const growth = useMemo(() => computePassiveIncomeYoy(rows), [rows]);

  return (
    <div className="surface overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3">
        <h2 className="text-base font-semibold">Passive Income — Year-over-Year Growth</h2>
        <span className="rounded-[var(--radius-pill)] bg-bg-input px-2 py-0.5 text-xs text-text-muted">
          {growth.length} comparisons
        </span>
      </div>
      <div className="overflow-x-auto border-t border-border">
        <table className="w-full min-w-[720px] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
              <th className="px-3 py-2 font-medium">Comparison</th>
              <th className="px-3 py-2 text-right font-medium">Months Compared</th>
              <th className="px-3 py-2 text-right font-medium">This Period Total</th>
              <th className="px-3 py-2 text-right font-medium">Prior Period Total</th>
              <th className="px-3 py-2 text-right font-medium">Change</th>
            </tr>
          </thead>
          <tbody>
            {growth.length === 0 && (
              <tr>
                <td colSpan={5} className="px-3 py-6 text-center text-text-muted">
                  Not enough multi-year history to compute growth yet.
                </td>
              </tr>
            )}
            {growth.map((row) => (
              <tr
                key={row.year}
                className="border-b border-border/60 transition-colors last:border-0 hover:bg-bg-hover/40"
              >
                <td className="px-3 py-2 font-semibold">
                  {row.year} vs {row.priorYear}
                  {row.isPartialYear && (
                    <span className="ml-2 rounded-[var(--radius-pill)] bg-bg-input px-2 py-0.5 text-[10px] font-normal uppercase tracking-wide text-text-muted">
                      YTD
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 text-right font-mono">
                  Jan–{MONTHS[row.monthsCompared - 1].label}
                </td>
                <td className="px-3 py-2 text-right font-mono">{eur(row.thisYearNet)}</td>
                <td className="px-3 py-2 text-right font-mono">{eur(row.priorYearNet)}</td>
                <td className={`px-3 py-2 text-right font-mono ${row.pctChange === null ? "text-text-muted" : signedColor(row.pctChange)}`}>
                  {row.pctChange === null ? "—" : `${row.pctChange >= 0 ? "+" : ""}${row.pctChange.toFixed(1)}%`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="border-t border-border px-4 py-2 text-xs text-text-muted">
        Combines net dividends, scrip, and options income. Each comparison uses the same number of complete months
        on both sides, so the current (partial) year is never compared against a full prior year.
      </p>
    </div>
  );
}

function PassiveIncomeYoyChart({
  rows,
  selectedYears,
}: {
  rows: EconomicsAggregatedMonthlyRow[];
  selectedYears: Set<number>;
}) {
  const years = Array.from(new Set(rows.map((row) => parseYearMonth(row.month).year))).sort((a, b) => a - b);
  const visibleYears = years.filter((year) => selectedYears.has(year));
  if (!years.length) return <p className="text-sm text-text-muted">No multi-year data.</p>;

  const byMonth = new Map<number, Record<string, number | string>>();
  for (let month = 1; month <= 12; month += 1) {
    byMonth.set(month, { monthLabel: MONTHS[month - 1].label });
  }
  for (const row of rows) {
    const { year, month } = parseYearMonth(row.month);
    const bucket = byMonth.get(month);
    if (bucket) bucket[String(year)] = row.combined_net_eur;
  }
  const chartData = Array.from(byMonth.entries())
    .sort((a, b) => a[0] - b[0])
    .map(([, value]) => value);

  return (
    <div>
      <h3 className="mb-2 text-sm font-medium text-text-muted">Passive Income by Month (EUR)</h3>
      <div style={{ height: 260 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartData} margin={{ top: 8, right: 8, bottom: 4, left: 2 }}>
            <CartesianGrid stroke="rgba(148,163,184,0.08)" strokeDasharray="3 3" vertical={false} />
            <XAxis
              dataKey="monthLabel"
              tick={{ fill: "#8d969e", fontSize: 10 }}
              tickLine={false}
              axisLine={{ stroke: "rgba(148,163,184,0.15)" }}
            />
            <YAxis
              tick={{ fill: "#8d969e", fontSize: 10 }}
              tickLine={false}
              axisLine={{ stroke: "rgba(148,163,184,0.15)" }}
              tickFormatter={(value) => eur(Number(value))}
              width={72}
            />
            <Tooltip content={<ChartTooltip />} />
            <Legend wrapperStyle={{ fontSize: 11, color: "#8d969e" }} iconType="circle" iconSize={8} />
            {visibleYears.map((year) => (
              <Line
                key={year}
                type="monotone"
                dataKey={String(year)}
                name={String(year)}
                stroke={LINE_COLORS[years.indexOf(year) % LINE_COLORS.length]}
                strokeWidth={2}
                dot={{ r: 2 }}
                connectNulls
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-2 text-xs text-text-muted">{YEAR_MONTH_CAPTION}</p>
    </div>
  );
}

function InvestedCapitalYearlyChart({ rows }: { rows: InvestedCapitalYearlyRow[] }) {
  if (!rows.length) return <p className="text-sm text-text-muted">No buy/sell history.</p>;
  const chartData = rows.map((row) => ({
    label: String(row.year),
    buys_eur: row.buys_eur,
    sells_eur: row.sells_eur,
  }));

  return (
    <div>
      <h3 className="mb-2 text-sm font-medium text-text-muted">Buys vs Sells by Year (EUR)</h3>
      <div style={{ height: 260 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chartData} margin={{ top: 8, right: 8, bottom: 4, left: 2 }}>
            <CartesianGrid stroke="rgba(148,163,184,0.08)" strokeDasharray="3 3" vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fill: "#8d969e", fontSize: 10 }}
              tickLine={false}
              axisLine={{ stroke: "rgba(148,163,184,0.15)" }}
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
            <Legend wrapperStyle={{ fontSize: 11, color: "#8d969e" }} iconType="circle" iconSize={8} />
            <Bar dataKey="buys_eur" name="Buys" fill="#5b61ff" radius={[3, 3, 0, 0]} maxBarSize={28} isAnimationActive={false} />
            <Bar dataKey="sells_eur" name="Sells" fill="#00c493" radius={[3, 3, 0, 0]} maxBarSize={28} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function InvestedCapitalCumulativeChart({ rows }: { rows: InvestedCapitalReport["cumulative"] }) {
  if (!rows.length) return <p className="text-sm text-text-muted">No cumulative invested-capital history.</p>;
  const chartData = rows.map((row) => ({
    label: String(row.year),
    cumulative_net_invested_eur: row.cumulative_net_invested_eur,
  }));

  return (
    <div>
      <h3 className="mb-2 text-sm font-medium text-text-muted">Cumulative Invested Capital — Buys − Sells (EUR)</h3>
      <div style={{ height: 260 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartData} margin={{ top: 8, right: 8, bottom: 4, left: 2 }}>
            <CartesianGrid stroke="rgba(148,163,184,0.08)" strokeDasharray="3 3" vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fill: "#8d969e", fontSize: 10 }}
              tickLine={false}
              axisLine={{ stroke: "rgba(148,163,184,0.15)" }}
            />
            <YAxis
              tick={{ fill: "#8d969e", fontSize: 10 }}
              tickLine={false}
              axisLine={{ stroke: "rgba(148,163,184,0.15)" }}
              tickFormatter={(value) => eur(Number(value))}
              width={72}
            />
            <ReferenceLine y={0} stroke="rgba(148,163,184,0.35)" />
            <Tooltip content={<ChartTooltip />} />
            <Line
              type="monotone"
              dataKey="cumulative_net_invested_eur"
              name="Cumulative Net Invested"
              stroke="#5b61ff"
              strokeWidth={2}
              dot={{ r: 3 }}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-2 text-xs text-text-muted">
        Running total of stock buys minus sells across all years. Options premium cash flow is excluded — this
        tracks capital deployed into equity positions only.
      </p>
    </div>
  );
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
      label: "Dividends Cash (Net)",
      value: dividendsCash(summary),
      prefix: "€",
      decimals: 2,
      tone: (dividendsCash(summary) >= 0 ? "green" : "red") as "green" | "red",
    },
    {
      label: "Scrip Dividends",
      value: summary.dividends_scrip_eur ?? undefined,
      prefix: "€",
      decimals: 2,
      tone: ((summary.dividends_scrip_eur ?? 0) >= 0 ? "purple" : "red") as "purple" | "red",
      hint: "Economic value",
      tooltip: overviewCoverageTitle(summary),
    },
    {
      label: "Dividends Total",
      value: dividendsTotal(summary),
      prefix: "€",
      decimals: 2,
      tone: (dividendsTotal(summary) >= 0 ? "green" : "red") as "green" | "red",
      partial: isPartial(summary),
      tooltip: overviewCoverageTitle(summary),
    },
    {
      label: "Combined Cash Flow",
      value: summary.combined_net_eur ?? 0,
      prefix: "€",
      decimals: 2,
      tone: ((summary.combined_net_eur ?? 0) >= 0 ? "purple" : "red") as "purple" | "red",
      partial: isPartial(summary),
      tooltip: overviewCoverageTitle(summary),
    },
    {
      label: "Avg Monthly Total (last 12mo)",
      value: avgLast12 ?? undefined,
      prefix: "€",
      decimals: 2,
      tone: ((avgLast12 ?? 0) >= 0 ? "green" : "red") as "green" | "red",
      hint: "Months with no activity are excluded from the average",
    },
    {
      label: "Cash Yield on Cost",
      value: yoc ?? undefined,
      suffix: "%",
      decimals: 2,
      tone: "green" as const,
      hint: "Annualized dividends on current cost basis, held positions only",
    },
  ];

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {cards.map((card, index) => (
        <Reveal key={card.label} index={index} className="relative h-full">
          <StatCard
            label={card.label}
            value={"value" in card ? card.value : undefined}
            prefix={card.prefix}
            decimals={card.decimals}
            suffix={"suffix" in card ? card.suffix : undefined}
            tone={card.tone}
            hint={"hint" in card ? card.hint : undefined}
            tooltip={"tooltip" in card ? card.tooltip : undefined}
          />
          {"partial" in card && card.partial && (
            <span className="absolute right-4 top-4 z-10">
              <PartialBadge coverage={summary} />
            </span>
          )}
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
  dataKey: "options_net_eur" | "dividends_total_net_eur";
  title: string;
  fill: string;
}) {
  if (!rows.length) return <p className="text-sm text-text-muted">No data.</p>;

  const chartData = rows.map((row) => ({
    label: formatMonthLabel(row.month),
    value: dataKey === "dividends_total_net_eur" ? dividendsTotal(row) : row.options_net_eur,
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
        <table className="w-full min-w-[1120px] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
              <th className="px-3 py-2 font-medium">Month</th>
              <th className="px-3 py-2 text-right font-medium">Options Net (EUR)</th>
              <th className="px-3 py-2 text-right font-medium">Dividends Cash (EUR)</th>
              <th className="px-3 py-2 text-right font-medium">Scrip Dividends (EUR)</th>
              <th className="px-3 py-2 text-right font-medium">Dividends Total (EUR)</th>
              <th className="px-3 py-2 text-right font-medium">Combined Net (EUR)</th>
              <th className="px-3 py-2 text-right font-medium">Option Positions</th>
              <th className="px-3 py-2 text-right font-medium">Dividend Events</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={8} className="px-3 py-6 text-center text-text-muted">
                  No economics rows match the selected filters.
                </td>
              </tr>
            )}
            {rows.map((row) => (
              <tr key={row.month} className="border-b border-border/60 transition-colors last:border-0 hover:bg-bg-hover/40">
                <td className="px-3 py-2">{formatMonthLabel(row.month)}</td>
                <td className={`px-3 py-2 text-right font-mono ${signedColor(row.options_net_eur)}`}>{eur(row.options_net_eur)}</td>
                <td className={`px-3 py-2 text-right font-mono ${signedColor(dividendsCash(row))}`}>{eur(dividendsCash(row))}</td>
                <td className={`px-3 py-2 text-right font-mono ${
                  row.dividends_scrip_eur == null ? "text-text-muted" : signedColor(row.dividends_scrip_eur)
                }`} title={overviewCoverageTitle(row)}>
                  <NullableAmount value={row.dividends_scrip_eur} />
                </td>
                <td className={`px-3 py-2 text-right font-mono ${signedColor(dividendsTotal(row))}`}>
                  <span className="inline-flex items-center justify-end gap-2">
                    {eur(dividendsTotal(row))}
                    <PartialBadge coverage={row} />
                  </span>
                </td>
                <td className={`px-3 py-2 text-right font-mono ${signedColor(row.combined_net_eur)}`}>
                  <span className="inline-flex items-center justify-end gap-2">
                    {eur(row.combined_net_eur)}
                    <PartialBadge coverage={row} />
                  </span>
                </td>
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
  "symbol" | "options_net_eur" | "dividends_net_eur" | "dividends_cash_net_eur" | "dividends_scrip_eur" | "dividends_total_net_eur" | "combined_net_eur" | "option_positions" | "dividend_events"
>;

const BY_SYMBOL_COLS: { key: BySymbolKey; label: string; num?: boolean }[] = [
  { key: "symbol", label: "Symbol" },
  { key: "options_net_eur", label: "Options Net (EUR)", num: true },
  { key: "dividends_cash_net_eur", label: "Dividends Cash (EUR)", num: true },
  { key: "dividends_scrip_eur", label: "Scrip Dividends (EUR)", num: true },
  { key: "dividends_total_net_eur", label: "Dividends Total (EUR)", num: true },
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

function overviewSortValue(row: EconomicsAggregatedBySymbolRow, key: BySymbolKey) {
  if (key === "dividends_cash_net_eur") return dividendsCash(row);
  if (key === "dividends_total_net_eur") return dividendsTotal(row);
  return row[key];
}

function BySymbolSection({ rows }: { rows: EconomicsAggregatedBySymbolRow[] }) {
  const [sortKey, setSortKey] = useState<BySymbolKey>("combined_net_eur");
  const [dir, setDir] = useState<"asc" | "desc">("desc");

  const sorted = useMemo(
    () =>
      [...rows].sort((left, right) => {
        const comparison = compareValues(overviewSortValue(left, sortKey), overviewSortValue(right, sortKey));
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
        <table className="w-full min-w-[1120px] text-sm">
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
                <td colSpan={8} className="px-3 py-6 text-center text-text-muted">
                  No symbol-level economics available.
                </td>
              </tr>
            )}
            {sorted.map((row) => (
              <tr key={row.symbol} className="border-b border-border/60 transition-colors last:border-0 hover:bg-bg-hover/40">
                <td className="px-3 py-2 font-semibold">{row.symbol}</td>
                <td className={`px-3 py-2 text-right font-mono ${signedColor(row.options_net_eur)}`}>{eur(row.options_net_eur)}</td>
                <td className={`px-3 py-2 text-right font-mono ${signedColor(dividendsCash(row))}`}>{eur(dividendsCash(row))}</td>
                <td className={`px-3 py-2 text-right font-mono ${
                  row.dividends_scrip_eur == null ? "text-text-muted" : signedColor(row.dividends_scrip_eur)
                }`} title={overviewCoverageTitle(row)}>
                  <NullableAmount value={row.dividends_scrip_eur} />
                </td>
                <td className={`px-3 py-2 text-right font-mono ${signedColor(dividendsTotal(row))}`}>
                  <span className="inline-flex items-center justify-end gap-2">
                    {eur(dividendsTotal(row))}
                    <PartialBadge coverage={row} />
                  </span>
                </td>
                <td className={`px-3 py-2 text-right font-mono ${signedColor(row.combined_net_eur)}`}>
                  <span className="inline-flex items-center justify-end gap-2">
                    {eur(row.combined_net_eur)}
                    <PartialBadge coverage={row} />
                  </span>
                </td>
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
  const [comparisonData, setComparisonData] = useState<EconomicsAggregatedReport | null>(null);
  const [capitalData, setCapitalData] = useState<InvestedCapitalReport | null>(null);
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

    // Comparison/capital data ignore the Year filter: YoY growth and the
    // invested-capital evolution are inherently multi-year views.
    const comparisonParams = new URLSearchParams();
    if (months.length) comparisonParams.set("month", months.join(","));
    if (symbols.length) comparisonParams.set("symbol", symbols.join(","));
    if (accountIds.length) comparisonParams.set("account_id", accountIds.join(","));

    const capitalParams = new URLSearchParams();
    if (symbols.length) capitalParams.set("symbol", symbols.join(","));
    if (accountIds.length) capitalParams.set("account_id", accountIds.join(","));

    const queryString = params.toString();
    window.history.replaceState({}, "", queryString ? `/economics?${queryString}` : "/economics");

    try {
      const [response, comparisonResponse, capitalResponse] = await Promise.all([
        fetch(`/api/economics/overview${queryString ? `?${queryString}` : ""}`),
        fetch(`/api/economics/overview${comparisonParams.toString() ? `?${comparisonParams.toString()}` : ""}`),
        fetch(`/api/economics/capital${capitalParams.toString() ? `?${capitalParams.toString()}` : ""}`),
      ]);
      const [body, comparisonBody, capitalBody] = await Promise.all([
        response.json().catch(() => ({})),
        comparisonResponse.json().catch(() => ({})),
        capitalResponse.json().catch(() => ({})),
      ]);
      if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
      if (!comparisonResponse.ok) throw new Error(comparisonBody.error || `HTTP ${comparisonResponse.status}`);
      if (!capitalResponse.ok) throw new Error(capitalBody.error || `HTTP ${capitalResponse.status}`);
      setData(body as EconomicsAggregatedReport);
      setComparisonData(comparisonBody as EconomicsAggregatedReport);
      setCapitalData(capitalBody as InvestedCapitalReport);
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

  const [selectedPassiveIncomeYears, setSelectedPassiveIncomeYears] = useState<Set<number>>(new Set());
  const passiveIncomeRows = useMemo(() => comparisonData?.monthly ?? [], [comparisonData]);
  const availablePassiveIncomeYears = useMemo(
    () => Array.from(new Set(passiveIncomeRows.map((row) => parseYearMonth(row.month).year))).sort((a, b) => a - b),
    [passiveIncomeRows],
  );

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      setSelectedPassiveIncomeYears((prev) => {
        const stillValid = prev.size > 0 && Array.from(prev).every((y) => availablePassiveIncomeYears.includes(y));
        return stillValid ? prev : new Set(availablePassiveIncomeYears);
      });
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [availablePassiveIncomeYears]);

  function togglePassiveIncomeYear(yearValue: number) {
    setSelectedPassiveIncomeYears((prev) => {
      const next = new Set(prev);
      if (next.has(yearValue)) next.delete(yearValue);
      else next.add(yearValue);
      return next;
    });
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Economics Overview</h1>
        <p className="mt-1 text-sm text-text-muted">
          EUR-based options cash flow and total dividend income across your current filter scope.
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
          {coverage && coverage.excluded_paper_positions > 0 && (
            <div className={coverage || accountIds.length > 0 ? "mt-1" : ""}>
              Paper positions excluded from real totals.
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
              <OverviewBarChart rows={data.monthly} dataKey="dividends_total_net_eur" title="Monthly Dividends Total (EUR)" fill="#00c493" />
            </div>
          </div>

          {comparisonData && <PassiveIncomeYoySection rows={comparisonData.monthly} />}

          {comparisonData && (
            <div className="surface p-4">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
                <h2 className="text-base font-semibold">Passive Income — Year Comparison</h2>
                <div className="flex flex-wrap gap-1.5">
                  {availablePassiveIncomeYears.map((yearValue) => (
                    <button
                      key={yearValue}
                      type="button"
                      onClick={() => togglePassiveIncomeYear(yearValue)}
                      className={`rounded-[var(--radius-pill)] border px-2.5 py-0.5 text-xs transition-colors ${
                        selectedPassiveIncomeYears.has(yearValue)
                          ? "border-accent-blue/40 bg-accent-blue/15 text-accent-blue"
                          : "border-border bg-bg-input text-text-muted hover:text-text"
                      }`}
                    >
                      {yearValue}
                    </button>
                  ))}
                </div>
              </div>
              <PassiveIncomeYoyChart rows={comparisonData.monthly} selectedYears={selectedPassiveIncomeYears} />
            </div>
          )}

          {capitalData && (
            <div className="surface p-4">
              <h2 className="mb-4 text-base font-semibold">Invested Capital Evolution</h2>
              <div className="grid gap-6 lg:grid-cols-2">
                <InvestedCapitalYearlyChart rows={capitalData.yearly} />
                <InvestedCapitalCumulativeChart rows={capitalData.cumulative} />
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
