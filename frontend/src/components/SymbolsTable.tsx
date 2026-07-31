"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { usd } from "@/lib/format";
import { categoryClass, entryClass } from "@/lib/badges";
import SymbolInfoModal from "@/components/SymbolInfoModal";
import type { SymbolRow } from "@/types/symbols";

function num(n: number | null | undefined, digits = 1): string {
  return typeof n === "number" && isFinite(n) ? n.toFixed(digits) : "—";
}
function momentumClass(m: string): string {
  const t = (m || "").toLowerCase();
  if (t.startsWith("bullish")) return "text-accent-green border-accent-green/40 bg-accent-green/10";
  if (t.startsWith("bearish")) return "text-accent-red border-accent-red/40 bg-accent-red/10";
  if (t === "weakening") return "text-accent-orange border-accent-orange/40 bg-accent-orange/10";
  return "text-text-muted border-border bg-bg-input";
}
function Pill({ text, className }: { text: string; className: string }) {
  if (!text) return <span className="text-text-muted">—</span>;
  return <span className={`inline-block rounded-[var(--radius-pill)] border px-2 py-0.5 text-xs ${className}`}>{text}</span>;
}

type SortKey =
  | "symbol" | "category" | "dgi_score" | "tech_timing" | "entry_tag"
  | "momentum" | "price" | "total_shares" | "in_calls" | "put_exposure";

const COLUMNS: { key: SortKey; label: string; align?: "right"; num?: boolean }[] = [
  { key: "symbol", label: "Symbol" },
  { key: "category", label: "Category" },
  { key: "dgi_score", label: "DGI", align: "right", num: true },
  { key: "tech_timing", label: "Tech", align: "right", num: true },
  { key: "entry_tag", label: "Entry" },
  { key: "momentum", label: "Momentum" },
  { key: "price", label: "Price", align: "right", num: true },
  { key: "total_shares", label: "Shares", align: "right", num: true },
  { key: "in_calls", label: "In Calls", align: "right", num: true },
  { key: "put_exposure", label: "Puts $", align: "right", num: true },
];

export default function SymbolsTable({ rows }: { rows: SymbolRow[] }) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<SortKey>("symbol");
  const [dir, setDir] = useState<"asc" | "desc">("asc");
  const [modalSymbol, setModalSymbol] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const query = q.trim().toUpperCase();
    let out = rows;
    if (query) {
      out = rows.filter(
        (r) =>
          r.symbol?.toUpperCase().includes(query) ||
          (r.category || "").toUpperCase().includes(query) ||
          (r.entry_tag || "").toUpperCase().includes(query),
      );
    }
    const sorted = [...out].sort((a, b) => {
      const av = a[sort] as unknown;
      const bv = b[sort] as unknown;
      let cmp: number;
      if (typeof av === "number" || typeof bv === "number") {
        cmp = (Number(av) || -Infinity) - (Number(bv) || -Infinity);
      } else {
        cmp = String(av ?? "").localeCompare(String(bv ?? ""));
      }
      return dir === "asc" ? cmp : -cmp;
    });
    return sorted;
  }, [rows, q, sort, dir]);

  function toggleSort(key: SortKey) {
    if (sort === key) setDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSort(key);
      setDir(key === "symbol" || key === "category" ? "asc" : "desc");
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="🔍 Filter symbols…"
          className="w-full max-w-xs rounded-[var(--radius-pill)] border border-border bg-bg-input px-3.5 py-1.5 text-sm text-text placeholder:text-text-muted focus:border-accent-blue focus:outline-none"
        />
        <span className="shrink-0 text-xs text-text-muted">{filtered.length} of {rows.length}</span>
      </div>

      <div className="surface table-modern overflow-x-auto">
        <table className="w-full min-w-[860px] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
              {COLUMNS.map((c) => (
                <th
                  key={c.key}
                  onClick={() => toggleSort(c.key)}
                  className={`cursor-pointer select-none px-4 py-3 font-medium transition-colors hover:text-text ${c.align === "right" ? "text-right" : ""}`}
                >
                  {c.label}
                  <span className="ml-1 text-[0.65rem]">{sort === c.key ? (dir === "asc" ? "▲" : "▼") : ""}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr>
                <td colSpan={COLUMNS.length} className="px-4 py-8 text-center text-text-muted">
                  No symbols match.
                </td>
              </tr>
            )}
            {filtered.map((r) => (
              <tr
                key={r.symbol}
                onClick={() => setModalSymbol(r.symbol)}
                className="cursor-pointer border-b border-border/60 transition-colors last:border-0 hover:bg-bg-hover/50"
              >
                <td className="px-4 py-3">
                  <Link
                    href={`/symbols/${r.symbol}`}
                    onClick={(e) => e.stopPropagation()}
                    className="font-semibold text-text hover:text-accent-blue"
                  >
                    {r.symbol}
                  </Link>
                </td>
                <td className="px-4 py-3"><Pill text={r.category} className={categoryClass(r.category)} /></td>
                <td className="px-4 py-3 text-right font-mono">{num(r.dgi_score)}</td>
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

      {modalSymbol && (
        <SymbolInfoModal symbol={modalSymbol} onClose={() => setModalSymbol(null)} />
      )}
    </div>
  );
}
