/**
 * caGroupIndicator.ts — Pure helpers for corporate-action group badge display.
 *
 * Amendment H §H.3.4: ledger_txn docs that belong to a corporate-action group
 * carry `ca_group_id`, `ca_leg_type`, `ca_event_type`, and `ca_group_seq`.
 * These helpers drive the group indicator icon in StockTransactionsTable and
 * MovementDetailDialog.
 *
 * Pattern: mirrors filterMovementsByType.ts / filterPortfolioRows.ts conventions.
 */

/** Minimal shape required from a movement object to check CA group membership. */
export interface CaGroupMovementShape {
  ca_group_id?: string | null;
  ca_leg_type?: string | null;
  ca_event_type?: string | null;
  ca_group_seq?: number | null;
}

/**
 * Returns true when the movement belongs to a corporate-action group.
 * Non-CA movements have ca_group_id absent or null.
 */
export function isCaGroupMovement(
  movement: CaGroupMovementShape,
): boolean {
  return movement.ca_group_id != null && movement.ca_group_id !== "";
}

/**
 * User-facing labels for each CA leg type.
 * Maps the internal `ca_leg_type` enum to a display string.
 */
export const CA_LEG_TYPE_LABELS: Record<string, string> = {
  CASH_DIVIDEND: "Cash Dividend",
  RIGHTS_SOLD: "Rights Sold",
  SHARE_ACQUISITION: "Share Acquisition",
  CASH_TOP_UP: "Cash Top-Up",
  CONSOLIDATION_OUT: "Consolidation Out",
  CONSOLIDATION_IN: "Consolidation In",
  FRACTIONAL_CASH_OUT: "Fractional Cash",
};

/**
 * Returns the user-facing label for a ca_leg_type, or null if unknown/absent.
 */
export function getCaLegTypeLabel(
  legType: string | null | undefined,
): string | null {
  if (!legType) return null;
  return CA_LEG_TYPE_LABELS[legType] ?? null;
}
