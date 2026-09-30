export const SYMBOL_MOMENTUM_OPTIONS = [
  "Bullish",
  "Bullish (overextended)",
  "Weakening",
  "Neutral",
  "Bearish",
  "Bearish (oversold)",
  "Unknown",
] as const;

export type SymbolMomentumFilter = (typeof SYMBOL_MOMENTUM_OPTIONS)[number];

const KNOWN_MOMENTUM = new Set<string>(SYMBOL_MOMENTUM_OPTIONS);

export function symbolMomentumFilterValue(
  momentum: string | null | undefined,
): SymbolMomentumFilter {
  const value = momentum?.trim() ?? "";
  return KNOWN_MOMENTUM.has(value) ? value as SymbolMomentumFilter : "Unknown";
}

export function matchesSymbolMomentum(
  momentum: string | null | undefined,
  selected: ReadonlySet<SymbolMomentumFilter>,
): boolean {
  return selected.size === 0 || selected.has(symbolMomentumFilterValue(momentum));
}
