"use client";

import { Fragment, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import PositionDetail from "@/components/PositionDetail";
import type { Position } from "@/types/symbol-detail";

const STATUS_FILTERS = [
  { label: "All", value: "" },
  { label: "Active", value: "active" },
  { label: "Rolled", value: "rolled" },
  { label: "Closed", value: "closed" },
];

const DATE_FILTERS = [
  { label: "All", days: 0 },
  { label: "15d", days: 15 },
  { label: "1m", days: 30 },
  { label: "1y", days: 365 },
];

const CLOSE_REASONS = [
  { label: "Manual close", value: "manual" },
  { label: "Expired", value: "expired" },
  { label: "Assigned", value: "assigned" },
];

function num(v: number | null | undefined, digits = 2): string {
  return typeof v === "number" && isFinite(v) ? v.toFixed(digits) : "—";
}

function statusClass(status: string | undefined): string {
  if (status === "active") return "border-accent-green/40 bg-accent-green/10 text-accent-green";
  if (status === "rolled") return "border-accent-orange/40 bg-accent-orange/10 text-accent-orange";
  return "border-border bg-bg-input text-text-muted";
}

function riskClass(risk: string): string {
  const r = risk.toLowerCase();
  if (r.includes("high")) return "border-accent-red/40 bg-accent-red/10 text-accent-red";
  if (r.includes("medium") || r.includes("moderate")) return "border-accent-orange/40 bg-accent-orange/10 text-accent-orange";
  return "border-accent-green/40 bg-accent-green/10 text-accent-green";
}

function moneynessClass(m: string): string {
  const s = m.toLowerCase();
  if (s.includes("itm") || s.includes("in the money")) return "border-accent-red/40 bg-accent-red/10 text-accent-red";
  if (s.includes("atm") || s.includes("at the money")) return "border-accent-orange/40 bg-accent-orange/10 text-accent-orange";
  return "border-accent-green/40 bg-accent-green/10 text-accent-green";
}

function Badge({ text, className }: { text: string; className: string }) {
  return (
    <span className={`inline-block rounded-[var(--radius-pill)] border px-2 py-0.5 text-xs ${className}`}>
      {text}
    </span>
  );
}

const inputCls =
  "rounded-[var(--radius)] border border-border bg-bg-input px-2 py-1 text-sm text-text outline-none focus:border-accent-blue";
const labelCls = "mb-1 block text-xs text-text-muted";

export default function PositionsTable({ symbol, positions }: { symbol: string; positions: Position[] }) {
  const router = useRouter();
  const [status, setStatus] = useState("");
  const [days, setDays] = useState(0);

  // Which position currently has an open Roll / Close / Notes editor.
  const [rollFor, setRollFor] = useState<string | null>(null);
  const [closeFor, setCloseFor] = useState<string | null>(null);
  const [notesFor, setNotesFor] = useState<string | null>(null);
  const [notesDraft, setNotesDraft] = useState("");
  const [detailsFor, setDetailsFor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Newest first (mirrors the legacy `| reverse`).
  const ordered = useMemo(() => [...positions].reverse(), [positions]);

  const filtered = useMemo(() => {
    const now = Date.now();
    return ordered.filter((p) => {
      if (status && p.status !== status) return false;
      if (days > 0 && p.opened_at) {
        const ts = new Date(String(p.opened_at).slice(0, 10)).getTime();
        if (isFinite(ts) && (now - ts) / 86400000 > days) return false;
      }
      return true;
    });
  }, [ordered, status, days]);

  function resetEditors() {
    setRollFor(null);
    setCloseFor(null);
    setNotesFor(null);
    setError(null);
  }

  async function send(url: string, method: string, body: Record<string, unknown>): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError((data as { error?: string }).error || `Request failed (${res.status})`);
        return false;
      }
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Network error");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function submitRoll(posId: string, form: HTMLFormElement) {
    const fd = new FormData(form);
    const newStrike = fd.get("new_strike");
    const newExpiration = String(fd.get("new_expiration") || "");
    if (!newStrike || !newExpiration) {
      setError("New strike and new expiration are required.");
      return;
    }
    const body: Record<string, unknown> = {
      new_strike: Number(newStrike),
      new_expiration: newExpiration,
    };
    const buyback = fd.get("buyback_cost");
    const premium = fd.get("premium");
    const notes = String(fd.get("notes") || "").trim();
    if (buyback && String(buyback).trim() !== "") body.buyback_cost = Number(buyback);
    if (premium && String(premium).trim() !== "") body.premium = Number(premium);
    if (notes) body.notes = notes;

    const ok = await send(
      `/api/symbols/${encodeURIComponent(symbol)}/positions/${encodeURIComponent(posId)}/roll`,
      "POST",
      body,
    );
    if (ok) {
      resetEditors();
      router.refresh();
    }
  }

  async function submitClose(posId: string, form: HTMLFormElement) {
    const fd = new FormData(form);
    const reason = String(fd.get("close_reason") || "manual");
    const body: Record<string, unknown> = { close_reason: reason };
    const buyback = fd.get("buyback_cost");
    if (reason === "manual" && buyback && String(buyback).trim() !== "") {
      body.buyback_cost = Number(buyback);
    }
    const ok = await send(
      `/api/symbols/${encodeURIComponent(symbol)}/positions/${encodeURIComponent(posId)}/close`,
      "PUT",
      body,
    );
    if (ok) {
      resetEditors();
      router.refresh();
    }
  }

  async function saveNotes(posId: string) {
    const ok = await send(
      `/api/symbols/${encodeURIComponent(symbol)}/positions/${encodeURIComponent(posId)}/notes`,
      "PATCH",
      { notes: notesDraft },
    );
    if (ok) {
      setNotesFor(null);
      router.refresh();
    }
  }

  const COLS = 10;

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">
          Positions <span className="text-sm text-text-muted">({filtered.length} of {positions.length})</span>
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1 rounded-[var(--radius-pill)] border border-border bg-bg-card p-1">
            {STATUS_FILTERS.map((s) => (
              <button
                key={s.label}
                type="button"
                onClick={() => setStatus(s.value)}
                className={`rounded-[var(--radius-pill)] px-3 py-1 text-xs transition ${
                  status === s.value ? "bg-accent-blue text-white" : "text-text-muted hover:text-text"
                }`}
              >
                {s.label}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-1 rounded-[var(--radius-pill)] border border-border bg-bg-card p-1">
            {DATE_FILTERS.map((dt) => (
              <button
                key={dt.label}
                type="button"
                onClick={() => setDays(dt.days)}
                className={`rounded-[var(--radius-pill)] px-3 py-1 text-xs transition ${
                  days === dt.days ? "bg-accent-blue text-white" : "text-text-muted hover:text-text"
                }`}
              >
                {dt.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {error && (
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-3 py-2 text-sm text-accent-red">
          {error}
        </div>
      )}

      <div className="overflow-x-auto rounded-[var(--radius)] border border-border bg-bg-card">
        <table className="w-full min-w-[960px] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
              <th className="px-4 py-3 font-medium">Type</th>
              <th className="px-4 py-3 text-right font-medium">Strike</th>
              <th className="px-4 py-3 font-medium">Expiration</th>
              <th className="px-4 py-3 font-medium">Risk</th>
              <th className="px-4 py-3 font-medium">Moneyness</th>
              <th className="px-4 py-3 font-medium">Status</th>
              <th className="px-4 py-3 font-medium">Opened</th>
              <th className="px-4 py-3 text-right font-medium">Premium</th>
              <th className="px-4 py-3 font-medium">Notes</th>
              <th className="px-4 py-3 text-right font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr>
                <td colSpan={COLS} className="px-4 py-6 text-center text-text-muted">
                  No positions match the current filters.
                </td>
              </tr>
            )}
            {filtered.map((p, i) => {
              const posId = p.position_id ?? "";
              const isActive = p.status === "active";
              const editingNotes = notesFor === posId;
              return (
                <Fragment key={posId || i}>
                  <tr className={`border-b border-border/60 last:border-0 ${p.status !== "active" ? "text-text-muted" : ""}`}>
                    <td className="px-4 py-3">
                      <Badge
                        text={(p.type ?? "—").toUpperCase()}
                        className={p.type === "call" ? "border-accent-green/40 bg-accent-green/10 text-accent-green" : "border-accent-blue/40 bg-accent-blue/10 text-accent-blue"}
                      />
                    </td>
                    <td className="px-4 py-3 text-right font-mono">{p.strike != null ? `$${num(p.strike)}` : "—"}</td>
                    <td className="px-4 py-3 font-mono">{p.expiration ?? "—"}</td>
                    <td className="px-4 py-3">
                      {p.assignment_risk ? <Badge text={p.assignment_risk} className={riskClass(p.assignment_risk)} /> : "—"}
                    </td>
                    <td className="px-4 py-3">
                      {p.moneyness ? <Badge text={p.moneyness} className={moneynessClass(p.moneyness)} /> : "—"}
                    </td>
                    <td className="px-4 py-3">
                      <span className="flex flex-wrap items-center gap-1">
                        <Badge text={p.status ?? "—"} className={statusClass(p.status)} />
                        {p.status === "closed" && p.close_reason && (
                          <Badge
                            text={p.close_reason}
                            className={p.close_reason === "assigned" ? "border-accent-red/40 bg-accent-red/10 text-accent-red" : "border-border bg-bg-input text-text-muted"}
                          />
                        )}
                      </span>
                    </td>
                    <td className="px-4 py-3 font-mono">{p.opened_at ? String(p.opened_at).slice(0, 10) : "—"}</td>
                    <td className="px-4 py-3 text-right font-mono">
                      {p.display_premium != null ? `$${num(p.display_premium)}` : "—"}
                    </td>
                    <td className="px-4 py-3 max-w-[200px]">
                      {editingNotes ? (
                        <div className="flex items-start gap-1">
                          <textarea
                            className={`${inputCls} min-w-[160px] resize-y`}
                            rows={2}
                            value={notesDraft}
                            onChange={(e) => setNotesDraft(e.target.value)}
                            autoFocus
                          />
                          <div className="flex flex-col gap-1">
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => saveNotes(posId)}
                              className="rounded-[var(--radius-pill)] bg-accent-blue px-2 py-0.5 text-xs text-white disabled:opacity-50"
                            >
                              Save
                            </button>
                            <button
                              type="button"
                              onClick={() => setNotesFor(null)}
                              className="rounded-[var(--radius-pill)] border border-border px-2 py-0.5 text-xs text-text-muted hover:text-text"
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      ) : (
                        <button
                          type="button"
                          title="Click to edit"
                          disabled={!posId}
                          onClick={() => {
                            resetEditors();
                            setNotesDraft(p.notes ?? "");
                            setNotesFor(posId);
                          }}
                          className="block w-full truncate text-left hover:text-accent-blue"
                        >
                          {p.notes || "—"}
                        </button>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex justify-end gap-1">
                        {isActive && posId && (
                          <>
                            <button
                              type="button"
                              onClick={() => {
                                const next = rollFor === posId ? null : posId;
                                resetEditors();
                                setRollFor(next);
                              }}
                              className="rounded-[var(--radius-pill)] border border-border px-2 py-0.5 text-xs text-text-muted hover:text-text"
                            >
                              Roll
                            </button>
                            <button
                              type="button"
                              onClick={() => {
                                const next = closeFor === posId ? null : posId;
                                resetEditors();
                                setCloseFor(next);
                              }}
                              className="rounded-[var(--radius-pill)] border border-border px-2 py-0.5 text-xs text-text-muted hover:text-text"
                            >
                              Close
                            </button>
                          </>
                        )}
                        {posId ? (
                          <button
                            type="button"
                            onClick={() => setDetailsFor((cur) => (cur === posId ? null : posId))}
                            className="rounded-[var(--radius-pill)] border border-border px-2 py-0.5 text-xs text-text-muted hover:text-text"
                            aria-expanded={detailsFor === posId}
                          >
                            {detailsFor === posId ? "▲ Details" : "▼ Details"}
                          </button>
                        ) : (
                          <span className="text-text-muted">—</span>
                        )}
                      </div>
                    </td>
                  </tr>

                  {detailsFor === posId && posId && (
                    <tr className="border-b border-border/60 bg-bg-input/30">
                      <td colSpan={COLS} className="px-4 py-4">
                        <PositionDetail symbol={symbol} position={p} />
                      </td>
                    </tr>
                  )}

                  {rollFor === posId && (
                    <tr className="border-b border-border/60 bg-bg-input/40">
                      <td colSpan={COLS} className="px-4 py-4">
                        <form
                          onSubmit={(e) => {
                            e.preventDefault();
                            submitRoll(posId, e.currentTarget);
                          }}
                        >
                          <h4 className="mb-3 text-sm font-semibold">🔄 Roll {(p.type ?? "").toUpperCase()} position</h4>
                          <div className="flex flex-wrap items-end gap-3">
                            <div>
                              <label className={labelCls}>New strike</label>
                              <input name="new_strike" type="number" step="0.5" defaultValue={p.strike ?? undefined} className={`${inputCls} w-28`} required />
                            </div>
                            <div>
                              <label className={labelCls}>New expiration</label>
                              <input name="new_expiration" type="date" defaultValue={p.expiration ?? undefined} className={`${inputCls} w-40`} required />
                            </div>
                            <div>
                              <label className={labelCls}>Buyback cost</label>
                              <input name="buyback_cost" type="number" step="0.01" placeholder="Cost to close" className={`${inputCls} w-32`} />
                            </div>
                            <div>
                              <label className={labelCls}>Premium received</label>
                              <input name="premium" type="number" step="0.01" placeholder="New premium" className={`${inputCls} w-32`} />
                            </div>
                            <div className="grow">
                              <label className={labelCls}>Notes (optional)</label>
                              <input name="notes" type="text" placeholder="e.g. Rolling up for better premium" className={`${inputCls} w-full`} />
                            </div>
                          </div>
                          <div className="mt-3 flex gap-2">
                            <button
                              type="submit"
                              disabled={busy}
                              className="rounded-[var(--radius-pill)] bg-accent-blue px-4 py-1.5 text-sm text-white disabled:opacity-50"
                            >
                              {busy ? "Rolling…" : "Confirm Roll"}
                            </button>
                            <button
                              type="button"
                              onClick={resetEditors}
                              className="rounded-[var(--radius-pill)] border border-border px-4 py-1.5 text-sm text-text-muted hover:text-text"
                            >
                              Cancel
                            </button>
                          </div>
                        </form>
                      </td>
                    </tr>
                  )}

                  {closeFor === posId && (
                    <tr className="border-b border-border/60 bg-bg-input/40">
                      <td colSpan={COLS} className="px-4 py-4">
                        <CloseForm busy={busy} onCancel={resetEditors} onSubmit={(form) => submitClose(posId, form)} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function CloseForm({
  busy,
  onCancel,
  onSubmit,
}: {
  busy: boolean;
  onCancel: () => void;
  onSubmit: (form: HTMLFormElement) => void;
}) {
  const [reason, setReason] = useState("manual");
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit(e.currentTarget);
      }}
    >
      <h4 className="mb-3 text-sm font-semibold">Close position</h4>
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label className={labelCls}>Reason</label>
          <select
            name="close_reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            className={`${inputCls} w-40`}
          >
            {CLOSE_REASONS.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
        </div>
        {reason === "manual" && (
          <div>
            <label className={labelCls}>Buyback cost (optional)</label>
            <input name="buyback_cost" type="number" step="0.01" placeholder="Cost to close" className={`${inputCls} w-32`} />
          </div>
        )}
      </div>
      <div className="mt-3 flex gap-2">
        <button
          type="submit"
          disabled={busy}
          className="rounded-[var(--radius-pill)] bg-accent-red px-4 py-1.5 text-sm text-white disabled:opacity-50"
        >
          {busy ? "Closing…" : "Confirm Close"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-[var(--radius-pill)] border border-border px-4 py-1.5 text-sm text-text-muted hover:text-text"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
