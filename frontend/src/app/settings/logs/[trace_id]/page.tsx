import { apiFetch } from "@/lib/api";
import type { AgentTraceDetailResponse } from "@/types/agent-traces";

export const dynamic = "force-dynamic";
export const metadata = { title: "Agent Trace — Option Income Lab" };

function fmtDuration(d?: number | null): string {
  return d !== null && d !== undefined ? `${d.toFixed(2)}s` : "—";
}

function TraceBlock({ text }: { text?: string }) {
  return (
    <pre className="m-0 max-h-[480px] overflow-auto whitespace-pre-wrap break-words rounded-[var(--radius)] border border-border bg-bg-input p-4 font-mono text-[0.82rem] leading-relaxed">
      {text && text.length ? text : "(empty)"}
    </pre>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-[var(--radius)] border border-border bg-bg-card">
      <div className="border-b border-border px-5 py-3">
        <h2 className="text-base font-semibold">{title}</h2>
      </div>
      <div className="px-5 py-4">{children}</div>
    </section>
  );
}

function Field({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div>
      <span className="block text-[0.72rem] uppercase tracking-wide text-text-muted">{label}</span>
      <span className={`block text-[0.95rem] font-semibold ${mono ? "font-mono" : ""}`}>{value}</span>
    </div>
  );
}

export default async function TraceDetailPage({
  params,
}: {
  params: Promise<{ trace_id: string }>;
}) {
  const { trace_id } = await params;
  let data: AgentTraceDetailResponse | null = null;
  let error: string | null = null;

  try {
    data = await apiFetch<AgentTraceDetailResponse>(
      `/api/agent-traces/${encodeURIComponent(trace_id)}`,
    );
  } catch (err) {
    error = err instanceof Error ? err.message : "Failed to load trace";
  }

  if (error || !data?.trace) {
    return (
      <div className="flex flex-col gap-4">
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
          ⚠️ {error ?? "Trace not found"}
        </div>
      </div>
    );
  }

  const t = data.trace;
  const parsedStr =
    t.parsed !== undefined && t.parsed !== null ? JSON.stringify(t.parsed, null, 2) : "";
  const extraStr =
    t.extra !== undefined && t.extra !== null ? JSON.stringify(t.extra, null, 2) : "";

  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-2xl font-semibold">🧾 Agent Trace</h1>
        <p className="text-sm text-text-muted">{t.timestamp}</p>
      </div>

      <Card title="Overview">
        <div className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-3">
          <Field label="Symbol" value={t.symbol && t.symbol !== "_" ? t.symbol : "—"} />
          <Field label="Agent" value={data.agent_label || "—"} />
          <Field label="Phase" value={t.phase || "—"} />
          <Field label="Model" value={t.model || "—"} mono />
          <Field label="Duration" value={fmtDuration(t.duration_seconds)} />
          <Field label="Confidence" value={t.confidence || "—"} />
          <Field label="Decision" value={t.activity || "—"} />
          <Field label="Alert" value={t.is_alert ? "📢 Yes" : "No"} />
        </div>
        {t.error && (
          <div className="mt-4 rounded-[var(--radius)] border border-accent-orange/40 bg-accent-orange/10 px-4 py-3 text-sm">
            <strong>Error:</strong> {t.error}
          </div>
        )}
      </Card>

      {t.skills && t.skills.length > 0 && (
        <Card title="🛠️ Skills">
          <ul className="list-disc pl-5 font-mono text-sm">
            {t.skills.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </Card>
      )}

      <Card title="📜 System Prompt">
        <TraceBlock text={t.system_prompt} />
      </Card>

      <Card title="👤 User Message">
        <TraceBlock text={t.user_message} />
      </Card>

      <Card title="🤖 Assistant Response">
        <TraceBlock text={t.response_text} />
      </Card>

      {parsedStr && (
        <Card title="🧩 Parsed Result">
          <TraceBlock text={parsedStr} />
        </Card>
      )}

      {extraStr && (
        <Card title="➕ Extra">
          <TraceBlock text={extraStr} />
        </Card>
      )}
    </div>
  );
}
