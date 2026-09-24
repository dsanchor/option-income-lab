import type { LedgerMovement, MovementsResponse } from "@/types/portfolio";

export interface ActiveMovementRequest {
  readonly signal: AbortSignal;
  isCurrent(): boolean;
}

export class LatestMovementRequest {
  private generation = 0;
  private controller: AbortController | null = null;

  begin(): ActiveMovementRequest {
    this.controller?.abort();
    const controller = new AbortController();
    const generation = ++this.generation;
    this.controller = controller;
    return {
      signal: controller.signal,
      isCurrent: () =>
        generation === this.generation && !controller.signal.aborted,
    };
  }

  cancel(): void {
    this.generation += 1;
    this.controller?.abort();
    this.controller = null;
  }
}

export function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

export function dedupeMovementsById(
  movements: LedgerMovement[],
): LedgerMovement[] {
  const seen = new Set<string>();
  return movements.filter((movement) => {
    if (!movement.id) return true;
    if (seen.has(movement.id)) return false;
    seen.add(movement.id);
    return true;
  });
}

interface FetchAllMovementPagesOptions {
  pageSize: number;
  signal: AbortSignal;
  fetchPage: (
    offset: number,
    limit: number,
    signal: AbortSignal,
  ) => Promise<MovementsResponse>;
}

export interface AllMovementPages {
  movements: LedgerMovement[];
  reportedTotal: number;
  rawRowCount: number;
  incomplete: boolean;
}

/**
 * Fetches until the authoritative page-exhaustion signal (a short page).
 * total_count is diagnostic only: stopping on raw or deduped count can miss a
 * unique later row when pages overlap during concurrent data changes.
 */
export async function fetchAllMovementPages({
  pageSize,
  signal,
  fetchPage,
}: FetchAllMovementPagesOptions): Promise<AllMovementPages> {
  const rows: LedgerMovement[] = [];
  let offset = 0;
  let rawRowCount = 0;
  let reportedTotal: number | null = null;

  while (true) {
    const page = await fetchPage(offset, pageSize, signal);
    if (reportedTotal === null) reportedTotal = page.total_count;
    rawRowCount += page.movements.length;
    rows.push(...page.movements);

    if (page.movements.length < pageSize) break;
    offset += pageSize;
  }

  const total = reportedTotal ?? 0;
  return {
    movements: dedupeMovementsById(rows),
    reportedTotal: total,
    rawRowCount,
    incomplete: rawRowCount < total,
  };
}
