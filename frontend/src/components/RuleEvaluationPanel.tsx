import type { ReactNode } from "react";
import {
  Ban,
  XCircle,
  AlertTriangle,
  HelpCircle,
  CheckCircle2,
  MinusCircle,
  Info,
} from "lucide-react";
import type { RuleEvaluation, RuleResult, RuleStatus } from "@/types/activity-detail";

/**
 * Renders the persisted `rule_evaluation` object on an activity (backend/src/rule_evaluator.py,
 * schema_version 1). Historic activities without this field render nothing here — the rest of
 * the activity detail page is unaffected (byte-for-behavior compatibility).
 *
 * Financial-viability rules (cash / buying-power / share-coverage / collateral / margin) are out
 * of scope per product decision and are filtered defensively even though the backend never emits
 * them, so a stray field can never leak into the UI.
 */

// Display order per rule-evaluation design (blocked is most severe).
const STATUS_ORDER: RuleStatus[] = [
  "blocked",
  "fail",
  "warning",
  "unknown",
  "pass",
  "not_applicable",
  "informational",
];

// Sections open by default — everything that needs attention; pass/N/A stay collapsed.
const STATUS_OPEN_BY_DEFAULT = new Set<RuleStatus>(["blocked", "fail", "warning", "unknown", "informational"]);

const STATUS_META: Record<RuleStatus, { label: string; icon: typeof Ban; cls: string }> = {
  blocked: { label: "Blocked", icon: Ban, cls: "border-accent-red/40 bg-accent-red/20 text-accent-red" },
  fail: { label: "Failed", icon: XCircle, cls: "border-accent-red/40 bg-accent-red/15 text-accent-red" },
  warning: { label: "Warning", icon: AlertTriangle, cls: "border-accent-orange/40 bg-accent-orange/20 text-accent-orange" },
  unknown: { label: "Unknown", icon: HelpCircle, cls: "border-border bg-bg-input text-text-muted" },
  pass: { label: "Passed", icon: CheckCircle2, cls: "border-accent-green/40 bg-accent-green/20 text-accent-green" },
  not_applicable: { label: "Not applicable", icon: MinusCircle, cls: "border-border bg-bg-input text-text-muted" },
  informational: { label: "Informational", icon: Info, cls: "border-accent-blue/40 bg-accent-blue/20 text-accent-blue" },
};

// Buy Tracker scoring/trigger rule_ids in catalog order — used to build the compact
// five-dimension scorecard and WAIT-trigger strip without reordering the full status list.
const BT_SCORE_RULE_IDS = ["bt_value_entry", "bt_trend", "bt_momentum", "bt_income", "bt_calendar"];
const BT_TRIGGER_RULE_IDS = ["bt_wait_earnings", "bt_wait_rsi_80", "bt_wait_extended", "bt_wait_div_cut", "bt_wait_triple_bear"];

// Defensive out-of-scope filter — financial viability is never evaluated by the backend,
// but if a stray field ever appears we must not render it.
const FORBIDDEN_KEYWORDS = ["cash", "buying_power", "buying-power", "share_coverage", "share-coverage", "collateral", "margin"];

function isFinancialViabilityRule(rule: RuleResult): boolean {
  const haystack = `${rule.rule_id ?? ""} ${rule.label ?? ""} ${rule.group ?? ""}`.toLowerCase();
  return FORBIDDEN_KEYWORDS.some((k) => haystack.includes(k));
}

function statusMeta(status: string | undefined): { label: string; icon: typeof Ban; cls: string } {
  return STATUS_META[status as RuleStatus] ?? { label: status || "Unknown", icon: HelpCircle, cls: "border-border bg-bg-input text-text-muted" };
}

function titleCase(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

interface StatusGroup {
  status: RuleStatus;
  rules: RuleResult[];
}

function groupByStatus(rules: RuleResult[]): StatusGroup[] {
  const byStatus = new Map<RuleStatus, RuleResult[]>();
  for (const status of STATUS_ORDER) byStatus.set(status, []);
  for (const rule of rules) {
    const status = (rule.status as RuleStatus) in STATUS_META ? (rule.status as RuleStatus) : "unknown";
    byStatus.get(status)!.push(rule);
  }
  return STATUS_ORDER.filter((s) => byStatus.get(s)!.length > 0).map((status) => ({ status, rules: byStatus.get(status)! }));
}

function CountBadge({ status, count }: { status: RuleStatus; count: number }) {
  const meta = statusMeta(status);
  const Icon = meta.icon;
  return (
    <span
      role="listitem"
      className={`inline-flex items-center gap-1 rounded-[var(--radius-pill)] border px-2 py-0.5 text-xs font-medium ${meta.cls}`}
    >
      <Icon aria-hidden="true" size={13} />
      {count}
      <span className="sr-only">{meta.label}</span>
    </span>
  );
}

function RuleDetailBody({ rule }: { rule: RuleResult }) {
  const dataRefs = rule.data_refs && Object.keys(rule.data_refs).length > 0 ? rule.data_refs : null;
  return (
    <div className="space-y-1 border-t border-border/60 px-3 py-2 text-xs">
      {rule.expected && (
        <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-2">
          <span className="min-w-[70px] font-medium text-text-muted">Expected</span>
          <span>{rule.expected}</span>
        </div>
      )}
      {rule.observed && (
        <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-2">
          <span className="min-w-[70px] font-medium text-text-muted">Observed</span>
          <span>{rule.observed}</span>
        </div>
      )}
      {rule.detail && (
        <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-2">
          <span className="min-w-[70px] font-medium text-text-muted">Detail</span>
          <span>{rule.detail}</span>
        </div>
      )}
      {rule.source && (
        <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-2">
          <span className="min-w-[70px] font-medium text-text-muted">Source</span>
          <span>{titleCase(rule.source)}</span>
        </div>
      )}
      {dataRefs && (
        <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-2">
          <span className="min-w-[70px] font-medium text-text-muted">Data</span>
          <span className="font-mono">
            {Object.entries(dataRefs)
              .map(([k, v]) => `${k}: ${String(v)}`)
              .join(", ")}
          </span>
        </div>
      )}
    </div>
  );
}

function RulePill({ rule }: { rule: RuleResult }) {
  const meta = statusMeta(rule.status);
  const Icon = meta.icon;
  const ariaLabel = `${rule.label}: ${meta.label}${rule.observed ? ` — ${rule.observed}` : ""}`;
  return (
    <details className={`overflow-hidden rounded-[var(--radius)] border text-sm ${meta.cls} ${rule.blocking ? "border-l-4 border-l-accent-red" : ""}`}>
      <summary aria-label={ariaLabel} className="flex cursor-pointer flex-wrap items-center gap-2 px-3 py-1.5 [&::-webkit-details-marker]:hidden">
        <Icon aria-hidden="true" size={14} />
        <span className="font-semibold">{rule.label}</span>
        {rule.observed && <span className="text-text-muted">{rule.observed}</span>}
        {rule.group && (
          <span className="ml-auto rounded-[var(--radius-pill)] bg-bg-input px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-text-muted">
            {rule.group}
          </span>
        )}
      </summary>
      <RuleDetailBody rule={rule} />
    </details>
  );
}

function ScorecardCell({ rule }: { rule: RuleResult }) {
  const meta = statusMeta(rule.status);
  const Icon = meta.icon;
  const ariaLabel = `${rule.label}: ${meta.label}${rule.observed ? ` — ${rule.observed}` : ""}`;
  return (
    <details role="listitem" className={`overflow-hidden rounded-[var(--radius)] border text-sm ${meta.cls}`}>
      <summary aria-label={ariaLabel} className="flex cursor-pointer flex-col items-start gap-0.5 px-3 py-2 [&::-webkit-details-marker]:hidden">
        <span className="flex items-center gap-1.5 font-semibold">
          <Icon aria-hidden="true" size={14} />
          {rule.label}
        </span>
        {rule.observed && <span className="text-xs text-text-muted">{rule.observed}</span>}
      </summary>
      <RuleDetailBody rule={rule} />
    </details>
  );
}

function StatusSection({ group }: { group: StatusGroup }) {
  const meta = statusMeta(group.status);
  const Icon = meta.icon;
  return (
    <details open={STATUS_OPEN_BY_DEFAULT.has(group.status)} className="mb-3 last:mb-0">
      <summary className="flex cursor-pointer items-center gap-2 rounded-[var(--radius)] px-2 py-1.5 text-sm font-medium hover:bg-bg-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-blue [&::-webkit-details-marker]:hidden">
        <Icon aria-hidden="true" size={15} />
        {meta.label}
        <span className="rounded-[var(--radius-pill)] bg-bg-input px-1.5 py-0.5 text-xs text-text-muted">{group.rules.length}</span>
      </summary>
      <div className="mt-2 flex flex-wrap gap-2 pl-1">
        {group.rules.map((rule) => (
          <div key={rule.rule_id} className="w-full sm:w-[calc(50%-0.25rem)] lg:w-[calc(33.333%-0.35rem)]">
            <RulePill rule={rule} />
          </div>
        ))}
      </div>
    </details>
  );
}

function Section({ children }: { children: ReactNode }) {
  return <>{children}</>;
}

/**
 * Compact summary-count badges for list rows — rendered only when the activity carries a
 * persisted `rule_evaluation` (historic activities without the field render nothing).
 */
export function RuleStatusBadges({ evaluation, title }: { evaluation: RuleEvaluation; title?: string }) {
  const summaryCounts = evaluation.summary_counts ?? {};
  const withCounts = STATUS_ORDER.filter((status) => (summaryCounts[status] ?? 0) > 0);
  if (withCounts.length === 0) return null;
  return (
    <span role="list" aria-label={title ?? "Rule evaluation summary counts"} className="inline-flex flex-wrap items-center gap-1">
      {withCounts.map((status) => {
        const meta = statusMeta(status);
        const Icon = meta.icon;
        return (
          <span
            key={status}
            role="listitem"
            title={`${meta.label}: ${summaryCounts[status]}`}
            className={`inline-flex items-center gap-0.5 rounded-[var(--radius-pill)] border px-1.5 py-0.5 text-[11px] font-medium ${meta.cls}`}
          >
            <Icon aria-hidden="true" size={11} />
            {summaryCounts[status]}
            <span className="sr-only">{meta.label}</span>
          </span>
        );
      })}
    </span>
  );
}

export default function RuleEvaluationPanel({ evaluation }: { evaluation: RuleEvaluation }) {
  // Normalize into `blocks` — flat `rules` (non-monitor agents) vs. `phases` array
  // (monitor assessment/roll runs) both become { phase, statusGroups }[].
  const blocks: { phase?: string | null; rules: RuleResult[] }[] = evaluation.phases?.length
    ? evaluation.phases.map((p) => ({ phase: p.phase, rules: (p.rules ?? []).filter((r) => !isFinancialViabilityRule(r)) }))
    : [{ phase: evaluation.phase ?? null, rules: (evaluation.rules ?? []).filter((r) => !isFinancialViabilityRule(r)) }];

  const allRules = blocks.flatMap((b) => b.rules);
  const firstBlockerId = evaluation.first_blocker;
  const firstBlockerRule = firstBlockerId ? allRules.find((r) => r.rule_id === firstBlockerId) : undefined;

  const isBuyTracker = evaluation.agent_type === "buy_tracker";
  const byId = new Map(allRules.map((r) => [r.rule_id, r]));
  const scorecard = isBuyTracker ? BT_SCORE_RULE_IDS.map((id) => byId.get(id)).filter((r): r is RuleResult => !!r) : null;
  const triggers = isBuyTracker ? BT_TRIGGER_RULE_IDS.map((id) => byId.get(id)).filter((r): r is RuleResult => !!r) : null;
  const activeTriggers = triggers?.filter((r) => r.status === "blocked") ?? [];

  const summaryCounts = evaluation.summary_counts ?? {};

  return (
    <section className="space-y-4 rounded-[var(--radius)] border border-border bg-bg-card p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">Rule Evaluation</h2>
        <div role="list" aria-label="Rule evaluation summary counts" className="flex flex-wrap gap-1.5">
          {STATUS_ORDER.filter((status) => (summaryCounts[status] ?? 0) > 0).map((status) => (
            <CountBadge key={status} status={status} count={summaryCounts[status] ?? 0} />
          ))}
        </div>
      </div>

      {firstBlockerRule && (
        <div role="alert" className="flex items-start gap-2 rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-3 py-2 text-sm">
          <Ban aria-hidden="true" size={18} className="mt-0.5 shrink-0 text-accent-red" />
          <div>
            <strong className="text-accent-red">First blocker: {firstBlockerRule.label}</strong>
            {firstBlockerRule.observed && <div className="text-text-muted">{firstBlockerRule.observed}</div>}
          </div>
        </div>
      )}

      {scorecard && scorecard.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-text-muted">Scorecard</h3>
          <div role="list" aria-label="Buy Tracker five-dimension scorecard" className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-5">
            {scorecard.map((rule) => (
              <ScorecardCell key={rule.rule_id} rule={rule} />
            ))}
          </div>
          {triggers && triggers.length > 0 && (
            <div
              role="group"
              aria-label="Buy Tracker WAIT triggers"
              className={`flex flex-wrap items-center gap-1.5 rounded-[var(--radius)] border px-3 py-2 text-xs ${
                activeTriggers.length > 0 ? "border-accent-red/40 bg-accent-red/10" : "border-border bg-bg-input text-text-muted"
              }`}
            >
              {activeTriggers.length > 0 ? (
                <>
                  <span className="font-semibold text-accent-red">⚠ WAIT triggered by:</span>
                  {activeTriggers.map((rule) => (
                    <span key={rule.rule_id} className="rounded-[var(--radius-pill)] border border-accent-red/40 bg-accent-red/20 px-2 py-0.5 text-accent-red">
                      {rule.label}
                    </span>
                  ))}
                </>
              ) : (
                <span>No WAIT triggers active</span>
              )}
            </div>
          )}
        </div>
      )}

      {blocks.map((block, i) => (
        <Section key={i}>
          {block.phase && <h3 className="text-sm font-semibold text-text">{titleCase(block.phase)} Phase</h3>}
          {groupByStatus(block.rules).map((group) => (
            <StatusSection key={group.status} group={group} />
          ))}
        </Section>
      ))}
    </section>
  );
}
