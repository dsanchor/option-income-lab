import Link from "next/link";
import type {
  DgiAnalysis,
  DgiAnalysisQualityDetail,
} from "@/types/dgi";
import DgiAnalyzeSearch from "./DgiAnalyzeSearch";

function scoreClass(v: number): string {
  if (v >= 70) return "text-accent-green";
  if (v >= 40) return "text-accent-orange";
  return "text-accent-red";
}

function barColor(v: number): string {
  if (v >= 70) return "var(--color-accent-green)";
  if (v >= 40) return "var(--color-accent-orange)";
  return "var(--color-accent-red)";
}

function pct(v: number): string {
  // dividend_yield may arrive decimal (<1) or already-percent (>=1).
  return `${(v < 1 ? v * 100 : v).toFixed(2)}%`;
}

function marketCap(v: number): string {
  if (v >= 1_000_000_000) return `$${(v / 1_000_000_000).toFixed(1)}B`;
  if (v > 0) return `$${(v / 1_000_000).toFixed(0)}M`;
  return "—";
}

/** Horizontal score bar with a label, raw value and 0-100 score. */
function ScoreBar({
  label,
  score,
  detail,
  weight,
  height = 6,
}: {
  label: string;
  score: number;
  detail: string;
  weight?: number;
  height?: number;
}) {
  return (
    <div className="mb-4">
      <div className="mb-1 flex items-baseline justify-between">
        <span className="text-sm font-medium">{label}</span>
        <span className="font-mono text-sm">
          <span className="text-text-muted">{detail}</span>
          {" · "}
          <strong className={scoreClass(score)}>{Math.round(score)}</strong>
          <span className="text-xs text-text-muted">
            {" "}
            / 100{weight != null && ` (${Math.round(weight * 100)}%)`}
          </span>
        </span>
      </div>
      <div
        className="overflow-hidden rounded-full bg-bg-input"
        style={{ height }}
      >
        <div
          className="h-full rounded-full"
          style={{ width: `${Math.max(0, Math.min(100, score))}%`, background: barColor(score) }}
        />
      </div>
    </div>
  );
}

const RADAR_KEYS = [
  "dividend_yield",
  "dividend_growth",
  "payout_safety",
  "valuation",
  "financial_health",
  "consistency",
];
const RADAR_LABELS = [
  "Div Yield",
  "Div Growth",
  "Payout Safety",
  "Valuation",
  "Fin. Health",
  "Consistency",
];

/** Dependency-free SVG radar chart of the 6 fundamental sub-scores vs. thresholds. */
function RadarChart({ qd, symbol }: { qd: DgiAnalysisQualityDetail; symbol: string }) {
  const size = 340;
  const cx = size / 2;
  const cy = size / 2;
  const r = size / 2 - 60;
  const n = RADAR_KEYS.length;
  const max = 100;

  const point = (i: number, value: number) => {
    const angle = (Math.PI * 2 * i) / n - Math.PI / 2;
    const rad = (Math.max(0, Math.min(max, value)) / max) * r;
    return [cx + rad * Math.cos(angle), cy + rad * Math.sin(angle)];
  };
  const polygon = (vals: number[]) =>
    vals.map((v, i) => point(i, v).join(",")).join(" ");

  const data = RADAR_KEYS.map((k) => qd.sub_scores?.[k] ?? 0);
  const minLine = RADAR_KEYS.map((k) => qd.minimum_thresholds?.[k] ?? 0);
  const idealLine = RADAR_KEYS.map((k) => qd.ideal_thresholds?.[k] ?? 0);

  const rings = [20, 40, 60, 80, 100];

  return (
    <div className="mx-auto max-w-[400px]">
      <svg width="100%" viewBox={`0 0 ${size} ${size}`} role="img" aria-label="Score contribution radar">
        {/* rings */}
        {rings.map((ring) => (
          <polygon
            key={ring}
            points={polygon(RADAR_KEYS.map(() => ring))}
            fill="none"
            stroke="rgba(255,255,255,0.08)"
          />
        ))}
        {/* axes + labels */}
        {RADAR_KEYS.map((_, i) => {
          const [x, y] = point(i, max);
          const [lx, ly] = point(i, max + 22);
          return (
            <g key={i}>
              <line x1={cx} y1={cy} x2={x} y2={y} stroke="rgba(255,255,255,0.08)" />
              <text
                x={lx}
                y={ly}
                textAnchor="middle"
                dominantBaseline="middle"
                fontSize={11}
                className="fill-text-muted"
              >
                {RADAR_LABELS[i]}
              </text>
            </g>
          );
        })}
        {/* ideal (green dashed) */}
        <polygon
          points={polygon(idealLine)}
          fill="none"
          stroke="rgba(34,197,94,0.85)"
          strokeWidth={1.5}
          strokeDasharray="6 4"
        />
        {/* minimum (red dashed) */}
        <polygon
          points={polygon(minLine)}
          fill="none"
          stroke="rgba(239,68,68,0.85)"
          strokeWidth={1.5}
          strokeDasharray="6 4"
        />
        {/* actual (blue filled) */}
        <polygon
          points={polygon(data)}
          fill="rgba(59,130,246,0.15)"
          stroke="rgba(59,130,246,0.85)"
          strokeWidth={2}
        />
        {data.map((v, i) => {
          const [x, y] = point(i, v);
          return <circle key={i} cx={x} cy={y} r={3} fill="rgba(59,130,246,1)" />;
        })}
      </svg>
      <div className="mt-2 flex flex-wrap justify-center gap-4 text-xs text-text-muted">
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-3 rounded-sm bg-accent-blue" /> {symbol}
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-0 w-3 border-t-2 border-dashed border-accent-red" /> Mínimo (65)
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-0 w-3 border-t-2 border-dashed border-accent-green" /> Ideal (80)
        </span>
      </div>
    </div>
  );
}

const cardCls = "rounded-[var(--radius)] border border-border bg-bg-card p-5";

export default function DgiAnalysisView({ result }: { result: DgiAnalysis }) {
  const m = result.metrics;
  const t = result.technicals;
  const qd = result.quality_detail;
  const sub = qd.sub_scores;
  const w = qd.weights;
  const qs = result.quality_score;
  const techScore = t.score ?? 0;

  const fundItems: { label: string; score: number; weight: number; display: string }[] = [
    {
      label: "Dividend Yield",
      score: sub.dividend_yield ?? 0,
      weight: w.dividend_yield ?? 0,
      display: pct(m.dividend_yield),
    },
    {
      label: "Dividend Growth (CAGR 5y)",
      score: sub.dividend_growth ?? 0,
      weight: w.dividend_growth ?? 0,
      display: `${(m.dividend_cagr_5y * 100).toFixed(1)}%`,
    },
    {
      label: "Payout Safety",
      score: sub.payout_safety ?? 0,
      weight: w.payout_safety ?? 0,
      display: `${(m.payout_ratio * 100).toFixed(0)}%`,
    },
    {
      label: "Valuation (PE)",
      score: sub.valuation ?? 0,
      weight: w.valuation ?? 0,
      display: m.pe_ratio.toFixed(1),
    },
    {
      label: "Financial Health",
      score: sub.financial_health ?? 0,
      weight: w.financial_health ?? 0,
      display: `D/E: ${m.debt_to_equity.toFixed(2)} · ROE: ${(m.roe * 100).toFixed(1)}%`,
    },
    {
      label: "Consistency (Years)",
      score: sub.consistency ?? 0,
      weight: w.consistency ?? 0,
      display: `${m.years_consecutive_increases} years`,
    },
  ];

  const techItems: { label: string; score: number; detail: string }[] = t.sub_scores
    ? [
        {
          label: "RSI Score (30%)",
          score: t.sub_scores.rsi_score ?? 0,
          detail: `RSI: ${(t.rsi ?? 0).toFixed(1)}`,
        },
        {
          label: "SMA Score (25%)",
          score: t.sub_scores.sma_score ?? 0,
          detail: `SMA50: ${(t.sma_50 ?? 0).toFixed(2)} · SMA200: ${(t.sma_200 ?? 0).toFixed(2)}`,
        },
        {
          label: "52w High Distance (25%)",
          score: t.sub_scores.high_dist_score ?? 0,
          detail: `52w H: $${(t.high_52w ?? 0).toFixed(2)} · L: $${(t.low_52w ?? 0).toFixed(2)}`,
        },
        {
          label: "Bollinger Bands (20%)",
          score: t.sub_scores.bb_score ?? 0,
          detail: `Position: ${(t.bb?.position ?? 0).toFixed(2)}`,
        },
      ]
    : [];

  const metricItems: [string, string][] = [
    ["Dividend Yield", pct(m.dividend_yield)],
    ["Div Growth CAGR 5y", `${(m.dividend_cagr_5y * 100).toFixed(1)}%`],
    ["Consecutive Years", String(m.years_consecutive_increases)],
    ["Payout Ratio", `${(m.payout_ratio * 100).toFixed(0)}%`],
    ["Trailing PE", m.pe_ratio.toFixed(1)],
    ["Forward PE", m.forward_pe.toFixed(1)],
    ["Debt/Equity", m.debt_to_equity.toFixed(2)],
    ["ROE", `${(m.roe * 100).toFixed(1)}%`],
    ["Market Cap", marketCap(m.market_cap)],
    ["Sector", m.sector || "—"],
    ["Exchange", m.exchange || "—"],
    ["Momentum", result.momentum || "—"],
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">DGI Symbol Analysis</h1>
        <p className="text-sm text-text-muted">
          {result.symbol} — {result.name}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Link
          href="/dgi"
          className="rounded-[var(--radius-pill)] border border-border bg-bg-input px-4 py-2 text-sm text-text-muted transition-colors hover:bg-hover"
        >
          ← Back to Screener
        </Link>
        <DgiAnalyzeSearch />
      </div>

      {/* Overall score card */}
      <div className={cardCls}>
        <div className="grid grid-cols-2 gap-6 text-center md:grid-cols-4">
          <div>
            <div className="mb-2 text-xs uppercase tracking-wide text-text-muted">Quality Score</div>
            <div className={`text-4xl font-bold ${scoreClass(qs)}`}>{qs.toFixed(1)}</div>
            <div className="mt-2 overflow-hidden rounded-full bg-bg-input" style={{ height: 8 }}>
              <div
                className="h-full rounded-full"
                style={{ width: `${qs}%`, background: barColor(qs) }}
              />
            </div>
          </div>
          <div>
            <div className="mb-2 text-xs uppercase tracking-wide text-text-muted">Entry Tag</div>
            <div className="text-lg">
              <span className="rounded-[var(--radius-pill)] border border-accent-blue/40 bg-accent-blue/10 px-3 py-1 text-sm text-accent-blue">
                {result.entry_tag}
              </span>
            </div>
          </div>
          <div>
            <div className="mb-2 text-xs uppercase tracking-wide text-text-muted">Category</div>
            <div className="text-lg">
              <span className="rounded-[var(--radius-pill)] border border-accent-purple/40 bg-accent-purple/10 px-3 py-1 text-sm text-accent-purple">
                {result.category}
              </span>
            </div>
          </div>
          <div>
            <div className="mb-2 text-xs uppercase tracking-wide text-text-muted">Price</div>
            <div className="font-mono text-3xl font-semibold">${m.current_price.toFixed(2)}</div>
          </div>
        </div>

        {!result.has_dividends && (
          <div className="mt-4 rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
            ⚠️ This stock has no dividend history — DGI scores may not be meaningful.
          </div>
        )}
        {!result.passes_minimum_filters && (
          <div className="mt-3 rounded-[var(--radius)] bg-bg-input px-4 py-2 text-sm text-text-muted">
            ℹ️ This stock does not pass all DGI quality filters (yield, payout, PE, D/E, years,
            market cap, growth) — however, it may still appear in the screener&apos;s top list if its
            overall score is high enough.
          </div>
        )}
      </div>

      {/* Breakdown */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Fundamentals */}
        <div className={cardCls}>
          <h3 className="mb-4 text-xs uppercase tracking-wide text-text-muted">
            Fundamental Scores (70% weight)
          </h3>
          {fundItems.map((f) => (
            <ScoreBar key={f.label} label={f.label} score={f.score} detail={f.display} weight={f.weight} />
          ))}
          {qd.health_detail && (
            <div className="mt-2 rounded-[var(--radius)] bg-bg-input px-4 py-3">
              <div className="mb-1 text-sm text-text-muted">Health Detail</div>
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  D/E Score:{" "}
                  <strong className="font-mono">
                    {Math.round(qd.health_detail.debt_to_equity_score)}
                  </strong>
                </div>
                <div>
                  ROE Score:{" "}
                  <strong className="font-mono">{Math.round(qd.health_detail.roe_score)}</strong>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Technicals */}
        <div className={cardCls}>
          <h3 className="mb-4 text-xs uppercase tracking-wide text-text-muted">
            Technical Timing (informational)
          </h3>
          <div className="mb-5">
            <div className="mb-1 flex items-baseline justify-between">
              <span className="text-base font-semibold">Combined Score</span>
              <span className="font-mono">
                <strong className={scoreClass(techScore)}>{techScore.toFixed(1)}</strong>
                <span className="text-xs text-text-muted"> / 100</span>
              </span>
            </div>
            <div className="overflow-hidden rounded-full bg-bg-input" style={{ height: 8 }}>
              <div
                className="h-full rounded-full"
                style={{ width: `${techScore}%`, background: barColor(techScore) }}
              />
            </div>
          </div>
          {techItems.length > 0 ? (
            techItems.map((ti) => (
              <ScoreBar key={ti.label} label={ti.label} score={ti.score} detail={ti.detail} height={5} />
            ))
          ) : (
            <p className="text-sm text-text-muted">No technical data available for this symbol.</p>
          )}
        </div>
      </div>

      {/* Key metrics */}
      <div className={cardCls}>
        <h3 className="mb-4 text-xs uppercase tracking-wide text-text-muted">Key Metrics</h3>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4">
          {metricItems.map(([label, value]) => (
            <div key={label} className="rounded-[var(--radius)] bg-bg-input px-4 py-3">
              <div className="mb-1 text-xs uppercase tracking-wide text-text-muted">{label}</div>
              <div className="font-mono text-base font-semibold">{value}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Radar */}
      <div className={cardCls}>
        <h3 className="mb-4 text-xs uppercase tracking-wide text-text-muted">Score Contribution</h3>
        <RadarChart qd={qd} symbol={result.symbol} />
      </div>
    </div>
  );
}
