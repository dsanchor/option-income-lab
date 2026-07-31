import Link from "next/link";
import DgiAnalysisView from "@/components/DgiAnalysisView";
import DgiAnalyzeSearch from "@/components/DgiAnalyzeSearch";
import { apiFetch } from "@/lib/api";
import type { DgiAnalysis } from "@/types/dgi";

export const dynamic = "force-dynamic";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol } = await params;
  return { title: `DGI Analysis — ${symbol.toUpperCase()} — Option Income Lab` };
}

export default async function DgiAnalyzePage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol } = await params;
  let data: DgiAnalysis | null = null;
  let error: string | null = null;

  try {
    data = await apiFetch<DgiAnalysis>(`/api/dgi/analyze/${encodeURIComponent(symbol)}`);
    if (data?.error) {
      error = data.error;
      data = null;
    }
  } catch (err) {
    error = err instanceof Error ? err.message : "Analysis failed";
  }

  if (error || !data) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-semibold">DGI Symbol Analysis</h1>
          <p className="text-sm text-text-muted">{symbol.toUpperCase()}</p>
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
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
          ⚠️ {error ?? "No data"}
        </div>
      </div>
    );
  }

  return <DgiAnalysisView result={data} />;
}
