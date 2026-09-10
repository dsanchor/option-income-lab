"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import MultiSelect from "@/components/MultiSelect";
import Reveal from "@/components/Reveal";
import StatCard from "@/components/StatCard";
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

const usd = (value: number | null | undefined, currencyCode = "USD") =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: currencyCode,
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

const signedColor = (value: number) => (value >= 0 ? "text-accent-green" : "text-accent-red");

function formatMonthLabel(value: string) {
  const [year, month] = value.split("-");
  const date = new Date(Number(year), Number(month) - 1, 1);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("en-US", { month: "short", year: "numeric" }).format(date);
}

function SummaryRow({ summary }: { summary: EconomicsAggregatedSummary }) {
  const optionNet = summary.options_net_native ?? 0;
  const dividendNet = summary.dividends_net_eur ?? 0;
  const cards = [
    {
      label: `Options Net (${summary.options_currency || "USD"} native)`,
      value: optionNet,
      prefix: "$",
      suffix: "",
      decimals: 2,
      tone: (optionNet >= 0 ? "green" : "red") as "green" | "red",
    },
    {
      label: "Dividends Net (EUR)",
      value: dividendNet,
      prefix: "€",
      suffix: "",
      decimals: 2,
      tone: (dividendNet >= 0 ? "green" : "red") as "green" | "red",
    },
    {
      label: "Option Positions",
      value: summary.total_option_positions ?? 0,
      suffix: "",
      decimals: 0,
      tone: "blue" as const,
    },
    {
      label: "Dividend Events",
      value: summary.total_dividend_events ?? 0,
      suffix: "",
      decimals: 0,
      tone: "purple" as const,
    },
    {
      label: "Symbols with Cash Flow",
      value: summary.total_symbols ?? 0,
      suffix: "",
      decimals: 0,
      tone: "orange" as const,
    },
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

function ChartTooltip({
  active,
  payload,
  label,
  currency,
}: {
  active?: boolean;
  payload?: { name?: string; value?: number; color?: string }[];
  label?: string;
  currency: "USD" | "EUR";
}) {
  if (!active || !payload?.length) return null;
  const format = currency === "EUR" ? eur : (value: number) => usd(value, "USD");

  return (
    <div className="rounded-[10px] border border-border bg-bg-card px-3 py-2 text-xs shadow-lg">
      {label && <div className="mb-1 font-medium text-text">{label}</div>}
      {payload.map((entry, index) => (
        <div key={`${entry.name}-${index}`} className="flex items-center gap-2">
          <span className="inline-block h-2 w-2 rounded-sm" style={{ background: entry.color }} />
          <span className="text-text-muted">{entry.name}</span>
          <span className="ml-auto font-mono text-text">{format(Number(entry.value ?? 0))}</span>
        </div>
      ))}
    </div>
  );
}

function OverviewBarChart({
  rows,
  dataKey,
  title,
  currency,
  fill,
}: {
  rows: EconomicsAggregatedMonthlyRow[];
  dataKey: "options_net_native" | "dividends_net_eur";
  title: string;
  currency: "USD" | "EUR";
  fill: string;
}) {
  if (!rows.length) return <p className="text-sm text-text-muted">No data.</p>;
  const format = currency === "EUR" ? eur : (value: number) => usd(value, "USD");
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
              tickFormatter={(value) => format(Number(value))}
              width={72}
            />
            <ReferenceLine y={0} stroke="rgba(148,163,184,0.35)" />
            <Tooltip content={<ChartTooltip currency={currency} />} cursor={{ fill: "rgba(148,163,184,0.08)" }} />
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
        <table className="w-full min-w-[720px] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
              <th className="px-3 py-2 font-medium">Month</th>
              <th className="px-3 py-2 text-right font-medium">Options Net (USD native)</th>
              <th className="px-3 py-2 text-right font-medium">Dividends Net (EUR)</th>
              <th className="px-3 py-2 text-right font-medium">Option Positions</th>
              <th className="px-3 py-2 text-right font-medium">Dividend Events</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={5} className="px-3 py-6 text-center text-text-muted">
                  No economics rows match the selected filters.
                </td>
              </tr>
            )}
            {rows.map((row) => (
              <tr key={row.month} className="border-b border-border/60 transition-colors last:border-0 hover:bg-bg-hover/40">
                <td className="px-3 py-2">{formatMonthLabel(row.month)}</td>
                <td className={`px-3 py-2 text-right font-mono ${signedColor(row.options_net_native)}`}>
                  {usd(row.options_net_native, "USD")}
                </td>
                <td className={`px-3 py-2 text-right font-mono ${signedColor(row.dividends_net_eur)}`}>
                  {eur(row.dividends_net_eur)}
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
  "symbol" | "options_net_native" | "dividends_net_eur" | "option_positions" | "dividend_events"
>;

const BY_SYMBOL_COLS: { key: BySymbolKey; label: string; num?: boolean }[] = [
  { key: "symbol", label: "Symbol" },
  { key: "options_net_native", label: "Options Net (USD native)", num: true },
  { key: "dividends_net_eur", label: "Dividends Net (EUR)", num: true },
  { key: "option_positions", label: "Option Positions", num: true },
  { key: "dividend_events", label: "Dividend Events", num: true },
];

function compareValues(a: unknown, b: unknown): number {
  const left = a ?? "";
  const right = b ?? "";
  if (typeof left === "number" && typeof right === "number") return left - right;
  return String(left).localeCompare(String(right));
}

function BySymbolSection({ rows }: { rows: EconomicsAggregatedBySymbolRow[] }) {
  const [sortKey, setSortKey] = useState<BySymbolKey>("options_net_native");
  const [dir, setDir] = useState<"asc" | "desc">("desc");

  const sorted = useMemo(
    () =>
      [...rows].sort((a, b) => {
        const comparison = compareValues(a[sortKey], b[sortKey]);
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
                <td colSpan={5} className="px-3 py-6 text-center text-text-muted">
                  No symbol-level economics available.
                </td>
              </tr>
            )}
            {sorted.map((row) => (
              <tr key={row.symbol} className="border-b border-border/60 transition-colors last:border-0 hover:bg-bg-hover/40">
                <td className="px-3 py-2 font-semibold">{row.symbol}</td>
                <td className={`px-3 py-2 text-right font-mono ${signedColor(row.options_net_native)}`}>
                  {usd(row.options_net_native, "USD")}
                </td>
                <td className={`px-3 py-2 text-right font-mono ${signedColor(row.dividends_net_eur)}`}>
                  {eur(row.dividends_net_eur)}
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
  const [year, setYear] = useState<string>("");
  const [months, setMonths] = useState<string[]>([]);
  const [symbols, setSymbols] = useState<string[]>([]);
  const [data, setData] = useState<EconomicsAggregatedReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [initialized, setInitialized] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setYear(params.get("year") ?? String(new Date().getFullYear()));
    setMonths(params.get("month") ? params.get("month")!.split(",").filter(Boolean) : []);
    setSymbols(params.get("symbol") ? params.get("symbol")!.split(",").filter(Boolean) : []);
    setInitialized(true);
  }, []);

  const fetchData = useCallback(async () => {
    setError(null);
    setLoading(true);

    const params = new URLSearchParams();
    if (year) params.set("year", year);
    if (months.length) params.set("month", months.join(","));
    if (symbols.length) params.set("symbol", symbols.join(","));

    const qs = params.toString();
    window.history.replaceState({}, "", qs ? `/economics?${qs}` : "/economics");

    try {
      const res = await fetch(`/api/economics/overview${qs ? `?${qs}` : ""}`);
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
      const report = body as EconomicsAggregatedReport;
      // Dividends net should reflect cash + derechos (rights) combined everywhere in the overview.
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
  }, [months, symbols, year]);

  useEffect(() => {
    if (!initialized) return;
    fetchData();
  }, [fetchData, initialized]);

  const yearOptions = data?.filters.years ?? [];
  const symbolOptions = (data?.filters.symbols ?? []).map((symbol) => ({ value: symbol, label: symbol }));

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Economics Overview</h1>
        <p className="mt-1 text-sm text-text-muted">
          Side-by-side options and dividends cash-flow trends without blending USD-native options income with EUR dividends.
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
              <OverviewBarChart
                rows={data.monthly}
                dataKey="options_net_native"
                title="Monthly Options Net"
                currency="USD"
                fill="#5b61ff"
              />
              <OverviewBarChart
                rows={data.monthly}
                dataKey="dividends_net_eur"
                title="Monthly Dividends Net"
                currency="EUR"
                fill="#00c493"
              />
            </div>
          </div>
        </>
      )}
    </div>
  );
}
