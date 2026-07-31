import SettingsConfigView from "@/components/SettingsConfigView";
import { apiFetch } from "@/lib/api";
import type { SettingsConfig } from "@/types/settings";

export const dynamic = "force-dynamic";
export const metadata = { title: "Configuration — Option Income Lab" };

export default async function SettingsConfigPage() {
  let data: SettingsConfig | null = null;
  let error: string | null = null;

  try {
    data = await apiFetch<SettingsConfig>("/api/settings/config");
  } catch (err) {
    error = err instanceof Error ? err.message : "Failed to load configuration";
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">⚙️ Configuration</h1>
        <p className="text-sm text-text-muted">Scheduler tasks and Telegram notifications.</p>
      </div>
      {error || !data ? (
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
          ⚠️ {error ?? "No data"}
        </div>
      ) : (
        <SettingsConfigView initial={data} />
      )}
    </div>
  );
}
