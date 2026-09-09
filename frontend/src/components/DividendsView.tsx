"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import MultiSelect from "@/components/MultiSelect";
import Reveal from "@/components/Reveal";
import StatCard from "@/components/StatCard";
import {
  Area,
  AreaChart,
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
import type {
  DividendPosition,
  DividendsBySymbolRow,
  DividendsCumulativeRow,
  DividendsMonthlyRow,
  DividendsReport,
  DividendsSummary,
  DividendsYearlyRow,
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

const YEAR_MONTH_CAPTION = "Shows all years · filtered by symbol/account only, not by Year/Month.";
const LINE_COLORS = ["#5b61ff", "#00c493", "#ff9416", "#c084fc", "#38bdf8", "#f43f5e"];

const eur = (value: number | null | undefined) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "EUR",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Number(value || 0));

const nativeCurrency = (value: number | null | undefined, currencyCode: string | null | undefined) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: currencyCode || "EUR",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Number(value || 0));

const signedColor = (value: number) => (value >= 0 ? "text-accent-green" : "text-accent-red");

function formatMonthLabel(value: string) {
  const [year, month] = value.split("-");
  const date = new Date(Number(year), Number(month) - 1, 1);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("en-US", { month: "short", year: "numeric" }).format(date);
}

function parseYearMonth(value: string) {
  const [year, month] = value.split("-");
  return { year: Number(year), month: Number(month) };
}

function compareValues(a: unknown, b: unknown): number {
  const left = a ?? "";
  const right = b ?? "";
  if (typeof left === "number" && typeof right === "number") return left - right;
  const leftDate = Date.parse(String(left));
  const rightDate = Date.parse(String(right));
  if (!Number.isNaN(leftDate) && !Number.isNaN(rightDate)) return leftDate - rightDate;
  return String(left).localeCompare(String(right));
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

function SummaryRow({ summary }: { summary: DividendsSummary }) {
  const cards = [
    { label: "Total Gross (EUR)", value: summary.total_gross_eur ?? 0, prefix: "€", suffix: "", decimals: 2, tone: "blue" as const },
    { label: "Total Withholding (EUR)", value: summary.total_withholding_eur ?? 0, prefix: "€", suffix: "", decimals: 2, tone: "orange" as const },
    {
      label: "Total Net (EUR)",
      value: summary.total_net_eur ?? 0,
      prefix: "€",
      suffix: "",
      decimals: 2,
      tone: ((summary.total_net_eur ?? 0) >= 0 ? "green" : "red") as "green" | "red",
    },
    { label: "Dividend Count", value: summary.total_dividends ?? 0, suffix: "", decimals: 0, tone: "purple" as const },
    { label: "Effective Withholding %", value: summary.effective_withholding_pct ?? 0, prefix: "", suffix: "%", decimals: 2, tone: "red" as const },
  ];

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
      {cards.map((card, index) => (
        <Reveal key={card.label} index={index} className="h-full">
          <StatCard
            label={card.label}
            value={card.value}
            prefix={card.prefix}
            suffix={card.suffix}
            decimals={card.decimals}
            tone={card.tone}
          />
        </Reveal>
      ))}
    </div>
  );
}

function MonthlySection({ rows }: { rows: DividendsMonthlyRow[] }) {
  return (
    <div className="surface overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3">
        <h2 className="text-base font-semibold">Monthly Dividends</h2>
        <span className="rounded-[var(--radius-pill)] bg-bg-input px-2 py-0.5 text-xs text-text-muted">
          {rows.length} months
        </span>
      </div>
      <div className="overflow-x-auto border-t border-border">
        <table className="w-full min-w-[760px] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
              <th className="px-3 py-2 font-medium">Month</th>
              <th className="px-3 py-2 text-right font-medium">Gross</th>
              <th className="px-3 py-2 text-right font-medium">Withholding</th>
              <th className="px-3 py-2 text-right font-medium">Net</th>
              <th className="px-3 py-2 text-right font-medium">Dividend Count</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={5} className="px-3 py-6 text-center text-text-muted">
                  No dividends match the selected filters.
                </td>
              </tr>
            )}
            {rows.map((row) => (
              <tr key={row.month} className="border-b border-border/60 transition-colors last:border-0 hover:bg-bg-hover/40">
                <td className="px-3 py-2">{formatMonthLabel(row.month)}</td>
                <td className="px-3 py-2 text-right font-mono">{eur(row.gross_eur)}</td>
                <td className="px-3 py-2 text-right font-mono">{eur(row.withholding_total_eur)}</td>
                <td className={`px-3 py-2 text-right font-mono ${signedColor(row.net_eur)}`}>{eur(row.net_eur)}</td>
                <td className="px-3 py-2 text-right font-mono">{row.dividend_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

type DividendsBySymbolKey = keyof Pick<
  DividendsBySymbolRow,
  "symbol" | "gross_eur" | "withholding_total_eur" | "net_eur" | "dividend_count"
>;

const BY_SYMBOL_COLS: { key: DividendsBySymbolKey; label: string; num?: boolean }[] = [
  { key: "symbol", label: "Symbol" },
  { key: "gross_eur", label: "Gross", num: true },
  { key: "withholding_total_eur", label: "Withholding", num: true },
  { key: "net_eur", label: "Net", num: true },
  { key: "dividend_count", label: "Dividend Count", num: true },
];

function BySymbolSection({ rows }: { rows: DividendsBySymbolRow[] }) {
  const [sortKey, setSortKey] = useState<DividendsBySymbolKey>("net_eur");
  const [dir, setDir] = useState<"asc" | "desc">("desc");

  const sorted = useMemo(
    () =>
      [...rows].sort((a, b) => {
        const comparison = compareValues(a[sortKey], b[sortKey]);
        return dir === "asc" ? comparison : -comparison;
      }),
    [dir, rows, sortKey],
  );

  function onSort(key: DividendsBySymbolKey) {
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
        <table className="w-full min-w-[640px] text-sm">
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
                <td colSpan={5} className="px-3 py-6 text-center text-text-muted">
                  No symbol-level dividends available.
                </td>
              </tr>
            )}
            {sorted.map((row) => (
              <tr key={row.symbol} className="border-b border-border/60 transition-colors last:border-0 hover:bg-bg-hover/40">
                <td className="px-3 py-2 font-semibold">{row.symbol}</td>
                <td className="px-3 py-2 text-right font-mono">{eur(row.gross_eur)}</td>
                <td className="px-3 py-2 text-right font-mono">{eur(row.withholding_total_eur)}</td>
                <td className={`px-3 py-2 text-right font-mono ${signedColor(row.net_eur)}`}>{eur(row.net_eur)}</td>
                <td className="px-3 py-2 text-right font-mono">{row.dividend_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

type DividendsYearlyKey = keyof Pick<DividendsYearlyRow, "year" | "gross_eur" | "withholding_eur" | "net_eur" | "dividend_count">;

const BY_YEAR_COLS: { key: DividendsYearlyKey; label: string; num?: boolean }[] = [
  { key: "year", label: "Year" },
  { key: "gross_eur", label: "Gross", num: true },
  { key: "withholding_eur", label: "Withholding", num: true },
  { key: "net_eur", label: "Net", num: true },
  { key: "dividend_count", label: "Dividend Count", num: true },
];

function ByYearSection({ rows }: { rows: DividendsYearlyRow[] }) {
  const [sortKey, setSortKey] = useState<DividendsYearlyKey>("year");
  const [dir, setDir] = useState<"asc" | "desc">("asc");

  const sorted = useMemo(
    () =>
      [...rows].sort((a, b) => {
        const comparison = compareValues(a[sortKey], b[sortKey]);
        return dir === "asc" ? comparison : -comparison;
      }),
    [dir, rows, sortKey],
  );

  function onSort(key: DividendsYearlyKey) {
    if (key === sortKey) setDir((current) => (current === "asc" ? "desc" : "asc"));
    else {
      setSortKey(key);
      setDir(key === "year" ? "asc" : "desc");
    }
  }

  return (
    <div className="surface overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3">
        <h2 className="text-base font-semibold">By Year</h2>
        <span className="rounded-[var(--radius-pill)] bg-bg-input px-2 py-0.5 text-xs text-text-muted">
          {rows.length} years
        </span>
      </div>
      <div className="overflow-x-auto border-t border-border">
        <table className="w-full min-w-[640px] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
              {BY_YEAR_COLS.map((column) => (
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
                <td colSpan={5} className="px-3 py-6 text-center text-text-muted">
                  No yearly dividend comparison data available.
                </td>
              </tr>
            )}
            {sorted.map((row) => (
              <tr key={row.year} className="border-b border-border/60 transition-colors last:border-0 hover:bg-bg-hover/40">
                <td className="px-3 py-2 font-semibold">{row.year}</td>
                <td className="px-3 py-2 text-right font-mono">{eur(row.gross_eur)}</td>
                <td className="px-3 py-2 text-right font-mono">{eur(row.withholding_eur)}</td>
                <td className={`px-3 py-2 text-right font-mono ${signedColor(row.net_eur)}`}>{eur(row.net_eur)}</td>
                <td className="px-3 py-2 text-right font-mono">{row.dividend_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="border-t border-border px-4 py-2 text-xs text-text-muted">{YEAR_MONTH_CAPTION}</p>
    </div>
  );
}

function MonthlyNetChart({ rows }: { rows: DividendsMonthlyRow[] }) {
  if (!rows.length) return <p className="text-sm text-text-muted">No data.</p>;
  const chartData = rows.map((row) => ({ label: formatMonthLabel(row.month), net_eur: row.net_eur }));

  return (
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
          <Tooltip cursor={{ fill: "rgba(148,163,184,0.08)" }} content={<ChartTooltip />} />
          <Bar dataKey="net_eur" name="Net Dividends" fill="#00c493" radius={[3, 3, 0, 0]} maxBarSize={28} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function GrossVsNetChart({ rows }: { rows: DividendsBySymbolRow[] }) {
  if (!rows.length) return <p className="text-sm text-text-muted">No data.</p>;
  const chartData = rows.map((row) => ({
    symbol: row.symbol,
    gross_eur: row.gross_eur,
    net_eur: row.net_eur,
    withholding_total_eur: row.withholding_total_eur,
  }));

  return (
    <div style={{ height: 260 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={chartData} margin={{ top: 8, right: 8, bottom: 4, left: 2 }} barGap={4}>
          <CartesianGrid stroke="rgba(148,163,184,0.08)" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="symbol"
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
          <Tooltip
            cursor={{ fill: "rgba(148,163,184,0.08)" }}
            content={({ active, payload, label }) => {
              if (!active || !payload?.length) return null;
              const source = payload[0]?.payload as { withholding_total_eur: number };
              return (
                <div className="rounded-[10px] border border-border bg-bg-card px-3 py-2 text-xs shadow-lg">
                  <div className="mb-1 font-medium text-text">{label}</div>
                  {payload.map((entry, index) => (
                    <div key={`${entry.name}-${index}`} className="flex items-center gap-2">
                      <span className="inline-block h-2 w-2 rounded-sm" style={{ background: entry.color }} />
                      <span className="text-text-muted">{entry.name}</span>
                      <span className="ml-auto font-mono text-text">{eur(Number(entry.value ?? 0))}</span>
                    </div>
                  ))}
                  <div className="mt-1 border-t border-border pt-1 text-text-muted">
                    Withholding: <span className="font-mono text-text">{eur(source.withholding_total_eur)}</span>
                  </div>
                </div>
              );
            }}
          />
          <Legend wrapperStyle={{ fontSize: 11, color: "#8d969e" }} iconType="circle" iconSize={8} />
          <Bar dataKey="gross_eur" name="Gross" fill="#5b61ff" radius={[3, 3, 0, 0]} maxBarSize={22} isAnimationActive={false} />
          <Bar dataKey="net_eur" name="Net" fill="#00c493" radius={[3, 3, 0, 0]} maxBarSize={22} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function YearOverYearChart({ rows }: { rows: DividendsMonthlyRow[] }) {
  const years = Array.from(new Set(rows.map((row) => parseYearMonth(row.month).year))).sort((a, b) => a - b);
  if (!years.length) return <p className="text-sm text-text-muted">No multi-year data.</p>;

  const byMonth = new Map<number, Record<string, number | string>>();
  for (let month = 1; month <= 12; month += 1) {
    byMonth.set(month, { monthLabel: MONTHS[month - 1].label });
  }
  for (const row of rows) {
    const { year, month } = parseYearMonth(row.month);
    const bucket = byMonth.get(month);
    if (bucket) bucket[String(year)] = row.net_eur;
  }
  const chartData = Array.from(byMonth.entries())
    .sort((a, b) => a[0] - b[0])
    .map(([, value]) => value);

  return (
    <>
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
            {years.map((year, index) => (
              <Line
                key={year}
                type="monotone"
                dataKey={String(year)}
                name={String(year)}
                stroke={LINE_COLORS[index % LINE_COLORS.length]}
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
    </>
  );
}

function CumulativeChart({ rows }: { rows: DividendsCumulativeRow[] }) {
  if (!rows.length) return <p className="text-sm text-text-muted">No cumulative dividend history.</p>;
  const chartData = rows.map((row) => ({ month: formatMonthLabel(row.month), cumulative_net_eur: row.cumulative_net_eur }));

  return (
    <>
      <div style={{ height: 260 }}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ top: 8, right: 8, bottom: 4, left: 2 }}>
            <CartesianGrid stroke="rgba(148,163,184,0.08)" strokeDasharray="3 3" vertical={false} />
            <XAxis
              dataKey="month"
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
            <Tooltip content={<ChartTooltip />} />
            <Area
              type="monotone"
              dataKey="cumulative_net_eur"
              name="Cumulative Net"
              stroke="#5b61ff"
              fill="rgba(91,97,255,0.25)"
              strokeWidth={2}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-2 text-xs text-text-muted">{YEAR_MONTH_CAPTION}</p>
    </>
  );
}

type DividendSortKey = "trade_date" | "account_id" | "symbol" | "gross_eur" | "withholding_total_eur" | "net_eur";

const POSITION_COLS: {
  key:
    | DividendSortKey
    | "gross_amount"
    | "gross_currency"
    | "fees_eur"
    | "withholding_source_eur"
    | "withholding_destination_eur"
    | "correction_status";
  label: string;
  num?: boolean;
  sortable?: boolean;
}[] = [
  { key: "trade_date", label: "Trade Date", sortable: true },
  { key: "account_id", label: "Account", sortable: true },
  { key: "symbol", label: "Symbol", sortable: true },
  { key: "gross_amount", label: "Gross Native", num: true },
  { key: "gross_currency", label: "Currency" },
  { key: "gross_eur", label: "Gross EUR", num: true, sortable: true },
  { key: "fees_eur", label: "Fees EUR", num: true },
  { key: "withholding_source_eur", label: "Withholding Src", num: true },
  { key: "withholding_destination_eur", label: "Withholding Dest", num: true },
  { key: "withholding_total_eur", label: "Withholding Total", num: true, sortable: true },
  { key: "net_eur", label: "Net EUR", num: true, sortable: true },
  { key: "correction_status", label: "Status" },
];

function statusBadgeClass(status: string): string {
  if (status === "CORRECTED") return "border-accent-orange/40 bg-accent-orange/10 text-accent-orange";
  if (status === "VOID") return "border-accent-red/40 bg-accent-red/10 text-accent-red";
  return "border-accent-green/40 bg-accent-green/10 text-accent-green";
}

function DividendsDetail({ rows }: { rows: DividendPosition[] }) {
  const [sortKey, setSortKey] = useState<DividendSortKey>("trade_date");
  const [dir, setDir] = useState<"asc" | "desc">("desc");

  const sorted = useMemo(
    () =>
      [...rows].sort((a, b) => {
        const comparison = compareValues(a[sortKey], b[sortKey]);
        return dir === "asc" ? comparison : -comparison;
      }),
    [dir, rows, sortKey],
  );

  function onSort(key: DividendSortKey) {
    if (key === sortKey) setDir((current) => (current === "asc" ? "desc" : "asc"));
    else {
      setSortKey(key);
      setDir(key === "trade_date" ? "desc" : "asc");
    }
  }

  return (
    <details open className="surface overflow-hidden">
      <summary className="flex cursor-pointer items-center justify-between px-4 py-3">
        <span className="text-base font-semibold">Dividends Detail</span>
        <span className="rounded-[var(--radius-pill)] bg-bg-input px-2 py-0.5 text-xs text-text-muted">
          {rows.length} rows
        </span>
      </summary>
      <div className="overflow-x-auto border-t border-border">
        <table className="w-full min-w-[1180px] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
              {POSITION_COLS.map((column) => (
                <th
                  key={column.key}
                  onClick={column.sortable ? () => onSort(column.key as DividendSortKey) : undefined}
                  className={`${column.sortable ? "cursor-pointer select-none hover:text-text" : ""} px-3 py-2 font-medium ${column.num ? "text-right" : ""}`}
                >
                  {column.label}
                  <span className="ml-1">
                    {column.sortable && sortKey === column.key ? (dir === "asc" ? "▲" : "▼") : ""}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 && (
              <tr>
                <td colSpan={POSITION_COLS.length} className="px-3 py-6 text-center text-text-muted">
                  No dividend movements match the selected filters.
                </td>
              </tr>
            )}
            {sorted.map((row) => (
              <tr key={row.id} className="border-b border-border/60 transition-colors last:border-0 hover:bg-bg-hover/40">
                <td className="px-3 py-2 font-mono">{row.trade_date}</td>
                <td className="px-3 py-2">{row.account_id || "—"}</td>
                <td className="px-3 py-2 font-semibold">{row.symbol}</td>
                <td className="px-3 py-2 text-right font-mono">{nativeCurrency(row.gross_amount, row.gross_currency)}</td>
                <td className="px-3 py-2 font-mono">{row.gross_currency}</td>
                <td className="px-3 py-2 text-right font-mono">{eur(row.gross_eur)}</td>
                <td className="px-3 py-2 text-right font-mono">{eur(row.fees_eur)}</td>
                <td className="px-3 py-2 text-right font-mono">{eur(row.withholding_source_eur)}</td>
                <td className="px-3 py-2 text-right font-mono">{eur(row.withholding_destination_eur)}</td>
                <td className="px-3 py-2 text-right font-mono">{eur(row.withholding_total_eur)}</td>
                <td className={`px-3 py-2 text-right font-mono ${signedColor(row.net_eur)}`}>{eur(row.net_eur)}</td>
                <td className="px-3 py-2">
                  <span className={`inline-block rounded-[var(--radius-pill)] border px-2 py-0.5 text-xs ${statusBadgeClass(row.correction_status)}`}>
                    {row.correction_status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

export default function DividendsView() {
  const [year, setYear] = useState<string>("");
  const [months, setMonths] = useState<string[]>([]);
  const [symbols, setSymbols] = useState<string[]>([]);
  const [accountIds, setAccountIds] = useState<string[]>([]);
  const [data, setData] = useState<DividendsReport | null>(null);
  const [comparisonData, setComparisonData] = useState<DividendsReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [initialized, setInitialized] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setYear(params.get("year") ?? String(new Date().getFullYear()));
    setMonths(params.get("month") ? params.get("month")!.split(",").filter(Boolean) : []);
    setSymbols(params.get("symbol") ? params.get("symbol")!.split(",").filter(Boolean) : []);
    setAccountIds(params.get("account_id") ? params.get("account_id")!.split(",").filter(Boolean) : []);
    setInitialized(true);
  }, []);

  const fetchData = useCallback(async () => {
    setError(null);
    setLoading(true);

    const params = new URLSearchParams();
    if (year) params.set("year", year);
    if (months.length) params.set("month", months.join(","));
    if (symbols.length) params.set("symbol", symbols.join(","));
    if (accountIds.length) params.set("account_id", accountIds.join(","));

    const comparisonParams = new URLSearchParams();
    if (symbols.length) comparisonParams.set("symbol", symbols.join(","));
    if (accountIds.length) comparisonParams.set("account_id", accountIds.join(","));

    const qs = params.toString();
    const comparisonQs = comparisonParams.toString();
    window.history.replaceState({}, "", qs ? `/economics/dividends?${qs}` : "/economics/dividends");

    try {
      const [mainRes, comparisonRes] = await Promise.all([
        fetch(`/api/economics/dividends${qs ? `?${qs}` : ""}`),
        fetch(`/api/economics/dividends${comparisonQs ? `?${comparisonQs}` : ""}`),
      ]);
      const [mainBody, comparisonBody] = await Promise.all([
        mainRes.json().catch(() => ({})),
        comparisonRes.json().catch(() => ({})),
      ]);
      if (!mainRes.ok) throw new Error((mainBody as { error?: string }).error || `HTTP ${mainRes.status}`);
      if (!comparisonRes.ok) throw new Error((comparisonBody as { error?: string }).error || `HTTP ${comparisonRes.status}`);
      setData(mainBody as DividendsReport);
      setComparisonData(comparisonBody as DividendsReport);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load dividends data.");
    } finally {
      setLoading(false);
    }
  }, [accountIds, months, symbols, year]);

  useEffect(() => {
    if (!initialized) return;
    fetchData();
  }, [fetchData, initialized]);

  const yearOptions = data?.filters.years ?? [];
  const symbolOptions = (data?.filters.symbols ?? []).map((symbol) => ({ value: symbol, label: symbol }));
  const accountOptions = (data?.filters.account_ids ?? []).map((accountId) => ({ value: accountId, label: accountId }));
  const yoyRows = comparisonData?.monthly ?? [];
  const yearlyRows = comparisonData?.yearly ?? [];
  const cumulativeRows = comparisonData?.cumulative ?? [];

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Economics · Dividends</h1>
        <p className="mt-1 text-sm text-text-muted">
          Gross, withholding, net income, and dividend snowball trends across your accounts and symbols.
        </p>
      </div>

      {error && (
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
          ⚠️ {error}
        </div>
      )}

      {data && <SummaryRow summary={data.summary} />}

      <div className="surface p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-base font-semibold">Filters</h2>
          <span className="rounded-[var(--radius-pill)] bg-bg-input px-2 py-0.5 text-xs text-text-muted">
            {data?.summary.total_dividends ?? 0} dividends
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
          Loading dividends data…
        </div>
      )}

      {data && comparisonData && (
        <>
          <MonthlySection rows={data.monthly} />
          <BySymbolSection rows={data.by_symbol} />
          <ByYearSection rows={yearlyRows} />

          <div className="surface p-4">
            <h2 className="mb-4 text-base font-semibold">Charts</h2>
            <div className="grid gap-6 xl:grid-cols-2">
              <div>
                <h3 className="mb-2 text-sm font-medium text-text-muted">Monthly Net Dividends</h3>
                <MonthlyNetChart rows={data.monthly} />
              </div>
              <div>
                <h3 className="mb-2 text-sm font-medium text-text-muted">Gross vs Net by Symbol</h3>
                <GrossVsNetChart rows={data.by_symbol} />
              </div>
              <div>
                <h3 className="mb-2 text-sm font-medium text-text-muted">Year-over-Year Monthly Comparison</h3>
                <YearOverYearChart rows={yoyRows} />
              </div>
              <div>
                <h3 className="mb-2 text-sm font-medium text-text-muted">Cumulative Net Dividends (Snowball)</h3>
                <CumulativeChart rows={cumulativeRows} />
              </div>
            </div>
          </div>

          <DividendsDetail rows={data.positions} />
        </>
      )}
    </div>
  );
}
