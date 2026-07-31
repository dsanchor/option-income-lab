"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { renderMarkdown } from "@/lib/markdown";

interface AgentResult {
  cached_resources?: string[];
  error?: string;
  [k: string]: unknown;
}

/**
 * Client view for the LLM-driven Report / Technical Analysis pages. POSTs to the
 * given BFF endpoint on mount, renders the markdown result and cached-resource
 * metadata, and offers a Regenerate button.
 */
export default function AgentMarkdownView({
  symbol,
  endpoint,
  resultKey,
  title,
  subtitle,
  emptyText,
}: {
  symbol: string;
  endpoint: string; // e.g. `/api/symbols/AAPL/report`
  resultKey: "report" | "analysis";
  title: string;
  subtitle: string;
  emptyText: string;
}) {
  const [html, setHtml] = useState<string | null>(null);
  const [cached, setCached] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const run = useCallback(async () => {
    setLoading(true);
    setError(null);
    setHtml(null);
    setCached([]);
    try {
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const data = (await res.json().catch(() => ({}))) as AgentResult;
      if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
      const md = (data[resultKey] as string) || emptyText;
      setHtml(renderMarkdown(md));
      setCached(Array.isArray(data.cached_resources) ? data.cached_resources : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to generate");
    } finally {
      setLoading(false);
    }
  }, [endpoint, resultKey, emptyText]);

  useEffect(() => {
    run();
  }, [run]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link href={`/symbols/${symbol}`} className="text-sm text-text-muted hover:text-text">
            ← {symbol}
          </Link>
          <h1 className="mt-1 text-2xl font-semibold">{title}</h1>
          <p className="text-sm text-text-muted">{subtitle}</p>
        </div>
        <button
          type="button"
          onClick={run}
          disabled={loading}
          className="inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] border border-border bg-bg-input px-4 py-2 text-sm text-text transition hover:bg-bg-hover disabled:opacity-50"
        >
          {loading ? "Generating…" : "↻ Regenerate"}
        </button>
      </div>

      <div className="mx-auto w-full max-w-[960px] rounded-[var(--radius)] border border-border bg-bg-card px-6 py-6">
        {loading && (
          <div className="py-12 text-center text-text-muted">
            <div className="mb-2 text-2xl">⏳</div>
            Generating… this may take a moment.
          </div>
        )}
        {!loading && error && (
          <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
            ⚠️ {error}
          </div>
        )}
        {!loading && !error && html != null && (
          <>
            <div
              className="leading-relaxed [&_a]:text-accent-blue [&_strong]:text-text"
              dangerouslySetInnerHTML={{ __html: html }}
            />
            {cached.length > 0 && (
              <div className="mt-6 border-t border-border pt-4 text-xs text-text-muted">
                📦 Cached data used: {cached.join(", ")}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
