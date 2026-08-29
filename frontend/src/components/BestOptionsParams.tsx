"use client";

import type { ReactNode } from "react";
import { AlertTriangle, Info } from "lucide-react";
import type { BestOptionsParameters } from "@/types/best-options";

function fmtPct(v: number | null | undefined, digits = 1): string {
  return typeof v === "number" && isFinite(v) ? `${v.toFixed(digits)}%` : "—";
}
function fmtNum(v: number | null | undefined, digits = 2): string {
  return typeof v === "number" && isFinite(v) ? v.toFixed(digits) : "—";
}
function fmtDate(v: string | null | undefined): string {
  if (!v) return "—";
  return v.length >= 10 ? v.slice(0, 10) : v;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-wide text-text-muted">{label}</span>
      <span className="text-sm font-medium">{children}</span>
    </div>
  );
}

/**
 * Renders the `parameters` block from GET /api/symbols/{symbol}/best-options
 * (backend/src/best_options.py, design §6) — the single source of truth for what
 * the scorer actually consumed. Never re-derives thresholds/colours itself.
 *
 * Two disclosures are mandatory per the design, not optional: `category.defaulted`
 * (we are guessing the stock's profile) and `iv_rank_enforced: false` (a green row
 * here can coexist with a past agent WAIT that cited IV Rank — that is intended,
 * not a bug, but it must be visible).
 */
export default function BestOptionsParams({ parameters }: { parameters: BestOptionsParameters }) {
  const cat = parameters.category;
  const dte = parameters.dte;
  const th = parameters.thresholds;
  const chain = parameters.chain;

  return (
    <section className="space-y-3 rounded-[var(--radius)] border border-border bg-bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold">Analysis Parameters</h2>
        </div>
        <span className="shrink-0 rounded-[var(--radius-pill)] border border-border bg-bg-input px-2 py-1 text-[11px] text-text-muted">
          Evaluated {fmtDate(parameters.evaluated_at)} · schema v{parameters.schema_version}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <Field label="Category / Profile">
          {cat.label}
          {cat.defaulted && (
            <span
              title={`Raw value: ${cat.raw ?? "none"} (source: ${cat.source}) — defaulted to Balanced`}
              className="ml-1.5 inline-block rounded-[var(--radius-pill)] border border-accent-orange/40 bg-accent-orange/10 px-1.5 py-0.5 align-middle text-[10px] font-normal text-accent-orange"
            >
              defaulted
            </span>
          )}
        </Field>
        <Field label="Delta band, calls (|Δ|)">{th.call.delta_lo.toFixed(2)}–{th.call.delta_hi.toFixed(2)}</Field>
        <Field label="Delta band, puts (|Δ|)">{th.put.delta_lo.toFixed(2)}–{th.put.delta_hi.toFixed(2)}</Field>
        <Field label="Premium floor, calls (30d)">{fmtPct(th.call.premium_min_pct)}</Field>
        <Field label="Premium floor, puts (30d)">{fmtPct(th.put.premium_min_pct)}</Field>
        <Field label="Premium wait, calls (30d)">{fmtPct(th.call.premium_wait_pct)}</Field>
        <Field label="Premium wait, puts (30d)">{fmtPct(th.put.premium_wait_pct)}</Field>
        <Field label="DTE window">
          {dte.min}–{dte.max}d <span className="font-normal text-text-muted">(agent cap {dte.system_cap}d, {dte.timezone})</span>
        </Field>
        <Field label="Underlying price">{fmtNum(parameters.underlying.price)}</Field>
        <Field label="ATM IV">{fmtPct(parameters.atm_iv != null ? parameters.atm_iv * 100 : null)}</Field>
        <Field label="Premium basis">
          calls: {parameters.premium.basis.call}, puts: {parameters.premium.basis.put} (from bid)
        </Field>
        <Field label="Liquidity floor">OI ≥ {parameters.liquidity.min_open_interest}, spread ≤ {fmtPct(parameters.liquidity.max_spread_pct * 100, 0)}</Field>
        <Field label="Next earnings">
          {parameters.earnings.known ? fmtDate(parameters.earnings.next_earnings_date) : "Unknown"}
        </Field>
      </div>

      {!parameters.iv_rank_enforced && (
        <div role="note" className="flex items-start gap-2 rounded-[var(--radius)] border border-accent-orange/40 bg-accent-orange/10 px-3 py-2 text-xs text-accent-orange">
          <AlertTriangle aria-hidden="true" size={15} className="mt-0.5 shrink-0" />
          <span>
            IV Rank floor (calls {th.call.iv_rank_min != null ? th.call.iv_rank_min : "n/a"}, puts{" "}
            {th.put.iv_rank_min != null ? th.put.iv_rank_min : "n/a"}) is <strong>not enforced</strong> on this
            page. {parameters.iv_rank_note}
          </span>
        </div>
      )}

      {!parameters.earnings.known && (
        <div role="note" className="flex items-start gap-2 rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 text-xs text-text-muted">
          <Info aria-hidden="true" size={14} className="mt-0.5 shrink-0" />
          Next earnings date is unknown for this symbol — the earnings-span gate is never treated as failed for an
          unknown date; it renders as an informational flag on affected rows instead.
        </div>
      )}

      {chain.total_contracts > 0 && chain.stale_contracts > 0 && (
        <div role="note" className="flex items-start gap-2 rounded-[var(--radius)] border border-accent-orange/40 bg-accent-orange/10 px-3 py-2 text-xs text-accent-orange">
          <AlertTriangle aria-hidden="true" size={15} className="mt-0.5 shrink-0" />
          <span>
            {chain.stale_contracts} of {chain.total_contracts} contracts in this chain are serving last-known-good
            (stale) quotes
            {chain.quote_asof_min && chain.quote_asof_max ? ` — as of ${chain.quote_asof_min} through ${chain.quote_asof_max}` : ""}.
            Staleness never changes a row&apos;s colour.
          </span>
        </div>
      )}

      <details className="text-xs text-text-muted">
        <summary className="cursor-pointer select-none">Scoring weights, colour thresholds &amp; sources</summary>
        <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Field label="Annualized return">{fmtPct(parameters.weights.annualized_return * 100, 0)}</Field>
          <Field label="Cushion">{fmtPct(parameters.weights.cushion * 100, 0)}</Field>
          <Field label="Delta fit">{fmtPct(parameters.weights.delta_fit * 100, 0)}</Field>
          <Field label="Liquidity">{fmtPct(parameters.weights.liquidity * 100, 0)}</Field>
        </div>
        <p className="mt-2">
          Green ≥ {parameters.color_thresholds.green} · Yellow {parameters.color_thresholds.yellow}–
          {parameters.color_thresholds.green - 1} · Red &lt; {parameters.color_thresholds.yellow}, any failed safety
          gate, or premium below the DTE-scaled wait floor.
        </p>
        <p className="mt-2">
          Thresholds source — calls: <code>{parameters.thresholds_source.call}</code>
          <br />
          Thresholds source — puts: <code>{parameters.thresholds_source.put}</code>
          <br />
          Skill reference — calls: <code>{parameters.skill_reference.call}</code>
          <br />
          Skill reference — puts: <code>{parameters.skill_reference.put}</code>
          <br />
          DTE-scaled premium: <code>{parameters.premium.dte_scaling}</code>
        </p>
        <p className="mt-2">Chain timestamp: {parameters.chain.timestamp ?? "—"}</p>
      </details>
    </section>
  );
}
