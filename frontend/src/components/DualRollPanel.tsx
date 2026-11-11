import RuleEvaluationPanel from "@/components/RuleEvaluationPanel";
import type { ActivityDoc, RollAlternativeId } from "@/types/activity-detail";

function money(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? `$${value.toFixed(2)}` : "—";
}

export default function DualRollPanel({ activity }: { activity: ActivityDoc }) {
  const dual = activity.roll_alternatives;
  if (!dual) return null;
  const alpha = activity.alpha_view;
  return (
    <section className="space-y-4 rounded-[var(--radius)] border border-border bg-bg-card p-4">
      <h2 className="text-lg font-semibold">Roll alternatives</h2>
      <p className="text-sm">
        Principal prefers <strong>{dual.preferred_alternative_id}</strong>. {dual.preference_reason}
      </p>
      <p className="text-sm text-text-muted">
        Apply roll always targets DEFENSIVE. CREDIT_EXTENDED is advisory only.
        All credits are estimates before commissions, not guaranteed fills.
      </p>
      <div className="space-y-1 border-t border-border pt-3 text-sm">
        <h3 className="font-semibold">Supervisor audit</h3>
        <p className="text-text-muted">
          The Supervisor audits risk and data for each available destination below;
          it is not a third roll strategy. Unavailable destinations cannot receive a contract approval.
          There is no separate Apply Recommendation action for these advisory reviews.
        </p>
        {!activity.supervisor_view && (
          <p className="text-text-muted">No Supervisor result saved for this activity — no approval inferred.</p>
        )}
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        {(["DEFENSIVE", "CREDIT_EXTENDED"] as RollAlternativeId[]).map((id) => {
          const candidate = dual.alternatives[id];
          const available = candidate?.status === "available";
          const review = activity.supervisor_view?.alternative_reviews?.[id];
          const reviewed = review?.review_status === "complete"
            && ["ORIGINAL_HOLDS", "RECONSIDER"].includes(review.net_assessment ?? "")
            && ["WEAK", "MODERATE", "STRONG"].includes(review.challenge_strength ?? "");
          const economics = candidate?.roll_economics;
          return (
            <article key={id} className="min-w-0 space-y-3 rounded-[var(--radius)] border border-border p-3">
              <h3 className="font-semibold">{id}</h3>
              <p className="text-sm">{candidate?.status?.replaceAll("_", " ") ?? "unavailable"}</p>
              {candidate?.reason && <p className="text-sm text-text-muted">{candidate.reason}</p>}
              {!!candidate?.expiry_checks?.length && (
                <details className="text-sm">
                  <summary className="cursor-pointer">Expiry eligibility checks · {candidate.expiry_checks.length}</summary>
                  <ul className="mt-2 space-y-3">
                    {candidate.expiry_checks.map((check) => (
                      <li key={check.expiration} className="space-y-1 border-l-2 border-border pl-2">
                        <p className="font-mono">{check.expiration} · {check.status.replaceAll("_", " ")}</p>
                        <p>New bid / ask: {money(check.bid)} / {money(check.ask)} · held ask: {money(check.buyback_ask)}</p>
                        <p className="text-text-muted">Quote as of: {check.quote_asof ?? "unknown"}</p>
                        {check.reasons.map((reason, i) => <p key={i}>{reason}</p>)}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              {available && (
                <>
                  <p className="font-mono text-sm">
                    ${candidate.new_strike} · {candidate.new_expiration} · {candidate.dte} DTE
                  </p>
                  <dl className="space-y-1 text-sm">
                    <div><dt className="inline">Buyback ask / new bid: </dt><dd className="inline">{money(economics?.buyback_cost)} / {money(economics?.new_premium)}</dd></div>
                    <div><dt className="inline">Estimated net credit/debit per share: </dt><dd className="inline">{money(economics?.net_credit)}</dd></div>
                    {economics?.net_credit_total != null && (
                      <div><dt className="inline">Estimated total before commissions: </dt><dd className="inline">{money(economics.net_credit_total)} ({economics.quantity} × {economics.multiplier})</dd></div>
                    )}
                  </dl>
                  {candidate.rule_evaluation && <RuleEvaluationPanel evaluation={candidate.rule_evaluation} />}
                </>
              )}
              <div className="space-y-2 border-t border-border pt-2 text-sm">
                <h4 className="font-semibold">Supervisor · {id}</h4>
                {!available ? (
                  <p className="text-text-muted">Not audited as a destination: no eligible executable contract. See the unavailability reason above.</p>
                ) : reviewed ? (
                  <>
                    <p>{review.net_assessment === "RECONSIDER" ? "⚠ Reconsider" : "Original decision holds"} · {review.challenge_strength}</p>
                    <p>{review.one_liner}</p>
                    {review.counter_arguments?.map((arg, i) => (
                      <p key={i}>{arg.point} {arg.data_support}</p>
                    ))}
                  </>
                ) : <p className="text-text-muted">Scoped audit unavailable — not an approval.</p>}
              </div>
            </article>
          );
        })}
      </div>
      <div className="space-y-2 border-t border-border pt-3 text-sm">
        <h3 className="font-semibold">Alpha comparison</h3>
        {alpha?.comparison_status === "complete" && alpha.preferred_alternative_id
          && typeof alpha.comparison_rationale === "string" && alpha.comparison_rationale.trim()
          && dual.alternatives[alpha.preferred_alternative_id]?.status === "available" ? (
          <>
            <p>Alpha prefers <strong>{alpha.preferred_alternative_id}</strong> (advisory; apply target unchanged).</p>
            <p>{alpha.comparison_rationale}</p>
          </>
        ) : <p className="text-text-muted">Scoped comparison unavailable — no preference inferred from a legacy verdict.</p>}
        {alpha?.additional_proposal_status === "available" && alpha.additional_alternative && (
          <div className="space-y-1 rounded-[var(--radius)] border border-border p-3">
            <h4 className="font-semibold">Additional Alpha suggestion · advisory only</h4>
            <p>Baseline: {alpha.additional_alternative.baseline_alternative_id} · relaxed: {alpha.relaxed_parameter}</p>
            <p>${alpha.additional_alternative.strike} · {alpha.additional_alternative.expiration} · {alpha.additional_alternative.dte} DTE</p>
            <p>{alpha.parameter_detail} {alpha.additional_alternative.rationale}</p>
            <p>Trade-off: {alpha.additional_alternative.trade_off}</p>
            <p>Estimated net credit/debit before commissions: {money(alpha.additional_alternative.roll_economics.net_credit)} per share; total {money(alpha.additional_alternative.roll_economics.net_credit_total)}.</p>
          </div>
        )}
        {alpha?.additional_proposal_status === "rejected" && (
          <p className="text-text-muted">Additional proposal rejected: {alpha.additional_proposal_reason}</p>
        )}
      </div>
    </section>
  );
}
