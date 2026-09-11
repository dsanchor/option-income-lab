"use client";

import { useEffect, useState, useCallback } from "react";
import { X, History, Link2, Trash2 } from "lucide-react";
import type { LedgerMovement, OptionTxnType, WarningType } from "@/types/portfolio";
import { OPTION_TXN_TYPES, SALES_TYPE_LABELS } from "@/types/portfolio";
import type { BrokerAccount } from "@/types/portfolio";
import { getAccountName } from "@/lib/accountDisplay";
import { correctMovement, getMovements, voidCorporateActionGroup } from "@/lib/portfolio-api";
import { PaperBadge } from "@/components/OptionLinkageBadges";
import MovementCorrectionDialog from "./MovementCorrectionDialog";
import ReassignmentDialog from "./ReassignmentDialog";
import CorporateActionForm, { buildCaInitialState } from "./CorporateActionForm";
import OptionPositionLinkPicker from "./OptionPositionLinkPicker";

const TXN_BADGE: Record<string, string> = {
  BUY: "bg-accent-green/15 text-accent-green",
  SELL: "bg-accent-red/15 text-accent-red",
  DIVIDEND: "bg-accent-blue/15 text-accent-blue",
  TRANSFER_OUT: "bg-accent-orange/15 text-accent-orange",
  TRANSFER_IN: "bg-accent-orange/15 text-accent-orange",
  CALL_SELL: "bg-accent-purple/15 text-accent-purple",
  CALL_BUY: "bg-accent-cyan/15 text-accent-cyan",
  PUT_SELL: "bg-accent-purple/15 text-accent-purple",
  PUT_BUY: "bg-accent-cyan/15 text-accent-cyan",
};

const WARNING_SHORT: Record<WarningType, string> = {
  NEGATIVE_INVENTORY: "Negative inventory",
  RIGHTS_AMOUNT: "Rights amount pending",
  PROBABLE_DUPLICATE: "Probable duplicate",
  DERECHOS_WITH_QUANTITY: "Rights sale with quantity",
  ACCIONES_ZERO_QUANTITY: "Share sale, zero quantity",
  INVALID_SALES_TYPE: "Invalid sale type",
};

const MOVEMENT_WARNING_LABELS: Record<string, string> = {
  OPTION_MOVEMENT_UNLINKED: "Option movement not linked to a position",
};

function Field({ label, value, mono = false }: { label: string; value: string | null | undefined; mono?: boolean }) {
  return (
    <div>
      <div className="text-xs text-text-muted mb-0.5">{label}</div>
      <div className={`text-sm ${mono ? "font-mono" : ""} ${value == null ? "text-text-muted italic" : "text-text"}`}>
        {value ?? "—"}
      </div>
    </div>
  );
}

function formatEurAmount(amount: string | null | undefined, currency?: string): string {
  if (!amount) return "—";
  const n = Number(amount);
  if (isNaN(n)) return amount;
  const eur = `€${n.toLocaleString("es-ES", { minimumFractionDigits: 2 })}`;
  if (currency && currency !== "EUR") {
    return `${eur} (${currency})`;
  }
  return eur;
}

function hasNonZeroAmount(amount: string | null | undefined): boolean {
  if (!amount) return false;
  const parsed = Number(amount);
  return !Number.isNaN(parsed) && parsed !== 0;
}

export interface MovementDetailDialogProps {
  movement: LedgerMovement;
  accounts?: BrokerAccount[];
  onClose: () => void;
  onRefresh: () => void;
}

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
  CASH_TOP_UP: "Cash Top-Up",
  CONSOLIDATION_OUT: "Consolidation Out",
  CONSOLIDATION_IN: "Consolidation In",
  FRACTIONAL_CASH_OUT: "Fractional Cash",
};
const CA_EVENT_LABEL: Record<string, string> = {
  CASH_DIVIDEND: "Cash Dividend",
  DIVIDEND_WITH_SCRIP: "Dividend with Scrip",
  SCRIP_DIVIDEND: "Scrip Dividend",
  RIGHTS_ISSUE: "Rights Issue",
  SHARE_CONSOLIDATION: "Share Consolidation",
};

export default function MovementDetailDialog({ movement: m, accounts = [], onClose, onRefresh }: MovementDetailDialogProps) {
  const [showCorrect, setShowCorrect] = useState(false);
  const [showReassign, setShowReassign] = useState(false);
  const [showGroupCorrect, setShowGroupCorrect] = useState(false);
  const [quickOptionPositionId, setQuickOptionPositionId] = useState(m.option_position_id ?? "");
  const [quickLinkNote, setQuickLinkNote] = useState("Link option movement to position");
  const [quickLinkSaving, setQuickLinkSaving] = useState(false);
  const [quickLinkError, setQuickLinkError] = useState<string | null>(null);

  // Corporate action group state
  const [groupLegs, setGroupLegs] = useState<LedgerMovement[] | null>(null);
  const [groupLoading, setGroupLoading] = useState(false);
  const [groupError, setGroupError] = useState<string | null>(null);
  const [voidConfirm, setVoidConfirm] = useState(false);
  const [voidReason, setVoidReason] = useState("");
  const [voiding, setVoiding] = useState(false);
  const [voidError, setVoidError] = useState<string | null>(null);

  const handleClose = useCallback(() => onClose(), [onClose]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") handleClose(); }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [handleClose]);

  // Fetch sibling legs when this is a CA group member
  useEffect(() => {
    if (!m.ca_group_id) return;
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      setGroupLoading(true);
      setGroupError(null);
      getMovements({ security_id: m.security_id, ca_group_id: m.ca_group_id, limit: 20 })
        .then((r) => {
          if (!cancelled) setGroupLegs(r.movements);
        })
        .catch(() => {
          if (!cancelled) setGroupError("Could not load group legs.");
        })
        .finally(() => {
          if (!cancelled) setGroupLoading(false);
        });
    });
    return () => {
      cancelled = true;
    };
  }, [m.ca_group_id, m.security_id]);

  async function handleVoidGroup() {
    if (!m.ca_group_id) return;
    setVoiding(true);
    setVoidError(null);
    try {
      await voidCorporateActionGroup(m.ca_group_id, {
        account_id: m.account_id,
        reason: voidReason.trim() || "User-initiated void",
      });
      setVoidConfirm(false);
      onRefresh();
      onClose();
    } catch (err) {
      const e = err as { data?: { detail?: string } };
      setVoidError(e.data?.detail ?? (err instanceof Error ? err.message : "Void failed."));
    } finally {
      setVoiding(false);
    }
  }

  const importSourceLabel = m.import_source === "csv_import" ? "CSV Import" : "Manual";
  const movementWarnings = m.movement_warnings ?? [];
  const isOptionTxn = OPTION_TXN_TYPES.includes(m.txn_type as OptionTxnType);
  const hasOptionMetadata = isOptionTxn || Boolean(
    m.option_position_id ||
    m.option_link_kind ||
    m.option_type ||
    m.option_strike != null ||
    m.option_expiration ||
    m.option_symbol ||
    m.option_close_date
  );
  const hasUnlinkedOptionWarning = movementWarnings.includes("OPTION_MOVEMENT_UNLINKED");

  async function handleQuickLinkSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!quickOptionPositionId.trim()) {
      setQuickLinkError("Position ID is required to link this movement.");
      return;
    }
    if (!quickLinkNote.trim()) {
      setQuickLinkError("Correction note is required.");
      return;
    }

    setQuickLinkSaving(true);
    setQuickLinkError(null);
    try {
      await correctMovement(m.id, {
        account_id: m.account_id,
        correction_note: quickLinkNote.trim(),
        option_position_id: quickOptionPositionId.trim(),
      });
      onRefresh();
    } catch (err) {
      const e = err as { data?: { detail?: string } };
      setQuickLinkError(e.data?.detail ?? (err instanceof Error ? err.message : "Could not link movement."));
    } finally {
      setQuickLinkSaving(false);
    }
  }

  if (showGroupCorrect && m.ca_group_id) {
    return (
      <div
        className="fixed inset-0 z-[200] flex items-start justify-center overflow-auto bg-black/60 p-4"
        onClick={() => setShowGroupCorrect(false)}
      >
        <div
          className="mt-12 mb-12 w-full max-w-[700px] rounded-[var(--radius)] border border-border bg-bg-card"
          onClick={(e) => e.stopPropagation()}
          role="dialog"
          aria-modal="true"
          aria-label="Replace corporate action group"
        >
          <div className="flex items-center justify-between border-b border-border px-5 py-3">
            <div>
              <h3 className="text-base font-semibold text-text">Replace corporate action group</h3>
              <p className="text-xs text-text-muted mt-0.5">
                All legs are replaced atomically. Original group preserved as SUPERSEDED audit trail.
              </p>
            </div>
            <button
              type="button"
              onClick={() => setShowGroupCorrect(false)}
              aria-label="Close"
              className="rounded-[var(--radius)] p-1 text-text-muted hover:bg-bg-hover hover:text-text transition-colors"
            >
              <X size={18} />
            </button>
          </div>
          <div className="px-5 py-4 overflow-y-auto max-h-[80vh]">
            <CorporateActionForm
              mode="correct"
              caGroupId={m.ca_group_id}
              initialState={buildCaInitialState(groupLegs ?? [m], m)}
              accounts={[]}
              securities={[]}
              onSuccess={() => { setShowGroupCorrect(false); onRefresh(); onClose(); }}
            />
          </div>
        </div>
      </div>
    );
  }

  if (showCorrect) {
    return (
      <MovementCorrectionDialog
        movement={m}
        accounts={accounts}
        onClose={() => setShowCorrect(false)}
        onCorrected={() => { setShowCorrect(false); onRefresh(); onClose(); }}
      />
    );
  }

  if (showReassign) {
    return (
      <ReassignmentDialog
        mode="individual"
        movementId={m.id}
        currentAccountId={m.account_id}
        onClose={() => setShowReassign(false)}
        onReassigned={() => { setShowReassign(false); onRefresh(); onClose(); }}
      />
    );
  }

  return (
    <div
      className="fixed inset-0 z-[200] flex items-start justify-center overflow-auto bg-black/60 p-4"
      onClick={handleClose}
    >
      <div
        className="mt-12 mb-12 w-full max-w-[620px] rounded-[var(--radius)] border border-border bg-bg-card"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Movement detail"
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <div className="flex items-center gap-2 flex-wrap">
            <span
              className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                TXN_BADGE[m.txn_type] ?? "bg-bg-hover text-text-muted"
              }`}
            >
              {m.txn_type}
            </span>
            {m.correction_status && m.correction_status !== "ACTIVE" && (
              <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                m.correction_status === "SUPERSEDED"
                  ? "bg-accent-orange/15 text-accent-orange"
                  : "bg-accent-red/15 text-accent-red"
              }`}>
                {m.correction_status}
              </span>
            )}
            {m.is_paper && <PaperBadge />}
            <h3 className="text-base font-semibold text-text">
              {m.ticker} — {m.company_name}
            </h3>
          </div>
          <button
            type="button"
            onClick={handleClose}
            aria-label="Close"
            className="rounded-[var(--radius)] p-1 text-text-muted hover:bg-bg-hover hover:text-text transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-4 space-y-5">
          {/* Core fields */}
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <Field label="Date" value={m.trade_date} mono />
            <Field label="Symbol ID" value={m.security_id} mono />
            <Field label="Account" value={getAccountName(m.account_id, accounts)} />
            {m.quantity != null && Number(m.quantity) !== 0 && (
              <Field
                label="Quantity"
                value={Number(m.quantity).toLocaleString("es-ES", { maximumFractionDigits: 6 })}
                mono
              />
            )}
            {m.txn_type === "SELL" && (
              <Field
                label="Sale type"
                value={m.sales_type != null ? (SALES_TYPE_LABELS[m.sales_type] ?? m.sales_type) : null}
              />
            )}
          </div>

          {/* Amounts */}
          <div>
            <div className="text-xs font-semibold uppercase tracking-wide text-text-muted mb-2">Amounts</div>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 rounded-[var(--radius)] border border-border bg-bg-card/50 p-3">
              <Field label="Gross" value={formatEurAmount(m.gross?.eur_amount, m.gross?.currency)} mono />
              <Field label="Fees" value={formatEurAmount(m.fees?.total_eur, m.fees?.currency)} mono />
              <Field label="Net" value={formatEurAmount(m.net?.eur_amount, m.net?.currency)} mono />
              {m.txn_type === "DIVIDEND" && hasNonZeroAmount(m.source_derechos_amount) && (
                <Field label="Derechos" value={formatEurAmount(m.source_derechos_amount)} mono />
              )}
              {m.withholding?.source && (
                <Field
                  label={`WHT Source (${m.withholding.source.country ?? ""})`}
                  value={formatEurAmount(m.withholding.source.amount_eur)}
                  mono
                />
              )}
              {m.withholding?.destination && (
                <Field
                  label={`WHT Dest`}
                  value={formatEurAmount(m.withholding.destination.amount_eur)}
                  mono
                />
              )}
            </div>
          </div>

          {/* FX */}
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <Field label="FX Rate" value={m.fx?.rate ?? null} mono />
            <Field label="FX Source" value={m.fx?.rate_source ?? null} />
            <Field label="Import source" value={importSourceLabel} />
          </div>

          {/* Cost basis status */}
          {m.cost_basis_status === "INCOMPLETE" && (
            <div className="rounded-[var(--radius)] border border-accent-orange/30 bg-accent-orange/5 px-4 py-2 text-sm text-text-muted">
              <span className="text-accent-orange mr-1">⚠</span>
              Cost basis incomplete — likely a zero-cost corporate-action acquisition.
            </div>
          )}

          {hasUnlinkedOptionWarning && (
            <div className="rounded-[var(--radius)] border border-accent-orange/30 bg-accent-orange/5 px-4 py-3 text-sm text-text-muted space-y-3">
              <div>
                <span className="text-accent-orange mr-1">⚠</span>
                <span className="font-medium text-text">This option movement is not linked to a position yet.</span>
              </div>
              <p className="text-xs">
                Add the matching <span className="font-mono">option_position_id</span> now, or use the full correction flow for additional metadata.
              </p>
              <form onSubmit={handleQuickLinkSubmit} className="space-y-2">
                <OptionPositionLinkPicker
                  symbol={m.ticker ?? null}
                  txnType={m.txn_type as OptionTxnType}
                  value={quickOptionPositionId}
                  onChange={setQuickOptionPositionId}
                />
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
                  <input
                    type="text"
                    value={quickLinkNote}
                    onChange={(e) => setQuickLinkNote(e.target.value)}
                    placeholder="Correction note"
                    className="w-full rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 text-sm text-text placeholder:text-text-muted focus:border-accent-orange focus:outline-none"
                  />
                  <button
                    type="submit"
                    disabled={quickLinkSaving}
                    className="rounded-[var(--radius)] bg-accent-orange/15 px-3 py-2 text-sm text-accent-orange hover:bg-accent-orange/25 disabled:opacity-50"
                  >
                    {quickLinkSaving ? "Linking…" : "Link"}
                  </button>
                </div>
              </form>
              {quickLinkError && <div className="text-xs text-accent-red">{quickLinkError}</div>}
            </div>
          )}

          {/* Derechos note */}
          {m.txn_type === "SELL" && m.sales_type === "DERECHOS" && (
            <div className="rounded-[var(--radius)] border border-accent-blue/20 bg-accent-blue/5 px-4 py-2 text-xs text-text-muted">
              ℹ Rights sale: proceeds are recorded but <strong className="text-text">share quantity is not reduced</strong> — rights entitlements are separate from ordinary share ownership.
            </div>
          )}

          {hasOptionMetadata && (
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-text-muted mb-2">Option linkage</div>
              <div className="grid grid-cols-2 gap-4 rounded-[var(--radius)] border border-border bg-bg-card/50 p-3 sm:grid-cols-3">
                <Field label="Position ID" value={m.option_position_id ?? null} mono />
                <Field label="Link kind" value={m.option_link_kind ?? null} />
                <Field label="Option type" value={m.option_type ?? null} />
                <Field
                  label="Strike"
                  value={m.option_strike != null ? String(m.option_strike) : null}
                  mono
                />
                <Field label="Expiration" value={m.option_expiration ?? null} mono />
                <Field label="Close date" value={m.option_close_date ?? null} mono />
                <Field label="Option symbol" value={m.option_symbol ?? null} mono />
              </div>
            </div>
          )}

          {/* Warnings */}
          {(m.warnings && m.warnings.length > 0) || movementWarnings.length > 0 ? (
            <div className="rounded-[var(--radius)] border border-accent-orange/30 bg-accent-orange/5 px-4 py-2 space-y-1">
              {movementWarnings.map((warning, i) => (
                <div key={`${warning}-${i}`} className="flex items-start gap-2 text-xs text-text-muted">
                  <span className="text-accent-orange mt-0.5 shrink-0">⚠</span>
                  <span>
                    <span className="font-medium text-text mr-1">
                      {MOVEMENT_WARNING_LABELS[warning] ?? warning}:
                    </span>
                    {warning === "OPTION_MOVEMENT_UNLINKED"
                      ? "Set option_position_id to link this movement with its option position."
                      : warning}
                  </span>
                </div>
              ))}
              {m.warnings?.map((w, i) => (
                <div key={i} className="flex items-start gap-2 text-xs text-text-muted">
                  <span className="text-accent-orange mt-0.5 shrink-0">⚠</span>
                  <span>
                    <span className="font-medium text-text mr-1">
                      {WARNING_SHORT[w.type as WarningType] ?? w.type}:
                    </span>
                    {w.message}
                  </span>
                </div>
              ))}
            </div>
          ) : null}

          {/* Transfer details */}
          {(m.txn_type === "TRANSFER_OUT" || m.txn_type === "TRANSFER_IN") && (
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-text-muted mb-2">Transfer</div>
              <div className="grid grid-cols-2 gap-4 rounded-[var(--radius)] border border-border bg-bg-card/50 p-3">
                <Field label="Direction" value={m.txn_type === "TRANSFER_OUT" ? "Out → destination" : "In ← source"} />
                {m.transfer_source_account_id && (
                  <Field label="From account" value={getAccountName(m.transfer_source_account_id!, accounts)} />
                )}
                {m.transfer_dest_account_id && (
                  <Field label="To account" value={getAccountName(m.transfer_dest_account_id!, accounts)} />
                )}
                {m.transfer_cost_basis_eur && (
                  <Field
                    label={`Cost basis EUR${m.transfer_cost_basis_overridden ? " (overridden)" : " (derived)"}`}
                    value={formatEurAmount(m.transfer_cost_basis_eur)}
                    mono
                  />
                )}
                {m.transfer_fee && m.transfer_fee.total_eur && (
                  <Field label="Transfer fee" value={formatEurAmount(m.transfer_fee.total_eur, m.transfer_fee.currency)} mono />
                )}
                {m.transfer_group_id && (
                  <Field label="Group ID" value={m.transfer_group_id} mono />
                )}
              </div>
            </div>
          )}

          {/* Corporate action group panel */}
          {m.ca_group_id && (
            <div>
              <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-text-muted mb-2">
                <Link2 size={12} className="text-accent-blue" />
                Corporate action group
              </div>
              <div className="rounded-[var(--radius)] border border-accent-blue/20 bg-accent-blue/5 px-4 py-3 space-y-3">
                {/* Group meta */}
                <div className="grid grid-cols-2 gap-3 text-xs">
                  {m.ca_event_type && (
                    <div>
                      <div className="text-text-muted mb-0.5">Event type</div>
                      <div className="font-medium text-text">{CA_EVENT_LABEL[m.ca_event_type] ?? m.ca_event_type}</div>
                    </div>
                  )}
                  {m.ca_leg_type && (
                    <div>
                      <div className="text-text-muted mb-0.5">This leg</div>
                      <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${CA_LEG_BADGE[m.ca_leg_type] ?? "bg-bg-hover text-text-muted"}`}>
                        {CA_LEG_LABEL[m.ca_leg_type] ?? m.ca_leg_type}
                        {m.ca_group_seq != null ? ` #${m.ca_group_seq}` : ""}
                      </span>
                    </div>
                  )}
                  <div className="col-span-2">
                    <div className="text-text-muted mb-0.5">Group ID</div>
                    <div className="font-mono text-text-muted text-xs">{m.ca_group_id}</div>
                  </div>
                </div>

                {/* Sibling legs */}
                {groupLoading && (
                  <div className="text-xs text-text-muted">Loading group legs…</div>
                )}
                {groupError && (
                  <div className="text-xs text-accent-red">{groupError}</div>
                )}
                {groupLegs && groupLegs.length > 0 && (
                  <div>
                    <div className="text-xs text-text-muted mb-1.5 font-medium">All legs in this group</div>
                    <div className="space-y-1">
                      {groupLegs.map((leg) => (
                        <div
                          key={leg.id}
                          className={`flex items-center gap-2 rounded-[var(--radius)] border px-3 py-1.5 text-xs ${
                            leg.id === m.id
                              ? "border-accent-blue/30 bg-accent-blue/5"
                              : "border-border bg-bg-card/50"
                          }`}
                        >
                          {leg.ca_leg_type && (
                            <span className={`rounded-full px-1.5 py-0.5 font-medium ${CA_LEG_BADGE[leg.ca_leg_type] ?? "bg-bg-hover text-text-muted"}`}>
                              {CA_LEG_LABEL[leg.ca_leg_type] ?? leg.ca_leg_type}
                            </span>
                          )}
                          <span className="font-mono text-text-muted">{leg.trade_date}</span>
                          {leg.quantity != null && (
                            <span className="text-text">× {Number(leg.quantity).toLocaleString("en-US", { maximumFractionDigits: 4 })}</span>
                          )}
                          <span className="ml-auto font-mono text-text">
                            €{Number(leg.gross?.eur_amount || "0").toLocaleString("es-ES", { minimumFractionDigits: 2 })}
                          </span>
                          {leg.correction_status && leg.correction_status !== "ACTIVE" && (
                            <span className="text-accent-orange">{leg.correction_status}</span>
                          )}
                          {leg.id === m.id && (
                            <span className="text-accent-blue font-medium">← this</span>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Group action buttons */}
                <div className="flex flex-wrap gap-2">
                  {/* Replace entire group */}
                  <button
                    type="button"
                    onClick={() => setShowGroupCorrect(true)}
                    className="inline-flex items-center gap-1.5 rounded-[var(--radius)] border border-accent-blue/30 px-3 py-1 text-xs text-accent-blue hover:bg-accent-blue/5 transition-colors"
                  >
                    <History size={11} />
                    Replace entire group
                  </button>

                  {/* Void group confirmation */}
                  {!voidConfirm ? (
                    <button
                      type="button"
                      onClick={() => setVoidConfirm(true)}
                      className="inline-flex items-center gap-1.5 rounded-[var(--radius)] border border-accent-red/30 px-3 py-1 text-xs text-accent-red hover:bg-accent-red/5 transition-colors"
                    >
                      <Trash2 size={11} />
                      Void entire group
                    </button>
                  ) : (
                    <div className="w-full space-y-2 rounded-[var(--radius)] border border-accent-red/30 bg-accent-red/5 p-3">
                      <p className="text-xs font-medium text-accent-red">
                        Void all {groupLegs?.length ?? "?"} legs in this group? This cannot be undone.
                      </p>
                      <input
                        type="text"
                        value={voidReason}
                        onChange={(e) => setVoidReason(e.target.value)}
                        placeholder="Reason (optional)"
                        className="w-full rounded-[var(--radius)] border border-border bg-bg-input px-2 py-1 text-xs text-text placeholder:text-text-muted focus:border-accent-red focus:outline-none"
                      />
                      {voidError && <p className="text-xs text-accent-red">{voidError}</p>}
                      <div className="flex gap-2">
                        <button
                          type="button"
                          onClick={handleVoidGroup}
                          disabled={voiding}
                          className="rounded-[var(--radius)] bg-accent-red/15 px-3 py-1 text-xs text-accent-red hover:bg-accent-red/25 disabled:opacity-50"
                        >
                          {voiding ? "Voiding…" : "Confirm void"}
                        </button>
                        <button
                          type="button"
                          onClick={() => { setVoidConfirm(false); setVoidError(null); }}
                          className="rounded-[var(--radius)] border border-border px-3 py-1 text-xs text-text-muted hover:bg-bg-hover"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Correction audit chain */}
          {(m.corrects_movement_id || m.superseded_by || m.correction_note) && (
            <div className="rounded-[var(--radius)] border border-accent-orange/20 bg-accent-orange/5 px-4 py-3 text-xs space-y-1">
              <div className="font-medium text-text-muted uppercase tracking-wide mb-1">Correction audit</div>
              {m.correction_note && (
                <div><span className="text-text-muted">Note: </span><span className="text-text">{m.correction_note}</span></div>
              )}
              {m.corrects_movement_id && (
                <div><span className="text-text-muted">Corrects: </span><span className="font-mono text-text-muted">{m.corrects_movement_id}</span></div>
              )}
              {m.superseded_by && (
                <div><span className="text-text-muted">Superseded by: </span><span className="font-mono text-text-muted">{m.superseded_by}</span></div>
              )}
            </div>
          )}

          {/* Movement ID */}
          <div className="text-xs text-text-muted font-mono border-t border-border/40 pt-2">
            ID: {m.id}
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center justify-between gap-2 border-t border-border px-5 py-3">
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => setShowCorrect(true)}
              disabled={m.txn_type === "TRANSFER_OUT" || m.txn_type === "TRANSFER_IN"}
              title={
                m.txn_type === "TRANSFER_OUT" || m.txn_type === "TRANSFER_IN"
                  ? "Transfers cannot be corrected individually. Void the transfer pair and create a new one."
                  : m.ca_group_id
                  ? "Correct this leg only — group fields (ca_group_id, leg type) are preserved"
                  : undefined
              }
              className="inline-flex items-center gap-1.5 rounded-[var(--radius)] border border-border px-3 py-1.5 text-xs text-text-muted hover:bg-bg-hover hover:text-text transition-colors disabled:cursor-not-allowed disabled:opacity-40"
            >
              <History size={13} />
              {m.ca_group_id ? "Correct this leg" : "Correct movement"}
            </button>
            <button
              type="button"
              onClick={() => setShowReassign(true)}
              className="inline-flex items-center gap-1.5 rounded-[var(--radius)] border border-border px-3 py-1.5 text-xs text-text-muted hover:bg-bg-hover hover:text-text transition-colors"
            >
              Reassign account
            </button>
          </div>
          <button
            type="button"
            onClick={handleClose}
            className="rounded-[var(--radius)] border border-border px-3 py-1.5 text-xs text-text-muted hover:bg-bg-hover"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
