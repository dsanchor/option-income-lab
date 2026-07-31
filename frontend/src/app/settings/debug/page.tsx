import SettingsDebugView from "@/components/SettingsDebugView";
import AgentRunStats from "@/components/AgentRunStats";
import { apiFetch } from "@/lib/api";
import type { SettingsDebug, SettingsRuntime } from "@/types/settings";

export const dynamic = "force-dynamic";
export const metadata = { title: "Debug — Option Income Lab" };

export default async function SettingsDebugPage() {
  let data: SettingsDebug | null = null;
  let error: string | null = null;

  try {
    data = await apiFetch<SettingsDebug>("/api/settings/debug");
  } catch (err) {
    error = err instanceof Error ? err.message : "Failed to load debug diagnostics";
  }

  let runtime: SettingsRuntime | null = null;
  try {
    runtime = await apiFetch<SettingsRuntime>("/api/settings/runtime");
  } catch {
    runtime = null;
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">🔍 Debug</h1>
        <p className="text-sm text-text-muted">Testing tools and connection diagnostics.</p>
      </div>
      {runtime && <AgentRunStats agentRun={runtime.telemetry_stats?.agent_run} />}
      {error || !data ? (
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
          ⚠️ {error ?? "No data"}
        </div>
      ) : (
        <SettingsDebugView data={data} />
      )}
    </div>
  );
}
