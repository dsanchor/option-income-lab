/**
 * validateTradeValueMatch.ts — Pure helper for Amendment G §G.1.3 cross-validation.
 *
 * BUY/SELL forms show three linked fields: quantity, unitPrice, tradeValue.
 * Any two auto-compute the third. When all three are explicitly provided,
 * this helper validates that quantity × unitPrice ≈ tradeValue (within 0.01
 * tolerance in the transaction currency).
 *
 * The server NEVER receives unitPrice — it only gets gross (tradeValue).
 * Unit price is a UI-only convenience field (§G.1.3).
 */

export interface TradeValueValidation {
  valid: boolean;
  /** Populated when valid=false with a user-visible error message. */
  error?: string;
  /** Auto-computed tradeValue when qty + price given but tradeValue empty. */
  computedTradeValue?: number;
  /** Auto-computed unitPrice when qty + tradeValue given but unitPrice empty. */
  computedUnitPrice?: number;
}

/**
 * Tolerance for cross-validation: 0.01 in transaction currency (§G.1.3).
 */
const TOLERANCE = 0.01;

/**
 * Validates/auto-computes the three linked fields.
 *
 * Resolution rules (§G.1.3):
 * 1. qty + price filled, tradeValue empty → auto-computes tradeValue
 * 2. qty + tradeValue filled, price empty → auto-computes unitPrice
 * 3. All three filled → cross-validate |qty × price − tradeValue| ≤ 0.01
 * 4. Only tradeValue filled (qty zero/empty) → valid for zero-cost inputs
 *
 * @param quantity       Parsed number (≥ 0) or null/undefined if empty
 * @param unitPrice      Parsed number (> 0) or null/undefined if empty
 * @param tradeValue     Parsed number (≥ 0) or null/undefined if empty
 */
export function validateTradeValueMatch(
  quantity: number | null | undefined,
  unitPrice: number | null | undefined,
  tradeValue: number | null | undefined,
): TradeValueValidation {
  const hasQty = quantity != null && isFinite(quantity);
  const hasPrice = unitPrice != null && isFinite(unitPrice);
  const hasTrade = tradeValue != null && isFinite(tradeValue);

  // Rule 1: qty + price → auto-compute tradeValue
  if (hasQty && hasPrice && !hasTrade) {
    return {
      valid: true,
      computedTradeValue: quantity! * unitPrice!,
    };
  }

  // Rule 2: qty + tradeValue → auto-compute unitPrice
  if (hasQty && hasTrade && !hasPrice) {
    if (quantity! > 0) {
      return {
        valid: true,
        computedUnitPrice: tradeValue! / quantity!,
      };
    }
    // qty === 0, tradeValue present → valid (zero-cost acquisition)
    return { valid: true };
  }

  // Rule 3: all three filled → cross-validate
  if (hasQty && hasPrice && hasTrade) {
    const computed = quantity! * unitPrice!;
    const delta = Math.abs(computed - tradeValue!);
    if (delta > TOLERANCE) {
      return {
        valid: false,
        error:
          `Trade value doesn't match quantity × price. ` +
          `Expected ≈${computed.toFixed(2)}, got ${tradeValue!.toFixed(2)}. ` +
          `Correct one of them.`,
      };
    }
    return { valid: true };
  }

  // Rule 4: only tradeValue (qty zero/empty) → valid
  if (hasTrade && !hasQty && !hasPrice) {
    return { valid: true };
  }

  // Insufficient data to validate — allow form to proceed
  return { valid: true };
}
