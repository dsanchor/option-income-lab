import type { Position } from "@/types/symbol-detail";

export type ForecastOptionPosition = {
  type: "call" | "put";
  strike: number;
  expiration: string;
  dte: number;
  contracts: number | null;
  premium: number | null;
  accountName: string | null;
  paper: boolean;
};

export type ForecastOptionGroup = {
  weeks: number;
  strike: number;
  overflow: boolean;
  positions: ForecastOptionPosition[];
};

function numeric(value: unknown): number | null {
  if (typeof value !== "number" && (typeof value !== "string" || !value.trim())) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function dateNumber(value: unknown): number | null {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const parsed = Date.parse(`${value}T00:00:00Z`);
  return Number.isFinite(parsed) && new Date(parsed).toISOString().slice(0, 10) === value
    ? parsed : null;
}

export function forecastOptionPositions(positions: Position[], now = new Date()) {
  const today = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
  }).format(now);
  const todayNumber = dateNumber(today)!;
  const groups = new Map<string, ForecastOptionGroup>();
  let unavailable = 0;
  for (const position of positions) {
    if (position.status !== "active" || (position.type !== "call" && position.type !== "put")) continue;
    const remaining = position.contract_counts?.remaining;
    const rawCount = position.open_contracts ?? position.contracts_open ?? position.contracts ?? position.quantity;
    const count = remaining
      ? remaining.status === "resolved" ? numeric(remaining.count) : null
      : numeric(rawCount);
    if (count === 0) continue;
    const strike = numeric(position.strike);
    const expiry = dateNumber(position.expiration);
    if (strike == null || strike <= 0 || expiry == null) {
      unavailable++;
      continue;
    }
    // DTE is live calendar days in the US market timezone, not a saved monitor snapshot.
    const dte = Math.round((expiry - todayNumber) / 86_400_000);
    const weeks = Math.min(20, Math.max(0, dte)) / 5;
    const option: ForecastOptionPosition = {
      type: position.type,
      strike,
      expiration: position.expiration!,
      dte,
      contracts: count != null && Number.isInteger(count) ? Math.abs(count) : null,
      premium: numeric(position.display_premium),
      accountName: typeof position.account_name === "string" && position.account_name.trim()
        ? position.account_name.trim() : null,
      paper: position.is_paper === true,
    };
    const key = `${weeks}:${strike}`;
    const group = groups.get(key);
    if (group) {
      group.positions.push(option);
      group.overflow ||= dte > 20;
    } else {
      groups.set(key, { weeks, strike, overflow: dte > 20, positions: [option] });
    }
  }
  return { groups: [...groups.values()], unavailable };
}
