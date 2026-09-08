import type { PortfolioSection, SymbolState, HoldingsByAccount } from "@/types/symbol-detail";
import { getAccountBadgeClass, UNASSIGNED_LABEL } from "@/lib/accountDisplay";

interface Props {
  portfolio: PortfolioSection;
  symbolState: SymbolState | null | undefined;
}

function eur(v: string | null | undefined): string {
  if (!v) return "—";
  const n = parseFloat(v);
  if (!isFinite(n)) return "—";
  return new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR", maximumFractionDigits: 2 }).format(n);
}

function fmtShares(v: string | null | undefined): string {
  if (!v) return "—";
  const n = parseFloat(v);
  if (!isFinite(n)) return "—";
  return n % 1 === 0 ? n.toFixed(0) : n.toFixed(6).replace(/0+$/, "");
}

/** Visible account label — account_name only; _unassigned sentinel → UNASSIGNED_LABEL. */
function acctLabel(acct: HoldingsByAccount): string {
  if (!acct.account_id || acct.account_id === "_unassigned") return UNASSIGNED_LABEL;
  return acct.account_name ?? acct.account_id;
}

export default function PortfolioHoldingsCard({ portfolio, symbolState }: Props) {
  const isHistorical =
    symbolState === "portfolio_historical" ||
    (parseFloat(portfolio.current_shares) === 0 && symbolState !== "watchlist_and_portfolio");

  const hasAccountRows =
    portfolio.holdings_by_account && portfolio.holdings_by_account.length > 0;

  const showDividends = !!portfolio.total_dividends_eur;

  return (
    <div className="space-y-2">
      {/* ── Section heading ─────────────────────────────────────────── */}
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-text">Portfolio Holdings</h3>
        {isHistorical && (
          <span className="inline-block rounded-[var(--radius-pill)] border border-border px-2 py-0.5 text-xs text-text-muted">
            Historical — 0 shares
          </span>
        )}
      </div>

      {/* ── Responsive semantic table — single flat container, no inner card ── */}
      <div className="overflow-x-auto rounded-[var(--radius)] border border-border">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-bg-card/80">
              <th scope="col" className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-text-muted">
                Account
              </th>
              <th scope="col" className="px-4 py-2.5 text-right text-xs font-semibold uppercase tracking-wide text-text-muted">
                Shares
              </th>
              <th scope="col" className="px-4 py-2.5 text-right text-xs font-semibold uppercase tracking-wide text-text-muted">
                Avg Cost (€)
              </th>
              <th scope="col" className="px-4 py-2.5 text-right text-xs font-semibold uppercase tracking-wide text-text-muted">
                Invested (€)
              </th>
              {showDividends && (
                <th scope="col" className="px-4 py-2.5 text-right text-xs font-semibold uppercase tracking-wide text-text-muted">
                  Dividends (€)
                </th>
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-border/40">
            {hasAccountRows &&
              portfolio.holdings_by_account!.map((acct) => (
                <tr key={acct.account_id}>
                  <td className="px-4 py-2.5">
                    <span
                      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${getAccountBadgeClass(acct.account_id)}`}
                    >
                      {acctLabel(acct)}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-text">
                    {fmtShares(acct.shares)}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-text">
                    {eur(acct.avg_cost_eur)}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-text">
                    {eur(acct.current_invested_eur)}
                  </td>
                  {showDividends && (
                    <td className="px-4 py-2.5 text-right font-mono text-accent-green">
                      {eur(acct.total_dividends_eur)}
                    </td>
                  )}
                </tr>
              ))}

            {/* Totals / summary row — always shown */}
            <tr className={hasAccountRows ? "border-t border-border/60 bg-bg-card/60 font-medium" : ""}>
              <td className="px-4 py-2.5 text-xs text-text-muted">
                {hasAccountRows ? "Total" : isHistorical ? "Historical" : "All accounts"}
              </td>
              <td className="px-4 py-2.5 text-right font-mono text-text">
                {isHistorical ? "0" : fmtShares(portfolio.current_shares)}
              </td>
              <td className="px-4 py-2.5 text-right font-mono text-text">
                {eur(portfolio.average_cost_eur)}
              </td>
              <td className="px-4 py-2.5 text-right font-mono text-text">
                {eur(portfolio.current_invested_eur)}
              </td>
              {showDividends && (
                <td className="px-4 py-2.5 text-right font-mono text-accent-green">
                  {eur(portfolio.total_dividends_eur)}
                </td>
              )}
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}
