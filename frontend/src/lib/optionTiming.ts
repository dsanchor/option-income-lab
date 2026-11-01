import type {
  OptionTimingHistory,
  OptionTimingPoint,
  OptionTimingSideKey,
  OptionTimingSnapshot,
} from "@/types/option-timing";
import { decodeSymbolParam } from "@/lib/symbolEncoding";

export const OPTION_TIMING_SIDES: { key: OptionTimingSideKey; label: string; short: string }[] = [
  { key: "covered_call", label: "Covered CALL", short: "CALL" },
  { key: "cash_secured_put", label: "Cash-secured PUT", short: "PUT" },
];

export function optionTimingScore(snapshot: OptionTimingSnapshot | null | undefined, side: OptionTimingSideKey): number | null {
  const value = snapshot?.[side];
  return value?.status === "available" && typeof value.score === "number"
    && Number.isFinite(value.score) && value.score >= 0 && value.score <= 100
    ? value.score : null;
}

export function compareOptionTiming(a: number | null, b: number | null, direction: "asc" | "desc"): number {
  if (a === null) return b === null ? 0 : 1;
  if (b === null) return -1;
  return direction === "asc" ? a - b : b - a;
}

export function optionTimingPoints(history: OptionTimingHistory | undefined, current: OptionTimingSnapshot | null | undefined): OptionTimingPoint[] {
  const points = [...(history?.points ?? [])];
  if (current && current.input_timestamp_available !== false
    && !points.some((point) => point.snapshot_id === current.snapshot_id)) {
    points.push({ ...current, history_kind: "observed" });
  }
  return points.filter((point) => point.input_timestamp_available !== false && Number.isFinite(Date.parse(point.as_of)))
    .sort((a, b) => Date.parse(a.as_of) - Date.parse(b.as_of));
}

export async function fetchOptionTimingHistory(symbol: string, range: string, signal?: AbortSignal): Promise<OptionTimingHistory> {
  const canonicalSymbol = decodeSymbolParam(symbol);
  const res = await fetch(`/api/symbols/${encodeURIComponent(canonicalSymbol)}/option-timing/history?range=${encodeURIComponent(range)}`, {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!res.ok) throw new Error(`Could not load Option Timing history (HTTP ${res.status}).`);
  const data = await res.json() as OptionTimingHistory;
  if (data.schema_version !== 1 || !Array.isArray(data.points)) throw new Error("Unsupported Option Timing history response.");
  return data;
}
