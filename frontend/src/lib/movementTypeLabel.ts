export interface MovementTypeLabelShape {
  txn_type?: string | null;
  ca_leg_type?: string | null;
  ca_event_type?: string | null;
}

const MOVEMENT_TYPE_LABELS: Record<string, string> = {
  BUY: "Buy",
  SELL: "Sell",
  DIVIDEND: "Dividend",
  TRANSFER_OUT: "Transfer Out",
  TRANSFER_IN: "Transfer In",
  CALL_SELL: "Call Sell",
  CALL_BUY: "Call Buy",
  PUT_SELL: "Put Sell",
  PUT_BUY: "Put Buy",
};

const DIVIDEND_SHARE_EVENTS = new Set([
  "SCRIP_DIVIDEND",
  "DIVIDEND_WITH_SCRIP",
]);

export function getMovementTypeLabel(
  movement: MovementTypeLabelShape,
): string {
  const txnType = movement.txn_type;
  if (!txnType) return "—";

  if (
    txnType === "BUY" &&
    movement.ca_leg_type === "SHARE_ACQUISITION" &&
    movement.ca_event_type != null &&
    DIVIDEND_SHARE_EVENTS.has(movement.ca_event_type)
  ) {
    return "Dividend · Buy";
  }

  return MOVEMENT_TYPE_LABELS[txnType] ?? txnType;
}
