import { queryOptions } from "@tanstack/react-query";
import type { SymbolsOverview, SymbolsViewMode } from "@/types/symbols";

export function symbolsViewQueryOptions(view: SymbolsViewMode) {
  return queryOptions<SymbolsOverview>({
    queryKey: ["symbols-overview", view],
    queryFn: async ({ signal }) => {
      const response = await fetch(`/api/symbols/overview?view=${view}`, { signal });
      const body = await response.json();
      if (!response.ok || body.error) throw new Error(body.error || "Symbols view is unavailable");
      return body;
    },
    staleTime: 60_000,
    retry: false,
  });
}
