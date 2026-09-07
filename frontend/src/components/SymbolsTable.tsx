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
import { symbolHref } from "@/lib/symbolEncoding";
import SymbolInfoModal from "@/components/SymbolInfoModal";
import type { SymbolRow } from "@/types/symbols";

function num(n: number | null | undefined, digits = 1): string {
  return typeof n === "number" && isFinite(n) ? n.toFixed(digits) : "—";
}
function eur(v: string | number | null | undefined): string {
  if (v == null) return "—";
  const n = typeof v === "number" ? v : parseFloat(v);
  if (!isFinite(n)) return "—";
  return new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR", maximumFractionDigits: 2 }).format(n);
}
function portfolioShares(v: string | null | undefined): string {
  if (!v) return "—";
  const n = parseFloat(v);
  if (!isFinite(n)) return "—";
  return n % 1 === 0 ? n.toFixed(0) : n.toFixed(4).replace(/0+$/, "");
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

/** Returns true for rows that originate from the portfolio (have ledger history). */
function isPortfolioRow(r: SymbolRow): boolean {
  if (r.row_source != null) return r.row_source !== "watchlist";
  // Fallback for legacy responses: if portfolio_shares is present, treat as portfolio row
  return r.portfolio_shares != null || r.list_section === "portfolio";
}

/**
 * Returns true when a row should be hidden under the "hide historical zeros" toggle.
 * Only hides auto-enrolled rows with exactly zero portfolio shares.
 * Explicit watchlist rows (is_auto_enrolled = false) are always shown.
 */
function isHiddenZeroRow(r: SymbolRow): boolean {
  if (r.portfolio_shares == null) return false;
  const shares = parseFloat(r.portfolio_shares);
  if (!isFinite(shares) || shares !== 0) return false;
  // Only hide if backend confirms auto-enrolled; default to hiding if field is absent
  // to match the legacy "hideZeroPortfolio" behavior.
  return r.is_auto_enrolled !== false;
}

type SortKey =
  | "symbol" | "category" | "dgi_score" | "tech_timing" | "entry_tag"
  | "momentum" | "price" | "price_eur" | "total_shares" | "in_calls" | "put_exposure"
  | "portfolio_shares" | "portfolio_avg_cost_eur" | "portfolio_invested_eur"
  | "current_value_eur" | "portfolio_dividends_eur";

/**
 * Case-insensitive row search predicate.
 * Checks symbol, display_name (company name), category, and entry_tag fields.
 * Called from the main filter useMemo — colocated with display_name usage.
 */
function matchesSearchQuery(r: { symbol?: string | null; display_name?: string | null; category?: string | null; entry_tag?: string | null }, query: string): boolean {
  if (!query) return true;
  const q = query.trim().toUpperCase();
  if (!q) return true;
  return (
    (r.symbol || "").toUpperCase().includes(q) ||
    (r.display_name || "").toUpperCase().includes(q) ||
    (r.category || "").toUpperCase().includes(q) ||
    (r.entry_tag || "").toUpperCase().includes(q)
  );
}

/**
 * Currency-aware price formatter.
 * - GBp/GBX: backend stores price_major (pounds); reconstruct pence display.
 * - USD / EUR / CHF: prefix or "CHF " prefix.
 * - null currency: legacy "$" fallback (no FX data yet).
 * - Unknown currency: number + code suffix.
 * Never divides or converts independently — only reconstructs the display unit.
 */
function formatPrice(price: number | null | undefined, currency: string | null | undefined): string {
  if (price == null || !isFinite(price)) return "—";
  if (!currency) return `$${num(price, 2)}`;
  if (currency === "GBp" || currency === "GBX") {
    // price is price_major (e.g. 48.15 GBP); display as whole-pence (e.g. "4,815 GBp")
    const pence = Math.round(price * 100);
    return `${new Intl.NumberFormat("en-US").format(pence)} GBp`;
  }
  const upper = currency.toUpperCase();
  if (upper === "USD") return `$${num(price, 2)}`;
  if (upper === "EUR") return `€${num(price, 2)}`;
  if (upper === "CHF") return `CHF ${num(price, 2)}`;
  return `${num(price, 2)} ${currency}`;
}

const SUITABILITY_FILTERS: { key: SymbolSuitabilityFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "ideal_puts", label: "Ideal Puts" },
  { key: "ideal_calls", label: "Ideal Calls" },
  { key: "no_puts", label: "No Puts" },
  { key: "no_calls", label: "No Calls" },
];

type ViewMode = "portfolio" | "options";

const COLUMNS: { key: SortKey; label: string; align?: "right"; modes?: ViewMode[] }[] = [
  { key: "symbol", label: "Symbol" },
  { key: "category", label: "Category" },
  { key: "dgi_score", label: "DGI", align: "right" },
  { key: "tech_timing", label: "Tech", align: "right" },
  { key: "entry_tag", label: "Entry" },
  { key: "momentum", label: "Momentum" },
  { key: "price", label: "Price", align: "right" },
  { key: "price_eur", label: "Price €", align: "right", modes: ["portfolio"] },
  { key: "portfolio_shares", label: "Shares", align: "right", modes: ["portfolio"] },
  { key: "portfolio_avg_cost_eur", label: "Avg Cost", align: "right", modes: ["portfolio"] },
  { key: "portfolio_invested_eur", label: "Invested", align: "right", modes: ["portfolio"] },
  { key: "current_value_eur", label: "Value €", align: "right", modes: ["portfolio"] },
  { key: "portfolio_dividends_eur", label: "Dividends", align: "right", modes: ["portfolio"] },
  { key: "in_calls", label: "In Calls", align: "right", modes: ["options"] },
  { key: "put_exposure", label: "Puts $", align: "right", modes: ["options"] },
];

export default function SymbolsTable({ rows }: { rows: SymbolRow[] }) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<SortKey>("symbol");
  const [dir, setDir] = useState<"asc" | "desc">("asc");
  const [suitabilityFilter, setSuitabilityFilter] = useState<SymbolSuitabilityFilter>("all");
  const [modalSymbol, setModalSymbol] = useState<string | null>(null);
  const [hideZero, setHideZero] = useState(true);
  const [viewMode, setViewMode] = useState<ViewMode>("portfolio");

  // Inline shares editing (watchlist-only rows)
  const [editingShares, setEditingShares] = useState<string | null>(null);
  const [sharesInput, setSharesInput] = useState("");
  const [sharesSaving, setSharesSaving] = useState<string | null>(null);
  const [sharesError, setSharesError] = useState<string | null>(null);
  const [localShares, setLocalShares] = useState<Record<string, number>>({});

  const [deletingSymbols, setDeletingSymbols] = useState<Set<string>>(new Set());
  const [removedSymbols, setRemovedSymbols] = useState<Set<string>>(new Set());

  const [prevRows, setPrevRows] = useState(rows);
  if (rows !== prevRows) {
    setPrevRows(rows);
    if (removedSymbols.size > 0) {
      const stillPresent = new Set(
        [...removedSymbols].filter((s) => rows.some((r) => r.symbol === s)),
      );
      if (stillPresent.size !== removedSymbols.size) setRemovedSymbols(stillPresent);
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
    if (!/^\d+$/.test(input)) { setSharesError("Shares must be a whole number."); return; }
    const val = Number(input);
    if (!Number.isSafeInteger(val)) { setSharesError("Shares must be a whole number."); return; }
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
      setSharesError(error instanceof Error ? `Could not update ${symbol}: ${error.message}` : `Could not update ${symbol}`);
    } finally {
      setSharesSaving(null);
    }
  }, [sharesInput, localShares, router]);

  const deleteSymbol = useCallback(async (symbol: string) => {
    if (deletingSymbols.has(symbol)) return;
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
      setRemovedSymbols((prev) => new Set(prev).add(symbol));
      router.refresh();
    } catch (error) {
      toast.error(error instanceof Error ? `Could not delete ${symbol}: ${error.message}` : `Could not delete ${symbol}`);
    } finally {
      setDeletingSymbols((prev) => { const n = new Set(prev); n.delete(symbol); return n; });
    }
  }, [deletingSymbols, router]);

  const filtered = useMemo(() => {
    const query = q.trim().toUpperCase();
    let out = rows.filter((r) => {
      if (removedSymbols.has(r.symbol)) return false;
      if (hideZero && isHiddenZeroRow(r)) return false;
      return true;
    });
    if (query) {
      out = out.filter((r) => matchesSearchQuery(r, query));
    }
    if (suitabilityFilter !== "all") {
      out = out.filter((r) => matchesSymbolSuitability(r.entry_tag, r.momentum, suitabilityFilter));
    }
    const sorted = [...out].sort((a, b) => {
      const av = a[sort] as unknown;
      const bv = b[sort] as unknown;
      let cmp: number;
      if (typeof av === "number" || typeof bv === "number") {
        cmp = (Number(av) || -Infinity) - (Number(bv) || -Infinity);
      } else if (typeof av === "string" && typeof bv === "string" && !isNaN(parseFloat(av))) {
        cmp = (parseFloat(av) || -Infinity) - (parseFloat(bv as string) || -Infinity);
      } else {
        cmp = String(av ?? "").localeCompare(String(bv ?? ""));
      }
      return dir === "asc" ? cmp : -cmp;
    });
    return sorted;
  }, [rows, q, sort, dir, suitabilityFilter, removedSymbols, hideZero]);

  // Count hidden zero-rows for toggle hint
  const hiddenZeroCount = useMemo(
    () => rows.filter((r) => !removedSymbols.has(r.symbol) && isHiddenZeroRow(r)).length,
    [rows, removedSymbols],
  );

  /** Columns visible in the current view mode (no-modes = always visible). */
  const visibleColumns = useMemo(
    () => COLUMNS.filter((c) => !c.modes || c.modes.includes(viewMode)),
    [viewMode],
  );

  /** Switch view mode; reset sort key if it becomes hidden. */
  function changeViewMode(mode: ViewMode) {
    setViewMode(mode);
    const newVisibleKeys = new Set(
      COLUMNS.filter((c) => !c.modes || c.modes.includes(mode)).map((c) => c.key),
    );
    if (!newVisibleKeys.has(sort)) {
      setSort("symbol");
      setDir("asc");
    }
  }

  function toggleSort(key: SortKey) {
    if (sort === key) setDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSort(key); setDir(key === "symbol" || key === "category" ? "asc" : "desc"); }
  }

  const portfolioFiltered = filtered.filter(isPortfolioRow);
  const watchlistFiltered = filtered.filter((r) => !isPortfolioRow(r));

  // Row renderer closes over all component state — no prop threading needed
  function renderRow(r: SymbolRow) {
    const isPortfolio = isPortfolioRow(r);
    const effectiveShares = localShares[r.symbol] ?? r.total_shares;
    const portfolioSharesNum = parseFloat(r.portfolio_shares ?? "0") || 0;
    const isZeroHistorical = isHiddenZeroRow(r);
    const href = symbolHref(r.security_id ?? r.symbol);
    const divNum = r.portfolio_dividends_eur != null ? parseFloat(r.portfolio_dividends_eur as string) : NaN;
    const divClass = !isFinite(divNum) || divNum === 0
      ? "text-text-muted"
      : divNum > 0 ? "text-accent-green" : "text-accent-red";
    return (
      <tr
        key={r.symbol}
        onClick={() => setModalSymbol(r.security_id ?? r.symbol)}
        className={`cursor-pointer border-b border-border/60 transition-colors last:border-0 hover:bg-bg-hover/50 ${isZeroHistorical ? "opacity-60" : ""}`}
      >
        <td className="px-4 py-3">
          <Link
            href={href}
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
        <td className="px-4 py-3 text-right font-mono">
          <span
            className={r.pricing_status === "stale" ? "opacity-60" : undefined}
            title={
              r.pricing_status === "stale"
                ? "Price data is stale (>2h old)"
                : r.pricing_status === "error"
                  ? "Pricing unavailable — showing enrichment estimate"
                  : undefined
            }
          >
            {formatPrice(r.price, r.price_display_currency)}
          </span>
        </td>
        {viewMode === "portfolio" && (
          <td className="px-4 py-3 text-right font-mono text-xs">{eur(r.price_eur)}</td>
        )}

        {/* Shares — portfolio: read-only ledger value; watchlist-only: editable override */}
        {viewMode === "portfolio" && (
          <td
            className="px-4 py-3 text-right font-mono"
            onClick={(e) => { if (!isPortfolio) e.stopPropagation(); }}
          >
            {isPortfolio ? (
              r.portfolio_shares != null ? (
                parseFloat(r.portfolio_shares) === 0
                  ? <span className="text-text-muted text-xs">0</span>
                  : portfolioShares(r.portfolio_shares)
              ) : "—"
            ) : (
              editingShares === r.symbol ? (
                <input
                  type="number" min="0" step="1" value={sharesInput}
                  onChange={(e) => setSharesInput(e.target.value)}
                  onBlur={() => saveShares(r.symbol, r.total_shares)}
                  onKeyDown={(e) => {
                    e.stopPropagation();
                    if (e.key === "Enter") { e.preventDefault(); e.currentTarget.blur(); }
                    if (e.key === "Escape") { e.preventDefault(); cancelEdit(); }
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
              )
            )}
          </td>
        )}

        {viewMode === "portfolio" && (
          <td className="px-4 py-3 text-right font-mono text-xs">{isPortfolio ? eur(r.portfolio_avg_cost_eur) : "—"}</td>
        )}
        {viewMode === "portfolio" && (
          <td className="px-4 py-3 text-right font-mono text-xs">{isPortfolio ? eur(r.portfolio_invested_eur) : "—"}</td>
        )}
        {viewMode === "portfolio" && (
          <td className="px-4 py-3 text-right font-mono text-xs">{isPortfolio ? eur(r.current_value_eur) : "—"}</td>
        )}
        {viewMode === "portfolio" && (
          <td className={`px-4 py-3 text-right font-mono text-xs ${divClass}`}>
            {isPortfolio ? eur(r.portfolio_dividends_eur) : "—"}
          </td>
        )}

        {/* In Calls — options mode only */}
        {viewMode === "options" && (
          <td className="px-4 py-3 text-right font-mono">
            {isPortfolio ? (
              <span className={portfolioSharesNum > 0 && r.in_calls >= portfolioSharesNum ? "text-accent-orange" : ""}>
                {r.in_calls > 0 ? r.in_calls : portfolioSharesNum >= 100 ? "0" : "—"}
              </span>
            ) : (
              <span className={effectiveShares > 0 && r.in_calls >= effectiveShares ? "text-accent-orange" : ""}>
                {r.in_calls > 0 ? r.in_calls : effectiveShares >= 100 ? "0" : "—"}
              </span>
            )}
          </td>
        )}
        {viewMode === "options" && (
          <td className="px-4 py-3 text-right font-mono">{r.put_exposure > 0 ? `$${usd(r.put_exposure)}` : "—"}</td>
        )}
        <td
          className="px-4 py-3 text-right"
          onClick={(e) => e.stopPropagation()}
        >
          <button
            type="button"
            onClick={() => deleteSymbol(r.symbol)}
            disabled={deletingSymbols.has(r.symbol)}
            aria-label={`Delete ${r.symbol} and all its data`}
            title={isPortfolio
              ? `Remove ${r.symbol} config (ledger data is preserved)`
              : `Delete ${r.symbol} and all its data`}
            className="inline-flex items-center justify-center rounded-[var(--radius-pill)] border border-accent-red/40 p-1.5 text-accent-red transition-colors hover:bg-accent-red/10 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-red/60"
          >
            <Trash2 size={14} className="shrink-0" aria-hidden />
          </button>
        </td>
      </tr>
    );
  }

  return (
    <div className="space-y-3">
      {/* Search + filter bar — surface card matching Economics filter section */}
      <div className="surface p-3">
        <div className="flex flex-wrap items-center gap-3">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="🔍 Filter symbols…"
          className="w-full max-w-xs rounded-[var(--radius-pill)] border border-border bg-bg-input px-3.5 py-1.5 text-sm text-text placeholder:text-text-muted focus:border-accent-blue focus:outline-none"
        />
        {/* View mode selector — controls column visibility only, not rows */}
        <div
          role="radiogroup"
          aria-label="Table view mode"
          className="flex items-center rounded-[var(--radius-pill)] border border-border bg-bg-card p-0.5"
        >
          {(["portfolio", "options"] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              role="radio"
              aria-checked={viewMode === mode}
              onClick={() => changeViewMode(mode)}
              className={`rounded-[var(--radius-pill)] px-3 py-1 text-xs transition-colors ${
                viewMode === mode
                  ? "bg-accent-blue text-white"
                  : "text-text-muted hover:text-text"
              }`}
            >
              {mode === "portfolio" ? "Portfolio" : "Options"}
            </button>
          ))}
        </div>
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
        <label className="flex items-center gap-1.5 cursor-pointer select-none text-xs text-text-muted hover:text-text">
          <input
            type="checkbox"
            checked={hideZero}
            onChange={(e) => setHideZero(e.target.checked)}
            className="rounded border-border"
            aria-label="Hide historical zero-share symbols"
          />
          Hide historical (0 shares)
          {hiddenZeroCount > 0 && !hideZero && (
            <span className="text-accent-orange">({hiddenZeroCount} shown)</span>
          )}
        </label>
        <span className="ml-auto shrink-0 text-xs text-text-muted">
          {portfolioFiltered.length} portfolio · {watchlistFiltered.length} watchlist
        </span>
        </div>
      </div>

      {sharesError && (
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-3 py-2 text-xs text-accent-red">
          ⚠️ {sharesError}{" "}
          <button type="button" className="underline" onClick={() => setSharesError(null)}>Dismiss</button>
        </div>
      )}

      <div className="surface table-modern overflow-x-auto">
        <table className="w-full min-w-[1200px] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
              {visibleColumns.map((c) => (
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
            {portfolioFiltered.length === 0 && watchlistFiltered.length === 0 && (
              <tr>
                <td colSpan={visibleColumns.length + 1} className="px-4 py-8 text-center text-text-muted">
                  No symbols match.
                </td>
              </tr>
            )}
            {portfolioFiltered.length > 0 && (
              <tr>
                <td
                  colSpan={visibleColumns.length + 1}
                  className="border-b border-border/60 bg-bg-hover/40 px-4 py-1 text-[0.65rem] font-semibold uppercase tracking-widest text-text-muted select-none"
                >
                  Portfolio
                </td>
              </tr>
            )}
            {portfolioFiltered.map(renderRow)}
            {watchlistFiltered.length > 0 && (
              <tr>
                <td
                  colSpan={visibleColumns.length + 1}
                  className="border-b border-border/60 bg-bg-hover/40 px-4 py-1 text-[0.65rem] font-semibold uppercase tracking-widest text-text-muted select-none"
                >
                  Watchlist
                </td>
              </tr>
            )}
            {watchlistFiltered.map(renderRow)}
          </tbody>
        </table>
      </div>

      {modalSymbol && (
        <SymbolInfoModal symbol={modalSymbol} onClose={() => setModalSymbol(null)} />
      )}
    </div>
  );
}
