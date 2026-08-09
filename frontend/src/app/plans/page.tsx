import PlansView from "@/components/PlansView";
import { apiFetch } from "@/lib/api";
import type { Plan } from "@/types/plans";
import type { SymbolsOverview } from "@/types/symbols";

export const dynamic = "force-dynamic";
export const metadata = { title: "Action Plans — Option Income Lab" };

export default async function PlansPage() {
  let plans: Plan[] = [];
  let symbols: string[] = [];
  let error: string | null = null;

  try {
    plans = await apiFetch<Plan[]>("/api/plans");
  } catch (err) {
    error = err instanceof Error ? err.message : "Failed to load plans";
  }

  try {
    const overview = await apiFetch<SymbolsOverview>("/api/symbols/overview");
    symbols = (overview.rows ?? []).map((r) => r.symbol).sort();
  } catch {
    // Symbol dropdown just stays empty if the overview fails.
  }

  if (error) {
    return (
      <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
        ⚠️ {error}
      </div>
    );
  }

  return <PlansView initialPlans={plans} symbols={symbols} />;
}
