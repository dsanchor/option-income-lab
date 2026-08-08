import { apiFetch } from "@/lib/api";
import { usd, timeAgo } from "@/lib/format";
import SymbolsTable from "@/components/SymbolsTable";
import AddSymbolForm from "@/components/AddSymbolForm";
import type { SymbolsOverview } from "@/types/symbols";

export const dynamic = "force-dynamic";

async function getData(): Promise<SymbolsOverview> {
  try {
    return await apiFetch<SymbolsOverview>("/api/symbols/overview");
  } catch (err) {
    return { error: err instanceof Error ? err.message : "Failed to load symbols" };
  }
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
          <h1 className="text-2xl font-semibold tracking-tight">Symbols</h1>
          {d.last_update_ts && (
            <p className="text-sm text-text-muted">Last enrichment {timeAgo(d.last_update_ts)}</p>
          )}
        </div>
        <div className="flex flex-wrap gap-4 text-sm text-text-muted">
          <span>{d.symbol_count ?? rows.length} tracked</span>
          <span>Calls exposure <span className="text-text">${usd(d.total_call_exposure)}</span></span>
          <span>Puts committed <span className="text-text">${usd(d.total_put_exposure)}</span></span>
        </div>
      </div>

      <AddSymbolForm />
      <SymbolsTable rows={rows} />
    </div>
  );
}
