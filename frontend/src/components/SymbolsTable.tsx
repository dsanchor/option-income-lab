"use client";

import Link from "next/link";
import { useMemo, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Trash2 } from "lucide-react";
import { usd } from "@/lib/format";
import { categoryClass, entryClass } from "@/lib/badges";
import {
  matchesSymbolSuitability,
  type SymbolSuitabilityFilter,
} from "@/lib/symbolSuitability";
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

const SUITABILITY_FILTERS: { key: SymbolSuitabilityFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "ideal_puts", label: "Ideal Puts" },
  { key: "ideal_calls", label: "Ideal Calls" },
  { key: "no_puts", label: "No Puts" },
  { key: "no_calls", label: "No Calls" },
];

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
  const router = useRouter();
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<SortKey>("symbol");
  const [dir, setDir] = useState<"asc" | "desc">("asc");
  const [suitabilityFilter, setSuitabilityFilter] = useState<SymbolSuitabilityFilter>("all");
  const [modalSymbol, setModalSymbol] = useState<string | null>(null);

  // Inline shares editing
  const [editingShares, setEditingShares] = useState<string | null>(null);
  const [sharesInput, setSharesInput] = useState("");
  const [sharesSaving, setSharesSaving] = useState<string | null>(null);
  const [sharesError, setSharesError] = useState<string | null>(null);
  const [localShares, setLocalShares] = useState<Record<string, number>>({});

  // Delete symbol — tracked as a Set so a second, unrelated concurrent
  // delete never re-enables (or duplicates the DELETE for) a symbol whose
  // own request is still in flight.
  const [deletingSymbols, setDeletingSymbols] = useState<Set<string>>(new Set());
  const [removedSymbols, setRemovedSymbols] = useState<Set<string>>(new Set());

  // Once `rows` (from router.refresh()) confirms a symbol is truly gone,
  // drop it from the optimistic-hide set during render — otherwise
  // re-adding the same ticker later (without a full page reload) would
  // stay stuck hidden. This mirrors React's documented "adjust state when
  // a prop changes" pattern (compare-in-render), not a useEffect, so it
  // never triggers an extra render pass.
  const [prevRows, setPrevRows] = useState(rows);
  if (rows !== prevRows) {
    setPrevRows(rows);
    if (removedSymbols.size > 0) {
      const stillPresent = new Set(
        [...removedSymbols].filter((s) => rows.some((r) => r.symbol === s)),
      );
      if (stillPresent.size !== removedSymbols.size) {
        setRemovedSymbols(stillPresent);
      }
    }
  }

  const startEdit = useCallback((symbol: string, current: number) => {
    setEditingShares(symbol);
    setSharesInput(String(current || 0));
    setSharesError(null);
  }, []);

  const cancelEdit = useCallback(() => {
    setEditingShares(null);
    setSharesInput("");
  }, []);

  const saveShares = useCallback(async (symbol: string, originalShares: number) => {
    const input = sharesInput.trim();
    if (!/^\d+$/.test(input)) {
      setSharesError("Shares must be a whole number of zero or greater.");
      return;
    }
    const val = Number(input);
    if (!Number.isSafeInteger(val)) {
      setSharesError("Shares must be a whole number of zero or greater.");
      return;
    }
    setEditingShares(null);
    if (val === (localShares[symbol] ?? originalShares)) return;
    setSharesSaving(symbol);
    setLocalShares((prev) => ({ ...prev, [symbol]: val }));
    try {
      const res = await fetch(`/api/symbols/${encodeURIComponent(symbol)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ total_shares: val }),
      });
      const data = await res.json().catch(() => ({})) as { error?: string };
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      router.refresh();
    } catch (error) {
      setLocalShares((prev) => { const n = { ...prev }; delete n[symbol]; return n; });
      setSharesError(
        error instanceof Error
          ? `Could not update shares for ${symbol}: ${error.message}`
          : `Could not update shares for ${symbol}`,
      );
    } finally {
      setSharesSaving(null);
    }
  }, [sharesInput, localShares, router]);

  const deleteSymbol = useCallback(async (symbol: string) => {
    if (deletingSymbols.has(symbol)) return; // already in flight — never duplicate the DELETE
    const confirmed = window.confirm(
      `Delete ${symbol} and all its data? This permanently removes ${symbol} and everything stored for it — positions, activity history, plans, forecasts, and analysis. This cannot be undone.`,
    );
    if (!confirmed) return;
    setDeletingSymbols((prev) => new Set(prev).add(symbol));
    try {
      const res = await fetch(`/api/symbols/${encodeURIComponent(symbol)}`, {
        method: "DELETE",
        headers: { Accept: "application/json" },
      });
      const data = await res.json().catch(() => ({})) as { error?: string };
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setRemovedSymbols((prev) => {
        const next = new Set(prev);
        next.add(symbol);
        return next;
      });
      router.refresh();
    } catch (error) {
      toast.error(
        error instanceof Error
          ? `Could not delete ${symbol}: ${error.message}`
          : `Could not delete ${symbol}`,
      );
    } finally {
      setDeletingSymbols((prev) => {
        const next = new Set(prev);
        next.delete(symbol);
        return next;
      });
    }
  }, [deletingSymbols, router]);

  const filtered = useMemo(() => {
    const query = q.trim().toUpperCase();
    let out = rows.filter((r) => !removedSymbols.has(r.symbol));
    if (query) {
      out = out.filter(
        (r) =>
          r.symbol?.toUpperCase().includes(query) ||
          (r.category || "").toUpperCase().includes(query) ||
          (r.entry_tag || "").toUpperCase().includes(query),
      );
    }
    if (suitabilityFilter !== "all") {
      out = out.filter((r) =>
        matchesSymbolSuitability(r.entry_tag, r.momentum, suitabilityFilter),
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
  }, [rows, q, sort, dir, suitabilityFilter, removedSymbols]);

  function toggleSort(key: SortKey) {
    if (sort === key) setDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSort(key);
      setDir(key === "symbol" || key === "category" ? "asc" : "desc");
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="🔍 Filter symbols…"
          className="w-full max-w-xs rounded-[var(--radius-pill)] border border-border bg-bg-input px-3.5 py-1.5 text-sm text-text placeholder:text-text-muted focus:border-accent-blue focus:outline-none"
        />
        <div className="flex flex-wrap gap-1.5">
          {SUITABILITY_FILTERS.map(({ key, label }) => (
            <button
              key={key}
              type="button"
              onClick={() => setSuitabilityFilter(key)}
              className={`rounded-[var(--radius-pill)] border px-3 py-1 text-xs transition-colors ${
                suitabilityFilter === key
                  ? "border-accent-blue bg-accent-blue/15 text-accent-blue"
                  : "border-border bg-bg-input text-text-muted hover:border-border hover:text-text"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <span className="ml-auto shrink-0 text-xs text-text-muted">{filtered.length} of {rows.length}</span>
      </div>

      {sharesError && (
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-3 py-2 text-xs text-accent-red">
          ⚠️ {sharesError}{" "}
          <button type="button" className="underline" onClick={() => setSharesError(null)}>Dismiss</button>
        </div>
      )}

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
              <th className="px-4 py-3 text-right font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr>
                <td colSpan={COLUMNS.length + 1} className="px-4 py-8 text-center text-text-muted">
                  No symbols match.
                </td>
              </tr>
            )}
            {filtered.map((r) => {
              const effectiveShares = localShares[r.symbol] ?? r.total_shares;
              return (
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
                <td
                  className="px-4 py-3 text-right font-mono"
                  onClick={(e) => e.stopPropagation()}
                >
                  {editingShares === r.symbol ? (
                    <input
                      type="number"
                      min="0"
                      step="1"
                      value={sharesInput}
                      onChange={(e) => setSharesInput(e.target.value)}
                      onBlur={() => saveShares(r.symbol, r.total_shares)}
                      onKeyDown={(e) => {
                        e.stopPropagation();
                        if (e.key === "Enter") {
                          e.preventDefault();
                          e.currentTarget.blur();
                        }
                        if (e.key === "Escape") {
                          e.preventDefault();
                          cancelEdit();
                        }
                      }}
                      className="w-20 rounded border border-accent-blue bg-bg-input px-2 py-0.5 text-right font-mono text-sm focus:outline-none"
                      autoFocus
                      onClick={(e) => e.stopPropagation()}
                    />
                  ) : sharesSaving === r.symbol ? (
                    <span className="text-text-muted">…</span>
                  ) : (
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); startEdit(r.symbol, effectiveShares); }}
                      className="cursor-text hover:text-accent-blue hover:underline"
                      title="Click to edit shares"
                    >
                      {effectiveShares > 0 ? effectiveShares : <span className="text-text-muted">—</span>}
                    </button>
                  )}
                </td>
                <td className="px-4 py-3 text-right font-mono">
                  <span className={effectiveShares > 0 && r.in_calls >= effectiveShares ? "text-accent-orange" : ""}>
                    {r.in_calls > 0 ? r.in_calls : "—"}
                  </span>
                </td>
                <td className="px-4 py-3 text-right font-mono">{r.put_exposure > 0 ? `$${usd(r.put_exposure)}` : "—"}</td>
                <td
                  className="px-4 py-3 text-right"
                  onClick={(e) => e.stopPropagation()}
                >
                  <button
                    type="button"
                    onClick={() => deleteSymbol(r.symbol)}
                    disabled={deletingSymbols.has(r.symbol)}
                    aria-label={`Delete ${r.symbol} and all its data`}
                    title={`Delete ${r.symbol} and all its data`}
                    className="inline-flex items-center justify-center rounded-[var(--radius-pill)] border border-accent-red/40 p-1.5 text-accent-red transition-colors hover:bg-accent-red/10 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-red/60"
                  >
                    <Trash2 size={14} className="shrink-0" aria-hidden />
                  </button>
                </td>
              </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {modalSymbol && (
        <SymbolInfoModal symbol={modalSymbol} onClose={() => setModalSymbol(null)} />
      )}
    </div>
  );
}
