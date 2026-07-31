import SettingsRuntimeView from "@/components/SettingsRuntimeView";
import { apiFetch } from "@/lib/api";
import type { SettingsRuntime } from "@/types/settings";

export const dynamic = "force-dynamic";
export const metadata = { title: "Runtime Stats — Option Income Lab" };

export default async function SettingsRuntimePage() {
  let data: SettingsRuntime | null = null;
  let error: string | null = null;

  try {
    data = await apiFetch<SettingsRuntime>("/api/settings/runtime");
  } catch (err) {
    error = err instanceof Error ? err.message : "Failed to load runtime stats";
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">📊 Runtime Stats</h1>
        <p className="text-sm text-text-muted">
          Cache status, agent performance, and data fetch metrics.
        </p>
      </div>
      {error || !data ? (
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
          ⚠️ {error ?? "No data"}
        </div>
      ) : (
        <SettingsRuntimeView data={data} />
      )}
    </div>
  );
}
