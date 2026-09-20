import type { BackupExportPreview } from "@/types/backup";

function ListBlock({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div>
      <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-text-muted">{title}</h4>
      <ul className="space-y-1 text-sm">
        {items.map((item, index) => <li key={`${item}-${index}`}>• {item}</li>)}
      </ul>
    </div>
  );
}

export default function BackupDependencySummary({
  preview,
  includesSourceRows,
}: {
  preview: BackupExportPreview;
  includesSourceRows: boolean;
}) {
  const additions = Array.isArray(preview.added_dependencies)
    ? preview.added_dependencies.map((item) =>
        typeof item === "string"
          ? item
          : `${item.section ?? item.name ?? "Dependency"}${item.count != null ? ` (${item.count})` : ""}${item.reason ? ` — ${item.reason}` : ""}`,
      )
    : Object.entries(preview.added_dependencies).map(
        ([section, count]) => `${section.replaceAll("_", " ")} (${count})`,
      );
  return (
    <section className="rounded-[var(--radius)] border border-border bg-bg-card-2 p-4" aria-label="Backup preview">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold">Effective backup scope</h3>
        <span className="rounded-full bg-accent-green/15 px-2.5 py-1 text-xs text-accent-green">
          Preview ready
        </span>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <ListBlock title="Requested sections" items={preview.requested_sections} />
        <ListBlock title="Effective sections" items={preview.effective_sections} />
        <ListBlock title="Automatically added dependencies" items={additions} />
        <div>
          <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-text-muted">Record counts</h4>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
            {Object.entries(preview.counts).map(([name, count]) => (
              <div key={name} className="contents">
                <dt className="text-text-muted">{name.replaceAll("_", " ")}</dt>
                <dd className="text-right font-mono">{count}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>
      {includesSourceRows ? (
        <div className="mt-4 rounded-[var(--radius)] border border-accent-orange/40 bg-accent-orange/10 p-3 text-sm">
          ⚠️ Broker source rows are included and may contain sensitive financial information.
        </div>
      ) : null}
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <ListBlock title="Warnings" items={preview.warnings} />
        <ListBlock title="Permanent exclusions" items={preview.exclusions} />
      </div>
    </section>
  );
}
