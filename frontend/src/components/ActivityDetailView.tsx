import Link from "next/link";
import ActivityActions from "@/components/ActivityActions";
import ApplyRecommendation from "@/components/ApplyRecommendation";
import type { ActivityDetail, ActivityDoc } from "@/types/activity-detail";

function activityClass(a: string | undefined): string {
  const s = (a ?? "").toLowerCase();
  if (s.includes("strong_buy") || s === "buy" || s.includes("open")) return "border-accent-green/40 bg-accent-green/10 text-accent-green";
  if (s.includes("sell") || s.includes("close") || s.includes("assign")) return "border-accent-red/40 bg-accent-red/10 text-accent-red";
  if (s.includes("roll")) return "border-accent-orange/40 bg-accent-orange/10 text-accent-orange";
  if (s.includes("wait") || s.includes("hold")) return "border-border bg-bg-input text-text-muted";
  return "border-accent-blue/40 bg-accent-blue/10 text-accent-blue";
}

function confidenceClass(c: string | undefined): string {
  const s = (c ?? "").toLowerCase();
  if (s === "high") return "border-accent-green/40 bg-accent-green/10 text-accent-green";
  if (s === "low") return "border-accent-red/40 bg-accent-red/10 text-accent-red";
  return "border-accent-orange/40 bg-accent-orange/10 text-accent-orange";
}

function riskRatingMeta(r: number): { label: string; cls: string } {
  if (r <= 2) return { label: "Low", cls: "border-accent-green/40 bg-accent-green/10 text-accent-green" };
  if (r <= 4) return { label: "Moderate", cls: "border-accent-orange/40 bg-accent-orange/10 text-accent-orange" };
  if (r <= 6) return { label: "Elevated", cls: "border-accent-orange/40 bg-accent-orange/10 text-accent-orange" };
  if (r <= 8) return { label: "High", cls: "border-accent-red/40 bg-accent-red/10 text-accent-red" };
  return { label: "Very High", cls: "border-accent-red/40 bg-accent-red/10 text-accent-red" };
}

function assignmentRiskClass(r: string): string {
  const s = r.toLowerCase();
  if (s.includes("high")) return "border-accent-red/40 bg-accent-red/10 text-accent-red";
  if (s.includes("medium") || s.includes("moderate")) return "border-accent-orange/40 bg-accent-orange/10 text-accent-orange";
  return "border-accent-green/40 bg-accent-green/10 text-accent-green";
}

function Badge({ text, className }: { text: string; className: string }) {
  return (
    <span className={`inline-block rounded-[var(--radius-pill)] border px-2 py-0.5 text-xs ${className}`}>
      {text}
    </span>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <div className="text-xs uppercase tracking-wide text-text-muted">{label}</div>
      <div className="text-sm text-text">{children}</div>
    </div>
  );
}

function titleCase(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function pick(...vals: (number | string | null | undefined)[]): number | string | null {
  for (const v of vals) if (v !== null && v !== undefined && v !== "") return v;
  return null;
}

function SupervisorPanel({ sv, activity }: { sv: NonNullable<ActivityDoc["supervisor_view"]>; activity: ActivityDoc }) {
  const strength = String(sv.challenge_strength ?? "").toUpperCase();
  const strengthLabel =
    strength === "WEAK" ? "Decision is solid"
    : strength === "MODERATE" ? "Points to consider"
    : strength === "STRONG" ? "Seriously reconsider"
    : sv.challenge_strength ?? "—";
  const strengthCls =
    strength === "STRONG" ? "border-accent-red/40 bg-accent-red/10 text-accent-red"
    : strength === "MODERATE" ? "border-accent-orange/40 bg-accent-orange/10 text-accent-orange"
    : "border-accent-green/40 bg-accent-green/10 text-accent-green";
  const reconsider = String(sv.net_assessment ?? "").toUpperCase() === "RECONSIDER";
  return (
    <section className="space-y-3 rounded-[var(--radius)] border border-border bg-bg-card p-4">
      <div className="flex items-center gap-2">
        <span>🛡️</span>
        <h2 className="text-lg font-semibold">Supervisor</h2>
        <Badge text={strengthLabel} className={strengthCls} />
      </div>
      {sv.counter_arguments && sv.counter_arguments.length > 0 && (
        <ol className="list-decimal space-y-2 pl-5 text-sm">
          {sv.counter_arguments.map((arg, i) => (
            <li key={i}>
              <span className="font-medium">{arg.point}</span>
              {arg.data_support && <span className="block text-text-muted">{arg.data_support}</span>}
            </li>
          ))}
        </ol>
      )}
      <div className="text-sm">
        {reconsider ? (
          <span>⚠️ <strong>Reconsider</strong>{sv.one_liner && <> — <em>{sv.one_liner}</em></>}</span>
        ) : (
          <span>✅ <strong>Original decision holds</strong>{sv.one_liner && <> — <em>{sv.one_liner}</em></>}</span>
        )}
      </div>
      {activity.id && (
        <div className="pt-1">
          <ApplyRecommendation activity={activity} sourceAgent="supervisor" />
        </div>
      )}
    </section>
  );
}

function AlphaPanel({ av, activity }: { av: NonNullable<ActivityDoc["alpha_view"]>; activity: ActivityDoc }) {
  const strength = String(av.opportunity_strength ?? "").toUpperCase();
  const strengthLabel =
    strength === "NONE" ? "No safe relaxation"
    : strength === "MODERATE" ? "Relaxed alternative"
    : strength === "STRONG" ? "Near-threshold alternative"
    : av.opportunity_strength ?? "—";
  const strengthCls =
    strength === "NONE" ? "border-border bg-bg-input text-text-muted"
    : "border-accent-orange/40 bg-accent-orange/10 text-accent-orange";
  const alt = av.alternative;
  const contract = alt
    ? [
        alt.strike != null && `Strike: $${alt.strike}`,
        alt.expiration && `Exp: ${alt.expiration}`,
        alt.dte != null && `(${alt.dte} DTE)`,
        alt.premium != null && `Premium: $${alt.premium}`,
        alt.delta != null && `Δ ${alt.delta}`,
      ].filter(Boolean).join(" · ")
    : "";
  return (
    <section className="space-y-3 rounded-[var(--radius)] border border-border bg-bg-card p-4">
      <div className="flex items-center gap-2">
        <span>🔍</span>
        <h2 className="text-lg font-semibold">Alpha Advisor</h2>
        <Badge text={strengthLabel} className={strengthCls} />
      </div>
      {av.relaxed_parameter && av.relaxed_parameter !== "none" && (
        <div className="rounded-[var(--radius)] border-l-2 border-accent-orange bg-accent-orange/10 px-3 py-2 text-sm">
          🔓 <strong>Relaxed:</strong> {titleCase(av.relaxed_parameter)}
          {av.parameter_detail && <> — {av.parameter_detail}</>}
        </div>
      )}
      {alt && (
        <div className="space-y-2 text-sm">
          {alt.action && <div className="font-medium">→ {alt.action}</div>}
          {alt.rationale && <div className="text-text-muted">{alt.rationale}</div>}
          {alt.premium_comparison && <div>💰 {alt.premium_comparison}</div>}
          {contract && (
            <div className="rounded-[var(--radius)] bg-bg-input px-3 py-2 font-mono text-xs">
              📋 <strong>Alternative contract:</strong> {contract}
            </div>
          )}
          {alt.trade_off ? (
            <div>⚖️ Trade-off: {alt.trade_off}</div>
          ) : alt.additional_risk ? (
            <div>⚠️ Risk: {alt.additional_risk}</div>
          ) : null}
        </div>
      )}
      <div className="text-sm">
        {strength === "NONE" ? (
          <span>✅ <strong>No safe parameter relaxation available</strong>{av.one_liner && <> — <em>{av.one_liner}</em></>}</span>
        ) : (
          <span>🔓 <strong>Parameter relaxation alternative</strong>{av.one_liner && <> — <em>{av.one_liner}</em></>}</span>
        )}
      </div>
      {activity.id && (
        <div className="pt-1">
          <ApplyRecommendation activity={activity} sourceAgent="alpha_advisor" />
        </div>
      )}
    </section>
  );
}

export default function ActivityDetailView({ data }: { data: ActivityDetail }) {
  const a = data.activity;
  const dStrike = pick(a.strike, a.new_strike, a.current_strike);
  const dExpiration = pick(a.expiration, a.new_expiration, a.current_expiration);
  const hasStrikeChange = a.current_strike != null && a.new_strike != null;
  const hasExpChange = a.current_expiration != null && a.new_expiration != null;

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Link href={`/symbols/${encodeURIComponent(data.symbol)}`} className="text-sm text-accent-blue hover:underline">
          ← {data.display_name}
        </Link>
        <h1 className="text-2xl font-semibold">Activity Detail</h1>

        {data.is_alert && (
          <div className="rounded-[var(--radius)] border border-accent-orange/40 bg-accent-orange/10 px-4 py-2 text-sm text-accent-orange">
            ⚡ This activity triggered an alert
          </div>
        )}
        {a.data_error && (
          <div className="rounded-[var(--radius)] border border-accent-orange/40 bg-accent-orange/10 px-4 py-2 text-sm text-accent-orange">
            ⚠️ This analysis was performed with partial data — some data resources were unavailable.
          </div>
        )}
        {a.created_from && (
          <div className="rounded-[var(--radius)] border border-border bg-bg-input px-4 py-2 text-sm text-text-muted">
            📎 Created from {a.created_from.source_agent === "alpha_advisor" ? "Alpha Advisor" : "Supervisor"} recommendation on{" "}
            {a.created_from.source_activity_id ? (
              <Link href={`/activities/${encodeURIComponent(a.created_from.source_activity_id)}`} className="text-accent-blue hover:underline">
                original activity
              </Link>
            ) : (
              "original activity"
            )}
            {a.created_from.recommendation && <> — <em>{a.created_from.recommendation.slice(0, 100)}</em></>}
          </div>
        )}
      </div>

      {/* Activity card */}
      <section className="space-y-4 rounded-[var(--radius)] border border-border bg-bg-card p-4">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-lg font-semibold">Activity</h2>
          {a.activity && <Badge text={a.activity} className={activityClass(a.activity)} />}
        </div>

        {a.reason && <p className="whitespace-pre-wrap text-sm text-text">{a.reason}</p>}

        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          <Field label="Timestamp"><span className="font-mono">{a.timestamp || "—"}</span></Field>
          <Field label="Agent">{data.agent_label || "—"}</Field>
          <Field label="Symbol"><span className="font-mono">{data.symbol || "—"}</span></Field>
          <Field label="Activity">{a.activity ? <Badge text={a.activity} className={activityClass(a.activity)} /> : "—"}</Field>
          <Field label="Confidence">
            {a.confidence ? <Badge text={String(a.confidence)} className={confidenceClass(String(a.confidence))} /> : "—"}
          </Field>
          {typeof a.risk_rating === "number" ? (
            <Field label="Risk Rating">
              {(() => {
                const m = riskRatingMeta(a.risk_rating!);
                return <Badge text={`${a.risk_rating}/10 ${m.label}`} className={m.cls} />;
              })()}
            </Field>
          ) : a.assignment_risk ? (
            <Field label="Assignment Risk">
              <Badge text={a.assignment_risk} className={assignmentRiskClass(a.assignment_risk)} />
            </Field>
          ) : null}
          {a.underlying_price != null && (
            <Field label="Underlying Price"><span className="font-mono">${a.underlying_price}</span></Field>
          )}
          {dStrike != null && <Field label="Strike"><span className="font-mono">${dStrike}</span></Field>}
          {dExpiration != null && <Field label="Expiration"><span className="font-mono">{dExpiration}</span></Field>}
          {hasStrikeChange && (
            <>
              <Field label="Current Strike"><span className="font-mono">${a.current_strike}</span></Field>
              <Field label="New Strike"><span className="font-mono">${a.new_strike}</span></Field>
            </>
          )}
          {hasExpChange && (
            <>
              <Field label="Current Expiration"><span className="font-mono">{a.current_expiration}</span></Field>
              <Field label="New Expiration"><span className="font-mono">{a.new_expiration}</span></Field>
            </>
          )}
          {a.premium != null && <Field label="Premium"><span className="font-mono">${a.premium}</span></Field>}
          {a.iv != null && <Field label="IV"><span className="font-mono">{a.iv}%</span></Field>}
          {a.risk_flags && a.risk_flags.length > 0 && (
            <Field label="Risk Flags">
              <span className="flex flex-wrap gap-1">
                {a.risk_flags.map((f, i) => (
                  <Badge key={i} text={f} className="border-border bg-bg-input text-text-muted" />
                ))}
              </span>
            </Field>
          )}
          {a.position_id && <Field label="Position ID"><span className="font-mono">{a.position_id}</span></Field>}
        </div>
      </section>

      {a.supervisor_view && <SupervisorPanel sv={a.supervisor_view} activity={a} />}
      {a.alpha_view && <AlphaPanel av={a.alpha_view} activity={a} />}

      {a.id && (
        <ActivityActions
          symbol={data.symbol}
          activityId={a.id}
          agentType={data.agent_type}
          isAlert={data.is_alert}
        />
      )}
    </div>
  );
}
