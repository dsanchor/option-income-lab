"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import MultiSelect from "@/components/MultiSelect";
import StatCard from "@/components/StatCard";
import Reveal from "@/components/Reveal";
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
import type {
  EconomicsBySymbolRow,
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

const currency = (v: number | null | undefined) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Number(v || 0));

const pct = (v: number | null | undefined) => `${Number(v || 0).toFixed(2)}%`;
const netColor = (v: number) => (v >= 0 ? "text-accent-green" : "text-accent-red");

function Pills({
  options,
  value,
  onChange,
}: {
  options: { value: string; label: string }[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex items-center gap-1 rounded-[var(--radius-pill)] border border-border bg-bg-card p-1">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          className={`rounded-[var(--radius-pill)] px-3 py-1 text-xs transition ${
            value === o.value ? "bg-accent-blue text-white" : "text-text-muted hover:text-text"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function SummaryRow({ summary }: { summary: EconomicsSummary }) {
  const net = summary.net_income ?? 0;
  const roc = summary.avg_roc_annualized ?? summary.avg_roc_pct ?? 0;
  const cards = [
    { label: "Total Premium Collected", value: summary.total_premium ?? 0, prefix: "$", suffix: "", decimals: 2, tone: "green" as const },
    { label: "Total Buyback Costs", value: summary.total_buyback ?? 0, prefix: "$", suffix: "", decimals: 2, tone: "orange" as const },
    { label: "Net Income", value: net, prefix: "$", suffix: "", decimals: 2, tone: (net >= 0 ? "green" : "red") as "green" | "red" },
    { label: "Avg RoC% (Annualized)", value: roc, prefix: "", suffix: "%", decimals: 2, tone: "blue" as const },
    { label: "Win Rate", value: summary.win_rate ?? 0, prefix: "", suffix: "%", decimals: 2, tone: "purple" as const },
  ];
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
      {cards.map((c, i) => (
        <Reveal key={c.label} index={i} className="h-full">
          <StatCard
            label={c.label}
            value={c.value}
            prefix={c.prefix}
            suffix={c.suffix}
            decimals={c.decimals}
            tone={c.tone}
          />
        </Reveal>
      ))}
    </div>
  );
}

/** Dependency-free stacked bar chart: monthly calls net + puts net. */
const CALLS_COLOR = "#00c493";
const PUTS_COLOR = "#ff9416";
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
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-2">
          <span className="inline-block h-2 w-2 rounded-sm" style={{ background: p.color }} />
          <span className="text-text-muted">{p.name}</span>
          <span className="ml-auto font-mono text-text">{currency(Number(p.value ?? 0))}</span>
        </div>
      ))}
    </div>
  );
}

function MonthlyNetChart({ rows }: { rows: EconomicsMonthlyRow[] }) {
  if (!rows.length) return <p className="text-sm text-text-muted">No data.</p>;
  const chartData = rows.map((r) => ({
    label: r.label,
    calls_net: r.calls_net,
    puts_net: r.puts_net,
    net: r.calls_net + r.puts_net,
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
            tickFormatter={(v) => currency(Number(v))}
            width={56}
          />
          <ReferenceLine y={0} stroke="rgba(148,163,184,0.35)" />
          <Tooltip cursor={{ fill: "rgba(148,163,184,0.08)" }} content={<ChartTooltip />} />
          <Legend
            wrapperStyle={{ fontSize: 11, color: "#8d969e" }}
            iconType="circle"
            iconSize={8}
          />
          <Bar dataKey="calls_net" name="Calls Net" fill={CALLS_COLOR} radius={[3, 3, 0, 0]} maxBarSize={22} isAnimationActive={false} />
          <Bar dataKey="puts_net" name="Puts Net" fill={PUTS_COLOR} radius={[3, 3, 0, 0]} maxBarSize={22} isAnimationActive={false} />
          <Bar dataKey="net" name="Net" fill={NET_COLOR} radius={[3, 3, 0, 0]} maxBarSize={22} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Recharts donut: calls vs puts net (absolute weights). */
function TypeDoughnut({ callsNet, putsNet }: { callsNet: number; putsNet: number }) {
  const a = Math.abs(callsNet);
  const b = Math.abs(putsNet);
  const total = a + b;
  const pieData = [
    { name: "Calls", value: a, raw: callsNet, color: CALLS_COLOR },
    { name: "Puts", value: b, raw: putsNet, color: PUTS_COLOR },
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
            {(total > 0 ? pieData : [{ color: "var(--color-border)" }]).map((d, i) => (
              <Cell key={i} fill={d.color} />
            ))}
          </Pie>
          {total > 0 && (
            <Tooltip
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const p = payload[0].payload as { name: string; raw: number; color: string };
                return (
                  <div className="rounded-[10px] border border-border bg-bg-card px-3 py-2 text-xs shadow-lg">
                    <span className="flex items-center gap-2">
                      <span className="inline-block h-2 w-2 rounded-sm" style={{ background: p.color }} />
                      <span className="text-text-muted">{p.name}</span>
                      <span className="ml-auto font-mono text-text">{currency(p.raw)}</span>
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
        <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-sm" style={{ background: CALLS_COLOR }} /> Calls {currency(callsNet)}</span>
        <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-sm" style={{ background: PUTS_COLOR }} /> Puts {currency(putsNet)}</span>
      </div>
    </div>
  );
}

const POSITION_COLS: { key: EconomicsSortKey; label: string; num?: boolean }[] = [
  { key: "symbol", label: "Symbol" },
  { key: "type", label: "Type" },
  { key: "strike", label: "Strike", num: true },
  { key: "expiration", label: "Expiration" },
  { key: "premium", label: "Premium", num: true },
  { key: "buyback_cost", label: "Buyback", num: true },
  { key: "net", label: "Net", num: true },
  { key: "roc_pct", label: "RoC%", num: true },
  { key: "roc_annualized", label: "RoC% Ann.", num: true },
  { key: "days_held", label: "Days Held", num: true },
  { key: "status", label: "Status" },
  { key: "opened_at", label: "Opened" },
];

function compareValues(a: unknown, b: unknown): number {
  const l = a ?? "";
  const r = b ?? "";
  if (typeof l === "number" && typeof r === "number") return l - r;
  const ld = Date.parse(String(l));
  const rd = Date.parse(String(r));
  if (!Number.isNaN(ld) && !Number.isNaN(rd)) return ld - rd;
  return String(l).localeCompare(String(r));
}

function statusBadgeClass(status: string): string {
  if (status === "rolled") return "border-accent-orange/40 bg-accent-orange/10 text-accent-orange";
  if (status === "closed") return "border-accent-blue/40 bg-accent-blue/10 text-accent-blue";
  return "border-accent-green/40 bg-accent-green/10 text-accent-green";
}

function PositionsDetail({ rows }: { rows: EconomicsPosition[] }) {
  const [sortKey, setSortKey] = useState<EconomicsSortKey>("opened_at");
  const [dir, setDir] = useState<"asc" | "desc">("desc");

  const sorted = useMemo(() => {
    return [...rows].sort((a, b) => {
      const c = compareValues(a[sortKey], b[sortKey]);
      return dir === "asc" ? c : -c;
    });
  }, [rows, sortKey, dir]);

  function onSort(key: EconomicsSortKey) {
    if (key === sortKey) setDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(key);
      setDir(key === "opened_at" ? "desc" : "asc");
    }
  }

  return (
    <details open className="surface overflow-hidden">
      <summary className="flex cursor-pointer items-center justify-between px-4 py-3">
        <span className="text-base font-semibold">Positions Detail</span>
        <span className="rounded-[var(--radius-pill)] bg-bg-input px-2 py-0.5 text-xs text-text-muted">
          {rows.length} rows
        </span>
      </summary>
      <div className="overflow-x-auto border-t border-border">
        <table className="w-full min-w-[960px] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
              {POSITION_COLS.map((c) => (
                <th
                  key={c.key}
                  onClick={() => onSort(c.key)}
                  className={`cursor-pointer select-none px-3 py-2 font-medium ${c.num ? "text-right" : ""}`}
                >
                  {c.label}
                  <span className="ml-1">{sortKey === c.key ? (dir === "asc" ? "▲" : "▼") : ""}</span>
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
            {sorted.map((p, i) => (
              <tr key={p.position_id ?? i} className="border-b border-border/60 transition-colors last:border-0 hover:bg-bg-hover/40">
                <td className="px-3 py-2 font-semibold">{p.symbol}</td>
                <td className="px-3 py-2">
                  <span
                    className={`inline-block rounded-[var(--radius-pill)] border px-2 py-0.5 text-xs ${
                      p.type === "call"
                        ? "border-accent-green/40 bg-accent-green/10 text-accent-green"
                        : "border-accent-blue/40 bg-accent-blue/10 text-accent-blue"
                    }`}
                  >
                    {p.type.toUpperCase()}
                  </span>
                </td>
                <td className="px-3 py-2 text-right font-mono">{p.strike != null ? currency(p.strike) : "—"}</td>
                <td className="px-3 py-2">{p.expiration || "—"}</td>
                <td className="px-3 py-2 text-right font-mono">{currency(p.premium)}</td>
                <td className="px-3 py-2 text-right font-mono">{p.buyback_cost != null ? currency(p.buyback_cost) : "—"}</td>
                <td className={`px-3 py-2 text-right font-mono ${netColor(p.net)}`}>{currency(p.net)}</td>
                <td className="px-3 py-2 text-right font-mono">{p.roc_pct != null ? pct(p.roc_pct) : "—"}</td>
                <td className="px-3 py-2 text-right font-mono">{p.roc_annualized != null ? pct(p.roc_annualized) : "—"}</td>
                <td className="px-3 py-2 text-right font-mono">{p.days_held ?? "—"}</td>
                <td className="px-3 py-2">
                  <span className={`inline-block rounded-[var(--radius-pill)] border px-2 py-0.5 text-xs ${statusBadgeClass(p.status)}`}>
                    {p.status}
                  </span>
                </td>
                <td className="px-3 py-2 font-mono">{(p.opened_at || "").slice(0, 10) || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

export default function EconomicsView() {
  const [year, setYear] = useState<string>("");
  const [months, setMonths] = useState<string[]>([]);
  const [symbols, setSymbols] = useState<string[]>([]);
  const [type, setType] = useState<string>("");
  const [status, setStatus] = useState<string>("");
  const [data, setData] = useState<EconomicsReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [initialized, setInitialized] = useState(false);

  // Initialize from URL once on mount.
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    setYear(p.get("year") ?? String(new Date().getFullYear()));
    setType(p.get("type") ?? "");
    setStatus(p.get("status") ?? "");
    setMonths(p.get("month") ? p.get("month")!.split(",").filter(Boolean) : []);
    setSymbols(p.get("symbol") ? p.get("symbol")!.split(",").filter(Boolean) : []);
    setInitialized(true);
  }, []);

  const fetchData = useCallback(async () => {
    setError(null);
    const params = new URLSearchParams();
    if (year) params.set("year", year);
    if (months.length) params.set("month", months.join(","));
    if (symbols.length) params.set("symbol", symbols.join(","));
    if (type) params.set("type", type);
    if (status) params.set("status", status);
    const qs = params.toString();
    // Keep the browser URL in sync (shareable filters).
    window.history.replaceState({}, "", qs ? `/economics?${qs}` : "/economics");
    try {
      const res = await fetch(`/api/economics${qs ? `?${qs}` : ""}`);
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
      setData(body as EconomicsReport);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load economics data.");
    } finally {
      setLoading(false);
    }
  }, [year, months, symbols, type, status]);

  useEffect(() => {
    if (!initialized) return;
    fetchData();
  }, [initialized, fetchData]);

  const yearOptions = data?.filters.years ?? [];
  const symbolOptions = (data?.filters.symbols ?? []).map((s) => ({ value: s, label: s }));

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Economics</h1>
        <p className="mt-1 text-sm text-text-muted">
          Premium, buybacks, net income, and options RoC analytics across all symbols.
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
            {data?.summary.total_positions ?? 0} positions
          </span>
        </div>
        <div className="flex flex-wrap items-end gap-4">
          <div className="flex flex-col gap-1">
            <span className="text-xs text-text-muted">Year</span>
            <select
              value={year}
              onChange={(e) => setYear(e.target.value)}
              className="rounded-[var(--radius)] border border-border bg-bg-input px-3 py-1.5 text-sm text-text"
            >
              <option value="">All Years</option>
              {yearOptions.map((y) => (
                <option key={y} value={String(y)}>
                  {y}
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
                <h3 className="mb-2 text-sm font-medium text-text-muted">Monthly Net Income</h3>
                <MonthlyNetChart rows={data.monthly} />
              </div>
              <div>
                <h3 className="mb-2 text-sm font-medium text-text-muted">Calls vs Puts Net</h3>
                <TypeDoughnut callsNet={data.by_type.calls.net} putsNet={data.by_type.puts.net} />
              </div>
            </div>
          </div>

          <PositionsDetail rows={data.positions} />
        </>
      )}
    </div>
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
        <table className="w-full min-w-[720px] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
              <th className="px-3 py-2 font-medium">Month</th>
              <th className="px-3 py-2 text-right font-medium">Premium</th>
              <th className="px-3 py-2 text-right font-medium">Buyback</th>
              <th className="px-3 py-2 text-right font-medium">Net</th>
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
            {rows.map((r) => (
              <tr key={`${r.year}-${r.month}`} className="border-b border-border/60 transition-colors last:border-0 hover:bg-bg-hover/40">
                <td className="px-3 py-2">{r.label}</td>
                <td className="px-3 py-2 text-right font-mono">{currency(r.premium)}</td>
                <td className="px-3 py-2 text-right font-mono">{currency(r.buyback)}</td>
                <td className={`px-3 py-2 text-right font-mono ${netColor(r.net)}`}>{currency(r.net)}</td>
                <td className="px-3 py-2 text-right font-mono">{r.positions_count}</td>
                <td className="px-3 py-2 text-right font-mono">{pct(r.avg_roc_annualized ?? r.avg_roc_pct)}</td>
                <td className="px-3 py-2 text-right font-mono">{r.calls_count}</td>
                <td className="px-3 py-2 text-right font-mono">{r.puts_count}</td>
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
  "symbol" | "premium" | "buyback" | "net" | "positions_count" | "avg_roc_annualized"
>;

const BY_SYMBOL_COLS: { key: BySymbolKey; label: string; num?: boolean }[] = [
  { key: "symbol", label: "Symbol" },
  { key: "premium", label: "Premium", num: true },
  { key: "buyback", label: "Buyback", num: true },
  { key: "net", label: "Net", num: true },
  { key: "positions_count", label: "# Positions", num: true },
  { key: "avg_roc_annualized", label: "Avg RoC% (Ann.)", num: true },
];

function BySymbolSection({ rows }: { rows: EconomicsBySymbolRow[] }) {
  const [sortKey, setSortKey] = useState<BySymbolKey>("net");
  const [dir, setDir] = useState<"asc" | "desc">("desc");

  const sorted = useMemo(() => {
    return [...rows].sort((a, b) => {
      const c = compareValues(a[sortKey], b[sortKey]);
      return dir === "asc" ? c : -c;
    });
  }, [rows, sortKey, dir]);

  function onSort(key: BySymbolKey) {
    if (key === sortKey) setDir((d) => (d === "asc" ? "desc" : "asc"));
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
              {BY_SYMBOL_COLS.map((c) => (
                <th
                  key={c.key}
                  onClick={() => onSort(c.key)}
                  className={`cursor-pointer select-none px-3 py-2 font-medium hover:text-text ${c.num ? "text-right" : ""}`}
                >
                  {c.label}
                  <span className="ml-1">{sortKey === c.key ? (dir === "asc" ? "▲" : "▼") : ""}</span>
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
            {sorted.map((r) => (
              <tr key={r.symbol} className="border-b border-border/60 transition-colors last:border-0 hover:bg-bg-hover/40">
                <td className="px-3 py-2 font-semibold">{r.symbol}</td>
                <td className="px-3 py-2 text-right font-mono">{currency(r.premium)}</td>
                <td className="px-3 py-2 text-right font-mono">{currency(r.buyback)}</td>
                <td className={`px-3 py-2 text-right font-mono ${netColor(r.net)}`}>{currency(r.net)}</td>
                <td className="px-3 py-2 text-right font-mono">{r.positions_count}</td>
                <td className="px-3 py-2 text-right font-mono">{pct(r.avg_roc_annualized ?? r.avg_roc_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
