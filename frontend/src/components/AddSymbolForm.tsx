"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  CircleAlert,
  CircleCheck,
  LoaderCircle,
  Plus,
  Search,
  X,
  ChevronDown,
} from "lucide-react";
import {
  searchSecurities,
  addSymbol,
  type SecuritySearchCandidate,
} from "@/lib/portfolio-api";
import SecurityCreateForm from "@/components/SecurityCreateForm";
import type { SecurityMaster } from "@/types/portfolio";

type Phase = "search" | "create";

export default function AddSymbolForm({ onAdded }: { onAdded?: () => void } = {}) {
  const router = useRouter();
  const formRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<Phase>("search");

  // Search state
  const [query, setQuery] = useState("");
  const [candidates, setCandidates] = useState<SecuritySearchCandidate[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  // Submission state
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Debounce search
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setCandidates([]);
      setSearchError(null);
      return;
    }
    setSearching(true);
    const timer = setTimeout(async () => {
      try {
        const res = await searchSecurities(q, 10);
        setCandidates(res.candidates);
        setSearchError(null);
      } catch {
        setSearchError("Search failed. Try again.");
        setCandidates([]);
      } finally {
        setSearching(false);
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  // Close on outside click / Escape
  useEffect(() => {
    if (!open) return;
    function onPointerDown(e: MouseEvent) {
      if (!saving && formRef.current && !formRef.current.contains(e.target as Node)) {
        closeForm();
      }
    }
    function onKeyDown(e: KeyboardEvent) {
      if (!saving && e.key === "Escape") closeForm();
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, saving]); // eslint-disable-line react-hooks/exhaustive-deps

  function openForm() {
    setOpen(true);
    setPhase("search");
    setQuery("");
    setCandidates([]);
    setError(null);
    setSuccess(null);
    setSearchError(null);
    setTimeout(() => searchRef.current?.focus(), 50);
  }

  function closeForm() {
    setOpen(false);
  }

  const selectExisting = useCallback(async (candidate: SecuritySearchCandidate) => {
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      const res = await addSymbol({ security_id: candidate.security_id });
      onAdded?.();
      const label = candidate.has_config ? "Already in watchlist" : `${candidate.ticker} added`;
      setSuccess(label);
      setTimeout(() => {
        setOpen(false);
        router.push(res.navigate_to);
      }, 700);
    } catch (err) {
      const e = err as { status?: number; data?: { error?: string } };
      setError(e.data?.error ?? (err instanceof Error ? err.message : "Failed to add symbol"));
    } finally {
      setSaving(false);
    }
  }, [router, onAdded]);

  const handleCreated = useCallback(async (security: SecurityMaster) => {
    setSaving(true);
    setError(null);
    try {
      const res = await addSymbol({ security_id: security.security_id });
      onAdded?.();
      setSuccess(`${security.ticker} added!`);
      setTimeout(() => {
        setOpen(false);
        router.push(res.navigate_to);
      }, 700);
    } catch (err) {
      const e = err as { status?: number; data?: { error?: string } };
      setError(e.data?.error ?? (err instanceof Error ? err.message : "Failed to add symbol"));
    } finally {
      setSaving(false);
    }
  }, [router, onAdded]);

  return (
    <div ref={formRef} className="relative inline-block">
      <button
        type="button"
        onClick={open ? closeForm : openForm}
        disabled={saving}
        className="inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] bg-accent-blue px-4 py-2 text-sm font-medium text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue/60"
        aria-expanded={open}
        aria-haspopup="dialog"
      >
        <Plus size={15} aria-hidden />
        Add Symbol
      </button>

      {open && (
        <div
          role="dialog"
          aria-labelledby="add-symbol-title"
          className="absolute left-0 z-30 mt-2 w-[min(36rem,calc(100vw-2rem))] rounded-[var(--radius)] border border-border bg-bg-card p-4 shadow-lg"
        >
          <div className="mb-3 flex items-center justify-between">
            <h3 id="add-symbol-title" className="text-sm font-semibold text-text">
              Add Symbol
            </h3>
            <button
              type="button"
              onClick={closeForm}
              disabled={saving}
              className="grid h-8 w-8 place-items-center rounded-[var(--radius-pill)] text-text-muted transition-colors hover:bg-bg-hover hover:text-text disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue/60"
              aria-label="Close"
            >
              <X size={16} aria-hidden />
            </button>
          </div>

          {/* Status messages */}
          <div aria-live="polite" className="mb-2">
            {error && (
              <p className="flex items-center gap-1.5 text-xs text-accent-red">
                <CircleAlert size={14} className="shrink-0" aria-hidden />
                {error}
              </p>
            )}
            {success && (
              <p className="flex items-center gap-1.5 text-xs text-accent-green">
                <CircleCheck size={14} className="shrink-0" aria-hidden />
                {success}
              </p>
            )}
          </div>

          {phase === "search" && (
            <div className="space-y-3">
              {/* Search input */}
              <label className="block">
                <span className="sr-only">Search securities</span>
                <div className="relative">
                  <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" aria-hidden />
                  <input
                    ref={searchRef}
                    type="search"
                    autoComplete="off"
                    placeholder="Search by ticker or company name…"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    className="h-10 w-full rounded-[var(--radius)] border border-border bg-bg-input pl-9 pr-3 text-sm text-text placeholder:text-text-muted focus:border-accent-blue focus:outline-none"
                    disabled={saving}
                  />
                  {searching && (
                    <LoaderCircle size={14} className="absolute right-3 top-1/2 -translate-y-1/2 animate-spin text-text-muted" aria-hidden />
                  )}
                </div>
              </label>

              {/* Search error */}
              {searchError && (
                <p className="text-xs text-accent-red">{searchError}</p>
              )}

              {/* Candidates list */}
              {candidates.length > 0 && (
                <ul className="max-h-60 overflow-y-auto divide-y divide-border/50 rounded-[var(--radius)] border border-border/60" role="listbox" aria-label="Security matches">
                  {candidates.map((c) => (
                    <li key={c.security_id} role="option" aria-selected={false}>
                      <button
                        type="button"
                        onClick={() => selectExisting(c)}
                        disabled={saving}
                        className="w-full flex items-center justify-between gap-3 px-3 py-2.5 text-left text-sm hover:bg-bg-hover transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <span className="min-w-0">
                          <span className="font-mono font-semibold text-text mr-2">{c.security_id}</span>
                          <span className="truncate text-text-muted">{c.company_name}</span>
                        </span>
                        <div className="flex shrink-0 items-center gap-2">
                          {c.has_config && (
                            <span className="rounded-[var(--radius-pill)] border border-accent-green/40 bg-accent-green/10 px-2 py-0.5 text-xs text-accent-green">
                              In watchlist
                            </span>
                          )}
                          <span className="text-xs text-accent-blue">Add →</span>
                        </div>
                      </button>
                    </li>
                  ))}
                </ul>
              )}

              {/* Empty state after search */}
              {query.trim() && !searching && candidates.length === 0 && !searchError && (
                <p className="text-sm text-text-muted">
                  No securities match &ldquo;{query}&rdquo;.
                </p>
              )}

              {/* Create new option */}
              <div className="border-t border-border/60 pt-3">
                <button
                  type="button"
                  onClick={() => setPhase("create")}
                  disabled={saving}
                  className="flex w-full items-center gap-2 rounded-[var(--radius)] border border-border/60 bg-bg-input px-3 py-2 text-sm text-text-muted hover:bg-bg-hover hover:text-text transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Plus size={14} aria-hidden />
                  Create new security
                  <ChevronDown size={14} className="ml-auto" aria-hidden />
                </button>
              </div>
            </div>
          )}

          {phase === "create" && (
            <div className="space-y-2">
              <button
                type="button"
                onClick={() => { setPhase("search"); setError(null); }}
                className="flex items-center gap-1.5 text-xs text-accent-blue hover:underline"
              >
                ← Back to search
              </button>
              <SecurityCreateForm
                onCreated={handleCreated}
                onCancel={() => setPhase("search")}
                prefillName={query.trim() || undefined}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
