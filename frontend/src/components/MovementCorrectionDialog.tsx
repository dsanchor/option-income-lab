"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { History, RefreshCw, X } from "lucide-react";
import { correctMovement, getFxRate } from "@/lib/portfolio-api";
import type {
  BrokerAccount,
  CostBasisStatus,
  FxRateSource,
  LedgerMovement,
  MovementCorrectionRequest,
  OptionContractType,
  OptionLinkKind,
  OptionTxnType,
  WithholdingLegInput,
} from "@/types/portfolio";
import { OPTION_TXN_TYPES, SALES_TYPE_LABELS } from "@/types/portfolio";
import { getAccountName } from "@/lib/accountDisplay";
import OptionPositionLinkPicker from "./OptionPositionLinkPicker";

const inputCls =
  "w-full rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 text-sm text-text placeholder:text-text-muted focus:border-accent-blue focus:outline-none";
const labelCls = "mb-1 block text-xs font-medium text-text-muted";
const sectionHeadCls = "text-xs font-semibold uppercase tracking-wide text-text-muted mb-2";

const OPTION_LINK_KIND_OPTIONS: OptionLinkKind[] = ["OPEN_SELL", "CLOSE_BUY", "ASSIGNMENT_STOCK"];
const OPTION_TYPE_OPTIONS: OptionContractType[] = ["call", "put"];
const OPTION_LINK_KIND_BY_TXN_TYPE: Record<OptionTxnType, Extract<OptionLinkKind, "OPEN_SELL" | "CLOSE_BUY">> = {
  CALL_SELL: "OPEN_SELL",
  CALL_BUY: "CLOSE_BUY",
  PUT_SELL: "OPEN_SELL",
  PUT_BUY: "CLOSE_BUY",
};
const OPTION_TYPE_BY_TXN_TYPE: Record<OptionTxnType, OptionContractType> = {
  CALL_SELL: "call",
  CALL_BUY: "call",
  PUT_SELL: "put",
  PUT_BUY: "put",
};

type WithholdingDestState = "not_captured" | "zero" | "value";

function initDestState(m: LedgerMovement): WithholdingDestState {
  if (!m.withholding?.destination) return "not_captured";
  if (m.withholding.destination.amount_eur === "0") return "zero";
  return "value";
}

// ─── FxHelper ─────────────────────────────────────────────────────────────────

function FxHelper({
  currency,
  date,
  onApply,
}: {
  currency: string;
  date: string;
  onApply: (rate: string) => void;
}) {
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [rate, setRate] = useState<string | null>(null);

  const fetchRate = useCallback(async () => {
    if (!currency || currency === "EUR" || !date) return;
    setLoading(true);
    setErr(null);
    try {
      const result = await getFxRate(currency, "EUR", date);
      setRate(result.rate);
    } catch {
      setErr("FX rate unavailable");
    } finally {
      setLoading(false);
    }
  }, [currency, date]);

  if (!currency || currency === "EUR") return null;

  return (
    <div className="col-span-full flex flex-wrap items-center gap-2 text-xs text-text-muted">
      {rate ? (
        <>
          <span className="text-accent-green">
            1 {currency} = {rate} EUR
          </span>
          <button type="button" onClick={fetchRate} className="text-text-muted hover:text-text" aria-label="Refresh FX rate">
            <RefreshCw size={10} />
          </button>
          <button
            type="button"
            onClick={() => onApply(rate)}
            className="rounded border border-border px-2 py-0.5 hover:bg-bg-hover"
          >
            Apply to EUR amounts
          </button>
        </>
      ) : (
        <button
          type="button"
          onClick={fetchRate}
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

// ─── WithholdingSourceSection ─────────────────────────────────────────────────

function WithholdingSourceSection({
  country,
  amount,
  derivedRate,
  onChange,
}: {
  country: string;
  amount: string;
  /** H.1: always derived from amount ÷ gross; never user-editable per contract */
  derivedRate?: string | null;
  onChange: (f: { country?: string; amount?: string }) => void;
}) {
  return (
    <div className="grid grid-cols-3 gap-2">
      <div>
        <label className={labelCls}>Origin country</label>
        <input
          type="text"
          value={country}
          onChange={(e) => onChange({ country: e.target.value.toUpperCase() })}
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
          value={amount}
          onChange={(e) => onChange({ amount: e.target.value })}
          placeholder="0.00"
          className={inputCls}
        />
      </div>
      <div>
        <label className={labelCls}>Rate % (derived)</label>
        <div className={`${inputCls} bg-bg-hover text-text-muted cursor-default select-none`}>
          {derivedRate != null ? `${derivedRate}%` : "—"}
        </div>
        <p className="mt-0.5 text-xs text-text-muted">
          {derivedRate != null ? "Auto-computed from amount ÷ gross" : "Enter amount above to derive rate"}
        </p>
      </div>
    </div>
  );
}

// ─── WithholdingDestSection (3-state) ─────────────────────────────────────────

function WithholdingDestSection({
  destState,
  country,
  amount,
  derivedRate,
  onStateChange,
  onFieldChange,
}: {
  destState: WithholdingDestState;
  country: string;
  amount: string;
  /** H.1: always derived from amount ÷ gross; never user-editable per contract */
  derivedRate?: string | null;
  onStateChange: (s: WithholdingDestState) => void;
  onFieldChange: (f: { country?: string; amount?: string }) => void;
}) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-4">
        {(["not_captured", "zero", "value"] as const).map((s) => (
          <label key={s} className="flex items-center gap-1.5 cursor-pointer select-none">
            <input
              type="radio"
              name="wht_dest_state"
              value={s}
              checked={destState === s}
              onChange={() => onStateChange(s)}
              className="accent-accent-blue"
            />
            <span className="text-xs text-text">
              {s === "not_captured" ? "⚠ Not captured (null)" : s === "zero" ? "€0.00 confirmed zero" : "Value"}
            </span>
          </label>
        ))}
      </div>

      {destState === "value" && (
        <div className="grid grid-cols-3 gap-2">
          <div>
            <label className={labelCls}>Dest country</label>
            <input
              type="text"
              value={country}
              onChange={(e) => onFieldChange({ country: e.target.value.toUpperCase() })}
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
              onChange={(e) => onFieldChange({ amount: e.target.value })}
              placeholder="0.00"
              className={inputCls}
            />
          </div>
          <div>
            <label className={labelCls}>Rate % (derived)</label>
            <div className={`${inputCls} bg-bg-hover text-text-muted cursor-default select-none`}>
              {derivedRate != null ? `${derivedRate}%` : "—"}
            </div>
            <p className="mt-0.5 text-xs text-text-muted">
              {derivedRate != null ? "Auto-computed from amount ÷ (gross − origin WHT)" : "Enter amount above to derive rate"}
            </p>
          </div>
        </div>
      )}

      {destState === "not_captured" && (
        <p className="text-xs text-text-muted">
          Broker did not capture destination withholding. Net will not subtract any destination tax.
        </p>
      )}
      {destState === "zero" && (
        <p className="text-xs text-text-muted">
          Confirmed zero destination withholding — no tax withheld at destination country.
        </p>
      )}
    </div>
  );
}

// ─── Main dialog ─────────────────────────────────────────────────────────────

export interface MovementCorrectionDialogProps {
  movement: LedgerMovement;
  accounts?: BrokerAccount[];
  onClose: () => void;
  onCorrected: () => void;
}

export default function MovementCorrectionDialog({
  movement: m,
  accounts = [],
  onClose,
  onCorrected,
}: MovementCorrectionDialogProps) {
  const isTransfer = m.txn_type === "TRANSFER_OUT" || m.txn_type === "TRANSFER_IN";
  const isOptionTxn = OPTION_TXN_TYPES.includes(m.txn_type as OptionTxnType);
  const supportsOptionMetadata = isOptionTxn || m.txn_type === "BUY" || m.txn_type === "SELL";
  const optionTxnType = isOptionTxn ? (m.txn_type as OptionTxnType) : null;
  const hasWithholding = m.txn_type === "DIVIDEND" || m.txn_type === "SELL";
  const originalGross = m.gross;
  const originalFees = m.fees;
  const originalWithholding = m.withholding;

  // ── Shared fields ──────────────────────────────────────────────────────────
  const [reason, setReason] = useState("");
  const [tradeDate, setTradeDate] = useState(m.trade_date ?? "");
  const [notes, setNotes] = useState("");

  // ── Quantity ───────────────────────────────────────────────────────────────
  const [quantityEnabled, setQuantityEnabled] = useState(m.quantity !== null);
  const [quantity, setQuantity] = useState(m.quantity ?? "");

  // ── Gross ──────────────────────────────────────────────────────────────────
  const [grossAmount, setGrossAmount] = useState(originalGross?.amount ?? "");
  const [grossCurrency, setGrossCurrency] = useState(originalGross?.currency ?? "");
  const [grossEurAmount, setGrossEurAmount] = useState(originalGross?.eur_amount ?? "");

  // ── Fees ───────────────────────────────────────────────────────────────────
  const [feesTotal, setFeesTotal] = useState(originalFees?.total ?? "");
  const [feesCurrency, setFeesCurrency] = useState(originalFees?.currency ?? "");
  const [feesEur, setFeesEur] = useState(originalFees?.total_eur ?? "");

  // ── FX ─────────────────────────────────────────────────────────────────────
  const [fxRate, setFxRate] = useState(m.fx?.rate ?? "");
  const [fxSource, setFxSource] = useState<FxRateSource>(m.fx?.rate_source ?? "ECB");

  // ── Sales type (SELL) ──────────────────────────────────────────────────────
  const [salesType, setSalesType] = useState<"ACCIONES" | "DERECHOS">(m.sales_type ?? "ACCIONES");

  // ── Cost basis status (BUY) ────────────────────────────────────────────────
  const [costBasisStatus, setCostBasisStatus] = useState<CostBasisStatus>(
    m.cost_basis_status ?? "COMPLETE"
  );

  // ── Option metadata ────────────────────────────────────────────────────────
  const [optionPositionId, setOptionPositionId] = useState(m.option_position_id ?? "");
  const [optionLinkKind, setOptionLinkKind] = useState<OptionLinkKind | "">(
    (m.option_link_kind as OptionLinkKind | undefined) ??
      (optionTxnType ? OPTION_LINK_KIND_BY_TXN_TYPE[optionTxnType] : "")
  );
  const [optionType, setOptionType] = useState<OptionContractType | "">(
    (m.option_type as OptionContractType | undefined) ??
      (optionTxnType ? OPTION_TYPE_BY_TXN_TYPE[optionTxnType] : "")
  );
  const [optionStrike, setOptionStrike] = useState(
    m.option_strike != null ? String(m.option_strike) : ""
  );
  const [optionExpiration, setOptionExpiration] = useState(m.option_expiration ?? "");
  const [optionSymbol, setOptionSymbol] = useState(m.option_symbol ?? "");
  const [optionCloseDate, setOptionCloseDate] = useState(m.option_close_date ?? "");

  // ── Withholding source ─────────────────────────────────────────────────────
  const [whtSrcCountry, setWhtSrcCountry] = useState(originalWithholding?.source?.country ?? "");
  const [whtSrcAmount, setWhtSrcAmount] = useState(originalWithholding?.source?.amount_eur ?? "");

  // ── Withholding destination (3-state) ──────────────────────────────────────
  const [whtDestState, setWhtDestState] = useState<WithholdingDestState>(() => initDestState(m));
  const [whtDestCountry, setWhtDestCountry] = useState(originalWithholding?.destination?.country ?? "ES");
  const [whtDestAmount, setWhtDestAmount] = useState(originalWithholding?.destination?.amount_eur ?? "");

  // ── SELL withholding toggle (collapsible; default open when original had WHT) ──
  const [showSellWht, setShowSellWht] = useState(
    !!(originalWithholding?.source || originalWithholding?.destination)
  );

  // ── Dialog state ───────────────────────────────────────────────────────────
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<{ originalId: string; correctedId: string } | null>(null);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  // FX apply: populate fxRate, recompute EUR amounts from amount × rate
  const handleFxApply = useCallback(
    (rate: string) => {
      const r = parseFloat(rate);
      if (isNaN(r) || r <= 0) return;
      setFxRate(rate);
      const g = parseFloat(grossAmount);
      if (!isNaN(g)) setGrossEurAmount((g * r).toFixed(6));
      const f = parseFloat(feesTotal);
      if (!isNaN(f)) setFeesEur((f * r).toFixed(6));
    },
    [grossAmount, feesTotal]
  );

  // Net preview (client-side estimate; server is authoritative)
  const netPreview = useMemo(() => {
    const g = parseFloat(grossEurAmount);
    if (isNaN(g)) return null;
    const f = isNaN(parseFloat(feesEur)) ? 0 : parseFloat(feesEur);
    const whtSrc = hasWithholding ? (parseFloat(whtSrcAmount) || 0) : 0;
    const whtDest =
      hasWithholding && whtDestState === "value" ? (parseFloat(whtDestAmount) || 0) : 0;
    const isBuyLike = m.txn_type === "BUY" || m.txn_type === "CALL_BUY" || m.txn_type === "PUT_BUY";
    return isBuyLike ? g + f : g - f - whtSrc - whtDest;
  }, [grossEurAmount, feesEur, hasWithholding, m.txn_type, whtSrcAmount, whtDestState, whtDestAmount]);

  // Amendment H.1: derived WHT rates (amounts are primary inputs)
  const grossEurNum = parseFloat(grossEurAmount) || (grossCurrency === "EUR" ? parseFloat(grossAmount) : 0);
  const derivedSrcRate =
    grossEurNum > 0 && parseFloat(whtSrcAmount) > 0
      ? ((parseFloat(whtSrcAmount) / grossEurNum) * 100).toFixed(2)
      : null;
  // Destination WHT is levied on the amount remaining after origin withholding,
  // so its rate base is gross minus the origin WHT amount, not raw gross.
  const grossEurNetOfOriginWht = Math.max(0, grossEurNum - (parseFloat(whtSrcAmount) || 0));
  const derivedDestRate =
    grossEurNetOfOriginWht > 0 && whtDestState === "value" && parseFloat(whtDestAmount) > 0
      ? ((parseFloat(whtDestAmount) / grossEurNetOfOriginWht) * 100).toFixed(2)
      : null;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!reason.trim()) {
      setError("A correction reason is required for the audit trail.");
      return;
    }
    setSaving(true);
    setError(null);

    try {
      const payload: MovementCorrectionRequest = {
        account_id: m.account_id,
        correction_note: reason.trim(),
      };
      let hasChanges = false;

      // Trade date
      if (tradeDate && tradeDate !== m.trade_date) {
        payload.trade_date = tradeDate;
        hasChanges = true;
      }

      // Quantity
      if (m.txn_type === "DIVIDEND") {
        if (!quantityEnabled && m.quantity != null) {
          // User cleared quantity that originally existed
          payload.quantity = null;
          hasChanges = true;
        } else if (quantityEnabled && quantity && quantity !== (m.quantity ?? "")) {
          payload.quantity = quantity;
          hasChanges = true;
        }
      } else if (!isOptionTxn && m.quantity !== null && quantity && quantity !== m.quantity) {
        payload.quantity = quantity;
        hasChanges = true;
      }

      // Gross (send as a unit when any of the 3 fields changed)
      if (
        grossAmount !== (originalGross?.amount ?? "") ||
        grossCurrency !== (originalGross?.currency ?? "") ||
        grossEurAmount !== (originalGross?.eur_amount ?? "")
      ) {
        if (!grossAmount || !grossCurrency || !grossEurAmount) {
          setError(
            "All gross fields (amount, currency, EUR amount) must be filled when correcting gross."
          );
          setSaving(false);
          return;
        }
        payload.gross = {
          amount: grossAmount,
          currency: grossCurrency,
          eur_amount: grossEurAmount,
        };
        hasChanges = true;
      }

      // Fees (send as a unit)
      if (
        feesTotal !== (originalFees?.total ?? "") ||
        feesCurrency !== (originalFees?.currency ?? "") ||
        feesEur !== (originalFees?.total_eur ?? "")
      ) {
        if (!feesTotal || !feesCurrency || !feesEur) {
          setError(
            "All fees fields (total, currency, EUR total) must be filled when correcting fees."
          );
          setSaving(false);
          return;
        }
        payload.fees = {
          total: feesTotal,
          currency: feesCurrency,
          total_eur: feesEur,
        };
        hasChanges = true;
      }

      // FX
      if (fxRate && (fxRate !== (m.fx?.rate ?? "") || fxSource !== (m.fx?.rate_source ?? "ECB"))) {
        payload.fx = { rate: fxRate, rate_source: fxSource };
        hasChanges = true;
      }

      // Sales type (SELL only)
      if (m.txn_type === "SELL" && salesType !== (m.sales_type ?? "ACCIONES")) {
        payload.sales_type = salesType;
        hasChanges = true;
      }

      // Cost basis status (BUY only)
      if (m.txn_type === "BUY" && costBasisStatus !== (m.cost_basis_status ?? "COMPLETE")) {
        payload.cost_basis_status = costBasisStatus;
        hasChanges = true;
      }

      if (supportsOptionMetadata) {
        const normalizedOptionPositionId = optionPositionId.trim();
        const originalOptionPositionId = m.option_position_id ?? "";
        if (normalizedOptionPositionId && normalizedOptionPositionId !== originalOptionPositionId) {
          payload.option_position_id = normalizedOptionPositionId;
          hasChanges = true;
        }

        const originalLinkKind = (m.option_link_kind as OptionLinkKind | undefined) ?? "";
        if (optionLinkKind && optionLinkKind !== originalLinkKind) {
          payload.option_link_kind = optionLinkKind;
          hasChanges = true;
        }

        const originalOptionType = (m.option_type as OptionContractType | undefined) ?? "";
        if (optionType && optionType !== originalOptionType) {
          payload.option_type = optionType;
          hasChanges = true;
        }

        const originalStrike = m.option_strike != null ? String(m.option_strike) : "";
        if (optionStrike.trim() && optionStrike.trim() !== originalStrike) {
          payload.option_strike = Number(optionStrike);
          hasChanges = true;
        }

        if (optionExpiration && optionExpiration !== (m.option_expiration ?? "")) {
          payload.option_expiration = optionExpiration;
          hasChanges = true;
        }

        const normalizedOptionSymbol = optionSymbol.trim();
        if (normalizedOptionSymbol && normalizedOptionSymbol !== (m.option_symbol ?? "")) {
          payload.option_symbol = normalizedOptionSymbol;
          hasChanges = true;
        }

        if (optionCloseDate && optionCloseDate !== (m.option_close_date ?? "")) {
          payload.option_close_date = optionCloseDate;
          hasChanges = true;
        }

        if ((payload.option_link_kind || optionLinkKind) === "ASSIGNMENT_STOCK" && !(
          payload.option_position_id || normalizedOptionPositionId || m.option_position_id
        )) {
          setError("Assignment-linked stock movements require an option position ID.");
          setSaving(false);
          return;
        }
      }

      // Withholding (DIVIDEND always visible; SELL only when toggle enabled)
      const whtVisible =
        m.txn_type === "DIVIDEND" || (m.txn_type === "SELL" && showSellWht);
      if (whtVisible) {
        const origDestState = initDestState(m);

        // H.1: rate_pct is derived — exclude from change detection (amount/country are authoritative)
        const srcChanged =
          whtSrcCountry !== (originalWithholding?.source?.country ?? "") ||
          whtSrcAmount !== (originalWithholding?.source?.amount_eur ?? "");

        const destChanged =
          whtDestState !== origDestState ||
          (whtDestState === "value" &&
            (whtDestCountry !== (originalWithholding?.destination?.country ?? "ES") ||
              whtDestAmount !== (originalWithholding?.destination?.amount_eur ?? "")));

        if (srcChanged || destChanged) {
          // H.1: rate_pct is server-authoritative — derive from amounts; server overwrites anyway
          const grossEurForRate = parseFloat(grossEurAmount) || (grossCurrency === "EUR" ? parseFloat(grossAmount) : 0);
          const srcRatePct = grossEurForRate > 0 && parseFloat(whtSrcAmount) > 0
            ? ((parseFloat(whtSrcAmount) / grossEurForRate) * 100).toFixed(4)
            : undefined;
          const destRatePct = grossEurForRate > 0 && parseFloat(whtDestAmount) > 0
            ? ((parseFloat(whtDestAmount) / grossEurForRate) * 100).toFixed(4)
            : undefined;

          // Build source leg
          const srcObj: WithholdingLegInput | null =
            whtSrcCountry || whtSrcAmount
              ? {
                  country: whtSrcCountry || undefined,
                  rate_pct: srcRatePct,
                  amount_eur: whtSrcAmount || undefined,
                }
              : null;

          // Build destination leg from 3-state control
          let destObj: WithholdingLegInput | null;
          if (whtDestState === "not_captured") {
            destObj = null;
          } else if (whtDestState === "zero") {
            destObj = { country: "ES", rate_pct: "0", amount_eur: "0" };
          } else {
            if (!whtDestAmount) {
              setError("Withholding destination amount is required when 'Value' is selected.");
              setSaving(false);
              return;
            }
            destObj = {
              country: whtDestCountry || undefined,
              rate_pct: destRatePct,
              amount_eur: whtDestAmount,
            };
          }

          payload.withholding = { source: srcObj, destination: destObj };
          hasChanges = true;
        }
      }

      // Notes
      if (notes.trim()) {
        payload.notes = notes.trim();
        hasChanges = true;
      }

      if (!hasChanges) {
        setError("No fields changed. Edit at least one field to create a correction.");
        setSaving(false);
        return;
      }

      const result = await correctMovement(m.id, payload);
      setSuccess({ originalId: result.original.id, correctedId: result.replacement.id });
    } catch (err) {
      const e = err as { status?: number; data?: { error?: string; detail?: string } };
      if (e.data?.error === "group_leg_correction_required") {
        setError(
          "Financial fields on group legs must be corrected atomically. " +
          "Close this dialog and use \u201CReplace entire group\u201D from the movement detail instead.",
        );
      } else {
        setError(e.data?.detail ?? (err instanceof Error ? err.message : "Correction failed"));
      }
    } finally {
      setSaving(false);
    }
  }

  const fmtEur = (v: string | null | undefined) => {
    if (!v) return "—";
    const n = Number(v);
    return isNaN(n) ? v : `€${n.toLocaleString("es-ES", { minimumFractionDigits: 2 })}`;
  };

  return (
    <div
      className="fixed inset-0 z-[210] flex items-start justify-center overflow-auto bg-black/70 p-4"
      onClick={onClose}
    >
      <div
        className="mt-12 mb-12 w-full max-w-[700px] rounded-[var(--radius)] border border-border bg-bg-card"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Correct movement"
      >
        {/* ── Header ── */}
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <div className="flex items-center gap-2">
            <History size={16} className="text-accent-orange" />
            <h3 className="text-base font-semibold text-text">Correct Movement</h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-[var(--radius)] p-1 text-text-muted hover:bg-bg-hover hover:text-text transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          {/* ── Audit notice ── */}
          <div className="rounded-[var(--radius)] border border-accent-orange/30 bg-accent-orange/5 px-4 py-3 text-sm text-text-muted">
            <p className="font-medium text-text mb-1">Auditable correction — original preserved</p>
            <p>
              Saving creates a <strong className="text-text">corrected replacement</strong>{" "}
              movement. The original is{" "}
              <strong className="text-text">preserved in history</strong> and marked superseded.
              No data is deleted.
            </p>
          </div>

          {/* ── Original summary ── */}
          <div className="rounded-[var(--radius)] bg-bg-input/50 px-4 py-3 text-xs space-y-1">
            <div className="font-semibold text-text-muted uppercase tracking-wide mb-1">
              Original (will be preserved)
            </div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 sm:grid-cols-3">
              <span className="text-text-muted">Type:</span>
              <span className="font-mono text-text">{m.txn_type}</span>
              <span className="text-text-muted hidden sm:block">Account:</span>
              <span className="font-mono text-text col-span-2 sm:col-span-1 hidden sm:block truncate">{getAccountName(m.account_id, accounts)}</span>
              <span className="text-text-muted">Symbol:</span>
              <span className="font-mono text-text">
                {m.ticker} ({m.security_id})
              </span>
              <span className="text-text-muted">Date:</span>
              <span className="font-mono text-text">{m.trade_date}</span>
              {m.quantity != null && (
                <>
                  <span className="text-text-muted">Quantity:</span>
                  <span className="font-mono text-text">{m.quantity}</span>
                </>
              )}
              <span className="text-text-muted">Gross:</span>
              <span className="font-mono text-text">{fmtEur(m.gross?.eur_amount)}</span>
              <span className="text-text-muted">Net:</span>
              <span className="font-mono text-text">{fmtEur(m.net?.eur_amount)}</span>
              <span className="text-text-muted">ID:</span>
              <span className="font-mono text-text-muted truncate col-span-2 sm:col-span-1">
                {m.id}
              </span>
            </div>
          </div>

          {/* ── Transfer: not correctable ── */}
          {isTransfer && (
            <div className="rounded-[var(--radius)] border border-accent-orange/40 bg-accent-orange/5 px-4 py-4 text-sm text-text-muted space-y-2">
              <p className="font-medium text-text">⚠ Transfers cannot be corrected individually</p>
              <p>
                Transfer movements are paired (TRANSFER_OUT + TRANSFER_IN). Correcting one half
                would break the pair invariant.
              </p>
              <p>
                <strong className="text-text">To fix a transfer error:</strong> Void both movements
                in the transfer pair, then create a new transfer with the correct values.
              </p>
            </div>
          )}

          {/* ── Success ── */}
          {success && (
            <div className="space-y-3">
              <div className="rounded-[var(--radius)] border border-accent-green/30 bg-accent-green/5 px-4 py-3 text-sm">
                <p className="font-medium text-accent-green mb-1">✓ Correction recorded</p>
                <div className="text-xs text-text-muted space-y-0.5">
                  <div>
                    Original ID: <span className="font-mono">{success.originalId}</span>
                  </div>
                  <div>
                    Corrected ID: <span className="font-mono">{success.correctedId}</span>
                  </div>
                </div>
              </div>
              <button
                type="button"
                onClick={onCorrected}
                className="rounded-[var(--radius)] bg-accent-green/15 px-4 py-1.5 text-sm text-accent-green hover:bg-accent-green/25"
              >
                Done
              </button>
            </div>
          )}

          {/* ── Correction form (BUY / SELL / DIVIDEND) ── */}
          {!isTransfer && !success && (
            <form onSubmit={handleSubmit} className="space-y-5">
              {/* Correction reason */}
              <div>
                <label className={`${labelCls} text-accent-red`}>
                  Correction reason * (required for audit trail)
                </label>
                <input
                  type="text"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="e.g. Wrong withholding amount — broker statement differs"
                  className={`${inputCls} border-accent-orange/50`}
                  required
                  aria-required="true"
                />
              </div>

              <div className={sectionHeadCls}>Corrected values — leave unchanged to keep originals</div>

              {/* ── Date (safe for all types) ── */}
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <label className={labelCls}>Trade date</label>
                  <input
                    type="date"
                    value={tradeDate}
                    onChange={(e) => setTradeDate(e.target.value)}
                    className={inputCls}
                    aria-label="Trade date"
                  />
                </div>

                {/* Quantity: BUY/SELL — required when original had one */}
                {!isOptionTxn && m.txn_type !== "DIVIDEND" && m.quantity !== null && (
                  <div>
                    <label className={labelCls}>Quantity</label>
                    <input
                      type="number"
                      step="any"
                      value={quantity}
                      onChange={(e) => setQuantity(e.target.value)}
                      placeholder="Shares"
                      className={inputCls}
                    />
                  </div>
                )}
              </div>

              {isOptionTxn && (
                <div className="rounded-[var(--radius)] border border-accent-purple/20 bg-accent-purple/5 px-3 py-2 text-xs text-text-muted">
                  Quantity is fixed at <strong className="text-text">0</strong> for option movements and cannot be edited.
                </div>
              )}

              {/* Quantity toggle for DIVIDEND (nullable per Amendment I) */}
              {m.txn_type === "DIVIDEND" && (
                <div>
                  <label className="flex items-center gap-2 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={quantityEnabled}
                      onChange={(e) => setQuantityEnabled(e.target.checked)}
                      className="rounded border-border"
                    />
                    <span className={`${labelCls} mb-0`}>Quantity known (optional for dividends)</span>
                  </label>
                  {quantityEnabled && (
                    <input
                      type="number"
                      step="any"
                      min="0"
                      value={quantity}
                      onChange={(e) => setQuantity(e.target.value)}
                      placeholder="Shares / units"
                      className={`${inputCls} mt-2`}
                    />
                  )}
                </div>
              )}

              {/* ── Sale type (SELL only) ── */}
              {m.txn_type === "SELL" && (
                <div>
                  <div className={sectionHeadCls}>Sale type</div>
                  <div className="flex gap-6">
                    {(["ACCIONES", "DERECHOS"] as const).map((t) => (
                      <label key={t} className="flex items-center gap-2 cursor-pointer select-none">
                        <input
                          type="radio"
                          name="corr_sales_type"
                          value={t}
                          checked={salesType === t}
                          onChange={() => setSalesType(t)}
                          className="accent-accent-blue"
                        />
                        <span className="text-sm text-text">{SALES_TYPE_LABELS[t]}</span>
                      </label>
                    ))}
                  </div>
                  {salesType === "DERECHOS" && (
                    <div className="mt-2 rounded-[var(--radius)] border border-accent-blue/20 bg-accent-blue/5 px-3 py-2 text-xs text-text-muted">
                      ℹ Rights sale: proceeds recorded, but share quantity is{" "}
                      <strong className="text-text">NOT</strong> reduced from holdings.
                    </div>
                  )}
                </div>
              )}

              {/* ── Gross (BUY / SELL / DIVIDEND) ── */}
              <div>
                <div className={sectionHeadCls}>Gross amount</div>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                  <div>
                    <label className={labelCls}>Amount</label>
                    <input
                      type="number"
                      step="any"
                      min="0"
                      value={grossAmount}
                      onChange={(e) => setGrossAmount(e.target.value)}
                      className={inputCls}
                    />
                  </div>
                  <div>
                    <label className={labelCls}>Currency</label>
                    <input
                      type="text"
                      value={grossCurrency}
                      onChange={(e) => setGrossCurrency(e.target.value.toUpperCase())}
                      maxLength={3}
                      className={inputCls}
                    />
                  </div>
                  <div>
                    <label className={labelCls}>EUR amount</label>
                    <input
                      type="number"
                      step="any"
                      min="0"
                      value={grossEurAmount}
                      onChange={(e) => setGrossEurAmount(e.target.value)}
                      className={inputCls}
                    />
                  </div>
                  <FxHelper currency={grossCurrency} date={tradeDate} onApply={handleFxApply} />
                </div>
              </div>

              {/* ── Fees (BUY / SELL / DIVIDEND) ── */}
              <div>
                <div className={sectionHeadCls}>Fees</div>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                  <div>
                    <label className={labelCls}>Total</label>
                    <input
                      type="number"
                      step="any"
                      min="0"
                      value={feesTotal}
                      onChange={(e) => setFeesTotal(e.target.value)}
                      className={inputCls}
                    />
                  </div>
                  <div>
                    <label className={labelCls}>Currency</label>
                    <input
                      type="text"
                      value={feesCurrency}
                      onChange={(e) => setFeesCurrency(e.target.value.toUpperCase())}
                      maxLength={3}
                      className={inputCls}
                    />
                  </div>
                  <div>
                    <label className={labelCls}>EUR total</label>
                    <input
                      type="number"
                      step="any"
                      min="0"
                      value={feesEur}
                      onChange={(e) => setFeesEur(e.target.value)}
                      className={inputCls}
                    />
                  </div>
                </div>
              </div>

              {/* ── FX (non-EUR currency) ── */}
              {grossCurrency !== "EUR" && (
                <div>
                  <div className={sectionHeadCls}>FX rate ({grossCurrency}/EUR)</div>
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    <div>
                      <label className={labelCls}>Rate</label>
                      <input
                        type="number"
                        step="any"
                        min="0"
                        value={fxRate}
                        onChange={(e) => setFxRate(e.target.value)}
                        placeholder="0.000000000"
                        className={inputCls}
                      />
                    </div>
                    <div>
                      <label className={labelCls}>Source</label>
                      <select
                        value={fxSource}
                        onChange={(e) => setFxSource(e.target.value as FxRateSource)}
                        className={inputCls}
                      >
                        <option value="ECB">ECB</option>
                        <option value="BROKER">BROKER</option>
                        <option value="MANUAL">MANUAL</option>
                      </select>
                    </div>
                  </div>
                </div>
              )}

              {/* ── Cost basis status (BUY only) ── */}
              {m.txn_type === "BUY" && (
                <div>
                  <label className={labelCls}>Cost basis status</label>
                  <select
                    value={costBasisStatus}
                    onChange={(e) => setCostBasisStatus(e.target.value as CostBasisStatus)}
                    className={inputCls}
                  >
                    <option value="COMPLETE">COMPLETE</option>
                    <option value="INCOMPLETE">INCOMPLETE</option>
                  </select>
                  {costBasisStatus === "INCOMPLETE" && (
                    <p className="mt-1 text-xs text-text-muted">
                      ⚠ Incomplete — e.g. zero-cost corporate-action acquisition.
                    </p>
                  )}
                </div>
              )}

              {supportsOptionMetadata && (
                <div className="rounded-[var(--radius)] border border-border bg-bg-card/30 px-4 py-3 space-y-4">
                  <div className={sectionHeadCls}>Option linkage</div>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <div className="sm:col-span-2">
                      <OptionPositionLinkPicker
                        symbol={m.ticker ?? null}
                        txnType={m.txn_type as OptionTxnType}
                        value={optionPositionId}
                        onChange={setOptionPositionId}
                      />
                    </div>
                    <div>
                      <label className={labelCls}>Link kind</label>
                      <select
                        value={optionLinkKind}
                        onChange={(e) => setOptionLinkKind(e.target.value as OptionLinkKind | "")}
                        className={inputCls}
                        disabled={isOptionTxn}
                      >
                        {isOptionTxn ? (
                          <option value={optionTxnType ? OPTION_LINK_KIND_BY_TXN_TYPE[optionTxnType] : optionLinkKind}>
                            {optionTxnType ? OPTION_LINK_KIND_BY_TXN_TYPE[optionTxnType] : optionLinkKind}
                          </option>
                        ) : (
                          <>
                            <option value="">— Not linked —</option>
                            {OPTION_LINK_KIND_OPTIONS.filter((kind) => kind === "ASSIGNMENT_STOCK").map((kind) => (
                              <option key={kind} value={kind}>
                                {kind}
                              </option>
                            ))}
                          </>
                        )}
                      </select>
                      <p className="mt-1 text-xs text-text-muted">
                        {isOptionTxn
                          ? "Auto-set by the movement type."
                          : "Stock BUY/SELL movements only support ASSIGNMENT_STOCK when linked."}
                      </p>
                    </div>
                    <div>
                      <label className={labelCls}>Option type</label>
                      <select
                        value={optionType}
                        onChange={(e) => setOptionType(e.target.value as OptionContractType | "")}
                        className={inputCls}
                        disabled={isOptionTxn}
                      >
                        {isOptionTxn ? (
                          <option value={optionTxnType ? OPTION_TYPE_BY_TXN_TYPE[optionTxnType] : optionType}>
                            {optionTxnType ? OPTION_TYPE_BY_TXN_TYPE[optionTxnType] : optionType}
                          </option>
                        ) : (
                          <>
                            <option value="">— Unknown —</option>
                            {OPTION_TYPE_OPTIONS.map((kind) => (
                              <option key={kind} value={kind}>
                                {kind}
                              </option>
                            ))}
                          </>
                        )}
                      </select>
                      <p className="mt-1 text-xs text-text-muted">
                        {isOptionTxn ? "Auto-set by the movement type." : "Optional metadata for assignment-linked stock movements."}
                      </p>
                    </div>
                    <div>
                      <label className={labelCls}>Strike</label>
                      <input
                        type="number"
                        step="any"
                        min="0"
                        value={optionStrike}
                        onChange={(e) => setOptionStrike(e.target.value)}
                        placeholder="0.00"
                        className={inputCls}
                      />
                    </div>
                    <div>
                      <label className={labelCls}>Expiration</label>
                      <input
                        type="date"
                        value={optionExpiration}
                        onChange={(e) => setOptionExpiration(e.target.value)}
                        className={inputCls}
                      />
                    </div>
                    <div>
                      <label className={labelCls}>Option symbol</label>
                      <input
                        type="text"
                        value={optionSymbol}
                        onChange={(e) => setOptionSymbol(e.target.value)}
                        placeholder="Broker contract label"
                        className={inputCls}
                      />
                    </div>
                    <div>
                      <label className={labelCls}>Close date</label>
                      <input
                        type="date"
                        value={optionCloseDate}
                        onChange={(e) => setOptionCloseDate(e.target.value)}
                        className={inputCls}
                      />
                    </div>
                  </div>
                </div>
              )}

              {/* ── Withholding toggle for SELL (optional) ── */}
              {m.txn_type === "SELL" && (
                <div>
                  <label className="flex items-center gap-2 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      id="show_sell_wht"
                      checked={showSellWht}
                      onChange={(e) => setShowSellWht(e.target.checked)}
                      className="rounded border-border"
                    />
                    <span className={`${sectionHeadCls} mb-0`}>
                      Withholding (rare — applies in some jurisdictions on equity dispositions)
                    </span>
                  </label>
                </div>
              )}

              {/* ── Withholding fields — SELL (when toggled) or DIVIDEND (always) ── */}
              {(m.txn_type === "DIVIDEND" || (m.txn_type === "SELL" && showSellWht)) && (
                <div className="rounded-[var(--radius)] border border-border bg-bg-card/30 px-4 py-3 space-y-4">
                  {m.txn_type === "DIVIDEND" && (
                    <div className={sectionHeadCls}>Withholding (optional)</div>
                  )}
                  <div>
                    <div className={sectionHeadCls}>Origin withholding (source country)</div>
                    <WithholdingSourceSection
                      country={whtSrcCountry}
                      amount={whtSrcAmount}
                      derivedRate={derivedSrcRate}
                      onChange={(f) => {
                        if (f.country !== undefined) setWhtSrcCountry(f.country);
                        if (f.amount !== undefined) setWhtSrcAmount(f.amount);
                      }}
                    />
                  </div>

                  <div>
                    <div className={sectionHeadCls}>
                      Destination withholding
                    </div>
                    <WithholdingDestSection
                      destState={whtDestState}
                      country={whtDestCountry}
                      amount={whtDestAmount}
                      derivedRate={derivedDestRate}
                      onStateChange={setWhtDestState}
                      onFieldChange={(f) => {
                        if (f.country !== undefined) setWhtDestCountry(f.country);
                        if (f.amount !== undefined) setWhtDestAmount(f.amount);
                      }}
                    />
                  </div>
                </div>
              )}

              {/* ── Net preview (all types) ── */}
              {netPreview !== null && (
                <div className="rounded-[var(--radius)] border border-border bg-bg-input/40 px-4 py-2 text-xs">
                  <span className="font-medium text-text">Estimated net (preview):</span>{" "}
                  <span className="font-mono text-text">
                    €
                    {netPreview.toLocaleString("es-ES", {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 6,
                    })}
                  </span>
                  <span className="ml-2 text-text-muted/70">
                    Server recomputes authoritatively on save.
                  </span>
                </div>
              )}

              {/* ── Notes ── */}
              <div>
                <label className={labelCls}>Correction notes (optional)</label>
                <textarea
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="Additional context for this correction"
                  rows={2}
                  className={`${inputCls} resize-none`}
                />
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

              {/* ── Actions ── */}
              <div className="flex gap-2">
                <button
                  type="submit"
                  disabled={saving}
                  className="rounded-[var(--radius)] bg-accent-orange/15 px-4 py-1.5 text-sm text-accent-orange hover:bg-accent-orange/25 disabled:opacity-50"
                >
                  {saving ? "Saving…" : "Save correction"}
                </button>
                <button
                  type="button"
                  onClick={onClose}
                  disabled={saving}
                  className="rounded-[var(--radius)] border border-border px-4 py-1.5 text-sm text-text-muted hover:bg-bg-hover"
                >
                  Cancel
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
