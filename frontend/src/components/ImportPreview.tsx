"use client";

import type { ImportPreviewData } from "@/types/import";
import type { WarningType } from "@/types/portfolio";
import { SALES_TYPE_LABELS } from "@/types/portfolio";

interface Props {
  preview: ImportPreviewData;
  onCommit: () => void;
  onBack: () => void;
  committing: boolean;
}

const WARNING_LABELS: Record<WarningType, string> = {
  NEGATIVE_INVENTORY: "Negative inventory",
  RIGHTS_AMOUNT: "Rights / scrip amount",
  PROBABLE_DUPLICATE: "Probable duplicate",
  DERECHOS_WITH_QUANTITY: "Rights sale with quantity",
  ACCIONES_ZERO_QUANTITY: "Share sale, zero quantity",
  INVALID_SALES_TYPE: "Invalid sale type",
};

/** Preview table showing movements to be committed, persistent warnings, and confirm button. */
export default function ImportPreview({ preview, onCommit, onBack, committing }: Props) {
  const { movements, warnings, total_movements, skipped_rows, skip_reasons } = preview;

  return (
    <div className="space-y-6">
      {/* Summary bar */}
      <div className="flex flex-wrap gap-4 text-sm">
        <span>
          <span className="font-semibold text-text">{total_movements}</span>
          <span className="text-text-muted"> movements to commit</span>
        </span>
        {skipped_rows > 0 && (
          <span>
            <span className="text-accent-orange">{skipped_rows}</span>
            <span className="text-text-muted"> skipped</span>
          </span>
        )}
        {warnings.length > 0 && (
          <span>
            <span className="text-accent-orange">⚠ {warnings.length}</span>
            <span className="text-text-muted"> warning{warnings.length !== 1 ? "s" : ""}</span>
          </span>
        )}
      </div>

      {/* Persistent warnings panel */}
      {warnings.length > 0 && (
        <div className="rounded-[var(--radius)] border border-accent-orange/30 bg-accent-orange/5 p-4 space-y-2">
          <div className="text-sm font-medium text-accent-orange">
            ⚠ Persistent warnings — these will be saved with the movements
          </div>
          <ul className="space-y-1">
            {warnings.map((w, i) => (
              <li key={i} className="flex items-start gap-2 text-sm">
                <span className="text-accent-orange mt-0.5 shrink-0">•</span>
                <span className="text-text-muted">
                  <span className="font-medium text-text mr-1">
                    {WARNING_LABELS[w.type as WarningType] ?? w.type}
                  </span>
                  {w.message}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Skip reasons */}
      {skip_reasons.length > 0 && (
        <div className="rounded-[var(--radius)] border border-border bg-bg-card p-3 space-y-1">
          <div className="text-xs font-semibold uppercase tracking-wide text-text-muted">
            Skipped rows
          </div>
          {skip_reasons.map((r, i) => (
            <div key={i} className="flex justify-between text-sm">
              <span className="text-text">{r.company}</span>
              <span className="text-text-muted">
                {r.reason} · {r.row_count} rows
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Movements table */}
      <div className="overflow-x-auto rounded-[var(--radius)] border border-border">
        <table className="w-full table-modern text-sm">
          <thead>
            <tr className="border-b border-border bg-bg-card/80">
              <th className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-text-muted">
                Type
              </th>
              <th className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-text-muted">
                Symbol
              </th>
              <th className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-text-muted">
                Date
              </th>
              <th className="px-4 py-2.5 text-right text-xs font-semibold uppercase tracking-wide text-text-muted">
                Qty
              </th>
              <th className="px-4 py-2.5 text-right text-xs font-semibold uppercase tracking-wide text-text-muted">
                Gross (€)
              </th>
              <th className="px-4 py-2.5 text-right text-xs font-semibold uppercase tracking-wide text-text-muted">
                Net (€)
              </th>
              <th className="px-2 py-2.5" />
            </tr>
          </thead>
          <tbody className="divide-y divide-border/40">
            {movements.map((m) => {
              const hasWarnings = m.warnings && m.warnings.length > 0;
              return (
                <tr key={m.row_index} className={hasWarnings ? "bg-accent-orange/5" : ""}>
                  <td className="px-4 py-2">
                    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${txnBadge(m.txn_type)}`}>
                      {m.txn_type}
                    </span>
                    {m.txn_type === "SELL" && m.sales_type === "DERECHOS" && (
                      <span className="ml-1 rounded-full px-1.5 py-0.5 text-xs bg-accent-orange/15 text-accent-orange">
                        {SALES_TYPE_LABELS.DERECHOS}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2">
                    <div className="font-mono font-semibold text-text">{m.ticker}</div>
                    <div className="text-xs text-text-muted truncate max-w-[140px]">
                      {m.company_name}
                    </div>
                  </td>
                  <td className="px-4 py-2 text-text-muted">{m.trade_date}</td>
                  <td className="px-4 py-2 text-right font-mono text-text">{m.quantity ?? "—"}</td>
                  <td className="px-4 py-2 text-right font-mono text-text">{m.gross_eur}</td>
                  <td className="px-4 py-2 text-right font-mono text-text">{m.net_eur}</td>
                  <td className="px-2 py-2 text-right">
                    {hasWarnings && (
                      <span
                        title={m.warnings!.map((w) => w.message).join("; ")}
                        className="text-accent-orange cursor-help"
                      >
                        ⚠
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Action buttons */}
      <div className="flex gap-3 justify-end pt-2">
        <button
          type="button"
          onClick={onBack}
          disabled={committing}
          className="rounded-[var(--radius)] border border-border px-4 py-2 text-sm text-text-muted hover:bg-bg-hover disabled:opacity-50"
        >
          ← Back to questions
        </button>
        <button
          type="button"
          onClick={onCommit}
          disabled={committing}
          className="rounded-[var(--radius)] bg-[image:var(--grad-blue)] px-6 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-50 shadow-[var(--shadow-glow-blue)]"
        >
          {committing
            ? "Committing…"
            : `Confirm & commit ${total_movements} movements`}
        </button>
      </div>
    </div>
  );
}

function txnBadge(type: string): string {
  if (type === "BUY") return "bg-accent-green/15 text-accent-green";
  if (type === "SELL") return "bg-accent-red/15 text-accent-red";
  if (type === "DIVIDEND") return "bg-accent-blue/15 text-accent-blue";
  return "bg-bg-hover text-text-muted";
}
