import DgiScreenerView from "@/components/DgiScreenerView";
import { apiFetch } from "@/lib/api";
import type { DgiEntry, DgiTopResponse } from "@/types/dgi";

export const dynamic = "force-dynamic";
export const metadata = { title: "DGI Screener — Option Income Lab" };

export default async function DgiPage() {
  let entries: DgiEntry[] = [];
  let error: string | null = null;

  try {
    const data = await apiFetch<DgiTopResponse>("/api/dgi/top");
    if (data.error) error = data.error;
    entries = (data.top ?? []).slice().sort((a, b) => (a.rank ?? 999) - (b.rank ?? 999));
  } catch (err) {
    error = err instanceof Error ? err.message : "Failed to load DGI data";
  }

  if (error) {
    return (
      <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
        ⚠️ {error}
      </div>
    );
  }

  return <DgiScreenerView entries={entries} />;
}
