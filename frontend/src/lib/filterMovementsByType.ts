import { isUnsupportedRightsMovement } from "./legacyMovementExclusion.js";

/** Shared movement-type membership rules for user-facing filters. */

export interface MovementTypeFilterShape {
  txn_type?: string | null;
  ca_leg_type?: string | null;
  ca_event_type?: string | null;
}

const DIVIDEND_SHARE_EVENTS = new Set([
  "SCRIP_DIVIDEND",
  "DIVIDEND_WITH_SCRIP",
]);

export function isDividendDerivedShareAcquisition(
  movement: MovementTypeFilterShape,
): boolean {
  return (
    movement.txn_type === "BUY" &&
    movement.ca_leg_type === "SHARE_ACQUISITION" &&
    movement.ca_event_type != null &&
    DIVIDEND_SHARE_EVENTS.has(movement.ca_event_type)
  );
}

export function matchesMovementTypeFilter(
  movement: MovementTypeFilterShape,
  filter: string | null | undefined,
): boolean {
  if (isUnsupportedRightsMovement(movement)) return false;
  if (!filter || filter === "ALL") return true;
  if (movement.txn_type === filter) return true;
  return filter === "DIVIDEND" && isDividendDerivedShareAcquisition(movement);
}

/**
 * Dividend membership spans stored BUY and DIVIDEND rows, so it cannot be
 * represented by the backend's exact txn_type query parameter.
 */
export function getServerMovementTypeFilter(
  filter: string | null | undefined,
): string | undefined {
  return !filter || filter === "ALL" || filter === "DIVIDEND"
    ? undefined
    : filter;
}

export function filterMovementsByType<T extends MovementTypeFilterShape>(
  movements: T[],
  filter: string | null | undefined,
): T[] {
  return movements.filter((movement) =>
    matchesMovementTypeFilter(movement, filter),
  );
}

export type StocksTabTxnType = "BUY" | "SELL" | "DIVIDEND";

export const STOCKS_TAB_TYPES: readonly StocksTabTxnType[] = [
  "BUY",
  "SELL",
  "DIVIDEND",
];

/**
 * Returns true if the given txn_type should appear in the Stocks tab.
 * TRANSFER_IN, TRANSFER_OUT, and any unknown types are excluded.
 */
export function isStocksTabMovement(txn_type: string): boolean {
  return (STOCKS_TAB_TYPES as readonly string[]).includes(txn_type);
}

/**
 * Filters a movements array to only those types shown in the Stocks tab.
 * Preserves original order (backend returns newest-first by trade_date).
 */
export function filterMovementsForStocksTab<
  T extends { txn_type: string } & MovementTypeFilterShape,
>(movements: T[]): T[] {
  return movements.filter(
    (movement) =>
      !isUnsupportedRightsMovement(movement) &&
      isStocksTabMovement(movement.txn_type),
  );
}
