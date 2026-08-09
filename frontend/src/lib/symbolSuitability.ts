export type SymbolSuitabilityFilter =
  | "all"
  | "ideal_puts"
  | "ideal_calls"
  | "no_puts"
  | "no_calls";

function normalize(value: string | null | undefined): string {
  return (value ?? "").trim().replace(/\s+/g, " ").toLowerCase();
}

export function matchesSymbolSuitability(
  entryTag: string | null | undefined,
  momentum: string | null | undefined,
  filter: SymbolSuitabilityFilter,
): boolean {
  if (filter === "all") return true;

  const entry = normalize(entryTag);
  const normalizedMomentum = normalize(momentum);
  const baseMomentum = normalizedMomentum.split("(", 1)[0].trim();
  const isOversold = normalizedMomentum.includes("oversold");
  const isOverextended = normalizedMomentum.includes("overextended");

  switch (filter) {
    case "ideal_puts":
      return (
        (["strong buy", "buy"].includes(entry) &&
          ["bullish", "neutral", "weakening"].includes(baseMomentum)) ||
        isOversold
      );
    case "ideal_calls":
      return (
        (["hold", "wait"].includes(entry) &&
          ["weakening", "bearish", "neutral"].includes(baseMomentum)) ||
        isOverextended
      );
    case "no_puts":
      return ["strong buy", "buy"].includes(entry) && normalizedMomentum === "bearish";
    case "no_calls":
      return entry === "wait" && normalizedMomentum === "bullish";
  }
}