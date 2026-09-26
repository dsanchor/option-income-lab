/**
 * @deprecated Replaced by StockTransactionsTable (Amendment I).
 * No longer rendered on the symbol detail page. Preserved for backward compat.
 */
import Link from "next/link";
import type { RecentMovement } from "@/types/symbol-detail";
import { excludeUnsupportedRightsMovements } from "@/lib/rightsExclusion";

interface Props {
  movements: RecentMovement[];
  movementCount?: number;
  securityId?: string | null;
}

const TXN_LABELS: Record<string, { label: string; cls: string }> = {
  BUY: { label: "Buy", cls: "text-accent-green border-accent-green/40 bg-accent-green/10" },
  SELL: { label: "Sell", cls: "text-accent-red border-accent-red/40 bg-accent-red/10" },
  DIVIDEND: { label: "Dividend", cls: "text-accent-blue border-accent-blue/40 bg-accent-blue/10" },
  TRANSFER_IN: { label: "In", cls: "text-text-muted border-border bg-bg-input" },
  TRANSFER_OUT: { label: "Out", cls: "text-text-muted border-border bg-bg-input" },
};

function eur(v: string | null | undefined): string {
  if (!v) return "—";
  const n = parseFloat(v);
  if (!isFinite(n)) return "—";
  return new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR", maximumFractionDigits: 2 }).format(n);
}

function qty(v: string | null | undefined): string {
  if (!v) return "";
  const n = parseFloat(v);
  if (!isFinite(n)) return "";
  return n % 1 === 0 ? n.toFixed(0) : n.toFixed(4).replace(/0+$/, "");
}

export default function SymbolMovementsTable({ movements, movementCount, securityId }: Props) {
  const visibleMovements = excludeUnsupportedRightsMovements(movements);
  const visibleMovementCount = movementCount === undefined
    ? undefined
    : Math.max(0, movementCount - (movements.length - visibleMovements.length));
  if (visibleMovements.length === 0) {
    return (
      <div className="surface rounded-[var(--radius)] border border-border p-4">
        <h3 className="mb-2 text-sm font-semibold text-text">Stock Movements</h3>
        <p className="text-sm text-text-muted">No movements recorded.</p>
      </div>
    );
  }

  const viewAllHref = securityId
    ? `/portfolio/movements?security_id=${encodeURIComponent(securityId)}`
    : "/portfolio/movements";

  return (
    <div className="surface rounded-[var(--radius)] border border-border overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border/60">
        <h3 className="text-sm font-semibold text-text">
          Stock Movements
          {visibleMovementCount !== undefined && visibleMovementCount > visibleMovements.length && (
            <span className="ml-1.5 text-xs font-normal text-text-muted">
              (showing {visibleMovements.length} of {visibleMovementCount})
            </span>
          )}
        </h3>
        <Link
          href={viewAllHref}
          className="text-xs text-accent-blue hover:underline"
        >
          View all →
        </Link>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[540px] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
              <th className="px-4 py-2 font-medium">Date</th>
              <th className="px-4 py-2 font-medium">Type</th>
              <th className="px-4 py-2 font-medium text-right">Qty</th>
              <th className="px-4 py-2 font-medium text-right">Gross (EUR)</th>
              <th className="px-4 py-2 font-medium text-right">Net (EUR)</th>
            </tr>
          </thead>
          <tbody>
            {visibleMovements.map((m) => {
              const meta = TXN_LABELS[m.txn_type] ?? { label: m.txn_type, cls: "text-text-muted border-border bg-bg-input" };
              return (
                <tr key={m.id} className="border-b border-border/40 last:border-0">
                  <td className="px-4 py-2 font-mono text-xs text-text-muted">{m.trade_date}</td>
                  <td className="px-4 py-2">
                    <span className={`inline-block rounded-[var(--radius-pill)] border px-2 py-0.5 text-xs ${meta.cls}`}>
                      {meta.label}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-xs">{qty(m.quantity)}</td>
                  <td className="px-4 py-2 text-right font-mono text-xs">{eur(m.gross_eur)}</td>
                  <td className="px-4 py-2 text-right font-mono text-xs text-text-muted">{eur(m.net_eur)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
