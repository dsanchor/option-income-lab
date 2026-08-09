import AiProvidersView from "@/components/AiProvidersView";
import { apiFetch } from "@/lib/api";
import type { AiProvidersConfig } from "@/types/aiProviders";

export const dynamic = "force-dynamic";
export const metadata = { title: "AI Providers — Option Income Lab" };

export default async function AiProvidersPage() {
  let data: AiProvidersConfig | null = null;
  let error: string | null = null;

  try {
    data = await apiFetch<AiProvidersConfig>("/api/settings/ai-providers");
  } catch (err) {
    error = err instanceof Error ? err.message : "Failed to load AI providers";
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">AI Providers</h1>
        <p className="text-sm text-text-muted">
          Provider and model selection by internal AI function. Credentials remain environment-managed.
        </p>
      </div>
      {error || !data ? (
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm text-accent-red">
          {error ?? "No data"}
        </div>
      ) : (
        <AiProvidersView initial={data} />
      )}
    </div>
  );
}
