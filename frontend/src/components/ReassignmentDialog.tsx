"use client";

import { useEffect, useState, useCallback } from "react";
import { X } from "lucide-react";
import { reassignMovement, batchReassignMovements, getBatchReassignmentPreview, listAccounts, listSecurities } from "@/lib/portfolio-api";
import type { BrokerAccount, BatchReassignmentPreviewResponse } from "@/types/portfolio";
import type { SecurityMaster } from "@/types/portfolio";
import { formatAccountName, getAccountName } from "@/lib/accountDisplay";

const inputCls =
  "w-full rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 text-sm text-text placeholder:text-text-muted focus:border-accent-blue focus:outline-none";
const labelCls = "mb-1 block text-xs font-medium text-text-muted";

interface IndividualModeProps {
  movementId: string;
  currentAccountId: string;
  accounts: BrokerAccount[];
  onReassigned: () => void;
  onCancel: () => void;
}

function IndividualMode({ movementId, currentAccountId, accounts, onReassigned, onCancel }: IndividualModeProps) {
  const [newAccountId, setNewAccountId] = useState("_unassigned");
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (newAccountId === currentAccountId) {
      setError("New account must differ from the current account.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await reassignMovement(movementId, {
        source_account_id: currentAccountId,
        dest_account_id: newAccountId,
        reason: reason.trim() || undefined,
      });
      onReassigned();
    } catch (err) {
      const e = err as { data?: { detail?: string } };
      setError(e.data?.detail ?? (err instanceof Error ? err.message : "Reassignment failed"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <label className={labelCls}>Current account</label>
        <div className="text-sm text-text font-medium">{getAccountName(currentAccountId, accounts)}</div>
      </div>
      <div>
        <label className={labelCls}>Assign to *</label>
        <select value={newAccountId} onChange={(e) => setNewAccountId(e.target.value)} className={inputCls} required>
          <option value="_unassigned">Sin asignar</option>
          {accounts.map((a) => (
            <option key={a.account_id} value={a.account_id}>{formatAccountName(a)}</option>
          ))}
        </select>
      </div>
      <div>
        <label className={labelCls}>Reason (optional)</label>
        <input
          type="text"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Optional context"
          className={inputCls}
        />
      </div>
      {error && (
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-3 py-2 text-sm text-accent-red">{error}</div>
      )}
      <div className="flex gap-2">
        <button type="submit" disabled={saving} className="rounded-[var(--radius)] bg-accent-blue/15 px-4 py-1.5 text-sm text-accent-blue hover:bg-accent-blue/25 disabled:opacity-50">
          {saving ? "Saving…" : "Reassign"}
        </button>
        <button type="button" onClick={onCancel} disabled={saving} className="rounded-[var(--radius)] border border-border px-4 py-1.5 text-sm text-text-muted hover:bg-bg-hover">
          Cancel
        </button>
      </div>
    </form>
  );
}

// ─── Batch Reassignment Mode ──────────────────────────────────────────────────

interface BatchModeProps {
  accounts: BrokerAccount[];
  securities: SecurityMaster[];
  lockedSecurityId?: string;
  onReassigned: () => void;
  onCancel: () => void;
}

function BatchMode({ accounts, securities, lockedSecurityId, onReassigned, onCancel }: BatchModeProps) {
  const [securityId, setSecurityId] = useState(lockedSecurityId ?? "");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [sourceAccountId, setSourceAccountId] = useState("_unassigned");
  const [destAccountId, setDestAccountId] = useState("_unassigned");
  const [reason, setReason] = useState("");

  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<BatchReassignmentPreviewResponse | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const [confirmed, setConfirmed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [successCount, setSuccessCount] = useState<number | null>(null);

  // Reset preview whenever filters change
  function resetPreview() {
    setPreview(null);
    setPreviewError(null);
    setConfirmed(false);
  }

  async function handlePreview() {
    if (sourceAccountId === destAccountId) {
      setPreviewError("Source and destination accounts must differ.");
      return;
    }
    setPreviewing(true);
    setPreviewError(null);
    setPreview(null);
    setConfirmed(false);
    try {
      const result = await getBatchReassignmentPreview({
        source_account_id: sourceAccountId,
        dest_account_id: destAccountId,
        security_id: securityId.trim() || undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
      });
      setPreview(result);
    } catch (err) {
      const e = err as { data?: { detail?: string } };
      setPreviewError(e.data?.detail ?? (err instanceof Error ? err.message : "Preview failed"));
    } finally {
      setPreviewing(false);
    }
  }

  async function handleApply() {
    setSaving(true);
    setApplyError(null);
    try {
      const result = await batchReassignMovements({
        source_account_id: sourceAccountId,
        dest_account_id: destAccountId,
        security_id: securityId.trim() || undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
        reason: reason.trim() || undefined,
      });
      setSuccessCount(result.reassigned_count);
    } catch (err) {
      const e = err as { data?: { detail?: string } };
      setApplyError(e.data?.detail ?? (err instanceof Error ? err.message : "Batch reassignment failed"));
    } finally {
      setSaving(false);
    }
  }

  if (successCount !== null) {
    return (
      <div className="space-y-3">
        <div className="rounded-[var(--radius)] border border-accent-green/30 bg-accent-green/5 px-4 py-3 text-sm">
          <p className="font-medium text-accent-green">
            ✓ {successCount} movement{successCount !== 1 ? "s" : ""} reassigned
          </p>
        </div>
        <button
          type="button"
          onClick={onReassigned}
          className="rounded-[var(--radius)] bg-accent-green/15 px-4 py-1.5 text-sm text-accent-green hover:bg-accent-green/25"
        >
          Done
        </button>
      </div>
    );
  }

  const zeroMatch = preview !== null && preview.affected_count === 0;

  return (
    <div className="space-y-4">
      {/* ── Step 1: Filters ── */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label className={labelCls}>Security{lockedSecurityId ? "" : " (optional filter)"}</label>
          {lockedSecurityId ? (
            <div
              className={`${inputCls} bg-bg-hover text-text cursor-default select-none font-mono`}
              aria-label="Security (locked)"
            >
              {(() => {
                const s = securities.find((s) => s.security_id === lockedSecurityId);
                return s ? `${s.ticker} — ${s.company_name}` : lockedSecurityId;
              })()}
            </div>
          ) : (
            <select
              value={securityId}
              onChange={(e) => { setSecurityId(e.target.value); resetPreview(); }}
              className={inputCls}
            >
              <option value="">All securities</option>
              {securities.map((s) => (
                <option key={s.security_id} value={s.security_id}>{s.ticker} — {s.company_name}</option>
              ))}
            </select>
          )}
        </div>
        <div>
          <label className={labelCls}>Date from</label>
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => { setDateFrom(e.target.value); resetPreview(); }}
            className={inputCls}
          />
        </div>
        <div>
          <label className={labelCls}>Date to</label>
          <input
            type="date"
            value={dateTo}
            onChange={(e) => { setDateTo(e.target.value); resetPreview(); }}
            className={inputCls}
          />
        </div>
        <div>
          <label className={labelCls}>Source account (from) *</label>
          <select
            value={sourceAccountId}
            onChange={(e) => { setSourceAccountId(e.target.value); resetPreview(); }}
            className={inputCls}
            required
          >
            <option value="_unassigned">Sin asignar</option>
            {accounts.map((a) => (
              <option key={a.account_id} value={a.account_id}>{formatAccountName(a)}</option>
            ))}
          </select>
        </div>
        <div>
          <label className={labelCls}>Destination account (to) *</label>
          <select
            value={destAccountId}
            onChange={(e) => { setDestAccountId(e.target.value); resetPreview(); }}
            className={inputCls}
            required
          >
            <option value="_unassigned">Sin asignar</option>
            {accounts.map((a) => (
              <option key={a.account_id} value={a.account_id}>{formatAccountName(a)}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Preview error */}
      {previewError && (
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-3 py-2 text-sm text-accent-red">
          {previewError}
        </div>
      )}

      {/* Preview result */}
      {preview !== null && (
        <div className={`rounded-[var(--radius)] border px-4 py-3 text-sm space-y-2 ${
          zeroMatch
            ? "border-border bg-bg-input/50"
            : "border-accent-blue/20 bg-accent-blue/5"
        }`}>
          {zeroMatch ? (
            <p className="text-text-muted">No movements match the selected criteria.</p>
          ) : (
            <>
              <p className="font-medium text-text">
                Preview:{" "}
                <span className="text-accent-blue">
                  {preview.affected_count} movement{preview.affected_count !== 1 ? "s" : ""}
                </span>{" "}
                will be reassigned
              </p>
              {preview.sample.length > 0 && (
                <div className="rounded-[var(--radius)] bg-bg-input/60 overflow-hidden">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-border/40">
                        <th className="px-2 py-1 text-left font-medium text-text-muted">Date</th>
                        <th className="px-2 py-1 text-left font-medium text-text-muted">Security</th>
                        <th className="px-2 py-1 text-left font-medium text-text-muted">Type</th>
                        <th className="px-2 py-1 text-right font-medium text-text-muted">Qty</th>
                      </tr>
                    </thead>
                    <tbody>
                      {preview.sample.map((item) => (
                        <tr key={item.id} className="border-b border-border/20 last:border-0">
                          <td className="px-2 py-1 font-mono text-text-muted">{item.trade_date}</td>
                          <td className="px-2 py-1 font-mono text-text">{item.security_id}</td>
                          <td className="px-2 py-1 text-text-muted">{item.txn_type}</td>
                          <td className="px-2 py-1 text-right font-mono text-text-muted">{item.quantity ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {preview.affected_count > preview.sample.length && (
                    <div className="px-2 py-1 text-xs text-text-muted italic">
                      …and {preview.affected_count - preview.sample.length} more
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* ── Step 2: Confirm + Reason (only shown after a non-zero preview) ── */}
      {preview !== null && !zeroMatch && (
        <>
          <div>
            <label className={labelCls}>Reason (optional)</label>
            <input
              type="text"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Optional context for audit trail"
              className={inputCls}
            />
          </div>

          <label className="flex items-start gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
              className="mt-0.5 rounded border-border"
            />
            <span className="text-xs text-text-muted">
              I confirm that{" "}
              <strong className="text-text">{preview.affected_count} movement{preview.affected_count !== 1 ? "s" : ""}</strong>{" "}
              will be reassigned. The server will re-verify the count at execution time.
            </span>
          </label>

          {applyError && (
            <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-3 py-2 text-sm text-accent-red">
              {applyError}
            </div>
          )}
        </>
      )}

      {/* Actions */}
      <div className="flex flex-wrap gap-2">
        {/* Always show Preview button (re-runnable) */}
        <button
          type="button"
          onClick={handlePreview}
          disabled={previewing || saving}
          className="rounded-[var(--radius)] bg-accent-blue/15 px-4 py-1.5 text-sm text-accent-blue hover:bg-accent-blue/25 disabled:opacity-50"
        >
          {previewing ? "Checking…" : preview !== null ? "Re-preview" : "Preview changes"}
        </button>

        {/* Apply only available after non-zero preview + confirmation */}
        {preview !== null && !zeroMatch && (
          <button
            type="button"
            onClick={handleApply}
            disabled={saving || !confirmed}
            className="rounded-[var(--radius)] bg-accent-orange/15 px-4 py-1.5 text-sm text-accent-orange hover:bg-accent-orange/25 disabled:opacity-50"
          >
            {saving
              ? "Applying…"
              : `Reassign ${preview.affected_count} movement${preview.affected_count !== 1 ? "s" : ""}`}
          </button>
        )}

        <button
          type="button"
          onClick={onCancel}
          disabled={saving}
          className="rounded-[var(--radius)] border border-border px-4 py-1.5 text-sm text-text-muted hover:bg-bg-hover"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

// ─── Main ReassignmentDialog ──────────────────────────────────────────────────

export type ReassignmentMode = "individual" | "batch";

export interface ReassignmentDialogProps {
  mode: ReassignmentMode;
  movementId?: string;
  currentAccountId?: string;
  /** When provided, batch mode is prefilled and locked to this security. */
  lockedSecurityId?: string;
  onClose: () => void;
  onReassigned: () => void;
}

export default function ReassignmentDialog({
  mode: initialMode,
  movementId,
  currentAccountId = "_unassigned",
  lockedSecurityId,
  onClose,
  onReassigned,
}: ReassignmentDialogProps) {
  const [accounts, setAccounts] = useState<BrokerAccount[]>([]);
  const [securities, setSecurities] = useState<SecurityMaster[]>([]);
  const [loadingAccounts, setLoadingAccounts] = useState(true);
  const [activeMode, setActiveMode] = useState<ReassignmentMode>(initialMode);

  const loadAccounts = useCallback(async () => {
    try {
      const [acctResp, secResp] = await Promise.allSettled([listAccounts(), listSecurities()]);
      if (acctResp.status === "fulfilled") setAccounts(acctResp.value.accounts);
      if (secResp.status === "fulfilled") setSecurities(secResp.value.securities);
    } catch {
      // Non-critical — proceed with empty lists
    } finally {
      setLoadingAccounts(false);
    }
  }, []);

  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { loadAccounts(); }, [loadAccounts]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") onClose(); }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const title = activeMode === "individual" ? "Reassign Movement" : "Batch Reassignment";

  return (
    <div
      className="fixed inset-0 z-[210] flex items-start justify-center overflow-auto bg-black/70 p-4"
      onClick={onClose}
    >
      <div
        className="mt-12 mb-12 w-full max-w-[580px] rounded-[var(--radius)] border border-border bg-bg-card"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <div className="flex items-center gap-3">
            <h3 className="text-base font-semibold text-text">{title}</h3>
            {/* Mode toggle — only show when both modes are available */}
            {initialMode !== "individual" || movementId === undefined ? null : (
              <div className="flex rounded-[var(--radius)] border border-border overflow-hidden text-xs">
                <button
                  type="button"
                  onClick={() => setActiveMode("individual")}
                  className={`px-2 py-1 transition-colors ${activeMode === "individual" ? "bg-accent-blue/15 text-accent-blue" : "text-text-muted hover:bg-bg-hover"}`}
                >
                  Single
                </button>
                <button
                  type="button"
                  onClick={() => setActiveMode("batch")}
                  className={`px-2 py-1 transition-colors ${activeMode === "batch" ? "bg-accent-blue/15 text-accent-blue" : "text-text-muted hover:bg-bg-hover"}`}
                >
                  Batch
                </button>
              </div>
            )}
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="text-text-muted hover:text-text">
            <X size={18} />
          </button>
        </div>

        <div className="px-5 py-4">
          {loadingAccounts ? (
            <div className="text-sm text-text-muted">Loading accounts…</div>
          ) : activeMode === "individual" && movementId ? (
            <IndividualMode
              movementId={movementId}
              currentAccountId={currentAccountId}
              accounts={accounts}
              onReassigned={onReassigned}
              onCancel={onClose}
            />
          ) : (
            <BatchMode
              accounts={accounts}
              securities={securities}
              lockedSecurityId={lockedSecurityId}
              onReassigned={onReassigned}
              onCancel={onClose}
            />
          )}
        </div>
      </div>
    </div>
  );
}
