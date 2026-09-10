"use client";

import { useState, useCallback } from "react";
import { RefreshCw, ChevronDown, ChevronRight } from "lucide-react";
import { createCorporateAction, correctCorporateActionGroup, getFxRate } from "@/lib/portfolio-api";
import type {
  BrokerAccount,
  CaEventType,
  CaLegType,
  CorporateActionCreateRequest,
  CorporateActionCorrectRequest,
  CorporateActionLegRequest,
  CostBasisStatus,
  LedgerMovement,
} from "@/types/portfolio";
import type { SecurityMaster } from "@/types/portfolio";
import { formatAccountName } from "@/lib/accountDisplay";

const inputCls =
  "w-full rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 text-sm text-text placeholder:text-text-muted focus:border-accent-blue focus:outline-none";
const labelCls = "mb-1 block text-xs font-medium text-text-muted";
const sectionHeadCls =
  "text-xs font-semibold uppercase tracking-wide text-text-muted";

// ─── Event type catalogue ──────────────────────────────────────────────────────

const EVENT_TYPES: Array<{ value: CaEventType; label: string; description: string; legs: CaLegType[] }> = [
  {
    value: "CASH_DIVIDEND",
    label: "Cash Dividend",
    description: "Ordinary cash dividend — broker sends cash to account",
    legs: ["CASH_DIVIDEND"],
  },
  {
    value: "DIVIDEND_WITH_SCRIP",
    label: "Dividend with Scrip",
    description: "Cash portion plus additional shares (scrip election)",
    legs: ["CASH_DIVIDEND", "SHARE_ACQUISITION"],
  },
  {
    value: "SCRIP_DIVIDEND",
    label: "Scrip Dividend",
    description: "Shares only — no cash payout; investor takes shares instead",
    legs: ["SHARE_ACQUISITION"],
  },
  {
    value: "RIGHTS_ISSUE",
    label: "Rights Issue",
    description: "New shares allocated from subscription rights",
    legs: ["SHARE_ACQUISITION"],
  },
  {
    value: "SHARE_CONSOLIDATION",
    label: "Share Consolidation",
    description: "Reverse split — fewer shares, same total cost basis; optional fractional cash",
    legs: ["CONSOLIDATION_OUT", "CONSOLIDATION_IN"],
  },
];

const CA_LEG_BADGE: Record<string, string> = {
  CASH_DIVIDEND: "bg-accent-blue/15 text-accent-blue",
  RIGHTS_SOLD: "bg-accent-red/15 text-accent-red",
  SHARE_ACQUISITION: "bg-accent-green/15 text-accent-green",
  CASH_TOP_UP: "bg-accent-orange/15 text-accent-orange",
  CONSOLIDATION_OUT: "bg-accent-red/15 text-accent-red",
  CONSOLIDATION_IN: "bg-accent-green/15 text-accent-green",
  FRACTIONAL_CASH_OUT: "bg-accent-orange/15 text-accent-orange",
};

const CA_LEG_LABEL: Record<string, string> = {
  CASH_DIVIDEND: "Cash Dividend",
  RIGHTS_SOLD: "Rights Sold",
  SHARE_ACQUISITION: "Share Acquisition",
  CASH_TOP_UP: "Investor Cash Top-Up",
  CONSOLIDATION_OUT: "Shares Removed",
  CONSOLIDATION_IN: "Shares Consolidated",
  FRACTIONAL_CASH_OUT: "Fractional Cash Settlement",
};

// ─── Form state ────────────────────────────────────────────────────────────────

type WhtDestState = "not_captured" | "zero" | "value";

export interface CaFormState {
  // Common
  security_id: string;
  account_id: string;
  event_type: CaEventType;
  payment_date: string;
  ex_dividend_date: string;
  currency: string;
  fx_rate: string;         // shared FX rate for all legs (EUR base)
  notes: string;

  // CASH_DIVIDEND leg (CASH_DIVIDEND, DIVIDEND_WITH_SCRIP)
  cd_gross: string;
  cd_gross_eur: string;
  cd_fees: string;
  cd_wht_src_country: string;
  cd_wht_src_amount: string;
  cd_wht_dest_state: WhtDestState;
  cd_wht_dest_country: string;
  cd_wht_dest_amount: string;

  // SHARE_ACQUISITION leg (DIVIDEND_WITH_SCRIP, SCRIP_DIVIDEND, RIGHTS_ISSUE)
  sa_quantity: string;
  sa_gross: string;          // FMV; "0" accepted for pure scrip
  sa_gross_eur: string;
  sa_cost_basis: CostBasisStatus;
  sa_notes: string;

  // Optional: RIGHTS_SOLD leg (RIGHTS_ISSUE only)
  rs_enabled: boolean;
  rs_quantity: string;
  rs_gross: string;
  rs_gross_eur: string;
  rs_fees: string;

  // Optional: CASH_TOP_UP leg (DIVIDEND_WITH_SCRIP only)
  ctu_enabled: boolean;
  ctu_gross: string;
  ctu_gross_eur: string;

  // SHARE_CONSOLIDATION fields
  co_quantity: string;                    // pre-consolidation shares to remove
  ci_quantity: string;                    // post-consolidation shares (= old × ratio)
  ci_transfer_cost_basis_eur: string;     // carried cost basis (operator reads from holdings)
  fco_enabled: boolean;                   // whether fractional cash leg is included
  fco_quantity: string;                   // fractional shares (e.g., "0.12")
  fco_gross: string;                      // cash proceeds in listing currency
  fco_gross_eur: string;                  // cash proceeds in EUR
  fco_fees: string;                       // fees on fractional disposal
}

const defaultState = (): CaFormState => ({
  security_id: "",
  account_id: "_unassigned",
  event_type: "CASH_DIVIDEND",
  payment_date: "",
  ex_dividend_date: "",
  currency: "EUR",
  fx_rate: "",
  notes: "",
  cd_gross: "",
  cd_gross_eur: "",
  cd_fees: "",
  cd_wht_src_country: "",
  cd_wht_src_amount: "",
  cd_wht_dest_state: "not_captured",
  cd_wht_dest_country: "ES",
  cd_wht_dest_amount: "",
  sa_quantity: "",
  sa_gross: "",
  sa_gross_eur: "",
  sa_cost_basis: "INCOMPLETE",
  sa_notes: "",
  rs_enabled: false,
  rs_quantity: "",
  rs_gross: "",
  rs_gross_eur: "",
  rs_fees: "",
  ctu_enabled: false,
  ctu_gross: "",
  ctu_gross_eur: "",
  co_quantity: "",
  ci_quantity: "",
  ci_transfer_cost_basis_eur: "",
  fco_enabled: false,
  fco_quantity: "",
  fco_gross: "",
  fco_gross_eur: "",
  fco_fees: "",
});

// ─── Pre-fill helper for group correction ─────────────────────────────────────

/** Build a CaFormState pre-filled from existing group legs for use in correction mode. */
export function buildCaInitialState(legs: LedgerMovement[], representative: LedgerMovement): Partial<CaFormState> {
  const cdLeg = legs.find((l) => l.ca_leg_type === "CASH_DIVIDEND");
  const saLeg = legs.find((l) => l.ca_leg_type === "SHARE_ACQUISITION");
  const rsLeg = legs.find((l) => l.ca_leg_type === "RIGHTS_SOLD");
  const ctuLeg = legs.find((l) => l.ca_leg_type === "CASH_TOP_UP");
  const coLeg = legs.find((l) => l.ca_leg_type === "CONSOLIDATION_OUT");
  const ciLeg = legs.find((l) => l.ca_leg_type === "CONSOLIDATION_IN");
  const fcoLeg = legs.find((l) => l.ca_leg_type === "FRACTIONAL_CASH_OUT");

  const baseLeg = cdLeg ?? saLeg ?? representative;
  const currency = baseLeg.gross?.currency ?? "EUR";
  const fxRate = baseLeg.fx?.rate ?? "";

  const state: Partial<CaFormState> = {
    security_id: representative.security_id ?? "",
    account_id: representative.account_id ?? "_unassigned",
    event_type: (representative.ca_event_type as CaEventType) ?? "CASH_DIVIDEND",
    payment_date: representative.trade_date ?? "",
    currency,
    fx_rate: fxRate,
    notes: "",
  };

  if (cdLeg) {
    state.cd_gross = cdLeg.gross?.amount ?? "";
    state.cd_gross_eur = cdLeg.gross?.eur_amount ?? "";
    state.cd_fees = cdLeg.fees?.total ?? "";
    state.cd_wht_src_country = cdLeg.withholding?.source?.country ?? "";
    state.cd_wht_src_amount = cdLeg.withholding?.source?.amount_eur ?? "";
    const dest = cdLeg.withholding?.destination;
    if (!dest) {
      state.cd_wht_dest_state = "not_captured";
    } else if (dest.amount_eur === "0") {
      state.cd_wht_dest_state = "zero";
      state.cd_wht_dest_country = dest.country ?? "ES";
    } else {
      state.cd_wht_dest_state = "value";
      state.cd_wht_dest_country = dest.country ?? "";
      state.cd_wht_dest_amount = dest.amount_eur ?? "";
    }
  }

  if (saLeg) {
    state.sa_quantity = saLeg.quantity ?? "";
    state.sa_gross = saLeg.gross?.amount ?? "";
    state.sa_gross_eur = saLeg.gross?.eur_amount ?? "";
    state.sa_cost_basis = (saLeg.cost_basis_status as CostBasisStatus) ?? "INCOMPLETE";
    state.sa_notes = "";
  }

  if (rsLeg) {
    state.rs_enabled = true;
    state.rs_quantity = rsLeg.quantity ?? "";
    state.rs_gross = rsLeg.gross?.amount ?? "";
    state.rs_gross_eur = rsLeg.gross?.eur_amount ?? "";
    state.rs_fees = rsLeg.fees?.total ?? "";
  }

  if (ctuLeg) {
    state.ctu_enabled = true;
    state.ctu_gross = ctuLeg.gross?.amount ?? "";
    state.ctu_gross_eur = ctuLeg.gross?.eur_amount ?? "";
  }

  if (coLeg) {
    state.co_quantity = coLeg.quantity ?? "";
  }
  if (ciLeg) {
    state.ci_quantity = ciLeg.quantity ?? "";
    state.ci_transfer_cost_basis_eur = ciLeg.transfer_cost_basis_eur ?? "";
  }
  if (fcoLeg) {
    state.fco_enabled = true;
    state.fco_quantity = fcoLeg.quantity ?? "";
    state.fco_gross = fcoLeg.gross?.amount ?? "";
    state.fco_gross_eur = fcoLeg.gross?.eur_amount ?? "";
    state.fco_fees = fcoLeg.fees?.total ?? "";
  }

  return state;
}

// ─── FX helper ────────────────────────────────────────────────────────────────

function FxHelper({ currency, date, onRate }: { currency: string; date: string; onRate: (r: string) => void }) {
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [rate, setRate] = useState<string | null>(null);

  const fetch = useCallback(async () => {
    if (!currency || currency === "EUR" || !date) return;
    setLoading(true);
    setErr(null);
    try {
      const r = await getFxRate(currency, "EUR", date);
      setRate(r.rate);
      onRate(r.rate);
    } catch {
      setErr("FX rate unavailable");
    } finally {
      setLoading(false);
    }
  }, [currency, date, onRate]);

  if (!currency || currency === "EUR") return null;

  return (
    <div className="col-span-full flex flex-wrap items-center gap-2 text-xs text-text-muted">
      {rate ? (
        <>
          <span className="text-accent-green">1 {currency} = {rate} EUR</span>
          <button type="button" onClick={fetch} className="text-text-muted hover:text-text" aria-label="Refresh rate">
            <RefreshCw size={10} />
          </button>
        </>
      ) : (
        <button
          type="button"
          onClick={fetch}
          disabled={loading || !date}
          className="underline hover:text-text disabled:opacity-40"
        >
          {loading ? "Fetching…" : `Get ${currency}/EUR rate`}
        </button>
      )}
      {err && <span className="text-accent-red">{err}</span>}
    </div>
  );
}

// ─── Collapsible leg section ───────────────────────────────────────────────────

function LegSection({
  legType,
  optional,
  enabled,
  onToggle,
  children,
}: {
  legType: string;
  optional?: boolean;
  enabled?: boolean;
  onToggle?: () => void;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(true);
  const isShown = !optional || enabled;

  return (
    <div className="rounded-[var(--radius)] border border-border bg-bg-card/30">
      <div
        className="flex items-center gap-2 px-4 py-2.5 cursor-pointer select-none"
        onClick={() => { if (isShown) setOpen((o) => !o); }}
      >
        {optional && (
          <input
            type="checkbox"
            checked={enabled}
            onChange={onToggle}
            onClick={(e) => e.stopPropagation()}
            className="rounded border-border"
            aria-label={`Enable ${CA_LEG_LABEL[legType] ?? legType} leg`}
          />
        )}
        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${CA_LEG_BADGE[legType] ?? "bg-bg-hover text-text-muted"}`}>
          {CA_LEG_LABEL[legType] ?? legType}
        </span>
        {optional && (
          <span className="text-xs text-text-muted">
            {enabled ? "included" : "not included"}
          </span>
        )}
        <span className="ml-auto text-text-muted">
          {open && isShown ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
      </div>
      {open && isShown && (
        <div className="border-t border-border/60 px-4 py-3">
          {children}
        </div>
      )}
    </div>
  );
}

// ─── Derived WHT rate field ────────────────────────────────────────────────────

function WhtRateDisplay({
  amount,
  grossEur,
  basisLabel = "gross",
}: {
  amount: string;
  grossEur: string;
  basisLabel?: string;
}) {
  const amtN = parseFloat(amount) || 0;
  const grossN = parseFloat(grossEur) || 0;
  const derived = amtN > 0 && grossN > 0 ? ((amtN / grossN) * 100).toFixed(2) : null;

  return (
    <div>
      <label className={labelCls}>Rate % (derived)</label>
      <div className={`${inputCls} bg-bg-hover text-text-muted cursor-default select-none`}>
        {derived != null ? `${derived}%` : "—"}
      </div>
      <p className="mt-0.5 text-xs text-text-muted">
        {derived != null ? `Auto-computed from amount ÷ ${basisLabel}` : "Enter amount above to derive rate"}
      </p>
    </div>
  );
}

// ─── WHT destination 3-state ───────────────────────────────────────────────────

function WhtDestFields({
  state,
  country,
  amount,
  grossEur,
  onState,
  onCountry,
  onAmount,
}: {
  state: WhtDestState;
  country: string;
  amount: string;
  grossEur: string;
  onState: (s: WhtDestState) => void;
  onCountry: (v: string) => void;
  onAmount: (v: string) => void;
}) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-4">
        {(["not_captured", "zero", "value"] as const).map((s) => (
          <label key={s} className="flex items-center gap-1.5 cursor-pointer select-none">
            <input
              type="radio"
              name="ca_wht_dest"
              value={s}
              checked={state === s}
              onChange={() => onState(s)}
              className="accent-accent-blue"
            />
            <span className="text-xs text-text">
              {s === "not_captured" ? "⚠ Not captured" : s === "zero" ? "€0.00 confirmed zero" : "Explicit amount"}
            </span>
          </label>
        ))}
      </div>
      {state === "value" && (
        <div className="grid grid-cols-3 gap-2">
          <div>
            <label className={labelCls}>Dest country</label>
            <input
              type="text"
              value={country}
              onChange={(e) => onCountry(e.target.value.toUpperCase())}
              maxLength={2}
              placeholder="ES"
              className={inputCls}
            />
          </div>
          <div>
            <label className={labelCls}>Amount € (primary)</label>
            <input
              type="number"
              step="any"
              min="0"
              value={amount}
              onChange={(e) => onAmount(e.target.value)}
              placeholder="0.00"
              className={inputCls}
            />
          </div>
          <WhtRateDisplay amount={amount} grossEur={grossEur} basisLabel="(gross − origin WHT)" />
        </div>
      )}
      {state === "not_captured" && (
        <p className="text-xs text-text-muted">No destination withholding recorded — net will not subtract destination tax.</p>
      )}
      {state === "zero" && (
        <p className="text-xs text-text-muted">Confirmed zero destination withholding.</p>
      )}
    </div>
  );
}

// ─── Build payload helpers ─────────────────────────────────────────────────────

function makeGross(amount: string, currency: string, eurAmount: string) {
  const eur = eurAmount || (currency === "EUR" ? amount : "0");
  return { amount: amount || "0", currency, eur_amount: eur || "0" };
}

function makeFees(total: string, currency: string) {
  if (!total || parseFloat(total) === 0) return null;
  const eur = currency === "EUR" ? total : "0";
  return { total, currency, total_eur: eur };
}

function derivedRate(amount: string, grossEur: string): string | undefined {
  const a = parseFloat(amount) || 0;
  const g = parseFloat(grossEur) || 0;
  if (a > 0 && g > 0) return ((a / g) * 100).toFixed(4);
  return undefined;
}

function buildWithholding(
  srcCountry: string,
  srcAmount: string,
  grossEur: string,
  destState: WhtDestState,
  destCountry: string,
  destAmount: string,
) {
  const hasSrc = !!(srcCountry || parseFloat(srcAmount) > 0);
  if (!hasSrc && destState === "not_captured") return undefined;

  const src = hasSrc
    ? {
        country: srcCountry || undefined,
        rate_pct: derivedRate(srcAmount, grossEur),
        amount_eur: srcAmount || "0",
      }
    : null;

  const destGrossEur = grossEur;
  let dest: { country?: string; rate_pct?: string; amount_eur: string } | null;
  if (destState === "not_captured") {
    dest = null;
  } else if (destState === "zero") {
    dest = { country: destCountry || "ES", rate_pct: "0", amount_eur: "0" };
  } else {
    dest = {
      country: destCountry || undefined,
      rate_pct: derivedRate(destAmount, destGrossEur),
      amount_eur: destAmount || "0",
    };
  }

  return { source: src, destination: dest };
}

function makeFx(fxRate: string, currency: string) {
  if (!fxRate || currency === "EUR") return undefined;
  return { rate: fxRate, rate_source: "ECB" as const };
}

// ─── Build legs from form state ────────────────────────────────────────────────

function buildLegs(form: CaFormState): CorporateActionLegRequest[] {
  const ev = form.event_type;
  const legs: CorporateActionLegRequest[] = [];

  // CASH_DIVIDEND leg
  if (ev === "CASH_DIVIDEND" || ev === "DIVIDEND_WITH_SCRIP") {
    const grossEur = form.cd_gross_eur || (form.currency === "EUR" ? form.cd_gross : "0");
    const wht = buildWithholding(
      form.cd_wht_src_country,
      form.cd_wht_src_amount,
      grossEur,
      form.cd_wht_dest_state,
      form.cd_wht_dest_country,
      form.cd_wht_dest_amount,
    );
    legs.push({
      leg_type: "CASH_DIVIDEND",
      trade_date: form.payment_date,
      gross: makeGross(form.cd_gross, form.currency, form.cd_gross_eur),
      fees: makeFees(form.cd_fees, form.currency),
      withholding: wht ?? undefined,
      fx: makeFx(form.fx_rate, form.currency),
    });
  }

  // SHARE_ACQUISITION leg
  if (ev === "DIVIDEND_WITH_SCRIP" || ev === "SCRIP_DIVIDEND" || ev === "RIGHTS_ISSUE") {
    legs.push({
      leg_type: "SHARE_ACQUISITION",
      trade_date: form.payment_date,
      quantity: form.sa_quantity || undefined,
      gross: makeGross(form.sa_gross || "0", form.currency, form.sa_gross_eur || "0"),
      cost_basis_status: form.sa_cost_basis,
      fx: makeFx(form.fx_rate, form.currency),
      notes: form.sa_notes || undefined,
    });
  }

  // RIGHTS_SOLD leg (optional — RIGHTS_ISSUE)
  if (ev === "RIGHTS_ISSUE" && form.rs_enabled) {
    const rsGrossEur = form.rs_gross_eur || (form.currency === "EUR" ? form.rs_gross : "0");
    legs.push({
      leg_type: "RIGHTS_SOLD",
      trade_date: form.payment_date,
      quantity: form.rs_quantity || undefined,
      gross: makeGross(form.rs_gross, form.currency, form.rs_gross_eur),
      fees: makeFees(form.rs_fees, form.currency),
      fx: makeFx(form.fx_rate, form.currency),
    });
    void rsGrossEur;
  }

  // CASH_TOP_UP leg (optional — DIVIDEND_WITH_SCRIP)
  if (ev === "DIVIDEND_WITH_SCRIP" && form.ctu_enabled) {
    legs.push({
      leg_type: "CASH_TOP_UP",
      trade_date: form.payment_date,
      gross: makeGross(form.ctu_gross, form.currency, form.ctu_gross_eur),
      fx: makeFx(form.fx_rate, form.currency),
    });
  }

  // SHARE_CONSOLIDATION legs
  if (ev === "SHARE_CONSOLIDATION") {
    legs.push({
      leg_type: "CONSOLIDATION_OUT",
      trade_date: form.payment_date,
      quantity: form.co_quantity || undefined,
      gross: makeGross("0", "EUR", "0"),
      fees: null,
    });
    legs.push({
      leg_type: "CONSOLIDATION_IN",
      trade_date: form.payment_date,
      quantity: form.ci_quantity || undefined,
      gross: makeGross("0", "EUR", "0"),
      transfer_cost_basis_eur: form.ci_transfer_cost_basis_eur || undefined,
    });
    if (form.fco_enabled) {
      legs.push({
        leg_type: "FRACTIONAL_CASH_OUT",
        trade_date: form.payment_date,
        quantity: form.fco_quantity || undefined,
        gross: makeGross(form.fco_gross, form.currency, form.fco_gross_eur),
        fees: makeFees(form.fco_fees, form.currency),
        fx: makeFx(form.fx_rate, form.currency),
      });
    }
  }

  return legs;
}

// ─── Validation ────────────────────────────────────────────────────────────────

function validate(form: CaFormState): string | null {
  if (!form.security_id) return "Symbol is required.";
  if (!form.payment_date) return "Payment date is required.";

  const ev = form.event_type;
  if (ev === "CASH_DIVIDEND" || ev === "DIVIDEND_WITH_SCRIP") {
    if (!form.cd_gross) return "Cash dividend gross amount is required.";
  }
  if (ev === "DIVIDEND_WITH_SCRIP" || ev === "SCRIP_DIVIDEND" || ev === "RIGHTS_ISSUE") {
    if (!form.sa_quantity) return "Share acquisition quantity is required.";
  }
  if (ev === "SHARE_CONSOLIDATION") {
    if (!form.co_quantity || parseFloat(form.co_quantity) <= 0) {
      return "Pre-consolidation share quantity is required and must be greater than zero.";
    }
    if (!form.ci_quantity || parseFloat(form.ci_quantity) <= 0) {
      return "Post-consolidation share quantity is required and must be greater than zero.";
    }
    if (!form.ci_transfer_cost_basis_eur || parseFloat(form.ci_transfer_cost_basis_eur) < 0) {
      return "Carried cost basis (€) is required and must not be negative.";
    }
    if (form.fco_enabled) {
      if (!form.fco_quantity || parseFloat(form.fco_quantity) <= 0) {
        return "Fractional cash-out quantity is required and must be greater than zero when enabled.";
      }
      if (!form.fco_gross) {
        return "Fractional cash-out gross proceeds is required when enabled.";
      }
    }
  }
  if (ev === "RIGHTS_ISSUE" && form.rs_enabled && !form.rs_gross) {
    return "Rights sold proceeds (gross) is required.";
  }
  if (ev === "DIVIDEND_WITH_SCRIP" && form.ctu_enabled && !form.ctu_gross) {
    return "Cash top-up amount is required when included.";
  }
  return null;
}

// ─── Net preview (CASH_DIVIDEND leg) ──────────────────────────────────────────

function CashDivNetPreview({ form }: { form: CaFormState }) {
  const grossEur = parseFloat(form.cd_gross_eur || (form.currency === "EUR" ? form.cd_gross : "0")) || 0;
  if (grossEur === 0) return null;

  const fees = parseFloat(form.cd_fees) || 0;
  const whtSrc = parseFloat(form.cd_wht_src_amount) || 0;
  const whtDest = form.cd_wht_dest_state === "value" ? (parseFloat(form.cd_wht_dest_amount) || 0) : 0;
  const net = grossEur - fees - whtSrc - whtDest;

  return (
    <div className="rounded-[var(--radius)] border border-border bg-bg-input/40 px-3 py-2 text-xs space-y-1">
      <div className="font-medium text-text-muted uppercase tracking-wide mb-1">Cash leg estimate (server is authoritative)</div>
      <div className="flex justify-between">
        <span>Gross</span>
        <span className="font-mono text-text">€{grossEur.toFixed(2)}</span>
      </div>
      {fees > 0 && (
        <div className="flex justify-between text-accent-red/80">
          <span>− Fees</span>
          <span className="font-mono">€{fees.toFixed(2)}</span>
        </div>
      )}
      {whtSrc > 0 && (
        <div className="flex justify-between text-accent-red/80">
          <span>− WHT origin</span>
          <span className="font-mono">€{whtSrc.toFixed(2)}</span>
        </div>
      )}
      {whtDest > 0 && (
        <div className="flex justify-between text-accent-red/80">
          <span>− WHT destination</span>
          <span className="font-mono">€{whtDest.toFixed(2)}</span>
        </div>
      )}
      <div className="flex justify-between border-t border-border/40 pt-1 font-medium">
        <span>Net</span>
        <span className="font-mono text-text">€{net.toFixed(2)}</span>
      </div>
    </div>
  );
}

// ─── Net preview (SHARE_CONSOLIDATION legs) ───────────────────────────────────

function ConsolidationSummaryPreview({ form }: { form: CaFormState }) {
  const coQty = parseFloat(form.co_quantity) || 0;
  const ciQty = parseFloat(form.ci_quantity) || 0;
  const carriedCost = parseFloat(form.ci_transfer_cost_basis_eur) || 0;
  if (coQty === 0 && ciQty === 0 && carriedCost === 0) return null;

  const fcoQty = form.fco_enabled ? (parseFloat(form.fco_quantity) || 0) : 0;
  const fractionalCostEstimate =
    form.fco_enabled && fcoQty > 0 && ciQty > 0 ? (fcoQty / ciQty) * carriedCost : 0;

  return (
    <div className="rounded-[var(--radius)] border border-border bg-bg-input/40 px-3 py-2 text-xs space-y-1">
      <div className="font-medium text-text-muted uppercase tracking-wide mb-1">Consolidation summary (server is authoritative)</div>
      <div className="flex justify-between">
        <span>Shares</span>
        <span className="font-mono text-text">
          {coQty || "—"} → {ciQty || "—"}
          {form.fco_enabled && fcoQty > 0 ? ` (−${fcoQty} fractional)` : ""}
        </span>
      </div>
      <div className="flex justify-between">
        <span>Cost carried</span>
        <span className="font-mono text-text">€{carriedCost.toFixed(2)}</span>
      </div>
      {form.fco_enabled && fcoQty > 0 && (
        <div className="flex justify-between text-accent-orange/80">
          <span>Fractional cost estimate</span>
          <span className="font-mono">€{fractionalCostEstimate.toFixed(2)}</span>
        </div>
      )}
    </div>
  );
}

// ─── Main form ─────────────────────────────────────────────────────────────────

export interface CorporateActionFormProps {
  accounts: BrokerAccount[];
  securities: SecurityMaster[];
  onSuccess: () => void;
  /** "correct" mode: uses the group correction endpoint instead of create. */
  mode?: "create" | "correct";
  /** Required when mode === "correct". */
  caGroupId?: string;
  /** Pre-fill form state (used in correction mode). */
  initialState?: Partial<CaFormState>;
}

export default function CorporateActionForm({
  accounts,
  securities,
  onSuccess,
  mode = "create",
  caGroupId,
  initialState,
}: CorporateActionFormProps) {
  const [form, setForm] = useState<CaFormState>(() => ({ ...defaultState(), ...initialState }));
  const [correctionNote, setCorrectionNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const set = useCallback((patch: Partial<CaFormState>) => setForm((f) => ({ ...f, ...patch })), []);

  const isCorrect = mode === "correct";
  const hasCashDiv = form.event_type === "CASH_DIVIDEND" || form.event_type === "DIVIDEND_WITH_SCRIP";
  const hasShareAcq =
    form.event_type === "DIVIDEND_WITH_SCRIP" ||
    form.event_type === "SCRIP_DIVIDEND" ||
    form.event_type === "RIGHTS_ISSUE";
  const canRightsSold = form.event_type === "RIGHTS_ISSUE";
  const canCashTopUp = form.event_type === "DIVIDEND_WITH_SCRIP";
  const isConsolidation = form.event_type === "SHARE_CONSOLIDATION";

  const cdGrossEur = form.cd_gross_eur || (form.currency === "EUR" ? form.cd_gross : "0");
  // Destination WHT is levied on the amount remaining after origin (source-country)
  // withholding has already been deducted, so the derived rate must use gross minus
  // origin WHT, not raw gross.
  const cdGrossEurAfterOriginWht = String(
    Math.max(0, (parseFloat(cdGrossEur) || 0) - (parseFloat(form.cd_wht_src_amount) || 0)),
  );

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const errMsg = validate(form);
    if (errMsg) { setError(errMsg); return; }
    if (isCorrect && !correctionNote.trim()) {
      setError("A correction note is required.");
      return;
    }

    setSaving(true);
    setError(null);
    try {
      if (isCorrect && caGroupId) {
        const request: CorporateActionCorrectRequest = {
          account_id: form.account_id || "_unassigned",
          correction_note: correctionNote.trim(),
          event_type: form.event_type,
          security_id: form.security_id || undefined,
          payment_date: form.payment_date || undefined,
          notes: form.notes || undefined,
          legs: buildLegs(form),
        };
        await correctCorporateActionGroup(caGroupId, request);
      } else {
        const request: CorporateActionCreateRequest = {
          event_type: form.event_type,
          security_id: form.security_id,
          account_id: form.account_id || "_unassigned",
          payment_date: form.payment_date,
          ex_dividend_date: form.ex_dividend_date || undefined,
          notes: form.notes || undefined,
          legs: buildLegs(form),
        };
        await createCorporateAction(request);
      }
      onSuccess();
    } catch (err) {
      const e = err as { data?: { detail?: string } };
      setError(e.data?.detail ?? (err instanceof Error ? err.message : isCorrect ? "Group correction failed." : "Failed to create corporate action."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {/* ── Correction note (correction mode only) ── */}
      {isCorrect && (
        <div>
          <label className={labelCls}>Correction note * <span className="text-accent-orange">(required)</span></label>
          <input
            type="text"
            value={correctionNote}
            onChange={(e) => setCorrectionNote(e.target.value)}
            placeholder="Describe what is being corrected…"
            className={inputCls}
            required
          />
          <p className="mt-0.5 text-xs text-text-muted">
            Saving replaces all group legs atomically. Original group is preserved as SUPERSEDED audit trail.
          </p>
        </div>
      )}

      {/* ── Common fields ── */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {/* Symbol — read-only in correction mode (cannot change partition) */}
        <div className="sm:col-span-2">
          <label className={labelCls}>Symbol {isCorrect ? "(locked)" : "*"}</label>
          {isCorrect ? (
            <div className={`${inputCls} bg-bg-hover text-text-muted cursor-default select-none`}>
              {form.security_id || "—"}
            </div>
          ) : (
            <select
              value={form.security_id}
              onChange={(e) => set({ security_id: e.target.value })}
              className={inputCls}
              required
            >
              <option value="">— Select symbol —</option>
              {securities.map((s) => (
                <option key={s.security_id} value={s.security_id}>
                  {s.ticker} — {s.company_name}
                </option>
              ))}
            </select>
          )}
        </div>

        {/* Account — read-only in correction mode (must match original partition) */}
        <div>
          <label className={labelCls}>Account {isCorrect ? "(locked)" : ""}</label>
          {isCorrect ? (
            <div className={`${inputCls} bg-bg-hover text-text-muted cursor-default select-none`}>
              {form.account_id === "_unassigned" ? "Unassigned" : form.account_id}
            </div>
          ) : (
            <select
              value={form.account_id}
              onChange={(e) => set({ account_id: e.target.value })}
              className={inputCls}
            >
              <option value="_unassigned">Unassigned</option>
              {accounts.map((a) => (
                <option key={a.account_id} value={a.account_id}>{formatAccountName(a)}</option>
              ))}
            </select>
          )}
        </div>

        {/* Event type */}
        <div>
          <label className={labelCls}>Event type *</label>
          <select
            value={form.event_type}
            onChange={(e) => set({ event_type: e.target.value as CaEventType })}
            className={inputCls}
          >
            {EVENT_TYPES.map((et) => (
              <option key={et.value} value={et.value}>{et.label}</option>
            ))}
          </select>
          <p className="mt-0.5 text-xs text-text-muted">
            {EVENT_TYPES.find((et) => et.value === form.event_type)?.description}
          </p>
        </div>

        {/* Payment date */}
        <div>
          <label className={labelCls}>Payment date *</label>
          <input
            type="date"
            value={form.payment_date}
            onChange={(e) => set({ payment_date: e.target.value })}
            className={inputCls}
            required
          />
        </div>

        {/* Ex-dividend date */}
        <div>
          <label className={labelCls}>Ex-dividend date (optional)</label>
          <input
            type="date"
            value={form.ex_dividend_date}
            onChange={(e) => set({ ex_dividend_date: e.target.value })}
            className={inputCls}
          />
        </div>

        {/* Currency */}
        <div>
          <label className={labelCls}>Currency</label>
          <input
            type="text"
            value={form.currency}
            onChange={(e) => set({ currency: e.target.value.toUpperCase() })}
            maxLength={3}
            placeholder="EUR"
            className={inputCls}
          />
        </div>

        {/* FX rate (shared) */}
        {form.currency !== "EUR" && (
          <>
            <div>
              <label className={labelCls}>{form.currency}/EUR rate</label>
              <input
                type="number"
                step="any"
                min="0"
                value={form.fx_rate}
                onChange={(e) => set({ fx_rate: e.target.value })}
                placeholder="0.000000000"
                className={inputCls}
              />
            </div>
            <FxHelper
              currency={form.currency}
              date={form.payment_date}
              onRate={(r) => set({ fx_rate: r })}
            />
          </>
        )}

        {/* Notes */}
        <div className="sm:col-span-2">
          <label className={labelCls}>Notes (applied to all legs)</label>
          <textarea
            value={form.notes}
            onChange={(e) => set({ notes: e.target.value })}
            rows={1}
            className={`${inputCls} resize-none`}
            placeholder="Optional context"
          />
        </div>
      </div>

      {/* ── Leg sections ── */}
      <div className="space-y-3">
        <div className={`${sectionHeadCls} mb-1`}>Legs</div>

        {/* CASH_DIVIDEND leg */}
        {hasCashDiv && (
          <LegSection legType="CASH_DIVIDEND">
            <div className="space-y-3">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <div>
                  <label className={labelCls}>Gross amount *</label>
                  <input
                    type="number"
                    step="any"
                    min="0"
                    value={form.cd_gross}
                    onChange={(e) => set({ cd_gross: e.target.value })}
                    placeholder="0.00"
                    className={inputCls}
                    required
                  />
                </div>
                {form.currency !== "EUR" && (
                  <div>
                    <label className={labelCls}>EUR equivalent</label>
                    <input
                      type="number"
                      step="any"
                      min="0"
                      value={form.cd_gross_eur}
                      onChange={(e) => set({ cd_gross_eur: e.target.value })}
                      placeholder="0.00"
                      className={inputCls}
                    />
                  </div>
                )}
                <div>
                  <label className={labelCls}>Fees (optional)</label>
                  <input
                    type="number"
                    step="any"
                    min="0"
                    value={form.cd_fees}
                    onChange={(e) => set({ cd_fees: e.target.value })}
                    placeholder="0.00"
                    className={inputCls}
                  />
                </div>
              </div>

              {/* WHT origin */}
              <div>
                <div className={`${sectionHeadCls} mb-2`}>Origin withholding (source country)</div>
                <div className="grid grid-cols-3 gap-2">
                  <div>
                    <label className={labelCls}>Country</label>
                    <input
                      type="text"
                      value={form.cd_wht_src_country}
                      onChange={(e) => set({ cd_wht_src_country: e.target.value.toUpperCase() })}
                      maxLength={2}
                      placeholder="US"
                      className={inputCls}
                    />
                  </div>
                  <div>
                    <label className={labelCls}>Amount € (primary)</label>
                    <input
                      type="number"
                      step="any"
                      min="0"
                      value={form.cd_wht_src_amount}
                      onChange={(e) => set({ cd_wht_src_amount: e.target.value })}
                      placeholder="0.00"
                      className={inputCls}
                    />
                  </div>
                  <WhtRateDisplay amount={form.cd_wht_src_amount} grossEur={cdGrossEur} />
                </div>
              </div>

              {/* WHT destination */}
              <div>
                <div className={`${sectionHeadCls} mb-2`}>Destination withholding</div>
                <WhtDestFields
                  state={form.cd_wht_dest_state}
                  country={form.cd_wht_dest_country}
                  amount={form.cd_wht_dest_amount}
                  grossEur={cdGrossEurAfterOriginWht}
                  onState={(s) => set({ cd_wht_dest_state: s })}
                  onCountry={(v) => set({ cd_wht_dest_country: v })}
                  onAmount={(v) => set({ cd_wht_dest_amount: v })}
                />
              </div>

              <CashDivNetPreview form={form} />
            </div>
          </LegSection>
        )}

        {/* SHARE_ACQUISITION leg */}
        {hasShareAcq && (
          <LegSection legType="SHARE_ACQUISITION">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <label className={labelCls}>Quantity *</label>
                <input
                  type="number"
                  step="any"
                  min="0"
                  value={form.sa_quantity}
                  onChange={(e) => set({ sa_quantity: e.target.value })}
                  placeholder="Shares received"
                  className={inputCls}
                  required
                />
              </div>
              <div>
                <label className={labelCls}>Cost basis</label>
                <select
                  value={form.sa_cost_basis}
                  onChange={(e) => set({ sa_cost_basis: e.target.value as CostBasisStatus })}
                  className={inputCls}
                >
                  <option value="INCOMPLETE">INCOMPLETE (scrip/rights — FMV pending)</option>
                  <option value="COMPLETE">COMPLETE (known FMV)</option>
                </select>
              </div>
              <div>
                <label className={labelCls}>Gross / FMV (0 for pure scrip)</label>
                <input
                  type="number"
                  step="any"
                  min="0"
                  value={form.sa_gross}
                  onChange={(e) => set({ sa_gross: e.target.value })}
                  placeholder="0.00"
                  className={inputCls}
                />
              </div>
              {form.currency !== "EUR" && (
                <div>
                  <label className={labelCls}>Gross EUR</label>
                  <input
                    type="number"
                    step="any"
                    min="0"
                    value={form.sa_gross_eur}
                    onChange={(e) => set({ sa_gross_eur: e.target.value })}
                    placeholder="0.00"
                    className={inputCls}
                  />
                </div>
              )}
              <div className="sm:col-span-2">
                <label className={labelCls}>Notes (e.g. FMV per share)</label>
                <input
                  type="text"
                  value={form.sa_notes}
                  onChange={(e) => set({ sa_notes: e.target.value })}
                  placeholder="Optional"
                  className={inputCls}
                />
              </div>
            </div>
          </LegSection>
        )}

        {/* Optional: RIGHTS_SOLD leg (RIGHTS_ISSUE) */}
        {canRightsSold && (
          <LegSection
            legType="RIGHTS_SOLD"
            optional
            enabled={form.rs_enabled}
            onToggle={() => set({ rs_enabled: !form.rs_enabled })}
          >
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <label className={labelCls}>Quantity (rights sold)</label>
                <input
                  type="number"
                  step="any"
                  min="0"
                  value={form.rs_quantity}
                  onChange={(e) => set({ rs_quantity: e.target.value })}
                  placeholder="Rights units"
                  className={inputCls}
                />
              </div>
              <div>
                <label className={labelCls}>Gross proceeds *</label>
                <input
                  type="number"
                  step="any"
                  min="0"
                  value={form.rs_gross}
                  onChange={(e) => set({ rs_gross: e.target.value })}
                  placeholder="0.00"
                  className={inputCls}
                />
              </div>
              {form.currency !== "EUR" && (
                <div>
                  <label className={labelCls}>Gross EUR</label>
                  <input
                    type="number"
                    step="any"
                    min="0"
                    value={form.rs_gross_eur}
                    onChange={(e) => set({ rs_gross_eur: e.target.value })}
                    placeholder="0.00"
                    className={inputCls}
                  />
                </div>
              )}
              <div>
                <label className={labelCls}>Fees</label>
                <input
                  type="number"
                  step="any"
                  min="0"
                  value={form.rs_fees}
                  onChange={(e) => set({ rs_fees: e.target.value })}
                  placeholder="0.00"
                  className={inputCls}
                />
              </div>
            </div>
          </LegSection>
        )}

        {/* Optional: CASH_TOP_UP leg (DIVIDEND_WITH_SCRIP) */}
        {canCashTopUp && (
          <LegSection
            legType="CASH_TOP_UP"
            optional
            enabled={form.ctu_enabled}
            onToggle={() => set({ ctu_enabled: !form.ctu_enabled })}
          >
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <label className={labelCls}>Investor top-up amount *</label>
                <input
                  type="number"
                  step="any"
                  min="0"
                  value={form.ctu_gross}
                  onChange={(e) => set({ ctu_gross: e.target.value })}
                  placeholder="0.00"
                  className={inputCls}
                />
                <p className="mt-0.5 text-xs text-text-muted">Cash contributed by investor to round to whole shares.</p>
              </div>
              {form.currency !== "EUR" && (
                <div>
                  <label className={labelCls}>EUR equivalent</label>
                  <input
                    type="number"
                    step="any"
                    min="0"
                    value={form.ctu_gross_eur}
                    onChange={(e) => set({ ctu_gross_eur: e.target.value })}
                    placeholder="0.00"
                    className={inputCls}
                  />
                </div>
              )}
            </div>
          </LegSection>
        )}

        {/* SHARE_CONSOLIDATION legs */}
        {isConsolidation && (
          <>
            <LegSection legType="CONSOLIDATION_OUT">
              <div>
                <label className={labelCls}>Pre-consolidation shares to remove *</label>
                <input
                  type="number"
                  step="any"
                  min="0"
                  value={form.co_quantity}
                  onChange={(e) => set({ co_quantity: e.target.value })}
                  placeholder="Shares before consolidation"
                  className={inputCls}
                  required
                />
              </div>
            </LegSection>

            <LegSection legType="CONSOLIDATION_IN">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <label className={labelCls}>Post-consolidation shares *</label>
                  <input
                    type="number"
                    step="any"
                    min="0"
                    value={form.ci_quantity}
                    onChange={(e) => set({ ci_quantity: e.target.value })}
                    placeholder="Shares after consolidation"
                    className={inputCls}
                    required
                  />
                </div>
                <div>
                  <label className={labelCls}>Carried cost basis € *</label>
                  <input
                    type="number"
                    step="any"
                    min="0"
                    value={form.ci_transfer_cost_basis_eur}
                    onChange={(e) => set({ ci_transfer_cost_basis_eur: e.target.value })}
                    placeholder="0.00"
                    className={inputCls}
                    required
                  />
                  <p className="mt-0.5 text-xs text-text-muted">
                    Read this from the Holdings page before creating the consolidation.
                  </p>
                </div>
              </div>
            </LegSection>

            <LegSection
              legType="FRACTIONAL_CASH_OUT"
              optional
              enabled={form.fco_enabled}
              onToggle={() => set({ fco_enabled: !form.fco_enabled })}
            >
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <label className={labelCls}>Fractional quantity *</label>
                  <input
                    type="number"
                    step="any"
                    min="0"
                    value={form.fco_quantity}
                    onChange={(e) => set({ fco_quantity: e.target.value })}
                    placeholder="e.g. 0.12"
                    className={inputCls}
                  />
                </div>
                <div>
                  <label className={labelCls}>Cash proceeds *</label>
                  <input
                    type="number"
                    step="any"
                    min="0"
                    value={form.fco_gross}
                    onChange={(e) => set({ fco_gross: e.target.value })}
                    placeholder="0.00"
                    className={inputCls}
                  />
                </div>
                {form.currency !== "EUR" && (
                  <div>
                    <label className={labelCls}>Cash proceeds EUR</label>
                    <input
                      type="number"
                      step="any"
                      min="0"
                      value={form.fco_gross_eur}
                      onChange={(e) => set({ fco_gross_eur: e.target.value })}
                      placeholder="0.00"
                      className={inputCls}
                    />
                  </div>
                )}
                <div>
                  <label className={labelCls}>Fees</label>
                  <input
                    type="number"
                    step="any"
                    min="0"
                    value={form.fco_fees}
                    onChange={(e) => set({ fco_fees: e.target.value })}
                    placeholder="0.00"
                    className={inputCls}
                  />
                </div>
              </div>
            </LegSection>

            <ConsolidationSummaryPreview form={form} />
          </>
        )}
      </div>

      {/* ── Error ── */}
      {error && (
        <div
          role="alert"
          className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-3 py-2 text-sm text-accent-red"
        >
          {error}
        </div>
      )}

      {/* ── Submit ── */}
      <div className="flex justify-end">
        <button
          type="submit"
          disabled={saving}
          className="rounded-[var(--radius)] bg-accent-blue px-5 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50 transition-opacity"
        >
          {saving
            ? isCorrect ? "Replacing…" : "Recording…"
            : isCorrect ? "Replace entire group" : "Record corporate action"}
        </button>
      </div>
    </form>
  );
}
