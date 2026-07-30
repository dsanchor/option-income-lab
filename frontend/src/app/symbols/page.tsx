import Link from "next/link";
import { apiFetch } from "@/lib/api";
import { usd, timeAgo } from "@/lib/format";
import type { SymbolsOverview, SymbolRow } from "@/types/symbols";

export const dynamic = "force-dynamic";

async function getData(): Promise<SymbolsOverview> {
  try {
    return await apiFetch<SymbolsOverview>("/api/symbols/overview");
  } catch (err) {
    return { error: err instanceof Error ? err.message : "Failed to load symbols" };
  }
}

function num(n: number | null | undefined, digits = 1): string {
  return typeof n === "number" && isFinite(n) ? n.toFixed(digits) : "—";
}

function entryClass(tag: string): string {
  const t = tag.toLowerCase();
  if (t === "strong buy" || t === "buy") return "text-accent-green border-accent-green/40 bg-accent-green/10";
  if (t === "accumulate") return "text-accent-blue border-accent-blue/40 bg-accent-blue/10";
  if (t === "wait") return "text-accent-red border-accent-red/40 bg-accent-red/10";
  return "text-text-muted border-border bg-bg-input";
}

function momentumClass(m: string): string {
  const t = m.toLowerCase();
  if (t.startsWith("bullish")) return "text-accent-green border-accent-green/40 bg-accent-green/10";
  if (t.startsWith("bearish")) return "text-accent-red border-accent-red/40 bg-accent-red/10";
  if (t === "weakening") return "text-accent-orange border-accent-orange/40 bg-accent-orange/10";
  return "text-text-muted border-border bg-bg-input";
}

function scoreClass(s: number | null): string {
  if (s == null) return "text-text-muted";
  if (s >= 70) return "text-accent-green";
  if (s >= 40) return "text-text";
  return "text-accent-red";
}

function Pill({ text, className }: { text: string; className: string }) {
  if (!text) return <span className="text-text-muted">—</span>;
  return (
    <span className={`inline-block rounded-[var(--radius-pill)] border px-2 py-0.5 text-xs ${className}`}>
      {text}
    </span>
  );
}

export default async function SymbolsPage() {
  const d = await getData();

  if (d.error) {
    return (
      <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
        ⚠️ {d.error}
      </div>
    );
  }

  const rows = d.rows ?? [];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Symbols</h1>
          {d.last_update_ts && (
            <p className="text-sm text-text-muted">Last enrichment {timeAgo(d.last_update_ts)}</p>
          )}
        </div>
        <div className="flex gap-4 text-sm text-text-muted">
          <span>{d.symbol_count ?? rows.length} tracked</span>
          <span>Calls exposure <span className="text-text">${usd(d.total_call_exposure)}</span></span>
          <span>Puts committed <span className="text-text">${usd(d.total_put_exposure)}</span></span>
        </div>
      </div>

      <div className="overflow-x-auto rounded-[var(--radius)] border border-border bg-bg-card">
        <table className="w-full min-w-[860px] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
              <th className="px-4 py-3 font-medium">Symbol</th>
              <th className="px-4 py-3 font-medium">Category</th>
              <th className="px-4 py-3 text-right font-medium">DGI</th>
              <th className="px-4 py-3 text-right font-medium">Tech</th>
              <th className="px-4 py-3 font-medium">Entry</th>
              <th className="px-4 py-3 font-medium">Momentum</th>
              <th className="px-4 py-3 text-right font-medium">Price</th>
              <th className="px-4 py-3 text-right font-medium">Shares</th>
              <th className="px-4 py-3 text-right font-medium">In Calls</th>
              <th className="px-4 py-3 text-right font-medium">Puts $</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={10} className="px-4 py-8 text-center text-text-muted">
                  No symbols tracked yet.
                </td>
              </tr>
            )}
            {rows.map((r: SymbolRow) => (
              <tr key={r.symbol} className="border-b border-border/60 last:border-0 hover:bg-bg-input/50">
                <td className="px-4 py-3">
                  <Link href={`/symbols/${r.symbol}`} className="font-semibold text-text hover:text-accent-blue">
                    {r.symbol}
                  </Link>
                </td>
                <td className="px-4 py-3">
                  <Pill text={r.category} className="text-text-muted border-border bg-bg-input" />
                </td>
                <td className={`px-4 py-3 text-right font-mono ${scoreClass(r.dgi_score)}`}>{num(r.dgi_score)}</td>
                <td className="px-4 py-3 text-right font-mono">{num(r.tech_timing)}</td>
                <td className="px-4 py-3"><Pill text={r.entry_tag} className={entryClass(r.entry_tag)} /></td>
                <td className="px-4 py-3"><Pill text={r.momentum} className={momentumClass(r.momentum)} /></td>
                <td className="px-4 py-3 text-right font-mono">{r.price != null ? `$${num(r.price, 2)}` : "—"}</td>
                <td className="px-4 py-3 text-right font-mono">{r.total_shares > 0 ? r.total_shares : "—"}</td>
                <td className="px-4 py-3 text-right font-mono">
                  <span className={r.total_shares > 0 && r.in_calls >= r.total_shares ? "text-accent-orange" : ""}>
                    {r.in_calls > 0 ? r.in_calls : "—"}
                  </span>
                </td>
                <td className="px-4 py-3 text-right font-mono">{r.put_exposure > 0 ? `$${usd(r.put_exposure)}` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
