import AgentLogsView from "@/components/AgentLogsView";
import { apiFetch } from "@/lib/api";
import type { AgentTracesResponse } from "@/types/agent-traces";

export const dynamic = "force-dynamic";
export const metadata = { title: "Agent Logs — Option Income Lab" };

export default async function AgentLogsPage() {
  let data: AgentTracesResponse | null = null;
  let error: string | null = null;

  try {
    data = await apiFetch<AgentTracesResponse>("/api/agent-traces");
  } catch (err) {
    error = err instanceof Error ? err.message : "Failed to load agent traces";
  }

  if (error || !data) {
    return (
      <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
        ⚠️ {error ?? "No data"}
      </div>
    );
  }

  return <AgentLogsView data={data} />;
}
