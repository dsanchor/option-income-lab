import { queryOptions } from "@tanstack/react-query";
import type { SymbolDetail } from "@/types/symbol-detail";

export type SymbolDetailSection = "stocks" | "options" | "plans";

export function symbolDetailQueryOptions(symbol: string, section: SymbolDetailSection, snapshot = "legacy") {
  return queryOptions<Partial<SymbolDetail>>({
    queryKey: ["symbol-detail", symbol, section, snapshot],
    queryFn: async ({ signal }) => {
      const response = await fetch(`/api/symbols/${encodeURIComponent(symbol)}/detail?section=${section}`, { signal });
      const body = await response.json();
      if (!response.ok || body.error) throw new Error(body.error || `Could not load ${section}`);
      return body;
    },
    staleTime: 60_000,
    retry: false,
  });
}
