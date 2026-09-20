import type { BackupDryRunPlan, BackupImportRecordStatus } from "@/types/backup";

const blockingStatuses = new Set<BackupImportRecordStatus>([
  "CONFLICT_REQUIRES_CHOICE",
  "BLOCKED_MISSING_REFERENCE",
  "BLOCKED_INVARIANT",
]);

export function dryRunHasBlockingIssues(report: BackupDryRunPlan) {
  const records = report.records ?? report.per_record_statuses ?? [];
  return (
    report.valid === false ||
    Boolean(report.blocking_errors?.length) ||
    Boolean(report.errors?.length) ||
    Boolean(report.dependency_errors?.length) ||
    Boolean(report.collisions?.length) ||
    records.some((record) => blockingStatuses.has(record.status))
  );
}

export default function BackupDryRunReport({ report }: { report: BackupDryRunPlan }) {
  const records = report.records ?? report.per_record_statuses ?? [];
  const counts = records.reduce<Record<string, number>>((result, record) => {
    result[record.status] = (result[record.status] ?? 0) + 1;
    return result;
  }, {});
  return (
    <section className="rounded-[var(--radius)] border border-border bg-bg-card-2 p-4" aria-label="Dry-run report">
      <h3 className="font-semibold">Dry-run report</h3>
      <p className="mt-1 text-sm text-text-muted">{report.human_summary ?? report.summary ?? "Destination pre-flight completed."}</p>
      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {Object.entries(counts).map(([status, count]) => (
          <div key={status} className="rounded-[var(--radius)] bg-bg-input p-3">
            <div className="text-xs text-text-muted">{status.replaceAll("_", " ")}</div>
            <div className="mt-1 font-mono text-lg">{count}</div>
          </div>
        ))}
      </div>
      {report.ordering?.length ? <p className="mt-4 text-sm"><span className="text-text-muted">Apply order:</span> {report.ordering.join(" → ")}</p> : null}
      {report.predicted_controls || report.controls ? (
        <details className="mt-4">
          <summary className="cursor-pointer text-sm font-semibold">Predicted controls</summary>
          <pre className="mt-2 overflow-auto rounded-[var(--radius)] bg-bg-input p-3 text-xs">{JSON.stringify(report.predicted_controls ?? report.controls, null, 2)}</pre>
        </details>
      ) : null}
      {dryRunHasBlockingIssues(report) ? (
        <p role="alert" className="mt-4 rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 p-3 text-sm text-accent-red">
          Apply is blocked. Resolve conflicts, missing dependencies, or invariant errors before restoring.
        </p>
      ) : null}
    </section>
  );
}
