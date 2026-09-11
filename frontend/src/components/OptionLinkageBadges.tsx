const WARNING_LABELS: Record<string, string> = {
  OPTION_SECURITY_UNRESOLVED: "Security not resolved — check Security Master",
  OPTION_OPENING_SELL_MISSING: "Missing opening sell movement",
  OPTION_MANUAL_CLOSE_BUY_MISSING: "Missing closing buy movement",
  OPTION_ASSIGNMENT_STOCK_MISSING: "Missing assignment stock movement",
};

const COVERAGE_STATUS_META: Record<string, { label: string; className: string }> = {
  linked: {
    label: "Linked",
    className: "border-accent-green/40 bg-accent-green/10 text-accent-green",
  },
  unlinked: {
    label: "Unlinked",
    className: "border-border bg-bg-input text-text-muted",
  },
  unresolved_security: {
    label: "Unresolved",
    className: "border-accent-red/40 bg-accent-red/10 text-accent-red",
  },
  account_filtered_out: {
    label: "Filtered",
    className: "border-accent-blue/40 bg-accent-blue/10 text-accent-blue",
  },
};

const WARNING_BADGE_CLASS =
  "border-accent-orange/40 bg-accent-orange/10 text-accent-orange";
const PAPER_BADGE_CLASS =
  "border-accent-purple/40 bg-accent-purple/10 text-accent-purple";

function pillClassName(className: string) {
  return `inline-block rounded-[var(--radius-pill)] border px-2 py-0.5 text-xs ${className}`;
}

export function getOptionWarningLabel(code: string): string {
  return WARNING_LABELS[code] ?? code;
}

export function getCoverageStatusLabel(status?: string | null): string {
  if (!status) return "Unknown";
  return COVERAGE_STATUS_META[status]?.label ?? status.replaceAll("_", " ");
}

export function getCoverageStatusClassName(status?: string | null): string {
  if (!status) return "border-border bg-bg-input text-text-muted";
  return COVERAGE_STATUS_META[status]?.className ?? "border-border bg-bg-input text-text-muted";
}

export function CoverageStatusBadge({ status }: { status?: string | null }) {
  if (!status) return null;
  return (
    <span className={pillClassName(getCoverageStatusClassName(status))}>
      {getCoverageStatusLabel(status)}
    </span>
  );
}

export function WarningBadge({
  warnings,
  className = "",
}: {
  warnings?: string[] | null;
  className?: string;
}) {
  const items = warnings?.filter(Boolean) ?? [];
  if (items.length === 0) return null;

  const tooltip = items.map(getOptionWarningLabel).join("\n");

  return (
    <span
      title={tooltip}
      className={`${pillClassName(WARNING_BADGE_CLASS)} ${className}`.trim()}
    >
      {items.length === 1 ? "1 warning" : `${items.length} warnings`}
    </span>
  );
}

export function WarningList({
  warnings,
  emptyText = "No linkage warnings.",
}: {
  warnings?: string[] | null;
  emptyText?: string;
}) {
  const items = warnings?.filter(Boolean) ?? [];
  if (items.length === 0) {
    return <span className="text-sm text-text-muted">{emptyText}</span>;
  }

  return (
    <ul className="space-y-1">
      {items.map((warning) => (
        <li key={warning} className="flex items-start gap-2 text-sm text-text">
          <span className="mt-0.5 shrink-0 text-accent-orange">⚠</span>
          <span>{getOptionWarningLabel(warning)}</span>
        </li>
      ))}
    </ul>
  );
}

export function PaperBadge({ className = "" }: { className?: string }) {
  return (
    <span className={`${pillClassName(PAPER_BADGE_CLASS)} ${className}`.trim()}>
      Paper
    </span>
  );
}
