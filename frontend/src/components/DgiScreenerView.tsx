"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import type {
  DgiEntry,
  DgiFilterCheck,
  DgiScorePoint,
} from "@/types/dgi";

type SortState = { col: string; asc: boolean };

const num = (v: unknown): number => (typeof v === "number" && isFinite(v) ? v : 0);

function scoreClass(score: number): string {
  if (score >= 70) return "text-accent-green";
  if (score >= 40) return "text-text";
  return "text-accent-red";
}

function fmtVal(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return v.toLocaleString(undefined, { maximumFractionDigits: 4 });
  return String(v);
}

// ── Detail modal helpers ──────────────────────────────────────────────

function KeyValueSection({ title, obj }: { title: string; obj: Record<string, unknown> }) {
  const entries = Object.entries(obj).filter(([, v]) => !(v && typeof v === "object" && !Array.isArray(v)));
  const nested = Object.entries(obj).filter(([, v]) => v && typeof v === "object" && !Array.isArray(v));
  return (
    <div className="mb-4">
      <h4 className="mb-2 text-xs font-medium uppercase tracking-wide text-text-muted">{title}</h4>
      <div className="rounded-[var(--radius)] bg-bg-input px-4 py-3">
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3">
          {entries.map(([k, v]) => (
            <div key={k}>
              <div className="text-xs text-text-muted">{k.replace(/_/g, " ")}</div>
              <div className="font-mono text-sm">{formatField(k, v)}</div>
            </div>
          ))}
        </div>
      </div>
      {nested.map(([k, v]) => (
        <KeyValueSection key={k} title={`${title} › ${k.replace(/_/g, " ")}`} obj={v as Record<string, unknown>} />
      ))}
    </div>
  );
}

function formatField(k: string, v: unknown): string {
  if (typeof v === "number") {
    if (k === "payout_ratio") return `${(v * 100).toFixed(1)}%`;
    if (k === "dividend_yield" || k === "dividend_cagr_5y" || k === "roe") return `${(v * 100).toFixed(2)}%`;
    if (k === "debt_to_equity") return v.toFixed(2);
    if (k === "market_cap" && v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
    if (k === "market_cap" && v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
    if (k === "current_price") return `$${v.toFixed(2)}`;
  }
  return fmtVal(v);
}

function fmtCheck(key: string, c: DgiFilterCheck): { actual: string; threshold: string } {
  let actual = typeof c.actual === "number" ? c.actual.toLocaleString(undefined, { maximumFractionDigits: 4 }) : String(c.actual);
  let threshold = typeof c.threshold === "number" ? c.threshold.toLocaleString(undefined, { maximumFractionDigits: 4 }) : String(c.threshold);
  const a = Number(c.actual);
  const t = Number(c.threshold);
  if (key === "min_yield" || key === "max_payout" || key === "min_growth") {
    actual = `${(a * 100).toFixed(2)}%`;
    threshold = `${(t * 100).toFixed(2)}%`;
  } else if (key === "min_market_cap") {
    actual = `$${(a / 1e9).toFixed(1)}B`;
    threshold = `$${(t / 1e9).toFixed(1)}B`;
  } else if (key === "min_years") {
    actual = `${Math.round(a)} yrs`;
    threshold = `${Math.round(t)} yrs`;
  }
  return { actual, threshold };
}

const RADAR_LABELS = ["Div Yield", "Div Growth", "Payout Safety", "Valuation", "Fin. Health", "Consistency"];
const RADAR_KEYS = ["dividend_yield", "dividend_growth", "payout_safety", "valuation", "financial_health", "consistency"];

function RadarChart({ entry }: { entry: DgiEntry }) {
  const qd = entry.quality_detail;
  if (!qd?.sub_scores) return null;
  const size = 320;
  const cx = size / 2;
  const cy = size / 2;
  const r = 110;
  const n = RADAR_KEYS.length;
  const angle = (i: number) => (Math.PI * 2 * i) / n - Math.PI / 2;
  const point = (i: number, value: number) => {
    const rad = (value / 100) * r;
    return [cx + rad * Math.cos(angle(i)), cy + rad * Math.sin(angle(i))];
  };
  const poly = (vals: number[]) => vals.map((v, i) => point(i, v).join(",")).join(" ");

  const data = RADAR_KEYS.map((k) => qd.sub_scores?.[k] ?? 0);
  const mins = RADAR_KEYS.map((k) => qd.minimum_thresholds?.[k] ?? 65);
  const ideals = RADAR_KEYS.map((k) => qd.ideal_thresholds?.[k] ?? 80);

  return (
    <svg width={size} height={size} role="img" aria-label="Score contribution radar" className="mx-auto">
      {[20, 40, 60, 80, 100].map((ring) => (
        <polygon
          key={ring}
          points={poly(RADAR_KEYS.map(() => ring))}
          fill="none"
          stroke="rgba(255,255,255,0.08)"
        />
      ))}
      {RADAR_KEYS.map((_, i) => {
        const [x, y] = point(i, 100);
        return <line key={i} x1={cx} y1={cy} x2={x} y2={y} stroke="rgba(255,255,255,0.08)" />;
      })}
      <polygon points={poly(ideals)} fill="none" stroke="rgba(34,197,94,0.85)" strokeWidth={1.5} strokeDasharray="6 4" />
      <polygon points={poly(mins)} fill="none" stroke="rgba(239,68,68,0.85)" strokeWidth={1.5} strokeDasharray="6 4" />
      <polygon points={poly(data)} fill="rgba(59,130,246,0.15)" stroke="rgba(59,130,246,0.9)" strokeWidth={2} />
      {RADAR_LABELS.map((label, i) => {
        const [x, y] = point(i, 118);
        return (
          <text key={label} x={x} y={y} textAnchor="middle" dominantBaseline="middle" fontSize={11} className="fill-text-muted">
            {label}
          </text>
        );
      })}
      <g>
        <rect x={8} y={size - 46} width={10} height={2} fill="rgba(59,130,246,0.9)" />
        <text x={22} y={size - 43} fontSize={10} className="fill-text-muted">{entry.symbol}</text>
        <rect x={8} y={size - 30} width={10} height={2} fill="rgba(239,68,68,0.85)" />
        <text x={22} y={size - 27} fontSize={10} className="fill-text-muted">Mínimo (65)</text>
        <rect x={8} y={size - 14} width={10} height={2} fill="rgba(34,197,94,0.85)" />
        <text x={22} y={size - 11} fontSize={10} className="fill-text-muted">Ideal (80)</text>
      </g>
    </svg>
  );
}

function ScoreEvolution({ history }: { history: DgiScorePoint[] }) {
  if (history.length <= 1) {
    return (
      <p className="py-4 text-center text-sm text-text-muted">
        Score history will appear after multiple screener runs
      </p>
    );
  }
  const w = Math.max(history.length * 40, 320);
  const h = 200;
  const pad = { top: 10, bottom: 30, left: 30, right: 10 };
  const plotW = w - pad.left - pad.right;
  const plotH = h - pad.top - pad.bottom;
  const x = (i: number) => pad.left + (history.length === 1 ? 0 : (i / (history.length - 1)) * plotW);
  const y = (v: number) => pad.top + (1 - v / 100) * plotH;
  const path = history.map((p, i) => `${i === 0 ? "M" : "L"}${x(i)},${y(p.score)}`).join(" ");
  const area = `${path} L${x(history.length - 1)},${pad.top + plotH} L${x(0)},${pad.top + plotH} Z`;

  return (
    <div className="overflow-x-auto">
      <svg width={w} height={h} role="img" aria-label="Score evolution">
        {[0, 25, 50, 75, 100].map((g) => (
          <g key={g}>
            <line x1={pad.left} y1={y(g)} x2={w - pad.right} y2={y(g)} stroke="rgba(255,255,255,0.08)" />
            <text x={pad.left - 6} y={y(g)} textAnchor="end" dominantBaseline="middle" fontSize={9} className="fill-text-muted">{g}</text>
          </g>
        ))}
        <path d={area} fill="rgba(96,165,250,0.1)" />
        <path d={path} fill="none" stroke="#60a5fa" strokeWidth={2} />
        {history.map((p, i) => (
          <circle key={i} cx={x(i)} cy={y(p.score)} r={3} fill="#60a5fa" />
        ))}
      </svg>
    </div>
  );
}

function DetailModal({ entry, onClose }: { entry: DgiEntry; onClose: () => void }) {
  const overview: Record<string, unknown> = {
    rank: entry.rank,
    quality_score: entry.quality_score,
    category: entry.category,
    entry_tag: entry.entry_tag,
    days_on_list: entry.days_on_list,
    first_appeared: entry.first_appeared,
  };
  const fd = entry.filter_detail;
  return (
    <div className="fixed inset-0 z-[200] flex items-start justify-center overflow-auto bg-black/60 p-4" onClick={onClose}>
      <div className="mt-12 w-full max-w-[700px] rounded-[var(--radius)] border border-border bg-bg-card" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <h3 className="text-lg font-semibold">{entry.symbol} — Detail</h3>
          <button type="button" onClick={onClose} className="text-xl text-text-muted hover:text-text">&times;</button>
        </div>
        <div className="px-5 py-4">
          <KeyValueSection title="Overview" obj={overview} />

          {fd?.checks && (
            <div className="mb-4">
              <h4 className="mb-2 text-xs font-medium uppercase tracking-wide text-text-muted">
                {fd.passes_all ? "✅" : "⚠️"} Filter Status
              </h4>
              <div className="rounded-[var(--radius)] bg-bg-input px-4 py-2">
                {Object.entries(fd.checks).map(([k, c], i, arr) => {
                  const { actual, threshold } = fmtCheck(k, c);
                  return (
                    <div
                      key={k}
                      className={`flex items-center justify-between py-1.5 ${i < arr.length - 1 ? "border-b border-border" : ""} ${c.passes ? "" : "text-accent-red"}`}
                    >
                      <span>{c.passes ? "✅" : "❌"} {c.label}</span>
                      <span className="font-mono text-sm">
                        {actual} <span className="text-xs text-text-muted">{c.op} {threshold}</span>
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {entry.metrics && <KeyValueSection title="Fundamentals" obj={entry.metrics} />}
          {entry.technicals && <KeyValueSection title="Technical Timing" obj={entry.technicals} />}

          {entry.quality_detail?.sub_scores && (
            <div className="mb-4">
              <h4 className="mb-2 text-xs font-medium uppercase tracking-wide text-text-muted">Score Contribution</h4>
              <div className="rounded-[var(--radius)] bg-bg-input px-4 py-3">
                <RadarChart entry={entry} />
              </div>
            </div>
          )}

          <div>
            <h4 className="mb-2 text-xs font-medium uppercase tracking-wide text-text-muted">Score Evolution</h4>
            <div className="rounded-[var(--radius)] bg-bg-input px-4 py-3">
              <ScoreEvolution history={entry.score_history ?? []} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Row action button (CSP / TBuy) ────────────────────────────────────

function AddButton({ entry, mode }: { entry: DgiEntry; mode: "csp" | "buy" }) {
  const [label, setLabel] = useState(mode === "csp" ? "CSP" : "TBuy");
  const [busy, setBusy] = useState(false);

  async function onClick(e: React.MouseEvent) {
    e.stopPropagation();
    setBusy(true);
    setLabel("⏳");
    const exchange = entry.exchange || "NYSE";
    const flag = mode === "csp" ? { cash_secured_put: true } : { buy_tracker: true };
    try {
      const res = await fetch("/api/symbols", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol: entry.symbol, exchange, ...flag }),
      });
      if (res.status === 409) {
        const put = await fetch(`/api/symbols/${encodeURIComponent(entry.symbol)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(flag),
        });
        setLabel(put.ok ? "✓" : "✗");
      } else {
        setLabel(res.ok ? "✓" : "✗");
      }
    } catch {
      setLabel("✗");
    } finally {
      setTimeout(() => {
        setLabel(mode === "csp" ? "CSP" : "TBuy");
        setBusy(false);
      }, 4000);
    }
  }

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      title={`Add ${entry.symbol} with ${mode === "csp" ? "CSP" : "Buy Tracker"} enabled`}
      className="rounded border border-border bg-bg-input px-2 py-0.5 text-xs text-text-muted hover:text-text disabled:opacity-60"
    >
      {label}
    </button>
  );
}

// ── Filters state ──────────────────────────────────────────────────────

const DEFAULT_FILTERS = { qs: 0, dy: 0, dg: 0, years: 0, timing: 0, priceMin: 0, priceMax: 1000 };

export default function DgiScreenerView({ entries }: { entries: DgiEntry[] }) {
  const router = useRouter();
  const [analyze, setAnalyze] = useState("");
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [sort, setSort] = useState<SortState>({ col: "rank", asc: true });
  const [detail, setDetail] = useState<DgiEntry | null>(null);

  const filtered = useMemo(() => {
    return entries.filter((e) => {
      const qs = num(e.quality_score);
      const dy = num(e.metrics?.dividend_yield) * 100;
      const dg = num(e.metrics?.dividend_cagr_5y) * 100;
      const years = num(e.metrics?.years_consecutive_increases);
      const timing = num(e.technicals?.score);
      const price = num(e.metrics?.current_price);
      return (
        qs >= filters.qs &&
        dy >= filters.dy / 10 &&
        dg >= filters.dg &&
        years >= filters.years &&
        timing >= filters.timing &&
        price >= filters.priceMin &&
        price <= filters.priceMax
      );
    });
  }, [entries, filters]);

  const sorted = useMemo(() => {
    const val = (e: DgiEntry): number | string => {
      switch (sort.col) {
        case "rank": return num(e.rank);
        case "symbol": return e.symbol;
        case "category": return e.category ?? "";
        case "score": return num(e.quality_score);
        case "dividend_yield": return num(e.metrics?.dividend_yield);
        case "dividend_growth_cagr": return num(e.metrics?.dividend_cagr_5y);
        case "years_of_growth": return num(e.metrics?.years_consecutive_increases);
        case "days_on_list": return num(e.days_on_list);
        case "technical_timing_score": return num(e.technicals?.score);
        case "entry_tag": return e.entry_tag ?? "";
        case "current_price": return num(e.metrics?.current_price);
        default: return 0;
      }
    };
    return [...filtered].sort((a, b) => {
      const av = val(a);
      const bv = val(b);
      if (typeof av === "number" && typeof bv === "number") return sort.asc ? av - bv : bv - av;
      return sort.asc ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
    });
  }, [filtered, sort]);

  function onSort(col: string) {
    setSort((s) => ({ col, asc: s.col === col ? !s.asc : true }));
  }

  function applyBeasts() {
    setFilters({ qs: 70, dy: 25, dg: 10, years: 10, timing: 90, priceMin: 0, priceMax: 1000 });
  }

  function goAnalyze() {
    const sym = analyze.trim().toUpperCase();
    if (sym) router.push(`/dgi/analyze/${encodeURIComponent(sym)}`);
  }

  const COLS: { col: string; label: string; sortable: boolean }[] = [
    { col: "rank", label: "#", sortable: true },
    { col: "symbol", label: "Symbol", sortable: true },
    { col: "category", label: "Category", sortable: true },
    { col: "score", label: "Quality Score", sortable: true },
    { col: "dividend_yield", label: "Div Yield %", sortable: true },
    { col: "dividend_growth_cagr", label: "Div Growth CAGR %", sortable: true },
    { col: "years_of_growth", label: "Years", sortable: true },
    { col: "days_on_list", label: "Days on List", sortable: true },
    { col: "technical_timing_score", label: "Timing", sortable: true },
    { col: "entry_tag", label: "Entry", sortable: true },
    { col: "current_price", label: "Price", sortable: true },
    { col: "actions", label: "", sortable: false },
  ];

  const arrow = (col: string) => (sort.col === col ? (sort.asc ? " ▲" : " ▼") : "");

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">DGI Screener</h1>
          <p className="text-sm text-text-muted">Top Dividend Growth Stocks</p>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={analyze}
            onChange={(e) => setAnalyze(e.target.value.toUpperCase())}
            onKeyDown={(e) => e.key === "Enter" && goAnalyze()}
            placeholder="🔍 Analyze symbol (e.g. AAPL)"
            maxLength={10}
            className="w-56 rounded-[var(--radius)] border border-border bg-bg-input px-3 py-1.5 text-sm uppercase text-text"
          />
          <button type="button" onClick={goAnalyze} className="rounded-[var(--radius-pill)] bg-accent-blue px-3 py-1.5 text-sm text-white">
            Go
          </button>
        </div>
      </div>

      {entries.length === 0 ? (
        <div className="rounded-[var(--radius)] border border-border bg-bg-card px-4 py-8 text-center text-text-muted">
          No DGI screening results yet. Run the screener to populate this table.
        </div>
      ) : (
        <>
          {/* Filters */}
          <div className="rounded-[var(--radius)] border border-border bg-bg-card p-4">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-lg font-semibold">Filters</h2>
              <span className="rounded-[var(--radius-pill)] border border-border bg-bg-input px-2 py-0.5 text-xs text-text-muted">
                Showing {filtered.length} of {entries.length}
              </span>
            </div>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <Slider label="Quality Score ≥" value={filters.qs} display={String(filters.qs)} onChange={(v) => setFilters((f) => ({ ...f, qs: v }))} />
              <Slider label="Div Yield ≥" value={filters.dy} display={`${(filters.dy / 10).toFixed(1)}%`} onChange={(v) => setFilters((f) => ({ ...f, dy: v }))} />
              <Slider label="Div Growth ≥" value={filters.dg} display={`${filters.dg}%`} onChange={(v) => setFilters((f) => ({ ...f, dg: v }))} />
              <Slider label="Years ≥" value={filters.years} display={String(filters.years)} onChange={(v) => setFilters((f) => ({ ...f, years: v }))} />
              <Slider label="Timing ≥" value={filters.timing} display={String(filters.timing)} onChange={(v) => setFilters((f) => ({ ...f, timing: v }))} />
              <div>
                <label className="mb-1 block text-xs text-text-muted">
                  💲 Price: ${filters.priceMin} – ${filters.priceMax}
                </label>
                <div className="flex items-center gap-2">
                  <input
                    type="range" min={0} max={1000} step={5} value={filters.priceMin}
                    onChange={(e) => setFilters((f) => ({ ...f, priceMin: Math.min(Number(e.target.value), f.priceMax) }))}
                    className="w-full"
                  />
                  <input
                    type="range" min={0} max={1000} step={5} value={filters.priceMax}
                    onChange={(e) => setFilters((f) => ({ ...f, priceMax: Math.max(Number(e.target.value), f.priceMin) }))}
                    className="w-full"
                  />
                </div>
              </div>
            </div>
            <div className="mt-3 flex gap-2">
              <button type="button" onClick={applyBeasts} className="rounded border border-border bg-bg-input px-3 py-1 text-xs hover:text-text">🐂 Beasts</button>
              <button type="button" onClick={() => setFilters(DEFAULT_FILTERS)} className="rounded border border-border bg-bg-input px-3 py-1 text-xs hover:text-text">Reset Filters</button>
            </div>
          </div>

          {/* Table */}
          <div className="overflow-x-auto rounded-[var(--radius)] border border-border bg-bg-card">
            <table className="w-full min-w-[1000px] text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
                  {COLS.map((c) => (
                    <th
                      key={c.col}
                      onClick={() => c.sortable && onSort(c.col)}
                      className={`px-3 py-2 font-medium ${c.sortable ? "cursor-pointer select-none" : ""}`}
                    >
                      {c.label}{c.sortable ? arrow(c.col) : ""}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sorted.map((e, i) => {
                  const score = num(e.quality_score);
                  return (
                    <tr
                      key={e.symbol}
                      onClick={() => setDetail(e)}
                      className="cursor-pointer border-b border-border/60 last:border-0 hover:bg-bg-hover"
                    >
                      <td className="px-3 py-2 font-mono">{e.rank ?? i + 1}</td>
                      <td className="px-3 py-2 font-semibold">{e.symbol}</td>
                      <td className="px-3 py-2">
                        <span className="inline-block rounded-[var(--radius-pill)] border border-border bg-bg-input px-2 py-0.5 text-xs text-text-muted">
                          {e.category ?? "—"}
                        </span>
                      </td>
                      <td className="px-3 py-2 font-mono">
                        <div className="flex items-center gap-2">
                          <span className="h-1.5 w-16 overflow-hidden rounded-full bg-bg-input">
                            <span className="block h-full rounded-full bg-accent-blue" style={{ width: `${score}%` }} />
                          </span>
                          <span className={scoreClass(score)}>{score.toFixed(1)}</span>
                        </div>
                      </td>
                      <td className="px-3 py-2 font-mono">{(num(e.metrics?.dividend_yield) * 100).toFixed(2)}%</td>
                      <td className="px-3 py-2 font-mono">{(num(e.metrics?.dividend_cagr_5y) * 100).toFixed(1)}%</td>
                      <td className="px-3 py-2 font-mono">{e.metrics?.years_consecutive_increases ?? "—"}</td>
                      <td className="px-3 py-2 font-mono">{e.days_on_list ?? 0}</td>
                      <td className="px-3 py-2 font-mono">{num(e.technicals?.score).toFixed(1)}</td>
                      <td className="px-3 py-2">
                        <span className="inline-block rounded-[var(--radius-pill)] border border-border bg-bg-input px-2 py-0.5 text-xs text-text-muted">
                          {e.entry_tag ?? "—"}
                        </span>
                      </td>
                      <td className="px-3 py-2 font-mono">${num(e.metrics?.current_price).toFixed(2)}</td>
                      <td className="px-3 py-2" onClick={(ev) => ev.stopPropagation()}>
                        <span className="inline-flex gap-1">
                          <AddButton entry={e} mode="csp" />
                          <AddButton entry={e} mode="buy" />
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {detail && <DetailModal entry={detail} onClose={() => setDetail(null)} />}
    </div>
  );
}

function Slider({
  label,
  value,
  display,
  onChange,
}: {
  label: string;
  value: number;
  display: string;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <label className="mb-1 block text-xs text-text-muted">
        {label} <span className="text-text">{display}</span>
      </label>
      <input
        type="range"
        min={0}
        max={100}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full"
      />
    </div>
  );
}
