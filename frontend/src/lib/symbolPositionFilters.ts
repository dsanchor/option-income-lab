import type { SymbolRow } from "@/types/symbols";

export interface SymbolPositionFilters {
  openOptions: boolean;
  coveredCallCapacity: boolean;
}

export const ALL_SYMBOL_POSITIONS: SymbolPositionFilters = {
  openOptions: false,
  coveredCallCapacity: false,
};

/** Independent position filters intersect with each other and the other filters.
 * Missing projections do not imply positions or real covered-call capacity. */
export function matchesSymbolPositions(
  row: Pick<SymbolRow, "has_open_options" | "can_open_covered_calls">,
  filters: SymbolPositionFilters,
): boolean {
  return (
    (!filters.openOptions || row.has_open_options === true) &&
    (!filters.coveredCallCapacity || row.can_open_covered_calls === true)
  );
}
