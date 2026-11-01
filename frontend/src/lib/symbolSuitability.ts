import type { OptionTimingSnapshot } from "@/types/option-timing";
import { optionTimingScore } from "@/lib/optionTiming";

export type SymbolSuitabilityFilter =
  | "all"
  | "ideal_puts"
  | "ideal_calls"
  | "no_puts"
  | "no_calls";

export function matchesSymbolSuitability(
  snapshot: OptionTimingSnapshot | null | undefined,
  filter: SymbolSuitabilityFilter,
): boolean {
  if (filter === "all") return true;
  if (snapshot?.freshness !== "fresh" || snapshot.input_timestamp_available === false) return false;

  const side = filter === "ideal_calls" || filter === "no_calls" ? "covered_call" : "cash_secured_put";
  const score = optionTimingScore(snapshot, side);
  if (score === null) return false;
  return filter === "ideal_calls" || filter === "ideal_puts" ? score >= 75 : score < 25;
}